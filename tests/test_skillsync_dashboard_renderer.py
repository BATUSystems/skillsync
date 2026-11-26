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
// A real browser returns a NodeList from querySelectorAll: indexing, length, item(), forEach, keys/values/entries, iteration, and NOTHING of Array
// (no find/filter/map/some/every/reduce/includes/slice). `__asArray` is for the test code only; the renderer must never use it.
class NL {
  constructor(items) {
    items.forEach((x, i) => { this[i] = x; });
    Object.defineProperty(this, 'length', { value: items.length });
    Object.defineProperty(this, '__items', { value: items });
  }
  item(i) { return this.__items[i] || null; }
  forEach(f, t) { this.__items.forEach((x, i) => f.call(t, x, i, this)); }
  keys() { return this.__items.keys(); }
  values() { return this.__items.values(); }
  entries() { return this.__items.entries(); }
  [Symbol.iterator]() { return this.__items[Symbol.iterator](); }
  __asArray() { return this.__items.slice(); }
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
  querySelectorAll(sel) { const out = []; const walk = (n) => n.childNodes.forEach((c) => { if (c instanceof E) { if (matches(c, sel)) out.push(c); walk(c); } }); walk(this); return new NL(out); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() { return { left: 10, top: 10, right: 50, bottom: 30, width: 40, height: 20 }; }
  get offsetHeight() { return 20; }
  scrollIntoView() {}
  focus() { this.ownerDocument.focused = this; }
  get isConnected() { return Boolean(this.ownerDocument.root && this.ownerDocument.root.contains(this)); }
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
  doc.root = root;
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
  const buttons = (scope) => scope.querySelectorAll('button[data-gate]').__asArray().map((b) => ({
    gate: b.getAttribute('data-gate'), state: b.getAttribute('data-state'), disabled: b.disabled, text: text(b), cls: b.getAttribute('class'),
    extra: b.getAttribute('data-desc-extra'), approveAttr: b.hasAttribute('data-approve') }));
  const tags = new Set(); walk(m.root, (n) => { if (n instanceof E) tags.add(n.localName); });
  return {
    title: text(q('#title')),
    docTitle: m.calls.titles[m.calls.titles.length - 1] || null,
    acts: qa('#acts .act').__asArray().map((a) => ({ key: a.getAttribute('data-key'), cls: a.getAttribute('class'), chip: text(a.querySelector('.act-head .chip')),
      chipEnd: a.querySelector('.act-head .chip').classList.contains('chip-end'), buttons: buttons(a), docBtn: a.querySelector('[data-open-doc]') ? a.querySelector('[data-open-doc]').getAttribute('data-open-doc') : null })),
    pct: text(q('#acts .pct')),
    fills: qa('.fill').__asArray().map((f) => f.style.width),
    feed: qa('#feed li').length, feedText: text(q('#feed')), sidc: qa('#feed .sidc').__asArray().map(text),
    bannerHidden: q('#waitBanner').hidden, banner: text(q('#waitBanner')), bannerButtons: buttons(q('#waitBanner')),
    phases: qa('#phases .phase').length, steps: qa('#phases .step').length,
    docCount: text(q('#docCount')), docItems: qa('#docList .doc-item').length,
    planMenu: qa('#planMenu li').length, planBtn: text(q('#planBtnText')),
    meta: text(q('#meta')), foot: text(q('#foot')), sprite: qa('symbol').length,
    readiness: q('#acts .readiness') ? { cls: q('#acts .readiness').getAttribute('class'), chip: text(q('#acts .readiness-chip')),
      chipDesc: q('#acts .readiness-chip').getAttribute('data-desc'), facts: text(q('#acts .readiness-facts')),
      open: q('#acts .readiness [data-open-doc]') ? q('#acts .readiness [data-open-doc]').getAttribute('data-open-doc') : null,
      inAct: q('#acts .act[data-key="plan"] .readiness') !== null } : null,
    questions: q('#acts .questions') !== null || q('#acts .q-head') !== null || q('#acts .q-group') !== null,
    qtoast: qa('#waitBanner [data-questions-toast]').__asArray().map((n) => ({ text: text(n), cls: n.getAttribute('class'), role: n.getAttribute('role'),
      hrefs: n.querySelectorAll('a').__asArray().map((a) => a.getAttribute('href')), close: n.querySelectorAll('[data-notice-close]').length })),
    substeps: q('#acts details.substeps') ? { inAct: q('#acts .act[data-key="real"] details.substeps') !== null, summary: text(q('#acts .substeps summary')),
      summaryDesc: q('#acts .substeps summary').getAttribute('data-desc'), open: q('#acts details.substeps').hasAttribute('open'),
      items: qa('#acts .sub-list li').__asArray().map((li) => ({ id: text(li.querySelector('.q-id')), t: text(li.querySelector('.sub-t')), cls: li.getAttribute('class') })) } : null,
    timing: q('#acts details.timing-box') ? { inAct: q('#acts .act[data-key="real"] details.timing-box') !== null, summary: text(q('#acts .timing-box summary')),
      summaryDesc: q('#acts .timing-box summary').getAttribute('data-desc'), summaryTip: q('#acts .timing-box summary').getAttribute('data-desc-extra'),
      open: q('#acts details.timing-box').hasAttribute('open'),
      rows: qa('#acts .timing-list li').__asArray().map((li) => ({ id: text(li.querySelector('.q-id')), name: text(li.querySelector('.tt')), t: text(li.querySelector('.sub-t')), cls: li.getAttribute('class') })) } : null,
    copyButtons: qa('.wait-copy').length + qa('[data-copy-word]').length + qa('button').__asArray().filter((b) => /Kopiuj|Skopiowano/.test(text(b))).length,
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
  // Token zgód (approvals.tokenRequired, B1): bez tokenu w tej karcie (deps.hasApproveToken() === false) przyciski zgód są nieaktywne z jednym
  // powodem i nie wołają approve; z tokenem albo gdy serwer go nie wymaga zachowują się jak dotąd. Host bez deps.hasApproveToken (webview) nie jest „bez tokenu”.
  out.token = {};
  for (const name of ['cp-ready', 'plan-in-progress', 'questions', 'silence']) {
    const res = {};
    for (const mode of ['missing', 'present', 'notRequired', 'noHook']) {
      const j = JSON.parse(JSON.stringify(input.fixtures[name]));
      j.approvals.here = true;
      j.approvals.tokenRequired = mode !== 'notRequired';
      const deps = mode === 'missing' ? { hasApproveToken: () => false } : mode === 'present' ? { hasApproveToken: () => true } : {};
      const m = mount(j, { now: input.now[name], deps });
      res[mode] = summary(m);
      m.board.destroy();
      let calls = 0;
      for (const a of j.approvals.actions) {
        for (const scope of ['#acts', '#waitBanner']) {
          const c = mount(j, { now: input.now[name], deps });
          const btn = () => c.root.querySelector(`${scope} button[data-gate="${a.kind}"]`);
          if (btn()) { btn().dispatchEvent(new Ev('click')); if (btn()) btn().dispatchEvent(new Ev('click')); }
          await tick(); await tick();
          calls += c.calls.approve.length;
          c.board.destroy();
        }
      }
      res[mode].approveCalls = calls;
    }
    out.token[name] = res;
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
    const hrefs = (r) => r.querySelectorAll('a').__asArray().map((a) => [text(a), a.getAttribute('href')]);
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
  // Q-TOAST: pytania do eksperta to toast ze stanu wszystkich przebiegów (d.expertQuestions); w kafelku speca nie ma sekcji pytań.
  {
    const origin = { prototypeOrigin: (port) => `http://127.0.0.1:${port}` };
    const qa0 = (root, sel) => root.querySelectorAll(sel).__asArray().map((n) => text(n));
    const toast = (d, deps) => summary(mount(d, { now: NOW['questions'], deps: deps || {} })).qtoast;
    const j = clone(F['questions']);
    j.blockingQuestions.items[0].question = '<img src=x onerror=alert(1)>';
    j.assumptions.items[0].accepted = '<b>tak</b>';
    ex.qxss = summary(mount(j, { now: NOW['questions'] }));
    const v = clone(F['questions']); v.approvals.viewer = true; v.approvals.here = false;
    ex.qviewer = summary(mount(v, { now: NOW['questions'] }));
    ex.qtoast = { two: toast(F['questions'], origin), noOrigin: toast(F['questions']) };
    const one = clone(F['questions']); one.expertQuestions = { open: 2, blocking: 1, runs: [one.expertQuestions.runs[0]] };
    ex.qtoast.one = toast(one, origin);
    const hostile = clone(one); hostile.expertQuestions.runs[0].url = '/_experts/../x';
    ex.qtoast.hostileUrl = toast(hostile, origin);
    const badOrigin = toast(F['questions'], { prototypeOrigin: () => 'javascript:alert(2)' });
    ex.qtoast.badOrigin = badOrigin;
    const zero = clone(F['questions']); zero.expertQuestions = { open: 0, blocking: 0, runs: [] };
    ex.qtoast.zero = toast(zero, origin);
    const junk = clone(F['questions']); junk.expertQuestions = { open: '<b>3</b>', blocking: null, runs: 'x' };
    ex.qtoast.junk = toast(junk, origin);
    const old = clone(F['questions']); delete old.expertQuestions;
    ex.qtoast.absent = toast(old, origin);
    // bez timera i bez „×”; znika po update z open = 0 (stan, nie wynik akcji)
    const tm = { q: new Map(), n: 0, set(fn, ms) { const id = ++this.n; this.q.set(id, { fn, ms }); return id; }, clear(id) { this.q.delete(id); } };
    const m = mount(clone(F['questions']), { now: NOW['questions'], deps: { timers: tm, ...origin } });
    const timersAfterMount = tm.q.size;
    const before = qa0(m.root, '#waitBanner [data-questions-toast]');
    m.board.update(zero);
    ex.qtoast.lifecycle = { timersAfterMount, before, after: qa0(m.root, '#waitBanner [data-questions-toast]'), banner: text(m.root.querySelector('#waitBanner')) };
    // razem z toastem zgody oba są widoczne
    const cp = clone(F['cp-ready']); cp.approvals.here = true; cp.expertQuestions = clone(F['questions']).expertQuestions;
    const mc = mount(cp, { now: NOW['cp-ready'], deps: origin });
    ex.qtoast.withBanner = { toast: qa0(mc.root, '#waitBanner [data-questions-toast]'), items: mc.root.querySelectorAll('#waitBanner .wait-item').length };
  }
  // CORE-1150-ETA: szacowany koniec w kafelku aktywnego aktu i przy aktywnym kroku (nie w pasku), odliczanie w miejscu z `tick`; godzina z untilIso.
  {
    const e = F['eta'];
    const until = Date.parse(e.eta.untilIso);
    let t = until - 30 * 60000;
    const tm = fakeTimers();
    const m = mount(clone(e), { now: () => t, deps: { timers: tm } });
    const rd = (root) => ({
      act: root.querySelectorAll('#acts [data-eta-line="act"]').__asArray().map((n) => text(n)),
      step: root.querySelectorAll('#phases [data-eta-line="step"]').__asArray().map((n) => text(n)),
      count: root.querySelectorAll('[data-eta-countdown]').__asArray().map((n) => text(n)),
      bar: root.querySelectorAll('#waitBanner [data-eta-toast]').length });
    const before = rd(m.root), timersAfterMount = tm.q.size;
    m.board.tick();   // the first tick may redraw once (the count of open questions is taken); from then on the nodes stay
    const node0 = m.root.querySelector('#acts [data-eta-countdown]');
    const aria0 = node0 ? node0.getAttribute('aria-label') : null;
    const role = node0 ? node0.getAttribute('role') : null;
    t = until - 30 * 60000 + 2000; m.board.tick(); const node1 = m.root.querySelector('#acts [data-eta-countdown]');
    t = until - 60000; m.board.tick(); const node2 = m.root.querySelector('#acts [data-eta-countdown]'); const minute = rd(m.root);
    t = until + 5000; m.board.tick(); const over5 = rd(m.root);
    const sameNode = node0 === node1;
    const overCls = m.root.querySelector('#acts [data-eta-line="act"]').getAttribute('class');
    t = until + 17 * 60000 + 5000; m.board.tick(); const over17 = rd(m.root);
    t = until + 65 * 60000 + 5000; m.board.tick(); const over65 = rd(m.root); t = until - 30 * 60000;
    const upd = clone(e); upd.eta = Object.assign({}, e.eta, { operation: 'prototyp', until: '12:30', untilIso: '2026-01-11T12:30:00+00:00', untilOriginalIso: '2026-01-11T12:00:00+00:00', shiftSeconds: 1800 });
    m.board.update(upd); const updated = rd(m.root);
    const paused = clone(e); paused.eta = Object.assign({}, e.eta, { paused: true }); paused.waiting = [{ what: 'odpowiedź Q1', source: 'checkpoint', since: '11:20', action: 'x', link: null }];
    const pausedRd = rd(mount(paused, { now: () => until - 1000 }).root);
    const agentWait = clone(paused); agentWait.waiting = [{ what: 'CI', source: 'checkpoint', since: '11:20', action: 'x', link: null, who: { role: 'agent', login: null, label: 'agent' } }];
    const noWhat = rd(mount(agentWait, { now: () => until - 1000 }).root);
    const gone = clone(e); gone.eta = null; m.board.update(gone); const afterGone = rd(m.root);
    const noIso = clone(e); noIso.eta.untilIso = null; t = until + 99 * 60000;
    const noIsoRd = rd(mount(noIso, { now: () => t }).root);
    const noSteps = clone(e); noSteps.eta.steps = [];
    const noStepsRd = rd(mount(noSteps, { now: () => until - 1000 }).root);
    const junk = clone(e); junk.eta = { operation: '<b>x</b>', until: 5 };
    const hostile = clone(e); hostile.eta.operation = '<img src=x onerror=alert(1)>';
    const hm = mount(hostile, { now: () => until - 1000 });
    const old = clone(e); delete old.eta;
    const legacy = clone(e); legacy.eta = { operation: 'parsowanie fixtures', until: '11:40', untilIso: e.eta.untilIso, since: '11:10' };
    const legacyRd = rd(mount(legacy, { now: () => until - 1000 }).root);
    const shifted = clone(e); shifted.eta = Object.assign({}, e.eta, { shiftSeconds: 1800 });
    const shiftTip = mount(shifted, { now: () => until - 1000 }).root.querySelector('#acts [data-eta-line="act"]').getAttribute('data-tip');
    const dev = clone(e); dev.estimates = [{ steps: ['F2.1'], operation: 'a', state: 'late', adjustedErrorSeconds: 900, estimatedSeconds: 1200, actualSeconds: 2100, userWaitSeconds: 300 }];
    const devNode = mount(dev, { now: () => until - 1000 }).root.querySelector('#phases .step-dev');
    const devTip = devNode ? devNode.getAttribute('data-tip') : null;
    const noIsoNode = mount(noIso, { now: () => t }).root.querySelector('#acts [data-eta-countdown]');
    const noIsoRole = noIsoNode ? noIsoNode.getAttribute('role') : 'missing';
    const overAtMount = mount(clone(e), { now: () => until + 5000 }).root.querySelector('#acts [data-eta-line="act"]').getAttribute('class');
    const hm2 = (ms) => { const x = new Date(ms); return String(x.getHours()).padStart(2, '0') + ':' + String(x.getMinutes()).padStart(2, '0'); };
    ex.eta = { shiftTip, devTip, aria0, noIsoRole, overAtMount, clock: hm2(until), clock2: hm2(Date.parse('2026-01-11T12:30:00+00:00')), before, timersAfterMount, role, minute, over5, overCls, over17, over65, sameNode, updated, pausedRd, noWhat, afterGone, noIsoRd, noStepsRd, legacyRd,
      junk: rd(mount(junk, { now: () => t }).root), absent: rd(mount(old, { now: () => t }).root),
      hostile: rd(hm.root), hostileTags: hm.root.querySelectorAll('#acts img').length + hm.root.querySelectorAll('#acts b').length + hm.root.querySelectorAll('#waitBanner img').length };
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
    {  // 170-A: panel niewypchniętych commitów (delivery.commits / commitsMore)
      const mk = (commits, more) => {
        const j = clone(F['plan-in-progress']);
        j.git = { branch: 'feat/x', head: 'abcdef1234', headFull: 'abcdef1234', branchUrl: 'https://example.test/b', branchPushed: true, remote: { host: 'github', repo: 'o/r' } };
        j.delivery = { ...j.delivery, mode: 'pr', branch: 'feat/x', remote: 'origin/feat/x', ahead: 22, pushed: true };
        if (commits !== undefined) j.delivery.commits = commits;
        if (more !== undefined) j.delivery.commitsMore = more;
        return j;
      };
      const state = (m) => {
        const btn = m.root.querySelector('#commitsBtn'), panel = m.root.querySelector('#commitsPanel');
        return { tag: btn ? btn.tagName : null, expanded: btn ? btn.getAttribute('aria-expanded') : null, controls: btn ? btn.getAttribute('aria-controls') : null,
          hidden: panel ? panel.hidden : null, items: panel ? panel.querySelectorAll('li').__asArray().map((n) => text(n)) : [], more: panel && panel.querySelector('.commits-more') ? text(panel.querySelector('.commits-more')) : null,
          imgs: panel ? panel.querySelectorAll('img').length : 0, title: panel ? text(panel.querySelector('#commitsTitle')) : null,
          closeLabel: panel ? panel.querySelector('.commits-close').getAttribute('aria-label') : null };
      };
      const evil = '<img src=x onerror=alert(1)> password: [REDACTED]';
      const m = mount(mk([{ sha: 'abc1234', subject: evil }, { sha: 'def5678', subject: 'drugi' }], 20), { now: NOW['plan-in-progress'] });
      const key = (k) => m.board.handleKey({ key: k, target: m.root, ctrlKey: false, metaKey: false, altKey: false });
      const closed = state(m);
      m.root.querySelector('#commitsBtn').click();
      const opened = state(m);
      m.board.update(mk([{ sha: 'abc1234', subject: evil }, { sha: 'def5678', subject: 'drugi' }], 20));
      const keptOnRefresh = state(m);
      key('Escape');
      const afterEsc = state(m);
      m.root.querySelector('#commitsBtn').click();
      m.root.querySelector('#commitsPanel .commits-close').click();
      const afterClose = state(m);
      m.root.querySelector('#commitsBtn').click();
      m.root.querySelector('#commitsPanel').querySelector('li').click();   // klik w panelu nie zamyka
      const afterInside = state(m);
      m.root.dispatchEvent(new Ev('click'));                                   // klik poza panelem zamyka
      const afterOutside = state(m);
      // wypchnięcie opróżnia listę: stan otwarcia nie wraca przy następnych commitach
      m.root.querySelector('#commitsBtn').click();
      m.board.update(mk([], 0));
      m.board.update(mk([{ sha: 'abc1234', subject: 'nowy' }], 0));
      const afterEmpty = state(m);
      const none = mount(mk(undefined), { now: NOW['plan-in-progress'] });
      const empty = mount(mk([], 0), { now: NOW['plan-in-progress'] });
      stab.commits = { closed, opened, keptOnRefresh, afterEsc, afterClose, afterEmpty, afterInside, afterOutside,
        noField: { btn: Boolean(none.root.querySelector('#commitsBtn')), chip: text(none.root.querySelector('#meta .ahead-chip')) },
        emptyList: { btn: Boolean(empty.root.querySelector('#commitsBtn')), chip: text(empty.root.querySelector('#meta .ahead-chip')) } };
    }
    {  // 180-A: zakładka „Testy”, sekcje, pasek przy kroku i link
      const counts = (o) => ({ total: 0, passed: 0, failed: 0, skipped: 0, running: 0, waiting: 0, ...o });
      const attempt = (n, state, c, extra) => ({ n, state, startedAt: '2026-01-11T09:30:00Z', endedAt: state === 'running' ? null : '2026-01-11T09:31:00Z', durationMs: 60000,
        command: 'python3 -m unittest -v', tool: 'unittest', levels: 'tests', source: 'junit', exit: null, reason: null, counts: c, truncated: false, groups: [], ...(extra || {}) });
      const evil = '<img src=x onerror=alert(1)>';
      const tests = {
        sections: [
          { step: { id: 'F1.3', title: 'Test struktury' }, state: 'running', attempts: [attempt(1, 'running', counts({ total: 4, passed: 1, running: 1, waiting: 2 }), {
            groups: [{ name: 'mod.A', state: 'running', total: 4, counts: counts({ passed: 1, running: 1, waiting: 2 }), tests: [{ id: 'mod.A::t2', name: 't2', state: 'running' }] }] })] },
          { step: { id: 'F1.2', title: 'Fixtures' }, state: 'failed', attempts: [attempt(2, 'failed', counts({ total: 3, passed: 2, failed: 1 }), {
            live: 'completion', groups: [{ name: 'mod.B', state: 'failed', total: 3, counts: counts({ passed: 2, failed: 1 }), tests: [{ id: 'mod.B::bad', name: 'bad ' + evil, state: 'failed', ms: 7, msg: 'AssertionError: 1 != 2\n' + evil }] }] }),
            attempt(1, 'failed', counts({ total: 3, passed: 1, failed: 2 }))] },
          { step: { id: 'F1.1', title: 'Opis' }, state: 'passed', attempts: [attempt(1, 'passed', counts({ total: 5, passed: 5 }))] },
        ],
        failedTotal: 1, runningTotal: 1 };
      const mk = (withTests) => {
        const j = clone(F['plan-in-progress']);
        if (withTests) {
          j.tests = tests;
          for (const ph of j.progress.phases) for (const stp of ph.steps) { const sec = tests.sections.find((x) => x.step.id === stp.id); if (sec) stp.tests = { state: sec.state, attempt: sec.attempts[0].n, counts: sec.attempts[0].counts }; }
        }
        return j;
      };
      const state = (m) => ({ tabHidden: m.root.querySelector('#tab-tests').hidden, count: text(m.root.querySelector('#tCount')), countCls: m.root.querySelector('#tCount').getAttribute('class'),
        view: m.board.state().view, viewHidden: m.root.querySelector('#view-tests').hidden,
        sections: m.root.querySelectorAll('#testsView .tsec').__asArray().map((n) => ({ id: n.getAttribute('id'), head: text(n.querySelector('.tsec-head')), expanded: n.querySelector('.tsec-head').getAttribute('aria-expanded'), controls: n.querySelector('.tsec-head').getAttribute('aria-controls'), body: Boolean(n.querySelector('.tsec-body')) })) });
      const m = mount(mk(true), { now: NOW['plan-in-progress'] });
      const initial = state(m);
      m.root.querySelector('#tsec-btn-F1_2').click();
      const opened = state(m);
      const msg = m.root.querySelector('#testsView .t.is-failed .msg');
      const rows = { imgs: m.root.querySelectorAll('#testsView img').length, name: text(m.root.querySelector('#testsView .t.is-failed .nm')), msg: msg ? text(msg) : null, historyButtons: m.root.querySelectorAll('#testsView .tab-att').length, level: text(m.root.querySelector('#tsec-F1_2 .tlevel')) };
      m.root.querySelectorAll('#testsView .tab-att')[1].click();
      const prev = text(m.root.querySelector('#tsec-F1_2 .sub'));
      m.root.querySelector('#tsec-btn-F1_2').click();
      const closed = state(m);
      const bars = m.root.querySelectorAll('#phases .stbar').__asArray().map((n) => ({ text: text(n), label: n.querySelector('.tbar').getAttribute('aria-label') }));
      const link = m.root.querySelector('#phases button[data-test-step="F1.1"]');
      link.click();
      const afterLink = state(m);
      const nav = m.calls.nav.slice();
      m.root.querySelector('#tab-progress').click();
      const none = mount(mk(false), { now: NOW['plan-in-progress'] });
      stab.testsTab = { initial, opened, rows, prev, closed, bars, afterLink, nav, noTests: { tabHidden: none.root.querySelector('#tab-tests').hidden, stbar: none.root.querySelectorAll('#phases .stbar').length, tab: state(none).view } };
    }
    {  // F2 CORE-WORKDIR: chip katalogu roboczego w nagłówku (project.worktree)
      const wd = (wt, noField, ahead, noBranch) => {
        const j = clone(F['plan-in-progress']);
        j.git = { branch: 'feat/x', head: 'abcdef1234', headFull: 'abcdef1234', branchUrl: 'https://example.test/b', branchPushed: true, remote: { host: 'github', repo: 'o/r' } };
        j.delivery = { ...j.delivery, mode: 'pr', branch: 'feat/x', remote: 'origin/feat/x', ahead: ahead || 0, pushed: true };
        if (noBranch) j.git = { branch: null, head: null };
        j.project = { ...(j.project || {}) };
        if (noField) delete j.project.worktree; else j.project.worktree = wt;
        const m = mount(j, { now: NOW['plan-in-progress'] });
        const el = m.root.querySelector('#meta .workdir');
        const kids = m.root.querySelector('#meta').childNodes.map((c) => text(c) || '');
        const nameEl = el ? el.querySelector('.workdir-name') : null;
        return { found: Boolean(el), tag: el ? el.tagName : null, text: el ? text(el) : null, cls: el ? el.getAttribute('class') : null,
          desc: el ? el.getAttribute('data-desc') : null, extra: el ? el.getAttribute('data-desc-extra') : null, inner: Boolean(nameEl), innerText: nameEl ? text(nameEl) : null,
          parentCls: el ? el.parentNode.getAttribute('class') : null, icon: el ? Boolean(el.parentNode.querySelector('.i-folder, use')) : false,
          buttons: m.root.querySelectorAll('#meta .workdir button').length, count: m.root.querySelectorAll('#meta .workdir').length, order: kids };
      };
      const long = 'x'.repeat(200);
      stab.workdir = { main: wd({ kind: 'main', name: 'app', path: '/work/app', mainPath: null }),
        wt: wd({ kind: 'worktree', name: 'F1', path: '/work/app-wt/F1', mainPath: '/work/app' }),
        wtNoMain: wd({ kind: 'worktree', name: 'F1', path: '/work/app-wt/F1', mainPath: null }),
        nul: wd(null), missing: wd(undefined, true), unknown: wd({ kind: 'sparkly', name: 'z', path: '/z', mainPath: null }),
        ordered: wd({ kind: 'worktree', name: 'F1', path: '/work/app-wt/F1', mainPath: '/work/app' }, false, 3),
        noBranchMain: wd({ kind: 'main', name: 'app', path: '/work/app', mainPath: null }, false, 0, true),
        noBranchWt: wd({ kind: 'worktree', name: 'F1', path: '/work/app-wt/F1', mainPath: '/work/app' }, false, 0, true),
        noBranchNull: wd(null, false, 0, true),
        longName: wd({ kind: 'worktree', name: long, path: '/work/' + long, mainPath: null }) };
    }
    {  // CORE-1170-MODE: chip trybu dostarczania w nagłówku (`delivery.mode`, `base`, `source`)
      const md = (dv, noField) => {
        const j = clone(F['plan-in-progress']);
        j.git = { branch: 'feat/x', head: 'abcdef1234', headFull: 'abcdef1234', branchUrl: 'https://example.test/b', branchPushed: true, remote: { host: 'github', repo: 'o/r' } };
        j.project = { ...(j.project || {}), worktree: { kind: 'main', name: 'app', path: '/work/app', mainPath: null } };
        j.plan = { ...(j.plan || {}), pr: { number: 7, url: 'https://example.test/pr/7' } };
        if (noField) delete j.delivery; else j.delivery = dv;
        const m = mount(j, { now: NOW['plan-in-progress'] });
        const els = m.root.querySelectorAll('#meta .delivery-mode');
        const el = els.length ? els[0] : null;
        const kids = m.root.querySelector('#meta').childNodes.map((c) => (c.querySelector && c.querySelector('.delivery-mode') ? 'MODE' : /^PR #7/.test(text(c) || '') ? 'PR' : c.querySelector && c.querySelector('.git-link') ? 'BRANCH' : '?'));
        const res = { count: els.length, tag: el ? el.tagName : null, cls: el ? el.getAttribute('class') : null, text: el ? text(el) : null, delivery: el ? el.getAttribute('data-delivery') : null,
          tip: el ? el.getAttribute('data-tip') : null, imgs: m.root.querySelectorAll('#meta img').length, buttons: el ? el.querySelectorAll('button').length : 0, order: kids };
        m.board.destroy();
        return res;
      };
      const bad = '<img src=x onerror=alert(1)>';
      stab.mode = { pr: md({ mode: 'pr', base: 'main', source: 'manifest' }), prNoBase: md({ mode: 'pr', base: null, source: 'default' }),
        direct: md({ mode: 'direct', base: 'develop', source: 'manifest' }), emptyBase: md({ mode: 'pr', base: '', source: 'manifest' }),
        invalid: md({ mode: 'invalid', base: null, source: 'manifest' }), srcInvalid: md({ mode: null, base: null, source: 'invalid' }),
        srcInvalidMode: md({ mode: 'pr', base: 'main', source: 'invalid' }),
        noField: md(null, true), nul: md(null), notObject: md('direct'), numeric: md({ mode: 5, base: 'main', source: 'manifest' }), empty: md({ mode: '', base: 'main', source: 'manifest' }),
        noMode: md({ base: 'main', source: 'manifest' }), hostile: md({ mode: 'direct', base: bad, source: 'manifest' }) };
    }
    ex.stab = stab;
  }
  // WRITING-UX: pozycje „sesja pracuje” (znacznik zapisu) i „odpowiedź eksperta” w banerze czekania.
  {
    const origin = { prototypeOrigin: (port) => `http://127.0.0.1:${port}` };
    const items = (d, deps) => {
      const m = mount(d, { now: NOW['questions'], deps: deps || {} });
      return m.root.querySelectorAll('#waitBanner .wait-item').__asArray().map((n) => ({ text: text(n), cls: n.getAttribute('class'),
        hrefs: n.querySelectorAll('a').__asArray().map((a) => a.getAttribute('href')), expertLinks: n.querySelectorAll('[data-wait-expert]').length }));
    };
    const base = clone(F['questions']); base.waiting = []; base.expertQuestions = { open: 0, blocking: 0, runs: [] };
    const writing = clone(base);
    writing.prototype = Object.assign({}, writing.prototype, { slug: 'run-alpha', writing: { since: 1, expiresAt: 2, renewedAt: 1 } });
    writing.waiting = [{ what: 'agent pisze ekrany (do 14:25)', source: 'writing', since: '14:10', action: 'Agent pisze ekrany prototypu; akceptacja wróci, gdy skończy. Nic nie musisz robić.', link: { kind: 'prototype', slug: 'run-alpha' } }];
    const expert = clone(base);
    expert.waiting = [{ what: 'odpowiedź eksperta (1 blokujących)', source: 'expert', since: null, action: 'Odpowiedz na pytania eksperta na stronie pytań; sesja czeka na odpowiedź.', link: { kind: 'expert', slug: 'run-alpha', url: '/_experts/run-alpha' } }];
    const many = clone(expert); many.waiting[0].link = { kind: 'expert', slug: null, url: '/_experts' };
    const hostile = clone(expert); hostile.waiting[0].link.url = '/_experts/../x';
    const titleOf = (d) => { const m = mount(d, { now: NOW['questions'] }); return m.calls.titles[m.calls.titles.length - 1]; };
    const wtitle = { writing: titleOf(writing), expert: titleOf(expert) };
    ex.wux = { titles: wtitle, writing: items(writing, Object.assign({ prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/` }, origin)), expert: items(expert, origin), expertNoOrigin: items(expert), expertMany: items(many, origin),
      expertHostile: items(hostile, origin), expertBadOrigin: items(expert, { prototypeOrigin: () => 'javascript:alert(2)' }) };
  }
  // STATUS-160-A: stan etapu, pasek ekranów, „Czeka na”, dwa odcienie paska, parseWarnings, zgodność ze starym JSON-em.
  {
    const base = clone(F['plan-in-progress']); const nowP = NOW['plan-in-progress'];
    const stageOf = (m, key) => m.root.querySelectorAll('#acts .stage').__asArray().filter((n) => n.getAttribute('data-desc') === key)[0] || null;
    const tile = (n) => n ? { cls: n.getAttribute('class'), dot: n.querySelectorAll('.stage-dot').length, tip: n.getAttribute('data-desc-extra'), dataState: n.getAttribute('data-state'),
      screens: n.querySelector('.stage-screens') ? text(n.querySelector('.stage-screens')) : null, meter: n.querySelector('.stage-bar') ? { max: n.querySelector('.stage-bar').getAttribute('aria-valuemax'), now: n.querySelector('.stage-bar').getAttribute('aria-valuenow'), w: n.querySelector('.stage-bar .fill').style.width } : null,
      text: text(n) } : null;
    const withStages = (mut) => { const d = clone(base); mut(d); return d; };
    const nowMs = Number(typeof nowP === 'function' ? nowP() : nowP);
    const states = withStages((d) => { d.run = Object.assign({}, d.run, { lastReportAt: new Date(nowMs - 5 * 60000).toISOString() }); const st = d.acts[0].stages; st[0].state = 'done'; st[1].state = 'active'; st[1].since = '10:05'; st[2].state = 'planned'; st[2].done = false; });
    const ms = mount(states, { now: nowP });
    const k = states.acts[0].stages.map((x) => x.key);
    ex.s160 = { stages: k.map((key) => tile(stageOf(ms, key))) };
    // M2: an active stage stops pulsing when the run is silent (the data keeps `state: active`)
    const quietOf = (lastReportAt, silence) => { const d = clone(states); d.run = Object.assign({}, d.run, { lastReportAt }); if (silence === undefined) delete d.silence; else d.silence = silence;
      return tile(stageOf(mount(d, { now: nowP }), k[1])); };
    ex.s160.quiet = {
      old: quietOf(new Date(nowMs - 90 * 60000).toISOString()),
      fresh: quietOf(new Date(nowMs - 5 * 60000).toISOString()),
      silenceKey: quietOf(new Date(nowMs - 5 * 60000).toISOString(), { since: new Date(nowMs - 50 * 60000).toISOString(), thresholdMin: 30 }),
    };
    const proto = (writing, eta) => withStages((d) => {
      d.acts[0].stages[1] = { key: 'stage_prototype', label: 'Prototyp', done: false, tip: 'ekrany', state: 'active', since: '10:10' };
      d.prototype = Object.assign({}, d.prototype, { slug: 'x', writing });
      if (eta) d.eta = eta; else delete d.eta;
    });
    const protoTile = (d) => tile(stageOf(mount(d, { now: nowP }), 'stage_prototype'));
    ex.s160.screens = protoTile(proto({ since: 1, expiresAt: 2, renewedAt: 1, screensPlanned: 8, screensDone: 3 }, { operation: 'prototyp', until: '23:59', untilIso: '2099-01-01T23:59:00Z' }));
    ex.s160.screensNoPlan = protoTile(proto({ since: 1, expiresAt: 2, renewedAt: 1, screensDone: 3 }));
    ex.s160.screensNoWriting = protoTile(proto(null));
    // czeka na
    const waitText = (w, extra) => { const d = clone(base); d.waiting = [Object.assign({ what: 'odpowiedź Q3', source: 'checkpoint', since: '09:50', action: 'a', link: null }, w)]; const m = mount(d, { now: nowP });
      return text(m.root.querySelector('#waitBanner .wait-item strong')); };
    ex.s160.who = {
      label: waitText({ who: { role: 'acceptor', login: null, label: 'akceptujący' } }),
      login: waitText({ who: { role: 'expert', login: 'jan', label: 'ekspert' } }),
      none: waitText({}),
      writing: waitText({ source: 'writing', what: 'agent pisze ekrany (do 14:25)', who: { role: 'agent', login: null, label: 'agent' } }),
      hostile: waitText({ who: { role: 'x', login: '<b>z</b>', label: '<i>q</i>' } }),
    };
    // dwa odcienie
    const barOf = (d) => { const m = mount(d, { now: nowP }); const b = m.root.querySelector('#acts .act[data-key="real"] .bar'); const lg = m.root.querySelector('#acts [data-bar-legend]');
      return { cls: b.getAttribute('class'), valuetext: b.getAttribute('aria-valuetext'), fills: b.querySelectorAll('.fill, .fill-pending, .fill-active').__asArray().map((f) => [f.getAttribute('class'), f.style.width]),
        legend: lg ? text(lg) : null, pend: m.root.querySelector('#acts .pend') ? text(m.root.querySelector('#acts .pend')) : null }; };
    const two = withStages((d) => { d.progress.percent = 40; d.progress.pendingPercent = 10; d.progress.activePercent = 15; d.progress.pending = 1; });
    const noActive = withStages((d) => { d.progress.percent = 40; d.progress.pendingPercent = 10; delete d.progress.activePercent; });
    ex.s160.bars = { two: barOf(two), none: barOf(noActive) };
    // parseWarnings
    const warnOf = (list) => { const d = clone(base); if (list === undefined) delete d.run.parseWarnings; else d.run.parseWarnings = list; const m = mount(d, { now: nowP });
      const el = m.root.querySelector('#parseWarn'); const tags = []; walk(m.root, (n) => { if (n instanceof E) tags.push(n.localName); });
      return { hidden: el.hidden, text: text(el), tip: el.querySelector('[data-desc]') ? el.querySelector('[data-desc]').getAttribute('data-desc-extra') : null, lines: m.root.querySelectorAll('.parse-warn').length, img: tags.includes('img') }; };
    ex.s160.warn = {
      two: warnOf([{ time: '10:01', segment: 'w toku', fragment: 'bez id <img src=x>', reason: 'no-step-id' }, { time: '10:02', segment: 'zrobione', fragment: 'x F1.1', reason: 'later-id' }]),
      unbalanced: warnOf([{ time: '10:03', segment: 'raport', fragment: 'F1.1 (a; b, F1.2; ryzyko: brak', reason: 'unbalanced-bracket' }]),
      unknown: warnOf([{ time: '10:04', segment: 'raport', fragment: 'x', reason: 'reason-from-the-future' }]),
      none: warnOf(undefined), empty: warnOf([]),
    };
    // zgodność: fixture sprzed zmian (bez nowych kluczy) renderuje się bez wyjątku i bez nowych elementów
    ex.s160.legacy = {};
    for (const [name, json] of Object.entries(F)) {
      const d = clone(json);
      d.acts.forEach((a) => (a.stages || []).forEach((s) => { delete s.state; delete s.since; }));
      (d.waiting || []).forEach((w) => { delete w.who; });
      if (d.progress) delete d.progress.activePercent;
      if (d.run) delete d.run.parseWarnings;
      if (d.prototype && d.prototype.writing) { delete d.prototype.writing.screensPlanned; delete d.prototype.writing.screensDone; }
      try {
        const m = mount(d, { now: NOW[name] });
        ex.s160.legacy[name] = { dots: m.root.querySelectorAll('.stage-dot').length, active: m.root.querySelectorAll('.stage.is-active').length, screens: m.root.querySelectorAll('.stage-screens').length,
          legend: m.root.querySelectorAll('[data-bar-legend]').length, warn: m.root.querySelector('#parseWarn').hidden, light: m.root.querySelectorAll('.fill-active').length,
          sessionWait: /Czeka na:/.test(text(m.root.querySelector('#waitBanner'))), doneVsDone: m.root.querySelectorAll('.stage.is-done').length };
      } catch (e) { ex.s160.legacy[name] = { error: String(e && e.stack || e) }; }
    }
  }

  // STATUS-160-A faza 5: blok „Wynik”, legenda pojęć, adresat pytań, toast komentarzy po akceptacji, zakładka „Pytania”.
  {
    const base = clone(F['questions']); const nowQ = NOW['questions'];
    const s5 = {};
    const msTimers = () => ({ q: new Map(), n: 0, set(fn, ms) { const id = ++this.n; this.q.set(id, { fn, ms }); return id; }, clear(id) { this.q.delete(id); }, list() { return [...this.q.values()].map((x) => x.ms); } });
    // CORE-QTAB (assertions changed on purpose): `bare` also drops `blockingQuestions` and `assumptions`: the fixture `questions` has 2 blocking questions and 3 assumptions,
    // which show the tab now, so `bare` would no longer model "nothing to show"; `withSpecQuestions` puts them back where a case needs the realize group.
    const bare = () => { const d = clone(base); delete d.result; delete d.prototypes; delete d.expertQuestions.items; delete d.expertQuestions.current; // CORE-QFILTER K5: `bare` = JSON bez `current` (starszy serwer)
      delete d.blockingQuestions; delete d.assumptions;
      delete (d.prototype || {}).commentsAfterAcceptance;
      (d.expertQuestions.runs || []).forEach((r) => { delete r.addressees; }); return d; };
    const origin = { prototypeOrigin: (port) => `http://127.0.0.1:${port}`, prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/` };
    const mountQ = (d, deps) => mount(d, { now: nowQ, deps: Object.assign({}, deps || {}) });
    // (a) Wynik + legenda
    const longText = Array.from({ length: 15 }, (_, i) => (i % 2 ? `- punkt ${i} <b>x</b>` : `linia ${i}`)).join('\n');
    const resOf = (result, descs, deps) => { const d = bare(); if (result !== undefined) d.result = result; const m = mountQ(d, deps); if (descs) m.board.setDescriptions(descs);
      const box = m.root.querySelector('#resultBox'); const g = m.root.querySelector('.glossary');
      const tags = []; walk(m.root, (n) => { if (n instanceof E) tags.push(n.localName); });
      return { hidden: box.hidden, head: m.root.querySelector('[data-result-head]') ? m.root.querySelector('[data-result-head]').textContent : null,
        rest: m.root.querySelector('[data-result-rest]') ? m.root.querySelector('[data-result-rest]').textContent : null, details: m.root.querySelectorAll('#resultBox details').length,
        summary: m.root.querySelector('#resultBox summary') ? text(m.root.querySelector('#resultBox summary')) : null,
        path: m.root.querySelector('.result-path') ? text(m.root.querySelector('.result-path')) : null, inProgress: m.root.querySelector('#view-progress #resultBox') !== null,
        glossary: g ? { n: m.root.querySelectorAll('.glossary [data-glossary]').length, keys: m.root.querySelectorAll('.glossary [data-glossary]').__asArray().map((x) => x.getAttribute('data-glossary')),
          summary: text(g.querySelector('summary')), open: g.hasAttribute('open'), first: text(m.root.querySelector('.glossary [data-glossary]')) } : null, b: tags.includes('b') }; };
    s5.result = { long: resOf({ text: longText, path: 'docs/plans/x.md' }), short: resOf({ text: '- a\n- b', path: 'docs/plans/x.md' }), none: resOf(null), absent: resOf(undefined),
      described: resOf(null, { legend_act: { title: 'Akt (z JSON)', text: 'Tekst z opisów.' } }) };
    // Wynik sformatowany: serwer daje `html` i `moreHtml`, hosta `mountHtml` wstawia je; bez `html` zostaje <pre> z `text` (przypadek `long` wyżej).
    const richResult = { text: 'surowy **tekst**', path: 'docs/plans/x.md', html: '<h3>A</h3><p>b</p>', moreHtml: '<p>reszta</p>', moreLines: 7 };
    const richMount = mountQ(Object.assign(bare(), { result: richResult }));
    const richBox = richMount.root.querySelector('#resultBox');
    const noRestMount = mountQ(Object.assign(bare(), { result: { text: 'x', path: 'p', html: '<p>x</p>' } }));
    s5.resultRich = { mounted: richMount.calls.mountHtml, headTag: richBox.querySelector('[data-result-head]').localName, headClass: richBox.querySelector('[data-result-head]').getAttribute('class'),
      restClass: richBox.querySelector('[data-result-rest]').getAttribute('class'), summary: text(richBox.querySelector('summary')), details: richBox.querySelectorAll('details').length,
      noRestDetails: noRestMount.root.querySelectorAll('#resultBox details').length, noRestMounted: noRestMount.calls.mountHtml, clickable: true };
    // (b) adresat w toaście
    const toastOf = (runs, viewer) => { const d = bare(); d.expertQuestions.open = 2; d.expertQuestions.blocking = 1; d.expertQuestions.runs = runs; if (viewer) d.approvals = Object.assign({}, d.approvals, { viewer: true });
      const m = mountQ(d, origin); const t = m.root.querySelector('[data-questions-toast]'); return { text: text(t), addr: t.querySelector('[data-addressees]') ? text(t.querySelector('[data-addressees]')) : null }; };
    s5.toast = {
      all: toastOf([{ slug: 'run-a', open: 2, blocking: 1, url: '/_experts/run-a', addressees: 'all' }]),
      list: toastOf([{ slug: 'run-a', open: 1, blocking: 1, url: '/_experts/run-a', addressees: ['jan', 'ola'] }, { slug: 'run-b', open: 1, blocking: 0, url: '/_experts/run-b', addressees: ['ola', 'ewa'] }]),
      empty: toastOf([{ slug: 'run-a', open: 2, blocking: 1, url: '/_experts/run-a', addressees: [] }]),
      missing: toastOf([{ slug: 'run-a', open: 2, blocking: 1, url: '/_experts/run-a' }]),
      viewerLogins: toastOf([{ slug: 'run-a', open: 2, blocking: 1, url: '/_experts/run-a', addressees: ['jan'] }], true),
    };
    // (c) toast komentarzy po akceptacji
    const cmtOf = (caa, deps, slug) => { const d = bare(); d.prototype = Object.assign({}, d.prototype, { slug: slug === undefined ? 'run-alpha' : slug, port: 5130, up: true });
      if (caa !== undefined) d.prototype.commentsAfterAcceptance = caa; const tm = msTimers(); const m = mount(d, { now: nowQ, deps: Object.assign({ timers: tm }, deps || {}) });
      const t = m.root.querySelector('[data-comments-toast]'); const out = t ? { text: text(t), close: t.querySelectorAll('[data-notice-close], .wait-x').length, hrefs: t.querySelectorAll('a').__asArray().map((a) => [text(a), a.getAttribute('href')]) } : null;
      return { toast: out, timers: tm.list(), banner: m.root.querySelector('#waitBanner').hidden }; };
    s5.comments = {
      two: cmtOf({ count: 3, screens: [{ id: 's1', count: 2 }, { id: 's3', count: 1 }] }, origin),
      noHost: cmtOf({ count: 3, screens: [{ id: 's1', count: 3 }] }),
      zero: cmtOf({ count: 0, screens: [] }, origin), absent: cmtOf(undefined, origin), noSlug: cmtOf({ count: 1, screens: [{ id: 's1', count: 1 }] }, origin, null),
      hostile: cmtOf({ count: 1, screens: [{ id: '<i>s</i>', count: 1 }] }, origin),
    };
    // (d) zakładka „Pytania”
    const qd = (mut) => { const d = bare(); mut(d); return d; };
    const withSpecQuestions = (d) => { d.blockingQuestions = clone(base.blockingQuestions); d.assumptions = clone(base.assumptions); return d; };
    // CORE-QFILTER K3: zakładka czyta `expertQuestions.current`; dawne wejścia `prototypes`/`items` z testów wierszy jadą teraz w `current`.
    const setCur = (d, protos, items, open) => { d.expertQuestions.current = { open: open == null ? 2 : open, blocking: 1, prototypes: protos, items }; };
    const tabOf = (d, deps) => { const m = mountQ(d, deps || origin); const tab = m.root.querySelector('#tab-questions'); return { m, hidden: tab.hidden, panelHidden: m.root.querySelector('#view-questions').hidden, role: tab.getAttribute('role'), panelRole: m.root.querySelector('#view-questions').getAttribute('role'),
      controls: tab.getAttribute('aria-controls'), selected: tab.getAttribute('aria-selected'), count: text(m.root.querySelector('#qCount')) }; };
    const strip = (o) => { const { m, ...rest } = o; return rest; };
    s5.tab = { bare: strip(tabOf(bare())), withProtos: strip(tabOf(qd((d) => { d.prototypes = []; }))), withItems: strip(tabOf(qd((d) => { d.expertQuestions.items = []; }))),
      protosWrongType: strip(tabOf(qd((d) => { d.prototypes = 'x'; }))), itemsWrongType: strip(tabOf(qd((d) => { d.expertQuestions.items = {}; }))),
      // CORE-QTAB: the spec questions alone show the tab (the same wrong-typed project-wide fields, but the spec has 2 blocking questions)
      protosWrongTypeWithSpec: strip(tabOf(withSpecQuestions(qd((d) => { d.prototypes = 'x'; })))), itemsWrongTypeWithSpec: strip(tabOf(withSpecQuestions(qd((d) => { d.expertQuestions.items = {}; })))),
      withSpecQuestions: strip(tabOf(withSpecQuestions(bare()))),
      // CORE-QTAB: an older server without `blockingQuestions` and `assumptions` (the fields are absent, not empty): the expert group still draws, the realize group says there is nothing
      olderServerNoSpecFields: (() => { const d = clone(base); delete d.blockingQuestions; delete d.assumptions; const t = tabOf(d); const m = t.m;
        return { ...strip(t), realize: m.root.querySelectorAll('.q-realize').length, assume: m.root.querySelectorAll('.q-assume').length,
          realizeEmpty: text(m.root.querySelector('.q-group[data-group="realize"] .empty')), expertProtos: m.root.querySelectorAll('#qProtos .q-proto').length }; })(),
      olderServerNothingAtAll: (() => { const d = bare(); const t = tabOf(d); return { ...strip(t), groups: t.m.root.querySelectorAll('.q-group').length }; })() };
    const longQ = 'x'.repeat(200);
    const full = qd((d) => {
      d.expertQuestions.open = 2; d.approvals = Object.assign({}, d.approvals, { viewer: false });
      const protosF = [{ slug: 'run-alpha', iteration: 2, screens: 4, accepted: 3, stale: 1, threads: { open: 2, awaiting: 1, decided: 5 }, commentsAfterAcceptance: { count: 2, screens: [{ id: 's1', count: 2 }] }, writing: { since: 1, expiresAt: 2, renewedAt: 1, screensPlanned: 6, screensDone: 2 } },
        { slug: 'run-beta', iteration: null, screens: 0, accepted: 0, stale: 0, threads: {}, commentsAfterAcceptance: { count: 0, screens: [] }, writing: null }];
      const itemsF = [
        { id: 'qn-1', runSlug: 'run-alpha', text: longQ, blocking: true, addressees: ['jan', 'ola'], state: 'open', at: '2026-01-12T11:59:55Z', url: '/_experts/run-alpha' },
        { id: 'qn-2', runSlug: 'run-beta', text: '<b>krótkie</b>', blocking: false, addressees: [], state: 'answered', at: '2026-01-10T11:00:00Z', url: '/_experts/run-beta' },
        { id: 'qn-3', runSlug: 'run-beta', blocking: false, addressees: [], state: 'withdrawn', at: 'zły', url: '/_experts/../x' }];
      setCur(d, protosF, itemsF, 2);
    });
    const rows = (d, deps) => { const m = mountQ(d, deps || origin); const tags = []; walk(m.root, (n) => { if (n instanceof E) tags.push(n.localName); });
      return { protos: m.root.querySelectorAll('#qProtos .q-proto').__asArray().map((n) => ({ text: text(n), hrefs: n.querySelectorAll('a').__asArray().map((a) => a.getAttribute('href')) })),
        items: m.root.querySelectorAll('#qItems .q-item').__asArray().map((n) => ({ text: text(n), cls: n.getAttribute('class'), hrefs: n.querySelectorAll('a').__asArray().map((a) => a.getAttribute('href')), to: n.querySelector('[data-addressees]') ? text(n.querySelector('[data-addressees]')) : null, qtext: text(n.querySelector('.q-text')) })),
        empties: m.root.querySelectorAll('#view-questions .empty').__asArray().map((n) => text(n)), b: tags.includes('b') }; };
    s5.rows = { full: rows(full), noHost: rows(full, {}),
      viewer: rows(qd((d) => { d.approvals = Object.assign({}, d.approvals, { viewer: true });
        setCur(d, [{ slug: 'run-alpha', iteration: 1, screens: 2, accepted: 1, stale: 0, threads: { open: 1 } }],
          [{ id: 'qn-1', runSlug: 'run-alpha', blocking: true, addressees: [], state: 'open', at: '2026-01-12T11:00:00Z', url: '/_experts/run-alpha' }]); })),
      viewerLeak: rows(qd((d) => { d.approvals = Object.assign({}, d.approvals, { viewer: true });
        setCur(d, [], [{ id: 'qn-9', runSlug: 'r', text: 'SEKRET', blocking: false, addressees: ['jan'], state: 'open', at: '2026-01-12T11:00:00Z', url: '/_experts/r' }]); })),
      // assertions changed on purpose (CORE-QTAB): an empty `current` alone no longer shows the tab (both groups empty = hidden, K5); the realize group's content brings it back, the expert group says "nothing"
      empty: rows(withSpecQuestions(qd((d) => { setCur(d, [], [], 0); }))),
      emptyBoth: rows(qd((d) => { setCur(d, [], [], 0); })),
      wrongTypes: (() => { try { return rows(qd((d) => { setCur(d, [null, 7, 'x', { slug: 5, threads: 'a', writing: 3, commentsAfterAcceptance: 'z' }], [null, 3, { id: 1, state: 7, addressees: 5, text: 9, at: {} }]); })); } catch (e) { return { error: String(e && e.stack || e) }; } })(),
      itemsCap: (() => { const d = qd((d2) => { setCur(d2, [], Array.from({ length: 60 }, (_, i) => ({ id: 'q' + i, runSlug: 'r', text: 't', blocking: false, addressees: [], state: 'open', at: '2026-01-12T11:00:00Z', url: '/_experts/r' }))); }); return rows(d).items.length; })() };
    // CORE-QFILTER F2 (K3, K4, K5): zakładka tylko bieżącego przebiegu; toast projektowy bez zmian; `current: null` a brak klucza.
    {
      const pr = (slug) => ({ slug, iteration: 1, screens: 2, accepted: 1, stale: 0, threads: { open: 1 } });
      const it = (id, runSlug, state) => ({ id, runSlug, text: 'tekst ' + id, blocking: false, addressees: [], state: state || 'open', at: '2026-01-12T11:00:00Z', url: '/_experts/' + runSlug });
      const projectWide = (d) => { d.prototypes = [pr('run-alpha'), pr('run-beta')]; d.expertQuestions.items = [it('qn-a', 'run-alpha'), it('qn-b', 'run-beta'), it('qn-g', 'run-gamma')]; };
      const view = (d) => { const m = mountQ(d); const tab = m.root.querySelector('#tab-questions');
        return { hidden: tab.hidden, panelHidden: m.root.querySelector('#view-questions').hidden, count: text(m.root.querySelector('#qCount')),
          protos: m.root.querySelectorAll('#qProtos .q-proto').__asArray().map((n) => n.getAttribute('data-proto')), items: m.root.querySelectorAll('#qItems .q-item').__asArray().map((n) => n.getAttribute('data-question')),
          toast: (m.root.querySelector('[data-questions-toast]') || { textContent: null }).textContent }; };
      const withCur = (cur) => { const d = bare(); projectWide(d); d.expertQuestions.open = 3; d.expertQuestions.blocking = 2; d.expertQuestions.current = cur; return d; };
      const noKey = (mut) => { const d = bare(); projectWide(d); if (mut) mut(d); return d; };
      s5.qfilter = {
        current: view(withCur({ open: 1, blocking: 0, prototypes: [pr('run-alpha')], items: [it('qn-a', 'run-alpha')] })),
        currentZero: view(withCur({ open: 0, blocking: 0, prototypes: [pr('run-alpha')], items: [it('qn-a', 'run-alpha', 'answered')] })),
        currentNull: view(withCur(null)),
        currentNullToNav: (() => { const m = mountQ(withCur({ open: 1, blocking: 0, prototypes: [pr('run-alpha')], items: [] })); m.root.querySelector('#tab-questions').click(); const before = m.board.state().view;
          m.board.update(withCur(null)); return { before, after: m.board.state().view, hidden: m.root.querySelector('#tab-questions').hidden }; })(),
        currentBadType: Object.fromEntries([['array', []], ['number', 7], ['string', 'x']].map(([k, v]) => [k, view(withCur(v))])),
        currentOpenString: view(withCur({ open: '3', blocking: 0, prototypes: [pr('run-alpha')], items: [] })),
        // integracja serwer -> renderer: fixtura `questions` z `current` wyliczonym przez serwer, bez żadnych poprawek
        server: (() => { const m = mountQ(clone(F['questions'])); return { hidden: m.root.querySelector('#tab-questions').hidden, count: text(m.root.querySelector('#qCount')),
          protos: m.root.querySelectorAll('#qProtos .q-proto').__asArray().map((n) => n.getAttribute('data-proto')), items: m.root.querySelectorAll('#qItems .q-item').__asArray().map((n) => n.getAttribute('data-question')) }; })(),
        fallback: view(noKey((d) => { d.expertQuestions.runs = [{ slug: 'run-alpha', open: 2, blocking: 1, url: '/_experts/run-alpha' }, { slug: 'run-beta', open: 1, blocking: 1, url: '/_experts/run-beta' }]; })),
        fallbackNoRun: view(noKey((d) => { d.expertQuestions.runs = [{ slug: 'run-beta', open: 1, blocking: 1, url: '/_experts/run-beta' }]; })),
        fallbackNoSlug: view(noKey((d) => { d.prototype = Object.assign({}, d.prototype, { slug: null }); })),
        fallbackNoPrototype: view(noKey((d) => { delete d.prototype; })),
        // CORE-QTAB (assertions changed on purpose): the same inputs with the spec's 2 blocking questions keep the tab (realize group), the expert group stays empty
        currentNullWithSpec: view(withSpecQuestions(withCur(null))),
        currentBadTypeWithSpec: Object.fromEntries([['array', []], ['number', 7], ['string', 'x']].map(([k, v]) => [k, view(withSpecQuestions(withCur(v)))])),
        fallbackNoSlugWithSpec: view(withSpecQuestions(noKey((d) => { d.prototype = Object.assign({}, d.prototype, { slug: null }); }))),
        fallbackNoPrototypeWithSpec: view(withSpecQuestions(noKey((d) => { delete d.prototype; }))),
      };
    }
    // przełączanie i strzałki
    { const d = clone(full); const m = mountQ(d); const tabs = () => ['progress', 'docs', 'questions'].map((v) => m.root.querySelector('#tab-' + v).getAttribute('aria-selected'));
      const panels = () => ['progress', 'docs', 'questions'].map((v) => m.root.querySelector('#view-' + v).hidden);
      const before = { tabs: tabs(), panels: panels() }; m.root.querySelector('#tab-questions').click(); const afterClick = { tabs: tabs(), panels: panels(), nav: m.calls.nav.slice() };
      const key = (k) => { const e = new Ev('keydown', { key: k }); m.root.querySelector('#tab-questions').dispatchEvent(e); return e.defaultPrevented; };
      const prevented = key('ArrowRight'); const wrapped = tabs(); key('ArrowLeft'); const back = tabs(); key('Home'); const home = tabs(); key('End'); const end = tabs();
      m.board.update(bare()); const gone = { view: m.board.state().view, tabs: tabs(), tabHidden: m.root.querySelector('#tab-questions').hidden };
      s5.nav = { before, afterClick, prevented, wrapped, back, home, end, gone }; }
    // (e) zgodność: fixtures bez nowych kluczy
    s5.legacy = {}; s5.legacySpec = {};
    for (const [name, json] of Object.entries(F)) {
      const d = clone(json); delete d.result; delete d.prototypes; if (d.prototype) delete d.prototype.commentsAfterAcceptance;
      if (d.expertQuestions) { delete d.expertQuestions.items; delete d.expertQuestions.current; (d.expertQuestions.runs || []).forEach((r) => { delete r.addressees; }); }
      // CORE-QTAB (assertions changed on purpose): a legacy JSON has no `blockingQuestions` and `assumptions` either; `legacySpec` below keeps them
      const withSpec = clone(d);
      delete d.blockingQuestions; delete d.assumptions;
      try { const m = mount(withSpec, { now: NOW[name] });
        s5.legacySpec[name] = { tabHidden: m.root.querySelector('#tab-questions').hidden, realize: m.root.querySelectorAll('.q-realize').length, assume: m.root.querySelectorAll('.q-assume').length,
          count: text(m.root.querySelector('#qCount')), groups: m.root.querySelectorAll('.q-group').length }; m.board.destroy(); }
      catch (e) { s5.legacySpec[name] = { error: String(e && e.stack || e) }; }
      try { const m = mount(d, { now: NOW[name] }); const tab = m.root.querySelector('#tab-questions');
        s5.legacy[name] = { tabHidden: tab.hidden, panelHidden: m.root.querySelector('#view-questions').hidden, resultHidden: m.root.querySelector('#resultBox').hidden, comments: m.root.querySelectorAll('[data-comments-toast]').length,
          addr: m.root.querySelectorAll('[data-addressees]').length, glossary: m.root.querySelectorAll('.glossary [data-glossary]').length, rows: m.root.querySelectorAll('.q-row').length };
      } catch (e) { s5.legacy[name] = { error: String(e && e.stack || e) }; }
    }
    ex.s160b = s5;
  }
  // 170-B: pływający przycisk „na górę” w widoku dokumentu (haczyk deps.scroll.toTop; próg z histerezą; fokus; brak haczyka = brak przycisku).
  {
    const name = Object.keys(F)[0];
    const hook = () => ({ cbs: [], goes: 0, unsub: 0, subscribe(cb) { this.cbs.push(cb); return () => { this.unsub++; }; }, go() { this.goes++; },
      emit(m) { this.cbs.forEach((c) => c(m)); } });
    const ex170 = {};
    const open = async (extra) => {
      const hk = hook(); const m = mount(clone(F[name]), { now: NOW[name], preview: true, deps: { scroll: { save: () => 0, restore: () => {}, reveal: () => {}, toTop: hk } }, ...(extra || {}) });
      m.board.setDoc('docs/plans/demo.md'); m.board.setView('docs', false); await tick(); await tick();
      return { hk, m, btn: () => m.root.querySelector('#toTop') };
    };
    {
      const { hk, m, btn } = await open();
      const b = btn(); const shape = { type: b.getAttribute('type'), label: b.getAttribute('aria-label'), cls: b.getAttribute('class'), tip: b.getAttribute('data-tip'), icon: b.querySelectorAll('use').length };
      const trace = []; const vis = () => trace.push(!btn().hidden);
      vis();                                     // y = 0 (brak metryk): ukryty
      hk.emit({ y: 0, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 800, viewport: 1000, height: 5000 }); vis();   // > 0,75: pokazany
      hk.emit({ y: 600, viewport: 1000, height: 5000 }); vis();   // między 0,5 a 0,75: histereza, zostaje
      hk.emit({ y: 400, viewport: 1000, height: 5000 }); vis();   // < 0,5: schowany
      hk.emit({ y: 600, viewport: 1000, height: 5000 }); vis();   // w górę przez histerezę, nadal schowany
      hk.emit({ y: 800, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 0, viewport: 1000, height: 5000 }); vis();
      ex170.threshold = { shape, trace, rootChild: btn().parentNode === m.root };
    }
    {
      const { hk, m, btn } = await open();
      hk.emit({ y: 2000, viewport: 1000, height: 6000 });
      const shownDocs = !btn().hidden;
      m.board.setView('progress', false); const shownProgress = !btn().hidden;
      m.board.setView('questions', false); const shownQuestions = !btn().hidden;
      m.board.setView('docs', false); await tick(); await tick(); const backInDocs = !btn().hidden;
      ex170.views = { shownDocs, shownProgress, shownQuestions, backInDocs };
    }
    {
      const { hk, m, btn } = await open({ actions: { loadDoc: () => Promise.reject(new Error('HTTP 500')) } });
      hk.emit({ y: 2000, viewport: 1000, height: 6000 }); await tick();
      ex170.failedLoad = { hidden: btn().hidden };
    }
    {
      const { hk, m, btn } = await open();
      hk.emit({ y: 2000, viewport: 1000, height: 6000 });
      const focused = []; const tab = m.root.querySelector('#tab-docs'); tab.focus = (o) => focused.push(o);
      const before = !btn().hidden; btn().click();
      ex170.click = { before, goes: hk.goes, focused, shownRightAfter: !btn().hidden, tabId: tab.id };
      hk.emit({ y: 900, viewport: 1000 });   // płynne przewijanie trwa: nadal powyżej progu ukrycia, przycisk nie mruga
      ex170.click.shownDuringScroll = !btn().hidden;
      hk.emit({ y: 400, viewport: 1000 });   // zeszło poniżej 0,5: chowa się sam
      ex170.click.hiddenAtTheTop = btn().hidden;
      m.board.destroy(); ex170.click.unsub = hk.unsub; ex170.click.rootClassGone = !m.root.classList.contains('has-to-top');
    }
    {
      const { hk, m, btn } = await open();
      const edges = [];
      const at = (y, viewport) => { hk.emit({ y, viewport: viewport === undefined ? 1000 : viewport }); edges.push(!btn().hidden); };
      at(750);    // dokładnie 0,75: próg jest ostry (>), nadal ukryty
      at(751);    // powyżej: pokazany
      at(500);    // dokładnie 0,5: histereza (<), nadal pokazany
      at(499);    // poniżej: schowany
      at(2000, 0);  // okno bez wysokości: nigdy
      ex170.edges = { edges, hasClass: m.root.classList.contains('has-to-top') };
    }
    {
      const { hk, m, btn } = await open();
      hk.emit({ y: 2000, viewport: 1000 });
      const focused = []; const tab = m.root.querySelector('#tab-docs'); tab.focus = (o) => focused.push(o);
      btn().dispatchEvent(new Ev('focus'));   // fokus klawiaturą na przycisku
      hk.emit({ y: 0, viewport: 1000 });      // użytkownik przewinął na górę bez klikania: przycisk się chowa
      const hiddenNow = btn().hidden; const movedOnHide = focused.length;
      hk.emit({ y: 2000, viewport: 1000 }); btn().dispatchEvent(new Ev('focus')); btn().dispatchEvent(new Ev('blur'));   // fokus odszedł sam
      hk.emit({ y: 0, viewport: 1000 });
      ex170.focusOnHide = { hiddenNow, movedOnHide, afterBlur: focused.length, option: focused[0] };
    }
    {
      const m = mount(clone(F[name]), { now: NOW[name], preview: true });
      const m2 = mount(clone(F[name]), { now: NOW[name], preview: true, deps: { scroll: { save: () => 0, restore: () => {}, reveal: () => {} } } });
      const m3 = mount(clone(F[name]), { now: NOW[name], preview: true, deps: { scroll: { save: () => 0, restore: () => {}, reveal: () => {}, toTop: { subscribe: 'nie funkcja', go() {} } } } });
      ex170.noHook = { noClass: !m.root.classList.contains('has-to-top'), none: m.root.querySelector('#toTop') === null, oldAdapter: m2.root.querySelector('#toTop') === null, broken: m3.root.querySelector('#toTop') === null };
    }
    ex.s170 = ex170;
  }
  // CORE-TAP F3 (K8): stan „polecenie zawiodło” w nagłówku sekcji testów (exit ≠ 0 przy failed = 0); K10: przycisk „na górę” w widoku Testy.
  {
    const exT = {};
    const cnt = (o) => ({ total: 0, passed: 0, failed: 0, skipped: 0, running: 0, waiting: 0, ...o });
    const att = (n, state, c, extra) => ({ n, state, startedAt: '2026-01-11T09:30:00Z', endedAt: state === 'running' ? null : '2026-01-11T09:31:00Z', durationMs: 60000,
      command: 'node --test', tool: 'tap', levels: 'tests', source: null, exit: null, reason: null, counts: c, truncated: false, groups: [], ...(extra || {}) });
    const sec = (id, state, attempts) => ({ step: { id, title: 'Krok ' + id }, state, attempts });
    const build = (sections, failedTotal, base) => { const j = clone(F[base || 'plan-in-progress']); j.tests = { sections, failedTotal, runningTotal: 0 }; return j; };
    const MSG_A = 'żaden test nie zgłosił porażki';
    const headOf = (m, id) => { const n = m.root.querySelector('#tsec-' + id + ' .tsec-head'); return n; };
    const report = (m, id) => {
      const head = headOf(m, id), t = text(head);
      const marks = head.querySelectorAll('*').__asArray().filter((n) => text(n).includes(MSG_A) && !n.querySelectorAll('*').__asArray().some((k) => text(k).includes(MSG_A)));
      return { head: t, expanded: head.getAttribute('aria-expanded'), markCls: marks.length ? marks[0].getAttribute('class') : null, inHead: t.includes(MSG_A) };
    };
    {
      const sections = [
        sec('CMDF', 'failed', [att(1, 'failed', cnt({ total: 2, passed: 2 }), { exit: 1 })]),                                   // tap: wyjątek poza testami
        sec('PYT', 'failed', [att(1, 'failed', cnt({ total: 3, passed: 3 }), { exit: 2, tool: 'pytest', levels: 'counts', command: 'pytest -q' })]),   // błąd zbierania
        sec('TMO', 'failed', [att(1, 'failed', cnt({ total: 1, passed: 1 }), { exit: 124, tool: 'unittest' })]),               // timeout z kodem
        sec('GRN', 'passed', [att(1, 'passed', cnt({ total: 2, passed: 2 }), { exit: 0 })]),
        sec('RED', 'failed', [att(1, 'failed', cnt({ total: 3, passed: 2, failed: 1 }), { exit: 1 })]),
        sec('NOEX', 'interrupted', [att(1, 'interrupted', cnt({ total: 2, passed: 2 }), { exit: null })]),
        sec('RUN', 'running', [att(1, 'running', cnt({ total: 3, passed: 1, running: 1, waiting: 1 }), { exit: null })]),
        sec('HIST', 'failed', [att(2, 'failed', cnt({ total: 2, passed: 2 }), { exit: 3 }), att(1, 'passed', cnt({ total: 2, passed: 2 }), { exit: 0 })]),
        sec('LVC', 'failed', [att(1, 'failed', cnt({ total: 0 }), { exit: 1, levels: 'command', tool: 'make', command: 'make test' })]),   // poziom „polecenie”: bez wyników testów
        sec('NOC', 'failed', [att(1, 'failed', null, { exit: 5, levels: 'counts' })]),                                          // brak counts: failed = 0
        sec('STR', 'failed', [att(1, 'failed', cnt({ total: 2, passed: 2 }), { exit: '1' })]),                                 // exit jako napis: nie liczba
      ];
      const m = mount(build(sections, 1), { now: NOW['plan-in-progress'] });
      const out = {};
      for (const id of ['CMDF', 'PYT', 'TMO', 'GRN', 'RED', 'NOEX', 'RUN', 'HIST', 'LVC', 'NOC', 'STR']) out[id] = report(m, id);
      const FULL = 'wyniki ok dotyczą tylko testów, które zdążyły się wykonać';
      const bodyNote = (id) => { const n = m.root.querySelector('#tsec-body-' + id + ' .cmd-failed-note'); return n ? text(n) : null; };
      const bodyOf = (id) => m.root.querySelector('#tsec-body-' + id) !== null;
      const bodies = {};
      for (const id of ['CMDF', 'GRN', 'RED', 'LVC', 'STR']) bodies[id] = { before: bodyOf(id), note: bodyNote(id) };   // zwinięte: ani ciała, ani linii
      for (const id of ['CMDF', 'GRN', 'RED', 'LVC', 'STR', 'NOC']) m.root.querySelector('#tsec-btn-' + id).click();
      for (const id of ['CMDF', 'GRN', 'RED', 'LVC', 'STR', 'NOC']) bodies[id] = { ...(bodies[id] || {}), open: bodyOf(id), note: bodyNote(id), full: (bodyNote(id) || '').includes(FULL), beforeNote: bodies[id] ? bodies[id].note : null };
      out.bodies = bodies;
      m.root.querySelector('#tsec-btn-HIST').click();                       // otwarcie sekcji: lista prób
      m.root.querySelectorAll('#testsView .tab-att')[1].click();             // starsza, zielona próba
      out.HISTold = report(m, 'HIST');
      m.root.querySelectorAll('#testsView .tab-att')[0].click();
      out.HISTnew = report(m, 'HIST');
      exT.states = out;
      const only = mount(build([sections[0]], 0), { now: NOW['plan-in-progress'] });
      exT.tab = { count: text(only.root.querySelector('#tCount')), cls: only.root.querySelector('#tCount').getAttribute('class') };
    }
    const hook = () => ({ cbs: [], goes: 0, subscribe(cb) { this.cbs.push(cb); return () => {}; }, go() { this.goes++; }, emit(m) { this.cbs.forEach((c) => c(m)); } });
    const openTests = (base, noHook) => {
      const hk = hook(); const scroll = { save: () => 0, restore: () => {}, reveal: () => {}, ...(noHook ? {} : { toTop: hk }) };
      const m = mount(build([sec('S1', 'passed', [att(1, 'passed', cnt({ total: 1, passed: 1 }))])], 0, base), { now: NOW[base || 'plan-in-progress'], preview: true, deps: { scroll } });
      m.board.setView('tests', false);
      const focused = []; for (const v of ['progress', 'tests', 'docs', 'questions']) { const t = m.root.querySelector('#tab-' + v); t.focus = (o) => focused.push([v, o]); }
      return { hk, m, focused, btn: () => m.root.querySelector('#toTop') };
    };
    {
      const { hk, m, btn } = openTests();
      const trace = []; const vis = () => trace.push(!btn().hidden);
      vis();
      hk.emit({ y: 0, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 800, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 600, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 400, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 600, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 800, viewport: 1000, height: 5000 }); vis();
      hk.emit({ y: 2000, viewport: 0 }); vis();
      exT.threshold = { trace, view: m.board.state().view, hasClass: m.root.classList.contains('has-to-top'), padHit: m.root.querySelector('#view-tests .tests-view') !== null };
    }
    {
      const { hk, m, btn, focused } = openTests();
      hk.emit({ y: 2000, viewport: 1000 });
      btn().dispatchEvent(new Ev('focus'));
      hk.emit({ y: 0, viewport: 1000 });
      exT.focusOnHide = { hidden: btn().hidden, focused: focused.slice() };
    }
    {
      const { hk, m, btn, focused } = openTests();
      hk.emit({ y: 2000, viewport: 1000 });
      btn().click();
      exT.click = { goes: hk.goes, focused: focused.slice(), still: !btn().hidden };
    }
    for (const [key, view, base] of [['toProgress', 'progress'], ['toQuestions', 'questions', 'questions']]) {
      const { hk, m, btn, focused } = openTests(base);
      hk.emit({ y: 2000, viewport: 1000 });
      const before = !btn().hidden; btn().dispatchEvent(new Ev('focus'));
      m.board.setView(view, false);
      exT[key] = { before, view: m.board.state().view, hidden: btn().hidden, focused: focused.slice() };
    }
    {
      const { hk, m, btn, focused } = openTests();
      hk.emit({ y: 2000, viewport: 1000 });
      btn().dispatchEvent(new Ev('focus'));
      const seen = [!btn().hidden];
      m.board.setDoc('docs/plans/demo.md'); m.board.setView('docs', false); seen.push(!btn().hidden);
      const atSwitch = focused.slice();
      await tick(); await tick(); seen.push(!btn().hidden);
      exT.toDocs = { seen, atSwitch, view: m.board.state().view, buttons: m.root.querySelectorAll('#toTop').length };
    }
    {
      const a = openTests(undefined, true);
      a.hk.emit({ y: 2000, viewport: 1000 });
      exT.noHook = { none: a.btn() === null, noClass: !a.m.root.classList.contains('has-to-top') };
    }
    ex.coreTap = exT;
  }
  // CORE-TESTDEBT F2 (K5): the note about skipped result files in the tests tab (`tests.skipped`).
  {
    const exD = {};
    const okSec = { step: { id: 'S1', title: 'Krok S1' }, state: 'passed', attempts: [{ n: 1, state: 'passed', startedAt: '2026-01-11T09:30:00Z', endedAt: '2026-01-11T09:31:00Z', durationMs: 60000,
      command: 'node --test', tool: 'tap', levels: 'tests', source: null, exit: 0, reason: null, counts: { total: 1, passed: 1, failed: 0, skipped: 0, running: 0, waiting: 0 }, truncated: false, groups: [] }] };
    const mkT = (sections, skipped, withField) => { const j = clone(F['plan-in-progress']); j.tests = { sections, failedTotal: 0, runningTotal: 0 }; if (withField) j.tests.skipped = skipped; return j; };
    const view = (j) => {
      const m = mount(j, { now: NOW['plan-in-progress'] });
      const note = m.root.querySelector('#testsView .tskipped');
      const r = { tabHidden: m.root.querySelector('#tab-tests').hidden, note: note ? text(note) : null, role: note ? note.getAttribute('role') : null, notes: m.root.querySelectorAll('#testsView .tskipped').length,
        legend: m.root.querySelectorAll('#testsView .tlegend').length, secs: m.root.querySelectorAll('#testsView .tsec').length, empty: m.root.querySelectorAll('#testsView .empty').length, imgs: m.root.querySelectorAll('#testsView img').length };
      m.board.destroy(); return r;
    };
    const P = (path, reason) => ({ path, reason });
    exD.three = view(mkT([okSec], [P('a/one', 'not a step directory'), P('b/two', 'unreadable'), P('c/three', 'weird <b>reason</b>')], true));
    exD.one = view(mkT([okSec], [P('only/one', 'unreadable')], true));
    exD.six = view(mkT([okSec], ['p1', 'p2', 'p3', 'p4', 'p5', 'p6'].map((p) => P(p, 'unreadable')), true));
    exD.twenty = view(mkT([okSec], Array.from({ length: 20 }, (_, i) => P('d' + i, 'unreadable')), true));
    exD.emptySections = view(mkT([], [P('tests', 'unreadable')], true));
    exD.mixed = view(mkT([okSec], [null, 'str', P('m/ok', 'unreadable'), P('m/nr', null), P('m/obj', { a: 1 }), 7], true));
    exD.noField = view(mkT([okSec], null, false));
    exD.emptyList = view(mkT([okSec], [], true));
    exD.emptyBoth = view(mkT([], [], true));
    ex.testDebt = exD;
  }
  // CORE-TESTDEBT2 (a, K1): the note about the groups of an attempt cut by the server (`groupsOmitted`).
  {
    const mkA = (extra) => ({ n: 1, state: 'passed', startedAt: '2026-01-11T09:30:00Z', endedAt: '2026-01-11T09:31:00Z', durationMs: 60000, command: 'x', tool: 'tap', levels: 'tests', source: null, exit: 0, reason: null,
      counts: { total: 2000, passed: 2000, failed: 0, skipped: 0, running: 0, waiting: 0 }, truncated: false, groups: [], ...extra });
    const run = (extra) => {
      const j = clone(F['plan-in-progress']);
      j.tests = { sections: [{ step: { id: 'S1', title: 'Krok S1' }, state: 'running', attempts: [mkA(extra)] }], failedTotal: 0, runningTotal: 0 };
      const m = mount(j, { now: NOW['plan-in-progress'] });
      const n = m.root.querySelectorAll('#testsView .tgroups-note');
      const r = { count: n.length, note: n.length ? text(n[0]) : null, role: n.length ? n[0].getAttribute('role') : null };
      m.board.destroy(); return r;
    };
    ex.groupsNote = { cut: run({ groupsTotal: 2000, groupsOmitted: 1800 }), zero: run({ groupsTotal: 3, groupsOmitted: 0 }), absent: run({}), junk: run({ groupsTotal: 'x', groupsOmitted: 'y' }), noTotal: run({ groupsOmitted: 5 }) };
  }
  // Formatowanie w linii: tytuły kroków, „czeka na”, pytania do eksperta. HTML wstawia tylko host (mountHtml); dymki i atrybuty zostają czystym tekstem.
  {
    const nm = 'plan-in-progress';
    const d = clone(F[nm]);
    const step = d.progress.phases[0].steps[0];
    step.title = 'Krok **ważny** z `kodem`'; step.titleHtml = 'Krok <strong>ważny</strong> z <code>kodem</code>';
    const plainStep = d.progress.phases[0].steps[1];
    plainStep.title = 'Krok zwykły'; delete plainStep.titleHtml;
    d.waiting = [{ what: 'decyzja o **zakresie**', whatHtml: 'decyzja o <strong>zakresie</strong>', source: 'checkpoint', since: null, action: 'Odpowiedz w sesji.', link: null }];
    const m = mount(d, { now: NOW[nm] });
    const li = m.root.querySelectorAll('li.step').__asArray().filter((x) => text(x.querySelector('.sid')) === step.id)[0];
    const liPlain = m.root.querySelectorAll('li.step').__asArray().filter((x) => text(x.querySelector('.sid')) === plainStep.id)[0];
    const wait = m.root.querySelector('#waitBanner .wait-txt strong');
    const noMount = mount(d, { now: NOW[nm], deps: {} });
    const q = clone(F['questions']);
    q.expertQuestions.items = [
      { id: 'Q1', runSlug: 'r', text: 'Czy **tak**?', textHtml: 'Czy <strong>tak</strong>?', blocking: true, state: 'open', at: null, addressees: 'all', url: null },
      { id: 'Q2', runSlug: 'r', text: 'zwykłe pytanie', blocking: false, state: 'open', at: null, addressees: 'all', url: null },
    ];
    // CORE-QFILTER K3: zakładka renderuje `expertQuestions.current`, więc te same wiersze jadą teraz tam (asercje bez zmian)
    q.expertQuestions.current = { open: 2, blocking: 1, prototypes: [], items: q.expertQuestions.items };
    const mq = mount(q, { now: NOW['questions'] });
    const fd = clone(F[nm]);
    fd.run = Object.assign({}, fd.run, { checkpoints: [{ time: '10:00', text: 'zrobione **S1**', textHtml: 'zrobione <strong>S1</strong>' }, { time: '10:05', text: 'zrobione S2' }] });
    const mfeed = mount(fd, { now: NOW[nm] });
    ex.inlineMd = {
      feedMounted: mfeed.calls.mountHtml.filter((h) => h.indexOf('zrobione') === 0),
      feedClick: ((mfeed.root.querySelector('#feed').listeners || {}).click || []).length,
      feedPlain: text(mfeed.root.querySelectorAll('#feed li')[1].querySelectorAll('span')[1]),
      mounted: m.calls.mountHtml,
      stepTitleNode: li ? li.querySelector('.tt').localName : null, stepTitleText: li ? text(li.querySelector('.tt')) : null,
      stepTip: li ? li.getAttribute('data-desc-extra') : null,
      plainStepText: liPlain ? text(liPlain.querySelector('.tt')) : null,
      waitText: text(wait), waitStrong: wait ? wait.childNodes.map((c) => (c instanceof E ? c.localName : 't')) : null,
      questionMounted: mq.calls.mountHtml.filter((h) => h.indexOf('Czy') === 0),
      questionPlain: text(mq.root.querySelectorAll('li.q-item')[1].querySelector('.q-text')),
    };
  }
  // Pomiar i prognoza: wiersz prognozy w akcie III (przedział, powód, wstrzymana) i czas aktów I i II. Prognoza ma inną klasę i etykietę niż pomiar.
  {
    const nm = 'plan-in-progress';
    const run = (patch, view) => { const d = clone(F[nm]); Object.assign(d, patch); return mount(d, { now: NOW[nm], deps: view ? { forecastView: view } : {} }); };
    const rangeF = { scope: 'plan', remaining: 5, samples: 4, basis: 'run', paused: false, lowSeconds: 3000, medianSeconds: 4500, highSeconds: 6750 };
    const one = (m, sel) => { const e = m.root.querySelector(sel); return e ? { text: text(e), cls: e.getAttribute('class') } : null; };
    ex.forecast = {
      tempo: one(run({ forecast: rangeF }, 'tempo'), '[data-forecast]'),
      tempoPaused: one(run({ forecast: Object.assign({}, rangeF, { paused: true }) }, 'tempo'), '[data-forecast]'),
      tempoFast: one(run({ forecast: Object.assign({}, rangeF, { medianSeconds: 100 }) }, 'tempo'), '[data-forecast]'),
      tempoNoRemaining: run({ forecast: Object.assign({}, rangeF, { remaining: 0 }) }, 'tempo').root.querySelectorAll('[data-forecast]').length,
      badView: one(run({ forecast: rangeF }, 'nonsense'), '[data-forecast]'),
      range: one(run({ forecast: rangeF }), '[data-forecast]'),
      rangeExplicit: one(run({ forecast: rangeF }, 'range'), '[data-forecast]'),
      same: one(run({ forecast: Object.assign({}, rangeF, { lowSeconds: 3000, highSeconds: 3040 }) }, 'range'), '[data-forecast]'),
      paused: one(run({ forecast: Object.assign({}, rangeF, { paused: true }) }, 'range'), '[data-forecast]'),
      few: one(run({ forecast: { scope: 'plan', reason: 'not-enough-data', have: 2, need: 4 } }), '[data-forecast]'),
      moments: one(run({ forecast: { scope: 'plan', reason: 'few-moments', have: 1, need: 3 } }), '[data-forecast]'),
      norun: one(run({ forecast: { scope: 'plan', reason: 'no-run-start' } }), '[data-forecast]'),
      silentReasons: ['plan-not-running', 'no-remaining'].map((r) => run({ forecast: { scope: 'plan', reason: r } }).root.querySelectorAll('[data-forecast]').length),
      absent: run({ forecast: undefined }).root.querySelectorAll('[data-forecast]').length,
      broken: run({ forecast: { scope: 'plan', remaining: 'x', lowSeconds: -1, medianSeconds: 1, highSeconds: 2 } }).root.querySelectorAll('[data-forecast]').length,
      acts: (() => {
        const d = clone(F[nm]);
        d.timing = Object.assign({}, d.timing, { available: true, acts: [
          { key: 'spec', startedAt: '2026-01-11T09:00:00+01:00', endedAt: '2026-01-11T10:05:00+01:00', workSeconds: 3000, waitSeconds: 900, unknown: false, reason: null, endSource: 'approval' },
          { key: 'plan', startedAt: '2026-01-11T10:05:00+01:00', endedAt: null, workSeconds: null, waitSeconds: null, unknown: true, reason: 'no-approval', endSource: null } ] });
        const m = mount(d, { now: NOW[nm] });
        return { spec: one(m, '[data-measure="spec"]'), plan: one(m, '[data-measure="plan"]'), real: m.root.querySelectorAll('[data-measure="real"]').length };
      })(),
    };
  }
  // CORE-QFILTER F3 (K9–K11): zwijane grupy listy dokumentów (details/summary), stan w st.docGroupsOpen, rozwinięcie przy wyborze.
  {
    const name = Object.keys(F)[0];
    const mk = (key, label, n) => ({ key, label, items: Array.from({ length: n }, (_, i) => ({ path: `docs/${key}/d${i}.md`, title: `D${i} ${key}`, status: 'draft', mtime: '2026-01-12T10:00:00Z' })) });
    const docs = () => [mk('task', 'Bieżące zadanie', 2), mk('run', 'Przebieg r1', 2), mk('specs', 'Speki', 3), mk('plans', 'Plany', 2), mk('design', 'Projekt', 1), mk('domain', 'Domena', 4)];
    const data = () => { const d = clone(F[name]); d.docs = docs(); return d; };
    const grp = (m, key) => m.root.querySelectorAll('#docList details').__asArray().filter((x) => x.getAttribute('data-doc-group') === key)[0] || null;
    const state = (m) => { const o = {}; m.root.querySelectorAll('#docList details').forEach((x) => { o[x.getAttribute('data-doc-group')] = x.hasAttribute('open'); }); return o; };
    const toggle = (g, open) => { if (open) g.setAttribute('open', ''); else g.removeAttribute('open'); g.dispatchEvent(new Ev('toggle')); };
    const exQ = {};
    try {
      {
      const m = mount(data(), { now: NOW[name], preview: true });
      const dl = m.root.querySelector('#docList');
      exQ.initial = state(m);
      exQ.keys = m.root.querySelectorAll('#docList details').__asArray().map((x) => x.getAttribute('data-doc-group'));
      exQ.summaries = m.root.querySelectorAll('#docList details').__asArray().map((x) => text(x.querySelector('summary')));
      exQ.firstChild = m.root.querySelectorAll('#docList details').__asArray().map((x) => x.childNodes[0] && x.childNodes[0].localName);
      exQ.h3 = dl.querySelectorAll('h3').length;
      exQ.items = m.root.querySelectorAll('#docList .doc-item').length;
      exQ.itemsInClosed = grp(m, 'specs').querySelectorAll('.doc-item').length;
      exQ.docCount = text(m.root.querySelector('#docCount'));
      const cur = () => m.root.querySelectorAll('#docList .doc-item').__asArray().filter((b) => b.getAttribute('aria-current') === 'true').map((b) => b.getAttribute('data-doc'));
      exQ.current = [cur(), (m.board.update(data()), cur())];   // jak dotąd: domyślny wybór oznaczony dopiero po kolejnym rysowaniu
      exQ.keydown = dl.querySelectorAll('*').__asArray().filter((x) => (x.listeners.keydown || []).length || (x.listeners.keyup || []).length).length + ((dl.listeners.keydown || []).length);
      // przeżycie update(): rozwinięcie i zwinięcie
      toggle(grp(m, 'plans'), true);
      m.board.update(data());
      exQ.keptOpen = state(m);
      toggle(grp(m, 'task'), false);
      m.board.update(data());
      exQ.keptClosedTask = state(m).task;
    }
    {
      // wybór przez loadDoc (klik), rozwija grupę; po ręcznym zwinięciu odświeżenie jej nie otwiera
      const m = mount(data(), { now: NOW[name], preview: true });
      m.root.querySelectorAll('#docList .doc-item').__asArray().filter((b) => b.getAttribute('data-doc') === 'docs/specs/d1.md')[0].click();
      await tick(); await tick();
      exQ.afterClick = { specs: state(m).specs, plans: state(m).plans, current: m.root.querySelectorAll('#docList .doc-item').__asArray().filter((b) => b.getAttribute('aria-current') === 'true').map((b) => b.getAttribute('data-doc')) };
      toggle(grp(m, 'specs'), false);
      m.board.update(data()); await tick(); await tick();
      exQ.afterCollapse = state(m).specs;
      m.board.setDoc('docs/domain/d2.md');
      exQ.afterSetDoc = state(m).domain;
      toggle(grp(m, 'domain'), false);
      m.board.update(data());
      exQ.afterSetDocRefresh = state(m).domain;
      // link md-doc / setView -> loadDoc
      m.board.setDoc('docs/design/d0.md'); m.board.setView('docs', false); await tick(); await tick();
      exQ.afterView = state(m).design;
    }
    {
      // setDoc po narysowaniu listy: grupa wybranego dokumentu (inna niż domyślna) rozwija się od razu
      const m = mount(data(), { now: NOW[name], preview: true });
      m.board.setDoc('docs/plans/d1.md'); await tick();
      exQ.afterSetDocOtherGroup = state(m).plans;
    }
    {
      // setDoc przed pierwszym update(): grupa rozwija się przy pierwszym rysowaniu
      const m = mount(null, { now: NOW[name], preview: true });
      m.board.setDoc('docs/specs/d2.md');
      m.board.update(data());
      exQ.setDocFirst = state(m);
    }
    {
      // fokus na summary przeżywa przebudowę listy
      const m = mount(data(), { now: NOW[name], preview: true });
      const sm = grp(m, 'plans').querySelector('summary');
      sm.dispatchEvent(new Ev('focus'));
      m.doc.focused = null;
      m.board.update(data());
      const nw = grp(m, 'plans').querySelector('summary');
      exQ.focus = { restored: m.doc.focused === nw, fresh: nw !== sm };
    }
    {
      // openDoc (przycisk readiness w trybie podglądu) rozwija grupę dokumentu
      const d2 = clone(F['readiness-blocked']); d2.docs = docs(); d2.docs[2].items[0].path = d2.readiness.source;
      const m = mount(d2, { now: NOW['readiness-blocked'], preview: true });
      const before = state(m).specs;
      m.root.querySelector('.readiness [data-open-doc]').click(); await tick(); await tick();
      exQ.openDoc = { before, after: state(m).specs, view: m.board.state().view };
    }
    {
      // nic nie trafia do storage po toggle
      const m = mount(data(), { now: NOW[name], preview: true });
      toggle(grp(m, 'specs'), true); toggle(grp(m, 'specs'), false);
      m.board.update(data());
      exQ.storage = m.calls.storage.length;
      // odłączony <details> (po przebudowie listy) nie zmienia stanu
      const old = grp(m, 'plans'); toggle(grp(m, 'plans'), true);
      m.board.update(data()); old.removeAttribute('open'); old.dispatchEvent(new Ev('toggle'));
      exQ.detached = state(m).plans;
    }
    {
      // zmiana grupy wybranego dokumentu (np. przełączenie planu): nowa grupa rozwija się raz
      const m = mount(data(), { now: NOW[name], preview: true });
      m.board.setDoc('docs/task/d0.md');
      const d = data(); d.docs[0].items = d.docs[0].items.slice(1); d.docs[3].items.push({ path: 'docs/task/d0.md', title: 'D0', status: 'draft', mtime: '2026-01-12T10:00:00Z' });
      m.board.update(d);
      exQ.groupMoved = state(m).plans;
      toggle(grp(m, 'plans'), false); m.board.update(d);
      exQ.groupMovedCollapsed = state(m).plans;
    }
    {
      // fokus na summary grupy, która znika z docs[]: powrót grupy nie kradnie fokusu
      const m = mount(data(), { now: NOW[name], preview: true });
      grp(m, 'plans').querySelector('summary').dispatchEvent(new Ev('focus'));
      const without = data(); without.docs = without.docs.filter((g) => g.key !== 'plans');
      m.board.update(without);
      m.doc.focused = null;
      m.board.update(data());
      exQ.focusGone = m.doc.focused;
    }
    } catch (e) { exQ.error = String(e && e.stack || e); }
    ex.qfilterDocs = exQ;
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
                self.assertEqual(got["sprite"], 17)
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

    def test_workdir_chip_for_main_and_worktree(self) -> None:
        """K4/K5: chip katalogu roboczego w nagłówku; `span`, nie przycisk; opis przez desc(), ścieżki w data-desc-extra."""
        w = self.out["extra"]["stab"]["workdir"]
        self.assertTrue(w["main"]["found"], w["main"])
        self.assertEqual(w["main"]["text"], "główny checkout")
        self.assertEqual(w["main"]["extra"], "/work/app")
        self.assertEqual(w["wt"]["text"], "worktree: F1")
        self.assertEqual(w["wt"]["tag"], "SPAN")
        self.assertEqual(w["wt"]["buttons"], 0)
        self.assertEqual(w["wt"]["desc"], "workdir_chip")
        self.assertIn("/work/app-wt/F1", w["wt"]["extra"])
        self.assertIn("Główny checkout: /work/app", w["wt"]["extra"])
        self.assertNotIn("Główny checkout", w["wtNoMain"]["extra"])
        self.assertNotIn("Główny checkout", w["main"]["extra"])
        for k in ("main", "wt"):
            self.assertIn("chip", w[k]["cls"].split())
            self.assertIn("s-planned", w[k]["cls"].split())
            self.assertTrue(w[k]["inner"], "wielokropek na wewnętrznym span nazwy")
            self.assertEqual(w[k]["parentCls"], "m")
            self.assertTrue(w[k]["icon"])
        self.assertEqual(w["wt"]["innerText"], "worktree: F1")

    def test_workdir_chip_absent_without_usable_data(self) -> None:
        w = self.out["extra"]["stab"]["workdir"]
        for k in ("nul", "missing", "unknown"):
            self.assertFalse(w[k]["found"], (k, w[k]))

    def test_workdir_chip_sits_after_branch_and_before_unpushed(self) -> None:
        o = self.out["extra"]["stab"]["workdir"]["ordered"]["order"]
        iw = next(i for i, t in enumerate(o) if t.startswith("worktree: F1"))
        ib = next(i for i, t in enumerate(o) if "feat/x" in t)
        ia = next(i for i, t in enumerate(o) if "niewypchnięt" in t)
        self.assertLess(ib, iw)
        self.assertLess(iw, ia)

    def test_workdir_chip_shows_exactly_once_with_or_without_branch(self) -> None:
        w = self.out["extra"]["stab"]["workdir"]
        self.assertEqual(w["noBranchMain"]["count"], 1, w["noBranchMain"])
        self.assertEqual(w["noBranchMain"]["text"], "główny checkout")
        self.assertEqual(w["noBranchWt"]["count"], 1, w["noBranchWt"])
        self.assertEqual(w["noBranchWt"]["text"], "worktree: F1")
        self.assertEqual(w["noBranchNull"]["count"], 0)
        for k in ("main", "wt", "ordered"):
            self.assertEqual(w[k]["count"], 1, k)

    def test_workdir_chip_long_name_is_truncated_by_css_inside(self) -> None:
        w = self.out["extra"]["stab"]["workdir"]["longName"]
        self.assertTrue(w["found"])
        self.assertEqual(w["innerText"], "worktree: " + "x" * 200)
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.workdir \{[^}]*max-width")
        m = re.search(r"\.workdir \.workdir-name \{([^}]*)\}", css)
        self.assertIsNotNone(m, "reguła .workdir-name")
        for decl in ("overflow: hidden", "text-overflow: ellipsis", "white-space: nowrap", "display: inline-block", "max-width"):
            self.assertIn(decl, m.group(1))
        self.assertNotIn("[data-tip]", m.group(0))

    def test_workdir_chip_description_exists_and_renderer_adds_no_deps(self) -> None:
        descs = json.loads((ASSETS.parent / "descriptions.json").read_text(encoding="utf-8"))
        self.assertIn("workdir_chip", descs)
        self.assertTrue(descs["workdir_chip"].get("title"))
        self.assertTrue(descs["workdir_chip"].get("text"))
        src = RENDERER.read_text(encoding="utf-8")
        self.assertIn('icon("i-folder")', src)
        self.assertIn('desc("workdir_chip"', src)
        self.assertIn('"i-folder":', src)

    def test_stab_unpushed_badge_opens_a_panel_with_the_commits(self) -> None:
        """170-A: z `delivery.commits` plakietka jest przyciskiem; panel: aria, Esc, klik poza, tekst; bez pola jak dotąd (K5, K6)."""
        c = self.out["extra"]["stab"]["commits"]
        self.assertEqual((c["closed"]["tag"], c["closed"]["expanded"], c["closed"]["controls"]), ("BUTTON", "false", "commitsPanel"))
        self.assertTrue(c["closed"]["hidden"])
        self.assertEqual((c["opened"]["expanded"], c["opened"]["hidden"]), ("true", False))
        self.assertEqual(c["opened"]["items"][1], "def5678drugi")
        self.assertIn("password: [REDACTED]", c["opened"]["items"][0])
        self.assertIn("<img src=x", c["opened"]["items"][0], "temat jest tekstem, nie znacznikiem")
        self.assertEqual(c["opened"]["imgs"], 0)
        self.assertEqual(c["opened"]["more"], "Pokazano 2 najnowsze; starszych: 20.")
        self.assertEqual(c["opened"]["title"], "Niewypchnięte commity (22) · feat/x")
        self.assertEqual(c["opened"]["closeLabel"], "Zamknij listę commitów")
        self.assertEqual(c["afterClose"]["expanded"], "false", "przycisk zamknięcia zamyka panel")
        self.assertEqual(c["keptOnRefresh"]["expanded"], "true", "stan panelu przeżywa odświeżenie danych")
        self.assertEqual((c["afterEsc"]["expanded"], c["afterEsc"]["hidden"]), ("false", True))
        self.assertEqual(c["afterInside"]["expanded"], "true", "klik w panelu go nie zamyka")
        self.assertEqual(c["afterOutside"]["expanded"], "false")
        self.assertEqual(c["afterEmpty"]["expanded"], "false", "po opróżnieniu listy panel nie wraca otwarty")
        self.assertEqual(c["noField"], {"btn": False, "chip": "22 niewypchnięte"})
        self.assertEqual(c["emptyList"], {"btn": False, "chip": "22 niewypchnięte"})

    def test_stab_delivery_mode_chip(self) -> None:
        """CORE-1170-MODE: chip trybu w nagłówku: tekst i dymek z danych wejściowych (nie z renderera), ostrzeżenie dla złego trybu, brak chipu bez pola."""
        m = self.out["extra"]["stab"]["mode"]
        tip = "Tryb dostarczania: {mode}; baza: {base}; źródło: {source}".format
        warn = "Nieprawidłowy tryb dostarczania w manifeście (delivery.mode albo delivery.base): nie zakładaj »pr«, zapytaj użytkownika"
        for key, mode, base, label, tooltip in (
                ("pr", "pr", "main", "tryb: pr, baza: main", tip(mode="pr", base="main", source="z manifestu")),
                ("prNoBase", "pr", None, "tryb: pr", tip(mode="pr", base="brak", source="domyślny (brak delivery.mode w manifeście)")),
                ("emptyBase", "pr", "", "tryb: pr", tip(mode="pr", base="brak", source="z manifestu")),
                ("direct", "direct", "develop", "tryb: direct, baza: develop", tip(mode="direct", base="develop", source="z manifestu"))):
            got = m[key]
            with self.subTest(case=key):
                self.assertEqual((got["count"], got["tag"], got["text"], got["delivery"], got["tip"]), (1, "SPAN", label, mode, tooltip))
                self.assertEqual(got["cls"], "chip s-planned delivery-mode")
                self.assertEqual(got["buttons"], 0, "chip to nie przycisk")
        for key in ("invalid", "srcInvalid", "srcInvalidMode"):   # serwer wysyła mode=invalid/source=manifest; renderer ostrzega także przy source=invalid
            got = m[key]
            with self.subTest(case=key):
                self.assertEqual((got["count"], got["text"], got["delivery"], got["tip"]), (1, "tryb dostarczania: błąd", "invalid", warn))
                self.assertEqual(got["cls"], "chip s-warn delivery-mode")
                self.assertNotIn("pr", got["text"].split(), "zły tryb nie udaje domyślnego »pr«")
        for key in ("noField", "nul", "notObject", "numeric", "empty", "noMode"):
            with self.subTest(case=key):
                self.assertEqual((m[key]["count"], m[key]["text"], m[key]["tip"]), (0, None, None), "bez trybu nie ma chipu (i nie ma wyjątku: harness by padł)")
        self.assertEqual(m["hostile"]["text"], "tryb: direct, baza: <img src=x onerror=alert(1)>", "baza jest tekstem")
        self.assertEqual(m["hostile"]["imgs"], 0)
        order = [x for x in m["pr"]["order"] if x != "?"]
        self.assertTrue({"BRANCH", "MODE", "PR"} <= set(order), order)
        self.assertLess(order.index("BRANCH"), order.index("MODE"), "chip trybu po bloku gałęzi")
        self.assertLess(order.index("MODE"), order.index("PR"), "chip trybu przed chipem PR")

    def test_stab_tests_tab_sections_bars_and_links(self) -> None:
        """180-A: sekcje kroków (najnowsza u góry, rozwinięta tylko trwająca), pasek i link przy kroku, tekst jako tekst, zakładka ukryta bez danych."""
        t = self.out["extra"]["stab"]["testsTab"]
        self.assertFalse(t["initial"]["tabHidden"])
        self.assertEqual((t["initial"]["count"], t["initial"]["countCls"]), ("1", "count is-bad"))
        self.assertEqual([s["id"] for s in t["initial"]["sections"]], ["tsec-F1_3", "tsec-F1_2", "tsec-F1_1"])
        self.assertEqual([(s["expanded"], s["body"]) for s in t["initial"]["sections"]], [("true", True), ("false", False), ("false", False)])   # tylko trwająca
        self.assertIn("1 czerwony", t["initial"]["sections"][1]["head"])
        self.assertEqual(t["opened"]["sections"][1]["expanded"], "true")
        self.assertEqual([bool(x["controls"]) for x in t["initial"]["sections"]], [True, False, False])   # aria-controls only for an open section (its body exists)
        self.assertEqual(t["rows"]["imgs"], 0)
        self.assertIn("<img src=x", t["rows"]["name"])
        self.assertIn("AssertionError: 1 != 2", t["rows"]["msg"])
        self.assertEqual(t["rows"]["historyButtons"], 2)
        self.assertIn("bez stanu „uruchomiony”", t["rows"]["level"])   # an adapter of `live: completion`: no artificial running state
        self.assertIn("bieg 1", t["prev"])
        self.assertEqual(t["closed"]["sections"][1]["expanded"], "false")
        self.assertTrue(any("1/3" in b["text"] or "2/3" in b["text"] for b in t["bars"]), t["bars"])
        self.assertTrue(all("testów" in b["label"] or "testu" in b["label"] for b in t["bars"]))
        self.assertEqual((t["afterLink"]["view"], t["afterLink"]["sections"][2]["expanded"]), ("tests", "true"))
        self.assertIn("tests:F1.1", t["nav"])
        self.assertEqual(t["noTests"], {"tabHidden": True, "stbar": 0, "tab": "progress"})

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

    def test_questions_section_is_gone_from_the_spec_act(self) -> None:
        # Q-TOAST: strona statusu nie rysuje listy pytań ani założeń (dla prowadzącego i widza, także z zerem); pola JSON zostają.
        d = self.fixtures["questions"]
        self.assertTrue(d["blockingQuestions"]["items"] and d["assumptions"]["items"])
        for name, got in self.out["fixtures"].items():
            with self.subTest(fixture=name):
                self.assertIs(got["questions"], False)
        self.assertIs(self.out["extra"]["qviewer"]["questions"], False)
        x = self.out["extra"]["qxss"]
        self.assertNotIn("error", x)
        for tag in ("img", "script", "b", "i"):
            self.assertNotIn(tag, x["tags"])
        self.assertNotIn("<img", x["banner"])

    def test_waiting_banner_writing_and_expert_items(self) -> None:
        w = self.out["extra"]["wux"]
        writing = w["writing"][0]
        self.assertNotIn("⏳", w["titles"]["writing"], "praca sesji nie jest czekaniem na człowieka")
        self.assertIn("⏳ Czeka", w["titles"]["expert"])
        self.assertIn("Sesja pracuje od 14:10: agent pisze ekrany (do 14:25)", writing["text"])
        self.assertNotIn("czeka na Ciebie", writing["text"])
        self.assertIn("Zniknie, gdy agent skończy pisać albo znacznik wygaśnie", writing["text"])
        self.assertIn("znacznik zapisu ekranów prototypu", writing["text"])
        self.assertEqual(writing["hrefs"], ["http://127.0.0.1:5130/run-alpha/"])
        expert = w["expert"][0]
        self.assertIn("Sesja czeka na Ciebie: odpowiedź eksperta (1 blokujących)", expert["text"])
        self.assertEqual(expert["hrefs"], ["http://127.0.0.1:5130/_experts/run-alpha"])
        self.assertEqual(expert["expertLinks"], 1)
        self.assertEqual(w["expertMany"][0]["hrefs"], ["http://127.0.0.1:5130/_experts"])
        self.assertEqual(w["expertHostile"][0]["hrefs"], ["http://127.0.0.1:5130/_experts"], "zła ścieżka wraca do listy")
        self.assertEqual(w["expertNoOrigin"][0]["hrefs"], [], "bez adresu serwera prototypów nie ma odnośnika")
        self.assertEqual(w["expertNoOrigin"][0]["expertLinks"], 0)
        self.assertNotIn("javascript", " ".join(w["expertBadOrigin"][0]["hrefs"]))

    def test_questions_toast_counts_all_runs(self) -> None:
        d = self.fixtures["questions"]["expertQuestions"]
        self.assertEqual((d["open"], d["blocking"], len(d["runs"])), (3, 2, 2))
        t = self.out["extra"]["qtoast"]
        text = "Pytania do eksperta: 3 otwartych (2 blokujących)"
        self.assertEqual(len(t["two"]), 1)
        two = t["two"][0]
        self.assertTrue(two["text"].startswith(text), two["text"])
        self.assertEqual(two["hrefs"], ["http://127.0.0.1:5130/_experts"])  # kilka przebiegów = lista
        self.assertEqual((two["close"], two["role"]), (0, None))  # rolę status ma już baner
        self.assertIn("wait-item", two["cls"])
        self.assertEqual(t["one"][0]["hrefs"], ["http://127.0.0.1:5130/_experts/run-alpha"])  # jeden przebieg = jego strona
        self.assertTrue(t["one"][0]["text"].startswith("Pytania do eksperta: 2 otwartych (1 blokujących)"))
        # bez adresu serwera prototypów albo z adresem spoza http(s): sam tekst
        self.assertEqual(t["noOrigin"][0]["hrefs"], [])
        self.assertEqual(t["badOrigin"][0]["hrefs"], [])
        self.assertTrue(t["noOrigin"][0]["text"].startswith(text))
        # url z danych nie jest ufany: bierze się tylko ścieżka /_experts/…, inaczej lista
        self.assertEqual(t["hostileUrl"][0]["hrefs"], ["http://127.0.0.1:5130/_experts"])
        # open = 0, brak pola i śmieci = brak toastu
        for key in ("zero", "junk", "absent"):
            self.assertEqual(t[key], [], key)

    def test_questions_toast_is_a_state_not_an_action_result(self) -> None:
        life = self.out["extra"]["qtoast"]["lifecycle"]
        self.assertEqual(life["timersAfterMount"], 0)  # bez timera
        self.assertEqual(len(life["before"]), 1)
        self.assertEqual(life["after"], [])  # znika, gdy open = 0
        self.assertNotIn("Pytania do eksperta", life["banner"])
        both = self.out["extra"]["qtoast"]["withBanner"]
        self.assertEqual(len(both["toast"]), 1)
        self.assertGreaterEqual(both["items"], 2)  # obok innych elementów banera
        self.assertEqual(len(self.out["extra"]["qviewer"]["qtoast"]), 1)  # widz widzi to samo

    def test_eta_is_in_the_act_tile_and_at_the_step_and_counts_in_place(self) -> None:
        e = self.out["extra"]["eta"]
        self.assertEqual(self.fixtures["eta"]["eta"]["until"], "11:40")
        self.assertEqual(self.fixtures["eta"]["eta"]["steps"], ["F2.1"])
        self.assertEqual(e["before"]["bar"], 0)   # no toast in the notification bar any more
        self.assertEqual(e["timersAfterMount"], 0)
        self.assertEqual(len(e["before"]["act"]), 1)
        self.assertIn("parsowanie fixtures: koniec ok. " + e["clock"], e["before"]["act"][0])
        self.assertIn("zostało 30:00", e["before"]["act"][0])
        self.assertEqual(len(e["before"]["step"]), 1)   # the active step of the list (F2.1)
        self.assertIn("zostało 30:00", e["before"]["step"][0])
        self.assertEqual(e["role"], "timer")
        self.assertEqual(e["minute"]["count"], ["zostało 01:00", "zostało 01:00"])
        self.assertEqual(e["over5"]["count"], ["przekroczono o 00:05", "przekroczono o 00:05"])
        self.assertIn("is-over", e["overCls"])
        self.assertEqual(e["over17"]["count"], ["przekroczono o 17:05", "przekroczono o 17:05"])
        self.assertEqual(e["over65"]["count"], ["przekroczono o 1 h 05 min", "przekroczono o 1 h 05 min"])
        self.assertTrue(e["sameNode"], "the countdown is updated in place by the tick: the same DOM node, no render()")
        self.assertIn("prototyp: koniec ok. " + e["clock2"], e["updated"]["act"][0])   # the new (shifted) term replaces the old one
        self.assertEqual(e["afterGone"]["act"], [])
        self.assertEqual(e["afterGone"]["step"], [])
        self.assertIn("koniec ok. 11:40", e["noIsoRd"]["act"][0])   # without untilIso only the clock, no countdown
        self.assertEqual(e["noIsoRd"]["count"], ["", ""])
        self.assertIsNone(e["noIsoRole"])   # no countdown: no timer role and no label
        self.assertIn("is-over", e["overAtMount"])   # past the term already at the first draw
        self.assertEqual(e["aria0"], "parsowanie fixtures: koniec ok. " + e["clock"] + ", zostało 30:00")
        self.assertIn("30 min", e["shiftTip"])   # the tooltip of a shifted term
        self.assertEqual(e["devTip"], "szacunek 20 min, było 35 min, czekanie na użytkownika 5 min")
        self.assertEqual(e["noStepsRd"]["step"], [])   # an estimate without steps shows only in the tile
        self.assertEqual(len(e["noStepsRd"]["act"]), 1)
        self.assertEqual(len(e["legacyRd"]["act"]), 1)   # a payload without the new fields still draws
        self.assertEqual(e["legacyRd"]["step"], [])

    def test_eta_paused_shows_what_the_session_waits_for_and_never_counts(self) -> None:
        e = self.out["extra"]["eta"]
        self.assertEqual(e["pausedRd"]["count"][0], "wstrzymane: czeka na odpowiedź Q1")
        self.assertEqual(e["noWhat"]["count"][0], "wstrzymane: czeka na użytkownika")   # a waiting item of the agent is not the user's

    def test_eta_is_text_only_for_everybody(self) -> None:
        e = self.out["extra"]["eta"]
        self.assertEqual(e["junk"]["act"], [])
        self.assertEqual(e["absent"]["act"], [])
        self.assertEqual(e["hostileTags"], 0)
        self.assertIn("<img src=x onerror=alert(1)>: koniec ok. " + e["clock"], e["hostile"]["act"][0])

    def test_doc_groups_are_collapsible_details(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual(q["keys"], ["task", "run", "specs", "plans", "design", "domain"])
        self.assertEqual(q["initial"], {"task": True, "run": True, "specs": False, "plans": False, "design": False, "domain": False})
        self.assertEqual(q["summaries"], ["Bieżące zadanie (2)", "Przebieg r1 (2)", "Speki (3)", "Plany (2)", "Projekt (1)", "Domena (4)"])
        self.assertEqual(q["firstChild"], ["summary"] * 6)
        self.assertEqual(q["h3"], 0)

    def test_doc_items_stay_in_dom_in_collapsed_groups(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual((q["items"], q["itemsInClosed"], q["docCount"]), (14, 3, "14"))
        self.assertEqual(q["current"], [[], ["docs/task/d0.md"]])

    def test_doc_groups_state_survives_update(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual(q["keptOpen"], {"task": True, "run": True, "specs": False, "plans": True, "design": False, "domain": False})
        self.assertIs(q["keptClosedTask"], False)

    def test_selecting_a_document_reveals_its_group_once(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual(q["afterClick"], {"specs": True, "plans": False, "current": ["docs/specs/d1.md"]})
        self.assertIs(q["afterCollapse"], False)       # odświeżenie bez zmiany wyboru nie rozwija ponownie
        self.assertIs(q["afterSetDoc"], True)          # setDoc
        self.assertIs(q["afterSetDocRefresh"], False)
        self.assertIs(q["afterView"], True)            # setDoc + setView('docs') -> loadDoc
        self.assertIs(q["afterSetDocOtherGroup"], True)
        self.assertIs(q["setDocFirst"]["specs"], True)  # setDoc przed pierwszym rysowaniem

    def test_doc_group_summary_keeps_focus_and_default_keys(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual(q["keydown"], 0)  # nic w #docList nie przechwytuje Enter/Spacji
        self.assertEqual(q["focus"], {"restored": True, "fresh": True})
        self.assertIsNone(q["focusGone"])

    def test_doc_groups_open_on_openDoc_move_and_leave_no_storage(self) -> None:
        q = self.out["extra"]["qfilterDocs"]
        self.assertNotIn("error", q, q.get("error"))
        self.assertEqual(q["openDoc"], {"before": False, "after": True, "view": "docs"})
        self.assertEqual(q["storage"], 0)
        self.assertIs(q["detached"], True)  # toggle odłączonego details nie nadpisuje stanu
        self.assertIs(q["groupMoved"], True)
        self.assertIs(q["groupMovedCollapsed"], False)

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

    def test_timing_section(self) -> None:
        got = self.out["fixtures"]["timing"]["timing"]
        self.assertTrue(got["inAct"])
        self.assertEqual((got["summary"], got["summaryDesc"], got["open"]), ("Czas pracy i czekania", "timing_time", False))
        # fixture `timing`: faza 1 = praca 1:25:00 (5100 s), czekanie 45:00 (2700 s); faza 2 nieznana; okno „CP” bez zamknięcia
        self.assertEqual([r["id"] for r in got["rows"]], ["Faza 1", "Faza 2", "Σ"])
        self.assertEqual(got["rows"][0]["t"], "praca 1:25:00 · czekanie 45:00")
        self.assertEqual((got["rows"][1]["t"], got["rows"][1]["cls"]), ("praca brak danych · czekanie brak danych (niepełne)", "is-nodata"))
        self.assertEqual(got["rows"][2]["name"], "Razem (okna nieznane: 1)")
        self.assertEqual(got["rows"][2]["cls"], "is-nodata")
        tip = got["summaryTip"].split("\n")
        self.assertEqual(len(tip), 3)
        self.assertIn("do zgody", tip[0])
        self.assertIn("do kolejnego checkpointu", tip[1])
        self.assertIn("okno nieznane", tip[2])
        self.assertEqual(got["rows"][2]["t"], "praca 1:25:00 · czekanie 45:00+ (niepełne)")   # „+”: są okna nieznane, więc to dolna granica
        for name in ("empty", "run-mode", "readiness-blocked", "spec-act"):
            shown = self.out["fixtures"][name]["timing"]    # brak sekcji albo tylko wiersz sumy z oknem nieznanym (bez faz)
            self.assertTrue(shown is None or [r["id"] for r in shown["rows"]] == ["Σ"], name)
        only_window = self.out["fixtures"]["questions"]["timing"]               # tylko okno nieznane: sam wiersz sumy, nic nie jest szacowane
        self.assertEqual([r["id"] for r in only_window["rows"]], ["Σ"])
        self.assertEqual(only_window["rows"][0]["t"], "praca brak danych · czekanie 0:00+ (niepełne)")
        self.assertIn("okno nieznane", only_window["summaryTip"])

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
    TOKEN_TEXT_START = "Zgody wymagają adresu z tokenem"

    def test_missing_token_disables_the_approve_buttons_with_one_reason(self) -> None:
        waiting_seen = 0
        for name, modes in self.out["token"].items():
            with self.subTest(fixture=name, mode="missing"):
                got = modes["missing"]
                self.assertNotIn("error", got, got.get("error"))
                self.assertEqual(got["approveCalls"], 0)   # a click on a disabled button does not reach approve
                buttons = [b for a in got["acts"] for b in a["buttons"]] + got["bannerButtons"]
                for b in buttons:
                    self.assertFalse(b["approveAttr"], b)
                    if b["state"] == "waiting":
                        waiting_seen += 1
                        self.assertTrue(b["disabled"], b)
                        self.assertTrue((b["extra"] or "").startswith(self.TOKEN_TEXT_START), b)
        self.assertGreater(waiting_seen, 0, "at least one fixture offers a consent that waits")

    def test_with_a_token_or_without_the_requirement_the_buttons_work_as_before(self) -> None:
        for name, modes in self.out["token"].items():
            for mode in ("present", "notRequired", "noHook"):
                with self.subTest(fixture=name, mode=mode):
                    got = modes[mode]
                    self.assertNotIn("error", got, got.get("error"))
                    reference = self.out["viewer"][name]["absent"]
                    self.assertEqual(got["approveCalls"], reference["approveCalls"])
                    buttons = [b for a in got["acts"] for b in a["buttons"]] + got["bannerButtons"]
                    for b in buttons:
                        self.assertFalse((b["extra"] or "").startswith(self.TOKEN_TEXT_START), b)
        self.assertTrue(any(m["present"]["approveCalls"] > 0 for m in self.out["token"].values()))

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

    def test_s160_stage_state_indicator_and_tip(self) -> None:
        done, active, planned = self.out["extra"]["s160"]["stages"]
        self.assertIn("is-active", active["cls"].split())
        self.assertEqual(active["dot"], 1)
        self.assertEqual(active["dataState"], "active")
        self.assertTrue(active["tip"].startswith("trwa od 10:05"), active["tip"])
        self.assertIn("w trakcie, od 10:05", active["text"])
        for tile, state in ((done, "done"), (planned, "planned")):
            self.assertEqual(tile["dot"], 0)
            self.assertNotIn("is-active", tile["cls"].split())
            self.assertEqual(tile["dataState"], state)
            self.assertNotIn("trwa od", tile["tip"] or "")
        self.assertIn("is-done", done["cls"].split())
        self.assertNotIn("is-done", planned["cls"].split())

    def test_s160_active_stage_stops_pulsing_when_the_run_is_silent(self) -> None:
        q = self.out["extra"]["s160"]["quiet"]
        self.assertIn("is-quiet", q["old"]["cls"].split())
        self.assertIn("brak aktywności od 90 min", q["old"]["text"])
        self.assertTrue(q["old"]["tip"].startswith("brak aktywności od 90 min"), q["old"]["tip"])
        self.assertEqual(q["old"]["dataState"], "active")  # the data stays `active`
        self.assertNotIn("is-quiet", q["fresh"]["cls"].split())
        self.assertIn("w trakcie", q["fresh"]["text"])
        self.assertIn("is-quiet", q["silenceKey"]["cls"].split(), "the silence signal of the plan decides when it is present")
        self.assertIn("brak aktywności od 50 min", q["silenceKey"]["text"])

    def test_s160_prototype_screens_bar(self) -> None:
        s = self.out["extra"]["s160"]
        full = s["screens"]
        self.assertEqual(full["screens"], "3 z 8 ekranów")  # koniec (eta) w atrybucie title
        self.assertEqual(full["meter"], {"max": "8", "now": "3", "w": "38%"})
        part = s["screensNoPlan"]
        self.assertEqual(part["screens"], "3 ekrany")
        self.assertIsNone(part["meter"])
        self.assertIsNone(s["screensNoWriting"]["screens"])
        self.assertIsNone(s["screensNoWriting"]["meter"])

    def test_s160_waiting_who(self) -> None:
        w = self.out["extra"]["s160"]["who"]
        self.assertEqual(w["label"], "Czeka na: akceptujący od 09:50: odpowiedź Q3")
        self.assertEqual(w["login"], "Czeka na: ekspert (jan) od 09:50: odpowiedź Q3")
        self.assertEqual(w["none"], "Sesja czeka na Ciebie od 09:50: odpowiedź Q3")
        self.assertEqual(w["writing"], "Sesja pracuje od 09:50: agent pisze ekrany (do 14:25)")
        self.assertEqual(w["hostile"], "Czeka na: <i>q</i> (<b>z</b>) od 09:50: odpowiedź Q3")

    def test_s160_two_shades_and_caption(self) -> None:
        two, none = self.out["extra"]["s160"]["bars"]["two"], self.out["extra"]["s160"]["bars"]["none"]
        self.assertIn("has-active", two["cls"].split())
        self.assertEqual(two["legend"], "40% zweryfikowane, 25% zgłoszone lub w toku")
        self.assertEqual(two["valuetext"], "40% zweryfikowane, 25% zgłoszone lub w toku")
        self.assertEqual([f[1] for f in two["fills"]], ["40%", "10%", "15%"])
        self.assertEqual(two["pend"], "(+10% do weryfikacji)")
        self.assertNotIn("has-active", none["cls"].split())
        self.assertIsNone(none["legend"])
        self.assertEqual(none["valuetext"], "40%, plus 10% do weryfikacji")
        self.assertEqual([f[0] for f in none["fills"]], ["fill", "fill-pending"])

    def test_s170_to_top_button_threshold_with_hysteresis(self) -> None:
        t = self.out["extra"]["s170"]["threshold"]
        self.assertEqual(t["shape"], {"type": "button", "label": "Wróć na górę strony", "cls": "to-top", "tip": "Na górę", "icon": 1})
        self.assertEqual(t["trace"], [False, False, True, True, False, False, True, False])
        self.assertTrue(t["rootChild"], "the button lives outside .dash-wrap (position: fixed needs no containment)")

    def test_s170_to_top_button_only_in_the_document_view(self) -> None:
        v = self.out["extra"]["s170"]["views"]
        self.assertEqual(v, {"shownDocs": True, "shownProgress": False, "shownQuestions": False, "backInDocs": True})
        self.assertTrue(self.out["extra"]["s170"]["failedLoad"]["hidden"])   # a document that failed to load: no button

    def test_s170_click_goes_to_top_moves_the_focus_and_destroy_unsubscribes(self) -> None:
        c = self.out["extra"]["s170"]["click"]
        self.assertTrue(c["before"])
        self.assertEqual((c["goes"], c["tabId"]), (1, "tab-docs"))
        self.assertEqual(c["focused"], [{"preventScroll": True}])   # the focus moves to the tab at once, the button stays while the page scrolls
        self.assertTrue(c["shownRightAfter"])
        self.assertTrue(c["shownDuringScroll"], "no blink while the smooth scroll is still above the hide threshold")
        self.assertTrue(c["hiddenAtTheTop"])
        self.assertEqual(c["unsub"], 1)
        self.assertTrue(c["rootClassGone"])

    # CORE-TESTDEBT2 (a), K1: the note about the groups cut by the server.
    def test_core_testdebt2_groups_note_only_when_groups_were_omitted(self) -> None:
        g = self.out["extra"]["groupsNote"]
        self.assertEqual(g["cut"]["count"], 1)
        self.assertEqual(g["cut"]["role"], "note")
        self.assertIn("Pokazano 200 z 2000 grup", g["cut"]["note"])
        for key in ("zero", "absent", "junk", "noTotal"):
            self.assertEqual(g[key]["count"], 0, key)

    # CORE-TESTDEBT F2, K5: the note about skipped result files (`tests.skipped`) in the tests tab.
    def test_core_testdebt_skipped_note_text_count_paths_and_reasons(self) -> None:
        t = self.out["extra"]["testDebt"]
        n = t["three"]
        self.assertEqual(n["role"], "note")
        self.assertEqual(n["notes"], 1)
        self.assertTrue(n["note"].startswith("Pominięto 3 pliki lub katalogi wyników testów (nie dało się ich uwzględnić): "), n["note"])
        self.assertIn("a/one (katalog spoza kroków)", n["note"])
        self.assertIn("b/two (nieczytelny)", n["note"])
        self.assertIn("c/three (weird <b>reason</b>)", n["note"])   # unknown reason: raw text, no HTML
        self.assertEqual(n["imgs"], 0)
        self.assertEqual(n["secs"], 1, "the sections are still drawn")
        self.assertIn("Pominięto 1 plik lub katalog wyników testów", t["one"]["note"])

    def test_core_testdebt_skipped_note_shows_at_most_five_paths(self) -> None:
        note = self.out["extra"]["testDebt"]["six"]["note"]
        for p in ("p1", "p2", "p3", "p4", "p5"):
            self.assertIn(p, note)
        self.assertNotIn("p6", note)
        self.assertIn("Pominięto 6 ", note)
        self.assertTrue(note.endswith(" i 1 więcej."), note)

    def test_core_testdebt_skipped_note_at_server_limit_says_at_least_twenty(self) -> None:
        note = self.out["extra"]["testDebt"]["twenty"]["note"]
        self.assertIn("co najmniej 20", note)
        self.assertNotIn("d19", note)
        self.assertTrue(note.endswith(" i 15 więcej."), note)

    def test_core_testdebt_skipped_count_matches_the_listed_entries_and_empty_reason_has_no_brackets(self) -> None:
        note = self.out["extra"]["testDebt"]["mixed"]["note"]
        self.assertIn("Pominięto 3 pliki lub katalogi", note)   # null, string and number are not entries
        self.assertIn("m/ok (nieczytelny)", note)
        self.assertIn("m/nr, m/obj", note)
        self.assertNotIn("()", note)
        self.assertNotIn("[object", note)

    def test_core_testdebt_skipped_with_empty_sections_shows_the_tab_with_the_note(self) -> None:
        e = self.out["extra"]["testDebt"]["emptySections"]
        self.assertFalse(e["tabHidden"])
        self.assertEqual(e["note"], "Nie udało się odczytać wyników testów.")
        self.assertEqual((e["secs"], e["legend"]), (0, 0))
        self.assertEqual(e["empty"], 1, "a short message instead of the empty list")

    def test_core_testdebt_no_skipped_field_or_empty_list_leaves_the_tab_unchanged(self) -> None:
        t = self.out["extra"]["testDebt"]
        for k in ("noField", "emptyList"):
            self.assertIsNone(t[k]["note"], k)
            self.assertEqual((t[k]["tabHidden"], t[k]["secs"], t[k]["legend"], t[k]["empty"]), (False, 1, 1, 0), k)
        self.assertEqual((t["emptyBoth"]["note"], t["emptyBoth"]["secs"], t["emptyBoth"]["empty"]), (None, 0, 0))

    # CORE-TAP F3, K8: "the command failed" state (exit != 0 and failed = 0) in the section HEADER of the tests tab.
    # Contract fragments for the implementer: "kod N" and "żaden test nie zgłosił porażki" in .tsec-head (visible while the body is collapsed).
    def test_core_tap_command_failed_state_in_the_collapsed_header_for_every_tool(self) -> None:
        s = self.out["extra"]["coreTap"]["states"]
        for sid, code in (("CMDF", 1), ("PYT", 2), ("TMO", 124)):   # tap, pytest (collection error), timeout with a code
            self.assertEqual(s[sid]["expanded"], "false", sid)
            self.assertIn("żaden test nie zgłosił porażki", s[sid]["head"], sid)
            self.assertIn(f"kod {code}", s[sid]["head"], sid)
            self.assertRegex(s[sid]["markCls"] or "", r"bad|fail", f"{sid}: the message carries an error class")

    def test_core_tap_command_failed_state_only_where_it_belongs(self) -> None:
        s = self.out["extra"]["coreTap"]["states"]
        for sid in ("GRN", "RED", "NOEX", "RUN"):   # green, with a failure, no exit (interrupted), running
            self.assertFalse(s[sid]["inHead"], f"{sid}: no new state")
        self.assertIn("1", s["RED"]["head"])   # the existing red chips stay
        # the exit comes from the shown attempt: the older green attempt has none, the newest has code 3
        self.assertTrue(s["HIST"]["inHead"])
        self.assertIn("kod 3", s["HIST"]["head"])
        self.assertFalse(s["HISTold"]["inHead"])
        self.assertTrue(s["HISTnew"]["inHead"])

    def test_core_tap_command_failed_state_levels_counts_and_exit_type(self) -> None:
        s = self.out["extra"]["coreTap"]["states"]
        self.assertFalse(s["LVC"]["inHead"], "levels 'command' with exit != 0: no test results, so no chip")
        self.assertFalse(s["STR"]["inHead"], "exit as a string is not a number: no chip")
        self.assertTrue(s["NOC"]["inHead"], "no counts at all means failed = 0: the chip shows")
        self.assertIn("kod 5", s["NOC"]["head"])

    def test_core_tap_full_sentence_is_a_visible_line_in_the_expanded_body(self) -> None:
        b = self.out["extra"]["coreTap"]["states"]["bodies"]
        full = "żaden test nie zgłosił porażki (wyjątek poza raportowanymi testami); wyniki ok dotyczą tylko testów, które zdążyły się wykonać"
        self.assertFalse(b["CMDF"]["before"], "collapsed: no body")
        self.assertTrue(b["CMDF"]["open"])
        self.assertEqual(b["CMDF"]["note"], "kod 1, " + full)
        for sid in ("GRN", "RED", "LVC", "STR"):   # attempts without the state: body opens, no line
            self.assertTrue(b[sid]["open"], sid)
            self.assertIsNone(b[sid]["note"], sid)
        self.assertIn("kod 5, " + full, b["NOC"]["note"])

    def test_core_tap_command_failure_does_not_raise_the_failed_counter_on_the_tab(self) -> None:
        t = self.out["extra"]["coreTap"]["tab"]
        self.assertEqual((t["count"], t["cls"]), ("", "count"))   # failedTotal stays what the server sent

    # CORE-TAP F3, K10: the "back to top" button in the Tests view.
    def test_core_tap_to_top_in_tests_threshold_hysteresis_and_css_hit(self) -> None:
        t = self.out["extra"]["coreTap"]["threshold"]
        self.assertEqual(t["view"], "tests")
        self.assertEqual(t["trace"], [False, False, True, True, False, False, True, False])
        self.assertTrue(t["hasClass"])
        self.assertTrue(t["padHit"], "`#view-tests .tests-view` exists in the DOM: the padding rule hits")

    def test_core_tap_to_top_in_tests_focus_returns_to_the_tests_tab(self) -> None:
        e = self.out["extra"]["coreTap"]
        self.assertTrue(e["focusOnHide"]["hidden"])
        self.assertEqual([f[0] for f in e["focusOnHide"]["focused"]], ["tests"])
        self.assertEqual(e["click"]["goes"], 1)
        self.assertEqual(e["click"]["focused"], [["tests", {"preventScroll": True}]])
        self.assertTrue(e["click"]["still"])

    def test_core_tap_to_top_leaving_tests_hides_it_and_focuses_the_tab_of_the_new_view(self) -> None:
        e = self.out["extra"]["coreTap"]
        for key, view in (("toProgress", "progress"), ("toQuestions", "questions")):
            self.assertTrue(e[key]["before"], key)
            self.assertEqual(e[key]["view"], view, key)
            self.assertTrue(e[key]["hidden"], key)
            self.assertEqual([f[0] for f in e[key]["focused"]], [view], key)

    def test_core_tap_to_top_tests_to_docs_one_button_and_focus_on_the_docs_tab(self) -> None:
        d = self.out["extra"]["coreTap"]["toDocs"]
        self.assertEqual(d["view"], "docs")
        self.assertEqual(d["buttons"], 1)
        self.assertEqual([f[0] for f in d["atSwitch"]], ["docs"])
        self.assertEqual(d["seen"], [True, False, True], "hidden while the document loads, shown once after it")

    def test_core_tap_to_top_in_tests_needs_the_hook(self) -> None:
        self.assertEqual(self.out["extra"]["coreTap"]["noHook"], {"none": True, "noClass": True})

    def test_s170_threshold_edges_and_a_window_without_height(self) -> None:
        e = self.out["extra"]["s170"]["edges"]
        self.assertEqual(e["edges"], [False, True, True, False, False])
        self.assertTrue(e["hasClass"])

    def test_s170_a_focused_button_hands_the_focus_to_the_tab_when_it_hides(self) -> None:
        f = self.out["extra"]["s170"]["focusOnHide"]
        self.assertEqual((f["hiddenNow"], f["movedOnHide"], f["afterBlur"]), (True, 1, 1))   # moved once; no move when the focus had left already
        self.assertEqual(f["option"], {"preventScroll": True})

    def test_s170_without_the_hook_there_is_no_button(self) -> None:
        self.assertEqual(self.out["extra"]["s170"]["noHook"], {"noClass": True, "none": True, "oldAdapter": True, "broken": True})

    def test_s160_parse_warnings_line(self) -> None:
        w = self.out["extra"]["s160"]["warn"]
        two = w["two"]
        self.assertFalse(two["hidden"])
        self.assertEqual(two["lines"], 1)
        self.assertEqual(two["text"], "2 fragmenty raportu pominięto (bez id kroku na początku; id kroku nie na początku elementu)")
        self.assertIn("bez id <img src=x>", two["tip"])
        self.assertIn("10:02 zrobione", two["tip"])
        self.assertFalse(two["img"])
        for key in ("none", "empty"):
            self.assertTrue(w[key]["hidden"], key)
            self.assertIn(w[key]["text"], ("", None))

    def test_s160_parse_warning_reasons_have_labels_and_an_unknown_one_shows_raw(self) -> None:
        w = self.out["extra"]["s160"]["warn"]
        self.assertEqual(w["unbalanced"]["text"], "1 fragment raportu pominięto (niezamknięty nawias, lista dzielona po każdym „;”)")
        self.assertIn("10:03 raport: „F1.1 (a; b, F1.2; ryzyko: brak” (niezamknięty nawias", w["unbalanced"]["tip"])
        self.assertEqual(w["unknown"]["text"], "1 fragment raportu pominięto (reason-from-the-future)")   # a reason this renderer does not know is shown as sent
        self.assertIn("(reason-from-the-future)", w["unknown"]["tip"])

    def test_s160_legacy_json_renders_without_new_elements(self) -> None:
        legacy = self.out["extra"]["s160"]["legacy"]
        self.assertEqual(set(legacy), set(self.fixtures))
        for name, got in legacy.items():
            with self.subTest(fixture=name):
                self.assertNotIn("error", got, got.get("error"))
                self.assertEqual((got["dots"], got["active"], got["screens"], got["legend"], got["light"]), (0, 0, 0, 0, 0))
                self.assertTrue(got["warn"])
                self.assertFalse(got["sessionWait"])

    def test_forecast_and_measure_are_shown_apart(self) -> None:
        r = self.out["extra"]["forecast"]
        # the `tempo` view (host option): the pace per step and the steps left, no sum and no clock time
        self.assertEqual(r["tempo"]["cls"], "forecast")
        self.assertEqual(r["tempo"]["text"], "Prognoza: orientacyjnie, tempo: ok. 15 min pracy na krok, z 4 zakończonych kroków; zostało 5 kroków")
        self.assertNotRegex(r["tempo"]["text"], r"koniec ok|\d\d:\d\d|do końca kroków")
        self.assertIn("is-paused", r["tempoPaused"]["cls"])
        self.assertIn("wstrzymane", r["tempoPaused"]["text"])
        self.assertIn("mniej niż minuta pracy na krok", r["tempoFast"]["text"])
        self.assertEqual(r["tempoNoRemaining"], 0)
        self.assertEqual(r["badView"]["text"], r["range"]["text"], "an unknown view falls back to the default (range)")
        self.assertEqual(r["rangeExplicit"]["text"], r["range"]["text"])
        # the default view: the range with an approximate clock time, marked as approximate in the row itself
        self.assertIn("orientacyjnie:", r["range"]["text"])
        self.assertIn("Rząd wielkości, nie obietnica.", r["range"]["text"])
        self.assertEqual(r["range"]["cls"], "forecast")
        self.assertIn("Prognoza", r["range"]["text"])
        self.assertIn("ok. 50 min–1 h 55 min pracy", r["range"]["text"])
        self.assertIn("zostało 5 kroków", r["range"]["text"])
        self.assertIn("bez czekania na zgody", r["range"]["text"])
        self.assertRegex(r["range"]["text"], r"koniec ok\. \d\d:\d\d–\d\d:\d\d")
        self.assertIn("ok. 50 min pracy", r["same"]["text"])
        self.assertNotIn("–", r["same"]["text"].split("pracy")[0])
        self.assertIn("is-paused", r["paused"]["cls"])
        self.assertIn("wstrzymana", r["paused"]["text"])
        self.assertNotRegex(r["paused"]["text"], r"koniec ok\.")
        self.assertIn("za mało danych", r["few"]["text"])
        self.assertIn("zakończone kroki z czasem: 2, potrzeba co najmniej 4", r["few"]["text"])
        self.assertIn("is-none", r["few"]["cls"])
        self.assertIn("RUN=", r["norun"]["text"])
        self.assertIn("w zbyt mało różnych momentach (1, potrzeba co najmniej 3)", r["moments"]["text"])
        self.assertEqual(r["silentReasons"], [0, 0], "a plan that does not run or has nothing left shows no forecast line")
        self.assertEqual((r["absent"], r["broken"]), (0, 0), "no field or a broken one: no line, no number made up")

    def test_act_times_are_measures_with_their_own_label(self) -> None:
        a = self.out["extra"]["forecast"]["acts"]
        self.assertEqual(a["spec"]["cls"], "measure")
        self.assertEqual(a["spec"]["text"], "Pomiar: praca 50 min, czekanie 15 min")
        self.assertRegex(a["plan"]["text"], r"^Pomiar: (trwa od|czas nieznany)")
        self.assertEqual(a["real"], 0, "act III has its own timing block, not a measure line")

    def test_inline_markdown_in_titles_waiting_and_questions(self) -> None:
        r = self.out["extra"]["inlineMd"]
        self.assertIn("Krok <strong>ważny</strong> z <code>kodem</code>", r["mounted"])
        self.assertIn("decyzja o <strong>zakresie</strong>", r["mounted"])
        self.assertEqual(r["questionMounted"], ["Czy <strong>tak</strong>?"])
        self.assertEqual(r["feedMounted"], ["zrobione <strong>S1</strong>"])
        self.assertEqual(r["feedPlain"], "zrobione S2")
        self.assertEqual(r["feedClick"], 1, "links of the feed (md-doc, md-anchor) work like in the other blocks")
        self.assertEqual(r["stepTitleNode"], "span")
        self.assertEqual(r["plainStepText"], "Krok zwykły", "no `titleHtml` = plain text, as before")
        self.assertEqual(r["questionPlain"], "zwykłe pytanie")
        self.assertEqual(r["waitStrong"], ["t", "span"], "the prefix is text, the item is one mounted span")
        self.assertNotIn("**", r["stepTip"] or "", "the tooltip is plain text without markers")
        self.assertNotIn("`", r["stepTip"] or "")
        self.assertIn("Krok ważny z kodem", r["stepTip"] or "")

    def test_result_is_formatted_when_the_server_sends_html(self) -> None:
        r = self.out["extra"]["s160b"]["resultRich"]
        self.assertEqual(r["mounted"], ["<h3>A</h3><p>b</p>", "<p>reszta</p>"])
        self.assertEqual(r["headTag"], "div")
        self.assertIn("md", r["headClass"].split())
        self.assertIn("md", r["restClass"].split())
        self.assertEqual(r["summary"], "Pokaż resztę (7 linii)")
        self.assertEqual(r["details"], 1)
        self.assertEqual(r["noRestDetails"], 0)
        self.assertEqual(r["noRestMounted"], ["<p>x</p>"])

    def test_s160b_result_block_and_glossary(self) -> None:
        r = self.out["extra"]["s160b"]["result"]
        long = r["long"]
        self.assertFalse(long["hidden"])
        self.assertTrue(long["inProgress"])
        self.assertEqual(long["path"], "Plan: docs/plans/x.md")
        self.assertEqual(len(long["head"].split("\n")), 12)
        self.assertEqual(len(long["rest"].split("\n")), 3)
        self.assertIn("\n- punkt 1 <b>x</b>\n", "\n" + long["head"] + "\n")
        self.assertFalse(long["b"])
        self.assertEqual(long["details"], 1)
        self.assertEqual(long["summary"], "Pokaż resztę (3 linie)")
        short = r["short"]
        self.assertEqual(short["head"], "- a\n- b")
        self.assertEqual(short["details"], 0)
        for key in ("none", "absent"):
            self.assertTrue(r[key]["hidden"], key)
        for key, got in r.items():  # legenda zawsze, zwinięta, z dziewięcioma pojęciami
            with self.subTest(case=key):
                self.assertEqual(got["glossary"]["n"], 9)
                self.assertEqual(got["glossary"]["summary"], "Pojęcia")
                self.assertFalse(got["glossary"]["open"])
        self.assertEqual(r["none"]["glossary"]["keys"], ["legend_act", "legend_stage", "legend_phase", "legend_step", "legend_consent",
                                                         "legend_result", "legend_debt", "legend_eta", "legend_verify"])
        self.assertTrue(r["none"]["glossary"]["first"].startswith("AktJeden z trzech"))  # tekst awaryjny
        self.assertEqual(r["described"]["glossary"]["first"], "Akt (z JSON)Tekst z opisów.")

    def test_s160b_addressees_in_toast(self) -> None:
        t = self.out["extra"]["s160b"]["toast"]
        self.assertEqual(t["all"]["addr"], "do wszystkich ekspertów")
        self.assertEqual(t["list"]["addr"], "do: ewa, jan, ola")
        for key in ("empty", "missing"):
            self.assertIsNone(t[key]["addr"], key)
        self.assertEqual(t["viewerLogins"]["addr"], "do: jan", "viewer sees the addressees like the operator (decision 15.11)")
        self.assertTrue(t["all"]["text"].startswith("Pytania do eksperta: 2 otwartych (1 blokujących)"))

    def test_s160b_comments_after_acceptance_toast(self) -> None:
        c = self.out["extra"]["s160b"]["comments"]
        two = c["two"]
        self.assertEqual(two["toast"]["text"].split("Otwórz")[0], "3 nowe komentarze po akceptacji (ekrany s1, s3)")
        self.assertEqual(two["toast"]["close"], 0)
        self.assertEqual(two["timers"], [])
        self.assertEqual(two["toast"]["hrefs"], [["Otwórz prototyp run-alpha", "http://127.0.0.1:5130/run-alpha/"]])
        self.assertEqual(c["noHost"]["toast"]["hrefs"], [])
        self.assertIn("3 nowe komentarze po akceptacji (ekran s1)", c["noHost"]["toast"]["text"])
        self.assertEqual(c["noSlug"]["toast"]["hrefs"], [])
        self.assertIn("<i>s</i>", c["hostile"]["toast"]["text"])
        for key in ("zero", "absent"):
            self.assertIsNone(c[key]["toast"], key)

    def test_s160b_questions_tab_visibility_and_a11y(self) -> None:
        s = self.out["extra"]["s160b"]
        tab = s["tab"]
        for key in ("bare", "protosWrongType", "itemsWrongType"):
            # assertions changed on purpose (CORE-QTAB): `bare` has no spec questions now (fixture fields removed), so "hidden" still holds; the cases with them are below
            self.assertTrue(tab[key]["hidden"], key)
            self.assertTrue(tab[key]["panelHidden"], key)
        for key in ("protosWrongTypeWithSpec", "itemsWrongTypeWithSpec", "withSpecQuestions"):
            # CORE-QTAB (assertions changed on purpose): the 2 blocking questions of the fixture `questions` show the tab although the expert fields are junk or absent
            self.assertFalse(tab[key]["hidden"], key)
            self.assertTrue(tab[key]["panelHidden"], key)
            self.assertEqual(tab[key]["count"], "2", key)
            self.assertEqual((tab[key]["role"], tab[key]["panelRole"], tab[key]["controls"]), ("tab", "tabpanel", "view-questions"))
        old = tab["olderServerNoSpecFields"]
        self.assertFalse(old["hidden"], "an older server without the spec fields still shows the expert group (current: 2 prototypes, 2 questions)")
        self.assertEqual((old["realize"], old["assume"], old["realizeEmpty"], old["expertProtos"], old["count"]), (0, 0, "Brak pytań do realizacji", 2, "2"))
        nothing = tab["olderServerNothingAtAll"]
        self.assertTrue(nothing["hidden"] and nothing["panelHidden"])
        self.assertEqual(nothing["count"], "")
        for key in ("withProtos", "withItems"):
            self.assertFalse(tab[key]["hidden"], key)
            self.assertEqual((tab[key]["role"], tab[key]["panelRole"], tab[key]["controls"], tab[key]["selected"]), ("tab", "tabpanel", "view-questions", "false"))
            self.assertTrue(tab[key]["panelHidden"], key)
        nav = s["nav"]
        self.assertEqual(nav["before"], {"tabs": ["true", "false", "false"], "panels": [False, True, True]})
        self.assertEqual(nav["afterClick"]["tabs"], ["false", "false", "true"])
        self.assertEqual(nav["afterClick"]["panels"], [True, True, False])
        self.assertEqual(nav["afterClick"]["nav"][-1], "questions")
        self.assertTrue(nav["prevented"])
        self.assertEqual(nav["wrapped"], ["true", "false", "false"])  # z ostatniej zakładki w prawo na pierwszą
        self.assertEqual(nav["back"], ["false", "false", "true"])
        self.assertEqual(nav["home"], ["true", "false", "false"])
        self.assertEqual(nav["end"], ["false", "false", "true"])
        self.assertEqual(nav["gone"]["view"], "progress")  # zakładka zniknęła z danych: widok wraca do Postępu
        self.assertEqual(nav["gone"]["tabs"], ["true", "false", "false"])
        self.assertTrue(nav["gone"]["tabHidden"])

    def test_s160b_questions_rows(self) -> None:
        r = self.out["extra"]["s160b"]["rows"]
        full = r["full"]
        self.assertEqual(len(full["protos"]), 2)
        a = full["protos"][0]["text"]
        for part in ("run-alpha", "iteracja 2", "3 z 4 ekranów zaakceptowanych, nieaktualne 1", "agent pisze… 2 z 6 ekranów", "3 wątki otwarte", "2 komentarze po akceptacji"):
            self.assertIn(part, a)
        self.assertEqual(full["protos"][0]["hrefs"], ["http://127.0.0.1:5130/run-alpha/"])
        self.assertNotIn("po akceptacji", full["protos"][1]["text"])
        self.assertNotIn("agent pisze", full["protos"][1]["text"])
        self.assertIn("0 wątków otwartych", full["protos"][1]["text"])
        q1, q2, q3 = full["items"]
        self.assertEqual(q1["qtext"], "x" * 160 + "…")
        self.assertIn("blokujące", q1["text"])
        self.assertIn("otwarte", q1["text"])
        self.assertIn("przed chwilą", q1["text"])
        self.assertEqual(q1["to"], "do: jan, ola")
        self.assertEqual(q1["hrefs"], ["http://127.0.0.1:5130/_experts/run-alpha"])
        self.assertEqual(q2["qtext"], "<b>krótkie</b>")
        self.assertFalse(full["b"])
        self.assertIn("odpowiedziane", q2["text"])
        self.assertIn("2 d temu", q2["text"])
        self.assertEqual(q2["to"], "do wszystkich ekspertów")
        self.assertEqual(q3["qtext"], "treść niedostępna")
        self.assertIn("wycofane", q3["text"])
        self.assertEqual(q3["hrefs"], ["http://127.0.0.1:5130/_experts"])  # zła ścieżka url spada do listy
        self.assertEqual(r["noHost"]["items"][0]["hrefs"], [])
        self.assertEqual(r["noHost"]["protos"][0]["hrefs"], [])
        self.assertEqual(r["itemsCap"], 50)
        v = r["viewer"]["items"][0]
        self.assertEqual(v["qtext"], "treść niedostępna", "no text in the JSON: neutral caption")
        self.assertEqual(v["to"], "do wszystkich ekspertów")
        leak = r["viewerLeak"]["items"][0]
        self.assertEqual(leak["qtext"], "SEKRET", "viewer sees the question text like the operator (decision 15.11)")
        self.assertEqual(leak["to"], "do: jan")
        self.assertEqual(r["empty"]["empties"], ["Brak prototypów", "Brak pytań"])
        self.assertEqual(r["emptyBoth"]["empties"], [], "assertions changed on purpose (CORE-QTAB): with both groups empty the panel is cleared (the tab is hidden)")
        self.assertNotIn("error", r["wrongTypes"], r["wrongTypes"].get("error"))

    def test_qfilter_tab_shows_only_the_current_run(self) -> None:
        """CORE-QFILTER K3, K4: rows and counter come from `expertQuestions.current`, not from the project-wide lists; the toast stays project-wide."""
        q = self.out["extra"]["s160b"]["qfilter"]
        cur = q["current"]
        self.assertFalse(cur["hidden"])
        self.assertEqual((cur["protos"], cur["items"], cur["count"]), (["run-alpha"], ["qn-a"], "1"), "the counter is current.open (1), not the project-wide 3")
        self.assertIn("3 otwartych", cur["toast"], "the toast counts the whole project (K4)")
        zero = q["currentZero"]
        self.assertFalse(zero["hidden"])
        self.assertEqual((zero["count"], zero["items"]), ("", ["qn-a"]), "open 0: empty counter, the tab stays")

    def test_qfilter_current_null_hides_the_tab(self) -> None:
        """CORE-QFILTER K3 (STATUS-160-A assertions changed on purpose: a non-empty project-wide `prototypes`/`items` no longer shows the tab when `current` is null)."""
        q = self.out["extra"]["s160b"]["qfilter"]
        null = q["currentNull"]
        # assertions changed on purpose (CORE-QTAB): the tab stays hidden only without spec questions (`bare`); with them it stays visible with the realize group
        self.assertTrue(null["hidden"] and null["panelHidden"])
        self.assertEqual((null["protos"], null["items"], null["count"]), ([], [], ""))
        spec = q["currentNullWithSpec"]
        self.assertFalse(spec["hidden"])
        self.assertEqual((spec["protos"], spec["items"], spec["count"]), ([], [], "2"), "the expert rows are gone (current is null), the 2 spec questions count")
        self.assertIn("3 otwartych", null["toast"], "the project toast survives")
        nav = q["currentNullToNav"]
        self.assertEqual((nav["before"], nav["after"], nav["hidden"]), ("questions", "progress", True), "the open view falls back to progress")

    def test_qfilter_server_json_to_renderer_and_bad_current(self) -> None:
        """CORE-QFILTER K3: the fixture of the real server (`current` untouched) renders as the server computed it; junk `current` hides the tab."""
        q = self.out["extra"]["s160b"]["qfilter"]
        srv = q["server"]
        self.assertFalse(srv["hidden"])
        # assertions changed on purpose (CORE-QTAB): the counter is the sum of the 2 spec questions and the 2 open expert questions
        self.assertEqual((srv["protos"], srv["items"], srv["count"]), (["run-alpha", "run-delta"], ["qn-b00001", "qn-a00001"], "4"))
        for key, got in q["currentBadType"].items():
            # assertions changed on purpose (CORE-QTAB): without spec questions (`bare`) junk `current` still hides the tab; with them only the realize group is left
            self.assertTrue(got["hidden"], key)
            self.assertEqual((got["protos"], got["items"], got["count"]), ([], [], ""), key)
        for key, got in q["currentBadTypeWithSpec"].items():
            self.assertFalse(got["hidden"], key)
            self.assertEqual((got["protos"], got["items"], got["count"]), ([], [], "2"), key)
        self.assertFalse(q["currentOpenString"]["hidden"])
        self.assertEqual(q["currentOpenString"]["count"], "", "a non-numeric open gives an empty counter")

    def test_qfilter_fallback_without_current_key(self) -> None:
        """CORE-QFILTER K5: a missing `current` key (older server) filters locally by `prototype.slug`; `null` is not the same as missing."""
        q = self.out["extra"]["s160b"]["qfilter"]
        fb = q["fallback"]
        self.assertFalse(fb["hidden"])
        self.assertEqual((fb["protos"], fb["items"], fb["count"]), (["run-alpha"], ["qn-a"], "2"))
        self.assertEqual(q["fallbackNoRun"]["count"], "")
        self.assertEqual(q["fallbackNoRun"]["protos"], ["run-alpha"])
        for key in ("fallbackNoSlug", "fallbackNoPrototype"):
            # assertions changed on purpose (CORE-QTAB): `bare` has no spec questions, so the tab is still hidden; with the spec's questions see below
            self.assertTrue(q[key]["hidden"], key)
            self.assertEqual((q[key]["protos"], q[key]["items"], q[key]["count"]), ([], [], ""), key)
        for key in ("fallbackNoSlugWithSpec", "fallbackNoPrototypeWithSpec"):
            # assertions changed on purpose (CORE-QTAB): the approximation finds no run, but the 2 spec questions keep the tab; the expert group is empty
            self.assertFalse(q[key]["hidden"], key)
            self.assertEqual((q[key]["protos"], q[key]["items"], q[key]["count"]), ([], [], "2"), key)

    def test_s160b_legacy_json_has_no_new_elements(self) -> None:
        legacy = self.out["extra"]["s160b"]["legacy"]
        self.assertEqual(set(legacy), set(self.fixtures))
        for name, got in legacy.items():
            with self.subTest(fixture=name):
                # assertions changed on purpose (CORE-QTAB): the legacy JSON also lacks `blockingQuestions` and `assumptions` (the harness removes them);
                # the same fixtures with those fields are checked in `test_s160b_legacy_json_with_spec_questions_shows_the_realize_group`
                self.assertNotIn("error", got, got.get("error"))
                self.assertTrue(got["tabHidden"] and got["panelHidden"] and got["resultHidden"])
                self.assertEqual((got["comments"], got["addr"], got["rows"]), (0, 0, 0))
                self.assertEqual(got["glossary"], 9)  # legenda pojęć jest zawsze

    def test_s160b_legacy_json_with_spec_questions_shows_the_realize_group(self) -> None:
        """CORE-QTAB: the contract fixtures keep their spec fields; a fixture with questions or assumptions shows the tab, the rest stays hidden."""
        spec = self.out["extra"]["s160b"]["legacySpec"]
        self.assertEqual(set(spec), set(self.fixtures))
        shown = set()
        for name, got in spec.items():
            data = self.fixtures[name]
            blocking, assumptions = len(data["blockingQuestions"]["items"]) if "blockingQuestions" in data else 0, len(data["assumptions"]["items"]) if "assumptions" in data else 0
            with self.subTest(fixture=name):
                self.assertNotIn("error", got, got.get("error"))
                self.assertEqual((got["realize"], got["assume"]), (blocking, assumptions))
                self.assertEqual(got["tabHidden"], blocking + assumptions == 0)
                self.assertEqual(got["count"], str(blocking) if blocking else "")
                self.assertEqual(got["groups"], 2)
                if blocking + assumptions:
                    shown.add(name)
        self.assertEqual(shown, {"plan-in-progress", "questions", "spec-act"}, "the fixtures with spec questions or assumptions")

    def test_s160b_css_has_new_blocks(self) -> None:
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        for sel in (".result-box", ".glossary", ".q-row", ".q-list", ".q-group", ".q-group-title", ".q-assume", ".q-more", ".q-accepted"):
            self.assertIn(sel, css)

    def test_s160_css_pulse_stops_under_reduced_motion(self) -> None:
        css = (ASSETS / "app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.stage-dot\s*\{[^}]*animation:\s*pulse")
        block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S)
        self.assertIsNotNone(block)
        self.assertRegex(block.group(1), r"\.stage-dot[^{]*\{[^}]*animation:\s*none")


OLD_RENDERER = ROOT / "tests" / "fixtures" / "renderer-1.5.0" / "dashboard-render.js"


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class OldRendererOnNewJson(unittest.TestCase):
    """A-K11, the other direction (review T1): the renderer of 1.5.0 (frozen copy) shows the current fixtures, which carry the new keys of
    the contract (`state`, `who`, `activePercent`, `result`, `prototypes`, `expertQuestions.items`, ...), without an exception: unknown keys are ignored."""

    def test_old_renderer_renders_every_current_fixture(self) -> None:
        self.assertTrue(OLD_RENDERER.is_file())
        fixtures = fixture_files()
        # a status with the reason of 161-E (`unbalanced-bracket`) that the frozen renderer does not know: it must render, not throw
        warned = json.loads(json.dumps(next(iter(fixtures.values()))))
        warned["run"] = {**(warned.get("run") or {}), "parseWarnings": [{"time": "10:03", "segment": "raport", "fragment": "F1.1 (a; b, F1.2", "reason": "unbalanced-bracket"}]}
        fixtures["with-unbalanced-bracket-warning"] = warned
        # 170-A: `delivery.commits` / `commitsMore` of an unpushed branch: the frozen renderer keeps its plain badge and ignores the list
        listed = json.loads(json.dumps(next(iter(fixtures.values()))))
        listed["git"] = {"branch": "feat/x", "head": "abcdef1234", "headFull": "abcdef1234", "branchUrl": None, "branchPushed": True, "remote": None}
        listed["delivery"] = {**(listed.get("delivery") or {}), "mode": "pr", "branch": "feat/x", "remote": "origin/feat/x", "ahead": 3, "pushed": True,
                              "commits": [{"sha": "abc1234", "subject": "pierwszy"}], "commitsMore": 2}
        fixtures["with-unpushed-commit-list"] = listed
        # 180-A: the `tests` field and `progress...steps[].tests`: the frozen renderer ignores both and draws no tab
        tested = json.loads(json.dumps(next(iter(fixtures.values()))))
        tested["tests"] = {"sections": [{"step": {"id": "F1.1", "title": "x"}, "state": "failed", "attempts": [{"n": 1, "state": "failed", "counts": {"total": 2, "failed": 1}, "groups": []}]}],
                           "failedTotal": 1, "runningTotal": 0}
        for phase in (tested.get("progress") or {}).get("phases", [])[:1]:
            for step in phase.get("steps", [])[:1]:
                step["tests"] = {"state": "failed", "attempt": 1, "counts": {"total": 2, "failed": 1}}
        fixtures["with-tests-field"] = tested
        # CORE-QFILTER K8: `expertQuestions.current` (an object, and null) is an unknown key for the frozen renderer
        for label, cur in (("with-expert-current", {"open": 1, "blocking": 0, "prototypes": [{"slug": "run-alpha", "screens": 1, "accepted": 0, "stale": 0, "threads": {"open": 1}}],
                                                    "items": [{"id": "qn-a", "runSlug": "run-alpha", "text": "t", "blocking": False, "addressees": [], "state": "open", "at": "2026-01-11T09:00:00Z", "url": "/_experts/run-alpha"}]}),
                           ("with-expert-current-null", None)):
            withq = json.loads(json.dumps(fixtures["questions"]))
            withq["expertQuestions"]["current"] = cur
            fixtures[label] = withq
        head = HARNESS[:HARNESS.index("  const F = input.fixtures")]
        tail = """
  const res = {};
  for (const [name, d] of Object.entries(input.fixtures)) {
    try { const m = mount(d, { now: input.now[name] }); res[name] = { ok: true, acts: m.root.querySelectorAll('#acts .act').length,
      stages: m.root.querySelectorAll('#acts .stage').length, newTab: !!m.root.querySelector('#tab-questions'), parseWarn: !!m.root.querySelector('#parseWarn'),
      commitsBtn: !!m.root.querySelector('#commitsBtn'), testsTab: !!m.root.querySelector('#tab-tests'), stbar: m.root.querySelectorAll('.stbar').length, ahead: (m.root.querySelector('#meta .ahead-chip') || {}).textContent || null }; m.board.destroy(); }
    catch (e) { res[name] = { error: String(e && e.stack || e) }; }
  }
  process.stdout.write(JSON.stringify({ res, violations }));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""
        with tempfile.TemporaryDirectory() as tmp:
            harness, data = Path(tmp) / "old.cjs", Path(tmp) / "input.json"
            harness.write_text(head + tail, encoding="utf-8")
            data.write_text(json.dumps({"fixtures": fixtures, "now": {n: ms(d["generatedAt"]) for n, d in fixtures.items()}}), encoding="utf-8")
            proc = subprocess.run([NODE, str(harness), str(OLD_RENDERER), str(data)], capture_output=True, text=True, timeout=60, cwd=tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        out = json.loads(proc.stdout)
        self.assertNotIn("fatal", out)
        self.assertEqual(out["violations"], [])
        self.assertEqual(set(out["res"]), set(fixtures))
        for name, got in out["res"].items():
            with self.subTest(fixture=name):
                self.assertTrue(got.get("ok"), got)
                self.assertGreaterEqual(got["acts"], 1)
                self.assertFalse(got["newTab"] or got["parseWarn"] or got["commitsBtn"] or got["testsTab"] or got["stbar"], "the old renderer knows no new elements")
        self.assertEqual(out["res"]["with-unpushed-commit-list"]["ahead"], "3 niewypchnięte", "the old badge is unchanged")


if __name__ == "__main__":
    unittest.main()
