"""CLAUDE-0: skills that take arguments carry an `argument-hint` (the autocomplete hint of Claude Code); the others do not."""
import re
import unittest
from pathlib import Path

import yaml

SKILLS = Path(__file__).resolve().parents[1] / 'library' / 'skills'   # relative to the repository: no environment variable (a skipped test proves nothing)

# The contract (spec `2026-11-21-claude-0-argument-hint`, K3). A new skill that takes arguments needs an entry here: the list is deliberate.
EXPECTED = {
    'feature': '<opis zadania | KLUCZ-123 | ścieżka FRD>',
    'bugfix': '<opis błędu | KLUCZ-123>',
    'dispatch': '<cel> | status | drain | resume <run-id>',
    'codereview': '<link PR Azure> [ZADANIA-JIRA] | --base <ref> | <ścieżka planu>',
    'pr-run': '<brief | ścieżka planu> [--go] | --resume <PR | plan>',
    'docx-to-markdown': '<ścieżka do pliku DOCX>',
}
MAX_LENGTH = 120


def frontmatter(path):
    text = path.read_text(encoding='utf-8')
    match = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if match is None:
        raise AssertionError(f'{path}: missing frontmatter')
    return match.group(1), yaml.safe_load(match.group(1))


class ArgumentHint(unittest.TestCase):
    def skills(self):
        found = sorted(SKILLS.glob('*/SKILL.md'))
        self.assertGreater(len(found), 20, 'the skills of the library were not found')
        return found

    def test_every_skill_of_the_contract_exists(self):
        for name in EXPECTED:
            with self.subTest(skill=name):
                self.assertTrue((SKILLS / name / 'SKILL.md').is_file())

    def test_the_skills_with_arguments_have_exactly_the_hint_of_the_contract(self):
        for name, hint in EXPECTED.items():
            with self.subTest(skill=name):
                raw, data = frontmatter(SKILLS / name / 'SKILL.md')
                self.assertEqual(data.get('argument-hint'), hint)

    def test_the_hint_is_one_short_quoted_string_without_a_final_period(self):
        for name in EXPECTED:
            with self.subTest(skill=name):
                raw, data = frontmatter(SKILLS / name / 'SKILL.md')
                value = data['argument-hint']
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
                self.assertLessEqual(len(value), MAX_LENGTH)
                self.assertFalse(value.rstrip().endswith('.'))
                line = next(item for item in raw.split('\n') if item.startswith('argument-hint:'))
                self.assertRegex(line, r"^argument-hint: '.*'$", 'the value is quoted like `description` (an unquoted colon or bracket breaks the YAML)')

    def test_no_other_skill_has_the_field(self):
        for path in self.skills():
            name = path.parent.name
            if name in EXPECTED:
                continue
            with self.subTest(skill=name):
                raw, data = frontmatter(path)
                self.assertNotIn('argument-hint', data, f'{name} has no arguments: add it to EXPECTED in this test if it does')


if __name__ == '__main__':
    unittest.main()
