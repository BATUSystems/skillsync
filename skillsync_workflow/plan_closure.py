"""Śledzony w planie dowód zamknięcia przebiegu: linia `Zamknięcie: final <data> <sha40>` (gramatyka: `protocol_tokens`).

Moduł neutralny: nie importuje `quality` ani `status` (między nimi jest cykl importów). Tu mieszkają zapis linii przez
`checkpoint --final` i wspólna ocena zamknięcia dla `quality` (doctor) i `status`; hak Stop (`library/hooks/check-closure.py`)
ma samodzielną kopię odczytu i oceny.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path

from . import protocol_tokens as tokens

MAX_PLAN_BYTES = 512 * 1024   # jak w haku: większego planu nikt nie ocenia
_HEAD_BYTES = 64 * 1024       # z nadmiarem na `HEADER_LIMIT` znaków (UTF-8, CRLF)
_SHA40 = re.compile(r'[0-9a-f]{40}')
# Wzorzec id jak `_slug` w runs.py.
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
MAX_RUN_BYTES = 5 * 1024 * 1024
_RESULT_HEAD = re.compile(r'^##\s+' + tokens.bare_alternation('result') + r'\b[^\n]*$', re.MULTILINE)
_RESULT_PLACEHOLDER = re.compile(r'^\(?\s*(?:po wykonaniu|after (?:the )?(?:run|delivery)|tbd|todo)\s*\)?\.?$', re.IGNORECASE)


def plan_has_result(text):
    """Sekcja `## Wynik` (albo `## Result`) planu ma treść; sam szablon i same nagłówki `###` się nie liczą."""
    match = _RESULT_HEAD.search(text)
    if not match:
        return False
    rest = text[match.end():]
    end = re.search(r'^##\s+\S', rest, re.MULTILINE)
    body = '\n'.join(line for line in (rest[:end.start()] if end else rest).splitlines() if not line.lstrip().startswith('#')).strip()
    return bool(body) and not _RESULT_PLACEHOLDER.match(body)


def _record_name(name, run_id):
    return name == f'{run_id}.json' or (name.startswith(f'{run_id}.') and name.endswith('.json'))


def has_final_record(root, run_id):
    """Czy `<id>.json` albo `<id>.*.json` ma rekord `final`; dowiązania, duże i nieczytelne pliki są pomijane."""
    directory = Path(root) / '.skillsync' / 'runs'
    try:
        if directory.parent.is_symlink() or directory.is_symlink() or not directory.is_dir():
            return False
        paths = sorted(directory.iterdir())
    except OSError:
        return False
    for path in paths:
        if not _record_name(path.name, run_id):
            continue
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RUN_BYTES:
                continue
            records = json.loads(path.read_text(encoding='utf-8')).get('checkpoints')
        except (OSError, ValueError, AttributeError, RecursionError):
            continue
        if isinstance(records, list) and any(isinstance(item, dict) and item.get('final') is True for item in records):
            return True
    return False


def has_local_trace(root, run_id):
    """Dowolny lokalny ślad przebiegu w `.skillsync/runs`: `<id>.json`, `<id>.*.json` albo katalog `<id>`; magazyn przez
    dowiązanie albo nieczytelny liczy się jako ślad (nie da się wykluczyć, że przebieg jest stąd)."""
    directory = Path(root) / '.skillsync' / 'runs'
    try:
        if directory.parent.is_symlink() or directory.is_symlink():
            return True
        if not directory.is_dir():
            return False
        if os.path.lexists(directory / run_id):
            return True
        return any(_record_name(path.name, run_id) for path in directory.iterdir())
    except OSError:
        return True


def run_evidence(root, run_id):
    """Co mówi lokalny magazyn o przebiegu: `final`, `foreign` (brak śladu) albo `open` (ślad bez rekordu `final`)."""
    if has_final_record(root, run_id):
        return 'final'
    return 'open' if has_local_trace(root, run_id) else 'foreign'


def closure_verdict(root, text):
    """Wspólna ocena zamknięcia planu `done`: `{'state', 'sha'}`, bez hosta i ścieżek (pole `closure` statusu).

    Linia przebiegu jak w `tokens.RUN_LINE`, w pierwszych `HEADER_LIMIT` znakach i poza blokami kodu (`Przebieg:` także w treści,
    `Run:` tylko w nagłówku); linia zamknięcia tylko w nagłówku, poza blokami kodu.

    Stany w kolejności rozstrzygania: `none` (brak linii przebiegu albo zły id), `tracked` (poprawna linia
    zamknięcia; magazyn nie jest czytany, `sha` z linii), `final` (lokalny rekord), `bad-line` (linia o złym kształcie
    i brak rekordu `final`), `foreign` (brak lokalnego śladu), `open` (ślad bez `final`). `sha` jest tylko przy `tracked`."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')   # jak hak, który czyta plan w trybie tekstowym
    match = tokens.RUN_LINE.search(text[:tokens.HEADER_LIMIT])
    if not match or not RUN_ID.fullmatch(match.group(1)):
        return {'state': 'none', 'sha': None}
    line = tokens.closure_line(text)
    if line:
        return {'state': 'tracked', 'sha': line[1]}
    state = run_evidence(root, match.group(1))
    if state != 'final' and tokens.closure_line_present(text):
        state = 'bad-line'
    return {'state': state, 'sha': None}


def _normalized(data):
    """Tekst tak, jak widzą go czytelnicy planu (uniwersalne końce linii)."""
    return data.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')


def _plans_of_run(directory, run_id):
    """Plany z linią przebiegu równą `run_id`: (z linią w nagłówku, z linią tylko w treści)."""
    in_header, in_body = [], []
    for path in sorted(directory.glob('*.md')):
        if path.name.lower() == 'readme.md':
            continue
        try:
            if not path.is_file():
                continue
            with open(path, 'rb') as handle:
                head = _normalized(handle.read(_HEAD_BYTES))[:tokens.HEADER_LIMIT]
        except OSError:
            continue
        match = tokens.RUN_LINE.search(head)
        if match and match.group(1) == run_id:
            (in_header if match.start() < len(tokens.plan_header(head)) else in_body).append(path)
    return in_header, in_body


def _rewrite(raw, run_id, day, sha):
    """Bajty planu z jedną linią zamknięcia bezpośrednio pod linią przebiegu i akcja `added|updated|none`.

    Reszta pliku zostaje bajt w bajt; nowa linia dostaje koniec linii wiersza przebiegu."""
    raw.decode('utf-8')   # ścisłe: plik spoza UTF-8 nie jest zmieniany (UnicodeDecodeError)
    lines = raw.splitlines(keepends=True)
    contents = [line.rstrip(b'\r\n') for line in lines]
    texts = [content.decode('utf-8') for content in contents]
    header_end = next((index for index, text in enumerate(texts) if text.startswith('## ')), len(texts))
    fenced = tokens.fenced_lines(texts[:header_end])   # przykłady w blokach kodu są treścią autora
    run_index = None
    for index in range(header_end):
        if index in fenced:
            continue
        match = tokens.RUN_LINE.search(texts[index])
        if match and match.group(1) == run_id:
            run_index = index
            break
    if run_index is None:
        raise ValueError('nie znaleziono linii przebiegu w nagłówku poza blokami kodu')
    stale = {index for index in range(header_end)
             if index != run_index and index not in fenced and tokens.closure_line_replaceable(texts[index])}
    ending = lines[run_index][len(contents[run_index]):]
    new_ending = ending
    if not ending:   # linia przebiegu kończy plik bez końca linii: nowa linia też go nie dostaje
        ending = next((line[len(content):] for line, content in zip(lines, contents) if len(line) > len(content)), b'\n')
    elif run_index + 1 in stale:   # odświeżenie w miejscu: koniec linii (także jego brak na końcu pliku) zostaje
        new_ending = lines[run_index + 1][len(contents[run_index + 1]):]
    stamp_line = f'{tokens.names("closure")[0]} final {day} {sha}'.encode('utf-8') + new_ending
    result = []
    for index, line in enumerate(lines):
        if index in stale:
            continue
        if index == run_index:
            result.append(contents[index] + ending)
            result.append(stamp_line)
        else:
            result.append(line)
    updated = b''.join(result)
    if updated == raw:
        return raw, 'none'
    if tokens.closure_line(_normalized(updated)) != (day, sha):
        raise ValueError(f'linia nie byłaby czytana (poza pierwszymi {tokens.HEADER_LIMIT} znakami nagłówka)')
    return updated, 'updated' if stale else 'added'


def _stamp_file(path, run_id, day, sha):
    """Zapis atomowy linii; plik zmieniony między odczytem a podmianą = jedna ponowna próba, potem błąd."""
    for _attempt in range(2):
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('plan nie jest zwykłym plikiem (dowiązanie symboliczne)')
        if info.st_size > MAX_PLAN_BYTES:
            raise ValueError('plan przekracza limit rozmiaru')
        raw = path.read_bytes()
        updated, action = _rewrite(raw, run_id, day, sha)
        if action == 'none':
            return action
        descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
            if not path.is_symlink() and path.read_bytes() == raw:
                os.replace(temporary, path)
                return action
        finally:
            Path(temporary).unlink(missing_ok=True)
    raise ValueError('plan zmienia się w trakcie zapisu')


def stamp_plan_closure(root, run_id, sha, day):
    """Wstawia albo odświeża linię zamknięcia w jedynym planie przebiegu `run_id`.

    Zwraca `({'path': <względna albo None>, 'action': 'added'|'updated'|'none'}, ostrzeżenia)`. Nigdy nie rzuca wyjątkiem:
    rekord `final` jest już zapisany i problem z planem nie może zmienić wyniku komendy."""
    result = {'path': None, 'action': 'none'}

    def skipped(reason):
        return result, [f'plan-closure: {reason}; linia `{tokens.names("closure")[0]}` nie została zapisana']

    try:
        root = Path(root)
        directory = root / 'docs' / 'plans'
        if (root / 'docs').is_symlink() or directory.is_symlink():
            return skipped('docs/plans prowadzi przez dowiązanie symboliczne')
        plans, in_body = _plans_of_run(directory, run_id) if directory.is_dir() else ([], [])
        if len(plans) > 1:
            names = ', '.join(path.relative_to(root).as_posix() for path in plans)
            return skipped(f'więcej niż jeden plan przebiegu `{run_id}` ({names})')
        if not plans:
            if in_body:
                return skipped(f'linia przebiegu `{run_id}` jest poza nagłówkiem planu '
                               f'({in_body[0].relative_to(root).as_posix()})')
            return skipped(f'brak planu przebiegu `{run_id}` w docs/plans')
        result['path'] = plans[0].relative_to(root).as_posix()
        if not isinstance(sha, str) or not _SHA40.fullmatch(sha):
            return skipped('brak 40-znakowego sha HEAD')
        result['action'] = _stamp_file(plans[0], run_id, day, sha)
        return result, []
    except UnicodeDecodeError:
        return skipped(f'{result["path"]}: plik nie jest w UTF-8')
    except (OSError, ValueError) as error:
        return skipped(f'{result["path"] or "docs/plans"}: {error}')
