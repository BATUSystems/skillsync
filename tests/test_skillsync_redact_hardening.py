"""CORE-1190-MASK: `common.redact` and what used to slip through it: environment variables named `..._PAT` and `..._PWD`, a secret that hides behind
Unicode format characters (category Cf: zero-width space, bidi controls), and a lone CR as the end of a line after an unterminated quote.
The variable `PWD` itself (the working directory) is not a secret and stays readable."""
import os
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow.common import _FORMAT_HINT, redact  # noqa: E402

VALUE = "hunter2hunter2"
ZWSP, ZWJ, BIDI, BOM, SHY = "​", "‍", "⁦", "﻿", "­"


class EnvironmentNames(unittest.TestCase):
    def test_pat_and_pwd_variables_are_secrets(self) -> None:
        for name in ("GITHUB_PAT", "AZURE_DEVOPS_PAT", "DB_PWD", "SQL_PWD_PROD", "PAT_GITHUB", "PAT", "MY_PWD_2", "github_pat"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "s3cr3t-value-123"}, clear=False):
                self.assertEqual(redact("a s3cr3t-value-123 b"), "a [REDACTED] b")

    def test_names_that_only_contain_the_letters_are_not(self) -> None:
        for name in ("PATH", "PATHEXT", "PWD", "OLDPWD", "COMPAT_X", "PATCH_LEVEL", "SPATIAL", "UPWDX"):
            with self.subTest(name=name), mock.patch.dict(os.environ, {name: "plain-visible-value-123"}, clear=False):
                self.assertEqual(redact("a plain-visible-value-123 b"), "a plain-visible-value-123 b")

    def test_the_working_directory_stays_readable(self) -> None:
        with mock.patch.dict(os.environ, {"PWD": "/home/user/Repos/project-x"}, clear=False):
            self.assertEqual(redact("cwd /home/user/Repos/project-x ok"), "cwd /home/user/Repos/project-x ok")


class FormatCharacters(unittest.TestCase):
    def test_a_key_split_by_a_format_character_is_still_a_key(self) -> None:
        for mark in (ZWSP, ZWJ, BIDI, BOM, SHY):
            with self.subTest(mark=repr(mark)):
                masked = redact(f"pass{mark}word={VALUE}")
                self.assertNotIn(VALUE, masked)
                self.assertIn("[REDACTED]", masked)
                self.assertNotIn(mark, masked)

    def test_a_secret_of_the_environment_split_by_a_format_character_is_masked(self) -> None:
        with mock.patch.dict(os.environ, {"API_TOKEN": "abc" + "def" + "123456"}, clear=False):
            self.assertEqual(redact(f"x abc{ZWSP}def123456 y"), "x [REDACTED] y")

    def test_json_and_header_forms_behind_format_characters(self) -> None:
        self.assertNotIn(VALUE, redact(f'{{"to{ZWSP}ken": "{VALUE}"}}'))
        self.assertNotIn("abcdef12", redact(f"Author{ZWJ}ization: Bearer abcdef12345678"))

    def test_text_with_format_characters_and_no_secret_is_unchanged(self) -> None:
        for text in (f"zero{ZWSP}width and {BIDI}bidi{BIDI}", f"{BOM}Plan: ok", "family: \U0001F468‍\U0001F469‍\U0001F467", f"soft{SHY}hyphen"):
            with self.subTest(text=text):
                self.assertEqual(redact(text), text)

    def test_the_masked_result_of_a_visible_secret_keeps_the_rest(self) -> None:
        self.assertEqual(redact(f"password={VALUE} ok"), "password=[REDACTED] ok")


    def test_the_quick_look_covers_every_format_character_of_this_python(self) -> None:
        # a new Unicode version may add a Cf character; this test then says which one the hint (`common._FORMAT_HINT`) misses
        missing = [hex(code) for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)) == "Cf" and not _FORMAT_HINT.search(chr(code))]
        self.assertEqual(missing, [])

    def test_a_polish_text_takes_the_fast_path(self) -> None:
        self.assertIsNone(_FORMAT_HINT.search("Zażółć gęślą jaźń, ŁÓDŹ — „cytat” … ©"))


class LoneCarriageReturn(unittest.TestCase):
    def test_an_unterminated_quote_ends_at_a_lone_cr(self) -> None:
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                self.assertEqual(redact(f"password={quote}{VALUE}\rnext line"), f"password={quote}[REDACTED]\rnext line")

    def test_the_other_line_ends_are_as_before(self) -> None:
        self.assertEqual(redact(f'token="{VALUE}\nnext'), 'token="[REDACTED]\nnext')
        self.assertEqual(redact(f'token="{VALUE}\r\nnext'), 'token="[REDACTED]\r\nnext')
        self.assertEqual(redact(f'token="{VALUE}"\rnext'), 'token="[REDACTED]"\rnext')
        self.assertEqual(redact(f'token="{VALUE}'), 'token="[REDACTED]')

    def test_a_secret_with_a_lone_cr_in_the_middle_is_masked_up_to_the_cr_only(self) -> None:
        masked = redact(f'secret="{VALUE}\rpassword={VALUE}')
        self.assertNotIn(VALUE, masked)


if __name__ == "__main__":
    unittest.main()
