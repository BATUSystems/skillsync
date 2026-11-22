"""Interface-text catalog (`data/i18n/<lang>.json`) and the manifest key `language`.

The catalog is flat (`srv.wait.generic`), values are either a string or an object of plural forms
(`one`/`few`/`many`/`other`) and parameters are written `{name}`. Keys `srv.*` are translated on the server (the sentences of the
status JSON are ready); keys `ui.*` go to the renderer (`catalog_for_client`) in the status JSON field `texts` together with `lang`;
a renderer without `texts` falls back to its built-in Polish.
`resolve_language` is the only source of truth for the manifest key `language` (default `pl`; unknown value = warning and `pl`).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DEFAULT_LANGUAGE = 'pl'
SUPPORTED_LANGUAGES = ('pl', 'en')
CATALOG_DIR = Path(__file__).resolve().parent / 'data' / 'i18n'
_PLURAL_FORMS = ('one', 'few', 'many', 'other')
_PARAM = re.compile(r'\{(\w+)\}')


def _safe(value, limit=40):
    """A short single-line rendering of an untrusted value for a warning (no control characters, bounded)."""
    text = value if isinstance(value, str) else type(value).__name__
    text = ''.join(ch if ch.isprintable() and ch not in '\r\n' else '?' for ch in text)
    return text[:limit] + ('…' if len(text) > limit else '')


def resolve_language(manifest):
    """`(language, warning)` for a project manifest (a mapping, or `None`). Missing key = `pl` without a warning;
    anything that is not exactly a supported language code = `pl` plus a warning (never an error: a manifest may be written
    before the catalog of its language exists)."""
    value = manifest.get('language') if isinstance(manifest, dict) else None
    if value is None:
        return DEFAULT_LANGUAGE, None
    if isinstance(value, str) and value in SUPPORTED_LANGUAGES:
        return value, None
    return DEFAULT_LANGUAGE, (f"manifest key 'language' must be one of {', '.join(SUPPORTED_LANGUAGES)} "
                              f"(got {_safe(value)!r}); using '{DEFAULT_LANGUAGE}'")


def plural_category(lang, n):
    """CLDR-like plural category of the integer `n` for `lang`; non-integers (also booleans, strings, negatives) are `other`."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return 'other'
    if lang == 'pl':
        if n == 1:
            return 'one'
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return 'few'
        return 'many'
    return 'one' if n == 1 else 'other'


def pick_plural(lang, forms, n):
    """The text for `n` from an object of plural forms: its own category, then `other`, then `many`."""
    for key in (plural_category(lang, n), 'other', 'many'):
        if key in forms:
            return forms[key]
    return next(iter(forms.values()), '')


def fmt(template, **params):
    """Substitutes `{name}` once from `params`; unknown placeholders stay, and substituted values are never re-read."""
    return _PARAM.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), template)


@lru_cache(maxsize=None)
def _load(lang):
    return json.loads((CATALOG_DIR / f'{lang}.json').read_text(encoding='utf-8'))


def catalog(lang):
    """The whole catalog of `lang` (a copy); an unsupported language gets the Polish one."""
    return dict(_load(lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE))


def catalog_for_client(lang):
    """The part of the catalog the renderer receives in the status JSON (`texts`): keys `ui.*`. The server keys (`srv.*`) are
    translated on the server and stay in the sentences of the JSON."""
    return {key: value for key, value in catalog(lang).items() if key.startswith('ui.')}


def t(lang, key, n=None, **params):
    """Text for `key`; plural forms are chosen with `n` (also available as `{n}`). Unknown key returns the key itself."""
    value = catalog(lang).get(key)
    if value is None:
        return key
    if isinstance(value, dict):
        value = pick_plural(lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE, value, n)
    return fmt(value, **({'n': n} if n is not None else {}), **params)
