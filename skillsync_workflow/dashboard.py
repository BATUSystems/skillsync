"""Dashboard engine: the project status page as a JSON contract (`schema: 1`, `docs/dashboard-contract.md`).

`build_status(root, plan=None)` reads (never writes) plans `docs/plans/*.md`, specs, `.skillsync/runs/<run-id>/`
(`checkpoints.md`, `approvals.jsonl`, `readiness.md`), prototypes and `.git` of the project and returns the dictionary the
status page renders. `skillsync dashboard show` prints it; `dashboard serve` (`dashboard_http.py`) serves it live. Shared
parsers come from `status.py` (status line, git blob sha, approval entry rules); phases and `Fn.k` sub-steps of `## Progress` are the dashboard's own extension (contract section
"Zgodność z `skillsync status`")."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import threading
import time

import yaml

from . import __version__
from .common import add_project_arguments, decode_text, file_lines, plan_section, read_project, redact
from .layers import add_layer_argument
from .delivery_mode import delivery_info
from .status import (APPROVAL_WORDS, APPROVALS_FILE, MAX_APPROVALS_BYTES, _APPROVAL_SHA, _approval_at, _message, _status_fields,
                     git_blob_sha, plan_state_for_run, valid_branch_name)
from .pathfmt import posix
from .specs import specs_info
from . import plan_phases, dashboard_tests, dashboard_forecast, dashboard_agents, dashboard_estimates, test_events
from . import feedback, i18n
from .layers import LayerError
from . import protocol_tokens as tokens

SCHEMA = 1
APP_NAME = 'dashboard'
PROTOTYPE_PORT = 5130
DEFAULT_PORT = 5140  # `dashboard serve`; pick another port per project to run several in parallel
PORT_MIN, PORT_MAX = 5100, 5199
FORBIDDEN_PORTS = frozenset({4444, 4445, 4446, 4447, 4448, 9222, 9333})
DOC_DIRS = ('docs/specs', 'docs/plans', 'docs/design', 'docs/domain')
DOC_LABELS = {'docs/specs': 'srv.docs.specs', 'docs/plans': 'srv.docs.plans', 'docs/design': 'srv.docs.design', 'docs/domain': 'srv.docs.domain'}   # catalog keys
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
IN_PROGRESS_RE = re.compile(r'w\s+toku\s*:?\s*(?P<seg>.*)', re.IGNORECASE)   # a segment of `split_segments`: `;` inside brackets is part of it
DONE_RE = re.compile(r'(?<![\w])zrobion[eya]\s*:?\s*(?P<seg>.*)', re.IGNORECASE)
# English keywords: only at the start of a segment and with the colon (prose has "review done: 0", "plugin in progress:"); the Polish ones win everywhere.
IN_PROGRESS_EN_RE = re.compile(r'^\s*in\s+progress\s*:\s*(?P<seg>.*)', re.IGNORECASE)
DONE_EN_RE = re.compile(r'^\s*done\s*:\s*(?P<seg>.*)', re.IGNORECASE)
ID_LIST_RE = re.compile(rf'^\s*(?P<ids>{STEP_ID}(?:(?:\s*(?:[/+&‖]|\|\|)\s*|\s+(?:i|oraz)\s+){STEP_ID})*)(?![\w-])')
# "The session waits for a human" in a checkpoint: segment `czeka: <what>` (segments are split by `;`, like
# "zrobione" / "w toku"). The newest checkpoint without `czeka:` clears the banner.
WAIT_RE = re.compile(r'^\s*(?:czeka|waiting)\s*:\s*(?P<seg>.+)', re.IGNORECASE)   # matched against one segment of `split_segments`
WAIT_NONE = {'', '-', '—', 'nic', 'brak', 'nie', 'none', 'n/a', 'nothing', 'no'}
HANDOVER_PLACEHOLDERS = WAIT_NONE | {'tbd', 'todo', 'none', 'n/a', 'null'}   # one-word placeholders are valid ref names, but nobody hands over to them
# "The session works until HH:MM" in a checkpoint: segment `eta: <operation> do HH:MM` (like `czeka:`, split by `;`).
ETA_RE = re.compile(r'^\s*eta\s*:\s*(?P<seg>.+)', re.IGNORECASE)
ETA_AT_RE = re.compile(r'^(?P<operation>.*?\S)\s+(?:do|until)\s+(?P<time>\d{1,2}:\d{2})\.?$', re.IGNORECASE)
# "A stage is going on now": segment `trwa: <stage key>[, <key>…]` of a checkpoint (a segment of `split_segments`, like `czeka:`).
TRWA_RE = re.compile(r'^\s*(?:trwa|running|ongoing)\s*:\s*(?P<seg>.+)', re.IGNORECASE)
# Who the waiting item is addressed to: suffix `(kto: <role>[ <login>])` of a `czeka:` value.
WHO_RE = re.compile(r'\s*\(\s*(?:kto|who)\s*:\s*(?P<who>[^()]*?)\s*\)\s*\.?\s*$', re.IGNORECASE)
WHO_ROLES = {'acceptor': 'akceptujący', 'developer': 'programista', 'expert': 'ekspert', 'commenter': 'komentujący', 'agent': 'agent'}   # Polish labels (the default language)
WHO_ROLE_KEYS = {'acceptor': 'srv.who.acceptor', 'developer': 'srv.who.developer', 'expert': 'srv.who.expert', 'commenter': 'srv.who.commenter',
                 'agent': 'srv.who.agent'}
ACCEPTANCE_HEADS = ('odbiór', 'odbior', 'acceptance')   # the head of `(kto: odbiór)` / `(who: acceptance)`: an acceptor with its own label
WHO_ALIASES = {'akceptujący': 'acceptor', 'akceptujacy': 'acceptor', 'programista': 'developer', 'ekspert': 'expert',
               'komentujący': 'commenter', 'komentujacy': 'commenter', 'odbiór': 'acceptor', 'odbior': 'acceptor',
               'approver': 'acceptor', 'acceptance': 'acceptor'}
STATUS_WAIT_RE = re.compile(r'(?:czeka\s+na|waiting\s+for)\s+(?P<what>[^);,]+)', re.IGNORECASE)
RETRO_ANSWER_RE = re.compile(rf'\b{tokens.token_alternation("yes")}\b.*\b{tokens.token_alternation("no")}\b|\bP\d+\b')   # a retro proposal: YES/NO (TAK/NIE) or P<n>
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
RISKY_SECTIONS = tuple(tokens.names(key)[0] for key in ('contracts', 'migration', 'data'))   # the Polish section titles (English aliases wait for the title-prefix rule)

READINESS_RE = re.compile(r'^Readiness:\s*(?P<status>PASS|BLOCKED)\b', re.IGNORECASE)
READINESS_DATE_RE = re.compile(r'^(?:Data|Date):\s*(?P<val>\S.*?)\s*$', re.MULTILINE)
READINESS_SHA_RE = re.compile(r'^Plan-sha:\s*`?(?P<val>[0-9a-fA-F]{7,64})`?\s*$', re.MULTILINE)
QUESTIONS_HEADING = re.compile(r'^##\s+(?:Pytania otwarte|Open questions)\b', re.IGNORECASE)
QUESTION_RE = re.compile(r'^\s*[-*]\s+(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?(?:\s*\[(?P<tag>[^\]]*)\])?\s*:\s*(?P<body>\S.*?)\s*$')
TABLE_QUESTION_RE = re.compile(r'^\s*\|\s*(?:\*\*)?(?P<id>Q\d+)(?:\*\*)?\s*\|(?P<rest>.*)$')
ACCEPTED_RE = re.compile(r'\s+(?:—|–|--|-)\s+(?:przyj[eę]te|assumed|accepted)\s*:\s*', re.IGNORECASE)
ASSUMPTION_TAGS = {'założenie', 'zalozenie'} | {name.strip('[]').lower() for name in tokens.names('assumption')}

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

MASK_MARGIN = 4096   # bytes read beyond a limit so that a secret on the limit is masked whole (see `read_masked`)


def read_text(path, limit=MAX_DOC_BYTES):
    with open(path, 'rb') as handle:
        return decode_text(handle.read(limit))


def read_masked(path, limit=MAX_DOC_BYTES):
    """`read_text` with every secret masked (`common.redact`) in the WHOLE text, right after reading and before any parser cuts, splits or
    trims it: a key before a cut keeps masking its value, and a secret from the environment is replaced while it is still in one piece.
    The text of plans, specs, checkpoints and notes goes through this reader; the final `redact` of the status stays as the second layer.
    The byte limit is applied AFTER the masking: `MASK_MARGIN` more bytes are read, so a secret that lies on the limit is whole when it is masked
    (a longer one is cut by the margin and is no longer a secret of this machine), and only then the text is cut to `limit` bytes."""
    with open(path, 'rb') as handle:
        data = handle.read(limit + MASK_MARGIN)
    text = redact(decode_text(data))
    return text.encode('utf-8')[:limit].decode('utf-8', 'ignore') if len(data) > limit else text


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


def _header_key(name):
    return name.rstrip(':').lower()


HEADER_ALIASES = {_header_key(tokens.names(key)[1]): _header_key(tokens.names(key)[0]) for key in ('prototype', 'handoff')}   # English header keys read as the Polish ones
HEADER_ALIASES[_header_key(tokens.names('run')[1])] = _header_key(tokens.names('run')[0])   # `Run: id (note)` reads like `Przebieg: id (note)`
HEADER_ALIASES['date'] = 'data'   # not in the token map yet


def parse_header(text):
    """Title (first `# `) and `Key: value` fields before the first `## ` (English keys `Prototype`, `Date`, `Handoff` are stored
    under the Polish names; the first line of a key wins; the run id is read by `protocol_tokens.RUN_LINE`: `Przebieg:` or `Run:`)."""
    title, fields, head = None, {}, []
    for line in file_lines(text):
        if line.startswith('## '):
            break
        head.append(line)
        if title is None and line.startswith('# '):
            title = line[2:].strip()
            continue
        match = HEADER_RE.match(line)
        if match:
            key = match.group('key').strip().lower()
            fields.setdefault(HEADER_ALIASES.get(key, key), match.group('val').strip())
    run = tokens.RUN_LINE.search('\n'.join(head))
    if run and run.group(1):
        # The first line of the key wins, as for every other header field: `RUN_LINE` only normalizes the same id (backticks) or fills a gap.
        first = fields.get('przebieg')
        if first is None or first.strip().strip('`') == run.group(1):
            fields['przebieg'] = run.group(1)
    return title, fields


def parse_progress(text, lang='pl'):
    """`## Progress`: phases `### …` and steps `- [ ] ID Title` / `- [x] ID Title — sha`; code blocks are skipped."""
    phases, current, in_progress, in_fence = [], None, False, False
    for line in file_lines(text):
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
            current = {'name': i18n.t(lang, 'srv.phase.steps'), 'steps': []}
            phases.append(current)
        rest = match.group('rest').strip()
        title, note = rest, ''
        for sep in (' — ', ' -- ', ' – '):
            if sep in rest:
                title, note = rest.split(sep, 1)
                break
        token = note.strip().split()[0].strip(',;()') if note.strip() else ''
        is_test = plan_phases.has_test_mark(title)
        current['steps'].append({'id': match.group('id'), 'title': plan_phases.strip_test_mark(title) if is_test else title.strip(),
                                 'done': match.group('mark') != ' ', 'note': note.strip(), 'sha': token if SHA_RE.match(token) else None,
                                 **({'test': True} if is_test else {})})
    return [phase for phase in phases if phase['steps']]


def parse_checkpoints(text):
    """`RUN=<iso>` and `- HH:MM text` lines (indented lines continue the previous one)."""
    run_start, items = None, []
    for line in file_lines(text):
        stripped = line.strip()
        if stripped.startswith('RUN='):
            run_start = stripped[4:].strip()
            continue
        match = CHECKPOINT_RE.match(line)
        if match:
            hours, minutes = match.group('time').split(':')
            items.append({'time': f'{int(hours):02d}:{minutes}', 'text': match.group('text').strip()})   # contract: HH:MM
        elif items and stripped and line[:1] in (' ', '\t'):
            items[-1]['text'] += ' ' + stripped
    return run_start, items


def wait_marker(text):
    """Value of the `czeka:` segment of a checkpoint text, or None."""
    match = _first_segment_match(WAIT_RE, text)
    if not match:
        return None
    value = match.group('seg').strip().rstrip('.')
    return None if value.lower() in WAIT_NONE else value


def parse_eta(text):
    """`(operation, "HH:MM")` of the `eta: <operation> do HH:MM` segment of a checkpoint text, or None (no segment, no
    readable time, an empty operation, or the "nothing" values of `czeka:`)."""
    match = _first_segment_match(ETA_RE, text)
    found = ETA_AT_RE.match(match.group('seg').strip()) if match else None
    if not found:
        return None
    hours, minutes = (int(part) for part in found.group('time').split(':'))
    if hours > 23 or minutes > 59:
        return None
    return found.group('operation').strip(), f'{hours:02d}:{minutes:02d}'


def parse_eta_clear(text):
    """True for an explicit `eta: -` (the dash, also en/em dash): the estimate ends. `parse_eta` returns None for it AND for no segment at
    all, so the two need this separate check."""
    match = _first_segment_match(ETA_RE, text)
    return bool(match) and match.group('seg').strip().rstrip('.') in ('-', '–', '—')


def trwa_marker(text):
    """Stage keys of the `trwa:` segment of a checkpoint text (lower case, comma separated; `‖` also joins), or None."""
    match = _first_segment_match(TRWA_RE, text)
    if not match:
        return None
    keys = []
    for part in split_top_level(match.group('seg').replace('‖', ',')):
        key = ' '.join(part.split()).strip(' .').lower()
        if key and key not in NO_ITEMS and key not in keys:
            keys.append(key)
    return keys or None


def split_who(value, lang='pl'):
    """`(text without the suffix, who)` for a `czeka:` value: `who` = `{role, login, label}` from `(kto: <role>[ <login>])`, or None
    without a suffix. An unknown role keeps its text as `label` with `role: None` (no error)."""
    match = WHO_RE.search(value)
    if not match:
        return value, None
    clean = value[:match.start()].rstrip()
    words = match.group('who').split()
    if not words or not clean:
        return value, None
    head = words[0].lower()
    role = head if head in WHO_ROLES else WHO_ALIASES.get(head)
    login = ' '.join(words[1:]) or None if role else None
    label = i18n.t(lang, WHO_ROLE_KEYS[role]) if role else ' '.join(words)
    if head in ACCEPTANCE_HEADS:
        label = i18n.t(lang, 'srv.who.acceptance')
    return clean, {'role': role, 'login': login, 'label': label}


def eta_records(chronological, instants):
    """Checkpoints as the records of `dashboard_estimates` (`instant` None without a run start)."""
    records = []
    for index, item in enumerate(chronological):
        text = item['text']
        records.append({'time': item['time'], 'instant': instants[index] if instants else None, 'eta': parse_eta(text), 'etaClear': parse_eta_clear(text),
                        'wait': wait_marker(text), 'inProgress': ids_in_progress(text), 'done': ids_reported_done(text)})
    return records


def build_eta(run_start, chronological, plan_status, timing=None):
    """`eta` of the dashboard: the open series of `eta:` lines after the last checkpoint, or None (see `dashboard_estimates`: one table ends a
    series). An estimate with steps (the `w toku` ids of its line) goes on through `czeka:` lines; `shiftSeconds` is the time the user was waited
    for (closed windows of `timing`) since the term was set, `untilIso` the shifted term, `untilOriginalIso` the term of the checkpoint,
    `paused` an open waiting window of the user. An estimate without steps ends at a `czeka:` line, as before. `untilIso` is read like
    `timing` reads clocks (run start + midnight crossings; None without a run start); an estimate more than 12 h before its checkpoint is the
    next day. The server reads no clock: the renderer counts the overrun from `now`."""
    if not chronological or plan_status in ('done', 'implemented'):
        return None
    instants = _instants(run_start, chronological)
    windows = timing.get('windows') if isinstance(timing, dict) and timing.get('available') else []
    return dashboard_estimates.current_eta(eta_records(chronological, instants), instants, windows, False, parse_iso, split_who)


def build_estimates(run_start, chronological, plan_status, timing):
    """`estimates[]` (one row per series of `eta:` lines, the error without the time the user was waited for) or None when there is none."""
    instants = _instants(run_start, chronological)
    windows = timing.get('windows') if isinstance(timing, dict) and timing.get('available') else []
    rows = dashboard_estimates.build_rows(eta_records(chronological, instants), instants, windows, plan_status in ('done', 'implemented'),
                                          parse_iso, split_who)
    return rows or None


def wait_item(what, source, since=None, ctx=None, lang='pl'):
    """Banner entry: what the session waits for, what to do and where the link leads (`doc` or `prototype`)."""
    ctx = ctx or {}
    low = what.lower()
    item = {'what': what, 'source': source, 'since': since, 'action': i18n.t(lang, 'srv.wait.generic', what=what), 'link': None}
    if 'prototyp' in low:   # also "prototype"
        match = re.search(r'prototyp\w*\s+(?P<slug>[A-Za-z0-9][\w.-]*)', what, re.IGNORECASE)
        slug = match.group('slug') if match else ctx.get('slug')
        item.update(action=i18n.t(lang, 'srv.wait.prototype'),
                    link={'kind': 'prototype', 'slug': slug} if slug else None)
    elif 'go plan' in low:
        item.update(action=i18n.t(lang, 'srv.wait.goPlan'),
                    link=ctx.get('spec') and {'kind': 'doc', 'path': ctx['spec']}, word='GO plan', approve='spec')
    elif 'go implement' in low:
        item.update(action=i18n.t(lang, 'srv.wait.goImplement'),
                    link=ctx.get('plan') and {'kind': 'doc', 'path': ctx['plan']}, word='GO implement', approve='plan')
    elif (re.match(r'(odpowied\w*|pytani\w*|answer\w*|question\w*)?\s*Q\d', what, re.IGNORECASE) or 'pytani' in low or 'odpowied' in low
          or 'question' in low or 'answer' in low):
        item.update(action=i18n.t(lang, 'srv.wait.questions', what=what), link=ctx.get('spec') and {'kind': 'doc', 'path': ctx['spec']})
    elif re.fullmatch(r'(CP|CPD)!?', what.strip(), re.IGNORECASE):
        item.update(action=i18n.t(lang, 'srv.wait.cp'),
                    word=what.strip().upper(),
                    approve='cp' if what.strip().upper().startswith('CP') and 'D' not in what.upper() else None)
    elif re.fullmatch(r'READY!?', what.strip(), re.IGNORECASE):
        item.update(action=i18n.t(lang, 'srv.wait.ready'),
                    word=what.strip().upper(), approve='ready')
    elif RETRO_ANSWER_RE.search(what):
        item.update(action=i18n.t(lang, 'srv.wait.retro', what=what))
    return item


def split_segments(text):
    """Segments of a checkpoint text: `;` ends a segment only outside brackets (`(`, `[`, `{`), so `w toku: F1.1 (a; b), F1.3`
    stays one segment. A line whose brackets do not balance (an unclosed `(` would swallow the rest of the line) is split at
    every `;`, as before the brackets were honoured."""
    parts = split_top_level(text, ';', balanced=True)
    return parts if parts is not None else text.split(';')


def _first_segment_match(pattern, text):
    for segment in split_segments(text):
        match = pattern.search(segment)
        if match:
            return match
    return None


def _keyword_match(polish, english, text):
    """The Polish keyword in any segment first (as before the English ones existed), else the English one at the start of a segment."""
    return _first_segment_match(polish, text) or _first_segment_match(english, text)


def split_top_level(segment, sep=',', balanced=False):
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
    return None if balanced and depth else parts


def unclosed_bracket_start(text):
    """Index of the outermost opening bracket that is never closed, None when the brackets balance. Counts like `split_top_level`
    (any closing bracket closes the innermost open one, a closing bracket at depth 0 is ignored), so the answer is not None exactly
    when `split_segments` falls back to every `;`."""
    stack = []
    for index, char in enumerate(text):
        if char in '([{':
            stack.append(index)
        elif char in ')]}' and stack:
            stack.pop()
    return stack[0] if stack else None


def _lead_ids(segment):
    ids = []
    for item in split_top_level(segment):
        lead = ID_LIST_RE.match(item)
        if lead:
            for sid in ID_FIND_RE.findall(lead.group('ids')):
                if sid not in ids:
                    ids.append(sid)
    return ids


PARSE_WARNING_LIMIT = 10
PARSE_WARNING_FRAGMENT = 80
NO_ITEMS = {'', '-', '—', 'nic', 'brak', 'nie', 'żadne', 'żaden', 'none', 'no', 'n/a', 'nothing'}


def report_warnings(text):
    """Pieces of the `w toku` / `zrobione` lists of one checkpoint text that the id reading skips: `[{segment, fragment, reason}]`.
    `no-step-id`: an element without a step id at its start; `later-id`: an element that starts with an id and names another one
    further on outside brackets (only the leading ids count). The skip stays (no guessing from descriptions); the warning makes it visible.
    `unbalanced-bracket` (segment `raport`): the text has an opening bracket that is never closed, so `split_segments` falls back to every `;`
    and a step id after such a `;` is lost without any other trace; the fragment starts at the element that holds the unclosed bracket."""
    found = []
    unbalanced = unclosed_bracket_start(text) is not None   # decided on the text as written
    text = redact(text)   # the WHOLE text is masked before anything is cut out of it: a key before the cut (`password: x (a; F1.2`) must still mask its value
    if unbalanced:
        opener = unclosed_bracket_start(text)   # the masking can remove brackets that were part of a secret: then the fragment starts at the beginning
        start = 0 if opener is None else max(text.rfind(sep, 0, opener) for sep in ';,:') + 1   # the element that holds the bracket
        found.append({'segment': 'raport', 'fragment': ' '.join(text[start:].split())[:PARSE_WARNING_FRAGMENT], 'reason': 'unbalanced-bracket'})
    for label, pattern, english in (('w toku', IN_PROGRESS_RE, IN_PROGRESS_EN_RE), ('zrobione', DONE_RE, DONE_EN_RE)):
        match = _keyword_match(pattern, english, text)
        if not match:
            continue
        for item in split_top_level(match.group('seg')):
            piece = ' '.join(item.split())
            if piece.rstrip('.').lower() in NO_ITEMS:
                continue
            lead = ID_LIST_RE.match(item)
            if not lead:
                reason = 'no-step-id'
            elif ID_FIND_RE.search(_without_brackets(item[lead.end():])):
                reason = 'later-id'
            else:
                continue
            found.append({'segment': label, 'fragment': piece[:PARSE_WARNING_FRAGMENT], 'reason': reason})
    return found


def _without_brackets(text):
    out, depth = [], 0
    for char in text:
        if char in '([{':
            depth += 1
        elif char in ')]}' and depth:
            depth -= 1
        elif not depth:
            out.append(char)
    return ''.join(out)


def checkpoint_warnings(chronological):
    """`run.parseWarnings`: the newest `PARSE_WARNING_LIMIT` warnings of the run, newest first, each with the checkpoint `time`."""
    out = []
    for item in reversed(chronological):
        for warning in report_warnings(item['text']):
            out.append({'time': item['time'], **warning})
            if len(out) >= PARSE_WARNING_LIMIT:
                return out
    return out


def ids_reported_done(text):
    """Ids after "zrobione" (up to `;`): a visual hint only, it does not change the percentage."""
    match = _keyword_match(DONE_RE, DONE_EN_RE, text)
    return _lead_ids(match.group('seg')) if match else []


def ids_in_progress(text):
    """Step ids after "w toku" up to the next `;`, only items that start with an id; None when the phrase is absent."""
    match = _keyword_match(IN_PROGRESS_RE, IN_PROGRESS_EN_RE, text)
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
    """(assumptions, blocking) from the spec's `## Pytania otwarte` / `## Open questions` section (contract: `assumptions`, `blockingQuestions`).

    `- Q<n> [ZAŁOŻENIE]: <question> — przyjęte: <value>` (em/en dash or hyphen; English `[ASSUMPTION]` … `accepted:`) is an assumption;
    `- Q<n>: <question>` and a table row `| Q<n> | <question> | …` are blocking; other bracket tags (e.g. `[ROZSTRZYGNIĘTE …]`,
    `[RESOLVED …]`) are skipped."""
    assumptions, blocking, seen = [], [], set()
    inside = in_fence = False
    for line in file_lines(text):
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

PLAN_CACHE_MAX = 512
_PLAN_CACHE: dict = {}
_PLAN_CACHE_LOCK = threading.Lock()


def _plan_content(path):
    """(title, status, spec, run, complete) of a plan file, parsed once per (path, mtime_ns, size): `build_status` runs on
    every change of the project and on every poll, and a plan is read whole to count its steps. None when unreadable.
    Time-dependent parts (`activity`, `runAlive`) are never cached: they come from the run directory on every call."""
    try:
        info = path.stat()
    except OSError:
        return None
    key, stamp = str(path), (info.st_mtime_ns, info.st_size)
    with _PLAN_CACHE_LOCK:
        hit = _PLAN_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        text = read_masked(path, HEADER_BYTES)
    except OSError:
        return None
    title, fields = parse_header(text)
    try:
        steps = [step for phase in parse_progress(read_masked(path)) for step in phase['steps']]
    except OSError:
        steps = []
    content = (title, doc_status(text)[0], first_token(fields.get('spec')), first_token(fields.get('przebieg')),
               bool(steps) and all(step['done'] for step in steps))
    with _PLAN_CACHE_LOCK:
        if len(_PLAN_CACHE) >= PLAN_CACHE_MAX:
            _PLAN_CACHE.clear()
        _PLAN_CACHE[key] = (stamp, content)
    return content


def list_plans(root):
    plans = []
    for path in sorted((root / 'docs/plans').glob('*.md')):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        content = _plan_content(path)
        if content is None:
            continue
        title, status, spec, run, complete = content
        mtime = _mtime(path) or 0
        alive, activity = False, mtime
        directory = run_dir(root, run)
        if directory is not None:
            alive = True
            activity = max([activity] + [stamp for stamp in (_mtime(directory / name) for name in ('checkpoints.md', 'approvals.jsonl'))
                                         if stamp])
        plans.append({'path': rel, 'title': title or path.stem, 'status': status,
                      'spec': spec, 'run': run, 'mtime': mtime,
                      'complete': complete, 'runAlive': alive, 'activity': activity})
    return plans


def list_specs(root):
    specs = []
    for path in sorted((root / 'docs/specs').glob('*.md')):
        rel = _posix_rel(root, path)
        if rel is None or resolve_doc(root, rel) is None:
            continue
        try:
            text = read_masked(path, HEADER_BYTES)
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


EXPERT_QUESTIONS_FILE = 'questions.json'
EXPERT_QUESTIONS_MAX_BYTES = 2 * 1024 * 1024  # the server writes up to 1 MiB plus one operation; the margin keeps such a file counted
EXPERT_QUESTION_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
EXPERT_QUESTION_TYPES = ('ask', 'answer', 'comment', 'recorded', 'withdraw')


def _read_questions(path):
    """`(asked, closed)` of one `questions.json` (`expert-questions/1`), read like the prototype server folds it: `asked` = `{questionId: its
    first `ask` operation}` in order of asking; `closed` = `{questionId: 'answered'|'withdrawn'}` from the first `answer` or `withdraw`
    of it. Operations without id/at/questionId/known type are skipped. None for an unreadable or malformed file."""
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(EXPERT_QUESTIONS_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > EXPERT_QUESTIONS_MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, RecursionError):
        return None
    version = data.get('questionsFormat') if isinstance(data, dict) else None
    operations = data.get('operations') if type(version) is int and version == 1 else None
    if not isinstance(operations, list):
        return None
    usable = [op for op in operations
              if isinstance(op, dict) and isinstance(op.get('id'), str) and isinstance(op.get('at'), str)
              and isinstance(op.get('questionId'), str) and op.get('type') in EXPERT_QUESTION_TYPES]
    asked, closed, seen = {}, {}, set()
    # the same order and de-duplication as the server's fold: operations by (at, id), an `ask` of a question first,
    # an operation whose id was already seen is skipped
    ordered = sorted(usable, key=lambda op: (op['at'], op['id']))
    for op in ordered:
        if op['type'] == 'ask' and op['id'] not in seen:
            seen.add(op['id'])
            asked.setdefault(op['questionId'], op)
    for op in ordered:
        if op['type'] == 'ask' or op['id'] in seen:
            continue
        seen.add(op['id'])
        if op['type'] in ('answer', 'withdraw'):
            closed.setdefault(op['questionId'], 'answered' if op['type'] == 'answer' else 'withdrawn')
    return asked, closed


def _open_questions(path):
    """`(open, blocking)` of one `questions.json`: a question is open from its first `ask` until an `answer` or `withdraw` of it.
    None for an unreadable or malformed file."""
    folded = _read_questions(path)
    if folded is None:
        return None
    asked, closed = folded
    still_open = [op for qid, op in asked.items() if qid not in closed]
    return len(still_open), sum(1 for op in still_open if op.get('blocking') is True)


def _addressees(op):
    """Logins an `ask` is addressed to: a sorted list of strings; empty = every expert of the run."""
    value = op.get('addressees')
    return sorted({item for item in value if isinstance(item, str) and item}) if isinstance(value, list) else []


QUESTION_TEXT_LIMIT = 160
QUESTION_ITEMS_LIMIT = 50


def _question_items(slug, asked, closed):
    """`expertQuestions.items[]` entries of one run: `{id, runSlug, text (up to 160 characters), blocking, addressees, state, at, url}`;
    `state` = `open`, `answered` or `withdrawn`."""
    items = []
    for qid, op in asked.items():
        text = ' '.join(redact(str(op.get('text') or '')).split())   # masked whole, then trimmed and cut
        items.append({'id': qid, 'runSlug': slug, 'text': text[:QUESTION_TEXT_LIMIT], 'blocking': op.get('blocking') is True,
                      'addressees': _addressees(op), 'state': closed.get(qid, 'open'), 'at': op['at'], 'url': f'/_experts/{slug}'})
    return items


def _expert_questions_pass(root):
    """`(expertQuestions without `current`, {slug: (asked, closed)})`: one reading of every `questions.json` of the project
    (`docs/prototypes/<slug>/questions.json`), shared by the project view and the view of the run (`build_current_questions`)."""
    base = prototypes_root(root)
    runs, items, folded_by_slug = [], [], {}
    if base is not None:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            names = []
        for name in names:
            if not EXPERT_QUESTION_SLUG.fullmatch(name):
                continue
            directory = base / name
            meta, questions = directory / 'prototype.json', directory / EXPERT_QUESTIONS_FILE
            if directory.is_symlink() or not directory.is_dir() or meta.is_symlink() or not meta.is_file():
                continue
            if questions.is_symlink() or not questions.is_file():
                continue
            folded = _read_questions(questions)
            if folded is None:
                continue
            folded_by_slug[name] = folded
            asked, closed = folded
            items += _question_items(name, asked, closed)
            still_open = [op for qid, op in asked.items() if qid not in closed]
            if still_open:
                everyone = any(not _addressees(op) for op in still_open)
                runs.append({'slug': name, 'open': len(still_open), 'blocking': sum(1 for op in still_open if op.get('blocking') is True),
                             'url': f'/_experts/{name}',
                             'addressees': 'all' if everyone else sorted({login for op in still_open for login in _addressees(op)})})
    items.sort(key=lambda item: (item['at'], item['id']), reverse=True)
    summary = {'open': sum(run['open'] for run in runs), 'blocking': sum(run['blocking'] for run in runs), 'runs': runs,
               'items': items[:QUESTION_ITEMS_LIMIT]}
    return summary, folded_by_slug


def build_expert_questions(root):
    """Contract field `expertQuestions` (without `current`): open questions to a domain expert across ALL runs of the project
    (`docs/prototypes/<slug>/questions.json`), not only the run of the visible spec. `runs` lists only runs with open
    questions; `url` is the path of that run's page on the prototype server (the consumer adds the origin)."""
    return _expert_questions_pass(root)[0]


def _spec_key(value):
    """A spec path as a normalized relative path (`./docs/x.md` = `docs/x.md`), or None for anything that is not a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        return None
    return posixpath.normpath(value.strip().replace('\\', '/'))


def build_current_questions(root, spec, slug, folded, overview, writing):
    """`expertQuestions.current`: the prototypes and the questions of the run of the visible spec, or None.  Bound by the spec path:
    prototypes = those whose `prototype.json` has `requirements` equal to `spec.path` (both as normalized relative paths), plus the prototype named by `slug` (the `Prototyp:`
    line) when it exists; questions = the `ask`s of ALL prototypes whose `origin.spec` is that path (a normalized relative path; an `ask`
    without `origin` is only in the project view).  `folded` = `{slug: (asked, closed)}` from the single reading of `questions.json`;
    `origin.spec` is only a filter and never leaves."""
    target = _spec_key(spec.get('path')) if spec.get('exists') else None
    base = prototypes_root(root)
    if target is None or base is None:
        return None
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    rows, items, still_open = [], [], []
    for name in names:
        if not EXPERT_QUESTION_SLUG.fullmatch(name):
            continue
        directory = base / name
        meta = directory / 'prototype.json'
        if directory.is_symlink() or not directory.is_dir() or meta.is_symlink() or not meta.is_file():
            continue
        if len(rows) < PROTOTYPES_LIMIT and (name == slug or _prototype_requirements(meta) == target):
            info = overview(name)
            if info is not None:
                rows.append({'slug': name, **info, 'writing': writing(name) if writing else None})
        if name in folded:
            asked, closed = folded[name]
            mine = {qid: op for qid, op in asked.items()
                    if isinstance(op.get('origin'), dict) and _spec_key(op['origin'].get('spec')) == target}
            items += _question_items(name, mine, closed)
            still_open += [op for qid, op in mine.items() if qid not in closed]
    if not rows and not items:
        return None
    items.sort(key=lambda item: (item['at'], item['id']), reverse=True)
    return {'spec': spec['path'], 'prototypes': rows, 'open': len(still_open),
            'blocking': sum(1 for op in still_open if op.get('blocking') is True), 'items': items[:QUESTION_ITEMS_LIMIT]}


def _prototype_requirements(meta):
    """`requirements` of a prototype as a normalized spec path (wider than the raw comparison of `find_prototype`: `./docs/x.md` matches `docs/x.md`), or None."""
    try:
        data = json.loads(read_text(meta, 64_000))
    except (OSError, ValueError, RecursionError):
        return None
    return _spec_key(data.get('requirements')) if isinstance(data, dict) else None


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


def _comments_after_acceptance(directory, slug):
    """`{count, screens: [{id, count}]}` (Q6): messages (a new thread or a reply, not retracted) newer than the last VALID acceptance of
    their screen, in threads that are still undecided (`open`, `awaiting`); stateless (no "read" mark), so it goes away when the thread is
    decided. None when the comment log cannot be read."""
    from . import prototype_server as server
    from .prototype import MAX_COMMENTS_READ, read_regular_bytes
    path = directory / server.COMMENTS_FILE
    if not os.path.lexists(path):
        return {'count': 0, 'screens': []}
    data, _reason = read_regular_bytes(path, MAX_COMMENTS_READ)
    if data is None:
        return None
    fold = server.fold_operations(server.parse_document(data.decode('utf-8'), slug).operations)
    hashes = server.screen_hashes(str(directory))
    accepted = server.acceptance_state(fold, hashes['screens'], hashes.get('aliases'))
    per_screen = {}
    for thread in fold['threads']:
        entry = accepted.get(thread.get('screen'))
        if not entry or not entry['valid'] or thread['status'] not in ('open', 'awaiting'):
            continue
        since = parse_iso(entry['at'])
        if since is None:
            continue
        newer = sum(1 for message in thread['messages']
                    if not message.get('retracted') and (parse_iso(message.get('at')) or since) > since)
        if newer:
            per_screen[thread['screen']] = per_screen.get(thread['screen'], 0) + newer
    return {'count': sum(per_screen.values()), 'screens': [{'id': name, 'count': per_screen[name]} for name in sorted(per_screen)]}


def _safe_after_acceptance(directory, slug):
    """`_comments_after_acceptance`, or zero comments when the log is unreadable or malformed (the prototype stays on the page)."""
    try:
        return _comments_after_acceptance(directory, slug) or {'count': 0, 'screens': []}
    except Exception:
        return {'count': 0, 'screens': []}


def prototype_overview(root, slug):
    """One prototype for the page (`prototypes[]` without `slug` and `writing`): `{iteration, screens, accepted, stale, threads,
    commentsAfterAcceptance}` (in-process, read-only, the readers of `prototype list` and the prototype server), or None."""
    if not slug or not re.fullmatch(r'[A-Za-z0-9][\w.-]*', slug):
        return None
    base = prototypes_root(root)
    directory = base / slug if base is not None else None
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return None
    try:
        from .prototype import prototype_comments_info
        fields = prototype_comments_info(directory, slug)[0]
        acceptance = fields.get('acceptance') if isinstance(fields.get('acceptance'), dict) else {}
        threads = fields.get('threads') if isinstance(fields.get('threads'), dict) else {}
        return {'iteration': fields.get('iteration'), 'screens': int(acceptance.get('screens') or 0),
                'accepted': int(acceptance.get('accepted') or 0), 'stale': int(acceptance.get('stale') or 0),
                'threads': {key: value for key, value in threads.items() if isinstance(value, int) and not isinstance(value, bool)},
                'commentsAfterAcceptance': _safe_after_acceptance(directory, slug)}
    except Exception:  # an unreadable prototype must not break the status page
        return None


PROTOTYPES_LIMIT = 50


def build_prototypes(root, overview, writing):
    """`prototypes[]`: every prototype of the project (up to 50, by slug) with its acceptance, threads and comments after acceptance."""
    base = prototypes_root(root)
    if base is None:
        return []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        if not EXPERT_QUESTION_SLUG.fullmatch(name):
            continue
        directory = base / name
        meta = directory / 'prototype.json'
        if directory.is_symlink() or not directory.is_dir() or meta.is_symlink() or not meta.is_file():
            continue
        info = overview(name)
        if info is None:
            continue
        out.append({'slug': name, **info, 'writing': writing(name) if writing else None})
        if len(out) >= PROTOTYPES_LIMIT:
            break
    return out


def prototype_writing(root, slug, now_ms=None):
    """`{since, expiresAt, renewedAt}` of a valid writing marker of the prototype (`.writing.json`, in-process, read-only, the
    same reader as the prototype server; never `by`), or None.  The clock is `now_ms` (UTC milliseconds), by default the host's."""
    if not slug or not re.fullmatch(r'[A-Za-z0-9][\w.-]*', slug):
        return None
    base = prototypes_root(root)
    directory = base / slug if base is not None else None
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return None
    try:
        from .prototype_server import public_writing
        return public_writing(str(directory), int(time.time() * 1000) if now_ms is None else int(now_ms))
    except Exception:  # an unreadable marker must not break the status page
        return None


def risky_sections(text):
    """Spec sections `## Kontrakty|Migracja|Dane` (only the Polish titles; the English ones return with the title-starts-with-alias rule of the aliases package) with content (not "nie dotyczy"/"not applicable"): pre-implement is needed before GO plan."""
    found = []
    for name in RISKY_SECTIONS:
        match = re.search(rf'^##\s+{name}\b[^\n]*\n(?P<body>.*?)(?=^#{{1,6}}\s|\Z)', text, re.MULTILINE | re.DOTALL)
        body = (match.group('body') if match else '').strip()
        if body and not re.match(r'^[-*\s]*(?:nie dotyczy|not applicable)', body, re.IGNORECASE):
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
    for line in file_lines(text):
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
        return packed.is_file() and any(line.endswith(' ' + ref) for line in file_lines(read_text(packed)))
    except OSError:
        return False


def git_locations(root):
    """`(git_dir, common_dir, linked)` of the project from `.git` only (git is not run): `.git` is a directory, or a file
    `gitdir: <path>` (relative to `root`) naming the git directory; only there a `commondir` file marks a linked worktree and
    names the common directory (relative to the git directory; `linked` is true). A `.git` directory is always the main
    checkout, even with a `commondir` file inside. `None` when the `.git` file has no `gitdir:` line. File errors
    (`OSError`, `ValueError` for a NUL byte) are the caller's: both `read_git` and `read_worktree` catch them."""
    git = root / '.git'
    common, linked = git, False
    if git.is_file():
        match = re.match(r'gitdir:\s*(.+)', read_text(git, 4096).strip())
        if not match:
            return None
        git = common = (root / match.group(1).strip()).resolve()
        if (git / 'commondir').is_file():
            common, linked = (git / read_text(git / 'commondir', 4096).strip()).resolve(), True
    return git, common, linked


def read_worktree(root):
    """`project.worktree`: `{kind: main|worktree, name, path, mainPath}`, or None (no `.git`, a subdirectory of a repository,
    a damaged linked worktree, any read error). `mainPath` is the parent of the common git directory of a linked worktree
    when that directory is named `.git`, else None; it is resolved, `path` is `root` as given."""
    try:
        if not (root / '.git').exists():
            return None
        locations = git_locations(root)
        if locations is None:
            return None
        git, common, linked = locations
        if not git.is_dir():
            return None
        info = {'kind': 'main', 'name': root.name, 'path': posix(root), 'mainPath': None}
        if linked:
            info['kind'] = 'worktree'
            if common.name == '.git':
                info['mainPath'] = posix(common.parent)
        return info
    except (OSError, ValueError):
        return None


def read_git(root):
    """Branch, HEAD and origin of the project repository read from `.git` files (git is not run)."""
    info = {'branch': None, 'head': None, 'headFull': None, 'remote': None, 'branchUrl': None, 'branchPushed': None}
    try:
        locations = git_locations(root)
        if locations is None:
            return info
        git, common, _ = locations
        head = read_text(git / 'HEAD', 4096).strip()
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
                    for line in file_lines(read_text(packed)):
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
    except (OSError, ValueError):
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


COMMITS_SHOWN = 20          # commits listed under the "not pushed" badge; the rest is only counted (`commitsMore`)
COMMIT_SUBJECT_MAX = 80     # characters of a commit subject on the page
_ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_CONTROL_CHARS = re.compile('[\x00-\x1f\x7f-\x9f]')
_INVISIBLE = re.compile('[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]')   # zero-width and bidirectional marks
_SHORT_SHA = re.compile(r'[0-9a-f]{4,64}')


def _commit_list(out):
    """`git log -z --format=%h%x1f%s` (bytes) as `[{'sha', 'subject'}]`: decoded explicitly (`decode_text`), every subject masked
    WHOLE (`redact`) before it is flattened and cut to COMMIT_SUBJECT_MAX characters (a cut subject ends with `…`), so a key before the cut still masks its value."""
    commits = []
    for record in out.split(b'\x00'):
        sha, _, subject = decode_text(record).partition('\x1f')
        sha = sha.strip()
        if _SHORT_SHA.fullmatch(sha):   # a record without a hash (a signature line of the user's git configuration) is dropped
            clean = _INVISIBLE.sub('', _ANSI.sub('', subject))   # before the mask: a zero-width mark inside a key must not hide the key from it
            flat = ' '.join(_CONTROL_CHARS.sub(' ', redact(clean)).split())
            commits.append({'sha': sha, 'subject': flat if len(flat) <= COMMIT_SUBJECT_MAX else flat[:COMMIT_SUBJECT_MAX - 1].rstrip() + '…'})
    return commits


def git_ahead(root, branch):
    """How many commits HEAD has that the remote counterpart of the working branch has not (read-only): `origin/<branch>..HEAD`,
    or `HEAD --not --remotes=origin` when the branch is not on origin. "CP" pushes the working branch, not the base.
    `commits` lists the newest COMMITS_SHOWN of them (`sha`, masked `subject`; one `git log`), `commitsMore` counts the older ones;
    `commits` is None when the count is unknown."""
    def git(*args, raw=False):
        try:
            out = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=not raw, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout if raw else out.stdout.strip()
    if not branch or not REF_NAME_RE.match(branch):
        return {'remote': None, 'pushed': None, 'ahead': None, 'commits': None, 'commitsMore': 0}
    remote = f'origin/{branch}'
    if git('rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}^{{commit}}'):
        count, pushed, rng = git('rev-list', '--count', f'{remote}..HEAD'), True, [f'{remote}..HEAD']
    else:
        count, pushed, rng = git('rev-list', '--count', 'HEAD', '--not', '--remotes=origin'), False, ['HEAD', '--not', '--remotes=origin']
    ahead = int(count) if count and count.isdigit() else None
    commits = None
    if ahead is not None:
        raw = git('log', '-z', '--no-color', '--no-ext-diff', '--no-show-signature', '--encoding=UTF-8', '--abbrev=7', f'-n{COMMITS_SHOWN}', '--format=%h%x1f%s', *rng, raw=True) if ahead else b''
        commits = _commit_list(raw) if raw is not None else None
    more = max(ahead - len(commits), 0) if ahead is not None and commits is not None else 0
    return {'remote': remote, 'pushed': pushed, 'ahead': ahead, 'commits': commits, 'commitsMore': more}


_HANDOVER_WRAP = re.compile(r'^(\*\*|__|\*|_|`)(.+)\1$')


def handover_value(plan_fields):
    """The value of the plan line `Oddanie:`, or None when the line is absent or empty. Markdown wrapped around the whole value (`` `main` ``,
    `**main**`) is removed as a pair; a lone `*` or `_` is a character of the text, not markup (`x*` is not the branch `x`)."""
    value = (plan_fields.get('oddanie') or '').strip()
    while True:
        wrapped = _HANDOVER_WRAP.match(value)
        if not wrapped:
            break
        value = wrapped.group(2).strip()
    return value or None


def _is_handover_branch(value):
    return bool(value) and value.lower() not in HANDOVER_PLACEHOLDERS and valid_branch_name(value)


def delivery_target(delivery, branch, plan_fields):
    """Where "CP" delivers: the working branch; in `direct` mode the plan line `Oddanie: <branch>` (the user's answer), but only when the WHOLE
    value is one valid branch name. Any other text (a placeholder, a note after the name) is ignored: it is not a branch."""
    handover = handover_value(plan_fields)
    if delivery.get('mode') == 'direct' and _is_handover_branch(handover):
        return handover
    return branch


def handover_ignored(delivery, plan_fields):
    """The value of `Oddanie:` that `delivery_target` ignored (the plan has the line, `direct` mode, but the value is not a branch name), else None."""
    handover = handover_value(plan_fields)
    if delivery.get('mode') == 'direct' and handover and not _is_handover_branch(handover):
        return handover
    return None


def delivery_words(delivery, ahead, plan, waiting, branch=None, target=None, ignored=None, lang='pl'):
    """Delivery words of act III: `CP` always, `READY` only in `pr` mode. Active when the plan is `done`, the session waits
    for the word or the working branch has commits origin does not have; `tip` says where and how many. `ignored`: the `Oddanie:` value that was
    not a branch name (`handover_ignored`); the `CP` tip says so, without repeating the text."""
    if delivery.get('mode') == 'invalid':
        return []
    waits = {(item.get('word') or '').rstrip('!') for item in waiting}
    done = bool(plan) and plan.get('status') == 'done'
    count = ahead.get('ahead')
    target = target or branch
    where = f'origin/{target}' if target else 'origin'
    if target and branch and target != branch:
        where = i18n.t(lang, 'srv.delivery.whereFrom', where=where, branch=branch)
    out = []
    for word in ['CP'] + (['READY'] if delivery.get('mode') == 'pr' else []):
        reasons = []
        if word in waits:
            reasons.append(i18n.t(lang, 'srv.delivery.reasonWaits', word=word))
        if done:
            reasons.append('plan done')
        if count:
            remote = ahead.get('remote') if ahead.get('pushed') else i18n.t(lang, 'srv.delivery.noOrigin')
            reasons.append(i18n.t(lang, 'srv.delivery.unpushed', n=count, remote=remote))
        tip = '; '.join(reasons) if reasons else i18n.t(lang, 'srv.delivery.allPushed' if count == 0 else 'srv.delivery.nothing')
        if word == 'CP':
            tip = i18n.t(lang, 'srv.delivery.cpTipIgnored' if ignored else 'srv.delivery.cpTip', where=where, tip=tip)
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
        lines = file_lines(path.read_text(encoding='utf-8', errors='replace'))
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


def _given_tip(word, entry, lang='pl'):
    at = _hhmm(entry)
    return i18n.t(lang, 'srv.consent.givenAt', word=word, at=at).rstrip() if at else i18n.t(lang, 'srv.consent.givenAlready', word=word)


def _consent_current(entry, plan_state, tracked):
    """K13: does the entry (same sha as HEAD) still stand for the plan in its current status?"""
    if 'planState' in entry:
        return entry['planState'] == plan_state
    if plan_state != 'done':
        return True
    return (tracked() if callable(tracked) else tracked) is not False


def consent_buttons(spec, plan, waiting, words, log, head_full, run_ok, run_plan_state=None, tracked=True, lang='pl'):
    """Consent buttons of the act tiles, each in one of the states: before (the act has not come) = no entry; `waiting` =
    visible and active (the session waits for this consent); `running` = visible, inactive, `reason` "czeka na…";
    `after` = visible, inactive, `reason` "zgoda dana HH:MM" (already given; no second entry). The "waiting" signal is
    written by the session: `czeka na GO plan|GO implement` in the spec/plan status or `czeka:` in a checkpoint.
    CP/READY `after` (K13): the last entry of the word, with the sha of HEAD and `planState` equal to the current status of the plan of the run
    (`run_plan_state`, default the status of the shown plan); an entry without the field counts by sha alone, except with `tracked`
    false (`tracked` = bool or callable) and a `done` plan: there it gives no `after`, or an old consent would block a new one."""
    out = []
    waits = {(item.get('word') or '').rstrip('!').upper() for item in waiting}
    last = {entry.get('word'): entry for entry in log}
    acc = spec.get('prototypeAcceptance') or {}
    proto_open = bool(acc.get('screens')) and (acc.get('accepted', 0) < acc['screens'] or acc.get('stale'))
    proto_reason = i18n.t(lang, 'srv.consent.protoPending', accepted=acc.get('accepted', 0), screens=acc.get('screens', 0))
    plan_status = (plan or {}).get('status')
    plan_text = ((plan or {}).get('statusText') or '').lower()
    sketch = bool(plan) and ('szkic' in plan_text or 'sketch' in plan_text or 'czeka na spec' in plan_text or 'waiting for spec' in plan_text)
    spec_text = (spec.get('statusText') or '').lower()

    def add(kind, state, reason, path, sha, given=None):
        out.append({'kind': kind, 'word': APPROVE_KINDS[kind], 'state': state, 'reason': reason, 'path': path,
                    'sha': sha or '', 'given': given, 'blocked': None if state == 'waiting' else reason})

    # Act I -> "GO plan": only with a spec.
    if spec.get('exists'):
        if spec.get('status') in SPEC_DONE:
            add('spec', 'after', _given_tip('GO plan', last.get('GO plan'), lang), spec['path'], spec.get('fileSha'),
                last.get('GO plan') or {'at': None})
        elif spec.get('status') == 'draft' and (not plan or sketch):
            signal = ('GO PLAN' in waits or any(phrase in text for text in (spec_text, plan_text)
                                                for phrase in ('czeka na go plan', 'waiting for go plan')))
            reason = (proto_reason if proto_open
                      else None if signal else i18n.t(lang, 'srv.consent.specLoop'))
            add('spec', 'waiting' if reason is None else 'running', reason, spec['path'], spec.get('fileSha'))
    # Act II -> "GO implement": a plan other than a sketch.
    if plan and not sketch:
        if plan_status in PLAN_APPROVED:
            add('plan', 'after', _given_tip('GO implement', last.get('GO implement'), lang), plan['path'], plan.get('fileSha'),
                last.get('GO implement') or {'at': None})
        elif plan_status == 'draft':
            signal = 'GO IMPLEMENT' in waits or 'czeka na go implement' in plan_text or 'waiting for go implement' in plan_text
            reason = (i18n.t(lang, 'srv.consent.needGoPlan') if spec.get('exists') and spec.get('status') != 'approved'
                      else proto_reason if proto_open
                      else i18n.t(lang, 'srv.consent.actTwo') if not signal
                      else None)
            add('plan', 'waiting' if reason is None else 'running', reason, plan['path'], plan.get('fileSha'))
    # Act III -> "CP" / "READY": from the start of the implementation (plan in_progress or done).
    if plan_status in ('in_progress', 'done'):
        for word in words:
            kind = word['word'].lower()
            entry = last.get(word['word'])   # the last entry of the word decides (as before K13)
            if entry and head_full and entry.get('sha') == head_full and _consent_current(entry, run_plan_state or plan_status, tracked):
                add(kind, 'after', _given_tip(word['word'], entry, lang) + f' (HEAD {head_full[:8]})', None, head_full, entry)
                out[-1]['target'] = entry.get('target') or word.get('target')
                continue
            reason = None if word['active'] else i18n.t(lang, 'srv.consent.realization', tip=word['tip'])
            if reason is None and not head_full:
                reason = i18n.t(lang, 'srv.consent.noHead')
            elif reason is None and not run_ok:
                reason = i18n.t(lang, 'srv.consent.noRunDir', dir='.skillsync/runs/<run-id>')
            add(kind, 'waiting' if reason is None else 'running', reason or word['tip'], None, head_full)
            out[-1]['reason'] = reason if reason else word['tip']
            out[-1]['target'] = word.get('target')
    return out


def approvals_state(root, spec, plan, delivery, run_id, head_full, log, waiting, enabled=True, tracked=True, lang='pl'):
    """`approvals {enabled, actions[], last}`; every action carries `run` (the run directory the consent is written to, or
    null when it does not exist); `path` of `cp`/`ready` is the run id."""
    if not enabled:
        return {'enabled': False, 'actions': [], 'last': log[-1] if log else None}
    run_ok = run_dir(root, run_id) is not None
    run_state = plan_state_for_run(root, run_id) if run_ok else None   # K13: the plan of the run, not the plan shown on the page
    if callable(tracked):   # one `specs_info` (git) per status build, not one per consent word
        probe, cache = tracked, []
        tracked = lambda: cache[0] if cache else (cache.append(probe()) or cache[0])
    actions = consent_buttons(spec, plan, waiting, delivery.get('words', []), log, head_full, run_ok, run_state, tracked, lang)
    for action in actions:
        if action['kind'] in ('cp', 'ready'):
            action['path'] = run_id
            if run_state is not None:
                action['planState'] = run_state   # the state the user saw; the click sends it back (409 when it changed)
        action['run'] = run_id if run_ok else None
    return {'enabled': True, 'actions': actions, 'last': log[-1] if log else None}


# --------------------------------------------------------------------------- model

def _stage_token(text):
    return re.sub(r'[\s\-]+', '_', re.sub(r'\s*(×\d+|\d+/\d+)\s*$', '', str(text or '').strip().lower())).strip('_')


_STAGE_PLACEHOLDERS = re.compile(r'\s*(?:×\s*\{n\}|\{accepted\}\s*/\s*\{screens\})\s*$')


@lru_cache(maxsize=None)
def _stage_label_groups():
    """Stage label tokens by catalog key (`srv.stage.*`): the labels of every supported language of one stage (`Szkic` and `Draft`)."""
    groups = {}
    for lang in i18n.SUPPORTED_LANGUAGES:
        for key, value in i18n.catalog(lang).items():
            if key.split('.')[:2] == ['srv', 'stage'] and isinstance(value, str):
                groups.setdefault(key, set()).add(_stage_token(_STAGE_PLACEHOLDERS.sub('', value)))
    return tuple(frozenset(group) for group in groups.values())


def _trwa_since(trwa, act, open_act, key, label):
    """Time of the checkpoint since which `trwa:` names this stage, or None: the key of the stage (`stage_` dropped) or its label
    in any supported language (`Review ×2` = `review`, `Szkic` = `Draft`); a plain `review` means the review of the act that is still open."""
    if not trwa:
        return None
    names = {_stage_token(label)}
    if key:
        names.add(_stage_token(key[6:] if key.startswith('stage_') else key))
    for group in _stage_label_groups():
        if names & group:
            names |= group
    for name in sorted(names):
        if name in trwa:
            if name == 'review' and act != open_act:
                continue
            return trwa[name] or ''
    return None


def _stage(label, done, tip='', key=None):
    """Stage tile; `key` = description key in the skill's `descriptions.json`."""
    return {'label': label, 'done': done, 'tip': tip, 'key': key}


_REVIEW_ROUND = {
    'spec': re.compile(r'spec-review(?:-[a-z0-9.]+)*-r?\d+\.md'),
    'plan': re.compile(r'plan-review(?:-[a-z0-9.]+)*-r?\d+\.md'),
    'code': re.compile(r'(?:code-review(?:-[a-z0-9.]+)*-r?\d+|final-review(?:-[a-z0-9.]+)*(?:-r?\d+)?)\.md'),
}
_DECISIONS = re.compile(r'(?:^|-)(?:decisions|decyzje)(?:-|\.|$)')


def review_rounds(names, kind):
    """Review rounds of `kind` (`spec`, `plan`, `code`): only a frozen report `<step>-N.md` (`.md`, a number at the end, optionally
    `rN`; legacy `final-review[-<word>...][-N].md` counts as code) is a round. A name with a `decisions`/`decyzje` segment
    anywhere, `.log`, `.json` and other files next to the report are not."""
    pattern = _REVIEW_ROUND[kind]
    return sum(1 for name in names if pattern.fullmatch(name) and not _DECISIONS.search(name))


def compute_acts(spec, plan, progress, run_files, extra=None, lang='pl'):
    extra = extra or {}
    names = [name.rsplit('/', 1)[-1].lower() for name in run_files]
    spec_reviews = review_rounds(names, 'spec')
    plan_reviews = review_rounds(names, 'plan')
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

    trwa = extra.get('trwa') or {}
    writing_on = extra.get('writingSince')   # HH:MM of a valid writing marker (the prototype stage is going on) or None
    open_act = next((key for key, state in (('spec', spec_state), ('plan', plan_state), ('real', real_state)) if state != 'done'), 'real')

    def stage(act, label, done, tip='', key=None, auto=None):
        """`_stage` plus `state` (`done`, `active`, `planned`) and `since` (HH:MM) of an active one: `active` from a `trwa:` key of the
        newest checkpoint or from a fact of the files (`auto`); a stage that is done stays `done` whatever the checkpoint says."""
        item = _stage(label, done, tip, key)
        since = _trwa_since(trwa, act, open_act, key, label)
        if done:
            item['state'] = 'done'
        elif since is not None or auto is not None:
            item['state'] = 'active'
            if since or auto:
                item['since'] = since or auto
        else:
            item['state'] = 'planned'
        return item

    def text(key, **params):
        return i18n.t(lang, key, **params)

    spec_stages = [
        stage('spec', text('srv.stage.draft'), spec.get('exists', False), spec.get('path') or text('srv.tip.noSpec'), key='stage_spec_draft',
              auto='' if spec.get('preparing') else None),
        stage('spec', text('srv.stage.reviewN', n=spec_reviews) if spec_reviews > 1 else text('srv.stage.review'), spec_reviews > 0,
               text('srv.tip.specReviews'), key='stage_spec_review'),
    ]
    acc = spec.get('prototypeAcceptance')
    if acc and acc.get('screens'):
        accepted = acc['accepted'] == acc['screens'] and not acc.get('stale')
        proto_tip = text('srv.tip.protoAcceptedStale' if acc.get('stale') else 'srv.tip.protoAccepted', proto=spec.get('prototype'),
                         accepted=acc['accepted'], screens=acc['screens'], stale=acc.get('stale'))
        spec_stages.append(stage('spec', text('srv.stage.protoCount', accepted=acc['accepted'], screens=acc['screens']), accepted,
                                  key='stage_prototype', auto=writing_on, tip=proto_tip))
    elif spec.get('prototype') or proto_rounds:
        spec_stages.append(stage('spec', text('srv.stage.proto'), bool(proto_rounds) or spec_state == 'done',
                                  spec.get('prototype') or text('srv.tip.protoNotes'), key='stage_prototype', auto=writing_on))
    spec_stages.append(stage('spec', text('srv.stage.approved'), spec_state == 'done', text('srv.tip.specStatus', status=spec_status or '—'),
                              key='stage_spec_approved'))
    plan_stages = [
        stage('plan', text('srv.stage.plan'), plan is not None, plan.get('path') if plan else text('srv.tip.noPlan'), key='stage_plan'),
        stage('plan', text('srv.stage.review'), plan_reviews > 0, text('srv.tip.planReviews'), key='stage_plan_review'),
        stage('plan', text('srv.stage.preImplement'), pre_impl, text('srv.tip.preImplement'), key='stage_pre_implement'),
        stage('plan', text('srv.stage.goImplement'), plan_status in ('in_progress', 'done'), text('srv.tip.planStatus', status=plan_status or '—'),
               key='stage_go_implement'),
    ]
    code_reviews = review_rounds(names, 'code')
    real_stages = [
        stage('real', text('srv.stage.codeReviewN', n=code_reviews) if code_reviews > 1 else text('srv.stage.codeReview'), code_reviews > 0,
               text('srv.tip.codeReviews'), key='code_review'),
        stage('real', text('srv.stage.closure'), plan_status == 'done', text('srv.tip.planStatus', status=plan_status or '—'), key='closure'),
    ]
    risky = extra.get('risky') or []
    spec_warning = (text('srv.act.preImplementWarning', sections=', '.join(risky))
                    if risky and not pre_impl and spec.get('exists') else None)
    current = progress.get('current')
    total, done = progress.get('total', 0), progress.get('done', 0)
    real_detail = (text('srv.act.step', index=current['index'], total=total, id=current['id'], title=current['title']) if current
                   else text('srv.act.noSteps') if not total
                   else text('srv.act.allMarked') if done == total else text('srv.act.doneOf', n=total, done=done, total=total))
    return [
        {'key': 'spec', 'roman': 'I', 'name': text('srv.act.spec'), 'state': spec_state, 'stages': spec_stages, 'warning': spec_warning,
         'detail': text('srv.act.specPreparing') if spec.get('preparing') else spec.get('sha') and text('srv.act.specSha', sha=spec['sha'][:10]),
         'status': spec_status},
        {'key': 'plan', 'roman': 'II', 'name': text('srv.act.plan'), 'state': plan_state, 'stages': plan_stages,
         'detail': (text('srv.act.planSteps', steps=text('srv.act.stepsCount', n=progress.get('total', 0)),
                         phases=text('srv.act.phasesCount', n=len(progress.get('phases', [])))) if plan
                    else text('srv.act.noPlanGiven') if spec_state == 'done'
                    else text('srv.act.noPlanWaiting') if spec.get('exists') or spec.get('preparing') else None),
         'status': plan_status},
        {'key': 'real', 'roman': 'III', 'name': text('srv.act.real'), 'state': real_state, 'stages': real_stages,
         'percent': progress.get('percent', 0), 'detail': real_detail, 'status': plan_status},
    ]


def _pending_percent(done, pending, total):
    """Segment "done per checkpoint, awaiting verification": a difference of roundings, so both segments add up."""
    if not total:
        return 0
    return round(100 * (done + pending) / total) - round(100 * done / total)


def _active_percent(settled, working, total):
    """Third segment of the progress bar: steps in progress count as half a step (a difference of roundings, so the three segments
    add up and never pass 100)."""
    if not total or not working:
        return 0
    return min(round(100 * (settled + working / 2) / total), 100) - round(100 * settled / total)


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
    working = sum(1 for phase in out_phases for step in phase['steps'] if step['state'] == 'active')
    return {
        'total': total, 'done': done_total, 'percent': round(100 * done_total / total) if total else 0,
        'pending': len(reported_set), 'pendingPercent': _pending_percent(done_total, len(reported_set), total),
        'activePercent': _active_percent(done_total + len(reported_set), working, total),
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
                        'missingData': seconds is None and (done or step['state'] == 'reported'),
                        'noData': seconds is None})
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
            if not out:   # the first checkpoint before the run start: a clock of another zone or a wrong `RUN=`, not a next day
                return None
            moment += timedelta(days=1)
        out.append(moment)
        previous = moment
    return out


def _phase_label(phase):
    """Label of a plan phase (`Faza 1` of `### Faza 1: Kontrakt`): `progress.phases` has it, `parse_progress` only the name."""
    if 'label' in phase:
        return phase['label'] or phase['name']
    label, _, short = phase['name'].partition(':')
    return label.strip() if short else phase['name']


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


def _act_times(run_start, windows, given, iso):
    """`timing.acts`: measured time of act I (spec: `RUN=` to the first `GO plan`) and act II (plan: `GO plan` to the first `GO implement`),
    the waiting windows inside subtracted exactly as for a step. Nothing is estimated: an act whose end is not in the consent file
    (consent given in the session, or the act still goes on) has `endedAt: null`, `unknown: true`, `reason: 'no-approval'`; a window
    that nothing closes inside the act gives `reason: 'open-window'`. Act II is listed only when `GO plan` is known (it is its start)."""
    first = {}
    for moment, word in given:
        first.setdefault(word, moment)
    out = []
    for key, begin, end in (('spec', run_start, first.get('GO plan')), ('plan', first.get('GO plan'), first.get('GO implement'))):
        if begin is None:
            continue
        entry = {'key': key, 'startedAt': iso(begin), 'endedAt': iso(end) if end else None, 'workSeconds': None, 'waitSeconds': None,
                 'unknown': True, 'reason': 'no-approval' if end is None else None, 'endSource': 'approval' if end else None}
        if end is not None:
            if end < begin:
                entry['reason'] = 'order'
            else:
                wait = _overlap_seconds(windows, begin, end)
                if wait is None:
                    entry['reason'] = 'open-window'
                else:
                    entry.update(workSeconds=int((end - begin).total_seconds()) - wait, waitSeconds=wait, unknown=False)
        out.append(entry)
    return out


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
        return {'available': False, 'reason': 'run start (RUN=) missing, or the first checkpoint is earlier than it, or a checkpoint time is unreadable', 'windows': [], 'steps': [],
                'stages': [], 'acts': [], 'totals': {'workSeconds': None, 'waitSeconds': None, 'windows': 0, 'unknownWindows': 0,
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
            steps.append({'id': sid, 'phase': _phase_label(phase), 'startedAt': iso(begin), 'finishedAt': iso(end),
                          'workSeconds': work, 'waitSeconds': wait, 'unknown': work is None, 'reason': reason})
        if not spans and not unknown_steps:
            continue
        wait_total = _overlap_seconds(windows, min(a for a, _ in spans), max(b for _, b in spans)) if spans else None
        stages.append({'phase': _phase_label(phase), 'name': phase['name'], 'workSeconds': work_total if spans else None,
                       'waitSeconds': wait_total, 'unknownSteps': unknown_steps,
                       'complete': bool(spans) and not unknown_steps and wait_total is not None})
    unknown_windows = sum(1 for window in windows if window['seconds'] is None)
    known_wait = sum(window['seconds'] for window in windows if window['seconds'] is not None)
    unknown_steps_total = sum(1 for step in steps if step['unknown'])
    out_windows = []
    for window in windows:
        known = window['seconds'] is not None
        out_windows.append({'what': window['what'], 'startedAt': iso(window['startAt']),
                            'endedAt': iso(window['endAt'].astimezone(window['startAt'].tzinfo)) if known else None,
                            'seconds': window['seconds'], 'source': window['source'] if known else None, 'unknown': not known})
    return {'available': True, 'reason': None, 'windows': out_windows, 'steps': steps, 'stages': stages,
            'acts': _act_times(parse_iso(run_start), windows, given, iso),
            'totals': {'workSeconds': (sum(stage['workSeconds'] or 0 for stage in stages)
                                       if any(stage['workSeconds'] is not None for stage in stages) else None),
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
        text = read_masked(path, 64_000)
    except OSError:
        return out
    out['source'] = rel
    first = next((line.strip() for line in file_lines(text) if line.strip()), '')
    match = READINESS_RE.match(first)
    out['status'] = match.group('status').upper() if match else None
    date, sha = READINESS_DATE_RE.search(text), READINESS_SHA_RE.search(text)
    out['date'] = clean_value(date.group('val')) if date else None
    out['planSha'] = sha.group('val').lower() if sha else None
    return out


def _local_hhmm(millis):
    return time.strftime('%H:%M', time.localtime(millis / 1000.0))


def writing_item(slug, writing, lang='pl'):
    """Banner entry for a valid writing marker: the agent is still writing screens, nothing to accept yet."""
    planned, done = writing.get('screensPlanned'), writing.get('screensDone')
    until = _local_hhmm(writing['expiresAt'])
    what = (i18n.t(lang, 'srv.writing.whatCount', done=done, planned=planned, until=until) if planned and done is not None
            else i18n.t(lang, 'srv.writing.what', until=until))
    return {'what': what, 'source': 'writing',
            'since': _local_hhmm(writing['since']),
            'action': i18n.t(lang, 'srv.writing.action'),
            'link': {'kind': 'prototype', 'slug': slug}}


def expert_item(expert, lang='pl'):
    """Banner entry for open blocking questions to a domain expert (link like the toast: one run or the list)."""
    runs = [run for run in (expert.get('runs') or []) if run.get('blocking')]
    single = runs[0] if len(runs) == 1 and len(expert.get('runs') or []) == 1 else None
    blocking = expert['blocking']
    return {'what': i18n.t(lang, 'srv.expert.what', n=blocking), 'source': 'expert', 'since': None,
            'action': i18n.t(lang, 'srv.expert.action'),
            'link': {'kind': 'expert', 'slug': single['slug'] if single else None,
                     'url': single['url'] if single else '/_experts'}}


RESULT_STATUSES = ('in_progress', 'done', 'implemented')
RESULT_MAX_BYTES = 4096


def build_result(plan_text, plan_info):
    """`result {text, path}`: the `## Wynik` section of the chosen plan (up to 4 KiB, as text) once the plan runs or is closed; None without
    a plan, for a plan that has not started, or without a non-empty section."""
    if not plan_info or plan_info.get('status') not in RESULT_STATUSES:
        return None
    section = plan_section(plan_text, 'Wynik') or plan_section(plan_text, 'Result')
    if not section:
        return None
    return {'text': section.encode('utf-8')[:RESULT_MAX_BYTES].decode('utf-8', 'ignore').strip(), 'path': plan_info['path']}


def trwa_stages(chronological):
    """`{stage key: HH:MM since}` of the NEWEST checkpoint's `trwa:` segment, or {}: a checkpoint with `czeka:` (the session waits,
    it does not work) or without the segment clears it. `since` = the oldest checkpoint of the unbroken run of newest ones that
    names the key."""
    if not chronological:
        return {}
    def names(item):
        return [] if wait_marker(item['text']) else [_stage_token(key) for key in trwa_marker(item['text']) or []]
    found = {key: chronological[-1]['time'] for key in names(chronological[-1])}
    running = set(found)
    for item in reversed(chronological[:-1]):
        here = names(item)
        for key in list(running):
            if key in here:
                found[key] = item['time']
            else:
                running.discard(key)   # the run of newest checkpoints naming the key ends here: `since` stays at the later time
        if not running:
            break
    return found


def link_warning(root, spec, run, spec_exists, lang='pl'):
    """One sentence about the spec -> run -> prototype link that fails, or None when the link is complete (act I only)."""
    if not spec_exists:
        return None
    if not run.get('id'):
        return i18n.t(lang, 'srv.link.noRun')
    if not run.get('exists'):
        return i18n.t(lang, 'srv.link.noRunDir', id=run['id'], dir=RUNS_DIR)
    proto = spec.get('prototype')
    base = prototypes_root(root)
    if proto and (base is None or not (base / proto.rstrip('/').rsplit('/', 1)[-1] / 'prototype.json').is_file()):
        return i18n.t(lang, 'srv.link.noPrototype', proto=proto, dir=PROTOTYPES_DIR)
    return None


def default_who(item, lang='pl'):
    """`who` of a waiting item without a `(kto: …)` suffix, from its `source` (and consent word): the expert answers `expert` items,
    the session writes `writing` items, `acceptance` (a human acceptance step) goes to the acceptor labelled "odbiór"; consents and
    prototype acceptance belong to the acceptor. None = the page keeps the generic "Sesja czeka na Ciebie"."""
    source = item.get('source')
    if source == 'expert':
        return {'role': 'expert', 'login': None, 'label': i18n.t(lang, WHO_ROLE_KEYS['expert'])}
    if source == 'writing':
        return {'role': 'agent', 'login': None, 'label': i18n.t(lang, WHO_ROLE_KEYS['agent'])}
    if source == 'acceptance':
        return {'role': 'acceptor', 'login': None, 'label': i18n.t(lang, 'srv.who.acceptance')}
    if source in ('prototype', 'plan', 'spec') or item.get('word') or item.get('approve'):
        return {'role': 'acceptor', 'login': None, 'label': i18n.t(lang, WHO_ROLE_KEYS['acceptor'])}
    return None


def waiting_items(run, spec, plan, slug, writing=None, expert=None, lang='pl'):
    """What the session waits for: the `czeka:` marker of the newest checkpoint wins; otherwise facts from the files:
    `Status: … (czeka na …)` of the plan or spec, a prototype with unaccepted screens before "GO implement" and open
    blocking questions to a domain expert.  A valid writing marker (`writing`) of the prototype comes first and replaces every
    acceptance item: there is nothing to accept while the agent still writes."""
    ctx = {'spec': spec.get('path') if spec else None, 'plan': plan.get('path') if plan else None, 'slug': slug}
    newest = run['checkpoints'][0] if run.get('checkpoints') else None
    marker = wait_marker(newest['text']) if newest else None
    items = []
    if marker:
        clean, who = split_who(marker, lang)
        items = [wait_item(clean, 'checkpoint', newest['time'], ctx, lang)]
        if who:
            items[0]['who'] = who
    else:
        for doc, source in ((plan, 'plan'), (spec, 'spec')):
            text = (doc or {}).get('statusText') or ''
            match = STATUS_WAIT_RE.search(text)
            satisfied = match and re.search(r'GO plan|GO implement', match.group('what'), re.IGNORECASE) and \
                doc.get('status') in ('approved', 'in_progress', 'done', 'implemented')
            if match and not satisfied and doc.get('status') in ('draft', 'approved', 'in_progress'):
                items.append(wait_item(match.group('what').strip(), source, None, ctx, lang))
                break
        acc = spec.get('prototypeAcceptance') if spec else None
        before_go = not plan or plan.get('status') in ('draft', 'approved')
        if acc and acc.get('screens') and before_go and (acc['accepted'] < acc['screens'] or acc.get('stale')):
            what = i18n.t(lang, 'srv.wait.protoAcceptance', slug=slug, accepted=acc['accepted'], screens=acc['screens'])
            if not any('prototyp' in item['what'].lower() for item in items):
                items.append(wait_item(what, 'prototype', None, ctx, lang))
        if expert and expert.get('blocking'):
            items.append(expert_item(expert, lang))
    if writing and slug:
        items = [item for item in items if item['source'] != 'prototype'
                 and not (item.get('link') or {}).get('kind') == 'prototype']
        items.insert(0, writing_item(slug, writing, lang))
    for item in items:
        if 'who' not in item:
            who = default_who(item, lang)
            if who:
                item['who'] = who
    return items


def doc_item(root, rel):
    path = resolve_doc(root, rel)
    if not path:
        return None
    try:
        text = read_masked(path, HEADER_BYTES)
    except OSError:
        return None
    title, _ = parse_header(text)
    return {'path': rel, 'title': title or path.stem, 'status': doc_status(text)[0], 'mtime': _iso(_mtime(path))}


def doc_groups(root, current, run_files, run_id, lang='pl'):
    groups = []
    task = [item for item in (doc_item(root, rel) for rel in current if rel) if item]
    if task:
        groups.append({'key': 'task', 'label': i18n.t(lang, 'srv.docs.task'), 'items': task})
    if run_files:
        items = [item for item in (doc_item(root, rel) for rel in run_files) if item]
        if items:
            groups.append({'key': 'run', 'label': i18n.t(lang, 'srv.docs.run', id=run_id), 'items': items})
    for directory in DOC_DIRS:
        items = []
        for path in sorted((root / directory).glob('*.md'), key=lambda item: item.name, reverse=True):
            rel = _posix_rel(root, path)
            if rel and rel not in current:
                item = doc_item(root, rel)
                if item:
                    items.append(item)
        if items:
            groups.append({'key': directory.split('/')[-1], 'label': i18n.t(lang, DOC_LABELS[directory]), 'items': items})
    return groups


def _normal_plan_rel(plan):
    return plan.replace(os.sep, '/').removeprefix('./') if plan else None


def feedback_state(root):
    """The key `feedback` of the status: the states of the change-request drafts from local files only (`feedback.drafts_state`, no tracker, no write).
    A project that cannot be read as a layer project has the empty disabled state (the page must not fail for it)."""
    try:
        return feedback.drafts_state(feedback.open_session(root))
    except (feedback.FeedbackError, LayerError, OSError, ValueError, KeyError, TypeError, RecursionError, yaml.YAMLError):
        return {'enabled': False, 'total': 0, 'drafts': []}


def build_status(root, plan=None, *, manifest=None, approvals=True, session_url=None, prototype_port=PROTOTYPE_PORT,
                 version=0, ahead=None, prototype_up=None, acceptance=None, writing=None, now_ms=None, overview=None):
    """The dashboard contract (`schema: 1`) for the project at `root`. Read-only: nothing in the project is written.

    `plan` = plan path relative to the project (unknown = automatic choice, `planAuto: true`). `version`, `session_url`,
    and the probes `ahead(root, branch, head)`, `prototype_up()`, `acceptance(slug)` let a server add its live state and
    caches; `show` uses the defaults (`version` 0, no session, prototype `up` false).  `writing(slug)` is the probe of the
    writing marker (default: the file, read with `now_ms` or the host clock)."""
    root = Path(root).resolve()
    if manifest is None:
        _, manifest = read_project(root)
    lang, _language_warning = i18n.resolve_language(manifest)   # `skillsync status` reports the warning; the page keeps `pl`
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

    plan_info, fields, phases, plan_text = None, {}, [], ''
    if chosen:
        plan_path = root / chosen['path']
        text = plan_text = read_masked(plan_path)
        title, fields = parse_header(text)
        phases = parse_progress(text, lang)
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
        spec_text = read_masked(spec_path, 400_000)
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
            start, chronological = parse_checkpoints(read_masked(checkpoints))
            run['start'] = start
            run['checkpoints'] = list(reversed(chronological))
            last_change.append(_mtime(checkpoints))
            run['lastReportAt'] = _iso(_mtime(checkpoints))
            warnings = checkpoint_warnings(chronological)
            if warnings:
                run['parseWarnings'] = warnings
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
    writing_state = ((writing or (lambda name: prototype_writing(root, name, now_ms)))(slug)) if slug else None
    if writing_state:
        # `screensDone`: screens of `index.html` without the template example, counted like the acceptance (`prototype list`)
        counted = spec.get('prototypeAcceptance') or (acceptance or (lambda name: prototype_acceptance(root, name)))(slug)
        if counted:   # an unreadable prototype has no count: the key is left out rather than claim 0 screens
            writing_state = {**writing_state, 'screensDone': int(counted.get('screens') or 0)}
    acts = compute_acts(spec, plan_info, progress, run['files'],
                        {'risky': risky_sections(spec_text), 'trwa': trwa_stages(chronological),
                         'writingSince': _local_hhmm(writing_state['since']) if writing_state else None}, lang)
    acts[0]['linkWarning'] = link_warning(root, spec, run, bool(spec_path), lang)
    expert_questions, folded_questions = _expert_questions_pass(root)
    overview_of = overview or (lambda name: prototype_overview(root, name))
    writing_of = writing or (lambda name: prototype_writing(root, name, now_ms))
    prototypes = build_prototypes(root, overview_of, writing_of)
    current_questions = build_current_questions(root, spec, slug, folded_questions, overview_of, writing_of)
    current_after = (overview_of(slug) or {}).get('commentsAfterAcceptance') if slug else None
    waiting = waiting_items(run, spec if spec_path else None, plan_info, slug, writing=writing_state, expert=expert_questions, lang=lang)
    git_info = read_git(root)
    delivery = read_delivery(manifest)
    ahead_state = (ahead or (lambda base, branch, head: git_ahead(base, branch)))(root, git_info['branch'], git_info['head'])
    target = delivery_target(delivery, git_info['branch'], fields)
    delivery_state = {**delivery, 'branch': git_info['branch'], 'target': target, 'remote': ahead_state.get('remote'),
                      'pushed': ahead_state.get('pushed'), 'ahead': ahead_state.get('ahead'),
                      'commits': ahead_state.get('commits'), 'commitsMore': ahead_state.get('commitsMore') or 0,
                      'words': delivery_words(delivery, ahead_state, plan_info, waiting, git_info['branch'], target,
                                             handover_ignored(delivery, fields), lang)}
    current_docs = [spec_rel if spec_path else None, chosen['path'] if chosen else None]
    assumptions, blocking = parse_questions(spec_text) if spec_path else ([], [])
    timing = build_timing(run['start'], chronological, run_approvals(root, run_id), progress['phases'])
    status = {
        'schema': SCHEMA,
        'lang': lang,
        'app': {'name': APP_NAME, 'version': __version__},
        'project': {'name': root.name, 'path': posix(root), 'worktree': read_worktree(root)},
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
        'result': build_result(plan_text, plan_info),
        'waiting': waiting,
        'eta': build_eta(run['start'], chronological, plan_info['status'] if plan_info else None, timing),
        # Report silence: the consumer counts the minutes (no events arrive when nothing changes).
        'silence': ({'since': run.get('lastReportAt'), 'thresholdMin': SILENCE_MIN}
                    if plan_info and plan_info.get('status') == 'in_progress' and run.get('lastReportAt')
                    and not any(item.get('source') in ('checkpoint', 'expert') for item in waiting) else None),
        'approvals': approvals_state(root, spec, plan_info, delivery_state, run_id, git_info['headFull'],
                                     run_approvals(root, run_id), waiting, approvals,
                                     lambda: specs_info(root, manifest)['tracked'], lang),
        'delivery': delivery_state,
        'feedback': feedback_state(root),
        'session': {'url': session_url},
        'prototype': {'slug': slug, 'port': prototype_port, 'up': bool(slug) and bool(prototype_up and prototype_up()),
                      'writing': writing_state, **({'commentsAfterAcceptance': current_after} if current_after and current_after.get('count') else {})},
        'prototypes': prototypes,
        'docs': doc_groups(root, [rel for rel in current_docs if rel], run['files'], run_id, lang),
        'readiness': read_readiness(root, run_id, run['exists']),
        'assumptions': {'count': len(assumptions), 'items': assumptions},
        'blockingQuestions': {'count': len(blocking), 'items': blocking},
        'expertQuestions': {**expert_questions, 'current': current_questions},
        'substeps': build_substeps(progress, chronological),
        'timing': timing,
        'forecast': dashboard_forecast.build_forecast(timing, progress, plan_info['status'] if plan_info else None,
                                                      bool(chronological) and wait_marker(chronological[-1]['text']) is not None),
    }
    estimates = build_estimates(run['start'], chronological, plan_info['status'] if plan_info else None, timing)
    if estimates:
        status['estimates'] = estimates   # additive, only when the checkpoints hold `eta:` lines
    if lang != i18n.DEFAULT_LANGUAGE:   # the renderer has its Polish built in; another language brings its catalog
        status['texts'] = i18n.catalog_for_client(lang)
    return dashboard_tests.add_to_status(status, root, run_id, progress['phases'])


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
        run_start, chronological = parse_checkpoints(read_masked(checkpoints_path))
    try:
        store = _runs._store_path(root, run_id)
        data = _runs._read(store) if store.exists() or store.is_symlink() else None
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise MetricsBlocked(f'run records: {error}') from None
    records = data['checkpoints'] if data else []

    chosen = _metrics_plan(root, run_id, plan)
    started, finished, mentioned = substep_marks(chronological)
    if chosen:
        steps = [(step['id'], step['title']) for phase in parse_progress(read_masked(root / chosen['path']))
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
    report = {
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
                               parse_progress(read_masked(root / chosen['path'])) if chosen else []),
    }
    estimates = build_estimates(run_start, chronological, chosen['status'] if chosen else None, report['timing'])
    if estimates:   # additive: only when the checkpoints hold `eta:` lines (N17 reads these raw rows, the page needs none of the totals)
        report['estimates'] = estimates
        report['estimateTotals'] = dashboard_estimates.totals(estimates)
    rows = dashboard_agents.metrics_rows(run_dir, run_start)   # N16: only when the run has a journal (the field is additive, like `agents` of `show`)
    if rows is not None:
        report['agents'] = rows
    return report


# --------------------------------------------------------------------------- CLI

def _cmd_show(args):
    manifest_for_mask = {}
    try:
        root, manifest = read_project(args.path)
        manifest_for_mask = manifest
        report, code = build_status(root, args.plan, manifest=manifest), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500)}, 2
    print(json.dumps(dashboard_tests.finalize_status(report, test_events.mask_setting(manifest_for_mask)), ensure_ascii=False, indent=2))
    return code


def _cmd_metrics(args):
    try:
        root, _ = read_project(args.path)
        report, code = build_metrics(root, args.run_id, args.plan), 0
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report, code = {'schema': SCHEMA, 'status': 'BLOCKED', 'reason': _message(error, 500)}, 2
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return code


def _cmd_serve(args):
    from .dashboard_http import serve_command  # lazy: http.server only for `serve`
    return serve_command(args)


def _cmd_url(args):
    from .dashboard_token import url_command
    return url_command(args)


def register(subparsers):
    parser = subparsers.add_parser('dashboard', help='Project status page (JSON contract schema 1, docs/dashboard-contract.md)',
                                   description='Project status page: print its JSON (show, metrics), serve it live (serve) or print the address of a running server (url).')
    actions = parser.add_subparsers(dest='dashboard_action', required=True)
    show = actions.add_parser('show', help='Print the dashboard JSON of a project (read-only)',
                              description='Print the dashboard JSON of a project (contract schema 1). Read-only.')
    add_project_arguments(show)
    show.add_argument('--plan', help='plan relative to the project, e.g. docs/plans/<file>.md (default: chosen automatically)')
    show.set_defaults(func=_cmd_show)
    metrics = actions.add_parser('metrics', help='Print general metrics of one run: sub-step times, rounds, failures '
                                                 '(read-only; same JSON with or without --json)',
                                description='Print general metrics of one run (sub-step times, rounds, failures) as JSON. Read-only; the output is the same with or without --json.')
    add_project_arguments(metrics)
    metrics.add_argument('run_id', metavar='run-id', help='run id (directory .skillsync/runs/<run-id>)')
    metrics.add_argument('--plan', help='plan relative to the project (default: the plan with "Przebieg: <run-id>")')
    metrics.set_defaults(func=_cmd_metrics)
    serve = actions.add_parser('serve', help='Serve the live status page of a project (foreground; Ctrl+C stops)',
                              description='Serve the live status page of a project in the foreground until Ctrl+C or SIGTERM. Consents given on the page write approval files of the project.')
    serve.add_argument('path', nargs='?', default='.', help='Project path (default: .)')
    add_layer_argument(serve)
    serve.add_argument('--plan', help='plan relative to the project (default: chosen automatically, switchable on the page)')
    serve.add_argument('--port', type=int, default=None,
                       help=f'the single-port mode (range {PORT_MIN}-{PORT_MAX}; one port per project): the role comes from the Host header, listens on '
                            f'127.0.0.1 unless --host says otherwise. Without any port flag: the two-port mode on {DEFAULT_PORT} (read) and '
                            f'{DEFAULT_PORT + 1} (consents)')
    serve.add_argument('--read-port', type=int, default=None, metavar='N',
                       help=f'two-port mode: read-only socket (no consent route at all; safe to hand out or tunnel); default {DEFAULT_PORT}; '
                            'not with --port')
    serve.add_argument('--approve-port', type=int, default=None, metavar='M',
                       help=f'two-port mode: consent socket (token, strict Origin, no proxy headers); default {DEFAULT_PORT + 1}; '
                            'not with --port or --no-approvals')
    serve.add_argument('--read-bind', default=None, metavar='ADDR',
                       help='listen address of the read socket (default: --host, else the dashboard: setting, else 0.0.0.0 = every interface; '
                            '127.0.0.1 = this machine only)')
    serve.add_argument('--approve-bind', default=None, metavar='ADDR',
                       help='listen address of the consent socket (default: the dashboard: setting, else 127.0.0.1 = this machine only; --host does not move it; '
                            '0.0.0.0 = every interface, plain HTTP: the token travels unencrypted)')
    serve.add_argument('--allow-proxy-headers', action='store_true',
                       help='consent socket: accept writes that carry proxy headers (Forwarded, X-Forwarded-*, X-Real-IP, Via, ...), '
                            'for a reverse proxy you trust')
    serve.add_argument('--prototype-port', type=int, default=None,
                       help=f'port of the prototype server the page checks (default: prototype.port of the manifest, else {PROTOTYPE_PORT})')
    serve.add_argument('--host', default=None,
                       help='listen address: with --port N (single-port mode, default 127.0.0.1; other devices also need --approve-host or --read-host); '
                            'with --read-port it moves the READ socket only (consents: --approve-bind). Without any port flag it is refused (see --read-bind)')
    serve.add_argument('--approve-host', action='append', default=[], metavar='NAME',
                       help='extra Host name (no port) allowed to open the page and give consents (repeatable); '
                            'default: localhost only; add --no-approvals for a read-only view')
    serve.add_argument('--read-host', action='append', default=[], metavar='NAME',
                       help='extra Host name (no port) allowed to open the page read-only (repeatable): every read, '
                            'no consents (POST /api/approve = 403), e.g. viewers through a tunnel; '
                            'consents stay with loopback and --approve-host')
    serve.add_argument('--no-approvals', action='store_true', help='read-only page: no consent buttons, POST /api/approve = 403')
    serve.add_argument('--session-url', help='https address of the session (the Open session button); no credentials')
    serve.add_argument('--poll', type=float, default=1.0, help='Seconds between file change checks (default: 1.0, min 0.2)')
    serve.add_argument('--verbose', action='store_true', help='log every request to stderr')
    serve.set_defaults(func=_cmd_serve)
    url = actions.add_parser('url', help='Print the address with the consent token of a running `dashboard serve` of the project '
                                         '(read from the machine state; the token is in the fragment `#zgoda=`)',
                         description='Print the address with the consent token of a running `dashboard serve` of the project, read from the machine state. Read-only; the token is in the fragment `#zgoda=`.')
    url.add_argument('path', nargs='?', default='.', help='Project path (default: .)')
    add_layer_argument(url)
    url.add_argument('--port', type=int, default=None, help='port of the server (needed when several servers of the project run)')
    url.add_argument('--host', default=None, metavar='NAME', help='Host name of the address (a name given to `serve --approve-host`; default 127.0.0.1)')
    url.add_argument('--read', action='store_true',
                     help='print the address of the read socket instead (no fragment, no token: safe to hand to a person from outside)')
    url.add_argument('--lan', action='store_true',
                     help='print the addresses of the read socket for OTHER devices on the local network (one per line; JSON `lanUrls`); '
                          'BLOCKED read_not_exposed when the read socket listens on this machine only')
    url.add_argument('--json', action='store_true', help='JSON {schema, status, url, port, pid[, role, readUrl, lanUrls]} or BLOCKED {code, reason} (exit 2)')
    url.set_defaults(func=_cmd_url)
