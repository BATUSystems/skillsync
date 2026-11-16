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
MAX_READ_LINE = 4 * MAX_LINE   # a longer line is not an event of ours (the writer never writes one over MAX_LINE): skipped, never parsed
MAX_NUMBER = 10 ** 12
MAX_STRING = 20_000
MAX_DEPTH = 6
MAX_ITEMS = 6000
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


_SENSITIVE_WORD = r'(?:token|password|passwd|secret|key|pass|auth|credential)'
_FLAG_VALUE = re.compile(r'(?i)(?<![\w-])(--?[\w.-]*' + _SENSITIVE_WORD + r'[\w.-]*)(=|\s+(?!-))("[^"]*"|\'[^\']*\'|\S+)')
_SHORT_FLAG_VALUE = re.compile(r'(?<![\w-])(-p)(\s*)(?!-)(\S+)')
_SHORT_FLAG_TOOLS = ('mysql', 'mysqldump', 'mysqladmin', 'mariadb', 'sshpass')   # `-p` is a password there; elsewhere it is a plugin, a profile, a port


def mask_command(text):
    """The command line of a run with the secrets it may carry masked (a HEURISTIC; the command line is always masked, whatever `tests.mask` says):
    `redact` (pairs `name=value`, URL credentials, `Authorization`), plus the VALUE after a flag whose name contains token, password, passwd, secret,
    key, pass, auth or credential (`--token VALUE`, `-api-key VALUE`; a name that only contains such a word, as `--keyword`, is masked too: the heuristic
    prefers a masked value to a published secret), and after `-p` for the tools where it is a password (`mysql -p VALUE`, `-pVALUE`, `sshpass -p VALUE`).
    NOT masked: a secret in a positional argument, a short flag of another kind (`-k VALUE`), a value that the shell builds or reads from the environment
    only when it is not in the line itself, a secret in an environment variable passed by the caller (it is not part of the line)."""
    text = _FLAG_VALUE.sub(lambda m: m.group(1) + m.group(2) + '[REDACTED]', '' if text is None else str(text))
    words = text.split()
    if words and Path(words[0]).name.lower() in _SHORT_FLAG_TOOLS:
        text = _SHORT_FLAG_VALUE.sub(lambda m: m.group(1) + m.group(2) + '[REDACTED]', text)
    return text


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
        fields['command'] = clean_text(mask_command(fields.get('command', '')), MAX_COMMAND, mask=True)   # the command line is ALWAYS masked (see `mask_command`)
        return self._write({'t': 'start', 'at': _now(), 'startedAt': _now(), 'host': socket.gethostname(), **fields}, force=True)

    def collected(self, total, names=None):
        """The `collected` event: the total, and the names of the tests as `waiting` ones, in as many events as the line limit needs (the first
        one carries the total). More than 5000 names: the rest is dropped and `truncated` is set."""
        total = int(total)
        names = [clean_text(str(n).replace('\\', '/'), MAX_NAME, mask=self.mask) for n in (names or [])]   # the same `/` as the ids of `test`
        if len(names) > 5000:
            names = names[:5000]
            self.truncated = True
        ok = self._write({'t': 'collected', 'total': total})
        chunk, size = [], 0
        for name in names:
            cost = len(name.encode('utf-8')) * 6 + 8   # worst case of the JSON escapes
            if chunk and size + cost > MAX_LINE - 512:
                ok = self._write({'t': 'collected', 'names': chunk}) and ok
                chunk, size = [], 0
            chunk.append(name)
            size += cost
        if chunk:
            ok = self._write({'t': 'collected', 'names': chunk}) and ok
        return ok

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


def _clean_value(value, depth=0):
    """The value of an event line made safe or `ValueError`: a lone surrogate (which no encoder takes) becomes U+FFFD, a string is capped, a number
    must be finite and within `MAX_NUMBER`, containers are shallow and small. A file of another machine or of a broken tool must never reach the page
    (NaN, a huge number, a surrogate) or the reader (recursion) as anything but a skipped line."""
    if depth > MAX_DEPTH:
        raise ValueError('too deep')
    if isinstance(value, str):
        value = value if len(value) <= MAX_STRING else value[:MAX_STRING]
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            value = value.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace')
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if abs(value) > MAX_NUMBER:
            raise ValueError('number out of range')
        return value
    if isinstance(value, float):
        if value != value or abs(value) > MAX_NUMBER:   # NaN, infinity, 1e999
            raise ValueError('number out of range')
        return value
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise ValueError('too many items')
        return [_clean_value(item, depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise ValueError('too many items')
        return {_clean_value(key, depth + 1): _clean_value(item, depth + 1) for key, item in value.items()}
    raise ValueError('unexpected value')


def _reject_constant(name):
    raise ValueError(f'{name} is not JSON')


def read_events(path, offset=0):
    """`(events, new_offset, skipped)`: only COMPLETE lines (ending in a line feed) from `offset` on; a line that does not parse, is too long, is too deep
    (any error of the parser, a recursion included), has a number that is not finite or a value of a kind JSON does not have, or is not an object of this
    version is counted in `skipped`. A write in progress (no line feed yet) is left for the next call."""
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
        if len(line) > MAX_READ_LINE:
            skipped += 1
            continue
        try:
            event = _clean_value(json.loads(line, parse_constant=_reject_constant))
        except (ValueError, RecursionError, MemoryError, OverflowError, TypeError):
            skipped += 1
            continue
        if isinstance(event, dict) and event.get('v') == EVENT_VERSION and isinstance(event.get('t'), str):
            events.append(event)
        else:
            skipped += 1
    return events, offset + end, skipped


def _count(value):
    """A counter of an event: a non-negative integer within the limits, else 0 (a string or NaN of a broken file must not reach a sum)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_NUMBER else 0


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_NUMBER else None


_START_TEXT = ('at', 'run', 'step', 'stepSource', 'command', 'tool', 'host', 'startedAt', 'adapter', 'live', 'levels', 'allowNoCount', 'treeStart')
_END_TEXT = ('at', 'status', 'reason', 'source', 'tree')


def _typed(event, text_keys, int_keys, bool_keys=()):
    """Only the known fields of an event, each of its kind (others dropped): the rest of the code can trust the types."""
    out = {key: event[key] for key in text_keys if isinstance(event.get(key), str)}
    out.update({key: event[key] for key in int_keys if _int_or_none(event.get(key)) is not None})
    out.update({key: event[key] for key in bool_keys if isinstance(event.get(key), bool)})
    return out


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
        self.ident = None   # (device, inode, size) of the file this state was read from

    def feed(self, events):
        for event in events:
            kind = event.get('t')
            if kind == 'start':
                self.start = {**_typed(event, _START_TEXT, ('pid', 'attempt'), ('allowSkips',)), 'at': event['at'] if isinstance(event.get('at'), str) else None}
            elif kind == 'import':
                self.imported = {'sourceSha': event['sourceSha'] if isinstance(event.get('sourceSha'), str) else None,
                                 'at': event['at'] if isinstance(event.get('at'), str) else None}
            elif kind == 'collected':
                if _int_or_none(event.get('total')) is not None:
                    self.collected_total = event['total']
                for name in (event.get('names') if isinstance(event.get('names'), list) else []):
                    if isinstance(name, str):
                        self.tests.setdefault(name, {'id': name, 'name': name, 'state': 'waiting', 'group': None})
            elif kind == 'test' and isinstance(event.get('id'), str) and event.get('state') in STATES and all(isinstance(event.get(k), (str, type(None))) for k in ('name', 'group', 'msg')):
                known = self.tests.get(event['id'], {})
                self.tests[event['id']] = {'id': event['id'], 'name': event.get('name') or event['id'], 'state': event['state'],
                                           'group': event.get('group') or known.get('group'), 'ms': _int_or_none(event.get('ms')) if 'ms' in event else known.get('ms'),
                                           'msg': event.get('msg') or known.get('msg')}
            elif kind == 'counts':
                self.stream_counts = {k: _count(event.get(k)) for k in ('passed', 'failed', 'skipped', 'running')}
                if _int_or_none(event.get('total')) is not None:
                    self.collected_total = event['total']
            elif kind == 'end':
                counts = event.get('counts')
                self.end = {'t': 'end', **_typed(event, _END_TEXT, ('exit', 'ms')), **({'truncated': True} if event.get('truncated') is True else {}),
                            **({'counts': {k: v for k, v in counts.items() if k in ('total', 'passed', 'failed', 'skipped', 'running', 'waiting') and _int_or_none(v) is not None}}
                               if isinstance(counts, dict) else {})}
                self.truncated = bool(event.get('truncated')) or self.truncated
        self._recount()

    def _recount(self):
        by = {'passed': 0, 'failed': 0, 'skipped': 0, 'running': 0, 'waiting': 0}
        for test in self.tests.values():
            if self.end and test['state'] == 'running':
                test['state'] = 'waiting'   # the run is over and the test has no result: it is not running (the page must not show it as such)
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
    """Read (the rest of) an attempt file into an `AttemptState` (a previous state continues from its offset). The state remembers which file it
    read (device and inode): a file replaced by another one, or shorter than the offset, is read from the start."""
    try:
        info = os.stat(path)
        ident = (info.st_dev, info.st_ino, info.st_size)
    except OSError:
        ident = None
    if previous is not None and (ident is None or previous.ident is None or ident[:2] != previous.ident[:2] or ident[2] < previous.offset):
        previous = None
    state = previous or AttemptState(int(Path(path).stem) if Path(path).stem.isdigit() else None)
    state.ident = ident
    events, state.offset, skipped = read_events(path, state.offset)
    state.skipped_lines += skipped
    state.feed(events)
    return state


def process_state(pid, host=None):
    """`True` (the process exists on this machine), `False` (it does not), `None` (unknown: another machine, Windows, no valid pid is `False`)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if host and host != socket.gethostname():   # the pid of another machine (an imported run) says nothing here
        return None
    if sys.platform == 'win32':   # `os.kill(pid, 0)` would TERMINATE the process there
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def is_alive(pid, host=None):
    """True unless the process is known not to exist (unknown counts as alive: the silence threshold decides then)."""
    return process_state(pid, host) is not False
