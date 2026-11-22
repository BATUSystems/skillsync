"""Safe copy and restore of a SQL Server test database through sqlcmd (tool of the test-env skill).
S1 holds the pure logic: validators, SQL builders, child environment, secret masking and exit codes.
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
import sys
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


def sqlcmd_args(server, sql, *, auth, trust_server_certificate, server_is_local):
    if auth not in ('integrated', 'env'):
        raise Refusal('rule auth-mode: unknown auth mode')
    args = ['-S', server, '-d', 'master', '-b', '-x', '-Q', sql]
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


def main(argv=None):
    sys.stderr.write('NotRun: command line is not implemented yet\n')
    return EXIT['NOT_RUN']


if __name__ == '__main__':
    sys.exit(main())
