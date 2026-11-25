"""CORE-1180 M-3: `dashboard serve` in a worktree of a project with a running server (the tab is added, no second server), the pin of the
directory of the command, the state file of schema 2, the address of a tab (`dashboard url`), `/healthz` with `serverId` and the `worktrees[]` of the
status JSON. Written against the spec (sections "Katalog uruchomienia (przypięcie)", "Dopisanie zamiast drugiego serwera", "Adres zakładki i stan
serwera", "Kontrakt JSON", K1, K7, K8, K9), never against the implementation: the public commands and files are driven from outside, the
planner (`plan_serve`) and the readers of the state/pin files are called only to check a decision or a file.

The layout of a repository is built from plain files (no test runs `git`); real servers run in subprocesses on the ports of the test slice
(`SKILLSYNC_TEST_DASHBOARD_PORTS`), under a private data home. A server that must NOT register a tab (the timeout) or a second project server
(`ambiguous_server`) is a state file plus a thread that answers `/healthz` (the state file names the process of the test as its pid).
Tokens are never literals: the tests read them from the state files. The one `skipTest` is the symlink one (a system that refuses links)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from layers_helpers import home_env  # noqa: E402
from tmp_paths import child  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_token as dt  # noqa: E402
from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from tests import test_skillsync_dashboard_worktrees as wtt  # noqa: E402
from tests.serve_start import bind_free, kill_tree, release, start_serve, start_serve_ports, stop_serve  # noqa: E402
from tests.test_skillsync_dashboard_server import CLI, free_port, port_range  # noqa: E402

TAB = r"(?:main|[0-9a-f]{12})"
ADDRESS = re.compile(rf"^http://127\.0\.0\.1:(?P<port>\d+)/\?tab=(?P<tab>{TAB})#zgoda=(?P<own>{TAB})\.(?P<token>[A-Za-z0-9_-]{{16,}})$")
SERVER_ID = re.compile(r"^[0-9a-f]{16}$")
FLAG_ATTRIBUTES = {   # every flag of the settings of a server -> an attribute of the parsed arguments that carries it
    "--port": ("port", 5190), "--read-port": ("read_port", 5191), "--approve-port": ("approve_port", 5192),
    "--read-bind": ("read_bind", "127.0.0.1"), "--approve-bind": ("approve_bind", "127.0.0.1"),
    "--read-host": ("read_host", ["viewer.example"]), "--approve-host": ("approve_host", ["me.example"]),
    "--allow-proxy-headers": ("allow_proxy_headers", True), "--no-approvals": ("no_approvals", True),
    "--layer": ("layers", ["core=/nowhere"]), "--prototype-port": ("prototype_port", 5193), "--host": ("host", "127.0.0.1"),
    "--session-url": ("session_url", "https://session.example/s"), "--poll": ("poll", 2.5), "--verbose": ("verbose", True),
}
STANDALONE_WITHOUT_A_SERVER = {"--no-approvals", "--layer"}   # a preview is never the server of the project (spec: "Rodzaj serwera ustala start")


def serve_args(path, **kwargs):
    """The parsed arguments of `dashboard serve` as the parser leaves them (nothing given)."""
    values = dict(path=str(path), layers=None, plan=None, session_url=None, approve_host=[], read_host=[], poll=1.0, verbose=False,
                  prototype_port=None, port=None, read_port=None, approve_port=None, read_bind=None, approve_bind=None,
                  allow_proxy_headers=False, host=None, no_approvals=False, standalone=False, all_tabs=False, json=False)
    values.update(kwargs)
    return SimpleNamespace(**values)


def url_args(path, **kwargs):
    values = dict(path=str(path), layers=[f"core={ROOT / 'library'}"], port=None, host=None, json=False, read=False, lan=False, all_tabs=False)
    values.update(kwargs)
    return SimpleNamespace(**values)


def get(port, path, headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def tab_ids(record):
    return [tab["id"] for tab in record["tabs"]]


class HealthOnly(BaseHTTPRequestHandler):
    """Answers `/healthz` with the body of its server, nothing else: a process that `servers()` may take for a server."""

    def do_GET(self):  # noqa: N802
        data = json.dumps(self.server.body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Base(wtt.Layout):
    """A main directory and worktrees as plain files, a private data home and config (the layer of the repository), commands run as subprocesses."""

    def setUp(self) -> None:
        super().setUp()
        self.config = self.tmp / "machine-config.yaml"
        self.config.write_text(
            f"layers:\n  - id: core\n    kind: core\n    source: {json.dumps(str(ROOT))}\n    subdir: library\n    path: {json.dumps(str(ROOT))}\n"
            "layer_rules:\n  - match: ['**']\n    use: [core]\n", encoding="utf-8")
        self.extra_env = {"SKILLSYNC_CONFIG": str(self.config), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"), **home_env(self.tmp)}
        patch = mock.patch.dict(os.environ, self.extra_env)
        patch.start()
        self.addCleanup(patch.stop)
        self.env = {**os.environ}
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        self.servers = []

    # --- layout
    def worktree(self, name, *, runs=False, marker=False) -> Path:
        wt = self.add_worktree(name)
        wtt.write(wt / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        if runs:
            self.run_dir(wt)
        if marker:
            wtt.write(wt / ".skillsync" / "no-dashboard", "")
        return wt

    def tab_of(self, path) -> str:
        return dw.tab_id(str(path), str(self.main))

    # --- commands
    def cli(self, *args, cwd=None, timeout=60):
        return subprocess.run([sys.executable, str(CLI), "dashboard", *args], capture_output=True, text=True, env=self.env, cwd=cwd,
                              timeout=timeout)

    def append(self, path, *flags, cwd=None, timeout=60):
        """`dashboard serve <path>` with no flag of the settings: the add-a-tab form (a path given, or the working directory)."""
        if cwd is not None:
            return self.cli("serve", *flags, cwd=cwd, timeout=timeout)
        return self.cli("serve", str(path), *flags, timeout=timeout)

    # --- servers
    def start_project(self, root=None, *extra):
        """A project server (two sockets) started from `root`: no server runs yet, so the flags of the settings are allowed."""
        proc, read_port, approve_port = start_serve_ports(self, CLI, root or self.main, [], self.env, free_port, "--read-bind", "127.0.0.1", *extra)
        self.servers.append(proc)
        return proc, read_port, approve_port

    def records(self) -> list:
        directory = dt.state_dir()
        out = []
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            record["_file"] = path
            out.append(record)
        return out

    def record_at(self, port) -> dict:
        found = [record for record in self.records() if record.get("port") == port]
        self.assertEqual(len(found), 1, f"state files of port {port}: {found}")
        return found[0]

    def tokens_of(self, record) -> set:
        """Every form of every token a state file holds (`<id>.<hmac>`, the hmac alone, the bare token)."""
        found = set()
        if record.get("token"):
            found.add(record["token"])
        for tab in record.get("tabs") or []:
            if tab.get("token"):
                found.add(tab["token"])
                found.add(tab["token"].split(".", 1)[-1])
        return found

    def open_ports(self) -> set:
        found = set()
        for port in port_range():
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    found.add(port)
        return found

    def wait_for(self, condition, what, seconds=10.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.1)
        self.fail(f"timed out ({seconds} s): {what}")

    # --- fake servers (a state file plus `/healthz`)
    def fake_server(self, *, kind="project", schema=2, tabs=None, token=None, server_id=None, health_server_id=None, root=None):
        """A state file of a server that is alive for `servers()`: the process of the test is its pid and a thread answers `/healthz`.
        Returns `(server_id, port, token)`. `health_server_id` makes the thread answer another id (a stranger on the port)."""
        server_id = server_id or dt.new_server_id()
        token = token or "fake-" + "".join(f"{ord(c):02x}" for c in server_id)
        body = {"ok": True, "version": "0"}
        if schema == 2:
            body["serverId"] = health_server_id or server_id
        else:
            body["tokenId"] = dt.token_id(token)
        health, port = bind_free(lambda p: ThreadingHTTPServer(("127.0.0.1", p), HealthOnly), free_port)
        health.body = body
        threading.Thread(target=health.serve_forever, daemon=True).start()
        self.addCleanup(health.server_close)
        self.addCleanup(health.shutdown)
        project = dw.project_identity(str(self.main))
        listed = tabs if tabs is not None else [{"id": "main", "path": str(self.main), "token": f"main.{token}"}]
        if schema == 2:
            dt.write_state(root or self.main, port, token, "127.0.0.1", server_id=server_id, kind=kind, common_dir=project.common_dir, tabs=listed)
        else:
            dt.write_state(root or self.main, port, token, "127.0.0.1")
        return server_id, port, token


class ServerCase(Base):
    """A running project server (main tab only) and two worktrees without any trace of work."""

    def setUp(self) -> None:
        super().setUp()
        self.wt1 = self.worktree("wt1")
        self.wt2 = self.worktree("wt2")
        self.proc, self.read_port, self.approve_port = self.start_project()

    def approve_record(self) -> dict:
        return self.record_at(self.approve_port)

    def read_record(self) -> dict:
        return self.record_at(self.read_port)

    def appended(self, path, *flags) -> subprocess.CompletedProcess:
        result = self.append(path, *flags)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def last_line(self, result) -> str:
        return result.stdout.strip().splitlines()[-1]


# ======================================================================== (1) joining a running project server


class JoinsARunningServer(ServerCase):
    def test_no_socket_is_opened_and_the_address_is_of_the_own_tab_only(self) -> None:
        before_ports, before_files = self.open_ports(), sorted(r["_file"].name for r in self.records())
        main_tokens = self.tokens_of(self.approve_record())
        self.assertTrue({self.read_port, self.approve_port} <= before_ports, "control: the probe sees the sockets of the server")
        result = self.appended(self.wt1)
        self.assertEqual((self.open_ports(), sorted(r["_file"].name for r in self.records())), (before_ports, before_files),
                         "joining a tab opens no socket and writes no state file of its own")
        match = ADDRESS.match(self.last_line(result))
        self.assertIsNotNone(match, result.stdout)
        tab = self.tab_of(self.wt1)
        self.assertEqual((match["tab"], match["own"]), (tab, tab))
        self.assertEqual(int(match["port"]), self.approve_port, "the address with consents is on the consent socket")
        record = self.approve_record()
        entry = next(item for item in record["tabs"] if item["id"] == tab)
        self.assertEqual(entry["token"], f"{tab}.{match['token']}", "the printed pair is the token of the tab in the state file")
        self.assertEqual(wtt.real(entry["path"]), wtt.real(self.wt1))
        for token in main_tokens:
            self.assertNotIn(token, result.stdout, "the token of the main tab is not printed")
            self.assertNotIn(token, result.stderr)
        self.assertEqual(result.stdout.count("#zgoda="), 1)

    def test_the_read_socket_state_lists_the_tab_without_a_token(self) -> None:
        self.appended(self.wt1)
        tab = self.tab_of(self.wt1)
        self.wait_for(lambda: tab in tab_ids(self.read_record()), "the read-socket file lists the tab")
        self.assertTrue(all("token" not in item for item in self.read_record()["tabs"]))
        self.assertNotIn("token", self.read_record())

    def test_stderr_names_the_server_pid_and_warns_that_closing_its_terminal_closes_the_page(self) -> None:
        result = self.appended(self.wt1)
        self.assertIn(str(self.approve_record()["pid"]), result.stderr)
        self.assertIn("zamknię", result.stderr.lower())   # PL: "zamknięcie terminala serwera zamknie stronę wszystkim zakładkom"
        self.assertNotIn("#zgoda=", result.stderr)

    def test_the_working_directory_is_used_when_no_path_is_given(self) -> None:
        result = self.append(None, cwd=str(self.wt2))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ADDRESS.match(self.last_line(result))["tab"], self.tab_of(self.wt2))

    def test_all_tabs_prints_every_pair_with_the_own_one_last(self) -> None:
        self.appended(self.wt1)
        result = self.appended(self.wt2, "--all-tabs")
        lines = [line for line in result.stdout.strip().splitlines() if line.startswith("http")]
        record = self.approve_record()
        self.assertEqual(sorted(ADDRESS.match(line)["tab"] for line in lines), sorted(tab_ids(record)))
        self.assertEqual(ADDRESS.match(lines[-1])["tab"], self.tab_of(self.wt2), "the own tab is the last line")
        for line in lines:
            match = ADDRESS.match(line)
            self.assertEqual(next(t["token"] for t in record["tabs"] if t["id"] == match["tab"]), f"{match['tab']}.{match['token']}")

    def test_json_returns_one_object_with_the_own_address(self) -> None:
        result = self.appended(self.wt1, "--json")
        answer = json.loads(result.stdout)
        self.assertIsInstance(answer, dict)
        self.assertEqual(answer["status"], "PASS")
        match = ADDRESS.match(answer["url"])
        self.assertIsNotNone(match, answer)
        self.assertEqual(match["tab"], self.tab_of(self.wt1))
        for token in self.tokens_of(self.approve_record()) - {f"{match['tab']}.{match['token']}", match["token"]}:
            self.assertNotIn(token, result.stdout)

    def test_json_with_all_tabs_lists_every_tab(self) -> None:
        answer = json.loads(self.appended(self.wt1, "--json", "--all-tabs").stdout)
        self.assertEqual(answer["status"], "PASS")
        pairs = answer["tabs"]
        self.assertEqual(sorted(ADDRESS.match(pair["url"])["tab"] for pair in pairs), sorted(tab_ids(self.approve_record())))
        self.assertEqual(answer["url"], next(pair["url"] for pair in pairs if pair["id"] == self.tab_of(self.wt1)))

    def test_joining_twice_gives_the_same_address_and_one_tab(self) -> None:
        first = self.last_line(self.appended(self.wt1))
        second = self.last_line(self.appended(self.wt1))
        self.assertEqual(first, second)
        self.assertEqual(tab_ids(self.approve_record()).count(self.tab_of(self.wt1)), 1)

    def test_the_main_directory_itself_gets_its_own_address(self) -> None:
        result = self.appended(self.main)
        match = ADDRESS.match(self.last_line(result))
        self.assertEqual((match["tab"], match["own"]), ("main", "main"))

    def test_the_server_keeps_running_and_the_command_leaves_nothing_behind(self) -> None:
        self.appended(self.wt1)
        self.assertIsNone(self.proc.poll())
        self.assertEqual(get(self.read_port, "/healthz")[0], 200)


# ======================================================================== (2) a fresh worktree without a trace of work


class FreshWorktree(Base):
    def test_a_worktree_with_the_plans_of_the_main_directory_and_no_run_is_pinned_and_gets_a_tab(self) -> None:
        for folder in (self.main, ):
            wtt.write(folder / "docs" / "plans" / "2026-11-01-demo.md", "# Plan\n\nStatus: approved\n")
        wt = self.worktree("fresh")
        wtt.write(wt / "docs" / "plans" / "2026-11-01-demo.md", "# Plan\n\nStatus: approved\n")
        self.assertFalse(dw.has_trace(str(wt), str(self.main)), "precondition: R1 would skip this worktree")
        _proc, _read, approve = self.start_project()
        self.assertNotIn(self.tab_of(wt), tab_ids(self.record_at(approve)), "precondition: a worktree without a trace is not registered by itself")
        result = self.cli("serve", str(wt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ADDRESS.match(result.stdout.strip().splitlines()[-1])["tab"], self.tab_of(wt))
        self.assertIn(self.tab_of(wt), tab_ids(self.record_at(approve)))

    def test_a_worktree_with_no_plans_and_no_run_is_pinned_too(self) -> None:
        wt = self.worktree("bare-fresh")
        _proc, _read, approve = self.start_project()
        result = self.cli("serve", str(wt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(self.tab_of(wt), tab_ids(self.record_at(approve)))


# ======================================================================== (3) registration_timeout


class RegistrationTimeout(Base):
    def test_a_server_that_does_not_register_the_tab_ends_blocked_without_an_address_and_without_the_pin(self) -> None:
        wt = self.worktree("slow")
        server_id, _port, token = self.fake_server()
        started = time.monotonic()
        proc = subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(wt)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        pin = dt.pins_dir(server_id) / f"{self.tab_of(wt)}.json"
        self.wait_for(pin.exists, "the pin file appears before the command gives up", seconds=4.0)
        self.assertEqual(json.loads(pin.read_text(encoding="utf-8"))["path"], str(wt))
        out, err = proc.communicate(timeout=90)
        result = subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("registration_timeout", result.stderr)
        self.assertEqual(result.stdout.strip(), "", "no address, not even one without a token")
        self.assertNotIn("#zgoda", result.stdout + result.stderr)
        self.assertNotIn("http://", result.stdout)
        self.assertLess(elapsed, 30, "the wait is a few seconds, not unbounded")
        pins = dt.pins_dir(server_id)
        self.assertEqual([p.name for p in pins.glob("*.json")] if pins.is_dir() else [], [], "the pin file is removed")
        self.assertNotIn(token, result.stdout + result.stderr)

    def test_the_timeout_is_a_few_seconds_and_the_wait_polls_the_state_file(self) -> None:
        self.assertGreaterEqual(dt.REGISTRATION_WAIT_S, 1.0)
        self.assertLessEqual(dt.REGISTRATION_WAIT_S, 15.0)


# ======================================================================== (4) R2 / R3 refuse before anything is written


class RefusedBeforeThePin(ServerCase):
    def assert_refused(self, wt, code, *flags):
        pins = dt.pins_dir(self.approve_record()["serverId"])
        before = sorted(p.name for p in pins.glob("*")) if pins.is_dir() else []
        started = time.monotonic()
        result = self.append(wt, *flags)
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertLess(time.monotonic() - started, dt.REGISTRATION_WAIT_S + 15, "an exclusion is not waited out")
        self.assertEqual(sorted(p.name for p in pins.glob("*")) if pins.is_dir() else [], before, "no pin file was written")
        self.assertNotIn(self.tab_of(wt), tab_ids(self.approve_record()))
        return result

    def test_a_marker_in_the_worktree_is_worktree_excluded(self) -> None:
        marked = self.worktree("marked", marker=True)
        result = self.assert_refused(marked, "worktree_excluded")
        self.assertIn("worktree_excluded", result.stderr)
        self.assertEqual(result.stdout.strip(), "", "no dead address")

    def test_the_exclude_key_of_the_main_manifest_is_worktree_excluded(self) -> None:
        wtt.write(self.main / ".github" / ".skillsync.yaml",
                  f"skills: [dashboard]\ndashboard:\n  worktrees:\n    exclude: [{json.dumps(str(self.wt1))}]\n")
        result = self.assert_refused(self.wt1, "worktree_excluded")
        self.assertIn("worktree_excluded", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_a_worktree_the_back_link_of_git_does_not_confirm_is_worktree_rejected(self) -> None:
        stray = self.worktree("stray")
        wtt.write(self.entry_dir("stray") / "gitdir", str(self.tmp / "elsewhere" / ".git") + "\n")   # git lists the entry, but not for this directory
        result = self.assert_refused(stray, "worktree_rejected")
        self.assertIn("worktree_rejected", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_a_worktree_whose_git_file_names_another_entry_is_worktree_rejected(self) -> None:
        other = self.worktree("other")
        stray = self.worktree("liar")
        wtt.write(stray / ".git", f"gitdir: {self.entry_dir('other')}\n")
        result = self.assert_refused(stray, "worktree_rejected")
        self.assertIn("worktree_rejected", result.stderr)
        self.assertNotIn(self.tab_of(other), tab_ids(self.approve_record()))

    def test_json_reports_the_code_as_an_object_and_prints_no_address(self) -> None:
        marked = self.worktree("marked-json", marker=True)
        result = self.assert_refused(marked, "worktree_excluded", "--json")
        answer = json.loads(result.stdout)
        self.assertEqual((answer["status"], answer["code"]), ("BLOCKED", "worktree_excluded"))
        self.assertNotIn("url", answer)
        self.assertNotIn("#zgoda", result.stdout)


# ======================================================================== (5) the reader of the pin files


class PinReader(Base):
    """`dashboard_token.read_pins` (the reader of the server) on files placed by hand; the directory is the one of a made-up server id."""

    def setUp(self) -> None:
        super().setUp()
        self.sid = dt.new_server_id()
        self.pins = dt.pins_dir(self.sid)
        self.wt = self.worktree("pinned")
        self.id = self.tab_of(self.wt)

    def body(self, path=None, **extra) -> str:
        return json.dumps({"path": str(path or self.wt), "at": "2026-11-25T10:00:00+00:00", **extra})

    def place(self, name, text="", *, mode=None) -> Path:
        self.pins.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.pins / name
        target.write_text(text, encoding="utf-8")
        return target

    def read(self) -> list:
        return [item["id"] for item in dt.read_pins(self.sid, str(self.main))]

    def test_a_valid_pin_is_read(self) -> None:
        dt.write_pin(self.sid, self.id, self.wt)
        self.assertEqual(self.read(), [self.id])

    def test_a_missing_directory_is_no_pins_and_no_error(self) -> None:
        self.assertEqual(self.read(), [])

    def test_a_file_over_4_kib_is_ignored_and_removed(self) -> None:
        target = self.place(f"{self.id}.json", self.body(padding="x" * 5000))
        self.assertEqual(self.read(), [])
        self.assertFalse(target.exists())

    def test_a_name_that_is_not_the_id_of_the_path_inside_is_ignored_and_removed(self) -> None:
        target = self.place(f"{'0' * 12}.json", self.body())
        self.assertEqual(self.read(), [])
        self.assertFalse(target.exists())

    def test_a_name_that_is_not_an_id_is_ignored(self) -> None:
        for name in ("notanid.json", "ABCDEF123456.json", "abc.json", "main.json.bak", "00000000000g.json"):
            self.place(name, self.body())
        self.assertEqual(self.read(), [])

    def test_a_symlink_instead_of_the_file_is_ignored_and_never_followed(self) -> None:
        real = self.tmp / "outside.json"
        real.write_text(self.body(), encoding="utf-8")
        self.pins.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.symlink(real, self.pins / f"{self.id}.json")
        self.assertEqual(self.read(), [])
        self.assertTrue(real.exists(), "the target of the link is never touched")

    def test_temporary_files_are_skipped(self) -> None:
        self.place(f"{self.id}.json.tmp", self.body())
        self.place(".tmp-abc.tmp", self.body())
        self.assertEqual(self.read(), [])

    def test_garbage_is_ignored_and_removed_without_an_exception(self) -> None:
        names = {}
        for index, text in enumerate(("not json at all", "[1, 2]", "{}", '{"path": 5}', '{"path": ""}', "\x00\x01\x02", "")):
            path = self.tmp / f"ghost{index}"
            names[index] = self.place(f"{dw.tab_id(str(path), str(self.main))}.json", text)
        self.assertEqual(self.read(), [])
        self.assertEqual([p for p in names.values() if p.exists()], [])

    def test_at_most_64_files_are_taken_per_refresh_and_the_rest_removed(self) -> None:
        for index in range(70):
            path = self.tmp / "ghost" / str(index)
            self.place(f"{dw.tab_id(str(path), str(self.main))}.json", self.body(path))
        found = self.read()
        self.assertLessEqual(len(found), 64)
        self.assertLessEqual(len(list(self.pins.glob("*.json"))), 64, "the files beyond the capacity are removed")

    def test_the_limits_are_those_of_the_spec(self) -> None:
        self.assertEqual((dt.MAX_PIN_BYTES, dt.MAX_PIN_FILES, dt.MAX_PINS), (4096, 64, 16))

    def test_the_pin_directory_is_private_and_the_file_atomic(self) -> None:
        target = dt.write_pin(self.sid, self.id, self.wt)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["path"], str(self.wt))
        self.assertEqual([p.name for p in self.pins.iterdir() if p.suffix == ".tmp"], [], "no temporary file is left behind")
        if os.name != "nt":
            self.assertEqual(self.pins.stat().st_mode & 0o777, 0o700)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class PinsOfARunningServer(ServerCase):
    """The server follows the pin directory by itself: a valid pin makes a tab, everything else is dropped and the server stays up."""

    def pin_dir(self) -> Path:
        return dt.pins_dir(self.approve_record()["serverId"])

    def test_a_pin_placed_by_hand_for_a_real_worktree_becomes_a_tab(self) -> None:
        dt.write_pin(self.approve_record()["serverId"], self.tab_of(self.wt2), self.wt2)
        self.wait_for(lambda: self.tab_of(self.wt2) in tab_ids(self.approve_record()), "the pinned worktree gets a tab")

    def test_a_pin_for_a_directory_that_is_no_worktree_of_the_project_is_dropped(self) -> None:
        foreign = self.tmp / "foreign"
        wtt.write(foreign / "readme.txt")
        pin = dt.write_pin(self.approve_record()["serverId"], self.tab_of(foreign), foreign)
        self.wait_for(lambda: not pin.exists(), "the pin of a foreign directory is removed")
        time.sleep(0.5)
        self.assertNotIn(self.tab_of(foreign), tab_ids(self.approve_record()))
        self.assertIsNone(self.proc.poll())

    def test_a_pin_of_an_excluded_worktree_is_dropped(self) -> None:
        marked = self.worktree("late-marked", marker=True)
        pin = dt.write_pin(self.approve_record()["serverId"], self.tab_of(marked), marked)
        self.wait_for(lambda: not pin.exists(), "the pin of an excluded worktree is removed")
        self.assertNotIn(self.tab_of(marked), tab_ids(self.approve_record()))

    def test_every_kind_of_damaged_file_is_ignored_and_the_server_stays_up(self) -> None:
        directory = self.pin_dir()
        directory.mkdir(mode=0o700, exist_ok=True)
        junk = [directory / f"{'f' * 12}.json", directory / "garbage.json", directory / f"{self.tab_of(self.wt1)}.json.tmp"]
        junk[0].write_text("{" + "x" * 6000, encoding="utf-8")
        junk[1].write_text("{}", encoding="utf-8")
        junk[2].write_text(json.dumps({"path": str(self.wt1), "at": "now"}), encoding="utf-8")
        wrong_id = directory / f"{self.tab_of(self.wt2)}.json"
        wrong_id.write_text(json.dumps({"path": str(self.wt1), "at": "now"}), encoding="utf-8")   # the id is of another path
        self.wait_for(lambda: not junk[0].exists() and not junk[1].exists() and not wrong_id.exists(), "damaged pins are removed")
        self.assertEqual(get(self.read_port, "/healthz")[0], 200)
        self.assertIsNone(self.proc.poll())
        self.assertNotIn(self.tab_of(self.wt1), tab_ids(self.approve_record()), "a temporary file is never read as a pin")
        self.assertNotIn(self.tab_of(self.wt2), tab_ids(self.approve_record()), "a pin of another id never registers the path inside")


# ======================================================================== (6) the limit of 16


class TabLimit(Base):
    def test_with_16_pins_a_new_attempt_is_blocked_with_tab_limit(self) -> None:
        server_id, _port, _token = self.fake_server()
        for index in range(16):
            wt = self.worktree(f"w{index:02d}")
            dt.write_pin(server_id, self.tab_of(wt), wt)
        last = self.worktree("one-too-many")
        result = self.cli("serve", str(last))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("tab_limit", result.stderr)
        self.assertEqual(result.stdout.strip(), "", "no dead address")
        self.assertFalse((dt.pins_dir(server_id) / f"{self.tab_of(last)}.json").exists(), "no pin is written over the limit")
        answer = json.loads(self.cli("serve", str(last), "--json").stdout)
        self.assertEqual((answer["status"], answer["code"]), ("BLOCKED", "tab_limit"))


class MainAndFifteenPins(Base):
    """The main tab counts: main + 15 pinned = 16 visible tabs. The 15th join still registers (proved by its id in `tabs[]`), the 16th is `tab_limit`."""

    def test_the_fifteenth_join_registers_and_the_sixteenth_is_tab_limit_at_once(self) -> None:
        _proc, _read, approve = self.start_project()
        for index in range(15):
            wt = self.worktree(f"j{index:02d}")
            result = self.cli("serve", str(wt))
            self.assertEqual(result.returncode, 0, (index, result.stderr))
            self.assertIn(self.tab_of(wt), tab_ids(self.record_at(approve)), f"join {index + 1} is a tab")
        self.assertEqual(len(tab_ids(self.record_at(approve))), 16)
        last = self.worktree("sixteenth")
        started = time.monotonic()
        result = self.cli("serve", str(last))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("tab_limit", result.stderr)
        self.assertNotIn("registration_timeout", result.stderr)
        self.assertLess(time.monotonic() - started, dt.REGISTRATION_WAIT_S, "refused by the command, not after a wait")
        self.assertEqual(result.stdout.strip(), "")
        self.assertNotIn(self.tab_of(last), tab_ids(self.record_at(approve)))

    def test_with_a_fake_server_15_pins_and_main_are_already_the_limit(self) -> None:
        server_id, _port, _token = self.fake_server()
        for index in range(15):
            wt = self.worktree(f"w{index:02d}")
            dt.write_pin(server_id, self.tab_of(wt), wt)
        result = self.cli("serve", str(self.worktree("the-16th")))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("tab_limit", result.stderr)


class PinnedWinsOverUnpinnedAtTheLimit(Base):
    def test_a_pinned_directory_gets_a_tab_when_the_visible_tabs_are_full(self) -> None:
        for index in range(17):
            self.worktree(f"busy{index:02d}", runs=True)
        latecomer = self.worktree("latecomer")
        _proc, _read, approve = self.start_project()
        self.assertLessEqual(len(tab_ids(self.record_at(approve))), 16)
        self.assertIn("main", tab_ids(self.record_at(approve)))
        result = self.cli("serve", str(latecomer))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.record_at(approve)
        self.assertIn(self.tab_of(latecomer), tab_ids(record), "the pinned directory has precedence over tabs without a pin")
        self.assertIn("main", tab_ids(record))
        self.assertLessEqual(len(tab_ids(record)), 16)


# ======================================================================== (7) flags of the settings: a server of its own


class PlanDecision(Base):
    """The decision of `plan_serve` (join or start, and of which kind) without opening a socket; the servers are state files."""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.worktree("decided")
        healthy = mock.patch.object(dt, "_healthy", lambda host, port, expected=None, timeout=1.0: True)
        healthy.start()
        self.addCleanup(healthy.stop)

    def running(self, **kwargs):
        project = dw.project_identity(str(self.main))
        sid = dt.new_server_id()
        tabs = [{"id": "main", "path": str(self.main), "token": "main.tok" + "0" * 20}]
        dt.write_state(self.main, 5185, "tok" + "0" * 20, "127.0.0.1", server_id=sid, common_dir=project.common_dir, tabs=tabs,
                       **{"kind": "project", **kwargs})
        return sid

    def test_without_a_flag_a_running_project_server_is_joined(self) -> None:
        self.running()
        plan = dt.plan_serve(serve_args(self.wt))
        self.assertEqual(plan.mode, "append")

    def test_every_flag_of_the_settings_gives_a_server_of_its_own_next_to_a_running_one(self) -> None:
        self.running()
        for flag, (attribute, value) in FLAG_ATTRIBUTES.items():
            with self.subTest(flag=flag):
                plan = dt.plan_serve(serve_args(self.wt, **{attribute: value}))
                self.assertEqual((plan.mode, plan.kind), ("start", "standalone"), flag)
                self.assertTrue(plan.notes, f"{flag}: the user is told that a server of its own starts")

    def test_the_standalone_flag_and_no_approvals_start_a_server_of_their_own(self) -> None:
        self.running()
        self.assertEqual(dt.plan_serve(serve_args(self.wt, standalone=True)).mode, "start")
        self.assertEqual(dt.plan_serve(serve_args(self.wt, no_approvals=True)).kind, "standalone")

    def test_a_flag_of_the_tab_does_not_stop_the_join(self) -> None:
        self.running()
        self.assertEqual(dt.plan_serve(serve_args(self.wt, plan="docs/plans/x.md", json=True, all_tabs=True)).mode, "append")

    def test_the_default_poll_is_not_a_setting(self) -> None:
        self.running()
        self.assertEqual(dt.plan_serve(serve_args(self.wt, poll=1.0)).mode, "append")

    def test_without_a_running_server_every_flag_is_allowed_for_a_new_project_server(self) -> None:
        for flag, (attribute, value) in FLAG_ATTRIBUTES.items():
            with self.subTest(flag=flag):
                pair = {"read_port": 5191, "approve_port": 5192} if flag in ("--read-port", "--approve-port") else {}   # both sockets: a lone `--read-port` is a read-only server (m6)
                plan = dt.plan_serve(serve_args(self.wt, **{attribute: value, **pair}))
                self.assertEqual(plan.mode, "start")
                self.assertEqual(plan.kind, "standalone" if flag in STANDALONE_WITHOUT_A_SERVER else "project", flag)

    def test_standalone_flag_without_a_server_is_standalone(self) -> None:
        self.assertEqual(dt.plan_serve(serve_args(self.wt, standalone=True, port=5190)).kind, "standalone")

    def test_a_standalone_server_is_never_the_target_of_a_join(self) -> None:
        self.running(kind="standalone")
        self.assertEqual(dt.plan_serve(serve_args(self.wt)).mode, "start")

    def test_a_server_of_another_project_is_never_the_target(self) -> None:
        other = self.tmp / "other-project"
        wtt.write(other / ".git" / "HEAD", "ref: refs/heads/main\n")
        wtt.write(other / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        project = dw.project_identity(str(other))
        dt.write_state(other, 5186, "tok" + "1" * 20, "127.0.0.1", server_id=dt.new_server_id(), kind="project", common_dir=project.common_dir,
                       tabs=[{"id": "main", "path": str(other), "token": "main.tok" + "1" * 20}])
        self.assertEqual(dt.plan_serve(serve_args(self.wt)).mode, "start")

    def test_the_common_directory_decides_not_the_name_of_the_state_file(self) -> None:
        self.running()   # the state file is named after the main directory; the worktree has another digest
        self.assertNotEqual(dt.state_path(self.wt, 5185), dt.state_path(self.main, 5185))
        self.assertEqual(dt.plan_serve(serve_args(self.wt)).mode, "append")

    def test_a_directory_outside_git_is_never_joined(self) -> None:
        self.running()
        plain = self.tmp / "plain"
        wtt.write(plain / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        self.assertEqual(dt.plan_serve(serve_args(plain)).mode, "start")


class FlagsStartAServerOfTheirOwn(ServerCase):
    """The three representatives, with real processes: a port, a preview without consents, a preview with another layer."""

    def standalone(self, *extra, layer=()):
        proc, port = start_serve(self, CLI, self.main, list(layer), self.env, free_port, *extra)
        self.servers.append(proc)
        return proc, port

    def test_a_port_flag_starts_a_standalone_server_and_warns(self) -> None:
        proc, port = self.standalone()
        record = self.record_at(port)
        self.assertEqual(record["kind"], "standalone")
        self.assertEqual(len(record["tabs"]), 1)
        self.assertEqual(wtt.real(record["tabs"][0]["path"]), wtt.real(self.main))
        _out, err, _graceful = stop_serve(proc)
        self.assertTrue(str(self.read_port) in err or str(self.approve_port) in err, "the warning names the project server")
        self.assertIn("--port", err)

    def test_no_approvals_starts_a_standalone_server_without_consents(self) -> None:
        proc, port = self.standalone("--no-approvals")
        record = self.record_at(port)
        self.assertEqual((record["kind"], record["approvals"]), ("standalone", False))
        stop_serve(proc)

    def test_a_preview_with_another_layer_is_standalone_and_never_takes_the_cards_tabs(self) -> None:
        _proc, port = self.standalone("--no-approvals", layer=["--layer", f"core={ROOT / 'library'}"])
        self.assertEqual(self.record_at(port)["kind"], "standalone")
        result = self.appended(self.wt1)
        self.assertEqual(ADDRESS.match(self.last_line(result))["port"], str(self.approve_port), "the tab joins the project server, not the preview")
        self.assertEqual(len(self.record_at(port)["tabs"]), 1)

    def test_the_standalone_flag_starts_a_standalone_server(self) -> None:
        _proc, port = self.standalone("--standalone")
        self.assertEqual(self.record_at(port)["kind"], "standalone")
        self.assertRegex(self.record_at(port)["tabs"][0]["id"], rf"^{TAB}$")

    def test_a_standalone_server_is_never_the_target_of_a_later_join(self) -> None:
        self.standalone()
        result = self.appended(self.wt2)
        self.assertEqual(ADDRESS.match(self.last_line(result))["port"], str(self.approve_port))
        self.assertIn(self.tab_of(self.wt2), tab_ids(self.approve_record()))


# ======================================================================== (8) no project server: start one


class StartsAProjectServer(Base):
    def test_started_in_a_worktree_it_is_a_project_server_with_the_main_tab_and_the_tab_of_the_directory(self) -> None:
        wt = self.worktree("starter")
        _proc, _read, approve = self.start_project(wt)
        record = self.record_at(approve)
        self.assertEqual(record["kind"], "project")
        self.assertEqual(wtt.real(record["commonDir"]), wtt.real(self.common))
        self.assertEqual(sorted(tab_ids(record)), sorted(["main", self.tab_of(wt)]))
        self.assertTrue(SERVER_ID.match(record["serverId"]))
        self.assertEqual(record["schema"], 2)

    def test_started_in_the_main_directory_the_flags_of_the_settings_are_kept(self) -> None:
        _proc, read, approve = self.start_project(self.main)
        self.assertEqual(self.record_at(read)["bind"], "127.0.0.1")
        self.assertEqual(self.record_at(approve)["kind"], "project")
        self.assertEqual(tab_ids(self.record_at(approve)), ["main"])

    def test_the_second_start_without_flags_joins_the_first(self) -> None:
        wt = self.worktree("second")
        _proc, _read, approve = self.start_project(self.main)
        result = self.cli("serve", str(wt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([r for r in self.records() if r["kind"] == "project"]), 2, "one server = two sockets = two state files, no third")
        self.assertIn(self.tab_of(wt), tab_ids(self.record_at(approve)))

    def test_the_start_prints_the_address_of_the_tab_with_its_own_token(self) -> None:
        proc, _read, approve = self.start_project(self.main)
        record = self.record_at(approve)
        _out, err, _graceful = stop_serve(proc)
        main_token = next(t["token"] for t in record["tabs"] if t["id"] == "main")
        self.assertIn(f"/?tab=main#zgoda={main_token}", err)


# ======================================================================== (9) servers of schema 1 and 2


class SchemaOneAndTwo(Base):
    def test_servers_reads_both_schemas(self) -> None:
        sid2, port2, _token2 = self.fake_server(root=self.main)
        other = self.tmp / "old-project"
        wtt.write(other / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        _sid1, port1, _token1 = self.fake_server(schema=1, root=other)
        found = {item["port"]: item for item in dt.servers(self.main)}
        self.assertEqual(set(found), {port2})
        self.assertEqual((found[port2]["schema"], found[port2]["serverId"]), (2, sid2))
        old = {item["port"]: item for item in dt.servers(other)}
        self.assertEqual(set(old), {port1})
        self.assertEqual(old[port1]["schema"], 1)

    def test_a_server_of_schema_1_of_the_same_project_is_never_joined(self) -> None:
        self.fake_server(schema=1)
        wt = self.worktree("beside-old")
        plan = dt.plan_serve(serve_args(wt))
        self.assertEqual(plan.mode, "start")

    def test_a_schema_1_file_beside_no_project_server_ends_in_legacy_server_or_a_separate_start_never_a_join(self) -> None:
        wt = self.worktree("beside-legacy")
        project = dw.project_identity(str(self.main))
        dt.write_state(self.main, ports.DEFAULT_READ_PORT, None, "127.0.0.1", role="read", peer=ports.DEFAULT_APPROVE_PORT)
        dt.write_state(self.main, ports.DEFAULT_APPROVE_PORT, "tok" + "2" * 20, "127.0.0.1", role="approve", peer=ports.DEFAULT_READ_PORT)
        self.assertEqual(dw.normalize_key(project.common_dir), dw.normalize_key(self.common))
        out = io.StringIO()
        with mock.patch.object(dt, "_healthy", lambda host, port, expected=None, timeout=1.0: True), \
                mock.patch.object(ports, "port_taken", return_value=False), \
                mock.patch.object(srv, "make_server", side_effect=AssertionError("a server was started on the default ports")), \
                mock.patch("sys.stdout", new=out), mock.patch("sys.stderr", new=io.StringIO()):
            code = srv.serve_command(serve_args(wt, json=True))
        self.assertEqual(code, 2, out.getvalue())
        answer = json.loads(out.getvalue())
        self.assertEqual((answer["status"], answer.get("code")), ("BLOCKED", "legacy_server"))
        self.assertNotIn("url", answer)

    def test_a_server_of_schema_2_is_not_seen_as_one_of_another_root(self) -> None:
        self.fake_server()
        elsewhere = self.tmp / "elsewhere"
        wtt.write(elsewhere / "x.txt")
        self.assertEqual(dt.servers(elsewhere), [])


# ======================================================================== (10) ambiguous_server


class AmbiguousServer(Base):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.worktree("ambiguous")
        self.first = self.fake_server()
        self.second = self.fake_server()

    def assert_ambiguous(self, result):
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        text = result.stdout + result.stderr
        self.assertIn("ambiguous_server", text)
        self.assertIn(str(self.first[1]), text)
        self.assertIn(str(self.second[1]), text)
        self.assertIn("--port", text)
        self.assertNotIn("#zgoda", text)

    def test_serve_refuses_to_guess_and_names_the_ports(self) -> None:
        self.assert_ambiguous(self.cli("serve", str(self.wt)))

    def test_serve_json_refuses_with_an_object(self) -> None:
        result = self.cli("serve", str(self.wt), "--json")
        self.assert_ambiguous(result)
        self.assertEqual(json.loads(result.stdout)["code"], "ambiguous_server")

    def test_url_refuses_to_guess_and_names_the_ports(self) -> None:
        self.assert_ambiguous(self.cli("url", str(self.main), "--layer", f"core={ROOT / 'library'}"))

    def test_url_with_a_port_names_one_of_them(self) -> None:
        result = self.cli("url", str(self.main), "--port", str(self.second[1]), "--layer", f"core={ROOT / 'library'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f":{self.second[1]}/", result.stdout)
        self.assertIn(self.second[2], result.stdout)

    def test_the_two_sockets_of_one_server_are_one_server_not_two(self) -> None:
        sid = self.first[0]
        health, port = bind_free(lambda p: ThreadingHTTPServer(("127.0.0.1", p), HealthOnly), free_port)
        health.body = {"ok": True, "version": "0", "serverId": sid}
        threading.Thread(target=health.serve_forever, daemon=True).start()
        self.addCleanup(health.server_close)
        self.addCleanup(health.shutdown)
        project = dw.project_identity(str(self.main))
        dt.write_state(self.main, port, None, "127.0.0.1", role="read", server_id=sid, kind="project", common_dir=project.common_dir,
                       tabs=[{"id": "main", "path": str(self.main)}])
        found = dt.servers_for_project(project.common_dir)
        self.assertEqual(len([item for item in found if item["serverId"] == sid]), 2, "two sockets of the first server")
        self.assertEqual(len({item["serverId"] for item in found}), 2, "but only two servers: the two sockets are one")


# ======================================================================== (11) dashboard url


class UrlOfATab(ServerCase):
    def url(self, path, *flags):
        return self.cli("url", str(path), "--layer", f"core={ROOT / 'library'}", *flags)

    def test_the_default_is_the_project_server_and_the_tab_of_the_directory_with_its_own_token(self) -> None:
        self.appended(self.wt1)
        result = self.url(self.wt1)
        self.assertEqual(result.returncode, 0, result.stderr)
        match = ADDRESS.match(result.stdout.strip().splitlines()[-1])
        self.assertIsNotNone(match, result.stdout)
        self.assertEqual((match["tab"], int(match["port"])), (self.tab_of(self.wt1), self.approve_port))
        record = self.approve_record()
        self.assertEqual(next(t["token"] for t in record["tabs"] if t["id"] == match["tab"]), f"{match['tab']}.{match['token']}")
        for token in self.tokens_of(record) - {f"{match['tab']}.{match['token']}", match["token"]}:
            self.assertNotIn(token, result.stdout, "only the own pair")

    def test_the_main_directory_has_the_main_tab(self) -> None:
        match = ADDRESS.match(self.url(self.main).stdout.strip().splitlines()[-1])
        self.assertEqual(match["tab"], "main")

    def test_all_tabs_prints_every_pair_with_the_own_one_last(self) -> None:
        self.appended(self.wt1)
        result = self.url(self.wt1, "--all-tabs")
        lines = [line for line in result.stdout.strip().splitlines() if line.startswith("http")]
        self.assertEqual(sorted(ADDRESS.match(line)["tab"] for line in lines), sorted(tab_ids(self.approve_record())))
        self.assertEqual(ADDRESS.match(lines[-1])["tab"], self.tab_of(self.wt1))

    def test_json_is_an_object_with_the_own_address(self) -> None:
        self.appended(self.wt1)
        answer = json.loads(self.url(self.wt1, "--json").stdout)
        self.assertEqual(answer["status"], "PASS")
        self.assertEqual(ADDRESS.match(answer["url"])["tab"], self.tab_of(self.wt1))

    def test_a_directory_without_a_tab_is_unknown_tab(self) -> None:
        result = self.url(self.wt2)
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("unknown_tab", result.stdout + result.stderr)
        self.assertNotIn("#zgoda", result.stdout)
        answer = json.loads(self.url(self.wt2, "--json").stdout)
        self.assertEqual((answer["status"], answer["code"]), ("BLOCKED", "unknown_tab"))

    def test_url_does_not_add_a_tab(self) -> None:
        self.url(self.wt2)
        self.assertNotIn(self.tab_of(self.wt2), tab_ids(self.approve_record()))

    def test_a_standalone_server_is_not_the_default_answer_but_a_port_names_it(self) -> None:
        proc, port = start_serve(self, CLI, self.main, [], self.env, free_port)
        self.servers.append(proc)
        standalone = self.record_at(port)
        default = self.url(self.main)
        self.assertEqual(int(ADDRESS.match(default.stdout.strip().splitlines()[-1])["port"]), self.approve_port, "the default is the project server")
        named = self.url(self.main, "--port", str(port))
        self.assertEqual(named.returncode, 0, (named.stdout, named.stderr))
        line = named.stdout.strip().splitlines()[-1]
        self.assertIn(f":{port}/", line)
        fragment = line.split("#zgoda=", 1)[1]
        self.assertIn(fragment, self.tokens_of(standalone) | {t["token"] for t in standalone["tabs"] if t.get("token")}, "a token of that server")
        self.assertNotIn(self.tokens_of(self.approve_record()).pop(), line)

    def test_a_standalone_server_names_only_the_tab_of_its_directory(self) -> None:
        proc, port = start_serve(self, CLI, self.main, [], self.env, free_port)
        self.servers.append(proc)
        result = self.url(self.wt1, "--port", str(port))
        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertIn("unknown_tab", result.stdout + result.stderr)


class UrlWithOnlyAStandaloneServer(Base):
    def test_the_default_is_blocked_in_a_git_project_and_a_port_names_the_server(self) -> None:
        wtt.write(self.main / "x.txt")
        proc, port = start_serve(self, CLI, self.main, [], self.env, free_port, "--standalone")
        self.servers.append(proc)
        self.assertEqual(self.record_at(port)["kind"], "standalone")
        layer = ["--layer", f"core={ROOT / 'library'}"]
        blocked = self.cli("url", str(self.main), "--json", *layer)
        self.assertEqual(blocked.returncode, 2, (blocked.stdout, blocked.stderr))
        self.assertEqual(json.loads(blocked.stdout)["status"], "BLOCKED")
        self.assertNotIn("#zgoda", blocked.stdout)
        named = self.cli("url", str(self.main), "--port", str(port), *layer)
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assertIn(f":{port}/", named.stdout)



class UrlOutsideAGitProject(Base):
    """Without a project git (plain directory, submodule, bare flag) `url` is what it was: the server found by the root, the address of earlier versions."""

    def dirs(self):
        plain = self.tmp / "plain"
        wtt.write(plain / "readme.md")
        submodule = self.tmp / "super" / "sub"
        wtt.write(submodule / ".git", "gitdir: ../.git/modules/sub\n")
        wtt.write(self.tmp / "super" / ".git" / "modules" / "sub" / "HEAD", "ref: refs/heads/main\n")
        bare = self.tmp / "flagged"
        wtt.write(bare / ".git" / "HEAD", "ref: refs/heads/main\n")
        wtt.write(bare / ".git" / "config", "[core]\n\tbare = true\n")
        return {"plain": plain, "submodule": submodule, "bare": bare}

    def test_the_address_of_a_server_found_by_the_root_is_the_old_one_with_the_bare_token(self) -> None:
        for index, (name, folder) in enumerate(self.dirs().items()):
            with self.subTest(kind=name):
                wtt.write(folder / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
                self.assertIsNone(dw.project_identity(str(folder)))
                port = 5170 + index
                token = "tok-" + name + "-" + "0" * 20
                dt.write_state(folder, port, token)
                out = io.StringIO()
                with mock.patch.object(dt, "_healthy", lambda host, port, expected=None, timeout=1.0: True), mock.patch("sys.stdout", new=out):
                    code = dt.url_command(url_args(folder))
                self.assertEqual((code, out.getvalue().strip()), (0, f"http://127.0.0.1:{port}/#zgoda={token}"))

    def test_a_server_of_schema_2_of_a_plain_directory_is_found_by_the_root_too(self) -> None:
        folder = self.dirs()["plain"]
        wtt.write(folder / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        dt.write_state(folder, 5175, "tok-two-" + "1" * 20, server_id=dt.new_server_id(), kind="standalone",
                       tabs=[{"id": "main", "path": str(folder), "token": "main.tok-two-" + "1" * 20}])
        out = io.StringIO()
        with mock.patch.object(dt, "_healthy", lambda host, port, expected=None, timeout=1.0: True), mock.patch("sys.stdout", new=out):
            code = dt.url_command(url_args(folder))
        self.assertEqual(code, 0)
        self.assertIn(":5175/", out.getvalue())
        self.assertIn("#zgoda=", out.getvalue())

    def test_no_server_is_server_not_running_as_before(self) -> None:
        folder = self.dirs()["plain"]
        wtt.write(folder / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        out = io.StringIO()
        with mock.patch("sys.stdout", new=out):
            code = dt.url_command(url_args(folder, json=True))
        self.assertEqual((code, json.loads(out.getvalue())["code"]), (2, "server_not_running"))


# ======================================================================== (12) /healthz and the identity of a process


class Healthz(ServerCase):
    def test_both_sockets_answer_with_the_server_id_and_never_with_a_token_id(self) -> None:
        answers = []
        for port in (self.read_port, self.approve_port):
            status, text = get(port, "/healthz")
            self.assertEqual(status, 200)
            answers.append(json.loads(text))
        for answer in answers:
            self.assertTrue(SERVER_ID.match(answer["serverId"]), answer)
            self.assertNotIn("tokenId", answer)
            self.assertIs(answer["ok"], True)
        self.assertEqual(answers[0]["serverId"], answers[1]["serverId"], "one process, one id, on both sockets")
        self.assertEqual(answers[0]["serverId"], self.approve_record()["serverId"])

    def test_the_server_id_is_not_a_secret_derived_from_the_token(self) -> None:
        answer = json.loads(get(self.read_port, "/healthz")[1])
        for token in self.tokens_of(self.approve_record()):
            self.assertNotIn(token, json.dumps(answer))

    def test_the_server_id_differs_per_process(self) -> None:
        proc, port = start_serve(self, CLI, self.main, [], self.env, free_port)
        self.servers.append(proc)
        other = json.loads(get(port, "/healthz")[1])["serverId"]
        self.assertNotEqual(other, self.approve_record()["serverId"])

    def test_the_server_id_changes_with_a_restart(self) -> None:
        first = self.approve_record()["serverId"]
        stop_serve(self.proc)
        _proc, _read, approve = self.start_project()
        self.assertNotEqual(self.record_at(approve)["serverId"], first)


class ForeignProcessOnThePort(Base):
    def test_servers_drops_a_process_whose_server_id_is_not_the_one_in_the_file(self) -> None:
        self.fake_server(health_server_id="0" * 16)
        self.assertEqual(dt.servers(self.main), [])
        self.assertEqual(dt.servers_for_project(dw.project_identity(str(self.main)).common_dir), [])

    def test_servers_keeps_the_process_whose_server_id_matches(self) -> None:
        sid, port, _token = self.fake_server()
        self.assertEqual([(item["port"], item["serverId"]) for item in dt.servers(self.main)], [(port, sid)])

    def test_a_joining_command_does_not_trust_a_stranger_on_the_port(self) -> None:
        self.fake_server(health_server_id="f" * 16)
        wt = self.worktree("stranger-neighbour")
        project = dw.project_identity(str(self.main))
        self.assertEqual(dt.servers_for_project(project.common_dir), [])
        plan = dt.plan_serve(serve_args(wt))
        self.assertEqual(plan.mode, "start")


# ======================================================================== (13) the JSON of the status


class StatusJson(ServerCase):
    REQUIRED = {"id", "name", "path", "kind", "branch", "head", "current", "lastActivity", "waiting"}

    def status(self, query=""):
        code, text = get(self.read_port, "/api/status" + query)
        self.assertEqual(code, 200, text)
        return json.loads(text), text

    def test_one_tab_has_no_worktrees_field(self) -> None:
        data, _text = self.status()
        self.assertNotIn("worktrees", data)
        self.assertEqual(data["schema"], 1)

    def test_several_tabs_have_the_field_with_every_tab_and_the_contract_fields(self) -> None:
        self.appended(self.wt1)
        data, _text = self.status()
        self.assertEqual(data["schema"], 1)
        listed = data["worktrees"]
        self.assertEqual(sorted(item["id"] for item in listed), sorted(tab_ids(self.approve_record())))
        for item in listed:
            self.assertTrue(self.REQUIRED <= set(item), item)
            self.assertRegex(item["id"], rf"^{TAB}$")
            self.assertIsInstance(item["current"], bool)
            self.assertIsInstance(item["waiting"], bool)
            self.assertIsInstance(item["name"], str)
            self.assertTrue(item["lastActivity"] is None or isinstance(item["lastActivity"], str))
        names = {item["id"]: item["path"] for item in listed}
        self.assertEqual(wtt.real(names[self.tab_of(self.wt1)]), wtt.real(self.wt1))
        self.assertEqual(next(item for item in listed if item["id"] == "main")["kind"], "main")
        self.assertNotEqual(next(item for item in listed if item["id"] == self.tab_of(self.wt1))["kind"], "main")

    def test_the_branch_of_a_worktree_is_its_own(self) -> None:
        self.appended(self.wt1)
        listed = {item["id"]: item for item in self.status()[0]["worktrees"]}
        self.assertEqual(listed["main"]["branch"], "main")
        self.assertEqual(listed[self.tab_of(self.wt1)]["branch"], "wt1")

    def test_the_status_of_a_tab_carries_the_same_list_and_no_token_anywhere(self) -> None:
        self.appended(self.wt1)
        tokens = self.tokens_of(self.approve_record())
        for query in ("", f"?tab={self.tab_of(self.wt1)}", "?tab=main"):
            data, text = self.status(query)
            self.assertIn("worktrees", data, query)
            for token in tokens:
                self.assertNotIn(token, text, query)
            for item in data["worktrees"]:
                self.assertFalse({key for key in item if "token" in key.lower()}, item)

    def test_show_does_not_have_the_field_whatever_the_server_has(self) -> None:
        self.appended(self.wt1)
        result = self.cli("show", str(self.main), "--layer", f"core={ROOT / 'library'}")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertNotIn("worktrees", data)
        self.assertEqual(data["schema"], 1)

    def test_a_server_of_a_directory_outside_git_has_no_field(self) -> None:
        plain = self.tmp / "plain-tab"
        wtt.write(plain / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        proc, port = start_serve(self, CLI, plain, [], self.env, free_port)
        self.servers.append(proc)
        data = json.loads(get(port, "/api/status")[1])
        self.assertNotIn("worktrees", data)


# ======================================================================== (14) clean-up


class CleanUp(Base):
    @unittest.skipIf(os.name == "nt", "the graceful stop is SIGTERM; Windows kills the process (see `stop_serve`)")
    def test_a_stopped_server_removes_its_state_files_and_its_pin_directory(self) -> None:
        wt = self.worktree("tidy")
        proc, _read, approve = self.start_project()
        server_id = self.record_at(approve)["serverId"]
        result = self.cli("serve", str(wt))
        self.assertEqual(result.returncode, 0, result.stderr)
        pins = dt.pins_dir(server_id)
        self.assertTrue(pins.is_dir() and list(pins.glob("*.json")), "precondition: the join left a pin")
        _out, err, graceful = stop_serve(proc)
        self.assertTrue(graceful)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual([r for r in self.records() if r.get("serverId") == server_id], [])
        self.assertFalse(pins.exists())

    def test_the_pin_directory_of_a_killed_server_is_swept_at_the_next_start(self) -> None:
        wt = self.worktree("orphan")
        proc, _read, approve = self.start_project()
        server_id = self.record_at(approve)["serverId"]
        self.assertEqual(self.cli("serve", str(wt)).returncode, 0)
        pins = dt.pins_dir(server_id)
        self.assertTrue(pins.is_dir(), "precondition")
        kill_tree(proc)
        proc.wait(timeout=15)
        self.assertTrue(pins.is_dir(), "precondition: a killed server cannot clean up")
        self.start_project()
        self.wait_for(lambda: not pins.exists(), "the directory of the dead server is swept by the new one", seconds=15)

    def test_a_sweep_keeps_the_pin_directory_of_a_living_server_and_removes_it_once_the_server_is_dead(self) -> None:
        wt = self.worktree("living")
        proc, _read, approve = self.start_project()
        server_id = self.record_at(approve)["serverId"]
        self.assertEqual(self.cli("serve", str(wt)).returncode, 0)
        pins = dt.pins_dir(server_id)
        self.assertTrue(pins.is_dir(), "precondition: the join left a pin directory")
        dt.sweep_pins()   # called directly: a start of a server of another kind would not even reach it
        self.assertTrue(pins.is_dir(), "the directory of a living server stays")
        kill_tree(proc)
        proc.wait(timeout=15)
        dt.sweep_pins()
        self.assertFalse(pins.exists(), "the same sweep removes it when the server is dead")

    def test_a_sweep_does_not_follow_a_state_directory_that_is_a_link(self) -> None:
        """n1: a state directory that is a link (or not the user's own) is not a place to sweep in."""
        real = self.tmp / "elsewhere-state"
        victim = child(real, f"{dt.new_server_id()}-pins")
        wtt.write(victim / "main.json", "{}")
        state = dt.state_dir()
        shutil.rmtree(state, ignore_errors=True)
        state.parent.mkdir(parents=True, exist_ok=True)
        self.symlink(real, state)
        dt.sweep_pins()
        self.assertTrue(victim.is_dir(), "nothing is removed through a linked state directory")


# ======================================================================== (15) races


class Races(ServerCase):
    def test_parallel_joins_of_one_tab_give_one_address_and_one_entry(self) -> None:
        procs = [subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(self.wt1)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=self.env) for _ in range(3)]
        outputs = []
        for proc in procs:
            self.addCleanup(lambda proc=proc: proc.poll() is None and proc.kill())
            out, err = proc.communicate(timeout=90)
            outputs.append((proc.returncode, out, err))
        for code, out, err in outputs:
            self.assertEqual(code, 0, err)
        self.assertEqual(len({out.strip().splitlines()[-1] for _code, out, _err in outputs}), 1, "the same address for every joiner")
        self.assertEqual(tab_ids(self.approve_record()).count(self.tab_of(self.wt1)), 1)

    def test_parallel_joins_of_two_tabs_each_get_their_own(self) -> None:
        procs = [subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=self.env) for path in (self.wt1, self.wt2)]
        lines = []
        for proc in procs:
            self.addCleanup(lambda proc=proc: proc.poll() is None and proc.kill())
            out, err = proc.communicate(timeout=90)
            self.assertEqual(proc.returncode, 0, err)
            lines.append(out.strip().splitlines()[-1])
        self.assertEqual(sorted(ADDRESS.match(line)["tab"] for line in lines), sorted([self.tab_of(self.wt1), self.tab_of(self.wt2)]))
        self.assertEqual(sorted(tab_ids(self.approve_record())), sorted(["main", self.tab_of(self.wt1), self.tab_of(self.wt2)]))


class LostBindNextToAProjectServer(ServerCase):
    def test_the_constants_of_the_retry_are_those_of_the_spec(self) -> None:
        self.assertEqual((dt.RACE_RETRIES, dt.RACE_DELAY_S), (3, 0.2))

    def test_a_start_without_flags_beside_no_server_may_race_and_a_start_with_flags_may_not(self) -> None:
        stop_serve(self.proc)
        self.wait_for(lambda: not self.records(), "the server is gone")
        self.assertTrue(dt.plan_serve(serve_args(self.wt1)).race)
        self.assertFalse(dt.plan_serve(serve_args(self.wt1, port=5190)).race)

    def test_the_retry_joins_a_server_that_appeared_after_the_lost_bind(self) -> None:
        plan = dt.plan_serve(serve_args(self.wt1))
        plan.live = []
        self.assertEqual(plan.mode, "append", "precondition: the server of the setUp runs")
        plan.mode = "start"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dt.retry_append(serve_args(self.wt1), plan, attempts=3, delay=0.05)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(ADDRESS.match(out.getvalue().strip().splitlines()[-1])["tab"], self.tab_of(self.wt1))

    def test_the_retry_gives_up_after_three_attempts_when_no_server_appears(self) -> None:
        stop_serve(self.proc)
        self.wait_for(lambda: not self.records(), "the server is gone")
        plan = dt.plan_serve(serve_args(self.wt1))
        looks = []
        real = dt.servers_for_project
        with mock.patch.object(dt, "servers_for_project", lambda *a, **k: looks.append(1) or real(*a, **k)):
            self.assertIsNone(dt.retry_append(serve_args(self.wt1), plan, delay=0.01))
        self.assertEqual(len(looks), dt.RACE_RETRIES)


# ======================================================================== (16) tokens stay out of the logs


class TokenHygiene(Base):
    def test_a_token_of_a_joined_tab_is_in_no_log_and_no_stderr_and_the_state_is_private(self) -> None:
        wt1, wt2 = self.worktree("hy1"), self.worktree("hy2")
        proc, read, approve = self.start_project(self.main, "--verbose")
        joined = [self.cli("serve", str(wt)) for wt in (wt1, wt2)]
        urls = [self.cli("url", str(wt), "--layer", f"core={ROOT / 'library'}") for wt in (wt1, wt2)]
        record = self.record_at(approve)
        for path in ("/", "/healthz", "/api/status", f"/api/status?tab={self.tab_of(wt1)}", "/api/tabs", "/assets/app.js"):
            get(read, path)
        get(approve, "/api/status")
        main_tokens = {t["token"] for t in record["tabs"] if t["id"] == "main"}
        other_tokens = self.tokens_of(record) - main_tokens - {t.split(".", 1)[-1] for t in main_tokens}
        self.assertGreaterEqual(len(other_tokens), 4, "precondition: two tabs with a pair and a bare hmac each")
        for result in joined + urls:
            self.assertEqual(result.returncode, 0, result.stderr)
            for token in self.tokens_of(record):
                self.assertNotIn(token, result.stderr, "stderr of a command never carries a token")
            self.assertEqual(len(re.findall(r"#zgoda=", result.stdout)), 1)
        _out, log, _graceful = stop_serve(proc)
        for token in other_tokens:
            self.assertNotIn(token, log, "the log of the server never names a token of a joined tab")
        main_hmac = record["token"]
        self.assertEqual(log.count(main_hmac), 1, "control: the scan sees a token; the start banner of the default tab is the only place")

    def test_state_files_and_their_directory_are_private(self) -> None:
        wt = self.worktree("private")
        _proc, read, approve = self.start_project()
        self.assertEqual(self.cli("serve", str(wt)).returncode, 0)
        if os.name == "nt":
            return   # the owner-only ACL is not a POSIX mode; `test_skillsync_dashboard_approve_token` covers the ACL helper
        self.assertEqual(self.record_at(approve)["_file"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.record_at(read)["_file"].stat().st_mode & 0o777, 0o600)
        self.assertEqual(dt.state_dir().stat().st_mode & 0o777, 0o700)

    def test_no_response_of_the_server_carries_a_token(self) -> None:
        wt = self.worktree("clean")
        _proc, read, approve = self.start_project()
        self.assertEqual(self.cli("serve", str(wt)).returncode, 0)
        record = self.record_at(approve)
        tokens = self.tokens_of(record)
        self.assertTrue(tokens)
        for port in (read, approve):
            for path in ("/", "/index.html", "/healthz", "/api/status", f"/api/status?tab={self.tab_of(wt)}", "/api/tabs", "/api/descriptions",
                         "/assets/app.js", "/assets/app.css"):
                with self.subTest(port=port, path=path):
                    status, text = get(port, path)
                    for token in tokens:
                        self.assertNotIn(token, text)
                    del status

    def test_the_read_socket_file_has_no_token_at_all(self) -> None:
        _proc, read, _approve = self.start_project()
        record = self.record_at(read)
        self.assertEqual(self.tokens_of(record), set())
        self.assertNotIn("token", record)
        self.assertNotIn("tokenId", record)



# ======================================================================== review round 1: fail-closed settings, odd files, guards


class MainDirectoryGone(Base):
    """m1: with no main working directory only the marker excludes: the keys of `dashboard.worktrees.*` are read from the manifest of the MAIN
    directory and there is none, so the manifest of the directory of the command has no say (the server and the command use the same rule).
    A real repository cannot lose its main directory and keep a common directory, so the project is the same one with a main path that is gone."""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.worktree("keyed")
        real = dw.project_identity(str(self.main))
        self.gone = dw.Project(root=str(self.wt), common_dir=real.common_dir, main_path=str(self.tmp / "vanished-main"))
        self.keyed = {"skills": ["dashboard"], "dashboard": {"worktrees": {"exclude": [str(self.wt)]}}}

    def test_the_settings_do_not_come_from_the_manifest_of_the_directory_of_the_command(self) -> None:
        self.assertEqual(dt.worktree_settings(self.gone, str(self.wt), self.keyed)["exclude"], [])

    def test_the_command_does_not_refuse_a_worktree_for_a_key_of_its_own_manifest(self) -> None:
        _entry, code = dt.check_tab(self.gone, str(self.wt), self.keyed)
        self.assertIsNone(code)

    def test_the_marker_still_excludes(self) -> None:
        marked = self.worktree("marked-alone", marker=True)
        _entry, code = dt.check_tab(self.gone, str(marked), {"skills": ["dashboard"]})
        self.assertEqual(code, "worktree_excluded")

    def test_with_a_main_directory_its_manifest_decides(self) -> None:
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees:\n    exclude: [" + json.dumps(str(self.wt)) + "]\n")
        project = dw.project_identity(str(self.main))
        self.assertEqual([wtt.real(path) for path in dt.worktree_settings(project, str(self.wt), {"skills": ["dashboard"]})["exclude"]],
                         [wtt.real(self.wt)], "control: the key of the main manifest is honoured")


class BrokenManifestIsFailClosed(ServerCase):
    """m2: a manifest whose `dashboard:` cannot be read gives no list of exclusions, so nothing is pinned (an excluded worktree must not slip in)."""

    BROKEN = {
        "wrong type of worktrees": "skills: [dashboard]\ndashboard:\n  worktrees: 5\n",
        "wrong type of exclude": "skills: [dashboard]\ndashboard:\n  worktrees:\n    exclude: 7\n",
        "unknown key in dashboard": "skills: [dashboard]\ndashboard:\n  no_such_setting: 1\n",
    }

    def test_a_join_is_refused_and_no_tab_appears(self) -> None:
        for name, manifest in self.BROKEN.items():
            with self.subTest(name=name):
                wtt.write(self.main / ".github" / ".skillsync.yaml", manifest)
                result = self.append(self.wt1)
                self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
                self.assertNotIn("#zgoda", result.stdout)
                self.assertEqual(result.stdout.strip(), "")
                self.assertNotIn(self.tab_of(self.wt1), tab_ids(self.approve_record()))

    def test_a_pin_placed_by_hand_is_refused_too(self) -> None:
        wtt.write(self.main / ".github" / ".skillsync.yaml", self.BROKEN["wrong type of worktrees"])
        pin = dt.write_pin(self.approve_record()["serverId"], self.tab_of(self.wt2), self.wt2)
        self.wait_for(lambda: not pin.exists(), "the pin is refused and removed")
        self.assertNotIn(self.tab_of(self.wt2), tab_ids(self.approve_record()))


class BrokenManifestAtStart(Base):
    def test_a_start_with_a_port_does_not_register_the_worktree_as_a_project_tab(self) -> None:
        wt = self.worktree("marked-start", marker=True)
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees: 5\n")
        proc, port = start_serve(self, CLI, wt, [], self.env, free_port)
        self.servers.append(proc)
        record = self.record_at(port)
        self.assertFalse(record["kind"] == "project" and self.tab_of(wt) in tab_ids(record), "an excluded worktree is not a project tab")


class VictimOfTheLimitSurvivesAFailedPin(Base):
    """m4: a pin whose tab cannot be built does not cost an existing tab its place. In-process (the failure is injected: no manifest of a real
    worktree makes the building fail): a registry at the limit of 16 tabs and a `PinWatcher` over it."""

    def setUp(self) -> None:
        super().setUp()
        board = srv.Board(self.main, poll=0.2, heartbeat=0.5, prototype_port=1, assets={}, descriptions={}, approvals=True)
        self.addCleanup(board.stop.set)
        self.project = dw.project_identity(str(self.main))
        self.registry = srv.TabRegistry(board, project=self.project)
        for index in range(16):
            self.worktree(f"v{index:02d}")
        entries = [e for e in dw.list_entries(self.project) if e.kind != "main"]
        self.registry.register(next(e for e in dw.list_entries(self.project) if e.kind == "main"), board)
        for entry in entries[:15]:
            new = self.registry._new_board(entry.path)
            self.addCleanup(new.stop.set)
            self.registry.register(entry, new)
        self.late = self.worktree("late")
        self.watcher = srv.PinWatcher(self.registry, self.project, self.main, {}, threading.Event())
        dt.write_pin(self.registry.server_id, self.tab_of(self.late), self.late)

    def test_control_a_good_pin_at_the_limit_takes_the_place_of_an_unpinned_tab(self) -> None:
        self.assertEqual(len(self.registry.ids()), 16)
        before = set(self.registry.ids())
        self.assertTrue(self.watcher.run_once())
        after = set(self.registry.ids())
        self.assertIn(self.tab_of(self.late), after)
        self.assertEqual(len(before - after), 1, "one tab made room")
        self.assertEqual(len(after), 16)

    def test_a_board_that_cannot_be_built_leaves_the_registry_as_it_was(self) -> None:
        before = sorted(self.registry.ids())
        with mock.patch.object(self.registry, "_new_board", side_effect=ValueError("no board")):
            self.watcher.run_once()
        self.assertEqual(sorted(self.registry.ids()), before)

    def test_a_registration_that_fails_leaves_the_registry_as_it_was(self) -> None:
        before = sorted(self.registry.ids())
        with mock.patch.object(self.registry, "register", side_effect=OSError("disk")):
            self.watcher.run_once()
        self.assertEqual(sorted(self.registry.ids()), before)
        self.assertNotIn(self.tab_of(self.late), self.registry.ids())


class ApprovalsWithoutAnOwnToken(Base):
    """m5: a state record that says consents are on but has no token for the tab of the directory never yields an address."""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.worktree("tokenless")
        self.sid, self.port, self.token = self.fake_server(
            tabs=[{"id": "main", "path": str(self.main), "token": "x"}, {"id": self.tab_of(self.wt), "path": str(self.wt)}])
        # `fake_server` keeps the token of `main` out of the tab list on purpose: the tab of the directory has none

    def test_serve_prints_no_address_and_does_not_succeed(self) -> None:
        result = self.cli("serve", str(self.wt), timeout=90)
        self.assertNotEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertNotIn("http://", result.stdout)
        self.assertNotIn("#zgoda", result.stdout + result.stderr)

    def test_url_prints_no_address_without_a_token(self) -> None:
        result = self.cli("url", str(self.wt), "--layer", f"core={ROOT / 'library'}")
        self.assertNotIn(f"tab={self.tab_of(self.wt)}", result.stdout, "an address of the tab without its token is useless and misleading")


class ReadSocketOnlyServer(Base):
    """m6: `--read-port N` with no consent socket is a server of its own (standalone) and never the target of a join."""

    def test_it_is_standalone_and_not_a_target(self) -> None:
        wt = self.worktree("beside-reader")
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(self.main), "--read-port", str(port), "--no-approvals",
                                 "--read-bind", "127.0.0.1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(lambda: release(proc))
        record = self.wait_for(lambda: next((r for r in self.records() if r.get("port") == port), None), "the read-only server writes its state", 20)
        self.assertEqual(record["kind"], "standalone")
        self.assertEqual(dt.plan_serve(serve_args(wt)).mode, "start")
        self.assertEqual(dt.servers_for_project(dw.project_identity(str(self.main)).common_dir), [])


class OddFilesInThePinDirectory(ServerCase):
    """m7: a FIFO named like a pin blocks nobody; a pin directory that is a link is not read."""

    def test_a_fifo_with_a_pin_name_does_not_block_the_watcher(self) -> None:
        if not hasattr(os, "mkfifo"):   # Windows has no FIFO: nothing to create there, the POSIX run is the proof
            return
        directory = dt.pins_dir(self.approve_record()["serverId"])
        directory.mkdir(mode=0o700, exist_ok=True)
        fifo = directory / f"{'a' * 12}.json"
        os.mkfifo(fifo)
        self.addCleanup(lambda: fifo.exists() and fifo.unlink())
        dt.write_pin(self.approve_record()["serverId"], self.tab_of(self.wt2), self.wt2)
        self.wait_for(lambda: self.tab_of(self.wt2) in tab_ids(self.approve_record()), "a later pin is still handled (the watcher is not stuck)", 15)
        self.assertIsNone(self.proc.poll())

    def test_the_reader_does_not_hang_on_a_fifo(self) -> None:
        if not hasattr(os, "mkfifo"):
            return
        sid = dt.new_server_id()
        directory = dt.pins_dir(sid)
        directory.mkdir(mode=0o700, parents=True)
        fifo = directory / f"{'b' * 12}.json"
        os.mkfifo(fifo)
        done = []
        thread = threading.Thread(target=lambda: done.append(dt.read_pins(sid, str(self.main))), daemon=True)
        thread.start()
        thread.join(5)
        if thread.is_alive():   # release the reader so that the thread ends, then fail
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK) if False else None
            self.fail("read_pins blocks on a FIFO")
        self.assertEqual(done, [[]])

    def test_a_pin_directory_that_is_a_link_is_ignored(self) -> None:
        sid = dt.new_server_id()
        wt = self.worktree("linked-pins")
        real = self.tmp / "other-pins"
        real.mkdir()
        wtt.write(child(real, f"{self.tab_of(wt)}.json"), json.dumps({"path": str(wt), "at": "now"}))
        self.symlink(real, dt.pins_dir(sid))
        self.assertEqual(dt.read_pins(sid, str(self.main)), [])
        self.assertTrue((child(real, f"{self.tab_of(wt)}.json")).exists(), "the target of the link is left alone")


class StateIsNotRevived(Base):
    """m8: the file of a server that stopped is not brought back by a late refresh of its tabs."""

    def test_refresh_after_the_removal_writes_nothing(self) -> None:
        sid = dt.new_server_id()
        project = dw.project_identity(str(self.main))
        tabs = [{"id": "main", "path": str(self.main), "token": "main.tok" + "3" * 20}]
        dt.write_state(self.main, 5187, "tok" + "3" * 20, "127.0.0.1", server_id=sid, kind="project", common_dir=project.common_dir, tabs=tabs)
        path = dt.state_path(self.main, 5187)
        self.assertTrue(dt.refresh_tabs(self.main, 5187, tabs, pid=os.getpid()), "control: a refresh of a live file works")
        dt.remove_state(self.main, 5187, pid=os.getpid())
        self.assertFalse(path.exists())
        self.assertFalse(dt.refresh_tabs(self.main, 5187, tabs, pid=os.getpid()))
        self.assertFalse(path.exists(), "the state file is not recreated")

    def test_a_refresh_of_another_process_file_is_refused(self) -> None:
        project = dw.project_identity(str(self.main))
        dt.write_state(self.main, 5188, "tok" + "4" * 20, "127.0.0.1", server_id=dt.new_server_id(), kind="project", common_dir=project.common_dir,
                       tabs=[{"id": "main", "path": str(self.main), "token": "main.x"}])
        self.assertFalse(dt.refresh_tabs(self.main, 5188, [], pid=os.getpid() + 1))


class EveryFlagOfServeIsClassified(unittest.TestCase):
    """m9: a flag added to `dashboard serve` is either a setting of the server (it makes a server of its own next to a running one) or on this list."""

    NOT_SETTINGS = {"--help", "--plan", "--standalone", "--all-tabs", "--json"}   # `--plan` is a setting of the tab, the rest only shape the output / the choice

    def parsed_flags(self) -> set:
        text = subprocess.run([sys.executable, str(CLI), "dashboard", "serve", "--help"], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SKILLSYNC_NO_COMPLETION": "1"}).stdout
        return {m.group(1) for line in text.splitlines() if (m := re.match(r"^  (?:-\w, )?(--[\w-]+)", line))}

    def test_the_flags_of_the_parser_are_all_classified(self) -> None:
        settings = {flag for flag, _dest in getattr(srv, "SETTING_FLAGS", dt.SETTING_FLAGS)}
        parsed = self.parsed_flags()
        self.assertTrue({"--port", "--layer", "--standalone", "--json", "--all-tabs"} <= parsed, f"control: the help was parsed ({sorted(parsed)})")
        self.assertEqual(sorted(parsed - settings - self.NOT_SETTINGS), [], "a flag of `serve` with no classification: setting or not?")
        self.assertEqual(sorted(settings - parsed), [], "a setting flag that the parser does not have")


# ======================================================================== (17) --help


class HelpMentionsTheNewFlags(unittest.TestCase):
    def help(self, command):
        env = {**os.environ, "SKILLSYNC_NO_COMPLETION": "1"}
        return subprocess.run([sys.executable, str(CLI), "dashboard", command, "--help"], capture_output=True, text=True, env=env, timeout=60)

    def test_serve_help_has_standalone_all_tabs_and_json(self) -> None:
        result = self.help("serve")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--standalone", "--all-tabs", "--json"):
            self.assertIn(flag, result.stdout)

    def test_url_help_has_all_tabs_and_json(self) -> None:
        result = self.help("url")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--all-tabs", "--json"):
            self.assertIn(flag, result.stdout)

    def test_every_new_flag_has_a_description(self) -> None:
        text = self.help("serve").stdout
        for flag in ("--standalone", "--all-tabs"):
            line = next(index for index, row in enumerate(text.splitlines()) if re.match(rf"^\s{{2}}{flag}\b", row))
            rows = text.splitlines()[line:line + 3]
            self.assertTrue(any(len(row.strip().split()) > 3 for row in rows), flag)


if __name__ == "__main__":
    unittest.main()
