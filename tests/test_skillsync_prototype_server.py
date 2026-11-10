"""Serwer komentarzy prototypów (`skillsync_workflow/prototype_server.py`), część serwerowa kroku 1.6 planu W1.

Kontrakt: docs/specs/2026-11-04-prototype-komentarze-wspolne.md (K1-K4, K6-K14, K16 po stronie serwera, K34,
K41, K42, „Kontrakty”, „Dane”, „Bezpieczeństwo”). Testy opisują zachowanie z speca; nie sprawdzają szczegółów
implementacji. Serwer zawsze startuje na porcie 0 i jest sprzątany w `tearDown`. Zegar i liczba iteracji PBKDF2 są
wstrzykiwane (`ServerConfig(clock=…, pbkdf2_iterations=…)`); tylko jeden test używa pełnych 600 000 iteracji.
Każdy test negatywny bezpieczeństwa ma przypadek kontrolny (ta sama ścieżka z poprawnym wejściem przechodzi).
"""
import ast
import base64
import contextlib
import hashlib
import http.client
import io
import itertools
import json
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype_server as ps  # noqa: E402

# ── Strażnik katalogu tymczasowego (ostatni test W1) ───────────────────────────
# Moduły server, accept i restore działają w prywatnym katalogu nadrzędnym P (`tempfile.tempdir` i `TMPDIR`, więc dziedziczą go też
# podprocesy CLI i serve). Po testach w P mogą być tylko katalogi (tempdiry testów i modułów); każdy inny wpis (plik, dowiązanie)
# to coś, co powstało poza katalogami testu, i kończy moduł błędem z listą wpisów. Cudze sesje we wspólnym /tmp nie wchodzą do P.
_GUARD_STACK = []


def guard_begin():
    parent = tempfile.mkdtemp(prefix='skillsync-guard-')
    state = {'parent': parent, 'tempdir': tempfile.tempdir, 'TMPDIR': os.environ.get('TMPDIR')}
    tempfile.tempdir = parent
    os.environ['TMPDIR'] = parent
    return state


def guard_end(state):
    """Przywraca `tempfile.tempdir` i `TMPDIR`, usuwa P i zgłasza AssertionError z listą wpisów, które nie są katalogami."""
    parent = state['parent']
    try:
        stray = sorted(name for name in os.listdir(parent)
                       if os.path.islink(os.path.join(parent, name)) or not os.path.isdir(os.path.join(parent, name)))
    finally:
        tempfile.tempdir = state['tempdir']
        if state['TMPDIR'] is None:
            os.environ.pop('TMPDIR', None)
        else:
            os.environ['TMPDIR'] = state['TMPDIR']
        shutil.rmtree(parent, ignore_errors=True)
    if stray:
        raise AssertionError('w katalogu tymczasowym testów powstały wpisy inne niż katalogi tempfile: %s' % ', '.join(stray))


def setUpModule():
    _GUARD_STACK.append(guard_begin())


def tearDownModule():
    guard_end(_GUARD_STACK.pop())


MODULE = Path(ps.__file__).resolve()
POSIX = os.name != 'nt'
ITER = 1000  # iteracje PBKDF2 w testach (wstrzyknięte); pełne 600 000 tylko w FullIterationsTest
BASE_EPOCH = datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc).timestamp()

A = 'anna.kowalska@example.com'      # komentujący
B = 'anna.nowak@example.com'         # komentujący (kolizja imienia z A)
C = 'celina.akceptujaca@example.com'  # akceptujący
D = 'dorota.dev@example.com'        # programista
MUST = 'marek.nowy@example.com'      # komentujący z hasłem domyślnym (mustChange)
PASSWORDS = {A: 'Haslo-Anny-1234', B: 'Haslo-Nowak-1234', C: 'Haslo-Celiny-1234', D: 'Haslo-Doroty-1234'}
ROLES = {A: 'commenter', B: 'commenter', C: 'acceptor', D: 'developer'}
DEFAULT_PW = 'Domyslne-Haslo-9876'
SECRET_NEW_PW = 'Zupelnie-Nowe-Haslo-55'

_COUNTER = itertools.count(1)


def uid(prefix='x'):
    return '%s-%d' % (prefix, next(_COUNTER))


def symlinks_supported():
    path = tempfile.mkdtemp(prefix='proto-symlink-')
    try:
        os.symlink(path, os.path.join(path, 'link'))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        shutil.rmtree(path, ignore_errors=True)


needs_symlinks = unittest.skipUnless(symlinks_supported(), 'brak dowiązań symbolicznych w tym środowisku')
needs_posix = unittest.skipUnless(POSIX, 'tryby plików (0600/0644) mają sens tylko na POSIX')


class FakeClock(object):
    """Zegar w sekundach od epoki (kontrakt `time.time`), ustawiany przez test."""

    def __init__(self, value=BASE_EPOCH):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


# ── świat testowy: katalog prototypów, plik użytkowników ─────────────────────

def write(path, text, mode=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
    if mode is not None:
        os.chmod(str(path), mode)


def build_world(tmp, clock):
    """`root` z prototypami `demo` (bez comments.json), `old` (v1), plik użytkowników poza `root`."""
    tmp = Path(tmp)
    root = tmp / 'root'
    write(root / 'demo' / 'prototype.json', '{"title": "Demo"}\n')
    write(root / 'demo' / 'index.html', '<!doctype html><html data-prototype-id="demo"><body>DEMO-INDEX</body></html>\n')
    write(root / 'demo' / 'prototype.js', '/* engine */\n')
    write(root / 'demo' / 'nolist' / 'plik.txt', 'bez indeksu\n')
    write(root / 'old' / 'prototype.json', '{"title": "Old"}\n')
    write(root / 'old' / 'index.html', '<html data-prototype-id="old"><body>OLD</body></html>\n')
    write(root / 'old' / 'comments.js', 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = [];\n')
    write(root / 'nojson' / 'index.html', '<html></html>\n')
    write(tmp / 'outside' / 'secret.txt', 'TOP-SECRET-OUTSIDE\n')
    users_path = tmp / 'private' / 'users.json'
    users = ps.UsersFile(str(users_path), iterations=ITER, clock=clock)
    for login in (A, B, C, D):
        ps.add_user(users, login, role=ROLES[login], password=PASSWORDS[login])
    ps.add_user(users, MUST, role='commenter', default_password=DEFAULT_PW)
    return str(root), str(users_path)


# ── klient HTTP ──────────────────────────────────────────────────────────────

class Resp(object):
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self):
        return self.body.decode('utf-8', 'replace')

    @property
    def json(self):
        return json.loads(self.body.decode('utf-8')) if self.body else None

    def header(self, name):
        return self.headers.get(name)

    def cookies(self):
        return self.headers.get_all('Set-Cookie') or []


class Client(object):
    def __init__(self, server, ip=None, timeout=15):
        self.host = '127.0.0.1'
        self.port = server.port
        self.ip = ip
        self.timeout = timeout
        self.cookie = None
        self.csrf = None
        self.login_name = None

    def request(self, method, path, data=None, headers=None, raw=None):
        """`data` = obiekt JSON (POST), `raw` = bajty; wartość `None` w `headers` usuwa nagłówek."""
        merged = {}
        if self.cookie:
            merged['Cookie'] = self.cookie
        if self.ip:
            merged['X-Forwarded-For'] = self.ip
        body = None
        if raw is not None:
            body = raw
        elif data is not None:
            body = json.dumps(data).encode('utf-8')
        if method == 'POST':
            merged['Content-Type'] = 'application/json'
            if self.csrf:
                merged['X-Skillsync-CSRF'] = self.csrf
        for key, value in (headers or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            try:
                conn.request(method, path, body=body, headers=merged)
            except (BrokenPipeError, ConnectionResetError):
                pass  # serwer odpowiedział i zamknął połączenie przed końcem wysyłania ciała
            response = conn.getresponse()
            return Resp(response.status, response.msg, response.read())
        finally:
            conn.close()

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, data=None, **kwargs):
        return self.request('POST', path, data=data, **kwargs)

    def login(self, login, password, **kwargs):
        response = self.post('/_api/v1/login', {'login': login, 'password': password}, **kwargs)
        if response.status == 200:
            for cookie in response.cookies():
                name, _, rest = cookie.partition('=')
                self.cookie = '%s=%s' % (name, rest.split(';', 1)[0])
            self.csrf = response.json['csrf']
            self.login_name = response.json['user']['login']
        return response

    def op(self, slug, kind, thread=None, payload=None, cid=None, **extra):
        body = {'cid': cid or uid('c'), 'type': kind}
        if thread is not None:
            body['threadId'] = thread
        body['payload'] = {} if payload is None else payload
        body.update(extra)
        return self.post('/_api/v1/prototypes/%s/operations' % slug, body)

    def operations(self, slug, query=''):
        return self.get('/_api/v1/prototypes/%s/operations%s' % (slug, query))


def anchor(kind='screen', iteration=1):
    if kind == 'element':
        return {'kind': 'element', 'protoId': 's1-button-1', 'x': 0.4, 'y': 0.6, 'iteration': iteration}
    return {'kind': 'screen', 'x': 0.5, 'y': 0.25, 'iteration': iteration}


def create_payload(mid, text='Komentarz', screen='s1', anchor_value=None):
    return {'message': {'id': mid, 'text': text}, 'screen': screen, 'anchor': anchor_value or anchor()}


# ── czytanie i kontrola pliku dziennika ──────────────────────────────────────

def read_file_bytes(directory):
    path = os.path.join(directory, 'comments.json')
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as handle:
        return handle.read()


def file_operations(directory):
    data = read_file_bytes(directory)
    return json.loads(data.decode('utf-8'))['operations']


def assert_canonical_text(case, data):
    """Plik: UTF-8 bez BOM, nagłówek w 1. linii, jedna operacja na linię, zwarta, `]}` + `\\n`."""
    case.assertFalse(data.startswith(b'\xef\xbb\xbf'))
    text = data.decode('utf-8')
    case.assertTrue(text.endswith(']}\n'), repr(text[-20:]))
    lines = text[:-1].split('\n')
    document = json.loads(text)
    operations = document['operations']
    case.assertEqual(len(lines), len(operations) + 2)
    case.assertEqual(json.loads(lines[0] + ']}')['operations'], [])
    case.assertEqual(lines[-1], ']}')
    key_order = ['id', 'type', 'threadId', 'at', 'by', 'cid', 'payload']
    for index, operation in enumerate(operations):
        line = lines[1 + index]
        expected_tail = ',' if index < len(operations) - 1 else ''
        ordered = dict((key, operation[key]) for key in key_order if key in operation)
        case.assertEqual(list(operation), list(ordered), 'kolejność kluczy operacji')
        compact = json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)
        case.assertEqual(line, compact + expected_tail)


def op_lines(data):
    """Linie operacji bez końcowego przecinka (do porównań prefiksu bajt w bajt)."""
    lines = data.decode('utf-8')[:-1].split('\n')[1:-1]
    return [line[:-1] if line.endswith(',') else line for line in lines]


def raw_id(number):
    return 'op-' + str(number).zfill(26)


def raw_at(number):
    return '2026-11-05T09:00:%02d.000Z' % number


def raw_create(number, by=A):
    return {'id': raw_id(number), 'type': 'create', 'threadId': 'th-ext-%d' % number, 'at': raw_at(number), 'by': by,
            'cid': 'c-ext-%d' % number,
            'payload': create_payload('m-ext-%d' % number, 'zewnętrzny %d' % number)}


def write_journal(directory, slug, operations):
    write(os.path.join(directory, 'comments.json'), ps.serialize_document(slug, operations))


# ── baza testów serwera ──────────────────────────────────────────────────────

class ServerCase(unittest.TestCase):
    login_to_view = False
    secure_cookies = False
    trust_proxy = False
    source_writes = True
    fake_clock = True
    max_file_bytes = ps.MAX_FILE_BYTES
    lock_timeout = 5.0

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-server-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = FakeClock()
        self.root, self.users_path = build_world(self.tmp, self.clock)
        self.demo = os.path.join(self.root, 'demo')
        self._servers = []
        self.start_server()

    def tearDown(self):
        for server, thread in reversed(self._servers):
            server.shutdown()
            server.server_close()
            thread.join(10)
        self._servers = []

    def config(self, **overrides):
        values = dict(host='127.0.0.1', port=0, clock=self.clock if self.fake_clock else None,
                      pbkdf2_iterations=ITER, log=None, git_checks=False, login_to_view=self.login_to_view,
                      secure_cookies=self.secure_cookies, trust_proxy=self.trust_proxy,
                      source_writes=self.source_writes, max_file_bytes=self.max_file_bytes,
                      lock_timeout=self.lock_timeout)
        values.update(overrides)
        return ps.ServerConfig(self.root, self.users_path, **values)

    def start_server(self, **overrides):
        server = ps.make_server(self.config(**overrides))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        self.server = server
        self.anon = Client(server)
        return server

    # pomocnicze

    def client(self, login=None, password=None, ip=None):
        client = Client(self.server, ip=ip)
        if login is not None:
            response = client.login(login, password or PASSWORDS[login])
            self.assertEqual(response.status, 200, response.text)
        return client

    def check(self, response, status, error=None):
        self.assertEqual(response.status, status, response.text)
        if error is not None:
            self.assertEqual(response.json.get('error'), error, response.text)
        return response

    def journal(self):
        return read_file_bytes(self.demo)

    def assert_no_write(self, before, action):
        """Wykonuje `action()` i sprawdza, że `comments.json` nie zmienił się (bajt w bajt)."""
        result = action()
        self.assertEqual(self.journal(), before, 'plik zmieniony mimo odmowy')
        return result

    def seed(self, client, thread, mid, text='Pierwszy wpis'):
        return self.check(client.op('demo', 'create', thread, create_payload(mid, text)), 201)


# ═════════════════════════════════════════════════════════════════════════════
# Zgodność z Pythonem 3.8 i samodzielność modułu
# ═════════════════════════════════════════════════════════════════════════════

FORBIDDEN_ATTRIBUTES = {
    'removeprefix': 'str.removeprefix (3.9)', 'removesuffix': 'str.removesuffix (3.9)',
    'is_relative_to': 'Path.is_relative_to (3.9)', 'with_stem': 'Path.with_stem (3.9)',
    'bit_count': 'int.bit_count (3.10)', 'randbytes': 'random.randbytes (3.9)', 'lcm': 'math.lcm (3.9)',
    'to_thread': 'asyncio.to_thread (3.9)', 'waitstatus_to_exitcode': 'os.waitstatus_to_exitcode (3.9)',
}
FORBIDDEN_MODULES = {'zoneinfo': '3.9', 'graphlib': '3.9', 'tomllib': '3.11', 'yaml': 'PyYAML'}
BUILTIN_GENERICS = {'list', 'dict', 'set', 'tuple', 'frozenset', 'type'}


def scan_forbidden(source):
    """Lista naruszeń zgodności z 3.8 (AST); puste = zgodne."""
    tree = ast.parse(source, feature_version=(3, 8))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                found.append((node.lineno, FORBIDDEN_ATTRIBUTES[node.attr]))
            if node.attr == 'cache' and isinstance(node.value, ast.Name) and node.value.id == 'functools':
                found.append((node.lineno, 'functools.cache (3.9)'))
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or '').split('.')[0]
            if root in FORBIDDEN_MODULES:
                found.append((node.lineno, 'import %s' % root))
            if node.module == 'functools' and any(alias.name == 'cache' for alias in node.names):
                found.append((node.lineno, 'from functools import cache (3.9)'))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in FORBIDDEN_MODULES:
                    found.append((node.lineno, 'import %s' % alias.name))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            for side in (node.left, node.right):
                is_dict_call = (isinstance(side, ast.Call) and isinstance(side.func, ast.Name)
                                and side.func.id == 'dict')
                if isinstance(side, (ast.Dict, ast.DictComp)) or is_dict_call:
                    found.append((node.lineno, 'operator | na dict (3.9)'))
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr) \
                and isinstance(node.value, (ast.Dict, ast.DictComp)):
            found.append((node.lineno, 'operator |= na dict (3.9)'))
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in BUILTIN_GENERICS:
            found.append((node.lineno, '%s[...] (3.9)' % node.value.id))
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == 'strict' and isinstance(node.func, ast.Name) and node.func.id == 'zip':
                    found.append((node.lineno, 'zip(strict=) (3.10)'))
    return found


def forbidden_imports(source):
    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                bad.append((node.lineno, 'import względny'))
            root = (node.module or '').split('.')[0]
            if root in ('skillsync_workflow', 'yaml', 'skillsync'):
                bad.append((node.lineno, root))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in ('skillsync_workflow', 'yaml', 'skillsync'):
                    bad.append((node.lineno, alias.name))
    return bad


class CompatibilityTests(unittest.TestCase):
    source = MODULE.read_text(encoding='utf-8')

    def test_module_parses_as_python_38(self):
        tree = ast.parse(self.source, feature_version=(3, 8))
        self.assertGreater(len(tree.body), 50)

    def test_python_38_parser_rejects_newer_syntax(self):
        """Kontrola: parser z `feature_version=(3, 8)` naprawdę odrzuca składnię 3.10 (test potrafi paść)."""
        with self.assertRaises(SyntaxError):
            ast.parse('match x:\n    case 1:\n        pass\n', feature_version=(3, 8))

    def test_no_forbidden_apis(self):
        self.assertEqual(scan_forbidden(self.source), [])

    def test_scanner_detects_each_forbidden_api(self):
        """Kontrola skanera: każdy zakazany wzorzec w próbce jest wykrywany, czysta próbka przechodzi."""
        samples = [
            "a = 'x'.removeprefix('y')", "a = 'x'.removesuffix('y')", "ok = p.is_relative_to(q)",
            "import functools\n" "@functools.cache\ndef f(): pass", "from functools import cache",
            "import zoneinfo", "from graphlib import TopologicalSorter", "c = {'a': 1} | {'b': 2}",
            "c = dict(a=1) | other", "c = list[int]", "n = (3).bit_count()", "z = zip(a, b, strict=True)",
        ]
        for sample in samples:
            self.assertTrue(scan_forbidden(sample), sample)
        clean = "import functools\n@functools.lru_cache(maxsize=None)\ndef f(x):\n    return 'a'.startswith(x)\nd = {}\nd.update(a=1)\n"
        self.assertEqual(scan_forbidden(clean), [])

    def test_no_imports_from_skillsync_workflow_or_yaml(self):
        self.assertEqual(forbidden_imports(self.source), [])
        self.assertTrue(forbidden_imports('from skillsync_workflow import common'))
        self.assertTrue(forbidden_imports('import yaml'))
        self.assertTrue(forbidden_imports('from . import sibling'))

    def test_module_runs_standalone_without_package(self):
        """Moduł skopiowany sam do pustego katalogu daje się zaimportować i uruchomić (`--help`)."""
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'prototype_server.py')
            shutil.copy(str(MODULE), target)
            env = dict(os.environ, PYTHONPATH='')
            run = subprocess.run([sys.executable, '-I', target, '--help'], cwd=directory, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', 'replace'))
            self.assertIn(b'serve', run.stdout)
            self.assertIn(b'user', run.stdout)


# ═════════════════════════════════════════════════════════════════════════════
# Format i walidacja (K1, K2)
# ═════════════════════════════════════════════════════════════════════════════

VALID_PAYLOADS = {
    'create': create_payload('m-1', 'tekst'),
    'reply': {'message': {'id': 'm-2', 'text': 'odpowiedź'}},
    'edit': {'messageId': 'm-1', 'text': 'nowy tekst'},
    'retract': {'messageId': 'm-1'},
    'vote': {'messageId': 'm-1', 'value': 'up'},
    'resolve': {'reason': 'author'},
    'reopen': {},
    'reanchor': {'screen': 's1', 'anchor': anchor('element')},
    'delete': {},
    'triage': {'class': 'clear'},
    'decision': {'kind': 'agent', 'outcome': 'apply', 'text': 'zrobione'},
    'accept': {'scope': 'screen', 'screens': {'s1': 'a' * 64}, 'iteration': 1, 'via': 'ui'},
    'unaccept': {'screens': ['s1']},
    'iteration': {'number': 1, 'path': 'iterations/1/', 'hash': 'b' * 64},
    'restore': {'screen': 's1', 'from': 1, 'frozen': 2, 'reason': 'bo tak', 'hashBefore': 'a' * 64,
                'hashAfter': 'b' * 64},
}
GLOBAL = ('accept', 'unaccept', 'iteration', 'restore')


def full_operation(kind, number=1):
    operation = {'id': raw_id(number), 'type': kind, 'at': raw_at(number), 'by': A, 'cid': 'c-%d' % number,
                 'payload': json.loads(json.dumps(VALID_PAYLOADS[kind]))}
    if kind not in GLOBAL:
        operation['threadId'] = 'th-1'
    return operation


class OperationValidationTests(unittest.TestCase):
    def test_all_fifteen_types_are_known(self):
        self.assertEqual(set(ps.OPERATION_TYPES), set(VALID_PAYLOADS))
        self.assertEqual(len(ps.OPERATION_TYPES), 15)

    def test_every_type_validates_and_requires_its_fields(self):
        for kind in VALID_PAYLOADS:
            with self.subTest(kind=kind):
                ps.validate_operation(full_operation(kind))  # kontrola: poprawna operacja przechodzi
                for key in VALID_PAYLOADS[kind]:
                    broken = full_operation(kind)
                    del broken['payload'][key]
                    with self.assertRaises(ps.PrototypeServerError) as caught:
                        ps.validate_operation(broken)
                    self.assertEqual(caught.exception.status, 400, '%s bez %s' % (kind, key))

    def test_operation_envelope_is_validated(self):
        ps.validate_operation(full_operation('create'))
        for field in ('id', 'type', 'at', 'by', 'payload', 'threadId'):
            broken = full_operation('create')
            del broken[field]
            with self.subTest(missing=field), self.assertRaises(ps.PrototypeServerError):
                ps.validate_operation(broken)
        for at in ('2026-11-05T09:00:00Z', '2026-11-05 09:00:00.000Z', '2026-13-05T09:00:00.000Z', 5, None):
            broken = full_operation('create')
            broken['at'] = at
            with self.subTest(at=at), self.assertRaises(ps.PrototypeServerError):
                ps.validate_operation(broken)
        unknown = full_operation('create')
        unknown['type'] = 'teleport'
        with self.assertRaises(ps.PrototypeServerError):
            ps.validate_operation(unknown)

    def test_global_types_need_no_thread_id(self):
        for kind in GLOBAL:
            operation = full_operation(kind)
            self.assertNotIn('threadId', operation)
            ps.validate_operation(operation)

    def test_value_domains(self):
        cases = [
            ('vote', {'messageId': 'm-1', 'value': 'meh'}), ('resolve', {'reason': 'whatever'}),
            ('triage', {'class': 'maybe'}), ('decision', {'kind': 'agent', 'outcome': 'shrug', 'text': 'x'}),
            ('accept', {'scope': 'galaxy', 'screens': {'s1': 'a' * 64}, 'iteration': 1, 'via': 'ui'}),
            ('accept', {'scope': 'screen', 'screens': {'s1': 'nie-hash'}, 'iteration': 1, 'via': 'ui'}),
            ('iteration', {'number': 1, 'path': 'iterations/2/', 'hash': 'b' * 64}),
            ('restore', {'screen': 's1', 'from': 1, 'frozen': 2, 'reason': '', 'hashBefore': 'a' * 64,
                         'hashAfter': 'b' * 64}),
        ]
        for kind, payload in cases:
            operation = full_operation(kind)
            operation['payload'] = payload
            with self.subTest(kind=kind, payload=payload), self.assertRaises(ps.PrototypeServerError):
                ps.validate_operation(operation)
        for value in ('up', 'down', 'none'):
            operation = full_operation('vote')
            operation['payload']['value'] = value
            ps.validate_operation(operation)

    def test_anchor_rules(self):
        """`protoId` wymagane przy `element`, zabronione przy `screen`; x, y w 0-1."""
        def create_with(anchor_value):
            operation = full_operation('create')
            operation['payload']['anchor'] = anchor_value
            return operation
        ps.validate_operation(create_with(anchor('element')))
        ps.validate_operation(create_with(anchor('screen')))
        element_without = anchor('element')
        del element_without['protoId']
        screen_with = anchor('screen')
        screen_with['protoId'] = 's1-button-1'
        out_of_range = anchor('screen')
        out_of_range['x'] = 1.5
        for bad in (element_without, screen_with, out_of_range, {'kind': 'nowhere'}):
            with self.subTest(anchor=bad), self.assertRaises(ps.PrototypeServerError):
                ps.validate_operation(create_with(bad))

    def test_text_length_limit_is_4000(self):
        for length, accepted in ((4000, True), (4001, False)):
            payload = create_payload('m-1', 'a' * length)
            if accepted:
                ps.validate_payload('create', payload, 'browser')
            else:
                with self.assertRaises(ps.PrototypeServerError):
                    ps.validate_payload('create', payload, 'browser')

    def test_browser_origin_rejects_lost_anchor_and_foreign_resolve_reason(self):
        lost = create_payload('m-1', 'x', anchor_value={'kind': 'lost'})
        ps.validate_payload('create', lost, 'import')  # migracja może
        with self.assertRaises(ps.PrototypeServerError):
            ps.validate_payload('create', lost, 'browser')
        ps.validate_payload('resolve', {'reason': 'author'}, 'browser')
        with self.assertRaises(ps.PrototypeServerError):
            ps.validate_payload('resolve', {'reason': 'done'}, 'browser')
        ps.validate_payload('resolve', {'reason': 'done'}, 'file')  # CLI agenta może


class SerializationTests(unittest.TestCase):
    def test_canonical_line_is_compact_with_fixed_key_order(self):
        scrambled = {'payload': create_payload('m-1', 'zażółć gęślą jaźń 😀'), 'cid': 'c-1', 'by': A,
                     'at': raw_at(1), 'threadId': 'th-1', 'type': 'create', 'id': raw_id(1)}
        line = ps.serialize_operation(scrambled)
        expected = json.dumps({key: scrambled[key] for key in ('id', 'type', 'threadId', 'at', 'by', 'cid', 'payload')},
                              separators=(',', ':'), ensure_ascii=False)
        self.assertEqual(line, expected)
        self.assertNotIn('\\u', line)
        self.assertNotIn('\n', line)
        self.assertIn('zażółć gęślą jaźń 😀', line)

    def test_document_layout_header_one_operation_per_line_trailer(self):
        operations = [raw_create(1), raw_create(2), raw_create(3)]
        text = ps.serialize_document('demo', operations)
        data = text.encode('utf-8')
        assert_canonical_text(self, data)
        lines = text.split('\n')
        self.assertEqual(len(lines), 6)  # nagłówek, 3 operacje, ]}, pusty ogon po \n
        self.assertEqual(lines[-2:], [']}', ''])
        header = json.loads(lines[0] + ']}')
        self.assertEqual(header['format'], 'skillsync-prototype-comments')
        self.assertEqual(header['version'], 2)
        self.assertEqual(header['prototypeId'], 'demo')
        self.assertEqual(list(header)[-1], 'operations')

    def test_empty_document(self):
        text = ps.serialize_document('demo', [])
        assert_canonical_text(self, text.encode('utf-8'))
        self.assertEqual(json.loads(text)['operations'], [])
        self.assertEqual(text, ps.empty_document('demo'))

    def test_roundtrip_is_byte_stable(self):
        text = ps.serialize_document('demo', [raw_create(1), raw_create(2)])
        document = ps.parse_document(text, 'demo')
        self.assertEqual(ps.serialize_document('demo', document.operations), text)

    def test_parse_rejects_wrong_files(self):
        good = ps.serialize_document('demo', [])
        ps.parse_document(good, 'demo')
        wrong = [
            good.replace('skillsync-prototype-comments', 'something-else'), good.replace('"version":2', '"version":1'),
            good.replace('"demo"', '"inny"'), '[]', 'not json', good.replace('"operations":[', '"operations":{'),
        ]
        for text in wrong:
            with self.subTest(text=text[:60]), self.assertRaises(ps.PrototypeServerError):
                ps.parse_document(text, 'demo')

    def test_prefix_fingerprint_depends_on_ids_and_order(self):
        ops = [raw_create(1), raw_create(2), raw_create(3)]
        value = ps.prefix_fingerprint(ops)
        self.assertRegex(value, r'^[0-9a-f]{16}$')
        self.assertEqual(ps.prefix_fingerprint(ops), value)
        self.assertEqual(ps.prefix_fingerprint(ops, 2), ps.prefix_fingerprint(ops[:2]))
        self.assertNotEqual(ps.prefix_fingerprint(ops, 2), value)
        self.assertNotEqual(ps.prefix_fingerprint([ops[1], ops[0], ops[2]]), value)
        self.assertNotEqual(ps.prefix_fingerprint([ops[0], ops[1], raw_create(4)]), value)


class PeopleLabelTests(unittest.TestCase):
    """K41: imię, pełne imię i nazwisko, `label` przy kolizjach."""

    def test_names_from_address_and_name_field(self):
        people = ps.compute_labels(['henryk.testowy@example.com', 'jan.kos@example.com'],
                                   {'jan.kos@example.com': 'Janusz Kosiński'})
        self.assertEqual(people['henryk.testowy@example.com'],
                         {'firstName': 'Henryk', 'fullName': 'Henryk Testowy', 'label': 'Henryk'})
        self.assertEqual(people['jan.kos@example.com'],
                         {'firstName': 'Janusz', 'fullName': 'Janusz Kosiński', 'label': 'Janusz'})

    def test_first_name_collision_adds_surname_initial(self):
        people = ps.compute_labels([A, B])
        self.assertEqual(people[A]['label'], 'Anna K.')
        self.assertEqual(people[B]['label'], 'Anna N.')

    def test_initial_collision_falls_back_to_full_name(self):
        people = ps.compute_labels(['anna.kowalska@example.com', 'anna.kozlowska@example.com', 'jan.nowak@example.com'])
        self.assertEqual(people['anna.kowalska@example.com']['label'], 'Anna Kowalska')
        self.assertEqual(people['anna.kozlowska@example.com']['label'], 'Anna Kozlowska')
        self.assertEqual(people['jan.nowak@example.com']['label'], 'Jan')

    def test_compute_people_has_no_roles(self):
        people = ps.compute_people([A, B])
        self.assertEqual(set(people[A]), {'firstName', 'fullName', 'label'})


# ═════════════════════════════════════════════════════════════════════════════
# Zapis, atomowość, blokady, limity (K1-K4, K34)
# ═════════════════════════════════════════════════════════════════════════════

WRITER_SCRIPT = r'''
import sys, importlib.util
spec = importlib.util.spec_from_file_location("ps_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
store = mod.CommentsStore(sys.argv[2], "demo")
tag = sys.argv[3]
for i in range(int(sys.argv[4])):
    store.append([{"type": "create", "threadId": "th-%s-%d" % (tag, i), "by": "agent", "cid": "c-%s-%d" % (tag, i),
                   "payload": {"message": {"id": "m-%s-%d" % (tag, i), "text": "agent %s %d" % (tag, i)},
                               "screen": "s1", "anchor": {"kind": "screen", "x": 0.1, "y": 0.2, "iteration": 1}}}])
'''

CRASH_SCRIPT = r'''
import os, sys, importlib.util
spec = importlib.util.spec_from_file_location("ps_under_test", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.os.replace = lambda *args, **kwargs: os._exit(9)   # proces ginie po zapisie pliku tymczasowego
store = mod.CommentsStore(sys.argv[2], "demo")
store.append([{"type": "create", "threadId": "th-crash", "by": "agent", "cid": "c-crash",
               "payload": {"message": {"id": "m-crash", "text": "nie dojdzie"}, "screen": "s1",
                           "anchor": {"kind": "screen", "x": 0.1, "y": 0.2, "iteration": 1}}}])
'''


def run_script(script, *args):
    return subprocess.run([sys.executable, '-c', script, str(MODULE)] + [str(arg) for arg in args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-store-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.directory = os.path.join(self.tmp, 'demo')
        write(os.path.join(self.directory, 'prototype.json'), '{}\n')
        self.clock = FakeClock()
        self.store = ps.CommentsStore(self.directory, 'demo', clock=self.clock)

    def partial(self, number, by='agent'):
        return {'type': 'create', 'threadId': 'th-s-%d' % number, 'by': by, 'cid': 'c-s-%d' % number,
                'payload': create_payload('m-s-%d' % number, 'wpis %d' % number)}


class AtomicWriteTests(StoreCase):
    def test_interrupted_write_before_replace_keeps_old_file(self):
        self.store.append([self.partial(1)])
        before = read_file_bytes(self.directory)
        with mock.patch.object(ps.os, 'replace', side_effect=OSError('przerwanie przed os.replace')):
            with self.assertRaises(OSError):
                self.store.append([self.partial(2)])
        self.assertEqual(read_file_bytes(self.directory), before)
        assert_canonical_text(self, read_file_bytes(self.directory))
        leftovers = [name for name in os.listdir(self.directory) if name.endswith('.tmp')]
        self.assertEqual(leftovers, [])
        # kontrola: bez przerwania ten sam zapis przechodzi i zmienia plik
        self.store.append([self.partial(2)])
        self.assertNotEqual(read_file_bytes(self.directory), before)
        self.assertEqual(len(file_operations(self.directory)), 2)

    def test_process_killed_between_tmp_write_and_replace(self):
        self.store.append([self.partial(1)])
        before = read_file_bytes(self.directory)
        run = run_script(CRASH_SCRIPT, self.directory)
        self.assertEqual(run.returncode, 9, run.stderr.decode('utf-8', 'replace'))
        self.assertEqual(read_file_bytes(self.directory), before)
        assert_canonical_text(self, read_file_bytes(self.directory))
        # kontrola: po awarii kolejny zapis (nowy proces) działa i dopisuje do starego pliku
        fresh = ps.CommentsStore(self.directory, 'demo', clock=self.clock)
        fresh.append([self.partial(2)])
        self.assertEqual(len(file_operations(self.directory)), 2)

    def test_write_failure_does_not_lose_old_operations_in_cache(self):
        self.store.append([self.partial(1)])
        with mock.patch.object(ps.os, 'replace', side_effect=OSError('boom')):
            with self.assertRaises(OSError):
                self.store.append([self.partial(2)])
        self.assertEqual(self.store.snapshot().total, 1)

    @needs_posix
    def test_existing_file_mode_is_preserved(self):
        self.store.append([self.partial(1)])
        path = os.path.join(self.directory, 'comments.json')
        for mode in (0o644, 0o664, 0o640):
            os.chmod(path, mode)
            self.store.append([self.partial(10 + mode)])
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), mode)

    @needs_posix
    def test_new_file_has_default_0644(self):
        umask = os.umask(0)
        os.umask(umask)
        self.store.append([self.partial(1)])
        mode = stat.S_IMODE(os.stat(os.path.join(self.directory, 'comments.json')).st_mode)
        self.assertEqual(mode, 0o644 & ~umask)


class MonotonicClockTests(StoreCase):
    def test_at_and_id_grow_when_clock_goes_back(self):
        self.store.append([self.partial(1)])
        first = file_operations(self.directory)[0]
        self.clock.advance(-3600)
        self.store.append([self.partial(2)])
        second = file_operations(self.directory)[1]
        self.assertEqual(ps.parse_at(second['at']), ps.parse_at(first['at']) + 1)
        self.assertGreater(second['at'], first['at'])
        self.assertGreater(second['id'], first['id'])

    def test_at_follows_clock_when_it_is_ahead(self):
        self.store.append([self.partial(1)])
        self.clock.advance(60)
        self.store.append([self.partial(2)])
        first, second = file_operations(self.directory)
        self.assertEqual(ps.parse_at(second['at']) - ps.parse_at(first['at']), 60000)

    def test_batch_in_one_millisecond_gets_strictly_increasing_values(self):
        self.store.append([self.partial(number) for number in range(1, 6)])
        operations = file_operations(self.directory)
        self.assertEqual([op['at'] for op in operations], sorted(set(op['at'] for op in operations)))
        self.assertEqual([op['id'] for op in operations], sorted(set(op['id'] for op in operations)))
        for operation in operations:
            self.assertRegex(operation['id'], r'^op-[0-9A-HJKMNP-TV-Z]{26}$')
            self.assertRegex(operation['at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

    def test_clock_far_behind_existing_future_operations(self):
        write_journal(self.directory, 'demo', [raw_create(30)])
        self.store.append([self.partial(1)])
        operations = file_operations(self.directory)
        self.assertGreater(operations[1]['at'], operations[0]['at'])
        self.assertGreater(operations[1]['id'], operations[0]['id'])

    def test_id_time_part_encodes_at(self):
        """Pierwsze 10 znaków identyfikatora to czas `at` w ms (alfabet Crockford)."""
        self.store.append([self.partial(1)])
        operation = file_operations(self.directory)[0]
        alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
        value = 0
        for char in operation['id'][3:13]:
            value = value * 32 + alphabet.index(char)
        self.assertEqual(value, ps.parse_at(operation['at']))


class ConcurrencyTests(unittest.TestCase):
    """K3: 20 wątków HTTP i 2 procesy zapisujące bez utraty i duplikatów."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-conc-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = FakeClock()
        self.root, self.users_path = build_world(self.tmp, self.clock)
        self.demo = os.path.join(self.root, 'demo')
        config = ps.ServerConfig(self.root, self.users_path, port=0, pbkdf2_iterations=ITER, log=None, git_checks=False)
        self.server = ps.make_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def test_20_http_threads_and_2_processes_lose_nothing(self):
        owner = Client(self.server)
        self.assertEqual(owner.login(A, PASSWORDS[A]).status, 200)
        processes = [subprocess.Popen([sys.executable, '-c', WRITER_SCRIPT, str(MODULE), self.demo, tag, '10'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for tag in ('p1', 'p2')]
        results = {}
        barrier = threading.Barrier(20)

        def worker(index):
            client = Client(self.server)
            client.cookie, client.csrf = owner.cookie, owner.csrf
            barrier.wait()
            results[index] = client.op('demo', 'create', 'th-http-%d' % index,
                                       create_payload('m-http-%d' % index, 'wątek %d' % index), cid='c-http-%d' % index)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        for process in processes:
            out, err = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, err.decode('utf-8', 'replace'))
        self.assertEqual(sorted(r.status for r in results.values()), [201] * 20,
                         [r.text for r in results.values() if r.status != 201])
        data = read_file_bytes(self.demo)
        assert_canonical_text(self, data)
        operations = json.loads(data.decode('utf-8'))['operations']
        self.assertEqual(len(operations), 40)
        for key in ('id', 'cid', 'threadId'):
            values = [operation[key] for operation in operations]
            self.assertEqual(len(set(values)), len(values), 'duplikaty w %s' % key)
        self.assertEqual({op['threadId'] for op in operations},
                         {'th-http-%d' % i for i in range(20)} | {'th-%s-%d' % (t, i) for t in ('p1', 'p2') for i in range(10)})
        self.assertEqual([op['id'] for op in operations], sorted(op['id'] for op in operations))
        self.assertEqual([op['at'] for op in operations], sorted(set(op['at'] for op in operations)))
        fold = ps.fold_operations(operations)
        self.assertEqual(sorted(t['number'] for t in fold['threads']), list(range(1, 41)))


# ═════════════════════════════════════════════════════════════════════════════
# Użytkownicy i CLI modułu (K10, K41, K42)
# ═════════════════════════════════════════════════════════════════════════════

class UsersCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-users-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.users_path = os.path.join(self.tmp, 'private', 'users.json')
        self.clock = FakeClock()

    def users_file(self):
        return ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)

    def cli(self, argv, stdin='', iterations=ITER):
        """Uruchomienie CLI modułu w procesie (niskie iteracje). Zwraca (kod, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                args = ps.build_parser().parse_args(argv)
        except SystemExit as exit_:
            return exit_.code, '', 'argparse'
        code = ps.run_user_command(args, stdin=io.StringIO(stdin), out=out, err=err, iterations=iterations)
        return code, out.getvalue(), err.getvalue()

    def cli_json(self, argv, stdin='', expect=0):
        code, out, err = self.cli(argv, stdin)
        self.assertEqual(code, expect, out + err)
        return json.loads(out), err

    def stored(self):
        with open(self.users_path, encoding='utf-8') as handle:
            return json.load(handle)


class PasswordHashTests(unittest.TestCase):
    def test_pbkdf2_format_and_independent_verification(self):
        encoded = ps.hash_password('Sekret-12345', iterations=ITER)
        parts = encoded.split('$')
        self.assertEqual(parts[0], 'pbkdf2_sha256')
        self.assertEqual(int(parts[1]), ITER)
        salt, digest = base64.b64decode(parts[2]), base64.b64decode(parts[3])
        self.assertEqual(len(salt), 16)
        self.assertEqual(len(digest), 32)
        self.assertEqual(digest, hashlib.pbkdf2_hmac('sha256', 'Sekret-12345'.encode('utf-8'), salt, ITER))

    def test_verify_accepts_right_and_rejects_wrong_password(self):
        encoded = ps.hash_password('Sekret-12345', iterations=ITER)
        self.assertTrue(ps.verify_password('Sekret-12345', encoded))
        self.assertFalse(ps.verify_password('Sekret-12346', encoded))
        self.assertFalse(ps.verify_password('', encoded))

    def test_salt_is_random(self):
        self.assertNotEqual(ps.hash_password('x' * 12, ITER), ps.hash_password('x' * 12, ITER))

    def test_malformed_hashes_never_verify(self):
        good = ps.hash_password('Sekret-12345', ITER)
        self.assertTrue(ps.verify_password('Sekret-12345', good))
        broken = ['', 'plain', 'md5$1$a$b', good.replace('pbkdf2_sha256', 'pbkdf2_sha1'),
                  good.rsplit('$', 1)[0], good.replace('$%d$' % ITER, '$abc$'), good + '$extra', None, 5]
        for value in broken:
            with self.subTest(value=value):
                self.assertFalse(ps.verify_password('Sekret-12345', value))

    def test_default_iteration_constant_is_600000_and_salt_16(self):
        self.assertEqual(ps.DEFAULT_ITERATIONS, 600000)
        self.assertEqual(ps.MIN_PASSWORD, 10)


class UsersFileCliTests(UsersCase):
    def test_add_normalizes_email_and_stores_record(self):
        out, _ = self.cli_json(['user', 'add', '  Jan.Kos@Example.COM ', '--users', self.users_path,
                                '--password-stdin', '--name', 'Jan Kos'], stdin='Dobre-Haslo-123\n')
        self.assertEqual(out['user']['login'], 'jan.kos@example.com')
        record = self.stored()['users'][0]
        self.assertEqual(self.stored()['version'], 1)
        self.assertEqual(record['login'], 'jan.kos@example.com')
        self.assertEqual(record['role'], 'commenter')
        self.assertEqual(record['name'], 'Jan Kos')
        self.assertFalse(record['mustChange'])
        for key in ('password', 'created', 'passwordChanged'):
            self.assertIn(key, record)
        self.assertTrue(ps.verify_password('Dobre-Haslo-123', record['password']))

    def test_invalid_and_reserved_logins_are_blocked(self):
        for login in ('bez-malpy', 'a@b', 'spacje w@example.com', 'agent', 'import', 'server', '@example.com'):
            with self.subTest(login=login):
                code, out, err = self.cli(['user', 'add', login, '--users', self.users_path, '--password-stdin'],
                                          stdin='Dobre-Haslo-123\n')
                self.assertEqual(code, 2, out)
                self.assertEqual(json.loads(out)['status'], 'BLOCKED')
        self.assertFalse(os.path.exists(self.users_path))
        out, _ = self.cli_json(['user', 'add', 'ok@example.com', '--users', self.users_path, '--password-stdin'],
                               stdin='Dobre-Haslo-123\n')  # kontrola
        self.assertEqual(out['status'], 'PASS')

    def test_password_shorter_than_10_is_blocked_10_is_accepted(self):
        code, out, _ = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                                stdin='dziewiec-9\n'[:9] + '\n')
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(self.users_path))
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='1234567890\n')

    def test_default_password_sets_must_change_and_is_never_printed(self):
        code, out, err = self.cli(['user', 'add', 'nowy@example.com', '--users', self.users_path,
                                   '--default-password-stdin'], stdin=DEFAULT_PW + '\n')
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.stored()['users'][0]['mustChange'])
        self.assertTrue(ps.verify_password(DEFAULT_PW, self.stored()['users'][0]['password']))
        self.assertNotIn(DEFAULT_PW, out + err)
        self.assertIn('nowy@example.com', out)  # kontrola: sprawdzamy realne wyjście, nie puste
        # kontrola detektora: ten sam warunek faktycznie pada, gdy sekret jest w wyjściu
        with self.assertRaises(AssertionError):
            self.assertNotIn(DEFAULT_PW, out + 'wyciek: ' + DEFAULT_PW)

    def test_default_password_never_in_argv_listing_or_any_command_output(self):
        outputs = []
        for argv, stdin in [
            (['user', 'add', 'a@example.com', '--users', self.users_path, '--default-password-stdin'], DEFAULT_PW + '\n'),
            (['user', 'add', 'b@example.com', '--users', self.users_path, '--role', 'developer',
              '--default-password-stdin'], DEFAULT_PW + '\n'),
            (['user', 'passwd', 'a@example.com', '--users', self.users_path, '--default-password-stdin'], DEFAULT_PW + '\n'),
            (['user', 'list', '--users', self.users_path], ''),
        ]:
            code, out, err = self.cli(argv, stdin)
            self.assertEqual(code, 0, out + err)
            outputs.append(out + err)
        self.assertTrue(all(outputs))
        for text in outputs:
            self.assertNotIn(DEFAULT_PW, text)
            self.assertNotIn('pbkdf2_sha256', text)

    def test_add_without_any_password_source_is_blocked(self):
        code, out, _ = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.users_path))
        code, _out, _err = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path, '--default-password-stdin'],
                                    stdin=DEFAULT_PW + '\n')  # kontrola
        self.assertEqual(code, 0)

    def test_privileged_roles_get_warning_about_default_password(self):
        stderr = {}
        for role in ('commenter', 'acceptor', 'developer'):
            code, out, err = self.cli(['user', 'add', '%s@example.com' % role, '--users', self.users_path, '--role', role,
                                       '--default-password-stdin'], stdin=DEFAULT_PW + '\n')
            self.assertEqual(code, 0)
            stderr[role] = err
        if POSIX:
            self.assertEqual(stderr['commenter'].strip(), '')
        self.assertIn('acceptor@example.com', stderr['acceptor'])
        self.assertIn('developer@example.com', stderr['developer'])
        self.assertNotIn(DEFAULT_PW, ''.join(stderr.values()))

    def test_duplicate_user_is_blocked(self):
        argv = ['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin']
        self.cli_json(argv, stdin='Dobre-Haslo-123\n')
        code, out, _ = self.cli(argv, stdin='Inne-Haslo-12345\n')
        self.assertEqual(code, 2, out)
        self.assertEqual(len(self.stored()['users']), 1)
        self.assertTrue(ps.verify_password('Dobre-Haslo-123', self.stored()['users'][0]['password']))

    def test_passwd_resets_to_default_with_must_change_or_sets_given_password(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        self.assertFalse(self.stored()['users'][0]['mustChange'])
        self.cli_json(['user', 'passwd', 'a@example.com', '--users', self.users_path, '--default-password-stdin'],
                      stdin=DEFAULT_PW + '\n')
        record = self.stored()['users'][0]
        self.assertTrue(record['mustChange'])
        self.assertTrue(ps.verify_password(DEFAULT_PW, record['password']))
        self.assertFalse(ps.verify_password('Dobre-Haslo-123', record['password']))
        self.cli_json(['user', 'passwd', 'A@Example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Zupelnie-Nowe-1\n')
        record = self.stored()['users'][0]
        self.assertFalse(record['mustChange'])
        self.assertTrue(ps.verify_password('Zupelnie-Nowe-1', record['password']))

    def test_passwd_and_remove_for_unknown_user_or_missing_file_are_blocked(self):
        for action in ('passwd', 'remove'):
            code, _out, _err = self.cli(['user', action, 'a@example.com', '--users', self.users_path,
                                         '--default-password-stdin'] if action == 'passwd'
                                        else ['user', action, 'a@example.com', '--users', self.users_path],
                                        stdin=DEFAULT_PW + '\n')
            self.assertEqual(code, 2, action)  # brak pliku
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        code, _out, _err = self.cli(['user', 'remove', 'nikt@example.com', '--users', self.users_path])
        self.assertEqual(code, 2)
        code, _out, _err = self.cli(['user', 'passwd', 'nikt@example.com', '--users', self.users_path,
                                     '--default-password-stdin'], stdin=DEFAULT_PW + '\n')
        self.assertEqual(code, 2)
        self.cli_json(['user', 'remove', 'a@example.com', '--users', self.users_path])  # kontrola
        self.assertEqual(self.stored()['users'], [])

    def test_list_shows_must_change_and_never_hashes(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        self.cli_json(['user', 'add', 'b@example.com', '--users', self.users_path, '--default-password-stdin'],
                      stdin=DEFAULT_PW + '\n')
        self.assertIn('pbkdf2_sha256', Path(self.users_path).read_text())  # kontrola: skróty są w pliku
        code, out, err = self.cli(['user', 'list', '--users', self.users_path])
        self.assertEqual(code, 0)
        listing = json.loads(out)['users']
        self.assertEqual({u['login']: u['mustChange'] for u in listing}, {'a@example.com': False, 'b@example.com': True})
        self.assertNotIn('pbkdf2', out + err)
        self.assertTrue(all('password' not in user for user in listing))

    def test_stdin_flags_are_mutually_exclusive(self):
        combos = [['--password-stdin', '--default-password-stdin'], ['--password-prompt', '--password-stdin'],
                  ['--password-prompt', '--default-password-stdin']]
        for flags in combos:
            with self.subTest(flags=flags):
                code, _out, _err = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path] + flags,
                                            stdin='Dobre-Haslo-123\n')
                self.assertEqual(code, 2)
                self.assertFalse(os.path.exists(self.users_path))
        code, _out, _err = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                                    stdin='Dobre-Haslo-123\n')  # kontrola: pojedyncza flaga działa
        self.assertEqual(code, 0)

    def test_stdin_flags_exclusion_via_real_process(self):
        run = subprocess.run([sys.executable, str(MODULE), 'user', 'add', 'a@example.com', '--users', self.users_path,
                              '--password-stdin', '--default-password-stdin'], input=b'Dobre-Haslo-123\n',
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(os.path.exists(self.users_path))

    def test_password_not_accepted_from_argv(self):
        code, _out, _err = self.cli(['user', 'add', 'a@example.com', '--users', self.users_path, '--password',
                                     'Dobre-Haslo-123'])
        self.assertEqual(code, 2)  # argparse: nieznana opcja
        self.assertFalse(os.path.exists(self.users_path))

    def test_import_creates_accounts_skips_existing_with_must_change(self):
        self.cli_json(['user', 'add', 'istnieje@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        listing = os.path.join(self.tmp, 'lista.txt')
        write(listing, '# lista\nNowy.Jeden@Example.com\n\n  istnieje@example.com  \nnowy.dwa@example.com\nnowy.jeden@example.com\n')
        out, _ = self.cli_json(['user', 'import', listing, '--users', self.users_path, '--default-password-stdin'],
                               stdin=DEFAULT_PW + '\n')
        self.assertEqual(out['added'], ['nowy.jeden@example.com', 'nowy.dwa@example.com'])
        self.assertEqual(out['skipped'], ['istnieje@example.com'])
        by_login = {u['login']: u for u in self.stored()['users']}
        self.assertEqual(by_login['istnieje@example.com']['mustChange'], False)
        for login in out['added']:
            self.assertEqual(by_login[login]['role'], 'commenter')
            self.assertTrue(by_login[login]['mustChange'])
            self.assertTrue(ps.verify_password(DEFAULT_PW, by_login[login]['password']))
        self.assertTrue(ps.verify_password('Dobre-Haslo-123', by_login['istnieje@example.com']['password']))

    def test_import_with_invalid_address_writes_nothing(self):
        listing = os.path.join(self.tmp, 'lista.txt')
        write(listing, 'ok@example.com\nzly adres\n')
        code, out, _ = self.cli(['user', 'import', listing, '--users', self.users_path, '--default-password-stdin'],
                                stdin=DEFAULT_PW + '\n')
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(self.users_path))
        write(listing, 'ok@example.com\n')  # kontrola
        self.cli_json(['user', 'import', listing, '--users', self.users_path, '--default-password-stdin'],
                      stdin=DEFAULT_PW + '\n')

    def test_import_requires_default_password(self):
        listing = os.path.join(self.tmp, 'lista.txt')
        write(listing, 'ok@example.com\n')
        code, _out, _err = self.cli(['user', 'import', listing, '--users', self.users_path])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.users_path))

    @needs_posix
    def test_users_file_and_backup_are_0600(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        self.cli_json(['user', 'add', 'b@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        self.assertEqual(stat.S_IMODE(os.stat(self.users_path).st_mode), 0o600)
        backup = self.users_path + '.bak'
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)

    @needs_posix
    def test_wider_permissions_are_blocked_with_chmod_hint(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        self.cli_json(['user', 'list', '--users', self.users_path])  # kontrola: 0600 przechodzi
        for mode in (0o644, 0o640, 0o660, 0o604):
            os.chmod(self.users_path, mode)
            code, out, _err = self.cli(['user', 'list', '--users', self.users_path])
            self.assertEqual(code, 2, '%o' % mode)
            self.assertIn('chmod 600', out)
            code, _out, _err = self.cli(['user', 'add', 'x@example.com', '--users', self.users_path, '--password-stdin'],
                                        stdin='Dobre-Haslo-123\n')
            self.assertEqual(code, 2, '%o' % mode)
        self.assertEqual(len(self.stored()['users']), 1)

    @needs_posix
    def test_wider_permissions_blocked_in_real_process(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        command = [sys.executable, str(MODULE), 'user', 'list', '--users', self.users_path]
        ok = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(ok.returncode, 0)
        os.chmod(self.users_path, 0o644)
        bad = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(bad.returncode, 2)

    def test_process_list_and_remove_via_module_cli(self):
        self.cli_json(['user', 'add', 'a@example.com', '--users', self.users_path, '--password-stdin'],
                      stdin='Dobre-Haslo-123\n')
        run = subprocess.run([sys.executable, str(MODULE), 'user', 'list', '--users', self.users_path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([u['login'] for u in json.loads(run.stdout.decode())['users']], ['a@example.com'])
        run = subprocess.run([sys.executable, str(MODULE), 'user', 'remove', 'a@example.com', '--users', self.users_path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.stored()['users'], [])
        run = subprocess.run([sys.executable, str(MODULE), 'user', 'remove', 'a@example.com', '--users', self.users_path],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 2)


class FullIterationsTest(UsersCase):
    """Jedyny test z pełnymi 600 000 iteracji: moduł uruchomiony jako samodzielny proces."""

    def test_cli_add_passwd_use_600000_iterations(self):
        def run(*argv, stdin=b''):
            return subprocess.run([sys.executable, '-W', 'ignore', str(MODULE), 'user'] + list(argv),
                                  input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        added = run('add', 'a@example.com', '--users', self.users_path, '--password-stdin', stdin=b'Dobre-Haslo-123\n')
        self.assertEqual(added.returncode, 0, added.stderr)
        encoded = self.stored()['users'][0]['password']
        parts = encoded.split('$')
        self.assertEqual(parts[:2], ['pbkdf2_sha256', '600000'])
        self.assertEqual(len(base64.b64decode(parts[2])), 16)
        self.assertEqual(base64.b64decode(parts[3]),
                         hashlib.pbkdf2_hmac('sha256', b'Dobre-Haslo-123', base64.b64decode(parts[2]), 600000))
        self.assertTrue(ps.verify_password('Dobre-Haslo-123', encoded))
        reset = run('passwd', 'a@example.com', '--users', self.users_path, '--default-password-stdin',
                    stdin=(DEFAULT_PW + '\n').encode())
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertNotIn(DEFAULT_PW.encode(), reset.stdout + reset.stderr)
        record = self.stored()['users'][0]
        self.assertTrue(record['password'].startswith('pbkdf2_sha256$600000$'))
        self.assertTrue(record['mustChange'])
        self.assertTrue(ps.verify_password(DEFAULT_PW, record['password']))
        self.assertFalse(ps.verify_password('Dobre-Haslo-123', record['password']))


# ═════════════════════════════════════════════════════════════════════════════
# HTTP: odczyt publiczny, pliki statyczne, ścieżki (K8, K9)
# ═════════════════════════════════════════════════════════════════════════════

class StaticAndReadTests(ServerCase):
    def test_public_read_without_session(self):
        page = self.anon.get('/demo/index.html')
        self.check(page, 200)
        self.assertIn('DEMO-INDEX', page.text)
        self.assertIn('text/html', page.header('Content-Type'))
        self.check(self.anon.get('/demo/prototype.js'), 200)
        self.check(self.anon.get('/demo/prototype.json'), 200)
        head = self.anon.request('HEAD', '/demo/index.html')
        self.check(head, 200)
        self.assertEqual(head.body, b'')
        self.assertEqual(int(head.header('Content-Length')), len(page.body))

    def test_public_session_endpoint_and_operations_without_cookie(self):
        session = self.check(self.anon.get('/_api/v1/session'), 200).json
        self.assertIsNone(session['user'])
        self.assertIsNone(session['csrf'])
        self.assertEqual(session['server']['loginToView'], False)
        self.assertIn('sourceWrites', session['server'])
        listing = self.check(self.anon.operations('demo'), 200).json
        self.assertEqual(listing['prototypeId'], 'demo')
        self.assertEqual(listing['total'], 0)
        self.assertEqual(listing['operations'], [])
        self.assertEqual(listing['iteration'], {'current': 1})
        self.assertFalse(listing['reset'])

    def test_comments_file_is_publicly_readable_after_write(self):
        author = self.client(A)
        self.seed(author, 'th-1', 'm-1')
        served = self.check(self.anon.get('/demo/comments.json'), 200)
        self.assertEqual(served.body, self.journal())

    def test_directory_redirect_index_and_no_listing(self):
        redirect = self.anon.get('/demo')
        self.check(redirect, 301)
        self.assertEqual(redirect.header('Location'), '/demo/')
        self.assertIn('DEMO-INDEX', self.check(self.anon.get('/demo/'), 200).text)
        self.check(self.anon.get('/demo/nolist/'), 404)   # katalog bez index.html
        self.check(self.anon.get('/demo/nolist'), 301)
        self.check(self.anon.get('/'), 404)               # korzeń bez index.html, bez listingu
        self.check(self.anon.get('/demo/index.html/'), 404)
        self.assertNotIn('plik.txt', self.anon.get('/demo/nolist/').text)

    def test_root_gallery_is_computed_live_when_configured(self):
        calls = []

        def gallery():
            calls.append(1)
            return '<!doctype html><html><body>LIVE-GALLERY %d</body></html>' % len(calls)

        self.start_server(gallery=gallery)
        first = self.check(self.anon.get('/'), 200)
        self.assertIn('LIVE-GALLERY 1', first.text)
        self.assertIn('text/html', first.header('Content-Type'))
        self.assertIn('LIVE-GALLERY 2', self.check(self.anon.get('/index.html'), 200).text)   # liczona przy każdym żądaniu
        self.assertIn('DEMO-INDEX', self.check(self.anon.get('/demo/'), 200).text)         # prototypy bez zmian
        self.assertEqual(self.check(self.anon.request('HEAD', '/'), 200).body, b'')

    def test_root_gallery_falls_back_to_static_file_when_callable_fails(self):
        def broken():
            raise RuntimeError('boom')

        self.start_server(gallery=broken)
        self.check(self.anon.get('/'), 404)   # brak statycznego index.html → jak bez galerii
        write(Path(self.root) / 'index.html', '<html><body>STATIC-GALLERY</body></html>\n')
        self.assertIn('STATIC-GALLERY', self.check(self.anon.get('/'), 200).text)

    def test_other_methods_are_405(self):
        for method in ('PUT', 'DELETE', 'PATCH', 'OPTIONS', 'TRACE'):
            for path in ('/demo/index.html', '/_api/v1/session', '/_api/v1/prototypes/demo/operations'):
                with self.subTest(method=method, path=path):
                    self.check(self.anon.request(method, path), 405)
        self.check(self.anon.get('/demo/index.html'), 200)  # kontrola
        self.check(self.anon.post('/demo/index.html', {}), 405)

    def test_security_headers_on_static_and_api(self):
        for response in (self.anon.get('/demo/index.html'), self.anon.get('/_api/v1/session'),
                         self.anon.get('/demo/nie-ma.html'), self.anon.get('/_login')):
            self.assertEqual(response.header('X-Content-Type-Options'), 'nosniff', response.status)
            self.assertEqual(response.header('Referrer-Policy'), 'same-origin')
            self.assertIn("frame-ancestors 'self'", response.header('Content-Security-Policy'))
        self.assertEqual(self.anon.get('/_api/v1/session').header('Cache-Control'), 'no-store')
        self.assertEqual(self.anon.operations('demo').header('Cache-Control'), 'no-store')
        self.assertEqual(self.anon.get('/demo/index.html').header('Cache-Control'), 'no-cache')

    def test_unknown_prototype_and_bad_slug(self):
        for slug in ('brak', 'nojson', 'Bad_Slug', '..', 'a%2fb', 'UPPER'):
            with self.subTest(slug=slug):
                self.check(self.anon.operations(slug), 404)
        self.check(self.anon.operations('demo'), 200)  # kontrola


class PathSafetyTests(ServerCase):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.demo, '.env'), 'SEKRET=1\n')
        write(os.path.join(self.demo, '.hidden', 'x.txt'), 'ukryty\n')
        write(os.path.join(self.demo, 'draft.tmp'), 'tmp\n')
        write(os.path.join(self.demo, '.comments.json.abcdef.tmp'), 'tmp\n')
        write(os.path.join(self.demo, 'comments.json.lock'), '')
        write(os.path.join(self.demo, 'cokolwiek.lock'), '')
        write(os.path.join(self.demo, 'ok.txt'), 'DOSTEPNY\n')

    def test_forbidden_paths_are_404_and_control_is_served(self):
        self.assertTrue(os.path.exists(self.users_path))  # traversal miałby co ujawnić
        forbidden = [
            '/demo/../../private/users.json', '/../private/users.json', '/demo/%2e%2e/%2e%2e/private/users.json',
            '/demo/%2E%2E/index.html', '/demo/..%2fprototype.json', '/demo/..%5cprototype.json',
            '/%2e%2e/%2e%2e/etc/passwd', '/demo/.env', '/demo/.hidden/x.txt', '/demo/./.env', '/demo/draft.tmp',
            '/demo/.comments.json.abcdef.tmp', '/demo/comments.json.lock', '/demo/cokolwiek.lock',
            '/demo/ok.txt%00', '/.git/config', '/demo/%2e/ok.txt',
        ]
        for path in forbidden:
            with self.subTest(path=path):
                response = self.anon.get(path)
                self.assertEqual(response.status, 404, response.text[:200])
                self.assertNotIn('pbkdf2', response.text)
                self.assertNotIn('SEKRET', response.text)
        control = self.check(self.anon.get('/demo/ok.txt'), 200)
        self.assertEqual(control.text, 'DOSTEPNY\n')

    @needs_symlinks
    def test_symlink_leaving_root_is_404_but_inside_root_is_served(self):
        outside = os.path.join(self.tmp, 'outside', 'secret.txt')
        os.symlink(outside, os.path.join(self.demo, 'wyciek.txt'))
        os.symlink(os.path.join(self.tmp, 'outside'), os.path.join(self.root, 'katalog-na-zewnatrz'))
        os.symlink(os.path.join(self.demo, 'ok.txt'), os.path.join(self.demo, 'alias.txt'))
        os.symlink(self.users_path, os.path.join(self.demo, 'users-alias.json'))
        for path in ('/demo/wyciek.txt', '/katalog-na-zewnatrz/secret.txt', '/demo/users-alias.json'):
            with self.subTest(path=path):
                response = self.anon.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn('TOP-SECRET', response.text)
                self.assertNotIn('pbkdf2', response.text)
        self.assertEqual(os.path.realpath(os.path.join(self.demo, 'wyciek.txt')), os.path.realpath(outside))
        self.check(self.anon.get('/demo/alias.txt'), 200)  # kontrola: realpath wewnątrz root

    def test_users_file_inside_root_is_never_served_and_startup_refuses(self):
        inside = os.path.join(self.root, 'users.json')
        users = ps.UsersFile(inside, iterations=ITER)
        ps.add_user(users, A, password=PASSWORDS[A])
        config = ps.ServerConfig(self.root, inside, port=0, pbkdf2_iterations=ITER, log=None, git_checks=False)
        errors, _warnings = ps.startup_checks(config)
        self.assertTrue(errors, 'plik użytkowników w serwowanym katalogu musi blokować start')
        self.assertEqual(ps.startup_checks(self.config())[0], [])  # kontrola: plik poza root przechodzi
        with self.assertRaises(ps.StartupError):
            ps.make_server(config)
        server = ps.make_server(config, check=False)  # wymuszenie: nawet wtedy plik nie jest serwowany
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self._servers.append((server, thread))
        client = Client(server)
        for path in ('/users.json', '/users.json.bak', '/users.json.lock', '/%75sers.json'):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn('pbkdf2', response.text)
        self.check(client.get('/demo/index.html'), 200)  # kontrola

    @needs_symlinks
    def test_users_file_reached_through_symlink_in_root_is_404(self):
        """Ścieżka, której `realpath` wskazuje plik użytkowników, daje 404 (K9), a realny plik leży poza root."""
        os.symlink(os.path.join(self.tmp, 'private'), os.path.join(self.root, 'alias'))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'alias', 'users.json')))
        for path in ('/alias/users.json', '/alias/users.json.bak', '/alias/'):
            with self.subTest(path=path):
                response = self.anon.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn('pbkdf2', response.text)
        self.check(self.anon.get('/demo/index.html'), 200)  # kontrola


# ═════════════════════════════════════════════════════════════════════════════
# Start serwera: ostrzeżenia i odmowy (K9, K33)
# ═════════════════════════════════════════════════════════════════════════════

class StartupTests(ServerCase):
    def test_public_read_warning_unless_login_to_view(self):
        _errors, warnings = ps.startup_checks(self.config())
        self.assertIn(ps.PUBLIC_READ_WARNING, warnings)
        self.assertIn('widoczne bez logowania', ps.PUBLIC_READ_WARNING)
        _errors, warnings = ps.startup_checks(self.config(login_to_view=True))
        self.assertNotIn(ps.PUBLIC_READ_WARNING, warnings)
        self.assertIn(ps.PUBLIC_READ_WARNING, self.server.app.warnings)

    def test_non_loopback_host_warns_about_http_without_tls(self):
        _e, local = ps.startup_checks(self.config(host='127.0.0.1'))
        _e, remote = ps.startup_checks(self.config(host='192.168.10.5'))
        self.assertFalse([w for w in local if 'TLS' in w])
        self.assertTrue([w for w in remote if 'TLS' in w])

    def test_must_change_privileged_accounts_are_listed(self):
        users = ps.UsersFile(self.users_path, iterations=ITER)
        ps.add_user(users, 'szef@example.com', role='developer', default_password=DEFAULT_PW)
        ps.add_user(users, 'wizytator@example.com', role='acceptor', default_password=DEFAULT_PW)
        _e, warnings = ps.startup_checks(self.config())
        joined = '\n'.join(warnings)
        self.assertIn('szef@example.com', joined)
        self.assertIn('wizytator@example.com', joined)
        self.assertNotIn(MUST, joined)  # komentujący z mustChange nie jest uprzywilejowany
        self.assertNotIn(DEFAULT_PW, joined)

    def test_v1_prototype_warning(self):
        _e, warnings = ps.startup_checks(self.config())
        self.assertTrue([w for w in warnings if 'old' in w and 'migrate' in w], warnings)

    def test_missing_or_broken_users_file_refuses_start(self):
        self.assertEqual(ps.startup_checks(self.config())[0], [])
        os.unlink(self.users_path)
        self.assertTrue(ps.startup_checks(self.config())[0])
        write(self.users_path, '{not json', 0o600)
        self.assertTrue(ps.startup_checks(self.config())[0])

    @needs_posix
    def test_wide_users_file_refuses_start(self):
        self.assertEqual(ps.startup_checks(self.config())[0], [])
        os.chmod(self.users_path, 0o644)
        errors = ps.startup_checks(self.config())[0]
        self.assertTrue([e for e in errors if 'chmod 600' in e], errors)

    def test_serve_returns_2_when_refused_and_creates_no_server(self):
        os.unlink(self.users_path)
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(ps.serve(self.config(), out=out, err=err), 2)
        self.assertIn('błąd', err.getvalue())

    def test_occupied_port_returns_2(self):
        out, err = io.StringIO(), io.StringIO()
        config = self.config(port=self.server.port)
        self.assertEqual(ps.serve(config, out=out, err=err), 2)

    @unittest.skipUnless(shutil.which('git'), 'brak polecenia git')
    def test_git_checks_untracked_unignored_tracked_and_ignored(self):
        project = os.path.join(self.tmp, 'projekt')
        os.makedirs(os.path.join(project, 'root'))
        os.makedirs(os.path.join(project, '.skillsync'))
        users_path = os.path.join(project, '.skillsync', 'users.json')
        shutil.copy(self.users_path, users_path)
        os.chmod(users_path, 0o600)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def git(*args):
            return subprocess.run(['git', '-C', project] + list(args), env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(git('init', '-q').returncode, 0)
        config = ps.ServerConfig(os.path.join(project, 'root'), users_path, port=0, git_checks=True, log=None)
        self.assertTrue(ps.startup_checks(config)[0], 'nieignorowany plik w repozytorium')
        write(os.path.join(project, '.gitignore'), '.skillsync/\n')
        self.assertEqual(ps.startup_checks(config)[0], [], 'kontrola: ignorowany plik przechodzi')
        self.assertEqual(git('add', '-f', '.skillsync/users.json').returncode, 0)
        self.assertTrue(ps.startup_checks(config)[0], 'śledzony plik mimo ignorowania')
        # poza repozytorium: kontrola gita pominięta z ostrzeżeniem
        outside_config = self.config(git_checks=True)
        errors, warnings = ps.startup_checks(outside_config)
        self.assertEqual(errors, [])
        self.assertTrue([w for w in warnings if 'git' in w])


# ═════════════════════════════════════════════════════════════════════════════
# --login-to-view (K8)
# ═════════════════════════════════════════════════════════════════════════════

class LoginToViewTests(ServerCase):
    login_to_view = True

    def test_html_document_redirects_others_401(self):
        for path in ('/demo/', '/demo/index.html'):
            response = self.anon.get(path)
            self.assertEqual(response.status, 302, path)
            self.assertEqual(unquote(response.header('Location')), '/_login?next=' + path)
        for path in ('/demo', '/demo/index.html/x', '/demo/comments.json'):
            with self.subTest(path=path):
                self.check(self.anon.get(path), 401)
        self.check(self.anon.get('/demo/prototype.json'), 401)
        self.check(self.anon.get('/demo/prototype.js'), 401)
        self.check(self.anon.operations('demo'), 401)
        self.check(self.anon.request('HEAD', '/demo/'), 401)
        self.check(self.anon.get('/_api/v1/prototypes/demo/operations?after=0'), 401)

    def test_exceptions_login_page_session_and_login(self):
        self.check(self.anon.get('/_login'), 200)
        session = self.check(self.anon.get('/_api/v1/session'), 200).json
        self.assertIsNone(session['user'])
        self.assertTrue(session['server']['loginToView'])
        self.assertIn('views', session['server']['features'], 'flaga widoków także bez sesji przy --login-to-view')
        self.check(self.anon.post('/_api/v1/login', {'login': A, 'password': 'zle'}), 401)

    def test_after_login_everything_is_readable(self):
        client = self.client(A)
        self.check(client.get('/demo/'), 200)
        self.check(client.get('/demo/prototype.json'), 200)
        self.check(client.operations('demo'), 200)
        client.post('/_api/v1/logout')
        self.check(client.get('/demo/prototype.json'), 401)

    def test_post_without_session_stays_401(self):
        self.check(self.anon.op('demo', 'create', 'th-1', create_payload('m-1')), 401)


class PublicModeContrastTest(ServerCase):
    def test_same_requests_pass_without_login_to_view(self):
        self.check(self.anon.get('/demo/'), 200)
        self.check(self.anon.get('/demo/prototype.json'), 200)


# ═════════════════════════════════════════════════════════════════════════════
# Logowanie, sesje, ciasteczko, CSRF, Origin, limity prób (K11-K13)
# ═════════════════════════════════════════════════════════════════════════════

class LoginTests(ServerCase):
    def test_login_normalizes_login_and_returns_user_and_csrf(self):
        client = Client(self.server)
        response = client.login('  ANNA.Kowalska@EXAMPLE.com ', PASSWORDS[A])
        self.check(response, 200)
        user = response.json['user']
        self.assertEqual(user['login'], A)
        self.assertEqual(user['role'], 'commenter')
        self.assertEqual(user['firstName'], 'Anna')
        self.assertEqual(user['name'], 'Anna Kowalska')
        self.assertFalse(user['mustChange'])
        self.assertTrue(response.json['csrf'])
        self.assertNotIn('password', user)
        self.assertNotIn('pbkdf2', response.text)

    def test_session_endpoint_with_cookie(self):
        client = self.client(D)
        session = self.check(client.get('/_api/v1/session'), 200).json
        self.assertEqual(session['user']['role'], 'developer')
        self.assertEqual(session['csrf'], client.csrf)
        self.assertEqual(set(session['user']), {'login', 'name', 'firstName', 'role', 'mustChange'})

    def test_bad_password_and_unknown_login_are_indistinguishable(self):
        wrong = Client(self.server).login(A, 'zle-haslo-123')
        unknown = Client(self.server).login('nikt@example.com', 'zle-haslo-123')
        self.check(wrong, 401, 'invalid_credentials')
        self.check(unknown, 401, 'invalid_credentials')
        self.assertEqual(wrong.body, unknown.body)
        self.assertEqual(wrong.cookies(), [])
        self.check(Client(self.server).login(A, PASSWORDS[A]), 200)  # kontrola

    def test_unknown_login_still_computes_a_hash(self):
        calls = []
        real = ps.verify_password

        def counting(password, encoded):
            calls.append(encoded)
            return real(password, encoded)
        with mock.patch.object(ps, 'verify_password', counting):
            Client(self.server).login('nikt@example.com', 'zle-haslo-123')
            self.assertEqual(len(calls), 1, 'nieznany login musi kosztować jedno PBKDF2 (skrót-atrapa)')
            Client(self.server).login(A, PASSWORDS[A])
            self.assertEqual(len(calls), 2)

    def test_login_requires_json_content_type_and_matching_origin(self):
        body = {'login': A, 'password': PASSWORDS[A]}
        self.check(Client(self.server).post('/_api/v1/login', body, headers={'Content-Type': 'text/plain'}), 403)
        self.check(Client(self.server).post('/_api/v1/login', body,
                                            headers={'Content-Type': 'application/x-www-form-urlencoded'}), 403)
        origin = 'http://127.0.0.1:%d' % self.server.port
        self.check(Client(self.server).post('/_api/v1/login', body, headers={'Origin': 'https://zly.example'}), 403)
        self.check(Client(self.server).post('/_api/v1/login', body, headers={'Origin': 'http://127.0.0.1:1'}), 403)
        self.check(Client(self.server).post('/_api/v1/login', body, headers={'Origin': origin}), 200)  # kontrola
        self.check(Client(self.server).post('/_api/v1/login', body,
                                            headers={'Content-Type': 'application/json; charset=utf-8'}), 200)
        self.check(Client(self.server).post('/_api/v1/login', body), 200)  # brak Origin jest dozwolony

    def test_login_with_malformed_body(self):
        client = Client(self.server)
        self.check(client.post('/_api/v1/login', raw=b'not json'), 400)
        self.check(client.post('/_api/v1/login', ['lista']), 400)
        self.check(client.post('/_api/v1/login', {'login': A}), 400)

    def test_logout_ends_session(self):
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')
        response = self.check(client.post('/_api/v1/logout'), 204)
        self.assertTrue(any('Max-Age=0' in c for c in response.cookies()))
        self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 401)
        self.assertIsNone(client.get('/_api/v1/session').json['user'])

    def test_cookie_attributes(self):
        response = Client(self.server).login(A, PASSWORDS[A])
        (cookie,) = response.cookies()
        name = 'skillsync_proto_%d' % self.server.port
        self.assertTrue(cookie.startswith(name + '='), cookie)
        self.assertEqual(self.server.app.cookie_name, name)
        attributes = [part.strip() for part in cookie.split(';')[1:]]
        self.assertIn('HttpOnly', attributes)
        self.assertIn('SameSite=Lax', attributes)
        self.assertIn('Path=/', attributes)
        self.assertNotIn('Secure', attributes)

    def test_secure_cookies_flag_adds_secure(self):
        self.tearDown()
        self.start_server(secure_cookies=True)
        (cookie,) = Client(self.server).login(A, PASSWORDS[A]).cookies()
        self.assertIn('Secure', [part.strip() for part in cookie.split(';')[1:]])
        self.assertIn('HttpOnly', cookie)

    def test_two_servers_use_different_cookie_names(self):
        first = self.server
        second = self.start_server()
        self.assertNotEqual(first.port, second.port)
        self.assertNotEqual(first.app.cookie_name, second.app.cookie_name)

    def test_cookie_from_other_port_is_not_a_session(self):
        client = self.client(A)
        other = self.start_server()
        foreign = Client(other)
        foreign.cookie = client.cookie  # nazwa zawiera port innego serwera
        self.assertIsNone(foreign.get('/_api/v1/session').json['user'])

    def test_session_expires_after_12_hours_idle(self):
        client = self.client(A)
        self.clock.advance(11 * 3600)
        self.assertIsNotNone(client.get('/_api/v1/session').json['user'])  # kontrola + odświeża bezczynność
        self.clock.advance(11 * 3600 + 59 * 60)
        self.assertIsNotNone(client.get('/_api/v1/session').json['user'])
        self.clock.advance(12 * 3600 + 60)
        self.assertIsNone(client.get('/_api/v1/session').json['user'])
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 401)

    def test_session_expires_after_7_days_even_when_active(self):
        client = self.client(A)
        for _ in range(27):  # 27 * 6 h = 162 h < 168 h
            self.clock.advance(6 * 3600)
            self.assertIsNotNone(client.get('/_api/v1/session').json['user'])
        self.clock.advance(6 * 3600 + 3600)
        self.assertIsNone(client.get('/_api/v1/session').json['user'])

    def test_removed_user_loses_session(self):
        client = self.client(A)
        self.assertIsNotNone(client.get('/_api/v1/session').json['user'])
        ps.remove_user(ps.UsersFile(self.users_path, iterations=ITER), A)
        self.assertIsNone(client.get('/_api/v1/session').json['user'])
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 401)
        self.assertIsNone(self.journal())
        other = self.client(B)  # kontrola: sesja innego konta działa
        self.check(other.op('demo', 'create', 'th-2', create_payload('m-2')), 201)

    def test_password_change_in_file_invalidates_sessions(self):
        client, other = self.client(A), self.client(B)
        ps.set_password(ps.UsersFile(self.users_path, iterations=ITER), A, password=SECRET_NEW_PW)
        self.assertIsNone(client.get('/_api/v1/session').json['user'])
        self.assertIsNotNone(other.get('/_api/v1/session').json['user'])
        self.check(Client(self.server).login(A, PASSWORDS[A]), 401, 'invalid_credentials')
        self.check(Client(self.server).login(A, SECRET_NEW_PW), 200)

    def test_role_change_takes_effect_on_next_request(self):
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')
        self.check(client.op('demo', 'delete', 'th-1'), 403)
        users = ps.UsersFile(self.users_path, iterations=ITER)

        def promote(records):
            for record in records:
                if record['login'] == A:
                    record['role'] = 'developer'
        users.update(promote)
        self.check(client.op('demo', 'delete', 'th-1'), 201)  # bez ponownego logowania


class RateLimitTests(ServerCase):
    trust_proxy = True

    def attempts(self, login, count, ip, password='zle-haslo-123'):
        return [Client(self.server, ip=ip).login(login, password) for _ in range(count)]

    def counting_verify(self):
        calls = []
        real = ps.verify_password

        def counting(password, encoded):
            calls.append(1)
            return real(password, encoded)
        return calls, mock.patch.object(ps, 'verify_password', counting)

    def test_pair_limit_5_failures_then_429_with_retry_after(self):
        responses = self.attempts(A, 5, '10.0.0.1')
        self.assertEqual([r.status for r in responses], [401] * 5)
        blocked = Client(self.server, ip='10.0.0.1').login(A, 'zle-haslo-123')
        self.check(blocked, 429, 'rate_limited')
        retry = int(blocked.header('Retry-After'))
        self.assertTrue(1 <= retry <= 900, retry)
        self.assertEqual(blocked.json['retryAfter'], retry)
        self.assertEqual(blocked.cookies(), [])

    def test_four_failures_do_not_block(self):
        self.attempts(A, 4, '10.0.0.1')
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 200)

    def test_limit_applies_before_pbkdf2_even_for_correct_password(self):
        self.attempts(A, 5, '10.0.0.1')
        calls, patch = self.counting_verify()
        with patch:
            correct = Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A])
            wrong = Client(self.server, ip='10.0.0.1').login(A, 'zle-haslo-123')
            self.check(correct, 429)
            self.check(wrong, 429)
            self.assertEqual(calls, [], 'przy zablokowanej parze nie wolno liczyć PBKDF2')
            # kontrola: ta sama aparatura liczy wywołania, gdy para nie jest zablokowana
            self.check(Client(self.server, ip='10.0.0.2').login(A, 'zle-haslo-123'), 401)
            self.assertEqual(len(calls), 1)

    def test_other_ip_and_other_login_are_not_blocked(self):
        self.attempts(A, 5, '10.0.0.1')
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 429)
        self.check(Client(self.server, ip='10.0.0.2').login(A, PASSWORDS[A]), 200)
        self.check(Client(self.server, ip='10.0.0.1').login(B, PASSWORDS[B]), 200)

    def test_successful_login_resets_pair_counter(self):
        self.attempts(A, 4, '10.0.0.1')
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 200)
        self.attempts(A, 4, '10.0.0.1')
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 200)  # 8 błędów łącznie, ale nie 5 z rzędu

    def test_login_is_normalized_before_counting(self):
        for variant in ('ANNA.KOWALSKA@example.com', ' anna.kowalska@EXAMPLE.com', 'Anna.Kowalska@example.com',
                        ' ANNA.kowalska@example.com ', 'anna.kowalska@example.com'):
            self.check(Client(self.server, ip='10.0.0.1').login(variant, 'zle-haslo-123'), 401)
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 429)

    def test_ip_limit_30_failures_across_logins_blocks_ip(self):
        for index in range(30):
            self.check(Client(self.server, ip='10.0.0.9').login('nikt%d@example.com' % index, 'zle-haslo-123'), 401)
        blocked = Client(self.server, ip='10.0.0.9').login(A, PASSWORDS[A])
        self.check(blocked, 429, 'rate_limited')
        self.assertTrue(blocked.header('Retry-After'))
        self.check(Client(self.server, ip='10.0.0.10').login(A, PASSWORDS[A]), 200)  # inne IP

    def test_29_failures_from_one_ip_do_not_block(self):
        for index in range(29):
            self.check(Client(self.server, ip='10.0.0.9').login('nikt%d@example.com' % index, 'zle-haslo-123'), 401)
        self.check(Client(self.server, ip='10.0.0.9').login(A, PASSWORDS[A]), 200)

    def test_block_expires_after_15_minutes(self):
        self.attempts(A, 5, '10.0.0.1')
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 429)
        self.clock.advance(14 * 60)
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 429)
        self.clock.advance(61)
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 200)

    def test_forwarded_for_is_ignored_without_trust_proxy(self):
        self.tearDown()
        self.start_server(trust_proxy=False)
        for index in range(5):
            self.check(Client(self.server, ip='10.1.1.%d' % index).login(A, 'zle-haslo-123'), 401)
        # nagłówek jest ignorowany: wszystkie próby liczą się do adresu połączenia, więc kolejny „inny adres” też jest blokowany
        self.check(Client(self.server, ip='10.9.9.9').login(A, PASSWORDS[A]), 429)

    def test_trust_proxy_uses_forwarded_address(self):
        """Kontrola poprzedniego testu: z `--trust-proxy` ten sam ruch jest rozdzielony po adresach z nagłówka."""
        for index in range(5):
            self.check(Client(self.server, ip='10.1.1.%d' % index).login(A, 'zle-haslo-123'), 401)
        self.check(Client(self.server, ip='10.9.9.9').login(A, PASSWORDS[A]), 200)


class ParallelRateLimitTests(ServerCase):
    """M1: równoległe złe logowania nie omijają limitu (PBKDF2 liczy się tylko do limitu)."""
    trust_proxy = True

    def parallel_logins(self, logins, ip, password='zle-haslo-123'):
        calls = []
        real = ps.verify_password
        gate = threading.Barrier(len(logins))

        def slow(password_value, encoded):
            calls.append(1)
            time.sleep(0.25)   # rozciąga okno, w którym wszystkie wątki są „w toku”
            return real(password_value, encoded)
        results = [None] * len(logins)

        def worker(index):
            client = Client(self.server, ip=ip, timeout=60)
            gate.wait(30)
            results[index] = client.login(logins[index], password).status
        with mock.patch.object(ps, 'verify_password', slow):
            threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(logins))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)
        self.assertNotIn(None, results, 'wątek nie zakończył logowania')
        return len(calls), results

    def test_twenty_parallel_wrong_logins_for_one_pair_run_at_most_five_pbkdf2(self):
        calls, statuses = self.parallel_logins([A] * 20, '10.0.0.1')
        self.assertLessEqual(calls, 5)
        self.assertGreaterEqual(calls, 1)
        self.assertEqual(statuses.count(401), calls, 'każde policzone PBKDF2 to jedno 401')
        self.assertEqual(statuses.count(429), 20 - calls, 'reszta dostaje 429')
        self.assertEqual(sorted(set(statuses)) in ([401, 429], [429]), True, statuses)
        # po wyczerpaniu budżetu para jest zablokowana także dla poprawnego hasła
        self.check(Client(self.server, ip='10.0.0.1').login(A, PASSWORDS[A]), 429, 'rate_limited')

    def test_parallel_ip_limit_30_with_different_logins(self):
        logins = ['nikt%d@example.com' % index for index in range(40)]
        calls, statuses = self.parallel_logins(logins, '10.0.0.7')
        self.assertLessEqual(calls, 30)
        self.assertEqual(statuses.count(401), calls)
        self.assertEqual(statuses.count(429), 40 - calls)
        self.check(Client(self.server, ip='10.0.0.7').login(A, PASSWORDS[A]), 429, 'rate_limited')
        self.check(Client(self.server, ip='10.0.0.8').login(A, PASSWORDS[A]), 200)  # kontrola: inne IP

    def test_control_sequential_attempts_still_give_five_wrong_then_429(self):
        calls, statuses = [], []
        real = ps.verify_password

        def counting(password, encoded):
            calls.append(1)
            return real(password, encoded)
        with mock.patch.object(ps, 'verify_password', counting):
            for _ in range(7):
                statuses.append(Client(self.server, ip='10.0.0.2').login(A, 'zle-haslo-123').status)
        self.assertEqual(statuses, [401] * 5 + [429] * 2)
        self.assertEqual(len(calls), 5)

    def test_a_correct_login_in_flight_does_not_count_as_a_failure(self):
        calls, statuses = self.parallel_logins([A] * 3, '10.0.0.3', password=PASSWORDS[A])
        self.assertEqual(statuses, [200] * 3, 'poprawne hasła w toku nie wyczerpują budżetu')
        self.check(Client(self.server, ip='10.0.0.3').login(A, 'zle-haslo-123'), 401)

    def test_table_overflow_evicts_the_oldest_entries_not_everything(self):
        clock = FakeClock(1000.0)
        limiter = ps.LoginRateLimiter(clock=clock, max_keys=10)
        for index in range(10):                      # 10 par, każda zablokowana (5 porażek), coraz nowsza
            clock.advance(1)
            for _ in range(5):
                limiter.failure('u%d@example.com' % index, '10.0.0.%d' % index)
        self.assertGreater(limiter.check('u0@example.com', '10.0.0.0'), 0, 'kontrola: przed przepełnieniem zablokowana')
        clock.advance(1)
        for _ in range(5):
            limiter.failure('nowy@example.com', '10.0.1.1')    # 11. klucz: przepełnienie
        self.assertEqual(limiter.check('u0@example.com', '10.0.0.0'), 0, 'najstarszy wpis został usunięty')
        for index in range(5, 10):
            self.assertGreater(limiter.check('u%d@example.com' % index, '10.0.0.%d' % index), 0,
                               'nowsze wpisy zostają (tabela nie jest zerowana)')
        self.assertGreater(limiter.check('nowy@example.com', '10.0.1.1'), 0)


class CsrfOriginTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.author = self.client(A)
        self.body = {'cid': 'c-csrf', 'type': 'create', 'threadId': 'th-1', 'payload': create_payload('m-1')}
        self.url = '/_api/v1/prototypes/demo/operations'

    def test_post_without_session_is_401(self):
        self.check(self.anon.post(self.url, self.body), 401)
        self.assertIsNone(self.journal())

    def test_missing_wrong_or_foreign_csrf_is_403_without_write(self):
        other = self.client(B)
        for headers in ({'X-Skillsync-CSRF': None}, {'X-Skillsync-CSRF': 'zly-token'}, {'X-Skillsync-CSRF': ''},
                        {'X-Skillsync-CSRF': other.csrf}):
            with self.subTest(headers=headers):
                self.check(self.author.post(self.url, self.body, headers=headers), 403)
        self.assertIsNone(self.journal())
        self.check(self.author.post(self.url, self.body), 201)  # kontrola: właściwy token

    def test_wrong_content_type_is_403_without_write(self):
        for ctype in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data', ''):
            with self.subTest(ctype=ctype):
                self.check(self.author.post(self.url, self.body, headers={'Content-Type': ctype or None}), 403)
        self.assertIsNone(self.journal())
        self.check(self.author.post(self.url, self.body, headers={'Content-Type': 'application/json; charset=UTF-8'}), 201)

    def test_foreign_origin_is_403_without_write(self):
        good = 'http://127.0.0.1:%d' % self.server.port
        for origin in ('http://evil.example', 'http://127.0.0.1:1', 'null', 'http://127.0.0.1.evil.example:%d' % self.server.port):
            with self.subTest(origin=origin):
                self.check(self.author.post(self.url, self.body, headers={'Origin': origin}), 403)
        self.assertIsNone(self.journal())
        self.check(self.author.post(self.url, self.body, headers={'Origin': good}), 201)

    def test_password_and_logout_need_csrf_too(self):
        self.check(self.author.post('/_api/v1/password', {'current': PASSWORDS[A], 'new': SECRET_NEW_PW},
                                    headers={'X-Skillsync-CSRF': None}), 403)
        self.check(self.author.post('/_api/v1/logout', headers={'X-Skillsync-CSRF': None}), 403)
        self.assertIsNotNone(self.author.get('/_api/v1/session').json['user'])
        self.check(self.author.post('/_api/v1/logout'), 204)

    def test_body_over_64_kib_is_413_and_exactly_64_kib_is_accepted(self):
        before = self.journal()
        payload = json.dumps(self.body).encode('utf-8')
        limit = 64 * 1024
        padded = payload + b' ' * (limit - len(payload))
        self.assertEqual(len(padded), limit)
        too_big = payload + b' ' * (limit + 1 - len(payload))
        response = self.author.post(self.url, raw=too_big)
        self.check(response, 413)
        self.assertEqual(self.journal(), before)
        self.check(self.author.post(self.url, raw=padded), 201)  # kontrola na granicy
        self.assertEqual(len(file_operations(self.demo)), 1)

    def test_malformed_json_bodies_are_400(self):
        for raw in (b'', b'{', b'[]', b'"x"', b'null', b'{"cid": NaN}', b'\xff\xfe'):
            with self.subTest(raw=raw):
                self.check(self.author.post(self.url, raw=raw), 400)
        self.assertIsNone(self.journal())


class LoginPageTests(ServerCase):
    def test_login_page_is_served_and_self_contained(self):
        page = self.check(self.anon.get('/_login'), 200)
        self.assertIn('text/html', page.header('Content-Type'))
        self.assertIn('type="password"', page.text)
        self.assertEqual(re.findall(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', page.text), [])

    def test_next_must_be_local_path(self):
        from urllib.parse import quote
        for value in ('//evil.example', 'https://evil.example/x', '/\\evil.example', 'evil.example', '///evil',
                      'javascript:alert(1)', '/ok\r\nSet-Cookie: x=1'):
            with self.subTest(next=value):
                self.assertEqual(ps.safe_next(value), '/')
                response = self.anon.get('/_login?next=' + quote(value, safe=''))
                self.assertEqual(response.status, 302, 'niebezpieczne `next` ma przekierować, nie zwrócić strony')
                location = response.header('Location')
                self.assertEqual(location, '/_login?next=%2F', 'przekierowanie na `/` (next = `/`)')
                self.assertNotIn('evil', location)
                self.assertEqual(response.header('Set-Cookie'), None)
        for value in ('/', '/demo/', '/demo/index.html?x=1'):
            self.assertEqual(ps.safe_next(value), value)  # kontrola: lokalna ścieżka zostaje
            response = self.anon.get('/_login?next=' + quote(value, safe=''))
            self.assertEqual(response.status, 200, 'kontrola: lokalna ścieżka nie jest przekierowywana')
        self.check(self.anon.get('/_login?next=%2Fdemo%2F'), 200)


# ═════════════════════════════════════════════════════════════════════════════
# mustChange i zmiana hasła (K42)
# ═════════════════════════════════════════════════════════════════════════════

class MustChangeTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.seeded = self.client(A)
        self.seed(self.seeded, 'th-1', 'm-1')
        self.must = Client(self.server)
        self.login_response = self.must.login(MUST, DEFAULT_PW)

    def test_login_reports_must_change(self):
        self.check(self.login_response, 200)
        self.assertTrue(self.login_response.json['user']['mustChange'])
        self.assertTrue(self.must.get('/_api/v1/session').json['user']['mustChange'])
        self.assertFalse(self.seeded.get('/_api/v1/session').json['user']['mustChange'])

    def test_writes_are_403_password_change_required_reads_pass(self):
        before = self.journal()
        for kind, thread, payload in (
                ('create', 'th-9', create_payload('m-9')), ('reply', 'th-1', {'message': {'id': 'm-8', 'text': 'x'}}),
                ('vote', 'th-1', {'messageId': 'm-1', 'value': 'up'})):
            with self.subTest(kind=kind):
                self.check(self.must.op('demo', kind, thread, payload), 403, 'password_change_required')
        self.assertEqual(self.journal(), before)
        self.check(self.must.operations('demo'), 200)
        self.check(self.must.get('/demo/index.html'), 200)
        self.check(self.must.get('/_api/v1/session'), 200)

    def test_logout_is_allowed(self):
        self.check(self.must.post('/_api/v1/logout'), 204)
        self.assertIsNone(self.must.get('/_api/v1/session').json['user'])

    def test_must_change_check_comes_after_csrf(self):
        self.check(self.must.op('demo', 'create', 'th-9', create_payload('m-9'), ), 403, 'password_change_required')
        response = self.must.post('/_api/v1/prototypes/demo/operations', {'cid': 'c-1', 'type': 'vote'},
                                  headers={'X-Skillsync-CSRF': 'zly'})
        self.check(response, 403)
        self.assertNotEqual(response.json.get('error'), 'password_change_required')

    def test_weak_or_unchanged_password_is_400_and_leaves_must_change(self):
        for new in ('', 'krotkie', '123456789', DEFAULT_PW):
            with self.subTest(new=new):
                self.check(self.must.post('/_api/v1/password', {'current': DEFAULT_PW, 'new': new}), 400, 'weak_password')
        self.assertTrue(self.must.get('/_api/v1/session').json['user']['mustChange'])

    def test_wrong_current_password_is_403_invalid_current(self):
        self.check(self.must.post('/_api/v1/password', {'current': 'cos-innego-123', 'new': SECRET_NEW_PW}), 403,
                   'invalid_current')
        self.assertTrue(self.must.get('/_api/v1/session').json['user']['mustChange'])

    def test_exactly_10_characters_is_accepted(self):
        self.check(self.must.post('/_api/v1/password', {'current': DEFAULT_PW, 'new': '1234567890'}), 204)
        self.assertTrue(ps.verify_password('1234567890', ps.UsersFile(self.users_path).get(MUST)['password']))

    def test_password_change_unlocks_writes_and_expires_other_sessions(self):
        second = Client(self.server)
        self.check(second.login(MUST, DEFAULT_PW), 200)
        bystander = self.client(B)
        self.check(self.must.post('/_api/v1/password', {'current': DEFAULT_PW, 'new': SECRET_NEW_PW}), 204)
        self.assertFalse(self.must.get('/_api/v1/session').json['user']['mustChange'])
        self.check(self.must.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)
        self.assertIsNone(second.get('/_api/v1/session').json['user'], 'inne sesje konta wygasają')
        self.assertIsNotNone(bystander.get('/_api/v1/session').json['user'], 'sesje innych kont zostają')
        self.check(Client(self.server).login(MUST, DEFAULT_PW), 401)
        self.check(Client(self.server).login(MUST, SECRET_NEW_PW), 200)
        record = ps.UsersFile(self.users_path).get(MUST)
        self.assertFalse(record['mustChange'])
        self.assertNotIn(SECRET_NEW_PW, Path(self.users_path).read_text())

    def test_regular_user_can_change_password_too(self):
        client = self.client(B)
        self.check(client.post('/_api/v1/password', {'current': PASSWORDS[B], 'new': PASSWORDS[B]}), 400, 'weak_password')
        self.check(client.post('/_api/v1/password', {'current': PASSWORDS[B], 'new': SECRET_NEW_PW}), 204)
        self.check(Client(self.server).login(B, SECRET_NEW_PW), 200)

    def test_password_endpoint_requires_session(self):
        self.check(self.anon.post('/_api/v1/password', {'current': DEFAULT_PW, 'new': SECRET_NEW_PW}), 401)

    def test_password_attempts_count_toward_login_limit(self):
        """Zgadywanie bieżącego hasła przez przejętą sesję nie omija limitu prób."""
        statuses = [self.must.post('/_api/v1/password', {'current': 'zle-%d-haslo' % i, 'new': SECRET_NEW_PW}).status
                    for i in range(7)]
        self.assertEqual(statuses[:5], [403] * 5)
        self.assertEqual(statuses[5:], [429, 429])

    def test_default_password_not_in_responses(self):
        for response in (self.login_response, self.must.get('/_api/v1/session'),
                         self.must.post('/_api/v1/password', {'current': DEFAULT_PW, 'new': 'krotkie'})):
            self.assertNotIn(DEFAULT_PW, response.text)


# ═════════════════════════════════════════════════════════════════════════════
# Operacje: zapis, autor z sesji, dedup, role, spójność odwołań (K1, K4, K6, K8, K14, K16)
# ═════════════════════════════════════════════════════════════════════════════

class FirstWriteAndAuthorTests(ServerCase):
    def test_first_post_creates_comments_json_with_header(self):
        self.assertIsNone(self.journal())
        client = self.client(A)
        response = self.check(client.op('demo', 'create', 'th-1', create_payload('m-1', 'Pierwszy')), 201)
        data = self.journal()
        assert_canonical_text(self, data)
        document = json.loads(data.decode('utf-8'))
        self.assertEqual(document['format'], 'skillsync-prototype-comments')
        self.assertEqual(document['version'], 2)
        self.assertEqual(document['prototypeId'], 'demo')
        self.assertEqual(len(document['operations']), 1)
        operation = response.json['operation']
        self.assertEqual(operation, document['operations'][0])
        self.assertEqual(response.json['total'], 1)
        self.assertRegex(response.json['prefix'], r'^[0-9a-f]{16}$')
        self.assertTrue(os.path.exists(os.path.join(self.demo, 'comments.json.lock')))
        self.assertFalse([n for n in os.listdir(self.demo) if n.endswith('.tmp')])

    @needs_posix
    def test_new_comments_json_is_world_readable(self):
        umask = os.umask(0)
        os.umask(umask)
        self.seed(self.client(A), 'th-1', 'm-1')
        mode = stat.S_IMODE(os.stat(os.path.join(self.demo, 'comments.json')).st_mode)
        self.assertEqual(mode, 0o644 & ~umask)

    def test_author_is_session_login_and_client_by_is_rejected(self):
        client = self.client(A)
        for forged in (B, 'agent', 'import', A, ''):
            body_before = self.journal()
            response = client.op('demo', 'create', 'th-forged', create_payload('m-forged'), by=forged)
            self.check(response, 400)
            self.assertEqual(self.journal(), body_before)
        response = self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201)  # kontrola
        self.assertEqual(response.json['operation']['by'], A)
        reply = self.check(self.client(B).op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}), 201)
        self.assertEqual(reply.json['operation']['by'], B)

    def test_cid_is_required_and_server_assigns_id_and_at(self):
        client = self.client(A)
        body = {'type': 'create', 'threadId': 'th-1', 'payload': create_payload('m-1')}
        self.check(client.post('/_api/v1/prototypes/demo/operations', body), 400)
        self.assertIsNone(self.journal())
        operation = self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201).json['operation']
        self.assertRegex(operation['id'], r'^op-[0-9A-HJKMNP-TV-Z]{26}$')
        self.assertEqual(operation['at'], '2026-11-05T09:00:00.000Z')
        self.assertEqual(list(operation), ['id', 'type', 'threadId', 'at', 'by', 'cid', 'payload'])

    def test_client_cannot_set_id_or_at(self):
        client = self.client(A)
        response = client.op('demo', 'create', 'th-1', create_payload('m-1'), id='op-' + '0' * 26,
                             at='2000-01-01T00:00:00.000Z')
        self.assertEqual(response.status, 400)
        self.assertIsNone(self.journal())

    def test_invalid_payloads_are_400_without_write(self):
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')
        before = self.journal()
        bad = [
            ('create', 'th-2', create_payload('m-2', '')), ('create', 'th-2', create_payload('m-2', '   ')),
            ('create', 'th-2', create_payload('m-2', 'x' * 4001)), ('create', 'bad-id', create_payload('m-2')),
            ('create', 'th-2', create_payload('zly-id')), ('create', 'th-2', {'screen': 's1'}),
            ('create', 'th-2', create_payload('m-2', anchor_value={'kind': 'lost'})),
            ('create', 'th-2', create_payload('m-2', anchor_value={'kind': 'element', 'x': 0, 'y': 0, 'iteration': 1})),
            ('reply', 'th-1', {'message': {'id': 'm-3', 'text': ''}}), ('vote', 'th-1', {'messageId': 'm-1', 'value': 'x'}),
            ('edit', 'th-1', {'messageId': 'm-1', 'text': ''}), ('nieznany', 'th-1', {}),
        ]
        for kind, thread, payload in bad:
            with self.subTest(kind=kind, payload=str(payload)[:60]):
                self.check(client.op('demo', kind, thread, payload), 400)
        self.assertEqual(self.journal(), before)
        self.check(client.op('demo', 'create', 'th-2', create_payload('m-2', 'x' * 4000)), 201)  # kontrola: granica 4000

    def test_anchor_iteration_above_current_is_400(self):
        client = self.client(A)
        self.check(client.op('demo', 'create', 'th-2', create_payload('m-2', anchor_value=anchor(iteration=2))), 400)
        self.assertIsNone(self.journal())
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1', anchor_value=anchor(iteration=1))), 201)

    def test_post_to_unknown_prototype_is_404(self):
        client = self.client(A)
        for slug in ('brak', 'nojson', 'Bad_Slug'):
            self.check(client.op(slug, 'create', 'th-1', create_payload('m-1')), 404)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'brak')))
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201)

    def test_v1_prototype_needs_migration_and_stays_untouched(self):
        old = os.path.join(self.root, 'old')
        before = sorted(os.listdir(old))
        content = Path(old, 'comments.js').read_bytes()
        client = self.client(A)
        self.check(client.op('old', 'create', 'th-1', create_payload('m-1')), 409, 'needs_migration')
        self.assertEqual(sorted(os.listdir(old)), before)
        self.assertEqual(Path(old, 'comments.js').read_bytes(), content)
        self.assertFalse(os.path.exists(os.path.join(old, 'comments.json')))
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201)  # kontrola: v2 przyjmuje


class GetOperationsTests(ServerCase):
    def test_people_and_labels_in_response(self):
        a, b = self.client(A), self.client(B)
        self.seed(a, 'th-1', 'm-1')
        listing = self.check(a.operations('demo'), 200).json
        self.assertEqual(listing['people'], {A: {'firstName': 'Anna', 'fullName': 'Anna Kowalska', 'label': 'Anna'}})
        self.check(b.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'odp'}}), 201)
        people = self.anon.operations('demo').json['people']
        self.assertEqual(people[A]['label'], 'Anna K.')
        self.assertEqual(people[B]['label'], 'Anna N.')
        for person in people.values():
            self.assertEqual(set(person), {'firstName', 'fullName', 'label'})
        self.assertNotIn('role', self.anon.operations('demo').text)
        self.assertNotIn(C, self.anon.operations('demo').text)  # lista wszystkich kont nie wycieka

    def test_incremental_read_with_prefix(self):
        client = self.client(A)
        first = self.seed(client, 'th-1', 'm-1').json
        full = self.anon.operations('demo').json
        self.assertEqual(full['prefix'], first['prefix'])
        self.assertEqual(full['total'], 1)
        second = self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201).json
        delta = self.anon.operations('demo', '?after=1&prefix=%s' % first['prefix']).json
        self.assertFalse(delta['reset'])
        self.assertEqual(delta['total'], 2)
        self.assertEqual([op['id'] for op in delta['operations']], [second['operation']['id']])
        self.assertEqual(delta['prefix'], second['prefix'])
        same = self.anon.operations('demo', '?after=2&prefix=%s' % second['prefix']).json
        self.assertEqual((same['reset'], same['operations']), (False, []))

    def test_after_beyond_total_or_foreign_prefix_resets_with_full_journal(self):
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')
        self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)
        current = self.anon.operations('demo').json
        for query in ('?after=5&prefix=%s' % current['prefix'], '?after=1&prefix=0123456789abcdef',
                      '?after=2&prefix=0123456789abcdef', '?after=1'):
            with self.subTest(query=query):
                reset = self.anon.operations('demo', query).json
                self.assertTrue(reset['reset'])
                self.assertEqual(reset['operations'], current['operations'])
                self.assertEqual(reset['total'], 2)
        self.assertFalse(self.anon.operations('demo', '?after=0').json['reset'])  # kontrola

    def test_file_replaced_by_other_history_resets(self):
        """K15: plik zmieniony inaczej niż dopisaniem (np. `git checkout`) -> pełny dziennik i `reset: true`."""
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')
        known = self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201).json
        self.assertFalse(self.anon.operations('demo', '?after=2&prefix=%s' % known['prefix']).json['reset'])
        write_journal(self.demo, 'demo', [raw_create(1), raw_create(2), raw_create(3)])
        replaced = self.anon.operations('demo', '?after=2&prefix=%s' % known['prefix']).json
        self.assertTrue(replaced['reset'])
        self.assertEqual(replaced['total'], 3)
        self.assertEqual([op['id'] for op in replaced['operations']], [raw_id(1), raw_id(2), raw_id(3)])
        # po przeładowaniu nowy odcisk jest spójny i przyrost działa dalej
        fresh = self.anon.operations('demo', '?after=3&prefix=%s' % replaced['prefix']).json
        self.assertEqual((fresh['reset'], fresh['operations']), (False, []))

    def test_write_by_another_process_is_visible_and_appended_after(self):
        client = self.client(A)
        first = self.seed(client, 'th-1', 'm-1').json
        self.assertEqual(self.anon.operations('demo').json['total'], 1)  # rozgrzany cache
        run = run_script(WRITER_SCRIPT, self.demo, 'zew', 3)
        self.assertEqual(run.returncode, 0, run.stderr.decode())
        listing = self.anon.operations('demo', '?after=1&prefix=%s' % first['prefix']).json
        self.assertEqual(listing['total'], 4)
        self.assertFalse(listing['reset'])
        self.assertEqual([op['threadId'] for op in listing['operations']], ['th-zew-0', 'th-zew-1', 'th-zew-2'])
        # kolejny zapis przez serwer widzi operacje obcego procesu (brak nadpisania)
        self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'down'}), 201)
        ids = [op['threadId'] for op in file_operations(self.demo)]
        self.assertEqual(ids, ['th-1', 'th-zew-0', 'th-zew-1', 'th-zew-2', 'th-1'])
        assert_canonical_text(self, self.journal())

    def test_retracted_text_stays_in_api(self):
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1', 'TEKST-DO-WYCOFANIA')
        self.check(client.op('demo', 'retract', 'th-1', {'messageId': 'm-1'}), 201)
        self.assertIn('TEKST-DO-WYCOFANIA', self.anon.operations('demo').text)
        self.assertIn(b'TEKST-DO-WYCOFANIA', self.journal())


class JournalPrefixTests(ServerCase):
    def test_journal_only_grows_across_mixed_writes(self):
        """K2: po każdym zapisie poprzednia lista operacji jest prefiksem nowej, linia po linii."""
        a, b, d = self.client(A), self.client(B), self.client(D)
        store = ps.CommentsStore(self.demo, 'demo', clock=self.clock)
        steps = [
            lambda: a.op('demo', 'create', 'th-1', create_payload('m-1', 'ą ę ó 😀')),
            lambda: b.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'odp'}}),
            lambda: a.op('demo', 'vote', 'th-1', {'messageId': 'm-2', 'value': 'down'}),
            lambda: a.op('demo', 'edit', 'th-1', {'messageId': 'm-1', 'text': 'zmienione'}),
            lambda: store.append([{'type': 'create', 'threadId': 'th-agent', 'by': 'agent', 'cid': 'c-agent',
                                   'payload': create_payload('m-agent', 'od agenta')}]),
            lambda: self.clock.advance(-7200),
            lambda: b.op('demo', 'retract', 'th-1', {'messageId': 'm-2'}),
            lambda: d.op('demo', 'resolve', 'th-1', {'reason': 'author'}),
            lambda: a.op('demo', 'reopen', 'th-1', {}),
            lambda: d.op('demo', 'delete', 'th-agent', {}),
        ]
        previous_lines, previous_ops = [], []
        for step in steps:
            step()
            data = self.journal()
            if data is None:
                continue
            assert_canonical_text(self, data)
            lines = op_lines(data)
            operations = file_operations(self.demo)
            self.assertEqual(lines[:len(previous_lines)], previous_lines, 'dziennik przestał być prefiksem')
            self.assertEqual(operations[:len(previous_ops)], previous_ops)
            previous_lines, previous_ops = lines, operations
        self.assertGreaterEqual(len(previous_ops), 8)

    def test_rejected_and_deduplicated_requests_leave_bytes_identical(self):
        a = self.client(A)
        first = self.seed(a, 'th-1', 'm-1')
        before = self.journal()
        a.op('demo', 'create', 'th-1', create_payload('m-9'))                 # duplicate_id
        a.op('demo', 'delete', 'th-1')                                        # 403
        a.post('/_api/v1/prototypes/demo/operations', raw=b'zepsute')         # 400
        self.anon.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'})  # 401
        repeat = a.op('demo', 'create', 'th-1', create_payload('m-1'), cid=first.json['operation']['cid'])  # dedup 200
        self.assertEqual(repeat.status, 200)
        self.assertEqual(self.journal(), before)


class DedupTests(ServerCase):
    def test_create_reply_vote_replay_returns_existing_operation(self):
        a, b = self.client(A), self.client(B)
        created = self.check(a.op('demo', 'create', 'th-1', create_payload('m-1'), cid='c-create'), 201).json
        replied = self.check(b.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}, cid='c-reply'), 201).json
        voted = self.check(a.op('demo', 'vote', 'th-1', {'messageId': 'm-2', 'value': 'up'}, cid='c-vote'), 201).json
        before = self.journal()
        for client, kind, thread, payload, cid, first in (
                (a, 'create', 'th-1', create_payload('m-1'), 'c-create', created),
                (b, 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}, 'c-reply', replied),
                (a, 'vote', 'th-1', {'messageId': 'm-2', 'value': 'up'}, 'c-vote', voted)):
            with self.subTest(kind=kind):
                again = self.check(client.op('demo', kind, thread, payload, cid=cid), 200)
                self.assertEqual(again.json['operation'], first['operation'])
                self.assertEqual(again.json['total'], 3)
        self.assertEqual(self.journal(), before)

    def test_all_queueable_types_are_idempotent(self):
        """K16: ponowienie z kolejki offline (create, reply, edit, retract, vote, resolve, reopen, reanchor, delete)."""
        a, d = self.client(A), self.client(D)
        steps = [
            (a, 'create', 'th-1', create_payload('m-1')), (a, 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}),
            (a, 'edit', 'th-1', {'messageId': 'm-2', 'text': 'y'}), (a, 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}),
            (a, 'resolve', 'th-1', {'reason': 'author'}), (a, 'reopen', 'th-1', {}),
            (a, 'reanchor', 'th-1', {'screen': 's2', 'anchor': anchor()}), (a, 'retract', 'th-1', {'messageId': 'm-2'}),
            (d, 'delete', 'th-1', {}),
        ]
        results = []
        for client, kind, thread, payload in steps:
            cid = 'c-' + kind
            first = self.check(client.op('demo', kind, thread, payload, cid=cid), 201)
            results.append((client, kind, thread, payload, cid, first.json['operation']))
        before = self.journal()
        for client, kind, thread, payload, cid, operation in results:
            with self.subTest(kind=kind):
                again = client.op('demo', kind, thread, payload, cid=cid)
                self.assertEqual(again.status, 200, again.text)  # także `delete` i `reopen` na już zmienionym stanie
                self.assertEqual(again.json['operation'], operation)
        self.assertEqual(self.journal(), before)

    def test_dedup_comes_before_validation_and_duplicate_id_check(self):
        """K4: ten sam (`by`, `cid`) = 200 także dla `create` z istniejącym `threadId` i dla zepsutej treści."""
        a = self.client(A)
        first = self.check(a.op('demo', 'create', 'th-1', create_payload('m-1'), cid='c-same'), 201).json['operation']
        before = self.journal()
        existing_thread = a.op('demo', 'create', 'th-1', create_payload('m-1'), cid='c-same')
        self.check(existing_thread, 200)
        self.assertEqual(existing_thread.json['operation'], first)
        garbage = a.op('demo', 'vote', 'th-77', {'zly': 'ładunek'}, cid='c-same')
        self.check(garbage, 200)
        self.assertEqual(garbage.json['operation'], first)
        self.assertEqual(self.journal(), before)
        # kontrola: nowy cid z tym samym threadId nie jest deduplikowany, tylko odrzucony jako duplikat identyfikatora
        self.check(a.op('demo', 'create', 'th-1', create_payload('m-5'), cid='c-other'), 409, 'duplicate_id')

    def test_dedup_is_per_author(self):
        a, b = self.client(A), self.client(B)
        self.check(a.op('demo', 'create', 'th-1', create_payload('m-1'), cid='c-shared'), 201)
        other = self.check(b.op('demo', 'create', 'th-2', create_payload('m-2'), cid='c-shared'), 201)
        self.assertEqual(other.json['operation']['by'], B)
        self.assertEqual(len(file_operations(self.demo)), 2)

    def test_dedup_response_for_forbidden_replay_does_not_leak_other_authors_operation(self):
        a, b = self.client(A), self.client(B)
        self.check(a.op('demo', 'create', 'th-1', create_payload('m-1', 'sekretny tekst A'), cid='c-a'), 201)
        response = b.op('demo', 'create', 'th-3', create_payload('m-3', 'tekst B'), cid='c-a')
        self.check(response, 201)  # inny autor, ten sam cid: nowa operacja B, nie operacja A
        self.assertNotIn('sekretny tekst A', response.text)


class PrototypeHttpLockTests(ServerCase):
    lock_timeout = 0.4

    def test_503_while_lock_is_held_then_ok_after_release(self):
        client = self.client(A)
        self.seed(client, 'th-0', 'm-0')
        before = self.journal()
        lock_path = os.path.join(self.demo, 'comments.json.lock')
        started = time.monotonic()
        with ps.FileLock(lock_path, timeout=5):
            blocked = client.op('demo', 'create', 'th-1', create_payload('m-1'))
        self.check(blocked, 503)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)
        self.assertEqual(self.journal(), before)
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201)  # kontrola po zwolnieniu
        self.assertEqual(len(file_operations(self.demo)), 2)

    @unittest.skipUnless(POSIX, 'flock między procesami: POSIX')
    def test_503_while_another_process_holds_the_file_lock(self):
        client = self.client(A)
        self.seed(client, 'th-0', 'm-0')
        holder_script = ("import fcntl, sys\nf = open(sys.argv[1], 'a+')\nfcntl.flock(f, fcntl.LOCK_EX)\n"
                         "print('locked', flush=True)\nsys.stdin.readline()\n")
        holder = subprocess.Popen([sys.executable, '-c', holder_script, os.path.join(self.demo, 'comments.json.lock')],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(holder.stdout.readline().strip(), b'locked')
            before = self.journal()
            self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 503)
            self.assertEqual(self.journal(), before)
        finally:
            holder.communicate(b'\n', timeout=30)
        self.check(client.op('demo', 'create', 'th-1', create_payload('m-1')), 201)


class FileSizeLimitTests(ServerCase):
    max_file_bytes = 3000

    def test_507_when_file_reaches_limit_dedup_still_works(self):
        client = self.client(A)
        statuses, last_ok = [], None
        for index in range(30):
            response = client.op('demo', 'create', 'th-%d' % index, create_payload('m-%d' % index, 'wpis ' + 'x' * 200),
                                 cid='q-%d' % index)
            statuses.append(response.status)
            if response.status == 201:
                last_ok = (index, response.json['operation'])
            else:
                break
        self.assertEqual(statuses[0], 201)
        self.assertEqual(statuses[-1], 507, statuses)
        self.assertTrue(all(code == 201 for code in statuses[:-1]))
        before = self.journal()
        self.assertGreaterEqual(len(before), 3000)
        index, operation = last_ok
        again = client.op('demo', 'create', 'th-%d' % index, create_payload('m-%d' % index), cid='q-%d' % index)
        self.check(again, 200)
        self.assertEqual(again.json['operation'], operation)
        self.check(client.op('demo', 'vote', 'th-0', {'messageId': 'm-0', 'value': 'up'}), 507)
        self.assertEqual(self.journal(), before)
        assert_canonical_text(self, before)

    def test_default_limit_is_20_mib_and_warning_10_mib(self):
        self.assertEqual(ps.MAX_FILE_BYTES, 20 * 1024 * 1024)
        self.assertEqual(ps.WARN_FILE_BYTES, 10 * 1024 * 1024)
        self.assertEqual(ps.MAX_BODY, 64 * 1024)
        self.assertEqual(ps.MAX_TEXT, 4000)


class MonotonicHttpTests(ServerCase):
    def test_clock_set_back_one_hour_keeps_order_of_create_and_votes(self):
        """K34: po cofnięciu zegara o 1 h operacja nie trafia przed `create` ani przed poprzedni głos."""
        a = self.client(A)
        created = self.check(a.op('demo', 'create', 'th-1', create_payload('m-1')), 201).json['operation']
        up = self.check(a.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201).json['operation']
        self.clock.advance(-3600)
        down = self.check(a.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'down'}), 201).json['operation']
        reply = self.check(self.client(B).op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}), 201).json['operation']
        sequence = [created, up, down, reply]
        self.assertEqual([op['at'] for op in sequence], sorted(set(op['at'] for op in sequence)))
        self.assertEqual([op['id'] for op in sequence], sorted(set(op['id'] for op in sequence)))
        self.assertEqual(ps.parse_at(down['at']), ps.parse_at(up['at']) + 1)
        fold = ps.fold_operations(file_operations(self.demo))
        message = fold['threads'][0]['messages'][0]
        self.assertEqual(message['votes'], {A: 'down'})  # ostatni głos wygrywa także po cofnięciu zegara
        self.assertEqual([m['id'] for m in fold['threads'][0]['messages']], ['m-1', 'm-2'])

    def test_at_has_millisecond_utc_format_and_follows_clock(self):
        a = self.client(A)
        first = self.check(a.op('demo', 'create', 'th-1', create_payload('m-1')), 201).json['operation']
        self.clock.advance(5.25)
        second = self.check(a.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201).json['operation']
        self.assertEqual(first['at'], '2026-11-05T09:00:00.000Z')
        self.assertEqual(second['at'], '2026-11-05T09:00:05.250Z')


# ═════════════════════════════════════════════════════════════════════════════
# Macierz ról (K14)
# ═════════════════════════════════════════════════════════════════════════════

class RoleMatrixTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.c, self.d = self.client(A), self.client(B), self.client(C), self.client(D)
        self.seed(self.a, 'th-1', 'm-1', 'wpis autora A')
        self.check(self.b.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'odpowiedź B'}}), 201)

    def forbidden(self, client, kind, thread, payload, expected=403):
        before = self.journal()
        response = client.op('demo', kind, thread, payload)
        self.check(response, expected)
        self.assertEqual(self.journal(), before, 'zapis mimo odmowy')
        return response

    def test_create_reply_vote_for_every_role(self):
        for index, client in enumerate((self.a, self.b, self.c, self.d)):
            self.check(client.op('demo', 'create', 'th-r%d' % index, create_payload('m-r%d' % index)), 201)
            self.check(client.op('demo', 'reply', 'th-1', {'message': {'id': 'm-rr%d' % index, 'text': 'x'}}), 201)
            self.check(client.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)

    def test_edit_and_retract_only_own_message_even_for_developer(self):
        for client in (self.b, self.c, self.d):
            self.forbidden(client, 'edit', 'th-1', {'messageId': 'm-1', 'text': 'przejęte'})
            self.forbidden(client, 'retract', 'th-1', {'messageId': 'm-1'})
        self.check(self.a.op('demo', 'edit', 'th-1', {'messageId': 'm-1', 'text': 'moje nowe'}), 201)  # kontrola
        self.check(self.b.op('demo', 'edit', 'th-1', {'messageId': 'm-2', 'text': 'moje B'}), 201)
        self.forbidden(self.a, 'edit', 'th-1', {'messageId': 'm-2', 'text': 'cudze'})
        self.check(self.b.op('demo', 'retract', 'th-1', {'messageId': 'm-2'}), 201)

    def test_delete_only_developer(self):
        for client in (self.a, self.b, self.c):
            self.forbidden(client, 'delete', 'th-1', {})
        self.check(self.d.op('demo', 'delete', 'th-1', {}), 201)
        self.check(self.a.op('demo', 'reply', 'th-1', {'message': {'id': 'm-9', 'text': 'x'}}), 404, 'thread_not_found')

    def test_resolve_reopen_reanchor_own_thread_or_developer(self):
        payloads = {'resolve': {'reason': 'author'}, 'reopen': {}, 'reanchor': {'screen': 's2', 'anchor': anchor()}}
        for kind, payload in payloads.items():
            with self.subTest(kind=kind):
                for stranger in (self.b, self.c):
                    self.forbidden(stranger, kind, 'th-1', payload)
        for kind, payload in payloads.items():
            with self.subTest(kind=kind, who='autor'):
                self.check(self.a.op('demo', kind, 'th-1', payload), 201)
        for kind, payload in payloads.items():
            with self.subTest(kind=kind, who='programista'):
                self.check(self.d.op('demo', kind, 'th-1', payload), 201)

    def test_browser_resolve_only_with_reason_author(self):
        for reason in ('done', 'kept', 'out_of_scope', 'cokolwiek'):
            for client in (self.a, self.d):
                self.forbidden(client, 'resolve', 'th-1', {'reason': reason}, expected=400)
        self.check(self.d.op('demo', 'resolve', 'th-1', {'reason': 'author'}), 201)  # kontrola

    def test_reanchor_from_browser_cannot_set_lost(self):
        self.forbidden(self.a, 'reanchor', 'th-1', {'screen': 's1', 'anchor': {'kind': 'lost'}}, expected=400)
        self.forbidden(self.d, 'reanchor', 'th-1', {'screen': 's1', 'anchor': {'kind': 'lost'}}, expected=400)
        self.check(self.a.op('demo', 'reanchor', 'th-1', {'screen': 's1', 'anchor': anchor('element')}), 201)

    def test_cli_only_types_are_403_cli_only_for_every_role(self):
        payloads = {
            'triage': {'class': 'clear'}, 'decision': {'kind': 'agent', 'outcome': 'apply', 'text': 'x'},
            'iteration': {'number': 1, 'path': 'iterations/1/', 'hash': 'a' * 64},
            'restore': {'screen': 's1', 'from': 1, 'frozen': 2, 'reason': 'bo', 'hashBefore': 'a' * 64,
                        'hashAfter': 'b' * 64},
        }
        for kind, payload in payloads.items():
            for name, client in (('A', self.a), ('C', self.c), ('D', self.d)):
                with self.subTest(kind=kind, role=name):
                    thread = None if kind in GLOBAL else 'th-1'
                    self.forbidden(client, kind, thread, payload).json  # 403 bez zapisu
                    self.assertEqual(client.op('demo', kind, thread, payload).json['error'], 'cli_only')

    def test_accept_and_unaccept_are_browser_types_after_f3(self):
        """Kontrakt sprzed F3: accept i unaccept to 403 `cli_only` dla każdej roli; teraz (F3, kontrakt B): typy
        przeglądarkowe, a o odmowie decyduje rola (commenter 403 `forbidden`) i treść (acceptor i developer
        dostają błąd treści, nie `cli_only`)."""
        payloads = {
            'accept': {'scope': 'screen', 'screens': {'s1': 'a' * 64}, 'version': 'b' * 64},
            'unaccept': {'screens': ['s1']},
        }
        for kind, payload in payloads.items():
            for name, client in (('A', self.a), ('C', self.c), ('D', self.d)):
                with self.subTest(kind=kind, role=name):
                    before = self.journal()
                    response = client.op('demo', kind, None, payload)
                    self.assertNotEqual((response.json or {}).get('error'), 'cli_only', response.text)
                    if name == 'A':
                        self.check(response, 403, 'forbidden')
                    else:
                        # `demo` of this world has no screens: the reference is refused with 400/409, never as cli_only
                        self.assertIn(response.status, (400, 409), response.text)
                        self.assertIn(response.json['error'], ('unknown_screen', 'stale_view', 'not_accepted'))
                    self.assertEqual(self.journal(), before, 'odmowa nie zapisuje')

    def test_accept_fields_via_who_iteration_from_client_are_not_accepted(self):
        for extra in ({'via': 'cli'}, {'who': A}, {'iteration': 99}):
            payload = {'scope': 'screen', 'screens': {'s1': 'a' * 64}, 'iteration': 1, 'via': 'ui'}
            payload.update(extra)
            response = self.c.op('demo', 'accept', None, payload)
            # F1: 403 cli_only; F3 (kontrakt B): 400 `field_not_allowed` dla pól ustawianych przez serwer
            self.check(response, 400, 'field_not_allowed')
        self.assertEqual(len(file_operations(self.demo)), 2)

    def test_role_check_precedes_reference_for_foreign_messages(self):
        """Cudzy wpis: 403 także programisty; kontrola: ten sam wpis własny jest edytowalny."""
        self.forbidden(self.d, 'edit', 'th-1', {'messageId': 'm-2', 'text': 'x'})
        self.check(self.b.op('demo', 'edit', 'th-1', {'messageId': 'm-2', 'text': 'x'}), 201)


class ReferenceConsistencyTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.d = self.client(A), self.client(B), self.client(D)
        self.seed(self.a, 'th-1', 'm-1')
        self.seed(self.a, 'th-2', 'm-3')

    def rejected(self, client, kind, thread, payload, status, error):
        before = self.journal()
        response = client.op('demo', kind, thread, payload)
        self.check(response, status, error)
        self.assertEqual(self.journal(), before)

    def test_duplicate_ids(self):
        self.rejected(self.a, 'create', 'th-1', create_payload('m-new'), 409, 'duplicate_id')
        self.rejected(self.a, 'create', 'th-new', create_payload('m-1'), 409, 'duplicate_id')
        self.rejected(self.b, 'create', 'th-new', create_payload('m-3'), 409, 'duplicate_id')  # message.id z innego wątku
        self.rejected(self.b, 'reply', 'th-1', {'message': {'id': 'm-1', 'text': 'x'}}, 409, 'duplicate_id')
        self.rejected(self.b, 'reply', 'th-1', {'message': {'id': 'm-3', 'text': 'x'}}, 409, 'duplicate_id')
        self.check(self.b.op('demo', 'create', 'th-new', create_payload('m-new')), 201)  # kontrola

    def test_operations_on_missing_thread_are_404(self):
        payloads = {
            'reply': {'message': {'id': 'm-x', 'text': 'x'}}, 'edit': {'messageId': 'm-1', 'text': 'x'},
            'retract': {'messageId': 'm-1'}, 'vote': {'messageId': 'm-1', 'value': 'up'}, 'resolve': {'reason': 'author'},
            'reopen': {}, 'reanchor': {'screen': 's1', 'anchor': anchor()}, 'delete': {},
        }
        for kind, payload in payloads.items():
            with self.subTest(kind=kind):
                self.rejected(self.d, kind, 'th-nie-ma', payload, 404, 'thread_not_found')
        self.check(self.d.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)  # kontrola

    def test_message_outside_thread_is_400(self):
        for kind, payload in (('edit', {'messageId': 'm-3', 'text': 'x'}), ('retract', {'messageId': 'm-3'}),
                              ('vote', {'messageId': 'm-3', 'value': 'up'}),
                              ('vote', {'messageId': 'm-nie-ma', 'value': 'up'})):
            with self.subTest(kind=kind, payload=payload):
                self.rejected(self.a, kind, 'th-1', payload, 400, 'message_not_in_thread')
        self.check(self.a.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)

    def test_retracted_message_rejects_edit_vote_retract(self):
        self.check(self.b.op('demo', 'reply', 'th-1', {'message': {'id': 'm-2', 'text': 'x'}}), 201)
        self.check(self.b.op('demo', 'retract', 'th-1', {'messageId': 'm-2'}), 201)
        self.rejected(self.b, 'edit', 'th-1', {'messageId': 'm-2', 'text': 'nowy'}, 409, 'message_retracted')
        self.rejected(self.a, 'vote', 'th-1', {'messageId': 'm-2', 'value': 'up'}, 409, 'message_retracted')
        self.rejected(self.b, 'retract', 'th-1', {'messageId': 'm-2'}, 409, 'message_retracted')
        self.check(self.a.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'up'}), 201)  # kontrola

    def test_deleted_thread_is_404_for_everything_including_second_delete(self):
        self.check(self.d.op('demo', 'delete', 'th-2', {}), 201)
        for kind, payload in (('reply', {'message': {'id': 'm-y', 'text': 'x'}}), ('vote', {'messageId': 'm-3', 'value': 'up'}),
                              ('edit', {'messageId': 'm-3', 'text': 'x'}), ('delete', {}), ('reopen', {})):
            with self.subTest(kind=kind):
                self.rejected(self.d, kind, 'th-2', payload, 404, 'thread_not_found')
        self.rejected(self.a, 'create', 'th-2', create_payload('m-z'), 409, 'duplicate_id')  # identyfikator zajęty na stałe

    def test_missing_or_malformed_thread_id(self):
        before = self.journal()
        for thread in (None, '', 'zly', 'th-' + 'x' * 100, 5):
            with self.subTest(thread=thread):
                body = {'cid': uid('c'), 'type': 'vote', 'payload': {'messageId': 'm-1', 'value': 'up'}}
                if thread is not None:
                    body['threadId'] = thread
                response = self.a.post('/_api/v1/prototypes/demo/operations', body)
                self.assertIn(response.status, (400, 404))
        self.assertEqual(self.journal(), before)


class RetractVisibilityTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.d = self.client(A), self.client(B), self.client(D)

    def thread(self, thread, mid, author=None):
        self.seed(author or self.a, thread, mid, 'wpis otwierający %s' % mid)

    def reply(self, client, thread, mid):
        self.check(client.op('demo', 'reply', thread, {'message': {'id': mid, 'text': 'odpowiedź %s' % mid}}), 201)

    def hidden(self, thread, mid):
        """Wątek jest ukryty: dalsze operacje = 404 `thread_not_found` (nie 409)."""
        before = self.journal()
        for client, kind, payload in (
                (self.b, 'reply', {'message': {'id': uid('m'), 'text': 'x'}}),
                (self.b, 'vote', {'messageId': mid, 'value': 'up'}), (self.a, 'edit', {'messageId': mid, 'text': 'x'}),
                (self.a, 'retract', {'messageId': mid}), (self.d, 'delete', {}), (self.a, 'reopen', {})):
            with self.subTest(thread=thread, kind=kind):
                self.check(client.op('demo', kind, thread, payload), 404, 'thread_not_found')
        self.assertEqual(self.journal(), before)

    def test_retracting_opening_message_without_others_replies_hides_thread(self):
        self.thread('th-1', 'm-1')
        self.check(self.a.op('demo', 'retract', 'th-1', {'messageId': 'm-1'}), 201)
        self.hidden('th-1', 'm-1')

    def test_own_replies_vanish_with_the_thread(self):
        self.thread('th-1', 'm-1')
        self.reply(self.a, 'th-1', 'm-2')
        self.check(self.a.op('demo', 'retract', 'th-1', {'messageId': 'm-1'}), 201)
        self.hidden('th-1', 'm-1')

    def test_only_retracted_replies_of_others_still_hide(self):
        self.thread('th-1', 'm-1')
        self.reply(self.b, 'th-1', 'm-2')
        self.check(self.b.op('demo', 'retract', 'th-1', {'messageId': 'm-2'}), 201)
        self.check(self.a.op('demo', 'retract', 'th-1', {'messageId': 'm-1'}), 201)
        self.hidden('th-1', 'm-1')

    def test_unretracted_reply_of_another_person_keeps_stub_and_thread_alive(self):
        self.thread('th-1', 'm-1')
        self.reply(self.b, 'th-1', 'm-2')
        self.check(self.a.op('demo', 'retract', 'th-1', {'messageId': 'm-1'}), 201)
        # wątek zostaje z zaślepką: dalsze odpowiedzi i głosy na niewycofane wpisy działają
        self.reply(self.d, 'th-1', 'm-3')
        self.check(self.d.op('demo', 'vote', 'th-1', {'messageId': 'm-2', 'value': 'up'}), 201)
        # a wpis wycofany jest nieedytowalny (409), nie 404
        self.check(self.a.op('demo', 'edit', 'th-1', {'messageId': 'm-1', 'text': 'x'}), 409, 'message_retracted')

    def test_retracting_a_reply_does_not_hide_the_thread(self):
        self.thread('th-1', 'm-1')
        self.reply(self.b, 'th-1', 'm-2')
        self.check(self.b.op('demo', 'retract', 'th-1', {'messageId': 'm-2'}), 201)
        self.reply(self.a, 'th-1', 'm-3')
        self.check(self.d.op('demo', 'vote', 'th-1', {'messageId': 'm-1', 'value': 'down'}), 201)

    def test_numbers_leave_gaps_and_text_stays_in_file(self):
        self.thread('th-1', 'm-1')
        self.thread('th-2', 'm-2')
        self.thread('th-3', 'm-3')
        self.check(self.a.op('demo', 'retract', 'th-2', {'messageId': 'm-2'}), 201)
        self.check(self.d.op('demo', 'delete', 'th-1', {}), 201)
        fold = ps.fold_operations(file_operations(self.demo))
        self.assertEqual([(t['id'], t['number']) for t in fold['threads']], [('th-3', 3)])
        self.assertIn('wpis otwierający m-2'.encode('utf-8'), self.journal())  # tekst wycofanego wpisu zostaje w dzienniku


# ═════════════════════════════════════════════════════════════════════════════
# Zgodność JSON API z kontraktem: ciało odpowiedzi błędów
# ═════════════════════════════════════════════════════════════════════════════

class ErrorBodyTests(ServerCase):
    def test_errors_are_json_with_error_code_and_no_secrets(self):
        a = self.client(A)
        responses = [self.anon.op('demo', 'create', 'th-1', create_payload('m-1')),
                     a.op('demo', 'delete', 'th-404', {}), a.op('old', 'create', 'th-1', create_payload('m-1')),
                     Client(self.server).login(A, 'zle-haslo-123')]
        for response in responses:
            with self.subTest(status=response.status):
                self.assertIn('application/json', response.header('Content-Type'))
                self.assertIn('error', response.json)
                self.assertNotIn('Traceback', response.text)
                self.assertNotIn(PASSWORDS[A], response.text)
                self.assertNotIn('pbkdf2', response.text)

    def test_request_log_has_no_secrets(self):
        stream = io.StringIO()
        self.tearDown()
        self.start_server(log=stream)
        client = Client(self.server)
        client.login(A, PASSWORDS[A])
        client.op('demo', 'create', 'th-1', create_payload('m-1', 'TRESC-WPISU'))
        client.get('/demo/index.html?token=ABC123')
        log = stream.getvalue()
        self.assertTrue(log.strip(), 'kontrola: dziennik nie jest pusty')
        self.assertIn(A, log)  # login w dzienniku jest dozwolony
        for secret in (PASSWORDS[A], client.csrf, client.cookie.split('=', 1)[1], 'TRESC-WPISU', 'ABC123'):
            self.assertNotIn(secret, log)


class DamagedLogTests(ServerCase):
    def damaged(self, operations):
        text = ('{"format":"skillsync-prototype-comments","version":2,"prototypeId":"demo","operations":%s}\n'
                % json.dumps(operations))
        write(os.path.join(self.demo, 'comments.json'), text)
        return text.encode('utf-8')

    def test_non_object_operation_gives_409_invalid_log_and_no_write(self):
        a = self.client(A)
        for operations in (['x'], [None], [[1]], [raw_create(1), 'x']):
            with self.subTest(operations=operations):
                before = self.damaged(operations)
                response = a.op('demo', 'create', 'th-new', create_payload('m-new'))
                self.check(response, 409, 'invalid_log')
                self.assertTrue(response.json.get('message'), 'powód uszkodzenia w odpowiedzi')
                self.assertEqual(self.journal(), before, 'plik bez zmian')
                self.check(a.op('demo', 'create', 'th-new', create_payload('m-new')), 409, 'invalid_log')  # nie 500, trwale

    def test_control_valid_log_accepts_the_same_request(self):
        a = self.client(A)
        self.damaged([raw_create(1)])
        self.check(a.op('demo', 'create', 'th-new', create_payload('m-new')), 201)


class UsersFileRemovedTests(ServerCase):
    def test_removing_the_users_file_after_start_cuts_off_logins_and_sessions(self):
        a = self.client(A)
        self.assertEqual(self.check(a.get('/_api/v1/session'), 200).json['user']['login'], A)  # kontrola
        self.check(Client(self.server).login(B, PASSWORDS[B]), 200)
        backup = os.path.join(self.tmp, 'users.backup')
        shutil.copy(self.users_path, backup)
        os.unlink(self.users_path)
        self.assertIsNone(self.check(a.get('/_api/v1/session'), 200).json['user'], 'istniejąca sesja przestaje działać')
        self.check(a.op('demo', 'create', 'th-x', create_payload('m-x')), 401)
        self.assertIsNone(self.journal(), 'nic nie zapisano')
        self.check(Client(self.server).login(B, PASSWORDS[B]), 401, 'invalid_credentials')
        self.check(Client(self.server).login(A, PASSWORDS[A]), 401, 'invalid_credentials')
        # po przywróceniu pliku konta wracają (nowe logowanie przechodzi)
        shutil.copy(backup, self.users_path)
        self.check(Client(self.server).login(B, PASSWORDS[B]), 200)


class MigratedV1IdsTests(ServerCase):
    """B1: id silnika v1 (`thread-…`, `msg-…`) w zmigrowanym dzienniku; przeglądarka nadal używa `th-`/`m-`."""

    def setUp(self):
        super().setUp()
        imported = {'id': raw_id(1), 'type': 'create', 'threadId': 'thread-0a1b2c3d-1111-4222-8333-444455556666',
                    'at': raw_at(1), 'by': 'import',
                    'payload': {'message': {'id': 'msg-0a1b2c3d-1111-4222-8333-444455556666', 'text': 'z v1',
                                            'importedAuthor': 'Recenzent A'},
                                'screen': 's1', 'anchor': {'kind': 'screen', 'x': 0, 'y': 0, 'iteration': 1},
                                'label': 'Nowy'}}
        self.thread = imported['threadId']
        self.message = imported['payload']['message']['id']
        write_journal(self.demo, 'demo', [imported])
        self.a = self.client(A)

    def test_browser_create_with_v1_style_thread_id_is_rejected_and_th_is_accepted(self):
        before = self.journal()
        self.check(self.a.op('demo', 'create', 'thread-nowy', create_payload('m-n1')), 400)
        self.check(self.a.op('demo', 'create', 'th-nowy', create_payload('msg-n2')), 400)
        self.assertEqual(self.journal(), before, 'odrzucone żądania nic nie zapisują')
        self.check(self.a.op('demo', 'create', 'th-nowy', create_payload('m-n1')), 201)  # kontrola

    def test_operations_on_migrated_threads_and_messages_are_accepted(self):
        listed = self.check(self.a.operations('demo'), 200).json
        self.assertEqual(listed['operations'][0]['threadId'], self.thread)
        self.check(self.a.op('demo', 'reply', self.thread, {'message': {'id': 'm-r1', 'text': 'odp'}}), 201)
        self.check(self.a.op('demo', 'vote', self.thread, {'messageId': self.message, 'value': 'up'}), 201)
        self.check(self.a.op('demo', 'reply', self.thread, {'message': {'id': 'msg-r2', 'text': 'x'}}), 400)  # kontrola


class MigrationBackupFileTests(ServerCase):
    """R2-5: `comments.v1.js` (kopia po migracji) nie jest serwowany i nie wchodzi do skrótu zasobów."""

    def test_comments_v1_js_is_404_and_does_not_change_the_assets_hash(self):
        self.check(self.anon.get('/demo/prototype.js'), 200)  # kontrola: zwykły zasób jest serwowany
        before = ps.assets_hash(self.demo)
        write(os.path.join(self.demo, 'comments.v1.js'), 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {};\n')
        self.check(self.anon.get('/demo/comments.v1.js'), 404)
        self.assertEqual(ps.assets_hash(self.demo), before, 'plik nie zmienia skrótu zasobów')
        self.assertNotIn('comments.v1.js', ps.asset_files(self.demo))
        self.assertEqual(ps.screen_hashes(self.demo)['assets'], before)
        # kontrola: zwykły nowy zasób zmienia skrót i jest serwowany
        write(os.path.join(self.demo, 'logo.js'), 'var x = 1;\n')
        self.assertNotEqual(ps.assets_hash(self.demo), before)
        self.assertIn('logo.js', ps.asset_files(self.demo))
        self.check(self.anon.get('/demo/logo.js'), 200)


class ConcurrentPasswordChangeTests(ServerCase):
    """R2-9: żądanie tej samej sesji w oknie między zapisem hasła a aktualizacją sesji nie wylogowuje."""

    def test_parallel_request_of_the_same_session_does_not_log_it_out(self):
        client = self.client(A)
        written, release = threading.Event(), threading.Event()
        real = ps.change_own_password

        def paused(*args, **kwargs):
            result = real(*args, **kwargs)     # plik użytkowników ma już nowy skrót
            written.set()
            self.assertTrue(release.wait(30))
            return result
        outcome = []
        with mock.patch.object(ps, 'change_own_password', paused):
            worker = threading.Thread(target=lambda: outcome.append(
                client.post('/_api/v1/password', {'current': PASSWORDS[A], 'new': SECRET_NEW_PW}).status))
            worker.start()
            try:
                self.assertTrue(written.wait(30))
                during = client.get('/_api/v1/session').json
            finally:
                release.set()
                worker.join(30)
        self.assertEqual(outcome, [204])
        self.assertEqual((during['user'] or {}).get('login'), A, 'sesja przetrwała okno zmiany hasła')
        self.assertEqual(client.get('/_api/v1/session').json['user']['login'], A)
        # kontrola: inna sesja tego konta wygasa, a nowe hasło działa
        other = Client(self.server)
        self.check(other.login(A, SECRET_NEW_PW), 200)
        self.check(Client(self.server).login(A, PASSWORDS[A]), 401)


# ═════════════════════════════════════════════════════════════════════════════
# F2: hash ekranów, iteracje, przywracanie i podgląd (K8, K14, K23, K25, K37, K39)
# ═════════════════════════════════════════════════════════════════════════════

R_HEAD = ('<!doctype html>\n<html lang="pl" data-prototype-id="demo">\n<head>\n<meta charset="utf-8">\n'
          '<title>Demo</title>\n<link rel="stylesheet" href="screens.css">\n</head>\n<body>\n')
R_TAIL = '<script src="prototype.js"></script>\n</body>\n</html>\n'
R_S1 = '<section class="screen" id="s1" data-prototype-example>\n  <div class="frame">Przykład</div>\n</section>\n'
R_S2_V1 = ('<section class="screen" id="s2">\n  <div class="frame"><h2 data-proto-id="s2-h">Tydzień</h2>'
           '<button type="button" data-proto-id="s2-btn-a">A</button></div>\n</section>\n')
R_S2_V2 = R_S2_V1.replace('data-proto-id="s2-btn-a">A<', 'data-proto-id="s2-btn-b">B<')
R_S3 = '<section class="screen" id="s3">\n  <div class="frame"><h2 data-proto-id="s3-h">Inny</h2></div>\n</section>\n'
R_S3_V2 = R_S3.replace('Inny', 'Inny zmieniony')
ACC2 = 'must.acceptor@example.com'   # akceptujący z hasłem domyślnym (mustChange)
RESTORE_URL = '/_api/v1/prototypes/demo/restore'
PREVIEW_URL = '/_api/v1/prototypes/demo/restore-preview'


def r_page(s2=R_S2_V1, s3=R_S3, head=R_HEAD):
    return head + R_S1 + s2 + s3 + R_TAIL


def element_anchor(proto_id):
    return {'kind': 'element', 'protoId': proto_id, 'x': 0.5, 'y': 0.5, 'iteration': 1}


class RestoreServerCase(ServerCase):
    """`demo` with the screens s1 (example), s2, s3: iteration 1 is frozen (s2 has `s2-btn-a`), the current page
    has `s2-btn-b` and a changed s3.  Clients: `acc` (acceptor), `dev` (developer), `com` (commenter)."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.demo, 'index.html'), r_page())
        write(os.path.join(self.demo, 'screens.css'), 'body { margin: 0 }\n')
        write(os.path.join(self.demo, 'README.md'), 'Opis\n')
        self.store = ps.CommentsStore(self.demo, 'demo', clock=self.clock)
        ps.perform_iterate(self.store, self.demo)
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V2, R_S3_V2))
        self.start_server()
        self.acc, self.dev, self.com = self.client(C), self.client(D), self.client(A)

    def current_version(self):
        return self.anon.operations('demo').json['version']

    def body(self, **overrides):
        body = {'cid': uid('r'), 'screen': 's2', 'from': 1, 'reason': 'wróć', 'version': self.current_version()}
        body.update(overrides)
        return {key: value for key, value in body.items() if value is not None}

    def restore(self, client, body=None, headers=None, **overrides):
        return client.post(RESTORE_URL, self.body(**overrides) if body is None else body, headers=headers)

    def preview(self, client, query='?screen=s2&from=1'):
        return client.get(PREVIEW_URL + query)

    def files(self):
        iterations = os.path.join(self.demo, 'iterations')
        return {'index': read_file_bytes_of(self.demo, 'index.html'), 'journal': self.journal(),
                'iterations': sorted(os.listdir(iterations)) if os.path.isdir(iterations) else []}

    def index_text(self):
        return read_file_bytes_of(self.demo, 'index.html').decode('utf-8')

    def assert_untouched(self, before):
        self.assertEqual(self.files(), before, 'odmowa nie może nic zapisać')


def read_file_bytes_of(directory, name):
    with open(os.path.join(directory, name), 'rb') as handle:
        return handle.read()


class OperationsHashTests(RestoreServerCase):
    def listing(self):
        return self.check(self.anon.operations('demo'), 200).json

    def test_listing_has_version_screens_and_iterations(self):
        listing = self.listing()
        self.assertRegex(listing['version'], r'^[0-9a-f]{64}$')
        self.assertEqual(set(listing['screens']), {'s2', 's3'}, 'the example screen s1 is not hashed')
        for value in listing['screens'].values():
            self.assertRegex(value, r'^[0-9a-f]{64}$')
        self.assertEqual(listing['iterations'], [{'number': 1, 'path': 'iterations/1/', 'exists': True}])
        self.assertEqual(self.listing()['version'], listing['version'], 'the version is stable between calls')

    def test_k25_what_changes_the_hash_of_a_screen(self):
        base = self.listing()
        index = os.path.join(self.demo, 'index.html')
        text = self.index_text()
        cases = [
            ('text of screen s2', 'index.html', text.replace('Tydzień', 'Tydzień dłuższy'), {'s2'}),
            ('text of another screen', 'index.html', text.replace('Inny zmieniony', 'Inny jeszcze bardziej'), {'s3'}),
            ('shared CSS', 'screens.css', 'body { margin: 1px; padding: 0 }\n', {'s2', 's3'}),
            ('<head> of the page', 'index.html', text.replace('<title>Demo</title>', '<title>Demo 2</title>'), {'s2', 's3'}),
            ('asset in a subdirectory', 'img/logo.svg', '<svg></svg>\n', {'s2', 's3'}),
        ]
        for name, relative, content, changed in cases:
            with self.subTest(change=name):
                path = os.path.join(self.demo, relative)
                before = read_file_bytes_of(self.demo, relative) if os.path.exists(path) else None
                write(path, content)
                after = self.listing()
                moved = {screen for screen in base['screens'] if after['screens'][screen] != base['screens'][screen]}
                self.assertEqual(moved, changed)
                self.assertNotEqual(after['version'], base['version'])
                if before is None:
                    os.unlink(path)
                else:
                    write(path, before)
                self.assertEqual(self.listing()['screens'], base['screens'], 'restoring the file restores the hash')

    def test_k25_what_does_not_change_the_hash(self):
        base = self.listing()
        for relative, content in (('README.md', 'Zupełnie inny opis, dłuższy\n'), ('prototype.json', '{"title": "Inny tytuł"}\n'),
                                  ('prototype.js', '/* inny silnik */\n')):
            with self.subTest(file=relative):
                write(os.path.join(self.demo, relative), content)
                self.assertEqual(self.listing()['screens'], base['screens'])
                self.assertEqual(self.listing()['version'], base['version'])
        client = self.client(A)
        self.seed(client, 'th-1', 'm-1')  # comments.json
        self.assertEqual(self.listing()['version'], base['version'], 'a comment does not change the version')
        ps.perform_iterate(self.store, self.demo)  # a new iteration directory
        self.assertEqual(self.listing()['version'], base['version'], 'iterations/ is not a part of the version')
        text = self.index_text().replace('Tydzień', 'Tydzień nowy')  # control: the check can fail
        write(os.path.join(self.demo, 'index.html'), text)
        self.assertNotEqual(self.listing()['version'], base['version'])

    def test_line_endings_do_not_change_the_hash(self):
        base = self.listing()
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace('\n', '\r\n'))
        write(os.path.join(self.demo, 'screens.css'), 'body { margin: 0 }\r\n')
        self.assertEqual(self.listing()['screens'], base['screens'])
        self.assertEqual(self.listing()['version'], base['version'])

    def test_same_size_edit_is_noticed_through_the_modification_time(self):
        base = self.listing()
        path = os.path.join(self.demo, 'screens.css')
        write(path, 'body { margin: 9 }\n')  # the same length as before
        stat_before = os.stat(path)
        os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns + 5_000_000_000))
        self.assertNotEqual(self.listing()['version'], base['version'])

    def test_iterations_listing_follows_the_operations_and_the_directories(self):
        os.makedirs(os.path.join(self.demo, 'iterations', '9'))
        write(os.path.join(self.demo, 'iterations', '9', 'index.html'), '<html></html>')  # a directory without an operation
        self.store.append([{'type': 'iteration', 'by': 'agent',
                            'payload': {'number': 4, 'path': 'iterations/4/', 'hash': 'a' * 64}},
                           {'type': 'iteration', 'by': 'agent',
                            'payload': {'number': 4, 'path': 'iterations/4/', 'hash': 'b' * 64}}])  # duplicated number
        self.assertEqual(self.listing()['iterations'], [
            {'number': 1, 'path': 'iterations/1/', 'exists': True},
            {'number': 4, 'path': 'iterations/4/', 'exists': False}])  # the listing has no 9 and no duplicate

    def test_frozen_page_is_served_without_the_journal_and_staging_directories_are_hidden(self):
        frozen = self.anon.get('/demo/iterations/1/index.html')
        self.check(frozen, 200)
        self.assertIn('data-prototype-iteration="1" data-prototype-frozen', frozen.text)
        self.check(self.anon.get('/demo/comments.json'), 200)  # control: the live journal is served, the frozen copy has none
        self.check(self.anon.get('/demo/iterations/1/comments.json'), 404)
        os.makedirs(os.path.join(self.demo, 'iterations', '2.tmp-ab12'))
        write(os.path.join(self.demo, 'iterations', '2.tmp-ab12', 'x.html'), 'połowa')
        self.check(self.anon.get('/demo/iterations/2.tmp-ab12/x.html'), 404)
        self.check(self.anon.get('/demo/iterations/1/index.html'), 200)  # control


class RestoreApiTests(RestoreServerCase):
    def test_acceptor_restores_one_screen_and_the_response_has_the_pair(self):
        prefix_before = op_lines(self.journal())
        response = self.check(self.restore(self.acc), 201)
        data = response.json
        kinds = [operation['type'] for operation in data['operations']]
        self.assertEqual(kinds, ['iteration', 'restore'])
        self.assertEqual(data['iteration'], {'current': 3})
        iteration, restore = data['operations']
        self.assertEqual((iteration['by'], restore['by']), (C, C))
        self.assertEqual(iteration['payload']['number'], 2)
        self.assertNotIn('who', restore['payload'], 'the browser restore has no `who`')
        self.assertEqual((restore['payload']['screen'], restore['payload']['from'], restore['payload']['frozen']),
                         ('s2', 1, 2))
        self.assertNotEqual(restore['payload']['hashBefore'], restore['payload']['hashAfter'])
        self.assertEqual(iteration['cid'], restore['cid'], 'the cid is on both operations')
        text = self.index_text()
        self.assertIn(R_S2_V1, text)
        self.assertNotIn(R_S2_V2, text)
        self.assertIn(R_S3_V2, text, 'only the section of s2 is replaced')
        self.assertIn(R_S2_V2, read_file_bytes_of(os.path.join(self.demo, 'iterations', '2'), 'index.html').decode('utf-8'),
                      'iteration 2 is the version from before the restore')
        self.assertEqual(op_lines(self.journal())[:len(prefix_before)], prefix_before, 'K2: the prefix stays')
        assert_canonical_text(self, self.journal())
        listing = self.anon.operations('demo').json
        self.assertEqual([item['number'] for item in listing['iterations']], [1, 2])
        self.assertEqual(listing['screens']['s2'], restore['payload']['hashAfter'])

    def test_developer_may_restore_too_and_a_second_restore_undoes_the_first(self):
        self.check(self.restore(self.dev), 201)
        undo = self.check(self.restore(self.dev, **{'from': 2}), 201).json
        self.assertEqual(undo['iteration'], {'current': 4})
        self.assertIn(R_S2_V2, self.index_text())

    def test_repeated_cid_returns_the_same_pair_without_a_write_even_when_the_version_is_stale(self):
        body = self.body()
        first = self.check(self.restore(self.acc, body), 201).json
        before = self.files()
        again = self.check(self.restore(self.acc, body), 200).json  # `version` is stale now: dedup comes first
        self.assertEqual([item['id'] for item in again['operations']], [item['id'] for item in first['operations']])
        self.assertEqual(self.files(), before)
        self.check(self.restore(self.acc, dict(body, cid=uid('r'))), 409, 'stale_view')  # control: a new cid is refused

    def test_without_a_session_is_401_and_a_commenter_is_403_forbidden(self):
        before = self.files()
        self.check(self.restore(self.anon), 401)
        self.check(self.restore(self.com), 403, 'forbidden')
        self.assert_untouched(before)
        self.check(self.restore(self.acc), 201)  # control: the same body from an acceptor

    def test_csrf_origin_and_content_type_are_checked_before_the_role(self):
        before = self.files()
        for headers in ({'X-Skillsync-CSRF': None}, {'X-Skillsync-CSRF': 'zly-token'}, {'Origin': 'http://evil.example'},
                        {'Content-Type': 'text/plain'}):
            with self.subTest(headers=headers):
                self.check(self.restore(self.acc, headers=headers), 403)
        self.assert_untouched(before)
        self.check(self.restore(self.acc, headers={'Origin': 'http://127.0.0.1:%d' % self.server.port}), 201)

    def test_csrf_is_checked_before_the_stale_version(self):
        stale = self.restore(self.acc, version='f' * 64, headers={'X-Skillsync-CSRF': None})
        self.check(stale, 403)
        self.check(self.restore(self.acc, version='f' * 64), 409, 'stale_view')

    def test_account_that_must_change_the_password_is_403(self):
        users = ps.UsersFile(self.users_path, iterations=ITER, clock=self.clock)
        ps.add_user(users, ACC2, role='acceptor', default_password=DEFAULT_PW)
        must = Client(self.server)
        self.check(must.login(ACC2, DEFAULT_PW), 200)
        before = self.files()
        self.check(self.restore(must), 403, 'password_change_required')
        self.assert_untouched(before)

    def test_oversized_body_is_413(self):
        body = json.dumps(self.body()).encode('utf-8')
        before = self.files()
        self.check(self.acc.post(RESTORE_URL, raw=body + b' ' * (64 * 1024 + 1 - len(body))), 413)
        self.assert_untouched(before)
        self.check(self.restore(self.acc), 201)

    def test_schema_violations_are_400_without_a_write(self):
        before = self.files()
        cases = [
            ('by in the body', {'by': C}, 'by_not_allowed'),
            ('unknown field', {'extra': 1}, 'invalid_request'),
            ('empty reason', {'reason': '   '}, None),
            ('too long reason', {'reason': 'x' * 501}, None),
            ('version that is not a hash', {'version': 'abc'}, None),
            ('from as text', {'from': '1'}, None),
            ('from zero', {'from': 0}, None),
            ('from as float', {'from': 1.5}, None),
            ('screen that is not an identifier', {'screen': '2 ekran'}, None),
            ('missing version', {'version': None}, None),
            ('missing screen', {'screen': None}, None),
        ]
        for name, overrides, error in cases:
            with self.subTest(case=name):
                response = self.restore(self.acc, **overrides)
                self.check(response, 400, error)
        self.assert_untouched(before)
        self.check(self.restore(self.acc, reason='x' * 500), 201)  # control: 500 characters are fine

    def test_missing_cid_is_400_cid_required(self):
        body = self.body()
        del body['cid']
        before = self.files()
        self.check(self.restore(self.acc, body), 400, 'cid_required')
        self.check(self.restore(self.acc, dict(body, cid='')), 400, 'cid_required')
        self.assert_untouched(before)
        self.check(self.restore(self.acc, dict(body, cid=uid('r'))), 201)

    def test_unknown_prototype_is_404_and_a_v1_prototype_is_409_needs_migration_for_every_role(self):
        url = '/_api/v1/prototypes/%s/restore'
        body = self.body()
        for client in (self.acc, self.com):
            self.check(client.post(url % 'nie-ma', body), 404)
            self.check(client.post(url % 'old', body), 409, 'needs_migration')
        self.check(self.restore(self.acc, body), 201)

    def test_stale_version_is_409_without_a_write_and_a_fresh_version_passes(self):
        stale = self.body()
        write(os.path.join(self.demo, 'screens.css'), 'body { margin: 0; color: red }\n')  # the page changed after loading
        before = self.files()
        self.check(self.restore(self.acc, stale), 409, 'stale_view')
        self.check(self.restore(self.acc, version='0' * 64), 409, 'stale_view')
        self.assert_untouched(before)
        self.check(self.restore(self.acc), 201)  # control: the version read now

    def test_plan_errors_have_their_codes_and_write_nothing(self):
        before = self.files()
        self.check(self.restore(self.acc, **{'from': 99}), 404, 'iteration_not_found')
        self.check(self.restore(self.acc, screen='s9'), 409, 'screen_missing')
        self.assert_untouched(before)
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V2, R_S3_V2.replace('</section>', R_S2_V1.replace('s2', 'sx').replace('id="sx"', 'id="s5"') + '</section>')))
        before = self.files()
        self.check(self.restore(self.acc, screen='s3'), 409, 'ambiguous_section')
        self.assert_untouched(before)
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V2, R_S3_V2.replace('s3-h', 's2-btn-a')))
        before = self.files()
        failed = self.check(self.restore(self.acc), 409, 'ids_check_failed')
        self.assertEqual(failed.json['duplicates'], ['s2-btn-a'])  # the details are flat in the body
        self.assertEqual((failed.json['missing'], failed.json['invalid']), ([], []))
        self.assert_untouched(before)
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V2, R_S3_V2))
        self.check(self.restore(self.acc), 201)  # control

    def test_an_orphan_iteration_directory_is_409_incomplete_restore(self):
        shutil.copytree(os.path.join(self.demo, 'iterations', '1'), os.path.join(self.demo, 'iterations', '2'))
        before = self.files()
        self.check(self.restore(self.acc), 409, 'incomplete_restore')
        self.assert_untouched(before)
        shutil.rmtree(os.path.join(self.demo, 'iterations', '2'))
        self.check(self.restore(self.acc), 201)

    def test_order_of_checks_role_before_version_and_schema_before_version(self):
        before = self.files()
        self.check(self.restore(self.com, version='f' * 64), 403, 'forbidden')   # role before the stale version
        self.check(self.restore(self.acc, version='f' * 64, reason=''), 400)     # schema before the stale version
        self.check(self.restore(self.acc, version='f' * 64, **{'from': 99}), 409, 'stale_view')  # version before the plan
        self.assert_untouched(before)
        self.check(self.restore(self.acc, **{'from': 99}), 404, 'iteration_not_found')

    def test_order_of_checks_cid_and_prototype_before_the_role(self):
        body = self.body()
        del body['cid']
        self.check(self.restore(self.com, body), 400, 'cid_required')
        self.check(self.com.post('/_api/v1/prototypes/nie-ma/restore', self.body()), 404)
        self.check(self.restore(self.com), 403, 'forbidden')

    def test_get_restore_is_not_allowed(self):
        self.check(self.acc.get(RESTORE_URL), 405)

    def test_restore_is_rejected_for_the_operations_endpoint(self):
        payload = {'screen': 's2', 'from': 1, 'frozen': 2, 'reason': 'bo', 'hashBefore': 'a' * 64, 'hashAfter': 'b' * 64}
        before = self.files()
        for client in (self.acc, self.dev):
            self.check(client.op('demo', 'restore', None, payload), 403, 'cli_only')
            self.check(client.op('demo', 'iteration', None, {'number': 2, 'path': 'iterations/2/', 'hash': 'a' * 64}),
                       403, 'cli_only')
        self.assert_untouched(before)


class RestoreLockTests(RestoreServerCase):
    lock_timeout = 0.4

    def test_503_while_the_prototype_lock_is_held_then_the_restore_passes(self):
        before = self.files()
        body = self.body()
        with ps.FileLock(os.path.join(self.demo, 'comments.json.lock'), timeout=5):
            blocked = self.restore(self.acc, body)
        self.check(blocked, 503)
        self.assert_untouched(before)
        self.check(self.restore(self.acc, body), 201)  # the same request after the release


class RestoreNoSourceWritesTests(RestoreServerCase):
    def setUp(self):
        super().setUp()
        self.start_server(source_writes=False)
        self.acc, self.dev, self.com = self.client(C), self.client(D), self.client(A)

    def test_session_reports_it_and_restore_is_403_source_writes_disabled_for_privileged_roles(self):
        self.assertFalse(self.acc.get('/_api/v1/session').json['server']['sourceWrites'])
        before = self.files()
        for client in (self.acc, self.dev):
            self.check(self.restore(client), 403, 'source_writes_disabled')
        self.check(self.restore(self.com), 403, 'source_writes_disabled')   # before the role check
        self.check(self.restore(self.anon), 401)
        self.assert_untouched(before)

    def test_control_the_same_request_passes_on_a_server_that_writes_sources(self):
        other = self.start_server(source_writes=True)
        client = Client(other)
        self.check(client.login(C, PASSWORDS[C]), 200)
        self.assertTrue(client.get('/_api/v1/session').json['server']['sourceWrites'])
        self.check(client.post(RESTORE_URL, self.body()), 201)

    def test_the_disabled_server_does_not_touch_staging_directories_but_registers_an_orphan(self):
        staging = os.path.join(self.demo, 'iterations', '5.tmp-abcd')
        os.makedirs(staging)
        self.start_server(source_writes=False)
        self.assertTrue(os.path.isdir(staging), 'sources are not changed with --no-source-writes')
        self.start_server(source_writes=True)  # control: a writing server removes it
        self.assertFalse(os.path.exists(staging))


class RestorePreviewApiTests(RestoreServerCase):
    def setUp(self):
        super().setUp()
        a = self.client(A)
        self.check(a.op('demo', 'create', 'th-a', create_payload('m-a', 'on a', 's2', element_anchor('s2-btn-a'))), 201)
        self.check(a.op('demo', 'create', 'th-b', create_payload('m-b', 'on b', 's2', element_anchor('s2-btn-b'))), 201)
        self.check(a.op('demo', 'create', 'th-c', create_payload('m-c', 'other screen', 's3', element_anchor('s3-h'))), 201)
        self.check(a.op('demo', 'create', 'th-d', create_payload('m-d', 'screen pin', 's2', anchor('screen'))), 201)
        self.check(a.op('demo', 'create', 'th-e', create_payload('m-e', 'closed', 's2', element_anchor('s2-btn-a'))), 201)
        self.check(a.op('demo', 'resolve', 'th-e', {'reason': 'author'}), 201)
        self.check(self.dev.op('demo', 'create', 'th-f', create_payload('m-f', 'deleted', 's2', element_anchor('s2-btn-a'))), 201)
        self.check(self.dev.op('demo', 'delete', 'th-f', {}), 201)

    def test_counts_visible_unfinished_threads_of_the_screen_with_an_element_anchor(self):
        for client in (self.acc, self.dev):
            data = self.check(self.preview(client), 200).json
            self.assertEqual(data, {'threadsBack': 1, 'threadsLost': 1, 'acceptanceLost': 0, 'unchanged': False})
        self.assertEqual(self.check(self.preview(self.acc, '?screen=s3&from=1'), 200).json['threadsLost'], 0)

    def test_preview_writes_nothing_and_matches_what_the_restore_does(self):
        before = self.files()
        preview = self.check(self.preview(self.acc), 200).json
        self.assert_untouched(before)
        self.check(self.restore(self.acc), 201)
        after = self.preview(self.acc, '?screen=s2&from=2')  # restoring the frozen pre-restore version flips the numbers
        self.assertEqual((after.json['threadsBack'], after.json['threadsLost']), (preview['threadsLost'], preview['threadsBack']))

    def test_access_401_then_403_then_parameters_then_plan(self):
        bad = '?screen=zly&from=0'
        self.check(self.preview(self.anon, bad), 401)
        self.check(self.preview(self.com, bad), 403, 'forbidden')
        self.check(self.preview(self.acc, bad), 400)
        self.check(self.preview(self.acc, '?screen=s2&from=99'), 404, 'iteration_not_found')
        self.check(self.preview(self.acc, '?screen=s9&from=1'), 409, 'screen_missing')
        self.check(self.preview(self.acc), 200)  # control

    def test_bad_parameters_are_400(self):
        for query in ('', '?screen=s2', '?from=1', '?screen=s2&from=', '?screen=s2&from=abc', '?screen=s2&from=0',
                      '?screen=s2&from=-1', '?screen=s%202&from=1', '?screen=2s&from=1', '?screen=s2&from=1.5'):
            with self.subTest(query=query):
                self.check(self.preview(self.acc, query), 400)
        self.check(self.preview(self.acc, '?screen=s2&from=1'), 200)

    def test_unknown_and_v1_prototypes(self):
        self.check(self.acc.get('/_api/v1/prototypes/nie-ma/restore-preview?screen=s2&from=1'), 404)
        self.check(self.acc.get('/_api/v1/prototypes/old/restore-preview?screen=s2&from=1'), 409, 'needs_migration')

    def test_preview_is_not_public_even_when_reading_is(self):
        self.assertFalse(self.login_to_view)
        self.check(self.anon.operations('demo'), 200)   # reading is public
        self.check(self.preview(self.anon), 401)         # the preview is not
        self.check(self.preview(self.com), 403, 'forbidden')

    def test_post_is_not_allowed(self):
        self.check(self.acc.post(PREVIEW_URL + '?screen=s2&from=1', {}), 405)


class RestoreUnchangedApiTests(RestoreServerCase):
    """D27: restoring a screen whose section equals the current one is refused as a no-op (200, no write)."""

    def make_s2_equal_to_iteration_1(self):
        write(os.path.join(self.demo, 'index.html'), r_page(R_S2_V1, R_S3_V2))

    def test_preview_has_unchanged_always_and_it_follows_the_section(self):
        self.assertIs(self.check(self.preview(self.acc), 200).json['unchanged'], False, 'control: s2 differs')
        self.make_s2_equal_to_iteration_1()
        data = self.check(self.preview(self.acc), 200).json
        self.assertEqual(data, {'threadsBack': 0, 'threadsLost': 0, 'acceptanceLost': 0, 'unchanged': True})
        self.assertIs(self.check(self.preview(self.acc, '?screen=s3&from=1'), 200).json['unchanged'], False,
                      'another screen of the same page still differs')

    def test_post_of_an_unchanged_screen_is_200_without_any_write_and_a_repeat_gives_the_same(self):
        self.make_s2_equal_to_iteration_1()
        before = self.files()
        body = self.body()
        first = self.check(self.restore(self.acc, body), 200).json
        self.assertEqual(first, {'unchanged': True, 'version': self.current_version()})
        self.assert_untouched(before)
        self.assertEqual(self.files()['iterations'], ['1'], 'nothing is frozen')
        again = self.check(self.restore(self.acc, body), 200).json  # the same cid: nothing was stored under it
        self.assertEqual(again, first)
        self.assert_untouched(before)
        self.assertEqual(self.check(self.anon.operations('demo'), 200).json['iteration'], {'current': 2})
        changed = self.check(self.restore(self.acc, screen='s3'), 201).json  # control: a changed screen is restored
        self.assertEqual([item['type'] for item in changed['operations']], ['iteration', 'restore'])

    def test_unchanged_is_the_last_check(self):
        self.make_s2_equal_to_iteration_1()
        before = self.files()
        self.check(self.restore(self.com), 403, 'forbidden')
        self.check(self.restore(self.acc, version='f' * 64), 409, 'stale_view')
        self.check(self.restore(self.acc, **{'from': 9}), 404, 'iteration_not_found')
        self.check(self.restore(self.acc, reason=' '), 400)
        self.assert_untouched(before)
        self.check(self.restore(self.acc), 200)  # control: the same request with valid input

    def test_a_cid_of_a_real_restore_is_deduplicated_before_the_unchanged_check(self):
        body = self.body()
        first = self.check(self.restore(self.acc, body), 201).json  # s2 is now equal to iteration 1
        again = self.check(self.restore(self.acc, body), 200).json
        self.assertEqual([item['id'] for item in again['operations']], [item['id'] for item in first['operations']])
        self.assertNotIn('unchanged', again)
        fresh = self.check(self.restore(self.acc, cid=uid('r')), 200).json  # control: a new cid meets D27
        self.assertEqual(fresh, {'unchanged': True, 'version': self.current_version()})


class RestorePreviewNoSourceWritesTests(RestoreServerCase):
    source_writes = False

    def test_privileged_roles_get_403_source_writes_disabled_and_the_others_keep_their_codes(self):
        self.check(self.preview(self.anon), 401)
        self.check(self.preview(self.com), 403, 'forbidden')
        for client in (self.acc, self.dev):
            self.check(self.preview(client), 403, 'source_writes_disabled')


class RestoreMarkerServerTests(RestoreServerCase):
    """F06: a restore undone by hand is never repeated; recovery that writes says so."""

    MARKER = '.restore-pending.tmp'

    def undo_by_hand(self):
        self.check(self.restore(self.acc), 201)
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace(R_S2_V1, R_S2_V2))

    def test_a_restore_after_undoing_one_by_hand_is_201_and_keeps_the_undo(self):
        self.undo_by_hand()
        response = self.check(self.restore(self.acc, screen='s3'), 201).json
        self.assertNotIn('recovery', response)
        text = self.index_text()
        self.assertIn(R_S2_V2, text, 'the undo by hand stays')
        self.assertIn(R_S3, text, 'control: the requested screen is restored')

    def test_server_start_after_undoing_a_restore_by_hand_keeps_the_undo(self):
        self.undo_by_hand()
        server = self.start_server()
        self.assertIn(R_S2_V2, self.index_text())
        self.assertFalse(any('odtwarzanie' in warning for warning in server.app.warnings), server.app.warnings)

    def test_an_interrupted_restore_is_finished_and_the_response_says_so(self):
        real = ps.atomic_write_bytes

        def fail_on_index(path, data, *args, **kwargs):
            if os.path.basename(path) == 'index.html' and os.sep + 'iterations' + os.sep not in path:
                raise OSError('przerwanie przed zapisem index.html')
            return real(path, data, *args, **kwargs)

        with mock.patch.object(ps, 'atomic_write_bytes', fail_on_index):
            with self.assertRaises(OSError):
                ps.perform_restore(self.store, self.demo, 's2', 1, 'powód', by=C)
        self.assertTrue(os.path.exists(os.path.join(self.demo, self.MARKER)))
        version = self.current_version()
        response = self.check(self.restore(self.acc, screen='s3', version=version), 409, 'stale_view').json
        self.assertTrue(any('finished the interrupted restore of s2' in note for note in response['recovery']), response)
        self.assertIn(R_S2_V1, self.index_text())
        self.assertFalse(os.path.exists(os.path.join(self.demo, self.MARKER)))
        self.check(self.restore(self.acc, screen='s3'), 201)  # control: with the fresh version it passes

    def test_s1_2_an_error_after_recovery_carries_the_recovery_notes(self):
        os.makedirs(os.path.join(self.demo, 'iterations', '2.tmp-ab12'))   # recovery removes it; the version stays
        response = self.check(self.restore(self.acc, **{'from': 9}), 404, 'iteration_not_found').json
        self.assertTrue(any('removed the unfinished freeze iterations/2.tmp-ab12/' in note
                            for note in response.get('recovery', [])), response)
        plain = self.check(self.restore(self.acc, **{'from': 9}), 404, 'iteration_not_found').json
        self.assertNotIn('recovery', plain, 'control: nothing was recovered the second time')

    def test_a_stale_marker_at_start_is_reported_and_removed_without_a_write(self):
        write(os.path.join(self.demo, self.MARKER), '{"screen":"s2","from":1,"frozen":7,"hashBefore":"%s","hashAfter":"%s"}'
              % ('a' * 64, 'b' * 64))
        before = self.files()
        server = self.start_server()
        self.assertTrue(any('never reached the log' in warning for warning in server.app.warnings), server.app.warnings)
        self.assertFalse(os.path.exists(os.path.join(self.demo, self.MARKER)))
        self.assertEqual(self.files(), before)

    def test_the_marker_is_not_served_and_not_hashed(self):
        base = self.anon.operations('demo').json
        write(os.path.join(self.demo, self.MARKER), '{}')
        self.check(self.anon.get('/demo/' + self.MARKER), 404)
        self.check(self.anon.get('/demo/index.html'), 200)  # control
        after = self.anon.operations('demo').json
        self.assertEqual((after['screens'], after['version']), (base['screens'], base['version']))


class RestoreStartupRecoveryTests(RestoreServerCase):
    """Recovery at the start of the server (`by: server`)."""

    def last_ops(self, count):
        return file_operations(self.demo)[-count:]

    def test_an_orphan_equal_to_the_current_version_is_registered_as_an_iteration_by_server(self):
        with self.store.lock():
            ps.freeze_iteration(self.demo, 2)
        self.assertEqual(len(file_operations(self.demo)), 1)
        server = self.start_server()
        operation = self.last_ops(1)[0]
        self.assertEqual((operation['type'], operation['by'], operation['payload']['number']), ('iteration', 'server', 2))
        self.assertTrue(any('odtwarzanie' in warning for warning in server.app.warnings), server.app.warnings)
        total = len(file_operations(self.demo))
        self.start_server()   # control: a second start has nothing to do
        self.assertEqual(len(file_operations(self.demo)), total)

    def test_staging_directories_are_removed(self):
        staging = os.path.join(self.demo, 'iterations', '2.tmp-ab12')
        os.makedirs(staging)
        write(os.path.join(staging, 'x.html'), 'połowa')
        self.start_server()
        self.assertFalse(os.path.exists(staging))
        self.assertTrue(os.path.isdir(os.path.join(self.demo, 'iterations', '1')), 'finished iterations stay')

    def test_an_orphan_that_differs_is_reported_and_the_server_still_reads(self):
        shutil.copytree(os.path.join(self.demo, 'iterations', '1'), os.path.join(self.demo, 'iterations', '2'))
        total = len(file_operations(self.demo))
        server = self.start_server()
        self.assertTrue(any('incomplete_restore' in warning for warning in server.app.warnings), server.app.warnings)
        self.assertEqual(len(file_operations(self.demo)), total)
        self.check(Client(server).operations('demo'), 200)

    def test_a_restore_interrupted_before_the_page_write_is_finished_at_start(self):
        real = ps.atomic_write_bytes

        def fail_on_index(path, data, *args, **kwargs):
            if os.path.basename(path) == 'index.html' and os.sep + 'iterations' + os.sep not in path:
                raise OSError('przerwanie przed zapisem index.html')
            return real(path, data, *args, **kwargs)

        with mock.patch.object(ps, 'atomic_write_bytes', fail_on_index):
            with self.assertRaises(OSError):
                ps.perform_restore(self.store, self.demo, 's2', 1, 'powód', by=C)
        self.assertIn(R_S2_V2, self.index_text(), 'step (4) did not happen')
        self.assertEqual([op['type'] for op in self.last_ops(2)], ['iteration', 'restore'])
        self.start_server()
        self.assertIn(R_S2_V1, self.index_text(), 'step (4) was repeated at the start')
        self.assertEqual([op['type'] for op in self.last_ops(2)], ['iteration', 'restore'], 'no new operations')

    def test_a_restore_that_is_not_the_last_operation_is_not_repeated_at_start(self):
        self.check(self.restore(self.acc), 201)
        write(os.path.join(self.demo, 'index.html'), self.index_text().replace(R_S2_V1, R_S2_V2))  # undone by hand
        self.seed(self.client(A), 'th-9', 'm-9')                                                    # restore is not last
        self.start_server()
        self.assertIn(R_S2_V2, self.index_text())
        self.assertNotIn(R_S2_V1, self.index_text())


# ═════════════════════════════════════════════════════════════════════════════
# Widok wątku (D28b, K8-K10, D3, D4, D10): pole `view` w `create`
# ═════════════════════════════════════════════════════════════════════════════

def view_op(number, view=None, thread='th-v', message='Komentarz', with_view=True, **extra):
    payload = create_payload('m-v%d' % number, message)
    if with_view:
        payload['view'] = view
    payload.update(extra)
    return {'id': raw_id(number), 'type': 'create', 'threadId': thread, 'at': raw_at(number), 'by': A, 'payload': payload}


def _threads(folded):
    return folded['threads'] if isinstance(folded, dict) else folded


BAD_VIEWS = ('tablet', 'Mobile', 'DESKTOP', '', 5, 0, True, None, ['mobile'], {'mobile': 1})


class ViewValidationTests(unittest.TestCase):
    def test_desktop_mobile_and_missing_view_are_accepted(self):
        for payload in (create_payload('m-1'), dict(create_payload('m-1'), view='desktop'),
                        dict(create_payload('m-1'), view='mobile')):
            with self.subTest(view=payload.get('view', '(brak)')):
                ps.validate_payload('create', payload, 'browser')

    def test_any_other_value_or_type_is_400_naming_the_field(self):
        for value in BAD_VIEWS:
            with self.subTest(view=value), self.assertRaises(ps.PrototypeServerError) as caught:
                ps.validate_payload('create', dict(create_payload('m-1'), view=value), 'browser')
            self.assertEqual(caught.exception.status, 400)
            self.assertIn('view', caught.exception.message)

    def test_operation_from_a_file_is_validated_the_same_way(self):
        ps.validate_operation(view_op(1, 'mobile'), origin='file')  # kontrola
        for value in ('tablet', 5, None):
            with self.subTest(view=value), self.assertRaises(ps.PrototypeServerError) as caught:
                ps.validate_operation(view_op(1, value), origin='file')
            self.assertEqual(caught.exception.status, 400)

    def test_view_is_unknown_in_reanchor_and_other_types(self):
        good = {'screen': 's2', 'anchor': anchor()}
        ps.validate_payload('reanchor', good, 'browser')  # kontrola
        for value in ('desktop', 'mobile'):
            with self.subTest(view=value), self.assertRaises(ps.PrototypeServerError) as caught:
                ps.validate_payload('reanchor', dict(good, view=value), 'browser')
            self.assertEqual(caught.exception.status, 400)
            self.assertIn('view', caught.exception.message)
        with self.assertRaises(ps.PrototypeServerError):
            ps.validate_payload('reply', {'message': {'id': 'm-2', 'text': 'x'}, 'view': 'mobile'}, 'browser')


class ViewFoldTests(unittest.TestCase):
    """Fold Pythona serwera: widok wątku według D4 (K8, D3). Fold v1 w prototype.py nie zna widoku (v1 nie ma pola)."""

    FOLDS = (('serwer', ps.fold_operations),)

    def views(self, operations):
        result = []
        for name, fold in self.FOLDS:
            result.append((name, dict((t['id'], t['view']) for t in _threads(fold(operations)))))
        return result

    def test_only_exactly_mobile_gives_mobile(self):
        cases = [('th-m', 'mobile', 'mobile'), ('th-d', 'desktop', 'desktop')]
        for thread, value, expected in cases:
            for name, views in self.views([view_op(1, value, thread)]):
                with self.subTest(fold=name, view=value):
                    self.assertEqual(views, {thread: expected})

    def test_missing_field_is_desktop(self):
        for name, views in self.views([view_op(1, thread='th-n', with_view=False)]):
            with self.subTest(fold=name):
                self.assertEqual(views, {'th-n': 'desktop'})

    def test_invalid_values_in_a_hand_written_file_fall_back_to_desktop(self):
        for value in BAD_VIEWS:
            for name, views in self.views([view_op(1, value, 'th-x')]):
                with self.subTest(fold=name, view=value):
                    self.assertEqual(views, {'th-x': 'desktop'})

    def test_a_skipped_create_does_not_decide_the_view(self):
        """`create` z pustą wiadomością (lub bez ekranu) jest pomijany; liczy się ten, z którego powstaje wątek."""
        empty = view_op(1, 'mobile', 'th-s', message='')
        empty['payload']['message'] = None
        real = view_op(2, 'desktop', 'th-s')
        for name, views in self.views([empty, real]):
            with self.subTest(fold=name, order='pusty mobile, potem desktop'):
                self.assertEqual(views, {'th-s': 'desktop'})
        empty2 = view_op(1, 'desktop', 'th-t')
        empty2['payload']['message'] = ''
        real2 = view_op(2, 'mobile', 'th-t')
        for name, views in self.views([empty2, real2]):
            with self.subTest(fold=name, order='pusty desktop, potem mobile'):
                self.assertEqual(views, {'th-t': 'mobile'})
        noscreen = view_op(1, 'mobile', 'th-u')
        del noscreen['payload']['screen']
        for name, views in self.views([noscreen, view_op(2, with_view=False, thread='th-u')]):
            with self.subTest(fold=name, order='bez ekranu'):
                self.assertEqual(views, {'th-u': 'desktop'})

    def test_a_second_create_with_the_same_thread_does_not_change_the_view(self):
        for name, views in self.views([view_op(1, 'desktop', 'th-w'), view_op(2, 'mobile', 'th-w')]):
            with self.subTest(fold=name):
                self.assertEqual(views, {'th-w': 'desktop'})
        for name, views in self.views([view_op(1, 'mobile', 'th-w'), view_op(2, 'desktop', 'th-w')]):
            with self.subTest(fold=name, reverse=True):
                self.assertEqual(views, {'th-w': 'mobile'})

    def test_later_operations_do_not_change_the_view(self):
        def later(number, kind, payload):
            return {'id': raw_id(number), 'type': kind, 'threadId': 'th-l', 'at': raw_at(number), 'by': A,
                    'payload': payload}
        later_ops = [later(2, 'reply', {'message': {'id': 'm-l2', 'text': 'x'}}),
                     later(3, 'reanchor', {'screen': 's2', 'anchor': anchor()}),
                     later(4, 'resolve', {'reason': 'author'}), later(5, 'reopen', {}),
                     later(6, 'edit', {'messageId': 'm-v1', 'text': 'zmiana'}),
                     later(7, 'vote', {'messageId': 'm-v1', 'value': 'up'})]
        for first, expected in (('mobile', 'mobile'), ('desktop', 'desktop')):
            for name, views in self.views([view_op(1, first, 'th-l')] + later_ops):
                with self.subTest(fold=name, first=first):
                    self.assertEqual(views, {'th-l': expected})

    def test_each_thread_has_its_own_view(self):
        operations = [view_op(1, 'mobile', 'th-a'), view_op(2, 'desktop', 'th-b'), view_op(3, 'mobile', 'th-c')]
        for name, views in self.views(operations):
            with self.subTest(fold=name):
                self.assertEqual(views, {'th-a': 'mobile', 'th-b': 'desktop', 'th-c': 'mobile'})

    def test_v1_migrated_threads_are_desktop(self):
        """Migracja v1 -> v2 (CLI: MigrateTests w test_skillsync_prototype_cli.py) nie zna widoku: wątki z v1 są `desktop`."""
        migrated = [{'id': raw_id(1), 'type': 'create', 'threadId': 'thread-0a1b2c3d-1111-4222-8333-444455556666',
                     'at': raw_at(1), 'by': 'import',
                     'payload': {'message': {'id': 'msg-0a1b2c3d-1111-4222-8333-444455556666', 'text': 'z v1',
                                             'importedAuthor': 'Recenzent A'},
                                 'screen': 's1', 'anchor': {'kind': 'screen', 'x': 0, 'y': 0, 'iteration': 1},
                                 'label': 'Nowy'}}]
        for name, views in self.views(migrated):
            with self.subTest(fold=name):
                self.assertEqual(views, {'thread-0a1b2c3d-1111-4222-8333-444455556666': 'desktop'})


class ViewHttpTests(ServerCase):
    def post_view(self, client, thread, view, **kwargs):
        payload = create_payload('m-' + thread)
        payload['view'] = view
        return client.op('demo', 'create', thread, payload, **kwargs)

    def test_create_with_desktop_and_mobile_is_stored_with_the_view_and_listed(self):
        a = self.client(A)
        for thread, view in (('th-d', 'desktop'), ('th-m', 'mobile')):
            created = self.check(self.post_view(a, thread, view), 201).json['operation']
            self.assertEqual(created['payload']['view'], view)
        self.check(a.op('demo', 'create', 'th-n', create_payload('m-th-n')), 201)  # brak pola
        stored = dict((o['threadId'], o['payload'].get('view', '(brak)')) for o in file_operations(self.demo))
        self.assertEqual(stored, {'th-d': 'desktop', 'th-m': 'mobile', 'th-n': '(brak)'})
        fold = ps.fold_operations(file_operations(self.demo))
        self.assertEqual(dict((t['id'], t['view']) for t in fold['threads']),
                         {'th-d': 'desktop', 'th-m': 'mobile', 'th-n': 'desktop'})

    def test_invalid_view_is_400_with_the_field_in_the_message_and_no_write(self):
        a = self.client(A)
        self.seed(a, 'th-1', 'm-1')
        before = self.journal()
        for value in BAD_VIEWS:
            with self.subTest(view=value):
                response = self.check(self.post_view(a, 'th-bad', value), 400)
                self.assertIn('view', response.text)
                self.assertEqual(self.journal(), before, 'zapis mimo odrzucenia')
        self.check(self.post_view(a, 'th-bad', 'mobile'), 201)  # kontrola

    def test_reanchor_with_view_is_400_and_does_not_touch_the_thread(self):
        a = self.client(A)
        self.seed(a, 'th-1', 'm-1')
        before = self.journal()
        response = self.check(a.op('demo', 'reanchor', 'th-1', {'screen': 's2', 'anchor': anchor(), 'view': 'mobile'}), 400)
        self.assertIn('view', response.text)
        self.assertEqual(self.journal(), before)
        self.check(a.op('demo', 'reanchor', 'th-1', {'screen': 's2', 'anchor': anchor()}), 201)  # kontrola

    def test_every_role_may_create_with_a_view_as_before(self):
        for index, login in enumerate((A, B, C, D)):
            with self.subTest(login=login):
                self.check(self.post_view(self.client(login), 'th-r%d' % index, 'mobile'), 201)

    def test_roles_and_csrf_rules_apply_to_create_with_view(self):
        before = self.journal()
        self.check(self.post_view(self.anon, 'th-anon', 'mobile'), 401)  # bez sesji (konto bez prawa pisania)
        a = self.client(A)
        url = '/_api/v1/prototypes/demo/operations'
        body = {'cid': 'c-vcsrf', 'type': 'create', 'threadId': 'th-csrf',
                'payload': dict(create_payload('m-csrf'), view='mobile')}
        self.check(a.post(url, body, headers={'X-Skillsync-CSRF': 'zly-token'}), 403)
        self.check(a.post(url, body, headers={'Content-Type': 'text/plain'}), 403)
        self.assertEqual(self.journal(), before, 'zapis mimo odmowy')
        self.check(self.post_view(a, 'th-ok', 'mobile'), 201)  # kontrola
        # obce operacje na wątku z widokiem nadal wg macierzy ról
        self.check(self.client(B).op('demo', 'delete', 'th-ok', {}), 403)
        self.check(self.client(D).op('demo', 'delete', 'th-ok', {}), 201)

    def test_session_lists_the_views_feature(self):
        for client in (self.anon, self.client(A)):
            server = self.check(client.get('/_api/v1/session'), 200).json['server']
            self.assertIsInstance(server['features'], list)
            self.assertIn('views', server['features'])


class ViewMergeTests(unittest.TestCase):
    def test_merge_operations_adds_a_create_with_view_unchanged(self):
        target = [view_op(1, 'desktop', 'th-1')]
        other = [view_op(1, 'desktop', 'th-1'), view_op(2, 'mobile', 'th-2')]
        merged, added = ps.merge_operations(target, other)
        self.assertEqual([o['id'] for o in added], [raw_id(2)])
        self.assertEqual(merged[-1]['payload']['view'], 'mobile')
        self.assertEqual(ps.serialize_operation(merged[-1]), ps.serialize_operation(other[1]))
        self.assertIn('"view":"mobile"', ps.serialize_operation(merged[-1]))
        views = dict((t['id'], t['view']) for t in ps.fold_operations(merged)['threads'])
        self.assertEqual(views, {'th-1': 'desktop', 'th-2': 'mobile'})

    def test_same_id_with_a_different_view_is_a_conflict(self):
        with self.assertRaises(ps.MergeConflict) as caught:
            ps.merge_operations([view_op(1, 'desktop', 'th-1')], [view_op(1, 'mobile', 'th-1')])
        self.assertEqual(caught.exception.conflicts[0]['reason'], 'same id, different content')

    def test_merge_refuses_a_create_with_an_invalid_view(self):
        with self.assertRaises(ps.MergeConflict) as caught:
            ps.merge_operations([], [view_op(2, 'tablet', 'th-2')])
        self.assertIn('view', caught.exception.conflicts[0]['reason'])


# ═════════════════════════════════════════════════════════════════════════════
# Moduł samodzielny jako proces (serve)
# ═════════════════════════════════════════════════════════════════════════════

class StandaloneServeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-standalone-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = FakeClock()
        self.root, self.users_path = build_world(self.tmp, self.clock)
        self.module_dir = os.path.join(self.tmp, 'host')
        os.makedirs(self.module_dir)
        self.module = os.path.join(self.module_dir, 'prototype_server.py')
        shutil.copy(str(MODULE), self.module)

    def start(self, *extra):
        kwargs = {}
        if POSIX:
            kwargs['preexec_fn'] = lambda: signal.signal(signal.SIGINT, signal.SIG_DFL)
        process = subprocess.Popen([sys.executable, '-I', self.module, 'serve', '--root', self.root, '--users',
                                    self.users_path, '--port', '0'] + list(extra), cwd=self.module_dir,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        self.addCleanup(self.stop, process)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line) for line in iter(process.stdout.readline, b'')],
                         daemon=True).start()
        try:
            line = lines.get(timeout=30).decode('utf-8')
        except queue.Empty:
            process.kill()
            self.fail('serwer nie wypisał adresu: %s' % process.stderr.read().decode('utf-8', 'replace'))
        match = re.search(r'http://127\.0\.0\.1:(\d+)/', line)
        self.assertTrue(match, line)
        return process, int(match.group(1))

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def test_serve_process_serves_public_files_and_writes_with_login(self):
        process, port = self.start()

        class Fake(object):
            pass
        fake = Fake()
        fake.port = port
        client = Client(fake)
        self.assertEqual(client.get('/demo/index.html').status, 200)
        response = client.login(A, PASSWORDS[A])
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.cookies()[0].split('=', 1)[0], 'skillsync_proto_%d' % port)
        self.assertEqual(client.op('demo', 'create', 'th-1', create_payload('m-1')).status, 201)
        assert_canonical_text(self, read_file_bytes(os.path.join(self.root, 'demo')))

    @needs_posix
    def test_ctrl_c_exits_zero_and_start_prints_public_read_warning(self):
        process, port = self.start()
        time.sleep(0.3)
        process.send_signal(signal.SIGINT)
        self.assertEqual(process.wait(30), 0)
        self.assertIn(ps.PUBLIC_READ_WARNING, process.stderr.read().decode('utf-8'))

    def test_serve_refuses_users_file_inside_root_with_exit_2(self):
        inside = os.path.join(self.root, 'users.json')
        shutil.copy(self.users_path, inside)
        os.chmod(inside, 0o600)
        run = subprocess.run([sys.executable, '-I', self.module, 'serve', '--root', self.root, '--users', inside,
                              '--port', '0'], cwd=self.module_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=60)
        self.assertEqual(run.returncode, 2, run.stdout)
        self.assertNotIn(b'Serwer prototyp', run.stdout)
        ok = self.start()  # kontrola: plik poza root startuje
        self.assertIsNotNone(ok)

    def test_serve_refuses_missing_users_file_with_exit_2(self):
        run = subprocess.run([sys.executable, '-I', self.module, 'serve', '--root', self.root, '--users',
                              os.path.join(self.tmp, 'brak.json'), '--port', '0'], cwd=self.module_dir,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(run.returncode, 2)


class TmpdirGuardSelfTests(unittest.TestCase):
    """Dowód, że strażnik potrafi paść (i że nie zgłasza fałszywych alarmów)."""

    def test_a_file_created_in_the_temporary_directory_is_reported(self):
        state = guard_begin()
        try:
            self.assertEqual(tempfile.gettempdir(), state['parent'])
            self.assertEqual(os.environ['TMPDIR'], state['parent'])
            Path(tempfile.gettempdir(), 'x').write_text('zabłąkany plik', encoding='utf-8')
            os.symlink('x', os.path.join(state['parent'], 'wskaznik'))
            tempfile.mkdtemp(prefix='tmp')                      # an ordinary tempfile directory is allowed
        finally:
            with self.assertRaises(AssertionError) as caught:
                guard_end(state)
        self.assertIn('wskaznik', str(caught.exception))
        self.assertIn('x', str(caught.exception).split(': ', 1)[1].split(', '))
        self.assertFalse(os.path.exists(state['parent']), 'the parent is removed even after a failure')

    def test_subprocesses_inherit_the_private_directory(self):
        state = guard_begin()
        try:
            result = subprocess.run([sys.executable, '-c', 'import tempfile;print(tempfile.gettempdir())'], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.stdout.strip(), state['parent'])
        finally:
            guard_end(state)

    def test_directories_only_pass_and_the_previous_values_are_restored(self):
        before_dir, before_env = tempfile.tempdir, os.environ.get('TMPDIR')
        state = guard_begin()
        try:
            os.makedirs(os.path.join(tempfile.mkdtemp(prefix='proto-server-'), 'glebiej'))
            tempfile.mkdtemp(prefix='skillsync-contract-')
        finally:
            guard_end(state)                                    # control: only directories, no error
        self.assertEqual((tempfile.tempdir, os.environ.get('TMPDIR')), (before_dir, before_env))
        self.assertFalse(os.path.exists(state['parent']))


if __name__ == '__main__':
    unittest.main()
