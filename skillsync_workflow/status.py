"""Project status for IDE integrations (read-only: plans with progress, specs, FRD files, run checkpoints, claims) and the only write
operation, `plan archive|unarchive`, which rewrites a single plan Status line."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import time
import yaml

from .common import add_project_arguments, read_project, redact
from .layers import add_layer_argument
from .storage import read_json

SCHEMA = 1
PLAN_STATUSES = ('draft', 'approved', 'in_progress', 'done', 'archived')
SPEC_STATUSES = ('draft', 'approved', 'implemented', 'superseded')
MAX_PLANS = 200
MAX_DOCS = 200
MAX_PLAN_BYTES = 512 * 1024
# Tolerates older plan styles: `**Status:** done`, `Status: **done**`, `Status: `done``, `- Status:`, `> Status:`,
# YAML front matter `status: done`. Group 1 = raw value line (for statusText), group 2 = first word.
_STATUS = re.compile(r'^(?:[-*>][ \t]+)?(?:\*\*|__)?status(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*'
                     r'(?P<raw>[`*_]*(?P<word>[A-Za-z_]+)[^\n]*)', re.MULTILINE | re.IGNORECASE)
STATUS_TEXT_MAX = 80
_TITLE = re.compile(r'^#\s+(.+?)\s*$', re.MULTILINE)
_STEP = re.compile(r'^\s*-\s\[([ xX])\]\s+\S', re.MULTILINE)
_PR = re.compile(r'^PR:\s*#(\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
_TICKET = re.compile(r'^Ticket:\s*([A-Z][A-Z0-9_]+-\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
SPEC_LINE = re.compile(r'^Spec:(.*)$', re.MULTILINE)
SPEC_FULL = re.compile(r'^Spec:[ \t]*`?([^\s`]*)`?[ \t\r]*$')
_SPEC_PLAN_LINE = re.compile(r'^Plan:.*$', re.MULTILINE)
_SPEC_PLAN_FULL = re.compile(r'^Plan:[ \t]*`?([^\s`]*)`?[ \t\r]*$')


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _section_marks(text, heading):
    match = re.search(heading, text, re.MULTILINE)
    if not match:
        return []
    rest = text[match.end():]
    end = re.search(r'^##\s+\S', rest, re.MULTILINE)
    return _STEP.findall(rest[:end.start()] if end else rest)


def _progress(text):
    """Return {'total', 'done'} from the `## Progress` section; older plans without it fall back to a checklist
    in `## Kroki` / `## Steps`. None when neither section has checklist steps."""
    marks = (_section_marks(text, r'^##\s+Progress\s*$')
             or _section_marks(text, r'^##\s+(?:Kroki|Steps)\b[^\n]*$'))
    return {'total': len(marks), 'done': sum(1 for mark in marks if mark in 'xX')} if marks else None


def _status_fields(text, allowed):
    """Return (status, statusText) from the first status line; a word outside `allowed` gives `unknown`."""
    match = _STATUS.search(text[:4000])
    status = match.group('word').lower() if match else 'unknown'
    status_text = re.sub(r'[`*]', '', match.group('raw')).strip()[:STATUS_TEXT_MAX] if match else None
    return status if status in allowed else 'unknown', redact(status_text) if status_text else None


def _doc_shape(name, folder):
    """True when `name` is exactly `docs/<folder>/<file>.md`, without README.md."""
    parts = PurePosixPath(name).parts
    return (len(parts) == 3 and parts[:2] == ('docs', folder) and str(PurePosixPath(name)) == name and '\\' not in name
            and name.endswith('.md') and parts[2] != '.md' and parts[2].lower() != 'readme.md')


def is_spec_path(name):
    return _doc_shape(name, 'specs')


def _plan_spec(text):
    """Path from the plan's only `Spec:` line when it has the spec shape, otherwise None."""
    lines = SPEC_LINE.findall(text)
    match = SPEC_FULL.match('Spec:' + lines[0]) if len(lines) == 1 else None
    return match.group(1) if match and is_spec_path(match.group(1)) else None


def plan_needs_human(status, steps, ticket=False, pr=False):
    """`approval` for a draft; for an `in_progress` plan whose Progress steps are all checked: `closure` (Result,
    `--final` and `done` are still missing), or `pull_request` when it came from a ticket and has no `PR:` line yet
    (it stays `in_progress` until the human code review and the PRs; information, not a closure gap)."""
    if status == 'draft':
        return ['approval']
    if status == 'in_progress' and steps and steps['total'] and steps['done'] == steps['total']:
        return ['pull_request'] if ticket and not pr else ['closure']
    return []


def plan_text_needs_human(status, text):
    """`plan_needs_human` read from the whole plan text (Progress, `Ticket:` and `PR:` lines)."""
    return plan_needs_human(status, _progress(text), bool(_TICKET.search(text)), bool(_PR.search(text)))


def plan_record(root, path):
    text = path.read_text(encoding='utf-8')
    status, status_text = _status_fields(text, PLAN_STATUSES)
    title = _TITLE.search(text)
    steps = _progress(text)
    pr = _PR.search(text)
    ticket = _TICKET.search(text)
    return {'path': path.relative_to(root).as_posix(), 'title': title.group(1) if title else path.stem, 'status': status,
            'statusText': status_text, 'steps': steps, 'pr': {'number': int(pr.group(1)), 'link': pr.group(2)} if pr else None,
            'ticket': {'key': ticket.group(1), 'link': ticket.group(2)} if ticket else None, 'spec': _plan_spec(text),
            'needsHuman': plan_needs_human(status, steps, bool(ticket), bool(pr)), 'updated': _iso(path.stat().st_mtime)}


def _plans(root, findings):
    directory = root / 'docs' / 'plans'
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob('*.md'), reverse=True)[:MAX_PLANS]:
        if path.is_symlink() or path.name.lower() == 'readme.md':
            continue
        try:
            if path.stat().st_size > MAX_PLAN_BYTES:
                findings.append(f'Plan skipped (over {MAX_PLAN_BYTES // 1024} KiB): {path.name}')
                continue
            rows.append(plan_record(root, path))
        except (OSError, UnicodeDecodeError) as error:
            findings.append(f'Plan unreadable: {path.name} ({type(error).__name__})')
    return rows


def _doc_path(root, path):
    return path.relative_to(root).as_posix()


def _doc_files(root, folder, findings, suffix='', reverse=False):
    """Regular, non-symlink, non-hidden files directly in `docs/<folder>`, without README.md, sorted by name.

    Only `stat` happens here: the content stays unread. A name that is not valid UTF-8 becomes a finding, because it
    would break printing the whole report."""
    directory = root / 'docs' / folder
    if (root / 'docs').is_symlink() or directory.is_symlink() or not directory.is_dir():
        return []
    rows = []
    try:
        for path in sorted(directory.iterdir(), key=lambda item: item.name, reverse=reverse):
            name = path.name
            if name.startswith('.') or not name.endswith(suffix) or name.lower() == 'readme.md':
                continue
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
            except FileNotFoundError:
                continue
            try:
                name.encode('utf-8')
            except UnicodeEncodeError:
                shown = name.encode('utf-8', 'backslashreplace').decode('utf-8')
                findings.append(f'Document skipped (name is not valid UTF-8): docs/{folder}/{shown}')
                continue
            rows.append(path)
    except OSError as error:
        findings.append(f'Directory unreadable: docs/{folder} ({type(error).__name__})')
        return []
    if len(rows) > MAX_DOCS:
        findings.append(f'Documents over the limit of {MAX_DOCS} in docs/{folder}: {len(rows) - MAX_DOCS} skipped')
    return rows[:MAX_DOCS]


def _spec_plan(text):
    """Path from the first `Plan:` line when it has the plan shape, otherwise None (also the template placeholder)."""
    line = _SPEC_PLAN_LINE.search(text)
    match = _SPEC_PLAN_FULL.match(line.group(0)) if line else None
    return match.group(1) if match and _doc_shape(match.group(1), 'plans') else None


def _specs(root, findings):
    rows = []
    for path in _doc_files(root, 'specs', findings, suffix='.md', reverse=True):
        try:
            if path.stat().st_size > MAX_PLAN_BYTES:
                findings.append(f'Spec skipped (over {MAX_PLAN_BYTES // 1024} KiB): {path.name}')
                continue
            text = path.read_text(encoding='utf-8')
            status, status_text = _status_fields(text, SPEC_STATUSES)
            title = _TITLE.search(text)
            rows.append({'path': _doc_path(root, path), 'title': title.group(1) if title else path.stem, 'status': status,
                         'statusText': status_text, 'plan': _spec_plan(text), 'updated': _iso(path.stat().st_mtime)})
        except (OSError, UnicodeDecodeError) as error:
            findings.append(f'Spec unreadable: {path.name} ({type(error).__name__})')
    return rows


def _frds(root, findings):
    rows = []
    for path in _doc_files(root, 'frd', findings):
        try:
            rows.append({'path': _doc_path(root, path), 'name': path.name, 'updated': _iso(path.stat().st_mtime)})
        except OSError as error:
            findings.append(f'FRD unreadable: {path.name} ({type(error).__name__})')
    return rows


def _runs(root, findings):
    from .runs import _read
    directory = root / '.skillsync' / 'runs'
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob('*.json')):
        try:
            data = _read(path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            findings.append(f'Run unreadable: {path.stem} ({" ".join(str(error).split())[:120]})')
            continue
        if 'benchmark' in data or not data['checkpoints']:
            continue
        last = data['checkpoints'][-1]
        rows.append({'id': path.stem, 'checkpoints': len(data['checkpoints']), 'state': last['status'],
                     'lastStep': last['step'], 'recordedAt': last['recorded_at'], 'next': last['next']})
    return rows


def _claims(root, findings):
    directory = root / '.skillsync' / 'claims'
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows, now = [], time.time()
    for path in sorted(directory.glob('*.json')):
        try:
            record = read_json(path)
            rows.append({'task': record['task_id'], 'owner': record['run_id'], 'acquiredAt': _iso(record['acquired_at']),
                         'expiresAt': _iso(record['expires_at']), 'active': record['expires_at'] > now})
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            findings.append(f'Claim unreadable: {path.stem} ({type(error).__name__})')
    return rows


def project_status(root, manifest):
    root = Path(root).resolve()
    findings = []
    project = manifest.get('project', {})
    name = project.get('name') if isinstance(project, dict) else None
    return {'schema': SCHEMA, 'status': 'PASS', 'project': name or f'{root.parent.name}/{root.name}', 'root': str(root),
            'plans': _plans(root, findings), 'specs': _specs(root, findings), 'frds': _frds(root, findings), 'runs': _runs(root, findings), 'claims': _claims(root, findings),
            'findings': findings}


def _cmd_status(args):
    try:
        root, manifest = read_project(args.path)
        report, code = project_status(root, manifest), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


_TRANSITIONS = {'approve': ('draft', 'approved'), 'archive': ('done', 'archived'), 'unarchive': ('archived', 'done')}


def _plan_file(root, name):
    """Resolve `name` to a regular, non-symlink `docs/plans/*.md` file inside the project or raise ValueError."""
    if not name or '\0' in name or '..' in Path(name).parts:
        raise ValueError(f'Invalid plan path: {name!r}')
    plans = root / 'docs' / 'plans'
    for part in (root / 'docs', plans):
        if part.is_symlink():
            raise ValueError(f'Symlink in plan path: {part.relative_to(root)}')
    if not plans.is_dir():
        raise ValueError('Project has no docs/plans directory')
    given = Path(name).expanduser()
    candidate = given if given.is_absolute() else root / given
    if candidate.is_symlink():
        raise ValueError(f'Plan is a symlink: {name}')
    resolved = candidate.resolve()
    if resolved.parent != plans.resolve() or resolved.suffix != '.md' or resolved.name.lower() == 'readme.md':
        raise ValueError(f'Plan must be a docs/plans/*.md file (not README.md): {name}')
    if not resolved.is_file():
        raise ValueError(f'Plan not found: {name}')
    if resolved.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError(f'Plan over {MAX_PLAN_BYTES // 1024} KiB: {name}')
    return resolved


def set_plan_status(root, name, action):
    """Rewrite only the status word of the first Status line (suffix and markup kept), atomically. Returns the report."""
    root = Path(root).resolve()
    source, target = _TRANSITIONS[action]
    path = _plan_file(root, name)
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    match = _STATUS.search(text[:4000])
    if not match:
        raise ValueError('Plan has no Status line in the first 4000 characters')
    current = match.group('word').lower()
    if current != source:
        raise ValueError(f'Cannot {action} a plan with status {current!r}; expected {source!r}')
    start, end = match.span('word')
    updated = (text[:start] + target + text[end:]).encode('utf-8')
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {'schema': SCHEMA, 'status': 'PASS', 'reason': f'Plan status {source} -> {target}', 'root': str(root),
            'plan': path.relative_to(root).as_posix(), 'from': source, 'to': target}


def _cmd_plan(args):
    try:
        root, _ = read_project(args.path)
        report, code = set_plan_status(root, args.plan, args.plan_action), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500],
                        'root': str(Path(args.path).expanduser().resolve())}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def register(subparsers):
    parser = subparsers.add_parser('status', help='Read-only project status: plans, runs, claims (JSON)')
    add_project_arguments(parser)
    parser.set_defaults(func=_cmd_status)
    plan = subparsers.add_parser('plan', help='Plan file operations: approve / archive / unarchive (changes only the Status line)')
    actions = plan.add_subparsers(dest='plan_action', required=True)
    for action, (source, target) in _TRANSITIONS.items():
        sub = actions.add_parser(action, help=f'Set Status {source} -> {target} in a docs/plans/*.md file (JSON)')
        sub.add_argument('path')
        sub.add_argument('plan', help='Plan file (relative to the project or absolute) in docs/plans')
        add_layer_argument(sub)
        sub.add_argument('--json', action='store_true')
        sub.set_defaults(func=_cmd_plan)
