"""Śledzony w planie dowód zamknięcia przebiegu: linia `Zamknięcie: final <data> <sha40>` (gramatyka: `protocol_tokens`).

Moduł neutralny: nie importuje `quality` ani `status` (między nimi jest cykl importów). Tu mieszka zapis linii przez
`checkpoint --final`; hak Stop (`library/hooks/check-closure.py`) ma samodzielną kopię odczytu.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from . import protocol_tokens as tokens

MAX_PLAN_BYTES = 512 * 1024   # jak w haku: większego planu nikt nie ocenia
_HEAD_BYTES = 64 * 1024       # z nadmiarem na `HEADER_LIMIT` znaków (UTF-8, CRLF)
_SHA40 = re.compile(r'[0-9a-f]{40}')


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


def _fenced(texts):
    """Numery wierszy wewnątrz bloków kodu (``` albo ~~~): przykład linii w nagłówku jest treścią autora."""
    inside, found = None, set()
    for index, text in enumerate(texts):
        mark = text.lstrip()[:3]
        if mark in ('```', '~~~') and inside in (None, mark):
            inside = None if inside else mark
        elif inside:
            found.add(index)
    return found


def _rewrite(raw, run_id, day, sha):
    """Bajty planu z jedną linią zamknięcia bezpośrednio pod linią przebiegu i akcja `added|updated|none`.

    Reszta pliku zostaje bajt w bajt; nowa linia dostaje koniec linii wiersza przebiegu."""
    raw.decode('utf-8')   # ścisłe: plik spoza UTF-8 nie jest zmieniany (UnicodeDecodeError)
    lines = raw.splitlines(keepends=True)
    contents = [line.rstrip(b'\r\n') for line in lines]
    texts = [content.decode('utf-8') for content in contents]
    header_end = next((index for index, text in enumerate(texts) if text.startswith('## ')), len(texts))
    run_index = None
    for index in range(header_end):
        match = tokens.RUN_LINE.search(texts[index])
        if match and match.group(1) == run_id:
            run_index = index
            break
    if run_index is None:
        raise ValueError('nie znaleziono linii przebiegu w nagłówku')
    fenced = _fenced(texts[:header_end])
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
