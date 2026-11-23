"""Safe copy and restore of a SQL Server test database through sqlcmd (tool of the test-env skill).

Purpose:
    Make a COPY_ONLY backup of a test database with a verified sidecar, and restore it later, so that a test run
    can be undone. A restore always makes a safety backup first, which is the way back.
Usage:
    db_snapshot.py backup --database NAME --label LABEL [--server S] [--dir D] [--dry-run] [--json] [--sha256] [--no-verify]
    db_snapshot.py restore --file FILE.bak --database NAME --confirm NAME [--dir D] [--no-safety-backup] [--dry-run] [--json]
    db_snapshot.py --selftest
    Remote hosts: backup only, with --allow-host HOST (repeatable), --dir and --server-os windows|linux.
Inputs:
    Command line options only. Credentials come from the environment (SQLCMDUSER and SQLCMDPASSWORD with --auth env,
    the default on Linux and macOS) or from the Windows login (--auth integrated, the default on Windows).
    --allow-host is never read from the environment. The child sqlcmd gets a whitelisted environment.
Outputs:
    The result on stdout: text (first line is the status) or one JSON document with --json. It holds the path, the
    undo command and a notice for the test environment coordinator. Progress and errors go to stderr, with secrets masked.
    A backup writes FILE.bak and FILE.bak.json (the sidecar) in the copy directory, outside any git repository.
Exit codes:
    0 PASS. 1 FAIL: the operation failed after the first changing call (BACKUP, ALTER or RESTORE) had started, so the
    database state may have changed (for example RESTORE failed halfway). 2 BLOCKED: nothing was changed: a refusal (bad
    input, missing --confirm, remote restore, a copy that does not match) or an execution error before the first
    changing call (a failed read of the state, identity or backup header, or a failed VERIFYONLY), and a usage error.
    3 NOT_RUN: a dry run, or sqlcmd is missing. Example: exit 1 after a failed restore means read the message, the
    database state may have changed, and use the safety backup named there.
Dependencies:
    Python 3.11 or newer (standard library only), sqlcmd on PATH, and a reachable SQL Server engine.
Limits:
    The path is resolved by the engine, so the engine must see the same directory. The tool does not create the restored
    database (it must exist and be ONLINE), and it handles single full backup sets only. Verified against a live engine on
    SQL Server LocalDB 2019 (Windows, sqlcmd 15.0, integrated auth) only; other engine configurations are not verified
    (Linux, containers, full instances, go-sqlcmd, SQL authentication, remote backup).
Security:
    No credentials in arguments, messages or files; they travel only in the environment of the child process and are
    masked in all output. Restore is allowed only on a local engine and needs --confirm with the exact database name.
    System databases are refused. Copies may hold personal data and are not encrypted; keep them out of repositories.
"""
import argparse
import base64
import collections
import contextlib
import dataclasses
import datetime
import json
import ntpath
import os
import platform as platform_module
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse

EXIT = {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2, 'NOT_RUN': 3}

SYSTEM_DATABASES = frozenset(('master', 'model', 'msdb', 'tempdb', 'distribution', 'mssqlsystemresource'))
WINDOWS_DEVICES = frozenset(['NUL', 'CON', 'PRN', 'AUX'] + ['COM' + str(i) for i in range(1, 10)] + ['LPT' + str(i) for i in range(1, 10)])
CHILD_ENV_KEYS = ('PATH', 'SYSTEMROOT', 'COMSPEC', 'TEMP', 'TMP', 'HOME', 'LANG')
MASK = '***'
CHANGED_STATE_LINE = 'stan bazy mógł się zmienić'


class Refusal(Exception):
    """Input refused before any change in the database (exit code 2)."""


class SqlFailure(Exception):
    """A step failed; exit code 1 after the first changing call has started, otherwise 2 (nothing was changed)."""


class NotRun(Exception):
    """Environment missing or dry run (exit code 3)."""


class RunState:
    def __init__(self):
        self.mutated = False

    def mark_mutating(self):
        self.mutated = True


def exit_code_for(exc, state):
    if isinstance(exc, Refusal):
        return EXIT['BLOCKED']
    if isinstance(exc, NotRun):
        return EXIT['NOT_RUN']
    return EXIT['FAIL'] if state.mutated else EXIT['BLOCKED']


def _safe_message(exc):
    import subprocess
    if isinstance(exc, subprocess.TimeoutExpired):
        return 'external command timed out'
    if isinstance(exc, subprocess.CalledProcessError):
        return 'external command failed'
    return str(exc)


def run_guarded(fn, state, stderr, masker):
    try:
        code = fn()
        return EXIT['PASS'] if code is None else code
    except SystemExit:
        raise
    except BaseException as exc:
        stderr.write(type(exc).__name__ + ': ' + masker.mask(_safe_message(exc)) + '\n')
        if state.mutated:
            stderr.write(CHANGED_STATE_LINE + '\n')
        return exit_code_for(exc, state)


# ---------------------------------------------------------------- validators

def validate_database(name):
    if not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_]{1,128}', name, re.ASCII) is None:
        raise Refusal('rule database-name: name must match [A-Za-z0-9_]{1,128}')
    if name.lower() in SYSTEM_DATABASES:
        raise Refusal('rule database-not-system: system databases are refused')
    return name


def validate_label(label):
    if not isinstance(label, str) or re.fullmatch(r'[a-z0-9-]{1,40}', label, re.ASCII) is None:
        raise Refusal('rule label: label must match [a-z0-9-]{1,40}')
    return label


@dataclasses.dataclass(frozen=True)
class ServerSpec:
    raw: str
    kind: str
    host: str
    instance: object
    port: object
    canonical: str
    localdb: bool


_LOCAL_HOSTS = frozenset(('.', 'localhost', '(local)', '127.0.0.1', '::1', '[::1]'))
_SERVER_RE = re.compile(
    r'(?P<host>\[::1\]|::1|\(local\)|\.|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)'
    r'(?:\\(?P<inst>[A-Za-z0-9_$#-]{1,16}))?'
    r'(?:,(?P<port>[0-9]{1,5}))?', re.ASCII | re.IGNORECASE)
_LOCALDB_RE = re.compile(r'(?i:\(localdb\))\\(?P<inst>[A-Za-z0-9_$#-]{1,16})', re.ASCII)


def _refuse_server(why):
    raise Refusal('rule server-syntax: ' + why)


def _check_numeric_host(host):
    labels = host.split('.')
    last = labels[-1]
    if not (last.isdigit() or last.startswith('0x')):
        return
    if len(labels) != 4:
        _refuse_server('abbreviated or numeric host form')
    for label in labels:
        if re.fullmatch(r'[0-9]{1,3}', label, re.ASCII) is None or (len(label) > 1 and label[0] == '0') or int(label) > 255:
            _refuse_server('malformed IPv4 address')
    if host == '0.0.0.0':
        _refuse_server('wildcard address')


def parse_server(text):
    if not isinstance(text, str):
        _refuse_server('server must be text')
    match = _LOCALDB_RE.fullmatch(text)
    if match is not None:
        instance = match.group('inst')
        return ServerSpec(text, 'local', '(localdb)', instance, None, ('(localdb)\\' + instance).lower(), True)
    match = _SERVER_RE.fullmatch(text)
    if match is None or len(match.group('host')) > 253:
        _refuse_server('unsupported server form')
    host = match.group('host').lower()
    instance = match.group('inst')
    port = None
    if match.group('port') is not None:
        port = int(match.group('port'))
        if not 1 <= port <= 65535:
            _refuse_server('port out of range')
    if host in _LOCAL_HOSTS:
        kind = 'local'
        canon_host = 'local'
    else:
        _check_numeric_host(host)
        kind = 'remote'
        canon_host = host
    canonical = canon_host
    if instance is not None:
        canonical += '\\' + instance.lower()
    if port is not None:
        canonical += ',' + str(port)
    return ServerSpec(text, kind, canon_host if kind == 'local' else host, instance, port, canonical, False)


def check_host_allowed(spec, allow_hosts, command):
    if command not in ('backup', 'restore'):
        raise Refusal('rule command: unknown command')
    if spec.kind == 'local':
        return False
    if command == 'restore':
        raise Refusal('rule restore-local-only: restore on a remote host is always refused')
    wanted = (spec.canonical, spec.raw.lower())
    for item in (allow_hosts or []):
        if isinstance(item, str) and item.lower() in wanted:
            return True
    raise Refusal('rule remote-host-allowlist: remote host is not in the allow list')


def validate_server_path(path, *, flavor, kind):
    if flavor not in ('windows', 'posix') or kind not in ('dir', 'file'):
        raise ValueError('unknown flavor or kind')
    if not isinstance(path, str) or not path:
        raise Refusal('rule server-path: path must be non-empty text')
    if re.fullmatch(r'[A-Za-z0-9 _.\-\\/:]+', path, re.ASCII) is None:
        raise Refusal('rule server-path-charset: path has a character outside the whitelist (ASCII letters, digits, space and _ . - \\ / :); use --dir with a directory that has such a path')
    if flavor == 'windows':
        match = re.fullmatch(r'([A-Za-z]):[\\/](.*)', path, re.ASCII | re.DOTALL)
        if match is None:
            raise Refusal('rule server-path-absolute: path must start with a drive letter and a separator')
        root = match.group(1) + ':\\'
        rest = match.group(2)
        if ':' in rest:
            raise Refusal('rule server-path-stream: colon is allowed only after the drive letter')
        segments = re.split(r'[\\/]', rest) if rest else []
        joiner = '\\'
    else:
        if not path.startswith('/'):
            raise Refusal('rule server-path-absolute: path must be absolute')
        if '\\' in path or ':' in path:
            raise Refusal('rule server-path-charset: backslash and colon are not allowed')
        root = '/'
        rest = path[1:]
        segments = rest.split('/') if rest else []
        joiner = '/'
    for segment in segments:
        if segment == '' or segment in ('.', '..'):
            raise Refusal('rule server-path-segment: empty, dot or parent segment')
        if segment.endswith(' '):
            raise Refusal('rule server-path-segment: segment must not end with a space')
        if flavor == 'windows':
            if segment.endswith('.'):
                raise Refusal('rule server-path-segment: segment must not end with a dot')
            if segment.split('.')[0].rstrip(' ').upper() in WINDOWS_DEVICES:
                raise Refusal('rule server-path-device: reserved device name')
    if kind == 'file':
        if not segments or not segments[-1].lower().endswith('.bak') or len(segments[-1]) <= 4:
            raise Refusal('rule server-path-extension: backup file must have the .bak extension')
    return root + joiner.join(segments)


def _path_module(path):
    """The path rules that fit a real path by its shape, not by the host: a drive letter or a UNC / extended-length prefix means Windows rules;
    a path rooted with a slash means POSIX rules (a backslash is then an ordinary name character); a relative path with a backslash means
    Windows rules; anything else follows the host (`os.path`)."""
    if re.match(r'(?:[A-Za-z]:|\\\\)', path):
        return ntpath
    if path.startswith('/'):
        return posixpath
    if '\\' in path:
        return ntpath
    return os.path


def check_dir_outside_repo(path, *, lexists, realpath):
    current = realpath(path)
    flavor = _path_module(current)
    for _ in range(4096):
        if lexists(flavor.join(current, '.git')):
            raise Refusal('rule dir-outside-repo: directory is inside a git repository; use --dir with a directory outside any repository')
        parent = flavor.dirname(current)
        if parent == current or not parent:
            return
        current = parent
    raise Refusal('rule dir-outside-repo: path is too deep to check')


def default_backup_dir(platform, environ, home):
    if platform == 'win32':
        base = environ.get('LOCALAPPDATA') or (home + '\\AppData\\Local')
        return base.rstrip('\\') + '\\db-copies'
    if platform == 'darwin':
        return home.rstrip('/') + '/Library/Application Support/db-copies'
    state = environ.get('XDG_STATE_HOME')
    if state:
        return state.rstrip('/') + '/db-copies'
    return home.rstrip('/') + '/.local/state/db-copies'


def unique_name(db, label, now, token=None):
    validate_database(db)
    validate_label(label)
    if token is None:
        token = secrets.token_hex(3)
    if re.fullmatch(r'[0-9a-f]{6}', token, re.ASCII) is None:
        raise Refusal('rule unique-token: token must be 6 lowercase hex digits')
    if now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc)
    name = db + '-' + label + '-' + now.strftime('%Y%m%d-%H%M%S') + '-' + token + '.bak'
    validate_server_path('/' + name, flavor='posix', kind='file')
    return name


# ---------------------------------------------------------------- SQL builders

def quote_ident(name):
    return '[' + validate_database(name) + ']'


def quote_literal(text):
    return "N'" + text.replace("'", "''") + "'"


def build_backup_sql(db, path):
    return 'BACKUP DATABASE ' + quote_ident(db) + ' TO DISK = ' + quote_literal(path) + ' WITH COPY_ONLY, INIT, CHECKSUM'


def build_verify_sql(path):
    return 'RESTORE VERIFYONLY FROM DISK = ' + quote_literal(path) + ' WITH CHECKSUM, FILE = 1'


def build_headeronly_sql(path):
    return 'RESTORE HEADERONLY FROM DISK = ' + quote_literal(path)


def build_state_sql(db):
    validate_database(db)
    return 'SELECT state_desc, user_access_desc, recovery_model_desc FROM sys.databases WHERE name = ' + quote_literal(db)


def build_identity_sql():
    return ("SELECT @@SERVERNAME AS server_name, CAST(SERVERPROPERTY('MachineName') AS nvarchar(256)) AS machine_name, "
            "CAST(SERVERPROPERTY('InstanceName') AS nvarchar(256)) AS instance_name")


def build_restore_batch(db, path):
    ident = quote_ident(db)
    literal = quote_literal(path)
    return ' '.join((
        'BEGIN TRY',
        'ALTER DATABASE ' + ident + ' SET SINGLE_USER WITH ROLLBACK IMMEDIATE;',
        'RESTORE DATABASE ' + ident + ' FROM DISK = ' + literal + ' WITH REPLACE, FILE = 1, CHECKSUM;',
        'ALTER DATABASE ' + ident + ' SET MULTI_USER;',
        'END TRY',
        'BEGIN CATCH',
        'BEGIN TRY ALTER DATABASE ' + ident + ' SET MULTI_USER; END TRY BEGIN CATCH DECLARE @i int; END CATCH;',
        'THROW;',
        'END CATCH'))


# ---------------------------------------------------------------- environment and sqlcmd

def child_env(environ, auth, platform):
    keys = set(CHILD_ENV_KEYS)
    result = {}
    for key, value in environ.items():
        probe = key.upper() if platform == 'win32' else key
        if probe in keys or probe.startswith('LC_'):
            result[key] = value
    if auth == 'env':
        for name in ('SQLCMDUSER', 'SQLCMDPASSWORD'):
            if not environ.get(name):
                raise Refusal('rule auth-env: environment variable ' + name + ' is required for auth env')
            result[name] = environ[name]
    return result


def resolve_auth(auth, platform):
    if auth == 'auto':
        return 'integrated' if platform == 'win32' else 'env'
    if auth in ('integrated', 'env'):
        return auth
    raise Refusal('rule auth-mode: unknown auth mode')


def sqlcmd_args(server, sql, *, auth, trust_server_certificate, server_is_local, table_mode=False):
    if auth not in ('integrated', 'env'):
        raise Refusal('rule auth-mode: unknown auth mode')
    args = ['-S', server, '-d', 'master', '-b', '-x']
    if table_mode:
        args.extend(['-W', '-s', '|', '-w', '65535'])
    args.extend(['-Q', sql])
    if auth == 'integrated':
        args.append('-E')
    if trust_server_certificate:
        if not server_is_local:
            raise Refusal('rule trust-certificate-local-only: -C is allowed only for a local server')
        args.append('-C')
    return args


def find_sqlcmd(environ, platform, *, isfile, access):
    windows = platform == 'win32'
    flavor = ntpath if windows else posixpath
    name = 'sqlcmd.exe' if windows else 'sqlcmd'
    for entry in (environ.get('PATH') or '').split(';' if windows else ':'):
        entry = entry.strip('"') if windows else entry
        if not entry or entry == '.' or not flavor.isabs(entry):
            continue
        candidate = flavor.join(entry, name)
        if isfile(candidate) and access(candidate, os.X_OK):
            return candidate
    return None


# ---------------------------------------------------------------- masking

class Masker:
    def __init__(self, secrets_list):
        variants = set()
        for secret in secrets_list:
            if not secret:
                continue
            raw = secret.encode('utf-8')
            for variant in (secret,
                            urllib.parse.quote(secret, safe=''),
                            urllib.parse.quote_plus(secret),
                            json.dumps(secret)[1:-1],
                            json.dumps(secret, ensure_ascii=False)[1:-1],
                            base64.b64encode(raw).decode('ascii'),
                            base64.urlsafe_b64encode(raw).decode('ascii'),
                            base64.b64encode(secret.encode('utf-16-le')).decode('ascii')):
                variants.add(variant)
                if variant.rstrip('='):
                    variants.add(variant.rstrip('='))
        ordered = sorted(variants, key=lambda v: (-len(v), v))
        self._pattern = re.compile('|'.join(re.escape(v) for v in ordered)) if ordered else None

    @classmethod
    def from_env(cls, environ, auth):
        password = environ.get('SQLCMDPASSWORD')
        return cls([password] if password else [])

    def mask(self, text):
        if self._pattern is None:
            return text
        return self._pattern.sub(MASK, text)

    def mask_value(self, obj):
        if isinstance(obj, str):
            return self.mask(obj)
        if isinstance(obj, dict):
            return {self.mask_value(k): self.mask_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.mask_value(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self.mask_value(v) for v in obj)
        return obj


# ---------------------------------------------------------------- S2: table output, runner, file system

HEADER_UNREADABLE = 'cannot read the backup header unambiguously'
MAX_PLAIN_OUTPUT = 4000
MAX_TABLE_OUTPUT = 1000000
_ROWS_AFFECTED_RE = re.compile(r'\((\d+) rows? affected\)')
_SEPARATOR_RE = re.compile(r'[-|]+')
_ERROR_LINE_RE = re.compile(r'Msg \d+, Level (?:1[1-9]|2\d)')


def parse_sqlcmd_table(stdout):
    """Parse the output of `sqlcmd -W -s "|"` into a list of dicts keyed by the header names."""
    lines = stdout.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    position = 0
    while position < len(lines) and not lines[position].strip():
        position += 1
    if position >= len(lines) or _ROWS_AFFECTED_RE.fullmatch(lines[position].strip()):
        raise SqlFailure('no table header in the output: ' + HEADER_UNREADABLE)
    names = lines[position].strip().split('|')
    if len(set(names)) != len(names):
        raise SqlFailure('duplicate column names: ' + HEADER_UNREADABLE)
    position += 1
    if position >= len(lines) or _SEPARATOR_RE.fullmatch(lines[position].strip()) is None:
        raise SqlFailure('no header separator line: ' + HEADER_UNREADABLE)
    position += 1
    rows = []
    while position < len(lines):
        line = lines[position].strip()
        if not line or _ROWS_AFFECTED_RE.fullmatch(line):
            break
        fields = line.split('|')
        if len(fields) != len(names):
            raise SqlFailure(HEADER_UNREADABLE)
        rows.append(dict(zip(names, fields)))
        position += 1
    # After the end of the data only "(N rows affected)" with N equal to the number of rows may follow (a field with an
    # embedded empty line must not be able to forge a second table or extra rows).
    counts = [line.strip() for line in lines[position:] if line.strip()]
    if counts:
        found = _ROWS_AFFECTED_RE.fullmatch(counts[0]) if len(counts) == 1 else None
        if found is None or int(found.group(1)) != len(rows):
            raise SqlFailure('unexpected text after the table data: ' + HEADER_UNREADABLE)
    return rows


@dataclasses.dataclass
class RunResult:
    rc: int
    stdout: str
    stderr: str


def guard_runner(runner, state):
    """Mark the state as mutated BEFORE every mutating call (also when the call raises)."""
    def guarded(sql, *, mutating, timeout, table=False):
        if mutating:
            state.mark_mutating()
        return runner(sql, mutating=mutating, timeout=timeout, table=table)
    return guarded


class SubprocessRunner:
    def __init__(self, sqlcmd_path, server_raw, auth, env, masker, cwd, trust_server_certificate, server_is_local, popen=subprocess.run):
        self.sqlcmd_path = sqlcmd_path
        self.server_raw = server_raw
        self.auth = auth
        self.env = env
        self.masker = masker
        self.cwd = cwd
        self.trust_server_certificate = trust_server_certificate
        self.server_is_local = server_is_local
        self.popen = popen

    def __call__(self, sql, *, mutating, timeout, table=False):
        args = sqlcmd_args(self.server_raw, sql, auth=self.auth, trust_server_certificate=self.trust_server_certificate,
                           server_is_local=self.server_is_local, table_mode=table)
        done = self.popen([self.sqlcmd_path] + args, env=self.env, cwd=self.cwd, capture_output=True, text=True, errors='replace', timeout=timeout, shell=False)
        if table:
            # Table data goes to the parser raw (masking could corrupt a cell); it is never printed or stored as is:
            # every message and result passes through the masker in run_guarded, format_result and the output streams.
            out = done.stdout or ''
            err = done.stderr or ''
            if len(out) > MAX_TABLE_OUTPUT or len(err) > MAX_TABLE_OUTPUT:
                raise SqlFailure('output too large')
        else:
            out = self.masker.mask(done.stdout or '')[:MAX_PLAIN_OUTPUT]
            err = self.masker.mask(done.stderr or '')[:MAX_PLAIN_OUTPUT]
        return RunResult(done.returncode, out, err)


# One entry of a directory as `lstat` sees it (never follows a link): kind is 'file', 'dir', 'link' or 'other'.
FsEntry = collections.namedtuple('FsEntry', 'kind size mtime')


class RealFs:
    def listdir(self, path):
        return os.listdir(path)

    def lstat(self, path):
        info = os.lstat(path)
        mode = info.st_mode
        kind = 'link' if stat.S_ISLNK(mode) else 'file' if stat.S_ISREG(mode) else 'dir' if stat.S_ISDIR(mode) else 'other'
        return FsEntry(kind, info.st_size, info.st_mtime)

    def isdir(self, path):
        return os.path.isdir(path)

    def unlink(self, path):
        os.unlink(path)

    def makedirs(self, path, mode):
        os.makedirs(path, mode=mode, exist_ok=True)

    def exists(self, path):
        return os.path.exists(path)

    def getsize(self, path):
        return os.path.getsize(path)

    def realpath(self, path):
        return os.path.realpath(path)

    def lexists(self, path):
        return os.path.lexists(path)

    def sha256(self, path):
        import hashlib
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def isfile(self, path):
        return os.path.isfile(path)

    def read_bytes(self, path, limit):
        with open(path, 'rb') as handle:
            return handle.read(limit + 1)

    def write_atomic(self, path, data):
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.' + os.path.basename(path) + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as handle:
                if os.name != 'nt':
                    if hasattr(os, 'fchmod'):
                        os.fchmod(handle.fileno(), 0o600)
                    else:
                        os.chmod(tmp, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------- S2: backup flow

@dataclasses.dataclass
class BackupOptions:
    database: str
    label: str
    server: str = 'localhost'
    dir: object = None
    auth: str = 'auto'
    allow_hosts: tuple = ()
    server_os: object = None
    no_verify: bool = False
    dry_run: bool = False
    timeout: float = 600.0
    trust_server_certificate: bool = False
    sha256: bool = False


def _excerpt(result):
    text = (result.stderr or '') + '\n' + (result.stdout or '')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chosen = next((line for line in lines if _ERROR_LINE_RE.search(line)), lines[0] if lines else '')
    return chosen[:300]


def _checked(result, step, path=None, table=False):
    """Raise SqlFailure when the step failed: non-zero rc or a Msg line of severity 11 or higher.

    The output of a table read holds data fields (for example a backup name), so there a Msg line counts only on stderr or
    with a non-zero rc, and the output is never copied into a message; a plain step may show a masked excerpt.
    """
    if table:
        found = _ERROR_LINE_RE.search(result.stderr or '')
    else:
        found = _ERROR_LINE_RE.search(result.stdout or '') or _ERROR_LINE_RE.search(result.stderr or '')
    if result.rc != 0 or found:
        text = step + ' failed (rc ' + str(result.rc) + ')'
        if path is not None:
            text += '; the copy was left at ' + path
        if table:
            text += '; the output of a table read is not shown'
        else:
            detail = _excerpt(result)
            if detail:
                text += ': ' + detail
        raise SqlFailure(text)
    return result


def _table_row(result, step, path=None):
    _checked(result, step, path, table=True)
    return parse_sqlcmd_table(result.stdout or '')


_NAME_RE = re.compile(r'[A-Za-z0-9_.\\$#-]+')
_GUID_RE = re.compile(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}')
_DIGITS_RE = re.compile(r'[0-9]+')
_DBNAME_RE = re.compile(r'[A-Za-z0-9_]+')


def _whitelisted(row, key, pattern, step):
    """A header value used in a decision must match its whitelist; anything else is not trusted."""
    if pattern.fullmatch(row[key]) is None:
        raise SqlFailure(step + ' has an unexpected value in column ' + key + ': ' + HEADER_UNREADABLE)


def _join(flavor, directory, name):
    separator = '\\' if flavor == 'windows' else '/'
    return directory + ('' if directory.endswith(separator) else separator) + name


def _shell_text(text, windows=False):
    """Quote a value for the shell of the platform: double quotes on Windows, single quotes elsewhere ($ and # stay literal)."""
    if re.fullmatch(r'[A-Za-z0-9_./:@,-]+', text):
        return text
    if windows:
        return '"' + text + '"'
    return "'" + text.replace("'", "'\\''") + "'"


def _restore_command(path, db, server, directory=None, trust_server_certificate=False, windows=False):
    """The command that undoes a copy; it must run as it stands, so it names the server, directory and certificate flag in use."""
    command = 'db_snapshot.py restore --file ' + _shell_text(path, windows) + ' --database ' + db + ' --confirm ' + db
    if server != 'localhost':
        command += ' --server ' + _shell_text(server, windows)
    if directory:
        command += ' --dir ' + _shell_text(directory, windows)
    if trust_server_certificate:
        command += ' --trust-server-certificate'
    return command


def _format_size(size):
    if size is None:
        return 'nieznany (zdalny host)'
    return str(size) + ' B (' + format(size / 1048576.0, '.1f') + ' MiB)'


def _restore_notice(result):
    safety = result.get('safety_backup_path')
    lines = [
        'Przywrócono bazę ' + str(result.get('database')) + ' na serwerze ' + str(result.get('server')) + ' z pliku ' + str(result.get('file'))
        + ' (restore).',
        'Czas (UTC): ' + str(result.get('restored_utc')),
    ]
    if safety:
        lines.append('Kopia bezpieczeństwa sprzed przywrócenia: ' + str(safety))
        lines.append('Cofnięcie (przywrócenie kopii bezpieczeństwa): ' + str(result.get('rollback_command')))
    else:
        lines.append('Kopia bezpieczeństwa: brak (ryzykowne: brak drogi powrotu).')
    for warning in result.get('warnings') or []:
        lines.append('Ostrzeżenie: ' + str(warning))
    lines.append('Uwaga: baza testowa jest współdzielonym stanem; poinformuj innych użytkowników środowiska o przywróceniu.')
    lines.append('Uwaga: kopia może zawierać dane osobowe i nie jest szyfrowana.')
    return '\n'.join(lines)


def coordinator_notice(result, command='backup'):
    if command == 'restore':
        return _restore_notice(result)
    lines = [
        'Kopia bazy ' + str(result.get('database')) + ' na serwerze ' + str(result.get('server')) + ' (' + command + ', etykieta: '
        + str(result.get('label')) + ').',
        'Ścieżka: ' + str(result.get('path')),
        'Rozmiar: ' + _format_size(result.get('size')),
        'Czas (UTC): ' + str(result.get('created_utc')),
        'Cofnięcie: ' + str(result.get('restore_command')),
        'Uwaga: baza testowa jest współdzielonym stanem; poinformuj innych użytkowników środowiska przed cofnięciem.',
        'Uwaga: kopia może zawierać dane osobowe i nie jest szyfrowana.',
    ]
    if result.get('remote'):
        lines.insert(2, 'Typ: zdalny host; ścieżka na serwerze, nie zweryfikowana lokalnie.')
        lines.append('Cofnięcie zdalnej kopii: narzędzie odmawia restore na zdalnym hoście; wykonaj je na serwerze.')
    return '\n'.join(lines)


def _identity(runner, timeout):
    result = runner(build_identity_sql(), mutating=False, timeout=min(timeout, 30.0), table=True)
    rows = _table_row(result, 'reading the server identity')
    if len(rows) != 1 or 'server_name' not in rows[0] or 'machine_name' not in rows[0] or 'instance_name' not in rows[0]:
        raise SqlFailure('cannot read the server identity unambiguously')
    row = rows[0]
    return {'engine_server_name': row['server_name'], 'machine_name': row['machine_name'],
            'instance_name': None if row['instance_name'] == 'NULL' else row['instance_name']}


def _check_header(rows, db, path):
    if len(rows) != 1:
        raise SqlFailure('the backup header must hold exactly one backup set; the copy was left at ' + path)
    row = rows[0]
    for key in ('DatabaseName', 'BackupType', 'BackupSetGUID', 'FirstLSN', 'LastLSN', 'BackupFinishDate'):
        if key not in row:
            raise SqlFailure('the backup header lacks column ' + key + '; the copy was left at ' + path)
    for key, pattern in (('DatabaseName', _DBNAME_RE), ('BackupType', _DIGITS_RE), ('BackupSetGUID', _GUID_RE), ('FirstLSN', _DIGITS_RE),
                         ('LastLSN', _DIGITS_RE)):
        if pattern.fullmatch(row[key]) is None:
            raise SqlFailure('the backup header has an unexpected value in column ' + key + '; the copy was left at ' + path)
    if row['DatabaseName'].lower() != db.lower():
        raise SqlFailure('the backup header names another database; the copy was left at ' + path)
    if row['BackupType'] != '1':
        raise SqlFailure('the backup is not a full database backup; the copy was left at ' + path)
    return row


def run_backup(opts, *, runner, fs, clock, token_fn, state, environ, platform, masker=None):
    def _m(value):
        return value if masker is None else masker.mask_value(value)
    # (1) validation, before any runner call or write
    db = validate_database(opts.database)
    label = validate_label(opts.label)
    spec = parse_server(opts.server)
    remote = check_host_allowed(spec, opts.allow_hosts, 'backup')
    home = environ.get('HOME') or environ.get('USERPROFILE') or os.path.expanduser('~')
    local_flavor = 'windows' if platform == 'win32' else 'posix'
    state_dir = default_backup_dir(platform, environ, home)
    if remote:
        if not opts.dir:
            raise Refusal('rule remote-dir: a remote host requires --dir (a directory on the server)')
        if opts.server_os not in ('windows', 'linux'):
            raise Refusal('rule remote-server-os: a remote host requires --server-os windows or linux')
        flavor = 'windows' if opts.server_os == 'windows' else 'posix'
        directory = validate_server_path(opts.dir, flavor=flavor, kind='dir')
        check_dir_outside_repo(state_dir, lexists=fs.lexists, realpath=fs.realpath)
    else:
        flavor = local_flavor
        directory = validate_server_path(opts.dir or state_dir, flavor=flavor, kind='dir')
        check_dir_outside_repo(directory, lexists=fs.lexists, realpath=fs.realpath)
    # (2) unique name and path
    now = clock()
    name = unique_name(db, label, now, token_fn())
    path = _join(flavor, directory, name)
    validate_server_path(path, flavor=flavor, kind='file')
    sidecar_path = _join(local_flavor, state_dir, name + '.json') if remote else path + '.json'
    created = now.astimezone(datetime.timezone.utc) if now.tzinfo is not None else now
    created_utc = created.strftime('%Y-%m-%dT%H:%M:%SZ')
    # (3) dry run
    if opts.dry_run:
        steps = ['identity', 'backup', 'verify' if not opts.no_verify else 'verify skipped', 'headeronly']
        plan = {'database': db, 'label': label, 'server': opts.server, 'remote': remote, 'path': path, 'sidecar': sidecar_path,
                'steps': steps, 'sha256': bool(opts.sha256 and not remote)}
        notrun = NotRun('dry run: nothing was executed')
        notrun.result = {'status': 'NOT_RUN', 'plan': plan}
        raise notrun
    # (3b) a target that already exists is never written over (a local directory only; a server path cannot be probed from here)
    if not remote and fs.lexists(path):
        raise Refusal('rule target-exists: the backup target already exists: ' + path)
    # (4) engine identity (read only)
    identity = _identity(runner, opts.timeout)
    # (5) local directory
    if not remote:
        fs.makedirs(directory, 0o700)
    # (6) backup
    _checked(runner(build_backup_sql(db, path), mutating=True, timeout=opts.timeout), 'BACKUP')
    # (7) verify
    if not opts.no_verify:
        _checked(runner(build_verify_sql(path), mutating=False, timeout=opts.timeout), 'RESTORE VERIFYONLY', path)
    # (8) header
    rows = _table_row(runner(build_headeronly_sql(path), mutating=False, timeout=min(opts.timeout, 120.0), table=True), 'RESTORE HEADERONLY', path)
    row = _check_header(rows, db, path)
    # (9) local file
    size = None
    if not remote:
        if not fs.exists(path):
            raise SqlFailure('the backup file is missing: ' + path)
        size = fs.getsize(path)
        if not size > 0:
            raise SqlFailure('the backup file is empty: ' + path)
    # (10) sidecar
    sidecar = {
        'schema': 1, 'database': db, 'label': label,
        'server': dict(_m(identity), raw=opts.server, canonical=spec.canonical),
        'file': name, 'size': size, 'created_utc': created_utc,
        'backup_set_guid': _m(row['BackupSetGUID']), 'first_lsn': _m(row['FirstLSN']), 'last_lsn': _m(row['LastLSN']),
        'backup_finish_date': _m(row['BackupFinishDate']), 'remote': remote, 'server_path': path, 'copy_only': True,
    }
    try:
        if opts.sha256 and not remote:
            sidecar['sha256'] = fs.sha256(path)
        fs.write_atomic(sidecar_path, json.dumps(sidecar, sort_keys=True, indent=2).encode('utf-8'))
    except OSError as exc:
        raise SqlFailure('cannot write the sidecar (' + type(exc).__name__ + '); the copy was left at ' + path)
    # (11) result
    result = {
        'status': 'PASS', 'database': db, 'label': label, 'server': opts.server, 'path': path, 'size': size, 'remote': remote,
        'verified_locally': not remote, 'verify': 'skipped' if opts.no_verify else 'passed', 'created_utc': created_utc,
        'sidecar': sidecar_path,
        'restore_command': _restore_command(path, db, opts.server, directory if opts.dir else None, opts.trust_server_certificate,
                                            local_flavor == 'windows'),
    }
    result['coordinator_notice'] = coordinator_notice(result)
    return result


# ---------------------------------------------------------------- S3: sidecar parser, machine names, restore flow

SIDECAR_MAX_BYTES = 65536
_SIDECAR_STR_KEYS = ('database', 'label', 'file', 'created_utc', 'backup_set_guid', 'first_lsn', 'last_lsn', 'backup_finish_date', 'server_path')
_SIDECAR_BOOL_KEYS = ('remote', 'copy_only')
_SIDECAR_SERVER_KEYS = ('raw', 'canonical', 'engine_server_name', 'machine_name', 'instance_name')
_SIDECAR_KEYS = frozenset(('schema', 'size', 'server', 'sha256') + _SIDECAR_STR_KEYS + _SIDECAR_BOOL_KEYS)


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def read_sidecar(raw):
    """Strict parser of the untrusted sidecar file; returns a dict and never follows any path from it."""
    if not isinstance(raw, (bytes, bytearray)):
        raise Refusal('rule sidecar: the sidecar must be read as bytes')
    if len(raw) > SIDECAR_MAX_BYTES:
        raise Refusal('rule sidecar: the sidecar is larger than ' + str(SIDECAR_MAX_BYTES) + ' bytes')
    try:
        data = json.loads(bytes(raw).decode('utf-8'), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refusal('rule sidecar: the sidecar is not a valid JSON document') from None
    if not isinstance(data, dict):
        raise Refusal('rule sidecar: the sidecar must be a JSON object')
    if type(data.get('schema')) is not int or data['schema'] != 1:
        raise Refusal('rule sidecar: unsupported sidecar schema')
    for key in data:
        if key not in _SIDECAR_KEYS:
            raise Refusal('rule sidecar: unknown sidecar key')
    for key in _SIDECAR_KEYS - {'sha256'}:
        if key not in data:
            raise Refusal('rule sidecar: a required sidecar key is missing')
    for key in _SIDECAR_STR_KEYS:
        if not isinstance(data[key], str):
            raise Refusal('rule sidecar: wrong type of the sidecar key ' + key)
    for key in _SIDECAR_BOOL_KEYS:
        if not isinstance(data[key], bool):
            raise Refusal('rule sidecar: wrong type of the sidecar key ' + key)
    if data['size'] is not None and (type(data['size']) is not int or data['size'] < 0):
        raise Refusal('rule sidecar: wrong type of the sidecar key size')
    if 'sha256' in data and (not isinstance(data['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', data['sha256'], re.ASCII) is None):
        raise Refusal('rule sidecar: wrong type of the sidecar key sha256')
    server = data['server']
    if not isinstance(server, dict):
        raise Refusal('rule sidecar: the sidecar server entry must be an object')
    for key in server:
        if key not in _SIDECAR_SERVER_KEYS:
            raise Refusal('rule sidecar: unknown key in the sidecar server entry')
    for key in _SIDECAR_SERVER_KEYS:
        if key not in server:
            raise Refusal('rule sidecar: a required key of the sidecar server entry is missing')
        value = server[key]
        if not (isinstance(value, str) or (key == 'instance_name' and value is None)):
            raise Refusal('rule sidecar: wrong type in the sidecar server entry')
    return data


def machine_names_match(engine_name, local_name):
    """Compare machine names ignoring case; both are cut to 15 characters only when one side is a 15 character NetBIOS name."""
    if not isinstance(engine_name, str) or not isinstance(local_name, str) or not engine_name or not local_name:
        return False
    if len(engine_name) == 15 or len(local_name) == 15:
        return engine_name[:15].lower() == local_name[:15].lower()
    return engine_name.lower() == local_name.lower()


@dataclasses.dataclass
class RestoreOptions:
    database: str
    file: str
    confirm: object
    server: str = 'localhost'
    dir: object = None
    auth: str = 'auto'
    allow_missing_sidecar: bool = False
    no_safety_backup: bool = False
    allow_machine_mismatch: bool = False
    dry_run: bool = False
    restore_timeout: float = 3600.0
    state_timeout: float = 30.0
    timeout: float = 600.0
    trust_server_certificate: bool = False


def _state_row(runner, db, timeout):
    rows = _table_row(runner(build_state_sql(db), mutating=False, timeout=timeout, table=True), 'reading the database state')
    if len(rows) > 1:
        raise SqlFailure('cannot read the database state unambiguously')
    if rows and not all(key in rows[0] for key in ('state_desc', 'user_access_desc', 'recovery_model_desc')):
        raise SqlFailure('cannot read the database state unambiguously')
    return rows[0] if rows else None


def _actual_state(runner, db, timeout):
    """Read the real state after a failed step; any problem (also a second interrupt) means unknown. Returns (text, state_desc)."""
    try:
        row = _state_row(runner, db, timeout)
    except (Exception, KeyboardInterrupt):
        row = None
    if row is None:
        return 'unknown', None
    return 'state_desc=' + row['state_desc'] + ', user_access_desc=' + row['user_access_desc'], row['state_desc']


def _way_back(safety, db, command, state_desc='ONLINE'):
    """The way back after a failure; the tool refuses a restore from any state but ONLINE, so then the plain statement is given."""
    if not safety:
        return 'No safety backup was made, so there is no way back.'
    text = 'The way back: restore the safety backup ' + safety + ' (command: ' + command + ').'
    if state_desc != 'ONLINE':
        text += (' The database is not ONLINE and the tool refuses a restore from that state; restore it by hand: RESTORE DATABASE '
                 + quote_ident(db) + ' FROM DISK = ' + quote_literal(safety) + ' WITH REPLACE, RECOVERY, FILE = 1')
    return text


def _failure_reason(exc):
    if isinstance(exc, SqlFailure):
        return str(exc)
    if isinstance(exc, KeyboardInterrupt):
        return 'interrupted'
    return type(exc).__name__


def _check_restore_header(rows, db, sidecar, identity, opts, warnings):
    if not rows:
        raise Refusal('the backup file holds no backup set')
    if len(rows) > 1:
        raise Refusal('the file holds multiple backup sets; only a single full backup set is supported')
    row = rows[0]
    required = ['DatabaseName', 'BackupType', 'ServerName', 'MachineName', 'BackupSetGUID', 'FirstLSN', 'LastLSN']
    if sidecar is not None:
        required.append('BackupFinishDate')
    for key in required:
        if key not in row:
            raise SqlFailure('the backup header lacks column ' + key + ': ' + HEADER_UNREADABLE)
    for key, pattern in (('DatabaseName', _DBNAME_RE), ('BackupType', _DIGITS_RE), ('ServerName', _NAME_RE), ('MachineName', _NAME_RE),
                         ('BackupSetGUID', _GUID_RE), ('FirstLSN', _DIGITS_RE), ('LastLSN', _DIGITS_RE)):
        _whitelisted(row, key, pattern, 'the backup header')
    if row['BackupType'] != '1':
        raise Refusal('the file is not a full database backup')
    if row['DatabaseName'].lower() != db.lower():
        raise Refusal('the file holds a backup of a different database')
    sources = {'ServerName': [('the backup header', row['ServerName'])], 'MachineName': [('the backup header', row['MachineName'])]}
    if sidecar is not None:
        server = sidecar['server']
        if (row['ServerName'].lower() != server['engine_server_name'].lower() or row['MachineName'].lower() != server['machine_name'].lower()
                or row['BackupSetGUID'].lower() != sidecar['backup_set_guid'].lower() or row['FirstLSN'] != sidecar['first_lsn']
                or row['LastLSN'] != sidecar['last_lsn']):
            raise Refusal('the backup header does not match the sidecar')
        if row['BackupFinishDate'] != sidecar['backup_finish_date']:
            raise Refusal('the backup finish date in the header differs from the sidecar')
        sources['ServerName'].append(('the sidecar', server['engine_server_name']))
        sources['MachineName'].append(('the sidecar', server['machine_name']))
    # The origin of the copy (header and sidecar alike) must be the target engine itself.
    for column, wanted in (('ServerName', identity['engine_server_name']), ('MachineName', identity['machine_name'])):
        for label, value in sources[column]:
            if value.lower() != wanted.lower():
                if not opts.allow_machine_mismatch:
                    raise Refusal(label + ' ' + column + ' differs from the target engine (the backup comes from a different server)')
                warnings.append(label + ' ' + column + ' differs from the target engine (allowed by --allow-machine-mismatch)')
    return row


def run_restore(opts, *, runner, fs, clock, token_fn, state, environ, platform, machine_name, emit, masker=None):
    def _m(value):
        return value if masker is None else masker.mask_value(value)
    warnings = []
    # (1) validation, before any runner call or write
    db = validate_database(opts.database)
    spec = parse_server(opts.server)
    check_host_allowed(spec, (), 'restore')
    if not isinstance(opts.confirm, str) or opts.confirm != opts.database:
        raise Refusal('rule confirm: --confirm must repeat the database name exactly')
    local_flavor = 'windows' if platform == 'win32' else 'posix'
    pathmod = ntpath if local_flavor == 'windows' else posixpath
    home = environ.get('HOME') or environ.get('USERPROFILE') or os.path.expanduser('~')
    file_path = validate_server_path(opts.file, flavor=local_flavor, kind='file')
    directory = validate_server_path(opts.dir or default_backup_dir(platform, environ, home), flavor=local_flavor, kind='dir')
    check_dir_outside_repo(directory, lexists=fs.lexists, realpath=fs.realpath)
    parent = pathmod.dirname(file_path)
    if (parent.lower() != directory.lower()) if local_flavor == 'windows' else (parent != directory):
        raise Refusal('rule file-location: the file must reside in the backup directory')
    if not fs.isfile(file_path):
        raise Refusal('rule file-missing: the backup file does not exist or is not a regular file')
    if pathmod.normcase(fs.realpath(file_path)) != pathmod.normcase(pathmod.join(fs.realpath(directory), pathmod.basename(file_path))):
        raise Refusal('rule file-symlink: the backup file is a symbolic link or lies behind one')
    sidecar_path = file_path + '.json'
    sidecar = None
    if fs.isfile(sidecar_path):
        try:
            blob = fs.read_bytes(sidecar_path, SIDECAR_MAX_BYTES + 1)
        except OSError:
            raise Refusal('rule sidecar: the sidecar cannot be read') from None
        sidecar = read_sidecar(blob)
        if sidecar['database'].lower() != db.lower():
            raise Refusal('rule sidecar: the sidecar describes a different database')
        if sidecar['file'] != pathmod.basename(file_path):
            raise Refusal('rule sidecar: the sidecar describes a different file')
        if sidecar['server']['canonical'] != spec.canonical:
            raise Refusal('rule sidecar: the backup comes from a different server')
        try:
            actual_size = fs.getsize(file_path)
        except OSError:
            raise Refusal('rule sidecar: the backup file size cannot be read') from None
        if sidecar['size'] is None or sidecar['size'] != actual_size:
            raise Refusal('rule sidecar: the file size differs from the sidecar')
        if 'sha256' not in sidecar:
            warnings.append('no sha256 recorded in the sidecar; the content of the copy was not checked against a digest')
    elif opts.allow_missing_sidecar:
        warnings.append('the sidecar is missing; the backup origin was checked only against the target engine (--allow-missing-sidecar)')
    else:
        raise Refusal('rule sidecar: the sidecar file is missing (use --allow-missing-sidecar to proceed without it)')
    # (2) dry run
    if opts.dry_run:
        plan = ['read the database state', 'check the engine identity', 'read the backup header (HEADERONLY)', 'verify the backup (VERIFYONLY)',
                'skip the safety backup (risky: no way back)' if opts.no_safety_backup else 'make a safety backup (label pre-restore)',
                'restore with REPLACE (SINGLE_USER, then MULTI_USER)', 'read the final database state']
        notrun = NotRun('dry run: nothing was executed')
        notrun.result = {'status': 'NOT_RUN', 'database': db, 'file': file_path, 'plan': plan, 'warnings': warnings}
        raise notrun
    # (3) database state
    row = _state_row(runner, db, opts.state_timeout)
    if row is None:
        raise Refusal('restoring a non-existent database is not supported')
    if row['state_desc'] != 'ONLINE':
        raise Refusal('the database is in state ' + row['state_desc'] + '; bring it ONLINE (or finish the pending restore or recovery) and retry')
    previous_state = _m({'state_desc': row['state_desc'], 'user_access_desc': row['user_access_desc'], 'recovery_model_desc': row['recovery_model_desc']})
    if row['recovery_model_desc'] == 'FULL':
        warnings.append('the database uses the FULL recovery model; the restore breaks the log backup chain')
    # (4) target engine identity
    identity = _identity(runner, opts.state_timeout)
    if not machine_names_match(identity['machine_name'], machine_name):
        if not opts.allow_machine_mismatch:
            raise Refusal('the engine runs on a different machine than this one; restore is allowed only for a local engine')
        warnings.append('the engine machine name differs from this machine (allowed by --allow-machine-mismatch)')
    # (5) backup header
    header_rows = _table_row(runner(build_headeronly_sql(file_path), mutating=False, timeout=min(opts.timeout, 120.0), table=True),
                             'RESTORE HEADERONLY')
    _check_restore_header(header_rows, db, sidecar, identity, opts, warnings)
    # (6) verify
    _checked(runner(build_verify_sql(file_path), mutating=False, timeout=opts.restore_timeout), 'RESTORE VERIFYONLY')
    # (6b) the digest of the copy against the sidecar, before anything is changed
    if sidecar is not None and 'sha256' in sidecar and fs.sha256(file_path) != sidecar['sha256']:
        raise Refusal('rule sha256: the copy does not match the sha256 in the sidecar')
    # (7) safety backup
    safety = None
    if opts.no_safety_backup:
        warnings.append('risky: no safety backup (--no-safety-backup), there is no way back')
    else:
        backup_opts = BackupOptions(database=db, label='pre-restore', server=opts.server, dir=directory, auth=opts.auth, timeout=opts.timeout,
                                    trust_server_certificate=opts.trust_server_certificate)
        saved = run_backup(backup_opts, runner=runner, fs=fs, clock=clock, token_fn=token_fn, state=state, environ=environ, platform=platform, masker=masker)
        safety = saved['path']
        emit(_m('safety backup: ' + safety))
    undo = _restore_command(safety, db, opts.server, directory if opts.dir else None, opts.trust_server_certificate, local_flavor == 'windows') if safety else None
    emit('restoring database ' + db + ' from the backup file')
    # (8) destructive step
    try:
        _checked(runner(build_restore_batch(db, file_path), mutating=True, timeout=opts.restore_timeout), 'RESTORE DATABASE')
    except (Exception, KeyboardInterrupt) as exc:
        reason = _failure_reason(exc)
        actual, state_desc = _actual_state(runner, db, opts.state_timeout)
        raise SqlFailure(
            'RESTORE DATABASE did not finish cleanly (' + reason + '). Database state read afterwards: ' + actual + '. After a failure, a timeout or '
            'an interruption the state of the database is uncertain (it may have changed); no automatic repair was attempted. '
            + _way_back(safety, db, undo, state_desc)) from None
    # (9) state after the restore
    try:
        after = _state_row(runner, db, opts.state_timeout)
    except (Exception, KeyboardInterrupt) as exc:
        raise SqlFailure('the restore finished but the final state could not be read (' + _failure_reason(exc) + '). '
                         + _way_back(safety, db, undo, None)) from None
    if after is None or after['state_desc'] != 'ONLINE' or after['user_access_desc'] != 'MULTI_USER':
        actual = 'unknown' if after is None else 'state_desc=' + after['state_desc'] + ', user_access_desc=' + after['user_access_desc']
        raise SqlFailure('the restore finished but the database is not ONLINE and MULTI_USER (state: ' + actual + '). '
                         + _way_back(safety, db, undo, None if after is None else after['state_desc']))
    # (10) result
    now = clock()
    now = now.astimezone(datetime.timezone.utc) if now.tzinfo is not None else now
    result = {'status': 'PASS', 'database': db, 'file': file_path, 'server': opts.server, 'safety_backup_path': safety,
              'previous_state': previous_state, 'warnings': _m(warnings), 'restored_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
              'rollback_command': undo}
    result['coordinator_notice'] = coordinator_notice(result, 'restore')
    return result


# ---------------------------------------------------------------- retention (prune): only files this tool made, only with --confirm

PRUNE_MIN_AGE_SECONDS = 3600          # a copy or sidecar younger than this is never deleted (a backup may still be writing it)
PRUNE_FUTURE_SLACK_SECONDS = 300      # a created_utc further in the future than this is not trusted
PRUNE_MAX_ENTRIES = 10000
PRUNE_KEEP_DEFAULT = 5
PRUNE_KEEP_DEFAULTS = {'pre-restore': 3}
PRUNE_KEEP_MAX = 1000
PRUNE_DAYS_MAX = 36500
PRUNE_LIST_LIMIT = 50
_SNAPSHOT_NAME_RE = re.compile(r'([A-Za-z0-9_]{1,128})-([a-z0-9-]{1,40})-(\d{8})-(\d{6})-([0-9a-f]{6})\.bak', re.ASCII)
_CREATED_UTC_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', re.ASCII)


def parse_snapshot_name(name):
    """The parts of a copy name made by `unique_name`, or None. The database has no hyphen, so the tail
    `-<8 digits>-<6 digits>-<6 hex>.bak` makes the reading unambiguous (a hyphen after the database starts the label)."""
    match = _SNAPSHOT_NAME_RE.fullmatch(name) if isinstance(name, str) else None
    if match is None:
        return None
    return {'database': match.group(1), 'label': match.group(2), 'date': match.group(3), 'time': match.group(4), 'token': match.group(5)}


PruneItem = collections.namedtuple('PruneItem', 'kind bak sidecar bak_stat sidecar_stat database label')


class PrunePlan:
    """What `prune` would do: items to delete, entries kept (with the reason) and entries skipped (with the reason)."""

    def __init__(self, delete, kept, skipped):
        self.delete = delete
        self.kept = kept
        self.skipped = skipped

    @property
    def skipped_total(self):
        return len(self.skipped)


def _stamp_of(parsed):
    """The UTC time of a name as epoch seconds, plus the `created_utc` text it implies; None for an impossible calendar date."""
    date, time = parsed['date'], parsed['time']
    try:
        moment = datetime.datetime(int(date[:4]), int(date[4:6]), int(date[6:]), int(time[:2]), int(time[2:4]), int(time[4:]), tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return moment.timestamp(), moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def plan_prune(fs, directory, database, label, keep, older_than_days, now):
    """What `prune` would delete, keep and skip (no change is made). Only a regular `.bak` with a local sidecar that matches its name
    (database, label, file, size, created_utc equal to the stamp in the name) is ours; anything else is skipped with a reason."""
    if keep is not None and (type(keep) is not int or keep < 1 or keep > PRUNE_KEEP_MAX):
        raise Refusal('rule prune-keep: --keep must be a whole number from 1 to ' + str(PRUNE_KEEP_MAX))
    if older_than_days is not None and (type(older_than_days) is not int or older_than_days < 1 or older_than_days > PRUNE_DAYS_MAX):
        raise Refusal('rule prune-older-than-days: --older-than-days must be a whole number from 1 to ' + str(PRUNE_DAYS_MAX))
    pathmod = _path_module(directory)
    try:
        names = list(fs.listdir(directory))
    except OSError:
        raise Refusal('rule prune-directory: the backup directory cannot be listed') from None
    if len(names) > PRUNE_MAX_ENTRIES:
        raise Refusal('rule prune-entries: the backup directory holds more than ' + str(PRUNE_MAX_ENTRIES) + ' entries')
    now_ts = now.timestamp()
    wanted = database.lower()
    present_bak = {item.lower() for item in names if item.lower().endswith('.bak')}
    kept, skipped, groups, orphans = [], [], {}, []

    def skip(name, reason):
        skipped.append({'file': name, 'reason': reason})

    def read_own_sidecar(path):
        """(sidecar dict, entry) or (None, reason)."""
        try:
            entry = fs.lstat(path)
        except OSError:
            return None, 'no-sidecar'
        if entry.kind != 'file':
            return None, 'sidecar-not-regular-file'
        try:
            blob = fs.read_bytes(path, SIDECAR_MAX_BYTES + 1)
        except OSError:
            return None, 'sidecar-unreadable'
        try:
            data = read_sidecar(blob)
        except Refusal:
            return None, 'sidecar-invalid'
        return (data, entry), None

    for name in sorted(names):
        full = pathmod.join(directory, name)
        is_sidecar = name.endswith('.bak.json')
        bak_name = name[:-len('.json')] if is_sidecar else name
        parsed = parse_snapshot_name(bak_name) if (is_sidecar or name.endswith('.bak')) else None
        if parsed is None:
            skip(name, 'not-a-snapshot-file')
            continue
        if parsed['database'].lower() != wanted:
            skip(name, 'other-database')
            continue
        if label is not None and parsed['label'] != label:
            skip(name, 'other-label')
            continue
        if is_sidecar and bak_name.lower() in present_bak:
            continue   # handled together with its .bak (whatever kind of entry that is): not an orphan
        side_path = full if is_sidecar else full + '.json'
        side_name = pathmod.basename(side_path)
        if not is_sidecar:
            try:
                bak_entry = fs.lstat(full)
            except OSError:
                skip(name, 'bak-not-regular-file')
                continue
            if bak_entry.kind != 'file':
                skip(name, 'bak-not-regular-file')
                continue
            if side_name not in names:
                skip(name, 'no-sidecar')
                continue
        found, reason = read_own_sidecar(side_path)
        if found is None:
            skip(name, reason)
            continue
        data, side_entry = found
        if data['remote'] is not False:
            skip(name, 'sidecar-remote')
            continue
        if (not isinstance(data['database'], str) or data['database'].lower() != parsed['database'].lower() or data['label'] != parsed['label']
                or data['file'] != bak_name or (not is_sidecar and (data['size'] is None or data['size'] != bak_entry.size))):
            skip(name, 'sidecar-mismatch')
            continue
        stamp = _stamp_of(parsed)
        expected_text = '%s-%s-%sT%s:%s:%sZ' % (parsed['date'][:4], parsed['date'][4:6], parsed['date'][6:], parsed['time'][:2], parsed['time'][2:4], parsed['time'][4:])
        if _CREATED_UTC_RE.fullmatch(data['created_utc']) is None or data['created_utc'] != expected_text:
            skip(name, 'created-utc-mismatch')
            continue
        if stamp is None:
            skip(name, 'impossible-date')
            continue
        created_ts = stamp[0]
        if is_sidecar:
            if now_ts - side_entry.mtime < PRUNE_MIN_AGE_SECONDS:
                kept.append({'file': name, 'reason': 'orphan-too-young'})
            else:
                orphans.append(PruneItem('orphan', None, name, None, side_entry, parsed['database'], parsed['label']))
            continue
        if created_ts > now_ts + PRUNE_FUTURE_SLACK_SECONDS:
            skip(name, 'future-timestamp')
            continue
        groups.setdefault((parsed['database'].lower(), parsed['label']), []).append(
            {'name': name, 'created': created_ts, 'bak': bak_entry, 'side': side_entry, 'side_name': side_name, 'database': parsed['database']})
    delete = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda m: (m['created'], m['name']), reverse=True)
        limit = keep if keep is not None else PRUNE_KEEP_DEFAULTS.get(key[1], PRUNE_KEEP_DEFAULT)
        planned = []
        for index, member in enumerate(members):
            newest = max(member['created'], member['bak'].mtime)
            if index < limit:
                kept.append({'file': member['name'], 'reason': 'within-newest'})
            elif now_ts - newest < PRUNE_MIN_AGE_SECONDS:
                kept.append({'file': member['name'], 'reason': 'too-young'})
            elif older_than_days is not None and now_ts - newest < older_than_days * 86400:
                kept.append({'file': member['name'], 'reason': 'newer-than-days'})
            else:
                planned.append(PruneItem('copy', member['name'], member['side_name'], member['bak'], member['side'], member['database'], key[1]))
        delete.extend(reversed(planned))   # the oldest first
    delete.extend(orphans)
    return PrunePlan(delete=delete, kept=kept, skipped=skipped)


PRUNE_PARTIAL_LINE = 'pliki kopii zostały częściowo usunięte'


class PruneOptions:
    def __init__(self, database, confirm=None, label=None, keep=None, older_than_days=None, dir=None, dry_run=False):
        self.database, self.confirm, self.label, self.keep = database, confirm, label, keep
        self.older_than_days, self.dir, self.dry_run = older_than_days, dir, dry_run


class PruneProgress:
    """What a prune has done so far; read by the wrapper to report a partial result after an exception."""

    def __init__(self):
        self.deleted = []
        self.failed = []
        self.skipped = []
        self.context = {}

    def partial_result(self, exc):
        failed = list(self.failed) + [{'file': None, 'reason': 'stopped by ' + type(exc).__name__}]
        return dict(self.context, status='FAIL', deleted=list(self.deleted), failed=failed, kept=[], skipped=list(self.skipped)[:PRUNE_LIST_LIMIT],
                    skipped_total=len(self.skipped), dry_run=False)


def _prune_delete_one(fs, path, name, expected, progress):
    """Delete one file after a fresh lstat: 'deleted', 'gone' (already removed: not a failure), 'changed' (not the file that was planned) or 'failed'."""
    try:
        current = fs.lstat(path)
    except FileNotFoundError:
        return 'gone'
    except OSError as exc:
        progress.failed.append({'file': name, 'reason': exc.strerror or type(exc).__name__})
        return 'failed'
    if current.kind != 'file' or current.size != expected.size or current.mtime != expected.mtime:
        progress.skipped.append({'file': name, 'reason': 'changed-since-planned'})
        return 'changed'
    try:
        fs.unlink(path)
    except FileNotFoundError:
        return 'gone'
    except OSError as exc:
        progress.failed.append({'file': name, 'reason': exc.strerror or type(exc).__name__})
        return 'failed'
    progress.deleted.append(name)
    return 'deleted'


def execute_prune(fs, directory, plan, progress):
    """The copy first, then its sidecar (an interrupted prune leaves at most an orphan sidecar, which the next prune removes); the sidecar of a
    copy that was not deleted stays; one failing file does not stop the others."""
    pathmod = _path_module(directory)
    for item in plan.delete:
        if item.kind == 'copy':
            outcome = _prune_delete_one(fs, pathmod.join(directory, item.bak), item.bak, item.bak_stat, progress)
            if outcome in ('failed', 'changed'):
                continue
        _prune_delete_one(fs, pathmod.join(directory, item.sidecar), item.sidecar, item.sidecar_stat, progress)


def run_prune(opts, *, fs, clock, environ, platform, progress, masker=None):
    db = validate_database(opts.database)
    if not isinstance(opts.confirm, str) or opts.confirm != opts.database:
        raise Refusal('rule confirm: --confirm must repeat the database name exactly')
    if opts.label is not None:
        validate_label(opts.label)
    local_flavor = 'windows' if platform == 'win32' else 'posix'
    home = environ.get('HOME') or environ.get('USERPROFILE') or os.path.expanduser('~')
    directory = validate_server_path(opts.dir or default_backup_dir(platform, environ, home), flavor=local_flavor, kind='dir')
    check_dir_outside_repo(directory, lexists=fs.lexists, realpath=fs.realpath)
    real = fs.realpath(directory)   # resolved once: every listing and deletion uses this path
    if not fs.isdir(real):
        raise Refusal('rule prune-directory: the backup directory does not exist')
    now = clock()
    now = now.astimezone(datetime.timezone.utc) if now.tzinfo is not None else now.replace(tzinfo=datetime.timezone.utc)
    plan = plan_prune(fs, real, db, opts.label, opts.keep, opts.older_than_days, now)
    progress.context = {'database': db, 'dir': real, 'label': opts.label, 'older_than_days': opts.older_than_days}
    warnings = [] if opts.label is not None else ['without --label every label of this database is included, also the ones named by hand']
    if opts.dry_run:
        would = []
        for item in plan.delete:
            would.extend([item.bak, item.sidecar] if item.kind == 'copy' else [item.sidecar])
        notrun = NotRun('dry run: nothing was deleted')
        notrun.result = dict(progress.context, status='NOT_RUN', dry_run=True, would_delete=would, kept=plan.kept, skipped=plan.skipped[:PRUNE_LIST_LIMIT],
                             skipped_total=plan.skipped_total, warnings=warnings, keep=opts.keep)
        raise notrun
    execute_prune(fs, real, plan, progress)
    skipped = plan.skipped + progress.skipped
    status = 'PASS' if not progress.failed else ('FAIL' if progress.deleted else 'BLOCKED')
    return dict(progress.context, status=status, deleted=list(progress.deleted), kept=plan.kept, skipped=skipped[:PRUNE_LIST_LIMIT],
                skipped_total=len(skipped), failed=list(progress.failed), dry_run=False)


def run_prune_guarded(work, progress, out, err, masker, as_json):
    """The one wrapper around a whole prune (it has no engine, so no RunState): any exception after a deletion is exit 1 with the partial result and
    a line saying files were partly removed; before any deletion it is exit 2; never a traceback and never a code outside 0..3."""
    try:
        code = work()
        return EXIT['PASS'] if code is None else code
    except SystemExit:
        raise
    except BaseException as exc:
        err.write(type(exc).__name__ + ': ' + masker.mask(_safe_message(exc)) + '\n')
        if isinstance(exc, NotRun):
            return EXIT['NOT_RUN']
        if isinstance(exc, Refusal) or not progress.deleted:
            return EXIT['BLOCKED']
        err.write(PRUNE_PARTIAL_LINE + '\n')
        out.write(format_result(progress.partial_result(exc), as_json, masker) + '\n')
        return EXIT['FAIL']


def format_result(result, as_json, masker):
    safe = masker.mask_value(result)
    if as_json:
        return masker.mask(json.dumps(safe, sort_keys=True, indent=2, ensure_ascii=False))
    lines = []
    for key in sorted(safe):
        value = safe[key]
        if key == 'status':
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, ensure_ascii=False)
        lines.append(key + ': ' + str(value))
    return masker.mask(str(safe.get('status')) + '\n' + '\n'.join(lines))


# ---------------------------------------------------------------- S4: selftest (in memory, no process, no disk)

class _MemFs:
    """A tiny in-memory file system for the selftest."""

    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.links = set()
        self.mtimes = {}

    def makedirs(self, path, mode):
        self.dirs.add(path)

    def listdir(self, path):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip('/') + '/'
        names = {item[len(prefix):] for item in list(self.files) + list(self.dirs) + list(self.links)
                 if item.startswith(prefix) and '/' not in item[len(prefix):]}
        return sorted(names)

    def lstat(self, path):
        if path in self.links:
            return FsEntry('link', len(self.files.get(path, b'')), self.mtimes.get(path, 0.0))
        if path in self.files:
            return FsEntry('file', len(self.files[path]), self.mtimes.get(path, 0.0))
        if path in self.dirs:
            return FsEntry('dir', 0, self.mtimes.get(path, 0.0))
        raise FileNotFoundError(path)

    def isdir(self, path):
        return path in self.dirs

    def unlink(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]
        self.links.discard(path)

    def exists(self, path):
        return path in self.files

    def getsize(self, path):
        return len(self.files[path])

    def realpath(self, path):
        return path

    def lexists(self, path):
        return path in self.files or path in self.dirs

    def sha256(self, path):
        import hashlib
        return hashlib.sha256(self.files[path]).hexdigest()

    def isfile(self, path):
        return path in self.files

    def read_bytes(self, path, limit):
        return self.files[path][:limit + 1]

    def write_atomic(self, path, data):
        self.files[path] = bytes(data)


def _table_text(columns, rows):
    lines = ['|'.join(columns), '|'.join('-' * len(c) for c in columns)]
    lines.extend('|'.join(row) for row in rows)
    return '\n'.join(lines) + '\n\n(' + str(len(rows)) + ' rows affected)\n'


class _MemEngine:
    """A stateful fake engine: BACKUP stores a snapshot of the data, RESTORE brings it back."""

    def __init__(self, fs, node):
        self.fs = fs
        self.node = node
        self.data = 'original'
        self.calls = []
        self.mutations = 0

    def __call__(self, sql, *, mutating, timeout, table=False):
        self.calls.append(sql)
        if mutating:
            self.mutations += 1
        if sql.startswith('SELECT @@SERVERNAME'):
            return RunResult(0, _table_text(['server_name', 'machine_name', 'instance_name'], [[self.node, self.node, 'NULL']]), '')
        if sql.startswith('SELECT state_desc'):
            return RunResult(0, _table_text(['state_desc', 'user_access_desc', 'recovery_model_desc'], [['ONLINE', 'MULTI_USER', 'SIMPLE']]), '')
        found = re.fullmatch(r"BACKUP DATABASE \[(\w+)\] TO DISK = N'(.*)' WITH COPY_ONLY, INIT, CHECKSUM", sql)
        if found:
            snapshot = {'db': found.group(1), 'data': self.data}
            self.fs.files[found.group(2)] = json.dumps(snapshot).encode('utf-8')
            return RunResult(0, 'BACKUP DATABASE successfully processed.\n', '')
        if sql.startswith('RESTORE VERIFYONLY'):
            return RunResult(0, 'The backup set on file 1 is valid.\n', '')
        found = re.fullmatch(r"RESTORE HEADERONLY FROM DISK = N'(.*)'", sql)
        if found:
            snapshot = json.loads(self.fs.files[found.group(1)].decode('utf-8'))
            columns = ['DatabaseName', 'BackupType', 'ServerName', 'MachineName', 'BackupSetGUID', 'FirstLSN', 'LastLSN', 'BackupFinishDate']
            row = [snapshot['db'], '1', self.node, self.node, 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE', '100', '150', '2026-11-22 10:20:30.000']
            return RunResult(0, _table_text(columns, [row]), '')
        found = re.search(r"RESTORE DATABASE \[(\w+)\] FROM DISK = N'(.*?)' WITH REPLACE", sql)
        if found:
            self.data = json.loads(self.fs.files[found.group(2)].decode('utf-8'))['data']
            return RunResult(0, 'RESTORE DATABASE successfully processed.\n', '')
        return RunResult(1, 'Msg 102, Level 15, State 1, Line 1 Incorrect syntax.\n', '')


def _check(condition, what='check failed'):
    if not condition:
        raise AssertionError(what)


def _refuses(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except Refusal:
        return True
    return False


def _selftest_world():
    fs = _MemFs()
    engine = _MemEngine(fs, 'selftesthost')
    clock = lambda: datetime.datetime(2026, 11, 22, 10, 20, 30, tzinfo=datetime.timezone.utc)
    return fs, engine, clock


def _st_database_name():
    for bad in ('master', 'TempDB', 'app]db', 'a\nb', '', 'a b', "a'b", 'x' * 129):
        _check(_refuses(validate_database, bad), 'accepted ' + repr(bad))
    _check(validate_database('appdb') == 'appdb')


def _st_label():
    for bad in ('', 'BAD', 'a b', 'a_b', 'x' * 41):
        _check(_refuses(validate_label, bad), 'accepted ' + repr(bad))
    _check(validate_label('before-test') == 'before-test')


def _st_hosts():
    _check(parse_server('localhost').kind == 'local')
    _check(parse_server('(local)').kind == 'local')
    remote = parse_server('db1.example.test')
    _check(remote.kind == 'remote')
    _check(_refuses(check_host_allowed, remote, (), 'backup'))
    _check(check_host_allowed(remote, ('DB1.example.test',), 'backup') is True)
    _check(_refuses(check_host_allowed, remote, ('db1.example',), 'backup'), 'prefix accepted')
    _check(_refuses(check_host_allowed, remote, ('db1.example.test.evil',), 'backup'))
    _check(_refuses(check_host_allowed, remote, ('db1.example.test',), 'restore'), 'remote restore accepted')


def _st_host_lookalike_evil_and_127_0_0_2():
    for text in ('localhost.evil.example', '127.0.0.2', 'localhost.', '127.1', '0.0.0.0', 'tcp:localhost', '(localhostx)'):
        spec = None
        try:
            spec = parse_server(text)
        except Refusal:
            continue
        _check(spec.kind == 'remote', 'a look-alike host was taken for local: ' + text)


def _st_host_port():
    _check(parse_server('localhost,1434').port == 1434 and parse_server('localhost,1434').kind == 'local')
    _check(parse_server('localhost,1434').canonical != parse_server('localhost').canonical)
    for bad in ('localhost,0', 'localhost,65536', 'localhost,'):
        _check(_refuses(parse_server, bad), 'accepted ' + bad)


def _st_host_instance():
    _check(parse_server('.\\SQLEXPRESS').canonical != parse_server('.').canonical)
    _check(parse_server('localhost\\SQLEXPRESS').canonical == parse_server('.\\sqlexpress').canonical)
    _check(parse_server('(localdb)\\app').localdb)
    _check(_refuses(parse_server, 'localhost\\' + 'x' * 17))


def _st_dir_outside_repo():
    def lexists(path):
        return path == '/h/repo/.git'
    check_dir_outside_repo('/h/other/out', lexists=lexists, realpath=lambda path: path)
    _check(_refuses(check_dir_outside_repo, '/h/repo/out', lexists=lexists, realpath=lambda path: path), 'a directory inside a repository was accepted')


def _st_table_parser_trailing_text():
    for text in ('a|b\n-|-\n1|2\n\n(1 rows affected)\nforged|row\n', 'a|b\n-|-\n1|2\n\n(2 rows affected)\n'):
        try:
            parse_sqlcmd_table(text)
        except SqlFailure:
            continue
        raise AssertionError('forged table text accepted')


def _st_undo_command_is_complete():
    command = _restore_command('/h/c/a.bak', 'appdb', 'localhost\\INST$1', '/h/c', True)
    _check(command == "db_snapshot.py restore --file /h/c/a.bak --database appdb --confirm appdb --server 'localhost\\INST$1' --dir /h/c "
                      "--trust-server-certificate", command)


def _st_server_paths():
    _check(validate_server_path('/h/copies/a.bak', flavor='posix', kind='file') == '/h/copies/a.bak')
    for flavor, bad in (('windows', '\\\\host\\share\\a.bak'), ('posix', '/h/../a.bak'), ('posix', "/h/it's/a.bak"),
                        ('posix', '/h/caf\u00e9/a.bak'), ('posix', 'relative/a.bak'), ('windows', 'D:\\h\\NUL\\a.bak'), ('posix', '/h/a.txt')):
        _check(_refuses(validate_server_path, bad, flavor=flavor, kind='file'), 'accepted ' + repr(bad))


def _st_sql_builders():
    _check(quote_literal("a'b") == "N'a''b'")
    _check(_refuses(quote_ident, 'a]b'))
    sql = build_backup_sql('appdb', '/h/c/a.bak')
    _check(sql.startswith('BACKUP DATABASE [appdb] TO DISK = N') and 'COPY_ONLY' in sql)
    _check("N'/h/c/it''s.bak'" in build_verify_sql("/h/c/it's.bak"))
    batch = build_restore_batch('appdb', '/h/c/a.bak')
    _check('SINGLE_USER' in batch and 'MULTI_USER' in batch and 'REPLACE' in batch)


def _st_sqlcmd_args():
    args = sqlcmd_args('localhost', 'SELECT 1', auth='env', trust_server_certificate=False, server_is_local=True)
    _check(args[args.index('-d') + 1] == 'master' and '-b' in args)
    _check(not any(flag in args for flag in ('-U', '-P', '-E')))
    _check('-E' in sqlcmd_args('localhost', 'SELECT 1', auth='integrated', trust_server_certificate=False, server_is_local=True))
    _check(_refuses(sqlcmd_args, 'db1.example.test', 'SELECT 1', auth='env', trust_server_certificate=True, server_is_local=False))


def _st_child_env():
    source = {'PATH': '/bin', 'HOME': '/h', 'SQLCMDINI': '/h/x.ini', 'SQLCMDDBNAME': 'other', 'PYTHONPATH': '/h', 'SQLCMDUSER': 'u', 'SQLCMDPASSWORD': 'p'}
    env = child_env(source, 'integrated', 'linux')
    _check(sorted(env) == ['HOME', 'PATH'], 'whitelist broken: ' + repr(sorted(env)))
    env = child_env(source, 'env', 'linux')
    _check('SQLCMDPASSWORD' in env and 'SQLCMDINI' not in env)
    _check(_refuses(child_env, {'PATH': '/bin'}, 'env', 'linux'))


def _st_masker():
    secret = 'selftest-' + 'marker' + '-1+/='
    masker = Masker([secret])
    quoted = urllib.parse.quote(secret, safe='')
    encoded = base64.b64encode(secret.encode('utf-8')).decode('ascii')
    for text in (secret, quoted, encoded, json.dumps(secret)):
        masked = masker.mask('before ' + text + ' after')
        _check(secret not in masked and quoted not in masked and encoded.rstrip('=') not in masked, 'not masked: ' + text)
    _check('***' in masker.mask(secret))
    _check(masker.mask('plain') == 'plain')


def _st_exit_codes():
    fresh, mutated = RunState(), RunState()
    mutated.mark_mutating()
    _check(exit_code_for(Refusal('x'), mutated) == 2)
    _check(exit_code_for(NotRun('x'), mutated) == 3)
    _check(exit_code_for(SqlFailure('x'), fresh) == 2, 'a failed step before any change must be BLOCKED')
    _check(exit_code_for(SqlFailure('x'), mutated) == 1, 'a failed step after a change must be FAIL')
    _check(exit_code_for(RuntimeError('x'), fresh) == 2, 'unexpected error before a change must be BLOCKED')
    _check(exit_code_for(RuntimeError('x'), mutated) == 1, 'unexpected error after a change must be FAIL')


def _st_table_parser():
    text = _table_text(['b', 'a'], [['2', '1']])
    _check(parse_sqlcmd_table(text) == [{'b': '2', 'a': '1'}], 'columns must be read by header name')
    try:
        parse_sqlcmd_table('a|a\n-|-\n1|2\n')
    except SqlFailure:
        return
    raise AssertionError('duplicate header accepted')


def _st_restore_needs_confirm():
    fs, engine, clock = _selftest_world()
    for confirm in (None, '', 'otherdb'):
        opts = RestoreOptions(database='appdb', file='/h/c/a.bak', confirm=confirm, dir='/h/c')
        try:
            run_restore(opts, runner=engine, fs=fs, clock=clock, token_fn=lambda: 'a1b2c3', state=RunState(), environ={'HOME': '/h/u'},
                        platform='linux', machine_name='selftesthost', emit=lambda text: None)
        except Refusal as exc:
            _check('confirm' in str(exc))
        else:
            raise AssertionError('restore without a valid --confirm went on')
    _check(engine.calls == [], 'the engine was called')


def _st_dry_run():
    fs, engine, clock = _selftest_world()
    opts = BackupOptions(database='appdb', label='snap', dir='/h/c', dry_run=True)
    try:
        run_backup(opts, runner=engine, fs=fs, clock=clock, token_fn=lambda: 'a1b2c3', state=RunState(), environ={'HOME': '/h/u'}, platform='linux')
    except NotRun as exc:
        _check(getattr(exc, 'result', {}).get('status') == 'NOT_RUN')
    else:
        raise AssertionError('a dry run did not stop')
    _check(engine.calls == [] and fs.files == {} and fs.dirs == set(), 'a dry run touched the world')


def _st_acceptance():
    fs, engine, clock = _selftest_world()
    state = RunState()
    environ = {'HOME': '/h/u'}
    common = dict(runner=engine, fs=fs, clock=clock, token_fn=lambda: 'a1b2c3', state=state, environ=environ, platform='linux')
    made = run_backup(BackupOptions(database='appdb', label='snap', dir='/h/c'), **common)
    _check(made['status'] == 'PASS' and made['path'] in fs.files and made['path'] + '.json' in fs.files)
    engine.data = 'changed by someone else'
    restored = run_restore(RestoreOptions(database='appdb', file=made['path'], confirm='appdb', dir='/h/c'), machine_name='selftesthost',
                           emit=lambda text: None, **common)
    _check(restored['status'] == 'PASS' and engine.data == 'original', 'the restore did not bring the state back')
    safety = restored['safety_backup_path']
    _check(safety in fs.files and 'pre-restore' in safety, 'no safety backup')
    _check(safety in restored['rollback_command'] and restored['coordinator_notice'])
    run_restore(RestoreOptions(database='appdb', file=safety, confirm='appdb', dir='/h/c', no_safety_backup=True), machine_name='selftesthost',
                emit=lambda text: None, **common)
    _check(engine.data == 'changed by someone else', 'the way back did not work')


def _st_failed_backup_changed_state_line():
    fs, engine, clock = _selftest_world()
    state = RunState()

    def failing(sql, *, mutating, timeout, table=False):
        if mutating:
            return RunResult(1, 'Msg 3201, Level 16, State 1 Cannot open backup device.\n', '')
        return engine(sql, mutating=mutating, timeout=timeout, table=table)
    errors = []

    class Sink:
        def write(self, text):
            errors.append(text)
    code = run_guarded(lambda: run_backup(BackupOptions(database='appdb', label='snap', dir='/h/c'), runner=guard_runner(failing, state), fs=fs,
                                          clock=clock, token_fn=lambda: 'a1b2c3', state=state, environ={'HOME': '/h/u'}, platform='linux'),
                       state, Sink(), Masker([]))
    _check(code == 1 and CHANGED_STATE_LINE in ''.join(errors))


def _st_sidecar():
    for bad in (b'', b'[]', b'{"schema": 2}', b'{"schema": 1, "schema": 1}', b'\xff'):
        _check(_refuses(read_sidecar, bad), 'accepted ' + repr(bad))


SELFTEST_CASES = (
    ('database-name', _st_database_name), ('label', _st_label), ('hosts', _st_hosts),
    ('host-lookalike-evil-and-127.0.0.2', _st_host_lookalike_evil_and_127_0_0_2), ('host-port', _st_host_port), ('host-instance', _st_host_instance),
    ('dir-outside-repo', _st_dir_outside_repo), ('server-paths', _st_server_paths),
    ('sql-builders', _st_sql_builders), ('sqlcmd-args-no-credentials', _st_sqlcmd_args), ('child-env-whitelist', _st_child_env),
    ('masker-variants', _st_masker), ('exit-codes', _st_exit_codes), ('table-parser', _st_table_parser),
    ('table-parser-trailing-text', _st_table_parser_trailing_text), ('undo-command-complete', _st_undo_command_is_complete), ('sidecar-parser', _st_sidecar),
    ('restore-needs-confirm', _st_restore_needs_confirm), ('dry-run-no-calls', _st_dry_run),
    ('acceptance-copy-change-restore', _st_acceptance), ('failed-backup-says-state-changed', _st_failed_backup_changed_state_line),
)


def run_selftest(stdout):
    passed = 0
    for name, case in SELFTEST_CASES:
        try:
            case()
        except Exception:
            stdout.write('FAIL ' + name + '\n')
        else:
            passed += 1
            stdout.write('PASS ' + name + '\n')
    stdout.write('selftest db_snapshot: ' + str(passed) + '/' + str(len(SELFTEST_CASES)) + ' passed\n')
    return EXIT['PASS'] if passed == len(SELFTEST_CASES) else EXIT['FAIL']


# ---------------------------------------------------------------- S4: command line

class _MaskedStream:
    """Every write passes through the masker; the secret never reaches the real stream."""

    def __init__(self, stream, masker):
        self._stream = stream
        self._masker = masker

    def write(self, text):
        return self._stream.write(self._masker.mask(text))

    def flush(self):
        flush = getattr(self._stream, 'flush', None)
        if flush is not None:
            flush()


class _LazyRunner:
    """The engine is looked up at the first call: refusals and dry runs need no sqlcmd."""

    def __init__(self, make):
        self._make = make
        self._runner = None

    def __call__(self, sql, *, mutating, timeout, table=False):
        if self._runner is None:
            self._runner = self._make()
        return self._runner(sql, mutating=mutating, timeout=timeout, table=table)


def _positive_float(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError('must be a number of seconds') from None
    if not 0 < value < float('inf'):
        raise argparse.ArgumentTypeError('must be a positive number of seconds')
    return value


def build_parser():
    parser = argparse.ArgumentParser(prog='db_snapshot.py', allow_abbrev=False,
                                     description='Safe copy and restore of a SQL Server test database through sqlcmd.')
    parser.add_argument('--selftest', action='store_true', help='run the in-memory self test (no database, network or sqlcmd)')
    commands = parser.add_subparsers(dest='command', metavar='{backup,restore}')

    def common(sub):
        sub.add_argument('--server', default='localhost', help='SQL Server (default: localhost)')
        sub.add_argument('--dir', default=None, help='backup directory as the server sees it (default: the state directory)')
        sub.add_argument('--auth', choices=('auto', 'integrated', 'env'), default='auto',
                         help='authentication: integrated, or env (SQLCMDUSER and SQLCMDPASSWORD from the environment)')
        sub.add_argument('--trust-server-certificate', action='store_true', help='pass -C to sqlcmd (local server only)')
        sub.add_argument('--dry-run', action='store_true', help='print the plan and change nothing (exit code 3)')
        sub.add_argument('--json', action='store_true', help='print the result as one JSON document')

    backup = commands.add_parser('backup', allow_abbrev=False, help='make a COPY_ONLY backup with a sidecar')
    backup.add_argument('--database', required=True)
    backup.add_argument('--label', required=True, help='short label [a-z0-9-]')
    common(backup)
    backup.add_argument('--allow-host', action='append', default=[], metavar='HOST', help='allow this remote host (repeatable)')
    backup.add_argument('--server-os', choices=('windows', 'linux'), default=None, help='OS of a remote server')
    backup.add_argument('--no-verify', action='store_true', help='skip RESTORE VERIFYONLY')
    backup.add_argument('--sha256', action='store_true', help='store the SHA-256 of a local copy in the sidecar')
    backup.add_argument('--timeout', type=_positive_float, default=600.0, help='seconds per sqlcmd call (default: 600)')

    restore = commands.add_parser('restore', allow_abbrev=False, help='restore a backup over an existing database (local engine only)')
    restore.add_argument('--file', required=True, help='the .bak file in the backup directory')
    restore.add_argument('--database', required=True)
    restore.add_argument('--confirm', default=None, help='repeat the database name to confirm the overwrite')
    common(restore)
    restore.add_argument('--no-safety-backup', action='store_true', help='risky: skip the safety backup (no way back)')
    restore.add_argument('--allow-missing-sidecar', action='store_true', help='restore without the sidecar file')
    restore.add_argument('--allow-machine-mismatch', action='store_true', help='allow a backup from another machine')
    restore.add_argument('--restore-timeout', type=_positive_float, default=3600.0, help='seconds for the restore (default: 3600)')
    restore.add_argument('--timeout', type=_positive_float, default=600.0, help='seconds for the safety backup (default: 600)')
    return parser


def _make_runner(args, environ, platform, masker, scratch):
    sqlcmd = find_sqlcmd(environ, platform, isfile=os.path.isfile, access=os.access)
    if sqlcmd is None:
        raise NotRun('sqlcmd was not found on PATH; install the sqlcmd command line tool (go-sqlcmd or the Microsoft ODBC tools) and retry')
    auth = resolve_auth(args.auth, platform)
    env = child_env(environ, auth, platform)
    # The child works in a private, empty directory (mode 0700) that is removed after the run: never in the cwd of the tool,
    # the copy directory or the shared temporary directory.
    scratch.append(tempfile.mkdtemp(prefix='db_snapshot-'))
    return SubprocessRunner(sqlcmd_path=sqlcmd, server_raw=args.server, auth=auth, env=env, masker=masker,
                            cwd=scratch[-1], trust_server_certificate=args.trust_server_certificate,
                            server_is_local=parse_server(args.server).kind == 'local')


def main(argv=None, *, environ=None, stdout=None, stderr=None, platform=None):
    environ = dict(os.environ) if environ is None else environ
    platform = sys.platform if platform is None else platform
    real_out = sys.stdout if stdout is None else stdout
    real_err = sys.stderr if stderr is None else stderr
    for stream in (real_out, real_err):
        try:
            stream.reconfigure(errors='replace')  # a console code page must not turn a message into a crash
        except (AttributeError, ValueError, OSError):
            pass
    parser = build_parser()
    try:
        with contextlib.redirect_stdout(real_out), contextlib.redirect_stderr(real_err):
            args = parser.parse_args(argv)
            if not args.selftest and args.command is None:
                parser.error('a subcommand (backup or restore) or --selftest is required')
            if args.selftest and args.command is not None:
                parser.error('--selftest cannot be combined with a subcommand')
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT['BLOCKED']
    if args.selftest:
        return run_selftest(real_out)
    masker = Masker.from_env(environ, args.auth)
    out = _MaskedStream(real_out, masker)
    err = _MaskedStream(real_err, masker)
    state = RunState()
    scratch = []
    runner = guard_runner(_LazyRunner(lambda: _make_runner(args, environ, platform, masker, scratch)), state)
    wiring = dict(runner=runner, fs=RealFs(), clock=lambda: datetime.datetime.now(datetime.timezone.utc),
                  token_fn=lambda: secrets.token_hex(3), state=state, environ=environ, platform=platform, masker=masker)

    def work():
        try:
            if args.command == 'backup':
                opts = BackupOptions(database=args.database, label=args.label, server=args.server, dir=args.dir, auth=args.auth,
                                     allow_hosts=tuple(args.allow_host), server_os=args.server_os, no_verify=args.no_verify, dry_run=args.dry_run,
                                     timeout=args.timeout, trust_server_certificate=args.trust_server_certificate, sha256=args.sha256)
                result = run_backup(opts, **wiring)
            else:
                opts = RestoreOptions(database=args.database, file=args.file, confirm=args.confirm, server=args.server, dir=args.dir, auth=args.auth,
                                      allow_missing_sidecar=args.allow_missing_sidecar, no_safety_backup=args.no_safety_backup,
                                      allow_machine_mismatch=args.allow_machine_mismatch, dry_run=args.dry_run,
                                      restore_timeout=args.restore_timeout, timeout=args.timeout,
                                      trust_server_certificate=args.trust_server_certificate)
                result = run_restore(opts, machine_name=platform_module.node(), emit=lambda text: err.write(text + '\n'), **wiring)
        except NotRun as exc:
            result = getattr(exc, 'result', None)
            if result is None:
                raise
            out.write(format_result(result, args.json, masker) + '\n')
            return EXIT['NOT_RUN']
        out.write(format_result(result, args.json, masker) + '\n')
        return EXIT.get(result.get('status'), EXIT['PASS'])

    try:
        return run_guarded(work, state, err, masker)
    finally:
        for directory in scratch:
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
