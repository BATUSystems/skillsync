"""The gate of the test steps (180-A): a step marked `[T]` in the plan does not close without the record of a passing `skillsync test-run`.

`checkpoint --status PASS --step <id>` of a test step needs, among the `--evidence` files, the events file of an attempt of THIS step
(`.skillsync/runs/<run>/tests/<step>/<n>.jsonl`) that ended `PASS` (exit 0) on the content of the working tree that is checkpointed now:
the attempt stores in `end` the id of the tree of the working state AFTER the run (`git write-tree` of a temporary index: HEAD plus every change
and untracked file, without `.skillsync/` and `docs/plans/`), which a commit made after the tests and an edit of the plan keep unchanged. The gate
protects against SKIPPING the tests, not against forging the file: `checkpoint --evidence` hashes a file and does not verify who wrote it. `plan_findings` gives the `doctor` and `status` warning
for plans whose test steps are done without any passing attempt.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import file_lines
from .plan_phases import parse_test_steps
from . import test_events as events

_RUN_LINE = re.compile(r'^Przebieg:[ \t]*`?([^\s`]*)`?[ \t\r]*$', re.MULTILINE)
_CHECKED = re.compile(r'^\s*[-*]\s+\[[xX]\]\s+([A-Z]{1,3}-?\d+[a-z]?(?:\.\d+[a-z]?)?)(?![\w-])', re.MULTILINE)
MAX_PLAN_BYTES = 512 * 1024
TREE_EXCLUDES = ('.skillsync', 'docs/plans')   # run files and the plans (edited at every step) are not part of the content the tests ran on


def work_tree_id(root):
    """Id of the tree of the working state (HEAD plus all changes and untracked files, without `.skillsync/` and `docs/plans/`), or None without git
    (or when git cannot be run). A temporary index seeded with a COPY of the real one (its stat data keeps `git add -A` from hashing the whole tree
    again) leaves the real index and the working tree untouched."""
    root = str(Path(root).resolve())
    try:
        with tempfile.TemporaryDirectory(prefix='skillsync-tree-') as tmp:
            env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index'), 'GIT_OPTIONAL_LOCKS': '0'}

            def git(*args, plain=False):
                return subprocess.run(['git', '-C', root, *args], capture_output=True, env=os.environ if plain else env, timeout=120)
            if git('rev-parse', '--git-dir', plain=True).returncode:
                return None
            real = git('rev-parse', '--git-path', 'index', plain=True)
            real_index = Path(real.stdout.decode().strip()) if real.returncode == 0 else None
            if real_index is not None and not real_index.is_absolute():
                real_index = Path(root) / real_index
            if real_index is not None and real_index.is_file():
                shutil.copyfile(real_index, Path(tmp) / 'index')
            elif git('rev-parse', '--verify', '--quiet', 'HEAD', plain=True).returncode == 0 and git('read-tree', 'HEAD').returncode:
                return None
            if git('add', '-A', '--', '.').returncode:
                return None
            for excluded in TREE_EXCLUDES:   # a pathspec exclude would fail on an ignored directory
                git('rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', excluded)
            done = git('write-tree')
            return (done.stdout.decode().strip() or None) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def plan_for_run(root, run_id):
    """`(relative path, text)` of the plan whose `Przebieg:` line names the run, else None."""
    directory = Path(root) / 'docs' / 'plans'
    if directory.is_symlink() or not directory.is_dir():
        return None
    for path in sorted(directory.glob('*.md')):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        match = _RUN_LINE.search(text[:4000])
        if match and match.group(1) == run_id:
            return f'docs/plans/{path.name}', text
    return None


def test_steps_of_run(root, run_id):
    found = plan_for_run(root, run_id)
    return parse_test_steps(found[1]) if found else []


def passing_attempts(root, run_id, step):
    """`[(number, AttemptState)]` of the attempts of the step that ended PASS."""
    out = []
    for path in events.list_attempts(root, run_id, step):
        state = events.read_attempt(path)
        if state.state == 'passed' and state.end and state.end.get('exit') == 0:
            out.append((int(path.stem), state))
    return out


def _head(root):
    try:
        done = subprocess.run(['git', '-C', str(Path(root).resolve()), 'rev-parse', 'HEAD'], capture_output=True, timeout=30)
        return done.stdout.decode().strip() or None if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _proof_problem(state, run_id, step):
    """Why the attempt is not a proof of the tests of this step and run, or None."""
    start, end = state.start or {}, state.end or {}
    if state.state != 'passed' or end.get('exit') != 0 or end.get('status') != 'PASS':
        return 'the run did not pass'
    if start.get('run') != run_id or start.get('step') != step or start.get('stepSource') == 'none':
        return 'the run belongs to another run or step'
    total, failed = (state.counts or {}).get('total'), (state.counts or {}).get('failed') or 0
    if not isinstance(total, int) or total <= 0:
        return 'the run has no known positive number of executed tests'
    if failed:
        return f'the run has {failed} failed tests'
    return None


def evidence_problem(root, run_id, step, evidence):
    """None when the evidence of a PASS of a test step is enough, else the reason (for the error of `checkpoint`). `evidence` = project-relative
    paths of the evidence files."""
    hint = (f'step {step} is a test step ([T]): attach as --evidence the events file of a passing test run of this step '
            f'(.skillsync/runs/{run_id}/tests/{step}/<n>.jsonl) made on the current content, or waive it with --no-tests <reason>; run: '
            f'skillsync test-run <project> --run {run_id} --step {step} -- <command>')
    prefix = f'.skillsync/runs/{run_id}/tests/{step}/'
    mine = [item for item in evidence if item.startswith(prefix) and item.endswith('.jsonl')]
    if not mine:
        return f'{hint} (no events file of this step in --evidence)'
    current = work_tree_id(root)
    last = 'the events file is not a passing run'
    for item in mine:
        state = events.read_attempt(Path(root) / item)
        problem = _proof_problem(state, run_id, step)
        if problem:
            last = f'{item}: {problem}'
            continue
        if state.imported:   # a run of another machine (a copy without `.git`): valid for exactly the commit the driver named
            sha = (state.imported or {}).get('sourceSha')
            head = _head(root)
            if not sha or head is None or not (head == sha or head.startswith(sha)):
                last = f'{item}: the imported run was made for commit {sha}, the checkpointed one is {head}'
                continue
            return None
        recorded = (state.end or {}).get('tree')
        if current is None or recorded is None:
            last = f'{item}: the content of the working tree cannot be compared (no git repository, or git cannot be run, or the run recorded no tree): {"re-run the tests in a git repository" if current is None else "run them again"}'
            continue
        if recorded != current:
            last = f'{item}: the tests ran on different content than the one checkpointed (tree {recorded} vs {current}); run them again'
            continue
        return None
    return f'{hint} ({last})'


def _run_record(root, run_id):
    try:
        path = Path(root) / '.skillsync' / 'runs' / f'{run_id}.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def waived_steps(root, run_id):
    """`{step: reason}` of the test steps closed with `checkpoint --no-tests <reason>` (a decision for a human at the closing)."""
    out = {}
    for record in (_run_record(root, run_id).get('checkpoints') or []):
        if isinstance(record, dict) and record.get('no_tests') and record.get('status') == 'PASS':
            out[str(record.get('step'))] = str(record['no_tests'])
    return out


def run_of_plan(text):
    match = _RUN_LINE.search(text[:4000])
    return match.group(1) if match and match.group(1) else None


def plan_findings(root, only_run=None):
    """`[(plan path, reason)]`: a test step (`[T]`) checked without a proof (`tests-not-run`), closed with a waiver (`tests-waived`), and a step that ran
    tests through skillsync without the `[T]` marker (`tests-step-not-marked`)."""
    found = []
    directory = Path(root) / 'docs' / 'plans'
    if directory.is_symlink() or not directory.is_dir():
        return found
    for path in sorted(directory.glob('*.md')):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        run = run_of_plan(text)
        if not run or (only_run is not None and run != only_run):
            continue
        label = f'docs/plans/{path.name}'
        steps = parse_test_steps(text)
        done = set(_CHECKED.findall(text))
        waived = waived_steps(root, run)
        for step, reason in waived.items():
            found.append((label, f'step {step} was closed WITHOUT tests (tests-waived): {reason}'))
        for step in steps:
            if step in waived:
                continue
            if step not in done:
                continue
            try:
                ok = any(_proof_problem(state, run, step) is None for _, state in passing_attempts(root, run, step))
            except (OSError, ValueError):
                ok = False
            if not ok:
                found.append((label, f'test step {step} ([T]) is checked but run `{run}` has no passing `skillsync test-run` '
                                     f'of it (tests-not-run); run the tests with `skillsync test-run <project> --run {run} --step {step} -- <command>`'))
        base = Path(root) / '.skillsync' / 'runs' / run / events.EVENTS_DIR
        try:
            ran = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink() and p.name != events.NO_STEP) if base.is_dir() and not base.is_symlink() else []
        except OSError:
            ran = []
        for step in ran:
            if step not in steps:
                found.append((label, f'step {step} ran tests through `skillsync test-run` but is not marked `[T]` in the plan (tests-step-not-marked): '
                                     'mark it so that the gate covers it'))
    return found


def doctor_findings(root):
    """`[(path, reason)]` for `doctor`: `runs-not-ignored` (the run files hold unmasked test results and git tracks them or does not ignore them)
    and `tests-reporter-missing` (an Angular project without a JUnit/JSON reporter: only the counters of the tests are visible)."""
    root = Path(root)
    found = []
    runs = root / '.skillsync' / 'runs'
    try:
        has_events = runs.is_dir() and not runs.is_symlink() and any(True for _ in runs.glob('*/tests/*/*.jsonl'))
    except OSError:
        has_events = False
    if has_events:
        def git(*args):
            return subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=30)
        try:
            if git('rev-parse', '--git-dir').returncode == 0:
                tracked = git('ls-files', '-z', '--', '.skillsync/runs').stdout.strip(b'\0')
                ignored = git('check-ignore', '-q', '--no-index', '.skillsync/runs/x/tests/s/1.jsonl').returncode == 0
                if tracked or not ignored:
                    found.append(('.gitignore', 'test results in `.skillsync/runs/*/tests/` are not masked (tests.mask is off by default) and '
                                  + ('git tracks the run files' if tracked else 'the directory is not ignored by git') + ' (runs-not-ignored): add `.skillsync/` to `.gitignore` '
                                  'and `git rm -r --cached .skillsync/runs`, or set `tests.mask: on`'))
        except (OSError, subprocess.SubprocessError):
            pass
    angular = root / 'angular.json'
    if angular.is_file() and not angular.is_symlink():
        try:
            text = angular.read_text(encoding='utf-8', errors='replace')[:2_000_000]
            package = (root / 'package.json').read_text(encoding='utf-8', errors='replace')[:2_000_000] if (root / 'package.json').is_file() else ''
        except OSError:
            return found
        if not re.search(r'"reporters"|junit', text, re.IGNORECASE) and not re.search(r'karma-junit-reporter|jest-junit', package):
            found.append(('angular.json', 'Angular project without a JUnit/JSON reporter for `ng test` (tests-reporter-missing): the status page shows only the counters of the '
                          'tests; add a reporter (Vitest: `"reporters": ["default", "junit"]` and `--output-file`, Karma: `karma-junit-reporter`) and pass it to '
                          '`skillsync test-run --junit <file>`. skillsync does not change the configuration of the project'))
    return found
