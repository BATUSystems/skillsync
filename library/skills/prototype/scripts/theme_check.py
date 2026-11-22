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
- `contrast`: WCAG 2.x dla par kolorów z jasnego bloku (`contrast.pairs` albo `pairsFrom`: plik JSON i ścieżka klucza z
  kropkami); `fg` i `bg` to nazwa tokenu (`--x`) albo literał koloru; aliasy `var(--x)` do 5 poziomów; formaty `#rgb`, `#rgba`,
  `#rrggbb`, `#rrggbbaa`, `rgb()`, `rgba()`, `hsl()`, `hsla()`, `white`, `black`, `transparent` oraz `oklch()`, `oklab()`,
  `lab()`, `lch()` (konwersja do sRGB wg CSS Color 4, przycięcie do gamutu z `outOfGamut: true` w findingu). Inne formaty
  (`color()`, `color-mix()`, `hwb()`, nazwane kolory, `calc()`, `L` w procentach) i cykle aliasów to UNVERIFIED (pole
  `reason`: `color-format` albo `alias`), nie wyjątek ani PASS. Werdykt z niezaokrąglonego stosunku; token pary spoza
  jasnego bloku to FAIL; wartość tokenu to ostatnia definicja w kolejności źródła. `deviations` ({file, pattern}) zamienia FAIL
  pary z `deviation` w WARN, gdy identyfikator jest w pliku (wzorzec stosowany per linia do 500 znaków, dłuższe: WARN).
- `prototype`: reguły stylu dla plików `files` (domyślnie `project.css`, `index.html`) w katalogach pasujących do `dirs` i
  mających `dirMarker` (domyślnie `index.html`). `var(--x)` musi wskazywać znany token (`knownTokensFrom` w katalogu
  prototypu plus `tokens.files`), a `rules` włączają zakazy literałów: `color`, `font`, `radius` (`true` albo
  `{allow: [...]}`), `weight`, `lineHeight`, `size`, `zIndex` (`tokens`, `forbid`, `allow`). Skanowane są arkusze, bloki
  `<style>` i atrybuty `style`; `url()` i napisy są maskowane, fallback `var(--x, wartość)` skanuje się jak wartość.
  `onMissingFile`: `fail` (domyślnie) albo `skip` z notą. Wzorce `dirs` to `fnmatch` per segment ścieżki (bez `**`).

Ścieżki projektu są względne, bez `..`, bez litery dysku, bez separatora na początku, a po `realpath` leżą pod katalogiem
projektu. Katalog skilli (`--skill-root`) jest jawny albo wyznaczany w projekcie (`.claude/skills`, `.agents/skills`,
`.github/skills`; pierwszy zawierający dany skill); skrypt nigdy nie liczy go z własnego położenia ani z katalogów
użytkownika, a dowiązanie skilla do biblioteki jest stanem normalnym.

Uwagi: tryb `separate` nie wymaga obecności obu rodzin bloków ciemnych (`.dark`/`[data-theme]` i `@media`): porównuje
te, które są; zero bloków to UNVERIFIED, jeden to PASS z adnotacją. Selektor ze słowem `dark`, którego silnik nie rozpoznaje
(`html[data-theme=dark]`, `body.dark`), daje WARN. Normalizacja tekstu kończy plik dokładnie jednym `\n`; `include` używa
`fnmatchcase` (wielkość liter ma znaczenie także na Windows). `tokens.required: null` wyłącza pokrycie i zawsze daje WARN.
Pliki CSS i HTML większe niż 2 MiB albo zagnieżdżone głębiej niż 200 poziomów to kod 2. Każdy nieoczekiwany wyjątek kończy
się kodem 2 i komunikatem „błąd wewnętrzny” bez treści plików.

Cel: kontrola motywu prototypów projektu z konfiguracji JSON, bez zapisu (tokeny, bloki ciemne, kontrast, reguły stylu).
Wejścia: konfiguracja JSON (plik albo stdin), katalog projektu (`--project`), katalog skilla (`--skill-root`) dla kopii `skill:<nazwa>/<ścieżka>`.
Wyjścia: linie `PASS|FAIL|WARN|UNVERIFIED <kontrola>: <opis>` na stdout albo ten sam wynik w JSON; skrypt niczego nie zapisuje.
Zależności: Python 3.11+, tylko biblioteka standardowa (parser CSS we wspólnym `css_tokens.py`).
Granice: czyta źródła CSS i HTML, nie wyrenderowaną stronę; nieobsługiwane formaty koloru dają UNVERIFIED; pliki powyżej 2 MiB to kod 2.
Bezpieczeństwo: tylko odczyt plików projektu i skilla; konfiguracja to dane, nic z niej nie jest wykonywane; błędy wewnętrzne bez treści plików.

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
import theme_colors as tcol  # noqa: E402
import theme_proto as tp  # noqa: E402

PASS, FAIL, WARN, UNVERIFIED, INFO = 'PASS', 'FAIL', 'WARN', 'UNVERIFIED', 'INFO'  # INFO nie wpływa na kod
CHECKS = ('tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'contrast', 'prototype')
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
MAX_FILE_BYTES = ct.MAX_FILE_BYTES
CONTRAST_KEYS = {'pairs', 'pairsFrom', 'deviations'}
PAIR_KEYS = {'name', 'fg', 'bg', 'min', 'deviation'}
PAIRS_FROM_KEYS = {'file', 'path'}
DEVIATION_KEYS = {'file', 'pattern'}
MAX_PATTERN = 200
MAX_DEVIATION_LINE = 500
DEFAULT_MIN = 4.5
PROTO_KEYS = {'dirs', 'dirMarker', 'files', 'knownTokensFrom', 'onMissingFile', 'rules'}
ON_MISSING = ('fail', 'skip')
DEFAULT_PROTO_FILES = ('project.css', 'index.html')


class ConfigError(Exception):
    """Błąd wywołania, konfiguracji albo brak pliku źródłowego (kod 2). Komunikat nie cytuje treści plików."""


class Result:
    def __init__(self):
        self.findings = []
        self.error = None

    def add(self, status, check, message, file=None, line=None, **extra):
        """Dodaje wynik; `extra` (np. `reason`, `outOfGamut`, `ratio`) trafia do JSON, nie do linii tekstowej."""
        finding = {'status': status, 'check': check, 'message': message, 'file': file, 'line': line}
        finding.update(extra)
        self.findings.append(finding)

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
        _str_list(config['forbid'], 'forbid', allow_empty=False)
        if any(v not in FORBIDDEN for v in config['forbid']):
            raise ConfigError('forbid: nieznana konstrukcja (dozwolone: %s)' % ', '.join(FORBIDDEN))
    if ('dark' in config or 'forbid' in config) and 'tokens' not in config:
        raise ConfigError('dark i forbid czytają tokens.files: dodaj klucz tokens')
    for key, allowed in (('copies', COPY_KEYS), ('copyDirs', COPY_DIR_KEYS)):
        if key not in config:
            continue
        if not isinstance(config[key], list) or not config[key]:
            raise ConfigError('%s: oczekiwano niepustej listy' % key)
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
    if 'contrast' in config:
        validate_contrast(config['contrast'])
    if 'prototype' in config:
        validate_prototype(config['prototype'])
    return [k for k in CHECKS if k in config]


def validate_pair(pair, label):
    _only_keys(pair, PAIR_KEYS, label)
    for key in ('fg', 'bg'):
        _str(pair.get(key), '%s.%s' % (label, key))
    if 'name' in pair:
        _str(pair['name'], label + '.name')
    if 'deviation' in pair:
        _str(pair['deviation'], label + '.deviation')
    if 'min' in pair:
        value = pair['min']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1000:
            raise ConfigError('%s.min: oczekiwano liczby dodatniej' % label)


def validate_contrast(contrast):
    _only_keys(contrast, CONTRAST_KEYS, 'contrast')
    if 'pairs' in contrast:
        if not isinstance(contrast['pairs'], list):
            raise ConfigError('contrast.pairs: oczekiwano listy')
        for n, pair in enumerate(contrast['pairs']):
            validate_pair(pair, 'contrast.pairs[%d]' % n)
    if contrast.get('pairsFrom') is not None:
        _only_keys(contrast['pairsFrom'], PAIRS_FROM_KEYS, 'contrast.pairsFrom')
        _str(contrast['pairsFrom'].get('file'), 'contrast.pairsFrom.file')
        _str(contrast['pairsFrom'].get('path'), 'contrast.pairsFrom.path')
    if contrast.get('deviations') is not None:
        dev = contrast['deviations']
        _only_keys(dev, DEVIATION_KEYS, 'contrast.deviations')
        _str(dev.get('file'), 'contrast.deviations.file')
        _str(dev.get('pattern'), 'contrast.deviations.pattern')
        compile_pattern(dev['pattern'])


def compile_pattern(pattern):
    if len(pattern) > MAX_PATTERN:
        raise ConfigError('contrast.deviations.pattern: wzorzec dłuższy niż %d znaków' % MAX_PATTERN)
    try:
        return re.compile(pattern, re.M)
    except (re.error, RecursionError, OverflowError):
        raise ConfigError('contrast.deviations.pattern: błędne wyrażenie regularne') from None


def validate_prototype(proto):
    _only_keys(proto, PROTO_KEYS, 'prototype')
    if 'dirs' not in proto:
        raise ConfigError('prototype.dirs: brak klucza')
    _str_list(proto['dirs'], 'prototype.dirs', allow_empty=False)
    if 'dirMarker' in proto:
        _str(proto['dirMarker'], 'prototype.dirMarker')
    if 'files' in proto:
        _str_list(proto['files'], 'prototype.files', allow_empty=False)
    if 'knownTokensFrom' in proto:
        _str_list(proto['knownTokensFrom'], 'prototype.knownTokensFrom')
    if proto.get('onMissingFile', 'fail') not in ON_MISSING:
        raise ConfigError('prototype.onMissingFile: oczekiwano jednej z wartości %s' % ', '.join(ON_MISSING))
    rules = proto.get('rules', {})
    _only_keys(rules, set(tp.RULE_KEYS), 'prototype.rules')
    for key in ('color', 'font', 'weight', 'lineHeight', 'size'):
        if key in rules and not isinstance(rules[key], bool):
            raise ConfigError('prototype.rules.%s: oczekiwano true albo false' % key)
    if 'radius' in rules and not isinstance(rules['radius'], bool):
        _only_keys(rules['radius'], {'allow'}, 'prototype.rules.radius')
        if 'allow' not in rules['radius']:
            raise ConfigError('prototype.rules.radius.allow: brak klucza')
        _str_list(rules['radius']['allow'], 'prototype.rules.radius.allow')
    if rules.get('zIndex', 'allow') not in tp.ZINDEX_MODES:
        raise ConfigError('prototype.rules.zIndex: oczekiwano jednej z wartości %s' % ', '.join(tp.ZINDEX_MODES))


def proto_rules(proto):
    """Reguły w postaci dla `theme_proto.decl_violations`: `radius` to lista dozwolonych wartości albo None."""
    rules = dict(proto.get('rules', {}))
    radius = rules.get('radius', False)
    rules['radius'] = [] if radius is True else (None if radius is False else list(radius['allow']))
    return rules


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

    def contain(self, path, label):
        """Realpath `path` musi leżeć pod projektem (dowiązanie wychodzące to kod 2); zwraca realpath."""
        real = os.path.realpath(path)
        base = os.path.normcase(self.real)
        if os.path.commonpath([os.path.normcase(real), base]) != base:
            raise ConfigError('%s: plik wychodzi poza projekt (dowiązanie)' % label)
        return real

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
        contrast = config.get('contrast')
        if contrast is not None:
            for key in ('pairsFrom', 'deviations'):
                if contrast.get(key) is not None:
                    self.project_path(contrast[key]['file'], 'contrast.%s.file' % key)
        proto = config.get('prototype')
        if proto is not None:
            for value in proto['dirs']:
                relative_parts(value, 'prototype.dirs')
            relative_parts(proto.get('dirMarker', 'index.html'), 'prototype.dirMarker')
            for key in ('files', 'knownTokensFrom'):
                for value in proto.get(key, []):
                    relative_parts(value, 'prototype.' + key)


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
        try:
            self.blocks = ct.parse_theme(text)
            self.hints = ct.dark_hints(text)
        except ValueError as error:
            raise ConfigError('%s: %s' % (rel, error)) from None


def load_theme_files(ctx, tokens, res, check):
    """Wczytuje `tokens.files`; brak pliku to FAIL z nazwą pliku (kopia motywu jest wynikiem projektu, nie wejściem).
    Zwraca (pliki, komplet): `komplet` to False, gdy któregoś pliku brakowało. Zły rozmiar lub kodowanie to kod 2."""
    loaded, complete = [], True
    for n, value in enumerate(tokens['files']):
        path, rel = ctx.project_path(value, 'tokens.files[%d]' % n)
        if not path.is_file():
            res.add(FAIL, check, 'brak pliku tokenów', rel)
            complete = False
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ConfigError('%s: plik większy niż 2 MiB' % rel)
        try:
            loaded.append(ThemeFile(rel, read_text(path)))
        except UnicodeDecodeError:
            raise ConfigError('%s: plik nie jest poprawnym UTF-8' % rel) from None
    return loaded, complete


# ---------------------------------------------------------------------------------------------------------------
# Kontrole
# ---------------------------------------------------------------------------------------------------------------

def _light_map(files):
    """Token -> (wartość, plik, linia) z jasnych bloków: wygrywa OSTATNIA definicja w kolejności źródła (w pliku
    największy numer linii, między plikami późniejszy plik), jak w kaskadzie."""
    light = {}
    for f in files:
        for stack, name, value, line in ct.css_declarations_lines(f.text):  # kolejność źródła
            if ct.LIGHT in ct.block_kinds(stack):
                light[name] = (value, f.rel, line)
    return light


def _dark_blocks(files):
    return [(f, b) for f in files for b in f.blocks if b.kind in (ct.DARK, ct.MEDIA_DARK)]


def check_tokens(ctx, cfg, files, res, complete=True):
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
        if not label.startswith('skill:'):
            for child in sorted(assets.rglob('*')):
                if child.is_file():
                    ctx.contain(child, 'tokens.assets')
        try:
            used = ct.used_tokens(assets)
        except ValueError as error:
            raise ConfigError('tokens.assets: %s' % error) from None
        for tok in sorted(set(required) - used):
            problems += 1
            res.add(FAIL, 'tokens', 'wymagany token %s nie jest używany przez zasoby (%s)' % (tok, label), rel)
        for tok in sorted(used - set(required)):
            problems += 1
            res.add(FAIL, 'tokens', 'zasoby (%s) używają tokenu %s, którego nie ma na liście' % (label, tok), rel)
        note = ', zbiór używanych przez zasoby równy liście'
    if not problems and complete:
        res.add(PASS, 'tokens', '%d wymaganych tokenów w jasnym bloku%s' % (len(required), note))


def check_dark(cfg, files, res, complete=True):
    mode = cfg['dark']
    darks = _dark_blocks(files)
    for f in files:
        for hint, key, line in f.hints:
            if hint == 'unrecognized':
                res.add(WARN, 'dark', 'selektor %s ze słowem dark nie jest rozpoznawany przez silnik (bloki ciemne: '
                        '.dark, [data-theme="dark"], :root[data-theme="dark"])' % key, f.rel, line)
            elif hint == 'plain' and mode == 'none':
                res.add(WARN, 'dark', 'blok ciemny %s ma deklaracje spoza tokenów (tryb none)' % key, f.rel, line)
    if mode == 'none':
        for f, block in darks:
            first = min(line for _v, line in block.tokens.values())
            res.add(FAIL, 'dark', 'blok ciemny %s nie może istnieć (tryb none)' % block.key, f.rel, first)
        if not darks and complete:
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
        if not problems and complete:
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
    if not darks:
        res.add(UNVERIFIED, 'dark', 'brak bloków ciemnych: czy selektor jest rozpoznawany przez silnik? (tryb separate)')
    elif not problems and complete:
        if len(darks) == 1:
            res.add(PASS, 'dark', '1 blok ciemny, nie ma czego porównywać (tryb separate; token tylko ciemny sprawdza tokens)')
        else:
            res.add(PASS, 'dark', '%d bloków ciemnych, spójne (tryb separate)' % len(darks))


def check_forbid(cfg, files, res, complete=True):
    problems = 0
    patterns = {'color-scheme': re.compile(r'(?<![\w-])color-scheme\s*:', re.I),
                'prefers-color-scheme': re.compile(r'prefers-color-scheme', re.I)}
    for f in files:
        text = ct.strip_css_comments(f.text, keep_lines=True)
        for name in cfg['forbid']:
            for m in patterns[name].finditer(text):
                problems += 1
                res.add(FAIL, 'forbid', 'zakazana konstrukcja %s' % name, f.rel, text.count('\n', 0, m.start()) + 1)
    if not problems and complete:
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
        if copy.exists() and os.path.samefile(source, copy):
            raise ConfigError('copies[%d]: source i copy wskazują ten sam plik' % n)
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
        if copy_dir.exists() and os.path.samefile(source, copy_dir):
            raise ConfigError('copyDirs[%d]: source i copy wskazują ten sam katalog' % n)
        names = sorted(p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file())
        chosen = [r for r in names if _included(r, item['include'])]
        if not chosen:
            res.add(UNVERIFIED, 'copyDirs', 'żaden plik źródła %s nie pasuje do include (nic nie sprawdzono)' % label,
                    copy_rel)
            continue
        ok = 0
        for rel in chosen:
            target = copy_dir / rel
            if target.exists():
                ctx.contain(target, 'copyDirs[%d]' % n)
                if os.path.samefile(source / rel, target):
                    raise ConfigError('copyDirs[%d]: source i copy wskazują ten sam plik' % n)
            if _compare_copy(source / rel, copy_dir / rel, '%s/%s' % (label, rel), '%s/%s' % (copy_rel, rel), 'auto',
                             res, 'copyDirs'):
                ok += 1
        if ok == len(chosen):
            res.add(PASS, 'copyDirs', '%d plików zgodnych ze źródłem %s' % (ok, label), copy_rel)


def read_contained(ctx, path, rel):
    """Tekst pliku pod projektem: realpath w projekcie, rozmiar do 2 MiB, UTF-8 (BOM dozwolony); błąd to kod 2."""
    ctx.contain(path, rel)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ConfigError('%s: plik większy niż 2 MiB' % rel)
    try:
        return read_text(path)
    except UnicodeDecodeError:
        raise ConfigError('%s: plik nie jest poprawnym UTF-8' % rel) from None


def _json_path(data, dotted):
    node = data
    for key in dotted.split('.'):
        if not key:
            raise ConfigError('contrast.pairsFrom.path: pusty składnik ścieżki')
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            raise ConfigError('contrast.pairsFrom.path: ścieżka nie istnieje w pliku')
    return node


def load_pairs(ctx, contrast):
    """Pary z `pairs` i `pairsFrom`: lista (para, plik źródłowy albo None). Błędy kształtu i brak pliku to kod 2."""
    pairs = [(p, None) for p in contrast.get('pairs', [])]
    source = contrast.get('pairsFrom')
    if source is not None:
        path, rel = ctx.project_path(source['file'], 'contrast.pairsFrom.file')
        if not path.is_file():
            raise ConfigError('contrast.pairsFrom: brak pliku z parami')
        try:
            data = json.loads(read_contained(ctx, path, rel))
        except (ValueError, RecursionError):
            raise ConfigError('contrast.pairsFrom: plik nie jest poprawnym JSON') from None
        node = _json_path(data, source['path'])
        if not isinstance(node, list):
            raise ConfigError('contrast.pairsFrom: pod ścieżką oczekiwano listy par')
        for n, pair in enumerate(node):
            validate_pair(pair, 'contrast.pairsFrom[%d]' % n)
            pairs.append((pair, rel))
    return pairs


def deviation_ids(ctx, contrast):
    dev = contrast.get('deviations')
    if dev is None:
        raise ConfigError('contrast: para z deviation wymaga contrast.deviations')
    path, rel = ctx.project_path(dev['file'], 'contrast.deviations.file')
    if not path.is_file():
        raise ConfigError('contrast.deviations: brak pliku odstępstw')
    regex = compile_pattern(dev['pattern'])
    ids, skipped = set(), 0
    for text_line in read_contained(ctx, path, rel).splitlines():
        if len(text_line) > MAX_DEVIATION_LINE:  # ochrona przed wolnym wyrażeniem regularnym
            skipped += 1
            continue
        ids |= {m.group(1) if regex.groups else m.group(0) for m in regex.finditer(text_line)}
    return ids, skipped, rel


def _floor3(value):
    return int(value * 1000) / 1000.0


def check_contrast(ctx, cfg, files, res):
    contrast = cfg['contrast']
    pairs = load_pairs(ctx, contrast)
    if not pairs:
        res.add(UNVERIFIED, 'contrast', 'brak par kontrastu (nic nie zmierzono)')
        return
    light = {name: value for name, (value, _file, _line) in _light_map(files).items()}
    uses_tokens = any(tcol.TOKEN_NAME_RE.fullmatch(p[side].strip()) or p[side].strip().lower().startswith('var(')
                      for p, _src in pairs for side in ('fg', 'bg'))
    if uses_tokens and 'tokens' not in cfg:
        raise ConfigError('contrast: pary z tokenami wymagają klucza tokens (pliki tokenów)')
    deviations = set()
    if any('deviation' in p for p, _src in pairs):
        deviations, skipped, dev_rel = deviation_ids(ctx, contrast)
        if skipped:
            res.add(WARN, 'contrast', 'pominięto %d linii dłuższych niż %d znaków w pliku odstępstw'
                    % (skipped, MAX_DEVIATION_LINE), dev_rel)
    for pair, source in pairs:
        name = pair.get('name') or '%s na %s' % (pair['fg'], pair['bg'])
        minimum = float(pair.get('min', DEFAULT_MIN))
        try:
            fg = tcol.resolve_color(pair['fg'], light)
            bg = tcol.resolve_color(pair['bg'], light)
        except tcol.MissingToken as error:
            res.add(FAIL, 'contrast', 'para "%s": token %s nie jest zdefiniowany w jasnym bloku' % (name, error.name),
                    source, reason='missing-token')
            continue
        except tcol.ColorFormat as error:
            res.add(UNVERIFIED, 'contrast', 'para "%s": nie da się zmierzyć: %s' % (name, error), source,
                    reason=error.reason)
            continue
        ratio = tcol.contrast_ratio(fg, bg)
        extra = {'ratio': ratio, 'min': minimum}
        if fg.out_of_gamut or bg.out_of_gamut:
            extra['outOfGamut'] = True
        text = 'para "%s": kontrast %.3f (min %s)' % (name, _floor3(ratio), minimum)
        if extra.get('outOfGamut'):
            text += ', kolor poza gamutem sRGB przycięty'
        if ratio >= minimum:
            res.add(PASS, 'contrast', text, source, **extra)
        elif pair.get('deviation') in deviations:
            res.add(WARN, 'contrast', text + ': poniżej minimum, odstępstwo %s' % pair['deviation'], source, **extra)
        elif 'deviation' in pair:
            res.add(FAIL, 'contrast', text + ': poniżej minimum, odstępstwo %s nie istnieje w pliku odstępstw'
                    % pair['deviation'], source, **extra)
        else:
            res.add(FAIL, 'contrast', text + ': poniżej minimum', source, **extra)


def expand_dirs(ctx, pattern, label):
    """Katalogi pasujące do wzorca (`fnmatch` per segment, bez `**`); realpath każdego leży pod projektem."""
    level = [ctx.real]
    for segment in relative_parts(pattern, label):
        nxt = []
        for base in level:
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            nxt += [os.path.join(base, n) for n in names
                    if fnmatch.fnmatchcase(n, segment) and os.path.isdir(os.path.join(base, n))]
        level = nxt
    return level


def check_prototype(ctx, cfg, files, res):
    proto = cfg['prototype']
    rules = proto_rules(proto)
    marker = relative_parts(proto.get('dirMarker', 'index.html'), 'prototype.dirMarker')
    names = [relative_parts(v, 'prototype.files') for v in proto.get('files', DEFAULT_PROTO_FILES)]
    token_sources = [relative_parts(v, 'prototype.knownTokensFrom') for v in proto.get('knownTokensFrom', [])]
    skip = proto.get('onMissingFile', 'fail') == 'skip'
    base_known = set()
    for f in files:
        try:
            base_known |= tp.declared_tokens(f.text)
        except ValueError as error:
            raise ConfigError('%s: %s' % (f.rel, error)) from None
    dirs, seen = [], set()
    for pattern in proto['dirs']:
        for found in expand_dirs(ctx, pattern, 'prototype.dirs'):
            real = ctx.contain(found, 'prototype.dirs')
            if real not in seen and os.path.isfile(os.path.join(real, *marker)):
                seen.add(real)
                dirs.append(real)
    if not dirs:
        res.add(UNVERIFIED, 'prototype', 'żaden katalog nie pasuje do dirs (ze znacznikiem %s): nic nie sprawdzono'
                % '/'.join(marker))
        return
    scanned = 0
    for real in sorted(dirs):
        dir_rel = os.path.relpath(real, ctx.real).replace(os.sep, '/')
        known, problems, checked = set(base_known), 0, 0
        for parts in token_sources:
            path = Path(real, *parts)
            if path.is_file():
                label = '%s/%s' % (dir_rel, '/'.join(parts))
                try:
                    known |= tp.declared_tokens(read_contained(ctx, path, label))
                except ValueError as error:
                    raise ConfigError('%s: %s' % (label, error)) from None
        if not known:
            problems += 1
            res.add(FAIL, 'prototype', 'brak znanych tokenów (nie da się sprawdzić istnienia var(--x))', dir_rel)
        for parts in names:
            path = Path(real, *parts)
            label = '%s/%s' % (dir_rel, '/'.join(parts))
            if not path.is_file():
                if skip:
                    res.add(INFO, 'prototype', 'pominięto brak pliku (onMissingFile: skip)', label)
                else:
                    problems += 1
                    res.add(FAIL, 'prototype', 'brak pliku', label)
                continue
            text = read_contained(ctx, path, label)
            try:
                if path.suffix.lower() in ('.html', '.htm'):
                    found = tp.html_violations(text, known, rules)
                else:
                    found = tp.css_violations(text, known, rules)
            except ValueError as error:
                raise ConfigError('%s: %s' % (label, error)) from None
            checked += 1
            for line, message in found:
                problems += 1
                res.add(FAIL, 'prototype', message, label, line)
        scanned += checked
        if not problems and checked:
            res.add(PASS, 'prototype', 'prototyp %s: %d plików bez naruszeń stylu' % (dir_rel, checked), dir_rel)
    if not scanned:
        res.add(UNVERIFIED, 'prototype', 'żaden plik prototypu nie został sprawdzony')


# ---------------------------------------------------------------------------------------------------------------
# Uruchomienie
# ---------------------------------------------------------------------------------------------------------------

def _run(config, project, skill_root, only, res):
    enabled = validate_config(config)
    skipped = []
    if only is not None:
        if not only:
            raise ConfigError('--only: podaj co najmniej jedną kontrolę')
        unknown = [name for name in only if name not in CHECKS]
        if unknown:
            raise ConfigError('--only: nieznana kontrola (dozwolone: %s)' % ', '.join(CHECKS))
        missing = [name for name in only if name not in enabled]
        if missing:
            raise ConfigError('--only: kontroli %s nie ma w konfiguracji' % ', '.join(missing))
        skipped = [name for name in enabled if name not in only]
        enabled = [name for name in enabled if name in only]
    ctx = Context(project, skill_root)
    ctx.prevalidate(config)
    if not enabled:
        res.add(UNVERIFIED, 'config', 'konfiguracja nie zawiera żadnej kontroli (nic nie sprawdzono)')
        return
    files, complete = None, True
    for name in enabled:
        if name in ('tokens', 'dark', 'forbid'):
            if config['tokens']['required'] is None and name == 'tokens':
                if only is not None:
                    raise ConfigError('tokens.required to null: kontrola pokrycia jest wyłączona')
                res.add(WARN, 'tokens', 'pokrycie wyłączone (tokens.required: null): wymagane tokeny nie są sprawdzane')
                continue
            if files is None:
                files, complete = load_theme_files(ctx, config['tokens'], res, name)
            if not files:
                if not config['tokens']['files']:
                    res.add(UNVERIFIED, name, 'tokens.files jest puste (brak plików do sprawdzenia)')
                else:
                    res.add(UNVERIFIED, name, 'żaden plik tokenów nie został wczytany')
                continue
            if name == 'tokens':
                check_tokens(ctx, config, files, res, complete)
            elif name == 'dark':
                check_dark(config, files, res, complete)
            else:
                check_forbid(config, files, res, complete)
        elif name in ('contrast', 'prototype'):
            if 'tokens' in config and files is None:
                files, complete = load_theme_files(ctx, config['tokens'], res, name)
            if name == 'contrast':
                check_contrast(ctx, config, files or [], res)
            else:
                check_prototype(ctx, config, files or [], res)
        elif name == 'copies':
            check_copies(ctx, config, res)
        elif name == 'copyDirs':
            check_copy_dirs(ctx, config, res)
    if not any(f['status'] != INFO for f in res.findings):
        res.add(UNVERIFIED, 'config', 'żadna kontrola nic nie zmierzyła')
    if skipped:
        res.add(INFO, 'config', 'pominięto kontrole z konfiguracji (--only): %s' % ', '.join(skipped))


def run(config, project, skill_root=None, only=None):
    """Wykonuje kontrole z konfiguracji `config` (słownik) w katalogu `project`; zwraca `Result`
    (`findings`, `code`, `error`). Błędy konfiguracji i brak źródeł dają `error` i kod 2, bez wyjątku;
    nieoczekiwany wyjątek to też kod 2 z komunikatem „błąd wewnętrzny” (bez treści plików)."""
    res = Result()
    try:
        _run(config, project, skill_root, list(only) if only is not None else None, res)
    except ConfigError as error:
        res.findings = []
        res.error = str(error)
    except Exception as error:  # noqa: BLE001  (siatka bezpieczeństwa: kod 2 zamiast śladu stosu)
        res.findings = []
        res.error = 'błąd wewnętrzny (%s)' % type(error).__name__
    return res


# ---------------------------------------------------------------------------------------------------------------
# Selftest: każda kontrola potrafi paść
# ---------------------------------------------------------------------------------------------------------------

_LIGHT = ':root { --a: 1; --b: 2; }\n'
_DARK = '[data-theme="dark"] { --a: 3; --b: 4; }\n'


def _case(name, files, config, expect_code, expect_check=None, expect_status=None, only=None, field=None):
    """`field`: (klucz, wartość), które musi mieć wynik (np. `('outOfGamut', True)` w JSON)."""
    return {'name': name, 'files': files, 'config': config, 'code': expect_code, 'check': expect_check,
            'status': expect_status, 'only': only, 'field': field}


def _tokens_cfg(**extra):
    cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt'}}
    cfg.update(extra)
    return cfg


def _contrast_cfg(pairs, **extra):
    cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'contrast': {'pairs': pairs}}
    cfg['contrast'].update(extra)
    return cfg


def contrast_cases():
    theme = {'t.css': ':root { --fg: #000; --bg: oklch(1 0 0); --lo: hsl(0 0% 60%); --alias: var(--bg); '
                      '--mix: color-mix(in srgb, red, blue); --loop: var(--loop); }\n'}
    pair = {'fg': '--fg', 'bg': '--bg'}
    dev = {'deviations.md': '### D12 odstępstwo\n'}
    dev_cfg = {'deviations': {'file': 'deviations.md', 'pattern': r'^###\s+(D\d+)\b'}}
    low = {'fg': '--lo', 'bg': '--bg', 'min': 4.5}
    only = ['contrast']
    return [
        _case('contrast: wystarczający', theme, _contrast_cfg([pair]), 0, 'contrast', PASS, only=only),
        _case('contrast: oklch poniżej minimum', {'t.css': ':root { --fg: oklch(0.7 0 0); --bg: oklch(0.75 0 0); }\n'},
              _contrast_cfg([pair]), 1, 'contrast', FAIL, only=only),
        _case('contrast: oklch poza gamutem, flaga i brak FAIL',
              {'t.css': ':root { --fg: oklch(0.86644 0.29483 142.495); --bg: #000; }\n'},
              _contrast_cfg([{'fg': '--fg', 'bg': '--bg', 'min': 1}]), 0, 'contrast', PASS, only=only,
              field=('outOfGamut', True)),
        _case('contrast: za niski', theme, _contrast_cfg([low]), 1, 'contrast', FAIL, only=only),
        _case('contrast: alias i literał', theme, _contrast_cfg([{'fg': '#123', 'bg': '--alias'}]), 0, 'contrast', PASS,
              only=only),
        _case('contrast: nierozpoznany format', theme, _contrast_cfg([{'fg': '--mix', 'bg': '--bg'}]), 3, 'contrast',
              UNVERIFIED, only=only),
        _case('contrast: cykl aliasów', theme, _contrast_cfg([{'fg': '--loop', 'bg': '--bg'}]), 3, 'contrast', UNVERIFIED,
              only=only),
        _case('contrast: token spoza jasnego bloku', theme, _contrast_cfg([{'fg': '--brak', 'bg': '--bg'}]), 1,
              'contrast', FAIL, only=only),
        _case('contrast: zero par', theme, _contrast_cfg([]), 3, 'contrast', UNVERIFIED, only=only),
        _case('contrast: odstępstwo istnieje', dict(theme, **dev), _contrast_cfg([dict(low, deviation='D12')], **dev_cfg),
              0, 'contrast', WARN, only=only),
        _case('contrast: odstępstwo nie istnieje', dict(theme, **dev), _contrast_cfg([dict(low, deviation='D99')], **dev_cfg),
              1, 'contrast', FAIL, only=only),
        _case('contrast: brak pliku odstępstw', theme, _contrast_cfg([dict(low, deviation='D12')], **dev_cfg), 2,
              only=only),
        _case('contrast: błędny wzorzec odstępstw', dict(theme, **dev),
              _contrast_cfg([dict(low, deviation='D12')], deviations={'file': 'deviations.md', 'pattern': '('}), 2,
              only=only),
    ]


def _proto_cfg(rules=None, **extra):
    cfg = {'version': 1, 'prototype': {'dirs': ['p/*'], 'knownTokensFrom': ['tokens.css'],
                                       'rules': rules if rules is not None else {'color': True, 'font': True}}}
    cfg['prototype'].update(extra)
    return cfg


def prototype_cases():
    tokens = ':root { --fg: #000; --space: 4px; }\n'
    ok = {'p/a/index.html': '<p style="color: var(--fg)">x</p>\n', 'p/a/project.css': '.a { color: var(--fg); }\n',
          'p/a/tokens.css': tokens}

    def with_css(css, **more):
        return dict(ok, **dict({'p/a/project.css': css}, **more))
    return [
        _case('prototype: czysty', ok, _proto_cfg(), 0, 'prototype', PASS),
        _case('prototype: literał koloru', with_css('.a { color: #f00; }\n'), _proto_cfg(), 1, 'prototype', FAIL),
        _case('prototype: literał koloru w atrybucie style', with_css('', **{'p/a/index.html': '<p style="color:red">x</p>'}),
              _proto_cfg(), 1, 'prototype', FAIL),
        _case('prototype: nieznany token', with_css('.a { color: var(--nie-ma); }\n'), _proto_cfg(), 1, 'prototype', FAIL),
        _case('prototype: font-family poza var()', with_css('.a { font-family: Arial; }\n'), _proto_cfg(), 1, 'prototype',
              FAIL),
        _case('prototype: promień spoza allow', with_css('.a { border-radius: 8px; }\n'),
              _proto_cfg({'radius': {'allow': ['0']}}), 1, 'prototype', FAIL),
        _case('prototype: z-index poza tokenami', with_css('.a { z-index: 5; }\n'), _proto_cfg({'zIndex': 'tokens'}), 1,
              'prototype', FAIL),
        _case('prototype: pusty zbiór tokenów', {k: v for k, v in ok.items() if k != 'p/a/tokens.css'}, _proto_cfg(), 1,
              'prototype', FAIL),
        _case('prototype: brak pliku', {k: v for k, v in ok.items() if k != 'p/a/project.css'}, _proto_cfg(), 1,
              'prototype', FAIL),
        _case('prototype: brak pliku, skip', {k: v for k, v in ok.items() if k != 'p/a/project.css'},
              _proto_cfg(onMissingFile='skip'), 0, 'prototype', PASS),
        _case('prototype: żaden katalog', {'p/x/readme.txt': 'x'}, _proto_cfg(), 3, 'prototype', UNVERIFIED),
        _case('prototype: literał font-weight', with_css('.a { font-weight: 700; }\n'), _proto_cfg({'weight': True}), 1,
              'prototype', FAIL),
        _case('prototype: literał line-height', with_css('.a { line-height: 1.5; }\n'), _proto_cfg({'lineHeight': True}), 1,
              'prototype', FAIL),
        _case('prototype: literał rozmiaru czcionki', with_css('.a { font-size: 14px; }\n'), _proto_cfg({'size': True}), 1,
              'prototype', FAIL),
        _case('prototype: długa forma promienia', with_css('.a { border-top-left-radius: 9px; }\n'),
              _proto_cfg({'radius': {'allow': []}}), 1, 'prototype', FAIL),
        _case('prototype: fallback var() bez literału', with_css('.a { color: var(--fg, var(--fg)); }\n'), _proto_cfg(), 0,
              'prototype', PASS),
    ]


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
        _case('dark separate: brak bloków', dict(ok_theme, **{'t.css': _LIGHT}), _tokens_cfg(dark='separate'), 3, 'dark',
              UNVERIFIED, only=['dark']),
        _case('dark: selektor nierozpoznany przez silnik', dict(ok_theme, **{'t.css': _LIGHT + 'html[data-theme=dark] { --a: 3; }\n'}),
              _tokens_cfg(dark='none'), 0, 'dark', WARN, only=['dark']),
        _case('dark none: blok bez tokenów', dict(ok_theme, **{'t.css': _LIGHT + '.dark { background: #000; }\n'}),
              _tokens_cfg(dark='none'), 0, 'dark', WARN, only=['dark']),
        _case('tokens: required null', ok_theme, {'version': 1, 'tokens': {'files': ['t.css'], 'required': None},
                                                   'forbid': ['color-scheme']}, 0, 'tokens', WARN),
        _case('forbid: pusta lista', ok_theme, _tokens_cfg(forbid=[]), 2),
        _case('forbid: czysto', ok_theme, _tokens_cfg(forbid=['color-scheme', 'prefers-color-scheme']), 0, 'forbid', PASS,
              only=['forbid']),
        _case('forbid: color-scheme', dict(ok_theme, **{'t.css': ':root { color-scheme: light; --a: 1; --b: 2; }\n'}),
              _tokens_cfg(forbid=['color-scheme']), 1, 'forbid', FAIL, only=['forbid']),
        _case('forbid: prefers-color-scheme', dict(ok_theme, **{'t.css': '@media (prefers-color-scheme: dark) { :root { --a: 1; } }\n'}),
              _tokens_cfg(forbid=['prefers-color-scheme']), 1, 'forbid', FAIL, only=['forbid']),
        _case('copies: równa po CRLF i BOM', {'src.css': 'a { b: c; }\n', 'dst.css': '\ufeffa { b: c; }\r\n'},
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
    ]
    cases += contrast_cases() + prototype_cases()
    cases += [
        _case('pusta konfiguracja', {}, {'version': 1}, 3, 'config', UNVERIFIED),
        _case('nieznany klucz', {}, {'version': 1, 'bogus': 1}, 2),
        _case('ścieżka z ..', req, {'version': 1, 'copies': [{'source': '../x', 'copy': 'y'}]}, 2),
    ]
    return cases


def selftest():
    """Uruchamia fikstury w pamięci; zwraca krotkę (lista niezgodności, liczba przypadków); pusta lista = każda
    kontrola potrafi paść."""
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
                                       and (not case['field'] or f.get(case['field'][0]) == case['field'][1])
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


def _utf8_streams():
    """Strumienie wyjścia w UTF-8 z zastępowaniem niekodowalnych znaków (jak `fsutil.reconfigure` w generatorze)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (OSError, ValueError):
                pass


def main(argv=None):
    _utf8_streams()
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001  (siatka bezpieczeństwa, np. RecursionError z json.loads)
        print('theme_check: BŁĄD: błąd wewnętrzny (%s)' % type(error).__name__, file=sys.stderr)
        if '--json' in (argv if argv is not None else sys.argv[1:]):
            print(json.dumps({'status': 'ERROR', 'code': 2, 'reason': 'błąd wewnętrzny (%s)' % type(error).__name__}, ensure_ascii=False))
        return 2


def _main(argv):
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
    except RecursionError:
        return _fail_usage('konfiguracja jest zagnieżdżona zbyt głęboko', args.json)
    except ValueError:
        return _fail_usage('konfiguracja nie jest poprawnym JSON', args.json)
    only = None
    if args.only is not None:
        only = [n.strip() for n in args.only.split(',') if n.strip()]
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
