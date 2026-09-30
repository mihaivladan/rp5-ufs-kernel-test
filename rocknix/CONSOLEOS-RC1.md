# ConsoleOS RP5 PCIe offline D3cold candidate

This branch keeps the exact RC3 kernel baseline and adds one bounded diagnostic
delta: when the RC0 endpoint is already absent and the validated offline path
is selected, let the driver use Linux 7.2's existing DesignWare D3cold host
teardown and Qualcomm host deinit instead of returning through the partial
`pcie_drv` resource path. Resume uses the matching existing host restore. The
ordinary live-endpoint/ADSP handoff path is unchanged.

The candidate does not add a PARF CLKREQ override, change GPIO80, alter L1SS,
or add any new device-tree power policy. The reused host deinit asserts PERST,
asks pwrctrl to power the endpoint down, powers off the QMP PHY, applies
`qcom_pcie_deinit_2_7_0()` (including `PHY_TEST_PWR_DOWN`, controller clocks and
supplies), and sets the DesignWare suspended state. Explicit markers and state
checks prove that the matching restore completed before offline completion.

This profile consolidates the device-tested native Linux fixes on the ROCKNIX
20260901 / Linux 7.2 baseline. It deliberately excludes diagnostic changes
that failed, had no measurable effect, or widened the test matrix.

RC3 is RC2 plus the exact three RPMh regulator suspend patches already used
by the device-tested `pcieoff1` baseline. They mirror ACTIVE regulator requests
into the RPMh SLEEP cache, provide SLEEP/WAKE regulator operations, map existing
`regulator-state-mem` constraints onto s2idle, and retain the diagnostic mode
marker. RC3 adds no new DT rail policy.

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

The DT is source-built from the proven Phase 4A wireless baseline, enables
exactly the accepted Phase 4AT product stack, and removes only
`regulator-always-on` from the shared MCU/RGB/gamepad rail. It also adds only
the PCIe0 sleep pinctrl state proven by Phase 4BL. It does not enable
QCE, Venus, CDSP, or SLPI. It does not include speculative RPMh rail policies,
a raw PARF override, or the failed exact-PSCI-state platform driver.

RC3 retains BTF, ftrace, kprobes, PM diagnostics, and the fuel-gauge interface
so the one-shot qualification boot can prove residency and recovery. These can
be stripped only after the RC passes the product validation matrix.
