"""`skillsync resources [path] [--json] [--max N]`: how many more sub-agents the machine can take now (read only, exit code 0).

Sources: the open runs of the log `agents.jsonl` (`agent_runs`) in every run directory of the project, the load of the machine
(`os.getloadavg`) and the available memory (`/proc/meminfo`). The limits are a prudence heuristic of one session, not the cost of a
particular sub-agent, and they are independent of the protocol of the station (`heavy-run.lock`, 10 GiB for heavy runs) and of
`dispatch.max_parallel`.

Known limits of the data: `running` sees only the runs written with `agent-run` in THIS project (it understates the load); `cpu_count`
and `MemAvailable` do not see the cgroup of a container; macOS and Windows have no `MemAvailable` here (`null`: it limits nothing and
`reason` says so); Windows has no load average either.

The function `collect` is pure over injected sources (`load`, `mem`, `cpus`, `now`), so the tests need no real machine.
"""
import json
import math
import os
import platform
from datetime import datetime, timedelta
from pathlib import Path

from . import agent_runs
from .common import read_project

SCHEMA = 1
DEFAULT_MAX = 5
MAX_ALLOWED = 5            # `--max` is 1..5: the limit of the sub-agents of one session (feature skill)
STALE_MINUTES = 90         # an open run older than this is abandoned (a session died without `agent-run end`)
LOAD_PER_CPU = 0.75        # byLoad = floor(0.75 * cpus - load1): one core per sub-agent, a quarter kept for the rest
MEMORY_RESERVE_MIB = 3072  # kept free for the machine and the session itself
MEMORY_PER_AGENT_MIB = 1024  # byMemory = floor((MemAvailable - reserve) / per agent)


def read_load1():
    """Load average of the last minute, or None (no `os.getloadavg`, e.g. Windows)."""
    try:
        return float(os.getloadavg()[0])
    except (AttributeError, OSError):
        return None


def read_mem_available_mib():
    """`MemAvailable` of `/proc/meminfo` in MiB, or None (macOS, Windows, no access)."""
    try:
        with open('/proc/meminfo', encoding='ascii', errors='replace') as handle:
            for line in handle:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) // 1024   # kB -> MiB
    except (OSError, ValueError, IndexError):
        pass
    return None


def _now():
    return datetime.now().astimezone()


def open_agent_runs(root, now, max_age_minutes=STALE_MINUTES):
    """Open runs of every `agents.jsonl` of the project: `[{agent, role, step, startedAt}]` in the order of the directories.
    A run without a readable start time (ISO with a zone) or older than `max_age_minutes` (None: no limit) is left out."""
    from .dashboard import parse_iso   # public parser of the status page (an ISO time with a zone)
    base = Path(root) / '.skillsync' / 'runs'
    found = []
    if Path(root, '.skillsync').is_symlink() or base.is_symlink() or not base.is_dir():
        return found
    for directory in sorted(base.iterdir()):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            opened = agent_runs.open_runs(agent_runs.read_events(directory))
        except (OSError, ValueError):
            continue
        for agent, item in opened.items():
            started = parse_iso(item.get('t')) if isinstance(item.get('t'), str) else None
            if started is None:
                continue
            if max_age_minutes is not None and now - started > timedelta(minutes=max_age_minutes):
                continue
            found.append({'agent': agent, 'role': item.get('role'), 'step': item.get('step'), 'startedAt': item.get('t'),
                          '_kind': item.get('kind'), '_run': directory.name, '_started': started})
    return found


def public_runs(items):
    return [{key: value for key, value in item.items() if not key.startswith('_')} for item in items]


def collect(root, max_agents=DEFAULT_MAX, load=read_load1, mem=read_mem_available_mib, cpus=os.cpu_count, now=_now):
    """The `resources` report (`schema: 1`). `load()`, `mem()`, `cpus()`, `now()` are injected sources (a `None` from `load` or
    `mem` means "no data": that limit is `null` and limits nothing)."""
    if not isinstance(max_agents, int) or isinstance(max_agents, bool) or not 1 <= max_agents <= MAX_ALLOWED:
        raise ValueError(f'--max: liczba 1-{MAX_ALLOWED}')
    count = max(1, int(cpus() or 1))
    load1 = load()
    available = mem()
    running = public_runs(open_agent_runs(root, now()))
    by_max = max_agents - len(running)
    by_load = None if load1 is None else math.floor(LOAD_PER_CPU * count - load1)
    by_memory = None if available is None else math.floor((available - MEMORY_RESERVE_MIB) / MEMORY_PER_AGENT_MIB)
    limits = {'byMax': by_max, 'byLoad': by_load, 'byMemory': by_memory}
    known = {name: value for name, value in limits.items() if value is not None}
    allowed = max(0, min(known.values()))
    notes = []
    if not running and allowed < 1:
        allowed = 1   # somebody else's load must not stop the work altogether
        notes.append('no sub-agent of ours is running, so one is always allowed')
    binding = [name for name, value in known.items() if value == min(known.values())]
    reason = f"limited by {', '.join(binding)} ({min(known.values())})"
    if by_load is None:
        notes.append('byLoad unknown (no load average on this system)')
    if by_memory is None:
        notes.append('byMemory unknown (no MemAvailable on this system)')
    if notes:
        reason += '; ' + '; '.join(notes)
    return {'schema': SCHEMA, 'host': {'os': platform.system().lower() or 'unknown', 'cpus': count, 'load1': load1, 'memAvailableMiB': available},
            'running': running, 'max': max_agents, 'allowed': allowed, 'limits': limits, 'reason': reason}


def render(report):
    host = report['host']
    load = 'n/a' if host['load1'] is None else f"{host['load1']:.2f}"
    memory = 'n/a' if host['memAvailableMiB'] is None else f"{host['memAvailableMiB']} MiB"
    lines = [f"allowed: {report['allowed']} (max {report['max']}, running {len(report['running'])})",
             f"host: {host['os']}, {host['cpus']} cpus, load1 {load}, memory available {memory}",
             'limits: ' + ', '.join(f"{name}={'n/a' if value is None else value}" for name, value in report['limits'].items()),
             f"reason: {report['reason']}"]
    for item in report['running']:
        lines.append(f"running: {item['agent']} {item['role']}" + (f" step {item['step']}" if item['step'] else '') + f" since {item['startedAt']}")
    return '\n'.join(lines)


def project_root(path):
    """The project root of `path`; a path without a manifest is used as it is (the report needs only the run logs)."""
    try:
        return read_project(path)[0]
    except ValueError:
        return Path(path).resolve()


def cmd_resources(args):
    report = collect(project_root(args.path), args.max)
    print(json.dumps(report, ensure_ascii=False) if args.json else render(report))
    return 0


def register(subparsers):
    parser = subparsers.add_parser('resources', help='How many more sub-agents fit now (limit 5, load and memory of the machine); read only')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--max', type=int, default=DEFAULT_MAX, choices=range(1, MAX_ALLOWED + 1), metavar='N',
                        help=f'limit of sub-agents of one session, 1-{MAX_ALLOWED} (default {DEFAULT_MAX})')
    parser.set_defaults(func=cmd_resources)
