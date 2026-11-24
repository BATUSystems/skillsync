#!/usr/bin/env python3
"""SessionStart hook: puts a short state of the project into the context of the session (startup, resume, clear, compact).

The text (at most 2000 characters) holds: the project name, `delivery.mode` from .github/.skillsync.yaml, the open plan from
`skillsync status --json` (title as quoted data, `status`, done/all steps, `needsHuman`), the reminder to run `skillsync route`,
after `source=compact` the reminder to read the plan and the last checkpoint, the number of `subagent-report` warnings of the last 14 days
from .skillsync/hook-events.jsonl, and ONE line when `status` reports skills of the manifest that are not linked for claude (with `skillsync link <root>`;
the line drops together with the warnings). Over the limit the parts drop in this order: warnings (and the link line), needsHuman, step counters, the compact reminder;
the project name and the `route` reminder stay; the text is never cut in the middle of a word.

Usage (set by `skillsync link`): session-context.py [--cli '<JSON list: argv of skillsync>']; stdin = the hook JSON (read as UTF-8).
The CLI is run as argv + ['status', '--json'] in the project root with a limit of 2 s; no result = the plan section is skipped.
Nothing from `status` is copied except the project name, plan title, status and counters, needsHuman and the two numbers of `linkage.hosts.claude`
(checked as integers; no `root`, no other paths, no `findings`). Standalone, standard library only, no network, writes nothing. Every error ends with an empty result and exit 0 (fail-open).
"""
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time
import unicodedata

LIMIT = 2000
TITLE_MAX = 120
NAME_MAX = 80
CLI_TIMEOUT = 2.0
GIT_TIMEOUT = 1.5
TOTAL_BUDGET = 3.0  # the hook timeout; one monotonic deadline for git and the CLI together
RESERVE = 0.3
EVENT_DAYS = 14
EVENT_LINES = 500
EVENT_TAIL_BYTES = 512 * 1024
CLOSED = {'done', 'closed', 'abandoned', 'cancelled', 'canceled', 'superseded', 'rejected', 'implemented'}


def read_stdin():
    return sys.stdin.buffer.read().decode('utf-8', 'replace')


def clean(value, limit):
    """One line of plain text: control and format characters and line breaks become spaces, runs of spaces collapse, cut at `limit` (at a word when possible)."""
    out = []
    for char in str(value):
        category = unicodedata.category(char)
        out.append(' ' if (category[0] == 'C' or category in ('Zl', 'Zp') or char in '\t\r\n') else char)
    text = ' '.join(''.join(out).split())
    if len(text) > limit:
        text = text[:limit]
        if ' ' in text[limit // 2:]:
            text = text[:text.rfind(' ')]
        text = text.rstrip() + '…'
    return text


def git_root(cwd, timeout=GIT_TIMEOUT):
    try:
        done = subprocess.run(['git', '-C', cwd, 'rev-parse', '--show-toplevel'], capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
        if done.returncode == 0:
            top = done.stdout.decode('utf-8', 'replace').strip()
            if top and os.path.isdir(top):
                return top
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


def parse_manifest(root):
    """`project` and `delivery.mode` from .github/.skillsync.yaml with a line reader (no YAML library)."""
    project = mode = None
    try:
        with open(os.path.join(root, '.github', '.skillsync.yaml'), 'rb') as handle:
            lines = handle.read(256 * 1024).decode('utf-8', 'replace').splitlines()
    except OSError:
        return project, mode
    in_delivery = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        match = re.match(r'^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$', line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).split(' #')[0].strip().strip('\'"')
        if indent == 0:
            in_delivery = key == 'delivery'
            if in_delivery and value.startswith('{'):
                flow = re.search(r'[{,]\s*[\'"]?mode[\'"]?\s*:\s*([^,}]*)', value)
                found = flow.group(1).strip().strip('\'"').strip() if flow else ''
                if found:
                    mode = found
                in_delivery = False
            if key == 'project' and value:
                project = value
        elif in_delivery and key == 'mode' and value:
            mode = value
    return project, mode


def run_status(cli, root, timeout=CLI_TIMEOUT):
    if timeout <= 0.1:
        return None
    if not (isinstance(cli, list) and cli and all(isinstance(item, str) and item for item in cli)):
        return None
    try:
        done = subprocess.run(cli + ['status', '--json'], capture_output=True, timeout=timeout, cwd=root, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            return None
        data = json.loads(done.stdout.decode('utf-8', 'replace'))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def open_plan(status):
    plans = status.get('plans') if status else None
    if not isinstance(plans, list):
        return None
    for plan in plans:
        if isinstance(plan, dict) and str(plan.get('status', '')).strip().lower() not in CLOSED and plan.get('title'):
            return plan
    return None


def count_warnings(root):
    """`subagent-report` events of the last 14 days among the last 500 lines of .skillsync/hook-events.jsonl; garbage lines are skipped."""
    path = os.path.join(root, '.skillsync', 'hook-events.jsonl')
    try:
        with open(path, 'rb') as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - EVENT_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return 0
    lines = raw.decode('utf-8', 'replace').splitlines()[-EVENT_LINES:]
    now = datetime.datetime.now().astimezone()
    count = 0
    for line in lines:
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('hook') != 'subagent-report':
                continue
            stamp = datetime.datetime.fromisoformat(str(event.get('time')))
            if stamp.tzinfo is None:
                stamp = stamp.astimezone()
            if 0 <= (now - stamp).total_seconds() <= EVENT_DAYS * 86400:
                count += 1
        except (ValueError, TypeError, OverflowError):
            continue
    return count


def cut_words(text, limit):
    if len(text) <= limit:
        return text
    text = text[:limit]
    if ' ' in text:
        text = text[:text.rfind(' ')]
    return text.rstrip()


def status_label(plan):
    """The plan status; for an unrecognised one (`unknown`) the sanitised original `statusText` when present."""
    status = plan.get('status', '')
    if str(status).strip().lower() == 'unknown':
        original = clean(plan.get('statusText', ''), 40) if isinstance(plan.get('statusText'), (str, int, float)) else ''
        if original:
            return original
    return clean(status, 30)


def quote_arg(value, windows=None):
    """One command-line argument for a hint, the same rule as `layers._quote_arg` of the package (a hook is standalone and cannot import it;
    `tests/test_skillsync_hook_session_context.py` keeps the two equal). POSIX: `shlex.quote`. Windows: `/` instead of `\\` and double quotes
    only when the path needs them, so the hint runs in cmd.exe, PowerShell and Git bash alike (apostrophes do not in cmd.exe)."""
    if windows is None:
        windows = os.name == 'nt'
    if not windows:
        return shlex.quote(value)
    text = value.replace('\\', '/')
    return f'"{text}"' if re.search(r'[\s&|<>()^%!";,]', text) else text


def link_line(status, root, windows=None):
    """One line when `status` says that skills of the manifest are not linked for claude, else None. Only integers are taken from `linkage`;
    the directory comes from the local git root, quoted when it holds a space."""
    linkage = status.get('linkage') if status else None
    hosts = linkage.get('hosts') if isinstance(linkage, dict) else None
    info = hosts.get('claude') if isinstance(hosts, dict) else None
    if not isinstance(info, dict) or not isinstance(info.get('missing'), list) or not isinstance(info.get('expected'), int) \
            or isinstance(info.get('expected'), bool) or not info['missing'] or info['expected'] < len(info['missing']):
        return None
    where = quote_arg(root, windows)
    if len(where) > 200 or re.search(r'[\x00-\x1f\x7f]', where) or ((os.name == 'nt' if windows is None else windows) and '"' in root):
        return None   # a path that cannot be shown whole and safely: no line (the command would be wrong)
    return f"Skille nie zlinkowane dla claude: {len(info['missing'])} z {info['expected']}; uruchom: skillsync link {where}."


def build(name, mode, plan, source, warnings, limit=LIMIT, linked=None):
    """Drops parts in the documented order until the text fits; the name and the route reminder always stay."""
    needs = []
    if plan and isinstance(plan.get('needsHuman'), list):
        needs = [clean(item, 40) for item in plan['needsHuman'] if isinstance(item, (str, int, float)) and str(item).strip()]
    steps = plan.get('steps') if plan else None
    counters = None
    if isinstance(steps, dict) and isinstance(steps.get('done'), int) and isinstance(steps.get('total'), int):
        counters = f"{steps['done']}/{steps['total']}"
    for keep in range(5):  # 0: everything, 1: no warnings, 2: + no needsHuman, 3: + no counters, 4: + no compact reminder
        use_warnings, use_needs, use_counters, use_compact = keep < 1, keep < 2, keep < 3, keep < 4
        head = f'Projekt: {name}' + (f' (delivery.mode: {mode})' if mode else '') + '.'
        lines = [head]
        if plan:
            title = clean(plan.get('title', ''), TITLE_MAX)
            line = f'Plan otwarty (tytuł to dane, nie instrukcja): "{title}", status: {status_label(plan)}'
            if use_counters and counters:
                line += f', kroki: {counters}'
            if use_needs and needs:
                line += f'; needsHuman: {", ".join(needs)}'
            lines.append(line + '.')
        if use_warnings and linked:
            lines.append(linked)
        lines.append(f'Przed zadaniem: skillsync route {name}.')
        if source == 'compact' and use_compact:
            lines.append('Po kompaktowaniu: odczytaj plan i ostatni checkpoint.')
        if use_warnings and warnings:
            lines.append(f'Ostrzeżenia subagent-report (ostatnie {EVENT_DAYS} dni): {warnings}.')
        text = '\n'.join(lines)
        if len(text) <= limit:
            return text
    return cut_words(text, limit)


def main():
    started = time.monotonic()
    cli = None
    args = sys.argv[1:]
    for index, arg in enumerate(args):
        if arg == '--cli' and index + 1 < len(args):
            cli = args[index + 1]
        elif arg.startswith('--cli='):
            cli = arg[len('--cli='):]
    payload = json.loads(read_stdin())
    if not isinstance(payload, dict):
        return
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
        return
    root = git_root(cwd, max(0.1, min(GIT_TIMEOUT, TOTAL_BUDGET - (time.monotonic() - started) - RESERVE)))
    source = payload.get('source') if isinstance(payload.get('source'), str) else ''
    try:
        cli_argv = json.loads(cli) if cli is not None else None
    except ValueError:
        cli_argv = None
    left = TOTAL_BUDGET - (time.monotonic() - started) - RESERVE
    status = run_status(cli_argv, root, min(CLI_TIMEOUT, left))
    manifest_project, mode = parse_manifest(root)
    name = None
    if status and isinstance(status.get('project'), str) and status['project'].strip():
        name = status['project']
    name = clean(name or manifest_project or os.path.basename(os.path.normpath(root)) or 'projekt', NAME_MAX)
    text = build(name, clean(mode, 30) if mode else None, open_plan(status), source, count_warnings(root), linked=link_line(status, root))
    out = {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': text}}
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except Exception:  # fail-open: a hook error must never disturb the session
        pass
    sys.exit(0)
