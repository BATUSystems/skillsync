"""D: LAYERS-INIT-NEXT-QUOTING: the `next` hint of `layers init` runs in cmd.exe too (double quotes and `/` on Windows)."""
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import layers  # noqa: E402


class QuoteArg(unittest.TestCase):
    def test_posix_keeps_shlex(self):
        with unittest.mock.patch.object(layers.os, 'name', 'posix'):
            self.assertEqual(layers._quote_arg('/data/Some Dir/x'), "'/data/Some Dir/x'")
            self.assertEqual(layers._quote_arg('/data/x'), '/data/x')

    def test_windows_uses_double_quotes_and_slashes_only_when_needed(self):
        with unittest.mock.patch.object(layers.os, 'name', 'nt'):
            self.assertEqual(layers._quote_arg('D:\\Some Dir\\x'), '"D:/Some Dir/x"')
            self.assertEqual(layers._quote_arg('D:\\Dir\\x'), 'D:/Dir/x')
            self.assertEqual(layers._quote_arg('D:\\a&b\\x'), '"D:/a&b/x"')
            self.assertNotIn("'", layers._quote_arg('D:\\Some Dir\\x'))


if __name__ == '__main__':
    unittest.main()
