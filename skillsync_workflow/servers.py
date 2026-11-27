"""`skillsync servers list|stop`: the live status page and prototype servers of this user, and their gentle stop (CORE-1200-SERVERS).

One module for the registry and the stop; `dashboard stop` and `dashboard serve --detach` (UNIFIED, 2.0.0) call the same functions:
`list_servers(kind, roots, deps)`, `stop_servers(selector, timeout, force, dry_run, deps)` with a structured result and `sweep_dead(kind, dry_run, deps)`.

The registry is READ ONLY: the state files of the status page servers (`dashboard_token.state_dir()`, schema 1 and 2) and of the prototype servers
(`<project>/.skillsync/.../server-<port>.json`, `prototype_server.link_read_states`; they live in the project, so a prototype server is found only
for the projects that are named: `--root`, the current project and the project roots of the status page servers). A token never leaves this module
(it is not copied into a record). A signal is sent only after the identity of the process was checked (command line and start time against the
record); a stopped server is never killed by name. `--force` is a separate, explicit step after the gentle stop did not work in time.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import dashboard_token, pathfmt

KINDS = ('dashboard', 'prototype')
DEFAULT_TIMEOUT = 15.0
START_TOLERANCE_S = 10.0     # a process cannot have started after its own state file was written (plus the granularity of the clocks)
OUTCOMES = ('stopped', 'dead-swept', 'still-running', 'refused')
POLL_S = 0.2


def _iso_epoch(text):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(text).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def _windows():
    return os.name == 'nt'


# ---------------------------------------------------------------- the identity of a process

def process_identity(pid):
    """`{'cmdline': str, 'started': epoch seconds or None}` of a running process, None when there is no such process or it cannot be read."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        if sys.platform.startswith('linux'):
            with open(f'/proc/{pid}/cmdline', 'rb') as handle:
                command = handle.read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
            started = None
            try:
                with open(f'/proc/{pid}/stat', encoding='utf-8', errors='replace') as handle:
                    fields = handle.read().rsplit(')', 1)[1].split()
                with open('/proc/stat', encoding='utf-8') as handle:
                    boot = next(int(line.split()[1]) for line in handle if line.startswith('btime'))
                started = boot + int(fields[19]) / os.sysconf('SC_CLK_TCK')
            except (OSError, ValueError, IndexError, StopIteration):
                started = None
            return {'cmdline': command, 'started': started}
        if _windows():
            script = (f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; if ($p) {{ $p.CommandLine; "
                      "([DateTimeOffset]$p.CreationDate).ToUnixTimeSeconds() }")
            done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, timeout=20, check=False)
            lines = done.stdout.decode('utf-8', 'replace').splitlines()
            if done.returncode != 0 or not lines:
                return None
            started = float(lines[-1]) if len(lines) > 1 and re.fullmatch(r'\d+', lines[-1].strip()) else None
            return {'cmdline': lines[0].strip(), 'started': started}
        done = subprocess.run(['ps', '-o', 'lstart=', '-o', 'command=', '-p', str(int(pid))], capture_output=True, timeout=10, check=False,
                              env={**os.environ, 'LC_ALL': 'C'})
        line = done.stdout.decode('utf-8', 'replace').strip('\n')
        if done.returncode != 0 or len(line) < 25:
            return None
        started = time.mktime(time.strptime(line[:24].strip(), '%a %b %d %H:%M:%S %Y'))
        return {'cmdline': line[24:].strip(), 'started': started}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _tokens(command):
    import shlex
    try:
        return [token.strip('"\'') for token in shlex.split(command, posix=not _windows())]
    except ValueError:
        return []


def identity_matches(kind, identity, recorded):
    """Is this process the server of the record? By tokens, not substrings: some token names skillsync (`skillsync`, `skillsync.exe`,
    `.../skillsync`, `skillsync_workflow`) and the tokens `<kind>` and `serve` follow one another; AND both start times are known and the process
    started before the record was written. A missing time is a refusal (nothing is signalled on a guess)."""
    if not identity:
        return False
    tokens = [token.lower() for token in _tokens(identity.get('cmdline') or '')]
    if not any(re.search(r'(^|[\\/])skillsync(_workflow)?(\.exe|\.cmd)?$', token) for token in tokens):
        return False
    if not any(tokens[i] == kind and tokens[i + 1] == 'serve' for i in range(len(tokens) - 1)):
        return False
    started = identity.get('started')
    if not isinstance(started, (int, float)) or not isinstance(recorded, (int, float)) or isinstance(started, bool) or isinstance(recorded, bool):
        return False
    return started <= recorded + START_TOLERANCE_S


# ---------------------------------------------------------------- the registry (read only)

def _record(kind, data, path, alive, project):
    return {'kind': kind, 'pid': data.get('pid'), 'port': data.get('port'), 'host': data.get('host'), 'role': data.get('role'),
            'root': data.get('root'), 'project': project, 'startedAt': data.get('startedAt'), 'tabs': data.get('tabs'), 'alive': alive,
            'approvals': bool(data.get('approvals')), '_path': str(path), '_instance': data.get('instance'), '_recorded': data.get('_recorded')}


def _windows_alive(kind, responded, pid, recorded, identity=None):
    """Windows has no cheap pid check, so a server is alive when it answers. One that does not answer (hung, busy, starting) but whose process is the
    server of the record is still ALIVE: it is never taken for dead (its state file, with the consent token, must not be removed under it)."""
    if responded:
        return True
    return identity_matches(kind, (identity or process_identity)(pid), recorded)


def _dashboard_records(healthy=None):
    found = []
    directory = dashboard_token.state_dir()
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith('.json') and not name.startswith('.'))
    except OSError:
        return found
    for name in names:
        path = directory / name
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if (not isinstance(data, dict) or data.get('schema') not in (dashboard_token.SCHEMA, dashboard_token.SCHEMA_TABS)
                or not isinstance(data.get('port'), int) or isinstance(data.get('port'), bool)):
            continue
        tabs = [tab.get('path') for tab in (data.get('tabs') or []) if isinstance(tab, dict) and isinstance(tab.get('path'), str)] \
            if data.get('schema') == dashboard_token.SCHEMA_TABS else []
        if _windows():
            expected = data.get('serverId') if isinstance(data.get('serverId'), str) else None
            alive = _windows_alive('dashboard', (healthy or dashboard_token._healthy)(data.get('host') or '127.0.0.1', data['port'], expected),
                                   data.get('pid'), _iso_epoch(data.get('startedAt')))
        else:
            alive = bool(dashboard_token._pid_alive(data.get('pid')))
        recorded = _iso_epoch(data.get('startedAt'))
        found.append(_record('dashboard', {**data, 'tabs': len(tabs), '_recorded': recorded,
                                           'role': data.get('role') if data.get('role') in ('read', 'approve') else 'both',
                                           'approvals': isinstance(data.get('token'), str) and bool(data.get('token'))}, path, alive, data.get('root')))
    return found


def _prototype_records(roots, probe=None):
    from . import prototype_server
    found = []
    seen = set()
    for root in roots:
        try:
            resolved = str(Path(root).resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        run_dir = prototype_server.link_run_dir_of(resolved)
        for data in prototype_server.link_read_states(run_dir):
            path = prototype_server.link_state_path(run_dir, data['port'])
            try:
                recorded = os.stat(path).st_mtime
            except OSError:
                recorded = None
            if _windows():
                alive = _windows_alive('prototype', (probe or _prototype_alive)(data), data.get('pid'), recorded)
            else:
                alive = bool(prototype_server.link_pid_alive(data.get('pid')))
            found.append(_record('prototype', {**data, 'role': None, 'startedAt': None, '_recorded': recorded}, path, alive, pathfmt.posix(resolved)))
    return found


def _prototype_alive(data):
    """Windows: the answer of the server itself (`/_api/v1/session`, the `instance` of the record), as a pid check says nothing there."""
    import urllib.request
    host = data.get('host') if data.get('host') in ('127.0.0.1', 'localhost') else '127.0.0.1'
    try:
        with urllib.request.urlopen(f"http://{host}:{int(data['port'])}/_api/v1/session", timeout=2.0) as response:
            payload = json.load(response)
        return (payload.get('server') or {}).get('instance') == data.get('instance')
    except (OSError, ValueError, AttributeError):
        return False


def project_roots(extra=(), cwd=None, dashboards=None):
    """Where prototype servers are looked for: the named roots, the project of the current directory and the project roots of the status page servers."""
    roots = [str(item) for item in extra]
    base = Path(cwd or os.getcwd()).resolve()
    for candidate in (base, *base.parents):
        if (candidate / '.git').exists():
            roots.append(str(candidate))
            break
    for record in (dashboards if dashboards is not None else _dashboard_records()):
        if isinstance(record.get('root'), str) and record['root']:
            roots.append(record['root'])
    return roots


class Deps:
    """Everything that touches the system, replaceable in tests."""

    def records(self, roots=()):
        dashboards = _dashboard_records()
        return dashboards + _prototype_records(project_roots(roots, dashboards=dashboards))

    def identity(self, pid):
        return process_identity(pid)

    def terminate(self, pid):
        """Send the gentle stop: True when it was sent, 'gone' when the process no longer exists, False when it could not be sent."""
        try:
            if _windows():
                return _send_ctrl_break(pid)
            os.kill(pid, signal.SIGTERM)
            return True
        except ProcessLookupError:
            return 'gone'
        except OSError:
            return False

    def kill(self, pid):
        try:
            if _windows():
                done = subprocess.run(['taskkill', '/F', '/PID', str(int(pid))], capture_output=True, timeout=20, check=False)
                return done.returncode == 0
            os.kill(pid, signal.SIGKILL)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def gone(self, record):
        """The state file is gone or its process is no more."""
        if _windows():   # no cheap pid check: the file is gone AND no process of this pid is left (it may still hold the files of the tool)
            return not os.path.exists(record['_path']) and process_identity(record.get('pid')) is None
        if not os.path.exists(record['_path']):
            return True
        return not dashboard_token._pid_alive(record.get('pid'))

    def remove(self, record):
        """Remove the state file that was read, and only while it is still that one (the same pid and instance): a server that started on the
        same project and port in the meantime keeps its file."""
        try:
            with open(record['_path'], 'rb') as handle:
                current = json.loads(handle.read(65536).decode('utf-8'))
            if not isinstance(current, dict) or current.get('pid') != record.get('pid') or current.get('instance') != record.get('_instance'):
                return
            os.unlink(record['_path'])
        except (OSError, ValueError):
            pass

    def sleep(self, seconds):
        time.sleep(seconds)

    def monotonic(self):
        return time.monotonic()


def _send_ctrl_break(pid):
    """Windows: CTRL_BREAK to the console of `pid` from a short helper process (attaching to a console detaches the caller from its own)."""
    # The helper shares the console it breaks, so it gets its own event. It survives only with a console control handler registered AFTER the attach
    # (`signal.signal` in Python does not help: the helper ends with 0xC000013A and a stop that worked is reported as not sent).
    code = ("import ctypes, sys, time\n"
            "k = ctypes.windll.kernel32\n"
            "k.FreeConsole()\n"
            "ok = bool(k.AttachConsole(int(sys.argv[1])))\n"
            "if ok:\n"
            "    handler = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)(lambda event: 1)   # 1 = handled: the default handler would end the process\n"
            "    ok = bool(k.SetConsoleCtrlHandler(handler, 1))\n"
            "if ok:\n"
            "    ok = bool(k.GenerateConsoleCtrlEvent(1, 0))   # CTRL_BREAK_EVENT\n"
            "    time.sleep(0.5)   # the event arrives in a thread of its own: stay until it was handled\n"
            "sys.exit(0 if ok else 1)\n")
    previous = None
    try:
        previous = signal.signal(signal.SIGBREAK, signal.SIG_IGN)   # the event goes to EVERY process of that console, this one included
    except (AttributeError, ValueError, OSError):
        previous = None
    try:
        done = subprocess.run([sys.executable, '-c', code, str(int(pid))], capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if previous is not None:
            try:
                signal.signal(signal.SIGBREAK, previous)
            except (AttributeError, ValueError, OSError):
                pass
    return done.returncode == 0


def public(record):
    """The record without the private fields (path, instance, recorded time): the shape that is printed. There is no token here by construction."""
    return {key: value for key, value in record.items() if not key.startswith('_')}


def list_servers(kind=None, roots=(), deps=None):
    deps = deps or Deps()
    return [public(record) for record in deps.records(roots) if kind in (None, record['kind'])]


# ---------------------------------------------------------------- stop

def _selected(record, selector):
    if selector.get('kind') not in (None, record['kind']):
        return False
    if selector.get('all'):
        return True
    if selector.get('port') is not None and record.get('port') != selector['port']:
        return False
    roots = [pathfmt.posix(str(Path(item).resolve())).rstrip('/') for item in selector.get('root') or []]
    recorded = [pathfmt.posix(str(record.get(key) or '')).rstrip('/') for key in ('project', 'root')]   # a record may write Windows separators
    if roots and not any(item in recorded for item in roots):
        return False
    return selector.get('port') is not None or bool(roots)


def stop_servers(selector, timeout=DEFAULT_TIMEOUT, force=False, dry_run=False, deps=None):
    """`selector` = `{root: [paths], port: int|None, all: bool, kind: 'dashboard'|'prototype'|None}`. Returns `{'status', 'items': [{pid, port, kind, outcome,
    reason, warnings}], 'dryRun'}`; status `PASS` (stopped or nothing to stop), `FAIL` (a server still runs) or `BLOCKED` (nothing selected, a refusal)."""
    deps = deps or Deps()
    if not (selector.get('all') or selector.get('port') is not None or selector.get('root')):
        return {'status': 'BLOCKED', 'items': [], 'dryRun': bool(dry_run),
                'reason': 'nothing selected: name --root, --port or --all (the running servers are listed by `skillsync servers list`)'}
    chosen = [record for record in deps.records(selector.get('root') or ()) if _selected(record, selector)]
    items = []
    signalled = {}   # (kind, pid) -> the item that handled the process: a server with two sockets has two state files and gets ONE signal
    for record in chosen:
        item = {'pid': record.get('pid'), 'port': record.get('port'), 'kind': record['kind'], 'outcome': None, 'reason': None, 'warnings': []}
        items.append(item)
        if record['kind'] == 'dashboard' and record.get('approvals') and record.get('role') in ('approve', 'both'):
            item['warnings'].append('the server accepts consents (GO, CP, READY): a consent waiting on its page is lost')
        if not record['alive']:
            if dry_run:
                item.update(outcome='dead-swept', reason='dry run: the process is not running, the leftover state file would be removed')
            else:
                deps.remove(record)
                item.update(outcome='dead-swept', reason='the process is not running; the leftover state file was removed')
            continue
        first = signalled.get((record['kind'], record.get('pid')))
        if first is not None:
            if first['outcome'] == 'stopped' and not dry_run:
                deps.remove(record)
            item.update(outcome=first['outcome'], reason=first['reason'])
            continue
        identity = deps.identity(record.get('pid'))
        if not identity_matches(record['kind'], identity, record.get('_recorded')):
            item.update(outcome='refused', reason='the process of this pid is not the server of the record (command line or start time differ): nothing was sent')
            continue
        if dry_run:
            item.update(outcome='stopped', reason='dry run: the server would be stopped')
            continue
        signalled[(record['kind'], record.get('pid'))] = item
        sent = deps.terminate(record['pid'])
        if sent == 'gone':
            deps.remove(record)
            item.update(outcome='dead-swept', reason='the process ended before the signal; the leftover state file was removed')
            continue
        if not sent:
            item.update(outcome='refused', reason='the gentle stop could not be sent' + ('' if force else '; --force ends the process after the identity check'))
            if not force:
                continue
        deadline = deps.monotonic() + max(0.0, float(timeout))
        while not deps.gone(record) and deps.monotonic() < deadline:
            deps.sleep(POLL_S)
        if deps.gone(record):
            item.update(outcome='stopped', reason=None)
            continue
        if not force:
            item.update(outcome='still-running', reason=f'the server did not stop within {timeout:g} s; nothing was killed (--force ends it after the identity check)')
            continue
        if not identity_matches(record['kind'], deps.identity(record['pid']), record.get('_recorded')):
            item.update(outcome='refused', reason='--force: the process changed its identity meanwhile; nothing was killed')
            continue
        if deps.kill(record['pid']):
            end = deps.monotonic() + 5.0
            while not deps.gone(record) and deps.monotonic() < end:
                deps.sleep(POLL_S)
            deps.remove(record)
            item.update(outcome='stopped', reason='ended with --force after the gentle stop did not work in time')
        else:
            item.update(outcome='still-running', reason='--force could not end the process')
    status = 'PASS'
    if any(item['outcome'] == 'still-running' for item in items):
        status = 'FAIL'
    elif any(item['outcome'] == 'refused' for item in items):
        status = 'BLOCKED'
    return {'status': status, 'items': items, 'dryRun': bool(dry_run)}


def sweep_dead(kind=None, dry_run=False, roots=(), deps=None):
    """Remove only the state files of processes that are not running (no signal). Returns the items like `stop_servers`."""
    deps = deps or Deps()
    items = []
    for record in deps.records(roots):
        if record['alive'] or kind not in (None, record['kind']):
            continue
        if not dry_run:
            deps.remove(record)
        items.append({'pid': record.get('pid'), 'port': record.get('port'), 'kind': record['kind'], 'outcome': 'dead-swept',
                      'reason': 'dry run: would be removed' if dry_run else 'the process is not running; the leftover state file was removed', 'warnings': []})
    return {'status': 'PASS', 'items': items, 'dryRun': bool(dry_run)}


# ---------------------------------------------------------------- the command

EXIT = {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}


def _print_list(records, as_json):
    if as_json:
        print(json.dumps({'servers': records}, indent=2, sort_keys=True))
        return
    if not records:
        print('no servers running')
        return
    for record in records:
        state = 'running' if record['alive'] else 'not running (leftover state file)'
        print(f"{record['kind']}  port {record['port']}  pid {record['pid']}  {state}  {record.get('project') or record.get('root') or '-'}")


def cmd_servers_list(args):
    records = list_servers(args.kind, args.root or ())
    _print_list(records, args.json)
    return 0


def cmd_servers_stop(args):
    selector = {'root': args.root or [], 'port': args.port, 'all': args.all, 'kind': args.kind}
    result = stop_servers(selector, timeout=args.timeout, force=args.force, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result['status'] + (' (dry run)' if result['dryRun'] else ''))
        if result.get('reason'):
            print(result['reason'])
        for item in result['items']:
            print(f"{item['kind']}  port {item['port']}  pid {item['pid']}  {item['outcome']}" + (f"  {item['reason']}" if item['reason'] else ''))
            for warning in item['warnings']:
                print(f'  warning: {warning}')
        if not result['items'] and result['status'] == 'PASS':
            print('nothing to stop')
    if result['status'] == 'BLOCKED' and not result['items']:
        return 2
    if result['dryRun'] and result['status'] == 'PASS':
        return 3
    return EXIT[result['status']]


def _timeout(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError('must be a number of seconds') from None
    if not 0 <= value <= 3600:
        raise argparse.ArgumentTypeError('must be between 0 and 3600 seconds')
    return value


def register(subparsers):
    parser = subparsers.add_parser('servers', help='List and gently stop the running status page and prototype servers of this user',
                                   description='The running servers of this user (`dashboard serve` of the status page, `prototype serve`) from their state files, and a '
                                               'gentle stop before an update of skillsync (a running server blocks `uv tool install --force` on Windows). Read only except '
                                               '`stop`. A server of the prototypes is found for the projects named by --root, the current project and the projects of the '
                                               'status page servers. A token is never printed.')
    sub = parser.add_subparsers(dest='servers_command', required=True, metavar='{list,stop}')
    lister = sub.add_parser('list', help='print the running servers (kind, port, pid, project; never a token)',
                            description='Print the running servers; a state file of a process that is not running is listed as such.')
    lister.add_argument('--root', action='append', default=[], metavar='PATH', help='also look for prototype servers of this project (repeatable)')
    lister.add_argument('--kind', choices=KINDS, default=None, help='only this kind of server (default: both)')
    lister.add_argument('--json', action='store_true', help='print the list as JSON')
    lister.set_defaults(func=cmd_servers_list)
    stopper = sub.add_parser('stop', help='stop the chosen servers gently (SIGTERM; Windows CTRL_BREAK); --force ends a stuck one after an identity check',
                             description='Stop the chosen servers. Choose with --root PATH (the project), --port N or --all; without a choice nothing is stopped (exit 2). '
                                         'The gentle stop is sent only after the process was checked against its record (command line and start time); the command waits '
                                         f'for the state file to disappear (default {DEFAULT_TIMEOUT} s). A server that does not stop is reported as still-running '
                                         'and is not killed unless --force is given. A state file of a process that is not running is removed. The result warns '
                                         'when the server accepts consents (GO, CP, READY).')
    stopper.add_argument('--root', action='append', default=[], metavar='PATH', help='servers of this project (repeatable)')
    stopper.add_argument('--port', type=int, default=None, help='the server on this port')
    stopper.add_argument('--all', action='store_true', help='every server of this user that is found')
    stopper.add_argument('--kind', choices=KINDS, default=None, help='only this kind of server (default: both)')
    stopper.add_argument('--timeout', type=_timeout, default=DEFAULT_TIMEOUT, metavar='SECONDS', help=f'seconds to wait for the stop (default {DEFAULT_TIMEOUT})')
    stopper.add_argument('--force', action='store_true', help='end a server that did not stop in time, only after the identity check')
    stopper.add_argument('--dry-run', action='store_true', help='print what would be stopped and change nothing (exit code 3)')
    stopper.add_argument('--json', action='store_true', help='print the result as JSON')
    stopper.set_defaults(func=cmd_servers_stop)
