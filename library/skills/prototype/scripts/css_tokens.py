"""Wspólny parser tokenów CSS skilla prototypów (Python 3.11+, tylko biblioteka standardowa).

Moduł importują `check_tokens.py` (strażnik skilla) i `theme_check.py` (kontrola motywu projektu); żaden z nich nie
duplikuje parsera. Skrypty dodają katalog `scripts/` do `sys.path` jawnie, bo tryb `-I` nie robi tego sam.

Definicje bloków są zgodne z silnikiem prototypów (`extract_tokens` w generatorze):

- jasny blok: selektor dokładnie `:root` (także na liście po przecinku), `@theme` i `@theme inline`; blok może leżeć
  w `@layer` (dowolnie głęboko), ale nie w `@media`, `@supports` ani w innym bloku;
- ciemny blok: selektor dokładnie `.dark`, `[data-theme="dark"]` albo `:root[data-theme="dark"]` (po normalizacji
  cudzysłowów); selektor ze słowem `dark` w innym znaczeniu (`.card-dark`, `.dark .card`) NIE jest blokiem ciemnym;
- `@media (prefers-color-scheme: dark)`: rozpoznawany osobno (rodzaj `media-dark`); silnik pomija go z ostrzeżeniem,
  ta biblioteka go zgłasza, żeby kontrole `dark` i `forbid` mogły o nim mówić.

Różnice względem silnika, świadome: parser nie ostrzega o `@import` i o tokenach w innych `@media`; wynik zawiera numery
linii; bloki w `@media` z innym warunkiem niż ciemny schemat są pomijane tak samo jak w silniku. Zgodność zbiorów
tokenów jasnych i ciemnych z `extract_tokens` pilnuje `tests/test_skillsync_css_tokens.py`.
"""
import re
from pathlib import Path

LIGHT_SELECTORS = frozenset({':root'})
DARK_SELECTORS = frozenset({'.dark', '[data-theme="dark"]', ':root[data-theme="dark"]'})
ENGINE_PREFIX = '--proto-'
TOKEN_RE = re.compile(r'--[\w-]+')
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_NESTING = 200
LIGHT, DARK, MEDIA_DARK = 'light', 'dark', 'media-dark'


# ---------------------------------------------------------------------------------------------------------------
# Komentarze i napisy
# ---------------------------------------------------------------------------------------------------------------

def _skip_string(text, index):
    """Indeks za napisem zaczynającym się w `index`; jak w silniku napis kończy się cudzysłowem albo końcem linii
    (znak po ukośniku wstecznym jest pomijany), a niezamknięty sięga końca tekstu."""
    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == '\\':
            index += 2
            continue
        if char == quote or char == '\n':
            return index + 1
        index += 1
    return len(text)


def strip_css_comments(text, keep_lines=False, strings=True):
    """Usuwa `/* … */` poza napisami (niezamknięty komentarz sięga końca tekstu). Z `keep_lines` zostają
    znaki nowej linii z komentarzy, więc numery linii w wyniku zgadzają się z oryginałem. Z `strings=False`
    napisy nie są rozpoznawane (tekst HTML: apostrof w prozie nie jest początkiem napisu)."""
    parts, index, start = [], 0, 0
    while index < len(text):
        char = text[index]
        if strings and char in '"\'':
            index = _skip_string(text, index)
        elif text.startswith('/*', index):
            parts.append(text[start:index])
            close = text.find('*/', index + 2)
            end = len(text) if close < 0 else close + 2
            if keep_lines:
                parts.append('\n' * text.count('\n', index, end))
            index = start = end
        else:
            index += 1
    parts.append(text[start:])
    return ''.join(parts)


# ---------------------------------------------------------------------------------------------------------------
# Deklaracje niestandardowych właściwości
# ---------------------------------------------------------------------------------------------------------------

def _scan(text, strings=True):
    """Deklaracje `właściwość: wartość` w tekście bez komentarzy: lista (stos_selektorów, nazwa, wartość, linia).
    Reguły jak w silniku: `{` zawsze otwiera blok, `}` zamyka, oba zerują licznik nawiasów, `;` dzieli poza nawiasami,
    napis kończy się cudzysłowem albo końcem linii. Zagnieżdżenie głębsze niż MAX_NESTING to ValueError."""
    out = []
    stack = []
    buf = []
    paren = 0
    line = 1
    buf_line = None
    i, n = 0, len(text)

    def flush():
        decl = ''.join(buf)
        m = re.match(r'\s*(--[\w-]+|[A-Za-z-][\w-]*)\s*:', decl)
        if m and stack:
            out.append((tuple(stack), m.group(1), ' '.join(decl[m.end():].split()), buf_line or line))

    while i < n:
        ch = text[i]
        if strings and ch in '"\'':
            j = _skip_string(text, i)
            chunk = text[i:j]
            if buf_line is None:
                buf_line = line
            buf.append(chunk)
            line += chunk.count('\n')
            i = j
            continue
        if ch == '{':
            stack.append(''.join(buf).strip())
            if len(stack) > MAX_NESTING:
                raise ValueError('zagnieżdżenie CSS głębsze niż %d' % MAX_NESTING)
            buf, buf_line, paren = [], None, 0
        elif ch == '}':
            flush()
            buf, buf_line, paren = [], None, 0
            if stack:
                stack.pop()
        elif ch == ';' and paren == 0:
            flush()
            buf, buf_line = [], None
        else:
            if ch == '(':
                paren += 1
            elif ch == ')':
                paren = max(0, paren - 1)
            if buf_line is None and not ch.isspace():
                buf_line = line
            buf.append(ch)
        if ch == '\n':
            line += 1
        i += 1
    return out


def css_declarations(text, with_values=False, strings=True):
    """Zwraca listę (stos_selektorów, nazwa_tokenu) dla deklaracji niestandardowych właściwości;
    z `with_values` krotki mają trzeci element: wartość ze znormalizowanymi białymi znakami (dosłowna, także
    z `!important`; silnik zachowuje oryginalne odstępy, więc porównania z silnikiem robi się po normalizacji)."""
    out = []
    for stack, name, value, _line in _scan(strip_css_comments(text, strings=strings), strings):
        if name.startswith('--'):
            out.append((stack, name, value) if with_values else (stack, name))
    return out


def declarations(css, line_offset=0):
    """Wszystkie deklaracje arkusza (także zwykłe właściwości): lista (stos_selektorów, nazwa, wartość, linia).
    Komentarze są zastępowane z zachowaniem numerów linii; `line_offset` przesuwa numery (osadzony fragment)."""
    return [(stack, name, value, line + line_offset)
            for stack, name, value, line in _scan(strip_css_comments(css, keep_lines=True))]


def css_declarations_lines(text):
    """Jak `css_declarations(with_values=True)`, z numerem linii deklaracji jako czwartym elementem."""
    return [d for d in _scan(strip_css_comments(text, keep_lines=True)) if d[1].startswith('--')]


def dark_hints(css):
    """Podpowiedzi o blokach ciemnych (lista (rodzaj, klucz, linia)): `unrecognized` to selektor ze słowem `dark`, którego
    silnik nie rozpoznaje (np. `html[data-theme=dark]`, `body.dark`), a który deklaruje tokeny; `plain` to rozpoznany blok
    ciemny z deklaracjami spoza tokenów (np. `background`)."""
    seen, out = set(), []
    for stack, name, _value, line in _scan(strip_css_comments(css, keep_lines=True)):
        kinds = block_kinds(stack)
        key = ' / '.join(stack)
        custom = name.startswith('--')
        if custom and not (kinds & {DARK, MEDIA_DARK}) and not stack[-1].startswith('@') \
                and re.search(r'(?<![\w-])dark(?![\w-])', stack[-1]):
            hint = 'unrecognized'
        elif not custom and kinds & {DARK, MEDIA_DARK}:
            hint = 'plain'
        else:
            continue
        if (hint, key) not in seen:
            seen.add((hint, key))
            out.append((hint, key, line))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Rodzaje bloków (definicje silnika)
# ---------------------------------------------------------------------------------------------------------------

def _split_selectors(prelude):
    parts, start, depth, index = [], 0, 0, 0
    while index < len(prelude):
        char = prelude[index]
        if char in '"\'':
            index = _skip_string(prelude, index)
            continue
        if char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0, depth - 1)
        elif char == ',' and depth == 0:
            parts.append(prelude[start:index])
            start = index + 1
        index += 1
    parts.append(prelude[start:])
    return parts


def normalize_selector(selector):
    """Postać kanoniczna do porównań: zwinięte białe znaki, `[data-theme="dark"]` z cudzysłowem."""
    value = re.sub(r'\s+', ' ', selector.strip())
    value = re.sub(r'\[([^\]]*)\]', lambda m: '[' + re.sub(r'\s*=\s*', '=', m.group(1).strip()) + ']', value)
    return re.sub(r'\[data-theme=(?:"dark"|\'dark\'|dark)\]', '[data-theme="dark"]', value)


def _at_rule(prelude):
    m = re.match(r'@([-\w]+)\s*(.*)', prelude, re.DOTALL)
    return (m.group(1).lower(), m.group(2).strip().lower()) if m else ('', '')


def _is_layer(prelude):
    return prelude.startswith('@') and _at_rule(prelude)[0] == 'layer'


def _is_media_dark(prelude):
    keyword, rest = _at_rule(prelude) if prelude.startswith('@') else ('', '')
    return keyword == 'media' and re.search(r'prefers-color-scheme\s*:\s*dark', rest) is not None


def block_kinds(stack):
    """Rodzaje bloku deklaracji o stosie `stack`: podzbiór {light, dark, media-dark} (pusty, gdy to nie motyw)."""
    if not stack:
        return frozenset()
    *outer, last = stack
    if last.startswith('@'):
        keyword, rest = _at_rule(last)
        if keyword == 'theme' and rest in ('', 'inline') and all(_is_layer(p) for p in outer):
            return frozenset({LIGHT})
        return frozenset()
    if all(_is_layer(p) for p in outer):
        selectors = {normalize_selector(s) for s in _split_selectors(last)}
        kinds = set()
        if selectors & LIGHT_SELECTORS:
            kinds.add(LIGHT)
        if selectors & DARK_SELECTORS:
            kinds.add(DARK)
        return frozenset(kinds)
    if len(outer) >= 1 and _is_media_dark(outer[-1]) and all(_is_layer(p) for p in outer[:-1]):
        return frozenset({MEDIA_DARK})
    return frozenset()


def is_dark(stack):
    """Blok ciemny wg silnika (bez `@media`)."""
    return DARK in block_kinds(stack)


def is_media_dark(stack):
    """Blok w `@media (prefers-color-scheme: dark)`."""
    return MEDIA_DARK in block_kinds(stack)


def is_root_block(stack):
    """Jasny blok wg silnika (`:root`, `@theme`, `@theme inline`)."""
    return LIGHT in block_kinds(stack)


class ThemeBlock:
    """Blok tokenów motywu: rodzaj, klucz (stos selektorów) i tokeny nazwa -> (wartość, linia)."""

    def __init__(self, kind, key):
        self.kind = kind
        self.key = key
        self.tokens = {}


def parse_theme(css):
    """Lista `ThemeBlock` w kolejności pierwszego wystąpienia; deklaracje tego samego rodzaju i klucza są scalane
    (późniejsza wygrywa, jak w kaskadzie), deklaracja w bloku o dwóch rodzajach (`:root, .dark`) trafia do obu."""
    blocks = {}
    order = []
    for stack, name, value, line in css_declarations_lines(css):
        for kind in sorted(block_kinds(stack)):
            key = ' / '.join(stack)
            block = blocks.get((kind, key))
            if block is None:
                block = blocks[(kind, key)] = ThemeBlock(kind, key)
                order.append(block)
            block.tokens[name] = (value, line)
    return order


def theme_tokens(css, blocks=None, media_dark_as_dark=False):
    """Zwraca (jasne, ciemne). Gdy podano listę `blocks`, trafia do niej słownik token -> znormalizowana wartość
    każdego bloku ciemnego osobno (klucz: stos selektorów bloku), w kolejności pierwszego wystąpienia.
    `media_dark_as_dark` zalicza do ciemnych także bloki w `@media (prefers-color-scheme: dark)` (tryb strażnika skilla)."""
    light, dark = set(), set()
    per_block = {}
    for stack, name, value in css_declarations(css, with_values=True):
        kinds = block_kinds(stack)
        if DARK in kinds or (media_dark_as_dark and MEDIA_DARK in kinds):
            dark.add(name)
            per_block.setdefault(' / '.join(stack), {})[name] = value
        if LIGHT in kinds:
            light.add(name)
    if blocks is not None:
        blocks.extend(per_block.items())
    return light, dark


# ---------------------------------------------------------------------------------------------------------------
# Tokeny używane przez zasoby szablonu
# ---------------------------------------------------------------------------------------------------------------

STYLE_ATTR_RE = re.compile(r'''\bstyle\s*=\s*("[^"]*"|'[^']*')''', re.I)
VAR_RE = re.compile(r'var\(\s*(--[\w-]+)')
DECL_RE = re.compile(r'(?<![\w-])(--[\w-]+)\s*:')


def used_tokens_in_text(text, is_html):
    """(użyte, lokalnie zdefiniowane) w jednym pliku. Deklaracja `--x:` w atrybucie `style` obowiązuje tylko dla
    tego atrybutu: nie zasłania tokenu użytego gdzie indziej, a `var(--x)` obok własnej deklaracji w tym samym
    atrybucie nie jest użyciem tokenu motywu."""
    strings = not is_html
    text = strip_css_comments(text, strings=strings)
    used, local = set(), set()
    if is_html:
        for m in STYLE_ATTR_RE.finditer(text):
            body = m.group(1)[1:-1]
            used.update(set(VAR_RE.findall(body)) - set(DECL_RE.findall(body)))
        text = STYLE_ATTR_RE.sub(' ', text)
    used.update(VAR_RE.findall(text))
    local.update(name for _, name in css_declarations(text, strings=strings))
    local.update(DECL_RE.findall(text))
    return used, local


def used_tokens(assets_dir):
    """Tokeny używane przez zasoby szablonu: var(--x) w *.css i *.html, bez zmiennych definiowanych lokalnie."""
    used, local = set(), set()
    for path in sorted(Path(assets_dir).rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.css', '.html'):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('%s: plik większy niż 2 MiB' % path.name)
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('%s: plik nie jest poprawnym UTF-8' % path.name) from None
        u, l = used_tokens_in_text(text, path.suffix.lower() == '.html')
        used |= u
        local |= l
    return {t for t in used - local if not t.startswith(ENGINE_PREFIX)}


_LINE_END = re.compile(r'\r\n|\r|\n')   # like `common.file_lines` of the generator: a rare separator (VT, FF, NEL, LS, PS) does not end a line


def read_required(path):
    return [ln.strip() for ln in _LINE_END.split(Path(path).read_text(encoding='utf-8-sig'))
            if ln.strip() and not ln.strip().startswith('#')]
