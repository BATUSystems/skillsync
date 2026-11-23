"""The `tests` part of the status page (180-A): sections per plan step, built from the event files of `test_events`.

`build_tests` folds the attempts of the run into the JSON (`tests` and `progress.phases[].steps[].tests`; both optional, `schema: 1` stays
additive). The page gets counters and only the failed and running tests (up to 100 per group); the rest of a group is fetched with
`GET /api/tests` (`tests_response`). `finalize_status` is THE place where the answer of the page is masked: everything goes through `redact`
EXCEPT the top-level key `tests`, which is raw unless `tests.mask` is on (decision 6 of the spec).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from pathlib import Path

from .common import redact
from . import test_events as events

SHOWN_ATTEMPTS = 5
MAX_INLINE_TESTS = 100
STALE_ALIVE_S = 24 * 3600   # silence after which even a live pid of this machine is not believed (a reused pid)
MAX_SKIPPED = 20   # the entries of `skipped` in the answer
MAX_RUNNING_GROUP = 50   # a RUNNING attempt lists every test of a group up to this size (waiting ones visible without a click)
MAX_RESPONSE_TESTS = 5000
MAX_GROUPS = 200   # the groups of an attempt in the answer (failed first, then running, the rest by name); the attempt counters still cover every test
STALE_RUNNING_S = 35 * 60
NO_GROUP = 'pozostałe testy'
_LOCK = threading.RLock()   # the server is multi-threaded: the shared states are read and folded under one lock
_CACHE = OrderedDict()   # path -> AttemptState, least recently used first (incremental reading: only the appended bytes are parsed)
CACHE_BUDGET = 64 * 1024 * 1024   # bytes of the files behind the cached states (a state weighs several times its file); the sum of the CURRENT offsets
CACHE_MAX_ENTRIES = 500   # a second guard besides the byte budget
_TOUCHED = set()   # keys read by the current call (`_begin_round`): never evicted, so one answer's working set is not thrown out in the middle of it


def _begin_round():
    _TOUCHED.clear()


def _evict():
    """At the END of a call: drops the least recently used states that the call did not touch while the budget or the entry limit is exceeded.
    Evicting at the end (not while reading) keeps the whole working set of the next call in the cache even when it alone is over the budget."""
    while len(_CACHE) > CACHE_MAX_ENTRIES or sum(s.offset for s in _CACHE.values()) > CACHE_BUDGET:
        victim = next((k for k in _CACHE if k not in _TOUCHED), None)
        if victim is None:
            return   # the working set of this call alone is over the budget: it stays until the next call
        del _CACHE[victim]


def _state(path):
    key = str(path)
    try:
        path.stat()
    except OSError:
        return None
    state = events.read_attempt(path, _CACHE.get(key))   # a replaced or shortened file is read from the start (see `read_attempt`)
    _CACHE[key] = state
    _CACHE.move_to_end(key)
    _TOUCHED.add(key)
    return state


def _effective_state(state, path):
    """`running` becomes `interrupted` when the process of the run is gone (same machine), or, when the process cannot be told (another machine,
    Windows), when the file has not changed for a long time. A live process of this machine is running whatever the silence: a test tool without
    an adapter prints nothing for long stretches."""
    if state.state != 'running':
        return state.state
    start = state.start or {}
    alive = events.process_state(start.get('pid'), start.get('host')) if start.get('pid') is not None else None
    if alive is False:
        return 'interrupted'
    try:
        idle = time.time() - path.stat().st_mtime
    except OSError:
        idle = 0
    if alive is True:   # a pid can be reused after a crash or a reboot: a live process counts only for a day of silence
        return 'interrupted' if idle > STALE_ALIVE_S else 'running'
    return 'interrupted' if idle > STALE_RUNNING_S else 'running'


def _group_of(test):
    return test.get('group') or NO_GROUP


def _summary(path, state):
    start, end = state.start or {}, state.end or {}
    running = _effective_state(state, path) == 'running'
    groups = {}
    for test in state.tests.values():
        if not running and test['state'] == 'running':
            test = {**test, 'state': 'waiting'}   # an interrupted attempt has no running test: no result means "waiting", not "running"
        group = groups.setdefault(_group_of(test), {'name': _group_of(test), 'counts': {'passed': 0, 'failed': 0, 'skipped': 0, 'running': 0, 'waiting': 0}, 'tests': []})
        group['counts'][test['state']] += 1
        group['tests'].append(test)
    listed = []
    for group in groups.values():
        group['state'] = 'failed' if group['counts']['failed'] else 'running' if group['counts']['running'] else 'waiting' if group['counts']['waiting'] and not group['counts']['passed'] else 'passed'
        group['total'] = sum(group['counts'].values())
        listed.append(group)
    listed.sort(key=lambda g: (0 if g['state'] == 'failed' else 1 if g['state'] == 'running' else 2, g['name']))
    groups_total = len(listed)
    listed = listed[:MAX_GROUPS]   # cut after the sort and BEFORE the tests of a group are turned into public objects
    for group in listed:
        hot = group['tests'] if running and len(group['tests']) <= MAX_RUNNING_GROUP else [t for t in group['tests'] if t['state'] in ('failed', 'running')]
        group['tests'] = [_public(t) for t in hot[:MAX_INLINE_TESTS]]
    counts = state.counts if running or not state.counts.get('running') else {**state.counts, 'running': 0, 'waiting': state.counts.get('waiting', 0) + state.counts['running']}
    return {'n': state.number, 'state': _effective_state(state, path), 'startedAt': start.get('at'), 'endedAt': end.get('at'),
            'durationMs': end.get('ms'), 'command': start.get('command'), 'tool': start.get('tool'), 'levels': 'tests' if state.tests else 'counts' if state.stream_counts else start.get('levels') or 'command', 'live': start.get('live') if start.get('live') in ('running', 'completion', 'counts') else None,
            'source': end.get('source'), 'exit': end.get('exit'), 'reason': end.get('reason'), 'counts': counts, 'truncated': state.truncated,
            'groups': listed, 'groupsTotal': groups_total, 'groupsOmitted': groups_total - len(listed)}


def _public(test):
    return {k: v for k, v in test.items() if v is not None}


def build_tests(root, run_id, progress=None, now=None):
    with _LOCK:
        _begin_round()
        try:
            return _build_tests(root, run_id, progress)
        finally:
            _evict()


def _build_tests(root, run_id, progress=None):
    """`(tests, {step id: step summary})` of the run, or `(None, {})` without any event file. `tests` = `{sections, failedTotal, runningTotal}`."""
    if not run_id:
        return None, {}
    base = Path(root) / '.skillsync' / 'runs' / run_id / events.EVENTS_DIR
    if base.is_symlink() or not base.is_dir():
        return None, {}
    titles = {}
    for phase in progress or []:
        for step in phase.get('steps', []):
            titles[step['id']] = step['title']
    sections, steps, skipped = [], {}, []

    def skip(where, reason):   # one bad directory or file never costs the page: it is left out and named (a short, fixed reason; never the content)
        if len(skipped) < MAX_SKIPPED:
            skipped.append({'path': f'tests/{where}', 'reason': reason})
    try:
        directories = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return None, {}
    for directory in directories:
        step = None if directory.name == events.NO_STEP else directory.name
        attempts = []
        try:
            paths = events.list_attempts(root, run_id, step)[-SHOWN_ATTEMPTS:]
        except (OSError, ValueError):   # a name outside the step id pattern, a reserved one (`CON`), a link on the way
            skip(directory.name, 'not a step directory')
            continue
        for path in paths:
            try:
                state = _state(path)
                if state is not None and state.start is not None:
                    attempts.append(_summary(path, state))
            except Exception:   # noqa: BLE001 - a file that cannot be folded is skipped, whatever the reason (the page must stay up)
                skip(f'{directory.name}/{path.name}', 'unreadable')
        if not attempts:
            continue
        attempts.reverse()   # newest first
        latest = attempts[0]
        sections.append({'step': {'id': step, 'title': titles.get(step)} if step else None, 'state': latest['state'], 'attempts': attempts})
        if step:
            steps[step] = {'state': latest['state'], 'attempt': latest['n'], 'counts': latest['counts']}
    if not sections and not skipped:
        return None, {}
    sections.sort(key=lambda s: s['attempts'][0].get('startedAt') or '', reverse=True)
    failed = sum(1 for s in sections if s['attempts'][0]['counts'].get('failed'))
    running = sum(1 for s in sections if s['state'] == 'running')
    return {'sections': sections, 'failedTotal': failed, 'runningTotal': running, **({'skipped': skipped} if skipped else {})}, steps


def add_to_status(status, root, run_id, progress):
    """Adds `tests` and the `tests` of the plan steps to a status dict (nothing when the run has no event files), and `agents` (N16: nothing
    when the run has no journal). One path for `show` and `GET /api/status`."""
    _add_agents(status, root, run_id)
    try:
        tests, steps = build_tests(root, run_id, progress)
    except Exception:   # noqa: BLE001 - the test data never takes the whole status down
        tests, steps = {'sections': [], 'failedTotal': 0, 'runningTotal': 0, 'skipped': [{'path': 'tests', 'reason': 'unreadable'}]}, {}
    if tests is None:
        return status
    status['tests'] = tests
    for phase in status.get('progress', {}).get('phases', []):
        for step in phase.get('steps', []):
            if step['id'] in steps:
                step['tests'] = steps[step['id']]
    return status


def _add_agents(status, root, run_id):
    from . import dashboard, dashboard_agents   # late: `dashboard` imports this module
    try:
        directory = dashboard.run_dir(root, run_id)
        if directory is None:
            return
        run = status.get('run') or {}
        agents = dashboard_agents.build_agents(directory, run_id, run.get('start'), [str(f).rsplit('/', 1)[-1] for f in run.get('files') or []])
    except Exception:   # noqa: BLE001 - the journal never takes the whole status down
        return
    if agents is not None:
        status['agents'] = agents


def tests_response(root, run_id, step, attempt, group=None, limit=500, offset=0):
    with _LOCK:
        _begin_round()
        try:
            return _tests_response(root, run_id, step, attempt, group, limit, offset)
        finally:
            _evict()


def _tests_response(root, run_id, step, attempt, group=None, limit=500, offset=0):
    """Body of `GET /api/tests`: the tests of one group (or all) of an attempt, `limit` (<= MAX_RESPONSE_TESTS) from `offset`, failed first; raw (the
    caller applies `finalize_response`). The parsing is incremental and shared with the status (one parse per change of the file). Names are
    validated (`ValueError`), a missing attempt gives None."""
    step = None if step in (None, '', events.NO_STEP) else step
    attempt = int(attempt)
    if attempt < 1 or (group is not None and len(group) > events.MAX_NAME):
        raise ValueError('bad attempt or group')
    limit = max(1, min(int(limit), MAX_RESPONSE_TESTS))
    offset = max(0, int(offset))
    directory = events.step_directory(root, run_id, step)   # an invalid name: ValueError (the caller answers 400)
    path = directory / f'{attempt}.jsonl'
    try:
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            return None
    except OSError:   # a name too long for the file system: there is no such attempt (404, not 500)
        return None
    state = _state(path)
    if state is None:
        return None
    tests = [t for t in state.tests.values() if group is None or _group_of(t) == group]
    tests.sort(key=lambda t: (0 if t['state'] == 'failed' else 1 if t['state'] == 'running' else 2 if t['state'] == 'waiting' else 3, t['id']))
    page = tests[offset:offset + limit]
    return {'run': run_id, 'step': step, 'attempt': attempt, 'group': group, 'total': len(tests), 'offset': offset, 'limit': limit,
            'hasMore': offset + limit < len(tests), 'tests': [_public(t) for t in page]}


RAW_LEAVES = ('id', 'name', 'group', 'msg')   # the fields of a test that are its RESULT (decision 6, review A1)


def _restore_leaves(masked, original):
    """Puts the raw result leaves of `original` (`groups[].name`, `groups[].tests[].id|name|group|msg`) back into the masked copy of the same
    shape. Everything else in the tree (`step.title`, `command`, `reason`, `tool`...) stays masked."""
    for section, source in zip(masked.get('sections', []), original.get('sections', [])):
        for attempt, origin in zip(section.get('attempts', []), source.get('attempts', [])):
            for group, orig_group in zip(attempt.get('groups', []), origin.get('groups', [])):
                if 'name' in orig_group:
                    group['name'] = orig_group['name']
                for test, orig_test in zip(group.get('tests', []), orig_group.get('tests', [])):
                    for key in RAW_LEAVES:
                        if key in orig_test:
                            test[key] = orig_test[key]
    return masked


def finalize_tests(tests, mask_tests=False):
    """The `tests` tree of the status as it leaves the process: all masked, the result leaves restored raw unless `tests.mask` is on."""
    masked = redact(tests)
    return masked if mask_tests else _restore_leaves(masked, tests)


def finalize_response(body, mask_tests=False):
    """Body of `GET /api/tests`: masked, the result leaves of `tests[]` raw unless `tests.mask` is on."""
    masked = redact(body)
    if not mask_tests:
        for test, original in zip(masked.get('tests', []), body.get('tests', [])):
            for key in RAW_LEAVES:
                if key in original:
                    test[key] = original[key]
        if 'group' in body:
            masked['group'] = body['group']
    return masked


def finalize_status(data, mask_tests=False):
    """THE masking of the answer of the page: `redact` over everything; the top-level `tests` keeps only the RAW LEAVES THAT ARE A TEST RESULT
    (`id`, `name`, `group`, `msg`, the name of a group) unless `mask_tests` (`tests.mask: on`); the rest of its tree is masked too (decision 6 and A1)."""
    if not isinstance(data, dict) or 'tests' not in data:
        return redact(data)
    rest = redact({k: v for k, v in data.items() if k != 'tests'})
    return {k: finalize_tests(data['tests'], mask_tests) if k == 'tests' else rest[redact(k, True)] for k in data}
