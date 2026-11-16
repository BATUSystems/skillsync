"""170-C, C8 items 1-3: the plan table parser (`[UI]` phases and the prototype gate), the check of missing lines of generated ignore files and the
fence stripper of the instruction check split the text of files with `common.file_lines`. A character that `str.splitlines()` treats as a line end
(VT, FF, FS, GS, RS, NEL, LS, PS) inside a line used to cut it: the `[UI]` mark after it was lost together with the rest of the table, the entry
of an ignore file looked present, and a code fence changed its state. The bytes of the line ends are written by the tests."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import quality  # noqa: E402
from skillsync_workflow.common import append_missing_lines, decode_text  # noqa: E402
from skillsync_workflow.plan_phases import parse_plan_phases  # noqa: E402

RARE = {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': ' ', 'PS': ' '}
TABLE = "| Id | Krok |\n|---|---|\n| F1 | Ekran{c}logowania [UI] |\n| F2 | Backend |\n| F3 [UI]{c}x | Panel |\n"
EXPECTED = {'phases': ['F1', 'F2', 'F3'], 'ui_phases': ['F1', 'F3'], 'stray_ui': 0}


class PlanPhasesAtRareSeparators(unittest.TestCase):
    def test_a_rare_separator_inside_a_row_loses_neither_the_ui_mark_nor_the_rest_of_the_table(self):
        self.assertEqual(parse_plan_phases(TABLE.format(c=' ')), EXPECTED)
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(parse_plan_phases(TABLE.format(c=char)), EXPECTED)

    def test_the_table_reads_the_same_with_lf_crlf_and_a_lone_cr(self):
        for name, end in (('LF', b'\n'), ('CRLF', b'\r\n'), ('CR', b'\r')):
            with self.subTest(line_end=name):
                text = decode_text(TABLE.format(c=' ').encode('utf-8').replace(b'\n', end))
                self.assertEqual(parse_plan_phases(text), EXPECTED)


class GateAtRareSeparators(unittest.TestCase):
    """At the level of the gate (`prototype gate --plan`): a plan with a `[UI]` phase and no `Prototyp:` line and no bypass is BLOCKED; before, the
    phase was lost in a row with such a character and the gate saw a plan without UI phases."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='gate-ends-')).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        (self.root / 'docs' / 'plans').mkdir(parents=True)

    def gate(self, table, end=b'\n'):
        text = '# Plan\n\nStatus: draft\n\n## Kroki\n\n' + table
        (self.root / 'docs' / 'plans' / 'p.md').write_bytes(text.encode('utf-8').replace(b'\n', end))
        return pt.prototype_gate(self.root, {}, plan='docs/plans/p.md')

    def test_a_ui_phase_behind_a_rare_separator_still_needs_a_prototype(self):
        control = self.gate(TABLE.format(c=' '))
        self.assertEqual((control['status'], control.get('error'), control['uiPhases']), ('BLOCKED', 'ui_phase_without_prototype', ['F1', 'F3']), control)
        for name, char in RARE.items():
            for line_end in (b'\n', b'\r\n'):
                with self.subTest(char=name, line_end=line_end):
                    report = self.gate(TABLE.format(c=char), line_end)
                    self.assertEqual((report['status'], report.get('error'), report['uiPhases']), ('BLOCKED', 'ui_phase_without_prototype', ['F1', 'F3']), report)


if __name__ == '__main__':
    unittest.main()
