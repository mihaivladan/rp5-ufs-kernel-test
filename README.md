# RP5 UFS clock-fix kernel experiment

The separate [ROCKNIX minimal sleep profile](rocknix/MINIMAL-SLEEP.md) builds a
one-purpose native Linux kernel for a local SD suspend/resume proof with optional
RP5 hardware compiled out. Its workflow is `ROCKNIX RP5 minimal sleep kernel`.

This directory is a standalone, manually triggered GitHub Actions build kit. It builds only an Armada kernel package on an `ubuntu-24.04-arm` runner. It does not build ROCKNIX, publish a container image, connect to a device, or install anything.

## Source and change

The baseline is `armada-os/armada-packages` commit `24d88394fd6ae777e7616c3ec245a7a35b5d2f81`: Linux 7.2.3 plus 144 Armada patches. [Its successful build](https://github.com/armada-os/armada-packages/actions/runs/34767879850) signed kernel package digest `sha256:4659cccbecd0015e5ab6ffb566d59e19b5514f889914b7ecbba2dad65addb550`, exactly the package pinned by the installed Armada release `20260915.feca679`.

The only additional driver patch is upstream [f07317a8d57f382ec505597816271dd72ffa20c7](https://kernel.googlesource.com/pub/scm/linux/kernel/git/next/linux-next/+/f07317a8d57f382ec505597816271dd72ffa20c7), “scsi: ufs: ufs-qcom: Enable only lane clocks in lane clock APIs,” by Nitin Rawat. The patch bytes are checksum-verified. The kernel release becomes `7.2.3-consoleos-ufs1`; configuration and final UFS source are exported for inspection. Compiler/package resolution can differ from the original build, so this is not a bit-for-bit reproduction.

## Build

Place only this directory's contents in a separate GitHub repository, including `.github`. Dispatch **RP5 UFS test kernel** from Actions. The checkout uses a fixed revision and fetches only the kernel recipe and toolchain settings. The upstream Podman recipe downloads and compiles Linux on the runner. Its 144 patches are followed by the UFS patch, with the original strict patch checks retained.

The artifact contains the compressed kernel/modules/device-tree package, its checksum, resolved kernel config, System.map, final UFS sources, package listing, source provenance and build log. A failed job may upload partial results for diagnosis; only a successful build AND successful verification qualify for deployment preparation.

This workflow uses a standard hosted ARM runner, read-only repository permissions, no registry login and no deployment secrets. It has a 120-minute timeout and seven-day artifact retention. No full kernel source is downloaded to the Mac. A recent upstream kernel job took 47m21s; that is a reference, not a promised duration. GitHub account/repository eligibility and usage limits must be checked before dispatch.

## Validation and deployment boundary

Local checks cover script syntax, recipe preparation, and patch applicability after the Armada patches touching the UFS source. Compilation and runtime behavior remain untested until CI and device validation respectively.

This produces a kernel package, not a bootable SD image. Armada uses immutable bootc deployments and an Android-format `/boot/efi/KERNEL` containing the kernel, initramfs and DTBs. The UFS driver is built into the kernel; replacing a `.ko` is not possible. Deployment requires the matching modules, regenerated initramfs, a preserved stock deployment and a verified recovery boot image. Do not overwrite the live kernel or decrement clock counts manually.

The ROCKNIX SD is the intended recovery environment; verify it can boot and access the internal Armada ESP before installing. The next engineering step after a successful build is preparing and validating a separate deployment. The test order is: boot/version/storage/audio checks; repeat the bounded XO-reference audit; inspect native suspend residency; measure standby drain only after those pass. The UFS fix may remove this leak without resolving every blocker to deeper sleep.
