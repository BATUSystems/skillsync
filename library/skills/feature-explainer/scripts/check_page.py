#!/usr/bin/env python3
"""Check an explainer page against the acceptance criteria of the feature-explainer skill.

Usage: check_page.py [--json] <page.html>
Exit codes: 0 = PASS, 1 = FAIL (at least one finding), 2 = usage error (missing/unreadable file).
Stdlib only (Python 3.11+).
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

MIN_SECTIONS = 9
URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?['\"]?\s*(https?:)?(//)?", re.I)
EXTERNAL = re.compile(r"^(https?:)?//", re.I)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []          # (kind, tag attrs dict)
        self.refs = []            # URLs referenced by images / url() as (url, is_image)
        self.script_srcs = []
        self.stylesheets = []
        self.hrefs = []
        self.ids = set()
        self.has_viewport = False
        self.css = []             # all CSS text (style elements and style attributes)
        self.sections = 0         # parts: <section> with an id or <h2>, plus <header>/<footer> with an id
        self._in_style = False
        self._section_stack = []  # [has_id, has_h2] per open <section>

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("style"):
            self.css.append(a["style"])
        if tag == "style":
            self._in_style = True
        elif tag == "meta" and a.get("name", "").lower() == "viewport":
            self.has_viewport = True
        elif tag == "script" and "src" in a:
            self.script_srcs.append(a["src"])
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if any(r in rel for r in ("stylesheet", "preload", "preconnect")) and EXTERNAL.match(a.get("href", "")):
                self.stylesheets.append(a["href"])
        elif tag == "a" and a.get("href", "").startswith("#"):
            self.hrefs.append(a["href"][1:])
        elif tag in ("img", "source"):
            if tag == "img":
                self.images.append(a)
            for u in [a.get("src", "")] + [p.split()[0] for p in a.get("srcset", "").split(",") if p.strip()]:
                if u:
                    self.refs.append(u)
        elif tag in ("header", "footer") and a.get("id"):
            self.sections += 1    # the title block and the sources footer are two of the nine parts
        elif tag == "section":
            self._section_stack.append([bool(a.get("id")), False])
        elif tag == "h2" and self._section_stack:
            self._section_stack[-1][1] = True

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        elif tag == "section" and self._section_stack:
            has_id, has_h2 = self._section_stack.pop()
            self.sections += 1 if (has_id or has_h2) else 0

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


def check(html, base):
    p = PageParser()
    p.feed(html)
    out = []
    css = "\n".join(p.css)
    css_urls = [m.group(2) for m in URL_RE.finditer(css)]
    for u in p.refs + css_urls:
        u = u.strip()
        if u.lower().startswith("data:"):
            out.append(("data-uri", "data: URI in image source or url(): %s..." % u[:30]))
        elif EXTERNAL.match(u):
            out.append(("external-resource", "external resource: %s" % u))
    for u in p.refs:
        u = u.strip()
        if not u or u.lower().startswith("data:") or EXTERNAL.match(u):
            continue
        path = unquote(re.split(r"[?#]", u)[0])
        if not (base / path).is_file():
            out.append(("missing-image", "image file not found next to the page: %s" % u))
    for a in p.images:
        if "alt" not in a:
            out.append(("missing-alt", "img without alt: %s" % a.get("src", "")[:60]))
        elif a["alt"].strip() == "" and a.get("role") != "presentation" and "aria-hidden" not in a:
            out.append(("missing-alt", "empty alt without role=presentation or aria-hidden: %s" % a.get("src", "")[:60]))
    for s in p.script_srcs:
        out.append(("external-script", "script with src: %s" % s))
    for s in p.stylesheets:
        out.append(("external-script", "external stylesheet/font: %s" % s))
    for m in IMPORT_RE.finditer(css):
        if m.group(1) or m.group(2):
            out.append(("external-script", "external @import in CSS"))
    if not p.has_viewport:
        out.append(("no-viewport", "missing <meta name=viewport>"))
    for h in sorted(set(p.hrefs)):
        if h and h not in p.ids:
            out.append(("toc-anchor", "href=#%s has no element with that id" % h))
    dark = re.search(r"prefers-color-scheme\s*:\s*dark|\[data-theme=['\"]?dark['\"]?\]", css)
    light = re.search(r":root\s*\{[^}]*--", css)
    if not (dark and light):
        out.append(("themes", "need light tokens in :root and a dark theme (prefers-color-scheme: dark or [data-theme=dark])"))
    # A "section" is a <section> element with an id or an <h2>, or the page <header>/<footer> that has an id (nine parts in all).
    if p.sections < MIN_SECTIONS:
        out.append(("sections", "found %d sections, expected at least %d" % (p.sections, MIN_SECTIONS)))
    return out


def main(argv):
    args = [a for a in argv if a != "--json"]
    as_json = len(args) != len(argv)
    if len(args) != 1:
        print("usage: check_page.py [--json] <page.html>", file=sys.stderr)
        return 2
    path = Path(args[0])
    try:
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print("error: cannot read %s: %s" % (path, e), file=sys.stderr)
        return 2
    findings = check(html, path.resolve().parent)
    result = "FAIL" if findings else "PASS"
    if as_json:
        print(json.dumps({"result": result, "findings": [{"code": c, "message": m} for c, m in findings]},
                         ensure_ascii=False, indent=2))
    else:
        for c, m in findings:
            print("FAIL %s: %s" % (c, m))
        if not findings:
            print("PASS all: no defects found")
        print("RESULT: %s" % result)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
