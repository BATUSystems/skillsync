#!/usr/bin/env python3
"""SubagentStop hook: warns when a writing subagent returned no report.

For the roles implementer, test-writer, scribe, mechanic and test-runner (exact name; an empty `agent_type` is the context
compaction and other roles are skipped) it checks `last_assistant_message`: a line starting with `Wynik:`, `Status:`, `Raport:` or
`Report:` OR a path under `.skillsync/runs/` counts as a report. Otherwise one JSON line goes to <project root>/.skillsync/hook-events.jsonl
(time, hook, event, agent_type, reason, session_id; never the text of the message). `doctor` and `session-context` read that file.

Usage (set by `skillsync link`): check-subagent-report.py [--mode warn|off] (missing = warn; unknown = off); stdin = the hook JSON (UTF-8).
It never blocks (exit 0, no `decision`). Standalone, standard library only, no network; the only write is the events file in .skillsync/.
Every error ends with exit 0 and silence (fail-open).
"""
import datetime
import json
import os
import re
import subprocess
import sys

ROLES = ('implementer', 'test-writer', 'scribe', 'mechanic', 'test-runner')
REPORT_LINE = re.compile(r'^[ \t]*(?:[-*>#]+[ \t]+)?(?:\*\*|__)?(?:Wynik|Status|Raport|Report):', re.MULTILINE)
RUNS_PATH = re.compile(r'(?<![\w.-])\.skillsync[/\\]runs[/\\]')


def mode_from_args(args):
    value = 'warn'
    for index, arg in enumerate(args):
        if arg == '--mode' and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith('--mode='):
            value = arg[len('--mode='):]
    return value if value in ('warn', 'off') else 'off'


def git_root(cwd):
    done = subprocess.run(['git', '-C', cwd, 'rev-parse', '--show-toplevel'], capture_output=True, timeout=3,
                          stdin=subprocess.DEVNULL, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    top = done.stdout.decode('utf-8', 'replace').strip() if done.returncode == 0 else ''
    return top if top and os.path.isdir(top) else None


def main():
    if mode_from_args(sys.argv[1:]) == 'off':
        return
    payload = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))
    if not isinstance(payload, dict):
        return
    role = payload.get('agent_type')
    if not isinstance(role, str) or role not in ROLES:
        return
    message = payload.get('last_assistant_message')
    if isinstance(message, str) and (REPORT_LINE.search(message) or RUNS_PATH.search(message)):
        return
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
        return
    root = git_root(cwd)
    if not root:
        return
    session = payload.get('session_id')
    event = {'time': datetime.datetime.now().astimezone().isoformat(), 'hook': 'subagent-report', 'event': 'SubagentStop',
             'agent_type': role, 'reason': 'no-report', 'session_id': session if isinstance(session, str) else ''}
    folder = os.path.join(root, '.skillsync')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'hook-events.jsonl'), 'ab') as handle:
        handle.write((json.dumps(event, ensure_ascii=False) + '\n').encode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except Exception:  # fail-open: the check must never disturb the session
        pass
    sys.exit(0)
