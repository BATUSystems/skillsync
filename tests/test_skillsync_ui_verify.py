"""CORE-1140-T1 S1: skill `ui-verify`. Czysta logika `scripts/lib/*.mjs` ma testy `node:test` w `tests/ui_verify/`
(uruchamiane tu jednym przebiegiem), a szkielet skilla (zależności przypięte, lockfile, fixtury, brak gołych importów,
brak nazw klienta) sprawdzają testy czysto Pythonowe."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "library" / "skills" / "ui-verify"
FIXTURES = SKILL / "fixtures"
NODE = os.environ.get("SKILLSYNC_TEST_NODE") or shutil.which("node")


def node_major(binary):
    if not binary:
        return 0
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(re.match(r"v(\d+)", out).group(1))
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        return 0


NODE_MAJOR = node_major(NODE)
PINNED = {"playwright-core": "1.65.0", "pngjs": "7.0.0", "pixelmatch": "8.0.0"}


class UiVerifyNodeTests(unittest.TestCase):
    @unittest.skipIf(NODE_MAJOR < 22, "node >= 22 wymagany (brak lub za stary Node; ustaw SKILLSYNC_TEST_NODE)")
    def test_node_suite_passes(self):
        proc = subprocess.run([NODE, "--test", "tests/ui_verify/*.test.mjs"], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-4000:] + proc.stderr[-2000:])


class UiVerifySkeletonTests(unittest.TestCase):
    def test_package_json_pins_exact_versions(self):
        pkg = json.loads((SKILL / "package.json").read_text(encoding="utf-8"))
        deps = pkg.get("dependencies", {})
        for name, version in PINNED.items():
            self.assertEqual(deps.get(name), version, name)
            self.assertFalse(deps[name].startswith(("^", "~")), name)
        self.assertEqual(pkg.get("engines", {}).get("node"), ">=20.11")

    def test_lockfile_has_integrity_for_every_dependency(self):
        lock = json.loads((SKILL / "package-lock.json").read_text(encoding="utf-8"))
        self.assertIn(lock.get("lockfileVersion"), (2, 3))
        packages = lock.get("packages", {})
        for name, version in PINNED.items():
            entry = packages.get(f"node_modules/{name}")
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry.get("version"), version, name)
            self.assertTrue(str(entry.get("integrity", "")).startswith("sha"), name)

    def test_fixtures_and_cases(self):
        cases = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
        by_id = {c["id"]: c for c in cases["cases"]}
        self.assertEqual(set(by_id), {"mirrored-icon", "shifted-flag", "near-identical"})
        self.assertNotIn("contrast", json.dumps(cases))
        want = {"mirrored-icon": "FAIL", "shifted-flag": "FAIL", "near-identical": "PASS"}
        for fid, verdict in want.items():
            self.assertEqual(by_id[fid]["expected"]["pixelDiff"], verdict, fid)
            self.assertIn(by_id[fid]["expected"]["measure"], ("PASS", "FAIL"), fid)
            for side in ("reference", "candidate"):
                for ext in ("html", "png"):
                    self.assertTrue((FIXTURES / fid / f"{side}.{ext}").is_file(), f"{fid}/{side}.{ext}")
        self.assertFalse((FIXTURES / "contrast").exists())

    def test_no_bare_import_specifiers_in_scripts(self):
        static = re.compile(r"""(?:\bfrom\s*|\bimport\s*)(['"])(?!\.{1,2}/|node:)([^'"]+)\1""")
        dynamic = re.compile(r"""\bimport\s*\(\s*(['"])(?!\.{1,2}/|node:)([^'"]+)\1\s*\)""")
        bad = []
        for path in sorted((SKILL / "scripts").rglob("*.mjs")) if (SKILL / "scripts").is_dir() else []:
            text = path.read_text(encoding="utf-8")
            for rx in (static, dynamic):
                for m in rx.finditer(text):
                    bad.append(f"{path.relative_to(ROOT)}: {m.group(2)}")
        self.assertEqual(bad, [])

    def test_bare_import_regex_catches_examples(self):
        static = re.compile(r"""(?:\bfrom\s*|\bimport\s*)(['"])(?!\.{1,2}/|node:)([^'"]+)\1""")
        dynamic = re.compile(r"""\bimport\s*\(\s*(['"])(?!\.{1,2}/|node:)([^'"]+)\1\s*\)""")
        for line in ("import x from 'pngjs';", 'import {a} from "playwright-core"', "import 'pixelmatch';"):
            self.assertTrue(static.search(line), line)
        self.assertTrue(dynamic.search("const m = await import('pngjs')"))
        for line in ("import x from './a.mjs';", "import fs from 'node:fs'", "import y from '../b.mjs'", "await import('node:fs')"):
            self.assertFalse(static.search(line) or dynamic.search(line), line)

    def test_no_forbidden_names_in_skill(self):
        forbidden = ("virt" + "ual", "enroll" + "ment", "p" + "cg", "acad" + "em", "rek" + "rut")
        hits = []
        for path in sorted(SKILL.rglob("*")):
            if "node_modules" in path.parts:
                continue
            rel = str(path.relative_to(SKILL)).lower()
            for word in forbidden:
                if word in rel:
                    hits.append(f"{rel}: nazwa ({word})")
            if path.is_file() and path.suffix.lower() in (".png", ".jpg", ".ico", ".woff", ".woff2"):
                continue
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8").lower()
                except UnicodeDecodeError:
                    continue
                for word in forbidden:
                    if word in text:
                        hits.append(f"{rel}: treść ({word})")
        self.assertEqual(hits, [])


class UiVerifyPixelDiffScriptTests(unittest.TestCase):
    """S3: pixel-diff.mjs ma poprawny nagłówek, a --selftest kończy się kodem 0 albo 3 (nigdy 1/2)."""

    SCRIPT = SKILL / "scripts" / "pixel-diff.mjs"

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_header_is_valid(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/pixel-diff.mjs")
        header_url = (SKILL / "scripts" / "lib" / "header.mjs").as_uri()
        code = (
            "import fs from 'node:fs';"
            f"const {{ parseHeader }} = await import({json.dumps(header_url)});"
            "const h = parseHeader(fs.readFileSync(process.argv[1], 'utf8'));"
            "process.stdout.write(JSON.stringify(h));"
        )
        proc = subprocess.run([NODE, "--input-type=module", "-e", code, str(self.SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_selftest_exits_zero_or_three(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/pixel-diff.mjs")
        proc = subprocess.run([NODE, str(self.SCRIPT), "--selftest"], capture_output=True, text=True, timeout=120)
        self.assertIn(proc.returncode, (0, 3), proc.stdout[-2000:] + proc.stderr[-2000:])


class UiVerifyMeasureScriptTests(unittest.TestCase):
    """S4: measure.mjs ma poprawny nagłówek, a --selftest (atrapa adaptera, bez cache i Chrome) kończy się kodem 0."""

    SCRIPT = SKILL / "scripts" / "measure.mjs"

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_header_is_valid(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/measure.mjs")
        header_url = (SKILL / "scripts" / "lib" / "header.mjs").as_uri()
        code = (
            "import fs from 'node:fs';"
            f"const {{ parseHeader }} = await import({json.dumps(header_url)});"
            "const h = parseHeader(fs.readFileSync(process.argv[1], 'utf8'));"
            "process.stdout.write(JSON.stringify(h));"
        )
        proc = subprocess.run([NODE, "--input-type=module", "-e", code, str(self.SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_selftest_exits_zero_without_dependency_cache(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/measure.mjs")
        with tempfile.TemporaryDirectory() as empty_cache:
            env = {k: v for k, v in os.environ.items() if not k.startswith("UI_VERIFY_")}
            env["UI_VERIFY_CACHE_DIR"] = empty_cache
            proc = subprocess.run([NODE, str(self.SCRIPT), "--selftest"], capture_output=True, text=True, timeout=120, env=env, cwd=empty_cache)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])


class UiVerifyCaptureScriptTests(unittest.TestCase):
    """S5: capture.mjs ma poprawny nagłówek, a --selftest (atrapy, bez cache i Chrome) kończy się kodem 0."""

    SCRIPT = SKILL / "scripts" / "capture.mjs"

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_header_is_valid(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/capture.mjs")
        header_url = (SKILL / "scripts" / "lib" / "header.mjs").as_uri()
        code = (
            "import fs from 'node:fs';"
            f"const {{ parseHeader }} = await import({json.dumps(header_url)});"
            "const h = parseHeader(fs.readFileSync(process.argv[1], 'utf8'));"
            "process.stdout.write(JSON.stringify(h));"
        )
        proc = subprocess.run([NODE, "--input-type=module", "-e", code, str(self.SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_selftest_exits_zero_without_dependency_cache(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/capture.mjs")
        with tempfile.TemporaryDirectory() as empty_cache:
            env = {k: v for k, v in os.environ.items() if not k.startswith("UI_VERIFY_")}
            env["UI_VERIFY_CACHE_DIR"] = empty_cache
            proc = subprocess.run([NODE, str(self.SCRIPT), "--selftest"], capture_output=True, text=True, timeout=120, env=env, cwd=empty_cache)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])


class UiVerifyDiffScriptTests(unittest.TestCase):
    """S5: diff.mjs ma poprawny nagłówek, a --selftest (atrapy, bez cache i Chrome) kończy się kodem 0."""

    SCRIPT = SKILL / "scripts" / "diff.mjs"

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_header_is_valid(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/diff.mjs")
        header_url = (SKILL / "scripts" / "lib" / "header.mjs").as_uri()
        code = (
            "import fs from 'node:fs';"
            f"const {{ parseHeader }} = await import({json.dumps(header_url)});"
            "const h = parseHeader(fs.readFileSync(process.argv[1], 'utf8'));"
            "process.stdout.write(JSON.stringify(h));"
        )
        proc = subprocess.run([NODE, "--input-type=module", "-e", code, str(self.SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)

    @unittest.skipIf(NODE_MAJOR < 22, "needs-node: node >= 22 wymagany (ustaw SKILLSYNC_TEST_NODE)")
    def test_selftest_exits_zero_without_dependency_cache(self):
        self.assertTrue(self.SCRIPT.is_file(), "brak scripts/diff.mjs")
        with tempfile.TemporaryDirectory() as empty_cache:
            env = {k: v for k, v in os.environ.items() if not k.startswith("UI_VERIFY_")}
            env["UI_VERIFY_CACHE_DIR"] = empty_cache
            proc = subprocess.run([NODE, str(self.SCRIPT), "--selftest"], capture_output=True, text=True, timeout=120, env=env, cwd=empty_cache)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
