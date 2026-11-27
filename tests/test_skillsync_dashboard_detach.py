"""`skillsync dashboard serve --detach` and `skillsync dashboard stop` (CORE-2000-UNIFIED, U-6a), plus the regression of `approvals` in `servers`.

Two groups. The unit tests replace only the process of the server (a fake `Popen` that does what a server does: writes its state file) and the answer
of `/healthz`; the state files, the logs and the parsers are the real ones. The end-to-end tests run REAL processes through a launcher file named
`skillsync` in the temporary directory (so `servers.identity_matches` sees the same tokens as for an installed command). Every start names a test port
(`--port`); the launcher also moves the default port to a test port, so no failure can ever take 5140, and refuses a third generation of processes
(a server that detaches again). State is isolated (`layers_helpers.isolated_paths`); processes are ended by PID, only after their command line proved
that they belong to the test.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from layers_helpers import GIT_IDENTITY_VARS, guard_real_state, isolated_environment, isolated_paths  # noqa: E402
from serve_start import require_isolated_state, state_files  # noqa: E402
from tmp_paths import descend  # noqa: E402

from skillsync_workflow import cli, dashboard, dashboard_detach as detach, dashboard_http, dashboard_ports, dashboard_token, servers  # noqa: E402

PORTS_ENV = "SKILLSYNC_TEST_DASHBOARD_PORTS"
DEFAULT_PORTS = "5170-5189"
FORBIDDEN_PORTS = (5140, 5141, 5142)
WINDOWS = os.name == "nt"
POSIX_MODES = "prawa 0600/0700 plików to uprawnienia POSIX; na Windows dostęp ogranicza ACL (winacl), nie tryb pliku"
POSIX_LINKS = "dowiązania symboliczne i O_NOFOLLOW: na Windows utworzenie dowiązania wymaga osobnego uprawnienia"
POSIX_SIGINT = "Ctrl+C jako SIGINT do jednego procesu to sygnał POSIX; na Windows zdarzenie konsoli trafia do całej grupy, także do biegu testów"

LAUNCHER = '''import os
import signal
import sys
import time
sys.path.insert(0, {root!r})
if os.name != "nt":
    signal.signal(signal.SIGINT, signal.default_int_handler)   # a test run started with `&` hands SIGINT down as ignored
depth = int(os.environ.get("SKILLSYNC_TEST_DETACH_DEPTH", "0")) + 1
os.environ["SKILLSYNC_TEST_DETACH_DEPTH"] = str(depth)
if depth > 2:   # the command and its server: a server that starts a server again is stopped here
    sys.exit("test launcher: a third generation of processes was refused")
if "serve" in sys.argv:
    with open(os.path.join({pids!r}, str(os.getpid())), "w", encoding="utf-8") as handle:
        handle.write(" ".join(sys.argv))
if depth == 2:
    try:
        os.rename({hang!r}, {hung!r})   # one server only takes the marker
    except OSError:
        pass
    else:
        with open({hung!r}, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        print("test launcher: this server hangs before it listens", flush=True)
        time.sleep(120)
        sys.exit(9)
from skillsync_workflow import dashboard_ports
dashboard_ports.DEFAULT_READ_PORT = int(os.environ["SKILLSYNC_TEST_PAGE_PORT"])
from skillsync_workflow.cli import run
run()
'''


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def parse(*argv: str) -> argparse.Namespace:
    """The arguments of `skillsync dashboard ...` through the real registration of the `dashboard` parser."""
    parser = argparse.ArgumentParser(prog="skillsync")
    dashboard.register(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(["dashboard", *argv])


def free_port(skip=()) -> int:
    raw = os.environ.get(PORTS_ENV) or DEFAULT_PORTS
    low, _, high = raw.partition("-")
    first, last = int(low), int(high or low)
    if not 5100 <= first <= last <= 5199:
        raise ValueError(f"{PORTS_ENV}={raw!r}: expected <from>-<to> within 5100-5199")
    for port in range(first, last + 1):
        if port in FORBIDDEN_PORTS or port in skip:
            continue
        with socket.socket() as probe:
            if not WINDOWS:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free port in the test pool {first}-{last}")


class FakeChild:
    """The `Popen` object of a server: `code` None = running. `stubborn`: the gentle stop does not end it."""

    def __init__(self, pid: int = 999999, code=None, stubborn: bool = False) -> None:
        self.pid, self.code, self.stubborn, self.killed, self.waits = pid, code, stubborn, False, []

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.killed:
            self.code = -9
        elif self.code is None:
            if self.stubborn:
                raise subprocess.TimeoutExpired("server", timeout)
            self.code = 0
        return self.code

    def kill(self):
        self.killed = True


class FakeDeps:
    def __init__(self) -> None:
        self.terminated = []

    def terminate(self, pid):
        self.terminated.append(pid)
        return True


class MachineCase(unittest.TestCase):
    """An isolated machine and one temporary project (no `.git`: a standalone server) in the test process."""

    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="detach-unit-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        machine = self.tmp / "machine"
        machine.mkdir()
        self.enterContext(isolated_environment(machine))
        self.project = self.tmp / "proj"
        write(self.project / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")

    def server_wrote_state(self, port: int, pid: int | None = None, root: Path | None = None, **kwargs) -> Path:
        """What a server does once it listens: its state file (`pid`: of another living process when given)."""
        kwargs.setdefault("server_id", dashboard_token.new_server_id())
        path = dashboard_token.write_state(root or self.project, port, True, role="page", **kwargs)
        self.assertIsNotNone(path)
        if pid is not None:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["pid"] = pid
            path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def run_detached(self, *extra: str, popen, timeout: float = 5.0, path: str | None = None):
        argv = ["dashboard", "serve", path or str(self.project), *extra, "--detach"]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = detach.serve_detached(parse(*argv[1:]), argv=[str(ROOT / "skillsync"), *argv], popen=popen, timeout=timeout)
        return code, out.getvalue(), err.getvalue()

    def logs(self) -> list:
        directory = dashboard_token.state_dir()
        return sorted(directory.glob("*.log")) if directory.is_dir() else []


class ChildCommandTests(unittest.TestCase):
    def test_the_flag_and_its_abbreviations_are_removed_and_everything_else_stays_in_order(self):
        script = str(ROOT / "skillsync")
        for flag in ("--detach", "--detac", "--deta", "--det", "--de"):
            with self.subTest(flag=flag):
                command = detach.child_command([script, "dashboard", "serve", "rel/dir", flag, "--port", "5170", "--json"], executable="PY")
                self.assertEqual(command, ["PY", script, "dashboard", "serve", "rel/dir", "--port", "5170", "--json"])

    def test_no_spelling_that_the_real_parser_takes_as_detach_makes_the_server_detach_again(self):
        """Whatever `skillsync` accepts as `--detach` must be gone from the command of the server: a server that detaches starts a server that
        detaches ... without end. The real `cli.main` parses both command lines; only the two commands behind the parser are replaced."""
        with tempfile.TemporaryDirectory() as machine, isolated_environment(machine):
            for end in range(len("--d"), len("--detach") + 1):
                flag = "--detach"[:end]
                with self.subTest(flag=flag):
                    argv = [str(ROOT / "skillsync"), "dashboard", "serve", "somewhere", flag]
                    calls = []
                    with mock.patch.object(detach, "serve_detached", lambda args: calls.append("detach") or 0), \
                            mock.patch.object(dashboard_http, "serve_command", lambda args: calls.append("foreground") or 0), \
                            contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                        with mock.patch.object(sys, "argv", argv):
                            cli.main()
                        self.assertEqual(calls, ["detach"], "the parser does not take this spelling as --detach")
                        calls.clear()
                        with mock.patch.object(sys, "argv", detach.child_command(argv)[1:]):
                            cli.main()
                    self.assertEqual(calls, ["foreground"])

    def test_a_windows_launcher_runs_directly_also_when_argv_lacks_the_suffix(self):
        with tempfile.TemporaryDirectory() as temp:
            launcher = write(Path(temp) / "skillsync.exe", "")
            for entry in (str(launcher), str(Path(temp) / "skillsync")):
                with self.subTest(entry=entry):
                    command = detach.child_command([entry, "dashboard", "serve", "--detach"], executable="PY")
                    self.assertEqual(command, [str(launcher), "dashboard", "serve"])

    def test_a_script_runs_through_this_interpreter_and_an_exe_beside_it_does_not_replace_it(self):
        with tempfile.TemporaryDirectory() as temp:
            script = write(Path(temp) / "skillsync", "")
            write(Path(temp) / "skillsync.exe", "")
            self.assertEqual(detach.child_command([str(script), "dashboard", "serve", "--detach"]), [sys.executable, str(script), "dashboard", "serve"])

    def test_the_command_of_the_server_is_recognised_as_the_server_by_servers_stop(self):
        started = {"started": 100.0}
        script = detach.child_command([str(ROOT / "skillsync"), "dashboard", "serve", ".", "--detach"], executable="/usr/bin/python3")
        with mock.patch.object(servers, "_windows", lambda: False):
            self.assertTrue(servers.identity_matches("dashboard", {**started, "cmdline": " ".join(script).replace("\\", "/")}, 101.0))
        exe = detach.child_command([r"C:\tools\bin\skillsync.exe", "dashboard", "serve", ".", "--detach"])
        with mock.patch.object(servers, "_windows", lambda: True):
            self.assertTrue(servers.identity_matches("dashboard", {**started, "cmdline": " ".join(exe)}, 101.0))
            self.assertFalse(servers.identity_matches("prototype", {**started, "cmdline": " ".join(exe)}, 101.0))

    def test_the_command_started_as_a_module_stays_recognisable_for_stop(self):
        """`python -m skillsync_workflow` puts the path of `__main__.py` into argv[0]; the server started from it must still be stoppable."""
        entry = str(ROOT / "skillsync_workflow" / "__main__.py")
        command = detach.child_command([entry, "dashboard", "serve", ".", "--detach"], executable="/usr/bin/python3")
        with mock.patch.object(servers, "_windows", lambda: False):
            self.assertTrue(servers.identity_matches("dashboard", {"cmdline": " ".join(command).replace("\\", "/"), "started": 100.0}, 101.0), command)


class SpawnOptionTests(unittest.TestCase):
    def test_posix_gets_a_session_of_its_own(self):
        self.assertEqual(detach.spawn_options(False), {"start_new_session": True})

    def test_windows_gets_a_process_group_and_a_hidden_console_but_is_not_detached_from_any_console(self):
        flags = detach.spawn_options(True)
        self.assertEqual(set(flags), {"creationflags"})
        self.assertEqual(flags["creationflags"], 0x00000200 | 0x08000000)
        self.assertFalse(flags["creationflags"] & 0x00000008, "DETACHED_PROCESS leaves no console for CTRL_BREAK to reach")


class WaitTests(unittest.TestCase):
    OLD = {"port": 1, "pid": 10, "serverId": "a"}
    NEW = {"port": 2, "pid": 20, "serverId": "b"}
    SECOND = {"port": 3, "pid": 20, "serverId": "b"}

    def wait(self, answers, child=None, timeout=10.0, before=None):
        clock, queue = [0.0], list(answers)

        def find(_plan):
            return list(queue.pop(0) if len(queue) > 1 else queue[0])

        return detach.wait_until_ready(child or FakeChild(), None, before or set(), timeout, find=find,
                                       sleep=lambda s: clock.__setitem__(0, clock[0] + s), monotonic=lambda: clock[0])

    def test_the_records_that_were_there_before_the_start_are_not_the_new_server(self):
        before = {detach._identity(self.OLD)}
        self.assertEqual(self.wait([[self.OLD], [self.OLD, self.NEW]], before=before), [self.NEW])
        self.assertIsNone(self.wait([[self.OLD]], before=before, timeout=1.0))

    def test_the_second_socket_that_appears_a_moment_later_is_included(self):
        self.assertEqual(self.wait([[self.NEW], [self.NEW, self.SECOND]]), [self.NEW, self.SECOND])

    def test_two_sockets_are_waited_for_until_both_state_files_are_there(self):
        read, approve = {**self.NEW, "peer": 3}, {**self.SECOND, "peer": 2}
        self.assertEqual(self.wait([[read], [read], [read], [read, approve]]), [read, approve])
        self.assertIsNone(self.wait([[read]], timeout=1.0))

    def test_a_record_that_vanishes_on_the_second_look_is_still_reported(self):
        self.assertEqual(self.wait([[self.NEW], []]), [self.NEW])

    def test_an_ended_process_without_a_record_is_an_empty_list_and_time_over_is_none(self):
        self.assertEqual(self.wait([[]], child=FakeChild(code=2)), [])
        self.assertIsNone(self.wait([[]], timeout=0.0))
        self.assertIsNone(self.wait([[]], timeout=3.0))


class EndChildTests(unittest.TestCase):
    def test_an_ended_child_gets_no_signal(self):
        deps = FakeDeps()
        detach.end_child(FakeChild(code=0), deps)
        self.assertEqual(deps.terminated, [])

    def test_the_gentle_stop_comes_first_and_a_child_that_ends_is_not_killed(self):
        deps, child = FakeDeps(), FakeChild(pid=4321)
        detach.end_child(child, deps)
        self.assertEqual((deps.terminated, child.killed, child.waits), ([4321], False, [detach.END_WAIT_S]))

    def test_a_child_that_ignores_the_gentle_stop_is_killed_and_reaped(self):
        deps, child = FakeDeps(), FakeChild(pid=4321, stubborn=True)
        detach.end_child(child, deps)
        self.assertEqual((deps.terminated, child.killed, child.code), ([4321], True, -9))


class LogTests(MachineCase):
    def open(self, root=None):
        path, stream = detach.open_log(root or self.project)
        stream.close()
        return path

    @unittest.skipIf(WINDOWS, POSIX_MODES)
    def test_the_log_and_its_directory_are_private_even_with_an_open_umask(self):
        previous = os.umask(0)
        try:
            path = self.open()
        finally:
            os.umask(previous)
        self.assertEqual(path.parent, dashboard_token.state_dir())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_only_the_newest_logs_of_this_directory_stay_and_other_files_are_left_alone(self):
        other = self.open(self.tmp)
        state = self.server_wrote_state(5170)
        made = [self.open() for _ in range(detach.KEPT_LOGS + 3)]
        kept = [path for path in made if path.exists()]
        self.assertEqual(kept, made[-detach.KEPT_LOGS:])
        self.assertTrue(other.exists() and state.exists())
        self.assertEqual(len(self.logs()), detach.KEPT_LOGS + 1)

    @unittest.skipIf(WINDOWS, POSIX_LINKS)
    def test_a_link_in_the_place_of_the_log_is_refused_and_its_target_is_not_written(self):
        victim = write(self.tmp / "victim.txt", "keep\n")
        directory = dashboard_token.state_dir()
        directory.mkdir(parents=True)
        name = f"{dashboard_token._digest(self.project)}-detach-{7:020d}-{os.getpid()}.log"
        for target in (victim, self.tmp / "not-there"):
            with self.subTest(target=target.name):
                link = directory / name
                if os.path.lexists(link):
                    link.unlink()
                link.symlink_to(target)
                with mock.patch.object(detach.time, "time_ns", lambda: 7), self.assertRaises(OSError):
                    detach.open_log(self.project)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")
        self.assertFalse((self.tmp / "not-there").exists())

    @unittest.skipIf(WINDOWS, POSIX_LINKS)
    def test_a_state_directory_that_is_a_link_blocks_the_start_and_starts_nothing(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        directory = dashboard_token.state_dir()
        directory.parent.mkdir(parents=True)
        directory.symlink_to(elsewhere, target_is_directory=True)
        code, out, _err = self.run_detached("--port", "5170", "--json", popen=mock.Mock(side_effect=AssertionError("started")))
        self.assertEqual((code, json.loads(out)["code"]), (2, "detach_failed"))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_the_tail_has_the_last_lines_only_and_a_missing_log_gives_nothing(self):
        path = write(self.tmp / "x.log", "".join(f"line {number}\n" for number in range(100)))
        self.assertEqual(detach.log_tail(path, 3), "line 97\nline 98\nline 99")
        self.assertEqual(len(detach.log_tail(path).splitlines()), detach.TAIL_LINES)
        self.assertEqual(detach.log_tail(self.tmp / "missing.log"), "")


class ServeDetachedTests(MachineCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(dashboard_token, "_healthy", lambda *args, **kwargs: True))
        self.calls = []

    def popen(self, child: FakeChild, says: str = "", ready: dict | None = None):
        def start(command, **options):
            self.calls.append((command, options))
            options["stdout"].write(says.encode("utf-8"))
            options["stdout"].flush()
            if ready is not None:
                self.server_wrote_state(**ready)
            return child
        return start

    def test_the_server_gets_closed_input_the_log_as_output_and_an_environment_that_does_not_buffer(self):
        child = FakeChild(pid=os.getpid())
        code, out, err = self.run_detached("--port", "5170", popen=self.popen(child, ready={"port": 5170}))
        self.assertEqual(code, 0, err)
        command, options = self.calls[0]
        self.assertNotIn("--detach", command)
        self.assertEqual(command[-3:], [str(self.project), "--port", "5170"])
        self.assertEqual((options["stdin"], options["stderr"]), (subprocess.DEVNULL, subprocess.STDOUT))
        self.assertEqual((options["env"]["PYTHONUNBUFFERED"], options["env"]["PYTHONIOENCODING"]), ("1", "utf-8"))
        for key, value in detach.spawn_options(WINDOWS).items():
            self.assertEqual(options[key], value)
        self.assertTrue(options["stdout"].closed, "the command keeps no handle of the log")
        self.assertEqual(out, "http://127.0.0.1:5170/\n", "stdout is the address alone")
        self.assertIn(str(child.pid), err)
        self.assertIn(str(self.logs()[0]), err)
        self.assertIn("dashboard stop", err)

    def test_json_is_one_object_with_the_address_the_process_and_the_log(self):
        code, out, _err = self.run_detached("--port", "5170", "--json", popen=self.popen(FakeChild(pid=os.getpid()), ready={"port": 5170}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"schema": 1, "status": "PASS", "mode": "detached", "url": "http://127.0.0.1:5170/", "port": 5170,
                                           "pid": os.getpid(), "log": str(self.logs()[0])})

    def test_a_server_that_ends_before_it_answers_is_detach_failed_with_its_last_lines(self):
        child = FakeChild(code=2)
        code, out, err = self.run_detached("--port", "5170", "--json", popen=self.popen(child, says="why-it-ended\n"))
        report = json.loads(out)
        self.assertEqual((code, report["status"], report["code"]), (2, "BLOCKED", "detach_failed"))
        self.assertIn(str(self.logs()[0]), report["reason"])
        self.assertIn("why-it-ended", err)

    def test_without_json_a_refusal_goes_to_stderr_and_stdout_stays_empty(self):
        code, out, err = self.run_detached("--port", "5170", popen=self.popen(FakeChild(code=2)))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("detach_failed:", err)

    def test_a_server_that_cannot_be_started_is_detach_failed(self):
        code, out, _err = self.run_detached("--port", "5170", "--json", popen=mock.Mock(side_effect=PermissionError("no exec")))
        report = json.loads(out)
        self.assertEqual((code, report["code"]), (2, "detach_failed"))
        self.assertIn("no exec", report["reason"])

    def test_a_server_that_never_answers_is_ended_and_reported_as_detach_timeout(self):
        child = FakeChild()
        deps = FakeDeps()
        with mock.patch.object(servers, "Deps", lambda: _SweepNothing(deps)):
            code, out, err = self.run_detached("--port", "5170", "--json", popen=self.popen(child, says="still-starting\n"), timeout=0.0)
        report = json.loads(out)
        self.assertEqual((code, report["code"]), (2, "detach_timeout"))
        self.assertEqual(deps.terminated, [child.pid])
        self.assertIsNotNone(child.poll())
        self.assertIn("still-starting", err)

    def test_ctrl_c_while_waiting_ends_the_server_and_is_passed_on(self):
        child = FakeChild()
        with mock.patch.object(detach, "wait_until_ready", mock.Mock(side_effect=KeyboardInterrupt)), \
                mock.patch.object(detach, "end_child") as ended, self.assertRaises(KeyboardInterrupt):
            self.run_detached("--port", "5170", popen=self.popen(child))
        ended.assert_called_once_with(child)

    def test_a_directory_that_is_not_a_project_is_invalid_project_and_starts_nothing(self):
        start = mock.Mock(side_effect=AssertionError("started"))
        for path in (str(self.tmp / "missing"), str(self.tmp)):
            with self.subTest(path=path):
                code, out, _err = self.run_detached("--json", popen=start, path=path)
                self.assertEqual((code, json.loads(out)["code"]), (2, "invalid_project"))
        start.assert_not_called()
        self.assertEqual(self.logs(), [])

    def test_a_running_project_server_gets_the_directory_as_a_tab_and_no_process_is_started(self):
        write(self.project / ".git" / "HEAD", "ref: refs/heads/main\n")
        common = str(self.project / ".git")
        self.server_wrote_state(5170, kind="project", common_dir=common, tabs=[{"id": "main", "path": str(self.project)}])
        start = mock.Mock(side_effect=AssertionError("started"))
        code, out, err = self.run_detached("--json", popen=start)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["url"], "http://127.0.0.1:5170/?tab=main")
        start.assert_not_called()
        self.assertEqual(self.logs(), [])

    def test_the_server_of_another_process_is_not_reported_as_the_one_this_command_started(self):
        """A second start runs at the same moment (another port): its state file appears first, while our own server has not answered. The
        command must not claim that server as its own (wrong address, and a log that belongs to another process) while its own one goes on."""
        child = FakeChild(pid=os.getpid())
        deps = FakeDeps()
        with mock.patch.object(servers, "Deps", lambda: _SweepNothing(deps)):
            code, out, _err = self.run_detached("--port", "5170", "--json", popen=self.popen(child, ready={"port": 5171, "pid": os.getppid()}),
                                                timeout=0.5)
        report = json.loads(out)
        self.assertFalse(report["status"] == "PASS" and report["pid"] != child.pid,
                         f"reported the server of pid {report.get('pid')} on port {report.get('port')}, the started process is {child.pid} on 5170")

    def test_json_stays_json_when_the_started_process_only_added_a_tab(self):
        """The child found a server that had only just started: it adds a tab, prints messages on stderr and its report on stdout, and ends with 0.
        With `--json` the stdout of the command is one JSON object."""
        answer = {"schema": 1, "status": "PASS", "mode": "append", "url": "http://127.0.0.1:5170/?tab=main", "port": 5170, "pid": 1}
        says = "a message of the server on stderr\n" + json.dumps(answer, indent=2) + "\n"
        code, out, _err = self.run_detached("--json", popen=self.popen(FakeChild(code=0), says=says))
        self.assertEqual(code, 0)
        try:
            parsed = json.loads(out)
        except ValueError:
            self.fail(f"stdout is not JSON: {out!r}")
        self.assertEqual(parsed["url"], answer["url"])


class _SweepNothing(servers.Deps):
    """`servers.Deps` with the real registry but a recorded gentle stop (the child of the test is a fake)."""

    def __init__(self, fake: FakeDeps) -> None:
        self.fake = fake

    def terminate(self, pid):
        return self.fake.terminate(pid)


class PageAddressTests(unittest.TestCase):
    class Plan:
        def __init__(self, root):
            self.root = root

    def test_the_socket_with_the_consents_is_printed_and_a_tab_of_a_project_server_by_its_id(self):
        root = Path(tempfile.gettempdir()).resolve()
        read = {"port": 5170, "approvals": False, "host": "127.0.0.1", "tabs": [], "kind": "standalone"}
        approve = {"port": 5171, "approvals": True, "host": "127.0.0.1", "tabs": [], "kind": "standalone"}
        self.assertEqual(detach.page_address([read, approve], self.Plan(root))[1], "http://127.0.0.1:5171/")
        project = {**approve, "kind": "project", "tabs": [{"id": "0123456789ab", "path": str(root)}]}
        self.assertEqual(detach.page_address([project], self.Plan(root))[1], "http://127.0.0.1:5171/?tab=0123456789ab")

    def test_a_bind_the_page_does_not_sign_in_under_is_offered_on_loopback(self):
        record = {"port": 5170, "approvals": True, "host": "10.1.2.3", "hosts": [], "tabs": [], "kind": "standalone"}
        self.assertEqual(detach.page_address([record], self.Plan(Path(".")))[1], "http://127.0.0.1:5170/")


class ApprovalsRegressionTests(MachineCase):
    """`servers` and the records of 2.0.0, which have no token: `approvals` comes from the record, and the stop warns that consents are lost."""

    def record(self, approvals, role="page", **extra):
        path = dashboard_token.write_state(self.project, 5170, approvals, role=role, server_id=dashboard_token.new_server_id())
        if extra:
            data = {**json.loads(path.read_text(encoding="utf-8")), **extra}
            path.write_text(json.dumps(data), encoding="utf-8")
        listed = servers.list_servers("dashboard")
        self.assertEqual(len(listed), 1, listed)
        dry = servers.stop_servers({"root": [str(self.project)], "kind": "dashboard"}, dry_run=True,
                                   deps=_Recognised())
        return listed[0], dry["items"][0]["warnings"]

    def test_a_server_with_consents_and_no_token_is_listed_so_and_the_stop_warns(self):
        for role in ("page", "approve"):
            with self.subTest(role=role):
                listed, warnings = self.record(True, role)
                self.assertIs(listed["approvals"], True)
                self.assertEqual(len(warnings), 1)
                self.assertIn("consent", warnings[0])

    def test_a_server_without_consents_and_a_read_socket_give_no_warning(self):
        for approvals, role in ((False, "page"), (None, "read")):
            with self.subTest(role=role):
                listed, warnings = self.record(approvals, role)
                self.assertIs(listed["approvals"], False)
                self.assertEqual(warnings, [])

    def test_a_record_of_an_earlier_version_with_a_token_still_counts_and_the_token_is_not_copied(self):
        secret = "-".join(("old", "server", "value"))
        listed, warnings = self.record(False, "approve", token=secret)
        self.assertIs(listed["approvals"], True)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn(secret, json.dumps(listed))


class _Recognised(servers.Deps):
    def identity(self, pid):
        return {"cmdline": "skillsync dashboard serve", "started": 0.0}


# ---------------------------------------------------------------------------------------------- real processes

class ProcessCase(unittest.TestCase):
    """One temporary git project, one isolated machine, real `skillsync` processes."""

    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="detach-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        machine = self.tmp / "machine"
        machine.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key not in GIT_IDENTITY_VARS and key != "SKILLSYNC_TEST_DETACH_DEPTH"}
        self.env.update(isolated_paths(machine))
        self.env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "SKILLSYNC_TEST_PAGE_PORT": str(free_port())})
        require_isolated_state(self, self.env)
        # the core layer comes from the machine config: a `--layer` flag is a setting of the server and would make every start a standalone one
        write(Path(self.env["SKILLSYNC_CONFIG"]), json.dumps({"layers": [{"id": "core", "kind": "core", "path": str(ROOT / "library")}],
                                                              "layer_rules": [{"match": ["**"], "use": ["core"]}]}) + "\n")
        self.pids = self.tmp / "pids"
        self.pids.mkdir()
        self.hang, self.hung = self.tmp / "hang-next-server", self.tmp / "hung-server"
        self.launcher = write(self.tmp / "bin" / "skillsync", LAUNCHER.format(root=str(ROOT), pids=str(self.pids), hang=str(self.hang),
                                                                              hung=str(self.hung)))
        self.addCleanup(self.kill_all)   # registered after the removal of the directory: runs before it
        self.project = self.tmp / "proj"
        write(self.project / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        write(self.project / ".gitignore", ".skillsync/\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", ".github/.skillsync.yaml", ".gitignore")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "start")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.project), env=self.env, check=True, capture_output=True, timeout=60)

    def worktree(self) -> Path:
        path = self.tmp / "wt"
        self.git("worktree", "add", "-q", "-b", "side", str(path))
        return path

    # --- processes

    def ours(self, pid: int) -> bool:
        """Is `pid` a living process of this test (its command line names the temporary directory)."""
        identity = servers.process_identity(pid)
        return bool(identity) and str(self.tmp).replace("\\", "/").lower() in identity["cmdline"].replace("\\", "/").lower()

    def known_pids(self) -> set:
        found = {int(path.name) for path in self.pids.iterdir()}
        for path in state_files(self.env):
            with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                found.add(int(json.loads(path.read_text(encoding="utf-8"))["pid"]))
        return found

    def kill_all(self) -> None:
        for pid in self.known_pids():
            if pid != os.getpid() and self.ours(pid):
                servers.Deps().kill(pid)
                self.wait_gone(pid)

    def wait_gone(self, pid: int, seconds: float = 20.0) -> bool:
        deadline = time.monotonic() + seconds
        while self.ours(pid):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True

    def command(self, *args: str) -> list:
        return [sys.executable, str(self.launcher), "dashboard", *args]

    def run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(self.command(*args), cwd=str(cwd or self.tmp), env=self.env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90, check=False, stdin=subprocess.DEVNULL)

    def serve(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return self.run_cli("serve", *args, "--detach", cwd=cwd)

    def started(self, done: subprocess.CompletedProcess) -> dict:
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        try:
            return json.loads(done.stdout)
        except ValueError:
            self.fail(f"stdout is not JSON: {done.stdout!r}\n{done.stderr}")

    def healthy(self, port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
                return response.status == 200 and json.load(response).get("ok") is True
        except (OSError, ValueError):
            return False

    def records(self) -> list:
        return [json.loads(path.read_text(encoding="utf-8")) for path in state_files(self.env)]

    def assert_stopped(self, report: dict) -> None:
        self.assertTrue(self.wait_gone(report["pid"]), f"process {report['pid']} still runs")
        self.assertFalse(self.healthy(report["port"]))
        self.assertNotIn(report["pid"], [record["pid"] for record in self.records()])


class RoundTripTests(ProcessCase):
    def test_start_health_second_start_from_a_worktree_stop_from_the_worktree(self):
        worktree = self.worktree()
        port = free_port()
        done = self.serve(str(self.project), "--port", str(port), "--json")
        first = self.started(done)   # the command has returned
        self.assertEqual((first["status"], first["mode"], first["port"]), ("PASS", "detached", port))
        self.assertTrue(self.ours(first["pid"]), "the server does not outlive the command")
        self.assertTrue(self.healthy(port))
        if not WINDOWS:
            self.assertEqual(os.getsid(first["pid"]), first["pid"], "the server has no session of its own")
            self.assertNotEqual(os.getsid(first["pid"]), os.getsid(0))
            self.assertEqual(stat.S_IMODE(os.stat(first["log"]).st_mode), 0o600)
        self.assertIn(str(port), Path(first["log"]).read_text(encoding="utf-8"), "the console of the server is not in the log")
        self.assertEqual([(record["pid"], record["kind"]) for record in self.records()], [(first["pid"], "project")])

        processes = set(self.known_pids())
        second = self.started(self.serve(".", "--json", cwd=worktree))   # relative path, no port flag: only a tab
        self.assertEqual((second["pid"], second["port"]), (first["pid"], port))
        self.assertIn("?tab=", second["url"])
        tabs = {Path(tab["path"]).resolve() for record in self.records() for tab in record["tabs"]}
        self.assertEqual(tabs, {self.project, worktree})
        self.assertEqual(len(self.known_pids() - processes), 1, "only the command itself ran: no second server")
        self.assertEqual(len(list(Path(first["log"]).parent.glob("*.log"))), 1)

        dry = self.run_cli("stop", "--dry-run", "--json", cwd=worktree)
        self.assertEqual(dry.returncode, 3, dry.stdout + dry.stderr)
        self.assertEqual([(item["pid"], item["outcome"]) for item in json.loads(dry.stdout)["items"]], [(first["pid"], "stopped")])
        self.assertTrue(self.healthy(port), "a dry run stopped the server")

        wrong = self.run_cli("stop", "--port", str(free_port(skip=(port,))), "--json", cwd=worktree)
        self.assertEqual((wrong.returncode, json.loads(wrong.stdout)["items"]), (0, []), wrong.stdout + wrong.stderr)
        self.assertTrue(self.healthy(port), "--port of another server stopped this one")

        stopped = self.run_cli("stop", "--port", str(port), "--json", cwd=worktree)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        result = json.loads(stopped.stdout)   # the note about the shared server goes to stderr and keeps stdout JSON
        self.assertEqual([(item["pid"], item["outcome"]) for item in result["items"]], [(first["pid"], "stopped")])
        self.assertTrue(any("consent" in warning for warning in result["items"][0]["warnings"]), result)
        self.assertIn("worktree", stopped.stderr)
        self.assert_stopped(first)
        self.assertEqual(self.records(), [])

        again = self.run_cli("stop", "--json", cwd=worktree)
        self.assertEqual((again.returncode, json.loads(again.stdout)["items"]), (0, []))

    def test_a_relative_path_and_the_plain_output_give_an_address_that_answers_and_stop_from_the_main_checkout(self):
        port = free_port()
        done = self.serve("proj", "--port", str(port), cwd=self.tmp)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), f"http://127.0.0.1:{port}/?tab=main")
        self.assertTrue(self.healthy(port))
        [record] = self.records()
        stopped = self.run_cli("stop", cwd=self.project)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("stopped", stopped.stdout)
        self.assertNotIn("worktree", stopped.stderr, "the note of a shared server is for a worktree")
        self.assert_stopped({"pid": record["pid"], "port": port})

    def test_nothing_to_stop_is_pass_and_a_directory_without_a_manifest_is_blocked(self):
        nothing = self.run_cli("stop", str(self.project))
        self.assertEqual(nothing.returncode, 0, nothing.stdout + nothing.stderr)
        self.assertIn("nothing to stop", nothing.stdout)
        blocked = self.run_cli("stop", str(self.tmp / "missing"), "--json")
        self.assertEqual((blocked.returncode, json.loads(blocked.stdout)["code"]), (2, "invalid_project"), blocked.stdout)

    def test_a_standalone_server_of_a_worktree_stops_from_that_worktree_together_with_the_project_server(self):
        """The documented selection: `servers stop` for the directory of the command and the main checkout."""
        worktree = self.worktree()
        shared_port = free_port()
        shared = self.started(self.serve(str(self.project), "--port", str(shared_port), "--json"))
        own = self.started(self.serve(str(worktree), "--standalone", "--port", str(free_port(skip=(shared_port,))), "--json"))
        self.assertNotEqual(shared["pid"], own["pid"])

        from_main = self.run_cli("stop", "--json", cwd=self.project)
        self.assertEqual([item["pid"] for item in json.loads(from_main.stdout)["items"]], [shared["pid"]], from_main.stdout + from_main.stderr)
        self.assert_stopped(shared)
        self.assertTrue(self.healthy(own["port"]))

        shared = self.started(self.serve(str(self.project), "--port", str(shared_port), "--json"))
        from_worktree = self.run_cli("stop", "--json", cwd=worktree)
        self.assertEqual(from_worktree.returncode, 0, from_worktree.stdout + from_worktree.stderr)
        self.assertEqual({item["pid"] for item in json.loads(from_worktree.stdout)["items"]}, {shared["pid"], own["pid"]})
        self.assert_stopped(shared)
        self.assert_stopped(own)

    def test_a_start_removes_the_leftover_of_a_dead_server_of_any_project_first(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(timeout=30)
        with mock.patch.dict(os.environ, self.env):
            other = self.tmp / "other"
            other.mkdir()
            leftover = dashboard_token.write_state(other, free_port(), True, role="page", server_id=dashboard_token.new_server_id())
        record = json.loads(leftover.read_text(encoding="utf-8"))
        record["pid"] = gone.pid
        leftover.write_text(json.dumps(record), encoding="utf-8")
        report = self.started(self.serve(str(self.project), "--port", str(free_port(skip=(record["port"],))), "--json"))
        self.assertFalse(leftover.exists(), "the state file of the dead server stayed")
        self.assertEqual([item["pid"] for item in self.records()], [report["pid"]])

    def test_a_busy_port_is_detach_failed_with_the_reason_and_leaves_no_process(self):
        port = free_port()
        with socket.socket() as holder:
            if WINDOWS:
                holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # the port may be in TIME_WAIT after an earlier test
            holder.bind(("127.0.0.1", port))
            holder.listen(1)
            done = self.serve(str(self.project), "--port", str(port), "--json")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual((report["status"], report["code"]), ("BLOCKED", "detach_failed"))
        self.assertIn("nasłuchiwać", done.stderr)
        self.assertEqual([pid for pid in self.known_pids() if self.ours(pid)], [])
        self.assertEqual(self.records(), [])

    def test_started_as_a_python_module_the_server_starts_and_stops(self):
        port = free_port()
        env = {**self.env, "PYTHONPATH": str(ROOT)}
        module = [sys.executable, "-m", "skillsync_workflow", "dashboard"]
        done = subprocess.run([*module, "serve", str(self.project), "--port", str(port), "--detach", "--json"], cwd=str(self.tmp),
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=False)
        report = self.started(done)
        stopped = subprocess.run([*module, "stop", str(self.project), "--json"], cwd=str(self.tmp), env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=90, check=False)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assert_stopped(report)


class ParallelAndInterruptTests(ProcessCase):
    def spawn(self, port: int) -> subprocess.Popen:
        proc = subprocess.Popen(self.command("serve", str(self.project), "--port", str(port), "--json", "--detach"), cwd=str(self.tmp),
                                env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace")

        def end():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=20)

        self.addCleanup(end)
        return proc

    def hung_pid(self, parent: subprocess.Popen) -> int:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and parent.poll() is None:
            text = self.hung.read_text(encoding="utf-8") if self.hung.exists() else ""
            if text.isdigit():
                return int(text)
            time.sleep(0.05)
        self.fail("the server that should hang did not start")

    def test_two_starts_at_once_on_one_port_leave_exactly_one_server_and_report_only_that_one(self):
        port = free_port()
        commands = [self.spawn(port), self.spawn(port)]
        results = [(proc.communicate(timeout=90), proc.returncode) for proc in commands]
        self.assertTrue(self.healthy(port), results)
        [record] = self.records()
        passed = []
        for (out, err), code in results:
            self.assertIn(code, (0, 2), out + err)
            if code == 0:
                passed.append(out)
        self.assertTrue(passed, results)
        for out in passed:
            self.assertIn(f":{port}/", out)
            self.assertIn(str(record["pid"]), out)
        deadline = time.monotonic() + 20
        while len([pid for pid in self.known_pids() if self.ours(pid)]) > 1 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual([pid for pid in self.known_pids() if self.ours(pid)], [record["pid"]])

    def test_a_command_does_not_adopt_the_server_of_a_second_start_and_leave_its_own_running(self):
        """The first server hangs before it listens; a second start (another port) succeeds meanwhile."""
        self.hang.write_text("", encoding="utf-8")
        first_port = free_port()
        first = self.spawn(first_port)
        hung = self.hung_pid(first)
        other = self.started(self.serve(str(self.project), "--port", str(free_port(skip=(first_port,))), "--json"))
        try:
            out, _err = first.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            return   # still waiting for its own server: correct
        self.assertFalse(first.returncode == 0,
                         f"exit 0 with {out!r} (the server of the other command is pid {other['pid']}); the own server {hung} "
                         f"{'still runs' if self.ours(hung) else 'ended'}")

    @unittest.skipIf(WINDOWS, POSIX_SIGINT)
    def test_ctrl_c_while_waiting_leaves_no_server_behind(self):
        self.hang.write_text("", encoding="utf-8")
        command = self.spawn(free_port())
        hung = self.hung_pid(command)
        time.sleep(1.0)   # the command is in its wait (the server has nothing to show for it)
        self.assertTrue(self.ours(hung))
        self.assertNotEqual(os.getpgid(hung), os.getpgid(command.pid), "Ctrl+C of the terminal would hit the server too")
        command.send_signal(signal.SIGINT)
        command.communicate(timeout=30)
        self.assertNotEqual(command.returncode, 0)
        self.assertTrue(self.wait_gone(hung, 10), f"the server {hung} still runs after Ctrl+C")
        self.assertEqual(self.records(), [])

    def test_a_real_server_that_hangs_is_ended_after_the_time_limit(self):
        self.hang.write_text("", encoding="utf-8")
        port = free_port()
        argv = [str(self.launcher), "dashboard", "serve", str(self.project), "--port", str(port), "--json", "--detach"]
        processes = []

        def start(command, **options):
            proc = subprocess.Popen(command, **options)
            processes.append(proc)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (self.hung.exists() and self.hung.read_text(encoding="utf-8").isdigit()):
                time.sleep(0.05)
            return proc

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.dict(os.environ, {"SKILLSYNC_TEST_DETACH_DEPTH": "1"}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = detach.serve_detached(parse(*argv[2:]), argv=argv, popen=start, timeout=1.0)
        self.assertEqual((code, json.loads(out.getvalue())["code"]), (2, "detach_timeout"), out.getvalue() + err.getvalue())
        self.assertIsNotNone(processes[0].poll(), "the hung server still runs")
        self.assertIn("this server hangs", err.getvalue())


if __name__ == "__main__":
    unittest.main()
