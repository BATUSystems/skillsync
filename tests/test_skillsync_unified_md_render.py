"""CORE-2000-UNIFIED K21: the server-side Markdown renderers of the status page (`render_markdown`, `render_inline`, `inline_html`) on a
corpus of payloads. Their HTML is inserted as it is into the page of the consents, so no text of a document may become markup: only the tags
and attributes of the renderer, no attribute with `<` or a NUL, no `style`, links only to http(s), mailto or the page itself:
python3 -m unittest tests.test_skillsync_unified_md_render
"""
from __future__ import annotations

import sys
import unittest
from html.parser import HTMLParser
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard_http as dh  # noqa: E402

ALLOWED = {
    "a": {"href", "target", "rel", "class", "data-doc", "data-anchor"}, "span": {"class", "title", "role", "aria-label"},
    "code": {"class"}, "pre": {"class"}, "p": set(), "br": set(), "strong": set(), "em": set(), "del": set(), "ul": set(), "ol": set(),
    "li": {"class"}, "div": {"class"}, "table": set(), "thead": set(), "tbody": set(), "tr": set(), "th": {"class"}, "td": {"class"},
    "blockquote": set(), "hr": set(), **{f"h{level}": {"id"} for level in range(1, 7)},
}
STYLE = "style=position:fixed;inset:0;z-index:99999;"
INNER = (
    f"[x](https://b.example/{STYLE})",
    "[x](https://b.example/onmouseover=alert(1))",
    f"![{STYLE}](y)",
    f"`{STYLE}`",
    f"[x](docs/plans/{STYLE}.md)",
    f"[x](#{STYLE})",
    f"[x](missing/{STYLE})",
    f"<https://c.example/{STYLE}>",
)
OUTER = (
    "<http://e.example/{}>",
    "<https://e.example/a{}b>",
    "[y](https://e.example/{})",
    "[y](docs/plans/{}.md)",
    "[y](#{})",
    "[y](elsewhere/{})",
    "![alt {}](z)",
    "[{}](https://e.example/ok)",
)
PAYLOADS = (
    '<img src=x onerror="alert(1)">', "<script>alert(1)</script>", "[x](javascript:alert(1))", "[x](java\tscript:alert(1))",
    "[x](JAVASCRIPT:alert(1))", "[x](data:text/html,<script>alert(1)</script>)", "[x](vbscript:msgbox(1))", "<javascript:alert(1)>",
    "[x](https://a.example/\"onmouseover=\"alert(1))", "<https://a.example/\"onmouseover=\"alert(1)>", "[x](https://a.example/<b>)",
    "[[x](javascript:alert(1))](https://a.example/ok)", "\x000\x00 [x](https://a.example/\x000\x00)", "<https://a.example/\x000\x00>",
    "![\" onerror=\"alert(1)](x)", "[a](x\"onmouseover=\"alert(1) \"t\")", "| <http://e.example/[x](https://b.example/" + STYLE + ")> |\n|---|\n| 1 |",
)


class Collect(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append((tag, attrs))


def corpus() -> list:
    return [*PAYLOADS, *(outer.format(inner) for outer, inner in product(OUTER, INNER)),
            *(outer.format(inner.format(deepest)) for outer, inner, deepest in
              product(OUTER[:3], OUTER[:3], INNER[:3]))]


def renderers(text: str) -> dict:
    """Every way a text of the project reaches the page as HTML."""
    return {
        "render_markdown": dh.render_markdown(text, "docs/plans/p.md"),
        "paragraph and list": dh.render_markdown(f"## {text}\n\n- {text}\n- [ ] {text}\n\n> {text}\n", "docs/plans/p.md"),
        "render_inline": dh.render_inline(text, "docs/plans"),
        "inline_html": dh.inline_html(text) or "",
        "inline_html cut": dh.inline_html(text, 60) or "",
        "feed": dh.decorate_feed_html(dh.inline_html(text) or ""),
    }


class MarkupTests(unittest.TestCase):
    def test_no_text_becomes_markup(self) -> None:
        for text in corpus():
            for name, rendered in renderers(text).items():
                with self.subTest(renderer=name, text=text):
                    self.assertNotIn("\x00", rendered)
                    parsed = Collect()
                    parsed.feed(rendered)
                    parsed.close()
                    for tag, attrs in parsed.tags:
                        self.assertIn(tag, ALLOWED, rendered)
                        for key, value in attrs:
                            self.assertIn(key, ALLOWED[tag], rendered)
                            self.assertNotIn("<", value or "", rendered)
                            self.assertNotIn("\x00", value or "", rendered)
                            if key == "href":
                                self.assertRegex(value, r"^(https?://|mailto:|#$)", rendered)

    def test_control_the_links_of_a_document_still_work(self) -> None:
        for text, expected in (
            ("[a](https://a.example/p?q=1&r=2)", '<a href="https://a.example/p?q=1&amp;r=2" target="_blank" rel="noopener noreferrer">a</a>'),
            ("<https://a.example/p>", '<a href="https://a.example/p" target="_blank" rel="noopener noreferrer">https://a.example/p</a>'),
            ("[s](../specs/s.md)", '<a href="#" class="md-doc" data-doc="docs/specs/s.md">s</a>'),
            ("[k](#krok-1)", '<a href="#" class="md-anchor" data-anchor="md-krok-1">k</a>'),
            ("[**b**](https://a.example/)", '<a href="https://a.example/" target="_blank" rel="noopener noreferrer"><strong>b</strong></a>'),
        ):
            with self.subTest(text=text):
                self.assertEqual(dh.render_inline(text, "docs/plans"), expected)

    def test_the_corpus_covers_the_nesting_that_broke_the_renderer(self) -> None:
        self.assertIn(f"<http://e.example/[x](https://b.example/{STYLE})>", corpus())


ADDRESSES = ("jan.pass@example.com", "jan.__pass__@example.com", "jan.**pass**@example.com", "x.PASS-1@example.com", "j~~a~~n@example.com",
             "ala@pass.example.com")


class AnonymousHtmlTests(unittest.TestCase):
    """The e-mail mask of the page without a session also holds for the `*Html` fields, whatever tags the renderer put inside the address."""

    @staticmethod
    def shown(source: str) -> dict:
        data = {"progress": {"phases": [{"steps": [{"title": source}]}]}, "run": {"checkpoints": [{"text": source}]},
                "result": {"text": source, "path": "docs/plans/p.md", "html": dh.render_markdown(source),
                           "moreHtml": dh.render_markdown(source), "moreLines": 1}}
        return dh.public_tree(dh.add_inline_html(data), dh.public_text_filter([]))

    @staticmethod
    def visible(value) -> str:
        class Text(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list = []

            def handle_data(self, data) -> None:
                self.parts.append(data)

        if isinstance(value, dict):
            return " ".join(AnonymousHtmlTests.visible(item) for item in value.values())
        if isinstance(value, list):
            return " ".join(AnonymousHtmlTests.visible(item) for item in value)
        parser = Text()
        parser.feed(str(value))
        parser.close()
        return "".join(parser.parts)

    def test_no_address_in_the_visible_text_of_any_field(self) -> None:
        for address in ADDRESSES:
            for source in (f"**ważne** {address} koniec", f"{address} `a`", f"- [ ] {address}"):
                with self.subTest(source=source):
                    text = self.visible(self.shown(source))
                    self.assertNotIn("@example.com", text, text)
                    self.assertNotIn("@pass", text, text)

    def test_control_formatting_without_an_address_and_a_whole_address_stay_formatted(self) -> None:
        plain = self.shown("**ważne** PASS krok S1")
        self.assertIn("<strong>ważne</strong>", plain["progress"]["phases"][0]["steps"][0]["titleHtml"])
        self.assertIn('<span class="ph">PASS</span>', plain["run"]["checkpoints"][0]["textHtml"])
        self.assertIn("html", plain["result"])
        whole = self.shown("**ważne** adres: kto@example.com")
        self.assertIn("<strong>ważne</strong>", whole["progress"]["phases"][0]["steps"][0]["titleHtml"])
        self.assertNotIn("kto@", whole["progress"]["phases"][0]["steps"][0]["titleHtml"])


if __name__ == "__main__":
    unittest.main()
