"""180-M: the guard of the real per-user state directories covers EVERY system, and `home_env` moves the state of every system under a base."""
from __future__ import annotations

import json
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


# Whole seconds: the modification time of Windows has a resolution of 100 ns, so 1 ns and 5 ns are the same time there.
SECOND = 10 ** 9


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
        os.utime(watched / "kept.json", ns=(1 * SECOND, 1 * SECOND))

        def change() -> None:
            (watched / "kept.json").write_text("{\"a\": 1}", encoding="utf-8")
            os.utime(watched / "kept.json", ns=(2 * SECOND, 2 * SECOND))
            (watched / "new.json").write_text("{}", encoding="utf-8")
            (watched / "gone.json").unlink()

        lines = self.difference(watched, change)
        text = "\n".join(lines)
        self.assertEqual(len(lines), 3, lines)
        self.assertIn(f"kept.json: file 2 B, mtime {SECOND} -> file 8 B, mtime {2 * SECOND}", text)
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
        os.utime(watched / "clone" / ".git" / "objects", ns=(1 * SECOND, 1 * SECOND))
        self.assertEqual(self.difference(watched, lambda: os.utime(watched / "clone" / ".git" / "objects", ns=(2 * SECOND, 2 * SECOND))), [])

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


class CompositeCacheNoise(unittest.TestCase):
    """K2: the `link` of ANOTHER session (a new mtime of the `composed` directory, a rewritten registry of its own projects) is not a
    leak of the test; every file, a new or removed directory and a registry entry of a project below the temporary directory still are."""

    LAYOUTS = (("composed",), ("cache", "composed"))   # Linux and macOS, Windows

    def watched(self, layout=("composed",)) -> tuple:
        tmp = Path(tempfile.mkdtemp(prefix="state-guard-noise-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        watched = tmp / "skillsync"
        composed = watched.joinpath(*layout)
        (composed / "feature" / "abc123").mkdir(parents=True)
        (composed / "feature" / "abc123" / "SKILL.md").write_text("x", encoding="utf-8")
        return watched, composed

    def registry(self, composed: Path, entries: dict) -> None:
        (composed / ".projects.json").write_text(json.dumps(entries), encoding="utf-8")

    def difference(self, watched: Path, change) -> list:
        with mock.patch.object(helpers, "REAL_STATE_DIRS", (watched,)):
            before = helpers.real_state_snapshot()
            change()
            return helpers.real_state_difference(before, helpers.real_state_snapshot())

    def test_a_new_mtime_and_size_of_the_composed_directories_are_not_a_difference(self) -> None:
        for layout in self.LAYOUTS:
            watched, composed = self.watched(layout)
            os.utime(composed, ns=(1 * SECOND, 1 * SECOND))
            os.utime(composed / "feature", ns=(1 * SECOND, 1 * SECOND))
            with self.subTest(layout="/".join(layout)):
                def change() -> None:
                    os.utime(composed, ns=(5 * SECOND, 5 * SECOND))
                    os.utime(composed / "feature", ns=(5 * SECOND, 5 * SECOND))
                    os.utime(composed / "feature" / "abc123", ns=(5 * SECOND, 5 * SECOND))
                self.assertEqual(self.difference(watched, change), [])

    def test_a_new_removed_directory_and_a_file_change_in_the_composed_cache_are_still_seen(self) -> None:
        for layout in self.LAYOUTS:
            for name, change in (
                ("new directory", lambda c: (c / "feature" / "fresh").mkdir()),
                ("removed directory", lambda c: __import__("shutil").rmtree(c / "feature" / "abc123")),
                ("removed empty directory", lambda c: (c / "empty").rmdir()),
                ("new file", lambda c: (c / "feature" / "abc123" / "new.md").write_text("x", encoding="utf-8")),
                ("changed file", lambda c: (c / "feature" / "abc123" / "SKILL.md").write_text("changed", encoding="utf-8")),
            ):
                watched, composed = self.watched(layout)
                (composed / "empty").mkdir()
                with self.subTest(layout="/".join(layout), change=name):
                    self.assertNotEqual(self.difference(watched, lambda: change(composed)), [])

    @helpers.needs_os_symlinks
    def test_a_replaced_symlink_to_a_directory_in_the_composed_cache_is_seen(self) -> None:
        watched, composed = self.watched()
        target_one, target_two = composed / "feature" / "abc123", composed / "feature"
        (composed / "link").symlink_to(target_one, target_is_directory=True)

        def change() -> None:
            (composed / "link").unlink()
            (composed / "link").symlink_to(target_two, target_is_directory=True)
        lines = self.difference(watched, change)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(f"link -> {target_one}", lines[0])   # the target is compared too: size and mtime of a replaced link can be equal
        self.assertIn(f"link -> {target_two}", lines[0])

    def test_a_directory_called_composed_elsewhere_keeps_its_metadata_compared(self) -> None:
        watched, composed = self.watched()
        deep = watched / "layers" / "batu" / "composed"
        deep.mkdir(parents=True)
        os.utime(deep, ns=(1 * SECOND, 1 * SECOND))
        self.assertNotEqual(self.difference(watched, lambda: os.utime(deep, ns=(5 * SECOND, 5 * SECOND))), [])

    def test_a_transient_file_in_a_directory_outside_the_composed_cache_is_seen(self) -> None:
        watched, _ = self.watched()
        (watched / "dashboard").mkdir()

        def change() -> None:
            (watched / "dashboard" / "state.json.tmp").write_text("{}", encoding="utf-8")
            (watched / "dashboard" / "state.json.tmp").unlink()
            os.utime(watched / "dashboard", ns=(7 * SECOND, 7 * SECOND))
        self.assertNotEqual(self.difference(watched, change), [])

    def test_the_registry_is_compared_only_for_projects_below_the_temporary_directory(self) -> None:
        for layout in self.LAYOUTS:
            watched, composed = self.watched(layout)
            temporary = Path(tempfile.gettempdir()) / "state-guard-project-x"
            self.registry(composed, {"/srv/work/real": {"feature": "aaa"}})
            with self.subTest(layout="/".join(layout)):
                outside = lambda: self.registry(composed, {"/srv/work/real": {"feature": "bbb"}, "/srv/other/y": {"feature": "c"}})
                self.assertEqual(self.difference(watched, outside), [])
                inside = lambda: self.registry(composed, {str(temporary): {"feature": "bbb"}})
                lines = self.difference(watched, inside)
                self.assertEqual(len(lines), 1, lines)
                self.assertIn(f".projects.json[{temporary}]: brak ->", lines[0])
                self.assertIn('"feature": "bbb"', lines[0])

    def test_an_atomic_rewrite_of_the_registry_without_a_change_is_not_a_difference(self) -> None:
        watched, composed = self.watched()
        temporary = str(Path(tempfile.gettempdir()) / "state-guard-project-y")
        self.registry(composed, {temporary: {"feature": "aaa"}, "/srv/work/real": {"feature": "b"}})

        def change() -> None:
            rewritten = composed / ".projects.json.tmp-123"
            rewritten.write_text(json.dumps({temporary: {"feature": "aaa"}, "/srv/work/real": {"feature": "c"}}), encoding="utf-8")
            os.replace(rewritten, composed / ".projects.json")
        self.assertEqual(self.difference(watched, change), [])

    def test_the_temporary_file_of_the_registry_is_ignored(self) -> None:
        watched, composed = self.watched()
        self.assertEqual(self.difference(watched, lambda: (composed / ".projects.json.tmp-9").write_text("{", encoding="utf-8")), [])

    def test_a_damaged_or_missing_registry_counts_as_empty(self) -> None:
        watched, composed = self.watched()
        temporary = str(Path(tempfile.gettempdir()) / "state-guard-project-z")
        self.registry(composed, {temporary: {"feature": "a"}})
        damaged = lambda: (composed / ".projects.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(len(self.difference(watched, damaged)), 1)   # the entry disappeared
        self.assertEqual(self.difference(watched, lambda: (composed / ".projects.json").write_text("[1]", encoding="utf-8")), [])
        self.assertEqual(self.difference(watched, lambda: (composed / ".projects.json").unlink()), [])

    def test_a_changed_value_of_a_tmp_project_and_odd_registry_values_are_handled(self) -> None:
        watched, composed = self.watched()
        temporary = str(Path(tempfile.gettempdir()) / "state-guard-project-v")
        self.registry(composed, {temporary: {"feature": "a"}})
        self.assertEqual(len(self.difference(watched, lambda: self.registry(composed, {temporary: {"feature": "b"}}))), 1)
        self.assertEqual(len(self.difference(watched, lambda: self.registry(composed, {temporary: "text"}))), 1)
        lines = self.difference(watched, lambda: self.registry(composed, {"bad\0key": 1}))   # a key that cannot be resolved: no crash
        self.assertEqual(len(lines), 1, lines)   # only the entry of the tmp project that disappeared

    @helpers.needs_os_symlinks
    def test_a_temporary_directory_that_is_a_symlink_is_recognised_by_the_resolved_path(self) -> None:
        watched, composed = self.watched()
        real = Path(tempfile.mkdtemp(prefix="state-guard-real-"))
        self.addCleanup(__import__("shutil").rmtree, real, ignore_errors=True)
        link = Path(tempfile.mkdtemp(prefix="state-guard-link-")) / "tmp"
        self.addCleanup(__import__("shutil").rmtree, link.parent, ignore_errors=True)
        link.symlink_to(real, target_is_directory=True)
        (real / "project").mkdir()
        with mock.patch.object(helpers, "_TMP_ROOT", real.resolve()):
            self.registry(composed, {})
            lines = self.difference(watched, lambda: self.registry(composed, {str(link / "project"): {"feature": "a"}}))
        self.assertEqual(len(lines), 1, lines)

    def test_the_registry_is_recognised_without_the_environment_of_the_process(self) -> None:
        """The cleanup of `IsolatedTestCase` runs under a substituted environment (`TMPDIR`, `HOME`): the registry is found by its place
        below `REAL_STATE_DIRS`, not through `composed_root()`, (the temporary directory is taken once at import, see the symlink test)."""
        watched, composed = self.watched()
        temporary = str(Path(tempfile.gettempdir()) / "state-guard-project-w")
        self.registry(composed, {})
        substituted = {"TMPDIR": "/nonexistent-tmp", "TEMP": "/nonexistent-tmp", "TMP": "/nonexistent-tmp", "HOME": "/nonexistent-home",
                       "XDG_CACHE_HOME": "/nonexistent-cache", "LOCALAPPDATA": "/nonexistent-local"}
        with mock.patch.dict(os.environ, substituted):
            lines = self.difference(watched, lambda: self.registry(composed, {temporary: {"feature": "a"}}))
        self.assertEqual(len(lines), 1, lines)


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
