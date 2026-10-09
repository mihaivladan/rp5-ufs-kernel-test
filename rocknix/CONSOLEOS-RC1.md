# ConsoleOS RP5 RC17 product-PM kernel

## Product PM3 kernel packaging

The product build emits Linux 7.2's ARM64 `vmlinuz.efi` zboot image with a
zstd-compressed payload as `boot/KERNEL`. The resolved config must disable the
RAID6 boot benchmark and the product-excluded debug/tracing families: DWARF and
BTF debug info, `KALLSYMS_ALL`, ftrace, kprobes, uprobes, BPF events, dynamic
debug and PM debug. `KALLSYMS`, `DEBUG_FS`, ordinary BPF networking support,
the built-in early-display fallback and the external helper bundle remain.
The generic ARM PL011 UART and its console are built in solely so the exact
device KERNEL can produce boot evidence on QEMU's standard ARM `virt` machine;
the RP5 has no PL011 node, so this adds no device boot or lifecycle path.

This profile consolidates the device-tested native Linux fixes on the ROCKNIX
20260901 / Linux 7.2 baseline. It deliberately excludes diagnostic changes
that failed, had no measurable effect, or widened the test matrix.

The profile keeps the device-proven RC17 base and includes the previously
qualified RC6 USB-C product change:
the PM8150B PD PHY and its L2A regulator request remain off while USB-C is
unplugged. The CC/Type-C port stays active for cable detection. TCPM powers the
PD PHY before an attached session and powers it back down after disconnect.
The power-down path also verifies the PMIC's physical CC-attached bit, so
TCPM's temporary `attached=false` role update during a PD hard reset cannot
cycle the PHY or its supply.
This is the driver implementation justified by the approximately 5 mA Phase
4CS result; it does not ship that test's diagnostic compatible substitution.

The RC3 base is RC2 plus the exact three RPMh regulator suspend patches used
by the device-tested `pcieoff1` baseline. They mirror ACTIVE regulator requests
into the RPMh SLEEP cache, provide SLEEP/WAKE regulator operations, map existing
`regulator-state-mem` constraints onto s2idle, and retain the diagnostic mode
marker.

The product DT makes the shared MCU/RGB/gamepad rail controllable and declares
the gamepad as a consumer. Bound gamepad and RGB drivers release that rail
during system sleep and restore their hardware before userspace resumes.

The RP5 fan requests a zero-percent startup state. Existing ROCKNIX behavior
is unchanged on every board without that opt-in property: those boards retain
the downstream fixed PWM 70 probe policy. On RP5 this removes that audible
startup pulse; normal cooling requests can still raise the fan immediately.

RC6 corrects the attached-session locking audit finding in the first RC5
build: PD signal transmission no longer leaves `state_lock` held, and the
threaded receive IRQ no longer unlocks a mutex it did not acquire. The
unplugged power-gating behavior measured by Phase 4CT is otherwise unchanged.

RC2 was RC1 plus the Phase 4BL-proven QCA-off PCIe lifecycle: an absent
endpoint uses the validated offline resource-suspend path, GPIO80 changes from
live `pci_e0` CLKREQ# to an input GPIO with pull-up only for suspend, and the
default PCIe mux is restored before userspace can power and rescan QCA6390.

Included kernel changes:

- upstream UFS lane-clock fix;
- DPU CRTC state initialization fix;
- upstream Adreno A6xx stale RPMh-vote fix;
- SM8250 ADSP no-auto-boot control for selecting the pinned RP5 firmware;
- Q6AFE LPASS hardware vote-handle repair;
- four upstream LPASS macro PM-clock conversions;
- Qualcomm PCIe `pcie_drv` ownership handoff around suspend;
- endpoint-absent PCIe resource suspend with fail-closed downstream-device
  detection;
- Android-style PCIe0 `default`/`sleep` pinctrl switching for GPIO80;
- the PM8150B Gen4 coulomb-counter diagnostic patch is deliberately excluded
  from the product profile: its synchronous `charge_counter` read blocks the
  battery `uevent` path for repeated timeouts during early userspace startup;
- Retroid gamepad rumble-device cleanup on serdev unbind/rebind;
- RPMh regulator SLEEP/WAKE support and ACTIVE-to-SLEEP cache mirroring;
- regulator-core application of existing `state_mem` policy during s2idle;
- the already-tested RPMh suspend-policy diagnostic marker.
- PM8150B PD-PHY regulator, voltage and load gating across Type-C
  attach/detach while retaining CC detection.
- Retroid gamepad and HTR3212 bound-driver system PM for the shared rail.
- opt-in pwm-fan startup policy, with RP5 starting at 0% rather than
  ROCKNIX's fixed PWM 70 probe policy.
- `CONFIG_RAID6_PQ_BENCHMARK=n`; the unused boot-time RAID6 implementation
  benchmark is not run.

The kernel keeps the accepted early-display helper and its assets embedded as
a frozen fallback.  Normal boot may additionally load
`/boot/redika/redika-early-display.cpio`.  That archive unpacks only below
`/consoleos/early-display-external`, so a missing, truncated, or partially
unpacked archive cannot overwrite the fallback.  The embedded `/init` selects
the external helper only after its complete `SHA256SUMS` passes with the exact
initramfs BusyBox.  A selector error or external-helper failure runs the
embedded helper.  The external CPIO is reproducible and self-contained, which
makes later helper or artwork releases one hash-locked file replacement rather
than another kernel build.

The DT is source-built from the proven Phase 4A wireless baseline, enables
exactly the accepted Phase 4AT product stack, and removes only
`regulator-always-on` from the shared MCU/RGB/gamepad rail. It also adds only
the PCIe0 sleep pinctrl state proven by Phase 4BL. It does not enable
QCE, Venus, CDSP, or SLPI. It does not include speculative RPMh rail policies,
PCIe PHY power-off, or the failed exact-PSCI-state platform driver.

This profile retains the fuel-gauge interface and the read-only Qualcomm sleep
residency files needed by the product validation matrix. Product-excluded BTF,
ftrace, kprobes and PM debug facilities are disabled.
