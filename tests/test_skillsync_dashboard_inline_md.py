"""Inline Markdown on the status page: step titles, "waiting for" items and expert questions get `titleHtml`, `whatHtml`, `textHtml` from
`/api/status` (the same safe inline renderer as the document view, no blocks). The plain fields stay; a text without formatting gets no
`*Html`. Examples are neutral (no project data).
Run: `python3 -m unittest tests.test_skillsync_dashboard_inline_md`."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402

RENDER = Path(__file__).resolve().parent.parent / "library" / "skills" / "dashboard" / "assets" / "dashboard-render.js"


class InlineHtmlTests(unittest.TestCase):
    def test_bold_italic_code_and_safe_link(self) -> None:
        html = srv.inline_html("**Waga** _lekka_ `kod` [dok](https://example.com/a)")
        self.assertIn("<strong>Waga</strong>", html)
        self.assertIn("<em>lekka</em>", html)
        self.assertIn("<code>kod</code>", html)
        self.assertIn('href="https://example.com/a"', html)

    def test_plain_text_gets_no_html(self) -> None:
        self.assertIsNone(srv.inline_html("zwykły tytuł kroku"))
        self.assertIsNone(srv.inline_html(""))
        self.assertIsNone(srv.inline_html(None))
        self.assertIsNone(srv.inline_html("a & b < c"), "escaping alone is not formatting")

    def test_raw_html_is_escaped_and_unsafe_links_are_not_links(self) -> None:
        html = srv.inline_html("**x** <img src=x onerror=alert(1)> <script>a</script> [zły](javascript:alert(1))")
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn("&lt;img", html)

    def test_blocks_are_not_rendered(self) -> None:
        html = srv.inline_html("# nagłówek **b**\n- punkt\n| a | b |")
        self.assertNotIn("<h1", html)
        self.assertNotIn("<ul", html)
        self.assertNotIn("<table", html)
        self.assertNotIn("<br", html)


class FeedHighlightTests(unittest.TestCase):
    """A formatted checkpoint row keeps the highlighting of step ids and key phrases of a plain row (`decorateCheckpoint` of the page)."""

    def feed(self, text: str) -> str:
        d = srv.add_inline_html({"run": {"checkpoints": [{"time": "10:00", "text": text}]}})
        return d["run"]["checkpoints"][0].get("textHtml", "")

    def test_ids_and_phrases_are_highlighted_in_a_formatted_row(self) -> None:
        html = self.feed("**zrobione** S1 i F2.1; w toku: S2a, ryzyko PASS")
        self.assertIn('<strong><span class="ph">zrobione</span></strong>', html)
        self.assertIn('<span class="sidc">S1</span>', html)
        self.assertIn('<span class="sidc">S2a</span>', html)
        self.assertIn('<span class="ph">w toku</span>', html)
        self.assertIn('<span class="ph">ryzyko</span>', html)
        self.assertIn('<span class="ph">PASS</span>', html)

    def test_nothing_is_highlighted_inside_code_or_a_link(self) -> None:
        html = self.feed("`S1 zrobione` oraz [S2 w toku](https://example.com/S3) **S4**")
        self.assertIn("<code>S1 zrobione</code>", html)
        self.assertNotIn('<span class="sidc">S2', html)
        self.assertNotIn('<span class="ph">w toku</span></a>', html)
        self.assertIn('<a href="https://example.com/S3" target="_blank" rel="noopener noreferrer">S2 w toku</a>', html)
        self.assertIn('<span class="sidc">S4</span>', html)

    def test_attributes_are_never_touched(self) -> None:
        html = self.feed("[zobacz](https://example.com/S1/zrobione) **ok**")
        self.assertIn('href="https://example.com/S1/zrobione"', html)
        self.assertNotIn('href="https://example.com/<', html)

    def test_markup_in_the_text_is_not_injected(self) -> None:
        html = self.feed("**zrobione** <script>alert(1)</script> <img src=x onerror=alert(1)> & S1")
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&amp;", html)
        self.assertIn('<span class="sidc">S1</span>', html)

    def test_a_row_without_formatting_gets_no_html_and_stays_as_before(self) -> None:
        self.assertEqual(self.feed("zrobione S1; w toku: S2"), "")

    def test_the_same_patterns_as_the_page(self) -> None:
        code = RENDER.read_text(encoding="utf-8")
        self.assertIn("const ID_RE = /(?<![\\w-])([A-Z]{1,3}-?\\d+[a-z]?)(?![\\w-])/g;", code)
        self.assertIn("const PHRASE_RE = /\\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\\b/gi;", code)
        self.assertEqual(srv._FEED_ID.pattern, r"(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])")
        self.assertEqual(srv._FEED_PHRASE.pattern, r"\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b")


class CutTests(unittest.TestCase):
    def test_a_short_text_is_not_cut(self) -> None:
        self.assertEqual(srv.cut_inline("krótki", 160), "krótki")

    def test_the_cut_never_falls_inside_a_span(self) -> None:
        for span in ("`kod w środku`", "**pogrubione słowa**", "[etykieta](https://example.com/dlugi/adres)", "~~skreślone~~"):
            text = "x" * 20 + span + " koniec"
            for limit in range(21, 20 + len(span)):
                cut = srv.cut_inline(text, limit)
                self.assertEqual(cut, "x" * 20 + "…", (span, limit))

    def test_the_cut_text_renders_without_stray_markers_or_open_tags(self) -> None:
        text = "Pytanie " + "**ważne** " * 30
        html = srv.inline_html(text, 160)
        self.assertEqual(html.count("<strong>"), html.count("</strong>"))
        self.assertNotIn("**", html)
        self.assertTrue(html.endswith("…"))

    def test_a_text_that_fits_after_the_span_is_cut_after_it(self) -> None:
        self.assertEqual(srv.cut_inline("`a` " + "y" * 50, 10), "`a` yyyyyy…")


class StatusFieldsTests(unittest.TestCase):
    def data(self) -> dict:
        return {
            "progress": {"phases": [{"steps": [{"id": "F1.1", "title": "Krok **ważny**"}, {"id": "F1.2", "title": "Krok zwykły"}]}],
                         "current": {"id": "F1.1", "title": "Krok **ważny**"}},
            "substeps": [{"id": "F1.1", "title": "`kod` w tytule"}],
            "waiting": [{"what": "decyzja o **zakresie**", "action": "Sesja czeka: decyzja o **zakresie**. Odpowiedz w sesji.", "source": "checkpoint"}, {"what": "zwykłe", "source": "checkpoint"}],
            "expertQuestions": {"items": [{"id": "Q1", "text": "Czy **tak**?"}, {"id": "Q2", "text": "zwykłe pytanie"}],
                                "current": {"spec": "docs/specs/x.md", "prototypes": [], "open": 1, "blocking": 0,
                                            "items": [{"id": "Q3", "text": "Czy `kod` działa?"}, {"id": "Q4", "text": "zwykłe"}]}},
            "run": {"checkpoints": [{"time": "10:00", "text": "zrobione **S1**"}, {"time": "10:05", "text": "zwykły wiersz"}]},
            "result": None,
        }

    def test_fields_are_added_only_for_formatted_texts_and_plain_ones_stay(self) -> None:
        d = srv.add_inline_html(self.data())
        first, second = d["progress"]["phases"][0]["steps"]
        self.assertEqual(first["titleHtml"], "Krok <strong>ważny</strong>")
        self.assertEqual(first["title"], "Krok **ważny**")
        self.assertNotIn("titleHtml", second)
        self.assertEqual(d["progress"]["current"]["titleHtml"], "Krok <strong>ważny</strong>")
        self.assertEqual(d["substeps"][0]["titleHtml"], "<code>kod</code> w tytule")
        self.assertEqual(d["waiting"][0]["whatHtml"], "decyzja o <strong>zakresie</strong>")
        self.assertEqual(d["waiting"][0]["actionHtml"], "Sesja czeka: decyzja o <strong>zakresie</strong>. Odpowiedz w sesji.")
        self.assertNotIn("whatHtml", d["waiting"][1])
        self.assertEqual(d["expertQuestions"]["items"][0]["textHtml"], "Czy <strong>tak</strong>?")
        self.assertNotIn("textHtml", d["expertQuestions"]["items"][1])
        current = d["expertQuestions"]["current"]["items"]
        self.assertEqual(current[0]["textHtml"], "Czy <code>kod</code> działa?")
        self.assertEqual(current[0]["text"], "Czy `kod` działa?")
        self.assertNotIn("textHtml", current[1])
        feed = d["run"]["checkpoints"]
        self.assertEqual(feed[0]["textHtml"], '<span class="ph">zrobione</span> <strong><span class="sidc">S1</span></strong>')
        self.assertEqual(feed[0]["text"], "zrobione **S1**")
        self.assertNotIn("textHtml", feed[1])

    def test_missing_or_odd_sections_are_tolerated(self) -> None:
        self.assertEqual(srv.add_inline_html({}), {})
        odd = {"progress": None, "substeps": None, "waiting": [None, 3], "expertQuestions": {"items": None, "current": {"items": None}}}
        self.assertEqual(srv.add_inline_html(odd), odd)
        for current in (None, "x", 7, [], {}, {"items": [None, 3, "x"]}):
            data = {"expertQuestions": {"items": [], "current": current}}
            self.assertEqual(srv.add_inline_html(data), {"expertQuestions": {"items": [], "current": current}})

    def test_a_long_question_text_is_cut_in_the_source_not_in_the_html(self) -> None:
        text = "**" + "bardzo " * 40 + "długie**"
        d = srv.add_inline_html({"expertQuestions": {"items": [{"id": "Q", "text": text}]}})
        html = d["expertQuestions"]["items"][0].get("textHtml", "")
        self.assertNotIn("<strong>", html, "the span the limit would cut is dropped whole")
        self.assertEqual(d["expertQuestions"]["items"][0]["text"], text, "the plain text is not changed")

    def test_a_long_text_of_the_current_run_is_cut_like_the_project_one(self) -> None:
        text = "**" + "bardzo " * 40 + "długie**"
        short = "Czy **tak**?"
        d = srv.add_inline_html({"expertQuestions": {"items": [], "current": {"items": [{"id": "Q", "text": text}, {"id": "R", "text": short}]}}})
        items = d["expertQuestions"]["current"]["items"]
        self.assertNotIn("<strong>", items[0].get("textHtml", ""), "the span the limit would cut is dropped whole")
        self.assertEqual(items[0]["text"], text)
        self.assertEqual(items[1]["textHtml"], "Czy <strong>tak</strong>?")

    def test_the_api_adds_it_and_show_does_not(self) -> None:
        """The same function marks `/api/status`; `dashboard show` and `build_status` never carry `textHtml`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".github").mkdir()
            (root / ".github" / ".skillsync.yaml").write_text("skills: [dashboard]\n", encoding="utf-8")
            (root / "docs" / "specs").mkdir(parents=True)
            (root / "docs" / "specs" / "2026-02-01-demo.md").write_text("# Spec: demo\n\nStatus: draft\n", encoding="utf-8")
            directory = root / "docs" / "prototypes" / "run-a"
            directory.mkdir(parents=True)
            (directory / "prototype.json").write_text(json.dumps({"requirements": "docs/specs/2026-02-01-demo.md"}), encoding="utf-8")
            ask = {"id": "o1", "type": "ask", "questionId": "qn-000001", "at": "2026-01-11T09:00:00Z", "by": "agent", "text": "Czy **tak**?",
                   "blocking": True, "assumed": None, "addressees": [], "refs": [], "options": [],
                   "origin": {"spec": "docs/specs/2026-02-01-demo.md", "q": "Q1"}}
            (directory / "questions.json").write_text(json.dumps({"questionsFormat": 1, "operations": [ask]}), encoding="utf-8")
            status = engine.build_status(root, approvals=False)
            self.assertNotIn("textHtml", json.dumps(status["expertQuestions"]))
            served = srv.add_inline_html(json.loads(json.dumps(status)))
            self.assertEqual(served["expertQuestions"]["current"]["items"][0]["textHtml"], "Czy <strong>tak</strong>?")
            self.assertEqual(served["expertQuestions"]["items"][0]["textHtml"], "Czy <strong>tak</strong>?")


class PageTests(unittest.TestCase):
    def test_the_page_inserts_inline_html_only_through_mount_html(self) -> None:
        code = RENDER.read_text(encoding="utf-8")
        start = code.index("const inl = (cls, text, html)")
        body = code[start:code.index("const kit =", start)]
        self.assertNotIn("innerHTML", body)
        self.assertIn("deps.mountHtml", body)


if __name__ == "__main__":
    unittest.main()
