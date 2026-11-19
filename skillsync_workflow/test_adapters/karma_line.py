"""The shared reading of the Karma `Executed N of M [(X FAILED)] [(skipped S)] [SUCCESS|ERROR|DISCONNECTED|FAILED] [(... secs ...)]` progress line.

Pure text in, numbers out; only the standard library (no import of the product modules), so the adapter, the live reader of the recorder and the
summary reader of the validation can all use it without an import cycle.

Karma's `Executed` does not count the skipped specs: `passed = N - X`, `failed = X`, `skipped = S` (the parenthesis, 0 when absent), `total = M`.
A line is a counter only when the text before `Executed` is empty or a browser label that ends with `): `, and `of M` is followed by the end of the line or by
a space and `(`, `SUCCESS`, `ERROR`, `DISCONNECTED` or `FAILED`; a log line (`LOG: 'Job: Executed 5 of 8 steps'`) or a test name is not. Terminal escapes are stripped
before the line is judged (the next line of the `progress` reporter starts with `ESC[1A ESC[2K`). Several browsers print a line each: the sum of the last line of
each label. The empty label (output of scripts and synthetic output) is one label.
"""
import re

MAX_LINE = 2000
MAX_BROWSERS = 64
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
LINE = re.compile(r'^(?:(?P<label>[^\'"]*?\)): )?Executed (?P<n>\d+) of (?P<m>\d+)(?P<rest>$|\s.*$)')
RESTART = re.compile(r'^\s*(?:$|\(|SUCCESS\b|ERROR\b|DISCONNECTED\b|FAILED\b)')
FAILED = re.compile(r'\((\d+) FAILED\)')
SKIPPED = re.compile(r'\(skipped (\d+)\)')


def parse_line(text):
    """`(label, executed, total, failed, skipped)` of a Karma counter line, or None for any other text."""
    if len(text) > MAX_LINE:
        return None
    text = ANSI.sub('', text).strip()
    found = LINE.match(text)
    if not found:
        return None
    rest = found.group('rest')
    if not RESTART.match(rest):
        return None
    failed, skipped = FAILED.search(rest), SKIPPED.search(rest)
    return (found.group('label') or '', int(found.group('n')), int(found.group('m')),
            int(failed.group(1)) if failed else 0, int(skipped.group(1)) if skipped else 0)


class Tally:
    """The last counter line of every browser label; `totals()` is their sum."""

    def __init__(self):
        self.browsers = {}   # label -> (executed, failed, skipped, total)

    def feed(self, text):
        """True when `text` is a counter line and was taken."""
        parsed = parse_line(text)
        if parsed is None:
            return False
        label, executed, total, failed, skipped = parsed
        if label not in self.browsers and len(self.browsers) >= MAX_BROWSERS:
            return False
        self.browsers[label] = (executed, failed, skipped, total)
        return True

    def totals(self):
        """`{'executed', 'passed', 'failed', 'skipped', 'total', 'unexecuted'}` summed over the browsers, or None before any counter line."""
        if not self.browsers:
            return None
        executed, failed, skipped, total = (sum(v[i] for v in self.browsers.values()) for i in range(4))
        passed = sum(max(0, v[0] - v[1]) for v in self.browsers.values())
        unexecuted = sum(max(0, v[3] - v[0] - v[2]) for v in self.browsers.values())   # specs that neither ran nor were skipped (an interrupted run)
        return {'executed': executed, 'passed': passed, 'failed': failed, 'skipped': skipped, 'total': total, 'unexecuted': unexecuted}


def read_output(output):
    """`Tally.totals()` of a whole output (lines split on newlines and carriage returns), or None when it has no counter line."""
    if 'Executed' not in (output or ''):
        return None
    tally = Tally()
    for line in re.split(r'[\r\n]+', output or ''):
        tally.feed(line)
    return tally.totals()
