"""`link` appends to a user's .gitignore without touching the existing bytes or line endings (Windows, CRLF/mixed)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.test_skillsync import skillsync  # noqa: E402


class AddGitignoreEntriesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".gitignore"

    def add(self, before, entries, **kwargs):
        if before is not None:
            self.path.write_bytes(before)
        changed = skillsync.add_gitignore_entries(self.path, entries, **kwargs)
        return changed, self.path.read_bytes() if self.path.exists() else None

    def test_lf_file_gets_lf_lines_and_keeps_its_bytes(self):
        before = b"bin/\nobj/\n"
        changed, after = self.add(before, ["CLAUDE.md"])
        self.assertTrue(changed)
        self.assertEqual(after, before + b"CLAUDE.md\n")

    def test_crlf_file_gets_crlf_lines_and_keeps_its_bytes(self):
        before = b"bin/\r\nobj/\r\n"
        changed, after = self.add(before, ["CLAUDE.md", ".skillsync/"])
        self.assertTrue(changed)
        self.assertEqual(after, before + b"CLAUDE.md\r\n.skillsync/\r\n")

    def test_mixed_endings_keep_every_byte_and_follow_the_last_line(self):
        before = b"a\r\nb\nc\r\nd\n"
        _, after = self.add(before, ["x"])
        self.assertEqual(after, before + b"x\n")
        before = b"a\nb\r\nc\n\r\nlast\r\n"
        _, after = self.add(before, ["x"])
        self.assertEqual(after, before + b"x\r\n")

    def test_missing_final_newline_is_completed_in_the_style_of_the_file(self):
        _, after = self.add(b"a\r\nb", ["x"])
        self.assertEqual(after, b"a\r\nb\r\nx\r\n")
        _, after = self.add(b"a\nb", ["x"])
        self.assertEqual(after, b"a\nb\nx\n")
        _, after = self.add(b"single", ["x"])
        self.assertEqual(after, b"single\nx\n")

    def test_new_or_empty_file_uses_lf(self):
        changed, after = self.add(None, ["a", "b"])
        self.assertTrue(changed)
        self.assertEqual(after, b"a\nb\n")
        _, after = self.add(b"", ["c"])
        self.assertEqual(after, b"c\n")

    def test_existing_entry_leaves_the_file_untouched(self):
        before = b"x\r\n  \r\n\r\nCLAUDE.md\r\n\r\n"
        changed, after = self.add(before, ["CLAUDE.md"])
        self.assertFalse(changed)
        self.assertEqual(after, before)

    def test_trailing_blank_lines_and_foreign_bytes_survive(self):
        before = b"caf\xc3\xa9/\r\n\xff\xfe-binary\r\n\r\n\r\n"
        _, after = self.add(before, ["new"])
        self.assertEqual(after, before + b"new\r\n")

    def test_lone_cr_or_lf_and_mixed_without_final_terminator(self):
        _, after = self.add(b"\r", ["x"])
        self.assertEqual(after, b"\r\nx\n")
        _, after = self.add(b"\n", ["x"])
        self.assertEqual(after, b"\nx\n")
        _, after = self.add(b"a\nb\r\nc", ["x"])
        self.assertEqual(after, b"a\nb\r\nc\r\nx\r\n")

    def test_only_missing_entries_are_added_once(self):
        _, after = self.add(b"a\n", ["a", "b", "b", "c"])
        self.assertEqual(after, b"a\nb\nc\n")

    def test_dry_run_writes_nothing(self):
        before = b"a\r\n"
        changed, after = self.add(before, ["b"], dry_run=True)
        self.assertTrue(changed)
        self.assertEqual(after, before)


class EnsureFunctionsKeepLineEndingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_root_gitignore_diff_is_only_the_added_line(self):
        content = b"bin/\r\nobj/\n*.user\r\n"
        (self.root / ".gitignore").write_bytes(content)
        skillsync.ensure_root_gitignore(self.root, entry="CLAUDE.md")
        skillsync.ensure_root_gitignore(self.root, entry=".skillsync/")
        self.assertEqual((self.root / ".gitignore").read_bytes(), content + b"CLAUDE.md\r\n.skillsync/\r\n")

    def test_github_claude_and_cursor_gitignores_keep_crlf(self):
        for folder, call in (
            (".github", lambda d: skillsync.ensure_gitignore(d)),
            (".claude", lambda d: skillsync.ensure_claude_gitignore(d, ["skills/x", "agents/y.md"])),
            (".cursor/rules", lambda d: skillsync.ensure_cursor_gitignore(d)),
        ):
            directory = self.root / folder
            directory.mkdir(parents=True)
            before = b"keep\r\nme\r\n"
            (directory / ".gitignore").write_bytes(before)
            call(directory)
            after = (directory / ".gitignore").read_bytes()
            self.assertTrue(after.startswith(before), folder)
            self.assertNotIn(b"\n", after.replace(b"\r\n", b""), folder)  # only CRLF line endings
            self.assertGreater(len(after), len(before), folder)


class SaveStateGitignoreTests(unittest.TestCase):
    def test_state_folder_gitignore_keeps_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".github").mkdir()
            before = b"copilot-instructions.md\r\nskills/\r\n"
            (project / ".github" / ".gitignore").write_bytes(before)
            skillsync.save_state(project, {"k": "v"})
            after = (project / ".github" / ".gitignore").read_bytes()
            self.assertEqual(after, before + skillsync.STATE_FILE.name.encode() + b"\r\n")


if __name__ == "__main__":
    unittest.main()
