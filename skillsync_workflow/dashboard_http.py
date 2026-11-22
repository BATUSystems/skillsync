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
import hmac
import html
import json
import os
import posixpath
import re
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
from . import dashboard_listen
from . import dashboard_ports as ports
from . import dashboard_tests
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


def load_descriptions(path: Path | None) -> dict:
    """Static descriptions of steps, acts and gates (tooltips): `descriptions.json` of the skill, `{key: {title, text}}`."""
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
                 approve_token: str | None = None):
        self.root = Path(root).resolve()
        self.approvals = approvals
        # Per process, never in a status, asset, log or error report (see `dashboard_token`); None = no consents at all.
        self.approve_token = (approve_token or dashboard_token.new_token()) if approvals else None
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
        self.sse_clients = 0
        self.sse_by_role: dict[str, int] = {}   # streams of the two-port sockets, by role (`sse_clients` stays the total)
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
        git = self.root / ".git"
        paths.extend([git / "HEAD", git / "packed-refs"])
        branch = engine.read_git(self.root).get("branch")
        if branch:
            paths.append(git / "refs/heads" / branch)
        # Push and fetch change origin refs: the count of commits to push (CP) refreshes without project file changes.
        base = engine.read_delivery(self.manifest()).get("base")
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
                plan_state: str | None = None) -> tuple[int, dict]:
        """Consent from a button: only the action the page offers right now (the same file and sha).

        GO: `status.set_doc_status(..., expect_sha=sha)` rewrites the Status line (the consent itself), then
        `status.record_approval` writes the trail entry to the run's `approvals.jsonl` when the run directory exists
        (without a run there is no trail: `trail` = null; the session reads the consent from the Status line)."""
        lang = self.lang(fresh=True)
        if not self.approvals:
            return 403, {"error": i18n.t(lang, "srv.http.approvalsOff")}
        if kind not in APPROVE_KINDS or not isinstance(rel, str) or not isinstance(sha, str):
            return 400, {"error": i18n.t(lang, "srv.http.badRequest")}
        if kind in ("cp", "ready"):
            if plan_state is not None and (not isinstance(plan_state, str) or plan_state not in status_core.PLAN_STATUSES):
                return 400, {"error": i18n.t(lang, "srv.http.badRequest")}
            return self._approve_delivery(kind, rel, sha, plan if isinstance(plan, str) else None, via, plan_state)
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
            try:
                report = status_core.set_doc_status(self.root, rel, "approve", folder, expect_sha=sha)
            except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                return 409, {"error": i18n.t(lang, "srv.http.statusRefused", reason=status_core._message(error, 300))}
            run_id = offer.get("run")
            trail = None
            if run_id:
                record = status_core.record_approval(self.root, word, run_id, sha=sha, source="dashboard", file=rel,
                                                     sha_after=report.get("sha"), via=via)
                if record["status"] == "PASS":
                    trail = record["path"]
                else:
                    log(f"ślad zgody „{word}” nie zapisany: {record['reason']}")
            log(f"zgoda „{word}” dla {rel} (sha {sha[:10]}, ślad: {trail or 'brak przebiegu'})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": rel, "trail": trail, "next": i18n.t(lang, "srv.http.nextHint")}

    def _approve_delivery(self, kind: str, run_id: str, sha: str, plan: str | None = None, via: dict | None = None,
                          plan_state: str | None = None) -> tuple[int, dict]:
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
            record = status_core.record_approval(
                self.root, word, run_id, sha=sha, source="dashboard", target=d.get("target") if kind == "cp" else None,
                branch=d.get("branch"), plan=(current.get("plan") or {}).get("path"), ahead=d.get("ahead"),
                mode=d.get("mode"), via=via, expect_plan_state=plan_state)
            if record.get("code") == "plan_state_changed":
                return 409, {"error": i18n.t(lang, "srv.http.planStateChanged"), "code": "plan_state_changed"}
            if record["status"] != "PASS":
                log(f"wpis zgody „{word}” nie zapisany: {record['reason']}")
                return 500, {"error": i18n.t(lang, "srv.http.saveFailed", reason=record["reason"])}
            log(f"zgoda „{word}” dla HEAD {sha[:10]} (przebieg {run_id})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": record["path"], "next": i18n.t(lang, "srv.http.nextHint")}


# --------------------------------------------------------------------------- HTTP

class Handler(LingeringCloseMixin, BaseHTTPRequestHandler):
    server_version = f"dashboard/{__version__}"
    board: Board  # set in make_server
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

    def do_GET(self) -> None:  # noqa: N802
        if self._host_refused():
            return None
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
                here, viewer = self._approvals_role()
                data["approvals"]["here"] = here
                data["approvals"]["viewer"] = viewer
                token_required = bool(self.board.approve_token)   # only the fact: the value never leaves the process
                if self.role != ports.ROLE_BOTH:   # two-port mode: the role is the socket, never the header
                    data["approvals"]["role"] = self.role
                    token_required = token_required and self.role == ports.ROLE_APPROVE
                data["approvals"]["tokenRequired"] = token_required
                return self._json(200, dashboard_tests.finalize_status(data, test_events.mask_setting(self.board.manifest())))  # as `dashboard show`: the viewer sees what the operator sees; secrets masked except the raw `tests` (decision 6)
            if route == "/api/tests":   # read-only on BOTH sockets, like /api/status (the same rule: no write route anywhere near it)
                one = lambda name: (query.get(name) or [None])[0]
                try:
                    body = dashboard_tests.tests_response(self.board.root, one("run"), one("step"), one("attempt") or "0", one("group"), one("limit") or 500, one("offset") or 0)
                except (ValueError, TypeError):
                    body = None
                if body is None:
                    return self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.noTestRun")})
                return self._json(200, dashboard_tests.finalize_response(body, test_events.mask_setting(self.board.manifest())))
            if route == "/api/doc":
                doc = self.board.doc((query.get("path") or [None])[0])
                if doc is None:
                    return self._json(404, {"error": i18n.t(self.board.lang(), "srv.http.docUnavailable")})
                return self._json(200, doc)
            if route == "/events":
                if self.command == "HEAD":  # headers only; takes no SSE slot and opens no stream
                    return self._send(200, b"", "text/event-stream; charset=utf-8")
                return self._events()
            if route == "/api/descriptions":
                return self._json(200, load_descriptions(self.board.descriptions))
            if route == "/healthz":
                answer = {"ok": True, "version": self.board.version}
                if self.board.approve_token and self.role != ports.ROLE_READ:   # a short digest, never the token: `dashboard url` tells its server from a stranger on the port
                    answer["tokenId"] = dashboard_token.token_id(self.board.approve_token)
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

    def _approve_allowed(self) -> str | None:
        """Reason to refuse a write or None: loopback Host (also against DNS rebinding and tunnels), own header (forces
        a CORS preflight the server does not answer), matching Origin when the browser sent one, JSON content type."""
        allowed = self.board.approve_hosts
        host = request_host(self.headers.get("Host"))
        strict = self.role == ports.ROLE_APPROVE   # the consent socket of the two-port mode: exact Origin, no intermediary headers
        lang = self.board.lang()
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
            given = self.headers.get(TOKEN_HEADER) or ""
            if not hmac.compare_digest(given.encode("utf-8", "replace"), self.board.approve_token.encode("utf-8")):
                if strict:
                    line = self.board.token_failures.add(self.client_address[0])
                    if line:
                        log(line)
                return i18n.t(lang, "srv.http.tokenRefused")
        return None

    def do_POST(self) -> None:  # noqa: N802
        if self._host_refused():
            return None
        route = urlparse(self.path).path
        try:
            lang = self.board.lang()
            if route != "/api/approve":
                return self._json(404, {"error": i18n.t(lang, "srv.http.noPage")})
            refused = self._approve_allowed()
            if refused:
                return self._json(403, {"error": refused, "code": "approve_token"} if refused == i18n.t(lang, "srv.http.tokenRefused") else {"error": refused})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._json(400, {"error": i18n.t(lang, "srv.http.badLength")})
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                return self._json(400, {"error": i18n.t(lang, "srv.http.badRequest")})
            via = self._via() if self.role == ports.ROLE_APPROVE else None
            code, data = self.board.approve(body.get("kind"), body.get("path"), body.get("sha"), body.get("plan"), via,
                                           body.get("planState"))
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
        self._send(200, body, ASSET_TYPES[path.suffix])

    def _events(self) -> None:
        board = self.board
        with board.cond:
            limit = SSE_ROLE_LIMITS.get(self.role)
            if board.sse_clients >= MAX_SSE_CLIENTS or (limit is not None and board.sse_by_role.get(self.role, 0) >= limit):
                return self._json(503, {"error": i18n.t(board.lang(), "srv.http.tooManyBrowsers")})
            board.sse_clients += 1
            if limit is not None:
                board.sse_by_role[self.role] = board.sse_by_role.get(self.role, 0) + 1
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
                if self.role in SSE_ROLE_LIMITS:
                    board.sse_by_role[self.role] = board.sse_by_role.get(self.role, 0) - 1
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


def make_server(board: Board, host: str, port: int, verbose: bool = False, role: str = ports.ROLE_BOTH,
                allow_proxy_headers: bool = False, peer_port: int | None = None) -> Server:
    validate_port(port)
    handler = type("BoundHandler", (ReadHandler if role == ports.ROLE_READ else Handler,), {"board": board, "verbose": verbose, "role": role, "own_port": port,
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
    if mode is not None:
        return _serve_ports(args, mode)
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
        server = make_server(board, args.host, args.port, args.verbose)
    except OSError as error:
        return emit({"status": "BLOCKED", "reason": f"nie można nasłuchiwać na {args.host}:{args.port} "
                                                    f"({error.strerror or error})"})
    except (LayerError, ValueError, TypeError, yaml.YAMLError) as error:
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    host, port = server.server_address[:2]
    url = f"http://{'[%s]' % host if ':' in host else host}:{port}/"
    cleanup_done, console_close = threading.Event(), None
    try:
        # Everything from the SIGTERM handler to `serve_forever` is under the `try` (SIGINT in the start window stops cleanly).
        console_close = _install_stop_handlers(cleanup_done)
        threading.Thread(target=board.watch_loop, name="watcher", daemon=True).start()
        log(f"projekt {board.root} → {url}  (Ctrl+C kończy)")
        if board.approvals:
            # The only place where the token is printed: a fragment goes neither to the server nor to a proxy. The page keeps it in the
            # browser; `skillsync dashboard url <project>` prints the same address for a running server (state file outside the project).
            token_host = dashboard_token.bind_name(host)   # where the server really listens (a wildcard bind: loopback): the probe of `dashboard url`
            shown_host = dashboard_token.address_host(host, board.approve_hosts)   # a name the page accepts for consents
            log(f"zgody (przyciski na stronie): otwórz {dashboard_token.address(port, board.approve_token, shown_host)} "
                f"(bez fragmentu strona działa tylko do odczytu; adres z tokenem: `skillsync dashboard url`)")
            for name in sorted(board.approve_hosts - LOOPBACK_HOSTS):
                log(f"  zdalnie ({name}, --approve-host): {dashboard_token.address(port, board.approve_token, name)}")
            dashboard_token.write_state(board.root, port, board.approve_token, token_host, warn=lambda text: log(f"UWAGA: {text}"),
                                        hosts=sorted(board.approve_hosts - LOOPBACK_HOSTS))
        else:
            # no token: the file only says `approvals: false` (replaces a leftover of a killed server with consents on this port)
            dashboard_token.write_state(board.root, port, None, dashboard_token.bind_name(host), warn=lambda text: log(f"UWAGA: {text}"))
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
            server.server_close()
            dashboard_token.remove_state(board.root, port, os.getpid())
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "url": url, "stopped": True})


def _serve_ports(args, mode: dict) -> int:
    """The two-port mode: one `Board`, one watcher, a server per socket (`read`: no write route at all; `approve`: token, strict Origin,
    no proxy headers). Both sockets are bound BEFORE any thread, state file or console line, so a failure of the second leaves nothing."""
    servers: list[tuple[str, Server, int, str]] = []
    failing = None
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
    states: list[int] = []
    cleanup_done, console_close = threading.Event(), None
    try:
        console_close = _install_stop_handlers(cleanup_done)
        threading.Thread(target=board.watch_loop, name="watcher", daemon=True).start()
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
                dashboard_token.write_state(board.root, port, None, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port, bind=bind)
            else:
                shown = dashboard_token.address_host(bind, board.approve_hosts)
                log(f"zgody (przyciski, tylko dla Ciebie, nigdy do tunelu): {dashboard_token.address(port, board.approve_token, shown)}  [nasłuch {bind}:{port}]")
                dashboard_token.write_state(board.root, port, board.approve_token, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port)
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
            for port in states:
                dashboard_token.remove_state(board.root, port, os.getpid())
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "stopped": True, "ports": {role: port for role, _server, port, _bind in servers}})
