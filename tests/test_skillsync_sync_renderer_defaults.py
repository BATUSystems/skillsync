"""`scripts/sync_renderer_defaults.py`: the generator of the built-in Polish catalog block of the dashboard renderer (I18N-1)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_renderer_defaults.py"
RENDERER = ROOT / "library" / "skills" / "dashboard" / "assets" / "dashboard-render.js"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=120, cwd=ROOT)


class SyncRendererDefaults(unittest.TestCase):
    def test_check_passes_on_the_repository(self):
        result = run("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_fails_on_a_broken_copy_and_writes_nothing(self):
        source = RENDERER.read_text(encoding="utf-8")
        broken = source.replace('"ui.act.openPlan": "Otwórz plan"', '"ui.act.openPlan": "Otwórz plan (stary)"', 1)
        self.assertNotEqual(broken, source)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "dashboard-render.js"
            copy.write_text(broken, encoding="utf-8")
            result = run("--check", "--renderer", str(copy))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(copy.read_text(encoding="utf-8"), broken)

    def test_rewrite_repairs_the_copy_and_is_idempotent(self):
        source = RENDERER.read_text(encoding="utf-8")
        broken = source.replace('"ui.act.openPlan": "Otwórz plan"', '"ui.act.openPlan": "Otwórz plan (stary)"', 1)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "dashboard-render.js"
            copy.write_text(broken, encoding="utf-8")
            self.assertEqual(run("--renderer", str(copy)).returncode, 0)
            self.assertEqual(copy.read_text(encoding="utf-8"), source)
            self.assertEqual(run("--check", "--renderer", str(copy)).returncode, 0)

    def test_rewrite_writes_lf_only(self):
        source = RENDERER.read_text(encoding="utf-8")
        broken = source.replace('"ui.act.openPlan": "Otwórz plan"', '"ui.act.openPlan": "Otwórz plan (stary)"', 1)
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "dashboard-render.js"
            copy.write_bytes(broken.replace("\n", "\r\n").encode("utf-8"))
            self.assertEqual(run("--renderer", str(copy)).returncode, 0)
            self.assertNotIn(b"\r", copy.read_bytes())

    def test_missing_markers_or_file_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "no-markers.js"
            copy.write_text("const x = 1;\n", encoding="utf-8")
            self.assertEqual(run("--check", "--renderer", str(copy)).returncode, 2)
            self.assertEqual(run("--check", "--renderer", str(Path(tmp) / "missing.js")).returncode, 2)

    def test_renderer_comment_points_at_the_script(self):
        self.assertIn("scripts/sync_renderer_defaults.py", RENDERER.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
