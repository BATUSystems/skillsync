"""Step 2.1t: merge semantics of layers (K2): one test per row of the spec table, the owner of
every file, and the trust model (`allow_override`).

Layers are synthetic. Aggregated files are merged by `LayerSet.merged_file`; per-file rows by
`resolve`/`skill_files`/`listing`; the trust model by `check_layers`. The `allow_override`
format is the one decided in the plan ("Wynik"): a core file path, `<section>.<list>:<id>` or
`codex.yaml:<dotted.key>`, all with globs.
"""
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import LINK_MODES, LayersCase, generator_version, git, link_mode, make_layer, write, yaml_text  # noqa: E402
from skillsync_workflow import layers  # noqa: E402


def _probe(kind):
    """True when the host file system supports symlinks (`kind='symlink'`) or distinguishes case."""
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        try:
            if kind == 'symlink':
                (base / 'target').mkdir()
                os.symlink(base / 'target', base / 'link', target_is_directory=True)
                return (base / 'link').is_dir()
            (base / 'a').write_text('1')
            (base / 'A').write_text('2')
            return len(list(base.iterdir())) == 2
        except (OSError, NotImplementedError):
            return False


CAN_SYMLINK = _probe('symlink')
CASE_SENSITIVE_FS = _probe('case')


def codes(findings):
    return {item['code'] for item in findings}


def by_code(findings, code):
    return [item for item in findings if item['code'] == code]


def owner_map(files):
    return {rel: owner.id for rel, (owner, _) in files.items()}


class MergeCase(LayersCase):
    sets = 0

    def build(self, *specs, allow_override=None):
        """specs: (id, kind, {rel: text}); one rule `**` using the layers in the given order."""
        MergeCase.sets += 1
        layer_specs = []
        for layer_id, kind, files in specs:
            options = {'version': generator_version()} if kind == 'core' else {}
            layer_specs.append((layer_id, kind, make_layer(self.work / f'set{MergeCase.sets}' / layer_id, layer_id, kind, files, **options)))
        extra = {} if allow_override is None else {'allow_override': allow_override}
        self.write_config([(spec[0], spec[1], spec[2]) for spec in layer_specs], [(['**'], [spec[0] for spec in specs])], **extra)
        return layers.resolve_layers(self.project(f'proj{MergeCase.sets}'))

    def findings(self, layerset, **kwargs):
        kwargs.setdefault('hooks', False)
        return layers.check_layers(layerset, **kwargs)

    def three(self, core=None, acme=None, me=None, **kwargs):
        return self.build(('core', 'core', core or {}), ('acme', 'org', acme or {}), ('me', 'personal', me or {}), **kwargs)


# ====================================================================== rows of the table (precedence, sums)


class PerFileRowsTest(MergeCase):
    def test_skills_are_resolved_per_file_with_the_later_layer_winning(self):
        layerset = self.three(
            core={'skills/feature/SKILL.md': 'core skill\n', 'skills/feature/ref.md': 'core ref\n',
                  'skills/feature/only-core.md': 'c\n', 'skills/solo/SKILL.md': 'solo\n'},
            acme={'skills/feature/ref.md': 'org ref\n', 'skills/feature/extra.md': 'org extra\n'},
            me={'skills/feature/SKILL.md': 'my skill\n', 'skills/mine/SKILL.md': 'mine\n'})
        files = layerset.skill_files('feature')
        self.assertEqual(owner_map(files), {'SKILL.md': 'me', 'extra.md': 'acme', 'only-core.md': 'core', 'ref.md': 'acme'})
        self.assertEqual({rel: path.read_text(encoding='utf-8') for rel, (_, path) in files.items()},
                         {'SKILL.md': 'my skill\n', 'extra.md': 'org extra\n', 'only-core.md': 'c\n', 'ref.md': 'org ref\n'})
        self.assertEqual([layer.id for layer in layerset.skill_providers('feature')], ['core', 'acme', 'me'])
        self.assertEqual([layer.id for layer in layerset.skill_providers('solo')], ['core'])     # a single provider
        self.assertEqual(layerset.skill_names(), ['feature', 'mine', 'solo'])
        owner, path = layerset.resolve('skills/feature/ref.md')
        self.assertEqual((owner.id, path.read_text(encoding='utf-8')), ('acme', 'org ref\n'))

    def test_a_new_file_of_a_skill_belongs_to_the_last_provider_of_skill_md(self):
        layerset = self.three(core={'skills/feature/SKILL.md': 'c\n'}, acme={'skills/feature/SKILL.md': 'o\n'},
                              me={'skills/feature/ref.md': 'r\n'})
        self.assertEqual(layerset.skill_new_file_owner('feature').id, 'acme')
        # a new skill (nobody provides SKILL.md) goes to the personal layer
        self.assertEqual(layerset.skill_new_file_owner('brand-new').id, 'me')

    def test_cache_and_vcs_directories_of_a_skill_are_not_files_of_the_skill(self):
        layerset = self.three(core={'skills/feature/SKILL.md': 'c\n', 'skills/feature/__pycache__/x.pyc': 'b',
                                    'skills/feature/.git/config': 'g', 'skills/feature/mod.pyc': 'b'})
        self.assertEqual(list(layerset.skill_files('feature')), ['SKILL.md'])

    def test_instruction_files_are_whole_files_and_the_later_layer_wins(self):
        layerset = self.three(
            core={'instructions/_base.md': 'base\n', 'instructions/java.md': 'core java\nline 2\n', 'instructions/only.md': 'o\n'},
            acme={'instructions/java.md': 'org java\n'})
        owner, path = layerset.resolve('instructions/java.md')
        self.assertEqual((owner.id, path.read_text(encoding='utf-8')), ('acme', 'org java\n'))      # no concatenation
        listing = layerset.instruction_files()
        self.assertEqual(sorted(listing), ['java.md', 'only.md'])                                   # `_base.md` is not here
        self.assertEqual(listing['java.md'][0].id, 'acme')

    def test_a_name_collision_between_layers_is_a_warning(self):
        layerset = self.three(core={'instructions/java.md': 'c\n', 'instructions/solo.md': 's\n'},
                              acme={'instructions/java.md': 'o\n'})
        found = by_code(self.findings(layerset, trust=True), 'instruction-collision')
        self.assertEqual([(item['severity'], item['layer']) for item in found], [('warning', 'acme')])
        self.assertIn('java.md', found[0]['message'])
        self.assertNotIn('solo.md', found[0]['message'])
        self.assertEqual(layers.findings_status(found), 'PASS')

    def test_agent_files_are_whole_files_and_the_later_layer_wins(self):
        layerset = self.three(core={'agents/reviewer.md': 'core\n', 'agents/other.md': 'x\n'}, me={'agents/reviewer.md': 'mine\n'})
        listing = layerset.agent_files()
        self.assertEqual({name: (owner.id, path.read_text(encoding='utf-8')) for name, (owner, path) in listing.items()},
                         {'other.md': ('core', 'x\n'), 'reviewer.md': ('me', 'mine\n')})

    def test_workflow_projects_and_trackers_are_whole_files(self):
        layerset = self.three(
            core={'workflow/projects/shared.yaml': yaml_text({'a': 1, 'b': 2}),
                  'workflow/trackers/jira.yaml': yaml_text({'kind': 'core', 'keep': True})},
            acme={'workflow/projects/shared.yaml': yaml_text({'a': 9}),
                  'workflow/trackers/jira.yaml': yaml_text({'kind': 'org'})})
        for rel, expected in (('workflow/projects/shared.yaml', {'a': 9}), ('workflow/trackers/jira.yaml', {'kind': 'org'})):
            with self.subTest(rel):
                owner, path = layerset.resolve(rel)
                self.assertEqual(owner.id, 'acme')
                import yaml
                self.assertEqual(yaml.safe_load(path.read_text(encoding='utf-8')), expected)    # no key of core's file survives

    def test_lesson_files_are_per_path(self):
        layerset = self.three(core={'workflow/lessons/a.md': 'core\n', 'workflow/lessons/b.md': 'b\n'},
                              acme={'workflow/lessons/a.md': 'org\n'})
        self.assertEqual(layerset.lesson_file('a.md')[0].id, 'acme')
        self.assertEqual(layerset.lesson_file('b.md')[0].id, 'core')
        self.assertIsNone(layerset.lesson_file('missing.md'))


class CoreOnlyRowTest(MergeCase):
    FILES = ('instructions/_base.md', 'workflow/schema.json', 'workflow/benchmark.yaml', 'hooks/pre-commit.sh')

    def test_only_the_core_layer_provides_these_files(self):
        for rel in self.FILES:
            with self.subTest(rel):
                layerset = self.three(core={rel: 'core\n'}, acme={rel: 'org\n'}, me={rel: 'mine\n'})
                owner, path = layerset.resolve(rel)
                self.assertEqual((owner.id, path.read_text(encoding='utf-8')), ('core', 'core\n'))
                self.assertEqual(layers.merge_rule(rel), 'core-only')

    def test_a_copy_outside_core_is_ignored_with_a_warning(self):
        for rel in self.FILES:
            with self.subTest(rel):
                layerset = self.three(core={}, acme={rel: 'org\n'}, me={rel: 'mine\n'})
                self.assertIsNone(layerset.resolve(rel))                      # nothing from core: no result at all
                found = by_code(self.findings(layerset), 'core-only-ignored')
                self.assertEqual(sorted(item['layer'] for item in found), ['acme', 'me'])
                self.assertTrue(all(item['severity'] == 'warning' for item in found))
        clean = self.three(core={rel: 'c\n' for rel in self.FILES})
        self.assertNotIn('core-only-ignored', codes(self.findings(clean)))


class BaseFragmentsRowTest(MergeCase):
    def order(self, layerset):
        return [(item['layer'], item['name'], item['position']) for item in layerset.base_fragments()]

    def test_prefixes_00_to_09_go_before_the_core_text_and_the_rest_after(self):
        layerset = self.three(
            core={'instructions/_base.d/20-core.md': 'c\n', 'instructions/_base.d/05-core.md': 'c\n'},
            acme={'instructions/_base.d/05-org.md': 'o\n', 'instructions/_base.d/30-z.md': 'z\n', 'instructions/_base.d/15-b.md': 'b\n'},
            me={'instructions/_base.d/01-persona.md': 'p\n', 'instructions/_base.d/99-x.md': 'x\n'})
        self.assertEqual(self.order(layerset), [
            ('core', '05-core.md', 'pre'), ('acme', '05-org.md', 'pre'), ('me', '01-persona.md', 'pre'),
            ('core', '20-core.md', 'post'), ('acme', '15-b.md', 'post'), ('acme', '30-z.md', 'post'), ('me', '99-x.md', 'post')])

    def test_prefix_boundaries(self):
        files = {f'instructions/_base.d/{name}': 'x\n' for name in ('00-a.md', '09-z.md', '10-a.md', '0a-x.md', '9-x.md', 'x.md')}
        layerset = self.build(('core', 'core', files))
        positions = {item['name']: item['position'] for item in layerset.base_fragments()}
        self.assertEqual(positions, {'00-a.md': 'pre', '09-z.md': 'pre', '10-a.md': 'post', '0a-x.md': 'post',
                                     '9-x.md': 'post', 'x.md': 'post'})

    def test_only_markdown_files_are_fragments(self):
        layerset = self.three(acme={'instructions/_base.d/05-a.md': 'a\n', 'instructions/_base.d/notes.txt': 'n\n',
                                    'instructions/_base.d/sub/06-b.md': 'b\n'})
        self.assertEqual([item['name'] for item in layerset.base_fragments()], ['05-a.md'])

    def test_order_of_layers_in_use_decides_not_the_config_order(self):
        files = lambda name: {f'instructions/_base.d/20-{name}.md': name + '\n'}   # noqa: E731
        first = self.build(('core', 'core', files('c')), ('beta', 'org', files('b')), ('acme', 'org', files('a')))
        self.assertEqual([item['layer'] for item in first.base_fragments()], ['core', 'beta', 'acme'])
        second = self.build(('core', 'core', files('c')), ('acme', 'org', files('a')), ('beta', 'org', files('b')))
        self.assertEqual([item['layer'] for item in second.base_fragments()], ['core', 'acme', 'beta'])

    def test_fragments_are_wrapped_in_markers_with_layer_and_file_name(self):
        block = layers.fragment_block('acme', '05-org.md', 'text')
        self.assertEqual(block, '<!-- SKILLSYNC:FRAGMENT begin acme/05-org.md -->\ntext\n<!-- SKILLSYNC:FRAGMENT end acme/05-org.md -->\n')
        self.assertEqual(layers.fragment_block('a', 'f.md', 'x\n'), '<!-- SKILLSYNC:FRAGMENT begin a/f.md -->\nx\n<!-- SKILLSYNC:FRAGMENT end a/f.md -->\n')

    def test_fragments_are_a_sum_not_an_override(self):
        layerset = self.three(core={'instructions/_base.d/10-same.md': 'core\n'}, acme={'instructions/_base.d/10-same.md': 'org\n'})
        self.assertEqual([(item['layer'], item['name']) for item in layerset.base_fragments()],
                         [('core', '10-same.md'), ('acme', '10-same.md')])
        self.assertEqual(layers.merge_rule('instructions/_base.d/10-same.md'), 'base-fragment')
        self.assertIsNone(layerset.resolve('instructions/_base.d/10-same.md'))
        self.assertNotIn('instruction-collision', codes(self.findings(layerset)))


class CodexRowTest(MergeCase):
    def test_maps_merge_deeply_and_exclusion_lists_are_summed(self):
        layerset = self.three(
            core={'codex.yaml': yaml_text({'exclude_skills': ['a', 'b'],
                                           'agents': {'reviewer': {'sandbox_mode': 'read-only', 'model': 'm1'}, 'writer': {'model': 'w'}}})},
            acme={'codex.yaml': yaml_text({'exclude_skills': ['b', 'c'],
                                           'agents': {'reviewer': {'sandbox_mode': 'workspace-write'}, 'extra': {'model': 'e'}}})},
            me={'codex.yaml': yaml_text({'exclude_skills': ['d'], 'agents': {'writer': {'effort': 'high'}}})})
        merged = layerset.merged_file('codex.yaml')
        self.assertEqual(merged.data['exclude_skills'], ['a', 'b', 'c', 'd'])               # sum, deduplicated, ordered
        self.assertEqual(merged.data['agents'], {
            'reviewer': {'sandbox_mode': 'workspace-write', 'model': 'm1'},               # leaf replaced, sibling kept
            'writer': {'model': 'w', 'effort': 'high'}, 'extra': {'model': 'e'}})
        self.assertEqual([source[0] for source in merged.sources], ['core', 'acme', 'me'])

    def test_owners_name_the_layer_of_each_leaf(self):
        layerset = self.three(core={'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro', 'model': 'm'}}})},
                              acme={'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'rw'}}})})
        owners = layerset.merged_file('codex.yaml').owners
        self.assertEqual((owners['codex.yaml:agents.reviewer.sandbox_mode'], owners['codex.yaml:agents.reviewer.model']), ('acme', 'core'))

    def test_a_single_layer_is_copied_unchanged(self):
        data = {'exclude_skills': ['a'], 'agents': {'r': {'x': [1, 2]}}}
        layerset = self.build(('core', 'core', {'codex.yaml': yaml_text(data)}))
        self.assertEqual(layerset.merged_file('codex.yaml').data, data)


class DefaultsRowTest(MergeCase):
    def merged(self, core, acme=None, me=None):
        return self.three(core={'workflow/defaults.yaml': yaml_text(core)},
                          acme={'workflow/defaults.yaml': yaml_text(acme)} if acme is not None else None,
                          me={'workflow/defaults.yaml': yaml_text(me)} if me is not None else None).merged_file('workflow/defaults.yaml')

    def test_maps_are_merged_and_ordinary_lists_are_replaced(self):
        merged = self.merged({'context': {'base': 1, 'max': 2, 'tags': ['a', 'b']}, 'keep': {'x': 1}},
                             {'context': {'max': 3, 'tags': ['c']}}, {'extra': 1})
        self.assertEqual(merged.data, {'context': {'base': 1, 'max': 3, 'tags': ['c']}, 'keep': {'x': 1}, 'extra': 1})

    def test_validation_checks_are_joined_by_id(self):
        merged = self.merged(
            {'validation': {'checks': [{'id': 'unit', 'command': 'core'}, {'id': 'lint', 'command': 'l'}]}},
            {'validation': {'checks': [{'id': 'unit', 'command': 'org'}, {'id': 'e2e', 'command': 'e'}]}},
            {'validation': {'checks': [{'id': 'e2e', 'command': 'mine'}]}})
        self.assertEqual(merged.data['validation']['checks'],
                         [{'id': 'unit', 'command': 'org'}, {'id': 'lint', 'command': 'l'}, {'id': 'e2e', 'command': 'mine'}])
        self.assertEqual({key: owner for key, owner in merged.owners.items() if key.startswith('validation.checks:')},
                         {'validation.checks:unit': 'acme', 'validation.checks:lint': 'core', 'validation.checks:e2e': 'me'})

    def test_guardrails_rules_are_joined_by_id(self):
        merged = self.merged({'guardrails': {'rules': [{'id': 'r1', 'text': 'c'}, {'id': 'r2', 'text': 'c2'}]}},
                             {'guardrails': {'rules': [{'id': 'r1', 'text': 'o'}, {'id': 'r3', 'text': 'o3'}]}})
        self.assertEqual(merged.data['guardrails']['rules'],
                         [{'id': 'r1', 'text': 'o'}, {'id': 'r2', 'text': 'c2'}, {'id': 'r3', 'text': 'o3'}])
        self.assertEqual(merged.owners['guardrails.rules:r1'], 'acme')

    def test_routing_rules_are_appended_in_use_order_without_duplicates(self):
        rule_a, rule_b, rule_c = ({'topics': ['a'], 'instructions': ['x']}, {'topics': ['b']}, {'topics': ['c']})
        merged = self.merged({'routing': {'rules': [rule_a, rule_b]}},
                             {'routing': {'rules': [dict(rule_a), rule_c]}},          # a duplicate by content
                             {'routing': {'rules': [dict(rule_b), {'topics': ['d']}]}})
        self.assertEqual(merged.data['routing']['rules'], [rule_a, rule_b, rule_c, {'topics': ['d']}])

    def test_guardrails_checks_without_id_are_appended_with_deduplication(self):
        merged = self.merged({'guardrails': {'checks': [{'run': 'one'}, {'run': 'two'}]}},
                             {'guardrails': {'checks': [{'run': 'two'}, {'run': 'three'}]}})
        self.assertEqual(merged.data['guardrails']['checks'], [{'run': 'one'}, {'run': 'two'}, {'run': 'three'}])

    def test_deduplication_ignores_key_order_but_not_values(self):
        merged = self.merged({'routing': {'rules': [{'a': 1, 'b': 2}]}}, {'routing': {'rules': [{'b': 2, 'a': 1}, {'a': 1, 'b': 3}]}})
        self.assertEqual(len(merged.data['routing']['rules']), 2)

    def test_the_first_layer_is_merged_like_every_other(self):
        # the id owners must be recorded for the first layer too (the trust model relies on them)
        merged = self.merged({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]}})
        self.assertEqual(merged.owners['validation.checks:unit'], 'core')


class CatalogProfilesRowsTest(MergeCase):
    def test_skill_catalog_is_the_sum_of_skills_and_orgs_with_dotted_files(self):
        layerset = self.three(
            core={'workflow/skill-catalog.yaml': yaml_text({'version': 1, 'skills': {'a': {'scope': 'workflow'}, 'b': {'scope': 'tool'}},
                                                            'orgs': {'x': {'skill': 'x'}}})},
            acme={'workflow/skill-catalog.d/acme.yaml': yaml_text({'skills': {'c': {'scope': 'org'}, 'b': {'scope': 'org'}},
                                                                   'orgs': {'y': {'skill': 'y'}}})},
            me={'workflow/skill-catalog.d/me.yaml': yaml_text({'skills': {'d': {'scope': 'personal'}}})})
        merged = layerset.merged_file('workflow/skill-catalog.yaml')
        self.assertEqual(sorted(merged.data['skills']), ['a', 'b', 'c', 'd'])
        self.assertEqual(merged.data['skills']['b'], {'scope': 'org'})                  # collision: the later wins
        self.assertEqual(sorted(merged.data['orgs']), ['x', 'y'])
        self.assertEqual({key: owner for key, owner in merged.owners.items() if '.b' in key or '.d' in key},
                         {'skill-catalog:skills.b': 'acme', 'skill-catalog:skills.d': 'me'})

    def test_skill_profiles_are_unions_per_profile_including_default(self):
        layerset = self.three(
            core={'workflow/skill-profiles.yaml': yaml_text({'version': 1, 'default': ['core'],
                                                              'profiles': {'core': ['s1', 's2'], 'ui': ['u1']}})},
            acme={'workflow/skill-profiles.d/acme.yaml': yaml_text({'default': ['core', 'acme-base'],
                                                                    'profiles': {'core': ['s2', 's3'], 'acme': ['a1']}})},
            me={'workflow/skill-profiles.d/me.yaml': yaml_text({'default': ['mine'], 'profiles': {'ui': ['u2', 'u1']}})})
        data = layerset.merged_file('workflow/skill-profiles.yaml').data
        self.assertEqual(data['default'], ['core', 'acme-base', 'mine'])
        self.assertEqual(data['profiles'], {'core': ['s1', 's2', 's3'], 'ui': ['u1', 'u2'], 'acme': ['a1']})

    def test_dotted_files_are_read_in_name_order_after_the_base_file_of_their_layer(self):
        layerset = self.three(
            core={'workflow/skill-profiles.yaml': yaml_text({'default': ['one']}),
                  'workflow/skill-profiles.d/b.yaml': yaml_text({'default': ['three']}),
                  'workflow/skill-profiles.d/a.yaml': yaml_text({'default': ['two']}),
                  'workflow/skill-profiles.d/skip.txt': 'not yaml\n'},
            acme={'workflow/skill-profiles.d/z.yaml': yaml_text({'default': ['four']})})
        merged = layerset.merged_file('workflow/skill-profiles.yaml')
        self.assertEqual(merged.data['default'], ['one', 'two', 'three', 'four'])
        self.assertEqual([layer for layer, _ in merged.sources], ['core', 'core', 'core', 'acme'])


class CauseKeysRowTest(MergeCase):
    def keys(self, *entries):
        return self.build(('core', 'core', {'workflow/cause-keys.yaml': yaml_text({'version': 1, 'keys': entries[0]})}),
                          ('acme', 'org', {'workflow/cause-keys.d/acme.yaml': yaml_text({'version': 1, 'keys': entries[1]})}))

    def test_keys_and_aliases_are_summed(self):
        layerset = self.keys({'k1': {'description': 'one', 'aliases': ['a1']}}, {'k2': {'description': 'two', 'aliases': ['a2']}})
        data = layerset.merged_file('workflow/cause-keys.yaml').data
        self.assertEqual(data['keys'], {'k1': {'description': 'one', 'aliases': ['a1']}, 'k2': {'description': 'two', 'aliases': ['a2']}})
        self.assertEqual(self.findings(layerset) and by_code(self.findings(layerset), 'alias-collision'), [])

    def test_a_duplicate_key_takes_the_later_description_and_unites_aliases(self):
        layerset = self.keys({'k1': {'description': 'core text', 'aliases': ['a1']}}, {'k1': {'description': 'org text', 'aliases': ['a2']}})
        merged = layerset.merged_file('workflow/cause-keys.yaml')
        self.assertEqual(merged.data['keys']['k1'], {'description': 'org text', 'aliases': ['a1', 'a2']})
        self.assertEqual(by_code(self.findings(layerset), 'alias-collision'), [])      # a duplicate key is not an error

    def test_the_same_alias_for_the_same_key_in_two_layers_is_not_a_collision(self):
        layerset = self.keys({'k1': {'aliases': ['a1']}}, {'k1': {'aliases': ['a1']}})
        self.assertEqual(by_code(self.findings(layerset), 'alias-collision'), [])
        self.assertEqual(layerset.merged_file('workflow/cause-keys.yaml').data['keys']['k1']['aliases'], ['a1'])

    def test_an_alias_claimed_by_two_keys_of_different_layers_is_an_error(self):
        layerset = self.keys({'k1': {'aliases': ['shared']}}, {'k2': {'aliases': ['shared']}})
        found = by_code(self.findings(layerset), 'alias-collision')
        self.assertEqual([(item['severity'], item['layer']) for item in found], [('error', 'acme')])
        self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')
        merged = layerset.merged_file('workflow/cause-keys.yaml')
        self.assertEqual(merged.data['keys']['k1']['aliases'], ['shared'])               # the earlier owner keeps it
        self.assertNotIn('shared', merged.data['keys']['k2']['aliases'])                 # never silently shared

    def test_an_alias_equal_to_a_key_of_another_layer_is_an_error_both_ways(self):
        for core, org in (({'k1': {}}, {'k2': {'aliases': ['k1']}}), ({'k1': {'aliases': ['k2']}}, {'k2': {}})):
            with self.subTest(core=core):
                layerset = self.keys(core, org)
                self.assertTrue(by_code(self.findings(layerset), 'alias-collision'))
                self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')


class LessonsIndexRowTest(MergeCase):
    def test_records_are_summed_by_path(self):
        layerset = self.three(
            core={'workflow/lessons/index.yaml': yaml_text({'version': 1, 'lessons': [
                {'path': 'a.md', 'title': 'core A'}, {'path': 'b.md', 'title': 'core B'}]})},
            acme={'workflow/lessons/index.d/acme.yaml': yaml_text({'lessons': [
                {'path': 'b.md', 'title': 'org B'}, {'path': 'c.md', 'title': 'org C'}]})},
            me={'workflow/lessons/index.d/me.yaml': yaml_text({'lessons': [{'title': 'no path'}, {'path': 'd.md', 'title': 'D'}]})})
        merged = layerset.merged_file('workflow/lessons/index.yaml')
        self.assertEqual([(item.get('path'), item['title']) for item in merged.data['lessons']],
                         [('a.md', 'core A'), ('b.md', 'org B'), ('c.md', 'org C'), (None, 'no path'), ('d.md', 'D')])
        self.assertEqual({key: owner for key, owner in merged.owners.items()},
                         {'lessons:a.md': 'core', 'lessons:b.md': 'acme', 'lessons:c.md': 'acme', 'lessons:d.md': 'me'})
        self.assertEqual(merged.data['version'], 1)


class NotComposedRowTest(MergeCase):
    ROOT_FILES = ('NOTICE', 'NOTICE.md', 'CHANGELOG.md', 'README.md', 'LICENSE', 'layer.yaml', 'leakguard/identity.yaml',
                  'leakguard/anything.txt')

    def test_files_of_the_layer_itself_are_not_composed(self):
        layerset = self.three(core={'NOTICE': 'core\n', 'leakguard/identity.yaml': 'c\n'}, acme={'NOTICE': 'org\n', 'leakguard/identity.yaml': 'o\n'})
        for rel in self.ROOT_FILES:
            with self.subTest(rel):
                self.assertEqual(layers.merge_rule(rel), 'not-composed')
                self.assertIsNone(layerset.resolve(rel))

    def test_only_the_layer_root_level_documents_are_exempt(self):
        self.assertEqual(layers.merge_rule('skills/feature/README.md'), 'skill')
        self.assertEqual(layers.merge_rule('skills/feature/CHANGELOG.md'), 'skill')
        self.assertEqual(layers.merge_rule('docs/NOTICE'), 'per-file')


class RoutingOfPathsTest(MergeCase):
    def test_every_table_row_maps_to_its_merge_rule(self):
        table = {
            'skills/a/SKILL.md': 'skill', 'skills/a/refs/x.md': 'skill',
            'instructions/java.md': 'instruction', 'instructions/_base.md': 'core-only',
            'instructions/_base.d/05-x.md': 'base-fragment',
            'workflow/schema.json': 'core-only', 'workflow/benchmark.yaml': 'core-only', 'hooks/a.sh': 'core-only',
            'agents/r.md': 'agent', 'codex.yaml': 'codex', 'workflow/defaults.yaml': 'defaults',
            'workflow/projects/p.yaml': 'per-file', 'workflow/trackers/t.yaml': 'per-file',
            'workflow/skill-catalog.yaml': 'catalog', 'workflow/skill-catalog.d/x.yaml': 'catalog',
            'workflow/skill-profiles.yaml': 'profiles', 'workflow/skill-profiles.d/x.yaml': 'profiles',
            'workflow/cause-keys.yaml': 'cause-keys', 'workflow/cause-keys.d/x.yaml': 'cause-keys',
            'workflow/lessons/index.yaml': 'lessons-index', 'workflow/lessons/index.d/x.yaml': 'lessons-index',
            'workflow/lessons/a.md': 'lesson-file',
            'NOTICE': 'not-composed', 'README.md': 'not-composed', 'CHANGELOG.md': 'not-composed',
            'layer.yaml': 'not-composed', 'leakguard/identity.yaml': 'not-composed',
        }
        for rel, expected in table.items():
            with self.subTest(rel):
                self.assertEqual(layers.merge_rule(rel), expected)

    def test_aggregated_files_cannot_be_resolved_per_file_and_others_cannot_be_merged(self):
        layerset = self.three(core={'codex.yaml': 'a: 1\n', 'skills/x/SKILL.md': 'x\n'})
        with self.assertRaises(layers.LayerError) as aggregate:
            layerset.resolve('codex.yaml')
        self.assertEqual(aggregate.exception.code, 'aggregate-file')
        with self.assertRaises(layers.LayerError) as other:
            layerset.merged_file('skills/x/SKILL.md')
        self.assertEqual(other.exception.code, 'not-aggregate')

    def test_paths_cannot_escape_the_layer(self):
        layerset = self.three()
        with self.assertRaises(layers.LayerError):
            layerset.resolve('skills/../../etc/passwd')

    def test_broken_yaml_in_an_aggregate_is_an_error_finding_not_a_crash(self):
        layerset = self.three(core={'codex.yaml': 'a: [unclosed\n'})
        found = by_code(self.findings(layerset), 'merge-invalid')
        self.assertEqual([item['severity'] for item in found], ['error'])
        self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')


# ====================================================================== trust model (K2)


class TrustModelTest(MergeCase):
    """An org layer must not override core elements without `allow_override`; personal may."""

    ELEMENTS = {
        'skill file': ({'skills/feature/SKILL.md': 'core\n'}, {'skills/feature/SKILL.md': 'org\n'}, 'skills/feature/SKILL.md'),
        'role (agent file)': ({'agents/reviewer.md': 'core\n'}, {'agents/reviewer.md': 'org\n'}, 'agents/reviewer.md'),
        'tracker adapter': ({'workflow/trackers/jira.yaml': 'a: 1\n'}, {'workflow/trackers/jira.yaml': 'a: 2\n'}, 'workflow/trackers/jira.yaml'),
        'validation check': ({'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]}})},
                             {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'o'}]}})},
                             'validation.checks:unit'),
        'guardrail rule': ({'workflow/defaults.yaml': yaml_text({'guardrails': {'rules': [{'id': 'r1', 'text': 'c'}]}})},
                           {'workflow/defaults.yaml': yaml_text({'guardrails': {'rules': [{'id': 'r1', 'text': 'o'}]}})},
                           'guardrails.rules:r1'),
        'codex role key': ({'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'read-only'}}})},
                           {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'workspace-write'}}})},
                           'codex.yaml:agents.reviewer.sandbox_mode'),
    }

    def consent_findings(self, layerset):
        return by_code(self.findings(layerset), 'override-without-consent')

    def test_an_org_override_of_a_core_element_is_an_error_without_consent(self):
        for label, (core, org, _) in self.ELEMENTS.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', org))
                found = self.consent_findings(layerset)
                self.assertEqual([(item['severity'], item['layer']) for item in found], [('error', 'acme')], found)
                self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')

    def test_a_matching_allow_override_entry_lets_the_override_pass(self):
        for label, (core, org, key) in self.ELEMENTS.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=[key])
                self.assertEqual(self.consent_findings(layerset), [])
                self.assertEqual(layers.findings_status(self.findings(layerset)), 'PASS')

    def test_globs_in_allow_override_work_and_unrelated_entries_do_not(self):
        core, org, _ = self.ELEMENTS['skill file']
        for entry in ('skills/feature/**', 'skills/*/SKILL.md', 'skills/feature/SKILL.md'):
            with self.subTest(allowed=entry):
                layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=[entry])
                self.assertEqual(self.consent_findings(layerset), [])
        for entry in ('skills/other/**', 'agents/reviewer.md', 'validation.checks:unit'):
            with self.subTest(not_allowed=entry):
                layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=[entry])
                self.assertEqual(len(self.consent_findings(layerset)), 1)
        core, org, _ = self.ELEMENTS['validation check']
        layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['validation.checks:*'])
        self.assertEqual(self.consent_findings(layerset), [])

    def test_consent_for_one_element_does_not_cover_another(self):
        core = {**self.ELEMENTS['skill file'][0], **self.ELEMENTS['role (agent file)'][0]}
        org = {**self.ELEMENTS['skill file'][1], **self.ELEMENTS['role (agent file)'][1]}
        layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['agents/reviewer.md'])
        found = self.consent_findings(layerset)
        self.assertEqual(len(found), 1)
        self.assertIn('skills/feature/SKILL.md', found[0]['message'])

    def test_personal_layers_may_override_core_elements(self):
        for label, (core, personal, _) in self.ELEMENTS.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('me', 'personal', personal))
                self.assertEqual(self.consent_findings(layerset), [])
                self.assertEqual(layers.findings_status(self.findings(layerset)), 'PASS')

    def test_additions_that_override_nothing_need_no_consent(self):
        core = {'skills/feature/SKILL.md': 'c\n', 'agents/reviewer.md': 'c\n',
                'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]}}),
                'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro'}}})}
        org = {'skills/feature/extra.md': 'o\n', 'skills/own/SKILL.md': 'o\n', 'agents/own.md': 'o\n',
               'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'new', 'command': 'n'}]}}),
               'codex.yaml': yaml_text({'agents': {'own': {'sandbox_mode': 'rw', 'model': 'm'}}, 'exclude_skills': ['x']})}
        layerset = self.build(('core', 'core', core), ('acme', 'org', org))
        self.assertEqual(self.consent_findings(layerset), [])                                  # a new org role is allowed

    def test_every_key_of_a_core_role_is_protected_added_changed_or_removed(self):
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro', 'effort': 'low'}}})}
        cases = {
            'added key': {'agents': {'reviewer': {'model': 'm'}}},
            'changed key': {'agents': {'reviewer': {'effort': 'high'}}},
            'removed key (set to null)': {'agents': {'reviewer': {'effort': None}}},
        }
        for label, data in cases.items():
            with self.subTest(label):
                org = {'codex.yaml': yaml_text(data)}
                found = self.consent_findings(self.build(('core', 'core', core), ('acme', 'org', org)))
                self.assertEqual([item['layer'] for item in found], ['acme'], label)
                self.assertTrue(found[0]['message'].startswith('codex.yaml:agents.reviewer.'), found[0]['message'])
                allowed = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.reviewer.*'])
                self.assertEqual(self.consent_findings(allowed), [])
                personal = self.build(('core', 'core', core), ('me', 'personal', org))
                self.assertEqual(self.consent_findings(personal), [])

    def test_adding_a_key_to_an_empty_core_role_needs_consent(self):
        core = {'codex.yaml': yaml_text({'agents': {'x': {}}})}
        org = {'codex.yaml': yaml_text({'agents': {'x': {'model': 'm'}}})}
        found = self.consent_findings(self.build(('core', 'core', core), ('acme', 'org', org)))
        self.assertEqual([item['message'].split()[0] for item in found], ['codex.yaml:agents.x'])
        allowed = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.x'])
        self.assertEqual(self.consent_findings(allowed), [])
        self.assertEqual(self.consent_findings(self.build(('core', 'core', core), ('me', 'personal', org))), [])

    def test_replacing_a_container_by_another_type_cannot_bypass_the_model(self):
        core = {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]},
                                                      'guardrails': {'rules': [{'id': 'r1', 'text': 't'}]}}),
                'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro'}}})}
        variants = {
            'validation.checks null': {'workflow/defaults.yaml': 'validation:\n  checks: null\n'},
            'validation.checks empty map': {'workflow/defaults.yaml': 'validation:\n  checks: {}\n'},
            'validation.checks scalar': {'workflow/defaults.yaml': 'validation:\n  checks: nope\n'},
            'validation null': {'workflow/defaults.yaml': 'validation: null\n'},
            'validation scalar': {'workflow/defaults.yaml': 'validation: 5\n'},
            'guardrails.rules null': {'workflow/defaults.yaml': 'guardrails:\n  rules: null\n'},
            'guardrails.rules scalar': {'workflow/defaults.yaml': 'guardrails:\n  rules: off\n'},
            'guardrails null': {'workflow/defaults.yaml': 'guardrails: null\n'},
            'codex agents null': {'codex.yaml': 'agents: null\n'},
            'codex agents list': {'codex.yaml': 'agents: []\n'},
            'codex role null': {'codex.yaml': 'agents:\n  reviewer: null\n'},
        }
        for label, org in variants.items():
            with self.subTest(label):
                found = self.consent_findings(self.build(('core', 'core', core), ('acme', 'org', org)))
                self.assertEqual([item['layer'] for item in found], ['acme'], label)
                self.assertEqual(layers.findings_status(self.findings(self.build(('core', 'core', core), ('acme', 'org', org)))), 'FAIL')
                allowed = self.build(('core', 'core', core), ('acme', 'org', org),
                                     allow_override=['validation.checks:*', 'guardrails.rules:*', 'codex.yaml:agents.*'])
                self.assertEqual(self.consent_findings(allowed), [], label)
                self.assertEqual(self.consent_findings(self.build(('core', 'core', core), ('me', 'personal', org))), [], label)

    def test_empty_containers_that_change_nothing_are_not_overrides(self):
        core = {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]}}),
                'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro'}}})}
        for label, org in {'agents empty map': {'codex.yaml': 'agents: {}\n'},
                           'role empty map': {'codex.yaml': 'agents:\n  reviewer: {}\n'},
                           'validation empty map': {'workflow/defaults.yaml': 'validation: {}\n'},
                           'checks empty list': {'workflow/defaults.yaml': 'validation:\n  checks: []\n'},
                           'no file content': {'codex.yaml': '', 'workflow/defaults.yaml': ''}}.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', org))
                self.assertEqual(self.consent_findings(layerset), [], label)

    def test_the_result_of_the_merge_is_compared_so_a_later_layer_cannot_undo_a_guarded_change_unseen(self):
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro'}}})}
        changed = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'rw'}}})}
        layerset = self.build(('core', 'core', core), ('acme', 'org', changed), ('me', 'personal', dict(core)))
        self.assertEqual([item['layer'] for item in self.consent_findings(layerset)], ['acme'])      # judged per org layer vs core

    def test_an_org_may_override_another_org_and_personal_may_override_an_org(self):
        layerset = self.build(('core', 'core', {}), ('acme', 'org', {'skills/s/SKILL.md': 'a\n', 'agents/r.md': 'a\n'}),
                              ('beta', 'org', {'skills/s/SKILL.md': 'b\n', 'agents/r.md': 'b\n'}),
                              ('me', 'personal', {'skills/s/SKILL.md': 'm\n'}))
        self.assertEqual(self.consent_findings(layerset), [])

    def test_element_overrides_between_non_core_layers_need_no_consent(self):
        check = lambda command: {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': command}]}})}   # noqa: E731
        layerset = self.build(('core', 'core', {}), ('acme', 'org', check('a')), ('beta', 'org', check('b')), ('me', 'personal', check('m')))
        self.assertEqual(self.consent_findings(layerset), [])
        merged = layerset.merged_file('workflow/defaults.yaml')
        self.assertEqual(merged.data['validation']['checks'], [{'id': 'unit', 'command': 'm'}])
        self.assertEqual(merged.owners['validation.checks:unit'], 'me')

    def test_an_identical_copy_of_a_core_element_is_not_an_override(self):
        for label, (core, _, _) in self.ELEMENTS.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', dict(core)))
                self.assertEqual(self.consent_findings(layerset), [])
        # a list element with an `id` and a lessons record copied unchanged
        defaults = {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c', 'args': ['-x']}]}})}
        lessons = {'workflow/lessons/index.yaml': yaml_text({'lessons': [{'path': 'a.md', 'title': 'A'}]})}
        layerset = self.build(('core', 'core', {**defaults, **lessons}), ('acme', 'org', {**defaults, **lessons}))
        self.assertEqual(self.consent_findings(layerset), [])
        self.assertEqual(by_code(self.findings(layerset), 'override-without-consent'), [])

    def test_a_copy_by_one_org_does_not_hide_a_change_by_another(self):
        for label, (core, changed, _) in self.ELEMENTS.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', dict(core)), ('beta', 'org', changed))
                self.assertEqual([item['layer'] for item in self.consent_findings(layerset)], ['beta'])
                # the order of the two org layers does not matter: the comparison is with core
                layerset = self.build(('core', 'core', core), ('beta', 'org', changed), ('acme', 'org', dict(core)))
                self.assertEqual([item['layer'] for item in self.consent_findings(layerset)], ['beta'])
                # a second org that changes the same element is reported too
                layerset = self.build(('core', 'core', core), ('acme', 'org', changed), ('beta', 'org', changed))
                self.assertEqual(sorted(item['layer'] for item in self.consent_findings(layerset)), ['acme', 'beta'])

    def test_replacing_a_core_role_leaf_by_a_subtree_or_back_is_an_override(self):
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'read-only', 'tools': {'a': 1}}}})}
        for label, replacement in (('leaf by subtree', {'reviewer': {'sandbox_mode': {'mode': 'rw'}}}),
                                   ('subtree by leaf', {'reviewer': {'tools': 'none'}}),
                                   ('subtree member changed', {'reviewer': {'tools': {'a': 2}}})):
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', {'codex.yaml': yaml_text({'agents': replacement})}))
                self.assertEqual(len(self.consent_findings(layerset)), 1, label)

    def test_catalog_and_cause_key_aggregates_have_no_trust_findings(self):
        core = {'workflow/skill-catalog.yaml': yaml_text({'skills': {'a': {'scope': 'workflow'}}, 'orgs': {'x': {'skill': 'x'}}}),
                'workflow/cause-keys.yaml': yaml_text({'version': 1, 'keys': {'k1': {'description': 'core text', 'aliases': []}}})}
        org = {'workflow/skill-catalog.d/acme.yaml': yaml_text({'skills': {'a': {'scope': 'org'}}, 'orgs': {'x': {'skill': 'other'}}}),
               'workflow/cause-keys.d/acme.yaml': yaml_text({'version': 1, 'keys': {'k1': {'description': 'org text', 'aliases': []}}})}
        layerset = self.build(('core', 'core', core), ('acme', 'org', org))
        self.assertEqual(self.findings(layerset), [])                                         # not even a warning
        merged = layerset.merged_file('workflow/skill-catalog.yaml')
        self.assertEqual(merged.data['skills']['a'], {'scope': 'org'})                         # later still wins
        self.assertEqual(layerset.merged_file('workflow/cause-keys.yaml').data['keys']['k1']['description'], 'org text')

    def test_switching_trust_off_keeps_the_merge_findings(self):
        collision = {'workflow/cause-keys.yaml': yaml_text({'keys': {'k1': {'aliases': ['shared']}}})}
        other = {'workflow/cause-keys.d/a.yaml': yaml_text({'keys': {'k2': {'aliases': ['shared']}}}),
                 'codex.yaml': 'a: [unclosed\n', 'instructions/java.md': 'x\n', 'hooks/pre.sh': 'x\n'}
        layerset = self.build(('core', 'core', {**collision, 'instructions/java.md': 'c\n'}), ('acme', 'org', other))
        for trust in (True, False):
            with self.subTest(trust=trust):
                found = codes(self.findings(layerset, trust=trust))
                self.assertTrue({'alias-collision', 'merge-invalid', 'instruction-collision', 'core-only-ignored'} <= found, found)
        core, org, _ = self.ELEMENTS['validation check']
        layerset = self.build(('core', 'core', core), ('acme', 'org', org))
        self.assertIn('override-without-consent', codes(self.findings(layerset, trust=True)))
        self.assertNotIn('override-without-consent', codes(self.findings(layerset, trust=False)))

    # ---- D73: no symlinks, junctions or reparse points inside layers; every read skips them

    @staticmethod
    def link(target, where):
        where.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, where, target_is_directory=Path(target).is_dir())

    def outside(self, files):
        """A directory OUTSIDE every layer (a link target that must never be read)."""
        MergeCase.sets += 1
        base = self.work / f'external{MergeCase.sets}'
        for rel, text in files.items():
            write(base / rel, text)
        return base

    def linked_org(self, core=None, **kwargs):
        """core + org layers; the org layer holds real `aaa/` and `bbb.md` used as link targets."""
        core = core if core is not None else {'skills/feature/SKILL.md': 'core\n', 'agents/reviewer.md': 'core\n',
                                              'workflow/trackers/jira.yaml': 'a: 1\n', 'hooks/x.py': 'core\n'}
        org = {'aaa/SKILL.md': 'evil\n', 'aaa/jira.yaml': 'a: 2\n', 'aaa/reviewer.md': 'evil\n', 'bbb.md': 'evil\n',
               'ccc.yaml': yaml_text({'default': ['evil']}), 'ddd/z.yaml': yaml_text({'skills': {'evil': {'scope': 'x'}}})}
        return self.build(('core', 'core', core), ('acme', 'org', org), **kwargs)

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_links_are_skipped_by_every_read_of_the_layer(self):
        for mode in LINK_MODES:                      # layers are read the same way in symlink and copy mode
            with self.subTest(mode), link_mode(mode):
                layerset = self.linked_org()
                acme = layerset.get('acme')
                root = acme.root
                self.link(root / 'aaa', root / 'skills' / 'feature')                      # directory link over a core skill
                self.link(root / 'aaa', root / 'skills' / 'alias')                        # directory link, new name
                self.link(root / 'bbb.md', root / 'agents' / 'reviewer.md')               # file link over a core role
                self.link(root / 'aaa', root / 'workflow' / 'trackers')                   # directory link over the tracker dir
                self.link(root / 'bbb.md', root / 'hooks' / 'x.py')                       # file link over a core hook
                self.link(root / 'bbb.md', root / 'instructions' / '_base.d' / '05-x.md')  # fragment
                self.link(root / 'ccc.yaml', root / 'workflow' / 'skill-profiles.d' / 'x.yaml')   # aggregate part
                self.link(root / 'bbb.md', root / 'instructions' / 'java.md')
                self.link(root / 'ddd', root / 'workflow' / 'skill-catalog.d')             # a whole aggregate directory
                self.link(root / 'ccc.yaml', root / 'workflow' / 'skill-profiles.yaml')    # the base file of an aggregate
                self.link(root / 'ccc.yaml', root / 'codex.yaml')
                self.link(root / 'bbb.md', root / 'skills' / 'solo' / 'SKILL.md')           # a skill whose SKILL.md is a link
                self.assertEqual(self.consent_findings(layerset), [], 'the trust model does not read links')
                self.assertEqual([layer.id for layer in layerset.skill_providers('feature')], ['core'])
                self.assertEqual({rel: owner.id for rel, (owner, _) in layerset.skill_files('feature').items()}, {'SKILL.md': 'core'})
                self.assertEqual(layerset.skill_names(), ['feature'])
                self.assertEqual(layerset.resolve('agents/reviewer.md')[0].id, 'core')
                self.assertEqual({name: owner.id for name, (owner, _) in layerset.agent_files().items()}, {'reviewer.md': 'core'})
                self.assertEqual({name: owner.id for name, (owner, _) in layerset.listing('workflow/trackers').items()}, {'jira.yaml': 'core'})
                self.assertEqual(layerset.resolve('workflow/trackers/jira.yaml')[0].id, 'core')
                self.assertEqual(layerset.resolve('hooks/x.py')[0].id, 'core')
                self.assertEqual(layerset.base_fragments(), [])
                self.assertEqual(layerset.instruction_files(), {})
                self.assertEqual([item[0] for item in layerset.merged_file('workflow/skill-profiles.yaml').sources], [])
                self.assertNotIn('evil', str(layerset.merged_file('workflow/skill-profiles.yaml').data))
                self.assertNotIn('evil', str(layerset.merged_file('workflow/skill-catalog.yaml').data))
                self.assertEqual(layerset.merged_file('codex.yaml').sources, [])
                self.assertEqual(by_code(self.findings(layerset), 'core-only-ignored'), [], 'a linked hook is not read, so not reported as ignored')
                self.assertEqual(layers.layer_files(acme, 'skills'), [])
                self.assertEqual(layers.layer_files(acme, 'skills/feature'), [])
                self.assertEqual(layers.layer_files(acme, 'agents'), [])
                self.assertEqual(sorted(layers.layer_files(acme)), sorted(
                    ['CHANGELOG.md', 'README.md', 'aaa/SKILL.md', 'aaa/jira.yaml', 'aaa/reviewer.md', 'bbb.md', 'ccc.yaml', 'ddd/z.yaml', 'layer.yaml']))
                self.assertEqual(layers.layer_links(acme), ['agents/reviewer.md', 'codex.yaml', 'hooks/x.py', 'instructions/_base.d/05-x.md',
                                                            'instructions/java.md', 'skills/alias', 'skills/feature',
                                                            'skills/solo/SKILL.md', 'workflow/skill-catalog.d',
                                                            'workflow/skill-profiles.d/x.yaml', 'workflow/skill-profiles.yaml',
                                                            'workflow/trackers'])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_every_link_is_an_error_finding_without_the_target(self):
        layerset = self.linked_org()
        root = layerset.get('acme').root
        outside = self.outside({'SKILL.md': 'x\n'})
        self.link(outside, root / 'skills' / 'out')                      # leaves the layer
        self.link(root / 'aaa', root / 'skills' / 'back')                # leaves and returns / stays inside
        self.link(root / 'bbb.md', root / 'agents' / 'reviewer.md')
        found = by_code(self.findings(layerset), 'symlink-in-layer')
        self.assertEqual(sorted(item['message'].split(': ')[1].split(' ')[0] for item in found),
                         ['agents/reviewer.md', 'skills/back', 'skills/out'])
        self.assertTrue(all(item['severity'] == 'error' and item['layer'] == 'acme' for item in found))
        self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')
        for item in found:
            self.assertNotIn(outside.name, item['message'])
            self.assertNotIn('aaa', item['message'])
            self.assertNotIn('bbb', item['message'])
        self.assertEqual(len(by_code(self.findings(layerset, trust=False), 'symlink-in-layer')), 3)   # not part of the trust switch
        clean = self.linked_org()
        self.assertEqual(by_code(self.findings(clean), 'symlink-in-layer'), [])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_links_in_core_are_skipped_and_reported_too(self):
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'core\n', 'aaa/SKILL.md': 'core\n', 'bbb.md': 'core\n'}),
                              ('acme', 'org', {'skills/linked/SKILL.md': 'org\n', 'skills/feature/SKILL.md': 'core\n',
                                               'agents/reviewer.md': 'org differs\n'}))
        core = layerset.core.root
        self.link(core / 'aaa', core / 'skills' / 'linked')
        self.link(core / 'bbb.md', core / 'agents' / 'reviewer.md')
        self.assertEqual(layerset.skill_names(), ['feature', 'linked'])               # `linked` exists in the org layer only
        self.assertEqual([layer.id for layer in layerset.skill_providers('linked')], ['acme'])
        self.assertEqual(self.consent_findings(layerset), [], 'a linked core entry guards nothing')
        found = by_code(self.findings(layerset), 'symlink-in-layer')
        self.assertEqual(sorted(item['layer'] for item in found), ['core', 'core'])
        self.assertEqual(layers.layer_links(layerset.core), ['agents/reviewer.md', 'skills/linked'])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_a_link_in_the_middle_of_a_path_excludes_the_whole_subtree(self):
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'core\n'}),
                              ('acme', 'org', {'real/feature/SKILL.md': 'evil\n', 'real/feature/ref.md': 'evil\n', 'real/other/SKILL.md': 'x\n'}))
        acme = layerset.get('acme')
        self.link(acme.root / 'real', acme.root / 'skills')                          # `skills` itself is a link
        self.assertEqual(layerset.skill_names(), ['feature'])
        self.assertEqual([layer.id for layer in layerset.skill_providers('feature')], ['core'])
        self.assertEqual(sorted(layerset.skill_files('feature')), ['SKILL.md'])
        self.assertEqual(layers.layer_files(acme, 'skills/feature'), [])              # a link on the path to rel_dir
        self.assertEqual(layers.layer_files(acme, 'skills/other'), [])
        self.assertEqual(self.consent_findings(layerset), [])
        self.assertEqual(layers.layer_links(acme), ['skills'])                       # only the link, not its subtree

    def subdir_layer(self, *, git_repo=False, extra=None):
        """A layer whose layout lives in `library/` of a repository (checkout) next to a core layer.
        Returns (checkout path, project path)."""
        MergeCase.sets += 1
        repo = self.work / f'repo{MergeCase.sets}'
        make_layer(repo / 'library', 'sub', 'org', {'skills/real/SKILL.md': 'x\n', 'instructions/_base.d/05-x.md': 'f\n',
                                                     'workflow/skill-catalog.yaml': yaml_text({'skills': {'real': {'scope': 'org'}}}),
                                                     **(extra or {})})
        core = make_layer(self.work / f'core{MergeCase.sets}', 'core', 'core', version=generator_version())
        self.write_config([('core', 'core', core), ('sub', 'org', repo, {'subdir': 'library'})], [(['**'], ['core', 'sub'])])
        if git_repo:
            git(repo, 'init', '-q', '-b', 'main')
            git(repo, 'config', 'user.email', 'dev@example.test')
            git(repo, 'config', 'user.name', 'Dev')
        return repo, self.project(f'proj{MergeCase.sets}')

    @staticmethod
    def commit_all(repo):
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '--no-verify', '-m', 'content')

    def sub(self, project):
        layerset = layers.resolve_layers(project)
        return layerset, layerset.get('sub')

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_links_inside_a_subdir_layer_are_reported_relative_to_the_checkout(self):
        repo, project = self.subdir_layer()
        write(repo / 'elsewhere' / 'SKILL.md', 'in the repo, outside subdir\n')
        self.link(repo / 'elsewhere', repo / 'library' / 'skills' / 'up')            # leaves `subdir`, stays in the repo
        self.link(repo / 'library' / 'skills' / 'real', repo / 'library' / 'skills' / 'again')
        layerset, sub = self.sub(project)
        self.assertEqual(layerset.skill_names(), ['real'])
        self.assertEqual(layers.layer_links(sub), ['library/skills/again', 'library/skills/up'])
        self.assertEqual(sorted(item['message'].split(': ')[1].split(' ')[0] for item in by_code(self.findings(layerset), 'symlink-in-layer')),
                         ['library/skills/again', 'library/skills/up'])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_a_link_on_the_subdir_path_disables_the_whole_layer(self):
        repo, project = self.subdir_layer()
        real = repo.parent / (repo.name + '-real')
        (repo / 'library').rename(real)
        self.link(real, repo / 'library')                                              # `library` itself is a link
        layerset, sub = self.sub(project)
        self.assertFalse(sub.root_clean)
        self.assertEqual(layerset.skill_names(), [])
        self.assertEqual(layerset.skill_providers('real'), [])
        self.assertEqual(layers.layer_files(sub), [])
        self.assertEqual(layers.layer_files(sub, 'skills'), [])
        self.assertIsNone(layerset.resolve('skills/real/SKILL.md'))
        self.assertEqual(layerset.listing('skills', dirs=True), {})
        self.assertEqual(layerset.base_fragments(), [])
        self.assertEqual(layerset.merged_file('workflow/skill-catalog.yaml').sources, [])
        self.assertEqual(layerset.merged_file('workflow/skill-catalog.yaml').data, {})
        self.assertIsNone(sub.meta)
        self.assertTrue(sub.meta_error)
        with self.assertRaises(layers.LayerError) as caught:
            sub.identity_file()
        self.assertIn(caught.exception.code, ('layer-yaml-invalid', 'symlink-in-layer'))
        self.assertEqual(self.consent_findings(layerset), [])
        findings = self.findings(layerset)
        self.assertEqual(layers.layer_links(sub), ['library'])
        self.assertEqual([item['layer'] for item in by_code(findings, 'symlink-in-layer')], ['sub'])
        self.assertTrue(by_code(findings, 'layer-yaml-invalid'))
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        for item in findings:
            self.assertNotIn(real.name, item['message'])                                # the target is never disclosed

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_an_untracked_link_on_the_subdir_path_is_still_a_violation_in_a_git_checkout(self):
        repo, project = self.subdir_layer(git_repo=True)
        real = repo.parent / (repo.name + '-real')
        (repo / 'library').rename(real)
        self.link(real, repo / 'library')                                              # never added to git
        layerset, sub = self.sub(project)
        self.assertEqual(layers.layer_links(sub), ['library'])
        self.assertEqual(layers.layer_files(sub), [])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_outside_subdir_only_links_tracked_by_git_are_violations(self):
        repo, project = self.subdir_layer(git_repo=True)
        write(repo / '.gitignore', '.claude/\n')
        write(repo / 'tools' / 'x.txt', 'x\n')
        self.link(repo / 'tools' / 'x.txt', repo / 'tools' / 'tracked')
        self.commit_all(repo)
        self.link(repo / 'tools' / 'x.txt', repo / 'tools' / 'untracked')              # not added to git
        self.link(repo / 'tools', repo / '.claude' / 'skills' / 'z')                   # ignored artefact, like the ones `skillsync link` writes
        layerset, sub = self.sub(project)
        self.assertEqual(layers.layer_links(sub), ['tools/tracked'])
        found = by_code(self.findings(layerset), 'symlink-in-layer')
        self.assertEqual([(item['layer'], item['severity']) for item in found], [('sub', 'error')])
        self.assertIn('tools/tracked', found[0]['message'])
        self.assertEqual(layerset.skill_names(), ['real'], 'readers are unaffected by links outside the layout root')
        # inside the root every link counts, tracked or not
        self.link(repo / 'tools' / 'x.txt', repo / 'library' / 'skills' / 'real' / 'loose')
        self.assertEqual(layers.layer_links(sub), ['library/skills/real/loose', 'tools/tracked'])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_a_checkout_without_git_reports_every_link_outside_dot_git(self):
        repo, project = self.subdir_layer()
        write(repo / 'tools' / 'x.txt', 'x\n')
        self.link(repo / 'tools' / 'x.txt', repo / 'tools' / 'l1')
        self.link(repo / 'tools' / 'x.txt', repo / '.claude' / 'l2')
        layerset, sub = self.sub(project)
        self.assertEqual(layers.layer_links(sub), ['.claude/l2', 'tools/l1'])
        self.assertEqual(len(by_code(self.findings(layerset), 'symlink-in-layer')), 2)

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_a_layer_without_subdir_reports_every_link_even_untracked(self):
        layerset = self.build(('core', 'core', {}), ('acme', 'org', {'real.md': 'x\n'}))
        acme = layerset.get('acme')
        git(acme.path, 'init', '-q', '-b', 'main')
        self.link(acme.root / 'real.md', acme.root / '.claude' / 'link')                 # root == checkout: nothing is exempt
        self.assertEqual(layers.layer_links(acme), ['.claude/link'])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_layer_yaml_that_is_a_link_is_invalid_and_its_content_is_not_disclosed(self):
        layerset = self.build(('core', 'core', {}), ('acme', 'org', {}))
        acme = layerset.get('acme')
        (acme.root / 'layer.yaml').rename(acme.root / 'real-meta.yaml')
        (acme.root / 'real-meta.yaml').write_text('schema: 1\nid: UNIQUEID\nkind: UNIQUEKIND\nversion: UNIQUEVER\n', encoding='utf-8')
        self.link(acme.root / 'real-meta.yaml', acme.root / 'layer.yaml')
        meta, error = layers.read_layer_yaml(acme.root)
        self.assertIsNone(meta)
        self.assertIn('symlink', error)
        self.assertNotIn('UNIQUE', error)
        fresh = layers.resolve_layers(self.project('p3')).get('acme')
        findings = self.findings(layers.resolve_layers(self.project('p3')))
        self.assertTrue(by_code(findings, 'layer-yaml-invalid'))
        self.assertEqual(layers.findings_status(findings), 'BLOCKED')
        for item in findings:
            self.assertNotIn('UNIQUE', item['message'])
        with self.assertRaises(layers.LayerError) as caught:
            fresh.identity_file()
        self.assertEqual(caught.exception.code, 'layer-yaml-invalid')
        self.assertNotIn('UNIQUE', str(caught.exception))

    def test_validation_messages_of_layer_yaml_do_not_contain_the_file_content(self):
        base = {'schema': 1, 'id': 'a', 'kind': 'org', 'version': '1.0.0', 'core_min': '0.9.0'}
        bad = {'version': 'UNIQUEVER', 'core_min': 'UNIQUECM', 'schema': 'UNIQUESC', 'id': 'UNIQUE ID', 'kind': 'UNIQUEKIND',
               'requires': ['UNIQUE REQ'], 'identity': '../UNIQUEPATH'}
        for key, value in bad.items():
            with self.subTest(key):
                directory = self.work / f'meta-{key}'
                write(directory / 'layer.yaml', yaml_text(dict(base, **{key: value})))
                meta, error = layers.read_layer_yaml(directory)
                self.assertIsNone(meta)
                self.assertTrue(error)
                self.assertNotIn('UNIQUE', error)
        future = self.work / 'meta-future'
        write(future / 'layer.yaml', yaml_text(dict(base, schema=9173)))
        self.assertNotIn('9173', layers.read_layer_yaml(future)[1])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_a_symlink_loop_is_just_two_skipped_links(self):
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'core\n'}), ('acme', 'org', {'skills/loop/SKILL.md': 'x\n'}))
        acme = layerset.get('acme').root
        os.symlink(acme / 'skills', acme / 'skills' / 'loop' / 'again', target_is_directory=True)
        os.symlink(acme, acme / 'skills' / 'up', target_is_directory=True)
        result = {}
        worker = threading.Thread(target=lambda: result.update(findings=self.findings(layerset), files=layerset.skill_files('loop')), daemon=True)
        worker.start()
        worker.join(60)
        self.assertFalse(worker.is_alive(), 'checks did not finish on a symlink loop')
        self.assertEqual(sorted(result['files']), ['SKILL.md'])
        self.assertEqual(len(by_code(result['findings'], 'symlink-in-layer')), 2)

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_an_identity_file_that_is_a_link_or_lies_under_one_is_rejected(self):
        layerset = self.build(('core', 'core', {}), ('acme', 'org', {'real/identity.yaml': yaml_text({'schema': 1, 'layer': 'acme', 'terms': []})}))
        acme = layerset.get('acme')
        self.link(acme.root / 'real' / 'identity.yaml', acme.root / 'leakguard' / 'identity.yaml')
        with self.assertRaises(layers.LayerError) as caught:
            acme.identity_file()
        self.assertEqual(caught.exception.code, 'symlink-in-layer')
        (acme.root / 'leakguard' / 'identity.yaml').unlink()
        self.link(acme.root / 'real', acme.root / 'leakguard' / 'dir')
        meta_dir = acme.root / 'layer.yaml'
        meta_dir.write_text(meta_dir.read_text(encoding='utf-8') + 'identity: leakguard/dir/identity.yaml\n', encoding='utf-8')
        acme = layers.resolve_layers(self.project('p2')).get('acme')
        with self.assertRaises(layers.LayerError) as under:
            acme.identity_file()
        self.assertEqual(under.exception.code, 'symlink-in-layer')

    def test_junctions_and_reparse_points_are_detected_like_symlinks(self):
        regular = os.lstat(self.work)
        fake = lambda attributes: types.SimpleNamespace(st_mode=regular.st_mode, st_file_attributes=attributes)   # noqa: E731
        with mock.patch.object(os, 'lstat', return_value=fake(0x400)):                     # FILE_ATTRIBUTE_REPARSE_POINT
            self.assertTrue(layers.is_link(self.work / 'x'))
        with mock.patch.object(os, 'lstat', return_value=fake(0x10)):                      # a plain directory attribute
            self.assertFalse(layers.is_link(self.work / 'x'))
        with mock.patch.object(os, 'lstat', return_value=fake(0)), \
                mock.patch.object(os.path, 'isjunction', create=True, return_value=True):    # Python 3.12+ junction check
            self.assertTrue(layers.is_link(self.work / 'x'))
        with mock.patch.object(os, 'lstat', side_effect=OSError):
            self.assertFalse(layers.is_link(self.work / 'missing'))
        # a reparse point is skipped by the walk exactly like a symlink
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'core\n', 'skills/point/SKILL.md': 'x\n'}))
        point = layerset.core.root / 'skills' / 'point'
        real_is_link = layers.is_link
        with mock.patch.object(layers, 'is_link', lambda path: Path(path) == point or real_is_link(path)):
            self.assertEqual(layerset.skill_names(), ['feature'])
            self.assertEqual(layers.layer_links(layerset.core), ['skills/point'])

    @unittest.skipUnless(sys.platform == 'win32', 'junction tylko na Windows')
    def test_a_real_junction_is_skipped_and_reported(self):
        import subprocess
        layerset = self.build(('core', 'core', {'real/SKILL.md': 'x\n'}))
        core = layerset.core.root
        (core / 'skills').mkdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(core / 'skills' / 'junc'), str(core / 'real')], check=True, capture_output=True)
        self.assertEqual(layerset.skill_names(), [])
        self.assertEqual(layers.layer_links(layerset.core), ['skills/junc'])
        self.assertIn('symlink-in-layer', codes(self.findings(layerset)))

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_links_made_by_the_generator_in_projects_and_the_composite_cache_are_not_layer_violations(self):
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'core\n'}))
        project = self.project('Repos/proj-with-links')
        write(project / '.github' / 'skills' / 'placeholder', 'x\n')
        self.link(layerset.core.root / 'skills' / 'feature', project / '.github' / 'skills' / 'feature')
        cache = self.work / 'composed' / 'feature' / 'hash1'
        cache.mkdir(parents=True)
        self.link(layerset.core.root / 'skills' / 'feature' / 'SKILL.md', cache / 'SKILL.md')
        findings = layers.check_layers(layers.resolve_layers(project), hooks=False, cache_dir=self.work / 'composed')
        self.assertEqual(by_code(findings, 'symlink-in-layer'), [])
        self.assertEqual(layers.layer_links(layerset.core), [])

    @unittest.skipUnless(CAN_SYMLINK, 'this host cannot create symlinks')
    def test_org_cannot_hide_an_override_behind_or_through_a_link(self):
        # a real file next to links still counts; only the linked entries are ignored
        layerset = self.linked_org()
        root = layerset.get('acme').root
        write(root / 'skills' / 'feature' / 'SKILL.md', 'real override\n')
        self.link(root / 'bbb.md', root / 'skills' / 'feature' / 'ref.md')
        found = self.consent_findings(layerset)
        self.assertEqual([item['message'].split()[0] for item in found], ['skills/feature/SKILL.md'])

    # ---- M2: letter case cannot hide an override

    def test_letter_case_variants_of_guarded_core_files_are_overrides(self):
        core = {'skills/feature/SKILL.md': 'core\n', 'agents/reviewer.md': 'core\n', 'workflow/trackers/jira.yaml': 'a: 1\n',
                'hooks/x.py': 'core\n'}
        variants = {
            'file name': ('skills/feature/skill.md', 'skills/feature/SKILL.md'),
            'skill directory': ('skills/Feature/SKILL.md', 'skills/feature/SKILL.md'),
            'role': ('agents/Reviewer.md', 'agents/reviewer.md'),
            'tracker': ('workflow/trackers/Jira.yaml', 'workflow/trackers/jira.yaml'),
            'hook': ('hooks/X.py', 'hooks/x.py'),
        }
        for label, (rel, core_rel) in variants.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', {rel: 'org\n'}))
                found = self.consent_findings(layerset)
                self.assertEqual([item['layer'] for item in found], ['acme'], found)
                allowed = self.build(('core', 'core', core), ('acme', 'org', {rel: 'org\n'}), allow_override=[rel])
                self.assertEqual(self.consent_findings(allowed), [], 'a consent naming the org path is accepted')
                by_core = self.build(('core', 'core', core), ('acme', 'org', {rel: 'org\n'}), allow_override=[core_rel])
                self.assertEqual(self.consent_findings(by_core), [], 'a consent naming the core path is accepted')
        # identical bytes in another spelling are still an identical copy
        copy = self.build(('core', 'core', core), ('acme', 'org', {'agents/Reviewer.md': 'core\n'}))
        self.assertEqual(self.consent_findings(copy), [])
        personal = self.build(('core', 'core', core), ('me', 'personal', {'agents/Reviewer.md': 'x\n'}))
        self.assertEqual(self.consent_findings(personal), [])

    def test_names_that_differ_only_by_case_are_a_collision(self):
        core = {'skills/feature/SKILL.md': 'core\n', 'agents/reviewer.md': 'core\n', 'workflow/trackers/jira.yaml': 'a\n',
                'instructions/java.md': 'c\n'}
        for label, rel in {'skill name': 'skills/Feature/SKILL.md', 'role': 'agents/Reviewer.md', 'tracker': 'workflow/trackers/Jira.yaml',
                           'instruction': 'instructions/Java.md', 'file of a skill': 'skills/feature/skill.md'}.items():
            with self.subTest(label):
                layerset = self.build(('core', 'core', core), ('acme', 'org', {rel: 'org\n'}))
                found = by_code(self.findings(layerset), 'case-collision')
                self.assertTrue(found, label)
                self.assertTrue(all(item['severity'] == 'error' for item in found))
                self.assertEqual(layers.findings_status(self.findings(layerset)), 'FAIL')
                self.assertTrue(by_code(self.findings(layerset, trust=False), 'case-collision'), 'not part of the trust switch')
        same = self.build(('core', 'core', core), ('acme', 'org', {'skills/feature/SKILL.md': 'org\n'}))
        self.assertEqual(by_code(self.findings(same), 'case-collision'), [])

    @unittest.skipUnless(CASE_SENSITIVE_FS, 'this file system does not distinguish letter case inside one layer')
    def test_case_collisions_inside_one_layer(self):
        layerset = self.build(('core', 'core', {'skills/feature/SKILL.md': 'a\n', 'skills/Feature/SKILL.md': 'b\n',
                                                  'skills/solo/SKILL.md': 'c\n', 'skills/solo/skill.md': 'd\n'}))
        found = by_code(self.findings(layerset), 'case-collision')
        self.assertGreaterEqual(len(found), 2)
        self.assertEqual({item['severity'] for item in found}, {'error'})

    # ---- n1: consent is per full key

    def test_consent_for_one_check_does_not_cover_a_check_whose_id_extends_it(self):
        checks = lambda a, ab: {'workflow/defaults.yaml': yaml_text({'validation': {'checks': [   # noqa: E731
            {'id': 'a', 'command': a}, {'id': 'a.b', 'command': ab}]}})}
        none = self.build(('core', 'core', checks('1', '1')), ('acme', 'org', checks('2', '2')))
        self.assertEqual(sorted(item['message'].split()[0] for item in self.consent_findings(none)),
                         ['validation.checks:a', 'validation.checks:a.b'])         # ids with dots are never collapsed
        layerset = self.build(('core', 'core', checks('1', '1')), ('acme', 'org', checks('2', '2')), allow_override=['validation.checks:a'])
        found = self.consent_findings(layerset)
        self.assertEqual([item['message'].split()[0] for item in found], ['validation.checks:a.b'])
        both = self.build(('core', 'core', checks('1', '1')), ('acme', 'org', checks('2', '2')),
                          allow_override=['validation.checks:a', 'validation.checks:a.b'])
        self.assertEqual(self.consent_findings(both), [])

    def test_role_keys_of_codex_are_collapsed_to_the_outermost_changed_key(self):
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro', 'effort': 'low'}}})}
        org = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': {'mode': 'rw', 'net': True}}}})}
        found = self.consent_findings(self.build(('core', 'core', core), ('acme', 'org', org)))
        self.assertEqual([item['message'].split()[0] for item in found], ['codex.yaml:agents.reviewer.sandbox_mode'])
        glob = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.reviewer.sandbox_mode*'])
        self.assertEqual(self.consent_findings(glob), [])

    def test_consent_for_the_outer_role_key_covers_a_leaf_replaced_by_a_subtree(self):
        """One change, one key (round-2 contract): consent for the reported outer key covers the whole change."""
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro', 'effort': 'low'}}})}
        org = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': {'mode': 'rw', 'net': True}}}})}
        allowed = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.reviewer.sandbox_mode'])
        self.assertEqual(self.consent_findings(allowed), [])

    def test_consent_for_a_role_key_covers_descendants_only_at_a_dot_boundary(self):
        core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'ro', 'sandbox_modeX': 'a', 'sandbox': 'b'}}})}
        org = {'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_modeX': 'changed', 'sandbox': 'changed'}}})}
        layerset = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.reviewer.sandbox_mode'])
        self.assertEqual(sorted(item['message'].split()[0] for item in self.consent_findings(layerset)),
                         ['codex.yaml:agents.reviewer.sandbox', 'codex.yaml:agents.reviewer.sandbox_modeX'])
        ancestor = self.build(('core', 'core', core), ('acme', 'org', org), allow_override=['codex.yaml:agents.reviewer'])
        self.assertEqual(self.consent_findings(ancestor), [], 'consent for the role covers its keys')
        deep_core = {'codex.yaml': yaml_text({'agents': {'reviewer': {'tools': {'a': {'b': 1}}}}})}
        deep_org = {'codex.yaml': yaml_text({'agents': {'reviewer': {'tools': {'a': {'b': 2}}}}})}
        for allow, expected in (('codex.yaml:agents.reviewer.tools', 0), ('codex.yaml:agents.reviewer.tools.a', 0),
                                ('codex.yaml:agents.reviewer.tools.a.b', 0), ('codex.yaml:agents.reviewer.tools.x', 1)):
            with self.subTest(allow=allow):          # any ancestor level covers the leaf, a sibling does not
                layerset = self.build(('core', 'core', deep_core), ('acme', 'org', deep_org), allow_override=[allow])
                self.assertEqual(len(self.consent_findings(layerset)), expected)

    def test_trust_checks_can_be_switched_off_for_other_callers(self):
        core, org, _ = self.ELEMENTS['validation check']
        layerset = self.build(('core', 'core', core), ('acme', 'org', org))
        self.assertEqual(by_code(self.findings(layerset, trust=False), 'override-without-consent'), [])

    def test_override_allowed_matches_globs_with_case(self):
        config = layers.parse_machine_config({'allow_override': ['skills/*/SKILL.md', 'validation.checks:u*']}, None)
        self.assertTrue(layers.override_allowed(config, 'skills/feature/SKILL.md'))
        self.assertTrue(layers.override_allowed(config, 'validation.checks:unit'))
        self.assertFalse(layers.override_allowed(config, 'validation.checks:lint'))
        self.assertFalse(layers.override_allowed(config, 'Skills/feature/SKILL.md'))
        self.assertFalse(layers.override_allowed(layers.parse_machine_config({}, None), 'skills/feature/SKILL.md'))

    def test_owner_of_an_overridden_element_is_the_overriding_layer(self):
        core, org, _ = self.ELEMENTS['validation check']
        merged = self.build(('core', 'core', core), ('acme', 'org', org)).merged_file('workflow/defaults.yaml')
        self.assertEqual(merged.owners['validation.checks:unit'], 'acme')
        self.assertEqual(merged.overrides, [('validation.checks:unit', 'core', 'acme')])


if __name__ == '__main__':
    unittest.main()
