#!/bin/sh
# Run on RP5. No suspend, boot changes, firmware flashing or direct ARC access.
set -eu
if [ "$#" -ne 4 ]; then
    echo 'usage: rp5-aop-diag.sh EXPECTED_BOOT MODULE_PATH MODULE_SHA256 load|status|starc-on|starc-off|unload' >&2
    exit 2
fi
expected_boot=$1
module_path=$2
module_hash=$3
action=$4
fw_hash=1e673233ef5ca65778db6effe5f8e57eef880d657ccc047ad44c49f0f302cc14
test "$(id -u)" = 0
test "$(uname -r)" = 7.2.0
test "$(cat /proc/sys/kernel/random/boot_id)" = "$expected_boot"
test "$(tr -d '\000' < /proc/device-tree/model)" = 'Retroid Pocket 5'
test "$module_hash" = a6a17df86f7258d700a717850efe878c343e682aa57507544502d72b519a7b9a
test "$(sha256sum "$module_path" | cut -d ' ' -f1)" = "$module_hash"
test "$(sha256sum /dev/disk/by-partlabel/aop_a | cut -d ' ' -f1)" = "$fw_hash"
test "$(sha256sum /dev/disk/by-partlabel/aop_b | cut -d ' ' -f1)" = "$fw_hash"
debug=/sys/kernel/debug/rp5_aop_diag
case "$action" in
    load) insmod "$module_path" firmware_sha256="$fw_hash" ;;
    status) cat "$debug/snapshots" ;;
    starc-on) printf starc_on > "$debug/control" ;;
    starc-off) printf starc_off > "$debug/control" ;;
    unload)
        printf starc_off > "$debug/control"
        rmmod rp5_aop_diag
        test ! -e "$debug"
        ;;
    *) echo 'Unknown action' >&2; exit 2 ;;
esac
