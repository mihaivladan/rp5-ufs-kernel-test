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

The `rocknix-retroarch.yml` workflow does not invoke the ROCKNIX package
pipeline or purge the hosted runner. It checks out only the pinned ROCKNIX
RetroArch recipe/patches and pinned RetroArch source, applies those patches
plus the ConsoleOS patch, then cross-compiles RetroArch directly with the
aarch64 GNU toolchain.

The configure options are the exact options selected by the pinned ROCKNIX
recipe for `ROCKNIX`, `SM8250`, `aarch64`: Wayland, full OpenGL (SM8250 has
`PREFER_GLES=no`), Vulkan and Vulkan display. The corresponding make-time
feature switches are preserved too. `build-standalone-aarch64.sh` is the
reproducible GPL build script. Ubuntu 25.04 supplies the compiler, headers and
pkg-config metadata, but the final link resolves shared libraries from the
checksum-verified ROCKNIX 20260901 runtime image. Every artifact contains the
resolved build command, configure flags, make flags, toolchain versions,
config log, hashes and provenance.

The build fails unless every resulting `NEEDED` soname exists in that ROCKNIX
runtime, every required `GLIBC_`, `GLIBCXX_` and `GCC_` symbol version is
provided there, the ELF interpreter matches stock, and `retroarch --features`
under QEMU exactly matches the stock ROCKNIX binary. These host-side checks do
not replace the required RP5 smoke test: the candidate must still launch the
pinned SwanStation game with the qualified drivers and complete a correlated
Save then Load before it can be accepted.

The completed artifact is cached under a key made only from the RetroArch
commit, ROCKNIX commit and ConsoleOS patch SHA-256. An unchanged tuple skips
all toolchain installation and compilation and simply republishes the verified
cached artifact under the stable `consoleos-rocknix-sm8250-retroarch` name used
by the ConsoleOS image build.

