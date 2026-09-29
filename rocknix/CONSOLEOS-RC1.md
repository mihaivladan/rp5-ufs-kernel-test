# ConsoleOS RP5 kernel RC2

This profile consolidates the device-tested native Linux fixes on the ROCKNIX
20260901 / Linux 7.2 baseline. It deliberately excludes diagnostic changes
that failed, had no measurable effect, or widened the test matrix.

RC2 is RC1 plus the Phase 4BL-proven QCA-off PCIe lifecycle: an absent
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
- PM8150B Gen4 coulomb-counter diagnostics;
- Retroid gamepad rumble-device cleanup on serdev unbind/rebind.

The DT is source-built from the proven Phase 4A wireless baseline, enables
exactly the accepted Phase 4AT product stack, and removes only
`regulator-always-on` from the shared MCU/RGB/gamepad rail. It also adds only
the PCIe0 sleep pinctrl state proven by Phase 4BL. It does not enable
QCE, Venus, CDSP, or SLPI. It does not include speculative RPMh rail policies,
PCIe PHY power-off, or the failed exact-PSCI-state platform driver.

RC2 retains BTF, ftrace, kprobes, PM diagnostics, and the fuel-gauge interface
so the one-shot qualification boot can prove residency and recovery. These can
be stripped only after the RC passes the product validation matrix.
