"""memory_sync: one-way scp of memory files to remote hosts on content change, with retry backoff."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skillsync import skillsync


def completed(returncode=0, stderr=""):
    return mock.Mock(returncode=returncode, stderr=stderr)


class MemorySyncTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "relationship.md"
        self.source.write_text("v1", encoding="utf-8")
        self.config = {"memory_sync": {"files": [str(self.source)],
                                       "targets": [{"host": "win-laptop", "dir": "C:/Users/user/.codex/memories"}]}}

    def run_sync(self, state, result=None, **kwargs):
        with mock.patch.object(skillsync.subprocess, "run", return_value=result or completed()) as run:
            return skillsync.sync_memory(self.config, state, **kwargs), run

    def test_copies_once_and_only_again_after_change(self):
        state = {}
        messages, run = self.run_sync(state)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "scp")
        self.assertEqual(command[-1], "win-laptop:C:/Users/user/.codex/memories/relationship.md")
        self.assertIn("BatchMode=yes", command)
        self.assertTrue(messages[0].startswith("[update]"))
        _, run = self.run_sync(state)
        run.assert_not_called()
        self.source.write_text("v2", encoding="utf-8")
        _, run = self.run_sync(state)
        run.assert_called_once()

    def test_failure_is_retried_only_after_backoff(self):
        state = {}
        messages, _ = self.run_sync(state, completed(255, "Connection timed out"))
        self.assertTrue(messages[0].startswith("[warn]"))
        _, run = self.run_sync(state)
        run.assert_not_called()
        for entry in state.values():
            entry["retry_at"] = 0
        messages, run = self.run_sync(state)
        run.assert_called_once()
        self.assertTrue(messages[0].startswith("[update]"))

    def test_failure_without_stderr_is_still_a_failure(self):
        state = {}
        messages, _ = self.run_sync(state, completed(1, ""))
        self.assertTrue(messages[0].startswith("[warn]"))
        self.assertFalse(any(entry.get("digest") for entry in state.values()))

    def test_dry_run_and_missing_source_do_not_copy(self):
        _, run = self.run_sync({}, dry_run=True)
        run.assert_not_called()
        self.source.unlink()
        _, run = self.run_sync({})
        run.assert_not_called()

    def test_invalid_host_is_skipped(self):
        self.config["memory_sync"]["targets"] = [{"host": "-oProxyCommand=x", "dir": "C:/a"}, {"host": "ok", "dir": ""}]
        with mock.patch("builtins.print"):
            _, run = self.run_sync({})
        run.assert_not_called()

    def test_no_section_is_noop(self):
        self.config.pop("memory_sync")
        messages, run = self.run_sync({})
        self.assertEqual(messages, [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
