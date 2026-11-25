"""CORE-1180 M-4: the life of the tabs of one server (the shared observer, the refresh of the list, the removal after two refreshes, the global
limits) and its cost, tested against the spec (sections "Limity i koszt (globalne dla serwera)", "Wyrejestrowanie", "Rejestracja automatyczna...",
K5, K6, K10), never against the implementation: the layout of a repository is built from plain files (no test runs `git`; `engine.git_ahead` is a
stand-in that counts its calls), the clock of the observer is a number the test moves, the HTTP server is real.

Sections: new tabs, removal, rotation without a leak, cost of the ticks (K10: measured and written to stderr as `MEASURE ...` lines), limits,
the tab of the start directory, the prototype probe of a tab, the minors of M-3 (`_wait_for_tab`, `plan_serve`), the server of one tab, the
stop and the errors of the observer. A server in a subprocess runs on the ports of the test slice (`SKILLSYNC_TEST_DASHBOARD_PORTS`), under a
private data home. Tokens are read from the registry or the state files, never written as literals. The only `skipTest` is the symlink one of
`wtt.Layout` (not used here)."""
from __future__ import annotations

import builtins
import contextlib
import functools
import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from layers_helpers import home_env  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_token as dt  # noqa: E402
from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from skillsync_workflow import i18n  # noqa: E402
from tests import test_skillsync_dashboard_attach as att  # noqa: E402
from tests import test_skillsync_dashboard_worktrees as wtt  # noqa: E402
from tmp_paths import child  # noqa: E402
from tests.serve_start import bind_free, start_serve, stop_serve  # noqa: E402
from tests.test_skillsync_dashboard_ports import two_ports  # noqa: E402
from tests.test_skillsync_dashboard_server import ASSETS, CLI, DESCRIPTIONS, PLAN, RUN, free_port, make_project  # noqa: E402
from tests.test_skillsync_dashboard_tabs_approvals import approvals_rows as _approvals_rows, wait_for_cp  # noqa: E402

STEP = 0.25          # simulated seconds between two ticks (more than the poll of a board, so every tick may check a watched board)
JSON_HEADERS = {"Content-Type": "application/json", "X-Status-Board": "1"}
BODY = {"kind": "cp", "path": "2026-01-10-demo-etap-1", "sha": "0" * 40}   # any body: the refusals under test come before it is read
REFUSED = {"error": srv.TOKEN_REFUSED, "code": "approve_token"}
MAX_TABS = dw.MAX_VISIBLE
MAX_SSE = srv.MAX_SSE_CLIENTS
RUN_ID = RUN.rsplit("/", 1)[-1]
NO_AHEAD = {"remote": None, "pushed": None, "ahead": None, "commits": None, "commitsMore": 0}


# ----------------------------------------------------------------------------------------------------------- helpers

class Sse:
    """A client of `/events`: reads the stream line by line (the server answers HTTP/1.0 and closes the stream when it ends it)."""

    def __init__(self, port, tab=None, timeout=10.0):
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        self.connection.request("GET", "/events" + (f"?tab={tab}" if tab else ""))
        self.response = self.connection.getresponse()
        self.status = self.response.status

    def event(self, name, seconds=10.0):
        """The data of the next event called `name` (a dict), or None when the stream ended or `seconds` passed."""
        deadline, current = time.monotonic() + seconds, None
        while time.monotonic() < deadline:
            try:
                raw = self.response.readline()
            except (TimeoutError, OSError):
                return None
            if not raw:
                return None
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event: "):
                current = line[len("event: "):]
            elif line.startswith("data: ") and current == name:
                return json.loads(line[len("data: "):])
        return None

    def ended(self, seconds=10.0) -> bool:
        """True when the server ended the stream (end of file) within `seconds`."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if not self.response.readline():
                    return True
            except (TimeoutError, OSError):
                return False
        return False

    def close(self) -> None:
        for closable in (self.response, self.connection):   # a response that will close owns the socket: closing the connection alone leaves it open
            with contextlib.suppress(Exception):
                closable.close()


class Counting:
    """What the code of ONE thread asks of the operating system while the context is open: files opened, directories listed, `stat` calls;
    processes started by any thread (`subprocess.Popen`, which `run`/`check_output` use, and `os.system`)."""

    def __init__(self):
        self.opens = self.listdirs = self.scandirs = self.stats = self.spawns = 0
        self.spawned: list = []
        self._stack = contextlib.ExitStack()
        self._thread = threading.get_ident()

    def _count(self, real, field):
        @functools.wraps(real)
        def wrapper(*args, **kwargs):
            if threading.get_ident() == self._thread:
                setattr(self, field, getattr(self, field) + 1)
            return real(*args, **kwargs)
        return wrapper

    def __enter__(self):
        for owner, name, field in ((builtins, "open", "opens"), (io, "open", "opens"), (os, "open", "opens"), (os, "listdir", "listdirs"),
                                   (os, "scandir", "scandirs"), (os, "stat", "stats"), (os, "lstat", "stats")):
            self._stack.enter_context(mock.patch.object(owner, name, self._count(getattr(owner, name), field)))
        real_popen, real_system = subprocess.Popen, os.system

        def popen(*args, **kwargs):
            self.spawns += 1
            self.spawned.append(args[0] if args else kwargs.get("args"))
            return real_popen(*args, **kwargs)

        def system(*args, **kwargs):
            self.spawns += 1
            self.spawned.append(args[0] if args else None)
            return real_system(*args, **kwargs)

        self._stack.enter_context(mock.patch.object(subprocess, "Popen", popen))
        self._stack.enter_context(mock.patch.object(os, "system", system))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    @property
    def listings(self) -> int:
        return self.listdirs + self.scandirs


def sha_of(text) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------------------------------- the stack

class Stack(wtt.Layout):
    """A main directory and worktrees as plain files, a registry over them, an observer driven by a clock the test moves, an HTTP server on demand.
    The state directories of the tests are under the temporary directory; nothing runs `git`."""
    POLL = 0.2

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.dict(os.environ, home_env(self.tmp))
        patch.start()
        self.addCleanup(patch.stop)
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        wtt.write(self.main / "docs" / "plans" / "main-plan.md", "# Plan: main\n\nStatus: in_progress\n")
        self.logged: list = []
        patch = mock.patch.object(srv, "log", lambda text, *a, **k: self.logged.append(str(text)))
        patch.start()
        self.addCleanup(patch.stop)
        self.ahead_calls: list = []
        patch = mock.patch.object(srv.engine, "git_ahead", self.fake_ahead)
        patch.start()
        self.addCleanup(patch.stop)
        self.clock = [0.0]
        self.ticks = 0
        self.changed: list = []
        self.registry = self.observer = self.board = self.project = self.found = None
        self.port = None
        self.clients: list = []

    def fake_ahead(self, root, branch):
        self.ahead_calls.append((str(root), branch))
        return dict(NO_AHEAD)

    # --- layout
    def live_worktree(self, name, *, age=0.0, branch=True, trace=True) -> Path:
        """A worktree with a trace of work (a run and its own plan and spec), the manifest of a skillsync project and a branch of its own; `age`
        seconds old (its files carry that time: the order of the tabs is by activity)."""
        wt = self.add_worktree(name)
        wtt.write(wt / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        if trace:
            stamp = time.time() - age
            for rel in (f".skillsync/runs/run-{name}/checkpoints.md", f".skillsync/runs/run-{name}/S1-implementer.md",
                        f".skillsync/runs/run-{name}/agents.jsonl", f"docs/plans/{name}.md", f"docs/specs/{name}.md"):
                wtt.write(wt / rel, f"# {name}\n\nStatus: in_progress\n", mtime=stamp)
        if branch:
            wtt.write(child(self.common / "refs" / "heads", name), sha_of(name) + "\n")
        return wt

    def remove_worktree(self, name) -> None:
        """What `git worktree remove` leaves: neither the directory nor the entry in the common directory."""
        shutil.rmtree(child(self.tmp, name), ignore_errors=True)
        shutil.rmtree(self.entry_dir(name), ignore_errors=True)

    def tab_of(self, path) -> str:
        return dw.tab_id(str(path), str(self.main))

    def new_board(self, root, **kwargs):
        kwargs.setdefault("prototype_port", 1)
        board = srv.Board(Path(root), poll=self.POLL, heartbeat=0.5, assets=ASSETS, descriptions=DESCRIPTIONS, approvals=True, **kwargs)
        self.addCleanup(board.stop.set)
        return board

    # --- the server under test
    def start(self, default=None, *, pins=False, board_args=None):
        """The registry as the start of a project server makes it (`discover`, the start directory pinned) and the observer that serves it."""
        root = Path(default) if default else self.main
        self.project = dw.project_identity(str(root))
        self.assertIsNotNone(self.project)
        self.board = self.new_board(root, **(board_args or {}))
        self.registry = srv.TabRegistry(self.board, project=self.project)
        self.found = dw.discover(self.project, pinned=[str(root)])
        for entry in self.found.tabs:
            self.registry.register(entry)
        self.registry.pin(self.registry.default_id)
        watcher = srv.PinWatcher(self.registry, self.project, self.board.root, None, self.board.stop) if pins else None
        self.observer = srv.Observer(self.registry, self.board.stop, project=self.project, root=self.board.root, manifest=None, pins=watcher,
                                     clock=lambda: self.clock[0])
        self.addCleanup(self.registry.stop_all)
        self.addCleanup(self.drop_pins, self.registry.server_id)
        return self.registry

    def drop_pins(self, server_id) -> None:
        with contextlib.suppress(Exception):
            dt.remove_pins(server_id)

    def serve(self, **kwargs) -> int:
        server, port = bind_free(lambda p: srv.make_server(self.registry, "127.0.0.1", p, **kwargs), free_port)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(self.close_server, server)
        self.addCleanup(self.close_clients)
        self.addCleanup(self.registry.stop_all)   # runs first: the streams end, so nothing waits for them
        self.port = port
        return port

    @staticmethod
    def close_server(server) -> None:
        server.shutdown()
        server.server_close()

    def close_clients(self) -> None:
        for client in self.clients:
            client.close()

    def sse(self, tab=None, port=None) -> Sse:
        client = Sse(port or self.port, tab)
        self.clients.append(client)
        return client

    # --- the clock
    def tick(self, count=1) -> dict:
        """`count` ticks of the observer, each STEP simulated seconds after the last one; the roots that changed are collected in `self.changed`."""
        result = {}
        for _ in range(count):
            self.ticks += 1
            self.clock[0] = self.ticks * STEP
            result = self.observer.tick(self.clock[0])
            self.changed.extend(result["changed"])
        return result

    def tick_until(self, condition, seconds=10.0) -> bool:
        for _ in range(int(seconds / STEP)):
            if condition():
                return True
            self.tick()
        return condition()

    def wait_until(self, condition, what, seconds=10.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.05)
        self.fail(f"timed out ({seconds} s): {what}")

    # --- http
    def http(self, method, path, data=None, headers=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def get_json(self, path):
        code, raw = self.http("GET", path)
        try:
            return code, json.loads(raw.decode("utf-8"))
        except ValueError:
            return code, raw.decode("utf-8", "replace")

    def post_json(self, token, data=None, route="/api/approve"):
        headers = {**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.port}"}
        if token is not None:
            headers["X-Approve-Token"] = token
        code, raw = self.http("POST", route, data if data is not None else BODY, headers)
        try:
            return code, json.loads(raw.decode("utf-8"))
        except ValueError:
            return code, raw.decode("utf-8", "replace")

    def tab_ids_listed(self) -> list:
        code, tabs = self.get_json("/api/tabs")
        self.assertEqual(code, 200, tabs)
        return [tab["id"] for tab in tabs["tabs"]]


# ======================================================================================== (1) a worktree added after the start

class NewWorktreeAppears(Stack):
    def test_a_worktree_added_after_the_start_becomes_a_tab_through_the_ticks_alone(self) -> None:
        self.start()
        self.serve()
        self.assertEqual(self.registry.ids(), ["main"])
        late = self.live_worktree("late")
        tab = self.tab_of(late)
        self.assertNotIn(tab, self.registry.ids(), "control: the tab does not exist before the list is refreshed")
        with Counting() as counted:
            appeared = self.tick_until(lambda: tab in self.registry.ids(), seconds=6.0)
        self.assertTrue(appeared, "the observer adds the tab of a new worktree without a restart")
        self.assertEqual(counted.spawns, 0, f"the registration of a tab starts no process: {counted.spawned}")
        self.assertEqual(self.ahead_calls, [], "the registration does not ask git how far ahead the branch is")
        self.assertIn(tab, self.tab_ids_listed())
        self.assertIsNotNone(self.registry.board(tab))

    def test_the_new_tab_has_a_token_of_its_own_that_the_api_never_shows(self) -> None:
        self.start()
        self.serve()
        late = self.live_worktree("late")
        tab = self.tab_of(late)
        self.assertTrue(self.tick_until(lambda: tab in self.registry.ids(), seconds=6.0))
        state = {item["id"]: item for item in self.registry.tabs_state()}
        self.assertTrue(state[tab]["token"].startswith(tab + "."), "the token of a tab is `<id>.<hmac>`")
        self.assertNotEqual(state[tab]["token"], state["main"]["token"])
        code, raw = self.http("GET", "/api/tabs")
        self.assertEqual(code, 200)
        self.assertNotIn(state[tab]["token"].split(".", 1)[1], raw.decode("utf-8"), "K9: no token in /api/tabs")
        code, body = self.get_json(f"/api/status?tab={tab}")
        self.assertEqual(code, 200, body)

    def test_the_state_file_of_schema_two_follows_the_list(self) -> None:
        """The listener of the registry is what keeps `tabs[]` of the state files current (`refresh_tabs`): it is called when the tab appears."""
        self.start()
        port = free_port()
        dt.write_state(self.main, port, self.board.approve_token, "127.0.0.1", server_id=self.registry.server_id, kind="project",
                       common_dir=self.project.common_dir, tabs=self.registry.tabs_state())
        self.registry.add_listener(lambda: dt.refresh_tabs(self.main, port, self.registry.tabs_state(), pid=os.getpid()))
        late = self.live_worktree("late")
        tab = self.tab_of(late)
        self.assertTrue(self.tick_until(lambda: tab in self.registry.ids(), seconds=6.0))
        record = json.loads(next(dt.state_dir().glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], 2)
        tabs = {item["id"]: item for item in record["tabs"]}
        self.assertIn(tab, tabs)
        self.assertEqual(tabs[tab]["token"], self.registry.token_for(tab))

    def test_the_list_is_refreshed_every_few_seconds_not_on_every_tick(self) -> None:
        self.start()
        late = self.live_worktree("late")
        tab = self.tab_of(late)
        self.tick(3)   # 0.75 s of the observer clock
        self.assertNotIn(tab, self.registry.ids(), "a refresh of the list is a scan of the directories: it is not done 4 times a second")
        self.assertTrue(self.tick_until(lambda: tab in self.registry.ids(), seconds=6.0))


class ServerInASubprocess(att.Base):
    """The wiring of the real server: the thread of the observer, the state files, `/api/tabs` and the end of the process. Real time (the list is
    refreshed every 2 s), so the waits are generous."""

    def begin(self):
        self.proc, self.read_port, self.approve_port = self.start_project()
        return self.proc

    def approve_record(self):
        return next((record for record in self.records() if record.get("port") == self.approve_port), None)

    def tokens_in(self, tab):
        record = self.approve_record() or {}
        return next((item.get("token") for item in record.get("tabs", []) if item.get("id") == tab), None)

    def read_tabs(self) -> list:
        code, raw = att.get(self.read_port, "/api/tabs")
        return [tab["id"] for tab in json.loads(raw)["tabs"]] if code == 200 else []

    def test_a_worktree_created_after_the_start_appears_in_the_page_and_the_state_file(self) -> None:
        self.begin()
        self.assertEqual(self.read_tabs(), ["main"])
        late = self.worktree("late", runs=True)
        tab = self.tab_of(late)
        token = self.wait_for(lambda: self.tokens_in(tab), "the token of the new tab in the state file", 15.0)
        self.assertTrue(token.startswith(tab + "."))
        self.wait_for(lambda: tab in self.read_tabs(), "the new tab in /api/tabs", 5.0)
        code, raw = att.get(self.read_port, "/api/tabs")
        self.assertNotIn(token.split(".", 1)[1], raw, "K9: no token in /api/tabs")
        self.assertEqual(att.get(self.read_port, f"/api/status?tab={tab}")[0], 200)

    def test_a_removed_worktree_leaves_the_page_and_a_stale_page_gets_410(self) -> None:
        gone = self.worktree("gone", runs=True)
        tab = self.tab_of(gone)
        self.begin()
        token = self.wait_for(lambda: self.tokens_in(tab), "the tab of the worktree at the start", 5.0)
        shutil.rmtree(gone)
        shutil.rmtree(self.entry_dir("gone"))
        self.wait_for(lambda: tab not in self.read_tabs(), "the tab leaves /api/tabs", 20.0)
        self.assertEqual(att.get(self.read_port, f"/api/status?tab={tab}")[0], 404)
        self.assertIsNone(self.tokens_in(tab) if self.approve_record() else None, "the state file no longer holds the token of the gone tab")
        request = urllib.request.Request(f"http://127.0.0.1:{self.approve_port}/api/approve", data=json.dumps(BODY).encode("utf-8"), method="POST",
                                         headers={**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.approve_port}", "X-Approve-Token": token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 410)
        caught.exception.close()

    def test_an_excluded_worktree_leaves_the_page_and_the_refusal_is_the_one_of_a_bad_token(self) -> None:
        marked = self.worktree("marked", runs=True)
        tab = self.tab_of(marked)
        self.begin()
        token = self.wait_for(lambda: self.tokens_in(tab), "the tab of the worktree at the start", 5.0)
        wtt.write(marked / ".skillsync" / "no-dashboard", "")
        self.wait_for(lambda: tab not in self.read_tabs(), "the excluded tab leaves /api/tabs", 20.0)
        self.assertEqual(att.get(self.read_port, f"/api/status?tab={tab}")[0], 404)
        request = urllib.request.Request(f"http://127.0.0.1:{self.approve_port}/api/approve", data=json.dumps(BODY).encode("utf-8"), method="POST",
                                         headers={**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.approve_port}", "X-Approve-Token": token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 403, "an excluded tab leaves no tombstone: 403, not 410")
        self.assertEqual(json.loads(caught.exception.read()), REFUSED)
        caught.exception.close()

    def test_the_stop_ends_the_process_promptly_and_leaves_nothing(self) -> None:
        proc = self.begin()
        self.assertTrue(self.records())
        started = time.monotonic()
        _out, _err, graceful = stop_serve(proc)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8.0, "the observer is joined for at most a few seconds: the stop does not hang")
        if graceful:
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(self.records(), [], "the state files are removed at the stop")
        self.assertNotIn(self.read_port, self.open_ports())
        self.assertNotIn(self.approve_port, self.open_ports())


# ======================================================================================== (2) removal after two refreshes

class Removal(Stack):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.live_worktree("doomed")
        self.tab = self.tab_of(self.wt)
        self.start()
        self.serve()
        self.token = self.registry.token_for(self.tab)
        self.doomed_board = self.registry.board(self.tab)
        self.assertIsNotNone(self.doomed_board, "control: the tab exists before the worktree goes")
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 200)

    def assert_stays_one_refresh_then_goes(self) -> None:
        self.observer.refresh()
        self.assertIsNotNone(self.registry.board(self.tab), "after ONE refresh the tab is still there (no flicker on a dismounted disk)")
        self.assertIn(self.tab, self.tab_ids_listed())
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 200)
        self.assertFalse(self.doomed_board.stop.is_set())
        self.observer.refresh()
        self.assertIsNone(self.registry.board(self.tab), "after the SECOND consecutive refresh it is gone")
        self.assertNotIn(self.tab, self.tab_ids_listed())

    def test_a_removed_worktree_goes_after_two_refreshes_and_a_stale_consent_gets_410(self) -> None:
        self.remove_worktree("doomed")
        self.assert_stays_one_refresh_then_goes()
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 404)
        self.assertEqual(self.get_json(f"/api/doc?tab={self.tab}&path=docs/plans/doomed.md")[0], 404)
        code, body = self.post_json(self.token)
        self.assertEqual(code, 410, body)
        self.assertEqual(self.registry.ids(), ["main"])

    def test_a_worktree_that_lost_its_back_link_goes_the_same_way(self) -> None:
        (self.wt / ".git").unlink()
        self.assert_stays_one_refresh_then_goes()
        self.assertEqual(self.post_json(self.token)[0], 410)

    def test_a_worktree_that_vanished_from_the_list_of_git_goes_the_same_way(self) -> None:
        shutil.rmtree(self.entry_dir("doomed"))
        self.assert_stays_one_refresh_then_goes()
        self.assertEqual(self.post_json(self.token)[0], 410)

    def test_an_excluded_worktree_goes_after_two_refreshes_and_gets_no_tombstone(self) -> None:
        wtt.write(self.wt / ".skillsync" / "no-dashboard", "")
        self.assert_stays_one_refresh_then_goes()
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 404)
        code, body = self.post_json(self.token)
        self.assertEqual((code, body), (403, REFUSED), "an excluded tab is not told apart from a tab that never existed")

    def test_an_exclusion_in_the_manifest_of_the_main_directory_does_the_same(self) -> None:
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees:\n    exclude: [" + json.dumps(str(self.wt)) + "]\n")
        self.assert_stays_one_refresh_then_goes()
        self.assertEqual(self.post_json(self.token), (403, REFUSED))

    def test_a_tab_that_disappears_and_comes_back_within_one_cycle_stays(self) -> None:
        saved = self.tmp / "saved-doomed"
        self.wt.rename(saved)
        self.observer.refresh()                      # missing once
        self.assertIs(self.registry.board(self.tab), self.doomed_board)
        saved.rename(self.wt)                        # back
        self.observer.refresh()
        self.observer.refresh()
        self.assertIs(self.registry.board(self.tab), self.doomed_board, "the same board: nothing was removed and re-added")
        self.assertFalse(self.doomed_board.stop.is_set())
        self.assertEqual(self.registry.token_for(self.tab), self.token, "the token of the tab did not change")
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 200)

    def test_two_refreshes_that_are_not_consecutive_do_not_remove_the_tab(self) -> None:
        saved = self.tmp / "saved-doomed"
        for _round in range(3):
            self.wt.rename(saved)
            self.observer.refresh()                  # missing once ...
            saved.rename(self.wt)
            self.observer.refresh()                  # ... found again: the count starts over
        self.assertIs(self.registry.board(self.tab), self.doomed_board)

    def test_the_removal_stops_the_board_and_ends_its_streams(self) -> None:
        stream = self.sse(self.tab)
        self.assertEqual(stream.status, 200)
        self.assertIsNotNone(stream.event("hello"))
        self.assertEqual((self.board.sse_clients, self.doomed_board.sse_local), (1, 1))
        self.remove_worktree("doomed")
        self.observer.refresh()
        self.assertFalse(stream.ended(1.0), "control: after one refresh the stream is still open")
        self.observer.refresh()
        self.assertTrue(self.doomed_board.stop.is_set(), "the board of a gone tab is stopped")
        self.assertTrue(stream.ended(10.0), "its SSE stream is closed by the server")
        self.wait_until(lambda: self.board.sse_clients == 0, "the slot of the stream is returned to the server")

    def test_the_other_tabs_are_not_touched_by_a_removal(self) -> None:
        other = self.live_worktree("other")
        other_tab = self.tab_of(other)
        self.assertTrue(self.tick_until(lambda: other_tab in self.registry.ids(), seconds=6.0))
        other_board, other_token = self.registry.board(other_tab), self.registry.token_for(other_tab)
        self.remove_worktree("doomed")
        self.observer.refresh()
        self.observer.refresh()
        self.assertIsNone(self.registry.board(self.tab))
        self.assertIs(self.registry.board(other_tab), other_board)
        self.assertFalse(other_board.stop.is_set())
        self.assertEqual(self.registry.token_for(other_tab), other_token)
        self.assertEqual(self.get_json(f"/api/status?tab={other_tab}")[0], 200)

    def test_the_ticks_remove_the_tab_on_their_own_after_two_refresh_intervals(self) -> None:
        self.remove_worktree("doomed")
        self.tick(int(1.0 / STEP))   # a second: no refresh yet
        self.assertIsNotNone(self.registry.board(self.tab))
        self.assertTrue(self.tick_until(lambda: self.registry.board(self.tab) is None, seconds=8.0), "the observer removes the tab by itself")


# ======================================================================================== (3) no leak at rotation

class Rotation(Stack):
    CYCLES = 40
    SLACK = 2          # threads: the same number of threads before and after, give or take this many

    def cycle(self, number) -> None:
        name = f"rot{number % 5}"
        wt = self.live_worktree(name)
        tab = self.tab_of(wt)
        self.observer.refresh()
        self.assertIn(tab, self.registry.ids(), f"cycle {number}: the new worktree is a tab")
        stream = self.sse(tab)
        self.assertEqual(stream.status, 200)
        self.assertIsNotNone(stream.event("hello"))
        board = self.registry.board(tab)
        self.remove_worktree(name)
        self.observer.refresh()
        self.observer.refresh()
        self.assertNotIn(tab, self.registry.ids(), f"cycle {number}: the removed worktree is not a tab")
        self.assertTrue(board.stop.is_set())
        self.assertTrue(stream.ended(10.0), f"cycle {number}: the stream of the gone tab ended")
        stream.close()
        self.clients.remove(stream)

    def test_forty_cycles_of_add_and_remove_leave_no_thread_and_no_stream(self) -> None:
        self.start()
        self.serve()
        for number in range(2):   # warm-up: the threads that start once (imports, resolvers) are not a leak
            self.cycle(number)
        self.wait_until(lambda: self.board.sse_clients == 0, "the warm-up streams are gone")
        time.sleep(0.3)
        threads, streams = threading.active_count(), (self.board.sse_clients, dict(self.board.sse_by_role))
        for number in range(2, self.CYCLES + 2):
            self.cycle(number)
        self.wait_until(lambda: threading.active_count() <= threads + self.SLACK, f"the number of threads back to {threads} (+{self.SLACK}), now {threading.active_count()}")
        self.wait_until(lambda: self.board.sse_clients == 0, "every SSE slot is returned")
        self.assertEqual(self.board.sse_clients, streams[0])
        self.assertTrue(all(count == 0 for count in self.board.sse_by_role.values()), self.board.sse_by_role)
        self.assertEqual(self.registry.ids(), ["main"])
        self.assertEqual(self.board.sse_local, 0)
        self.assertEqual(self.http("GET", "/healthz")[0], 200, "the server is alive and answers")

    def test_the_slot_of_a_stream_is_returned_when_its_tab_is_removed(self) -> None:
        self.start()
        self.serve()
        wt = self.live_worktree("slot")
        tab = self.tab_of(wt)
        self.observer.refresh()
        streams = [self.sse(tab) for _ in range(5)]
        for stream in streams:
            self.assertIsNotNone(stream.event("hello"))
        self.assertEqual(self.board.sse_clients, 5, "the counter of the server counts the streams of every tab")
        self.remove_worktree("slot")
        self.observer.refresh()
        self.observer.refresh()
        for stream in streams:
            self.assertTrue(stream.ended(10.0))
        self.wait_until(lambda: self.board.sse_clients == 0, "all five slots are free again")


# ======================================================================================== (4) cost (K10)

class Measured(Stack):
    """Sixteen worktrees and the main directory: seventeen directories, sixteen tabs (the limit). The ticks are counted per kind."""
    WORKTREES = 16
    TICKS = 20

    def setUp(self) -> None:
        super().setUp()
        for number in range(self.WORKTREES):
            self.live_worktree(f"w{number:02d}", age=number * 600.0)   # w00 is the newest; the oldest does not fit
        self.build_calls: dict = {}
        self.signature_calls: dict = {}
        real_signature, real_build = srv.Board.signature, srv.engine.build_status

        def signature(board):
            self.signature_calls[str(board.root)] = self.signature_calls.get(str(board.root), 0) + 1
            return real_signature(board)

        def build_status(root, *args, **kwargs):
            self.build_calls[str(root)] = self.build_calls.get(str(root), 0) + 1
            return real_build(root, *args, **kwargs)

        for owner, name, spy in ((srv.Board, "signature", signature), (srv.engine, "build_status", build_status)):
            patch = mock.patch.object(owner, name, spy)
            patch.start()
            self.addCleanup(patch.stop)

    def root_of(self, name) -> str:
        return str(self.registry.board(self.tab_of(child(self.tmp, name))).root)

    def total(self, calls, *, without=()) -> int:
        return sum(count for root, count in calls.items() if root not in without)

    def run_ticks(self, label, count=None):
        """`count` counted ticks; returns the rows `{refresh, opens, listings, stats, spawns, ms}` and writes a `MEASURE` line."""
        rows = []
        for _ in range(count or self.TICKS):
            before = self.observer.stats["refreshes"]
            self.ticks += 1
            self.clock[0] = self.ticks * STEP
            with Counting() as counted:
                started = time.perf_counter()
                result = self.observer.tick(self.clock[0])
                elapsed = (time.perf_counter() - started) * 1000
            self.changed.extend(result["changed"])
            rows.append({"refresh": self.observer.stats["refreshes"] > before, "opens": counted.opens, "listings": counted.listings, "stats": counted.stats,
                         "spawns": counted.spawns, "ms": elapsed, "spawned": counted.spawned})
        self.report(label, rows)
        return rows

    def report(self, label, rows) -> None:
        for kind, wanted in (("plain tick", False), ("refresh tick", True)):
            part = [row for row in rows if row["refresh"] is wanted]
            if not part:
                continue
            line = (f"MEASURE {label} | {kind} | n={len(part)} | opens avg={sum(r['opens'] for r in part) / len(part):.1f} max={max(r['opens'] for r in part)}"
                    f" | listdir+scandir avg={sum(r['listings'] for r in part) / len(part):.1f} max={max(r['listings'] for r in part)}"
                    f" | stat avg={sum(r['stats'] for r in part) / len(part):.1f} max={max(r['stats'] for r in part)}"
                    f" | subprocess={sum(r['spawns'] for r in part)} | ms avg={sum(r['ms'] for r in part) / len(part):.2f} max={max(r['ms'] for r in part):.2f}")
            print(line, file=sys.stderr)


class FixtureOfSixteenWorktrees(Measured):
    def test_the_fixture_is_at_the_limit_of_tabs(self) -> None:
        self.start()
        self.assertEqual(len(self.registry.ids()), MAX_TABS, "seventeen directories, sixteen tabs")
        self.assertEqual(len(self.found.hidden), 1, "the seventeenth is hidden by the limit")
        self.assertIn(self.tab_of(self.tmp / "w00"), self.registry.ids())
        self.assertNotIn(self.tab_of(child(self.tmp, f"w{self.WORKTREES - 1:02d}")), self.registry.ids(), "the oldest does not fit")


class CostOfTicks(Measured):
    # Budgets (opens, directory listings) per tick: five times what was measured on this fixture (the measured numbers are in the report of
    # the step and in the `MEASURE` lines a run writes). Idle: nothing but the one listing of the pin directory.
    BUDGET_IDLE = (5, 1)
    BUDGET_REFRESH = (425, 415)      # measured 85 opens, 83 listings (and ~1400 stat calls) for one refresh of 17 directories
    BUDGET_ONE_TAB = (20, 25)        # measured 4 opens, 5 listings, ~55 stat calls for the signature of one watched tab
    BUDGET_ALL_TABS = (310, 310)     # measured 62 opens, 62 listings, ~860 stat calls for sixteen watched tabs

    def setUp(self) -> None:
        super().setUp()
        self.start(pins=True)
        self.serve()
        self.assertEqual(len(self.registry.ids()), MAX_TABS)
        self.signature_calls.clear()   # the construction of a board reads its signature once: that is the start, not a tick
        self.build_calls.clear()
        self.ahead_calls.clear()

    def within(self, rows, idle=None, one=None):
        """Every tick of `rows` is inside the budget of its kind (a refresh tick, else `idle` or the budget of watched tabs given)."""
        for row in rows:
            opens, listings = self.BUDGET_REFRESH if row["refresh"] else (idle or one)
            self.assertLessEqual(row["opens"], opens, f"files opened by a tick: {row}")
            self.assertLessEqual(row["listings"], listings, f"directories listed by a tick: {row}")

    def test_nobody_looks_so_no_tick_checks_a_board_builds_a_status_or_starts_a_process(self) -> None:
        rows = self.run_ticks("nobody watches")
        self.assertEqual(sum(row["spawns"] for row in rows), 0, [row["spawned"] for row in rows if row["spawned"]])
        self.assertEqual(self.ahead_calls, [], "no tick asks git how far ahead a branch is")
        self.assertEqual(self.total(self.build_calls), 0, f"no status is built in a tick: {self.build_calls}")
        self.assertEqual(self.total(self.signature_calls), 0, f"the signature of an unwatched board is not read: {self.signature_calls}")
        self.assertGreaterEqual(sum(1 for row in rows if row["refresh"]), 1, "control: the 20 ticks include a refresh of the list")
        self.within(rows, idle=self.BUDGET_IDLE)

    def test_a_refresh_of_the_list_stays_inside_the_budget_and_starts_no_process(self) -> None:
        rows = [row for row in self.run_ticks("nobody watches (refresh)") if row["refresh"]]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["spawns"], 0)
        self.within(rows)

    def test_a_tab_that_was_asked_for_is_checked_and_the_others_are_not(self) -> None:
        target = self.registry.board(self.tab_of(self.tmp / "w05"))
        target.touch(now=self.clock[0])
        rows = self.run_ticks("one tab asked for")
        root = str(target.root)
        self.assertGreaterEqual(self.signature_calls.get(root, 0), self.TICKS - 2, "a watched tab is checked at its pace")
        self.assertEqual(self.total(self.signature_calls, without=(root,)), 0, f"nobody else is checked: {self.signature_calls}")
        self.assertEqual(self.total(self.build_calls), 0)
        self.assertEqual(sum(row["spawns"] for row in rows), 0)
        self.assertEqual(self.ahead_calls, [])
        self.within(rows, one=self.BUDGET_ONE_TAB)

    def test_a_stream_makes_its_tab_watched(self) -> None:
        stream = self.sse(self.tab_of(self.tmp / "w07"))
        self.assertIsNotNone(stream.event("hello"))
        rows = self.run_ticks("one stream")
        root = self.root_of("w07")
        self.assertGreaterEqual(self.signature_calls.get(root, 0), self.TICKS - 2)
        self.assertEqual(self.total(self.signature_calls, without=(root,)), 0)
        self.assertEqual(sum(row["spawns"] for row in rows), 0)
        self.within(rows, one=self.BUDGET_ONE_TAB)

    def test_a_tab_is_dropped_from_the_watch_when_nobody_asks_for_it_any_more(self) -> None:
        target = self.registry.board(self.tab_of(self.tmp / "w05"))
        target.touch(now=self.clock[0])
        self.run_ticks("watch grace (asked for)", 4)
        root = str(target.root)
        self.assertGreater(self.signature_calls.get(root, 0), 0)
        quiet = int((srv.Observer.WATCH_GRACE_S + 2.0) / STEP)
        self.run_ticks("watch grace (waiting out)", quiet)
        before = self.signature_calls.get(root, 0)
        self.run_ticks("watch grace (after)", 8)
        self.assertEqual(self.signature_calls.get(root, 0), before, "after the grace period nobody reads its signature")

    def test_all_sixteen_asked_for_still_start_no_process_and_build_no_status(self) -> None:
        for board in self.registry.boards():
            board.touch(now=self.clock[0])
        rows = self.run_ticks("all sixteen asked for")
        self.assertEqual(sum(row["spawns"] for row in rows), 0, [row["spawned"] for row in rows if row["spawned"]])
        self.assertEqual(self.total(self.build_calls), 0)
        self.assertEqual(self.ahead_calls, [])
        self.assertGreaterEqual(len(self.signature_calls), MAX_TABS, "every watched tab was checked")
        self.within(rows, one=self.BUDGET_ALL_TABS)

    def test_a_status_is_built_for_the_tab_that_is_asked_for_only(self) -> None:
        tab = self.tab_of(self.tmp / "w03")
        code, body = self.get_json(f"/api/status?tab={tab}")
        self.assertEqual(code, 200, body)
        self.assertEqual(self.build_calls, {self.root_of("w03"): 1}, "one request, one status, of that tab only")
        self.assertIsNotNone(self.registry.board(tab).last_request, "the request marks the tab as watched")
        untouched = [board for board in self.registry.boards() if board is not self.registry.board(tab)]
        self.assertTrue(all(board.last_request is None for board in untouched), "no other tab is marked")
        self.assertEqual(self.get_json("/api/status?tab=0123456789ab")[0], 404)
        self.assertEqual(self.build_calls, {self.root_of("w03"): 1}, "an unknown tab builds nothing")

    def test_the_list_of_tabs_builds_no_status_for_any_tab(self) -> None:
        self.assertEqual(len(self.tab_ids_listed()), MAX_TABS)
        self.assertEqual(self.build_calls, {})
        code, body = self.get_json(f"/api/status?tab={self.tab_of(self.tmp / 'w03')}")
        self.assertEqual(code, 200)
        self.assertEqual(len(body["worktrees"]), MAX_TABS, "worktrees[] is a light summary of every tab")
        self.assertEqual(sum(self.build_calls.values()), 1, "the summary of the other fifteen tabs built no status")

    def test_one_thread_serves_sixteen_boards(self) -> None:
        before = threading.active_count()
        self.observer.start()
        self.addCleanup(self.stop_observer)
        self.assertEqual(threading.active_count() - before, 1, "one thread of the observer, not one per tab")
        self.assertEqual([thread.name for thread in threading.enumerate()].count("observer"), 1)
        print(f"MEASURE threads | boards={len(self.registry.boards())} | threads before={before} after={threading.active_count()} | observer threads=1", file=sys.stderr)

    def stop_observer(self) -> None:
        self.board.stop.set()
        self.observer.join(5.0)


class HeadOfAWorktree(Measured):
    """K10: the HEAD of a worktree is read through its `.git` FILE (the gitdir it names), so a commit in a worktree reaches the page."""
    WORKTREES = 4

    def setUp(self) -> None:
        super().setUp()
        self.start(pins=True)
        self.serve()
        self.tab = self.tab_of(self.tmp / "w01")
        self.watched = self.registry.board(self.tab)

    def commit(self, name, *, content=None, bump=2.0) -> str:
        """A commit in the worktree as git records it: the ref of its branch gets a new id (a new mtime too, so the coarse clocks of other systems see it)."""
        sha = sha_of(f"{name}-{time.time()}-{content}")
        path = child(self.common / "refs" / "heads", name)
        wtt.write(path, sha + "\n")
        later = time.time() + bump
        os.utime(path, (later, later))
        return sha

    def test_the_dot_git_of_the_worktree_is_a_file_that_names_its_gitdir(self) -> None:
        self.assertTrue((self.tmp / "w01" / ".git").is_file(), "control: the fixture is a linked worktree")

    def test_a_commit_in_a_watched_worktree_raises_the_version_and_sends_change(self) -> None:
        stream = self.sse(self.tab)
        self.assertIsNotNone(stream.event("hello"))
        version = self.watched.version
        self.tick(2)
        self.assertEqual(self.watched.version, version, "control: nothing changed, nothing is announced")
        self.commit("w01")
        self.tick(2)
        self.assertGreater(self.watched.version, version)
        self.assertIn(str(self.watched.root), self.changed)
        self.assertIsNotNone(stream.event("change", 5.0), "the page of the tab gets the event")

    def test_a_branch_switched_in_the_gitdir_of_the_worktree_is_seen_too(self) -> None:
        stream = self.sse(self.tab)
        self.assertIsNotNone(stream.event("hello"))
        version = self.watched.version
        wtt.write(self.common / "refs" / "heads" / "elsewhere", sha_of("elsewhere") + "\n")
        head = self.entry_dir("w01") / "HEAD"
        wtt.write(head, "ref: refs/heads/elsewhere\n")
        later = time.time() + 3.0
        os.utime(head, (later, later))
        self.tick(2)
        self.assertGreater(self.watched.version, version, "HEAD of a linked worktree lives in <commondir>/worktrees/<id>/HEAD, not in <worktree>/.git/HEAD")
        self.assertIsNotNone(stream.event("change", 5.0))

    def test_a_commit_in_a_worktree_nobody_looks_at_is_in_the_summary_after_the_next_refresh(self) -> None:
        quiet = self.tab_of(self.tmp / "w02")
        board = self.registry.board(quiet)
        version, old = board.version, {tab["id"]: tab["head"] for tab in self.get_json("/api/tabs")[1]["tabs"]}[quiet]
        sha = self.commit("w02")
        self.tick(2)
        self.assertEqual(board.version, version, "an unwatched tab announces nothing")
        self.assertEqual({tab["id"]: tab["head"] for tab in self.get_json("/api/tabs")[1]["tabs"]}[quiet], old, "the summary is as of the last refresh")
        self.observer.refresh()
        now = {tab["id"]: tab["head"] for tab in self.get_json("/api/tabs")[1]["tabs"]}[quiet]
        self.assertEqual(now, sha[:12], "the light summary shows the new HEAD (read from the files of git)")
        self.assertNotEqual(now, old)

    def test_a_commit_in_one_worktree_is_not_announced_to_another(self) -> None:
        other = self.registry.board(self.tab_of(self.tmp / "w02"))
        other.touch(now=self.clock[0])
        self.tick(2)
        version = other.version
        self.commit("w01")
        self.watched.touch(now=self.clock[0])
        self.tick(3)
        self.assertGreater(self.watched.version, 0)
        self.assertEqual(other.version, version)


# ======================================================================================== (5) global limits

class SseLimit(Stack):
    def setUp(self) -> None:
        super().setUp()
        self.live_worktree("second")
        self.start()
        self.serve()
        self.tab_a, self.tab_b = "main", self.tab_of(self.tmp / "second")
        self.assertEqual(sorted(self.registry.ids()), sorted([self.tab_a, self.tab_b]))

    def test_the_limit_of_streams_is_the_servers_not_each_tabs(self) -> None:
        on_a = [self.sse(self.tab_a) for _ in range(40)]
        on_b = [self.sse(self.tab_b) for _ in range(MAX_SSE - 40)]
        self.assertTrue(all(client.status == 200 for client in on_a + on_b), "64 streams in all are accepted, 40 on one tab and 24 on the other")
        self.assertEqual(self.board.sse_clients, MAX_SSE)
        refused_b = self.sse(self.tab_b)
        self.assertEqual(refused_b.status, 503, "the 65th stream is refused, as it is today")
        refused_a = self.sse(self.tab_a)
        self.assertEqual(refused_a.status, 503, "also on the tab that holds only 40")
        self.assertEqual(self.board.sse_clients, MAX_SSE, "a refusal takes no slot")

    def test_a_slot_returned_by_one_tab_is_given_to_the_other(self) -> None:
        clients = [self.sse(self.tab_a) for _ in range(MAX_SSE)]
        self.assertTrue(all(client.status == 200 for client in clients))
        self.assertEqual(self.sse(self.tab_b).status, 503, "full: even a tab with no stream of its own is refused")
        clients[0].close()
        self.wait_until(lambda: self.board.sse_clients == MAX_SSE - 1, "the server notices the closed stream")
        self.assertEqual(self.sse(self.tab_b).status, 200)

    def test_the_counter_is_one_object_for_every_tab(self) -> None:
        other = self.registry.board(self.tab_b)
        self.assertEqual(self.sse(self.tab_b).status, 200)
        self.assertEqual(self.board.sse_clients, 1, "a stream of another tab counts in the counter of the server")
        self.assertEqual(other.sse_local, 1)
        self.assertEqual(self.board.sse_local, 0)


class SharedTokenFailures(Stack):
    def setUp(self) -> None:
        super().setUp()
        self.live_worktree("second")
        self.start()
        self.tab_a, self.tab_b = "main", self.tab_of(self.tmp / "second")
        self.read_port, self.approve_port = two_ports()
        for role, port, peer in ((ports.ROLE_READ, self.read_port, self.approve_port), (ports.ROLE_APPROVE, self.approve_port, self.read_port)):
            server = srv.make_server(self.registry, "127.0.0.1", port, role=role, peer_port=peer)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
            self.addCleanup(self.close_server, server)
        self.counters: list = []
        real = ports.TokenFailures.add

        def add(counter, peer):
            self.counters.append(id(counter))
            return real(counter, peer)

        patch = mock.patch.object(ports.TokenFailures, "add", add)
        patch.start()
        self.addCleanup(patch.stop)

    def refused(self, token) -> int:
        connection = http.client.HTTPConnection("127.0.0.1", self.approve_port, timeout=10)
        try:
            connection.request("POST", "/api/approve", json.dumps(BODY).encode("utf-8"),
                               {**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.approve_port}", "X-Approve-Token": token})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_failures_on_both_tabs_go_to_one_counter(self) -> None:
        wrong = "0" * 64
        self.assertEqual(self.refused(f"{self.tab_a}.{wrong}"), 403)
        self.assertEqual(self.refused(f"{self.tab_b}.{wrong}"), 403)
        self.assertEqual(self.refused(f"{self.tab_b}.{wrong}"), 403)
        self.assertEqual(len(self.counters), 3, "every refused token is counted")
        self.assertEqual(len(set(self.counters)), 1, "the failures of the tab A and of the tab B add up in ONE counter")

    def test_the_report_of_the_failures_is_one_line_for_the_server(self) -> None:
        wrong = "0" * 64
        self.refused(f"{self.tab_a}.{wrong}")
        self.refused(f"{self.tab_b}.{wrong}")
        self.assertEqual(len([line for line in self.logged if "UWAGA" in line]), 1, f"one report a minute for the whole server: {self.logged}")

    def test_every_tab_holds_the_counter_of_the_server(self) -> None:
        boards = self.registry.boards()
        self.assertEqual(len(boards), 2)
        self.assertEqual(len({id(board.token_failures) for board in boards}), 1)


class TabLimit(Measured):
    WORKTREES = 17   # seventeen worktrees and the main directory: eighteen candidates for sixteen places

    def test_the_registry_never_holds_more_than_sixteen_tabs(self) -> None:
        self.start()
        self.assertEqual(len(self.registry.ids()), MAX_TABS)
        self.serve()
        hidden = self.found.hidden
        self.assertEqual(len(hidden), 2)
        for _ in range(5):
            self.observer.refresh()
            self.assertLessEqual(len(self.registry.ids()), MAX_TABS)
        for entry in hidden:
            self.assertIsNone(self.registry.board(entry.id), "a hidden worktree has no board")
            self.assertEqual(self.get_json(f"/api/status?tab={entry.id}")[0], 404)
        self.assertLessEqual(len(self.get_json("/api/tabs")[1]["tabs"]), MAX_TABS)

    def test_a_new_busy_worktree_waits_for_a_place_instead_of_exceeding_the_limit(self) -> None:
        self.start()
        newest = self.live_worktree("newest", age=-60.0)
        tab = self.tab_of(newest)
        self.assertEqual(len(self.registry.ids()), MAX_TABS)
        sizes = []
        for _ in range(6):
            self.observer.refresh()
            sizes.append(len(self.registry.ids()))
        self.assertLessEqual(max(sizes), MAX_TABS, sizes)
        self.assertIn(tab, self.registry.ids(), "the most active worktree gets a place as soon as a stale tab gives one up")
        self.assertEqual(len(self.registry.ids()), MAX_TABS)


class PinnedWinsAtTheLimit(Measured):
    WORKTREES = 15   # main + 15 = the sixteen places

    def setUp(self) -> None:
        super().setUp()
        self.start(pins=True)
        self.serve()
        self.fresh = self.add_worktree("fresh")   # no trace of work, no run: a hidden one, unless pinned
        wtt.write(self.fresh / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        self.fresh_tab = self.tab_of(self.fresh)

    def test_a_pinned_directory_gets_a_place_from_the_least_active_unpinned_tab(self) -> None:
        self.assertEqual(len(self.registry.ids()), MAX_TABS, "control: all places are taken")
        oldest = self.tab_of(child(self.tmp, f"w{self.WORKTREES - 1:02d}"))
        victim_token = self.registry.token_for(oldest)
        self.assertNotIn(self.fresh_tab, self.observer.registry.ids())
        dt.write_pin(self.registry.server_id, self.fresh_tab, self.fresh)
        self.assertTrue(self.tick_until(lambda: self.fresh_tab in self.registry.ids(), seconds=3.0), "the observer reads the pin directory at once")
        self.assertEqual(len(self.registry.ids()), MAX_TABS, "the limit holds")
        self.assertNotIn(oldest, self.registry.ids(), "the least active of the others made room")
        self.assertIn("main", self.registry.ids())
        self.assertEqual(self.post_json(victim_token)[0], 410, "the tab that was pushed out leaves a tombstone like any removed tab")

    def test_the_pinned_tab_outlasts_the_refreshes_though_no_rule_lets_it_in(self) -> None:
        dt.write_pin(self.registry.server_id, self.fresh_tab, self.fresh)
        self.assertTrue(self.tick_until(lambda: self.fresh_tab in self.registry.ids(), seconds=3.0))
        for _ in range(6):
            self.observer.refresh()
        self.assertIn(self.fresh_tab, self.registry.ids(), "a pin lasts as long as its tab (R1 and the inactivity do not apply to it)")
        self.assertEqual(len(self.registry.ids()), MAX_TABS)

    def test_without_a_pin_the_same_worktree_stays_out(self) -> None:
        for _ in range(4):
            self.observer.refresh()
        self.assertNotIn(self.fresh_tab, self.registry.ids(), "control: no trace of work, no tab")

    def test_a_pinned_directory_that_is_excluded_does_not_get_in(self) -> None:
        wtt.write(self.fresh / ".skillsync" / "no-dashboard", "")
        dt.write_pin(self.registry.server_id, self.fresh_tab, self.fresh)
        self.tick(4)
        for _ in range(3):
            self.observer.refresh()
        self.assertNotIn(self.fresh_tab, self.registry.ids())


# ======================================================================================== (6) the tab of the start directory

class StartDirectoryNeverGoes(Stack):
    """The directory the server was started in keeps its tab whatever happens to it: the server lives, its reads are empty, every write is refused."""

    def setUp(self) -> None:
        super().setUp()
        self.starter = self.live_worktree("starter", trace=False)   # no trace of work in the registry's eyes: only the pin keeps it
        template = make_project(self.tmp / "template")             # a run that waits for CP, with the plan of the demo project
        for rel in (".skillsync", "docs"):
            shutil.copytree(template / rel, self.starter / rel, dirs_exist_ok=True)
        wait_for_cp(self.starter)
        self.tab = self.tab_of(self.starter)
        self.live_worktree("friend")
        self.start(default=self.starter)
        self.serve()
        self.assertEqual(self.registry.default_id, self.tab)
        self.assertIn("main", self.registry.ids())
        self.token = self.registry.token_for(self.tab)
        code, status = self.get_json("/api/status")
        self.assertEqual(code, 200, status)
        offer = next((item for item in status["approvals"]["actions"] if item["kind"] == "cp" and item["state"] == "waiting"), None)
        self.assertIsNotNone(offer, "control: the start directory offers CP")
        self.sha = offer["sha"]

    def consent(self):
        return self.post_json(self.token, {"kind": "cp", "path": RUN_ID, "sha": self.sha, "plan": PLAN})

    def written(self) -> list:
        return _approvals_rows(self.starter)

    def refresh_a_lot(self) -> None:
        for _ in range(5):
            self.observer.refresh()

    def assert_default_stands(self) -> None:
        self.assertEqual(self.registry.default_id, self.tab)
        self.assertIn(self.tab, self.registry.ids(), "the tab of the start directory is never removed")
        self.assertTrue(self.registry.ids(), "the registry is never empty")
        self.assertEqual(self.http("GET", "/healthz")[0], 200, "the server is alive")
        self.assertFalse(self.board.stop.is_set())

    def assert_refused(self) -> dict:
        """409 and no line in `approvals.jsonl`. (Whether it is `tabChanged` of the re-validation or the answer of an offer that is gone with the
        `.git` file cannot be told apart here: the offer comes from the same files; a test whose offer is untouched asserts the code on the result.)"""
        code, body = self.consent()
        self.assertEqual(code, 409, body)
        self.assertEqual(self.written(), [], "nothing was written")
        return body

    def test_control_the_same_consent_is_accepted_while_the_tab_is_in_order(self) -> None:
        code, body = self.consent()
        self.assertEqual(code, 200, body)
        self.assertEqual(len(self.written()), 1)

    def test_a_vanished_start_directory_keeps_its_tab_reads_empty_and_refuses_every_write(self) -> None:
        self.remove_worktree("starter")
        self.refresh_a_lot()
        self.assert_default_stands()
        code, body = self.get_json("/api/status")
        self.assertEqual(code, 200, "the read does not crash the server")
        self.assertEqual((body["plans"], body["plan"], body["run"]["exists"]), ([], None, False), "nothing of the vanished directory is shown")
        self.assertEqual(self.consent()[0], 409)
        self.assertFalse(self.starter.exists(), "the refusal did not re-create the directory")
        self.assertEqual(self.get_json("/api/tabs")[1]["current"], self.tab)

    def test_a_start_directory_that_got_the_exclusion_marker_keeps_its_tab_and_refuses_writes(self) -> None:
        wtt.write(self.starter / ".skillsync" / "no-dashboard", "")
        self.refresh_a_lot()
        self.assert_default_stands()
        self.assertEqual(self.assert_refused().get("code"), "tabChanged", "the offer is untouched, so the refusal is the re-validation")

    def test_a_start_directory_that_lost_its_back_link_keeps_its_tab_and_refuses_writes(self) -> None:
        (self.starter / ".git").unlink()
        self.refresh_a_lot()
        self.assert_default_stands()
        self.assert_refused()

    def test_a_start_directory_that_left_the_list_of_git_keeps_its_tab_and_refuses_writes(self) -> None:
        shutil.rmtree(self.entry_dir("starter"))
        self.refresh_a_lot()
        self.assert_default_stands()
        self.assert_refused()

    def test_the_other_tabs_still_work_beside_a_vanished_default(self) -> None:
        self.remove_worktree("starter")
        self.refresh_a_lot()
        friend = self.tab_of(self.tmp / "friend")
        self.assertIn(friend, self.registry.ids())
        self.assertEqual(self.get_json(f"/api/status?tab={friend}")[0], 200)

    def test_only_the_start_directory_is_protected(self) -> None:
        shutil.rmtree(self.entry_dir("friend"))
        self.observer.refresh()
        self.assertIn(self.tab_of(self.tmp / "friend"), self.registry.ids())
        self.observer.refresh()
        self.assertNotIn(self.tab_of(self.tmp / "friend"), self.registry.ids(), "control: another tab goes after two refreshes")


# ======================================================================================== (7) the prototype probe of a tab

class PrototypeProbe(Stack):
    """A tab with its own `prototype.port` in its manifest is probed on that port; a tab without one is not probed at all; the port of one tab says
    nothing about another."""

    def setUp(self) -> None:
        super().setUp()
        self.listeners: list = []

    def listen(self) -> int:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) if os.name != "nt" else None
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        self.listeners.append(sock)
        self.addCleanup(sock.close)
        return sock.getsockname()[1]

    def closed_port(self) -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def manifest(self, root, port) -> None:
        text = "skills: [dashboard]\n" + (f"prototype:\n  port: {port}\n" if port else "")
        wtt.write(Path(root) / ".github" / ".skillsync.yaml", text)

    def build(self, port_main, port_b, port_c):
        """main (own port), `b` (own port) and `c` (no port at all)."""
        for name, port in (("b", port_b), ("c", port_c)):
            self.live_worktree(name)
            self.manifest(child(self.tmp, name), port)
        self.manifest(self.main, port_main)
        self.start(board_args={"prototype_port": port_main or 1})
        return {name: self.registry.board(self.tab_of(child(self.tmp, name))) for name in ("b", "c")}

    def test_each_tab_is_probed_on_its_own_port(self) -> None:
        open_port = self.listen()
        boards = self.build(open_port, self.closed_port(), None)
        self.assertEqual(len(self.registry.ids()), 3)
        self.assertTrue(self.board.prototype_up(), "control: the port of the main directory is open")
        self.assertFalse(boards["b"].prototype_up(), "the tab B has a port of its own, nobody listens on it: the open port of the tab A is no answer")

    def test_a_tab_without_a_port_in_its_manifest_is_not_probed(self) -> None:
        open_port = self.listen()
        with mock.patch.object(srv.engine, "PROTOTYPE_PORT", open_port):   # even the default port of the engine is open
            boards = self.build(open_port, self.closed_port(), None)
        self.assertFalse(boards["c"].prototype_up(), "no `prototype.port` in the manifest of the tab: no probe, no prototype")
        self.assertTrue(self.board.prototype_up())

    def test_the_port_of_the_other_tab_does_not_make_this_one_up(self) -> None:
        open_port = self.listen()
        boards = self.build(self.closed_port(), open_port, None)
        self.assertTrue(boards["b"].prototype_up(), "the tab B has its own open port")
        self.assertFalse(self.board.prototype_up(), "the tab A listens on a closed port of its own")
        self.assertFalse(boards["c"].prototype_up(), "and the tab C has none")

    def test_the_status_of_a_tab_reports_its_own_probe(self) -> None:
        open_port = self.listen()
        closed = self.closed_port()
        for name in ("b", "c"):
            self.live_worktree(name)
        self.manifest(self.main, open_port)
        self.manifest(self.tmp / "b", closed)
        self.manifest(self.tmp / "c", None)
        for root in (self.main, self.tmp / "b", self.tmp / "c"):
            wtt.write(Path(root) / "docs" / "plans" / "2026-01-10-demo.md",
                      "# Plan: Demo\n\nStatus: in_progress\nData: 2026-01-10\nPrototyp: docs/prototypes/demo\n\n## Cel\n\nx\n")
        self.start(board_args={"prototype_port": open_port})
        self.serve()
        seen = {}
        for name, tab in (("main", "main"), ("b", self.tab_of(self.tmp / "b")), ("c", self.tab_of(self.tmp / "c"))):
            code, status = self.get_json(f"/api/status?tab={tab}&plan=docs/plans/2026-01-10-demo.md")
            self.assertEqual(code, 200, status)
            seen[name] = status["prototype"]
        self.assertEqual(seen["main"]["port"], open_port)
        self.assertEqual(seen["b"]["port"], closed)
        self.assertEqual(seen["main"]["slug"], "demo", "control: the fixture gives the tab a prototype to look for")
        self.assertEqual([seen[name]["up"] for name in ("main", "b", "c")], [True, False, False])


# ======================================================================================== (8) the minors of M-3

class WaitForTheTab(att.Base):
    """`_wait_for_tab` ends only with the record that has the tab AND, for a target with consents, a token: the read socket knows the tab without it."""

    def setUp(self) -> None:
        super().setUp()
        self.wt = self.worktree("joiner")
        self.tab = self.tab_of(self.wt)
        self.project = dw.project_identity(str(self.main))
        self.server_id = dt.new_server_id()
        self.token = "fake-" + "".join(f"{ord(c):02x}" for c in self.server_id)
        self.main_tab = {"id": "main", "path": str(self.main), "token": f"main.{self.token}"}
        self.mine = {"id": self.tab, "path": str(self.wt), "token": f"{self.tab}.{self.token}"}

    def sockets(self, read_tabs, approve_tabs):
        """A read socket (no token anywhere in its file) and a consent socket, both alive for `servers()`."""
        servers = {}
        for role in ("read", "approve"):
            health, port = bind_free(lambda p: ThreadingHTTPServer(("127.0.0.1", p), att.HealthOnly), free_port)
            health.body = {"ok": True, "version": "0", "serverId": self.server_id}
            threading.Thread(target=health.serve_forever, daemon=True).start()
            self.addCleanup(health.server_close)
            self.addCleanup(health.shutdown)
            servers[role] = port
        dt.write_state(self.main, servers["read"], None, "127.0.0.1", server_id=self.server_id, kind="project", common_dir=self.project.common_dir,
                       tabs=read_tabs, role="read", peer=servers["approve"])
        dt.write_state(self.main, servers["approve"], self.token, "127.0.0.1", server_id=self.server_id, kind="project", common_dir=self.project.common_dir,
                       tabs=approve_tabs, role="approve", peer=servers["read"])
        return servers

    def wait(self, timeout, approvals):
        return dt._wait_for_tab(self.project.common_dir, self.server_id, self.tab, timeout, approvals)

    def test_a_read_socket_that_knows_the_tab_does_not_end_the_wait_for_a_consent_socket(self) -> None:
        self.sockets([self.main_tab, self.mine], [self.main_tab])
        started = time.monotonic()
        self.assertIsNone(self.wait(0.8, True), "no token for the tab on any consent socket: the wait times out")
        self.assertGreaterEqual(time.monotonic() - started, 0.6, "it really waited")

    def test_the_same_files_end_the_wait_of_a_target_without_consents(self) -> None:
        servers = self.sockets([self.main_tab, self.mine], [self.main_tab])
        item = self.wait(2.0, False)
        self.assertTrue(item, "control: the read socket has the tab")
        self.assertEqual(item["port"], servers["read"])

    def test_a_consent_socket_that_lists_the_tab_without_its_token_does_not_end_the_wait(self) -> None:
        bare = {"id": self.tab, "path": str(self.wt)}
        self.sockets([self.main_tab, self.mine], [self.main_tab, bare])
        self.assertIsNone(self.wait(0.8, True))

    def test_the_wait_ends_when_the_token_appears_on_the_consent_socket(self) -> None:
        servers = self.sockets([self.main_tab, self.mine], [self.main_tab])
        threading.Timer(0.6, dt.refresh_tabs, args=(self.main, servers["approve"], [self.main_tab, self.mine], os.getpid())).start()
        started = time.monotonic()
        item = self.wait(8.0, True)
        self.assertTrue(item, "the record of the consent socket with the token ends the wait")
        self.assertGreaterEqual(time.monotonic() - started, 0.4, "not before the token was there")
        self.assertEqual(item["port"], servers["approve"])
        self.assertTrue(item["approvals"])
        self.assertTrue(next(tab for tab in item["tabs"] if tab["id"] == self.tab).get("token"))

    def test_the_command_prints_no_address_while_the_token_is_missing(self) -> None:
        self.sockets([self.main_tab, self.mine], [self.main_tab])
        args = att.serve_args(self.wt)
        plan = dt.plan_serve(args)
        self.assertEqual(plan.mode, "append", "control: the fake servers are a running project server")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dt, "REGISTRATION_WAIT_S", 1.0), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dt.append_command(args, plan)
        self.assertEqual(code, 2, (out.getvalue(), err.getvalue()))
        self.assertNotIn("#zgoda", out.getvalue() + err.getvalue(), "no address with or without a token")
        self.assertNotIn("http://", out.getvalue(), "stdout carries no address at all")
        self.assertIn("registration_timeout", out.getvalue() + err.getvalue())
        self.assertEqual(list(dt.pins_dir(self.server_id).glob("*.json")) if dt.pins_dir(self.server_id).exists() else [], [], "the pin was taken back")

    def test_the_command_prints_the_address_when_the_consent_socket_has_the_token(self) -> None:
        self.sockets([self.main_tab, self.mine], [self.main_tab, self.mine])
        args = att.serve_args(self.wt)
        plan = dt.plan_serve(args)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dt, "REGISTRATION_WAIT_S", 1.0), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dt.append_command(args, plan)
        self.assertEqual(code, 0, (out.getvalue(), err.getvalue()))
        last = out.getvalue().strip().splitlines()[-1]
        self.assertIn(f"?tab={self.tab}#zgoda={self.tab}.", last)


class PlanOfTheServe(att.Base):
    def note_text(self, key_text) -> list:
        """The beginning of the message of `srv.serve.mainManifest` in both languages (the part before the reason)."""
        return [i18n.t(lang, "srv.serve.mainManifest", reason="\0").split("\0")[0] for lang in ("pl", "en")]

    def test_a_main_checkout_without_a_manifest_is_announced_not_silently_standalone(self) -> None:
        wt = self.worktree("keyless")
        (self.main / ".github" / ".skillsync.yaml").unlink()
        plan = dt.plan_serve(att.serve_args(wt, port=free_port()))
        self.assertEqual(plan.kind, "standalone")
        self.assertIn("srv.serve.mainManifest", [key for key, _params in plan.notes], "the plan says why the server will not be the one of the project")

    def test_the_start_prints_that_note_on_stderr(self) -> None:
        wt = self.worktree("keyless-start")
        (self.main / ".github" / ".skillsync.yaml").unlink()
        proc, port = start_serve(self, CLI, wt, [], self.env, free_port)
        self.servers.append(proc)
        record = self.record_at(port)
        self.assertEqual(record["kind"], "standalone")
        self.assertEqual([tab["id"] for tab in record["tabs"]], [self.tab_of(wt)], "one tab: the directory of the command")
        _out, err, _graceful = stop_serve(proc)
        self.assertTrue(any(prefix and prefix in err for prefix in self.note_text("srv.serve.mainManifest")), err)

    def test_a_main_checkout_with_a_manifest_gives_no_such_note(self) -> None:
        wt = self.worktree("keyed")
        plan = dt.plan_serve(att.serve_args(wt, port=free_port()))
        self.assertEqual(plan.kind, "project", "control: with a readable manifest the same start is the server of the project")
        self.assertNotIn("srv.serve.mainManifest", [key for key, _params in plan.notes])

    def test_a_read_port_alone_is_a_preview_without_consents_so_standalone(self) -> None:
        wt = self.worktree("preview")
        plan = dt.plan_serve(att.serve_args(wt, read_port=free_port()))
        self.assertEqual((plan.kind, plan.mode), ("standalone", "start"))

    def test_a_read_port_with_a_consent_port_is_the_server_of_the_project(self) -> None:
        wt = self.worktree("both")
        first, second = free_port(), free_port()
        plan = dt.plan_serve(att.serve_args(wt, read_port=first, approve_port=second))
        self.assertEqual((plan.kind, plan.mode), ("project", "start"), "control: the same flags with the second socket")

    def test_a_port_with_a_broken_dashboard_section_starts_and_registers_only_the_start_directory(self) -> None:
        busy = self.worktree("busy", runs=True)
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees: 5\n")
        proc, port = start_serve(self, CLI, self.main, [], self.env, free_port)
        self.servers.append(proc)
        record = self.record_at(port)
        self.assertEqual([tab["id"] for tab in record["tabs"]], ["main"], "backward compatible: the start directory, nothing else")
        time.sleep(2.6)   # more than one refresh of the list: the busy worktree does not slip in
        self.assertEqual([tab["id"] for tab in self.record_at(port)["tabs"]], ["main"])
        self.assertNotIn(self.tab_of(busy), json.loads(att.get(port, "/api/tabs")[1])["tabs"].__repr__())
        self.assertEqual(att.get(port, "/api/status")[0], 200, "the page of the start directory works")


# ======================================================================================== (9) a server of one tab, as before

class SingleTabServer(Stack):
    """No git project: one tab, the observer thread, and a change of the files reaches `/events` as it always did."""

    def setUp(self) -> None:
        super().setUp()
        self.plain = self.tmp / "plain"
        wtt.write(self.plain / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        wtt.write(self.plain / "docs" / "plans" / "first.md", "# Plan: first\n\nStatus: in_progress\n")
        self.board = self.new_board(self.plain)
        self.registry = srv.TabRegistry(self.board)
        self.observer = srv.Observer(self.registry, self.board.stop)
        self.addCleanup(self.registry.stop_all)

    def stop_observer(self) -> None:
        self.board.stop.set()
        self.observer.join(5.0)

    def test_a_change_of_the_files_is_a_change_event_through_the_one_observer(self) -> None:
        before = threading.active_count()
        self.observer.start()
        self.addCleanup(self.stop_observer)
        self.assertEqual(threading.active_count() - before, 1, "one thread of the observer, no thread of the board's own")
        self.serve()
        stream = self.sse()
        self.assertIsNotNone(stream.event("hello"))
        wtt.write(self.plain / "docs" / "plans" / "second.md", "# Plan: second\n\nStatus: in_progress\n")
        self.assertIsNotNone(stream.event("change", 8.0), "the page is told about a new file in the directory")
        self.assertGreater(self.board.version, 0)

    def test_the_ticks_alone_see_the_change_of_a_single_tab_without_a_request_for_it(self) -> None:
        self.assertEqual(len(self.registry.ids()), 1)
        self.tick(2)
        version = self.board.version
        wtt.write(self.plain / "docs" / "plans" / "third.md", "# Plan: third\n\nStatus: in_progress\n")
        self.tick(2)
        self.assertGreater(self.board.version, version, "the only tab of a server is always watched: nobody has to ask for it first")

    def test_a_server_of_one_tab_does_not_refresh_a_list(self) -> None:
        self.tick(int(10.0 / STEP))
        self.assertEqual(self.observer.stats["refreshes"], 0)
        self.assertEqual(self.registry.ids(), [self.registry.default_id])


class SingleTabServerInASubprocess(att.Base):
    def test_a_plain_directory_gets_change_events_from_the_server_process(self) -> None:
        plain = self.tmp / "plain-proc"
        wtt.write(plain / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        wtt.write(plain / "docs" / "plans" / "first.md", "# Plan: first\n\nStatus: in_progress\n")
        proc, port = start_serve(self, CLI, plain, [], self.env, free_port, "--poll", "0.2")
        self.servers.append(proc)
        stream = Sse(port)
        self.addCleanup(stream.close)
        self.assertIsNotNone(stream.event("hello"))
        wtt.write(plain / "docs" / "plans" / "second.md", "# Plan: second\n\nStatus: in_progress\n")
        self.assertIsNotNone(stream.event("change", 10.0), "a server of one tab keeps announcing changes as before")


# ======================================================================================== (10) the stop and the errors of the observer

class StopAndErrors(Stack):
    POLL = 0.05

    def test_the_observer_ends_with_the_stop_of_the_server_and_leaves_no_thread(self) -> None:
        self.start()
        before = threading.active_count()
        self.observer.start()
        self.assertTrue(self.observer.alive())
        self.assertEqual(threading.active_count() - before, 1)
        self.board.stop.set()
        started = time.monotonic()
        self.observer.join(3.0)
        self.assertLess(time.monotonic() - started, 3.0, "the thread ended inside the limit of the join")
        self.assertFalse(self.observer.alive())
        self.assertEqual(threading.active_count(), before, "no thread of the observer is left")
        self.assertNotIn("observer", [thread.name for thread in threading.enumerate()])

    def test_the_stop_of_a_server_with_sixteen_boards_ends_the_one_thread(self) -> None:
        for number in range(15):
            self.live_worktree(f"s{number:02d}")
        self.start()
        self.assertEqual(len(self.registry.ids()), MAX_TABS)
        before = threading.active_count()
        self.observer.start()
        time.sleep(0.2)
        self.registry.stop_all()
        self.board.stop.set()
        self.observer.join(3.0)
        self.assertFalse(self.observer.alive())
        self.assertEqual(threading.active_count(), before)

    def test_an_exception_in_a_tick_does_not_kill_the_thread_and_is_logged_once(self) -> None:
        self.start()
        calls = []
        real = self.observer.tick

        def flaky(now=None):
            calls.append(now)
            if len(calls) <= 8:
                raise RuntimeError("the tick broke")
            return real(now)

        self.observer.tick = flaky
        self.observer.start()
        self.addCleanup(self.stop_it)
        self.wait_until(lambda: len(calls) >= 14, "the observer goes on after the failures", 15.0)
        self.assertTrue(self.observer.alive(), "an exception in a tick does not end the observer")
        self.assertLessEqual(len(self.logged), 1, f"a lasting error is logged at most once a minute: {self.logged}")
        self.assertGreaterEqual(len(self.logged), 1, "but it is logged")

    def stop_it(self) -> None:
        self.board.stop.set()
        self.observer.join(5.0)

    def test_an_error_of_one_board_is_counted_and_the_next_tick_works(self) -> None:
        self.live_worktree("second")
        self.start()
        second = self.registry.board(self.tab_of(self.tmp / "second"))
        for board in self.registry.boards():
            board.touch(now=self.clock[0])
        real = srv.Board.check_once
        raised = []

        def check_once(board):
            if board is second and not raised:
                raised.append(1)
                raise OSError("the disk went away")
            return real(board)

        with mock.patch.object(srv.Board, "check_once", check_once):
            wtt.write(self.tmp / "second" / "docs" / "plans" / "later.md", "# Plan: later\n\nStatus: in_progress\n")
            result = self.tick()                                  # must not raise
            self.assertEqual(result["changed"].count(str(second.root)), 0)
            self.assertEqual(raised, [1])
            version = second.version
            self.tick(2)
        self.assertGreater(second.version, version, "the next tick looks again and finds the change that the failed one missed")
        self.assertLessEqual(len(self.logged), 1)

    def test_a_broken_manifest_of_the_main_directory_stops_the_additions_but_not_the_observer(self) -> None:
        self.start()
        self.assertEqual(self.registry.ids(), ["main"])
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees: 5\n")
        late = self.live_worktree("late")
        for _ in range(3):
            self.assertFalse(self.observer.refresh(), "nothing changes while the list cannot be read (fail closed)")
        self.assertNotIn(self.tab_of(late), self.registry.ids())
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        self.assertTrue(self.observer.refresh())
        self.assertIn(self.tab_of(late), self.registry.ids(), "once the manifest is right the tab comes")
        self.assertLessEqual(len(self.logged), 1, "the repeated failure is logged once")


# ======================================================================================== review 1 (M-4 fix 1): waiting, write guard, clock

def given_row(word, sha) -> str:
    return json.dumps({"word": word, "at": "2026-11-25T10:00:00+00:00", "sha": sha, "by": "cli", "schema": 1}) + "\n"


class WaitingOfEveryTab(Stack):
    """`worktrees[].waiting` comes from the files at the refresh of the list: current also for a tab nobody looks at, never True after a consent."""

    def setUp(self) -> None:
        super().setUp()
        self.other = self.live_worktree("other", trace=False)
        template = make_project(self.tmp / "template")
        for rel in (".skillsync", "docs"):
            shutil.copytree(template / rel, self.other / rel, dirs_exist_ok=True)
        wtt.write(child(self.common / "refs" / "heads", "other"), sha_of("other") + "\n")
        wait_for_cp(self.other)
        self.tab = self.tab_of(self.other)
        self.start()
        self.serve()

    def flag(self, tab=None):
        row = next(item for item in self.registry.worktrees() if item["id"] == (tab or self.tab))
        return row["waiting"]

    def test_a_tab_nobody_ever_looked_at_with_a_pending_consent_is_waiting_after_one_refresh(self) -> None:
        board = self.registry.board(self.tab)
        self.assertIsNone(board.last_request, "control: nobody asked for this tab")
        self.assertFalse(self.flag(), "before the first refresh there is nothing to say")
        self.observer.refresh()
        self.assertTrue(self.flag())
        self.assertFalse(self.flag("main"), "the main checkout waits for nothing")
        self.assertIsNone(board.last_request, "the flag did not need a request or a status of the tab")
        status = self.get_json("/api/status")[1]
        self.assertEqual({row["id"]: row["waiting"] for row in status["worktrees"]}, {"main": False, self.tab: True})

    def test_a_consent_given_by_the_cli_clears_the_flag_at_the_next_refresh_without_the_tab_being_opened(self) -> None:
        self.observer.refresh()
        self.assertTrue(self.flag())
        wtt.write(self.other / RUN / "approvals.jsonl", given_row("CP", sha_of("other")))
        self.observer.refresh()
        self.assertFalse(self.flag(), "the stale True of the last status is gone")
        self.assertEqual(self.registry.board(self.tab).last_request, None)

    def test_a_consent_for_another_head_does_not_clear_the_flag(self) -> None:
        self.observer.refresh()
        wtt.write(self.other / RUN / "approvals.jsonl", given_row("CP", sha_of("elsewhere")))
        self.observer.refresh()
        self.assertTrue(self.flag())

    def test_the_flag_follows_the_files_both_ways(self) -> None:
        self.observer.refresh()
        wtt.write(self.other / RUN / "checkpoints.md", "RUN=2026-11-25T10:00:00+00:00\n- 11:30 zrobione: S9; w toku: S10\n")
        self.observer.refresh()
        self.assertFalse(self.flag(), "the session works again")
        wait_for_cp(self.other)
        self.observer.refresh()
        self.assertTrue(self.flag(), "and waits again")


class ClockOfTheRefresh(Stack):
    """`_next_refresh` is one interval after the refresh ENDED; a pass that takes long is logged once."""

    def slow_refresh(self, seconds, calls):
        real = self.observer.refresh

        def refresh():
            calls.append(self.clock[0])
            result = real()
            self.clock[0] += seconds
            return result

        self.observer.refresh = refresh

    def test_a_refresh_longer_than_the_interval_does_not_make_the_next_passes_refresh_again(self) -> None:
        self.start()
        calls: list = []
        self.slow_refresh(5.0, calls)
        self.clock[0] = 2.0
        self.observer.tick(2.0)
        self.assertEqual(len(calls), 1)
        for now in (7.0, 8.0, 8.9):
            self.clock[0] = now
            self.observer.tick(now)
        self.assertEqual(len(calls), 1, "the interval counts from the END of the refresh (clock 7.0), not from its start")
        self.clock[0] = 9.0
        self.observer.tick(9.0)
        self.assertEqual(len(calls), 2)

    def test_the_rounds_of_the_absence_are_refreshes_apart_by_at_least_the_interval(self) -> None:
        wt = self.live_worktree("doomed")
        tab = self.tab_of(wt)
        self.start()
        calls: list = []
        self.slow_refresh(3.0, calls)
        self.remove_worktree("doomed")
        self.clock[0] = 2.0
        self.observer.tick(2.0)                  # missing once; the refresh ends at 5.0
        for now in (5.0, 6.0, 6.9):
            self.clock[0] = now
            self.observer.tick(now)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(self.registry.board(tab), "one round is not enough")
        self.clock[0] = 7.0
        self.observer.tick(7.0)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.registry.board(tab), "the second round, one interval after the first ended, removes it")

    def test_a_tick_longer_than_five_seconds_is_logged_once(self) -> None:
        self.start()
        calls: list = []
        self.slow_refresh(6.0, calls)
        self.clock[0] = 2.0
        self.observer.tick(2.0)
        slow = [line for line in self.logged if "przejście" in line]
        self.assertEqual(len(slow), 1, self.logged)
        self.observer._next_refresh = 0.0
        self.clock[0] = 20.0
        self.observer.tick(20.0)
        self.assertEqual(len([line for line in self.logged if "przejście" in line]), 1, "a lasting slowness is not logged on every pass")

    def test_the_table_of_logged_keys_stays_small(self) -> None:
        self.start()
        for number in range(500):
            self.observer._error(f"board:/tmp/x{number}", RuntimeError("x"))
        self.assertLessEqual(len(self.observer._logged), srv.Observer.MAX_LOGGED)

    def test_an_observer_that_does_not_stop_in_time_is_logged(self) -> None:
        class Stuck:
            def join(self, timeout=None):
                return None

            def alive(self):
                return True

        srv._stop_observer(Stuck())
        self.assertTrue(any("obserwator nie zakończył" in line for line in self.logged), self.logged)


class WriteGuardIsFresh(Stack):
    """R3 at the write reads the marker and the manifest of the main checkout NOW; the start directory with an exclusion is shut for reads, not removed."""

    def setUp(self) -> None:
        super().setUp()
        self.starter = self.live_worktree("starter", trace=False)
        self.other = self.live_worktree("other", trace=False)
        template = make_project(self.tmp / "template")
        for root in (self.starter, self.other):
            for rel in (".skillsync", "docs"):
                shutil.copytree(template / rel, root / rel, dirs_exist_ok=True)
            wait_for_cp(root)
        self.tab = self.tab_of(self.other)
        self.default = self.tab_of(self.starter)
        self.start(default=self.starter)
        self.serve()
        self.tokens = {tab: self.registry.token_for(tab) for tab in (self.tab, self.default)}
        self.sha = {}
        for tab in (self.tab, self.default):
            status = self.get_json(f"/api/status?tab={tab}")[1]
            offer = next((item for item in status["approvals"]["actions"] if item["kind"] == "cp" and item["state"] == "waiting"), None)
            self.assertIsNotNone(offer, "control: the tab offers CP")
            self.sha[tab] = offer["sha"]

    def consent(self, tab=None):
        tab = tab or self.tab
        return self.post_json(self.tokens[tab], {"kind": "cp", "path": RUN_ID, "sha": self.sha[tab], "plan": PLAN})

    def written(self, root=None) -> list:
        return _approvals_rows(root or self.other)

    def test_control_the_consent_goes_through(self) -> None:
        code, body = self.consent()
        self.assertEqual(code, 200, body)
        self.assertEqual(len(self.written()), 1)

    def test_a_marker_added_to_a_tab_before_its_first_refresh_gets_409_tab_changed(self) -> None:
        self.assertEqual(self.observer.stats["refreshes"], 0)
        wtt.write(self.other / ".skillsync" / "no-dashboard", "")
        code, body = self.consent()
        self.assertEqual((code, body.get("code")), (409, "tabChanged"), body)
        self.assertEqual(self.written(), [])

    def test_an_exclusion_of_the_manifest_written_between_two_refreshes_refuses_the_write(self) -> None:
        self.observer.refresh()
        self.assertIn(self.tab, self.registry.ids(), "control: the refresh before the change left the tab")
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees:\n    exclude: [" + json.dumps(str(self.other)) + "]\n")
        code, body = self.consent()
        self.assertEqual((code, body.get("code")), (409, "tabChanged"), body)
        self.assertEqual(self.written(), [])

    def test_a_broken_manifest_of_the_main_directory_refuses_the_write_and_a_repaired_one_allows_it(self) -> None:
        self.observer.refresh()
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\ndashboard:\n  worktrees: 5\n")
        code, body = self.consent()
        self.assertEqual((code, body.get("code")), (409, "tabChanged"), body)
        self.assertEqual(self.written(), [], "fail closed: the exclusion list cannot be read")
        wtt.write(self.main / ".github" / ".skillsync.yaml", "skills: [dashboard]\n")
        self.assertEqual(self.consent()[0], 200)

    def test_the_start_directory_with_an_exclusion_answers_404_to_reads_but_lives_and_comes_back(self) -> None:
        marker = self.starter / ".skillsync" / "no-dashboard"
        wtt.write(marker, "")
        self.observer.refresh()
        self.assertIn(self.default, self.registry.ids(), "never removed")
        for path in ("/api/status", f"/api/status?tab={self.default}", f"/api/doc?path={PLAN}", "/api/tests?run=x"):
            code, body = self.get_json(path)
            self.assertEqual(code, 404, (path, body))
            self.assertEqual(body.get("code") if isinstance(body, dict) else None, "unknownTab", path)
        self.assertEqual(self.http("GET", "/healthz")[0], 200, "the server lives")
        self.assertEqual(self.get_json("/api/tabs")[0], 200)
        self.assertEqual(self.get_json(f"/api/status?tab={self.tab}")[0], 200, "the other tab is untouched")
        code, body = self.consent(self.default)
        self.assertEqual((code, body.get("code")), (409, "tabChanged"), body)
        self.assertEqual(self.written(self.starter), [])
        marker.unlink()
        self.observer.refresh()
        self.assertEqual(self.get_json("/api/status")[0], 200, "the tab reads again once the marker is gone")
        self.assertEqual(self.consent(self.default)[0], 200)


if __name__ == "__main__":
    unittest.main()
