# ConsoleOS RetroArch load-completion patch

ConsoleOS ships a small GPLv3 RetroArch patch that makes `LOAD_STATE_SLOT`
observable instead of treating command dispatch as successful completion.

Pinned inputs:

- ROCKNIX distribution: `1ebff24f36501fb6493beb2bf83bf2604536d9aa`
- RetroArch: `bdba046fa6766380bc2457532f38e589df769aaf`
- Device/profile: `ROCKNIX`, `SM8250`, `aarch64`

The patch adds a monotonically increasing load generation and the
`GET_LOAD_STATE_STATUS` network-control command. `SUCCEEDED` is published
only after the core accepts the deserialized state. ConsoleOS uses the
generation to reject stale or unrelated completion replies.

The `rocknix-retroarch.yml` workflow copies this patch into the exact pinned
ROCKNIX package, uses ROCKNIX's public SM8250 compiler caches, builds only the
`retroarch` package and uploads the binary with hashes and provenance.

