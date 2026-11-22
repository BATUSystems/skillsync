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
