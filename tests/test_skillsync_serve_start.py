"""`tests/serve_start.py`: which state file counts as the file of OUR server (POSIX: the pid; Windows: port + project root + start time).
The Windows branch is forced by the `windows` parameter, so Linux and macOS exercise it too."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_token  # noqa: E402
from serve_start import own_state  # noqa: E402


class OwnState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serve-start-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.env = {"XDG_DATA_HOME": str(self.tmp / "xdg-data"), "LOCALAPPDATA": str(self.tmp / "xdg-data"),
                    "HOME": str(self.tmp / "home"), "USERPROFILE": str(self.tmp / "home")}
        self.root = self.tmp / "proj-ours"
        self.root.mkdir()
        self.proc = SimpleNamespace(pid=4242)
        self.started = datetime.now(timezone.utc)

    def write(self, name, root=None, pid=1, port=5190, at=None):
        with mock.patch.dict(os.environ, self.env):
            directory = dashboard_token.state_dir()
        directory.mkdir(parents=True, exist_ok=True)
        record = {"schema": 1, "pid": pid, "port": port, "root": dashboard_token.root_text(root or self.root),
                  "startedAt": (at or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
        (directory / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")

    def own(self, windows, port=5190):
        return own_state(self.env, self.proc, port, self.root, self.started, windows=windows)

    def test_posix_takes_only_the_pid_of_the_process(self):
        self.write("a", pid=1)
        self.assertFalse(self.own(False), "the same root and port with another pid is not ours on POSIX")
        self.write("b", pid=4242)
        self.assertTrue(self.own(False))

    def test_windows_takes_the_port_the_root_and_a_fresh_start(self):
        self.write("a", pid=1)   # the pid is the venv launcher's, not ours
        self.assertTrue(self.own(True))

    def test_windows_a_foreign_server_on_the_same_port_with_another_root_is_not_ours(self):
        other = self.tmp / "proj-foreign"
        other.mkdir()
        self.write("foreign", root=other, pid=4242)   # even with our pid: the root decides
        self.assertFalse(self.own(True))

    def test_windows_a_file_older_than_our_start_is_not_ours(self):
        self.write("stale", at=self.started - timedelta(seconds=30))
        self.assertFalse(self.own(True))

    def test_windows_another_port_is_not_ours(self):
        self.write("other-port", port=5191)
        self.assertFalse(self.own(True))

    def test_windows_without_a_root_or_a_start_time_nothing_is_ours(self):
        self.write("a")
        self.assertFalse(own_state(self.env, self.proc, 5190, None, self.started, windows=True))
        self.assertFalse(own_state(self.env, self.proc, 5190, self.root, None, windows=True))


if __name__ == "__main__":
    unittest.main()
