#!/usr/bin/env python3
"""Decode RP5 AOP diagnostic module snapshots without accessing hardware."""
import argparse
import json
from pathlib import Path
import struct

FW_HASH = "1e673233ef5ca65778db6effe5f8e57eef880d657ccc047ad44c49f0f302cc14"


def decode(text):
    lines = text.splitlines()
    header = lines[0].split()
    if header[:4] != ['schema', '1', 'firmware', FW_HASH]:
        raise ValueError('Unknown snapshot schema/firmware')
    result = {'firmware_sha256': FW_HASH, 'snapshots': [],
              'limits': ['Kernel callbacks bracket suspend; they are not in-sleep sampling.',
                         'Raw DDR event values are not client blocker identities.',
                         '64 DDR slots and 59 AOP slots; physical order is not chronological.']}
    current = None
    for line in lines[1:]:
        fields = line.split()
        if fields[0] == 'secure_probe':
            if len(fields) != 9:
                raise ValueError('Invalid secure probe')
            result['secure_probe'] = {'address': int(fields[2], 16),
                                      'result': int(fields[4]), 'value': int(fields[6], 16)}
            result['ddr_unavailable'] = bool(int(fields[8]))
        elif fields[0] == 'snapshot':
            if len(fields) != 7 or fields[2] != 'valid' or fields[4] != 'ticks':
                raise ValueError('Invalid snapshot header')
            current = {'phase': fields[1], 'valid': bool(int(fields[3])),
                       'ticks_before': int(fields[5]), 'ticks_after': int(fields[6]),
                       'ddr': [], 'aop': [], 'aop_tail': []}
            result['snapshots'].append(current)
        elif fields[0] in ('ddr', 'aop'):
            if current is None or not current['valid'] or len(fields) != 6:
                raise ValueError('Unexpected ring record')
            ring, slot = fields[0], int(fields[1])
            words = [int(x, 16) for x in fields[2:]]
            if any(not 0 <= x <= 0xffffffff for x in words):
                raise ValueError('Expected unsigned 32-bit words')
            target = 'aop_tail' if ring == 'aop' and slot >= 59 else ring
            expected = len(current[target]) + (59 if target == 'aop_tail' else 0)
            if slot != expected or slot >= 64:
                raise ValueError('Duplicate or out-of-order physical slot')
            raw = struct.pack('<II', words[1], words[2])
            tag = raw.decode('ascii') if all(32 <= x <= 126 for x in raw) else None
            age_ticks = ((current['ticks_before'] & 0xffffffff) - words[0]) & 0xffffffff
            item = {'slot': slot, 'words': words, 'tag': tag,
                    'value': words[3], 'age_seconds_modulo_223_696': age_ticks / 19200000}
            if target == 'aop_tail':
                item = {'slot': slot, 'words': words}
            elif tag in ('STARCLOG', 'DDRLOG  '):
                item['logging_change'] = {'old': words[3] >> 24,
                                          'requested': (words[3] >> 16) & 255}
            current[target].append(item)
        else:
            raise ValueError('Unknown record type')
    if [s['phase'] for s in result['snapshots']] != ['suspend_noirq', 'resume_noirq', 'awake']:
        raise ValueError('Missing or unexpected phase')
    for snapshot in result['snapshots']:
        expected = [0 if result.get('ddr_unavailable') else 64, 59, 5]
        if snapshot['valid'] and [len(snapshot[k]) for k in ('ddr', 'aop', 'aop_tail')] != expected:
            raise ValueError('Truncated ring capture')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    print(json.dumps(decode(args.snapshot.read_text()), indent=2))
