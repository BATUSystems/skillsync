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

RARE = {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': '\u2028', 'PS': '\u2029'}
TABLE = "| Id | Krok |\n|---|---|\n| F1 | Ekran{c}logowania [UI] |\n| F2 | Backend |\n| F3 [UI]{c}x | Panel |\n"
assert [ord(char) for char in RARE.values()] == [0x0b, 0x0c, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]   # the characters are the intended ones, not spaces
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
                text = decode_text(TABLE.format(c='\u2028').encode('utf-8').replace(b'\n', end))
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


class AppendMissingLinesAtRareSeparators(unittest.TestCase):
    ENTRIES = ['copilot-instructions.md', 'skills/', '.skillsync.yaml']

    def test_an_entry_that_only_looks_present_after_a_cut_is_appended_and_the_old_bytes_stay(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                original = f'copilot-instructions.md\nskills/{char}old\n'
                updated = append_missing_lines(original, self.ENTRIES)
                self.assertTrue(updated.startswith(original), 'the existing text stays byte for byte')
                self.assertEqual(updated[len(original):], 'skills/\n.skillsync.yaml\n')

    def test_an_entry_that_is_a_line_of_its_own_is_not_appended_again(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                original = f'copilot-instructions.md\nskills/\n.skillsync.yaml\nnote{char}text\n'
                self.assertIsNone(append_missing_lines(original, self.ENTRIES))

    def test_crlf_and_a_lone_cr_file_keeps_its_bytes_and_its_line_end_style(self):
        original = 'copilot-instructions.md\r\nskills/\u2028old\r\n'
        updated = append_missing_lines(original, self.ENTRIES)
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated[len(original):], 'skills/\r\n.skillsync.yaml\r\n')
        self.assertIsNone(append_missing_lines('a\rb\r', ['a', 'b']))       # a lone CR is a line end for the check, as before
        self.assertTrue(append_missing_lines('a\rb\r', ['c']).startswith('a\rb\r'))   # the old bytes stay (the added line follows them)

    def test_the_key_function_still_applies(self):
        self.assertIsNone(append_missing_lines('  skills/  \n', ['skills/'], key=str.strip))
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertIsNone(append_missing_lines(f'{char}skills/{char}\n', ['skills/'], key=str.strip))            # `strip` takes it off the edges
                self.assertEqual(append_missing_lines(f'skills/{char}old\n', ['skills/'], key=str.strip), f'skills/{char}old\nskills/\n')

    def test_a_bom_and_undecodable_bytes_leave_the_old_bytes_and_the_check_as_before(self):
        text = '\ufeffcopilot-instructions.md\nskills/\n'
        self.assertEqual(append_missing_lines(text, ['skills/', '.skillsync.yaml']), text + '.skillsync.yaml\n')
        raw = b'skills/\n\xff\xfe broken\n'.decode('utf-8', 'surrogateescape')                     # as `link` reads an ignore file
        updated = append_missing_lines(raw, ['skills/', '.skillsync.yaml'])
        self.assertEqual(updated.encode('utf-8', 'surrogateescape'), b'skills/\n\xff\xfe broken\n.skillsync.yaml\n')


class OutsideFencesAtRareSeparators(unittest.TestCase):
    TEXT = "tekst\n```\nkod{c}```\nnie kod\n```\nkod2\n```\nkoniec\n"

    def test_a_rare_separator_inside_a_code_block_does_not_change_the_state_of_the_fence(self):
        expected = quality._outside_fences(self.TEXT.format(c=' '))
        self.assertEqual(expected, 'tekst\nkod2')
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(quality._outside_fences(self.TEXT.format(c=char)), expected)

    def test_a_rare_separator_in_the_line_of_a_fence_follows_commonmark(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                # "```<char>python" opens a block, and inside it does not close it; "```<char>" (only white space after the marker) closes it
                opened = f"a\n```{char}python\nkod\n```{char}python\nstill code\n```\nb\n"
                self.assertIn('b', quality._outside_fences(opened))
                self.assertNotIn('still code', quality._outside_fences(opened))
                self.assertNotIn('kod', quality._outside_fences(opened))

    def test_crlf_gives_the_same_text(self):
        text = decode_text(self.TEXT.format(c='\u2028').encode('utf-8').replace(b'\n', b'\r\n'))
        self.assertEqual(quality._outside_fences(text), 'tekst\nkod2')


if __name__ == '__main__':
    unittest.main()
