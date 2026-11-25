"""CORE-1180 M-6: zakladka „Historia” (backend): `GET /api/history` i `POST /api/plan-status` (archiwizacja planow `done`).

Dowodem jest ZAWARTOSC plikow planow i audytu w obu katalogach (nie tylko kod odpowiedzi): zapis dotyczy wylacznie pliku zakladki wlasciciela,
kopia w drugiej zakladce (ten sam blob) zostaje bez zmian, a audyt ma kolejnosc intent -> done. Serwer, rejestr i dwa katalogi jak w testach zgod zakladek."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_history as history  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402
from tests.test_skillsync_dashboard_tabs_approvals import TabsCase, approvals_rows  # noqa: E402

DONE = "docs/plans/2025-12-01-starszy.md"
ROUTE = "/api/plan-status"


def sha_of(root, rel) -> str:
    return status_core.git_blob_sha((Path(root) / rel).read_bytes())


def status_of(root, rel) -> str:
    return status_core._status_fields((Path(root) / rel).read_text(encoding="utf-8"), status_core.PLAN_STATUSES)[0]


def audit_rows(root) -> list:
    path = Path(root) / ".skillsync" / "audit" / "plan-status.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class HistoryCase(TabsCase):
    def token(self, tab_id):
        return self.reg.token_for(tab_id)

    def body(self, tab_id, root, action="archive", rel=DONE, sha=None):
        return {"tab": tab_id, "path": rel, "sha": sha or sha_of(root, rel), "action": action}

    def archive_a(self, **kwargs):
        return self.post_json(self.body(self.id_a, self.root_a, **kwargs), self.token(self.id_a), route=ROUTE)

    def row(self, items, tab_id=None, rel=DONE):
        return next(item for item in items if item["path"] == rel and (tab_id is None or item["tab"] == tab_id))


class ListCase(HistoryCase):
    def test_the_same_file_in_two_tabs_is_one_row_with_two_copies(self) -> None:
        code, data = self.get_json("/api/history")
        self.assertEqual(code, 200, data)
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a, self.id_b])
        self.assertEqual([tab["main"] for tab in data["tabs"]], [True, False])
        self.assertEqual(data["tabs"][1]["branch"], "feature/other")
        rows = [item for item in data["items"] if item["path"] == DONE]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual((row["tab"], row["copies"], row["copyTabs"], row["status"], row["action"], row["abandoned"]),
                         (self.id_a, 2, [self.id_a, self.id_b], "done", "archive", False))
        self.assertEqual(row["sha"], sha_of(self.root_a, DONE))
        self.assertEqual((data["schema"], data["truncated"], data["count"]), (1, False, len(data["items"])))
        self.assertIs(data["here"], True)

    def test_a_plan_that_is_not_finished_is_not_listed(self) -> None:
        _, data = self.get_json("/api/history")
        self.assertEqual({item["status"] for item in data["items"]}, {"done"})
        self.assertNotIn("docs/plans/2026-01-10-demo-etap-1.md", [item["path"] for item in data["items"]])

    def test_a_copy_that_differs_is_its_own_row(self) -> None:
        path = self.root_b / DONE
        path.write_text(path.read_text(encoding="utf-8") + "\nDopisek.\n", encoding="utf-8")
        _, data = self.get_json("/api/history")
        rows = [item for item in data["items"] if item["path"] == DONE]
        self.assertEqual(sorted((row["tab"], row["copies"]) for row in rows), sorted([(self.id_a, 1), (self.id_b, 1)]))

    def test_an_abandoned_approved_plan_is_marked_and_has_no_action(self) -> None:
        plan = self.root_a / "docs/plans/2026-01-10-demo-etap-1.md"
        text = plan.read_text(encoding="utf-8")
        plan.write_text(status_core._STATUS.sub(lambda m: m.group(0).replace(m.group("word"), "approved", 1), text, count=1), encoding="utf-8")
        old = plan.stat().st_mtime - 30 * 86400
        os.utime(plan, (old, old))
        _, data = self.get_json("/api/history")
        row = self.row(data["items"], self.id_a, "docs/plans/2026-01-10-demo-etap-1.md")
        self.assertEqual((row["abandoned"], row["action"], row["status"]), (True, None, "approved"))

    def test_a_tab_that_left_has_no_plans_in_the_history(self) -> None:
        self.reg.deregister(self.id_b)
        _, data = self.get_json("/api/history")
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a])
        self.assertEqual(self.row(data["items"])["copies"], 1)

    def test_a_shut_tab_has_no_plans_in_the_history(self) -> None:
        with mock.patch.object(self.reg, "is_shut", lambda tab_id: tab_id == self.id_b):
            _, data = self.get_json("/api/history")
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a])

    def test_a_symlinked_plans_directory_is_not_read(self) -> None:
        plans = self.root_b / "docs" / "plans"
        moved = self.root_b / "docs" / "plans-real"
        plans.rename(moved)
        try:
            os.symlink(str(moved), str(plans), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        _, data = self.get_json("/api/history")
        self.assertEqual(self.row(data["items"])["copies"], 1)


class LimitsCase(HistoryCase):
    def test_the_status_filter_comes_before_the_per_tab_limit(self) -> None:
        plans = self.root_a / "docs" / "plans"
        template = (self.root_a / DONE).read_text(encoding="utf-8")
        for number in range(history.MAX_PER_TAB + 5):   # newer names sort first and are done too: the oldest must not be silently lost
            (plans / f"2026-06-{number:04d}-x.md").write_text(template.replace("# ", f"# {number} ", 1) + f"\nn{number}\n", encoding="utf-8")
        _, data = self.get_json("/api/history")
        self.assertTrue(data["truncated"])
        in_a = [item for item in data["items"] if item["tab"] == self.id_a]
        self.assertEqual(len(in_a), history.MAX_PER_TAB)

    def test_a_non_finished_plan_does_not_use_up_the_limit(self) -> None:
        plans = self.root_a / "docs" / "plans"
        template = (self.root_a / "docs/plans/2026-01-10-demo-etap-1.md").read_text(encoding="utf-8")
        for number in range(history.MAX_PER_TAB + 20):   # newest names, all in progress: the old done plan still shows
            (plans / f"2027-01-{number:04d}-wip.md").write_text(template + f"\nw{number}\n", encoding="utf-8")
        _, data = self.get_json("/api/history")
        self.assertFalse(data["truncated"])
        self.assertEqual(self.row(data["items"], self.id_a)["copies"], 2)

    def test_the_row_limit_sets_truncated_and_count(self) -> None:
        with mock.patch.object(history, "MAX_ROWS", 0):
            _, data = self.get_json("/api/history")
        self.assertEqual((data["items"], data["truncated"]), ([], True))
        self.assertGreaterEqual(data["count"], 1)

    def test_a_secret_in_a_title_is_masked(self) -> None:
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        path = self.root_a / DONE
        path.write_text(path.read_text(encoding="utf-8").replace("# ", f"# token={secret} ", 1), encoding="utf-8")
        _, raw = self.http("GET", "/api/history")
        self.assertNotIn(secret, raw.decode("utf-8"))
        self.assertNotIn(str(self.tmp), raw.decode("utf-8"))


class ArchiveCase(HistoryCase):
    def test_archiving_in_one_tab_leaves_the_copy_in_the_other_alone(self) -> None:
        before_b = (self.root_b / DONE).read_bytes()
        code, data = self.archive_a()
        self.assertEqual(code, 200, data)
        self.assertEqual((data["ok"], data["tab"], data["from"], data["to"], data["auditFailed"]), (True, self.id_a, "done", "archived", False))
        self.assertEqual(data["sha"], sha_of(self.root_a, DONE))
        self.assertEqual(status_of(self.root_a, DONE), "archived")
        self.assertEqual((self.root_b / DONE).read_bytes(), before_b)
        _, listing = self.get_json("/api/history")
        self.assertEqual(self.row(listing["items"], self.id_a)["status"], "archived")
        self.assertEqual(self.row(listing["items"], self.id_a)["action"], "unarchive")
        other = self.row(listing["items"], self.id_b)
        self.assertEqual((other["status"], other["copies"], other["action"]), ("done", 1, "archive"))

    def test_unarchive_brings_the_plan_back_and_the_trail_has_the_order_intent_done(self) -> None:
        self.assertEqual(self.archive_a()[0], 200)
        code, data = self.archive_a(action="unarchive", sha=sha_of(self.root_a, DONE))
        self.assertEqual(code, 200, data)
        self.assertEqual(status_of(self.root_a, DONE), "done")
        rows = audit_rows(self.root_a)
        self.assertEqual([(row["phase"], row["from"], row["to"]) for row in rows],
                         [("intent", "done", "archived"), ("done", "done", "archived"), ("intent", "archived", "done"), ("done", "archived", "done")])
        self.assertTrue(all(row["tab"] == self.id_a and row["path"] == DONE and row["by"] for row in rows))
        self.assertEqual(audit_rows(self.root_b), [])
        self.assertEqual((approvals_rows(self.root_a), approvals_rows(self.root_b)), ([], []))

    def test_the_token_of_one_tab_never_archives_the_file_of_the_other(self) -> None:
        before = (self.root_a / DONE).read_bytes(), (self.root_b / DONE).read_bytes()
        for token_tab, body_tab in ((self.id_a, self.id_b), (self.id_b, self.id_a)):
            code, data = self.post_json(self.body(body_tab, self.root_a), self.token(token_tab), route=ROUTE)
            self.assertEqual((code, data.get("code")), (400, "tabMismatch"), data)
        self.assertEqual(((self.root_a / DONE).read_bytes(), (self.root_b / DONE).read_bytes()), before)
        self.assertEqual((audit_rows(self.root_a), audit_rows(self.root_b)), ([], []))

    def test_a_missing_or_wrong_token_is_the_ordinary_refusal(self) -> None:
        for token in (None, "zly-token", self.token(self.id_a)[:-1] + ("1" if self.token(self.id_a)[-1] == "0" else "0")):
            code, data = self.post_json(self.body(self.id_a, self.root_a), token, route=ROUTE)
            self.assertEqual((code, data.get("code")), (403, "approve_token"), data)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_the_header_chain_is_the_one_of_the_consents(self) -> None:
        body, token = self.body(self.id_a, self.root_a), self.token(self.id_a)
        code, _ = self.http("POST", ROUTE, body, {"Content-Type": "application/json", "Origin": self.base, "X-Approve-Token": token})
        self.assertEqual(code, 403)   # no X-Status-Board
        code, _ = self.http("POST", ROUTE, body, {"Content-Type": "application/json", "X-Status-Board": "1", "Origin": "http://evil.example", "X-Approve-Token": token})
        self.assertEqual(code, 403)
        code, _ = self.http("POST", ROUTE, body, {"Content-Type": "text/plain", "X-Status-Board": "1", "Origin": self.base, "X-Approve-Token": token})
        self.assertEqual(code, 403)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_refusals_of_the_content(self) -> None:
        cases = [
            (dict(sha="0" * 40), 409, "shaMismatch"),
            (dict(action="unarchive"), 409, "statusNotAllowed"),
            (dict(rel="docs/plans/2026-01-10-demo-etap-1.md"), 409, "statusNotAllowed"),
            (dict(rel="docs/plans/nie-ma.md", sha="0" * 40), 404, "planGone"),
            (dict(rel="../x.md", sha="0" * 40), 400, "badRequest"),
            (dict(rel="docs/specs/demo-etap-1.md", sha="0" * 40), 400, "badRequest"),
            (dict(action="delete"), 400, "badRequest"),
        ]
        for kwargs, http, code in cases:
            rel = kwargs.get("rel", DONE)
            body = {"tab": self.id_a, "path": rel, "sha": kwargs.get("sha") or sha_of(self.root_a, rel), "action": kwargs.get("action", "archive")}
            got, data = self.post_json(body, self.token(self.id_a), route=ROUTE)
            self.assertEqual((got, data.get("code")), (http, code), (kwargs, data))
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(audit_rows(self.root_a), [])

    def test_a_body_without_the_tab_or_with_a_wrong_type_is_a_400(self) -> None:
        good = self.body(self.id_a, self.root_a)
        for mutate in (lambda b: b.pop("tab"), lambda b: b.update(sha=5), lambda b: b.update(path=None), lambda b: b.pop("action")):
            body = dict(good)
            mutate(body)
            code, _ = self.post_json(body, self.token(self.id_a), route=ROUTE)
            self.assertEqual(code, 400, body)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_a_symlink_on_the_audit_path_refuses_the_change_and_changes_nothing(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.root_a / ".skillsync").mkdir(exist_ok=True)
        try:
            os.symlink(str(outside), str(self.root_a / ".skillsync" / "audit"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        code, data = self.archive_a()
        self.assertEqual((code, data.get("code")), (500, "auditRefused"), data)
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_symlink_on_the_lock_path_refuses_without_writing_outside(self) -> None:
        outside = self.tmp / "outside-locks"
        outside.mkdir()
        (self.root_a / ".skillsync").mkdir(exist_ok=True)
        try:
            os.symlink(str(outside), str(self.root_a / ".skillsync" / "locks"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        code, _ = self.archive_a()
        self.assertEqual(code, 500)
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_dot_skillsync_symlink_changes_nothing(self) -> None:
        outside = self.tmp / "outside-state"
        outside.mkdir()
        real = self.root_a / ".skillsync"
        if real.exists():
            real.rename(self.root_a / ".skillsync-real")
        try:
            os.symlink(str(outside), str(real), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        code, _ = self.archive_a()
        self.assertEqual(code, 500)
        self.assertEqual((status_of(self.root_a, DONE), list(outside.iterdir())), ("done", []))

    def test_a_symlinked_audit_file_or_a_fifo_refuses_the_change(self) -> None:
        audit = self.root_a / ".skillsync" / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        target = self.tmp / "elsewhere.jsonl"
        target.write_text("", encoding="utf-8")
        try:
            os.symlink(str(target), str(audit / "plan-status.jsonl"))
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        code, data = self.archive_a()
        self.assertEqual((code, data.get("code")), (500, "auditRefused"), data)
        self.assertEqual((status_of(self.root_a, DONE), target.read_text(encoding="utf-8")), ("done", ""))
        (audit / "plan-status.jsonl").unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(str(audit / "plan-status.jsonl"))
            code, data = self.archive_a()   # must return at once: no open of the pipe
            self.assertEqual((code, data.get("code")), (500, "auditRefused"), data)

    def test_an_audit_over_the_size_limit_refuses_the_change(self) -> None:
        with mock.patch.object(history, "MAX_AUDIT_BYTES", 10):
            code, data = self.archive_a()
        self.assertEqual((code, data.get("code"), status_of(self.root_a, DONE)), (500, "auditRefused", "done"), data)

    def test_a_write_that_fails_after_the_intent_leaves_a_failed_row_and_a_500_for_io(self) -> None:
        with mock.patch.object(history.status_core, "set_doc_status", side_effect=OSError("no space")):
            code, data = self.archive_a()
        self.assertEqual((code, data.get("code")), (500, "serverError"), data)
        self.assertEqual([row["phase"] for row in audit_rows(self.root_a)], ["intent", "failed"])
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_a_replaced_tab_path_gets_no_state_directory_and_no_other_refusal(self) -> None:
        body, token = self.body(self.id_b, self.root_b), self.token(self.id_b)
        moved = self.swap_ancestor_for_a_link()
        outside_state = moved / "proj" / ".skillsync" / "locks"
        code, data = self.post_json(body, token, route=ROUTE)
        self.assertEqual((code, data.get("code")), (409, "tabChanged"), data)
        self.assertFalse(outside_state.exists())
        self.assertEqual(status_of(moved / "proj", DONE), "done")

    def test_a_worktree_that_got_an_exclusion_marker_leaves_the_history_at_once(self) -> None:
        (self.root_b / ".skillsync").mkdir(exist_ok=True)
        (self.root_b / ".skillsync" / "no-dashboard").write_text("", encoding="utf-8")
        _, data = self.get_json("/api/history")
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a])
        code, answer = self.post_json(self.body(self.id_b, self.root_b), self.token(self.id_b), route=ROUTE)
        self.assertEqual(code, 409, answer)   # the re-validation of the write refuses (the registry removes the tab itself only after two refreshes)
        self.assertEqual(status_of(self.root_b, DONE), "done")

    def test_the_read_socket_has_no_plan_status_route(self) -> None:
        handler = srv.ReadHandler
        self.assertIs(handler.do_POST, handler._no_route)

    def test_a_failed_done_entry_still_changes_the_status_and_says_so(self) -> None:
        real = history._audit_append

        def flaky(root, row):
            if row.get("phase") == "done":
                raise OSError("disk")
            return real(root, row)
        with mock.patch.object(history, "_audit_append", flaky):
            code, data = self.archive_a()
        self.assertEqual(code, 200, data)
        self.assertIs(data["auditFailed"], True)
        self.assertEqual(status_of(self.root_a, DONE), "archived")
        self.assertEqual([row["phase"] for row in audit_rows(self.root_a)], ["intent"])

    def test_a_busy_file_lock_is_a_409_and_nothing_changes(self) -> None:
        lock = history.FileLock(str(Path(history._state_dir(self.root_a, "locks")) / history.LOCK_NAME), 1.0, 0o600, unlink=True)
        with mock.patch.object(history, "LOCK_TIMEOUT", 0.2), lock:
            code, data = self.archive_a()
        self.assertEqual((code, data.get("code")), (409, "locked"), data)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_a_tab_that_changed_before_the_write_is_a_409_tab_changed(self) -> None:
        with mock.patch.object(self.reg, "revalidate_for_write", lambda tab_id: False):
            code, data = self.archive_a()
        self.assertEqual((code, data.get("code")), (409, "tabChanged"), data)
        self.assertEqual((status_of(self.root_a, DONE), audit_rows(self.root_a)), ("done", []))

    def test_a_gone_tab_is_a_410(self) -> None:
        token, body = self.token(self.id_b), self.body(self.id_b, self.root_b)
        self.reg.deregister(self.id_b)
        code, data = self.post_json(body, token, route=ROUTE)
        self.assertEqual((code, data.get("code")), (410, "tabGone"), data)
        self.assertEqual(status_of(self.root_b, DONE), "done")

    def test_without_approvals_nothing_is_written(self) -> None:
        board = SimpleNamespace(approvals=False)
        code, data = srv.plan_status_request(board, self.id_a, self.body(self.id_a, self.root_a), None, None, "pl")
        self.assertEqual((code, data["code"]), (403, "approvalsOff"))
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_two_requests_for_the_same_file_write_once(self) -> None:
        results = []
        barrier = threading.Barrier(2)
        body, token = self.body(self.id_a, self.root_a), self.token(self.id_a)

        def go():
            barrier.wait()
            results.append(self.post_json(body, token, route=ROUTE))
        threads = [threading.Thread(target=go) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(code for code, _ in results), [200, 409], results)
        loser = next(data for code, data in results if code == 409)
        self.assertIn(loser["code"], ("shaMismatch", "statusNotAllowed", "locked"))
        self.assertEqual([row["phase"] for row in audit_rows(self.root_a)], ["intent", "done"])

    def test_the_same_blob_in_two_tabs_is_archived_independently(self) -> None:
        barrier = threading.Barrier(2)
        results = {}

        def go(tab_id, root):
            barrier.wait()
            results[tab_id] = self.post_json(self.body(tab_id, root), self.token(tab_id), route=ROUTE)
        threads = [threading.Thread(target=go, args=(self.id_a, self.root_a)), threading.Thread(target=go, args=(self.id_b, self.root_b))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual({tab: code for tab, (code, _) in results.items()}, {self.id_a: 200, self.id_b: 200})
        self.assertEqual((status_of(self.root_a, DONE), status_of(self.root_b, DONE)), ("archived", "archived"))
        self.assertEqual(([row["tab"] for row in audit_rows(self.root_a)], [row["tab"] for row in audit_rows(self.root_b)]),
                         ([self.id_a] * 2, [self.id_b] * 2))


if __name__ == "__main__":
    unittest.main()
