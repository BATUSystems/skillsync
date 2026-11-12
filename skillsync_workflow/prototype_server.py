#!/usr/bin/env python3
"""Shared comments server for skillsync prototypes (standalone module).

One file, Python 3.8+ standard library only: no PyYAML and no imports from the
rest of ``skillsync_workflow`` (the generator imports this module, never the
other way round).  It can be copied to any host and run directly::

    python3 prototype_server.py serve --root <prototypes dir> --users <users.json>
    python3 prototype_server.py user add|passwd|remove|list|import --users <users.json> ...

Contents (source of the contract: docs/specs/2026-11-04-prototype-komentarze-wspolne.md):

* ``comments.json`` v2: parsing, validation of every operation type, canonical
  serialization (header on the first line, one operation per line).
* Fold v2 (thread state, statuses, votes, edits, retractions, numbers, labels).
* Monotonic ``at``/``id`` under a lock (injected clock), process + file lock,
  atomic write preserving the file mode, reload after ``(inode, mtime_ns, size)``.
* HTML helpers shared with the generator: ``data-proto-id`` collection and
  ``ids --fix``, section boundaries, v1 anchor resolution, screen hashes.
* Iterations and restore (F2): freeze to ``iterations/N/``, restore of one screen
  section, recovery after an interruption, agent operations, ``merge-comments``.
* Users file (PBKDF2-HMAC-SHA256), sessions, login rate limits.
* HTTP server (``ThreadingHTTPServer``): static files from ``root``, API F1 and F2
  (``version``/``screens``/``iterations`` in ``GET …/operations``, ``POST …/restore``,
  ``GET …/restore-preview``).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:  # Windows
    import msvcrt
except ImportError:
    msvcrt = None


# ── Constants ────────────────────────────────────────────────────────────────

SERVER_VERSION = '1'
COMMENTS_FORMAT = 'skillsync-prototype-comments'
COMMENTS_VERSION = 2
COMMENTS_FILE = 'comments.json'
LEGACY_COMMENTS_FILE = 'comments.js'
PROTOTYPE_FILE = 'prototype.json'
LOCK_SUFFIX = '.lock'

THREAD_TYPES = ('create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen', 'reanchor', 'delete',
                'triage', 'decision')
GLOBAL_TYPES = ('accept', 'unaccept', 'iteration', 'restore')
OPERATION_TYPES = THREAD_TYPES + GLOBAL_TYPES
# Types accepted by `POST /_api/v1/prototypes/<slug>/operations`.  `accept`/`unaccept` have their own
# checks (hashes, `version`, ownership); `triage`, `decision`, `iteration`, `restore` are CLI only (K14).
BROWSER_TYPES = ('create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen', 'reanchor', 'delete',
                 'accept', 'unaccept')
OPERATION_KEYS = ('id', 'type', 'threadId', 'at', 'by', 'cid', 'payload')

STATUSES = ('open', 'awaiting', 'decided', 'closed', 'deferred')
OPEN_STATUSES = ('open', 'awaiting', 'decided')
ANCHOR_KINDS = ('element', 'screen', 'lost')
VOTE_VALUES = ('up', 'down', 'none')
RESOLVE_REASONS = ('done', 'kept', 'out_of_scope', 'author')
TRIAGE_CLASSES = ('clear', 'disputed', 'out_of_scope')
DECISION_KINDS = ('agent', 'developer')
DECISION_OUTCOMES = ('apply', 'keep', 'variant', 'defer', 'out_of_scope')
ACCEPT_SCOPES = ('screen', 'all', 'project')
ACCEPT_VIA = ('ui', 'cli')

LEGACY_ROLES = ('commenter', 'acceptor', 'developer')   # the `role` field of older cores
ROLES = LEGACY_ROLES + ('expert',)
PRIVILEGED_ROLES = ('acceptor', 'developer')
ROLE_PRECEDENCE = ('developer', 'acceptor', 'expert', 'commenter')   # highest first
SERVER_FEATURES = ('views', 'login-link', 'questions')   # `login-link` is listed only while the endpoint is active
VIEW_VALUES = ('desktop', 'mobile')
RESERVED_LOGINS = ('agent', 'import', 'server')

MAX_TEXT = 4000            # UTF-16 code units, like `String.length` in the engine
MAX_LABEL = 200
MAX_HINT = 120
MAX_REASON = 500
MAX_NAME = 120
MAX_PASSWORD = 4096
MAX_BODY = 64 * 1024
WARN_FILE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
LOCK_TIMEOUT = 5.0
DEFAULT_ITERATIONS = 600000
SALT_BYTES = 16
MIN_PASSWORD = 10
SESSION_IDLE = 12 * 3600
SESSION_MAX = 7 * 24 * 3600
RATE_WINDOW = 15 * 60
RATE_PAIR_LIMIT = 5
RATE_IP_LIMIT = 30
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 4445
CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
PUBLIC_READ_WARNING = 'prototypy i komentarze są widoczne bez logowania dla każdego, kto zna adres'
LEGACY_QUESTIONS_WARNING = ('pytania do eksperta są w starym pliku <root>/questions.json: serwer pokazuje je tylko do odczytu; '
                            'uruchom skillsync prototype question migrate (pytania trafią do <root>/<slug>/questions.json)')
EXPERT_PUBLIC_WARNING = ('istnieją konta z rolą expert, a serwer działa bez --login-to-view: pytania do eksperta wymagają '
                         'sesji, ale prototypy i komentarze są dla każdego; uruchom serwer z --login-to-view')

SLUG_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
LOGIN_RE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')
AT_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d{3})Z')
OP_ID_RE = re.compile(r'op-[A-Za-z0-9_-]{1,80}')
NEW_OP_ID_RE = re.compile(r'op-[0-9A-HJKMNP-TV-Z]{26}')
THREAD_ID_RE = re.compile(r'th-[A-Za-z0-9_-]{1,80}')
MESSAGE_ID_RE = re.compile(r'm-[A-Za-z0-9_-]{1,80}')
# Ids accepted in stored operations (migration from engine v1: `thread-…`, `msg-…`) and in
# references to existing threads/messages; new ids from the browser keep `th-`/`m-`.
LEGACY_THREAD_ID_RE = re.compile(r'(?:th|thread)-[A-Za-z0-9_-]{1,80}')
LEGACY_MESSAGE_ID_RE = re.compile(r'(?:m|msg)-[A-Za-z0-9_-]{1,80}')
CID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')
SCREEN_ID_RE = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}')
PROTO_ID_RE = re.compile(r'[a-z0-9]+(?:[-.][a-z0-9]+)*')
PROTO_ID_MAX = 64
HASH_RE = re.compile(r'[0-9a-f]{64}')
COMMIT_RE = re.compile(r'[0-9a-f]{7,64}')

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ── Errors ───────────────────────────────────────────────────────────────────

class PrototypeServerError(Exception):
    """Error with an HTTP status and a stable error code (`{"error": code}` in the API)."""

    def __init__(self, status, code, message='', details=None, headers=None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


class OperationError(PrototypeServerError):
    """Invalid operation or reference (400/403/404/409)."""


class LockTimeout(PrototypeServerError):
    def __init__(self, message='lock wait exceeded'):
        super().__init__(503, 'lock_timeout', message, headers={'Retry-After': '1'})


class FileTooLarge(PrototypeServerError):
    def __init__(self, size):
        super().__init__(507, 'file_too_large', 'comments.json reached the size limit', {'size': size})


class CommentsFileError(PrototypeServerError):
    """Unreadable or malformed `comments.json` (409 `invalid_log` with the reason; nothing is written)."""

    def __init__(self, message):
        super().__init__(409, 'invalid_log', message)


class NeedsMigration(PrototypeServerError):
    def __init__(self):
        super().__init__(409, 'needs_migration', 'prototype v1 (comments.js): run `skillsync prototype migrate`')


class UserError(Exception):
    """Users file / account problem; CLI maps it to BLOCKED (exit 2)."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class StartupError(Exception):
    """`serve` refuses to start (exit 2); `errors` lists every reason."""

    def __init__(self, errors, warnings=None):
        super().__init__('; '.join(errors))
        self.errors = list(errors)
        self.warnings = list(warnings or [])


# ── Time and identifiers ─────────────────────────────────────────────────────

def system_clock():
    """Default clock: seconds since the epoch (same contract as `time.time`)."""
    return time.time()


def clock_ms(clock):
    return int(math.floor(clock() * 1000))


def format_at(ms):
    """`YYYY-MM-DDTHH:MM:SS.mmmZ` (UTC) for epoch milliseconds."""
    seconds, millis = divmod(int(ms), 1000)
    moment = _EPOCH + timedelta(seconds=seconds)
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03dZ' % (
        moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second, millis)


def parse_at(value):
    """Epoch milliseconds of an `at` string, or None when the format is not exact."""
    if not isinstance(value, str):
        return None
    match = AT_RE.fullmatch(value)
    if not match:
        return None
    try:
        moment = datetime(*(int(part) for part in match.groups()[:6]), tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = moment - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + int(match.group(7))


def _b32_encode(value, length):
    chars = []
    for _ in range(length):
        value, rest = divmod(value, 32)
        chars.append(CROCKFORD[rest])
    if value:
        raise ValueError('value does not fit')
    return ''.join(reversed(chars))


def _b32_decode(text):
    value = 0
    for char in text:
        value = value * 32 + CROCKFORD.index(char)
    return value


def new_operation_id(at_ms, previous=None, random_bits=None):
    """`op-` + 26 Crockford base32 chars: 10 of time (ms), 4 of counter, 12 of randomness.

    The result is strictly greater (as a string) than `previous` when `previous`
    has the same format, so ids grow within one log even inside one millisecond.
    """
    if random_bits is None:
        random_bits = secrets.randbits(60)
    body = _b32_encode(at_ms % (1 << 50), 10) + _b32_encode(0, 4) + _b32_encode(random_bits % (1 << 60), 12)
    candidate = 'op-' + body
    if isinstance(previous, str) and NEW_OP_ID_RE.fullmatch(previous) and candidate <= previous:
        candidate = 'op-' + _b32_encode(_b32_decode(previous[3:]) + 1, 26)
    return candidate


# ── Small helpers ────────────────────────────────────────────────────────────

def _is_str(value, minimum=1):
    return isinstance(value, str) and len(value) >= minimum


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def text_length(text):
    """Length in UTF-16 code units (JavaScript `String.length`)."""
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def truncate_utf16(text, limit):
    """Longest prefix of `text` with at most `limit` UTF-16 code units, never splitting a surrogate pair."""
    units, end = 0, 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > limit:
            break
        units += size
        end += 1
    return text[:end]


def _encodable(text):
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def _utf16_key(text):
    return text.encode('utf-16-be', 'surrogatepass')


def _sha256_hex(data):
    if isinstance(data, str):
        data = data.encode('utf-8', 'surrogatepass')
    return hashlib.sha256(data).hexdigest()


def _reject_constant(name):
    raise ValueError('Non-standard JSON constant: %s' % name)


def strict_json_loads(text):
    """`json.loads` without NaN/Infinity."""
    return json.loads(text, parse_constant=_reject_constant)


def _read_umask():
    try:
        with open('/proc/self/status', encoding='ascii', errors='replace') as handle:
            for line in handle:
                if line.startswith('Umask:'):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    mask = os.umask(0)
    os.umask(mask)
    return mask


_IMPORT_UMASK = _read_umask()


def current_umask():
    """Process umask without the racy `os.umask` dance when `/proc` is available."""
    try:
        with open('/proc/self/status', encoding='ascii', errors='replace') as handle:
            for line in handle:
                if line.startswith('Umask:'):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    return _IMPORT_UMASK


# ── comments.json: serialization and parsing ────────────────────────────────

_ARRAY_INDEX = re.compile(r'0|[1-9][0-9]*')
_LONE_SURROGATE = re.compile('[\ud800-\udfff]')


def _js_key_order(keys):
    """Own-key order of a JavaScript object: array-index keys ascending, then the rest in insertion order."""
    indexes = [key for key in keys if _ARRAY_INDEX.fullmatch(key) and int(key) < 4294967295]
    if not indexes:
        return list(keys)
    taken = set(indexes)
    return sorted(indexes, key=int) + [key for key in keys if key not in taken]


def js_number(value):
    """ECMAScript `Number::toString` of a finite JSON number (`2.0` -> `2`, `1e-7` -> `1e-7`)."""
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) <= 2 ** 53:
            return str(value)
        value = float(value)
    if not math.isfinite(value):
        return 'null'
    if value == 0:
        return '0'
    sign = '-' if value < 0 else ''
    text = repr(abs(value))
    mantissa, _sep, exponent = text.partition('e')
    whole, _dot, fraction = mantissa.partition('.')
    digits = (whole + fraction).lstrip('0')
    point = len(whole) + (int(exponent) if exponent else 0)
    stripped = (whole + fraction)
    point -= len(stripped) - len(stripped.lstrip('0'))
    digits = digits.rstrip('0') or '0'
    k, n = len(digits), point
    if k <= n <= 21:
        return sign + digits + '0' * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + '.' + digits[n:]
    if -6 < n <= 0:
        return sign + '0.' + '0' * (-n) + digits
    e = n - 1
    tail = ('e+%d' % e) if e >= 0 else ('e-%d' % -e)
    if k == 1:
        return sign + digits + tail
    return sign + digits[0] + '.' + digits[1:] + tail


def _js_string(text):
    encoded = json.dumps(text, ensure_ascii=False)
    return _LONE_SURROGATE.sub(lambda match: '\\u%04x' % ord(match.group()), encoded)


def js_stringify(value):
    """Compact JSON exactly like `JSON.stringify(value)` for JSON-parsed values."""
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, str):
        return _js_string(value)
    if isinstance(value, (int, float)):
        return js_number(value)
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(js_stringify(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(_js_string(key) + ':' + js_stringify(value[key])
                              for key in _js_key_order([str(key) for key in value])) + '}'
    raise TypeError('not a JSON value: %r' % type(value))


def canonical_operation(operation):
    """`canonicalOperation`: keys `id, type, threadId, at, by, cid, payload` first, others after."""
    ordered = {}
    for key in OPERATION_KEYS:
        if key in operation:
            ordered[key] = operation[key]
    for key, value in operation.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def serialize_operation(operation):
    """One line: `JSON.stringify(canonicalOperation(op))`."""
    return js_stringify(canonical_operation(operation))


def serialize_document(prototype_id, operations):
    """`serializeCommentsDocument`: byte-identical with the engine and the `comments.json` template.

    Line 1 `{"format":"skillsync-prototype-comments","version":2,"prototypeId":"<slug>","operations":[`,
    then one operation per line joined with `,\\n`, then `]}` and `\\n`.  Other header keys are
    not kept (the engine does not write them either).
    """
    header = ('{"format":' + _js_string(COMMENTS_FORMAT) + ',"version":' + str(COMMENTS_VERSION)
              + ',"prototypeId":' + _js_string(str(prototype_id)) + ',"operations":[')
    lines = [serialize_operation(operation) for operation in operations]
    return header + '\n' + (',\n'.join(lines) + '\n' if lines else '') + ']}\n'


def empty_document(prototype_id):
    return serialize_document(prototype_id, [])


class CommentsDocument(object):
    """Parsed `comments.json`: header fields and the raw operation list (not normalized)."""

    def __init__(self, prototype_id, operations, extra=None):
        self.prototype_id = prototype_id
        self.operations = operations
        self.extra = extra or {}


def parse_document(text, slug=None):
    """Parse `comments.json` text; raise `CommentsFileError` for a wrong format.

    A leading BOM is tolerated on reading (written files never have one).  Operations
    are returned as stored; invalid entries are skipped later by the fold, never removed.
    """
    if text.startswith('﻿'):
        text = text[1:]
    try:
        value = strict_json_loads(text)
    except (ValueError, RecursionError) as error:
        raise CommentsFileError('comments.json is not valid JSON: %s' % error.__class__.__name__)
    if not isinstance(value, dict):
        raise CommentsFileError('comments.json must be an object')
    if value.get('format') != COMMENTS_FORMAT:
        raise CommentsFileError('comments.json has a wrong `format`')
    version = value.get('version')
    if not _is_int(version) or version != COMMENTS_VERSION:
        raise CommentsFileError('comments.json has an unsupported `version`')
    prototype_id = value.get('prototypeId')
    if not _is_str(prototype_id):
        raise CommentsFileError('comments.json has no `prototypeId`')
    if slug is not None and prototype_id != slug:
        raise CommentsFileError('comments.json belongs to another prototype')
    operations = value.get('operations')
    if not isinstance(operations, list):
        raise CommentsFileError('comments.json has no `operations` list')
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise CommentsFileError('comments.json: operations[%d] is not an object' % index)
    extra = dict((key, item) for key, item in value.items()
                 if key not in ('format', 'version', 'prototypeId', 'operations'))
    return CommentsDocument(prototype_id, operations, extra)


def prefix_fingerprint(operations, count=None):
    """SHA-256 (hex, 16 chars) of the `id`s of the first `count` operations joined with `\\n`."""
    selected = operations if count is None else operations[:count]
    ids = [operation.get('id') if isinstance(operation, dict) and isinstance(operation.get('id'), str) else ''
           for operation in selected]
    return _sha256_hex('\n'.join(ids))[:16]


# ── Operation schema ─────────────────────────────────────────────────────────

def _bad(message, code='invalid_operation', status=400):
    raise OperationError(status, code, message)


def _check_object(value, where, required=(), optional=()):
    if not isinstance(value, dict):
        _bad('%s must be an object' % where)
    for key in required:
        if key not in value:
            _bad('%s.%s is required' % (where, key))
    unknown = [key for key in value if key not in required and key not in optional]
    if unknown:
        _bad('%s has unknown fields: %s' % (where, ', '.join(sorted(str(key) for key in unknown))))


def _check_string(value, where, minimum=1, maximum=None, pattern=None, strip_empty=False):
    if not isinstance(value, str):
        _bad('%s must be a string' % where)
    if not _encodable(value):
        _bad('%s contains a lone surrogate' % where)
    if strip_empty and not value.strip():
        _bad('%s must not be empty' % where)
    length = text_length(value)
    if length < minimum:
        _bad('%s is too short' % where)
    if maximum is not None and length > maximum:
        _bad('%s is longer than %d characters' % (where, maximum), code='text_too_long')
    if pattern is not None and not pattern.fullmatch(value):
        _bad('%s has an invalid format' % where)


def _check_choice(value, where, choices):
    if not isinstance(value, str) or value not in choices:
        _bad('%s must be one of: %s' % (where, ', '.join(choices)))


MAX_SAFE_INTEGER = 2 ** 53   # JavaScript Number.MAX_SAFE_INTEGER + 1: larger integers do not survive the engine


def _check_int(value, where, minimum=1):
    if not _is_int(value) or value < minimum:
        _bad('%s must be an integer >= %d' % (where, minimum))
    if value > MAX_SAFE_INTEGER:
        _bad('%s must be at most %d' % (where, MAX_SAFE_INTEGER))


def _check_unit(value, where):
    if not _is_number(value) or value < 0 or value > 1:
        _bad('%s must be a number between 0 and 1' % where)


def _check_proto_id(value, where):
    _check_string(value, where, maximum=PROTO_ID_MAX, pattern=PROTO_ID_RE)


def _check_anchor(anchor, where, allow_lost=True):
    if not isinstance(anchor, dict):
        _bad('%s must be an object' % where)
    kind = anchor.get('kind')
    _check_choice(kind, where + '.kind', ANCHOR_KINDS if allow_lost else ('element', 'screen'))
    if kind == 'element':
        _check_object(anchor, where, ('kind', 'protoId', 'x', 'y', 'iteration'))
        _check_proto_id(anchor['protoId'], where + '.protoId')
    elif kind == 'screen':
        _check_object(anchor, where, ('kind', 'x', 'y', 'iteration'))
    else:
        _check_object(anchor, where, ('kind',), ('protoId', 'x', 'y', 'iteration'))
        if 'protoId' in anchor:
            _check_proto_id(anchor['protoId'], where + '.protoId')
    if 'x' in anchor:
        _check_unit(anchor['x'], where + '.x')
    if 'y' in anchor:
        _check_unit(anchor['y'], where + '.y')
    if 'iteration' in anchor:
        _check_int(anchor['iteration'], where + '.iteration')


def _check_message(message, where, imported):
    optional = ('importedAuthor',) if imported else ()
    _check_object(message, where, ('id', 'text'), optional)
    _check_string(message['id'], where + '.id', pattern=LEGACY_MESSAGE_ID_RE if imported else MESSAGE_ID_RE)
    # The 4000-unit limit applies to new entries; imported (v1) texts are kept whole.
    _check_string(message['text'], where + '.text', minimum=0 if imported else 1,
                  maximum=None if imported else MAX_TEXT, strip_empty=not imported)
    if 'importedAuthor' in message:
        _check_string(message['importedAuthor'], where + '.importedAuthor', minimum=0, maximum=MAX_NAME)


def _check_hint(hint, where, imported):
    _check_object(hint, where, ('text',), ('legacyPath',) if imported else ())
    _check_string(hint['text'], where + '.text', minimum=0, maximum=MAX_HINT)
    if 'legacyPath' in hint:
        _check_string(hint['legacyPath'], where + '.legacyPath', minimum=0, maximum=1000)


def _check_screen(value, where):
    _check_string(value, where, pattern=SCREEN_ID_RE)


def _check_hash(value, where):
    _check_string(value, where, pattern=HASH_RE)


def validate_payload(kind, payload, origin='file'):
    """Validate `payload` of an operation `kind`.

    `origin`: `browser` (POST from the engine: no `lost` anchors, no migration-only
    fields, `resolve` only with `reason: author`), `import` (migration: `lost`,
    `importedAuthor`, `hint.legacyPath`, empty texts) or `file`/`cli` (any valid stored form).
    Raises `OperationError` (400).
    """
    browser = origin == 'browser'
    imported = origin in ('import', 'file')
    where = 'payload'
    if kind == 'create':
        _check_object(payload, where, ('message', 'screen', 'anchor'), ('label', 'hint', 'view'))
        _check_message(payload['message'], where + '.message', imported)
        _check_screen(payload['screen'], where + '.screen')
        if 'view' in payload:
            _check_choice(payload['view'], where + '.view', VIEW_VALUES)
        _check_anchor(payload['anchor'], where + '.anchor', allow_lost=not browser)
        if 'label' in payload:
            _check_string(payload['label'], where + '.label', minimum=0, maximum=MAX_LABEL)
        if 'hint' in payload:
            _check_hint(payload['hint'], where + '.hint', imported)
    elif kind == 'reply':
        _check_object(payload, where, ('message',))
        _check_message(payload['message'], where + '.message', imported)
    elif kind == 'edit':
        _check_object(payload, where, ('messageId', 'text'))
        _check_string(payload['messageId'], where + '.messageId', pattern=LEGACY_MESSAGE_ID_RE)
        _check_string(payload['text'], where + '.text', maximum=MAX_TEXT, strip_empty=True)
    elif kind == 'retract':
        _check_object(payload, where, ('messageId',))
        _check_string(payload['messageId'], where + '.messageId', pattern=LEGACY_MESSAGE_ID_RE)
    elif kind == 'vote':
        _check_object(payload, where, ('messageId', 'value'))
        _check_string(payload['messageId'], where + '.messageId', pattern=LEGACY_MESSAGE_ID_RE)
        _check_choice(payload['value'], where + '.value', VOTE_VALUES)
    elif kind == 'resolve':
        _check_object(payload, where, ('reason',), ('iteration',))
        _check_choice(payload['reason'], where + '.reason', ('author',) if browser else RESOLVE_REASONS)
        if 'iteration' in payload:
            _check_int(payload['iteration'], where + '.iteration')
    elif kind in ('reopen', 'delete'):
        _check_object(payload, where)
    elif kind == 'reanchor':
        _check_object(payload, where, ('screen', 'anchor'), ('label', 'hint'))
        _check_screen(payload['screen'], where + '.screen')
        _check_anchor(payload['anchor'], where + '.anchor', allow_lost=not browser)
        if 'label' in payload:
            _check_string(payload['label'], where + '.label', minimum=0, maximum=MAX_LABEL)
        if 'hint' in payload:
            _check_hint(payload['hint'], where + '.hint', imported)
    elif kind == 'triage':
        _check_object(payload, where, ('class',), ('note',))
        _check_choice(payload['class'], where + '.class', TRIAGE_CLASSES)
        if 'note' in payload:
            _check_string(payload['note'], where + '.note', minimum=0, maximum=MAX_TEXT)
    elif kind == 'decision':
        _check_object(payload, where, ('kind', 'outcome', 'text'), ('who', 'iteration'))
        _check_choice(payload['kind'], where + '.kind', DECISION_KINDS)
        _check_choice(payload['outcome'], where + '.outcome', DECISION_OUTCOMES)
        _check_string(payload['text'], where + '.text', minimum=0, maximum=MAX_TEXT)
        if payload['kind'] == 'developer' and 'who' not in payload:
            _bad('payload.who is required for kind developer')
        if 'who' in payload:
            _check_string(payload['who'], where + '.who', maximum=320)
        if 'iteration' in payload:
            _check_int(payload['iteration'], where + '.iteration')
    elif kind == 'accept':
        _check_object(payload, where, ('scope', 'screens', 'iteration', 'via'), ('who',))
        _check_choice(payload['scope'], where + '.scope', ACCEPT_SCOPES)
        screens = payload['screens']
        if not isinstance(screens, dict) or not screens:
            _bad('payload.screens must be a non-empty object')
        for screen, digest in screens.items():
            _check_screen(screen, where + '.screens key')
            _check_hash(digest, where + '.screens.' + screen)
        _check_int(payload['iteration'], where + '.iteration')
        _check_choice(payload['via'], where + '.via', ACCEPT_VIA)
        if 'who' in payload:
            _check_string(payload['who'], where + '.who', maximum=320)
    elif kind == 'unaccept':
        _check_object(payload, where, ('screens',))
        screens = payload['screens']
        if not isinstance(screens, list) or not screens:
            _bad('payload.screens must be a non-empty list')
        for screen in screens:
            _check_screen(screen, where + '.screens[]')
    elif kind == 'iteration':
        _check_object(payload, where, ('number', 'path', 'hash'), ('commit',))
        _check_int(payload['number'], where + '.number')
        if payload['path'] != 'iterations/%d/' % payload['number']:
            _bad('payload.path must be iterations/<number>/')
        _check_hash(payload['hash'], where + '.hash')
        if 'commit' in payload:
            _check_string(payload['commit'], where + '.commit', pattern=COMMIT_RE)
    elif kind == 'restore':
        _check_object(payload, where, ('screen', 'from', 'frozen', 'reason', 'hashBefore', 'hashAfter'), ('who',))
        _check_screen(payload['screen'], where + '.screen')
        _check_int(payload['from'], where + '.from')
        _check_int(payload['frozen'], where + '.frozen')
        _check_string(payload['reason'], where + '.reason', maximum=MAX_REASON, strip_empty=True)
        _check_hash(payload['hashBefore'], where + '.hashBefore')
        _check_hash(payload['hashAfter'], where + '.hashAfter')
        if 'who' in payload:
            _check_string(payload['who'], where + '.who', maximum=320)
    else:
        _bad('unknown operation type', code='invalid_type')


def validate_operation(operation, origin='file'):
    """Full schema check of a stored operation (top-level keys and payload).  Raises `OperationError`."""
    if not isinstance(operation, dict):
        _bad('operation must be an object')
    kind = operation.get('type')
    if not isinstance(kind, str) or kind not in OPERATION_TYPES:
        _bad('unknown operation type', code='invalid_type')
    thread = kind in THREAD_TYPES
    required = ('id', 'type', 'at', 'by', 'payload') + (('threadId',) if thread else ())
    _check_object(operation, 'operation', required, ('cid',))
    _check_string(operation['id'], 'id', pattern=OP_ID_RE)
    if parse_at(operation['at']) is None:
        _bad('at must be YYYY-MM-DDTHH:MM:SS.mmmZ')
    _check_string(operation['by'], 'by', maximum=320)
    if thread:
        _check_string(operation['threadId'], 'threadId', pattern=LEGACY_THREAD_ID_RE)
    if 'cid' in operation:
        _check_string(operation['cid'], 'cid', pattern=CID_RE)
    if origin == 'file' and operation['by'] == 'import':
        origin = 'import'
    validate_payload(kind, operation['payload'], origin)


# ── Fold v2 ──────────────────────────────────────────────────────────────────

def is_operation(operation):
    """Fold step 1 filter: `id` and `at` non-empty strings, known `type`, `threadId` for thread types."""
    if not isinstance(operation, dict):
        return False
    for key in ('id', 'at'):
        if not _is_str(operation.get(key)):
            return False
    kind = operation.get('type')
    if not isinstance(kind, str) or kind not in OPERATION_TYPES:
        return False
    if kind in THREAD_TYPES and not _is_str(operation.get('threadId')):
        return False
    return True


def normalize_operations(operations):
    """Drop invalid entries; for a repeated `id` the first valid entry wins."""
    seen, result = set(), []
    for operation in operations if isinstance(operations, list) else []:
        if not is_operation(operation) or operation['id'] in seen:
            continue
        seen.add(operation['id'])
        result.append(operation)
    return result


def sort_operations(operations):
    """Order by (`at`, `id`) compared as UTF-16 code units (JavaScript `<`)."""
    return sorted(operations, key=lambda item: (_utf16_key(item['at']), _utf16_key(item['id'])))


def js_truthy(value):
    """JavaScript truthiness of a JSON value: null, false, 0 and "" are falsy; objects and arrays are truthy."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and value == value
    if isinstance(value, str):
        return value != ''
    return True


def _is_integral(value):
    """JavaScript `typeof v === 'number' && isFinite(v) && Math.floor(v) === v` for JSON values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer()
    return True


def _payload(operation):
    """`payloadOf`: the payload when it is a plain object (not a list), else `{}`."""
    payload = operation.get('payload')
    return payload if isinstance(payload, dict) else {}


def _author(operation):
    """`authorOf`: a non-empty string `by`, else None."""
    value = operation.get('by')
    return value if _is_str(value) else None


def _message_id(raw):
    return raw['id'] if isinstance(raw, dict) and isinstance(raw.get('id'), str) else None


def _fold_message(raw, by, at, opening):
    """`makeMessage` of the engine."""
    source = raw if isinstance(raw, dict) else {}
    imported = source.get('importedAuthor')
    return {'id': _message_id(raw), 'text': source['text'] if isinstance(source.get('text'), str) else '',
            'by': by, 'at': at, 'opening': opening,
            'importedAuthor': imported if isinstance(imported, str) else None,
            'edited': False, 'editedAt': None, 'retracted': False, 'votes': {}}


def anchor_kind(anchor):
    """`anchorKind`: `element` (object with kind element and a non-empty protoId), `lost`, otherwise `screen`."""
    if not isinstance(anchor, dict):
        return 'screen'
    if anchor.get('kind') == 'element' and _is_str(anchor.get('protoId')):
        return 'element'
    if anchor.get('kind') == 'lost':
        return 'lost'
    return 'screen'


def _find_message(thread, message_id):
    """`findMessage`: only a non-empty string id matches."""
    if thread is None or not _is_str(message_id):
        return None
    for message in thread['messages']:
        if message['id'] == message_id:
            return message
    return None


_DECISION_STATUS = {
    'apply': ('decided', None), 'variant': ('decided', None), 'keep': ('closed', 'kept'),
    'out_of_scope': ('closed', 'out_of_scope'), 'defer': ('deferred', None),
}


def _apply_thread_operation(thread, operation, payload, by):
    """`applyThreadOperation`: True (applied), False (skipped) or 'hide' (retract hides the thread)."""
    kind = operation['type']
    if kind == 'reply':
        if not js_truthy(payload.get('message')):
            return False
        reply_id = _message_id(payload['message'])
        if any(item['id'] == reply_id for item in thread['messages']):
            return False
        thread['messages'].append(_fold_message(payload['message'], by, operation['at'], False))
        return True
    if kind == 'edit':
        message = _find_message(thread, payload.get('messageId'))
        if (message is None or message['retracted'] or by is None or message['by'] != by
                or not isinstance(payload.get('text'), str)):
            return False
        message['text'] = payload['text']
        message['edited'] = True
        message['editedAt'] = operation['at']
        return True
    if kind == 'retract':
        message = _find_message(thread, payload.get('messageId'))
        if message is None or message['retracted'] or by is None or message['by'] != by:
            return False
        message['retracted'] = True
        if message['opening'] and not any(not item['opening'] and not item['retracted'] and item['by'] != message['by']
                                          for item in thread['messages']):
            return 'hide'
        return True
    if kind == 'vote':
        message = _find_message(thread, payload.get('messageId'))
        value = payload.get('value')
        if message is None or message['retracted'] or by is None or not isinstance(value, str) or value not in VOTE_VALUES:
            return False
        if value == 'none':
            message['votes'].pop(by, None)
        else:
            message['votes'][by] = value
        return True
    if kind == 'resolve':
        thread['status'] = 'closed'
        thread['statusReason'] = payload['reason'] if isinstance(payload.get('reason'), str) else None
        return True
    if kind == 'reopen':
        thread['status'] = 'open'
        thread['statusReason'] = None
        return True
    if kind == 'reanchor':
        if not isinstance(payload.get('screen'), str):
            return False
        thread['screen'] = payload['screen']
        thread['anchor'] = payload['anchor'] if isinstance(payload.get('anchor'), dict) else None
        thread['label'] = payload['label'] if isinstance(payload.get('label'), str) else ''
        return True
    if kind == 'triage':
        klass = payload.get('class')
        if not isinstance(klass, str) or klass not in TRIAGE_CLASSES:
            return False
        note = payload.get('note')
        thread['triage'] = {'class': klass, 'note': note if isinstance(note, str) else None,
                            'at': operation['at'], 'by': by}
        if klass == 'disputed':
            thread['status'] = 'awaiting'
            thread['statusReason'] = None
        return True
    if kind == 'decision':
        decision_kind, outcome = payload.get('kind'), payload.get('outcome')
        if (not isinstance(decision_kind, str) or decision_kind not in DECISION_KINDS
                or not isinstance(outcome, str) or outcome not in _DECISION_STATUS):
            return False
        who, text, iteration = payload.get('who'), payload.get('text'), payload.get('iteration')
        thread['decisions'].append({
            'id': operation['id'], 'kind': decision_kind, 'who': who if isinstance(who, str) else None,
            'outcome': outcome, 'text': text if isinstance(text, str) else '',
            'iteration': iteration if _is_integral(iteration) else None, 'at': operation['at'], 'by': by,
        })
        thread['status'], thread['statusReason'] = _DECISION_STATUS[outcome]
        return True
    return False


def fold_operations(operations):
    """Fold v2 of a raw operation list, a 1:1 port of `foldDocument` in the engine
    (`skills/prototype/assets/repo/prototype.js`, interpretations 1-14 of step 1.4).

    Steps 1-4 as v1: normalization, sort by (`at`, `id`) in UTF-16 units, tombstone
    `delete`, a thread is born from its first `create` with a JS-truthy `payload.message`
    and a string `payload.screen`; numbers count those creates over all threads (deleted
    and hidden ones leave gaps).

    Result keys shared with the engine: `threads` (visible, in creation order),
    `currentIteration`, `removed` ({threadId: 'retracted'|'deleted'}).  Python extras:
    `byId` (visible threads), `numbers`, `deleted` (set), `hidden` (set),
    `iterationDuplicates`, `restores`.

    `acceptance` (shared with the engine, step A of the F3 contract): `{sN: {hash, by, who, at,
    iteration, scope, via, opId}}` for every screen whose LAST covering `accept`/`unaccept` is an
    `accept`; keys sorted.  An `accept` counts when `payload.screens` is an object; its entries with a
    non-empty string hash are taken (others ignored); `scope` and `via` are kept when strings, else None.
    An `unaccept` counts when `payload.screens` is a list; its non-empty string elements drop the entry.
    `who` = `payload.who` for `via: cli` (a non-empty string), else `by`;
    `iteration` = the payload number when integral, else None.  Validity is not part of the fold
    (see `acceptance_state`).

    Thread: `{id, number, screen, anchor (dict|None), label, hint (dict|None), by, at,
    status, statusReason, triage ({class, note, at, by}|None), decisions [{id, kind, who,
    outcome, text, iteration, at, by}], messages, lastAt, events [{type, by, at}]}`.
    Message: `{id (str|None), text, by, at, opening, importedAuthor, edited, editedAt,
    retracted, votes {login: 'up'|'down'}}`.
    """
    ordered = sort_operations(normalize_operations(operations))
    deleted = set(operation['threadId'] for operation in ordered if operation['type'] == 'delete')
    numbers = {}
    for operation in ordered:
        if operation['type'] != 'create' or operation['threadId'] in numbers:
            continue
        payload = _payload(operation)
        if js_truthy(payload.get('message')) and isinstance(payload.get('screen'), str):
            numbers[operation['threadId']] = len(numbers) + 1
    hidden, threads, order = set(), {}, []
    max_iteration = 0
    iteration_counts, restores = {}, []
    acceptance = {}
    for operation in ordered:
        kind = operation['type']
        payload = _payload(operation)
        if kind in GLOBAL_TYPES:
            number = payload.get('number')
            if kind == 'accept':
                _fold_accept(acceptance, operation, payload)
            elif kind == 'unaccept':
                screens = payload.get('screens')
                if isinstance(screens, list):
                    for screen in screens:
                        if _is_str(screen):
                            acceptance.pop(screen, None)
            elif kind == 'iteration' and _is_integral(number) and number >= 1:
                iteration_counts[int(number)] = iteration_counts.get(int(number), 0) + 1
                if number > max_iteration:
                    max_iteration = int(number)
            elif kind == 'restore':
                restores.append(operation)
            continue
        thread_id = operation['threadId']
        if thread_id in deleted or thread_id in hidden:
            continue
        by = _author(operation)
        thread = threads.get(thread_id)
        if kind == 'create':
            if thread is not None or not js_truthy(payload.get('message')) or not isinstance(payload.get('screen'), str):
                continue
            threads[thread_id] = {
                'id': thread_id, 'number': numbers[thread_id], 'screen': payload['screen'],
                'anchor': payload['anchor'] if isinstance(payload.get('anchor'), dict) else None,
                'label': payload['label'] if isinstance(payload.get('label'), str) else '',
                'hint': payload['hint'] if isinstance(payload.get('hint'), dict) else None,
                'view': 'mobile' if payload.get('view') == 'mobile' else 'desktop',
                'by': by, 'at': operation['at'], 'status': 'open', 'statusReason': None, 'triage': None,
                'decisions': [], 'messages': [_fold_message(payload['message'], by, operation['at'], True)],
                'lastAt': operation['at'], 'events': [{'type': 'create', 'by': by, 'at': operation['at']}],
            }
            order.append(thread_id)
            continue
        if thread is None:
            continue
        result = _apply_thread_operation(thread, operation, payload, by)
        if result == 'hide':
            hidden.add(thread_id)
            continue
        if result:
            thread['events'].append({'type': kind, 'by': by, 'at': operation['at']})
            thread['lastAt'] = operation['at']
    removed = dict((thread_id, 'retracted') for thread_id in hidden)
    removed.update((thread_id, 'deleted') for thread_id in deleted)
    visible = [threads[thread_id] for thread_id in order if thread_id not in hidden]
    return {
        'threads': visible,
        'currentIteration': max_iteration + 1,
        'removed': removed,
        'byId': dict((thread['id'], thread) for thread in visible),
        'numbers': numbers,
        'deleted': deleted,
        'hidden': hidden,
        'iterationDuplicates': sorted(number for number, count in iteration_counts.items() if count > 1),
        'restores': restores,
        'acceptance': dict((screen, acceptance[screen]) for screen in sorted(acceptance, key=_utf16_key)),
    }


def _fold_accept(acceptance, operation, payload):
    """Apply one `accept` to the acceptance map of the fold (see `fold_operations`)."""
    screens = payload.get('screens')
    scope, via = payload.get('scope'), payload.get('via')
    if not isinstance(screens, dict):
        return
    by = _author(operation)
    who = payload.get('who')
    iteration = payload.get('iteration')
    for screen, digest in screens.items():
        if not _is_str(digest):
            continue
        acceptance[screen] = {
            'hash': digest, 'by': by, 'who': who if via == 'cli' and _is_str(who) else by, 'at': operation['at'],
            'iteration': int(iteration) if _is_integral(iteration) else None,
            'scope': scope if isinstance(scope, str) else None, 'via': via if isinstance(via, str) else None,
            'opId': operation['id'],
        }


def acceptance_state(fold, screens, aliases=None):
    """`{sN: {valid, who, at, iteration, via}}` for screens with an acceptance in `fold`.

    `screens` = `{sN: current hash}`; `valid` = the accepted hash equals the current one.  Screens
    missing from `screens` (removed or examples) are left out.
    """
    state = {}
    for screen, entry in fold['acceptance'].items():
        if screen not in screens:
            continue
        valid = entry['hash'] == screens[screen] or entry['hash'] in ((aliases or {}).get(screen) or ())
        state[screen] = {'valid': valid, 'who': entry['who'], 'at': entry['at'],
                         'iteration': entry['iteration'], 'via': entry['via']}
    return state


def open_count_by_screen(threads):
    """`openCountByScreen`: open/awaiting/decided threads per `screen` (handles not checked)."""
    counts = {}
    for thread in threads:
        if thread['status'] in OPEN_STATUSES:
            counts[thread['screen']] = counts.get(thread['screen'], 0) + 1
    return counts


def vote_summary(message):
    """`voteSummary`: `{up: [logins], down: [logins]}` sorted in UTF-16 order."""
    votes = message.get('votes') or {}
    return {'up': sorted((login for login, value in votes.items() if value == 'up'), key=_utf16_key),
            'down': sorted((login for login, value in votes.items() if value == 'down'), key=_utf16_key)}


def thread_has_handle(thread, screen_ids=None, proto_ids_by_screen=None):
    """True when the thread has a handle in the current version.

    `screen_ids`: ids of existing screens (None = do not check); `proto_ids_by_screen`:
    {screen: iterable of data-proto-id} from `collect_proto_ids` (None = do not check).
    `anchor_kind` decides the kind: `lost` never has a handle, `screen` has one while its
    screen exists, `element` while its `protoId` is in that screen.
    """
    kind = anchor_kind(thread.get('anchor'))
    if kind == 'lost':
        return False
    if screen_ids is not None and thread.get('screen') not in screen_ids:
        return False
    if kind == 'screen' or proto_ids_by_screen is None:
        return True
    return thread['anchor']['protoId'] in proto_ids_by_screen.get(thread.get('screen'), ())


def thread_counts(fold, screen_ids=None, proto_ids_by_screen=None):
    """`{open, awaiting, decided, closed, deferred, lost}` of visible threads.

    Status counts include threads without a handle; `lost` counts threads without a
    handle whose status is open/awaiting/decided (the panel filter "Bez uchwytu").
    """
    counts = dict((status, 0) for status in STATUSES)
    counts['lost'] = 0
    for thread in fold['threads']:
        counts[thread['status']] = counts.get(thread['status'], 0) + 1
        if thread['status'] in OPEN_STATUSES and not thread_has_handle(thread, screen_ids, proto_ids_by_screen):
            counts['lost'] += 1
    return counts


def open_thread_count(fold):
    """Open threads for `prototype list` (`comments`): status open, awaiting or decided."""
    return sum(1 for thread in fold['threads'] if thread['status'] in OPEN_STATUSES)


def thread_hints(thread, has_handle=True):
    """Facts for the agent: `multiple-authors`, `down-majority`, `down-without-discussion`, `orphan`."""
    hints = []
    live = [message for message in thread['messages'] if not message['retracted']]
    if len(set(message['by'] for message in live)) >= 2:
        hints.append('multiple-authors')
    opening = thread['messages'][0]
    if not opening['retracted']:
        values = list(opening['votes'].values())
        downs, ups = values.count('down'), values.count('up')
        if downs > ups:
            hints.append('down-majority')
        if downs:
            repliers = set(message['by'] for message in live if not message['opening'])
            down_voters = [login for login, value in opening['votes'].items() if value == 'down']
            if not any(login in repliers for login in down_voters):
                hints.append('down-without-discussion')
    if not has_handle:
        hints.append('orphan')
    return hints


# ── People and labels ────────────────────────────────────────────────────────

def person_from_login(login, name=None):
    """`personFromLogin`: `{login, firstName, surname, fullName}`.

    Words = `name` split on whitespace when set, else the part before `@` split on dots
    (empty parts dropped) with the first code point upper-cased; `fullName` = words joined
    with spaces (or the login), `firstName` = first word (or the login), `surname` = the rest.
    """
    login = login if isinstance(login, str) else ''
    if isinstance(name, str) and name.strip():
        words = name.split()
    else:
        words = [word[:1].upper() + word[1:] for word in login.split('@')[0].split('.') if word]
    return {'login': login, 'firstName': words[0] if words else login, 'surname': ' '.join(words[1:]),
            'fullName': ' '.join(words) or login}


def person_names(login, name=None):
    """`(firstName, fullName)` of `person_from_login`."""
    person = person_from_login(login, name)
    return person['firstName'], person['fullName']


def compute_labels(logins, names=None):
    """`computeLabels`: `{login: {firstName, fullName, label}}`.

    Unique `firstName` -> label `firstName`; within a group sharing `firstName`:
    `firstName + ' ' + upper(first code point of surname) + '.'` (no surname -> `firstName`);
    when that short label repeats within the group -> `fullName`.
    """
    names = names or {}
    people, order = {}, []
    for login in logins or []:
        if not _is_str(login) or login in people:
            continue
        people[login] = person_from_login(login, names.get(login))
        order.append(login)
    groups = {}
    for login in order:
        groups.setdefault(people[login]['firstName'], []).append(login)
    result = {}
    for first, group in groups.items():
        if len(group) == 1:
            result[group[0]] = {'firstName': first, 'fullName': people[group[0]]['fullName'], 'label': first}
            continue
        short = {}
        for login in group:
            surname = people[login]['surname']
            short[login] = '%s %s.' % (first, surname[:1].upper()) if surname else first
        for login in group:
            same = sum(1 for other in group if short[other] == short[login])
            result[login] = {'firstName': first, 'fullName': people[login]['fullName'],
                             'label': people[login]['fullName'] if same > 1 else short[login]}
    return result


def collect_logins(operations):
    """Logins of the log as the engine (`peopleFromOperations`): `by` of normalized operations, in file order."""
    logins = []
    for operation in normalize_operations(operations):
        by = _author(operation)
        if by is not None and by not in logins:
            logins.append(by)
    return logins


def accept_who_logins(operations):
    """Logins from `payload.who` of `accept` operations with `via: cli` (their `by` is `agent`), in file order.

    `GET …/operations` adds them to `people` so the UI shows the name from the users file; the engine
    parity of `collect_logins` stays untouched."""
    logins = []
    for operation in normalize_operations(operations):
        if operation['type'] != 'accept':
            continue
        payload = _payload(operation)
        who = payload.get('who')
        if payload.get('via') == 'cli' and _is_str(who) and who not in logins:
            logins.append(who)
    return logins


def compute_people(logins, names=None):
    """`people` of `GET …/operations`: `compute_labels` sorted by login."""
    labels = compute_labels(logins, names)
    return dict((login, labels[login]) for login in sorted(labels))


def people_from_operations(operations, names=None):
    """`peopleFromOperations` of the engine."""
    return compute_labels(collect_logins(operations), names)


# ── HTML: positions, sections, data-proto-id ─────────────────────────────────

VOID_ELEMENTS = frozenset('area base br col embed hr img input keygen link meta param source track wbr'.split())
COMMENTABLE_TAGS = frozenset('button input select textarea h1 h2 h3 h4 th table form'.split())
COMMENTABLE_CLASSES = ('card', 'modal', 'drawer')
_CLASS_SPLIT = re.compile(r'[ \t\n\f\r]+')


def _attr(attrs, name):
    """Value of the first attribute `name` ('' for a valueless one), None when absent."""
    for key, value in attrs:
        if key == name:
            return '' if value is None else value
    return None


def _class_tokens(attrs):
    value = _attr(attrs, 'class')
    return [token for token in _CLASS_SPLIT.split(value) if token] if value else []


def is_screen_section(tag, attrs):
    return tag == 'section' and 'screen' in _class_tokens(attrs)


def is_commentable(tag, attrs):
    """Closed list from the spec: button, a[href], input, select, textarea, [role=button],
    [data-goto], h1-h4, th, table, form, .card, .modal, .drawer."""
    if tag in COMMENTABLE_TAGS:
        return True
    if tag == 'a' and _attr(attrs, 'href') is not None:
        return True
    role = _attr(attrs, 'role')
    if role is not None and role.strip().lower() == 'button':
        return True
    if _attr(attrs, 'data-goto') is not None:
        return True
    return any(token in COMMENTABLE_CLASSES for token in _class_tokens(attrs))


def normalize_newlines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


class _SourceParser(HTMLParser):
    """`HTMLParser` that knows absolute offsets of tag events in `source`."""

    def __init__(self, source):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.source = source
        self.broken = False
        self._line_starts = [0]
        for match in re.finditer('\n', source):
            self._line_starts.append(match.end())

    def source_offset(self):
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def tag_end(self, start):
        end = self.source.find('>', start)
        return len(self.source) if end < 0 else end + 1

    def run(self):
        try:
            self.feed(self.source)
            self.close()
        except (AssertionError, ValueError):
            self.broken = True
        self.finish()
        return self

    def finish(self):
        pass


class _SectionParser(_SourceParser):
    def __init__(self, source):
        _SourceParser.__init__(self, source)
        self.sections = []
        self._stack = []
        self.head = None
        self._head_start = None

    def handle_starttag(self, tag, attrs):
        if tag == 'head' and self._head_start is None and self.head is None:
            self._head_start = self.source_offset()
        if tag != 'section':
            return
        start = self.source_offset()
        record = {'id': _attr(attrs, 'id'), 'start': start, 'contentStart': self.tag_end(start), 'end': None,
                  'screen': 'screen' in _class_tokens(attrs),
                  'example': _attr(attrs, 'data-prototype-example') is not None,
                  'depth': len(self._stack)}
        self.sections.append(record)
        self._stack.append(record)

    def handle_startendtag(self, tag, attrs):
        # Browsers ignore `/` on `<section/>`: it opens a section like a start tag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'head' and self._head_start is not None and self.head is None:
            start = self.source_offset()
            self.head = (self._head_start, self.tag_end(start))
        if tag == 'section' and self._stack:
            record = self._stack.pop()
            record['end'] = self.tag_end(self.source_offset())


def section_spans(html):
    """Boundaries of `<section>` elements found by `html.parser` with a nesting counter.

    Returns `{sections, screens, head, problems}`: `sections` every section
    `{id, start, contentStart, end, screen, example, depth}` (offsets into `html`,
    `end` after `</section>`, None when unclosed); `screens` = {id: section} of screen
    sections (`class` token `screen`) with an `id`, first occurrence; `head` = (start, end)
    of `<head>…</head>` or None; `problems` = [{screen, problem}] with `unclosed` and
    `duplicate_id` (a restore/hash on such a screen is ambiguous).
    """
    parser = _SectionParser(html).run()
    screens, problems = {}, []
    for record in parser.sections:
        if not record['screen'] or not record['id']:
            continue
        if record['end'] is None:
            problems.append({'screen': record['id'], 'problem': 'unclosed'})
        if record['id'] in screens:
            problems.append({'screen': record['id'], 'problem': 'duplicate_id'})
            continue
        screens[record['id']] = record
    return {'sections': parser.sections, 'screens': screens, 'head': parser.head, 'problems': problems}


def section_source(html, screen_id, spans=None):
    """Source of `<section … id="screen_id">` up to its `</section>`, or None (missing/ambiguous)."""
    spans = spans or section_spans(html)
    if any(problem['screen'] == screen_id for problem in spans['problems']):
        return None
    record = spans['screens'].get(screen_id)
    if record is None or record['end'] is None:
        return None
    return html[record['start']:record['end']]


class _IdParser(_SourceParser):
    def __init__(self, source):
        _SourceParser.__init__(self, source)
        self.stack = []
        self.screens = []
        self.records = []      # every data-proto-id outside example screens
        self.missing = []
        self.taken = set()     # every data-proto-id value in the document (examples too)

    def _context(self):
        if self.stack:
            return self.stack[-1][1]
        return {'screen': None, 'example': False, 'frame': False}

    def _element(self, tag, attrs, push):
        parent = self._context()
        screen, example, inside_frame = parent['screen'], parent['example'], parent['frame']
        child_frame = inside_frame
        if is_screen_section(tag, attrs):
            screen = _attr(attrs, 'id') or ''
            example = _attr(attrs, 'data-prototype-example') is not None
            self.screens.append({'id': screen, 'example': example})
            child_frame = False
        if 'frame' in _class_tokens(attrs):
            child_frame = True
        value = _attr(attrs, 'data-proto-id')
        if value is not None:
            self.taken.add(value)
        if not example:
            line, column = self.getpos()
            if value is not None:
                self.records.append({'id': value, 'screen': screen, 'line': line, 'column': column + 1})
            elif inside_frame and screen and is_commentable(tag, attrs):
                start = self.source_offset()
                self.missing.append({'screen': screen, 'tag': tag, 'line': line, 'column': column + 1,
                                     'offset': start, 'text': self.get_starttag_text()})
        if push and tag not in VOID_ELEMENTS:
            self.stack.append((tag, {'screen': screen, 'example': example, 'frame': child_frame}))

    def handle_starttag(self, tag, attrs):
        self._element(tag, attrs, True)

    def handle_startendtag(self, tag, attrs):
        self._element(tag, attrs, False)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def _scan_ids(html):
    return _IdParser(html).run()


def collect_proto_ids(html):
    """`data-proto-id` facts of an `index.html` (screens with `data-prototype-example` skipped).

    Returns a JSON-ready dict:
      `screens` [{id, example}] screen sections in document order;
      `byScreen` {screen: [ids in document order]};
      `ids` {id: [screen or None, …]};
      `duplicates` sorted ids used more than once;
      `invalid` [{id, screen, line, column, reason}] (pattern, length);
      `missing` [{screen, tag, line, column}] commentable elements inside `.frame` of a
      screen without `data-proto-id`;
      `ok` no duplicates, invalid nor missing.
    """
    parser = _scan_ids(html)
    by_screen, ids, invalid = {}, {}, []
    for record in parser.records:
        ids.setdefault(record['id'], []).append(record['screen'])
        if record['screen']:
            by_screen.setdefault(record['screen'], []).append(record['id'])
        if not PROTO_ID_RE.fullmatch(record['id']):
            invalid.append(dict(record, reason='pattern'))
        elif len(record['id']) > PROTO_ID_MAX:
            invalid.append(dict(record, reason='length'))
    duplicates = sorted(value for value, screens in ids.items() if len(screens) > 1)
    missing = [dict((key, item[key]) for key in ('screen', 'tag', 'line', 'column')) for item in parser.missing]
    return {'screens': parser.screens, 'byScreen': by_screen, 'ids': ids, 'duplicates': duplicates,
            'invalid': invalid, 'missing': missing,
            'ok': not duplicates and not invalid and not missing}


def proto_ids_by_screen(html):
    """{screen: set(data-proto-id)} for handle checks (`thread_has_handle`)."""
    return dict((screen, set(values)) for screen, values in collect_proto_ids(html)['byScreen'].items())


def _id_part(value, fallback):
    part = re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')
    return part or fallback


def fix_proto_ids(html):
    """Add missing `data-proto-id` (`<screen>-<tag>-<k>`, k per screen and tag in document
    order, skipping taken values); existing attributes are never changed.

    Returns `(new_html, added)`, `added` = [{screen, tag, id, line, column}].  Idempotent.
    """
    parser = _scan_ids(html)
    taken = set(parser.taken)
    counters, plan = {}, []
    for item in parser.missing:
        screen_part = _id_part(item['screen'], 'screen')
        tag_part = _id_part(item['tag'], 'el')
        key = (screen_part, tag_part)
        k = counters.get(key, 1)
        while True:
            suffix = '-%s-%d' % (tag_part, k)
            candidate = screen_part[:max(1, PROTO_ID_MAX - len(suffix))].strip('-') + suffix
            if candidate not in taken:
                break
            k += 1
        counters[key] = k + 1
        taken.add(candidate)
        plan.append((item, candidate))
    result = html
    applied = set()
    for index, (item, candidate) in sorted(enumerate(plan), key=lambda entry: entry[1][0]['offset'], reverse=True):
        start, text = item['offset'], item['text'] or ''
        if result[start:start + len(text)] != text:
            continue
        name = re.match(r'<[^\s/>]+', text)
        position = start + (name.end() if name else len(text) - 1)
        result = result[:position] + ' data-proto-id="%s"' % candidate + result[position:]
        applied.add(index)
    added = [{'screen': item['screen'], 'tag': item['tag'], 'id': candidate,
              'line': item['line'], 'column': item['column']}
             for index, (item, candidate) in enumerate(plan) if index in applied]
    return result, added


# ── v1 anchors (migration step 3) ────────────────────────────────────────────

_BLOCK_CLOSES_P = frozenset(('address article aside blockquote details dialog div dl fieldset figcaption figure '
                             'footer form h1 h2 h3 h4 h5 h6 header hgroup hr main menu nav ol p pre section '
                             'table ul').split())
_TABLE_PATH_TAGS = frozenset('tbody thead tfoot tr td th colgroup caption col'.split())
_NO_SELF_NESTING = frozenset(('a', 'button', 'form'))
_FOREIGN_ROOTS = frozenset(('svg', 'math'))
_PATH_PART = re.compile(r'([a-z][a-z0-9-]*)(?::([1-9][0-9]*))?')


class _Node(object):
    __slots__ = ('tag', 'attrs', 'children', 'parent', 'foreign')

    def __init__(self, tag, attrs, parent, foreign):
        self.tag = tag
        self.attrs = {}
        for key, value in attrs:
            self.attrs.setdefault(key, '' if value is None else value)
        self.children = []
        self.parent = parent
        self.foreign = foreign


class _StrictScreenParser(_SourceParser):
    """Element trees of screen sections with a strict own tag stack.

    A section is ambiguous when its source cannot be trusted to equal the HTML5 DOM:
    an end tag not matching the top of the stack, an element left open at `</section>`,
    a non-void `<x/>` outside SVG/MathML, an end tag of a void element, `<p>` holding a
    block element, nested `a`/`button`/`form`, a nested screen section, or no `</section>`.
    """

    def __init__(self, source):
        _SourceParser.__init__(self, source)
        self.screens = {}
        self.duplicates = set()
        self.stack = []
        self.current = None

    def _ambiguous(self, reason):
        if self.current is not None and not self.current['ambiguous']:
            self.current['ambiguous'] = reason

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, closed=True)

    def _start(self, tag, attrs, closed):
        if self.current is None:
            if is_screen_section(tag, attrs):
                screen_id = _attr(attrs, 'id') or ''
                root = _Node(tag, attrs, None, False)
                record = {'id': screen_id, 'root': root, 'ambiguous': None}
                if screen_id in self.screens:
                    self.duplicates.add(screen_id)
                else:
                    self.screens[screen_id] = record
                self.current = record
                self.stack = [root]
                if closed:
                    self._ambiguous('self_closing_section')
            return
        parent = self.stack[-1]
        foreign = parent.foreign or tag in _FOREIGN_ROOTS
        if is_screen_section(tag, attrs):
            self._ambiguous('nested_screen')
            inner = _attr(attrs, 'id') or ''
            if inner and inner not in self.screens:
                self.screens[inner] = {'id': inner, 'root': None, 'ambiguous': 'nested_screen'}
        if not foreign:
            if parent.tag == 'p' and tag in _BLOCK_CLOSES_P:
                self._ambiguous('p_with_block')
            if tag in _NO_SELF_NESTING and any(node.tag == tag for node in self.stack):
                self._ambiguous('nested_' + tag)
            if tag in ('html', 'head', 'body'):
                self._ambiguous('document_tag')
        node = _Node(tag, attrs, parent, foreign)
        parent.children.append(node)
        if closed:
            if not foreign and tag not in VOID_ELEMENTS:
                self._ambiguous('self_closing_' + tag)
            return
        if tag in VOID_ELEMENTS and not foreign:
            return
        self.stack.append(node)

    def handle_endtag(self, tag):
        if self.current is None:
            return
        top = self.stack[-1]
        if tag in VOID_ELEMENTS and not top.foreign:
            self._ambiguous('void_end_tag')
            return
        if top.tag == tag:
            self.stack.pop()
        else:
            self._ambiguous('mismatched_end_tag')
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index].tag == tag:
                    del self.stack[index:]
                    break
        if not self.stack:
            self.current = None

    def finish(self):
        if self.current is not None:
            self._ambiguous('unclosed_section')
            self.current = None
        for screen_id in self.duplicates:
            self.screens[screen_id]['ambiguous'] = self.screens[screen_id]['ambiguous'] or 'duplicate_screen'


class V1AnchorResolver(object):
    """Resolve v1 positional anchors (`tag:n>…`, `prototype.js:anchorFor`) against `index.html`.

    Use the HTML after `fix_proto_ids`.  `resolve(screen, path)` returns
    `{'kind': 'screen'}` for an empty path, `{'kind': 'element', 'protoId': …}` when the
    path is unambiguous and the target (or its nearest ancestor inside the section) has a
    valid `data-proto-id`, otherwise `{'kind': 'lost', 'reason': …}`.  Unambiguous means:
    the screen section passes the strict stack check and no step is a table part
    (`tbody`, `thead`, `tfoot`, `tr`, `td`, `th`, `colgroup`, `caption`, `col`) or a
    child of `table`.
    """

    def __init__(self, html):
        self._parser = _StrictScreenParser(html).run()
        if self._parser.broken:
            for record in self._parser.screens.values():
                record['ambiguous'] = record['ambiguous'] or 'parser_error'

    def section_problem(self, screen_id):
        record = self._parser.screens.get(screen_id)
        if record is None:
            return 'screen_missing'
        return record['ambiguous']

    def resolve(self, screen_id, path):
        if path is None or path == '':
            return {'kind': 'screen'}
        if not isinstance(path, str):
            return {'kind': 'lost', 'reason': 'invalid_path'}
        record = self._parser.screens.get(screen_id)
        if record is None:
            return {'kind': 'lost', 'reason': 'screen_missing'}
        if record['ambiguous'] or record['root'] is None:
            return {'kind': 'lost', 'reason': 'ambiguous_section'}
        node = record['root']
        for part in path.split('>'):
            match = _PATH_PART.fullmatch(part)
            if not match:
                return {'kind': 'lost', 'reason': 'invalid_path'}
            tag, index = match.group(1), int(match.group(2) or 1)
            if tag in _TABLE_PATH_TAGS or node.tag == 'table':
                return {'kind': 'lost', 'reason': 'table_path'}
            matches = [child for child in node.children if child.tag == tag]
            if index > len(matches):
                return {'kind': 'lost', 'reason': 'not_found'}
            node = matches[index - 1]
        target = node
        while target is not None and target is not record['root']:
            value = target.attrs.get('data-proto-id')
            if isinstance(value, str) and PROTO_ID_RE.fullmatch(value) and len(value) <= PROTO_ID_MAX:
                return {'kind': 'element', 'protoId': value}
            target = target.parent
        return {'kind': 'lost', 'reason': 'no_proto_id'}


def resolve_v1_anchor(html, screen_id, path):
    """One-shot `V1AnchorResolver(html).resolve(screen_id, path)`."""
    return V1AnchorResolver(html).resolve(screen_id, path)


# ── Screen hashes ────────────────────────────────────────────────────────────

TEXT_EXTENSIONS = ('.html', '.css', '.js', '.svg', '.json', '.txt')
_ASSET_EXCLUDED_TOP = frozenset(('comments.json', 'comments.js', 'comments.v1.js', 'questions.json', 'README.md',
                                 'prototype.json', 'prototype.js', 'index.html'))
LEGACY_COMMENTS_KEPT = 'comments.v1.js'
# Otoczka silnika (paski, panel komentarzy, dialogi): nie zmienia ekranów, więc nie wchodzi do hasha ekranu. Dawny wzór
# ją liczył — akceptacje z tamtego hasha zostają ważne przez `aliases` (bieżący plik i historia z `prototype.json`).
ENGINE_ASSETS = ('prototype.css',)
ENGINE_HISTORY_KEY = 'engineAssetHistory'
ENGINE_HISTORY_MAX = 20


def normalize_newline_bytes(data):
    return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


class HashCache(object):
    """Digest cache keyed by `(path, mtime_ns, size)`; thread safe.

    `prototype_hashes(directory)` also memoizes the whole `screen_hashes` result keyed by the
    `(relative path, mtime_ns, size)` of every hashed file and `index.html`, so polling the API
    every 5 s costs only `stat` calls while nothing changes.
    """

    def __init__(self):
        self._items = {}
        self._prototypes = {}
        self._lock = threading.Lock()

    def prototype_hashes(self, directory):
        """`screen_hashes(directory)` from the cache; recomputed after any file change."""
        directory = os.path.abspath(directory)
        signature = hash_signature(directory)
        with self._lock:
            cached = self._prototypes.get(directory)
            if cached is not None and cached[0] == signature:
                return cached[1]
        result = screen_hashes(directory, self)
        with self._lock:
            self._prototypes[directory] = (signature, result)
        return result

    def digest(self, path, text):
        info = os.stat(path)
        key = (info.st_mtime_ns, info.st_size)
        with self._lock:
            cached = self._items.get(path)
            if cached is not None and cached[0] == key:
                return cached[1]
        with open(path, 'rb') as handle:
            data = handle.read()
        if text:
            data = normalize_newline_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._items[path] = (key, digest)
        return digest


# Staging directory of a freeze (`iterations/<n>.tmp-<hex>/`, step 2 of a restore).
_STAGING_RE = re.compile(r'[1-9][0-9]*\.tmp-[0-9a-f]+')


# Never served statically and never hashed: lock and temporary files, dot files, freeze staging
# directories and the kept v1 log of `migrate`.
def _excluded_segment(name):
    """Case-insensitive (macOS and Windows file systems); on Windows trailing dots and spaces are ignored too."""
    lowered = name.lower()
    forms = (lowered, lowered.rstrip('. ')) if os.name == 'nt' else (lowered,)
    return any(item.startswith('.') or item.endswith('.lock') or item.endswith('.tmp')
               or item == LEGACY_COMMENTS_KEPT or bool(_STAGING_RE.fullmatch(item)) for item in forms)


def asset_files(directory):
    """Sorted relative POSIX paths that enter `assets_hash` (symlinks are skipped)."""
    result = []
    base = os.path.abspath(directory)
    for current, dirs, files in os.walk(base, followlinks=False):
        relative_dir = os.path.relpath(current, base)
        top = relative_dir == '.'
        kept = []
        for name in dirs:
            if _excluded_segment(name) or (top and name == 'iterations'):
                continue
            if os.path.islink(os.path.join(current, name)):
                continue
            kept.append(name)
        dirs[:] = sorted(kept)
        for name in files:
            if _excluded_segment(name) or (top and name in _ASSET_EXCLUDED_TOP):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            relative = name if top else relative_dir.replace(os.sep, '/') + '/' + name
            result.append(relative)
    return sorted(result)


def hash_signature(directory):
    """`((relative, mtime_ns, size), …)` of `asset_files` plus `index.html` (missing = None)."""
    items = []
    for relative in asset_files(directory) + ['index.html']:
        try:
            info = os.stat(os.path.join(directory, *relative.split('/')))
        except OSError:
            items.append((relative, None, None))
            continue
        items.append((relative, info.st_mtime_ns, info.st_size))
    return tuple(items)


def assets_hash(directory, cache=None, engine_digest=None):
    """SHA-256 of the sorted lines `path\\0sha256(content)\\n` of prototype assets.

    Text files (`.html .css .js .svg .json .txt`) are hashed after CRLF/CR -> LF.
    Excluded: `iterations/**`, dot files, `*.lock`, `*.tmp`, `comments.v1.js` (anywhere), and at the
    top level `comments.json`, `comments.js`, `README.md`, `prototype.json`, `prototype.js`, `index.html`.
    """
    cache = cache or HashCache()
    lines = []
    files = asset_files(directory)
    for relative in files:
        if relative in ENGINE_ASSETS:
            if engine_digest is None:
                continue  # wzór bieżący: otoczka silnika poza hashem
            lines.append(relative + '\0' + engine_digest + '\n')  # wzór dawny z podanym skrótem pliku
            continue
        path = os.path.join(directory, *relative.split('/'))
        digest = cache.digest(path, relative.lower().endswith(TEXT_EXTENSIONS))
        lines.append(relative + '\0' + digest + '\n')
    if engine_digest is not None and not any(relative in ENGINE_ASSETS for relative in files):
        lines.append(ENGINE_ASSETS[0] + '\0' + engine_digest + '\n')
        lines.sort()
    return _sha256_hex(''.join(lines))


def engine_digests(directory, cache=None):
    """Skróty `prototype.css` do zgodności wstecz: bieżący plik i historia zapisana przez `migrate` w `prototype.json`."""
    cache = cache or HashCache()
    found = []
    path = os.path.join(directory, ENGINE_ASSETS[0])
    if os.path.isfile(path) and not os.path.islink(path):
        try:
            found.append(cache.digest(path, True))
        except OSError:
            pass
    try:
        with open(os.path.join(directory, PROTOTYPE_FILE), 'rb') as handle:
            meta = json.loads(handle.read(64 * 1024).decode('utf-8'))
        history = meta.get(ENGINE_HISTORY_KEY) if isinstance(meta, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        history = None
    for item in history if isinstance(history, list) else []:
        if isinstance(item, str) and HASH_RE.fullmatch(item) and item not in found:
            found.append(item)
    return found


def head_hash(html):
    """SHA-256 of `<head>…</head>` source (LF-normalized `html`); empty source without `<head>`."""
    spans = section_spans(html)
    source = html[spans['head'][0]:spans['head'][1]] if spans['head'] else ''
    return _sha256_hex(source)


def screen_hash(assets_digest, head_digest, section):
    """SHA-256(`assets_hash` + `head_hash` + section source)."""
    return _sha256_hex(assets_digest + head_digest + section)


def prototype_version(screens):
    """SHA-256 of `screen\\0hash\\n` lines sorted by screen id (`version` in the API, F2)."""
    return _sha256_hex(''.join('%s\0%s\n' % (screen, screens[screen]) for screen in sorted(screens)))


def unique_problems(problems):
    """`[{screen, problem}]` of `screen_hashes()['ambiguous']`, one entry per screen id (the first problem);
    entries without an id (`missing_id`) are all kept."""
    seen, result = set(), []
    for item in problems:
        if item['screen'] is not None:
            if item['screen'] in seen:
                continue
            seen.add(item['screen'])
        result.append({'screen': item['screen'], 'problem': item['problem']})
    return result


def ambiguous_screens(spans):
    """Problems of `section_spans` that matter for acceptance: `unclosed`/`duplicate_id` unless every section
    with that id is a `data-prototype-example`, plus `missing_id` for a non-example `class="screen"` section
    without `id`."""
    result = []
    for problem in spans['problems']:
        sections = [item for item in spans['sections'] if item['screen'] and item['id'] == problem['screen']]
        if not all(item['example'] for item in sections):
            result.append(problem)
    for item in spans['sections']:
        if item['screen'] and not item['id'] and not item['example']:
            result.append({'screen': None, 'problem': 'missing_id'})
    return result


def unknown_screen_message(unknown, ambiguous):
    """Message of `unknown_screen`: names the cause for a screen whose section is ambiguous."""
    causes = dict((item['screen'], item['problem']) for item in ambiguous if item['screen'] is not None)
    return '; '.join('screen %s is ambiguous: %s' % (screen, causes[screen]) if screen in causes
                     else 'unknown screen: %s' % screen for screen in unknown)


def read_index_html(directory):
    """`index.html` as LF-normalized text (undecodable bytes kept via surrogateescape)."""
    with open(os.path.join(directory, 'index.html'), 'rb') as handle:
        data = handle.read()
    return normalize_newlines(data.decode('utf-8', 'surrogateescape'))


def screen_hashes(directory, cache=None, extra_engine_digests=()):
    """Hashes of the screens of a prototype directory (screens with `data-prototype-example` skipped).

    Returns `{assets, head, screens: {sN: hash}, version, problems}`; screens with a
    problem from `section_spans` (unclosed, duplicate id) are left out of `screens`.
    """
    cache = cache or HashCache()
    html = read_index_html(directory)
    spans = section_spans(html)
    assets_digest = assets_hash(directory, cache)
    head_source = html[spans['head'][0]:spans['head'][1]] if spans['head'] else ''
    head_digest = _sha256_hex(head_source)
    broken = set(problem['screen'] for problem in spans['problems'])
    screens = {}
    digests = engine_digests(directory, cache)
    digests += [d for d in extra_engine_digests or () if d not in digests]
    legacy = [assets_hash(directory, cache, engine_digest=digest) for digest in digests]
    aliases = {}
    for screen_id, record in spans['screens'].items():
        if record['example'] or screen_id in broken or record['end'] is None:
            continue
        section = html[record['start']:record['end']]
        screens[screen_id] = screen_hash(assets_digest, head_digest, section)
        aliases[screen_id] = [screen_hash(old, head_digest, section) for old in legacy]
    return {'assets': assets_digest, 'head': head_digest, 'screens': screens, 'aliases': aliases,
            'version': prototype_version(screens), 'problems': spans['problems'],
            'ambiguous': ambiguous_screens(spans)}


# ── Locks and atomic writes ──────────────────────────────────────────────────

_PROCESS_LOCKS = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(path):
    key = os.path.normcase(os.path.abspath(path))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = _PROCESS_LOCKS[key] = threading.Lock()
        return lock


def _try_lock_fd(fd):
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _unlock_fd(fd):
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class FileLock(object):
    """`threading.Lock` per path in this process plus an exclusive lock on `path`
    (`fcntl.flock` on POSIX, `msvcrt.locking` on Windows).  Waits up to `timeout`
    seconds in total, then raises `LockTimeout` (HTTP 503)."""

    def __init__(self, path, timeout=LOCK_TIMEOUT, mode=0o666):
        self.path = os.path.abspath(path)
        self.timeout = timeout
        self.mode = mode
        self._thread_lock = _process_lock(self.path)
        self._fd = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        if not self._thread_lock.acquire(timeout=max(0.0, self.timeout)):
            raise LockTimeout()
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            fd = os.open(self.path, flags, self.mode)
            while True:
                try:
                    _try_lock_fd(fd)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise LockTimeout()
                    time.sleep(0.02)
            self._fd = fd
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self._fd is not None:
                try:
                    _unlock_fd(self._fd)
                finally:
                    os.close(self._fd)
                    self._fd = None
        finally:
            self._thread_lock.release()
        return False


def atomic_write_bytes(path, data, mode=None, default_mode=0o644):
    """Atomic replace of `path` with `data`.

    Temporary file `.<name>.<hex>.tmp` in the same directory (`O_EXCL`), `fsync`,
    explicit `chmod` (the existing file's mode, else `mode`, else `default_mode & ~umask`;
    never the 0600 of `mkstemp`), `os.replace`, `fsync` of the directory on POSIX.
    A symlink or non-regular target is refused; the temporary file is removed on error.
    """
    path = os.path.abspath(os.fspath(path))
    directory, name = os.path.split(path)
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError('refusing to replace a symbolic link or non-file: %s' % name)
        final_mode = stat.S_IMODE(info.st_mode) if mode is None else mode
    else:
        final_mode = (default_mode & ~current_umask()) if mode is None else mode
    temporary = os.path.join(directory, '.%s.%s.tmp' % (name, secrets.token_hex(6)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(temporary, final_mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if os.name != 'nt':
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)


def _stat_key(info):
    return (info.st_ino, info.st_mtime_ns, info.st_size)


# ── comments.json store ──────────────────────────────────────────────────────

class Existing(object):
    """Returned by a transaction builder when the operation already exists (dedup by `by`, `cid`)."""

    def __init__(self, operation):
        self.operation = operation


class CommentsSnapshot(object):
    """Immutable view of one `comments.json` state (raw operations in file order)."""

    def __init__(self, slug, operations, extra=None, size=0, key=None, exists=True):
        self.slug = slug
        self.operations = operations
        self.extra = extra or {}
        self.size = size
        self.key = key
        self.exists = exists
        self._fold = None
        self._index = None
        self._lock = threading.Lock()

    @property
    def total(self):
        return len(self.operations)

    def prefix(self, count=None):
        return prefix_fingerprint(self.operations, count)

    @property
    def fold(self):
        with self._lock:
            if self._fold is None:
                self._fold = fold_operations(self.operations)
            return self._fold

    @property
    def index(self):
        """`{threadIds, messageIds, cids, maxAt, maxNewId}` over raw operations."""
        with self._lock:
            if self._index is None:
                thread_ids, message_ids, cids = set(), set(), {}
                max_at, max_id = None, None
                for operation in self.operations:
                    if not isinstance(operation, dict):
                        continue
                    if isinstance(operation.get('threadId'), str):
                        thread_ids.add(operation['threadId'])
                    payload = _payload(operation)
                    message = payload.get('message')
                    if isinstance(message, dict) and isinstance(message.get('id'), str):
                        message_ids.add(message['id'])
                    if isinstance(operation.get('by'), str) and isinstance(operation.get('cid'), str):
                        cids.setdefault((operation['by'], operation['cid']), operation)
                    at_ms = parse_at(operation.get('at'))
                    if at_ms is not None and (max_at is None or at_ms > max_at):
                        max_at = at_ms
                    op_id = operation.get('id')
                    if isinstance(op_id, str) and NEW_OP_ID_RE.fullmatch(op_id) and (max_id is None or op_id > max_id):
                        max_id = op_id
                self._index = {'threadIds': thread_ids, 'messageIds': message_ids, 'cids': cids,
                               'maxAt': max_at, 'maxNewId': max_id}
            return self._index

    def find_cid(self, by, cid):
        return self.index['cids'].get((by, cid))


class TransactionResult(object):
    def __init__(self, operations=None, existing=None, total=0, prefix='', created=False, size=0):
        self.operations = operations or []
        self.existing = existing
        self.total = total
        self.prefix = prefix
        self.created = created
        self.size = size

    @property
    def size_warning(self):
        return self.size >= WARN_FILE_BYTES


class CommentsStore(object):
    """`<directory>/comments.json` of one prototype: cached reads and locked appends.

    `clock` returns seconds since the epoch (default `time.time`); tests inject a fake
    clock here (never through environment variables).  Reads reload the file after a
    change of `(inode, mtime_ns, size)`; `transaction` always re-reads under the lock.
    """

    def __init__(self, directory, slug, clock=None, lock_timeout=LOCK_TIMEOUT, max_bytes=MAX_FILE_BYTES):
        self.directory = os.path.abspath(directory)
        self.slug = slug
        self.path = os.path.join(self.directory, COMMENTS_FILE)
        self.lock_path = self.path + LOCK_SUFFIX
        self.clock = clock or system_clock
        self.lock_timeout = lock_timeout
        self.max_bytes = max_bytes
        self._cache = None
        self._cache_lock = threading.Lock()

    def state(self):
        """`v2` (comments.json), `v1` (comments.js only), `new` (prototype.json only), `absent`."""
        if os.path.lexists(self.path):
            return 'v2'
        if os.path.lexists(os.path.join(self.directory, LEGACY_COMMENTS_FILE)):
            return 'v1'
        if os.path.lexists(os.path.join(self.directory, PROTOTYPE_FILE)):
            return 'new'
        return 'absent'

    def _read(self):
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return CommentsSnapshot(self.slug, [], exists=False)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise CommentsFileError('comments.json must be a regular file')
        key = _stat_key(info)
        with self._cache_lock:
            if self._cache is not None and self._cache.key == key:
                return self._cache
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(self.path, flags)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            data = handle.read()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise CommentsFileError('comments.json is not UTF-8')
        document = parse_document(text, self.slug)
        snapshot = CommentsSnapshot(self.slug, document.operations, document.extra, len(data), _stat_key(info))
        with self._cache_lock:
            self._cache = snapshot
        return snapshot

    def snapshot(self):
        """Current state (an empty snapshot with `exists=False` when the file is missing)."""
        return self._read()

    def transaction(self, build, create=True):
        """Append operations atomically under the lock.

        `build(snapshot)` returns `Existing(operation)` (nothing written), one partial
        operation or a list of partial operations `{type, threadId?, by, cid?, payload}`; it may raise
        `PrototypeServerError`.  The store assigns monotonic `at` = max(now, largest `at`
        + 1 ms) and growing `id`, validates each operation, refuses with 507 when the file
        already has `max_bytes`, and writes the whole canonical document atomically.
        A missing file is created with the header when `create` and `prototype.json`
        exists; a v1 prototype raises `NeedsMigration`.
        """
        with self.lock():
            return self.transaction_locked(build, create)

    def lock(self):
        """The prototype lock (`comments.json.lock`): every write of the prototype (`comments.json`,
        `index.html` of a restore, `iterations/`) happens under it.  Not reentrant."""
        return FileLock(self.lock_path, self.lock_timeout)

    def transaction_locked(self, build, create=True):
        """`transaction` for a caller that already holds `lock()` (iterate, restore, recovery)."""
        snapshot = self._read()
        created = False
        if not snapshot.exists:
            state = self.state()
            if state == 'v1':
                raise NeedsMigration()
            if state != 'new' or not create:
                raise PrototypeServerError(404, 'not_found', 'prototype not found')
            created = True
        result = build(snapshot)
        if isinstance(result, Existing):
            return TransactionResult(existing=result.operation, total=snapshot.total,
                                     prefix=snapshot.prefix(), size=snapshot.size)
        partials = [result] if isinstance(result, dict) else list(result or [])
        if not partials:
            return TransactionResult(total=snapshot.total, prefix=snapshot.prefix(), size=snapshot.size)
        if snapshot.size >= self.max_bytes:
            raise FileTooLarge(snapshot.size)
        now = clock_ms(self.clock)
        last_at = snapshot.index['maxAt']
        last_id = snapshot.index['maxNewId']
        written = []
        for partial in partials:
            at_ms = now if last_at is None else max(now, last_at + 1)
            op_id = new_operation_id(at_ms, last_id)
            operation = {'id': op_id, 'type': partial['type']}
            if partial.get('threadId') is not None:
                operation['threadId'] = partial['threadId']
            operation['at'] = format_at(at_ms)
            operation['by'] = partial['by']
            if partial.get('cid') is not None:
                operation['cid'] = partial['cid']
            operation['payload'] = partial.get('payload', {})
            validate_operation(operation, origin='file')
            written.append(operation)
            last_at, last_id = at_ms, op_id
        operations = snapshot.operations + written
        try:
            data = serialize_document(self.slug, operations).encode('utf-8')
        except OverflowError:
            raise OperationError(400, 'invalid_request', 'a number in the operation is out of range')
        atomic_write_bytes(self.path, data)
        info = os.lstat(self.path)
        fresh = CommentsSnapshot(self.slug, operations, snapshot.extra, len(data), _stat_key(info))
        with self._cache_lock:
            self._cache = fresh
        return TransactionResult(operations=written, total=fresh.total, prefix=fresh.prefix(),
                                 created=created, size=len(data))

    def append(self, partials, create=True):
        """Append ready partial operations (CLI use): `transaction(lambda snapshot: partials)`."""
        return self.transaction(lambda snapshot: list(partials), create=create)

    def append_locked(self, partials, create=True):
        """`append` under a lock already held by the caller."""
        return self.transaction_locked(lambda snapshot: list(partials), create=create)


# ── questions.json store (questions to a domain expert) ──────────────────────

QUESTIONS_FILE = 'questions.json'
QUESTIONS_FORMAT = 1
QUESTIONS_SCHEMA = 'expert-questions/1'
QUESTION_TYPES = ('ask', 'answer', 'comment', 'recorded', 'withdraw')
QUESTION_STATES = ('open', 'answered', 'withdrawn')

# Lengths in UTF-16 code units (like MAX_TEXT).
MAX_QUESTION_TEXT = 2000
MAX_QUESTION_ASSUMED = 500
MAX_QUESTION_REF_TITLE = 200
MAX_QUESTION_REF_HREF = 500
MAX_QUESTION_REF_EXCERPT = 4000
MAX_QUESTION_REFS = 5
MAX_QUESTION_OPTIONS = 8
MAX_QUESTION_OPTION_LABEL = 200
MAX_QUESTION_ANSWER_TEXT = 4000
MAX_QUESTION_COMMENT = 2000
MAX_QUESTION_ADDRESSEES = 50
MAX_QUESTION_LOGIN = 320
QUESTIONS_MAX_FILE_BYTES = 1024 * 1024
QUESTIONS_ASK_LIMIT_BYTES = 768 * 1024   # a new `ask` is refused above this; the rest is a reserve for answers
QUESTIONS_COMMENT_LIMIT_BYTES = 896 * 1024   # a `comment` is refused above this; answers/recorded/withdraw go to the hard limit
QUESTION_WRITE_LIMIT = 60                # HTTP writes (answer, comment) per login ...
QUESTION_WRITE_WINDOW = 60               # ... per this many seconds
QUESTION_COMMENT_MAX = 50                # comments (late answers included) per question

QUESTION_ID_RE = re.compile(r'qn-[0-9a-f]{6,64}')
QUESTION_OPTION_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
QUESTION_ORIGIN_Q_RE = re.compile(r'Q[0-9]{1,6}')
_QUESTION_SCHEME_RE = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')
_QUESTION_CONTROL_RE = re.compile('[\x00-\x1f\x7f]')
# key order of a stored line: the common keys first, then the fields of the type
QUESTION_KEYS = ('id', 'type', 'questionId', 'at', 'by', 'cid', 'text', 'blocking', 'assumed', 'addressees',
                 'refs', 'options', 'refersTo', 'origin', 'multi', 'optionId', 'optionIds', 'confirm', 'comment', 'spec', 'line')
_QUESTION_COMMON = ('id', 'type', 'questionId', 'at', 'by', 'cid')
_QUESTION_FIELDS = {
    'ask': (('text', 'blocking', 'assumed', 'addressees', 'refs', 'options', 'origin'), ('refersTo', 'multi')),
    'answer': ((), ('optionId', 'optionIds', 'text', 'confirm', 'comment')),
    'comment': (('text',), ()),
    'recorded': (('spec', 'line'), ()),
    'withdraw': ((), ()),
}


class QuestionsFileTooLarge(PrototypeServerError):
    """507 `file_too_large` for `questions.json` (same code as `comments.json`)."""

    def __init__(self, size):
        super().__init__(507, 'file_too_large', 'questions.json reached the size limit', {'size': size})


class QuestionsFileError(PrototypeServerError):
    """Unreadable or malformed `questions.json` (409 `invalid_log`; nothing is written)."""

    def __init__(self, message):
        super().__init__(409, 'invalid_log', message)


def new_question_id():
    """`qn-` + 12 hex characters."""
    return 'qn-' + secrets.token_hex(6)


def canonical_question_operation(operation):
    """Stored key order: `id, type, questionId, at, by, cid`, then the type's fields, then any others."""
    ordered = {}
    for key in QUESTION_KEYS:
        if key in operation:
            ordered[key] = operation[key]
    for key, value in operation.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def serialize_question_operation(operation):
    return js_stringify(canonical_question_operation(operation))


def serialize_questions(operations):
    """`{"questionsFormat":1,"operations":[` + one operation per line + `]}`."""
    lines = [serialize_question_operation(operation) for operation in operations]
    return '{"questionsFormat":%d,"operations":[\n' % QUESTIONS_FORMAT + (',\n'.join(lines) + '\n' if lines else '') + ']}\n'


def parse_questions_document(text):
    """Operations of `questions.json` text as stored; `QuestionsFileError` for a wrong format."""
    if text.startswith('﻿'):
        text = text[1:]
    try:
        value = strict_json_loads(text)
    except (ValueError, RecursionError) as error:
        raise QuestionsFileError('questions.json is not valid JSON: %s' % error.__class__.__name__)
    if not isinstance(value, dict):
        raise QuestionsFileError('questions.json must be an object')
    version = value.get('questionsFormat')
    if not _is_int(version) or version != QUESTIONS_FORMAT:
        raise QuestionsFileError('questions.json has an unsupported `questionsFormat`')
    operations = value.get('operations')
    if not isinstance(operations, list):
        raise QuestionsFileError('questions.json has no `operations` list')
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise QuestionsFileError('questions.json: operations[%d] is not an object' % index)
    return operations


# -- validation ---------------------------------------------------------------

def _check_question_login(value, where):
    _check_string(value, where, maximum=MAX_QUESTION_LOGIN)
    if value != value.strip() or _QUESTION_CONTROL_RE.search(value) or re.search(r'\s', value):
        _bad('%s must not contain white space' % where)


def _check_question_path(value, where):
    """Relative path (a spec): no scheme, no leading `/`, no `\\`, no `..` segment."""
    _check_string(value, where, maximum=MAX_QUESTION_REF_HREF)
    if (value.startswith('/') or '\\' in value or _QUESTION_CONTROL_RE.search(value)
            or _QUESTION_SCHEME_RE.match(value) or ':' in value.split('/')[0]
            or any(part == '..' for part in value.split('/'))):
        _bad('%s must be a relative path inside the repository' % where)


def _check_question_href(value, where):
    """A relative reference or an `https:` URL."""
    _check_string(value, where, maximum=MAX_QUESTION_REF_HREF)
    if _QUESTION_CONTROL_RE.search(value) or re.search(r'\s', value) or '\\' in value:
        _bad('%s has an invalid format' % where)
    if value.lower().startswith('https://'):
        if len(value) <= len('https://'):
            _bad('%s has an invalid format' % where)
        return
    if value.startswith('//') or _QUESTION_SCHEME_RE.match(value):
        _bad('%s must be relative or an https URL' % where)


def _check_question_refs(refs):
    if not isinstance(refs, list):
        _bad('refs must be a list')
    if len(refs) > MAX_QUESTION_REFS:
        _bad('refs has more than %d items' % MAX_QUESTION_REFS, code='too_many')
    for index, ref in enumerate(refs):
        where = 'refs[%d]' % index
        _check_object(ref, where, required=('title', 'href'), optional=('excerpt',))
        _check_string(ref['title'], where + '.title', maximum=MAX_QUESTION_REF_TITLE, strip_empty=True)
        _check_question_href(ref['href'], where + '.href')
        if 'excerpt' in ref:
            _check_string(ref['excerpt'], where + '.excerpt', minimum=0, maximum=MAX_QUESTION_REF_EXCERPT)


def _check_question_options(options, multi=False):
    if not isinstance(options, list):
        _bad('options must be a list')
    if len(options) > MAX_QUESTION_OPTIONS:
        _bad('options has more than %d items' % MAX_QUESTION_OPTIONS, code='too_many')
    seen, recommended = set(), 0
    for index, option in enumerate(options):
        where = 'options[%d]' % index
        _check_object(option, where, required=('id', 'label'), optional=('recommended',))
        _check_string(option['id'], where + '.id', pattern=QUESTION_OPTION_ID_RE)
        _check_string(option['label'], where + '.label', maximum=MAX_QUESTION_OPTION_LABEL, strip_empty=True)
        if option['id'] in seen:
            _bad('%s.id is duplicated' % where)
        seen.add(option['id'])
        if 'recommended' in option:
            if not isinstance(option['recommended'], bool):
                _bad('%s.recommended must be a boolean' % where)
            recommended += 1 if option['recommended'] else 0
    if recommended > 1 and not multi:
        _bad('options: at most one option may be recommended (several only in a multi-choice question)')
    if multi and len(options) < 2:
        _bad('a multi-choice question needs at least 2 options', code='multi_needs_options')


def _check_question_addressees(value):
    if value == 'all':
        return
    if not isinstance(value, list) or not value:
        _bad('addressees must be "all" or a non-empty list of logins')
    if len(value) > MAX_QUESTION_ADDRESSEES:
        _bad('addressees has more than %d items' % MAX_QUESTION_ADDRESSEES, code='too_many')
    seen = set()
    for index, login in enumerate(value):
        _check_question_login(login, 'addressees[%d]' % index)
        if login != normalize_login(login):
            _bad('addressees[%d] must be a normalized login' % index)
        if login in seen:
            _bad('addressees[%d] is duplicated' % index)
        seen.add(login)


def validate_question_operation(operation, known=None):
    """Schema of one stored operation of `questions.json` (400 `invalid_operation` and friends).

    `known` (optional) maps `questionId` to the folded question; with it `refersTo` must exist,
    `optionId` must be an option of the question and `confirm` needs a non-null `assumed`.
    There is no `number` field: the label of a question is `origin.q` and its `id`.
    """
    if not isinstance(operation, dict):
        _bad('operation must be an object')
    kind = operation.get('type')
    if not isinstance(kind, str) or kind not in QUESTION_TYPES:
        _bad('type must be one of: %s' % ', '.join(QUESTION_TYPES), code='invalid_type')
    required, optional = _QUESTION_FIELDS[kind]
    _check_object(operation, 'operation', required=_QUESTION_COMMON + required, optional=optional)
    _check_string(operation['id'], 'id', pattern=OP_ID_RE)
    if parse_at(operation['at']) is None:
        _bad('at must be YYYY-MM-DDTHH:MM:SS.mmmZ')
    _check_question_login(operation['by'], 'by')
    _check_string(operation['cid'], 'cid', pattern=CID_RE)
    _check_string(operation['questionId'], 'questionId', pattern=QUESTION_ID_RE)
    question = known.get(operation['questionId']) if known is not None else None
    if kind == 'ask':
        _check_string(operation['text'], 'text', maximum=MAX_QUESTION_TEXT, strip_empty=True)
        blocking, assumed = operation['blocking'], operation['assumed']
        if not isinstance(blocking, bool):
            _bad('blocking must be a boolean')
        if assumed is not None:
            _check_string(assumed, 'assumed', maximum=MAX_QUESTION_ASSUMED, strip_empty=True)
        if blocking == (assumed is not None):
            _bad('a question is either blocking (assumed null) or carries an assumed value')
        _check_question_addressees(operation['addressees'])
        _check_question_refs(operation['refs'])
        multi = operation.get('multi', False)
        if not isinstance(multi, bool):
            _bad('multi must be a boolean')
        _check_question_options(operation['options'], multi)
        _check_object(operation['origin'], 'origin', required=('spec', 'q'))
        _check_question_path(operation['origin']['spec'], 'origin.spec')
        _check_string(operation['origin']['q'], 'origin.q', pattern=QUESTION_ORIGIN_Q_RE)
        if 'refersTo' in operation:
            _check_string(operation['refersTo'], 'refersTo', pattern=QUESTION_ID_RE)
            if operation['refersTo'] == operation['questionId']:
                _bad('refersTo must not point at the question itself')
            if known is not None and operation['refersTo'] not in known:
                _bad('refersTo points at an unknown question', code='unknown_reference')
    elif kind == 'answer':
        present = [key for key in ('optionId', 'optionIds', 'text', 'confirm') if key in operation]
        if len(present) != 1:
            _bad('answer needs exactly one of: optionId, optionIds, text, confirm')
        if 'optionId' in operation:
            _check_string(operation['optionId'], 'optionId', pattern=QUESTION_OPTION_ID_RE)
            if question is not None and question.get('multi'):
                _bad('a multi-choice question takes optionIds (a non-empty list), not optionId', code='wrong_answer_kind')
            if question is not None and operation['optionId'] not in [o['id'] for o in question['options']]:
                _bad('optionId is not an option of the question', code='unknown_option')
        elif 'optionIds' in operation:
            chosen = operation['optionIds']
            if not isinstance(chosen, list):
                _bad('optionIds must be a list')
            if not chosen:
                _bad('optionIds must not be empty: choose at least one option or answer with text', code='empty_selection')
            if len(chosen) > MAX_QUESTION_OPTIONS:
                _bad('optionIds has more than %d items' % MAX_QUESTION_OPTIONS, code='too_many')
            for index, option_id in enumerate(chosen):
                _check_string(option_id, 'optionIds[%d]' % index, pattern=QUESTION_OPTION_ID_RE)
            if len(set(chosen)) != len(chosen):
                _bad('optionIds has a duplicate')
            if question is not None:
                if not question.get('multi'):
                    _bad('a single-choice question takes optionId, not optionIds', code='wrong_answer_kind')
                known_ids = [o['id'] for o in question['options']]
                if any(option_id not in known_ids for option_id in chosen):
                    _bad('optionIds holds an option that the question does not have', code='unknown_option')
        elif 'text' in operation:
            _check_string(operation['text'], 'text', maximum=MAX_QUESTION_ANSWER_TEXT, strip_empty=True)
        else:
            if operation['confirm'] is not True:
                _bad('confirm must be true')
            if question is not None and question['assumed'] is None:
                _bad('confirm needs a question with an assumed value')
        if 'comment' in operation:
            _check_string(operation['comment'], 'comment', maximum=MAX_QUESTION_COMMENT, strip_empty=True)
        if operation['by'] in RESERVED_LOGINS:
            _bad('an answer comes from an expert, not from "%s"' % operation['by'])
    elif kind == 'comment':
        _check_string(operation['text'], 'text', maximum=MAX_QUESTION_COMMENT, strip_empty=True)
    elif kind == 'recorded':
        _check_question_path(operation['spec'], 'spec')
        _check_int(operation['line'], 'line')


# -- fold ----------------------------------------------------------------------

def _clone(value):
    if isinstance(value, dict):
        return dict((key, _clone(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [_clone(item) for item in value]
    return value


def _question_sort_key(operation):
    return (operation['at'], operation['id'])


def _list_of(value, kind):
    return [item for item in value if isinstance(item, kind)] if isinstance(value, list) else []


def _ask_model(operation):
    addressees = operation.get('addressees')
    origin = operation.get('origin')
    return {
        'id': operation['questionId'], 'opId': operation['id'], 'by': operation.get('by'), 'at': operation['at'],
        'text': operation.get('text') if isinstance(operation.get('text'), str) else '',
        'state': 'open', 'blocking': operation.get('blocking') is True,
        'assumed': operation.get('assumed') if isinstance(operation.get('assumed'), str) else None,
        'addressees': 'all' if addressees == 'all' else [a for a in _list_of(addressees, str)],
        'refs': [_clone(r) for r in _list_of(operation.get('refs'), dict)],
        'options': [_clone(o) for o in _list_of(operation.get('options'), dict)],
        'multi': operation.get('multi') is True,
        'refersTo': operation.get('refersTo') if isinstance(operation.get('refersTo'), str) else None,
        'origin': _clone(origin) if isinstance(origin, dict) else {},
        'answer': None, 'comments': [], 'recorded': None, 'withdrawn': None,
    }


def selected_option_labels(question, option_ids):
    """Labels of the chosen options in the order of the question's options; an unknown id stays as its id."""
    ids = [item for item in option_ids if isinstance(item, str)]
    labels = []
    for option in question.get('options') or []:
        if option.get('id') in ids:
            labels.append(option['label'] if isinstance(option.get('label'), str) else option['id'])
    known = [option.get('id') for option in question.get('options') or []]
    return labels + [item for item in ids if item not in known]


def _answer_text(question, operation):
    """Readable text of an answer-like operation (used for a late answer treated as a comment)."""
    parts = []
    if isinstance(operation.get('text'), str):
        parts.append(operation['text'])
    elif isinstance(operation.get('optionId'), str):
        label = [o.get('label') for o in question['options'] if o.get('id') == operation['optionId']]
        parts.append(label[0] if label and isinstance(label[0], str) else operation['optionId'])
    elif isinstance(operation.get('optionIds'), list):
        parts.append(', '.join(selected_option_labels(question, operation['optionIds'])))
    elif operation.get('confirm') is True:
        parts.append(question['assumed'] or '')
    if isinstance(operation.get('comment'), str):
        parts.append(operation['comment'])
    return '\n'.join(part for part in parts if part)


def fold_questions(operations):
    """State of every question: `{'questions': [model…], 'byId': {id: model}}` in `(at, id)` order.

    The first `answer` (in `(at, id)` order) closes an open question; later `answer`s, and an
    `answer` after `withdraw`, behave like a `comment` (`late: true`).  `withdraw` counts only on an
    open question; `recorded` only on an answered one (the first wins).  Operations of an unknown
    question and malformed entries are skipped, never removed.  Every `ask` is applied before the other
    operations (each group in `(at, id)` order), so an operation timestamped before its `ask` still counts.
    """
    usable = [op for op in operations
              if isinstance(op, dict) and isinstance(op.get('id'), str) and isinstance(op.get('at'), str)
              and isinstance(op.get('questionId'), str) and op.get('type') in QUESTION_TYPES]
    by_id, order, seen = {}, [], set()
    ordered = sorted(usable, key=_question_sort_key)
    # two passes: every `ask` first, so an operation whose `at` precedes its `ask` is not lost
    for operation in ordered:
        if operation['type'] != 'ask' or operation['id'] in seen:
            continue
        seen.add(operation['id'])
        if operation['questionId'] not in by_id:
            by_id[operation['questionId']] = _ask_model(operation)
            order.append(by_id[operation['questionId']])
    for operation in ordered:
        if operation['type'] == 'ask':
            continue
        if operation['id'] in seen:
            continue
        seen.add(operation['id'])
        kind, qid = operation['type'], operation['questionId']
        question = by_id.get(qid)
        if question is None:
            continue
        entry = {'id': operation['id'], 'by': operation.get('by'), 'at': operation['at']}
        if kind == 'comment':
            entry['text'] = operation.get('text') if isinstance(operation.get('text'), str) else ''
            question['comments'].append(entry)
        elif kind == 'answer':
            if question['state'] == 'open':
                question['state'] = 'answered'
                for key in ('optionId', 'text', 'comment'):
                    if isinstance(operation.get(key), str):
                        entry[key] = operation[key]
                if isinstance(operation.get('optionIds'), list):
                    entry['optionIds'] = [item for item in operation['optionIds'] if isinstance(item, str)]
                if operation.get('confirm') is True:
                    entry['confirm'] = True
                question['answer'] = entry
            else:
                entry['text'] = _answer_text(question, operation)
                entry['late'] = True
                question['comments'].append(entry)
        elif kind == 'withdraw':
            if question['state'] == 'open':
                question['state'] = 'withdrawn'
                question['withdrawn'] = entry
        elif kind == 'recorded':
            if question['state'] == 'answered' and question['recorded'] is None:
                entry['spec'] = operation.get('spec')
                entry['line'] = operation.get('line')
                question['recorded'] = entry
    return {'questions': order, 'byId': by_id}


# -- views ---------------------------------------------------------------------

def _viewer_roles(viewer):
    if not isinstance(viewer, dict):
        return ()
    roles = viewer.get('roles')
    if isinstance(roles, str):
        return (roles,)
    if isinstance(roles, (list, tuple)) and roles:
        return tuple(roles)
    role = viewer.get('role')
    return (role,) if isinstance(role, str) else ()


def _is_admin_viewer(viewer):
    return any(role in PRIVILEGED_ROLES for role in _viewer_roles(viewer))


def _is_addressed(question, viewer):
    if question['addressees'] == 'all':
        return True
    return normalize_login(viewer.get('login')) in question['addressees']


def can_view_question(question, viewer):
    """D13: `developer`/`acceptor` see every question; an `expert` sees the ones addressed to them or to
    all, never a withdrawn one; `commenter` (and anybody else) sees none."""
    if _is_admin_viewer(viewer):
        return True
    if 'expert' not in _viewer_roles(viewer):
        return False
    return question['state'] != 'withdrawn' and _is_addressed(question, viewer)


def can_answer(question, viewer):
    """Only an account with the role `expert` seen as an addressee (or the question is for all) may
    answer; `developer`/`acceptor` without `expert` only comment.  The state is checked by the store."""
    return 'expert' in _viewer_roles(viewer) and _is_addressed(question, viewer)


def question_view(question, viewer, by_id=None, slug=None):
    """JSON model of `question` for `viewer` (`{login, roles}`), or None when the viewer must not see it.

    The admin view has `addressees`; the expert view has only `forAll` and no `refersTo` unless the
    referenced question is visible to the viewer (`by_id` is needed to know; without it `refersTo` is dropped)."""
    if not can_view_question(question, viewer):
        return None
    admin = _is_admin_viewer(viewer)
    view = {
        'id': question['id'], 'slug': slug, 'text': question['text'], 'state': question['state'], 'by': question['by'],
        'at': question['at'], 'blocking': question['blocking'], 'assumed': question['assumed'],
        'forAll': question['addressees'] == 'all', 'refs': _clone(question['refs']),
        'options': _clone(question['options']), 'multi': question['multi'], 'origin': _clone(question['origin']),
        'answer': _clone(question['answer']), 'comments': _clone(question['comments']),
        'recorded': _clone(question['recorded']), 'refersTo': None,
        'canAnswer': can_answer(question, viewer) and question['state'] == 'open',
    }
    if not admin:   # the repository path of the spec is not for an expert; `origin.q` stays, `recorded` is only a time
        view['origin'] = {'q': question['origin'].get('q')}
        if question['recorded'] is not None:
            view['recorded'] = {'at': question['recorded']['at']}
    target = question['refersTo']
    if target is not None:
        if admin:
            view['refersTo'] = target
        elif by_id is not None and target in by_id and can_view_question(by_id[target], viewer):
            view['refersTo'] = target
    if admin:
        view['addressees'] = _clone(question['addressees'])
        view['withdrawn'] = _clone(question['withdrawn'])
    return view


def get_question_view(snapshot, question_id, viewer, slug=None):
    """The view of one question, or None (unknown or invisible: the API answers 404 in both cases)."""
    by_id = snapshot.fold['byId']
    question = by_id.get(question_id)
    return None if question is None else question_view(question, viewer, by_id, slug)


def visible_questions(snapshot, viewer, slug=None):
    """Views of the questions `viewer` may see, in `(at, id)` order (`slug`: the run the file belongs to)."""
    fold = snapshot.fold
    views = [question_view(question, viewer, fold['byId'], slug) for question in fold['questions']]
    return [view for view in views if view is not None]


# -- merge (git driver) and export ----------------------------------------------

def _questions_size(operations):
    try:
        return len(serialize_questions(list(operations)).encode('utf-8', 'surrogatepass'))
    except (TypeError, ValueError, OverflowError):
        return 0


def merge_question_operations(target_ops, other_ops, max_bytes=QUESTIONS_MAX_FILE_BYTES):
    """Union by `id` sorted by `(at, id)`; commutative.  Returns `(operations, added)`, `added` = the
    operations of `other_ops` missing in `target_ops`.

    Conflicts (raise `MergeConflict`): the same `id` with different content, an operation that fails
    the schema, an `answer` whose `ask` is not in the result, a result above `max_bytes` that is also larger than both inputs (a file legally
    written one operation over the limit merges with itself).
    """
    by_id, conflicts = {}, []
    for source in (target_ops, other_ops):
        for operation in source:
            if not isinstance(operation, dict) or not isinstance(operation.get('id'), str):
                conflicts.append({'id': None, 'reason': 'operation without an id'})
                continue
            known = by_id.get(operation['id'])
            if known is not None:
                if serialize_question_operation(known) != serialize_question_operation(operation):
                    conflicts.append({'id': operation['id'], 'reason': 'same id, different content'})
                continue
            try:
                validate_question_operation(operation)
            except PrototypeServerError as error:
                conflicts.append({'id': operation['id'], 'reason': 'invalid operation: %s' % error.message})
                continue
            by_id[operation['id']] = operation
    merged = sorted(by_id.values(), key=_question_sort_key)
    asked = set(op['questionId'] for op in merged if op['type'] == 'ask')
    for operation in merged:
        if operation['type'] == 'answer' and operation['questionId'] not in asked:
            conflicts.append({'id': operation['id'], 'reason': 'answer without an ask'})
    size = len(serialize_questions(merged).encode('utf-8', 'surrogatepass'))
    if size > max_bytes and size > max(_questions_size(target_ops), _questions_size(other_ops)):
        conflicts.append({'id': None, 'reason': 'file_too_large: %d bytes (limit %d)' % (size, max_bytes)})
    if conflicts:
        raise MergeConflict(conflicts)
    present = set(op.get('id') for op in target_ops if isinstance(op, dict))
    return merged, [op for op in merged if op['id'] not in present]


def export_questions(snapshot, project, url=None, users=None, slug=None):
    """`expert-questions/1` (read from the file, no server): every question with its answer, comments,
    `recorded` and addressees.  `users` (login to name, or login to record with `name`) gives `name`;
    without it `name` equals `by`."""
    operations = snapshot if isinstance(snapshot, list) else snapshot.operations
    users = users or {}

    def name(login):
        record = users.get(login) if isinstance(login, str) else None
        if isinstance(record, dict):
            record = record.get('name')
        return record if isinstance(record, str) and record else login

    questions = []
    for question in fold_questions(operations)['questions']:
        item = {'id': question['id'], 'slug': slug, 'text': question['text'], 'state': question['state'],
                'blocking': question['blocking'], 'assumed': question['assumed'],
                'addressees': _clone(question['addressees']), 'refs': _clone(question['refs']),
                'options': _clone(question['options'])}
        if question['multi']:
            item['multi'] = True
        answer = question['answer']
        if answer is not None:
            exported = {'by': answer['by'], 'name': name(answer['by']), 'at': answer['at']}
            for key in ('optionId', 'text', 'comment'):
                if key in answer:
                    exported[key] = answer[key]
            if 'optionId' in answer:
                labels = [o.get('label') for o in question['options'] if o.get('id') == answer['optionId']]
                if labels and isinstance(labels[0], str):
                    exported['optionLabel'] = labels[0]
            if 'optionIds' in answer:
                exported['optionIds'] = list(answer['optionIds'])
                exported['optionLabels'] = selected_option_labels(question, answer['optionIds'])
            if answer.get('confirm'):
                exported['confirm'] = True
            item['answer'] = exported
        item['comments'] = [dict([('by', c['by']), ('name', name(c['by'])), ('at', c['at']), ('text', c['text'])]
                                 + ([('late', True)] if c.get('late') else [])) for c in question['comments']]
        if question['refersTo'] is not None:
            item['refersTo'] = question['refersTo']
        item['origin'] = _clone(question['origin'])
        if question['recorded'] is not None:
            recorded = question['recorded']
            item['recorded'] = {'spec': recorded['spec'], 'line': recorded['line'], 'at': recorded['at']}
        if url:
            item['link'] = '%s#%s' % (url, question['id'])
        questions.append(item)
    result = {'schema': QUESTIONS_SCHEMA, 'project': project}
    if url:
        result['url'] = url
    result['questions'] = questions
    return result


# -- the store -------------------------------------------------------------------

class QuestionsSnapshot(object):
    """Immutable view of one `questions.json` state (raw operations in file order)."""

    def __init__(self, operations, size=0, key=None, exists=True):
        self.operations = operations
        self.size = size
        self.key = key
        self.exists = exists
        self._fold = None
        self._lock = threading.Lock()

    @property
    def total(self):
        return len(self.operations)

    def prefix(self, count=None):
        return prefix_fingerprint(self.operations, count)

    @property
    def fold(self):
        with self._lock:
            if self._fold is None:
                self._fold = fold_questions(self.operations)
            return self._fold

    def find_cid(self, by, cid):
        """First operation with `(by, cid)` or None."""
        for operation in self.operations:
            if operation.get('by') == by and operation.get('cid') == cid and isinstance(cid, str):
                return operation
        return None


def check_question_state(fold, operation):
    """Online checks of a new operation against the folded questions (404 / 409), after dedup.

    404 `not_found` (unknown question), 409 `duplicate_id` (an `ask` with a known id),
    409 `already_answered` (`answer`, `withdraw` on an answered question), 409 `withdrawn`
    (`answer` on a withdrawn one), 409 `not_answered` (`recorded` before an answer)."""
    kind, qid = operation['type'], operation['questionId']
    question = fold['byId'].get(qid)
    if kind == 'ask':
        if question is not None:
            raise OperationError(409, 'duplicate_id', 'question id already exists')
        return
    if question is None:
        raise OperationError(404, 'not_found', 'question not found')
    if kind == 'answer':
        if question['state'] == 'answered':
            raise OperationError(409, 'already_answered', 'the question is already answered')
        if question['state'] == 'withdrawn':
            raise OperationError(409, 'withdrawn', 'the question was withdrawn')
    elif kind == 'withdraw':
        if question['state'] == 'answered':
            raise OperationError(409, 'already_answered', 'an answered question cannot be withdrawn')
        if question['state'] == 'withdrawn':
            raise OperationError(409, 'withdrawn', 'the question was already withdrawn')
    elif kind == 'recorded':
        if question['state'] != 'answered':
            raise OperationError(409, 'not_answered', 'the question has no answer to record')


class QuestionsStore(object):
    """`questions.json` of one directory (`<root>/<slug>` of a run; `<root>` for the old project file): cached reads
    and locked appends (pattern of `CommentsStore`).

    `clock` returns seconds since the epoch (default `time.time`).  `root` is created by the first write.
    The lock is `questions.json.lock` (plus a lock per path in the process); a timeout raises `LockTimeout` (503).
    """

    def __init__(self, root_dir, clock=None, lock_timeout=LOCK_TIMEOUT, max_bytes=QUESTIONS_MAX_FILE_BYTES):
        self.directory = os.path.abspath(root_dir)
        self.path = os.path.join(self.directory, QUESTIONS_FILE)
        self.lock_path = self.path + LOCK_SUFFIX
        self.clock = clock or system_clock
        self.lock_timeout = lock_timeout
        self.max_bytes = max_bytes
        self._cache = None
        self._cache_lock = threading.Lock()

    def _read(self):
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return QuestionsSnapshot([], exists=False)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise QuestionsFileError('questions.json must be a regular file')
        key = _stat_key(info)
        with self._cache_lock:
            if self._cache is not None and self._cache.key == key:
                return self._cache
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        fd = os.open(self.path, flags)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            data = handle.read()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise QuestionsFileError('questions.json is not UTF-8')
        snapshot = QuestionsSnapshot(parse_questions_document(text), len(data), _stat_key(info))
        with self._cache_lock:
            self._cache = snapshot
        return snapshot

    def snapshot(self):
        """Current state (an empty snapshot with `exists=False` when the file is missing)."""
        return self._read()

    def sweep_orphan_lock(self):
        """Remove `questions.json.lock` that outlived its `questions.json` (the data file was deleted by hand);
        True when removed.  Best effort: only without the data file, only when the lock is free right now
        (a held lock or a platform that cannot unlink an open file leaves it)."""
        if os.path.lexists(self.path) or not os.path.lexists(self.lock_path):
            return False
        try:
            with FileLock(self.lock_path, 0.2):
                if os.path.lexists(self.path):
                    return False
                os.unlink(self.lock_path)
        except (LockTimeout, OSError):
            return False
        return True

    def lock(self):
        """The file lock; not reentrant.  Creates `root` when it is missing (use `transaction`, which
        creates it only when something is really written)."""
        os.makedirs(self.directory, exist_ok=True)
        return FileLock(self.lock_path, self.lock_timeout)

    def transaction(self, build):
        """Append operations atomically under the lock.

        `build(snapshot)` returns `Existing(operation)` (nothing written), one partial operation or a
        list of them `{type, questionId, by, cid, <fields>}` (no `id`, no `at`); it may raise
        `PrototypeServerError`.  For each partial the store runs the dedup `(by, cid)` (the same
        `questionId` and `type`: the existing operation, idempotent; otherwise 409 `cid_conflict`),
        then the schema (400) and the state checks (404/409), assigns monotonic `at` and `id`, refuses
        with 507 when the file is already above `max_bytes` (an `ask`: above `QUESTIONS_ASK_LIMIT_BYTES`, a
        `comment`: above `QUESTIONS_COMMENT_LIMIT_BYTES`), and
        writes the whole document atomically.
        """
        if not os.path.lexists(self.path):
            # nothing to race on yet: plan without the lock, so a domain error leaves no `root` behind;
            # `build` therefore runs twice when something is to be written (it must be repeatable); a refused write
            # leaves no lock file behind either
            planned = self.transaction_locked(build, dry=True)
            if not planned.operations:
                return planned
        with self.lock():
            return self.transaction_locked(build)

    def transaction_locked(self, build, dry=False):
        snapshot = self._read()
        result = build(snapshot)
        if isinstance(result, Existing):
            return TransactionResult(existing=result.operation, total=snapshot.total,
                                     prefix=snapshot.prefix(), size=snapshot.size)
        partials = [result] if isinstance(result, dict) else list(result or [])
        if not partials:
            return TransactionResult(total=snapshot.total, prefix=snapshot.prefix(), size=snapshot.size)
        now = clock_ms(self.clock)
        working = list(snapshot.operations)
        last_at, last_id = None, None
        for operation in working:
            moment = parse_at(operation.get('at'))
            if moment is not None and (last_at is None or moment > last_at):
                last_at = moment
            op_id = operation.get('id')
            if isinstance(op_id, str) and NEW_OP_ID_RE.fullmatch(op_id) and (last_id is None or op_id > last_id):
                last_id = op_id
        written, existing = [], None
        current_size = snapshot.size if snapshot.exists else None
        for partial in partials:
            if not isinstance(partial, dict):
                _bad('operation must be an object')
            kind = partial.get('type')
            cid = partial.get('cid')
            found = None
            if isinstance(cid, str):
                for operation in working:
                    if operation.get('by') == partial.get('by') and operation.get('cid') == cid:
                        found = operation
                        break
            if found is not None:
                if found.get('questionId') != partial.get('questionId') or found.get('type') != kind:
                    raise OperationError(409, 'cid_conflict', 'cid was already used for another operation')
                existing = existing or found
                continue
            fold = fold_questions(working)
            at_ms = now if last_at is None else max(now, last_at + 1)
            op_id = new_operation_id(at_ms, last_id)
            operation = {'id': op_id, 'type': kind, 'questionId': partial.get('questionId'),
                         'at': format_at(at_ms), 'by': partial.get('by'), 'cid': cid}
            for key, value in partial.items():
                if key not in operation:
                    operation[key] = value
            validate_question_operation(operation, fold['byId'])
            check_question_state(fold, operation)
            if kind == 'recorded' and fold['byId'][operation['questionId']]['recorded'] is not None:
                # idempotent: the first `recorded` stays, the repeated request returns it
                for item in working:
                    if item.get('type') == 'recorded' and item.get('questionId') == operation['questionId']:
                        existing = existing or item
                        break
                continue
            limit = (QUESTIONS_ASK_LIMIT_BYTES if kind == 'ask'
                     else QUESTIONS_COMMENT_LIMIT_BYTES if kind == 'comment' else self.max_bytes)
            if current_size is None:
                current_size = len(serialize_questions(working).encode('utf-8', 'surrogatepass'))
            if current_size > min(limit, self.max_bytes):
                raise QuestionsFileTooLarge(current_size)
            current_size = None
            written.append(operation)
            working.append(operation)
            last_at, last_id = at_ms, op_id
        if not written:
            return TransactionResult(existing=existing, total=snapshot.total, prefix=snapshot.prefix(),
                                     size=snapshot.size)
        if dry:
            return TransactionResult(operations=written, total=snapshot.total, prefix=snapshot.prefix(),
                                     size=snapshot.size)
        created = not snapshot.exists
        try:
            data = serialize_questions(working).encode('utf-8')
        except OverflowError:
            raise OperationError(400, 'invalid_request', 'a number in the operation is out of range')
        atomic_write_bytes(self.path, data)
        info = os.lstat(self.path)
        fresh = QuestionsSnapshot(working, len(data), _stat_key(info))
        with self._cache_lock:
            self._cache = fresh
        return TransactionResult(operations=written, total=fresh.total, prefix=fresh.prefix(),
                                 created=created, size=len(data))

    def append(self, partials):
        """Append ready partial operations (CLI use): `transaction(lambda snapshot: partials)`."""
        return self.transaction(lambda snapshot: list(partials))


# ── Browser operations: role and reference checks ────────────────────────────

def check_role(kind, login, role, thread, payload):
    """Role matrix for the browser (step "rola" of the POST order); 403 `forbidden`.

    Ownership checks need the target; a missing target is reported later by
    `check_references` (404/400).
    """
    def deny(message):
        raise OperationError(403, 'forbidden', message)
    if kind == 'delete' and role != 'developer':
        deny('only a developer may delete a thread')
    if kind in ('accept', 'unaccept') and role not in PRIVILEGED_ROLES:
        deny('only an acceptor or a developer may accept a screen')
    if kind in ('edit', 'retract') and isinstance(payload, dict):
        message = _find_message(thread, payload.get('messageId'))
        if message is not None and message['by'] != login:
            deny('only the author may change a message')
    if kind in ('resolve', 'reopen', 'reanchor') and thread is not None:
        if thread['by'] != login and role != 'developer':
            deny('only the thread author or a developer may do this')


def check_references(snapshot, kind, thread_id, payload):
    """"Spójność odwołań" for thread operations (server and CLI).

    409 `duplicate_id` (create with an existing thread or message id, reply with an
    existing message id), 404 `thread_not_found` (missing, deleted or hidden thread),
    400 `message_not_in_thread`, 409 `message_retracted`, 400 `invalid_iteration`
    (anchor iteration above the current one).
    """
    fold = snapshot.fold
    index = snapshot.index
    if kind == 'create':
        if thread_id in index['threadIds']:
            raise OperationError(409, 'duplicate_id', 'thread id already exists')
        if payload['message']['id'] in index['messageIds']:
            raise OperationError(409, 'duplicate_id', 'message id already exists')
    elif kind in THREAD_TYPES:
        thread = fold['byId'].get(thread_id)
        if thread is None:
            raise OperationError(404, 'thread_not_found', 'thread does not exist or was removed')
        if kind == 'reply' and payload['message']['id'] in index['messageIds']:
            raise OperationError(409, 'duplicate_id', 'message id already exists')
        if kind in ('edit', 'vote', 'retract'):
            message = _find_message(thread, payload.get('messageId'))
            if message is None:
                raise OperationError(400, 'message_not_in_thread', 'message is not in this thread')
            if message['retracted']:
                raise OperationError(409, 'message_retracted', 'message was retracted')
    if kind in ('create', 'reanchor'):
        anchor = payload.get('anchor')
        if isinstance(anchor, dict) and _is_int(anchor.get('iteration')) and anchor['iteration'] > fold['currentIteration']:
            raise OperationError(400, 'invalid_iteration', 'anchor iteration is above the current iteration')


def _check_acceptance_body(body, kind):
    """Shape of an `accept`/`unaccept` request body: no `threadId`, no unknown fields (400)."""
    unknown = [key for key in body if key not in ('cid', 'type', 'payload')]
    if unknown:
        raise OperationError(400, 'invalid_request', 'unknown fields: %s' % ', '.join(sorted(map(str, unknown))))
    if not isinstance(body.get('payload'), dict):
        raise OperationError(400, 'invalid_request', 'payload must be an object')


def _prepare_accept(snapshot, login, body, current, writing=None):
    """Browser `accept`: payload `{scope: screen, screens: {sN: hash}, version}` (400 `field_not_allowed`
    for `via`/`who`/`iteration`, 400 `invalid_scope`, 400 `invalid_request` for other than one screen,
    409 `stale_view` for another `version` or hash, 400 `unknown_screen`).  The stored payload adds
    `iteration` (current) and `via: ui`; `version` is not stored.  `writing()` (writing marker) raises 409
    `writing_in_progress` for a valid marker: after the shape checks (and the `cid` dedup of the caller), before
    the `version`/hash comparison."""
    payload = body['payload']
    forbidden = [key for key in ('via', 'who', 'iteration') if key in payload]
    if forbidden:
        raise OperationError(400, 'field_not_allowed', 'the server sets: %s' % ', '.join(forbidden))
    if 'scope' in payload and payload['scope'] != 'screen':
        raise OperationError(400, 'invalid_scope', 'the browser accepts only scope screen '
                             '(all: CLI, project: POST /_api/v1/project/accept)')
    unknown = [key for key in payload if key not in ('scope', 'screens', 'version')]
    if unknown:
        raise OperationError(400, 'invalid_request', 'payload has unknown fields: %s'
                             % ', '.join(sorted(map(str, unknown))))
    for key in ('scope', 'screens', 'version'):
        if key not in payload:
            _bad('payload.%s is required' % key, code='invalid_request')
    screens = payload['screens']
    if not isinstance(screens, dict) or len(screens) != 1:
        _bad('payload.screens must be an object with exactly one screen', code='invalid_request')
    (screen, digest), = screens.items()
    _check_screen(screen, 'payload.screens key')
    _check_hash(digest, 'payload.screens.' + screen)
    _check_hash(payload['version'], 'payload.version')
    if writing is not None:
        writing()
    hashes = current()
    if payload['version'] != hashes['version']:
        raise OperationError(409, 'stale_view', 'the prototype changed since the page was loaded')
    if screen not in hashes['screens']:
        raise OperationError(400, 'unknown_screen', unknown_screen_message([screen], hashes['ambiguous']))
    if digest != hashes['screens'][screen]:
        raise OperationError(409, 'stale_view', 'screen %s changed since the page was loaded' % screen)
    return {'type': 'accept', 'by': login, 'cid': body['cid'],
            'payload': {'scope': 'screen', 'screens': {screen: digest},
                        'iteration': snapshot.fold['currentIteration'], 'via': 'ui'}}


def _prepare_unaccept(snapshot, login, role, body, current):
    """Browser `unaccept`: payload `{screens: [sN]}`; 400 `unknown_screen`, 409 `not_accepted` (no
    current entry in the fold), 403 `forbidden` for an acceptor undoing somebody else's acceptance."""
    payload = body['payload']
    validate_payload('unaccept', payload, origin='browser')
    screens = list(dict.fromkeys(payload['screens']))
    hashes = current()
    for screen in screens:
        if screen not in hashes['screens']:
            raise OperationError(400, 'unknown_screen', unknown_screen_message([screen], hashes['ambiguous']))
    acceptance = snapshot.fold['acceptance']
    missing = [screen for screen in screens if screen not in acceptance]
    if missing:
        raise OperationError(409, 'not_accepted', 'no acceptance of: %s' % ', '.join(missing),
                             {'screens': missing})
    if role != 'developer' and any(acceptance[screen]['who'] != login for screen in screens):
        raise OperationError(403, 'forbidden', 'an acceptor may undo only their own acceptance')
    return {'type': 'unaccept', 'by': login, 'cid': body['cid'], 'payload': {'screens': screens}}


def prepare_browser_operation(snapshot, login, role, body, current=None, writing=None):
    """POST order from "dedup" on: dedup (`by`, `cid`) → type → role → schema → references.

    Returns `Existing(operation)` for a repeated `cid` or a partial operation for
    `CommentsStore.transaction`.  Raises `OperationError`.  `current()` returns the current
    `{screens, version}` (`screen_hashes`); it is needed for `accept`/`unaccept` and is called under
    the prototype lock.  `writing()` raises 409 `writing_in_progress` (only `accept`).
    """
    if not isinstance(body, dict):
        raise OperationError(400, 'invalid_request', 'body must be a JSON object')
    cid = body.get('cid')
    if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
        raise OperationError(400, 'cid_required', 'cid is required')
    existing = snapshot.find_cid(login, cid)
    if existing is not None:
        return Existing(existing)
    kind = body.get('type')
    if not isinstance(kind, str) or kind not in OPERATION_TYPES:
        raise OperationError(400, 'invalid_type', 'unknown operation type')
    if kind not in BROWSER_TYPES:
        raise OperationError(403, 'cli_only', 'this operation is accepted only from the CLI')
    thread_id = body.get('threadId')
    payload = body.get('payload')
    thread = snapshot.fold['byId'].get(thread_id) if isinstance(thread_id, str) else None
    check_role(kind, login, role, thread, payload)
    if 'by' in body:
        raise OperationError(400, 'by_not_allowed', 'the author comes from the session')
    if kind in ('accept', 'unaccept'):
        _check_acceptance_body(body, kind)
        if current is None:
            raise OperationError(500, 'internal_error', 'hashes of the prototype are not available')
        if kind == 'accept':
            return _prepare_accept(snapshot, login, body, current, writing)
        return _prepare_unaccept(snapshot, login, role, body, current)
    unknown = [key for key in body if key not in ('cid', 'type', 'threadId', 'payload')]
    if unknown:
        raise OperationError(400, 'invalid_request', 'unknown fields: %s' % ', '.join(sorted(map(str, unknown))))
    _check_string(thread_id, 'threadId', pattern=THREAD_ID_RE if kind == 'create' else LEGACY_THREAD_ID_RE)
    validate_payload(kind, payload, origin='browser')
    check_references(snapshot, kind, thread_id, payload)
    return {'type': kind, 'threadId': thread_id, 'by': login, 'cid': cid, 'payload': payload}


# ── Iterations and restore (F2) ──────────────────────────────────────────────

ITERATIONS_DIR = 'iterations'
_ITERATION_NAME = re.compile(r'[1-9][0-9]{0,8}')
# Top-level entries a freeze never copies (dot files, `*.lock`, `*.tmp` and staging names are skipped everywhere).
_FREEZE_EXCLUDED_TOP = frozenset((COMMENTS_FILE, LEGACY_COMMENTS_FILE, LEGACY_COMMENTS_KEPT, ITERATIONS_DIR, 'questions.json'))
_FROZEN_ATTRS = re.compile(
    r'\s+data-prototype-(?:iteration|frozen)(?![\w-])(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s"\'=<>`]+))?',
    re.IGNORECASE)
RESTORE_ERRORS = ('iteration_not_found', 'screen_missing', 'ambiguous_section', 'ids_check_failed',
                  'incomplete_restore', 'source_writes_disabled', 'stale_view')


class IdsCheckFailed(PrototypeServerError):
    """`ids --check` failed: on the current `index.html` (iterate, exit 1) or on a restore result (409)."""

    def __init__(self, info, message='data-proto-id check failed'):
        PrototypeServerError.__init__(self, 409, 'ids_check_failed', message, {
            'missing': info.get('missing', []), 'duplicates': info.get('duplicates', []),
            'invalid': info.get('invalid', [])})
        self.info = info


def iteration_hash(hashes):
    """`hash` of an `iteration` operation: SHA-256 of `assets_hash` + `head_hash` + `version`
    (the hash of all screen hashes) of the frozen copy; equal for a copy and its source."""
    return _sha256_hex(hashes['assets'] + hashes['head'] + hashes['version'])


def iteration_numbers(operations):
    """Sorted distinct `payload.number` of valid `iteration` operations (as the fold counts them)."""
    numbers = set()
    for operation in normalize_operations(operations):
        if operation['type'] != 'iteration':
            continue
        number = _payload(operation).get('number')
        if _is_integral(number) and number >= 1:
            numbers.add(int(number))
    return sorted(numbers)


def iteration_directory(directory, number):
    """`<directory>/iterations/<number>` when it is a real directory inside the prototype
    (no symlink, `realpath` inside the prototype), else None.  The path is built only from
    the number, never from client text."""
    if not _is_integral(number) or number < 1:
        return None
    base = os.path.realpath(directory)
    parent = os.path.join(directory, ITERATIONS_DIR)
    path = os.path.join(parent, str(int(number)))
    for candidate in (parent, path):
        try:
            info = os.lstat(candidate)
        except OSError:
            return None
        if not stat.S_ISDIR(info.st_mode):
            return None
    real = os.path.realpath(path)
    if not _within(real, base) or real == base:
        return None
    return path


def iterations_listing(directory, operations):
    """`[{number, path, exists}]` of `GET …/operations`: iterations with an `iteration` operation;
    `exists` = `iterations/<n>/index.html` is a regular file (an operation without its directory,
    e.g. after `merge-comments`, is listed with `exists: false`)."""
    result = []
    for number in iteration_numbers(operations):
        path = iteration_directory(directory, number)
        exists = path is not None and os.path.isfile(os.path.join(path, 'index.html')) \
            and not os.path.islink(os.path.join(path, 'index.html'))
        result.append({'number': number, 'path': '%s/%d/' % (ITERATIONS_DIR, number), 'exists': exists})
    return result


def read_raw_index(directory):
    """`index.html` as text without newline translation (undecodable bytes kept via surrogateescape);
    a symbolic link or non-file is refused (`OSError`)."""
    path = os.path.join(directory, 'index.html')
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OSError('index.html must be a regular file')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        return handle.read().decode('utf-8', 'surrogateescape')


def _encode_html(text):
    return text.encode('utf-8', 'surrogateescape')


class _HtmlTagParser(_SourceParser):
    def __init__(self, source):
        _SourceParser.__init__(self, source)
        self.found = None

    def handle_starttag(self, tag, attrs):
        if tag == 'html' and self.found is None:
            self.found = (self.source_offset(), self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def mark_frozen_html(text, number):
    """`(text, marked)`: the first `<html>` start tag gets `data-prototype-iteration="N"
    data-prototype-frozen` (earlier values of these attributes are replaced); nothing else changes."""
    parser = _HtmlTagParser(text).run()
    if parser.found is None or not parser.found[1]:
        return text, False
    start, tag = parser.found
    if text[start:start + len(tag)] != tag:
        return text, False
    cleaned = _FROZEN_ATTRS.sub('', tag)
    end = len(cleaned) - (2 if cleaned.endswith('/>') else 1)
    marked = cleaned[:end].rstrip() + ' data-prototype-iteration="%d" data-prototype-frozen' % number + cleaned[end:]
    return text[:start] + marked + text[start + len(tag):], True


def _freeze_skip(name, top):
    return _excluded_segment(name) or (top and name in _FREEZE_EXCLUDED_TOP)


def _copy_tree(source, target):
    """Byte copy of the prototype into a new directory `target` (no symlinks, no special files,
    without `comments.json`, `comments.js`, `comments.v1.js`, `iterations/`, dot files, locks, temporaries)."""
    os.mkdir(target)
    for current, dirs, files in os.walk(source, followlinks=False):
        relative = os.path.relpath(current, source)
        top = relative == '.'
        destination = target if top else os.path.join(target, relative)
        kept = []
        for name in sorted(dirs):
            path = os.path.join(current, name)
            if _freeze_skip(name, top) or os.path.islink(path):
                continue
            os.mkdir(os.path.join(destination, name))
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = os.path.join(current, name)
            if _freeze_skip(name, top) or os.path.islink(path) or not os.path.isfile(path):
                continue
            copy = os.path.join(destination, name)
            shutil.copyfile(path, copy, follow_symlinks=False)
            shutil.copymode(path, copy, follow_symlinks=False)


def _fsync_directory(path):
    if os.name == 'nt':
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def freeze_iteration(directory, number):
    """Step (2): copy the current version to `iterations/<n>.tmp-<hex>/`, mark its `index.html`
    frozen and rename it to `iterations/<n>/` (atomic).  Call with the prototype lock held.

    Returns `(path, hash, warnings)`; 409 `iteration_exists` when `iterations/<n>/` exists.
    """
    base = os.path.abspath(directory)
    parent = os.path.join(base, ITERATIONS_DIR)
    if os.path.lexists(parent):
        if os.path.islink(parent) or not os.path.isdir(parent):
            raise PrototypeServerError(409, 'invalid_iterations', 'iterations/ must be a directory')
    else:
        os.mkdir(parent)
    final = os.path.join(parent, str(number))
    if os.path.lexists(final):
        raise PrototypeServerError(409, 'iteration_exists', 'iterations/%d/ already exists' % number)
    staging = os.path.join(parent, '%d.tmp-%s' % (number, secrets.token_hex(6)))
    warnings = []
    try:
        _copy_tree(base, staging)
        index = os.path.join(staging, 'index.html')
        if os.path.isfile(index):
            text = read_raw_index(staging)
            marked, ok = mark_frozen_html(text, number)
            if ok:
                atomic_write_bytes(index, _encode_html(marked))
            else:
                warnings.append('index.html has no <html> start tag: data-prototype-frozen was not added')
        else:
            warnings.append('the prototype has no index.html')
        digest = iteration_hash(screen_hashes(staging)) if os.path.isfile(index) else _sha256_hex('')
        _fsync_directory(staging)
        os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _fsync_directory(parent)
    return final, digest, warnings


def current_iteration_hash(directory, cache=None):
    try:
        hashes = cache.prototype_hashes(directory) if cache is not None else screen_hashes(directory)
    except OSError:
        return _sha256_hex('')
    return iteration_hash(hashes)


def _screen_records(spans, screen):
    record = spans['screens'].get(screen)
    if record is None:
        return None, False
    ambiguous = any(problem['screen'] == screen for problem in spans['problems']) or record['end'] is None
    if not ambiguous:
        # A screen nested in another screen (or holding one) has no single replaceable section.
        for other in spans['sections']:
            if other is record or not other['screen']:
                continue
            holds = record['start'] < other['start'] < record['end']
            nested = other['start'] < record['start'] and (other['end'] is None or other['end'] >= record['end'])
            if holds or nested:
                ambiguous = True
                break
    return record, ambiguous


def plan_restore(directory, fold, screen, source_number):
    """The shared core of `restore`, `restore --dry-run` and `GET …/restore-preview`.

    Builds the new `index.html` (the current one with section `screen` replaced by the section of
    `iterations/<source_number>/index.html`) and its effects, without writing.  Raises
    404 `iteration_not_found`, 409 `screen_missing`, `ambiguous_section`, `ids_check_failed`.
    Returns `{html, screen, from, hashBefore, hashAfter, threadsBack, threadsLost, acceptanceLost,
    unchanged}` (`unchanged` = the section of the iteration hashes like the current one, D27);
    the thread counts cover visible open/awaiting/decided threads of the screen anchored to an
    element (`threadsBack`: handle absent now, present after; `threadsLost`: the reverse);
    `acceptanceLost` is 1 when the screen has an acceptance of its current hash and the restore changes it.
    """
    source_dir = iteration_directory(directory, source_number)
    if source_dir is None or not os.path.isfile(os.path.join(source_dir, 'index.html')):
        raise PrototypeServerError(404, 'iteration_not_found', 'iterations/%s/ does not exist' % source_number)
    try:
        current = read_raw_index(directory)
        source = read_raw_index(source_dir)
    except OSError as error:
        raise PrototypeServerError(409, 'screen_missing', 'index.html cannot be read (%s)' % error)
    current_spans, source_spans = section_spans(current), section_spans(source)
    current_record, current_ambiguous = _screen_records(current_spans, screen)
    source_record, source_ambiguous = _screen_records(source_spans, screen)
    if current_record is None or source_record is None:
        where = 'the current version' if current_record is None else 'iteration %s' % source_number
        raise PrototypeServerError(409, 'screen_missing', 'screen %s is not in %s' % (screen, where))
    if current_ambiguous or source_ambiguous:
        raise PrototypeServerError(409, 'ambiguous_section',
                                   'the section of screen %s is ambiguous for the parser' % screen)
    section = source[source_record['start']:source_record['end']]
    html = current[:current_record['start']] + section + current[current_record['end']:]
    after_ids = collect_proto_ids(html)
    if not after_ids['ok']:
        raise IdsCheckFailed(after_ids, 'the restored index.html fails ids --check')
    assets = assets_hash(directory)
    before_text, after_text = normalize_newlines(current), normalize_newlines(html)
    before_hash = screen_hash(assets, head_hash(before_text),
                              normalize_newlines(current[current_record['start']:current_record['end']]))
    after_hash = screen_hash(assets, head_hash(after_text), normalize_newlines(section))
    now_ids = set(collect_proto_ids(current)['byScreen'].get(screen, ()))
    new_ids = set(after_ids['byScreen'].get(screen, ()))
    back = lost = 0
    for thread in fold['threads']:
        if thread['screen'] != screen or thread['status'] not in OPEN_STATUSES:
            continue
        if anchor_kind(thread.get('anchor')) != 'element':
            continue
        proto_id = thread['anchor']['protoId']
        if proto_id not in now_ids and proto_id in new_ids:
            back += 1
        elif proto_id in now_ids and proto_id not in new_ids:
            lost += 1
    accepted = fold.get('acceptance', {}).get(screen)
    unchanged = before_hash == after_hash
    return {'html': html, 'screen': screen, 'from': int(source_number), 'hashBefore': before_hash,
            'hashAfter': after_hash, 'threadsBack': back, 'threadsLost': lost,
            'acceptanceLost': 1 if accepted is not None and accepted['hash'] == before_hash and not unchanged else 0,
            'unchanged': unchanged, 'current': current}


def current_screen_hash(directory, screen):
    """Hash of `screen` in the current files, or None (missing/ambiguous screen, unreadable file)."""
    try:
        html = normalize_newlines(read_raw_index(directory))
    except OSError:
        return None
    spans = section_spans(html)
    record, ambiguous = _screen_records(spans, screen)
    if record is None or ambiguous:
        return None
    head = html[spans['head'][0]:spans['head'][1]] if spans['head'] else ''
    return screen_hash(assets_hash(directory), _sha256_hex(head), html[record['start']:record['end']])


# F06: durable marker of an unfinished restore.  Written atomically (temporary file, fsync, rename) after the
# commit of K40 and before the freeze (step 2, the first step recovery may have to finish), removed after the
# atomic write of `index.html` (step 4).  It names the restore by `frozen` (the iteration number C, unique in
# the log) plus `screen`, `from`, `hashBefore`, `hashAfter`: the operation id does not exist before step 3.  Automatic recovery repeats step 4
# only while this marker names the last `restore` of the journal; without it a screen equal to `hashBefore`
# is a decision of the user (`git checkout`, undo in an editor) and is never overwritten.  A dot file ending
# in `.tmp`: never served, hashed or frozen, and ignored by the prototype `.gitignore` (`*.tmp`).
RESTORE_MARKER = '.restore-pending.tmp'
_MARKER_KEYS = ('screen', 'from', 'frozen', 'hashBefore', 'hashAfter')


def read_restore_marker(directory):
    """`{screen, from, frozen, hashBefore, hashAfter}` of the marker, `{}` for an unreadable one, None without it."""
    path = os.path.join(directory, RESTORE_MARKER)
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        return {}
    try:
        with open(path, 'rb') as handle:
            value = strict_json_loads(handle.read().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def write_restore_marker(directory, payload):
    data = js_stringify(dict((key, payload[key]) for key in _MARKER_KEYS)) + '\n'
    atomic_write_bytes(os.path.join(directory, RESTORE_MARKER), data.encode('utf-8'))


def remove_restore_marker(directory):
    path = os.path.join(directory, RESTORE_MARKER)
    try:
        if os.path.islink(path) or os.path.isdir(path):
            return
        os.unlink(path)
    except FileNotFoundError:
        return
    _fsync_directory(directory)


# writing marker: the "agent is still writing screens" marker, `<prototype>/.writing.json`.  A dot file: never
# served, hashed or frozen (`_excluded_segment`).  Times are UTC milliseconds; the clock is always injected
# (`now_ms`), a read never writes.  Anything that is not a valid marker counts as no marker (fail-open), so a
# forgotten, damaged or forged file can never block acceptance for good.
WRITING_MARKER = '.writing.json'
WRITING_MAX_BYTES = 4096
WRITING_MAX_WINDOW_MS = 3600000
WRITING_CLOCK_SKEW_MS = 60000
WRITING_TTL_MIN = 60
WRITING_TTL_MAX = 3600
WRITING_TTL_DEFAULT = 900
WRITING_BY_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}')


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def read_writing(directory, now_ms):
    """`{since, expiresAt, by}` of a valid writing marker, else None (see "Ważny marker" in the spec).

    `lstat` before the open (no `O_NOFOLLOW` on Windows), then `O_NOFOLLOW|O_NONBLOCK` + `fstat` + `S_ISREG`
    (a FIFO must not hang a server thread), at most 4 KiB, strict JSON, `version == 1`, integer `since` and
    `expiresAt` (not bool), `since <= now + 60 s`, `now < expiresAt`, `0 < expiresAt - since <= 3600 s`.
    """
    path = os.path.join(directory, WRITING_MARKER)
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > WRITING_MAX_BYTES:
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                     | getattr(os, 'O_BINARY', 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > WRITING_MAX_BYTES:
                return None
            data = b''
            while len(data) <= WRITING_MAX_BYTES:
                chunk = os.read(fd, WRITING_MAX_BYTES + 1 - len(data))
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
        if len(data) > WRITING_MAX_BYTES:
            return None
        value = strict_json_loads(data.decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(value, dict) or not _is_int(value.get('version')) or value['version'] != 1:
        return None
    since, expires = value.get('since'), value.get('expiresAt')
    if isinstance(now_ms, float):
        now_ms = int(now_ms)
    if not _is_int(since) or not _is_int(expires) or not _is_int(now_ms):
        return None
    if since > now_ms + WRITING_CLOCK_SKEW_MS or now_ms >= expires:
        return None
    if not 0 < expires - since <= WRITING_MAX_WINDOW_MS:
        return None
    by = value.get('by')
    return {'since': since, 'expiresAt': expires, 'by': by if isinstance(by, str) and WRITING_BY_RE.fullmatch(by) else 'agent'}


def write_writing(directory, now_ms, ttl_s, by='agent'):
    """Atomically (re)write the marker: `since = now`, `expiresAt = now + ttl`; a short retry on Windows where
    `os.replace` can fail while a reader holds the file.  Returns `{since, expiresAt, by}`."""
    payload = {'version': 1, 'since': int(now_ms), 'expiresAt': int(now_ms) + int(ttl_s) * 1000, 'by': by}
    data = (js_stringify(payload) + '\n').encode('utf-8')
    path = os.path.join(directory, WRITING_MARKER)
    attempts = 5 if os.name == 'nt' else 1
    for attempt in range(attempts):
        try:
            atomic_write_bytes(path, data)
            break
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (attempt + 1))
    return {'since': payload['since'], 'expiresAt': payload['expiresAt'], 'by': by}


def remove_writing(directory):
    """Idempotent removal of the marker (a symlink or a directory in its place is left alone)."""
    path = os.path.join(directory, WRITING_MARKER)
    try:
        if os.path.islink(path) or os.path.isdir(path):
            return False
        os.unlink(path)
    except FileNotFoundError:
        return False
    _fsync_directory(directory)
    return True


def public_writing(directory, now_ms):
    """The `writing` field of the API, `prototype list` and the live gallery: `None` or `{since, expiresAt}`
    (never `by`).  The one helper of that shape."""
    marker = read_writing(directory, now_ms)
    return None if marker is None else {'since': marker['since'], 'expiresAt': marker['expiresAt']}


def writing_conflict(slug, marker):
    """409 `writing_in_progress` `{slug, since, expiresAt}` for a valid `marker` (no `by`)."""
    return OperationError(409, 'writing_in_progress', 'the agent is still writing screens of this prototype',
                          {'slug': slug, 'since': marker['since'], 'expiresAt': marker['expiresAt']})


def writing_entries(items, now_ms):
    """`[{slug, since, expiresAt}]` of the `(slug, directory)` pairs with a valid marker, in the given order."""
    result = []
    for slug, directory in items:
        marker = read_writing(directory, now_ms)
        if marker is not None:
            result.append({'slug': slug, 'since': marker['since'], 'expiresAt': marker['expiresAt']})
    return result


def _marker_matches(marker, operation):
    if not marker or operation is None:
        return False
    payload = _payload(operation)
    return all(marker.get(key) == payload.get(key) for key in _MARKER_KEYS)


def recovery_notes(report):
    """Human-readable list of what a recovery wrote (F06: never silent)."""
    notes = []
    if report is None:
        return notes
    for path in report.get('removed', []):
        notes.append('recovery removed the unfinished freeze %s' % path)
    for operation in report.get('appended', []):
        notes.append('recovery registered the interrupted freeze iterations/%s/' % operation['payload']['number'])
    if report.get('redone') is not None:
        payload = _payload(report['redone'])
        notes.append('recovery finished the interrupted restore of %s from iteration %s (index.html rewritten)'
                     % (payload.get('screen'), payload.get('from')))
    return notes


def _last_restore(operations, last_only):
    """The `restore` operation to finish: the last operation of the file (`last_only`) or the last
    `restore` in file order (`--resume`)."""
    for operation in reversed(operations):
        if not isinstance(operation, dict):
            if last_only:
                return None
            continue
        if operation.get('type') == 'restore' and is_operation(operation):
            return operation
        if last_only:
            return None
    return None


def _unfinishable_hint(resume):
    if resume:
        return ''
    return ('; if that restore is no longer wanted, delete the marker %s in the prototype directory '
            '(the screen stays as it is now)' % RESTORE_MARKER)


def _unfinishable_details(resume):
    return {} if resume else {'marker': RESTORE_MARKER}


def recovery_actions(store, directory, resume=False, snapshot=None):
    """What recovery would do now (read only; the result of "Przywrócenie: kolejność i odtwarzanie").

    Returns `{staging: [names], append: [{number, hash}], redo: plan|None, restore: op|None,
    state, marker}`; automatic recovery looks only at the last `restore` named by the marker
    `RESTORE_MARKER` (F06), `resume` at the last `restore` of the log with or without it;
    `marker` = the marker file exists (it is removed by `recover_prototype`).
    `state` of the restore to finish is `redo` (screen hash = `hashBefore`), `complete`
    (= `hashAfter` or the restore changed nothing), `changed` (differs from both) or `none`.
    Raises 409 `incomplete_restore` for a directory `iterations/<n>/` without an `iteration`
    operation whose content differs from the current version (or whose number is not the current
    iteration), and when the restore to redo cannot be rebuilt.
    """
    snapshot = snapshot or store.snapshot()
    marker = read_restore_marker(directory)
    result = {'staging': [], 'append': [], 'redo': None, 'restore': None, 'state': 'none',
              'marker': marker is not None, 'staleMarker': None}
    if marker is not None and not _marker_matches(marker, _last_restore(snapshot.operations, last_only=False)):
        result['staleMarker'] = marker
    parent = os.path.join(directory, ITERATIONS_DIR)
    names = []
    if os.path.isdir(parent) and not os.path.islink(parent):
        try:
            names = sorted(os.listdir(parent))
        except OSError:
            names = []
    for name in names:
        if '.tmp-' in name and os.path.isdir(os.path.join(parent, name)) \
                and not os.path.islink(os.path.join(parent, name)):
            result['staging'].append(name)
    known = set(iteration_numbers(snapshot.operations))
    orphans = sorted(int(name) for name in names
                     if _ITERATION_NAME.fullmatch(name) and int(name) not in known
                     and iteration_directory(directory, int(name)) is not None)
    if orphans:
        current_number = snapshot.fold['currentIteration']
        current_hash = current_iteration_hash(directory)
        for number in orphans:
            path = iteration_directory(directory, number)
            frozen_hash = current_iteration_hash(path)
            if number == current_number and frozen_hash == current_hash:
                result['append'].append({'number': number, 'hash': current_hash})
                current_number += 1
                continue
            relative = '%s/%d/' % (ITERATIONS_DIR, number)
            raise PrototypeServerError(
                409, 'incomplete_restore',
                '%s has no iteration operation and differs from the current version.  If it came from git '
                '(see `git status -- %s` and `git log -- %s`), merge the comments.json of that branch with '
                '`skillsync prototype merge-comments`; if it is an unsaved freeze of an interrupted iterate/restore, '
                'remove it (nothing in it is missing from the current version or from git) and try again'
                % (relative, relative, relative), {'path': relative})
        return result
    operation = _last_restore(snapshot.operations, last_only=False)
    if operation is None:
        return result
    if not resume and not _marker_matches(marker, operation):
        # F06: no marker of this restore = step 4 was done; a screen equal to `hashBefore` is the user's choice.
        return result
    result['restore'] = operation
    payload = _payload(operation)
    screen, before, after = payload.get('screen'), payload.get('hashBefore'), payload.get('hashAfter')
    now = current_screen_hash(directory, screen) if isinstance(screen, str) else None
    if now is None or now != before or before == after:
        result['state'] = 'complete' if now is not None and (now == after or before == after) else 'changed'
        return result
    try:
        plan = plan_restore(directory, snapshot.fold, screen, payload.get('from'))
    except PrototypeServerError as error:
        raise PrototypeServerError(409, 'incomplete_restore',
                                   'the last restore of %s cannot be finished: %s%s'
                                   % (screen, error.message, _unfinishable_hint(resume)), _unfinishable_details(resume))
    if plan['hashAfter'] != after:
        raise PrototypeServerError(409, 'incomplete_restore',
                                   'the last restore of %s cannot be finished: iterations/%s/ changed since%s'
                                   % (screen, payload.get('from'), _unfinishable_hint(resume)), _unfinishable_details(resume))
    result['redo'] = plan
    result['state'] = 'redo'
    return result


def recover_prototype(store, directory, by, allow_source=True, resume=False):
    """Recovery of an interrupted iterate/restore; call with `store.lock()` held.

    Removes staging directories `iterations/*.tmp-*`, appends the missing `iteration` operation
    for a directory equal to the current version (`by`: `agent` from the CLI, `server` from
    `serve`) and repeats step (4) of the last `restore` named by the marker of an unfinished
    restore (any last `restore` with `resume`) while the screen hash still equals `hashBefore`.  `allow_source=False`
    (`--no-source-writes`) skips source writes (staging removal and step 4) with a warning.
    Returns `{removed, appended, redone, state, warnings}`; raises 409 `incomplete_restore`.
    """
    report = {'removed': [], 'appended': [], 'redone': None, 'state': 'none', 'warnings': [], 'staleMarker': None}
    actions = recovery_actions(store, directory, resume=resume)
    parent = os.path.join(directory, ITERATIONS_DIR)
    for name in actions['staging']:
        if not allow_source:
            report['warnings'].append('%s/%s: unfinished freeze left (source writes are disabled)' % (ITERATIONS_DIR, name))
            continue
        shutil.rmtree(os.path.join(parent, name), ignore_errors=True)
        report['removed'].append('%s/%s/' % (ITERATIONS_DIR, name))
    if actions['append']:
        partials = [{'type': 'iteration', 'by': by,
                     'payload': {'number': item['number'], 'path': '%s/%d/' % (ITERATIONS_DIR, item['number']),
                                 'hash': item['hash']}} for item in actions['append']]
        report['appended'] = store.append_locked(partials).operations
    report['state'] = actions['state']
    if actions['redo'] is not None:
        if allow_source:
            atomic_write_bytes(os.path.join(directory, 'index.html'), _encode_html(actions['redo']['html']))
            report['redone'] = actions['restore']
            remove_restore_marker(directory)
        else:
            report['warnings'].append('the last restore of %s is not finished (source writes are disabled)'
                                      % _payload(actions['restore']).get('screen'))
    elif actions['marker'] and allow_source:
        remove_restore_marker(directory)   # stale or finished: the restore it names needs nothing more
    if actions['staleMarker'] is not None:
        stale = actions['staleMarker']
        report['staleMarker'] = stale
        report['warnings'].append(
            'an interrupted restore of %s from iteration %s (freeze %s) never reached the log: nothing was restored, '
            'repeat the restore if it is still wanted%s' % (stale.get('screen'), stale.get('from'), stale.get('frozen'),
                                                            '' if allow_source else ' (marker kept: source writes are disabled)'))
    return report


def needs_recovery(store, directory):
    """Cheap check without the lock: a staging or orphan directory, or the marker of an unfinished restore."""
    parent = os.path.join(directory, ITERATIONS_DIR)
    try:
        snapshot = store.snapshot()
    except PrototypeServerError:
        return False
    if os.path.isdir(parent):
        known = set(iteration_numbers(snapshot.operations))
        try:
            names = os.listdir(parent)
        except OSError:
            names = []
        for name in names:
            if '.tmp-' in name or (_ITERATION_NAME.fullmatch(name) and int(name) not in known):
                return True
    return os.path.lexists(os.path.join(directory, RESTORE_MARKER))


def _attach_recovery(error, notes):
    """S1-2: what recovery wrote travels with every error raised after it (`details.recovery`, attribute)."""
    if not notes:
        return
    setattr(error, 'recovery_notes', list(notes))
    if isinstance(error, PrototypeServerError):
        error.details.setdefault('recovery', list(notes))


def _require_v2(store):
    state = store.state()
    if state == 'v1':
        raise NeedsMigration()
    if state not in ('v2', 'new'):
        raise PrototypeServerError(404, 'not_found', 'prototype not found')


def perform_iterate(store, directory, by='agent', before_freeze=None, allow_source=True):
    """`iterate`: under the prototype lock recovery, `ids --check` (409 `ids_check_failed`, the CLI
    reports FAIL), `before_freeze(number)` (commit of K40; returns a sha or None, raises to block),
    freeze of the current iteration N and one `iteration {number, path, hash, commit?}` operation.

    When recovery appended the operation for an interrupted freeze of the same content, nothing is
    frozen again (`recovered: true`).  Returns `{number, path, hash, commit, operation, current,
    recovered, recovery, warnings}`.
    """
    with store.lock():
        _require_v2(store)
        recovery = recover_prototype(store, directory, by, allow_source=allow_source)
        warnings = recovery_notes(recovery) + list(recovery['warnings'])
        try:
            return _iterate_after_recovery(store, directory, by, before_freeze, recovery, warnings)
        except BaseException as error:
            _attach_recovery(error, recovery_notes(recovery) + list(recovery['warnings']))
            raise


def _iterate_after_recovery(store, directory, by, before_freeze, recovery, warnings):
    if recovery['appended']:
        operation = recovery['appended'][-1]
        snapshot = store.snapshot()
        return {'number': operation['payload']['number'], 'path': operation['payload']['path'],
                'hash': operation['payload']['hash'], 'commit': None, 'operation': operation,
                'current': snapshot.fold['currentIteration'], 'recovered': True, 'recovery': recovery,
                'warnings': warnings}
    try:
        info = collect_proto_ids(read_raw_index(directory))
    except OSError as error:
        raise PrototypeServerError(409, 'screen_missing', 'index.html cannot be read (%s)' % error)
    if not info['ok']:
        raise IdsCheckFailed(info, 'ids --check fails: run skillsync prototype ids --fix first')
    snapshot = store.snapshot()
    if snapshot.size >= store.max_bytes:
        raise FileTooLarge(snapshot.size)
    number = snapshot.fold['currentIteration']
    if os.path.lexists(os.path.join(directory, ITERATIONS_DIR, str(number))):
        raise PrototypeServerError(409, 'iteration_exists', '%s/%d/ already exists' % (ITERATIONS_DIR, number))
    commit = before_freeze(number) if before_freeze is not None else None
    path, digest, freeze_warnings = freeze_iteration(directory, number)
    warnings.extend(freeze_warnings)
    payload = {'number': number, 'path': '%s/%d/' % (ITERATIONS_DIR, number), 'hash': digest}
    if commit:
        payload['commit'] = commit
    written = store.append_locked([{'type': 'iteration', 'by': by, 'payload': payload}])
    return {'number': number, 'path': payload['path'], 'hash': digest, 'commit': commit,
            'operation': written.operations[0], 'current': number + 1, 'recovered': False,
            'recovery': recovery, 'warnings': warnings}


def find_restore_pair(snapshot, by, cid):
    """Operations of an earlier restore with the same (`by`, `cid`) in file order, or []."""
    return [operation for operation in snapshot.operations
            if isinstance(operation, dict) and operation.get('by') == by and operation.get('cid') == cid]


def perform_restore(store, directory, screen, source_number, reason, by, who=None, cid=None, version=None,
                    before_freeze=None, recovery_by=None, allow_source=True, precheck=None, hash_cache=None,
                    log=None, writing=None):
    """`restore` of one screen in the order of "Przywrócenie: kolejność i odtwarzanie", all under the
    prototype lock: dedup (`by`, `cid`) → `precheck()` (role, schema) → recovery → `writing()` (writing marker:
    raises 409 `writing_in_progress`; the browser passes it, the CLI does not) → `version`
    (409 `stale_view`) → `plan_restore` → `unchanged` (D27: the section equals the current one =
    return `unchanged: True` without any write) → (1) `before_freeze(C)` (CLI commit, K40) → (2) freeze of
    the current iteration C (refused with 409 `stale_view` and removed when `index.html` or the hashed files
    changed since the plan, S1-1) → marker `RESTORE_MARKER` (F06) → (3) one write of `iteration {number: C}` +
    `restore {…, hashBefore, hashAfter}` → (4) atomic write of `index.html` → marker removed.  What
    recovery wrote is reported (`warnings`, `log`, `recovery` in a `stale_view` error).

    Returns `{existing, operations, current, plan, commit, recovery, warnings}`; `existing` = the
    operations of a repeated `cid` (nothing written).
    """
    if not allow_source:
        raise PrototypeServerError(403, 'source_writes_disabled', 'this server does not write prototype sources')
    with store.lock():
        _require_v2(store)
        if cid is not None:
            pair = find_restore_pair(store.snapshot(), by, cid)
            if pair:
                return {'existing': True, 'unchanged': False, 'operations': pair, 'current': store.snapshot().fold['currentIteration'],
                        'plan': None, 'commit': None, 'recovery': None, 'warnings': [], 'notes': []}
        if precheck is not None:
            precheck()
        recovery = recover_prototype(store, directory, recovery_by or by, allow_source=True)
        notes = recovery_notes(recovery) + list(recovery['warnings'])
        if log is not None:
            for note in notes:
                log(note)
        warnings = list(notes)
        if writing is not None:
            writing()
        try:
            return _restore_after_recovery(store, directory, screen, source_number, reason, by, who, cid, version,
                                           before_freeze, hash_cache, recovery, notes, warnings)
        except BaseException as error:
            _attach_recovery(error, notes)
            raise


def _restore_after_recovery(store, directory, screen, source_number, reason, by, who, cid, version,
                            before_freeze, hash_cache, recovery, notes, warnings):
    if version is not None:
        hashes = hash_cache.prototype_hashes(directory) if hash_cache is not None else screen_hashes(directory)
        if hashes['version'] != version:
            message = 'the prototype changed since the page was loaded'
            if notes:
                message += ' (%s)' % '; '.join(notes)
            raise PrototypeServerError(409, 'stale_view', message)
    snapshot = store.snapshot()
    planned_hash = current_iteration_hash(directory)   # S1-1: the version the plan is built on
    plan = plan_restore(directory, snapshot.fold, screen, source_number)
    if snapshot.size >= store.max_bytes:
        raise FileTooLarge(snapshot.size)
    frozen = snapshot.fold['currentIteration']
    if os.path.lexists(os.path.join(directory, ITERATIONS_DIR, str(frozen))):
        raise PrototypeServerError(409, 'incomplete_restore', '%s/%d/ already exists' % (ITERATIONS_DIR, frozen))
    if plan['unchanged']:
        # D27: the last control; nothing is frozen, logged, written or committed.
        hashes = hash_cache.prototype_hashes(directory) if hash_cache is not None else screen_hashes(directory)
        return {'existing': False, 'unchanged': True, 'version': hashes['version'], 'operations': [],
                'current': frozen, 'plan': plan, 'commit': None, 'recovery': recovery, 'warnings': warnings,
                'notes': notes}
    commit = before_freeze(frozen) if before_freeze is not None else None
    restore_payload = {'screen': screen, 'from': plan['from'], 'frozen': frozen, 'reason': reason}
    if who is not None:
        restore_payload['who'] = who
    restore_payload['hashBefore'] = plan['hashBefore']
    restore_payload['hashAfter'] = plan['hashAfter']
    write_restore_marker(directory, restore_payload)       # F06: before step (2)
    try:
        frozen_path, digest, freeze_warnings = freeze_iteration(directory, frozen)
        # S1-1: the commit hook (K40) or an editor may have changed the sources since the plan; the freeze
        # holds that newer state, so a restore based on the old plan would drop it.  Refuse without a log entry.
        try:
            changed = read_raw_index(directory) != plan['current'] or digest != planned_hash
        except OSError:
            changed = True
        if changed:
            shutil.rmtree(frozen_path, ignore_errors=True)
            _fsync_directory(os.path.join(directory, ITERATIONS_DIR))
            message = ('the prototype files changed during the restore (commit hook, editor or formatter); '
                       'nothing was restored, load the current version and try again')
            details = {}
            if os.path.lexists(frozen_path):   # S2-5: say so when the freeze could not be removed
                leftover = '%s/%d/' % (ITERATIONS_DIR, frozen)
                message += '; warning: %s could not be removed, delete it by hand before the next restore' % leftover
                details = {'leftover': leftover}
            raise PrototypeServerError(409, 'stale_view', message, details)
        iteration_payload = {'number': frozen, 'path': '%s/%d/' % (ITERATIONS_DIR, frozen), 'hash': digest}
        if commit:
            iteration_payload['commit'] = commit
        partials = [{'type': 'iteration', 'by': by, 'cid': cid, 'payload': iteration_payload},
                    {'type': 'restore', 'by': by, 'cid': cid, 'payload': restore_payload}]
        written = store.append_locked(partials)
    except BaseException:
        # Nothing of step (3) in the log = nothing to finish (an orphan freeze is registered by the next
        # recovery), so the marker goes.  S1-3: when step (3) is already in the log (e.g. Ctrl+C right after
        # `os.replace`), the marker stays and recovery finishes step (4).  A real crash keeps it anyway.
        try:
            logged = _marker_matches(read_restore_marker(directory),
                                     _last_restore(store.snapshot().operations, last_only=False))
        except Exception:
            logged = True
        if not logged:
            remove_restore_marker(directory)
        raise
    warnings.extend(freeze_warnings)
    atomic_write_bytes(os.path.join(directory, 'index.html'), _encode_html(plan['html']))
    remove_restore_marker(directory)                       # F06: after step (4)
    return {'existing': False, 'unchanged': False, 'operations': written.operations, 'current': frozen + 1,
            'plan': plan, 'commit': commit, 'recovery': recovery, 'warnings': warnings, 'notes': notes}


def _preview_parts(store, directory, screen, source_number):
    snapshot = store.snapshot()
    actions = recovery_actions(store, directory, snapshot=snapshot)
    return snapshot, actions, plan_restore(directory, snapshot.fold, screen, source_number)


def preview_restore(store, directory, screen, source_number):
    """`restore --dry-run` and `GET …/restore-preview` (read only; without the lock, and one retry under
    `store.lock()` after a transient `incomplete_restore` of a parallel restore, S1-6): pending recovery is
    taken into account for the numbers (409 `incomplete_restore` when recovery would refuse).
    Returns `{threadsBack, threadsLost, acceptanceLost, unchanged, frozen, current, hashBefore,
    hashAfter, pendingRecovery, warnings}`."""
    _require_v2(store)
    try:
        snapshot, actions, plan = _preview_parts(store, directory, screen, source_number)
    except PrototypeServerError as error:
        if error.code != 'incomplete_restore':
            raise
        # S1-6: a restore running in parallel looks unfinished from outside the lock; one retry under the
        # lock (still read only) sees its end.  A lasting `incomplete_restore` keeps the 409.
        with store.lock():
            snapshot, actions, plan = _preview_parts(store, directory, screen, source_number)
    frozen = snapshot.fold['currentIteration'] + len(actions['append'])
    warnings = []
    if actions['redo'] is not None:
        warnings.append('an interrupted restore of %s will be finished first; numbers are for the current files'
                        % _payload(actions['restore']).get('screen'))
    return {'threadsBack': plan['threadsBack'], 'threadsLost': plan['threadsLost'],
            'acceptanceLost': plan['acceptanceLost'], 'unchanged': plan['unchanged'], 'frozen': frozen, 'current': frozen + 1,
            'hashBefore': plan['hashBefore'], 'hashAfter': plan['hashAfter'],
            'pendingRecovery': {'staging': actions['staging'], 'append': [item['number'] for item in actions['append']],
                                'redo': actions['redo'] is not None},
            'warnings': warnings}


def resume_restore(store, directory, by='agent', allow_source=True):
    """`restore --resume`: recovery that also finishes the last `restore` of the log when it is not
    the last operation (screen hash still `hashBefore`)."""
    with store.lock():
        _require_v2(store)
        return recover_prototype(store, directory, by, allow_source=allow_source, resume=True)


def validate_restore_request(body):
    """Schema of `POST …/restore`: `{cid, screen, from, reason, version}` only (400)."""
    if 'by' in body:
        raise OperationError(400, 'by_not_allowed', 'the author comes from the session')
    unknown = [key for key in body if key not in ('cid', 'screen', 'from', 'reason', 'version')]
    if unknown:
        raise OperationError(400, 'invalid_request', 'unknown fields: %s' % ', '.join(sorted(map(str, unknown))))
    _check_object(body, 'body', ('cid', 'screen', 'from', 'reason', 'version'))
    _check_screen(body['screen'], 'screen')
    _check_int(body['from'], 'from')
    _check_string(body['reason'], 'reason', maximum=MAX_REASON, strip_empty=True)
    _check_hash(body['version'], 'version')



# ── Project-wide acceptance (K43) ────────────────────────────────────────────

class _WritingStarted(Exception):
    """A valid writing marker was found under the prototype lock in `project/accept` (becomes 409 `partial`)."""

    def __init__(self, marker):
        Exception.__init__(self)
        self.marker = marker


class _VersionChanged(Exception):
    """The `version` of a prototype changed between the comparison and the write of `project/accept`."""


def _prototype_title(directory, slug):
    try:
        with open(os.path.join(directory, PROTOTYPE_FILE), 'rb') as handle:
            data = strict_json_loads(handle.read(1024 * 1024).decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return slug
    title = data.get('title') if isinstance(data, dict) else None
    return title if isinstance(title, str) and title else slug


def project_listing(app):
    """`GET /_api/v1/project`: `[{slug, title, iteration, screens, open, awaiting, version, format}]` in the
    order of `slug`.  v1 (`comments.js` only): `format: 1`, `iteration`/`version`/`open`/`awaiting` null.
    A prototype whose `comments.json` cannot be read keeps `format: 2`, nulls and `error`.  `writing`: `None` or
    `{since, expiresAt}` (writing marker)."""
    result = []
    now_ms = clock_ms(app.clock)
    for slug, directory in enumerate_prototype_dirs(app.root):
        store = app.store(slug, directory)
        hashes = app.prototype_hashes(directory)
        entry = {'slug': slug, 'title': _prototype_title(directory, slug), 'iteration': None,
                 'screens': len(hashes['screens']), 'problems': len(unique_problems(hashes['ambiguous'])),
                 'open': None, 'awaiting': None, 'version': None, 'format': 2,
                 'writing': public_writing(directory, now_ms)}
        if store.state() == 'v1':
            entry['format'] = 1
        else:
            try:
                fold = store.snapshot().fold
            except PrototypeServerError as error:
                entry['error'] = error.code
            else:
                entry['iteration'] = fold['currentIteration']
                entry['open'] = open_thread_count(fold)
                entry['awaiting'] = sum(1 for thread in fold['threads'] if thread['status'] == 'awaiting')
                entry['version'] = hashes['version']
        result.append(entry)
    return result


def validate_project_accept_request(body):
    """Schema of `POST …/project/accept`: `{cid, versions: {slug: version}}` only (400)."""
    if 'by' in body:
        raise OperationError(400, 'by_not_allowed', 'the author comes from the session')
    cid = body.get('cid')
    if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
        raise OperationError(400, 'cid_required', 'cid is required')
    unknown = [key for key in body if key not in ('cid', 'versions')]
    if unknown:
        raise OperationError(400, 'invalid_request', 'unknown fields: %s' % ', '.join(sorted(map(str, unknown))))
    versions = body.get('versions')
    if not isinstance(versions, dict):
        raise OperationError(400, 'invalid_request', 'versions must be an object {slug: version}')
    for slug, version in versions.items():
        if version is not None and (not isinstance(version, str) or not HASH_RE.fullmatch(version)):
            raise OperationError(400, 'invalid_request', 'versions.%s must be a version hash or null' % slug)
    return cid, versions


def accept_project(app, login, cid, versions):
    """`POST …/project/accept` for a validated request; returns `(status, body)` or raises.

    1. A v2 prototype whose `comments.json` already has an operation (`login`, `cid`) is done.
    2. The versions of all other v2 prototypes are compared first: a missing or different one gives 409
       `stale_view` `{stale: [slug]}` before any write.
    3. In the order of `slug`, each prototype is written under its own lock (never two at once): `cid` and
       `version` are checked again and one `accept` (`scope: project`, all screens, `via: ui`) is appended;
       a prototype without screens is skipped.
    3a. writing marker: before the first write the markers of all prototypes that would be written (not done by `cid`,
       not empty) are read; any valid one gives 409 `writing_in_progress` `{writing: [{slug, since, expiresAt}]}`
       before `stale_view`.  A marker that appears under the lock of a prototype gives 409 `partial` with the same
       `writing` list (the detected slug is always in it; `accepted` is `[]` when it is the first prototype).
    4. A changed version under the lock or a lock timeout gives 409 `partial` `{accepted, pending}`; any other
       failure (507, `invalid_log`, `OSError` as 500 `write_failed`) keeps its own status and code and carries
       `accepted` and `pending` in the body.
    Success: 201 `{accepted, skipped, skippedScreens?}` (`skippedScreens`: screens left out because their section is
    ambiguous, `[{slug, screen, reason}]`), or 200 with the same body when nothing new was written."""
    prototypes = enumerate_prototype_dirs(app.root)
    skipped, targets, done, skipped_screens = [], [], set(), []
    for slug, directory in prototypes:
        store = app.store(slug, directory)
        if store.state() == 'v1':
            skipped.append(slug)
            continue
        targets.append((slug, directory, store))
        try:
            if store.snapshot().find_cid(login, cid) is not None:
                done.add(slug)
        except PrototypeServerError:
            pass
    stale, empty, no_screens, hashed = [], set(), set(), {}
    for slug, directory, _store in targets:
        hashes = hashed[slug] = app.prototype_hashes(directory)
        skipped_screens.extend({'slug': slug, 'screen': item['screen'], 'reason': item['problem']}
                               for item in unique_problems(hashes['ambiguous']))
        if slug not in done and not hashes['screens']:
            no_screens.add(slug)
    # writing marker: a valid marker of any prototype that would be written refuses the whole request before any
    # write and before `stale_view` (done by `cid` and empty prototypes are left out).
    writing = writing_entries([(slug, directory) for slug, directory, _store in targets
                               if slug not in done and slug not in no_screens], clock_ms(app.clock))
    if writing:
        raise OperationError(409, 'writing_in_progress', 'the agent is still writing screens: %s'
                             % ', '.join(item['slug'] for item in writing), {'writing': writing})
    for slug, directory, _store in targets:
        if slug in done:
            continue
        if versions.get(slug) != hashed[slug]['version']:
            stale.append(slug)
        elif slug in no_screens:
            empty.add(slug)
    if stale:
        raise OperationError(409, 'stale_view', 'the project changed since the window was opened',
                             {'stale': stale})
    accepted, written = [], 0
    pending = [slug for slug, _directory, _store in targets if slug not in done and slug not in empty]
    for slug, directory, store in targets:
        if slug in done:
            accepted.append(slug)
            continue
        if slug in empty:
            skipped.append(slug)
            continue

        def build(snapshot, slug=slug, directory=directory):
            existing = snapshot.find_cid(login, cid)
            if existing is not None:
                return Existing(existing)
            hashes = app.prototype_hashes(directory)
            marker = read_writing(directory, clock_ms(app.clock)) if hashes['screens'] else None
            if marker is not None:
                raise _WritingStarted(marker)   # a marker appeared after the preflight (under the prototype lock)
            if hashes['version'] != versions.get(slug):
                raise _VersionChanged()
            if not hashes['screens']:
                return []
            return {'type': 'accept', 'by': login, 'cid': cid,
                    'payload': {'scope': 'project', 'screens': dict(hashes['screens']),
                                'iteration': snapshot.fold['currentIteration'], 'via': 'ui'}}

        try:
            result = store.transaction(build)
        except _WritingStarted as started:
            listed = writing_entries([(item_slug, item_dir) for item_slug, item_dir, _s in targets
                                      if item_slug in pending], clock_ms(app.clock))
            if all(item['slug'] != slug for item in listed):   # the marker vanished between the detection and the read
                listed.append({'slug': slug, 'since': started.marker['since'], 'expiresAt': started.marker['expiresAt']})
                listed.sort(key=lambda item: item['slug'])
            raise OperationError(409, 'partial', 'not every prototype was accepted: the agent started writing, '
                                 'repeat with the same cid',
                                 {'accepted': sorted(accepted), 'pending': pending, 'writing': listed})
        except (_VersionChanged, LockTimeout):
            raise OperationError(409, 'partial', 'not every prototype was accepted: repeat with the same cid',
                                 {'accepted': sorted(accepted), 'pending': pending})
        except PrototypeServerError as error:
            raise PrototypeServerError(error.status, error.code, error.message,
                                       dict(error.details, accepted=sorted(accepted), pending=list(pending)),
                                       error.headers)
        except OSError as error:
            raise PrototypeServerError(500, 'write_failed', 'cannot write %s (%s)' % (slug, error.__class__.__name__),
                                       {'accepted': sorted(accepted), 'pending': list(pending)})
        pending.remove(slug)
        if result.operations:
            written += 1
            accepted.append(slug)
        elif result.existing is not None:
            accepted.append(slug)
        else:
            skipped.append(slug)
    body = {'accepted': sorted(accepted), 'skipped': sorted(skipped)}
    if skipped_screens:
        body['skippedScreens'] = skipped_screens
    return (201 if written or not done else 200), body


# ── Agent operations (CLI) and merge ─────────────────────────────────────────

def resolve_thread(snapshot, reference):
    """`th-…`/`thread-…` id or thread number → visible thread; 404 `thread_not_found` for a
    missing, deleted or hidden (retracted) thread."""
    fold = snapshot.fold
    thread_id = None
    if isinstance(reference, str) and reference.isascii() and reference.isdigit():
        number = int(reference)
        for candidate, value in fold['numbers'].items():
            if value == number:
                thread_id = candidate
                break
    elif isinstance(reference, str) and LEGACY_THREAD_ID_RE.fullmatch(reference):
        thread_id = reference
    thread = fold['byId'].get(thread_id) if thread_id is not None else None
    if thread is None:
        state = 'deleted' if thread_id in fold['deleted'] else 'retracted' if thread_id in fold['hidden'] else 'missing'
        raise PrototypeServerError(404, 'thread_not_found', 'thread %s does not exist or was removed (%s)'
                                   % (reference, state), {'thread': reference, 'state': state})
    return thread


def thread_has_cli_decision(thread):
    """True when the thread has a `decision` not followed by a `reopen` (triage is refused then)."""
    last_decision = last_reopen = None
    for index, event in enumerate(thread['events']):
        if event['type'] == 'decision':
            last_decision = index
        elif event['type'] == 'reopen':
            last_reopen = index
    return last_decision is not None and (last_reopen is None or last_reopen < last_decision)


def agent_operation(store, reference, kind, payload, by='agent'):
    """Append a CLI operation (`triage`, `decision`, `resolve`, `reply`) on a thread given as `th-…` or a number,
    resolved and checked under the lock; returns `(operation, thread)`."""
    found = {}

    def build(snapshot):
        thread = resolve_thread(snapshot, reference)
        if kind == 'triage' and thread_has_cli_decision(thread):
            raise PrototypeServerError(409, 'thread_decided',
                                       'thread #%d already has a decision: reopen it first' % thread['number'])
        validate_payload(kind, payload, origin='file')
        check_references(snapshot, kind, thread['id'], payload)
        found['thread'] = thread
        return {'type': kind, 'threadId': thread['id'], 'by': by, 'payload': payload}

    result = store.transaction(build, create=False)
    return result.operations[0], found['thread']


def thread_facts(thread, people, screen_ids=None, proto_ids=None):
    """Facts of one thread for `comments --json` (D6): no classification, retracted texts hidden."""
    has_handle = thread_has_handle(thread, screen_ids, proto_ids)
    anchor = thread.get('anchor') or {}

    def name(login):
        person = people.get(login)
        return person['label'] if person else login

    messages = []
    for message in thread['messages']:
        votes = vote_summary(message)
        messages.append({
            'id': message['id'], 'by': message['by'], 'name': name(message['by']),
            'importedAuthor': message['importedAuthor'], 'opening': message['opening'], 'at': message['at'],
            'text': None if message['retracted'] else message['text'], 'retracted': message['retracted'],
            'edited': message['edited'],
            'votes': {'up': [{'login': login, 'name': name(login)} for login in votes['up']],
                      'down': [{'login': login, 'name': name(login)} for login in votes['down']]},
        })
    authors = []
    for message in thread['messages']:
        if not message['retracted'] and message['by'] not in authors:
            authors.append(message['by'])
    return {
        'threadId': thread['id'], 'number': thread['number'], 'screen': thread['screen'],
        'view': thread.get('view', 'desktop'),
        'element': {'kind': anchor_kind(thread.get('anchor')), 'protoId': anchor.get('protoId'),
                    'label': thread['label'], 'hint': thread['hint'], 'handle': has_handle},
        'status': thread['status'], 'statusReason': thread['statusReason'], 'by': thread['by'], 'at': thread['at'],
        'authors': [{'login': login, 'name': name(login)} for login in authors],
        'triage': thread['triage'], 'decisions': thread['decisions'], 'messages': messages,
        'hints': thread_hints(thread, has_handle),
    }


class MergeConflict(PrototypeServerError):
    def __init__(self, conflicts):
        PrototypeServerError.__init__(self, 409, 'merge_conflict', '%d conflict(s)' % len(conflicts),
                                      {'conflicts': conflicts})
        self.conflicts = conflicts


def merge_operations(target, other):
    """K29: `target` + the operations of `other` whose `id` is not in `target` (in `other`'s order).

    Conflicts (raise `MergeConflict`): the same `id` with a different canonical serialization,
    two `iteration` operations with the same `number` and a different `hash`, an added operation
    that fails the schema.  Returns `(operations, added)`.
    """
    by_id = {}
    for operation in target:
        if isinstance(operation, dict) and isinstance(operation.get('id'), str):
            by_id.setdefault(operation['id'], operation)
    conflicts, added = [], []
    for operation in other:
        if not isinstance(operation, dict) or not isinstance(operation.get('id'), str):
            conflicts.append({'id': None, 'reason': 'operation without an id'})
            continue
        known = by_id.get(operation['id'])
        if known is not None:
            if serialize_operation(known) != serialize_operation(operation):
                conflicts.append({'id': operation['id'], 'reason': 'same id, different content'})
            continue
        try:
            validate_operation(operation, origin='file')
        except PrototypeServerError as error:
            conflicts.append({'id': operation['id'], 'reason': 'invalid operation: %s' % error.message})
            continue
        by_id[operation['id']] = operation
        added.append(operation)
    merged = list(target) + added
    # A duplicated iteration number already inside `target` is only a warning (`list`, `comments`);
    # the merge refuses to introduce a new one with a different hash.
    hashes = {}
    for operation in merged:
        if not isinstance(operation, dict) or operation.get('type') != 'iteration':
            continue
        payload = _payload(operation)
        number, digest = payload.get('number'), payload.get('hash')
        if not _is_integral(number):
            continue
        previous = hashes.setdefault(int(number), digest)
        if previous != digest and any(operation is item for item in added):
            conflicts.append({'id': operation.get('id'), 'reason': 'iteration %d frozen twice with a different hash'
                              % int(number)})
    if conflicts:
        raise MergeConflict(conflicts)
    return merged, added


def missing_iterations(directory, operations):
    """`[{id, type, number}]`: `iteration` (`number`) and `restore` (`frozen`, `from`) operations whose
    `iterations/<n>/` is not in the working copy (`directory` None = not known: [])."""
    if directory is None:
        return []
    result = []
    for operation in normalize_operations(operations):
        payload = _payload(operation)
        if operation['type'] == 'iteration':
            numbers = [payload.get('number')]
        elif operation['type'] == 'restore':
            numbers = [payload.get('frozen'), payload.get('from')]
        else:
            continue
        for number in numbers:
            if _is_integral(number) and number >= 1 and iteration_directory(directory, number) is None:
                result.append({'id': operation['id'], 'type': operation['type'], 'number': int(number)})
    return result


# ── Users file ───────────────────────────────────────────────────────────────

def normalize_login(value):
    return value.strip().lower() if isinstance(value, str) else ''


def validate_login(login):
    """Raise `UserError` for a reserved login or one that is not an e-mail address."""
    if login in RESERVED_LOGINS:
        raise UserError('reserved_login', 'login is reserved: %s' % login)
    if not LOGIN_RE.fullmatch(login) or len(login) > 320 or not _encodable(login):
        raise UserError('invalid_login', 'login must be an e-mail address')


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def hash_password(password, iterations=DEFAULT_ITERATIONS, salt=None):
    """`pbkdf2_sha256$<iterations>$<salt b64>$<digest b64>` (salt 16 B, digest 32 B)."""
    salt = os.urandom(SALT_BYTES) if salt is None else salt
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8', 'surrogatepass'), salt, int(iterations))
    return 'pbkdf2_sha256$%d$%s$%s' % (int(iterations), _b64(salt), _b64(digest))


def verify_password(password, encoded):
    """Constant-time check of `password` against an encoded hash; False for any bad format."""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    parts = encoded.split('$')
    if len(parts) != 4 or parts[0] != 'pbkdf2_sha256' or not parts[1].isdigit():
        return False
    iterations = int(parts[1])
    if iterations < 1 or iterations > 10000000:
        return False
    try:
        salt = base64.b64decode(parts[2].encode('ascii'), validate=True)
        expected = base64.b64decode(parts[3].encode('ascii'), validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    if not expected:
        return False
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8', 'surrogatepass'), salt, iterations,
                                 dklen=len(expected))
    return hmac.compare_digest(digest, expected)


def private_file_problem(path):
    """None when `path` is private (no group/other bits), else a message with `chmod 600`.

    On Windows (`os.name == 'nt'`) `st_mode` carries no permissions: returns None and
    callers print `PRIVATE_CHECK_SKIPPED` as a warning.
    """
    if os.name == 'nt':
        return None
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        return 'permissions of %s are too wide (%03o); run: chmod 600 %s' % (
            path, stat.S_IMODE(info.st_mode), path)
    return None


PRIVATE_CHECK_SKIPPED = 'kontrola uprawnień 0600 pominięta na Windows'


def roles_of(record):
    """Roles of an account record as a tuple; `roles` wins over `role`.  Never mutates the record."""
    roles = record.get('roles')
    if isinstance(roles, (list, tuple)) and roles:
        return tuple(roles)
    role = record.get('role')
    return (role,) if isinstance(role, str) else ()


def primary_role(record):
    """Highest role of the account (developer > acceptor > expert > commenter)."""
    roles = roles_of(record)
    for candidate in ROLE_PRECEDENCE:
        if candidate in roles:
            return candidate
    return roles[0] if roles else 'commenter'


def has_privileged_role(record):
    return any(role in PRIVILEGED_ROLES for role in roles_of(record))


def _legacy_role_for(roles):
    """Value of the backward-compatible `role` field: the highest legacy role, `commenter` for expert-only."""
    for candidate in ('developer', 'acceptor'):
        if candidate in roles:
            return candidate
    return 'commenter'


def role_fields(roles):
    """Record fields for a role list (D16): `role` always, `roles` only for `expert` or several roles."""
    roles = list(roles)
    fields = {'role': _legacy_role_for(roles)}
    if 'expert' in roles or len(roles) > 1:
        fields['roles'] = roles
    return fields


def normalize_role_list(role=None, roles=None):
    """Validated, de-duplicated role list from `role`/`roles` arguments (raises `invalid_role`)."""
    if roles is None:
        roles = [role if role is not None else 'commenter']
    result = []
    for item in roles:
        if item not in ROLES:
            raise UserError('invalid_role', 'role must be one of: %s' % ', '.join(ROLES))
        if item not in result:
            result.append(item)
    if not result:
        raise UserError('invalid_role', 'at least one role is required')
    return result


def _validate_users_data(data):
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('users'), list):
        raise UserError('invalid_users_file', 'users file must be {"version": 1, "users": [...]}')
    seen = set()
    for record in data['users']:
        if not isinstance(record, dict):
            raise UserError('invalid_users_file', 'every user must be an object')
        login = record.get('login')
        if not isinstance(login, str) or not login or login in seen:
            raise UserError('invalid_users_file', 'user without a unique login')
        seen.add(login)
        has_role, has_roles = 'role' in record, 'roles' in record
        if not has_role and not has_roles:
            raise UserError('invalid_users_file', 'user %s has an unknown role' % login)
        if has_role and record['role'] not in LEGACY_ROLES:
            raise UserError('invalid_users_file', 'user %s has an unknown role' % login)
        if has_roles:
            listed = record['roles']
            if (not isinstance(listed, list) or not listed
                    or any(not isinstance(item, str) or item not in ROLES for item in listed)
                    or len(set(listed)) != len(listed)):
                raise UserError('invalid_users_file',
                                'user %s has invalid roles (a non-empty list of unique known roles)' % login)
            if has_role and record['role'] != _legacy_role_for(listed):
                raise UserError('invalid_users_file', 'user %s: role must be %s to match roles'
                                % (login, _legacy_role_for(listed)))
        if not isinstance(record.get('password'), str) or not isinstance(record.get('mustChange'), bool):
            raise UserError('invalid_users_file', 'user %s lacks password/mustChange' % login)
        if 'name' in record and record['name'] is not None and not isinstance(record['name'], str):
            raise UserError('invalid_users_file', 'user %s has a non-string name' % login)
    return data


def public_user(record):
    """Account fields safe to print (never the password hash)."""
    result = {'login': record['login']}
    if record.get('name'):
        result['name'] = record['name']
    result['role'] = primary_role(record)
    result['roles'] = list(roles_of(record))
    for key in ('mustChange', 'created', 'passwordChanged'):
        result[key] = record.get(key)
    return result


class UsersFile(object):
    """Users JSON file `{"version": 1, "users": [{login, name?, role, password, mustChange,
    created, passwordChanged}]}` kept 0600, with `users.json.bak` (0600) before each write.

    Reads are cached and reloaded after a change of `(inode, mtime_ns, size)`; an invalid
    file during operation keeps the last good copy.  `iterations` is used for new hashes
    (verification reads the count from each hash).
    """

    def __init__(self, path, iterations=DEFAULT_ITERATIONS, clock=None, lock_timeout=LOCK_TIMEOUT):
        self.path = os.path.abspath(os.fspath(path))
        self.backup_path = self.path + '.bak'
        self.lock_path = self.path + LOCK_SUFFIX
        self.iterations = int(iterations)
        self.clock = clock or system_clock
        self.lock_timeout = lock_timeout
        self._cache = None
        self._key = None
        self._lock = threading.Lock()
        self._dummy = None
        self.on_missing = None   # callback(path) once per disappearance of the file (server log)
        self._missing_reported = False

    def exists(self):
        return os.path.isfile(self.path)

    def permissions_problem(self):
        return private_file_problem(self.path)

    def _read_file(self):
        info = os.lstat(self.path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise UserError('invalid_users_file', 'users file must be a regular file')
        with open(self.path, 'rb') as handle:
            data = handle.read()
        try:
            parsed = strict_json_loads(data.decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise UserError('invalid_users_file', 'users file is not valid JSON')
        return _validate_users_data(parsed), _stat_key(info)

    def load(self, strict=True):
        """Parsed data (cached).  `strict=False` keeps the last good copy on errors."""
        with self._lock:
            try:
                info = os.lstat(self.path)
            except FileNotFoundError:
                if strict:
                    raise UserError('users_file_missing', 'users file does not exist: %s' % self.path)
                # A removed file means no accounts: sessions and logins fail until it is back.
                self._cache, self._key = None, None
                if not self._missing_reported:
                    self._missing_reported = True
                    if self.on_missing is not None:
                        self.on_missing(self.path)
                return {'version': 1, 'users': []}
            self._missing_reported = False
            key = _stat_key(info)
            if self._cache is not None and self._key == key:
                return self._cache
            try:
                data, key = self._read_file()
            except UserError:
                if strict or self._cache is None:
                    raise
                return self._cache
            self._cache, self._key = data, key
            return data

    def users(self, strict=False):
        return list(self.load(strict=strict)['users'])

    def get(self, login):
        for record in self.users():
            if record['login'] == login:
                return record
        return None

    def names(self):
        return dict((record['login'], record['name']) for record in self.users() if record.get('name'))

    def dummy_hash(self):
        if self._dummy is None:
            self._dummy = hash_password(secrets.token_hex(16), self.iterations)
        return self._dummy

    def authenticate(self, login, password):
        """User record for a correct password, else None (an unknown login costs one PBKDF2 too)."""
        record = self.get(login)
        encoded = record['password'] if record is not None else self.dummy_hash()
        valid = verify_password(password, encoded)
        return record if (record is not None and valid) else None

    def update(self, change):
        """Lock, re-read, `change(users_list)` (mutate in place, return value passed through), backup, write 0600."""
        directory = os.path.dirname(self.path)
        if not os.path.isdir(directory):
            os.makedirs(directory, mode=0o700, exist_ok=True)
        with FileLock(self.lock_path, self.lock_timeout, mode=0o600):
            if os.path.lexists(self.path):
                data, _key = self._read_file()
                with open(self.path, 'rb') as handle:
                    previous = handle.read()
            else:
                data, previous = {'version': 1, 'users': []}, None
            result = change(data['users'])
            _validate_users_data(data)
            if previous is not None:
                atomic_write_bytes(self.backup_path, previous, mode=0o600)
            text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
            atomic_write_bytes(self.path, text.encode('utf-8'), mode=0o600)
            with self._lock:
                self._cache, self._key = None, None
            return result

    def now(self):
        return format_at(clock_ms(self.clock))


def _choose_password(password, default_password):
    """`(encoded-source, must_change)` rules shared by `add`/`passwd`/`import`."""
    if password is not None:
        if text_length(password) < MIN_PASSWORD:
            raise UserError('weak_password', 'password must have at least %d characters' % MIN_PASSWORD)
        return password, False
    if not default_password:
        raise UserError('default_password_missing',
                        'no default password: pass --default-password-stdin or a password flag')
    return default_password, True


def _privileged_warning(login, role):
    return ('konto %s (%s) do pierwszego logowania chroni tylko wspólne hasło domyślne; '
            'rozważ --password-prompt' % (login, role))


def _primary_of(role_list):
    return primary_role({'roles': role_list})


def _needs_warning(roles):
    return 'expert' in roles or any(role in PRIVILEGED_ROLES for role in roles)


def add_user(users_file, login, role='commenter', password=None, default_password=None, name=None, roles=None):
    """Create an account; returns `(public record, warnings)`.  Raises `UserError`."""
    login = normalize_login(login)
    validate_login(login)
    role_list = normalize_role_list(role, roles)
    if name is not None:
        name = ' '.join(name.split())
        if not name or text_length(name) > MAX_NAME or not _encodable(name):
            raise UserError('invalid_name', 'name must be 1-%d characters' % MAX_NAME)
    secret, must_change = _choose_password(password, default_password)
    now = users_file.now()
    record = {'login': login}
    if name:
        record['name'] = name
    record.update(role_fields(role_list))
    record.update({'password': hash_password(secret, users_file.iterations),
                   'mustChange': must_change, 'created': now, 'passwordChanged': now})

    def change(users):
        if any(item['login'] == login for item in users):
            raise UserError('user_exists', 'user already exists: %s' % login)
        users.append(record)
    users_file.update(change)
    warnings = [_privileged_warning(login, primary_role(record))] if must_change and _needs_warning(role_list) else []
    return public_user(record), warnings


def set_password(users_file, login, password=None, default_password=None):
    """`passwd`: explicit password, or reset to the default with `mustChange`."""
    login = normalize_login(login)
    secret, must_change = _choose_password(password, default_password)
    encoded = hash_password(secret, users_file.iterations)
    now = users_file.now()

    def change(users):
        for item in users:
            if item['login'] == login:
                item['password'] = encoded
                item['mustChange'] = must_change
                item['passwordChanged'] = now
                return dict(item)
        raise UserError('user_not_found', 'no such user: %s' % login)
    if not users_file.exists():
        raise UserError('users_file_missing', 'users file does not exist: %s' % users_file.path)
    record = users_file.update(change)
    warnings = ([_privileged_warning(login, primary_role(record))]
                if must_change and _needs_warning(roles_of(record)) else [])
    return public_user(record), warnings


def set_roles(users_file, login, roles):
    """Replace the role set of an existing account (`user roles`); recomputes `role`/`roles` (D16)."""
    login = normalize_login(login)
    role_list = normalize_role_list(None, list(roles) if roles else [])
    fields = role_fields(role_list)

    def change(users):
        for item in users:
            if item['login'] == login:
                item.pop('roles', None)
                item.update(fields)
                return dict(item)
        raise UserError('user_not_found', 'no such user: %s' % login)
    if not users_file.exists():
        raise UserError('users_file_missing', 'users file does not exist: %s' % users_file.path)
    record = users_file.update(change)
    warnings = [_privileged_warning(login, ', '.join(roles_of(record)))] if (
        record.get('mustChange') and _needs_warning(roles_of(record))) else []
    return public_user(record), warnings


def change_own_password(users_file, login, new_password, encoded=None):
    """Server-side password change: new hash (`encoded` when precomputed), `mustChange` cleared; returns the hash."""
    encoded = encoded or hash_password(new_password, users_file.iterations)
    now = users_file.now()

    def change(users):
        for item in users:
            if item['login'] == login:
                item['password'] = encoded
                item['mustChange'] = False
                item['passwordChanged'] = now
                return encoded
        raise UserError('user_not_found', 'no such user: %s' % login)
    return users_file.update(change)


def remove_user(users_file, login):
    login = normalize_login(login)
    if not users_file.exists():
        raise UserError('users_file_missing', 'users file does not exist: %s' % users_file.path)

    def change(users):
        for index, item in enumerate(users):
            if item['login'] == login:
                del users[index]
                return public_user(item)
        raise UserError('user_not_found', 'no such user: %s' % login)
    return users_file.update(change)


def list_users(users_file):
    return [public_user(record) for record in users_file.users(strict=True)]


def parse_import_lines(text):
    """Logins from an import file: one address per line; blank lines and `#` comments skipped."""
    logins, invalid = [], []
    for number, line in enumerate(text.splitlines(), 1):
        value = line.strip()
        if not value or value.startswith('#'):
            continue
        login = normalize_login(value)
        try:
            validate_login(login)
        except UserError as error:
            invalid.append({'line': number, 'error': error.code})
            continue
        if login not in logins:
            logins.append(login)
    return logins, invalid


def import_users(users_file, logins, role='commenter', default_password=None, roles=None):
    """Create accounts with the default password and `mustChange`, skipping existing ones."""
    role_list = normalize_role_list(role, roles)
    secret, _must = _choose_password(None, default_password)
    normalized = []
    for login in logins:
        login = normalize_login(login)
        validate_login(login)
        if login not in normalized:
            normalized.append(login)
    now = users_file.now()
    encoded = dict((login, hash_password(secret, users_file.iterations)) for login in normalized)

    def change(users):
        existing = set(item['login'] for item in users)
        added, skipped = [], []
        for login in normalized:
            if login in existing:
                skipped.append(login)
                continue
            entry = {'login': login}
            entry.update(role_fields(role_list))
            entry.update({'password': encoded[login], 'mustChange': True, 'created': now, 'passwordChanged': now})
            users.append(entry)
            added.append(login)
        return added, skipped
    added, skipped = users_file.update(change)
    warnings = ([_privileged_warning(login, _primary_of(role_list)) for login in added]
                if _needs_warning(role_list) else [])
    return {'added': added, 'skipped': skipped}, warnings


# ── Sessions and login rate limits ───────────────────────────────────────────

class Session(object):
    __slots__ = ('token', 'login', 'password_ref', 'pending_ref', 'csrf', 'created', 'last_seen', 'via')

    def __init__(self, token, login, password_ref, csrf, now, via=None):
        self.via = via   # 'link' for a session created by a login link
        self.token = token
        self.login = login
        self.password_ref = password_ref
        self.pending_ref = None   # new hash while this session changes its own password
        self.csrf = csrf
        self.created = now
        self.last_seen = now


class SessionStore(object):
    """In-memory sessions: 32-byte tokens, 12 h idle and 7 days absolute expiry.

    A session is valid only while its user exists and still has the password hash
    the session was created with (removal or password change invalidates it).
    """

    def __init__(self, clock=None, idle=SESSION_IDLE, maximum=SESSION_MAX):
        self.clock = clock or system_clock
        self.idle = idle
        self.maximum = maximum
        self._items = {}
        self._lock = threading.Lock()

    def create(self, login, password_ref, via=None):
        now = self.clock()
        session = Session(secrets.token_urlsafe(32), login, password_ref, secrets.token_urlsafe(32), now, via)
        with self._lock:
            self._prune(now)
            self._items[session.token] = session
        return session

    def _prune(self, now):
        for token in [token for token, item in self._items.items() if self._expired(item, now)]:
            del self._items[token]

    def _expired(self, session, now):
        return now - session.last_seen > self.idle or now - session.created > self.maximum

    def lookup(self, token, users_file):
        """`(session, user)` for a valid token, else `(None, None)`; touches the session."""
        if not token:
            return None, None
        now = self.clock()
        with self._lock:
            session = self._items.get(token)
            if session is None:
                return None, None
            if self._expired(session, now):
                del self._items[token]
                return None, None
        user = users_file.get(session.login)
        with self._lock:
            refs = [session.password_ref] + ([session.pending_ref] if session.pending_ref else [])
        if user is None or not any(hmac.compare_digest(user['password'], ref) for ref in refs):
            with self._lock:
                self._items.pop(token, None)
            return None, None
        with self._lock:
            session.last_seen = now
        return session, user

    def drop(self, token):
        with self._lock:
            self._items.pop(token, None)

    def begin_password_change(self, session, encoded):
        """Accept `encoded` for this session before the users file changes (no window for parallel requests)."""
        with self._lock:
            session.pending_ref = encoded

    def finish_password_change(self, session, encoded, ok):
        with self._lock:
            if ok:
                session.password_ref = encoded
            if session.pending_ref == encoded:   # a parallel change of the same session keeps its own pending hash
                session.pending_ref = None

    def drop_login(self, login, keep=None):
        with self._lock:
            for token in [token for token, item in self._items.items() if item.login == login and token != keep]:
                del self._items[token]

    def count(self):
        with self._lock:
            return len(self._items)


class WriteRateLimiter(object):
    """Sliding window of write attempts per login (K18); bounded memory (expired entries are dropped).

    Every attempt that passes the role gate counts, also those ending in 404/400/409 (no oracle on
    visibility or data); only an attempt refused by the limiter itself is not counted.
    """

    def __init__(self, clock=None, window=QUESTION_WRITE_WINDOW, limit=QUESTION_WRITE_LIMIT, max_keys=100000):
        self.clock = clock or system_clock
        self.window = window
        self.limit = limit
        self.max_keys = max_keys
        self._items = {}
        self._lock = threading.Lock()

    def hit(self, login):
        """Counts an attempt; returns 0 when allowed, otherwise the seconds to wait (>= 1; a refused attempt is not counted)."""
        with self._lock:
            now = self.clock()
            if login not in self._items and len(self._items) >= self.max_keys:
                for key in [key for key, entries in self._items.items() if not entries or now - entries[-1] >= self.window]:
                    del self._items[key]
                if len(self._items) >= self.max_keys:
                    for key in sorted(self._items, key=lambda k: self._items[k][-1])[:max(1, self.max_keys // 10)]:
                        del self._items[key]
            entries = self._items.get(login)
            if entries is None:
                entries = self._items[login] = deque()
            if entries and now < entries[-1]:
                entries.clear()   # the clock jumped back: old stamps would make retryAfter lie
            while entries and now - entries[0] >= self.window:
                entries.popleft()
            if len(entries) >= self.limit:
                return max(1, min(self.window, int(math.ceil(entries[0] + self.window - now))))
            entries.append(now)
            return 0


class LoginRateLimiter(object):
    """Failed logins per (login, IP) and per IP in a sliding window, enforced before PBKDF2.

    `reserve` counts the attempt as pending atomically, so N parallel attempts run at most
    the limit of PBKDF2 computations; `release` turns it into a failure or clears the pair
    on success.  Over `max_keys` entries the oldest ones are evicted (never the whole table).
    """

    def __init__(self, clock=None, window=RATE_WINDOW, pair_limit=RATE_PAIR_LIMIT, ip_limit=RATE_IP_LIMIT,
                 max_keys=100000):
        self.clock = clock or system_clock
        self.window = window
        self.pair_limit = pair_limit
        self.ip_limit = ip_limit
        self.max_keys = max_keys
        self._pairs = {}
        self._ips = {}
        self._pending_pairs = {}
        self._pending_ips = {}
        self._lock = threading.Lock()

    def _trim(self, table, key, now):
        entries = table.get(key)
        if entries is None:
            return None
        while entries and now - entries[0] >= self.window:
            entries.popleft()
        if not entries:
            del table[key]
            return None
        return entries

    def _wait_locked(self, login, ip, now):
        waits = []
        for table, pending, key, limit in ((self._pairs, self._pending_pairs, (login, ip), self.pair_limit),
                                           (self._ips, self._pending_ips, ip, self.ip_limit)):
            entries = self._trim(table, key, now)
            failures = len(entries) if entries is not None else 0
            if failures >= limit:
                waits.append(entries[0] + self.window - now)
            elif failures + pending.get(key, 0) >= limit:
                waits.append(1)   # attempts in flight already use the remaining budget
        if not waits:
            return 0
        return max(1, int(math.ceil(max(waits))))

    def check(self, login, ip):
        """Seconds to wait (`Retry-After`), 0 when an attempt is allowed (pending attempts count)."""
        with self._lock:
            return self._wait_locked(login, ip, self.clock())

    def reserve(self, login, ip):
        """`(wait, reservation)`: wait > 0 refuses; otherwise the attempt is pending until `release`."""
        with self._lock:
            wait = self._wait_locked(login, ip, self.clock())
            if wait:
                return wait, None
            self._pending_pairs[(login, ip)] = self._pending_pairs.get((login, ip), 0) + 1
            self._pending_ips[ip] = self._pending_ips.get(ip, 0) + 1
            return 0, (login, ip)

    def release(self, reservation, failed):
        if reservation is None:
            return
        login, ip = reservation
        with self._lock:
            for pending, key in ((self._pending_pairs, (login, ip)), (self._pending_ips, ip)):
                left = pending.get(key, 0) - 1
                if left > 0:
                    pending[key] = left
                else:
                    pending.pop(key, None)
            if failed:
                self._record_locked(login, ip, self.clock())
            else:
                self._pairs.pop((login, ip), None)

    def _evict_locked(self, table):
        if len(table) < self.max_keys:
            return
        excess = len(table) - self.max_keys + max(1, self.max_keys // 10)
        oldest = sorted(table, key=lambda key: table[key][-1] if table[key] else 0)[:excess]
        for key in oldest:
            del table[key]

    def _record_locked(self, login, ip, now):
        if (login, ip) not in self._pairs:
            self._evict_locked(self._pairs)
        if ip not in self._ips:
            self._evict_locked(self._ips)
        self._pairs.setdefault((login, ip), deque()).append(now)
        self._ips.setdefault(ip, deque()).append(now)

    def failure(self, login, ip):
        with self._lock:
            self._record_locked(login, ip, self.clock())

    def success(self, login, ip):
        with self._lock:
            self._pairs.pop((login, ip), None)


# ── One-time login links (`GET /_login/link`, `skillsync prototype login-link`) ───────────────────────────
#
# Layout under `<repo>/.skillsync/prototype/run/` (0700, owned by the effective user, never a symlink):
#   server-<port>.json (0600)               {schema, pid, host, port, instance, version, root, users}, written by the
#                                            server after bind (`runtime_dir` set) and removed when it stops;
#   login/<sha256(token)>.json (0600)       {login, path, expires} per issued token (the token itself is never stored);
#   login/.<sha256>.<random>.used           a token being redeemed (the rename that makes the use atomic).
# The token (256 bits) exists only on the CLI stdout and in the query of the URL: nothing below logs or raises it.

_LINK_POSIX = os.name != 'nt'
LINK_PATH = '/_login/link'
LINK_RUN_SUBPATH = ('.skillsync', 'prototype', 'run')
LINK_LOGIN_DIR = 'login'
LINK_STATE_SCHEMA = 1
LINK_TOKEN_RE = re.compile(r'[A-Za-z0-9_-]{43}')
LINK_QUERY_RE = re.compile(r't=([A-Za-z0-9_-]{43})')
LINK_TTL_DEFAULT = 60            # seconds a token lives
LINK_TTL_MAX = 120               # the server refuses a token whose expiry is further away than this
LINK_MAX_PENDING = 32            # tokens waiting to be redeemed
LINK_MAX_TOKEN_FILE = 4096
LINK_STALE_SECONDS = 300         # leftovers (`.used`, `.tmp`) older than this are swept by the next `login-link`
LINK_HOSTS = ('127.0.0.1', 'localhost')    # a server listening here is reachable at 127.0.0.1
LINK_WILDCARD_HOSTS = ('0.0.0.0', '::', '')
_LINK_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


class LoginLinkError(Exception):
    """A refusal with a stable `code` (reported as BLOCKED by the CLI)."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def link_run_dir_of(repo):
    return os.path.join(os.fspath(repo), *LINK_RUN_SUBPATH)


def link_token_hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def link_new_token():
    return secrets.token_urlsafe(32)


# ── directories ──────────────────────────────────────────────────────────────

def link_directory_problem(path):
    """`None` when `path` is a private directory (0700, owned by us, not a symlink), `'missing'` when absent,
    else a short description.  On Windows only the symlink/directory part is checked (no mode bits there)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 'missing'
    except OSError as error:
        return 'cannot stat (%s)' % (error.strerror or error.__class__.__name__)
    if stat.S_ISLNK(info.st_mode):
        return 'is a symlink'
    if not stat.S_ISDIR(info.st_mode):
        return 'is not a directory'
    if _LINK_POSIX:
        if info.st_uid != os.geteuid():
            return 'is owned by another user'
        if stat.S_IMODE(info.st_mode) != 0o700:
            return 'has mode %03o instead of 700' % stat.S_IMODE(info.st_mode)
    return None


def _link_make_private_dir(path):
    os.mkdir(path, 0o700)
    if _LINK_POSIX:
        os.chmod(path, 0o700)   # the umask must not narrow or widen it


def link_prepare_run_dir(path):
    """Create the run directory when absent; return a problem text (the link endpoint stays off) or None."""
    problem = link_directory_problem(path)
    if problem == 'missing':
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _link_make_private_dir(path)
        except OSError as error:
            return 'cannot create (%s)' % (error.strerror or error.__class__.__name__)
        problem = link_directory_problem(path)
    if problem is None:
        # The directory ignores itself in git (like a virtualenv), so no tracked/ignored setup is needed.
        marker = os.path.join(path, '.gitignore')
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _LINK_NOFOLLOW, 0o644)
        except FileExistsError:
            pass
        except OSError:
            pass
        else:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write('*\n')
    return problem


def link_login_dir(run_dir, create=False):
    """`<run>/login` after the same checks; created (0700) on request.  Raises `LoginLinkError('run_dir_unsafe')`."""
    problem = link_directory_problem(run_dir)
    if problem is not None:
        raise LoginLinkError('run_dir_unsafe', 'katalog run/ jest niebezpieczny (%s): %s' % (problem, run_dir))
    path = os.path.join(run_dir, LINK_LOGIN_DIR)
    problem = link_directory_problem(path)
    if problem == 'missing' and create:
        try:
            _link_make_private_dir(path)
        except FileExistsError:
            pass
        except OSError as error:
            raise LoginLinkError('run_dir_unsafe', 'nie można utworzyć %s (%s)' % (path, error.strerror or error))
        problem = link_directory_problem(path)
    if problem is not None:
        raise LoginLinkError('run_dir_unsafe', 'katalog login/ jest niebezpieczny (%s): %s' % (problem, path))
    return path


# ── state file ───────────────────────────────────────────────────────────────

def link_state_path(run_dir, port):
    return os.path.join(run_dir, 'server-%d.json' % int(port))


def _link_write_private(path, data):
    """Atomic private write: `O_EXCL` temporary file, fsync, rename onto `path`."""
    temporary = os.path.join(os.path.dirname(path), '.%s.%s.tmp' % (os.path.basename(path), secrets.token_hex(4)))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _LINK_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def link_write_state(run_dir, state):
    path = link_state_path(run_dir, state['port'])
    _link_write_private(path, (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return path


def link_remove_state(path, instance):
    """Remove our own state file (same `instance`); a file of another server stays."""
    try:
        with open(path, 'rb') as handle:
            data = json.loads(handle.read(65536).decode('utf-8'))
        if isinstance(data, dict) and data.get('instance') == instance:
            os.unlink(path)
    except (OSError, ValueError):
        pass


def link_read_states(run_dir):
    """`[state]` of every valid `server-<port>.json` (regular, owned by us, private); broken files are skipped."""
    states = []
    try:
        names = sorted(os.listdir(run_dir))
    except OSError:
        return states
    for name in names:
        found = re.fullmatch(r'server-([0-9]{1,5})\.json', name)
        if not found:
            continue
        path = os.path.join(run_dir, name)
        try:
            descriptor = os.open(path, os.O_RDONLY | _LINK_NOFOLLOW)
        except OSError:
            continue
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or (_LINK_POSIX and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
                continue
            data = json.loads(os.read(descriptor, 65536).decode('utf-8'))
        except (OSError, ValueError):
            continue
        finally:
            os.close(descriptor)
        if not isinstance(data, dict) or data.get('port') != int(found.group(1)):
            continue
        if not (isinstance(data.get('pid'), int) and isinstance(data.get('host'), str)
                and isinstance(data.get('instance'), str) and isinstance(data.get('root'), str)
                and isinstance(data.get('users'), str)):
            continue
        states.append(data)
    return states


def link_pid_alive(pid):
    """Is a process with this PID there?  Windows: always true (`os.kill` would terminate it); the `instance`
    check decides there."""
    if not _LINK_POSIX:
        return True
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def link_host_kind(host):
    """`'loopback'` (127.0.0.1/localhost), `'wildcard'`, or `None` (a host a link cannot be built for)."""
    host = (host or '').strip().lower()
    if host in LINK_HOSTS:
        return 'loopback'
    if host in LINK_WILDCARD_HOSTS:
        return 'wildcard'
    return None


# ── request checks (server) ──────────────────────────────────────────────────

def _link_address(text):
    try:
        address = ipaddress.ip_address(text.split('%', 1)[0].strip('[]'))
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address


def link_is_loopback_peer(peer):
    """The raw `client_address[0]` is exactly 127.0.0.1, ::1 or ::ffff:127.0.0.1."""
    address = _link_address(peer) if isinstance(peer, str) else None
    return address is not None and address in (ipaddress.IPv4Address('127.0.0.1'), ipaddress.IPv6Address('::1'))


_LINK_PROXY_EXACT = frozenset(('forwarded', 'x-real-ip', 'via'))


def link_has_proxy_header(headers):
    for key in headers.keys():
        lowered = key.lower()
        if lowered in _LINK_PROXY_EXACT or lowered.startswith('x-forwarded-'):
            return True
    return False


def link_is_loopback_host_header(values):
    """The one `Host` header names the loopback (127.0.0.1, ::1, localhost; any port)."""
    if len(values) != 1:
        return False
    value = values[0].strip().lower()
    if not value or any(char in value for char in '@/?#\\ \t'):
        return False
    if value.startswith('['):
        end = value.find(']')
        rest = value[end + 1:] if end > 0 else 'x'
        if rest and not re.fullmatch(r':[0-9]{1,5}', rest):
            return False
        host = value[1:end]
    else:
        host, colon, port = value.partition(':')
        if colon and not re.fullmatch(r'[0-9]{1,5}', port):
            return False
    if host == 'localhost':
        return True
    address = _link_address(host)
    return address is not None and address in (ipaddress.IPv4Address('127.0.0.1'), ipaddress.IPv6Address('::1'))


def link_path_allowed(path):
    """`--path` / stored path: a server-relative path (one leading `/`), printable ASCII, not the API."""
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or len(path) > 1000:
        return False
    if '\\' in path or any(ord(char) < 0x21 or ord(char) > 0x7e for char in path):
        return False
    return not (path == '/_api' or path.startswith('/_api/'))


# ── issuing (CLI) ────────────────────────────────────────────────────────────

def _link_read_token_file(path, limit=LINK_MAX_TOKEN_FILE):
    """`dict` of a private regular token file, or None."""
    try:
        descriptor = os.open(path, os.O_RDONLY | _LINK_NOFOLLOW)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        if _LINK_POSIX and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            return None
        data = json.loads(os.read(descriptor, limit).decode('utf-8'))
    except (OSError, ValueError):
        return None
    finally:
        os.close(descriptor)
    if not isinstance(data, dict):
        return None
    login, target, expires = data.get('login'), data.get('path'), data.get('expires')
    if not isinstance(login, str) or not isinstance(target, str):
        return None
    if isinstance(expires, bool) or not isinstance(expires, (int, float)) or expires != expires:
        return None
    instance = data.get('instance')
    return {'login': login, 'path': target, 'expires': float(expires),
            'instance': instance if isinstance(instance, str) else None}


def _link_sweep(directory, now):
    """Remove expired or unreadable tokens and old leftovers; return the number of pending tokens."""
    pending = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(directory, name)
        if re.fullmatch(r'[0-9a-f]{64}\.json', name):
            data = _link_read_token_file(path)
            if data is None or data['expires'] < now:
                _link_unlink(path)
            else:
                pending += 1
        elif name.startswith('.'):
            try:
                if now - os.lstat(path).st_mtime > LINK_STALE_SECONDS:
                    _link_unlink(path)
            except OSError:
                pass
    return pending


def _link_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def link_issue(run_dir, login, path, now=None, ttl=LINK_TTL_DEFAULT, instance=None):
    """Create a one-time token file for `login`; returns `(token, expires)` (epoch seconds).
    Raises `LoginLinkError('run_dir_unsafe' | 'too_many_pending')`."""
    now = time.time() if now is None else now
    directory = link_login_dir(run_dir, create=True)
    if _link_sweep(directory, now) >= LINK_MAX_PENDING:
        raise LoginLinkError('too_many_pending', 'za dużo oczekujących tokenów logowania (%d); poczekaj, aż wygasną' % LINK_MAX_PENDING)
    token = link_new_token()
    digest = link_token_hash(token)
    expires = round(now + min(ttl, LINK_TTL_MAX), 3)
    record = {'login': login, 'path': path, 'expires': expires}
    if instance is not None:
        record['instance'] = instance   # binds the token to one server process
    data = (json.dumps(record, ensure_ascii=True) + '\n').encode('ascii')
    final = os.path.join(directory, digest + '.json')
    temporary = os.path.join(directory, '.%s.%s.tmp' % (digest, secrets.token_hex(4)))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _LINK_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(temporary, final)
    except OSError as error:
        _link_unlink(temporary)
        raise LoginLinkError('run_dir_unsafe', 'nie można zapisać tokenu (%s)' % (error.strerror or error.__class__.__name__))
    return token, expires


# ── redeeming (server) ───────────────────────────────────────────────────────

def link_consume(run_dir, token, now, instance=None):
    """Atomically take the token file (rename, so exactly one caller wins), read and delete it.

    Returns `{login, path, expires}` or None for anything wrong (missing, used, unreadable, expired, too far in the
    future); the caller still checks the account.  Nothing here raises the token or its hash."""
    if not isinstance(token, str) or not LINK_TOKEN_RE.fullmatch(token):
        return None
    try:
        directory = link_login_dir(run_dir)
    except LoginLinkError:
        return None
    digest = link_token_hash(token)
    source = os.path.join(directory, digest + '.json')
    taken = os.path.join(directory, '.%s.%s.used' % (digest, secrets.token_hex(4)))
    if instance is not None:
        # a token of another server (other `instance`) is refused without being used up
        peek = _link_read_token_file(source)
        if peek is not None and peek.get('instance') != instance:
            return None
    try:
        os.rename(source, taken)
    except OSError:
        return None
    try:
        data = _link_read_token_file(taken)
    finally:
        _link_unlink(taken)
    if data is None or (instance is not None and data.get('instance') != instance):
        return None
    if data['expires'] < now or data['expires'] - now > LINK_TTL_MAX:
        return None
    return data


# ── Audit of writes from login-link sessions: `<run>/audit.jsonl` (local, never `comments.json`) ─────────

LINK_AUDIT_FILE = 'audit.jsonl'


def link_audit_append(run_dir, record, lock_timeout=None):
    """Append one JSON line (0600, `O_APPEND|O_NOFOLLOW`, under a `FileLock`); a symlink in place of the file is
    refused (OSError), nothing is written through it.  The record never holds a token or its hash."""
    problem = link_directory_problem(run_dir)
    if problem is not None:
        raise OSError('katalog run/ %s' % problem)
    path = os.path.join(run_dir, LINK_AUDIT_FILE)
    data = (json.dumps(record, ensure_ascii=True) + '\n').encode('ascii')
    with FileLock(path + LOCK_SUFFIX, LOCK_TIMEOUT if lock_timeout is None else lock_timeout, 0o600):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _LINK_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OSError('audit.jsonl nie jest zwykłym plikiem')
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


# ── Server configuration and startup checks ──────────────────────────────────

class ServerConfig(object):
    """Settings of `serve`: the CLI flags plus hooks for tests and the generator.

    `clock` (seconds since the epoch), `pbkdf2_iterations` (new hashes), `lock_timeout`,
    `max_file_bytes` (507 limit), `log` (stream for the request log, None = silent),
    `git_checks` (False skips the git part of `startup_checks`), `recover` (False skips the
    recovery of interrupted freezes and restores at start), `source_writes` (restore from the UI;
    the CLI turns it off with `--no-source-writes` and outside a git working copy), `runtime_dir` (directory of the
    state file and of the login-link tokens; None = neither).
    """

    def __init__(self, root, users, host=DEFAULT_HOST, port=DEFAULT_PORT, secure_cookies=False, trust_proxy=False,
                 login_to_view=False, source_writes=True, clock=None, pbkdf2_iterations=DEFAULT_ITERATIONS,
                 lock_timeout=LOCK_TIMEOUT, log=None, git_checks=True, max_file_bytes=MAX_FILE_BYTES, recover=True,
                 gallery=None, runtime_dir=None):
        self.root = os.path.abspath(os.fspath(root))
        self.users = os.path.abspath(os.fspath(users))
        self.host = host
        self.port = int(port)
        self.secure_cookies = bool(secure_cookies)
        self.trust_proxy = bool(trust_proxy)
        self.login_to_view = bool(login_to_view)
        self.source_writes = bool(source_writes)
        self.clock = clock or system_clock
        self.pbkdf2_iterations = int(pbkdf2_iterations)
        self.lock_timeout = lock_timeout
        self.log = log
        self.git_checks = git_checks
        self.max_file_bytes = int(max_file_bytes)
        self.recover = bool(recover)
        # `gallery`: callable returning the HTML of the gallery computed now; `/` and `/index.html` of the root are
        # answered with it (the static `<root>/index.html` is only the fallback when the callable fails or is None)
        self.gallery = gallery
        # `runtime_dir`: `<repo>/.skillsync/prototype/run/`; None (standalone, tests) = no state file, no login link
        self.runtime_dir = os.path.abspath(os.fspath(runtime_dir)) if runtime_dir is not None else None


def is_loopback_host(host):
    if host in ('localhost', 'localhost.localdomain'):
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def _git(args, cwd, timeout=10):
    try:
        return subprocess.run(['git'] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def git_users_file_problems(users_path):
    """`(errors, warnings)` of the git checks: tracked or not ignored = error; no git = warning."""
    directory = os.path.dirname(users_path)
    inside = _git(['rev-parse', '--is-inside-work-tree'], directory)
    if inside is None:
        return [], ['brak polecenia git: kontrola „plik użytkowników poza gitem” pominięta']
    if inside.returncode != 0 or inside.stdout.strip() != b'true':
        return [], ['katalog pliku użytkowników nie jest repozytorium gita: kontrola gita pominięta']
    errors = []
    tracked = _git(['ls-files', '--error-unmatch', '--', users_path], directory)
    if tracked is not None and tracked.returncode == 0:
        errors.append('plik użytkowników jest śledzony przez git: %s' % users_path)
    ignored = _git(['check-ignore', '-q', '--', users_path], directory)
    if ignored is None or ignored.returncode != 0:
        errors.append('plik użytkowników nie jest ignorowany przez git (git check-ignore): %s' % users_path)
    return errors, []


def in_git_worktree(path):
    """True when `path` is inside a git working copy (no git = False)."""
    process = _git(['rev-parse', '--is-inside-work-tree'], path)
    return process is not None and process.returncode == 0 and process.stdout.strip() == b'true'


def _within(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def enumerate_prototype_dirs(root):
    """`[(slug, path)]` of direct, non-symlink subdirectories of `root` with `prototype.json`."""
    result = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return result
    for name in names:
        if not SLUG_RE.fullmatch(name):
            continue
        path = os.path.join(root, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode) and os.path.lexists(os.path.join(path, PROTOTYPE_FILE)):
            result.append((name, path))
    return result


def v1_prototypes(root):
    """Slugs of prototypes with `comments.js` and without `comments.json`."""
    return [slug for slug, path in enumerate_prototype_dirs(root)
            if os.path.lexists(os.path.join(path, LEGACY_COMMENTS_FILE))
            and not os.path.lexists(os.path.join(path, COMMENTS_FILE))]


def startup_checks(config):
    """`(errors, warnings)` before `serve` starts; any error = exit 2."""
    errors, warnings = [], []
    root_real = os.path.realpath(config.root)
    if not os.path.isdir(root_real):
        errors.append('katalog prototypów nie istnieje: %s' % config.root)
    users_real = os.path.realpath(config.users)
    records = []
    if _within(users_real, root_real):
        errors.append('plik użytkowników leży w serwowanym katalogu: %s' % config.users)
    if not os.path.isfile(users_real):
        errors.append('brak pliku użytkowników: %s (utwórz go: user add)' % config.users)
    else:
        users = UsersFile(config.users)
        problem = users.permissions_problem()
        if problem:
            errors.append(problem)
        if os.name == 'nt':
            warnings.append(PRIVATE_CHECK_SKIPPED)
        try:
            records = users.users(strict=True)
        except UserError as error:
            errors.append(error.message)
            records = []
        weak = sorted(record['login'] for record in records
                      if record['mustChange'] and _needs_warning(roles_of(record)))
        if weak:
            warnings.append('konta akceptujących, programistów i ekspertów z hasłem domyślnym (mustChange): ' + ', '.join(weak))
        if config.git_checks:
            git_errors, git_warnings = git_users_file_problems(users_real)
            errors.extend(git_errors)
            warnings.extend(git_warnings)
    if not config.login_to_view:
        warnings.append(PUBLIC_READ_WARNING)
        if any('expert' in roles_of(record) for record in records):
            warnings.append(EXPERT_PUBLIC_WARNING)
    if not is_loopback_host(config.host):
        warnings.append('adres %s nie jest pętlą zwrotną: HTTP bez TLS, hasła i ciasteczka idą otwartym tekstem; '
                        'poza LAN użyj proxy z TLS i --secure-cookies' % config.host)
    for slug in v1_prototypes(root_real) if os.path.isdir(root_real) else []:
        warnings.append('prototyp v1, uruchom migrate: %s' % slug)
    if os.path.lexists(os.path.join(root_real, QUESTIONS_FILE)):
        warnings.append(LEGACY_QUESTIONS_WARNING)
    return errors, warnings


# ── Application state ────────────────────────────────────────────────────────

class PrototypeApp(object):
    """State shared by request handlers: users, sessions, rate limits, stores."""

    def __init__(self, config):
        self.config = config
        self.root = os.path.realpath(config.root)
        self.clock = config.clock
        self.users = UsersFile(config.users, iterations=config.pbkdf2_iterations, clock=config.clock,
                               lock_timeout=config.lock_timeout)
        self.sessions = SessionStore(clock=config.clock)
        self.users.on_missing = lambda path: self.log(
            'ostrzeżenie: brak pliku użytkowników %s: żadne konto nie działa do jego przywrócenia' % path)
        self.limiter = LoginRateLimiter(clock=config.clock)
        self.write_limiter = WriteRateLimiter(clock=config.clock)
        self.port = config.port
        self.source_writes = config.source_writes
        users_real = os.path.realpath(config.users)
        questions_real = os.path.join(self.root, QUESTIONS_FILE)
        self.denied_paths = frozenset(item.lower() for item in (
            users_real, users_real + '.bak', users_real + LOCK_SUFFIX, questions_real, questions_real + LOCK_SUFFIX))
        self._question_stores = {}
        self._legacy_questions = None
        self._stores = {}
        self._stores_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._forwarded_warned = False
        self.warnings = []
        self.hash_cache = HashCache()
        self.instance = secrets.token_urlsafe(16)   # nonce of this server process (state file, `/session`)
        self.link_dir = None                        # run directory while the login-link endpoint is active
        self.state_file = None

    def enable_login_link(self, bound):
        """Prepare `runtime_dir` and write the state file after bind; any problem leaves the endpoint off (warning)."""
        config = self.config
        run_dir = config.runtime_dir
        if run_dir is None:
            return
        problem = link_prepare_run_dir(run_dir)
        if problem is None and config.git_checks:
            inside = _git(['rev-parse', '--is-inside-work-tree'], run_dir)
            if inside is not None and inside.returncode == 0 and inside.stdout.strip() == b'true':
                ignored = _git(['check-ignore', '-q', '--', os.path.join(run_dir, LINK_LOGIN_DIR, 'x.json')], run_dir)
                if ignored is None or ignored.returncode != 0:
                    problem = 'nie jest ignorowany przez git (git check-ignore)'
        if problem is not None:
            self.warnings.append('katalog run/ %s: %s; logowanie z linku wyłączone' % (run_dir, problem))
            return
        state = {'schema': LINK_STATE_SCHEMA, 'pid': os.getpid(), 'host': bound.server_address[0],
                 'port': bound.port, 'instance': self.instance, 'version': SERVER_VERSION, 'root': self.root,
                 'users': os.path.realpath(config.users), 'link': not config.trust_proxy}
        try:
            self.state_file = link_write_state(run_dir, state)
        except (OSError, UnicodeEncodeError) as error:
            self.warnings.append('plik stanu serwera w %s nie powstał (%s): logowanie z linku wyłączone'
                                 % (run_dir, getattr(error, 'strerror', None) or error.__class__.__name__))
            return
        if config.trust_proxy:
            # state file (liveness) stays with `link: false`; no endpoint, no `login-link` feature
            self.warnings.append('--trust-proxy: logowanie z linku wyłączone (link działa tylko bez proxy)')
            return
        self.link_dir = run_dir

    def redeem_link(self, query):
        """`(user, path)` for a valid one-time token in `query`, else None (every reason alike)."""
        found = LINK_QUERY_RE.fullmatch(query or '')
        if found is None or self.link_dir is None:
            return None
        now = self.clock()
        data = link_consume(self.link_dir, found.group(1), now, self.instance)
        if data is None or not link_path_allowed(data['path']) or safe_next(data['path']) != data['path']:
            return None
        try:
            user = self.users.get(data['login'])
        except UserError:
            return None
        if user is None or user['mustChange']:
            return None
        return user, data['path']

    def audit(self, session, op, screen=None):
        """One line in `run/audit.jsonl` for a write made from a login-link session (nothing for other sessions
        or without `runtime_dir`); a failure only warns in the log, the write itself has happened."""
        if session is None or session.via != 'link' or self.link_dir is None:
            return
        record = {'at': format_at(clock_ms(self.clock)), 'login': session.login, 'via': 'link', 'op': op}
        if screen:
            record['screen'] = screen
        try:
            link_audit_append(self.link_dir, record, self.config.lock_timeout)
        except (OSError, PrototypeServerError) as error:
            self.log('ostrzeżenie: audyt sesji z linku nie zapisany (%s)' % (getattr(error, 'strerror', None)
                                                                          or error.__class__.__name__))

    def close_link(self):
        if self.state_file is not None:
            link_remove_state(self.state_file, self.instance)
            self.state_file = None
        self.link_dir = None

    def recover_all(self):
        """Recovery of interrupted freezes and restores at the start of `serve` (`by: server`).

        Returns warnings; a refusal (`incomplete_restore`) or a busy lock only warns, reading goes on.
        """
        warnings = []
        for slug, directory in enumerate_prototype_dirs(self.root):
            store = self.store(slug, directory)
            if store.state() != 'v2' or not needs_recovery(store, directory):
                continue
            try:
                with store.lock():
                    report = recover_prototype(store, directory, 'server', allow_source=self.source_writes)
            except PrototypeServerError as error:
                warnings.append('prototyp %s: odtwarzanie przerwanego przywrócenia odmówione (%s: %s)'
                                % (slug, error.code, error.message))
                continue
            except OSError as error:
                warnings.append('prototyp %s: odtwarzanie nie powiodło się (%s)' % (slug, error))
                continue
            done = []
            if report['removed']:
                done.append('usunięto ' + ', '.join(report['removed']))
            if report['appended']:
                done.append('dopisano iterację ' + ', '.join(str(op['payload']['number']) for op in report['appended']))
            if report['redone'] is not None:
                done.append('dokończono przywrócenie ekranu %s' % report['redone']['payload']['screen'])
            if done:
                warnings.append('prototyp %s: odtwarzanie: %s' % (slug, '; '.join(done)))
            warnings.extend('prototyp %s: %s' % (slug, item) for item in report['warnings'])
        return warnings

    def prototype_hashes(self, directory):
        """`screen_hashes` from the stat-keyed cache; an unreadable `index.html` gives no screens."""
        try:
            return self.hash_cache.prototype_hashes(directory)
        except OSError:
            return {'assets': None, 'head': None, 'screens': {}, 'version': prototype_version({}), 'problems': [],
                    'ambiguous': []}

    @property
    def cookie_name(self):
        return 'skillsync_proto_%d' % self.port

    def log(self, message):
        stream = self.config.log
        if stream is None:
            return
        with self._log_lock:
            try:
                stream.write(message + '\n')
                stream.flush()
            except (OSError, ValueError):
                pass

    def warn_forwarded_once(self):
        if self._forwarded_warned:
            return
        self._forwarded_warned = True
        self.log('ostrzeżenie: żądanie z X-Forwarded-For bez --trust-proxy; nagłówek jest ignorowany, '
                 'a limit prób liczy adres proxy')

    def prototype_dir(self, slug):
        """`(directory, state)`; raises 404 for an invalid slug or a missing prototype."""
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        directory = os.path.join(self.root, slug)
        try:
            info = os.lstat(directory)
        except OSError:
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        if not stat.S_ISDIR(info.st_mode) or not os.path.lexists(os.path.join(directory, PROTOTYPE_FILE)):
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        store = self.store(slug, directory)
        return directory, store.state()

    def store(self, slug, directory=None):
        with self._stores_lock:
            store = self._stores.get(slug)
            if store is None:
                store = self._stores[slug] = CommentsStore(directory or os.path.join(self.root, slug), slug,
                                                           clock=self.clock, lock_timeout=self.config.lock_timeout,
                                                           max_bytes=self.config.max_file_bytes)
            return store

    def questions_store(self, slug):
        """The `QuestionsStore` of `<root>/<slug>/questions.json` (one per run, created on first use); 404 for an
        invalid slug or a directory that is not a prototype."""
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        with self._stores_lock:
            store = self._question_stores.get(slug)
            if store is not None:
                return store
        directory = os.path.join(self.root, slug)
        try:
            info = os.lstat(directory)
        except OSError:
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        if not stat.S_ISDIR(info.st_mode) or not os.path.lexists(os.path.join(directory, PROTOTYPE_FILE)):
            raise PrototypeServerError(404, 'not_found', 'prototype not found')
        with self._stores_lock:
            store = self._question_stores.get(slug)
            if store is None:
                store = self._question_stores[slug] = QuestionsStore(
                    directory, clock=self.clock, lock_timeout=self.config.lock_timeout)
            return store

    def question_slugs(self):
        """Slugs of the runs that have a `questions.json` (a regular file; symlinks and other kinds are skipped), sorted."""
        found = []
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return found
        for name in names:
            if not SLUG_RE.fullmatch(name):
                continue
            directory = os.path.join(self.root, name)
            try:
                if stat.S_ISLNK(os.lstat(directory).st_mode) or not os.path.isdir(directory):
                    continue
                if not os.path.lexists(os.path.join(directory, PROTOTYPE_FILE)):   # `questions_store` needs a prototype
                    continue
                info = os.lstat(os.path.join(directory, QUESTIONS_FILE))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                found.append(name)
        return found

    def legacy_questions_store(self):
        """The old project-wide `<root>/questions.json` (read only since 1.2.1), or None when it does not exist."""
        path = os.path.join(self.root, QUESTIONS_FILE)
        if not os.path.lexists(path):
            return None
        with self._stores_lock:
            if self._legacy_questions is None:
                self._legacy_questions = QuestionsStore(self.root, clock=self.clock,
                                                        lock_timeout=self.config.lock_timeout)
            return self._legacy_questions

    def locate_question(self, question_id, slug=None):
        """`(slug, store, snapshot)` of the file that holds the question, `slug` None for the legacy file, or None.
        `slug` narrows the search to one run; a damaged file of another run is skipped, the asked one raises."""
        if not isinstance(question_id, str) or not QUESTION_ID_RE.fullmatch(question_id):
            return None
        for name in ([slug] if slug is not None else self.question_slugs()):
            store = self.questions_store(name)
            try:
                snapshot = store.snapshot()
            except QuestionsFileError:
                if slug is not None:
                    raise
                continue
            if question_id in snapshot.fold['byId']:
                return name, store, snapshot
        if slug is None:
            legacy = self.legacy_questions_store()
            if legacy is not None:
                try:
                    snapshot = legacy.snapshot()
                except QuestionsFileError:
                    return None
                if question_id in snapshot.fold['byId']:
                    return None, legacy, snapshot
        return None

    def collect_questions(self, viewer, slug=None):
        """`{questions, runs, legacy}` for `viewer`: `questions` of the run `slug` (all runs and the legacy file without
        it), `runs` = `[{slug, open, answered, total}]` over every run with a visible question, `legacy` = `{count}` or None."""
        views, runs, damaged, seen = [], [], [], set()
        for name in self.question_slugs():
            try:
                snapshot = self.questions_store(name).snapshot()
                seen.update(snapshot.fold['byId'])
                found = visible_questions(snapshot, viewer, name)
            except QuestionsFileError:
                if slug == name:
                    raise
                damaged.append(name)
                continue
            if not found:
                continue
            runs.append({'slug': name, 'open': sum(1 for item in found if item['state'] == 'open'),
                         'answered': sum(1 for item in found if item['state'] == 'answered'), 'total': len(found)})
            if slug is None or slug == name:
                views.extend(found)
        legacy_info = None
        legacy = self.legacy_questions_store()
        if legacy is not None:
            try:
                # a question already moved to a run (an interrupted migration) is shown once, from its run
                old = [item for item in visible_questions(legacy.snapshot(), viewer, None) if item['id'] not in seen]
            except QuestionsFileError:
                old = []
                damaged.append(None)
            legacy_info = {'count': len(old), 'readOnly': True}
            if slug is None:
                views.extend(old)
        views.sort(key=lambda item: (item['at'], item['id']))
        result = {'questions': views, 'runs': runs, 'legacy': legacy_info}
        if damaged and any(role in viewer['roles'] for role in PRIVILEGED_ROLES):
            result['damaged'] = [item for item in damaged if item is not None] + (['questions.json'] if None in damaged else [])
        return result

    def user_payload(self, user):
        first, full = person_names(user['login'], user.get('name'))
        return {'login': user['login'], 'name': full, 'firstName': first, 'role': primary_role(user),
                'roles': list(roles_of(user)), 'mustChange': bool(user['mustChange'])}


def safe_next(value):
    """`next` of `/_login`: a local path starting with exactly one `/`, else `/`."""
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//') or '\\' in value:
        return '/'
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in value):
        return '/'
    return value


def _host_key(value, scheme):
    value = value.strip().lower()
    if scheme == 'http' and value.endswith(':80'):
        value = value[:-3]
    elif scheme == 'https' and value.endswith(':443'):
        value = value[:-4]
    return value


_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.png': 'image/png',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
    '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
    '.otf': 'font/otf', '.pdf': 'application/pdf', '.xml': 'application/xml; charset=utf-8',
}


def content_type_for(path):
    return _MIME_TYPES.get(os.path.splitext(path)[1].lower(), 'application/octet-stream')


_CONTROL_CHARS = re.compile('[\x00-\x1f\x7f]')


def _log_safe(text):
    """Control characters of client-supplied text (path, error line) as `\\xNN`, so a request cannot forge log lines."""
    return _CONTROL_CHARS.sub(lambda match: '\\x%02x' % ord(match.group()), text)


_API_PREFIX = '/_api/v1'
_OPERATIONS_PATH = re.compile(r'/_api/v1/prototypes/([^/]+)/operations')
_RESTORE_PATH = re.compile(r'/_api/v1/prototypes/([^/]+)/restore')
_PREVIEW_PATH = re.compile(r'/_api/v1/prototypes/([^/]+)/restore-preview')
_QUESTIONS_PATH = _API_PREFIX + '/questions'
_RUN_QUESTIONS_PATH = re.compile(r'/_api/v1/prototypes/([^/]+)/questions')
_QUESTION_PATH = re.compile(r'/_api/v1/(?:prototypes/([^/]+)/)?questions/([^/]+)')
_QUESTION_ACTION_PATH = re.compile(r'/_api/v1/(?:prototypes/([^/]+)/)?questions/([^/]+)/(answer|comment)')
_LOGIN_TO_VIEW_EXCEPTIONS = ('/_login', _API_PREFIX + '/login', _API_PREFIX + '/session')


# ── HTTP handler ─────────────────────────────────────────────────────────────

class PrototypeRequestHandler(BaseHTTPRequestHandler):
    server_version = 'skillsync-prototype/' + SERVER_VERSION
    sys_version = ''
    timeout = 30

    def __getattr__(self, name):
        # Every method without a `do_*` handler gets 405 (BaseHTTPRequestHandler would send 501).
        if name.startswith('do_'):
            return self._method_not_allowed
        raise AttributeError(name)

    # ── plumbing ──

    @property
    def app(self):
        return self.server.app

    def log_request(self, code='-', size='-'):
        # `parse_request` logs a refusal (`send_error`) before `path`/`command` exist (bad request line).
        raw_path = getattr(self, 'path', None)
        if isinstance(raw_path, str):
            try:
                path = urlsplit(raw_path).path
            except ValueError:
                path = '-'
        else:
            # Never the query: a login-link token may sit in a malformed request line (cut at `?`, long token-like runs hidden).
            line = str(getattr(self, 'requestline', '') or '-').split('?', 1)[0]
            path = re.sub(r'[A-Za-z0-9_-]{32,}', '…', line)
        path = path[:200]   # cut before `_log_safe`, so no `\xNN` sequence is split
        try:
            code = int(code)
        except (TypeError, ValueError):
            pass
        command = getattr(self, 'command', None) or '-'
        self.app.log(_log_safe('%s %s %s %s' % (command, path, code, getattr(self, '_login', None) or '-')))

    def log_error(self, format, *args):
        self.app.log(_log_safe('błąd HTTP %s' % (args[0] if args else '')))

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        if not getattr(self, '_strict_page', False):   # the expert page sends its own Referrer-Policy and CSP
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "frame-ancestors 'self'")
        BaseHTTPRequestHandler.end_headers(self)

    def _send(self, status, body=b'', content_type=None, headers=None, cache='no-store'):
        self.send_response(status)
        if content_type:
            self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if cache:
            self.send_header('Cache-Control', cache)
        for key, value in (headers or {}).items():
            if isinstance(value, (list, tuple)):
                for item in value:
                    self.send_header(key, item)
            else:
                self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def _json(self, status, value, headers=None):
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if not _encodable(text):
            text = json.dumps(value, ensure_ascii=True, allow_nan=False)
        body = text.encode('utf-8')
        self._send(status, body, 'application/json; charset=utf-8', headers, 'no-store')

    def _empty(self, status, headers=None):
        self._send(status, b'', None, headers, 'no-store')

    def _error(self, error):
        value = {'error': error.code}
        if error.message:
            value['message'] = error.message
        value.update(error.details)
        self._json(error.status, value, error.headers)

    def _method_not_allowed(self):
        self._login = None
        self.close_connection = True
        self._json(405, {'error': 'method_not_allowed'}, {'Allow': 'GET, HEAD, POST'})

    def do_GET(self):
        self._dispatch('GET')

    def do_HEAD(self):
        self._dispatch('HEAD')

    def do_POST(self):
        self._dispatch('POST')

    def _client_ip(self):
        forwarded = self.headers.get('X-Forwarded-For')
        if forwarded:
            if self.app.config.trust_proxy:
                parts = [part.strip() for part in forwarded.split(',') if part.strip()]
                if parts:
                    return parts[-1]
            else:
                self.app.warn_forwarded_once()
        return self.client_address[0] if self.client_address else '-'

    def _cookie_token(self):
        header = self.headers.get('Cookie')
        if not header:
            return None
        name = self.app.cookie_name
        for part in header.split(';'):
            key, sep, value = part.strip().partition('=')
            if sep and key == name:
                return value.strip().strip('"') or None
        return None

    def _session(self):
        token = self._cookie_token()
        session, user = self.app.sessions.lookup(token, self.app.users)
        return session, user

    def _cookie(self, token, expire=False):
        parts = ['%s=%s' % (self.app.cookie_name, '' if expire else token), 'HttpOnly', 'SameSite=Lax', 'Path=/']
        parts.append('Max-Age=0' if expire else 'Max-Age=%d' % SESSION_MAX)
        if self.app.config.secure_cookies:
            parts.append('Secure')
        return '; '.join(parts)

    # ── dispatch ──

    def _dispatch(self, method):
        self._login = None
        self._strict_page = False
        try:
            split = urlsplit(self.path)
            path = split.path or '/'
            session, user = self._session()
            if session is not None:
                self._login = session.login
            if self.headers.get('X-Forwarded-For') and not self.app.config.trust_proxy:
                self.app.warn_forwarded_once()
            if path == _API_PREFIX or path.startswith(_API_PREFIX + '/') or path.startswith('/_api/'):
                self._api(method, path, split.query, session, user)
                return
            if path == LINK_PATH and self.app.link_dir is not None:
                self._login_link(method, split.query, session)
                return
            if path == '/_login':
                self._login_page(method, split.query)
                return
            if path == EXPERTS_PATH or path.startswith(EXPERTS_PATH + '/'):
                self._experts_page(method, session, path)
                return
            if method not in ('GET', 'HEAD'):
                self._method_not_allowed()
                return
            if self.app.config.login_to_view and session is None:
                if method == 'GET' and (path.endswith('/') or path.endswith('.html')):
                    self._empty(302, {'Location': '/_login?next=' + quote(path, safe='/')})
                else:
                    self._json(401, {'error': 'login_required'})
                return
            self._static(path, split.query)
        except PrototypeServerError as error:
            self._error(error)
        except OverflowError:
            self._json(400, {'error': 'invalid_request', 'message': 'a number in the request is out of range'})
        except UserError as error:
            self._json(500, {'error': error.code})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # pragma: no cover - defensive
            self.app.log('błąd wewnętrzny:\n' + traceback.format_exc())
            try:
                self._json(500, {'error': 'internal_error'})
            except Exception:
                self.close_connection = True

    # ── static files ──

    def _not_found(self):
        self._send(404, b'Not found\n', 'text/plain; charset=utf-8', None, 'no-cache')

    def _live_gallery(self, target):
        """`<root>/index.html` served from `config.gallery()` (state as of now: acceptance, comments, new prototypes).
        False = not the gallery or the callable failed (the caller falls back to the static file)."""
        gallery = self.app.config.gallery
        if gallery is None or target != os.path.realpath(os.path.join(self.app.root, 'index.html')):
            return False
        try:
            html = gallery()
        except Exception as error:   # noqa: BLE001 — galeria na żywo nie może położyć serwera; plik statyczny zostaje
            self.app.log('ostrzeżenie: galeria na żywo nie powstała (%s): podaję statyczny index.html' % error)
            return False
        if not isinstance(html, str):
            return False
        self._send(200, html.encode('utf-8'), 'text/html; charset=utf-8', None, 'no-cache')
        return True

    def _static(self, raw_path, query):
        try:
            decoded = unquote(raw_path, errors='strict')
        except UnicodeDecodeError:
            self._not_found()
            return
        if '\x00' in decoded or '\\' in decoded or not decoded.startswith('/'):
            self._not_found()
            return
        segments = decoded[1:].split('/')
        trailing = decoded.endswith('/')
        if trailing:
            segments = segments[:-1]
        for segment in segments:
            if (not segment or segment in ('.', '..') or _excluded_segment(segment) or ':' in segment):
                self._not_found()
                return
        for segment in segments[:2]:
            lowered = segment.lower()
            if (lowered.rstrip('. ') if os.name == 'nt' else lowered) == QUESTIONS_FILE:
                # `<root>/questions.json` (the old project file) and `<root>/<slug>/questions.json` are served only
                # through `/_api/v1/questions`; a deeper file of that name belongs to the prototype (demo data)
                self._not_found()
                return
        root = self.app.root
        target = os.path.realpath(os.path.join(root, *segments)) if segments else root
        if not self._allowed_real(target):
            self._not_found()
            return
        if os.path.isdir(target):
            if not trailing:
                location = quote(decoded, safe='/') + '/' + ('?' + query if query else '')
                self._send(301, b'', None, {'Location': location}, 'no-cache')
                return
            target = os.path.realpath(os.path.join(target, 'index.html'))
            if not self._allowed_real(target):
                self._not_found()
                return
        elif trailing:
            self._not_found()
            return
        if self._live_gallery(target):
            return
        if not os.path.isfile(target):
            self._not_found()
            return
        try:
            handle = open(target, 'rb')
        except OSError:
            self._not_found()
            return
        with handle:
            size = os.fstat(handle.fileno()).st_size
            self.send_response(200)
            self.send_header('Content-Type', content_type_for(target))
            self.send_header('Content-Length', str(size))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    def _allowed_real(self, real):
        root = self.app.root
        if not _within(real, root) or real.lower() in self.app.denied_paths:
            return False
        relative = os.path.relpath(real, root)
        if relative == '.':
            return True
        parts = relative.split(os.sep)
        if len(parts) <= 2 and parts[-1].lower() == QUESTIONS_FILE:   # also behind a symbolic link inside a run
            return False
        return not any(_excluded_segment(part) for part in parts)

    # ── one-time login link ──

    def _link_request_allowed(self):
        """Raw peer is the loopback, no proxy headers, `Host` is the loopback (`--trust-proxy` has no endpoint at all)."""
        peer = self.client_address[0] if self.client_address else None
        if not link_is_loopback_peer(peer):
            return False
        if link_has_proxy_header(self.headers):
            return False
        return link_is_loopback_host_header(self.headers.get_all('Host') or [])

    def _login_link(self, method, query, old_session):
        """`GET /_login/link?t=<token>`: the token becomes a session (302 to the stored path, Set-Cookie);
        every refusal is the same 302 to `/_login`, whatever the reason, and happens before the token is used."""
        if method != 'GET':
            self.close_connection = True
            self._json(405, {'error': 'method_not_allowed'}, {'Allow': 'GET'})
            return
        app = self.app
        grant = None
        try:
            if self._link_request_allowed():
                grant = app.redeem_link(query)
        except OSError:
            grant = None
        if grant is None:
            self._empty(302, {'Location': '/_login'})
            return
        user, target = grant
        if old_session is not None:
            app.sessions.drop(old_session.token)
        session = app.sessions.create(user['login'], user['password'], via='link')
        self._login = user['login']
        self._empty(302, {'Location': target, 'Set-Cookie': self._cookie(session.token)})

    # ── login page ──

    def _login_page(self, method, query):
        if method not in ('GET', 'HEAD'):
            self._method_not_allowed()
            return
        values = parse_qs(query, keep_blank_values=True).get('next')
        if values is not None:
            value = values[0]
            if safe_next(value) != value:
                self._empty(302, {'Location': '/_login?next=%2F'})
                return
        self._send(200, LOGIN_PAGE.encode('utf-8'), 'text/html; charset=utf-8', None, 'no-store')

    def _experts_page(self, method, session, path='/_experts'):
        """`/_experts` and `/_experts/<slug>`: no data in the HTML (the page reads the API after the session); strict own CSP.
        The slug is checked against the slug grammar only (the page itself shows an empty run); anything else is 404."""
        if method not in ('GET', 'HEAD'):
            self._method_not_allowed()
            return
        if path != EXPERTS_PATH and not SLUG_RE.fullmatch(path[len(EXPERTS_PATH) + 1:]):
            self._not_found()
            return
        if session is None:
            self._empty(302, {'Location': '/_login?next=' + quote(path, safe='/')})
            return
        self._strict_page = True
        try:
            self._send(200, EXPERTS_PAGE.encode('utf-8'), 'text/html; charset=utf-8',
                       {'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': EXPERTS_CSP}, 'no-store')
        finally:
            self._strict_page = False   # an exception in `_send` must not leave the flag for the next response

    # ── API ──

    def _api(self, method, path, query, session, user):
        app = self.app
        if app.config.login_to_view and session is None and path not in _LOGIN_TO_VIEW_EXCEPTIONS:
            raise PrototypeServerError(401, 'login_required', 'login required')
        if path == _API_PREFIX + '/session':
            self._require_method(method, ('GET', 'HEAD'))
            self._json(200, {
                'user': app.user_payload(user) if user is not None else None,
                'csrf': session.csrf if session is not None else None,
                'server': {'version': SERVER_VERSION, 'loginToView': app.config.login_to_view,
                           'sourceWrites': app.source_writes, 'instance': app.instance,
                           'features': [item for item in SERVER_FEATURES
                                        if item != 'login-link' or app.link_dir is not None]},
            })
            return
        if path == _API_PREFIX + '/login':
            self._require_method(method, ('POST',))
            self._api_login(session)
            return
        if path == _API_PREFIX + '/logout':
            self._require_method(method, ('POST',))
            self._post_checks(session, user, allow_must_change=True, read_body=False)
            app.sessions.drop(session.token)
            self._login = session.login
            self._empty(204, {'Set-Cookie': self._cookie('', expire=True)})
            return
        if path == _API_PREFIX + '/password':
            self._require_method(method, ('POST',))
            self._api_password(session, user)
            return
        if path == _API_PREFIX + '/project':
            self._require_method(method, ('GET', 'HEAD'))
            self._api_project(session, user)
            return
        if path == _API_PREFIX + '/project/accept':
            self._require_method(method, ('POST',))
            self._api_project_accept(session, user)
            return
        if path == _QUESTIONS_PATH:
            self._require_method(method, ('GET', 'HEAD'))
            slugs = parse_qs(query).get('slug') or []
            self._api_questions_list(session, user, unquote(slugs[0]) if slugs else None)
            return
        match = _RUN_QUESTIONS_PATH.fullmatch(path)
        if match:
            self._require_method(method, ('GET', 'HEAD'))
            self._api_questions_list(session, user, unquote(match.group(1)))
            return
        match = _QUESTION_ACTION_PATH.fullmatch(path)
        if match:
            self._require_method(method, ('POST',))
            self._api_question_post(unquote(match.group(2)), match.group(3), session, user,
                                    unquote(match.group(1)) if match.group(1) else None)
            return
        match = _QUESTION_PATH.fullmatch(path)
        if match:
            self._require_method(method, ('GET', 'HEAD'))
            self._api_question_get(unquote(match.group(2)), session, user,
                                   unquote(match.group(1)) if match.group(1) else None)
            return
        match = _OPERATIONS_PATH.fullmatch(path)
        if match:
            slug = unquote(match.group(1))
            if method in ('GET', 'HEAD'):
                self._api_operations_get(slug, query)
            elif method == 'POST':
                self._api_operations_post(slug, session, user)
            else:
                self._method_not_allowed()
            return
        match = _RESTORE_PATH.fullmatch(path)
        if match:
            self._require_method(method, ('POST',))
            self._api_restore(unquote(match.group(1)), session, user)
            return
        match = _PREVIEW_PATH.fullmatch(path)
        if match:
            self._require_method(method, ('GET', 'HEAD'))
            self._api_restore_preview(unquote(match.group(1)), query, session, user)
            return
        raise PrototypeServerError(404, 'not_found', 'unknown API endpoint')

    def _require_method(self, method, allowed):
        if method not in allowed:
            self.close_connection = True
            raise PrototypeServerError(405, 'method_not_allowed', headers={'Allow': ', '.join(allowed)})

    def _post_checks(self, session, user, allow_must_change=False, require_session=True, read_body=True, raw=False):
        """POST order: session (401) → Content-Type/Origin/CSRF (403) → mustChange (403) → size (413) → JSON."""
        if require_session and session is None:
            self.close_connection = True
            raise PrototypeServerError(401, 'unauthorized', 'login required')
        content_type = (self.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type != 'application/json':
            self.close_connection = True
            raise PrototypeServerError(403, 'invalid_content_type', 'Content-Type must be application/json')
        origin = self.headers.get('Origin')
        if origin is not None:
            host = self.headers.get('Host') or ''
            try:
                parts = urlsplit(origin)
            except ValueError:
                parts = None
            if (parts is None or parts.scheme not in ('http', 'https') or not parts.netloc
                    or _host_key(parts.netloc, parts.scheme) != _host_key(host, parts.scheme)):
                self.close_connection = True
                raise PrototypeServerError(403, 'invalid_origin', 'Origin does not match Host')
        if require_session:
            token = self.headers.get('X-Skillsync-CSRF') or ''
            if not hmac.compare_digest(token.encode('utf-8', 'replace'), session.csrf.encode('ascii')):
                self.close_connection = True
                raise PrototypeServerError(403, 'invalid_csrf', 'missing or wrong X-Skillsync-CSRF')
            if user is not None and user['mustChange'] and not allow_must_change:
                self.close_connection = True
                raise PrototypeServerError(403, 'password_change_required', 'change the password first')
        length_header = self.headers.get('Content-Length')
        if length_header is None:
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                raise PrototypeServerError(411, 'length_required', 'Content-Length is required')
            length = 0
        else:
            try:
                length = int(length_header)
            except ValueError:
                self.close_connection = True
                raise PrototypeServerError(400, 'invalid_request', 'bad Content-Length')
            if length < 0:
                self.close_connection = True
                raise PrototypeServerError(400, 'invalid_request', 'bad Content-Length')
        if length > MAX_BODY:
            self.close_connection = True
            raise PrototypeServerError(413, 'payload_too_large', 'request body is larger than 64 KiB')
        data = self.rfile.read(length) if length else b''
        if raw:
            return data   # the caller parses the body later (the question routes check the role first)
        if not read_body:
            return None
        try:
            body = strict_json_loads(data.decode('utf-8'))
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise PrototypeServerError(400, 'invalid_json', 'body must be JSON')
        if not isinstance(body, dict):
            raise PrototypeServerError(400, 'invalid_request', 'body must be a JSON object')
        return body

    def _api_login(self, old_session):
        app = self.app
        body = self._post_checks(None, None, require_session=False)
        raw_login = body.get('login')
        if not isinstance(raw_login, str) or text_length(raw_login) > 320:
            raise PrototypeServerError(400, 'invalid_request', 'login and password are required')
        login = normalize_login(raw_login)
        password = body.get('password')
        if not login or not isinstance(password, str) or len(password) > MAX_PASSWORD:
            raise PrototypeServerError(400, 'invalid_request', 'login and password are required')
        ip = self._client_ip()
        wait, reservation = app.limiter.reserve(login, ip)
        if wait:
            raise PrototypeServerError(429, 'rate_limited', 'too many failed attempts', {'retryAfter': wait},
                                       {'Retry-After': str(wait)})
        user = None
        try:
            user = app.users.authenticate(login, password)
        finally:
            app.limiter.release(reservation, failed=user is None)
        if user is None:
            raise PrototypeServerError(401, 'invalid_credentials', 'wrong login or password')
        if old_session is not None:
            app.sessions.drop(old_session.token)
        session = app.sessions.create(user['login'], user['password'])
        self._login = user['login']
        self._json(200, {'user': app.user_payload(user), 'csrf': session.csrf},
                   {'Set-Cookie': self._cookie(session.token)})

    def _api_password(self, session, user):
        app = self.app
        body = self._post_checks(session, user, allow_must_change=True)
        current, new = body.get('current'), body.get('new')
        if not isinstance(current, str) or not isinstance(new, str) or len(new) > MAX_PASSWORD:
            raise PrototypeServerError(400, 'invalid_request', 'current and new are required')
        if text_length(new) < MIN_PASSWORD:
            raise PrototypeServerError(400, 'weak_password', 'new password must have at least 10 characters')
        ip = self._client_ip()
        wait, reservation = app.limiter.reserve(user['login'], ip)
        if wait:
            raise PrototypeServerError(429, 'rate_limited', 'too many failed attempts', {'retryAfter': wait},
                                       {'Retry-After': str(wait)})
        valid = False
        try:
            valid = verify_password(current, user['password'])
        finally:
            app.limiter.release(reservation, failed=not valid)
        if not valid:
            raise PrototypeServerError(403, 'invalid_current', 'current password is wrong')
        if new == current:
            raise PrototypeServerError(400, 'weak_password', 'new password must differ from the current one')
        encoded = hash_password(new, app.users.iterations)
        app.sessions.begin_password_change(session, encoded)
        ok = False
        try:
            change_own_password(app.users, user['login'], new, encoded=encoded)
            ok = True
        finally:
            app.sessions.finish_password_change(session, encoded, ok)
        app.sessions.drop_login(user['login'], keep=session.token)
        self._empty(204)

    def _api_operations_get(self, slug, query):
        app = self.app
        _directory, state = app.prototype_dir(slug)
        if state == 'v1':
            raise NeedsMigration()
        params = parse_qs(query, keep_blank_values=True)
        after_values = params.get('after', ['0'])
        try:
            after = int(after_values[0])
        except ValueError:
            after = -1
        if after < 0:
            raise PrototypeServerError(400, 'invalid_after', 'after must be a non-negative integer')
        prefix = params.get('prefix', [None])[0]
        snapshot = app.store(slug).snapshot()
        total = snapshot.total
        reset = after > total or (after > 0 and prefix != snapshot.prefix(after))
        operations = snapshot.operations if (reset or after == 0) else snapshot.operations[after:]
        logins = collect_logins(snapshot.operations)
        logins.extend(who for who in accept_who_logins(snapshot.operations) if who not in logins)
        people = compute_people(logins, app.users.names())
        hashes = app.prototype_hashes(_directory)
        self._json(200, {'prototypeId': slug, 'total': total, 'prefix': snapshot.prefix(total), 'reset': reset,
                         'operations': operations, 'people': people,
                         'iteration': {'current': snapshot.fold['currentIteration']},
                         'version': hashes['version'], 'screens': hashes['screens'],
                         'acceptance': acceptance_state(snapshot.fold, hashes['screens'], hashes.get('aliases')),
                         'iterations': iterations_listing(_directory, snapshot.operations),
                         'writing': public_writing(_directory, clock_ms(app.clock))})

    def _api_operations_post(self, slug, session, user):
        app = self.app
        body = self._post_checks(session, user)
        _directory, state = app.prototype_dir(slug)
        if state == 'v1':
            raise NeedsMigration()
        store = app.store(slug)
        login, role = user['login'], primary_role(user)
        def check_writing():
            marker = read_writing(_directory, clock_ms(app.clock))   # under the transaction lock, the store's clock
            if marker is not None:
                raise writing_conflict(slug, marker)

        result = store.transaction(lambda snapshot: prepare_browser_operation(
            snapshot, login, role, body, current=lambda: app.prototype_hashes(_directory), writing=check_writing))
        if result.existing is not None:
            self._json(200, {'operation': result.existing, 'total': result.total, 'prefix': result.prefix})
            return
        if result.size_warning:
            app.log('ostrzeżenie: comments.json prototypu %s ma %d B (limit ostrzeżenia 10 MiB)' % (slug, result.size))
        written = result.operations[0]
        payload = written.get('payload') if isinstance(written.get('payload'), dict) else {}
        screens = payload.get('screens')
        screen = payload.get('screen') if isinstance(payload.get('screen'), str) else (
            next(iter(screens)) if isinstance(screens, dict) and len(screens) == 1 else None)
        app.audit(session, written['type'], screen)
        self._json(201, {'operation': written, 'total': result.total, 'prefix': result.prefix})

    # ── questions to an expert ──

    def _questions_gate(self, session, user, roles):
        """Session (401) → mustChange (403) → role (403 `forbidden`) for the question routes; the session is
        required always, also without `--login-to-view`.  Returns the viewer `{login, roles}`."""
        if session is None or user is None:
            raise PrototypeServerError(401, 'unauthorized', 'login required')
        if user['mustChange']:
            raise PrototypeServerError(403, 'password_change_required', 'change the password first')
        have = roles_of(user)
        if not any(role in have for role in roles):
            raise PrototypeServerError(403, 'forbidden', 'your role has no access to expert questions')
        return {'login': user['login'], 'roles': have}

    def _api_questions_list(self, session, user, slug=None):
        viewer = self._questions_gate(session, user, ('developer', 'acceptor', 'expert'))
        if slug is not None:
            self.app.questions_store(slug)   # 404 for an invalid slug or a directory that is not a prototype
        self._json(200, self.app.collect_questions(viewer, slug))

    def _api_question_get(self, question_id, session, user, slug=None):
        viewer = self._questions_gate(session, user, ('developer', 'acceptor', 'expert'))
        if slug is not None:
            self.app.questions_store(slug)
        found = self.app.locate_question(question_id, slug)
        view = None if found is None else get_question_view(found[2], question_id, viewer, found[0])
        if view is None:
            raise PrototypeServerError(404, 'not_found', 'question not found')
        self._json(200, {'question': view})

    def _api_question_post(self, question_id, kind, session, user, slug=None):
        """`answer` / `comment`; order: session → CSRF → mustChange → role → rate limit (429) → visibility (404) → body → dedup →
        state (409) → schema (400).  Everything after the role runs in `build`, under the lock, on the snapshot
        of that transaction; the author is the login of the session."""
        app = self.app
        data = self._post_checks(session, user, raw=True)
        allowed = ('expert',) if kind == 'answer' else ('developer', 'acceptor', 'expert')
        viewer = self._questions_gate(session, user, allowed)
        login = user['login']
        wait = app.write_limiter.hit(login)   # independent of the data: counts every attempt past the role gate
        if wait:
            raise PrototypeServerError(429, 'rate_limited', 'too many writes', {'retryAfter': wait},
                                       {'Retry-After': str(wait)})
        if slug is not None:
            app.questions_store(slug)
        found = app.locate_question(question_id, slug)
        if found is None:
            raise PrototypeServerError(404, 'not_found', 'question not found')
        slug, store, located = found
        if slug is None:   # the old project-wide file: visible questions are read only until `question migrate`
            old = located.fold['byId'][question_id]
            if not can_view_question(old, viewer):
                raise PrototypeServerError(404, 'not_found', 'question not found')
            raise PrototypeServerError(409, 'migration_required', 'the question is in the old questions.json: '
                                       'run skillsync prototype question migrate', {'readOnly': True})

        def not_found():
            return OperationError(404, 'not_found', 'question not found')

        seen = {}

        def build(snapshot):
            seen['snapshot'] = snapshot
            question = snapshot.fold['byId'].get(question_id) if QUESTION_ID_RE.fullmatch(question_id) else None
            if question is None or not can_view_question(question, viewer):
                raise not_found()
            if kind == 'answer' and not can_answer(question, viewer):
                raise OperationError(403, 'forbidden', 'the question is not addressed to you')
            try:
                body = strict_json_loads(data.decode('utf-8'))
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise PrototypeServerError(400, 'invalid_json', 'body must be JSON')
            if not isinstance(body, dict):
                raise PrototypeServerError(400, 'invalid_request', 'body must be a JSON object')
            cid = body.get('cid')
            if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
                raise OperationError(400, 'cid_required', 'cid is required')
            found = snapshot.find_cid(login, cid)
            if found is not None:
                if found.get('questionId') != question_id or found.get('type') != kind:
                    raise OperationError(409, 'cid_conflict', 'cid was already used for another operation')
                return Existing(found)
            check_question_state(snapshot.fold, {'type': kind, 'questionId': question_id})
            if kind == 'comment' and len(question['comments']) >= QUESTION_COMMENT_MAX:
                raise OperationError(409, 'comment_limit', 'the question has reached the comment limit')
            partial = {'type': kind, 'questionId': question_id, 'by': login, 'cid': cid}
            for key, value in body.items():
                if key not in partial:
                    partial[key] = value
            return partial

        result = store.transaction(build)
        written = result.existing if result.existing is not None else result.operations[0]
        # the view comes from the state of this very transaction: its snapshot plus what it wrote
        fold = fold_questions(list(seen['snapshot'].operations) + list(result.operations))
        question = fold['byId'].get(question_id)
        view = None if question is None else question_view(question, viewer, fold['byId'], slug)
        if view is None:   # the question vanished from the fold: a damaged file, not a client error
            raise OperationError(500, 'corrupt_questions', 'the questions file is inconsistent')
        operation = {key: written.get(key) for key in ('id', 'type', 'at', 'cid')}
        if not all(isinstance(value, str) for value in operation.values()):
            # a damaged stored operation reached by dedup (lacks a field): a controlled domain error
            raise OperationError(500, 'corrupt_operation', 'the stored operation is damaged')
        status = 200 if result.existing is not None else 201
        if status == 201:
            app.audit(session, 'question/' + kind)
        self._json(status, {'question': view, 'operation': operation})

    def _project_access(self, session, user):
        """401 without a session, 403 `forbidden` for the commenter role."""
        if session is None or user is None:
            raise PrototypeServerError(401, 'unauthorized', 'login required')
        if not has_privileged_role(user):
            raise PrototypeServerError(403, 'forbidden', 'only an acceptor or a developer may accept a project')

    def _api_project(self, session, user):
        self._project_access(session, user)
        self._json(200, {'prototypes': project_listing(self.app)})

    def _api_project_accept(self, session, user):
        body = self._post_checks(session, user)
        cid = body.get('cid')
        if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
            raise OperationError(400, 'cid_required', 'cid is required')
        self._project_access(session, user)
        cid, versions = validate_project_accept_request(body)
        try:
            status, result = accept_project(self.app, user['login'], cid, versions)
        except PrototypeServerError as error:
            if error.details.get('accepted'):   # a partial write (409 partial, 507, 500): those prototypes are written
                self.app.audit(session, 'project/accept')
            raise
        if status == 201:
            self.app.audit(session, 'project/accept')
        self._json(status, result)

    def _restore_access(self, session, user):
        """401 without a session, 403 `forbidden` for the commenter role, 403 `source_writes_disabled`."""
        if session is None or user is None:
            raise PrototypeServerError(401, 'unauthorized', 'login required')
        if not has_privileged_role(user):
            raise PrototypeServerError(403, 'forbidden', 'only an acceptor or a developer may restore a screen')
        if not self.app.source_writes:
            raise PrototypeServerError(403, 'source_writes_disabled', 'this server does not write prototype sources')

    def _api_restore_preview(self, slug, query, session, user):
        app = self.app
        self._restore_access(session, user)
        directory, state = app.prototype_dir(slug)
        if state == 'v1':
            raise NeedsMigration()
        params = parse_qs(query, keep_blank_values=True)
        screen = params.get('screen', [''])[0]
        if not SCREEN_ID_RE.fullmatch(screen):
            raise PrototypeServerError(400, 'invalid_request', 'screen is required')
        try:
            source = int(params.get('from', [''])[0])
        except ValueError:
            source = 0
        if source < 1:
            raise PrototypeServerError(400, 'invalid_request', 'from must be an iteration number')
        preview = preview_restore(app.store(slug), directory, screen, source)
        self._json(200, {'threadsBack': preview['threadsBack'], 'threadsLost': preview['threadsLost'],
                         'acceptanceLost': preview['acceptanceLost'], 'unchanged': preview['unchanged']})

    def _api_restore(self, slug, session, user):
        app = self.app
        body = self._post_checks(session, user)
        directory, state = app.prototype_dir(slug)
        if state == 'v1':
            raise NeedsMigration()
        if not app.source_writes:
            raise PrototypeServerError(403, 'source_writes_disabled', 'this server does not write prototype sources')
        cid = body.get('cid')
        if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
            raise OperationError(400, 'cid_required', 'cid is required')
        login, role = user['login'], primary_role(user)

        def precheck():
            if role not in PRIVILEGED_ROLES:
                raise OperationError(403, 'forbidden', 'only an acceptor or a developer may restore a screen')
            validate_restore_request(body)

        def check_writing():
            marker = read_writing(directory, clock_ms(app.clock))
            if marker is not None:
                raise writing_conflict(slug, marker)

        store = app.store(slug)
        screen_id = body.get('screen') if isinstance(body.get('screen'), str) else None
        known = store.snapshot().find_cid(login, cid) is not None
        try:
            result = perform_restore(store, directory, body.get('screen'), body.get('from'), body.get('reason'),
                                     by=login, cid=cid, version=body.get('version'), recovery_by='server',
                                     allow_source=True, precheck=precheck, hash_cache=app.hash_cache,
                                     log=lambda note: app.log('ostrzeżenie: prototyp %s: %s' % (slug, note)),
                                     writing=check_writing)
        except BaseException:
            if not known and store.snapshot().find_cid(login, cid) is not None:   # the pair of operations was written
                app.audit(session, 'restore', screen_id)
            raise
        extra = {'recovery': result['notes']} if result['notes'] else {}
        if result['unchanged']:
            self._json(200, dict({'unchanged': True, 'version': result['version']}, **extra))
            return
        status = 200 if result['existing'] else 201
        if status == 201:
            app.audit(session, 'restore', screen_id)
        self._json(status, dict({'operations': result['operations'], 'iteration': {'current': result['current']}},
                                **extra))


class PrototypeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != 'nt'
    # Listen backlog: the default of 5 resets bursts of parallel clients (K3: 20 writers).
    request_queue_size = 128

    def __init__(self, address, handler, app):
        self.app = app
        if ':' in address[0]:
            self.address_family = socket.AF_INET6
        ThreadingHTTPServer.__init__(self, address, handler)

    def server_bind(self):
        # Skip `socket.getfqdn` of HTTPServer.server_bind (slow DNS on some hosts).
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port

    def server_close(self):
        self.app.close_link()   # state file of this server goes with it
        ThreadingHTTPServer.server_close(self)

    @property
    def port(self):
        return self.server_address[1]

    @property
    def url(self):
        host = self.server_address[0]
        if ':' in host:
            host = '[%s]' % host
        return 'http://%s:%d/' % (host, self.port)


def make_server(config, check=True):
    """Bind a server (port 0 = free port) without serving.

    With `check`, `startup_checks` errors raise `StartupError`; warnings are in
    `server.app.warnings`.  The cookie name uses the bound port.
    """
    warnings = []
    if check:
        errors, warnings = startup_checks(config)
        if errors:
            raise StartupError(errors, warnings)
    app = PrototypeApp(config)
    app.warnings = list(warnings)
    if config.recover:
        app.warnings.extend(app.recover_all())
    server = PrototypeHTTPServer((config.host, config.port), PrototypeRequestHandler, app)
    app.port = server.port
    app.enable_login_link(server)
    return server


def serve(config, out=None, err=None):
    """Run `serve` in the foreground; returns the exit code (0 after Ctrl+C, 2 when refused)."""
    out = out or sys.stdout
    err = err or sys.stderr
    if config.log is None:
        config.log = err
    try:
        server = make_server(config)
    except StartupError as error:
        for warning in error.warnings:
            err.write('ostrzeżenie: %s\n' % warning)
        for message in error.errors:
            err.write('błąd: %s\n' % message)
        return 2
    except OSError as error:
        err.write('błąd: nie można otworzyć %s:%d (%s)\n' % (config.host, config.port, error.strerror or error))
        return 2
    for warning in server.app.warnings:
        err.write('ostrzeżenie: %s\n' % warning)
    out.write('Serwer prototypów: %s (Ctrl+C kończy)\n' % server.url)
    out.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ── Login page ───────────────────────────────────────────────────────────────

LOGIN_PAGE = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Logowanie · prototypy</title>
<style>
:root{--bg:#f6f7f9;--surface:#fff;--text:#1d2330;--muted:#5b6475;--border:#d5d9e0;--accent:#2f5fd0;--accent-text:#fff;--danger:#b42318;--radius:.6rem;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--surface:#1d2129;--text:#e7eaf0;--muted:#a3abba;--border:#363c48;--accent:#7b9cf0;--accent-text:#0d1220;--danger:#f97066}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(24rem,92vw);background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.5rem}
h1{font-size:1.25rem;margin:0 0 .25rem}p{margin:0 0 1rem;color:var(--muted)}label{display:block;margin:.75rem 0 .25rem;font-weight:600}
input{width:100%;padding:.55rem .7rem;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--text);font:inherit}
button{margin-top:1.25rem;width:100%;padding:.6rem;border:0;border-radius:var(--radius);background:var(--accent);color:var(--accent-text);font:inherit;font-weight:600;cursor:pointer}
input:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.error{color:var(--danger);min-height:1.5em;margin-top:.75rem}[hidden]{display:none}
</style>
</head>
<body>
<main>
<form id="login-form" novalidate>
<h1>Logowanie</h1>
<p>Zaloguj się adresem e-mail, żeby komentować prototypy.</p>
<label for="login">Adres e-mail</label>
<input id="login" name="login" type="email" autocomplete="username" required autofocus>
<label for="password">Hasło</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Zaloguj</button>
<div class="error" id="login-error" role="alert"></div>
</form>
<form id="change-form" novalidate hidden>
<h1>Ustaw własne hasło</h1>
<p>Konto ma hasło domyślne. Ustaw własne (co najmniej 10 znaków), żeby pracować dalej.</p>
<div id="current-box" hidden>
<label for="current">Obecne hasło</label>
<input id="current" name="current" type="password" autocomplete="current-password">
</div>
<label for="new">Nowe hasło</label>
<input id="new" name="new" type="password" autocomplete="new-password" minlength="10" required>
<label for="repeat">Powtórz nowe hasło</label>
<input id="repeat" name="repeat" type="password" autocomplete="new-password" minlength="10" required>
<button type="submit">Zmień hasło</button>
<div class="error" id="change-error" role="alert"></div>
</form>
</main>
<script>
(function () {
  'use strict';
  function safeNext(value) {
    if (typeof value !== 'string' || value.charAt(0) !== '/' || value.charAt(1) === '/' || value.indexOf('\\') >= 0) return '/';
    for (var i = 0; i < value.length; i += 1) { var c = value.charCodeAt(i); if (c < 32 || c === 127) return '/'; }
    return value;
  }
  var next = safeNext(new URLSearchParams(location.search).get('next') || '/');
  var state = { csrf: null, current: null };
  var loginForm = document.getElementById('login-form');
  var changeForm = document.getElementById('change-form');
  function show(id, text) { document.getElementById(id).textContent = text || ''; }
  function post(path, body, csrf) {
    var headers = { 'Content-Type': 'application/json' };
    if (csrf) headers['X-Skillsync-CSRF'] = csrf;
    return fetch(path, { method: 'POST', credentials: 'same-origin', headers: headers, body: JSON.stringify(body) });
  }
  function stepTwo(knownCurrent) {
    loginForm.hidden = true;
    changeForm.hidden = false;
    document.getElementById('current-box').hidden = !!knownCurrent;
    document.getElementById(knownCurrent ? 'new' : 'current').focus();
  }
  fetch('/_api/v1/session', { credentials: 'same-origin' }).then(function (response) {
    return response.ok ? response.json() : null;
  }).then(function (data) {
    if (data && data.user && data.user.mustChange) { state.csrf = data.csrf; stepTwo(false); }
  }).catch(function () {});
  loginForm.addEventListener('submit', function (event) {
    event.preventDefault();
    show('login-error', '');
    var login = document.getElementById('login').value.trim().toLowerCase();
    var password = document.getElementById('password').value;
    post('/_api/v1/login', { login: login, password: password }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (response.status === 200 && data.user) {
          state.csrf = data.csrf;
          if (data.user.mustChange) { state.current = password; stepTwo(true); }
          else location.replace(next);
        } else if (response.status === 429) {
          show('login-error', 'Zbyt wiele nieudanych prób. Spróbuj ponownie za ' + (data.retryAfter || 60) + ' s.');
        } else {
          show('login-error', 'Nieprawidłowy adres e-mail lub hasło.');
        }
      });
    }).catch(function () { show('login-error', 'Brak połączenia z serwerem.'); });
  });
  changeForm.addEventListener('submit', function (event) {
    event.preventDefault();
    show('change-error', '');
    var current = state.current !== null ? state.current : document.getElementById('current').value;
    var fresh = document.getElementById('new').value;
    if (fresh.length < 10) { show('change-error', 'Nowe hasło musi mieć co najmniej 10 znaków.'); return; }
    if (fresh !== document.getElementById('repeat').value) { show('change-error', 'Hasła się różnią.'); return; }
    if (fresh === current) { show('change-error', 'Nowe hasło musi różnić się od obecnego.'); return; }
    post('/_api/v1/password', { current: current, 'new': fresh }, state.csrf).then(function (response) {
      if (response.status === 204) { state.current = null; location.replace(next); return; }
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (data.error === 'invalid_current') show('change-error', 'Obecne hasło jest nieprawidłowe.');
        else if (data.error === 'weak_password') show('change-error', 'Hasło jest za słabe albo równe obecnemu.');
        else if (response.status === 401) show('change-error', 'Sesja wygasła. Zaloguj się ponownie.');
        else show('change-error', 'Nie udało się zmienić hasła.');
      });
    }).catch(function () { show('change-error', 'Brak połączenia z serwerem.'); });
  });
})();
</script>
</body>
</html>
"""


# ── Expert questions page (`GET /_experts`) ─────────────────────────────────

EXPERTS_PATH = '/_experts'

EXPERTS_STYLE = r"""
:root{--bg:#f6f7f9;--surface:#fff;--text:#1d2330;--muted:#4f5869;--border:#c9ced8;--accent:#2f5fd0;--accent-text:#fff;--danger:#b42318;--ok:#157347;--quote:#eef1f6;--radius:.6rem;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--surface:#1d2129;--text:#e7eaf0;--muted:#a9b1c0;--border:#3b4250;--accent:#8aa8f5;--accent-text:#0d1220;--danger:#f97066;--ok:#5fd08f;--quote:#262b35}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:1rem 1.25rem;border-bottom:1px solid var(--border);background:var(--surface)}
h1{font-size:1.25rem;margin:0}h2{font-size:1.15rem;margin:0 0 .5rem}h3{font-size:1rem;margin:1.25rem 0 .5rem}
.who{margin:.25rem 0 0;color:var(--muted);font-size:.9rem}
.layout{display:grid;grid-template-columns:minmax(14rem,22rem) 1fr;gap:1rem;padding:1rem 1.25rem;max-width:80rem;margin:0 auto}
@media (max-width:48rem){.layout{grid-template-columns:1fr;padding:.75rem}}
nav,section.detail,.notice{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1rem}
nav ul{list-style:none;margin:0;padding:0}nav li+li{margin-top:.5rem}
nav a{display:block;padding:.5rem .6rem;border:1px solid var(--border);border-radius:var(--radius);color:var(--text);text-decoration:none}
nav a[aria-current="true"]{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.badge{display:inline-block;margin:.25rem .35rem 0 0;padding:0 .45rem;border:1px solid var(--border);border-radius:1rem;font-size:.8rem;color:var(--muted)}
.badge.block{border-color:var(--danger);color:var(--danger)}.badge.done{border-color:var(--ok);color:var(--ok)}
.muted{color:var(--muted)}
.qtext{white-space:pre-wrap;overflow-wrap:anywhere}
blockquote{margin:.4rem 0;padding:.5rem .75rem;background:var(--quote);border-left:4px solid var(--border);white-space:pre-wrap;overflow-wrap:anywhere}
a{color:var(--accent)}a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible,[tabindex]:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
fieldset{border:1px solid var(--border);border-radius:var(--radius);margin:.75rem 0;padding:.5rem .9rem}legend{font-weight:600;padding:0 .3rem}
.opt{display:flex;gap:.5rem;align-items:flex-start;margin:.4rem 0}.opt input{margin-top:.35rem}
label.block{display:block;margin:.75rem 0 .25rem;font-weight:600}
textarea{width:100%;min-height:5rem;padding:.55rem .7rem;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--text);font:inherit}
button{margin-top:.75rem;padding:.6rem 1.1rem;border:0;border-radius:var(--radius);background:var(--accent);color:var(--accent-text);font:inherit;font-weight:600;cursor:pointer}
button[disabled]{opacity:.6;cursor:progress}
.alert{color:var(--danger);font-weight:600;margin:0 0 .75rem}.status{color:var(--ok);margin:0 0 .75rem}
.alert:empty,.status:empty{position:absolute;width:1px;height:1px;margin:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.entry{border-top:1px solid var(--border);padding:.6rem 0}.entry .meta{color:var(--muted);font-size:.85rem}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
[hidden]{display:none}
"""

EXPERTS_SCRIPT = r"""
(function () {
  'use strict';
  var API = '/_api/v1';
  var BASE = '/_experts';
  var LOGIN = '/_login?next=' + encodeURIComponent(location.pathname);
  var SLUG = (function () {
    var rest = location.pathname.slice(BASE.length).replace(/^\//, '');
    return /^[a-z0-9]+(-[a-z0-9]+)*$/.test(rest) ? rest : null;
  })();
  var state = { csrf: null, questions: [], runs: [], legacy: null, current: null, pending: {}, busy: {} };
  var listBox = document.getElementById('list');
  var detail = document.getElementById('detail');
  var alertBox = document.getElementById('alert');
  var statusBox = document.getElementById('status');
  var whoBox = document.getElementById('who');

  function el(tag, props, children) {
    var node = document.createElement(tag);
    var key;
    props = props || {};
    for (key in props) {
      if (Object.prototype.hasOwnProperty.call(props, key)) {
        if (key === 'text') node.textContent = props[key];
        else node.setAttribute(key, props[key]);
      }
    }
    (children || []).forEach(function (child) { if (child) node.appendChild(child); });
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function say(text) { statusBox.textContent = text || ''; }
  function fail(text) { alertBox.textContent = text || ''; }
  function when(value) {
    var date = new Date(value);
    return isNaN(date.getTime()) ? String(value || '') : date.toLocaleString('pl-PL');
  }
  function newCid() {
    var bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    var out = 'c';
    for (var i = 0; i < bytes.length; i += 1) out += ('0' + bytes[i].toString(16)).slice(-2);
    return out;
  }
  function toLogin() { location.replace(LOGIN); }
  function api(method, path, body) {
    var headers = {};
    var options = { method: method, credentials: 'same-origin', headers: headers };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['X-Skillsync-CSRF'] = state.csrf || '';
      options.body = JSON.stringify(body);
    }
    return fetch(API + path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { status: response.status, data: data || {} };
      });
    });
  }
  function describe(result) {
    var status = result.status;
    var code = result.data.error;
    if (status === 401) { toLogin(); return 'Sesja wygasła. Zaloguj się ponownie.'; }
    if (status === 403 && code === 'password_change_required') return 'Najpierw zmień hasło domyślne.';
    if (status === 403 && (code === 'invalid_csrf' || code === 'invalid_origin' || code === 'invalid_content_type')) {
      refreshSession();
      return 'Sesja się zmieniła. Odśwież stronę i spróbuj ponownie.';
    }
    if (status === 403) return 'Brak uprawnień: ta akcja wymaga roli ekspert i pytania skierowanego do Ciebie.';
    if (status === 404) return 'Nie znaleziono pytania (mogło zostać wycofane albo nie jest do Ciebie).';
    if (status === 409 && code === 'migration_required') return 'To pytanie jest w starym pliku, tylko do odczytu. Programista musi je przenieść (skillsync prototype question migrate).';
    if (status === 409 && code === 'already_answered') return 'Ktoś już odpowiedział na to pytanie. Widok został odświeżony.';
    if (status === 409 && code === 'comment_limit') return 'Pytanie osiągnęło limit komentarzy.';
    if (status === 409) return 'Konflikt zapisu (' + String(code || status) + '). Odśwież widok.';
    if (status === 413) return 'Treść jest za duża.';
    if (status === 507) return 'Plik pytań jest pełny, zapis niemożliwy. Skontaktuj się z programistą.';
    if (status === 503) return 'Serwer jest chwilowo zajęty. Spróbuj ponownie za chwilę.';
    if (status === 429) return 'Zbyt wiele zapisów, spróbuj ponownie za ' + (result.data.retryAfter || 60) + ' s.';
    if (status === 400) return 'Serwer odrzucił dane (' + String(code || 'błąd żądania') + ').';
    return 'Nie udało się wykonać akcji (HTTP ' + status + ').';
  }
  function refreshSession() {
    try {
      fetch(API + '/session', { credentials: 'same-origin' }).then(function (response) {
        return response.ok ? response.json() : null;
      }).then(function (data) {
        if (data && data.user && data.csrf) state.csrf = data.csrf; else if (!data || !data.user) toLogin();
      }).catch(function () {});
    } catch (error) { /* offline: the user retries after a reload */ }
  }
  var UNEXPECTED = 'Nieoczekiwana odpowiedź serwera.';
  function validQuestion(question) {
    return !!question && typeof question === 'object' && typeof question.id === 'string' && typeof question.text === 'string';
  }
  var OFFLINE = 'Brak połączenia z serwerem (offline), spróbuj ponownie.';

  function badges(question) {
    var items = [];
    if (question.slug) items.push(el('span', { 'class': 'badge', text: 'Przebieg: ' + question.slug }));
    if (question.blocking) items.push(el('span', { 'class': 'badge block', text: 'Blokujące' }));
    items.push(el('span', { 'class': 'badge' + (question.state === 'open' ? '' : ' done'),
                            text: question.state === 'open' ? 'Otwarte' : 'Odpowiedziane' }));
    if (question.assumed !== null && question.assumed !== undefined) {
      items.push(el('span', { 'class': 'badge', text: 'Przyjęto: ' + question.assumed }));
    }
    return items;
  }
  function label(question) {
    var q = (question.origin && question.origin.q) ? question.origin.q : question.id;
    var spec = question.origin && typeof question.origin.spec === 'string' ? question.origin.spec.split('/').pop() : '';
    return spec ? spec + ' · ' + q : q;
  }

  function runsNav() {
    var box = el('div', { id: 'runs' });
    var ul = el('ul');
    var all = el('a', { href: BASE, text: 'Wszystkie przebiegi' });
    if (!SLUG) all.setAttribute('aria-current', 'true');
    ul.appendChild(el('li', {}, [all]));
    state.runs.forEach(function (run) {
      var link = el('a', { href: BASE + '/' + encodeURIComponent(run.slug), text: run.slug + ' (otwarte: ' + run.open + ', razem: ' + run.total + ')' });
      if (SLUG === run.slug) link.setAttribute('aria-current', 'true');
      ul.appendChild(el('li', {}, [link]));
    });
    box.appendChild(el('h2', { text: 'Przebiegi' }));
    box.appendChild(ul);
    if (state.legacy && state.legacy.count) {
      box.appendChild(el('p', { 'class': 'muted', id: 'legacy-note',
        text: 'Pytania w starym pliku (' + state.legacy.count + ') są tylko do odczytu. Programista musi je przenieść poleceniem skillsync prototype question migrate.' }));
    }
    return box;
  }

  function renderList() {
    clear(listBox);
    listBox.appendChild(runsNav());
    if (!state.questions.length) {
      listBox.appendChild(el('p', { 'class': 'muted', text: SLUG ? 'Brak pytań do Ciebie w tym przebiegu.' : 'Brak pytań do Ciebie.' }));
      return;
    }
    listBox.appendChild(el('h2', { text: SLUG ? 'Pytania: ' + SLUG : 'Pytania' }));
    var ul = el('ul');
    state.questions.forEach(function (question) {
      var link = el('a', { href: '#' + encodeURIComponent(question.id) }, [
        el('strong', { text: label(question) + ': ' }),
        el('span', { text: question.text.length > 90 ? question.text.slice(0, 90) + '…' : question.text }),
        el('br')
      ].concat(badges(question)));
      if (state.current === question.id) link.setAttribute('aria-current', 'true');
      ul.appendChild(el('li', {}, [link]));
    });
    listBox.appendChild(ul);
  }

  function refNode(ref) {
    var box = el('div', { 'class': 'entry' });
    var title = el('strong', { text: ref.title || '' });
    var href = typeof ref.href === 'string' ? ref.href : '';
    var url = null;
    try { url = new URL(href); } catch (error) { url = null; }
    box.appendChild(title);
    if (url && url.protocol === 'https:' && !url.username && !url.password) {
      box.appendChild(document.createTextNode(' '));
      box.appendChild(el('a', { href: url.href, target: '_blank', rel: 'noopener noreferrer', text: url.href }));
    } else if (href) {
      box.appendChild(document.createTextNode(' '));
      box.appendChild(el('span', { 'class': 'muted', text: '(' + href + ')' }));
    }
    if (ref.excerpt) box.appendChild(el('blockquote', { text: ref.excerpt }));
    return box;
  }

  function answerSummary(question) {
    var answer = question.answer;
    var value;
    if (answer.confirm) value = 'Potwierdzono przyjętą wartość: ' + (question.assumed === null ? '' : question.assumed);
    else if (answer.optionId) {
      value = answer.optionLabel || answer.optionId;
      (question.options || []).forEach(function (option) { if (option.id === answer.optionId) value = option.label; });
    } else if (answer.optionIds && answer.optionIds.length) {
      value = (question.options || []).filter(function (option) { return answer.optionIds.indexOf(option.id) !== -1; })
        .map(function (option) { return option.label; }).join(', ') || answer.optionIds.join(', ');
    } else value = answer.text || '';
    var box = el('div', { 'class': 'entry' }, [
      el('div', { 'class': 'meta', text: 'Odpowiedź: ' + (answer.name || answer.by) + ', ' + when(answer.at) }),
      el('div', { 'class': 'qtext', text: value })
    ]);
    if (answer.comment) box.appendChild(el('div', { 'class': 'qtext muted', text: 'Komentarz: ' + answer.comment }));
    return box;
  }

  function checkbox(id, value, text) {
    var input = el('input', { type: 'checkbox', name: 'multi', id: id, value: value });
    return el('div', { 'class': 'opt' }, [input, el('label', { 'for': id, text: text })]);
  }

  function radio(id, value, text, extra) {
    var input = el('input', { type: 'radio', name: 'choice', id: id, value: value });
    return el('div', { 'class': 'opt' }, [input, el('label', { 'for': id, text: text }), extra]);
  }

  function renderDetail(question, keepFocus) {
    clear(detail);
    var heading = el('h2', { id: 'detail-title', tabindex: '-1', text: label(question) });
    detail.appendChild(heading);
    badges(question).forEach(function (badge) { detail.appendChild(badge); });
    detail.appendChild(el('p', { 'class': 'qtext', text: question.text }));
    if (question.refs && question.refs.length) {
      detail.appendChild(el('h3', { text: 'Źródła' }));
      question.refs.forEach(function (ref) { detail.appendChild(refNode(ref)); });
    }
    if (question.answer) {
      detail.appendChild(el('h3', { text: 'Odpowiedź' }));
      detail.appendChild(answerSummary(question));
      detail.appendChild(el('p', { 'class': 'muted', text: 'Pytanie jest zamknięte. Można dodawać tylko komentarze.' }));
    } else if (question.canAnswer) {
      detail.appendChild(answerForm(question));
    } else {
      detail.appendChild(el('p', { 'class': 'muted', text: 'Nie możesz odpowiedzieć na to pytanie (rola ekspert i adresowanie do Ciebie są wymagane). Możesz je skomentować.' }));
    }
    detail.appendChild(el('h3', { text: 'Komentarze' }));
    var comments = el('div', { id: 'comments' });
    if (!(question.comments || []).length) comments.appendChild(el('p', { 'class': 'muted', text: 'Brak komentarzy.' }));
    (question.comments || []).forEach(function (comment) {
      comments.appendChild(el('div', { 'class': 'entry' }, [
        el('div', { 'class': 'meta', text: (comment.name || comment.by) + ', ' + when(comment.at) + (comment.late ? ' (po odpowiedzi)' : '') }),
        el('div', { 'class': 'qtext', text: comment.text })
      ]));
    });
    detail.appendChild(comments);
    detail.appendChild(commentForm(question));
    if (!keepFocus) heading.focus();
    syncBusy();
  }

  function answerForm(question) {
    var form = el('form', { id: 'answer-form', novalidate: 'novalidate' });
    var fieldset = el('fieldset');
    fieldset.appendChild(el('legend', { text: 'Twoja odpowiedź' }));
    if (question.multi) {
      fieldset.setAttribute('aria-describedby', 'multi-hint');
      fieldset.appendChild(el('p', { 'class': 'muted', id: 'multi-hint', text: 'Zaznacz co najmniej jeden wariant albo wpisz własną odpowiedź.' }));
    }
    (question.options || []).forEach(function (option, index) {
      var text = option.label + (option.recommended ? ' (Rekomendacja agenta)' : '');
      fieldset.appendChild(question.multi ? checkbox('opt-' + index, option.id, text) : radio('opt-' + index, 'o:' + option.id, text));
    });
    if (question.assumed !== null && question.assumed !== undefined) {
      fieldset.appendChild(radio('opt-confirm', 'confirm', 'Potwierdzam przyjętą wartość: ' + question.assumed));
    }
    fieldset.appendChild(radio('opt-custom', 'custom', 'Własna odpowiedź'));
    fieldset.appendChild(el('label', { 'class': 'block', 'for': 'custom-text', text: 'Treść własnej odpowiedzi' }));
    fieldset.appendChild(el('textarea', { id: 'custom-text', maxlength: '4000' }));
    form.appendChild(fieldset);
    form.appendChild(el('label', { 'class': 'block', 'for': 'answer-comment', text: 'Komentarz do odpowiedzi (opcjonalny)' }));
    form.appendChild(el('textarea', { id: 'answer-comment', maxlength: '2000' }));
    form.appendChild(el('button', { type: 'submit', id: 'answer-send', text: 'Wyślij odpowiedź' }));
    function clearBoxes() {
      Array.prototype.forEach.call(form.querySelectorAll('input[name="multi"]'), function (box) { box.checked = false; });
    }
    form.querySelector('#custom-text').addEventListener('input', function () {
      form.querySelector('#opt-custom').checked = true;
      clearBoxes();
    });
    if (question.multi) {
      // The set of options, the confirmation and the own answer exclude one another.
      Array.prototype.forEach.call(form.querySelectorAll('input[name="multi"]'), function (box) {
        box.addEventListener('change', function () {
          if (box.checked) Array.prototype.forEach.call(form.querySelectorAll('input[name="choice"]'), function (radio) { radio.checked = false; });
        });
      });
      Array.prototype.forEach.call(form.querySelectorAll('input[name="choice"]'), function (radio) {
        radio.addEventListener('change', clearBoxes);
      });
    }
    form.addEventListener('submit', function (event) { event.preventDefault(); submitAnswer(question, form); });
    return form;
  }

  function commentForm(question) {
    var form = el('form', { id: 'comment-form', novalidate: 'novalidate' });
    form.appendChild(el('label', { 'class': 'block', 'for': 'comment-text', text: 'Dodaj komentarz' }));
    form.appendChild(el('textarea', { id: 'comment-text', maxlength: '2000' }));
    form.appendChild(el('button', { type: 'submit', id: 'comment-send', text: 'Wyślij komentarz' }));
    form.addEventListener('submit', function (event) { event.preventDefault(); submitComment(question, form); });
    return form;
  }

  // The busy flag is per question: a slow save of A must not block or disable B.
  function syncBusy() {
    var on = !!(state.current && state.busy[state.current]);
    Array.prototype.forEach.call(detail.querySelectorAll('button'), function (button) {
      if (on) button.setAttribute('disabled', 'disabled'); else button.removeAttribute('disabled');
    });
    detail.setAttribute('aria-busy', on ? 'true' : 'false');
  }
  function lock(id, on) {
    if (on) state.busy[id] = true; else delete state.busy[id];
    syncBusy();
  }

  // One cid per distinct action; the same cid for a retry of the very same request.
  function cidFor(kind, question, payload) {
    var key = kind + '|' + question.id + '|' + JSON.stringify(payload);
    if (!state.pending[key]) { state.pending = {}; state.pending[key] = newCid(); }
    return { key: key, cid: state.pending[key] };
  }

  function send(kind, question, payload) {
    var id = question.id;
    if (state.busy[id]) { say('Trwa zapis poprzedniej odpowiedzi.'); return; }
    fail(''); say('');
    var ticket = cidFor(kind, question, payload);
    var body = { cid: ticket.cid };
    Object.keys(payload).forEach(function (name) { body[name] = payload[name]; });
    lock(id, true);
    api('POST', '/questions/' + encodeURIComponent(id) + '/' + kind, body).then(function (result) {
      lock(id, false);
      // The user may have moved to another question while this request was in flight.
      var viewing = state.current === id;
      if (result.status === 200 || result.status === 201) {
        var updated = result.data.question;
        if (!validQuestion(updated) || updated.id !== id) { fail(UNEXPECTED); return; }
        state.pending = {};
        replaceQuestion(updated);
        renderList();
        var done = kind === 'answer' ? 'Odpowiedź zapisana.' : 'Komentarz zapisany.';
        if (viewing) { renderDetail(updated, false); say(done); }
        else say(done + ' (' + label(updated) + ')');
        return;
      }
      var message = describe(result);
      if (result.status !== 429 && result.status !== 503) state.pending = {};
      fail(viewing ? message : label(question) + ': ' + message);
      if (result.status === 409 || result.status === 404) { reloadAll(); }
      else if (viewing) document.getElementById('alert').focus();
    }).catch(function () {
      lock(id, false);
      fail(OFFLINE);
      if (state.current === id) document.getElementById('alert').focus();
    });
  }

  function submitAnswer(question, form) {
    var chosen = form.querySelector('input[name="choice"]:checked');
    var comment = form.querySelector('#answer-comment').value.trim();
    var payload = {};
    var boxes = Array.prototype.filter.call(form.querySelectorAll('input[name="multi"]'), function (box) { return box.checked; });
    if (question.multi && boxes.length) {
      payload.optionIds = boxes.map(function (box) { return box.value; });
      if (comment) payload.comment = comment;
      send('answer', question, payload);
      return;
    }
    if (!chosen && question.multi) { fail('Zaznacz co najmniej jeden wariant albo wpisz własną odpowiedź.'); form.querySelector('input[name="multi"]').focus(); return; }
    if (!chosen) { fail('Wybierz odpowiedź albo wpisz własną.'); form.querySelector('input[name="choice"]').focus(); return; }
    if (chosen.value === 'confirm') payload.confirm = true;
    else if (chosen.value === 'custom') {
      var text = form.querySelector('#custom-text').value.trim();
      if (!text) { fail('Wpisz treść własnej odpowiedzi.'); form.querySelector('#custom-text').focus(); return; }
      payload.text = text;
    } else payload.optionId = chosen.value.slice(2);
    if (comment) payload.comment = comment;
    send('answer', question, payload);
  }

  function submitComment(question, form) {
    var text = form.querySelector('#comment-text').value.trim();
    if (!text) { fail('Wpisz treść komentarza.'); form.querySelector('#comment-text').focus(); return; }
    send('comment', question, { text: text });
  }

  function replaceQuestion(question) {
    var found = false;
    state.questions = state.questions.map(function (item) {
      if (item.id === question.id) { found = true; return question; }
      return item;
    });
    if (!found) state.questions.push(question);
  }

  function currentId() {
    var raw;
    try { raw = decodeURIComponent((location.hash || '').replace(/^#/, '')); } catch (error) { return null; }
    return /^qn-[0-9a-f]{6,64}$/.test(raw) ? raw : null;
  }

  function showCurrent(event) {
    var id = currentId();
    var changed = id !== state.current;
    if (changed) { fail(''); say(''); }
    state.current = id;
    renderList();
    clear(detail);
    if (!id) {
      detail.appendChild(el('p', { 'class': 'muted', text: 'Wybierz pytanie z listy.' }));
      return Promise.resolve();
    }
    return api('GET', '/questions/' + encodeURIComponent(id)).then(function (result) {
      if (state.current !== id) return;
      if (result.status === 200 && validQuestion(result.data.question) && result.data.question.id === id) {
        replaceQuestion(result.data.question);
        renderList();
        // Focus moves to the heading only on a user-driven selection, never on an automatic refresh.
        renderDetail(result.data.question, !(event && event.type === 'hashchange' && changed));
        if (event && event.type === 'hashchange' && changed) say('Wybrano pytanie ' + label(result.data.question) + '.');
        return;
      }
      detail.appendChild(el('p', { 'class': 'muted', text: 'Nie można wyświetlić pytania.' }));
      fail(result.status === 200 ? UNEXPECTED : describe(result));
    }).catch(function () {
      detail.appendChild(el('p', { 'class': 'muted', text: 'Nie można wyświetlić pytania.' }));
      fail(OFFLINE);
    });
  }

  function loadList() {
    return api('GET', SLUG ? '/prototypes/' + encodeURIComponent(SLUG) + '/questions' : '/questions').then(function (result) {
      if (result.status === 200 && Array.isArray(result.data.questions)) {
        state.questions = result.data.questions;
        state.runs = Array.isArray(result.data.runs) ? result.data.runs : [];
        state.legacy = result.data.legacy || null;
        return true;
      }
      fail(result.status === 200 ? UNEXPECTED : (result.status === 404 && SLUG ? 'Nie ma przebiegu „' + SLUG + '”.' : describe(result)));
      if (result.status === 403 && result.data.error === 'password_change_required') showMustChange();
      return false;
    }).catch(function () { fail(OFFLINE); return false; });
  }

  function reloadAll() {
    return loadList().then(function (ok) {
      if (!ok) return;
      var message = alertBox.textContent;
      // The location (the user's choice of question) is never changed here.
      showCurrent().then(function () { if (message) fail(message); });
    });
  }

  function showMustChange() {
    clear(listBox);
    clear(detail);
    var box = el('div', { 'class': 'notice' }, [
      el('p', { text: 'Konto ma hasło domyślne. Zmień hasło, żeby odpowiadać na pytania. ' }),
      el('a', { href: LOGIN, text: 'Zmień hasło' })
    ]);
    detail.appendChild(box);
  }

  function start() {
    fetch(API + '/session', { credentials: 'same-origin' }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (data) {
      if (!data || !data.user) { toLogin(); return; }
      state.csrf = data.csrf;
      whoBox.textContent = 'Zalogowano jako ' + (data.user.name || data.user.login);
      if (data.user.mustChange) { showMustChange(); return; }
      return loadList().then(function (ok) {
        if (!ok) return;
        window.addEventListener('hashchange', showCurrent);
        return showCurrent();
      });
    }).catch(function () { fail(OFFLINE); });
  }
  start();
})();
"""


def _csp_hash(text):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii')


EXPERTS_PAGE = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pytania do eksperta</title>
<style>%s</style>
</head>
<body>
<header>
<h1>Pytania do eksperta</h1>
<p class="who" id="who"></p>
</header>
<div class="layout">
<nav aria-label="Lista pytań" id="list"><p class="muted">Ładowanie…</p></nav>
<section class="detail" aria-label="Pytanie">
<div class="alert" id="alert" role="alert" tabindex="-1"></div>
<div class="status" id="status" role="status" aria-live="polite"></div>
<div id="detail"></div>
</section>
</div>
<script>%s</script>
</body>
</html>
""" % (EXPERTS_STYLE, EXPERTS_SCRIPT)

EXPERTS_CSP = ("default-src 'none'; script-src %s; style-src %s; connect-src 'self'; base-uri 'none'; "
               "form-action 'none'; frame-ancestors 'none'") % (_csp_hash(EXPERTS_SCRIPT), _csp_hash(EXPERTS_STYLE))


# ── CLI ──────────────────────────────────────────────────────────────────────

def _emit(out, value):
    out.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _read_stdin_secret(stdin):
    line = stdin.readline()
    if line.endswith('\n'):
        line = line[:-1]
    if line.endswith('\r'):
        line = line[:-1]
    if not line:
        raise UserError('empty_password', 'no password on standard input')
    return line


def _prompt_password():
    first = getpass.getpass('Hasło: ')
    second = getpass.getpass('Powtórz hasło: ')
    if first != second:
        raise UserError('password_mismatch', 'passwords differ')
    return first


def _password_args(args, stdin):
    """`(password, default_password)` from the mutually exclusive flags (never from argv)."""
    password = default = None
    if getattr(args, 'password_prompt', False):
        password = _prompt_password()
    elif getattr(args, 'password_stdin', False):
        password = _read_stdin_secret(stdin)
    elif getattr(args, 'default_password_stdin', False):
        default = _read_stdin_secret(stdin)
    return password, default


def _users_for_cli(path, iterations, must_exist):
    users = UsersFile(path, iterations=iterations)
    warnings = []
    if users.exists():
        problem = users.permissions_problem()
        if problem:
            raise UserError('insecure_permissions', problem)
        if os.name == 'nt':
            warnings.append(PRIVATE_CHECK_SKIPPED)
    elif must_exist:
        raise UserError('users_file_missing', 'users file does not exist: %s' % users.path)
    return users, warnings


def run_user_command(args, stdin=None, out=None, err=None, iterations=DEFAULT_ITERATIONS):
    """`user add|passwd|remove|list|import`; returns the exit code (0 PASS, 2 BLOCKED)."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    err = err or sys.stderr
    action = args.user_action
    try:
        users, warnings = _users_for_cli(args.users, iterations, must_exist=action in ('passwd', 'remove', 'list', 'roles'))
        if action == 'add':
            password, default = _password_args(args, stdin)
            record, extra = add_user(users, args.login, roles=args.role or ['commenter'], password=password, default_password=default,
                                     name=args.name)
            result = {'status': 'PASS', 'action': 'add', 'user': record}
        elif action == 'roles':
            record, extra = set_roles(users, args.login, args.role or [])
            result = {'status': 'PASS', 'action': 'roles', 'user': record}
        elif action == 'passwd':
            password, default = _password_args(args, stdin)
            record, extra = set_password(users, args.login, password=password, default_password=default)
            result = {'status': 'PASS', 'action': 'passwd', 'user': record}
        elif action == 'remove':
            record, extra = remove_user(users, args.login), []
            result = {'status': 'PASS', 'action': 'remove', 'user': record}
        elif action == 'list':
            extra = []
            result = {'status': 'PASS', 'action': 'list', 'users': list_users(users)}
        else:
            try:
                with open(args.file, encoding='utf-8-sig') as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError) as error:
                raise UserError('import_file_unreadable', 'cannot read %s (%s)' % (args.file, error.__class__.__name__))
            logins, invalid = parse_import_lines(text)
            if invalid:
                _emit(out, {'status': 'BLOCKED', 'action': 'import', 'error': 'invalid_login', 'invalid': invalid})
                return 2
            _password, default = _password_args(args, stdin)
            outcome, extra = import_users(users, logins, roles=args.role or ['commenter'], default_password=default)
            result = dict({'status': 'PASS', 'action': 'import'}, **outcome)
        for warning in warnings + extra:
            err.write('ostrzeżenie: %s\n' % warning)
        _emit(out, result)
        return 0
    except UserError as error:
        _emit(out, {'status': 'BLOCKED', 'action': action, 'error': error.code, 'message': error.message})
        return 2
    except LockTimeout:
        _emit(out, {'status': 'BLOCKED', 'action': action, 'error': 'lock_timeout'})
        return 2


def build_parser():
    parser = argparse.ArgumentParser(prog='prototype_server.py', description='Shared comments server for prototypes')
    commands = parser.add_subparsers(dest='command')
    commands.required = True
    serve_parser = commands.add_parser('serve', help='run the server in the foreground')
    serve_parser.add_argument('--root', required=True, help='directory with prototypes')
    serve_parser.add_argument('--users', required=True, help='users JSON file (0600, outside root and git)')
    serve_parser.add_argument('--host', default=DEFAULT_HOST)
    serve_parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    serve_parser.add_argument('--secure-cookies', action='store_true')
    serve_parser.add_argument('--trust-proxy', action='store_true')
    serve_parser.add_argument('--login-to-view', action='store_true')
    serve_parser.add_argument('--no-source-writes', action='store_true')
    user_parser = commands.add_parser('user', help='manage the users file')
    actions = user_parser.add_subparsers(dest='user_action')
    actions.required = True

    def password_group(target, prompt=True):
        group = target.add_mutually_exclusive_group()
        if prompt:
            group.add_argument('--password-prompt', action='store_true')
            group.add_argument('--password-stdin', action='store_true')
        group.add_argument('--default-password-stdin', action='store_true')

    add = actions.add_parser('add')
    add.add_argument('login')
    add.add_argument('--users', required=True)
    add.add_argument('--name')
    add.add_argument('--role', action='append', choices=ROLES, default=None)
    password_group(add)
    passwd = actions.add_parser('passwd')
    passwd.add_argument('login')
    passwd.add_argument('--users', required=True)
    password_group(passwd)
    roles_parser = actions.add_parser('roles')
    roles_parser.add_argument('login')
    roles_parser.add_argument('--users', required=True)
    roles_parser.add_argument('--role', action='append', choices=ROLES, default=None)
    remove = actions.add_parser('remove')
    remove.add_argument('login')
    remove.add_argument('--users', required=True)
    listing = actions.add_parser('list')
    listing.add_argument('--users', required=True)
    importing = actions.add_parser('import')
    importing.add_argument('file')
    importing.add_argument('--users', required=True)
    importing.add_argument('--role', action='append', choices=ROLES, default=None)
    password_group(importing, prompt=False)
    return parser


def _ensure_utf8_streams():
    """Kopia `fsutil.ensure_utf8_streams` (ten plik jest samodzielny, bez importów z pakietu): stdout/stderr w cp1252
    (rura, zadanie bez konsoli, Windows) nie mogą przewrócić polskich komunikatów (`UnicodeEncodeError`)."""
    for stream, errors in ((sys.stdout, 'replace'), (sys.stderr, 'backslashreplace')):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None or (getattr(stream, 'encoding', None) or '').lower().replace('-', '').replace('_', '') == 'utf8':
            continue
        try:
            reconfigure(encoding='utf-8', errors=errors)
        except (OSError, ValueError):
            pass


def main(argv=None):
    _ensure_utf8_streams()
    args = build_parser().parse_args(argv)
    if args.command == 'serve':
        source_writes = not args.no_source_writes
        if source_writes and not (os.path.isdir(args.root) and in_git_worktree(args.root)):
            source_writes = False
            sys.stderr.write('ostrzeżenie: %s nie leży w kopii roboczej gita: przywracanie z UI wyłączone '
                             '(source writes off)\n' % args.root)
        config = ServerConfig(args.root, args.users, host=args.host, port=args.port,
                              secure_cookies=args.secure_cookies, trust_proxy=args.trust_proxy,
                              login_to_view=args.login_to_view, source_writes=source_writes)
        return serve(config)
    return run_user_command(args)


if __name__ == '__main__':
    sys.exit(main())
