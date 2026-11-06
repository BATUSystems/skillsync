"""Read-only view of `.skillsync/dispatch/*.yaml` for IDE integrations (`skillsync dispatch`, schema 1).

The state file is the dispatcher's hypothesis (`authoritative: false`), written by hand over time, so real files deviate
from `skills/dispatch/references/state.md`. Every field is read through a whitelist with a shape validator; anything else
(free text, session names, paths, notes) is never copied. A malformed field becomes `null` plus a finding that names the
field but never the value; a malformed file becomes an `unreadable` entry. Nothing here raises for bad data or writes.
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import stat
import time
import yaml

from .common import add_project_arguments, exit_code, read_project, redact

SCHEMA = 1
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 50
MAX_SHOWN = 20
MAX_ITEMS = 200
STALE_SECONDS = 6 * 3600
DISPATCH_STATUSES = ('planning', 'running', 'draining', 'done', 'aborted')
STATUS_ALIASES = {'active': 'running'}
ACTIVE_STATUSES = ('planning', 'running', 'draining')
PACKAGE_STATUSES = ('todo', 'assigned', 'go', 'in_progress', 'review', 'done', 'blocked')
CARD_STATES = ('dispatcher', 'probed', 'free', 'busy', 'working', 'waiting_user', 'unavailable', 'released')
QUEUE_STATES = ('waiting', 'granted', 'pushed', 'verified')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,79}')
_BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,119}')
_HASH = re.compile(r'[0-9a-f]{7,40}')
_SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')  # same alphabet as runs._slug: no ':' (drive-relative paths on Windows)
_TOKEN_SHAPE = re.compile(r'[A-Za-z0-9_]{32,}')  # long unbroken runs look like credentials, not identifiers or branches


def _iso(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


class _Reader:
    """Validators that record a finding (field name only) instead of leaking or raising."""

    def __init__(self, file_name, findings):
        self.file, self.findings = file_name, findings

    def bad(self, field):
        self.findings.append(f'{self.file}: invalid {field}')

    def text(self, value, pattern, field):
        if value is None:
            return None
        if isinstance(value, str) and pattern.fullmatch(value) and (pattern is _HASH or not _TOKEN_SHAPE.search(value)):
            return value
        self.bad(field)
        return None

    def number(self, value, low, high, field):
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            return value
        self.bad(field)
        return None

    def moment(self, value, field):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str) and len(value) <= 40:
            try:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
                return value
            except ValueError:
                pass
        self.bad(field)
        return None

    def mappings(self, value, field):
        """List of mappings; a single mapping counts as one entry (seen in real files); anything else is empty."""
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            rows = [item for item in value[:MAX_ITEMS] if isinstance(item, dict)]
            if len(rows) != len(value[:MAX_ITEMS]):
                self.bad(field)
            return rows
        self.bad(field)
        return []

    def enum(self, value, allowed, aliases=None):
        if not isinstance(value, str):
            return 'unknown'
        value = (aliases or {}).get(value, value)
        return value if value in allowed else 'unknown'


def _claims(root, findings):
    """task -> (run_id, expires_at) from `.skillsync/claims`; unreadable files are skipped silently (state is a hint)."""
    directory = root / '.skillsync' / 'claims'
    out = {}
    if directory.is_symlink() or not directory.is_dir():
        return out
    for index, path in enumerate(sorted(directory.glob('*.json'))[:MAX_FILES * 4]):
        try:
            mode = path.lstat()
            if not stat.S_ISREG(mode.st_mode) or mode.st_size > 64 * 1024:  # symlinks, FIFOs and devices would hang or escape
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and isinstance(data.get('task_id'), str) and isinstance(data.get('expires_at'), (int, float)) \
                    and not isinstance(data['expires_at'], bool):
                out[data['task_id']] = (data.get('run_id'), data['expires_at'])
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            findings.append(f'claims/{index}: unreadable')
    return out


def _run(root, ident, findings, cache):
    """(run summary, cost) for run `<dispatch run>.<package>`; absent run -> exists false and unknown cost."""
    unknown = {'usd': None, 'partial': False, 'state': 'unknown'}
    if ident is None or not _SLUG.fullmatch(ident):
        return None, unknown
    if ident in cache:
        return cache[ident]
    cache[ident] = result = _run_uncached(root, ident, findings, unknown)
    return result


def _run_uncached(root, ident, findings, unknown):
    directory = root / '.skillsync' / 'runs'
    if directory.is_symlink():
        findings.append('runs: directory is a symlink')
        return None, unknown
    path = directory / f'{ident}.json'
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {'id': ident, 'exists': False, 'steps': 0, 'lastStatus': None, 'lastStep': None}, unknown
    except OSError:
        findings.append('runs: unreadable record')
        return {'id': ident, 'exists': True, 'steps': None, 'lastStatus': None, 'lastStep': None}, unknown
    summary = {'id': ident, 'exists': True, 'steps': None, 'lastStatus': None, 'lastStep': None}
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        findings.append('runs: unreadable record')
        return summary, unknown
    try:
        from .runs import _read
        records = _read(path)['checkpoints']
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        findings.append('runs: unreadable record')
        return summary, unknown
    summary.update(steps=len(records), lastStatus=records[-1]['status'] if records else None,
                   lastStep=records[-1]['step'] if records else None)
    return summary, _cost(records)


def _cost(records):
    """Sum of USD `cost` over checkpoints. Missing cost is unknown, never 0; other units are never mixed or converted."""
    unknown = {'usd': None, 'partial': False, 'state': 'unknown'}
    if not records:
        return unknown
    known, total = [], len(records)
    for record in records:
        cost = record.get('cost')
        if cost is None:
            continue
        if record.get('cost_unit', 'USD') != 'USD' or isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost):
            return unknown
        known.append(cost)
    if not known:
        return unknown
    usd = round(sum(known), 6)
    if not math.isfinite(usd):
        return unknown
    return {'usd': usd, 'partial': len(known) < total, 'state': 'known' if len(known) == total else 'partial'}


def _package(reader, root, run_id, item, claims, findings, now, cache):
    ident = reader.text(item.get('id'), _ID, 'package.id')
    claim = None
    raw = item.get('claim')
    if isinstance(raw, dict):
        task = reader.text(raw.get('task_id'), _ID, 'package.claim.task_id')
        claim_run = reader.text(raw.get('run_id'), _ID, 'package.claim.run_id')
        ttl = reader.number(raw.get('ttl'), 1, 86400, 'package.claim.ttl')
        claim = {'taskId': task, 'runId': claim_run, 'ttl': ttl}
    elif raw is not None:
        reader.bad('package.claim')
    active = None
    if claim and claim['taskId'] in claims:
        owner, expires = claims[claim['taskId']]
        active = (expires > now) if owner == claim['runId'] else None
    evidence = item.get('evidence')
    if isinstance(evidence, list):
        count = len(evidence[:1000])
    elif isinstance(evidence, str) and evidence:
        count = 1
    else:
        count = None
        if evidence is not None and evidence != '':
            reader.bad('package.evidence')
    result = item.get('result_commit', item.get('commit'))
    run, cost = _run(root, f'{run_id}.{ident}' if run_id and ident else None, findings, cache)
    return {'id': ident, 'card': reader.text(item.get('card'), _ID, 'package.card'),
            'status': reader.enum(item.get('status'), PACKAGE_STATUSES),
            'branch': reader.text(item.get('branch'), _BRANCH, 'package.branch'),
            'inputCommit': reader.text(item.get('input_commit'), _HASH, 'package.input_commit'),
            'resultCommit': reader.text(result, _HASH if isinstance(result, str) and _HASH.fullmatch(result) else _BRANCH, 'package.result_commit'),
            'evidenceCount': count, 'claim': claim, 'claimActive': active, 'run': run, 'cost': cost}


def _queue(reader, value):
    rows = []
    if value is not None and not isinstance(value, list):
        reader.bad('merge_queue')
    for item in (value[:MAX_ITEMS] if isinstance(value, list) else []):
        if isinstance(item, str):
            package, state = reader.text(item, _ID, 'merge_queue'), None
        elif isinstance(item, dict):
            package = reader.text(item.get('package'), _ID, 'merge_queue.package')
            state = item.get('state') if item.get('state') in QUEUE_STATES else None
        else:
            continue
        if package:
            rows.append({'package': package, 'state': state})
    return rows


def _freshness(mtime, now, status):
    age = max(0, int(now - mtime))
    return {'fileUpdated': _iso(mtime), 'ageSeconds': age, 'stale': status in ACTIVE_STATUSES and age > STALE_SECONDS}


def _dispatch_entry(root, path, index, claims, findings, now, cache):
    # The file name is user-controlled free text: it is shown (and used in findings) only when it is a plain identifier.
    safe = _ID.fullmatch(path.stem) is not None and not _TOKEN_SHAPE.search(path.stem)
    label = path.name if safe else f'entry-{index}'
    try:
        stat_result = path.stat()
        mtime = stat_result.st_mtime
    except OSError:
        findings.append(f'{label}: unreadable (OSError)')
        return {'file': label if safe else None, 'state': 'unreadable', 'runId': None, 'freshness': None}
    base = {'file': label if safe else None, 'state': 'ok'}
    try:
        if stat_result.st_size > MAX_FILE_BYTES:
            raise ValueError('oversized')
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('shape')
        reader = _Reader(label, findings)
        run_id = reader.text(data.get('run_id'), _ID, 'run_id')
        status = reader.enum(data.get('status'), DISPATCH_STATUSES, STATUS_ALIASES)
        master = data.get('master')
        head = data.get('master_head') or (master.get('head') if isinstance(master, dict) else None)
        cards = [{'alias': reader.text(c.get('alias'), _ID, 'card.alias'), 'state': reader.enum(c.get('state'), CARD_STATES),
                  'package': reader.text(c.get('package'), _ID, 'card.package'),
                  'branch': reader.text(c.get('branch'), _BRANCH, 'card.branch')} for c in reader.mappings(data.get('cards'), 'cards')]
        packages = [_package(reader, root, run_id, p, claims, findings, now, cache) for p in reader.mappings(data.get('packages'), 'packages')]
        leases = [{'resource': reader.text(l.get('resource'), _ID, 'lease.resource'), 'holder': reader.text(l.get('holder'), _ID, 'lease.holder'),
                   'card': reader.text(l.get('card'), _ID, 'lease.card'), 'grantedAt': reader.moment(l.get('granted_at'), 'lease.granted_at'),
                   'released': l.get('released_at') is not None} for l in reader.mappings(data.get('leases'), 'leases')]
        slot = data.get('push_slot')
        slot_package = reader.text(slot if isinstance(slot, str) else (slot.get('package') if isinstance(slot, dict) else None), _ID, 'push_slot')
        by_status = {}
        for package in packages:
            by_status[package['status']] = by_status.get(package['status'], 0) + 1
        base.update({'runId': run_id or (path.stem if safe else None), 'status': status,
                     'baseBranch': reader.text(data.get('base_branch'), _BRANCH, 'base_branch'),
                     'baseCommit': reader.text(data.get('base_commit'), _HASH, 'base_commit'),
                     'masterHead': reader.text(head, _HASH, 'master_head'),
                     'parallelLimit': reader.number(data.get('parallel_limit'), 1, 64, 'parallel_limit'),
                     'freshness': _freshness(mtime, now, status),
                     'cards': cards, 'packages': packages, 'leases': leases, 'mergeQueue': _queue(reader, data.get('merge_queue')),
                     'pushSlot': {'package': slot_package} if slot_package else None,
                     'counts': {'packages': len(packages), 'byStatus': by_status}})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, UnicodeDecodeError, RecursionError, MemoryError) as error:
        findings.append(f'{label}: unreadable ({type(error).__name__})')
        return {'file': label if safe else None, 'state': 'unreadable', 'runId': None, 'freshness': _freshness(mtime, now, 'unknown')}
    return base


def dispatch_report(root, manifest, run_id=None):
    root = Path(root).resolve()
    if run_id is not None and not _ID.fullmatch(run_id):
        raise ValueError('Invalid --run-id')
    state_dir = root / '.skillsync'
    directory = state_dir / 'dispatch'
    if state_dir.is_symlink() or directory.is_symlink():
        raise ValueError('.skillsync and .skillsync/dispatch must not be symlinks')
    project = manifest.get('project', {})
    name = project.get('name') if isinstance(project, dict) else None
    report = {'schema': SCHEMA, 'status': 'NOT_RUN', 'project': name or f'{root.parent.name}/{root.name}', 'root': str(root),
              'generatedAt': _iso(time.time()), 'authoritative': False, 'dispatches': [], 'findings': []}
    if not directory.is_dir():
        return report
    files = []
    for path in directory.glob('*.yaml'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            files.append((path.stat().st_mtime, path))
        except OSError:
            continue
    files.sort(key=lambda pair: (-pair[0], pair[1].name))
    now, claims, rows, cache = time.time(), _claims(root, report['findings']), [], {}
    for index, (_, path) in enumerate(files[:MAX_FILES]):
        entry = _dispatch_entry(root, path, index, claims, report['findings'], now, cache)
        if run_id is not None and entry.get('runId') != run_id:
            continue
        rows.append(entry)
        if len(rows) >= MAX_SHOWN:
            break
    report['dispatches'] = rows
    if files:
        report['status'] = 'PASS'
    return report


def _cmd_dispatch(args):
    try:
        root, manifest = read_project(args.path)
        report = dispatch_report(root, manifest, args.run_id)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}
    print(json.dumps(redact(report), ensure_ascii=True, indent=2, allow_nan=False))
    return exit_code(report)


def register(subparsers):
    parser = subparsers.add_parser('dispatch', help='Read-only dispatch state: runs, cards, packages, leases (JSON)')
    add_project_arguments(parser)
    parser.add_argument('--run-id')
    parser.set_defaults(func=_cmd_dispatch)
