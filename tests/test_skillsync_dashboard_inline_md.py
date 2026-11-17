"""Inline Markdown on the status page: step titles, "waiting for" items and expert questions get `titleHtml`, `whatHtml`, `textHtml` from
`/api/status` (the same safe inline renderer as the document view, no blocks). The plain fields stay; a text without formatting gets no
`*Html`. Examples are neutral (no project data).
Run: `python3 -m unittest tests.test_skillsync_dashboard_inline_md`."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
            "expertQuestions": {"items": [{"id": "Q1", "text": "Czy **tak**?"}, {"id": "Q2", "text": "zwykłe pytanie"}]},
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
        feed = d["run"]["checkpoints"]
        self.assertEqual(feed[0]["textHtml"], "zrobione <strong>S1</strong>")
        self.assertEqual(feed[0]["text"], "zrobione **S1**")
        self.assertNotIn("textHtml", feed[1])

    def test_missing_or_odd_sections_are_tolerated(self) -> None:
        self.assertEqual(srv.add_inline_html({}), {})
        odd = {"progress": None, "substeps": None, "waiting": [None, 3], "expertQuestions": {"items": None}}
        self.assertEqual(srv.add_inline_html(odd), odd)

    def test_a_long_question_text_is_cut_in_the_source_not_in_the_html(self) -> None:
        text = "**" + "bardzo " * 40 + "długie**"
        d = srv.add_inline_html({"expertQuestions": {"items": [{"id": "Q", "text": text}]}})
        html = d["expertQuestions"]["items"][0].get("textHtml", "")
        self.assertNotIn("<strong>", html, "the span the limit would cut is dropped whole")
        self.assertEqual(d["expertQuestions"]["items"][0]["text"], text, "the plain text is not changed")


class PageTests(unittest.TestCase):
    def test_the_page_inserts_inline_html_only_through_mount_html(self) -> None:
        code = RENDER.read_text(encoding="utf-8")
        start = code.index("const inl = (cls, text, html)")
        body = code[start:code.index("const kit =", start)]
        self.assertNotIn("innerHTML", body)
        self.assertIn("deps.mountHtml", body)


if __name__ == "__main__":
    unittest.main()
