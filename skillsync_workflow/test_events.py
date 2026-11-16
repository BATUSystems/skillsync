"""Test run events of a plan step (180-A): the files `.skillsync/runs/<run-id>/tests/<step-id>/<attempt>.jsonl`.

One line is one event (JSON Lines, UTF-8, `ensure_ascii=False`), appended with a single `write` on a file opened `O_APPEND`; a reader ignores a last
line without its line end (a write in progress) and every line that does not parse. The attempt number is taken with `O_EXCL`, so parallel runs
of one step never share a file. Event types (`v: 1`): `start`, `collected`, `test`, `counts`, `end`.

Masking of the text of a test result is decided in ONE place, `mask_text`, with ONE setting, `tests.mask` of the project manifest (default off:
the names and messages of tests are stored and shown as they are; the command line is always masked). Limits: 10 000 `test` events and 8 MiB per
attempt (the counters of `end` stay true, `truncated` is set), `msg` 2000 characters, names 300.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

from .common import decode_text, file_lines, redact
from .runs import _slug

EVENTS_DIR = 'tests'
NO_STEP = '_none'
EVENT_VERSION = 1
MAX_TEST_EVENTS = 10_000
MAX_BYTES = 8 * 1024 * 1024
MAX_MSG = 2000
MAX_NAME = 300
MAX_COMMAND = 1000
MAX_LINE = 16 * 1024
COUNTS_INTERVAL_S = 1.0
STATES = ('running', 'passed', 'failed', 'skipped')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}   # device names of Windows
_ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_CONTROL = re.compile('[\x00-\x08\x0b-\x1f\x7f-\x9f]')
_INVISIBLE = re.compile('[؜​-‏‪-‮⁠-⁤⁦-⁩﻿]')


def mask_setting(manifest):
    """`tests.mask` of the manifest: off by default and for an explicit off value; ANY other value masks (a typo must not publish the results)."""
    tests = (manifest or {}).get('tests')
    if not isinstance(tests, dict) or 'mask' not in tests:
        return False
    value = tests['mask']
    if value is None or value is False or (isinstance(value, str) and value.strip().lower() in ('off', 'false', 'no', '0', '')) or value == 0:
        return False
    return True   # fail closed: `true`, "on", "yes", 1 and anything else unusual mask the results


def clean_text(text, limit, mask=False, keep_newlines=False):
    """Text of an event: ANSI and invisible/bidirectional marks removed BEFORE the (optional) mask, control characters to a space (a line end
    stays when `keep_newlines`), then cut to `limit` characters (a cut text ends with an ellipsis)."""
    text = _INVISIBLE.sub('', _ANSI.sub('', '' if text is None else str(text)))
    if mask:
        text = redact(text)
    if keep_newlines:
        text = '\n'.join(_CONTROL.sub(' ', part).rstrip() for part in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'))   # the indentation of a trace stays
        text = re.sub(r'\n{3,}', '\n\n', text).strip('\n')
    else:
        text = ' '.join(_CONTROL.sub(' ', text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def mask_text(text, mask, limit=MAX_MSG, keep_newlines=False):
    """THE place where the masking of the text of a test result is decided (setting `tests.mask`)."""
    return clean_text(text, limit, mask=mask, keep_newlines=keep_newlines)


def _is_number(text):
    return text.isascii() and text.isdecimal()


def _safe_name(value, field):
    """`_slug` plus the names Windows would turn into something else: a device name (`CON`, `NUL`, `COM1`... with or without an extension)
    and a trailing dot (`F1.` and `F1` are the same name in Win32)."""
    value = _slug(value, field)
    if value.endswith('.') or value.split('.')[0].upper() in _RESERVED:
        raise ValueError(f'{field}: {value!r} is not usable as a directory name on every system (reserved device name or trailing dot)')
    return value


def step_directory(root, run_id, step):
    """`.skillsync/runs/<run-id>/tests/<step-id|_none>` (names validated, no symlinks on the way); not created."""
    run_id = _safe_name(run_id, '--run')
    name = NO_STEP if step is None else _safe_name(step, '--step')
    base = Path(root) / '.skillsync'
    for part in (base, base / 'runs', base / 'runs' / run_id):
        if part.is_symlink():
            raise ValueError(f'symlink on the way to the test events: {part.name}')
    return base / 'runs' / run_id / EVENTS_DIR / name


def new_attempt(root, run_id, step):
    """Create the next attempt file (`O_EXCL`); returns `(path, number)`. Parallel callers get different numbers."""
    directory = step_directory(root, run_id, step)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError('symlink at the test events directory')
    number = 1 + max([int(p.stem) for p in directory.glob('*.jsonl') if _is_number(p.stem)] or [0])
    while True:
        path = directory / f'{number}.jsonl'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            number += 1
            continue
        os.close(fd)
        return path, number


def list_attempts(root, run_id, step):
    directory = step_directory(root, run_id, step)
    if not directory.is_dir() or directory.is_symlink():
        return []
    return sorted((p for p in directory.glob('*.jsonl') if _is_number(p.stem) and p.is_file() and not p.is_symlink()), key=lambda p: int(p.stem))


class EventWriter:
    """Appends the events of one attempt. Limits and the mask live here; the caller passes raw text."""

    def __init__(self, path, mask=False):
        self.path = Path(path)
        self.mask = bool(mask)
        self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        self.bytes = 0
        self.tests = 0
        self.truncated = False
        self.last_counts = 0.0
        self._counts = None
        self._lock = threading.Lock()   # the reader threads of `test-run` and `--import` may write at the same time

    def _write(self, event, force=False):
        with self._lock:
            return self._write_locked(event, force)

    def _write_locked(self, event, force):
        event = {'v': EVENT_VERSION, **event}
        data = (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if len(data) > MAX_LINE:
            return False
        if not force and self.bytes + len(data) > MAX_BYTES:
            self.truncated = True
            return False
        os.write(self.fd, data)
        self.bytes += len(data)
        return True

    def start(self, **fields):
        fields['command'] = clean_text(fields.get('command', ''), MAX_COMMAND, mask=True)   # the command line is ALWAYS masked
        return self._write({'t': 'start', 'at': _now(), 'startedAt': _now(), 'host': socket.gethostname(), **fields}, force=True)

    def collected(self, total, names=None):
        event = {'t': 'collected', 'total': int(total)}
        if names:
            event['names'] = [clean_text(n, MAX_NAME, mask=self.mask) for n in names[:5000]]
        return self._write(event)

    def test(self, test_id, name, state, group=None, ms=None, msg=None):
        if state not in STATES:
            raise ValueError(f'unknown test state: {state!r}')
        if self.tests >= MAX_TEST_EVENTS:
            self.truncated = True
            return False
        self.tests += 1
        event = {'t': 'test', 'id': mask_text(str(test_id).replace('\\', '/'), self.mask, MAX_NAME), 'name': mask_text(name, self.mask, MAX_NAME), 'state': state}
        if group:
            event['group'] = mask_text(group, self.mask, MAX_NAME)
        if ms is not None:
            event['ms'] = int(ms)
        if msg:
            event['msg'] = mask_text(msg, self.mask, MAX_MSG, keep_newlines=True)
        return self._write(event)

    def counts(self, passed=0, failed=0, skipped=0, running=0, total=None, force=False):
        now = time.monotonic()
        if not force and now - self.last_counts < COUNTS_INTERVAL_S:
            self._counts = (passed, failed, skipped, running, total)
            return False
        self.last_counts = now
        self._counts = None
        event = {'t': 'counts', 'passed': passed, 'failed': failed, 'skipped': skipped, 'running': running}
        if total is not None:
            event['total'] = total
        return self._write(event)

    def end(self, status, exit_code, reason=None, counts=None, ms=None, source=None, tree=None):
        if self._counts:
            self.counts(*self._counts[:4], total=self._counts[4], force=True)
        event = {'t': 'end', 'at': _now(), 'status': status, 'exit': exit_code}
        if reason:
            event['reason'] = clean_text(reason, 500, mask=True)
        if counts:
            event['counts'] = counts
        if ms is not None:
            event['ms'] = int(ms)
        if source:
            event['source'] = source
        if tree:
            event['tree'] = tree   # the content of the working tree AFTER the run (the gate compares it, see test_gate)
        if self.truncated:
            event['truncated'] = True
        return self._write(event, force=True)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + 'Z'


def read_events(path, offset=0):
    """`(events, new_offset, skipped)`: only COMPLETE lines (ending in a line feed) from `offset` on; a line that does not parse or is not an
    object of this version is counted in `skipped`. A write in progress (no line feed yet) is left for the next call."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(offset)
            data = handle.read(MAX_BYTES + MAX_LINE)
    except OSError:
        return [], offset, 0
    end = data.rfind(b'\n') + 1
    if end == 0:
        return [], offset, 0
    events, skipped = [], 0
    for line in file_lines(decode_text(data[:end])):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(event, dict) and event.get('v') == EVENT_VERSION and isinstance(event.get('t'), str):
            events.append(event)
        else:
            skipped += 1
    return events, offset + end, skipped


class AttemptState:
    """Folds the events of one attempt into what the page shows; `feed` can be called with the events read since the last offset."""

    def __init__(self, number=None):
        self.number = number
        self.state = 'running'
        self.start = self.end = None
        self.tests = {}
        self.counts = {'total': None, 'passed': 0, 'failed': 0, 'skipped': 0, 'running': 0, 'waiting': 0}
        self.stream_counts = None
        self.truncated = False
        self.collected_total = None
        self.imported = None   # the `import` event of an attempt copied from another machine (`test-run --import`)
        self.skipped_lines = 0
        self.offset = 0

    def feed(self, events):
        for event in events:
            kind = event.get('t')
            if kind == 'start':
                self.start = {**event, 'at': event['at'] if isinstance(event.get('at'), str) else None}
            elif kind == 'import':
                self.imported = event
            elif kind == 'collected':
                if isinstance(event.get('total'), int):
                    self.collected_total = event['total']
                for name in (event.get('names') if isinstance(event.get('names'), list) else []):
                    if isinstance(name, str):
                        self.tests.setdefault(name, {'id': name, 'name': name, 'state': 'waiting', 'group': None})
            elif kind == 'test' and isinstance(event.get('id'), str) and event.get('state') in STATES and all(isinstance(event.get(k), (str, type(None))) for k in ('name', 'group', 'msg')):
                known = self.tests.get(event['id'], {})
                self.tests[event['id']] = {'id': event['id'], 'name': event.get('name') or event['id'], 'state': event['state'],
                                           'group': event.get('group') or known.get('group'), 'ms': event.get('ms', known.get('ms')),
                                           'msg': event.get('msg') or known.get('msg')}
            elif kind == 'counts':
                self.stream_counts = {k: event.get(k, 0) for k in ('passed', 'failed', 'skipped', 'running')}
                if isinstance(event.get('total'), int):
                    self.collected_total = event['total']
            elif kind == 'end':
                self.end = event
                self.truncated = bool(event.get('truncated')) or self.truncated
        self._recount()

    def _recount(self):
        by = {'passed': 0, 'failed': 0, 'skipped': 0, 'running': 0, 'waiting': 0}
        for test in self.tests.values():
            by[test['state']] += 1
        if self.end and isinstance(self.end.get('counts'), dict):
            by.update({k: v for k, v in self.end['counts'].items() if k in by and isinstance(v, int)})
            total = self.end['counts'].get('total')
        elif self.tests:
            total = self.collected_total if self.collected_total is not None else sum(by.values())
        elif self.stream_counts:
            by.update(self.stream_counts)
            total = self.collected_total
        else:
            total = self.collected_total
        self.counts = {'total': total, **by}
        if self.end:
            status = self.end.get('status')
            self.state = {'PASS': 'passed', 'FAIL': 'failed', 'BLOCKED': 'interrupted'}.get(status if isinstance(status, str) else None, 'failed' if by['failed'] else 'interrupted')
        else:
            self.state = 'running'


def read_attempt(path, previous=None):
    """Read (the rest of) an attempt file into an `AttemptState` (a previous state continues from its offset)."""
    state = previous or AttemptState(int(Path(path).stem) if Path(path).stem.isdigit() else None)
    events, state.offset, skipped = read_events(path, state.offset)
    state.skipped_lines += skipped
    state.feed(events)
    return state


def is_alive(pid, host=None):
    """True when a process with this pid exists on this machine (always True on Windows: unknown) (used to tell an interrupted attempt from a running one)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if host and host != socket.gethostname():   # the pid of another machine (an imported run) says nothing here
        return True
    if sys.platform == 'win32':   # `os.kill(pid, 0)` would TERMINATE the process there: unknown counts as alive (the silence threshold decides)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True
