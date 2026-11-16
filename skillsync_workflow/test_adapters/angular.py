"""Adapter of `ng test` (180-A3): the Angular CLI runs Vitest (the default of Angular 21) or Karma + Jasmine, chosen by the project, not by the command.

The adapter cannot tell which from the command (and may not read the project files), so it reads the LINES: it hands every line to both the Vitest logic and the Karma
logic of this package; the lines of one tool never match the patterns of the other (`✓ file > name` versus `<browser>: Executed N of M`), so only the tool that is
really running emits. `--format vitest` or `--format karma` forces one of them. `live = 'counts'` is the weaker of the two guarantees (Karma prints only numbers; Vitest
`completion`). NOT_VERIFIED: the output of `ng test` itself was not recorded (no Angular project was run; the two engines were run directly); its reporter may differ from
`verbose`/`progress`, in which case nothing is emitted and the run falls back to the counters of the summary and the report (`tests-reporter-missing` of `doctor`
tells how to add a reporter).
"""
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
        return 'ng' in words[:3] and 'test' in words

    def feed(self, stream, text, ctx):
        self.vitest.feed(text, ctx)
        self.karma.feed(text, ctx)

    def end(self, ctx, exit_code, output, reports):
        return self.vitest.complete()
