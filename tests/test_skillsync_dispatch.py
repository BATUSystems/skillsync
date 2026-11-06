"""feature rename (alias `agentflow`), dispatch auto-companion, cleanup, `.skillsync/` ignore, `dispatch:` manifest section."""
import copy
import sys
import unittest
import unittest.mock
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import LayersCase, yaml_text  # noqa: E402
from skillsync_workflow import common as workflow_common, layers as workflow_layers  # noqa: E402
from skillsync_workflow.dispatch import validate_dispatch  # noqa: E402
from tests.test_skillsync import TempProjectMixin, skillsync  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402
from layers_helpers import link_mode, needs_os_symlinks  # noqa: E402

COMPANIONS = ["workflow", "pre-implement", "smart-test", "bugfix", "codereview"]
EXTENDED = ["workflow-retro", "task-claim", "workflow-upgrade", "skill-profiles", "project-guard", "dispatch",
            "ci-stabilize", "tracker-workflow"]


def write_skill(library, name, adapters=False):
    folder = Path(library) / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test {name}\n---\nBody\n", encoding="utf-8")
    if adapters:
        (folder / "references").mkdir(exist_ok=True)
        for host in ("claude", "codex"):
            (folder / "references" / f"{host}.md").write_text("adapter\n", encoding="utf-8")


class SharedConstantsTests(unittest.TestCase):
    def test_entry_script_and_package_agree_on_workflow_names(self):
        self.assertEqual(skillsync.WORKFLOW_SKILL, workflow_common.WORKFLOW_SKILL)
        self.assertEqual(skillsync.LEGACY_WORKFLOW_SKILL, workflow_common.LEGACY_WORKFLOW_SKILL)
        self.assertEqual(skillsync.WORKFLOW_COMPANIONS, workflow_common.WORKFLOW_COMPANIONS)
        self.assertEqual(list(skillsync.WORKFLOW_COMPANIONS), COMPANIONS)
        self.assertEqual(skillsync.WORKFLOW_SKILL, "feature")


class ExpandSkillsTests(TempProjectMixin, unittest.TestCase):
    def library_with(self, *names):
        for name in names:
            write_skill(self.library_dir, name)

    def expand(self, specs, tags=None):
        return skillsync.expand_skills(specs, self.library_dir / "skills", tags or {})

    def one_layer(self):
        """The library as the one-layer `LayerSet` the script builds from its `Path` form (2.3: the package
        expander accepts only a `LayerSet`, K1)."""
        return workflow_common.as_layerset(self.library_dir)

    def test_agentflow_alias_expands_to_feature_not_agentflow(self):
        self.library_with("feature", *COMPANIONS)
        expanded = self.expand(["agentflow"])
        self.assertEqual(expanded[0], "feature")
        self.assertNotIn("agentflow", expanded)

    def test_alias_through_tag_and_glob_is_deduplicated(self):
        self.library_with("feature", "dispatch", "other")
        self.assertEqual(self.expand(["@wf"], {"wf": ["agentflow"]})[0], "feature")
        expanded = self.expand(["agentflow", "agent*", "feature"])
        self.assertEqual(expanded.count("feature"), 1)

    def test_alias_applies_even_if_library_still_has_both_directories(self):
        self.library_with("feature", "agentflow")
        self.assertEqual(self.expand(["agentflow"]), ["feature"])

    def test_package_expander_matches_script_for_alias(self):
        self.library_with("feature", *COMPANIONS)
        manifest = {"skills": ["agentflow"]}
        self.assertEqual(workflow_common.expand_project_skills(manifest, self.one_layer()),
                         skillsync.expand_skills(["agentflow"], self.library_dir / "skills", {}))

    def test_transitional_fallback_keeps_legacy_library_working(self):
        self.library_with("agentflow", "workflow", "dispatch")
        expanded = self.expand(["agentflow"])
        self.assertEqual(expanded, ["agentflow", "workflow"])
        self.assertTrue(skillsync.has_feature({"skills": ["agentflow"]}, self.config))
        self.assertEqual(workflow_common.expand_project_skills({"skills": ["agentflow"]}, self.one_layer()), expanded)

    def test_feature_pulls_in_only_the_core_companions_not_dispatch(self):
        self.library_with("feature", *COMPANIONS, *EXTENDED)
        expanded = self.expand(["feature"])
        self.assertEqual(expanded, ["feature"] + COMPANIONS)
        self.assertNotIn("dispatch", expanded)
        self.assertNotIn("task-claim", expanded)
        self.assertEqual(workflow_common.expand_project_skills({"skills": ["feature"]}, self.one_layer()), expanded)

    def test_dispatch_is_available_explicitly(self):
        self.library_with("feature", *COMPANIONS, *EXTENDED)
        expanded = self.expand(["feature", "dispatch"])
        self.assertEqual(expanded, ["feature", "dispatch"] + COMPANIONS)
        self.assertEqual(workflow_common.expand_project_skills({"skills": ["feature", "dispatch"]}, self.one_layer()),
                         expanded)

    def test_workflow_extended_profile_adds_dispatch_and_task_claim(self):
        self.library_with("feature", *COMPANIONS, *EXTENDED)
        (self.library_dir / "workflow").mkdir(exist_ok=True)
        (self.library_dir / "workflow" / "skill-profiles.yaml").write_text(
            yaml.safe_dump({"version": 1, "profiles": {"workflow-extended": EXTENDED}}), encoding="utf-8")
        manifest = {"skills": ["feature"], "workflow": {"skill_profiles": {"selected": ["workflow-extended"]}}}
        expanded = skillsync.expand_skills(["feature"], self.library_dir / "skills", {}, manifest)
        self.assertIn("dispatch", expanded)
        self.assertIn("task-claim", expanded)
        self.assertEqual(expanded[0], "feature")
        self.assertEqual(workflow_common.expand_project_skills(manifest, self.one_layer()), expanded)

    def test_dispatch_is_not_added_without_feature_or_when_missing_in_library(self):
        self.library_with("feature", "workflow", "other", "dispatch")
        self.assertNotIn("dispatch", self.expand(["other"]))
        (self.library_dir / "skills" / "dispatch" / "SKILL.md").unlink()
        (self.library_dir / "skills" / "dispatch").rmdir()
        self.assertEqual(self.expand(["feature"]), ["feature", "workflow"])


class ExpandSkillsOnLayersTests(LayersCase):
    """Parity of the two expansion implementations on a `LayerSet`; the `Path` form stays only in the script (2.3)."""

    SPECS = ["agentflow", "@grp", "glob-*"]

    def skill_files(self, *names):
        return {f"skills/{name}/SKILL.md": f"---\nname: {name}\ndescription: test {name}\n---\nBody\n" for name in names}

    def setUp(self):
        super().setUp()
        core = self.layer_dir("core", "core", {
            **self.skill_files("feature", "workflow", "pre-implement", "smart-test", "bugfix", "dispatch", "other"),
            "workflow/skill-profiles.yaml": yaml_text({"version": 1, "profiles": {"extended": ["dispatch"]}})})
        org = self.layer_dir("org", "org", {
            **self.skill_files("org-skill", "glob-a", "codereview"),  # a companion supplied by a non-core layer
            "workflow/skill-profiles.d/org.yaml": yaml_text({"profiles": {"extended": ["org-skill"], "org-only": ["glob-a"]}})})
        personal = self.layer_dir("personal", "personal", {
            **self.skill_files("mine", "glob-b"),
            "workflow/skill-profiles.d/me.yaml": yaml_text({"profiles": {"extended": ["mine", "dispatch"]}})})
        self.write_config(layers=[("core", "core", core), ("org", "org", org), ("personal", "personal", personal)],
                          rules=[(["**"], ["core", "org", "personal"])], tags={"grp": ["org-skill", "agentflow"]})
        self.layerset = workflow_layers.resolve_layers(None, config=workflow_layers.load_machine_config(), cwd=self.work)
        self.manifest = {"skills": list(self.SPECS), "workflow": {"skill_profiles": {"selected": ["extended"]}}}

    def both(self, manifest):
        tags = {"grp": ["org-skill", "agentflow"]}
        script = skillsync.expand_skills(manifest.get("skills", []), self.layerset, tags, manifest)
        package = workflow_common.expand_project_skills(manifest, self.layerset)
        return script, package

    def test_both_implementations_give_the_same_list_across_layers(self):
        script, package = self.both(self.manifest)
        self.assertEqual(script, package)
        self.assertEqual(script, ["feature", "org-skill", "glob-a", "glob-b", "dispatch", "mine",
                                  "workflow", "pre-implement", "smart-test", "bugfix", "codereview"])

    def test_profile_lists_are_summed_over_layers_with_deduplication(self):
        manifest = {"skills": ["other"], "workflow": {"skill_profiles": {"selected": ["extended", "org-only"]}}}
        script, package = self.both(manifest)
        self.assertEqual(script, package)
        self.assertEqual(script, ["other", "dispatch", "org-skill", "mine", "glob-a"])

    def test_alias_tag_wildcard_and_companions_without_profiles(self):
        manifest = {"skills": ["agentflow", "glob-?"]}
        script, package = self.both(manifest)
        self.assertEqual(script, package)
        self.assertEqual(script[:3], ["feature", "glob-a", "glob-b"])
        self.assertNotIn("agentflow", script)
        self.assertNotIn("dispatch", script)

    def test_a_skill_that_exists_in_no_layer_is_not_a_wildcard_match_and_a_profile_naming_it_fails_in_both(self):
        self.assertNotIn("ghost", self.both({"skills": ["*"]})[0])
        org = self.layerset.get("org").root
        (org / "workflow" / "skill-profiles.d" / "org.yaml").write_text(
            yaml_text({"profiles": {"extended": ["ghost"]}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            skillsync.expand_skills(["feature"], self.layerset, {}, self.manifest)
        with self.assertRaises(ValueError):
            workflow_common.expand_project_skills(self.manifest, self.layerset)

    def test_unsafe_names_are_rejected_with_the_same_message_in_both_implementations(self):
        for name in ("../..", "..", "/etc/passwd", "a/../../b", "../x"):
            for via in ("name", "tag"):
                with self.subTest(name=name, via=via):
                    spec, tags = (name, {}) if via == "name" else ("@bad", {"bad": [name]})
                    with self.assertRaises(ValueError) as script:
                        skillsync.expand_skills([spec], self.layerset, tags)
                    with unittest.mock.patch.object(workflow_common, "read_yaml", return_value={"tags": tags}):
                        with self.assertRaises(ValueError) as package:
                            workflow_common.expand_project_skills({"skills": [spec]}, self.layerset)
                    self.assertEqual(str(script.exception), str(package.exception))
                    self.assertIn("Unsafe skill name", str(script.exception))
        for walk in (self.layerset.skill_files, lambda n: workflow_layers.layer_files(self.layerset.get("core"), f"skills/{n}")):
            with self.assertRaises(workflow_layers.LayerError):
                walk("../..")

    def test_the_script_path_form_equals_the_one_layer_layerset_and_the_package_refuses_a_path(self):
        library = self.work / "layers" / "core"
        manifest = {"skills": ["agentflow", "glob-*"], "workflow": {"skill_profiles": {"selected": ["extended"]}}}
        single = workflow_layers.resolve_layers(None, layers=[f"core={library}"],
                                                config=workflow_layers.load_machine_config(), cwd=self.work)
        expected = skillsync.expand_skills(manifest["skills"], single, {}, manifest)
        self.assertEqual(skillsync.expand_skills(manifest["skills"], library / "skills", {}, manifest), expected)
        with self.assertRaises(TypeError):  # 2.3: workflow modules take only a resolved LayerSet (K1)
            workflow_common.expand_project_skills(manifest, library)
        self.assertEqual(workflow_common.expand_project_skills(manifest, single), expected)
        self.assertEqual(expected, ["feature", "dispatch", "workflow", "pre-implement", "smart-test", "bugfix"])


class LinkAfterRenameTests(TempProjectMixin, unittest.TestCase):
    SKILLS = ("feature", "workflow")

    def setUp(self):
        super().setUp()
        self.manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        self.manifest_path.parent.mkdir(parents=True)
        self.manifest_path.write_text("skills: [agentflow]\n", encoding="utf-8")
        skillsync.save_yaml(self.library_dir / "codex.yaml", {"max_concurrent_threads_per_session": 3, "agents": {}})
        (self.library_dir / "instructions" / "_base.md").write_text("Base\n", encoding="utf-8")
        self.config = {**self.config, "discover_paths": []}
        skillsync._symlink_warned = False

    def link(self, method="symlink"):
        skillsync.save_yaml(self.manifest_path, {"skills": ["agentflow"], "sync": {"method": method}})
        skillsync.link_project(self.manifest_path, self.config)

    def installed(self):
        return {host: self.project_dir / host / "skills" for host in (".github", ".claude", ".agents")}

    def migrate_library(self):
        """Library state after the rename: skills/agentflow -> skills/feature, dispatch added."""
        (self.library_dir / "skills" / "agentflow").rename(self.library_dir / "skills" / "feature")
        write_skill(self.library_dir, "dispatch")

    def start_with_legacy_library(self):
        write_skill(self.library_dir, "agentflow")
        write_skill(self.library_dir, "workflow")

    def assert_migrated(self):
        for host, directory in self.installed().items():
            with self.subTest(host=host):
                for name in self.SKILLS:
                    self.assertTrue((directory / name).exists(), f"{host}/{name}")
                self.assertFalse((directory / "agentflow").exists() or (directory / "agentflow").is_symlink(), host)

    @needs_os_symlinks
    @link_mode('symlink')  # asserts links: pinned, so SKILLSYNC_NO_SYMLINK=1 in the run does not change it
    def test_link_removes_orphaned_agentflow_links_and_installs_feature(self):
        self.start_with_legacy_library()
        self.link()
        for directory in self.installed().values():
            self.assertTrue((directory / "agentflow").is_symlink())
        self.migrate_library()
        self.link()
        self.assert_migrated()

    def test_link_removes_orphaned_agentflow_copies_in_copy_mode(self):
        self.start_with_legacy_library()
        self.link("copy")
        for directory in self.installed().values():
            self.assertTrue((directory / "agentflow" / "SKILL.md").is_file())
        self.migrate_library()
        self.link("copy")
        self.assert_migrated()

    @needs_os_symlinks
    def test_unmanaged_agentflow_directory_is_never_touched(self):
        self.start_with_legacy_library()
        self.migrate_library()
        manual = self.project_dir / ".claude" / "skills" / "agentflow"
        manual.mkdir(parents=True)
        (manual / "SKILL.md").write_text("hand written", encoding="utf-8")
        mine = self.project_dir / ".agents" / "skills" / "my-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine", encoding="utf-8")
        self.link()
        self.assertEqual((manual / "SKILL.md").read_text(encoding="utf-8"), "hand written")
        self.assertTrue((mine / "SKILL.md").exists())
        self.assertTrue((self.project_dir / ".claude" / "skills" / "feature").exists())

    def test_link_with_legacy_only_library_installs_agentflow_as_fallback(self):
        self.start_with_legacy_library()
        self.link("copy")
        self.assertTrue((self.installed()[".claude"] / "agentflow" / "SKILL.md").is_file())
        self.assertFalse((self.installed()[".claude"] / "feature").exists())


class SkillsyncIgnoreTests(TempProjectMixin, unittest.TestCase):
    """Dispatch state lives in `.skillsync/dispatch/`; link ignores the whole `.skillsync/` directory."""

    def setUp(self):
        super().setUp()
        self.manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        self.manifest_path.parent.mkdir(parents=True)
        self.config = {**self.config, "discover_paths": []}
        skillsync.save_yaml(self.library_dir / "codex.yaml", {"max_concurrent_threads_per_session": 3, "agents": {}})
        for name in ("feature", "dispatch", "other"):
            write_skill(self.library_dir, name)

    def link(self, skills):
        skillsync.save_yaml(self.manifest_path, {"skills": skills, "sync": {"method": "copy"}})
        skillsync.link_project(self.manifest_path, self.config)

    def test_feature_project_ignores_skillsync_directory(self):
        self.link(["agentflow"])
        lines = (self.project_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".skillsync/", lines)
        self.link(["feature"])
        self.assertEqual((self.project_dir / ".gitignore").read_text(encoding="utf-8").splitlines().count(".skillsync/"), 1)

    def test_project_without_feature_does_not_get_the_ignore_entry(self):
        self.link(["other"])
        gitignore = self.project_dir / ".gitignore"
        self.assertNotIn(".skillsync/", gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else [])


class ValidateDispatchTests(unittest.TestCase):
    FULL = {
        "max_parallel": 3,
        "worktree_root": "../{project}-wt",
        "setup": ["skillsync link {worktree}", "cd frontend/x && npm ci"],
        "isolated_resources": {"test_db": "db_{package}", "env": {"CONNSTR": "Host=localhost;Database={test_db}"}},
        "leased_resources": [{"name": "e2e", "ports": [5500, 4250], "databases": ["e2e_db"], "command": "scripts/e2e.sh"}],
        "owned_files": ["docs/prompts/README.md"],
        "additive_files": ["docs/lessons/index.yaml"],
        "id_registries": ["SEC", "ADR"],
        "guards": {"min_free_disk_gb": 6, "max_load": 12.5},
    }

    def broken(self, mutate):
        section = copy.deepcopy(self.FULL)
        mutate(section)
        errors, warnings = validate_dispatch(section)
        return errors, warnings

    def test_missing_null_and_complete_sections_are_valid(self):
        self.assertEqual(validate_dispatch(None), ([], []))
        self.assertEqual(validate_dispatch({}), ([], []))
        self.assertEqual(validate_dispatch(self.FULL), ([], []))

    def test_section_must_be_mapping(self):
        self.assertEqual(validate_dispatch(["x"])[0], ["dispatch: must be a mapping"])

    def test_type_errors_name_the_key_path(self):
        cases = {
            "dispatch.max_parallel": lambda s: s.update(max_parallel=0),
            "dispatch.max_parallel ": lambda s: s.update(max_parallel=True),
            "dispatch.worktree_root": lambda s: s.update(worktree_root=["x"]),
            "dispatch.setup[1]": lambda s: s.update(setup=["ok", 5]),
            "dispatch.owned_files": lambda s: s.update(owned_files="docs/x"),
            "dispatch.additive_files[0]": lambda s: s.update(additive_files=[None]),
            "dispatch.id_registries": lambda s: s.update(id_registries={"SEC": 1}),
            "dispatch.isolated_resources": lambda s: s.update(isolated_resources=["x"]),
            "dispatch.isolated_resources.env": lambda s: s["isolated_resources"].update(env=["A=B"]),
            "dispatch.isolated_resources.env.CONNSTR": lambda s: s["isolated_resources"]["env"].update(CONNSTR=5),
            "dispatch.isolated_resources.test_db": lambda s: s["isolated_resources"].update(test_db={"a": 1}),
            "dispatch.leased_resources": lambda s: s.update(leased_resources={"name": "e2e"}),
            "dispatch.leased_resources[0]": lambda s: s.update(leased_resources=["e2e"]),
            "dispatch.leased_resources[0].name": lambda s: s["leased_resources"][0].pop("name"),
            "dispatch.leased_resources[0].ports": lambda s: s["leased_resources"][0].update(ports=5500),
            "dispatch.leased_resources[0].ports[1]": lambda s: s["leased_resources"][0].update(ports=[5500, "x"]),
            "dispatch.leased_resources[0].databases": lambda s: s["leased_resources"][0].update(databases="db"),
            "dispatch.leased_resources[0].command": lambda s: s["leased_resources"][0].update(command=["a"]),
            "dispatch.guards": lambda s: s.update(guards=5),
            "dispatch.guards.min_free_disk_gb": lambda s: s["guards"].update(min_free_disk_gb=-1),
            "dispatch.guards.max_load": lambda s: s["guards"].update(max_load="high"),
        }
        for path, mutate in cases.items():
            with self.subTest(path=path):
                errors, warnings = self.broken(mutate)
                self.assertTrue(any(error.startswith(path.strip() + ":") or error.startswith(path.strip() + "[")
                                    for error in errors), errors)
                self.assertEqual(warnings, [])

    def test_duplicate_lease_names_and_bad_port_range_are_errors(self):
        errors, _ = self.broken(lambda s: s["leased_resources"].append({"name": "e2e"}))
        self.assertTrue(any("duplicate resource name" in e for e in errors), errors)
        errors, _ = self.broken(lambda s: s["leased_resources"][0].update(ports=[70000]))
        self.assertTrue(any("ports[0]" in e for e in errors), errors)

    def test_unknown_keys_are_warnings_not_errors(self):
        errors, warnings = self.broken(lambda s: (s.update(extra=1), s["guards"].update(max_ram=1),
                                                  s["leased_resources"][0].update(color="red")))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(w.split(":")[0] for w in warnings),
                         ["dispatch.extra", "dispatch.guards.max_ram", "dispatch.leased_resources[0].color"])


class DoctorDispatchTests(WorkflowFixture):
    def dispatch_findings(self, report):
        return [f for f in report["findings"] if f["reason"].startswith("dispatch")]

    def test_manifest_without_dispatch_section_is_fine(self):
        report = self.report("doctor")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(self.dispatch_findings(report), [])

    def test_valid_dispatch_section_passes_without_findings(self):
        self.manifest["dispatch"] = copy.deepcopy(ValidateDispatchTests.FULL)
        self.save()
        report = self.report("doctor")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(self.dispatch_findings(report), [])

    def test_invalid_dispatch_section_is_an_error_with_key_path(self):
        self.manifest["dispatch"] = {"max_parallel": "three", "leased_resources": [{"ports": [1]}]}
        self.save()
        report = self.report("doctor", code=1)
        self.assertEqual(report["status"], "FAIL")
        errors = {f["reason"].split(":")[0] for f in report["findings"] if f["level"] == "error"}
        self.assertEqual(errors, {"dispatch.max_parallel", "dispatch.leased_resources[0].name"})

    def test_unknown_dispatch_key_is_only_a_warning(self):
        self.manifest["dispatch"] = {"max_parallel": 2, "colour": "red"}
        self.save()
        report = self.report("doctor")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual([(f["level"], f["reason"].split(":")[0]) for f in self.dispatch_findings(report)],
                         [("warning", "dispatch.colour")])


class DoctorAgentflowNameTests(WorkflowFixture):
    def deprecation(self, report):
        return [f for f in report["findings"] if "Deprecated skill name" in f["reason"]]

    def test_agentflow_in_manifest_warns_with_replacement_hint_and_resolves_to_feature(self):
        write_skill(self.library, "feature", adapters=True)
        self.manifest["skills"] = ["agentflow"]
        self.save()
        report = self.report("doctor", code=1)  # not linked yet: Missing installed skill
        warning = self.deprecation(report)
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0]["level"], "warning")
        self.assertIn("feature", warning[0]["reason"])
        missing = [f["path"] for f in report["findings"] if f["reason"] == "Missing installed skill"]
        self.assertTrue(missing and all(Path(p).parent.name == "skills" and Path(p).name == "feature" for p in missing), missing)
        self.assertFalse(any(f["reason"] == "Missing skill" for f in report["findings"]))

    def test_feature_in_manifest_has_no_deprecation_warning(self):
        write_skill(self.library, "feature", adapters=True)
        self.manifest["skills"] = ["feature"]
        self.save()
        self.assertEqual(self.deprecation(self.report("doctor", code=1)), [])

    def test_agentflow_with_legacy_only_library_still_validates_the_skill(self):
        write_skill(self.library, "agentflow", adapters=True)
        self.manifest["skills"] = ["agentflow"]
        self.save()
        report = self.report("doctor", code=1)
        self.assertEqual(len(self.deprecation(report)), 1)
        self.assertFalse(any(f["reason"] == "Missing skill" for f in report["findings"]))
        self.assertTrue(any(f["reason"] == "Missing installed skill" and f["path"].endswith("skills/agentflow")
                            for f in report["findings"]))

    def test_dispatch_adapters_are_checked_like_feature_adapters(self):
        write_skill(self.library, "feature", adapters=True)
        write_skill(self.library, "dispatch")  # no references/
        self.manifest["skills"] = ["feature", "dispatch"]
        self.save()
        report = self.report("doctor", code=1)
        self.assertTrue(any(f["reason"] == "Missing enabled dispatch adapter" for f in report["findings"]), report["findings"])
        self.assertFalse(any(f["reason"] == "Missing enabled feature adapter" for f in report["findings"]))


if __name__ == "__main__":
    unittest.main()
