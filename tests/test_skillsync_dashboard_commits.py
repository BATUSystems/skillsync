"""170-A: lista niewypchniętych commitów w `delivery` (`git_ahead`: `commits`, `commitsMore`)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402

AUTHOR = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t" + "@" + "example.invalid",
          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t" + "@" + "example.invalid"}


@unittest.skipUnless(shutil.which("git"), "brak git")
class CommitListTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="commits-"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.git("init", "-q", "-b", "main")
        self.commit("base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "-b", "feature/x")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True,
                              env={**os.environ, **AUTHOR}).stdout.strip()

    def commit(self, message: str) -> None:
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def ahead(self) -> dict:
        return engine.git_ahead(self.repo, "feature/x")

    def test_commits_newest_first_with_short_sha_and_subject(self) -> None:
        self.commit("pierwszy")
        self.commit("drugi\n\nopis w treści, nie w temacie")
        state = self.ahead()
        self.assertEqual((state["ahead"], state["commitsMore"]), (2, 0))
        self.assertEqual([c["subject"] for c in state["commits"]], ["drugi", "pierwszy"])
        self.assertEqual(state["commits"][0]["sha"], self.git("rev-parse", "--short=7", "HEAD")[:7])
        self.assertEqual(set(state["commits"][0]), {"sha", "subject"})

    def test_both_variants_branch_on_origin_and_not(self) -> None:
        self.commit("a")
        self.assertFalse(self.ahead()["pushed"])
        self.assertEqual([c["subject"] for c in self.ahead()["commits"]], ["a"])
        self.git("update-ref", "refs/remotes/origin/feature/x", "HEAD")
        self.commit("b")
        state = self.ahead()
        self.assertTrue(state["pushed"])
        self.assertEqual([c["subject"] for c in state["commits"]], ["b"])

    def test_nothing_ahead_gives_an_empty_list(self) -> None:
        state = self.ahead()
        self.assertEqual((state["ahead"], state["commits"], state["commitsMore"]), (0, [], 0))

    def test_limit_20_and_the_rest_is_counted(self) -> None:
        for i in range(25):
            self.commit(f"c{i:02d}")
        state = self.ahead()
        self.assertEqual((state["ahead"], len(state["commits"]), state["commitsMore"]), (25, 20, 5))
        self.assertEqual(state["commits"][0]["subject"], "c24")
        self.assertEqual(state["commits"][-1]["subject"], "c05")

    def test_exactly_20_has_nothing_more(self) -> None:
        for i in range(20):
            self.commit(f"c{i}")
        state = self.ahead()
        self.assertEqual((len(state["commits"]), state["commitsMore"]), (20, 0))

    def test_subject_is_cut_to_80_characters_and_one_line(self) -> None:
        self.commit("x" * 200)
        subject = self.ahead()["commits"][0]["subject"]
        self.assertEqual(len(subject), 80)
        self.assertTrue(subject.startswith("xxxx") and subject.endswith("x…"), subject)

    def test_a_subject_of_exactly_80_characters_is_not_marked_as_cut(self) -> None:
        self.commit("y" * 80)
        self.assertEqual(self.ahead()["commits"][0]["subject"], "y" * 80)

    def test_the_cut_comes_after_the_mask(self) -> None:
        self.commit("z" * 70 + " token=" + "q" * 30)
        subject = self.ahead()["commits"][0]["subject"]
        self.assertNotIn("qqqq", subject)
        self.assertLessEqual(len(subject), 80)

    def test_polish_letters_and_control_characters(self) -> None:
        self.commit("zażółć gęślą jaźń\x1b[31m")
        subject = self.ahead()["commits"][0]["subject"]
        self.assertIn("zażółć gęślą jaźń", subject)
        self.assertNotIn("\x1b", subject)

    def test_a_key_before_the_cut_still_masks_its_value(self) -> None:
        # klucz leży w pierwszych 80 znakach, wartość przekracza cięcie: maska działa na całym temacie PRZED cięciem
        secret = "hunter2" + "hunter2" + "hunter2"
        self.commit("a" * 60 + " password: " + secret)
        subject = self.ahead()["commits"][0]["subject"]
        self.assertNotIn("hunter2", subject)
        self.assertNotIn(secret[:4], subject)

    def test_a_secret_from_the_environment_is_masked_whole_not_a_prefix(self) -> None:
        secret = "-".join(["zq9x", "wv8y", "ut7s", "rp6q"])
        self.commit("b" * 70 + " " + secret)
        with mock.patch.dict(os.environ, {"DEPLOY_TOKEN": secret}):
            subject = self.ahead()["commits"][0]["subject"]
        self.assertNotIn("zq9x", subject)
        self.assertNotIn("rp6q", subject)

    def test_credentials_in_a_url_are_masked(self) -> None:
        self.commit("push do https://user:" + "pw" * 6 + "@example.invalid/r.git")
        subject = self.ahead()["commits"][0]["subject"]
        self.assertNotIn("pwpw", subject)

    def test_ansi_bidi_and_zero_width_characters_are_removed(self) -> None:
        self.commit("a\x1b[31mczerwony\x1b[0m \u202eodwrócony\u200b tekst")
        subject = self.ahead()["commits"][0]["subject"]
        self.assertEqual(subject, "aczerwony odwrócony tekst")

    def test_a_zero_width_mark_inside_a_key_does_not_hide_it_from_the_mask(self) -> None:
        self.commit("pass\u200bword: hunter2hunter2 koniec")
        subject = self.ahead()["commits"][0]["subject"]
        self.assertNotIn("hunter2", subject)

    def test_the_hash_has_a_fixed_length_whatever_the_git_configuration(self) -> None:
        self.git("config", "core.abbrev", "12")
        self.commit("x")
        self.assertEqual(len(self.ahead()["commits"][0]["sha"]), 7)

    def test_an_empty_subject_gives_a_row_with_only_the_hash(self) -> None:
        self.git("commit", "-q", "--allow-empty", "--allow-empty-message", "-m", "")
        commit = self.ahead()["commits"][0]
        self.assertEqual(commit["subject"], "")
        self.assertRegex(commit["sha"], r"^[0-9a-f]{4,}$")

    def test_the_separator_in_a_subject_stays_in_the_subject(self) -> None:
        self.commit("przed\x1fpo")
        commit = self.ahead()["commits"][0]
        self.assertRegex(commit["sha"], r"^[0-9a-f]{4,}$")
        self.assertIn("przed", commit["subject"])

    def test_bytes_that_are_not_utf8_do_not_break_the_list(self) -> None:
        message = self.repo / "msg.bin"
        message.write_bytes(b"bajty \xff\xfe w temacie\n")
        self.git("commit", "-q", "--allow-empty", "-F", str(message))
        subject = self.ahead()["commits"][0]["subject"]
        self.assertTrue(subject.startswith("bajty"))

    def test_the_user_git_configuration_does_not_leak_into_the_list(self) -> None:
        self.git("config", "log.showSignature", "true")
        self.commit("z podpisem")
        commits = self.ahead()["commits"]
        self.assertEqual(commits[0]["subject"], "z podpisem")
        self.assertRegex(commits[0]["sha"], r"^[0-9a-f]{4,}$")

    def test_21_commits_leave_one_in_commits_more(self) -> None:
        for i in range(21):
            self.commit(f"c{i}")
        state = self.ahead()
        self.assertEqual((len(state["commits"]), state["commitsMore"]), (20, 1))

    def test_unknown_branch_has_no_list(self) -> None:
        state = engine.git_ahead(self.repo, None)
        self.assertEqual((state["ahead"], state["commits"], state["commitsMore"]), (None, None, 0))

    def test_one_git_log_call_for_the_list(self) -> None:
        self.commit("a")
        real = subprocess.run
        calls: list[list[str]] = []

        def spy(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return real(cmd, *args, **kwargs)

        with mock.patch.object(engine.subprocess, "run", spy):
            self.ahead()
        self.assertEqual(sum(1 for c in calls if "log" in c), 1)

    def test_the_list_is_kept_in_memory_until_head_changes(self) -> None:
        self.commit("a")
        board = srv.Board.__new__(srv.Board)
        board._ahead = ((), 0.0, {})
        board.root = self.repo
        calls = []
        real = engine.git_ahead

        def counting(root, branch):
            calls.append(branch)
            return real(root, branch)

        with mock.patch.object(engine, "git_ahead", counting):
            first = board.ahead("feature/x", "h1")
            again = board.ahead("feature/x", "h1")
            self.assertIs(first, again)
            self.assertEqual(len(calls), 1)
            self.commit("b")
            moved = board.ahead("feature/x", "h2")
        self.assertEqual(len(calls), 2)
        self.assertEqual([c["subject"] for c in moved["commits"]], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
