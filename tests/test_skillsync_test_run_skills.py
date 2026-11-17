"""180-A: the rule "tests of a plan step run through skillsync" is in the skills and roles that run tests, and the shared reference exists."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'library'
REFERENCE = LIB / 'skills/workflow/references/test-run.md'

CARRIERS = (('skills/feature/SKILL.md', r'skillsync test-run'), ('skills/bugfix/SKILL.md', r'skillsync test-run'), ('skills/smart-test/SKILL.md', r'skillsync test-run'),
            ('skills/workflow/SKILL.md', r'skillsync test-run'), ('agents/test-runner.md', r'skillsync test-run --run <id> --step <id>'),
            ('agents/test-writer.md', r'skillsync test-run --run <id> --step <id>'), ('agents/implementer.md', r'skillsync test-run --run <id> --step <id>'),
            ('skills/skillsync/SKILL.md', r'skillsync test-run <path>'), ('skills/dashboard/SKILL.md', r'Zakładka „Testy”'))


class TestRunRule(unittest.TestCase):
    def test_every_carrier_names_the_command(self):
        for name, pattern in CARRIERS:
            with self.subTest(name):
                self.assertRegex((LIB / name).read_text(encoding='utf-8'), re.escape(pattern))

    def test_the_skills_link_to_the_reference_and_it_exists(self):
        self.assertTrue(REFERENCE.is_file())
        text = REFERENCE.read_text(encoding='utf-8')
        for needle in ('--run', '--step', '`[T]`', '--no-tests', 'tests-waived', 'Nie wklejaj komunikatów porażek', 'chroni przed pominięciem'):
            self.assertIn(needle, text)
        for name in ('skills/feature/SKILL.md', 'skills/bugfix/SKILL.md', 'skills/smart-test/SKILL.md'):
            self.assertIn('references/test-run.md', (LIB / name).read_text(encoding='utf-8'), name)
        self.assertIn('references/test-run.md', (LIB / 'skills/workflow/SKILL.md').read_text(encoding='utf-8'))

    def test_the_security_text_says_that_results_are_not_masked(self):
        text = (ROOT / 'docs/security.md').read_text(encoding='utf-8')
        self.assertIn('niemaskowane', text)
        self.assertIn('także gdy port odczytu jest widoczny w sieci lokalnej', text)
        self.assertIn('FAŁSZERSTWEM', text)
        changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        self.assertRegex(changelog, r'(?s)### Security\n\n- \*\*Test results are NOT masked by default\*\*')


if __name__ == '__main__':
    unittest.main()
