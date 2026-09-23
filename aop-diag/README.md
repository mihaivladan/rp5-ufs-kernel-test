# RP5 AOP diagnostic module

This is a diagnostic implementation for stock ROCKNIX `7.2.0` and the exact
RP5 firmware SHA256 embedded in the source. It is **not a sleep fix** and does
not yet expose the individual ARC client votes.

## Validated on hardware

- Loads and unloads on stock `7.2.0`; no replacement kernel/boot files required.
- Reads the existing AOP event buffer using scalar 32-bit accesses.
- The existing QMP driver acknowledges the fixed `starc_log` on/off messages.
  An acknowledgement does not independently verify handler execution.
- Protected `qcom_scm_io_readl` probes at `0xc370000`, `0xc360000` and
  `0xb7f00c0` return `-EINVAL`, without rebooting. No direct-access fallback.
- Offline decoder validation tests pass. Suspend-noirq/resume-noirq callbacks
  are implemented but have **not yet been tested through a suspend cycle**.

The first experimental revision `6f7b35e` read DDR bank `0xc360000` directly;
that read was followed by a reset. Do not deploy that revision or its artifact
SHA256 `5a60e0e261419f4baf42c10583eac55172b8abaa1a0f04b5b694d3e23a8eac18`.
A separate direct ARC read at `0xb7f00c0` also reset the RP5. Both direct
accesses are excluded from the corrected implementation. Firmware address
mapping does not establish application-processor access permission.

## Build and use

The `aop-diag.yml` workflow builds against pinned Linux 7.2, the pinned ROCKNIX
release patches, and the stock device configuration. The reference symbol
table is from the project's earlier native ROCKNIX build; stock has module
version CRCs disabled. The compiler differs from stock (GCC 14 vs 15), so the
module's real stock load/unload test is recorded above; this does not imply
general ABI compatibility for arbitrary modules.

Use `rp5-aop-diag.sh` on the device to validate boot ID, kernel, board, module
checksum and both AOP partition hashes before loading. It is pinned to the
tested corrected artifact from Actions run `35589385510`:
`a6a17df86f7258d700a717850efe878c343e682aa57507544502d72b519a7b9a`.
Source changes require rebuilding and revalidating the pinned artifact.

`/sys/kernel/debug/rp5_aop_diag/snapshots` returns saved callback snapshots
and an awake snapshot. `control` accepts `starc_on`, `starc_off`, and three
explicit secure probes: `scm_aop`, `scm_ddr`, `scm_arc`. Unknown input fails.
No arbitrary addresses or arbitrary firmware messages are accepted.

Logging has a freezable five-minute rollback and unload rollback. It changes
only the `starc_log` flag, whose firmware image default is zero. No frequency,
voltage or sleep constraint is modified. No command requests system suspend.
On timeout, command receipt can be uncertain; rollback is still attempted.

`decode-rp5-ddr-log.py` decodes module output and explicitly represents missing
DDR data. `inspect-rp5-aop-layout.py` reproduces the static resource table and
log locations from a local exact-hash firmware file. Do not upload firmware.

```sh
python3 -m unittest discover -s aop-diag -p 'test_rp5_ddr_decoder.py'
```

Callback snapshots bracket the device-suspend phase; they are not samples
taken during deep sleep. An actual entry still requires same-boot residency
count and duration increases plus a successful suspend/resume.
