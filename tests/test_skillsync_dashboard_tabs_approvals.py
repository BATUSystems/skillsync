"""CORE-1180 M-2: zgody przypiete do zakladki (token `<id>.<hmac>`), rejestr zakladek `TabRegistry` i izolacja zapisu miedzy katalogami.

Testy sprawdzaja KONTRAKT speca i kroku M-2 (K3, K9), nie implementacje: dwa katalogi robocze tego samego projektu git sa zbudowane PLIKAMI
(`.git` jako katalog i jako plik `gitdir:`, `worktrees/<id>/{HEAD,commondir,gitdir}`), zadne wywolanie `git` nie jest czescia testu. Serwer startuje na
porcie efemerycznym z puli testowej, kazda zgoda idzie prawdziwym HTTP (`/api/approve`, `/api/feedback-approve`), a dowodem izolacji jest ZAWARTOSC
plikow `approvals.jsonl` obu katalogow (nie tylko kod odpowiedzi). Nic nie dotyka prawdziwych katalogow stanu Skillsync (`guard_real_state`).
Atrapy sekretow sa skladane w czasie wykonania. Jedyny `skipTest` to brak uprawnien do dowiazan symbolicznych."""
from __future__ import annotations

import contextlib
import http.client
import io
import itertools
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from layers_helpers import guard_real_state  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from skillsync_workflow import feedback  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402
from tests.serve_start import bind_free  # noqa: E402
from tests.test_skillsync_dashboard_ports import two_ports  # noqa: E402
from tests.test_skillsync_dashboard_server import (ASSETS, DESCRIPTIONS, PLAN, RUN, SPEC, free_port,  # noqa: E402
                                                   install_fake_cli, make_project)
from tests.test_skillsync_feedback_command import CommandCase  # noqa: E402

RUN_ID = "2026-01-10-demo-etap-1"
JSON_HEADERS = {"Content-Type": "application/json", "X-Status-Board": "1"}
HEAD_A = "abc1234def5678"          # `make_project`: refs/heads/feature/demo
HEAD_B = "def5678abc1234"          # another tip for the second working directory
REFUSED = {"error": srv.TOKEN_REFUSED, "code": "approve_token"}
CLOCK0 = 1_800_000_000.0
TTL = 600.0                        # tombstone: 10 minutes
SECRET_TEXT = "tabs-" + "registry-" + "process-" + "0123456789abcdef"
FORBIDDEN_CONTROL = {chr(c) for c in range(0, 32)} | {chr(127)}


def write(path, text) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_registry(board, **kwargs):
    """`TabRegistry(board, secret=...)`: the contract does not fix the type of `secret`, so bytes are tried first and then text."""
    for secret in (SECRET_TEXT.encode("utf-8"), SECRET_TEXT):
        try:
            return srv.TabRegistry(board, secret=secret, **kwargs)
        except (TypeError, AttributeError):
            continue
    return srv.TabRegistry(board, **kwargs)


def link_worktree(main_root: Path, wt_root: Path, name: str, branch: str, tip: str | None = None) -> None:
    """Turns `wt_root` into a linked worktree of `main_root` the way git lays it out (files only)."""
    common = main_root / ".git"
    write(common / "config", "[core]\n\trepositoryformatversion = 0\n")
    if tip is not None:
        write(common / "refs" / "heads" / branch, tip + "\n")
    entry = common / "worktrees" / name
    write(entry / "HEAD", f"ref: refs/heads/{branch}\n")
    write(entry / "commondir", "../..\n")
    write(entry / "gitdir", str(wt_root / ".git") + "\n")
    git = wt_root / ".git"
    if git.is_dir():
        shutil.rmtree(git)
    write(git, f"gitdir: {entry}\n")


def approvals_rows(root: Path) -> list:
    path = Path(root) / RUN / "approvals.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wait_for_cp(root: Path) -> None:
    with (Path(root) / RUN / "checkpoints.md").open("a", encoding="utf-8") as handle:
        handle.write("- 11:20 zrobione: S9; w toku: —; czeka: CP\n")


def tree_snapshot(*roots: Path) -> dict:
    """Every file under `runs/` and `docs/` of the given directories with its bytes: a change anywhere shows up as a difference."""
    seen = {}
    for root in roots:
        for sub in (".skillsync/runs", "docs"):
            base = Path(root) / sub
            for path in sorted(base.rglob("*")) if base.exists() else []:
                if path.is_file():
                    seen[str(path)] = path.read_bytes()
    return seen


class Tabs:
    """Two tabs (A = main checkout, B = linked worktree) of one project git, one `TabRegistry`, one HTTP server."""
    SAME_HEAD = False
    SINGLE = False        # True: the registry knows only the default board (a server with one tab)

    def build_tabs(self, base: Path) -> None:
        self.base_dir = base
        self.root_a = make_project(base / "a")
        self.root_b = make_project(base / "b")
        branch_b = "feature/demo" if self.SAME_HEAD else "feature/other"
        link_worktree(self.root_a, self.root_b, "wtb", branch_b, None if self.SAME_HEAD else HEAD_B)
        for root in (self.root_a, self.root_b):
            wait_for_cp(root)
        self.project = dw.project_identity(str(self.root_a))
        self.assertIsNotNone(self.project)
        self.clock = [CLOCK0]
        self.board_a = self.new_board(self.root_a)
        self.board_b = self.new_board(self.root_b)
        self.reg = make_registry(self.board_a, project=None if self.SINGLE else self.project, now=lambda: self.clock[0])
        self.boards = {os.path.realpath(self.root_a): self.board_a, os.path.realpath(self.root_b): self.board_b}
        if self.SINGLE:
            self.id_a, self.id_b = self.reg.ids()[0], None
        else:
            self.id_a = self.register_root(self.root_a)
            self.id_b = self.register_root(self.root_b)

    def new_board(self, root: Path):
        board = srv.Board(root, poll=0.2, heartbeat=0.5, prototype_port=1, assets=ASSETS, descriptions=DESCRIPTIONS, approvals=True)
        board.prototype_acceptance = lambda slug: None
        self.addCleanup(board.stop.set)
        return board

    def entry_for(self, root: Path):
        wanted = dw.normalize_key(str(root))
        for entry in dw.list_entries(self.project):
            if dw.normalize_key(entry.path) == wanted:
                return entry
        self.fail(f"no entry for {root}")

    def register_root(self, root: Path, board=None) -> str:
        """Registers the directory (once) and returns its tab id."""
        entry = self.entry_for(root)
        if entry.id not in self.reg.ids():
            self.reg.register(entry, board=board or self.boards.get(os.path.realpath(root)) or self.new_board(root))
        return entry.id

    def extra_worktree(self, name: str, *, excluded: bool = False) -> Path:
        root = make_project(self.base_dir / name)
        link_worktree(self.root_a, root, name, f"feature/{name}", "0123abc" + name.encode().hex()[:7])
        wait_for_cp(root)
        if excluded:
            write(root / ".skillsync" / "no-dashboard", "")
        return root

    def start_server(self, verbose: bool = False) -> None:
        self.server, self.port = bind_free(lambda port: srv.make_server(self.reg, "127.0.0.1", port, verbose=verbose), free_port)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.addCleanup(self.stop_server, self.server)

    @staticmethod
    def stop_server(server) -> None:
        server.shutdown()
        server.server_close()

    # --- the path of a tab replaced after the registration (an ancestor renamed and replaced by a link to the renamed one)
    def swap_ancestor_for_a_link(self) -> Path:
        original = self.base_dir / "b"
        moved = self.base_dir / "b-moved"
        original.rename(moved)
        try:
            os.symlink(str(moved), str(original), target_is_directory=True)
        except (OSError, NotImplementedError):
            moved.rename(original)
            self.skipTest("this system refuses to create symlinks for the user")
        self.addCleanup(self.undo_swap)
        return moved

    def undo_swap(self) -> None:
        original, moved = self.base_dir / "b", self.base_dir / "b-moved"
        if moved.exists() and original.is_symlink():
            original.unlink()
            moved.rename(original)

    def tab_changed_text(self, board) -> str:
        return srv.i18n.t(board.lang(), "srv.http.tabChanged")

    def assert_tab_changed(self, code, body) -> None:
        self.assertEqual(code, 409, body)
        self.assertTrue(body.get("code") == "tabChanged" or body.get("error") == self.tab_changed_text(self.board_b), body)

    # --- http
    def http(self, method: str, path: str, data=None, headers=None, raw: bytes | None = None) -> tuple[int, bytes]:
        body = raw if raw is not None else (json.dumps(data).encode("utf-8") if data is not None else None)
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def get_json(self, path: str) -> tuple[int, object]:
        code, raw = self.http("GET", path)
        try:
            return code, json.loads(raw.decode("utf-8"))
        except ValueError:
            return code, raw.decode("utf-8", "replace")

    def post(self, data, token, route: str = "/api/approve", headers=None) -> tuple[int, bytes]:
        merged = {**JSON_HEADERS, "Origin": self.base, **(headers or {})}
        if token is not None:
            merged["X-Approve-Token"] = token
        return self.http("POST", route, data, merged)

    def post_json(self, data, token, **kwargs) -> tuple[int, object]:
        code, raw = self.post(data, token, **kwargs)
        try:
            return code, json.loads(raw.decode("utf-8"))
        except ValueError:
            return code, raw.decode("utf-8", "replace")

    def offered_sha(self, tab_id: str) -> str:
        code, status = self.get_json(f"/api/status?tab={tab_id}")
        self.assertEqual(code, 200, status)
        offer = next((a for a in status["approvals"]["actions"] if a["kind"] == "cp"), None)
        self.assertIsNotNone(offer, status["approvals"])
        self.assertEqual(offer["state"], "waiting", offer)
        return offer["sha"]

    def cp_body(self, sha: str, tab=None) -> dict:
        body = {"kind": "cp", "path": RUN_ID, "sha": sha, "plan": PLAN}
        if tab is not None:
            body["tab"] = tab
        return body


class TabsCase(Tabs, unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="tabs-approvals-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        install_fake_cli(self)
        self.logged: list = []
        patcher = mock.patch.object(srv, "log", lambda text, *a, **k: self.logged.append(str(text)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.build_tabs(self.tmp)
        self.start_server()
        self.sha = {self.id_a: self.offered_sha(self.id_a)}
        if self.id_b:
            self.sha[self.id_b] = self.offered_sha(self.id_b)

    def assert_server_alive_and_quiet(self) -> None:
        code, tabs = self.get_json("/api/tabs")
        self.assertEqual(code, 200, tabs)
        self.assertEqual([line for line in self.logged if line.startswith("błąd")], [], "an unhandled error was logged by the server")

    def assert_nothing_written(self) -> None:
        self.assertEqual((approvals_rows(self.root_a), approvals_rows(self.root_b)), ([], []))


# --------------------------------------------------------------------------- K3: the matrix tab x token x sha

class IsolationMatrix(TabsCase):
    """Two directories, tokens A and B, `tab` of the body A / B / absent, sha of A and of B: every foreign combination is refused and writes in NEITHER
    directory; a matching one writes only in its own."""

    def expected_code(self, token_tab: str, body_tab, sha_tab: str) -> int:
        if body_tab is not None and body_tab != token_tab:
            return 400
        return 200 if self.sha[sha_tab] == self.sha[token_tab] else 409

    def test_the_two_directories_really_have_the_heads_this_variant_says(self) -> None:
        if self.SAME_HEAD:
            self.assertEqual(self.sha[self.id_a], self.sha[self.id_b])
        else:
            self.assertNotEqual(self.sha[self.id_a], self.sha[self.id_b])

    def test_every_foreign_combination_is_refused_and_writes_nothing(self) -> None:
        tabs = (self.id_a, self.id_b)
        for token_tab, body_tab, sha_tab in itertools.product(tabs, (*tabs, None), tabs):
            expected = self.expected_code(token_tab, body_tab, sha_tab)
            if expected == 200:
                continue
            with self.subTest(token=token_tab, tab=body_tab, sha=sha_tab):
                code, body = self.post_json(self.cp_body(self.sha[sha_tab], body_tab), self.reg.token_for(token_tab))
                self.assertEqual(code, expected, body)
                self.assert_nothing_written()
        self.assert_server_alive_and_quiet()

    def test_a_matching_combination_writes_only_in_its_own_directory(self) -> None:
        code, body = self.post_json(self.cp_body(self.sha[self.id_a], self.id_a), self.reg.token_for(self.id_a))
        self.assertEqual(code, 200, body)
        self.assertEqual((len(approvals_rows(self.root_a)), approvals_rows(self.root_b)), (1, []))
        self.assertEqual(approvals_rows(self.root_a)[0]["sha"], self.sha[self.id_a])
        code, body = self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_b))   # no `tab` in the body: the token decides
        self.assertEqual(code, 200, body)
        self.assertEqual((len(approvals_rows(self.root_a)), len(approvals_rows(self.root_b))), (1, 1))
        self.assertEqual(approvals_rows(self.root_b)[0]["sha"], self.sha[self.id_b])
        for root in (self.root_a, self.root_b):   # the audit field (Q8) is optional, but when it is there it names the directory of the tab
            row = approvals_rows(root)[0]
            if "worktree" in row:
                self.assertEqual(row["worktree"], Path(root).name)

    def test_a_token_of_one_tab_cannot_write_into_the_other_even_with_the_sha_of_the_other(self) -> None:
        code, body = self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_a))
        self.assertEqual(code, 200 if self.SAME_HEAD else 409, body)   # identical HEAD: the sha is the same, so only the directory of the TOKEN is written
        self.assertEqual(approvals_rows(self.root_b), [])
        self.assertEqual(len(approvals_rows(self.root_a)), 1 if self.SAME_HEAD else 0)


class IsolationMatrixWithIdenticalHead(IsolationMatrix):
    """Both directories offer the very same sha: only the token and the board of the tab separate them."""
    SAME_HEAD = True

    def test_with_the_same_sha_the_tab_of_the_body_and_the_token_still_decide(self) -> None:
        sha = self.sha[self.id_a]
        for token_tab, other in ((self.id_a, self.id_b), (self.id_b, self.id_a)):
            with self.subTest(token=token_tab):
                code, body = self.post_json(self.cp_body(sha, other), self.reg.token_for(token_tab))
                self.assertEqual(code, 400, body)
                self.assert_nothing_written()
        code, _ = self.post_json(self.cp_body(sha, self.id_b), self.reg.token_for(self.id_b))
        self.assertEqual(code, 200)
        self.assertEqual((approvals_rows(self.root_a), len(approvals_rows(self.root_b))), ([], 1))


# --------------------------------------------------------------------------- K3: `tab` that is not an id

class BadTabValues(TabsCase):
    BAD = ("../", "../main", "..", "../../etc", "a/b", "main/x", "/main", "\\main", "main\\..\\main", "x" * 1001, "x" * 2000, "f" * 13, "g" * 12,
           "zzzzzzzzzzzz", "MAIN", "Main", "ABCDEF012345", " main", "main ", "main\n", "main\x00", "%2e%2e%2f", "", "0123456789a")

    def test_a_bad_tab_in_the_body_is_refused_with_400_and_writes_nothing(self) -> None:
        for bad in self.BAD:
            for route, body in (("/api/approve", self.cp_body(self.sha[self.id_a], bad)),
                                ("/api/feedback-approve", {"draft": "F1", "sha": "a" * 64, "tab": bad})):
                with self.subTest(tab=bad[:20], length=len(bad), route=route):
                    code, answer = self.post_json(body, self.reg.token_for(self.id_a), route=route)
                    self.assertEqual(code, 400, answer)
                    self.assert_nothing_written()
        self.assert_server_alive_and_quiet()

    def test_a_body_over_the_limit_is_a_400_bad_length_and_writes_nothing(self) -> None:
        body = {**self.cp_body(self.sha[self.id_a]), "tab": "x" * (srv.MAX_BODY * 2)}
        code, answer = self.post_json(body, self.reg.token_for(self.id_a))
        self.assertEqual((code, answer), (400, {"error": srv.i18n.t("pl", "srv.http.badLength")}))
        self.assert_nothing_written()

    def test_a_null_tab_in_the_body_is_not_the_same_as_no_tab_and_is_a_400(self) -> None:
        """Documented: a body that names the key `tab` must name the tab of the token; `null` names nothing, so it is refused (absent = the token decides)."""
        for route, body in (("/api/approve", {**self.cp_body(self.sha[self.id_a]), "tab": None}),
                            ("/api/feedback-approve", {"draft": "F1", "sha": "a" * 64, "tab": None})):
            with self.subTest(route=route):
                code, answer = self.post_json(body, self.reg.token_for(self.id_a), route=route)
                self.assertEqual(code, 400, answer)
        self.assert_nothing_written()

    def test_a_tab_that_is_not_a_string_is_refused_with_400(self) -> None:
        for bad in (123, ["main"], {"id": "main"}, True):
            with self.subTest(tab=bad):
                code, answer = self.post_json({**self.cp_body(self.sha[self.id_a]), "tab": bad}, self.reg.token_for(self.id_a))
                self.assertEqual(code, 400, answer)
                self.assert_nothing_written()
        self.assert_server_alive_and_quiet()

    def test_a_bad_tab_in_a_get_is_a_404_and_never_an_error(self) -> None:
        for bad in [value for value in self.BAD if value and len(value) < 5000] + ["0123456789ab", "ffffffffffff"]:   # a longer request line is the 414 of the stdlib
            quoted = urllib.parse.quote(bad, safe="")
            for path in (f"/api/status?tab={quoted}", f"/api/doc?tab={quoted}&path={SPEC}", f"/api/tests?tab={quoted}", f"/events?tab={quoted}"):
                with self.subTest(path=path[:60]):
                    code, _ = self.http("GET", path)
                    self.assertEqual(code, 404)
        self.assert_server_alive_and_quiet()

    def test_a_known_tab_still_answers_after_all_of_it(self) -> None:
        for tab in (self.id_a, self.id_b):
            code, status = self.get_json(f"/api/status?tab={tab}")
            self.assertEqual(code, 200)
            self.assertIn("plan", status)

    def test_a_bad_id_in_the_token_is_the_ordinary_token_refusal(self) -> None:
        hmac_part = self.reg.token_for(self.id_a).split(".", 1)[1]
        for bad in ("../x", "x" * 5000, "ZZZZ", "main/../main", "", "0123456789a", "MAIN"):
            with self.subTest(id=bad[:20]):
                code, answer = self.post(self.cp_body(self.sha[self.id_a]), f"{bad}.{hmac_part}")
                self.assertEqual((code, json.loads(answer)), (403, REFUSED))
        self.assert_nothing_written()


# --------------------------------------------------------------------------- K3: one answer for every bad pair

class UniformRefusal(TabsCase):
    def build_variants(self) -> dict:
        token_a = self.reg.token_for(self.id_a)
        hmac_a = token_a.split(".", 1)[1]
        excluded_root = self.extra_worktree("excl", excluded=True)
        excluded_id = self.entry_for(excluded_root).id
        self.assertNotIn(excluded_id, self.reg.ids(), "an excluded directory has no tab")
        gone_root = self.extra_worktree("gone")
        gone_id = self.register_root(gone_root)
        gone_token = self.reg.token_for(gone_id)
        self.reg.deregister(gone_id)
        self.clock[0] += TTL + 5      # past the tombstone: this id is "unknown" now
        return {
            "no header": None,
            "empty": "",
            "wrong hmac of a known tab": f"{self.id_a}.{'0' * len(hmac_a)}",
            "truncated": token_a[:-1],
            "an id that exists nowhere": f"{'1' * 12}.{hmac_a}",
            "the hmac of A under the id of B": f"{self.id_b}.{hmac_a}",
            "the id of an excluded directory": f"{excluded_id}.{hmac_a}",
            "a vanished tab without a tombstone": gone_token,
            "a bare token on a multi-tab server": hmac_a,
            "a bare token upper-cased": hmac_a.upper(),
            "a foreign shape": "x",
        }

    def test_every_bad_pair_gets_the_same_status_and_the_same_text_as_a_wrong_token(self) -> None:
        body = self.cp_body(self.sha[self.id_a])
        answers = {name: self.post(body, token) for name, token in self.build_variants().items()}
        for name, (code, raw) in answers.items():
            with self.subTest(case=name):
                self.assertEqual(code, 403)
                self.assertEqual(json.loads(raw), REFUSED)
                self.assertEqual(raw, answers["no header"][1], "the bytes of the refusal differ")
        self.assert_nothing_written()

    def test_the_same_holds_for_the_feedback_route(self) -> None:
        body = {"draft": "F1", "sha": "a" * 64}
        answers = {name: self.post(body, token, route="/api/feedback-approve") for name, token in self.build_variants().items()}
        for name, (code, raw) in answers.items():
            with self.subTest(case=name):
                self.assertEqual((code, json.loads(raw)), (403, REFUSED))
                self.assertEqual(raw, answers["no header"][1])

    def test_the_refusals_before_the_token_do_not_depend_on_the_pair(self) -> None:
        body = self.cp_body(self.sha[self.id_a])
        for token in (None, self.reg.token_for(self.id_a), f"{'1' * 12}.x"):
            with self.subTest(token=token and token[:6]):
                code, raw = self.post(body, token, headers={"X-Status-Board": "0"})
                self.assertEqual((code, json.loads(raw)), (403, {"error": "Brak nagłówka X-Status-Board."}))
                code, raw = self.post(body, token, headers={"Origin": "https://evil.example"})
                self.assertEqual((code, json.loads(raw)), (403, {"error": "Obcy Origin."}))

    def test_an_unknown_or_malformed_id_costs_a_compare_like_a_wrong_hmac(self) -> None:
        """"Atrapa compare_digest": the refusal of an id that is not in the registry runs through the same comparison as the refusal of a wrong hmac."""
        hmac_a = self.reg.token_for(self.id_a).split(".", 1)[1]

        def compares(token: str) -> int:
            with mock.patch.object(srv.hmac, "compare_digest", wraps=srv.hmac.compare_digest) as spy:
                self.assertEqual(self.reg.resolve_token(token)[0], "deny")
            return spy.call_count

        baseline = compares(f"{self.id_a}.{'0' * len(hmac_a)}")
        self.assertGreaterEqual(baseline, 1)
        for token in (f"{'1' * 12}.{hmac_a}", f"../x.{hmac_a}", f"{'x' * 3000}.{hmac_a}", f"MAIN.{hmac_a}"):
            with self.subTest(token=token[:20]):
                self.assertEqual(compares(token), baseline)


# --------------------------------------------------------------------------- tombstones (unit level, steered clock)

class Tombstones(TabsCase):
    def second_tab(self, name: str = "t2") -> tuple:
        root = self.extra_worktree(name)
        tab = self.register_root(root)
        return root, tab, self.reg.token_for(tab)

    def test_a_vanished_tab_is_gone_for_ten_minutes_with_the_right_hmac(self) -> None:
        _, tab, token = self.second_tab()
        self.assertEqual(self.reg.resolve_token(token), ("ok", tab))
        self.reg.deregister(tab)
        self.assertNotIn(tab, self.reg.ids())
        self.assertEqual(self.reg.resolve_token(token), ("gone", tab))
        self.clock[0] += TTL - 1
        self.assertEqual(self.reg.resolve_token(token), ("gone", tab))
        self.clock[0] += 2
        self.assertEqual(self.reg.resolve_token(token), ("deny", None))
        self.assertEqual(self.reg.resolve_token(token), ("deny", None), "the answer does not come back")

    def test_a_wrong_hmac_of_a_tombstoned_id_is_a_plain_deny_and_a_right_one_is_gone(self) -> None:
        _, tab, token = self.second_tab()
        self.reg.deregister(tab)
        hmac_part = token.split(".", 1)[1]
        flipped = ("0" if hmac_part[0] != "0" else "1") + hmac_part[1:]
        for bad in (f"{tab}.{flipped}", f"{tab}.{hmac_part[:-1]}", f"{tab}.", f"{tab}.{hmac_part}0", hmac_part):
            with self.subTest(token=bad[:20]):
                self.assertEqual(self.reg.resolve_token(bad), ("deny", None))
        self.assertEqual(self.reg.resolve_token(token), ("gone", tab))

    def test_over_http_the_gone_tab_is_a_410_and_a_wrong_hmac_is_the_usual_403(self) -> None:
        root, tab, token = self.second_tab()
        sha = self.offered_sha(tab)
        self.reg.deregister(tab)
        code, body = self.post_json(self.cp_body(sha), token)
        self.assertEqual(code, 410, body)
        self.assertEqual(approvals_rows(root), [])
        wrong = f"{tab}.{token.split('.', 1)[1][::-1]}"
        code, raw = self.post(self.cp_body(sha), wrong)
        self.assertEqual((code, json.loads(raw)), (403, REFUSED))
        self.clock[0] += TTL + 1
        self.assertEqual(self.post_json(self.cp_body(sha), token), (403, REFUSED))
        self.assertEqual(approvals_rows(root), [])

    def test_the_feedback_route_answers_gone_the_same_way(self) -> None:
        _, tab, token = self.second_tab()
        self.reg.deregister(tab)
        code, _ = self.post_json({"draft": "F1", "sha": "a" * 64}, token, route="/api/feedback-approve")
        self.assertEqual(code, 410)
        self.clock[0] += TTL + 1
        self.assertEqual(self.post_json({"draft": "F1", "sha": "a" * 64}, token, route="/api/feedback-approve"), (403, REFUSED))

    def test_a_get_of_a_vanished_tab_is_a_404(self) -> None:
        _, tab, _ = self.second_tab()
        self.assertEqual(self.get_json(f"/api/status?tab={tab}")[0], 200)
        self.reg.deregister(tab)
        for path in (f"/api/status?tab={tab}", f"/api/doc?tab={tab}&path={SPEC}", f"/api/tests?tab={tab}", f"/events?tab={tab}"):
            self.assertEqual(self.http("GET", path)[0], 404, path)

    def test_at_most_sixty_four_tombstones_survive_and_the_oldest_falls_out_first(self) -> None:
        tokens = []
        for number in range(66):
            root = self.base_dir / f"bulk{number}"
            root.mkdir()
            tab = f"{number + 1:012x}"
            entry = dw.Entry(tab, dw.normalize_path(str(root)), root.name, "worktree", False, False, None, None, None)
            self.reg.register(entry, board=self.new_board(root))
            tokens.append((tab, self.reg.token_for(tab)))
        for tab, _ in tokens:
            self.reg.deregister(tab)
            self.clock[0] += 1.0
        answers = [self.reg.resolve_token(token) for _, token in tokens]
        gone = [item for item in answers if item[0] == "gone"]
        self.assertEqual(len(gone), 64, answers)
        self.assertEqual(answers[0], ("deny", None))
        self.assertEqual(answers[1], ("deny", None))
        self.assertEqual(answers[-1], ("gone", tokens[-1][0]))
        self.assertEqual(answers[2], ("gone", tokens[2][0]))

    def test_registering_the_same_path_again_clears_the_tombstone(self) -> None:
        root, tab, token = self.second_tab()
        entry = self.entry_for(root)
        self.reg.deregister(tab)
        self.assertEqual(self.reg.resolve_token(token)[0], "gone")
        self.reg.register(entry, board=self.new_board(root))
        self.assertIn(tab, self.reg.ids())
        self.assertEqual(self.reg.resolve_token(token), ("ok", tab))
        self.reg.deregister(tab)                # a new tombstone is a new ten minutes
        self.clock[0] += TTL - 1
        self.assertEqual(self.reg.resolve_token(token)[0], "gone")

    def test_deregister_stops_the_board_and_forgets_the_tab(self) -> None:
        root, tab, _ = self.second_tab()
        board = self.reg.board(tab)
        self.assertFalse(board.stop.is_set())
        self.reg.deregister(tab)
        self.assertTrue(board.stop.is_set())
        self.assertNotIn(tab, self.reg.ids())

    def test_garbage_in_the_token_never_raises(self) -> None:
        for value in (None, "", ".", "..", "a.b.c", "x" * 20_000, "\x00", "ö.ö", "main.", ".abc", "main.\x00", " ", "main .abc"):
            with self.subTest(value=repr(value)[:30]):
                self.assertEqual(self.reg.resolve_token(value)[0], "deny")


# --------------------------------------------------------------------------- bare token

class BareTokenOnOneTab(TabsCase):
    SINGLE = True

    def test_a_server_with_one_tab_takes_both_shapes(self) -> None:
        token = self.reg.token_for(self.id_a)
        bare = token.split(".", 1)[1]
        self.assertEqual(self.reg.resolve_token(token), ("ok", self.id_a))
        self.assertEqual(self.reg.resolve_token(bare), ("ok", self.id_a))
        self.assertEqual(self.reg.resolve_token(bare[:-1] + ("0" if bare[-1] != "0" else "1"))[0], "deny")

    def test_over_http_the_bare_token_writes_into_the_only_tab(self) -> None:
        bare = self.reg.token_for(self.id_a).split(".", 1)[1]
        code, body = self.post_json(self.cp_body(self.sha[self.id_a]), bare)
        self.assertEqual(code, 200, body)
        self.assertEqual(len(approvals_rows(self.root_a)), 1)


class BareTokenOnTwoTabs(TabsCase):
    def test_a_server_with_two_tabs_does_not_take_a_bare_token(self) -> None:
        for tab in (self.id_a, self.id_b):
            bare = self.reg.token_for(tab).split(".", 1)[1]
            self.assertEqual(self.reg.resolve_token(bare), ("deny", None))
            code, raw = self.post(self.cp_body(self.sha[tab]), bare)
            self.assertEqual((code, json.loads(raw)), (403, REFUSED))
        self.assert_nothing_written()

    def test_the_tab_that_is_left_after_a_deregister_takes_the_bare_token_again(self) -> None:
        bare = self.reg.token_for(self.id_a).split(".", 1)[1]
        self.assertEqual(self.reg.resolve_token(bare), ("deny", None))
        self.reg.deregister(self.id_b)
        self.assertEqual(list(self.reg.ids()), [self.id_a])
        self.assertEqual(self.reg.resolve_token(bare), ("ok", self.id_a))


# --------------------------------------------------------------------------- 409 tabChanged

class TabChangedAfterRegistration(TabsCase):
    def test_a_matching_post_after_the_path_was_replaced_is_a_409_and_writes_nothing(self) -> None:
        moved = self.swap_ancestor_for_a_link()
        code, body = self.post_json(self.cp_body(self.sha[self.id_b], self.id_b), self.reg.token_for(self.id_b))
        self.assert_tab_changed(code, body)   # the 409 of the tab, not the 409 of a sha
        self.assertEqual(approvals_rows(self.root_a), [])
        self.assertEqual(list(moved.rglob("approvals.jsonl")), [])
        self.assertFalse(self.reg.revalidate_for_write(self.id_b))
        self.assertTrue(self.reg.revalidate_for_write(self.id_a))

    def test_the_same_post_succeeds_when_the_path_is_back(self) -> None:
        self.swap_ancestor_for_a_link()
        self.assertEqual(self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_b))[0], 409)
        self.undo_swap()
        self.assertTrue(self.reg.revalidate_for_write(self.id_b))
        code, body = self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_b))
        self.assertEqual(code, 200, body)
        self.assertEqual(len(approvals_rows(self.root_b)), 1)

    def test_residual_toctou_the_write_is_given_the_validated_root_of_the_tab(self) -> None:
        """RESIDUAL RISK (spec, "Re-walidacja przy zapisie"): the check and the write are two steps (`_approve_lock` is per process, `record_approval` resolves
        the path itself), so a path replaced AFTER the guard is not detected: the protection is best effort. What can be shown without touching production is
        that the writer is handed the root that was validated (the registered directory of the tab), never a path taken from the request."""
        try:
            probe = self.base_dir / "probe-link"
            os.symlink(str(self.base_dir), str(probe), target_is_directory=True)
            probe.unlink()
        except (OSError, NotImplementedError):
            self.skipTest("this system refuses to create symlinks for the user")
        registered = dw.normalize_key(str(self.root_b))
        real = srv.status_core.record_approval
        seen: list = []

        def replace_then_write(root, *args, **kwargs):
            seen.append(dw.normalize_key(str(root)))
            original, moved = self.base_dir / "b", self.base_dir / "b-moved"
            original.rename(moved)
            os.symlink(str(moved), str(original), target_is_directory=True)
            self.addCleanup(self.undo_swap)
            return real(root, *args, **kwargs)

        with mock.patch.object(srv.status_core, "record_approval", replace_then_write):
            code, body = self.post_json(self.cp_body(self.sha[self.id_b], self.id_b), self.reg.token_for(self.id_b))
        self.assertEqual(seen, [registered], "the writer got another root than the validated one")
        self.assertIn(code, (200, 409, 500), body)   # the outcome of the lost race is not a contract
        self.assertEqual(approvals_rows(self.root_a), [])

    def test_a_removed_back_link_refuses_the_write_as_well(self) -> None:
        (self.root_a / ".git" / "worktrees" / "wtb" / "gitdir").unlink()   # the back-link of R2 is gone; the board still reads its HEAD, so only the guard can refuse
        code, body = self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_b))
        self.assert_tab_changed(code, body)
        self.assertFalse(self.reg.revalidate_for_write(self.id_b))
        self.assertEqual(approvals_rows(self.root_b), [])


# --------------------------------------------------------------------------- a tab that vanishes during the request

class TabVanishesDuringTheRequest(TabsCase):
    def test_deregister_between_the_token_and_the_write_refuses_and_writes_nothing(self) -> None:
        original = self.reg.resolve_token
        fired = []

        def resolve_then_vanish(value):
            result = original(value)
            if result[0] == "ok" and result[1] == self.id_b and not fired:
                fired.append(True)
                self.reg.deregister(self.id_b)
            return result

        with mock.patch.object(self.reg, "resolve_token", resolve_then_vanish):
            code, body = self.post_json(self.cp_body(self.sha[self.id_b], self.id_b), self.reg.token_for(self.id_b))
        self.assertTrue(fired, "the hook did not run: the handler does not go through `registry.resolve_token`")
        self.assertIn(code, (409, 410), body)
        self.assertEqual(approvals_rows(self.root_b), [])
        self.assertEqual(approvals_rows(self.root_a), [])
        self.assert_server_alive_and_quiet()

    def test_deregister_right_before_the_revalidation_refuses_and_writes_nothing(self) -> None:
        original = self.reg.revalidate_for_write
        fired = []

        def vanish_then_revalidate(tab_id):
            if tab_id == self.id_b and not fired:
                fired.append(True)
                self.reg.deregister(self.id_b)
            return original(tab_id)

        with mock.patch.object(self.reg, "revalidate_for_write", vanish_then_revalidate):
            code, body = self.post_json(self.cp_body(self.sha[self.id_b]), self.reg.token_for(self.id_b))
        self.assertTrue(fired, "the hook did not run: the write does not go through `registry.revalidate_for_write`")
        self.assertIn(code, (409, 410), body)
        self.assertEqual(approvals_rows(self.root_b), [])
        self.assertEqual(approvals_rows(self.root_a), [])
        self.assert_server_alive_and_quiet()


# --------------------------------------------------------------------------- parallel consents with one run id

class ParallelConsents(TabsCase):
    def test_two_threads_with_the_same_run_id_write_each_into_its_own_directory(self) -> None:
        outcomes: dict = {}
        errors: list = []
        barrier = threading.Barrier(2)

        def click(tab: str) -> None:
            try:
                barrier.wait(timeout=10)
                outcomes[tab] = self.post_json(self.cp_body(self.sha[tab], tab), self.reg.token_for(tab))
            except Exception as error:   # noqa: BLE001 (the test reports every failure of a thread)
                errors.append(repr(error))

        threads = [threading.Thread(target=click, args=(tab,)) for tab in (self.id_a, self.id_b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual({tab: outcome[0] for tab, outcome in outcomes.items()}, {self.id_a: 200, self.id_b: 200}, outcomes)
        rows_a, rows_b = approvals_rows(self.root_a), approvals_rows(self.root_b)
        self.assertEqual(([row["sha"] for row in rows_a], [row["sha"] for row in rows_b]), ([self.sha[self.id_a]], [self.sha[self.id_b]]))
        self.assertEqual({row["run"] for row in rows_a + rows_b}, {RUN_ID})
        self.assert_server_alive_and_quiet()

    def test_repeated_rounds_never_mix_the_directories(self) -> None:
        for round_number in range(3):
            tips = {self.id_a: f"a{round_number}0000{round_number}beef", self.id_b: f"b{round_number}0000{round_number}beef"}
            write(self.root_a / ".git" / "refs" / "heads" / "feature" / "demo", tips[self.id_a] + "\n")
            write(self.root_a / ".git" / "refs" / "heads" / "feature" / "other", tips[self.id_b] + "\n")
            sha = {tab: self.offered_sha(tab) for tab in tips}
            barrier = threading.Barrier(2)
            outcomes: dict = {}

            def click(tab: str) -> None:
                barrier.wait(timeout=10)
                outcomes[tab] = self.post_json(self.cp_body(sha[tab], tab), self.reg.token_for(tab))[0]

            threads = [threading.Thread(target=click, args=(tab,)) for tab in tips]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            with self.subTest(round=round_number):
                self.assertEqual(outcomes, {self.id_a: 200, self.id_b: 200})
        shas_a = [row["sha"] for row in approvals_rows(self.root_a)]
        shas_b = [row["sha"] for row in approvals_rows(self.root_b)]
        self.assertEqual((len(shas_a), len(shas_b)), (3, 3))
        self.assertEqual(set(shas_a) & set(shas_b), set())


# --------------------------------------------------------------------------- K9: the token never leaves

class TokenNeverLeaks(TabsCase):
    def forbidden(self) -> list:
        values = {SECRET_TEXT}
        for tab in (self.id_a, self.id_b):
            token = self.reg.token_for(tab)
            values.update({token, token.split(".", 1)[1]})
        for board in (self.board_a, self.board_b):
            if board.approve_token:
                values.add(board.approve_token)
        return sorted(value for value in values if len(value) >= 16)

    def first_events(self, path: str) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            text = str(response.headers)
            for _ in range(12):
                line = response.fp.readline()
                text += line.decode("utf-8", "replace")
                if line == b"\n" and "event:" in text:
                    break
            return text
        finally:
            connection.close()

    def test_no_response_log_or_stderr_line_contains_a_token_or_the_secret(self) -> None:
        captured = io.StringIO()
        seen: dict = {}
        with contextlib.redirect_stderr(captured):
            for tab in (self.id_a, self.id_b):
                for path in (f"/api/status?tab={tab}", f"/api/doc?tab={tab}&path={SPEC}", f"/api/tests?tab={tab}"):
                    code, raw = self.http("GET", path)
                    seen[path] = raw.decode("utf-8", "replace")
                seen[f"/events?tab={tab}"] = self.first_events(f"/events?tab={tab}")
            for path in ("/api/tabs", "/healthz", "/", "/index.html", "/assets/app.js", "/assets/dashboard-render.js", "/assets/app.css", "/assets/theme.js",
                         "/api/descriptions", "/api/status", "/events"):
                if path == "/events":
                    seen[path] = self.first_events(path)
                    continue
                code, raw = self.http("GET", path)
                seen[path] = raw.decode("utf-8", "replace")
            for tab in (self.id_a, self.id_b):
                self.post_json(self.cp_body("0" * 14, tab), self.reg.token_for(tab))                      # refusals: a wrong sha
                self.post_json(self.cp_body(self.sha[tab], tab), f"{tab}.wrong")                         # refusals: a wrong token
                self.post_json({"draft": "F1", "sha": "a" * 64, "tab": tab}, self.reg.token_for(tab), route="/api/feedback-approve")
            self.post_json(self.cp_body(self.sha[self.id_a], self.id_a), self.reg.token_for(self.id_a))   # an accepted consent
            seen["answer of a refusal"] = self.post(self.cp_body("0" * 14, self.id_b), self.reg.token_for(self.id_b))[1].decode("utf-8", "replace")
        for path, text in seen.items():
            for value in self.forbidden():
                with self.subTest(response=path, secret=value[:6]):
                    self.assertNotIn(value, text)
        log_text = "\n".join(self.logged) + captured.getvalue()
        self.assertTrue(self.logged or captured.getvalue(), "the scan of the log has nothing to scan: the server logged nothing")
        for value in self.forbidden():
            with self.subTest(log=True, secret=value[:6]):
                self.assertNotIn(value, log_text)

    def test_the_status_of_a_tab_only_says_that_a_token_is_required(self) -> None:
        for tab in (self.id_a, self.id_b):
            code, status = self.get_json(f"/api/status?tab={tab}")
            self.assertEqual(code, 200)
            self.assertIs(status["approvals"]["tokenRequired"], True)
            self.assertNotIn("token", {key.lower() for key in status["approvals"] if key != "tokenRequired"})


# --------------------------------------------------------------------------- /api/doc stays inside the root of its tab

class DocStaysInTheTab(TabsCase):
    MARKER = "marker-only-in-the-second-directory"

    def setUp(self) -> None:
        super().setUp()
        write(self.root_b / "docs" / "specs" / "only-b.md", f"# Spec: Only B\n\nStatus: draft\n\n{self.MARKER}\n")

    def doc(self, tab: str, path: str) -> tuple[int, str]:
        code, raw = self.http("GET", f"/api/doc?tab={tab}&path={urllib.parse.quote(path, safe='/')}")
        return code, raw.decode("utf-8", "replace")

    def test_the_owner_tab_reads_its_file_and_the_other_one_does_not(self) -> None:
        code, text = self.doc(self.id_b, "docs/specs/only-b.md")
        self.assertEqual(code, 200)
        self.assertIn(self.MARKER, text)
        code, text = self.doc(self.id_a, "docs/specs/only-b.md")
        self.assertEqual(code, 404)
        self.assertNotIn(self.MARKER, text)

    def test_dot_dot_does_not_reach_the_other_directory(self) -> None:
        relative_b = os.path.relpath(self.root_b, self.root_a).replace(os.sep, "/")
        for path in (f"{relative_b}/docs/specs/only-b.md", f"docs/../{relative_b}/docs/specs/only-b.md", f"../{self.root_b.name}/docs/specs/only-b.md",
                     "../../b/proj/docs/specs/only-b.md", str(self.root_b / "docs" / "specs" / "only-b.md")):
            with self.subTest(path=path):
                code, text = self.doc(self.id_a, path)
                self.assertEqual(code, 404)
                self.assertNotIn(self.MARKER, text)

    def link(self, target: Path, link: Path) -> None:
        try:
            os.symlink(str(target), str(link), target_is_directory=target.is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("this system refuses to create symlinks for the user")

    def test_a_symlink_to_the_other_directory_is_not_followed(self) -> None:
        self.link(self.root_b / "docs" / "specs" / "only-b.md", self.root_a / "docs" / "specs" / "borrowed.md")
        self.link(self.root_b / "docs" / "specs", self.root_a / "docs" / "linked-specs")
        for path in ("docs/specs/borrowed.md", "docs/linked-specs/only-b.md"):
            with self.subTest(path=path):
                code, text = self.doc(self.id_a, path)
                self.assertEqual(code, 404)
                self.assertNotIn(self.MARKER, text)


# --------------------------------------------------------------------------- /api/tabs

class TabsEndpoint(TabsCase):
    def test_every_tab_is_listed_with_id_name_kind_and_the_current_one_marked(self) -> None:
        code, payload = self.get_json("/api/tabs")
        self.assertEqual(code, 200, payload)
        self.assertEqual(set(payload) & {"tabs", "current"}, {"tabs", "current"})
        tabs = {item["id"]: item for item in payload["tabs"]}
        self.assertEqual(set(tabs), {self.id_a, self.id_b})
        for tab, root, kind in ((self.id_a, self.root_a, "main"), (self.id_b, self.root_b, "worktree")):
            item = tabs[tab]
            for key in ("id", "name", "kind", "current"):
                self.assertIn(key, item)
            self.assertEqual((item["kind"], item["name"]), (kind, root.name))
        self.assertEqual([item["id"] for item in payload["tabs"] if item["current"]], [payload["current"]])
        self.assertEqual(payload["current"], self.id_a, "the server started in directory A")

    def test_the_list_carries_no_token_and_no_secret(self) -> None:
        code, raw = self.http("GET", "/api/tabs")
        text = raw.decode("utf-8")
        for tab in (self.id_a, self.id_b):
            token = self.reg.token_for(tab)
            self.assertNotIn(token, text)
            self.assertNotIn(token.split(".", 1)[1], text)
        self.assertNotIn("token", text.lower().replace("tokenrequired", ""))

    def test_summary_has_no_tokens(self) -> None:
        text = json.dumps(self.reg.summary(), default=str)
        for tab in (self.id_a, self.id_b):
            self.assertNotIn(self.reg.token_for(tab).split(".", 1)[1], text)

    def test_a_tab_registered_later_shows_up_and_a_vanished_one_leaves(self) -> None:
        root = self.extra_worktree("late")
        tab = self.register_root(root)
        self.assertIn(tab, [item["id"] for item in self.get_json("/api/tabs")[1]["tabs"]])
        self.reg.deregister(tab)
        self.assertNotIn(tab, [item["id"] for item in self.get_json("/api/tabs")[1]["tabs"]])

    def test_the_default_tab_answers_without_a_tab_parameter(self) -> None:
        code, status = self.get_json("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(status["project"]["path"].replace("\\", "/").rstrip("/"), dw.normalize_path(str(self.root_a)).rstrip("/"))


# --------------------------------------------------------------------------- GO plan / GO implement (`Board.approve` -> `set_doc_status`)

SPEC2 = "docs/specs/demo-etap-2.md"
PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"
RUN2 = "2026-01-20-demo-etap-2"


def go_files(root: Path, kind: str, differ: bool) -> None:
    """A spec waiting for GO plan (`kind: spec`) or a plan waiting for GO implement over an approved spec (`kind: plan`); `differ` makes the blob sha unique."""
    root = Path(root)
    (root / ".skillsync" / "runs" / RUN2).mkdir(parents=True, exist_ok=True)
    extra = "\nOnly in this directory.\n" if differ else ""
    status = "draft (czeka na GO plan)" if kind == "spec" else "approved"
    spec = write(root / SPEC2, f"# Spec: Demo 2\n\nStatus: {status}\nPlan: {PLAN2}\n{extra}")
    later = (root / PLAN).stat().st_mtime + 100
    os.utime(spec, (later, later))
    if kind == "plan":
        plan = write(root / PLAN2, f"# Plan: Demo 2\n\nStatus: draft (czeka na GO implement)\nSpec: {SPEC2}\nPrzebieg: {RUN2}\n{extra}")
        os.utime(plan, (later + 10, later + 10))


def go_trail(root: Path) -> list:
    path = Path(root) / ".skillsync" / "runs" / RUN2 / "approvals.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


class GoCase(TabsCase):
    KIND = "spec"
    IDENTICAL = False

    def setUp(self) -> None:
        super().setUp()
        for root, differ in ((self.root_a, False), (self.root_b, not self.IDENTICAL)):
            go_files(root, self.KIND, differ)
        self.go_path = SPEC2 if self.KIND == "spec" else PLAN2
        self.go_sha = {tab: self.go_offer(tab) for tab in (self.id_a, self.id_b)}

    def go_offer(self, tab: str) -> str:
        code, status = self.get_json(f"/api/status?tab={tab}" + (f"&plan={PLAN2}" if self.KIND == "plan" else ""))
        self.assertEqual(code, 200, status)
        offer = next((a for a in status["approvals"]["actions"] if a["kind"] == self.KIND), None)
        self.assertIsNotNone(offer, status["approvals"])
        self.assertEqual((offer["state"], offer["path"]), ("waiting", self.go_path), offer)
        return offer["sha"]

    def go_body(self, sha: str, tab=None) -> dict:
        body = {"kind": self.KIND, "path": self.go_path, "sha": sha}
        if self.KIND == "plan":
            body["plan"] = PLAN2
        if tab is not None:
            body["tab"] = tab
        return body

    def status_line(self, root: Path) -> str:
        text = (Path(root) / self.go_path).read_text(encoding="utf-8")
        return next(line for line in text.splitlines() if line.startswith("Status:"))


class GoIsolationTests:
    """The same matrix as for CP, for the writers that change a file: only the directory of the TOKEN gets its Status line changed and its trail row."""

    def test_the_two_directories_have_the_blob_sha_this_variant_says(self) -> None:
        self.assertEqual(self.go_sha[self.id_a] == self.go_sha[self.id_b], self.IDENTICAL)

    def test_every_foreign_combination_is_refused_and_nothing_changes_in_either_directory(self) -> None:
        before = tree_snapshot(self.root_a, self.root_b)
        tabs = (self.id_a, self.id_b)
        for token_tab, body_tab, sha_tab in itertools.product(tabs, (*tabs, None), tabs):
            if body_tab is not None and body_tab != token_tab:
                expected = 400
            elif self.go_sha[sha_tab] != self.go_sha[token_tab]:
                expected = 409
            else:
                continue
            with self.subTest(token=token_tab, tab=body_tab, sha=sha_tab):
                code, body = self.post_json(self.go_body(self.go_sha[sha_tab], body_tab), self.reg.token_for(token_tab))
                self.assertEqual(code, expected, body)
                self.assertEqual(tree_snapshot(self.root_a, self.root_b), before)
                self.assertEqual((go_trail(self.root_a), go_trail(self.root_b)), ([], []))
        self.assert_server_alive_and_quiet()

    def test_a_matching_pair_changes_the_status_line_only_in_its_own_directory(self) -> None:
        before_b = (self.root_b / self.go_path).read_bytes()
        code, body = self.post_json(self.go_body(self.go_sha[self.id_a], self.id_a), self.reg.token_for(self.id_a))
        self.assertEqual(code, 200, body)
        self.assertIn("approved", self.status_line(self.root_a))
        self.assertEqual((self.root_b / self.go_path).read_bytes(), before_b, "the file of the other directory changed")
        self.assertEqual((len(go_trail(self.root_a)), go_trail(self.root_b)), (1, []))
        before_a = (self.root_a / self.go_path).read_bytes()
        code, body = self.post_json(self.go_body(self.go_sha[self.id_b]), self.reg.token_for(self.id_b))   # no `tab`: the token decides
        self.assertEqual(code, 200, body)
        self.assertIn("approved", self.status_line(self.root_b))
        self.assertEqual((self.root_a / self.go_path).read_bytes(), before_a)
        self.assertEqual((len(go_trail(self.root_a)), len(go_trail(self.root_b))), (1, 1))

    def test_the_token_of_a_decides_even_when_the_body_carries_the_sha_of_b(self) -> None:
        before_b = (self.root_b / self.go_path).read_bytes()
        code, body = self.post_json(self.go_body(self.go_sha[self.id_b]), self.reg.token_for(self.id_a))
        self.assertEqual(code, 200 if self.IDENTICAL else 409, body)
        self.assertEqual((self.root_b / self.go_path).read_bytes(), before_b)
        self.assertEqual(go_trail(self.root_b), [])


class GoSpecWithDifferentSha(GoIsolationTests, GoCase):
    KIND, IDENTICAL = "spec", False


class GoSpecWithIdenticalSha(GoIsolationTests, GoCase):
    KIND, IDENTICAL, SAME_HEAD = "spec", True, True


class GoPlanWithDifferentSha(GoIsolationTests, GoCase):
    KIND, IDENTICAL = "plan", False


class GoPlanWithIdenticalSha(GoIsolationTests, GoCase):
    KIND, IDENTICAL, SAME_HEAD = "plan", True, True


class GoAfterThePathWasReplaced:
    def test_a_matching_go_after_the_path_was_replaced_is_a_409_tab_changed_and_the_file_is_untouched(self) -> None:
        before = (self.root_b / self.go_path).read_bytes()
        moved = self.swap_ancestor_for_a_link()
        code, body = self.post_json(self.go_body(self.go_sha[self.id_b], self.id_b), self.reg.token_for(self.id_b))
        self.assert_tab_changed(code, body)
        self.assertEqual((moved / "proj" / self.go_path).read_bytes(), before)
        self.assertEqual(list(moved.rglob("approvals.jsonl")), [])
        self.assertEqual(go_trail(self.root_a), [])


class GoSpecAfterThePathWasReplaced(GoAfterThePathWasReplaced, GoCase):
    KIND = "spec"


class GoPlanAfterThePathWasReplaced(GoAfterThePathWasReplaced, GoCase):
    KIND = "plan"


# --------------------------------------------------------------------------- the registry without a project, `tombstone=False`, `register` guards

class RegistryContracts(TabsCase):
    def test_a_registry_of_two_tabs_without_a_project_refuses_every_write(self) -> None:
        """Fail closed: without a project nothing can re-validate a tab, so with more than one tab no write is allowed (one tab keeps working)."""
        board_a, board_b = self.new_board(self.root_a), self.new_board(self.root_b)
        reg = make_registry(board_a, project=None, now=lambda: self.clock[0])
        self.assertTrue(reg.revalidate_for_write(reg.ids()[0]), "a single tab without a project stays writable")
        reg.register(self.entry_for(self.root_b), board=board_b)
        self.assertEqual(len(reg.ids()), 2)
        for tab in reg.ids():
            self.assertFalse(reg.revalidate_for_write(tab))
        self.reg = reg
        self.start_server()
        tab_b = self.entry_for(self.root_b).id
        code, body = self.post_json(self.cp_body(self.sha[tab_b], tab_b), reg.token_for(tab_b))
        self.assert_tab_changed(code, body)
        self.assert_nothing_written()

    def test_a_tab_removed_without_a_tombstone_is_an_unknown_one(self) -> None:
        root = self.extra_worktree("quiet")
        tab = self.register_root(root)
        token = self.reg.token_for(tab)
        self.reg.deregister(tab, tombstone=False)
        self.assertNotIn(tab, self.reg.ids())
        self.assertEqual(self.reg.resolve_token(token), ("deny", None))
        code, raw = self.post(self.cp_body("0" * 14), token)
        self.assertEqual((code, json.loads(raw)), (403, REFUSED))
        self.assertEqual(approvals_rows(root), [])

    def test_a_board_of_another_directory_than_the_entry_is_refused(self) -> None:
        root = self.extra_worktree("mixed")
        entry = self.entry_for(root)
        with self.assertRaises(ValueError):
            self.reg.register(entry, board=self.new_board(self.root_a))
        self.assertNotIn(entry.id, self.reg.ids())
        self.reg.register(entry, board=self.new_board(root))      # the matching board is fine
        self.assertIn(entry.id, self.reg.ids())


# --------------------------------------------------------------------------- the strict consent socket

class StrictApproveSocket(TabsCase):
    """Two-port mode: the consent socket judges the Origin exactly and counts every refused token (`token_failures`)."""

    def setUp(self) -> None:
        super().setUp()
        self.read_port, self.approve_port = two_ports()
        for role, port, peer in ((ports.ROLE_READ, self.read_port, self.approve_port), (ports.ROLE_APPROVE, self.approve_port, self.read_port)):
            server = srv.make_server(self.reg, "127.0.0.1", port, role=role, peer_port=peer)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
            self.addCleanup(self.stop_server, server)
        self.failures: list = []
        for board in (self.board_a, self.board_b):
            real = board.token_failures.add
            board.token_failures.add = lambda peer, real=real: (self.failures.append(peer), real(peer))[1]

    def strict_post(self, data, token) -> tuple[int, bytes]:
        headers = {**JSON_HEADERS, "Origin": f"http://127.0.0.1:{self.approve_port}"}
        if token is not None:
            headers["X-Approve-Token"] = token
        connection = http.client.HTTPConnection("127.0.0.1", self.approve_port, timeout=10)
        try:
            connection.request("POST", "/api/approve", json.dumps(data).encode("utf-8"), headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_unknown_id_and_wrong_hmac_are_the_same_refusal_and_count_the_same(self) -> None:
        hmac_a = self.reg.token_for(self.id_a).split(".", 1)[1]
        variants = {"wrong hmac": f"{self.id_a}.{'0' * len(hmac_a)}", "unknown id": f"{'1' * 12}.{hmac_a}", "malformed id": f"../x.{hmac_a}",
                    "bare token": hmac_a, "no token": None}
        answers = {}
        for name, token in variants.items():
            before = len(self.failures)
            answers[name] = self.strict_post(self.cp_body(self.sha[self.id_a]), token)
            with self.subTest(case=name):
                self.assertEqual(len(self.failures) - before, 1, "every refused token is counted once")
                self.assertEqual((answers[name][0], json.loads(answers[name][1])), (403, REFUSED))
                self.assertEqual(answers[name][1], answers["wrong hmac"][1])
        self.assert_nothing_written()

    def test_a_right_token_is_not_counted_and_writes_only_its_own_tab(self) -> None:
        code, raw = self.strict_post(self.cp_body(self.sha[self.id_b], self.id_b), self.reg.token_for(self.id_b))
        self.assertEqual(code, 200, raw)
        self.assertEqual(self.failures, [])
        self.assertEqual((approvals_rows(self.root_a), len(approvals_rows(self.root_b))), ([], 1))

    def test_a_foreign_origin_is_refused_before_the_token_and_not_counted(self) -> None:
        headers = {**JSON_HEADERS, "Origin": "https://evil.example", "X-Approve-Token": "x"}
        connection = http.client.HTTPConnection("127.0.0.1", self.approve_port, timeout=10)
        try:
            connection.request("POST", "/api/approve", json.dumps(self.cp_body(self.sha[self.id_a])).encode("utf-8"), headers)
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()
        self.assertEqual(self.failures, [])


# --------------------------------------------------------------------------- Q11: the feedback consent is global, the tab only a label

class FeedbackConsentFromTab(Tabs, CommandCase):
    """The store of feedback lives in the MAIN directory (`feedback.store_root`): a consent given on tab B is a row there with `tab` = B and touches
    neither `runs` nor `docs` of any tab."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(feedback.CARD_ENV, None)
        self.logged: list = []
        log_patch = mock.patch.object(srv, "log", lambda text, *a, **k: self.logged.append(str(text)))
        log_patch.start()
        self.addCleanup(log_patch.stop)
        def main_worktree_from_files(path):
            """What `git rev-parse` answers for a linked worktree, read from the files of the layout (the test runs no `git`)."""
            project = dw.project_identity(str(path))
            return Path(project.main_path) if project and project.main_path and dw.normalize_key(project.main_path) != dw.normalize_key(str(path)) else None

        patch_main = mock.patch.object(feedback.layers, "main_worktree", main_worktree_from_files)
        patch_main.start()
        self.addCleanup(patch_main.stop)
        self.draft_id, self.preview = self.previewed()        # the draft and its preview are made in the main directory (its store is the project's)
        self.root_a = self.root
        self.root_b = self.base / "wtb"
        write(self.root_a / ".git" / "HEAD", "ref: refs/heads/main\n")
        write(self.root_a / "docs" / "plans" / "a.md", "# Plan A\n")
        write(self.root_b / "docs" / "plans" / "b.md", "# Plan B\n")
        shutil.copy(self.root_a / ".github" / ".skillsync.yaml", self.root_b / ".github" / ".skillsync.yaml") if (self.root_b / ".github").exists() else \
            write(self.root_b / ".github" / ".skillsync.yaml", (self.root_a / ".github" / ".skillsync.yaml").read_text(encoding="utf-8"))   # a tracked file: a real worktree has it
        write(self.root_b / ".skillsync" / "runs" / "r1" / "checkpoints.md", "- 10:00 zrobione: S1\n")
        link_worktree(self.root_a, self.root_b, "wtb", "feature/b", HEAD_B)
        self.base_dir = self.base
        self.project = dw.project_identity(str(self.root_a))
        self.assertIsNotNone(self.project)
        self.clock = [CLOCK0]
        self.board_a = self.new_board(self.root_a)
        self.board_b = self.new_board(self.root_b)
        self.reg = make_registry(self.board_a, project=self.project, now=lambda: self.clock[0])
        self.boards = {os.path.realpath(self.root_a): self.board_a, os.path.realpath(self.root_b): self.board_b}
        self.id_a = self.register_root(self.root_a)
        self.id_b = self.register_root(self.root_b)
        self.start_server()

    def full_sha(self, draft_id: str) -> str:
        path = feedback.preview_path(self.root_a / ".skillsync" / "feedback", draft_id)
        return json.loads(Path(path).read_text(encoding="utf-8"))["sha"]

    def feedback_rows(self, root: Path) -> list:
        path = Path(root) / ".skillsync" / "feedback" / "approvals.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []

    def click(self, token_tab: str, body_tab=None) -> tuple[int, object]:
        body = {"draft": self.draft_id, "sha": self.full_sha(self.draft_id)}
        if body_tab is not None:
            body["tab"] = body_tab
        return self.post_json(body, self.reg.token_for(token_tab), route="/api/feedback-approve")

    def test_a_consent_from_tab_b_is_a_row_in_the_main_store_with_the_tab_and_nothing_else_changes(self) -> None:
        before = tree_snapshot(self.root_a, self.root_b)
        code, body = self.click(self.id_b, self.id_b)
        self.assertEqual(code, 200, body)
        rows = self.feedback_rows(self.root_a)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["draft"], rows[0]["sha"], rows[0].get("tab")), (self.draft_id, self.full_sha(self.draft_id), self.id_b))
        self.assertFalse((self.root_b / ".skillsync" / "feedback").exists(), "the worktree got a store of its own")
        self.assertEqual(tree_snapshot(self.root_a, self.root_b), before, "runs/ or docs/ of a tab changed")
        self.assertEqual(list(self.root_a.glob(".skillsync/runs/*/approvals.jsonl")) + list(self.root_b.glob(".skillsync/runs/*/approvals.jsonl")), [])

    def test_the_token_of_the_other_tab_cannot_give_the_consent_for_b(self) -> None:
        before = tree_snapshot(self.root_a, self.root_b)
        code, body = self.click(self.id_a, self.id_b)
        self.assertEqual(code, 400, body)
        self.assertEqual(self.feedback_rows(self.root_a), [])
        self.assertEqual(tree_snapshot(self.root_a, self.root_b), before)

    def test_a_wrong_pair_is_the_usual_403_and_records_nothing(self) -> None:
        code, raw = self.post({"draft": self.draft_id, "sha": self.full_sha(self.draft_id)}, f"{self.id_b}.wrong", route="/api/feedback-approve")
        self.assertEqual((code, json.loads(raw)), (403, REFUSED))
        self.assertEqual(self.feedback_rows(self.root_a), [])

    def test_a_worktree_without_a_manifest_is_refused_or_written_in_the_main_store_never_a_400_or_a_500(self) -> None:
        """The manifest is a tracked file, so a real worktree has it; one that does not has no project to read the layers from. The consent is global for the
        project, so the answer is either a 409 `stale` with nothing written (the refusal of the session) or the row in the store of the main directory."""
        (self.root_b / ".github" / ".skillsync.yaml").unlink()
        code, body = self.click(self.id_b, self.id_b)
        self.assertIn(code, (200, 409), body)
        if code == 409:
            self.assertEqual(body.get("code"), "stale", body)
            self.assertEqual(self.feedback_rows(self.root_a), [])
        else:
            self.assertEqual([row.get("tab") for row in self.feedback_rows(self.root_a)], [self.id_b])
        self.assertFalse((self.root_b / ".skillsync" / "feedback").exists())

    def test_a_consent_from_the_main_tab_records_its_own_label(self) -> None:
        code, body = self.click(self.id_a)
        self.assertEqual(code, 200, body)
        rows = self.feedback_rows(self.root_a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("tab"), self.id_a)


# --------------------------------------------------------------------------- Q8: the audit field of the record

class RecordApprovalWorktreeField(unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="record-worktree-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "proj"
        (self.root / ".skillsync" / "runs" / RUN_ID).mkdir(parents=True)
        self.counter = 0

    def record(self, **kwargs) -> dict:
        self.counter += 1
        return status_core.record_approval(self.root, "CP", RUN_ID, sha=f"abc{self.counter:04d}def", source="dashboard", plan_state="in_progress", **kwargs)

    def rows(self) -> list:
        return approvals_rows(self.root)

    def test_the_field_is_added_when_given(self) -> None:
        result = self.record(worktree="x")
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(self.rows()[-1]["worktree"], "x")
        self.assertEqual(result["entry"]["worktree"], "x")

    def test_without_the_argument_there_is_no_field(self) -> None:
        self.assertEqual(self.record()["status"], "PASS")
        self.assertNotIn("worktree", self.rows()[-1])
        self.assertEqual(self.record(worktree=None)["status"], "PASS")
        self.assertNotIn("worktree", self.rows()[-1])

    def test_a_name_with_control_characters_never_reaches_the_file_as_one(self) -> None:
        for name in ("a\x00b", "a\nb", "a\r\nb\tc", "\x1b[31mred", "tail\x7f", "a b"):
            with self.subTest(name=repr(name)):
                result = self.record(worktree=name)
                self.assertIn(result["status"], ("PASS", "BLOCKED"))
        raw = (self.root / RUN / "approvals.jsonl").read_text(encoding="utf-8") if (self.root / RUN / "approvals.jsonl").exists() else ""
        for line in raw.splitlines():
            row = json.loads(line)                       # every line is still one JSON object
            self.assertIsInstance(row, dict)
            self.assertFalse(FORBIDDEN_CONTROL & set(str(row.get("worktree", ""))), row)
        self.assertEqual(len(raw.splitlines()), len(self.rows()), "a newline of the name split a record")

    def test_a_very_long_name_is_refused_or_cut(self) -> None:
        result = self.record(worktree="w" * 5000)
        self.assertIn(result["status"], ("PASS", "BLOCKED"))
        for row in self.rows():
            self.assertLess(len(str(row.get("worktree", ""))), 5000)

    def test_a_name_that_is_not_a_string_does_not_raise(self) -> None:
        for name in (123, ["a"], {"a": 1}, True):
            with self.subTest(name=repr(name)):
                self.assertIn(self.record(worktree=name)["status"], ("PASS", "BLOCKED"))
        for row in self.rows():
            self.assertIsInstance(row.get("worktree", ""), str)


if __name__ == "__main__":
    unittest.main()
