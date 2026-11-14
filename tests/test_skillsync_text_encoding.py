"""Text files are read and written as UTF-8 explicitly (Windows default is the code page)."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PACKAGE = Path(__file__).resolve().parents[1] / "skillsync_workflow"


def calls_without_encoding(source):
    """Line numbers of `read_text`/`write_text` calls that do not name an encoding (keyword or positional)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("read_text", "write_text")
                and not any(keyword.arg == "encoding" for keyword in node.keywords)
                # Path.read_text(encoding) / Path.write_text(data, encoding) also take it positionally;
                # engine.read_text(path) is the dashboard's own helper (it reads UTF-8 itself)
                and len(node.args) < (1 if node.func.attr == "read_text" else 2)):
            found.append(node.lineno)
    return found


class ExplicitEncodingTests(unittest.TestCase):
    def test_every_read_text_and_write_text_names_its_encoding(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            offenders += [f"{path.name}:{line}" for line in calls_without_encoding(path.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_the_guard_still_catches_a_real_path_call_without_an_encoding(self):
        self.assertEqual(calls_without_encoding("p.read_text()"), [1])
        self.assertEqual(calls_without_encoding("p.write_text(data)"), [1])
        self.assertEqual(calls_without_encoding("p.write_text(data, newline='')"), [1])
        self.assertEqual(calls_without_encoding("x = 1\nPath('a').read_text(errors='replace')"), [2])

    def test_the_guard_accepts_a_named_encoding_in_either_form(self):
        self.assertEqual(calls_without_encoding("p.read_text(encoding='utf-8')"), [])
        self.assertEqual(calls_without_encoding("p.read_text('utf-8')"), [])
        self.assertEqual(calls_without_encoding("p.write_text(data, 'utf-8')"), [])
        self.assertEqual(calls_without_encoding("p.write_text(data, encoding='utf-8')"), [])
        self.assertEqual(calls_without_encoding("engine.read_text(path)"), [])


if __name__ == "__main__":
    unittest.main()
