"""Text files are read and written as UTF-8 explicitly (Windows default is the code page)."""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PACKAGE = Path(__file__).resolve().parents[1] / "skillsync_workflow"


class ExplicitEncodingTests(unittest.TestCase):
    def test_every_read_text_and_write_text_names_its_encoding(self):
        offenders = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("read_text", "write_text")
                        and not any(keyword.arg == "encoding" for keyword in node.keywords)
                        # Path.read_text(encoding) / Path.write_text(data, encoding) also take it positionally;
                        # engine.read_text(path) is the dashboard's own helper (it reads UTF-8 itself)
                        and len(node.args) < (1 if node.func.attr == "read_text" else 2)):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
