# ConsoleOS RP5 kernel RC5

This profile consolidates the device-tested native Linux fixes on the ROCKNIX
20260901 / Linux 7.2 baseline. It deliberately excludes diagnostic changes
that failed, had no measurable effect, or widened the test matrix.

RC5 keeps the exact device-proven RC3 base and adds one product-facing change:
the PM8150B PD PHY and its L2A regulator request remain off while USB-C is
unplugged. The CC/Type-C port stays active for cable detection. TCPM powers the
PD PHY before an attached session and powers it back down after disconnect.
This is the driver implementation justified by the approximately 5 mA Phase
4CS result; it does not ship that test's diagnostic compatible substitution.

The RC3 base is RC2 plus the exact three RPMh regulator suspend patches used
by the device-tested `pcieoff1` baseline. They mirror ACTIVE regulator requests
into the RPMh SLEEP cache, provide SLEEP/WAKE regulator operations, map existing
`regulator-state-mem` constraints onto s2idle, and retain the diagnostic mode
marker. RC5 adds no new DT rail policy.

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
- PM8150B Gen4 coulomb-counter diagnostics;
- Retroid gamepad rumble-device cleanup on serdev unbind/rebind;
- RPMh regulator SLEEP/WAKE support and ACTIVE-to-SLEEP cache mirroring;
- regulator-core application of existing `state_mem` policy during s2idle;
- the already-tested RPMh suspend-policy diagnostic marker.
- PM8150B PD-PHY regulator, voltage and load gating across Type-C
  attach/detach while retaining CC detection.

The DT is source-built from the proven Phase 4A wireless baseline, enables
exactly the accepted Phase 4AT product stack, and removes only
`regulator-always-on` from the shared MCU/RGB/gamepad rail. It also adds only
the PCIe0 sleep pinctrl state proven by Phase 4BL. It does not enable
QCE, Venus, CDSP, or SLPI. It does not include speculative RPMh rail policies,
PCIe PHY power-off, or the failed exact-PSCI-state platform driver.

RC5 retains BTF, ftrace, kprobes, PM diagnostics, and the fuel-gauge interface
so the one-shot qualification boot can prove residency and recovery. These can
be stripped only after the RC passes the product validation matrix.
