# RP5 PCIe endpoint-off suspend kernel

Release: `7.2.0-consoleos-pcieoff1`

This diagnostic kernel retains the complete accepted Phase 4BF foundation:
the ADSP no-auto-boot policy, Q6AFE vote-handle repair, upstream LPASS macro
PM-clock fixes, Qualcomm PCIe DRV handoff, PM8150B coulomb diagnostics and RPMh
regulator sleep-state support.

It adds one bounded RC0 state path for the Phase 4BF QCA package-off test. If
the PCIe link is down and the Linux PCI hierarchy contains no downstream
device, the controller skips the impossible ADSP handoff and uses the existing
Qualcomm DRV resource-suspend helper. Resume restores those same clocks,
interconnect paths, OPP and supplies, then completes without sending an ADSP
reclaim that has no matching handoff.

A down link with an enumerated downstream device remains an error. A live link
still requires the original acknowledged ADSP handoff. The generic DesignWare
suspend path is not used for the endpoint-off case; removing
`qcom,drv-supported` already produced a non-resumable device test and is not a
supported configuration.

This is a diagnostic kernel, not a permanent product policy. It must first
pass a one-shot suspend/resume test with strict AOSD/CXSD/DDR residency proof,
post-resume PCIe resource-state evidence and verified stock rollback.
