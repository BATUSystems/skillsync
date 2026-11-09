"""AGENTS.md and .codex/config.toml of the user keep their bytes and line endings; backups are byte copies (Windows, CRLF)."""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import json  # noqa: E402
from unittest import mock  # noqa: E402

from tests.test_skillsync import TempProjectMixin, skillsync  # noqa: E402

BEGIN, END = "<!-- SKILLSYNC:CODEX-BEGIN -->", "<!-- SKILLSYNC:CODEX-END -->"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class AgentsMdTests(Base):
    def run_entrypoint(self, original, enabled=True):
        target = self.root / "AGENTS.md"
        if original is not None:
            target.write_bytes(original)
        skillsync.update_codex_entrypoint(self.root, enabled)
        return target.read_bytes() if target.exists() else None

    def test_crlf_file_keeps_its_bytes_and_the_block_is_crlf(self):
        before = b"# Moje reguly\r\n\r\nzdanie z polskimi znakami: za\xc5\xbc\xc3\xb3\xc5\x82\xc4\x87\r\n"
        after = self.run_entrypoint(before)
        self.assertTrue(after.startswith(before))
        self.assertIn(BEGIN.encode(), after)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))  # CRLF everywhere
        self.assertEqual((self.root / "AGENTS.md.skillsync-backup").read_bytes(), before)  # byte copy

    def test_mixed_file_keeps_every_original_byte(self):
        before = b"a\r\nb\nc\r\nlast\n"
        after = self.run_entrypoint(before)
        self.assertTrue(after.startswith(before))
        self.assertEqual((self.root / "AGENTS.md.skillsync-backup").read_bytes(), before)

    def test_second_run_changes_nothing_and_removal_restores_the_file(self):
        before = b"x\r\ny\r\n"
        first = self.run_entrypoint(before)
        self.assertEqual(self.run_entrypoint(first), first)
        removed = self.run_entrypoint(first, enabled=False)
        self.assertNotIn(BEGIN.encode(), removed)
        self.assertTrue(removed.startswith(before))

    def test_lf_and_new_files_stay_lf(self):
        after = self.run_entrypoint(b"x\ny\n")
        self.assertNotIn(b"\r", after)
        (self.root / "AGENTS.md").unlink()
        after = self.run_entrypoint(None)
        self.assertNotIn(b"\r", after)


class CodexConfigTests(Base):
    def run_config(self, original, enabled=True):
        target = self.root / ".codex" / "config.toml"
        target.parent.mkdir(exist_ok=True)
        if original is not None:
            target.write_bytes(original)
        skillsync.update_codex_config(self.root, 4, enabled)
        return target.read_bytes(), target

    def test_config_without_agents_table_gets_a_crlf_table(self):
        before = b'model = "x"\r\n[tui]\r\nflag = true\r\n'
        after, target = self.run_config(before)
        self.assertTrue(after.startswith(before.rstrip()))
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        data = tomllib.loads(after.decode())
        self.assertEqual(data["agents"]["max_concurrent_threads_per_session"], 4)
        self.assertEqual((target.parent / "config.toml.skillsync-backup").read_bytes(), before)

    def test_config_with_agents_table_inserts_the_block_in_crlf(self):
        before = b'[agents]  # moje\r\nmax_depth = 2\r\n[tui]\r\nflag = true\r\n'
        after, _ = self.run_config(before)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertTrue(after.endswith(b"max_depth = 2\r\n[tui]\r\nflag = true\r\n"))
        self.assertEqual(tomllib.loads(after.decode())["agents"]["max_depth"], 2)

    def test_second_run_is_idempotent_and_lf_config_stays_lf(self):
        after, _ = self.run_config(b'[agents]\r\nmax_depth = 2\r\n')
        again, _ = self.run_config(after)
        self.assertEqual(again, after)
        (self.root / ".codex" / "config.toml").unlink()
        lf, _ = self.run_config(b'model = "x"\n')
        self.assertNotIn(b"\r", lf)


class JsonFilesTests(TempProjectMixin, unittest.TestCase):
    def assert_crlf_only(self, raw):
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(b"\r\n", raw)

    def test_json_file_text_follows_the_previous_line_ending(self):
        self.assertEqual(skillsync.json_file_text({"a": 1}), '{\n  "a": 1\n}\n')
        self.assertEqual(skillsync.json_file_text({"a": 1}, "{}\r\n"), '{\r\n  "a": 1\r\n}\r\n')
        self.assertEqual(skillsync.json_file_text({"a": 1}, "{}\n"), '{\n  "a": 1\n}\n')

    def test_mcp_json_keeps_crlf_and_new_file_is_lf(self):
        target = self.project_dir / ".mcp.json"
        target.write_bytes(b'{\r\n  "mcpServers": {}\r\n}\r\n')
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assert_crlf_only(target.read_bytes())
        self.assertIn("chrome-devtools", json.loads(target.read_text())["mcpServers"])
        target.unlink()
        skillsync.build_claude_mcp(self.project_dir, {}, self.config)
        self.assertNotIn(b"\r", target.read_bytes())

    def test_claude_settings_keep_crlf_and_the_backup_is_a_byte_copy(self):
        path = self.project_dir / ".claude" / "settings.json"
        path.parent.mkdir()
        old = {"type": "command", "command": "python3", "args": [".claude/hooks/check-tests.py", "tests"]}
        data = {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [{"matcher": "", "hooks": [old]}]}}
        before = (json.dumps(data, indent=4) + "\n").replace("\n", "\r\n").encode()
        path.write_bytes(before)
        skillsync.merge_claude_settings(self.project_dir, {"skills": ["feature"], "hooks": {"stop_tests": True}}, self.config)
        self.assert_crlf_only(path.read_bytes())
        self.assertEqual((path.parent / "settings.json.skillsync-backup").read_bytes(), before)

    def test_tasks_json_keeps_crlf_and_the_backup_is_a_byte_copy(self):
        from skillsync_workflow import selfcmd
        target = self.project_dir / ".vscode" / "tasks.json"
        target.parent.mkdir()
        before = b'{\r\n  "version": "2.0.0",\r\n  "tasks": [{"label": "mine", "type": "shell", "command": "x"}]\r\n}\r\n'
        target.write_bytes(before)
        with mock.patch.object(selfcmd, "_user_entry", return_value=None):
            skillsync.ensure_link_on_open(self.project_dir, {"skills": ["feature"]}, self.config)
        after = target.read_bytes()
        self.assert_crlf_only(after)
        self.assertEqual(len(json.loads(after)["tasks"]), 2)
        self.assertEqual((target.parent / "tasks.json.skillsync-backup").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
