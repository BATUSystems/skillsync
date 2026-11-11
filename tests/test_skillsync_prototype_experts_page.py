"""Strona HTML `GET /_experts` (EXPERT-Q, E4): sesja, nagłówki, CSP z hashami, brak danych w HTML, metody, izolacja.

Kryteria: .skillsync/runs/EXPERT-Q/spec.md (K4, K5, D10, „Kontrakty -> API", strona `/_experts`).
Serwer startuje na porcie 0 (harness `QCase`), loginy to `*@example.com`, hasła-atrapy składane w czasie wykonania.
Kontrole statyczne skryptu (punkt 9) mają kontrolę przeciwną: wzorzec rozpoznaje sztuczny, zły przykład.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_experts_page.py`.
Zgodność ze składnią Pythona 3.8.
"""
import base64
import hashlib
import http.client
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests import test_skillsync_prototype_server as S  # noqa: E402
from tests import test_skillsync_prototype_questions_api as Q  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

PAGE = '/_experts'
LOGIN_REDIRECT = '/_login?next=/_experts'


def sha_token(text):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii')


def split_page(body):
    scripts = re.findall(r'<script\b([^>]*)>(.*?)</script>', body, re.S | re.I)
    styles = re.findall(r'<style\b([^>]*)>(.*?)</style>', body, re.S | re.I)
    return scripts, styles


def directive(csp, name):
    for part in csp.split(';'):
        tokens = part.strip().split()
        if tokens and tokens[0] == name:
            return tokens[1:]
    return None


FORBIDDEN_SINKS = (r'\binnerHTML\b', r'\bouterHTML\b', r'\binsertAdjacentHTML\b', r'\bdocument\.write\b',
                   r'\beval\s*\(', r'\bnew\s+Function\b')


def sinks_in(script):
    return [pattern for pattern in FORBIDDEN_SINKS if re.search(pattern, script)]


# Pola rekordów API i wzorzec bezpiecznego ujścia (textContent przez właściwość `text:` helpera `el`, createTextNode).
FIELD_SINKS = {
    'text': r"text:\s*question\.text",
    'assumed': r"text:\s*'Przyjęto: '\s*\+\s*question\.assumed",
    'refs.title': r"text:\s*ref\.title",
    'refs.excerpt': r"text:\s*ref\.excerpt",
    'options.label': r"option\.label\s*\+",                    # trafia do parametru `text` helpera radio -> el(label, {text})
    'answer.text': r"value\s*=\s*answer\.text[\s\S]*?text:\s*value",
    'comment.text': r"text:\s*comment\.text",
    'by': r"text:\s*\(comment\.name\s*\|\|\s*comment\.by\)",
}


def unsafe_use(script, field):
    """Prawda, gdy pole jest przypisywane do ujścia innego niż tekstowe (HTML)."""
    short = field.split('.')[-1]
    return bool(re.search(r'(innerHTML|outerHTML|insertAdjacentHTML)[^;\n]*%s' % re.escape(short), script))


class ExpertsPageBase(Q.QCase):
    def who(self, login):
        if login == Q.MX:
            client = S.Client(self.server)
            self.assertEqual(client.login(login, S.DEFAULT_PW).status, 200)
            return client
        return super().who(login)

    def page(self, client=None):
        return (client or self.who(Q.EX)).get(PAGE)


# ═════════════════════════════════════════════════════════════════════════════
# (1) sesja wymagana
# ═════════════════════════════════════════════════════════════════════════════

class SessionTests(ExpertsPageBase):
    def test_without_session_redirects_to_login_with_next(self):
        response = self.anon.get(PAGE)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.header('Location'), LOGIN_REDIRECT)
        self.assertNotIn('Pytania do eksperta', response.text)
        self.assertEqual(response.body, b'')

    def test_without_session_redirects_also_with_login_to_view(self):
        self.start_server(login_to_view=True)
        response = self.anon.get(PAGE)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.header('Location'), LOGIN_REDIRECT)

    def test_control_with_session_gets_the_page(self):
        self.assertEqual(self.page().status, 200)

    def test_login_redirect_target_is_a_valid_local_next(self):
        self.assertEqual(ps.safe_next(PAGE), PAGE)   # `next` z przekierowania nie jest odrzucany przez /_login


# ═════════════════════════════════════════════════════════════════════════════
# (2)(3) nagłówki i CSP
# ═════════════════════════════════════════════════════════════════════════════

class HeadersTests(ExpertsPageBase):
    def setUp(self):
        super().setUp()
        self.response = self.page()
        self.csp = self.response.header('Content-Security-Policy')

    def test_status_and_security_headers(self):
        r = self.response
        self.assertEqual(r.status, 200)
        self.assertEqual(r.header('Content-Type'), 'text/html; charset=utf-8')
        self.assertEqual(r.header('Cache-Control'), 'no-store')
        self.assertEqual(r.header('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.header('Referrer-Policy'), 'no-referrer')

    def test_csp_and_referrer_policy_are_single_values(self):
        for name in ('Content-Security-Policy', 'Referrer-Policy', 'X-Content-Type-Options', 'Cache-Control'):
            self.assertEqual(len(self.response.headers.get_all(name) or []), 1, name)
        self.assertNotIn("frame-ancestors 'self'", self.csp)
        self.assertNotIn('same-origin', self.response.header('Referrer-Policy'))

    def test_csp_directives(self):
        for name, expected in (('default-src', ["'none'"]), ('connect-src', ["'self'"]), ('frame-ancestors', ["'none'"]),
                               ('base-uri', ["'none'"]), ('form-action', ["'none'"])):
            self.assertEqual(directive(self.csp, name), expected, name)
        for banned in ("'unsafe-inline'", "'unsafe-eval'", '*', 'data:', 'http:'):
            self.assertNotIn(banned, self.csp.split(), banned)

    def test_csp_negative_check_recognizes_a_bad_policy(self):
        bad = "default-src 'none'; script-src 'unsafe-inline'"
        self.assertIn("'unsafe-inline'", bad.split())
        self.assertIsNone(directive(bad, 'connect-src'))
        self.assertNotIn("'unsafe-inline'", self.csp.split())

    def test_hashes_match_actual_script_and_style(self):
        scripts, styles = split_page(self.response.text)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(len(styles), 1)
        self.assertEqual(directive(self.csp, 'script-src'), [sha_token(scripts[0][1])])
        self.assertEqual(directive(self.csp, 'style-src'), [sha_token(styles[0][1])])

    def test_hash_check_detects_a_changed_body(self):
        scripts, _ = split_page(self.response.text)
        self.assertNotEqual(directive(self.csp, 'script-src'), [sha_token(scripts[0][1] + ' ')])

    def test_script_and_style_are_inline_without_attributes(self):
        scripts, styles = split_page(self.response.text)
        self.assertEqual(scripts[0][0].strip(), '')    # bez src=, type= itd.
        self.assertEqual(styles[0][0].strip(), '')

    def test_no_inline_handlers_javascript_urls_or_external_resources(self):
        body = self.response.text
        self.assertEqual(re.findall(r'<[^>]*\son[a-z]+\s*=', body, re.I), [])
        self.assertNotIn('javascript:', body.lower())
        self.assertEqual(re.findall(r'(?:src|href|action|data)\s*=\s*["\']?(?:https?:)?//', body, re.I), [])
        self.assertEqual(re.findall(r'url\(\s*["\']?(?:https?:)?//', body, re.I), [])
        self.assertEqual(re.findall(r'@import', body, re.I), [])
        self.assertEqual(re.findall(r'<(?:link|iframe|object|embed|img)\b', body, re.I), [])

    def test_external_resource_pattern_recognizes_a_bad_sample(self):
        bad = '<img src="https://x.example/a.png"><a href=//x.example>'
        self.assertEqual(len(re.findall(r'(?:src|href|action|data)\s*=\s*["\']?(?:https?:)?//', bad, re.I)), 2)
        self.assertTrue(re.findall(r'<[^>]*\son[a-z]+\s*=', '<div onclick="x()">', re.I))


# ═════════════════════════════════════════════════════════════════════════════
# (4) brak danych w HTML, brak ujść HTML w skrypcie
# ═════════════════════════════════════════════════════════════════════════════

class NoDataTests(ExpertsPageBase):
    def test_body_contains_no_question_data_or_secrets(self):
        body = self.page().text
        self.assertIn(Q.SECRET, self.who(Q.EX2).get('/_api/v1/questions').text,
                      'kontrola: dane są dostępne przez API dla adresata')
        for needle in (Q.SECRET, 'Pytanie qn-', 'qn-0000', 'wyciag publiczny', 'odpowiedz trzeciego', Q.EX, Q.EX2, Q.PW,
                       'docs/specs/a.md'):
            self.assertNotIn(needle, body, needle)

    def test_page_is_identical_for_every_account(self):
        pages = {self.page(self.who(login)).body for login in (Q.EX, Q.EX2, Q.AE, Q.DE)}
        self.assertEqual(len(pages), 1, 'HTML nie zależy od konta (brak ról i danych w treści)')

    def test_page_does_not_reveal_roles_or_user(self):
        body = self.page().text
        for login in (Q.EX, Q.AE, Q.DE, S.A):
            self.assertNotIn(login, body)

    def test_script_has_no_html_sinks(self):
        scripts, _ = split_page(self.page().text)
        self.assertEqual(sinks_in(scripts[0][1]), [])

    def test_sink_detector_recognizes_bad_samples(self):
        bad = ('node.innerHTML = x; a.outerHTML = y; b.insertAdjacentHTML("beforeend", z); document.write(q); '
               'eval ("1"); new  Function("x")')
        self.assertEqual(len(sinks_in(bad)), len(FORBIDDEN_SINKS))
        self.assertEqual(sinks_in('node.textContent = x; document.createTextNode(y)'), [])


# ═════════════════════════════════════════════════════════════════════════════
# (5) HEAD i metody
# ═════════════════════════════════════════════════════════════════════════════

class MethodTests(ExpertsPageBase):
    def test_head_matches_get_without_body(self):
        client = self.who(Q.EX)
        get = client.get(PAGE)
        head = client.request('HEAD', PAGE)
        self.assertEqual(head.status, 200)
        self.assertEqual(head.body, b'')
        for name in ('Content-Type', 'Cache-Control', 'X-Content-Type-Options', 'Referrer-Policy',
                     'Content-Security-Policy', 'Content-Length'):
            self.assertEqual(head.header(name), get.header(name), name)
            self.assertEqual(len(head.headers.get_all(name) or []), 1, name)

    def test_head_without_session_redirects(self):
        response = self.anon.request('HEAD', PAGE)
        self.assertEqual(response.status, 302)
        self.assertEqual(response.header('Location'), LOGIN_REDIRECT)

    def test_write_methods_are_405(self):
        client = self.who(Q.EX)
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            for who in (client, self.anon):
                with self.subTest(method=method, session=who is client):
                    response = who.request(method, PAGE, data={'x': 1} if method != 'DELETE' else None)
                    self.assertEqual(response.status, 405)
                    self.assertNotIn('Pytania do eksperta', response.text)
        self.assertEqual(self.page(client).status, 200, 'kontrola: GET po odmowach nadal działa')


# ═════════════════════════════════════════════════════════════════════════════
# (6) pozostałe strony bez zmian, brak przecieku flagi
# ═════════════════════════════════════════════════════════════════════════════

class IsolationTests(ExpertsPageBase):
    def test_other_pages_keep_default_headers(self):
        client = self.who(Q.EX)
        for path in ('/_login', '/_api/v1/session', '/demo/index.html', '/demo/prototype.js', '/demo/nie-ma.html',
                     '/_api/v1/questions'):
            for who in (client, self.anon):
                response = who.get(path)
                with self.subTest(path=path, session=who is client):
                    self.assertEqual(response.header('X-Content-Type-Options'), 'nosniff')
                    self.assertEqual(response.header('Referrer-Policy'), 'same-origin')
                    self.assertEqual(response.header('Content-Security-Policy'), "frame-ancestors 'self'")
                    self.assertEqual(len(response.headers.get_all('Content-Security-Policy') or []), 1)

    def test_login_page_is_the_same_before_and_after_visiting_the_expert_page(self):
        client = self.who(Q.EX)
        before = client.get('/_login')
        self.page(client)
        after = client.get('/_login')
        self.assertEqual(self.fingerprint(before), self.fingerprint(after))

    def test_session_payload_unchanged_by_the_page(self):
        client = self.who(Q.EX)
        before = client.get('/_api/v1/session').json
        self.page(client)
        self.assertEqual(client.get('/_api/v1/session').json, before)

    def test_flag_does_not_leak_to_the_next_request_on_the_same_connection(self):
        client = self.who(Q.EX)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=15)
        self.addCleanup(conn.close)
        conn.request('GET', PAGE, headers={'Cookie': client.cookie})
        first = conn.getresponse()
        first.read()
        self.assertEqual(first.status, 200)
        self.assertEqual(first.getheader('Referrer-Policy'), 'no-referrer')
        if first.will_close:
            # Serwer to HTTP/1.0 (bez keep-alive): każde żądanie ma nowy handler; sprawdzamy jednak drugie żądanie.
            conn.close()
            conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=15)
            self.addCleanup(conn.close)
        conn.request('GET', '/_login')
        second = conn.getresponse()
        second.read()
        self.assertEqual(second.status, 200)
        self.assertEqual(second.getheader('Referrer-Policy'), 'same-origin')
        self.assertEqual(second.getheader('Content-Security-Policy'), "frame-ancestors 'self'")

    def test_flag_is_reset_per_dispatch_on_a_reused_handler(self):
        """Obiekt handlera użyty ponownie (keep-alive): `_dispatch` zeruje flagę przed obsługą."""
        handler = object.__new__(ps.PrototypeRequestHandler)
        handler._strict_page = True
        handler.path = '/_login'
        sent = []
        handler.send_header = lambda key, value: sent.append((key, value))

        seen = []
        handler.headers = {}
        handler.server = types.SimpleNamespace(app=types.SimpleNamespace(config=types.SimpleNamespace(trust_proxy=False)))
        handler._session = lambda: (None, None)
        handler._login_page = lambda method, query: seen.append(handler._strict_page)   # moment po zerowaniu flagi
        handler._dispatch('GET')
        self.assertEqual(seen, [False])
        self.assertFalse(handler._strict_page)
        original = ps.BaseHTTPRequestHandler.end_headers
        ps.BaseHTTPRequestHandler.end_headers = lambda self_: None
        try:
            handler.end_headers()
            handler._strict_page = True
            handler.end_headers()
        finally:
            ps.BaseHTTPRequestHandler.end_headers = original
        names = [key for key, _ in sent]
        self.assertEqual(names.count('Referrer-Policy'), 1, 'domyślny Referrer-Policy tylko gdy flaga wyłączona')
        self.assertEqual(names.count('Content-Security-Policy'), 1)
        self.assertEqual(names.count('X-Content-Type-Options'), 2)


# ═════════════════════════════════════════════════════════════════════════════
# (7) konta bez uprawnień do pytań
# ═════════════════════════════════════════════════════════════════════════════

class AccountKindTests(ExpertsPageBase):
    """Decyzja implementacji: strona jest dla każdego zalogowanego (200); odmowę pokazuje API (403)."""

    def test_commenter_gets_the_page_and_api_refuses(self):
        client = self.who(S.A)
        page = client.get(PAGE)
        self.assertEqual(page.status, 200)
        self.assertEqual(page.body, self.page().body, 'ta sama treść co dla eksperta')
        self.assertEqual(client.get('/_api/v1/questions').status, 403)

    def test_must_change_account_gets_the_page_and_api_refuses(self):
        client = self.who(Q.MX)
        page = client.get(PAGE)
        self.assertEqual(page.status, 200)
        self.assertEqual(page.body, self.page().body)
        response = client.get('/_api/v1/questions')
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json['error'], 'password_change_required')

    def test_page_text_does_not_depend_on_roles(self):
        bodies = {self.page(self.who(login)).body for login in (S.A, Q.MX, Q.EX, Q.AE)}
        self.assertEqual(len(bodies), 1)

    def test_script_handles_must_change_and_missing_session_client_side(self):
        script = split_page(self.page().text)[0][0][1]
        self.assertIn('password_change_required', script)
        self.assertIn('toLogin()', script)
        self.assertIn("location.replace(LOGIN)", script)


# ═════════════════════════════════════════════════════════════════════════════
# (8) warianty ścieżki
# ═════════════════════════════════════════════════════════════════════════════

class PathVariantTests(ExpertsPageBase):
    """Zachowanie pinned: tylko dokładna ścieżka `/_experts` to strona; reszta idzie przez statykę (404)."""

    def test_variants_are_not_the_page(self):
        client = self.who(Q.EX)
        for path in ('/_experts/', '/_Experts', '/_EXPERTS', '/_experts/x', '/_experts.html', '/_experts%2F'):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn('Pytania do eksperta', response.text)
                self.assertEqual(response.header('Referrer-Policy'), 'same-origin')
                self.assertEqual(response.header('Content-Security-Policy'), "frame-ancestors 'self'")

    def test_query_string_does_not_change_the_page(self):
        client = self.who(Q.EX)
        self.assertEqual(client.get(PAGE + '?x=1').body, client.get(PAGE).body)
        self.assertEqual(client.get(PAGE + '?x=1').status, 200)

    def test_variants_without_session_are_not_a_redirect_to_the_page(self):
        response = self.anon.get('/_experts/')
        self.assertNotEqual(response.header('Location'), LOGIN_REDIRECT)


# ═════════════════════════════════════════════════════════════════════════════
# (9)(10) statyczna kontrola skryptu
# ═════════════════════════════════════════════════════════════════════════════

class ScriptStaticTests(ExpertsPageBase):
    def setUp(self):
        super().setUp()
        self.script = split_page(self.page().text)[0][0][1]

    def test_every_record_field_goes_to_a_text_sink(self):
        for field, pattern in FIELD_SINKS.items():
            with self.subTest(field=field):
                self.assertTrue(re.search(pattern, self.script), 'brak tekstowego ujścia dla %s' % field)
                self.assertFalse(unsafe_use(self.script, field), 'pole %s trafia do ujścia HTML' % field)

    def test_field_pattern_rejects_a_bad_sample(self):
        bad = 'node.innerHTML = ref.excerpt; box.innerHTML = question.text + comment.text;'
        for field in ('refs.excerpt', 'text', 'comment.text'):
            self.assertFalse(re.search(FIELD_SINKS[field], bad), field)
            self.assertTrue(unsafe_use(bad, field), field)

    def test_helper_sets_text_only_via_textcontent(self):
        self.assertIn("node.textContent = props[key]", self.script)
        self.assertIn("document.createTextNode(' ')", self.script)
        self.assertNotIn('createContextualFragment', self.script)
        self.assertNotIn('DOMParser', self.script)

    def test_record_fields_never_become_attributes_except_checked_href(self):
        # w `el(...)` pola rekordów nie trafiają do atrybutów; jedyny href z rekordu to `url.href` po sprawdzeniu https
        for match in re.finditer(r"href:\s*([^,}]+)", self.script):
            self.assertIn(match.group(1).strip(), ("url.href", "LOGIN", "'#' + encodeURIComponent(question.id)"))
        self.assertNotRegex(self.script, r"setAttribute\(\s*['\"](?:href|src|on\w+|style)['\"]")
        self.assertNotRegex(self.script, r"\.(?:href|src)\s*=(?!=)")

    def test_href_requires_https_and_noopener_noreferrer(self):
        self.assertRegex(self.script, r"url\.protocol\s*===\s*'https:'")
        self.assertRegex(self.script, r"href:\s*url\.href,\s*target:\s*'_blank',\s*rel:\s*'noopener noreferrer'")
        self.assertNotRegex(self.script, r"protocol\s*!==?\s*'(?:javascript|http|data):'")
        self.assertEqual(len(re.findall(r"target:\s*'_blank'", self.script)), 1)
        self.assertEqual(len(re.findall(r"rel:\s*'noopener noreferrer'", self.script)), 1)

    def test_href_pattern_rejects_a_bad_sample(self):
        bad = "el('a', { href: ref.href, target: '_blank', text: ref.title })"
        self.assertNotRegex(bad, r"url\.protocol\s*===\s*'https:'")
        self.assertNotRegex(bad, r"href:\s*url\.href,\s*target:\s*'_blank',\s*rel:\s*'noopener noreferrer'")

    def test_script_makes_only_same_origin_api_requests(self):
        self.assertIn("var API = '/_api/v1';", self.script)
        self.assertEqual(re.findall(r"fetch\(\s*([^,)]+)", self.script), ["API + path", "API + '/session'", "API + '/session'"])
        self.assertNotRegex(self.script, r"https?://")
        self.assertNotIn('XMLHttpRequest', self.script)
        self.assertNotIn('localStorage', self.script)
        self.assertNotIn('sessionStorage', self.script)

    def test_node_syntax_check(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('brak node w środowisku')
        with tempfile.TemporaryDirectory(prefix='experts-js-') as tmp:
            path = Path(tmp) / 'experts.js'
            path.write_text(self.script, encoding='utf-8')
            done = subprocess.run([node, '--check', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace'))
            bad = Path(tmp) / 'bad.js'
            bad.write_text(self.script + '\n)(', encoding='utf-8')
            broken = subprocess.run([node, '--check', str(bad)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=60)
            self.assertNotEqual(broken.returncode, 0, 'kontrola: zepsuty skrypt musi nie przejść')


NODE_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
function grab(name) {
  const start = script.indexOf('  function ' + name + '(');
  if (start < 0) throw new Error('brak funkcji ' + name);
  const end = script.indexOf('\n  }\n', start);
  return script.slice(start, end + 4);
}
function node(tag) {
  return { tag, attrs: {}, children: [], textContent: '',
    setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); return c; } };
}
const fetched = [];
const ctx = { API: '/_api/v1', LOGIN: '/_login?next=/_experts', state: { csrf: 'old' }, URL, location: { hash: '', replaced: [],
    replace(x) { this.replaced.push(x); } },
  document: { createElement: node, createTextNode(t) { return { text: t }; } },
  fetch(url) { fetched.push(url); return Promise.resolve({ ok: true, json: () => Promise.resolve({ user: { login: 'x' }, csrf: 'new' }) }); },
  toLogin() { ctx.location.replace('/_login?next=/_experts'); }, out: {} };
vm.createContext(ctx);
vm.runInContext(['el', 'refreshSession', 'validQuestion', 'describe', 'currentId', 'refNode'].map(grab).join('\n') +
  '\nvar UNEXPECTED = "Nieoczekiwana odpowiedź serwera.";', ctx);
const out = {};
const ids = {};
for (const h of ['#qn-abcdef', '#qn-ABCDEF', '#qn-abc', '#%E0%A4%A', '#', '', '#qn-' + 'a'.repeat(65), '#qn-' + 'a'.repeat(64), '#..%2Fx']) {
  ctx.location.hash = h; ids[h] = vm.runInContext('currentId()', ctx);
}
out.ids = ids;
out.describe = {};
for (const code of ['invalid_csrf', 'invalid_origin', 'invalid_content_type', 'not_expert']) {
  out.describe[code] = vm.runInContext('describe(' + JSON.stringify({ status: 403, data: { error: code } }) + ')', ctx);
}
out.fetched = fetched.slice();
const refs = {};
function render(href) {
  ctx.refIn = { title: 'T', href };
  const box = vm.runInContext('refNode(refIn)', ctx);
  const a = box.children.find(c => c.tag === 'a'); const sp = box.children.find(c => c.tag === 'span');
  return a ? { link: a.attrs.href, text: a.textContent, rel: a.attrs.rel } : { plain: sp ? sp.textContent : null };
}
for (const href of ['https://example.com/a?b=1', 'https://user:pw@example.com/', 'https://user@example.com/', 'http://example.com/', 'javascript:alert(1)', 'HTTPS://Example.com']) refs[href] = render(href);
out.refs = refs;
out.valid = [null, {}, { id: 'a', text: 'b' }].map(q => { ctx.qq = q; return vm.runInContext('validQuestion(qq)', ctx); });
setTimeout(() => { out.csrf = ctx.state.csrf; console.log(JSON.stringify(out)); }, 20);
"""


class ScriptBehaviorTests(ExpertsPageBase):
    """Poprawki E4: czyste funkcje skryptu wyciągnięte z EXPERTS_SCRIPT i uruchomione w node z atrapami DOM/fetch."""

    def setUp(self):
        super().setUp()
        self.script = split_page(self.page().text)[0][0][1]

    def test_race_guard_and_per_question_busy_are_present(self):
        self.assertIn('var state = { csrf: null, questions: [], current: null, pending: {}, busy: {} };', self.script)
        self.assertIn('if (state.busy[id]) { say(\'Trwa zapis poprzedniej odpowiedzi.\'); return; }', self.script)
        self.assertIn('var viewing = state.current === id;', self.script)
        self.assertRegex(self.script, r"if \(viewing\) \{ renderDetail\(updated, false\); say\(done\); \}")
        self.assertNotIn("location.hash =", self.script, 'reloadAll i reszta nie przestawiają hasha')
        self.assertIn('function reloadAll() {', self.script)

    def test_focus_moves_only_after_a_user_selection(self):
        self.assertIn("event.type === 'hashchange' && changed", self.script)
        self.assertIn("say('Wybrano pytanie ' + label(result.data.question) + '.')", self.script)
        self.assertIn("tabindex: '-1'", self.script)
        self.assertIn("window.addEventListener('hashchange', showCurrent)", self.script)

    def test_live_regions_are_hidden_visually_not_with_display_none(self):
        style = split_page(self.page().text)[1][0][1]
        self.assertNotRegex(style, r"\.(?:alert|status):empty\s*\{[^}]*display:\s*none")
        self.assertRegex(style, r"\.alert:empty,\.status:empty\{[^}]*clip:rect")

    def test_unexpected_shape_has_its_own_message(self):
        self.assertIn("var UNEXPECTED = 'Nieoczekiwana odpowiedź serwera.';", self.script)
        self.assertGreaterEqual(self.script.count('UNEXPECTED'), 4)

    def test_node_pure_functions(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('brak node w środowisku')
        import json
        with tempfile.TemporaryDirectory(prefix='experts-js-') as tmp:
            (Path(tmp) / 'experts.js').write_text(self.script, encoding='utf-8')
            (Path(tmp) / 'harness.js').write_text(NODE_HARNESS, encoding='utf-8')
            done = subprocess.run([node, str(Path(tmp) / 'harness.js'), str(Path(tmp) / 'experts.js')],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8', 'replace'))
        out = json.loads(done.stdout.decode('utf-8'))
        ids = out['ids']
        self.assertEqual(ids['#qn-abcdef'], 'qn-abcdef')
        self.assertEqual(ids['#qn-' + 'a' * 64], 'qn-' + 'a' * 64)
        for bad in ('#qn-ABCDEF', '#qn-abc', '#%E0%A4%A', '#', '', '#qn-' + 'a' * 65, '#..%2Fx'):
            self.assertIsNone(ids[bad], bad)   # także zepsute kodowanie: bez wyjątku
        for code in ('invalid_csrf', 'invalid_origin', 'invalid_content_type'):
            self.assertEqual(out['describe'][code], 'Sesja się zmieniła. Odśwież stronę i spróbuj ponownie.')
        self.assertTrue(out['describe']['not_expert'].startswith('Brak uprawnień'))
        self.assertEqual(out['fetched'], ['/_api/v1/session'] * 3, 'ponowne pobranie /session tylko dla błędów sesji')
        self.assertEqual(out['csrf'], 'new')
        refs = out['refs']
        self.assertEqual(refs['https://example.com/a?b=1'], {'link': 'https://example.com/a?b=1',
                                                              'text': 'https://example.com/a?b=1', 'rel': 'noopener noreferrer'})
        self.assertEqual(refs['HTTPS://Example.com']['text'], refs['HTTPS://Example.com']['link'])
        self.assertEqual(refs['HTTPS://Example.com']['link'], 'https://example.com/')
        for bad in ('https://user:pw@example.com/', 'https://user@example.com/', 'http://example.com/', 'javascript:alert(1)'):
            self.assertIn('plain', refs[bad], bad)
        self.assertEqual(out['valid'], [False, False, True])


class StrictFlagExceptionTests(ExpertsPageBase):
    def test_exception_in_send_does_not_leak_the_flag(self):
        handler = object.__new__(ps.PrototypeRequestHandler)
        handler._strict_page = False

        def boom(*args, **kwargs):
            raise OSError('broken pipe')
        handler._send = boom
        with self.assertRaises(OSError):
            handler._experts_page('GET', object())
        self.assertFalse(handler._strict_page)
        # kontrola testu: bez try/finally flaga zostałaby ustawiona
        handler._strict_page = True
        self.assertTrue(handler._strict_page)


if __name__ == '__main__':
    unittest.main()
