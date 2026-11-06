"""Wyrocznia narzędzi i szablonów skilla `prototype` (pliki z biblioteki wskazanej przez SKILLSYNC_TEST_LIBRARY).

Trzy obszary, wszystkie czarną skrzynką (bez czytania kodu silnika i arkuszy):
- `scripts/check_tokens.py` jako program z linii poleceń: kody wyjścia i komunikaty na fiksturach z
  `tests/fixtures/prototype/check-tokens/` oraz na kopiach plików biblioteki w katalogu tymczasowym;
- szablony `assets/repo/index.html` i `assets/template.html`: placeholdery, struktura ekranów, blokada motywu,
  sprite z symbolami `ic-*`, parsowanie przez `html.parser`;
- tematy dokumentów: znaczniki z tabel `docs/prototype-contract/tematy-dokumentow.md` muszą występować w dokumentach skilla.

Każdy test negatywny ma przypadek kontrolny (ten sam przebieg na danych poprawnych kończy się sukcesem), a funkcje
sprawdzające szablony i dokumenty są dodatkowo ćwiczone na celowo zepsutych danych.
"""
import html.parser
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from glob import glob
from pathlib import Path

from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
needs_library = unittest.skipUnless(LIB, 'ustaw SKILLSYNC_TEST_LIBRARY na bibliotekę z szablonami prototypu')

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / 'tests' / 'fixtures' / 'prototype' / 'check-tokens'


def skill_dir():
    return Path(LIB) / 'skills' / 'prototype'


def read(path):
    return Path(path).read_text(encoding='utf-8')


# ---------------------------------------------------------------------------------------------------------------
# check_tokens.py
# ---------------------------------------------------------------------------------------------------------------

class Result:
    def __init__(self, proc):
        self.rc = proc.returncode
        self.out = proc.stdout
        self.err = proc.stderr
        self.all = proc.stdout + proc.stderr


def run_tool(*args, cwd=None):
    script = skill_dir() / 'scripts' / 'check_tokens.py'
    proc = subprocess.run([sys.executable, str(script)] + [str(a) for a in args], cwd=cwd, capture_output=True,
                          text=True, encoding='utf-8', timeout=120)
    return Result(proc)


@needs_library
class CheckTokensTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='proto-lib-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.assets = skill_dir() / 'assets'

    def copy_assets(self):
        """Kopia assets/ do mutacji (oryginał w bibliotece pozostaje nietknięty)."""
        dst = self.tmp / 'assets'
        shutil.copytree(self.assets, dst)
        return dst

    # --- przypadki dodatnie ------------------------------------------------------------------------------------

    def test_real_library_passes(self):
        r = run_tool()
        self.assertEqual(r.rc, 0, r.all)
        self.assertIn('check_tokens: OK', r.out)
        self.assertEqual(r.err, '')

    def test_explicit_skill_dir_equals_default(self):
        r = run_tool(skill_dir())
        self.assertEqual(r.rc, 0, r.all)
        self.assertEqual(r.out, run_tool().out)

    def test_cwd_does_not_matter(self):
        r = run_tool(cwd=self.tmp)
        self.assertEqual(r.rc, 0, r.all)

    def test_help(self):
        r = run_tool('--help')
        self.assertEqual(r.rc, 0, r.all)
        self.assertIn('usage', r.out.lower())
        for flag in ('--assets', '--theme', '--required', '--comments', '--export', '--fixtures', '--print-used'):
            self.assertIn(flag, r.out)

    def test_print_used_equals_required_list(self):
        r = run_tool('--print-used')
        self.assertEqual(r.rc, 0, r.all)
        used = r.out.split()
        self.assertEqual(used, sorted(used))
        self.assertTrue(all(t.startswith('--') for t in used))
        required = [ln.strip() for ln in read(self.assets / 'theme' / 'required-tokens.txt').splitlines() if ln.strip()]
        self.assertEqual(set(used), set(required))

    def fixtures_root(self, overlay=None):
        """Kopia fikstur biblioteki w katalogu tymczasowym; `overlay` (katalog z check-tokens/) nadpisuje pliki."""
        dst = self.tmp / ('fixtures-' + (overlay.name if overlay else 'lib'))
        shutil.copytree(skill_dir() / 'scripts' / 'fixtures', dst)
        if overlay:
            for src in overlay.rglob('*'):
                if src.is_file():
                    shutil.copyfile(src, dst / src.relative_to(overlay))
        return dst

    def test_copied_fixtures_pass_control(self):
        r = run_tool('--fixtures', self.fixtures_root())
        self.assertEqual(r.rc, 0, r.all)
        self.assertIn('check_tokens: OK', r.out)

    def test_export_option_limits_v1_exports(self):
        r = run_tool('--export', skill_dir() / 'scripts' / 'fixtures' / 'v1' / 'export-comments.js')
        self.assertEqual(r.rc, 0, r.all)
        self.assertIn('eksporty v1: 1', r.out)

    # --- fikstury foldu (nadpisane pliki oczekiwań; reszta z biblioteki) ---------------------------------------

    def test_v2_fold_mismatch_fails_with_thread_and_file(self):
        r = run_tool('--fixtures', self.fixtures_root(FIX / 'bad-v2-fold'))
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('comments-v2.expected.json', r.err)
        self.assertIn('th-a', r.err)
        self.assertIn('status', r.err)
        self.assertNotIn('check_tokens: OK', r.out)

    def flipped_view_fixtures(self, thread_id):
        """Kopia fikstur, w której oczekiwany `view` wątku jest odwrócony (generowane w czasie testu)."""
        import json
        shutil.rmtree(self.tmp / 'fixtures-lib', ignore_errors=True)  # świeża kopia na każdy wątek
        root = self.fixtures_root()
        path = root / 'v2' / 'comments-v2.expected.json'
        doc = json.loads(read(path))
        th = next(t for t in doc['threads'] if t['id'] == thread_id)
        th['view'] = 'desktop' if th['view'] == 'mobile' else 'mobile'
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        return root

    def test_v2_view_mismatch_fails_with_thread_and_field(self):
        """Reguła D4: oczekiwanie z odwróconym `view` (mobile i desktop) kończy się rc 1."""
        for tid in ('th-m1', 'th-m2'):
            with self.subTest(thread=tid):
                r = run_tool('--fixtures', self.flipped_view_fixtures(tid))
                self.assertEqual(r.rc, 1, r.all)
                self.assertIn('comments-v2.expected.json', r.err)
                self.assertIn(tid, r.err)
                self.assertIn('view', r.err)
                self.assertNotIn('check_tokens: OK', r.out)

    def test_v2_view_rule_mutant_fails_original_passes(self):
        """Mutacja reguły widoku w kopii skryptu daje FAIL na fiksturze v2; oryginał daje PASS (kontrola asercji)."""
        dst = self.tmp / 'mutant-skill' / 'scripts'
        shutil.copytree(skill_dir() / 'scripts', dst)
        orig = "'view': 'mobile' if p.get('view') == 'mobile' else 'desktop'}"
        mutated = "'view': 'desktop' if p.get('view') == 'mobile' else 'mobile'}"
        text = read(dst / 'check_tokens.py')
        self.assertEqual(text.count(orig), 1, 'reguła widoku w check_tokens.py zmieniła kształt - zaktualizuj mutację')
        (dst / 'check_tokens.py').write_text(text.replace(orig, mutated), encoding='utf-8')
        fixtures = self.fixtures_root()
        control = run_tool('--fixtures', fixtures)
        self.assertEqual(control.rc, 0, control.all)
        proc = subprocess.run([sys.executable, str(dst / 'check_tokens.py'), str(skill_dir()), '--fixtures', str(fixtures)],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('view', proc.stderr)
        self.assertIn('th-m1', proc.stderr)

    def test_v1_fold_mismatch_fails_with_thread_and_file(self):
        r = run_tool('--fixtures', self.fixtures_root(FIX / 'bad-v1-fold'))
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('export-comments.expected.json', r.err)
        self.assertIn('thread-a', r.err)
        self.assertIn('resolved', r.err)

    def test_fold_failures_are_independent_of_each_other(self):
        """Błąd w v2 nie wskazuje v1 i odwrotnie (kontrola, że komunikat dotyczy właściwej fikstury)."""
        self.assertNotIn('export-comments', run_tool('--fixtures', self.fixtures_root(FIX / 'bad-v2-fold')).err)
        self.assertNotIn('comments-v2', run_tool('--fixtures', self.fixtures_root(FIX / 'bad-v1-fold')).err)

    def test_missing_fixture_dir_is_usage_error(self):
        r = run_tool('--fixtures', self.tmp / 'brak')
        self.assertEqual(r.rc, 2, r.all)
        self.assertIn(str(self.tmp / 'brak'), r.err)

    # --- blokada motywu w szablonie ----------------------------------------------------------------------------

    def with_index(self, name):
        assets = self.copy_assets()
        shutil.copyfile(FIX / 'templates' / name, assets / 'repo' / 'index.html')
        return run_tool('--assets', assets / 'repo', '--theme', assets / 'theme' / 'default.css',
                        '--required', assets / 'theme' / 'required-tokens.txt')

    def test_index_with_both_metas_passes_control(self):
        r = self.with_index('index-ok.html')
        self.assertEqual(r.rc, 0, r.all)

    def test_index_without_darkreader_lock_fails(self):
        r = self.with_index('index-no-darkreader.html')
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('darkreader-lock', r.err)
        self.assertNotIn('color-scheme', r.err)

    def test_index_without_color_scheme_fails(self):
        r = self.with_index('index-no-colorscheme.html')
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('color-scheme', r.err)
        self.assertNotIn('darkreader-lock', r.err)

    # --- tokeny motywu -----------------------------------------------------------------------------------------

    def theme_run(self, mutate_theme=None, mutate_required=None):
        assets = self.copy_assets()
        theme = assets / 'theme' / 'default.css'
        required = assets / 'theme' / 'required-tokens.txt'
        if mutate_theme:
            theme.write_text(mutate_theme(read(theme)), encoding='utf-8')
        if mutate_required:
            required.write_text(mutate_required(read(required)), encoding='utf-8')
        return run_tool('--assets', assets / 'repo', '--theme', theme, '--required', required)

    def test_unmodified_theme_copy_passes_control(self):
        self.assertEqual(self.theme_run().rc, 0)

    def test_theme_without_required_token_fails(self):
        def drop(css):
            out, n = re.subn(r'^[ \t]*--accent:[^;]*;[ \t]*\n', '', css, flags=re.M)
            self.assertGreaterEqual(n, 1, 'fikstura: motyw nie definiuje --accent')
            return out
        r = self.theme_run(mutate_theme=drop)
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('--accent', r.err)
        self.assertIn('default.css', r.err)

    def test_token_only_in_dark_block_fails(self):
        marker = ':root[data-theme="dark"] {'

        def add(css):
            self.assertIn(marker, css, 'fikstura: motyw bez bloku ciemnego')
            return css.replace(marker, marker + '\n  --tylko-ciemny: 1px;', 1)
        r = self.theme_run(mutate_theme=add)
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('--tylko-ciemny', r.err)

    def test_dark_blocks_with_different_values_fail(self):
        marker = '@media (prefers-color-scheme: dark)'

        def change(css):
            self.assertIn(marker, css, 'fikstura: motyw bez bloku ciemnego @media')
            head, tail = css.split(marker, 1)
            tail, n = re.subn(r'--card:[^;]*;', '--card: oklch(0.3 0 0);', tail, count=1)
            self.assertEqual(n, 1, 'fikstura: blok @media nie definiuje --card')
            return head + marker + tail
        r = self.theme_run(mutate_theme=change)
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('--card', r.err)
        self.assertIn('różne wartości', r.err)

    def test_required_list_with_unused_token_fails(self):
        r = self.theme_run(mutate_required=lambda txt: txt.rstrip('\n') + '\n--nieuzywany-token\n')
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('--nieuzywany-token', r.err)

    def test_missing_theme_file_fails(self):
        r = run_tool('--theme', self.tmp / 'brak.css')
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('brak.css', r.err)

    # --- szablon komentarzy i argumenty ------------------------------------------------------------------------

    def test_comments_template_not_an_empty_log_fails(self):
        bad = self.tmp / 'comments.json'
        bad.write_text('{}\n', encoding='utf-8')
        r = run_tool('--comments', bad)
        self.assertEqual(r.rc, 1, r.all)
        self.assertIn('comments.json', r.err)

    def test_real_comments_template_passes_control(self):
        r = run_tool('--comments', self.assets / 'repo' / 'comments.json')
        self.assertEqual(r.rc, 0, r.all)

    def test_missing_skill_dir_is_usage_error(self):
        r = run_tool(self.tmp / 'brak-skilla')
        self.assertEqual(r.rc, 2, r.all)
        self.assertIn(str(self.tmp / 'brak-skilla'), r.err)
        self.assertEqual(r.out, '')

    def test_unknown_option_is_usage_error(self):
        r = run_tool('--nie-ma-takiej-opcji')
        self.assertEqual(r.rc, 2, r.all)
        self.assertIn('unrecognized', r.err)


# ---------------------------------------------------------------------------------------------------------------
# Szablony HTML
# ---------------------------------------------------------------------------------------------------------------

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
OPTIONAL_END = {'p', 'li', 'dt', 'dd', 'tr', 'td', 'th', 'thead', 'tbody', 'tfoot', 'option', 'colgroup', 'html', 'head',
                'body'}
PLACEHOLDERS = {'{{TITLE}}', '{{SLUG}}', '{{REQUIREMENTS}}'}
SCREEN_ID = re.compile(r'^s\d+$')
PROTO_ID = re.compile(r'^[a-z0-9][a-z0-9.-]*$')


class Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
        self.elements = []  # (tag, attrs, parent_tags tuple)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self.stack)))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self.stack)))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top == tag:
                    break
                if top not in OPTIONAL_END:
                    self.errors.append('niezamknięty <%s> przed </%s>' % (top, tag))
        else:
            self.errors.append('zbędny </%s>' % tag)

    def close(self):
        super().close()
        for tag in self.stack:
            if tag not in OPTIONAL_END:
                self.errors.append('niezamknięty <%s> na końcu dokumentu' % tag)


def parse(text):
    c = Collector()
    c.feed(text)
    c.close()
    return c


def common_problems(text, c):
    problems = ['parser: ' + e for e in c.errors]
    screens = [(a, p) for t, a, p in c.elements if t == 'section' and 'screen' in (a.get('class') or '').split()]
    if not screens:
        problems.append('brak section.screen')
    ids = [a.get('id') for a, _ in screens]
    for i in ids:
        if not i or not SCREEN_ID.match(i):
            problems.append('ekran bez identyfikatora sN: %r' % i)
    if len(set(ids)) != len(ids):
        problems.append('powtórzony identyfikator ekranu')
    hrefs = [a.get('href') for t, a, _ in c.elements if t == 'a' and (a.get('href') or '').startswith('#s')]
    for h in hrefs:
        if h[1:] not in ids:
            problems.append('odnośnik %s bez ekranu' % h)
    return problems


def index_problems(text):
    c = parse(text)
    problems = common_problems(text, c)
    found = set(re.findall(r'\{\{[A-Z_]+\}\}', text))
    for p in sorted(PLACEHOLDERS - found):
        problems.append('brak placeholdera %s' % p)
    for p in sorted(found - PLACEHOLDERS):
        problems.append('nieznany placeholder %s' % p)
    html_attrs = next((a for t, a, _ in c.elements if t == 'html'), {})
    if html_attrs.get('data-prototype-id') != '{{SLUG}}':
        problems.append('html[data-prototype-id] różne od {{SLUG}}')
    metas = [(a, p) for t, a, p in c.elements if t == 'meta']
    scheme = [a for a, p in metas if a.get('name') == 'color-scheme']
    if not scheme:
        problems.append('brak meta color-scheme')
    elif not {'light', 'dark'} <= set((scheme[0].get('content') or '').split()):
        problems.append('meta color-scheme bez light i dark')
    if not [a for a, p in metas if a.get('name') == 'darkreader-lock']:
        problems.append('brak meta darkreader-lock')
    if [p for a, p in metas if a.get('name') in ('color-scheme', 'darkreader-lock') and 'head' not in p]:
        problems.append('meta blokady motywu poza head')
    examples = [a for t, a, _ in c.elements if t == 'section' and 'data-prototype-example' in a]
    if len(examples) != 1:
        problems.append('oczekiwano dokładnie jednego ekranu z data-prototype-example, jest %d' % len(examples))
    proto_ids = [a['data-proto-id'] for _, a, _ in c.elements if 'data-proto-id' in a]
    if not proto_ids:
        problems.append('ekran przykładowy bez data-proto-id')
    for i in proto_ids:
        if not PROTO_ID.match(i):
            problems.append('data-proto-id w złym formacie: %r' % i)
    if len(set(proto_ids)) != len(proto_ids):
        problems.append('powtórzony data-proto-id')
    in_example = [a for t, a, p in c.elements if 'data-proto-id' in a]
    if not in_example:
        problems.append('brak uchwytów')
    symbols = [a.get('id') for t, a, _ in c.elements if t == 'symbol']
    if not symbols:
        problems.append('brak symboli w sprite')
    for s in symbols:
        if not (s or '').startswith('ic-'):
            problems.append('symbol bez prefiksu ic-: %r' % s)
    if len(set(symbols)) != len(symbols):
        problems.append('powtórzony symbol')
    uses = [(a.get('href') or a.get('xlink:href') or '') for t, a, _ in c.elements if t == 'use']
    if not uses:
        problems.append('brak użyć <use>')
    for u in uses:
        if not u.startswith('#ic-'):
            problems.append('użycie spoza sprite: %r' % u)
        elif u[1:] not in symbols:
            problems.append('użycie %s bez symbolu' % u)
    return problems


def template_problems(text):
    c = parse(text)
    problems = common_problems(text, c)
    if '{{' in text:
        problems.append('placeholder w szablonie jednoplikowym')
    toc = [a['href'] for t, a, _ in c.elements if t == 'a' and (a.get('href') or '').startswith('#s')]
    ids = [a.get('id') for t, a, _ in c.elements if t == 'section' and 'screen' in (a.get('class') or '').split()]
    for i in ids:
        if '#' + i not in toc:
            problems.append('ekran %s bez pozycji w spisie' % i)
    if len(ids) < 2:
        problems.append('szablon pokazuje mniej niż dwa ekrany')
    return problems


def first_with(problems, needle):
    return [p for p in problems if needle in p]


@needs_library
class IndexTemplateTests(unittest.TestCase):
    def setUp(self):
        self.text = read(skill_dir() / 'assets' / 'repo' / 'index.html')

    def test_real_template_has_no_problems(self):
        self.assertEqual(index_problems(self.text), [])

    def test_exact_placeholder_set(self):
        self.assertEqual(set(re.findall(r'\{\{[A-Z_]+\}\}', self.text)), PLACEHOLDERS)

    def test_slug_placeholder_on_html_element(self):
        c = parse(self.text)
        root = next(a for t, a, _ in c.elements if t == 'html')
        self.assertEqual(root.get('data-prototype-id'), '{{SLUG}}')

    def test_example_screen_is_first_screen_with_handles(self):
        c = parse(self.text)
        screens = [a for t, a, _ in c.elements if t == 'section' and 'screen' in (a.get('class') or '').split()]
        self.assertTrue(screens)
        self.assertEqual(screens[0].get('id'), 's1')
        self.assertIn('data-prototype-example', screens[0])
        handles = [a['data-proto-id'] for _, a, _ in c.elements if 'data-proto-id' in a]
        self.assertGreaterEqual(len(handles), 2)

    def test_theme_lock_metas_in_head(self):
        c = parse(self.text)
        names = {a.get('name'): p for t, a, p in c.elements if t == 'meta'}
        self.assertIn('head', names['color-scheme'])
        self.assertIn('head', names['darkreader-lock'])

    def test_sprite_symbols_and_uses_are_consistent(self):
        c = parse(self.text)
        symbols = {a['id'] for t, a, _ in c.elements if t == 'symbol'}
        uses = {a['href'][1:] for t, a, _ in c.elements if t == 'use'}
        sprite = parse(read(skill_dir() / 'assets' / 'icons' / 'sprite.svg'))
        library = {a['id'] for t, a, _ in sprite.elements if t == 'symbol'}
        self.assertTrue(library, 'kontrola: sprite biblioteki ma symbole')
        self.assertEqual(symbols, library, 'szablon zawiera pełny sprite biblioteki (migrate świeżego prototypu nie zmienia ikon)')
        self.assertTrue(all(s.startswith('ic-') for s in symbols))
        self.assertTrue(uses)
        self.assertEqual(uses - symbols, set())

    def test_every_symbol_has_viewbox_and_drawing(self):
        c = parse(self.text)
        symbols = [a for t, a, _ in c.elements if t == 'symbol']
        self.assertTrue(symbols)
        for a in symbols:
            self.assertTrue(a.get('viewbox') or a.get('viewBox'), a.get('id'))
        paintable = {'path', 'circle', 'rect', 'line', 'polyline', 'polygon', 'ellipse'}
        # każdy symbol ma element rysujący: wystarczy, że kształty istnieją między znacznikami symbolu
        for sym in re.findall(r'<symbol\b.*?</symbol>', self.text, flags=re.S):
            self.assertTrue(any('<%s' % p in sym for p in paintable), sym[:60])

    # --- negatywy: ten sam checker na zepsutych kopiach (kontrola, że potrafi paść) ---------------------------

    def test_checker_detects_each_corruption(self):
        t = self.text
        cases = {
            'brak placeholdera TITLE': (t.replace('{{TITLE}}', 'X'), 'brak placeholdera {{TITLE}}'),
            'brak placeholdera SLUG': (t.replace('{{SLUG}}', 'x'), 'brak placeholdera {{SLUG}}'),
            'brak placeholdera REQUIREMENTS': (t.replace('{{REQUIREMENTS}}', 'x'), 'brak placeholdera {{REQUIREMENTS}}'),
            'obcy placeholder': (t.replace('{{TITLE}}', '{{INNY}}', 1), 'nieznany placeholder'),
            'brak darkreader-lock': (re.sub(r'<meta name="darkreader-lock"[^>]*>', '', t), 'darkreader-lock'),
            'brak color-scheme': (re.sub(r'<meta name="color-scheme"[^>]*>', '', t), 'color-scheme'),
            'color-scheme tylko light': (t.replace('content="light dark"', 'content="light"'), 'bez light i dark'),
            'brak ekranu przykładowego': (t.replace('data-prototype-example', ''), 'data-prototype-example'),
            'brak uchwytów': (re.sub(r'\sdata-proto-id="[^"]*"', '', t), 'data-proto-id'),
            'zły format uchwytu': (t.replace('data-proto-id="s1-tytul"', 'data-proto-id="Zły Uchwyt"'), 'złym formacie'),
            'zły id ekranu': (t.replace('id="s1"', 'id="ekran"', 1), 'identyfikatora sN'),
            'użycie bez symbolu': (t.replace('<use href="#ic-home"/>', '<use href="#ic-nie-ma"/>', 1), 'bez symbolu'),
            'brak slugu na html': (t.replace('data-prototype-id="{{SLUG}}"', ''), 'html[data-prototype-id]'),
            'niezamknięty div': (t.replace('</main>', '', 1), 'parser'),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(text, t, 'mutacja nic nie zmieniła')
                self.assertTrue(first_with(index_problems(text), needle), (needle, index_problems(text)))

    def test_checker_detects_removed_symbol_definition(self):
        c = parse(self.text)
        first = next(a['id'] for t, a, _ in c.elements if t == 'symbol')
        broken = re.sub(r'<symbol\b[^>]*\bid="%s".*?</symbol>' % re.escape(first), '', self.text, count=1, flags=re.S)
        self.assertNotEqual(broken, self.text)
        self.assertTrue(first_with(index_problems(broken), 'bez symbolu'))


@needs_library
class ArtifactTemplateTests(unittest.TestCase):
    def setUp(self):
        self.text = read(skill_dir() / 'assets' / 'template.html')

    def test_real_template_has_no_problems(self):
        self.assertEqual(template_problems(self.text), [])

    def test_screens_follow_sequence_and_match_toc(self):
        c = parse(self.text)
        ids = [a['id'] for t, a, _ in c.elements if t == 'section' and 'screen' in (a.get('class') or '').split()]
        self.assertEqual(ids, ['s%d' % (i + 1) for i in range(len(ids))])
        toc = [a['href'][1:] for t, a, _ in c.elements if t == 'a' and (a.get('href') or '').startswith('#s')]
        self.assertEqual(toc, ids)

    def test_has_no_placeholders(self):
        self.assertNotIn('{{', self.text)

    def test_checker_detects_each_corruption(self):
        t = self.text
        cases = {
            'obcy placeholder': (t.replace('Makieta przepływu', '{{TITLE}}', 1), 'placeholder'),
            'ekran bez spisu': (t.replace('<a href="#s2">', '<a href="#s9">', 1), 'bez ekranu'),
            'zły id ekranu': (t.replace('id="s1"', 'id="start"', 1), 'identyfikatora sN'),
            'powtórzony id': (t.replace('id="s2"', 'id="s1"', 1), 'powtórzony'),
        }
        for name, (text, needle) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(text, t, 'mutacja nic nie zmieniła')
                self.assertTrue(first_with(template_problems(text), needle), (needle, template_problems(text)))


# ---------------------------------------------------------------------------------------------------------------
# Tematy dokumentów
# ---------------------------------------------------------------------------------------------------------------

def find_topics_file():
    override = os.environ.get('SKILLSYNC_TEST_DOC_TOPICS')
    if override:
        return Path(override)
    hits = sorted(glob(str(REPO / 'docs' / 'prototype-contract' / 'tematy-dokumentow.md')))
    return Path(hits[0]) if hits else None


TOPICS_FILE = find_topics_file()
needs_topics = unittest.skipUnless(LIB and TOPICS_FILE and TOPICS_FILE.is_file(),
                                   'brak SKILLSYNC_TEST_LIBRARY albo pliku docs/prototype-contract/tematy-dokumentow.md')


def parse_topics(text):
    """{ścieżka dokumentu względem biblioteki: [(temat, [alternatywy rozdzielone „albo”, ...])]}.

    Wiersz tabeli ma dwie kolumny: temat i znaczniki. Znaczniki w pojedynczych odwrotnych apostrofach; kilka
    znaczników rozdzielonych przecinkiem są wymagane wszystkie, a grupa połączona słowem „albo” jest spełniona
    przy obecności któregokolwiek z jej członków.
    """
    docs = {}
    current = None
    for line in text.splitlines():
        head = re.match(r'^##\s+`([^`]+)`', line)
        if head:
            current = head.group(1)
            docs[current] = []
            continue
        if line.startswith('#'):
            current = None
            continue
        if current is None or not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 2 or cells[0] in ('Temat',) or set(cells[0]) <= set('-: '):
            continue
        groups = []
        for chunk in cells[1].split(','):
            alts = [m for m in re.findall(r'`([^`]+)`', chunk)]
            if alts:
                groups.append(alts)
        docs[current].append((cells[0], groups))
    return docs


def marker_present(doc_text, marker):
    pattern = r'(?<![\w-])' + re.escape(marker) + r'(?![\w-])'
    return re.search(pattern, doc_text) is not None


def missing_topics(doc_text, topics):
    missing = []
    for topic, groups in topics:
        for alts in groups:
            if not any(marker_present(doc_text, m) for m in alts):
                missing.append((topic, ' albo '.join(alts)))
    return missing


class TopicParserTests(unittest.TestCase):
    """Parser tabeli i sprawdzanie obecności na danych wbudowanych (nie wymaga biblioteki)."""

    TABLE = (
        '# Naglowek\n\n## `docs/a.md` (opis)\n\n| Temat | Znaczniki |\n|---|---|\n'
        '| Pierwszy | `alfa`, `beta-gamma` |\n| Drugi | `x1` albo `x2` |\n\n## `docs/b.md`\n\n| Temat | Znaczniki |\n|---|---|\n'
        '| Trzeci | `{{P}}` |\n'
    )

    def test_parse(self):
        docs = parse_topics(self.TABLE)
        self.assertEqual(list(docs), ['docs/a.md', 'docs/b.md'])
        self.assertEqual(docs['docs/a.md'], [('Pierwszy', [['alfa'], ['beta-gamma']]), ('Drugi', [['x1', 'x2']])])
        self.assertEqual(docs['docs/b.md'], [('Trzeci', [['{{P}}']])])

    def test_presence_with_alternatives(self):
        topics = parse_topics(self.TABLE)['docs/a.md']
        self.assertEqual(missing_topics('alfa beta-gamma x2', topics), [])
        self.assertEqual(missing_topics('alfa beta-gamma x1', topics), [])

    def test_absence_is_reported_per_group(self):
        topics = parse_topics(self.TABLE)['docs/a.md']
        self.assertEqual(missing_topics('beta-gamma x1', topics), [('Pierwszy', 'alfa')])
        self.assertEqual(missing_topics('alfa beta-gamma', topics), [('Drugi', 'x1 albo x2')])

    def test_marker_is_a_whole_identifier(self):
        self.assertTrue(marker_present('klasa dt w tabeli', 'dt'))
        self.assertFalse(marker_present('dt-head to inna klasa; width', 'dt'))
        self.assertFalse(marker_present('ostatni', 'tni.'))


@needs_topics
class DocumentTopicsTests(unittest.TestCase):
    def setUp(self):
        self.docs = parse_topics(read(TOPICS_FILE))

    def test_attachment_lists_the_four_documents(self):
        self.assertEqual(
            sorted(self.docs),
            sorted(['skills/prototype/SKILL.md', 'skills/prototype/assets/repo/README.md',
                    'skills/prototype/references/screen-patterns.md', 'skills/prototype/references/style.md']))
        for path, topics in self.docs.items():
            self.assertGreaterEqual(len(topics), 5, path)
            for topic, groups in topics:
                self.assertTrue(groups, '%s / %s: temat bez znaczników' % (path, topic))

    def test_every_document_contains_every_topic(self):
        for path, topics in self.docs.items():
            with self.subTest(path):
                target = Path(LIB) / path
                self.assertTrue(target.is_file(), path)
                self.assertEqual(missing_topics(read(target), topics), [])

    def test_dropping_a_marker_is_detected(self):
        """Kontrola ujemna: usunięcie z kopii dokumentu każdego znacznika po kolei kończy się wykryciem braku."""
        for path, topics in self.docs.items():
            original = read(Path(LIB) / path)
            for topic, groups in topics:
                for alts in groups:
                    broken = original
                    for m in alts:
                        broken = re.sub(r'(?<![\w-])' + re.escape(m) + r'(?![\w-])', '', broken)
                    with self.subTest(path=path, topic=topic, markers=alts):
                        self.assertNotEqual(broken, original, 'znacznik nie występuje w dokumencie')
                        self.assertIn((topic, ' albo '.join(alts)), missing_topics(broken, topics))


if __name__ == '__main__':
    unittest.main()
