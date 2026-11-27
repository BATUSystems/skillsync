"""CORE-2000-UNIFIED K21 and K2, every way the HTML of the renderers leaves the server: the masks of secrets and of the anonymous view must
not change the markup (B1), a hostile document must neither crash (M1) nor stall (M2) the server, and an address split by Markdown stays
masked (F1, F2): python3 -m unittest tests.test_skillsync_unified_render_paths
"""
from __future__ import annotations

import random
import re
import shutil
import sys
import tempfile
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_skillsync_unified_md_render as markup  # noqa: E402  (the module, so its test classes are not collected here)
from tmp_paths import descend  # noqa: E402

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import status  # noqa: E402
from skillsync_workflow import dashboard_tests  # noqa: E402
from skillsync_workflow.common import cut_whole, redact  # noqa: E402

EVIL = "https://evil.example/"
# the reproduction of the review: the mask of the value after `token=` swallowed the end of an attribute and of its tag
B1 = ('[A](token=y ) B"style=position:fixed;inset:0;z-index:99999;font-size:90vw;opacity:0 **z** [WWWW](' + EVIL + ")")
SECRET = "".join(("Kanarek", "Sekretny", "Napis", "73"))
BUDGET = 1.0


class Parsed(HTMLParser):
    def __init__(self, rendered: str) -> None:
        super().__init__()
        self.tags: list = []
        self.open: list = []
        self.broken: list = []
        self.text: list = []
        self.feed(rendered)
        self.close()

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append((tag, attrs))
        if tag not in ("br", "hr"):
            self.open.append(tag)

    def handle_endtag(self, tag) -> None:
        if tag != "li" and (not self.open or tag not in self.open):
            self.broken.append(tag)
        while self.open and self.open.pop() != tag:
            pass

    def handle_data(self, data) -> None:
        self.text.append(data)


def project(case: unittest.TestCase, body: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="render-paths-")).resolve()
    case.addCleanup(shutil.rmtree, root, True)
    for rel, text in (("docs/plans/p.md", f"# Plan: P\n\nStatus: in_progress\nPrzebieg: r1\n\n## Progress\n\n- [ ] S1 {body}\n\n## Wynik\n\n{body}\n"),
                      (".skillsync/runs/r1/checkpoints.md", f"RUN=2026-01-10T10:00:00+01:00\n- 10:00 {body}; czeka: CP {body}\n"),
                      (".github/.skillsync.yaml", "skills: [dashboard]\ndelivery:\n  mode: direct\n  base: main\n")):
        path = descend(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def html_fields(value, key: str = "") -> list:
    if isinstance(value, dict):
        return [found for name, item in value.items() for found in html_fields(item, name)]
    if isinstance(value, list):
        return [found for item in value for found in html_fields(item, key)]
    return [value] if isinstance(value, str) and (key in ("html", "moreHtml") or key.endswith("Html")) else []


def strings(value) -> list:
    if isinstance(value, dict):
        return [found for item in value.values() for found in strings(item)]
    if isinstance(value, list):
        return [found for item in value for found in strings(item)]
    return [value] if isinstance(value, str) else []


def source_paths(text: str) -> dict:
    """What leaves the server for `text`, from a source that is already masked (as the readers of plans and checkpoints mask it)."""
    source = redact(text)
    data = dh.add_inline_html({"run": {"checkpoints": [{"text": source}]}, "substeps": [{"title": source}],
                               "result": dh.result_html({"text": source, "path": "docs/plans/p.md"})})
    signed = dashboard_tests.finalize_status(data, False)
    anonymous = dh.public_status(dict(signed), None, [])
    return {"doc": redact({"html": dh.render_markdown(source, "docs/plans/p.md")}), "signed": signed, "anonymous": anonymous}


class MarkupTests(unittest.TestCase):
    def assert_safe(self, rendered: str, source: str) -> None:
        """Only the tags and attributes of the renderer (emphasis marks that cross, `**a ~~b** c~~`, may misnest their own tags)."""
        parsed = Parsed(rendered)
        for tag, attrs in parsed.tags:
            self.assertIn(tag, markup.ALLOWED, (source, rendered))
            for key, value in attrs:
                self.assertIn(key, markup.ALLOWED[tag], (source, rendered))
                self.assertNotIn("<", value or "", (source, rendered))
                if key == "href":
                    self.assertRegex(value, r"^(https?://|mailto:|#$)", (source, rendered))

    def test_the_reproduction_of_the_review_on_every_path(self) -> None:
        root = project(self, B1)
        board = dh.Board(root)
        answers = {"doc": board.doc("docs/plans/p.md"), "checkpoints": board.doc(".skillsync/runs/r1/checkpoints.md"),
                   "status": dashboard_tests.finalize_status(board.status(None), False), **source_paths(B1)}
        answers["anonymous status"] = dh.public_status(dict(answers["status"]), None, [root])
        seen = 0
        for name, answer in answers.items():
            for rendered in html_fields(answer):
                with self.subTest(path=name):
                    seen += 1
                    self.assert_safe(rendered, B1)
                    self.assertEqual(Parsed(rendered).broken, [], rendered)
                    for tag, attrs in Parsed(rendered).tags:
                        self.assertNotIn("style", dict(attrs))
        self.assertGreaterEqual(seen, 6, "the reproduction reaches the HTML of the answers")

    def test_random_documents_with_secrets_keep_the_markup(self) -> None:
        atoms = ["[", "]", "(", ")", "<", ">", "!", "`", "**", "_", "~~", '"', "'", " ", " ", "\n", "|", "&", "#", "- ", "> ", "x", "http://", EVIL,
                 "token=", "password: ", "secret=", "pwd=", "PAT: ", "api_key=", "Authorization: Bearer ", "://u:p@", "style=", "](", "[A](",
                 "<https://e.example/", "```", "\n\n", "jan@example.com", "​"]
        rng = random.Random(2000)
        for _ in range(2500):
            text = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 30)))
            for name, answer in source_paths(text).items():
                for rendered in html_fields(answer):
                    with self.subTest(path=name, text=text):
                        self.assert_safe(rendered, text)

    def test_a_secret_is_still_masked_in_the_html_also_when_formatting_splits_it(self) -> None:
        for text in (f"password: {SECRET}", f"token=**{SECRET}**", f"token=`{SECRET}`", f"to**ken**={SECRET}", f"[token]({EVIL})={SECRET}",
                     f"- secret: _{SECRET}_", f"| token={SECRET} | b |\n|---|---|\n| 1 | 2 |"):
            root = project(self, text.replace("\n", " "))
            descend(root, "docs/plans/p.md").write_text(text + "\n", encoding="utf-8")
            with self.subTest(text=text):
                answer = dh.Board(root).doc("docs/plans/p.md")
                self.assertNotIn(SECRET, "".join(Parsed(answer["html"]).text))
                self.assertNotIn(SECRET, answer["html"])

    def test_control_an_ordinary_document_keeps_its_formatting(self) -> None:
        root = project(self, "**ważne** [a](https://a.example/) `kod`")
        answer = dh.Board(root).doc("docs/plans/p.md")
        for expected in ("<strong>ważne</strong>", '<a href="https://a.example/"', "<code>kod</code>", "<h1"):
            self.assertIn(expected, answer["html"])


HOSTILE = {
    "heading": "# a" + " " * 100000 + "x", "heading hashes": "# " + "#" * 100000 + " x", "table separator": "a|b\n" + " " * 50000 + "--" + " " * 50000 + "x",
    "separator cells": "a|b\n" + "|--" * 30000 + "x", "backticks": "x " + "`" * 100000, "uneven backticks": "x " + "`a``b```c" * 12000,
    "brackets": "[" * 100000, "images": "![" * 50000, "links": "[x](" * 25000, "titles": '[x](u "' * 15000, "spaces after a target": "[x](u" + " " * 100000,
    "autolinks": "<http://" * 12000, "bold": "**a " * 25000, "underlines": "__a " * 25000, "italics": " *a" * 30000, "underscore": " _a" * 30000,
    "strikes": "~~a " * 25000, "angle": "<" * 100000, "angles in a heading": "# " + "<" * 100000, "anchors": "[x](#" + "<a" * 30000,
    "quotes": ">" * 20000 + " x", "list": "\n".join(" " * level + "- x" for level in range(1500)), "list one line": "- " * 50000 + "x",
    "quoted lists": "> " * 3000 + "- x",
}


class HostileTests(unittest.TestCase):
    def test_no_hostile_text_crashes_or_stalls_a_renderer(self) -> None:
        for name, text in HOSTILE.items():
            for label, call in (("markdown", lambda t: dh.render_markdown(t, "docs/plans/p.md")), ("inline", dh.inline_html),
                                ("cut", lambda t: dh.inline_html(t, 160))):
                with self.subTest(case=name, call=label):
                    started = time.monotonic()
                    call(text)
                    self.assertLess(time.monotonic() - started, BUDGET)

    def test_the_masks_of_the_anonymous_view_are_linear(self) -> None:
        clean = dh.public_text_filter([])
        for text in ("@ " + "a" * 200000, "a" * 200000 + "@", "a@" * 50000, "*" * 100000 + "@x", "[" * 100000 + "@"):
            with self.subTest(text=text[:20]):
                started = time.monotonic()
                clean(text)
                self.assertLess(time.monotonic() - started, BUDGET)

    def test_deep_nesting_leaves_the_status_the_document_and_the_consents_working(self) -> None:
        deep = ">" * 1500 + " x"
        root = project(self, deep)
        board = dh.Board(root)
        self.assertIn("x", board.doc("docs/plans/p.md")["html"])
        status = board.status(None)
        self.assertTrue(status["approvals"]["actions"], "the page still offers its consents")


class SplitAddressTests(unittest.TestCase):
    SOURCES = ("jan@example.**com**", "jan@example.`com`", "[jan](#)@example.com", "jan\\@example.com", "jan@example.c­om",
               "jan@**example**.com", "**jan**@example.com", "jan@example.com", "adres [jan@example](https://a.example/).com")

    def test_no_field_shows_the_address_to_a_request_without_a_session(self) -> None:
        for source in self.SOURCES:
            anonymous = source_paths(source)["anonymous"]
            with self.subTest(source=source):
                for value in strings(anonymous):
                    visible = "".join(Parsed(value).text)
                    for form in (value, visible):
                        plain = form.replace("**", "").replace("`", "").replace("\\", "").replace("­", "")
                        self.assertNotIn("jan@example", plain, value)

    def test_a_heading_does_not_keep_the_address_in_its_id(self) -> None:
        shown = source_paths("### Kontakt jan.kowalski@example.com")["anonymous"]
        self.assertNotIn("kowalski", " ".join(strings(shown)))

    def test_control_a_signed_in_account_sees_the_address(self) -> None:
        self.assertIn("jan@example.com", " ".join(strings(source_paths("jan@example.com")["signed"])))


class ReviewRound2Tests(unittest.TestCase):
    def test_a_word_that_looks_like_a_secret_degrades_only_its_block(self) -> None:
        source = "# T\n\n`password`: opis [a](https://a.example/)\n\n## Dalej\n\n- **b**\n\n```\nkod  w   bloku\n```\n"
        shown = redact({"html": dh.render_markdown(source, "docs/plans/p.md")})["html"]
        for kept in ("<h1", "<h2", "<ul><li><strong>b</strong>", "<pre><code>kod  w   bloku</code></pre>"):
            self.assertIn(kept, shown)
        self.assertNotIn("opis", shown, "the block itself is masked")

    def test_addresses_the_review_found(self) -> None:
        clean = dh.public_text_filter([])
        for source in ("jan@example._com_", '[jan](# "t")@example.com', 'jan@example.[com](https://a.example/ "t")', "jan\\.kowalski@example.com",
                       "[jan.kowalsk](#)i@example.com", "a@x.example.com+bob@y.example.org", "@".join(("jan", "a.example.com-ewa", "b.example.com")),
                       "jan@example.c\u034fom", "jan@example.com\ufe0f"):
            with self.subTest(source=source):
                shown = clean(source)
                for part in ("jan", "kowalsk", "bob@", "ewa@", "@"):
                    self.assertNotIn(part, shown, shown)

    def test_the_mask_leaves_the_rest_of_the_text_as_it_was(self) -> None:
        clean = dh.public_text_filter([])
        self.assertEqual(clean("jan@example.**com**; src/**/*.py, __init__.py"), "uczestnik**; src/**/*.py, __init__.py")
        self.assertEqual(clean("nic tu nie ma: src/**/*.py @functools"), "nic tu nie ma: src/**/*.py @functools")

    def test_an_address_in_a_link_target_is_masked_in_the_plain_text(self) -> None:
        clean = dh.public_text_filter([])
        for source in ("[kontakt](mailto:jan.kowalski@example.com)", "[jan.kowalski@example.com](mailto:jan.kowalski@example.com)",
                       "x](\njan.kowalski@example.com\n)", "`[a](mailto:jan.kowalski@example.com)`", "x@a.example.com[bob@b.example.org]",
                       "jan@example.c\u180fom", "**jan.kowalski@example.com**"):
            with self.subTest(source=source):
                shown = clean(source)
                for part in ("kowalski", "@"):
                    self.assertNotIn(part, shown, shown)
        self.assertEqual(clean("[kontakt](mailto:jan.kowalski@example.com)"), "[kontakt](mailto:uczestnik)")
        self.assertEqual(clean("matrix[i]@weights.data"), "matrix[i]@weights.data", "control: brackets that make no link join nothing")

    def test_a_field_with_something_to_hide_in_its_source_goes_to_the_plain_text(self) -> None:
        clean = dh.public_text_filter([])
        for source in ("### [Jan](mailto:jan.kowalski@example.com)", '## [Jan](# "jan.kowalski@example.com")', "**b** jan.kowalski@example.com",
                       "[a](#jan.kowalski@example.com)", "[log](/srv/ci/x/log.txt) **b**"):
            with self.subTest(source=source):
                result = dh.public_tree({"result": dh.result_html({"text": source, "path": "docs/plans/p.md"})}, clean)["result"]
                self.assertEqual(set(result), {"text", "path"})
                for part in ("kowalski", "/srv/ci"):
                    self.assertNotIn(part, " ".join(strings(result)))
        kept = {"text": "**b** [a](https://a.example/)", "textHtml": "<strong>b</strong>", "title": "t", "titleHtml": "<em>t</em>"}
        self.assertEqual(dh.public_tree(kept, clean), kept, "control: nothing to hide, nothing changes")
        self.assertEqual(dh.public_tree({"html": "<p>x</p>", "whatHtml": "<p>x</p>"}, clean), {}, "no source beside it: not shown")

    def test_an_id_of_a_heading_comes_from_the_words_it_shows(self) -> None:
        self.assertIn('<h2 id="md-opis">', dh.render_markdown("## [Opis](docs/plans/inny.md)"))
        self.assertIn('<h2 id="md-krok-1-a-b">', dh.render_markdown("## Krok `1` A & B"))

    def test_a_degraded_heading_loses_the_id_that_repeats_the_secret(self) -> None:
        for source in ("# to**ken**=" + SECRET, "### **Token**: " + SECRET):
            with self.subTest(source=source):
                shown = redact({"html": dh.render_markdown(redact(source), "docs/plans/p.md")})["html"]
                self.assertNotIn(SECRET.lower(), shown.lower())
        self.assertIn('<h2 id="md-opis">', redact({"html": dh.render_markdown("## Opis\n\ntok**en**=" + SECRET)})["html"], "control")

    def test_a_path_in_angle_brackets_and_an_address_split_in_a_target(self) -> None:
        clean = dh.public_text_filter([])
        self.assertEqual(clean("[x](</srv/ci/agent/x.txt>)"), "[x](<\u2026/x.txt>)")
        self.assertEqual(clean("[x](<//fileserver/share/x>)"), "[x](<\u2026/x>)")
        self.assertEqual(clean("<p>a</p> </ul> <br/>"), "<p>a</p> </ul> <br/>", "control: a closing tag is no path")
        shown = clean("[k](mailto:jan.kowalski@example.c\u200bom)")
        self.assertNotIn("kowalski", shown)
        self.assertNotIn("@", shown)

    def test_a_code_span_in_a_link_label_stays(self) -> None:
        self.assertIn("><code>docs/cli.md</code></span>", dh.render_inline("[`docs/cli.md`](cli.md)", "docs"))
        self.assertIn("<strong>a <code>b</code></strong></a>", dh.render_inline("[**a `b`**](https://a.example/)"))

    def test_a_cut_drops_addresses_written_together(self) -> None:
        text = "jan.kowalski@example.com+bob@example.org"
        for limit in range(1, len(text)):
            with self.subTest(limit=limit):
                self.assertNotIn("@", cut_whole(text, limit) if limit < len("jan.kowalski@example.com") else "")
        self.assertEqual(cut_whole(text, 33), "")
        self.assertEqual(cut_whole("do " + text, 36), "do")

    def test_more_hostile_text_stays_fast(self) -> None:
        spaces = " " * 60000
        clean = dh.public_text_filter([])
        for name, call in (
                ("header", lambda: engine.parse_header("a: x" + spaces + "y")), ("header render", lambda: dh.render_markdown("a: x" + spaces + "y\nb: c")),
                ("question", lambda: engine.parse_questions("## Pytania otwarte\n\n- Q1: x" + spaces + "y\n")),
                ("who", lambda: engine.WHO_RE.search("x (kto: a" + spaces + "b) y")), ("who spaces", lambda: engine.WHO_RE.search("x" + spaces + "y")),
                ("title", lambda: status._TITLE.search("# a" + spaces + "b")), ("heading", lambda: status._PRE_HEADING.search("## a" + spaces + "b")),
                ("who name", lambda: engine.split_who("x (kto:" + spaces + "(")),
                ("date", lambda: engine.READINESS_DATE_RE.search("Data: x" + spaces + "y")),
                ("stage digits", lambda: engine._stage_token("1" * 60000 + "x")), ("stage spaces", lambda: engine._stage_token("a" + spaces + "b")),
                ("accepted", lambda: engine.parse_questions("## Pytania otwarte\n\n- Q1 [ZAŁOŻENIE]: a" + spaces + "b\n")),
                ("cut", lambda: cut_whole("a" * 60000 + " @", 200)), ("cut many", lambda: cut_whole("a@" * 60000, 200)),
                ("mask", lambda: clean("a@" * 60000)), ("mask letters", lambda: clean("a@" + "b" * 200000)),
                ("list lines", lambda: dh.render_markdown("- a\n" + "  b\n" * 60000))):
            with self.subTest(case=name):
                started = time.monotonic()
                call()
                self.assertLess(time.monotonic() - started, BUDGET)


class SamePatternTests(unittest.TestCase):
    """The patterns made linear give the same groups as the ones they replace, on random lines."""

    def test_the_linear_patterns_match_as_before(self) -> None:
        def shown(name, found) -> tuple:   # `split_who` reads the words of the name: the spaces around it are not part of the result
            return (found.group("who").split(),) if name == "WHO_RE" else found.groups()

        old = {
            "HEADER_RE": (re.compile(r"^(?P<key>[^\s:#|>*`-][^:|]{0,40}):\s+(?P<val>\S.*?)\s*$"), engine.HEADER_RE, "match"),
            "QUESTION_RE": (re.compile(r"^\s*[-*]\s+(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?(?:\s*\[(?P<tag>[^\]]*)\])?\s*:\s*(?P<body>\S.*?)\s*$"),
                            engine.QUESTION_RE, "match"),
            "WHO_RE": (re.compile(r"\s*\(\s*(?:kto|who)\s*:\s*(?P<who>[^()]*?)\s*\)\s*\.?\s*$", re.IGNORECASE), engine.WHO_RE, "search"),
            "ACCEPTED_RE": (re.compile(r"\s+(?:—|–|--|-)\s+(?:przyj[eę]te|assumed|accepted)\s*:\s*", re.IGNORECASE), engine.ACCEPTED_RE, "search"),
            "READINESS_DATE_RE": (re.compile(r"^(?:Data|Date):\s*(?P<val>\S.*?)\s*$", re.MULTILINE), engine.READINESS_DATE_RE, "search"),
            "_TITLE": (re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE), status._TITLE, "search"),
            "_PRE_HEADING": (re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.*?)[ \t]*$", re.MULTILINE), status._PRE_HEADING, "search"),
        }
        atoms = {"HEADER_RE": ["Status", "a", ":", " ", "  ", "\t", "x", "|", "-", "#", "`", "\r"],
                 "QUESTION_RE": ["- Q1", "- ", "* ", "Q1", "Q22", "**", "[", "]", ":", " ", "  ", "x", "tag", "\t"],
                 "WHO_RE": ["(", ")", "kto", "who", ":", " ", "  ", ".", "x", "KTO"],
                 "ACCEPTED_RE": [" - przyjęte:", " — assumed: ", " -- ", "accepted", "przyjete", ":", "-", "–", " ", "  ", "\t", "x"],
                 "READINESS_DATE_RE": ["Data:", "Date:", " ", "  ", "x", "\n", "\t", "\r"],
                 "_TITLE": ["#", " ", "  ", "\n", "x", "\t", "\r"], "_PRE_HEADING": ["#", "##", " ", "  ", "\n", "x", "\t", "\r"]}
        rng = random.Random(7)
        for name, (before, after, method) in old.items():
            compared = 0
            for _ in range(60000):
                text = "".join(rng.choice(atoms[name]) for _ in range(rng.randint(1, 12)))
                first, second = getattr(before, method)(text), getattr(after, method)(text)
                self.assertEqual(first and (shown(name, first), first.start()), second and (shown(name, second), second.start()), (name, text))
                if name == "ACCEPTED_RE":   # the way it is used
                    self.assertEqual(before.split(text, 1), after.split(text, 1), text)
                compared += first is not None
            self.assertGreater(compared, 50, f"{name}: the corpus reaches the matches")

    def test_the_stage_token_reads_as_before(self) -> None:
        rng, changed = random.Random(11), 0
        for _ in range(60000):
            text = "".join(rng.choice(["Review", " ", "  ", "×", "1", "23", "/", "-", "x", "\t", "×2", "3/4"]) for _ in range(rng.randint(0, 10)))
            before = re.sub(r"[\s\-]+", "_", re.sub(r"\s*(×\d+|\d+/\d+)\s*$", "", text.strip().lower())).strip("_")
            self.assertEqual(engine._stage_token(text), before, text)
            changed += before != re.sub(r"[\s\-]+", "_", text.strip().lower()).strip("_")
        self.assertGreater(changed, 50, "the corpus reaches the counters")



if __name__ == "__main__":
    unittest.main()
