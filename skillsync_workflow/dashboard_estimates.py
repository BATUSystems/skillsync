"""Estimates of the status page (CORE-1150-ETA): the series of `eta:` lines of a run, the current `eta` (with the shift for the time
the user was waited for) and the discrepancy rows `estimates[]` (`dashboard metrics`, `show`).

Pure logic over prepared records (no file reads, no clock, no import of `dashboard`): `dashboard.py` turns the checkpoints into records
`{time, instant, eta: (operation, 'HH:MM') | None, etaClear, wait: text | None, inProgress: [ids] | None, done: [ids]}` and passes the windows
of `timing`. One table ends a series, for both consumers (spec "Jedna tabela końca serii"); the rules are applied in this order:

1 a closed plan; 2 `eta: -`; 3 `zrobione` of all steps of the series; 4 a line with `czeka:` (a series with steps goes on, one without
steps ends here); 5 `eta:` of the same operation and steps (the term may move); 6 `eta:` of another operation or steps (ends, a new one starts);
7 the first line without `czeka:` after a waiting line, without `eta:`, still "w toku" of a step of the series (goes on once, series with steps);
8 any other line without `eta:` (ends).
"""
from datetime import timedelta

WHO_AGENT = 'agent'


def norm_operation(text):
    return ' '.join(str(text).lower().split())


def resolve_term(instant, clock):
    """Absolute moment of `HH:MM` read against the checkpoint that carries it (more than 12 h before it: the next day)."""
    hours, minutes = (int(part) for part in clock.split(':'))
    moment = instant.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if instant - moment > timedelta(hours=12):
        moment += timedelta(days=1)
    return moment


def _new(record, index):
    operation, term = record['eta']
    steps = list(record['inProgress'] or [])
    return {'operation': operation, 'key': norm_operation(operation), 'steps': steps, 'stepset': set(steps), 'first': index, 'last': index,
            'term_idx': index, 'first_term': term, 'last_term': term, 'shifts': 0, 'end': None, 'reaction_used': False, 'closed_by_plan': False}


def fold_series(records, plan_closed=False, lookahead=False):
    """All series of the run in order; an open one has `end: None`. `lookahead` (the retrospective only) lets one line without `eta:` between
    two lines of the same operation and steps pass (a skipped repetition)."""
    series, current, done = [], None, set()   # `done`: steps reported done since the series started (a step reopened later starts a new series clean)
    for index, record in enumerate(records):
        done.update(record['done'])
        previous_wait = index > 0 and records[index - 1]['wait'] is not None
        steps = set(record['inProgress'] or [])

        def end(item, at=index):
            item['end'] = at
            return None

        if record['etaClear']:
            if current:
                current = end(current)
            continue
        if current and current['stepset'] and current['stepset'] <= done:
            current = end(current)
            if not record['eta'] or record['wait'] is not None:
                continue
        if record['wait'] is not None:
            if current is not None and not current['stepset']:
                current = end(current)
                continue
            if current is None and not (record['eta'] and steps):
                continue
            if current is not None:
                current['reaction_used'] = False   # every waiting cycle gets its own single line without `eta:` after the reaction
            if not record['eta']:
                continue   # a series with steps goes on through the waiting line
        if record['eta']:
            operation, term = record['eta']
            key = norm_operation(operation)
            if current and current['key'] == key and current['stepset'] == steps:
                if term != current['last_term']:
                    current['shifts'] += 1
                    current['last_term'] = term
                    current['term_idx'] = index
                current['operation'], current['last'] = operation, index
                continue
            if current:
                end(current)
            current = _new(record, index)
            series.append(current)
            done.clear()
            continue
        if current is None:
            continue
        if current['stepset'] and previous_wait and not current['reaction_used'] and current['stepset'] & steps:
            current['reaction_used'] = True
            continue
        if lookahead and record['wait'] is None and index + 1 < len(records):
            following = records[index + 1]
            if following['eta'] and norm_operation(following['eta'][0]) == current['key'] and set(following['inProgress'] or []) == current['stepset']:
                continue
        current = end(current)
    if plan_closed and current is not None and records:
        current['end'], current['closed_by_plan'] = len(records) - 1, True
    return series


def is_user_wait(what, split_who):
    """A waiting window counts as waiting for the user unless EVERY part of its text carries `(kto: agent)` (windows are merged as `A; B`)."""
    parts = [part for part in str(what).split('; ') if part.strip()]
    if not parts:
        return True
    for part in parts:
        who = split_who(part)[1]
        if not (who and who.get('role') == WHO_AGENT):
            return True
    return False


def clipped_wait_seconds(windows, start, finish, parse_iso, split_who):
    """(seconds, open) of the user windows clipped to `[start, finish]` (`finish` None = unbounded); `open` when a user window without an end touches it."""
    total, is_open = 0, False
    for window in windows or []:
        if not is_user_wait(window.get('what', ''), split_who):
            continue
        began = parse_iso(window.get('startedAt'))
        ended = parse_iso(window.get('endedAt'))
        if began is None:
            continue
        if ended is None:
            if finish is None or began < finish:
                is_open = True
            continue
        low, high = max(began, start), ended if finish is None else min(ended, finish)
        if high > low:
            total += int((high - low).total_seconds())
    return total, is_open


def current_eta(records, instants, windows, plan_closed, parse_iso, split_who):
    """The `eta` of the page (rev. of the spec): the open series after the last line, with `steps`, `paused`, `shiftSeconds`, `untilOriginalIso`
    and the shifted `untilIso`; None when no series is open."""
    if not records or plan_closed:
        return None
    series = fold_series(records)
    current = series[-1] if series and series[-1]['end'] is None else None
    if current is None:
        return None
    line = records[current['last']]
    out = {'operation': current['operation'], 'until': current['last_term'], 'untilIso': None, 'untilOriginalIso': None, 'since': line['time'],
           'steps': current['steps'], 'paused': False, 'shiftSeconds': 0}
    if instants:
        original = resolve_term(instants[current['last']], current['last_term'])
        out['untilOriginalIso'] = out['untilIso'] = original.isoformat()
        if current['stepset']:
            shift, _ = clipped_wait_seconds(windows, instants[current['term_idx']], None, parse_iso, split_who)
            out['shiftSeconds'] = shift
            if shift:
                out['untilIso'] = (original + timedelta(seconds=shift)).isoformat()
    if current['stepset'] and records[-1]['wait'] is not None:
        if instants:
            out['paused'] = clipped_wait_seconds(windows, instants[current['term_idx']], None, parse_iso, split_who)[1]
        else:
            out['paused'] = is_user_wait(records[-1]['wait'], split_who)
    return out


def build_rows(records, instants, windows, plan_closed, parse_iso, split_who):
    """`estimates[]`: one row per series of `eta:` lines (retrospective, with the lookahead of one line); [] without a run start."""
    if not records or not instants:
        return []
    rows = []
    for item in fold_series(records, plan_closed, lookahead=True):
        estimated_at = instants[item['first']]
        eta_first = resolve_term(estimated_at, item['first_term'])
        eta_last = resolve_term(instants[item['last']], item['last_term'])
        estimated = int((eta_first - estimated_at).total_seconds())
        row = {'steps': item['steps'], 'operation': item['operation'], 'estimatedAt': estimated_at.isoformat(), 'etaFirst': eta_first.isoformat(),
               'etaLast': eta_last.isoformat(), 'etaShifts': item['shifts']}
        if item['end'] is None:
            row['state'] = 'open'
            rows.append(row)
            continue
        ended_at = instants[item['end']]
        wait, is_open = clipped_wait_seconds(windows, estimated_at, ended_at, parse_iso, split_who)
        error = int((ended_at - eta_first).total_seconds())
        row.update(endedAt=ended_at.isoformat(), estimatedSeconds=estimated, actualSeconds=int((ended_at - estimated_at).total_seconds()),
                   userWaitSeconds=None if is_open else wait, errorSeconds=error)
        if is_open:
            row.update(adjustedErrorSeconds=None, state='unknown')
        else:
            adjusted = max(0, error - wait) if error > 0 else error
            row.update(adjustedErrorSeconds=adjusted, state='late' if adjusted > 0 else 'early' if adjusted < 0 else 'ontime')
        rows.append(row)
    return rows


def totals(rows):
    """`estimateTotals` of `dashboard metrics`: counts by state, the late and early seconds of the closed series and the median absolute error."""
    closed = [row for row in rows if row['state'] in ('late', 'early', 'ontime')]
    values = sorted(abs(row['adjustedErrorSeconds']) for row in closed)
    median = None
    if values:
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) // 2
    return {'series': len(rows), **{state: sum(1 for row in rows if row['state'] == state) for state in ('late', 'early', 'ontime', 'open', 'unknown')},
            'lateSeconds': sum(row['adjustedErrorSeconds'] for row in closed if row['adjustedErrorSeconds'] > 0),
            'earlySeconds': sum(-row['adjustedErrorSeconds'] for row in closed if row['adjustedErrorSeconds'] < 0),
            'medianAbsoluteErrorSeconds': median}
