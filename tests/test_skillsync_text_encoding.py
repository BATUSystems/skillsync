"""Text files are read and written as UTF-8 explicitly (Windows default is the code page), reports are UTF-8."""
import ast
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.test_skillsync import skillsync  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / "skillsync_workflow"


class ExplicitEncodingTests(unittest.TestCase):
    def test_every_read_text_and_write_text_names_its_encoding(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and not any(keyword.arg == "encoding" for keyword in node.keywords)):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


class Utf8StreamsTests(unittest.TestCase):
    def test_windows_streams_are_reconfigured_to_utf8(self):
        out, err = mock.Mock(), mock.Mock()
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(sys, "stderr", err):
            skillsync.use_utf8_streams()
        out.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        err.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_other_systems_and_streams_without_reconfigure_are_left_alone(self):
        out = mock.Mock()
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(sys, "stdout", out):
            skillsync.use_utf8_streams()
        out.reconfigure.assert_not_called()
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(sys, "stdout", object()), \
                mock.patch.object(sys, "stderr", object()):
            skillsync.use_utf8_streams()  # no reconfigure attribute: no error


if __name__ == "__main__":
    unittest.main()
