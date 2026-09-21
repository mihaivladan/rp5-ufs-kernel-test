# RP5 minimal sleep kernel

`ROCKNIX RP5 minimal sleep kernel` builds a distinct
`7.2.0-consoleos-minsleep2` kernel from the same pinned ROCKNIX 20260901
source, stock initramfs and firmware used by the validated UFS2 diagnostic
kernel.

This profile retains SD boot/storage, the PMIC Power button, CPU idle/PSCI,
RPMh/AOSS, regulators, clocks, interconnects, filesystems and Qualcomm sleep
statistics. It compiles out display/GPU, camera/video, audio, PCIe/Wi-Fi,
Bluetooth, USB, internal UFS, DSP remote processors, RPMsg, QCE, interconnect
bandwidth monitors, all Qualcomm GENI users, and the PWM fan driver. Its
matched `sm8250-retroidpocket-rp5-minsleep2.dtb` explicitly disables those
compiled-out consumers and their dedicated fixed rails. CI decompiles the
result through `fdtget`, rejects any expected consumer not marked disabled,
and rejects any retained SD, power-key, GCC, AOSS, RPMh or interconnect node
that became disabled.
The proof recorder must therefore run locally from SD; this kernel is not
expected to provide SSH or a visible display.

The workflow only builds and uploads a kernel/module/DTB artifact. It does not
modify a device. Deployment must use a separate module image, a local automatic
recorder and a consumed-before-boot one-shot GRUB entry with stock fallback.
