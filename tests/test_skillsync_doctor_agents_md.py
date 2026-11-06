"""Doctor in the `agents` entrypoint mode, inferred from artifacts (CLAUDE.md imports @AGENTS.md)."""
import json
import os
import unittest

from tests.test_skillsync_workflow import WorkflowFixture
from tests.layers_helpers import needs_os_symlinks  # noqa: E402

HEADER = '# CLAUDE.md\n\nThis file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.\n\n'
BLOCK = '<!-- SKILLSYNC:CODEX-BEGIN -->\n{}\n<!-- SKILLSYNC:CODEX-END -->\n'
NOTE = '<!-- SKILLSYNC:CODEX-NOTE-BEGIN -->\nCodex only\n<!-- SKILLSYNC:CODEX-NOTE-END -->'


class DoctorAgentsMode(WorkflowFixture):
    def findings(self):
        result = self.cli('doctor')
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)['findings']

    def reasons(self, path=None):
        return [f['reason'] for f in self.findings() if path is None or f['path'] == path]

    def test_import_without_block_is_an_error(self):
        self.write('CLAUDE.md', HEADER + '@AGENTS.md\n')
        self.assertTrue(any('no skillsync instructions block' in r for r in self.reasons('AGENTS.md')))
        self.write('AGENTS.md', BLOCK.format(NOTE))
        self.assertTrue(any('no skillsync instructions block' in r for r in self.reasons('AGENTS.md')))

    def test_import_with_block_is_clean(self):
        self.write('CLAUDE.md', HEADER + '@AGENTS.md\n')
        self.write('AGENTS.md', BLOCK.format('Rules\n\n' + NOTE))
        self.assertEqual([r for r in self.reasons('AGENTS.md')], [])

    def test_handwritten_import_of_own_agents_md_is_not_an_error(self):
        self.write('CLAUDE.md', 'My rules\n' '@AGENTS.md\n')
        self.write('AGENTS.md', '# Team agents\n' + 'x' * 200)
        self.manifest['workflow'] = {'context': {'max_bytes': 50}}
        self.save()
        self.assertEqual([f['reason'] for f in self.findings() if f['path'].endswith('AGENTS.md')], [])

    def test_inline_claude_md_needs_no_block(self):
        self.write('CLAUDE.md', HEADER + 'Rules\n')
        self.assertEqual(self.reasons('AGENTS.md'), [])

    @needs_os_symlinks
    def test_symlinked_agents_md_is_a_warning(self):
        target = self.base / 'shared.md'
        target.write_text('shared\n')
        (self.root / 'AGENTS.md').symlink_to(target)
        levels = [f['level'] for f in self.findings() if f['path'] == 'AGENTS.md']
        self.assertEqual(levels, ['warning'])

    def test_codex_copy_required_only_without_import(self):
        for name in ['feature']:
            self.library.joinpath('skills', name, 'references').mkdir(parents=True, exist_ok=True)
            self.library.joinpath('skills', name, 'SKILL.md').write_text(f'---\nname: {name}\ndescription: t\n---\n')
        self.manifest['skills'] = ['feature']
        self.save()
        self.write('AGENTS.md', BLOCK.format('Rules'))
        missing = lambda: [f['path'] for f in self.findings() if f['reason'] == 'Missing enabled Codex entrypoint/instructions']
        self.write('CLAUDE.md', HEADER + 'Rules\n')
        self.assertEqual(missing(), ['.codex/skillsync-instructions.md'])
        self.write('CLAUDE.md', HEADER + '@AGENTS.md\n')
        self.assertEqual(missing(), [])

    def test_claude_off_agents_block_needs_no_codex_copy(self):
        self.library.joinpath('skills', 'feature', 'references').mkdir(parents=True, exist_ok=True)
        self.library.joinpath('skills', 'feature', 'SKILL.md').write_text('---\nname: feature\ndescription: t\n---\n')
        self.manifest.update({'skills': ['feature'], 'claude': False})
        self.save()
        self.write('AGENTS.md', BLOCK.format('Rules\n\n' + NOTE))
        missing = [f['path'] for f in self.findings() if f['reason'] == 'Missing enabled Codex entrypoint/instructions']
        self.assertEqual(missing, [])

    def test_budget_counts_instruction_body_not_codex_note(self):
        self.manifest['workflow'] = {'context': {'max_bytes': 300}}
        self.save()
        self.write('CLAUDE.md', HEADER + '@AGENTS.md\n')
        self.write('AGENTS.md', BLOCK.format('y' * 250 + '\n\n' + NOTE + 'z' * 200))
        self.assertNotIn('Instruction budget exceeded', [f['reason'] for f in self.findings() if f['path'].endswith('AGENTS.md')])

    def test_budget_covers_agents_md_only_with_import(self):
        self.manifest['workflow'] = {'context': {'max_bytes': 50}}
        self.save()
        self.write('AGENTS.md', BLOCK.format('x' * 200))
        self.write('CLAUDE.md', HEADER + 'Rules\n')
        self.assertNotIn('Instruction budget exceeded', [f['reason'] for f in self.findings() if f['path'].endswith('AGENTS.md')])
        self.write('CLAUDE.md', HEADER + '@AGENTS.md\n')
        self.assertIn('Instruction budget exceeded', [f['reason'] for f in self.findings() if f['path'].endswith('AGENTS.md')])


if __name__ == '__main__':
    unittest.main()
