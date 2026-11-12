"""K3 renderer dashboardu: `library/skills/dashboard/assets/dashboard-render.js` jest czystym widokiem JSON → DOM
(ten sam plik wczytuje webview Skippstera), a `app.js` tylko adapterem strony (HTTP, SSE, pamięć, motyw).

- Test statyczny: w pliku renderera (bez komentarzy, z wyciętą treścią literałów) nie ma transportu, pamięci przeglądarki,
  globalnego `document`/`window`, pollingu ani wstrzykiwania HTML/stylu; kontrola ujemna dowodzi, że sprawdzian łapie
  każdą zakazaną konstrukcję i nie myli się na słowach w komentarzach i napisach.
- Test behawioralny (node >= 18): renderer na własnym cieniu DOM (bez jsdom) rysuje każdy `expected*.json` z
  `tests/fixtures/dashboard/`; cień rzuca wyjątek przy `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `cssText`,
  `setAttribute('style'|'on…')` i `document.write`. Wygląd (CSS, układ) weryfikuje przeglądarka, nie ten test.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "library" / "skills" / "dashboard" / "assets"
RENDERER = ASSETS / "dashboard-render.js"
ADAPTER = ASSETS / "app.js"
FIXTURES = ROOT / "tests" / "fixtures" / "dashboard"
NODE = os.environ.get("SKILLSYNC_TEST_NODE") or shutil.which("node")


def node_major(binary):
    if not binary:
        return 0
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(re.match(r"v(\d+)", out).group(1))
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        return 0


NODE_MAJOR = node_major(NODE)

# ---------------------------------------------------------------------------------------------- statyczny sprawdzian K3
_REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "new", "delete", "throw", "instanceof", "yield", "await"}


def strip_js(src: str, keep_strings: bool, mark: bool = False) -> str:
    """Kod JS bez komentarzy; literały regex zastąpione `/R/`; treść napisów i części tekstowych szablonów usunięta
    (`keep_strings=False`) albo zachowana (z `mark=True` otoczona znakami \x02…\x03). Wyrażenia `${…}` w szablonach
    zostają kodem."""
    out: list[str] = []
    opn, cls = ("\x02", "\x03") if mark else ("", "")
    i, n = 0, len(src)
    stack: list[int] = []  # głębokość nawiasów { } wewnątrz każdego otwartego ${ … }
    last = ""  # ostatni znaczący znak kodu (do odróżnienia regexu od dzielenia)
    last_word = ""

    def template(i: int) -> int:
        """Od znaku po otwierającym ` do zamykającego ` albo do `${` (wtedy push na stos)."""
        if keep_strings:
            out.append(opn)
        while i < n:
            c = src[i]
            if c == "\\":
                if keep_strings:
                    out.append(src[i:i + 2])
                i += 2
                continue
            if c == "`":
                out.append(cls + "`" if keep_strings else "`")
                return i + 1
            if c == "$" and i + 1 < n and src[i + 1] == "{":
                out.append(cls + "${" if keep_strings else "${")
                stack.append(0)
                return i + 2
            if keep_strings:
                out.append(c)
            i += 1
        return i

    while i < n:
        c = src[i]
        if c in " \t\r\n":
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(c + (opn + src[i + 1:j] + cls if keep_strings else "") + c)
            i = j + 1
            last, last_word = c, ""
            continue
        if c == "`":
            out.append("`")
            i = template(i + 1)
            last, last_word = "`", ""
            continue
        if c == "/" and (last == "" or last in _REGEX_BEFORE or last_word in _REGEX_KEYWORDS):
            j, in_class = i + 1, False
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            out.append("/R/")
            i = j
            last, last_word = "R", ""
            continue
        if stack and c == "{":
            stack[-1] += 1
        elif stack and c == "}":
            if stack[-1] == 0:
                stack.pop()
                out.append("}")
                i = template(i + 1)
                last, last_word = "`", ""
                continue
            stack[-1] -= 1
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            last_word = src[i:j]
            out.append(last_word)
            last = "w"
            i = j
            continue
        out.append(c)
        last, last_word = c, ""
        i += 1
    return "".join(out)


# Konstrukcje zakazane w kodzie renderera (treść napisów wycięta, więc słowa w tekstach UI nie przeszkadzają).
FORBIDDEN_CODE = {
    "fetch": r"\bfetch\s*\(",
    "EventSource": r"\bEventSource\b",
    "XMLHttpRequest": r"\bXMLHttpRequest\b",
    "WebSocket": r"\bWebSocket\b",
    "postMessage": r"\bpostMessage\b",
    "localStorage": r"\blocalStorage\b",
    "sessionStorage": r"\bsessionStorage\b",
    "indexedDB": r"\bindexedDB\b",
    "cookie": r"\bcookie\b",
    "document outside root": r"\b(?:ownerDocument|doc)\s*\.\s*(?:querySelector(?:All)?|getElementById|getElementsBy\w+|body|documentElement|head|activeElement|title|forms|links|images|write\w*)\b",
    "window": r"\bwindow\b",
    "document": r"\bdocument\b",
    "location": r"\blocation\b",
    "history": r"\bhistory\b",
    "navigator": r"\bnavigator\b",
    "matchMedia": r"\bmatchMedia\b",
    "setInterval": r"\bsetInterval\b",
    "Date.now": r"\bDate\s*\.\s*now\b",
    "new Date()": r"\bnew\s+Date\s*\(\s*\)",
    "innerHTML": r"\binnerHTML\b",
    "outerHTML": r"\bouterHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "createContextualFragment": r"\bcreateContextualFragment\b",
    "DOMParser": r"\bDOMParser\b",
    "srcdoc": r"\bsrcdoc\b",
    "eval": r"\beval\s*\(",
    "Function": r"\bFunction\s*\(",
    "import()": r"\bimport\s*\(",
    "setTimeout(string)": r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]",
    "cssText": r"\bcssText\b",
    ".style =": r"\.style\s*=(?!=)",
    "style key": r"(?<![\w$.])style\s*:",
    ".on… =": r"\.on[a-z]+\s*=(?!=)",
}
# Wzorce w treści napisów i szablonów: znaczniki HTML i atrybuty stylu/handlerów jako tekst.
FORBIDDEN_TEXT = {
    "HTML markup": r"<\s*/?\s*[a-zA-Z][\w-]*(?:\s[^<>]*)?/?>",
    "style=": r"\bstyle\s*=\s*[\"'`]",
    "inline handler": r"\bon[a-z]+\s*=\s*[\"'`]",
}
# Wzorce na kodzie z zachowanymi napisami: nazwa atrybutu podana jako literał.
FORBIDDEN_LITERAL = {
    "setAttribute(style|on…)": r"setAttribute\s*\(\s*[\"'`]\s*(?:style\b|on)",
    "\"style\" key": r"[\"']style[\"']\s*:",
}


def string_texts(src: str) -> str:
    """Treść wszystkich napisów i tekstowych części szablonów, po jednej w wierszu."""
    return "\n".join(re.findall(r"\x02(.*?)\x03", strip_js(src, keep_strings=True, mark=True), re.S))


def violations(src: str) -> list[str]:
    code, literal, texts = strip_js(src, keep_strings=False), strip_js(src, keep_strings=True), string_texts(src)
    found = [name for name, pat in FORBIDDEN_CODE.items() if re.search(pat, code)]
    found += [name for name, pat in FORBIDDEN_TEXT.items() if re.search(pat, texts)]
    found += [name for name, pat in FORBIDDEN_LITERAL.items() if re.search(pat, literal)]
    return found


class RendererStaticTests(unittest.TestCase):
    def test_renderer_has_no_transport_storage_or_html_injection(self) -> None:
        self.assertEqual(violations(RENDERER.read_text(encoding="utf-8")), [])

    def test_checker_catches_every_forbidden_construct(self) -> None:
        bad = {
            "fetch": "fetch('/api/status');",
            "EventSource": "const es = new EventSource('/events');",
            "XMLHttpRequest": "new XMLHttpRequest();",
            "WebSocket": "new WebSocket(u);",
            "postMessage": "vscode.postMessage(m);",
            "localStorage": "localStorage.getItem('x');",
            "sessionStorage": "sessionStorage.x = 1;",
            "indexedDB": "indexedDB.open('x');",
            "cookie": "x.cookie = 'a';",
            "window": "window.scrollTo(0, 0);",
            "document": "document.getElementById('x');",
            "document outside root": "root.ownerDocument.body.appendChild(x);",
            "location": "const u = location.hash;",
            "history": "history.pushState({}, '', u);",
            "navigator": "navigator.clipboard.writeText(t);",
            "matchMedia": "matchMedia('(x)');",
            "setInterval": "setInterval(poll, 5000);",
            "Date.now": "const t = Date.now();",
            "new Date()": "const t = new Date();",
            "innerHTML": "el.innerHTML = s;",
            "outerHTML": "el.outerHTML = s;",
            "insertAdjacentHTML": "el.insertAdjacentHTML('beforeend', s);",
            "createContextualFragment": "range.createContextualFragment(s);",
            "DOMParser": "new DOMParser();",
            "srcdoc": "frame.srcdoc = s;",
            "eval": "eval(s);",
            "Function": "new Function('return 1');",
            "import()": "import('./x.js');",
            "setTimeout(string)": "setTimeout('alert(1)', 10);",
            "cssText": "el.style.cssText = s;",
            ".style =": "el.style = s;",
            "style key": "h('div', { style: s });",
            ".on… =": "el.onclick = f;",
            "HTML markup": "const t = `<div class=\"x\">${v}</div>`;",
            "style=": "const t = 'style=\"color:red\"';",
            "inline handler": "const t = 'onclick=\"go()\"';",
            "setAttribute(style|on…)": "el.setAttribute('style', s);",
            "\"style\" key": "h('div', { \"style\": s });",
        }
        self.assertEqual(sorted(bad), sorted(list(FORBIDDEN_CODE) + list(FORBIDDEN_TEXT) + list(FORBIDDEN_LITERAL)))
        for rule, snippet in bad.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, violations(snippet))
        harmless = ("// fetch(x) localStorage innerHTML window document\n/* setInterval(eval(x)) */\n"
                    "const s = \"fetch localStorage innerHTML window.location history document.cookie\";\n"
                    "const r = /fetch\\(\"x\"\\)/g; const q = `teraz: ${a.b / 2} history`; el.style.width = w + \"px\";\n"
                    "el.style.setProperty('--x', v); el.addEventListener('click', f); const d = new Date(iso); root.ownerDocument; doc.createElement('div'); doc.createElementNS(ns, 'svg'); doc.defaultView; root.querySelectorAll('[id]');")
        for snippet in ("root.ownerDocument.querySelector('#x');", "doc.getElementById('x');", "doc.documentElement.dataset.theme = t;",
                        "const b = root.ownerDocument.activeElement;", "doc.querySelectorAll('a');"):
            self.assertIn("document outside root", violations(snippet), snippet)
        self.assertEqual(violations(harmless), [])

    def test_strip_js_keeps_template_expressions_as_code(self) -> None:
        self.assertIn("fetch", strip_js("const a = `x ${fetch('/a')} y`;", keep_strings=False))
        self.assertNotIn("tekst", strip_js("const a = `tekst ${b}`;", keep_strings=False))
        self.assertIn("window", strip_js("const a = `${ {k: window}.k }`;", keep_strings=False))

    def test_renderer_is_umd_and_exports_create(self) -> None:
        code = strip_js(RENDERER.read_text(encoding="utf-8"), keep_strings=False)
        self.assertRegex(code, r"module\.exports\s*=\s*factory\(\)")
        self.assertRegex(code, r"\.DashboardRenderer\s*=\s*factory\(\)")
        self.assertRegex(code, r"return\s*\{\s*create\b")

    def test_adapter_has_no_rendering_logic(self) -> None:
        src = ADAPTER.read_text(encoding="utf-8")
        literal = strip_js(src, keep_strings=True)
        code = strip_js(src, keep_strings=False)
        self.assertIsNone(re.search(FORBIDDEN_TEXT["HTML markup"], string_texts(src)), "app.js nie buduje znaczników HTML")
        for word in ("createElement", "textContent", "classList", "appendChild", "replaceChildren", "setAttribute", "querySelector"):
            self.assertNotRegex(code, r"\b" + word + r"\b", word)
        self.assertEqual(len(re.findall(r"\binnerHTML\b", code)), 1, "jedyne innerHTML: mountHtml podglądu Markdown")
        self.assertRegex(code, r"mountHtml:\s*\(el, html\)\s*=>\s*\{\s*el\.innerHTML\s*=\s*html;\s*\}")
        self.assertRegex(code, r"window\.DashboardRenderer\.create\(document\.getElementById\(\"\"\)")
        self.assertIn('getElementById("app")', literal)
        self.assertRegex(code, r"\btheme,")

    def test_page_loads_renderer_before_adapter(self) -> None:
        html = (ASSETS / "index.html").read_text(encoding="utf-8")
        head, body = html.split("</head>", 1)
        self.assertIn('<script src="/assets/theme.js"></script>', head)
        self.assertIn('<link rel="stylesheet" href="/assets/app.css">', head)
        self.assertIn('<div id="app"></div>', body)
        self.assertLess(body.index('<script src="/assets/dashboard-render.js"></script>'), body.index('<script src="/assets/app.js"></script>'))
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [], "bez skryptów inline")
        self.assertNotRegex(html, r"\sstyle\s*=|\son[a-z]+\s*=")
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        self.assertIn(".act-head .chip-end { margin-left: auto; }", css)  # dawny inline margin-left:auto
        self.assertIn(".dashboard-root { display: contents; }", css)

    def test_layout_follows_container_width_not_window(self) -> None:
        """DASH-COMPACT: układ zależy od szerokości kolumny hosta (webview = kolumna edytora), więc progi to @container."""
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.dash-wrap \{[^}]*container: dash / inline-size")
        for width in (1100, 640, 500, 420):
            self.assertIn(f"@container dash (max-width: {width}px)", css)
        self.assertNotRegex(re.sub(r"prefers-[a-z-]+", "", "\n".join(re.findall(r"@media[^{]*", css))), r"width|height", "układ tylko w @container")
        self.assertRegex(css, r"@container dash \(max-width: 640px\) \{[^@]*\.wait-go \{ white-space: normal;")
        self.assertIn(".plan-auto[hidden] { display: none; }", css)
        src = RENDERER.read_text(encoding="utf-8")
        wrap = src.index('h("div", { class: "dash-wrap" }')
        self.assertLess(wrap, src.index("root.appendChild(ui.descTip)"))
        for part in ("ui.waitBanner", '"nav"', '"main"', "ui.foot"):
            self.assertGreater(src.index(part, wrap), wrap)
        self.assertNotIn("ui.descTip", src[wrap:src.index("root.appendChild(ui.descTip)")], "dymek (position: fixed) poza kontenerem")


# ---------------------------------------------------------------------------------------------- test behawioralny (Node)
HARNESS = r"""
'use strict';
const fs = require('fs');
const [rendererPath, inputPath] = process.argv.slice(2);
const R = require(rendererPath);
const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
const violations = [];
const bad = (what) => { violations.push(what); throw new Error('forbidden: ' + what); };

class Ev {
  constructor(type, init) { Object.assign(this, init || {}); this.type = type; this.defaultPrevented = false; this._stop = false; }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this._stop = true; }
}
class N {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  get firstChild() { return this.childNodes[0] || null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  set textContent(v) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; if (v !== '' && v != null) this.appendChild(this.ownerDocument.createTextNode(String(v))); }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
}
class T extends N {
  constructor(doc, t) { super(doc); this.data = t; this.nodeType = 3; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}
function parseSel(sel) {
  return sel.split(',').map((alt) => alt.trim().split(/\s+/).map((part) => {
    const c = { tag: null, id: null, cls: [], attrs: [] };
    const re = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
    let m;
    while ((m = re.exec(part))) {
      if (m[1]) c.tag = m[1].toLowerCase(); else if (m[2]) c.id = m[2]; else if (m[3]) c.cls.push(m[3]); else c.attrs.push([m[4], m[5]]);
    }
    return c;
  }));
}
function matchOne(el, c) {
  if (!(el instanceof E)) return false;
  if (c.tag && el.localName !== c.tag) return false;
  if (c.id && el.getAttribute('id') !== c.id) return false;
  const cl = (el.getAttribute('class') || '').split(/\s+/);
  if (c.cls.some((k) => !cl.includes(k))) return false;
  return c.attrs.every(([k, v]) => el.hasAttribute(k) && (v === undefined || el.getAttribute(k) === v));
}
function matches(el, sel) {
  return parseSel(sel).some((parts) => {
    if (!matchOne(el, parts[parts.length - 1])) return false;
    let i = parts.length - 2, x = el.parentNode;
    while (i >= 0 && x) { if (matchOne(x, parts[i])) i--; x = x.parentNode; }
    return i < 0;
  });
}
class E extends N {
  constructor(doc, tag, ns) {
    super(doc); this.localName = tag.toLowerCase(); this.tagName = tag.toUpperCase(); this.namespaceURI = ns; this.nodeType = 1;
    this.attrs = new Map(); this.listeners = {};
    const style = {};
    Object.defineProperty(style, 'cssText', { set() { bad('cssText'); }, get() { return ''; } });
    style.setProperty = (k, v) => { style[k] = v; };
    this.style = style;
  }
  setAttribute(k, v) { if (/^on/i.test(k) || k.toLowerCase() === 'style') bad('setAttribute ' + k); this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  get id() { return this.getAttribute('id') || ''; }
  get hidden() { return this.attrs.has('hidden'); }
  set hidden(v) { if (v) this.attrs.set('hidden', ''); else this.attrs.delete('hidden'); }
  get disabled() { return this.attrs.has('disabled'); }
  get classList() {
    const get = () => (this.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    const put = (l) => this.attrs.set('class', l.join(' '));
    return { contains: (c) => get().includes(c), add: (...c) => put([...new Set(get().concat(c))]), remove: (...c) => put(get().filter((x) => !c.includes(x))) };
  }
  set innerHTML(v) { bad('innerHTML'); }
  get innerHTML() { return bad('innerHTML read'); }
  set outerHTML(v) { bad('outerHTML'); }
  insertAdjacentHTML() { bad('insertAdjacentHTML'); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    for (let n = this; n && !ev._stop; n = n.parentNode) for (const f of ((n.listeners || {})[ev.type] || []).slice()) { ev.currentTarget = n; f(ev); }
    return !ev.defaultPrevented;
  }
  click() { if (!this.disabled) this.dispatchEvent(new Ev('click')); }
  matches(sel) { return matches(this, sel); }
  closest(sel) { for (let x = this; x instanceof E; x = x.parentNode) if (matches(x, sel)) return x; return null; }
  querySelectorAll(sel) { const out = []; const walk = (n) => n.childNodes.forEach((c) => { if (c instanceof E) { if (matches(c, sel)) out.push(c); walk(c); } }); walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() { return { left: 10, top: 10, right: 50, bottom: 30, width: 40, height: 20 }; }
  get offsetHeight() { return 20; }
  scrollIntoView() {}
}
function makeDoc() {
  const doc = { defaultView: null };
  doc.createElement = (tag) => new E(doc, tag, 'http://www.w3.org/1999/xhtml');
  doc.createElementNS = (ns, tag) => new E(doc, tag, ns);
  doc.createTextNode = (t) => new T(doc, t);
  doc.write = () => bad('document.write');
  return doc;
}
const tick = () => new Promise((r) => setImmediate(r));
const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);

function mount(json, opts) {
  opts = opts || {};
  const doc = makeDoc();
  const root = doc.createElement('div');
  const calls = { approve: [], refresh: 0, openDoc: [], selectPlan: [], loadDoc: [], mountHtml: [], openLink: [], titles: [], nav: [], storage: [] };
  const actions = {
    approve: (a) => { calls.approve.push(a); return Promise.resolve({ ok: true, word: 'GO', text: 'zapisano' }); },
    refresh: () => { calls.refresh++; },
    openDoc: (p) => { calls.openDoc.push(p); },
    selectPlan: (p) => { calls.selectPlan.push(p); },
    openLink: (k, id, url) => { calls.openLink.push([k, id, url]); return true; },
  };
  if (opts.preview) actions.loadDoc = (p) => { calls.loadDoc.push(p); return Promise.resolve({ path: p, status: 'draft', mtime: '2026-01-12T10:00:00Z', html: '<p>x</p>' }); };
  Object.assign(actions, opts.actions || {});
  const board = R.create(root, {
    actions, now: () => (typeof opts.now === 'function' ? opts.now() : opts.now), raf: (f) => { f(); return 0; },
    ...(opts.deps || {}),
    storage: { get: () => null, set: (k, v) => calls.storage.push([k, v]) },
    theme: opts.theme ? { isDark: () => false, toggle: () => { calls.theme = (calls.theme || 0) + 1; } } : undefined,
    mountHtml: (el, html) => { calls.mountHtml.push(html); },
    onTitle: (t) => calls.titles.push(t), onNavigate: (h) => calls.nav.push(h),
  });
  board.setDescriptions({});
  if (json) board.update(json);
  return { doc, root, board, calls };
}
function walk(n, f) { f(n); (n.childNodes || []).forEach((c) => walk(c, f)); }
function summary(m) {
  const q = (s) => m.root.querySelector(s), qa = (s) => m.root.querySelectorAll(s);
  const buttons = (scope) => scope.querySelectorAll('button[data-gate]').map((b) => ({
    gate: b.getAttribute('data-gate'), state: b.getAttribute('data-state'), disabled: b.disabled, text: text(b), cls: b.getAttribute('class'),
    extra: b.getAttribute('data-desc-extra'), approveAttr: b.hasAttribute('data-approve') }));
  const tags = new Set(); walk(m.root, (n) => { if (n instanceof E) tags.add(n.localName); });
  return {
    title: text(q('#title')),
    docTitle: m.calls.titles[m.calls.titles.length - 1] || null,
    acts: qa('#acts .act').map((a) => ({ key: a.getAttribute('data-key'), cls: a.getAttribute('class'), chip: text(a.querySelector('.act-head .chip')),
      chipEnd: a.querySelector('.act-head .chip').classList.contains('chip-end'), buttons: buttons(a), docBtn: a.querySelector('[data-open-doc]') ? a.querySelector('[data-open-doc]').getAttribute('data-open-doc') : null })),
    pct: text(q('#acts .pct')),
    fills: qa('.fill').map((f) => f.style.width),
    feed: qa('#feed li').length, feedText: text(q('#feed')), sidc: qa('#feed .sidc').map(text),
    bannerHidden: q('#waitBanner').hidden, banner: text(q('#waitBanner')), bannerButtons: buttons(q('#waitBanner')),
    phases: qa('#phases .phase').length, steps: qa('#phases .step').length,
    docCount: text(q('#docCount')), docItems: qa('#docList .doc-item').length,
    planMenu: qa('#planMenu li').length, planBtn: text(q('#planBtnText')),
    meta: text(q('#meta')), foot: text(q('#foot')), sprite: qa('symbol').length,
    readiness: q('#acts .readiness') ? { cls: q('#acts .readiness').getAttribute('class'), chip: text(q('#acts .readiness-chip')),
      chipDesc: q('#acts .readiness-chip').getAttribute('data-desc'), facts: text(q('#acts .readiness-facts')),
      open: q('#acts .readiness [data-open-doc]') ? q('#acts .readiness [data-open-doc]').getAttribute('data-open-doc') : null,
      inAct: q('#acts .act[data-key="plan"] .readiness') !== null } : null,
    questions: q('#acts .questions') ? { inAct: q('#acts .act[data-key="spec"] .questions') !== null, heads: qa('#acts .q-head').map(text),
      descs: qa('#acts .q-head').map((x) => x.getAttribute('data-desc')), groups: qa('#acts .q-group').map((g) => ({ cls: g.getAttribute('class'),
        items: g.querySelectorAll('li').map(text), none: g.querySelector('.q-none') !== null })), strong: qa('#acts .questions strong').map(text) } : null,
    substeps: q('#acts details.substeps') ? { inAct: q('#acts .act[data-key="real"] details.substeps') !== null, summary: text(q('#acts .substeps summary')),
      summaryDesc: q('#acts .substeps summary').getAttribute('data-desc'), open: q('#acts details.substeps').hasAttribute('open'),
      items: qa('#acts .sub-list li').map((li) => ({ id: text(li.querySelector('.q-id')), t: text(li.querySelector('.sub-t')), cls: li.getAttribute('class') })) } : null,
    copyButtons: qa('.wait-copy').length + qa('[data-copy-word]').length + qa('button').filter((b) => /Kopiuj|Skopiowano/.test(text(b))).length,
    planAuto: q('#planAuto') ? { hidden: q('#planAuto').hidden, text: text(q('#planAuto')), label: q('#planAuto').getAttribute('aria-label'), tip: q('#planBtn').getAttribute('data-tip'), btn: text(q('#planBtnText')) } : null,
    tags: [...tags].sort(),
  };
}

(async () => {
  const out = { fixtures: {}, violations };
  for (const [name, json] of Object.entries(input.fixtures)) {
    try { out.fixtures[name] = summary(mount(json, { now: input.now[name] })); } catch (e) { out.fixtures[name] = { error: String(e && e.stack || e) }; }
  }
  // Zgody: dwa kliknięcia (uzbrojenie, potem approve z sha z JSON-a); aktywne tylko przy approvals.here.
  out.approve = {};
  for (const [name, json] of Object.entries(input.fixtures)) {
    const acts = (json.approvals && json.approvals.actions || []).filter((a) => a.state === 'waiting');
    if (!acts.length) continue;
    const here = JSON.parse(JSON.stringify(json)); here.approvals.here = true;
    const res = [];
    for (const a of acts) {
      const m = mount(here, { now: input.now[name] });
      const btn = () => m.root.querySelector(`#acts button[data-approve="${a.kind}"]`);
      const first = btn();
      const before = { text: text(first), disabled: first.disabled };
      first.click();
      const armed = { text: text(btn()), cls: btn().getAttribute('class'), approve: m.calls.approve.length };
      btn().click();
      await tick(); await tick();
      res.push({ kind: a.kind, sha: a.sha, before, armed, calls: m.calls.approve, refresh: m.calls.refresh, notice: text(m.root.querySelector('#waitBanner .wait-item.is-ok')), state: m.board.state() });
      m.board.destroy();
    }
    out.approve[name] = res;
  }
  // Widz zdalny (approvals.viewer === true): przyciski nieaktywne z jednym powodem, klik nie woła approve; baner widoczny.
  // Tryby: true (stan serwera: viewer i here=false), true-here (sztucznie here=true: blokuje sam viewer), false, absent (here=true).
  out.viewer = {};
  for (const name of ['cp-ready', 'plan-in-progress', 'questions', 'silence']) {
    const res = {};
    for (const mode of ['true', 'true-here', 'false', 'absent']) {
      const j = JSON.parse(JSON.stringify(input.fixtures[name]));
      j.approvals.here = mode !== 'true';
      if (mode.startsWith('true')) j.approvals.viewer = true; else if (mode === 'false') j.approvals.viewer = false;
      const m = mount(j, { now: input.now[name] });
      res[mode] = summary(m);
      m.board.destroy();
      let calls = 0;
      for (const a of j.approvals.actions) {
        for (const scope of ['#acts', '#waitBanner']) {
          const c = mount(j, { now: input.now[name] });
          const btn = () => c.root.querySelector(`${scope} button[data-gate="${a.kind}"]`);
          if (btn()) { btn().dispatchEvent(new Ev('click')); if (btn()) btn().dispatchEvent(new Ev('click')); }
          await tick(); await tick();
          calls += c.calls.approve.length;
          c.board.destroy();
        }
      }
      res[mode].approveCalls = calls;
    }
    out.viewer[name] = res;
  }
  // Uzbrojenie wygasa po 5 s (zegar realny w rendererze: tu tylko destroy() czyści timery i nie trzyma procesu).
  // Dane jako tekst: znacznik w tytule i checkpoincie nie tworzy elementu.
  {
    const evil = JSON.parse(JSON.stringify(input.fixtures['plan-in-progress']));
    evil.plan.title = 'Plan: <img src=x onerror=alert(1)>';
    evil.run.checkpoints[0].text = '<script>alert(1)</script> S1 PASS';
    evil.waiting = [{ what: '<b>x</b>', source: 'checkpoint', since: null, action: '<i>y</i>', link: null }];
    const m = mount(evil, { now: input.now['plan-in-progress'] });
    out.xss = summary(m);
  }
  // Nieznane klucze i dodatkowe pola: bez wyjątku.
  {
    const extra = JSON.parse(JSON.stringify(input.fixtures['plan-in-progress']));
    extra.futureKey = { a: [1, 2] }; extra.acts[0].futureField = 'x'; extra.progress.phases.forEach((p) => { p.futureField = 1; });
    extra.approvals.actions.push({ kind: 'deploy', state: 'waiting', word: 'CPD', sha: 'x' });
    try { out.unknown = summary(mount(extra, { now: input.now['plan-in-progress'] })); } catch (e) { out.unknown = { error: String(e && e.stack || e) }; }
  }
  // Dokumenty: bez actions.loadDoc „Otwórz specyfikację” woła actions.openDoc; z loadDoc podgląd + mountHtml i #hash.
  {
    const spec = input.fixtures['spec-act'];
    const m = mount(spec, { now: input.now['spec-act'] });
    m.root.querySelector('#acts [data-open-doc]').click();
    const m2 = mount(spec, { now: input.now['spec-act'], preview: true });
    m2.root.querySelector('#acts [data-open-doc]').click();
    await tick(); await tick();
    out.docs = { openDoc: m.calls.openDoc, view: m.board.state().view, preview: { loadDoc: m2.calls.loadDoc, mountHtml: m2.calls.mountHtml, nav: m2.calls.nav,
      view: m2.board.state().view, docsHidden: m2.root.querySelector('#view-docs').hidden, bar: text(m2.root.querySelector('#docView .doc-bar')) } };
  }
  // Klawisze i motyw: P/D zakładki, T motyw (deps.theme), Esc zamyka menu; wybór planu przez storage + selectPlan.
  {
    const m = mount(input.fixtures['plan-in-progress'], { now: input.now['plan-in-progress'], theme: true });
    const key = (k) => m.board.handleKey({ key: k, target: m.root, ctrlKey: false, metaKey: false, altKey: false });
    key('d'); const v1 = m.board.state().view; key('P'); const v2 = m.board.state().view; key('t');
    m.root.querySelector('#planBtn').click(); const menuOpen = !m.root.querySelector('#planMenu').hidden;
    m.root.querySelectorAll('#planMenu li')[1].click();
    out.keys = { v1, v2, theme: m.calls.theme || 0, themeBtn: Boolean(m.root.querySelector('#themeBtn')), menuOpen, menuAfter: m.root.querySelector('#planMenu').hidden,
      selectPlan: m.calls.selectPlan, storage: m.calls.storage };
    const noTheme = mount(input.fixtures['empty'], { now: input.now['empty'] });
    out.keys.noThemeBtn = !noTheme.root.querySelector('#themeBtn');
    m.board.destroy();
    out.keys.destroyed = m.root.childNodes.length;
  }
  const fakeTimers = () => ({ q: new Map(), n: 0, set(fn) { const id = ++this.n; this.q.set(id, fn); return id; }, clear(id) { this.q.delete(id); },
    run() { const fns = [...this.q.values()]; this.q.clear(); fns.forEach((f) => f()); } });
  const F = input.fixtures, NOW = input.now, clone = (x) => JSON.parse(JSON.stringify(x));
  const ex = {};
  // m6: pułapki cienia DOM działają (kontrola dodatnia); zebrane naruszenia usuwamy z listy głównej.
  {
    const before = violations.length, d = makeDoc(), el = d.createElement('div');
    const tries = [() => { el.innerHTML = '<b>x</b>'; }, () => { el.outerHTML = 'x'; }, () => el.setAttribute('style', 'color:red'),
      () => el.insertAdjacentHTML('beforeend', 'x'), () => { el.style.cssText = 'x'; }, () => el.setAttribute('onclick', 'x'), () => d.write('x')];
    let thrown = 0;
    for (const t of tries) { try { t(); } catch { thrown++; } }
    ex.traps = { thrown, caught: violations.splice(before) };
  }
  // m2: canApprove nadpisuje approvals.here (webview); texts nadpisuje teksty wskaźnika i braku połączenia.
  {
    const q = F['questions'];
    const off = mount(q, { now: NOW['questions'] });
    const on = mount(q, { now: NOW['questions'], deps: { canApprove: () => true } });
    const b = () => on.root.querySelector('#acts button[data-gate="spec"]');
    const before = { disabled: b().disabled, approveAttr: b().hasAttribute('data-approve') };
    b().click(); b().click(); await tick(); await tick();
    const fail = mount(q, { now: NOW['questions'], deps: { canApprove: () => true, texts: { live: { live: 'Połączono' }, liveTip: { live: 'Webview' }, noConnection: 'Brak hosta' } },
      actions: { approve: () => Promise.reject(new Error('x')) } });
    fail.board.setLive('live');
    const live = { text: text(fail.root.querySelector('.live-text')), cls: fail.root.querySelector('#live').getAttribute('class'), tip: fail.root.querySelector('#live').getAttribute('data-tip') };
    fail.board.setLive('polling');
    const polling = text(fail.root.querySelector('.live-text'));
    const fb = () => fail.root.querySelector('#acts button[data-gate="spec"]');
    fb().click(); fb().click(); await tick(); await tick();
    ex.canApprove = { offDisabled: off.root.querySelector('#acts button[data-gate="spec"]').disabled, offFoot: text(off.root.querySelector('#foot')),
      before, calls: on.calls.approve.length, onFoot: text(on.root.querySelector('#foot')), live, polling, failNotice: text(fail.root.querySelector('#waitBanner .is-err')) };
  }
  // m4: tylko http(s) w href.
  {
    const j = clone(F['plan-in-progress']);
    j.plan.pr = { url: 'javascript:alert(1)', number: 7 };
    j.git = { branch: 'feat/x', head: 'abcdef1234', branchUrl: 'data:text/html,<b>x</b>', branchPushed: true, remote: { host: 'github' } };
    j.session = { url: ' JavaScript:alert(2)' };
    j.prototype = { port: 1, slug: 's', up: true };
    j.waiting = [{ what: 'CP', source: 'checkpoint', since: null, action: 'a', word: 'CP', link: { kind: 'prototype', slug: 's' } }];
    const m = mount(j, { now: NOW['plan-in-progress'], deps: { prototypeUrl: () => 'javascript:alert(3)' } });
    const ok = clone(j); ok.plan.pr.url = 'https://example.test/pr/7'; ok.git.branchUrl = 'http://example.test/b'; ok.session.url = 'https://example.test/s';
    const m2 = mount(ok, { now: NOW['plan-in-progress'], deps: { prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/` } });
    const hrefs = (r) => r.querySelectorAll('a').map((a) => [text(a), a.getAttribute('href')]);
    ex.urls = { bad: hrefs(m.root), good: hrefs(m2.root) };
  }
  // m5: obiekt w polu tekstowym nie przerywa renderowania.
  {
    const j = clone(F['plan-in-progress']);
    j.acts.find((a) => a.key === 'spec').detail = { x: 1 };
    j.progress.phases[0].steps[0].title = { y: 2 };
    j.run.checkpoints[0].time = { z: 3 };
    try { const m = mount(j, { now: NOW['plan-in-progress'] }); ex.objects = { acts: m.root.querySelectorAll('#acts .act').length, text: text(m.root) }; }
    catch (e) { ex.objects = { error: String(e && e.stack || e) }; }
  }
  // m7: przepięte listenery.
  {
    const j = clone(F['plan-in-progress']);
    const ph = j.progress.phases[0]; ph.state = 'done'; ph.total = Math.max(2, ph.total);
    const m = mount(j, { now: NOW['plan-in-progress'] });
    const sec = () => m.root.querySelector('#phases .phase');
    const head = () => sec().querySelector('.phase-head');
    const s0 = { folded: sec().classList.contains('is-folded'), toggle: head().getAttribute('data-phase-toggle'), expanded: head().getAttribute('aria-expanded') };
    head().click();
    const s1 = { folded: sec().classList.contains('is-folded'), expanded: head().getAttribute('aria-expanded') };
    const kd = (key) => { const e = new Ev('keydown', { key }); head().dispatchEvent(e); return e.defaultPrevented; };
    const otherPrevented = kd('a');
    const s2 = sec().classList.contains('is-folded');
    const enterPrevented = kd('Enter');
    const s3 = sec().classList.contains('is-folded');
    ex.fold = { s0, s1, otherPrevented, s2, enterPrevented, s3 };
  }
  {
    const j = clone(F['empty']);
    j.waiting = [{ what: 'odpowiedź', source: 'checkpoint', since: null, action: 'a', word: 'CP', link: { kind: 'doc', path: 'docs/plans/x.md' } }];
    const copyCalls = [];
    const spy = { copyText: (t) => { copyCalls.push(t); return Promise.resolve(); } };
    const m = mount(j, { now: NOW['empty'], actions: spy });
    m.root.querySelector('[data-wait-doc]').click();
    const sum = summary(m);
    const m2 = mount(j, { now: NOW['empty'] });
    const n = mount(F['empty'], { now: NOW['empty'] });
    n.board.setNotice({ ok: false, word: 'CP', text: 'odmowa' });
    const noticeShown = { hidden: n.root.querySelector('#waitBanner').hidden, text: text(n.root.querySelector('#waitBanner')) };
    n.root.querySelector('.wait-x').click();
    ex.banner = { openDoc: m.calls.openDoc, copyButtons: sum.copyButtons, copyCalls, copyCallsApi: typeof m.board.copyText, noticeCopy: summary(n).copyButtons, noticeShown, noticeClosed: n.root.querySelector('#waitBanner').hidden };
    m.board.destroy(); m2.board.destroy();
  }
  {
    const j = clone(F['plan-in-progress']);
    j.git = { branch: 'feat/x', head: 'abcdef1', branchUrl: 'https://example.test/b', branchPushed: true, remote: { host: 'azure' } };
    const ev = (m) => { const e = new Ev('click'); m.root.querySelector('a.git-link').dispatchEvent(e); return e.defaultPrevented; };
    const taken = mount(j, { now: NOW['plan-in-progress'] });
    const left = mount(j, { now: NOW['plan-in-progress'], actions: { openLink: () => false } });
    ex.links = { taken: ev(taken), left: ev(left), calls: taken.calls.openLink };
  }
  {
    const j = clone(F['cp-ready']); j.approvals.here = true;
    const tm = fakeTimers();
    const m = mount(j, { now: NOW['cp-ready'], deps: { timers: tm } });
    const b = () => m.root.querySelector('#acts button[data-approve="cp"]');
    b().click();
    const armed = { text: text(b()), timers: tm.q.size };
    tm.run();
    ex.arm = { armed, after: text(b()), state: m.board.state().armed, cls: b().getAttribute('class'), calls: m.calls.approve.length };
  }
  {
    const j = F['silence'];
    const since = Date.parse(j.silence.since);
    let t = since + 10 * 60000;
    const m = mount(j, { now: () => t });
    const hidden0 = m.root.querySelector('#waitBanner').hidden;
    const el0 = text(m.root.querySelector('#elapsed'));
    t = since + 40 * 60000 + 5000; m.board.tick();
    ex.tick = { hidden0, el0, hidden1: m.root.querySelector('#waitBanner').hidden, banner: text(m.root.querySelector('#waitBanner')), el1: text(m.root.querySelector('#elapsed')),
      ago: text(m.root.querySelector('#ago')) };
  }
  {
    const m = mount(F['empty'], { now: NOW['empty'] });
    m.board.setDescriptions({ checkpoints: { title: 'Tytuł', text: 'Opis' } });
    const h2 = m.root.querySelector('#feedTitle');
    const tip = m.root.querySelector('#descTip');
    h2.dispatchEvent(new Ev('focusin', { target: h2 }));
    const shown = { hidden: tip.hidden, text: text(tip), width: tip.style.width };
    m.board.handleKey({ key: 'Escape', target: m.root });
    ex.tip = { shown, hiddenAfter: tip.hidden };
  }
  {
    const frames = [], cancelled = [];
    const m = mount(F['plan-in-progress'], { now: NOW['plan-in-progress'], deps: { raf: (f) => { frames.push(f); return frames.length; }, cancelRaf: (id) => cancelled.push(id) } });
    const fill = m.root.querySelector('.fill');
    m.board.destroy();
    frames.forEach((f) => f());
    ex.raf = { frames: frames.length, cancelled, width: fill.style.width || '' };
    const noPreview = mount(null, { now: 0 }), preview = mount(null, { now: 0, preview: true });
    ex.emptyDoc = { noPreview: text(noPreview.root.querySelector('#docView')), preview: text(preview.root.querySelector('#docView')) };
  }
  // Runda 2 (#31): XSS w pytaniach i założeniach, rozwinięcie podkroków po update(), link readiness, widz bez „Kopiuj”.
  {
    const j = clone(F['questions']);
    j.blockingQuestions.items[0].question = '<img src=x onerror=alert(1)>';
    j.assumptions.items[0].question = '<script>alert(2)</script>';
    j.assumptions.items[0].accepted = '<b>tak</b>';
    j.blockingQuestions.items[0].id = '<i>Q1</i>';
    ex.qxss = summary(mount(j, { now: NOW['questions'] }));
    const v = clone(F['questions']); v.approvals.viewer = true; v.approvals.here = false;
    ex.qviewer = summary(mount(v, { now: NOW['questions'] }));
  }
  {
    const m = mount(F['plan-in-progress'], { now: NOW['plan-in-progress'] });
    const det = () => m.root.querySelector('details.substeps');
    const closed = det().hasAttribute('open');
    det().setAttribute('open', ''); det().dispatchEvent(new Ev('toggle'));
    m.board.update(clone(F['plan-in-progress']));
    const kept = det().hasAttribute('open');
    det().removeAttribute('open'); det().dispatchEvent(new Ev('toggle'));
    m.board.update(clone(F['plan-in-progress']));
    const closedAgain = det().hasAttribute('open');
    const r = mount(F['readiness-blocked'], { now: NOW['readiness-blocked'] });
    r.root.querySelector('.readiness [data-open-doc]').click();
    const rp = mount(F['readiness-blocked'], { now: NOW['readiness-blocked'], preview: true });
    rp.root.querySelector('.readiness [data-open-doc]').click();
    await tick(); await tick();
    ex.sub = { closed, kept, closedAgain, readinessOpenDoc: r.calls.openDoc, readinessLoadDoc: rp.calls.loadDoc, readinessView: rp.board.state().view };
  }
  {
    const res = {};
    for (const name of ['cp-ready', 'questions', 'readiness-blocked']) {
      const v = clone(F[name]); v.approvals.viewer = true; v.approvals.here = false;
      const n = clone(F[name]);
      const given = clone(F[name]); given.approvals.viewer = true; // notice z „Kopiuj „dalej”” u widza też bez kopiowania
      const g = mount(given, { now: NOW[name] }); g.board.setNotice({ ok: true, word: 'CP', text: 'x' });
      res[name] = { viewer: summary(mount(v, { now: NOW[name] })).copyButtons, normal: summary(mount(n, { now: NOW[name] })).copyButtons,
        viewerNotice: summary(g).copyButtons,
        viewerApproveButtons: summary(mount(v, { now: NOW[name] })).bannerButtons.map((b) => [b.disabled, b.extra]) };
    }
    ex.viewerCopy = res;
  }
  {
    const res = {};
    for (const name of Object.keys(F)) {
      const auto = clone(F[name]); auto.planAuto = true;
      const manual = clone(F[name]); manual.planAuto = false;
      res[name] = { auto: summary(mount(auto, { now: NOW[name] })).planAuto, manual: summary(mount(manual, { now: NOW[name] })).planAuto,
        hasPlan: Boolean(F[name].plan) };
    }
    ex.planAuto = res;
  }

  // DASH-STAB D1: toasty (komunikat wyniku zgody i baner „czeka”) gasną po wykonaniu zadania, a toast zgody sam po czasie.
  {
    const msTimers = () => ({ q: new Map(), n: 0, set(fn, ms) { const id = ++this.n; this.q.set(id, { fn, ms }); return id; }, clear(id) { this.q.delete(id); },
      run() { const items = [...this.q.values()]; this.q.clear(); items.forEach((x) => x.fn()); }, list() { return [...this.q.values()].map((x) => x.ms); } });
    const banner = (m) => ({ hidden: m.root.querySelector('#waitBanner').hidden, items: m.root.querySelectorAll('#waitBanner .wait-item').length,
      ok: m.root.querySelectorAll('#waitBanner .wait-item.is-ok').length, err: m.root.querySelectorAll('#waitBanner .wait-item.is-err').length,
      text: text(m.root.querySelector('#waitBanner')) });
    const stab = {};
    {  // (a) toast wyniku zgody: ok i błąd gasną same; odliczanie stoi, gdy karta jest w tle
      const j = clone(F['cp-ready']); j.approvals.here = true;
      const tm = msTimers();
      const m = mount(j, { now: NOW['cp-ready'], deps: { timers: tm } });
      const b = () => m.root.querySelector('#acts button[data-approve="cp"]');
      b().click(); b().click(); await tick(); await tick();
      const shown = banner(m), timersWhileShown = tm.list();
      let hiddenTimers = null, backAgain = null;
      if (typeof m.board.setVisible === 'function') {
        m.board.setVisible(false); hiddenTimers = tm.list();
        m.board.setVisible(true); backAgain = tm.list();
      }
      tm.run();
      stab.notice = { shown, timersWhileShown, hiddenTimers, backAgain, after: banner(m), hasSetVisible: typeof m.board.setVisible === 'function', state: m.board.state().notice };
      const failingTm = msTimers();
      const failing = mount(j, { now: NOW['cp-ready'], deps: { timers: failingTm }, actions: { approve: () => Promise.reject(new Error('x')) } });
      const fb = () => failing.root.querySelector('#acts button[data-approve="cp"]');
      fb().click(); fb().click(); await tick(); await tick();
      stab.errorShown = banner(failing);
      stab.errorTimers = failingTm.list();
      failingTm.run(); stab.errorAfter = banner(failing);
      // toast ustawiony przy ukrytej karcie nie ma timera; startuje dopiero po powrocie
      const tm2 = msTimers(); const bg = mount(clone(F['cp-ready']), { now: NOW['cp-ready'], deps: { timers: tm2 } });
      const hasVis = typeof bg.board.setVisible === 'function';
      if (hasVis) bg.board.setVisible(false);
      bg.board.setNotice({ ok: false, word: 'CP', text: 'x' });
      const bgShown = { timers: tm2.list(), notice: banner(bg).err };
      if (hasVis) bg.board.setVisible(true);
      stab.background = { ...bgShown, afterReturn: tm2.list() };
    }
    {  // (b)(c) element banera „czeka” przy zapisanej zgodzie: znika, gdy sesja ruszyła albo zgoda jest stara; świeża i bez raportu zostaje
      const GEN = Date.parse('2026-01-12T12:00:00Z');
      const scenario = (kind, o) => {
        const j = clone(F['cp-ready']);
        j.waiting = [{ what: kind, word: kind, approve: kind, source: 'checkpoint', since: '11:50', link: null, action: 'Zatwierdź.' }];
        const act = j.approvals.actions.find((x) => x.kind === kind);
        act.state = 'after'; act.given = o.at === undefined ? null : { at: o.at, by: 'user', word: act.word, sha: act.sha, source: 'cli', schema: 1 };
        j.run.lastReportAt = o.reported; j.delivery.ahead = o.ahead === undefined ? 0 : o.ahead;
        if (o.plan) j.plan.status = o.plan;
        let t = GEN;
        const m = mount(j, { now: () => t });
        const first = banner(m), title = m.calls.titles[m.calls.titles.length - 1];
        return { first, title, advance(ms) { t += ms; m.board.tick(); return banner(m); } };
      };
      const fresh = '2026-01-12T11:58:00Z', old = '2026-01-12T10:00:00Z';
      const r = {
        freshQuiet: scenario('plan', { at: fresh, reported: '2026-01-12T11:00:00+00:00' }),
        reportedAfter: scenario('plan', { at: fresh, reported: '2026-01-12T11:59:00+00:00' }),
        oldConsent: scenario('plan', { at: old, reported: '2026-01-12T09:00:00+00:00' }),
        noAtRunning: scenario('plan', { at: undefined, reported: '2026-01-12T09:00:00+00:00', plan: 'in_progress' }),
        noAtApproved: scenario('plan', { at: undefined, reported: '2026-01-12T09:00:00+00:00', plan: 'approved' }),
        badAt: scenario('plan', { at: 'x', reported: '2026-01-12T09:00:00+00:00', plan: 'in_progress' }),
        cpAheadZeroFresh: scenario('cp', { at: fresh, reported: '2026-01-12T11:00:00+00:00', ahead: 0 }),
        readyFresh: scenario('ready', { at: fresh, reported: '2026-01-12T11:00:00+00:00', ahead: 0 }),
        cpReported: scenario('cp', { at: fresh, reported: '2026-01-12T11:59:00+00:00', ahead: 3 }),
      };
      const aging = scenario('plan', { at: fresh, reported: '2026-01-12T11:00:00+00:00' });
      stab.given = { ...Object.fromEntries(Object.entries(r).map(([k, v]) => [k, { items: v.first.items, title: v.title }])),
        agingBefore: aging.first.items, agingAfter: aging.advance(11 * 60000).items };
    }
    {  // D3: licznik niewypchniętych commitów przy gałęzi (delivery.ahead / delivery.pushed, bez zmiany kontraktu)
      const chip = (ahead, pushed, branchPushed, extra) => {
        const j = clone(F['plan-in-progress']);
        j.git = { branch: 'feat/x', head: 'abcdef1234', headFull: 'abcdef1234', branchUrl: 'https://example.test/b', branchPushed, remote: { host: 'github', repo: 'o/r' } };
        j.delivery = { ...j.delivery, mode: 'pr', branch: 'feat/x', remote: 'origin/feat/x', ahead, pushed, ...(extra || {}) };
        const m = mount(j, { now: NOW['plan-in-progress'] });
        const el = m.root.querySelector('#meta .ahead-chip');
        return el ? { text: text(el), cls: el.getAttribute('class'), tip: el.getAttribute('data-tip') } : null;
      };
      stab.ahead = { sixteen: chip(16, true, true), one: chip(1, false, false), two: chip(2, true, true), five: chip(5, true, true), twelve: chip(12, true, true),
        twentytwo: chip(22, true, true), zero: chip(0, true, true), unknown: chip(null, null, true), notPushed: chip(0, false, false), direct: chip(3, true, true, { mode: 'direct' }) };
    }
    ex.stab = stab;
  }
  out.extra = ex;
  // deps.now jest wymagane (renderer nie czyta zegara sam).
  try { R.create(makeDoc().createElement('div'), {}); out.nowRequired = false; } catch (e) { out.nowRequired = e instanceof TypeError; }
  out.exports = Object.keys(R).sort();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


def expected_title(d: dict) -> str:
    plan, spec = d.get("plan"), d.get("spec") or {}
    if plan:
        return re.sub(r"^Plan:\s*", "", plan["title"], flags=re.I)
    if d.get("mode") == "spec" and spec.get("exists"):
        return re.sub(r"^Spec:\s*", "", spec.get("title") or spec["path"], flags=re.I)
    return "Akt I: specyfikacja w przygotowaniu" if d.get("mode") == "run" else "Brak planu w realizacji"


def fixture_files() -> dict[str, dict]:
    out = {}
    for path in sorted(FIXTURES.glob("*/expected*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "acts" in data:  # expected-metrics.json to inny kontrakt (dashboard metrics)
            out[path.parent.name + ("" if path.name == "expected.json" else "/" + path.stem[len("expected-"):])] = data
    return out


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class RendererNodeTests(unittest.TestCase):
    STATE = {"after": "after", "waiting": "waiting"}  # inny stan akcji = przycisk „running”

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = fixture_files()
        cls.now = {name: ms(d["generatedAt"]) for name, d in cls.fixtures.items()}
        with tempfile.TemporaryDirectory() as tmp:
            harness, data = Path(tmp) / "harness.cjs", Path(tmp) / "input.json"
            harness.write_text(HARNESS, encoding="utf-8")
            data.write_text(json.dumps({"fixtures": cls.fixtures, "now": cls.now}), encoding="utf-8")
            proc = subprocess.run([NODE, str(harness), str(RENDERER), str(data)], capture_output=True, text=True, timeout=60, cwd=tmp)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise AssertionError(f"node rc={proc.returncode}: {proc.stderr[-2000:]}")
        cls.out = json.loads(proc.stdout)
        if "fatal" in cls.out:
            raise AssertionError(cls.out["fatal"])

    def test_all_contract_fixtures_are_covered(self) -> None:
        dirs = {p.name for p in FIXTURES.iterdir() if p.is_dir()}
        self.assertEqual({"empty", "plan-in-progress", "spec-act", "questions", "readiness-blocked", "run-mode", "silence", "cp-ready"} - dirs, set())
        self.assertEqual({n.split("/")[0] for n in self.fixtures}, dirs)
        self.assertEqual(self.out["violations"], [])

    def test_fixture_views(self) -> None:
        for name, d in self.fixtures.items():
            with self.subTest(fixture=name):
                got = self.out["fixtures"][name]
                self.assertNotIn("error", got, got.get("error"))
                self.assertEqual(got["title"], expected_title(d))
                self.assertEqual([a["key"] for a in got["acts"]], [a["key"] for a in d["acts"]])
                self.assertEqual(len(got["acts"]), 3)
                labels = {"done": "zrealizowany", "active": "w trakcie", "planned": "zaplanowany"}
                for act, src in zip(got["acts"], d["acts"]):
                    self.assertIn(f"s-{src['state']}", act["cls"].split())
                    self.assertEqual(act["chip"], labels[src["state"]])
                    self.assertTrue(act["chipEnd"])
                real = next(a for a in d["acts"] if a["key"] == "real")
                p = d["progress"]
                self.assertEqual(got["pct"], ("✓ " if real["state"] == "done" and p["percent"] < 100 else "") + f"{p['percent']}%")
                self.assertIn(f"{p['percent']}%", got["fills"])
                checkpoints = (d.get("run") or {}).get("checkpoints") or []
                self.assertEqual(got["feed"], len(checkpoints) or 1)
                if not checkpoints:
                    self.assertIn("Brak checkpointów", got["feedText"])
                silence = d.get("silence")
                quiet = bool(silence and silence.get("since")
                             and (self.now[name] - ms(silence["since"])) // 60000 >= silence["thresholdMin"])
                self.assertEqual(got["bannerHidden"], not (d.get("waiting") or quiet))
                for w in d.get("waiting") or []:
                    self.assertIn(f": {w['what']}", got["banner"])
                if quiet:
                    self.assertIn("Brak raportu postępu", got["banner"])
                if d.get("plan"):
                    self.assertEqual(got["steps"], sum(len(ph["steps"]) for ph in p["phases"]))
                    self.assertEqual(got["docTitle"].startswith("⏳ Czeka · "), bool(d.get("waiting")))
                    self.assertIn(f"{p['percent']}% · ", got["docTitle"])
                self.assertEqual(got["docItems"], sum(len(g["items"]) for g in d.get("docs") or []))
                self.assertEqual(got["planMenu"], 1 + len(d.get("plans") or []))
                self.assertEqual(got["sprite"], 14)
                self.assertIn(f"dashboard {d['app']['version']}", got["foot"])
                self.assertNotIn("img", got["tags"])

    def test_approval_button_states(self) -> None:
        slot = {"spec": "spec", "plan": "plan", "cp": "real", "ready": "real"}
        for name, d in self.fixtures.items():
            ap = d.get("approvals") or {}
            with self.subTest(fixture=name):
                got = {a["key"]: a["buttons"] for a in self.out["fixtures"][name]["acts"]}
                expected = {"spec": [], "plan": [], "real": []}
                for a in ap.get("actions") or [] if ap.get("enabled") else []:
                    expected[slot[a["kind"]]].append((a["kind"], self.STATE.get(a["state"], "running")))
                for key, want in expected.items():
                    self.assertEqual([(b["gate"], b["state"]) for b in got[key]], want, key)
                    for b in got[key]:
                        self.assertTrue(b["disabled"], b)  # fixtures nie mają approvals.here: wszystkie nieaktywne
                        if b["state"] == "after":
                            self.assertIn("is-given", b["cls"])
                        if b["state"] == "running":
                            self.assertIn("is-waiting", b["cls"])

    def test_double_click_approves_with_sha_from_json(self) -> None:
        self.assertTrue(self.out["approve"], "żaden fixture nie ma zgody w stanie waiting")
        self.assertIn("cp-ready", self.out["approve"])
        for name, runs in self.out["approve"].items():
            for run in runs:
                with self.subTest(fixture=name, kind=run["kind"]):
                    self.assertFalse(run["before"]["disabled"])
                    self.assertTrue(run["before"]["text"].startswith("Zatwierdź „"), run["before"])
                    self.assertTrue(run["armed"]["text"].startswith("Na pewno? Kliknij"), run["armed"])
                    self.assertIn("is-armed", run["armed"]["cls"])
                    self.assertEqual(run["armed"]["approve"], 0)  # pierwsze kliknięcie tylko uzbraja
                    self.assertEqual(len(run["calls"]), 1)
                    self.assertEqual((run["calls"][0]["kind"], run["calls"][0]["sha"]), (run["kind"], run["sha"]))
                    self.assertEqual(run["refresh"], 1)
                    self.assertIn("Zgoda „GO” zapisana", run["notice"])
                    self.assertIsNone(run["state"]["armed"])

    def test_shadow_dom_traps_fire(self) -> None:
        traps = self.out["extra"]["traps"]
        self.assertEqual(traps["thrown"], 7)
        self.assertEqual(traps["caught"], ["innerHTML", "outerHTML", "setAttribute style", "insertAdjacentHTML", "cssText", "setAttribute onclick", "document.write"])

    def test_can_approve_and_texts_from_host(self) -> None:
        c = self.out["extra"]["canApprove"]
        self.assertTrue(c["offDisabled"])  # bez here i bez canApprove: nieaktywny (jak dotąd)
        self.assertIn("tylko odczyt", c["offFoot"])
        self.assertEqual(c["before"], {"disabled": False, "approveAttr": True})
        self.assertEqual(c["calls"], 1)
        self.assertIn("odczyt + zgody", c["onFoot"])
        self.assertEqual(c["live"], {"text": "Połączono", "cls": "live is-live", "tip": "Webview"})
        self.assertEqual(c["polling"], "Odświeżam co 5 s")
        self.assertIn("Brak hosta", c["failNotice"])

    def test_only_http_urls_become_href(self) -> None:
        bad, good = self.out["extra"]["urls"]["bad"], self.out["extra"]["urls"]["good"]
        self.assertGreaterEqual(len(bad), 4)  # PR, gałąź, prototyp (zakładka i baner), sesja
        self.assertEqual([h for _, h in bad], [None] * len(bad))
        self.assertTrue(any(t == "PR #7" for t, _ in bad))
        self.assertEqual({h for _, h in good}, {"https://example.test/pr/7", "http://example.test/b", "https://example.test/s", "http://127.0.0.1:1/s/"})

    def test_objects_in_text_fields_are_text(self) -> None:
        got = self.out["extra"]["objects"]
        self.assertNotIn("error", got, got.get("error"))
        self.assertEqual(got["acts"], 3)
        self.assertIn("[object Object]", got["text"])

    def test_rewired_listeners(self) -> None:
        ex = self.out["extra"]
        f = ex["fold"]
        self.assertEqual(f["s0"]["folded"], True)
        self.assertEqual(f["s0"]["expanded"], "false")
        self.assertEqual(f["s1"], {"folded": False, "expanded": "true"})  # kliknięcie rozwija
        self.assertEqual((f["otherPrevented"], f["s2"]), (False, False))  # inny klawisz nic nie robi
        self.assertEqual((f["enterPrevented"], f["s3"]), (True, True))  # Enter zwija
        b = ex["banner"]
        self.assertEqual(b["openDoc"], ["docs/plans/x.md"])
        self.assertEqual((b["copyButtons"], b["noticeCopy"]), (0, 0))  # brak przycisków „Kopiuj” (karta, baner, komunikat)
        self.assertEqual(b["copyCalls"], [])
        self.assertFalse(b["noticeShown"]["hidden"])
        self.assertIn("Nie zapisano „CP”", b["noticeShown"]["text"])
        self.assertTrue(b["noticeClosed"])
        self.assertEqual((ex["links"]["taken"], ex["links"]["left"]), (True, False))
        self.assertEqual(ex["links"]["calls"], [["branch", "feat/x", "https://example.test/b"]])
        a = ex["arm"]
        self.assertTrue(a["armed"]["text"].startswith("Na pewno?"))
        self.assertEqual(a["armed"]["timers"], 1)
        self.assertTrue(a["after"].startswith("Zatwierdź „CP”"))  # uzbrojenie wygasło
        self.assertIsNone(a["state"])
        self.assertNotIn("is-armed", a["cls"])
        self.assertEqual(a["calls"], 0)
        t = ex["tick"]
        self.assertTrue(t["hidden0"])
        self.assertFalse(t["hidden1"])
        self.assertIn("Brak raportu postępu: od 40 min", t["banner"])
        self.assertNotEqual(t["el0"], t["el1"])
        self.assertTrue(t["ago"])
        tip = ex["tip"]
        self.assertEqual((tip["shown"]["hidden"], tip["shown"]["text"]), (False, "TytułOpis"))
        self.assertTrue(tip["shown"]["width"].endswith("px"))
        self.assertTrue(tip["hiddenAfter"])
        r = ex["raf"]
        self.assertGreaterEqual(r["frames"], 2)
        self.assertEqual(sorted(r["cancelled"]), list(range(1, r["frames"] + 1)))
        self.assertEqual(r["width"], "")  # klatka po destroy() nic nie rysuje
        self.assertEqual(ex["emptyDoc"]["noPreview"], "Wybierz dokument z listy po lewej.")
        self.assertIn("Podgląd odświeża się sam", ex["emptyDoc"]["preview"])

    def test_stab_approval_toast_expires_by_itself_and_waits_for_visibility(self) -> None:
        """D1 przyczyna 1: toast wyniku zgody gasł tylko po kliknięciu „×”; teraz sam (ok 10 s, błąd 30 s), a w tle odliczanie stoi."""
        stab = self.out["extra"]["stab"]
        n = stab["notice"]
        self.assertEqual((n["shown"]["ok"], n["shown"]["items"]), (1, 2), n)  # toast zgody + baner „czeka: CP”
        self.assertEqual(n["timersWhileShown"], [10000], n)
        self.assertTrue(n["hasSetVisible"])
        self.assertEqual(n["hiddenTimers"], [], "w tle odliczanie stoi")
        self.assertEqual(n["backAgain"], [10000], "po powrocie startuje od nowa")
        self.assertEqual(n["after"]["ok"], 0, n)
        self.assertIsNone(n["state"])
        self.assertEqual(stab["errorShown"]["err"], 1, stab["errorShown"])
        self.assertEqual(stab["errorTimers"], [30000], "błąd gaśnie po 30 s, nie po 10 s")
        self.assertEqual(stab["errorAfter"]["err"], 0)
        self.assertEqual(stab["background"], {"timers": [], "notice": 1, "afterReturn": [30000]})  # toast w tle: bez timera, start po powrocie

    def test_stab_waiting_banner_goes_when_the_session_moved_or_consent_is_old(self) -> None:
        """D1 przyczyna 2: zgoda zapisana, sesja ruszyła (raport po zgodzie) albo zgoda ma ponad 10 min: baner i tytuł karty nie ostrzegają.
        Świeża zgoda bez raportu zostaje; `delivery.ahead` i READY nie są sygnałem wykonania."""
        g = self.out["extra"]["stab"]["given"]
        self.assertEqual(g["freshQuiet"]["items"], 1, g)
        self.assertTrue(g["freshQuiet"]["title"].startswith("⏳"), g["freshQuiet"])
        self.assertEqual(g["reportedAfter"]["items"], 0, g)
        self.assertFalse(g["reportedAfter"]["title"].startswith("⏳"), "tytuł karty też przestaje sygnalizować oczekiwanie")
        self.assertEqual(g["oldConsent"]["items"], 0, g)
        self.assertEqual(g["noAtRunning"]["items"], 0, g)
        self.assertEqual(g["badAt"]["items"], 0, g)
        self.assertEqual(g["noAtApproved"]["items"], 1, g)  # zgoda ze statusu pliku, plan jeszcze nie ruszył
        self.assertEqual(g["cpAheadZeroFresh"]["items"], 1, "ahead 0 nie dowodzi wykonania CP")
        self.assertEqual(g["readyFresh"]["items"], 1, "READY nie zmienia liczby commitów")
        self.assertEqual(g["cpReported"]["items"], 0, g)
        self.assertEqual((g["agingBefore"], g["agingAfter"]), (1, 0), "zgoda starzeje się bez nowych danych z serwera (tick)")

    def test_stab_header_shows_unpushed_commits_next_to_the_branch(self) -> None:
        """D3: `branchPushed` znaczy tylko „origin/<gałąź> istnieje”; liczba niewypchniętych commitów (delivery.ahead) ma być widoczna przy gałęzi."""
        a = self.out["extra"]["stab"]["ahead"]
        self.assertEqual(a["sixteen"]["text"], "16 niewypchniętych", a)  # branchPushed: true, a 16 commitów lokalnie
        self.assertIn("16 commitów", a["sixteen"]["tip"])
        self.assertIn("s-warn", a["sixteen"]["cls"])
        self.assertEqual(a["one"]["text"], "1 niewypchnięty", a)
        self.assertIn("1 commit lokalnie", a["one"]["tip"])
        self.assertIn("nie ma jej jeszcze na origin", a["one"]["tip"])
        self.assertEqual(a["two"]["text"], "2 niewypchnięte")
        self.assertIn("2 commity", a["two"]["tip"])
        self.assertEqual(a["five"]["text"], "5 niewypchniętych")
        self.assertEqual(a["twelve"]["text"], "12 niewypchniętych")
        self.assertEqual(a["twentytwo"]["text"], "22 niewypchnięte")
        self.assertIsNone(a["zero"], "nic niewypchniętego: bez znacznika")
        self.assertIsNone(a["unknown"], "ahead nieznane (null): bez znacznika")
        self.assertIsNone(a["notPushed"], "gałąź bez własnych commitów nie ma czego wypychać")
        self.assertIn("s-planned", a["direct"]["cls"], "w trybie direct lokalne commity są normą do CP")

    def test_readiness_tile(self) -> None:
        none = {"cls": "readiness is-none", "chip": "Readiness: brak danych"}
        for name, d in self.fixtures.items():
            with self.subTest(fixture=name):
                got = self.out["fixtures"][name]["readiness"]
                self.assertIsNotNone(got)  # kafelek aktu II zawsze (jak w #31), bez danych neutralny
                self.assertTrue(got["inAct"])
                self.assertEqual(got["chipDesc"], "stage_readiness")
                r = d["readiness"]
                if not r.get("status"):
                    self.assertEqual({k: got[k] for k in none}, none)
                    self.assertIsNone(got["open"])
        pip, rb = self.out["fixtures"]["plan-in-progress"]["readiness"], self.out["fixtures"]["readiness-blocked"]["readiness"]
        self.assertEqual((pip["cls"], pip["chip"]), ("readiness is-pass", "Readiness: PASS"))
        self.assertIn("2026-01-10", pip["facts"])
        self.assertIn("plan 01234567", pip["facts"])
        self.assertEqual((rb["cls"], rb["chip"]), ("readiness is-blocked", "Readiness: BLOCKED"))
        self.assertIn("brief dla innej wersji planu", rb["facts"])
        self.assertIn("plan 22222222", rb["facts"])
        self.assertEqual(rb["open"], ".skillsync/runs/2026-01-11-demo-export/readiness.md")
        # skrócony planSha zgodny z prefiksem pełnego fileSha = brak znacznika
        cp = self.out["fixtures"]["cp-ready"]["readiness"]
        self.assertNotIn("brief dla innej wersji planu", cp["facts"] or "")
        sub = self.out["extra"]["sub"]
        self.assertEqual(sub["readinessOpenDoc"], [rb["open"]])
        self.assertEqual((sub["readinessLoadDoc"], sub["readinessView"]), ([rb["open"]], "docs"))

    def test_questions_section(self) -> None:
        q = self.out["fixtures"]["questions"]["questions"]
        d = self.fixtures["questions"]
        self.assertTrue(q["inAct"])
        self.assertEqual(q["heads"], ["Pytania blokujące (2)", "Założenia do korekty przy prototypie (3)"])
        self.assertEqual(q["descs"], ["questions_blocking", "questions_assumptions"])
        self.assertIn("is-blocking", q["groups"][0]["cls"])
        self.assertEqual(len(q["groups"][0]["items"]), 2)
        self.assertEqual(len(q["groups"][1]["items"]), 3)
        self.assertEqual(q["strong"], [a["accepted"] or "—" for a in d["assumptions"]["items"]])
        for item, src in zip(q["groups"][1]["items"], d["assumptions"]["items"]):
            self.assertIn(f"{src['question']} — przyjęte: ", item)
        sa = self.out["fixtures"]["spec-act"]["questions"]  # zero blokujących = „brak”
        self.assertEqual(sa["heads"][0], "Pytania blokujące (0)")
        self.assertTrue(sa["groups"][0]["none"])
        for name in ("empty", "run-mode", "silence"):
            self.assertIsNone(self.out["fixtures"][name]["questions"], name)  # brak speca i pytań = brak sekcji
        x = self.out["extra"]["qxss"]
        self.assertNotIn("error", x)
        for tag in ("img", "script", "b", "i"):
            self.assertNotIn(tag, x["tags"])
        self.assertIn("<img src=x onerror=alert(1)>", x["questions"]["groups"][0]["items"][0])
        self.assertIn("<i>Q1</i>", x["questions"]["groups"][0]["items"][0])
        self.assertIn("<script>alert(2)</script> — przyjęte: <b>tak</b>", x["questions"]["groups"][1]["items"][0])
        self.assertEqual(x["questions"]["strong"][0], "<b>tak</b>")
        self.assertEqual(self.out["extra"]["qviewer"]["questions"], q)  # widz widzi to samo

    def test_substeps_section(self) -> None:
        got = self.out["fixtures"]["plan-in-progress"]["substeps"]
        d = self.fixtures["plan-in-progress"]
        self.assertTrue(got["inAct"])
        self.assertEqual((got["summary"], got["summaryDesc"], got["open"]), ("Podkroki i czas (5)", "substeps_time", False))
        self.assertEqual([i["id"] for i in got["items"]], [s["id"] for s in d["substeps"]])
        self.assertEqual(got["items"][0]["t"], "15:00")  # 900 s
        self.assertEqual((got["items"][1]["t"], got["items"][1]["cls"]), ("brak danych", "is-nodata"))
        self.assertEqual([i["t"] for i in got["items"][2:]], ["w toku", "zaplanowany", "zaplanowany"])
        self.assertEqual(self.out["fixtures"]["cp-ready"]["substeps"]["items"][0]["t"], "35:00")
        for name in ("empty", "run-mode", "questions", "readiness-blocked"):
            self.assertIsNone(self.out["fixtures"][name]["substeps"], name)
        sub = self.out["extra"]["sub"]
        self.assertEqual((sub["closed"], sub["kept"], sub["closedAgain"]), (False, True, False))

    def test_no_copy_buttons_anywhere(self) -> None:
        for name, got in self.out["fixtures"].items():
            with self.subTest(fixture=name):
                self.assertEqual(got["copyButtons"], 0)
        for path in (RENDERER, ADAPTER, ASSETS / "app.css", ASSETS / "index.html"):
            src = path.read_text(encoding="utf-8")
            for word in ("copyText", "clipboard", "data-copy-word", "wait-copy", "COPY_RESET_MS", "copyWord"):
                self.assertNotIn(word, src, f"{path.name}: {word}")

    def test_plan_auto_badge(self) -> None:
        for name, got in self.out["extra"]["planAuto"].items():
            with self.subTest(fixture=name):
                if not got["hasPlan"]:
                    self.assertTrue(got["auto"]["hidden"])
                    continue
                a, m = got["auto"], got["manual"]
                self.assertFalse(a["hidden"])
                self.assertEqual(a["text"], "wybrany automatycznie")
                self.assertIn("automatycznie", a["label"])
                self.assertIn("z listy", a["label"])
                self.assertIn("docs/plans/", a["tip"])
                self.assertIn("automatycznie", a["tip"])
                self.assertNotIn("(bieżący)", a["btn"])
                self.assertTrue(m["hidden"])
                self.assertIn("ręcznie", m["tip"])
                self.assertNotIn("automatycznie", m["tip"])

    def test_viewer_has_no_copy_buttons(self) -> None:
        for name, got in self.out["extra"]["viewerCopy"].items():
            with self.subTest(fixture=name):
                self.assertEqual(got["viewer"], 0)
                self.assertEqual(got["viewerNotice"], 0)
                self.assertEqual(got["normal"], 0)  # także bez widza przycisków kopiowania nie ma
                for disabled, extra in got["viewerApproveButtons"]:
                    self.assertTrue(disabled)
                    self.assertEqual(extra, self.VIEWER_TEXT)
                self.assertTrue(got["viewerApproveButtons"] or name == "cp-ready")

    VIEWER_TEXT = "Zgody daje prowadzący na komputerze z sesją."

    def test_remote_viewer_cannot_approve(self) -> None:
        for name, modes in self.out["viewer"].items():
            d = self.fixtures[name]
            actions = d["approvals"]["actions"]
            for mode in ("true", "true-here"):
                with self.subTest(fixture=name, viewer=mode):
                    got = modes[mode]
                    self.assertNotIn("error", got, got.get("error"))
                    self.assertEqual(got["approveCalls"], 0)  # zdarzenie click wprost (także na nieaktywnym) nie woła approve
                    buttons = [b for a in got["acts"] for b in a["buttons"]] + got["bannerButtons"]
                    self.assertTrue(buttons)
                    for b in buttons:
                        self.assertTrue(b["disabled"], b)
                        self.assertFalse(b["approveAttr"], b)
                        if b["state"] in ("waiting", "running"):
                            self.assertEqual(b["extra"], self.VIEWER_TEXT)
                        else:
                            self.assertNotEqual(b["extra"], self.VIEWER_TEXT)
                    silence = d.get("silence")
                    self.assertEqual(got["bannerHidden"], not (d.get("waiting") or silence))
                    for w in d.get("waiting") or []:
                        self.assertIn(f": {w['what']}", got["banner"])
                    if silence:
                        self.assertIn("Brak raportu postępu", got["banner"])
            self.assertIn("tylko odczyt", modes["true"]["foot"])  # stopka bez zmian: viewer ma here=false
            for mode in ("false", "absent"):
                with self.subTest(fixture=name, viewer=mode):
                    got = modes[mode]
                    buttons = [b for a in got["acts"] for b in a["buttons"]] + got["bannerButtons"]
                    self.assertNotIn(self.VIEWER_TEXT, [b["extra"] for b in buttons])
                    waiting_kinds = {a["kind"] for a in actions if a["state"] == "waiting"}
                    self.assertEqual(got["approveCalls"], sum(1 for b in buttons if b["gate"] in waiting_kinds))
                    for b in buttons:
                        if b["state"] == "waiting":
                            self.assertTrue(b["approveAttr"] and not b["disabled"], b)
                        if b["state"] == "running":
                            self.assertEqual(b["extra"], next(a["reason"] for a in actions if a["kind"] == b["gate"]))
                    self.assertIn("odczyt + zgody", got["foot"])

    def test_data_is_inserted_as_text(self) -> None:
        got = self.out["xss"]
        self.assertNotIn("error", got)
        self.assertEqual(got["title"], "<img src=x onerror=alert(1)>")
        self.assertNotIn("img", got["tags"])
        self.assertNotIn("script", got["tags"])
        self.assertNotIn("b", got["tags"])
        self.assertIn("<script>alert(1)</script>", got["feedText"])
        self.assertIn("<b>x</b>", got["banner"])
        self.assertIn("S1", got["sidc"])

    def test_unknown_keys_do_not_break_rendering(self) -> None:
        got = self.out["unknown"]
        self.assertNotIn("error", got, got.get("error"))
        self.assertEqual(len(got["acts"]), 3)

    def test_documents_preview_and_host_open(self) -> None:
        docs, spec = self.out["docs"], self.fixtures["spec-act"]["spec"]["path"]
        self.assertEqual(docs["openDoc"], [spec])
        self.assertEqual(docs["view"], "progress")
        pv = docs["preview"]
        self.assertEqual((pv["view"], pv["docsHidden"]), ("docs", False))
        self.assertEqual(pv["loadDoc"], [spec])
        self.assertEqual(pv["mountHtml"], ["<p>x</p>"])
        self.assertEqual(pv["nav"], [f"docs:{spec}", f"docs:{spec}"])
        self.assertIn(spec, pv["bar"])

    def test_keys_theme_plan_menu_and_destroy(self) -> None:
        k = self.out["keys"]
        self.assertEqual((k["v1"], k["v2"]), ("docs", "progress"))
        self.assertEqual(k["theme"], 1)
        self.assertTrue(k["themeBtn"])
        self.assertTrue(k["noThemeBtn"])
        self.assertTrue(k["menuOpen"])
        self.assertTrue(k["menuAfter"])
        plans = self.fixtures["plan-in-progress"]["plans"]
        self.assertEqual(k["selectPlan"], [plans[-1]["path"]])
        self.assertEqual(k["storage"], [["plan", plans[-1]["path"]]])
        self.assertEqual(k["destroyed"], 0)
        self.assertTrue(self.out["nowRequired"])
        self.assertIn("create", self.out["exports"])
        self.assertIn("createDashboard", self.out["exports"])


if __name__ == "__main__":
    unittest.main()
