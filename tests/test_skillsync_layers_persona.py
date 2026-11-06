"""K5 (step 2.2at): the persona mechanism of a layer, i.e. `instructions/_base.d/` fragments and the overlay
`skills/dispatch/references/imiona.md`. With and without a synthetic personal layer, in both link modes.

The core here is synthetic (no real library); the persona content is a neutral placeholder."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import SKILL_TEXT, ComposeCase, each_mode, link_mode  # noqa: E402

BASE_END = '<!-- SKILLSYNC:BASE-END -->'
CORE_BASE = '# Core base\ncore rules\n' + BASE_END + '\ntail after the marker\n'
DISPATCH_SKILL = SKILL_TEXT.format('dispatch') + 'Cards get the aliases karta-1, karta-2 ... karta-N unless a list of names exists.\n'
CORE = {
    'instructions/_base.md': CORE_BASE,
    'instructions/proj.md': '# Project template\n',
    'codex.yaml': 'max_concurrent_threads_per_session: 2\n',
    'skills/feature/SKILL.md': SKILL_TEXT.format('feature'),
    'skills/dispatch/SKILL.md': DISPATCH_SKILL,
    'skills/dispatch/references/loop.md': 'loop\n',
}
FRAGMENT = '<!-- SKILLSYNC:FRAGMENT {kind} {layer}/{name} -->'
INSTRUCTION_FILES = ('.github/copilot-instructions.md', 'CLAUDE.md', '.codex/skillsync-instructions.md')


def marker(kind, layer, name):
    return FRAGMENT.format(kind=kind, layer=layer, name=name)


def order_of(text, layer_names):
    """Positions of the begin markers in `text`, in the order of `layer_names` ((layer, file) pairs)."""
    return [text.index(marker('begin', layer, name)) for layer, name in layer_names]


class PersonaCase(ComposeCase):
    def setUp(self):
        super().setUp()
        self.skills = ('feature', 'dispatch')

    def project(self, name, **extra):
        return self.new_project(name, skills=self.skills, **extra)

    def set_rules(self, *pairs):
        entries = [(i, i, self.layer_paths[i]) for i in ('core', 'org', 'personal') if i in self.layer_paths]
        self.write_config(layers=entries, rules=[([glob], use) for glob, use in pairs])

    def read(self, project, rel):
        return (Path(project) / rel).read_text(encoding='utf-8')


class FragmentTests(PersonaCase):
    ORG = {'instructions/_base.d/03-org-early.md': 'Org early fragment\n',
           'instructions/_base.d/50-org-late.md': 'Org late fragment\n'}
    PERSONAL = {'instructions/_base.d/05-persona.md': 'Persona early habit\n',
                'instructions/_base.d/09-persona-last-early.md': 'Persona last early\n',
                'instructions/_base.d/10-persona-first-late.md': 'Persona first late\n',
                'instructions/_base.d/20-persona-late.md': 'Persona late habit',  # no trailing newline
                'instructions/_base.d/notes.txt': 'not a fragment: wrong extension\n'}
    EARLY = [('org', '03-org-early.md'), ('personal', '05-persona.md'), ('personal', '09-persona-last-early.md')]
    LATE = [('org', '50-org-late.md'), ('personal', '10-persona-first-late.md'), ('personal', '20-persona-late.md')]

    def setUp(self):
        super().setUp()
        self.add_layers(core=CORE, org=self.ORG, personal=self.PERSONAL)

    @each_mode
    def test_order_markers_and_position_before_base_end_in_every_instruction_file(self, mode):
        project = self.project(f'order-{mode}', claude=True)
        self.link_ok(project, mode=mode)
        for rel in INSTRUCTION_FILES:
            text = self.read(project, rel)
            with self.subTest(file=rel):
                early, late = order_of(text, self.EARLY), order_of(text, self.LATE)
                core = text.index('core rules')
                end = text.index(BASE_END)
                self.assertEqual(early, sorted(early), 'layer order, then file name')
                self.assertEqual(late, sorted(late), 'layers first (org before personal), then file name')
                self.assertLess(max(early), core, '00-..09- fragments come before the core text')
                self.assertGreater(min(late), core, 'the other fragments come after the core text')
                self.assertLess(max(late), end, 'every fragment is before BASE-END')
                self.assertLess(end, text.index('tail after the marker'))
                self.assertLess(text.index('tail after the marker'), text.index('# Project template'))
                self.assertNotIn('not a fragment', text)
                self.assertEqual(text.count('SKILLSYNC:FRAGMENT begin'), 6)
                self.assertEqual(text.count('SKILLSYNC:FRAGMENT end'), 6)

    @each_mode
    def test_each_fragment_sits_between_its_own_begin_and_end_markers(self, mode):
        project = self.project(f'blocks-{mode}')
        self.link_ok(project, mode=mode)
        text = self.read(project, '.github/copilot-instructions.md')
        contents = {**{('org', n): t for n, t in
                       (('03-org-early.md', 'Org early fragment'), ('50-org-late.md', 'Org late fragment'))},
                    ('personal', '05-persona.md'): 'Persona early habit',
                    ('personal', '09-persona-last-early.md'): 'Persona last early',
                    ('personal', '10-persona-first-late.md'): 'Persona first late',
                    ('personal', '20-persona-late.md'): 'Persona late habit'}
        for (layer, name), body in contents.items():
            pattern = (re.escape(marker('begin', layer, name)) + r'\n' + re.escape(body) + r'\n'
                       + re.escape(marker('end', layer, name)) + r'\n')
            self.assertRegex(text, pattern, (layer, name))

    @each_mode
    def test_layer_order_of_the_rule_decides_not_the_file_name(self, mode):
        entries = [(i, i, self.layer_paths[i]) for i in ('core', 'org', 'personal')]
        self.write_config(layers=entries, rules=[(['**'], ['core', 'personal', 'org'])])
        project = self.project(f'reversed-{mode}')
        self.link_ok(project, mode=mode)
        text = self.read(project, '.github/copilot-instructions.md')
        early = order_of(text, [('personal', '05-persona.md'), ('personal', '09-persona-last-early.md'),
                                ('org', '03-org-early.md')])
        late = order_of(text, [('personal', '10-persona-first-late.md'), ('personal', '20-persona-late.md'),
                               ('org', '50-org-late.md')])
        self.assertEqual(early, sorted(early))
        self.assertEqual(late, sorted(late))

    @each_mode
    def test_relink_does_not_duplicate_fragments(self, mode):
        project = self.project(f'relink-{mode}', claude=True)
        self.link_ok(project, mode=mode)
        first = {rel: self.read(project, rel) for rel in INSTRUCTION_FILES}
        self.link_ok(project, mode=mode)
        self.assertEqual({rel: self.read(project, rel) for rel in INSTRUCTION_FILES}, first)

    @each_mode
    def test_instructions_command_composes_the_same_fragments(self, mode):
        project = self.project(f'command-{mode}')
        self.link_ok(project, mode=mode)
        expected = self.read(project, '.github/copilot-instructions.md')
        (project / '.github' / 'copilot-instructions.md').unlink()
        with link_mode(mode):
            process = self.cli('instructions', str(project))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.read(project, '.github/copilot-instructions.md'), expected)

    def test_dry_run_writes_no_instruction_file(self):
        project = self.project('dry', claude=True)
        process = self.link_ok(project, '--dry-run')
        self.assertIn('[dry-run]', process.stdout)
        for rel in INSTRUCTION_FILES:
            self.assertFalse((project / rel).exists(), rel)


class FragmentsWithoutPersonalTests(PersonaCase):
    @each_mode
    def test_without_any_fragment_the_text_is_the_core_base_and_the_template(self, mode):
        self.add_layers(core=CORE, org={})
        project = self.project(f'plain-{mode}', claude=True)
        self.link_ok(project, mode=mode)
        for rel in INSTRUCTION_FILES:
            text = self.read(project, rel)
            self.assertNotIn('SKILLSYNC:FRAGMENT', text, rel)
            self.assertIn(CORE_BASE, text, rel)
            self.assertTrue(text.rstrip().endswith('# Project template'), rel)

    @each_mode
    def test_any_layer_may_bring_fragments_not_only_personal(self, mode):
        self.add_layers(core=CORE, org={'instructions/_base.d/40-org.md': 'Org only fragment\n'})
        project = self.project(f'org-only-{mode}')
        self.link_ok(project, mode=mode)
        text = self.read(project, '.github/copilot-instructions.md')
        self.assertIn(marker('begin', 'org', '40-org.md'), text)
        self.assertLess(text.index('Org only fragment'), text.index(BASE_END))
        self.assertGreater(text.index('Org only fragment'), text.index('core rules'))

    @each_mode
    def test_a_personal_layer_in_the_rule_of_another_project_does_not_leak_into_this_one(self, mode):
        self.add_layers(core=CORE, org={}, personal={'instructions/_base.d/05-persona.md': 'Persona habit\n'})
        self.set_rules((f'**/with-{mode}', ['core', 'org', 'personal']), (f'**/without-{mode}', ['core', 'org']))
        with_persona, without = self.project(f'with-{mode}'), self.project(f'without-{mode}')
        self.link_ok(with_persona, mode=mode)
        self.link_ok(without, mode=mode)
        self.assertIn('Persona habit', self.read(with_persona, '.github/copilot-instructions.md'))
        self.assertNotIn('Persona habit', self.read(without, '.github/copilot-instructions.md'))
        self.assertNotIn('FRAGMENT', self.read(without, '.github/copilot-instructions.md'))

class DispatchOverlayTests(PersonaCase):
    NAMES = 'Alias list of the persona: Alfa, Beta, Gamma\n'

    def assert_core_skill_text_unchanged(self, project):
        self.assertEqual((self.skill_dir(project, 'dispatch') / 'SKILL.md').read_text(), DISPATCH_SKILL)

    @each_mode
    def test_personal_overlay_adds_the_name_list_without_touching_the_core_procedure(self, mode):
        self.add_layers(core=CORE, personal={'skills/dispatch/references/imiona.md': self.NAMES})
        project = self.project(f'persona-{mode}')
        self.link_ok(project, mode=mode)
        skill = self.skill_dir(project, 'dispatch')
        self.assertEqual((skill / 'references' / 'imiona.md').read_text(), self.NAMES)
        self.assertEqual((skill / 'references' / 'loop.md').read_text(), 'loop\n')
        self.assert_core_skill_text_unchanged(project)
        self.assertEqual(skill.is_symlink(), mode == 'symlink')

    @each_mode
    def test_without_personal_the_procedure_keeps_the_card_aliases(self, mode):
        self.add_layers(core=CORE, org={})
        project = self.project(f'nopersona-{mode}')
        self.link_ok(project, mode=mode)
        skill = self.skill_dir(project, 'dispatch')
        self.assertIn('karta-N', (skill / 'SKILL.md').read_text())
        self.assertFalse((skill / 'references' / 'imiona.md').exists())
        self.assertNotIn('Alfa', ''.join(p.read_text() for p in skill.rglob('*') if p.is_file()))

    @each_mode
    def test_personal_list_replaces_a_generic_core_list_per_file(self, mode):
        generic = 'Generic aliases: karta-1, karta-2, karta-3\n'
        self.add_layers(core={**CORE, 'skills/dispatch/references/imiona.md': generic},
                        personal={'skills/dispatch/references/imiona.md': self.NAMES})
        self.set_rules_for(mode)
        persona, plain = self.project(f'on-{mode}'), self.project(f'off-{mode}')
        self.link_ok(persona, mode=mode)
        self.link_ok(plain, mode=mode)
        self.assertEqual((self.skill_dir(persona, 'dispatch') / 'references/imiona.md').read_text(), self.NAMES)
        self.assertEqual((self.skill_dir(plain, 'dispatch') / 'references/imiona.md').read_text(), generic)
        self.assert_core_skill_text_unchanged(persona)
        self.assert_core_skill_text_unchanged(plain)

    def set_rules_for(self, mode):
        entries = [(i, i, self.layer_paths[i]) for i in ('core', 'personal')]
        self.write_config(layers=entries, rules=[([f'**/on-{mode}'], ['core', 'personal']),
                                                 ([f'**/off-{mode}'], ['core'])])

    @each_mode
    def test_overlay_and_fragments_work_together_in_one_personal_layer(self, mode):
        self.add_layers(core=CORE, personal={'skills/dispatch/references/imiona.md': self.NAMES,
                                             'instructions/_base.d/05-persona.md': 'Persona habit\n'})
        project = self.project(f'both-{mode}')
        self.link_ok(project, mode=mode)
        self.assertIn('Persona habit', self.read(project, '.github/copilot-instructions.md'))
        self.assertEqual((self.skill_dir(project, 'dispatch') / 'references/imiona.md').read_text(), self.NAMES)


if __name__ == '__main__':
    unittest.main()
