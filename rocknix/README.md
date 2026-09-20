# ROCKNIX RP5 diagnostic kernel + UFS fix

The current build is `7.2.0-consoleos-diag-ufs2`. It also fixes the uninitialized `cstate` pointer introduced by ROCKNIX's `7.2/0010-msm-resource-cleanup.patch`: initialize it before the newly added `num_mixers = 0` write. A captured ufs1 boot oops at `dpu_crtc_atomic_check+0x31c` and the exact built instruction (`str wzr, [x25, #0x1a0]`, with x25 zero) identify this failure. The prior ufs1 image boots successfully with MSM initialization skipped. Graphics with this correction still requires device validation.

The build treats uninitialized-variable diagnostics as errors for `dpu_crtc.o` and preserves that source, debug object, and annotated disassembly in the artifacts. The original stock boot files and prior diagnostic payload must remain available during deployment.

Manually run `ROCKNIX RP5 diagnostics and UFS fix` in this repository's Actions. This is a native ARM kernel-only build on GitHub, not a distribution build. No device connection or installation occurs.

The source is the exact installed ROCKNIX `20260901` revision `1ebff24f36501fb6493beb2bf83bf2604536d9aa`, Linux 7.2 plus its 35 selected upstream/project/device patches. The UFS lane-clock fix `f07317a8d57f` is appended. The diagnostic configuration enables PM debugging, genpd debug, symbols, function/graph/syscall/scheduler tracing, kprobes, dynamic debug, BPF events and BTF. Heavy memory sanitizers and lock debugging are not enabled. Trace collection is not automatically activated.

The runner downloads the official prebuilt update archive, verifies its checksum, and extracts the stock flat kernel, embedded config, early-boot CPIO and firmware. The kernel must match the SHA-256 of our saved device baseline. The CPIO is checked for completeness and absence of old kernel modules. Firmware is reused from the prebuilt SYSTEM. Only kernel sources and modules are compiled; no emulators, frontend or system applications are rebuilt. GCC 14 differs from the original GCC 15.2 toolchain; all modules are therefore rebuilt together, and this is not a bit-identical reproduction.

The output is a distinct `7.2.0-consoleos-diag-ufs1` package with a raw ARM64 Image for the installed GRUB boot path, RP5 DTBs, matching modules, configuration, System.map, Module.symvers, BTF, patched UFS sources and provenance. A successful upload is not necessarily a successful build: look for a successful job and `BUILD-SUCCESS.txt`. No bootable full SD image is produced.

**Do not copy the new kernel alone over the SD's KERNEL.** ROCKNIX's stock initramfs checks that SYSTEM contains the running kernel's modules. Deployment must first integrate the matching module tree into a separate SYSTEM/test boot setup and preserve the original KERNEL, SYSTEM and boot configuration. This packaging and on-device validation remain separate steps. The existing stock backup is retained locally and is not published here.

The extra diagnostics do not provide electrical power measurements for every component, and the UFS fix is not yet proven to meet the RP5 standby target. First verify boot/storage/audio, then clock-reference balance and suspend residency, then battery drain.
