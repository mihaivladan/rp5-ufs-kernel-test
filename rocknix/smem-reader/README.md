# ConsoleOS read-only ADSP SMEM reader

This external diagnostic module targets only the hash-pinned Phase 6H kernel,
`7.2.0-consoleos-adspab1`. It asks the existing Qualcomm SMEM driver for ADSP
host `2` items `606`, `624`, and `634` and exposes the results through three
mode-`0400` debugfs files under `/sys/kernel/debug/consoleos_smem/`.

The reader never calls `qcom_smem_alloc()`, has no write file operation, caps
each record at 8192 bytes, and preserves all accepted bytes as hex. A single
file read takes three copies approximately 10 ms apart and reports whether they
were identical. The copies are an observability aid, not an SMEM consistency
protocol; a changing DSP-owned record is reported as unstable.

The workflow reconstructs the exact patched Linux 7.2 source, validates the
saved Phase 6H `.config` and `Module.symvers` hashes, runs only `prepare` and
`modules_prepare`, and builds this external module. It does not build or replace
the kernel, DTB, or the installed kernel module set.
