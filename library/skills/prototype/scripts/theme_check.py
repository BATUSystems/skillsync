#!/usr/bin/env python3
"""Kontrola motywu prototypów w projekcie, sterowana konfiguracją JSON (Python 3.11+, tylko biblioteka standardowa).

Jedno źródło kontroli dla projektów i warstw: parser CSS jest wspólny (`css_tokens.py`), a to, co sprawdzać, opisuje
konfiguracja (`version: 1`). Każda kontrola włącza się kluczem; brak klucza oznacza, że kontrola nie istnieje (nigdy nie
jest cicho pomijana), a brak danych wejściowych daje kod 2 albo UNVERIFIED, nigdy PASS.

Kontrole (klucze konfiguracji):
- `tokens`: `files` (pliki tokenów projektu), `required` (lista wymaganych tokenów albo `null` = bez kontroli pokrycia;
  klucz jest obowiązkowy, brak listy to błąd), `assets` (katalog zasobów szablonu: zbiór używanych tokenów równy liście).
  Token wymagany musi być w JASNYM bloku (`:root`, `@theme`, `@theme inline`, także w `@layer`); token tylko w bloku
  ciemnym to błąd.
- `dark`: `separate` (bloki ciemne mają te same tokeny i wartości), `mirror` (blok ciemny identyczny z jasnym, brak bloku
  to błąd), `none` (blok ciemny nie może istnieć); ciemne bloki to `.dark`, `[data-theme="dark"]`,
  `:root[data-theme="dark"]` i `@media (prefers-color-scheme: dark)`.
- `forbid`: zakazane konstrukcje w plikach tokenów (`color-scheme`, `prefers-color-scheme`).
- `copies`, `copyDirs`: kopia w projekcie równa źródłu (z projektu albo ze skilla: `skill:<nazwa>/<ścieżka>`); tekstowe po
  normalizacji BOM i końców linii, pozostałe bajt w bajt; `copyDirs` wymaga jawnej listy `include` (wzorce fnmatch).
- `contrast`, `prototype`: zarejestrowane, jeszcze niezaimplementowane (konfiguracja z takim kluczem kończy się kodem 2).

Ścieżki projektu są względne, bez `..`, bez litery dysku, bez separatora na początku, a po `realpath` leżą pod katalogiem
projektu. Katalog skilli (`--skill-root`) jest jawny albo wyznaczany w projekcie (`.claude/skills`, `.agents/skills`,
`.github/skills`; pierwszy zawierający dany skill); skrypt nigdy nie liczy go z własnego położenia ani z katalogów
użytkownika, a dowiązanie skilla do biblioteki jest stanem normalnym.

Użycie:
  theme_check.py --config <plik.json | -> [--project <katalog>] [--skill-root <katalog>] [--only <kontrola>[,...]] [--json]
  theme_check.py --selftest
Wyjście: linie `PASS|FAIL|WARN|UNVERIFIED <kontrola>: <opis> [plik:linia]` na stdout i linia końcowa; `--json` daje ten sam wynik.
Kody wyjścia: 0 = PASS (także same WARN), 1 = FAIL, 2 = błąd wywołania, konfiguracji lub brak pliku źródłowego,
3 = UNVERIFIED bez FAIL (nic nie zmierzono). Skrypt niczego nie zapisuje. Wejście programowe: `run(config, project, skill_root)`.
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Tryb `-I` nie dodaje katalogu skryptu do `sys.path`, więc parser CSS importujemy jawnie (bez `resolve()`).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import css_tokens as ct  # noqa: E402

PASS, FAIL, WARN, UNVERIFIED = 'PASS', 'FAIL', 'WARN', 'UNVERIFIED'
CHECKS = ('tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'contrast', 'prototype')
NOT_IMPLEMENTED = ('contrast', 'prototype')
TOP_KEYS = {'version'} | set(CHECKS)
TOKENS_KEYS = {'files', 'required', 'assets'}
DARK_MODES = ('separate', 'mirror', 'none')
FORBIDDEN = ('color-scheme', 'prefers-color-scheme')
COPY_KEYS = {'source', 'copy', 'mode'}
COPY_DIR_KEYS = {'source', 'copy', 'include'}
COPY_MODES = ('auto', 'text', 'bytes')
TEXT_EXTENSIONS = frozenset({'.css', '.html', '.htm', '.js', '.mjs', '.json', '.md', '.txt', '.svg', '.xml',
                             '.yaml', '.yml', '.csv'})
SKILL_ROOT_CANDIDATES = ('.claude/skills', '.agents/skills', '.github/skills')
SKILL_NAME_RE = re.compile(r'[a-z0-9][a-z0-9-]*')


class ConfigError(Exception):
    """Błąd wywołania, konfiguracji albo brak pliku źródłowego (kod 2). Komunikat nie cytuje treści plików."""


class Result:
    def __init__(self):
        self.findings = []
        self.error = None

    def add(self, status, check, message, file=None, line=None):
        self.findings.append({'status': status, 'check': check, 'message': message, 'file': file, 'line': line})

    def count(self, status):
        return sum(1 for f in self.findings if f['status'] == status)

    @property
    def code(self):
        if self.error is not None:
            return 2
        if self.count(FAIL):
            return 1
        if self.count(UNVERIFIED):
            return 3
        return 0

    @property
    def status(self):
        return {0: PASS, 1: FAIL, 2: 'ERROR', 3: UNVERIFIED}[self.code]

    def lines(self):
        out = []
        for f in self.findings:
            where = ''
            if f['file']:
                where = ' [%s%s]' % (f['file'], ':%d' % f['line'] if f['line'] else '')
            out.append('%s %s: %s%s' % (f['status'], f['check'], f['message'], where))
        out.append('theme_check: %s (kod %d): %d PASS, %d FAIL, %d WARN, %d UNVERIFIED'
                   % (self.status, self.code, self.count(PASS), self.count(FAIL), self.count(WARN),
                      self.count(UNVERIFIED)))
        return out

    def to_dict(self):
        return {'status': self.status, 'code': self.code, 'error': self.error, 'findings': self.findings}


# ---------------------------------------------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------------------------------------------

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _only_keys(obj, allowed, label):
    if not isinstance(obj, dict):
        raise ConfigError('%s: oczekiwano obiektu' % label)
    if set(obj) - allowed:
        raise ConfigError('%s: nieznany klucz' % label)


def _str_list(value, label, allow_empty=True):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError('%s: oczekiwano listy niepustych napisów' % label)
    if not allow_empty and not value:
        raise ConfigError('%s: lista nie może być pusta' % label)


def _str(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError('%s: oczekiwano niepustego napisu' % label)


def validate_config(config):
    """Sprawdza kształt konfiguracji (kod 2 przy błędzie); zwraca listę kluczy włączonych kontroli."""
    _only_keys(config, TOP_KEYS, 'konfiguracja')
    if not _is_int(config.get('version')) or config['version'] != 1:
        raise ConfigError('konfiguracja: version musi wynosić 1')
    tokens = config.get('tokens')
    if 'tokens' in config:
        _only_keys(tokens, TOKENS_KEYS, 'tokens')
        if 'files' not in tokens:
            raise ConfigError('tokens.files: brak klucza')
        _str_list(tokens['files'], 'tokens.files')
        if 'required' not in tokens:
            raise ConfigError('tokens.required: brak klucza (podaj ścieżkę listy albo null, aby wyłączyć pokrycie)')
        if tokens['required'] is not None:
            _str(tokens['required'], 'tokens.required')
        if tokens.get('assets') is not None:
            _str(tokens['assets'], 'tokens.assets')
            if tokens['required'] is None:
                raise ConfigError('tokens.assets wymaga tokens.required')
    if 'dark' in config:
        if config['dark'] not in DARK_MODES:
            raise ConfigError('dark: oczekiwano jednej z wartości %s' % ', '.join(DARK_MODES))
    if 'forbid' in config:
        _str_list(config['forbid'], 'forbid')
        if any(v not in FORBIDDEN for v in config['forbid']):
            raise ConfigError('forbid: nieznana konstrukcja (dozwolone: %s)' % ', '.join(FORBIDDEN))
    if ('dark' in config or 'forbid' in config) and 'tokens' not in config:
        raise ConfigError('dark i forbid czytają tokens.files: dodaj klucz tokens')
    for key, allowed in (('copies', COPY_KEYS), ('copyDirs', COPY_DIR_KEYS)):
        if key not in config:
            continue
        if not isinstance(config[key], list):
            raise ConfigError('%s: oczekiwano listy' % key)
        for n, item in enumerate(config[key]):
            label = '%s[%d]' % (key, n)
            _only_keys(item, allowed, label)
            _str(item.get('source'), label + '.source')
            _str(item.get('copy'), label + '.copy')
            if key == 'copies' and item.get('mode', 'auto') not in COPY_MODES:
                raise ConfigError('%s.mode: oczekiwano jednej z wartości %s' % (label, ', '.join(COPY_MODES)))
            if key == 'copyDirs':
                if 'include' not in item:
                    raise ConfigError('%s.include: brak jawnej listy wzorców' % label)
                _str_list(item['include'], label + '.include', allow_empty=False)
    for key in NOT_IMPLEMENTED:
        if key in config:
            raise ConfigError('kontrola %s: not implemented yet' % key)
    return [k for k in CHECKS if k in config]


# ---------------------------------------------------------------------------------------------------------------
# Ścieżki
# ---------------------------------------------------------------------------------------------------------------

def is_absolute_path(value):
    """Ścieżka bezwzględna w dowolnym systemie: `/x`, `\\x`, `C:x` oraz to, co uznaje system bieżący."""
    return os.path.isabs(value) or value.startswith(('/', '\\')) or re.match(r'[A-Za-z]:', value) is not None


def relative_parts(value, label):
    """Składniki względnej ścieżki zapisanej z `/` albo `\\`; bez `..`, dysku, dwukropka, NUL i separatora na początku."""
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ConfigError('%s: oczekiwano niepustej ścieżki' % label)
    if is_absolute_path(value) or ':' in value:
        raise ConfigError('%s: ścieżka musi być względna (bez dysku i separatora na początku)' % label)
    parts = [p for p in value.replace('\\', '/').split('/') if p not in ('', '.')]
    if not parts:
        raise ConfigError('%s: pusta ścieżka' % label)
    if '..' in parts:
        raise ConfigError('%s: ścieżka nie może zawierać ..' % label)
    return parts


class Context:
    """Korzeń projektu i katalog skilli; rozwiązuje ścieżki projektu i źródła `skill:`."""

    def __init__(self, project, skill_root):
        self.project = Path(project)
        if not self.project.is_dir():
            raise ConfigError('katalog projektu nie istnieje')
        self.real = os.path.realpath(self.project)
        self.skill_root = Path(skill_root) if skill_root is not None else None
        self._skills = {}

    def project_path(self, value, label):
        parts = relative_parts(value, label)
        path = os.path.realpath(os.path.join(self.real, *parts))
        base = os.path.normcase(self.real)
        if os.path.commonpath([os.path.normcase(path), base]) != base:
            raise ConfigError('%s: ścieżka wychodzi poza projekt (także przez dowiązanie)' % label)
        return Path(path), '/'.join(parts)

    def skill_dir(self, name):
        if name in self._skills:
            return self._skills[name]
        found = None
        if self.skill_root is not None:
            candidate = self.skill_root / name
            found = candidate if candidate.is_dir() else None
        else:
            for sub in SKILL_ROOT_CANDIDATES:
                candidate = Path(self.real) / sub / name
                if candidate.is_dir():
                    found = candidate
                    break
        if found is None:
            how = '--skill-root' if self.skill_root is not None else 'katalogach skilli projektu'
            raise ConfigError('skill %s nie został znaleziony w %s' % (name, how))
        self._skills[name] = found
        return found

    def parse_source(self, value, label):
        """Składnia źródła bez dotykania dysku: ('skill', nazwa, składniki) albo ('project', ścieżka, etykieta)."""
        if isinstance(value, str) and value.startswith('skill:'):
            name, sep, rest = value[len('skill:'):].partition('/')
            if not sep or not SKILL_NAME_RE.fullmatch(name):
                raise ConfigError('%s: źródło skill: wymaga nazwy [a-z0-9][a-z0-9-]* i ścieżki' % label)
            return 'skill', name, relative_parts(rest, label)
        path, rel = self.project_path(value, label)
        return 'project', path, rel

    def source_path(self, value, label):
        """(ścieżka, etykieta) źródła: ścieżka projektu albo `skill:<nazwa>/<ścieżka>`."""
        kind, a, b = self.parse_source(value, label)
        if kind == 'skill':
            return self.skill_dir(a).joinpath(*b), 'skill:%s/%s' % (a, '/'.join(b))
        return a, b

    def prevalidate(self, config):
        """Sprawdza składnię wszystkich ścieżek konfiguracji (także kontroli wyłączonych przez --only)."""
        tokens = config.get('tokens')
        if tokens is not None:
            for n, value in enumerate(tokens['files']):
                self.project_path(value, 'tokens.files[%d]' % n)
            for key in ('required', 'assets'):
                if tokens.get(key) is not None:
                    self.parse_source(tokens[key], 'tokens.' + key)
        for key in ('copies', 'copyDirs'):
            for n, item in enumerate(config.get(key, [])):
                self.parse_source(item['source'], '%s[%d].source' % (key, n))
                self.project_path(item['copy'], '%s[%d].copy' % (key, n))


# ---------------------------------------------------------------------------------------------------------------
# Odczyt plików
# ---------------------------------------------------------------------------------------------------------------

def read_text(path):
    return Path(path).read_bytes().decode('utf-8-sig')


def normalized_text(data):
    text = data.decode('utf-8-sig')
    return text.replace('\r\n', '\n').replace('\r', '\n').rstrip('\n') + '\n'


def same_content(source, copy, mode):
    """Czy kopia jest równa źródłu: tekst po normalizacji BOM i końców linii albo bajty (`auto`: po rozszerzeniu)."""
    a, b = Path(source).read_bytes(), Path(copy).read_bytes()
    if mode == 'auto':
        mode = 'text' if Path(source).suffix.lower() in TEXT_EXTENSIONS else 'bytes'
    if mode == 'text':
        try:
            return normalized_text(a) == normalized_text(b)
        except UnicodeDecodeError:
            return a == b
    return a == b


class ThemeFile:
    def __init__(self, rel, text):
        self.rel = rel
        self.text = text
        self.blocks = ct.parse_theme(text)


def load_theme_files(ctx, tokens, res, check):
    """Wczytuje `tokens.files`; brak pliku to FAIL z nazwą pliku (kopia motywu jest wynikiem projektu, nie wejściem)."""
    loaded = []
    for n, value in enumerate(tokens['files']):
        path, rel = ctx.project_path(value, 'tokens.files[%d]' % n)
        if not path.is_file():
            res.add(FAIL, check, 'brak pliku tokenów', rel)
            continue
        try:
            loaded.append(ThemeFile(rel, read_text(path)))
        except (OSError, UnicodeDecodeError):
            res.add(FAIL, check, 'nie można odczytać pliku tokenów jako UTF-8', rel)
    return loaded


# ---------------------------------------------------------------------------------------------------------------
# Kontrole
# ---------------------------------------------------------------------------------------------------------------

def _light_map(files):
    light = {}
    for f in files:
        for block in f.blocks:
            if block.kind == ct.LIGHT:
                for name, (value, line) in block.tokens.items():
                    light[name] = (value, f.rel, line)
    return light


def _dark_blocks(files):
    return [(f, b) for f in files for b in f.blocks if b.kind in (ct.DARK, ct.MEDIA_DARK)]


def check_tokens(ctx, cfg, files, res):
    tokens = cfg['tokens']
    path, rel = ctx.source_path(tokens['required'], 'tokens.required')
    if not path.is_file():
        raise ConfigError('brak listy wymaganych tokenów (tokens.required)')
    try:
        required = ct.read_required(path)
    except (OSError, UnicodeDecodeError):
        raise ConfigError('nie można odczytać listy wymaganych tokenów') from None
    if not required:
        res.add(UNVERIFIED, 'tokens', 'lista wymaganych tokenów jest pusta (nic do sprawdzenia)', rel)
        return
    light = _light_map(files)
    dark = {}
    for f, block in _dark_blocks(files):
        for name, (_value, line) in block.tokens.items():
            dark.setdefault(name, (f.rel, line))
    problems = 0
    if not light:
        res.add(FAIL, 'tokens', 'brak jasnego bloku z tokenami (:root, @theme, @theme inline)')
        problems += 1
    for tok in required:
        if tok in light:
            continue
        problems += 1
        if tok in dark:
            res.add(FAIL, 'tokens', 'token %s zdefiniowany tylko w bloku ciemnym' % tok, *dark[tok])
        else:
            res.add(FAIL, 'tokens', 'brak definicji wymaganego tokenu %s w jasnym bloku' % tok)
    for tok in sorted(set(dark) - set(light) - set(required)):
        problems += 1
        res.add(FAIL, 'tokens', 'token %s zdefiniowany tylko w bloku ciemnym' % tok, *dark[tok])
    note = ''
    if tokens.get('assets'):
        assets, label = ctx.source_path(tokens['assets'], 'tokens.assets')
        if not assets.is_dir():
            raise ConfigError('tokens.assets: katalog zasobów nie istnieje')
        used = ct.used_tokens(assets)
        for tok in sorted(set(required) - used):
            problems += 1
            res.add(FAIL, 'tokens', 'wymagany token %s nie jest używany przez zasoby (%s)' % (tok, label), rel)
        for tok in sorted(used - set(required)):
            problems += 1
            res.add(FAIL, 'tokens', 'zasoby (%s) używają tokenu %s, którego nie ma na liście' % (label, tok), rel)
        note = ', zbiór używanych przez zasoby równy liście'
    if not problems:
        res.add(PASS, 'tokens', '%d wymaganych tokenów w jasnym bloku%s' % (len(required), note))


def check_dark(cfg, files, res):
    mode = cfg['dark']
    darks = _dark_blocks(files)
    if mode == 'none':
        for f, block in darks:
            first = min(line for _v, line in block.tokens.values())
            res.add(FAIL, 'dark', 'blok ciemny %s nie może istnieć (tryb none)' % block.key, f.rel, first)
        if not darks:
            res.add(PASS, 'dark', 'brak bloków ciemnych (tryb none)')
        return
    if mode == 'mirror':
        if not darks:
            res.add(FAIL, 'dark', 'brak bloku ciemnego (tryb mirror wymaga bloku identycznego z jasnym)')
            return
        light = _light_map(files)
        problems = 0
        for f, block in darks:
            for tok in sorted(set(light) - set(block.tokens)):
                problems += 1
                res.add(FAIL, 'dark', 'blok %s nie definiuje tokenu %s (tryb mirror)' % (block.key, tok), f.rel,
                        min(line for _v, line in block.tokens.values()))
            for tok in sorted(set(block.tokens) - set(light)):
                problems += 1
                res.add(FAIL, 'dark', 'blok %s ma token %s, którego nie ma w jasnym (tryb mirror)' % (block.key, tok),
                        f.rel, block.tokens[tok][1])
            for tok in sorted(set(block.tokens) & set(light)):
                if block.tokens[tok][0] != light[tok][0]:
                    problems += 1
                    res.add(FAIL, 'dark', 'token %s ma w bloku %s inną wartość niż w jasnym: "%s" vs "%s" (tryb mirror)'
                            % (tok, block.key, block.tokens[tok][0], light[tok][0]), f.rel, block.tokens[tok][1])
        if not problems:
            res.add(PASS, 'dark', '%d bloków ciemnych identycznych z jasnym (tryb mirror)' % len(darks))
        return
    problems = 0
    for i in range(1, len(darks)):
        (fa, a), (fb, b) = darks[0], darks[i]
        for tok in sorted(set(a.tokens) ^ set(b.tokens)):
            problems += 1
            owner, f = (a, fa) if tok in a.tokens else (b, fb)
            res.add(FAIL, 'dark', 'token %s jest tylko w jednym z bloków ciemnych (%s)' % (tok, owner.key), f.rel,
                    owner.tokens[tok][1])
        for tok in sorted(set(a.tokens) & set(b.tokens)):
            if a.tokens[tok][0] != b.tokens[tok][0]:
                problems += 1
                res.add(FAIL, 'dark', 'token %s ma różne wartości w blokach ciemnych: %s = "%s" vs %s = "%s"'
                        % (tok, a.key, a.tokens[tok][0], b.key, b.tokens[tok][0]), fb.rel, b.tokens[tok][1])
    if not problems:
        res.add(PASS, 'dark', '%d bloków ciemnych, spójne (tryb separate)' % len(darks))


def check_forbid(cfg, files, res):
    problems = 0
    patterns = {'color-scheme': re.compile(r'(?<![\w-])color-scheme\s*:'),
                'prefers-color-scheme': re.compile(r'prefers-color-scheme')}
    for f in files:
        text = ct.strip_css_comments(f.text, keep_lines=True)
        for name in cfg['forbid']:
            for m in patterns[name].finditer(text):
                problems += 1
                res.add(FAIL, 'forbid', 'zakazana konstrukcja %s' % name, f.rel, text.count('\n', 0, m.start()) + 1)
    if not problems:
        res.add(PASS, 'forbid', 'brak zakazanych konstrukcji (%s) w %d plikach' % (', '.join(cfg['forbid']), len(files)))


def _compare_copy(source, copy, label, copy_rel, mode, res, check):
    if not copy.is_file():
        res.add(FAIL, check, 'brak kopii (źródło: %s)' % label, copy_rel)
        return False
    try:
        same = same_content(source, copy, mode)
    except OSError:
        res.add(FAIL, check, 'nie można odczytać kopii', copy_rel)
        return False
    if not same:
        res.add(FAIL, check, 'kopia różni się od źródła %s' % label, copy_rel)
    return same


def check_copies(ctx, cfg, res):
    for n, item in enumerate(cfg['copies']):
        source, label = ctx.source_path(item['source'], 'copies[%d].source' % n)
        copy, copy_rel = ctx.project_path(item['copy'], 'copies[%d].copy' % n)
        if not source.is_file():
            raise ConfigError('copies[%d]: brak pliku źródłowego' % n)
        if _compare_copy(source, copy, label, copy_rel, item.get('mode', 'auto'), res, 'copies'):
            res.add(PASS, 'copies', 'kopia zgodna ze źródłem %s' % label, copy_rel)


def _included(rel, patterns):
    base = rel.rsplit('/', 1)[-1]
    return any(fnmatch.fnmatchcase(rel if '/' in p else base, p) for p in patterns)


def check_copy_dirs(ctx, cfg, res):
    for n, item in enumerate(cfg['copyDirs']):
        source, label = ctx.source_path(item['source'], 'copyDirs[%d].source' % n)
        copy_dir, copy_rel = ctx.project_path(item['copy'], 'copyDirs[%d].copy' % n)
        if not source.is_dir():
            raise ConfigError('copyDirs[%d]: katalog źródłowy nie istnieje' % n)
        names = sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())
        chosen = [r for r in names if _included(r, item['include'])]
        if not chosen:
            res.add(UNVERIFIED, 'copyDirs', 'żaden plik źródła %s nie pasuje do include (nic nie sprawdzono)' % label,
                    copy_rel)
            continue
        ok = 0
        for rel in chosen:
            if _compare_copy(source / rel, copy_dir / rel, '%s/%s' % (label, rel), '%s/%s' % (copy_rel, rel), 'auto',
                             res, 'copyDirs'):
                ok += 1
        if ok == len(chosen):
            res.add(PASS, 'copyDirs', '%d plików zgodnych ze źródłem %s' % (ok, label), copy_rel)


# ---------------------------------------------------------------------------------------------------------------
# Uruchomienie
# ---------------------------------------------------------------------------------------------------------------

def _run(config, project, skill_root, only, res):
    enabled = validate_config(config)
    if only is not None:
        unknown = [name for name in only if name not in CHECKS]
        if unknown:
            raise ConfigError('--only: nieznana kontrola (dozwolone: %s)' % ', '.join(CHECKS))
        missing = [name for name in only if name not in enabled]
        if missing:
            raise ConfigError('--only: kontroli %s nie ma w konfiguracji' % ', '.join(missing))
        enabled = [name for name in enabled if name in only]
    ctx = Context(project, skill_root)
    ctx.prevalidate(config)
    if not enabled:
        res.add(UNVERIFIED, 'config', 'konfiguracja nie zawiera żadnej kontroli (nic nie sprawdzono)')
        return
    files = None
    for name in enabled:
        if name in ('tokens', 'dark', 'forbid'):
            if config['tokens']['required'] is None and name == 'tokens':
                if only is not None:
                    raise ConfigError('tokens.required to null: kontrola pokrycia jest wyłączona')
                continue
            if files is None:
                files = load_theme_files(ctx, config['tokens'], res, name)
            if not files:
                if not config['tokens']['files']:
                    res.add(UNVERIFIED, name, 'tokens.files jest puste (brak plików do sprawdzenia)')
                else:
                    res.add(UNVERIFIED, name, 'żaden plik tokenów nie został wczytany')
                continue
            if name == 'tokens':
                check_tokens(ctx, config, files, res)
            elif name == 'dark':
                check_dark(config, files, res)
            else:
                check_forbid(config, files, res)
        elif name == 'copies':
            check_copies(ctx, config, res)
        elif name == 'copyDirs':
            check_copy_dirs(ctx, config, res)
    if not res.findings:
        res.add(UNVERIFIED, 'config', 'żadna kontrola nic nie zmierzyła')


def run(config, project, skill_root=None, only=None):
    """Wykonuje kontrole z konfiguracji `config` (słownik) w katalogu `project`; zwraca `Result`
    (`findings`, `code`, `error`). Błędy konfiguracji i brak źródeł dają `error` i kod 2, bez wyjątku."""
    res = Result()
    try:
        _run(config, project, skill_root, list(only) if only is not None else None, res)
    except ConfigError as error:
        res.findings = []
        res.error = str(error)
    return res


# ---------------------------------------------------------------------------------------------------------------
# Selftest: każda kontrola potrafi paść
# ---------------------------------------------------------------------------------------------------------------

_LIGHT = ':root { --a: 1; --b: 2; }\n'
_DARK = '[data-theme="dark"] { --a: 3; --b: 4; }\n'


def _case(name, files, config, expect_code, expect_check=None, expect_status=None, only=None):
    return {'name': name, 'files': files, 'config': config, 'code': expect_code, 'check': expect_check,
            'status': expect_status, 'only': only}


def _tokens_cfg(**extra):
    cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt'}}
    cfg.update(extra)
    return cfg


def selftest_cases():
    req = {'req.txt': '--a\n--b\n'}
    ok_theme = dict(req, **{'t.css': _LIGHT + _DARK})
    cases = [
        _case('tokens: komplet', ok_theme, _tokens_cfg(), 0, 'tokens', PASS),
        _case('tokens: brak tokenu', dict(req, **{'t.css': ':root { --a: 1; }\n'}), _tokens_cfg(), 1, 'tokens', FAIL),
        _case('tokens: token tylko ciemny', dict(req, **{'t.css': ':root { --a: 1; }\n.dark { --b: 2; }\n'}),
              _tokens_cfg(), 1, 'tokens', FAIL),
        _case('tokens: drugi :root', dict(req, **{'t.css': ':root { --a: 1; }\n:root { --b: 2; }\n'}), _tokens_cfg(),
              0, 'tokens', PASS),
        _case('tokens: @theme', dict(req, **{'t.css': '@theme { --a: 1; --b: 2; }\n'}), _tokens_cfg(), 0, 'tokens', PASS),
        _case('tokens: brak listy', {'t.css': _LIGHT}, _tokens_cfg(), 2),
        _case('tokens: assets', dict(ok_theme, **{'assets/x.css': 'a { color: var(--a); border: var(--b); }\n'}),
              {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt', 'assets': 'assets'}}, 0, 'tokens', PASS),
        _case('tokens: assets z nadmiarem', dict(ok_theme, **{'assets/x.css': 'a { color: var(--a) var(--b) var(--c); }\n'}),
              {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt', 'assets': 'assets'}}, 1, 'tokens', FAIL),
        _case('dark separate: zgodne', dict(ok_theme, **{'t.css': _LIGHT + _DARK + '.dark { --a: 3; --b: 4; }\n'}),
              _tokens_cfg(dark='separate'), 0, 'dark', PASS, only=['dark']),
        _case('dark separate: różne wartości', dict(ok_theme, **{'t.css': _LIGHT + _DARK + '.dark { --a: 9; --b: 4; }\n'}),
              _tokens_cfg(dark='separate'), 1, 'dark', FAIL, only=['dark']),
        _case('dark mirror: identyczny', dict(ok_theme, **{'t.css': _LIGHT + '.dark { --a: 1; --b: 2; }\n'}),
              _tokens_cfg(dark='mirror'), 0, 'dark', PASS, only=['dark']),
        _case('dark mirror: różny', ok_theme, _tokens_cfg(dark='mirror'), 1, 'dark', FAIL, only=['dark']),
        _case('dark mirror: brak bloku', dict(ok_theme, **{'t.css': _LIGHT}), _tokens_cfg(dark='mirror'), 1, 'dark', FAIL,
              only=['dark']),
        _case('dark none: brak bloku', dict(ok_theme, **{'t.css': _LIGHT}), _tokens_cfg(dark='none'), 0, 'dark', PASS,
              only=['dark']),
        _case('dark none: jest blok', ok_theme, _tokens_cfg(dark='none'), 1, 'dark', FAIL, only=['dark']),
        _case('dark bez klucza', ok_theme, _tokens_cfg(), 2, only=['dark']),
        _case('forbid: czysto', ok_theme, _tokens_cfg(forbid=['color-scheme', 'prefers-color-scheme']), 0, 'forbid', PASS,
              only=['forbid']),
        _case('forbid: color-scheme', dict(ok_theme, **{'t.css': ':root { color-scheme: light; --a: 1; --b: 2; }\n'}),
              _tokens_cfg(forbid=['color-scheme']), 1, 'forbid', FAIL, only=['forbid']),
        _case('forbid: prefers-color-scheme', dict(ok_theme, **{'t.css': '@media (prefers-color-scheme: dark) { :root { --a: 1; } }\n'}),
              _tokens_cfg(forbid=['prefers-color-scheme']), 1, 'forbid', FAIL, only=['forbid']),
        _case('copies: równa po CRLF i BOM', {'src.css': 'a { b: c; }\n', 'dst.css': '﻿a { b: c; }\r\n'},
              {'version': 1, 'copies': [{'source': 'src.css', 'copy': 'dst.css'}]}, 0, 'copies', PASS),
        _case('copies: inna', {'src.css': 'a { b: c; }\n', 'dst.css': 'a { b: d; }\n'},
              {'version': 1, 'copies': [{'source': 'src.css', 'copy': 'dst.css'}]}, 1, 'copies', FAIL),
        _case('copies: brak kopii', {'src.css': 'a\n'}, {'version': 1, 'copies': [{'source': 'src.css', 'copy': 'dst.css'}]},
              1, 'copies', FAIL),
        _case('copies: brak źródła', {}, {'version': 1, 'copies': [{'source': 'src.css', 'copy': 'dst.css'}]}, 2),
        _case('copyDirs: zgodne', {'s/a.bin': b'\x00\x01', 's/LICENSE': 'x\n', 's/SOURCE.txt': 'pomijany\n',
                                    'd/a.bin': b'\x00\x01', 'd/LICENSE': 'x\n'},
              {'version': 1, 'copyDirs': [{'source': 's', 'copy': 'd', 'include': ['*.bin', 'LICENSE']}]}, 0, 'copyDirs',
              PASS),
        _case('copyDirs: inne bajty', {'s/a.bin': b'\x00\x01', 'd/a.bin': b'\x00\x02'},
              {'version': 1, 'copyDirs': [{'source': 's', 'copy': 'd', 'include': ['*.bin']}]}, 1, 'copyDirs', FAIL),
        _case('copyDirs: brak pliku w kopii', {'s/a.bin': b'\x00', 'd/x.txt': 'x'},
              {'version': 1, 'copyDirs': [{'source': 's', 'copy': 'd', 'include': ['*.bin']}]}, 1, 'copyDirs', FAIL),
        _case('copyDirs: include nic nie wybiera', {'s/a.bin': b'\x00'},
              {'version': 1, 'copyDirs': [{'source': 's', 'copy': 'd', 'include': ['*.zzz']}]}, 3, 'copyDirs', UNVERIFIED),
        _case('pusta konfiguracja', {}, {'version': 1}, 3, 'config', UNVERIFIED),
        _case('nieznany klucz', {}, {'version': 1, 'bogus': 1}, 2),
        _case('ścieżka z ..', req, {'version': 1, 'copies': [{'source': '../x', 'copy': 'y'}]}, 2),
    ]
    return cases


def selftest():
    """Uruchamia fikstury w pamięci; zwraca listę niezgodności (pusta = każda kontrola potrafi paść)."""
    problems = []
    cases = selftest_cases()
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='theme-check-selftest-') as tmp:
            for rel, content in case['files'].items():
                path = Path(tmp, *rel.split('/'))
                path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    path.write_bytes(content)
                else:
                    path.write_bytes(content.encode('utf-8'))
            res = run(case['config'], tmp, None, only=case['only'])
        if res.code != case['code']:
            problems.append('%s: kod %d zamiast %d' % (case['name'], res.code, case['code']))
        elif case['check'] and not any(f['check'] == case['check'] and f['status'] == case['status']
                                       for f in res.findings):
            problems.append('%s: brak wyniku %s %s' % (case['name'], case['status'], case['check']))
    return problems, len(cases)


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog='theme_check.py', description='Kontrola motywu prototypów sterowana konfiguracją JSON.')
    ap.add_argument('--config', help='plik konfiguracji JSON albo - (standardowe wejście; wymaga --project)')
    ap.add_argument('--project', help='korzeń ścieżek konfiguracji (domyślnie katalog pliku konfiguracji)')
    ap.add_argument('--skill-root', help='katalog skilli (źródła skill:<nazwa>/...); domyślnie z projektu')
    ap.add_argument('--only', help='lista kontroli po przecinku (%s)' % ', '.join(CHECKS))
    ap.add_argument('--json', action='store_true', help='wynik jako JSON')
    ap.add_argument('--selftest', action='store_true', help='fikstury w pamięci: każda kontrola potrafi paść')
    return ap


def _fail_usage(message, as_json):
    print('theme_check: BŁĄD: %s' % message, file=sys.stderr)
    if as_json:
        print(json.dumps({'status': 'ERROR', 'code': 2, 'error': message, 'findings': []}, ensure_ascii=False))
    return 2


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.selftest:
        problems, count = selftest()
        for p in problems:
            print('theme_check: selftest: %s' % p, file=sys.stderr)
        if problems:
            return 1
        print('theme_check: selftest OK (%d przypadków)' % count)
        return 0
    if not args.config:
        return _fail_usage('podaj --config <plik.json | ->', args.json)
    try:
        if args.config == '-':
            if not args.project:
                return _fail_usage('--config - wymaga --project', args.json)
            raw = sys.stdin.buffer.read()
            project = Path(args.project)
        else:
            config_path = Path(args.config)
            raw = config_path.read_bytes()
            project = Path(args.project) if args.project else config_path.absolute().parent
        config = json.loads(raw.decode('utf-8-sig'))
    except (OSError, UnicodeDecodeError):
        return _fail_usage('nie można odczytać konfiguracji', args.json)
    except ValueError:
        return _fail_usage('konfiguracja nie jest poprawnym JSON', args.json)
    only = [n.strip() for n in args.only.split(',') if n.strip()] if args.only else None
    res = run(config, project, Path(args.skill_root) if args.skill_root else None, only)
    if res.error is not None:
        return _fail_usage(res.error, args.json)
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    else:
        print('\n'.join(res.lines()))
    return res.code


if __name__ == '__main__':
    sys.exit(main())
