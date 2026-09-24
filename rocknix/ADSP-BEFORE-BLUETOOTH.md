# RP5 ADSP-before-Bluetooth diagnostic kernel

This profile tests one narrow, not-yet-proven condition: whether the RP5's
Android ADSP firmware can preserve deep sleep when Linux completes ADSP boot
before the QCA6390 Bluetooth driver begins controller setup.

It is **not** based on the broader claim that ADSP-before-Bluetooth alone is
already known to work.  The original Phase 6 full-audio runs booted the
ROCKNIX ADSP around six seconds before Bluetooth and still recorded zero AOSD,
CXSD and DDR low-power residency.  Phase 6Y passed with the Android ADSP
firmware, but Bluetooth was loaded only after ADSP had also completed one deep
suspend/resume cycle.  The kernel profile deliberately removes that
intermediate suspend so the next run can distinguish enforced boot ordering
from a first-suspend/settling requirement.

The profile:

- keeps SM8250 ADSP remoteproc auto-boot disabled;
- changes the full-product/full-audio RP5 DT to select `adsp.mdt`;
- adds `qcom,rproc = <&adsp>` only to the RP5 QCA6390 Bluetooth node; and
- makes `hci_qca` synchronously call `rproc_boot()` and retain the corresponding
  power/reference counts before `hci_uart_register_device()`.

The build emits two candidate Device Trees.  The Bluetooth-only slice is
checked against a canonical, phandle-normalized semantic fingerprint of the
exact Phase 6W/6Y baseline before adding only the Android firmware name and
remoteproc dependency; it is the controlled first test.  The full-audio/product
candidate is retained as the reusable foundation for later integration tests
after that narrow discriminator is answered.

Boards without `qcom,rproc` retain the existing QCA behavior.  Removal or a
failed probe releases both the remoteproc power reference and object reference.

The proprietary RP5 firmware is intentionally not stored or built into this
public repository.  A one-shot test package must provide the already
hash-pinned `ADSP.HT.5.3-00632-SM8250-1` files through `firmware_class.path`.

Build with:

```sh
ROCKNIX_PROFILE=adsp-before-bluetooth bash rocknix/build.sh prepare
ROCKNIX_PROFILE=adsp-before-bluetooth bash rocknix/build.sh compile
```

No boot result is claimed by a successful build.  Device success still
requires a same-boot positive AOSD, CXSD, or DDR low-power count and duration
delta plus successful suspend/resume.
