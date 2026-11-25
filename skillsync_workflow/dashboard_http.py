"""Dashboard HTTP server (`skillsync dashboard serve`): a live presentation over the same JSON as `dashboard show`.

The state of every `/api/status` answer is `dashboard.build_status` (contract `schema: 1`, `docs/dashboard-contract.md`);
this module only adds the live parts: a file watcher with a version counter for SSE (`/events`: `hello`, `change`, `ping`),
the Markdown preview `/api/doc`, the page assets and `descriptions.json` of the `dashboard` skill (read from the active
layers through `LayerSet.skill_files`), and the consent buttons `POST /api/approve`.

Every endpoint (GET, HEAD, SSE and POST) answers only to a `Host` header from loopback (`localhost`, `127.0.0.1`, `::1`,
any port), from `--approve-host` or from `--read-host`; another `Host` (DNS rebinding, an unlisted tunnel or LAN name) = 403
without content. Three Host classes: loopback and `--approve-host` read and write; `--read-host` (viewers through a tunnel)
reads everything and never writes (`POST /api/approve` = 403 with the same refusal as a foreign Host).

The only write: `POST /api/approve`, in-process through the shared core functions of `status.py` (no `skillsync`
subprocess): "GO plan"/"GO implement" = `set_doc_status(..., expect_sha=<shown sha>)` and then, when the run directory
exists, `record_approval(..., source='dashboard')`; "CP"/"READY" = `record_approval(..., source='dashboard')` into
`.skillsync/runs/<run-id>/approvals.jsonl`. Header `X-Status-Board: 1`, matching `Origin`, `Content-Type:
application/json`, body <= 4096 bytes; `--no-approvals` turns it off. Standard library only.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import html
import json
import os
import posixpath
import re
import secrets
import signal
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import __version__
from . import dashboard as engine
from . import dashboard_history
from . import dashboard_listen
from . import dashboard_ports as ports
from . import dashboard_tests
from . import dashboard_worktrees
from . import feedback
from . import dashboard_token
from . import i18n
from . import test_events
from . import stopsignal
from . import status as status_core
from .common import emit, file_lines, load_layer_project, read_project, redact
from .layers import LayerError
from .prototype_server import LingeringCloseMixin

HEADER_RE = engine.HEADER_RE
is_allowed_rel = engine.is_allowed_rel

DEFAULT_PORT = engine.DEFAULT_PORT
PORT_MIN, PORT_MAX = engine.PORT_MIN, engine.PORT_MAX
FORBIDDEN_PORTS = engine.FORBIDDEN_PORTS
SKILL = 'dashboard'
ASSETS_PREFIX = 'assets/'
DESCRIPTIONS_FILE = 'descriptions.json'
ASSET_NAME_RE = re.compile(r'[\w-]+\.(html|css|js|svg)')
ASSET_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
}
MAX_ASSET_BYTES = 2_000_000
MAX_DOC_BYTES = engine.MAX_DOC_BYTES
MAX_SSE_CLIENTS = 64
# Two-port mode: each socket has its own share of the streams, so devices that fill the read socket cannot take the live refresh of the page that
# gives consents (the single-port mode keeps the one pool of 64).
FEEDBACK_DRAFT_RE = re.compile(r"F[1-9][0-9]{0,5}")
FEEDBACK_SHA_RE = re.compile(r"[0-9a-f]{64}")
SSE_ROLE_LIMITS = {ports.ROLE_READ: 48, ports.ROLE_APPROVE: 16}
MAX_BODY = 4096
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
APPROVE_KINDS = engine.APPROVE_KINDS
RUNS_DIR = engine.RUNS_DIR
RUN_APPROVALS = engine.APPROVALS_FILE  # .skillsync/runs/<run-id>/approvals.jsonl, the format `skillsync status` reads
MANIFEST = '.github/.skillsync.yaml'
PROTO_CACHE_S = 30
AHEAD_CACHE_S = 10
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HOST_REFUSED = i18n.t("pl", "srv.http.hostRefused")   # a write from a host that may only read (the reason of `_approve_allowed`); the Polish text, `_approve_allowed` translates it per project


def host_refused_message(role: str, host_header: str | None, lang: str = "pl") -> str:
    """The 403 text of a `Host` outside the list of THIS socket: names the flag that opens it for the socket and the `Host` of the request.
    The name is cleaned before it goes into the answer (letters, digits, dot, colon, dash, underscore; everything else `?`; at most 64 characters),
    so it cannot carry a control character, markup or a log injection; the answer is JSON with no page content."""
    shown = re.sub(r"[^A-Za-z0-9._:-]", "?", request_host(host_header))
    shown = (shown[:61] + "...") if len(shown) > 64 else (shown or i18n.t(lang, "srv.http.hostNone"))
    if role == ports.ROLE_READ:
        return i18n.t(lang, "srv.http.hostRead", shown=shown)
    if role == ports.ROLE_APPROVE:
        return i18n.t(lang, "srv.http.hostApprove", shown=shown)
    return i18n.t(lang, "srv.http.hostBoth", shown=shown)
TOKEN_HEADER = "X-Approve-Token"
TAB_ROUTES = frozenset({"/api/status", "/api/doc", "/api/tests", "/events"})   # the GET routes that take `?tab=<id>`
PROXY_REFUSED = i18n.t("pl", "srv.http.proxyRefused")   # the Polish texts of the refusals and the hint; a project in another language gets them from the catalog
TOKEN_REFUSED = i18n.t("pl", "srv.http.tokenRefused")
NEXT_HINT = i18n.t("pl", "srv.http.nextHint")


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
    for _ in range(3):  # nested fragments (a link with code in its label)
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
    """Simple renderer: headings, paragraphs, lists (nested, checkboxes), tables, code, quotes, links.

    Raw HTML of the source is always escaped (the preview never executes document content).
    """
    base_dir = posixpath.dirname(doc_path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            # a block of `Key: value` fields (e.g. a plan header): every line on its own
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


# --------------------------------------------------------------------------- helpers

RESULT_VISIBLE_LINES = 12
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")


def split_result(text: str, visible: int = RESULT_VISIBLE_LINES) -> tuple[str, str, int]:
    """`(head, rest, rest_lines)` of the `## Wynik` text for the collapsed view: the cut falls only BETWEEN Markdown blocks, never inside a
    table, a list (also a loose one), a fenced code block or a paragraph. A cut point is the line that starts a block: it follows a blank
    line (or is a heading), the cut is not inside a fence, and the line is not a list item that continues a list. Rule: the last cut point
    at or before `visible` lines whose head holds more than headings (so the head is never only a title), else the first cut point after them
    (a long table or list stays whole); no cut point at all = no `rest`. Text of `visible` lines or fewer is not cut."""
    lines = file_lines(text)   # CRLF, LF and a lone CR only: LS, PS, NEL and the like stay inside the line (see the guard of the line ends)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) <= visible:
        return "\n".join(lines), "", 0
    in_fence, fence, safe = False, "", []
    last_filled = ""   # the last non-blank line before the current one (to tell a list continuation from a new block)
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if i > 0 and not in_fence and line.strip() and (not lines[i - 1].strip() or _HEADING.match(line)):
            continues_list = bool(_LIST_ITEM.match(line)) and bool(_LIST_ITEM.match(last_filled) or last_filled[:1] in (" ", "\t"))
            if not continues_list:
                safe.append(i)
        if match:
            marker = match.group("fence")[0]
            if not in_fence:
                in_fence, fence = True, marker
            elif marker == fence:
                in_fence = False
        if line.strip():
            last_filled = line
    meaningful = [i for i in safe if any(x.strip() and not _HEADING.match(x) for x in lines[:i])]
    before = [i for i in meaningful if i <= visible]
    after = [i for i in safe if i > visible]
    cut = before[-1] if before else (after[0] if after else None)
    if cut is None:
        return "\n".join(lines), "", 0
    return "\n".join(lines[:cut]).rstrip(), "\n".join(lines[cut:]), len(lines) - cut


def result_html(result: dict) -> dict:
    """The `result` of the status plus `html` (the visible part), `moreHtml` and `moreLines` (the collapsed rest), rendered by the same
    safe `render_markdown` as `/api/doc` (raw HTML escaped, only safe link schemes). The text was masked as a whole when the plan was read
    (`engine.read_masked`), before any cut; `redact` of the whole answer stays the second layer. `text` and `path` stay as they were."""
    head, rest, rest_lines = split_result(result["text"])
    path = result.get("path") or ""
    answer = dict(result, html=render_markdown(head, path))
    if rest:
        answer["moreHtml"] = render_markdown(rest, path)
        answer["moreLines"] = rest_lines
    return answer


# Inline Markdown of single-line texts of the status page (step titles, "waiting for" items, expert questions): the same safe inline renderer
# as the document view, no blocks. A field `<name>Html` is added only when the text really has formatting (otherwise the plain text is shown).
_INLINE_SPANS = re.compile(r"(`+).+?\1|!?\[[^\]]*\]\([^)]*\)|\*\*.+?\*\*|__.+?__|~~.+?~~|<https?://[^>\s]+>")


def cut_inline(text: str, limit: int) -> str:
    """`text` cut to `limit` characters (+ an ellipsis) so that the cut never falls inside a code span, a link, a bold or strike-through
    span: a span the limit would cut is dropped whole. The cut is made on the SOURCE, before rendering, so it can never cut a tag."""
    if len(text) <= limit:
        return text
    cut = limit
    for span in _INLINE_SPANS.finditer(text):
        if span.start() < limit < span.end():
            cut = span.start()
            break
    return text[:cut].rstrip() + "…"


def inline_html(text: object, limit: int = 0) -> str | None:
    """Safe inline HTML of a one-line text, or None when it has no formatting (the client then shows the plain text)."""
    if not isinstance(text, str) or not text.strip():
        return None
    source = " ".join(text.split())
    if limit:
        source = cut_inline(source, limit)
    rendered = render_inline(source)
    return rendered if rendered != html.escape(source, quote=False) else None


# The feed of checkpoints highlights step ids and key phrases (the same patterns as the page's `decorateCheckpoint`, ASCII word rules like JS).
_FEED_ID = re.compile(r"(?<![\w-])([A-Z]{1,3}-?\d+[a-z]?)(?![\w-])", re.ASCII)
_FEED_PHRASE = re.compile(r"\b(zrobione|w toku|ryzyko|PASS|FAIL|BLOCKED|restart sesji)\b", re.ASCII | re.IGNORECASE)
_TAG = re.compile(r"(<[^>]*>)")


def decorate_feed_html(rendered: str) -> str:
    """`rendered` (the escaped HTML of `render_inline`) with step ids in `<span class="sidc">` and phrases in `<span class="ph">`, only in the TEXT
    segments between tags and never inside `<code>` or `<a>` (nor in attributes: a tag is never touched). The segments are already escaped, and
    the matches are ASCII letters, digits and spaces, so nothing is injected."""
    out, depth = [], 0
    for part in _TAG.split(rendered):
        if part.startswith("<") and part.endswith(">"):
            name = part[1:].lstrip("/").split(None, 1)[0].rstrip(">").lower() if len(part) > 2 else ""
            if name in ("code", "a"):
                depth = max(0, depth - 1) if part.startswith("</") else depth + 1
            out.append(part)
        elif depth or not part:
            out.append(part)
        else:
            pieces, last = [], 0
            for found in _FEED_ID.finditer(part):
                pieces.append(_decorate_phrases(part[last:found.start()]))
                pieces.append(f'<span class="sidc">{found.group(1)}</span>')
                last = found.end()
            pieces.append(_decorate_phrases(part[last:]))
            out.append("".join(pieces))
    return "".join(out)


def _decorate_phrases(text: str) -> str:
    return _FEED_PHRASE.sub(lambda found: f'<span class="ph">{found.group(1)}</span>', text)


def add_inline_html(data: dict) -> dict:
    """Additive fields of `/api/status` (`schema` stays 1): `titleHtml` of the plan steps, of `substeps` and of `progress.current`,
    `textHtml` of `run.checkpoints[]` (the feed), `whatHtml` and `actionHtml` of `waiting[]` (the action text repeats the item), `textHtml` of `expertQuestions.items[]` and `expertQuestions.current.items[]` (cut to the same 160 characters as the plain text). The texts were
    masked when the plan and the checkpoints were read; `text`, `title`, `what` stay as they were (tooltips and `title`/`aria` use them)."""
    def put(item: object, source: str, out: str, limit: int = 0) -> None:
        if isinstance(item, dict):
            rendered = inline_html(item.get(source), limit)
            if rendered:
                item[out] = rendered

    progress = data.get("progress")
    if isinstance(progress, dict):
        for phase in progress.get("phases") or []:
            for step in (phase.get("steps") or []) if isinstance(phase, dict) else []:
                put(step, "title", "titleHtml")
        put(progress.get("current"), "title", "titleHtml")
    run = data.get("run")
    for item in (run.get("checkpoints") or []) if isinstance(run, dict) else []:
        put(item, "text", "textHtml")
        if isinstance(item, dict) and isinstance(item.get("textHtml"), str):
            item["textHtml"] = decorate_feed_html(item["textHtml"])
    for item in data.get("substeps") or []:
        put(item, "title", "titleHtml")
    for item in data.get("waiting") or []:
        put(item, "what", "whatHtml")
        put(item, "action", "actionHtml")
    questions = data.get("expertQuestions")
    current = questions.get("current") if isinstance(questions, dict) else None
    for source in (questions, current):
        for item in (source.get("items") or []) if isinstance(source, dict) else []:
            put(item, "text", "textHtml", 160)
    return data


def log(msg: str) -> None:
    print(f"[dashboard {datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def session_url_ok(url: str | None) -> bool:
    """Session address given at start: https only, no credentials in the address."""
    if not url:
        return False
    u = urlparse(url)
    return u.scheme == "https" and bool(u.hostname) and not u.username and not u.password and len(url) <= 500


def request_host(host_header: str | None) -> str:
    """Host name of a `Host` header (no port, no IPv6 brackets), lower case."""
    raw = (host_header or "").strip().lower()
    if raw.startswith("["):
        return raw[1:raw.find("]")] if "]" in raw else ""
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def project_manifest(root: Path, fallback: dict | None = None) -> dict:
    """The project manifest read now (delivery mode may change while the server runs); unreadable = `fallback` or {}."""
    try:
        return read_project(root)[1]
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return fallback if fallback is not None else {}


PAGE_TITLE = "<title>{}</title>".format(i18n.t("pl", "ui.page.title")).encode("utf-8")


def page_with_language(body: bytes, lang: str) -> bytes:
    """The page `index.html` with `<html lang="…">` of the project language. The asset carries the static default `lang="pl"` (it is
    served as it is for `pl`, byte for byte); a project in another supported language gets its code instead (the first and only
    occurrence) and the `<title>` its text from the catalog (`ui.page.title`; the heading "Wczytuję status…" stays until the first status), and `app.js` keeps it in step with the `lang` of the status afterwards."""
    if lang == i18n.DEFAULT_LANGUAGE or lang not in i18n.SUPPORTED_LANGUAGES:
        return body
    body = body.replace(b'<html lang="pl">', f'<html lang="{lang}">'.encode("ascii"), 1)
    title = html.escape(i18n.t(lang, "ui.page.title"), quote=False).encode("utf-8")   # the `<title>` is static until the first status: only the Polish default is replaced
    return body.replace(PAGE_TITLE, b"<title>" + title + b"</title>", 1)


def skill_assets(layerset) -> tuple[dict[str, Path], Path | None]:
    """({asset name: Path}, descriptions.json Path) of the `dashboard` skill in the active layers: `LayerSet.skill_files`
    (per file the later layer wins, links skipped), the same rule the generator and `prototype` assets use. Only names of
    the asset allowlist (`[\\w-]+.(html|css|js|svg)` directly in `assets/`) are served."""
    files = layerset.skill_files(SKILL)
    assets = {}
    for rel, (_layer, path) in files.items():
        name = rel[len(ASSETS_PREFIX):] if rel.startswith(ASSETS_PREFIX) else ""
        if ASSET_NAME_RE.fullmatch(name):
            assets[name] = path
    found = files.get(DESCRIPTIONS_FILE)
    return assets, found[1] if found else None


def descriptions_path(path: Path | None, lang: str) -> Path | None:
    """The descriptions file for the project language: `descriptions.<lang>.json` next to `descriptions.json` (the same layer) for a
    supported language other than the default; a missing, non-regular or linked file = the Polish `descriptions.json`."""
    if path is None or lang == i18n.DEFAULT_LANGUAGE or lang not in i18n.SUPPORTED_LANGUAGES:
        return path
    sibling = path.with_name(f"descriptions.{lang}.json")
    try:
        if sibling.is_file() and not sibling.is_symlink():
            return sibling
    except OSError:
        pass
    return path


def load_descriptions(path: Path | None, lang: str = i18n.DEFAULT_LANGUAGE) -> dict:
    """Static descriptions of steps, acts and gates (tooltips): `descriptions.json` of the skill, `{key: {title, text}}`;
    for a project in another supported language its `descriptions.<lang>.json` when present (fallback: the Polish file)."""
    path = descriptions_path(path, lang)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("text"), str)}


# --------------------------------------------------------------------------- live state

class Board:
    """Server state: project, file watcher and the version counter for SSE. The status itself is `build_status`."""

    def __init__(self, root: Path, default_plan: str | None = None, poll: float = 1.0,
                 heartbeat: float = 15.0, prototype_port: int = engine.PROTOTYPE_PORT,
                 approvals: bool = True, session_url: str | None = None, approve_hosts: tuple[str, ...] = (),
                 read_hosts: tuple[str, ...] = (),
                 assets: dict[str, Path] | None = None, descriptions: Path | None = None, manifest: dict | None = None,
                 approve_token: str | None = None, probe_prototype: bool = True):
        self.root = Path(root).resolve()
        self.approvals = approvals
        # Per process, never in a status, asset, log or error report (see `dashboard_token`); None = no consents at all.
        self.approve_token = (approve_token or dashboard_token.new_token()) if approvals else None
        self._explicit_token = bool(approve_token) and approvals   # a token given by the caller is kept by a `TabRegistry` (the bare form of the single tab)
        self._tabs: TabRegistry | None = None   # the registry that wraps this board for `make_server` (one per board, so both sockets share it)
        self.session_url = session_url if session_url_ok(session_url) else None
        self.assets = dict(assets or {})
        self.descriptions = descriptions
        self._manifest = manifest
        self._approve_lock = threading.Lock()
        self.token_failures = ports.TokenFailures()
        self._ahead: tuple[tuple, float, dict] = ((), 0.0, {})
        self.approve_hosts = LOOPBACK_HOSTS | {h.strip().lower() for h in approve_hosts if h.strip()}
        # Read-only Host names (viewers through a tunnel): every read, never a write. A name that is also loopback or in
        # --approve-host keeps writing: the request cannot tell the presenter from a viewer under the same Host.
        self.read_hosts = frozenset(h.strip().lower() for h in read_hosts if h.strip())
        self.default_plan = default_plan
        self.poll = poll
        self.heartbeat = heartbeat
        self.prototype_port = prototype_port
        self.version = 0
        self.changed_at = time.time()
        self.cond = threading.Condition()
        self.stop = threading.Event()
        # The counters of the SSE streams belong to the SERVER: a board of another tab counts into `sse_owner` (the default board, which every tab of a
        # registry points to), so the limits are global. `sse_local` counts the streams of this board only (the observer watches a board that has some).
        self.sse_clients = 0
        self.sse_by_role: dict[str, int] = {}   # streams of the two-port sockets, by role (`sse_clients` stays the total)
        self.sse_owner: Board = self
        self.sse_local = 0
        self.clock = time.monotonic   # the clock of `touch`; the registry gives its boards the clock of the observer (one clock for "asked for" and "watched")
        self.last_request: float | None = None   # `clock()` of the last request that asked for this tab (`touch`)
        self.next_check = 0.0   # the observer checks this board's signature when its clock passes this
        self.probe_prototype = probe_prototype   # False: the manifest of the tab names no `prototype.port`, so there is no prototype to look for
        self._store: tuple[float, Path] | None = None
        self._proto = (0.0, False)
        self._accept: tuple[tuple, float, dict] = ((), 0.0, {})
        self._sig = self.signature()

    def manifest(self) -> dict:
        self._manifest = project_manifest(self.root, self._manifest)
        return self._manifest

    def lang(self, fresh: bool = False) -> str:
        """The language of the project (`i18n.resolve_language` of its manifest) for the texts of the answers. The cached manifest serves
        the routes that anyone may reach (a refusal does not read the file); `fresh` re-reads it (the consent paths)."""
        if fresh or self._manifest is None:
            self.manifest()
        return i18n.resolve_language(self._manifest)[0]

    # --- watcher
    def touch(self, now: float | None = None) -> None:
        """A request asked for this tab: the observer keeps its signature fresh for `WATCH_GRACE_S` seconds."""
        self.last_request = self.clock() if now is None else now

    def watched_paths(self) -> list[Path]:
        paths: list[Path] = [self.root / MANIFEST]
        for d in engine.DOC_DIRS:
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
                    paths.append(run / "agents.jsonl")   # the journal of sub-mind runs and review rounds (N16): a new line refreshes the page
                    tests = run / "tests"   # the event files of the test runs (180-A): a new line refreshes the page
                    if tests.is_dir() and not tests.is_symlink():
                        paths.append(tests)
                        for step in list(tests.iterdir())[:200]:
                            if step.is_dir() and not step.is_symlink():
                                paths.append(step)
                                paths.extend(sorted((f for f in step.glob("*.jsonl") if f.stem.isascii() and f.stem.isdecimal()), key=lambda f: int(f.stem))[-10:])
        protos = self.root / engine.PROTOTYPES_DIR
        if protos.is_dir():
            paths.extend(protos.glob("*/prototype.json"))
            paths.extend(protos.glob("*/comments.json"))
            paths.extend(protos.glob("*/questions.json"))
            paths.extend(protos.glob("*/.writing.json"))
        paths.extend(self._feedback_paths())
        git, common = self._git_dirs()
        paths.extend([git / "HEAD", common / "packed-refs"])
        branch = self._branch(git)
        if branch:
            paths.append(common / "refs/heads" / branch)
        # Push and fetch change origin refs: the count of commits to push (CP) refreshes without project file changes.
        base = engine.read_delivery(self.manifest()).get("base")
        for name in {branch, base, "HEAD", "main", "master"} - {None}:
            paths.append(common / "refs/remotes/origin" / name)
        return paths

    def _git_dirs(self) -> tuple[Path, Path]:
        """`(git dir, common dir)` of the checkout from `.git` files (`engine.git_locations`): for a linked worktree `.git` is a FILE naming its own git
        directory (HEAD) and the common one (branch refs, packed-refs, origin refs). Git is not run; an unreadable `.git` is the plain `<root>/.git`."""
        try:
            found = engine.git_locations(self.root)
        except (OSError, ValueError):
            found = None
        if found is None:
            return self.root / ".git", self.root / ".git"
        return found[0], found[1]

    @staticmethod
    def _branch(git: Path) -> str | None:
        try:
            head = engine.read_text(git / "HEAD", 4096).strip()
        except (OSError, ValueError):
            return None
        return head[4:].strip().removeprefix("refs/heads/") or None if head.startswith("ref:") else None

    FEEDBACK_WATCH_LIMIT = 200
    STORE_TTL_S = 30.0

    def _feedback_store(self) -> Path:
        """The feedback store the watcher looks at, from `.git` files only (`dashboard_worktrees.feedback_store_root`; no `git`, so a tick starts no process),
        kept `STORE_TTL_S` seconds. What is not a git project (or a submodule, a bare repository) watches `<root>/.skillsync/feedback`, which is
        also what `feedback.store_root` answers there. The write paths keep `feedback.store_root`."""
        now = time.monotonic()
        if self._store is not None and now - self._store[0] < self.STORE_TTL_S:
            return self._store[1]
        found = dashboard_worktrees.feedback_store_root(self.root)
        store = Path(found) if found else self.root.joinpath(*dashboard_worktrees.STORE_DIR)
        self._store = (now, store)
        return store

    def _feedback_paths(self) -> list[Path]:
        """The feedback store of the MAIN checkout (a card works in a worktree, the page in the main one): the consents file (also before it exists) and the newest
        draft, preview and result files, so a new preview, a consent or a send record refreshes the page. A missing store is no error."""
        store = self._feedback_store()
        paths = [store / feedback.APPROVALS_FILE]
        try:
            if store.is_dir() and not store.is_symlink():
                pattern = re.compile(r"F([1-9][0-9]{0,5})[.](?:json|preview[.]json|sent[^/]*[.]json|sending[.]json)")
                names = [(int(m.group(1)), name) for name in os.listdir(store) if (m := pattern.fullmatch(name))]
                paths.extend(store / name for _, name in sorted(names, reverse=True)[:self.FEEDBACK_WATCH_LIMIT])
        except OSError:
            pass
        return paths

    def signature(self) -> tuple:
        sig = []
        for p in self.watched_paths():
            try:
                st = p.stat()
                sig.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        # A writing marker expires without any file change: its validity is part of the signature, so the page refreshes then too.
        protos = self.root / engine.PROTOTYPES_DIR
        if protos.is_dir():
            for marker in protos.glob("*/.writing.json"):
                valid = engine.prototype_writing(self.root, marker.parent.name) is not None
                sig.append((f"writing:{marker.parent.name}", int(valid), 0))
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
            except Exception as exc:  # the watcher must not die
                log(f"obserwator: {exc!r}")

    # --- probes with caches (the engine calls them)
    def prototype_up(self) -> bool:
        if not self.probe_prototype:   # no `prototype.port` in the manifest of this tab: nothing to probe (never the port of another tab)
            return False
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

    def _accept_table(self) -> dict:
        """The in-memory table of the prototype readers: valid until `comments.json`/`index.html` of the prototypes change or for
        PROTO_CACHE_S seconds."""
        base = self.root / engine.PROTOTYPES_DIR
        key = tuple(sorted((str(f), engine._mtime(f)) for pat in ("*/comments.json", "*/index.html") for f in base.glob(pat)))
        cached_key, ts, table = self._accept
        if key != cached_key or time.time() - ts > PROTO_CACHE_S:
            table = {}
            self._accept = (key, time.time(), table)
        return table

    def prototype_acceptance(self, slug: str) -> dict | None:
        """Screen acceptance of a prototype (`dashboard.prototype_acceptance`, in-process, read-only), cached (`_accept_table`)."""
        table = self._accept_table()
        if slug not in table:
            table[slug] = engine.prototype_acceptance(self.root, slug)
        return table[slug]

    def prototype_overview(self, slug: str) -> dict | None:
        """`dashboard.prototype_overview` (acceptance, threads, comments after acceptance), cached like the acceptance."""
        table = self._accept_table()
        key = ("overview", slug)
        if key not in table:
            table[key] = engine.prototype_overview(self.root, slug)
        return table[key]

    def ahead(self, branch: str | None, head: str | None) -> dict:
        """`git_ahead` kept in memory until the branch/HEAD changes or for AHEAD_CACHE_S seconds."""
        key = (branch, head)
        cached_key, ts, value = self._ahead
        if key != cached_key or time.time() - ts > AHEAD_CACHE_S:
            value = engine.git_ahead(self.root, branch)
            self._ahead = (key, time.time(), value)
        return value

    # --- status (the same JSON as `dashboard show`, plus the live state)
    def status(self, plan_rel: str | None = None) -> dict:
        data = self._status(plan_rel)
        if isinstance(data.get("result"), dict) and isinstance(data["result"].get("text"), str):
            data["result"] = result_html(data["result"])
        return add_inline_html(data)

    def light_waiting(self, head12: str | None = None) -> bool:
        """"Does the session of this tab wait for the user", from the FILES only (no `git`, no `build_status`): the plan the status would pick, its `czeka na ...`
        status text, the newest `czeka:` of its checkpoints (`engine.waiting_items`), minus what `approvals.jsonl` of the run already holds (GO plan / GO implement
        given at all, CP / READY given for the HEAD of the tab). The light counterpart of `status()["waiting"]` for `worktrees[]`: it leaves out the waiting that
        needs the prototype or the questions of an expert, so it may say False where the status says True, never the other way round after a consent."""
        root = self.root
        plans = engine.list_plans(root)
        chosen = engine.choose_plan(plans)
        specs = engine.list_specs(root)
        spec_act = engine.choose_spec_act(plans, specs, chosen)
        run_act = engine.choose_run_act(root, plans, specs, chosen) if not spec_act else None
        if spec_act or run_act:
            chosen = None
        plan_info, fields = None, {}
        if chosen:
            text = engine.read_masked(root / chosen["path"], engine.HEADER_BYTES)
            _title, fields = engine.parse_header(text)
            status, status_text = engine.doc_status(text)
            plan_info = {"path": chosen["path"], "status": status, "statusText": status_text}
        spec_rel = spec_act["path"] if spec_act else engine.first_token(fields.get("spec"))
        spec_path = engine.resolve_doc(root, spec_rel) if spec_rel else None
        spec, spec_fields = None, {}
        if spec_path:
            spec_text = engine.read_masked(spec_path, engine.HEADER_BYTES)
            _title, spec_fields = engine.parse_header(spec_text)
            status, status_text = engine.doc_status(spec_text)
            spec = {"path": spec_rel, "status": status, "statusText": status_text}
        run_id = (run_act if run_act else engine.spec_run_id(root, spec_rel, spec_fields) if spec_act and spec_path
                  else engine.first_token(fields.get("przebieg")))
        run: dict = {"checkpoints": []}
        directory = engine.run_dir(root, run_id)
        if directory is not None:
            checkpoints = directory / "checkpoints.md"
            if checkpoints.is_file() and not checkpoints.is_symlink():
                _start, chronological = engine.parse_checkpoints(engine.read_masked(checkpoints))
                run["checkpoints"] = list(reversed(chronological))
        items = engine.waiting_items(run, spec, plan_info, None)
        if not items:
            return False
        given = engine.run_approvals(root, run_id) if directory is not None else []
        for item in items:
            word = str(item.get("word") or "").rstrip("!")
            if word in ("GO plan", "GO implement"):
                if any(entry.get("word") == word for entry in given):
                    continue
            elif word in ("CP", "READY") and head12:
                if any(entry.get("word") == word and str(entry.get("sha") or "").startswith(head12) for entry in given):
                    continue
            return True
        return False

    def _status(self, plan_rel: str | None = None) -> dict:
        return engine.build_status(
            self.root, plan_rel or self.default_plan, manifest=self.manifest(), approvals=self.approvals,
            session_url=self.session_url, prototype_port=self.prototype_port, version=self.version,
            ahead=lambda _root, branch, head: self.ahead(branch, head), prototype_up=self.prototype_up,
            acceptance=lambda slug: self.prototype_acceptance(slug), overview=lambda slug: self.prototype_overview(slug))

    # --- documents
    def doc(self, rel: str | None) -> dict | None:
        path = engine.resolve_doc(self.root, rel)
        if not path:
            return None
        size = path.stat().st_size
        text = engine.read_masked(path)   # the whole document is masked BEFORE it is parsed or rendered: this route is served on the read socket without a login
        title, _fields = engine.parse_header(text)
        # second layer, like `/api/status`: the whole answer is masked once more (the file name in `path` and in the fallback title is not part of the text)
        return redact({"path": rel, "title": title or path.stem, "status": engine.doc_status(text)[0],
                       "mtime": engine._iso(engine._mtime(path)), "size": size, "truncated": size > MAX_DOC_BYTES,
                       "html": render_markdown(text, rel or "")})

    # --- consents (in-process, the shared core functions of status.py; no subprocess, no own jsonl writer)
    def approve(self, kind: str, rel: str, sha: str, plan: str | None = None, via: dict | None = None,
                plan_state: str | None = None, guard=None, worktree: str | None = None) -> tuple[int, dict]:
        """Consent from a button: only the action the page offers right now (the same file and sha).

        GO: `status.set_doc_status(..., expect_sha=sha)` rewrites the Status line (the consent itself), then
        `status.record_approval` writes the trail entry to the run's `approvals.jsonl` when the run directory exists
        (without a run there is no trail: `trail` = null; the session reads the consent from the Status line).

        `guard` (a callable, the server of tabs): called under the consent lock right before the write; False = 409 `tabChanged`, nothing written.
        `worktree`: the directory name of the tab, kept in the trail entry (audit only)."""
        lang = self.lang(fresh=True)
        if not self.approvals:
            return 403, {"error": i18n.t(lang, "srv.http.approvalsOff")}
        if kind not in APPROVE_KINDS or not isinstance(rel, str) or not isinstance(sha, str):
            return 400, {"error": i18n.t(lang, "srv.http.badRequest")}
        if kind in ("cp", "ready"):
            if plan_state is not None and (not isinstance(plan_state, str) or plan_state not in status_core.PLAN_STATUSES):
                return 400, {"error": i18n.t(lang, "srv.http.badRequest")}
            return self._approve_delivery(kind, rel, sha, plan if isinstance(plan, str) else None, via, plan_state, guard, worktree)
        word = APPROVE_KINDS[kind]
        with self._approve_lock:
            current = self.status(rel if kind == "plan" else None)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind and a["path"] == rel), None)
            if offer is None:
                return 409, {"error": i18n.t(lang, "srv.http.notPossibleFor", word=word, rel=rel)}
            if offer["state"] != "waiting":
                return 409, {"error": i18n.t(lang, "srv.http.inactive", word=offer["word"], reason=offer["reason"])}
            if offer["sha"] != sha:
                return 409, {"error": i18n.t(lang, "srv.http.fileChanged")}
            folder = "specs" if kind == "spec" else "plans"
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            try:
                report = status_core.set_doc_status(self.root, rel, "approve", folder, expect_sha=sha)
            except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                return 409, {"error": i18n.t(lang, "srv.http.statusRefused", reason=status_core._message(error, 300))}
            run_id = offer.get("run")
            trail = None
            if run_id:
                record = status_core.record_approval(self.root, word, run_id, sha=sha, source="dashboard", file=rel,
                                                     sha_after=report.get("sha"), via=via, worktree=worktree)
                if record["status"] == "PASS":
                    trail = record["path"]
                else:
                    log(f"ślad zgody „{word}” nie zapisany: {record['reason']}")
            log(f"zgoda „{word}” dla {rel} (sha {sha[:10]}, ślad: {trail or 'brak przebiegu'})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": rel, "trail": trail, "next": i18n.t(lang, "srv.http.nextHint")}

    def _approve_delivery(self, kind: str, run_id: str, sha: str, plan: str | None = None, via: dict | None = None,
                          plan_state: str | None = None, guard=None, worktree: str | None = None) -> tuple[int, dict]:
        """CP / READY: `status.record_approval` into `.skillsync/runs/<run-id>/approvals.jsonl` with the sha of the HEAD
        shown on the page. The session checks the entry (word, sha = its HEAD) instead of a word typed in the terminal.
        `plan_state` (K13): the plan state the page showed (`planState` of the offer), passed to the writer as `expect_plan_state`: the writer compares it with
        the state read under the file lock and refuses a different one (`code: plan_state_changed`) = 409 (the plan was closed or reopened after the page
        was drawn, which no change of HEAD tells); absent = the state read by the writer, as before."""
        word = APPROVE_KINDS[kind]
        lang = self.lang()
        with self._approve_lock:
            current = self.status(plan)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind), None)
            if offer is None or offer["path"] != run_id:
                return 409, {"error": i18n.t(lang, "srv.http.notPossible", word=word)}
            if offer["state"] != "waiting":
                return 409, {"error": i18n.t(lang, "srv.http.inactive", word=word, reason=offer["reason"])}
            if offer["sha"] != sha:
                return 409, {"error": i18n.t(lang, "srv.http.headChanged")}
            d = current["delivery"]
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            record = status_core.record_approval(
                self.root, word, run_id, sha=sha, source="dashboard", target=d.get("target") if kind == "cp" else None,
                branch=d.get("branch"), plan=(current.get("plan") or {}).get("path"), ahead=d.get("ahead"),
                mode=d.get("mode"), via=via, expect_plan_state=plan_state, worktree=worktree)
            if record.get("code") == "plan_state_changed":
                return 409, {"error": i18n.t(lang, "srv.http.planStateChanged"), "code": "plan_state_changed"}
            if record["status"] != "PASS":
                log(f"wpis zgody „{word}” nie zapisany: {record['reason']}")
                return 500, {"error": i18n.t(lang, "srv.http.saveFailed", reason=record["reason"])}
            log(f"zgoda „{word}” dla HEAD {sha[:10]} (przebieg {run_id})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": record["path"], "next": i18n.t(lang, "srv.http.nextHint")}


    # Full literal keys (the i18n guard finds the keys used in code by their literals, so no key is built from a code).
    FEEDBACK_TEXT_KEYS = {"card": "srv.feedback.card", "no_preview": "srv.feedback.noPreview", "content_changed": "srv.feedback.contentChanged",
                          "stale": "srv.feedback.stale", "sent": "srv.feedback.sent", "scan": "srv.feedback.scan", "failed": "srv.feedback.failed"}

    def feedback_approve(self, draft: str, sha: str, via: dict | None = None, guard=None, tab: str | None = None,
                         session_root: Path | None = None) -> tuple[int, dict]:
        """Consent to send a feedback draft (`POST /api/feedback-approve`): `feedback.approve_from_preview` with `source='dashboard'`, the previewed
        content only (the full sha). No tracker call. The answer of a refusal is `{error, code}`: a fixed text of the catalogue, never a title, a body or a sha.
        The consent is global for the project: the store is the one of the MAIN checkout (`feedback.store_root`), whatever tab asks; `tab` is only the tab whose token
        was given (logged), `guard` is the same re-validation of that tab as for the other writes (False = 409 `tabChanged`)."""
        lang = self.lang(fresh=True)
        if not self.approvals:
            return 403, {"error": i18n.t(lang, "srv.http.approvalsOff")}
        with self._approve_lock:
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            if feedback.card_alias(self.root):   # a card of THIS tab never consents; the session directory is checked again by `approve_from_preview`
                log(f"zgoda feedbacku {draft} odmówiona (card, zakładka {tab or '-'})")
                return 403, {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS["card"]), "code": "card"}
            try:
                session = feedback.open_session(session_root or self.root)
                result = feedback.approve_from_preview(session, draft, sha=sha, source="dashboard", require_view=True, tab=tab)
            except feedback.FeedbackError as error:
                code = error.code if error.code in self.FEEDBACK_TEXT_KEYS else "failed"
                log(f"zgoda feedbacku {draft} odmówiona ({code}, sha {sha[:12]}, via {self._via_text(via)})")
                return (403 if code == "card" else 409), {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS[code]), "code": code}
            except (ValueError, LayerError, yaml.YAMLError) as error:   # a project the session cannot be opened for: the refusal of a stale target, not a bad request
                log(f"zgoda feedbacku {draft} odmówiona (sesja: {status_core._message(error, 120)})")
                return 409, {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS["stale"]), "code": "stale"}
            log(f"zgoda feedbacku {draft} (sha {result['sha12']}, {'już była' if result['already'] else 'zapisana'}, via {self._via_text(via)}, zakładka {tab or '-'})")
        self.check_once()
        return 200, {"ok": True, "draft": result["draft"], "sha12": result["sha12"], "approval": result["approval"], "already": result["already"]}

    @staticmethod
    def _via_text(via: dict | None) -> str:
        return f"{via.get('host', '')}:{via.get('port', '')}" if via else "-"


# --------------------------------------------------------------------------- tabs

class TabRegistry:
    """The tabs of one server: `tab id -> Board`, the token that carries the tab, and the tombstones of the tabs that went away.

    One tab = one `Board` (own root, cache and consent lock). The token of a tab is `<id>.<HMAC-SHA256(process secret, "<id>\\n<normalised path>")>`;
    `id` is only a dictionary key (always matched against `dashboard_worktrees.ID_PATTERN`, never part of a path). A server with ONE tab also accepts the
    bare token of its default tab (the page and `dashboard url` of earlier versions). A removed tab leaves a tombstone (at most `TOMBSTONES`, valid for
    `TOMBSTONE_TTL` seconds): a token whose HMAC is right for the tombstone gets "gone" (410), every other unknown token is "deny" (the same 403 as a wrong
    token, so the answer does not tell whether a tab ever existed)."""

    TOMBSTONES = 64
    TOMBSTONE_TTL = 600.0
    MISSING_ROUNDS = 2   # a tab that is not on the list of the project goes after this many consecutive refreshes (no flicker on a dismounted disk)

    def __init__(self, default_board: "Board", *, project=None, secret: bytes | None = None, now=None):
        self.project = project
        self.server_id = dashboard_token.new_server_id()   # random per process: `/healthz` answers it, the state files and the pin directory carry it
        self._secret = secret if secret is not None else secrets.token_bytes(32)
        self._now = now or time.time
        self._lock = threading.RLock()
        self._boards: dict[str, Board] = {}
        self._entries: dict[str, dashboard_worktrees.Entry] = {}
        self._paths: dict[str, str] = {}   # id -> the normalised path, computed once at registration (the HMAC never touches the disk)
        self._tombs: list[tuple[str, str, float]] = []   # (id, normalised path, time), oldest first
        self._pins: dict[str, str | None] = {}   # id -> its pin file (None: the directory the server was started in): a pinned tab outlasts the limit of the visible ones
        self._listeners: list = []   # called (without the lock) after the list of the tabs changed: the state files follow it
        self.clock = time.monotonic   # of the boards' `touch` and the observer's "watched" (`set_clock`)
        self._waiting: dict[str, bool] = {}   # id -> "waits for the user" from the files, as of the last refresh of the list (`refresh_waiting`; False until one)
        self._shut: set[str] = set()   # ids of the tabs that cannot be removed (the start directory) and carry an exclusion now: their reads answer as for an unknown tab
        self._hidden: list[dict] = []   # `/api/tabs` `hidden[]`: {name, lastActivity, reason} of the last refresh (no path, no token, never an excluded one)
        self._missing: dict[str, int] = {}   # id -> consecutive refreshes in which the tab was not on the list (only `reconcile` touches it)
        path = dashboard_worktrees.normalize_path(default_board.root)
        self.default_id = dashboard_worktrees.tab_id(path, project.main_path if project else None)
        self._dummy = hmac.new(self._secret, b"\0dummy", hashlib.sha256).hexdigest().encode("ascii")
        entry = dashboard_worktrees.Entry(self.default_id, path, os.path.basename(path) or path, "main" if self.default_id == "main" else "worktree",
                                          False, False, None, None, None)
        self._add(entry, default_board, path)

    # --- tokens
    def _mac(self, tab_id: str, path: str) -> str:
        return hmac.new(self._secret, f"{tab_id}\n{path}".encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()

    def token_for(self, tab_id: str) -> str:
        """`<id>.<hmac>` of a registered tab (KeyError for an unknown one). Only the server and `dashboard_token` print it, never an API."""
        with self._lock:
            path = self._paths[tab_id]
        return f"{tab_id}.{self._mac(tab_id, path)}"

    def resolve_token(self, header_value) -> tuple[str, str | None]:
        """`("ok", id)`, `("gone", id)` or `("deny", None)` for the value of `X-Approve-Token`.

        A value of the shape `<id>.<token>` (a valid id) is compared once with the HMAC of the tab, of its tombstone, or with a dummy of the same length for
        an unknown id; any other value is compared once with the bare token of the default tab (a dummy when the server has several tabs). Each case is ONE
        `compare_digest`, so the time does not tell a known tab from an unknown one. A dotted value that fails is also tried as a bare token (a token a
        caller chose may contain a dot)."""
        text = header_value if isinstance(header_value, str) else ""
        tab_id, dot, token = text.partition(".")
        with self._lock:
            default = self._boards.get(self.default_id)
            bare = default.approve_token.encode("utf-8") if len(self._boards) == 1 and default is not None and default.approve_token else None
            verdict, expected = "deny", self._dummy
            dotted = bool(dot) and dashboard_worktrees.ID_PATTERN.fullmatch(tab_id) is not None
            if dotted:
                if tab_id in self._boards:
                    verdict, expected = "ok", self._mac(tab_id, self._paths[tab_id]).encode("ascii")
                else:
                    stone = self._tombstone(tab_id)
                    if stone is not None:
                        verdict, expected = "gone", self._mac(tab_id, stone[1]).encode("ascii")
        if dotted:
            if hmac.compare_digest(token.encode("utf-8", "replace"), expected) and verdict != "deny":
                return verdict, tab_id
            if bare is None:
                return "deny", None
        if hmac.compare_digest(text.encode("utf-8", "replace"), bare if bare is not None else self._dummy) and bare is not None:
            return "ok", self.default_id
        return "deny", None

    # --- the tabs
    def ids(self) -> list[str]:
        with self._lock:
            return list(self._boards)

    def board(self, tab_id: str) -> "Board | None":
        with self._lock:
            return self._boards.get(tab_id) if isinstance(tab_id, str) else None

    def _add(self, entry, board: "Board", path: str) -> None:
        with self._lock:
            self._boards[entry.id] = board
            self._entries[entry.id] = entry
            self._paths[entry.id] = path
            board.clock = self.clock
            default = self._boards.get(self.default_id)
            if default is not None and default is not board:   # the limits belong to the server: one SSE counter and one `TokenFailures` for every tab
                board.sse_owner = default
                board.token_failures = default.token_failures
            if board.approvals and not board._explicit_token:   # the token of the tab is derived, so the registry and the board agree on it
                board.approve_token = self._mac(entry.id, path)

    def register(self, entry, board: "Board | None" = None) -> str:
        """Add the tab of an `Entry` (a `Board` of its path is made unless one is given) and drop the tombstone of its id and path.
        A given `board` must have the root of the entry (ValueError otherwise: a board of another directory would take the token of this one)."""
        if not isinstance(entry.id, str) or not dashboard_worktrees.ID_PATTERN.fullmatch(entry.id):
            raise ValueError("bad tab id")
        path = dashboard_worktrees.normalize_path(entry.path)   # the only disk access of the registration, outside the lock
        if board is not None and dashboard_worktrees.normalize_path(board.root) != path:
            raise ValueError("the board does not belong to the path of the entry")
        with self._lock:
            self._sweep()
            if entry.id in self._boards and self._paths[entry.id] == path:
                self._entries[entry.id] = entry
                return entry.id
        if board is None:
            board = self._new_board(path)
        with self._lock:
            if entry.id in self._boards:
                if self._paths[entry.id] == path:   # registered meanwhile by another thread: keep that board (and its token), drop the new one
                    self._entries[entry.id] = entry
                    self._tombs = [stone for stone in self._tombs if stone[0] != entry.id and stone[1] != path]
                    return entry.id
                self.deregister(entry.id)
            self._tombs = [stone for stone in self._tombs if stone[0] != entry.id and stone[1] != path]
            self._add(entry, board, path)
        self._changed()
        return entry.id

    def _new_board(self, path: str) -> "Board":
        """A `Board` of another directory with the settings of the server (hosts, assets, approvals); its own manifest, cache and lock."""
        with self._lock:
            base = self._boards.get(self.default_id) or next(iter(self._boards.values()), None)
        if base is None:
            raise ValueError("no board to take the settings of the server from (the registry is empty)")
        port = _tab_prototype_port(path)   # the `prototype.port` of THIS tab's manifest; none = no prototype (never the port of the default tab)
        board = Board(Path(path), poll=base.poll, heartbeat=base.heartbeat, prototype_port=port or engine.PROTOTYPE_PORT, approvals=base.approvals,
                     session_url=base.session_url, approve_hosts=tuple(base.approve_hosts), read_hosts=tuple(base.read_hosts),
                     assets=base.assets, descriptions=base.descriptions, probe_prototype=port is not None)
        if dashboard_worktrees.normalize_path(board.root) != path:   # the path moved between the registration and the board
            raise ValueError("the new board does not belong to the registered path")
        return board

    def deregister(self, tab_id: str, *, tombstone: bool = True) -> None:
        """Remove a tab: stop its `Board` (its SSE streams end) and leave a tombstone (none with `tombstone=False`: an excluded tab must not answer 410).
        An unknown id is ignored."""
        with self._lock:
            self._sweep()
            board = self._boards.pop(tab_id, None)
            entry = self._entries.pop(tab_id, None)
            path = self._paths.pop(tab_id, None)
            pin = self._pins.pop(tab_id, None)
            if board is None or entry is None or path is None:
                return
            if tombstone:
                self._tombs.append((tab_id, path, self._now()))
                del self._tombs[:-self.TOMBSTONES]
        if pin:   # the pin lasts until the tab goes
            try:
                os.unlink(pin)
            except OSError:
                pass
        board.stop.set()   # its SSE streams end (they release their slots of the server's counters); the observer no longer sees the board
        with board.cond:
            board.cond.notify_all()
        board._accept = ((), 0.0, {})   # the caches of a tab that is gone are not kept
        self._missing.pop(tab_id, None)
        self._changed()

    def _sweep(self) -> None:
        horizon = self._now() - self.TOMBSTONE_TTL
        self._tombs = [stone for stone in self._tombs if stone[2] > horizon][-self.TOMBSTONES:]

    def _tombstone(self, tab_id: str):
        with self._lock:
            self._sweep()
            return next((stone for stone in reversed(self._tombs) if stone[0] == tab_id), None)

    def summary(self) -> list[dict]:
        """The tabs for `/api/tabs`: no token, no secret."""
        with self._lock:
            return [{"id": e.id, "name": e.name, "path": e.path, "kind": e.kind, "branch": e.branch, "head": e.head, "current": e.id == self.default_id}
                    for e in self._entries.values()]

    HIDDEN_MAX = 64

    def hidden_summary(self) -> list[dict] | None:
        """`hidden[]` of `/api/tabs`: the worktrees the last refresh kept out of the tabs, newest activity first, at most `HIDDEN_MAX`. None for a server
        without a project (the field is absent there)."""
        if self.project is None:
            return None
        with self._lock:
            return [dict(item) for item in self._hidden]

    def worktrees(self) -> list[dict]:
        """`worktrees[]` of the status JSON (only a server with more than one tab): `{id, name, path, kind, branch, head, current, lastActivity,
        waiting}`, no token. The light summary of every tab: `branch`, `head` and `lastActivity` come from the last refresh of the registry (the observer
        reads them from the `.git` files and the run files, no `git`), `waiting` is the flag the last refresh of the list read from the files of the tab
        (`Board.light_waiting`, also for a tab nobody watches; False until the first refresh)."""
        with self._lock:
            rows = [(e, self._waiting.get(e.id, False)) for e in self._entries.values()]
        return [{"id": e.id, "name": e.name, "path": e.path, "kind": e.kind, "branch": e.branch, "head": e.head, "current": e.id == self.default_id,
                 "lastActivity": datetime.fromtimestamp(e.last_activity, timezone.utc).isoformat(timespec="seconds") if e.last_activity is not None else None,
                 "waiting": bool(waiting)} for e, waiting in rows]

    def tabs_state(self) -> list[dict]:
        """`tabs[]` of the state file: `{id, path, token}` (the writer drops the token where its file has none). The only place the tokens leave the registry."""
        with self._lock:
            ids = list(self._paths)
        return [{"id": tab_id, "path": self._paths[tab_id], "token": self.token_for(tab_id), "current": tab_id == self.default_id} for tab_id in ids if tab_id in self._paths]

    def add_listener(self, listener) -> None:
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception as exc:  # noqa: BLE001 - a state file that cannot be rewritten must not break the registry
                log(f"zakładki: {exc!r}")

    # --- pins (the directory a `dashboard serve` was run in outlasts the limit of the visible tabs)
    def pin(self, tab_id: str, file: str | None = None) -> None:
        with self._lock:
            if tab_id in self._boards:
                self._pins[tab_id] = file

    def is_pinned(self, tab_id: str) -> bool:
        with self._lock:
            return tab_id in self._pins

    def pin_file(self, tab_id: str) -> str | None:
        with self._lock:
            return self._pins.get(tab_id)

    def evictable(self) -> str | None:
        """The least active tab that is neither the main checkout, nor the default one, nor pinned: the one that makes room for a pin at the limit."""
        with self._lock:
            pool = [e for e in self._entries.values() if e.kind != "main" and e.id != self.default_id and e.id not in self._pins]
        pool.sort(key=lambda e: (e.last_activity is not None, e.last_activity or 0.0, e.name))
        return pool[0].id if pool else None

    def set_clock(self, clock) -> None:
        """One clock for the registry's boards (their `touch`) and the observer that judges whether a tab is watched."""
        with self._lock:
            self.clock = clock
            for board in self._boards.values():
                board.clock = clock

    def boards(self) -> list["Board"]:
        """The boards of the registered tabs (a snapshot)."""
        with self._lock:
            return list(self._boards.values())

    def pinned_paths(self) -> list[str]:
        """The normalised paths of the pinned tabs (the default one too): `discover` keeps them whatever their activity."""
        with self._lock:
            return [self._paths[tab_id] for tab_id in self._pins if tab_id in self._paths]

    def reconcile(self, found) -> bool:
        """Bring the tabs in line with a `dashboard_worktrees.discover` result (the refresh of the observer); True when a tab was added or removed.

        Every visible entry gets a tab (a new one only while the server has fewer than `MAX_VISIBLE`: the stale ones go first, the new one comes with the next
        refresh, so the registry never holds more than 16) and its `Entry` is renewed (HEAD, branch, activity). A tab that is not visible any more is removed
        only after `MISSING_ROUNDS` consecutive refreshes without it (an unmounted disk or a worktree being moved does not flicker); it leaves a tombstone
        (410 for a stale page) unless an exclusion removed it (`tombstone=False`: 404/403 as for a tab that never existed). The default tab (the directory the
        server was started in) is never removed, so the registry is never empty and a server whose start directory vanished stays up: its reads are empty
        and every write is refused by the re-validation (409 `tabChanged`)."""
        desired = {entry.id: entry for entry in found.tabs}
        reasons = getattr(found, "hidden_reasons", {})
        hidden = [{"name": e.name, "lastActivity": datetime.fromtimestamp(e.last_activity, timezone.utc).isoformat(timespec="seconds")
                   if e.last_activity is not None else None, "reason": reasons.get(e.id, "limit")} for e in found.hidden[:self.HIDDEN_MAX]]
        with self._lock:
            self._hidden = hidden   # `found.hidden` is already ordered by activity and holds no excluded entry
        excluded = set()
        for path in found.excluded:
            try:
                excluded.add(dashboard_worktrees.normalize_key(path))
            except (OSError, ValueError):
                continue
        changed = False
        for entry in found.tabs:
            with self._lock:
                known = entry.id in self._boards
                full = len(self._boards) >= dashboard_worktrees.MAX_VISIBLE
            if not known and full:
                continue
            try:
                before = known
                self.register(entry)
                changed = changed or not before
            except (ValueError, OSError) as exc:
                log(f"zakładki: {entry.name}: {exc}")
        with self._lock:
            registered = [(tab_id, self._paths[tab_id]) for tab_id in self._boards if tab_id != self.default_id and tab_id not in desired]
            for tab_id in list(self._missing):
                if tab_id in desired or tab_id not in self._boards:
                    del self._missing[tab_id]
        with self._lock:
            default_path = self._paths.get(self.default_id)
        try:
            shut_now = default_path is not None and dashboard_worktrees.normalize_key(default_path) in excluded
        except (OSError, ValueError):
            shut_now = False
        with self._lock:   # the start directory stays registered whatever happens to it; an exclusion only shuts it for reads and writes (the marker or the entry may go again)
            self._shut = {self.default_id} if shut_now else set()
        for tab_id, path in registered:
            self._missing[tab_id] = self._missing.get(tab_id, 0) + 1
            if self._missing[tab_id] >= self.MISSING_ROUNDS:
                try:
                    shut = dashboard_worktrees.normalize_key(path) in excluded
                except (OSError, ValueError):
                    shut = False
                self.deregister(tab_id, tombstone=not shut)
                changed = True
        return changed

    def refresh_waiting(self) -> None:
        """The `waiting` flag of every tab from its files (`Board.light_waiting`, no `git`): one tab that cannot be read keeps its last value and does not stop the others."""
        with self._lock:
            rows = [(e.id, e.head, self._boards.get(e.id)) for e in self._entries.values()]
        flags = {}
        for tab_id, head, board in rows:
            if board is None:
                continue
            try:
                flags[tab_id] = bool(board.light_waiting(head))
            except Exception as exc:  # noqa: BLE001 - a broken neighbour must not hide the others
                log(f"zakładki: waiting {tab_id}: {exc!r}")
        with self._lock:
            for tab_id, flag in flags.items():
                if tab_id in self._boards:
                    self._waiting[tab_id] = flag
            for tab_id in list(self._waiting):
                if tab_id not in self._boards:
                    del self._waiting[tab_id]

    def is_shut(self, tab_id: str) -> bool:
        """True while the tab (the start directory, never removed) carries an exclusion: reads answer 404 as for an unknown tab, writes are refused by the re-validation."""
        with self._lock:
            return tab_id in self._shut

    def stop_all(self) -> None:
        """The server stops: every tab's board stops (its watcher and SSE streams end)."""
        with self._lock:
            boards = list(self._boards.values())
        for board in boards:
            board.stop.set()
            with board.cond:
                board.cond.notify_all()

    def revalidate_for_write(self, tab_id: str) -> bool:
        """Right before a write: does the tab still pass R2 and name the same directory? (best effort). Without a project only a server of exactly
        one registered tab passes; several tabs and no project to check them against fail closed."""
        with self._lock:
            entry = self._entries.get(tab_id)
            alone = len(self._boards) == 1
        if entry is None:
            return False
        if self.project is None:
            return alone
        if not dashboard_worktrees.revalidate_entry(self.project, entry):
            return False
        # R3 at the write too: a tab that is not removed (the start directory, a pinned one) may have got the exclusion marker or an `exclude` entry since
        # it started. The marker and the main manifest are read NOW (no cache of a refresh); a manifest that cannot be read fails closed. The main checkout is always a tab.
        if entry.kind == "main":
            return True
        main = self.project.main_path
        try:
            if main and os.path.isdir(main) and not (Path(main) / ".github" / ".skillsync.yaml").is_file():
                exclude = []   # no manifest at all (never read, not "unreadable"): no list to honour; the marker below still applies
            else:
                exclude = dashboard_token.worktree_settings(self.project, None, None)["exclude"]
        except ValueError as exc:
            log(f"zakładki: zapis odmówiony, dashboard.worktrees nieczytelne: {exc}")
            return False
        return not dashboard_worktrees.is_excluded(entry.path, manifest_exclude=exclude, main_path=self.project.main_path)

    def main_root(self) -> Path | None:
        """The main checkout of the project when the registry has one (the feedback session is opened there), else None (the directory of the tab)."""
        main = self.project.main_path if self.project is not None else None
        return Path(main) if main and os.path.isdir(main) else None

    def worktree_name(self, tab_id: str) -> str | None:
        """The directory name of a tab for the `worktree` field of a trail entry, only when the server has more than one tab (or a project)."""
        with self._lock:
            entry = self._entries.get(tab_id)
            if entry is None or (self.project is None and len(self._boards) < 2):
                return None
        name = "".join(ch for ch in entry.name if ch.isprintable())[:128]
        return name or None


# --------------------------------------------------------------------------- HTTP

# --------------------------------------------------------------------------- history (the tab "Historia")
# Full literal keys (the i18n guard finds the keys used in code by their literals).
HISTORY_TEXT_KEYS = {"planGone": "srv.history.planGone", "shaMismatch": "srv.history.shaMismatch", "statusNotAllowed": "srv.history.statusNotAllowed",
                     "locked": "srv.history.locked", "auditRefused": "srv.history.auditRefused"}


def history_response(registry: "TabRegistry") -> dict:
    """`GET /api/history`: `{schema, truncated, count, tabs[], items[]}` over the roots of the registered tabs (never a path from the request); a tab that carries
    an exclusion (R3, `is_shut`) has no plans here. The default tab comes first."""
    rows = registry.summary()
    rows.sort(key=lambda row: row["id"] != registry.default_id)
    tabs, listed = [], []
    for row in rows:
        board = registry.board(row["id"])
        if board is None or registry.is_shut(row["id"]):
            continue
        listed.append(dashboard_history.TabRoot(row["id"], row["name"], Path(board.root)))
        tabs.append({"id": row["id"], "name": row["name"], "branch": row.get("branch"), "main": row.get("kind") == "main"})
    body = dashboard_history.collect(listed)
    return {"schema": 1, "truncated": body["truncated"], "count": body["count"], "tabs": tabs, "items": body["items"]}


def plan_status_request(board: "Board", tab_id: str, body: dict, guard, via: dict | None, lang: str) -> tuple[int, dict]:
    """`POST /api/plan-status` after the token chain: `{tab, path, sha, action}` of the tab of the token only. The write is `dashboard_history.change_status`
    under the consent lock of that tab's board, with the same re-validation (`guard`) as every other write. Answers `(code, {..})`; a refusal carries `code`."""
    def refuse(http: int, code: str, key: str) -> tuple[int, dict]:
        return http, {"error": i18n.t(lang, key), "code": code}
    if not board.approvals:
        return refuse(403, "approvalsOff", "srv.http.approvalsOff")
    tab, rel, sha, action = body.get("tab"), body.get("path"), body.get("sha"), body.get("action")
    if not (isinstance(tab, str) and isinstance(rel, str) and isinstance(sha, str) and isinstance(action, str)) or action not in dashboard_history.ACTIONS:
        return refuse(400, "badRequest", "srv.http.badRequest")
    if tab != tab_id:
        return refuse(400, "tabMismatch", "srv.http.tabMismatch")
    try:
        with board._approve_lock:
            result = dashboard_history.change_status(board.root, rel, action, sha, tab_id, via=via, guard=guard)
    except dashboard_history.HistoryError as error:
        if error.code == "tabChanged":
            return refuse(409, "tabChanged", "srv.http.tabChanged")
        if error.code == "badRequest":
            return refuse(400, "badRequest", "srv.http.badRequest")
        if error.code in HISTORY_TEXT_KEYS:
            log(f"plan-status {action} {rel[:120]!r}: odmowa {error.code}")
            return refuse(error.http, error.code, HISTORY_TEXT_KEYS[error.code])
        log(f"plan-status {action} {rel[:120]!r}: błąd {error.detail}")
        return refuse(500, "serverError", "srv.http.serverError")
    if result["auditFailed"]:
        log(f"plan-status {result['from']}->{result['to']} {rel}: zmiana wykonana, audyt „done” nie zapisany")
    else:
        log(f"plan-status {result['from']}->{result['to']} {rel} (sha {result['sha'][:10]})")
    board.check_once()   # the tab's page sees the change (SSE `change`)
    return 200, {"ok": True, **result}



class Handler(LingeringCloseMixin, BaseHTTPRequestHandler):
    server_version = f"dashboard/{__version__}"
    board: Board  # the board of the default tab, set in make_server (every route without `tab` and the language of every refusal)
    tabs: TabRegistry  # the tabs of the server, set in make_server
    verbose = False
    # Role of the socket (two-port mode): "read" has no write route at all, "approve" is the strict consent socket, "both" is the
    # single-port server of 1.5.0 (the role comes from `Host`, as before). Set by `make_server`.
    role = ports.ROLE_BOTH
    allow_proxy_headers = False
    own_port = 0
    peer_port = None
    # Socket timeout of every connection (`StreamRequestHandler.setup`): a client that opens a connection and sends no
    # request (or stops in the middle of it) is closed after 30 s instead of holding a thread. SSE only writes.
    timeout = 30

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 (stdlib API)
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
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_refused(self) -> bool:
        """Every endpoint: a `Host` outside loopback, `--approve-host` and `--read-host` (DNS rebinding, an unlisted
        tunnel or LAN name) gets 403 without any content; the refusal goes to the verbose log only. Passing this check is
        enough for reads; a write also needs `_approve_allowed` (loopback or `--approve-host`)."""
        host = request_host(self.headers.get("Host"))
        if self._host_allowed(host):
            return False
        if self.verbose:
            log(f"odmowa {self.command} {urlparse(self.path).path[:120]!r}: Host spoza listy ({host[:80]!r})")
        try:
            self._json(403, {"error": host_refused_message(self.role, self.headers.get("Host"), self.board.lang())})
        except OSError:
            pass
        return True

    def _host_allowed(self, host: str) -> bool:
        """May this `Host` open the page on this socket? Single-port: loopback, `--approve-host`, `--read-host` (as in 1.5.0). Two-port
        sockets add the IP literals of this machine (a phone opens `http://<IP>:<port>/` without being listed; a literal cannot come
        from DNS rebinding) and, on the read socket only, the machine's own host name (`ports.own_names`, text match, no DNS) and differ by role: the read socket accepts `--read-host` (also `'*'`), the consent socket never does."""
        board = self.board
        if self.role == ports.ROLE_BOTH:
            return host in board.approve_hosts or host in board.read_hosts
        if host in board.approve_hosts or ports.is_local_ip(host):
            return True
        return self.role == ports.ROLE_READ and (host in board.read_hosts or "*" in board.read_hosts or host in ports.own_names())

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _tab_board(self, query: dict) -> "Board | None":
        """The board of `?tab=<id>` (the default tab without it); None for an id outside `ID_PATTERN`, an unknown or a removed tab."""
        raw = (query.get("tab") or [None])[0]
        board = self.board if raw is None else (self.tabs.board(raw) if dashboard_worktrees.ID_PATTERN.fullmatch(raw) else None)
        if board is not None and self.tabs.is_shut(self.tabs.default_id if raw is None else raw):   # the start directory got an exclusion: no reads, as for an unknown tab
            return None
        if board is not None:
            board.touch()   # asked for: the observer keeps the signature of this tab fresh for a while
        return board

    def do_GET(self) -> None:  # noqa: N802
        if self._host_refused():
            return None
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path
        try:
            tab_board = None
            if route in TAB_ROUTES:
                tab_board = self._tab_board(query)
                if tab_board is None:
                    return self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.unknownTab"), "code": "unknownTab"})
            if route in ("/", "/index.html"):
                return self._asset("index.html")
            if route.startswith("/assets/"):
                return self._asset(route[len("/assets/"):])
            if route == "/api/status":
                data = tab_board.status((query.get("plan") or [None])[0])
                here, viewer = self._approvals_role()
                if not here and isinstance(data.get("feedback"), dict):   # the drafts' content only for the role that may consent: the rest gets `{draft, state}` (the answer is built per request, no cache)
                    data["feedback"] = feedback.read_form(data["feedback"])
                data["approvals"]["here"] = here
                data["approvals"]["viewer"] = viewer
                if len(self.tabs.ids()) > 1:   # a project server with several tabs: the list for the tab bar (a server of one tab answers as before)
                    data["worktrees"] = self.tabs.worktrees()
                token_required = bool(tab_board.approve_token)   # only the fact: the value never leaves the process
                if self.role != ports.ROLE_BOTH:   # two-port mode: the role is the socket, never the header
                    data["approvals"]["role"] = self.role
                    token_required = token_required and self.role == ports.ROLE_APPROVE
                data["approvals"]["tokenRequired"] = token_required
                return self._json(200, dashboard_tests.finalize_status(data, test_events.mask_setting(tab_board.manifest())))  # as `dashboard show`: the viewer sees what the operator sees; secrets masked except the raw `tests` (decision 6)
            if route == "/api/tests":   # read-only on BOTH sockets, like /api/status (the same rule: no write route anywhere near it)
                one = lambda name: (query.get(name) or [None])[0]
                try:
                    body = dashboard_tests.tests_response(tab_board.root, one("run"), one("step"), one("attempt") or "0", one("group"), one("limit") or 500, one("offset") or 0)
                except (ValueError, TypeError):
                    body = None
                if body is None:
                    return self._json(404, {"error": i18n.t(tab_board.lang(), "srv.http.noTestRun")})
                return self._json(200, dashboard_tests.finalize_response(body, test_events.mask_setting(tab_board.manifest())))
            if route == "/api/doc":
                doc = tab_board.doc((query.get("path") or [None])[0])   # resolved under the root of THIS tab only
                if doc is None:
                    return self._json(404, {"error": i18n.t(tab_board.lang(), "srv.http.docUnavailable")})
                return self._json(200, doc)
            if route == "/api/tabs":   # the tabs without any token; the same on every socket that may read
                answer = {"tabs": self.tabs.summary(), "current": self.tabs.default_id}
                hidden = self.tabs.hidden_summary()
                if hidden is not None:
                    answer["hidden"] = hidden
                return self._json(200, answer)
            if route == "/api/history":   # the finished plans of every tab (lazy: only when the page asks), read from the roots of the registered tabs
                data = history_response(self.tabs)
                data["here"] = self._approvals_role()[0]
                return self._json(200, data)
            if route == "/events":
                if self.command == "HEAD":  # headers only; takes no SSE slot and opens no stream
                    return self._send(200, b"", "text/event-stream; charset=utf-8")
                return self._events(tab_board)
            if route == "/api/descriptions":
                return self._json(200, load_descriptions(self.board.descriptions, self.board.lang()))
            if route == "/healthz":
                answer = {"ok": True, "version": self.board.version}
                if self.tabs is not None:   # random per process, not a secret (never derived from a token): `dashboard url` and the append of `dashboard serve` tell this server from a stranger on the port
                    answer["serverId"] = self.tabs.server_id
                return self._json(200, answer)
            return self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.noPage")})
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:
            log(f"błąd {route}: {exc!r}")
            try:
                return self._json(500, {"error": i18n.t(self.board.lang(), "srv.http.serverError")})
            except OSError:
                return None

    def _approvals_role(self) -> tuple[bool, bool]:
        """`(here, viewer)` of this request: `here` = may give consents, `viewer` = read-only. In the two-port mode the socket decides
        (consent socket = here, read socket = viewer); in the single-port mode the Host does (loopback or `--approve-host` = here,
        `--read-host` only = viewer). The one place that decides the role of `/api/status`."""
        if self.role != ports.ROLE_BOTH:   # two-port mode: the role is the socket, never the header
            return self.role == ports.ROLE_APPROVE, self.role == ports.ROLE_READ
        host = request_host(self.headers.get("Host"))
        here = host in self.board.approve_hosts
        return here, not here and host in self.board.read_hosts

    def _approve_allowed(self, lang: str) -> str | None:
        """Reason to refuse a write or None: loopback Host (also against DNS rebinding and tunnels), own header (forces
        a CORS preflight the server does not answer), matching Origin when the browser sent one, JSON content type."""
        allowed = self.board.approve_hosts
        host = request_host(self.headers.get("Host"))
        strict = self.role == ports.ROLE_APPROVE   # the consent socket of the two-port mode: exact Origin, no intermediary headers
        self._tab_id, self._tab_gone = self.tabs.default_id, False   # the tab of the token (set by the token step below)
        self._refusal_code = None   # set to "approve_token" only by the token refusal, so the caller needs no text compare (the language is read once per request)
        if host not in allowed and not (strict and self._host_allowed(host)):  # foreign Host: do_POST already refused it; --read-host: refused here with the fixed HOST_REFUSED text (no name of the request)
            return i18n.t(lang, "srv.http.hostRefused")
        if self.headers.get("X-Status-Board") != "1":
            return i18n.t(lang, "srv.http.noHeader")
        origin = self.headers.get("Origin")
        if strict:
            if not ports.strict_origin_ok(self.headers.get_all("Origin"), self.headers.get("Host")):
                return i18n.t(lang, "srv.http.foreignOrigin")
            if not self.allow_proxy_headers and ports.proxy_headers(self.headers):
                return i18n.t(lang, "srv.http.proxyRefused")
        elif origin and request_host(urlparse(origin).netloc) not in allowed:
            return i18n.t(lang, "srv.http.foreignOrigin")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return i18n.t(lang, "srv.http.expectJson")
        # Last: every refusal above is the same as before for a client without the token. The compare takes the same time for any input.
        if self.board.approve_token:   # `--no-approvals` has no token: it is refused after the body, as before
            verdict, tab_id = self.tabs.resolve_token(self.headers.get(TOKEN_HEADER))
            if verdict == "deny":   # a wrong, unknown or excluded tab gets the very refusal of a wrong token
                if strict:
                    line = self.board.token_failures.add(self.client_address[0])
                    if line:
                        log(line)
                self._refusal_code = "approve_token"
                return i18n.t(lang, "srv.http.tokenRefused")
            self._tab_id, self._tab_gone = tab_id, verdict == "gone"
        return None

    def do_POST(self) -> None:  # noqa: N802
        if self._host_refused():
            return None
        route = urlparse(self.path).path
        try:
            lang = self.board.lang()
            if route not in ("/api/approve", "/api/feedback-approve", "/api/plan-status"):
                return self._json(404, {"error": i18n.t(lang, "srv.http.noPage")})
            refused = self._approve_allowed(lang)
            if refused:
                return self._json(403, {"error": refused, "code": self._refusal_code} if self._refusal_code else {"error": refused})
            if self._tab_gone:   # the token was right for a tab that went away: nothing is written
                return self._json(410, {"error": i18n.t(lang, "srv.http.tabGone"), "code": "tabGone"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._json(400, {"error": i18n.t(lang, "srv.http.badLength")})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                return self._json(400, {"error": i18n.t(lang, "srv.http.badRequest")})
            tab_id = self._tab_id
            if "tab" in body and body["tab"] != tab_id:   # the body may only name the tab of the token
                return self._json(400, {"error": i18n.t(lang, "srv.http.tabMismatch"), "code": "tabMismatch"})
            board = self.tabs.board(tab_id)
            if board is None:   # removed between the token and now
                return self._json(410, {"error": i18n.t(lang, "srv.http.tabGone"), "code": "tabGone"})
            guard = lambda: self.tabs.board(tab_id) is board and self.tabs.revalidate_for_write(tab_id)  # noqa: E731
            via = self._via() if self.role == ports.ROLE_APPROVE else None
            if route == "/api/plan-status":
                code, data = plan_status_request(board, tab_id, body, guard, via, lang)
                return self._json(code, data)
            if route == "/api/feedback-approve":
                draft, sha = body.get("draft"), body.get("sha")
                if not (isinstance(draft, str) and FEEDBACK_DRAFT_RE.fullmatch(draft) and isinstance(sha, str) and FEEDBACK_SHA_RE.fullmatch(sha)):
                    return self._json(400, {"error": i18n.t(lang, "srv.http.badRequest")})
                code, data = board.feedback_approve(draft, sha, via, guard=guard, tab=tab_id, session_root=self.tabs.main_root())
                return self._json(code, data)
            code, data = board.approve(body.get("kind"), body.get("path"), body.get("sha"), body.get("plan"), via,
                                       body.get("planState"), guard=guard, worktree=self.tabs.worktree_name(tab_id))
            if code == 200 and via and not ports.is_loopback_bind(str(via.get("peer", ""))):
                log(f"zgoda z sieci: {via['peer']} (port {via['port']}, Host {via['host']})")
            return self._json(code, data)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": i18n.t(self.board.lang(), "srv.http.badRequest")})
        except Exception as exc:
            log(f"błąd {route}: {exc!r}")
            return self._json(500, {"error": i18n.t(self.board.lang(), "srv.http.serverError")})

    def _via(self) -> dict:
        """Where a consent came from, for `approvals.jsonl`: this socket's port, the Host asked for, and the peer address of the
        connection itself (never a header: `X-Forwarded-For` cannot change it)."""
        return {"port": self.own_port, "host": request_host(self.headers.get("Host"))[:80], "peer": str(self.client_address[0])[:64]}

    def _asset(self, name: str) -> None:
        """Only allowlisted names found in the skill's `assets/` of the active layers (no paths, no traversal)."""
        path = self.board.assets.get(name) if ASSET_NAME_RE.fullmatch(name) else None
        if path is None or path.is_symlink() or not path.is_file():
            return self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.noAsset")})
        with open(path, "rb") as fh:
            body = fh.read(MAX_ASSET_BYTES)
        if name == "index.html":
            body = page_with_language(body, self.board.lang())
        self._send(200, body, ASSET_TYPES[path.suffix])

    def _events(self, board: Board) -> None:
        owner = board.sse_owner   # the limits are the server's, whichever tab the stream is of
        with owner.cond:
            limit = SSE_ROLE_LIMITS.get(self.role)
            if owner.sse_clients >= MAX_SSE_CLIENTS or (limit is not None and owner.sse_by_role.get(self.role, 0) >= limit):
                return self._json(503, {"error": i18n.t(board.lang(), "srv.http.tooManyBrowsers")})
            owner.sse_clients += 1
            board.sse_local += 1
            if limit is not None:
                owner.sse_by_role[self.role] = owner.sse_by_role.get(self.role, 0) + 1
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
            with owner.cond:
                owner.sse_clients -= 1
                board.sse_local -= 1
                if self.role in SSE_ROLE_LIMITS:
                    owner.sse_by_role[self.role] = owner.sse_by_role.get(self.role, 0) - 1
            self.close_connection = True

    def _sse(self, event: str, data: dict, retry: int | None = None) -> None:
        payload = ""
        if retry:
            payload += f"retry: {retry}\n"
        payload += f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self.wfile.write(payload.encode("utf-8"))
        self.wfile.flush()


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for piece in str(version).split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def exclusive_option():
    """`SO_EXCLUSIVEADDRUSE` on Windows, None elsewhere."""
    return getattr(socket, "SO_EXCLUSIVEADDRUSE", None) if os.name == "nt" else None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    # Listen backlog: the default of 5 resets bursts of parallel clients (the same value as the prototype server).
    request_queue_size = 128

    def __init__(self, address, handler, exclusive: bool = False):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.exclusive = exclusive
        ThreadingHTTPServer.__init__(self, address, handler)

    def server_bind(self):
        # Windows lets a second process bind 127.0.0.1:<port> although 0.0.0.0:<port> is taken (the narrower address wins) and then receive
        # the connections of this machine: the two-port sockets ask for the address exclusively. Other systems: nothing (SO_REUSEADDR as before).
        option = exclusive_option() if self.exclusive else None
        if option is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, option, 1)
        # Skip `socket.getfqdn` of HTTPServer.server_bind (slow DNS on some hosts).
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine to print (no packet is sent: connect() on UDP only picks a route)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1, nothing is sent
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    for ip in sorted(ports.host_addresses()):   # bounded: a resolver that blocks must not delay the start or the stop (SIGTERM)
        if ":" not in ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)
    return found or ["<adres-tej-maszyny>"]


def port_problem(port: int) -> str | None:
    if port in FORBIDDEN_PORTS or not PORT_MIN <= port <= PORT_MAX:
        return f"Port {port} poza dozwolonym zakresem {PORT_MIN}–{PORT_MAX} (zabronione: 4444–4448, 9222, 9333)."
    return None


def validate_port(port: int) -> None:
    problem = port_problem(port)
    if problem:
        raise SystemExit(problem)


class ReadHandler(Handler):
    """The read socket: GET/HEAD as the page needs them, and NO other route. Every other method (POST, PUT, DELETE, PATCH, OPTIONS) is a
    plain 404 before any other logic (also before the `Host` check), whatever the headers or the token."""
    role = ports.ROLE_READ

    def _no_route(self) -> None:
        self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.noPage")})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _no_route  # noqa: N815

    def send_error(self, code, message=None, explain=None):   # any other method (unknown verbs) is the same plain 404 as a write route
        if code == 501:
            self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.noPage")})
            return
        super().send_error(code, message, explain)


def make_server(board_or_registry: "Board | TabRegistry", host: str, port: int, verbose: bool = False, role: str = ports.ROLE_BOTH,
                allow_proxy_headers: bool = False, peer_port: int | None = None) -> Server:
    """A server over one `Board` (wrapped in a registry of one tab, shared by every socket made for that board) or over a `TabRegistry`."""
    validate_port(port)
    if isinstance(board_or_registry, TabRegistry):
        registry = board_or_registry
    elif isinstance(board_or_registry, Board):
        if board_or_registry._tabs is None:
            board_or_registry._tabs = TabRegistry(board_or_registry)
        registry = board_or_registry._tabs
    else:   # a stand-in that only has to be bound to a socket (no request is served from it)
        registry = None
    board = registry.board(registry.default_id) if registry is not None else board_or_registry
    handler = type("BoundHandler", (ReadHandler if role == ports.ROLE_READ else Handler,), {"board": board, "tabs": registry, "verbose": verbose, "role": role, "own_port": port,
                                                "allow_proxy_headers": allow_proxy_headers, "peer_port": peer_port})
    return Server((host, port), handler, exclusive=role != ports.ROLE_BOTH)


# --------------------------------------------------------------------------- CLI

def _stop_on_sigterm(_signum, _frame):
    raise KeyboardInterrupt


def _install_stop_handlers(cleanup_done=None):
    """The stop signals of the foreground server, main thread only: `SIGTERM` as before, and on Windows also CTRL_BREAK (`SIGBREAK`, see
    `stopsignal`) and, when `cleanup_done` (a `threading.Event`) is given, the closing of the console window; Linux and macOS register nothing else.
    Returns the registration of the console handler (or None); the server ends with `stopsignal.cleanup_finished(registration, cleanup_done)`."""
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop_on_sigterm)
    stopsignal.install_sigbreak()
    return stopsignal.install_console_close(cleanup_done) if cleanup_done is not None else None


def _tab_prototype_port(path) -> int | None:
    """`prototype.port` of the manifest of the directory `path` (a tab of a project server), None when it has none or cannot be read: such a tab
    has no prototype to probe."""
    try:
        manifest = read_project(path)[1]
        from .prototype import resolve_prototype_settings  # lazy: the prototype module is large
        settings = resolve_prototype_settings((manifest or {}).get("prototype"), path)
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return None
    return None if any(error.startswith("prototype.port") for error in settings["errors"]) else settings["port"]


def _prototype_port(args, manifest, root=".") -> int:
    """Port of the prototype server: `--prototype-port`, else `prototype.port` of the manifest, else 5130."""
    flag = getattr(args, "prototype_port", None)
    if flag is not None:
        if isinstance(flag, bool) or not 1024 <= flag <= 65535:
            raise ValueError(f"--prototype-port {flag}: podaj port 1024–65535.")
        return flag
    from .prototype import resolve_prototype_settings  # lazy: the prototype module is large
    settings = resolve_prototype_settings((manifest or {}).get("prototype"), root)
    port_errors = [error for error in settings["errors"] if error.startswith("prototype.port")]
    if port_errors:
        raise ValueError(port_errors[0])
    return settings["port"] or engine.PROTOTYPE_PORT


class PinWatcher:
    """The pins of a project server (CORE-1180 M-3): `run_once` reads `<serverId>-pins/` and turns each valid pin into a tab; the `Observer` of the
    server calls `poll` every `INTERVAL` seconds (no thread of its own). The reader (`dashboard_token.read_pins`) checks the file; here the path gets the full R2 and R3
    check (`dashboard_worktrees.list_entries`, `is_excluded`; a pin skips R1 only), so a pin can only name a real worktree of this project. At the
    limit of 16 tabs a pin evicts the least active tab that is not pinned; with nothing to evict it is refused. A refused pin file is removed."""

    INTERVAL = 0.2

    def __init__(self, registry: "TabRegistry", project, root: Path, manifest: dict | None, stop: threading.Event):
        self.registry = registry
        self.project = project
        self.root = root
        self.manifest = manifest
        self.stop = stop
        self._seen: dict[str, tuple] = {}
        self._stamp: tuple | None = None   # what the last `poll` saw in the directory (names, size, mtime): an unchanged directory is not read again

    def _directory_stamp(self) -> tuple:
        """One `scandir` of the pin directory (the one listing per tick; no file is opened): `((name, size, mtime_ns, inode), ...)`, `()` when there is none."""
        try:
            directory = dashboard_token.pins_dir(self.registry.server_id)
            with os.scandir(directory) as scan:
                return tuple(sorted((item.name, info.st_size, info.st_mtime_ns, info.st_ino) for item in scan for info in (item.stat(follow_symlinks=False),)))
        except (OSError, ValueError):
            return ()

    def poll(self, force: bool = False) -> bool:
        """What the observer calls each tick: `run_once` only when the directory differs from the last look (or `force`: the periodic full pass, which
        also retries a pin that could not be applied); True when the list of the tabs changed."""
        stamp = self._directory_stamp()
        if not force and stamp == self._stamp:
            return False
        self._stamp = stamp
        return self.run_once()

    def _refuse(self, pin: dict, why: str) -> None:
        log(f"UWAGA: przypięcie zakładki {pin['id']} odrzucone ({why})")
        try:
            os.unlink(pin["file"])
        except OSError:
            pass

    def _apply_plan(self, board: "Board", plan: str | None) -> None:
        if plan and engine.resolve_doc(board.root, plan) is not None:   # the plan of the tab (`--plan` of the command): only a document of THIS tab
            board.default_plan = plan

    def run_once(self) -> bool:
        """One pass over the pins; True when the list of the tabs changed."""
        changed = False
        for pin in dashboard_token.read_pins(self.registry.server_id, self.project.main_path):
            marker = (pin["at"], pin["plan"], pin["file"])
            if self._seen.get(pin["id"]) == marker and self.registry.pin_file(pin["id"]) == pin["file"]:
                continue
            self._seen[pin["id"]] = marker
            changed = self._accept(pin) or changed
        return changed

    def _accept(self, pin: dict) -> bool:
        registry, tab = self.registry, pin["id"]
        key = dashboard_worktrees.normalize_key(pin["path"])
        entry = next((e for e in dashboard_worktrees.list_entries(self.project) if e.id == tab and dashboard_worktrees.normalize_key(e.path) == key), None)
        if entry is None:
            self._refuse(pin, "to nie jest główny katalog ani worktree tego projektu z poprawnym powiązaniem zwrotnym")
            return False
        if entry.kind != "main":
            try:
                exclude = dashboard_token.worktree_settings(self.project, self.root, self.manifest)["exclude"]
            except ValueError as exc:   # fail closed: the exclusion list cannot be read, so nothing is added
                log(f"UWAGA: dashboard.worktrees nieczytelne, przypięcie {tab} odrzucone: {exc}")
                self._refuse(pin, "nie można odczytać dashboard.worktrees.exclude")
                return False
            if dashboard_worktrees.is_excluded(entry.path, manifest_exclude=exclude, main_path=self.project.main_path):
                self._refuse(pin, "worktree jest wykluczony")
                return False
        board = registry.board(tab)
        if board is not None and dashboard_worktrees.normalize_key(board.root) == key:
            registry.pin(tab, pin["file"])   # already a tab: the pin keeps it and may bring a plan
            self._apply_plan(board, pin["plan"])
            return False
        victim = None
        if len(registry.ids()) >= dashboard_worktrees.MAX_VISIBLE:
            victim = registry.evictable()
            if victim is None:
                self._refuse(pin, f"limit {dashboard_worktrees.MAX_VISIBLE} przypiętych zakładek")
                return False
        try:   # first the new tab; the victim goes only when it stands (a failure leaves the registry as it was)
            board = registry._new_board(entry.path)
            self._apply_plan(board, pin["plan"])
            registry.register(entry, board)
        except (ValueError, OSError) as exc:
            self._refuse(pin, str(exc))
            return False
        registry.pin(tab, pin["file"])
        if victim is not None:
            registry.deregister(victim)
        return True


class Observer:
    """The ONE background thread of a server (CORE-1180 M-4): it replaces `Board.watch_loop` per tab and the thread of the pins. Each `tick`:

    - pins: one `scandir` of the pin directory (`PinWatcher.poll`); a changed directory is read and the new tabs appear at once;
    - the list of the project (every `REGISTRY_INTERVAL` seconds, project servers only): `discover` with the exclusion and `inactive_days` of the MAIN
      manifest, then `TabRegistry.reconcile` (new worktrees become tabs, a vanished one goes after two refreshes); the pins are read in full then too;
    - the signature of the boards: only of a WATCHED board (a client of its SSE, a request for it in the last `WATCH_GRACE_S` seconds, or the only tab of
      the server: then nothing is saved), each at the `poll` of the board; a changed signature is a `change` event of that tab. The other tabs have
      the light summary the refresh keeps (`worktrees[]`: HEAD, last activity).

    No tick starts a process: HEAD and the refs come from `.git` files (`Board.watched_paths` through `git_locations`), the feedback store from `commondir`
    (`Board._feedback_store`), the list from `<commondir>/worktrees`. An error never ends the thread (logged once a minute); the thread ends with `stop`.
    Tests drive it without the thread: `tick(now)` with their own clock values (`run_once` is the same call); `stats` counts what a tick did."""

    REGISTRY_INTERVAL = 2.0
    WATCH_GRACE_S = 30.0
    SLOW_TICK_S = 5.0   # a pass that takes longer than this (the clock of the observer) is logged
    MAX_LOGGED = 64

    def __init__(self, registry: "TabRegistry", stop: threading.Event, *, project=None, root: Path | None = None, manifest: dict | None = None,
                 pins: "PinWatcher | None" = None, clock=time.monotonic):
        self.registry = registry
        registry.set_clock(clock)
        self.stop = stop
        self.project = project
        self.root = root
        self.manifest = manifest
        self.pins = pins
        self.clock = clock
        default = registry.board(registry.default_id)
        poll = default.poll if default is not None else 1.0
        self.interval = PinWatcher.INTERVAL if pins is not None else poll   # the pins are looked at five times a second, a server without them at the pace of its boards
        self.stats = {"ticks": 0, "refreshes": 0, "checks": 0, "errors": 0}
        self._next_refresh = clock() + self.REGISTRY_INTERVAL   # the start has just made the list
        self._logged: dict[str, float] = {}   # key -> time of its last log line (at most `MAX_LOGGED` keys)
        self._thread: threading.Thread | None = None

    # --- the thread
    def start(self) -> "Observer":
        self._thread = threading.Thread(target=self._loop, name="observer", daemon=True)
        self._thread.start()
        return self

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self.stop.wait(self.interval):   # `stop` is the stop event of the server (set once, when the server ends)
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - the observer must not die
                self._error("observer", exc)

    def _error(self, key: str, exc: BaseException) -> None:
        self.stats["errors"] += 1
        self._log_limited(key, f"obserwator: {exc!r}")

    def _log_limited(self, key: str, text: str) -> None:
        """A lasting condition is logged once a minute per key, not five times a second; the table of keys stays small (one key per broken board would grow)."""
        now = time.monotonic()
        last = self._logged.get(key)
        if last is None or now - last >= 60:
            if len(self._logged) >= self.MAX_LOGGED:
                for old in sorted(self._logged, key=self._logged.get)[:self.MAX_LOGGED // 2]:
                    del self._logged[old]
            self._logged[key] = now
            log(text)

    # --- one pass
    def watched(self, board: "Board", now: float) -> bool:
        """Does anybody look at this board: a stream of its SSE, a request in the last `WATCH_GRACE_S` seconds, or it is the only tab."""
        if board.sse_local > 0 or len(self.registry.ids()) <= 1:
            return True
        return board.last_request is not None and now - board.last_request < self.WATCH_GRACE_S

    def tick(self, now: float | None = None) -> dict:
        """One pass (see the class); returns `{"tabs": bool, "changed": [roots of the boards whose signature moved]}`. Never raises for a bad tab."""
        began = self.clock()
        now = began if now is None else now
        self.stats["ticks"] += 1
        tabs_changed = False
        due = self.project is not None and now >= self._next_refresh
        if self.pins is not None:
            try:
                tabs_changed = self.pins.poll(force=due) or tabs_changed
            except Exception as exc:  # noqa: BLE001
                self._error("pins", exc)
        if due:
            started = self.clock()
            try:
                tabs_changed = self.refresh() or tabs_changed
            except Exception as exc:  # noqa: BLE001
                self._error("registry", exc)
            # The next refresh is one interval after this one ENDED: a refresh longer than the interval must not make the next pass refresh again at once
            # (the time it took is the distance of the clock before and after, so a test that hands `now` in by hand moves by the same amount).
            self._next_refresh = now + max(0.0, self.clock() - started) + self.REGISTRY_INTERVAL
        changed = []
        for board in self.registry.boards():
            if board.stop.is_set() or now < board.next_check or not self.watched(board, now):
                continue
            board.next_check = now + board.poll
            self.stats["checks"] += 1
            try:
                if board.check_once():
                    changed.append(str(board.root))
            except Exception as exc:  # noqa: BLE001 - one broken tab does not stop the others
                self._error(f"board:{board.root}", exc)
        took = self.clock() - began
        if took > self.SLOW_TICK_S:
            self._log_limited("slow-tick", f"obserwator: jedno przejście trwało {took:.1f} s (próg {self.SLOW_TICK_S:.0f} s)")
        return {"tabs": tabs_changed, "changed": changed}

    run_once = tick

    def refresh(self) -> bool:
        """The list of the project now: `discover` (exclusion and `inactive_days` of the main manifest, the pinned tabs kept) and `reconcile`. An unreadable
        `dashboard.worktrees` changes nothing (fail closed for the additions, nothing is dropped for a typo)."""
        if self.project is None:
            return False
        self.stats["refreshes"] += 1
        try:
            settings = dashboard_token.worktree_settings(self.project, self.root, self.manifest)
        except ValueError as exc:
            self._error("settings", exc)
            self.registry.refresh_waiting()   # the flags come from the files of the tabs, not from the settings
            return False
        found = dashboard_worktrees.discover(self.project, pinned=self.registry.pinned_paths(), manifest_exclude=settings["exclude"],
                                             inactive_days=settings["inactive_days"])
        changed = self.registry.reconcile(found)
        self.registry.refresh_waiting()
        return changed


def _prepare_registry(board: "Board", serve_plan, manifest: dict | None) -> tuple["TabRegistry", Path]:
    """The registry of the server over the `board` of the directory it was started in, and the root the state files are named after. A project server
    (`serve_plan.kind == "project"`) gets a tab for the main checkout and each worktree that passes R1-R3 (the start directory is pinned); a standalone
    server has the one tab of its directory, as before."""
    project = serve_plan.project if serve_plan.kind == "project" else None
    registry = TabRegistry(board, project=project)
    board._tabs = registry   # `make_server` reuses it: both sockets of the two-port mode share one registry
    state_root = board.root
    if project is None:
        return registry, state_root
    if project.main_path and os.path.isdir(project.main_path):
        state_root = Path(project.main_path)   # one state file name per project, whichever directory started the server
    try:
        settings = dashboard_token.worktree_settings(project, board.root, manifest)
    except ValueError as exc:   # fail closed: without the exclusion list no other directory becomes a tab (the start directory stays)
        log(f"UWAGA: dashboard.worktrees nieczytelne ({exc}); serwer startuje tylko z zakładką tego katalogu")
        registry.pin(registry.default_id)
        return registry, state_root
    try:
        found = dashboard_worktrees.discover(project, pinned=[str(board.root)], manifest_exclude=settings["exclude"],
                                             inactive_days=settings["inactive_days"], warn=lambda message: log(f"UWAGA: {message}"))
        for entry in found.tabs:
            registry.register(entry)
    except Exception as exc:  # noqa: BLE001 - a broken neighbour must not stop the server: it starts with the tabs it has
        log(f"UWAGA: rejestr zakładek: {exc!r}")
    registry.pin(registry.default_id)
    return registry, state_root


def _start_observer(registry: "TabRegistry", board: "Board", serve_plan, manifest: dict | None) -> "Observer":
    """The one thread of the server: the observer (with the pins and the refresh of the list for a project server)."""
    if serve_plan.kind != "project":
        return Observer(registry, board.stop).start()
    pins = PinWatcher(registry, serve_plan.project, board.root, manifest, board.stop)
    return Observer(registry, board.stop, project=serve_plan.project, root=board.root, manifest=manifest, pins=pins).start()


def _stop_observer(observer: "Observer | None") -> None:
    """The server ends (`board.stop` is set): the observer's thread ends with it; wait for it so no thread outlives the server."""
    if observer is not None:
        observer.join(3.0)
        if observer.alive():
            log("UWAGA: obserwator nie zakończył się w 3 s po zatrzymaniu serwera")


def _state_kwargs(registry: "TabRegistry", serve_plan) -> dict:
    return {"server_id": registry.server_id, "kind": serve_plan.kind, "common_dir": serve_plan.project.common_dir if serve_plan.project else None,
            "tabs": registry.tabs_state(), "terminal": dashboard_token.terminal_info()}


def _tab_url(registry: "TabRegistry", serve_plan, port: int, host: str) -> str:
    """The address the start prints for the default tab: a project server needs the tab and its own token (`id.token`); a standalone one keeps the
    address of earlier versions (the bare token)."""
    board = registry.board(registry.default_id)
    if serve_plan.kind == "project":
        return dashboard_token.tab_address(port, registry.default_id, registry.token_for(registry.default_id), host)
    return dashboard_token.address(port, board.approve_token, host)


def _follow_tabs(registry: "TabRegistry", state_root: Path, written: list[int]) -> None:
    """Keep `tabs[]` of every state file of this server equal to the registry (a pin made a tab)."""
    def refresh() -> None:
        tabs = registry.tabs_state()
        for port in list(written):
            dashboard_token.refresh_tabs(state_root, port, tabs, pid=os.getpid())
    registry.add_listener(refresh)
    refresh()


def _announce(serve_plan, registry: "TabRegistry") -> None:
    """Messages of the start (stderr): the notes of the plan, and for a project server where it runs and what closing it does."""
    for key, params in serve_plan.notes:
        log(i18n.t(serve_plan.lang, key, **params))
    if serve_plan.kind == "project":
        log(i18n.t(serve_plan.lang, "srv.serve.startProject", pid=os.getpid(), terminal=dashboard_token._terminal_text(dashboard_token.terminal_info())))


def serve_command(args) -> int:
    """`skillsync dashboard serve`: foreground server (Ctrl+C or SIGTERM stops); BLOCKED (exit 2) when it may not start.
    `--read-port` / `--approve-port`: the two-port mode (`_serve_ports`); otherwise the single-port mode of 1.5.0, unchanged."""
    try:
        settings = None
        if getattr(args, "port", None) is None:   # the `dashboard:` setting only matters for the two-port mode
            _root, manifest = read_project(args.path)
            unknown = dashboard_listen.blocking_unknown_keys(manifest)
            if unknown:
                raise ValueError(unknown)
            settings = dashboard_listen.settings(manifest)
        mode = ports.resolve(args, port_problem, settings)
    except (LayerError, TypeError, yaml.YAMLError) as error:
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    except ValueError as error:
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    serve_plan = dashboard_token.plan_serve(args)   # a running project server of this git project and no flag of settings: add a tab, open no socket
    if serve_plan.mode == "append":
        code = dashboard_token.append_command(args, serve_plan)
        if code is not None:
            return code
        serve_plan = dashboard_token.plan_serve(args)   # the server stopped between the two looks: decide again (it starts one)
    if serve_plan.legacy:
        first = serve_plan.legacy[0]
        return dashboard_token.blocked_report(args, "legacy_server", i18n.t(serve_plan.lang, "srv.serve.legacy", port=first["port"], pid=first["pid"]))
    if mode is not None:
        return _serve_ports(args, mode, serve_plan)
    if getattr(args, "port", None) is None:   # the single-port defaults (the parser leaves them unset to tell the modes apart)
        args.port = DEFAULT_PORT
    if getattr(args, "host", None) is None:   # only "not given": an explicit `--host ''` keeps meaning every interface, as in 1.5.0
        args.host = "127.0.0.1"
    try:
        root, manifest, layerset = load_layer_project(args.path, args.layers)
        problem = port_problem(args.port)
        if problem:
            raise ValueError(problem)
        if args.session_url and not session_url_ok(args.session_url):
            raise ValueError("--session-url: tylko adres https bez danych logowania.")
        plan = args.plan.replace(os.sep, "/").removeprefix("./") if args.plan else None
        if plan and engine.resolve_doc(root, plan) is None:
            raise ValueError(f"Plan {args.plan} nie istnieje albo leży poza dozwolonymi katalogami dokumentów.")
        for flag, names in (("--approve-host", args.approve_host), ("--read-host", getattr(args, "read_host", None) or ())):
            for name in names:
                if not name.strip() or any(c in name for c in ":[]/") or any(c.isspace() for c in name.strip()):
                    raise ValueError(f"{flag} {name!r}: podaj samą nazwę hosta, bez portu, nawiasów, ukośników i spacji.")
        assets, descriptions = skill_assets(layerset)
        if "index.html" not in assets:
            raise ValueError(f"Brak strony: żadna aktywna warstwa nie ma skills/{SKILL}/assets/index.html "
                             f"(warstwy: {', '.join(layerset.ids) or 'brak'}).")
        board = Board(root, plan, poll=max(0.2, args.poll), approvals=not args.no_approvals,
                      prototype_port=_prototype_port(args, manifest, root),
                      session_url=args.session_url, approve_hosts=tuple(args.approve_host),
                      read_hosts=tuple(getattr(args, "read_host", None) or ()), assets=assets,
                      descriptions=descriptions, manifest=manifest)
        registry, state_root = _prepare_registry(board, serve_plan, manifest)
        server = make_server(board, args.host, args.port, args.verbose)
    except OSError as error:
        return emit({"status": "BLOCKED", "reason": f"nie można nasłuchiwać na {args.host}:{args.port} "
                                                    f"({error.strerror or error})"})
    except (LayerError, ValueError, TypeError, yaml.YAMLError) as error:
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    host, port = server.server_address[:2]
    url = f"http://{'[%s]' % host if ':' in host else host}:{port}/"
    cleanup_done, console_close = threading.Event(), None
    written: list[int] = []
    observer = None
    try:
        # Everything from the SIGTERM handler to `serve_forever` is under the `try` (SIGINT in the start window stops cleanly).
        console_close = _install_stop_handlers(cleanup_done)
        log(f"projekt {board.root} → {url}  (Ctrl+C kończy)")
        if board.approvals:
            # The only place where the token is printed: a fragment goes neither to the server nor to a proxy. The page keeps it in the
            # browser; `skillsync dashboard url <project>` prints the same address for a running server (state file outside the project).
            token_host = dashboard_token.bind_name(host)   # where the server really listens (a wildcard bind: loopback): the probe of `dashboard url`
            shown_host = dashboard_token.address_host(host, board.approve_hosts)   # a name the page accepts for consents
            log(f"zgody (przyciski na stronie): otwórz {_tab_url(registry, serve_plan, port, shown_host)} "
                f"(bez fragmentu strona działa tylko do odczytu; adres z tokenem: `skillsync dashboard url`)")
            for name in sorted(board.approve_hosts - LOOPBACK_HOSTS):
                log(f"  zdalnie ({name}, --approve-host): {_tab_url(registry, serve_plan, port, name)}")
            dashboard_token.write_state(state_root, port, board.approve_token, token_host, warn=lambda text: log(f"UWAGA: {text}"),
                                        hosts=sorted(board.approve_hosts - LOOPBACK_HOSTS), **_state_kwargs(registry, serve_plan))
        else:
            # no token: the file only says `approvals: false` (replaces a leftover of a killed server with consents on this port)
            dashboard_token.write_state(state_root, port, None, dashboard_token.bind_name(host), warn=lambda text: log(f"UWAGA: {text}"),
                                        **_state_kwargs(registry, serve_plan))
        written.append(port)
        _announce(serve_plan, registry)
        if serve_plan.kind == "project":
            dashboard_token.sweep_pins()
            _follow_tabs(registry, state_root, written)
        observer = _start_observer(registry, board, serve_plan, manifest)
        extra = sorted(board.approve_hosts - LOOPBACK_HOSTS)
        readers = sorted(board.read_hosts - board.approve_hosts)
        if extra:
            log(f"hosty odczytu i zgód (--approve-host): {', '.join(extra)}"
                f"{' (zgody wyłączone: --no-approvals)' if not board.approvals else ''}")
        if readers:
            log(f"hosty tylko do odczytu (--read-host, bez zgód): {', '.join(readers)}")
        if readers and board.approvals:
            log("UWAGA: --read-host ogranicza zapis z przeglądarki, nie przed klientem podrabiającym nagłówek Host; "
                "przy nieufnych widzach użyj --no-approvals (osobna instancja bez zapisu: "
                f"skillsync dashboard serve <projekt> --port <inny port> --no-approvals --read-host {readers[0]}).")
        shadowed = sorted(board.read_hosts & board.approve_hosts)
        if shadowed:
            log(f"UWAGA: {', '.join(shadowed)} z --read-host jest też loopbackiem albo w --approve-host, więc daje zgody.")
        if args.host not in LOOPBACK_HOSTS:
            if not extra and not readers:
                log(f"UWAGA: nasłuch na {args.host}, ale strona odpowiada tylko pod localhost (nagłówek Host); inne urządzenia "
                    f"dostaną 403, dopóki ich adres z paska przeglądarki (nazwa lub IP) nie trafi do --approve-host "
                    f"albo --read-host.")
            elif not extra:
                log(f"UWAGA: nasłuch na {args.host}; strona (tylko odczyt) dostępna pod: {', '.join(readers)} — używaj "
                    f"w zaufanej sieci.")
            else:
                log(f"UWAGA: nasłuch na {args.host}; strona {'(tylko odczyt) ' if not board.approvals else 'i zgody '}"
                    f"dostępne pod: {', '.join(extra)} — używaj w zaufanej sieci"
                    f"{'' if not board.approvals else ' (--no-approvals = tylko odczyt)'}.")
            for ip in lan_addresses() if args.host in ("0.0.0.0", "::", "") else [args.host]:
                log(f"  z innego urządzenia: http://{ip}:{port}/ (wymaga --approve-host {ip} albo --read-host {ip})")
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stopsignal.cleanup_begun(console_close)   # a closed console window only waits from here on
        try:
            board.stop.set()
            with board.cond:
                board.cond.notify_all()
            registry.stop_all()
            _stop_observer(observer)
            server.server_close()
            dashboard_token.remove_state(state_root, port, os.getpid())
            dashboard_token.remove_pins(registry.server_id)
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "url": url, "stopped": True})


def _serve_ports(args, mode: dict, serve_plan=None) -> int:
    """The two-port mode: one `Board`, one watcher, a server per socket (`read`: no write route at all; `approve`: token, strict Origin,
    no proxy headers). Both sockets are bound BEFORE any thread, state file or console line, so a failure of the second leaves nothing."""
    servers: list[tuple[str, Server, int, str]] = []
    failing = None
    serve_plan = serve_plan or dashboard_token.ServePlan()   # no plan: the server of today (one tab, standalone)
    try:
        root, manifest, layerset = load_layer_project(args.path, args.layers)
        if args.session_url and not session_url_ok(args.session_url):
            raise ValueError("--session-url: tylko adres https bez danych logowania.")
        plan = args.plan.replace(os.sep, "/").removeprefix("./") if args.plan else None
        if plan and engine.resolve_doc(root, plan) is None:
            raise ValueError(f"Plan {args.plan} nie istnieje albo leży poza dozwolonymi katalogami dokumentów.")
        for flag, names in (("--approve-host", args.approve_host), ("--read-host", getattr(args, "read_host", None) or ())):
            for name in names:
                if flag == "--read-host" and name.strip() == "*":
                    continue   # any Host name, on the read socket only
                if not name.strip() or any(c in name for c in ":[]/*") or any(c.isspace() for c in name.strip()):
                    raise ValueError(f"{flag} {name!r}: podaj samą nazwę hosta, bez portu, nawiasów, ukośników i spacji.")
        assets, descriptions = skill_assets(layerset)
        if "index.html" not in assets:
            raise ValueError(f"Brak strony: żadna aktywna warstwa nie ma skills/{SKILL}/assets/index.html "
                             f"(warstwy: {', '.join(layerset.ids) or 'brak'}).")
        board = Board(root, plan, poll=max(0.2, args.poll), approvals=mode["approve"] is not None,
                      prototype_port=_prototype_port(args, manifest, root), session_url=args.session_url,
                      approve_hosts=tuple(args.approve_host), read_hosts=tuple(getattr(args, "read_host", None) or ()),
                      assets=assets, descriptions=descriptions, manifest=manifest)
        registry, state_root = _prepare_registry(board, serve_plan, manifest)
        read_port = mode["read"]["port"] if mode["read"] else None
        approve_port = mode["approve"]["port"] if mode["approve"] else None
        for role, peer in ((ports.ROLE_READ, approve_port), (ports.ROLE_APPROVE, read_port)):
            spec = mode[role]
            if spec:
                failing = spec["port"]
                if ports.port_taken(spec["bind"], spec["port"]):
                    raise OSError(errno.EADDRINUSE, "Address already in use")
                servers.append((role, make_server(board, spec["bind"], spec["port"], args.verbose, role=role, peer_port=peer,
                                                  allow_proxy_headers=mode["allow_proxy"] and role == ports.ROLE_APPROVE), spec["port"], spec["bind"]))
                failing = None
    except OSError as error:
        for _role, server, _port, _bind in servers:
            server.server_close()
        if serve_plan.race and serve_plan.project is not None:   # the port may be held by a project server that started a moment ago: join it
            code = dashboard_token.retry_append(args, serve_plan)
            if code is not None:
                return code
        wanted = "/".join(str(mode[role]["port"]) for role in (ports.ROLE_READ, ports.ROLE_APPROVE) if mode[role])
        owner = dashboard_token.port_owner(failing) if failing else None
        who = f"; port {failing} trzyma {owner}" if owner else (f"; port {failing} zajęty przez inny proces" if failing else "")
        return emit({"status": "BLOCKED", "reason": f"nie można nasłuchiwać ({error.strerror or error}); porty {wanted} muszą być wolne{who}. "
                                                    f"Wskaż inne: --read-port N --approve-port M (albo --port N: tryb jednego portu)."})
    except (LayerError, ValueError, TypeError, yaml.YAMLError) as error:
        for _role, server, _port, _bind in servers:
            server.server_close()
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    running: list[Server] = []   # servers whose `serve_forever` thread started (only those can be `shutdown()`)
    states: list[int] = []   # the ports whose state file this server wrote
    cleanup_done, console_close = threading.Event(), None
    observer = None
    try:
        console_close = _install_stop_handlers(cleanup_done)
        for role, server, port, bind in servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, name=f"serve-{role}", daemon=True)
            thread.start()
            running.append(server)
        warn = lambda text: log(f"UWAGA: {text}")  # noqa: E731
        log(f"projekt {board.root} (Ctrl+C kończy)")
        approve_hosts = sorted(board.approve_hosts - LOOPBACK_HOSTS)
        for role, server, port, bind in servers:
            local = dashboard_token.bind_name(bind)
            peer = mode["approve" if role == ports.ROLE_READ else "read"]
            peer_port = peer["port"] if peer else None
            if role == ports.ROLE_READ:
                log(f"odczyt (do tunelu i linków): {dashboard_token.read_address(port, local)}  [nasłuch {bind}:{port}, bez zgód i tokenu]")
                dashboard_token.write_state(state_root, port, None, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port, bind=bind,
                                            **_state_kwargs(registry, serve_plan))
            else:
                shown = dashboard_token.address_host(bind, board.approve_hosts)
                log(f"zgody (przyciski, tylko dla Ciebie, nigdy do tunelu): {_tab_url(registry, serve_plan, port, shown)}  [nasłuch {bind}:{port}]")
                dashboard_token.write_state(state_root, port, board.approve_token, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port,
                                            **_state_kwargs(registry, serve_plan))
            states.append(port)
            if not ports.is_loopback_bind(bind):
                if role == ports.ROLE_APPROVE:
                    log(f"UWAGA: port zgód {port} słucha na {bind}: widzi go każde urządzenie w sieci, a jedyną barierą jest token przesyłany "
                        f"nieszyfrowanym HTTP (podsłuch w tej sieci wystarczy do przechwycenia go). W nieufnej sieci uruchom z "
                        f"--approve-bind 127.0.0.1 albo użyj tunelu SSH; nigdy nie kieruj tunelu publicznego na ten port.")
                else:
                    log(f"UWAGA: port odczytu {port} słucha na {bind}: odczyt jest dostępny dla każdego urządzenia w tej sieci lokalnej (plany, spece, "
                        f"wyniki; tajne dane zredagowane heurystycznie, ale wyniki testów (nazwy i komunikaty porażek) są jawne, bez maski; ruch nieszyfrowany). W nieufnej sieci uruchom z --read-bind 127.0.0.1 "
                        f"albo ustaw dashboard.local_only: true w configu maszyny.")
                    if mode.get("read_default") and _version_tuple(__version__) < ports.LISTEN_NOTE_UNTIL:
                        log("  od 1.8.0 odczyt słucha domyślnie na wszystkich interfejsach; poprzedni nasłuch tylko lokalny przywraca "
                            "--read-bind 127.0.0.1 albo dashboard.local_only: true.")
                if bind in ("0.0.0.0", "::", ""):
                    for ip in lan_addresses():
                        log(f"  z innego urządzenia: http://{ip}:{port}/")
                    if role == ports.ROLE_READ:
                        names = sorted(name for name in ports.own_names() if name.endswith(".local")) or sorted(ports.own_names())[:1]
                        for name in names:
                            log(f"  albo po nazwie tej maszyny: http://{name}:{port}/")
                    if role == ports.ROLE_READ and os.name == "nt":
                        log("  Zapora Windows może zapytać o dostęp: „Zezwól” w sieci prywatnej daje podgląd z telefonu, odmowa zostawia stronę lokalnie.")
        approve_spec, read_spec = mode["approve"], mode["read"]
        if approve_spec and ports.is_loopback_bind(approve_spec["bind"]) and read_spec and not ports.is_loopback_bind(read_spec["bind"]):
            log("zgody są tylko na tej maszynie; zgody z telefonu: --approve-bind 0.0.0.0 (token idzie nieszyfrowanym HTTP; tylko zaufana sieć)")
        if all(ports.is_loopback_bind(bind) for _role, _server, _port, bind in servers):
            log("strona tylko na tej maszynie (nasłuch na loopbacku)")
        _announce(serve_plan, registry)
        if serve_plan.kind == "project":
            dashboard_token.sweep_pins()
            _follow_tabs(registry, state_root, states)
        observer = _start_observer(registry, board, serve_plan, manifest)
        while not board.stop.is_set():
            board.stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stopsignal.cleanup_begun(console_close)   # a closed console window only waits from here on
        try:
            board.stop.set()
            with board.cond:
                board.cond.notify_all()
            for server in running:
                server.shutdown()
            for _role, server, _port, _bind in servers:
                server.server_close()
            registry.stop_all()
            _stop_observer(observer)
            for port in states:
                dashboard_token.remove_state(state_root, port, os.getpid())
            dashboard_token.remove_pins(registry.server_id)
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "stopped": True, "ports": {role: port for role, _server, port, _bind in servers}})
