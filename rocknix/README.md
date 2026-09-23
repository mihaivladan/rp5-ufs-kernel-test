# ROCKNIX RP5 diagnostic kernel + UFS fix

The current build is `7.2.0-consoleos-diag-ufs2`. It also fixes the uninitialized `cstate` pointer introduced by ROCKNIX's `7.2/0010-msm-resource-cleanup.patch`: initialize it before the newly added `num_mixers = 0` write. A captured ufs1 boot oops at `dpu_crtc_atomic_check+0x31c` and the exact built instruction (`str wzr, [x25, #0x1a0]`, with x25 zero) identify this failure. The prior ufs1 image boots successfully with MSM initialization skipped. Graphics with this correction still requires device validation.

The build treats uninitialized-variable diagnostics as errors for `dpu_crtc.o` and preserves that source, debug object, and annotated disassembly in the artifacts. The original stock boot files and prior diagnostic payload must remain available during deployment.

Manually run `ROCKNIX RP5 diagnostics and UFS fix` in this repository's Actions. This is a native ARM kernel-only build on GitHub, not a distribution build. No device connection or installation occurs.

The source is the exact installed ROCKNIX `20260901` revision `1ebff24f36501fb6493beb2bf83bf2604536d9aa`, Linux 7.2 plus its 35 selected upstream/project/device patches. The UFS lane-clock fix `f07317a8d57f` is appended. The diagnostic configuration enables PM debugging, genpd debug, symbols, function/graph/syscall/scheduler tracing, kprobes, dynamic debug, BPF events and BTF. Heavy memory sanitizers and lock debugging are not enabled. Trace collection is not automatically activated.

The runner downloads the official prebuilt update archive, verifies its checksum, and extracts the stock flat kernel, embedded config, early-boot CPIO and firmware. The kernel must match the SHA-256 of our saved device baseline. The CPIO is checked for completeness and absence of old kernel modules. Firmware is reused from the prebuilt SYSTEM. Only kernel sources and modules are compiled; no emulators, frontend or system applications are rebuilt. GCC 14 differs from the original GCC 15.2 toolchain; all modules are therefore rebuilt together, and this is not a bit-identical reproduction.

The output is a distinct `7.2.0-consoleos-diag-ufs1` package with a raw ARM64 Image for the installed GRUB boot path, RP5 DTBs, matching modules, configuration, System.map, Module.symvers, BTF, patched UFS sources and provenance. A successful upload is not necessarily a successful build: look for a successful job and `BUILD-SUCCESS.txt`. No bootable full SD image is produced.

**Do not copy the new kernel alone over the SD's KERNEL.** ROCKNIX's stock initramfs checks that SYSTEM contains the running kernel's modules. Deployment must first integrate the matching module tree into a separate SYSTEM/test boot setup and preserve the original KERNEL, SYSTEM and boot configuration. This packaging and on-device validation remain separate steps. The existing stock backup is retained locally and is not published here.

The extra diagnostics do not provide electrical power measurements for every component, and the UFS fix is not yet proven to meet the RP5 standby target. First verify boot/storage/audio, then clock-reference balance and suspend residency, then battery drain.

## GPU stale RPMh vote fix

The `gpu-rpmh-fix` profile applies upstream commit `d9108bfdb746`,
`drm/msm/a6xx: Fix stale rpmh votes after suspend`, to the exact ROCKNIX
Linux 7.2 source. Linux 7.2 has an inverted `GMU_STATUS_FW_START` condition in
`a6xx_rpmh_stop()`: after GMU firmware starts, the first shutdown clears the
flag and returns before requesting GPU RSCC power collapse. The upstream fix
runs the RSCC stop sequence when firmware was started and places the GMU CM3
in reset before the power-off request.

Run **ROCKNIX RP5 GPU stale RPMh vote fix**. The output release is
`7.2.0-consoleos-gpu-rpmh1` and includes the same diagnostics and BTF as the
working diagnostic kernel. Device deployment remains a one-shot test with the
matching module image and preserved stock recovery.

## ADSP no-auto-boot A/B diagnostic

The `adsp-no-auto-ab` profile retains the byte-identical Phase 6D ADSP-only
Device Tree and changes only `sm8250_adsp_resource.auto_boot` from true to
false. The ADSP remoteproc therefore registers in `offline` state without
loading or executing firmware. A guarded device harness can suspend once in
that state, explicitly start the exact `qcom/sm8250/adsp.mbn` through the
remoteproc sysfs interface, and suspend again in the same boot.

Run **ROCKNIX RP5 ADSP no-auto-boot A/B**. The output release is
`7.2.0-consoleos-adspab1`; it includes the upstream A6xx stale-RPMh fix,
matching modules, PM diagnostics and BTF. The workflow rejects any Phase 6D
DTB checksum change and any SM8250 ADSP resource block that does not contain
exactly one `auto_boot = false` assignment. No device deployment is automatic.

## RP5 Android ADSP before QCA Bluetooth

The `adsp-before-bluetooth` profile extends the no-auto-boot kernel with an
RP5-only `qcom,rproc` dependency on the QCA6390 Bluetooth child. The QCA serdev
probe synchronously boots and holds ADSP before registering Bluetooth, while
the full-product Device Tree selects `adsp.mdt`. The proprietary, hash-pinned
RP5 Android firmware is supplied only by the device test package and is not
stored in this public repository.

This is a discriminator, not a claimed fix: an older full-audio run already
booted the ROCKNIX ADSP before Bluetooth and failed. See
[`ADSP-BEFORE-BLUETOOTH.md`](ADSP-BEFORE-BLUETOOTH.md) for the exact unresolved
boundary. Run **ROCKNIX RP5 ADSP before Bluetooth** to build release
`7.2.0-consoleos-adspbt1`; no device deployment is automatic.

# RP5 subsystem re-enable matrix

The `reenable-matrix` profile builds eleven independent Device Tree candidates
plus cumulative display-plus-GPU, display-plus-GPU-plus-QUP2,
display-plus-GPU-plus-gamepad, and display-plus-GPU-plus-gamepad with UART16
active-only ICC tags integration candidates, followed by the Phase 3
active-only-gamepad-plus-UFS and Phase 4 UFS-plus-wireless cumulative
candidates, from the proven stock-kernel pruned baseline.
`reenable-matrix.json` is the source of truth for each coherent subsystem
group. `generate-reenable-matrix.py` creates the candidate DTS files, and
`verify-reenable-matrix.py` rejects any compiled artifact whose semantic delta
contains anything beyond the named `disabled`-to-`okay` status changes, except
that the active-only product-fix candidate must change exactly the four UART16
ICC tag cells from zero (`QCOM_ICC_TAG_ALWAYS`) to
`QCOM_ICC_TAG_ACTIVE_ONLY`.

Every candidate retains the stock `7.2.0` kernel, removes all eight CPU ICC
paths and all 56 CPU OPP peak-bandwidth values, and retains CPU frequency
control. The workflow also requires the compiled baseline to reproduce the
SHA-256 of the DTB that recorded stock-kernel AOSD, CXSD and DDR residency.

Run the public workflow **ROCKNIX RP5 subsystem re-enable matrix**. Its artifact
contains the proven baseline, all seventeen candidate DTBs, generated DTS files,
the manifest, semantic verification output, provenance and checksums. These
artifacts are build candidates only; none is installed automatically.
