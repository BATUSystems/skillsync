"""I18N-1 (zgodność wstecz renderera): zamrożone renderery 1.5.0 / 1.11.0 / 1.12.0 i złoty zrzut nowego renderera w `pl`.

- K4: każdy stary renderer rysuje scenariusze testu `renderer_i18n` z polami `lang` + `texts` dokładnie tak samo jak bez nich.
- K1 (złoty zrzut): nowy renderer w `pl` (status bez `lang` i `texts`) rysuje te same scenariusze (wszystkie fixtury `tests/fixtures/dashboard/*/expected*.json`
  i dodatkowe stany) z identycznym tekstem DOM jak renderer 1.12.0 sprzed katalogu tekstów.
- K2 na realnej stronie: legenda pojęć z prawdziwym `descriptions.en.json` jest po angielsku, z `descriptions.json` po polsku.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tests.test_skillsync_dashboard_renderer import HARNESS, NODE, NODE_MAJOR, RENDERER, fixture_files, ms  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import HEAD, JS_TAIL, POLISH, clone, scenarios, ui_catalog  # noqa: E402
from tests.test_skillsync_dashboard_questions_render import DEFAULT_TIP, assert_tab_matches_data, probe_cases  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
OLD = {version: FIXTURES / f"renderer-{version}" / "dashboard-render.js" for version in ("1.5.0", "1.11.0", "1.12.0")}
DESCRIPTIONS = RENDERER.parents[1] / "descriptions.json"
DESCRIPTIONS_EN = RENDERER.parents[1] / "descriptions.en.json"
RUN_LIMIT_SECONDS = 8   # one run of all scenarios takes ~0.5 s; a leaked toast timer (10 s ok, 30 s error) pushes it past this, with a 15x margin for slow runners
SKIP = unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
# the part of the scenario harness that draws the scenarios; the plural parity and injection probes need the catalog of the new renderer
# `board.t` is new (an old renderer has no catalog): the probe of the live texts asks for it only when it exists
RUNS_TAIL = JS_TAIL.replace("m.board.t('ui.httpError', { status: 502 })", "(m.board.t ? m.board.t('ui.httpError', { status: 502 }) : '')")
assert "m.board.t ?" in RUNS_TAIL
RUNS_TAIL = RUNS_TAIL[:RUNS_TAIL.index("  // plural parity")] + "  process.stdout.write(JSON.stringify({ runs: out.runs, violations }));\n})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });\n"


def run_with(renderer: Path, source: str, payload: dict, timeout: int = 300) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        harness, data = Path(tmp) / "harness.cjs", Path(tmp) / "input.json"
        harness.write_text(source, encoding="utf-8")
        data.write_text(json.dumps(payload), encoding="utf-8")
        started = time.monotonic()
        proc = subprocess.run([NODE, str(harness), str(renderer), str(data)], capture_output=True, text=True, timeout=timeout, cwd=tmp)
        elapsed = time.monotonic() - started
    # a mount that is not destroyed leaves a real toast timer (10 s / 30 s) and Node waits for it before it exits; that once made one run last 30 s
    if elapsed > RUN_LIMIT_SECONDS:
        raise AssertionError(f"a node run took {elapsed:.0f} s (> {RUN_LIMIT_SECONDS} s): a mount without `board.destroy()` keeps a timer alive")
    if proc.returncode != 0 or not proc.stdout.strip():
        raise AssertionError(f"node rc={proc.returncode}: {proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    if "fatal" in out:
        raise AssertionError(out["fatal"])
    return out


def scenario_run(renderer: Path, items: list[dict]) -> dict:
    return run_with(renderer, HEAD + RUNS_TAIL, {"fixtures": {}, "now": {}, "scenarios": items, "catalogs": {"en": ui_catalog("en"), "pl": ui_catalog("pl")}})


# `deps.texts` as a function `(lang) => object` (the new bridge): every scenario gets one; `textsMode: "strip"` drops `deps.texts` instead
_DEPS_LINE = "const deps = Object.assign({}, deps0, sc.deps);"
assert _DEPS_LINE in RUNS_TAIL
TEXTS_TAIL = RUNS_TAIL.replace(_DEPS_LINE, _DEPS_LINE + """
    if (input.textsMode === 'fn') { const own = deps.texts || {}; deps.texts = () => own; }
    if (input.textsMode === 'strip') delete deps.texts;""")


def texts_run(renderer: Path, items: list[dict], mode: str) -> dict:
    return run_with(renderer, HEAD + TEXTS_TAIL, {"fixtures": {}, "now": {}, "scenarios": items, "textsMode": mode,
                                                  "catalogs": {"en": ui_catalog("en"), "pl": ui_catalog("pl")}})


# CORE-QTAB: the tab of the questions has two groups and a new tip. The text of the tab tip is the one line that changes for every scenario; the scenarios that have
# spec questions or assumptions (or a `current` of the expert group) show a tab the frozen 1.12.0 never drew, so they are not compared with it, but they are covered
# by `test_scenarios_with_spec_questions_draw_both_groups` (structure and counters from the same input).
OLD_TIP = "Prototypy i pytania do eksperta"
TIP_LINE = "<tip of the questions tab>"


def has_spec_questions(item: dict) -> bool:
    data = item["json"]
    spec = any(isinstance(data.get(field), dict) and data[field].get("items") for field in ("blockingQuestions", "assumptions"))
    return spec or isinstance((data.get("expertQuestions") or {}).get("current"), dict)


def without_spec_questions(items: list[dict]) -> list[dict]:
    """The same statuses with no spec questions or assumptions and no `current` of the expert group (the rest of the page stays comparable with 1.12.0)."""
    out = clone(items)
    for item in out:
        for field in ("blockingQuestions", "assumptions"):
            item["json"][field] = {"count": 0, "items": []}
        if "expertQuestions" in item["json"] and item["json"]["expertQuestions"] is not None and "current" in item["json"]["expertQuestions"]:
            item["json"]["expertQuestions"]["current"] = None
    return out


# CORE-1170-MODE: the header of the page has a chip of the delivery mode (new in the catalog renderer; the frozen 1.12.0 draws nothing of the kind). It adds two lines of text to every
# status with a `delivery.mode`: the tip and the label. The expected wording is computed here from the INPUT of the scenario (not read back from the renderer); `page_text` removes exactly
# those two lines from the page of the new renderer (after asserting they are there, in this order) and from the old page nothing (`strip_mode_chip` asserts it has none).
BAD_MODE_TIP = "Nieprawidłowy tryb dostarczania w manifeście (delivery.mode albo delivery.base): nie zakładaj »pr«, zapytaj użytkownika"


def mode_chip_lines(delivery) -> tuple[str, str] | None:
    """(tip, label) the chip of the mode must show for the `delivery` of a status, None when it must not be drawn (no object, no non-empty text `mode`)."""
    if not isinstance(delivery, dict):
        return None
    mode = delivery.get("mode")
    if mode == "invalid" or delivery.get("source") == "invalid":
        return BAD_MODE_TIP, "tryb dostarczania: błąd"
    if not isinstance(mode, str) or not mode:
        return None
    base = delivery.get("base") if isinstance(delivery.get("base"), str) and delivery.get("base") else ""
    source = "domyślny (brak delivery.mode w manifeście)" if delivery.get("source") == "default" else "z manifestu"
    return f"Tryb dostarczania: {mode}; baza: {base or 'brak'}; źródło: {source}", f"tryb: {mode}, baza: {base}" if base else f"tryb: {mode}"


def strip_mode_chip(snap: str, delivery) -> str:
    """The page text of the NEW renderer without the two lines of the mode chip. The chip must be there when the input has a mode (and only then)."""
    lines = snap.split("\n")
    expected = mode_chip_lines(delivery)
    if expected is None:
        assert not any(line.startswith(("Tryb dostarczania:", "tryb: ", "tryb dostarczania:")) for line in lines), "a chip of the mode drawn without a mode in the status"
        return snap
    # a scenario with actions dumps the page several times (one dump per state): the chip is in every dump, so the pair is removed wherever it stands, at least once
    out, found, i = [], 0, 0
    while i < len(lines):
        if i + 1 < len(lines) and (lines[i], lines[i + 1]) == expected:
            found += 1
            i += 2
        else:
            out.append(lines[i])
            i += 1
    assert found >= 1, f"the chip of the mode {expected} is not in the page text"
    return "\n".join(out)


def page_text(snap: str, delivery=None, new: bool = False) -> str:
    """The page text without the `HTTP:` probe line (new in the catalog renderer) and with the tip of the questions tab replaced by one token.
    Only the two exact wordings of the tip without counters: a tip with counters (`N do realizacji, M do eksperta`) must fail the comparison, the pages compared here have none.
    `new=True` (the page of the new renderer): the chip of the mode of `delivery` is removed too (CORE-1170-MODE); the old page never has it."""
    if new:
        snap = strip_mode_chip(snap, delivery)
    else:
        assert not any(line.startswith(("Tryb dostarczania:", "tryb: ")) for line in snap.split("\n")), "the frozen renderer knows no chip of the mode"
    snap = re.sub(r"\nHTTP:.*$", "", snap)
    lines = [TIP_LINE if line in (OLD_TIP, DEFAULT_TIP) else line for line in snap.split("\n")]
    return "\n".join(lines)


def deliveries(items: list[dict]) -> dict:
    return {item["name"]: item["json"].get("delivery") for item in items}


def plain(items):
    out = clone(items)
    for item in out:
        item["json"].pop("lang", None)
        item["json"].pop("texts", None)
    return out


def without_prototypes(items: list[dict]) -> list[dict]:
    """CORE-2000-UNIFIED: the same statuses with no prototype of the project and none of the visible spec. The new renderer replaced the link
    `Prototyp · N/M` next to the tabs with a tab of the prototypes, so a page with them differs by design (`test_skillsync_unified_page_prototypes`
    draws that tab); without them the rest of the page stays comparable with 1.12.0."""
    out = clone(items)
    for item in out:
        data = item["json"]
        data.pop("singleOrigin", None)   # the notes of `--single-origin` are new too
        if isinstance(data.get("prototypes"), list):
            data["prototypes"] = []
        if isinstance(data.get("prototype"), dict):
            data["prototype"]["slug"] = None
    return out


def with_english(items):
    out = clone(items)
    for item in out:
        item["json"]["lang"] = "en"
        item["json"]["texts"] = "@en"
    return out


@SKIP
class OldRenderersIgnoreLangAndTexts(unittest.TestCase):
    """K4 on every frozen renderer: the same scenarios with `lang`+`texts` draw the same text as without them."""

    @classmethod
    def setUpClass(cls):
        cls.items = scenarios()

    def check(self, version):
        renderer = OLD[version]
        self.assertTrue(renderer.is_file(), renderer)
        bare = scenario_run(renderer, plain(self.items))
        dressed = scenario_run(renderer, with_english(self.items))
        self.assertEqual(bare["violations"], [])
        self.assertEqual(dressed["violations"], [])
        self.assertEqual(sorted(bare["runs"]), sorted(item["name"] for item in self.items))
        self.assertEqual(sorted(dressed["runs"]), sorted(bare["runs"]))
        for name, snap in bare["runs"].items():
            with self.subTest(renderer=version, scenario=name):
                self.assertGreater(len(snap), 50)
                self.assertEqual(dressed["runs"][name], snap)

    def test_renderer_1_5_0(self):
        self.check("1.5.0")

    def test_renderer_1_11_0(self):
        self.check("1.11.0")

    def test_renderer_1_12_0(self):
        self.check("1.12.0")


@SKIP
class TextsFunctionBridge(unittest.TestCase):
    """`deps.texts` may be a function `(lang) => object`. An old renderer reads `texts.live` of the function (undefined) and keeps its own texts;
    the new renderer with a function returning the static object draws what it draws with the static object."""

    @classmethod
    def setUpClass(cls):
        cls.items = scenarios()

    def check_old(self, version):
        renderer = OLD[version]
        as_function = texts_run(renderer, self.items, "fn")
        without = texts_run(renderer, self.items, "strip")
        self.assertEqual(as_function["violations"], [])
        self.assertEqual(sorted(as_function["runs"]), sorted(without["runs"]))
        for name, snap in without["runs"].items():
            with self.subTest(renderer=version, scenario=name):
                self.assertEqual(as_function["runs"][name], snap)

    def test_renderer_1_5_0(self):
        self.check_old("1.5.0")

    def test_renderer_1_11_0(self):
        self.check_old("1.11.0")

    def test_renderer_1_12_0(self):
        self.check_old("1.12.0")

    def test_new_renderer_function_equals_static_object(self):
        static = scenario_run(RENDERER, self.items)
        as_function = texts_run(RENDERER, self.items, "fn")
        self.assertEqual(as_function["violations"], [])
        self.assertEqual(sorted(as_function["runs"]), sorted(static["runs"]))
        for name, snap in static["runs"].items():
            with self.subTest(scenario=name):
                self.assertEqual(as_function["runs"][name], snap)


@SKIP
class PolishRendererEqualsTheRendererBeforeTheCatalog(unittest.TestCase):
    """K1 golden snapshot: a status without `lang` and `texts` gives the same page text as the renderer of 1.12.0 (`ccc01eb`)."""

    def test_same_text_for_every_fixture_and_scenario(self):
        # the scenarios `agents-*` (N16) draw a field that renderer 1.12.0 does not know: it ignores it, so those pages differ by design
        # (the next test shows the old renderer draws them like the same status without the field)
        # the scenarios with an `eta` (CORE-1150-ETA) show the estimate in the tile of the act and at the step instead of the notification bar:
        # they differ by design, `test_the_same_page_without_the_estimate` compares them with the field removed
        # the scenarios with spec questions or assumptions draw the new realize group: `test_scenarios_with_spec_questions_draw_both_groups` covers them (CORE-QTAB),
        # `test_the_same_page_without_the_questions_of_the_spec` compares them with 1.12.0 with those questions removed
        items = [item for item in plain(scenarios()) if not item["name"].startswith("agents-") and not item["json"].get("eta") and not item["json"].get("estimates")
                 and not has_spec_questions(item)]
        items = without_prototypes(items)   # CORE-2000-UNIFIED (assertions changed on purpose): the tab of the prototypes replaced the link next to the tabs
        new = scenario_run(RENDERER, items)
        old = scenario_run(OLD["1.12.0"], items)
        self.assertEqual(new["violations"], [])
        self.assertEqual(old["violations"], [])
        self.assertEqual(sorted(new["runs"]), sorted(old["runs"]))
        self.assertGreaterEqual(len(new["runs"]), len(fixture_files()))
        modes = deliveries(items)
        for name, snap in old["runs"].items():
            with self.subTest(scenario=name):
                # `board.t` (the `HTTP:` probe line) does not exist in the old renderer: that line alone is left out
                # assertions changed on purpose (CORE-QTAB): the tip of the questions tab is the other line that differs (old and new wording); without spec questions the new one is the default tip
                # assertions changed on purpose (CORE-1170-MODE): the two lines of the delivery-mode chip (new header chip) are removed from the new page, see `strip_mode_chip`
                self.assertEqual(page_text(new["runs"][name], modes[name], new=True), page_text(snap))
                self.assertIn(DEFAULT_TIP, new["runs"][name].split("\n"))
                self.assertIn(OLD_TIP, snap.split("\n"))

    def test_the_same_page_without_the_questions_of_the_spec(self):
        """CORE-QTAB: the scenarios with spec questions or assumptions (and no `current` of the expert group), drawn without the questions and assumptions of the spec, are the page of 1.12.0
        (apart from the tip of the questions tab): the questions of the spec are the only thing that moved."""
        # the `questions` family also loses its `current` (`without_spec_questions`): with it the page draws group headings of the new tab by design (covered by the next test)
        items = without_spec_questions([item for item in plain(scenarios()) if has_spec_questions(item) and not item["name"].startswith("agents-")
                                        and not item["json"].get("eta") and not item["json"].get("estimates")])
        self.assertGreaterEqual(len(items), 40)
        items = without_prototypes(items)   # CORE-2000-UNIFIED
        new, old = scenario_run(RENDERER, items), scenario_run(OLD["1.12.0"], items)
        self.assertEqual((new["violations"], old["violations"]), ([], []))
        self.assertEqual(sorted(new["runs"]), sorted(old["runs"]))
        modes = deliveries(items)
        for name, snap in old["runs"].items():
            with self.subTest(scenario=name):
                # assertions changed on purpose (CORE-1170-MODE): the delivery-mode chip lines are removed from the new page (`strip_mode_chip`)
                self.assertEqual(page_text(new["runs"][name], modes[name], new=True), page_text(snap))

    def test_scenarios_with_spec_questions_draw_both_groups(self):
        """CORE-QTAB: every scenario left out of `test_same_text_for_every_fixture_and_scenario` because of spec questions (or a `current`), AND the `agents-*`/`eta` ones that have them,
        is drawn by the new renderer with both groups, the counters and the marks the spec asks for (K1, K3, K6, K7, K8), computed from the same input."""
        items = [item for item in plain(scenarios()) if has_spec_questions(item)]
        names = {item["name"] for item in items}
        self.assertGreaterEqual(len(names), 40, "the scenarios left out of the golden snapshot: a drop means the filter lost them")
        self.assertTrue({"fixture:plan-in-progress", "fixture:questions", "fixture:spec-act", "questions-tab", "questions-realize-overflow", "questions-no-realize", "eta-prototype-stage-over",
                         "agents-live-round", "prototype-writing-waiting"} <= names, sorted(names))
        got = probe_cases([{"name": item["name"], "json": item["json"], "now": item["now"], "deps": item["deps"]} for item in items])
        self.assertEqual(sorted(got), sorted(names))
        for item in items:
            with self.subTest(scenario=item["name"]):
                assert_tab_matches_data(self, item["json"], got[item["name"]])   # rows, text/title, `+N więcej`, counters, tip, marks
        self.assertEqual(got["questions-realize-overflow"]["groups"][0]["more"], ["+31 więcej"], "the overflow scenario shows the note")

    def test_delivery_mode_chip_wording_follows_the_input_of_every_scenario(self):
        """CORE-1170-MODE: the chip of the mode is the only difference left by the normalisation, so it is proven here for EVERY scenario (also those left out of the golden snapshot):
        with a mode in the input the new page has the tip and the label (wording computed from the input), the frozen 1.12.0 has neither; without it neither has them."""
        items = plain(scenarios())
        extra = []
        for name, delivery in (("no-field", "@drop"), ("not-object", "direct"), ("numeric-mode", {"mode": 5, "base": "main"}), ("empty-mode", {"mode": "", "base": "main"}),
                               ("source-invalid", {"mode": None, "base": None, "source": "invalid"}), ("hostile-base", {"mode": "direct", "base": "<img src=x onerror=alert(1)>", "source": "manifest"})):
            item = clone([next(i for i in items if i["name"] == "fixture:plan-in-progress")])[0]
            item["name"] = "mode-" + name
            if delivery == "@drop":
                item["json"].pop("delivery")
            else:
                item["json"]["delivery"] = delivery
            extra.append(item)
        items += extra
        new, old = scenario_run(RENDERER, items), scenario_run(OLD["1.12.0"], items)
        self.assertEqual((new["violations"], old["violations"]), ([], []))
        with_chip = without = 0
        for item in items:
            name, lines = item["name"], new["runs"][item["name"]].split("\n")
            expected = mode_chip_lines(item["json"].get("delivery"))
            with self.subTest(scenario=name):
                if expected is None:
                    without += 1
                    self.assertFalse([line for line in lines if line.startswith(("Tryb dostarczania:", "tryb: ", "tryb dostarczania:"))])
                else:
                    with_chip += 1
                    self.assertGreaterEqual(sum(1 for i in range(len(lines) - 1) if (lines[i], lines[i + 1]) == expected), 1, expected)
                    self.assertNotIn(expected[1], old["runs"][name].split("\n"))
                    self.assertNotIn(expected[0], old["runs"][name].split("\n"))
        self.assertGreaterEqual(with_chip, 60)
        self.assertGreaterEqual(without, 3)
        self.assertEqual(new["runs"]["mode-hostile-base"].count("<img src=x onerror=alert(1)>"), 2, "the base is text (label and tip, a single dump)")
        self.assertEqual(mode_chip_lines({"mode": "invalid", "base": "x", "source": "manifest"})[1], "tryb dostarczania: błąd")
        # the normaliser itself can fail: a page without the chip, or with a different one, is refused
        page = new["runs"]["fixture:plan-in-progress"]
        with self.assertRaises(AssertionError):
            strip_mode_chip(page, {"mode": "pr", "base": "other", "source": "manifest"})
        with self.assertRaises(AssertionError):
            strip_mode_chip(page, None)

    def test_the_same_page_without_the_estimate(self):
        """With `eta` removed the page of the new renderer is the page of 1.12.0: the estimate is the only thing that moved (bar -> tile and step)."""
        items = [item for item in plain(scenarios()) if item["json"].get("eta") or item["json"].get("estimates")]
        self.assertGreaterEqual(len(items), 1)
        bare = without_prototypes(without_spec_questions(items))   # CORE-2000-UNIFIED; CORE-QTAB (assertions changed on purpose): also without spec questions; those pages are in `test_scenarios_with_spec_questions_draw_both_groups`
        for item in bare:
            item["json"]["eta"] = None
            item["json"].pop("estimates", None)   # the step tooltip of a finished series is new too
        new, old = scenario_run(RENDERER, bare), scenario_run(OLD["1.12.0"], bare)
        self.assertEqual((new["violations"], old["violations"]), ([], []))
        modes = deliveries(items)
        for name, snap in old["runs"].items():
            with self.subTest(scenario=name):
                # assertions changed on purpose (CORE-1170-MODE): the delivery-mode chip lines are removed from the new page (`strip_mode_chip`)
                self.assertEqual(page_text(new["runs"][name], modes[name], new=True), page_text(snap))

    def test_the_frozen_renderer_ignores_the_agents_field(self):
        items = [item for item in plain(scenarios()) if item["name"].startswith("agents-")]
        self.assertEqual(len(items), 3)
        bare = clone(items)
        for item in bare:
            item["json"].pop("agents")
        shown, without = scenario_run(OLD["1.12.0"], items), scenario_run(OLD["1.12.0"], bare)
        self.assertEqual(shown["violations"], [])
        self.assertEqual(shown["runs"], without["runs"])

    def test_status_with_lang_pl_and_foreign_texts_is_the_same_page(self):
        """`lang: pl` keeps the built-in text even when a (misbehaving) server sends `texts` of another language."""
        items = plain(scenarios())
        dressed = clone(items)
        for item in dressed:
            item["json"]["lang"] = "pl"
            item["json"]["texts"] = "@en"
        self.assertEqual(scenario_run(RENDERER, dressed)["runs"], scenario_run(RENDERER, items)["runs"])


GLOSSARY_TAIL = r"""
(async () => {
  const out = { runs: {} };
  for (const [lang, desc] of Object.entries(input.descriptions)) {
    const j = JSON.parse(JSON.stringify(input.data));
    if (lang === 'en') { j.lang = 'en'; j.texts = input.en; }
    const m = mount(j, { now: input.now });
    m.board.setDescriptions(desc);
    out.runs[lang] = { glossary: m.root.querySelectorAll('.glossary [data-glossary]').__asArray().map((x) => ({ key: x.getAttribute('data-glossary'), title: text(x.querySelector('dt')), text: text(x.querySelector('dd')) })),
      summary: text(m.root.querySelector('.glossary summary')), page: text(m.root) };
    m.board.destroy();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


class DescriptionFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pl = json.loads(DESCRIPTIONS.read_text(encoding="utf-8"))
        cls.en = json.loads(DESCRIPTIONS_EN.read_text(encoding="utf-8"))

    def test_the_same_keys_and_structure(self):
        self.assertEqual(list(self.en), list(self.pl))
        self.assertEqual(len(self.pl), 40)
        for key, item in self.pl.items():
            with self.subTest(key=key):
                self.assertEqual(sorted(self.en[key]), sorted(item))
                self.assertEqual(sorted(self.en[key]), ["text", "title"])
                self.assertTrue(all(isinstance(self.en[key][part], str) and self.en[key][part].strip() for part in ("title", "text")))
                # the same number of lines (the tooltip keeps its paragraphs)
                self.assertEqual(self.en[key]["text"].count("\n"), item["text"].count("\n"))

    def test_english_has_no_polish_character_and_no_angle_bracket(self):
        for key, item in self.en.items():
            for part, value in item.items():
                with self.subTest(key=key, part=part):
                    self.assertIsNone(POLISH.search(value))
                    self.assertNotRegex(value, r"[<>]")

    def test_english_is_a_translation_not_a_copy(self):
        same = [key for key, item in self.en.items() if item["text"] == self.pl[key]["text"]]
        self.assertEqual(same, [])

    def test_protocol_tokens_of_the_polish_text_stay_in_code_spans(self):
        """A token the parsers read (`czeka:`, `Przebieg:` ...) stays Polish inside backticks in the English text."""
        for key, item in self.pl.items():
            for token in re.findall(r"`(czeka:|Przebieg:|Oddanie:)", item["text"]):
                with self.subTest(key=key, token=token):
                    self.assertIn("`" + token, self.en[key]["text"])


@SKIP
class GlossaryFollowsTheLanguageOfThePage(unittest.TestCase):
    """K2 on the real page: the glossary shows the descriptions it was given, the English file in an English page (no Polish text left)."""

    def test_glossary_with_the_real_description_files(self):
        data = clone(fixture_files()["plan-in-progress"])
        pl = json.loads(DESCRIPTIONS.read_text(encoding="utf-8"))
        en = json.loads(DESCRIPTIONS_EN.read_text(encoding="utf-8"))
        out = run_with(RENDERER, HEAD + GLOSSARY_TAIL, {"data": data, "now": ms(data["generatedAt"]), "en": ui_catalog("en"), "descriptions": {"pl": pl, "en": en}})
        keys = [k for k in pl if k.startswith("legend_")]
        for lang, source in (("pl", pl), ("en", en)):
            got = out["runs"][lang]["glossary"]
            with self.subTest(lang=lang):
                self.assertEqual([item["key"] for item in got], keys)
                self.assertEqual([(item["title"], item["text"]) for item in got], [(source[k]["title"], source[k]["text"]) for k in keys])
        self.assertEqual(out["runs"]["en"]["summary"], "Concepts")
        glossary = " ".join(item["title"] + " " + item["text"] for item in out["runs"]["en"]["glossary"])
        self.assertIsNone(POLISH.search(glossary))


if __name__ == "__main__":
    unittest.main()
