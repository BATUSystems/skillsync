"""Trusted tracker adapters and evidence-based local CI diagnosis."""
import json
import math
import os
import shutil
from pathlib import Path
import re
import string
import subprocess
import yaml

from .layers import LayerError
from .common import add_project_arguments, emit, layer_file, load_layer_project, read_yaml, redact, workflow_config
from .quality import plan_validation, run_validation, _reap_after_termination
from .runs import fingerprint
from .storage import read_json


_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
_SECRET = re.compile(r'(?i)token|secret|password|passwd|api[_-]?key|credential|authorization')


def _safe_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value) or '..' in value:
        raise ValueError('Expected safe identifier')
    return value


def _names(value, label):
    if not isinstance(value, list) or len(value) > 100 or not all(isinstance(x, str) and _ID.fullmatch(x) and '..' not in x for x in value) or len(set(value)) != len(value):
        raise ValueError(label + ' must be a bounded list of unique identifiers')
    return value


def _local(root, name):
    root = Path(root).resolve()
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
        raise ValueError('Expected project-relative local path')
    path = root
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError('Delivery paths cannot be symlinks')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Delivery path escapes root')
    return path


def _scrub(value):
    if isinstance(value, dict):
        return {redact(str(key)): '[REDACTED]' if _SECRET.search(str(key)) else _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return redact(value)


def _config(root, manifest, library):
    config = workflow_config(root, manifest, library).get('delivery', {})
    if not isinstance(config, dict):
        raise ValueError('workflow.delivery must be a mapping')
    _names(config.get('required_capabilities', []), 'required_capabilities')
    return config


def _operation(spec):
    if not isinstance(spec, dict) or spec.get('effect') not in ('read', 'write'):
        raise ValueError('Operation requires read/write effect')
    required = _names(spec.get('required_params', []), 'required_params')
    builtin = spec.get('builtin')
    if builtin is not None and builtin not in ('github', 'jira', 'azure'):
        raise ValueError('Unsupported builtin tracker provider')
    if builtin is not None and spec.get('argv') is not None:
        raise ValueError('Builtin and argv are mutually exclusive')
    argv = spec.get('argv', ['builtin'])
    if not isinstance(argv, list) or not 1 <= len(argv) <= 100 or not all(isinstance(x, str) and x and len(x) <= 8192 and '\0' not in x for x in argv):
        raise ValueError('Operation requires bounded argv')
    formatter = string.Formatter()
    for arg in argv:
        for _, field, format_spec, conversion in formatter.parse(arg):
            if field is not None and (field not in required or format_spec or conversion):
                raise ValueError('Only declared simple parameter substitutions are allowed')
    contract = spec.get('result')
    if not isinstance(contract, dict) or contract.get('type') not in ('object', 'list'):
        raise ValueError('Operation requires object/list result contract')
    _names(contract.get('required', []), 'result.required')
    timeout = spec.get('timeout', 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError('Operation timeout must be finite within 300 seconds')
    if spec['effect'] == 'write':
        verify = spec.get('verify')
        if not isinstance(verify, dict):
            raise ValueError('Write operation requires readback verification')
        _safe_id(verify.get('operation'))
        if not _names(verify.get('match'), 'verify.match'):
            raise ValueError('Readback match requires at least one field')
    return spec


def _descriptor(root, library, provider):
    provider = _safe_id(provider)
    project = _local(root, f'.skillsync/providers/{provider}.yaml')
    path = project if project.exists() else layer_file(library, f'workflow/trackers/{provider}.yaml')
    if path is None or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Missing or oversized provider descriptor')
    descriptor = read_yaml(path)
    if descriptor.get('version') != 1:
        raise ValueError('Unsupported provider version')
    capabilities = _names(descriptor.get('capabilities'), 'capabilities')
    operations = descriptor.get('operations')
    if not isinstance(operations, dict) or not operations or len(operations) > 100:
        raise ValueError('Provider requires bounded operations mapping')
    for name, spec in operations.items():
        _safe_id(name)
        _operation(spec)
        if spec.get('builtin') and (name not in ('read', 'update-title') or spec['effect'] != ('read' if name == 'read' else 'write')):
            raise ValueError('Builtin operation effect must match its capability')
        if name not in capabilities:
            raise ValueError('Operation missing declared capability')
    for spec in operations.values():
        if spec['effect'] == 'write':
            verify = operations.get(spec['verify']['operation'])
            if not verify or verify['effect'] != 'read':
                raise ValueError('Write verification must reference read capability')
    return descriptor


def _params(value, spec):
    required = spec.get('required_params', [])
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError('Parameters must exactly match required_params')
    for key, item in value.items():
        if not isinstance(item, (str, int, float, bool)) or isinstance(item, float) and not math.isfinite(item) or len(str(item)) > 8192 or '\0' in str(item):
            raise ValueError('Parameters must be bounded finite scalar values')
        if _SECRET.search(key):
            raise ValueError('Credentials must come from environment, not parameters')
    return value


def _invoke(root, spec, params, operation=None):
    try:
        if spec.get('builtin'):
            from .tracker_http import invoke
            value = invoke(spec['builtin'], operation, params, spec.get('timeout', 30))
        else:
            argv = [arg.format_map(params) for arg in spec['argv']]
            taskkill = shutil.which('taskkill') if os.name == 'nt' else None
            if os.name == 'nt' and not taskkill:
                return {'status': 'BLOCKED', 'reason': 'Enforceable adapter timeout requires taskkill'}
            # Disk-backed bounded reads avoid unbounded stdout/stderr accumulation.
            import tempfile
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=errors, start_new_session=os.name != 'nt')
                try:
                    process.wait(timeout=spec.get('timeout', 60))
                except subprocess.TimeoutExpired:
                    _reap_after_termination(process, taskkill)
                    return {'status': 'BLOCKED', 'reason': 'Adapter timed out; process tree cleaned up'}
                except BaseException:
                    _reap_after_termination(process, taskkill)
                    raise
                if process.returncode:
                    return {'status': 'FAIL', 'exit_code': process.returncode, 'reason': 'Adapter returned nonzero exit code'}
                output.seek(0)
                raw = output.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    return {'status': 'BLOCKED', 'reason': 'Adapter result exceeds limit'}
                value = json.loads(raw)
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'BLOCKED', 'reason': 'Adapter unavailable, request failed or invalid JSON result'}
    contract = spec['result']
    rows = [value] if contract['type'] == 'object' else value
    if not isinstance(rows, list) or len(rows) > 10000 or not all(isinstance(row, dict) and all(field in row for field in contract.get('required', [])) for row in rows):
        return {'status': 'BLOCKED', 'reason': 'Adapter result violates result contract'}
    return {'status': 'PASS', 'exit_code': 0, 'data': value}


def tracker(root, manifest, library, operation, params, run=False, allow_write=False, provider=None):
    config = _config(root, manifest, library)
    descriptor = _descriptor(root, library, provider or config.get('provider'))
    capabilities = descriptor['capabilities']
    if any(name not in capabilities for name in config.get('required_capabilities', [])):
        return {'status': 'BLOCKED', 'reason': 'Provider lacks required capabilities'}
    spec = descriptor['operations'].get(_safe_id(operation))
    if not spec:
        return {'status': 'BLOCKED', 'reason': 'Unsupported operation capability'}
    _params(params, spec)
    report = {'status': 'NOT_RUN', 'operation': operation, 'effect': spec['effect'], 'provider': provider or config.get('provider'), 'required_params': spec.get('required_params', [])}
    verify = None
    if spec['effect'] == 'write':
        verify = descriptor['operations'][spec['verify']['operation']]
        if any(key not in params for key in verify.get('required_params', [])):
            raise ValueError('Readback parameters must be provided by write operation')
        if not allow_write:
            return dict(report, status='BLOCKED', reason='Write requires --allow-write')
    if not run:
        return dict(report, reason='Plan only; use --run to invoke trusted adapter')
    result = _invoke(root, spec, params, operation)
    if result['status'] != 'PASS':
        return dict(report, **result)
    if verify:
        readback = _invoke(root, verify, {key: params[key] for key in verify.get('required_params', [])}, spec['verify']['operation'])
        if readback['status'] != 'PASS':
            return dict(report, status='BLOCKED', mutation_completed=True, reason='Write completed; readback failed')
        written, observed = result['data'], readback['data']
        if not isinstance(written, dict) or not isinstance(observed, dict) or not all(field in written and field in observed and written[field] == observed[field] for field in spec['verify']['match']):
            return dict(report, status='BLOCKED', mutation_completed=True, reason='Write completed; readback does not match')
        if any(field in params and str(observed[field]) != str(params[field]) for field in spec['verify']['match']):
            return dict(report, status='BLOCKED', mutation_completed=True, reason='Readback does not match requested values')
        result['verified'] = True
    return _scrub(dict(report, **result))


_CLASSES = (
    ('environment', r'(?i)connection refused|timed? out|no space left|out of memory|permission denied|network unreachable|missing (?:dependency|prerequisite)|command not found'),
    ('flaky', r'(?i)flaky|intermittent|race condition|retry succeeded|non.deterministic'),
    ('test', r'(?i)invalid test fixture|stale (?:locator|selector)|test collection error|fixture .*not found'),
)


def _diagnosis(log):
    for name, pattern in _CLASSES:
        if re.search(pattern, log):
            return name
    return 'implementation'


def ci(root, manifest, library, report_path, run=False):
    config = _config(root, manifest, library).get('ci', {})
    if not isinstance(config, dict):
        raise ValueError('delivery.ci must be mapping')
    limit = config.get('max_iterations', 1)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= 3:
        raise ValueError('CI max_iterations must be integer from 0 to 3')
    rerun = _names(config.get('rerun_check_ids', []), 'rerun_check_ids')
    fixes = _names(config.get('fix_check_ids', []), 'fix_check_ids')
    imported = read_json(_local(root, report_path))
    if not isinstance(imported, dict) or not isinstance(imported.get('checks'), list) or not 1 <= len(imported['checks']) <= 1000:
        raise ValueError('CI report requires bounded nonempty checks')
    source = fingerprint(root)
    if imported.get('source') != source:
        return {'status': 'BLOCKED', 'reason': 'CI report source fingerprint is stale or missing', 'source': source}
    checks = []
    seen = set()
    for check in imported['checks']:
        if not isinstance(check, dict):
            raise ValueError('CI check must be mapping')
        name = _safe_id(check.get('name'))
        if name in seen or check.get('status') not in ('PASS', 'FAIL', 'BLOCKED', 'NOT_RUN'):
            raise ValueError('CI checks require unique names and explicit status')
        seen.add(name)
        log = _local(root, check.get('log'))
        if not log.is_file() or log.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('CI check requires bounded local log evidence')
        text = log.read_text(encoding='utf-8', errors='replace')
        checks.append({'name': name, 'status': check['status'], 'log': log.relative_to(root).as_posix(), 'cause': None if check['status'] == 'PASS' else _diagnosis(text), 'cause_verified': False, 'note': 'Heuristic candidate; confirm against actual implementation and test before fixing'})
    report = {'status': 'NOT_RUN', 'checks': checks, 'source': source, 'iterations': [], 'ci_status': 'PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL', 'reason': 'Imported evidence analyzed; no fresh CI execution'}
    failing = any(c['status'] != 'PASS' for c in checks)
    if not run or not failing or not rerun or not limit:
        return report
    # Resolve all IDs up front. Imported commands and check IDs never become executable.
    trusted = plan_validation(root, manifest, library, check_ids=list(dict.fromkeys(fixes + rerun)))
    if trusted['status'] == 'BLOCKED':
        return dict(report, status='BLOCKED', reason='Trusted CI checks are unavailable')
    specs = {check['id']: check for check in trusted['checks']}
    if any(not spec.get('argv') or spec.get('command') for spec in specs.values()):
        return dict(report, status='BLOCKED', reason='CI remediation requires trusted argv checks')
    for iteration in range(limit):
        if fingerprint(root) != source:
            return dict(report, status='BLOCKED', reason='Source changed before CI remediation')
        if fixes:
            fixed = run_validation(root, [specs[name] for name in fixes])
            report['iterations'].append({'iteration': iteration + 1, 'phase': 'fix', 'evidence': fixed})
            if fixed['status'] != 'PASS':
                return dict(report, status=fixed['status'], reason='Trusted fix checks failed')
            source = fingerprint(root)
        before = fingerprint(root)
        executed = run_validation(root, [specs[name] for name in rerun])
        report['iterations'].append({'iteration': iteration + 1, 'phase': 'rerun', 'evidence': executed})
        report['source'] = source
        if fingerprint(root) != before:
            return dict(report, status='BLOCKED', reason='Source changed during CI rerun')
        if executed['status'] == 'PASS':
            # A local subset cannot attest the remote pipeline or other imported checks.
            return dict(report, status='PASS', ci_status='NOT_RUN', reason='Configured local CI controls passed; remote CI not rerun')
        report['status'] = executed['status']
    return dict(report, reason='Bounded local CI attempts exhausted', ci_status='NOT_RUN')


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        if args.command == 'tracker':
            report = tracker(root, manifest, library, args.operation, json.loads(args.params), args.run, args.allow_write, args.provider)
        else:
            report = ci(root, manifest, library, args.report, args.run)
    except LayerError as error:  # K3: keep the layer message (e.g. no rule -> `skillsync layers use`)
        report = {'status': 'BLOCKED', 'reason': str(error)}
    except (ValueError, OSError, TypeError, KeyError, yaml.YAMLError, KeyboardInterrupt) as error:
        report = {'status': 'BLOCKED', 'reason': 'Invalid delivery configuration or local evidence'}
    return emit(_scrub(report), args.json)


def register(subparsers):
    parser = subparsers.add_parser('tracker', help='Plan or invoke trusted tracker operations')
    add_project_arguments(parser)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--provider')
    parser.add_argument('--params', default='{}')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--allow-write', action='store_true')
    parser.set_defaults(func=_cmd)
    parser = subparsers.add_parser('ci', help='Diagnose imported CI logs and run bounded trusted checks')
    add_project_arguments(parser)
    parser.add_argument('--report', required=True)
    parser.add_argument('--run', action='store_true')
    parser.set_defaults(func=_cmd)
