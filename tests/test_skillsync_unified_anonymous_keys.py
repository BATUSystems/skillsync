"""CORE-2000-UNIFIED K2 (decision "(a) main keys + test", 2026-11-27): what a reader without a session gets from `/api/status`, `/api/tabs` and
`/api/history` is a list of allowed MAIN keys (of the answer and of every row of its lists of tabs and of plans: an unknown one is left out), and the
whole tree of keys of those answers is pinned in `tests/fixtures/unified-anonymous-keys.json`; the link to the session of the agent (`session.url`)
is not given to such a reader:
python3 -m unittest tests.test_skillsync_unified_anonymous_keys

A real HTTP server of two tabs (`Tabs`) over a project made as rich as the status can be: the page socket, the read socket, `--single-origin`, an
English project and an account that must change its password. No git, no `skillsync` subprocess."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from layers_helpers import guard_real_state  # noqa: E402
from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from skillsync_workflow import prototype_server as proto  # noqa: E402
from skillsync_workflow import test_events  # noqa: E402
from tests.serve_start import bind_free  # noqa: E402
from tests.test_skillsync_dashboard_server import PLAN, RUN, SPEC, SessionLogin, free_port, install_fake_cli, session_request  # noqa: E402
from tests.test_skillsync_dashboard_tabs_approvals import RUN_ID, Tabs, write  # noqa: E402
from tmp_paths import descend  # noqa: E402

PINNED = ROOT / "tests" / "fixtures" / "unified-anonymous-keys.json"
FIXTURES = ROOT / "tests" / "fixtures" / "dashboard" / "questions" / "input" / "docs" / "prototypes"
SESSION_URL = "https://agent.example.com/session/kanarek-sesji-4471"
SHA = "89abcdef0123456789abcdef0123456789abcdef"
MUSTCHANGE = "must@example.com"
MUSTCHANGE_PASSWORD = "Domyslne-" + "Haslo-" + "12345"
OPAQUE = ("texts",)   # the catalogue of the interface texts of another language: its keys are the catalogue, not data of the project
ROUTES = ("/api/status", "/api/tabs", "/api/history")

HOW = ("\nA key of an answer for a reader WITHOUT a session changed. Decide whether it may be read by anybody in the network:\n"
       "- yes: add the path to tests/fixtures/unified-anonymous-keys.json (a new MAIN key of an answer or of a row also to the PUBLIC_*_KEYS of\n"
       "  skillsync_workflow/dashboard_http.py) and describe the field in docs/dashboard-contract.md;\n"
       "- no: leave it out in `public_status` / `public_tabs_response` / `public_history`.\n"
       "A key that disappeared: the page of an anonymous reader may still read it (renderer, docs/dashboard-contract.md).\n")


def tree(value, prefix: str = "") -> set:
    found: set = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            found.add(path)
            if path not in OPAQUE:
                found |= tree(item, path)
    elif isinstance(value, list):
        for item in value:
            found |= tree(item, prefix + "[]")
    return found


def empty_containers(value, prefix: str = "") -> tuple[set, set]:
    """`(paths of lists, paths of lists that had an item)`: a list that is empty in every answer pins nothing below it."""
    seen, filled = set(), set()
    if isinstance(value, dict):
        items = [(f"{prefix}.{key}" if prefix else key, item) for key, item in value.items()]
    elif isinstance(value, list):
        seen.add(prefix)
        if value:
            filled.add(prefix)
        items = [(prefix + "[]", item) for item in value]
    else:
        items = []
    for path, item in items:
        if path not in OPAQUE:
            more_seen, more_filled = empty_containers(item, path)
            seen |= more_seen
            filled |= more_filled
    return seen, filled


class KeysCase(Tabs, unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="anon-keys-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        install_fake_cli(self)
        patcher = mock.patch.object(srv, "log", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        feedback = mock.patch.object(engine, "feedback_state", lambda root: {"enabled": True, "total": 1, "drafts": [
            {"draft": "F1", "state": "waiting", "sha": SHA, "sha12": SHA[:12], "reason": None, "generic": True, "view": {"title": "tytul"}}]})
        feedback.start()
        self.addCleanup(feedback.stop)
        self.build_tabs(self.tmp)
        self.enrich(self.root_a)
        for board in (self.board_a, self.board_b):
            board.session_url = SESSION_URL
            board.__dict__.pop("prototype_acceptance", None)   # the stub of `new_board`: the real reading, in-process
            board.ahead = lambda branch, head: {"remote": "origin", "pushed": False, "ahead": 21, "commitsMore": 1,
                                                "commits": [{"sha": "abc1234", "subject": "temat commita"}]}
        self.extra_worktree("hidden")
        self.reg.reconcile(dw.discover(self.project, inactive_days=0))   # a worktree out of the tabs: `hidden[]`
        self.start_server()

    # --- a project that fills as much of the status as there is

    def enrich(self, root: Path) -> None:
        run = descend(root, RUN)
        plan = descend(root, PLAN)
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "Przebieg: 2026-01-10-demo-etap-1\n", "Przebieg: 2026-01-10-demo-etap-1\nPR: #7 (link: https://example.com/pr/7)\n")
            .replace("docs/prototypes/demo-etap-1", "docs/prototypes/run-alpha")
            .replace("- [ ] S4 Dokumentacja", "".join(f"- [ ] F4.{number} Podkrok **{number}**\n" for number in range(1, 10)) + "- [ ] S4 Dokumentacja")
            + "\n## Wynik\n\n" + "".join(f"Akapit **{number}** wyniku.\n\n" for number in range(20)), encoding="utf-8")
        spec = descend(root, SPEC)
        spec.write_text(spec.read_text(encoding="utf-8").replace("Plan: ", "Prototyp: run-alpha\nPlan: ", 1)
                        + "\n## Pytania otwarte\n\n- Q1 [ZAŁOŻENIE]: kanał - przyjęte: aplikacja\n- Q4: czy wysyłać **podsumowanie**?\n", encoding="utf-8")
        shutil.copytree(FIXTURES, descend(root, "docs/prototypes"), dirs_exist_ok=True)
        questions = descend(root, "docs/prototypes/run-alpha/questions.json")
        questions.write_text(questions.read_text(encoding="utf-8").replace("2026-01-11-demo-alerts.md", "demo-etap-1.md")
                             .replace("docs/specs/2026-01-11-demo-etap-1.md", SPEC).replace("docs/specs/demo-etap-1.md", SPEC)
                             .replace('"addressees":[]', '"addressees":["ekspert@example.com"]').replace("Czy numer", "Czy **numer**"), encoding="utf-8")
        write(descend(root, "docs/prototypes/run-alpha/prototype.json"), json.dumps({"slug": "run-alpha", "title": "Tytul"}) + "\n")
        with descend(root, ".git/config").open("a", encoding="utf-8") as handle:
            handle.write('[remote "origin"]\n\turl = https://github.com/org/repo.git\n')
        index = ('<!doctype html>\n<html data-prototype-id="run-alpha">\n<body>\n<section class="screen" id="s1"><div class="frame">'
                 '<h2 data-proto-id="s1-h">Ekran</h2></div></section>\n</body>\n</html>\n')
        write(descend(root, "docs/prototypes/run-alpha/index.html"), index)
        proto.write_writing(str(descend(root, "docs/prototypes/run-alpha")), int(time.time() * 1000), 3000, by="agent", screens=3)
        hashes = proto.screen_hashes(str(descend(root, "docs/prototypes/run-alpha")))

        def operation(number, kind, payload, thread=None):
            found = {"id": "op-" + str(number).zfill(26), "type": kind, "at": "2026-11-05T09:%02d:00.000Z" % number, "by": "ktos@example.com",
                     "cid": f"c-{number}", "payload": payload}
            return {**found, "threadId": thread} if thread else found

        message = lambda number: {"message": {"id": f"m-{number}", "text": "tekst"}, "screen": "s1",
                                  "anchor": {"kind": "screen", "x": 0.5, "y": 0.5, "iteration": 1}}
        operations = [operation(1, "accept", {"scope": "screen", "screens": {"s1": hashes["screens"]["s1"]}, "iteration": 1, "via": "ui"}),
                      operation(2, "create", message(2), "th-1")]
        write(descend(root, "docs/prototypes/run-alpha/comments.json"), proto.serialize_document("run-alpha", operations))
        with (run / "checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write("- 11:30 zrobione: S2a; w toku: S2b; eta: testy S2b do 12:00\n"
                         "- 11:50 zrobione: S2b; w toku: S3a; eta: API do 12:30; czeka: odpowiedź Q4 od Jana\n"
                         "- 12:40 zrobione: S3a; w toku: S3b; eta: testy API do 13:00\n"
                         + "".join(f"- 12:{40 + number} w toku: F4.{number}\n- 12:{41 + number} zrobione: F4.{number}\n" for number in range(1, 8))
                         + "- 12:50 w toku: S3b, F4.8; **uwaga** na S3b; czeka: CP\n")
        rows = [{"schema": 1, "at": "2026-01-10T11:00:00+01:00", "word": word, "sha": SHA, "by": "ktos", "role": "approver", "session": "abcd1234",
                 "source": "dashboard", "file": rel, "via": {"peer": "10.0.0.9", "host": "h", "port": 1}, "worktree": "a",
                 "planState": SHA[:12]}
                for word, rel in (("GO plan", SPEC), ("GO implement", PLAN))]
        rows.append({**rows[0], "word": "CP", "file": None, "sha": "abc1234def5678", "target": "feature/demo"})
        write(run / "approvals.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        write(run / "readiness.md", "Readiness: PASS\nData: 2026-01-10\nPlan-sha: " + SHA + "\n")
        journal = [{"v": 1, "t": "2026-01-10T11:00:00+01:00", "ev": "start", "agent": "a-1", "role": "reviewer", "kind": "review", "profile": "spec",
                    "step": None, "round": 1, "eta": "11:30"},
                   {"v": 1, "t": "2026-01-10T11:20:00+01:00", "ev": "end", "agent": "a-1", "result": "PASS", "report": "S1-implementer.md"},
                   {"v": 1, "t": "2026-01-10T11:25:00+01:00", "ev": "start", "agent": "a-2", "role": "implementer", "kind": "run", "profile": None,
                    "step": "S2a", "round": None, "eta": "12:00"}]
        write(run / "agents.jsonl", "".join(json.dumps(row) + "\n" for row in journal))
        writer = test_events.EventWriter(test_events.new_attempt(root, RUN_ID, "S2a")[0])
        writer.start(run=RUN_ID, step="S2a", stepSource="flag", attempt=1, command="runner -q", tool="runner", pid=1, levels="command", treeStart=None)
        writer.test("t1", "test one", "failed", group="grupa", msg="boom", ms=2)
        writer.test("t2", "test two", "running", group="grupa")
        writer.end("FAIL", 1, source="command", reason="porażka", ms=5)
        write(descend(root, ".skillsync/runs") / RUN_ID / "tests" / "zly krok" / "1.jsonl", "x\n")   # `tests.skipped[]`
        write(descend(root, "docs/plans/2025-10-01-porzucony.md"), "# Plan: porzucony\n\nStatus: archived (porzucony, wcześniej: approved)\n")

    # --- requests

    def servers(self) -> dict:
        """Every way an answer reaches a reader without a session."""
        read, port = bind_free(lambda port: srv.make_server(self.reg, "127.0.0.1", port, role=ports.ROLE_READ), free_port)
        threading.Thread(target=read.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(self.stop_server, read)
        page, page_port = bind_free(lambda port: srv.make_server(self.reg, "127.0.0.1", port, role=ports.ROLE_PAGE, proto_app=self.kit.app), free_port)
        threading.Thread(target=page.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(self.stop_server, page)
        return {"both": self.port, "page": page_port, "read": port}

    def ask(self, port: int, path: str, headers: dict | None = None) -> dict:
        status, data, _headers = session_request(port, "GET", path, headers=headers)
        self.assertEqual(status, 200, (path, data))
        return data

    def must_change(self) -> SessionLogin:
        status, data, headers = session_request(self.port, "POST", "/_api/v1/login", {"login": MUSTCHANGE, "password": MUSTCHANGE_PASSWORD})
        self.assertEqual(status, 200, data)
        found = SessionLogin(self.port, status, data, headers)
        self.assertTrue(found.data["user"]["mustChange"], "control: it is such a session")
        return found

    def anonymous_answers(self) -> dict:
        """`{route: [answers]}`: the three socket roles, other plans, `--single-origin`, an English project; no session at all."""
        found: dict = {route: [] for route in ROUTES}
        servers = self.servers()
        queries = ["", f"?tab={self.id_a}&plan={PLAN}", f"?tab={self.id_b}", "?plan=docs/plans/2025-12-01-starszy.md"]
        for port in servers.values():
            for query in queries:
                found["/api/status"].append(self.ask(port, "/api/status" + query))
            found["/api/tabs"].append(self.ask(port, "/api/tabs"))
            found["/api/history"].append(self.ask(port, "/api/history"))
        self.kit.app.single_origin = True
        found["/api/status"].append(self.ask(self.port, f"/api/status?tab={self.id_a}&plan={PLAN}"))
        self.kit.app.single_origin = False
        write(descend(self.root_a, srv.MANIFEST), "language: en\ndelivery:\n  mode: wrong\n")   # `texts`, `delivery.error`
        found["/api/status"].append(self.ask(self.port, f"/api/status?tab={self.id_a}&plan={PLAN}"))
        write(descend(self.root_b, RUN) / "checkpoints.md", "RUN=2026-01-10T10:00:00+01:00\n- 10:00 w toku: S2b\n")   # nobody waits: `silence`
        found["/api/status"].append(self.ask(self.port, f"/api/status?tab={self.id_b}&plan={PLAN}"))
        write(descend(self.root_b, RUN) / "checkpoints.md", "RUN=2026-01-10T10:00:00+01:00\n- 10:05 w toku: S2b; czeka: odpowiedź na `Q9` od **Jana**\n")
        found["/api/status"].append(self.ask(self.port, f"/api/status?tab={self.id_b}&plan={PLAN}"))   # `waiting[].whatHtml`
        return found


class PinnedTreeTests(KeysCase):
    def test_the_whole_tree_of_keys_of_the_anonymous_answers_is_pinned(self) -> None:
        answers = self.anonymous_answers()
        pinned = json.loads(PINNED.read_text(encoding="utf-8"))
        for route in ROUTES:
            with self.subTest(route=route):
                found = set().union(*(tree(answer) for answer in answers[route]))
                new, gone = sorted(found - set(pinned[route])), sorted(set(pinned[route]) - found)
                self.assertEqual((new, gone), ([], []), f"{route}: new keys {new}, missing keys {gone}.{HOW}")
                seen, filled = set(), set()
                for answer in answers[route]:
                    more_seen, more_filled = empty_containers(answer)
                    seen |= more_seen
                    filled |= more_filled
                self.assertEqual(sorted(seen - filled), [], "the fixture must fill every list, else the keys of its rows are not pinned")

    def test_every_main_key_of_the_signed_in_answers_is_on_the_allowed_list(self) -> None:
        """A new main key is left out for an anonymous reader without a word; here the one who adds it is told to decide."""
        cookie = {"Cookie": self.session.pair}
        status = self.ask(self.port, f"/api/status?tab={self.id_a}&plan={PLAN}", cookie)
        tabs, history = self.ask(self.port, "/api/tabs", cookie), self.ask(self.port, "/api/history", cookie)
        for name, keys, allowed in (
                ("status", set(status), srv.PUBLIC_STATUS_KEYS), ("tabs", set(tabs), srv.PUBLIC_TABS_KEYS),
                ("history", set(history), srv.PUBLIC_HISTORY_KEYS),
                ("worktrees[]", {key for row in status["worktrees"] for key in row}, srv.PUBLIC_TAB_KEYS),
                ("tabs[]", {key for row in tabs["tabs"] for key in row}, srv.PUBLIC_TAB_KEYS),
                ("hidden[]", {key for row in tabs["hidden"] for key in row}, srv.PUBLIC_HIDDEN_KEYS),
                ("history.tabs[]", {key for row in history["tabs"] for key in row}, srv.PUBLIC_HISTORY_TAB_KEYS),
                ("history.items[]", {key for row in history["items"] for key in row}, srv.PUBLIC_HISTORY_ITEM_KEYS)):
            with self.subTest(answer=name):
                self.assertTrue(keys, "control: there is something to compare")
                self.assertEqual(sorted(keys - allowed), [], f"{name}: main keys that are not on the allowed list.{HOW}")

    def test_an_unknown_main_key_is_left_out_for_every_reader_without_a_session_and_a_signed_in_one_gets_it(self) -> None:
        canary = "kanarek-nowego-pola-9182"
        status, summary, hidden, worktrees, collect = (srv.Board.status, srv.TabRegistry.summary, srv.TabRegistry.hidden_summary,
                                                       srv.TabRegistry.worktrees, srv.history_response)
        rows = lambda items: [{**row, "newField": canary} for row in items]

        def history(registry):
            answer = collect(registry)
            return {**answer, "newField": canary, "tabs": rows(answer["tabs"]), "items": rows(answer["items"])}

        for owner, name, replacement in (
                (srv.Board, "status", lambda board, plan=None: {**status(board, plan), "newField": canary}),
                (srv.TabRegistry, "summary", lambda registry: rows(summary(registry))),
                (srv.TabRegistry, "hidden_summary", lambda registry: rows(hidden(registry))),
                (srv.TabRegistry, "worktrees", lambda registry: rows(worktrees(registry))),
                (srv, "history_response", history)):
            patcher = mock.patch.object(owner, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        servers = self.servers()
        must = {"Cookie": self.must_change().pair}
        for route in ROUTES:
            for name, port, headers in (*((name, port, None) for name, port in servers.items()), ("mustChange", self.port, must),
                                        ("a cookie that opens nothing", self.port, {"Cookie": self.session.stale_pair})):
                with self.subTest(route=route, reader=name):
                    answer = self.ask(port, route, headers)
                    self.assertNotIn(canary, json.dumps(answer))
                    self.assertNotIn("newField", json.dumps(answer))
            with self.subTest(route=route, reader="signed in"):
                self.assertIn(canary, json.dumps(self.ask(self.port, route, {"Cookie": self.session.pair})), "control")

    def test_a_session_that_must_change_its_password_gets_no_other_key_than_its_own_session(self) -> None:
        must = {"Cookie": self.must_change().pair}
        pinned = json.loads(PINNED.read_text(encoding="utf-8"))
        own = {"session.account", "session.roles", "session.mode"}
        for route, query in (("/api/status", f"?tab={self.id_a}&plan={PLAN}"), ("/api/tabs", ""), ("/api/history", "")):
            with self.subTest(route=route):
                found = tree(self.ask(self.port, route + query, must))
                self.assertEqual(sorted(found - set(pinned[route]) - own), [], HOW)
                if route == "/api/status":
                    self.assertEqual(own - found, set(), "control: the data of its own session")


class SessionUrlTests(KeysCase):
    def test_the_link_to_the_session_of_the_agent_is_only_for_a_signed_in_reader(self) -> None:
        servers = self.servers()
        must = {"Cookie": self.must_change().pair}
        for name, port, headers in (*((name, port, None) for name, port in servers.items()), ("mustChange", self.port, must)):
            with self.subTest(reader=name):
                answer = self.ask(port, "/api/status", headers)
                self.assertNotIn("kanarek-sesji", json.dumps(answer))
                self.assertIn("url", answer["session"], "the key of the contract stays")
                self.assertIsNone(answer["session"]["url"])
        self.assertEqual(self.ask(self.port, "/api/status", {"Cookie": self.session.pair})["session"]["url"], SESSION_URL, "control")

    def test_the_stream_of_changes_carries_no_data_of_the_status(self) -> None:
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", "/events")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        lines = []
        while not any(line.startswith("data:") for line in lines):
            lines.append(response.fp.readline().decode("utf-8").strip())
        data = json.loads(next(line for line in lines if line.startswith("data:"))[len("data:"):])
        self.assertEqual(sorted(data), ["heartbeat", "version"])


if __name__ == "__main__":
    unittest.main()
