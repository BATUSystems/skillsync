"""`skillsync plan-fresh [path] [--json]`: is the plan of the run as fresh as the data that already exist (read only, exit code 0).

Compares the plans `docs/plans/*.md` in `in_progress` with the newest line of `.skillsync/runs/<run-id>/checkpoints.md` and the open runs
of `agents.jsonl` (`agent-run`). Nothing is guessed from commits or from dependencies between steps; the parsers of the status page
(`dashboard.ids_reported_done`, `ids_in_progress`, `parse_checkpoints`, `STEP_ID`) read the checkpoints, no new step grammar is made here.

Blocking findings (the Stop hook `check-closure.py` stops the turn once on them):
- `step-reported-unchecked`: the newest line has `zrobione: X ... <sha>` (an element that starts with the step id and holds a commit sha:
  7-40 hex characters with at least one letter a-f, on word boundaries) and the step X is `[ ]` in the plan. A `zrobione:` element without a
  sha is "reported, waits for the review" and does not count. The newest line forgets `zrobione:` with the next line that has none.
- `step-running-unmarked`: an open run of kind `run` of the log has `step: X` of the plan, and the `w toku:` segment of the newest line has
  neither X nor its phase (`F1` covers `F1.2`) or there is no `checkpoints.md`.
Informational, never blocking, one finding per plan and code (count + the first 3 fragments, masked by `redact`):
- `step-id-unparsed`: a check-box line of the step section whose lead looks like a step id but does not fit `STEP_ID` (`FT.1`, `1`, `**0.1**`);
  such a step does not exist for the status page and for this command. Counted even when the plan has no recognised step at all.
- `checkpoint-id-skipped`: an element of the `w toku:` / `zrobione:` lists of the newest line that the id reading skips (`no-step-id`, `later-id`).
- `checkpoint-id-unknown`: an id that leads an element of the newest line but is no step of any `in_progress` plan of the same run (abbreviations
  such as `K2` or `O113` fit the grammar and vanish silently); the ids are compared with the steps of ALL such plans of the run. A plan whose `Przebieg:` is `<id>.<package>` counts as another run (a limit).
- `idle-capacity`: at least two `[ ]` steps of the first phase that has open steps are not in `w toku:` and `resources` allows
at least one more sub-agent (the readiness of the steps is judged by the orchestrator: dependencies are not parsed).

Window: a line of `checkpoints.md` counts when the file is newer than the plan file (the orchestrator saw the state after the last edit
of the plan) and not older than 6 hours (the lines have only `HH:MM`, so the file time stands in for the date). The events of
`agents.jsonl` (ISO with a zone) obey the same two conditions. Ids outside the steps of the plan are ignored. Plans with letter-only ids
(`A`...`E`) stay invisible (the id grammar of the status page, `STEP_ID`). The plan and the run have to lie in the project the
orchestrator works in: a session in a worktree without `docs/plans` finds nothing (no plan, no findings), a limit, not an error.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import agent_runs, dashboard, resources
from . import protocol_tokens as tokens

SCHEMA = 1
WINDOW_HOURS = 6
MAX_PLAN_BYTES = 512 * 1024
BLOCKING = ('step-reported-unchecked', 'step-running-unmarked')
STEP_ID_CODES = ('step-id-unparsed', 'checkpoint-id-skipped', 'checkpoint-id-unknown')
# a commit sha inside an element of `zrobione:`: 7-40 hex characters, at least one letter a-f, word boundaries (a plain number is not a sha)
SHA_IN_TEXT = re.compile(r'(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![0-9A-Za-z])')


def plan_steps(text):
    """`[{phase, id, done}]` of the first section `## Progress` (older plans: `## Kroki`/`## Steps`); `phase` is the `###` heading ('' without)."""
    steps = []
    for phase, line in dashboard.plan_progress_lines(text):
        match = dashboard.STEP_RE.match(line)
        if match:
            steps.append({'phase': phase, 'id': match.group('id'), 'done': match.group('mark') != ' '})
    return steps


def done_elements(text):
    """`[(ids, sha_present)]` of the elements of the `zrobione:` segment of a checkpoint text (the keyword rule of the status page:
    Polish in any segment first, then English at the start of a segment)."""
    reported = set(dashboard.ids_reported_done(text))
    if not reported:
        return []
    segments = dashboard.split_segments(text)
    match = None
    for pattern in (dashboard.DONE_RE, dashboard.DONE_EN_RE):
        match = next((found for found in (pattern.search(item) for item in segments) if found), None)
        if match:
            break
    if not match:
        return []
    found = []
    for element in dashboard.split_top_level(match.group('seg')):
        lead = dashboard.ID_LIST_RE.match(element)
        if not lead:
            continue
        ids = [item for item in dashboard.ID_FIND_RE.findall(lead.group('ids')) if item in reported]
        if ids:
            found.append((ids, bool(SHA_IN_TEXT.search(element))))
    return found


def covers(marked, step):
    """Does the `w toku:` list cover the step: the same id, or a phase id (`F1` covers `F1.2`)."""
    return any(step == item or step.startswith(item + '.') for item in marked)


def _plans(root):
    directory = Path(root) / 'docs' / 'plans'
    if directory.is_symlink() or not directory.is_dir():
        return
    for path in sorted(directory.glob('*.md')):
        if path.is_symlink() or not path.is_file() or path.name.lower() == 'readme.md':
            continue
        try:
            if path.stat().st_size > MAX_PLAN_BYTES:
                continue
            yield path, path.read_text(encoding='utf-8'), path.stat().st_mtime
        except (OSError, UnicodeDecodeError):
            continue


def _run_directories(root, run_id):
    """Run directories of the plan: `<id>` and `<id>.<package>` (the rule of `check-closure.py`), no symlinks."""
    base = Path(root) / '.skillsync' / 'runs'
    if Path(root, '.skillsync').is_symlink() or base.is_symlink() or not base.is_dir():
        return []
    return [item for item in sorted(base.iterdir())
            if (item.name == run_id or item.name.startswith(run_id + '.')) and item.is_dir() and not item.is_symlink()]


def _newest_checkpoints(directories):
    """`(text, mtime)` of the `checkpoints.md` with the newest mtime of the directories, or `(None, None)`."""
    best = (None, None)
    for directory in directories:
        path = directory / 'checkpoints.md'
        try:
            if path.is_symlink() or not path.is_file():
                continue
            stamp = path.stat().st_mtime
            if best[1] is None or stamp > best[1]:
                best = (dashboard.read_text(path), stamp)
        except OSError:
            continue
    return best


def analyze(root, now=None, allowed=None):
    """The `plan-fresh` report. `now` is a timestamp (injected by tests), `allowed` the free sub-agent places (injected; else `resources`)."""
    stamp = datetime.now().astimezone().timestamp() if now is None else now
    findings, idle, plans = [], {'allowed': None, 'steps': [], 'plan': None}, []
    limit = timedelta(hours=WINDOW_HOURS).total_seconds()
    entries, run_steps = [], {}
    for path, text, plan_mtime in _plans(root):
        status, _ = dashboard.doc_status(text)
        run = tokens.RUN_LINE.search(text[:4000])
        if status != 'in_progress' or not run or not agent_runs.RUN_ID_RE.fullmatch(run.group(1)):
            continue
        steps = plan_steps(text)
        entries.append((path, plan_mtime, run.group(1), steps, dashboard.plan_step_gaps(text)))
        run_steps.setdefault(run.group(1), set()).update(item['id'] for item in steps)
    reported_runs = set()   # the findings about the newest checkpoint line belong to the run: they are given once, with the first plan of it
    for path, plan_mtime, run_id, steps, gaps in entries:
        by_id = {item['id']: item for item in steps}
        label = f'docs/plans/{path.name}'
        if gaps:
            findings.append(_aggregate('step-id-unparsed', label, [item['token'] for item in gaps], [item['fragment'] for item in gaps],
                                       _hints(item['hint'] for item in gaps), f'{len(gaps)} step line(s) of {label} have no recognised step id'))
        if not by_id and not gaps:
            continue
        directories = _run_directories(root, run_id)
        plans.append(label)

        def fresh(moment):
            return moment >= int(plan_mtime) and stamp - moment <= limit

        checkpoints, ck_mtime = _newest_checkpoints(directories)
        newest = None
        if checkpoints is not None and fresh(ck_mtime):
            _, items = dashboard.parse_checkpoints(checkpoints)
            newest = items[-1]['text'] if items else None
        if newest is not None and run_id not in reported_runs:
            reported_runs.add(run_id)   # a plan with no readable step at all still gets `checkpoint-id-skipped` (the retro case)
            findings.extend(_checkpoint_id_findings(label, newest, run_steps.get(run_id, set())))
        if not by_id:
            continue
        marked = (dashboard.ids_in_progress(newest) or []) if newest is not None else []
        if newest is not None:
            seen = set()
            for ids, has_sha in done_elements(newest):
                for step in ids:
                    if has_sha and step in by_id and not by_id[step]['done'] and step not in seen:
                        seen.add(step)
                        findings.append({'code': 'step-reported-unchecked', 'plan': label, 'step': step, 'blocking': True,
                                         'detail': f'`zrobione:` with a commit sha in the newest checkpoint, but the step is `[ ]` in {label}'})
        for directory in directories:
            try:
                opened = agent_runs.open_runs(agent_runs.read_events(directory))
            except (OSError, ValueError):
                continue
            for agent, item in opened.items():
                started = dashboard.parse_iso(item.get('t')) if isinstance(item.get('t'), str) else None
                step = item.get('step')
                if (item.get('kind') != 'run' or started is None or not isinstance(step, str) or step not in by_id or not fresh(started.timestamp())
                        or covers(marked, step)):
                    continue
                if any(f['code'] == 'step-running-unmarked' and f['step'] == step and f['plan'] == label for f in findings):
                    continue
                findings.append({'code': 'step-running-unmarked', 'plan': label, 'step': step, 'blocking': True, 'agent': agent,
                                 'detail': f'open run {agent} ({item.get("role")}) of the step {step} is not in `w toku:` of the newest checkpoint'})
        if idle['plan'] is None:
            opened_phase = next((item['phase'] for item in steps if not item['done']), None)
            if opened_phase is not None:
                candidates = [item['id'] for item in steps if item['phase'] == opened_phase and not item['done'] and not covers(marked, item['id'])]
                if len(candidates) >= 2:
                    free = allowed if allowed is not None else resources.collect(root)['allowed']
                    if free >= 1:
                        idle = {'allowed': free, 'steps': candidates, 'plan': label}
                        findings.append({'code': 'idle-capacity', 'plan': label, 'step': ', '.join(candidates), 'blocking': False,
                                         'detail': f'free capacity: {free} sub-agents; check whether the steps {", ".join(candidates)} can run in parallel'})
    return {'schema': SCHEMA, 'plans': plans, 'findings': findings, 'blocking': any(item['blocking'] for item in findings), 'idle': idle}


def _aggregate(code, label, tokens_, fragments, hint, head):
    """One non-blocking finding of a plan: a count and the first 3 fragments."""
    shown = '; '.join(fragments[:3]) + (f'; and {len(fragments) - 3} more' if len(fragments) > 3 else '')
    return {'code': code, 'plan': label, 'step': ', '.join(tokens_[:3]), 'blocking': False, 'count': len(fragments),
            'detail': f'{head}: {shown}; {hint}'}


def _hints(hints):
    """The distinct hints in order of appearance, the first two (a mix of spellings gets each of its kinds named)."""
    return ' / '.join(list(dict.fromkeys(hints))[:2])


def _checkpoint_id_findings(label, newest, plan_ids):
    """`checkpoint-id-skipped` and `checkpoint-id-unknown` of the newest checkpoint line (see the module description)."""
    found = []
    skipped = [item for item in dashboard.report_warnings(newest) if item['reason'] in ('no-step-id', 'later-id')]
    if skipped:
        found.append(_aggregate('checkpoint-id-skipped', label, [item['fragment'].split()[0] for item in skipped if item['fragment']],
                                [item['fragment'] for item in skipped], _hints(dashboard.step_id_hint(item['reason']) for item in skipped),
                                f'{len(skipped)} element(s) of the newest checkpoint have no readable step id'))
    reported = list(dict.fromkeys((dashboard.ids_in_progress(newest) or []) + dashboard.ids_reported_done(newest)))
    unknown = [item for item in reported if item not in plan_ids and not any(covers([item], step) for step in plan_ids)] if plan_ids else []   # no readable step in the plans: nothing to compare with
    if unknown:
        found.append(_aggregate('checkpoint-id-unknown', label, unknown, unknown,
                                'write abbreviations in brackets after the step id, e.g. `F1.2 (K2)`',
                                f'{len(unknown)} id(s) at the start of an element of the newest checkpoint are no step of the plans of the run'))
    return found


def step_id_findings(root):
    """`[(plan path, reason)]` of the step id warnings (the three non-blocking codes), for `doctor`. Read only; never raises (fail-open)."""
    try:
        return [(item['plan'], f"{item['code']} {item['step']}: {item['detail']}")
                for item in analyze(root, allowed=0)['findings'] if item['code'] in STEP_ID_CODES]
    except Exception:   # noqa: BLE001 - a diagnostic never breaks `doctor`
        return []


def render(report):
    if not report['findings']:
        return 'plan-fresh: no findings'
    return '\n'.join(f"{'BLOCK' if item['blocking'] else 'info '} {item['code']} {item['step']}: {item['detail']}" for item in report['findings'])


def plan_findings(root):
    """`[(plan path, reason)]` of the BLOCKING findings, for `doctor`. Read only; never raises (fail-open)."""
    try:
        return [(item['plan'], f"{item['code']} {item['step']}: {item['detail']}; tick `[x]` with the sha after the review PASS, "
                               'move a step that waits for the review to `w toku: <step> (review)`, close the run with `agent-run end`')
                for item in analyze(root, allowed=0)['findings'] if item['blocking']]
    except Exception:   # noqa: BLE001 - a diagnostic never breaks `doctor`
        return []


def cmd_plan_fresh(args):
    try:
        report = analyze(resources.project_root(args.path))
    except (OSError, ValueError):
        report = {'schema': SCHEMA, 'plans': [], 'findings': [], 'blocking': False, 'idle': {'allowed': None, 'steps': [], 'plan': None}}
    print(json.dumps(report, ensure_ascii=False) if args.json else render(report))
    return 0


def register(subparsers):
    parser = subparsers.add_parser('plan-fresh', help='Is the in_progress plan as fresh as the checkpoints and the log of sub-agent runs; read only')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.set_defaults(func=cmd_plan_fresh)
