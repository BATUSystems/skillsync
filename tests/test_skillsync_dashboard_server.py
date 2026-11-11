"""Testy serwera dashboard (`skillsync dashboard serve`, skillsync_workflow/dashboard_http.py) i silnika, na którym stoi
(skillsync_workflow/dashboard.py): python3 -m unittest tests.test_skillsync_dashboard_server

Testy HTTP startują serwer na 127.0.0.1 na wolnym porcie z zakresu 5100–5199 (nigdy 5140), wybieranym dynamicznie, i zamykają
go po teście. Assety i descriptions.json pochodzą z warstwy `library/` repo przez `LayerSet.skill_files`, jak w komendzie.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest import mock
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "library" / "skills" / "dashboard"
CLI = ROOT / "skillsync"
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow.common import as_layerset  # noqa: E402

FIX = HERE / "fixtures" / "dashboard_server"
ASSETS, DESCRIPTIONS = srv.skill_assets(as_layerset(ROOT / "library"))


def free_port() -> int:
    """Wolny port z zakresu 5100–5199 poza 5140 (domyślnym portem dashboardu)."""
    for port in range(5101, 5200):
        if port == 5140:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("brak wolnego portu 5100–5199")


PLAN = "docs/plans/2026-01-10-demo-etap-1.md"
SPEC = "docs/specs/demo-etap-1.md"
RUN = ".skillsync/runs/2026-01-10-demo-etap-1"


def make_project(base: Path) -> Path:
    root = base / "proj"
    for rel, src in [
        (PLAN, "plan.md"),
        (SPEC, "spec.md"),
        (f"{RUN}/checkpoints.md", "checkpoints.md"),
        ("docs/plans/2025-12-01-starszy.md", "plan-done.md"),
        ("docs/plans/2025-11-01-szkic.md", "plan-draft.md"),
    ]:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / src, dst)
    (root / f"{RUN}/S1-implementer.md").write_text("# Raport S1\n\nStatus: PASS\n", encoding="utf-8")
    (root / f"{RUN}/spec-review-r1-decisions.md").write_text("# Review\n", encoding="utf-8")
    (root / f"{RUN}/notes.txt").write_text("nie md\n", encoding="utf-8")
    (root / ".skillsync/config.md").write_text("# poza runami\n", encoding="utf-8")
    (root / ".env").write_text("NOT_A_SECRET=1\n", encoding="utf-8")
    (root / "docs/secret.md").write_text("# poza whitelistą\n", encoding="utf-8")
    (root / ".git/refs/heads").mkdir(parents=True)
    (root / ".git/HEAD").write_text("ref: refs/heads/feature/demo\n", encoding="utf-8")
    (root / ".git/refs/heads/feature").mkdir()
    (root / ".git/refs/heads/feature/demo").write_text("abc1234def5678\n", encoding="utf-8")
    return root


def install_fake_cli(case: unittest.TestCase) -> None:
    """Pułapka `skillsync` w PATH: każde wywołanie trafia do `case.calls` i kończy się błędem. Serwer zapisuje zgody
    in-process (`status.set_doc_status`, `status.record_approval`), więc `case.calls` ma zostać pusty."""
    case.bin = case.tmp / "bin"
    case.bin.mkdir()
    case.calls = case.tmp / "calls.txt"
    fake = case.bin / "skillsync"
    fake.write_text("#!/bin/sh\n"
                    f"echo \"$@\" >> '{case.calls}'\n"
                    "echo 'pułapka: serwer nie woła CLI' >&2; exit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = f"{case.bin}{os.pathsep}{old_path}"
    case.addCleanup(os.environ.__setitem__, "PATH", old_path)


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="status-board-"))
        self.root = make_project(self.tmp)
        self.board = srv.Board(self.root, prototype_port=1)  # port 1: prototyp zawsze „wyłączony”
        self.board.prototype_acceptance = lambda slug: None  # bez wywołania CLI skillsync w testach

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class ParsingTests(unittest.TestCase):
    def test_header_fields(self) -> None:
        text = (FIX / "plan.md").read_text(encoding="utf-8")
        title, fields = engine.parse_header(text)
        self.assertEqual(title, "Plan: Demo, etap 1 — „Moduł przykładowy”")
        self.assertEqual(engine.doc_status(text)[0], "in_progress")  # status.py `_status_fields` (K3), nie własny parser
        self.assertEqual(engine.first_token(fields["spec"]), SPEC)
        self.assertEqual(engine.first_token(fields["przebieg"]), "2026-01-10-demo-etap-1")
        self.assertTrue(fields["spec-sha"].startswith("0123456789"))

    def test_progress_phases_and_steps(self) -> None:
        phases = engine.parse_progress((FIX / "plan.md").read_text(encoding="utf-8"))
        self.assertEqual([p["name"] for p in phases], ["Faza 1: Fundament", "Faza 2: API", "Faza 3: Domknięcie"])
        steps = [s for p in phases for s in p["steps"]]
        self.assertEqual([s["id"] for s in steps], ["S0", "S1", "S2a", "S2b", "S3a", "S3b", "S4"])
        self.assertEqual(steps[0]["sha"], "abc1234")
        self.assertEqual(steps[0]["title"], "Commit planu, baseline testów")
        self.assertIsNone(steps[-1]["sha"])
        self.assertEqual(steps[-1]["note"], "n/d (raport w przebiegu)")
        self.assertNotIn("S99", [s["id"] for s in steps])

    def test_progress_ids_with_dash_and_block_names(self) -> None:
        phases = engine.parse_progress((FIX / "plan-done.md").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in phases[0]["steps"]], ["A0", "A-1"])
        self.assertEqual(phases[0]["steps"][1]["sha"], "18848fd")

    def test_progress_without_phase_header(self) -> None:
        phases = engine.parse_progress((FIX / "plan-draft.md").read_text(encoding="utf-8"))
        self.assertEqual(phases[0]["name"], "Kroki")

    def test_checkpoints(self) -> None:
        start, items = engine.parse_checkpoints((FIX / "checkpoints.md").read_text(encoding="utf-8"))
        self.assertEqual(start, "2026-01-10T10:00:00+01:00")
        self.assertEqual(len(items), 3)
        self.assertTrue(items[-1]["text"].endswith("kontynuacja linii checkpointu"))
        self.assertIsNotNone(engine.parse_iso(start))

    def test_in_progress_ids_only_leading(self) -> None:
        text = ("checkpoint 1: zrobione S1, S2a/S2c; w toku: S2b/S2d (test-writer), S3a (implementer opus), "
                "S5a, szczebel 3 S1 na migawce, baseline; ryzyko: S9 też")
        self.assertEqual(engine.ids_in_progress(text), ["S2b", "S2d", "S3a", "S5a"])
        self.assertEqual(engine.ids_reported_done(text), ["S1", "S2a", "S2c"])
        self.assertIsNone(engine.ids_in_progress("restart sesji: przerwane S2b"))
        self.assertEqual(engine.ids_in_progress("W toku S4 i S5"), ["S4", "S5"])


class ProgressTests(unittest.TestCase):
    def phases(self):
        return engine.parse_progress((FIX / "plan.md").read_text(encoding="utf-8"))

    def test_percent_and_current_from_checkpoint(self) -> None:
        p = engine.build_progress(self.phases(), ["S2b", "S3a", "S1"], "in_progress", ["S2a"])
        self.assertEqual((p["done"], p["total"], p["percent"]), (2, 7, 29))
        self.assertEqual(p["active"], ["S2b", "S3a"])  # S1 już [x] — nie jest „w toku”
        self.assertEqual(p["current"], {"index": 4, "id": "S2b", "title": "Testy jednostkowe reguł"})
        self.assertEqual(p["parallel"], 1)
        self.assertEqual(p["activeSource"], "checkpoint")
        states = {s["id"]: s["state"] for ph in p["phases"] for s in ph["steps"]}
        self.assertEqual(states["S2a"], "reported")
        self.assertEqual(states["S3b"], "planned")
        self.assertEqual([ph["percent"] for ph in p["phases"]], [50, 0, 0])
        self.assertEqual(p["phases"][0]["label"], "Faza 1")
        self.assertEqual(p["phases"][0]["short"], "Fundament")

    def test_pending_segment_from_checkpoint(self) -> None:
        p = engine.build_progress(self.phases(), ["S3a"], "in_progress", ["S2a", "S2b", "S0", "S9"])
        # S0 już [x], S9 nie istnieje — liczą się tylko S2a i S2b; % zostaje po twardym [x]
        self.assertEqual((p["percent"], p["pending"], p["pendingPercent"]), (29, 2, 28))
        self.assertEqual(p["percent"] + p["pendingPercent"], round(100 * 4 / 7))
        self.assertEqual([(ph["pending"], ph["pendingPercent"]) for ph in p["phases"]], [(2, 50), (0, 0), (0, 0)])
        none = engine.build_progress(self.phases(), None, "in_progress")
        self.assertEqual((none["pending"], none["pendingPercent"]), (0, 0))
        self.assertEqual(engine._pending_percent(0, 0, 0), 0)

    def test_fallback_first_unchecked(self) -> None:
        p = engine.build_progress(self.phases(), None, "in_progress")
        self.assertEqual(p["active"], ["S2a"])
        self.assertEqual(p["activeSource"], "first-unchecked")

    def test_no_active_when_not_in_progress(self) -> None:
        p = engine.build_progress(self.phases(), None, "approved")
        self.assertEqual(p["active"], [])
        self.assertIsNone(p["current"])

    def test_empty_plan(self) -> None:
        p = engine.build_progress([], None, "draft")
        self.assertEqual((p["total"], p["percent"]), (0, 0))


class ActTests(unittest.TestCase):
    def acts(self, spec_status, plan_status, spec_exists=True, plan=True):
        spec = {"exists": spec_exists, "status": spec_status, "path": SPEC}
        plan_info = {"status": plan_status, "path": PLAN} if plan else None
        progress = {"total": 4, "done": 1, "percent": 25, "phases": [], "current": None}
        return {a["key"]: a["state"] for a in engine.compute_acts(spec, plan_info, progress, [])}

    def test_states(self) -> None:
        self.assertEqual(self.acts("draft", None, plan=False), {"spec": "active", "plan": "planned", "real": "planned"})
        self.assertEqual(self.acts("approved", None, plan=False), {"spec": "done", "plan": "active", "real": "planned"})
        self.assertEqual(self.acts("approved", "draft"), {"spec": "done", "plan": "active", "real": "planned"})
        self.assertEqual(self.acts("approved", "approved"), {"spec": "done", "plan": "done", "real": "planned"})
        self.assertEqual(self.acts("approved", "in_progress"), {"spec": "done", "plan": "done", "real": "active"})
        self.assertEqual(self.acts("implemented", "done"), {"spec": "done", "plan": "done", "real": "done"})
        self.assertEqual(self.acts(None, None, spec_exists=False, plan=False)["spec"], "planned")

    def test_stages_from_run_files(self) -> None:
        spec = {"exists": True, "status": "approved", "path": SPEC}
        acts = engine.compute_acts(spec, {"status": "in_progress", "path": PLAN},
                               {"total": 1, "phases": [], "current": None},
                               ["x/spec-review-r1.md", "x/spec-review-2-r1.md", "x/pre-implement.md"])
        stages = {s["label"]: s["done"] for s in acts[0]["stages"]}
        self.assertTrue(stages["Review ×2"])
        plan_stages = {s["label"]: s["done"] for s in acts[1]["stages"]}
        self.assertTrue(plan_stages["Pre-implement"])
        self.assertFalse(plan_stages["Review"])
        self.assertTrue(plan_stages["GO implement"])


class SpecActTests(ProjectCase):
    """Akt I: spec bez planu (plan powstaje dopiero przy „GO plan”)."""
    SPEC2 = "docs/specs/demo-etap-2.md"
    RUN2 = ".skillsync/runs/2026-01-20-demo-etap-2"

    def add_spec_act(self, status: str = "draft", newer: bool = True) -> None:
        spec = self.root / self.SPEC2
        spec.write_text(f"# Spec: Demo, etap 2\n\nStatus: {status}\nPlan: docs/plans/2026-01-20-demo-etap-2.md\n\n## Problem\n",
                        encoding="utf-8")
        run = self.root / self.RUN2
        run.mkdir(parents=True)
        (run / "checkpoints.md").write_text("RUN=2026-01-20T09:00:00+01:00\n- 09:10 start aktu I; w toku: spec\n",
                                            encoding="utf-8")
        (run / "spec-review-r1.md").write_text("# Review speca\n", encoding="utf-8")
        proto = self.root / "docs/prototypes/demo-etap-2"
        proto.mkdir(parents=True)
        (proto / "prototype.json").write_text(json.dumps({"slug": "demo-etap-2", "requirements": self.SPEC2}),
                                              encoding="utf-8")
        plan_mtime = (self.root / PLAN).stat().st_mtime
        os.utime(spec, (plan_mtime + (100 if newer else -100),) * 2)

    def test_newer_spec_without_plan_shows_act_one(self) -> None:
        self.add_spec_act()
        self.board.prototype_acceptance = lambda slug: {"screens": 5, "accepted": 3, "stale": 0}
        s = self.board.status()
        self.assertEqual((s["mode"], s["plan"]), ("spec", None))
        self.assertEqual((s["spec"]["path"], s["spec"]["status"]), (self.SPEC2, "draft"))
        self.assertEqual(s["run"]["id"], "2026-01-20-demo-etap-2")  # katalog o nazwie planu z linii `Plan:`
        self.assertEqual(s["run"]["checkpoints"][0]["time"], "09:10")
        self.assertEqual(s["prototype"]["slug"], "demo-etap-2")  # z prototype.json → requirements
        acts = {a["key"]: a for a in s["acts"]}
        self.assertEqual([acts[k]["state"] for k in ("spec", "plan", "real")], ["active", "planned", "planned"])
        self.assertEqual(acts["plan"]["detail"], "brak planu (czeka na GO plan)")
        stages = {st["label"]: st["done"] for st in acts["spec"]["stages"]}
        self.assertEqual((stages["Review"], stages["Prototyp 3/5"]), (True, False))
        self.assertEqual(s["docs"][0]["items"][0]["path"], self.SPEC2)
        json.dumps(s)

    def test_approved_spec_waits_for_plan(self) -> None:
        self.add_spec_act("approved")
        acts = {a["key"]: a for a in self.board.status()["acts"]}
        self.assertEqual((acts["spec"]["state"], acts["plan"]["state"]), ("done", "active"))
        self.assertIn("plan w przygotowaniu", acts["plan"]["detail"])

    def test_plan_newer_than_spec_wins(self) -> None:
        self.add_spec_act(newer=False)
        self.assertEqual(self.board.status()["mode"], "plan")

    def test_spec_referenced_by_plan_is_not_act_one(self) -> None:
        self.add_spec_act()
        (self.root / "docs/plans/2026-01-20-demo-etap-2.md").write_text(
            f"# Plan: etap 2\n\nStatus: draft\nSpec: {self.SPEC2}\n", encoding="utf-8")
        s = self.board.status()
        self.assertEqual((s["mode"], s["plan"]["path"]), ("plan", PLAN))  # in_progress wygrywa z nowym draftem

    def test_explicit_plan_ignores_act_one(self) -> None:
        self.add_spec_act()
        self.assertEqual(self.board.status(PLAN)["plan"]["path"], PLAN)

    def test_run_without_spec_and_plan_shows_act_one_in_preparation(self) -> None:
        run = self.root / ".skillsync/runs/2026-01-25-demo-etap-3"
        run.mkdir(parents=True)
        cp = run / "checkpoints.md"
        cp.write_text("RUN=2026-01-25T15:00:00+01:00\n- 15:14 start: etap 3 akt I (spec); w toku: spec; czeka: odpowiedź Q1\n",
                      encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(cp, (later, later))
        s = self.board.status()
        self.assertEqual((s["mode"], s["plan"], s["run"]["id"]), ("run", None, "2026-01-25-demo-etap-3"))
        self.assertEqual(s["run"]["checkpoints"][0]["time"], "15:14")
        self.assertEqual([w["what"] for w in s["waiting"]], ["odpowiedź Q1"])
        acts = {a["key"]: a for a in s["acts"]}
        self.assertEqual((acts["spec"]["state"], acts["spec"]["detail"]), ("active", "specyfikacja w przygotowaniu"))
        self.assertEqual(acts["plan"]["detail"], "brak planu (czeka na GO plan)")
        # przebieg wskazany przez plan (Przebieg:) albo starszy niż plan nie przejmuje widoku
        os.utime(cp, (later - 200, later - 200))
        self.assertEqual(self.board.status()["mode"], "plan")
        os.utime(cp, (later, later))
        (self.root / "docs/plans/2026-01-25-demo-etap-3.md").write_text(
            "# Plan: etap 3\n\nStatus: draft\nPrzebieg: 2026-01-25-demo-etap-3\n", encoding="utf-8")
        self.assertEqual(self.board.status()["mode"], "plan")
        (self.root / "docs/plans/2026-01-25-demo-etap-3.md").unlink()
        # gdy spec się pojawi, działa dotychczasowy akt I ze specem
        spec = self.root / "docs/specs/demo-etap-3.md"
        spec.write_text("# Spec: etap 3\n\nStatus: draft\n", encoding="utf-8")
        os.utime(spec, (later + 5, later + 5))
        s = self.board.status()
        self.assertEqual((s["mode"], s["spec"]["path"], s["run"]["id"]), ("spec", "docs/specs/demo-etap-3.md", "2026-01-25-demo-etap-3"))

    def test_run_from_spec_slug_and_explicit_line(self) -> None:
        runs = self.root / ".skillsync/runs"
        (runs / "2026-01-21-demo-etap-2").mkdir(parents=True)
        self.assertEqual(engine.spec_run_id(self.root, self.SPEC2, {}), "2026-01-21-demo-etap-2")
        self.assertEqual(engine.spec_run_id(self.root, self.SPEC2, {"przebieg": "2026-01-10-demo-etap-1"}),
                         "2026-01-10-demo-etap-1")
        self.assertIsNone(engine.spec_run_id(self.root, "docs/specs/inny.md", {"przebieg": "../../etc"}))


class SubstepTests(unittest.TestCase):
    """Podkroki faz F1.1…F1.k: liczone osobno, „w toku: F1” wskazuje pierwszy niezaznaczony podkrok; stare id bez zmian."""
    PLAN = """# Plan

## Progress
### Faza 0: Start
- [x] F0.1 Commit planu — 0c7620d
- [x] F0.2 Baseline — 1772/1772
### Faza 1: Zmiany
- [ ] F1.1 Kod BE ‖ FE
- [ ] F1.2 Szczebel 3 i commit
- [ ] F1.10 Dziesiąty podkrok
"""

    def test_substeps_are_steps_and_phase_ids_point_to_the_first_open_one(self) -> None:
        phases = engine.parse_progress(self.PLAN)
        self.assertEqual([[s["id"] for s in p["steps"]] for p in phases], [["F0.1", "F0.2"], ["F1.1", "F1.2", "F1.10"]])
        prog = engine.build_progress(phases, ["F1"], "in_progress")
        self.assertEqual((prog["done"], prog["total"], prog["active"], prog["current"]["id"]), (2, 5, ["F1.1"], "F1.1"))
        self.assertEqual([(p["done"], p["total"], p["state"]) for p in prog["phases"]], [(2, 2, "done"), (0, 3, "active")])
        self.assertEqual(engine.build_progress(phases, ["F1.2"], "in_progress")["active"], ["F1.2"])
        self.assertEqual(engine.ids_in_progress("zrobione: F0.2; w toku: F1.1 i F1.2"), ["F1.1", "F1.2"])

    def test_old_ids_without_a_dot_behave_as_before(self) -> None:
        phases = engine.parse_progress("## Progress\n- [x] S1 A\n- [ ] S2a B\n- [ ] A-1 C\n")
        self.assertEqual([s["id"] for s in phases[0]["steps"]], ["S1", "S2a", "A-1"])
        self.assertEqual(engine.build_progress(phases, ["S2a"], "in_progress")["active"], ["S2a"])


class WaitingTests(ProjectCase):
    """Baner „sesja czeka na Ciebie”: znacznik `czeka:` w checkpoincie i fakty z plików."""
    CP = f"{RUN}/checkpoints.md"

    def add_checkpoint(self, line: str) -> None:
        with (self.root / self.CP).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def test_marker_parsing(self) -> None:
        self.assertEqual(engine.wait_marker("zrobione: S1; w toku: —; czeka: GO implement"), "GO implement")
        self.assertEqual(engine.wait_marker("ryzyko: brak; czeka: akceptacja prototypu demo-etap-1."), "akceptacja prototypu demo-etap-1")
        for text in ("czeka: —", "czeka: brak", "czeka na CP", "w toku: S2", "ryzyko: czeka-y"):
            self.assertIsNone(engine.wait_marker(text), text)

    def test_item_actions_and_links(self) -> None:
        ctx = {"spec": SPEC, "plan": PLAN, "slug": "demo-etap-1"}
        proto = engine.wait_item("akceptacja prototypu demo-etap-2 (3/5)", "checkpoint", "10:50", ctx)
        self.assertEqual(proto["link"], {"kind": "prototype", "slug": "demo-etap-2"})
        self.assertEqual(engine.wait_item("GO implement", "plan", None, ctx)["link"], {"kind": "doc", "path": PLAN})
        self.assertEqual(engine.wait_item("GO plan", "spec", None, ctx)["link"], {"kind": "doc", "path": SPEC})
        self.assertIn("pytania", engine.wait_item("odpowiedź Q1–Q3", "checkpoint", None, ctx)["action"])
        self.assertIn("„CP”", engine.wait_item("CP", "checkpoint", None, ctx)["action"])
        self.assertIn("„READY”", engine.wait_item("READY", "checkpoint", None, ctx)["action"])
        self.assertIn("retrospektywy", engine.wait_item("TAK/NIE run-1/P2", "checkpoint", None, ctx)["action"])

    def test_banner_from_newest_checkpoint_until_next_one(self) -> None:
        self.assertEqual(self.board.status()["waiting"], [])
        self.add_checkpoint("- 10:50 zrobione: S2b; w toku: —; czeka: CP")
        w = self.board.status()["waiting"]
        self.assertEqual([(i["what"], i["source"], i["since"]) for i in w], [("CP", "checkpoint", "10:50")])
        self.add_checkpoint("- 10:55 start S4; w toku: S4")
        self.assertEqual(self.board.status()["waiting"], [])

    def test_draft_plan_waiting_for_go_implement(self) -> None:
        plan = self.root / "docs/plans/2025-11-01-szkic.md"
        plan.write_text("# Plan: szkic\n\nStatus: draft (czeka na GO implement)\n", encoding="utf-8")
        w = self.board.status("docs/plans/2025-11-01-szkic.md")["waiting"]
        self.assertEqual([(i["what"], i["source"]) for i in w], [("GO implement", "plan")])
        self.assertEqual(w[0]["link"], {"kind": "doc", "path": "docs/plans/2025-11-01-szkic.md"})

    def test_silence_hint_only_for_a_plan_in_progress_without_wait_marker(self) -> None:
        s = self.board.status()
        self.assertEqual(s["silence"]["thresholdMin"], engine.SILENCE_MIN)
        self.assertEqual(s["silence"]["since"], s["run"]["lastReportAt"])
        self.add_checkpoint("- 10:50 zrobione: S2b; w toku: —; czeka: CP")
        self.assertIsNone(self.board.status()["silence"])  # sesja czeka na człowieka: cisza oczekiwana
        plan = self.root / "docs/plans/2025-11-01-szkic.md"
        self.assertIsNone(self.board.status("docs/plans/2025-11-01-szkic.md")["silence"])  # plan draft

    def test_stale_go_note_on_an_approved_file_is_not_a_banner(self) -> None:
        plan = self.root / "docs/plans/2025-11-01-szkic.md"
        plan.write_text("# Plan: szkic\n\nStatus: approved (czeka na GO implement)\n", encoding="utf-8")
        self.assertEqual(self.board.status("docs/plans/2025-11-01-szkic.md")["waiting"], [])
        plan.write_text("# Plan: szkic\n\nStatus: approved (czeka na spec)\n", encoding="utf-8")
        self.assertEqual([w["what"] for w in self.board.status("docs/plans/2025-11-01-szkic.md")["waiting"]], ["spec"])

    def test_prototype_not_accepted_before_go_implement(self) -> None:
        self.board.prototype_acceptance = lambda slug: {"screens": 5, "accepted": 3, "stale": 0}
        self.assertEqual(self.board.status()["waiting"], [])  # plan in_progress: po bramce prototypu
        plan = self.root / "docs/plans/2025-11-01-szkic.md"
        plan.write_text(f"# Plan: szkic\n\nStatus: draft\nSpec: {SPEC}\nPrototyp: docs/prototypes/demo-etap-1\n", encoding="utf-8")
        w = self.board.status("docs/plans/2025-11-01-szkic.md")["waiting"]
        self.assertEqual([i["source"] for i in w], ["prototype"])
        self.assertIn("3/5", w[0]["what"])


class GitLinkTests(ProjectCase):
    def test_remote_web_hosts(self) -> None:
        az = engine.remote_web("git@ssh.dev.azure.com:v3/org/Proj/repo")
        self.assertEqual(az["repo"], "https://dev.azure.com/org/Proj/_git/repo")
        self.assertEqual(engine.remote_web("https://dev.azure.com/org/Proj/_git/repo/")["repo"], az["repo"])
        self.assertEqual(engine.remote_web("git@github.com:Org/repo.git")["repo"], "https://github.com/Org/repo")
        self.assertEqual(engine.remote_web("https://github.com/Org/repo")["host"], "github")
        for bad in ("https://gitlab.com/a/b", "git@ssh.dev.azure.com:v3/o/p/r\"><x", "", None, "file:///tmp/repo"):
            self.assertIsNone(engine.remote_web(bad), bad)

    def write_origin(self, url: str) -> None:
        (self.root / ".git/config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n', encoding="utf-8")

    def test_branch_link_only_when_pushed(self) -> None:
        self.write_origin("git@ssh.dev.azure.com:v3/org/Proj/repo")
        g = engine.read_git(self.root)
        self.assertEqual((g["remote"]["host"], g["branchPushed"]), ("azure", False))
        self.assertEqual(g["branchUrl"], "https://dev.azure.com/org/Proj/_git/repo?version=GBfeature%2Fdemo")
        (self.root / ".git/packed-refs").write_text("abc1234def5678 refs/remotes/origin/feature/demo\n", encoding="utf-8")
        self.assertTrue(engine.read_git(self.root)["branchPushed"])
        self.write_origin("git@github.com:Org/repo.git")
        self.assertEqual(engine.read_git(self.root)["branchUrl"], "https://github.com/Org/repo/tree/feature/demo")

    def test_plan_pr_line(self) -> None:
        plan = self.root / PLAN
        plan.write_text(plan.read_text(encoding="utf-8").replace("## Progress", "## Progress\nPR: #12 (link: https://example.com/pr/12)", 1),
                        encoding="utf-8")
        self.assertEqual(self.board.status()["plan"]["pr"], {"number": 12, "url": "https://example.com/pr/12"})
        plan.write_text(plan.read_text(encoding="utf-8").replace("https://example.com/pr/12", "javascript:alert(1)"), encoding="utf-8")
        self.assertIsNone(self.board.status()["plan"]["pr"])


class BoardTests(ProjectCase):
    def test_plan_choice_prefers_in_progress(self) -> None:
        self.assertEqual(engine.choose_plan(engine.list_plans(self.root))["path"], PLAN)

    def test_status_live_project(self) -> None:
        s = self.board.status()
        self.assertEqual(s["plan"]["path"], PLAN)
        self.assertEqual(s["spec"]["status"], "approved")
        self.assertEqual(s["run"]["startIso"], "2026-01-10T10:00:00+01:00")
        self.assertEqual(s["run"]["checkpoints"][0]["time"], "10:40")  # najnowszy pierwszy
        self.assertEqual(s["run"]["inProgressFrom"], "10:25")
        self.assertEqual(s["progress"]["active"], ["S2b", "S3a"])
        self.assertEqual(s["progress"]["percent"], 29)
        self.assertEqual([a["state"] for a in s["acts"]], ["done", "done", "active"])
        self.assertEqual((s["git"]["branch"], s["git"]["head"], s["git"]["branchUrl"]), ("feature/demo", "abc1234def56", None))
        self.assertEqual(s["prototype"]["slug"], "demo-etap-1")
        self.assertFalse(s["prototype"]["up"])
        groups = {g["key"]: [i["path"] for i in g["items"]] for g in s["docs"]}
        self.assertEqual(groups["task"], [SPEC, PLAN])
        self.assertIn(f"{RUN}/checkpoints.md", groups["run"])
        self.assertNotIn(f"{RUN}/notes.txt", groups["run"])
        json.dumps(s)  # serializowalny

    def test_explicit_plan_and_unknown_plan(self) -> None:
        self.assertEqual(self.board.status("docs/plans/2025-12-01-starszy.md")["plan"]["status"], "done")
        self.assertEqual(self.board.status("../../etc/passwd.md")["plan"]["path"], PLAN)

    def test_whitelist(self) -> None:
        ok = [SPEC, PLAN, f"{RUN}/checkpoints.md", f"{RUN}/S1-implementer.md"]
        for rel in ok:
            self.assertIsNotNone(engine.resolve_doc(self.root, rel), rel)
        bad = [
            ".env", "docs/secret.md", ".skillsync/config.md", f"{RUN}/notes.txt",
            "../proj/" + SPEC, "docs/specs/../../.env", "/etc/passwd", "docs/specs/../secret.md",
            str(self.root / SPEC), "docs\\specs\\demo-etap-1.md", "docs/specs/nie-ma.md", "", None,
            "docs/specs//demo-etap-1.md", "docs/specs/./demo-etap-1.md", ".git/HEAD",
        ]
        for rel in bad:
            self.assertIsNone(engine.resolve_doc(self.root, rel), rel)

    def test_symlink_escape_rejected(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("# poza projektem\n", encoding="utf-8")
        link = self.root / "docs/specs/link.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("brak symlinków")
        self.assertIsNone(engine.resolve_doc(self.root, "docs/specs/link.md"))

    def test_doc_render_escapes(self) -> None:
        doc = self.board.doc(SPEC)
        h = doc["html"]
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("javascript:", h.split("md-link-off")[0])
        self.assertIn('class="md-check is-done"', h)
        self.assertIn("<strong>pogrubienie</strong>", h)
        self.assertIn('<td class="al-right">2</td>', h)
        self.assertIn("<td>a | b</td>", h)
        self.assertIn("<ul><li>zagnieżdżone</li></ul>", h)

    def test_doc_links(self) -> None:
        h = self.board.doc(PLAN)["html"]
        self.assertIn(f'data-doc="{SPEC}"', h)
        self.assertIn("<code>kodem</code>", h)
        self.assertIn("Status: in_progress<br>", h)  # pola nagłówka w osobnych liniach

    def test_watcher_detects_change(self) -> None:
        self.assertFalse(self.board.check_once())
        cp = self.root / RUN / "checkpoints.md"
        with open(cp, "a", encoding="utf-8") as fh:
            fh.write("- 11:00 zrobione S2b; w toku: S3b\n")
        os.utime(cp, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
        self.assertTrue(self.board.check_once())
        self.assertEqual(self.board.version, 1)
        self.assertEqual(self.board.status()["progress"]["active"], ["S3b"])

    def test_port_validation(self) -> None:
        for port in (4444, 4448, 9222, 9333, 5099, 5200, 8080):
            with self.assertRaises(SystemExit):
                srv.validate_port(port)
        srv.validate_port(5140)
        srv.validate_port(5142)


def spy_core(case: unittest.TestCase) -> None:
    """`case.set_doc` i `case.record`: szpiegi funkcji rdzenia, które serwer woła in-process (wywołują oryginał)."""
    for name, attr in (("set_doc_status", "set_doc"), ("record_approval", "record")):
        patcher = mock.patch.object(srv.status_core, name, wraps=getattr(srv.status_core, name))
        setattr(case, attr, patcher.start())
        case.addCleanup(patcher.stop)


def cli_calls(case: unittest.TestCase) -> list[str]:
    return case.calls.read_text(encoding="utf-8").splitlines() if case.calls.exists() else []


class ApprovalTests(ProjectCase):
    """Przyciski zgód: „GO plan” i „GO implement” in-process (`status.set_doc_status` z sha pokazanej wersji, potem
    `status.record_approval` do przebiegu), bez podprocesu `skillsync`."""
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)

    def spec_act(self, plan_status: str | None = None, spec_status: str = "draft (czeka na GO plan)") -> None:
        spec = self.root / self.SPEC2
        spec.write_text(f"# Spec: Demo 2\n\nStatus: {spec_status}\nPlan: {self.PLAN2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        if plan_status:
            plan = self.root / self.PLAN2
            plan.write_text(f"# Plan: Demo 2\n\nStatus: {plan_status}\nSpec: {self.SPEC2}\n", encoding="utf-8")
            os.utime(plan, (later + 10, later + 10))

    def add_prototype(self) -> None:
        proto = self.root / "docs/prototypes/demo-etap-2"
        proto.mkdir(parents=True)
        (proto / "prototype.json").write_text(json.dumps({"slug": "demo-etap-2", "requirements": self.SPEC2}), encoding="utf-8")

    def actions(self, plan_rel: str | None = None) -> dict:
        return {a["kind"]: a for a in self.board.status(plan_rel)["approvals"]["actions"] if a["kind"] in ("spec", "plan")}

    def states(self, plan_rel: str | None = None) -> dict:
        return {k: (a["state"], a["reason"]) for k, a in self.actions(plan_rel).items()}

    def test_go_plan_states_before_running_waiting_after(self) -> None:
        # po: fixture ma spec approved i plan in_progress — oba przyciski nieaktywne z „zgoda … dana”
        self.assertEqual({k: v[0] for k, v in self.states().items()}, {"spec": "after", "plan": "after"})
        self.assertIn("zgoda „GO plan”", self.states()["spec"][1])
        # trwa: spec draft bez sygnału sesji — widoczny, nieaktywny; „GO implement” jeszcze nie istnieje (przed)
        self.spec_act(spec_status="draft")
        self.assertEqual(list(self.states()), ["spec"])
        self.assertEqual(self.states()["spec"][0], "running")
        self.assertIn("pętli spec", self.states()["spec"][1])
        # czeka: sesja zgłasza „czeka na GO plan”
        self.spec_act()
        acts = self.actions()
        self.assertEqual((acts["spec"]["state"], acts["spec"]["blocked"], acts["spec"]["path"]), ("waiting", None, self.SPEC2))
        self.assertEqual(acts["spec"]["sha"], engine.git_blob_sha((self.root / self.SPEC2).read_bytes()))
        # trwa: niezaakceptowany prototyp wstrzymuje mimo sygnału
        self.board.prototype_acceptance = lambda slug: {"screens": 5, "accepted": 3, "stale": 0}
        self.add_prototype()
        self.assertEqual(self.states()["spec"], ("running", "czeka na akceptację prototypu (3/5)"))

    def test_go_implement_states(self) -> None:
        self.spec_act("draft (szkic — czeka na GO plan)")
        self.assertEqual(list(self.states(self.PLAN2)), ["spec"])  # szkic: GO implement jeszcze nie istnieje
        self.spec_act("draft", spec_status="approved")
        st = self.states(self.PLAN2)
        self.assertEqual(st["spec"][0], "after")
        self.assertEqual(st["plan"][0], "running")
        self.assertIn("koniec aktu II", st["plan"][1])
        self.spec_act("draft (czeka na GO implement)", spec_status="approved")
        self.assertEqual(self.states(self.PLAN2)["plan"], ("waiting", None))
        self.spec_act("draft (czeka na GO implement)")
        self.assertEqual(self.states(self.PLAN2)["plan"][0], "running")  # spec jeszcze draft: najpierw GO plan
        self.spec_act("approved (czeka na GO implement)", spec_status="approved")
        self.assertEqual(self.states(self.PLAN2)["plan"][0], "after")

    def test_approve_in_process_with_sha_and_writes_the_run_trail(self) -> None:
        run = self.root / ".skillsync/runs/2026-01-20-demo-etap-2"  # przebieg z linii `Plan:` speca
        run.mkdir(parents=True)
        self.spec_act()
        offer = self.actions()["spec"]
        self.assertEqual(offer["run"], "2026-01-20-demo-etap-2")
        code, body = self.board.approve("spec", self.SPEC2, offer["sha"])
        self.assertEqual((code, body["word"], body["trail"]), (200, "GO plan", f"{srv.RUNS_DIR}/2026-01-20-demo-etap-2/approvals.jsonl"),
                         body)
        self.set_doc.assert_called_once_with(self.board.root, self.SPEC2, "approve", "specs", expect_sha=offer["sha"])
        after = (self.root / self.SPEC2).read_bytes()
        self.assertIn(b"Status: approved", after)
        audit = [json.loads(x) for x in (run / "approvals.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(a["word"], a["file"], a["sha"], a["shaAfter"], a["run"], a["source"]) for a in audit],
                         [("GO plan", self.SPEC2, offer["sha"], engine.git_blob_sha(after), "2026-01-20-demo-etap-2",
                           "dashboard")])
        self.assertEqual(cli_calls(self), [])  # bez podprocesu skillsync
        self.assertEqual(self.board.approve("spec", self.SPEC2, offer["sha"])[0], 409)  # drugi raz: już „po”
        self.assertEqual(len(audit), len((run / "approvals.jsonl").read_text(encoding="utf-8").splitlines()))

    def test_go_without_a_run_changes_the_status_and_writes_no_trail(self) -> None:
        self.spec_act()
        offer = self.actions()["spec"]
        self.assertIsNone(offer["run"])
        code, body = self.board.approve("spec", self.SPEC2, offer["sha"])
        self.assertEqual((code, body["trail"]), (200, None), body)
        self.assertIn(b"Status: approved", (self.root / self.SPEC2).read_bytes())
        self.record.assert_not_called()
        self.assertFalse((self.root / ".skillsync/status-board").exists())  # żadnego własnego dziennika serwera
        self.assertEqual(list((self.root / srv.RUNS_DIR).glob("*/approvals.jsonl")), [])

    def test_refusals_never_call_the_core_writers(self) -> None:
        self.spec_act()
        sha = self.actions()["spec"]["sha"]
        cases = [("spec", self.SPEC2, "0" * 40, 409), ("plan", PLAN, sha, 409), ("spec", "docs/specs/inny.md", sha, 409),
                 ("rm", self.SPEC2, sha, 400), ("spec", None, sha, 400)]
        for kind, rel, digest, code in cases:
            with self.subTest(kind=kind, rel=rel):
                self.assertEqual(self.board.approve(kind, rel, digest)[0], code)
        self.board.prototype_acceptance = lambda slug: {"screens": 2, "accepted": 1, "stale": 0}
        self.add_prototype()
        self.assertEqual(self.board.approve("spec", self.SPEC2, sha)[0], 409)
        self.set_doc.assert_not_called()
        self.record.assert_not_called()
        self.assertEqual(cli_calls(self), [])
        off = srv.Board(self.root, prototype_port=1, approvals=False)
        self.assertEqual(off.status()["approvals"], {"enabled": False, "actions": [], "last": None})
        self.assertEqual(off.approve("spec", self.SPEC2, sha)[0], 403)
        self.set_doc.assert_not_called()

    def test_file_changed_between_offer_and_write_is_refused_by_the_core(self) -> None:
        self.spec_act()
        sha = self.actions()["spec"]["sha"]
        real = self.set_doc._mock_wraps  # oryginał (atrybut modułu jest już szpiegiem)

        def edit_then_write(*args, **kwargs):  # zmiana pliku po sprawdzeniu oferty, przed zapisem
            path = self.root / self.SPEC2
            path.write_text(path.read_text(encoding="utf-8") + "\nNowa linia.\n", encoding="utf-8")
            return real(*args, **kwargs)
        self.set_doc.side_effect = edit_then_write
        code, body = self.board.approve("spec", self.SPEC2, sha)
        self.assertEqual(code, 409, body)
        self.assertIn("sha mismatch", body["error"])
        self.assertIn(b"draft", (self.root / self.SPEC2).read_bytes())
        self.record.assert_not_called()

    def test_go_does_not_depend_on_skillsync_in_path(self) -> None:
        self.spec_act()
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "pusty")}):
            board = srv.Board(self.root, prototype_port=1)
            board.prototype_acceptance = lambda slug: None
            spec = next(a for a in board.status()["approvals"]["actions"] if a["kind"] == "spec")
            self.assertEqual((spec["state"], spec["reason"]), ("waiting", None))
            self.assertEqual(board.approve("spec", self.SPEC2, spec["sha"])[0], 200)
        self.assertEqual(cli_calls(self), [])

    def test_session_url_and_host_helpers(self) -> None:
        self.assertTrue(srv.session_url_ok("https://claude.ai/code/session_01abc"))
        for bad in ("http://claude.ai/x", "javascript:alert(1)", "https://u:p" + "@" + "claude.ai/", "", None):
            self.assertFalse(srv.session_url_ok(bad), bad)
        self.assertIsNone(srv.Board(self.root, prototype_port=1, session_url="javascript:x").session_url)
        self.assertEqual([srv.request_host(h) for h in ("127.0.0.1:5140", "[::1]:5140", "LOCALHOST", "evil.example:80", None)],
                         ["127.0.0.1", "::1", "localhost", "evil.example", ""])


class DeliveryTests(ProjectCase):
    """Przyciski CP / READY (wariant A): tryb z manifestu, commity ponad bazą, plan done, znacznik `czeka:`."""

    def manifest(self, text: str) -> None:
        path = self.root / srv.MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def delivery(self) -> dict:
        """Tryb dostarczania z manifestu czytanego przez serwer przy każdym statusie (`Board.manifest`)."""
        return engine.read_delivery(self.board.manifest())

    def test_read_delivery_forms(self) -> None:
        self.assertEqual(self.delivery(), {"mode": "pr", "base": None, "source": "default"})
        self.manifest("skills: [a]\ndelivery:\n  mode: direct\n  base: develop  # baza\nother: 1\n")
        self.assertEqual(self.delivery(), {"mode": "direct", "base": "develop", "source": "manifest"})
        self.manifest("delivery: {mode: pr, base: 'main'}\n")
        self.assertEqual(self.delivery()["base"], "main")
        self.manifest("delivery:\n  mode: yolo\n  base: --upload-pack=x\n")
        self.assertEqual(self.delivery(), {"mode": "invalid", "base": None, "source": "manifest"})
        self.assertEqual(self.board.status()["delivery"]["words"], [])  # zły tryb = brak przycisków CP/READY

    def test_words_follow_mode_and_conditions(self) -> None:
        pr, direct = {"mode": "pr"}, {"mode": "direct"}
        none = {"remote": "origin/feature/x", "pushed": True, "ahead": 0}
        self.assertEqual([(w["word"], w["active"]) for w in engine.delivery_words(pr, none, None, [])],
                         [("CP", False), ("READY", False)])
        self.assertEqual([w["word"] for w in engine.delivery_words(direct, none, None, [])], ["CP"])
        self.assertEqual(engine.delivery_words({"mode": "invalid"}, none, None, []), [])
        ahead = engine.delivery_words(direct, {"remote": "origin/feature/x", "pushed": True, "ahead": 3}, None, [], "feature/x")[0]
        self.assertEqual((ahead["active"], ahead["target"]), (True, "feature/x"))
        self.assertEqual(ahead["tip"], "CP → origin/feature/x; 3 commity niewypchniętych względem origin/feature/x")
        fresh = engine.delivery_words(direct, {"remote": "origin/f", "pushed": False, "ahead": 5}, None, [], "f")[0]
        self.assertIn("5 commitów niewypchniętych względem origin (gałęzi jeszcze nie ma na origin)", fresh["tip"])
        handover = engine.delivery_words(direct, none, {"status": "done"}, [], "feature/x", "main")[0]
        self.assertEqual((handover["target"], handover["tip"].split(";")[0]), ("main", "CP → origin/main (oddanie z feature/x)"))
        self.assertTrue(engine.delivery_words(direct, none, {"status": "done"}, [])[0]["active"])
        waits = [engine.wait_item("READY", "checkpoint")]
        words = {w["word"]: w for w in engine.delivery_words(pr, none, None, waits)}
        self.assertEqual((words["CP"]["active"], words["READY"]["active"]), (False, True))
        self.assertIn("czeka na „READY”", words["READY"]["tip"])

    def test_target_is_the_working_branch_unless_the_plan_says_otherwise(self) -> None:
        self.assertEqual(engine.delivery_target({"mode": "direct"}, "feature/x", {}), "feature/x")
        self.assertEqual(engine.delivery_target({"mode": "direct"}, "feature/x", {"oddanie": "main"}), "main")
        self.assertEqual(engine.delivery_target({"mode": "pr"}, "feature/x", {"oddanie": "main"}), "feature/x")  # pr: nigdy bazy
        self.assertEqual(engine.delivery_target({"mode": "direct"}, "feature/x", {"oddanie": "--evil"}), "feature/x")

    def test_git_ahead_counts_commits_not_on_the_remote_branch(self) -> None:
        if not shutil.which("git"):
            self.skipTest("brak git")
        repo = self.tmp / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t" + "@" + "example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t" + "@" + "example.invalid"}
        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)
        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "base")
        git("update-ref", "refs/remotes/origin/main", "HEAD")  # „wypchnięty” main
        git("checkout", "-q", "-b", "feature/x")
        for i in range(2):
            git("commit", "-q", "--allow-empty", "-m", f"c{i}")
        # gałęzi nie ma na origin: liczą się commity, których nie ma na żadnym origin/*
        self.assertEqual(engine.git_ahead(repo, "feature/x"), {"remote": "origin/feature/x", "pushed": False, "ahead": 2})
        git("update-ref", "refs/remotes/origin/feature/x", "HEAD~1")  # wypchnięty jeden z dwóch
        self.assertEqual(engine.git_ahead(repo, "feature/x"), {"remote": "origin/feature/x", "pushed": True, "ahead": 1})
        self.assertEqual(engine.git_ahead(repo, None), {"remote": None, "pushed": None, "ahead": None})

    def test_status_exposes_delivery(self) -> None:
        d = self.board.status()["delivery"]
        self.assertEqual((d["mode"], d["source"]), ("pr", "default"))
        self.assertEqual([w["word"] for w in d["words"]], ["CP", "READY"])


class DeliveryApprovalTests(ProjectCase):
    """CP / READY z kafelka Aktu III: wpis w .skillsync/runs/<run-id>/approvals.jsonl z sha HEAD pokazanego na stronie."""
    HEAD = "abc1234def5678"  # z make_project: refs/heads/feature/demo

    def offers(self) -> dict:
        return {a["kind"]: a for a in self.board.status(PLAN)["approvals"]["actions"] if a["kind"] in ("cp", "ready")}

    def wait_for(self, word: str) -> None:
        with (self.root / f"{RUN}/checkpoints.md").open("a", encoding="utf-8") as fh:
            fh.write(f"- 11:20 zrobione: S9; w toku: —; czeka: {word}\n")

    def set_plan_status(self, status: str) -> None:
        plan = self.root / PLAN
        plan.write_text(re.sub(r"(?m)^Status:.*$", f"Status: {status}", plan.read_text(encoding="utf-8"), count=1),
                        encoding="utf-8")

    def test_cp_ready_states_before_running_waiting_after(self) -> None:
        offers = self.offers()
        self.assertEqual(sorted(offers), ["cp", "ready"])  # brak manifestu = tryb pr
        self.assertEqual((offers["cp"]["path"], offers["cp"]["sha"]), ("2026-01-10-demo-etap-1", self.HEAD))
        # trwa: realizacja w toku, brak powodu — widoczny i nieaktywny „czeka na koniec realizacji”
        self.assertEqual(offers["cp"]["state"], "running")
        self.assertIn("czeka na koniec realizacji", offers["cp"]["reason"])
        # przed: plan draft (akt III nie nadszedł) — brak przycisków
        self.set_plan_status("draft")
        self.assertEqual(self.offers(), {})
        # czeka: plan done albo znacznik `czeka: CP`
        self.set_plan_status("done")
        self.assertEqual({k: a["state"] for k, a in self.offers().items()}, {"cp": "waiting", "ready": "waiting"})
        self.set_plan_status("in_progress")
        self.wait_for("CP")
        offers = self.offers()
        self.assertEqual((offers["cp"]["state"], offers["ready"]["state"]), ("waiting", "running"))
        # po: zgoda zapisana dla tego HEAD — nieaktywny z godziną
        self.assertEqual(self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD, PLAN)[0], 200)
        cp = self.offers()["cp"]
        self.assertEqual(cp["state"], "after")
        self.assertRegex(cp["reason"], r"zgoda „CP” dana \d\d:\d\d")

    def test_cp_writes_run_approval_once_for_this_head(self) -> None:
        self.wait_for("CP")
        self.assertEqual(self.board.approve("cp", "2026-01-10-demo-etap-1", "0" * 14)[0], 409)  # inny HEAD
        code, body = self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD)
        self.assertEqual((code, body["word"]), (200, "CP"), body)
        log = [json.loads(x) for x in (self.root / f"{RUN}/approvals.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(e["word"], e["sha"], e["run"], e["source"], e["schema"]) for e in log],
                         [("CP", self.HEAD, "2026-01-10-demo-etap-1", "dashboard", 1)])
        self.assertEqual((log[0]["plan"], log[0]["target"], log[0]["branch"]), (PLAN, "feature/demo", "feature/demo"))
        self.assertEqual((self.offers()["cp"]["state"], self.offers()["cp"]["given"]["sha"]), ("after", self.HEAD))
        self.assertEqual(self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD)[0], 409)  # już zapisane
        self.assertEqual(self.board.status()["approvals"]["last"]["word"], "CP")
        (self.root / ".git/refs/heads/feature/demo").write_text("fff0000aaa1111\n", encoding="utf-8")
        self.assertEqual(self.offers()["cp"]["state"], "waiting")  # nowy HEAD = nowa zgoda

    def test_cp_goes_through_record_approval(self) -> None:
        self.wait_for("CP")
        with mock.patch.object(srv.status_core, "record_approval", wraps=srv.status_core.record_approval) as record:
            code, body = self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD, PLAN)
        self.assertEqual((code, body["file"]), (200, f"{RUN}/approvals.jsonl"), body)
        record.assert_called_once()
        args, kwargs = record.call_args
        self.assertEqual(args[1:3], ("CP", "2026-01-10-demo-etap-1"))
        self.assertEqual((kwargs["sha"], kwargs["source"], kwargs["target"], kwargs["plan"]),
                         (self.HEAD, "dashboard", "feature/demo", PLAN))

    def test_core_refusal_is_a_500_without_an_entry(self) -> None:
        self.wait_for("CP")
        refused = {"schema": 1, "status": "BLOCKED", "reason": "Approvals file is locked by another writer", "entry": None}
        with mock.patch.object(srv.status_core, "record_approval", return_value=refused):
            code, body = self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD)
        self.assertEqual(code, 500, body)
        self.assertIn("locked", body["error"])
        self.assertFalse((self.root / f"{RUN}/approvals.jsonl").exists())

    def test_run_ids_outside_the_pattern_are_refused(self) -> None:
        self.wait_for("CP")
        for bad in ("../x", "..", ".hidden", "../../etc", "a/b", ""):
            with self.subTest(run=bad):
                self.assertEqual(self.board.approve("cp", bad, self.HEAD)[0], 409)
        self.assertFalse((self.root / f"{RUN}/approvals.jsonl").exists())
        self.assertEqual(list(self.root.parent.glob("*/approvals.jsonl")), [])

    def test_blocked_and_foreign_requests_write_nothing(self) -> None:
        self.assertEqual(self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD)[0], 409)  # bez powodu
        self.wait_for("READY")
        self.assertEqual(self.board.approve("ready", "../../etc", self.HEAD)[0], 409)
        direct = self.root / srv.MANIFEST
        direct.parent.mkdir(parents=True, exist_ok=True)
        direct.write_text("delivery:\n  mode: direct\n", encoding="utf-8")
        self.assertEqual(self.board.approve("ready", "2026-01-10-demo-etap-1", self.HEAD)[0], 409)  # READY tylko w pr
        self.assertFalse((self.root / f"{RUN}/approvals.jsonl").exists())

    def test_read_approvals_skips_bad_lines(self) -> None:
        # Czytnik wpisów to `dashboard.run_approvals` z regułami przyjęcia ze status.py (znane słowo, `at`, sha hex):
        # poprawne wpisy przechodzą, błędne linie i wpisy są pomijane.
        path = self.root / f"{RUN}/approvals.jsonl"
        at = '"at": "2026-01-10T10:00:00Z"'
        path.write_text(f'{{"word": "CP", "sha": "abc1234", {at}}}\nnie json\n[1]\n{{"word": "READY", "sha": "abc1234", {at}}}\n'
                        f'{{"word": "CP", "sha": "a", {at}}}\n{{"word": "READY"}}\n{{"word": "RM", "sha": "abc1234", {at}}}\n',
                        encoding="utf-8")
        self.assertEqual([e["word"] for e in engine.run_approvals(self.root, "2026-01-10-demo-etap-1")], ["CP", "READY"])
        self.assertEqual(engine.run_approvals(self.root, "../x"), [])


class DescriptionTests(ProjectCase):
    """Dymki: każdy krok, akt i bramka ma statyczny opis w descriptions.json; ostrzeżenie pre-implement."""

    def test_every_referenced_key_has_a_description(self) -> None:
        import re as _re
        descs = srv.load_descriptions(DESCRIPTIONS)
        engine_src = (ROOT / "skillsync_workflow" / "dashboard.py").read_text(encoding="utf-8")
        server = set(_re.findall(r"key=['\"](\w+)['\"]", engine_src))
        js = (SKILL / "assets/app.js").read_text(encoding="utf-8")
        front = set(_re.findall(r'desc\("(\w+)"', js)) | set(_re.findall(r'"(gate_\w+|banner_\w+|closure|substep|phase)"', js))
        front = {k for k in front if not k.endswith("_")} | {"act_spec", "act_plan", "act_real"}
        html = set(_re.findall(r'data-desc="(\w+)"', (SKILL / "assets/index.html").read_text(encoding="utf-8")))
        needed = server | front | html
        self.assertGreaterEqual(len(needed), 20)
        self.assertEqual(sorted(needed - set(descs)), [])
        for key, item in descs.items():
            self.assertTrue(item.get("title") and len(item["text"]) > 60, key)
        for act in self.board.status()["acts"]:
            for stage in act.get("stages", []):
                self.assertIn(stage["key"], descs, stage)

    def test_pre_implement_warning(self) -> None:
        spec = self.root / "docs/specs/plan-etap-4.md"
        spec.write_text("# Spec\n\nStatus: draft\n\n## Kontrakty\n\nNowy endpoint.\n\n## Migracja\n\nnie dotyczy\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        acts = {a["key"]: a for a in self.board.status()["acts"]}
        stage = next(s for s in acts["spec"]["stages"] if s["key"] == "stage_spec_review")
        self.assertEqual(stage["tip"], "rundy review speca w katalogu przebiegu")
        self.assertEqual(acts["spec"]["warning"], "brak pre-implement przed GO plan: spec ma sekcje Kontrakty")
        self.assertEqual(engine.risky_sections("## Dane\n\n- nie dotyczy (brak zmian)\n"), [])


class HttpCase(ProjectCase):
    """Serwer na 127.0.0.1, wolny port 5100–5199 (nie 5140); zamykany w tearDown."""
    APPROVALS = True

    def setUp(self) -> None:
        super().setUp()
        self.port = free_port()
        self.board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, assets=ASSETS,
                               descriptions=DESCRIPTIONS, approvals=self.APPROVALS)
        self.server = srv.make_server(self.board, "127.0.0.1", self.port)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        threading.Thread(target=self.board.watch_loop, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.board.stop.set()
        with self.board.cond:
            self.board.cond.notify_all()
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def get(self, path: str):
        return urllib.request.urlopen(self.base + path, timeout=5)

    def post(self, data: dict, headers: dict) -> tuple[int, dict]:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/approve", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)



class HttpTests(HttpCase):
    def test_endpoints_and_headers(self) -> None:
        with self.get("/") as r:
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
            self.assertIn("no-cache", r.headers["Cache-Control"])
        with self.get("/api/status") as r:
            self.assertEqual(json.load(r)["plan"]["path"], PLAN)
        with self.get("/") as r:
            page = r.read().decode("utf-8")
        self.assertIn('<script src="/assets/theme.js"></script>', page.split("</head>")[0])
        self.assertIn('id="themeBtn"', page)
        with self.get("/assets/app.js") as r:
            js = r.read().decode("utf-8")
        self.assertIn("Otwórz specyfikację", js)  # Akt I → spec w Dokumentach
        self.assertIn("Otwórz plan", js)  # Akt II → plan w Dokumentach
        with self.get("/assets/theme.js") as r:
            self.assertIn("dashboard:theme", r.read().decode("utf-8"))
        with self.get("/assets/app.css") as r:
            css = r.read().decode("utf-8")
        self.assertIn(':root[data-theme="dark"]', css)
        self.assertIn(':root:not([data-theme="light"])', css)
        with self.get("/api/descriptions") as r:
            self.assertIn("stage_pre_implement", json.load(r))
        with self.get("/api/doc?path=" + SPEC) as r:
            self.assertIn("md-check", json.load(r)["html"])
        for bad in ("/api/doc?path=../../.env", "/api/doc?path=.env", "/assets/../dashboard_server.py", "/nope"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(bad)
            self.assertEqual(ctx.exception.code, 404, bad)

    def test_approve_endpoint_guards(self) -> None:
        with self.get("/api/status") as r:
            self.assertTrue(json.load(r)["approvals"]["here"])
        ok = {"Content-Type": "application/json", "X-Status-Board": "1"}
        data = {"kind": "plan", "path": PLAN, "sha": "0" * 40}
        for headers in ({"Content-Type": "application/json"}, {**ok, "Host": "evil.example"},
                        {**ok, "Origin": "https://evil.example"}, {**ok, "Content-Type": "text/plain"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(data, headers)[0], 403)
        code, body = self.post(data, {**ok, "Origin": f"http://localhost:{self.port}"})
        self.assertEqual(code, 409, body)  # przeszło strażników; plan in_progress nie ma oferty
        # Odczyt spoza loopbacku tylko przez jawne --approve-host: obcy Host = 403 także na GET.
        req = urllib.request.Request(self.base + "/api/status", headers={"Host": "tunel.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        self.board.approve_hosts = srv.LOOPBACK_HOSTS | {"lan-host"}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        lan = urllib.request.Request(self.base + "/api/status", headers={"Host": "lan-host:5140"})
        with urllib.request.urlopen(lan, timeout=5) as r:
            self.assertTrue(json.load(r)["approvals"]["here"])
        tunnel = {**ok, "Host": "lan-host:5140", "Origin": "http://lan-host:5140"}
        self.assertEqual(self.post(data, tunnel)[0], 409)  # dopuszczony host przechodzi strażników
        self.assertEqual(self.post(data, {**tunnel, "Origin": "http://evil.example"})[0], 403)

    def test_sse_change_after_file_edit(self) -> None:
        r = self.get("/events")
        self.assertEqual(r.headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertEqual(r.headers["X-Accel-Buffering"], "no")
        self.assertIn("no-cache", r.headers["Cache-Control"])
        events: list[str] = []

        def reader() -> None:
            try:
                for raw in r:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("event: "):
                        events.append(line[7:])
                        if line == "event: change":
                            return
            except Exception:
                return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        deadline = time.time() + 3
        while "hello" not in events and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.7)  # co najmniej jeden heartbeat (0.5 s)
        plan = self.root / PLAN
        plan.write_text(plan.read_text(encoding="utf-8").replace("- [ ] S2a", "- [x] S2a"), encoding="utf-8")
        os.utime(plan, ns=(time.time_ns(), time.time_ns() + 1_000_000_000))
        t.join(timeout=5)
        r.close()
        self.assertEqual(events[0], "hello")
        self.assertIn("ping", events)
        self.assertEqual(events[-1], "change")
        with self.get("/api/status") as resp:
            self.assertEqual(json.load(resp)["progress"]["done"], 3)


def raw_post(port: int, headers: dict, body: bytes = b"") -> tuple[int, dict]:
    """POST /api/approve przez http.client z dowolnym Content-Length (np. większym niż wysłana treść)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("POST", "/api/approve", skip_host="Host" in headers)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


class HttpSecurityTests(HttpCase):
    """K4: nagłówki bezpieczeństwa, Content-Type, allowlista assetów i dokumentów, limit SSE i treści POST."""
    OK = {"Content-Type": "application/json", "X-Status-Board": "1"}

    def test_security_headers_and_content_types(self) -> None:
        expected = {"/": "text/html; charset=utf-8", "/index.html": "text/html; charset=utf-8",
                    "/api/status": "application/json; charset=utf-8", "/assets/app.css": "text/css; charset=utf-8",
                    "/assets/app.js": "text/javascript; charset=utf-8", "/healthz": "application/json; charset=utf-8",
                    "/api/descriptions": "application/json; charset=utf-8"}
        for path, ctype in expected.items():
            with self.subTest(path=path), self.get(path) as r:
                self.assertEqual(r.headers["Content-Type"], ctype)
                self.assertEqual(r.headers["X-Frame-Options"], "DENY")
                self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
                self.assertIn("no-store", r.headers["Cache-Control"])
        with self.get("/") as r:
            csp = r.headers["Content-Security-Policy"]
        for part in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'", "base-uri 'none'",
                     "form-action 'none'", "connect-src 'self'"):
            self.assertIn(part, csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src", 1)[1].split(";")[0])  # bez inline script
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.headers["X-Frame-Options"], "DENY")
        self.assertEqual(ctx.exception.headers["X-Content-Type-Options"], "nosniff")
        with self.get("/healthz") as r:
            self.assertEqual(json.load(r), {"ok": True, "version": self.board.version})
        with self.get("/api/status") as r:
            data = json.load(r)
        self.assertEqual((data["schema"], data["app"]["name"]), (1, "dashboard"))  # ten sam kontrakt co `dashboard show`
        req = urllib.request.Request(self.base + "/", method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual((r.status, r.read()), (200, b""))

    def test_assets_come_only_from_the_allowlist(self) -> None:
        self.assertEqual(sorted(ASSETS), ["app.css", "app.js", "index.html", "theme.js"])
        self.assertEqual(DESCRIPTIONS, SKILL / "descriptions.json")
        for bad in ("/assets/SKILL.md", "/assets/descriptions.json", "/assets/..%2fSKILL.md", "/assets/%2e%2e/SKILL.md",
                    "/assets/../SKILL.md", "/assets/sub/app.js", "/assets/nie-ma.js", "/assets/.app.js", "/assets/app.js.map",
                    "/assets/", "/assets/app.py"):
            with self.subTest(path=bad), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(bad)
            self.assertEqual(ctx.exception.code, 404, bad)
        self.board.assets = {}  # bez warstwy z assetami strona nie istnieje
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/")
        self.assertEqual(ctx.exception.code, 404)

    def test_doc_endpoint_refuses_paths_outside_the_allowlist(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("# poza projektem\n", encoding="utf-8")
        links = []
        for name, target in (("docs/specs/link-out.md", outside), ("docs/specs/link-env.md", self.root / ".env")):
            try:
                (self.root / name).symlink_to(target)
                links.append(name)
            except OSError:
                pass
        bad = ["../../.env", ".env", "docs/specs/../../.env", "docs%2F..%2F.env", ".skillsync/config.md", "docs/secret.md",
               ".git/HEAD", str(self.root / SPEC), "docs/specs/%2e%2e/secret.md", f"{RUN}/notes.txt", *links]
        for rel in bad:
            with self.subTest(path=rel), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/api/doc?path=" + rel)
            self.assertEqual(ctx.exception.code, 404, rel)
        self.assertEqual(srv.MAX_DOC_BYTES, 2_000_000)
        big = self.root / "docs/specs/duzy.md"
        big.write_text("# Duży\n\n" + ("x" * 99 + "\n") * 21_000, encoding="utf-8")
        with self.get("/api/doc?path=docs/specs/duzy.md") as r:
            doc = json.load(r)
        self.assertEqual((doc["size"], doc["truncated"]), (big.stat().st_size, True))
        self.assertLessEqual(doc["html"].count("x"), srv.MAX_DOC_BYTES)

    def test_sse_client_limit(self) -> None:
        self.assertEqual(srv.MAX_SSE_CLIENTS, 64)
        with self.board.cond:
            self.board.sse_clients = srv.MAX_SSE_CLIENTS
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/events")
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(self.board.sse_clients, srv.MAX_SSE_CLIENTS)  # odmowa nie zajmuje miejsca
        with self.board.cond:
            self.board.sse_clients = srv.MAX_SSE_CLIENTS - 1
        r = self.get("/events")
        self.assertEqual(r.status, 200)
        self.assertEqual(self.board.sse_clients, srv.MAX_SSE_CLIENTS)
        r.close()

    def test_post_body_and_route_limits(self) -> None:
        self.assertEqual(srv.MAX_BODY, 4096)
        self.assertEqual(raw_post(self.port, {**self.OK, "Content-Length": str(srv.MAX_BODY + 1)})[0], 400)  # > 4096
        self.assertEqual(raw_post(self.port, {**self.OK, "Content-Length": "0"})[0], 400)
        self.assertEqual(raw_post(self.port, {**self.OK, "Content-Length": "-1"})[0], 400)
        self.assertEqual(raw_post(self.port, {**self.OK, "Content-Length": "x"})[0], 400)
        for body in (b"nie json", b"[1]", b"\xff\xfe"):
            with self.subTest(body=body):
                self.assertEqual(raw_post(self.port, {**self.OK, "Content-Length": str(len(body))}, body)[0], 400)
        self.assertEqual(self.post({}, {**self.OK, "Origin": "null"})[0], 403)  # Origin bez hosta = obcy
        req = urllib.request.Request(self.base + "/api/status", data=b"{}", method="POST", headers=self.OK)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 404)


class HttpApprovalTests(HttpCase):
    """K4: zgoda z poprawnymi nagłówkami przechodzi do zapisu; każda odmowa nie woła `set_doc_status`/`record_approval`."""
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"
    HEAD = "abc1234def5678"
    RUN_ID = "2026-01-10-demo-etap-1"

    def setUp(self) -> None:
        super().setUp()
        install_fake_cli(self)
        spy_core(self)
        self.ok = {"Content-Type": "application/json", "X-Status-Board": "1", "Origin": f"http://127.0.0.1:{self.port}"}

    def go_plan_waiting(self) -> str:
        spec = self.root / self.SPEC2
        spec.write_text(f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {self.PLAN2}\n", encoding="utf-8")
        later = (self.root / PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        with self.get("/api/status") as r:
            offer = next(a for a in json.load(r)["approvals"]["actions"] if a["kind"] == "spec")
        self.assertEqual((offer["state"], offer["path"]), ("waiting", self.SPEC2))
        return offer["sha"]

    def entries(self, run: str) -> list[dict]:
        path = self.root / srv.RUNS_DIR / run / "approvals.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_correct_headers_reach_the_consent(self) -> None:
        (self.root / srv.RUNS_DIR / "2026-01-20-demo-etap-2").mkdir(parents=True)
        sha = self.go_plan_waiting()
        code, body = self.post({"kind": "spec", "path": self.SPEC2, "sha": sha}, self.ok)
        self.assertEqual((code, body["word"], body["file"]), (200, "GO plan", self.SPEC2), body)
        self.set_doc.assert_called_once_with(self.board.root, self.SPEC2, "approve", "specs", expect_sha=sha)
        after = engine.git_blob_sha((self.root / self.SPEC2).read_bytes())
        self.assertEqual([(e["word"], e["sha"], e["file"], e["shaAfter"], e["source"])
                          for e in self.entries("2026-01-20-demo-etap-2")], [("GO plan", sha, self.SPEC2, after, "dashboard")])
        self.assertEqual(cli_calls(self), [])
        # bez Origin (np. curl na tej maszynie) też przechodzi; Host z --approve-host także
        self.board.approve_hosts = srv.LOOPBACK_HOSTS | {"lan-host"}
        code, _ = raw_post(self.port, {"Host": "lan-host:5140", "Content-Type": "application/json", "X-Status-Board": "1",
                                       "Content-Length": "2"}, b"{}")
        self.assertEqual(code, 400)  # przeszło strażników nagłówków; puste żądanie odrzuca dopiero walidacja treści

    def test_refusals_never_write(self) -> None:
        sha = self.go_plan_waiting()
        data = {"kind": "spec", "path": self.SPEC2, "sha": sha}
        bad_headers = [
            {k: v for k, v in self.ok.items() if k != "X-Status-Board"},
            {**self.ok, "X-Status-Board": "0"},
            {**self.ok, "Host": "evil.example"},
            {**self.ok, "Host": "127.0.0.1.evil.example"},
            {**self.ok, "Origin": "https://evil.example"},
            {**self.ok, "Origin": "http://localhost.evil.example:5140"},
            {**self.ok, "Content-Type": "text/plain"},
            {**self.ok, "Content-Type": "application/x-www-form-urlencoded"},
            {k: v for k, v in self.ok.items() if k != "Content-Type"},
        ]
        for headers in bad_headers:
            with self.subTest(headers=headers):
                self.assertEqual(self.post(data, headers)[0], 403)
        # treść > 4096 B: odmowa po samym Content-Length, przed odczytem treści (bez wysyłania jej, żeby nie zrywać połączenia)
        self.assertEqual(raw_post(self.port, {**self.ok, "Content-Length": str(srv.MAX_BODY + 1)})[0], 400)
        self.assertEqual(self.post({**data, "sha": "0" * 40}, self.ok)[0], 409)  # nieaktualny sha (plik się zmienił)
        self.assertEqual(self.post({**data, "kind": "rm"}, self.ok)[0], 400)
        self.assertEqual(self.post({**data, "path": "docs/specs/inny.md"}, self.ok)[0], 409)
        self.set_doc.assert_not_called()
        self.record.assert_not_called()
        self.assertEqual(cli_calls(self), [])
        self.assertIn(b"draft", (self.root / self.SPEC2).read_bytes())
        self.assertFalse((self.root / ".skillsync/status-board").exists())
        self.assertFalse((self.root / RUN / "approvals.jsonl").exists())

    def test_stale_head_and_second_write_are_refused(self) -> None:
        with (self.root / f"{RUN}/checkpoints.md").open("a", encoding="utf-8") as fh:
            fh.write("- 11:20 zrobione: S9; w toku: —; czeka: CP\n")
        cp = {"kind": "cp", "path": self.RUN_ID, "plan": PLAN}
        self.assertEqual(self.post({**cp, "sha": "0" * 14}, self.ok)[0], 409)  # HEAD inny niż pokazany
        self.record.assert_not_called()
        code, body = self.post({**cp, "sha": self.HEAD}, self.ok)
        self.assertEqual((code, body["file"]), (200, f"{RUN}/approvals.jsonl"), body)
        self.assertEqual(self.post({**cp, "sha": self.HEAD}, self.ok)[0], 409)  # drugi zapis dla tego HEAD
        log = (self.root / f"{RUN}/approvals.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([(json.loads(x)["word"], json.loads(x)["source"]) for x in log], [("CP", "dashboard")])
        self.assertEqual(self.record.call_count, 1)


def raw_get(port: int, path: str, host: str | None) -> tuple[int, dict, bytes]:
    """GET przez http.client z dowolnym nagłówkiem Host (None = bez nagłówka); SSE czyta tylko nagłówki."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        if host is not None:
            conn.putheader("Host", host)
        conn.endheaders()
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        body = b"" if headers.get("content-type", "").startswith("text/event-stream") else resp.read()
        return resp.status, headers, body
    finally:
        conn.close()


class HttpHostTests(HttpCase):
    """Kontrola Host na WSZYSTKICH endpointach: loopback (dowolny port) albo --approve-host; inaczej 403 bez treści."""
    ENDPOINTS = ("/", "/index.html", "/assets/app.js", "/assets/app.css", "/api/status", f"/api/doc?path={SPEC}",
                 "/api/descriptions", "/healthz", "/events", "/nope")
    FOREIGN = ("evil.example", "evil.example:5140", "127.0.0.1.evil.example", "localhost.evil.example", "tunel.example:80",
               "10.0.0.7", "[fe80::1]:5140", "", None)

    def test_foreign_host_is_refused_on_every_get(self) -> None:
        for path in self.ENDPOINTS:
            for host in self.FOREIGN:
                with self.subTest(path=path, host=host):
                    code, headers, body = raw_get(self.port, path, host)
                    self.assertEqual(code, 403)
                    self.assertEqual(headers["content-type"], "application/json; charset=utf-8")
                    self.assertEqual(headers["x-frame-options"], "DENY")
                    self.assertEqual(json.loads(body), {"error": srv.HOST_REFUSED})  # bez treści strony/statusu
        self.assertEqual(self.board.sse_clients, 0)  # odmowa SSE nie zajmuje miejsca

    def test_loopback_host_with_any_port_is_served(self) -> None:
        for host in ("127.0.0.1", f"127.0.0.1:{self.port}", "localhost:1", "LOCALHOST", "[::1]", "[::1]:5140", "::1"):
            for path in ("/", "/api/status", "/healthz", "/api/descriptions", f"/api/doc?path={SPEC}", "/assets/app.js"):
                with self.subTest(host=host, path=path):
                    self.assertEqual(raw_get(self.port, path, host)[0], 200)
            with self.subTest(host=host, path="/events"):
                code, headers, _ = raw_get(self.port, "/events", host)
                self.assertEqual((code, headers["content-type"]), (200, "text/event-stream; charset=utf-8"))

    def test_approve_host_admits_an_extra_host_for_reading(self) -> None:
        self.assertEqual(raw_get(self.port, "/api/status", "lan-host:5140")[0], 403)
        self.board.approve_hosts = srv.LOOPBACK_HOSTS | {"lan-host"}
        for path in ("/", "/api/status", "/healthz", "/events"):
            with self.subTest(path=path):
                self.assertEqual(raw_get(self.port, path, "lan-host:5140")[0], 200)
        code, _, body = raw_get(self.port, "/api/status", "LAN-HOST")
        self.assertEqual((code, json.loads(body)["approvals"]["here"]), (200, True))
        self.assertEqual(raw_get(self.port, "/api/status", "lan-host.evil.example")[0], 403)

    def test_foreign_host_post_is_refused_before_the_body(self) -> None:
        spy_core(self)
        for host in ("evil.example", "evil.example:5140", ""):
            with self.subTest(host=host):
                code, body = raw_post(self.port, {"Host": host, "Content-Type": "application/json", "X-Status-Board": "1",
                                                  "Content-Length": str(srv.MAX_BODY + 1)})
                self.assertEqual((code, body), (403, {"error": srv.HOST_REFUSED}))  # 403, nie 400: Host przed długością
        self.set_doc.assert_not_called()
        self.record.assert_not_called()

    def test_refusal_is_logged_only_in_verbose_mode(self) -> None:
        from io import StringIO
        for verbose, expect in ((False, False), (True, True)):
            with self.subTest(verbose=verbose), mock.patch.object(sys, "stderr", new=StringIO()) as err:
                self.server.RequestHandlerClass.verbose = verbose
                self.assertEqual(raw_get(self.port, "/api/status", "evil.example")[0], 403)
                self.assertEqual("Host spoza listy ('evil.example')" in err.getvalue(), expect, err.getvalue())


class HttpReadOnlyTests(HttpCase):
    """`--no-approvals`: strona tylko do odczytu, POST /api/approve = 403 nawet z poprawnymi nagłówkami."""
    APPROVALS = False

    def test_no_approvals_is_403(self) -> None:
        with self.get("/api/status") as r:
            approvals = json.load(r)["approvals"]
        self.assertEqual((approvals["enabled"], approvals["actions"]), (False, []))
        ok = {"Content-Type": "application/json", "X-Status-Board": "1"}
        code, body = self.post({"kind": "cp", "path": "2026-01-10-demo-etap-1", "sha": "abc1234def5678"}, ok)
        self.assertEqual(code, 403)
        self.assertIn("--no-approvals", body["error"])
        self.assertFalse((self.root / RUN / "approvals.jsonl").exists())


class ServeCommandTests(unittest.TestCase):
    """`skillsync dashboard serve`: rejestracja podkomendy, odmowy startu (BLOCKED) i bieg na wolnym porcie z warstwy `library/`."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-serve-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = make_project(self.tmp)
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        self.env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"),
                    "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"), "XDG_DATA_HOME": str(self.tmp / "xdg-data")}
        self.layer = ["--layer", f"core={ROOT / 'library'}"]

    def cli(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=timeout,
                              env=self.env, check=False)

    def test_help_lists_serve_and_its_flags(self) -> None:
        result = self.cli("dashboard", "serve", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--port", "--host", "--approve-host", "--no-approvals", "--layer", "--plan", "--session-url"):
            self.assertIn(flag, result.stdout)
        self.assertIn("5140", result.stdout)
        top = self.cli("dashboard", "--help")
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertIn("serve", top.stdout)
        self.assertIn("show", top.stdout)
        self.assertEqual(self.cli("dashboard").returncode, 2)  # podkomenda wymagana

    def test_refused_start_is_blocked(self) -> None:
        for port in ("5200", "4444", "9222", "5099"):
            with self.subTest(port=port):
                result = self.cli("dashboard", "serve", str(self.root), "--port", port, *self.layer)
                self.assertEqual(result.returncode, 2, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "BLOCKED")
                self.assertIn("5100", report["reason"])
        result = self.cli("dashboard", "serve", str(self.root), "--port", str(free_port()), "--session-url",
                          "http://example.com/x", *self.layer)
        self.assertEqual((result.returncode, json.loads(result.stdout)["status"]), (2, "BLOCKED"))
        result = self.cli("dashboard", "serve", str(self.root), "--port", str(free_port()), "--plan",
                          "../../etc/passwd.md", *self.layer)
        self.assertEqual((result.returncode, json.loads(result.stdout)["status"]), (2, "BLOCKED"))
        empty = self.tmp / "empty-layer"
        empty.mkdir()
        (empty / "layer.yaml").write_text((ROOT / "library" / "layer.yaml").read_text(encoding="utf-8"), encoding="utf-8")
        result = self.cli("dashboard", "serve", str(self.root), "--port", str(free_port()), "--layer", f"core={empty}")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("assets/index.html", json.loads(result.stdout)["reason"])
        (self.root / srv.MANIFEST).unlink()
        result = self.cli("dashboard", "serve", str(self.root), "--port", str(free_port()), *self.layer)
        self.assertEqual((result.returncode, json.loads(result.stdout)["status"]), (2, "BLOCKED"))

    @unittest.skipIf(os.name == "nt", "SIGTERM kończy proces na Windows bez obsługi")
    def test_serve_answers_and_stops_on_sigterm(self) -> None:
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(CLI), "dashboard", "serve", str(self.root), "--port", str(port),
                                 *self.layer], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        base, deadline, health = f"http://127.0.0.1:{port}", time.time() + 20, None
        while time.time() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    health = json.load(r)
                break
            except OSError:
                time.sleep(0.1)
        self.assertEqual(health, {"ok": True, "version": 0}, proc.stderr.read() if proc.poll() is not None else "")
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            self.assertIn("text/html", r.headers["Content-Type"])
        with urllib.request.urlopen(base + "/api/status", timeout=5) as r:
            data = json.load(r)
        self.assertEqual((data["schema"], data["plan"]["path"], data["approvals"]["here"]), (1, PLAN, True))
        with urllib.request.urlopen(base + "/api/descriptions", timeout=5) as r:
            self.assertIn("stage_pre_implement", json.load(r))
        proc.terminate()
        out, err = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual({k: v for k, v in json.loads(out).items() if k != "url"}, {"status": "PASS", "stopped": True})
        self.assertIn(f"127.0.0.1:{port}", err)


if __name__ == "__main__":
    unittest.main()
