"""180-D (C8 rest): the first line of a pre-implement report, the sections of risk of a spec, the first line of a README in the repo map and the first line
of a skill in `skillsync list` are read with `common.file_lines`. A character that `str.splitlines()` treats as a line end (VT, FF, FS, GS, RS, NEL, LS, PS)
inside such a line used to cut it. The characters are written as escapes (never as literals); the temporary directories have plain names and are checked to
lie inside the temporary directory (a name built from test data once pointed to the root of the drive on Windows)."""
import contextlib
import io
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import cli, repomap, status  # noqa: E402

RARE = {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': '\u2028', 'PS': '\u2029'}
assert [ord(char) for char in RARE.values()] == [0x0b, 0x0c, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ends-rest-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def inside(self, path):
        resolved = Path(path).resolve()
        self.assertTrue(resolved.is_relative_to(self.root), f'{resolved} lies outside {self.root}')
        return resolved


class RiskSectionsOfASpec(unittest.TestCase):
    """`pre_implement_sections`: a section whose first line is `TODO` is empty, a first line `TODO<char>text` is a real line of text."""
    SPEC = '# Spec\n\n## Kontrakty\n\nTODO{c}opis kontraktu API\n\n## Inne\n\ntekst\n'

    def test_a_rare_separator_inside_the_first_line_keeps_the_line_whole_so_the_section_is_not_empty(self):
        self.assertEqual(status.pre_implement_sections(self.SPEC.format(c='\n')), [])        # control: TODO alone on its line is an empty section
        self.assertEqual(status.pre_implement_sections(self.SPEC.format(c=' ')), ['Kontrakty'])   # control: a space
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(status.pre_implement_sections(self.SPEC.format(c=char)), ['Kontrakty'])

    def test_a_separator_at_the_end_of_an_empty_marker_still_means_empty(self):
        # `TODO<char>` alone on its line: the character is white space at the edge (as when `splitlines()` cut it off), the section stays empty
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(status.pre_implement_sections('# Spec\n\n## Kontrakty\n\nTODO' + char + '\n\n## Inne\n\ntekst\n'), [])
                self.assertEqual(status.pre_implement_sections('# Spec\n\n## Migracja\n\nnie dotyczy' + char + '\n'), [])

    def test_lf_and_crlf_read_the_same(self):   # a lone CR file is not read here at all: the heading pattern (`^...$` MULTILINE) knows only `\n` (outside this change)
        for name, end in (('LF', '\n'), ('CRLF', '\r\n')):
            with self.subTest(line_end=name):
                self.assertEqual(status.pre_implement_sections(self.SPEC.format(c='\u2028').replace('\n', end)), ['Kontrakty'])
                self.assertEqual(status.pre_implement_sections(self.SPEC.format(c=end).replace('\n', end)), [])


class PreImplementReport(TempCase):
    """`_pre_implement_reports`: the verdict is read from the first non-empty line, taken whole."""

    def reports(self, text):
        directory = self.inside(self.root / '.skillsync' / 'runs' / 'run1')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'pre-implement.md').write_bytes(text.encode('utf-8'))
        return status._pre_implement_reports(self.root, 'run1')

    def test_the_verdict_line_is_taken_whole(self):
        self.assertEqual(self.reports('Werdykt: READY\n'), [('pre-implement.md', 'READY')])
        for name, char in RARE.items():
            with self.subTest(char=name):
                # a separator at the edge is white space (`strip`): still the verdict; the same after a lone `\r` and `\r\n`
                self.assertEqual(self.reports(f'{char}Werdykt: READY{char}\n'), [('pre-implement.md', 'READY')])

    def test_text_after_the_verdict_behind_a_rare_separator_is_not_a_verdict_line(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                # before: the line was cut after READY and the verdict was recorded; it is one line with other text, as an editor and git show it
                self.assertEqual(self.reports(f'Werdykt: READY{char}ale warunkowo\n'), [])

    def test_the_first_line_of_only_separators_is_skipped(self):
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(self.reports(f'{char}\nWerdykt: BLOCKED\n'), [('pre-implement.md', 'BLOCKED')])


class FirstLineOfAReadme(TempCase):
    def purpose(self, text):
        (self.inside(self.root / 'README.md')).write_bytes(text.encode('utf-8'))
        return repomap._purpose(self.root, '', ['README.md'])

    def test_a_rare_separator_inside_the_first_line_does_not_cut_the_purpose(self):
        self.assertEqual(self.purpose('Narzędzie do planów\n'), 'Narzędzie do planów')
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(self.purpose(f'Narzędzie{char}do planów\n'), f'Narzędzie{char}do planów')

    def test_lf_crlf_and_a_lone_cr_give_the_same_first_line(self):
        for name, end in (('LF', '\n'), ('CRLF', '\r\n'), ('CR', '\r')):
            with self.subTest(line_end=name):
                self.assertEqual(self.purpose(f'# Tytuł{end}{end}Opis{end}'), 'Tytuł')


class FirstLineOfASkill(TempCase):
    def listed(self, text):
        skill = self.inside(self.root / 'SKILL.md')
        skill.write_bytes(text.encode('utf-8'))
        layer = SimpleNamespace(id='demo')
        layerset = SimpleNamespace(skill_providers=lambda _name: [layer], resolve=lambda _path: (layer, skill))
        out = io.StringIO()
        with mock.patch.object(cli, 'load_config', return_value={}), mock.patch.object(cli, 'resolve_project_layers', return_value=layerset), \
                mock.patch.object(cli, 'available_skills', return_value=['alfa']), contextlib.redirect_stdout(out):
            cli.cmd_list(Namespace(layers=[]))
        return [line for line in out.getvalue().split('\n') if line.startswith('  ')]   # the output is split at `\n` only: the rare characters are part of the line

    def test_the_description_is_the_first_line_whole(self):
        self.assertEqual(self.listed('Opis umiejętności\n'), ['  • alfa                           [demo] Opis umiejętności'])
        for name, char in RARE.items():
            with self.subTest(char=name):
                self.assertEqual(self.listed(f'Opis{char}umiejętności\n'), [f'  • alfa                           [demo] Opis{char}umiejętności'])

    def test_crlf_and_a_lone_cr_end_the_first_line(self):
        for name, end in (('CRLF', '\r\n'), ('CR', '\r')):
            with self.subTest(line_end=name):
                self.assertEqual(self.listed(f'Opis{end}drugi{end}'), ['  • alfa                           [demo] Opis'])


class TheTestDirectoryIsInsideTheTemporaryDirectory(TempCase):
    def test_a_path_built_from_a_rare_character_would_be_refused(self):
        with self.assertRaises(AssertionError):
            self.inside(self.root.parent / 'elsewhere')


if __name__ == '__main__':
    unittest.main()
