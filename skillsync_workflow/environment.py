"""Configured environment lifecycle with verified process ownership."""
import hashlib
import math
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import urlopen
import yaml
from .common import add_project_arguments, contained, emit, load_layer_project, redact, strings, validate_workflow, workflow_config
from .quality import _locks, plan_validation, run_validation, _reap_after_termination
from .selfcmd import self_argv
from .storage import digest, read_json, state_path, write_json


def limit(value, name, maximum=86400):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f'{name} must be finite between 0 and {maximum}')
    return float(value)


def argv(value, name):
    strings(value, name)
    if not value or any('\0' in item for item in value):
        raise ValueError(f'{name} requires nonempty argv without NUL')
    return value


def checks(root, manifest, library, definitions):
    if not isinstance(definitions, list):
        raise ValueError('checks must be a list')
    for item in definitions:
        if not isinstance(item, dict) or 'command' in item:
            raise ValueError('Environment/e2e checks require trusted argv definitions')
        argv(item.get('argv'), 'check.argv')
    validate_workflow({'validation': {'checks': definitions}})
    local = dict(manifest)
    local['workflow'] = dict(manifest.get('workflow', {}))
    local['workflow']['validation'] = {'checks': definitions}
    local['workflow']['guardrails'] = {'rules': [], 'checks': []}
    return plan_validation(root, local, library, profile='all')


def configuration(root, manifest, library):
    config = workflow_config(root, manifest, library).get('environment', {})
    if not isinstance(config, dict):
        raise ValueError('environment must be a mapping')
    if not config:
        return config
    argv(config.get('start'), 'environment.start')
    config = dict(config)
    config['cwd'] = str(contained(root, config.get('cwd', '.')))
    strings(config.get('inputs', []), 'environment.inputs')
    strings(config.get('resources', []), 'environment.resources')
    config['ttl'] = limit(config.get('ttl', 3600), 'environment.ttl', 604800)
    config['startup_timeout'] = limit(config.get('startup_timeout', 30), 'environment.startup_timeout', 3600)
    health = config.get('health')
    if not isinstance(health, dict) or bool(health.get('url')) == bool(health.get('argv')):
        raise ValueError('environment.health requires exactly one url or argv')
    if health.get('argv'):
        argv(health['argv'], 'health.argv')
    else:
        if not isinstance(health['url'], str):
            raise ValueError('Health URL must be a string')
        url = urlsplit(health['url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError('Health URL must be HTTP(S) without credentials')
    config['prepare_checks'] = checks(root, manifest, library, config.get('prepare_checks', []))['checks']
    return config


def source_digest(root, config):
    records = []
    for name in config.get('inputs', []):
        if any(re.search(r'(?i)(^\.env|secret|credential|password|token|private.*key)', part) for part in Path(name).parts):
            raise ValueError('Secret paths cannot be freshness inputs')
        path = contained(root, name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Freshness input must be a bounded regular project file')
        records.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest({'configuration': config, 'inputs': records})


def identity(pid):
    try:
        base = Path('/proc') / str(pid)
        raw = (base / 'stat').read_text(encoding='utf-8', errors='replace')
        fields = raw[raw.rfind(')') + 2:].split()
        if fields[0] == 'Z':
            return None
        return {'pid': int(pid), 'uid': base.stat().st_uid, 'start': fields[19], 'group': int(fields[2]), 'session': int(fields[3])}
    except (OSError, ValueError, IndexError):
        return None


def owned(record):
    owner = record.get('owner') if isinstance(record, dict) else None
    if not isinstance(owner, dict) or not isinstance(owner.get('pid'), int) or owner['pid'] <= 1:
        return False
    marker = record.get('launch_id')
    if not isinstance(marker, str) or not re.fullmatch(r'[a-f0-9]{32}', marker):
        return False
    try:
        inherited = (Path('/proc') / str(owner['pid']) / 'environ').read_bytes().split(b'\0')
    except OSError:
        return False
    return owner == identity(owner['pid']) and owner.get('uid') == os.getuid() and owner.get('group') == owner['pid'] and owner.get('session') == owner['pid'] and ('SKILLSYNC_ENV_LAUNCH_ID=' + marker).encode() in inherited


def stop(record):
    if not owned(record):
        return False
    owner = record['owner']
    members = []
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            item = identity(int(path.name))
            if item and item['uid'] == owner['uid'] and item['session'] == owner['session'] and item['group'] == owner['group']:
                members.append(item)
    os.killpg(owner['group'], signal.SIGTERM)
    deadline = time.monotonic() + 3
    while any(identity(item['pid']) == item for item in members) and time.monotonic() < deadline:
        time.sleep(.05)
    for item in members:
        if identity(item['pid']) == item:
            try:
                os.kill(item['pid'], signal.SIGKILL)
            except ProcessLookupError:
                pass
    return True


def health(root, config):
    try:
        spec = config['health']
        if spec.get('argv'):
            result = subprocess.Popen(spec['argv'], cwd=config['cwd'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                return result.wait(timeout=2) == 0
            except BaseException:
                _reap_after_termination(result)
                raise
        with urlopen(spec['url'], timeout=2) as response:
            return 200 <= response.status < 400
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def status(root, config, record):
    if not record:
        return {'status': 'NOT_RUN', 'reason': 'Environment has no managed process', 'warm': False}
    live = owned(record)
    fresh = isinstance(record.get('created_at'), (float, int)) and 0 <= time.time() - record['created_at'] <= config['ttl'] and record.get('fingerprint') == source_digest(root, config)
    ready = live and health(root, config)
    return {'status': 'PASS' if fresh and ready else 'BLOCKED', 'alive': live, 'fresh': fresh, 'healthy': ready, 'warm': fresh and ready}


def generate(root):
    # Launchers delegate configuration and ownership decisions to the same CLI.
    argv = [Path(item).as_posix() if os.path.isabs(item) else item for item in self_argv()]
    shell = '#!/bin/sh\nexec ' + shlex.join(argv) + ' env ' + shlex.quote(Path(root).as_posix()) + ' --action up --run "$@"\n'
    # PowerShell treats the typographic single quotes as quote characters too; doubling escapes each of them
    quote = lambda value: "'" + re.sub("['\u2018\u2019\u201a\u201b]", lambda m: m.group(0) * 2, value) + "'"
    ps = '& ' + ' '.join(quote(item) for item in argv) + ' env ' + quote(str(root)) + ' --action up --run @args\nexit $LASTEXITCODE\n'
    paths = []
    for name, text in [('start.sh', shell), ('start.ps1', ps)]:
        path = state_path(root, 'environments/' + name, create=True)
        # O_NOFOLLOW protects the file even when a launcher already exists.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o700)
        with os.fdopen(fd, 'w') as handle:
            handle.write(text)
        paths.append(str(path))
    return {'status': 'PASS', 'launchers': paths, 'windows_backend': 'BLOCKED', 'reason': 'Windows lifecycle needs a verified process ownership backend'}


def environment(root, manifest, library, action='plan', run=False, generate_launchers=False):
    config = configuration(root, manifest, library)
    if not config:
        return {'status': 'NOT_RUN', 'reason': 'No environment recipe configured'}
    if generate_launchers:
        return generate(root) if run else {'status': 'NOT_RUN', 'reason': 'Launcher generation requires --run'}
    if sys.platform != 'linux':
        return {'status': 'BLOCKED', 'reason': 'No verified process ownership backend on this host'}
    path = state_path(root, 'environments/default.json', create=run)
    if action == 'plan':
        return {'status': 'NOT_RUN', 'configuration': redact(config), 'fingerprint': source_digest(root, config)}
    if action in ('up', 'down') and not run:
        return {'status': 'NOT_RUN', 'reason': 'Environment mutations require --run'}
    with _locks(['environment:' + str(root)] + config['resources']):
        record = read_json(path)
        if action == 'status':
            return status(root, config, record)
        if action == 'down':
            if record and not owned(record):
                return {'status': 'BLOCKED', 'reason': 'Ownership cannot be verified; no process signalled'}
            stopped = stop(record) if record else False
            if stopped:
                path.unlink()
            return {'status': 'PASS' if stopped else 'NOT_RUN', 'stopped': stopped}
        current = status(root, config, record)
        if current.get('warm'):
            return dict(current, start='warm')
        if record:
            if owned(record):
                stop(record)
            elif identity(record.get('owner', {}).get('pid', 0)):
                return {'status': 'BLOCKED', 'reason': 'Existing PID has different ownership; no process signalled'}
        if config['prepare_checks']:
            held = {'environment:' + str(root), *config['resources']}
            prepare = [dict(item, resources=[x for x in item.get('resources', ['project:' + str(root)]) if x not in held] + ['env-prepare:' + str(root) + ':' + item['id']]) for item in config['prepare_checks']]
            prepared = run_validation(root, prepare)
            if prepared['status'] != 'PASS':
                return {'status': prepared['status'], 'prepare': prepared}
        process = None
        try:
            launch_id = uuid.uuid4().hex
            process = subprocess.Popen(config['start'], cwd=config['cwd'], env=dict(os.environ, SKILLSYNC_ENV_LAUNCH_ID=launch_id), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            owner = identity(process.pid)
            if not owner:
                process.wait(timeout=3)
                return {'status': 'FAIL', 'reason': 'Environment exited before ownership could be recorded'}
            record = {'owner': owner, 'launch_id': launch_id, 'created_at': time.time(), 'fingerprint': source_digest(root, config)}
            write_json(path, record)
            deadline = time.monotonic() + config['startup_timeout']
            while process.poll() is None and time.monotonic() < deadline:
                if health(root, config):
                    return dict(status(root, config, record), start='cold')
                time.sleep(.1)
            stop(record)
            process.wait(timeout=5)
            path.unlink(missing_ok=True)
            return {'status': 'FAIL', 'reason': 'Environment did not become healthy; owned process cleaned up'}
        except BaseException:
            if process is not None:
                stop(record)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                path.unlink(missing_ok=True)
            raise


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        report = environment(root, manifest, library, args.action, args.run, args.generate)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, yaml.YAMLError, KeyboardInterrupt) as error:
        report = {'status': 'BLOCKED', 'reason': str(error) or 'Interrupted; cleanup attempted'}
    return emit(report, args.json)


def register(subparsers):
    parser = subparsers.add_parser('env', help='Plan and manage a configured environment')
    add_project_arguments(parser)
    parser.add_argument('--action', choices=['plan', 'status', 'up', 'down'], default='plan')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--generate', action='store_true')
    parser.set_defaults(func=_cmd)
