"""Dashboard HTTP server (`skillsync dashboard serve`): a live presentation over the same JSON as `dashboard show`.

The state of every `/api/status` answer is `dashboard.build_status` (contract `schema: 1`, `docs/dashboard-contract.md`);
this module only adds the live parts: a file watcher with a version counter for SSE (`/events`: `hello`, `change`, `ping`),
the Markdown preview `/api/doc`, the page assets and `descriptions.json` of the `dashboard` skill (read from the active
layers through `LayerSet.skill_files`), and the consent buttons `POST /api/approve`.

Every endpoint (GET, HEAD, SSE and POST) answers only to a `Host` header from loopback (`localhost`, `127.0.0.1`, `::1`,
any port), the IP addresses and the name of this machine, `--approve-host`, `--read-host` or `--public-url`; another `Host` (DNS rebinding,
an unlisted tunnel or LAN name) = 403 without content. Everybody who may reach the page reads everything.

The writes (CORE-2000-UNIFIED): `POST /api/approve` (and `/api/feedback-approve`, `/api/plan-status`) from a SESSION with the role `approver`
(the sessions are `prototype_server.SessionRoutesMixin`: `/_login`, `/_api/v1/session|login|logout|password`), in-process through the shared core
functions of `status.py` (no `skillsync` subprocess): "GO plan"/"GO implement" = `set_doc_status(..., expect_sha=<shown sha>)` and then, when the
run directory exists, `record_approval(..., source='dashboard', by=<account>, role='approver', session=<digest>)`; "CP"/"READY" =
`record_approval(...)` into `.skillsync/runs/<run-id>/approvals.jsonl`. No token and no second password: the session, the CSRF token, the exact
`Origin`, `Sec-Fetch-Site: same-origin`, `X-Status-Board: 1`, `Content-Type: application/json`, body <= 4096 bytes; only locally or through TLS
(`--allow-remote-approvals` lifts that); every refusal and every consent goes to `run/audit.jsonl`; `--no-approvals` turns it off. Standard library only.
"""
from __future__ import annotations

import base64
import errno
import hashlib
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
import types
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
from . import prototype_server as proto_engine
from .prototype_server import LingeringCloseMixin, PrototypeServerError, SessionRoutesMixin

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
# Two-socket mode: each socket has its own share of the streams, so devices that fill the read socket cannot take the live refresh of the page that
# gives consents (the single-port mode and the one `page` socket keep the one pool of 64).
FEEDBACK_DRAFT_RE = re.compile(r"F[1-9][0-9]{0,5}")
FEEDBACK_SHA_RE = re.compile(r"[0-9a-f]{64}")
SSE_ROLE_LIMITS = {ports.ROLE_READ: 48, ports.ROLE_APPROVE: 16, ports.ROLE_PAGE: MAX_SSE_CLIENTS}
MAX_BODY = 4096
BODY_DEADLINE_S = 10.0   # the whole body of a consent must arrive within this time (not each read of it)
# The fields of an approval entry anyone may see: who gave it (`by`, `role`, `session`) and from where (`via`, `worktree`) are only for an approver.
PUBLIC_APPROVAL_FIELDS = ("word", "at", "sha", "source", "target", "planState")
# The routes of the page origin for sessions (answered by `SessionRoutesMixin`); `/_login/link` only while the server has a login link prepared.
SESSION_ROUTES = frozenset({"/_login", proto_engine.LINK_PATH, "/_api/v1/session", "/_api/v1/login", "/_api/v1/logout", "/_api/v1/password",
                            "/_api/v1/handoff",
                            proto_engine.HANDOFF_PATH})
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
APPROVE_KINDS = engine.APPROVE_KINDS
RUNS_DIR = engine.RUNS_DIR
RUN_APPROVALS = engine.APPROVALS_FILE  # .skillsync/runs/<run-id>/approvals.jsonl, the format `skillsync status` reads
MANIFEST = '.github/.skillsync.yaml'
PROTO_CACHE_S = 30
AHEAD_CACHE_S = 10
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


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
    if role == ports.ROLE_PAGE:
        return i18n.t(lang, "srv.http.hostPage", shown=shown)
    return i18n.t(lang, "srv.http.hostBoth", shown=shown)
TAB_ROUTES = frozenset({"/api/status", "/api/doc", "/api/tests", "/events"})   # the GET routes that take `?tab=<id>`
PROXY_REFUSED = i18n.t("pl", "srv.http.proxyRefused")   # the Polish texts of the refusals and the hint; a project in another language gets them from the catalog
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


def _origin_host_of(url: str | None) -> str | None:
    """The host name (no port) of the `--public-url` origin, lower case; None without one."""
    if not url:
        return None
    try:
        return request_host(urlparse(url).netloc) or None
    except ValueError:
        return None


_CONSENT_AUDIT_LOCK = threading.Lock()
_CONSENT_AUDIT_SEEN: dict[tuple, tuple[float, int]] = {}   # (app, source, code, account, route, tab) -> (time of the last written refusal, folded since)


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

def _who_fields(who: dict | None) -> dict:
    """The keyword arguments of `status.record_approval` for `who` (`{by, role, session}`): nothing for a caller without a session."""
    if not who:
        return {}
    return {key: who[key] for key in ("by", "role", "session") if who.get(key)}


class Board:
    """Server state: project, file watcher and the version counter for SSE. The status itself is `build_status`."""

    def __init__(self, root: Path, default_plan: str | None = None, poll: float = 1.0,
                 heartbeat: float = 15.0, prototype_port: int = engine.PROTOTYPE_PORT,
                 approvals: bool = True, session_url: str | None = None, approve_hosts: tuple[str, ...] = (),
                 read_hosts: tuple[str, ...] = (),
                 assets: dict[str, Path] | None = None, descriptions: Path | None = None, manifest: dict | None = None,
                 probe_prototype: bool = True):
        self.root = Path(root).resolve()
        self.approvals = approvals   # False (`--no-approvals`): every consent is refused (`approvals_disabled`); there is no token
        self._tabs: TabRegistry | None = None   # the registry that wraps this board for `make_server` (one per board, so both sockets share it)
        self.session_url = session_url if session_url_ok(session_url) else None
        self.assets = dict(assets or {})
        self.descriptions = descriptions
        self._manifest = manifest
        self._approve_lock = threading.Lock()
        self._ahead: tuple[tuple, float, dict] = ((), 0.0, {})
        self.approve_hosts = LOOPBACK_HOSTS | {h.strip().lower() for h in approve_hosts if h.strip()}
        # Host names of viewers through a tunnel or a proxy (`--read-host`): the page answers under them. Whether a request may CONSENT is decided
        # by its session and by where it comes from (locally or through TLS), never by the Host name.
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
        self._wait_state = None   # (signature, time, flag) of the last full `light_waiting` (`waiting_cached`)
        self.history_count = 0   # `done` + `archived` plans of this tab, counted by `light_waiting` from the plans it lists anyway (the counter of the tab bar)
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

    WAIT_RECHECK_S = 30.0   # `waiting_cached` computes again after this many seconds even when the files look unchanged (a safety net, e.g. a swapped symlink)

    def waiting_signature(self, head12: str | None = None):
        """What `light_waiting` depends on, from `stat` only (no file is opened): name, mtime and size of the plans and specs, of `checkpoints.md` and
        `approvals.jsonl` of every run, and the HEAD the consents are matched with."""
        root = str(self.root)
        parts: list = [head12]
        for folder in ("plans", "specs"):
            rows = []
            try:
                with os.scandir(os.path.join(root, "docs", folder)) as scan:
                    for item in scan:
                        if item.name.endswith(".md"):
                            try:   # one broken entry (a dangling link, a file that vanished) must not hide the others
                                info = item.stat()
                            except OSError:
                                rows.append((item.name, None))
                            else:
                                rows.append((item.name, info.st_mtime_ns, info.st_size))
            except OSError:
                pass
            rows.sort(key=repr)
            parts.append(tuple(rows))
        # a spec outside `docs/specs` that a plan points to (`docs/design`, `docs/domain`): `light_waiting` reads its status too
        rows = []
        for plan_row in parts[1]:   # the plans of the listing above (`_plan_content` is cached by the same stamp: no file is opened for a plan that did not change)
            if len(plan_row) != 3:
                continue
            with engine._PLAN_CACHE_LOCK:
                hit = engine._PLAN_CACHE.get(os.path.join(root, "docs", "plans", plan_row[0]))   # parsed by `light_waiting` (cold cache: no row yet, `waiting_cached` signs again after it)
            spec = hit[1][2] if hit else None
            if spec and not spec.startswith("docs/specs/"):
                path = engine.resolve_doc(Path(root), spec)
                try:
                    info = path.stat() if path is not None else None
                except OSError:
                    info = None
                rows.append((spec, info.st_mtime_ns, info.st_size) if info is not None else (spec, None))
        rows.sort(key=repr)
        parts.append(tuple(rows))
        runs = engine.runs_root(Path(root))
        rows = []
        if runs is not None:
            try:
                with os.scandir(runs) as scan:
                    for item in scan:
                        for name in ("checkpoints.md", engine.APPROVALS_FILE):
                            try:
                                info = os.stat(os.path.join(item.path, name))
                            except OSError:
                                rows.append((item.name, name, None))
                            else:
                                rows.append((item.name, name, info.st_mtime_ns, info.st_size))
            except OSError:
                pass
        rows.sort(key=repr)
        parts.append(tuple(rows))
        return tuple(parts)

    def waiting_cached(self, head12: str | None, now: float) -> bool:
        """`light_waiting` again only when `waiting_signature` moved (or `WAIT_RECHECK_S` passed): the refresh of the list every 2 s costs `stat` calls,
        not a read of every plan of every tab. `history_count` stays as the last full pass left it."""
        signature = self.waiting_signature(head12)
        last = self._wait_state
        if last is not None and last[0] == signature and now - last[1] < self.WAIT_RECHECK_S:
            return last[2]
        value = bool(self.light_waiting(head12))
        after = self.waiting_signature(head12)   # the plans are parsed now, so the specs they point to outside `docs/specs` are known
        if after[:3] + after[4:] != signature[:3] + signature[4:]:
            after = None   # a file changed while it was computed: the next refresh computes again
        self._wait_state = (after, now, value)
        return value

    def light_waiting(self, head12: str | None = None) -> bool:
        """"Does the session of this tab wait for the user", from the FILES only (no `git`, no `build_status`): the plan the status would pick, its `czeka na ...`
        status text, the newest `czeka:` of its checkpoints (`engine.waiting_items`), minus what `approvals.jsonl` of the run already holds (GO plan / GO implement
        given at all, CP / READY given for the HEAD of the tab). The light counterpart of `status()["waiting"]` for `worktrees[]`: it leaves out the waiting that
        needs the prototype or the questions of an expert, so it may say False where the status says True, never the other way round after a consent."""
        root = self.root
        plans = engine.list_plans(root)
        self.history_count = sum(1 for plan in plans if plan.get("status") in ("done", "archived"))   # no extra file is opened for it
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
                plan_state: str | None = None, guard=None, worktree: str | None = None, who: dict | None = None, intent=None,
                shown: dict | None = None) -> tuple[int, dict]:
        """Consent from a button: only the action the page offers right now (the same file and sha).

        GO: `status.set_doc_status(..., expect_sha=sha)` rewrites the Status line (the consent itself), then
        `status.record_approval` writes the trail entry to the run's `approvals.jsonl` when the run directory exists
        (without a run there is no trail: `trail` = null; the session reads the consent from the Status line).

        `guard` (a callable, the server of tabs): called under the consent lock right before the write; False = 409 `tabChanged`, nothing written.
        `worktree`: the directory name of the tab, kept in the trail entry (audit only).
        `who` (`{by, role, session}`, audit only): the account (its pseudonym), the role and the short digest of the session of the page that gave the consent;
        written to the trail entry as `by`, `role`, `session` (never a token). None for a caller without a session (a test, the library).
        `intent` (a callable `(details) -> bool`, the HTTP handler): called under the consent lock right before the write with `{word, path, run, sha}`; it writes the
        audit line of the consent and checks the session again; False = nothing written, 503 `audit_unavailable` (the handler replaces it by the refusal of the
        session). `shown` (CP / READY, `{branch, target}`): what the page showed; a difference from the current delivery = 409 `delivery_changed`.
        Every refusal carries `code`."""
        lang = self.lang(fresh=True)
        if not self.approvals:
            return 403, {"error": i18n.t(lang, "srv.http.approvalsOff"), "code": "approvals_disabled"}
        if kind not in APPROVE_KINDS or not isinstance(rel, str) or not isinstance(sha, str):
            return 400, {"error": i18n.t(lang, "srv.http.badRequest"), "code": "badRequest"}
        if kind in ("cp", "ready"):
            if plan_state is not None and (not isinstance(plan_state, str) or plan_state not in status_core.PLAN_STATUSES):
                return 400, {"error": i18n.t(lang, "srv.http.badRequest"), "code": "badRequest"}
            return self._approve_delivery(kind, rel, sha, plan if isinstance(plan, str) else None, via, plan_state, guard, worktree, who, intent, shown)
        word = APPROVE_KINDS[kind]
        with self._approve_lock:
            current = self.status(rel if kind == "plan" else None)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind and a["path"] == rel), None)
            if offer is None:
                return 409, {"error": i18n.t(lang, "srv.http.notPossibleFor", word=word, rel=rel), "code": "notPossible"}
            if offer["state"] != "waiting":
                return 409, {"error": i18n.t(lang, "srv.http.inactive", word=offer["word"], reason=offer["reason"]), "code": "inactive"}
            if offer["sha"] != sha:
                return 409, {"error": i18n.t(lang, "srv.http.fileChanged"), "code": "fileChanged"}
            folder = "specs" if kind == "spec" else "plans"
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            if intent is not None and not intent({"word": word, "path": rel, "run": offer.get("run"), "sha": sha}):
                return 503, {"error": i18n.t(lang, "srv.http.auditUnavailable"), "code": "audit_unavailable"}
            try:
                report = status_core.set_doc_status(self.root, rel, "approve", folder, expect_sha=sha)
            except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                return 409, {"error": i18n.t(lang, "srv.http.statusRefused", reason=status_core._message(error, 300)), "code": "statusRefused"}
            run_id = offer.get("run")
            trail = None
            if run_id:
                record = status_core.record_approval(self.root, word, run_id, sha=sha, source="dashboard", file=rel,
                                                     sha_after=report.get("sha"), via=via, worktree=worktree, **_who_fields(who))
                if record["status"] == "PASS":
                    trail = record["path"]
                else:
                    log(f"ślad zgody „{word}” nie zapisany: {record['reason']}")
            log(f"zgoda „{word}” dla {rel} (sha {sha[:10]}, ślad: {trail or 'brak przebiegu'})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": rel, "trail": trail, "next": i18n.t(lang, "srv.http.nextHint")}

    def _approve_delivery(self, kind: str, run_id: str, sha: str, plan: str | None = None, via: dict | None = None,
                          plan_state: str | None = None, guard=None, worktree: str | None = None, who: dict | None = None, intent=None,
                          shown: dict | None = None) -> tuple[int, dict]:
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
                return 409, {"error": i18n.t(lang, "srv.http.notPossible", word=word), "code": "notPossible"}
            if offer["state"] != "waiting":
                return 409, {"error": i18n.t(lang, "srv.http.inactive", word=word, reason=offer["reason"]), "code": "inactive"}
            if offer["sha"] != sha:
                return 409, {"error": i18n.t(lang, "srv.http.headChanged"), "code": "headChanged"}
            d = current["delivery"]
            if shown is not None and (shown.get("branch") != d.get("branch") or shown.get("target") != d.get("target")):
                return 409, {"error": i18n.t(lang, "srv.http.deliveryChanged"), "code": "delivery_changed"}
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            if intent is not None and not intent({"word": word, "path": (current.get("plan") or {}).get("path"), "run": run_id, "sha": sha}):
                return 503, {"error": i18n.t(lang, "srv.http.auditUnavailable"), "code": "audit_unavailable"}
            record = status_core.record_approval(
                self.root, word, run_id, sha=sha, source="dashboard", target=d.get("target") if kind == "cp" else None,
                branch=d.get("branch"), plan=(current.get("plan") or {}).get("path"), ahead=d.get("ahead"),
                mode=d.get("mode"), via=via, expect_plan_state=plan_state, worktree=worktree, **_who_fields(who))
            if record.get("code") == "plan_state_changed":
                return 409, {"error": i18n.t(lang, "srv.http.planStateChanged"), "code": "plan_state_changed"}
            if record["status"] != "PASS":
                log(f"wpis zgody „{word}” nie zapisany: {record['reason']}")
                return 500, {"error": i18n.t(lang, "srv.http.saveFailed", reason=record["reason"]), "code": "saveFailed"}
            log(f"zgoda „{word}” dla HEAD {sha[:10]} (przebieg {run_id})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": record["path"], "next": i18n.t(lang, "srv.http.nextHint")}


    # Full literal keys (the i18n guard finds the keys used in code by their literals, so no key is built from a code).
    FEEDBACK_TEXT_KEYS = {"card": "srv.feedback.card", "no_preview": "srv.feedback.noPreview", "content_changed": "srv.feedback.contentChanged",
                          "stale": "srv.feedback.stale", "sent": "srv.feedback.sent", "scan": "srv.feedback.scan", "failed": "srv.feedback.failed"}

    def feedback_approve(self, draft: str, sha: str, via: dict | None = None, guard=None, tab: str | None = None,
                         session_root: Path | None = None, who: dict | None = None, intent=None) -> tuple[int, dict]:
        """Consent to send a feedback draft (`POST /api/feedback-approve`): `feedback.approve_from_preview` with `source='dashboard'`, the previewed
        content only (the full sha). No tracker call. The answer of a refusal is `{error, code}`: a fixed text of the catalogue, never a title, a body or a sha.
        The consent is global for the project: the store is the one of the MAIN checkout (`feedback.store_root`), whatever tab asks; `tab` is only the tab whose token
        was given (logged), `guard` is the same re-validation of that tab as for the other writes (False = 409 `tabChanged`). `who` and `intent` as in `approve`:
        the account of the session (logged; the audit line of `intent` carries it) and the audit-and-check right before the write (False = 503 `audit_unavailable`)."""
        lang = self.lang(fresh=True)
        if not self.approvals:
            return 403, {"error": i18n.t(lang, "srv.http.approvalsOff"), "code": "approvals_disabled"}
        with self._approve_lock:
            if guard is not None and not guard():
                return 409, {"error": i18n.t(lang, "srv.http.tabChanged"), "code": "tabChanged"}
            if feedback.card_alias(self.root):   # a card of THIS tab never consents; the session directory is checked again by `approve_from_preview`
                log(f"zgoda feedbacku {draft} odmówiona (card, zakładka {tab or '-'})")
                return 403, {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS["card"]), "code": "card"}
            try:
                session = feedback.open_session(session_root or self.root)
                if intent is not None and not intent({"word": "feedback", "draft": draft, "sha": sha}):
                    return 503, {"error": i18n.t(lang, "srv.http.auditUnavailable"), "code": "audit_unavailable"}
                result = feedback.approve_from_preview(session, draft, sha=sha, source="dashboard", require_view=True, tab=tab)
            except feedback.FeedbackError as error:
                code = error.code if error.code in self.FEEDBACK_TEXT_KEYS else "failed"
                log(f"zgoda feedbacku {draft} odmówiona ({code}, sha {sha[:12]}, via {self._via_text(via)})")
                return (403 if code == "card" else 409), {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS[code]), "code": code}
            except (ValueError, LayerError, yaml.YAMLError) as error:   # a project the session cannot be opened for: the refusal of a stale target, not a bad request
                log(f"zgoda feedbacku {draft} odmówiona (sesja: {status_core._message(error, 120)})")
                return 409, {"error": i18n.t(lang, self.FEEDBACK_TEXT_KEYS["stale"]), "code": "stale"}
            log(f"zgoda feedbacku {draft} (sha {result['sha12']}, {'już była' if result['already'] else 'zapisana'}, konto {(who or {}).get('by') or '-'}, via {self._via_text(via)}, zakładka {tab or '-'})")
        self.check_once()
        return 200, {"ok": True, "draft": result["draft"], "sha12": result["sha12"], "approval": result["approval"], "already": result["already"]}

    @staticmethod
    def _via_text(via: dict | None) -> str:
        return f"{via.get('host', '')}:{via.get('port', '')}" if via else "-"


# --------------------------------------------------------------------------- tabs

class TabRegistry:
    """The tabs of one server: `tab id -> Board` and the tombstones of the tabs that went away.

    One tab = one `Board` (own root, cache and consent lock). A consent names its tab by the `id` in the body (the ids are public, in `/api/tabs`); the
    right to consent is the session of the request, not the tab. `id` is only a dictionary key (always matched against `dashboard_worktrees.ID_PATTERN`,
    never part of a path). A removed tab leaves a tombstone (at most `TOMBSTONES`, valid for `TOMBSTONE_TTL` seconds): a consent for it gets "gone" (410),
    any other unknown id is 404."""

    TOMBSTONES = 64
    TOMBSTONE_TTL = 600.0
    MISSING_ROUNDS = 2   # a tab that is not on the list of the project goes after this many consecutive refreshes (no flicker on a dismounted disk)

    def __init__(self, default_board: "Board", *, project=None, now=None):
        self.project = project
        self.server_id = dashboard_token.new_server_id()   # random per process: `/healthz` answers it, the state files and the pin directory carry it
        self._now = now or time.time
        self._lock = threading.RLock()
        self._boards: dict[str, Board] = {}
        self._entries: dict[str, dashboard_worktrees.Entry] = {}
        self._paths: dict[str, str] = {}   # id -> the normalised path, computed once at registration (the HMAC never touches the disk)
        self._tombs: list[tuple[str, str, float]] = []   # (id, normalised path, time), oldest first
        self._pins: dict[str, str | None] = {}   # id -> its pin file (None: the directory the server was started in): a pinned tab outlasts the limit of the visible ones
        self._listeners: list = []   # called (without the lock) after the list of the tabs changed: the state files follow it
        self._sync_failed_at = None   # time (`_resync_clock`) of the last failed write of the state files; None after a success
        self._resync_clock = time.monotonic
        self._history_cache = None   # (key, time, answer) of `history_response`
        self._logged: dict[str, float] = {}   # `log_limited` table of the registry (a lasting refusal is not logged on every refresh)
        self._synced = None   # `tabs_state()` as the state files hold it (`mark_synced`); False after a write that failed; None while no listener tracks it
        self.clock = time.monotonic   # of the boards' `touch` and the observer's "watched" (`set_clock`)
        self._history: dict[str, int] = {}   # id -> the number of `done`/`archived` plans, as of the last refresh of the list (with `_waiting`; 0 until one)
        self._waiting: dict[str, bool] = {}   # id -> "waits for the user" from the files, as of the last refresh of the list (`refresh_waiting`; False until one)
        self._shut: set[str] = set()   # ids of the tabs that cannot be removed (the start directory) and carry an exclusion now: their reads answer as for an unknown tab
        self._hidden: list[dict] = []   # `/api/tabs` `hidden[]`: {name, lastActivity, reason} of the last refresh (no path, no token, never an excluded one)
        self._missing: dict[str, int] = {}   # id -> consecutive refreshes in which the tab was not on the list (only `reconcile` touches it)
        path = dashboard_worktrees.normalize_path(default_board.root)
        self.default_id = dashboard_worktrees.tab_id(path, project.main_path if project else None)
        entry = dashboard_worktrees.Entry(self.default_id, path, os.path.basename(path) or path, "main" if self.default_id == "main" else "worktree",
                                          False, False, None, None, None)
        self._add(entry, default_board, path)

    # --- the verdict on the tab a consent names
    def lookup(self, tab_id) -> tuple[str, "Board | None"]:
        """`("ok", board)`, `("gone", None)` (a tab removed a moment ago: a tombstone) or `("unknown", None)` for the `id` of a consent. No token: the id is
        public and the right to consent is the session."""
        if not isinstance(tab_id, str) or dashboard_worktrees.ID_PATTERN.fullmatch(tab_id) is None:
            return "unknown", None
        with self._lock:
            board = self._boards.get(tab_id)
            if board is not None:   # a shut tab (the start directory with an exclusion) too: the write guard answers it with 409 `tabChanged`
                return "ok", board
        if self._tombstone(tab_id) is not None:
            return "gone", None
        return "unknown", None

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
            if default is not None and default is not board:   # the limits belong to the server: one SSE counter for every tab
                board.sse_owner = default

    def register(self, entry, board: "Board | None" = None, *, notify: bool = True) -> str:
        """Add the tab of an `Entry` (a `Board` of its path is made unless one is given) and drop the tombstone of its id and path.
        A given `board` must have the root of the entry (ValueError otherwise: a board of another directory would be shown as this tab).
        `notify=False` leaves the listeners (the state file) to the caller: a swap at the limit must not publish the 17th tab."""
        if not isinstance(entry.id, str) or not dashboard_worktrees.ID_PATTERN.fullmatch(entry.id):
            raise ValueError("bad tab id")
        path = dashboard_worktrees.normalize_path(entry.path)   # the only disk access of the registration, outside the lock
        if board is not None and dashboard_worktrees.fold_case(dashboard_worktrees.normalize_path(board.root)) != dashboard_worktrees.fold_case(path):
            raise ValueError("the board does not belong to the path of the entry")
        fold = dashboard_worktrees.fold_case(path)   # the paths are compared as the file system does (letter case, NFC), not byte for byte
        with self._lock:
            self._sweep()
            if entry.id in self._boards and dashboard_worktrees.fold_case(self._paths[entry.id]) == fold:
                self._entries[entry.id] = entry
                return entry.id
        if board is None:
            board = self._new_board(path)
        with self._lock:
            if entry.id in self._boards:
                if dashboard_worktrees.fold_case(self._paths[entry.id]) == fold:   # registered meanwhile by another thread: keep that board, drop the new one
                    self._entries[entry.id] = entry
                    self._tombs = [stone for stone in self._tombs if stone[0] != entry.id and dashboard_worktrees.fold_case(stone[1]) != fold]
                    return entry.id
                if entry.id == self.default_id:   # the start directory is never replaced (`deregister` would stop its board and end the observer)
                    raise ValueError("the tab of the start directory cannot be replaced")
                self.deregister(entry.id)
            self._tombs = [stone for stone in self._tombs if stone[0] != entry.id and dashboard_worktrees.fold_case(stone[1]) != fold]
            self._add(entry, board, path)
        if notify:
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

    def deregister(self, tab_id: str, *, tombstone: bool = True, notify: bool = True) -> None:
        """Remove a tab: stop its `Board` (its SSE streams end) and leave a tombstone (none with `tombstone=False`: an excluded tab must not answer 410).
        An unknown id is ignored."""
        with self._lock:
            self._sweep()
            board = self._boards.pop(tab_id, None)
            entry = self._entries.pop(tab_id, None)
            path = self._paths.pop(tab_id, None)
            pin = self._pins.pop(tab_id, None)
            self._history.pop(tab_id, None)   # a tab that comes back under the same id starts at 0 again
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
        if notify:
            self._changed()

    def _sweep(self) -> None:
        horizon = self._now() - self.TOMBSTONE_TTL
        self._tombs = [stone for stone in self._tombs if stone[2] > horizon][-self.TOMBSTONES:]

    def _tombstone(self, tab_id: str):
        with self._lock:
            self._sweep()
            return next((stone for stone in reversed(self._tombs) if stone[0] == tab_id), None)

    def summary(self) -> list[dict]:
        """The tabs for `/api/tabs`: no secret."""
        with self._lock:
            return [{"id": e.id, "name": e.name, "path": e.path, "kind": e.kind, "branch": e.branch, "head": e.head, "current": e.id == self.default_id,
                     "history": self._history.get(e.id, 0)}
                    for e in self._entries.values() if e.id not in self._shut]

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
        waiting}`. The light summary of every tab: `branch`, `head` and `lastActivity` come from the last refresh of the registry (the observer
        reads them from the `.git` files and the run files, no `git`), `waiting` is the flag the last refresh of the list read from the files of the tab
        (`Board.light_waiting`, also for a tab nobody watches; False until the first refresh)."""
        with self._lock:
            rows = [(e, self._waiting.get(e.id, False)) for e in self._entries.values() if e.id not in self._shut]
        return [{"id": e.id, "name": e.name, "path": e.path, "kind": e.kind, "branch": e.branch, "head": e.head, "current": e.id == self.default_id,
                 "lastActivity": datetime.fromtimestamp(e.last_activity, timezone.utc).isoformat(timespec="seconds") if e.last_activity is not None else None,
                 "waiting": bool(waiting)} for e, waiting in rows]

    def tabs_state(self) -> list[dict]:
        """`tabs[]` of the state file: `{id, path, current}` (no token: there is none)."""
        with self._lock:
            ids = [tab_id for tab_id in self._paths if tab_id not in self._shut]   # a shut tab (excluded start directory) is not listed anywhere
        return [{"id": tab_id, "path": self._paths[tab_id], "current": tab_id == self.default_id} for tab_id in ids if tab_id in self._paths]

    def add_listener(self, listener) -> None:
        self._listeners.append(listener)

    def mark_synced(self, tabs) -> None:
        """What the state files hold after the listener wrote them: `tabs` when every write succeeded, False when one failed."""
        with self._lock:
            self._synced = tabs
            self._sync_failed_at = None if tabs is not False else self._resync_clock()

    RESYNC_BACKOFF_S = 30.0   # after a failed write of the state files `resync` waits this long (the write may retry on Windows and would hold the observer every 2 s)

    def resync(self) -> bool:
        """A write that failed (Windows refusing the replace for longer than the retry) must not stay failed until the next change of the list: when the tabs
        differ from what the files last got, call the listeners again. True when it did."""
        with self._lock:
            if not self._listeners or self._synced is None:   # nobody tracks the files (a registry without state files)
                return False
            synced = self._synced
            if synced is False and self._sync_failed_at is not None and self._resync_clock() - self._sync_failed_at < self.RESYNC_BACKOFF_S:
                return False
        if self.tabs_state() == synced:
            return False
        self._changed()
        return True

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
                log_limited(self._logged, f"register:{entry.id}", f"zakładki: {entry.name}: {exc}")
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
        flags, counts = {}, {}
        for tab_id, head, board in rows:
            if board is None:
                continue
            try:
                flags[tab_id] = bool(board.waiting_cached(head, time.monotonic()))
                counts[tab_id] = int(board.history_count)
            except Exception as exc:  # noqa: BLE001 - a broken neighbour must not hide the others
                log(f"zakładki: waiting {tab_id}: {exc!r}")
        with self._lock:
            for tab_id, flag in flags.items():
                if tab_id in self._boards:
                    self._waiting[tab_id] = flag
                    if tab_id in counts:
                        self._history[tab_id] = counts[tab_id]
            for tab_id in list(self._waiting):
                if tab_id not in self._boards:
                    del self._waiting[tab_id]
            for tab_id in list(self._history):
                if tab_id not in self._boards:
                    del self._history[tab_id]

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
            log_limited(self._logged, "write-settings", f"zakładki: zapis odmówiony, dashboard.worktrees nieczytelne: {exc}")
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
                     "locked": "srv.history.locked", "auditRefused": "srv.history.auditRefused",
                     "notAbandoned": "srv.history.notAbandoned"}


_history_generation = 0   # bumped by every plan-status write: the cached answer of `history_response` never outlives it


def _bump_history() -> None:
    global _history_generation
    _history_generation += 1


HISTORY_CACHE_S = 2.0   # `GET /api/history` is built at most this often for the same list of tabs (an anonymous read must not cost a walk of every plan per request)


def history_response(registry: "TabRegistry") -> dict:
    """`GET /api/history`: `{schema, truncated, count, tabs[], items[]}` over the roots of the registered tabs (never a path from the request); a tab that carries
    an exclusion (R3, `is_shut`) has no plans here. The default tab comes first. The read does NOT re-validate each tab on disk (that is for the writes): it uses
    the state of the last refresh of the registry (the list of tabs, `is_shut`), and the built answer is kept `HISTORY_CACHE_S` seconds for the same list of tabs."""
    rows = registry.summary()
    rows.sort(key=lambda row: row["id"] != registry.default_id)
    tabs, listed = [], []
    for row in rows:
        board = registry.board(row["id"])
        if board is None or registry.is_shut(row["id"]):
            continue
        listed.append(dashboard_history.TabRoot(row["id"], row["name"], Path(board.root)))
        tabs.append({"id": row["id"], "name": row["name"], "branch": row.get("branch"), "main": row.get("kind") == "main"})
    key = (_history_generation,) + tuple((tab.id, tab.name, str(tab.root), item["branch"], item["main"]) for tab, item in zip(listed, tabs))
    now = time.monotonic()
    cached = registry._history_cache
    if cached is not None and cached[0] == key and now - cached[1] < HISTORY_CACHE_S:
        return dict(cached[2])
    body = dashboard_history.collect(listed)
    answer = {"schema": 1, "truncated": body["truncated"], "count": body["count"], "tabs": tabs, "items": body["items"]}
    registry._history_cache = (key, now, answer)
    return dict(answer)


def public_approvals(approvals: dict) -> dict:
    """The `approvals` of `/api/status` for a request without an `approver` session: `last` and `actions[].given` keep only `PUBLIC_APPROVAL_FIELDS`
    (never who consented, the session, or where it came from)."""
    def keep(entry):
        return {key: value for key, value in entry.items() if key in PUBLIC_APPROVAL_FIELDS} if isinstance(entry, dict) else entry

    shown = dict(approvals)
    shown["last"] = keep(shown.get("last"))
    shown["actions"] = [{**action, "given": keep(action.get("given"))} for action in shown.get("actions") or []]
    return shown


def plan_status_request(board: "Board", tab_id: str, body: dict, guard, via: dict | None, lang: str, intent=None) -> tuple[int, dict]:
    """`POST /api/plan-status` after the session gate: `{tab, path, sha, action}` of the tab it names. The write is `dashboard_history.change_status`
    under the consent lock of that tab's board, with the same re-validation (`guard`) as every other write. Answers `(code, {..})`; a refusal carries `code`."""
    def refuse(http: int, code: str, key: str) -> tuple[int, dict]:
        return http, {"error": i18n.t(lang, key), "code": code}
    if not board.approvals:
        return refuse(403, "approvalsOff", "srv.http.approvalsOff")
    refused_intent = []

    def checked() -> bool:
        """The guard of the change; its last call comes under the file lock right before the first write, so `intent` runs there."""
        if guard is not None and not guard():
            return False
        calls.append(True)
        if len(calls) == 2 and intent is not None and not intent({"word": action, "path": rel, "sha": sha}):
            refused_intent.append(True)
            return False
        return True

    calls: list[bool] = []
    tab, rel, sha, action = body.get("tab"), body.get("path"), body.get("sha"), body.get("action")
    if not (isinstance(tab, str) and isinstance(rel, str) and isinstance(sha, str) and isinstance(action, str)) or action not in dashboard_history.ACTIONS:
        return refuse(400, "badRequest", "srv.http.badRequest")
    if tab != tab_id:   # the tab of the request is the one of the body (the handler resolved it); a difference is a bad request, not a second tab
        return refuse(400, "badRequest", "srv.http.badRequest")
    try:
        with board._approve_lock:
            result = dashboard_history.change_status(board.root, rel, action, sha, tab_id, via=via,
                                                     guard=checked if intent is not None else guard)
    except dashboard_history.HistoryError as error:
        if refused_intent:
            return refuse(503, "audit_unavailable", "srv.http.auditUnavailable")
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
    _bump_history()   # a read within the TTL must see the new status and sha (after the audit failure too: the change was made)
    board.check_once()   # the tab's page sees the change (SSE `change`)
    return 200, {"ok": True, **result}



class Handler(SessionRoutesMixin, LingeringCloseMixin, BaseHTTPRequestHandler):
    server_version = f"dashboard/{__version__}"
    board: Board  # the board of the default tab, set in make_server (every route without `tab` and the language of every refusal)
    tabs: TabRegistry  # the tabs of the server, set in make_server
    verbose = False
    # Role of the socket: "page" is the one socket of the default mode (everybody reads, a session with `approver` consents), "read" has no write
    # route at all, "approve" is the consent socket of the two-socket mode, "both" is the single-port server of 1.5.0. Set by `make_server`.
    role = ports.ROLE_BOTH
    allow_proxy_headers = False
    own_port = 0
    peer_port = None
    # Socket timeout of every connection (`StreamRequestHandler.setup`): a client that opens a connection and sends no
    # request (or stops in the middle of it) is closed after 30 s instead of holding a thread. SSE only writes.
    timeout = 30

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 (stdlib API)
        if self.verbose:
            text = re.sub(r"\?\S*", "", fmt % args, count=1)   # never the query of a request line (a handover challenge, a link token)
            log(f"{self.address_string()} {text}")

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

    # --- the session routes of the page origin (the mixin of the prototype engine: one implementation of sessions, roles, CSRF and login limits)

    @property
    def app(self):
        """The `PrototypeApp` of the server (accounts, sessions, limits), None for a server made without one (no session routes then)."""
        return getattr(self.server, "proto_app", None)

    def _pa_send(self, status, body=b"", content_type=None, headers=None, cache="no-store") -> None:
        """The answer of a mixin route in the way of this page: its common headers (no-store, DENY, no-referrer) and, for HTML, its CSP
        unless the route names one (the login page does, with the hash of its script)."""
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        own_policy = False
        for key, value in (headers or {}).items():
            for item in value if isinstance(value, (list, tuple)) else [value]:
                self.send_header(key, item)
            own_policy = own_policy or key.lower() == "content-security-policy"
        if content_type and content_type.startswith("text/html") and not own_policy:
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _login_page_headers(self, page: str):
        """The login page has one inline script: the CSP of this page lets exactly that script run (by its hash), nothing else inline."""
        found = re.search(r"<script>(.*?)</script>", page, re.S)
        if found is None:
            return None
        digest = base64.b64encode(hashlib.sha256(found.group(1).encode("utf-8")).digest()).decode("ascii")
        return {"Content-Security-Policy": CSP.replace("script-src 'self'", f"script-src 'sha256-{digest}'")}

    def _local_only_refused(self) -> bool:
        """`--local-only`: a peer outside the loopback gets 403 on every route (the socket, not a header, decides)."""
        app = self.app
        if app is None or not app.config.local_only:
            return False
        if proto_engine.link_is_loopback_peer(self.client_address[0] if self.client_address else None):
            return False
        self.close_connection = True
        try:
            self._json(403, {"error": "local_only", "message": "this server answers only on the loopback"})
        except OSError:
            pass
        return True

    def _serve_session(self, route: str) -> bool:
        """The routes of the page origin for login and session (not on the read socket, not without a `PrototypeApp`): True when `route` was
        one of them and the answer is sent."""
        if route not in SESSION_ROUTES or self.role == ports.ROLE_READ or self.app is None:
            return False
        try:
            return self._session_route(self.command, route, urlparse(self.path).query)
        except PrototypeServerError as error:
            self._pa_error(error)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OverflowError:
            self._json(400, {"error": "invalid_request"})
        except Exception as exc:  # noqa: BLE001 - a route must not take the server down; the text of the error stays in the log
            log(f"błąd {route}: {exc!r}")
            try:
                self._json(500, {"error": "internal_error"})
            except OSError:
                self.close_connection = True
        return True

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
        if self.role == ports.ROLE_PAGE:   # the one socket: the names of a viewer or a proxy (`--read-host`, `--public-url`) and the machine's own
            app = self.app
            public = _origin_host_of(app.config.page_url) if app is not None else None
            return host in board.read_hosts or host in ports.own_names() or (public is not None and host == public)   # `'*'` opens the read socket only
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
        if self._local_only_refused() or self._host_refused():
            return None
        url = urlparse(self.path)
        query = parse_qs(url.query)
        route = url.path
        if self._serve_session(route):
            return None
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
                approver = self._approver_view()
                if not (approver and here) and isinstance(data.get("feedback"), dict):   # the drafts' content only for a session with `approver` that may consent now: the rest gets `{draft, state}` (the answer is built per request, no cache)
                    data["feedback"] = feedback.read_form(data["feedback"])
                data["approvals"] = dict(data["approvals"]) if approver else public_approvals(data["approvals"])
                data["approvals"]["here"] = here
                data["approvals"]["viewer"] = viewer
                if len(self.tabs.ids()) > 1:   # a project server with several tabs: the list for the tab bar (a server of one tab answers as before)
                    data["worktrees"] = self.tabs.worktrees()
                if self.role != ports.ROLE_BOTH:   # the role is the socket, never the header
                    data["approvals"]["role"] = self.role
                data["approvals"]["tokenRequired"] = False   # there is no token any more (the field stays for the renderers of earlier versions)
                session = self._session_summary()
                if session is not None:   # only for a signed-in request: the account of THIS session, never another one (added to the `session.url` object of the contract)
                    data["session"] = {**(data.get("session") if isinstance(data.get("session"), dict) else {}), **session}
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
        """`(here, viewer)` of this request: `here` = this request could give a consent (the consents are on and it comes locally or through TLS),
        `viewer` = it could not (read only). The right itself is the session with `approver` (`session` of `/api/status`); the one place that decides
        the two flags of `/api/status`."""
        if self.role == ports.ROLE_READ:
            return False, True
        here = bool(self.board.approvals) and self._consent_context_ok()
        return here, not here

    def _session_summary(self) -> dict | None:
        """`{account, roles, mode}` of the signed-in session of this request (`mode`: `password`, `link` or `handoff`: how the session began), None for an
        anonymous one or a server without accounts. The roles are the effective ones (a session of a login link or a handover has no `approver`)."""
        app = self.app
        if app is None or self.role == ports.ROLE_READ:
            return None
        session, user = self._session()
        if session is None or user is None:
            return None
        return {"account": proto_engine.account_of(user), "roles": list(app.effective_roles(user, session)),
                "mode": session.via if session.via in ("link", "handoff") else "password"}

    def _approver_view(self) -> bool:
        """Is this request one of a signed-in account with the effective role `approver` that need not change its password? Only such a request sees who
        consented, and (together with the context of the consents) the content of the feedback drafts."""
        app = self.app
        if app is None or self.role == ports.ROLE_READ:
            return False
        session, user = self._session()
        return bool(session is not None and user is not None and not user["mustChange"] and proto_engine.APPROVER in app.effective_roles(user, session))

    # --- consents: a session with the role `approver` (no token, no second password)

    def _consent_context_ok(self) -> bool:
        """Consents come only locally (a loopback peer, no proxy header, a loopback `Host`) or through TLS (a trusted proxy that forwards HTTPS); the
        explicit `--allow-remote-approvals` lifts that for a trusted network."""
        app = self.app
        if app is None or self._origin_kind() != "page":
            return False
        return self._context() in ("local", "tls") or bool(getattr(app, "allow_remote_approvals", False))

    def _consent_audit(self, event: str, user=None, **fields) -> None:
        """One line of `run/audit.jsonl`: `event`, `account` (the pseudonym, when the session is known), `source`, never a password, a token or a body.
        A refusal repeated within a second from the same source with the same code, route and tab is folded into a `suppressed` count of the next line (a flood of
        anonymous POSTs must not become a flood of fsyncs)."""
        app = self.app
        if app is None:
            return
        source = self._source()
        account = proto_engine.account_of(user) if user is not None else None
        if event == "consent_refused":
            key = (id(app), source, fields.get("code"), account, fields.get("route"), fields.get("tab"))
            now = time.monotonic()
            table = _CONSENT_AUDIT_SEEN
            with _CONSENT_AUDIT_LOCK:
                last, folded = table.get(key, (None, 0))
                if last is not None and now - last < 1.0:
                    table[key] = (last, folded + 1)
                    return
                if len(table) > 1024:
                    table.clear()
                table[key] = (now, 0)
            if folded:
                fields["suppressed"] = folded
        if account:
            fields["account"] = account
        app.audit_event(event, source=source, **fields)

    def _consent_refuse(self, status: int, code: str, key: str, lang: str, user=None, route: str = "") -> None:
        """The one shape of every refusal of a consent: `{error, code}` with a fixed text of the catalogue; audited; nothing was written."""
        self._consent_audit("consent_refused", user, code=code, http=status, route=route)
        self.close_connection = True   # the body of a refused POST is not read
        self._json(status, {"error": i18n.t(lang, key), "code": code})

    def _consent_gate(self, route: str, lang: str):
        """The session gate of `POST /api/approve|feedback-approve|plan-status`. Order: where the request comes from (local or TLS) -> the request
        itself (`X-Status-Board`, JSON, the exact `Origin` — `https` through TLS —, `Sec-Fetch-Site: same-origin`) -> the session (401 `login_required`
        without a cookie, `session_expired` for a cookie that no longer opens one) -> CSRF -> the role `approver` (a login-link or handover session never
        has it) -> `password_change_required` -> `approvals_disabled`. Returns `{session, user, who}` or None after a refusal (sent and audited). A refusal
        says only which step failed, never whether an account or a role exists."""
        app = self.app
        refuse = lambda status, code, key, user=None: self._consent_refuse(status, code, key, lang, user, route)   # noqa: E731
        if app is None:
            return refuse(401, "login_required", "srv.http.loginRequired")
        if not self._consent_context_ok():
            return refuse(403, "consent_context", "srv.http.consentContext")
        headers = self.headers
        if (headers.get("X-Status-Board") != "1" or not (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() == "application/json"
                or not ports.origin_matches(headers.get_all("Origin"), headers.get("Host"), secure=self._context() == "tls")
                or (headers.get("Sec-Fetch-Site") or "").strip().lower() != "same-origin"):
            return refuse(403, "consent_refused", "srv.http.consentRefused")
        session, user = self._session()
        if session is None or user is None:
            expired = bool(self._cookie_token())
            return refuse(401, "session_expired" if expired else "login_required", "srv.http.sessionExpired" if expired else "srv.http.loginRequired")
        csrf = headers.get("X-Skillsync-CSRF") or ""
        if not hmac.compare_digest(csrf.encode("utf-8", "replace"), session.csrf.encode("ascii")):
            return refuse(403, "consent_refused", "srv.http.consentRefused", user)
        app.sessions.touch(session)   # a write with a valid CSRF token is the activity that keeps an approver session alive
        problem = self._role_problem(session, user)
        if problem is not None:
            return refuse(*problem, user)
        digest = hashlib.sha256(session.token.encode("utf-8")).hexdigest()[:8]
        return {"session": session, "user": user, "who": {"by": proto_engine.account_of(user), "role": proto_engine.APPROVER, "session": digest}}

    def _role_problem(self, session, user) -> tuple[int, str, str] | None:
        """`(status, code, text key)` when the account of the session may not consent now (role, password to change, consents off), else None."""
        if proto_engine.APPROVER not in self.app.effective_roles(user, session):
            return 403, "forbidden_role", "srv.http.forbiddenRole"
        if user["mustChange"]:
            return 403, "password_change_required", "srv.http.passwordChangeRequired"
        if not self.board.approvals:
            return 403, "approvals_disabled", "srv.http.approvalsOff"
        return None

    def _consent_recheck(self, gate: dict) -> tuple[int, str, str] | None:
        """The checks of the gate that can change while the body is read or a lock is waited for, made again right before a write: the context, THE SAME
        session (a logout, a password change, an expiry end it), the effective role, the password to change."""
        if not self._consent_context_ok():
            return 403, "consent_context", "srv.http.consentContext"
        session, user = self._session()
        if session is not gate["session"] or user is None:
            return 401, "session_expired", "srv.http.sessionExpired"
        return self._role_problem(session, user)

    def _consent_audit_written(self, gate: dict, route: str, tab_id: str, details: dict) -> bool:
        """The `consent` line of `run/audit.jsonl`, written BEFORE the consent itself: False when it cannot be written (then no consent is written)."""
        app = self.app
        record = {"at": proto_engine.format_at(proto_engine.clock_ms(app.clock)), "event": "consent", "account": gate["who"]["by"],
                  "source": self._source(), "route": route, "tab": tab_id, "session": gate["who"]["session"]}
        record.update({key: str(value)[:300] for key, value in details.items() if value})
        try:
            if app.ensure_audit() is not None:
                return False
            proto_engine.link_audit_append(app.audit_dir, record, app.config.lock_timeout)
        except Exception as error:  # noqa: BLE001 - fail closed whatever went wrong with the audit
            log(f"audyt zgody nie zapisany ({type(error).__name__}): zgoda odrzucona")
            return False
        return True

    def _read_consent_body(self, length: int) -> bytes | None:
        """The body of a consent with ONE time limit for the whole of it (`BODY_DEADLINE_S`; a client that sends a byte at a time does not extend it);
        None when it did not arrive in time."""
        deadline = time.monotonic() + BODY_DEADLINE_S
        data = bytearray()
        previous = self.connection.gettimeout()
        try:
            while len(data) < length:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.connection.settimeout(left)
                chunk = self.rfile.read1(length - len(data))
                if not chunk:
                    break
                data.extend(chunk)
        except (socket.timeout, TimeoutError):
            return None
        finally:
            self.connection.settimeout(previous)
        return bytes(data)

    def do_POST(self) -> None:  # noqa: N802
        if self._local_only_refused() or self._host_refused():
            return None
        route = urlparse(self.path).path
        if self._serve_session(route):
            return None
        gate = None
        seen = {"tab": None}   # the tab of the request, for the audit of a refusal after the gate

        def answer(code: int, data: dict):
            """Every answer after the gate: a refusal is audited with its code, route and tab."""
            if code != 200:
                self._consent_audit("consent_refused", gate["user"], code=str(data.get("code") or "")[:40], http=code, route=route, tab=seen["tab"])
            return self._json(code, data)

        def refusal(code: int, name: str, key: str):
            return answer(code, {"error": i18n.t(lang, key), "code": name})

        try:
            lang = self.board.lang()
            if route not in ("/api/approve", "/api/feedback-approve", "/api/plan-status"):
                return self._json(404, {"error": i18n.t(lang, "srv.http.noPage")})
            gate = self._consent_gate(route, lang)
            if gate is None:
                return None
            who = gate["who"]
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                self.close_connection = True
                return refusal(400, "badLength", "srv.http.badLength")
            raw = self._read_consent_body(length)
            if raw is None:
                self.close_connection = True
                return refusal(408, "body_timeout", "srv.http.bodyTimeout")
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                return refusal(400, "badRequest", "srv.http.badRequest")
            if "tab" not in body and len(self.tabs.ids()) > 1:   # with several tabs the consent names its tab: never the default one by silence
                return refusal(400, "badRequest", "srv.http.badRequest")
            tab_id = body.get("tab", self.tabs.default_id)   # the tab is chosen by the body (the ids are public); the right to consent is the session
            if not isinstance(tab_id, str):
                return refusal(400, "badRequest", "srv.http.badRequest")
            seen["tab"] = tab_id[:40]
            verdict, board = self.tabs.lookup(tab_id)
            if verdict == "gone":
                return refusal(410, "tabGone", "srv.http.tabGone")
            if board is None:
                return refusal(404, "unknownTab", "srv.http.unknownTab")
            refused: list[tuple] = []   # the refusal of the session found right before the write (it replaces the answer of the write)

            def still_allowed() -> bool:
                problem = self._consent_recheck(gate)
                if problem is not None:
                    refused.append(problem)
                return problem is None

            def guard() -> bool:   # under the consent lock, before the write: the session as it is NOW, then the tab
                return still_allowed() and self.tabs.board(tab_id) is board and self.tabs.revalidate_for_write(tab_id)

            def intent(details: dict) -> bool:   # right before the write: the audit line (fail closed), then the session once more
                return self._consent_audit_written(gate, route, tab_id, details) and still_allowed()

            via = self._via() if self.role in (ports.ROLE_APPROVE, ports.ROLE_PAGE) else None
            if route == "/api/plan-status":
                code, data = plan_status_request(board, tab_id, body, guard, via, lang, intent=intent)
            elif route == "/api/feedback-approve":
                draft, sha = body.get("draft"), body.get("sha")
                if not (isinstance(draft, str) and FEEDBACK_DRAFT_RE.fullmatch(draft) and isinstance(sha, str) and FEEDBACK_SHA_RE.fullmatch(sha)):
                    return refusal(400, "badRequest", "srv.http.badRequest")
                code, data = board.feedback_approve(draft, sha, via, guard=guard, tab=tab_id, session_root=self.tabs.main_root(), who=who, intent=intent)
            else:
                shown = None
                if body.get("kind") in ("cp", "ready"):   # the consent is for the branch and target the page showed
                    shown = {key: body.get(key) for key in ("branch", "target")}
                    if not all(key in body and (body[key] is None or isinstance(body[key], str)) for key in shown):
                        return refusal(400, "badRequest", "srv.http.badRequest")
                code, data = board.approve(body.get("kind"), body.get("path"), body.get("sha"), body.get("plan"), via,
                                           body.get("planState"), guard=guard, worktree=self.tabs.worktree_name(tab_id), who=who, intent=intent,
                                           shown=shown)
                if code == 200 and via and not ports.is_loopback_bind(str(via.get("peer", ""))):
                    log(f"zgoda z sieci: {via['peer']} (port {via['port']}, Host {via['host']})")
            if refused and code != 200:
                status, name, key = refused[0]
                return refusal(status, name, key)
            return answer(code, data)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except (ValueError, UnicodeDecodeError):
            data = {"error": i18n.t(self.board.lang(), "srv.http.badRequest")}
            return self._json(400, data) if gate is None else answer(400, {**data, "code": "badRequest"})
        except Exception as exc:
            log(f"błąd {route}: {exc!r}")
            data = {"error": i18n.t(self.board.lang(), "srv.http.serverError")}
            return self._json(500, data) if gate is None else answer(500, {**data, "code": "serverError"})

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

    def __init__(self, address, handler, exclusive: bool = False, proto_app=None):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.exclusive = exclusive
        self.proto_app = proto_app   # the `PrototypeApp` shared by the sockets of this project (accounts, sessions, limits); None = no session routes
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
                allow_proxy_headers: bool = False, peer_port: int | None = None, proto_app=None) -> Server:
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
    return Server((host, port), handler, exclusive=role != ports.ROLE_BOTH, proto_app=proto_app)


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


def build_proto_app(root: Path, manifest: dict, board: "Board", args):
    """The `PrototypeApp` of the page origin: accounts from `.skillsync/prototype/users.json` of the project, `origin_kind='page'` (the only
    origin where an account with `approver` signs in) and the options of the serve command (`--allow-plain-login`, `--local-only`,
    `--trust-proxy` with `--proxy-address`, `--secure-cookies`, `--public-url`, `--prototype-url`; an option the command does not have counts
    as off). One app for every socket of the server. A missing users file or prototype directory is no error here (anonymous read works
    without accounts); a refused configuration (no list of proxies for `--trust-proxy`, a damaged or duplicated users file) raises `ValueError`."""
    from . import prototype as proto_cli   # the command module imports this one lazily; here too, at the moment of use
    repo = Path(root).resolve()
    try:
        settings = proto_cli._settings(repo, manifest)
    except proto_cli.PrototypeError as error:
        raise ValueError(str(error)) from error
    users = proto_cli.users_file_path(repo, None)
    config = proto_engine.ServerConfig(
        settings["root"], users, secure_cookies=getattr(args, "secure_cookies", False), trust_proxy=getattr(args, "trust_proxy", False),
        allow_plain_login=getattr(args, "allow_plain_login", False), local_only=getattr(args, "local_only", False),
        trusted_proxies=proto_engine.split_roles(getattr(args, "proxy_address", None)), origin_kind="page",
        page_url=getattr(args, "public_url", None), prototype_url=getattr(args, "prototype_url", None), login_to_view=False,
        source_writes=False, recover=False, language=board.lang(), log=sys.stderr, runtime_dir=proto_engine.link_run_dir_of(repo))
    for flag, given, cleaned in (("--public-url", getattr(args, "public_url", None), config.page_url),
                                 ("--prototype-url", getattr(args, "prototype_url", None), config.prototype_url)):
        if given is not None and cleaned is None:   # the engine would silently ignore it
            raise ValueError(f"{flag}: podaj sam origin, np. https://nazwa:port (bez ścieżki, zapytania i danych logowania).")
    proto_engine.set_language(config.language)
    errors, _warnings = proto_engine.startup_checks(config)
    absent = {proto_engine.tr("proto.srv.start.rootMissing", path=config.root), proto_engine.tr("proto.srv.start.usersMissing", path=config.users)}
    errors = [text for text in errors if text not in absent]
    if errors:
        raise ValueError("; ".join(errors))
    if config.allow_plain_login:
        log("UWAGA: " + proto_engine.tr("proto.srv.warn.plainLogin"))
    app = proto_engine.PrototypeApp(config)
    app.allow_remote_approvals = bool(getattr(args, "allow_remote_approvals", False))   # consents from a remote peer without TLS: off unless asked for
    if app.allow_remote_approvals:
        log("UWAGA: --allow-remote-approvals: zgody ze zdalnego peera bez TLS; sesja approver powstaje tylko lokalnie albo przez TLS, więc to rzadko ma sens; używaj w zaufanej sieci.")
    return app


def _enable_login_link(app, server: "Server | None") -> None:
    """After the bind: the state file and the audit of the sign-ins (`run/audit.jsonl`) for the socket that carries the session routes."""
    if app is None or server is None or app.state_file is not None or not Path(app.config.users).is_file():   # a project without accounts gets no run directory
        return
    address = server.server_address
    app.enable_login_link(types.SimpleNamespace(server_address=address, port=address[1]))
    for warning in app.warnings:
        log(f"UWAGA: {warning}")
    app.warnings.clear()


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
            registry.register(entry, board, notify=victim is None)   # with a victim the listeners hear of the swap once, below
        except (ValueError, OSError) as exc:
            self._refuse(pin, str(exc))
            return False
        registry.pin(tab, pin["file"])
        if victim is not None:
            registry.deregister(victim, notify=False)
            registry._changed()   # the state file is written after the swap (also when the victim was gone already): never 17 tabs in it
        return True


def log_limited(table: dict, key: str, text: str, max_keys: int = 64) -> None:
    """`log(text)` at most once a minute per `key` (`table`: key -> time of its last line, at most `max_keys` keys; the oldest half goes when full)."""
    now = time.monotonic()
    last = table.get(key)
    if last is None or now - last >= 60:
        if len(table) >= max_keys:
            for old in sorted(table, key=table.get)[:max_keys // 2]:
                del table[old]
        table[key] = now
        log(text)


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
        log_limited(self._logged, key, text, self.MAX_LOGGED)

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
        self.registry.resync()   # a state file write that failed earlier is repeated now
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
    """The address the start prints for the default tab: a project server names the tab (`/?tab=<id>`); a standalone one is the plain address."""
    if serve_plan.kind == "project":
        return dashboard_token.tab_address(port, registry.default_id, host)
    return dashboard_token.address(port, host)


def _follow_tabs(registry: "TabRegistry", state_root: Path, written: list[int]) -> None:
    """Keep `tabs[]` of every state file of this server equal to the registry (a pin made a tab)."""
    def refresh() -> None:
        tabs = registry.tabs_state()
        done = True
        for port in list(written):
            done = bool(dashboard_token.refresh_tabs(state_root, port, tabs, pid=os.getpid())) and done
        registry.mark_synced(tabs if done else False)
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
        proto_app = build_proto_app(root, manifest, board, args)
        server = make_server(board, args.host, args.port, args.verbose, proto_app=proto_app)
        _enable_login_link(proto_app, server)
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
        token_host = dashboard_token.bind_name(host)   # where the server really listens (a wildcard bind: loopback): the probe of `dashboard url`
        if board.approvals:
            shown_host = dashboard_token.address_host(host, board.approve_hosts)   # a name the page signs in under
            log(f"zgody (przyciski na stronie): zaloguj się kontem z rolą approver na {_tab_url(registry, serve_plan, port, shown_host)} "
                f"(lokalnie albo przez TLS; bez logowania strona działa tylko do odczytu; konto: `skillsync prototype user add <login> --roles approver`)")
            for name in sorted(board.approve_hosts - LOOPBACK_HOSTS):
                log(f"  zdalnie ({name}, --approve-host): {_tab_url(registry, serve_plan, port, name)}")
        dashboard_token.write_state(state_root, port, board.approvals, token_host, warn=lambda text: log(f"UWAGA: {text}"),
                                    hosts=sorted(board.approve_hosts - LOOPBACK_HOSTS), **_state_kwargs(registry, serve_plan))
        written.append(port)
        _announce(serve_plan, registry)
        if serve_plan.kind == "project":
            dashboard_token.sweep_pins()
            _follow_tabs(registry, state_root, written)
        observer = _start_observer(registry, board, serve_plan, manifest)
        extra = sorted(board.approve_hosts - LOOPBACK_HOSTS)
        readers = sorted(board.read_hosts - board.approve_hosts)
        if extra:
            log(f"dodatkowe hosty strony (--approve-host): {', '.join(extra)}"
                f"{' (zgody wyłączone: --no-approvals)' if not board.approvals else ''}")
        if readers:
            log(f"dodatkowe hosty strony (--read-host): {', '.join(readers)}")
        if (extra or readers) and board.approvals:
            log("zgody nie zależą od nazwy hosta: daje je tylko zalogowana sesja z rolą approver, lokalnie albo przez TLS; "
                "stronę bez żadnych zgód daje --no-approvals.")
        if args.host not in LOOPBACK_HOSTS:
            if not extra and not readers:
                log(f"UWAGA: nasłuch na {args.host}, ale strona odpowiada tylko pod localhost (nagłówek Host); inne urządzenia "
                    f"dostaną 403, dopóki ich adres z paska przeglądarki (nazwa lub IP) nie trafi do --approve-host "
                    f"albo --read-host.")
            else:
                log(f"UWAGA: nasłuch na {args.host}; strona dostępna pod: {', '.join(sorted(set(extra) | set(readers)))} — używaj "
                    f"w zaufanej sieci.")
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
            proto_app.close_link()
            dashboard_token.remove_state(state_root, port, os.getpid())
            dashboard_token.remove_pins(registry.server_id)
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "url": url, "stopped": True})


def _serve_ports(args, mode: dict, serve_plan=None) -> int:
    """The socket modes: one `Board`, one watcher, a server per socket. Default: ONE `page` socket (everybody reads, a session with `approver` consents).
    Explicit `--read-port` / `--approve-port`: `read` has no write route at all, `approve` is the page of the session routes and of the consents. All the
    sockets are bound BEFORE any thread, state file or console line, so a failure of the second leaves nothing."""
    servers: list[tuple[str, Server, int, str]] = []
    proto_app = None
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
        board = Board(root, plan, poll=max(0.2, args.poll), approvals=bool(mode["approvals"]),
                      prototype_port=_prototype_port(args, manifest, root), session_url=args.session_url,
                      approve_hosts=tuple(args.approve_host), read_hosts=tuple(getattr(args, "read_host", None) or ()),
                      assets=assets, descriptions=descriptions, manifest=manifest)
        registry, state_root = _prepare_registry(board, serve_plan, manifest)
        proto_app = build_proto_app(root, manifest, board, args)
        read_port = mode["read"]["port"] if mode["read"] else None
        approve_port = mode["approve"]["port"] if mode["approve"] else None
        for role, peer in ((ports.ROLE_PAGE, None), (ports.ROLE_READ, approve_port), (ports.ROLE_APPROVE, read_port)):
            spec = mode[role]
            if spec:
                failing = spec["port"]
                if ports.port_taken(spec["bind"], spec["port"]):
                    raise OSError(errno.EADDRINUSE, "Address already in use")
                servers.append((role, make_server(board, spec["bind"], spec["port"], args.verbose, role=role, peer_port=peer,
                                                  allow_proxy_headers=mode["allow_proxy"] and role == ports.ROLE_APPROVE, proto_app=proto_app),
                                spec["port"], spec["bind"]))
                failing = None
        _enable_login_link(proto_app, next((server for role, server, _port, _bind in servers if role != ports.ROLE_READ), None))
    except OSError as error:
        for _role, server, _port, _bind in servers:
            server.server_close()
        if serve_plan.race and serve_plan.project is not None:   # the port may be held by a project server that started a moment ago: join it
            code = dashboard_token.retry_append(args, serve_plan)
            if code is not None:
                return code
        wanted = "/".join(str(mode[role]["port"]) for role in (ports.ROLE_PAGE, ports.ROLE_READ, ports.ROLE_APPROVE) if mode[role])
        owner = dashboard_token.port_owner(failing) if failing else None
        who = f"; port {failing} trzyma {owner}" if owner else (f"; port {failing} zajęty przez inny proces" if failing else "")
        return emit({"status": "BLOCKED", "reason": f"nie można nasłuchiwać ({error.strerror or error}); porty {wanted} muszą być wolne{who}. "
                                                    f"Wskaż inny: --read-bind ADR dla innego adresu albo --port N (tryb jednego portu)."})
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
            peer = None if role == ports.ROLE_PAGE else mode["approve" if role == ports.ROLE_READ else "read"]
            peer_port = peer["port"] if peer else None
            if role == ports.ROLE_PAGE:
                shown = dashboard_token.address_host(bind, board.approve_hosts)
                log(f"strona (odczyt dla wszystkich; zgody po zalogowaniu kontem z rolą approver, lokalnie albo przez TLS): "
                    f"{_tab_url(registry, serve_plan, port, shown)}  [nasłuch {bind}:{port}]")
                dashboard_token.write_state(state_root, port, board.approvals, local, warn=warn, hosts=approve_hosts, role=role, bind=bind,
                                            **_state_kwargs(registry, serve_plan))
            elif role == ports.ROLE_READ:
                log(f"odczyt (do tunelu i linków): {dashboard_token.read_address(port, local)}  [nasłuch {bind}:{port}, bez zgód]")
                dashboard_token.write_state(state_root, port, None, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port, bind=bind,
                                            **_state_kwargs(registry, serve_plan))
            else:
                shown = dashboard_token.address_host(bind, board.approve_hosts)
                log(f"zgody (zaloguj się kontem z rolą approver): {_tab_url(registry, serve_plan, port, shown)}  [nasłuch {bind}:{port}]")
                dashboard_token.write_state(state_root, port, board.approvals, local, warn=warn, hosts=approve_hosts, role=role, peer=peer_port,
                                            **_state_kwargs(registry, serve_plan))
            states.append(port)
            if not ports.is_loopback_bind(bind):
                if role == ports.ROLE_APPROVE:
                    log(f"UWAGA: port zgód {port} słucha na {bind}: widzi go każde urządzenie w sieci; zgody daje tylko sesja z rolą approver, "
                        f"zalogowana lokalnie albo przez TLS (hasło nie idzie przez nieszyfrowane HTTP). W nieufnej sieci uruchom z "
                        f"--approve-bind 127.0.0.1.")
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
                    if role in (ports.ROLE_READ, ports.ROLE_PAGE):
                        names = sorted(name for name in ports.own_names() if name.endswith(".local")) or sorted(ports.own_names())[:1]
                        for name in names:
                            log(f"  albo po nazwie tej maszyny: http://{name}:{port}/")
                    if role in (ports.ROLE_READ, ports.ROLE_PAGE) and os.name == "nt":
                        log("  Zapora Windows może zapytać o dostęp: „Zezwól” w sieci prywatnej daje podgląd z telefonu, odmowa zostawia stronę lokalnie.")
        approve_spec, read_spec = mode["approve"], mode["read"]
        if approve_spec and ports.is_loopback_bind(approve_spec["bind"]) and read_spec and not ports.is_loopback_bind(read_spec["bind"]):
            log("zgody są tylko na tej maszynie; zgody z telefonu: --approve-bind 0.0.0.0 i zalogowanie przez TLS (albo --allow-remote-approvals w zaufanej sieci)")
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
            if proto_app is not None:
                proto_app.close_link()
            registry.stop_all()
            _stop_observer(observer)
            for port in states:
                dashboard_token.remove_state(state_root, port, os.getpid())
            dashboard_token.remove_pins(registry.server_id)
        finally:
            stopsignal.cleanup_finished(console_close, cleanup_done)   # last, even when a step above failed: a closed console window waits for this
    return emit({"status": "PASS", "stopped": True, "ports": {role: port for role, _server, port, _bind in servers}})
