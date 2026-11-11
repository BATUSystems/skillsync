"""Testy serwera dashboard: python3 -m unittest tests.test_skillsync_dashboard_server

Testy HTTP startują serwer na 127.0.0.1 na wolnym porcie z zakresu 5100–5199 (nigdy 5140), wybieranym dynamicznie.
"""
from __future__ import annotations

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
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "library" / "skills" / "dashboard"
sys.path.insert(0, str(SKILL / "scripts"))

import dashboard_server as sb  # noqa: E402

FIX = HERE / "fixtures" / "dashboard_server"


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


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="status-board-"))
        self.root = make_project(self.tmp)
        self.board = sb.Board(self.root, prototype_port=1)  # port 1: prototyp zawsze „wyłączony”
        self.board.prototype_acceptance = lambda slug: None  # bez wywołania CLI skillsync w testach

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class ParsingTests(unittest.TestCase):
    def test_header_fields(self) -> None:
        title, fields = sb.parse_header((FIX / "plan.md").read_text(encoding="utf-8"))
        self.assertEqual(title, "Plan: Demo, etap 1 — „Moduł przykładowy”")
        self.assertEqual(sb.normalize_status(fields["status"]), "in_progress")
        self.assertEqual(sb.first_token(fields["spec"]), SPEC)
        self.assertEqual(sb.first_token(fields["przebieg"]), "2026-01-10-demo-etap-1")
        self.assertTrue(fields["spec-sha"].startswith("0123456789"))

    def test_progress_phases_and_steps(self) -> None:
        phases = sb.parse_progress((FIX / "plan.md").read_text(encoding="utf-8"))
        self.assertEqual([p["name"] for p in phases], ["Faza 1: Fundament", "Faza 2: API", "Faza 3: Domknięcie"])
        steps = [s for p in phases for s in p["steps"]]
        self.assertEqual([s["id"] for s in steps], ["S0", "S1", "S2a", "S2b", "S3a", "S3b", "S4"])
        self.assertEqual(steps[0]["sha"], "abc1234")
        self.assertEqual(steps[0]["title"], "Commit planu, baseline testów")
        self.assertIsNone(steps[-1]["sha"])
        self.assertEqual(steps[-1]["note"], "n/d (raport w przebiegu)")
        self.assertNotIn("S99", [s["id"] for s in steps])

    def test_progress_ids_with_dash_and_block_names(self) -> None:
        phases = sb.parse_progress((FIX / "plan-done.md").read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in phases[0]["steps"]], ["A0", "A-1"])
        self.assertEqual(phases[0]["steps"][1]["sha"], "18848fd")

    def test_progress_without_phase_header(self) -> None:
        phases = sb.parse_progress((FIX / "plan-draft.md").read_text(encoding="utf-8"))
        self.assertEqual(phases[0]["name"], "Kroki")

    def test_checkpoints(self) -> None:
        start, items = sb.parse_checkpoints((FIX / "checkpoints.md").read_text(encoding="utf-8"))
        self.assertEqual(start, "2026-01-10T10:00:00+01:00")
        self.assertEqual(len(items), 3)
        self.assertTrue(items[-1]["text"].endswith("kontynuacja linii checkpointu"))
        self.assertIsNotNone(sb.parse_iso(start))

    def test_in_progress_ids_only_leading(self) -> None:
        text = ("checkpoint 1: zrobione S1, S2a/S2c; w toku: S2b/S2d (test-writer), S3a (implementer opus), "
                "S5a, szczebel 3 S1 na migawce, baseline; ryzyko: S9 też")
        self.assertEqual(sb.ids_in_progress(text), ["S2b", "S2d", "S3a", "S5a"])
        self.assertEqual(sb.ids_reported_done(text), ["S1", "S2a", "S2c"])
        self.assertIsNone(sb.ids_in_progress("restart sesji: przerwane S2b"))
        self.assertEqual(sb.ids_in_progress("W toku S4 i S5"), ["S4", "S5"])


class ProgressTests(unittest.TestCase):
    def phases(self):
        return sb.parse_progress((FIX / "plan.md").read_text(encoding="utf-8"))

    def test_percent_and_current_from_checkpoint(self) -> None:
        p = sb.build_progress(self.phases(), ["S2b", "S3a", "S1"], "in_progress", ["S2a"])
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
        p = sb.build_progress(self.phases(), ["S3a"], "in_progress", ["S2a", "S2b", "S0", "S9"])
        # S0 już [x], S9 nie istnieje — liczą się tylko S2a i S2b; % zostaje po twardym [x]
        self.assertEqual((p["percent"], p["pending"], p["pendingPercent"]), (29, 2, 28))
        self.assertEqual(p["percent"] + p["pendingPercent"], round(100 * 4 / 7))
        self.assertEqual([(ph["pending"], ph["pendingPercent"]) for ph in p["phases"]], [(2, 50), (0, 0), (0, 0)])
        none = sb.build_progress(self.phases(), None, "in_progress")
        self.assertEqual((none["pending"], none["pendingPercent"]), (0, 0))
        self.assertEqual(sb._pending_percent(0, 0, 0), 0)

    def test_fallback_first_unchecked(self) -> None:
        p = sb.build_progress(self.phases(), None, "in_progress")
        self.assertEqual(p["active"], ["S2a"])
        self.assertEqual(p["activeSource"], "first-unchecked")

    def test_no_active_when_not_in_progress(self) -> None:
        p = sb.build_progress(self.phases(), None, "approved")
        self.assertEqual(p["active"], [])
        self.assertIsNone(p["current"])

    def test_empty_plan(self) -> None:
        p = sb.build_progress([], None, "draft")
        self.assertEqual((p["total"], p["percent"]), (0, 0))


class ActTests(unittest.TestCase):
    def acts(self, spec_status, plan_status, spec_exists=True, plan=True):
        spec = {"exists": spec_exists, "status": spec_status, "path": SPEC}
        plan_info = {"status": plan_status, "path": PLAN} if plan else None
        progress = {"total": 4, "done": 1, "percent": 25, "phases": [], "current": None}
        return {a["key"]: a["state"] for a in sb.compute_acts(spec, plan_info, progress, [])}

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
        acts = sb.compute_acts(spec, {"status": "in_progress", "path": PLAN},
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
        self.assertEqual(sb.spec_run_id(self.root, self.SPEC2, {}), "2026-01-21-demo-etap-2")
        self.assertEqual(sb.spec_run_id(self.root, self.SPEC2, {"przebieg": "2026-01-10-demo-etap-1"}),
                         "2026-01-10-demo-etap-1")
        self.assertIsNone(sb.spec_run_id(self.root, "docs/specs/inny.md", {"przebieg": "../../etc"}))


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
        phases = sb.parse_progress(self.PLAN)
        self.assertEqual([[s["id"] for s in p["steps"]] for p in phases], [["F0.1", "F0.2"], ["F1.1", "F1.2", "F1.10"]])
        prog = sb.build_progress(phases, ["F1"], "in_progress")
        self.assertEqual((prog["done"], prog["total"], prog["active"], prog["current"]["id"]), (2, 5, ["F1.1"], "F1.1"))
        self.assertEqual([(p["done"], p["total"], p["state"]) for p in prog["phases"]], [(2, 2, "done"), (0, 3, "active")])
        self.assertEqual(sb.build_progress(phases, ["F1.2"], "in_progress")["active"], ["F1.2"])
        self.assertEqual(sb.ids_in_progress("zrobione: F0.2; w toku: F1.1 i F1.2"), ["F1.1", "F1.2"])

    def test_old_ids_without_a_dot_behave_as_before(self) -> None:
        phases = sb.parse_progress("## Progress\n- [x] S1 A\n- [ ] S2a B\n- [ ] A-1 C\n")
        self.assertEqual([s["id"] for s in phases[0]["steps"]], ["S1", "S2a", "A-1"])
        self.assertEqual(sb.build_progress(phases, ["S2a"], "in_progress")["active"], ["S2a"])


class WaitingTests(ProjectCase):
    """Baner „sesja czeka na Ciebie”: znacznik `czeka:` w checkpoincie i fakty z plików."""
    CP = f"{RUN}/checkpoints.md"

    def add_checkpoint(self, line: str) -> None:
        with (self.root / self.CP).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def test_marker_parsing(self) -> None:
        self.assertEqual(sb.wait_marker("zrobione: S1; w toku: —; czeka: GO implement"), "GO implement")
        self.assertEqual(sb.wait_marker("ryzyko: brak; czeka: akceptacja prototypu demo-etap-1."), "akceptacja prototypu demo-etap-1")
        for text in ("czeka: —", "czeka: brak", "czeka na CP", "w toku: S2", "ryzyko: czeka-y"):
            self.assertIsNone(sb.wait_marker(text), text)

    def test_item_actions_and_links(self) -> None:
        ctx = {"spec": SPEC, "plan": PLAN, "slug": "demo-etap-1"}
        proto = sb.wait_item("akceptacja prototypu demo-etap-2 (3/5)", "checkpoint", "10:50", ctx)
        self.assertEqual(proto["link"], {"kind": "prototype", "slug": "demo-etap-2"})
        self.assertEqual(sb.wait_item("GO implement", "plan", None, ctx)["link"], {"kind": "doc", "path": PLAN})
        self.assertEqual(sb.wait_item("GO plan", "spec", None, ctx)["link"], {"kind": "doc", "path": SPEC})
        self.assertIn("pytania", sb.wait_item("odpowiedź Q1–Q3", "checkpoint", None, ctx)["action"])
        self.assertIn("„CP”", sb.wait_item("CP", "checkpoint", None, ctx)["action"])
        self.assertIn("„READY”", sb.wait_item("READY", "checkpoint", None, ctx)["action"])
        self.assertIn("retrospektywy", sb.wait_item("TAK/NIE run-1/P2", "checkpoint", None, ctx)["action"])

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
        self.assertEqual(s["silence"]["thresholdMin"], sb.SILENCE_MIN)
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
        az = sb.remote_web("git@ssh.dev.azure.com:v3/org/Proj/repo")
        self.assertEqual(az["repo"], "https://dev.azure.com/org/Proj/_git/repo")
        self.assertEqual(sb.remote_web("https://dev.azure.com/org/Proj/_git/repo/")["repo"], az["repo"])
        self.assertEqual(sb.remote_web("git@github.com:Org/repo.git")["repo"], "https://github.com/Org/repo")
        self.assertEqual(sb.remote_web("https://github.com/Org/repo")["host"], "github")
        for bad in ("https://gitlab.com/a/b", "git@ssh.dev.azure.com:v3/o/p/r\"><x", "", None, "file:///tmp/repo"):
            self.assertIsNone(sb.remote_web(bad), bad)

    def write_origin(self, url: str) -> None:
        (self.root / ".git/config").write_text(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n', encoding="utf-8")

    def test_branch_link_only_when_pushed(self) -> None:
        self.write_origin("git@ssh.dev.azure.com:v3/org/Proj/repo")
        g = sb.read_git(self.root)
        self.assertEqual((g["remote"]["host"], g["branchPushed"]), ("azure", False))
        self.assertEqual(g["branchUrl"], "https://dev.azure.com/org/Proj/_git/repo?version=GBfeature%2Fdemo")
        (self.root / ".git/packed-refs").write_text("abc1234def5678 refs/remotes/origin/feature/demo\n", encoding="utf-8")
        self.assertTrue(sb.read_git(self.root)["branchPushed"])
        self.write_origin("git@github.com:Org/repo.git")
        self.assertEqual(sb.read_git(self.root)["branchUrl"], "https://github.com/Org/repo/tree/feature/demo")

    def test_plan_pr_line(self) -> None:
        plan = self.root / PLAN
        plan.write_text(plan.read_text(encoding="utf-8").replace("## Progress", "## Progress\nPR: #12 (link: https://example.com/pr/12)", 1),
                        encoding="utf-8")
        self.assertEqual(self.board.status()["plan"]["pr"], {"number": 12, "url": "https://example.com/pr/12"})
        plan.write_text(plan.read_text(encoding="utf-8").replace("https://example.com/pr/12", "javascript:alert(1)"), encoding="utf-8")
        self.assertIsNone(self.board.status()["plan"]["pr"])


class BoardTests(ProjectCase):
    def test_plan_choice_prefers_in_progress(self) -> None:
        self.assertEqual(sb.choose_plan(sb.list_plans(self.root))["path"], PLAN)

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
            self.assertIsNotNone(sb.resolve_doc(self.root, rel), rel)
        bad = [
            ".env", "docs/secret.md", ".skillsync/config.md", f"{RUN}/notes.txt",
            "../proj/" + SPEC, "docs/specs/../../.env", "/etc/passwd", "docs/specs/../secret.md",
            str(self.root / SPEC), "docs\\specs\\demo-etap-1.md", "docs/specs/nie-ma.md", "", None,
            "docs/specs//demo-etap-1.md", "docs/specs/./demo-etap-1.md", ".git/HEAD",
        ]
        for rel in bad:
            self.assertIsNone(sb.resolve_doc(self.root, rel), rel)

    def test_symlink_escape_rejected(self) -> None:
        outside = self.tmp / "outside.md"
        outside.write_text("# poza projektem\n", encoding="utf-8")
        link = self.root / "docs/specs/link.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("brak symlinków")
        self.assertIsNone(sb.resolve_doc(self.root, "docs/specs/link.md"))

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
                sb.validate_port(port)
        sb.validate_port(5140)
        sb.validate_port(5142)


class ApprovalTests(ProjectCase):
    """Przyciski zgód: „GO plan” (spec approve) i „GO implement” (plan approve) przez CLI, z sha pokazanej wersji."""
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"

    def setUp(self) -> None:
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls.txt"
        fake = self.bin / "skillsync"
        fake.write_text("#!/bin/sh\n"
                        f"echo \"$@\" >> '{self.calls}'\n"
                        "if [ \"$1\" = prototype ]; then echo '{\"prototypes\": []}'; exit 0; fi\n"
                        "if [ \"$3\" = --help ]; then echo 'usage: --expect-sha EXPECT_SHA'; exit 0; fi\n"
                        "echo '{\"status\": \"PASS\", \"sha\": \"after\"}'\n", encoding="utf-8")
        fake.chmod(0o755)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.old_path}"

    def tearDown(self) -> None:
        os.environ["PATH"] = self.old_path
        super().tearDown()

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
        self.assertEqual(acts["spec"]["sha"], sb.git_blob_sha((self.root / self.SPEC2).read_bytes()))
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

    def test_approve_calls_cli_with_sha_and_writes_audit(self) -> None:
        self.spec_act()
        offer = self.actions()["spec"]
        code, body = self.board.approve("spec", self.SPEC2, offer["sha"])
        self.assertEqual((code, body["word"]), (200, "GO plan"), body)
        calls = self.calls.read_text(encoding="utf-8").splitlines()
        self.assertIn(f"spec approve {self.root} {self.SPEC2} --expect-sha {offer['sha']} --json", calls)
        audit = [json.loads(x) for x in (self.root / sb.APPROVALS_LOG).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(a["word"], a["file"], a["sha"], a["source"]) for a in audit],
                         [("GO plan", self.SPEC2, offer["sha"], "dashboard")])

    def test_refusals_never_call_the_cli(self) -> None:
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
        self.assertFalse([c for c in (self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else [])
                          if "--expect-sha" in c])
        off = sb.Board(self.root, prototype_port=1, approvals=False)
        self.assertEqual(off.status()["approvals"], {"enabled": False, "actions": [], "last": None})
        self.assertEqual(off.approve("spec", self.SPEC2, sha)[0], 403)

    def test_old_cli_hides_buttons(self) -> None:
        self.spec_act()
        (self.bin / "skillsync").write_text("#!/bin/sh\necho 'invalid choice' >&2; exit 2\n", encoding="utf-8")
        board = sb.Board(self.root, prototype_port=1)
        state = board.status()["approvals"]
        spec = next(a for a in state["actions"] if a["kind"] == "spec")
        self.assertEqual(spec["state"], "running")
        self.assertIn("spec approve", spec["reason"])
        self.assertEqual(board.approve("spec", self.SPEC2, "0" * 40)[0], 503)

    def test_session_url_and_host_helpers(self) -> None:
        self.assertTrue(sb.session_url_ok("https://claude.ai/code/session_01abc"))
        for bad in ("http://claude.ai/x", "javascript:alert(1)", "https://u:p" + "@" + "claude.ai/", "", None):
            self.assertFalse(sb.session_url_ok(bad), bad)
        self.assertIsNone(sb.Board(self.root, prototype_port=1, session_url="javascript:x").session_url)
        self.assertEqual([sb.request_host(h) for h in ("127.0.0.1:5140", "[::1]:5140", "LOCALHOST", "evil.example:80", None)],
                         ["127.0.0.1", "::1", "localhost", "evil.example", ""])


class DeliveryTests(ProjectCase):
    """Przyciski CP / READY (wariant A): tryb z manifestu, commity ponad bazą, plan done, znacznik `czeka:`."""

    def manifest(self, text: str) -> None:
        path = self.root / sb.MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_read_delivery_forms(self) -> None:
        self.assertEqual(sb.read_delivery(self.root), {"mode": "pr", "base": None, "source": "default"})
        self.manifest("skills: [a]\ndelivery:\n  mode: direct\n  base: develop  # baza\nother: 1\n")
        self.assertEqual(sb.read_delivery(self.root), {"mode": "direct", "base": "develop", "source": "manifest"})
        self.manifest("delivery: {mode: pr, base: 'main'}\n")
        self.assertEqual(sb.read_delivery(self.root)["base"], "main")
        self.manifest("delivery:\n  mode: yolo\n  base: --upload-pack=x\n")
        self.assertEqual(sb.read_delivery(self.root), {"mode": "invalid", "base": None, "source": "manifest"})

    def test_words_follow_mode_and_conditions(self) -> None:
        pr, direct = {"mode": "pr"}, {"mode": "direct"}
        none = {"remote": "origin/feature/x", "pushed": True, "ahead": 0}
        self.assertEqual([(w["word"], w["active"]) for w in sb.delivery_words(pr, none, None, [])],
                         [("CP", False), ("READY", False)])
        self.assertEqual([w["word"] for w in sb.delivery_words(direct, none, None, [])], ["CP"])
        self.assertEqual(sb.delivery_words({"mode": "invalid"}, none, None, []), [])
        ahead = sb.delivery_words(direct, {"remote": "origin/feature/x", "pushed": True, "ahead": 3}, None, [], "feature/x")[0]
        self.assertEqual((ahead["active"], ahead["target"]), (True, "feature/x"))
        self.assertEqual(ahead["tip"], "CP → origin/feature/x; 3 commity niewypchniętych względem origin/feature/x")
        fresh = sb.delivery_words(direct, {"remote": "origin/f", "pushed": False, "ahead": 5}, None, [], "f")[0]
        self.assertIn("5 commitów niewypchniętych względem origin (gałęzi jeszcze nie ma na origin)", fresh["tip"])
        handover = sb.delivery_words(direct, none, {"status": "done"}, [], "feature/x", "main")[0]
        self.assertEqual((handover["target"], handover["tip"].split(";")[0]), ("main", "CP → origin/main (oddanie z feature/x)"))
        self.assertTrue(sb.delivery_words(direct, none, {"status": "done"}, [])[0]["active"])
        waits = [sb.wait_item("READY", "checkpoint")]
        words = {w["word"]: w for w in sb.delivery_words(pr, none, None, waits)}
        self.assertEqual((words["CP"]["active"], words["READY"]["active"]), (False, True))
        self.assertIn("czeka na „READY”", words["READY"]["tip"])

    def test_target_is_the_working_branch_unless_the_plan_says_otherwise(self) -> None:
        self.assertEqual(sb.delivery_target({"mode": "direct"}, "feature/x", {}), "feature/x")
        self.assertEqual(sb.delivery_target({"mode": "direct"}, "feature/x", {"oddanie": "main"}), "main")
        self.assertEqual(sb.delivery_target({"mode": "pr"}, "feature/x", {"oddanie": "main"}), "feature/x")  # pr: nigdy bazy
        self.assertEqual(sb.delivery_target({"mode": "direct"}, "feature/x", {"oddanie": "--evil"}), "feature/x")

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
        self.assertEqual(sb.git_ahead(repo, "feature/x"), {"remote": "origin/feature/x", "pushed": False, "ahead": 2})
        git("update-ref", "refs/remotes/origin/feature/x", "HEAD~1")  # wypchnięty jeden z dwóch
        self.assertEqual(sb.git_ahead(repo, "feature/x"), {"remote": "origin/feature/x", "pushed": True, "ahead": 1})
        self.assertEqual(sb.git_ahead(repo, None), {"remote": None, "pushed": None, "ahead": None})

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

    def test_blocked_and_foreign_requests_write_nothing(self) -> None:
        self.assertEqual(self.board.approve("cp", "2026-01-10-demo-etap-1", self.HEAD)[0], 409)  # bez powodu
        self.wait_for("READY")
        self.assertEqual(self.board.approve("ready", "../../etc", self.HEAD)[0], 409)
        direct = self.root / sb.MANIFEST
        direct.parent.mkdir(parents=True, exist_ok=True)
        direct.write_text("delivery:\n  mode: direct\n", encoding="utf-8")
        self.assertEqual(self.board.approve("ready", "2026-01-10-demo-etap-1", self.HEAD)[0], 409)  # READY tylko w pr
        self.assertFalse((self.root / f"{RUN}/approvals.jsonl").exists())

    def test_read_approvals_skips_bad_lines(self) -> None:
        path = self.root / f"{RUN}/approvals.jsonl"
        path.write_text('{"word": "CP", "sha": "a"}\nnie json\n[1]\n{"word": "READY"}\n', encoding="utf-8")
        self.assertEqual([e["word"] for e in sb.read_approvals(self.root, "2026-01-10-demo-etap-1")], ["CP", "READY"])
        self.assertEqual(sb.read_approvals(self.root, "../x"), [])


class DescriptionTests(ProjectCase):
    """Dymki: każdy krok, akt i bramka ma statyczny opis w descriptions.json; ostrzeżenie pre-implement."""

    def test_every_referenced_key_has_a_description(self) -> None:
        import re as _re
        descs = sb.load_descriptions()
        server = set(_re.findall(r'key="(\w+)"', (SKILL / "scripts/dashboard_server.py").read_text(encoding="utf-8")))
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
        self.assertEqual(sb.risky_sections("## Dane\n\n- nie dotyczy (brak zmian)\n"), [])


class HttpTests(ProjectCase):
    def setUp(self) -> None:
        super().setUp()
        self.port = free_port()
        self.board = sb.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1)
        self.server = sb.make_server(self.board, "127.0.0.1", self.port)
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

    def post(self, data: dict, headers: dict) -> tuple[int, dict]:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/approve", data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_approve_endpoint_guards(self) -> None:
        self.board.cli_supports_approvals = lambda: True  # bez zależności od wersji CLI na maszynie
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
        req = urllib.request.Request(self.base + "/api/status", headers={"Host": "tunel.example"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertFalse(json.load(r)["approvals"]["here"])
        self.board.approve_hosts = sb.LOOPBACK_HOSTS | {"lan-host"}
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertFalse(json.load(r)["approvals"]["here"])
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


if __name__ == "__main__":
    unittest.main()
