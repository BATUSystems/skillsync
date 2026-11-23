"""The `agents` part of the status page (N16): the running and the recent runs of sub-minds and review rounds, folded from
`.skillsync/runs/<run-id>/agents.jsonl`.

The journal is written by `skillsync agent-run` / `review-round` (one line per event, closed lists, no session text). Here it is only READ:
`build_agents` returns the optional top-level `agents` field of `show` and `GET /api/status` (`schema: 1` stays additive; no journal = no field).
The server reads no clock: whether a run overran its estimate or is abandoned is decided by the consumer (the renderer), so the field has no `stale`.

Limits (hard): the tail of the file only (at most `MAX_LINES` lines and `MAX_BYTES` bytes, the file is opened once), `running` at most 6 (the oldest
go to `omitted.running`), `recent` at most 8 (the rest to `omitted.recent`), a corrupted line is skipped, an orphaned `end` / `eta` (its `start`
is outside the tail) is ignored.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

JOURNAL = 'agents.jsonl'
MAX_LINES = 500
MAX_BYTES = 256 * 1024
MAX_LINE = 4096   # a longer line is not ours (the writer keeps events under 4 KiB)
MAX_RUNNING = 6
MAX_RECENT = 8
MIN_RECENT_S = 120   # a shorter run of `kind: run` is noise (a round of review is always shown)

ROLES = ('reviewer', 'verifier', 'implementer', 'test-writer', 'test-runner', 'scribe', 'mechanic', 'explore')
KINDS = ('review', 'run')
PROFILES = ('spec', 'plan', 'code', 'final', 'docs', 'prototype', 'retro', 'pre-implement')
RESULTS = ('PASS', 'FAIL', 'CHANGES_REQUIRED', 'BLOCKED', 'NOT_RUN', 'DONE', 'CONFIRMED', 'REFUTED', 'UNDETERMINED', 'ABORTED')
AGENT_RE = re.compile(r'a-[0-9]{1,6}')
STEP_RE = re.compile(r'[A-Za-z0-9._-]{1,32}')
ETA_RE = re.compile(r'([01][0-9]|2[0-3]):[0-5][0-9]')


def _moment(value, fallback_tz):
    """`t` of an event as an aware datetime (a naive one is read in the zone of the run start, else the local one), or None."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz) if fallback_tz else parsed.astimezone()
    return parsed


def _eta_iso(moment, clock):
    """The deadline `HH:MM` read against the date and zone of the event that carried it (a clock that lies more than 12 h before the event is the next
    day, like `build_eta`)."""
    hours, minutes = (int(part) for part in clock.split(':'))
    target = moment.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if moment - target > timedelta(hours=12):
        target += timedelta(days=1)
    return target.isoformat()


def _tail(path):
    """The last lines of the journal (at most MAX_LINES lines and MAX_BYTES bytes; one open), or None when there is no readable regular file."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with open(path, 'rb') as handle:
            size = os.fstat(handle.fileno()).st_size
            start = max(0, size - MAX_BYTES)
            handle.seek(start)
            data = handle.read(MAX_BYTES)
    except OSError:
        return None
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':   # the newline that ends the last line
        lines.pop()
    if start > 0:
        lines = lines[1:]   # the first line of a cut tail is partial
    return lines[-MAX_LINES:]


def _event(raw, fallback_tz):
    """One validated event as a dict, or None for anything that is not ours (corrupted line, unknown value, free text)."""
    if not raw.strip() or len(raw) > MAX_LINE:
        return None
    try:
        item = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(item, dict) or item.get('v') != 1:
        return None
    ev, agent = item.get('ev'), item.get('agent')
    moment = _moment(item.get('t'), fallback_tz)
    if ev not in ('start', 'eta', 'end') or not isinstance(agent, str) or not AGENT_RE.fullmatch(agent) or moment is None:
        return None
    out = {'ev': ev, 'agent': agent, 't': moment}
    eta = item.get('eta')
    if eta is not None and not (isinstance(eta, str) and ETA_RE.fullmatch(eta)):
        return None
    out['eta'] = eta
    if ev == 'start':
        role, kind, profile, step, rnd = item.get('role'), item.get('kind'), item.get('profile'), item.get('step'), item.get('round')
        if role not in ROLES or kind not in KINDS or (profile is not None and profile not in PROFILES):
            return None
        if step is not None and not (isinstance(step, str) and STEP_RE.fullmatch(step)):
            return None
        if rnd is not None and not (isinstance(rnd, int) and not isinstance(rnd, bool) and 1 <= rnd <= 99):
            return None
        out.update(role=role, kind=kind, profile=profile, step=step, round=rnd)
    elif ev == 'eta':
        if eta is None:
            return None
    else:
        result, report = item.get('result'), item.get('report')
        if result not in RESULTS or (report is not None and not (isinstance(report, str) and 0 < len(report) <= 120 and '/' not in report and '\\' not in report)):
            return None
        out.update(result=result, report=report)
    return out


def build_agents(run_dir, run_id, start_iso=None, files=()):
    """`agents` of the status for one run, or None when the run has no journal. `files` = names of the run's documents (a `report` is a link only when
    its file is among them). `start_iso` (the run start) only gives the zone for an event whose `t` carries none."""
    del run_id   # the journal lives in the directory of the run; the argument keeps the call symmetrical with the other builders
    lines = _tail(Path(run_dir) / JOURNAL)
    if lines is None:
        return None
    try:
        start = datetime.fromisoformat(start_iso.strip().replace('Z', '+00:00')) if isinstance(start_iso, str) else None
    except ValueError:
        start = None
    tz = start.tzinfo if start is not None else None
    runs, order = {}, []
    for raw in lines:
        event = _event(raw, tz)
        if event is None:
            continue
        agent = event['agent']
        if event['ev'] == 'start':
            if agent in runs:   # a repeated id: the first start wins (the writer never repeats one)
                continue
            runs[agent] = {'agent': agent, 'role': event['role'], 'kind': event['kind'], 'profile': event['profile'], 'step': event['step'],
                           'round': event['round'], 'start': event['t'], 'etaFirst': event['eta'], 'eta': event['eta'],
                           'etaIso': _eta_iso(event['t'], event['eta']) if event['eta'] else None, 'etaShifts': 0, 'end': None}
            order.append(agent)
            continue
        run = runs.get(agent)
        if run is None or run['end'] is not None:   # an orphaned or a late event
            continue
        if event['ev'] == 'eta':
            run['eta'], run['etaIso'] = event['eta'], _eta_iso(event['t'], event['eta'])
            run['etaShifts'] += 1
        else:
            run['end'] = event
    running_all = [runs[a] for a in order if runs[a]['end'] is None]
    running_all.sort(key=lambda r: r['start'])
    kept = running_all[-MAX_RUNNING:] if len(running_all) > MAX_RUNNING else running_all
    running = [{'agent': r['agent'], 'role': r['role'], 'kind': r['kind'], 'profile': r['profile'], 'step': r['step'], 'round': r['round'],
                'startedAt': r['start'].isoformat(), 'eta': r['eta'], 'etaFirst': r['etaFirst'], 'etaIso': r['etaIso'], 'etaShifts': r['etaShifts']}
               for r in kept]
    known = set(files)
    recent_all = []
    for r in (runs[a] for a in order if runs[a]['end'] is not None):
        seconds = max(0, int((r['end']['t'] - r['start']).total_seconds()))
        if r['kind'] == 'run' and (r['etaFirst'] is None and r['etaShifts'] == 0 or seconds < MIN_RECENT_S):
            continue
        report = r['end']['report']
        recent_all.append({'agent': r['agent'], 'role': r['role'], 'kind': r['kind'], 'profile': r['profile'], 'step': r['step'], 'round': r['round'],
                           'startedAt': r['start'].isoformat(), 'endedAt': r['end']['t'].isoformat(), 'seconds': seconds, 'etaIso': r['etaIso'],
                           'result': r['end']['result'], 'report': report if report in known else None, '_end': r['end']['t']})
    recent_all.sort(key=lambda r: r['_end'], reverse=True)
    recent = recent_all[:MAX_RECENT]
    for item in recent:
        del item['_end']
    return {'running': running, 'recent': recent,
            'omitted': {'running': len(running_all) - len(running), 'recent': len(recent_all) - len(recent)}}
