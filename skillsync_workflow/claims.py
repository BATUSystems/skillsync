"""Atomic, run-owned leases for complete workflow tasks."""
import math
import re
import time
import yaml

from .common import add_project_arguments, emit, read_project
from .quality import _locks
from .storage import read_json, state_path, write_json


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value) or '..' in value:
        raise ValueError('Claim identifiers must be short safe slugs')
    return value


def _ttl(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 86400:
        raise ValueError('Claim TTL must be finite between 1 and 86400 seconds')
    return value


def _record(value, task_id):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('task_id') != task_id:
        raise ValueError('Invalid claim record')
    _id(value.get('run_id'))
    for key in ('acquired_at', 'heartbeat_at', 'expires_at'):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
            raise ValueError('Invalid claim timestamps')
    if not value['acquired_at'] <= value['heartbeat_at'] <= value['expires_at']:
        raise ValueError('Invalid claim timestamp order')
    return value


def claim(root, task_id, run_id=None, action='status', ttl=900, to_run_id=None, takeover=False, run=False):
    task_id = _id(task_id)
    _ttl(ttl)
    if action not in ('status', 'acquire', 'renew', 'release', 'transfer'):
        raise ValueError('Unknown claim action')
    if action != 'status':
        _id(run_id)
    if action == 'transfer':
        _id(to_run_id)
        if to_run_id == run_id:
            raise ValueError('Transfer requires a different run id')
    resource = 'claim:' + str(root.resolve()) + ':' + task_id
    try:
        with _locks([resource]):
            path = state_path(root, 'claims/' + task_id + '.json', create=run and action != 'status')
            current = _record(read_json(path), task_id)
            now = time.time()
            active = current is not None and current['expires_at'] > now
            report = {'status': 'NOT_RUN', 'task_id': task_id, 'action': action,
                      'lease': current, 'active': active}
            if action == 'status':
                report['status'] = 'PASS'
                return report
            owner = current is not None and current['run_id'] == run_id
            if action == 'acquire':
                if active and not owner:
                    return dict(report, status='BLOCKED', reason='Task has an active foreign lease')
                if current and not active and not takeover:
                    return dict(report, status='BLOCKED', reason='Expired lease requires explicit takeover')
            elif not active or not owner:
                return dict(report, status='BLOCKED', reason='Mutation requires the active lease owner')
            if not run:
                return dict(report, reason='Plan only; use --run to mutate')
            if action == 'release':
                path.unlink()
                return dict(report, status='PASS', lease=None, active=False)
            # Active acquisition re-entry is idempotent; only renew changes its deadline.
            if action == 'acquire' and active and owner:
                return dict(report, status='PASS', reason='Existing run lease retained')
            acquired = current['acquired_at'] if action == 'renew' else now
            record = {'version': 1, 'task_id': task_id,
                      'run_id': to_run_id if action == 'transfer' else run_id,
                      'acquired_at': acquired, 'heartbeat_at': now, 'expires_at': now + ttl}
            write_json(path, record)
            return dict(report, status='PASS', lease=record, active=True)
    except BlockingIOError:
        return {'status': 'BLOCKED', 'reason': 'Claim transaction is busy', 'task_id': task_id}


def _cmd(args):
    try:
        root, _ = read_project(args.path)
        report = claim(root, args.task_id, args.run_id, args.action, args.ttl,
                       args.to_run_id, args.takeover, args.run)
    except (ValueError, OSError, TypeError, KeyError, yaml.YAMLError) as error:
        report = {'status': 'BLOCKED', 'reason': str(error)}
    return emit(report, args.json)


def register(subparsers):
    parser = subparsers.add_parser('claim', help='Plan or mutate run-owned task leases')
    add_project_arguments(parser)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--action', choices=['status', 'acquire', 'renew', 'release', 'transfer'], default='status')
    parser.add_argument('--ttl', type=float, default=900)
    parser.add_argument('--to-run-id')
    parser.add_argument('--takeover', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.set_defaults(func=_cmd)
