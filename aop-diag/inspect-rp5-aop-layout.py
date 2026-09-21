#!/usr/bin/env python3
"""Reproduce the RP5-specific firmware resource/log layout; offline only."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

FW_HASH = '1e673233ef5ca65778db6effe5f8e57eef880d657ccc047ad44c49f0f302cc14'


def inspect(data):
    if hashlib.sha256(data).hexdigest() != FW_HASH:
        raise ValueError('Unsupported firmware hash; no addresses inferred')
    def offset(address):
        if 0 <= address < 0x14f08:
            return address + 0x3000
        if 0xe0000 <= address < 0xe605c:
            return address - 0xe0000 + 0x17f10
        raise ValueError(f'Address outside mapped image: {address:x}')
    def word(address):
        return struct.unpack_from('<I', data, offset(address))[0]
    def string(address):
        start = offset(address)
        return data[start:start+32].split(b'\0', 1)[0].decode('ascii')
    count = word(0xe077c)
    if count != 11:
        raise ValueError('Unexpected ARC resource count')
    resources = [string(word(0xe0724+8*i)) for i in range(count)]
    expected = ['cx.lvl', 'mx.lvl', 'ebi.lvl', 'lcx.lvl', 'lmx.lvl',
                'gfx.lvl', 'mss.lvl', 'ddr.lvl', 'mmcx.lvl', 'qphy.lvl', 'xo.lvl']
    if resources != expected:
        raise ValueError('Unexpected ARC resource table')
    return {'firmware_sha256': FW_HASH,
            'resource_ids': dict(enumerate(resources)),
            'arc_status_aop_local': word(0x5490),
            'arc_sequence_aop_local': word(0x54a0),
            'aop_local_ddr_log': word(0xe1cd4+0x30),
            'aop_local_event_log': word(0xe1cd4+0x38),
            'ddr_log_records': 64, 'event_log_records': 59,
            'initial_starc_log_flag': data[offset(0xe1030)],
            'initial_ddr_log_flag': data[offset(0xe1031)],
            'warning': 'Firmware addresses do not establish AP access permission. Direct AP reads of 0xb7f00c0 and 0xc360000 reset this RP5.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect(args.firmware.read_bytes()), indent=2))
