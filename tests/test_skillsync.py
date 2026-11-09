"""Unit tests for skillsync (Claude Code agents/hooks/settings-merge additions).

Run: python3 -m unittest tests/test_skillsync.py
No pytest dependency on purpose - this must run identically on Windows without
an extra `pip install`.
"""
import contextlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import link_mode, needs_os_symlinks, transitional_config  # noqa: E402

from cli_module import SKILLSYNC_PATH, skillsync  # noqa: E402,F401  (re-exported for other test modules)


def _symlinks_supported() -> bool:
    if os.environ.get("SKILLSYNC_NO_SYMLINK"):
        return False
    tmp = Path(tempfile.mkdtemp(prefix="skillsync-symprobe-"))
    try:
        (tmp / "t").mkdir()
        os.symlink(tmp / "t", tmp / "l", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


needs_symlinks = unittest.skipUnless(_symlinks_supported(), "symlinks unavailable (Windows without Developer Mode)")


class TempProjectMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillsync-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project_dir = self.tmp / "project"
        self.project_dir.mkdir()
        self.library_dir = self.tmp / "library"
        (self.library_dir / "agents").mkdir(parents=True)
        (self.library_dir / "hooks").mkdir(parents=True)
        (self.library_dir / "skills").mkdir(parents=True)
        (self.library_dir / "instructions").mkdir(parents=True)
        # One core layer at the test library; no legacy `library:` key (the script ignores it since 2.2b, K1).
        self.config = transitional_config(self.library_dir, legacy_key=False, variables={}, tags={},
                                          python_command="python3")
        # link_project reaches the composite cache: never let a test touch the real ~/.cache/skillsync.
        self.cache_dir = self.tmp / "cache-home"
        from skillsync_workflow import layers as _layers
        patcher = mock.patch.object(_layers, "cache_home", lambda system=None: self.cache_dir)
        patcher.start()
        self.addCleanup(patcher.stop)


class CacheIsolationTests(TempProjectMixin, unittest.TestCase):
    def test_composite_cache_of_the_fixture_is_inside_the_temporary_directory(self):
        from skillsync_workflow import layers
        self.assertTrue(layers.composed_root().is_relative_to(self.tmp))
        self.assertNotEqual(layers.composed_root(), Path.home() / ".cache" / "skillsync" / "composed")


class ClaudeEnabledTests(unittest.TestCase):
    def test_default_enabled(self):
        self.assertTrue(skillsync.claude_enabled({}))

    def test_bool_false_disables(self):
        self.assertFalse(skillsync.claude_enabled({"claude": False}))

    def test_dict_enabled_false_disables(self):
        self.assertFalse(skillsync.claude_enabled({"claude": {"enabled": False}}))

    def test_dict_enabled_true_stays_enabled(self):
        self.assertTrue(skillsync.claude_enabled({"claude": {"enabled": True}}))


class MergeClaudeSettingsTests(TempProjectMixin, unittest.TestCase):
    def manifest(self, **overrides):
        base = {
            "hooks": {"stop_tests": True},
            "tests": {"command": "./run-tests.sh"},
        }
        base.update(overrides)
        return base

    def settings_path(self):
        return self.project_dir / ".claude" / "settings.json"

    def test_no_settings_written_when_stop_tests_disabled(self):
        skillsync.merge_claude_settings(self.project_dir, {}, self.config)
        self.assertFalse(self.settings_path().exists())

    def test_no_settings_written_when_claude_disabled(self):
        skillsync.merge_claude_settings(self.project_dir, self.manifest(claude=False), self.config)
        self.assertFalse(self.settings_path().exists())

    def test_warns_and_skips_when_no_test_command(self):
        manifest = {"hooks": {"stop_tests": True}}
        skillsync.merge_claude_settings(self.project_dir, manifest, self.config)
        self.assertFalse(self.settings_path().exists())

    def test_creates_settings_with_stop_hook(self):
        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)
        data = json.loads(self.settings_path().read_text(encoding="utf-8"))
        stop_hooks = data["hooks"]["Stop"]
        self.assertEqual(len(stop_hooks), 1)
        command_entry = stop_hooks[0]["hooks"][0]
        self.assertNotIn("args", command_entry)
        self.assertEqual(
            shlex.split(command_entry["command"]),
            ["python3", ".claude/hooks/check-tests.py", "./run-tests.sh"],
        )

    def test_preserves_existing_permissions_and_other_hooks(self):
        claude_dir = self.project_dir / ".claude"
        claude_dir.mkdir()
        existing = {
            "permissions": {"allow": ["Bash(git fetch *)", "Read(//home/user/**)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": []}]},
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)

        data = json.loads(self.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"], existing["permissions"])
        self.assertEqual(data["hooks"]["PreToolUse"], existing["hooks"]["PreToolUse"])
        self.assertIn("Stop", data["hooks"])

    def test_backup_created_on_overwrite(self):
        claude_dir = self.project_dir / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.json"
        settings_path.write_text(json.dumps({"permissions": {"allow": ["X"]}}), encoding="utf-8")

        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)

        backup_path = claude_dir / "settings.json.skillsync-backup"
        self.assertTrue(backup_path.exists())
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(backup_data, {"permissions": {"allow": ["X"]}})

    def test_idempotent_second_run_no_op(self):
        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)
        first_write = self.settings_path().read_text(encoding="utf-8")
        first_mtime = self.settings_path().stat().st_mtime_ns

        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)
        second_write = self.settings_path().read_text(encoding="utf-8")

        self.assertEqual(first_write, second_write)
        self.assertEqual(first_mtime, self.settings_path().stat().st_mtime_ns)

    def test_dry_run_writes_nothing(self):
        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config, dry_run=True)
        self.assertFalse(self.settings_path().exists())

    def test_invalid_existing_json_is_not_clobbered(self):
        claude_dir = self.project_dir / ".claude"
        claude_dir.mkdir()
        settings_path = claude_dir / "settings.json"
        settings_path.write_text("{not valid json", encoding="utf-8")

        skillsync.merge_claude_settings(self.project_dir, self.manifest(), self.config)

        self.assertEqual(settings_path.read_text(encoding="utf-8"), "{not valid json")


class EnsureClaudeGitignoreTests(TempProjectMixin, unittest.TestCase):
    ENTRIES = ["agents/implementer.md", "skills/feature", "hooks/check-tests.py"]

    def test_creates_expected_entries(self):
        claude_dir = self.project_dir / ".claude"
        skillsync.ensure_claude_gitignore(claude_dir, self.ENTRIES)
        content = (claude_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        for entry in self.ENTRIES:
            self.assertIn(entry, content)

    def test_never_ignores_whole_directories(self):
        claude_dir = self.project_dir / ".claude"
        skillsync.ensure_claude_gitignore(claude_dir, self.ENTRIES)
        content = (claude_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        for whole_dir in ("agents/", "skills/", "hooks/"):
            self.assertNotIn(whole_dir, content)

    def test_no_entries_creates_nothing(self):
        claude_dir = self.project_dir / ".claude"
        skillsync.ensure_claude_gitignore(claude_dir, [])
        self.assertFalse(claude_dir.exists())

    def test_dry_run_does_not_create_directory(self):
        claude_dir = self.project_dir / ".claude"
        skillsync.ensure_claude_gitignore(claude_dir, self.ENTRIES, dry_run=True)
        self.assertFalse(claude_dir.exists())

    def test_preserves_existing_lines_and_is_idempotent(self):
        claude_dir = self.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".gitignore").write_text("settings.local.json\n", encoding="utf-8")
        skillsync.ensure_claude_gitignore(claude_dir, self.ENTRIES)
        first = (claude_dir / ".gitignore").read_text(encoding="utf-8")
        skillsync.ensure_claude_gitignore(claude_dir, self.ENTRIES)
        second = (claude_dir / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertIn("settings.local.json", first.splitlines())


class ClaudeGitignoreEntriesTests(TempProjectMixin, unittest.TestCase):
    def test_entries_from_manifest(self):
        (self.library_dir / "skills" / "feature").mkdir()
        manifest = {
            "agents": ["implementer"],
            "skills": ["feature"],
            "hooks": {"stop_tests": True},
        }
        entries = skillsync.claude_gitignore_entries(manifest, self.config)
        self.assertEqual(
            entries,
            ["agents/implementer.md", "skills/feature", "hooks/check-tests.py", "settings.json.skillsync-backup"],
        )

    def test_legacy_manifest_has_no_entries(self):
        self.assertEqual(skillsync.claude_gitignore_entries({}, self.config), [])


class BuildClaudeAgentsTests(TempProjectMixin, unittest.TestCase):
    def write_agent(self, name="implementer"):
        (self.library_dir / "agents" / f"{name}.md").write_text(f"---\nname: {name}\n---\n")

    def agents_dir(self):
        return self.project_dir / ".claude" / "agents"

    def test_no_agents_key_creates_nothing(self):
        skillsync.build_claude_agents(self.project_dir, {}, self.config)
        self.assertFalse(self.agents_dir().exists())

    @needs_os_symlinks
    @link_mode('symlink')  # asserts the symlink output: pinned, so SKILLSYNC_NO_SYMLINK=1 in the run does not change it
    def test_symlinks_requested_agents(self):
        self.write_agent()
        manifest = {"agents": ["implementer"], "sync": {"method": "symlink"}}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        self.assertTrue((self.agents_dir() / "implementer.md").is_symlink())

    @link_mode('copy')
    def test_no_symlink_env_copies_requested_agents(self):
        """Copy-mode twin of `test_symlinks_requested_agents`: `SKILLSYNC_NO_SYMLINK=1` gives a file, not a link."""
        self.write_agent()
        manifest = {"agents": ["implementer"], "sync": {"method": "symlink"}}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        dest = self.agents_dir() / "implementer.md"
        self.assertTrue(dest.is_file())
        self.assertFalse(dest.is_symlink())
        self.assertEqual(dest.read_text(encoding="utf-8"), (self.library_dir / "agents" / "implementer.md").read_text(encoding="utf-8"))

    @link_mode('copy')
    def test_no_symlink_env_keeps_hand_written_agents_and_replaces_nothing_foreign(self):
        """Copy-mode twin of the hand-written guarantees: a copy run must not touch a file it does not manage."""
        self.write_agent()
        self.agents_dir().mkdir(parents=True)
        mine = self.agents_dir() / "my-own-agent.md"
        mine.write_text("---\nname: my-own-agent\n---\n", encoding="utf-8")
        foreign = self.agents_dir() / "implementer.md"
        foreign.write_text("HAND WRITTEN", encoding="utf-8")
        skillsync.build_claude_agents(
            self.project_dir, {"agents": ["implementer"], "sync": {"method": "symlink"}}, self.config
        )
        skillsync.build_claude_agents(self.project_dir, {"agents": [], "sync": {"method": "symlink"}}, self.config)
        self.assertTrue(mine.exists())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "HAND WRITTEN")

    def test_copy_method_copies_instead_of_symlinking(self):
        self.write_agent()
        manifest = {"agents": ["implementer"], "sync": {"method": "copy"}}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        dest = self.agents_dir() / "implementer.md"
        self.assertTrue(dest.is_file())
        self.assertFalse(dest.is_symlink())

    @needs_os_symlinks
    def test_removes_obsolete_library_symlink_even_when_list_emptied(self):
        self.write_agent()
        skillsync.build_claude_agents(
            self.project_dir, {"agents": ["implementer"], "sync": {"method": "symlink"}}, self.config
        )
        skillsync.build_claude_agents(
            self.project_dir, {"agents": [], "sync": {"method": "symlink"}}, self.config
        )
        self.assertFalse((self.agents_dir() / "implementer.md").exists())

    @needs_os_symlinks
    def test_hand_written_agent_is_never_removed(self):
        self.write_agent()
        self.agents_dir().mkdir(parents=True)
        mine = self.agents_dir() / "my-own-agent.md"
        mine.write_text("---\nname: my-own-agent\n---\n", encoding="utf-8")
        skillsync.build_claude_agents(
            self.project_dir, {"agents": ["implementer"], "sync": {"method": "symlink"}}, self.config
        )
        skillsync.build_claude_agents(
            self.project_dir, {"agents": [], "sync": {"method": "symlink"}}, self.config
        )
        self.assertTrue(mine.exists())

    def test_existing_real_file_not_overwritten_in_symlink_mode(self):
        self.write_agent()
        self.agents_dir().mkdir(parents=True)
        mine = self.agents_dir() / "implementer.md"
        mine.write_text("HAND WRITTEN", encoding="utf-8")
        skillsync.build_claude_agents(
            self.project_dir, {"agents": ["implementer"], "sync": {"method": "symlink"}}, self.config
        )
        self.assertFalse(mine.is_symlink())
        self.assertEqual(mine.read_text(encoding="utf-8"), "HAND WRITTEN")

    def test_dry_run_writes_nothing(self):
        self.write_agent()
        manifest = {"agents": ["implementer"], "sync": {"method": "symlink"}}
        skillsync.build_claude_agents(self.project_dir, manifest, self.config, dry_run=True)
        self.assertFalse(self.agents_dir().exists())


class BuildClaudeSkillsTests(TempProjectMixin, unittest.TestCase):
    def write_skill(self, name="feature"):
        d = self.library_dir / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

    def skills_dir(self):
        return self.project_dir / ".claude" / "skills"

    @link_mode('copy')
    def test_no_symlink_env_copies_manifest_skills(self):
        """Copy-mode twin of `test_mirrors_manifest_skills_as_symlinks`: a real directory with the library content."""
        self.write_skill()
        skillsync.build_claude_skills(
            self.project_dir, {"skills": ["feature"], "sync": {"method": "symlink"}}, self.config
        )
        copied = self.skills_dir() / "feature"
        self.assertTrue(copied.is_dir())
        self.assertFalse(copied.is_symlink())
        self.assertEqual((copied / "SKILL.md").read_bytes(), (self.library_dir / "skills" / "feature" / "SKILL.md").read_bytes())

    @needs_os_symlinks
    @link_mode('symlink')  # asserts the symlink output: pinned, so SKILLSYNC_NO_SYMLINK=1 in the run does not change it
    def test_mirrors_manifest_skills_as_symlinks(self):
        self.write_skill()
        skillsync.build_claude_skills(
            self.project_dir, {"skills": ["feature"], "sync": {"method": "symlink"}}, self.config
        )
        self.assertTrue((self.skills_dir() / "feature").is_symlink())
        self.assertTrue((self.skills_dir() / "feature" / "SKILL.md").exists())

    @needs_os_symlinks
    def test_hand_written_skill_dir_survives(self):
        self.write_skill()
        mine = self.skills_dir() / "my-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("mine", encoding="utf-8")
        skillsync.build_claude_skills(
            self.project_dir, {"skills": ["feature"], "sync": {"method": "symlink"}}, self.config
        )
        skillsync.build_claude_skills(
            self.project_dir, {"skills": [], "sync": {"method": "symlink"}}, self.config
        )
        self.assertTrue((mine / "SKILL.md").exists())
        self.assertFalse((self.skills_dir() / "feature").exists())

    def test_claude_disabled_creates_nothing(self):
        self.write_skill()
        skillsync.build_claude_skills(
            self.project_dir, {"skills": ["feature"], "claude": False}, self.config
        )
        self.assertFalse(self.skills_dir().exists())


class BuildClaudeHooksTests(TempProjectMixin, unittest.TestCase):
    def test_copies_hook_and_marks_executable(self):
        (self.library_dir / "hooks" / "check-tests.py").write_text("#!/usr/bin/env python3\n")
        skillsync.build_claude_hooks(self.project_dir, {"hooks": {"stop_tests": True}}, self.config)
        dest = self.project_dir / ".claude" / "hooks" / "check-tests.py"
        self.assertTrue(dest.is_file())
        self.assertFalse(dest.is_symlink())
        if sys.platform != "win32":
            self.assertTrue(dest.stat().st_mode & 0o111)

    def test_disabled_by_default(self):
        (self.library_dir / "hooks" / "check-tests.py").write_text("x")
        skillsync.build_claude_hooks(self.project_dir, {}, self.config)
        self.assertFalse((self.project_dir / ".claude" / "hooks").exists())


class StripJsoncTests(unittest.TestCase):
    def parse(self, text):
        return json.loads(skillsync.strip_jsonc(text))

    def test_line_and_block_comments_removed(self):
        self.assertEqual(self.parse('{\n// c\n"a": 1, /* x */ "b": 2}'), {"a": 1, "b": 2})

    def test_trailing_commas_removed(self):
        self.assertEqual(self.parse('{"a": [1, 2,], "b": 3,}'), {"a": [1, 2], "b": 3})

    def test_comment_markers_and_commas_inside_strings_survive(self):
        self.assertEqual(self.parse('{"u": "http://x/*y*/", "c": "a,}"}'), {"u": "http://x/*y*/", "c": "a,}"})

    def test_escaped_quote_in_string(self):
        self.assertEqual(self.parse(r'{"a": "q\"//not a comment"}'), {"a": 'q"//not a comment'})


class BuildClaudeMcpTests(TempProjectMixin, unittest.TestCase):
    def mcp_path(self):
        return self.project_dir / ".mcp.json"

    def read_mcp(self):
        return json.loads(self.mcp_path().read_text(encoding="utf-8"))

    def write_vscode(self, text):
        vscode = self.project_dir / ".vscode"
        vscode.mkdir(exist_ok=True)
        (vscode / "mcp.json").write_text(text, encoding="utf-8")

    def gitignore_lines(self):
        return (self.project_dir / ".gitignore").read_text(encoding="utf-8").splitlines()

    def test_creates_default_chrome_devtools(self):
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        data = self.read_mcp()
        self.assertEqual(data, {"mcpServers": skillsync.DEFAULT_MCP_SERVERS})
        self.assertIn("http://127.0.0.1:9222", data["mcpServers"]["chrome-devtools"]["args"])

    def test_gitignores_mcp_json(self):
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertIn(".mcp.json", self.gitignore_lines())

    def test_defaults_are_not_shared_between_calls(self):
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.read_mcp()["mcpServers"]["chrome-devtools"]["args"].append("x")
        self.assertNotIn("x", skillsync.DEFAULT_MCP_SERVERS["chrome-devtools"]["args"])

    def test_disabled_by_mcp_false_and_by_claude_false(self):
        skillsync.build_claude_mcp(self.project_dir, {"mcp": False}, self.config)
        skillsync.build_claude_mcp(self.project_dir, {"mcp": {"enabled": False}}, self.config)
        skillsync.build_claude_mcp(self.project_dir, {"claude": False}, self.config)
        self.assertFalse(self.mcp_path().exists())
        self.assertFalse((self.project_dir / ".gitignore").exists())

    def test_dry_run_writes_nothing(self):
        skillsync.build_claude_mcp(self.project_dir, {}, self.config, dry_run=True)
        self.assertFalse(self.mcp_path().exists())
        self.assertFalse((self.project_dir / ".gitignore").exists())

    def test_idempotent_second_run_no_op(self):
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        first_mtime = self.mcp_path().stat().st_mtime_ns
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(first_mtime, self.mcp_path().stat().st_mtime_ns)

    def test_existing_default_entry_is_not_overwritten(self):
        custom = {"mcpServers": {"chrome-devtools": {"command": "npx", "args": ["--browserUrl", "http://127.0.0.1:9333"]}}}
        self.mcp_path().write_text(json.dumps(custom), encoding="utf-8")
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.read_mcp(), custom)

    def test_vscode_servers_replace_defaults(self):
        self.write_vscode(json.dumps({"servers": {
            "chrome-devtools-a": {"type": "stdio", "command": "npx", "args": ["--browserUrl", "http://127.0.0.1:9222"]},
            "chrome-devtools-b": {"type": "stdio", "command": "npx", "args": ["--browserUrl", "http://127.0.0.1:9223"]},
        }}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        servers = self.read_mcp()["mcpServers"]
        self.assertEqual(sorted(servers), ["chrome-devtools-a", "chrome-devtools-b"])
        self.assertEqual(servers["chrome-devtools-b"]["args"][1], "http://127.0.0.1:9223")

    def test_vscode_overrides_same_named_entry_and_keeps_foreign_ones(self):
        self.mcp_path().write_text(json.dumps({
            "someKey": 1,
            "mcpServers": {
                "chrome-devtools": {"command": "old"},
                "mine": {"command": "mine"},
            },
        }), encoding="utf-8")
        self.write_vscode(json.dumps({"servers": {"chrome-devtools": {"command": "npx", "args": ["a"]}}}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        data = self.read_mcp()
        self.assertEqual(data["someKey"], 1)
        self.assertEqual(data["mcpServers"]["chrome-devtools"], {"command": "npx", "args": ["a"]})
        self.assertEqual(data["mcpServers"]["mine"], {"command": "mine"})

    def test_registry_style_vscode_name_is_shortened(self):
        self.write_vscode(json.dumps({"servers": {"io.github.ChromeDevTools/chrome-devtools-mcp": {"command": "npx"}}}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(list(self.read_mcp()["mcpServers"]), ["chrome-devtools-mcp"])

    def test_name_collision_keeps_full_sanitized_names(self):
        self.write_vscode(json.dumps({"servers": {"a/srv": {"command": "x"}, "b/srv": {"command": "y"}}}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(sorted(self.read_mcp()["mcpServers"]), ["a-srv", "b-srv"])

    def test_identical_config_under_other_name_is_not_duplicated(self):
        args = ["--registry", "https://registry.npmjs.org", "chrome-devtools-mcp@0.18.1", "--browserUrl", "http://127.0.0.1:9222"]
        existing = {"mcpServers": {"chrome-devtools": {"type": "stdio", "command": "npx", "args": args}}}
        self.mcp_path().write_text(json.dumps(existing), encoding="utf-8")
        self.write_vscode(json.dumps({"servers": {"io.github.ChromeDevTools/chrome-devtools-mcp": {"type": "stdio", "command": "npx", "args": args}}}))
        before = self.mcp_path().stat().st_mtime_ns
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.read_mcp(), existing)
        self.assertEqual(before, self.mcp_path().stat().st_mtime_ns)

    def test_vscode_jsonc_with_comments_is_parsed(self):
        self.write_vscode('{\n  // dev browser\n  "servers": {\n    "x": {"command": "npx",},\n  },\n}\n')
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.read_mcp(), {"mcpServers": {"x": {"command": "npx"}}})

    def test_vscode_variables_and_unsupported_keys_are_translated(self):
        self.write_vscode(json.dumps({"servers": {"x": {
            "type": "stdio",
            "command": "node",
            "args": ["${workspaceFolder}/srv.js"],
            "env": {"TOKEN": "${env:MY_TOKEN}"},
            "envFile": ".env",
        }}}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.read_mcp()["mcpServers"]["x"], {
            "type": "stdio",
            "command": "node",
            "args": ["./srv.js"],
            "env": {"TOKEN": "${MY_TOKEN}"},
        })

    def test_vscode_server_with_input_variable_is_skipped(self):
        self.write_vscode(json.dumps({"servers": {
            "secret": {"command": "npx", "env": {"K": "${input:key}"}},
            "ok": {"command": "npx"},
        }}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(list(self.read_mcp()["mcpServers"]), ["ok"])

    def test_vscode_http_server_is_kept(self):
        self.write_vscode(json.dumps({"servers": {"api": {"type": "http", "url": "https://x/mcp", "headers": {"A": "b"}}}}))
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.read_mcp()["mcpServers"]["api"], {"type": "http", "url": "https://x/mcp", "headers": {"A": "b"}})

    def test_unreadable_vscode_file_falls_back_to_defaults(self):
        self.write_vscode("{not json")
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(list(self.read_mcp()["mcpServers"]), ["chrome-devtools"])

    def test_empty_vscode_servers_fall_back_to_defaults(self):
        self.write_vscode('{"servers": {}}')
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(list(self.read_mcp()["mcpServers"]), ["chrome-devtools"])

    def test_invalid_existing_json_is_not_clobbered(self):
        self.mcp_path().write_text("{not valid json", encoding="utf-8")
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertEqual(self.mcp_path().read_text(encoding="utf-8"), "{not valid json")


class CopyModeAndSymlinkFallbackTests(TempProjectMixin, unittest.TestCase):
    """Windows without Developer Mode: symlinks fail, skillsync must copy and not lose edits."""

    def setUp(self):
        super().setUp()
        skill = self.library_dir / "skills" / "feature"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("v1\n", encoding="utf-8")
        self.manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        self.manifest_path.parent.mkdir(parents=True)
        self.manifest_path.write_text("skills: [feature]\n", encoding="utf-8")
        self.manifest = {"skills": ["feature"], "sync": {"method": "symlink"}}
        skillsync._symlink_warned = False

    def no_symlinks(self):
        def boom(*args, **kwargs):
            raise OSError(1314, "A required privilege is not held by the client")
        original = skillsync.os.symlink
        skillsync.os.symlink = boom
        self.addCleanup(setattr, skillsync.os, "symlink", original)

    def gh_skill(self):
        return self.project_dir / ".github" / "skills" / "feature"

    def link(self):
        skillsync.link_project(self.manifest_path, {**self.config, "discover_paths": []})

    def test_symlink_failure_falls_back_to_copy_and_records_state(self):
        self.no_symlinks()
        self.link()
        self.assertFalse(self.gh_skill().is_symlink())
        self.assertEqual((self.gh_skill() / "SKILL.md").read_text(encoding="utf-8"), "v1\n")
        # K4: copy state is per file `files{owner, base}` (base = content hash at copy time).
        files = skillsync.load_state(self.project_dir)["files"]
        base = skillsync.tree_hash(self.library_dir / "skills" / "feature" / "SKILL.md")
        for kind in ("github-skill", "claude-skill"):
            self.assertEqual(files[f"{kind}:feature/SKILL.md"], {"owner": "core", "base": base}, kind)
        self.assertNotIn("github-skill:feature", skillsync.load_state(self.project_dir))

    def test_state_file_is_gitignored_when_created(self):
        self.no_symlinks()
        self.link()
        gi = self.project_dir / ".github" / ".gitignore"
        self.assertIn(".skillsync-state.json", gi.read_text(encoding="utf-8").splitlines())

    @link_mode('copy')
    def test_no_symlink_env_records_copy_state_and_ignores_it(self):
        """Copy-mode twin of `test_normal_symlink_mode_records_no_state` and `..._not_gitignored_in_symlink_mode`."""
        self.link()
        self.assertTrue(self.gh_skill().is_dir())
        self.assertFalse(self.gh_skill().is_symlink())
        base = skillsync.tree_hash(self.library_dir / "skills" / "feature" / "SKILL.md")
        files = skillsync.load_state(self.project_dir)["files"]
        self.assertEqual(files["github-skill:feature/SKILL.md"], {"owner": "core", "base": base})
        gi = self.project_dir / ".github" / ".gitignore"
        self.assertIn(".skillsync-state.json", gi.read_text(encoding="utf-8").splitlines())

    @needs_os_symlinks
    @link_mode('symlink')  # asserts the symlink output: pinned, so SKILLSYNC_NO_SYMLINK=1 in the run does not change it
    def test_state_file_not_gitignored_in_symlink_mode(self):
        self.link()
        gi = self.project_dir / ".github" / ".gitignore"
        self.assertNotIn(".skillsync-state.json", gi.read_text(encoding="utf-8"))

    def test_second_link_is_a_noop(self):
        self.no_symlinks()
        self.link()
        before = (self.project_dir / ".github" / ".skillsync-state.json").read_text(encoding="utf-8")
        self.link()
        after = (self.project_dir / ".github" / ".skillsync-state.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_library_update_refreshes_untouched_copy(self):
        self.no_symlinks()
        self.link()
        (self.library_dir / "skills" / "feature" / "SKILL.md").write_text("v2\n", encoding="utf-8")
        self.link()
        self.assertEqual((self.gh_skill() / "SKILL.md").read_text(encoding="utf-8"), "v2\n")

    def core_consent_writer(self, dry_run=False):
        """K4: a core file is written only by a writer with `--to-layer core` into a writable core layer."""
        self.config = transitional_config(self.library_dir, legacy_key=False, writable=True, variables={}, tags={},
                                          python_command="python3")
        return skillsync.LayerWriter(skillsync.config_layers(self.config), to_layer="core", dry_run=dry_run)

    def push(self, writer=None, dry_run=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = skillsync.sync_copies_to_library(self.manifest_path, self.config, dry_run=dry_run, writer=writer)
            if writer is not None:
                code = skillsync.status_code(code, writer.commit())
        return code, out.getvalue()

    def test_local_edit_survives_link_and_is_pushed_to_library(self):
        """K4: the owner of the edited file is core, so it is pushed only with core consent (`--to-layer core`)."""
        self.no_symlinks()
        self.link()
        (self.gh_skill() / "SKILL.md").write_text("local edit\n", encoding="utf-8")

        self.link()
        self.assertEqual((self.gh_skill() / "SKILL.md").read_text(encoding="utf-8"), "local edit\n")

        code, _out = self.push(self.core_consent_writer())
        self.assertEqual(code, 0)
        lib_file = self.library_dir / "skills" / "feature" / "SKILL.md"
        self.assertEqual(lib_file.read_text(encoding="utf-8"), "local edit\n")
        entry = skillsync.load_state(self.project_dir)["files"]["github-skill:feature/SKILL.md"]
        self.assertEqual(entry, {"owner": "core", "base": skillsync.tree_hash(lib_file)}, "pushed edit = new base")

    def test_local_edit_of_a_core_file_without_consent_is_info_and_not_written(self):
        """K4: core is written only with `--to-layer <core>`; without it `[info]`, the file skipped, exit 0."""
        self.no_symlinks()
        self.link()
        (self.gh_skill() / "SKILL.md").write_text("local edit\n", encoding="utf-8")
        code, out = self.push()
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"\[info\] .*--to-layer core")
        self.assertEqual((self.library_dir / "skills" / "feature" / "SKILL.md").read_text(encoding="utf-8"), "v1\n")

    def test_conflict_overwrites_nothing(self):
        """K4: changed on both sides = `[conflict]`, nothing written even with core consent."""
        self.no_symlinks()
        self.link()
        (self.gh_skill() / "SKILL.md").write_text("local edit\n", encoding="utf-8")
        lib_file = self.library_dir / "skills" / "feature" / "SKILL.md"
        lib_file.write_text("library edit\n", encoding="utf-8")

        self.link()
        _code, out = self.push(self.core_consent_writer())

        self.assertIn("[conflict]", out)
        self.assertEqual((self.gh_skill() / "SKILL.md").read_text(encoding="utf-8"), "local edit\n")
        self.assertEqual(lib_file.read_text(encoding="utf-8"), "library edit\n")

    def test_push_dry_run_does_not_touch_library(self):
        """Dry run with core consent: the write is announced, nothing is written."""
        self.no_symlinks()
        self.link()
        (self.gh_skill() / "SKILL.md").write_text("local edit\n", encoding="utf-8")
        _code, out = self.push(self.core_consent_writer(dry_run=True), dry_run=True)
        self.assertIn("[dry-run] Would write", out)
        lib_file = self.library_dir / "skills" / "feature" / "SKILL.md"
        self.assertEqual(lib_file.read_text(encoding="utf-8"), "v1\n")

    @needs_os_symlinks
    @link_mode('symlink')  # asserts the symlink output: pinned, so SKILLSYNC_NO_SYMLINK=1 in the run does not change it
    def test_normal_symlink_mode_records_no_state(self):
        self.link()
        self.assertTrue(self.gh_skill().is_symlink())
        self.assertEqual(skillsync.load_state(self.project_dir), {})

    def test_crlf_only_difference_is_not_a_local_edit(self):
        self.no_symlinks()
        self.link()
        (self.gh_skill() / "SKILL.md").write_bytes(b"v1\r\n")
        (self.library_dir / "skills" / "feature" / "SKILL.md").write_text("v2\n", encoding="utf-8")
        self.link()
        self.assertEqual((self.gh_skill() / "SKILL.md").read_text(encoding="utf-8"), "v2\n")


class LineEndingTests(TempProjectMixin, unittest.TestCase):
    def test_write_text_lf_never_emits_cr(self):
        target = self.tmp / "out.txt"
        skillsync.write_text_lf(target, "a\nb\n")
        self.assertNotIn(b"\r", target.read_bytes())

    def test_tree_hash_ignores_crlf(self):
        a, b = self.tmp / "a.txt", self.tmp / "b.txt"
        a.write_bytes(b"x\r\ny\r\n")
        b.write_bytes(b"x\ny\n")
        self.assertEqual(skillsync.tree_hash(a), skillsync.tree_hash(b))

    def test_tree_hash_detects_real_change(self):
        a, b = self.tmp / "a.txt", self.tmp / "b.txt"
        a.write_bytes(b"x\n")
        b.write_bytes(b"y\n")
        self.assertNotEqual(skillsync.tree_hash(a), skillsync.tree_hash(b))


class AgentflowBootstrapTests(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.library_dir / "skills" / "feature").mkdir()
        (self.library_dir / "instructions" / "_base.md").write_text("Shared instructions\n", encoding="utf-8")
        self.manifest = {"skills": ["feature"]}

    def test_feature_without_template_gets_base_instructions(self):
        skillsync.build_claude_md(self.project_dir, self.manifest, self.config)
        target = self.project_dir / "CLAUDE.md"
        self.assertIn("Shared instructions", target.read_text(encoding="utf-8"))
        self.assertIn("CLAUDE.md", (self.project_dir / ".gitignore").read_text())
        (self.library_dir / "instructions" / "_base.md").write_text("Updated shared instructions\n")
        skillsync.build_claude_md(self.project_dir, self.manifest, self.config)
        self.assertIn("Updated shared instructions", target.read_text())

    def test_merge_only_preserves_and_references_local_project_instructions(self):
        source = self.project_dir / ".github" / "copilot-instructions.md"
        source.parent.mkdir()
        source.write_text("Project-specific local edits\n", encoding="utf-8")
        manifest = {"instructions": {"merge": ["_base"]}}
        skillsync.build_claude_md(self.project_dir, manifest, self.config)
        generated = (self.project_dir / "CLAUDE.md").read_text()
        self.assertIn("Shared instructions", generated)
        self.assertIn(".github/copilot-instructions.md", generated)
        self.assertEqual(source.read_text(), "Project-specific local edits\n")

    def test_no_template_leaves_handwritten_claude_md_alone(self):
        target = self.project_dir / "CLAUDE.md"
        target.write_text("Handwritten instructions\n")
        skillsync.build_claude_md(self.project_dir, self.manifest, self.config)
        self.assertEqual(target.read_text(), "Handwritten instructions\n")

    def test_legacy_empty_manifest_does_not_generate_instructions(self):
        skillsync.build_claude_md(self.project_dir, {}, self.config)
        self.assertFalse((self.project_dir / "CLAUDE.md").exists())

    def test_dry_run_and_claude_opt_out_do_not_write(self):
        skillsync.build_claude_md(self.project_dir, self.manifest, self.config, dry_run=True)
        skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config, dry_run=True)
        disabled = {**self.manifest, "claude": False, "codex": False}
        skillsync.build_claude_md(self.project_dir, disabled, self.config)
        skillsync.ensure_link_on_open(self.project_dir, disabled, self.config)
        self.assertEqual(list(self.project_dir.iterdir()), [])

    def test_task_bootstrap_preserves_other_tasks_and_is_idempotent(self):
        target = self.project_dir / ".vscode" / "tasks.json"
        target.parent.mkdir()
        original = '{// custom comment\n"version":"2.0.0","inputs":[{"id":"x"}],"tasks":[{"label":"build","command":"build-app"},],}'
        target.write_text(original, encoding="utf-8")
        from skillsync_workflow import selfcmd
        no_entry = mock.patch.object(selfcmd, "_user_entry", return_value=None)
        no_entry.start()
        self.addCleanup(no_entry.stop)
        skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
        data = json.loads(target.read_text())
        self.assertEqual(data["tasks"][0], {"label": "build", "command": "build-app"})
        self.assertEqual(data["inputs"], [{"id": "x"}])
        task = data["tasks"][1]
        self.assertEqual(task["runOptions"], {"runOn": "folderOpen"})
        self.assertEqual(task["type"], "process")
        self.assertEqual(task["args"][-2:], ["link", "${workspaceFolder}"])
        # no user entry (isolated from the real HOME/PATH), checkout: the wrapper itself is the command
        if os.name != "nt":  # Windows without an entry: interpreter + wrapper (selfcmd.link_task_argv)
            self.assertEqual(task["args"], ["link", "${workspaceFolder}"])
            self.assertEqual(Path(task["command"]).resolve(), SKILLSYNC_PATH.resolve())
        self.assertTrue(os.access(task["command"], os.X_OK))
        self.assertEqual(target.with_name("tasks.json.skillsync-backup").read_text(), original)
        content, mtime = target.read_bytes(), target.stat().st_mtime_ns
        skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(target.stat().st_mtime_ns, mtime)

    def test_existing_custom_link_task_is_not_overwritten(self):
        target = self.project_dir / ".vscode" / "tasks.json"
        target.parent.mkdir()
        original = '{"tasks":[{"label":"skillsync: link","command":"custom-wrapper"}]}'
        target.write_text(original)
        skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
        self.assertEqual(target.read_text(), original)

    def test_malformed_tasks_are_not_overwritten(self):
        target = self.project_dir / ".vscode" / "tasks.json"
        target.parent.mkdir()
        for original in ('{broken', '[]', '{"tasks":{}}'):
            with self.subTest(original=original):
                target.write_text(original)
                skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
                self.assertEqual(target.read_text(), original)

    def test_task_opt_out_and_non_feature_projects_are_untouched(self):
        skillsync.ensure_link_on_open(self.project_dir, {}, self.config)
        skillsync.ensure_link_on_open(self.project_dir, {**self.manifest, "vscode": {"link_on_open": False}}, self.config)
        self.assertFalse((self.project_dir / ".vscode").exists())

    def test_feature_tag_is_resolved(self):
        config = {**self.config, "tags": {"workflow": ["feature"]}}
        skillsync.ensure_link_on_open(self.project_dir, {"skills": ["@workflow"]}, config)
        self.assertTrue((self.project_dir / ".vscode" / "tasks.json").exists())


class ManifestDefaultsTests(TempProjectMixin, unittest.TestCase):
    """A manifest without the new sections behaves exactly like before (backward compat)."""

    def test_legacy_manifest_produces_no_claude_agents_hooks_settings(self):
        manifest = {
            "version": 1,
            "skills": [],
            "instructions": {},
            "sync": {"method": "symlink"},
        }
        skillsync.build_claude_agents(self.project_dir, manifest, self.config)
        skillsync.build_claude_hooks(self.project_dir, manifest, self.config)
        skillsync.merge_claude_settings(self.project_dir, manifest, self.config)

        self.assertFalse((self.project_dir / ".claude" / "agents").exists())
        self.assertFalse((self.project_dir / ".claude" / "hooks").exists())
        self.assertFalse((self.project_dir / ".claude" / "settings.json").exists())


class SymlinksDisabledTests(unittest.TestCase):
    """SKILLSYNC_NO_SYMLINK is read explicitly: 1/true/yes/on set it, 0/false/no/off and empty do not."""

    def disabled(self, value):
        env = {k: v for k, v in os.environ.items() if k != "SKILLSYNC_NO_SYMLINK"}
        if value is not None:
            env["SKILLSYNC_NO_SYMLINK"] = value
        skillsync._WARNED_NO_SYMLINK.clear()
        buffer = io.StringIO()
        with unittest.mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(buffer):
            return skillsync.symlinks_disabled(), buffer.getvalue()

    def test_true_values_in_any_case_disable_symlinks_without_a_warning(self):
        for value in ("1", "true", "TRUE", "Yes", "on", " On "):
            self.assertEqual(self.disabled(value), (True, ""), value)

    def test_false_values_and_unset_keep_symlinks_without_a_warning(self):
        for value in (None, "", "  ", "0", "false", "False", "NO", "off", " Off "):
            self.assertEqual(self.disabled(value), (False, ""), value)

    def test_other_values_warn_once_and_count_as_set(self):
        for value in ("2", "flase", "enabled"):
            result, text = self.disabled(value)
            self.assertTrue(result, value)
            self.assertIn("SKILLSYNC_NO_SYMLINK", text)
            self.assertNotIn(value, text)
        env = dict(os.environ, SKILLSYNC_NO_SYMLINK="flase")
        skillsync._WARNED_NO_SYMLINK.clear()
        buffer = io.StringIO()
        with unittest.mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(buffer):
            skillsync.symlinks_disabled()
            skillsync.symlinks_disabled()
        self.assertEqual(buffer.getvalue().count("[warn]"), 1)


class EntryPointsTests(unittest.TestCase):
    """The three ways to start the CLI (installed console script, `python -m`, checkout wrapper) and the wrapper's guards."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skillsync-entry-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        from skillsync_workflow import __version__
        self.expected = f"skillsync {__version__}"
        self.root = Path(SKILLSYNC_PATH).resolve().parent

    def run_cli(self, command, env=None, cwd=None):
        import subprocess
        base = dict(os.environ)
        base.update(env or {})
        return subprocess.run(command, capture_output=True, text=True, timeout=60, env=base, cwd=str(cwd or self.tmp))

    def assertVersion(self, process):
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(process.stdout.strip(), self.expected)

    def test_version_flag_of_the_wrapper(self):
        self.assertVersion(self.run_cli([sys.executable, str(SKILLSYNC_PATH), "--version"]))

    def test_version_flag_of_python_dash_m(self):
        self.assertVersion(self.run_cli([sys.executable, "-m", "skillsync_workflow", "--version"],
                                        env={"PYTHONPATH": str(self.root)}))

    def test_version_flag_of_the_main_function_in_process(self):
        buffer = io.StringIO()
        with mock.patch.object(sys, "argv", ["skillsync", "--version"]), contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit) as raised:
                skillsync.run()
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(buffer.getvalue().strip(), self.expected)

    def test_the_version_is_not_a_placeholder(self):
        self.assertRegex(self.expected, r"^skillsync \d+\.\d+\.\d+")

    @needs_os_symlinks
    def test_wrapper_started_through_a_symlink_finds_the_package(self):
        link = self.tmp / "bin" / "skillsync"
        link.parent.mkdir()
        os.symlink(SKILLSYNC_PATH, link)
        # -I: the script directory is not put on sys.path by the interpreter, so only the wrapper's own insert can find the package
        self.assertVersion(self.run_cli([sys.executable, "-I", str(link), "--version"], env={"PYTHONPATH": ""}))

    def test_missing_pyyaml_gives_a_readable_message_instead_of_a_traceback(self):
        shadow = self.tmp / "shadow"
        shadow.mkdir()
        (shadow / "yaml.py").write_text("raise ModuleNotFoundError(\"No module named 'yaml'\", name='yaml')\n",
                                        encoding="utf-8")
        process = self.run_cli([sys.executable, str(SKILLSYNC_PATH), "--version"], env={"PYTHONPATH": str(shadow)})
        self.assertNotEqual(process.returncode, 0)
        self.assertNotIn("Traceback", process.stderr)
        self.assertIn("PyYAML", process.stderr)
        self.assertEqual(process.stdout, "")

    def test_the_shadow_check_can_fail_other_missing_modules_are_not_swallowed(self):
        """Control: an import error of a different module keeps its traceback (the wrapper only handles `yaml`)."""
        shadow = self.tmp / "shadow-other"
        shadow.mkdir()
        (shadow / "yaml.py").write_text("raise ModuleNotFoundError(\"No module named 'other_dep'\", name='other_dep')\n",
                                        encoding="utf-8")
        process = self.run_cli([sys.executable, str(SKILLSYNC_PATH), "--version"], env={"PYTHONPATH": str(shadow)})
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("other_dep", process.stderr)
        self.assertNotIn("PyYAML is required", process.stderr)


if __name__ == "__main__":
    unittest.main()
