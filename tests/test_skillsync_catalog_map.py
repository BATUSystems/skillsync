"""Etap 3 contract: G2 description budget, G3 skill catalog/index/suggest/doctor, G4 repo map."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import common as workflow_common  # noqa: E402
from tests.test_skillsync import skillsync  # noqa: E402
from tests.test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import transitional_config  # noqa: E402

CORE = ["workflow", "pre-implement", "smart-test", "bugfix", "codereview"]


def put_skill(library, name, description=None):
    folder = Path(library) / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    description = f"opis {name}" if description is None else description
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nBody\n", encoding="utf-8")


class G1Constants(unittest.TestCase):
    def test_companions_are_identical_in_both_places(self):
        self.assertEqual(tuple(skillsync.WORKFLOW_COMPANIONS), tuple(CORE))
        self.assertEqual(tuple(workflow_common.WORKFLOW_COMPANIONS), tuple(CORE))
        self.assertEqual(skillsync.WORKFLOW_COMPANIONS, workflow_common.WORKFLOW_COMPANIONS)


class G2DescriptionBudget(WorkflowFixture):
    REASON = "Skill description longer than 200 bytes"

    def install(self, name, description):
        put_skill(self.library, name, description)
        for host in (".github/skills", ".claude/skills"):
            shutil.copytree(self.library / "skills" / name, self.root / host / name)
        self.manifest["skills"] = [name]
        self.save()

    def long_findings(self, report):
        return [f for f in report["findings"] if self.REASON in f["reason"]]

    def test_description_over_200_bytes_is_warning_only(self):
        self.install("wordy", "ą" * 101)  # 101 chars but 202 bytes
        report = self.report("doctor")  # exit 0: a warning must not fail doctor
        found = self.long_findings(report)
        self.assertEqual([f["level"] for f in found], ["warning"])
        self.assertNotEqual(report["status"], "FAIL")
        self.assertFalse(any(f["level"] == "error" for f in report["findings"]), report["findings"])

    def test_description_of_exactly_200_bytes_has_no_warning(self):
        self.install("tight", "ą" * 100)  # 200 bytes
        report = self.report("doctor")
        self.assertEqual(self.long_findings(report), [])
        self.assertEqual(report["status"], "PASS")


class CatalogFixture(WorkflowFixture):
    def catalog(self, data):
        self.write_library("workflow/skill-catalog.yaml", yaml.safe_dump({"version": 1, **data}))

    def write_library(self, name, text):
        path = self.library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def project(self, name, skills):
        for skill in skills:
            put_skill(self.library, skill)
            for host in (".github/skills", ".claude/skills"):
                target = self.root / host / skill
                if not target.exists():
                    shutil.copytree(self.library / "skills" / skill, target)
        self.manifest["project"] = {"name": name}
        self.manifest["skills"] = list(skills)
        self.save()

    def reasons(self, report, level=None):
        return [f["reason"] for f in report["findings"] if level in (None, f["level"])]

    def has(self, report, text):
        return [f for f in report["findings"] if text in f["reason"]]


class G3DoctorCatalog(CatalogFixture):
    ORGS = {"globex": {"skill": "globex-org", "projects": ["Globex/*"]},
            "acme": {"skill": "acmeskill", "projects": ["Acme/*"]}}

    def test_skill_of_another_organization_is_warning(self):
        self.catalog({"orgs": self.ORGS, "skills": {"gizmo": {"scope": "project", "org": "globex"},
                                                    "acmeskill": {"scope": "org", "org": "acme"}}})
        self.project("Acme/app", ["acmeskill", "gizmo"])
        report = self.report("doctor")
        found = self.has(report, "Skill of another organization")
        self.assertEqual([f["level"] for f in found], ["warning"])
        self.assertTrue(any("gizmo" in f["reason"] + f["path"] for f in found), found)
        self.assertEqual(report["status"], "PASS")

    def test_missing_organization_skill_is_warning(self):
        self.catalog({"orgs": self.ORGS, "skills": {"globex-org": {"scope": "org", "org": "globex"}}})
        self.project("Globex/web", ["other"])
        put_skill(self.library, "globex-org")
        report = self.report("doctor")
        found = self.has(report, "Missing organization skill")
        self.assertEqual([f["level"] for f in found], ["warning"])
        self.assertTrue(any("globex-org" in f["reason"] + f["path"] for f in found), found)
        self.assertEqual(report["status"], "PASS")

    def test_technology_not_detected_is_warning_and_cleared_by_detection(self):
        self.catalog({"orgs": self.ORGS, "skills": {"angular-21-patterns": {"scope": "tech", "tech": ["angular"]}}})
        self.project("Solo/app", ["angular-21-patterns"])
        report = self.report("doctor")
        found = self.has(report, "Technology not detected")
        self.assertEqual([f["level"] for f in found], ["warning"])
        self.assertEqual(report["status"], "PASS")
        self.write("web/angular.json", "{}")
        self.assertEqual(self.has(self.report("doctor"), "Technology not detected"), [])

    def test_project_matching_two_organizations_gets_no_org_warnings(self):
        orgs = {"globex": {"skill": "globex-org", "projects": ["Multi/*"]},
                "acme": {"skill": "acmeskill", "projects": ["Multi/*"]}}
        self.catalog({"orgs": orgs, "skills": {"globex-org": {"scope": "org", "org": "globex"},
                                               "acmeskill": {"scope": "org", "org": "acme"},
                                               "gizmo": {"scope": "project", "org": "globex"},
                                               "acme-thing": {"scope": "project", "org": "acme"}}})
        self.project("Multi/app", ["globex-org", "acmeskill", "gizmo", "acme-thing"])
        report = self.report("doctor")
        for text in ("Skill of another organization", "Missing organization skill"):
            self.assertEqual(self.has(report, text), [], text)

    def test_project_name_falls_back_to_parent_slash_directory(self):
        self.catalog({"orgs": {"globex": {"skill": "globex-org", "projects": [f"{self.base.name}/*"]}},
                      "skills": {"globex-org": {"scope": "org", "org": "globex"}}})
        put_skill(self.library, "globex-org")
        put_skill(self.library, "other")
        for host in (".github/skills", ".claude/skills"):
            shutil.copytree(self.library / "skills" / "other", self.root / host / "other")
        self.manifest.pop("project", None)
        self.manifest["skills"] = ["other"]
        self.save()
        report = self.report("doctor")
        self.assertTrue(any("globex-org" in f["reason"] + f["path"]
                            for f in self.has(report, "Missing organization skill")), report["findings"])

    def test_broken_catalog_is_warning_and_other_findings_remain(self):
        self.write_library("workflow/skill-catalog.yaml", "orgs: [unclosed\n")
        put_skill(self.library, "wordy", "ą" * 101)
        for host in (".github/skills", ".claude/skills"):
            shutil.copytree(self.library / "skills" / "wordy", self.root / host / "wordy")
        self.manifest["skills"] = ["wordy"]
        self.save()
        report = self.report("doctor")  # exit 0: not an error, not BLOCKED
        self.assertNotIn(report["status"], ("FAIL", "BLOCKED"))
        broken = [f for f in report["findings"] if "catalog" in f["reason"].lower()]
        self.assertTrue(broken and all(f["level"] == "warning" for f in broken), report["findings"])
        self.assertTrue(self.has(report, "longer than 200 bytes"), report["findings"])

    def test_without_catalog_no_catalog_warnings(self):
        self.project("Globex/web", ["angular-21-patterns", "gizmo"])
        report = self.report("doctor")
        for text in ("Skill of another organization", "Missing organization skill", "Technology not detected"):
            self.assertEqual(self.has(report, text), [], text)


class G3Detectors(CatalogFixture):
    def tech(self):
        self.manifest.setdefault("project", {"name": "Solo/app"})
        self.save()
        result = self.cli("suggest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return set(json.loads(result.stdout)["tech"])

    def setUp(self):
        super().setUp()
        self.catalog({"orgs": {}, "skills": {}})

    def test_hugo_not_detected_from_bare_config_toml(self):
        self.write(".codex/config.toml", "model='x'\n")
        self.write("config.toml", "title='x'\n")
        self.assertNotIn("hugo", self.tech())

    def test_hugo_detected_with_content_directory(self):
        self.write("site/config.toml", "title='x'\n")
        self.write("site/content/post.md", "hi\n")
        self.assertIn("hugo", self.tech())

    def test_node_modules_is_skipped(self):
        self.write("node_modules/dep/angular.json", "{}")
        self.write("node_modules/dep/package.json", "{}")
        self.assertNotIn("angular", self.tech())
        self.assertNotIn("node", self.tech())

    def test_marker_files(self):
        cases = {"angular": "web/angular.json", "django": "manage.py", "rust": "Cargo.toml",
                 "dotnet": "src/App.csproj", "capacitor": "capacitor.config.ts", "python": "pyproject.toml",
                 "node": "package.json"}
        for tech, marker in cases.items():
            with self.subTest(tech=tech):
                self.write(marker, "x\n")
                self.assertIn(tech, self.tech())
                (self.root / marker).unlink()


class G3Suggest(CatalogFixture):
    def snapshot(self):
        return sorted((str(p.relative_to(self.base)), p.stat().st_mtime_ns) for p in self.base.rglob("*") if p.is_file())

    def test_suggest_json_shape_and_no_writes(self):
        self.catalog({"orgs": {"globex": {"skill": "globex-org", "projects": ["Globex/*"]}},
                      "skills": {"globex-org": {"scope": "org", "org": "globex"},
                                 "angular-21-patterns": {"scope": "tech", "tech": ["angular"]},
                                 "gizmo": {"scope": "project", "org": "other"}}})
        self.project("Globex/web", ["gizmo"])
        put_skill(self.library, "angular-21-patterns")
        self.write("angular.json", "{}")
        before = self.snapshot()
        result = self.cli("suggest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(set(data), {"project", "orgs", "tech", "add", "remove", "reasons"})
        self.assertIn("globex", data["orgs"])
        self.assertIn("angular", data["tech"])
        self.assertIn("globex-org", data["add"])
        self.assertIn("angular-21-patterns", data["add"])
        self.assertIn("gizmo", data["remove"])
        self.assertEqual(self.snapshot(), before)


class G3MissingCatalog(CatalogFixture):
    def test_suggest_without_catalog_fails_with_message(self):
        self.project("Solo/app", [])
        result = self.cli("suggest")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue((result.stdout + result.stderr).strip())


class G3Index(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skillsync-index-")
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        (self.library / "workflow").mkdir(parents=True)
        config = Path(self.temp.name) / "config.yaml"
        config.write_text(yaml.safe_dump(transitional_config(self.library, legacy_key=False, variables={}, tags={})), encoding="utf-8")
        # The index goes to the per-user cache, never into a layer (2.2a): isolate the cache from the real one.
        cache = Path(self.temp.name) / "cache-home"
        self.env = {**os.environ, "SKILLSYNC_CONFIG": str(config), "HOME": str(cache), "USERPROFILE": str(cache),
                    "XDG_CACHE_HOME": str(cache / "xdg"), "LOCALAPPDATA": str(cache / "local")}
        put_skill(self.library, "gizmo", "Opis projektu gizmo")
        put_skill(self.library, "angular-21-patterns", "Wzorce Angulara")
        put_skill(self.library, "mystery", "Skill bez wpisu")
        (self.library / "workflow" / "skill-catalog.yaml").write_text(yaml.safe_dump({
            "version": 1, "orgs": {"globex": {"skill": "gizmo", "projects": ["Globex/*"]}},
            "skills": {"gizmo": {"scope": "project", "org": "globex"},
                       "angular-21-patterns": {"scope": "tech", "tech": ["angular"]}}}), encoding="utf-8")

    def run_index(self, *args):
        return subprocess.run([sys.executable, str(CLI), "skills", "index", *args], capture_output=True, text=True,
                              timeout=20, env=self.env)

    def test_index_groups_by_scope_and_lists_uncatalogued(self):
        result = self.run_index()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        target = Path(result.stdout.strip().removeprefix("Wrote "))
        self.assertTrue(str(target).startswith(self.env["HOME"]), target)  # cache, not the library
        self.assertFalse((self.library / "skills" / "INDEX.md").exists())
        text = target.read_text(encoding="utf-8")
        for name, desc in (("gizmo", "Opis projektu gizmo"), ("angular-21-patterns", "Wzorce Angulara"),
                           ("mystery", "Skill bez wpisu")):
            self.assertRegex(text, rf"(?m)^.*{re.escape(name)}.*{re.escape(desc)}")
        self.assertIn("Nieskatalogowane", text)
        tail = text.split("Nieskatalogowane", 1)
        self.assertNotIn("mystery", tail[0])
        self.assertIn("mystery", tail[1])
        self.assertNotIn("gizmo", tail[1])
        self.assertNotIn("angular-21-patterns", tail[1])
        self.assertIn("project", text.lower())
        self.assertIn("tech", text.lower())

    def test_stdout_prints_the_index_and_writes_nothing(self):
        result = self.run_index("--stdout")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mystery", result.stdout)
        self.assertFalse((self.library / "skills" / "INDEX.md").exists())
        self.assertFalse((Path(self.env["XDG_CACHE_HOME"]) / "skillsync" / "skills-index").exists())
        self.assertEqual(self.run_index("--check").returncode, 1)  # nothing was written to the cache either

    def test_check_detects_stale_index_and_passes_after_regeneration(self):
        self.assertEqual(self.run_index().returncode, 0)
        self.assertEqual(self.run_index("--check").returncode, 0)
        put_skill(self.library, "mystery", "Zmieniony opis")
        self.assertEqual(self.run_index("--check").returncode, 1)
        self.assertEqual(self.run_index().returncode, 0)
        self.assertEqual(self.run_index("--check").returncode, 0)

    def test_index_without_catalog_fails_with_message(self):
        (self.library / "workflow" / "skill-catalog.yaml").unlink()
        result = self.run_index()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue((result.stdout + result.stderr).strip())

    def test_invalid_scope_gives_readable_error(self):
        (self.library / "workflow" / "skill-catalog.yaml").write_text(yaml.safe_dump({
            "version": 1, "orgs": {}, "skills": {"gizmo": {"scope": "bogus"}}}), encoding="utf-8")
        result = self.run_index()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("scope", output.lower())
        self.assertIn("gizmo", output)


class G4Map(WorkflowFixture):
    def run_map(self, *args):
        return subprocess.run([sys.executable, str(CLI), "map", str(self.root), *args], capture_output=True, text=True,
                              timeout=60, cwd=self.root)

    @property
    def map_file(self):
        return self.root / ".skillsync" / "map.md"

    def setUp(self):
        super().setUp()
        self.write("mod/README.md", "\nObsługuje rozliczenia modułu płatności.\n\nWięcej.\n")
        self.write("mod/code.py", "x = 1\n")
        self.write("tests/test_a.py", "pass\n")
        self.write("db/migrations/0001.sql", "select 1;\n")
        self.write("app/package.json", json.dumps({"scripts": {"build": "tsc"}}))
        self.init_git()

    def test_map_created_with_fingerprint_header_and_sections(self):
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = self.map_file.read_text(encoding="utf-8")
        self.assertRegex(text.split("\n\n", 1)[0] if "\n\n" in text else text, r"(?is)fingerprint\W+[0-9a-f]{64}")
        self.assertIn("Obsługuje rozliczenia modułu płatności.", text)
        self.assertIn("mod", text)
        self.assertIn("package.json", text)
        self.assertIn("migrations", text)

    def test_check_fresh_stale_and_missing(self):
        self.assertEqual(self.run_map("--check").returncode, 1)  # no map yet
        self.assertEqual(self.run_map().returncode, 0)
        self.assertEqual(self.run_map("--check").returncode, 0)
        self.write("mod/new.py", "y = 2\n")
        self.git("add", "mod/new.py")
        self.assertEqual(self.run_map("--check").returncode, 1)
        self.assertEqual(self.run_map().returncode, 0)
        self.assertEqual(self.run_map("--check").returncode, 0)

    def test_large_tree_is_truncated_to_12_kb_with_annotation(self):
        for i in range(30):
            for j in range(8):
                for k in range(4):
                    self.write(f"pkg_with_a_long_name_{i:03d}/sub_module_{j}/leaf_directory_{k}/f.txt", "x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "big")
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        raw = self.map_file.read_bytes()
        self.assertLessEqual(len(raw), 12 * 1024)
        self.assertRegex(raw.decode("utf-8"), r"(?i)obci|trunc|ucięt")
        self.assertEqual(self.run_map("--check").returncode, 0)


class G4MapOrderingAndNoGit(WorkflowFixture):
    def run_map(self, *args):
        return subprocess.run([sys.executable, str(CLI), "map", str(self.root), *args], capture_output=True, text=True,
                              timeout=60, cwd=self.root)

    def test_nested_directory_listed_under_parent_before_dotted_sibling(self):
        self.write("Foo/a.txt", "x\n")
        self.write("Foo.Tests/a.txt", "x\n")
        self.write("Foo/Sub/a.txt", "x\n")
        self.init_git()
        self.assertEqual(self.run_map().returncode, 0)
        raw = (self.root / ".skillsync" / "map.md").read_text(encoding="utf-8").splitlines()
        start = next((i for i, line in enumerate(raw) if line.startswith("## Drzewo")), None)
        self.assertIsNotNone(start, "no '## Drzewo' section in map")
        end = next((i for i in range(start + 1, len(raw)) if raw[i].startswith("## ")), len(raw))
        lines = [line.strip().lstrip("-*+ `|\\_").rstrip("`/") for line in raw[start + 1:end]]
        def position(pattern):
            for number, line in enumerate(lines):
                if re.match(pattern, line):
                    return number
            self.fail(f"{pattern} not in map: {lines}")
        foo, sub, tests = position(r"^(Foo/)?Foo\b(?!\.)"), position(r"^(Foo/)?Sub\b"), position(r"^Foo\.Tests\b")
        self.assertLess(foo, sub)
        self.assertLess(sub, tests)

    def test_map_outside_git_repository_uses_file_list(self):
        self.write("mod/code.py", "x = 1\n")
        self.write("mod/README.md", "Opis modułu poza gitem.\n")
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        text = (self.root / ".skillsync" / "map.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?is)fingerprint\W+[0-9a-f]{64}")
        self.assertIn("Opis modułu poza gitem.", text)
        self.assertEqual(self.run_map("--check").returncode, 0)


class RoutedInstructionTests(CatalogFixture):
    MISSING = "Missing routed instruction"
    ABSENT = ".github/skills/task-claim/SKILL.md"

    def route_rule(self, *instructions):
        self.manifest["workflow"]["routing"] = {"rules": [{"topics": ["t"], "instructions": list(instructions)}]}
        self.save()

    def test_instruction_of_skill_not_in_project_is_skipped(self):
        self.route_rule(self.ABSENT)
        report = self.report("doctor")  # exit 0: no error
        self.assertEqual(self.has(report, self.MISSING), [])
        result = self.cli("route", "--topic", "t")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("task-claim", json.dumps([r.get("instructions") for r in json.loads(result.stdout)["routes"]]))

    def test_installed_skill_with_missing_file_is_still_an_error(self):
        self.project("Solo/app", ["other"])
        (self.root / ".github/skills/other/SKILL.md").unlink()
        self.route_rule(".github/skills/other/SKILL.md")
        report = self.report("doctor", code=1)
        self.assertTrue(self.has(report, self.MISSING), report["findings"])
        self.assertTrue(all(f["level"] == "error" for f in self.has(report, self.MISSING)))

    def test_missing_non_skill_instruction_is_still_an_error(self):
        self.route_rule("docs/missing-instruction.md")
        report = self.report("doctor", code=1)
        self.assertEqual([f["level"] for f in self.has(report, self.MISSING)], ["error"])


if __name__ == "__main__":
    unittest.main()
