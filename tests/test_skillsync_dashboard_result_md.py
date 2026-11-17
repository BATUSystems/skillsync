"""The `## Wynik` block of the status page is formatted Markdown (CORE-RESULT-MD): `/api/status` adds `result.html` (visible part) and
`result.moreHtml` + `result.moreLines` (collapsed rest), rendered by the same safe `render_markdown` as `/api/doc`. The cut for the collapsed
part falls only between Markdown blocks. Examples are neutral (no project data).
Run: `python3 -m unittest tests.test_skillsync_dashboard_result_md`."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard_http as srv  # noqa: E402


def items(count: int, prefix: str = "Krok") -> str:
    return "\n".join(f"- **{prefix} {i}:** `abc{i}` opis" for i in range(1, count + 1))


def table(rows: int) -> str:
    return "| Uwaga | Waga | Co zrobiono |\n|---|---|---|\n" + "\n".join(f"| punkt {i} | niska | poprawione |" for i in range(rows))


class SplitTests(unittest.TestCase):
    def test_short_text_is_not_cut(self) -> None:
        head, rest, count = srv.split_result("### A\n\n" + items(3))
        self.assertEqual((rest, count), ("", 0))
        self.assertIn("Krok 3", head)

    def test_a_long_list_is_never_cut_in_the_middle(self) -> None:
        text = "### Zmiany\n\n" + items(20) + "\n\n### Uwagi\n\nkoniec"
        head, rest, count = srv.split_result(text)
        self.assertTrue(head.endswith("Krok 20:** `abc20` opis"), head[-40:])
        self.assertTrue(rest.startswith("### Uwagi"))
        self.assertEqual(count, len(rest.split("\n")))

    def test_a_table_is_never_cut(self) -> None:
        text = "### Zmiany\n\n" + items(9) + "\n\n### Uwagi\n\n" + table(15) + "\n\nkoniec"
        head, rest, _count = srv.split_result(text)
        self.assertIn("### Uwagi", rest)
        self.assertEqual(rest.count("| punkt "), 15, "the whole table is in one part")
        self.assertEqual(head.count("|"), 0)

    def test_a_table_that_starts_before_the_limit_stays_whole(self) -> None:
        text = "Wstęp\n\n" + table(20) + "\n\nkoniec"
        head, rest, _count = srv.split_result(text)
        self.assertEqual((head.count("| punkt "), rest.count("| punkt ")) in ((20, 0), (0, 20)), True)

    def test_a_fenced_code_block_is_never_cut(self) -> None:
        code = "```\n" + "\n".join(f"linia {i}" for i in range(20)) + "\n\nz pustą linią\n```"
        head, rest, _count = srv.split_result("Wstęp\n\n" + code + "\n\nKoniec")
        self.assertEqual(head.count("```") % 2, 0, head)
        self.assertEqual(rest.count("```") % 2, 0, rest)

    def test_a_loose_list_stays_in_one_list(self) -> None:
        loose = "\n\n".join(f"- punkt {i}" for i in range(1, 9))
        head, rest, _count = srv.split_result("### Lista\n\n" + loose + "\n\n### Dalej\n\n" + items(12))
        self.assertTrue(head.rstrip().endswith("- punkt 8"), head[-30:])
        self.assertTrue(rest.startswith("### Dalej"))

    def test_the_head_is_never_only_a_heading(self) -> None:
        head, rest, _count = srv.split_result("### Tytuł\n\n" + items(30) + "\n\n### Dalej\n\nx")
        self.assertIn("Krok 30", head, "the first block after the title is long: it stays whole")
        self.assertTrue(rest.startswith("### Dalej"))

    def test_text_without_any_cut_point_is_not_collapsed(self) -> None:
        head, rest, count = srv.split_result(items(30))
        self.assertEqual((rest, count), ("", 0))
        self.assertIn("Krok 30", head)

    def test_the_last_cut_point_before_the_limit_is_preferred(self) -> None:
        text = "Akapit pierwszy.\n\nAkapit drugi.\n\n" + "\n\n".join(f"Akapit {i}." for i in range(3, 20))
        head, _rest, count = srv.split_result(text, 12)
        self.assertLessEqual(len(head.split("\n")), 12)
        self.assertGreater(count, 0)


class LineEndTests(unittest.TestCase):
    """The text of `## Wynik` is project text: it is cut only at CRLF, LF and a lone CR (`common.file_lines`), never at LS, PS, NEL, VT, FF, FS, GS, RS."""

    RARE = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e")

    def test_a_rare_line_end_does_not_cut_a_line_or_change_the_count(self) -> None:
        for char in self.RARE:
            with self.subTest(char=repr(char)):
                text = "### A\n\n" + items(20).replace("Krok 3:", f"Krok{char}3:") + "\n\n### B\n\nkoniec"
                head, rest, count = srv.split_result(text)
                self.assertIn(f"Krok{char}3:", head, "the line with the character stays whole")
                self.assertTrue(rest.startswith("### B"))
                self.assertEqual(count, len(rest.split("\n")))
                self.assertEqual(count, len(srv.split_result(text.replace(char, " "))[1].split("\n")), "the same cut as without the character")

    def test_crlf_and_a_lone_cr_still_cut_lines(self) -> None:
        base = "### A\n\n" + items(20) + "\n\n### B\n\nkoniec"
        for end in ("\r\n", "\r"):
            with self.subTest(end=repr(end)):
                head, rest, count = srv.split_result(base.replace("\n", end))
                self.assertTrue(rest.startswith("### B"))
                self.assertEqual(count, 3)

    def test_result_html_with_ls_in_the_text_does_not_split_the_item(self) -> None:
        answer = srv.result_html({"text": "- **Krok\u2028jeden:** opis\n- drugi", "path": "docs/plans/x.md"})
        self.assertIn("Krok\u2028jeden", answer["html"])
        self.assertEqual(answer["html"].count("<li>"), 2)


class RenderTests(unittest.TestCase):
    def test_result_html_formats_headings_lists_bold_code_and_tables(self) -> None:
        answer = srv.result_html({"text": "### Zmiany i dowody\n\n" + items(3) + "\n\n" + table(2), "path": "docs/plans/x.md"})
        html = answer["html"]
        self.assertIn("<h3", html)
        self.assertIn("<ul>", html)
        self.assertIn("<strong>Krok 1:</strong>", html)
        self.assertIn("<code>abc1</code>", html)
        self.assertIn('<div class="md-table"><table>', html)
        self.assertNotIn("moreHtml", answer)
        self.assertEqual(answer["text"].split("\n")[0], "### Zmiany i dowody", "`text` and `path` stay as the contract says")
        self.assertEqual(answer["path"], "docs/plans/x.md")

    def test_the_collapsed_rest_is_rendered_too(self) -> None:
        answer = srv.result_html({"text": "### A\n\n" + items(20) + "\n\n### B\n\n" + table(3), "path": "docs/plans/x.md"})
        self.assertIn("<table>", answer["moreHtml"])
        self.assertNotIn("<table>", answer["html"])
        self.assertEqual(answer["moreLines"], 7)

    def test_raw_html_is_escaped_and_unsafe_links_are_not_links(self) -> None:
        text = "<script>alert(1)</script> <img src=x onerror=alert(1)>\n\n[zły](javascript:alert(1)) [dobry](https://example.com/a)"
        html = srv.result_html({"text": text, "path": "docs/plans/x.md"})["html"]
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="https://example.com/a"', html)

    def test_the_renderer_of_the_page_does_not_use_inner_html_for_the_result(self) -> None:
        code = (Path(__file__).resolve().parent.parent / "library" / "skills" / "dashboard" / "assets" / "dashboard-render.js").read_text(encoding="utf-8")
        start = code.index("function renderResult(")
        body = code[start:code.index("// Legenda pojęć", start)]
        self.assertNotIn("innerHTML", body)
        self.assertIn("deps.mountHtml", body)


if __name__ == "__main__":
    unittest.main()
