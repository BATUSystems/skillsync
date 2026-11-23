"""N16 renderer: the section „Biegi podumysłów”, the green pulsing dot on the `Review ×N` label, the silence that a live run puts out, and the stale
rule of the consumer (now > max(start + 45 min, etaIso + 15 min)). The renderer runs on the shadow DOM of the renderer test (no jsdom, no browser: the
look of the page is proven in a browser, under LEASE); the clock is injected, the journal fixtures are written by hand."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_skillsync_dashboard_renderer import HARNESS, NODE, NODE_MAJOR, RENDERER, fixture_files  # noqa: E402

CSS = ROOT / "library" / "skills" / "dashboard" / "assets" / "app.css"

TAIL = r"""
  const F = input.fixtures, clone = (x) => JSON.parse(JSON.stringify(x));
  const base = Date.parse(F['plan-in-progress'].generatedAt);
  const iso = (min) => new Date(base + min * 60000).toISOString();
  const run = (o) => Object.assign({ agent: 'a-1', role: 'reviewer', kind: 'review', profile: 'code', step: null, round: 1, startedAt: iso(-10), eta: null, etaFirst: null, etaIso: null, etaShifts: 0 }, o);
  const withAgents = (name, agents, mut) => { const d = clone(F[name]); d.agents = Object.assign({ running: [], recent: [], omitted: { running: 0, recent: 0 } }, agents); if (mut) mut(d); return d; };
  const stage = (m, key) => m.root.querySelectorAll('#acts .stage').__asArray().filter((n) => n.getAttribute('data-desc') === key)[0] || null;
  const info = (n) => n ? { cls: n.getAttribute('class'), dataState: n.getAttribute('data-state'), text: text(n), dots: n.querySelectorAll('.stage-dot').length,
    runDots: n.querySelectorAll('[data-run-dot]').length, runDotCls: (n.querySelector('[data-run-dot]') || { getAttribute: () => null }).getAttribute('class'), tip: n.getAttribute('data-desc-extra') } : null;
  const box = (m) => { const b = m.root.querySelector('#agentsBox'); return { hidden: b.hidden, text: text(b), rows: b.querySelectorAll('.agent-row').length, open: !!b.querySelector('details') && b.querySelector('details').hasAttribute('open') }; };
  const banner = (m) => ({ hidden: m.root.querySelector('#waitBanner').hidden, text: text(m.root.querySelector('#waitBanner')), working: !!m.root.querySelector('[data-working-toast]') });
  const res = {};
  const plan = 'plan-in-progress';

  // no journal: nothing of the new section, the tiles as before
  { const m = mount(F[plan], { now: base }); res.none = { box: box(m), review: info(stage(m, 'code_review')) }; }

  // a round of code review (round 2: the tile is done, the label says ×2): the dot and the caption, no second dot
  {
    const d = withAgents(plan, { running: [run({ profile: 'code', round: 2, startedAt: iso(-20), eta: '15:05', etaFirst: '14:35', etaIso: iso(25), etaShifts: 2 })] }, (x) => {
      const t = x.acts[2].stages[0]; t.state = 'done'; t.done = true; t.label = 'Review kodu ×2'; });
    const m = mount(d, { now: base });
    res.round2 = { tile: info(stage(m, 'code_review')), box: box(m), other: info(stage(m, 'closure')) };
    const f = withAgents(plan, { running: [run({ profile: 'final', round: 1 })] });
    res.finalProfile = info(stage(mount(f, { now: base }), 'code_review'));
    const wrong = withAgents(plan, { running: [run({ profile: 'docs', round: 1 }), run({ agent: 'a-2', kind: 'run', profile: null, role: 'implementer' })] });
    res.otherProfiles = info(stage(mount(wrong, { now: base }), 'code_review'));
  }

  // round 1 of the spec review (the tile has no report yet): the journal makes it active from the start of the run and wins over a hand-written `trwa:`
  {
    const sp = 'spec-act';
    const d = withAgents(sp, { running: [run({ profile: 'spec', round: null, startedAt: iso(-5) })] });
    const m = mount(d, { now: base });
    res.spec1 = info(stage(m, 'stage_spec_review'));
    const handWritten = clone(d); handWritten.acts[0].stages[1].state = 'active'; handWritten.acts[0].stages[1].since = '06:01';
    res.specWins = info(stage(mount(handWritten, { now: base }), 'stage_spec_review'));
    const planRound = withAgents(sp, { running: [run({ profile: 'plan', round: 1 })] });
    const mp = mount(planRound, { now: base });
    res.planTile = info(stage(mp, 'stage_plan_review'));
    res.planTileSpecUntouched = info(stage(mp, 'stage_spec_review'));
  }

  // the stale rule: max(start + 45 min, etaIso + 15 min); a later `eta` event (etaIso) extends the validity; the clock moves with `tick`
  {
    let t = base;
    const noEta = withAgents(plan, { running: [run({ startedAt: iso(0) })] }, (x) => { x.silence = { since: iso(-60), thresholdMin: 35 }; });
    const m = mount(noEta, { now: () => t });
    const at = (min) => { t = base + min * 60000; m.board.tick(); return { tile: info(stage(m, 'code_review')), banner: banner(m), box: box(m) }; };
    res.stale = { at44: at(44), at46: at(46) };
    t = base;
    const shifted = withAgents(plan, { running: [run({ startedAt: iso(0), eta: '13:30', etaFirst: '12:30', etaIso: iso(90), etaShifts: 1 })] }, (x) => { x.silence = { since: iso(-60), thresholdMin: 35 }; });
    const m2 = mount(shifted, { now: () => t });
    const at2 = (min) => { t = base + min * 60000; m2.board.tick(); return { tile: info(stage(m2, 'code_review')), banner: banner(m2) }; };
    res.shifted = { at60: at2(60), at104: at2(104), at106: at2(106) };
    const earlyEta = withAgents(plan, { running: [run({ startedAt: iso(0), eta: '12:10', etaFirst: '12:10', etaIso: iso(10), etaShifts: 0 })] }, (x) => { x.silence = { since: iso(-60), thresholdMin: 35 }; });
    t = base; const m3 = mount(earlyEta, { now: () => t });
    t = base + 44 * 60000; m3.board.tick(); const a44 = banner(m3).working;   // an early deadline does not shorten the 45 minutes after the start
    t = base + 46 * 60000; m3.board.tick(); res.earlyEta = { at44Working: a44, at46Working: banner(m3).working };
  }

  // the silence: a live run puts it out (tile and banner), the toast says who works; the silence is back after `end` (no running)
  {
    const quietFixture = (x) => { x.silence = { since: iso(-60), thresholdMin: 35 }; x.run = Object.assign({}, x.run, { lastReportAt: iso(-60) }); };
    const live = withAgents(plan, { running: [run({ startedAt: iso(-20), round: 2 })] }, quietFixture);
    const m = mount(live, { now: base });
    const ended = withAgents(plan, { running: [], recent: [run({ endedAt: iso(-1), seconds: 1100, result: 'PASS', report: 'x.md' })] }, quietFixture);
    const m2 = mount(ended, { now: base });
    res.silence = { live: { banner: banner(m), tile: info(stage(m, 'code_review')), real: info(stage(m, 'closure')) }, ended: { banner: banner(m2) } };
    const noRound = withAgents(plan, { running: [run({ startedAt: iso(-20), round: null })] }, quietFixture);
    res.workingNoRound = banner(mount(noRound, { now: base })).text;
  }

  // the section: rows, progress bar, shifted/over/stale notes, recent with a report link only for a file of the run, omitted
  {
    const d = withAgents(plan, {
      running: [run({ agent: 'a-3', round: 2, step: 'F2.1', startedAt: iso(-20), eta: '15:05', etaFirst: '14:35', etaIso: iso(-10), etaShifts: 2 }),
                run({ agent: 'a-4', role: 'implementer', kind: 'run', profile: null, round: null, startedAt: iso(-100), etaIso: null }),
                run({ agent: 'a-5', role: 'test-runner', kind: 'run', profile: null, round: null, startedAt: iso(-5), eta: '12:30', etaFirst: '12:30', etaIso: iso(25) })],
      recent: [run({ agent: 'a-1', round: 1, startedAt: iso(-90), endedAt: iso(-60), seconds: 1800, result: 'CHANGES_REQUIRED', report: 'pre-implement.md' }),
               run({ agent: 'a-2', round: 1, startedAt: iso(-90), endedAt: iso(-60), seconds: 1800, result: 'PASS', report: 'missing.md' })],
      omitted: { running: 1, recent: 2 } });
    const m = mount(d, { now: base });
    const rows = m.root.querySelectorAll('#agentsBox .agent-row').__asArray();
    const bars = m.root.querySelectorAll('#agentsBox .agent-bar').__asArray();
    const reports = m.root.querySelectorAll('#agentsBox [data-open-doc]').__asArray().map((b) => b.getAttribute('data-open-doc'));
    res.section = { box: box(m), rows: rows.map((r) => ({ cls: r.getAttribute('class'), text: text(r) })), bars: bars.map((bar) => ({ role: bar.getAttribute('role'), now: bar.getAttribute('aria-valuenow'), valuetext: bar.getAttribute('aria-valuetext'), label: bar.getAttribute('aria-label') })),
      reports, tag: m.root.querySelector('#agentsBox ul').localName };
    res.sectionClosedByDefault = box(mount(withAgents(plan, { running: [], recent: [run({ endedAt: iso(-1), seconds: 600, result: 'PASS' })] }), { now: base })).open;
  }

  // the clock alone refreshes the section (a run went over its ETA between two statuses of the server)
  {
    let t = base;
    const d = withAgents(plan, { running: [run({ round: 2, startedAt: iso(-5), eta: '12:20', etaFirst: '12:20', etaIso: iso(10) })] });
    const m = mount(d, { now: () => t });
    const before = text(m.root.querySelector('#agentsBox'));
    t = base + 25 * 60000; m.board.tick();
    res.clockRefresh = { before, after: text(m.root.querySelector('#agentsBox')) };
  }

  // hostile and corrupted values: text only, no exception; the whole field unusable = no section
  {
    const d = withAgents(plan, { running: [run({ role: '<img src=x onerror=alert(1)>', step: '<b>x</b>', result: null })] });
    const m = mount(d, { now: base });
    res.hostile = { text: text(m.root.querySelector('#agentsBox')), imgs: m.root.querySelectorAll('img').length };
    const broken = [];
    for (const bad of ['text', 42, [], { running: 'x', recent: 7 }, { running: [1, null, 'a', {}, { role: 5, startedAt: 'bad' }] }]) {
      const e = clone(F[plan]); e.agents = bad;
      try { const mm = mount(e, { now: base }); broken.push({ hidden: mm.root.querySelector('#agentsBox').hidden, tile: info(stage(mm, 'code_review')).dots }); } catch (err) { broken.push({ error: String(err) }); }
    }
    res.broken = broken;
  }
  // review fixes: the bar moves in place (no re-render: the focus and the animation survive), the toast follows the silence threshold, the marker of a step,
  // the section collapses when the runs are over
  {
    let t = base;
    const d = withAgents(plan, { running: [run({ round: 2, startedAt: iso(-5), eta: '12:35', etaFirst: '12:35', etaIso: iso(25) })] });
    const m = mount(d, { now: () => t });
    m.board.tick();   // the first tick may settle the waiting counter with one render; from the second on only a change of state rebuilds the page
    const bar0 = m.root.querySelector('#agentsBox .agent-bar'), summary0 = m.root.querySelector('#agentsBox summary');
    const v0 = bar0.getAttribute('aria-valuenow');
    t = base + 6 * 60000; m.board.tick();
    const bar1 = m.root.querySelector('#agentsBox .agent-bar');
    res.barInPlace = { same: bar0 === bar1, sameSummary: summary0 === m.root.querySelector('#agentsBox summary'), before: v0, after: bar1.getAttribute('aria-valuenow'), width: bar1.querySelector('.fill').style.width,
      valuetext: bar1.getAttribute('aria-valuetext') };
    const calm = withAgents(plan, { running: [run({ startedAt: iso(0) })] }, (x) => { x.silence = { since: iso(-20), thresholdMin: 35 }; });
    t = base; const mc = mount(calm, { now: () => t });
    const w0 = banner(mc).working;
    t = base + 16 * 60000; mc.board.tick();
    res.toastAtThreshold = { before: w0, after: banner(mc).working };
    const nan = withAgents(plan, { running: [run({ startedAt: iso(-5) })] }, (x) => { x.silence = { since: 'not a time', thresholdMin: 35 }; });
    res.toastNaN = banner(mount(nan, { now: base })).working;
    const stepped = withAgents(plan, { running: [run({ agent: 'a-3', step: 'F2.1', profile: 'code', round: 2 })], recent: [run({ agent: 'a-2', step: 'F2.1', round: 1, endedAt: iso(-9), seconds: 3000, result: 'PASS' }),
      run({ agent: 'a-1', step: 'F1.1', kind: 'run', role: 'implementer', profile: null, round: null, endedAt: iso(-9), seconds: 3000, result: 'DONE' })] });
    const ms = mount(stepped, { now: base });
    res.stepMarks = ms.root.querySelectorAll('#phases .step').__asArray().map((n) => ({ id: text(n.querySelector('.sid')), mark: n.querySelector('[data-step-rounds]') ? text(n.querySelector('[data-step-rounds]')) : null }));
    res.noStepMarks = mount(withAgents(plan, {}), { now: base }).root.querySelectorAll('[data-step-rounds]').length;
    const over = withAgents(plan, { running: [run({ startedAt: iso(-5) })] });
    const mo = mount(over, { now: base });
    const ended = withAgents(plan, { running: [], recent: [run({ endedAt: iso(-1), seconds: 600, result: 'PASS' })] });
    mo.board.update(ended);
    res.collapsesAfterEnd = { open: !!mo.root.querySelector('#agentsBox details').hasAttribute('open'), rows: mo.root.querySelectorAll('#agentsBox .agent-row').length };
  }
  res.violations = violations;
  process.stdout.write(JSON.stringify(res));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE and NODE_MAJOR >= 18, "node >= 18 is required")
class AgentsRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        head = HARNESS[:HARNESS.index("  const F = input.fixtures")]
        with tempfile.TemporaryDirectory() as tmp:
            harness, data = Path(tmp) / "agents.cjs", Path(tmp) / "input.json"
            harness.write_text(head + TAIL, encoding="utf-8")
            data.write_text(json.dumps({"fixtures": fixture_files(), "now": {}}), encoding="utf-8")
            proc = subprocess.run([NODE, str(harness), str(RENDERER), str(data)], capture_output=True, text=True, timeout=60, cwd=tmp)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise AssertionError(f"node rc={proc.returncode}: {proc.stderr[-2000:]}")
        cls.out = json.loads(proc.stdout)
        if "fatal" in cls.out:
            raise AssertionError(cls.out["fatal"])

    def test_no_sink_was_touched(self):
        self.assertEqual(self.out["violations"], [])

    def test_without_a_journal_nothing_changes(self):
        got = self.out["none"]
        self.assertTrue(got["box"]["hidden"])
        self.assertEqual(got["box"]["rows"], 0)
        self.assertEqual(got["review"]["runDots"], 0)
        self.assertNotIn("data-run-dot", json.dumps(got))

    def test_a_live_round_of_review_lights_the_green_dot_and_the_caption_in_round_two(self):
        tile = self.out["round2"]["tile"]
        self.assertEqual(tile["runDots"], 1)
        self.assertIn("run-dot", tile["runDotCls"].split())
        self.assertNotIn("is-stale", tile["runDotCls"].split())
        self.assertEqual(tile["dots"], 1, "one dot, not an accent dot and a green one")
        self.assertIn("is-done", tile["cls"].split())
        self.assertEqual(tile["dataState"], "done", "a done tile stays done in round 2 and later")
        self.assertIn("Review kodu ×2", tile["text"])
        self.assertIn("runda 2 trwa od 11:40", tile["text"])
        self.assertIn("runda 2 trwa od 11:40", tile["tip"])
        self.assertEqual(self.out["round2"]["other"]["runDots"], 0)

    def test_the_profiles_map_to_the_tiles(self):
        self.assertEqual(self.out["finalProfile"]["runDots"], 1, "`final` belongs to the review of code")
        self.assertEqual(self.out["otherProfiles"]["runDots"], 0, "docs and a run of another kind light no review tile")
        self.assertEqual(self.out["planTile"]["runDots"], 1)
        self.assertEqual(self.out["planTileSpecUntouched"]["runDots"], 0)

    def test_round_one_makes_the_tile_active_from_the_start_of_the_run_and_wins_over_a_hand_written_trwa(self):
        tile = self.out["spec1"]
        self.assertEqual(tile["dataState"], "active")
        self.assertIn("is-active", tile["cls"].split())
        self.assertIn("trwa od 11:55", tile["tip"])
        self.assertIn("runda trwa od 11:55", tile["text"], "no `round` in the event: the caption without a number")
        self.assertEqual(tile["dots"], 1)
        won = self.out["specWins"]
        self.assertIn("trwa od 11:55", won["tip"])
        self.assertNotIn("06:01", won["tip"] + won["text"])

    def test_stale_rule_start_plus_45_min_and_a_later_eta_extends_it(self):
        s = self.out["stale"]
        self.assertEqual(s["at44"]["tile"]["runDotCls"].split().count("is-stale"), 0)
        self.assertTrue(s["at44"]["banner"]["working"])
        self.assertNotIn("is-quiet", s["at44"]["tile"]["cls"].split())
        stale = s["at46"]
        self.assertIn("is-stale", stale["tile"]["runDotCls"].split(), "the dot goes out (hollow, no pulse)")
        self.assertIn("runda nie zakończona", stale["tile"]["text"])
        self.assertFalse(stale["banner"]["working"])
        self.assertIn("Brak raportu postępu", stale["banner"]["text"], "the silence is back for a stale run")
        self.assertIn("nie zakończono", stale["box"]["text"])
        sh = self.out["shifted"]   # etaIso = +90 min: valid until +105 min
        self.assertTrue(sh["at60"]["banner"]["working"])
        self.assertTrue(sh["at104"]["banner"]["working"])
        self.assertFalse(sh["at106"]["banner"]["working"])
        self.assertIn("is-stale", sh["at106"]["tile"]["runDotCls"].split())
        self.assertTrue(self.out["earlyEta"]["at44Working"], "an early deadline does not shorten the 45 minutes")
        self.assertFalse(self.out["earlyEta"]["at46Working"])

    def test_a_live_run_puts_out_the_silence_and_says_who_works(self):
        live, ended = self.out["silence"]["live"], self.out["silence"]["ended"]
        self.assertTrue(live["banner"]["working"])
        self.assertIn("Trwa: reviewer, runda 2, od 11:40", live["banner"]["text"])
        self.assertNotIn("Brak raportu postępu", live["banner"]["text"])
        for tile in (live["tile"], live["real"]):
            self.assertNotIn("is-quiet", tile["cls"].split())
        self.assertFalse(ended["banner"]["working"])
        self.assertIn("Brak raportu postępu", ended["banner"]["text"], "after `end` the silence is back")
        self.assertIn("Trwa: reviewer, od 11:40", self.out["workingNoRound"])

    def test_the_section_rows_bar_and_notes(self):
        sec = self.out["section"]
        self.assertFalse(sec["box"]["hidden"])
        self.assertTrue(sec["box"]["open"], "open while something runs")
        self.assertEqual(sec["tag"], "ul")
        self.assertEqual(sec["box"]["rows"], 5)
        first, stale, third, done, missing = sec["rows"]
        self.assertIn("reviewer · runda 2 · krok F2.1 · od 11:40 · koniec ok. 15:05", first["text"])
        self.assertIn("ETA przesunięte ×2", first["text"])
        self.assertIn("przekroczono ETA o 10 min", first["text"])
        self.assertIn("is-over", first["cls"].split())
        self.assertIn("is-stale", stale["cls"].split())
        self.assertIn("nie zakończono", stale["text"])
        self.assertIn("test-runner · od 11:55 · koniec ok. 12:30", third["text"])
        self.assertIn("CHANGES_REQUIRED · trwał 30 min", done["text"])
        self.assertEqual(len(sec["bars"]), 2, "a bar for a run with a deadline that is not stale")
        self.assertEqual(sec["bars"][0]["now"], "100", "past its deadline: full")
        bar = sec["bars"][1]
        self.assertEqual((bar["role"], bar["now"]), ("progressbar", "17"), "5 of 30 minutes")
        self.assertEqual(bar["valuetext"], "17% czasu do ETA", "a text equivalent of the bar")
        self.assertIn("test-runner", bar["label"])
        self.assertEqual(sec["reports"], [".skillsync/runs/2026-01-10-demo-feature/pre-implement.md"], "a link only for a file of the run")
        self.assertIn("Pominięto starsze: 3", sec["box"]["text"])
        self.assertFalse(self.out["sectionClosedByDefault"], "only finished runs: collapsed")

    def test_the_clock_alone_refreshes_the_section(self):
        got = self.out["clockRefresh"]
        self.assertNotIn("przekroczono", got["before"])
        self.assertIn("przekroczono ETA o 15 min", got["after"])

    def test_values_are_text_and_an_unusable_field_gives_no_section(self):
        self.assertIn("<img src=x onerror=alert(1)>", self.out["hostile"]["text"])
        self.assertEqual(self.out["hostile"]["imgs"], 0)
        for case in self.out["broken"]:
            self.assertNotIn("error", case)
            self.assertTrue(case["hidden"])
            self.assertEqual(case["tile"], 0)


class AgentsRendererReviewFixes(AgentsRenderer):
    """The findings of the first review round: the clock moves the bar in place, the toast follows the threshold, the marker of a step, the section folds."""

    def test_the_bar_moves_in_place_and_the_page_is_not_rebuilt_by_the_clock(self):
        got = self.out["barInPlace"]
        self.assertTrue(got["same"], "the same bar node: no re-render, so the focus and the animation survive")
        self.assertTrue(got["sameSummary"])
        self.assertEqual((got["before"], got["after"]), ("17", "37"), "5 of 30 minutes, then 11 of 30")
        self.assertEqual(got["width"], "37%")
        self.assertEqual(got["valuetext"], "37% czasu do ETA")

    def test_the_working_toast_appears_when_the_silence_crosses_the_threshold_and_not_on_a_bad_time(self):
        self.assertFalse(self.out["toastAtThreshold"]["before"])
        self.assertTrue(self.out["toastAtThreshold"]["after"], "a run without a deadline: the clock alone shows the toast")
        self.assertFalse(self.out["toastNaN"], "an unreadable `silence.since` is no silence")

    def test_a_plan_step_with_rounds_of_review_has_a_marker(self):
        marks = {m["id"]: m["mark"] for m in self.out["stepMarks"]}
        self.assertEqual(marks["F2.1"], "2 rundy review kroku")
        self.assertIsNone(marks["F1.1"], "a run of another kind is not a round of review")
        self.assertEqual(self.out["noStepMarks"], 0)

    def test_the_section_folds_when_the_runs_are_over(self):
        self.assertEqual(self.out["collapsesAfterEnd"], {"open": False, "rows": 1})


class AgentsCss(unittest.TestCase):
    css = CSS.read_text(encoding="utf-8")

    def rule(self, selector):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def test_the_dot_uses_the_token_of_the_done_label_and_stops_pulsing_when_stale(self):
        self.assertIn("background: var(--done)", self.rule(".stage-dot.run-dot"))
        self.assertIn("var(--done)", self.rule(".stage.is-done"), "the label of a done stage has the same colour")
        stale = self.rule(".stage.is-quiet .stage-dot.run-dot, .stage-dot.run-dot.is-stale")
        self.assertIn("animation: none", stale)
        self.assertIn("var(--done)", stale)

    def test_reduced_motion_stops_the_dot_and_the_new_rules_use_tokens_only(self):
        reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", self.css, re.S).group(1)
        self.assertIn(".stage-dot", reduced)
        start = self.css.index("/* N16:")
        block = self.css[start:self.css.index(".agents-omitted", start) + 200]
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", block), [], "no new colours: tokens only")

    def test_the_section_is_hidden_with_the_attribute_and_wraps_on_a_narrow_screen(self):
        self.assertIn("display: none", self.rule(".agents-box[hidden]"))
        self.assertIn("flex-wrap: wrap", self.rule(".agent-row"))
        self.assertIn("overflow-wrap: anywhere", self.rule(".agent-row .agent-txt"))


if __name__ == "__main__":
    unittest.main()
