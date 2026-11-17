"""Replays a recorded run (a chunk fixture of `tests/fixtures/test-runs/*.jsonl`) through an adapter the way `quality._stream` delivers it.

A fixture is one `{"meta": {...}}` record (tool, version, command, environment) and one `{"t", "fd", "data"}` record per chunk read from the pipe, in arrival
order. The replay keeps one unfinished tail per stream: complete lines go to `feed`, the tail that remains (when not empty) to `partial`, exactly like the
reader. Emits are recorded in order by `Recorder` (a stand-in for the event writer: no coalescing), so a test can assert that `running` came before the result.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURES = ROOT / 'tests' / 'fixtures' / 'test-runs'

from skillsync_workflow import test_adapters  # noqa: E402


class Recorder:
    """Writer stand-in: keeps every emit in order."""

    def __init__(self):
        self.calls = []

    def collected(self, total, names=None):
        self.calls.append(('collected', total, list(names) if names is not None else None))

    def test(self, test_id, name, state, group=None, ms=None, msg=None):
        self.calls.append(('test', test_id, name, state, group, ms, msg))

    def counts(self, **kw):
        self.calls.append(('counts', kw))

    def emitted(self):
        return [c for c in self.calls if c[0] == 'test']

    def states(self, test_id):
        return [c[3] for c in self.emitted() if c[1] == test_id]

    def last(self):
        """`{id: (state, msg)}` the page would show (the last state wins)."""
        final = {}
        for c in self.emitted():
            final[c[1]] = (c[3], c[6] if c[6] else final.get(c[1], (None, None))[1])
        return final


def load(name):
    rows = [json.loads(line) for line in (FIXTURES / name).read_text(encoding='utf-8').splitlines() if line.strip()]
    return rows[0]['meta'], rows[1:]


def chunks_from_text(text, size=7, fd='out'):
    """Cuts a text into small chunks (a pipe may deliver any split): for the tests that vary the chunk size."""
    return [{'t': i / 1000, 'fd': fd, 'data': text[i:i + size]} for i in range(0, len(text), size)]


def replay(adapter_name, chunks, argv=None, tool=None, exit_code=0, reports=(), collect=None, adapter=None, cwd=None):
    """Runs `adapter_name` over `chunks`; returns `(recorder, adapter, ctx, end_result)`. `collect(argv)` stands in for `ctx.collect`."""
    recorder = Recorder()
    argv = list(argv or [adapter_name])
    adapter = adapter or test_adapters.resolve(adapter_name, argv, tool or adapter_name)
    if adapter is None:
        return recorder, None, None, None
    ctx = test_adapters.AdapterContext(argv, cwd or ROOT, ROOT, recorder)
    if collect is not None:
        ctx.collect = collect
    adapter.begin(ctx)
    tails = {'out': '', 'err': ''}
    output = []
    for chunk in chunks:
        fd = chunk['fd']
        lines = (tails[fd] + chunk['data']).split('\n')
        tails[fd] = lines.pop()
        for line in lines:
            line = line.rstrip('\r')
            output.append(line)
            adapter.feed(fd, line, ctx)
        if tails[fd]:
            adapter.partial(fd, tails[fd], ctx)
    for fd in ('out', 'err'):
        if tails[fd]:
            output.append(tails[fd])
            adapter.feed(fd, tails[fd].rstrip('\r'), ctx)
    skipped = adapter.end(ctx, exit_code, '\n'.join(output), [Path(r) for r in reports])
    return recorder, adapter, ctx, skipped


def replay_fixture(adapter_name, fixture, **kw):
    meta, chunks = load(fixture)
    kw.setdefault('argv', meta['command'])
    kw.setdefault('tool', meta['tool'])
    kw.setdefault('exit_code', meta['exit'])
    return replay(adapter_name, chunks, **kw)


def stream_text(name, fd='out'):
    """The whole text one stream of a fixture carried (for the collector stand-ins)."""
    return ''.join(c['data'] for c in load(name)[1] if c['fd'] == fd)


def rechunk(chunks, size):
    """The same bytes cut into chunks of `size` characters per stream, keeping the arrival order of the streams as far as the cuts allow."""
    out, pos = [], 0
    for c in chunks:
        for i in range(0, len(c['data']), size):
            out.append({'t': c['t'], 'fd': c['fd'], 'data': c['data'][i:i + size]})
    return out


def audit(module_file):
    """Constructs an adapter must not use (it only reads text and emits through `ctx.emit`): file or process access, the network, signals, threads, sleeps.
    Returns the list of offending names found in the source of `module_file`."""
    import ast
    tree = ast.parse(Path(module_file).read_text(encoding='utf-8'))
    banned_modules = {'os', 'subprocess', 'socket', 'threading', 'time', 'signal', 'shutil', 'tempfile', 'urllib', 'http', 'pathlib', 'ctypes', 'multiprocessing', 'asyncio'}
    banned_names = {'open', 'exec', 'eval', 'compile', '__import__', 'input', 'print'}
    banned_attrs = {'write_text', 'write_bytes', 'unlink', 'mkdir', 'rmdir', 'rename', 'kill', 'sleep', 'system', 'popen', 'Popen', 'read_text', 'read_bytes', 'touch'}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split('.')[0] in banned_modules]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or '').split('.')[0] in banned_modules:
            found.append(node.module)
        elif isinstance(node, ast.Name) and node.id in banned_names:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in banned_attrs:
            found.append('.' + node.attr)
    return found


def replay_to_files(adapter_name, chunks, argv=None, tool=None, exit_code=0, reports=(), collect=None):
    """Like `replay`, but through the real event writer in a temporary run directory; returns `(events, tree)` where `tree` lists every file created."""
    import tempfile
    from skillsync_workflow import test_events as ev
    with tempfile.TemporaryDirectory(prefix='adapter-replay-') as tmp:
        root = Path(tmp)
        path, _ = ev.new_attempt(root, 'R1', 'F1.1')
        writer = ev.EventWriter(path)
        argv = list(argv or [adapter_name])
        adapter = test_adapters.resolve(adapter_name, argv, tool or adapter_name)
        ctx = test_adapters.AdapterContext(argv, root, root, writer)
        if collect is not None:
            ctx.collect = collect
        adapter.begin(ctx)
        tails = {'out': '', 'err': ''}
        for chunk in chunks:
            fd = chunk['fd']
            lines = (tails[fd] + chunk['data']).split('\n')
            tails[fd] = lines.pop()
            for line in lines:
                adapter.feed(fd, line.rstrip('\r'), ctx)
            if tails[fd]:
                adapter.partial(fd, tails[fd], ctx)
        adapter.end(ctx, exit_code, '', [Path(r) for r in reports])
        writer.close()
        events = [e for e in ev.read_events(path)[0] if e.get('t') in ('collected', 'test', 'counts')]
        tree = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())   # `/` on every system
        return events, tree
