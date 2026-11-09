"""AGENTS.md and .codex/config.toml of the user keep their bytes and line endings; backups are byte copies (Windows, CRLF)."""
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.test_skillsync import skillsync  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
