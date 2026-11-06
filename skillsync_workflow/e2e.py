"""Local browser observations and evidence-bearing native E2E execution."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import yaml
from .common import add_project_arguments, emit, load_layer_project, redact, workflow_config
from .environment import checks, limit
from .quality import _locks, run_validation
from .runs import fingerprint
from .storage import digest, read_json, state_path, write_json


def _step_redact(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if re.search(r'(?i)password|passwd|token|secret|api[_-]?key|authorization|credential', str(key)) else _step_redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_step_redact(item) for item in value]
    return redact(value)


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', value) or '..' in value:
        raise ValueError('Observation id must be a safe slug')
    return value


def configuration(root, manifest, library):
    config = workflow_config(root, manifest, library).get('e2e', {})
    if not isinstance(config, dict):
        raise ValueError('e2e must be a mapping')
    config = dict(config)
    for key, default in [('observations', 'e2e/observations'), ('scenarios', 'e2e/scenarios')]:
        directory = config.setdefault(key, default)
        if not isinstance(directory, str) or not directory.startswith('e2e/'):
            raise ValueError(f'e2e.{key} must be relative under .skillsync/e2e')
        state_path(root, directory + '/probe.json')
    browser = config.get('browser', {})
    if not isinstance(browser, (str, dict)):
        raise ValueError('e2e.browser must be a provider name or descriptor')
    config['ttl'] = limit(config.get('ttl', 86400), 'e2e.ttl', 604800)
    config['checks'] = checks(root, manifest, library, config.get('checks', []))['checks']
    return config


def evidence_record(root, value):
    name = value.get('path') if isinstance(value, dict) else value
    if not isinstance(name, str):
        raise ValueError('Evidence requires a local path')
    relative = name.removeprefix('.skillsync/')
    if not relative.startswith('e2e/') or any(re.search(r'(?i)(^\.env|secret|credential|password|token|private.*key)', part) for part in Path(relative).parts):
        raise ValueError('Evidence must be a non-secret file under .skillsync/e2e')
    path = state_path(root, relative)
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError('Evidence must be an existing bounded regular local file')
    result = {'path': '.skillsync/' + relative, 'digest': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    if isinstance(value, dict) and value.get('digest') != result['digest']:
        raise ValueError('Observation evidence has changed')
    return result


def observation(root, data, config):
    if not isinstance(data, dict):
        raise ValueError('Observation must be a mapping')
    identity = slug(data.get('id'))
    url = data.get('URL', data.get('url'))
    if not isinstance(url, str):
        raise ValueError('Observation requires URL')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Observation URL must be HTTP(S) without credentials')
    if parsed.query or parsed.fragment:
        raise ValueError('Observation URL must omit query/fragment to avoid persisting credentials')
    stamp = data.get('observed_at')
    if not isinstance(stamp, str):
        raise ValueError('Observation requires timezone-aware observed_at')
    try:
        when = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if when.tzinfo is None:
            raise ValueError('missing timezone')
        age = (datetime.now(timezone.utc) - when).total_seconds()
    except (ValueError, OverflowError):
        raise ValueError('Invalid observation timestamp') from None
    if not 0 <= age <= config['ttl']:
        raise ValueError('Observation has expired or is dated in the future')
    source = data.get('source_fingerprint', data.get('fingerprint'))
    if source != fingerprint(root):
        raise ValueError('Observation source fingerprint is stale')
    steps = data.get('steps')
    if not isinstance(steps, list) or not steps or len(steps) > 200:
        raise ValueError('Observation requires between 1 and 200 observed steps')
    normalized = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Observed step must be a mapping')
        action, locator, expected = (step.get(key) for key in ('action', 'locator', 'expected'))
        if not isinstance(action, str) or not action or not isinstance(locator, (str, dict)) or not locator or not isinstance(expected, (str, dict)) or not expected:
            raise ValueError('Each observed step requires action, locator and expected evidence')
        if any(key in step for key in ('argv', 'command', 'shell')):
            raise ValueError('Observation steps are data, never executable commands')
        normalized.append(_step_redact({'action': action, 'locator': locator, 'expected': expected}))
    evidence = data.get('evidence')
    if not isinstance(evidence, list) or not evidence or len(evidence) > 100:
        raise ValueError('Observation requires local evidence')
    records = [evidence_record(root, item) for item in evidence]
    return {'id': identity, 'URL': url, 'observed_at': stamp, 'source_fingerprint': source, 'steps': normalized, 'evidence': records}


def e2e(root, manifest, library, action='plan', run=False, observation_path=None):
    config = configuration(root, manifest, library)
    if action == 'plan':
        return {'status': 'NOT_RUN', 'checks': redact(config['checks']), 'browser': redact(config.get('browser')), 'observations': config['observations'], 'scenarios': config['scenarios'], 'reason': 'Record real browser observations, then run configured native checks'}
    if not run:
        return {'status': 'NOT_RUN', 'reason': 'Recording and running require --run'}
    with _locks(['e2e:' + str(root)]):
        if action == 'record':
            if not observation_path:
                raise ValueError('record requires --observation')
            # Input itself is confined: never read a user-provided secret elsewhere.
            relative = str(observation_path).removeprefix('.skillsync/')
            if not relative.startswith('e2e/'):
                raise ValueError('Observation input must be under .skillsync/e2e')
            data = observation(root, read_json(state_path(root, relative)), config)
            path = state_path(root, config['observations'] + '/' + data['id'] + '.json', create=True)
            scenario = state_path(root, config['scenarios'] + '/' + data['id'] + '.json', create=True)
            write_json(path, data)
            write_json(scenario, {'version': 1, 'source_observation': str(path.relative_to(root)), 'observation_digest': digest(data), 'source_fingerprint': data['source_fingerprint'], 'URL': data['URL'], 'steps': data['steps'], 'evidence': data['evidence'], 'runner': 'Configured native e2e checks consume this observation; steps are not commands'})
            return {'status': 'NOT_RUN', 'recorded': str(path), 'scenario': str(scenario), 'reason': 'Observation recorded; no E2E tests executed'}
        directory = state_path(root, config['observations'] + '/probe.json').parent
        if observation_path:
            relative = str(observation_path).removeprefix('.skillsync/')
            if not relative.startswith(config['observations'] + '/'):
                raise ValueError('run observation must be a recorded observation')
            paths = [state_path(root, relative)]
        else:
            paths = sorted(directory.glob('*.json')) if directory.exists() else []
        if not paths:
            return {'status': 'BLOCKED', 'reason': 'No recorded browser observations'}
        observations = []
        for path in paths:
            path = state_path(root, str(path.relative_to(root / '.skillsync')))
            data = observation(root, read_json(path), config)
            scenario = read_json(state_path(root, config['scenarios'] + '/' + data['id'] + '.json'))
            if not isinstance(scenario, dict) or scenario.get('observation_digest') != digest(data) or any(scenario.get(key) != data[key] for key in ('source_fingerprint', 'URL', 'steps', 'evidence')):
                raise ValueError('Generated scenario missing or stale')
            observations.append({'id': data['id'], 'digest': digest(data)})
        native = config['checks']
        if not native or not any(item.get('kind') == 'e2e' and item.get('required', True) for item in native):
            return {'status': 'BLOCKED', 'reason': 'No required native E2E/test checks configured'}
        before = fingerprint(root)
        result = run_validation(root, native)
        if fingerprint(root) != before:
            result.update(status='BLOCKED', reason='Source changed during E2E execution')
        result['observations'] = observations
        result['source_fingerprint'] = fingerprint(root)
        result['browser_verification'] = 'Recorded observations; native runner evidence reported separately'
        path = state_path(root, 'e2e/last-run.json', create=True)
        write_json(path, redact(result))
        result['report'] = str(path)
        return result


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        report = e2e(root, manifest, library, args.action, args.run, args.observation)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, yaml.YAMLError, KeyboardInterrupt) as error:
        report = {'status': 'BLOCKED', 'reason': str(error) or 'Interrupted'}
    return emit(report, args.json)


def register(subparsers):
    parser = subparsers.add_parser('e2e', help='Record real observations and run native E2E checks')
    add_project_arguments(parser)
    parser.add_argument('--action', choices=['plan', 'record', 'run'], default='plan')
    parser.add_argument('--observation')
    parser.add_argument('--run', action='store_true')
    parser.set_defaults(func=_cmd)
