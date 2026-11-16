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


if __name__ == "__main__":
    unittest.main()
