# RP5 PM8150B coulomb diagnostics

This profile extends the accepted `audio-pcie-integration` kernel. It does not
change the RP5 device tree or remove any component from that stack.

The ROCKNIX PM8150B fuel-gauge driver normally exports only whole-percent
capacity. This diagnostic adds Qualcomm Gen4 direct-memory reads for:

- `CC_SOC_SW`, used for the standard `charge_counter` power-supply property;
- `CC_SOC`, scaled by nominal capacity;
- `BATT_SOC`, scaled by learned capacity;
- nominal and learned capacity; and
- both direct monotonic-SOC bytes.

The driver reads PM8150B `REVISION4` and selects Qualcomm's v1 or v2 SRAM
layout. Unknown revisions do not expose decoded results. SRAM contents are
never written. The driver does write the documented DMA arbitration/control
bits needed to obtain and release read access, and releases access after every
snapshot.

On the device, the standard estimate is:

```text
/sys/class/power_supply/battery/charge_counter
```

The auditable raw snapshot is exposed as `gen4_diagnostics` on the qcom-fg
platform device. Locate it without assuming a platform path:

```sh
find /sys/devices -name gen4_diagnostics -type f -print
```

`charge_counter` is a fuel-gauge estimate in microamp-hours, not a laboratory
coulomb-meter measurement. Capacity learning can move the derived value in a
direction that does not match a short discharge interval. Preserve and compare
the raw counters, voltage, current, elapsed time, and hardware deep-residency
deltas before using it to estimate suspend drain.
