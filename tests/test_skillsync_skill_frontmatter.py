"""CLAUDE-1: lint of the skill frontmatter (`skill_frontmatter.check`) and its use by `doctor` (a warning, never an error)."""
import re
import shutil
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import skill_frontmatter  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

SKILLS = ROOT / 'library' / 'skills'   # relative to the repository: no environment variable (a skipped test proves nothing)


def klasses(metadata):
    return [klass for klass, reason in skill_frontmatter.check(metadata)]


class Check(unittest.TestCase):
    def test_a_known_frontmatter_has_no_remark(self):
        self.assertEqual(skill_frontmatter.check({'name': 'x', 'description': 'd'}), [])
        full = {'name': 'x', 'description': 'd', 'argument-hint': '<a>', 'disable-model-invocation': True, 'user-invocable': False, 'allowed-tools': 'Read Grep',
                'model': 'inherit', 'effort': 'high', 'context': 'fork', 'agent': 'Explore', 'background': False, 'paths': ['**/*.cs'], 'shell': 'bash',
                'metadata': {'k': 'v'}, 'license': 'MIT', 'compatibility': 'c', 'when_to_use': 'w', 'arguments': 'a b', 'disallowed-tools': ['Bash'], 'hooks': {}}
        self.assertEqual(skill_frontmatter.check(full), [])

    def test_an_unknown_key_is_remarked(self):
        self.assertEqual(klasses({'name': 'x', 'description': 'd', 'argument-hnit': '<a>'}), ['unknown-key'])

    def test_a_wrong_type_is_remarked(self):
        for key, value in (('disable-model-invocation', 'maybe'), ('disable-model-invocation', 2), ('paths', 5), ('user-invocable', 'sure'), ('argument-hint', ['a']),
                           ('allowed-tools', {'a': 1}), ('metadata', 'text'), ('background', 'x'), ('model', 3)):
            with self.subTest(key=key, value=value):
                self.assertEqual(klasses({'name': 'x', 'description': 'd', key: value}), ['bad-type'])

    def test_a_wrong_value_is_remarked(self):
        for key, value in (('context', 'join'), ('effort', 'huge'), ('shell', 'zsh'), ('compatibility', 'c' * 501)):
            with self.subTest(key=key):
                self.assertEqual(klasses({'name': 'x', 'description': 'd', key: value}), ['bad-value'])

    def test_the_boolean_spellings_of_yaml_are_accepted(self):
        for text in ('true', 'false', 'yes', 'no', 'on', 'off', 'True', '1', '0'):
            with self.subTest(text=text):
                data = yaml.safe_load(f'disable-model-invocation: {text}')
                self.assertEqual(skill_frontmatter.check(data), [])

    def test_the_function_is_total(self):
        for value in (None, [], 'text', 5, {1: 'a'}, {True: 'a'}, {None: 'a'}, {'name': None}, {'paths': None}, {('a', 'b'): 1}):
            with self.subTest(value=repr(value)):
                result = skill_frontmatter.check(value)
                self.assertIsInstance(result, list)
                for klass, reason in result:
                    self.assertIsInstance(klass, str)
                    self.assertIsInstance(reason, str)

    def test_a_key_that_is_not_text_is_an_unknown_key(self):
        self.assertEqual(klasses({'name': 'x', 'description': 'd', 1: 'a'}), ['unknown-key'])
        self.assertEqual(klasses(yaml.safe_load('name: x\ndescription: d\nyes: 1')), ['unknown-key'])

    def test_both_spellings_of_user_invocable_are_known_and_silent(self):
        self.assertEqual(skill_frontmatter.check({'name': 'x', 'description': 'd', 'user-invokable': True}), [])
        self.assertEqual(skill_frontmatter.check({'name': 'x', 'description': 'd', 'user-invocable': True}), [])
        self.assertEqual(skill_frontmatter.check({'name': 'x', 'description': 'd', 'user-invokable': True, 'user-invocable': True}), [])

    def test_a_huge_integer_key_does_not_break_the_lint(self):
        self.assertEqual(klasses({'name': 'x', 'description': 'd', 10 ** 5000: 'a'}), ['unknown-key'])

    def test_a_key_of_an_unknown_host_is_silent(self):
        self.assertEqual(skill_frontmatter.check({'name': 'x', 'description': 'd', 'applyTo': '**/*.cs'}), [])

    def test_the_reason_names_the_key_and_stays_short(self):
        reason = skill_frontmatter.check({'name': 'x', 'description': 'd', 'paths': 'p' * 5000, 'bogus': 'v' * 5000, 'effort': 'e' * 5000})
        for klass, text in reason:
            self.assertLess(len(text), 200, text)
        self.assertTrue(any('bogus' in text for klass, text in reason))


class Library(unittest.TestCase):
    def test_every_skill_of_the_library_has_a_clean_frontmatter(self):
        files = sorted(SKILLS.glob('*/SKILL.md'))
        self.assertGreater(len(files), 20, 'the skills of the library were not found')
        for path in files:
            with self.subTest(skill=path.parent.name):
                match = re.match(r'---\n(.*?)\n---\n', path.read_text(encoding='utf-8'), re.S)
                self.assertIsNotNone(match)
                self.assertEqual(skill_frontmatter.check(yaml.safe_load(match.group(1))), [])


class DoctorWarns(WorkflowFixture):
    def install(self, name, extra):
        folder = self.library / 'skills' / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: opis {name}\n{extra}\n---\nBody\n', encoding='utf-8')
        for host in ('.github/skills', '.claude/skills'):
            shutil.copytree(folder, self.root / host / name)
        self.manifest['skills'] = [name]
        self.save()

    def test_an_unknown_key_is_a_warning_and_never_fails_doctor(self):
        self.install('odd', 'argument-hnit: x')
        report = self.report('doctor')
        found = [f for f in report['findings'] if 'argument-hnit' in f['reason']]
        self.assertEqual([f['level'] for f in found], ['warning'])
        self.assertNotEqual(report['status'], 'FAIL')
        self.assertFalse(any(f['level'] == 'error' for f in report['findings']), report['findings'])

    def test_a_wrong_type_is_a_warning(self):
        self.install('typed', 'disable-model-invocation: maybe')
        report = self.report('doctor')
        found = [f for f in report['findings'] if 'disable-model-invocation' in f['reason']]
        self.assertEqual([f['level'] for f in found], ['warning'])
        self.assertNotEqual(report['status'], 'FAIL')

    def test_a_clean_skill_and_the_other_spelling_have_no_remark(self):
        self.install('clean', 'user-invokable: true')
        report = self.report('doctor')
        self.assertFalse([f for f in report['findings'] if 'user-invokable' in f['reason'] or 'frontmatter key' in f['reason'].lower()], report['findings'])

    def test_a_skill_without_a_name_still_gets_the_lint_after_its_error(self):
        folder = self.library / 'skills' / 'nameless'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'SKILL.md').write_text('---\ndescription: d\nbogus-key: 1\n---\nBody\n', encoding='utf-8')
        for host in ('.github/skills', '.claude/skills'):
            shutil.copytree(folder, self.root / host / 'nameless')
        self.manifest['skills'] = ['nameless']
        self.save()
        report = self.report('doctor', code=1)   # the missing name is an error of `doctor` (exit 1); the lint still runs after it
        reasons = [f['reason'] for f in report['findings']]
        self.assertTrue(any('Missing name/description' in r for r in reasons), reasons)
        self.assertTrue(any('bogus-key' in r for r in reasons), reasons)


if __name__ == '__main__':
    unittest.main()
