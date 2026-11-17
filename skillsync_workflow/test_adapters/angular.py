"""Adapter of `ng test` (180-A3): the Angular CLI runs Vitest (the default of Angular 21) or Karma + Jasmine, chosen by the project, not by the command.

The adapter cannot tell which from the command (and may not read the project files), so it reads the LINES: it hands every line to both the Vitest logic and the Karma
logic of this package; the lines of one tool do not match the patterns of the other in practice (`✓ file > name` versus `<browser>: Executed N of M`; a test whose own output
imitates the other tool could make a stray counter), so only the tool that is really running emits. `--format vitest` or `--format karma` forces one of them. `live = 'counts'` is the weaker of the two guarantees (Karma prints only numbers; Vitest
`completion`). Verified on a real `ng test` (Angular CLI 21.2.30, fixtures `angular-*`): the Vitest runner of Angular prints its DEFAULT reporter, which names no passing test, so a green or a
mixed run emits nothing live and the run falls back to the counters of the summary (`--reporters=verbose` gives the lines, with a `|project| ` prefix that the Vitest
logic drops from the id); the Karma runner prints the usual `progress` lines (`Executed N of M`).
"""
import re

from . import Adapter, register
from .karma import KarmaCore
from .vitest import VitestCore


@register
class AngularAdapter(Adapter):
    name = 'angular'
    tools = ('angular',)
    live = 'counts'

    def __init__(self):
        self.vitest = VitestCore()
        self.karma = KarmaCore()

    def accepts(self, argv):
        words = [str(word).replace('\\', '/').rsplit('/', 1)[-1].lower() for word in argv]
        for i, word in enumerate(words[:3]):
            if re.sub(r'\.(exe|cmd|bat)$', '', word) == 'ng':
                return words[i + 1:i + 2] in (['test'], ['t'])
        return False

    def hint(self, argv, has_report=False):
        parts = []
        if not any(str(word).startswith('--reporters') for word in argv):
            parts.append('of a Vitest project: add --reporters=verbose')
        if not has_report:
            parts.append('of a Karma project: add the junit reporter (karma-junit-reporter) and pass --junit <file>')
        return ('test list ' + '; '.join(parts) + ' (the counters are always recorded)') if parts else None

    def feed(self, stream, text, ctx):
        self.vitest.feed(text, ctx)
        self.karma.feed(text, ctx)

    def end(self, ctx, exit_code, output, reports):
        return self.vitest.complete()
