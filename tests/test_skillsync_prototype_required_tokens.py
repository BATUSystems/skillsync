"""CORE-1190-C8: `prototype.parse_required_tokens` reads lines like the other readers of project files (`common.file_lines`):
a rare separator inside an entry (VT, FF, NEL, LS, PS) must not cut it in two, CRLF, LF and a lone CR still separate entries."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from skillsync_workflow import prototype  # noqa: E402

SEPARATORS = {'VT': '\x0b', 'FF': '\x0c', 'FS': '\x1c', 'GS': '\x1d', 'RS': '\x1e', 'NEL': '\x85', 'LS': ' ', 'PS': ' '}


class ParseRequiredTokensTests(unittest.TestCase):
    def test_a_rare_separator_inside_an_entry_does_not_split_it(self):
        for name, sep in SEPARATORS.items():
            with self.subTest(separator=name):
                self.assertEqual(prototype.parse_required_tokens(f'--a{sep}b\n--c\n'), [f'--a{sep}b', '--c'])

    def test_crlf_lf_and_a_lone_cr_still_separate_entries(self):
        self.assertEqual(prototype.parse_required_tokens('--a\r\n--b\n--c\r--d'), ['--a', '--b', '--c', '--d'])

    def test_blank_lines_comments_and_trailing_newline_are_skipped(self):
        self.assertEqual(prototype.parse_required_tokens('# comment\n\n  --a  \n   # indented comment\n--b\n'), ['--a', '--b'])
        self.assertEqual(prototype.parse_required_tokens(''), [])
        self.assertEqual(prototype.parse_required_tokens('# only a comment\n'), [])

    def test_the_library_file_gives_the_same_list_as_splitlines(self):
        text = (REPO / 'library' / 'skills' / 'prototype' / 'assets' / 'theme' / 'required-tokens.txt').read_text(encoding='utf-8')
        expected = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
        self.assertTrue(expected)
        self.assertEqual(prototype.parse_required_tokens(text), expected)


if __name__ == '__main__':
    unittest.main()
