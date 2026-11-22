"""Safe copy and restore of a SQL Server test database through sqlcmd (tool of the test-env skill).
S1 holds the pure logic: validators, SQL builders, child environment, secret masking and exit codes.
S2 adds the backup flow, the injected query runner and file system, and result formatting.
The full tool header and the command line are added in a later step.
"""
import base64
import dataclasses
import datetime
import json
import ntpath
import os
import posixpath
import re
import secrets
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
    """The operation failed and the database state may have changed (exit code 1)."""


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
    if isinstance(exc, SqlFailure):
        return EXIT['FAIL']
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
        return fn()
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
        raise Refusal('rule server-path-charset: path has a character outside the whitelist')
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


def check_dir_outside_repo(path, *, lexists, realpath):
    current = realpath(path)
    for _ in range(4096):
        if lexists(os.path.join(current, '.git')):
            raise Refusal('rule dir-outside-repo: directory is inside a git repository')
        parent = os.path.dirname(current)
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
        args.extend(['-W', '-s', '|'])
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
_ROWS_AFFECTED_RE = re.compile(r'\(\d+ rows? affected\)')
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
    for line in lines[position:]:
        line = line.strip()
        if not line or _ROWS_AFFECTED_RE.fullmatch(line):
            break
        fields = line.split('|')
        if len(fields) != len(names):
            raise SqlFailure(HEADER_UNREADABLE)
        rows.append(dict(zip(names, fields)))
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
        done = self.popen([self.sqlcmd_path] + args, env=self.env, cwd=self.cwd, capture_output=True, text=True, timeout=timeout, shell=False)
        out = self.masker.mask(done.stdout or '')
        err = self.masker.mask(done.stderr or '')
        if table:
            if len(out) > MAX_TABLE_OUTPUT or len(err) > MAX_TABLE_OUTPUT:
                raise SqlFailure('output too large')
        else:
            out = out[:MAX_PLAIN_OUTPUT]
            err = err[:MAX_PLAIN_OUTPUT]
        return RunResult(done.returncode, out, err)


class RealFs:
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

    def write_atomic(self, path, data):
        directory = os.path.dirname(path) or '.'
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.' + os.path.basename(path) + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as handle:
                if os.name != 'nt':
                    os.fchmod(handle.fileno(), 0o600)
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


def _checked(result, step, path=None):
    """Raise SqlFailure when the step failed: non-zero rc or a Msg line of severity 11 or higher."""
    found = _ERROR_LINE_RE.search(result.stdout or '') or _ERROR_LINE_RE.search(result.stderr or '')
    if result.rc != 0 or found:
        text = step + ' failed (rc ' + str(result.rc) + ')'
        if path is not None:
            text += '; the copy was left at ' + path
        detail = _excerpt(result)
        if detail:
            text += ': ' + detail
        raise SqlFailure(text)
    return result


def _table_row(result, step, path=None):
    _checked(result, step, path)
    return parse_sqlcmd_table(result.stdout or '')


def _join(flavor, directory, name):
    separator = '\\' if flavor == 'windows' else '/'
    return directory + ('' if directory.endswith(separator) else separator) + name


def _shell_text(text):
    if re.fullmatch(r'[A-Za-z0-9_./:@,-]+', text):
        return text
    return '"' + text + '"'


def _restore_command(path, db, server):
    command = 'db_snapshot.py restore --file ' + _shell_text(path) + ' --database ' + db + ' --confirm ' + db
    if server != 'localhost':
        command += ' --server ' + _shell_text(server)
    return command


def _format_size(size):
    if size is None:
        return 'nieznany (zdalny host)'
    return str(size) + ' B (' + format(size / 1048576.0, '.1f') + ' MiB)'


def coordinator_notice(result, command='backup'):
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
    if row['DatabaseName'].lower() != db.lower():
        raise SqlFailure('the backup header names another database; the copy was left at ' + path)
    if row['BackupType'] != '1':
        raise SqlFailure('the backup is not a full database backup; the copy was left at ' + path)
    return row


def run_backup(opts, *, runner, fs, clock, token_fn, state, environ, platform):
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
        'server': dict(identity, raw=opts.server, canonical=spec.canonical),
        'file': name, 'size': size, 'created_utc': created_utc,
        'backup_set_guid': row['BackupSetGUID'], 'first_lsn': row['FirstLSN'], 'last_lsn': row['LastLSN'],
        'backup_finish_date': row['BackupFinishDate'], 'remote': remote, 'server_path': path, 'copy_only': True,
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
        'sidecar': sidecar_path, 'restore_command': _restore_command(path, db, opts.server),
    }
    result['coordinator_notice'] = coordinator_notice(result)
    return result


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


def main(argv=None):
    sys.stderr.write('NotRun: command line is not implemented yet\n')
    return EXIT['NOT_RUN']


if __name__ == '__main__':
    sys.exit(main())
