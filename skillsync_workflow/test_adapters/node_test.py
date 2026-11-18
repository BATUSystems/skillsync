"""Adapter `node-test` (CORE-TAP): `node --test` with the reporter `tap` or `spec` (Node 20/22/24).

The stream decides: the first meaningful line `TAP version N` hands the stream to `TapParser` (tap.py, header required), a marker `▶ ✔ ✖ ﹣` after the indent starts `SpecParser`;
lines before the first of them are skipped, any other stream emits nothing; there is no switching after the choice. `live = 'completion'` (spec 4A, 5).
The adapter only reads text (no process, no file).
"""
import re
from skillsync_workflow.test_adapters import Adapter, register
from skillsync_workflow.test_adapters.tap import TapParser, HEADER, ANSI, MAX_LINE, MAX_NAME

MARK = re.compile(r'^(?P<indent> *)(?P<mark>[▶✔✖﹣]) (?P<rest>.+)$')
RES = re.compile(r'^(?P<name>.*) \((?P<ms>\d+(?:\.\d+)?)ms\)(?: # (?P<note>.*))?$')
TESTS = re.compile(r'^ℹ tests (\d+)\s*$')
SUITES = re.compile(r'^ℹ suites (\d+)\s*$')
ERR = re.compile(r'^\w*Error\b')


class G:
    def __init__(s, indent, name): s.indent, s.name, s.failed = indent, name, False


class SpecParser:
    def __init__(self):
        self.groups = []; self.seen = {}; self.dropped = 0; self.n = None; self.s = None
        self.L = 0; self.results = 0; self.done = False; self.window = []; self.pending = None

    def flush(self, ctx):
        p, self.pending = self.pending, None
        if not p: return
        msg = p['msg']
        if msg == "'test failed'" or (msg is None and p['group'] is None):   # a file that did not load: its error is the stderr block before the line
            err = next((c for c in reversed(p['window']) if ERR.match(c)), None)
            msg = err or ('test failed' if msg else None)
        if msg is not None:
            ctx.emit.test(p['id'], p['name'], 'failed', group=p['group'], ms=p['ms'], msg=msg)

    def feed_line(self, text, ctx):
        if len(text) > MAX_LINE: return
        text = ANSI.sub('', text).rstrip()
        if self.done:
            m = SUITES.match(text)
            if m and self.s is None: self.s = int(m.group(1))
            return
        p = self.pending
        marker = MARK.match(text)
        if p:
            if marker or TESTS.match(text): self.flush(ctx)
            else:
                ind = len(text) - len(text.lstrip(' '))
                p['seen'] += 1
                if p['seen'] <= 30 and text.strip() and ind > p['indent']:
                    if p['msg'] is None: p['msg'] = text.strip()
                    return
                if p['seen'] <= 30 and not text.strip(): return
                if p['seen'] <= 30 and ind > p['indent']: return
                self.flush(ctx)
        m = TESTS.match(text)
        if m:
            self.n = int(m.group(1)); self.done = True; return
        if not marker:
            if text.strip(): self.window = (self.window + [text.strip()])[-30:]
            return
        indent = len(marker.group('indent')); mark = marker.group('mark'); rest = marker.group('rest')
        if mark == '▶':
            while self.groups and self.groups[-1].indent >= indent: self.groups.pop()
            self.groups.append(G(indent, rest)); return
        r = RES.match(rest)
        if not r:
            self.dropped += 1; return
        self.L += 1
        name, ms, note = r.group('name'), float(r.group('ms')), r.group('note')
        window, self.window = self.window, []
        closing = None
        for i, g in enumerate(self.groups):
            if g.indent == indent and g.name == name: closing = i
        if closing is not None and closing == len(self.groups) - 1:
            g = self.groups.pop()
            if mark != '✖' or g.failed: return
        else:
            while self.groups and self.groups[-1].indent >= indent: self.groups.pop()
        parents = [g for g in self.groups if g.indent < indent]
        if mark == '﹣': state, msg = 'skipped', note
        elif note is not None: state, msg = 'skipped', f'todo: {note}'
        elif mark == '✔': state, msg = 'passed', None
        else: state, msg = 'failed', None
        group = ' > '.join(g.name for g in parents) or None
        base = f'{group}::{name}' if group else name
        c = self.seen[base[:MAX_NAME]] = self.seen.get(base[:MAX_NAME], 0) + 1
        tid = base if c == 1 else f'{base} ({c})'
        if c > 1: name = f'{name} ({c})'
        self.results += 1
        ctx.emit.test(tid, name, state, group=group, ms=int(round(ms)), msg=msg)
        if state == 'failed':
            for g in parents: g.failed = True
            self.pending = {'id': tid, 'name': name, 'group': group, 'ms': int(round(ms)), 'indent': indent, 'msg': None, 'seen': 0, 'window': window}

    def finish(self, ctx, exit_code):
        self.flush(ctx)
        if self.n is None or self.s is None or self.dropped or self.groups or self.results == 0: return False
        return self.n == self.L - self.s


@register
class NodeTestAdapter(Adapter):
    name = 'node-test'
    tools = ('node',)
    live = 'completion'
    VALUE = {'--require', '-r', '--import', '--loader', '--env-file', '--test-reporter', '--test-reporter-destination', '--test-name-pattern', '--test-skip-pattern', '-C', '--conditions'}

    def accepts(self, argv):
        from skillsync_workflow.test_run import unwrap_command
        core = unwrap_command(list(argv))
        if not core or re.sub(r'\.exe$', '', core[0].replace('\\', '/').rsplit('/', 1)[-1].lower()) != 'node': return False
        seen_test = False; reps = []; dest = False; i = 1
        while i < len(core):
            t = core[i]
            if not t.startswith('-'): break
            if t == '--': break
            if t == '--test': seen_test = True
            elif '=' in t:
                k, v = t.split('=', 1)
                if k == '--test-reporter': reps.append(v)
                if k == '--test-reporter-destination': dest = True
            elif t in self.VALUE:
                i += 1
                if t == '--test-reporter' and i < len(core): reps.append(core[i])
                if t == '--test-reporter-destination': dest = True
            i += 1
        if not seen_test: return False
        if any(r not in ('tap', 'spec') for r in reps): return False
        if dest and len(set(reps)) > 1: return False
        return True

    def __init__(self):
        self.parser = None; self.kind = None

    def begin(self, ctx): pass

    def feed(self, stream, text, ctx):
        if stream != 'out': return
        if self.parser is None:
            t = ANSI.sub('', text).strip()
            if HEADER.match(t): self.parser = TapParser(explicit=False)
            elif t[:1] in '▶✔✖﹣': self.parser = SpecParser()
            else: return
        self.parser.feed_line(text, ctx)

    def end(self, ctx, exit_code, output, reports):
        return self.parser is not None and self.parser.finish(ctx, exit_code)

