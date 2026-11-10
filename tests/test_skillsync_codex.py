"""Regression tests for native Codex generation and Claude Stop hook migration."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skillsync import SKILLSYNC_PATH, TempProjectMixin, skillsync
from tests.layers_helpers import needs_os_symlinks  # noqa: E402


class CodexTests(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manifest = {"skills": ["feature", "copilot-orchestrator"], "agents": ["explore", "reviewer"]}
        for name in self.manifest["skills"]:
            folder = self.library_dir / "skills" / name
            folder.mkdir()
            (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\nBody\n")
        for name in self.manifest["agents"]:
            (self.library_dir / "agents" / (name + ".md")).write_text(
                f'---\nname: {name}\ndescription: Read the code\nmodel: haiku\n---\nPolish: ąę. Quotes " and slash \\\n')
        (self.library_dir / "instructions" / "_base.md").write_text("Base {{ project }}\n")
        self.config["variables"] = {"project": "example"}
        skillsync.save_yaml(self.library_dir / "codex.yaml", {
            "max_concurrent_threads_per_session": 3,
            "exclude_skills": ["copilot-orchestrator"],
            "agents": {
                "explore": {"name": "explorer", "model": "gpt-6-luna", "sandbox_mode": "read-only"},
                "reviewer": {"name": "reviewer", "model": "gpt-6.1-sol", "model_reasoning_effort": "high", "sandbox_mode": "read-only"},
            },
        })

    def build(self, **overrides):
        skillsync.build_codex(self.project_dir, {**self.manifest, **overrides}, self.config)

    def config_path(self):
        return self.project_dir / ".codex" / "config.toml"

    def role(self, name="explorer"):
        return self.project_dir / ".codex" / "agents" / (name + ".toml")

    def test_shared_roles_compile_to_native_toml_and_skill_is_discoverable(self):
        self.build()
        data = tomllib.loads(self.role().read_text())
        self.assertEqual(data["model"], "gpt-6-luna")
        self.assertEqual(data["name"], "explorer")
        self.assertIn('ąę. Quotes " and slash \\', data["developer_instructions"])
        self.assertNotIn("tools", data)
        self.assertTrue((self.project_dir / ".agents/skills/feature/SKILL.md").is_file())
        self.assertFalse((self.project_dir / ".agents/skills/copilot-orchestrator").exists())
        self.assertEqual(tomllib.loads(self.config_path().read_text())["agents"], {"enabled": True, "max_concurrent_threads_per_session": 3})
        self.assertIn("Base example", (self.project_dir / ".codex/skillsync-instructions.md").read_text())
        self.assertIn("$feature", (self.project_dir / "AGENTS.md").read_text())

    def test_idempotence_includes_mtimes(self):
        self.build()
        files = [p for p in self.project_dir.rglob("*") if p.is_file() and not p.is_symlink()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        self.build()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_shared_role_update_propagates(self):
        self.build()
        source = self.library_dir / "agents/explore.md"
        source.write_text(source.read_text() + "New instruction\n")
        self.build()
        self.assertIn("New instruction", tomllib.loads(self.role().read_text())["developer_instructions"])

    def test_local_role_edits_survive_source_update(self):
        self.build()
        self.role().write_text("# custom role\n")
        source = self.library_dir / "agents/explore.md"
        source.write_text(source.read_text() + "new\n")
        self.build()
        self.assertEqual(self.role().read_text(), "# custom role\n")

    def test_manual_role_and_instructions_are_preserved(self):
        self.role().parent.mkdir(parents=True)
        self.role().write_text("custom\n")
        path = self.project_dir / ".codex/skillsync-instructions.md"
        path.write_text("Local instructions\n")
        self.build()
        self.assertEqual(self.role().read_text(), "custom\n")
        self.assertEqual(path.read_text(), "Local instructions\n")

    def test_handwritten_agents_entrypoint_preserves_outside_block(self):
        target = self.project_dir / "AGENTS.md"
        original = "# Local instructions\n\nUse existing tooling.\n"
        target.write_text(original)
        self.build()
        self.assertTrue(target.read_text().startswith(original))
        target.write_text(target.read_text() + "\nLater local note.\n")
        self.build()
        self.assertTrue(target.read_text().endswith("\nLater local note.\n"))
        self.assertEqual(target.with_name("AGENTS.md.skillsync-backup").read_text(), original)

    def test_local_config_model_permissions_and_agent_settings_survive(self):
        self.config_path().parent.mkdir()
        original = '# personal\nmodel = "custom"\napproval_policy = "on-request"\n[agents]\nenabled = false\nmax_threads = 7\n[mcp_servers.custom]\ncommand = "custom"\n'
        self.config_path().write_text(original)
        self.build()
        self.assertEqual(self.config_path().read_text(), original)

    def test_missing_settings_added_to_existing_agents_table(self):
        self.config_path().parent.mkdir()
        self.config_path().write_text('model = "custom"\n[agents] # local\nmax_threads = 2\n[mcp_servers.custom]\ncommand = "custom"\n')
        self.build()
        data = tomllib.loads(self.config_path().read_text())
        self.assertEqual(data["agents"], {"enabled": True, "max_threads": 2})
        self.assertEqual(data["mcp_servers"]["custom"]["command"], "custom")

    def test_limit_and_model_override(self):
        self.build(codex={"max_concurrent_threads_per_session": 2, "agent_overrides": {"explore": {"model": "another-model"}}})
        self.assertEqual(tomllib.loads(self.role().read_text())["model"], "another-model")
        self.assertEqual(tomllib.loads(self.config_path().read_text())["agents"]["max_concurrent_threads_per_session"], 2)
        self.build()
        self.assertEqual(tomllib.loads(self.config_path().read_text())["agents"]["max_concurrent_threads_per_session"], 3)

    def test_invalid_config_not_overwritten(self):
        self.config_path().parent.mkdir()
        self.config_path().write_text("[[[broken")
        self.build()
        self.assertEqual(self.config_path().read_text(), "[[[broken")

    def test_dry_run_creates_nothing(self):
        skillsync.build_codex(self.project_dir, self.manifest, self.config, dry_run=True)
        self.assertEqual(list(self.project_dir.iterdir()), [])

    def test_disabled_and_legacy_manifest_create_nothing(self):
        self.build(codex=False)
        self.build(codex={"enabled": False})
        skillsync.build_codex(self.project_dir, {}, self.config)
        self.assertEqual(list(self.project_dir.iterdir()), [])

    def test_opt_out_removes_managed_artifacts_preserves_local_files(self):
        self.build()
        self.role("reviewer").write_text("# local modification\n")
        custom = self.project_dir / ".agents/skills/manual"
        custom.mkdir()
        (custom / "SKILL.md").write_text("custom")
        self.build(codex=False)
        self.assertFalse(self.role().exists())
        self.assertTrue(self.role("reviewer").exists())
        self.assertTrue(custom.exists())
        self.assertFalse((self.project_dir / ".agents/skills/feature").exists())
        self.assertNotIn("skillsync-instructions", (self.project_dir / "AGENTS.md").read_text())
        self.assertEqual(tomllib.loads(self.config_path().read_text())["agents"], {})

    def test_copy_mode_preserves_local_edits_and_pushes_them(self):
        """K4: the owner is core, so the push needs core consent (`--to-layer core`, writable core layer);
        without it the edit is skipped with `[info]` and the layer stays unchanged."""
        self.build(sync={"method": "copy"})
        manifest_path = self.project_dir / ".github/.skillsync.yaml"
        skillsync.save_yaml(manifest_path, self.manifest)
        target = self.project_dir / ".agents/skills/feature/SKILL.md"
        target.write_text("local update\n")
        self.build(sync={"method": "copy"})
        self.assertEqual(target.read_text(), "local update\n")
        library_file = self.library_dir / "skills/feature/SKILL.md"
        original = library_file.read_text()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(skillsync.sync_copies_to_library(manifest_path, self.config), 0)
        self.assertIn("[info]", out.getvalue())
        self.assertEqual(library_file.read_text(), original)
        self.config["layers"][0]["writable"] = True
        writer = skillsync.LayerWriter(skillsync.config_layers(self.config), to_layer="core")
        skillsync.sync_copies_to_library(manifest_path, self.config, writer=writer)
        self.assertEqual(writer.commit(), 0)
        self.assertEqual(library_file.read_text(), "local update\n")

    def test_codex_only_project_gets_startup_task(self):
        manifest = {**self.manifest, "claude": False}
        from skillsync_workflow import selfcmd
        with mock.patch.object(selfcmd, "_user_entry", return_value=None):  # isolated from the real HOME/PATH
            skillsync.ensure_link_on_open(self.project_dir, manifest, self.config)
        task = json.loads((self.project_dir / ".vscode/tasks.json").read_text())["tasks"][0]
        self.assertEqual(task["runOptions"]["runOn"], "folderOpen")
        self.assertEqual(task["args"][-2:], ["link", "${workspaceFolder}"])
        if os.name != "nt":  # checkout without an entry: the wrapper is the command (Windows: interpreter + wrapper, selfcmd)
            self.assertEqual(task["args"], ["link", "${workspaceFolder}"])
            self.assertEqual(Path(task["command"]).resolve(), SKILLSYNC_PATH.resolve())

    def test_removed_role_pruned_and_manual_role_retained(self):
        self.build()
        custom = self.role("custom")
        custom.write_text("custom")
        self.build(agents=["reviewer"])
        self.assertFalse(self.role().exists())
        self.assertEqual(custom.read_text(), "custom")

    def test_feature_without_explicit_role_list_uses_shared_defaults(self):
        manifest = {"skills": ["feature"]}
        skillsync.build_codex(self.project_dir, manifest, self.config)
        self.assertTrue(self.role().is_file())
        self.assertTrue(self.role("reviewer").is_file())

    def test_claude_gets_same_default_roster_as_codex(self):
        manifest = {"skills": ["feature"]}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        skillsync.build_codex(self.project_dir, manifest, self.config)
        claude = sorted(p.stem for p in (self.project_dir / ".claude" / "agents").glob("*.md"))
        self.assertEqual(claude, ["explore", "reviewer"])
        self.assertTrue(self.role().is_file())
        entries = skillsync.claude_gitignore_entries(manifest, self.config)
        self.assertIn("agents/explore.md", entries)

    def test_old_role_name_explore_is_an_alias_for_one_release(self):
        manifest = {"skills": ["feature"], "agents": ["Explore", "reviewer", "explore"]}
        self.assertEqual(skillsync.resolve_roles(manifest, self.config), ["explore", "reviewer"])
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        skillsync.build_codex(self.project_dir, {**manifest, "codex": {"agent_overrides": {"Explore": {"model": "old-key-model"}}}},
                              self.config)
        self.assertEqual(sorted(p.name for p in (self.project_dir / ".claude" / "agents").glob("*.md")),
                         ["explore.md", "reviewer.md"])
        self.assertEqual(tomllib.loads(self.role().read_text())["model"], "old-key-model")

    def test_explicit_empty_agents_disables_roles_for_both_hosts(self):
        manifest = {"skills": ["feature"], "agents": []}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        skillsync.build_codex(self.project_dir, manifest, self.config)
        self.assertFalse((self.project_dir / ".claude" / "agents").exists())
        self.assertFalse(self.role().exists())

    def test_legacy_project_without_feature_gets_no_claude_roles(self):
        skillsync.build_claude_agents(self.project_dir, {"skills": []}, self.config)
        self.assertFalse((self.project_dir / ".claude" / "agents").exists())

    def test_dry_run_does_not_change_existing_files(self):
        self.build()
        files = [p for p in self.project_dir.rglob("*") if p.is_file() and not p.is_symlink()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        manifest = {**self.manifest, "codex": False}
        skillsync.build_codex(self.project_dir, manifest, self.config, dry_run=True)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    @needs_os_symlinks
    def test_external_symlinks_are_preserved(self):
        external = self.tmp / "outside"
        external.mkdir()
        (external / "SKILL.md").write_text("external")
        target = self.project_dir / ".agents/skills/feature"
        target.parent.mkdir(parents=True)
        target.symlink_to(external, target_is_directory=True)
        self.build()
        self.assertTrue(target.resolve().samefile(external))  # 8.3 short names on Windows differ textually

    def test_invalid_role_name_rejected(self):
        with self.assertRaises(ValueError):
            self.build(codex={"agent_overrides": {"explore": {"name": "../escape"}}})


class StopHookMigrationTests(TempProjectMixin, unittest.TestCase):
    def test_feature_removes_only_our_old_hook_in_mixed_group(self):
        path = self.project_dir / ".claude/settings.json"
        path.parent.mkdir()
        custom = {"type": "command", "command": "custom-audit"}
        old = {"type": "command", "command": "python3", "args": [".claude/hooks/check-tests.py", "tests"]}
        data = {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "", "hooks": [old, custom]}], "PreToolUse": []}}
        path.write_text(json.dumps(data))
        skillsync.merge_claude_settings(self.project_dir, {"skills": ["feature"], "hooks": {"stop_tests": True}}, self.config)
        actual = json.loads(path.read_text())
        self.assertEqual(actual["hooks"]["Stop"], [{"matcher": "", "hooks": [custom]}])
        self.assertEqual(actual["permissions"], data["permissions"])
        self.assertEqual(actual["hooks"]["PreToolUse"], [])

    def test_legacy_hook_migrates_without_removing_custom_group(self):
        path = self.project_dir / ".claude/settings.json"
        path.parent.mkdir()
        custom = {"hooks": [{"type": "prompt", "prompt": "custom"}]}
        path.write_text(json.dumps({"hooks": {"Stop": [custom]}}))
        manifest = {"hooks": {"stop_tests": True}, "tests": {"command": "echo test"}}
        skillsync.merge_claude_settings(self.project_dir, manifest, self.config)
        skillsync.merge_claude_settings(self.project_dir, manifest, self.config)
        groups = json.loads(path.read_text())["hooks"]["Stop"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0], custom)
        self.assertNotIn("args", groups[1]["hooks"][0])

    @unittest.skipUnless(os.name == "posix", "requires a POSIX shell")
    def test_generated_command_executes_and_preserves_shell_characters(self):
        path = self.project_dir / ".claude/hooks/check-tests.py"
        path.parent.mkdir(parents=True)
        path.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        command = "echo 'test'; printf '%s' \"$(this-must-not-execute)\""
        entry = skillsync.build_stop_hook_entry({"python_command": sys.executable}, command)["hooks"][0]
        result = subprocess.run(entry["command"], shell=True, cwd=self.project_dir, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [command])


if __name__ == "__main__":
    unittest.main()
