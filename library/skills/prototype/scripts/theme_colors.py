"""Kolory CSS do sRGB i kontrast WCAG 2.x (Python 3.11+, tylko biblioteka standardowa); moduł kontroli `contrast` w
`theme_check.py`.

Obsługiwane formaty: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`/`rgba()` (składnia z przecinkami i ze spacjami
i `/`, liczby i procenty, alfa), `hsl()`/`hsla()`, słowa `white`, `black`, `transparent` oraz `oklch()`, `oklab()`,
`lab()`, `lch()` (konwersja wg CSS Color 4 do sRGB, przycięcie do gamutu [0, 1] z flagą `out_of_gamut`). Wszystko inne
(`color()`, `color-mix()`, `hwb()`, nazwane kolory spoza listy, `calc()`, `none`, `L` w procentach) to `ColorFormat`
(UNVERIFIED), a nie wyjątek kontroli ani PASS. Alias `var(--x)` jest rozwiązywany do 5 poziomów; cykl i zbyt głęboki
łańcuch to `ColorFormat` z powodem `alias`.

Kontrast: luminancja WCAG 2.x z progiem 0,04045, arytmetyka zmiennoprzecinkowa, bez zaokrąglania stosunku przed werdyktem.
"""
import math
import re

MAX_ALIAS = 5
NUM = r'[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?'
NUM_RE = re.compile(NUM + r'(?:%|deg|grad|rad|turn)?')
ALIAS_RE = re.compile(r'var\(\s*(--[\w-]+)\s*(?:,(.*))?\)', re.S)
TOKEN_NAME_RE = re.compile(r'--[\w-]+')
FUNC_RE = re.compile(r'([a-z]+)\((.*)\)', re.S)
HEX_RE = re.compile(r'#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})')
GAMUT_EPS = 5e-4  # tolerancja flagi outOfGamut (wartości referencyjne mają 5 cyfr znaczących)

# CSS Color 4: OKLab -> LMS' (M2 odwrócona), LMS -> XYZ D65 (M1 odwrócona), XYZ D65 -> liniowy sRGB.
OKLAB_TO_LMS = ((1.0, 0.3963377773761749, 0.2158037573099136),
                (1.0, -0.1055613458156586, -0.0638541728258133),
                (1.0, -0.0894841775298119, -1.2914855480194092))
LMS_TO_XYZ = ((1.2268798758459243, -0.5578149944602171, 0.2813910456659647),
              (-0.0405757452148008, 1.1122868032803170, -0.0716110580655164),
              (-0.0763729366746601, -0.4214933324022432, 1.5869240198367816))
XYZ_TO_LINEAR_SRGB = ((3.2409699419045226, -1.537383177570094, -0.4986107602930034),
                      (-0.9692436362808796, 1.8759675015077202, 0.04155505740717559),
                      (0.05563007969699366, -0.20397695888897652, 1.0569715142428786))
D50_WHITE = (0.3457 / 0.3585, 1.0, (1.0 - 0.3457 - 0.3585) / 0.3585)
D50_TO_D65 = ((0.9554734527042182, -0.023098536874261423, 0.0632593086610217),
              (-0.028369706963208136, 1.0099954580058226, 0.021041398966943008),
              (0.012314001688319899, -0.020507696433477912, 1.3303659366080753))
KAPPA, EPSILON = 24389 / 27, 216 / 24389


class ColorFormat(Exception):
    """Wartość, której nie umiemy zmierzyć (UNVERIFIED); `reason`: `color-format` albo `alias`."""

    def __init__(self, message, reason='color-format'):
        super().__init__(message)
        self.reason = reason


class MissingToken(Exception):
    """Token wskazany przez parę albo alias nie jest zdefiniowany w jasnym bloku (FAIL)."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name


class Color:
    """Kolor sRGB (kanały w [0, 1], niepremultiplikowane) z alfą i flagą przycięcia do gamutu."""

    def __init__(self, r, g, b, a=1.0, out_of_gamut=False):
        self.r, self.g, self.b, self.a, self.out_of_gamut = r, g, b, a, out_of_gamut

    def rgb(self):
        return (self.r, self.g, self.b)

    def as_255(self):
        return tuple(int(round(c * 255)) for c in self.rgb())


def _clamp(value):
    return min(1.0, max(0.0, value))


def _mul(matrix, vec):
    return tuple(sum(row[i] * vec[i] for i in range(3)) for row in matrix)


def _encode(linear):
    sign = -1.0 if linear < 0 else 1.0
    v = abs(linear)
    out = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    return sign * out


def _from_linear(lin, alpha):
    enc = tuple(_encode(v) for v in lin)
    gamut = any(v < -GAMUT_EPS or v > 1 + GAMUT_EPS for v in enc)
    return Color(*(_clamp(v) for v in enc), alpha, gamut)


def oklab_to_srgb(lightness, a, b, alpha=1.0):
    lms = tuple(v ** 3 for v in _mul(OKLAB_TO_LMS, (lightness, a, b)))
    return _from_linear(_mul(XYZ_TO_LINEAR_SRGB, _mul(LMS_TO_XYZ, lms)), alpha)


def lab_to_srgb(lightness, a, b, alpha=1.0):
    fy = (lightness + 16) / 116
    fx, fz = a / 500 + fy, fy - b / 200
    x = fx ** 3 if fx ** 3 > EPSILON else (116 * fx - 16) / KAPPA
    y = fy ** 3 if lightness > KAPPA * EPSILON else lightness / KAPPA
    z = fz ** 3 if fz ** 3 > EPSILON else (116 * fz - 16) / KAPPA
    xyz50 = (x * D50_WHITE[0], y * D50_WHITE[1], z * D50_WHITE[2])
    return _from_linear(_mul(XYZ_TO_LINEAR_SRGB, _mul(D50_TO_D65, xyz50)), alpha)


def hsl_to_srgb(h, s, l, alpha=1.0):
    h = h % 360.0
    s, l = _clamp(s), _clamp(l)
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = l - c / 2
    sector = int(h // 60) % 6
    r, g, b = ((c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x))[sector]
    return Color(_clamp(r + m), _clamp(g + m), _clamp(b + m), alpha)


# ---------------------------------------------------------------------------------------------------------------
# Składnia
# ---------------------------------------------------------------------------------------------------------------

def _number(token, what, percent=None, allow_angle=False):
    """Liczba z tokenu. `percent`: mnożnik procentu (1% = percent) albo None (procent niedozwolony → ColorFormat)."""
    if not NUM_RE.fullmatch(token):
        raise ColorFormat('%s: nieobsługiwana składnia' % what)
    m = re.fullmatch('(' + NUM + ')(%|deg|grad|rad|turn)?', token)
    value, unit = float(m.group(1)), m.group(2)
    if unit == '%':
        if percent is None:
            raise ColorFormat('%s: procent nieobsługiwany' % what)
        return value * percent
    if unit in ('deg', 'grad', 'rad', 'turn'):
        if not allow_angle:
            raise ColorFormat('%s: jednostka kąta nieobsługiwana' % what)
        return value * {'deg': 1.0, 'grad': 0.9, 'rad': 180 / math.pi, 'turn': 360.0}[unit]
    return value


def _args(body, legacy_ok):
    """(składniki, alfa albo None) z treści funkcji koloru: składnia ze spacjami i `/` albo z przecinkami."""
    body = body.strip()
    if '(' in body or ')' in body:
        raise ColorFormat('zagnieżdżona funkcja (calc, var, ...) nieobsługiwana')
    if re.search(r'(?<![\w-])none(?![\w-])', body):
        raise ColorFormat('wartość none nieobsługiwana')
    if ',' in body:
        if not legacy_ok or '/' in body:
            raise ColorFormat('składnia z przecinkami nieobsługiwana dla tej funkcji')
        parts = [p.strip() for p in body.split(',')]
        if len(parts) not in (3, 4) or not all(parts):
            raise ColorFormat('zła liczba składników')
        return parts[:3], (parts[3] if len(parts) == 4 else None)
    parts = body.replace('/', ' / ').split()
    if '/' in parts:
        idx = parts.index('/')
        comps, rest = parts[:idx], parts[idx + 1:]
        if len(rest) != 1 or '/' in rest:
            raise ColorFormat('zła liczba składników')
    else:
        comps, rest = parts, []
    if len(comps) != 3:
        raise ColorFormat('zła liczba składników')
    return comps, (rest[0] if rest else None)


def _alpha(token):
    if token is None:
        return 1.0
    return _clamp(_number(token, 'alfa', percent=0.01))


def _parse_function(name, body):
    legacy = name in ('rgb', 'rgba', 'hsl', 'hsla')
    comps, alpha_tok = _args(body, legacy)
    alpha = _alpha(alpha_tok)
    if name in ('rgb', 'rgba'):
        rgb = [_clamp(_number(c, 'rgb', percent=1.0) / 100 if c.endswith('%') else _number(c, 'rgb') / 255)
               for c in comps]
        return Color(*rgb, alpha)
    if name in ('hsl', 'hsla'):
        h = _number(comps[0], 'hsl', allow_angle=True)
        s = _number(comps[1], 'hsl', percent=1.0) / 100
        l = _number(comps[2], 'hsl', percent=1.0) / 100
        return hsl_to_srgb(h, s, l, alpha)
    if name in ('oklab', 'oklch', 'lab', 'lch'):
        lightness = _number(comps[0], name)  # procent w L to ColorFormat (percent=None)
        second = _number(comps[1], name)
        if name in ('oklab', 'lab'):
            third = _number(comps[2], name)
            fn = oklab_to_srgb if name == 'oklab' else lab_to_srgb
            return fn(lightness, second, third, alpha)
        hue = math.radians(_number(comps[2], name, allow_angle=True))
        a, b = second * math.cos(hue), second * math.sin(hue)
        fn = oklab_to_srgb if name == 'oklch' else lab_to_srgb
        return fn(lightness, a, b, alpha)
    raise ColorFormat('funkcja %s() nieobsługiwana' % name)


def parse_color(value):
    """Literał koloru → `Color`; `ColorFormat` dla formatów spoza listy (także błędnie zapisanych)."""
    text = value.strip().lower()
    if text.endswith('!important'):
        text = text[:-len('!important')].strip()
    if text == 'white':
        return Color(1.0, 1.0, 1.0)
    if text == 'black':
        return Color(0.0, 0.0, 0.0)
    if text == 'transparent':
        return Color(0.0, 0.0, 0.0, 0.0)
    if text.startswith('#'):
        if not HEX_RE.fullmatch(text):
            raise ColorFormat('zły zapis szesnastkowy')
        digits = text[1:]
        if len(digits) in (3, 4):
            digits = ''.join(c * 2 for c in digits)
        vals = [int(digits[i:i + 2], 16) / 255 for i in range(0, len(digits), 2)]
        return Color(vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else 1.0)
    m = FUNC_RE.fullmatch(text)
    if m:
        return _parse_function(m.group(1), m.group(2))
    raise ColorFormat('format koloru nieobsługiwany')


def resolve_color(expr, tokens):
    """Kolor z wyrażenia pary: nazwa tokenu (`--x`), literał koloru albo `var(--x)`; aliasy do MAX_ALIAS poziomów.
    `tokens`: nazwa → wartość z jasnego bloku. Brak tokenu to `MissingToken`, reszta niemierzalna to `ColorFormat`."""
    value = expr.strip()
    seen, hops = [], 0
    if TOKEN_NAME_RE.fullmatch(value):
        value = 'var(%s)' % value
        hops = -1  # pierwsze wejście w token to nie alias
    while True:
        m = ALIAS_RE.fullmatch(value)
        if not m:
            return parse_color(value)
        name = m.group(1)
        if name in seen:
            raise ColorFormat('cykl aliasów (%s)' % ' -> '.join(seen + [name]), 'alias')
        if name not in tokens:
            if m.group(2) is not None:
                raise ColorFormat('var() z wartością zapasową nieobsługiwane przy niezdefiniowanym tokenie')
            raise MissingToken(name)
        hops += 1
        if hops > MAX_ALIAS:
            raise ColorFormat('łańcuch aliasów dłuższy niż %d poziomów' % MAX_ALIAS, 'alias')
        seen.append(name)
        value = tokens[name].strip()


# ---------------------------------------------------------------------------------------------------------------
# WCAG 2.x
# ---------------------------------------------------------------------------------------------------------------

def luminance(rgb):
    def lin(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _over(top, bottom_rgb):
    a = top.a
    return tuple(a * t + (1 - a) * b for t, b in zip(top.rgb(), bottom_rgb))


def contrast_ratio(fg, bg):
    """Stosunek kontrastu WCAG 2.x (niezaokrąglony): bg z alfą nad białym, fg z alfą nad bg."""
    bg_rgb = _over(bg, (1.0, 1.0, 1.0))
    fg_rgb = _over(fg, bg_rgb)
    lf, lb = luminance(fg_rgb), luminance(bg_rgb)
    hi, lo = (lf, lb) if lf >= lb else (lb, lf)
    return (hi + 0.05) / (lo + 0.05)
