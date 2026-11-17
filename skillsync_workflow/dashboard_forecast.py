"""`forecast` of the status page: how much WORK is left until the last plan step, from the pace of the current run.

A forecast, not a measurement: `timing` measures, this module extrapolates, so it is a separate field and says why it has no number
whenever the data is weak (nothing is guessed). Pure function over `timing` (already computed from `checkpoints.md` and `approvals.jsonl`)
and `progress`; it reads no files and no clock (the page adds the clock time from its own `now`).

Model ("pace"): the gaps between the moments at which steps finished, in the order they finished, the first gap counted from the first
"in progress" mark and shared equally by the steps that finished together; the waiting windows inside a gap are subtracted, so a night or
a consent never enters the sample. The remaining steps times the median work per step is the central value, times the first and the
third quartile the range. Backtest (see CHANGELOG 1.9.0): the range is an order of magnitude, not a promise. The range is deliberately wide: the
sum of K gaps spreads less (relatively) than K times a quartile, so the range over-covers rather than under-covers. Parallel steps are
covered by construction (the pace is of the whole run, not of one step); the size of a step is not modelled.
"""
from __future__ import annotations

import statistics
from datetime import datetime

SCOPE = 'plan'
MIN_FINISHED_STEPS = 4   # the one place of the minimum of data: finished steps with both marks in the current run


def _moment(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _gap_work(windows, begin, end):
    """Seconds of `[begin, end]` without the closed waiting windows; None when a window that nothing closes starts before `end`
    (the gap cannot be told, it is left out of the sample)."""
    total = int((end - begin).total_seconds())
    for window in windows:
        start, finish = _moment(window.get('startedAt')), _moment(window.get('endedAt'))
        if start is None:
            continue
        if finish is None:
            if start < end:
                return None
            continue
        low, high = max(start, begin), min(finish, end)
        if high > low:
            total -= int((high - low).total_seconds())
    return max(total, 0)


def gap_samples(timing):
    """`(work_per_step_in_seconds, finished_count)` of the run: steps with both marks, finished in order. The time from one finish moment to
    the next (the first one from the first "in progress" mark) is shared equally by the steps that finished at that moment: a session often
    reports several steps in one checkpoint, and a zero gap for each of them would say "instant", not "fast". Unreadable gaps are left out."""
    steps = []
    for step in timing.get('steps') or []:
        begin, end = _moment(step.get('startedAt')), _moment(step.get('finishedAt'))
        if begin is not None and end is not None and end >= begin:
            steps.append((begin, end))
    if not steps:
        return [], 0
    groups = {}
    for _, end in steps:
        groups[end] = groups.get(end, 0) + 1
    windows = timing.get('windows') or []
    previous, samples = min(begin for begin, _ in steps), []
    for moment in sorted(groups):
        work = _gap_work(windows, previous, moment)
        if work is not None:
            samples += [work / groups[moment]] * groups[moment]
        previous = moment
    return samples, len(steps)


def build_forecast(timing, progress, plan_status, waiting_open):
    """`forecast` of `show`/`/api/status` (`schema: 1`, additive): `{scope, reason, ...}` without a number, or the range.

    Without a number the `reason` is one of `plan-not-running`, `no-run-start`, `no-remaining`, `not-enough-data` (with `have` and `need`
    finished steps). With a number: `{scope, remaining, samples, basis: 'run', paused, lowSeconds, medianSeconds, highSeconds}`; `paused`
    when the newest checkpoint waits for a person (`waiting_open`): the range is work without waiting, the page shows no clock time then."""
    if plan_status != 'in_progress':
        return {'scope': SCOPE, 'reason': 'plan-not-running'}
    if not isinstance(timing, dict) or not timing.get('available'):
        return {'scope': SCOPE, 'reason': 'no-run-start'}
    total = int((progress or {}).get('total') or 0)
    remaining = max(0, total - int((progress or {}).get('done') or 0) - int((progress or {}).get('pending') or 0))
    if remaining == 0:
        return {'scope': SCOPE, 'reason': 'no-remaining'}
    gaps, finished = gap_samples(timing)
    if finished < MIN_FINISHED_STEPS or len(gaps) < MIN_FINISHED_STEPS:
        return {'scope': SCOPE, 'reason': 'not-enough-data', 'have': min(finished, len(gaps)), 'need': MIN_FINISHED_STEPS}
    first, median, third = statistics.quantiles(gaps, n=4, method='inclusive')
    return {'scope': SCOPE, 'remaining': remaining, 'samples': len(gaps), 'basis': 'run', 'paused': bool(waiting_open),
            'lowSeconds': round(remaining * first), 'medianSeconds': round(remaining * median), 'highSeconds': round(remaining * third)}
