"""Doctor warnings about instructions that drift from the project and library."""
import unittest

from tests.test_skillsync_workflow import WorkflowFixture
from tests.layers_helpers import needs_os_symlinks  # noqa: E402


class DoctorInstructionDriftTests(WorkflowFixture):
    def skill(self, name):
        self.library.joinpath('skills', name).mkdir(parents=True, exist_ok=True)
        self.library.joinpath('skills', name, 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: test\n---\n', encoding='utf-8')

    def messages(self):
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        return [f['reason'] for f in report['findings'] if f['path'] == 'instructions']

    def test_missing_vs_existing_path(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', 'See [a](docs/real.md), `docs/gone.md` and [b](docs/nope.md#anchor), `docs/`.\n')
        self.assertEqual(sorted(self.messages()), [
            'Missing path referenced in instructions: docs/gone.md (CLAUDE.md)',
            'Missing path referenced in instructions: docs/nope.md (CLAUDE.md)'])

    def test_skips_urls_placeholders_code_blocks_and_generated(self):
        self.write('AGENTS.md', '\n'.join([
            '[u](https://x.io/a.md) [h](#top) `<dir>/a.md` `{x}.md` `*.md` `$HOME/a.md` `~/a.md` `/abs/a.md`',
            '`.skillsync/runs/a.md` `.claude/agents/a.md` `.agents/skills/` `.codex/a.toml` `a b.md` `e.g.`',
            '```', '`docs/in-block.md` [x](docs/block.md)', '```', '']))
        self.assertEqual(self.messages(), [])

    @needs_os_symlinks
    def test_skill_not_in_library_and_not_installed(self):
        self.skill('present')
        self.skill('other')
        self.manifest['skills'] = ['present']
        self.save()
        for host in ('.github/skills', '.claude/skills'):
            link = self.root / host / 'present'
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(self.library / 'skills' / 'present')
        self.write('CLAUDE.md', 'Use skill `ghost`, skilla `other` and [p](../x/skills/present/SKILL.md), '
                                'skillu `present`, skille `ghost2`.\n')
        self.assertEqual(sorted(self.messages()), [
            'Skill named in instructions is not in the library: ghost',
            'Skill named in instructions is not in the library: ghost2',
            'Skill named in instructions is not installed in the project: other'])

    def test_limit_of_thirty(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', ' '.join(f'`docs/missing{i}.md`' for i in range(35)) + '\n')
        messages = self.messages()
        self.assertEqual(len(messages), 31)
        self.assertEqual(messages[-1], '… and 5 more')

    def test_precision_for_structure_descriptions(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', '`Controllers/` `appsettings.json` `ContainerModules/X.cs` `docs/` `docs/brak.md` '
                                '[l](docs/brak2.md) [t](tel:+48123) [d](data:text/plain,a.md) [w](https://a.io/skills/z/SKILL.md)\n')
        self.assertEqual(sorted(self.messages()), [
            'Missing path referenced in instructions: docs/brak.md (CLAUDE.md)',
            'Missing path referenced in instructions: docs/brak2.md (CLAUDE.md)'])

    def test_library_fallback_and_parent_traversal(self):
        self.library.joinpath('skills/workflow/references').mkdir(parents=True)
        self.library.joinpath('skills/workflow/references/knowledge.md').write_text('x')
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', '[k](workflow/references/knowledge.md) [k2](knowledge.md) `docs/../x.md` [p](../x.md)\n')
        self.assertEqual(self.messages(), [])

    def test_fences_tilde_and_indented(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', '- item\n  ~~~\n  [a](docs/t.md)\n  ~~~\n- x\n    ```md\n    [b](docs/i.md)\n    ```\n[c](docs/after.md)\n')
        self.assertEqual(self.messages(), ['Missing path referenced in instructions: docs/after.md (CLAUDE.md)'])

    def test_copilot_source_and_dedup(self):
        self.write('.github/copilot-instructions.md', '[a](docs/x.md) [b](docs/y.md)\n')
        self.write('CLAUDE.md', '[a](docs/x.md)\n')
        self.assertEqual(sorted(self.messages()), [
            'Missing path referenced in instructions: docs/x.md (CLAUDE.md, .github/copilot-instructions.md)',
            'Missing path referenced in instructions: docs/y.md (.github/copilot-instructions.md)'])

    def test_skill_phrase_case_and_inflection(self):
        self.write('CLAUDE.md', 'Skill `g1` oraz skilli `g2`.\n')
        self.assertEqual(sorted(self.messages()), [
            'Skill named in instructions is not in the library: g1',
            'Skill named in instructions is not in the library: g2'])

    def ignore(self, *entries):
        self.manifest['workflow']['doctor'] = {'ignore_instruction_drift': list(entries)}
        self.save()

    def test_ignore_path_glob_and_unprefixed(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', '[a](docs/a1.md) [b](docs/b1.md) [c](docs/c.md)\n')
        self.ignore('path:docs/a*.md', 'docs/b*.md')
        self.assertEqual(self.messages(), ['Missing path referenced in instructions: docs/c.md (CLAUDE.md)'])

    def test_ignore_skill_glob_keeps_others(self):
        self.write('CLAUDE.md', 'skill `ghost1`, skill `other`, [x](docs/m.md)\n')
        self.ignore('skill:ghost*')
        self.assertEqual(sorted(self.messages()), [
            'Missing path referenced in instructions: docs/m.md (CLAUDE.md)',
            'Skill named in instructions is not in the library: other'])

    def test_ignored_do_not_use_limit(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', ' '.join(f'[a](docs/m{i}.md)' for i in range(40)) + ' [k](docs/keep.md)\n')
        self.ignore('docs/m*.md')
        self.assertEqual(self.messages(), ['Missing path referenced in instructions: docs/keep.md (CLAUDE.md)'])

    def test_invalid_ignore_config_is_error_and_unfiltered(self):
        self.write('docs/real.md', 'x')
        self.write('CLAUDE.md', '[a](docs/a.md)\n')
        for value in ('docs/*', [1], [''], ['x' * 201], ['a'] * 51):
            self.manifest['workflow']['doctor'] = {'ignore_instruction_drift': value}
            self.save()
            report = self.report('doctor', code=1)
            self.assertEqual(report['status'], 'FAIL')
            reasons = [f['reason'] for f in report['findings']]
            self.assertTrue(any('ignore_instruction_drift' in r for r in reasons), value)
            self.assertIn('Missing path referenced in instructions: docs/a.md (CLAUDE.md)', reasons)

    def test_doctor_section_must_be_mapping(self):
        self.manifest['workflow']['doctor'] = ['x']
        self.save()
        result = self.rejected('doctor')
        self.assertIn('workflow.doctor must be a mapping', result.stdout + result.stderr)

    def test_no_instruction_files(self):
        self.assertEqual(self.messages(), [])


if __name__ == '__main__':
    unittest.main()
