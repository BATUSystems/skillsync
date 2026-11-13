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
from . import status as status_core
from .common import emit, load_layer_project, read_project, redact
from .layers import LayerError

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
HOST_REFUSED = "Strona odpowiada tylko pod localhost albo hostem z --approve-host (nagłówek Host)."
NEXT_HINT = ("Zgoda zapisana w pliku. Sesja czekająca na nią rusza sama (nasłuch zmian i budzik co 30 min); "
             "jeśli nie ruszy w ciągu minuty, napisz w sesji „dalej”.")


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
                 assets: dict[str, Path] | None = None, descriptions: Path | None = None, manifest: dict | None = None):
        self.root = Path(root).resolve()
        self.approvals = approvals
        self.session_url = session_url if session_url_ok(session_url) else None
        self.assets = dict(assets or {})
        self.descriptions = descriptions
        self._manifest = manifest
        self._approve_lock = threading.Lock()
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
        self._proto = (0.0, False)
        self._accept: tuple[tuple, float, dict] = ((), 0.0, {})
        self._sig = self.signature()

    def manifest(self) -> dict:
        self._manifest = project_manifest(self.root, self._manifest)
        return self._manifest

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
        protos = self.root / engine.PROTOTYPES_DIR
        if protos.is_dir():
            paths.extend(protos.glob("*/prototype.json"))
            paths.extend(protos.glob("*/comments.json"))
            paths.extend(protos.glob("*/questions.json"))
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

    def prototype_acceptance(self, slug: str) -> dict | None:
        """Screen acceptance of a prototype (`dashboard.prototype_acceptance`, in-process, read-only), kept in memory until
        `comments.json`/`index.html` of the prototypes change or for PROTO_CACHE_S seconds."""
        base = self.root / engine.PROTOTYPES_DIR
        key = tuple(sorted((str(f), engine._mtime(f)) for pat in ("*/comments.json", "*/index.html") for f in base.glob(pat)))
        cached_key, ts, table = self._accept
        if key != cached_key or time.time() - ts > PROTO_CACHE_S:
            table = {}
            self._accept = (key, time.time(), table)
        if slug not in table:
            table[slug] = engine.prototype_acceptance(self.root, slug)
        return table[slug]

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
        return engine.build_status(
            self.root, plan_rel or self.default_plan, manifest=self.manifest(), approvals=self.approvals,
            session_url=self.session_url, prototype_port=self.prototype_port, version=self.version,
            ahead=lambda _root, branch, head: self.ahead(branch, head), prototype_up=self.prototype_up,
            acceptance=lambda slug: self.prototype_acceptance(slug))

    # --- documents
    def doc(self, rel: str | None) -> dict | None:
        path = engine.resolve_doc(self.root, rel)
        if not path:
            return None
        size = path.stat().st_size
        text = engine.read_text(path)
        title, _fields = engine.parse_header(text)
        return {"path": rel, "title": title or path.stem, "status": engine.doc_status(text)[0],
                "mtime": engine._iso(engine._mtime(path)), "size": size, "truncated": size > MAX_DOC_BYTES,
                "html": render_markdown(text, rel or "")}

    # --- consents (in-process, the shared core functions of status.py; no subprocess, no own jsonl writer)
    def approve(self, kind: str, rel: str, sha: str, plan: str | None = None) -> tuple[int, dict]:
        """Consent from a button: only the action the page offers right now (the same file and sha).

        GO: `status.set_doc_status(..., expect_sha=sha)` rewrites the Status line (the consent itself), then
        `status.record_approval` writes the trail entry to the run's `approvals.jsonl` when the run directory exists
        (without a run there is no trail: `trail` = null; the session reads the consent from the Status line)."""
        if not self.approvals:
            return 403, {"error": "Zgody z tej strony są wyłączone (--no-approvals)."}
        if kind not in APPROVE_KINDS or not isinstance(rel, str) or not isinstance(sha, str):
            return 400, {"error": "Nieprawidłowe żądanie."}
        if kind in ("cp", "ready"):
            return self._approve_delivery(kind, rel, sha, plan if isinstance(plan, str) else None)
        word = APPROVE_KINDS[kind]
        with self._approve_lock:
            current = self.status(rel if kind == "plan" else None)
            offer = next((a for a in current["approvals"]["actions"] if a["kind"] == kind and a["path"] == rel), None)
            if offer is None:
                return 409, {"error": f"„{word}” nie jest teraz możliwe dla {rel}; odśwież stronę."}
            if offer["state"] != "waiting":
                return 409, {"error": f"„{offer['word']}” nieaktywne: {offer['reason']}."}
            if offer["sha"] != sha:
                return 409, {"error": "Plik zmienił się od wyświetlenia; przeczytaj go ponownie i zatwierdź jeszcze raz."}
            folder = "specs" if kind == "spec" else "plans"
            try:
                report = status_core.set_doc_status(self.root, rel, "approve", folder, expect_sha=sha)
            except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                return 409, {"error": f"Odmowa zmiany statusu: {' '.join(str(error).split())[:300]}"}
            run_id = offer.get("run")
            trail = None
            if run_id:
                record = status_core.record_approval(self.root, word, run_id, sha=sha, source="dashboard", file=rel,
                                                     sha_after=report.get("sha"))
                if record["status"] == "PASS":
                    trail = record["path"]
                else:
                    log(f"ślad zgody „{word}” nie zapisany: {record['reason']}")
            log(f"zgoda „{word}” dla {rel} (sha {sha[:10]}, ślad: {trail or 'brak przebiegu'})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": rel, "trail": trail, "next": NEXT_HINT}

    def _approve_delivery(self, kind: str, run_id: str, sha: str, plan: str | None = None) -> tuple[int, dict]:
        """CP / READY: `status.record_approval` into `.skillsync/runs/<run-id>/approvals.jsonl` with the sha of the HEAD
        shown on the page. The session checks the entry (word, sha = its HEAD) instead of a word typed in the terminal."""
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
            record = status_core.record_approval(
                self.root, word, run_id, sha=sha, source="dashboard", target=d.get("target") if kind == "cp" else None,
                branch=d.get("branch"), plan=(current.get("plan") or {}).get("path"), ahead=d.get("ahead"),
                mode=d.get("mode"))
            if record["status"] != "PASS":
                log(f"wpis zgody „{word}” nie zapisany: {record['reason']}")
                return 500, {"error": f"Nie udało się zapisać zgody ({record['reason']}); wpisz słowo w sesji."}
            log(f"zgoda „{word}” dla HEAD {sha[:10]} (przebieg {run_id})")
        self.check_once()
        return 200, {"ok": True, "word": word, "file": record["path"], "next": NEXT_HINT}


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"dashboard/{__version__}"
    board: Board  # set in make_server
    verbose = False
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
        if host in self.board.approve_hosts or host in self.board.read_hosts:
            return False
        if self.verbose:
            log(f"odmowa {self.command} {urlparse(self.path).path[:120]!r}: Host spoza listy ({host[:80]!r})")
        try:
            self._json(403, {"error": HOST_REFUSED})
        except OSError:
            pass
        return True

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
                host = request_host(self.headers.get("Host"))
                here = host in self.board.approve_hosts
                data["approvals"]["here"] = here
                data["approvals"]["viewer"] = not here and host in self.board.read_hosts  # read-only Host (--read-host)
                return self._json(200, redact(data))  # secrets masked as in `dashboard show`
            if route == "/api/doc":
                doc = self.board.doc((query.get("path") or [None])[0])
                if doc is None:
                    return self._json(404, {"error": "Dokument niedostępny (spoza listy dozwolonych albo nie istnieje)."})
                return self._json(200, doc)
            if route == "/events":
                if self.command == "HEAD":  # headers only; takes no SSE slot and opens no stream
                    return self._send(200, b"", "text/event-stream; charset=utf-8")
                return self._events()
            if route == "/api/descriptions":
                return self._json(200, load_descriptions(self.board.descriptions))
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
        """Reason to refuse a write or None: loopback Host (also against DNS rebinding and tunnels), own header (forces
        a CORS preflight the server does not answer), matching Origin when the browser sent one, JSON content type."""
        allowed = self.board.approve_hosts
        host = request_host(self.headers.get("Host"))
        if host not in allowed:  # foreign Host: do_POST already refused it; --read-host: refused here, same message
            return HOST_REFUSED
        if self.headers.get("X-Status-Board") != "1":
            return "Brak nagłówka X-Status-Board."
        origin = self.headers.get("Origin")
        if origin and request_host(urlparse(origin).netloc) not in allowed:
            return "Obcy Origin."
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return "Oczekiwano application/json."
        return None

    def do_POST(self) -> None:  # noqa: N802
        if self._host_refused():
            return None
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
        """Only allowlisted names found in the skill's `assets/` of the active layers (no paths, no traversal)."""
        path = self.board.assets.get(name) if ASSET_NAME_RE.fullmatch(name) else None
        if path is None or path.is_symlink() or not path.is_file():
            return self._json(404, {"error": "Nie ma takiego zasobu."})
        with open(path, "rb") as fh:
            body = fh.read(MAX_ASSET_BYTES)
        self._send(200, body, ASSET_TYPES[path.suffix])

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
    allow_reuse_address = os.name != "nt"
    # Listen backlog: the default of 5 resets bursts of parallel clients (the same value as the prototype server).
    request_queue_size = 128

    def __init__(self, address, handler):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        ThreadingHTTPServer.__init__(self, address, handler)

    def server_bind(self):
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
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found or ["<adres-tej-maszyny>"]


def port_problem(port: int) -> str | None:
    if port in FORBIDDEN_PORTS or not PORT_MIN <= port <= PORT_MAX:
        return f"Port {port} poza dozwolonym zakresem {PORT_MIN}–{PORT_MAX} (zabronione: 4444–4448, 9222, 9333)."
    return None


def validate_port(port: int) -> None:
    problem = port_problem(port)
    if problem:
        raise SystemExit(problem)


def make_server(board: Board, host: str, port: int, verbose: bool = False) -> Server:
    validate_port(port)
    handler = type("BoundHandler", (Handler,), {"board": board, "verbose": verbose})
    return Server((host, port), handler)


# --------------------------------------------------------------------------- CLI

def _stop_on_sigterm(_signum, _frame):
    raise KeyboardInterrupt


def _prototype_port(args, manifest) -> int:
    """Port of the prototype server: `--prototype-port`, else `prototype.port` of the manifest, else 5130."""
    flag = getattr(args, "prototype_port", None)
    if flag is not None:
        if isinstance(flag, bool) or not 1024 <= flag <= 65535:
            raise ValueError(f"--prototype-port {flag}: podaj port 1024–65535.")
        return flag
    from .prototype import resolve_prototype_settings  # lazy: the prototype module is large
    settings = resolve_prototype_settings((manifest or {}).get("prototype"), ".")
    port_errors = [error for error in settings["errors"] if error.startswith("prototype.port")]
    if port_errors:
        raise ValueError(port_errors[0])
    return settings["port"] or engine.PROTOTYPE_PORT


def serve_command(args) -> int:
    """`skillsync dashboard serve`: foreground server (Ctrl+C or SIGTERM stops); BLOCKED (exit 2) when it may not start."""
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
                      prototype_port=_prototype_port(args, manifest),
                      session_url=args.session_url, approve_hosts=tuple(args.approve_host),
                      read_hosts=tuple(getattr(args, "read_host", None) or ()), assets=assets,
                      descriptions=descriptions, manifest=manifest)
        server = make_server(board, args.host, args.port, args.verbose)
    except OSError as error:
        return emit({"status": "BLOCKED", "reason": f"nie można nasłuchiwać na {args.host}:{args.port} "
                                                    f"({error.strerror or error})"})
    except (LayerError, ValueError, TypeError, yaml.YAMLError) as error:
        return emit({"status": "BLOCKED", "reason": " ".join(str(error).split())[:500]})
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop_on_sigterm)
    threading.Thread(target=board.watch_loop, name="watcher", daemon=True).start()
    host, port = server.server_address[:2]
    url = f"http://{'[%s]' % host if ':' in host else host}:{port}/"
    log(f"projekt {board.root} → {url}  (Ctrl+C kończy)")
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
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        board.stop.set()
        with board.cond:
            board.cond.notify_all()
        server.server_close()
    return emit({"status": "PASS", "url": url, "stopped": True})
