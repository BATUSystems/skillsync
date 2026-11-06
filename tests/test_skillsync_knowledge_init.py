"""G6 contract: `skillsync knowledge init` skeleton and the doctor warning for a missing lessons index."""
import json
import subprocess
import sys
import unittest

import yaml

from tests.test_skillsync_workflow import CLI, WorkflowFixture

FILES = ("docs/domain/index.yaml", "docs/lessons/index.yaml", "docs/README.md")


class KnowledgeInit(WorkflowFixture):
    def run_init(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), "knowledge", "init", str(self.root), *args, "--json"],
                                capture_output=True, text=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout)

    def test_creates_three_missing_files_with_valid_yaml(self):
        report = self.run_init()
        self.assertIn("created", report)
        self.assertIn("existing", report)
        self.assertEqual(len(report["created"]), 3, report)
        self.assertEqual(report["existing"], [])
        for name in FILES:
            self.assertTrue((self.root / name).is_file(), name)
        self.assertEqual(yaml.safe_load((self.root / FILES[0]).read_text(encoding="utf-8")),
                         {"version": 1, "entries": []})
        self.assertEqual(yaml.safe_load((self.root / FILES[1]).read_text(encoding="utf-8")),
                         {"version": 1, "lessons": []})
        self.assertTrue((self.root / FILES[2]).read_text(encoding="utf-8").strip())

    def test_second_run_overwrites_nothing(self):
        self.run_init()
        readme = self.root / "docs/README.md"
        readme.write_bytes("# Moje README\nzmienione ręcznie ąę\n".encode("utf-8"))
        before = {name: (self.root / name).read_bytes() for name in FILES}
        report = self.run_init()
        self.assertEqual(report["created"], [])
        self.assertEqual(len(report["existing"]), 3, report)
        self.assertTrue(any(str(item).endswith("docs/README.md") for item in report["existing"]), report)
        self.assertEqual({name: (self.root / name).read_bytes() for name in FILES}, before)

    def test_dry_run_creates_nothing(self):
        report = self.run_init("--dry-run")
        self.assertIn("created", report)
        self.assertFalse((self.root / "docs").exists())

    def test_respects_parent_domain_setting(self):
        self.manifest["workflow"]["knowledge"] = {"domain": "../docs/domain"}
        self.save()
        report = self.run_init()
        self.assertEqual(len(report["created"]), 3, report)
        self.assertEqual(yaml.safe_load((self.base / "docs/domain/index.yaml").read_text(encoding="utf-8")),
                         {"version": 1, "entries": []})
        self.assertTrue((self.base / "docs/lessons/index.yaml").is_file())
        self.assertTrue((self.base / "docs/README.md").is_file())
        self.assertFalse((self.root / "docs/domain/index.yaml").exists())
        self.assertFalse((self.root / "docs/lessons/index.yaml").exists())

    def test_doctor_warns_about_missing_lessons_index_until_init(self):
        self.manifest["skills"] = ["feature"]
        self.save()

        def findings():
            result = self.cli("doctor")
            self.assertNotIn("Traceback", result.stderr)
            return [f for f in json.loads(result.stdout)["findings"] if "Missing lessons index" in f["reason"]]

        found = findings()  # exit code ignored: unlinked fixture has unrelated errors
        self.assertEqual([f["level"] for f in found], ["warning"])
        self.run_init()
        self.assertEqual(findings(), [])


class KnowledgeEscapeAndRoute(WorkflowFixture):
    def listing(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*"))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=20, cwd=self.root)

    def test_escaping_paths_fail_without_creating_files(self):
        # Project two levels below the fixture's own temp dir: `../../x` resolves to `self.base / "x"`, so the
        # test checks only a directory it created (never the shared parent of the temp dir).
        self.root = self.base / "nest" / "project"
        self.root.mkdir(parents=True)
        escape = self.base / "x"
        for key, value in (("domain", "../../x/docs/domain"), ("lessons", "../../x/docs/lessons")):
            with self.subTest(key=key):
                self.manifest["workflow"]["knowledge"] = {key: value}
                self.save()
                before = self.listing()
                result = self.run_cli("knowledge", "init", str(self.root), "--json")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "BLOCKED")
                label = "Domain knowledge" if key == "domain" else "Lessons"
                self.assertIn(f"{label} path escapes the project parent", report["reason"])
                self.assertEqual(self.listing(), before)
                self.assertFalse(escape.exists())

    def test_route_reads_lessons_from_configured_location(self):
        self.manifest["workflow"]["knowledge"] = {"lessons": "../docs/lessons"}
        self.save()
        self.write("../docs/lessons/index.yaml", yaml.safe_dump({"version": 1, "lessons": [
            {"path": "one.md", "title": "Lekcja jeden", "tags": ["topic-x"]}]}))
        self.write("../docs/lessons/one.md", "tresc\n")
        result = self.run_cli("route", str(self.root), "--topic", "topic-x", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lessons = json.loads(result.stdout)["lessons"]
        self.assertEqual([item["title"] for item in lessons], ["Lekcja jeden"])
        self.assertTrue(lessons[0]["path"].endswith("docs/lessons/one.md"))
        self.assertEqual(self.root.parent / "docs/lessons/one.md", self.base / "docs/lessons/one.md")


if __name__ == "__main__":
    unittest.main()
