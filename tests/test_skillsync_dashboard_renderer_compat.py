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


def plain(items):
    out = clone(items)
    for item in out:
        item["json"].pop("lang", None)
        item["json"].pop("texts", None)
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
        items = [item for item in plain(scenarios()) if not item["name"].startswith("agents-")]
        new = scenario_run(RENDERER, items)
        old = scenario_run(OLD["1.12.0"], items)
        self.assertEqual(new["violations"], [])
        self.assertEqual(old["violations"], [])
        self.assertEqual(sorted(new["runs"]), sorted(old["runs"]))
        self.assertGreaterEqual(len(new["runs"]), len(fixture_files()))
        for name, snap in old["runs"].items():
            with self.subTest(scenario=name):
                # `board.t` (the `HTTP:` probe line) does not exist in the old renderer: that line alone is left out
                self.assertEqual(re.sub(r"\nHTTP:.*$", "", new["runs"][name]), re.sub(r"\nHTTP:.*$", "", snap))

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
