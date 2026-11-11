"""Project status for IDE integrations (read-only: plans with progress, specs, FRD files, run checkpoints, claims) and the only write
operations, `plan approve|archive|unarchive` and `spec approve`, which rewrite a single Status line, and `approvals record`,
which appends one consent entry to `.skillsync/runs/<run-id>/approvals.jsonl`."""
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
    return {'schema': SCHEMA, 'status': 'PASS', 'reason': f'{kind} status {source} -> {target}', 'root': str(root),
            kind.lower(): path.relative_to(root).as_posix(), 'from': source, 'to': target, 'sha': git_blob_sha(updated)}


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


APPROVAL_SOURCES = ('cli', 'dashboard', 'ide')
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')  # the run-id pattern of `quality._RUN_ID` (a test keeps them equal)
_REF_NAME = re.compile(r'^(?!-)[\w.][\w./-]{0,120}$')
_GO_FOLDER = {'GO plan': 'specs', 'GO implement': 'plans'}
APPROVAL_LOCK_TIMEOUT = 10.0
APPROVAL_LOCK_STALE = 60.0


def _approval_user():
    import getpass
    try:
        return getpass.getuser() or 'unknown'
    except Exception:  # getpass raises OSError/KeyError/ImportError depending on the platform
        return 'unknown'


def _approval_run_dir(root, run_id):
    """`.skillsync/runs/<run-id>` as an existing directory without symlinks on the way, or ValueError."""
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError(f'Invalid run id: {run_id!r}')
    current = root
    for part in ('.skillsync', 'runs', run_id):
        current = current / part
        if current.is_symlink():
            raise ValueError(f'Symlink in the run path: {current.relative_to(root).as_posix()}')
        if not current.is_dir():
            raise ValueError(f'Run directory not found: {current.relative_to(root).as_posix()}')
    return current


def _head_sha(root):
    """HEAD of the project repository read from `.git` (no git process); ValueError without a readable HEAD."""
    from .dashboard import read_git  # lazy: dashboard imports this module
    head = read_git(root).get('headFull')
    if not head or not _APPROVAL_SHA.match(head):
        raise ValueError('No --sha and no readable HEAD of a git repository in the project')
    return head


class _ApprovalLock:
    """Inter-process lock as a `<file>.lock` created with O_EXCL (works on POSIX and Windows) holding a unique token.

    A lock older than APPROVAL_LOCK_STALE seconds is treated as left over by a crashed writer. It is taken over by an
    atomic `os.rename` to a unique name: of two waiters only one rename succeeds, and a waiter whose rename caught a fresh
    lock of another writer (created between its age check and the rename: other inode or mtime) puts it back with a
    hard link that never overwrites. `__exit__` removes the lock file only while it is still this lock (inode and token)."""

    def __init__(self, path, timeout=None):
        self.path, self.timeout = path, APPROVAL_LOCK_TIMEOUT if timeout is None else timeout
        self.token, self.inode = None, None

    def __enter__(self):
        import uuid
        deadline = time.monotonic() + self.timeout
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        token = f'{os.getpid()}-{uuid.uuid4().hex}'
        while True:
            try:
                fd = os.open(self.path, flags, 0o600)
            except FileExistsError:
                try:
                    seen = self.path.lstat()
                except FileNotFoundError:
                    continue
                if time.time() - seen.st_mtime > APPROVAL_LOCK_STALE:
                    self._take_over(seen)
                    continue
                if time.monotonic() >= deadline:
                    raise ValueError(f'Approvals file is locked by another writer: {self.path.name}') from None
                time.sleep(0.02)
                continue
            try:
                os.write(fd, token.encode('ascii'))
                self.inode = os.fstat(fd).st_ino
            finally:
                os.close(fd)
            self.token = token
            return self

    def _take_over(self, seen):
        """Removes the stale lock judged by `seen` (its lstat); never a lock created after that check."""
        import uuid
        grave = self.path.with_name(f'{self.path.name}.stale-{uuid.uuid4().hex}')
        try:
            os.rename(self.path, grave)
        except FileNotFoundError:
            return  # another waiter took it over first
        try:
            moved = grave.lstat()
            if (moved.st_ino, moved.st_mtime_ns) != (seen.st_ino, seen.st_mtime_ns):
                try:  # a fresh lock of another writer: back under its name, unless a newer lock exists already
                    os.link(grave, self.path)
                except FileExistsError:
                    pass
                except OSError:  # no hard links on this file system: best effort, never over an existing lock
                    if not self.path.exists():
                        os.rename(grave, self.path)
        finally:
            grave.unlink(missing_ok=True)

    def _owned(self):
        try:
            if self.path.lstat().st_ino != self.inode:
                return False
            return self.path.read_bytes()[:200] == (self.token or '').encode('ascii')
        except OSError:
            return False

    def __exit__(self, *exc):
        if self.token is not None and self._owned():
            self.path.unlink(missing_ok=True)
        self.token = None
        return False


def _approval_entries(path):
    """Parseable dict lines of an approvals file (for the duplicate check, before the read rules of `read_approvals`)."""
    rows = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def record_approval(root, word, run_id, sha=None, source='cli', target=None, branch=None, file=None, by=None, now=None,
                    plan=None, ahead=None, mode=None, sha_after=None):
    """The only writer of `.skillsync/runs/<run-id>/approvals.jsonl` (CLI `approvals record`, the dashboard server, IDE).
    Appends one JSON line under an inter-process lock and fsyncs it; refuses a second entry of the same (word, sha, run).
    `sha`: for GO the git blob sha of the approved file (default: of `file`), for CP/READY the HEAD the consent pushes
    (default: HEAD of the project repository). `sha_after` (GO only, `shaAfter` of the entry): the blob sha of the file after
    its Status line changed. Returns {schema, status PASS|BLOCKED, reason, entry, path}; no subprocess."""
    root = Path(root).expanduser().resolve()
    try:
        if word not in APPROVAL_WORDS:
            raise ValueError(f'Unknown consent word {word!r}; allowed: {", ".join(APPROVAL_WORDS)}')
        if source not in APPROVAL_SOURCES:
            raise ValueError(f'Unknown source {source!r}; allowed: {", ".join(APPROVAL_SOURCES)}')
        run_dir = _approval_run_dir(root, run_id)
        for name, value in (('target', target), ('branch', branch)):
            if value is not None and (not isinstance(value, str) or not _REF_NAME.match(value)):
                raise ValueError(f'Invalid --{name}: {value!r}')
        if file is not None:
            folder = _GO_FOLDER.get(word)
            if folder is None:
                raise ValueError(f'--file applies only to GO words, not to {word!r}')
            doc = _doc_file(root, file, folder)
            file = doc.relative_to(root).as_posix()
        if sha is None and file is None and word in _GO_FOLDER:
            raise ValueError(f'{word!r} needs --file or --sha (the git blob sha of the approved file, not HEAD)')
        if sha is None:
            sha = git_blob_sha(doc.read_bytes()) if file is not None else _head_sha(root)
        if not isinstance(sha, str) or not _APPROVAL_SHA.match(sha):
            raise ValueError('--sha must be 7-64 lowercase hex characters')
        if sha_after is not None and (word not in _GO_FOLDER or not isinstance(sha_after, str)
                                      or not _APPROVAL_SHA.match(sha_after)):
            raise ValueError('sha_after applies only to GO words and must be 7-64 lowercase hex characters')
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        entry = {'schema': SCHEMA, 'at': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'word': word, 'sha': sha,
                 'by': str(by if by is not None else _approval_user())[:80], 'source': source}
        extra = {'file': file, 'shaAfter': sha_after, 'run': run_id, 'plan': plan, 'target': target, 'branch': branch, 'ahead': ahead, 'mode': mode}
        entry.update({key: value for key, value in extra.items() if value is not None})
        entry = redact(entry)
        line = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
        path = run_dir / APPROVALS_FILE
        with _ApprovalLock(run_dir / (APPROVALS_FILE + '.lock')):
            if path.is_symlink():
                raise ValueError(f'Approvals file is a symlink: {APPROVALS_FILE}')
            size = path.stat().st_size if path.exists() else 0
            if size and not path.is_file():
                raise ValueError(f'Approvals path is not a regular file: {APPROVALS_FILE}')
            if size + len(line) + 1 > MAX_APPROVALS_BYTES:
                raise ValueError(f'Approvals file would exceed {MAX_APPROVALS_BYTES // 1024} KiB; start a new run')
            if size and any(item.get('word') == word and item.get('sha') == sha for item in _approval_entries(path)):
                raise ValueError(f'Consent {word!r} for sha {sha[:12]} is already recorded in run {run_id}')
            if size:
                with path.open('rb') as handle:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b'\n':
                        line = b'\n' + line  # a torn last line must not swallow the new entry
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_BINARY', 0), 0o644)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as error:
        return {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': redact(' '.join(str(error).split())[:500]), 'entry': None}
    return {'schema': SCHEMA, 'status': 'PASS', 'reason': f'Consent {word!r} recorded', 'entry': entry,
            'path': path.relative_to(root).as_posix()}


CLI_APPROVAL_SOURCES = ('cli', 'ide')  # `dashboard` is set only by the dashboard server, in its own process


def _cmd_approvals_record(args):
    try:
        if args.source not in CLI_APPROVAL_SOURCES:
            hint = '; dashboard is set only by the dashboard server in its own process' if args.source == 'dashboard' else ''
            raise ValueError(f'Unknown source {args.source!r}; the command accepts: {", ".join(CLI_APPROVAL_SOURCES)}{hint}')
        root, _ = read_project(args.path)
        report = record_approval(root, args.word, args.run, args.sha, args.source, args.target, args.branch, args.file)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500], 'entry': None}
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 2


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
    record = approval_actions.add_parser('record', help="Record the user's consent in approvals.jsonl (exit 0; 2 = refused); JSON")
    record.add_argument('path')
    record.add_argument('--word', required=True, help=f'one of: {", ".join(APPROVAL_WORDS)}')
    record.add_argument('--run', required=True, help='run id (.skillsync/runs/<run-id> must exist)')
    record.add_argument('--sha', help='GO: git blob sha of the approved file (default: of --file; GO needs --file or --sha); '
                                      'CP/READY: HEAD (default)')
    record.add_argument('--target', help='CP: branch the consent delivers to')
    record.add_argument('--branch', help='working branch')
    record.add_argument('--file', help='GO only: docs/specs/*.md (GO plan) or docs/plans/*.md (GO implement)')
    record.add_argument('--source', required=True,
                        help=f'one of: {", ".join(CLI_APPROVAL_SOURCES)} (dashboard is written only by the dashboard server)')
    add_layer_argument(record)
    record.add_argument('--json', action='store_true')
    record.set_defaults(func=_cmd_approvals_record)
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
