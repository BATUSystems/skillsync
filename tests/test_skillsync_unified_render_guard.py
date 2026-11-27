"""CORE-2000-UNIFIED K21, the two nets under the renderer: the guard of the HTML that leaves the server (`safe_html`, `guard_html`) and a
differential fuzz with a fixed seed over the renderers, the masks and the view without a session:
python3 -m unittest tests.test_skillsync_unified_render_guard
"""
from __future__ import annotations

import random
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow.common import redact, visible_html_text  # noqa: E402
from test_skillsync_unified_md_render import corpus  # noqa: E402

# What the renderers wrote before the fix (taken from release 1.20.1): the mask of secrets over finished HTML, and a link inside an autolink.
BROKEN = (
    '<p><span class="md-link-off" title="docs/plans/token=[REDACTED] B"style=position:fixed;inset:0;z-index:99999;font-size:90vw;opacity:0 <strong>z</strong> <a href="https://evil.example/" target="_blank" rel="noopener noreferrer">WWWW</a></p>',
    '<p><a href="http://e.example/<a href="https://b.example/style=position:fixed;inset:0;z-index:99999;" target="_blank" rel="noopener noreferrer">x</a>" target="_blank" rel="noopener noreferrer">http://e.example/<a href="https://b.example/style=position:fixed;inset:0;z-index:99999;" target="_blank" rel="noopener noreferrer">x</a></a></p>',
    '<div class="md-table"><table><thead><tr><th><a href="http://e.example/<a href="https://b.example/style=position:fixed;inset:0;z-index:99999;" target="_blank" rel="noopener noreferrer">x</a>" target="_blank" rel="noopener noreferrer">http://e.example/<a href="https://b.example/style=position:fixed;inset:0;z-index:99999;" target="_blank" rel="noopener noreferrer">x</a></a></th></tr></thead><tbody><tr><td>1</td></tr></tbody></table></div>',
)
FOREIGN = ('<img src=x onerror=alert(1)>', '<script>x</script>', '<p style="a">x</p>', '<a href="javascript:alert(1)">x</a>', '<!-- x -->',
           '<a href="https://a.example/" onclick="x">x</a>', '<p>a < b</p>', '<p>x</p><', '<p>x</p><a ', '<a data-doc="/etc/passwd">x</a>',
           '<span title="a&lt;b">x</span>', '<span title="a&quot;b">x</span>', '<span class=\xa0" onclick=x y=">a</span>', '<a class=\xa0" href=javascript:alert(1) y=">a</a>', '<?php ?>', '<!DOCTYPE x>', '<p>x\x00</p>', '<iframe></iframe>')


class GuardTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(dh, "log")   # every refusal is logged; here that is the expected outcome
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_guard_refuses_the_broken_html_of_the_released_renderer(self) -> None:
        for rendered in (*BROKEN, *FOREIGN):
            with self.subTest(rendered=rendered):
                self.assertFalse(dh.safe_html(rendered))

    def test_a_refused_field_is_never_repaired(self) -> None:
        for rendered in (*BROKEN, *FOREIGN):
            with self.subTest(rendered=rendered):
                shown = dh.guard_html({"rows": [{"text": "t", "textHtml": rendered, "title": "u", "titleHtml": "<em>u</em>"}],
                                       "result": {"text": "t", "path": "p", "html": rendered, "moreHtml": "<p>x</p>", "moreLines": 1}})
                self.assertEqual(shown["rows"], [{"text": "t", "title": "u", "titleHtml": "<em>u</em>"}])
                self.assertEqual(shown["result"], {"text": "t", "path": "p"})
                document = dh.guard_html({"path": "docs/plans/p.md", "html": rendered})["html"]
                self.assertTrue(dh.safe_html(document), document)
                self.assertRegex(document, r"^<pre>[^<]*</pre>$")

    def test_control_every_document_of_the_repository_passes(self) -> None:
        paths = sorted((ROOT / "docs").rglob("*.md"))
        self.assertGreater(len(paths), 50)
        refused = [str(path) for path in paths
                   if not dh.safe_html(redact({"html": dh.render_markdown(path.read_text(encoding="utf-8"), "docs/x.md")})["html"])]
        self.assertEqual(refused, [])


SEED = 20261127
LOCAL = "jan.kowalski"
ADDRESS = "@".join((LOCAL, "example.com"))
SECRET = "".join(("Kanarek", "Sekretny", "Napis", "73"))
SPECIAL = ["[", "]", "(", ")", "<", ">", "`", "```", "~~~", "*", "**", "_", "__", "~~", "|", "|---|", "---", "#", "## ", "- ", "- [ ] ",
           "1. ", "> ", " ", "  ", "\n", "\n\n", "\t", "\\", '"', "'", "=", "&", "&lt;", "&#64;", "!", ":", "/", ".", "@", "\x00", "\u200b",
           "\u034f", "http://a.example/", "https://", "mailto:", "javascript:", "data:", "<!--", "-->", "style=", "onerror=", "docs/plans/x.md",
           "#anchor", "word", "zażółć", "token=", "password: "]
# The path stands between spaces: how the path filter reads one glued to punctuation is the rule of U-4.3, tested with it.
SENSITIVE = [ADDRESS, "token=" + SECRET, " /srv/ci/agent/x.txt "]


def cases(count: int) -> list:
    rng = random.Random(SEED)
    known = corpus()
    made = []
    for number in range(count):
        parts = [rng.choice(SPECIAL) for _ in range(rng.randint(1, 24))]
        for _ in range(rng.randint(0, 2)):
            parts.insert(rng.randint(0, len(parts)), rng.choice(SENSITIVE))
        if number % 4 == 0:
            parts.insert(rng.randint(0, len(parts)), rng.choice(known))
        made.append("".join(parts))
    return made


def answer(source: str) -> dict:
    """The road of a text of the project to the answer of `/api/status`: masked when read, rendered, masked as a whole."""
    text = redact(source)
    line = " ".join(text.split())
    data = {"progress": {"phases": [{"steps": [{"title": line}]}]}, "run": {"checkpoints": [{"text": line}]},
            "waiting": [{"what": line, "action": line}], "result": dh.result_html({"text": text, "path": "docs/plans/p.md"})}
    return redact(dh.add_inline_html(data))


def strings(value) -> list:
    if isinstance(value, dict):
        return [item for one in value.values() for item in strings(one)]
    if isinstance(value, list):
        return [item for one in value for item in strings(one)]
    return [value] if isinstance(value, str) else []


def html_fields(value) -> list:
    if isinstance(value, list):
        return [item for one in value for item in html_fields(one)]
    if not isinstance(value, dict):
        return []
    return [item for key, one in value.items()
            for item in ([one] if isinstance(one, str) and (key in ("html", "moreHtml") or key.endswith("Html")) else html_fields(one))]


class FuzzTests(unittest.TestCase):
    def test_the_renderers_stay_inside_the_allowlist_and_nothing_sensitive_leaves_without_a_session(self) -> None:
        clean = dh.public_text_filter([])
        for source in cases(4000):
            with self.subTest(source=source):
                raw = [dh.render_markdown(source, "docs/plans/p.md"), dh.render_inline(source, "docs/plans"), dh.inline_html(source, 40) or ""]
                signed = answer(source)
                for rendered in (*raw, *html_fields(signed)):   # the renderers and the mask by themselves, before the guard
                    self.assertTrue(dh.safe_html(rendered), rendered)
                anonymous = dh.public_tree(signed, clean)
                for value in strings(anonymous):
                    for form in (value, visible_html_text(value)):
                        self.assertNotIn(ADDRESS, form)
                        self.assertNotIn(LOCAL + "@", form)
                        self.assertNotIn(SECRET.lower(), form.lower())
                        self.assertNotIn("/srv/ci", form)

    def test_the_time_grows_in_line_with_the_text(self) -> None:
        rng = random.Random(SEED)
        clean = dh.public_text_filter([])
        units = ["".join(rng.choice([*SPECIAL, *SENSITIVE[:1]]) for _ in range(rng.randint(1, 4))) for _ in range(60)]
        units += [atom for atom in SPECIAL]
        for unit in units:
            with self.subTest(unit=unit):
                spent = []
                for size in (40000, 80000):
                    source = (unit * (size // max(len(unit), 1) + 1))[:size]
                    started = time.monotonic()
                    dh.public_tree(answer(source), clean)
                    spent.append(time.monotonic() - started)
                self.assertLess(spent[0], 4.0, spent)
                self.assertLess(spent[1], 3.0 * spent[0] + 1.0, spent)


if __name__ == "__main__":
    unittest.main()
