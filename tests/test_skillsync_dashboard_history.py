"""CORE-1180 M-6: zakladka „Historia” (backend): `GET /api/history` i `POST /api/plan-status` (archiwizacja planow `done`).

Kontrakt U-2: `plan-status` przechodzi te sama bramke co zgody (sesja z rola `approver`, CSRF, Origin, `Sec-Fetch-Site`), a plik wybiera `tab` z body; nie ma tokenu zakladki.
Dowodem jest ZAWARTOSC plikow planow i audytu w obu katalogach (nie tylko kod odpowiedzi): zapis dotyczy wylacznie pliku zakladki wlasciciela,
kopia w drugiej zakladce (ten sam blob) zostaje bez zmian, a audyt ma kolejnosc intent -> done. Serwer, rejestr i dwa katalogi jak w testach zgod zakladek."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_history as history  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import i18n  # noqa: E402
from skillsync_workflow import prototype_server as proto_server  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402
from tests.test_skillsync_dashboard_server import COMMENTER_LOGIN, SESSION_PW_COMMENTER, session_request, split_post  # noqa: E402
from tests.test_skillsync_dashboard_tabs_approvals import TabsCase, approvals_rows  # noqa: E402

DONE = "docs/plans/2025-12-01-starszy.md"
IDLE = "docs/plans/2026-02-01-lezy.md"
IDLE_TEXT = "# Plan: leży\n\nStatus: approved\nPrzebieg: 2026-02-01-lezy\n\n## Progress\n\n- [ ] A-1 krok\n"
ROUTE = "/api/plan-status"


def idle_plan(root, days=30, text=IDLE_TEXT):
    """An untracked `approved` plan with no progress and no run, with the file time `days` days ago (B-4a: an uncommitted file is dated by it)."""
    path = Path(root) / "docs/plans/2026-02-01-lezy.md"
    path.write_text(text, encoding="utf-8", newline="")
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))
    return path


def sha_of(root, rel) -> str:
    return status_core.git_blob_sha((Path(root) / rel).read_bytes())


def status_of(root, rel) -> str:
    return status_core._status_fields((Path(root) / rel).read_text(encoding="utf-8"), status_core.PLAN_STATUSES)[0]


def audit_rows(root) -> list:
    path = Path(root) / ".skillsync" / "audit" / "plan-status.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class HistoryCase(TabsCase):
    def body(self, tab_id, root, action="archive", rel=DONE, sha=None):
        return {"tab": tab_id, "path": rel, "sha": sha or sha_of(root, rel), "action": action}

    def archive_a(self, **kwargs):
        return self.post_json(self.body(self.id_a, self.root_a, **kwargs), route=ROUTE)

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

    def test_an_approved_plan_with_progress_is_not_abandoned_however_old(self) -> None:
        # kontrakt B-4a: plan z odhaczonym krokiem nie jest porzucony (dawniej decydował sam wiek pliku)
        plan = self.root_a / "docs/plans/2026-01-10-demo-etap-1.md"
        text = plan.read_text(encoding="utf-8")
        self.assertIn("[x]", text, "the fixture plan has progress")
        plan.write_text(status_core._STATUS.sub(lambda m: m.group(0).replace(m.group("word"), "approved", 1), text, count=1), encoding="utf-8")
        old = plan.stat().st_mtime - 30 * 86400
        os.utime(plan, (old, old))
        _, data = self.get_json("/api/history")
        self.assertNotIn("docs/plans/2026-01-10-demo-etap-1.md", [item["path"] for item in data["items"]])

    def test_an_abandoned_approved_plan_is_marked_and_can_be_abandoned(self) -> None:
        idle_plan(self.root_a)
        _, data = self.get_json("/api/history")
        row = self.row(data["items"], self.id_a, IDLE)
        self.assertEqual((row["abandoned"], row["action"], row["status"], row["restoreTo"]), (True, "abandon", "approved", None))

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

    def test_the_tab_of_the_body_picks_the_directory_and_a_sha_of_the_other_copy_is_refused(self) -> None:
        path = self.root_b / DONE
        path.write_text(path.read_text(encoding="utf-8") + "\nDopisek.\n", encoding="utf-8")   # the copy of B differs now
        before = (self.root_a / DONE).read_bytes(), path.read_bytes()
        for body_tab, sha_root in ((self.id_a, self.root_b), (self.id_b, self.root_a)):
            code, data = self.post_json(self.body(body_tab, sha_root), route=ROUTE)
            self.assertEqual((code, data.get("code")), (409, "shaMismatch"), data)
        self.assertEqual(((self.root_a / DONE).read_bytes(), path.read_bytes()), before)
        self.assertEqual((audit_rows(self.root_a), audit_rows(self.root_b)), ([], []))

    def test_without_a_session_or_with_a_wrong_one_nothing_is_archived(self) -> None:
        body = self.body(self.id_a, self.root_a)
        code, data = self.post_json(body, None, route=ROUTE)
        self.assertEqual((code, data.get("code")), (401, "login_required"), data)
        for headers, expected in (({"Cookie": self.session.stale_pair, "X-Skillsync-CSRF": "x"}, (401, "session_expired")),
                                  ({"Cookie": self.session.pair, "X-Skillsync-CSRF": "x"}, (403, "consent_refused"))):
            code, raw = self.post(body, None, route=ROUTE, headers=headers)
            self.assertEqual((code, json.loads(raw)["code"]), expected)
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(self.archive_a()[0], 200)   # control

    def test_the_header_chain_is_the_one_of_the_consents(self) -> None:
        body = self.body(self.id_a, self.root_a)
        for headers in ({"X-Status-Board": "0"}, {"Origin": "http://evil.example"}, {"Content-Type": "text/plain"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                code, raw = self.post(body, route=ROUTE, headers=headers)
                self.assertEqual((code, json.loads(raw)["code"]), (403, "consent_refused"))
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(self.archive_a()[0], 200)   # control: the same request with the right headers

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
            got, data = self.post_json(body, route=ROUTE)
            self.assertEqual((got, data.get("code")), (http, code), (kwargs, data))
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(audit_rows(self.root_a), [])

    def test_a_body_without_the_tab_or_with_a_wrong_type_is_a_400(self) -> None:
        good = self.body(self.id_a, self.root_a)
        for mutate in (lambda b: b.pop("tab"), lambda b: b.update(sha=5), lambda b: b.update(path=None), lambda b: b.pop("action")):
            body = dict(good)
            mutate(body)
            code, _ = self.post_json(body, route=ROUTE)
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
        body = self.body(self.id_b, self.root_b)
        moved = self.swap_ancestor_for_a_link()
        outside_state = moved / "proj" / ".skillsync" / "locks"
        code, data = self.post_json(body, route=ROUTE)
        self.assertEqual((code, data.get("code")), (409, "tabChanged"), data)
        self.assertFalse(outside_state.exists())
        self.assertEqual(status_of(moved / "proj", DONE), "done")

    def test_a_worktree_that_got_an_exclusion_marker_leaves_the_history_with_the_registry_and_the_write_is_refused_at_once(self) -> None:
        (self.root_b / ".skillsync").mkdir(exist_ok=True)
        (self.root_b / ".skillsync" / "no-dashboard").write_text("", encoding="utf-8")
        code, answer = self.post_json(self.body(self.id_b, self.root_b), route=ROUTE)
        self.assertEqual(code, 409, answer)   # the re-validation of the write refuses at once (the registry removes the tab itself only after two refreshes)
        self.assertEqual(status_of(self.root_b, DONE), "done")
        with mock.patch.object(self.reg, "revalidate_for_write", side_effect=AssertionError("a read re-validates on disk")):
            _, data = self.get_json("/api/history")   # the anonymous read uses the state of the last refresh of the registry, not the disk
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a, self.id_b])
        self.reg.deregister(self.id_b, tombstone=False)   # what the second refresh does for an excluded tab
        history_cache = srv.HISTORY_CACHE_S
        _, data = self.get_json("/api/history")
        self.assertEqual([tab["id"] for tab in data["tabs"]], [self.id_a], history_cache)

    def test_a_read_within_the_ttl_after_a_status_write_sees_the_new_status_and_sha(self) -> None:
        _, before = self.get_json("/api/history")
        row = self.row(before["items"], self.id_a)
        self.assertEqual(row["status"], "done")
        code, data = self.archive_a()
        self.assertEqual(code, 200, data)
        _, after = self.get_json("/api/history")   # within HISTORY_CACHE_S of the first read
        moved = self.row(after["items"], self.id_a)
        self.assertEqual(moved["status"], "archived")
        self.assertNotEqual(moved["sha"], row["sha"], "the row carries the sha of the written file")
        self.assertEqual(moved["sha"], data["sha"])
        code, again = self.post_json(self.body(self.id_a, self.root_a, action="unarchive", sha=moved["sha"]), route=ROUTE)
        self.assertEqual(code, 200, again)   # the sha of the fresh row is accepted (no 409 shaMismatch)

    def test_the_history_answer_is_built_once_in_the_ttl_and_follows_the_clock(self) -> None:
        calls = []
        real = history.collect
        with mock.patch.object(history, "collect", lambda tabs, now=None: (calls.append(1), real(tabs, now))[1]):
            first = self.get_json("/api/history")[1]
            self.get_json("/api/history")
            self.assertEqual(len(calls), 1, "the second read within the TTL is the same answer")
            self.assertEqual(first["count"], self.get_json("/api/history")[1]["count"])
            self.reg._history_cache = (self.reg._history_cache[0], self.reg._history_cache[1] - srv.HISTORY_CACHE_S - 1, self.reg._history_cache[2])
            self.get_json("/api/history")
            self.assertEqual(len(calls), 2, "after the TTL it is built again")

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
        body = self.body(self.id_b, self.root_b)
        self.reg.deregister(self.id_b)
        code, data = self.post_json(body, route=ROUTE)
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
        body = self.body(self.id_a, self.root_a)

        def go():
            barrier.wait()
            results.append(self.post_json(body, route=ROUTE))
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
            results[tab_id] = self.post_json(self.body(tab_id, root), route=ROUTE)
        threads = [threading.Thread(target=go, args=(self.id_a, self.root_a)), threading.Thread(target=go, args=(self.id_b, self.root_b))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual({tab: code for tab, (code, _) in results.items()}, {self.id_a: 200, self.id_b: 200})
        self.assertEqual((status_of(self.root_a, DONE), status_of(self.root_b, DONE)), ("archived", "archived"))
        self.assertEqual(([row["tab"] for row in audit_rows(self.root_a)], [row["tab"] for row in audit_rows(self.root_b)]),
                         ([self.id_a] * 2, [self.id_b] * 2))


class SessionAndAuditBeforeTheChangeCase(HistoryCase):
    """Kontrakt U-2-3: `plan-status` is checked again right before the first write (B1) and needs the `consent` line of the audit (D1)."""

    def split(self, between=None) -> tuple[int, dict]:
        headers = {**self.session.headers(self.port), "Origin": self.base}
        return split_post(self.port, ROUTE, headers, json.dumps(self.body(self.id_a, self.root_a)).encode("utf-8"), between)

    def assert_untouched(self) -> None:
        self.assertEqual(status_of(self.root_a, DONE), "done")
        self.assertEqual(audit_rows(self.root_a), [])

    def test_a_logout_while_the_body_arrives_is_401_and_changes_nothing(self) -> None:
        def logout() -> None:
            status, data, _ = session_request(self.port, "POST", "/_api/v1/logout", {}, self.session.headers(self.port))
            self.assertLess(status, 300, data)
        code, answer = self.split(logout)
        self.assertEqual((code, answer.get("code")), (401, "session_expired"), answer)
        self.assert_untouched()

    def test_control_the_two_part_request_with_nothing_in_between_archives(self) -> None:
        code, answer = self.split()
        self.assertEqual(code, 200, answer)
        self.assertEqual(status_of(self.root_a, DONE), "archived")

    def test_without_the_audit_line_of_the_consent_nothing_changes(self) -> None:
        with mock.patch.object(proto_server, "link_audit_append", side_effect=OSError("disk")):
            code, answer = self.archive_a()
        self.assertEqual((code, answer.get("code")), (503, "audit_unavailable"), answer)
        self.assert_untouched()
        self.assertEqual(self.archive_a()[0], 200)   # control
        line = [line for line in self.kit.audit() if line["event"] == "consent"][-1]
        self.assertEqual((line["word"], line["path"], line["route"], line["tab"]), ("archive", DONE, ROUTE, self.id_a))

    def test_a_plan_status_without_a_tab_is_a_400_with_several_tabs(self) -> None:
        body = {key: value for key, value in self.body(self.id_a, self.root_a).items() if key != "tab"}
        code, answer = self.post_json(body, route=ROUTE)
        self.assertEqual((code, answer.get("code")), (400, "badRequest"), answer)
        self.assert_untouched()


class AbandonCase(HistoryCase):
    """B-4a (aneks speca, Z6): `POST /api/plan-status` z akcją `abandon` przez sesję approvera; odmowy jak przy `archive` i 409 `notAbandoned`."""

    def abandon_a(self, **kwargs):
        return self.post_json(self.body(self.id_a, self.root_a, **{"action": "abandon", "rel": IDLE, **kwargs}), route=ROUTE)

    def untouched(self, path, before):
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((audit_rows(self.root_a), audit_rows(self.root_b)), ([], []))

    def test_abandon_then_restore_goes_back_to_approved_with_the_audit_of_both(self) -> None:
        path = idle_plan(self.root_a)
        before = path.read_bytes()
        code, data = self.abandon_a()
        self.assertEqual(code, 200, data)
        self.assertEqual((data["from"], data["to"], data["tab"], data["auditFailed"]), ("approved", "archived", self.id_a, False))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertEqual(path.read_bytes(), before.replace(b"Status: approved\n", f"Status: archived\nPorzucony: {today}\n".encode()))
        self.assertEqual(data["sha"], sha_of(self.root_a, IDLE))
        _, listing = self.get_json("/api/history")
        row = self.row(listing["items"], self.id_a, IDLE)
        self.assertEqual((row["status"], row["action"], row["restoreTo"], row["abandoned"]), ("archived", "unarchive", "approved", False))
        code, back = self.abandon_a(action="unarchive")
        self.assertEqual((code, back.get("from"), back.get("to")), (200, "archived", "approved"), back)
        self.assertEqual(path.read_bytes(), before)
        rows = audit_rows(self.root_a)
        self.assertEqual([(r["phase"], r["from"], r["to"]) for r in rows],
                         [("intent", "approved", "archived"), ("done", "approved", "archived"), ("intent", "archived", "approved"), ("done", "archived", "approved")])
        self.assertTrue(all(r["tab"] == self.id_a and r["path"] == IDLE and r["by"] for r in rows))
        self.assertEqual((approvals_rows(self.root_a), approvals_rows(self.root_b)), ([], []))

    def test_an_archived_done_plan_restores_to_done(self) -> None:
        self.assertEqual(self.archive_a()[0], 200)
        _, listing = self.get_json("/api/history")
        self.assertEqual(self.row(listing["items"], self.id_a)["restoreTo"], "done")
        self.assertIsNone(self.row(listing["items"], self.id_b)["restoreTo"])

    def test_a_plan_that_does_not_meet_the_definition_is_a_409_not_abandoned(self) -> None:
        cases = {"fresh": (1, IDLE_TEXT), "progress": (30, IDLE_TEXT.replace("[ ]", "[x]"))}
        for label, (days, text) in cases.items():
            with self.subTest(label):
                path = idle_plan(self.root_a, days, text)
                before = path.read_bytes()
                code, data = self.abandon_a()
                self.assertEqual((code, data.get("code")), (409, "notAbandoned"), data)
                self.assertEqual(data["error"], i18n.t("pl", "srv.history.notAbandoned"))
                self.untouched(path, before)

    def test_a_run_that_appeared_after_the_list_was_read_refuses(self) -> None:
        path = idle_plan(self.root_a)
        _, listing = self.get_json("/api/history")
        self.assertEqual(self.row(listing["items"], self.id_a, IDLE)["action"], "abandon")
        (self.root_a / ".skillsync" / "runs" / "2026-02-01-lezy").mkdir(parents=True)
        before = path.read_bytes()
        code, data = self.abandon_a()
        self.assertEqual((code, data.get("code")), (409, "notAbandoned"), data)
        self.untouched(path, before)

    def test_the_run_of_the_tab_of_the_file_decides(self) -> None:
        idle_a, idle_b = idle_plan(self.root_a), idle_plan(self.root_b, text=IDLE_TEXT + "\nkopia B\n")
        (self.root_b / ".skillsync" / "runs" / "2026-02-01-lezy").mkdir(parents=True)
        before_b = idle_b.read_bytes()
        _, listing = self.get_json("/api/history")
        self.assertEqual([row["tab"] for row in listing["items"] if row["path"] == IDLE], [self.id_a])
        code, data = self.post_json(self.body(self.id_b, self.root_b, action="abandon", rel=IDLE), route=ROUTE)
        self.assertEqual((code, data.get("code")), (409, "notAbandoned"), data)
        self.assertEqual(self.abandon_a()[0], 200)
        self.assertEqual(idle_b.read_bytes(), before_b)
        self.assertIn(b"Porzucony:", idle_a.read_bytes())

    def test_other_refusals_of_the_content(self) -> None:
        path = idle_plan(self.root_a)
        before = path.read_bytes()
        for kwargs, expected in ((dict(sha="0" * 40), (409, "shaMismatch")), (dict(action="archive"), (409, "statusNotAllowed")),
                                 (dict(action="unarchive"), (409, "statusNotAllowed")), (dict(rel=DONE), (409, "statusNotAllowed"))):
            with self.subTest(**kwargs):
                code, data = self.abandon_a(**kwargs)
                self.assertEqual((code, data.get("code")), expected, data)
        self.untouched(path, before)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_without_the_session_of_the_consents_nothing_is_abandoned(self) -> None:
        path = idle_plan(self.root_a)
        before = path.read_bytes()
        body = self.body(self.id_a, self.root_a, action="abandon", rel=IDLE)
        code, data = self.post_json(body, None, route=ROUTE)
        self.assertEqual((code, data.get("code")), (401, "login_required"), data)
        for headers, expected in (({"Cookie": self.session.stale_pair, "X-Skillsync-CSRF": "x"}, (401, "session_expired")),
                                  ({"Cookie": self.session.pair, "X-Skillsync-CSRF": "x"}, (403, "consent_refused"))):
            code, raw = self.post(body, None, route=ROUTE, headers=headers)
            self.assertEqual((code, json.loads(raw)["code"]), expected)
        for headers in ({"X-Status-Board": "0"}, {"Origin": "http://evil.example"}, {"Content-Type": "text/plain"}):
            code, raw = self.post(body, route=ROUTE, headers=headers)
            self.assertEqual((code, json.loads(raw)["code"]), (403, "consent_refused"), headers)
        commenter = self.kit.login(self.port, COMMENTER_LOGIN, SESSION_PW_COMMENTER)
        code, raw = self.post(body, None, route=ROUTE, headers=commenter.headers(self.port))
        self.assertEqual(code, 403, raw)
        self.untouched(path, before)
        self.assertEqual(self.abandon_a()[0], 200)   # control

    def test_a_busy_lock_and_a_changed_tab_refuse(self) -> None:
        path = idle_plan(self.root_a)
        before = path.read_bytes()
        lock = history.FileLock(str(Path(history._state_dir(self.root_a, "locks")) / history.LOCK_NAME), 1.0, 0o600, unlink=True)
        with mock.patch.object(history, "LOCK_TIMEOUT", 0.2), lock:
            code, data = self.abandon_a()
        self.assertEqual((code, data.get("code")), (409, "locked"), data)
        with mock.patch.object(self.reg, "revalidate_for_write", lambda tab_id: False):
            code, data = self.abandon_a()
        self.assertEqual((code, data.get("code")), (409, "tabChanged"), data)
        self.untouched(path, before)


CLI = ROOT / "skillsync"


class SharedLockCase(HistoryCase):
    """The lock of `status.plan_status_lock` is shared by the page and `skillsync plan archive|approve` (a spec requirement)."""

    def setUp(self) -> None:
        super().setUp()
        manifest = self.root_a / ".github" / ".skillsync.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("workflow: {}\n", encoding="utf-8")

    def cli(self, root, *args):
        result = subprocess.run([sys.executable, str(CLI), "plan", *args], capture_output=True, text=True, timeout=60, cwd=root,
                                env={**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "none.yaml")})
        try:
            return result.returncode, json.loads(result.stdout)
        except ValueError:
            return result.returncode, result.stdout + result.stderr

    def test_the_holder_does_not_wait_for_itself(self) -> None:
        with status_core.plan_status_lock(self.root_a):
            report = status_core.set_doc_status(self.root_a, DONE, "archive", "plans", lock=False)
        self.assertEqual(report["to"], "archived")
        self.assertEqual(status_of(self.root_a, DONE), "archived")

    def test_set_doc_status_waits_for_a_busy_lock_and_then_says_so(self) -> None:
        with status_core.plan_status_lock(self.root_a), mock.patch.object(status_core, "PLAN_STATUS_LOCK_TIMEOUT", 0.2):
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "locked by another writer"):
                status_core.set_doc_status(self.root_a, DONE, "archive", "plans")
            self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_the_cli_with_a_busy_lock_is_blocked_with_a_nonzero_code_and_changes_nothing(self) -> None:
        with status_core.plan_status_lock(self.root_a):
            code, report = self.cli(self.root_a, "archive", str(self.root_a), DONE, "--json")
        self.assertEqual(code, 2, report)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("locked by another writer", report["reason"])
        self.assertEqual(status_of(self.root_a, DONE), "done")

    def test_the_cli_with_a_symlinked_state_directory_is_blocked_and_writes_nowhere(self) -> None:
        outside = self.tmp / "cli-outside"
        outside.mkdir()
        real = self.root_a / ".skillsync"
        real.rename(self.root_a / ".skillsync-real")
        try:
            os.symlink(str(outside), str(real), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks")
        code, report = self.cli(self.root_a, "archive", str(self.root_a), DONE, "--json")
        self.assertEqual((code, report["status"]), (2, "BLOCKED"), report)
        self.assertIn(".skillsync", report["reason"])
        self.assertEqual((status_of(self.root_a, DONE), list(outside.iterdir())), ("done", []))

    def test_the_cli_and_the_page_on_one_plan_write_once_in_turn(self) -> None:
        results = {}
        with status_core.plan_status_lock(self.root_a):
            page = threading.Thread(target=lambda: results.update(page=self.archive_a()))
            page.start()
            cli = threading.Thread(target=lambda: results.update(cli=self.cli(self.root_a, "archive", str(self.root_a), DONE, "--json")))
            cli.start()
            time.sleep(0.7)   # both are waiting for the lock now
        page.join()
        cli.join()
        page_code = results["page"][0]
        cli_code = results["cli"][0]
        self.assertEqual(int(page_code == 200) + int(cli_code == 0), 1, results)   # exactly one wrote
        loser = results["cli"][1] if page_code == 200 else results["page"][1]
        self.assertIn("BLOCKED" if page_code == 200 else "statusNotAllowed", json.dumps(loser) if not isinstance(loser, str) else loser)
        self.assertEqual(status_of(self.root_a, DONE), "archived")
        phases = [row["phase"] for row in audit_rows(self.root_a)]
        self.assertEqual(phases, ["intent", "done"] if page_code == 200 else [], results)


if __name__ == "__main__":
    unittest.main()
