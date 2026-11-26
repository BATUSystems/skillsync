"""Zakładka „Historia” strony statusu: lista zakończonych planów wszystkich zakładek projektu i zmiana statusu planu
(`done` <-> `archived`) z blokadą pliku i audytem (CORE-1180 M-6).

Moduł nie zna HTTP ani rejestru zakładek: dostaje korzenie zakładek (`TabRoot`) i wywołanie re-walidacji (`guard`).
Zapis dotyczy wyłącznie pliku planu w korzeniu zakładki właściciela; nic nie jest zapisywane między zakładkami.
"""
from __future__ import annotations

import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import status as status_core
from .common import redact
from .prototype_server import FileLock, LockTimeout, LockUnavailable

ACTIONS = ('archive', 'unarchive')
LOCK_NAME = status_core.PLAN_STATUS_LOCK
AUDIT_NAME = 'plan-status.jsonl'
LOCK_TIMEOUT = 3.0
MAX_AUDIT_BYTES = 1024 * 1024
MAX_ROWS = 1000
MAX_PER_TAB = 200      # finished plans of one tab
MAX_SCAN = 3000        # plan files looked at in one tab (newest names first; more than this and the older ones are not looked at: `truncated`)
ABANDONED_DAYS = 7
_CACHE_LIMIT = 4000
_cache: dict[tuple, tuple] = {}


class TabRoot(NamedTuple):
    id: str
    name: str
    root: Path


class HistoryError(Exception):
    """Odmowa zmiany statusu: `http` (kod), `code` (stabilny kod kontraktu), nic nie zapisano (poza `intent` w audycie po jego zapisie)."""

    def __init__(self, http: int, code: str, detail: str = ''):
        super().__init__(detail or code)
        self.http, self.code, self.detail = http, code, detail


# --------------------------------------------------------------------------- bezpieczne katalogi stanu
def _state_dir(root: Path, name: str) -> Path:
    """`.skillsync/<name>` of the root without a symlink on the way (OSError otherwise)."""
    try:
        return status_core.state_subdir(root, name)
    except ValueError as error:
        raise OSError(str(error)) from None


def _audit_append(root: Path, row: dict) -> None:
    """Jeden wiersz JSON do `.skillsync/audit/plan-status.jsonl` pod blokadą, z `O_NOFOLLOW` i limitem rozmiaru (OSError/ValueError przy porażce)."""
    directory = _state_dir(root, 'audit')
    path = directory / AUDIT_NAME
    line = (json.dumps(redact(row), ensure_ascii=False) + '\n').encode('utf-8')
    try:
        with FileLock(str(directory / (AUDIT_NAME + '.lock')), LOCK_TIMEOUT, 0o600, unlink=True):
            try:
                kind = os.lstat(path).st_mode
            except FileNotFoundError:
                kind = None
            if kind is not None and not stat.S_ISREG(kind):   # a symlink, a FIFO or a directory in the place of the file: refused before any open
                raise OSError('audit path is not a regular file')
            size = path.stat().st_size if kind is not None else 0
            if size + len(line) > MAX_AUDIT_BYTES:
                raise ValueError('audit file over the size limit')
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o644)
            try:
                if size and not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError('audit path is not a regular file')
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
    except LockTimeout:   # also LockUnavailable: the audit lock is not the plan lock, its failure is an audit failure
        raise OSError('audit lock unavailable') from None


# --------------------------------------------------------------------------- zmiana statusu
def change_status(root, rel, action, sha, tab, via=None, by=None, guard=None) -> dict:
    """`done` -> `archived` (archive) albo `archived` -> `done` (unarchive) jednego planu `docs/plans/*.md` w korzeniu zakładki.

    Kolejność pod blokadą pliku: sprawdzenie (sha, status) -> `guard` (re-walidacja zakładki) -> audyt `intent` -> zmiana wiersza Status ->
    audyt `done`. Brak zapisu audytu `intent` = brak zmiany (HistoryError 500 `auditRefused`); brak `done` = wynik z `auditFailed`.
    Zwraca {tab, path, from, to, sha, auditFailed}; zgłasza HistoryError."""
    root = Path(root)   # the root of the registered tab as it is (the guard validates exactly this path; no second resolution here)
    if action not in ACTIONS:
        raise HistoryError(400, 'badRequest')
    if not isinstance(rel, str) or not status_core.is_plan_path(rel):
        raise HistoryError(400, 'badRequest')
    source, target = status_core.plan_status_transition(action)
    if guard is not None and not guard():   # first: a replaced directory gets no lock file, no read, no other refusal
        raise HistoryError(409, 'tabChanged')
    try:
        with FileLock(str(_state_dir(root, 'locks') / LOCK_NAME), LOCK_TIMEOUT, 0o600, unlink=True):   # the lock of `status.plan_status_lock`, held across the checks and the write
            return _locked_change(root, rel, action, sha, tab, via, by, guard, source, target)
    except LockUnavailable:
        raise HistoryError(500, 'serverError', 'file locks unsupported') from None
    except LockTimeout:
        raise HistoryError(409, 'locked') from None
    except OSError as error:
        raise HistoryError(500, 'serverError', status_core._message(error, 200)) from None


def _locked_change(root, rel, action, sha, tab, via, by, guard, source, target):
    try:
        path = status_core._doc_file(root, rel, 'plans')
        raw = path.read_bytes()
    except (OSError, ValueError, UnicodeDecodeError):
        raise HistoryError(404, 'planGone') from None
    if not isinstance(sha, str) or status_core.git_blob_sha(raw) != sha:
        raise HistoryError(409, 'shaMismatch')
    current = status_core._status_fields(raw.decode('utf-8', 'replace'), status_core.PLAN_STATUSES)[0]
    if current != source:
        raise HistoryError(409, 'statusNotAllowed')
    if guard is not None and not guard():   # again under the file lock, right before the first write
        raise HistoryError(409, 'tabChanged')
    row = {'at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'by': str(by if by is not None else status_core._approval_user())[:80],
           'via': via, 'tab': tab, 'path': rel, 'from': source, 'to': target, 'sha': sha}
    try:
        _audit_append(root, {**row, 'phase': 'intent'})
    except (OSError, ValueError):
        raise HistoryError(500, 'auditRefused') from None
    try:
        report = status_core.set_doc_status(root, rel, action, 'plans', expect_sha=sha, lock=False)
    except (OSError, ValueError, UnicodeDecodeError) as error:
        try:
            _audit_append(root, {**row, 'phase': 'failed'})
        except (OSError, ValueError):
            pass
        text = str(error)
        if isinstance(error, ValueError) and 'sha mismatch' in text:
            raise HistoryError(409, 'shaMismatch', status_core._message(error, 200)) from None
        if isinstance(error, ValueError):
            raise HistoryError(409, 'statusNotAllowed', status_core._message(error, 200)) from None
        raise HistoryError(500, 'serverError', status_core._message(error, 200)) from None
    new_sha = report['sha']
    audit_failed = False
    try:
        _audit_append(root, {**row, 'sha': new_sha, 'shaBefore': sha, 'phase': 'done'})
    except (OSError, ValueError):
        audit_failed = True
    return {'tab': tab, 'path': rel, 'from': source, 'to': target, 'sha': new_sha, 'auditFailed': audit_failed}


# --------------------------------------------------------------------------- lista
def _record(path: Path):
    """(sha, status, title, mtime) pliku planu albo None; z pamięcią podręczną po (ścieżka, mtime, rozmiar)."""
    try:
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size)
        hit = _cache.get(key)
        if hit is not None:
            return hit
        if info.st_size > status_core.MAX_PLAN_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    text = raw.decode('utf-8', 'replace')
    state = status_core._status_fields(text, status_core.PLAN_STATUSES)[0]
    found = status_core._TITLE.search(text)
    title = redact(found.group(1).strip())[:200] if found else path.stem
    value = (status_core.git_blob_sha(raw), state, title, info.st_mtime)
    if len(_cache) >= _CACHE_LIMIT:
        _cache.clear()
    _cache[key] = value
    return value


def _plans_of(tab: TabRoot, now: float, cut: list):
    """Finished (and abandoned) plans of a tab; MAX_PER_TAB applies AFTER the status filter, MAX_SCAN before it (the newest names are looked at first); `cut` gets a True when either limit dropped a plan."""
    directory = tab.root / 'docs' / 'plans'
    try:
        if (tab.root / 'docs').is_symlink() or directory.is_symlink() or not directory.is_dir():
            return
        paths = sorted(directory.glob('*.md'), key=lambda item: item.name, reverse=True)
    except OSError:
        return
    if len(paths) > MAX_SCAN:
        cut.append(True)
        paths = paths[:MAX_SCAN]
    yielded = 0
    for path in paths:
        if path.is_symlink() or path.name.lower() == 'readme.md':
            continue
        found = _record(path)
        if found is None:
            continue
        sha, state, title, mtime = found
        abandoned = state == 'approved' and now - mtime > ABANDONED_DAYS * 86400
        if state in ('done', 'archived') or abandoned:
            if yielded >= MAX_PER_TAB:
                cut.append(True)
                return
            yielded += 1
            yield path, sha, state, title, mtime, abandoned


def collect(tabs: list[TabRoot], now: float | None = None) -> dict:
    """{count, truncated, items[]}: zakończone (i porzucone) plany zakładek `tabs` (w tej kolejności), kluczowane parą (ścieżka, blob sha).
    Właścicielem wiersza jest pierwsza zakładka z pary; `copies`/`copyTabs` liczą wszystkie zakładki z tą parą (kolejność `tabs`)."""
    now = time.time() if now is None else now
    rows: dict[tuple, dict] = {}
    order: list[tuple] = []
    cut: list = []
    for tab in tabs:
        for path, sha, state, title, mtime, abandoned in _plans_of(tab, now, cut):
            rel = path.relative_to(tab.root).as_posix()
            key = (rel, sha)
            row = rows.get(key)
            if row is None:
                action = None if abandoned else ('archive' if state == 'done' else 'unarchive' if state == 'archived' else None)
                rows[key] = {'tab': tab.id, 'tabName': tab.name, 'path': rel, 'title': title, 'status': state, 'sha': sha, 'copies': 1,
                             'copyTabs': [tab.id], 'abandoned': abandoned, 'action': action,
                             'mtime': datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec='seconds')}
                order.append(key)
            elif tab.id not in row['copyTabs']:
                row['copyTabs'].append(tab.id)
                row['copies'] = len(row['copyTabs'])
    items = [rows[key] for key in order]
    return {'count': len(items), 'truncated': len(items) > MAX_ROWS or bool(cut), 'items': items[:MAX_ROWS]}
