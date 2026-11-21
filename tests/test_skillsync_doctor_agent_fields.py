"""`doctor` makes the version requirements of the role fields visible (CLAUDE-2): the registry `ROLE_FIELDS` of `skillsync_workflow/skill_frontmatter.py`,
the pure function `agent_field_findings` and the single `info` finding of the project. No `claude` process is ever started."""
import json
import os
import stat
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import skill_frontmatter  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

PREFIX = 'Role fields that need a newer Claude Code'
VERSION_ENTRY_KEYS = {'introduced', 'behavior', 'verified', 'notify', 'older'}


class AgentFieldFindings(unittest.TestCase):
    def test_one_aggregated_text_for_the_whole_project(self):
        texts = skill_frontmatter.agent_field_findings([
            ('explore', {'name': 'explore', 'maxTurns': 40, 'omitClaudeMd': True}),
            ('reviewer', {'name': 'reviewer', 'maxTurns': 85})])
        self.assertEqual(len(texts), 1)
        text = texts[0]
        self.assertTrue(text.startswith(PREFIX))
        self.assertIn('`maxTurns` (roles: explore, reviewer; Claude Code >= 2.1.246', text)
        self.assertIn('`omitClaudeMd` (roles: explore; Claude Code >= 2.1.271', text)
        self.assertIn('installed version is not checked', text)

    def test_nothing_to_report_without_fields_of_the_table(self):
        for frontmatters in ([], None, [('a', {'name': 'a', 'description': 'd', 'tools': 'Read', 'model': 'haiku'})],
                             [('a', {'effort': 'low'})]):   # `effort` is in the table but its version for custom agents is unverified: silent
            with self.subTest(frontmatters=frontmatters):
                self.assertEqual(skill_frontmatter.agent_field_findings(frontmatters), [])

    def test_anything_that_is_not_a_mapping_is_skipped_and_never_raises(self):
        for broken in (None, 'junk', 3, ['maxTurns'], {1: 2}, {None: 'x'}):
            with self.subTest(broken=broken):
                self.assertEqual(skill_frontmatter.agent_field_findings([('custom', broken)]), [])
        self.assertEqual(len(skill_frontmatter.agent_field_findings([('custom', None), ('ok', {'maxTurns': 1})])), 1)

    def test_it_starts_no_process(self):
        def refuse(*args, **kwargs):
            raise AssertionError('a process was started')
        with mock.patch.object(subprocess, 'run', refuse), mock.patch.object(subprocess, 'Popen', refuse), \
                mock.patch.object(os, 'system', refuse):
            self.assertEqual(len(skill_frontmatter.agent_field_findings([('r', {'maxTurns': 5})])), 1)
            self.assertIsNone(skill_frontmatter.read_role_frontmatter(ROOT / 'no-such-role.md'))

    def test_reading_a_role_file_is_total(self):
        with tempfile.TemporaryDirectory(prefix='skillsync-role-') as folder:
            base = Path(folder)
            cases = {'ok.md': ('---\nname: ok\nmaxTurns: 3\n---\nbody\n', {'name': 'ok', 'maxTurns': 3}),
                     'none.md': ('# custom role\n', None), 'broken.md': ('---\nname: [unclosed\n---\n', None),
                     'empty.md': ('', None), 'open.md': ('---\n', None)}
            for name, (text, expected) in cases.items():
                (base / name).write_text(text, encoding='utf-8')
                with self.subTest(name=name):
                    self.assertEqual(skill_frontmatter.read_role_frontmatter(base / name), expected)
            (base / 'binary.md').write_bytes(b'---\n\xff\xfe\n---\n')
            self.assertIsNone(skill_frontmatter.read_role_frontmatter(base / 'binary.md'))


class RoleFieldTable(unittest.TestCase):
    def library_fields(self):
        keys = set()
        for path in sorted((ROOT / 'library' / 'agents').glob('*.md')):
            metadata = skill_frontmatter.read_role_frontmatter(path)
            self.assertIsInstance(metadata, dict, path.name)
            keys.update(metadata)
        return keys

    def test_every_field_of_the_library_roles_beyond_the_basic_ones_has_an_entry(self):
        for key in sorted(self.library_fields() - set(skill_frontmatter.ROLE_BASIC_FIELDS)):
            with self.subTest(field=key):
                self.assertIn(key, skill_frontmatter.ROLE_FIELDS, f'role field {key} has no version entry in ROLE_FIELDS')

    def test_the_real_library_roles_give_one_text_naming_the_limit_and_the_omit_field(self):
        frontmatters = [(path.stem, skill_frontmatter.read_role_frontmatter(path)) for path in sorted((ROOT / 'library' / 'agents').glob('*.md'))]
        texts = skill_frontmatter.agent_field_findings(frontmatters)
        self.assertEqual(len(texts), 1)
        roles = sorted(name for name, _ in frontmatters)
        self.assertIn('`maxTurns` (roles: ' + ', '.join(roles) + ';', texts[0])
        self.assertIn('`omitClaudeMd` (roles: explore, mechanic, test-runner, verifier;', texts[0])
        self.assertNotIn('disallowedTools', texts[0], 'no role of the library carries it (the probe F0 is negative)')

    def test_entries_are_complete_and_versions_look_like_versions(self):
        for key, entry in skill_frontmatter.ROLE_FIELDS.items():
            with self.subTest(field=key):
                self.assertEqual(set(entry), VERSION_ENTRY_KEYS)
                self.assertTrue(entry['introduced'] or entry['behavior'], 'a field without any version says nothing')
                for version in (entry['introduced'], entry['behavior']):
                    self.assertTrue(version is None or re.fullmatch(r'\d+\.\d+\.\d+', version), version)
                self.assertIsInstance(entry['verified'], bool)
                self.assertIsInstance(entry['notify'], bool)
                self.assertTrue(entry['older'])

    def test_the_basic_fields_are_not_in_the_table_and_the_skill_registry_is_untouched(self):
        self.assertFalse(set(skill_frontmatter.ROLE_BASIC_FIELDS) & set(skill_frontmatter.ROLE_FIELDS))
        self.assertNotIn('maxTurns', skill_frontmatter.KNOWN)   # the roles are camelCase, the skills kebab-case: two vocabularies, one module
        self.assertEqual(skill_frontmatter.check({'maxTurns': 5}), [(skill_frontmatter.UNKNOWN_KEY,
                         "Unknown frontmatter key 'maxTurns' (Claude Code ignores it without a word)")])


class DoctorRoleFields(WorkflowFixture):
    def setUp(self):
        super().setUp()
        for name, text in (('explore', '---\nname: explore\ndescription: d\ntools: Read\nmodel: haiku\nomitClaudeMd: true\nmaxTurns: 40\n---\nBody\n'),
                           ('reviewer', '---\nname: reviewer\ndescription: d\ntools: Read\nmodel: sonnet\nmaxTurns: 85\n---\nBody\n'),
                           ('custom', '# custom role\n'),
                           ('broken', '---\nname: [unclosed\n---\n')):
            for folder in (self.library / 'agents', self.root / '.claude' / 'agents'):
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f'{name}.md').write_text(text, encoding='utf-8')
        (self.library / 'skills' / 'feature').mkdir(parents=True)
        (self.library / 'skills' / 'feature' / 'SKILL.md').write_text('---\nname: feature\ndescription: test\n---\n', encoding='utf-8')
        self.write('CLAUDE.md', 'Instructions\n')
        self.manifest.update({'skills': ['feature'], 'agents': ['explore', 'reviewer', 'custom', 'broken'], 'codex': False})
        self.save()

    def findings(self, report):
        return [f for f in report['findings'] if f['reason'].startswith(PREFIX)]

    def test_one_info_finding_for_the_project_and_it_does_not_change_the_status(self):
        report = self.doctor_report()
        found = self.findings(report)
        self.assertEqual(len(found), 1, report['findings'])
        self.assertEqual((found[0]['level'], found[0]['path']), ('info', '.claude/agents'))
        self.assertIn('`maxTurns` (roles: explore, reviewer;', found[0]['reason'])
        self.assertIn('`omitClaudeMd` (roles: explore;', found[0]['reason'])
        self.assertNotIn('custom', found[0]['reason'])
        self.assertNotIn('broken', found[0]['reason'])
        errors = [f for f in report['findings'] if f['level'] == 'error']
        self.assertEqual(report['status'], 'FAIL' if errors else 'PASS')

    def test_the_status_and_the_other_findings_are_the_same_without_the_fields(self):
        with_fields = self.doctor_report()
        for name in ('explore', 'reviewer'):
            (self.library / 'agents' / f'{name}.md').write_text(f'---\nname: {name}\ndescription: d\ntools: Read\nmodel: haiku\n---\nBody\n', encoding='utf-8')
        without = self.doctor_report()
        self.assertEqual(self.findings(without), [])
        rest = [f for f in with_fields['findings'] if not f['reason'].startswith(PREFIX)]
        self.assertEqual(rest, without['findings'])
        self.assertEqual(with_fields['status'], without['status'])

    @unittest.skipIf(os.name == 'nt', 'the probe is a shell script on PATH')
    def test_doctor_never_starts_a_claude_process(self):
        probe = self.base / 'bin' / 'claude'
        marker = self.base / 'claude-was-started'
        probe.parent.mkdir()
        probe.write_text(f'#!/bin/sh\necho started > "{marker}"\necho "9.9.9 (Claude Code)"\n', encoding='utf-8')
        probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
        with mock.patch.dict(os.environ, {'PATH': str(probe.parent) + os.pathsep + os.environ.get('PATH', '')}):
            report = self.doctor_report()
        self.assertEqual(len(self.findings(report)), 1)
        self.assertFalse(marker.exists(), 'doctor started `claude`')

    def doctor_report(self):
        result = self.cli('doctor')
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        return json.loads(result.stdout)


if __name__ == '__main__':
    unittest.main()
