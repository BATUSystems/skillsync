"""Harness for the tests of the output adapters (180-A, stage 2): feeds the lines of a fixture to an adapter and returns the events it emitted."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import test_adapters, test_events as ev  # noqa: E402


def run_adapter(name, out_lines=(), err_lines=(), exit_code=0, reports=(), argv=None, cwd=None):
    """Runs the adapter `name` over the lines (stdout first, then stderr) and the report files; returns the events it emitted
    (`collected`, `test`, `counts`) and a dict with the result of `end` (`{'events': [...], 'skip_reports': bool, 'env': {...}}`)."""
    with tempfile.TemporaryDirectory(prefix='adapter-harness-') as tmp:
        root = Path(tmp)
        path, _ = ev.new_attempt(root, 'R1', 'F1.1')
        writer = ev.EventWriter(path)
        adapter = test_adapters.resolve(name, argv or [name], name)
        ctx = test_adapters.AdapterContext(argv or [name], cwd or root, root, writer)
        adapter.begin(ctx)
        for line in out_lines:
            adapter.feed('out', line, ctx)
        for line in err_lines:
            adapter.feed('err', line, ctx)
        output = '\n'.join([*out_lines, *err_lines])
        skip = bool(adapter.end(ctx, exit_code, output, [Path(r) for r in reports]))
        writer._counts and writer.counts(*writer._counts[:4], total=writer._counts[4], force=True)
        writer.close()
        events = [e for e in ev.read_events(path)[0] if e.get('t') in ('collected', 'test', 'counts')]
        return {'events': events, 'skip_reports': skip, 'env': adapter.env()}


def tests_of(result):
    """`[(id, state)]` of the `test` events in the order of the last state of each id (what the page would show)."""
    final = {}
    for event in result['events']:
        if event['t'] == 'test':
            final[event['id']] = event['state']
    return list(final.items())
