"""`skillsync setup`: machine setup wizard on top of the layer commands (INST-1).

Adds the core layer (tag = installed version), optionally one organisation layer (after an access check),
makes sure a `layer_rules` entry covers the projects and runs `layers check`. Idempotent: a second run with the
same arguments changes nothing. It only calls `layers add|use|check`; no layer logic lives here.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__

DEFAULT_CORE_SOURCE = 'https://github.com/BATUSystems/skillsync'
CORE_SUBDIR = 'library'
LS_REMOTE_TIMEOUT = 30
SCHEMA = 1


def register(subparsers):
    parser = subparsers.add_parser('setup', help='Set up this machine: core layer, optional organisation layer, '
                                                 'rules and a final check (idempotent)')
    parser.add_argument('--yes', action='store_true', help='Never ask questions; use flags and defaults')
    parser.add_argument('--dry-run', action='store_true', help='Show the plan (and check access); write nothing')
    parser.add_argument('--core-source', default=DEFAULT_CORE_SOURCE, metavar='URL',
                        help='Repository of the core layer (default: %(default)s)')
    parser.add_argument('--core-ref', metavar='REF', help=f'Tag, branch or commit of the core layer (default: v{__version__})')
    parser.add_argument('--org', metavar='ID', help='Id of the organisation layer to add')
    parser.add_argument('--org-source', metavar='URL', help='Repository URL of the organisation layer')
    parser.add_argument('--org-ref', metavar='REF', help='Tag, branch or commit of the organisation layer')
    parser.add_argument('--match', action='append', metavar='GLOB',
                        help='Project path glob the organisation layer applies to (repeatable; default `**`)')
    parser.add_argument('--json', action='store_true')
    parser.set_defaults(func=_cmd)


def _cmd(args):
    from .common import exit_code, redact
    report = redact(run_setup(args))   # credentials in URLs are masked in both output modes
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return exit_code(report)
    stream = sys.stdout if report['status'] in ('PASS', 'NOT_RUN') else sys.stderr
    suffix = ' (dry-run)' if report.get('dry_run') else ''
    print(f'setup: {report["status"]}{suffix}', file=stream)
    for item in report['steps']:
        print(f'  [{item["result"]}] {item["step"]}: {item["detail"]}', file=stream)
    for warning in report.get('warnings', []):
        print(f'  warning: {warning}', file=stream)
    if report['status'] in ('PASS', 'NOT_RUN'):
        print('Next:', file=stream)
        for line in report['next']:
            print(f'  {line}', file=stream)
    return exit_code(report)


class _Run:
    def __init__(self, args):
        self.args = args
        self.steps = []
        self.warnings = []
        self.dry = bool(args.dry_run)
        self.interactive = bool(not args.yes and sys.stdin is not None and sys.stdin.isatty())

    def step(self, name, result, detail, **extra):
        self.steps.append({'step': name, 'result': result, 'detail': detail, **extra})

    def blocked(self):
        return any(item['result'] == 'blocked' for item in self.steps)

    def ask(self, question, default=''):
        if not self.interactive:
            return default
        suffix = f' [{default}]' if default else ''
        prompt = f'{question}{suffix}: '
        try:
            if getattr(self.args, 'json', False):   # stdout carries the JSON report: ask on stderr
                print(prompt, end='', file=sys.stderr, flush=True)
                answer = input().strip()
            else:
                answer = input(prompt).strip()
        except EOFError:
            return default
        return answer or default


def run_setup(args):
    from . import layers
    run = _Run(args)
    try:
        _email_step(run)
        config = layers.load_machine_config()
    except layers.LayerError as exc:
        return _report(run, 'BLOCKED', reason=str(exc))
    _core_step(run, layers, config)
    org_id = None
    if not run.blocked():
        config = layers.load_machine_config()
        org_id = _org_step(run, layers, config)
    if not run.blocked():
        _rule_step(run, layers, org_id)
    status = 'BLOCKED' if run.blocked() else 'PASS'
    if status == 'PASS' and not run.dry:
        status = _check_step(run, layers)
    elif run.dry and status == 'PASS':
        run.step('check', 'skipped', 'dry-run: layers check is not run')
    return _report(run, status)


def _report(run, status, **extra):
    return {'schema': SCHEMA, 'status': status, 'dry_run': run.dry, 'steps': run.steps, 'warnings': run.warnings,
            'next': _next_steps(), **extra}


def _next_steps():
    return [
        'Link a project:   skillsync link <project> --dry-run && skillsync link <project>',
        'Check it:         skillsync doctor <project>',
        'Update layers:    skillsync pull (refreshes layer checkouts and links projects; it does not update '
        'the skillsync tool or a core layer pinned to a tag)',
        'Update the tool:  run install.sh from the new release tag (or `uv tool install --force git+'
        + DEFAULT_CORE_SOURCE + '@<tag>` and `skillsync layers update core --ref <tag>`)',
        'Shell PATH:       if `skillsync` is not found, run `uv tool update-shell` and open a new shell',
        'Docs (PL/EN):     ' + DEFAULT_CORE_SOURCE + '/blob/main/docs/installation.md',
    ]


def _git_global(*args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    try:
        return subprocess.run(['git', *args], capture_output=True, text=True, env=env, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None


def _email_step(run):
    """Step 0: `layers add` stores the author address in `leakguard.targets` at the moment it runs."""
    current = _git_global('config', '--global', 'user.email')
    email = current.stdout.strip() if current is not None and current.returncode == 0 else ''
    if email:
        run.step('author-email', 'unchanged', 'git user.email is set')
        return
    answer = run.ask('Commit author address (git user.email)', '') if not run.dry else ''
    if answer and '@' in answer:
        done = _git_global('config', '--global', 'user.email', answer)
        if done is not None and done.returncode == 0:
            run.step('author-email', 'done', 'git config --global user.email was set')
            return
    run.step('author-email', 'skipped', 'git user.email is not set')
    run.warnings.append('git user.email is not set: the leak guard stores the commit author address when a layer is '
                        'added, so its `authors` list is empty. Run `git config --global user.email "<address>"` and '
                        'then `skillsync layers check`; add the address under `leakguard.targets.<id>.authors` in the '
                        'machine config if needed.')


def _core_step(run, layers, config):
    existing = next((layer for layer in config.layers.values() if layer.kind == 'core'), None)
    ref = run.args.core_ref or f'v{__version__}'
    if existing is not None:
        detail = f'core layer {existing.id} is already configured'
        if existing.ref and existing.ref != ref:
            run.warnings.append(f'core layer {existing.id} is pinned to {existing.ref}, not {ref}; setup leaves it '
                                f'unchanged. Change it with `skillsync layers update {existing.id} --ref {ref}`.')
        run.step('core', 'unchanged', detail)
        return
    if run.dry:
        run.step('core', 'planned', f'would add core from {run.args.core_source} at {ref}')
        return
    report = layers.layers_add('core', 'core', run.args.core_source, subdir=CORE_SUBDIR, ref=ref, writable=False)
    if report['status'] != 'PASS':
        reason = report.get('reason', 'layers add failed')
        hint = (f' The ref {ref} does not exist in {run.args.core_source}. If this skillsync is not a release build, '
                'pass an existing tag, branch or commit with --core-ref.') if report.get('code') == 'unknown-ref' else ''
        run.step('core', 'blocked', reason + hint)
        return
    run.step('core', 'done', f'core layer added at {ref}')


_SSH_BATCH = '-o BatchMode=yes -o ConnectTimeout=15'
_ACCESS_PHRASES = ('permission denied', 'publickey', 'host key verification failed', 'could not resolve',
                   'unable to access', 'authentication failed', 'repository not found', 'could not read from remote')


def _ls_remote(url):
    """Access check before cloning. Returns (ok, message): the message is the most telling stderr line."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    # GIT_SSH_COMMAND wins over core.sshCommand, so extend it instead of being overridden by it.
    env['GIT_SSH_COMMAND'] = f"{os.environ.get('GIT_SSH_COMMAND') or 'ssh'} {_SSH_BATCH}"
    command = ['git', 'ls-remote', '--exit-code', url, 'HEAD']
    try:
        process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=LS_REMOTE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, 'timed out'
    except OSError as exc:
        return False, str(exc)
    if process.returncode == 0:
        return True, ''
    lines = [line.strip() for line in (process.stderr or '').splitlines() if line.strip()]
    telling = next((line for line in lines if any(phrase in line.lower() for phrase in _ACCESS_PHRASES)), None)
    return False, telling or (lines[-1] if lines else f'git exit {process.returncode}')


def _access_help(message):
    low = message.lower()
    if 'permission denied' in low or 'publickey' in low:
        return ('SSH rejected the key. Check that your SSH key is loaded (`ssh-add -l`) and added to your account at the '
                'provider; `ssh -T <git-host>` shows how the host sees you.')
    if 'host key verification failed' in low:
        return ('The host key is unknown. Connect once interactively (`ssh -T <git-host>`, answer yes) and run setup again.')
    if 'could not resolve' in low or 'timed out' in low or 'unable to access' in low or 'connection timed out' in low:
        return 'The host is not reachable. Check the network or VPN and run setup again.'
    return 'Check that `git ls-remote <url>` works without any question, then run setup again.'


def _org_step(run, layers, config):
    org_id = run.args.org
    source = run.args.org_source
    if not org_id and run.interactive:
        org_id = run.ask('Organisation layer id (empty = none)', '') or None
    if not org_id:
        run.step('org', 'skipped', 'no organisation layer requested')
        return None   # existing organisation layers and their rules are left alone
    if not source:
        source = run.ask(f'Repository URL of the {org_id} layer', '') or None
    existing = config.layers.get(org_id)
    if existing is not None:
        if existing.kind != 'org' or (source and existing.source != source):
            run.step('org', 'blocked', f'layer {org_id} is already configured with a different kind or source '
                     f'({existing.kind}, {existing.source}); change it with `skillsync layers` commands')
            return None
        run.step('org', 'unchanged', f'organisation layer {org_id} is already configured')
        return org_id
    if not source:
        run.step('org', 'blocked', f'--org-source is required to add the organisation layer {org_id}')
        return None
    ok, message = _ls_remote(source)
    if not ok:
        from .common import redact
        run.step('org', 'blocked', f'no access to {redact(source)}: {redact(message)}. {_access_help(message)}')
        return None
    if run.dry:
        run.step('org', 'planned', f'would add organisation layer {org_id} (read-only) from {source}')
        return org_id
    report = layers.layers_add(org_id, 'org', source, ref=run.args.org_ref, writable=False)
    if report['status'] != 'PASS':
        reason = report.get('reason', 'layers add failed')
        if 'not empty' in reason:
            reason += (' A previous run was probably interrupted while cloning; remove that directory (it is not in the '
                       'machine config) and run setup again.')
        run.step('org', 'blocked', reason)
        return None
    run.step('org', 'done', f'organisation layer {org_id} added (read-only)')
    return org_id


def _rule_step(run, layers, org_id):
    config = layers.load_machine_config()
    if org_id is None:
        if run.dry:
            if config.rules:
                run.step('rule', 'unchanged', 'the config already has layer_rules')
            else:
                run.step('rule', 'planned', 'would add the default rule `**: [core]`')
            return
        report = layers.layers_ensure_core_rule()
        if report['status'] != 'PASS':
            run.step('rule', 'blocked', report.get('reason', 'could not add the default rule'))
        elif report.get('changed'):
            run.step('rule', 'done', 'default rule `**: [core]` added')
        else:
            run.step('rule', 'unchanged', 'the config already has layer_rules')
        return
    globs = run.args.match
    if not globs:
        only_default = len(config.rules) == 1 and config.rules[0]['match'] == ['**'] and len(config.rules[0]['use']) == 1
        if config.rules and not only_default and not run.interactive \
                and org_id not in {item for rule in config.rules for item in rule['use']}:
            run.step('rule', 'blocked', 'the config already has layer_rules: pass --match <glob> to say which projects '
                     f'use {org_id}')
            return
        answer = run.ask('Project path glob for the organisation layer', '**')
        globs = [answer or '**']
    if org_id in {item for rule in config.rules for item in rule['use']} and not run.args.match:
        run.step('rule', 'unchanged', f'a layer_rules entry already uses {org_id}')
        return
    if run.dry:
        run.step('rule', 'planned', f'would add a rule for {org_id}: match {globs}')
        return
    report = layers.layers_use(org_id, globs)
    if report['status'] != 'PASS':
        run.step('rule', 'blocked', report.get('reason', 'layers use failed'))
    elif report.get('changed') is False:
        run.step('rule', 'unchanged', f'the rule for {org_id} already exists')
    else:
        run.step('rule', 'done', f'rule added for {org_id}: match {globs}')
    run.warnings.extend(report.get('warnings', []) if report.get('changed', True) else [])


def _probe_project(layers):
    """A fresh temporary directory that the configured rules match: anywhere for `**`, otherwise inside the literal
    prefix of a glob (`~/X/**` -> `~/X/<tmp>`). Returns (path, cleanup) or (None, None) when no prefix directory exists."""
    config = layers.load_machine_config()
    globs = [glob for rule in config.rules for glob in rule['match']]
    if '**' in globs:
        path = Path(tempfile.mkdtemp(prefix='skillsync-setup-'))
        return path, lambda: shutil.rmtree(path, ignore_errors=True)
    for glob in globs:
        prefix = glob.split('*', 1)[0].rstrip('/')
        if not prefix:
            continue
        base = Path(os.path.expanduser(prefix))
        if base.is_dir() and os.access(base, os.W_OK):
            path = Path(tempfile.mkdtemp(prefix='skillsync-setup-', dir=base))
            return path, lambda: shutil.rmtree(path, ignore_errors=True)
    return None, None


def _check_step(run, layers):
    probe, cleanup = _probe_project(layers)
    try:
        if probe is not None:
            report = layers.layers_check(str(probe))
        else:
            # No directory matches the rules yet: check every configured layer from a neutral cwd (never the
            # caller's cwd, which may be inside a project without a rule).
            neutral = tempfile.mkdtemp(prefix='skillsync-setup-cwd-')
            previous = os.getcwd()
            try:
                os.chdir(neutral)
                report = layers.layers_check(None)
            finally:
                os.chdir(previous)
                shutil.rmtree(neutral, ignore_errors=True)
            run.warnings.append('No existing directory matches the configured rules, so the rule was not checked '
                                'against a project. After cloning one run `skillsync layers check --project <project>`.')
    finally:
        if cleanup:
            cleanup()
    status = report['status']
    findings = report.get('findings', [])
    detail = 'layers check: ' + status + (f' ({len(findings)} findings: ' + ', '.join(sorted({item["code"] for item in findings}))
                                         + ')' if findings else '')
    run.step('check', 'done' if status == 'PASS' else 'blocked' if status == 'BLOCKED' else 'failed', detail)
    return status if status in ('PASS', 'FAIL', 'BLOCKED') else 'BLOCKED'
