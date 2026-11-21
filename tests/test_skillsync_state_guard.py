"""180-M: the guard of the real per-user state directories covers EVERY system, and `home_env` moves the state of every system under a base."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import layers_helpers as helpers  # noqa: E402
from skillsync_workflow import layers  # noqa: E402


class RealStateDirs(unittest.TestCase):
    def test_every_system_data_and_cache_directory_of_the_real_home_is_watched(self) -> None:
        home = helpers.REAL_HOME
        for system in ("Linux", "Darwin", "Windows"):
            with self.subTest(system=system), mock.patch.dict(os.environ, {"XDG_DATA_HOME": "", "XDG_CACHE_HOME": "", "LOCALAPPDATA": ""}):
                for directory in (layers.data_home(system), layers.cache_home(system)):
                    self.assertTrue(any(directory == watched or watched in directory.parents for watched in helpers.REAL_STATE_DIRS),
                                    f"{system}: {directory} (of the real home {home}) is not under a watched directory")

    def test_home_env_moves_the_state_of_every_system_under_the_base(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="state-guard-"))
        self.addCleanup(__import__("shutil").rmtree, base, ignore_errors=True)
        with mock.patch.dict(os.environ, helpers.home_env(base)):
            for system in ("Linux", "Darwin", "Windows"):
                for directory in (layers.data_home(system), layers.cache_home(system)):
                    with self.subTest(system=system, directory=str(directory)):
                        self.assertIn(base, directory.parents)

    def test_a_write_to_a_watched_directory_of_any_system_fails_the_guard(self) -> None:
        for name in (".cache", ".local/share", "Library/Caches", "Library/Application Support", "AppData/Local"):
            tmp = Path(tempfile.mkdtemp(prefix="state-guard-real-"))
            self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
            watched = tmp / name / "skillsync"
            watched.mkdir(parents=True)
            with self.subTest(directory=name), mock.patch.object(helpers, "REAL_STATE_DIRS", (watched,)):
                before = helpers.real_state_snapshot()
                (watched / "dashboard-state.json").write_text("{}", encoding="utf-8")
                self.assertNotEqual(helpers.real_state_snapshot(), before)


class GuardRealState(unittest.TestCase):
    def test_the_guard_fails_a_case_that_writes_to_a_watched_directory_and_passes_one_that_does_not(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="state-guard-case-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        watched = tmp / "skillsync"   # absent at first: the guard also sees a directory that appears
        for writes, expect_failure in ((True, True), (False, False)):
            with self.subTest(writes=writes), mock.patch.object(helpers, "REAL_STATE_DIRS", (watched,)):
                class Case(unittest.TestCase):
                    def runTest(self) -> None:
                        helpers.guard_real_state(self)
                        if writes:
                            watched.mkdir(exist_ok=True)
                            (watched / "x.json").write_text("{}", encoding="utf-8")
                case = Case()
                result = unittest.TestResult()
                case.run(result)
                self.assertEqual(bool(result.failures or result.errors), expect_failure, (result.failures, result.errors))
                if watched.exists():
                    __import__("shutil").rmtree(watched)


class StateDifference(unittest.TestCase):
    """K1: a failure names the changed paths (before -> after) instead of dumping two snapshots."""

    def watched(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="state-guard-diff-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        watched = tmp / "skillsync"
        watched.mkdir()
        return watched

    def difference(self, watched: Path, change) -> list:
        with mock.patch.object(helpers, "REAL_STATE_DIRS", (watched,)):
            before = helpers.real_state_snapshot()
            change()
            return helpers.real_state_difference(before, helpers.real_state_snapshot())

    def test_equal_snapshots_have_no_difference(self) -> None:
        watched = self.watched()
        (watched / "a.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.difference(watched, lambda: None), [])

    def test_a_replaced_new_and_removed_file_are_named_with_before_and_after(self) -> None:
        watched = self.watched()
        (watched / "kept.json").write_text("{}", encoding="utf-8")
        (watched / "gone.json").write_text("{}", encoding="utf-8")
        os.utime(watched / "kept.json", ns=(1, 1))

        def change() -> None:
            (watched / "kept.json").write_text("{\"a\": 1}", encoding="utf-8")
            os.utime(watched / "kept.json", ns=(2, 2))
            (watched / "new.json").write_text("{}", encoding="utf-8")
            (watched / "gone.json").unlink()

        lines = self.difference(watched, change)
        text = "\n".join(lines)
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("kept.json: file 2 B, mtime 1 -> file 8 B, mtime 2", text)
        self.assertIn("new.json: brak -> file 2 B", text)
        self.assertTrue(any("gone.json: file 2 B, mtime " in line and line.endswith("-> brak") for line in lines), lines)

    def test_a_new_and_a_removed_directory_are_named(self) -> None:
        watched = self.watched()
        (watched / "old-dir").mkdir()
        lines = self.difference(watched, lambda: ((watched / "old-dir").rmdir(), (watched / "new-dir").mkdir()))
        text = "\n".join(lines)
        self.assertIn("new-dir: brak -> dir", text)
        self.assertIn("old-dir: dir", text)

    def test_a_directory_that_appears_or_disappears_is_one_line(self) -> None:
        missing = Path(tempfile.mkdtemp(prefix="state-guard-diff-")) / "skillsync"
        self.addCleanup(__import__("shutil").rmtree, missing.parent, ignore_errors=True)
        lines = self.difference(missing, lambda: missing.mkdir())
        self.assertEqual(lines, [f"{missing}: brak -> katalog"])

    def test_more_than_ten_changes_are_cut_with_a_count(self) -> None:
        watched = self.watched()

        def change() -> None:
            for number in range(13):
                (watched / f"f{number:02d}.json").write_text("{}", encoding="utf-8")

        lines = self.difference(watched, change)
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[-1], "i 3 innych")

    def test_the_cut_is_exact_at_ten_and_eleven_changes(self) -> None:
        for count, expected in ((10, 10), (11, 11)):
            watched = self.watched()
            with self.subTest(count=count):
                lines = self.difference(watched, lambda: [(watched / f"f{n:02d}.json").write_text("{}", encoding="utf-8") for n in range(count)])
                self.assertEqual(len(lines), expected)
                self.assertEqual(lines[-1].startswith("i "), count == 11, lines)
                if count == 11:
                    self.assertEqual(lines[-1], "i 1 innych")

    def test_a_changed_mtime_of_a_directory_inside_git_is_not_a_difference(self) -> None:
        watched = self.watched()
        (watched / "clone" / ".git" / "objects").mkdir(parents=True)
        os.utime(watched / "clone" / ".git" / "objects", ns=(1, 1))
        self.assertEqual(self.difference(watched, lambda: os.utime(watched / "clone" / ".git" / "objects", ns=(2, 2))), [])

    def test_the_failure_message_of_the_guard_lists_the_paths(self) -> None:
        watched = self.watched()
        with mock.patch.object(helpers, "REAL_STATE_DIRS", (watched,)):
            class Case(unittest.TestCase):
                def runTest(self) -> None:
                    helpers.guard_real_state(self)
                    (watched / "leak.json").write_text("{}", encoding="utf-8")
            result = unittest.TestResult()
            Case().run(result)
        self.assertEqual(len(result.failures), 1, (result.failures, result.errors))
        message = result.failures[0][1]
        self.assertIn("test touched real Skillsync state dirs", message)
        self.assertIn("leak.json: brak -> file 2 B", message)


class EveryEnvironmentMovesTheStateOfEverySystem(unittest.TestCase):
    """180-W: a test module that builds its own environment with `XDG_DATA_HOME`/`XDG_CACHE_HOME` isolates only Linux (macOS keeps the data under
    `~/Library` of `HOME`, Windows under `LOCALAPPDATA`): such a module must also use `home_env` or name `LOCALAPPDATA` and `HOME`. The runs on Linux cannot
    see the difference, so this static check is the net for the modules that were added after the guard."""

    def test_modules_with_xdg_isolation_also_move_the_other_systems(self) -> None:
        import re
        problems = []
        for path in sorted((ROOT / "tests").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name in ("layers_helpers.py", "test_skillsync_state_guard.py") or not re.search(r"XDG_(DATA|CACHE)_HOME", text):
                continue
            if "home_env(" in text or "isolated_environment(" in text or "isolated_paths(" in text:
                continue
            if "LOCALAPPDATA" in text and re.search(r"""["']HOME["']""", text):
                continue
            problems.append(path.name)
        self.assertEqual(problems, [], "these modules isolate only XDG: use `home_env` (tests/layers_helpers.py)")


if __name__ == "__main__":
    unittest.main()
