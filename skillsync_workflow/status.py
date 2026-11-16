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
import subprocess
import tempfile
import time
import yaml

from .common import add_project_arguments, decode_text, file_lines, plan_section, read_project, redact
from .delivery_mode import delivery_info
from .layers import add_layer_argument
from .storage import read_json
from .pathfmt import posix

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
_STATUS_WINDOW_READ = 8192   # characters masked before the 4000-character window of `_status_fields` is cut (a secret on the cut is whole in this span)
_TITLE = re.compile(r'^#\s+(.+?)\s*$', re.MULTILINE)
_STEP = re.compile(r'^\s*-\s\[([ xX])\]\s+\S', re.MULTILINE)
_ACCEPTANCE_STEP = re.compile(r'^\s*-\s\[[ xX]\]\s+(?:[A-Za-z]+\d+(?:\.\d+)?\s+)?Odbi[oó]r\b', re.MULTILINE | re.IGNORECASE)
ACCEPTANCE_WORDS = {'pr': 'READY', 'direct': 'CP'}   # the closing consent word of a delivery mode: it is also the acceptance
PLAN_SUMMARY = 'Streszczenie dla człowieka'
SUMMARY_MAX_LINES = 40
SUMMARY_MAX_CHARS = 4000
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


def _message(error, limit):
    """Text of an error for a report: masked as a whole first, then whitespace is collapsed and the text cut."""
    return ' '.join(redact(str(error)).split())[:limit]


def _status_fields(text, allowed):
    """Return (status, statusText) from the first status line; a word outside `allowed` gives `unknown`."""
    window = redact(text[:_STATUS_WINDOW_READ])[:4000]   # masked BEFORE the window is cut and before the markers are stripped (a secret that holds `*` or a backtick)
    match = _STATUS.search(window)
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
_PRE_HEADING = re.compile(r'^(?P<hashes>#{1,6})[ \t]+(?P<title>.*?)[ \t]*$', re.MULTILINE)
_PRE_SECTION_WORD = re.compile(r'\b(?:' + '|'.join(PRE_IMPLEMENT_SECTIONS) + r')\b', re.IGNORECASE)
# Pusta treść: pierwsza niepusta linia (bez znaczników) to samo „nie dotyczy” (po nim koniec linii albo . - – — : , ;), samo
# TODO/TBD/n/a (po nim koniec linii, kropka albo dwukropek) albo same myślniki; „Todo-lista” i „Nie dotyczy starych” to treść.
_PRE_TAIL = '*_` \t\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029'   # what the end of a line may carry: markup and white space, also the separators that no longer cut a line
_PRE_EMPTY_BODY = re.compile(r'(?:nie[ \t]+dotyczy(?:[ \t]*$|[ \t]*[.\-–—:,;])|(?:todo|tbd|n/a)(?:[ \t]*$|[ \t]*[.:])|[-–—]+[ \t]*$)',
                             re.IGNORECASE)
_PRE_VERDICT = re.compile(r'^[*_`]*Werdykt:[*_` \t]*(?P<word>READY|NEEDS_CHANGES|BLOCKED)[*_`. \t]*$')
PRE_REPORT_GLOB = 'pre-implement*.md'
MAX_PRE_REPORT_BYTES = 256 * 1024


def _read_text_safe(path, limit=MAX_PLAN_BYTES, root=None):
    """Text of a regular file within `limit` bytes, otherwise None (never raises on I/O or decoding). Symlinks are refused,
    also in the directories between `root` (when given) and the file."""
    try:
        path = Path(path)
        chain = [path]
        if root is not None:
            relative = path.relative_to(root)
            chain = [Path(root).joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1)]
        if any(item.is_symlink() for item in chain) or not path.is_file() or path.stat().st_size > limit:
            return None
        return decode_text(path.read_bytes(), strict=True)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def pre_implement_sections(spec_text):
    """Headings (any level) naming Kontrakty, Migracja or Dane whose body is not empty and not „nie dotyczy”/TODO/TBD/n/a/-.
    The body ends at the nearest heading of the same or a higher level (subsections belong to it)."""
    marks = list(_PRE_HEADING.finditer(spec_text))
    found = []
    for index, mark in enumerate(marks):
        title = mark.group('title').strip()
        if not _PRE_SECTION_WORD.search(title):
            continue
        level = len(mark.group('hashes'))
        end = next((later.start() for later in marks[index + 1:] if len(later.group('hashes')) <= level), len(spec_text))
        body = spec_text[mark.end():end]
        lines = [re.sub(r'^[*_`>\s]+', '', line).rstrip(_PRE_TAIL) for line in file_lines(body)]
        lines = [line for line in lines if line.strip()]
        # a heading-only subsection (no text under it) does not make the section non-empty
        content = [line for line in lines if not _PRE_HEADING.match(line)]
        if content and not _PRE_EMPTY_BODY.match(content[0]):
            found.append(title)
    return found


def _natural_key(name):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', name)]


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
        first = next((line.strip() for line in file_lines(text or '') if line.strip()), '')
        match = _PRE_VERDICT.match(first)
        if match:
            rows.append((path.name, match.group('word')))
    return rows


def pre_implement_state(root, spec_rel, plan_text, status):
    """State of the `pre-implement` gate for a spec: {required, sections, run, report, verdict, warning}, or None when the
    spec file is unreadable. The warning (a string or None) is computed only for plans being prepared or in progress."""
    root = Path(root)
    spec_text = _read_text_safe(root / spec_rel, root=root)
    if spec_text is None:
        return None
    from .quality import _PLAN_RUN, _RUN_ID  # lazy: quality imports this module
    sections = pre_implement_sections(spec_text)
    required = bool(sections)
    run_match = _PLAN_RUN.search(plan_text) if plan_text else None
    run = run_match.group(1) if run_match and _RUN_ID.fullmatch(run_match.group(1)) else None
    reports = _pre_implement_reports(root, run) if run else []
    worst = max(reports, key=lambda item: (PRE_IMPLEMENT_VERDICTS.index(item[1]), _natural_key(item[0]))) if reports else None
    report = f'.skillsync/runs/{run}/{worst[0]}' if worst else None
    verdict = worst[1] if worst else None
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


def plan_summary(text):
    """The `## Streszczenie dla człowieka` section as plain text for the status page: heading markers removed, at most 40 lines and
    4000 characters (a cut one ends with `…`); None when the section is absent or empty."""
    body = plan_section(redact(text), PLAN_SUMMARY)   # the whole plan is masked before the section is taken, trimmed and cut
    if not body:
        return None
    lines, fence = [], None
    for line in file_lines(body):
        marker = re.match(r'^\s*(```+|~~~+)', line)
        if marker:
            fence = None if fence == marker.group(1)[0] else (fence or marker.group(1)[0])
        lines.append(line.rstrip() if fence or marker else re.sub(r'^\s*#{1,6}\s+', '', line).rstrip())   # a code block stays as written
    cut = len(lines) > SUMMARY_MAX_LINES
    result = '\n'.join(lines[:SUMMARY_MAX_LINES]).strip()
    if len(result) > SUMMARY_MAX_CHARS:
        result, cut = result[:SUMMARY_MAX_CHARS].rstrip(), True
    return redact(result + ('…' if cut else '')) or None


def has_acceptance_step(status, text):
    """A closed plan (`done`) whose `## Progress` has the human acceptance step (`Odbiór`)."""
    section = plan_section(text, 'Progress') if status == 'done' else None
    return bool(section) and bool(_ACCEPTANCE_STEP.search(section))


def _plan_done_at(root, sha, rel):
    """True when the plan file `rel` read from the commit `sha` (git, no working tree) has the status `done`. An unknown, junk or non-commit
    sha, a plan missing there or any other status is False: such an entry says nothing about the closing of this plan."""
    if not root or not isinstance(sha, str) or not _APPROVAL_SHA.fullmatch(sha):
        return False
    try:
        result = subprocess.run(['git', '-C', str(root), 'show', f'{sha}^{{commit}}:{rel}'], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return _status_fields(result.stdout.decode('utf-8', 'replace'), PLAN_STATUSES)[0] == 'done'


PLAN_HEADER_LINES = 30   # the `Status:` line of a plan sits in its header; the same word in the body is not the plan's status
_HUNK = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def _plan_status_kept_since(root, sha, rel):
    """True when no commit after `sha` (`sha..HEAD`, one git call, merge commits show no diff) touched a `Status:` line in the first
    `PLAN_HEADER_LINES` lines of the plan `rel` with a word other than `done`: the plan was not reopened since the entry. Edits of the text
    after the word (`done (note)`), body lines and mentions of status are not a reopening. Any git error, a timeout or an oversized history is
    False: the closing is then not counted (fail-safe). A `sha` that is not a plain hex object name is False without any git call."""
    if not root or not isinstance(sha, str) or not _APPROVAL_SHA.fullmatch(sha):
        return False
    try:
        result = subprocess.run(['git', '-C', str(root), 'log', '-p', '-U0', '--no-color', '--no-ext-diff', '--format=', f'{sha}..HEAD', '--', rel],
                                capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0 or len(result.stdout) > MAX_PLAN_BYTES * 8:
        return False
    old = new = 0
    for line in file_lines(result.stdout.decode('utf-8', 'replace')):
        hunk = _HUNK.match(line)
        if hunk:
            old, new = int(hunk.group(1)), int(hunk.group(2))
            continue
        if line[:1] not in ('+', '-') or line[:3] in ('+++', '---'):
            continue
        number = new if line[0] == '+' else old
        if line[0] == '+':
            new += 1
        else:
            old += 1
        match = _STATUS.match(line[1:]) if number <= PLAN_HEADER_LINES else None
        if match and match.group('word').lower() != 'done':
            return False
    return True


def acceptance_pending(status, text, context, rel=None):
    """True when a closed plan (`done`) that has the human acceptance step (`Odbiór` in `## Progress`: the material, a checklist and the
    coverage table, handed to the person before the closing word) still waits for that word. The plan is tied to its run by the `Przebieg:`
    line; every plan whose run directory exists in the worktree (`.skillsync/runs/<run-id>/`: the active run) waits on its own until the run's
    `approvals.jsonl` has the closing consent of the delivery mode (`READY` in `pr`, `CP` in `direct`) whose sha is a commit where this plan
    (`rel`) already has the status `done`: a `CP` given earlier in the run (it is said many times) or an entry with a junk sha closes nothing, and
    later commits of the branch do not light a finished plan again, but a reopening (a `Status:` line with another word in a later commit, `_plan_status_kept_since`) makes the old entry stale: the second closing waits for a new word. A plan whose run directory is gone (history of a merged branch) has no
    signal. The step's checkbox changes nothing. `context` = `{'mode', 'approvals', 'runs', 'root'}`; without it (or without a valid delivery
    mode) there is no signal."""
    if not has_acceptance_step(status, text) or not context:
        return False
    word = ACCEPTANCE_WORDS.get(context.get('mode'))
    if word is None:
        return False
    from .quality import _PLAN_RUN, _RUN_ID  # lazy: quality imports this module
    run = _PLAN_RUN.search(text[:4000])
    if not run or not _RUN_ID.fullmatch(run.group(1)) or run.group(1) not in context.get('runs', ()):
        return False
    return not any(row['run'] == run.group(1) and row['word'] == word and _plan_done_at(context.get('root'), row.get('sha'), rel)
                   and _plan_status_kept_since(context.get('root'), row.get('sha'), rel)
                   for row in context.get('approvals', []))   # `read_approvals` keeps one entry per (run, word): at most two git calls per plan


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


def plan_record(root, path, context=None):
    text = decode_text(path.read_bytes(), strict=True)   # invalid UTF-8 stays an error for the caller (a finding, no row), as `read_text(encoding='utf-8')` was
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
            'needsHuman': plan_needs_human(status, steps, bool(ticket), bool(pr)) + (['acceptance'] if acceptance_pending(status, text, context, path.relative_to(root).as_posix()) else []),
            'updated': _iso(path.stat().st_mtime),
            'preImplement': pre, 'summary': plan_summary(text)}


def _plans(root, findings, context=None):
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
            rows.append(plan_record(root, path, context))
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
            text = decode_text(path.read_bytes(), strict=True)
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
            findings.append(f'Run unreadable: {path.stem} ({_message(error, 120)})')
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


def _run_ids(root):
    """Names of the run directories of the worktree (`.skillsync/runs/<run-id>/`, no symlinks): the runs that are active here."""
    directory = Path(root) / '.skillsync' / 'runs'
    if directory.is_symlink() or not directory.is_dir():
        return set()
    try:
        return {item.name for item in directory.iterdir() if item.is_dir() and not item.is_symlink()}
    except OSError:
        return set()


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
            lines = file_lines(path.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError) as error:
            if findings is not None:
                findings.append(f'Approvals unreadable: {path.parent.name} ({_message(error, 120)})')
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
                   'source': redact(str(item.get('source') or ''))[:40]}
            if isinstance(item.get('file'), str):
                row['file'] = redact(item['file'])[:300]
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
    approvals = read_approvals(root, findings)
    context = {'mode': delivery_info(manifest)['mode'], 'approvals': approvals, 'runs': _run_ids(root), 'root': root}
    return {'schema': SCHEMA, 'status': 'PASS', 'project': name or f'{root.parent.name}/{root.name}', 'root': posix(root),
            'plans': _plans(root, findings, context), 'specs': _specs(root, findings), 'frds': _frds(root, findings), 'runs': _runs(root, findings), 'claims': _claims(root, findings),
            'approvals': approvals, 'findings': findings}


def _cmd_status(args):
    try:
        root, manifest = read_project(args.path)
        report, code = project_status(root, manifest), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500)}, 2
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
    report = {'schema': SCHEMA, 'status': 'PASS', 'reason': f'{kind} status {source} -> {target}', 'root': posix(root),
              kind.lower(): path.relative_to(root).as_posix(), 'from': source, 'to': target, 'sha': git_blob_sha(updated)}
    report['warnings'] = _approve_warnings(root, path, updated.decode('utf-8').replace('\r\n', '\n'), folder) if action == 'approve' else []
    return report


def _plan_text_for_spec(root, spec_rel, spec_text):
    """Plan text for a spec: the file named by its `Plan:` line, otherwise the newest `docs/plans/*.md` whose `Spec:` line
    points at this spec and which has a valid `Przebieg:` line. None when there is no such plan."""
    from .quality import _PLAN_RUN, _RUN_ID
    plan = _spec_plan(spec_text)
    text = _read_text_safe(root / plan, root=root) if plan else None
    if text is not None:
        return text
    directory = root / 'docs' / 'plans'
    try:
        if (root / 'docs').is_symlink() or directory.is_symlink() or not directory.is_dir():
            return None
        paths = sorted(directory.glob('*.md'), key=lambda item: item.name, reverse=True)
    except OSError:
        return None
    for path in paths[:MAX_PLANS]:
        if path.name.lower() == 'readme.md':
            continue
        candidate = _read_text_safe(path, root=root)
        if candidate is None or _plan_spec(candidate) != spec_rel:
            continue
        match = _PLAN_RUN.search(candidate)
        if match and _RUN_ID.fullmatch(match.group(1)):
            return candidate
    return None


def _approve_warnings(root, path, text, folder):
    """Pre-implement warning for the state after `approve` (a list, empty when nothing to warn about). Never raises."""
    try:
        if folder == 'plans':
            spec, plan_text = _plan_spec(text), text
        else:
            spec = path.relative_to(root).as_posix()
            plan_text = _plan_text_for_spec(root, spec, text)
            if plan_text is None:
                return []
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
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500),
                        'root': posix(Path(args.path).expanduser().resolve())}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def _doc_status_word(root, name):
    folder = 'specs' if Path(name).parts[:2] == ('docs', 'specs') else 'plans'
    path = _doc_file(root, name, folder)
    match = _STATUS.search(decode_text(path.read_bytes())[:4000])
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
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500)}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


APPROVAL_SOURCES = ('cli', 'dashboard', 'ide')
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')  # the run-id pattern of `quality._RUN_ID` (a test keeps them equal)
_REF_NAME = re.compile(r'^(?!-)[\w.][\w./-]{0,120}$')
_GO_FOLDER = {'GO plan': 'specs', 'GO implement': 'plans'}
APPROVAL_LOCK_TIMEOUT = 10.0


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


def _lock_module():
    """The prototype server module (imported on first use: ~0.3 s, and only a consent write needs it). Its `FileLock` is
    the one system lock (`flock`; `msvcrt.locking` on Windows) shared with the questions store, comments and users files;
    importing it opens no port, reads no configuration and changes no signal handler."""
    from . import prototype_server
    return prototype_server


def _approval_entries(path):
    """Parseable dict lines of an approvals file (for the duplicate check, before the read rules of `read_approvals`)."""
    rows = []
    for line in file_lines(path.read_text(encoding='utf-8', errors='replace')):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def record_approval(root, word, run_id, sha=None, source='cli', target=None, branch=None, file=None, by=None, now=None,
                    plan=None, ahead=None, mode=None, sha_after=None, via=None):
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
        if via is not None and not (isinstance(via, dict) and all(isinstance(key, str) for key in via)):
            raise ValueError('via must be an object (port, host, peer)')
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        entry = {'schema': SCHEMA, 'at': moment.strftime('%Y-%m-%dT%H:%M:%SZ'), 'word': word, 'sha': sha,
                 'by': redact(str(by if by is not None else _approval_user()))[:80], 'source': source}
        extra = {'file': file, 'shaAfter': sha_after, 'run': run_id, 'plan': plan, 'target': target, 'branch': branch, 'ahead': ahead, 'mode': mode, 'via': via}
        entry.update({key: value for key, value in extra.items() if value is not None})
        entry = redact(entry)
        line = (json.dumps(entry, ensure_ascii=False) + '\n').encode('utf-8')
        path = run_dir / APPROVALS_FILE
        server = _lock_module()
        try:
            with server.FileLock(str(run_dir / (APPROVALS_FILE + '.lock')), APPROVAL_LOCK_TIMEOUT, 0o600, unlink=True):
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
        except server.LockUnavailable:
            raise ValueError(f'The file system does not support file locks (approvals lock {APPROVALS_FILE}.lock not supported); '
                             'keep .skillsync on a local disk') from None
        except server.LockTimeout:
            raise ValueError(f'Approvals file is locked by another writer: {APPROVALS_FILE}.lock') from None
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as error:
        return {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500), 'entry': None}
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
        report = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500), 'entry': None}
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
