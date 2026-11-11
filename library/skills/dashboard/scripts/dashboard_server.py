#!/usr/bin/env python3
"""dashboard: strona statusu przebiegu skilla `feature` na żywo (stdlib, offline).

Czyta (tylko do odczytu) plan `docs/plans/*.md`, spec, `.skillsync/runs/<run-id>/checkpoints.md`
i `.git/HEAD` projektu, a przeglądarce podaje `/api/status` (JSON), `/api/doc` (podgląd Markdown)
oraz `/events` (SSE, zdarzenie `change` po zmianie plików, `ping` co 15 s).

Jedyny zapis: `POST /api/approve` — przycisk „GO plan”/„GO implement” woła `skillsync spec|plan approve` z sha
pokazanej wersji (zgoda użytkownika jak wpisane słowo; procedura `feature` uznaje ten status). Tylko z przeglądarki
na tej maszynie (Host loopback albo `--approve-host`, nagłówek `X-Status-Board`, zgodny `Origin`); `--no-approvals` wyłącza.

Użycie:
    python3 dashboard_server.py --project <ścieżka> [--plan docs/plans/x.md] [--port 5140] [--host 127.0.0.1]
    python3 dashboard_server.py --project <ścieżka> --json      # jednorazowy zrzut /api/status
    [--session-url https://claude.ai/code/...] [--no-approvals]
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "1.0.0"
DEFAULT_PORT = 5140
PORT_MIN, PORT_MAX = 5100, 5199
FORBIDDEN_PORTS = frozenset({4444, 4445, 4446, 4447, 4448, 9222, 9333})
PROTOTYPE_PORT = 5130
DOC_DIRS = ("docs/specs", "docs/plans", "docs/design", "docs/domain")
PROTOTYPES_DIR = "docs/prototypes"
PROTO_CACHE_S = 30
RUNS_DIR = ".skillsync/runs"
MAX_DOC_BYTES = 2_000_000
MAX_SSE_CLIENTS = 64
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

STEP_ID = r"[A-Z]{1,3}-?\d+[a-z]?(?:\.\d+[a-z]?)?"  # S1, S2a, A-1 oraz podkroki faz F1.2
STEP_RE = re.compile(rf"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<id>{STEP_ID})(?![\w-])\s*(?P<rest>.*)$")
HEADER_RE = re.compile(r"^(?P<key>[^\s:#|>*`-][^:|]{0,40}):\s+(?P<val>\S.*?)\s*$")
CHECKPOINT_RE = re.compile(r"^\s*[-*]\s+(?P<time>\d{1,2}:\d{2})\b\s*(?P<text>.*)$")
IN_PROGRESS_RE = re.compile(r"w\s+toku\s*:?\s*(?P<seg>[^;]*)", re.IGNORECASE)
DONE_RE = re.compile(r"(?<![\w])zrobion[eya]\s*:?\s*(?P<seg>[^;]*)", re.IGNORECASE)
ID_LIST_RE = re.compile(
    rf"^\s*(?P<ids>{STEP_ID}(?:(?:\s*[/+&]\s*|\s+(?:i|oraz)\s+){STEP_ID})*)(?![\w-])"
)
# Znacznik „sesja czeka na człowieka” w checkpoincie: segment `czeka: <co>` (segmenty rozdziela `;`, jak
# „zrobione”/„w toku”/„ryzyko”). Najnowszy checkpoint bez `czeka:` zdejmuje baner.
WAIT_RE = re.compile(r"(?:^|;)\s*czeka\s*:\s*(?P<seg>[^;]+)", re.IGNORECASE)
WAIT_NONE = {"", "-", "—", "nic", "brak", "nie"}
STATUS_WAIT_RE = re.compile(r"czeka\s+na\s+(?P<what>[^);,]+)", re.IGNORECASE)
ID_FIND_RE = re.compile(rf"(?<![\w-]){STEP_ID}(?![\w-])")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
RUN_ID_RE = re.compile(r"^[\w.-]{1,120}$")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
APPROVE_KINDS = {"spec": "GO plan", "plan": "GO implement", "cp": "CP", "ready": "READY"}
APPROVALS_LOG = ".skillsync/status-board/approvals.jsonl"  # zgody GO poza przebiegiem (bez Przebieg:)
RUN_APPROVALS = "approvals.jsonl"  # .skillsync/runs/<run-id>/approvals.jsonl — wspólny format ze `skillsync status`
APPROVALS_READ_MAX = 256_000
MAX_BODY = 4096
SILENCE_MIN = 35  # raport postępu: po każdym kroku albo co 30 min; po 35 min ciszy przy planie in_progress — sygnał dla człowieka
NEXT_HINT = ("Zgoda zapisana w pliku. Sesja czekająca na nią rusza sama (nasłuch zmian i budzik co 30 min); "
             "jeśli nie ruszy w ciągu minuty, napisz w sesji „dalej”.")
MANIFEST = ".github/.skillsync.yaml"
DELIVERY_MODES = {"pr", "direct"}
REF_NAME_RE = re.compile(r"^(?!-)[\w.][\w./-]{0,120}$")
AHEAD_CACHE_S = 10

STATUS_RANK = {"in_progress": 0, "approved": 1, "draft": 2, "done": 3}  # done: ostatni zamknięty (moment na CP/READY)
SPEC_DONE = {"approved", "implemented", "done", "accepted"}
SPEC_OPEN = {"draft", "approved"}  # spec, który może jeszcze nie mieć planu (akt I)
PLAN_APPROVED = {"approved", "in_progress", "done", "implemented"}


# --------------------------------------------------------------------------- parsowanie

def read_text(path: Path, limit: int = MAX_DOC_BYTES) -> str:
    with open(path, "rb") as fh:
        return fh.read(limit).decode("utf-8", errors="replace")


def clean_value(val: str | None) -> str | None:
    if val is None:
        return None
    return val.strip().strip("`*_ ").strip() or None


def first_token(val: str | None) -> str | None:
    val = clean_value(val)
    if not val:
        return None
    return val.split()[0].strip("`*_,;()") or None


def normalize_status(val: str | None) -> str | None:
    val = clean_value(val)
    if not val:
        return None
    m = re.match(r"[A-Za-z_-]+", val)
    return m.group(0).lower().replace("-", "_") if m else None


def parse_header(text: str) -> tuple[str | None, dict[str, str]]:
    """Tytuł (pierwsze `# `) i pola `Klucz: wartość` przed pierwszym `## `."""
    title: str | None = None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        if title is None and line.startswith("# "):
            title = line[2:].strip()
            continue
        m = HEADER_RE.match(line)
        if m:
            key = m.group("key").strip().lower()
            fields.setdefault(key, m.group("val").strip())
    return title, fields


def parse_progress(text: str) -> list[dict]:
    """Sekcja `## Progress`: fazy `### …` i kroki `- [ ] ID Tytuł` / `- [x] ID Tytuł — sha`."""
    phases: list[dict] = []
    current: dict | None = None
    in_progress = False
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):
            if in_progress:
                break
            in_progress = line[3:].strip().lower().startswith("progress")
            continue
        if not in_progress:
            continue
        if line.startswith("### "):
            current = {"name": line[4:].strip(), "steps": []}
            phases.append(current)
            continue
        m = STEP_RE.match(line)
        if not m:
            continue
        if current is None:
            current = {"name": "Kroki", "steps": []}
            phases.append(current)
        rest = m.group("rest").strip()
        title, note = rest, ""
        for sep in (" — ", " -- ", " – "):
            if sep in rest:
                title, note = rest.split(sep, 1)
                break
        token = note.strip().split()[0].strip(",;()") if note.strip() else ""
        current["steps"].append({
            "id": m.group("id"),
            "title": title.strip(),
            "done": m.group("mark") != " ",
            "note": note.strip(),
            "sha": token if SHA_RE.match(token) else None,
        })
    return [p for p in phases if p["steps"]]


def parse_checkpoints(text: str) -> tuple[str | None, list[dict]]:
    """`RUN=<iso>` i linie `- HH:MM tekst` (linie wcięte = kontynuacja)."""
    run_start: str | None = None
    items: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("RUN="):
            run_start = stripped[4:].strip()
            continue
        m = CHECKPOINT_RE.match(line)
        if m:
            items.append({"time": m.group("time"), "text": m.group("text").strip()})
        elif items and stripped and line[:1] in (" ", "\t"):
            items[-1]["text"] += " " + stripped
    return run_start, items


def wait_marker(text: str) -> str | None:
    """Wartość segmentu `czeka:` z tekstu checkpointu albo None."""
    m = WAIT_RE.search(text)
    if not m:
        return None
    val = m.group("seg").strip().rstrip(".")
    return None if val.lower() in WAIT_NONE else val


def wait_item(what: str, source: str, since: str | None = None, ctx: dict | None = None) -> dict:
    """Pozycja banera: na co sesja czeka, co zrobić i dokąd prowadzi odnośnik (`doc` w projekcie albo `prototype`)."""
    ctx = ctx or {}
    low = what.lower()
    item: dict = {"what": what, "source": source, "since": since, "action": f"Sesja czeka: {what}. Odpowiedz w sesji.",
                  "link": None}
    if "prototyp" in low:
        m = re.search(r"prototyp\w*\s+(?P<slug>[A-Za-z0-9][\w.-]*)", what, re.IGNORECASE)
        slug = m.group("slug") if m else ctx.get("slug")
        item.update(action="Otwórz prototyp, przejrzyj ekrany i zaakceptuj je (albo zostaw uwagi).",
                    link={"kind": "prototype", "slug": slug} if slug else None)
    elif "go plan" in low:
        item.update(action="Przeczytaj spec i zatwierdź „GO plan” przyciskiem albo wpisz to w sesji.",
                    link=ctx.get("spec") and {"kind": "doc", "path": ctx["spec"]}, word="GO plan", approve="spec")
    elif "go implement" in low:
        item.update(action="Przeczytaj plan i zatwierdź „GO implement” przyciskiem albo wpisz to w sesji (przy planie direct wystarcza „GO”).",
                    link=ctx.get("plan") and {"kind": "doc", "path": ctx["plan"]}, word="GO implement", approve="plan")
    elif re.match(r"(odpowied\w*|pytani\w*)?\s*Q\d", what, re.IGNORECASE) or "pytani" in low or "odpowied" in low:
        item.update(action=f"Odpowiedz w sesji na pytania ({what}).", link=ctx.get("spec") and {"kind": "doc", "path": ctx["spec"]})
    elif re.fullmatch(r"(CP|CPD)!?", what.strip(), re.IGNORECASE):
        item.update(action="Sprawdź wynik i zatwierdź „CP” przyciskiem w Akcie III albo wpisz to w sesji.",
                    word=what.strip().upper(), approve="cp" if what.strip().upper().startswith("CP") and "D" not in what.upper() else None)
    elif re.fullmatch(r"READY!?", what.strip(), re.IGNORECASE):
        item.update(action="Sprawdź draft PR i zatwierdź „READY” przyciskiem w Akcie III albo wpisz to w sesji.",
                    word=what.strip().upper(), approve="ready")
    elif re.search(r"\bTAK\b.*\bNIE\b|\bP\d+\b", what):
        item.update(action=f"Rozstrzygnij propozycję retrospektywy: wpisz „TAK …” albo „NIE …” ({what}).")
    return item


def split_top_level(segment: str, sep: str = ",") -> list[str]:
    parts, depth, buf = [], 0, []
    for ch in segment:
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _lead_ids(segment: str) -> list[str]:
    ids: list[str] = []
    for item in split_top_level(segment):
        lead = ID_LIST_RE.match(item)
        if lead:
            for sid in ID_FIND_RE.findall(lead.group("ids")):
                if sid not in ids:
                    ids.append(sid)
    return ids


def ids_reported_done(text: str) -> list[str]:
    """Id po frazie „zrobione” (do `;`) — tylko podpowiedź wizualna, nie zmienia procentu."""
    m = DONE_RE.search(text)
    return _lead_ids(m.group("seg")) if m else []


def ids_in_progress(text: str) -> list[str] | None:
    """Id kroków po frazie „w toku” do najbliższego `;`; tylko pozycje zaczynające się od id.

    Zwraca None, gdy frazy nie ma. „szczebel 3 S1 na migawce” nie liczy się jako krok S1.
    """
    m = IN_PROGRESS_RE.search(text)
    return _lead_ids(m.group("seg")) if m else None


def parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


# --------------------------------------------------------------------------- ścieżki

def _posix_rel(root: Path, path: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def is_allowed_rel(rel: str) -> bool:
    """Whitelist ścieżki względnej (bez dostępu do dysku)."""
    if not rel or "\x00" in rel or "\\" in rel or rel.startswith("/") or not rel.endswith(".md"):
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    if re.fullmatch(r"\.skillsync/runs/[^/.][^/]*/[^/.][^/]*\.md", rel):
        return True
    if any(p.startswith(".") for p in parts):
        return False
    return any(rel.startswith(d + "/") for d in DOC_DIRS)


def resolve_doc(root: Path, rel: str | None) -> Path | None:
    """Plik *.md z whitelisty pod projektem; odporne na `..`, ścieżki absolutne i symlinki na zewnątrz."""
    if not rel or not is_allowed_rel(rel):
        return None
    root_real = root.resolve()
    try:
        target = (root_real / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    real_rel = _posix_rel(root_real, target)
    if real_rel is None or not is_allowed_rel(real_rel) or not target.is_file():
        return None
    return target


# --------------------------------------------------------------------------- Markdown

def _slug(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text).lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return "md-" + re.sub(r"[\s_]+", "-", s).strip("-")[:80]


def _render_link(label_html: str, url: str, base_dir: str) -> str:
    url = url.strip()
    low = url.lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return (f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{label_html}</a>')
    if url.startswith("#"):
        return f'<a href="#" class="md-anchor" data-anchor="{html.escape(_slug(url[1:]), quote=True)}">{label_html}</a>'
    if re.match(r"^[a-z][a-z0-9+.-]*:", low):  # javascript:, data:, file: …
        return f'<span class="md-link-off">{label_html}</span>'
    path = url.split("#", 1)[0]
    target = posixpath.normpath(posixpath.join(base_dir, path)) if path else ""
    if target and is_allowed_rel(target):
        return f'<a href="#" class="md-doc" data-doc="{html.escape(target, quote=True)}">{label_html}</a>'
    return f'<span class="md-link-off" title="{html.escape(target or url, quote=True)}">{label_html}</span>'


def render_inline(text: str, base_dir: str = "") -> str:
    store: list[str] = []

    def keep(fragment: str) -> str:
        store.append(fragment)
        return f"\x00{len(store) - 1}\x00"

    text = text.replace("\x00", "")
    text = re.sub(r"(`+)(.+?)\1", lambda m: keep("<code>" + html.escape(m.group(2).strip()) + "</code>"), text)
    text = re.sub(
        r"!\[([^\]]*)\]\([^)]*\)",
        lambda m: keep('<span class="md-img">[obraz: ' + html.escape(m.group(1)) + "]</span>"),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)",
        lambda m: keep(_render_link(render_inline(m.group(1), base_dir), m.group(2), base_dir)),
        text,
    )
    text = re.sub(r"<(https?://[^>\s]+)>", lambda m: keep(_render_link(html.escape(m.group(1)), m.group(1), base_dir)), text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w])__(?=\S)(.+?)(?<=\S)__(?![\w])", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w])", r"<em>\1</em>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)
    for _ in range(3):  # zagnieżdżone wstawki (link z kodem w etykiecie)
        text = re.sub(r"\x00(\d+)\x00", lambda m: store[int(m.group(1))], text)
    return text


_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_LIST_ITEM = re.compile(r"^(?P<indent>\s*)(?P<marker>[-*+]|\d{1,9}[.)])\s+(?P<body>.*)$")
_FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})\s*(?P<lang>[\w+#.-]*)")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _render_table(rows: list[str], sep: str, base_dir: str) -> str:
    aligns = []
    for cell in _split_row(sep):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "")
    head = _split_row(rows[0])
    out = ['<div class="md-table"><table><thead><tr>']
    for i, cell in enumerate(head):
        cls = f' class="al-{aligns[i]}"' if i < len(aligns) and aligns[i] else ""
        out.append(f"<th{cls}>{render_inline(cell, base_dir)}</th>")
    out.append("</tr></thead><tbody>")
    for row in rows[1:]:
        out.append("<tr>")
        for i, cell in enumerate(_split_row(row)):
            cls = f' class="al-{aligns[i]}"' if i < len(aligns) and aligns[i] else ""
            out.append(f"<td{cls}>{render_inline(cell, base_dir)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _render_list(items: list[dict], base_dir: str) -> str:
    def li(item: dict) -> str:
        body = item["body"]
        m = re.match(r"^\[([ xX])\]\s+(.*)$", body)
        if m:
            checked = m.group(1) != " "
            box = (f'<span class="md-check{" is-done" if checked else ""}" role="img" '
                   f'aria-label="{"zrobione" if checked else "do zrobienia"}"></span>')
            return f'<li class="md-task">{box}<span>{render_inline(m.group(2), base_dir)}</span>'
        return "<li>" + render_inline(body, base_dir)

    def build(idx: int, indent: int) -> tuple[str, int]:
        ordered = items[idx]["ordered"]
        parts = ["<ol>" if ordered else "<ul>"]
        open_li = False
        while idx < len(items) and items[idx]["indent"] >= indent:
            item = items[idx]
            if item["indent"] > indent and open_li:
                sub, idx = build(idx, item["indent"])
                parts.append(sub)
                continue
            if open_li:
                parts.append("</li>")
            parts.append(li(item))
            open_li = True
            idx += 1
        if open_li:
            parts.append("</li>")
        parts.append("</ol>" if ordered else "</ul>")
        return "".join(parts), idx

    out, idx = [], 0
    while idx < len(items):
        chunk, idx = build(idx, items[idx]["indent"])
        out.append(chunk)
    return "".join(out)


def render_markdown(text: str, doc_path: str = "") -> str:
    """Prosty renderer: nagłówki, akapity, listy (zagnieżdżone, checkboxy), tabele, kod, cytaty, linki.

    Surowy HTML ze źródła jest zawsze escapowany (podgląd nie wykonuje treści dokumentu).
    """
    base_dir = posixpath.dirname(doc_path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            # blok pól „Klucz: wartość” (np. nagłówek planu) — każda linia osobno
            joiner = "<br>" if len(para) > 1 and all(HEADER_RE.match(x) for x in para) else " "
            if joiner == "<br>":
                body = joiner.join(render_inline(x, base_dir) for x in para)
            else:
                body = render_inline(" ".join(para), base_dir)
            out.append("<p>" + body + "</p>")
            para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        fence = _FENCE.match(line)
        if fence:
            flush()
            marker = fence.group("fence")
            lang = fence.group("lang")
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{html.escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
            continue
        if not stripped:
            flush()
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            content = render_inline(heading.group(2), base_dir)
            out.append(f'<h{level} id="{_slug(heading.group(2))}">{content}</h{level}>')
            i += 1
            continue
        if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if stripped.startswith(">"):
            flush()
            quote = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + render_markdown("\n".join(quote), doc_path) + "</blockquote>")
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            rows, sep = [line], lines[i + 1]
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(lines[i])
                i += 1
            out.append(_render_table(rows, sep, base_dir))
            continue
        item = _LIST_ITEM.match(line)
        if item and not para:
            items: list[dict] = []
            while i < n:
                cur = lines[i]
                m = _LIST_ITEM.match(cur)
                if m:
                    items.append({
                        "indent": len(m.group("indent").expandtabs(4)),
                        "ordered": m.group("marker")[0].isdigit(),
                        "body": m.group("body"),
                    })
                    i += 1
                elif cur.strip() and cur[:1] in (" ", "\t") and items:
                    items[-1]["body"] += " " + cur.strip()
                    i += 1
                elif not cur.strip() and i + 1 < n and _LIST_ITEM.match(lines[i + 1]) \
                        and (lines[i + 1][:1] in (" ", "\t") or items):
                    i += 1
                else:
                    break
            out.append(_render_list(items, base_dir))
            continue
        para.append(stripped)
        i += 1
    flush()
    return "\n".join(out)


# --------------------------------------------------------------------------- model statusu

def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec="seconds") if ts else None


def list_plans(root: Path) -> list[dict]:
    plans = []
    for path in sorted((root / "docs/plans").glob("*.md")):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        try:
            title, fields = parse_header(read_text(path, 16_000))
        except OSError:
            continue
        plans.append({
            "path": rel,
            "title": title or path.stem,
            "status": normalize_status(fields.get("status")),
            "spec": first_token(fields.get("spec")),
            "run": first_token(fields.get("przebieg")),
            "mtime": _mtime(path) or 0,
        })
    return plans


def list_specs(root: Path) -> list[dict]:
    specs = []
    for path in sorted((root / "docs/specs").glob("*.md")):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        try:
            title, fields = parse_header(read_text(path, 16_000))
        except OSError:
            continue
        specs.append({"path": rel, "title": title or path.stem, "status": normalize_status(fields.get("status")),
                      "mtime": _mtime(path) or 0})
    return specs


def choose_spec_act(plans: list[dict], specs: list[dict], chosen: dict | None) -> dict | None:
    """Spec w akcie I: najnowszy `draft`/`approved`, którego nie wskazuje żaden plan (`Spec:`), nowszy niż plan
    wybrany automatycznie. Skill `feature` zakłada plan dopiero przy „GO plan”, więc bez tego akt I jest niewidoczny."""
    referenced = {p["spec"] for p in plans if p.get("spec")}
    candidates = [sp for sp in specs if sp["status"] in SPEC_OPEN and sp["path"] not in referenced]
    if not candidates:
        return None
    newest = max(candidates, key=lambda sp: (sp["mtime"], sp["path"]))
    if chosen and chosen["mtime"] >= newest["mtime"]:
        return None
    return newest


def choose_run_act(root: Path, plans: list[dict], specs: list[dict], chosen: dict | None) -> str | None:
    """Akt I przed specem: najnowszy katalog przebiegu z `checkpoints.md` nowszym niż plan wybrany automatycznie, którego
    nie wskazuje żaden plan (`Przebieg:`) ani nie pasuje do żadnego speca (`<data>-<slug speca>`). Sesja zaczęła etap
    (pierwszy checkpoint), a speca jeszcze nie ma."""
    runs = root / RUNS_DIR
    if not runs.is_dir():
        return None
    taken = {p["run"] for p in plans if p.get("run")}
    slugs = [posixpath.splitext(posixpath.basename(sp["path"]))[0] for sp in specs]
    newest: tuple[float, str] | None = None
    for d in runs.iterdir():
        name = d.name
        if not d.is_dir() or d.is_symlink() or not RUN_ID_RE.match(name) or name in taken:
            continue
        if any(name == slug or name.endswith("-" + slug) for slug in slugs):
            continue
        mtime = _mtime(d / "checkpoints.md")
        if mtime is None or (chosen and chosen["mtime"] >= mtime):
            continue
        if newest is None or (mtime, name) > newest:
            newest = (mtime, name)
    return newest[1] if newest else None


def spec_run_id(root: Path, spec_rel: str, fields: dict[str, str]) -> str | None:
    """Przebieg speca bez planu: linia `Przebieg:` speca, potem katalog o nazwie planu z linii `Plan:`,
    na końcu najnowszy katalog `<data>-<slug speca>` (albo `<slug speca>`)."""
    runs = root / RUNS_DIR
    if not runs.is_dir():
        return None
    def ok(name: str | None) -> bool:
        return bool(name) and bool(RUN_ID_RE.match(name)) and (runs / name).is_dir()
    explicit = first_token(fields.get("przebieg"))
    if ok(explicit):
        return explicit
    plan = first_token(fields.get("plan"))
    if plan and ok(posixpath.splitext(posixpath.basename(plan))[0]):
        return posixpath.splitext(posixpath.basename(plan))[0]
    stem = posixpath.splitext(posixpath.basename(spec_rel))[0]
    names = sorted(d.name for d in runs.iterdir()
                   if d.is_dir() and RUN_ID_RE.match(d.name) and (d.name == stem or d.name.endswith("-" + stem)))
    return names[-1] if names else None


def find_prototype(root: Path, spec_rel: str | None) -> str | None:
    """Katalog prototypu, którego `prototype.json` ma `requirements` równe ścieżce speca."""
    base = root / PROTOTYPES_DIR
    if not spec_rel or not base.is_dir() or base.is_symlink():
        return None
    for meta in sorted(base.glob("*/prototype.json")):
        try:
            data = json.loads(read_text(meta, 64_000))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("requirements") == spec_rel:
            return f"{PROTOTYPES_DIR}/{meta.parent.name}"
    return None


def choose_plan(plans: list[dict]) -> dict | None:
    """Najnowszy `in_progress`, inaczej `approved`, inaczej `draft`, inaczej ostatni `done` (nazwa z datą, potem mtime)."""
    candidates = [p for p in plans if p["status"] in STATUS_RANK]
    if not candidates:
        return None
    newest_first = sorted(candidates, key=lambda p: (p["path"], p["mtime"]), reverse=True)
    return sorted(newest_first, key=lambda p: STATUS_RANK[p["status"]])[0]


REMOTE_PATTERNS = (
    # Azure DevOps: git@ssh.dev.azure.com:v3/org/project/repo, https://[user@]dev.azure.com/org/project/_git/repo
    (re.compile(r"^(?:ssh://)?git@ssh\.dev\.azure\.com[:/]v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)/?$"), "azure"),
    (re.compile(r"^https://(?:[^@/]+@)?dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/?#]+?)/?$"), "azure"),
    # GitHub: git@github.com:org/repo(.git), https://github.com/org/repo(.git)
    (re.compile(r"^(?:ssh://)?git@github\.com[:/](?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"), "github"),
    (re.compile(r"^https://(?:[^@/]+@)?github\.com/(?P<org>[^/]+)/(?P<repo>[^/?#]+?)(?:\.git)?/?$"), "github"),
)
PLAN_PR_RE = re.compile(r"^PR:\s*#(?P<num>\d+)\s*\(link:\s*(?P<url>https://[^\s)]+)\)\s*$", re.MULTILINE)
SAFE_SEG = re.compile(r"^[\w.~%-]+$")


def remote_web(url: str | None) -> dict | None:
    """Adres WWW repozytorium z URL-a `origin` (Azure DevOps albo GitHub); inny host = None (bez linku)."""
    url = (url or "").strip()
    for rx, host in REMOTE_PATTERNS:
        m = rx.match(url)
        if not m:
            continue
        parts = m.groupdict()
        if not all(SAFE_SEG.match(v) for v in parts.values()):
            return None
        if host == "azure":
            base = f"https://dev.azure.com/{parts['org']}/{parts['project']}/_git/{parts['repo']}"
            return {"host": "azure", "repo": base, "branchUrl": base + "?version=GB{branch}"}
        base = f"https://github.com/{parts['org']}/{parts['repo']}"
        return {"host": "github", "repo": base, "branchUrl": base + "/tree/{branch}"}
    return None


def _origin_url(common: Path) -> str | None:
    """`url` z sekcji `[remote "origin"]` pliku config repozytorium (bez uruchamiania gita)."""
    try:
        text = read_text(common / "config", 64_000)
    except OSError:
        return None
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped
            continue
        if section == '[remote "origin"]' and stripped.lower().startswith("url"):
            key, _, val = stripped.partition("=")
            if key.strip().lower() == "url":
                return val.strip()
    return None


def _has_ref(common: Path, ref: str) -> bool:
    if (common / ref).is_file():
        return True
    packed = common / "packed-refs"
    try:
        return packed.is_file() and any(line.endswith(" " + ref) for line in read_text(packed).splitlines())
    except OSError:
        return False


def read_git(root: Path) -> dict:
    info: dict = {"branch": None, "head": None, "remote": None, "branchUrl": None, "branchPushed": None}
    git = root / ".git"
    try:
        if git.is_file():
            m = re.match(r"gitdir:\s*(.+)", read_text(git, 4096).strip())
            if not m:
                return info
            git = (root / m.group(1).strip()).resolve()
        head = read_text(git / "HEAD", 4096).strip()
        common = git
        if (git / "commondir").is_file():
            common = (git / read_text(git / "commondir", 4096).strip()).resolve()
        if head.startswith("ref:"):
            ref = head[4:].strip()
            info["branch"] = ref.removeprefix("refs/heads/")
            for base in (git, common):
                if (base / ref).is_file():
                    info["headFull"] = read_text(base / ref, 128).strip()
                    info["head"] = info["headFull"][:12]
                    break
            else:
                packed = common / "packed-refs"
                if packed.is_file():
                    for line in read_text(packed).splitlines():
                        if line.endswith(" " + ref):
                            info["headFull"] = line.split()[0]
                            info["head"] = info["headFull"][:12]
        elif SHA_RE.match(head):
            info["headFull"], info["head"] = head, head[:12]
        web = remote_web(_origin_url(common))
        if web:
            from urllib.parse import quote
            info["remote"] = {"host": web["host"], "repo": web["repo"]}
            if info["branch"]:
                info["branchPushed"] = _has_ref(common, f"refs/remotes/origin/{info['branch']}")
                safe = "" if web["host"] == "azure" else "/"  # Azure koduje „/” w version=GB…
                info["branchUrl"] = web["branchUrl"].replace("{branch}", quote(info["branch"], safe=safe))
    except OSError:
        pass
    return info


def _stage(label: str, done: bool, tip: str = "", key: str | None = None) -> dict:
    """Etap kafelka; `key` = klucz opisu w `descriptions.json` (dymek: co, kto, wynik, skąd strona to wie)."""
    return {"label": label, "done": done, "tip": tip, "key": key}


RISKY_SECTIONS = ("Kontrakty", "Migracja", "Dane")


def risky_sections(text: str) -> list[str]:
    """Sekcje `## Kontrakty|Migracja|Dane` speca z treścią (nie „nie dotyczy”) — wtedy przed GO plan potrzebny pre-implement."""
    found = []
    for name in RISKY_SECTIONS:
        m = re.search(rf"^##\s+{name}\b[^\n]*\n(?P<body>.*?)(?=^#{{1,6}}\s|\Z)", text, re.MULTILINE | re.DOTALL)
        body = (m.group("body") if m else "").strip()
        if body and not re.match(r"^[-*\s]*nie dotyczy", body, re.IGNORECASE):
            found.append(name)
    return found


def compute_acts(spec: dict, plan: dict | None, progress: dict, run_files: list[str],
                 extra: dict | None = None) -> list[dict]:
    extra = extra or {}
    names = [f.rsplit("/", 1)[-1].lower() for f in run_files]
    spec_reviews = sum(1 for f in names if "spec-review" in f)
    plan_reviews = sum(1 for f in names if "plan-review" in f)
    pre_impl = any("pre-implement" in f for f in names)
    proto_rounds = sum(1 for f in names if "prototyp" in f)

    spec_status = spec.get("status")
    if spec.get("preparing"):
        spec_state = "active"
    elif not spec.get("exists"):
        spec_state = "planned"
    elif spec_status in SPEC_DONE:
        spec_state = "done"
    else:
        spec_state = "active"

    plan_status = plan.get("status") if plan else None
    if plan is None:
        plan_state = "active" if spec_state == "done" else "planned"
    elif plan_status in PLAN_APPROVED:
        plan_state = "done"
    else:
        plan_state = "active"

    if plan_status == "done":
        real_state = "done"
    elif plan_status == "in_progress":
        real_state = "active"
    else:
        real_state = "planned"

    spec_stages = [
        _stage("Szkic", spec.get("exists", False), spec.get("path") or "brak speca", key="stage_spec_draft"),
        _stage(f"Review ×{spec_reviews}" if spec_reviews > 1 else "Review", spec_reviews > 0,
               "rundy review speca w katalogu przebiegu", key="stage_spec_review"),
    ]
    acc = spec.get("prototypeAcceptance")
    if acc and acc.get("screens"):
        accepted = acc["accepted"] == acc["screens"] and not acc.get("stale")
        spec_stages.append(_stage(f"Prototyp {acc['accepted']}/{acc['screens']}", accepted, key="stage_prototype", tip=
                                  f"{spec.get('prototype')}: zaakceptowane ekrany {acc['accepted']} z {acc['screens']}"
                                  + (f", nieaktualne {acc['stale']}" if acc.get("stale") else "")))
    elif spec.get("prototype") or proto_rounds:
        spec_stages.append(_stage("Prototyp", bool(proto_rounds) or spec_state == "done",
                                  spec.get("prototype") or "uwagi do prototypu w przebiegu", key="stage_prototype"))
    spec_stages.append(_stage("Zatwierdzony", spec_state == "done", f"Status speca: {spec_status or '—'}",
                              key="stage_spec_approved"))

    plan_stages = [
        _stage("Plan", plan is not None, plan.get("path") if plan else "brak planu", key="stage_plan"),
        _stage("Review", plan_reviews > 0, "rundy review planu w katalogu przebiegu", key="stage_plan_review"),
        _stage("Pre-implement", pre_impl, "bramka gotowości przed ryzykowną zmianą", key="stage_pre_implement"),
        _stage("GO implement", plan_status in ("in_progress", "done"), f"Status planu: {plan_status or '—'}",
               key="stage_go_implement"),
    ]
    code_reviews = sum(1 for f in names if "code-review" in f or f.startswith("final-review"))
    real_stages = [
        _stage(f"Review kodu ×{code_reviews}" if code_reviews > 1 else "Review kodu", code_reviews > 0,
               "raporty code-review*/final-review* w katalogu przebiegu", key="code_review"),
        _stage("Zamknięcie", plan_status == "done", f"Status planu: {plan_status or '—'}", key="closure"),
    ]
    risky = extra.get("risky") or []
    spec_warning = (f"brak pre-implement przed GO plan: spec ma sekcje {', '.join(risky)}"
                    if risky and not pre_impl and spec.get("exists") else None)
    cur = progress.get("current")
    real_detail = (f"krok {cur['index']} z {progress['total']}: {cur['id']} {cur['title']}"
                   if cur else ("wszystkie kroki zaznaczone" if progress.get("total") else "brak kroków w Progress"))
    return [
        {"key": "spec", "roman": "I", "name": "Spec", "state": spec_state, "stages": spec_stages, "warning": spec_warning,
         "detail": "specyfikacja w przygotowaniu" if spec.get("preparing") else spec.get("sha") and f"Spec-sha {spec['sha'][:10]}",
         "status": spec_status},
        {"key": "plan", "roman": "II", "name": "Plan", "state": plan_state, "stages": plan_stages,
         "detail": (f"{progress.get('total', 0)} kroków w {len(progress.get('phases', []))} fazach" if plan
                    else "brak planu (GO plan dany, plan w przygotowaniu)" if spec_state == "done"
                    else "brak planu (czeka na GO plan)" if spec.get("exists") or spec.get("preparing") else None),
         "status": plan_status},
        {"key": "real", "roman": "III", "name": "Realizacja", "state": real_state, "stages": real_stages,
         "percent": progress.get("percent", 0), "detail": real_detail, "status": plan_status},
    ]


def _pending_percent(done: int, pending: int, total: int) -> int:
    """Odcinek „zrobione wg checkpointu, czeka na weryfikację”: różnica zaokrągleń, więc oba odcinki sumują się poprawnie."""
    if not total:
        return 0
    return round(100 * (done + pending) / total) - round(100 * done / total)


def build_progress(phases: list[dict], in_progress: list[str] | None, plan_status: str | None,
                   reported: list[str] | None = None) -> dict:
    steps = [s for p in phases for s in p["steps"]]
    known = {s["id"] for s in steps if not s["done"]}
    active = [sid for sid in (in_progress or []) if sid in known]
    # „w toku: F1” przy planie z podkrokami F1.1…F1.k = pierwszy niezaznaczony podkrok tej fazy
    for sid in in_progress or []:
        if sid not in known:
            first = next((s["id"] for s in steps if not s["done"] and s["id"].startswith(sid + ".")), None)
            if first and first not in active:
                active.append(first)
    reported_set = {sid for sid in (reported or []) if sid in known and sid not in active}
    source = "checkpoint" if active else None
    if not active and plan_status == "in_progress":
        first = next((s for s in steps if not s["done"]), None)
        if first:
            active, source = [first["id"]], "first-unchecked"
    index = 0
    current = None
    out_phases = []
    for phase in phases:
        p_steps = []
        for step in phase["steps"]:
            index += 1
            if step["done"]:
                state = "done"
            elif step["id"] in active:
                state = "active"
            else:
                state = "reported" if step["id"] in reported_set else "planned"
            p_steps.append({**step, "state": state, "index": index})
            if state == "active" and current is None:
                current = {"index": index, "id": step["id"], "title": step["title"]}
        done = sum(1 for s in p_steps if s["done"])
        pending = sum(1 for s in p_steps if s["state"] == "reported")
        name = phase["name"]
        label, _, short = name.partition(":")
        out_phases.append({
            "name": name, "label": label.strip() if short else "", "short": (short or name).strip(),
            "total": len(p_steps), "done": done,
            "percent": round(100 * done / len(p_steps)) if p_steps else 0,
            "pending": pending, "pendingPercent": _pending_percent(done, pending, len(p_steps)),
            "state": "done" if done == len(p_steps) else ("active" if any(s["state"] == "active" for s in p_steps) else "planned"),
            "steps": p_steps,
        })
    total = len(steps)
    done_total = sum(1 for s in steps if s["done"])
    return {
        "total": total, "done": done_total,
        "percent": round(100 * done_total / total) if total else 0,
        "pending": len(reported_set), "pendingPercent": _pending_percent(done_total, len(reported_set), total),
        "phases": out_phases, "active": active, "activeSource": source,
        "current": current, "parallel": max(0, len(active) - 1), "reported": sorted(reported_set),
    }


DESCRIPTIONS_FILE = Path(__file__).resolve().parent.parent / "descriptions.json"


def load_descriptions() -> dict:
    """Statyczne opisy kroków, aktów i bramek (dymki): `descriptions.json` skilla, łatwy do uzupełniania."""
    try:
        data = json.loads(DESCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("text"), str)}


def git_blob_sha(data: bytes) -> str:
    """`git hash-object --no-filters` bajtów pliku (to samo liczy `skillsync … approve --expect-sha`)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def file_sha(path: Path | None) -> str | None:
    try:
        return git_blob_sha(path.read_bytes()) if path else None
    except OSError:
        return None


def session_url_ok(url: str | None) -> bool:
    """Adres sesji podany przy starcie serwera: tylko https, bez danych logowania w adresie."""
    if not url:
        return False
    u = urlparse(url)
    return u.scheme == "https" and bool(u.hostname) and not u.username and not u.password and len(url) <= 500


def request_host(host_header: str | None) -> str:
    """Nazwa hosta z nagłówka Host (bez portu, bez nawiasów IPv6), małymi literami."""
    raw = (host_header or "").strip().lower()
    if raw.startswith("["):
        return raw[1:raw.find("]")] if "]" in raw else ""
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def read_delivery(root: Path) -> dict:
    """`delivery {mode, base}` z manifestu `.github/.skillsync.yaml` bez parsera YAML (blok `delivery:` z wcięciem
    albo `delivery: {mode: pr, base: main}`). Brak klucza = tryb `pr` (rdzeń AGENTS); zła wartość = `invalid`."""
    out = {"mode": "pr", "base": None, "source": "default"}
    try:
        text = read_text(root / MANIFEST, 64_000)
    except OSError:
        return out
    m = re.search(r"^delivery:[ \t]*\{(?P<inline>[^}\n]*)\}", text, re.MULTILINE)
    if m:
        body = m.group("inline").replace(",", "\n")
    else:
        m = re.search(r"^delivery:[ \t]*\n(?P<block>(?:[ \t]+[^\n]*\n?)+)", text, re.MULTILINE)
        if not m:
            return out
        body = m.group("block")
    fields = {k.lower(): v.strip().strip("'\"") for k, v in re.findall(r"^[ \t]*(\w+)[ \t]*:[ \t]*([^#\n]*)", body, re.MULTILINE)}
    mode = fields.get("mode", "pr").lower() or "pr"
    out.update(mode=mode if mode in DELIVERY_MODES else "invalid", source="manifest")
    base = fields.get("base")
    out["base"] = base if base and REF_NAME_RE.match(base) else None
    return out


def _commits(n: int) -> str:
    return f"{n} {'commit' if n == 1 else 'commity' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'commitów'}"


def git_ahead(root: Path, branch: str | None) -> dict:
    """Ile commitów HEAD nie ma jeszcze na zdalnym odpowiedniku gałęzi roboczej (tylko odczyt): `origin/<gałąź>..HEAD`,
    a gdy gałęzi nie ma na origin — `HEAD --not --remotes=origin`. „CP” wypycha gałąź roboczą, nie bazę."""
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None
    if not branch or not REF_NAME_RE.match(branch):
        return {"remote": None, "pushed": None, "ahead": None}
    remote = f"origin/{branch}"
    if git("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}^{{commit}}"):
        count, pushed = git("rev-list", "--count", f"{remote}..HEAD"), True
    else:
        count, pushed = git("rev-list", "--count", "HEAD", "--not", "--remotes=origin"), False
    return {"remote": remote, "pushed": pushed, "ahead": int(count) if count and count.isdigit() else None}


def read_approvals(root: Path, run_id: str | None) -> list[dict]:
    """Wpisy `.skillsync/runs/<run-id>/approvals.jsonl` (najnowsze na końcu); błędne linie pomijane."""
    if not run_id or not RUN_ID_RE.match(run_id):
        return []
    path = root / RUNS_DIR / run_id / RUN_APPROVALS
    try:
        if path.is_symlink() or not path.is_file():
            return []
        lines = read_text(path, APPROVALS_READ_MAX).splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("word"), str):
            out.append(item)
    return out


def delivery_target(delivery: dict, branch: str | None, plan_fields: dict[str, str]) -> str | None:
    """Dokąd „CP” oddaje pracę: gałąź robocza; w trybie `direct` linia planu `Oddanie: <gałąź>` (odpowiedź użytkownika)."""
    handover = first_token(plan_fields.get("oddanie"))
    if delivery.get("mode") == "direct" and handover and REF_NAME_RE.match(handover):
        return handover
    return branch


def delivery_words(delivery: dict, ahead: dict, plan: dict | None, waiting: list[dict],
                   branch: str | None = None, target: str | None = None) -> list[dict]:
    """Słowa dostarczania dla kafelka Aktu III: `CP` zawsze, `READY` tylko w trybie `pr`. Aktywne, gdy plan `done`,
    sesja czeka na to słowo albo gałąź robocza ma commity, których nie ma na origin; `tip` mówi, dokąd i ile."""
    if delivery.get("mode") == "invalid":
        return []
    waits = {(w.get("word") or "").rstrip("!") for w in waiting}
    done = bool(plan) and plan.get("status") == "done"
    n = ahead.get("ahead")
    target = target or branch
    where = f"origin/{target}" if target else "origin"
    if target and branch and target != branch:
        where += f" (oddanie z {branch})"
    words = ["CP"] + (["READY"] if delivery.get("mode") == "pr" else [])
    out = []
    for word in words:
        reasons = []
        if word in waits:
            reasons.append(f"sesja czeka na „{word}”")
        if done:
            reasons.append("plan done")
        if n:
            remote = ahead.get("remote") if ahead.get("pushed") else "origin (gałęzi jeszcze nie ma na origin)"
            reasons.append(f"{_commits(n)} niewypchniętych względem {remote}")
        tip = "; ".join(reasons) if reasons else ("wszystko wypchnięte" if n == 0 else "nic do dostarczenia")
        if word == "CP":
            tip = f"CP → {where}; {tip}"
        out.append({"word": word, "active": bool(reasons), "tip": tip, "target": target if word == "CP" else None})
    return out


def _hhmm(entry: dict | None) -> str:
    try:
        return datetime.fromisoformat(str(entry["at"]).replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except (KeyError, TypeError, ValueError):
        return ""


def _given_tip(word: str, entry: dict | None) -> str:
    at = _hhmm(entry)
    return f"zgoda „{word}” dana {at}".rstrip() if at else f"zgoda „{word}” już dana"


def consent_buttons(spec: dict, plan: dict | None, waiting: list[dict], words: list[dict], log: list[dict],
                    head_full: str | None, run_ok: bool, cli_ok: bool) -> list[dict]:
    """Przyciski zgód w kafelkach aktów, każdy w jednym z czterech stanów:
    przed (akt nie nadszedł) = brak pozycji; `waiting` = widoczny i aktywny (stan czeka na tę zgodę);
    `running` = widoczny, nieaktywny, `reason` „czeka na…” (akt trwa, jeszcze nie czeka);
    `after` = widoczny, nieaktywny, `reason` „zgoda dana HH:MM” (zgoda już dana; drugi zapis niemożliwy).
    Sygnał „czeka” pisze sesja: `czeka na GO plan|GO implement` w statusie speca/planu albo `czeka:` w checkpoincie."""
    out: list[dict] = []
    waits = {(w.get("word") or "").rstrip("!").upper() for w in waiting}
    last = {e.get("word"): e for e in log}
    acc = spec.get("prototypeAcceptance") or {}
    proto_open = bool(acc.get("screens")) and (acc.get("accepted", 0) < acc["screens"] or acc.get("stale"))
    proto_reason = f"czeka na akceptację prototypu ({acc.get('accepted', 0)}/{acc.get('screens', 0)})"
    plan_status = (plan or {}).get("status")
    plan_text = ((plan or {}).get("statusText") or "").lower()
    sketch = bool(plan) and ("szkic" in plan_text or "czeka na spec" in plan_text)
    spec_text = (spec.get("statusText") or "").lower()

    def add(kind: str, state: str, reason: str | None, path: str | None, sha: str | None, given: dict | None = None) -> None:
        out.append({"kind": kind, "word": APPROVE_KINDS[kind], "state": state, "reason": reason, "path": path,
                    "sha": sha or "", "given": given, "blocked": None if state == "waiting" else reason})

    # Akt I → „GO plan”: tylko gdy jest spec.
    if spec.get("exists"):
        if spec.get("status") in SPEC_DONE:
            add("spec", "after", _given_tip("GO plan", last.get("GO plan")), spec["path"], spec.get("fileSha"),
                last.get("GO plan") or {"at": None})
        elif spec.get("status") == "draft" and (not plan or sketch):
            signal = ("GO PLAN" in waits or "czeka na go plan" in spec_text or "czeka na go plan" in plan_text)
            reason = (None if signal and not proto_open and cli_ok else proto_reason if proto_open
                      else "czeka na koniec pętli spec (sesja zgłosi „czeka: GO plan”)" if not signal
                      else "zainstalowany skillsync nie zna `spec approve` — wpisz „GO plan” w sesji")
            add("spec", "waiting" if reason is None else "running", reason, spec["path"], spec.get("fileSha"))
    # Akt II → „GO implement”: gdy jest plan inny niż szkic.
    if plan and not sketch:
        if plan_status in PLAN_APPROVED:
            add("plan", "after", _given_tip("GO implement", last.get("GO implement")), plan["path"], plan.get("fileSha"),
                last.get("GO implement") or {"at": None})
        elif plan_status == "draft":
            signal = "GO IMPLEMENT" in waits or "czeka na go implement" in plan_text
            reason = ("czeka na „GO plan” (spec nie jest approved)" if spec.get("exists") and spec.get("status") != "approved"
                      else proto_reason if proto_open
                      else "czeka na koniec aktu II (pętla plan, readiness, wersja)" if not signal
                      else None if cli_ok else "zainstalowany skillsync nie zna `plan approve --expect-sha` — wpisz „GO implement” w sesji")
            add("plan", "waiting" if reason is None else "running", reason, plan["path"], plan.get("fileSha"))
    # Akt III → „CP” / „READY”: od startu realizacji (plan in_progress albo done).
    if plan_status in ("in_progress", "done"):
        for w in words:
            kind = w["word"].lower()
            entry = last.get(w["word"])
            if entry and head_full and entry.get("sha") == head_full:
                add(kind, "after", _given_tip(w["word"], entry) + f" (HEAD {head_full[:8]})", None, head_full, entry)
                out[-1]["target"] = entry.get("target") or w.get("target")
                continue
            reason = (None if w["active"] else f"czeka na koniec realizacji ({w['tip']})")
            if reason is None and not head_full:
                reason = "brak HEAD repozytorium"
            elif reason is None and not run_ok:
                reason = "brak katalogu przebiegu (.skillsync/runs/<run-id>) dla wpisu zgody"
            add(kind, "waiting" if reason is None else "running", reason or w["tip"], None, head_full)
            out[-1]["reason"] = reason if reason else w["tip"]
            out[-1]["target"] = w.get("target")
    return out


class Board:
    """Stan serwera: projekt, obserwator plików i licznik wersji dla SSE."""

    def __init__(self, root: Path, default_plan: str | None = None, poll: float = 1.0,
                 heartbeat: float = 15.0, prototype_port: int = PROTOTYPE_PORT,
                 approvals: bool = True, session_url: str | None = None, approve_hosts: tuple[str, ...] = ()):
        self.root = root.resolve()
        self.approvals = approvals
        self.session_url = session_url if session_url_ok(session_url) else None
        self._approve_lock = threading.Lock()
        self._cli_support: tuple[str | None, bool] = ("", False)
        self._ahead: tuple[tuple, float, dict] = ((), 0.0, {})
        self.approve_hosts = LOOPBACK_HOSTS | {h.strip().lower() for h in approve_hosts if h.strip()}
        self.default_plan = default_plan
        self.poll = poll
        self.heartbeat = heartbeat
        self.prototype_port = prototype_port
        self.version = 0
        self.changed_at = time.time()
        self.cond = threading.Condition()
        self.stop = threading.Event()
        self.sse_clients = 0
        self._sig = self.signature()
        self._proto = (0.0, False)
        self._accept: tuple[tuple, float, dict] = ((), 0.0, {})

    # --- obserwator
    def watched_paths(self) -> list[Path]:
        paths: list[Path] = []
        for d in DOC_DIRS:
            base = self.root / d
            paths.append(base)
            paths.extend(base.glob("*.md"))
        runs = self.root / RUNS_DIR
        paths.append(runs)
        if runs.is_dir():
            for run in runs.iterdir():
                if run.is_dir():
                    paths.append(run)
                    paths.extend(run.glob("*.md"))
                    paths.append(run / RUN_APPROVALS)
        protos = self.root / PROTOTYPES_DIR
        if protos.is_dir():
            paths.extend(protos.glob("*/prototype.json"))
            paths.extend(protos.glob("*/comments.json"))
        git = self.root / ".git"
        paths.extend([git / "HEAD", git / "packed-refs"])
        branch = read_git(self.root).get("branch")
        if branch:
            paths.append(git / "refs/heads" / branch)
        # Push i fetch zmieniają refy origin: licznik commitów do wypchnięcia (CP) odświeża się bez zmiany plików projektu.
        base = read_delivery(self.root).get("base")
        for name in {branch, base, "HEAD", "main", "master"} - {None}:
            paths.append(git / "refs/remotes/origin" / name)
        return paths

    def signature(self) -> tuple:
        sig = []
        for p in self.watched_paths():
            try:
                st = p.stat()
                sig.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        return tuple(sorted(sig))

    def check_once(self) -> bool:
        sig = self.signature()
        if sig == self._sig:
            return False
        self._sig = sig
        with self.cond:
            self.version += 1
            self.changed_at = time.time()
            self.cond.notify_all()
        return True

    def watch_loop(self) -> None:
        while not self.stop.wait(self.poll):
            try:
                self.check_once()
            except Exception as exc:  # obserwator nie może paść
                log(f"obserwator: {exc!r}")

    # --- prototyp
    def prototype_up(self) -> bool:
        ts, up = self._proto
        if time.time() - ts < 5:
            return up
        try:
            with socket.create_connection(("127.0.0.1", self.prototype_port), timeout=0.3):
                up = True
        except OSError:
            up = False
        self._proto = (time.time(), up)
        return up

    def waiting(self, run: dict, spec: dict | None, plan: dict | None, slug: str | None) -> list[dict]:
        """Na co sesja czeka: znacznik `czeka:` najnowszego checkpointu ma pierwszeństwo; bez niego fakty z plików:
        `Status: … (czeka na …)` planu albo speca i prototyp z niezaakceptowanymi ekranami przed „GO implement”."""
        ctx = {"spec": spec.get("path") if spec else None, "plan": plan.get("path") if plan else None, "slug": slug}
        newest = run["checkpoints"][0] if run.get("checkpoints") else None
        marker = wait_marker(newest["text"]) if newest else None
        if marker:
            return [wait_item(marker, "checkpoint", newest["time"], ctx)]
        items: list[dict] = []
        for doc, source in ((plan, "plan"), (spec, "spec")):
            text = (doc or {}).get("statusText") or ""
            m = STATUS_WAIT_RE.search(text)
            satisfied = m and re.search(r"GO plan|GO implement", m.group("what"), re.IGNORECASE) and \
                doc.get("status") in ("approved", "in_progress", "done", "implemented")
            if m and not satisfied and doc.get("status") in ("draft", "approved", "in_progress"):
                items.append(wait_item(m.group("what").strip(), source, None, ctx))
                break
        acc = spec.get("prototypeAcceptance") if spec else None
        before_go = not plan or plan.get("status") in ("draft", "approved")
        if acc and acc.get("screens") and before_go and (acc["accepted"] < acc["screens"] or acc.get("stale")):
            what = f"akceptacja prototypu {slug} ({acc['accepted']}/{acc['screens']})"
            if not any("prototyp" in i["what"].lower() for i in items):
                items.append(wait_item(what, "prototype", None, ctx))
        return items

    def prototype_acceptance(self, slug: str) -> dict | None:
        """Akceptacja ekranów z `skillsync prototype list` (tylko odczyt), w pamięci do zmiany comments.json/index.html
        prototypów albo PROTO_CACHE_S sekund. Bez CLI albo przy błędzie: None (etap bez licznika)."""
        base = self.root / PROTOTYPES_DIR
        key = tuple(sorted((str(f), _mtime(f)) for pat in ("*/comments.json", "*/index.html") for f in base.glob(pat)))
        cached_key, ts, table = self._accept
        if key != cached_key or time.time() - ts > PROTO_CACHE_S:
            table = {}
            exe = shutil.which("skillsync")
            if exe:
                try:
                    out = subprocess.run([exe, "prototype", "list", str(self.root)], capture_output=True, text=True,
                                         timeout=15, check=False).stdout
                    for item in json.loads(out).get("prototypes", []):
                        acc = item.get("acceptance")
                        if isinstance(acc, dict) and isinstance(item.get("slug"), str):
                            table[item["slug"]] = {k: int(acc.get(k) or 0) for k in ("screens", "accepted", "stale")}
                except (OSError, ValueError, subprocess.SubprocessError, AttributeError, TypeError):
                    table = {}
            self._accept = (key, time.time(), table)
        return table.get(slug)

    # --- dokumenty
    def doc_item(self, rel: str) -> dict | None:
        path = resolve_doc(self.root, rel)
        if not path:
            return None
        try:
            title, fields = parse_header(read_text(path, 16_000))
        except OSError:
            return None
        return {"path": rel, "title": title or path.stem, "status": normalize_status(fields.get("status")),
                "mtime": _iso(_mtime(path))}

    def doc(self, rel: str | None) -> dict | None:
        path = resolve_doc(self.root, rel)
        if not path:
            return None
        size = path.stat().st_size
        text = read_text(path)
        title, fields = parse_header(text)
        return {"path": rel, "title": title or path.stem, "status": normalize_status(fields.get("status")),
                "mtime": _iso(_mtime(path)), "size": size, "truncated": size > MAX_DOC_BYTES,
                "html": render_markdown(text, rel or "")}

    def doc_groups(self, current: list[str], run_files: list[str], run_id: str | None) -> list[dict]:
        groups = []
        task = [d for d in (self.doc_item(p) for p in current if p) if d]
        if task:
            groups.append({"key": "task", "label": "Bieżące zadanie", "items": task})
        if run_files:
            items = [d for d in (self.doc_item(p) for p in run_files) if d]
            if items:
                groups.append({"key": "run", "label": f"Przebieg {run_id}", "items": items})
        labels = {"docs/specs": "Speki", "docs/plans": "Plany", "docs/design": "Projekt", "docs/domain": "Domena"}
        for d in DOC_DIRS:
            files = sorted((self.root / d).glob("*.md"), key=lambda p: p.name, reverse=True)
            items = []
            for f in files:
                rel = _posix_rel(self.root, f)
                if rel and rel not in current:
                    item = self.doc_item(rel)
                    if item:
                        items.append(item)
            if items:
                groups.append({"key": d.split("/")[-1], "label": labels[d], "items": items})
        return groups

    # --- status
    def cli_supports_approvals(self) -> bool:
        """Czy `skillsync` w PATH zna `spec approve --expect-sha` (sprawdzane raz na ścieżkę programu)."""
        exe = shutil.which("skillsync")
        if exe != self._cli_support[0]:
            ok = False
            if exe:
                try:
                    out = subprocess.run([exe, "spec", "approve", "--help"], capture_output=True, text=True, timeout=15,
                                         check=False)
                    ok = out.returncode == 0 and "--expect-sha" in out.stdout
                except (OSError, subprocess.SubprocessError):
                    ok = False
            self._cli_support = (exe, ok)
        return self._cli_support[1]

    def approve(self, kind: str, rel: str, sha: str, plan: str | None = None) -> tuple[int, dict]:
        """Zgoda z przycisku: tylko akcja, którą strona właśnie oferuje (ten sam plik i sha), przez CLI skillsync."""
        if not self.approvals:
            return 403, {"error": "Zgody z tej strony są wyłączone (--no-approvals)."}
        if kind not in APPROVE_KINDS or not isinstance(rel, str) or not isinstance(sha, str):
            return 400, {"error": "Nieprawidłowe żądanie."}
        if kind in ("cp", "ready"):
            return self._approve_delivery(kind, rel, sha, plan if isinstance(plan, str) else None)
        if not self.cli_supports_approvals():
            return 503, {"error": "Zainstalowany skillsync nie zna `spec approve --expect-sha`; wpisz zgodę w sesji."}
        with self._approve_lock:
            current = self.status(rel if kind == "plan" else None)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind and a["path"] == rel), None)
            if offer is None:
                return 409, {"error": f"„{APPROVE_KINDS[kind]}” nie jest teraz możliwe dla {rel}; odśwież stronę."}
            if offer["state"] != "waiting":
                return 409, {"error": f"„{offer['word']}” nieaktywne: {offer['reason']}."}
            if offer["sha"] != sha:
                return 409, {"error": "Plik zmienił się od wyświetlenia; przeczytaj go ponownie i zatwierdź jeszcze raz."}
            exe = shutil.which("skillsync")
            if not exe:
                return 503, {"error": "Brak polecenia skillsync w PATH serwera; wpisz zgodę w sesji."}
            try:
                proc = subprocess.run([exe, kind, "approve", str(self.root), rel, "--expect-sha", sha, "--json"],
                                      capture_output=True, text=True, timeout=30, check=False)
                report = json.loads(proc.stdout or "{}")
            except (OSError, ValueError, subprocess.SubprocessError):
                return 502, {"error": "skillsync nie odpowiedział poprawnie; wpisz zgodę w sesji."}
            if proc.returncode != 0 or report.get("status") != "PASS":
                reason = str(report.get("reason") or proc.stderr.strip()[-300:] or "nieznany błąd")
                if "invalid choice" in proc.stderr or "unrecognized arguments" in proc.stderr:
                    reason = "zainstalowany skillsync nie zna tej komendy (potrzebny `spec approve` i `--expect-sha`)"
                return 409, {"error": f"skillsync odmówił: {reason}"}
            run_id = current["run"]["id"] if current["run"].get("exists") else None
            entry = self._entry(APPROVE_KINDS[kind], sha, file=rel, shaAfter=report.get("sha"))
            self._append_approval(run_id, entry)
            log(f"zgoda „{APPROVE_KINDS[kind]}” dla {rel} (sha {sha[:10]})")
        self.check_once()
        return 200, {"ok": True, "word": APPROVE_KINDS[kind], "file": rel, "next": NEXT_HINT}

    def _entry(self, word: str, sha: str, **extra) -> dict:
        try:
            by = getpass.getuser()
        except (OSError, KeyError):
            by = None
        entry = {"schema": 1, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "word": word, "sha": sha,
                 "by": by, "source": "dashboard"}
        entry.update({k: v for k, v in extra.items() if v is not None})
        return entry

    def _append_approval(self, run_id: str | None, entry: dict) -> bool:
        """Dopisuje wpis do approvals.jsonl przebiegu (albo, bez przebiegu, do śladu strony). Jedna linia, append."""
        if run_id and RUN_ID_RE.match(run_id) and (self.root / RUNS_DIR / run_id).is_dir():
            log_path = self.root / RUNS_DIR / run_id / RUN_APPROVALS
        else:
            log_path = self.root / APPROVALS_LOG
        try:
            if log_path.is_symlink():
                raise OSError("symlink")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            log(f"wpis zgody nie zapisany: {exc!r}")
            return False

    def _approve_delivery(self, kind: str, run_id: str, sha: str, plan: str | None = None) -> tuple[int, dict]:
        """CP / READY: wpis w `.skillsync/runs/<run-id>/approvals.jsonl` z sha HEAD pokazanego na stronie. Sesja
        sprawdza wpis (słowo, sha = jej HEAD) zamiast słowa wpisanego w terminalu."""
        word = APPROVE_KINDS[kind]
        with self._approve_lock:
            current = self.status(plan)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind), None)
            if offer is None or offer["path"] != run_id:
                return 409, {"error": f"„{word}” nie jest teraz możliwe; odśwież stronę."}
            if offer["state"] != "waiting":
                return 409, {"error": f"„{word}” nieaktywne: {offer['reason']}."}
            if offer["sha"] != sha:
                return 409, {"error": "HEAD zmienił się od wyświetlenia; odśwież stronę i sprawdź zmiany."}
            d = current["delivery"]
            entry = self._entry(word, sha, run=run_id, plan=(current.get("plan") or {}).get("path"),
                                target=d.get("target") if kind == "cp" else None, branch=d.get("branch"),
                                ahead=d.get("ahead"), mode=d.get("mode"))
            if not self._append_approval(run_id, entry):
                return 500, {"error": "Nie udało się zapisać zgody; wpisz słowo w sesji."}
            log(f"zgoda „{word}” dla HEAD {sha[:10]} (przebieg {run_id})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": f"{RUNS_DIR}/{run_id}/{RUN_APPROVALS}", "next": NEXT_HINT}

    def ahead(self, branch: str | None, head: str | None) -> dict:
        """`git_ahead` w pamięci do zmiany gałęzi/HEAD albo AHEAD_CACHE_S sekund."""
        key = (branch, head)
        cached_key, ts, value = self._ahead
        if key != cached_key or time.time() - ts > AHEAD_CACHE_S:
            value = git_ahead(self.root, branch)
            self._ahead = (key, time.time(), value)
        return value

    def approvals_state(self, spec: dict, plan: dict | None, delivery: dict, run_id: str | None,
                        head_full: str | None, log: list[dict], waiting: list[dict]) -> dict:
        if not self.approvals:
            return {"enabled": False, "actions": [], "last": log[-1] if log else None}
        run_ok = bool(run_id and RUN_ID_RE.match(run_id) and (self.root / RUNS_DIR / run_id).is_dir())
        actions = consent_buttons(spec, plan, waiting, delivery.get("words", []), log, head_full, run_ok,
                                  self.cli_supports_approvals())
        for a in actions:
            if a["kind"] in ("cp", "ready"):
                a["path"] = run_id
        return {"enabled": True, "actions": actions, "last": log[-1] if log else None}

    def status(self, plan_rel: str | None = None) -> dict:
        plans = list_plans(self.root)
        chosen = None
        requested = plan_rel or self.default_plan
        if requested:
            chosen = next((p for p in plans if p["path"] == requested), None)
        auto = chosen is None
        if chosen is None:
            chosen = choose_plan(plans)
        specs = list_specs(self.root) if auto else []
        spec_act = choose_spec_act(plans, specs, chosen) if auto else None
        run_act = choose_run_act(self.root, plans, specs, chosen) if auto and not spec_act else None
        if spec_act or run_act:
            chosen = None

        plan_info: dict | None = None
        fields: dict[str, str] = {}
        phases: list[dict] = []
        if chosen:
            text = read_text(self.root / chosen["path"])
            title, fields = parse_header(text)
            phases = parse_progress(text)
            pr = PLAN_PR_RE.search(text)
            plan_info = {"path": chosen["path"], "title": title or chosen["title"],
                         "pr": {"number": int(pr.group("num")), "url": pr.group("url")} if pr else None,
                         "status": normalize_status(fields.get("status")), "statusText": clean_value(fields.get("status")),
                         "date": clean_value(fields.get("data")), "mtime": _iso(_mtime(self.root / chosen["path"])),
                         "fileSha": file_sha(self.root / chosen["path"])}

        spec_rel = spec_act["path"] if spec_act else first_token(fields.get("spec"))
        spec_path = resolve_doc(self.root, spec_rel) if spec_rel else None
        spec: dict = {"path": spec_rel, "exists": spec_path is not None, "sha": first_token(fields.get("spec-sha")),
                      "prototype": first_token(fields.get("prototyp")), "preparing": bool(run_act)}
        if spec_path:
            s_title, s_fields = parse_header(read_text(spec_path, 16_000))
            spec.update({"title": s_title, "status": normalize_status(s_fields.get("status")),
                         "statusText": clean_value(s_fields.get("status")),
                         "mtime": _iso(_mtime(spec_path)), "fileSha": file_sha(spec_path)})
            spec["prototype"] = spec["prototype"] or first_token(s_fields.get("prototyp"))
        spec["prototype"] = spec["prototype"] or find_prototype(self.root, spec_rel if spec_path else None)
        proto = spec.get("prototype")
        slug = proto.rstrip("/").rsplit("/", 1)[-1] if proto else None
        if slug and spec_path:
            spec["prototypeAcceptance"] = self.prototype_acceptance(slug)

        run_id = (run_act if run_act else spec_run_id(self.root, spec_rel, s_fields) if spec_act and spec_path
                  else first_token(fields.get("przebieg")))
        run: dict = {"id": run_id, "exists": False, "start": None, "checkpoints": [], "files": []}
        in_progress: list[str] | None = None
        reported: list[str] = []
        last_change = [_mtime(self.root / chosen["path"]) if chosen else None, _mtime(spec_path) if spec_path else None]
        if run_id and RUN_ID_RE.match(run_id):
            run_dir = self.root / RUNS_DIR / run_id
            if run_dir.is_dir():
                run["exists"] = True
                run["files"] = sorted(
                    rel for rel in (f"{RUNS_DIR}/{run_id}/{f.name}" for f in run_dir.glob("*.md"))
                    if resolve_doc(self.root, rel))
                cp = run_dir / "checkpoints.md"
                if cp.is_file():
                    start, items = parse_checkpoints(read_text(cp))
                    run["start"] = start
                    run["checkpoints"] = list(reversed(items))
                    last_change.append(_mtime(cp))
                    run["lastReportAt"] = _iso(_mtime(cp))
                    for item in items:
                        reported += [sid for sid in ids_reported_done(item["text"]) if sid not in reported]
                    for item in reversed(items):
                        ids = ids_in_progress(item["text"])
                        if ids is not None:
                            in_progress = ids
                            run["inProgressFrom"] = item["time"]
                            break
        start_dt = parse_iso(run["start"])
        run["startIso"] = start_dt.isoformat() if start_dt else None

        progress = build_progress(phases, in_progress, plan_info["status"] if plan_info else None, reported)
        spec_text = read_text(spec_path, 400_000) if spec_path else ""
        acts = compute_acts(spec, plan_info, progress, run["files"],
                            {"risky": risky_sections(spec_text)})
        waiting = self.waiting(run, spec if spec_path else None, plan_info, slug)
        git_info = read_git(self.root)
        delivery = read_delivery(self.root)
        ahead = self.ahead(git_info.get("branch"), git_info.get("head"))
        target = delivery_target(delivery, git_info.get("branch"), fields)
        delivery_state = {**delivery, "branch": git_info.get("branch"), "target": target, "remote": ahead.get("remote"),
                          "pushed": ahead.get("pushed"), "ahead": ahead.get("ahead"),
                          "words": delivery_words(delivery, ahead, plan_info, waiting, git_info.get("branch"), target)}
        current_docs = [spec_rel if spec_path else None, chosen["path"] if chosen else None]
        return {
            "app": {"name": "dashboard", "version": VERSION},
            "project": {"name": self.root.name, "path": str(self.root)},
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": self.version,
            "lastUpdate": _iso(max((t for t in last_change if t), default=None)),
            "git": git_info,
            "plans": [{k: v for k, v in p.items() if k != "mtime"} for p in plans],
            "planAuto": auto,
            "mode": "plan" if chosen else ("spec" if spec_act else "run" if run_act else "none"),
            "plan": plan_info,
            "spec": spec,
            "run": run,
            "progress": progress,
            "acts": acts,
            "waiting": waiting,
            # Cisza raportów: przeglądarka liczy minuty sama (strona nie dostaje zdarzeń, gdy nic się nie zmienia).
            "silence": ({"since": run.get("lastReportAt"), "thresholdMin": SILENCE_MIN}
                        if plan_info and plan_info.get("status") == "in_progress" and run.get("lastReportAt")
                        and not any(w.get("source") == "checkpoint" for w in waiting) else None),
            "approvals": self.approvals_state(spec, plan_info, delivery_state, run_id, git_info.get("headFull"),
                                              read_approvals(self.root, run_id), waiting),
            "delivery": delivery_state,
            "session": {"url": self.session_url},
            "prototype": {"slug": slug, "port": self.prototype_port, "up": bool(slug) and self.prototype_up()},
            "docs": self.doc_groups([d for d in current_docs if d], run["files"], run_id),
        }


# --------------------------------------------------------------------------- HTTP

def log(msg: str) -> None:
    print(f"[dashboard {datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = f"dashboard/{VERSION}"
    board: Board  # ustawiane w make_server
    verbose = False

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 (API stdlib)
        if self.verbose:
            log(f"{self.address_string()} {fmt % args}")

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                             "frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path
        try:
            if route in ("/", "/index.html"):
                return self._asset("index.html")
            if route.startswith("/assets/"):
                return self._asset(route[len("/assets/"):])
            if route == "/api/status":
                data = self.board.status((query.get("plan") or [None])[0])
                data["approvals"]["here"] = request_host(self.headers.get("Host")) in self.board.approve_hosts
                return self._json(200, data)
            if route == "/api/doc":
                doc = self.board.doc((query.get("path") or [None])[0])
                if doc is None:
                    return self._json(404, {"error": "Dokument niedostępny (spoza listy dozwolonych albo nie istnieje)."})
                return self._json(200, doc)
            if route == "/events":
                return self._events()
            if route == "/api/descriptions":
                return self._json(200, load_descriptions())
            if route == "/healthz":
                return self._json(200, {"ok": True, "version": self.board.version})
            return self._json(404, {"error": "Nie ma takiej strony."})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            log(f"błąd {route}: {exc!r}")
            try:
                return self._json(500, {"error": "Błąd serwera statusu; szczegóły w konsoli."})
            except OSError:
                return None

    def _approve_allowed(self) -> str | None:
        """Powód odmowy zapisu albo None: Host loopback (także ochrona przed DNS rebinding i tunelem), własny nagłówek
        (wymusza preflight CORS, którego serwer nie obsługuje) i zgodny Origin, jeśli przeglądarka go wysłała."""
        allowed = self.board.approve_hosts
        host = request_host(self.headers.get("Host"))
        if host not in allowed:
            return ("Zgody tylko z localhost albo hosta z --approve-host; pod tym adresem strona jest tylko do odczytu.")
        if self.headers.get("X-Status-Board") != "1":
            return "Brak nagłówka X-Status-Board."
        origin = self.headers.get("Origin")
        if origin and request_host(urlparse(origin).netloc) not in allowed:
            return "Obcy Origin."
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return "Oczekiwano application/json."
        return None

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            if route != "/api/approve":
                return self._json(404, {"error": "Nie ma takiej strony."})
            refused = self._approve_allowed()
            if refused:
                return self._json(403, {"error": refused})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._json(400, {"error": "Nieprawidłowa długość żądania."})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                return self._json(400, {"error": "Nieprawidłowe żądanie."})
            code, data = self.board.approve(body.get("kind"), body.get("path"), body.get("sha"), body.get("plan"))
            return self._json(code, data)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Nieprawidłowe żądanie."})
        except Exception as exc:
            log(f"błąd {route}: {exc!r}")
            return self._json(500, {"error": "Błąd serwera statusu; szczegóły w konsoli."})

    def _asset(self, name: str) -> None:
        if not re.fullmatch(r"[\w-]+\.(html|css|js|svg)", name):
            return self._json(404, {"error": "Nie ma takiego zasobu."})
        path = ASSETS_DIR / name
        if not path.is_file():
            return self._json(404, {"error": "Nie ma takiego zasobu."})
        self._send(200, path.read_bytes(), ASSET_TYPES[path.suffix])

    def _events(self) -> None:
        board = self.board
        with board.cond:
            if board.sse_clients >= MAX_SSE_CLIENTS:
                return self._json(503, {"error": "Za dużo podłączonych przeglądarek."})
            board.sse_clients += 1
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._common_headers()
            self.end_headers()
            seen = board.version
            self._sse("hello", {"version": seen, "heartbeat": board.heartbeat}, retry=3000)
            while not board.stop.is_set():
                with board.cond:
                    board.cond.wait_for(lambda: board.version != seen or board.stop.is_set(), timeout=board.heartbeat)
                    current = board.version
                if board.stop.is_set():
                    break
                if current != seen:
                    seen = current
                    self._sse("change", {"version": current})
                else:
                    self._sse("ping", {"version": current, "t": int(time.time())})
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            with board.cond:
                board.sse_clients -= 1
            self.close_connection = True

    def _sse(self, event: str, data: dict, retry: int | None = None) -> None:
        payload = ""
        if retry:
            payload += f"retry: {retry}\n"
        payload += f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def lan_addresses() -> list[str]:
    """Adresy IPv4 tej maszyny do wyświetlenia (bez wysyłania pakietów: connect() na UDP tylko wybiera trasę)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1, nic nie jest wysyłane
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found or ["<adres-tej-maszyny>"]


def validate_port(port: int) -> None:
    if port in FORBIDDEN_PORTS or not PORT_MIN <= port <= PORT_MAX:
        raise SystemExit(f"Port {port} poza dozwolonym zakresem {PORT_MIN}–{PORT_MAX} (zabronione: 4444–4448, 9222, 9333).")


def make_server(board: Board, host: str, port: int, verbose: bool = False) -> Server:
    validate_port(port)
    handler = type("BoundHandler", (Handler,), {"board": board, "verbose": verbose})
    return Server((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Strona statusu przebiegu skilla feature (na żywo; jedyny zapis: przyciski zgód GO plan / GO implement).")
    ap.add_argument("--project", required=True, help="katalog projektu (z docs/plans)")
    ap.add_argument("--plan", help="plan względem projektu, np. docs/plans/2026-11-09-x.md (domyślnie: bieżący)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (domyślnie {DEFAULT_PORT}, zakres {PORT_MIN}–{PORT_MAX})")
    ap.add_argument("--host", default="127.0.0.1",
                    help="adres nasłuchu (domyślnie 127.0.0.1, dalej przez tunel; 0.0.0.0 = wszystkie interfejsy, np. podgląd z telefonu w zaufanej sieci)")
    ap.add_argument("--poll", type=float, default=1.0, help="co ile sekund sprawdzać zmiany plików")
    ap.add_argument("--json", action="store_true", help="wypisz /api/status i zakończ")
    ap.add_argument("--verbose", action="store_true", help="loguj każde żądanie na stderr")
    ap.add_argument("--session-url", help="https adres sesji (np. Remote Control); przycisk „Otwórz sesję” obok kopiowania słowa")
    ap.add_argument("--approve-host", action="append", default=[], metavar="NAZWA",
                    help="dodatkowa nazwa hosta (np. nazwa hosta albo IP w LAN), z której wolno dawać zgody; domyślnie tylko localhost")
    ap.add_argument("--no-approvals", action="store_true",
                    help="bez przycisków GO plan / GO implement (strona wyłącznie do odczytu)")
    args = ap.parse_args(argv)
    if args.session_url and not session_url_ok(args.session_url):
        raise SystemExit("--session-url: tylko adres https bez danych logowania.")

    root = Path(args.project).expanduser()
    if not root.is_dir():
        raise SystemExit(f"Brak katalogu projektu: {root}")
    if args.plan and resolve_doc(root, args.plan.replace(os.sep, "/").removeprefix("./")) is None:
        raise SystemExit(f"Plan {args.plan} nie istnieje albo leży poza docs/plans.")
    plan = args.plan.replace(os.sep, "/").removeprefix("./") if args.plan else None
    board = Board(root, plan, poll=max(0.2, args.poll), approvals=not args.no_approvals, session_url=args.session_url,
                  approve_hosts=tuple(args.approve_host))
    if args.json:
        print(json.dumps(board.status(), ensure_ascii=False, indent=2))
        return 0
    server = make_server(board, args.host, args.port, args.verbose)
    threading.Thread(target=board.watch_loop, name="watcher", daemon=True).start()
    log(f"projekt {board.root} → http://{args.host}:{args.port}/  (Ctrl+C kończy)")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log(f"UWAGA: nasłuch na {args.host} — strona (tylko odczyt) jest widoczna dla każdego w tej sieci; używaj w zaufanej sieci.")
        for ip in lan_addresses() if args.host in ("0.0.0.0", "::", "") else [args.host]:
            log(f"  z innego urządzenia: http://{ip}:{args.port}/")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        board.stop.set()
        with board.cond:
            board.cond.notify_all()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
