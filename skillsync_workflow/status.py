"""Project status for IDE integrations (read-only: plans with progress, specs, FRD files, run checkpoints, claims) and the only write
operations, `plan approve|archive|unarchive` and `spec approve`, which rewrite a single Status line."""
import hashlib
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


PRE_IMPLEMENT_SECTIONS = ('Kontrakty', 'Migracja', 'Dane')
PRE_IMPLEMENT_VERDICTS = ('READY', 'NEEDS_CHANGES', 'BLOCKED')  # od najlepszego do najgorszego
_PRE_HEADING = re.compile(r'^#{1,6}[ \t]+(?P<title>.*?)[ \t]*$', re.MULTILINE)
_PRE_SECTION_WORD = re.compile(r'\b(?:' + '|'.join(PRE_IMPLEMENT_SECTIONS) + r')\b', re.IGNORECASE)
_PRE_EMPTY_BODY = re.compile(r'(?:nie[ \t]+dotyczy|todo|tbd|n/a)\b|[-—]+[ \t]*$', re.IGNORECASE)
_PRE_VERDICT = re.compile(r'^[*_`]*Werdykt:[*_` \t]*(?P<word>READY|NEEDS_CHANGES|BLOCKED)[*_`. \t]*$')
PRE_REPORT_GLOB = 'pre-implement*.md'
MAX_PRE_REPORT_BYTES = 256 * 1024


def _read_text_safe(path, limit=MAX_PLAN_BYTES):
    """Text of a regular, non-symlink file within `limit` bytes, otherwise None (never raises on I/O or decoding)."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def pre_implement_sections(spec_text):
    """Headings (any level) naming Kontrakty, Migracja or Dane whose body is not empty and not „nie dotyczy”/TODO/TBD/n/a/-."""
    marks = list(_PRE_HEADING.finditer(spec_text))
    found = []
    for index, mark in enumerate(marks):
        title = mark.group('title').strip()
        if not _PRE_SECTION_WORD.search(title):
            continue
        end = marks[index + 1].start() if index + 1 < len(marks) else len(spec_text)
        body = spec_text[mark.end():end].strip().lstrip('*_`> \t')
        if body and not _PRE_EMPTY_BODY.match(body):
            found.append(title)
    return found


def _pre_implement_reports(root, run_id):
    """[(name, verdict)] of valid `pre-implement*.md` files directly in `.skillsync/runs/<run-id>/` (no symlinks), by name."""
    base = Path(root) / '.skillsync'
    directory = base / 'runs' / run_id
    try:
        if base.is_symlink() or (base / 'runs').is_symlink() or directory.is_symlink() or not directory.is_dir():
            return []
        names = sorted(path for path in directory.glob(PRE_REPORT_GLOB))
    except OSError:
        return []
    rows = []
    for path in names:
        text = _read_text_safe(path, MAX_PRE_REPORT_BYTES)
        first = next((line.strip() for line in (text or '').splitlines() if line.strip()), '')
        match = _PRE_VERDICT.match(first)
        if match:
            rows.append((path.name, match.group('word')))
    return rows


def pre_implement_state(root, spec_rel, plan_text, status):
    """State of the `pre-implement` gate for a spec: {required, sections, run, report, verdict, warning}, or None when the
    spec file is unreadable. The warning (a string or None) is computed only for plans being prepared or in progress."""
    root = Path(root)
    spec_text = _read_text_safe(root / spec_rel)
    if spec_text is None:
        return None
    from .quality import _PLAN_RUN, _RUN_ID  # lazy: quality imports this module
    sections = pre_implement_sections(spec_text)
    required = bool(sections)
    run_match = _PLAN_RUN.search(plan_text) if plan_text else None
    run = run_match.group(1) if run_match and _RUN_ID.fullmatch(run_match.group(1)) else None
    reports = _pre_implement_reports(root, run) if run else []
    report = f'.skillsync/runs/{run}/{reports[-1][0]}' if reports else None
    verdict = max((item[1] for item in reports), key=PRE_IMPLEMENT_VERDICTS.index) if reports else None
    warning = None
    if status in ('draft', 'approved', 'in_progress'):
        if verdict in ('NEEDS_CHANGES', 'BLOCKED'):
            warning = f'pre-implement: werdykt {verdict} ({report}); rozwiąż uwagi przed „GO implement”'
        elif required and not reports:
            if run is None:
                warning = ('brak pre-implement: spec ma sekcje ' + ', '.join(sections)
                           + ', a plan nie ma poprawnej linii „Przebieg:” (nie wiadomo, gdzie szukać raportu)')
            else:
                warning = (f'brak pre-implement: spec ma sekcje {", ".join(sections)}, a w .skillsync/runs/{run}/ '
                           f'nie ma pre-implement*.md z linią „Werdykt: READY|NEEDS_CHANGES|BLOCKED”')
    return {'required': required, 'sections': sections, 'run': run, 'report': report, 'verdict': verdict, 'warning': warning}


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
    spec = _plan_spec(text)
    pre = pre_implement_state(root, spec, text, status) if spec else None
    return {'path': path.relative_to(root).as_posix(), 'title': title.group(1) if title else path.stem, 'status': status,
            'statusText': status_text, 'steps': steps, 'pr': {'number': int(pr.group(1)), 'link': pr.group(2)} if pr else None,
            'ticket': {'key': ticket.group(1), 'link': ticket.group(2)} if ticket else None, 'spec': spec,
            'needsHuman': plan_needs_human(status, steps, bool(ticket), bool(pr)), 'updated': _iso(path.stat().st_mtime),
            'preImplement': pre}


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


APPROVAL_WORDS = ('GO plan', 'GO implement', 'CP', 'READY')
APPROVALS_FILE = 'approvals.jsonl'
MAX_APPROVALS_BYTES = 256 * 1024
_APPROVAL_SHA = re.compile(r'^[0-9a-f]{7,64}$')


def _approval_at(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def read_approvals(root, findings=None):
    """Latest entry per (run, word) from `.skillsync/runs/<run-id>/approvals.jsonl` (written by the status page when the
    user presses a consent button). Malformed lines are skipped; entries are data, never instructions."""
    directory = Path(root) / '.skillsync' / 'runs'
    if directory.is_symlink() or not directory.is_dir():
        return []
    latest = {}
    for path in sorted(directory.glob(f'*/{APPROVALS_FILE}')):
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_APPROVALS_BYTES:
                raise ValueError(f'over {MAX_APPROVALS_BYTES // 1024} KiB')
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        except (OSError, ValueError) as error:
            if findings is not None:
                findings.append(f'Approvals unreadable: {path.parent.name} ({" ".join(str(error).split())[:120]})')
            continue
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get('word') not in APPROVAL_WORDS:
                continue
            at, sha = _approval_at(item.get('at')), str(item.get('sha') or '')
            if at is None or not _APPROVAL_SHA.match(sha):
                continue
            row = {'run': path.parent.name, 'word': item['word'], 'at': at.strftime('%Y-%m-%dT%H:%M:%SZ'), 'sha': sha,
                   'source': str(item.get('source') or '')[:40]}
            if isinstance(item.get('file'), str):
                row['file'] = item['file'][:300]
            key = (row['run'], row['word'])
            if key not in latest or latest[key]['at'] <= row['at']:
                latest[key] = row
    return sorted(latest.values(), key=lambda row: (row['at'], row['run'], row['word']))


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
            'approvals': read_approvals(root, findings), 'findings': findings}


def _cmd_status(args):
    try:
        root, manifest = read_project(args.path)
        report, code = project_status(root, manifest), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


_TRANSITIONS = {'approve': ('draft', 'approved'), 'archive': ('done', 'archived'), 'unarchive': ('archived', 'done')}
_SPEC_TRANSITIONS = {'approve': ('draft', 'approved')}
_SATISFIED = {'specs': r'GO plan', 'plans': r'GO implement'}  # zgoda, którą daje `approve` danego pliku
_SHA = re.compile(r'^[0-9a-f]{40}$')


def git_blob_sha(data):
    """`git hash-object` of bytes (SHA-1 of the blob header and content); works outside a repository."""
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def _doc_file(root, name, folder='plans'):
    """Resolve `name` to a regular, non-symlink `docs/<folder>/*.md` file inside the project or raise ValueError."""
    kind = folder[:-1].capitalize()
    if not name or '\0' in name or '..' in Path(name).parts:
        raise ValueError(f'Invalid {kind.lower()} path: {name!r}')
    plans = root / 'docs' / folder
    for part in (root / 'docs', plans):
        if part.is_symlink():
            raise ValueError(f'Symlink in {kind.lower()} path: {part.relative_to(root)}')
    if not plans.is_dir():
        raise ValueError(f'Project has no docs/{folder} directory')
    given = Path(name).expanduser()
    candidate = given if given.is_absolute() else root / given
    if candidate.is_symlink():
        raise ValueError(f'{kind} is a symlink: {name}')
    resolved = candidate.resolve()
    if resolved.parent != plans.resolve() or resolved.suffix != '.md' or resolved.name.lower() == 'readme.md':
        raise ValueError(f'{kind} must be a docs/{folder}/*.md file (not README.md): {name}')
    if not resolved.is_file():
        raise ValueError(f'{kind} not found: {name}')
    if resolved.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError(f'{kind} over {MAX_PLAN_BYTES // 1024} KiB: {name}')
    return resolved


def _plan_file(root, name):
    return _doc_file(root, name, 'plans')


def set_doc_status(root, name, action, folder='plans', expect_sha=None):
    """Rewrite only the status word of the first Status line (suffix and markup kept), atomically. Returns the report.
    `expect_sha` (git blob sha of the file) refuses the change when the file differs from the version the user saw."""
    root = Path(root).resolve()
    source, target = (_TRANSITIONS if folder == 'plans' else _SPEC_TRANSITIONS)[action]
    kind = folder[:-1].capitalize()
    path = _doc_file(root, name, folder)
    raw = path.read_bytes()
    if expect_sha is not None:
        if not _SHA.match(expect_sha):
            raise ValueError('--expect-sha must be a 40-character lowercase git blob sha')
        if git_blob_sha(raw) != expect_sha:
            raise ValueError(f'{kind} changed since it was shown (sha mismatch); reload and approve again')
    text = raw.decode('utf-8')
    match = _STATUS.search(text[:4000])
    if not match:
        raise ValueError(f'{kind} has no Status line in the first 4000 characters')
    current = match.group('word').lower()
    if current != source:
        raise ValueError(f'Cannot {action} a {kind.lower()} with status {current!r}; expected {source!r}')
    start, end = match.span('word')
    line_end = text.find('\n', end)
    line_end = len(text) if line_end == -1 else line_end
    rest = text[end:line_end]
    if action == 'approve':
        # „czeka na <tę zgodę>” jest po zatwierdzeniu nieaktualne; inne dopiski (np. „czeka na spec”, „szkic”) zostają,
        # bo rozstrzyga je tabela wznowienia.
        consent = _SATISFIED[folder]
        rest = re.sub(r'[ \t]*\((?=[^()\n]*czeka na[^()\n]*' + consent + r')[^()\n]*\)', '', rest, flags=re.IGNORECASE)
    updated = (text[:start] + target + rest + text[line_end:]).encode('utf-8')
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
    report = {'schema': SCHEMA, 'status': 'PASS', 'reason': f'{kind} status {source} -> {target}', 'root': str(root),
              kind.lower(): path.relative_to(root).as_posix(), 'from': source, 'to': target, 'sha': git_blob_sha(updated)}
    report['warnings'] = _approve_warnings(root, path, updated.decode('utf-8'), folder) if action == 'approve' else []
    return report


def _approve_warnings(root, path, text, folder):
    """Pre-implement warning for the state after `approve` (a list, empty when nothing to warn about). Never raises."""
    try:
        if folder == 'plans':
            spec, plan_text = _plan_spec(text), text
        else:
            spec = path.relative_to(root).as_posix()
            plan = _spec_plan(text)
            plan_text = _read_text_safe(root / plan) if plan else None
        state = pre_implement_state(root, spec, plan_text, 'approved') if spec else None
    except (OSError, ValueError):
        return []
    return [state['warning']] if state and state['warning'] else []


def set_plan_status(root, name, action, expect_sha=None):
    return set_doc_status(root, name, action, 'plans', expect_sha)


def _cmd_doc(args):
    folder = 'specs' if args.doc_kind == 'spec' else 'plans'
    try:
        root, _ = read_project(args.path)
        report, code = set_doc_status(root, args.doc, args.doc_action, folder, args.expect_sha), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500],
                        'root': str(Path(args.path).expanduser().resolve())}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def _doc_status_word(root, name):
    folder = 'specs' if Path(name).parts[:2] == ('docs', 'specs') else 'plans'
    path = _doc_file(root, name, folder)
    match = _STATUS.search(path.read_text(encoding='utf-8', errors='replace')[:4000])
    return (match.group('word').lower() if match else None), git_blob_sha(path.read_bytes())


def find_approval(root, word, run=None, sha=None, since=None, doc=None):
    """One poll: a matching approvals.jsonl entry (word, optional run, sha, newer than `since`) or — for GO words with
    `doc` — the spec/plan already `approved` (button in the IDE or `spec|plan approve`). Returns the match or None."""
    since_at = _approval_at(since) if since else None
    for row in reversed(read_approvals(root)):
        if row['word'] != word or (run and row['run'] != run) or (sha and row['sha'] != sha):
            continue
        if since_at and _approval_at(row['at']) <= since_at:
            continue
        return {'kind': 'entry', **row}
    if doc and word in ('GO plan', 'GO implement'):
        status, file_sha = _doc_status_word(root, doc)
        if status in ('approved', 'in_progress', 'done', 'implemented'):
            return {'kind': 'status', 'word': word, 'file': doc, 'status': status, 'sha': file_sha}
    return None


def _cmd_approvals_wait(args):
    """Deterministic wait for Monitor / scripts: exits 0 with the match, 3 on timeout. Waking up is only a signal: the
    session still verifies the consent in the file (word, sha = its HEAD or the shown file version)."""
    root = Path(args.path).expanduser().resolve()
    deadline = time.monotonic() + max(0.0, args.timeout)
    try:
        read_project(args.path)
        if args.sha and not _APPROVAL_SHA.match(args.sha):
            raise ValueError('--sha must be 7-64 lowercase hex characters')
        while True:
            match = find_approval(root, args.word, args.run, args.sha, args.since, args.file)
            if match:
                report, code = {'schema': SCHEMA, 'status': 'PASS', 'approval': match}, 0
                break
            if time.monotonic() >= deadline:
                report, code = {'schema': SCHEMA, 'status': 'TIMEOUT', 'reason': f'No {args.word!r} approval yet'}, 3
                break
            time.sleep(min(max(0.2, args.interval), max(0.0, deadline - time.monotonic())) or 0.2)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def register(subparsers):
    parser = subparsers.add_parser('status', help='Read-only project status: plans, runs, claims (JSON)')
    add_project_arguments(parser)
    parser.set_defaults(func=_cmd_status)
    approvals = subparsers.add_parser('approvals', help='Consents from the status page (.skillsync/runs/<run-id>/approvals.jsonl)')
    approval_actions = approvals.add_subparsers(dest='approvals_action', required=True)
    wait = approval_actions.add_parser('wait', help='Wait until a consent appears (exit 0) or the timeout passes (exit 3); JSON')
    wait.add_argument('path')
    wait.add_argument('--word', required=True, choices=APPROVAL_WORDS)
    wait.add_argument('--run', help='run id (.skillsync/runs/<run-id>)')
    wait.add_argument('--sha', help='required sha: HEAD for CP/READY, shown file version for GO')
    wait.add_argument('--since', help='ISO time; older entries are ignored (default: any)')
    wait.add_argument('--file', help='GO only: docs/specs|plans/*.md whose status approved also ends the wait')
    wait.add_argument('--timeout', type=float, default=1800.0, help='seconds (default 1800 = 30 min)')
    wait.add_argument('--interval', type=float, default=5.0, help='poll interval in seconds (default 5)')
    add_layer_argument(wait)
    wait.add_argument('--json', action='store_true')
    wait.set_defaults(func=_cmd_approvals_wait)
    plan = subparsers.add_parser('plan', help='Plan file operations: approve / archive / unarchive (changes only the Status line)')
    spec = subparsers.add_parser('spec', help='Spec file operation: approve ("GO plan"; changes only the Status line)')
    for kind, parser_, transitions in (('plan', plan, _TRANSITIONS), ('spec', spec, _SPEC_TRANSITIONS)):
        actions = parser_.add_subparsers(dest='doc_action', required=True)
        for action, (source, target) in transitions.items():
            sub = actions.add_parser(action, help=f'Set Status {source} -> {target} in a docs/{kind}s/*.md file (JSON)')
            sub.add_argument('path')
            sub.add_argument('doc', metavar=kind, help=f'{kind.capitalize()} file (relative to the project or absolute) in docs/{kind}s')
            add_layer_argument(sub)
            sub.add_argument('--json', action='store_true')
            sub.add_argument('--expect-sha', help='git blob sha of the file as shown to the user; a different file is refused')
            sub.set_defaults(func=_cmd_doc, doc_kind=kind)
