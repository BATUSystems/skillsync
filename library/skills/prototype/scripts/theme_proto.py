"""Reguły stylu prototypu (kontrola `prototype` w `theme_check.py`; Python 3.11+, tylko biblioteka standardowa).

Czysta logika bez wejścia/wyjścia: deklaracje z arkuszy CSS, bloków `<style>` i atrybutów `style` w HTML sprawdzane
względem reguł (`color`, `font`, `radius`, `weight`, `lineHeight`, `size`, `zIndex`) i zbioru znanych tokenów. Deklaracje
czyta wspólny parser `css_tokens` (komentarze zastąpione z zachowaniem numerów linii, napisy, nawiasy).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import css_tokens as ct  # noqa: E402

COLOR_FUNC_RE = re.compile(r'(?<![\w-])(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|device-cmyk|light-dark)\s*\(', re.I)
HEX_RE = re.compile(r'#[0-9a-fA-F]{3,8}(?![\w-])')
SIZE_RE = re.compile(r'(?<![\w.-])-?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|pt|pc|cm|mm|in|vw|vh|vmin|vmax|ch|ex|%)(?![\w-])', re.I)
WORD_RE = re.compile(r'(?<![\w-])[A-Za-z]+(?![\w-])')
WEIGHT_KEYWORD_RE = re.compile(r'(?<![\w-])(normal|bold|bolder|lighter)(?![\w-])', re.I)
RADIUS_PROP_RE = re.compile(r'(?:-[a-z]+-)?border-(?:[a-z]+-[a-z]+-)?radius')  # także prefiksy vendor (-webkit-, -moz-)
STYLE_BLOCK_RE = re.compile(r'<style\b[^>]*>([\s\S]*?)</style>', re.I)
ZINDEX_TOKEN_PREFIX = '--z-index-'
ZINDEX_MODES = ('tokens', 'forbid', 'allow')
RULE_KEYS = ('color', 'font', 'radius', 'weight', 'lineHeight', 'size', 'zIndex')

# Nazwane kolory CSS i kolory systemowe (literał koloru w wartości jest zabroniony; `transparent` i `currentcolor` nie).
NAMED_COLORS = frozenset('''aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan
darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen
darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey
darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite
forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink
indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral
lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta
maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red
rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white
whitesmoke yellow yellowgreen
canvas canvastext linktext visitedtext activetext buttonface buttontext buttonborder field
fieldtext highlight highlighttext selecteditem selecteditemtext mark marktext graytext accentcolor accentcolortext'''.split())


def scan_vars(value):
    """Rozbija wartość na (nazwy tokenów, fallbacki, reszta): każde `var(--x, fallback)` jest wycinane z wartości
    (nawiasy zrównoważone), fallback zwracany osobno (skanowany jak zwykła wartość)."""
    names, fallbacks, rest, i = [], [], [], 0
    while i < len(value):
        if value[i:i + 4].lower() == 'var(' and (i == 0 or not (value[i - 1].isalnum() or value[i - 1] in '-_')):
            depth, j = 1, i + 4
            while j < len(value) and depth:
                depth += (value[j] == '(') - (value[j] == ')')
                j += 1
            inner = value[i + 4:j - 1] if depth == 0 else value[i + 4:]
            name, sep, fallback = inner.partition(',')
            names.append(name.strip())
            if sep:
                fallbacks.append(fallback.strip())
            rest.append(' ')
            i = j
        else:
            rest.append(value[i])
            i += 1
    return names, fallbacks, ''.join(rest)


def mask(text):
    """Usuwa `url(...)` i napisy z wartości (zawartość nie jest stylem)."""
    text = re.sub(r'url\([^)]*\)', ' ', text, flags=re.I)
    return re.sub(r'"[^"]*"|\'[^\']*\'', ' ', text)


def decl_violations(prop, value, known, rules):
    """Naruszenia dla jednej deklaracji (lista opisów). `known`: zbiór istniejących tokenów; `rules`: słownik reguł
    (klucze z RULE_KEYS; brak klucza albo False = reguła wyłączona; `radius`: lista dozwolonych wartości)."""
    prop = prop.strip().lower()
    value = re.sub(r'\s*!important\s*$', '', value.strip(), flags=re.I)
    out = []
    names, fallbacks, rest = scan_vars(value)
    for name in names:
        if name not in known:
            out.append('nieznany token %s w %s: %s' % (name, prop, value))
    for fallback in fallbacks:  # var(--x, fallback): fallback to zwykła wartość
        out += ['fallback: ' + v for v in decl_violations(prop, fallback, known, rules)]
    masked = mask(rest)
    if rules.get('color'):
        if HEX_RE.search(masked) or COLOR_FUNC_RE.search(masked):
            out.append('literał koloru w %s: %s' % (prop, value))
        else:
            for word in WORD_RE.findall(masked):
                if word.lower() in NAMED_COLORS:
                    out.append("nazwa koloru '%s' w %s: %s" % (word, prop, value))
                    break
    # Różnica względem źródła projektowego (zaostrzenie): `var(--a), serif` to FAIL (źródło: PASS, bo wystarczyło var());
    # sama lista var() rozdzielona przecinkami jest dozwolona.
    font_rest = re.sub(r'[,\s]', '', rest)
    if rules.get('font') and prop == 'font-family' and font_rest and font_rest.lower() != 'inherit':
        out.append('font-family poza var(): %s' % value)
    if rules.get('size') and prop in ('font', 'font-size') and SIZE_RE.search(masked):
        out.append('literał rozmiaru w %s: %s' % (prop, value))
    if rules.get('weight') and prop == 'font-weight' and (re.search(r'\d', masked) or WEIGHT_KEYWORD_RE.search(masked)):
        out.append('literał font-weight (tylko var(--token)): %s' % value)
    if rules.get('lineHeight') and prop == 'line-height' and (re.search(r'\d', masked) or WEIGHT_KEYWORD_RE.search(masked)):
        out.append('literał line-height (tylko var(--token)): %s' % value)
    if rules.get('radius') is not None and RADIUS_PROP_RE.fullmatch(prop):
        allow = {a.lower() for a in rules['radius']}
        for tok in masked.replace('/', ' ').split():
            if tok.lower() not in allow:
                out.append('literał promienia w %s: %s' % (prop, value))
                break
    if prop == 'z-index':
        mode = rules.get('zIndex', 'allow')
        if mode == 'forbid':
            out.append('z-index zabroniony: %s' % value)
        elif mode == 'tokens' and (masked.strip() or any(not n.startswith(ZINDEX_TOKEN_PREFIX) for n in names)
                                   or not names):
            out.append('z-index tylko przez var(%s*): %s' % (ZINDEX_TOKEN_PREFIX, value))
    return out


def _decls(css, line_offset=0):
    """(nazwa, wartość, linia) deklaracji arkusza; komentarze nie są skanowane, numery linii zgadzają się z tekstem."""
    return [(name, value, line) for _stack, name, value, line in ct.declarations(css, line_offset)]


def css_violations(css, known, rules, line_offset=0):
    """Lista (linia, opis) dla arkusza CSS."""
    return [(line, msg) for name, value, line in _decls(css, line_offset)
            for msg in decl_violations(name, value, known, rules)]


def html_violations(html, known, rules):
    """Lista (linia, opis) dla bloków `<style>` i atrybutów `style` w HTML (numery linii względem pliku)."""
    out = []
    for m in STYLE_BLOCK_RE.finditer(html):
        out += css_violations(m.group(1), known, rules, html.count('\n', 0, m.start(1)))
    for m in ct.STYLE_ATTR_RE.finditer(html):
        start = html.count('\n', 0, m.start())
        body = m.group(1)[1:-1]
        for name, value, line in _decls('a{' + body + '}', start):
            out += [(line, msg) for msg in decl_violations(name, value, known, rules)]
    return sorted(out)


def declared_tokens(css):
    """Nazwy niestandardowych właściwości zadeklarowanych w CSS (w dowolnym bloku)."""
    return {name for _stack, name in ct.css_declarations(css)}
