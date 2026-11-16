"""`skillsync test-run` and the observer of the shared runner (180-A, stage 1).

`test-run <project> [--run RUN] [--step ID] [--junit FILE|DIR ...] [--timeout S] -- <command...>` runs a test command of the project through the
shared runner (`quality.run_validation`) with an observer that writes the events of the run (`test_events`): the state of the command on the fly,
live counters where the output prints them (Karma "Executed N of M"), the list of the tests after the end from a JUnit XML or TRX report
(`--junit`, or the default places of dotnet, Maven and Gradle). The output of the command is passed through unchanged and the exit code of the
command is the exit code of `test-run`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import sys
import time
from pathlib import Path

from .common import file_lines, load_layer_project, read_project
from .dashboard import ids_in_progress, parse_checkpoints
from .quality import _test_counts, evidence_reason, run_validation
from .runs import _slug
from . import test_events as events
from . import test_gate
from . import test_adapters
from . import test_reports

KARMA = re.compile(r'Executed (\d+) of (\d+)(?: \((?:skipped (\d+))?(?:,? ?(\d+) FAILED)?\))?')
REPORT_SINCE_SLACK_S = 2.0
MAX_REPORT_FILES = 200
TOOLS = (('dotnet', 'dotnet'), ('pytest', 'pytest'), ('py.test', 'pytest'), ('cargo', 'cargo'), ('nextest', 'cargo'), ('ng', 'angular'),
         ('playwright', 'playwright'), ('cypress', 'cypress'), ('jest', 'jest'), ('vitest', 'vitest'), ('mvn', 'maven'), ('mvnw', 'maven'),
         ('gradle', 'gradle'), ('gradlew', 'gradle'), ('go', 'go'), ('node', 'node'), ('npm', 'npm'), ('npx', 'npx'))


def _failure_count(output):
    """Failed tests named by the summary of the common tools (unittest `FAILED (failures=2, errors=1)`, pytest `3 failed`); 0 when the output names none."""
    total = 0
    unittest_summary = re.search(r'FAILED \(([^)]*)\)', output)
    if unittest_summary:
        total = sum(int(n) for kind, n in re.findall(r'(failures|errors)=(\d+)', unittest_summary.group(1)))
    found = re.search(r'(\d+) failed', output)
    return max(total, int(found.group(1))) if found else total


def detect_tool(command):
    """Name of the tool of a command (`argv` list or shell string), `unittest` for `python -m unittest`, else `unknown`."""
    argv = command if isinstance(command, list) else shlex.split(command, posix=os.name != 'nt') if isinstance(command, str) else []
    words = [Path(word).name.lower() for word in argv]
    if 'unittest' in words:
        return 'unittest'
    if 'pytest' in words or 'py.test' in words:
        return 'pytest'
    for word in words[:3]:
        word = re.sub(r'\.(exe|cmd|bat)$', '', word)
        for name, tool in TOOLS:
            if word == name:
                return tool
    return 'unknown'


def resolve_run(root, explicit):
    """`(run id, error)`: the explicit id (validated), else the only directory of `.skillsync/runs/`."""
    if explicit:
        return _slug(explicit, '--run'), None
    base = Path(root) / '.skillsync' / 'runs'
    names = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.is_symlink()) if base.is_dir() and not base.is_symlink() else []
    if len(names) == 1:
        return names[0], None
    return None, ('--run is required: ' + (f'{len(names)} runs in .skillsync/runs/ ({", ".join(names[:8])})' if names else 'no run in .skillsync/runs/'))


def resolve_step(root, run_id, explicit):
    """`(step, source)`: the explicit `--step`; else the ONLY step "w toku" in the newest checkpoint that has the phrase; `(None, 'ambiguous')` when several steps
    are in progress (the caller must pass `--step`: parallel sub-minds, no guessing); else `(None, 'none')`."""
    if explicit:
        return _slug(explicit, '--step'), 'flag'
    path = Path(root) / '.skillsync' / 'runs' / run_id / 'checkpoints.md'
    try:
        if path.is_symlink() or not path.is_file():
            return None, 'none'
        _, items = parse_checkpoints(path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None, 'none'
    for item in reversed(items):
        ids = ids_in_progress(item['text'])
        if ids is not None:
            return (ids[0], 'checkpoint') if len(ids) == 1 else (None, 'ambiguous' if ids else 'none')
    return None, 'none'


def find_reports(tool, cwd, explicit, since):
    """Report files of a run: the explicit ones (a file, or a directory searched for `*.xml` and `*.trx`), else the default places of dotnet,
    Maven and Gradle; only files written since the run started."""
    cwd = Path(cwd)
    candidates = []
    for item in explicit:
        path = Path(item) if os.path.isabs(item) else cwd / item
        if path.is_dir():
            candidates += [p for pattern in ('*.xml', '*.trx') for p in path.rglob(pattern)]
        elif path.is_file():
            candidates.append(path)
    if not explicit:
        defaults = {'dotnet': ['**/TestResults/*.trx'], 'maven': ['**/target/surefire-reports/*.xml', '**/target/failsafe-reports/*.xml'],
                    'gradle': ['**/build/test-results/**/*.xml']}.get(tool, [])
        for pattern in defaults:
            candidates += list(cwd.glob(pattern))
    found = []
    for path in sorted(set(candidates)):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime >= since - REPORT_SINCE_SLACK_S:
                found.append(path)
        except OSError:
            continue
        if len(found) >= MAX_REPORT_FILES:
            break
    return found


class TestRecorder:
    """Observer of `quality.run_validation` that records one attempt of a step (`started`, `line`, `finished`)."""
    __test__ = False   # not a test case for unittest/pytest discovery

    def __init__(self, root, run_id, step, step_source, mask=False, junit=(), passthrough=False, allow_no_count=None, allow_skips=False, adapter=None):
        self.root, self.run_id, self.step, self.step_source = Path(root), run_id, step, step_source
        self.mask, self.junit, self.passthrough = mask, list(junit), passthrough
        self.allow_no_count, self.allow_skips = allow_no_count, bool(allow_skips)
        self.adapter, self.ctx = adapter, None   # the adapter of the tool (stage 2); dropped for the rest of the run when it raises
        self.writer = None
        self.path = None
        self.number = None
        self.started_wall = None
        self.last = None

    def started(self, check):
        self.path, self.number = events.new_attempt(self.root, self.run_id, self.step)
        self.writer = events.EventWriter(self.path, mask=self.mask)   # a failure here (disk, permissions) reaches `run_validation`: the command does not start
        command = check.get('command') or ' '.join(shlex.quote(a) for a in check.get('argv') or [])
        self.started_wall = time.time()
        self.writer.start(run=self.run_id, step=self.step, stepSource=self.step_source, attempt=self.number, command=command,
                          tool=detect_tool(check.get('argv') or check.get('command')), pid=os.getpid(), levels='command',
                          **({'allowNoCount': events.clean_text(self.allow_no_count, 300, mask=True)} if self.allow_no_count else {}),
                          **({'allowSkips': True} if self.allow_skips else {}), **({'adapter': self.adapter.name, 'live': self.adapter.live} if self.adapter is not None else {}))
        if self.adapter is not None:
            self.ctx = test_adapters.AdapterContext(check.get('argv') or shlex.split(check.get('command') or ''), check['cwd'], self.root, self.writer)
            self._adapter_call(lambda: self.adapter.begin(self.ctx))

    def _adapter_call(self, fn):
        """Calls the adapter; any exception drops the adapter for the rest of the run (the counters of the summary and the report remain)."""
        if self.adapter is None:
            return None
        try:
            return fn()
        except Exception:
            self.adapter = None
            return None

    def partial(self, check, stream, text):
        if self.adapter is not None:
            self._adapter_call(lambda: self.adapter.partial(stream, text, self.ctx))

    def line(self, check, stream, text):
        if self.passthrough:
            print(text, file=sys.stderr if stream == 'err' else sys.stdout, flush=True)
        if self.adapter is not None:
            self._adapter_call(lambda: self.adapter.feed(stream, text, self.ctx))
        found = KARMA.search(text)
        if found and self.writer is not None:
            done, total = int(found.group(1)), int(found.group(2))
            skipped, failed = int(found.group(3) or 0), int(found.group(4) or 0)
            self.writer.counts(passed=max(done - failed - skipped, 0), failed=failed, skipped=skipped, running=0, total=total)

    def finished(self, check, result, output):
        writer = self.writer
        if writer is None:
            return
        try:
            self._finish(check, result, output)
        finally:
            writer.close()

    def _finish(self, check, result, output):
        writer = self.writer
        code = result.get('exit_code')
        tool = detect_tool(check.get('argv') or check.get('command'))
        counts, source, ran, skipped, failed = None, 'regex', None, None, 0
        reports = find_reports(tool, check['cwd'], self.junit, self.started_wall or 0) if output is not None else []
        skip_reports = bool(self._adapter_call(lambda: self.adapter.end(self.ctx, code, output or '', list(reports)))) if self.adapter is not None else False
        listed = []
        for report in ([] if skip_reports else reports):
            try:
                parsed, info = test_reports.parse_any(report, with_info=True)
            except (OSError, ValueError):
                continue
            listed += parsed
            if info.get('truncated'):
                writer.truncated = True
        if listed:
            source = 'junit'
            for item in listed:
                writer.test(item['id'], item['name'], item['state'], group=item.get('group'), ms=item.get('ms'), msg=item.get('msg'))
            failed = sum(1 for i in listed if i['state'] == 'failed')
            skipped = sum(1 for i in listed if i['state'] == 'skipped')
            ran = len(listed) - skipped
            counts = {'total': len(listed), 'passed': sum(1 for i in listed if i['state'] == 'passed'), 'failed': failed, 'skipped': skipped}
        elif output is not None:
            ran, skipped = _test_counts(output)
            if ran is not None:
                failed = _failure_count(output) if code else 0   # a clean exit never reads "3 failed" out of a log line
                counts = {'total': ran + (skipped or 0), 'passed': max(ran - failed, 0), 'failed': failed, 'skipped': skipped or 0}
        if code is None:
            status, reason = 'BLOCKED', result.get('reason') or 'not run'
        elif result.get('reason', '').startswith('Timeout'):
            status, reason = 'BLOCKED', result['reason']
        elif code != 0:
            status, reason = 'FAIL', None
        else:
            reason = f'the exit code is 0 but the report has {failed} failed tests' if failed else evidence_reason('test', ran, skipped, False, self.allow_skips)
            if reason and self.allow_no_count and ran is None and not failed:
                reason = None
            status = 'BLOCKED' if reason else 'PASS'
        try:
            tree = test_gate.work_tree_id(self.root)
        except Exception:   # never lose the end of the run over the fingerprint
            tree = None
        writer.end(status, code, reason=reason, counts=counts, ms=int(1000 * float(result.get('duration_seconds') or 0)), source=source, tree=tree)
        self.last = {'status': status, 'attempt': self.number, 'file': self.path, 'source': source}


def import_events(root, run_id, step, source, source_sha):
    """`test-run --import`: validates an events file written on another machine (every line `v: 1`, a `start` of this run and step, an `end`, within
    the limits) and stores it, byte for byte, as the next attempt of the step, followed by one `import` event that names the commit it was made for
    (`--source-sha`: the copies made with `git archive` have no `.git`). Returns `(path, number)`."""
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f'not a file: {source.name}')
    if source.stat().st_size > events.MAX_BYTES:
        raise ValueError('the events file is over the limit (8 MiB)')
    data = source.read_bytes()
    if not data.endswith(b'\n'):
        raise ValueError('the events file ends in an incomplete line')
    parsed, _, skipped = events.read_events(source)
    if skipped:
        raise ValueError(f'{skipped} lines of the events file are not events of version {events.EVENT_VERSION}')
    start = next((e for e in parsed if e.get('t') == 'start'), None)
    if start is None or not any(e.get('t') == 'end' for e in parsed):
        raise ValueError('the events file needs a start and an end event')
    if not start.get('host') or not start.get('startedAt'):
        raise ValueError('the events file has no host and startedAt in its start event: it is not a run recorded by `test-run`')
    local = Path(root).resolve() / '.skillsync' / 'runs'
    if start.get('host') == socket.gethostname() or str(source.resolve()).startswith(str(local) + os.sep):
        raise ValueError('--import is for the runs of ANOTHER machine: this file was written here (or lies in the run files of this project); run the tests with `test-run` instead')
    if start.get('run') != run_id or start.get('step') != step:
        raise ValueError(f"the events file belongs to run {start.get('run')!r}, step {start.get('step')!r}, not to {run_id!r}, {step!r}")
    if sum(1 for e in parsed if e.get('t') == 'test') > events.MAX_TEST_EVENTS:
        raise ValueError('the events file has too many test events')
    if not re.fullmatch(r'[0-9a-f]{7,64}', source_sha or ''):
        raise ValueError('--source-sha must be the commit (hex, 7 to 64 characters) the run was made for')
    path, number = events.new_attempt(root, run_id, step)
    with open(path, 'ab') as handle:
        handle.write(data)
        handle.write((json.dumps({'v': events.EVENT_VERSION, 't': 'import', 'sourceSha': source_sha, 'at': events._now()}, separators=(',', ':')) + '\n').encode('utf-8'))
    return path, number


def _command(args):
    if getattr(args, 'import_file', None):
        try:
            root, _ = read_project(args.path)
            run_id, problem = resolve_run(root, args.run)
            if problem or not args.step:
                print(f"BLOCKED: {problem or '--step is required with --import'}", file=sys.stderr)
                return 2
            path, number = import_events(root, run_id, _slug(args.step, '--step'), args.import_file, args.source_sha)
        except (OSError, ValueError) as error:
            print(f'BLOCKED: {error}', file=sys.stderr)
            return 2
        print(f'test-run: imported as attempt {number}, events {path}', file=sys.stderr)
        return 0
    try:
        root, manifest = read_project(args.path, getattr(args, 'manifest', None))
        command = list(args.command or [])
        if command[:1] == ['--']:
            command = command[1:]
        if not command:
            raise ValueError('test-run needs a command after `--`')
        run_id, problem = resolve_run(root, args.run)
        if problem:
            print(f'BLOCKED: {problem}', file=sys.stderr)
            return 2
        step, source = resolve_step(root, run_id, args.step)
        if source == 'ambiguous':
            print('BLOCKED: several steps are in progress in the checkpoints: pass --step <id> (and --run <id>)', file=sys.stderr)
            return 2
        adapter = test_adapters.resolve(args.format, command, detect_tool(command))
        recorder = TestRecorder(root, run_id, step, source, mask=events.mask_setting(manifest), junit=args.junit or [], passthrough=True,
                                 allow_no_count=args.allow_no_count, allow_skips=args.allow_skips, adapter=adapter)
        check = {'id': 'test-run', 'argv': command, 'cwd': str(root), 'kind': 'build', 'timeout': float(args.timeout), 'required': True, '_env': {'PYTHONUNBUFFERED': '1', **(adapter.env() if adapter is not None else {})},
                 'resources': ['test-run:' + str(Path(root).resolve()) + ':' + (step or events.NO_STEP)]}
        report = run_validation(root, [check], observer=recorder)
    except (OSError, ValueError) as error:
        print(f'BLOCKED: {error}', file=sys.stderr)
        return 2
    result = (report.get('results') or [{}])[0]
    code = result.get('exit_code')
    last = recorder.last or {}
    where = f", events {last['file']}" if last.get('file') else ''
    print(f"test-run: step {step or '-'} ({source}), attempt {last.get('attempt', '-')}: {last.get('status', result.get('status'))}{where}", file=sys.stderr)
    if code is None:
        print(f"BLOCKED: {result.get('reason') or report.get('reason') or 'the command did not run'}", file=sys.stderr)
        return 127 if 'Missing prerequisites' in (result.get('reason') or '') else 2
    if (result.get('reason') or '').startswith('Timeout'):
        return 124
    return int(code)


def register(subparsers):
    parser = subparsers.add_parser('test-run', help='Run a test command through skillsync and record its events for the status page (a step of a plan)')
    parser.add_argument('path', help='project directory (use . for the current one)')
    parser.add_argument('--run', help='run id (.skillsync/runs/<id>/); default: the only run of the project')
    parser.add_argument('--step', help='plan step id (default: the only step "w toku" in the checkpoints)')
    parser.add_argument('--junit', action='append', help='JUnit XML or TRX report file or directory written by the command (default places: dotnet, Maven, Gradle)')
    parser.add_argument('--allow-no-count', metavar='REASON', help='accept a passing command whose number of executed tests is unknown (the reason is recorded and shown)')
    parser.add_argument('--allow-skips', action='store_true', help='accept skipped tests (recorded)')
    parser.add_argument('--format', default='auto', metavar='NAME', help='output adapter: auto (by the tool), none, or a name (live state of single tests, stage 2)')
    parser.add_argument('--import', dest='import_file', metavar='FILE', help='store an events file written on another machine (needs --step and --source-sha)')
    parser.add_argument('--source-sha', help='with --import: the commit the imported run was made for')
    parser.add_argument('--timeout', type=float, default=3600, help='seconds (default 3600)')
    parser.add_argument('command', nargs='*', help='-- <command...> (after the options, separated by --)')
    parser.set_defaults(func=_command)
