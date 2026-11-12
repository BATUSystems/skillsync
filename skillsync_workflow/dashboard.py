"""Dashboard engine: the project status page as a JSON contract (`schema: 1`, `docs/dashboard-contract.md`).

`build_status(root, plan=None)` reads (never writes) plans `docs/plans/*.md`, specs, `.skillsync/runs/<run-id>/`
(`checkpoints.md`, `approvals.jsonl`, `readiness.md`), prototypes and `.git` of the project and returns the dictionary the
status page renders. `skillsync dashboard show` prints it; `dashboard serve` (`dashboard_http.py`) serves it live. Shared
parsers come from `status.py` (status line, git blob sha, approval entry rules); phases and `Fn.k` sub-steps of `## Progress` are the dashboard's own extension (contract section
"Zgodność z `skillsync status`")."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess

import yaml

from . import __version__
from .common import add_project_arguments, read_project, redact
from .layers import add_layer_argument
from .delivery_mode import delivery_info
from .status import (APPROVAL_WORDS, APPROVALS_FILE, MAX_APPROVALS_BYTES, _APPROVAL_SHA, _approval_at, _status_fields,
                     git_blob_sha)

SCHEMA = 1
APP_NAME = 'dashboard'
PROTOTYPE_PORT = 5130
DEFAULT_PORT = 5140  # `dashboard serve`; pick another port per project to run several in parallel
PORT_MIN, PORT_MAX = 5100, 5199
FORBIDDEN_PORTS = frozenset({4444, 4445, 4446, 4447, 4448, 9222, 9333})
DOC_DIRS = ('docs/specs', 'docs/plans', 'docs/design', 'docs/domain')
DOC_LABELS = {'docs/specs': 'Speki', 'docs/plans': 'Plany', 'docs/design': 'Projekt', 'docs/domain': 'Domena'}
PROTOTYPES_DIR = 'docs/prototypes'
RUNS_DIR = '.skillsync/runs'
READINESS_FILE = 'readiness.md'
MAX_DOC_BYTES = 2_000_000
HEADER_BYTES = 16_000
SILENCE_MIN = 35  # progress report after every step or every 30 min; 35 min of silence on an in_progress plan = signal

STEP_ID = r'[A-Z]{1,3}-?\d+[a-z]?(?:\.\d+[a-z]?)?'  # S1, S2a, A-1 and phase sub-steps F1.2
STEP_RE = re.compile(rf'^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<id>{STEP_ID})(?![\w-])\s*(?P<rest>.*)$')
HEADER_RE = re.compile(r'^(?P<key>[^\s:#|>*`-][^:|]{0,40}):\s+(?P<val>\S.*?)\s*$')
CHECKPOINT_RE = re.compile(r'^\s*[-*]\s+(?P<time>\d{1,2}:\d{2})\b\s*(?P<text>.*)$')
IN_PROGRESS_RE = re.compile(r'w\s+toku\s*:?\s*(?P<seg>[^;]*)', re.IGNORECASE)
DONE_RE = re.compile(r'(?<![\w])zrobion[eya]\s*:?\s*(?P<seg>[^;]*)', re.IGNORECASE)
ID_LIST_RE = re.compile(rf'^\s*(?P<ids>{STEP_ID}(?:(?:\s*[/+&]\s*|\s+(?:i|oraz)\s+){STEP_ID})*)(?![\w-])')
# "The session waits for a human" in a checkpoint: segment `czeka: <what>` (segments are split by `;`, like
# "zrobione" / "w toku"). The newest checkpoint without `czeka:` clears the banner.
WAIT_RE = re.compile(r'(?:^|;)\s*czeka\s*:\s*(?P<seg>[^;]+)', re.IGNORECASE)
WAIT_NONE = {'', '-', '—', 'nic', 'brak', 'nie'}
STATUS_WAIT_RE = re.compile(r'czeka\s+na\s+(?P<what>[^);,]+)', re.IGNORECASE)
ID_FIND_RE = re.compile(rf'(?<![\w-]){STEP_ID}(?![\w-])')
SHA_RE = re.compile(r'^[0-9a-f]{7,40}$')
# Like the run pattern of quality.py: the first character is alphanumeric (no leading dot, so neither `.` nor `..` as a
# whole id) and `/` is not allowed; `..` inside an id (`a..b`) is not excluded. Use `fullmatch`/`run_dir`: `$` of `match`
# also accepts a trailing newline.
RUN_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
REF_NAME_RE = re.compile(r'^(?!-)[\w.][\w./-]{0,120}$')
PLAN_PR_RE = re.compile(r'^PR:\s*#(?P<num>\d+)\s*\(link:\s*(?P<url>https://[^\s)]+)\)\s*$', re.MULTILINE)
APPROVE_KINDS = {'spec': 'GO plan', 'plan': 'GO implement', 'cp': 'CP', 'ready': 'READY'}

PLAN_ROW_INTERNAL = ('mtime', 'complete', 'runAlive', 'activity')  # used to choose a plan, not part of the contract
STATUS_RANK = {'in_progress': 0, 'approved': 1, 'draft': 2, 'done': 3}  # done: the last closed one (time for CP/READY)
SPEC_DONE = {'approved', 'implemented', 'done', 'accepted'}
SPEC_OPEN = {'draft', 'approved'}  # a spec that may not have a plan yet (act I)
PLAN_APPROVED = {'approved', 'in_progress', 'done', 'implemented'}
RISKY_SECTIONS = ('Kontrakty', 'Migracja', 'Dane')

READINESS_RE = re.compile(r'^Readiness:\s*(?P<status>PASS|BLOCKED)\b', re.IGNORECASE)
READINESS_DATE_RE = re.compile(r'^Data:\s*(?P<val>\S.*?)\s*$', re.MULTILINE)
READINESS_SHA_RE = re.compile(r'^Plan-sha:\s*`?(?P<val>[0-9a-fA-F]{7,64})`?\s*$', re.MULTILINE)
QUESTIONS_HEADING = re.compile(r'^##\s+Pytania otwarte\b', re.IGNORECASE)
QUESTION_RE = re.compile(r'^\s*[-*]\s+(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?(?:\s*\[(?P<tag>[^\]]*)\])?\s*:\s*(?P<body>\S.*?)\s*$')
TABLE_QUESTION_RE = re.compile(r'^\s*\|\s*(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?\s*\|(?P<rest>.*)$')
ACCEPTED_RE = re.compile(r'\s+(?:—|–|--|-)\s+przyj[eę]te\s*:\s*', re.IGNORECASE)
ASSUMPTION_TAGS = {'założenie', 'zalozenie'}

REMOTE_PATTERNS = (
    # Azure DevOps: git@ssh.dev.azure.com:v3/org/project/repo, https://[user@]dev.azure.com/org/project/_git/repo
    (re.compile(r'^(?:ssh://)?git@ssh\.dev\.azure\.com[:/]v3/(?P<org>[^/]+)/(?P<project>[^/]+)/(?P<repo>[^/]+?)/?$'), 'azure'),
    (re.compile(r'^https://(?:[^@/]+@)?dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/?#]+?)/?$'), 'azure'),
    # GitHub: git@github.com:org/repo(.git), https://github.com/org/repo(.git)
    (re.compile(r'^(?:ssh://)?git@github\.com[:/](?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$'), 'github'),
    (re.compile(r'^https://(?:[^@/]+@)?github\.com/(?P<org>[^/]+)/(?P<repo>[^/?#]+?)(?:\.git)?/?$'), 'github'),
)
SAFE_SEG = re.compile(r'^[\w.~%-]+$')


class _AnyStatus:
    """`allowed` for `status._status_fields` that keeps every status word (the dashboard shows the word as written)."""

    def __contains__(self, item):
        return True


ANY_STATUS = _AnyStatus()


# --------------------------------------------------------------------------- parsing

def read_text(path, limit=MAX_DOC_BYTES):
    with open(path, 'rb') as handle:
        return handle.read(limit).decode('utf-8', errors='replace')


def clean_value(value):
    if value is None:
        return None
    return value.strip().strip('`*_ ').strip() or None


def first_token(value):
    value = clean_value(value)
    if not value:
        return None
    return value.split()[0].strip('`*_,;()') or None


def doc_status(text):
    """(status, statusText) of a plan/spec/document: the shared `status._status_fields` parser; no status line = (None, None)."""
    status, status_text = _status_fields(text, ANY_STATUS)
    return (None if status == 'unknown' else status), status_text


def parse_header(text):
    """Title (first `# `) and `Key: value` fields before the first `## `."""
    title, fields = None, {}
    for line in text.splitlines():
        if line.startswith('## '):
            break
        if title is None and line.startswith('# '):
            title = line[2:].strip()
            continue
        match = HEADER_RE.match(line)
        if match:
            fields.setdefault(match.group('key').strip().lower(), match.group('val').strip())
    return title, fields


def parse_progress(text):
    """`## Progress`: phases `### …` and steps `- [ ] ID Title` / `- [x] ID Title — sha`; code blocks are skipped."""
    phases, current, in_progress, in_fence = [], None, False, False
    for line in text.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith('## '):
            if in_progress:
                break
            in_progress = line[3:].strip().lower().startswith('progress')
            continue
        if not in_progress:
            continue
        if line.startswith('### '):
            current = {'name': line[4:].strip(), 'steps': []}
            phases.append(current)
            continue
        match = STEP_RE.match(line)
        if not match:
            continue
        if current is None:
            current = {'name': 'Kroki', 'steps': []}
            phases.append(current)
        rest = match.group('rest').strip()
        title, note = rest, ''
        for sep in (' — ', ' -- ', ' – '):
            if sep in rest:
                title, note = rest.split(sep, 1)
                break
        token = note.strip().split()[0].strip(',;()') if note.strip() else ''
        current['steps'].append({'id': match.group('id'), 'title': title.strip(), 'done': match.group('mark') != ' ',
                                 'note': note.strip(), 'sha': token if SHA_RE.match(token) else None})
    return [phase for phase in phases if phase['steps']]


def parse_checkpoints(text):
    """`RUN=<iso>` and `- HH:MM text` lines (indented lines continue the previous one)."""
    run_start, items = None, []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('RUN='):
            run_start = stripped[4:].strip()
            continue
        match = CHECKPOINT_RE.match(line)
        if match:
            items.append({'time': match.group('time'), 'text': match.group('text').strip()})
        elif items and stripped and line[:1] in (' ', '\t'):
            items[-1]['text'] += ' ' + stripped
    return run_start, items


def wait_marker(text):
    """Value of the `czeka:` segment of a checkpoint text, or None."""
    match = WAIT_RE.search(text)
    if not match:
        return None
    value = match.group('seg').strip().rstrip('.')
    return None if value.lower() in WAIT_NONE else value


def wait_item(what, source, since=None, ctx=None):
    """Banner entry: what the session waits for, what to do and where the link leads (`doc` or `prototype`)."""
    ctx = ctx or {}
    low = what.lower()
    item = {'what': what, 'source': source, 'since': since, 'action': f'Sesja czeka: {what}. Odpowiedz w sesji.', 'link': None}
    if 'prototyp' in low:
        match = re.search(r'prototyp\w*\s+(?P<slug>[A-Za-z0-9][\w.-]*)', what, re.IGNORECASE)
        slug = match.group('slug') if match else ctx.get('slug')
        item.update(action='Otwórz prototyp, przejrzyj ekrany i zaakceptuj je (albo zostaw uwagi).',
                    link={'kind': 'prototype', 'slug': slug} if slug else None)
    elif 'go plan' in low:
        item.update(action='Przeczytaj spec i zatwierdź „GO plan” przyciskiem albo wpisz to w sesji.',
                    link=ctx.get('spec') and {'kind': 'doc', 'path': ctx['spec']}, word='GO plan', approve='spec')
    elif 'go implement' in low:
        item.update(action='Przeczytaj plan i zatwierdź „GO implement” przyciskiem albo wpisz to w sesji '
                           '(przy planie direct wystarcza „GO”).',
                    link=ctx.get('plan') and {'kind': 'doc', 'path': ctx['plan']}, word='GO implement', approve='plan')
    elif re.match(r'(odpowied\w*|pytani\w*)?\s*Q\d', what, re.IGNORECASE) or 'pytani' in low or 'odpowied' in low:
        item.update(action=f'Odpowiedz w sesji na pytania ({what}).', link=ctx.get('spec') and {'kind': 'doc', 'path': ctx['spec']})
    elif re.fullmatch(r'(CP|CPD)!?', what.strip(), re.IGNORECASE):
        item.update(action='Sprawdź wynik i zatwierdź „CP” przyciskiem w Akcie III albo wpisz to w sesji.',
                    word=what.strip().upper(),
                    approve='cp' if what.strip().upper().startswith('CP') and 'D' not in what.upper() else None)
    elif re.fullmatch(r'READY!?', what.strip(), re.IGNORECASE):
        item.update(action='Sprawdź draft PR i zatwierdź „READY” przyciskiem w Akcie III albo wpisz to w sesji.',
                    word=what.strip().upper(), approve='ready')
    elif re.search(r'\bTAK\b.*\bNIE\b|\bP\d+\b', what):
        item.update(action=f'Rozstrzygnij propozycję retrospektywy: wpisz „TAK …” albo „NIE …” ({what}).')
    return item


def split_top_level(segment, sep=','):
    parts, depth, buf = [], 0, []
    for char in segment:
        if char in '([{':
            depth += 1
        elif char in ')]}' and depth:
            depth -= 1
        if char == sep and depth == 0:
            parts.append(''.join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append(''.join(buf))
    return parts


def _lead_ids(segment):
    ids = []
    for item in split_top_level(segment):
        lead = ID_LIST_RE.match(item)
        if lead:
            for sid in ID_FIND_RE.findall(lead.group('ids')):
                if sid not in ids:
                    ids.append(sid)
    return ids


def ids_reported_done(text):
    """Ids after "zrobione" (up to `;`): a visual hint only, it does not change the percentage."""
    match = DONE_RE.search(text)
    return _lead_ids(match.group('seg')) if match else []


def ids_in_progress(text):
    """Step ids after "w toku" up to the next `;`, only items that start with an id; None when the phrase is absent."""
    match = IN_PROGRESS_RE.search(text)
    return _lead_ids(match.group('seg')) if match else None


def parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def parse_questions(text):
    """(assumptions, blocking) from the spec's `## Pytania otwarte` section (contract: `assumptions`, `blockingQuestions`).

    `- Q<n> [ZAŁOŻENIE]: <question> — przyjęte: <value>` (em/en dash or hyphen) is an assumption; `- Q<n>: <question>` and
    a table row `| Q<n> | <question> | …` are blocking; other bracket tags (e.g. `[ROZSTRZYGNIĘTE …]`) are skipped."""
    assumptions, blocking, seen = [], [], set()
    inside = in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith('## '):
            if inside:
                break
            inside = bool(QUESTIONS_HEADING.match(line))
            continue
        if not inside:
            continue
        match = QUESTION_RE.match(line)
        if match:
            qid, tag, body = match.group('id'), match.group('tag'), match.group('body')
            if qid in seen:
                continue
            if tag is None:
                seen.add(qid)
                blocking.append({'id': qid, 'question': body})
            elif tag.strip().lower() in ASSUMPTION_TAGS:
                seen.add(qid)
                parts = ACCEPTED_RE.split(body, 1)
                question, accepted = (parts[0], parts[1]) if len(parts) == 2 else (body, '')
                assumptions.append({'id': qid, 'question': question.strip(), 'accepted': accepted.strip()})
            continue
        row = TABLE_QUESTION_RE.match(line)
        if row and row.group('id') not in seen:
            cells = [cell.strip() for cell in row.group('rest').split('|')]
            question = next((cell for cell in cells if cell), '')
            if question:
                seen.add(row.group('id'))
                blocking.append({'id': row.group('id'), 'question': question})
    return assumptions, blocking


# --------------------------------------------------------------------------- paths

def _posix_rel(root, path):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def is_allowed_rel(rel):
    """Allowlist of a relative path (no disk access)."""
    if not rel or '\x00' in rel or '\\' in rel or rel.startswith('/') or not rel.endswith('.md'):
        return False
    parts = rel.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return False
    if re.fullmatch(r'\.skillsync/runs/[^/.][^/]*/[^/.][^/]*\.md', rel):
        return True
    if any(part.startswith('.') for part in parts):
        return False
    return any(rel.startswith(directory + '/') for directory in DOC_DIRS)


def resolve_doc(root, rel):
    """An allowlisted `*.md` file under the project; resists `..`, absolute paths and symlinks leading outside."""
    if not rel or not is_allowed_rel(rel):
        return None
    root_real = Path(root).resolve()
    try:
        target = (root_real / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    real_rel = _posix_rel(root_real, target)
    if real_rel is None or not is_allowed_rel(real_rel) or not target.is_file():
        return None
    return target


def _mtime(path):
    try:
        return path.stat().st_mtime
    except (OSError, TypeError, AttributeError):
        return None


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec='seconds') if timestamp else None


def file_sha(path):
    try:
        return git_blob_sha(path.read_bytes()) if path else None
    except OSError:
        return None


# --------------------------------------------------------------------------- project documents

def list_plans(root):
    plans = []
    for path in sorted((root / 'docs/plans').glob('*.md')):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        try:
            text = read_text(path, HEADER_BYTES)
        except OSError:
            continue
        title, fields = parse_header(text)
        run = first_token(fields.get('przebieg'))
        mtime = _mtime(path) or 0
        alive, activity = False, mtime
        try:
            steps = [step for phase in parse_progress(read_text(path)) for step in phase['steps']]
        except OSError:
            steps = []
        complete = bool(steps) and all(step['done'] for step in steps)
        directory = run_dir(root, run)
        if directory is not None:
            alive = True
            activity = max([activity] + [stamp for stamp in (_mtime(directory / name) for name in ('checkpoints.md', 'approvals.jsonl'))
                                         if stamp])
        plans.append({'path': rel, 'title': title or path.stem, 'status': doc_status(text)[0],
                      'spec': first_token(fields.get('spec')), 'run': run, 'mtime': mtime,
                      'complete': complete, 'runAlive': alive, 'activity': activity})
    return plans


def list_specs(root):
    specs = []
    for path in sorted((root / 'docs/specs').glob('*.md')):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        try:
            text = read_text(path, HEADER_BYTES)
        except OSError:
            continue
        title, _ = parse_header(text)
        specs.append({'path': rel, 'title': title or path.stem, 'status': doc_status(text)[0], 'mtime': _mtime(path) or 0})
    return specs


def _plan_rank(plan):
    """`STATUS_RANK`, except an orphan: an `in_progress`/`approved` plan whose steps are all checked and whose run directory
    does not exist is finished work nobody closed, so it ranks like `done` and cannot hide a plan that is being worked on."""
    rank = STATUS_RANK[plan['status']]
    if plan['status'] in ('in_progress', 'approved') and plan.get('complete') and not plan.get('runAlive'):
        return STATUS_RANK['done']
    return rank


def choose_plan(plans):
    """The plan being worked on: lowest rank (`in_progress`, `approved`, `draft`, last `done`; an orphan counts as `done`),
    then a plan with an existing run first, then the freshest activity (run `checkpoints.md`/`approvals.jsonl`, plan mtime),
    then the dated name and mtime (the run and the activity count only for the open ranks, never for `done` or an orphan)."""
    candidates = [plan for plan in plans if plan['status'] in STATUS_RANK]
    if not candidates:
        return None
    newest_first = sorted(candidates, key=lambda plan: (plan['path'], plan['mtime']), reverse=True)
    def key(plan):
        rank = _plan_rank(plan)
        if rank >= STATUS_RANK['done'] or not plan.get('runAlive'):
            return (rank, 1, 0)       # closed work and plans without a run keep the dated-name order
        return (rank, 0, -plan.get('activity', 0))
    return sorted(newest_first, key=key)[0]


def choose_spec_act(plans, specs, chosen):
    """Spec of act I: the newest `draft`/`approved` spec no plan points to (`Spec:`), newer than the auto-chosen plan.
    `feature` creates the plan only at "GO plan", so without this act I would be invisible."""
    referenced = {plan['spec'] for plan in plans if plan.get('spec')}
    candidates = [spec for spec in specs if spec['status'] in SPEC_OPEN and spec['path'] not in referenced]
    if not candidates:
        return None
    newest = max(candidates, key=lambda spec: (spec['mtime'], spec['path']))
    if chosen and chosen['mtime'] >= newest['mtime']:
        return None
    return newest


def runs_root(root):
    """`.skillsync/runs` when it is a directory and neither it nor `.skillsync` is a symlink (the rule of `build_metrics`
    and `status.record_approval`); otherwise None, and the dashboard reads nothing from runs."""
    runs = root / RUNS_DIR
    if (root / '.skillsync').is_symlink() or runs.is_symlink() or not runs.is_dir():
        return None
    return runs


def run_dir(root, run_id):
    """`.skillsync/runs/<run-id>` as an existing directory with no symlink on the way, or None."""
    runs = runs_root(root)
    if runs is None or not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        return None
    path = runs / run_id
    return path if path.is_dir() and not path.is_symlink() else None


def choose_run_act(root, plans, specs, chosen):
    """Act I before the spec: the newest run directory with `checkpoints.md` newer than the auto-chosen plan that no plan
    points to (`Przebieg:`) and that matches no spec (`<date>-<spec slug>`)."""
    runs = runs_root(root)
    if runs is None:
        return None
    taken = {plan['run'] for plan in plans if plan.get('run')}
    slugs = [posixpath.splitext(posixpath.basename(spec['path']))[0] for spec in specs]
    newest = None
    for directory in runs.iterdir():
        name = directory.name
        if not directory.is_dir() or directory.is_symlink() or not RUN_ID_RE.match(name) or name in taken:
            continue
        if any(name == slug or name.endswith('-' + slug) for slug in slugs):
            continue
        mtime = _mtime(directory / 'checkpoints.md')
        if mtime is None or (chosen and chosen['mtime'] >= mtime):
            continue
        if newest is None or (mtime, name) > newest:
            newest = (mtime, name)
    return newest[1] if newest else None


def spec_run_id(root, spec_rel, fields):
    """Run of a spec without a plan: the spec's `Przebieg:` line, then the directory named after the `Plan:` line, then the
    newest `<date>-<spec slug>` (or `<spec slug>`) directory."""
    runs = runs_root(root)
    if runs is None:
        return None

    def ok(name):
        return run_dir(root, name) is not None

    explicit = first_token(fields.get('przebieg'))
    if ok(explicit):
        return explicit
    plan = first_token(fields.get('plan'))
    if plan and ok(posixpath.splitext(posixpath.basename(plan))[0]):
        return posixpath.splitext(posixpath.basename(plan))[0]
    stem = posixpath.splitext(posixpath.basename(spec_rel))[0]
    names = sorted(directory.name for directory in runs.iterdir()
                   if directory.is_dir() and not directory.is_symlink() and RUN_ID_RE.fullmatch(directory.name)
                   and (directory.name == stem or directory.name.endswith('-' + stem)))
    return names[-1] if names else None


def prototypes_root(root):
    """`docs/prototypes` as a directory without a symlink on the way, or None."""
    base = root / PROTOTYPES_DIR
    if base.parent.is_symlink() or base.is_symlink() or not base.is_dir():
        return None
    return base


def find_prototype(root, spec_rel):
    """Prototype directory whose `prototype.json` has `requirements` equal to the spec path; symlinks on the way
    (`docs`, `docs/prototypes`, the prototype directory, `prototype.json`) are skipped."""
    base = prototypes_root(root)
    if not spec_rel or base is None:
        return None
    for meta in sorted(base.glob('*/prototype.json')):
        if meta.parent.is_symlink() or meta.is_symlink() or not meta.is_file():
            continue
        try:
            data = json.loads(read_text(meta, 64_000))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('requirements') == spec_rel:
            return f'{PROTOTYPES_DIR}/{meta.parent.name}'
    return None


def prototype_acceptance(root, slug):
    """`{screens, accepted, stale}` of a prototype (in-process, read-only, the same function `prototype list` uses), or None."""
    if not slug or not re.fullmatch(r'[A-Za-z0-9][\w.-]*', slug):
        return None
    base = prototypes_root(root)
    directory = base / slug if base is not None else None
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return None
    try:
        from .prototype import prototype_comments_info
        acceptance = prototype_comments_info(directory, slug)[0].get('acceptance')
    except Exception:  # an unreadable prototype must not break the status page
        return None
    if not isinstance(acceptance, dict):
        return None
    return {key: int(acceptance.get(key) or 0) for key in ('screens', 'accepted', 'stale')}


def risky_sections(text):
    """Spec sections `## Kontrakty|Migracja|Dane` with content (not "nie dotyczy"): pre-implement is needed before GO plan."""
    found = []
    for name in RISKY_SECTIONS:
        match = re.search(rf'^##\s+{name}\b[^\n]*\n(?P<body>.*?)(?=^#{{1,6}}\s|\Z)', text, re.MULTILINE | re.DOTALL)
        body = (match.group('body') if match else '').strip()
        if body and not re.match(r'^[-*\s]*nie dotyczy', body, re.IGNORECASE):
            found.append(name)
    return found


# --------------------------------------------------------------------------- git and delivery

def remote_web(url):
    """Web address of the repository from the `origin` URL (Azure DevOps or GitHub); another host = None (no link)."""
    url = (url or '').strip()
    for regex, host in REMOTE_PATTERNS:
        match = regex.match(url)
        if not match:
            continue
        parts = match.groupdict()
        if not all(SAFE_SEG.match(value) for value in parts.values()):
            return None
        if host == 'azure':
            base = f"https://dev.azure.com/{parts['org']}/{parts['project']}/_git/{parts['repo']}"
            return {'host': 'azure', 'repo': base, 'branchUrl': base + '?version=GB{branch}'}
        base = f"https://github.com/{parts['org']}/{parts['repo']}"
        return {'host': 'github', 'repo': base, 'branchUrl': base + '/tree/{branch}'}
    return None


def _origin_url(common):
    """`url` of the `[remote "origin"]` section of the repository config (without running git)."""
    try:
        text = read_text(common / 'config', 64_000)
    except OSError:
        return None
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            section = stripped
            continue
        if section == '[remote "origin"]' and stripped.lower().startswith('url'):
            key, _, value = stripped.partition('=')
            if key.strip().lower() == 'url':
                return value.strip()
    return None


def _has_ref(common, ref):
    if (common / ref).is_file():
        return True
    packed = common / 'packed-refs'
    try:
        return packed.is_file() and any(line.endswith(' ' + ref) for line in read_text(packed).splitlines())
    except OSError:
        return False


def read_git(root):
    """Branch, HEAD and origin of the project repository read from `.git` files (git is not run)."""
    info = {'branch': None, 'head': None, 'headFull': None, 'remote': None, 'branchUrl': None, 'branchPushed': None}
    git = root / '.git'
    try:
        if git.is_file():
            match = re.match(r'gitdir:\s*(.+)', read_text(git, 4096).strip())
            if not match:
                return info
            git = (root / match.group(1).strip()).resolve()
        head = read_text(git / 'HEAD', 4096).strip()
        common = git
        if (git / 'commondir').is_file():
            common = (git / read_text(git / 'commondir', 4096).strip()).resolve()
        if head.startswith('ref:'):
            ref = head[4:].strip()
            info['branch'] = ref.removeprefix('refs/heads/')
            for base in (git, common):
                if (base / ref).is_file():
                    info['headFull'] = read_text(base / ref, 128).strip()
                    info['head'] = info['headFull'][:12]
                    break
            else:
                packed = common / 'packed-refs'
                if packed.is_file():
                    for line in read_text(packed).splitlines():
                        if line.endswith(' ' + ref):
                            info['headFull'] = line.split()[0]
                            info['head'] = info['headFull'][:12]
        elif SHA_RE.match(head):
            info['headFull'], info['head'] = head, head[:12]
        web = remote_web(_origin_url(common))
        if web:
            from urllib.parse import quote
            info['remote'] = {'host': web['host'], 'repo': web['repo']}
            if info['branch']:
                info['branchPushed'] = _has_ref(common, f"refs/remotes/origin/{info['branch']}")
                safe = '' if web['host'] == 'azure' else '/'  # Azure encodes "/" in version=GB…
                info['branchUrl'] = web['branchUrl'].replace('{branch}', quote(info['branch'], safe=safe))
    except OSError:
        pass
    return info


def read_delivery(manifest):
    """`{mode, base, source}` of the manifest through the shared `delivery_mode.delivery_info` (the same value `route`
    reports); a validation error becomes mode `invalid` (source `manifest`), as the status page always had it."""
    info = delivery_info(manifest)
    if info['source'] == 'invalid':
        return {'mode': 'invalid', 'base': None, 'source': 'manifest'}
    base = info.get('base')
    return {'mode': info['mode'], 'base': base if isinstance(base, str) and REF_NAME_RE.match(base) else None,
            'source': info['source']}


def git_ahead(root, branch):
    """How many commits HEAD has that the remote counterpart of the working branch has not (read-only): `origin/<branch>..HEAD`,
    or `HEAD --not --remotes=origin` when the branch is not on origin. "CP" pushes the working branch, not the base."""
    def git(*args):
        try:
            out = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None
    if not branch or not REF_NAME_RE.match(branch):
        return {'remote': None, 'pushed': None, 'ahead': None}
    remote = f'origin/{branch}'
    if git('rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}^{{commit}}'):
        count, pushed = git('rev-list', '--count', f'{remote}..HEAD'), True
    else:
        count, pushed = git('rev-list', '--count', 'HEAD', '--not', '--remotes=origin'), False
    return {'remote': remote, 'pushed': pushed, 'ahead': int(count) if count and count.isdigit() else None}


def delivery_target(delivery, branch, plan_fields):
    """Where "CP" delivers: the working branch; in `direct` mode the plan line `Oddanie: <branch>` (the user's answer)."""
    handover = first_token(plan_fields.get('oddanie'))
    if delivery.get('mode') == 'direct' and handover and REF_NAME_RE.match(handover):
        return handover
    return branch


def _commits(count):
    plural = 'commity' if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else 'commitów'
    return f"{count} {'commit' if count == 1 else plural}"


def delivery_words(delivery, ahead, plan, waiting, branch=None, target=None):
    """Delivery words of act III: `CP` always, `READY` only in `pr` mode. Active when the plan is `done`, the session waits
    for the word or the working branch has commits origin does not have; `tip` says where and how many."""
    if delivery.get('mode') == 'invalid':
        return []
    waits = {(item.get('word') or '').rstrip('!') for item in waiting}
    done = bool(plan) and plan.get('status') == 'done'
    count = ahead.get('ahead')
    target = target or branch
    where = f'origin/{target}' if target else 'origin'
    if target and branch and target != branch:
        where += f' (oddanie z {branch})'
    out = []
    for word in ['CP'] + (['READY'] if delivery.get('mode') == 'pr' else []):
        reasons = []
        if word in waits:
            reasons.append(f'sesja czeka na „{word}”')
        if done:
            reasons.append('plan done')
        if count:
            remote = ahead.get('remote') if ahead.get('pushed') else 'origin (gałęzi jeszcze nie ma na origin)'
            reasons.append(f'{_commits(count)} niewypchniętych względem {remote}')
        tip = '; '.join(reasons) if reasons else ('wszystko wypchnięte' if count == 0 else 'nic do dostarczenia')
        if word == 'CP':
            tip = f'CP → {where}; {tip}'
        out.append({'word': word, 'active': bool(reasons), 'tip': tip, 'target': target if word == 'CP' else None})
    return out


# --------------------------------------------------------------------------- approvals

def run_approvals(root, run_id):
    """Entries of `.skillsync/runs/<run-id>/approvals.jsonl` in file order, as written (the contract shows the entry in
    `given` and `last`). The acceptance rules are those of `status.read_approvals` (known word, parseable `at`, hex sha,
    size limit); `status` aggregates the newest entry per (run, word) without `by`/`schema`, so the reader is not shared."""
    directory = run_dir(root, run_id)
    if directory is None:
        return []
    path = directory / APPROVALS_FILE
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_APPROVALS_BYTES:
            return []
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (isinstance(item, dict) and item.get('word') in APPROVAL_WORDS and _approval_at(item.get('at')) is not None
                and _APPROVAL_SHA.match(str(item.get('sha') or ''))):
            out.append(item)
    return out


def _hhmm(entry):
    try:
        return datetime.fromisoformat(str(entry['at']).replace('Z', '+00:00')).astimezone().strftime('%H:%M')
    except (KeyError, TypeError, ValueError):
        return ''


def _given_tip(word, entry):
    at = _hhmm(entry)
    return f'zgoda „{word}” dana {at}'.rstrip() if at else f'zgoda „{word}” już dana'


def consent_buttons(spec, plan, waiting, words, log, head_full, run_ok):
    """Consent buttons of the act tiles, each in one of the states: before (the act has not come) = no entry; `waiting` =
    visible and active (the session waits for this consent); `running` = visible, inactive, `reason` "czeka na…";
    `after` = visible, inactive, `reason` "zgoda dana HH:MM" (already given; no second entry). The "waiting" signal is
    written by the session: `czeka na GO plan|GO implement` in the spec/plan status or `czeka:` in a checkpoint."""
    out = []
    waits = {(item.get('word') or '').rstrip('!').upper() for item in waiting}
    last = {entry.get('word'): entry for entry in log}
    acc = spec.get('prototypeAcceptance') or {}
    proto_open = bool(acc.get('screens')) and (acc.get('accepted', 0) < acc['screens'] or acc.get('stale'))
    proto_reason = f"czeka na akceptację prototypu ({acc.get('accepted', 0)}/{acc.get('screens', 0)})"
    plan_status = (plan or {}).get('status')
    plan_text = ((plan or {}).get('statusText') or '').lower()
    sketch = bool(plan) and ('szkic' in plan_text or 'czeka na spec' in plan_text)
    spec_text = (spec.get('statusText') or '').lower()

    def add(kind, state, reason, path, sha, given=None):
        out.append({'kind': kind, 'word': APPROVE_KINDS[kind], 'state': state, 'reason': reason, 'path': path,
                    'sha': sha or '', 'given': given, 'blocked': None if state == 'waiting' else reason})

    # Act I -> "GO plan": only with a spec.
    if spec.get('exists'):
        if spec.get('status') in SPEC_DONE:
            add('spec', 'after', _given_tip('GO plan', last.get('GO plan')), spec['path'], spec.get('fileSha'),
                last.get('GO plan') or {'at': None})
        elif spec.get('status') == 'draft' and (not plan or sketch):
            signal = 'GO PLAN' in waits or 'czeka na go plan' in spec_text or 'czeka na go plan' in plan_text
            reason = (proto_reason if proto_open
                      else None if signal else 'czeka na koniec pętli spec (sesja zgłosi „czeka: GO plan”)')
            add('spec', 'waiting' if reason is None else 'running', reason, spec['path'], spec.get('fileSha'))
    # Act II -> "GO implement": a plan other than a sketch.
    if plan and not sketch:
        if plan_status in PLAN_APPROVED:
            add('plan', 'after', _given_tip('GO implement', last.get('GO implement')), plan['path'], plan.get('fileSha'),
                last.get('GO implement') or {'at': None})
        elif plan_status == 'draft':
            signal = 'GO IMPLEMENT' in waits or 'czeka na go implement' in plan_text
            reason = ('czeka na „GO plan” (spec nie jest approved)' if spec.get('exists') and spec.get('status') != 'approved'
                      else proto_reason if proto_open
                      else 'czeka na koniec aktu II (pętla plan, readiness, wersja)' if not signal
                      else None)
            add('plan', 'waiting' if reason is None else 'running', reason, plan['path'], plan.get('fileSha'))
    # Act III -> "CP" / "READY": from the start of the implementation (plan in_progress or done).
    if plan_status in ('in_progress', 'done'):
        for word in words:
            kind = word['word'].lower()
            entry = last.get(word['word'])
            if entry and head_full and entry.get('sha') == head_full:
                add(kind, 'after', _given_tip(word['word'], entry) + f' (HEAD {head_full[:8]})', None, head_full, entry)
                out[-1]['target'] = entry.get('target') or word.get('target')
                continue
            reason = None if word['active'] else f"czeka na koniec realizacji ({word['tip']})"
            if reason is None and not head_full:
                reason = 'brak HEAD repozytorium'
            elif reason is None and not run_ok:
                reason = 'brak katalogu przebiegu (.skillsync/runs/<run-id>) dla wpisu zgody'
            add(kind, 'waiting' if reason is None else 'running', reason or word['tip'], None, head_full)
            out[-1]['reason'] = reason if reason else word['tip']
            out[-1]['target'] = word.get('target')
    return out


def approvals_state(root, spec, plan, delivery, run_id, head_full, log, waiting, enabled=True):
    """`approvals {enabled, actions[], last}`; every action carries `run` (the run directory the consent is written to, or
    null when it does not exist); `path` of `cp`/`ready` is the run id."""
    if not enabled:
        return {'enabled': False, 'actions': [], 'last': log[-1] if log else None}
    run_ok = run_dir(root, run_id) is not None
    actions = consent_buttons(spec, plan, waiting, delivery.get('words', []), log, head_full, run_ok)
    for action in actions:
        if action['kind'] in ('cp', 'ready'):
            action['path'] = run_id
        action['run'] = run_id if run_ok else None
    return {'enabled': True, 'actions': actions, 'last': log[-1] if log else None}


# --------------------------------------------------------------------------- model

def _stage(label, done, tip='', key=None):
    """Stage tile; `key` = description key in the skill's `descriptions.json`."""
    return {'label': label, 'done': done, 'tip': tip, 'key': key}


def compute_acts(spec, plan, progress, run_files, extra=None):
    extra = extra or {}
    names = [name.rsplit('/', 1)[-1].lower() for name in run_files]
    spec_reviews = sum(1 for name in names if 'spec-review' in name)
    plan_reviews = sum(1 for name in names if 'plan-review' in name)
    pre_impl = any('pre-implement' in name for name in names)
    proto_rounds = sum(1 for name in names if 'prototyp' in name)

    spec_status = spec.get('status')
    if spec.get('preparing'):
        spec_state = 'active'
    elif not spec.get('exists'):
        spec_state = 'planned'
    elif spec_status in SPEC_DONE:
        spec_state = 'done'
    else:
        spec_state = 'active'

    plan_status = plan.get('status') if plan else None
    if plan is None:
        plan_state = 'active' if spec_state == 'done' else 'planned'
    elif plan_status in PLAN_APPROVED:
        plan_state = 'done'
    else:
        plan_state = 'active'
    real_state = 'done' if plan_status == 'done' else 'active' if plan_status == 'in_progress' else 'planned'

    spec_stages = [
        _stage('Szkic', spec.get('exists', False), spec.get('path') or 'brak speca', key='stage_spec_draft'),
        _stage(f'Review ×{spec_reviews}' if spec_reviews > 1 else 'Review', spec_reviews > 0,
               'rundy review speca w katalogu przebiegu', key='stage_spec_review'),
    ]
    acc = spec.get('prototypeAcceptance')
    if acc and acc.get('screens'):
        accepted = acc['accepted'] == acc['screens'] and not acc.get('stale')
        spec_stages.append(_stage(f"Prototyp {acc['accepted']}/{acc['screens']}", accepted, key='stage_prototype',
                                  tip=f"{spec.get('prototype')}: zaakceptowane ekrany {acc['accepted']} z {acc['screens']}"
                                  + (f", nieaktualne {acc['stale']}" if acc.get('stale') else '')))
    elif spec.get('prototype') or proto_rounds:
        spec_stages.append(_stage('Prototyp', bool(proto_rounds) or spec_state == 'done',
                                  spec.get('prototype') or 'uwagi do prototypu w przebiegu', key='stage_prototype'))
    spec_stages.append(_stage('Zatwierdzony', spec_state == 'done', f"Status speca: {spec_status or '—'}",
                              key='stage_spec_approved'))
    plan_stages = [
        _stage('Plan', plan is not None, plan.get('path') if plan else 'brak planu', key='stage_plan'),
        _stage('Review', plan_reviews > 0, 'rundy review planu w katalogu przebiegu', key='stage_plan_review'),
        _stage('Pre-implement', pre_impl, 'bramka gotowości przed ryzykowną zmianą', key='stage_pre_implement'),
        _stage('GO implement', plan_status in ('in_progress', 'done'), f"Status planu: {plan_status or '—'}",
               key='stage_go_implement'),
    ]
    code_reviews = sum(1 for name in names if 'code-review' in name or name.startswith('final-review'))
    real_stages = [
        _stage(f'Review kodu ×{code_reviews}' if code_reviews > 1 else 'Review kodu', code_reviews > 0,
               'raporty code-review*/final-review* w katalogu przebiegu', key='code_review'),
        _stage('Zamknięcie', plan_status == 'done', f"Status planu: {plan_status or '—'}", key='closure'),
    ]
    risky = extra.get('risky') or []
    spec_warning = (f"brak pre-implement przed GO plan: spec ma sekcje {', '.join(risky)}"
                    if risky and not pre_impl and spec.get('exists') else None)
    current = progress.get('current')
    total, done = progress.get('total', 0), progress.get('done', 0)
    real_detail = (f"krok {current['index']} z {total}: {current['id']} {current['title']}" if current
                   else 'brak kroków w Progress' if not total
                   else 'wszystkie kroki zaznaczone' if done == total else f'zrobione {done} z {total} kroków')
    return [
        {'key': 'spec', 'roman': 'I', 'name': 'Spec', 'state': spec_state, 'stages': spec_stages, 'warning': spec_warning,
         'detail': 'specyfikacja w przygotowaniu' if spec.get('preparing') else spec.get('sha') and f"Spec-sha {spec['sha'][:10]}",
         'status': spec_status},
        {'key': 'plan', 'roman': 'II', 'name': 'Plan', 'state': plan_state, 'stages': plan_stages,
         'detail': (f"{progress.get('total', 0)} kroków w {len(progress.get('phases', []))} fazach" if plan
                    else 'brak planu (GO plan dany, plan w przygotowaniu)' if spec_state == 'done'
                    else 'brak planu (czeka na GO plan)' if spec.get('exists') or spec.get('preparing') else None),
         'status': plan_status},
        {'key': 'real', 'roman': 'III', 'name': 'Realizacja', 'state': real_state, 'stages': real_stages,
         'percent': progress.get('percent', 0), 'detail': real_detail, 'status': plan_status},
    ]


def _pending_percent(done, pending, total):
    """Segment "done per checkpoint, awaiting verification": a difference of roundings, so both segments add up."""
    if not total:
        return 0
    return round(100 * (done + pending) / total) - round(100 * done / total)


def build_progress(phases, in_progress, plan_status, reported=None):
    steps = [step for phase in phases for step in phase['steps']]
    known = {step['id'] for step in steps if not step['done']}
    active = [sid for sid in (in_progress or []) if sid in known]
    # "w toku: F1" on a plan with sub-steps F1.1…F1.k = the first unchecked sub-step of that phase
    for sid in in_progress or []:
        if sid not in known:
            first = next((step['id'] for step in steps if not step['done'] and step['id'].startswith(sid + '.')), None)
            if first and first not in active:
                active.append(first)
    reported_set = {sid for sid in (reported or []) if sid in known and sid not in active}
    source = 'checkpoint' if active else None
    if not active and plan_status == 'in_progress':
        first = next((step for step in steps if not step['done']), None)
        if first:
            active, source = [first['id']], 'first-unchecked'
    index, current, out_phases = 0, None, []
    for phase in phases:
        phase_steps = []
        for step in phase['steps']:
            index += 1
            if step['done']:
                state = 'done'
            elif step['id'] in active:
                state = 'active'
            else:
                state = 'reported' if step['id'] in reported_set else 'planned'
            phase_steps.append({**step, 'state': state, 'index': index})
            if state == 'active' and current is None:
                current = {'index': index, 'id': step['id'], 'title': step['title']}
        done = sum(1 for step in phase_steps if step['done'])
        pending = sum(1 for step in phase_steps if step['state'] == 'reported')
        name = phase['name']
        label, _, short = name.partition(':')
        out_phases.append({
            'name': name, 'label': label.strip() if short else '', 'short': (short or name).strip(),
            'total': len(phase_steps), 'done': done,
            'percent': round(100 * done / len(phase_steps)) if phase_steps else 0,
            'pending': pending, 'pendingPercent': _pending_percent(done, pending, len(phase_steps)),
            'state': 'done' if done == len(phase_steps)
            else ('active' if any(step['state'] == 'active' for step in phase_steps) else 'planned'),
            'steps': phase_steps,
        })
    total = len(steps)
    done_total = sum(1 for step in steps if step['done'])
    return {
        'total': total, 'done': done_total, 'percent': round(100 * done_total / total) if total else 0,
        'pending': len(reported_set), 'pendingPercent': _pending_percent(done_total, len(reported_set), total),
        'phases': out_phases, 'active': active, 'activeSource': source, 'current': current,
        'parallel': max(0, len(active) - 1), 'reported': sorted(reported_set),
    }


def _minutes(hhmm):
    hours, minutes = hhmm.split(':')
    return int(hours) * 60 + int(minutes)


def substep_marks(checkpoints):
    """Start ("w toku") and finish ("zrobione") of every step id: `({id: "HH:MM"}, {id: "HH:MM"}, ids in mention order)`
    from the first chronological checkpoint naming it. Shared by `substeps[]` (`show`) and `dashboard metrics`."""
    started, finished, order = {}, {}, []
    for item in checkpoints:  # chronological
        for sid in ids_in_progress(item['text']) or []:
            started.setdefault(sid, item['time'])
            if sid not in order:
                order.append(sid)
        for sid in ids_reported_done(item['text']):
            finished.setdefault(sid, item['time'])
            if sid not in order:
                order.append(sid)
    return started, finished, order


def substep_seconds(start, finish):
    """`finish − start` in seconds (minute resolution, crossing midnight = +24 h); None without one of the marks."""
    if not start or not finish:
        return None
    delta = _minutes(finish) - _minutes(start)
    return (delta + 24 * 60 if delta < 0 else delta) * 60


def build_substeps(progress, checkpoints):
    """`substeps[]`: plan steps with an `Fn.k` id, with start ("w toku") and finish ("zrobione") taken from the first
    checkpoint naming them; a done (checked or reported) sub-step without both marks has `missingData: true`."""
    started, finished, _ = substep_marks(checkpoints)
    out = []
    for phase in progress['phases']:
        for step in phase['steps']:
            if '.' not in step['id']:
                continue
            start, finish = started.get(step['id']), finished.get(step['id'])
            seconds = substep_seconds(start, finish)
            done = step['done']
            out.append({'id': step['id'], 'title': step['title'], 'phase': phase['label'] or phase['name'],
                        'state': step['state'], 'done': done, 'startedAt': start, 'finishedAt': finish,
                        'seconds': seconds,
                        'missingData': seconds is None and (done or step['state'] == 'reported')})
    return out


def _instants(run_start, checkpoints):
    """Absolute time of every checkpoint (`- HH:MM …` has no date): the clock of the first one is read against the run start
    (`RUN=<iso>`), a later clock that goes back means the next day. None when the run start is unknown."""
    start = parse_iso(run_start)
    if start is None:
        return None
    previous, out = start.replace(second=0, microsecond=0), []
    for item in checkpoints:
        try:
            hours, minutes = (int(part) for part in item['time'].split(':'))
            moment = previous.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        except (ValueError, KeyError):
            return None
        if moment < previous:
            moment += timedelta(days=1)
        out.append(moment)
        previous = moment
    return out


def _wait_words(what):
    return [word for word in APPROVAL_WORDS if re.search(rf'(?<![\w]){re.escape(word)}(?![\w])', what, re.IGNORECASE)]


def _overlap_seconds(windows, start, finish):
    """Seconds of the closed windows that lie inside `[start, finish]`; None when a window without an end touches it."""
    total = 0
    for window in windows:
        if window['end'] is None:
            if window['startAt'] < finish:
                return None
            continue
        low, high = max(window['startAt'], start), min(window['endAt'], finish)
        if high > low:
            total += int((high - low).total_seconds())
    return total


def build_timing(run_start, checkpoints, approvals, phases):
    """`timing` of the dashboard (`show`) and `dashboard metrics`: work time and waiting time per step and per plan phase.

    A waiting window opens at a checkpoint with `czeka:` and closes at the consent of `approvals.jsonl` named in it
    (`source: approval`, the first entry at or after the opening) or, for any other wait or a consent given in the session,
    at the next checkpoint without `czeka:` (`source: checkpoint`). A window that nothing closes is `unknown: true` with
    `seconds: null`; so is every step whose work time cannot be told (missing start or finish mark, an unknown window inside).
    Work of a step = first `w toku` mark to first `zrobione` mark minus the waiting windows inside; nothing is estimated.
    `checkpoints` are chronological `{time, text}`, `phases` are `parse_progress`/`progress.phases` (steps with `id`)."""
    instants = _instants(run_start, checkpoints)
    if instants is None:
        return {'available': False, 'reason': 'run start (RUN=) or checkpoint time missing', 'windows': [], 'steps': [],
                'stages': [], 'totals': {'workSeconds': None, 'waitSeconds': None, 'windows': 0, 'unknownWindows': 0,
                                         'unknownSteps': 0, 'complete': False}}
    given = []
    for entry in approvals or []:
        moment = _approval_at(entry.get('at'))
        if moment is not None:
            given.append((moment, entry.get('word')))
    given.sort(key=lambda pair: pair[0])

    def consent(window, limit):
        """First consent named by the window, given between its opening and `limit` (None = no limit)."""
        words = _wait_words(window['what'])
        return next((moment for moment, word in given if word in words and moment >= window['startAt']
                     and (limit is None or moment <= limit)), None)

    windows, current = [], None
    for item, moment in zip(checkpoints, instants):
        what = wait_marker(item['text'])
        if what:
            if current is not None and consent(current, moment) is None:   # still waiting: the same window goes on
                if what not in current['what']:
                    current['what'] += '; ' + what
                continue
            current = {'what': what, 'startAt': moment, 'end': None, 'endAt': None, 'source': None}
            windows.append(current)
        elif current is not None:
            current.update(end=True, endAt=moment, source='checkpoint')
            current = None
    for window in windows:
        moment = consent(window, window['endAt'])
        if moment is not None:
            window.update(end=True, endAt=moment, source='approval')
    for window in windows:
        seconds = int((window['endAt'] - window['startAt']).total_seconds()) if window['endAt'] else None
        window['seconds'] = seconds if seconds is not None and seconds >= 0 else None
        if window['seconds'] is None:
            window['end'] = None
    started, finished = {}, {}
    for item, moment in zip(checkpoints, instants):
        for sid in ids_in_progress(item['text']) or []:
            started.setdefault(sid, moment)
        for sid in ids_reported_done(item['text']):
            finished.setdefault(sid, moment)
    iso = lambda value: value.isoformat(timespec='seconds') if value else None  # noqa: E731
    steps, stages = [], []
    for phase in phases:
        spans, work_total, unknown_steps = [], 0, 0
        for step in phase['steps']:
            sid = step['id']
            begin, end = started.get(sid), finished.get(sid)
            if begin is None and end is None and not step.get('done'):
                continue
            wait = work = None
            reason = None
            if begin is None or end is None:
                reason = 'no-start' if begin is None else 'no-finish'
            elif end < begin:
                reason = 'order'
            else:
                wait = _overlap_seconds(windows, begin, end)
                if wait is None:
                    reason = 'open-window'
                else:
                    work = int((end - begin).total_seconds()) - wait
                    spans.append((begin, end))
            if work is None:
                unknown_steps += 1
            else:
                work_total += work
            steps.append({'id': sid, 'phase': phase.get('label') or phase['name'], 'startedAt': iso(begin), 'finishedAt': iso(end),
                          'workSeconds': work, 'waitSeconds': wait, 'unknown': work is None, 'reason': reason})
        if not spans and not unknown_steps:
            continue
        wait_total = _overlap_seconds(windows, min(a for a, _ in spans), max(b for _, b in spans)) if spans else None
        stages.append({'phase': phase.get('label') or phase['name'], 'name': phase['name'], 'workSeconds': work_total if spans else None,
                       'waitSeconds': wait_total, 'unknownSteps': unknown_steps,
                       'complete': bool(spans) and not unknown_steps and wait_total is not None})
    unknown_windows = sum(1 for window in windows if window['seconds'] is None)
    known_wait = sum(window['seconds'] for window in windows if window['seconds'] is not None)
    unknown_steps_total = sum(1 for step in steps if step['unknown'])
    out_windows = [{'what': window['what'], 'startedAt': iso(window['startAt']), 'endedAt': iso(window['endAt'].astimezone(window['startAt'].tzinfo)) if window['seconds'] is not None else None,
                    'seconds': window['seconds'], 'source': window['source'] if window['seconds'] is not None else None,
                    'unknown': window['seconds'] is None} for window in windows]
    return {'available': True, 'reason': None, 'windows': out_windows, 'steps': steps, 'stages': stages,
            'totals': {'workSeconds': sum(stage['workSeconds'] or 0 for stage in stages) if stages else None,
                       'waitSeconds': known_wait, 'windows': len(windows), 'unknownWindows': unknown_windows,
                       'unknownSteps': unknown_steps_total, 'complete': not unknown_windows and not unknown_steps_total}}


def read_readiness(root, run_id, run_exists):
    """`readiness {status, date, planSha, source}` from `.skillsync/runs/<run-id>/readiness.md` (produced by
    `skillsync readiness --run-id`); no file = all null; an unrecognized first line = status null, source set."""
    out = {'status': None, 'date': None, 'planSha': None, 'source': None}
    if not run_exists:
        return out
    rel = f'{RUNS_DIR}/{run_id}/{READINESS_FILE}'
    path = resolve_doc(root, rel)
    if path is None:
        return out
    try:
        text = read_text(path, 64_000)
    except OSError:
        return out
    out['source'] = rel
    first = next((line.strip() for line in text.splitlines() if line.strip()), '')
    match = READINESS_RE.match(first)
    out['status'] = match.group('status').upper() if match else None
    date, sha = READINESS_DATE_RE.search(text), READINESS_SHA_RE.search(text)
    out['date'] = clean_value(date.group('val')) if date else None
    out['planSha'] = sha.group('val').lower() if sha else None
    return out


def waiting_items(run, spec, plan, slug):
    """What the session waits for: the `czeka:` marker of the newest checkpoint wins; otherwise facts from the files:
    `Status: … (czeka na …)` of the plan or spec and a prototype with unaccepted screens before "GO implement"."""
    ctx = {'spec': spec.get('path') if spec else None, 'plan': plan.get('path') if plan else None, 'slug': slug}
    newest = run['checkpoints'][0] if run.get('checkpoints') else None
    marker = wait_marker(newest['text']) if newest else None
    if marker:
        return [wait_item(marker, 'checkpoint', newest['time'], ctx)]
    items = []
    for doc, source in ((plan, 'plan'), (spec, 'spec')):
        text = (doc or {}).get('statusText') or ''
        match = STATUS_WAIT_RE.search(text)
        satisfied = match and re.search(r'GO plan|GO implement', match.group('what'), re.IGNORECASE) and \
            doc.get('status') in ('approved', 'in_progress', 'done', 'implemented')
        if match and not satisfied and doc.get('status') in ('draft', 'approved', 'in_progress'):
            items.append(wait_item(match.group('what').strip(), source, None, ctx))
            break
    acc = spec.get('prototypeAcceptance') if spec else None
    before_go = not plan or plan.get('status') in ('draft', 'approved')
    if acc and acc.get('screens') and before_go and (acc['accepted'] < acc['screens'] or acc.get('stale')):
        what = f"akceptacja prototypu {slug} ({acc['accepted']}/{acc['screens']})"
        if not any('prototyp' in item['what'].lower() for item in items):
            items.append(wait_item(what, 'prototype', None, ctx))
    return items


def doc_item(root, rel):
    path = resolve_doc(root, rel)
    if not path:
        return None
    try:
        text = read_text(path, HEADER_BYTES)
    except OSError:
        return None
    title, _ = parse_header(text)
    return {'path': rel, 'title': title or path.stem, 'status': doc_status(text)[0], 'mtime': _iso(_mtime(path))}


def doc_groups(root, current, run_files, run_id):
    groups = []
    task = [item for item in (doc_item(root, rel) for rel in current if rel) if item]
    if task:
        groups.append({'key': 'task', 'label': 'Bieżące zadanie', 'items': task})
    if run_files:
        items = [item for item in (doc_item(root, rel) for rel in run_files) if item]
        if items:
            groups.append({'key': 'run', 'label': f'Przebieg {run_id}', 'items': items})
    for directory in DOC_DIRS:
        items = []
        for path in sorted((root / directory).glob('*.md'), key=lambda item: item.name, reverse=True):
            rel = _posix_rel(root, path)
            if rel and rel not in current:
                item = doc_item(root, rel)
                if item:
                    items.append(item)
        if items:
            groups.append({'key': directory.split('/')[-1], 'label': DOC_LABELS[directory], 'items': items})
    return groups


def _normal_plan_rel(plan):
    return plan.replace(os.sep, '/').removeprefix('./') if plan else None


def build_status(root, plan=None, *, manifest=None, approvals=True, session_url=None, prototype_port=PROTOTYPE_PORT,
                 version=0, ahead=None, prototype_up=None, acceptance=None):
    """The dashboard contract (`schema: 1`) for the project at `root`. Read-only: nothing in the project is written.

    `plan` = plan path relative to the project (unknown = automatic choice, `planAuto: true`). `version`, `session_url`,
    and the probes `ahead(root, branch, head)`, `prototype_up()`, `acceptance(slug)` let a server add its live state and
    caches; `show` uses the defaults (`version` 0, no session, prototype `up` false)."""
    root = Path(root).resolve()
    if manifest is None:
        _, manifest = read_project(root)
    plans = list_plans(root)
    requested = _normal_plan_rel(plan)
    chosen = next((row for row in plans if row['path'] == requested), None) if requested else None
    auto = chosen is None
    if chosen is None:
        chosen = choose_plan(plans)
    specs = list_specs(root) if auto else []
    spec_act = choose_spec_act(plans, specs, chosen) if auto else None
    run_act = choose_run_act(root, plans, specs, chosen) if auto and not spec_act else None
    if spec_act or run_act:
        chosen = None

    plan_info, fields, phases = None, {}, []
    if chosen:
        plan_path = root / chosen['path']
        text = read_text(plan_path)
        title, fields = parse_header(text)
        phases = parse_progress(text)
        pr = PLAN_PR_RE.search(text)
        status, status_text = doc_status(text)
        plan_info = {'path': chosen['path'], 'title': title or chosen['title'],
                     'pr': {'number': int(pr.group('num')), 'url': pr.group('url')} if pr else None,
                     'status': status, 'statusText': status_text, 'date': clean_value(fields.get('data')),
                     'mtime': _iso(_mtime(plan_path)), 'fileSha': file_sha(plan_path)}

    spec_rel = spec_act['path'] if spec_act else first_token(fields.get('spec'))
    spec_path = resolve_doc(root, spec_rel) if spec_rel else None
    spec = {'path': spec_rel, 'exists': spec_path is not None, 'sha': first_token(fields.get('spec-sha')),
            'prototype': first_token(fields.get('prototyp')), 'preparing': bool(run_act)}
    spec_text, spec_fields = '', {}
    if spec_path:
        spec_text = read_text(spec_path, 400_000)
        spec_title, spec_fields = parse_header(spec_text)
        status, status_text = doc_status(spec_text)
        spec.update({'title': spec_title, 'status': status, 'statusText': status_text,
                     'mtime': _iso(_mtime(spec_path)), 'fileSha': file_sha(spec_path)})
        spec['prototype'] = spec['prototype'] or first_token(spec_fields.get('prototyp'))
    spec['prototype'] = spec['prototype'] or find_prototype(root, spec_rel if spec_path else None)
    proto = spec.get('prototype')
    slug = proto.rstrip('/').rsplit('/', 1)[-1] if proto else None
    if slug and spec_path:
        spec['prototypeAcceptance'] = (acceptance or (lambda name: prototype_acceptance(root, name)))(slug)

    run_id = (run_act if run_act else spec_run_id(root, spec_rel, spec_fields) if spec_act and spec_path
              else first_token(fields.get('przebieg')))
    run = {'id': run_id, 'exists': False, 'start': None, 'checkpoints': [], 'files': []}
    in_progress, reported, chronological = None, [], []
    last_change = [_mtime(root / chosen['path']) if chosen else None, _mtime(spec_path) if spec_path else None]
    directory = run_dir(root, run_id)
    if directory is not None:
        run['exists'] = True
        run['files'] = sorted(rel for rel in (f'{RUNS_DIR}/{run_id}/{item.name}' for item in directory.glob('*.md'))
                              if resolve_doc(root, rel))
        checkpoints = directory / 'checkpoints.md'
        if checkpoints.is_file() and not checkpoints.is_symlink():
            start, chronological = parse_checkpoints(read_text(checkpoints))
            run['start'] = start
            run['checkpoints'] = list(reversed(chronological))
            last_change.append(_mtime(checkpoints))
            run['lastReportAt'] = _iso(_mtime(checkpoints))
            for item in chronological:
                reported += [sid for sid in ids_reported_done(item['text']) if sid not in reported]
            for item in reversed(chronological):
                ids = ids_in_progress(item['text'])
                if ids is not None:
                    in_progress = ids
                    run['inProgressFrom'] = item['time']
                    break
    start_dt = parse_iso(run['start'])
    run['startIso'] = start_dt.isoformat() if start_dt else None

    progress = build_progress(phases, in_progress, plan_info['status'] if plan_info else None, reported)
    acts = compute_acts(spec, plan_info, progress, run['files'], {'risky': risky_sections(spec_text)})
    waiting = waiting_items(run, spec if spec_path else None, plan_info, slug)
    git_info = read_git(root)
    delivery = read_delivery(manifest)
    ahead_state = (ahead or (lambda base, branch, head: git_ahead(base, branch)))(root, git_info['branch'], git_info['head'])
    target = delivery_target(delivery, git_info['branch'], fields)
    delivery_state = {**delivery, 'branch': git_info['branch'], 'target': target, 'remote': ahead_state.get('remote'),
                      'pushed': ahead_state.get('pushed'), 'ahead': ahead_state.get('ahead'),
                      'words': delivery_words(delivery, ahead_state, plan_info, waiting, git_info['branch'], target)}
    current_docs = [spec_rel if spec_path else None, chosen['path'] if chosen else None]
    assumptions, blocking = parse_questions(spec_text) if spec_path else ([], [])
    return {
        'schema': SCHEMA,
        'app': {'name': APP_NAME, 'version': __version__},
        'project': {'name': root.name, 'path': str(root)},
        'generatedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'version': version,
        'lastUpdate': _iso(max((stamp for stamp in last_change if stamp), default=None)),
        'git': git_info,
        'plans': [{key: value for key, value in row.items() if key not in PLAN_ROW_INTERNAL} for row in plans],
        'planAuto': auto,
        'mode': 'plan' if chosen else ('spec' if spec_act else 'run' if run_act else 'none'),
        'plan': plan_info,
        'spec': spec,
        'run': run,
        'progress': progress,
        'acts': acts,
        'waiting': waiting,
        # Report silence: the consumer counts the minutes (no events arrive when nothing changes).
        'silence': ({'since': run.get('lastReportAt'), 'thresholdMin': SILENCE_MIN}
                    if plan_info and plan_info.get('status') == 'in_progress' and run.get('lastReportAt')
                    and not any(item.get('source') == 'checkpoint' for item in waiting) else None),
        'approvals': approvals_state(root, spec, plan_info, delivery_state, run_id, git_info['headFull'],
                                     run_approvals(root, run_id), waiting, approvals),
        'delivery': delivery_state,
        'session': {'url': session_url},
        'prototype': {'slug': slug, 'port': prototype_port, 'up': bool(slug) and bool(prototype_up and prototype_up())},
        'docs': doc_groups(root, [rel for rel in current_docs if rel], run['files'], run_id),
        'readiness': read_readiness(root, run_id, run['exists']),
        'assumptions': {'count': len(assumptions), 'items': assumptions},
        'blockingQuestions': {'count': len(blocking), 'items': blocking},
        'substeps': build_substeps(progress, chronological),
        'timing': build_timing(run['start'], chronological, run_approvals(root, run_id), progress['phases']),
    }


# --------------------------------------------------------------------------- metrics

CHECKPOINTS_FILE = 'checkpoints.md'
FAILED_STATUSES = ('FAIL', 'BLOCKED')


class MetricsBlocked(ValueError):
    """`dashboard metrics` refuses the input (exit code 2)."""


def _metrics_plan(root, run_id, plan):
    """Plan whose `## Progress` lists the sub-steps: `plan` (relative path, must be a plan of the project) or the plan
    with `Przebieg: <run-id>` (choice as `choose_plan`; otherwise the first one); None = no plan."""
    plans = list_plans(root)
    requested = _normal_plan_rel(plan)
    if requested:
        chosen = next((row for row in plans if row['path'] == requested), None)
        if chosen is None:
            raise MetricsBlocked(f'unknown plan: {requested}')
        return chosen
    linked = [row for row in plans if row.get('run') == run_id]
    return choose_plan(linked) or (linked[0] if linked else None)


def _record_seconds(records):
    """Sum of `duration_seconds` of the step records (rounded to a second); None when any record has no duration."""
    if not records or any(record.get('duration_seconds') is None for record in records):
        return None
    return int(round(sum(record['duration_seconds'] for record in records)))


def build_metrics(root, run_id, plan=None):
    """`dashboard metrics` (`schema: 1`, docs/dashboard-contract.md): general metrics of one run. Read-only.

    Sub-step times come from `.skillsync/runs/<run-id>/checkpoints.md` (the same marks as `substeps[]` of `show`,
    `substep_marks`/`substep_seconds`); a sub-step without both marks falls back to its checkpoint records
    `.skillsync/runs/<run-id>.json` (sum of `duration_seconds`); without either it has `seconds: null, noData: true`.
    Nothing is estimated. Raises `MetricsBlocked` for a bad run id, a missing run directory or symlinks."""
    from . import runs as _runs  # lazy: the record reader is needed only here
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise MetricsBlocked('invalid run id')
    try:
        _runs._slug(run_id)
    except ValueError:
        raise MetricsBlocked('invalid run id') from None
    for path in (root / '.skillsync', root / RUNS_DIR):
        if path.is_symlink():
            raise MetricsBlocked(f'{_posix_rel(root, path)} is a symlink')
    run_dir = root / RUNS_DIR / run_id
    if run_dir.is_symlink():
        raise MetricsBlocked(f'{RUNS_DIR}/{run_id} is a symlink')
    if not run_dir.is_dir():
        raise MetricsBlocked(f'no run directory {RUNS_DIR}/{run_id}')
    checkpoints_path = run_dir / CHECKPOINTS_FILE
    if checkpoints_path.is_symlink():
        raise MetricsBlocked(f'{RUNS_DIR}/{run_id}/{CHECKPOINTS_FILE} is a symlink')
    chronological, run_start = [], None
    if checkpoints_path.is_file():
        run_start, chronological = parse_checkpoints(read_text(checkpoints_path))
    try:
        store = _runs._store_path(root, run_id)
        data = _runs._read(store) if store.exists() or store.is_symlink() else None
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise MetricsBlocked(f'run records: {error}') from None
    records = data['checkpoints'] if data else []

    chosen = _metrics_plan(root, run_id, plan)
    started, finished, mentioned = substep_marks(chronological)
    if chosen:
        steps = [(step['id'], step['title']) for phase in parse_progress(read_text(root / chosen['path']))
                 for step in phase['steps'] if '.' in step['id']]
    else:  # no plan: the sub-steps named in the checkpoints, in the order of the first mention
        steps = [(sid, None) for sid in mentioned if '.' in sid]
    by_step = {}
    for record in records:
        by_step.setdefault(record['step'], []).append(record)

    substeps = []
    for sid, title in steps:
        start, finish = started.get(sid), finished.get(sid)
        seconds, source = substep_seconds(start, finish), CHECKPOINTS_FILE
        if seconds is None:
            seconds, source = _record_seconds(by_step.get(sid)), 'records'
        if seconds is None:
            source = None
        substeps.append({'id': sid, 'title': title, 'seconds': seconds, 'startedAt': start, 'finishedAt': finish,
                         'noData': seconds is None, 'source': source})

    phases, members = [], {}
    for item in substeps:
        phase_id = item['id'].split('.', 1)[0]
        if phase_id not in members:
            members[phase_id] = []
            phases.append(phase_id)
        members[phase_id].append(item['seconds'])
    measured = [item['seconds'] for item in substeps if item['seconds'] is not None]
    without = len(substeps) - len(measured)

    if data is None:
        rounds, failures = {'records': None, 'failed': None, 'notRun': None, 'repeatedSteps': None}, []
    else:
        summary, _ = _runs._classify_run(run_id, records)
        rounds = {'records': len(records), 'failed': summary['failed_records'], 'notRun': summary['not_run_records'],
                  'repeatedSteps': summary['repeated_steps']}
        failures = [{'step': record['step'], 'status': record['status'], 'causeKey': record.get('cause_key')}
                    for record in records if record['status'] in FAILED_STATUSES]
    return {
        'schema': SCHEMA,
        'status': 'PASS',
        'run': run_id,
        'plan': chosen['path'] if chosen else None,
        'generatedAt': datetime.now().astimezone().isoformat(timespec='seconds'),
        'substeps': substeps,
        'phases': [{'id': phase_id,
                    'seconds': None if any(value is None for value in members[phase_id]) else sum(members[phase_id])}
                   for phase_id in phases],
        'totals': {'substeps': len(substeps), 'withData': len(measured), 'withoutData': without,
                   'seconds': sum(measured) if substeps and not without else None,
                   'measuredSeconds': sum(measured) if measured else None},
        'rounds': rounds,
        'failures': failures,
        'interventions': None,
        'timing': build_timing(run_start, chronological, run_approvals(root, run_id),
                               parse_progress(read_text(root / chosen['path'])) if chosen else []),
    }


# --------------------------------------------------------------------------- CLI

def _cmd_show(args):
    try:
        root, manifest = read_project(args.path)
        report, code = build_status(root, args.plan, manifest=manifest), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def _cmd_metrics(args):
    try:
        root, _ = read_project(args.path)
        report, code = build_metrics(root, args.run_id, args.plan), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': ' '.join(str(error).split())[:500]}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def _cmd_serve(args):
    from .dashboard_http import serve_command  # lazy: http.server only for `serve`
    return serve_command(args)


def register(subparsers):
    parser = subparsers.add_parser('dashboard', help='Project status page (JSON contract schema 1, docs/dashboard-contract.md)')
    actions = parser.add_subparsers(dest='dashboard_action', required=True)
    show = actions.add_parser('show', help='Print the dashboard JSON of a project (read-only)')
    add_project_arguments(show)
    show.add_argument('--plan', help='plan relative to the project, e.g. docs/plans/<file>.md (default: chosen automatically)')
    show.set_defaults(func=_cmd_show)
    metrics = actions.add_parser('metrics', help='Print general metrics of one run: sub-step times, rounds, failures '
                                                 '(read-only; same JSON with or without --json)')
    add_project_arguments(metrics)
    metrics.add_argument('run_id', metavar='run-id', help='run id (directory .skillsync/runs/<run-id>)')
    metrics.add_argument('--plan', help='plan relative to the project (default: the plan with "Przebieg: <run-id>")')
    metrics.set_defaults(func=_cmd_metrics)
    serve = actions.add_parser('serve', help='Serve the live status page of a project (foreground; Ctrl+C stops)')
    serve.add_argument('path', nargs='?', default='.')
    add_layer_argument(serve)
    serve.add_argument('--plan', help='plan relative to the project (default: chosen automatically, switchable on the page)')
    serve.add_argument('--port', type=int, default=DEFAULT_PORT,
                       help=f'port (default {DEFAULT_PORT}, range {PORT_MIN}-{PORT_MAX}; one port per project)')
    serve.add_argument('--host', default='127.0.0.1',
                       help='listen address (default 127.0.0.1; 0.0.0.0 = all interfaces; other devices also need --approve-host or --read-host)')
    serve.add_argument('--approve-host', action='append', default=[], metavar='NAME',
                       help='extra Host name (no port) allowed to open the page and give consents (repeatable); '
                            'default: localhost only; add --no-approvals for a read-only view')
    serve.add_argument('--read-host', action='append', default=[], metavar='NAME',
                       help='extra Host name (no port) allowed to open the page read-only (repeatable): every read, '
                            'no consents (POST /api/approve = 403), e.g. viewers through a tunnel; '
                            'consents stay with loopback and --approve-host')
    serve.add_argument('--no-approvals', action='store_true', help='read-only page: no consent buttons, POST /api/approve = 403')
    serve.add_argument('--session-url', help='https address of the session (button "Otwórz sesję"); no credentials')
    serve.add_argument('--poll', type=float, default=1.0, help='seconds between file change checks (min 0.2)')
    serve.add_argument('--verbose', action='store_true', help='log every request to stderr')
    serve.set_defaults(func=_cmd_serve)
