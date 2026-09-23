import importlib.util
from pathlib import Path
import struct
import unittest

spec = importlib.util.spec_from_file_location('ddr', Path(__file__).with_name('decode-rp5-ddr-log.py'))
ddr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ddr)


def fixture(available=True):
    lines = [f'schema 1 firmware {ddr.FW_HASH} logging_dirty 0 qmp_result 0']
    if not available:
        lines += ['secure_probe address 0c360000 result -1 value 00000000 ddr_unavailable 1']
    lines += ['snapshot suspend_noirq valid 0 ticks 0 0',
              'snapshot resume_noirq valid 0 ticks 0 0',
              'snapshot awake valid 1 ticks 100 200']
    for ring in (['ddr', 'aop'] if available else ['aop']):
        for i in range(64):
            lo, hi = struct.unpack('<II', b'STARCLOG')
            lines.append(f'{ring} {i:02d} 00000060 {lo:08x} {hi:08x} 00010000')
    return '\n'.join(lines)


class DecoderTest(unittest.TestCase):
    def test_accepted_logging_transition(self):
        data = ddr.decode(fixture())
        s = data['snapshots'][-1]
        self.assertEqual(s['ddr'][0]['logging_change'], {'old': 0, 'requested': 1})
        self.assertEqual(len(s['aop']), 59)
        self.assertNotIn('tag', s['aop_tail'][0])

    def test_denied_access_is_not_zero_data(self):
        data = ddr.decode(fixture(False))
        self.assertTrue(data['ddr_unavailable'])
        self.assertEqual(data['snapshots'][-1]['ddr'], [])
        self.assertEqual(data['secure_probe']['result'], -1)

    def test_truncated_ring_refused(self):
        with self.assertRaises(ValueError):
            ddr.decode(fixture().rsplit('\n', 1)[0])

    def test_unknown_firmware_refused(self):
        with self.assertRaises(ValueError):
            ddr.decode(fixture().replace(ddr.FW_HASH, '0' * 64))

    def test_duplicate_slot_refused(self):
        with self.assertRaises(ValueError):
            ddr.decode(fixture().replace('ddr 01 ', 'ddr 00 '))


if __name__ == '__main__':
    unittest.main()
