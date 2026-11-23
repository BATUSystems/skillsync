"""I18N-1.4/1.5: the dashboard renderer and the page adapter speak the language of the status (`lang`, `texts`).

- The built-in Polish catalog of the renderer (`DashboardRenderer.defaultTexts`) is the `ui.*` part of `pl.json` (the source of truth).
- Texts: `deps.texts` (host seam: live, liveTip, noConnection) > `texts` of the status (only for a language other than pl) > built-in Polish;
  a key missing in `texts` falls back to the built-in Polish text. A status without `lang`/`texts` renders exactly as before.
- English (K2): the renderer fed with `lang: "en"` and `catalog_for_client("en")` draws every contract fixture and a set of extra scenarios
  (all the places that show a text: header, git, acts, forecast, tests, questions, waiting banner, documents, approvals) with no
  Polish character and no leftover of a Polish catalog text; the data of the status (own words of the project) are not translated.
  Every `ui.*` key must be reached by the scenarios (coverage, with a short list of keys that need a state the harness cannot produce).
- Plurals (K3): the form chosen by the renderer equals the form of `i18n.t` for pl (1 / 2-4 / 5+ / 12-14 / 22) and en (1 / rest).
- Injection: parameter values and hostile templates reach the page only as text (the shadow DOM throws on any HTML sink).
- K4: the frozen renderer 1.5.0 ignores `lang` and `texts`.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from skillsync_workflow import common  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import i18n  # noqa: E402
from tests.test_skillsync_dashboard_renderer import (  # noqa: E402
    ADAPTER, HARNESS, NODE, NODE_MAJOR, OLD_RENDERER, RENDERER, fixture_files, ms, strip_js)
from tests.test_skillsync_dashboard_server import HttpCase  # noqa: E402

POLISH = re.compile("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
PARAM = re.compile(r"\{\w+\}")
SKIP = unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
# the shadow DOM and `mount`/`summary` helpers of the renderer test; the scenarios of this file follow them
HEAD = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
OLD_HEAD = HARNESS[:HARNESS.index("  const F = input.fixtures")]


def ui_catalog(lang):
    return i18n.catalog_for_client(lang)


def fold(value):
    """The same JSON with every string folded to ASCII-ish text (`ł` -> `l`, accents dropped): data of the project that has no Polish character."""
    if isinstance(value, str):
        text = value.replace("ł", "l").replace("Ł", "L")
        return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    if isinstance(value, list):
        return [fold(item) for item in value]
    if isinstance(value, dict):
        return {key: fold(item) for key, item in value.items()}
    return value


def clone(value):
    return json.loads(json.dumps(value))


def run_node(source, payload, timeout=180):
    """The JSON a harness prints (stdout); the harness and its input live in a temporary directory with literal names."""
    with tempfile.TemporaryDirectory() as tmp:
        harness, data = Path(tmp) / "harness.cjs", Path(tmp) / "input.json"
        harness.write_text(source, encoding="utf-8")
        data.write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run([NODE, str(harness), str(RENDERER), str(data)], capture_output=True, text=True, timeout=timeout, cwd=tmp)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node rc={proc.returncode}: {proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    if "fatal" in out:
        raise AssertionError(out["fatal"])
    return out


# ---------------------------------------------------------------------------------------------------------- scenarios
def scenarios():
    """(name, status JSON, extra) tuples: the contract fixtures and states that show the rest of the texts. `now` is in ms."""
    fx = fixture_files()
    out = []

    def add(name, base, edit=None, now_minutes=0, deps=None, act=None, preview=False, opts=None):
        data = clone(fx[base])
        if edit:
            edit(data)
        out.append({"name": name, "json": data, "now": ms(data["generatedAt"]) + now_minutes * 60000, "deps": deps or {}, "act": act or [],
                    "preview": preview, "opts": opts or {}})

    for name in fx:
        add("fixture:" + name, name)

    def delivery(data):
        data["git"] = {"branch": "feat/x", "head": "abcdef1234", "headFull": "abcdef1234", "branchUrl": "https://example.test/b", "branchPushed": True, "remote": {"host": "azure"}}
        data["delivery"].update({"mode": "pr", "branch": "feat/x", "remote": "origin/feat/x", "ahead": 3, "pushed": False,
                                 "commits": [{"sha": "abc1234", "subject": "first"}, {"sha": "def5678", "subject": "second"}], "commitsMore": 1})
        data["project"]["worktree"] = {"kind": "worktree", "name": "wt-x", "path": "/p/wt-x", "mainPath": "/p/main"}
    add("git-azure-worktree", "plan-in-progress", delivery)

    def delivery_plain(data):
        data["git"] = {"branch": "feat/y", "head": "abcdef1234", "headFull": "abcdef1234", "branchUrl": "https://example.test/b", "branchPushed": False, "remote": {"host": "github"}}
        data["delivery"].update({"mode": "pr", "branch": "feat/y", "remote": "origin/feat/y", "ahead": 2, "pushed": True, "commits": [], "commitsMore": 0})
        data["project"]["worktree"] = {"kind": "main", "path": "/p/main"}
    add("git-github-main", "plan-in-progress", delivery_plain)

    def delivery_local(data):
        data["git"] = {"branch": "feat/z", "head": "abcdef1234", "headFull": "abcdef1234", "branchUrl": None, "branchPushed": False, "remote": None}
        data["delivery"].update({"ahead": 1, "remote": None, "pushed": True, "commits": [{"sha": "a1", "subject": "only"}], "commitsMore": 0})
        data["project"]["worktree"] = {"kind": "worktree", "path": "/p/x"}
    add("git-local-only", "plan-in-progress", delivery_local)

    def prototype(data):
        data["prototype"] = {"port": 5130, "slug": "demo", "up": True, "writing": {"screensDone": 2, "screensPlanned": 5}}
        data["spec"]["prototypeAcceptance"] = {"accepted": 2, "screens": 5}
        data["prototype"]["commentsAfterAcceptance"] = {"count": 2, "screens": [{"id": "s1"}, {"id": "s2"}]}
        data["acts"][0]["stages"].append({"key": "stage_prototype", "label": "Prototyp", "done": False, "state": "active", "since": "09:00", "tip": "demo"})
        data["acts"][2]["stages"][0].update({"state": "active", "since": "09:10"})
        data["waiting"] = [
            {"what": "writing", "source": "writing", "since": "09:50", "action": "a", "link": {"kind": "prototype", "slug": "demo"}},
            {"what": "GO plan", "source": "spec", "since": None, "action": "b", "word": "GO plan", "who": {"label": "acceptance", "login": "ann"}, "link": {"kind": "doc", "path": "docs/plans/x.md"}},
            {"what": "ready", "source": "plan", "since": "09:55", "action": "c", "word": "READY", "who": {"label": "acceptance", "login": None}, "link": {"kind": "expert", "slug": "demo", "url": "/_experts/demo"}},
            {"what": "p", "source": "prototype", "since": None, "action": "d"}, {"what": "q", "source": "expert", "since": None, "action": "e"},
            {"what": "x", "source": "unknown-source", "since": None, "action": "f"}]
        data["expertQuestions"].update({"open": 2, "blocking": 1, "runs": [{"slug": "demo", "url": "/_experts/demo", "open": 2, "addressees": ["ann", "bob"]}]})
        data["session"] = {"url": "https://example.test/session"}
        data["run"]["parseWarnings"] = [{"time": "10:03", "segment": "report", "fragment": "F1.1 (a; b", "reason": "unbalanced-bracket"},
                                         {"time": "10:04", "segment": "report", "fragment": "x", "reason": "no-step-id"},
                                         {"time": "10:05", "segment": "report", "fragment": "y", "reason": "later-id"}, {"time": "", "segment": "", "fragment": "z", "reason": "odd"}]
    add("prototype-writing-waiting", "plan-in-progress", prototype, now_minutes=60)


    def feedback(data):
        import hashlib

        def draft(n, state="waiting", **over):
            sha = hashlib.sha256(f"F{n}".encode()).hexdigest()
            row = {"draft": f"F{n}", "state": state, "reason": None, "sha": sha, "sha12": sha[:12], "generic": False, "given": None,
                   "view": {"layer": "org", "target": {"organization": "Org", "project": "Proj", "area": "Area", "type": "Task"}, "mode": "create", "item": None, "item_title": None,
                            "title": "Title", "tags": ["a"], "body": "Body", "scan": {"status": "PASS", "coverage": "identity", "warnings": []}, "warning": None}}
            row.update(over)
            return row
        view_comment = {"layer": "org", "target": {"organization": "Org", "project": "Proj", "area": "Area", "type": "Task"}, "mode": "comment", "item": 7, "item_title": "Quoted",
                        "title": "Reply", "tags": [], "body": "Body", "scan": {"status": "PASS", "coverage": "generic", "warnings": ["note"]}, "warning": "careful"}
        rows = [draft(40, generic=True, view=view_comment), draft(39), draft(38, "after", given={"at": "2026-01-12T08:30:15.000000Z", "source": "dashboard"}),
                draft(37, "stale", reason="content changed", view=None), draft(36, "blocked", reason="scan blocked"), draft(35, "sent", view=None),
                draft(34, view=None)]
        rows += [draft(n) for n in range(33, 23, -1)]
        data.setdefault("approvals", {})["here"] = True
        data["feedback"] = {"enabled": True, "total": len(rows), "drafts": rows}
    add("feedback", "plan-in-progress", feedback, act=["feedback"], opts={"feedback": True})
    out[-1]["currentOnly"] = True   # the feedback block exists only in the current renderer: the compat runs of the frozen renderers draw it without the block and click nothing

    def prototype_down(data):
        data["prototype"] = {"port": 5130, "slug": "demo", "up": False, "writing": None}
        data["spec"]["prototypeAcceptance"] = {"accepted": 1, "screens": 3}
        data["expertQuestions"].update({"open": 1, "blocking": 0, "runs": [{"slug": "a", "addressees": "all"}, {"slug": "b", "addressees": []}]})
        data["waiting"] = [{"what": "a", "source": "writing", "since": "09:50", "action": "a"}]
    add("prototype-down", "plan-in-progress", prototype_down)

    def given(data):
        data["spec"]["status"] = "approved"
        data["approvals"]["actions"] = []
        data["waiting"] = [{"what": "GO plan", "source": "checkpoint", "since": "10:00", "action": "x", "word": "GO plan", "approve": "spec"}]
    add("consent-given", "spec-act", given)

    def forecast_range(data):
        data["forecast"] = {"scope": "plan", "lowSeconds": 600, "medianSeconds": 900, "highSeconds": 4000, "remaining": 3, "samples": 4, "paused": False}
        data["progress"].update({"pending": 1, "pendingPercent": 20, "reported": ["F1.3"], "parallel": 2, "activePercent": 20, "active": ["F1.3", "F2.1"], "activeSource": "plan"})
        data["progress"]["phases"][0]["steps"][2]["state"] = "reported"
    add("forecast-range", "plan-in-progress", forecast_range)

    def forecast_paused(data):
        data["forecast"] = {"scope": "plan", "lowSeconds": 3600, "medianSeconds": 3700, "highSeconds": 3700, "remaining": 1, "samples": 1, "paused": True}
        data["progress"].update({"pending": 1, "pendingPercent": 20, "reported": ["F1.3"]})
    add("forecast-paused", "plan-in-progress", forecast_paused)
    add("forecast-tempo", "plan-in-progress", forecast_range, deps={"forecastView": "tempo"})
    add("forecast-tempo-short", "plan-in-progress", lambda data: data.update(forecast={"scope": "plan", "lowSeconds": 60, "medianSeconds": 90, "highSeconds": 120, "remaining": 2, "samples": 2, "paused": True}), deps={"forecastView": "tempo"})
    for reason in ("few-moments", "no-run-start"):
        add("forecast-" + reason, "plan-in-progress", lambda data, reason=reason: data.update(forecast={"scope": "plan", "reason": reason}))

    def timing(data):
        data["timing"]["acts"] = [{"key": "spec", "unknown": False, "workSeconds": 4000, "waitSeconds": 120, "startedAt": "2026-01-11T09:00:00+00:00", "endedAt": "2026-01-11T10:05:00+00:00"},
                                  {"key": "plan", "unknown": True, "reason": "open-window", "startedAt": "2026-01-11T09:00:00+00:00", "endedAt": None},
                                  {"key": "real", "unknown": True, "reason": "no-approval", "startedAt": "2026-01-11T09:00:00+00:00", "endedAt": None}]
        data["timing"]["windows"] = [{"what": "a", "startedAt": "2026-01-11T09:00:00+00:00", "endedAt": "2026-01-11T09:20:00+00:00", "seconds": 1200, "source": "approval"},
                                     {"what": "b", "startedAt": "2026-01-11T09:30:00+00:00", "endedAt": "2026-01-11T09:40:00+00:00", "seconds": 600, "source": "checkpoint"},
                                     {"what": "c", "startedAt": "2026-01-11T10:00:00+00:00", "unknown": True}]
        data["timing"]["totals"] = {"workSeconds": 4000, "waitSeconds": 100, "complete": False, "unknownWindows": 1}
        for act in data["acts"]:
            if act["key"] == "real":
                act["state"] = "active"
    add("timing-variants", "timing", timing)
    add("timing-unknown-reason", "timing", lambda data: data["timing"]["acts"].append({"key": "plan", "unknown": True, "reason": "x", "startedAt": "2026-01-11T09:00:00+00:00"}))
    add("eta-over-hours", "eta", now_minutes=200)
    add("eta-over-minutes", "eta", now_minutes=70)
    add("eta-before", "eta", now_minutes=-3000)
    def stage_over(data):
        prototype(data)
        data["eta"] = dict(clone(fx["eta"])["eta"], untilIso=(datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00")) - timedelta(minutes=10)).isoformat())
    add("eta-prototype-stage-over", "plan-in-progress", stage_over)
    add("eta-paused", "eta", lambda data: data["eta"].update(paused=True) or data.__setitem__("waiting", [{"what": "odpowiedź Q1", "source": "checkpoint", "since": "11:20", "action": "x", "link": None}]), now_minutes=-10)
    add("eta-paused-user", "eta", lambda data: data["eta"].update(paused=True), now_minutes=-10)
    add("eta-shifted", "eta", lambda data: data["eta"].update(shiftSeconds=1800, untilOriginalIso="2026-01-11T11:10:00+00:00"), now_minutes=-10)
    add("eta-deviation", "eta", lambda data: data.__setitem__("estimates", [
        {"steps": ["F2.1"], "operation": "a", "state": "late", "adjustedErrorSeconds": 900, "estimatedSeconds": 1200, "actualSeconds": 2100, "userWaitSeconds": 300},
        {"steps": ["F2.1"], "operation": "b", "state": "early", "adjustedErrorSeconds": -300, "estimatedSeconds": 1200, "actualSeconds": 900, "userWaitSeconds": 0}]), now_minutes=-10)
    add("silence-late", "silence", now_minutes=75)

    # N16: sub-mind runs and rounds of review from the journal (`agents`): a live round with a shifted ETA, a stale run, a finished one with a report link
    def agents_iso(data, minutes):
        return (datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00")) + timedelta(minutes=minutes)).isoformat()

    def agents_live(data):
        data["run"]["files"] = list(data["run"].get("files") or []) + [".skillsync/runs/demo/code-review-F2.1-1.md"]
        data["agents"] = {"running": [
            {"agent": "a-3", "role": "reviewer", "kind": "review", "profile": "code", "step": "F2.1", "round": 2, "startedAt": agents_iso(data, -20), "eta": "15:05",
             "etaFirst": "14:35", "etaIso": agents_iso(data, 25), "etaShifts": 2},
            {"agent": "a-4", "role": "implementer", "kind": "run", "profile": None, "step": None, "round": None, "startedAt": agents_iso(data, -30), "eta": "14:00",
             "etaFirst": "14:00", "etaIso": agents_iso(data, -10), "etaShifts": 0}],
            "recent": [{"agent": "a-2", "role": "reviewer", "kind": "review", "profile": "code", "step": "F2.1", "round": 1, "startedAt": agents_iso(data, -90), "endedAt": agents_iso(data, -60),
                        "seconds": 1800, "etaIso": agents_iso(data, -65), "result": "CHANGES_REQUIRED", "report": "code-review-F2.1-1.md"}],
            "omitted": {"running": 1, "recent": 2}}
        data["silence"] = {"since": agents_iso(data, -60), "thresholdMin": 35}
    add("agents-live-round", "plan-in-progress", agents_live)

    def agents_stale(data):
        data["agents"] = {"running": [{"agent": "a-1", "role": "reviewer", "kind": "review", "profile": "code", "step": None, "round": None, "startedAt": agents_iso(data, -200),
                                       "eta": None, "etaFirst": None, "etaIso": None, "etaShifts": 0}], "recent": [], "omitted": {"running": 0, "recent": 0}}
    add("agents-stale-round", "plan-in-progress", agents_stale)

    def agents_spec_round(data):
        data["agents"] = {"running": [{"agent": "a-1", "role": "reviewer", "kind": "review", "profile": "spec", "step": None, "round": None, "startedAt": agents_iso(data, -5),
                                       "eta": None, "etaFirst": None, "etaIso": None, "etaShifts": 0}], "recent": [], "omitted": {"running": 0, "recent": 0}}
        data["silence"] = {"since": agents_iso(data, -60), "thresholdMin": 35}
    add("agents-spec-round-no-number", "spec-act", agents_spec_round)

    def tests(data):
        counts = {"total": 6, "passed": 2, "failed": 1, "skipped": 1, "running": 1, "waiting": 1}
        group = {"name": "TestSuite", "state": "failed", "total": 5, "counts": {"passed": 1, "failed": 1}, "tests": [{"name": "t1", "state": "passed", "ms": 12}, {"name": "t2", "state": "failed", "msg": "boom"},
                                                                                                           {"name": "t3", "state": "skipped"}, {"name": "t4", "state": "running"}, {"name": "t5", "state": "waiting"}]}
        data["tests"] = {"failedTotal": 1, "runningTotal": 1, "skipped": [{"path": "p%d" % i, "reason": "unreadable" if i % 2 else "not a step directory"} for i in range(7)],
                         "sections": [
                             {"step": {"id": "F1.1", "title": "Contract"}, "state": "failed", "attempts": [
                                 {"n": 1, "state": "failed", "levels": "tests", "counts": counts, "startedAt": "2026-01-11T09:00:00+00:00", "durationMs": 65000, "groups": [dict(group, total=8)], "groupsOmitted": 2, "groupsTotal": 4, "truncated": True, "reason": "x"},
                                 {"n": 2, "state": "running", "levels": "counts", "live": "completion", "counts": {"total": 3, "passed": 1}, "groups": []},
                                 {"n": 3, "state": "passed", "levels": "command", "counts": {}, "groups": []}, {"n": 4, "state": "interrupted", "counts": {}, "groups": []}]},
                             {"step": {"id": "F1.2", "title": "Cmd"}, "state": "interrupted", "attempts": [{"n": 1, "state": "passed", "levels": "tests", "exit": 3, "counts": {"total": 1, "passed": 1}, "command": "run x", "groups": []}]},
                             {"step": {"id": "F2.1", "title": "Run"}, "state": "running", "attempts": [{"n": 1, "state": "running", "levels": "tests", "counts": {"total": 2, "running": 2}, "groups": [group]}]},
                             {"step": None, "state": "passed", "attempts": [{"n": 1, "state": "passed", "levels": "tests", "counts": {"total": 1, "passed": 1}, "groups": []}]}]}
        data["progress"]["phases"][0]["steps"][0]["tests"] = {"state": "failed", "attempt": 1, "counts": counts}
        data["progress"]["phases"][0]["steps"][1]["tests"] = {"state": "passed", "attempt": 1, "counts": {"total": 1, "passed": 1}}
    add("tests-tab", "plan-in-progress", tests, act=["tests-open", "tests-more"], opts={"loadTests": "ok"})
    add("tests-tab-fail", "plan-in-progress", tests, act=["tests-open", "tests-more"], opts={"loadTests": "fail"})
    add("tests-skipped-many", "plan-in-progress", lambda data: data.update(tests={"failedTotal": 0, "sections": [], "skipped": [{"path": "p%d" % i} for i in range(20)]}))
    add("tests-skipped-only", "plan-in-progress", lambda data: data.update(tests={"failedTotal": 0, "sections": [], "skipped": [{"path": "tests"}]}))
    add("tests-skipped-one", "plan-in-progress", lambda data: data.update(tests={"failedTotal": 0, "sections": [], "skipped": [{"path": "other", "reason": "weird"}]}))

    def result(data):
        data["result"] = {"text": "\n".join("line %d" % i for i in range(20)), "path": "docs/plans/x.md", "html": "<p>x</p>", "moreHtml": "<p>y</p>", "moreLines": 8}
    add("result-rich", "plan-in-progress", result)
    add("result-rich-nocount", "plan-in-progress", lambda data: (result(data), data["result"].update(moreLines=0)))

    def result_plain(data):
        data["result"] = {"text": "\n".join("line %d" % i for i in range(20)), "path": "docs/plans/x.md"}
    add("result-plain", "plan-in-progress", result_plain)

    def questions(data):
        proto = {"slug": "run-alpha", "screens": 3, "accepted": 1, "stale": 1, "iteration": 2, "threads": {"open": 2, "awaiting": 1}, "commentsAfterAcceptance": {"count": 2, "screens": []},
                 "writing": {"screensDone": 1, "screensPlanned": 3}}
        data["expertQuestions"]["current"]["prototypes"] = [proto, {"slug": "run-beta", "screens": 1, "accepted": 0, "writing": {"screensDone": 2}, "threads": {}}]
        now = datetime.fromisoformat(data["generatedAt"])
        ages = [("qn-c", timedelta(seconds=3), ["ann"]), ("qn-e", timedelta(seconds=30), None), ("qn-f", timedelta(minutes=20), None), ("qn-g", timedelta(hours=5), None), ("qn-h", timedelta(days=3), None)]
        for qid, age, who in ages:
            data["expertQuestions"]["current"]["items"].append({"id": qid, "runSlug": "run-alpha", "text": "x" * 200 if qid == "qn-c" else "text", "blocking": False,
                                                                "addressees": who or [], "state": "answered", "at": (now - age).isoformat(), "url": "/_experts/run-alpha"})
        data["expertQuestions"]["current"]["items"].append({"id": "qn-d", "text": None, "state": "withdrawn", "addressees": "all", "at": "broken"})
    add("questions-tab", "questions", questions)
    add("questions-empty", "questions", lambda data: data["expertQuestions"].update(current={"open": 0, "items": [], "prototypes": []}))
    add("ago", "plan-in-progress", now_minutes=5)
    add("ago-now", "plan-in-progress", lambda data: data.update(lastUpdate=data["generatedAt"]))
    add("approve-spec", "questions", lambda data: data["approvals"].update(here=True))
    add("no-phases", "plan-in-progress", lambda data: data["progress"].update(phases=[]))
    add("proto-up-no-acceptance", "plan-in-progress", lambda data: data.update(prototype={"port": 5130, "slug": "demo", "up": True, "writing": None}))

    def stages(data):
        data["run"]["lastReportAt"] = data["generatedAt"]
        data["silence"] = None
        data["prototype"] = {"port": 5130, "slug": "demo", "up": True, "writing": {"screensDone": 1}}
        data["acts"][0]["stages"].append({"key": "stage_prototype", "label": "Prototyp", "done": False, "state": "active", "since": "", "tip": "demo"})
        data["acts"][2]["stages"][0].update({"state": "active", "since": "09:10"})
        data["acts"][2]["stages"][1].update({"state": "active", "since": ""})
    add("stages-active-calm", "plan-in-progress", stages)

    def stages_quiet(data):
        data["acts"][2]["stages"][0].update({"state": "active", "since": ""})
    add("stages-active-quiet", "plan-in-progress", stages_quiet)

    def statuses(data):
        data["plan"]["status"] = "implemented"
        data["docs"][0]["items"] += [{"path": "docs/x%d.md" % i, "title": "Plan: X", "status": status, "mtime": "2026-01-11T10:00:00+00:00"} for i, status in enumerate(("draft", "done", "weird", None))]
    add("statuses", "plan-in-progress", statuses)
    add("plan-manual", "plan-in-progress", lambda data: data.update(planAuto=False))
    add("ago-seconds", "plan-in-progress", lambda data: data.update(lastUpdate="2026-01-12T11:59:40+00:00"))
    add("ago-minutes", "plan-in-progress", lambda data: data.update(lastUpdate="2026-01-12T11:30:00+00:00"))
    add("approve-flow", "cp-ready", lambda data: data["approvals"].update(here=True), act=["approve"], deps={"hasApproveToken": True})
    add("viewer", "cp-ready", lambda data: data["approvals"].update(here=True, viewer=True))
    add("token-missing", "cp-ready", lambda data: data["approvals"].update(here=True, tokenRequired=True), deps={"hasApproveToken": False})
    add("live-states", "plan-in-progress", act=["live"], deps={"texts": {"live": {"live": "Host live"}, "liveTip": {"down": "Host down"}, "noConnection": "Host offline"}})
    add("live-states-plain", "plan-in-progress", act=["live"])
    add("docs", "plan-in-progress", act=["docs"], preview=True)
    add("docs-fail", "plan-in-progress", act=["docs-fail"], preview=True)
    return out


JS_TAIL = r"""
(async () => {
  const out = { runs: {}, used: [], violations, parity: {}, injection: {} };
  const used = new Set();
  const en = input.catalogs.en;
  const recording = (cat) => new Proxy(cat, { get(target, key) { if (typeof key === 'string') used.add(key); return target[key]; } });
  const resolve = (json) => { const j = JSON.parse(JSON.stringify(json)); if (j.texts === '@en') j.texts = recording(en); return j; };
  const SHOW = ['data-tip', 'aria-label', 'aria-valuetext', 'title', 'data-desc-extra'];
  const dump = (root) => {
    const parts = [];
    walk(root, (n) => { if (n instanceof T) parts.push(n.data); else if (n instanceof E) for (const k of SHOW) if (n.attrs.has(k)) parts.push(n.attrs.get(k)); });
    return parts.join('\n');
  };
  const deps0 = { prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/`, prototypeOrigin: (port) => `http://127.0.0.1:${port}`,
    scroll: { save: () => 0, restore: () => {}, reveal: () => {}, toTop: { subscribe: (cb) => { cb({ y: 0, viewport: 0 }); return () => {}; }, go: () => {} } } };
  for (const sc of input.scenarios) {
    const json = resolve(sc.json);
    const noFeedback = sc.currentOnly && !input.withCurrentOnly;   // the compat tests (frozen renderers, golden snapshot of 1.12.0) do not pass the flag
    if (noFeedback) delete json.feedback;
    const deps = Object.assign({}, deps0, sc.deps);
    if (sc.deps.hasApproveToken !== undefined) { const has = sc.deps.hasApproveToken; deps.hasApproveToken = () => has; }
    const actions = {};
    if (sc.opts.loadTests === 'ok') actions.loadTests = () => Promise.resolve({ tests: [{ name: 'more', state: 'passed' }] });
    if (sc.opts.loadTests === 'fail') actions.loadTests = () => Promise.reject(new Error('x'));
    if (sc.opts.feedback) actions.feedbackApprove = () => Promise.resolve({ ok: true, already: false, sha12: 'abcdefabcdef' });
    let docMtime = '2026-01-12T10:00:00Z';
    if (sc.act.includes('docs')) actions.loadDoc = (p) => Promise.resolve({ path: p, status: 'draft', mtime: docMtime, html: '<p>x</p>', truncated: true });
    if (sc.act.includes('docs-fail')) actions.loadDoc = () => Promise.reject(new Error('disk gone'));
    const m = mount(json, { now: sc.now, deps, preview: sc.preview, actions, theme: true });
    let snap = dump(m.root) + '\n' + m.calls.titles.join('\n');
    if (sc.act.includes('live')) {
      for (const mode of ['live', 'polling', 'down', 'connecting']) { m.board.setLive(mode); snap += '\nLIVE:' + mode + '|' + text(m.root.querySelector('.live-text')) + '|' + m.root.querySelector('#live').getAttribute('data-tip'); }
      snap += '\nHTTP:' + m.board.t('ui.httpError', { status: 502 });
    }
    if (sc.act.includes('tests-open')) {
      const n = m.root.querySelectorAll('.tsec-head').length;
      for (let i = 0; i < n; i++) { const head = m.root.querySelectorAll('.tsec-head')[i]; if (head.getAttribute('aria-expanded') === 'false') head.click(); }
      snap += '\n' + dump(m.root);
      const buttons = m.root.querySelectorAll('.tatt button').length;
      for (let i = 0; i < buttons; i++) { m.root.querySelectorAll('.tatt button')[i].click(); snap += '\n' + dump(m.root); }
      if (buttons) m.root.querySelectorAll('.tatt button')[0].click();
      out.cmdFailedNote = text(m.root.querySelector('.cmd-failed-note'));
    }
    if (sc.act.includes('tests-more')) {
      const more = m.root.querySelector('.tmore');
      if (more) { more.click(); await tick(); await tick(); snap += '\n' + dump(m.root); }
    }
    if (sc.act.includes('approve')) {
      const btn = () => m.root.querySelector('#acts button[data-approve="cp"]');
      btn().click(); snap += '\n' + dump(m.root) + '\n' + text(btn());
      btn().click(); snap += '\n' + text(m.root.querySelector('#acts')); await tick(); await tick(); snap += '\n' + dump(m.root);
      m.board.setNotice({ ok: false, word: 'CP', text: 'refused' }); snap += '\n' + dump(m.root);
      const fail = mount(resolve(sc.json), { now: sc.now, deps, actions: { approve: () => Promise.reject(new Error('x')) } });
      const fb = () => fail.root.querySelector('#acts button[data-approve="cp"]');
      fb().click(); fb().click(); await tick(); await tick(); snap += '\n' + dump(fail.root);
      fail.board.destroy();   // the error toast holds a real 30 s timer: without this Node waits for it before it exits (the whole run took 30 s)
    }
    if (sc.act.includes('feedback') && !noFeedback) {
      const openFb = (mm, id) => mm.root.querySelector('.fb-card[data-draft="' + id + '"] button.fb-approve').click();
      const fbDialog = (mm) => mm.root.querySelector('[role="dialog"]');
      openFb(m, 'F40'); snap += '\n' + dump(m.root);
      const ack = fbDialog(m).querySelector('input.fb-ack'); ack.checked = true; ack.dispatchEvent(new Ev('change'));
      fbDialog(m).querySelector('button.fb-confirm').click(); snap += '\n' + dump(m.root);
      await tick(); await tick(); snap += '\n' + dump(m.root);
      const refuse = mount(resolve(sc.json), { now: sc.now, deps, actions: { feedbackApprove: () => Promise.resolve({ ok: false, text: 'refused' }) } });
      openFb(refuse, 'F39'); fbDialog(refuse).querySelector('button.fb-confirm').click(); await tick(); await tick(); snap += '\n' + dump(refuse.root);
      refuse.board.destroy();
    }
    if (sc.act.includes('docs')) {
      m.board.setView('docs'); await tick(); await tick(); snap += '\n' + dump(m.root);
      docMtime = '2026-01-12T11:00:00Z'; m.board.update(resolve(sc.json)); await tick(); await tick(); snap += '\n' + dump(m.root);
    }
    if (sc.act.includes('docs-fail')) { m.board.setView('docs'); await tick(); await tick(); snap += '\n' + dump(m.root); }
    out.runs[sc.name] = snap;
    m.board.destroy();
  }
  // plural parity: the form chosen for n in both languages
  const base = (n, lang) => {
    const j = JSON.parse(JSON.stringify(input.parityBase));
    j.lang = lang; if (lang === 'en') j.texts = en;
    j.delivery.ahead = n; j.run.parseWarnings = Array.from({ length: n }, (_, i) => ({ time: '10:0' + (i % 10), segment: 's', fragment: 'f', reason: 'later-id' }));
    return j;
  };
  for (const lang of ['pl', 'en']) {
    out.parity[lang] = {};
    for (const n of input.parityNs) {
      const m = mount(base(n, lang), { now: input.parityNow });
      out.parity[lang][n] = { ahead: text(m.root.querySelector('#meta .ahead-chip')), skipped: text(m.root.querySelector('#parseWarn span')) };
      m.board.destroy();
    }
  }
  // injection: hostile template and hostile parameters stay text; backticks of a parameter never make a code fragment
  {
    const hostile = Object.assign({}, en, { 'ui.checkpoints.empty': 'None <script>alert(1)</script> `<img src=x onerror=1>` {file} `{file}`', 'ui.foot.project': 'project: `{path}` / {path}' });
    const j = JSON.parse(JSON.stringify(input.parityBase));
    j.lang = 'en'; j.texts = hostile; j.run.checkpoints = [];
    j.project.path = '<script>alert(1)</script>`x`{n}';
    const m = mount(j, { now: input.parityNow });
    const empty = m.root.querySelector('#feed .empty');
    out.injection = { empty: text(empty), emptyMono: empty.querySelectorAll('.mono').__asArray().map(text), foot: text(m.root.querySelector('#foot')),
      footMono: m.root.querySelectorAll('#foot .mono').__asArray().map(text), scripts: m.root.querySelectorAll('script').length + m.root.querySelectorAll('img').length };
  }
  out.used = [...used].sort();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@SKIP
class EnglishRenderer(unittest.TestCase):
    """The renderer fed with the English catalog: no Polish text anywhere (K2), every `ui.*` key reached, plurals as the Python catalog."""

    @classmethod
    def setUpClass(cls):
        cls.en = ui_catalog("en")
        cls.pl = ui_catalog("pl")
        cls.base = scenarios()
        cls.runs = {}
        cls.exceptions = {}
        payload_scenarios = []
        for sc in cls.base:
            raw = clone(sc)
            folded = clone(sc)
            for item in (raw, folded):
                item["json"]["lang"] = "en"
                item["json"]["texts"] = "@en"
            folded["json"] = {**fold({k: v for k, v in sc["json"].items()}), "lang": "en", "texts": "@en"}
            folded["name"] = sc["name"] + "|folded"
            payload_scenarios += [raw, folded]
        fx = fixture_files()
        parity_base = clone(fx["plan-in-progress"])
        parity_base["git"] = {"branch": "feat/x", "head": "abcdef1234", "headFull": "abcdef1234", "branchUrl": None, "branchPushed": True, "remote": None}
        parity_base["delivery"].update({"mode": "pr", "branch": "feat/x", "remote": "origin/feat/x", "pushed": True, "commits": [], "commitsMore": 0})
        cls.parity_ns = list(range(1, 131))
        cls.out = run_node(HEAD + JS_TAIL, {"fixtures": {}, "now": {}, "scenarios": payload_scenarios, "withCurrentOnly": True, "catalogs": {"en": cls.en, "pl": cls.pl},
                                            "parityBase": parity_base, "parityNs": cls.parity_ns, "parityNow": ms(parity_base["generatedAt"])})

    def data_text(self, name):
        """The text of the data of a scenario (the words of the project that the renderer shows as they are)."""
        sc = next(item for item in self.base if item["name"] == name.split("|")[0])
        data = fold(sc["json"]) if name.endswith("|folded") else sc["json"]
        return json.dumps({k: v for k, v in data.items() if k not in ("texts",)}, ensure_ascii=False)

    def test_no_script_html_or_style_sink_was_touched(self):
        self.assertEqual(self.out["violations"], [])

    def test_every_scenario_rendered(self):
        self.assertEqual(sorted(self.out["runs"]), sorted(sc["name"] + suffix for sc in self.base for suffix in ("", "|folded")))
        for name, snap in self.out["runs"].items():
            self.assertGreater(len(snap), 50, name)

    def test_english_run_of_folded_data_has_no_polish_character_at_all(self):
        for name, snap in self.out["runs"].items():
            if name.endswith("|folded"):
                with self.subTest(scenario=name):
                    self.assertEqual(sorted(set(POLISH.findall(snap))), [], "a Polish character in the page text of an English status")

    def test_english_run_has_polish_characters_only_in_the_data_of_the_project(self):
        for name, snap in self.out["runs"].items():
            if name.endswith("|folded"):
                continue
            data = self.data_text(name)
            data_words = set(re.findall(r"\w*[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]\w*", data))
            with self.subTest(scenario=name):
                # the page text of neighbouring elements is joined without a separator (`kodZamknięcie`): a word is data when it contains a data word
                leaks = [word for word in re.findall(r"\w*[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]\w*", snap) if not any(own in word for own in data_words)]
                self.assertEqual(sorted(set(leaks)), [])

    def test_no_text_of_the_polish_catalog_remains(self):
        """Every constant fragment (>= 6 characters) of a Polish `ui.*` text is absent from the English output, unless it is also English text or data."""
        english = "\n".join(text for value in self.en.values() for text in (value.values() if isinstance(value, dict) else [value]))
        fragments = set()
        for value in self.pl.values():
            for text in (value.values() if isinstance(value, dict) else [value]):
                for part in re.split(r"\{\w+\}|`", text):
                    if len(part.strip()) >= 6 and part not in english:
                        fragments.add(part)
        self.assertGreater(len(fragments), 200)
        for name, snap in self.out["runs"].items():
            data = self.data_text(name)
            with self.subTest(scenario=name):
                found = sorted(part for part in fragments if part in snap and part not in data)
                self.assertEqual(found, [])

    def test_every_ui_key_is_reached_by_the_scenarios(self):
        """The coverage of the English run: a key that no scenario reaches would be a text that this test never looked at."""
        used = set(self.out["used"])
        # `ui.title.loading` is shown before the first status, i.e. before the renderer knows the language: only its Polish text is ever drawn
        # `ui.page.title` is the `<title>` of the page: the server writes it into `index.html` (`page_with_language`), the renderer never draws it
        unused = sorted(set(self.en) - used - {"ui.title.loading", "ui.page.title"})
        self.assertEqual(unused, [], "keys not reached by any scenario (add a scenario that shows them)")
        self.assertEqual(sorted(used - set(self.en)), [], "the renderer asked for a key that the catalog does not have")

    def test_plural_forms_equal_the_catalog_rules_for_both_languages(self):
        for lang in ("pl", "en"):
            for n in self.parity_ns:
                got = self.out["parity"][lang][str(n)]
                with self.subTest(lang=lang, n=n):
                    self.assertEqual(got["ahead"], i18n.t(lang, "ui.ahead.label", n=n))
                    self.assertEqual(got["skipped"], i18n.t(lang, "ui.checkpoints.skipped", n=n) + " (" + i18n.t(lang, "ui.parseReason.laterId") + ")")

    def test_plural_forms_of_the_named_numbers(self):
        pl = {1: "1 niewypchnięty", 2: "2 niewypchnięte", 4: "4 niewypchnięte", 5: "5 niewypchniętych", 12: "12 niewypchniętych", 14: "14 niewypchniętych",
              22: "22 niewypchnięte", 100: "100 niewypchniętych", 102: "102 niewypchnięte"}
        for n, want in pl.items():
            self.assertEqual(self.out["parity"]["pl"][str(n)]["ahead"], want)
        self.assertEqual(self.out["parity"]["pl"]["1"]["skipped"], "1 fragment raportu pominięto (id kroku nie na początku elementu)")
        self.assertEqual(self.out["parity"]["pl"]["3"]["skipped"], "3 fragmenty raportu pominięto (id kroku nie na początku elementu)")
        self.assertEqual(self.out["parity"]["pl"]["5"]["skipped"], "5 fragmentów raportu pominięto (id kroku nie na początku elementu)")
        self.assertEqual(self.out["parity"]["en"]["1"]["skipped"], "1 report fragment skipped (the step id is not at the start of the item)")
        for n in (2, 5, 21, 101):
            self.assertEqual(self.out["parity"]["en"][str(n)]["skipped"], "%d report fragments skipped (the step id is not at the start of the item)" % n)

    def test_hostile_template_and_parameters_stay_text(self):
        got = self.out["injection"]
        self.assertEqual(got["scripts"], 0)
        self.assertEqual(got["empty"], "None <script>alert(1)</script> <img src=x onerror=1> {file} {file}")
        self.assertEqual(got["emptyMono"], ["<img src=x onerror=1>", "{file}"])   # code fragments of the template only; `{file}` has no parameter, so it stays
        self.assertEqual(got["footMono"], ["<script>alert(1)</script>`x`{n}"])   # one fragment, the backticks of the value are part of its text
        self.assertIn("project: <script>alert(1)</script>`x`{n} / <script>alert(1)</script>`x`{n}", got["foot"])   # the parameter is not re-read: `{n}` stays

    def test_command_failed_sentence_is_a_visible_body_line_with_the_exit_code(self):
        """The intent of the former source assertions: the exit code is in the sentence and the full sentence is a visible line of the body."""
        note = self.out["cmdFailedNote"]
        self.assertEqual(note, "exit code 3, no test reported a failure (an exception outside the reported tests); the ok results cover only the tests that managed to run")
        self.assertEqual(i18n.t("pl", "ui.tests.cmdFailedTip", exit=3),
                         "kod 3, żaden test nie zgłosił porażki (wyjątek poza raportowanymi testami); wyniki ok dotyczą tylko testów, które zdążyły się wykonać")

    def test_live_state_texts_follow_the_host_seam_then_the_catalog(self):
        snap = self.out["runs"]["live-states"]
        self.assertIn("LIVE:live|Host live|Changes in the plan, spec and checkpoints appear at once (SSE).", snap)
        self.assertIn("LIVE:polling|Refreshing every 5 s|The live channel is unavailable", snap)
        self.assertIn("LIVE:down|No connection|Host down", snap)
        self.assertIn("LIVE:connecting|Connecting…|Connecting to the status server…", snap)
        self.assertIn("HTTP:Error 502", snap)


@SKIP
class HostTextsFunction(unittest.TestCase):
    """`deps.texts` as a function `(lang) => object`, read lazily at every use; a bad function or result means no override."""

    def test_function_follows_the_language_and_bad_results_fall_back(self):
        fx = fixture_files()
        data = clone(fx["plan-in-progress"])
        payload = {"fixtures": {}, "now": {}, "data": data, "nowMs": ms(data["generatedAt"]), "en": ui_catalog("en")}
        tail = r"""
(async () => {
  const base = () => JSON.parse(JSON.stringify(input.data));
  const seen = [];
  const bag = { en: { live: { live: 'EN live' }, liveTip: { down: 'EN down tip' }, noConnection: 'EN offline' },
                pl: { live: { live: 'PL live' }, liveTip: { down: 'PL down tip' }, noConnection: 'PL offline' } };
  const fn = (lang) => { seen.push(lang); return bag[lang] || {}; };
  const probe = (m) => { const o = []; for (const mode of ['live', 'down']) { m.board.setLive(mode); o.push(text(m.root.querySelector('.live-text')) + '|' + m.root.querySelector('#live').getAttribute('data-tip')); } return o; };
  const out = {};
  const m = mount(Object.assign(base(), { lang: 'en', texts: input.en }), { now: input.nowMs, deps: { texts: fn } });
  out.en = probe(m);
  m.board.update(base());   // no `lang` = pl: the labels follow the new language
  out.pl = probe(m);
  out.seen = Array.from(new Set(seen));
  const stat = mount(Object.assign(base(), { lang: 'en', texts: input.en }), { now: input.nowMs, deps: { texts: bag.en } });
  out.static = probe(stat);
  const bads = { throws: () => { throw new Error('boom'); }, nul: () => null, arr: () => [], str: () => 'x', empty: () => ({}),
    getter: () => Object.defineProperty({}, 'live', { get() { throw new Error('getter'); }, enumerable: true }),
    proxy: () => new Proxy({}, { get() { throw new Error('trap'); }, has() { throw new Error('trap'); } }) };
  out.bad = {};
  for (const k of Object.keys(bads)) {
    const b = mount(Object.assign(base(), { lang: 'en', texts: input.en }), { now: input.nowMs, deps: { texts: bads[k] } });
    out.bad[k] = probe(b);
  }
  const none = mount(Object.assign(base(), { lang: 'en', texts: input.en }), { now: input.nowMs });
  out.none = probe(none);
  out.violations = violations;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""
        out = run_node(HEAD + tail, payload)
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["en"], ["EN live|Changes in the plan, spec and checkpoints appear at once (SSE).", "No connection|EN down tip"])
        self.assertEqual(out["pl"][1], "Brak połączenia|PL down tip")
        self.assertTrue(out["pl"][0].startswith("PL live|"), out["pl"])
        self.assertIn("en", out["seen"])
        self.assertIn("pl", out["seen"])
        self.assertEqual(out["static"], out["en"], "a static object works as before")
        for k, probe in out["bad"].items():
            self.assertEqual(probe, out["none"], f"a function giving `{k}` means no override")


    def test_relabel_alone_refreshes_the_live_label_after_a_language_change(self):
        fx = fixture_files()
        data = clone(fx["plan-in-progress"])
        payload = {"fixtures": {}, "now": {}, "data": data, "nowMs": ms(data["generatedAt"]), "en": ui_catalog("en")}
        tail = r"""
(async () => {
  const base = () => JSON.parse(JSON.stringify(input.data));
  const bag = { en: { live: { connecting: 'EN connecting' } }, pl: { live: { connecting: 'PL connecting' } } };
  const m = mount(base(), { now: input.nowMs, deps: { texts: (lang) => bag[lang] || {} } });
  const out = { start: text(m.root.querySelector('.live-text')) };
  m.board.update(Object.assign(base(), { lang: 'en', texts: input.en }));   // no setLive: only relabel() can change the label
  out.en = text(m.root.querySelector('.live-text'));
  m.board.update(base());
  out.pl = text(m.root.querySelector('.live-text'));
  out.violations = violations;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""
        out = run_node(HEAD + tail, payload)
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["start"], "PL connecting")
        self.assertEqual(out["en"], "EN connecting")
        self.assertEqual(out["pl"], "PL connecting")


@SKIP
class DefaultCatalogAndFallbacks(unittest.TestCase):
    def test_builtin_polish_catalog_is_the_ui_part_of_pl_json(self):
        out = subprocess.run([NODE, "-e", "process.stdout.write(JSON.stringify(require(process.argv[1]).defaultTexts))", str(RENDERER)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(json.loads(out.stdout), ui_catalog("pl"),
                         "the DEFAULT_TEXTS block of dashboard-render.js differs from the `ui.*` keys of pl.json (regenerate the block from pl.json)")

    def test_polish_default_and_the_fallbacks(self):
        fx = fixture_files()
        data = clone(fx["plan-in-progress"])
        payload = {"fixtures": {}, "now": {}, "data": data, "nowMs": ms(data["generatedAt"]), "en": ui_catalog("en"), "pl": ui_catalog("pl")}
        tail = r"""
(async () => {
  const dumpAll = (m) => text(m.root);
  const render = (extra, deps) => { const j = Object.assign(JSON.parse(JSON.stringify(input.data)), extra); const m = mount(j, { now: input.nowMs, deps }); const s = dumpAll(m) + '|' + m.root.querySelector('#tab-progress').getAttribute('data-tip'); m.board.destroy(); return s; };
  const out = {
    absent: render({ lang: undefined }),
    pl: render({ lang: 'pl' }),
    plWithForeignTexts: render({ lang: 'pl', texts: input.en }),
    partial: render({ lang: 'en', texts: { 'ui.tab.progress': 'Progress!', 'ui.tab.progressTip': 'Tip!' } }),
    english: render({ lang: 'en', texts: input.en }),
    hostSeam: null, badLang: render({ lang: '<b>x</b>', texts: input.en }), array: render({ lang: 'en', texts: [] }),
    nullTexts: render({ lang: 'en', texts: null }),
  };
  const m = mount(Object.assign(JSON.parse(JSON.stringify(input.data)), { lang: 'en', texts: input.en }), { now: input.nowMs, deps: { texts: { live: { connecting: 'Host connecting' } } } });
  out.hostSeam = text(m.root.querySelector('.live-text'));
  const flip = mount(JSON.parse(JSON.stringify(input.data)), { now: input.nowMs });
  const before = text(flip.root.querySelector('#tab-progress'));
  flip.board.update(Object.assign(JSON.parse(JSON.stringify(input.data)), { lang: 'en', texts: input.en }));
  out.flip = [before, text(flip.root.querySelector('#tab-progress')), text(flip.root.querySelector('#tab-docs')), flip.root.querySelector('#tab-docs').getAttribute('data-tip'), flip.board.t('ui.noConnection'), flip.board.lang()];
  flip.board.update(Object.assign(JSON.parse(JSON.stringify(input.data)), { lang: 'pl' }));
  out.flipBack = [text(flip.root.querySelector('#tab-progress')), flip.board.t('ui.noConnection'), flip.board.lang()];
  // the keyword of the closing step: Polish and English titles both get the description of the closure
  const closing = JSON.parse(JSON.stringify(input.data));
  closing.progress.phases[1].steps.push({ done: false, id: 'F9.1', index: 6, note: '', sha: null, state: 'planned', title: 'Closure of the plan' },
    { done: false, id: 'F9.2', index: 7, note: '', sha: null, state: 'planned', title: 'Zamknięcie planu' }, { done: false, id: 'F9.3', index: 8, note: '', sha: null, state: 'planned', title: 'Enclosure' });
  const cm = mount(closing, { now: input.nowMs });
  out.closure = cm.root.querySelectorAll('.step[data-desc="closure"]').length;
  out.violations = violations;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""
        out = run_node(HEAD + tail, payload)
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["closure"], 2)
        self.assertEqual(out["absent"], out["pl"])
        self.assertEqual(out["plWithForeignTexts"], out["pl"], "`texts` is read only for a language other than pl")
        self.assertIn("Postęp", out["pl"])
        self.assertTrue(out["partial"].endswith("|Tip!"))
        self.assertIn("Progress!", out["partial"])
        self.assertIn("Dokumenty", out["partial"], "a key missing in `texts` falls back to the built-in Polish text (not to the key)")
        self.assertNotRegex(out["partial"], r"\bui\.[a-z]")
        self.assertIn("Progress", out["english"])
        self.assertEqual(out["hostSeam"], "Host connecting")
        self.assertEqual(out["badLang"], out["pl"], "an invalid `lang` is Polish")
        for odd in ("array", "nullTexts"):
            self.assertIn("Postęp", out[odd], "lang en without a usable `texts` keeps the built-in Polish text")
        self.assertEqual(out["flip"][0], "Postęp")
        self.assertEqual(out["flip"][1], "Progress")
        self.assertTrue(out["flip"][2].startswith("Documents"), out["flip"][2])
        self.assertEqual(out["flip"][3:], ["Spec, plan and run files (key D)", "No connection to the status server.", "en"])
        self.assertEqual(out["flipBack"], ["Postęp", "Brak połączenia z serwerem statusu.", "pl"])


@SKIP
class OldRendererWithLangAndTexts(unittest.TestCase):
    """K4: the frozen renderer of 1.5.0 knows neither `lang` nor `texts`: it draws the same page with or without them."""

    def test_the_old_renderer_ignores_lang_and_texts(self):
        fixtures = fixture_files()
        tail = """
  const res = {};
  for (const [name, d] of Object.entries(input.fixtures)) {
    const plain = mount(d, { now: input.now[name] });
    const withTexts = mount(Object.assign({}, d, { lang: 'en', texts: input.texts }), { now: input.now[name] });
    res[name] = { same: text(plain.root) === text(withTexts.root), polish: /Postęp|Dokumenty/.test(text(withTexts.root)), len: text(plain.root).length };
    plain.board.destroy(); withTexts.board.destroy();
  }
  process.stdout.write(JSON.stringify({ res, violations }));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""
        with tempfile.TemporaryDirectory() as tmp:
            harness, data = Path(tmp) / "old.cjs", Path(tmp) / "input.json"
            harness.write_text(OLD_HEAD + tail, encoding="utf-8")
            data.write_text(json.dumps({"fixtures": fixtures, "now": {n: ms(d["generatedAt"]) for n, d in fixtures.items()}, "texts": ui_catalog("en")}), encoding="utf-8")
            proc = subprocess.run([NODE, str(harness), str(OLD_RENDERER), str(data)], capture_output=True, text=True, timeout=120, cwd=tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        out = json.loads(proc.stdout)
        self.assertNotIn("fatal", out)
        self.assertEqual(out["violations"], [])
        self.assertEqual(set(out["res"]), set(fixtures))
        for name, got in out["res"].items():
            with self.subTest(fixture=name):
                self.assertTrue(got["same"])
                self.assertTrue(got["polish"], "the old renderer keeps its Polish text")


class CatalogSurvivesTheServer(unittest.TestCase):
    def test_no_ui_text_is_masked_by_the_secret_redaction_of_the_status(self):
        """The status JSON is redacted before it is served (`common.redact`): a text like "token: open ..." would arrive as "token: [REDACTED] ..."."""
        for lang in ("pl", "en"):
            for key, value in ui_catalog(lang).items():
                for text in (value.values() if isinstance(value, dict) else [value]):
                    self.assertEqual(common.redact(text), text, (lang, key))


class PageAdapterAndSource(unittest.TestCase):
    def test_renderer_has_no_fixed_locale_and_no_polish_ui_text_in_code(self):
        src = RENDERER.read_text(encoding="utf-8")
        code = strip_js(src, keep_strings=True)
        self.assertEqual(re.findall(r"toLocale\w*String\(\s*[\"']", code), [], "the locale of the time comes from the language of the status")
        self.assertEqual(sorted(set(re.findall(r"[\"']pl-PL[\"']", code))), ['"pl-PL"'])   # only the default of `localeOf` and the initial language state
        self.assertEqual(len(re.findall(r"[\"']pl-PL[\"']", code)), 2)
        # the texts of the UI are keys of the catalog: no Polish diacritic is left in a string literal of the code outside the built-in catalog
        outside = re.sub(r"// <i18n-defaults>.*?// </i18n-defaults>", "", src, flags=re.S)
        literals = re.findall(r"\x02(.*?)\x03", strip_js(outside, keep_strings=True, mark=True), re.S)
        found = sorted({text for text in literals if POLISH.search(text)})
        self.assertEqual(found, [])

    def test_builtin_block_is_marked_and_lists_only_ui_keys(self):
        src = RENDERER.read_text(encoding="utf-8")
        block = src[src.index("// <i18n-defaults>"):src.index("// </i18n-defaults>")]
        keys = re.findall(r'^    "([^"]+)":', block, re.M)
        self.assertEqual(sorted(keys), sorted(ui_catalog("pl")))
        self.assertTrue(all(key.startswith("ui.") for key in keys))

    def test_adapter_uses_the_catalog_of_the_renderer_and_sets_the_page_language(self):
        code = strip_js(ADAPTER.read_text(encoding="utf-8"), keep_strings=True)
        self.assertIn('board.t("ui.httpError"', code)
        self.assertIn('board.t("ui.noConnection")', code)
        self.assertRegex(code, r"document\.documentElement\.lang\s*=\s*typeof data\.lang")
        self.assertNotRegex(strip_js(ADAPTER.read_text(encoding="utf-8"), keep_strings=False), r"documentElement\.setAttribute")
        self.assertNotIn("Błąd", ADAPTER.read_text(encoding="utf-8").split("// ----", 1)[1].split("async function fetchDoc", 1)[0])

    def test_page_language_is_substituted_only_for_another_language(self):
        page = (RENDERER.parent / "index.html").read_bytes()
        self.assertIn(b'<html lang="pl">', page)
        self.assertEqual(srv.page_with_language(page, "pl"), page)
        english = srv.page_with_language(page, "en")
        self.assertIn(b'<html lang="en">', english)
        self.assertEqual(english.count(b"lang="), 1)
        self.assertEqual(english.replace(b'lang="en"', b'lang="pl"').replace(b"<title>Run status</title>", b"<title>Status przebiegu</title>"), page)   # the `<title>` is the only other difference
        for odd in ("de", "", "pl-PL", '"><script>'):
            self.assertEqual(srv.page_with_language(page, odd), page, odd)


class PageServedInTheProjectLanguage(HttpCase):
    def read_page(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.read().decode("utf-8")

    def test_polish_without_the_key_and_english_with_it(self):
        for path in ("/", "/index.html"):
            self.assertIn('<html lang="pl">', self.read_page(path))
        manifest = self.root / ".github" / ".skillsync.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("language: en\n", encoding="utf-8")
        self.board.manifest()
        for path in ("/", "/index.html"):
            page = self.read_page(path)
            self.assertIn('<html lang="en">', page)
            self.assertNotIn('lang="pl"', page)
        with urllib.request.urlopen(self.base + "/api/status", timeout=5) as response:
            status = json.load(response)
        self.assertEqual(status["lang"], "en")
        self.assertEqual(status["texts"], ui_catalog("en"))


if __name__ == "__main__":
    unittest.main()
