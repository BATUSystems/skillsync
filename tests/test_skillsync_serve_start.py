"""`tests/serve_start.py`: which state file counts as the file of OUR server (POSIX: the pid; Windows: port + project root + start time).
The Windows branch is forced by the `windows` parameter, so Linux and macOS exercise it too."""
import errno
import json
import os
import socket
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
import serve_start
from serve_start import ATTEMPTS, bind_free, kill_tree, lost_bind, own_state, require_isolated_state, start_serve, state_snapshot, stop_serve  # noqa: E402


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

    def test_windows_a_project_server_started_in_a_worktree_is_ours_by_the_tab_of_the_directory(self):
        """The Windows run of 1.18.0: the state file of a project server names the MAIN checkout as `root`; the worktree it was started in is only in `tabs[]`."""
        main = self.tmp / "proj-main"
        main.mkdir()
        self.write("project", root=main, pid=1)
        with mock.patch.dict(os.environ, self.env):
            file = dashboard_token.state_dir() / "project.json"
        record = json.loads(file.read_text(encoding="utf-8"))
        record["tabs"] = [{"id": "main", "path": dashboard_token.root_text(main)}, {"id": "0123456789ab", "path": dashboard_token.root_text(self.root)}]
        file.write_text(json.dumps(record), encoding="utf-8")
        self.assertTrue(self.own(True))
        record["tabs"] = [{"id": "main", "path": dashboard_token.root_text(main)}]
        file.write_text(json.dumps(record), encoding="utf-8")
        self.assertFalse(self.own(True), "a tab of ANOTHER directory does not make the server ours")

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

    def test_windows_a_leftover_of_the_same_root_and_port_that_did_not_change_is_not_ours(self):
        """SV1: the killed predecessor leaves a fresh-looking file; only a file written after `Popen` counts."""
        self.write("a")
        before = state_snapshot(self.env)
        self.assertTrue(before)
        self.assertFalse(own_state(self.env, self.proc, 5190, self.root, self.started, windows=True, before=before))
        self.assertTrue(own_state(self.env, self.proc, 5190, self.root, self.started, windows=True, before={}))
        path = next(iter(before))
        os.utime(path, ns=(before[path] + 10**9, before[path] + 10**9))   # the new server rewrites the same file
        self.assertTrue(own_state(self.env, self.proc, 5190, self.root, self.started, windows=True, before=before))


class RequireIsolatedState(unittest.TestCase):
    """180-W: on macOS the server must get its own `HOME`, on Windows its own `LOCALAPPDATA` (Linux: `XDG_DATA_HOME` is enough)."""

    def refused(self, platform, env, real_env=None):
        case = unittest.TestCase()
        with mock.patch.object(serve_start, 'sys', SimpleNamespace(platform=platform)):
            try:
                require_isolated_state(case, env, real_env)
            except AssertionError:
                return True
        return False

    def test_the_real_variable_of_the_system_is_refused_and_a_moved_one_passes(self):
        for platform, name in (('darwin', 'HOME'), ('win32', 'LOCALAPPDATA')):
            real = {name: '/real/user'}
            with self.subTest(platform=platform):
                self.assertTrue(self.refused(platform, {name: '/real/user'}, real))   # the real one
                self.assertTrue(self.refused(platform, {}, real))                      # none given: inherits the real one
                self.assertFalse(self.refused(platform, {name: '/moved/elsewhere'}, real))
        self.assertFalse(self.refused('linux', {}))                       # Linux: XDG_DATA_HOME decides, nothing to demand here

    def test_a_patched_os_environ_is_not_the_real_one(self):
        """Regression (Windows run of 1.18.0 early): a test patches `os.environ` with `home_env` and hands the same values to the server; comparing
        them with the live `os.environ` refused every such start. The real value comes from `REAL_ENV`, so the isolated values pass."""
        for platform, name in (('darwin', 'HOME'), ('win32', 'LOCALAPPDATA')):
            with self.subTest(platform=platform), mock.patch.dict(os.environ, {name: '/isolated/by/the/test'}):
                self.assertFalse(self.refused(platform, {name: '/isolated/by/the/test'}, {name: '/real/user'}))
                self.assertFalse(self.refused(platform, dict(os.environ), {name: '/real/user'}))

    def test_the_default_reads_the_values_of_the_import_not_the_live_environment(self):
        """The default path (`real_env=None`): the isolated values of a patched `os.environ` pass, the values of `REAL_ENV` are refused."""
        for platform, name in (('darwin', 'HOME'), ('win32', 'LOCALAPPDATA')):
            with self.subTest(platform=platform), mock.patch.dict(os.environ, {name: '/isolated'}):
                self.assertFalse(self.refused(platform, {name: '/isolated'}))
                with mock.patch.object(serve_start, 'REAL_ENV', {name: '/isolated'}):
                    self.assertTrue(self.refused(platform, {name: '/isolated'}))

    def test_the_real_values_are_taken_at_import_and_follow_no_patch(self):
        with mock.patch.dict(os.environ, {'HOME': '/patched', 'LOCALAPPDATA': '/patched'}):
            self.assertNotEqual(serve_start.REAL_ENV.get('HOME'), '/patched')
            self.assertNotEqual(serve_start.REAL_ENV.get('LOCALAPPDATA'), '/patched')


class KillTree(unittest.TestCase):
    def proc(self, alive=True):
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None if alive else 0
        return proc

    def test_windows_kills_the_tree_of_our_own_pid_by_the_system_path(self):
        proc = self.proc()
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}), mock.patch("serve_start.subprocess.run") as run:
            kill_tree(proc, windows=True)
        command = run.call_args.args[0]
        self.assertFalse(run.call_args.kwargs.get("shell"), "a list of arguments, no shell")
        self.assertEqual(command[1:], ["/T", "/F", "/PID", "4242"])
        self.assertTrue(command[0].lower().endswith("taskkill.exe") and "system32" in command[0].lower(), command)
        proc.kill.assert_called_once()   # still alive in the mock: the launcher is killed too

    def test_windows_falls_back_to_kill_when_taskkill_is_missing(self):
        proc = self.proc()
        with mock.patch("serve_start.subprocess.run", side_effect=FileNotFoundError):
            kill_tree(proc, windows=True)
        proc.kill.assert_called_once()

    def test_posix_does_not_call_taskkill(self):
        proc = self.proc()
        with mock.patch("serve_start.subprocess.run") as run:
            kill_tree(proc, windows=False)
        run.assert_not_called()
        proc.kill.assert_called_once()

    def test_a_finished_process_is_left_alone(self):
        proc = self.proc(alive=False)
        with mock.patch("serve_start.subprocess.run") as run:
            kill_tree(proc, windows=True)
        run.assert_not_called()
        proc.kill.assert_not_called()


class StartServeAttempts(unittest.TestCase):
    """`start_serve` against a fake CLI that always ends with the given output, to count the tries."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serve-start-attempts-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.counter = self.tmp / "tries.txt"
        self.env = {**os.environ, "XDG_DATA_HOME": str(self.tmp / "xdg-data"), "LOCALAPPDATA": str(self.tmp / "xdg-data"),
                    "HOME": str(self.tmp / "home"), "USERPROFILE": str(self.tmp / "home"), "PYTHONUTF8": "1"}
        self.root = self.tmp / "proj"
        self.root.mkdir()

    def cli(self, reason):
        path = self.tmp / "fake_cli.py"
        path.write_text("import json, sys\nopen(%r, 'a').write('x')\nprint(json.dumps({'status': 'BLOCKED', 'reason': %r}, ensure_ascii=False))\n"
                        "sys.exit(2)\n" % (str(self.counter), reason), encoding="utf-8")
        return path

    def test_a_lost_bind_is_retried_until_the_attempts_run_out(self):
        with self.assertRaises(AssertionError) as raised:
            start_serve(self, self.cli("nie można nasłuchiwać na 127.0.0.1:5190 (Address already in use)"), self.root, [], self.env, lambda: 5190)
        self.assertEqual(len(self.counter.read_text()), ATTEMPTS)
        self.assertIn("did not start", str(raised.exception))

    def test_a_configuration_error_is_not_retried(self):
        with self.assertRaises(AssertionError):
            start_serve(self, self.cli("--plan: nieznany plik"), self.root, [], self.env, lambda: 5190)
        self.assertEqual(len(self.counter.read_text()), 1)


class StopServe(unittest.TestCase):
    def proc(self):
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        proc.communicate.return_value = ("out", "err")
        return proc

    def test_posix_terminates_and_the_stop_is_graceful(self):
        proc = self.proc()
        self.assertEqual(stop_serve(proc, windows=False), ("out", "err", True))
        proc.terminate.assert_called_once()

    def test_windows_kills_the_tree_and_the_stop_is_not_graceful(self):
        proc = self.proc()
        with mock.patch("serve_start.kill_tree") as kill:
            self.assertEqual(stop_serve(proc, windows=True), ("out", "err", False))
        kill.assert_called_once_with(proc, windows=True)
        proc.terminate.assert_not_called()


class LostBind(unittest.TestCase):
    def test_only_a_blocked_listen_is_retried(self):
        self.assertTrue(lost_bind('{"status": "BLOCKED", "reason": "nie można nasłuchiwać na 127.0.0.1:5190 (Address already in use)"}'))
        self.assertFalse(lost_bind('{"status": "BLOCKED", "reason": "--plan: nieznany plik"}'))   # SV4: a configuration error is not retried
        self.assertFalse(lost_bind("Traceback"))


class BindFree(unittest.TestCase):
    def test_a_port_taken_before_the_bind_moves_on_to_the_next_one(self):
        ports = iter([5190, 5191, 5192])
        tried = []

        def factory(port):
            tried.append(port)
            if port != 5192:
                raise OSError(errno.EADDRINUSE, "Address already in use")
            return f"server-{port}"

        self.assertEqual(bind_free(factory, lambda: next(ports)), ("server-5192", 5192))
        self.assertEqual(tried, [5190, 5191, 5192])

    def test_the_windows_code_of_a_taken_port_is_retried_too(self):
        ports = iter([5190, 5191])

        def factory(port):
            if port == 5190:
                raise OSError(10048, "WSAEADDRINUSE")
            return "ok"

        self.assertEqual(bind_free(factory, lambda: next(ports)), ("ok", 5191))

    def test_another_error_is_raised_at_once(self):
        calls = []

        def factory(port):
            calls.append(port)
            raise OSError(errno.EACCES, "Permission denied")

        with self.assertRaises(OSError):
            bind_free(factory, lambda: 5190)
        self.assertEqual(calls, [5190])

    def test_the_last_error_is_raised_after_the_attempts_run_out(self):
        calls = []

        def factory(port):
            calls.append(port)
            raise OSError(errno.EADDRINUSE, "Address already in use")

        with self.assertRaises(OSError) as raised:
            bind_free(factory, lambda: 5190, attempts=3)
        self.assertEqual(raised.exception.errno, errno.EADDRINUSE)
        self.assertEqual(len(calls), 3)
        self.assertIn("3 tries", str(raised.exception))
        self.assertIn("5190", str(raised.exception))

    def test_no_attempt_at_all_is_a_usage_error(self):
        with self.assertRaises(ValueError):
            bind_free(lambda port: "never", lambda: 5190, attempts=0)

    def test_a_really_occupied_port_loses_against_the_retry(self):
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            taken = holder.getsockname()[1]
            ports = iter([taken])

            def free():
                return next(ports, None) or self.free_other(taken)

            def factory(port):
                sock = socket.socket()
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    sock.close()
                    raise
                return sock

            sock, port = bind_free(factory, free)
            sock.close()
            self.assertNotEqual(port, taken)

    @staticmethod
    def free_other(taken):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]


if __name__ == "__main__":
    unittest.main()
