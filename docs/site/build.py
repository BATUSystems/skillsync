#!/usr/bin/env python3
"""Buduje wersję HTML dokumentów workflow (docs/workflow.md, docs/workflow-details.md).

Użycie:  python3 docs/site/build.py [--out KATALOG]
Wynik:   docs/site/out/workflow.html, docs/site/out/workflow-details.html (katalog poza gitem).

Zależność tylko do budowania dokumentacji: python-markdown (`pip install markdown`).
Generator skillsync jej nie potrzebuje.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent
DOCS = SITE.parent
SRC = SITE / "src"
DEFAULT_OUT = SITE / "out"

MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"
FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800"
    "&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap"
)

PAGES = [
    {
        "src": "workflow.md",
        "out": "workflow.html",
        "nav": "Mapa",
        "title": "Agent flow: mapa",
        "kicker": "skillsync · agent flow",
        "lede": "Skille, role i komendy, które prowadzą zmianę od speca do zweryfikowanej realizacji.",
    },
    {
        "src": "workflow-details.md",
        "out": "workflow-details.html",
        "nav": "Szczegóły",
        "title": "Agent flow: szczegóły",
        "kicker": "skillsync · agent flow",
        "lede": "Pełny cykl orkiestratora i podagentów: akty, zgody, pętle przeglądu, dowody i wiele sesji.",
    },
]

# Strażnik treści. W repo są tylko wzorce ogólne (lokalne ścieżki użytkownika).
# Nazwy klientów, persony i inne zakazane słowa trzyma lista lokalna poza repo:
# zmienna SKILLSYNC_DOCS_DENYLIST albo ~/.config/skillsync/docs-denylist.txt (jedno wyrażenie regularne na linię).
BUILTIN_DENY = [
    r"/home/[A-Za-z0-9_.-]+/",
    r"/Users/[A-Za-z0-9_.-]+/",
    r"[A-Za-z]:\\Users\\",
    r"~/\.(?:local|claude|codex|cursor)\b",
    r"~/\.config/(?!skillsync/)",
]

STATUS_CLASSES = {
    "PASS": "pass",
    "READY": "pass",
    "ZATWIERDZIĆ": "pass",
    "GOTOWY": "pass",
    "POTWIERDZONE": "pass",
    "FAIL": "fail",
    "OBALONE": "fail",
    "NIE ZATWIERDZAĆ": "fail",
    "BLOKUJE": "fail",
    "BLOCKED": "warn",
    "CHANGES_REQUIRED": "warn",
    "NEEDS_CHANGES": "warn",
    "LUKI": "warn",
    "NIEROZSTRZYGNIĘTE": "muted",
    "NOT_RUN": "muted",
    "BLOCKER": "fail",
    "MAJOR": "warn",
    "MINOR": "info",
    "NIT": "muted",
}


def fail(message: str, code: int = 1) -> None:
    print(f"build.py: {message}", file=sys.stderr)
    sys.exit(code)


def load_markdown():
    try:
        import markdown  # type: ignore
    except ImportError:
        fail(
            "brak pakietu python-markdown. Zainstaluj go poleceniem `python3 -m pip install \"markdown>=3.5\"` "
            "(zależność tylko do budowania dokumentacji, nie do działania skillsync).",
            2,
        )
    try:
        from markdown.extensions.toc import slugify_unicode  # type: ignore
    except ImportError:
        fail(
            f"python-markdown {getattr(markdown, '__version__', '?')} jest za stary (brak slugify_unicode). "
            "Zaktualizuj: `python3 -m pip install -U \"markdown>=3.5\"`.",
            2,
        )
    return markdown, slugify_unicode


def deny_patterns() -> tuple[list[str], str]:
    patterns = list(BUILTIN_DENY)
    env = os.environ.get("SKILLSYNC_DOCS_DENYLIST")
    path = Path(env).expanduser() if env else Path.home() / ".config" / "skillsync" / "docs-denylist.txt"
    if not path.is_file():
        return patterns, f"lista lokalna {path} nie istnieje: sprawdzono tylko ścieżki lokalne"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            try:
                re.compile(line)
            except re.error as exc:
                fail(f"błędny wzorzec w {path}: {line!r} ({exc})")
            patterns.append(line)
    return patterns, f"lista lokalna: {len(patterns) - len(BUILTIN_DENY)} wzorców"


def site_sources() -> list[Path]:
    """Pliki z docs/site/ idące do repo: też podlegają strażnikowi."""
    return [path for path in sorted(SITE.rglob("*")) if path.is_file() and DEFAULT_OUT not in path.parents
            and path.suffix in {".py", ".md", ".css", ".js"}]


def guard(name: str, text: str, patterns: list[str]) -> list[str]:
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                hits.append(f"{name}:{number}: zakazany wzorzec #{patterns.index(pattern) + 1}")
    return hits


def extract_mermaid(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def keep(match: re.Match) -> str:
        blocks.append(match.group(1).rstrip())
        return f"\n\nMERMAIDBLOCK{len(blocks) - 1}\n\n"

    return re.sub(r"^```mermaid\n(.*?)^```\s*$", keep, text, flags=re.S | re.M), blocks


def rewrite_links(body: str, page_names: dict[str, str]) -> str:
    def fix(match: re.Match) -> str:
        target = match.group(1)
        if re.match(r"^(?:[a-z]+:|#|//)", target):
            return match.group(0)
        path, _, anchor = target.partition("#")
        if path in page_names:
            new = page_names[path] + (f"#{anchor}" if anchor else "")
        else:
            new = "../../" + target
        return f'href="{new}"'

    return re.sub(r'href="([^"]+)"', fix, body)


def unescape_pipes(body: str) -> str:
    """`a\\|b` w komórce tabeli: GitHub pokazuje `a|b`, python-markdown zostawia ukośnik."""
    return re.sub(r"<code>(.*?)</code>", lambda m: "<code>" + m.group(1).replace("\\|", "|") + "</code>", body, flags=re.S)


def decorate(body: str) -> str:
    def status(match: re.Match) -> str:
        word = match.group(1)
        return f'<code class="st st-{STATUS_CLASSES[word]}">{word}</code>'

    words = "|".join(sorted((re.escape(w) for w in STATUS_CLASSES), key=len, reverse=True))
    body = re.sub(rf"<code>({words})</code>", status, body)
    body = re.sub(r"„(GO plan|GO implement|GO)”", r'<span class="gate">\1</span>', body)
    body = re.sub(r"<table>", '<div class="table-wrap"><table>', body)
    body = re.sub(r"</table>", "</table></div>", body)
    return body


def toc_html(tokens: list[dict]) -> str:
    items = []
    for token in tokens:
        if token["level"] != 2:
            continue
        children = "".join(
            f'<li><a href="#{child["id"]}">{html.escape(child["name"], quote=False)}</a></li>' for child in token["children"] if child["level"] == 3
        )
        sub = f'<ol class="toc-sub">{children}</ol>' if children else ""
        items.append(f'<li><a href="#{token["id"]}">{html.escape(token["name"], quote=False)}</a>{sub}</li>')
    return f'<ol class="toc-list">{"".join(items)}</ol>'


def hero_acts() -> str:
    return """
<div class="acts" aria-label="Trzy akty feature">
  <div class="act act-1"><span class="act-no">I</span><span class="act-name">Spec</span><span class="act-loop">pętla spec</span></div>
  <span class="gate gate-lg">GO plan</span>
  <div class="act act-2"><span class="act-no">II</span><span class="act-name">Plan</span><span class="act-loop">pętla plan</span></div>
  <span class="gate gate-lg">GO implement</span>
  <div class="act act-3"><span class="act-no">III</span><span class="act-name">Realizacja</span><span class="act-loop">pętla kod</span></div>
  <span class="gate gate-lg gate-done">done</span>
</div>
<ul class="facts">
  <li><b>3</b> akty</li><li><b>2</b> zgody</li><li><b>8</b> ról</li><li><b>4</b> rundy do STOP</li><li><b>1</b> źródło protokołu wielu sesji</li>
</ul>"""


def page_html(page: dict, body: str, toc: str, style: str, script: str, stamp: str) -> str:
    current = ' class="tab is-current" aria-current="page"'
    other = ' class="tab"'
    tabs = "".join(f'<a{current if p is page else other} href="{p["out"]}">{p["nav"]}</a>' for p in PAGES)
    return f"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page["title"])}</title>
<meta name="description" content="{html.escape(page["lede"])}">
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS_URL}">
<style>
{style}
</style>
</head>
<body>
<a class="skip" href="#content">Przejdź do treści</a>
<header class="topbar" id="top">
  <a class="brand" href="{PAGES[0]["out"]}"><span class="brand-mark" aria-hidden="true">◆</span> skillsync <span class="brand-sub">agent flow</span></a>
  <nav class="tabs" aria-label="Dokumenty">{tabs}</nav>
  <div class="theme" role="group" aria-label="Motyw">
    <button type="button" data-theme-set="auto">Auto</button><button type="button" data-theme-set="light">Jasny</button><button type="button" data-theme-set="dark">Ciemny</button>
  </div>
</header>
<section class="hero">
  <p class="kicker">{html.escape(page["kicker"])}</p>
  <h1>{html.escape(page["title"])}</h1>
  <p class="lede">{html.escape(page["lede"])}</p>
  {hero_acts()}
</section>
<div class="layout">
  <aside class="sidebar">
    <details class="toc" open>
      <summary>Spis treści</summary>
      <nav aria-label="Spis treści">{toc}</nav>
    </details>
  </aside>
  <main id="content" class="content">
{body}
    <footer class="foot">Wygenerowano z <code>docs/{page["src"]}</code> · {html.escape(stamp)} · <a href="#top" data-top>na górę ↑</a></footer>
  </main>
</div>
<script type="module">
const MERMAID_URL = "{MERMAID_URL}";
{script}
</script>
</body>
</html>
"""


def check_anchors(outputs: dict[str, str]) -> list[str]:
    problems = []
    ids = {name: set(re.findall(r'id="([^"]+)"', text)) for name, text in outputs.items()}
    for name, text in outputs.items():
        for target in re.findall(r'href="([^"]+)"', text):
            path, _, anchor = target.partition("#")
            if not anchor or re.match(r"^[a-z]+:", target):
                continue
            page = path or name
            if page in ids and html.unescape(anchor) not in ids[page]:
                problems.append(f"{name}: martwa kotwica {target}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Wersja HTML dokumentów workflow")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="katalog wynikowy (domyślnie docs/site/out)")
    parser.add_argument("--require-denylist", action="store_true",
                        help="przerwij, gdy brak listy lokalnej strażnika (np. przed publikacją)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    markdown, slugify_unicode = load_markdown()
    patterns, guard_note = deny_patterns()
    if len(patterns) == len(BUILTIN_DENY):
        if args.require_denylist:
            fail(f"strażnik: {guard_note}", 2)
        print(f"build.py: uwaga, {guard_note}", file=sys.stderr)
    style = (SRC / "style.css").read_text(encoding="utf-8")
    script = (SRC / "app.js").read_text(encoding="utf-8")
    page_names = {p["src"]: p["out"] for p in PAGES}

    problems: list[str] = []
    for path in site_sources():
        problems += guard(path.relative_to(DOCS).as_posix(), path.read_text(encoding="utf-8"), patterns)
    outputs: dict[str, str] = {}
    for page in PAGES:
        source = (DOCS / page["src"]).read_text(encoding="utf-8")
        problems += guard(page["src"], source, patterns)
        text, diagrams = extract_mermaid(source)
        text = re.sub(r"\A# .*\n", "", text)  # tytuł strony jest w nagłówku hero
        md = markdown.Markdown(
            extensions=["tables", "fenced_code", "sane_lists", "toc"],
            extension_configs={"toc": {"slugify": slugify_unicode, "toc_depth": "2-3"}},
        )
        body = md.convert(text)
        for index, diagram in enumerate(diagrams):
            block = f'<figure class="diagram"><pre class="mermaid">{html.escape(diagram)}</pre></figure>'
            body, count = re.subn(rf"<p>MERMAIDBLOCK{index}</p>", lambda _m, b=block: b, body)
            if count != 1:
                problems.append(f"{page['src']}: diagram {index + 1} nie trafił na swoje miejsce")
        body = decorate(rewrite_links(unescape_pipes(body), page_names))
        stamp = f"źródło {len(source.splitlines())} linii, {len(diagrams)} diagramów"
        outputs[page["out"]] = page_html(page, body, toc_html(md.toc_tokens), style, script, stamp)

    problems += check_anchors(outputs)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        fail(f"{len(problems)} problemów, nic nie zapisano")

    args.out.mkdir(parents=True, exist_ok=True)
    for name, text in outputs.items():
        (args.out / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"zapisano {args.out / name} ({len(text.encode('utf-8')) // 1024} KB)")
    print(f"strażnik: {guard_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
