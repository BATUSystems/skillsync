"""Tokeny protokołu w dwóch językach (PL/EN): jedno źródło prawdy dla generatora.

Czytamy oba języki, ZAPISUJEMY po polsku (poza zapisem w trybie `language: en`,
który obsługuje inna część systemu). Moduł jest liściem: importuje wyłącznie
bibliotekę standardową i nie ma efektów ubocznych przy imporcie.

Rodzaje (`kind`):
- `parsed`  - czytany przez parser generatora (oba języki muszą działać),
- `written` - zapisywany przez rdzeń (zostaje polski),
- `skills`  - dotyczy tylko warstwy skilli i dashboardu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    """Para token polski / angielski z rodzajem użycia."""
    pl: str
    en: str
    kind: str


def _t(pl: str, en: str, kind: str) -> Token:
    return Token(pl, en, kind)


TOKENS: dict[str, Token] = {
    'run': _t('Przebieg:', 'Run:', 'parsed'),
    'closure': _t('Zamknięcie:', 'Closure:', 'parsed'),
    'verdict': _t('Werdykt:', 'Verdict:', 'parsed'),
    'acceptance': _t('Odbiór', 'Acceptance', 'parsed'),
    'summary': _t('Streszczenie dla człowieka', 'Summary for humans', 'parsed'),
    'contracts': _t('Kontrakty', 'Contracts', 'parsed'),
    'migration': _t('Migracja', 'Migration', 'parsed'),
    'data': _t('Dane', 'Data', 'parsed'),
    'not_applicable': _t('nie dotyczy', 'not applicable', 'parsed'),
    'result': _t('Wynik', 'Result', 'parsed'),
    'step': _t('Krok', 'Step', 'parsed'),
    'expert': _t('[EKSPERT]', '[EXPERT]', 'parsed'),
    'assumption': _t('[ZAŁOŻENIE]', '[ASSUMPTION]', 'skills'),
    'handoff': _t('Oddanie:', 'Handoff:', 'skills'),
    'directory': _t('Katalog:', 'Directory:', 'skills'),
    'prototype': _t('Prototyp:', 'Prototype:', 'parsed'),
    'prototype_gate': _t('Bramka prototypu: pominięta', 'Prototype gate: skipped', 'parsed'),
    'plan_risk': _t('Ryzyko planu:', 'Plan risk:', 'skills'),
    'domain_terminology': _t('Terminologia domenowa', 'Domain terminology', 'skills'),
    'retro_signals': _t('Retro: sygnały', 'Retro: signals', 'skills'),
    'retro_proposals': _t('propozycje', 'proposals', 'skills'),
    'decision_apply': _t('Wprowadź', 'Apply', 'skills'),
    'decision_variant': _t('Wariant', 'Variant', 'skills'),
    'decision_defer': _t('Odłóż', 'Defer', 'skills'),
    'decision_keep': _t('Zostaw', 'Keep', 'skills'),
    'answer_marker': _t('— odpowiedź:', '— answer:', 'written'),
    'expert_marker': _t('— ekspert:', '— expert:', 'written'),
    'expert_ref': _t('(ekspert:', '(expert:', 'written'),
    'assumed_marker': _t('przyjęte:', 'assumed:', 'written'),
    'yes': _t('TAK', 'YES', 'skills'),
    'no': _t('NIE', 'NO', 'skills'),
    'goal': _t('Cel', 'Goal', 'parsed'),
    'criteria': _t('Kryteria', 'Criteria', 'parsed'),
    'tests': _t('TESTY', 'TESTS', 'parsed'),
    'knowledge': _t('Wiedza', 'Knowledge', 'parsed'),
    'risks': _t('Ryzyka', 'Risks', 'parsed'),
    'waiting': _t('czeka na', 'waiting for', 'parsed'),
    'open_questions': _t('Pytania otwarte', 'Open questions', 'skills'),
    'readiness_date': _t('Data:', 'Date:', 'skills'),
    'review_code': _t('kod', 'code', 'parsed'),
    'review_prototype': _t('prototyp', 'prototype', 'parsed'),
    'sketch': _t('szkic', 'sketch', 'parsed'),
    # Wartości segmentu `czeka:`/`waiting:` czytane przez dashboard (parser po stronie dashboard.py) i słowo zgody `POPRAW` (protokół wiadomości w skillu):
    'waiting_acceptance': _t('akceptacja prototypu', 'acceptance of prototype', 'skills'),
    'waiting_answer': _t('odpowiedź', 'answer', 'skills'),
    'fix_word': _t('POPRAW', 'FIX', 'skills'),
}

# Pary sekcji kontroli gotowości (`readiness`): nagłówek pasuje do któregokolwiek członka pary.
READINESS_PAIRS: dict[str, tuple[str, str]] = {
    key: (TOKENS[key].pl, TOKENS[key].en)
    for key in ('goal', 'criteria', 'tests', 'knowledge', 'risks', 'contracts', 'migration')
}

# Płaska mapa polski -> angielski (wszystkie pozycje, także `written` i `skills`).
PL_TO_EN: dict[str, str] = {t.pl: t.en for t in TOKENS.values()}


def names(key: str) -> tuple[str, str]:
    """Zwraca (polski, angielski) dla klucza; KeyError dla nieznanego klucza."""
    tok = TOKENS[key]
    return (tok.pl, tok.en)


def alternation(*names_: str) -> str:
    """Nieprzechwytująca alternatywa `(?:a|b|...)`: elementy escapowane, unikalne, najdłuższy pierwszy."""
    if not names_:
        raise ValueError('alternation wymaga co najmniej jednej nazwy')
    unique = sorted(set(names_), key=lambda n: (-len(n), n))
    return '(?:' + '|'.join(re.escape(n) for n in unique) + ')'


def ci(pattern: str) -> str:
    """Opakowuje wzorzec w lokalną flagę ignorowania wielkości liter `(?i:...)`."""
    return '(?i:' + pattern + ')'


def token_alternation(key: str) -> str:
    """Alternatywa obu języków tokenu o danym kluczu."""
    return alternation(*names(key))


def bare_names(key: str) -> tuple[str, str]:
    """Nazwy (polska, angielska) bez końcowego dwukropka, np. `('Werdykt', 'Verdict')` dla `verdict`."""
    return tuple(n[:-1] if n.endswith(':') else n for n in names(key))  # type: ignore[return-value]


def bare_alternation(key: str, *extra: str, capture: bool = False) -> str:
    """Alternatywa nazw tokenu bez dwukropka (plus ewentualne `extra`); `capture=True` daje jedną grupę przechwytującą zamiast `(?:...)`."""
    alt = alternation(*bare_names(key), *extra)
    return '(' + alt[3:] if capture else alt


def _run_regex(name: str) -> re.Pattern[str]:
    # Gramatyka linii identyczna jak dawny `quality._PLAN_RUN`.
    return re.compile('^' + re.escape(name) + r'[ \t]*`?([^\s`]*)`?[ \t\r]*$', re.MULTILINE)


_SECTION_LINE = re.compile(r'^## ', re.MULTILINE)


class RunLine:
    """Linia przebiegu planu: polskie `Przebieg:` w całym tekście, angielskie `Run:` tylko w nagłówku.

    Nagłówek to tekst przed pierwszą linią zaczynającą się od `## ` (brak takiej linii: cały tekst).
    Gdy pasują oba, wygrywa linia położona wcześniej. Zwracany jest prawdziwy `re.Match`.
    """

    def __init__(self) -> None:
        pl, en = names('run')
        self._pl = _run_regex(pl)
        self._en = _run_regex(en)

    def search(self, text: str) -> re.Match[str] | None:
        pl_match = self._pl.search(text)
        section = _SECTION_LINE.search(text)
        header_end = section.start() if section else len(text)
        en_match = self._en.search(text, 0, header_end)
        if pl_match is None:
            return en_match
        if en_match is None:
            return pl_match
        return en_match if en_match.start() < pl_match.start() else pl_match


RUN_LINE = RunLine()


# Linia zamknięcia planu: `Zamknięcie: final <data UTC YYYY-MM-DD> <sha40>` (alias `Closure:`). W odróżnieniu od `Przebieg:` oba języki
# są czytane tylko w nagłówku (przed pierwszym `## `, w pierwszych `HEADER_LIMIT` znakach), żeby cytat w treści planu go nie zamykał.
HEADER_LIMIT = 4000
_CLOSURE_LINE = re.compile('^' + alternation(*names('closure'))
                           + r'[ \t]*final[ \t]+([0-9]{4}-[0-9]{2}-[0-9]{2})[ \t]+([0-9a-fA-F]{40})[ \t\r]*$', re.MULTILINE)
_CLOSURE_ANY = re.compile('^' + alternation(*names('closure')), re.MULTILINE)
# Wiersz, który zapis linii może zastąpić: sam token albo token z `final …`; inna treść po tokenie to tekst autora planu.
_CLOSURE_REPLACEABLE = re.compile('^' + alternation(*names('closure')) + r'[ \t]*(?:final\b.*)?[ \t\r]*$')


def plan_header(text: str) -> str:
    """Nagłówek planu: pierwsze `HEADER_LIMIT` znaków (bez linii przeciętej limitem) do pierwszej linii zaczynającej się od `## `."""
    head = text[:HEADER_LIMIT]
    if text[HEADER_LIMIT:HEADER_LIMIT + 1] not in ('', '\r', '\n'):
        head = head[:head.rfind('\n') + 1]
    section = _SECTION_LINE.search(head)
    return head[:section.start()] if section else head


def closure_line(text: str) -> tuple[str, str] | None:
    """`(data, sha)` pierwszej poprawnej linii zamknięcia w nagłówku planu albo None."""
    match = _CLOSURE_LINE.search(plan_header(text))
    return (match.group(1), match.group(2)) if match else None


def closure_line_present(text: str) -> bool:
    """Czy nagłówek ma wiersz zaczynający się od tokenu zamknięcia (przy `closure_line` równym None: linia o złym kształcie)."""
    return _CLOSURE_ANY.search(plan_header(text)) is not None


def closure_line_replaceable(line: str) -> bool:
    """Czy pojedynczy wiersz (bez końca linii) to linia zamknięcia, także pusta lub o złym kształcie po `final`."""
    return _CLOSURE_REPLACEABLE.match(line) is not None


# Sekcja ryzyka: polskie słowo gdziekolwiek w tytule, angielskie tylko na jego początku (po numeracji i znacznikach).
_RISK_KEYS = ('contracts', 'migration', 'data')
_RISK_PL = re.compile(ci(r'\b' + alternation(*(TOKENS[k].pl for k in _RISK_KEYS)) + r'\b'))
_RISK_EN_PREFIX = re.compile(r'^[\s*_`]*(?:\d+(?:\.\d+)*[.)]?[\s*_`]*)?')
_RISK_EN = re.compile(ci(alternation(*(TOKENS[k].en for k in _RISK_KEYS)) + r'(?:[\s*_`]*(?:$|[:\-–—/,+&(])|\s+and\b)'))


def risk_section_title(title: str) -> bool:
    """Czy tytuł nagłówka nazywa sekcję ryzyka (Kontrakty/Migracja/Dane albo Contracts/Migration/Data na początku tytułu)."""
    if _RISK_PL.search(title):
        return True
    return _RISK_EN.match(title, _RISK_EN_PREFIX.match(title).end()) is not None


_ID = r'(?:[A-Za-z]+\d+(?:\.\d+)?\s+)?'
# Tytuł kroku akceptacyjnego (po opcjonalnym id kroku): `Odbiór` albo `Acceptance` jako cały tytuł lub przed `: – — - (`.
ACCEPTANCE_PATTERN = _ID + r'(?:Odbi[oó]r\b|Acceptance(?:[ \t\r]*$|[ \t]*[:–—(]|[ \t]+-))'
_ACCEPTANCE_TITLE = re.compile('^[ \\t]*' + ci(ACCEPTANCE_PATTERN))


def acceptance_title(title: str) -> bool:
    """Czy tytuł kroku planu to krok akceptacyjny (`Odbiór` albo `Acceptance`)."""
    return _ACCEPTANCE_TITLE.match(title) is not None
