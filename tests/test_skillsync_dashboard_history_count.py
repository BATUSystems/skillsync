"""CORE-1180 M-6: pole `history` w `/api/tabs` (liczba planow `done` i `archived` zakladki, licznik paska Historii).

Licznik liczy sie przy odswiezeniu listy (`refresh_waiting`) z planow, ktore `light_waiting` i tak wylicza: zadnego dodatkowego otwarcia pliku."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard as engine  # noqa: E402
from tests.test_skillsync_dashboard_history import DONE, HistoryCase, ROUTE, status_of  # noqa: E402


class HistoryCountCase(HistoryCase):
    def counts(self) -> dict:
        code, data = self.get_json("/api/tabs")
        self.assertEqual(code, 200, data)
        return {tab["id"]: tab["history"] for tab in data["tabs"]}

    def test_zero_until_the_first_refresh_then_the_number_of_finished_plans(self) -> None:
        self.assertEqual(set(self.counts().values()), {0})
        self.reg.refresh_waiting()
        self.assertEqual(self.counts(), {self.id_a: 1, self.id_b: 1})   # the fixture has one `done` plan and one in progress per tab

    def test_the_count_follows_an_archive_and_a_new_finished_plan(self) -> None:
        self.reg.refresh_waiting()
        template = (self.root_a / DONE).read_text(encoding="utf-8")
        (self.root_a / "docs/plans/2026-02-02-nowy.md").write_text(template, encoding="utf-8")
        self.reg.refresh_waiting()
        self.assertEqual(self.counts(), {self.id_a: 2, self.id_b: 1})
        self.assertEqual(self.archive_a()[0], 200)   # `archived` is counted as well: the number is of the whole History
        self.reg.refresh_waiting()
        self.assertEqual(self.counts()[self.id_a], 2)
        self.assertEqual(status_of(self.root_a, DONE), "archived")

    def test_a_tab_that_left_has_no_count(self) -> None:
        self.reg.refresh_waiting()
        self.reg.deregister(self.id_b)
        self.reg.refresh_waiting()
        self.assertEqual(self.counts(), {self.id_a: 1})
        self.assertEqual(self.reg._history.keys(), {self.id_a})

    def test_reading_the_tabs_opens_no_plan_file(self) -> None:
        self.reg.refresh_waiting()
        with mock.patch.object(engine, "list_plans", side_effect=AssertionError("list_plans in a read")), \
                mock.patch.object(engine, "_plan_content", side_effect=AssertionError("plan opened in a read")):
            self.counts()
            self.get_json("/api/tabs")

    def test_a_refresh_lists_the_plans_of_a_tab_once(self) -> None:
        real = engine.list_plans
        calls = []
        with mock.patch.object(engine, "list_plans", lambda root: (calls.append(str(root)), real(root))[1]):
            self.reg.refresh_waiting()
        self.assertEqual(len(calls), len(self.reg.ids()), calls)   # the counter costs no second listing


if __name__ == "__main__":
    unittest.main()
