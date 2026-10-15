# Retroid Pocket 5 Linux deep-sleep kernel research

Reproducible ROCKNIX/Linux kernel and Device Tree experiments for
suspend-to-idle and deep residency on the Retroid Pocket 5 (RP5, Qualcomm
SM8250), including AOSD/CXSD/DDR evidence and measured standby current.

This repository contains the kernel and Device Tree sources, patches, build
configuration and verification materials. ConsoleOS userspace is maintained
separately and is not included.

The work is based on the RP5 build from ROCKNIX 20260901, pinned to distribution
commit `1ebff24f36501fb6493beb2bf83bf2604536d9aa` and Linux 7.2. The older
Armada UFS experiment is kept at the repository root; nearly all later RP5 work
lives under [`rocknix/`](rocknix/).

> **Stock starting point: about 115 mA.** Out-of-the-box ROCKNIX 20260901 fell
> from 39% to 32% during a 3 h 1 min 38 s in-game suspend. This is a coarse
> estimate based on the whole-percent gauge and the RP5's nominal battery
> capacity, not a fuel-gauge charge-counter measurement. AOSD and CXSD remained
> at zero, so the stock run did not demonstrate deep residency.

## Where to start

The active product-kernel history is on
[`codex/rp5-rc6-pdphy-product`](https://github.com/mihaivladan/rp5-ufs-kernel-test/tree/codex/rp5-rc6-pdphy-product),
whose current kernel-code tip is `ac7a8cd`. The branch name is historical: it
began with the RC6 USB-C work and later became the lineage for the PM8 kernel.
The default `main` branch stops earlier, at the exact-state platform-suspend
experiment `d150944`.

The current branch builds two kernels from the same commit:

- `7.2.0-consoleos-rc17-productpm8` is the lean product kernel used for boot,
  functionality, latency and suspend/resume testing.
- `7.2.0-consoleos-rc17-productpm8-diagcc4` starts from the exact same product
  source, configuration and Device Tree, then adds only the recorded diagnostic
  configuration and PM8150B fuel-gauge patch.

The pair is built by the manually dispatched
[`ConsoleOS RP5 paired product and diagnostic zboot`](.github/workflows/rocknix-lpm-platform.yml)
workflow. Both jobs use the same compiler-cache family, publish their build
logs and provenance, and receive different release suffixes so they cannot be
mistaken for one another.

For the exact current patch set and packaging boundary, read
[`rocknix/CONSOLEOS-RC1.md`](rocknix/CONSOLEOS-RC1.md).

## What the investigation established

Linux reporting a successful suspend is not enough to call the RP5 deeply
asleep. For this project, a real entry requires a successful same-boot
suspend/resume and positive count **and** duration deltas in Qualcomm platform
and memory residency counters such as AOSD, CXSD and DDR.

The important results behind this history are:

1. The original diagnostic/UFS kernel could suspend and resume, but its AOSD,
   CXSD and DDR residency counters stayed at zero. The UFS fix was valid, but it
   was not the missing deep-sleep fix.
2. Commit `2f447f7` produced `minsleep4`, the first conclusive native-Linux
   deep-residency result on this RP5. It removed the CPU DDR/L3 interconnect
   paths and OPP bandwidth values from an intentionally minimal Device Tree.
3. The subsystem matrix then reintroduced hardware in controlled groups. This
   separated compatible components from regressions instead of changing the
   full board at once.
4. Display, GPU, gamepad, UFS, Wi-Fi/Bluetooth, USB-C and audio were brought
   back through a long series of matched builds and device tests. Several
   plausible fixes were correctly rejected because suspend succeeded but the
   residency counters did not move.
5. The resulting RC17/PM8 lineage combines the selected kernel fixes and
   guarded low-level interfaces needed by the current product-kernel
   configuration.
6. The product kernel deliberately excludes the synchronous fuel-gauge
   diagnostics. The matching diagnostic kernel retains them only for bounded
   drain measurement.

This repository therefore preserves failed and no-effect experiments as well
as successful ones. A branch or build artifact is evidence that a hypothesis
was tested; it is not automatically a recommended patch.

## Measured current milestones

The current figures below come from bounded PM8150B fuel-gauge charge-counter
intervals. They are useful engineering estimates, not external bench-meter
calibration. Short runs also include proportionally more entry and wake
overhead than the eight-hour runs, so values from different durations should
not be subtracted as if they were matched A/B tests.

These are independent experimental states listed in research order; they
should not be read as one supported configuration.

| Result | Interval | Residency result | Average current / estimate | What the number established |
| --- | ---: | --- | ---: | --- |
| Stock ROCKNIX 20260901 | 3 h 1 min 38 s | **No AOSD/CXSD residency** | **about 115 mA (coarse estimate)** | The out-of-box starting point, inferred from a 39% -> 32% whole-percentage change with a game suspended. It is not a qualified charge-counter measurement. |
| `minsleep4` (`2f447f7`) | about 474 s | Strict deep residency | **Not measured** | First conclusive native-Linux deep-residency entry. The observed whole-percent battery change was too coarse to publish as current. |
| Phase 4AT full-stack reference | 630.671 s | 99.999% AOSD/CXSD/DDR | **73.482 mA** | Proved that the high draw was present during genuine deep residency, rather than being explained by shallow fallback. |
| Phase 4AU radios soft-blocked | 690.459 s | 99.999% AOSD/CXSD/DDR | **67.572 mA** | A matched reduction of **5.910 mA** from the 4AT reference. |
| Phase 4BA external-board diagnostic bucket | 275.385 s | 99.997% AOSD/CXSD/DDR | **35.950 mA** | Reduced the 4AU reference by **31.622 mA**, proving that the combined board-peripheral bucket contained a major load. It did not identify a single owner. |
| Phase 4BB fan-only A/B | about 3 min per state | Strict deep residency | **72.098 -> 70.045 mA** | The fan path accounted for about **2.053 mA**. The treatment's raw pre-boundary sample was missing, so this remains partial component evidence. |
| Phases 4BC/4BD shared 3.3 V rail A/B | 203.852 / 179.151 s | at least 99.995% AOSD/CXSD/DDR | **66.554 -> 43.726 mA** | The matched rail-on/rail-off comparison isolated **22.828 mA**, the largest single board-level saving found in that round. |
| Phase 4BL QCA package-off/CLKREQ diagnostic | 120.995 s | 99.992-99.994% AOSD/CXSD/DDR | **27.016 mA** | **12.493 mA** below the matched 39.509 mA endpoint-retained control. The static diagnostic pin state was not a shippable radio policy. |
| Phase 4CH RC3 overnight diagnostic | 28,798.411 s | 100.000% AOSD/CXSD/DDR | **24.841 mA** | Confirmed that the passing diagnostic state was stable for roughly eight hours; it was not a product radio configuration. |
| Phase 4CU corrected RC6 PD-PHY A/B | 179.490 s | 99.993-99.995% AOSD/CXSD/DDR | **38.289 -> 32.071 mA** | The retained RC6 kernel change saved **6.218 mA raw**, or **6.748 mA** after voltage normalization, while keeping unplugged Type-C detection. |
| Phase 4DF RC6 retained-QCA overnight | 28,799.621 s | essentially complete AOSD/CXSD/DDR | **25.392 mA** | The best long-duration estimate for that retained-QCA product boundary. It supersedes the short 32.071 mA run as the duration-matched baseline, not as a new component saving. |
| Phase 4DV RC16 package-off overnight | 28,799.648 s | **No deep residency; all counters stayed zero** | **19.879 mA** | An important negative control: a lower current figure alone does not prove deep sleep. This is a shallow-suspend result, not an accepted deep-sleep breakthrough. |
| Phase 4FC RC17 minimal diagnostic | 28,798.435 s | 100.000% AOSD/CXSD/DDR | **12.600 mA** | Lowest qualified long-duration native-Linux figure recorded so far. It is a deliberately reduced diagnostic state, not the full ConsoleOS product configuration. |
| Current PM8 product/diagnostic pair | pending 2-4 h paired run | Strict residency qualified separately | **Not yet measured** | Do not reuse an older branch's number: PM8 requires its own source-equivalent diagnostic-kernel measurement. |

“Strict deep residency” means a successful same-boot suspend/resume with an
increase in both count and duration for AOSD, CXSD and DDR low-power residency.
Battery percentage and awake `current_now` readings are deliberately excluded
from this table.

## Commit map

The history is easier to understand as a set of questions than as 133 isolated
commit subjects.

| Commits | Why they exist | Outcome or present role |
| --- | --- | --- |
| `077c589`–`3701a15` | Reproduce the installed Armada and ROCKNIX kernels, add the upstream Qualcomm UFS lane-clock fix, pin the exact RP5 sources and correct the DPU state-initialization bug exposed by the first diagnostic boot. | Established a bootable, traceable kernel baseline. UFS clock accounting improved, but UFS/graphics changes alone did not produce deep residency. |
| `707f611`–`2f447f7` | Build progressively smaller kernels and matching pruned Device Trees, then remove the eight CPU ICC paths and 56 OPP bandwidth values. | `2f447f7` is the `minsleep4` breakthrough: the first strict AOSD/CXSD/DDR result under native Linux. This kernel is a diagnostic baseline, not a usable console kernel. |
| `10644ec`–`d969b7d` | Rebuild full and pruned DTBs, generate the subsystem re-enable matrix, isolate display/GPU behavior, and add the upstream A6xx stale-RPMh-vote repair. | Proved that components must be reintroduced as controlled groups. Display and an idle GPU could sleep independently; the initialized display+GPU path initially exposed a new blocker. The upstream A6xx repair became part of later kernels. |
| `0da7d8f`–`9e38c6c` | Reintroduce the gamepad/QUP provider, internal UFS, QCA6390 wireless, USB-C and the audio stack in cumulative candidates. | Located new residency boundaries while retaining exact semantic DT checks. These commits are test construction, not a claim that every cumulative candidate passed. |
| `34e4681`–`3ddfd9e` | Supply the missing DisplayPort codec dependency, narrow audio to ADSP/LPASS components, add the SMP2P sleep-state handshake and make ADSP boot controllable. | Corrected real dependency gaps and enabled same-boot A/B tests. ADSP firmware or handshake changes by themselves were not sufficient. The no-auto-boot control remains useful for selecting pinned firmware. |
| `af81d76`–`d150944` | Test integrated SLPI/ADSP coordination and an Android-derived exact PSCI platform-suspend state. | Both were valid negative results. They ruled out “SLPI handshake alone” and “exact PSCI state alone” rather than becoming product fixes. `d150944` is where `main` currently ends. |
| `d4d01df`–`ed5667c` | Add the missing SM8250 `pcie_drv` ownership handoff and make PCIe suspend/resume ordering dependency-safe. | Established the kernel mechanism for handing the QCA6390 PCIe path to firmware during s2idle and restoring it safely afterward. |
| `3ad7097`–`c43c2f0` | Repair Q6AFE LPASS hardware vote handles, backport LPASS macro runtime-PM clock handling, and combine the proven audio and PCIe changes. | These are the audio/PCIe integration foundations carried into the product lineage. |
| `07164a3`–`6579a06` | Add read-only PM8150B Gen4 fuel-gauge/coulomb diagnostics and route a registered workflow to build them. | Diagnostic-only measurement support. It is intentionally absent from the product kernel because its synchronous reads can block battery uevents. |
| `d98111e`–`8f1e6e1` | Assemble RC1, add endpoint-absent PCIe/CLKREQ kernel support in RC2, then add RPMh regulator SLEEP/WAKE support in RC3. | First consolidated product candidates. Later RCs build on this source and DT boundary. |
| `4c65445`–`573ccbd` | Gate the PM8150B USB-PD PHY and its regulator while unplugged, preserve it across PD hard reset, fix locking, and restore compiler caching. | The corrected RC6 implementation is retained in PM8. CC detection remains alive while the heavier PD PHY is powered only for an attached session. |
| `9591d76`–`a12b330` | Probe the SM8250 display RSCC clock path with explicit guards and a separately identified diagnostic release. | A diagnostic discriminator, not a product fix. Its result prevented an unsupported display-clock theory from silently entering the product stack. |
| `c35d5b8`–`e32a0cd` | Add guarded QCA6390 shutdown support, preserve the required QMI transport boundary, handle the endpoint-absent PCIe case and test PCIe interconnect release. | Established low-level kernel mechanisms and diagnostics. Not every experimental combination is part of the current product kernel. |
| `b28764e`–`f91dba5` | Test ADSP all-state votes, QCA shutdown boundaries, MHI behavior, PCIe0 power-domain collapse, noirq timing, MMIO/MSI guards, rootless GDSC handling and a persistent crash console. | A set of deliberately narrow kernel discriminators. Some fixed real defects; others were rejected or retained only as diagnostics. Only the selected kernel changes entered RC17. |
| `b9804f4`–`7c1c213` | Add an awake PCIe idle-cycle control and keep DT phandle numbering stable for byte/semantic comparison. | Diagnostic control and reproducibility work; not an additional product power policy. |
| `e08db9c`–`ffa6f5b` | Move Retroid gamepad/RGB shared-rail handling into bound-driver system PM and make the RP5 fan start silently under an opt-in property. | Product kernel behavior: the shared rail is released/restored by its drivers, and the RP5 no longer gets the fixed fan pulse at kernel startup. |
| `260e8b1`–`6279872` | Preserve the firmware framebuffer through MSM takeover, verify visible display ownership, separate UEFI and Linux rotation, and externalize the early-display helper/assets behind hashes. | Product boot-display path with an embedded fallback and a replaceable, verified external bundle. |
| `8081392`–`de8e0c4` | Package the kernel as ARM64 EFI zboot, exercise the real init path in QEMU, remove blocking coulomb diagnostics from the product, create the source-equivalent product/diagnostic pair, share the compiler cache and correct the panel/retimer DT boundary. | Defines the product-versus-diagnostic build contract used today. Tag `phase1-baseline-2026-10-09` points at `f05b974`, an important frozen baseline rather than the current branch tip. |
| `1d9958f`–`ac7a8cd` | Add observable deferred QCA restore state, correct the kernel resume dependency order, and provide a bounded kernel fallback. | Current PM8 kernel tip. Its observable interface and fail-safe behavior are documented by the source and build artifacts. |

## Why some commits look administrative

Several recurring commit types are intentional and should not be read as new
kernel behavior:

- **`ci:` and workflow-routing commits** make a branch-specific profile
  buildable through a workflow already registered by GitHub. “Restore workflow”
  commits return that shared workflow to its normal profile after the build.
- **Release-name commits** give each artifact a unique `uname -r` identity so
  device evidence cannot be attributed to the wrong kernel.
- **Phandle normalization commits** prevent generated Device Tree numbering
  from looking like a semantic hardware change.
- **Verifier/checksum commits** make CI reject a build when a patch, compiled
  DTB or configuration differs from the intended controlled variable.
- **Compiler-cache commits** preserve the established GitHub Actions `ccache`
  path. They affect build time and reproducibility, not runtime policy.

Those commits are part of the evidence chain: they show exactly what was built
and keep a one-line experiment from silently including unrelated changes.

## Notable branches

Branches are retained as experiment records. The most useful entry points are:

| Branch | Purpose |
| --- | --- |
| `main` | Original UFS/diagnostic work, minimal-sleep proof, subsystem matrix, ADSP/SLPI experiments and exact-state platform suspend. |
| `codex/rp5-aop-ddr-diag` | AOP/DDR layout discovery and guarded residency decoding. |
| `codex/rp5-adsp-no-auto-ab` | Same-boot ADSP-off versus ADSP-running experiments. |
| `codex/rp5-pcie-drv-handoff` | Firmware-acknowledged PCIe ownership transfer around s2idle. |
| `codex/rp5-lpass-devote-fix` | Q6AFE LPASS hardware vote-handle repair. |
| `codex/rp5-lpass-pm-clock-backport` | LPASS macro runtime-PM clock backports. |
| `codex/rp5-audio-pcie-integration` | Combined audio and PCIe sleep candidate. |
| `codex/rp5-fg-coulomb-counter` | Fuel-gauge diagnostic kernel; not a product configuration. |
| `codex/rp5-rpmh-sleep-policy` | RPMh regulator SLEEP/WAKE A/B work. |
| `codex/rp5-pcie-offline-suspend` | Endpoint-absent PCIe suspend path. |
| `codex/rp5-final-rc1` | First consolidated RP5 product candidate. |
| `codex/rp5-rc2-clkreq-sleep` | Endpoint-absent PCIe/CLKREQ kernel support. |
| `codex/rp5-rc3-rpmh-suspend` | RC2 plus RPMh regulator suspend plumbing. |
| `codex/rp5-rc5-pdphy-gating` | USB-C PD-PHY power gating and build-cache restoration. |
| `codex/rp5-rc7-display-rscc` | Display RSCC diagnostic. |
| `codex/rp5-rc8-qca-fast-suspend` | QCA fast-MHI suspend experiment. |
| `codex/rp5-rc9-qca-fast-suspend-qrtr` | QCA fast-suspend with QRTR ordering work. |
| `codex/rp5-rc27-awake-idle-cycle` | Awake PCIe idle-cycle control and preserved diagnostic patch. |
| `codex/rp5-display-ownership2` | Final early-display ownership and external helper bundle. |
| `codex/rp5-rc6-pdphy-product` | Current product/diagnostic PM8 kernel lineage. |

Older branch names describe the experiment at the point where the branch was
created; they are not release rankings, and a larger RC number does not mean a
branch is safer or newer than the current product tip.

## Repository layout

```text
.github/workflows/   Manual, pinned GitHub Actions kernel builds
rocknix/             ROCKNIX kernel profiles, patches, DT sources and verifiers
prepare.py           Original Armada UFS experiment preparation
verify.sh            Original Armada artifact verification
ufs-lane-clocks.patch
                     Upstream Qualcomm UFS lane-clock fix used by both lines
```

Important ROCKNIX references:

- [`rocknix/MINIMAL-SLEEP.md`](rocknix/MINIMAL-SLEEP.md) explains the
  deliberately stripped `minsleep4` proof kernel.
- [`rocknix/README.md`](rocknix/README.md) documents the diagnostic profiles,
  subsystem matrix and early-display bundle.
- [`rocknix/FG-COULOMB-COUNTER.md`](rocknix/FG-COULOMB-COUNTER.md) explains the
  diagnostic fuel-gauge interface and its measurement limits.
- [`rocknix/CONSOLEOS-RC1.md`](rocknix/CONSOLEOS-RC1.md) is the authoritative
  description of the current PM8 product and paired diagnostic profiles.

## Build and safety boundary

All workflows are manual and read-only with respect to the RP5. They fetch the
pinned upstream sources, apply the selected profile, compile the kernel and
matching modules, verify the resolved config and compiled Device Tree, and
upload checksummed artifacts plus logs.

A successful GitHub Actions job proves that the intended source built and
passed its static checks. It does not prove that the artifact boots, enters
deep sleep, resumes correctly, or is suitable for normal use.

Do not copy a kernel over a live ROCKNIX installation by itself. The kernel,
modules, Device Tree and boot format must match. Device testing requires a
hash-verified one-shot boot, a known wake method and an automatic path back to
stock. Experimental images in this repository are not end-user releases.

## Licence

Kernel patches and Device Tree changes retain the licences of the projects and
files they modify. Upstream sources are fetched during CI rather than vendored
wholesale in this repository. See the individual files for SPDX identifiers
and attribution.
