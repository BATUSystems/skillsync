"""CORE-1180 M-10 (backend): koszt odswiezania `waiting`, powtorzenie zapisu stanu/pinu na Windows, rejestr z inna wielkoscia liter, limit plikow na katalog,
logi z limitem, ukryta (wykluczona) zakladka startowa.

Wszystko na plikach w katalogu tymczasowym; stan Skillsync (`XDG_DATA_HOME`) przekierowany do niego. Atrapy bledow Windows: `PermissionError` z `os.replace`
i `prototype_server._WINDOWS = True` (na Linuksie `retry_transient` inaczej nie ponawia)."""
from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_token as token  # noqa: E402
from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from skillsync_workflow import prototype_server  # noqa: E402
from tmp_paths import child  # noqa: E402

PLAN = "# Plan {n}\n\nStatus: {status}\n\n## Progress\n\n- [{mark}] S1 krok\n"
SPEC = "# Spec {n}\n\nStatus: draft\n"
TABS = 16
PLANS = 120
SPECS = 100


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def entry(root: Path, tab_id: str, kind="worktree") -> dw.Entry:
    return dw.Entry(tab_id, dw.normalize_path(str(root)), root.name, kind, False, False, None, None, None)


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(os.path.realpath(holder.name))
        patch = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")})
        patch.start()
        self.addCleanup(patch.stop)

    def registry(self, names=("main",), project=None) -> srv.TabRegistry:
        roots = []
        for name in names:
            child(self.tmp, name).mkdir(exist_ok=True)
            roots.append(child(self.tmp, name))
        registry = srv.TabRegistry(srv.Board(roots[0], approvals=True), project=project)
        self.addCleanup(registry.stop_all)
        for root in roots[1:]:
            registry.register(entry(root, dw.tab_id(str(root))))
        return registry


class CostOfTheWaitingRefresh(Tmp):
    """Sixteen tabs of 120 plans and 100 specs each: the refresh of the list reads every plan once, the next one with no change opens none."""

    def setUp(self) -> None:
        super().setUp()
        names = [f"t{n:02d}" for n in range(TABS)]
        for name in names:
            for number in range(PLANS):
                write(child(self.tmp, name) / "docs" / "plans" / f"2026-01-01-p{number:03d}.md", PLAN.format(n=number, status="done", mark="x"))
            for number in range(SPECS):
                write(child(self.tmp, name) / "docs" / "specs" / f"2026-01-01-s{number:03d}.md", SPEC.format(n=number))
        engine._PLAN_CACHE.clear()
        engine._SPEC_CACHE.clear()
        self.registry_ = self.registry(names)

    @contextlib.contextmanager
    def counting(self):
        counted = {"docs": 0, "all": 0}
        real = builtins.open

        def spy(file, *args, **kwargs):
            counted["all"] += 1
            text = str(file).replace("\\", "/")
            if "/docs/plans/" in text or "/docs/specs/" in text or "/.skillsync/runs/" in text:
                counted["docs"] += 1
            return real(file, *args, **kwargs)

        with mock.patch.object(builtins, "open", spy), mock.patch.object(io, "open", spy):
            yield counted

    def test_the_second_refresh_without_a_change_opens_no_plan_or_spec(self) -> None:
        with self.counting() as first:
            self.registry_.refresh_waiting()
        with self.counting() as second:
            self.registry_.refresh_waiting()
        print(f"MEASURE M-10 waiting refresh | tabs={TABS} plans={PLANS} specs={SPECS} | first: plan/spec/run opens={first['docs']} | "
              f"steady: plan/spec/run opens={second['docs']}", file=sys.stderr)
        self.assertGreaterEqual(first["docs"], TABS * PLANS, "control: the first pass reads the plans")
        self.assertLessEqual(first["docs"], TABS * (PLANS + SPECS) * 2, "budget of the first pass: one read per file (and the chosen ones again)")
        self.assertEqual(second["docs"], 0, "a refresh with nothing changed opens no plan, spec or run file")
        self.assertEqual({tab["history"] for tab in self.registry_.summary()}, {PLANS}, "the counter of History survives the skipped pass")

    def test_the_cache_holds_every_plan_of_the_fixture_without_a_clear(self) -> None:
        self.registry_.refresh_waiting()
        self.assertGreater(len(engine._PLAN_CACHE), 512, "more than the old limit of 512 is kept")
        self.assertLessEqual(len(engine._PLAN_CACHE), engine.PLAN_CACHE_MAX)
        self.assertGreaterEqual(len(engine._SPEC_CACHE), TABS * SPECS)

    def test_a_changed_plan_is_noticed_and_only_its_tab_is_read_again(self) -> None:
        self.registry_.refresh_waiting()
        board = self.registry_.board(self.registry_.ids()[3])
        write(board.root / "docs" / "plans" / "2026-01-01-p000.md", PLAN.format(n=0, status="in_progress", mark=" ") + "\nczeka na CP\n")
        calls = []
        real = srv.Board.light_waiting
        with mock.patch.object(srv.Board, "light_waiting", lambda self_, head=None: (calls.append(str(self_.root)), real(self_, head))[1]):
            self.registry_.refresh_waiting()
        self.assertEqual(calls, [str(board.root)])
        self.assertEqual(board.history_count, PLANS - 1)

    def test_the_cache_drops_the_oldest_quarter_not_everything(self) -> None:
        cache: dict = {}
        with mock.patch.object(engine, "PLAN_CACHE_MAX", 8):
            for number in range(8):
                engine._cache_put(cache, f"k{number}", number)
            engine._cache_put(cache, "k8", 8)
        self.assertEqual(list(cache), ["k2", "k3", "k4", "k5", "k6", "k7", "k8"])

    def test_the_hash_cache_of_the_worktrees_drops_the_oldest_quarter(self) -> None:
        dw._cache.clear()
        self.addCleanup(dw._cache.clear)
        files = []
        for number in range(8):
            path = self.tmp / f"f{number}.md"
            write(path, f"x{number}")
            files.append(path)
        with mock.patch.object(dw, "_CACHE_LIMIT", 8):
            for path in files:
                dw._sha(str(path), path.stat())
            self.assertEqual(len(dw._cache), 8)
            extra = self.tmp / "extra.md"
            write(extra, "extra")
            dw._sha(str(extra), extra.stat())
        self.assertEqual(len(dw._cache), 7, "two went, one came: the rest was not read again")
        self.assertIn((str(files[7]), files[7].stat().st_size, files[7].stat().st_mtime_ns), dw._cache)


class WindowsRetries(Tmp):
    def flaky_replace(self, failures: int):
        real, state = os.replace, {"calls": 0}

        def replace(source, target):
            state["calls"] += 1
            if state["calls"] <= failures:
                raise PermissionError(13, "sharing violation")
            return real(source, target)

        return replace, state

    def test_the_state_file_is_replaced_after_two_refusals(self) -> None:
        root = self.tmp / "proj"
        root.mkdir()
        tabs = [{"id": "main", "path": str(root), "token": "t" * 8, "current": True}]
        path = token.write_state(root, 5181, "tok", server_id="a" * 16, kind="project", tabs=tabs)
        self.assertIsNotNone(path)
        replace, state = self.flaky_replace(2)
        tabs.append({"id": "b" * 12, "path": str(root / "w"), "token": "u" * 8})
        with mock.patch.object(prototype_server, "_WINDOWS", True), mock.patch.object(os, "replace", replace):
            self.assertTrue(token.refresh_tabs(root, 5181, tabs, pid=os.getpid()))
        self.assertEqual(state["calls"], 3)
        self.assertEqual([tab["id"] for tab in json.loads(path.read_text(encoding="utf-8"))["tabs"]], ["main", "b" * 12])

    def test_the_pin_is_replaced_after_two_refusals(self) -> None:
        replace, state = self.flaky_replace(2)
        with mock.patch.object(prototype_server, "_WINDOWS", True), mock.patch.object(os, "replace", replace):
            target = token.write_pin("c" * 16, "d" * 12, self.tmp / "x")
        self.assertEqual(state["calls"], 3)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["path"], str(self.tmp / "x"))

    def test_a_refusal_that_lasts_is_raised_for_the_pin(self) -> None:
        replace, _state = self.flaky_replace(10 ** 6)
        with mock.patch.object(prototype_server, "_WINDOWS", True), mock.patch.object(prototype_server, "TRANSIENT_RETRY_SECONDS", 0.05), \
                mock.patch.object(os, "replace", replace), self.assertRaises(PermissionError):
            token.write_pin("c" * 16, "d" * 12, self.tmp / "x")

    def test_a_failed_write_of_the_state_is_repeated_by_the_refresh_of_the_list(self) -> None:
        main = self.tmp / "main"
        registry = self.registry()
        path = token.write_state(main, 5182, "tok", server_id=registry.server_id, kind="project", tabs=registry.tabs_state())
        self.assertIsNotNone(path)
        srv._follow_tabs(registry, main, [5182])
        self.assertFalse(registry.resync(), "in step: nothing to repeat")
        second = self.tmp / "second"
        second.mkdir()
        replace, _state = self.flaky_replace(10 ** 6)
        with mock.patch.object(os, "replace", replace):   # POSIX: no retry, the write fails at once
            registry.register(entry(second, dw.tab_id(str(second))))
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["tabs"]), 1, "control: the failed write left the old file")
        observer = srv.Observer(registry, threading.Event(), project=SimpleNamespace(main_path=str(main)), root=main, manifest={})
        found = dw.Registry()
        found.tabs = [entry(main, registry.default_id, "main"), entry(second, dw.tab_id(str(second)))]
        with mock.patch.object(token, "worktree_settings", return_value={"exclude": [], "inactive_days": 7}), \
                mock.patch.object(dw, "discover", return_value=found):
            observer.refresh()
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["tabs"]), 1, "inside the backoff nothing is retried")
            registry._resync_clock = lambda: time.monotonic() + srv.TabRegistry.RESYNC_BACKOFF_S + 1
            observer.refresh()
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["tabs"]), 2, "the refresh after the backoff wrote it again")
        self.assertFalse(registry.resync(), "and the registry knows the files are in step")


class Backoff(Tmp):
    def test_a_failed_state_write_is_retried_at_most_every_thirty_seconds_and_the_backoff_resets_on_success(self) -> None:
        registry = self.registry()
        calls = []
        registry.add_listener(lambda: calls.append(1))
        now = [1000.0]
        registry._resync_clock = lambda: now[0]
        registry._synced = [{"stale": True}]
        registry.mark_synced(False)
        for moment in (1000.0, 1010.0, 1029.0):
            now[0] = moment
            self.assertFalse(registry.resync(), moment)
        self.assertEqual(calls, [])
        now[0] = 1031.0
        self.assertTrue(registry.resync())
        self.assertEqual(len(calls), 1)
        registry.mark_synced(False)   # the retry failed again: the clock restarts
        now[0] = 1040.0
        self.assertFalse(registry.resync())
        registry.mark_synced(registry.tabs_state())   # a success: no backoff, nothing to repeat
        self.assertIsNone(registry._sync_failed_at)
        self.assertFalse(registry.resync())
        registry._synced = [{"other": 1}]   # a change after a success is written at once
        self.assertTrue(registry.resync())


class WaitingSignature(Tmp):
    """Every file `light_waiting` reads moves the signature; the next refresh (not the 30 s net) recomputes."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "proj"
        write(self.root / "docs" / "plans" / "2026-01-01-a.md",
              "# Plan 1\n\nStatus: in_progress\nPrzebieg: 2026-01-01-r\nSpec: docs/design/x.md\n\n## Progress\n\n- [ ] S1 krok\n")
        write(self.root / "docs" / "specs" / "2026-01-01-s.md", SPEC.format(n=1))
        write(self.root / "docs" / "design" / "x.md", "# Design\n\nStatus: draft\n")
        self.run_dir = self.root / ".skillsync" / "runs" / "2026-01-01-r"
        write(self.run_dir / "checkpoints.md", "RUN=2026-01-01T10:00:00+00:00\n- 11:00 zrobione: S0\n")
        engine._PLAN_CACHE.clear()
        engine._SPEC_CACHE.clear()
        self.board = srv.Board(self.root)
        self.addCleanup(self.board.stop.set)
        self.board.light_waiting()   # parses the plans (the cache the signature reads the `Spec:` of), as the first refresh does

    def sig(self, head=None):
        return self.board.waiting_signature(head)

    def assert_moves(self, change, label, head=None) -> None:
        before = self.sig(head)
        change()
        self.assertNotEqual(before, self.sig(head), label)

    def test_every_input_of_the_light_flag_moves_the_signature(self) -> None:
        def append(path, text):
            def run():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(text)
            return run
        self.assert_moves(append(self.run_dir / "approvals.jsonl", '{"word": "CP"}\n'), "approvals.jsonl created")
        self.assert_moves(append(self.run_dir / "approvals.jsonl", '{"word": "READY"}\n'), "approvals.jsonl grew")
        self.assert_moves(lambda: (self.run_dir / "approvals.jsonl").unlink(), "approvals.jsonl removed")
        self.assert_moves(append(self.run_dir / "checkpoints.md", "- 12:00 zrobione: S9; w toku: —; czeka: CP\n"), "checkpoints.md")
        self.assert_moves(append(self.root / "docs" / "specs" / "2026-01-01-s.md", "more\n"), "spec in docs/specs")
        self.assert_moves(append(self.root / "docs" / "design" / "x.md", "more\n"), "spec outside docs/specs that the plan points to")
        self.assert_moves(append(self.root / "docs" / "plans" / "2026-01-01-a.md", "more\n"), "plan")
        self.assertNotEqual(self.sig("aaaaaaaaaaaa"), self.sig("bbbbbbbbbbbb"), "HEAD")
        self.assertEqual(self.sig("aaaaaaaaaaaa"), self.sig("aaaaaaaaaaaa"), "stable without a change")

    def test_a_dangling_markdown_link_does_not_hide_the_other_files(self) -> None:
        try:
            os.symlink(self.tmp / "nowhere.md", self.root / "docs" / "plans" / "2026-01-01-broken.md")
        except (OSError, NotImplementedError):
            self.skipTest("no permission for symbolic links")
        before = self.sig()
        write(self.root / "docs" / "plans" / "2026-01-02-b.md", PLAN.format(n=2, status="draft", mark=" "))
        self.assertNotEqual(before, self.sig(), "the plan next to the broken link is still in the signature")

    def test_the_cached_flag_is_recomputed_on_a_change_and_by_the_safety_net_only(self) -> None:
        calls = []
        real = srv.Board.light_waiting
        with mock.patch.object(srv.Board, "light_waiting", lambda self_, head=None: (calls.append(1), real(self_, head))[1]):
            self.board.waiting_cached(None, 100.0)
            self.board.waiting_cached(None, 110.0)
            self.assertEqual(len(calls), 1, "unchanged: cached")
            (self.run_dir / "approvals.jsonl").write_text('{"word": "CP"}\n', encoding="utf-8")
            self.board.waiting_cached(None, 111.0)
            self.assertEqual(len(calls), 2, "a changed file: at the next refresh, not after 30 s")
            self.board.waiting_cached(None, 100.0 + self.board.WAIT_RECHECK_S - 1)
            self.assertEqual(len(calls), 2)
            self.board.waiting_cached(None, 111.0 + self.board.WAIT_RECHECK_S + 1)
            self.assertEqual(len(calls), 3, "the safety net")

    def test_a_consent_in_approvals_clears_the_flag_at_the_next_refresh(self) -> None:
        write(self.run_dir / "checkpoints.md", "RUN=2026-01-01T10:00:00+00:00\n- 12:00 zrobione: S9; w toku: —; czeka: CP\n")
        (self.root / "docs" / "specs" / "2026-01-01-s.md").unlink()   # an unreferenced open spec would be the act of the tab instead of the plan
        head = "abcdef012345"
        self.assertTrue(self.board.waiting_cached(head, 0.0))
        write(self.run_dir / "approvals.jsonl", json.dumps({"word": "CP", "at": "2026-01-01T12:01:00+00:00", "sha": head + "0" * 28, "by": "cli", "schema": 1}) + "\n")
        self.assertFalse(self.board.waiting_cached(head, 1.0))


class RegistryPaths(Tmp):
    def test_a_path_in_another_letter_case_is_the_same_tab_where_the_file_system_folds_case(self) -> None:
        registry = self.registry()
        (self.tmp / "Work").mkdir()
        tab = "a" * 12
        with mock.patch.object(dw, "_folds_case", return_value=True):
            registry.register(entry(self.tmp / "Work", tab))
            board, token_before = registry.board(tab), registry.token_for(tab)
            changed = []
            registry.add_listener(lambda: changed.append(1))
            upper = dw.Entry(tab, dw.normalize_path(str(self.tmp / "Work")).replace("Work", "WORK"), "Work", "worktree", False, False, None, None, None)
            registry.register(upper)   # no such directory: only the spelling differs
            self.assertIs(registry.board(tab), board, "the same directory: the board stays")
            self.assertEqual(registry.token_for(tab), token_before)
            self.assertFalse(changed, "no tab was replaced")
        registry2 = self.registry()
        registry2.register(entry(self.tmp / "Work", tab))
        before = registry2.board(tab)
        registry2.register(upper)   # control: where the case counts, another spelling is another directory
        self.assertIsNot(registry2.board(tab), before)

    def test_the_start_directory_tab_is_never_replaced_by_register(self) -> None:
        registry = self.registry()
        other = self.tmp / "other"
        other.mkdir()
        board = registry.board(registry.default_id)
        with self.assertRaises(ValueError):
            registry.register(entry(other, registry.default_id, "main"))
        self.assertIs(registry.board(registry.default_id), board)
        self.assertFalse(board.stop.is_set(), "the board of the start directory was not stopped")

    def test_same_compares_by_the_normalised_key(self) -> None:
        (self.tmp / "Dir").mkdir()
        self.assertTrue(dw._same(str(self.tmp / "Dir"), str(self.tmp / "Dir")))
        with mock.patch.object(dw, "_folds_case", return_value=True):
            self.assertEqual(dw.normalize_key(str(self.tmp / "Dir")), dw.normalize_key(str(self.tmp / "Dir")).lower())
            self.assertEqual(dw.fold_case("Ä"), dw.fold_case("Ä"), "NFC")


class DocNames(Tmp):
    def test_more_than_the_limit_keeps_the_newest(self) -> None:
        folder = self.tmp / "plans"
        for number in range(250):
            write(child(folder, f"2026-{number // 28 + 1:02d}-{number % 28 + 1:02d}-p.md"), "x")
        names = dw._doc_names(str(folder))
        self.assertEqual(len(names), dw.MAX_FILES_PER_DIR)
        self.assertEqual(names[0], max(names))
        self.assertIn("2026-09-26-p.md", names, "the newest are kept")
        self.assertNotIn("2026-01-01-p.md", names, "the oldest are dropped")


class LimitedLogs(Tmp):
    def test_a_registration_that_keeps_failing_is_logged_once(self) -> None:
        registry = self.registry()
        item = entry(self.tmp / "gone", "e" * 12)
        found = dw.Registry()
        found.tabs = [entry(self.tmp / "main", registry.default_id, "main"), item]
        lines = []
        with mock.patch.object(srv, "log", lines.append), mock.patch.object(registry, "register", side_effect=ValueError("boom")):
            for _ in range(5):
                registry.reconcile(found)
        self.assertEqual(sorted(line for line in lines if "boom" in line), ["zakładki: gone: boom", "zakładki: main: boom"], "one line per tab, not one per refresh")


class ShutStartTab(Tmp):
    def test_an_excluded_start_directory_is_listed_nowhere(self) -> None:
        registry = self.registry(("main", "side"))
        self.assertEqual(len(registry.summary()), 2)
        registry._shut = {registry.default_id}
        self.assertEqual([row["id"] for row in registry.summary()], [i for i in registry.ids() if i != registry.default_id])
        self.assertEqual([row["id"] for row in registry.worktrees()], [row["id"] for row in registry.summary()])
        self.assertEqual([row["id"] for row in registry.tabs_state()], [row["id"] for row in registry.summary()])
        registry._shut = set()
        self.assertEqual(len(registry.tabs_state()), 2)


class HistoryCache(Tmp):
    def test_the_manifest_error_of_a_write_is_logged_once(self) -> None:
        main = self.tmp / "main"
        (main / ".github").mkdir(parents=True)
        (main / ".github" / ".skillsync.yaml").write_text("x: 1\n", encoding="utf-8")
        side = self.tmp / "side"
        side.mkdir()
        project = SimpleNamespace(main_path=str(main), common_dir=str(main / ".git"))
        registry = self.registry(("main",), project=project)
        item = entry(side, "f" * 12)
        registry.register(item)
        lines = []
        with mock.patch.object(srv, "log", lines.append), mock.patch.object(dw, "revalidate_entry", return_value=True), \
                mock.patch.object(token, "worktree_settings", side_effect=ValueError("bad yaml")):
            for _ in range(4):
                self.assertFalse(registry.revalidate_for_write(item.id))
        self.assertEqual(len([line for line in lines if "bad yaml" in line]), 1)


if __name__ == "__main__":
    unittest.main()
