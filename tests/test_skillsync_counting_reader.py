"""D: LINGER-READER-METHODS: every method of `rfile` that consumes bytes is counted by `_CountingReader` (a body read through
`readlines`, `readinto1` or iteration is a read body, so the connection does not linger for nothing)."""
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402

BODY = b'first\nsecond\nthird\n'


def reader():
    return ps._CountingReader(io.BufferedReader(io.BytesIO(BODY)))


class CountingReader(unittest.TestCase):
    def test_every_consuming_method_counts_the_bytes_it_hands_out(self):
        cases = {
            'read(all)': lambda r: r.read(),
            'read(n)': lambda r: r.read(7),
            'read1': lambda r: r.read1(5),
            'readline': lambda r: r.readline(),
            'readlines': lambda r: r.readlines(),
            'readlines(hint)': lambda r: r.readlines(8),
            'readinto': lambda r: r.readinto(bytearray(4)),
            'readinto1': lambda r: r.readinto1(bytearray(4)),
            'iteration': lambda r: list(r),
        }
        for name, call in cases.items():
            with self.subTest(method=name):
                counting = reader()
                handed_out = call(counting)
                expected = (len(handed_out) if isinstance(handed_out, (bytes, bytearray))
                            else handed_out if isinstance(handed_out, int)
                            else sum(len(line) for line in handed_out))
                self.assertGreater(expected, 0)
                self.assertEqual(counting.count, expected)

    def test_peek_does_not_consume_and_does_not_count(self):
        counting = reader()
        self.assertTrue(counting.peek(3))
        self.assertEqual(counting.count, 0)
        self.assertEqual(counting.read(), BODY)
        self.assertEqual(counting.count, len(BODY))

    def test_mixed_reads_add_up(self):
        counting = reader()
        counting.readline()
        counting.readlines()
        self.assertEqual(counting.count, len(BODY))


if __name__ == '__main__':
    unittest.main()
