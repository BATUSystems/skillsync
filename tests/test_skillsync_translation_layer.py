"""Translation layers (CORE-1130-I18N, I18N-3): `layer.yaml` keys, the trust model, `instructions/_base.md`, conformance with the core, the lock file.

A translation layer is an `org` layer with `translation_of: core` and a `language`. It replaces skill and role texts of the core without
`allow_override`, may replace `instructions/_base.md`, and is held to the structure and the version of the core files by `layers check`.
"""
import sys
import unittest
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer, require_os_symlinks, write, yaml_text  # noqa: E402
from skillsync_workflow import layers, translations  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

def yaml_load(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


EN = {'translation_of': 'core', 'language': 'en'}
CORE_SKILL = '---\nname: feature\ndescription: Opis\n---\n# Funkcja\n\n## Krok\n\ntekst\n'
EN_SKILL = '---\nname: feature\ndescription: Description\n---\n# Feature\n\n## Step\n\ntext\n'
CORE_FILES = {
    'skills/feature/SKILL.md': CORE_SKILL,
    'skills/feature/references/plan.md': '# Plan\n\ntekst\n',
    'skills/feature/assets/logo.txt': 'binary-ish\n',
    'agents/reviewer.md': '---\nname: reviewer\n---\n# Recenzent\n',
    'instructions/_base.md': '# Baza\n\ntekst\n',
    'instructions/java.md': '# Java\n',
    'hooks/pre.sh': 'echo\n',
    'workflow/trackers/jira.yaml': 'a: 1\n',
    'workflow/schema.json': '{}\n',
}
EN_FILES = {
    'skills/feature/SKILL.md': EN_SKILL,
    'skills/feature/references/plan.md': '# Plan\n\ntext\n',
    'agents/reviewer.md': '---\nname: reviewer\n---\n# Reviewer\n',
    'instructions/_base.md': '# Base\n\ntext\n',
}


class TranslationCase(LayersCase):
    sets = 0

    def build(self, core=None, translation=None, *, extra=None, config=None, lock=True, kind='org', other=None, consent=True):
        """core, translation: {rel: text}; the translation layer is `core-en` (org, translation_of core); a lock is written when `lock`."""
        TranslationCase.sets += 1
        base = child(self.work, f'tset{TranslationCase.sets}')
        specs = [('core', 'core', make_layer(child(base, 'core'), 'core', 'core', CORE_FILES if core is None else core, version=generator_version()))]
        meta = dict(EN if extra is None else extra)
        en_dir = make_layer(child(base, 'core-en'), 'core-en', kind, EN_FILES if translation is None else translation, extra_meta=meta)
        specs.append(('core-en', kind, en_dir, {'translation': True} if consent and kind == 'org' else {}))
        for layer_id, layer_kind, files, *more in (other or []):   # more: [entry keys of the machine config, layer.yaml keys]
            entry_keys, yaml_keys = (list(more) + [{}, {}])[:2]
            specs.append((layer_id, layer_kind, make_layer(child(base, layer_id), layer_id, layer_kind, files, extra_meta=yaml_keys), entry_keys))
        self.write_config(specs, [(['**'], [item[0] for item in specs])], **(config or {}))
        layerset = layers.resolve_layers(self.project(f'tproj{TranslationCase.sets}'))
        if lock:
            self.update_lock(layerset)
        return layerset

    def update_lock(self, layerset):
        layer = next((item for item in layerset.layers if item.is_translation), None)
        return translations.update_lock(layer, layerset.core) if layer is not None else None

    def findings(self, layerset, **kwargs):
        kwargs.setdefault('hooks', False)
        return layers.check_layers(layerset, **kwargs)

    def codes(self, layerset, **kwargs):
        return sorted(item['code'] for item in self.findings(layerset, **kwargs))

    def translation_layer(self, layerset):
        return next(item for item in layerset.layers if item.is_translation)


class LayerYamlTest(unittest.TestCase):
    def parse(self, **extra):
        return layers.parse_layer_yaml({'schema': 1, 'id': 'core-en', 'kind': 'org', **extra})

    def test_a_translation_layer_declares_the_core_and_a_language(self):
        meta = self.parse(**EN)
        self.assertEqual((meta['translation_of'], meta['language']), ('core', 'en'))

    def test_the_keys_are_optional(self):
        meta = self.parse()
        self.assertEqual((meta['translation_of'], meta['language']), (None, None))

    def test_only_the_core_can_be_translated_and_only_by_an_org_layer(self):
        with self.assertRaises(layers.LayerError):
            self.parse(translation_of='other')
        with self.assertRaises(layers.LayerError):
            layers.parse_layer_yaml({'schema': 1, 'id': 'me', 'kind': 'personal', 'translation_of': 'core'})
        with self.assertRaises(layers.LayerError):
            layers.parse_layer_yaml({'schema': 1, 'id': 'core', 'kind': 'core', 'translation_of': 'core'})

    def test_a_language_is_a_lower_case_code(self):
        for good in ('en', 'pl', 'pt-br'):
            self.assertEqual(self.parse(language=good)['language'], good)
        for bad in ('EN', 'english', '', 5, 'e'):
            with self.subTest(bad=bad), self.assertRaises(layers.LayerError):
                self.parse(language=bad)


class TrustAndBaseTest(TranslationCase):
    def test_a_translation_replaces_skill_and_role_texts_without_allow_override(self):
        layerset = self.build()
        self.assertTrue(self.translation_layer(layerset).is_translation)
        self.assertEqual([item for item in self.findings(layerset) if item['code'] == 'override-without-consent'], [])
        owner, path = layerset.resolve('skills/feature/SKILL.md')
        self.assertEqual(owner.id, 'core-en')
        self.assertEqual(path.read_text(encoding='utf-8'), EN_SKILL)
        self.assertEqual(layerset.resolve('agents/reviewer.md')[0].id, 'core-en')

    def test_a_plain_org_layer_with_the_same_files_still_needs_consent(self):
        layerset = self.build(extra={}, lock=False)
        self.assertFalse(layerset.layers[1].is_translation)
        found = [item for item in self.findings(layerset) if item['code'] == 'override-without-consent']
        self.assertTrue(found)

    def test_a_translation_does_not_gain_the_trackers_or_the_hooks_of_the_core(self):
        files = {**EN_FILES, 'workflow/trackers/jira.yaml': 'a: 2\n', 'hooks/pre.sh': 'echo changed\n'}
        layerset = self.build(translation=files, lock=False)
        refused = {item['message'].split()[0] for item in self.findings(layerset) if item['code'] == 'override-without-consent'}
        self.assertEqual(refused, {'workflow/trackers/jira.yaml', 'hooks/pre.sh'})   # both stay guarded, hooks are also core-only
        self.assertIn('core-only-ignored', self.codes(layerset))
        self.assertEqual(layerset.resolve('hooks/pre.sh')[0].id, 'core')

    def test_a_translation_may_replace_the_base_instructions_and_nothing_else_core_only(self):
        files = {**EN_FILES, 'workflow/schema.json': '{"x": 1}\n'}
        layerset = self.build(translation=files, lock=False)
        owner, path = layerset.resolve('instructions/_base.md')
        self.assertEqual((owner.id, path.read_text(encoding='utf-8')), ('core-en', '# Base\n\ntext\n'))
        self.assertEqual(layerset.resolve('workflow/schema.json')[0].id, 'core')
        ignored = [item['message'] for item in self.findings(layerset) if item['code'] == 'core-only-ignored']
        self.assertEqual(len(ignored), 1)
        self.assertIn('workflow/schema.json', ignored[0])

    def test_a_plain_org_layer_still_cannot_replace_the_base_instructions(self):
        layerset = self.build(extra={}, lock=False)
        self.assertEqual(layerset.resolve('instructions/_base.md')[0].id, 'core')
        self.assertTrue([item for item in self.findings(layerset) if item['code'] == 'core-only-ignored'])

    def test_the_translation_layer_may_sit_in_a_subdirectory_of_the_core_checkout(self):
        checkout = child(self.work, 'checkout')
        make_layer(child(checkout, 'library'), 'core', 'core', CORE_FILES, version=generator_version())
        make_layer(descend(checkout, 'layers/core-en'), 'core-en', 'org', EN_FILES, extra_meta=EN)
        self.write_config([('core', 'core', checkout, {'subdir': 'library'}), ('core-en', 'org', checkout, {'subdir': 'layers/core-en', 'translation': True})],
                          [(['**'], ['core', 'core-en'])])
        layerset = layers.resolve_layers(self.project('sub'))
        self.update_lock(layerset)
        self.assertEqual([item for item in self.findings(layerset) if item['severity'] in ('blocked', 'error')], [])
        self.assertEqual(layerset.resolve('skills/feature/SKILL.md')[0].id, 'core-en')
        self.assertEqual(layerset.resolve('skills/feature/assets/logo.txt')[0].id, 'core')   # an untranslated file is the core's


class ConformanceTest(TranslationCase):
    def errors(self, layerset):
        return sorted(item['code'] for item in self.findings(layerset) if item['severity'] in ('blocked', 'error'))

    def test_a_complete_translation_is_clean(self):
        self.assertEqual(self.errors(self.build()), [])

    def test_a_skill_that_is_not_translated_at_all_is_left_to_the_core(self):
        core = {**CORE_FILES, 'skills/other/SKILL.md': '---\nname: other\n---\n# Inny\n'}
        layerset = self.build(core=core)
        self.assertEqual(self.errors(layerset), [])
        self.assertEqual(layerset.resolve('skills/other/SKILL.md')[0].id, 'core')

    def test_a_half_translated_skill_is_incomplete(self):
        translation = {key: value for key, value in EN_FILES.items() if key != 'skills/feature/references/plan.md'}
        layerset = self.build(translation=translation)
        self.assertEqual(self.errors(layerset), ['translation-incomplete'])

    def test_a_file_without_a_core_counterpart_is_an_orphan(self):
        layerset = self.build(translation={**EN_FILES, 'skills/new/SKILL.md': '---\nname: new\n---\n# New\n', 'agents/ghost.md': '# Ghost\n'})
        found = [item['message'] for item in self.findings(layerset) if item['code'] == 'translation-orphan']
        self.assertEqual(len(found), 2)

    def test_the_headings_must_follow_the_source(self):
        layerset = self.build(translation={**EN_FILES, 'skills/feature/references/plan.md': '# Plan\n\n## Extra\n'})
        self.assertEqual(self.errors(layerset), ['translation-structure'])

    def test_headings_inside_code_fences_and_lines_that_are_not_headings_do_not_count(self):
        core = {**CORE_FILES, 'skills/feature/references/plan.md': '# Plan\n\n```sh\n# comment\n```\n\n#hashtag\n'}
        translation = {**EN_FILES, 'skills/feature/references/plan.md': '# Plan\n\n~~~\n# komentarz\n# drugi\n~~~\n'}
        self.assertEqual(self.errors(self.build(core=core, translation=translation)), [])

    def test_the_frontmatter_keys_and_name_must_match(self):
        for label, text in (('name', EN_SKILL.replace('name: feature', 'name: funkcja')),
                            ('key', EN_SKILL.replace('description: Description\n', '')),
                            ('extra key', EN_SKILL.replace('---\n#', 'extra: 1\n---\n#', 1))):
            with self.subTest(label):
                layerset = self.build(translation={**EN_FILES, 'skills/feature/SKILL.md': text})
                self.assertEqual(self.errors(layerset), ['translation-structure'])

    def test_the_translated_base_stays_within_the_budget_of_the_core_base(self):
        layerset = self.build(translation={**EN_FILES, 'instructions/_base.md': '# Base\n\n' + 'x' * 9000 + '\n'})
        self.assertEqual(self.errors(layerset), ['translation-base-too-large'])
        defaults = {**CORE_FILES, 'workflow/defaults.yaml': yaml_text({'context': {'base_max_bytes': 10000}})}
        layerset = self.build(core=defaults, translation={**EN_FILES, 'instructions/_base.md': '# Base\n\n' + 'x' * 9000 + '\n'})
        self.assertEqual(self.errors(layerset), [])

    def test_a_translation_needs_a_language(self):
        layerset = self.build(extra={'translation_of': 'core'})
        self.assertEqual(self.errors(layerset), ['translation-language-missing'])

    def test_the_lock_is_required_and_a_changed_source_makes_the_translation_stale(self):
        layerset = self.build(lock=False)
        self.assertEqual(self.errors(layerset), ['translation-lock-missing'])
        self.update_lock(layerset)
        self.assertEqual(self.errors(layerset), [])
        core_file = layerset.core.root / 'skills/feature/references/plan.md'
        core_file.write_text('# Plan\n\nzmieniony tekst\n', encoding='utf-8')
        found = [item['message'] for item in self.findings(layerset) if item['code'] == 'translation-stale']
        self.assertEqual(len(found), 1)
        self.assertIn('skills/feature/references/plan.md', found[0])
        self.update_lock(layerset)
        self.assertEqual(self.errors(layerset), [])

    def test_a_stale_mark_with_a_reason_excuses_one_file(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        (layerset.core.root / 'agents/reviewer.md').write_text('---\nname: reviewer\n---\n# Recenzent v2\n', encoding='utf-8')
        self.assertEqual(self.errors(layerset), ['translation-stale'])
        lock_path = layer.root / translations.LOCK_FILE
        data = __import__('yaml').safe_load(lock_path.read_text(encoding='utf-8'))
        data['stale'] = {'agents/reviewer.md': 'translation due after the release'}
        lock_path.write_text(yaml_text(data), encoding='utf-8')
        self.assertEqual(self.errors(layerset), [])
        data['stale'] = {'agents/reviewer.md': '  '}
        lock_path.write_text(yaml_text(data), encoding='utf-8')
        self.assertEqual(self.errors(layerset), ['translation-lock-invalid'])

    def test_a_new_translated_file_has_no_entry_until_the_lock_is_updated(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        write(descend(layer.root, 'instructions/java.md'), '# Java\n')
        self.assertEqual(self.errors(layerset), ['translation-stale'])
        self.assertEqual(self.update_lock(layerset)[1], ['instructions/java.md'])
        self.assertEqual(self.errors(layerset), [])

    def test_the_lock_does_not_list_files_that_are_not_translated(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        descend(layer.root, 'agents/reviewer.md').unlink()
        self.assertEqual(self.errors(layerset), ['translation-lock-extra'])

    def test_line_ends_do_not_change_the_hash(self):
        layerset = self.build()
        core_file = layerset.core.root / 'skills/feature/SKILL.md'
        core_file.write_bytes(core_file.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual(self.errors(layerset), [])

    def test_the_lock_is_written_the_same_way_every_time(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        first = (layer.root / translations.LOCK_FILE).read_bytes()
        path, changed = self.update_lock(layerset)
        self.assertEqual((path.read_bytes(), changed), (first, []))

    def test_conformance_runs_with_trust_switched_off_and_does_not_touch_other_layers(self):
        layerset = self.build(lock=False, other=[('me', 'personal', {'skills/feature/SKILL.md': 'mine\n'})])
        self.assertIn('translation-lock-missing', self.codes(layerset, trust=False))


class ConsentTest(TranslationCase):
    """The declaration in the layer's own `layer.yaml` is only a claim; the consent is `translation: true` in the machine config."""

    def test_a_layer_that_declares_itself_without_the_consent_gets_no_exemption(self):
        layerset = self.build(consent=False, lock=False)
        self.assertFalse(layerset.layers[1].is_translation)
        found = self.findings(layerset)
        self.assertTrue([item for item in found if item['code'] == 'override-without-consent'])
        self.assertIn('translation-not-consented', {item['code'] for item in found})
        self.assertEqual(layerset.resolve('instructions/_base.md')[0].id, 'core')   # the system prompt stays the core's
        self.assertEqual(layers.findings_status(found), 'FAIL')

    def test_the_consent_without_the_declaration_is_reported_and_gives_nothing(self):
        layerset = self.build(extra={}, lock=False)
        self.assertFalse(layerset.layers[1].is_translation)
        found = self.codes(layerset)
        self.assertIn('translation-not-declared', found)
        self.assertIn('override-without-consent', found)

    def test_the_consent_is_a_boolean_and_only_for_an_org_layer(self):
        for entry, label in (({'translation': 'yes'}, 'not boolean'), ({'translation': True, 'kind': 'personal'}, 'personal')):
            with self.subTest(label):
                path = make_layer(child(self.work, f'bad-{label.replace(" ", "-")}'), 'x', entry.get('kind', 'org'))
                self.write_config([('x', entry.get('kind', 'org'), path, {k: v for k, v in entry.items() if k == 'translation'})], [(['**'], ['x'])])
                with self.assertRaises(layers.LayerError):
                    layers.load_machine_config()

    def test_a_layer_given_with_the_layer_flag_keeps_the_consent_of_its_config_entry(self):
        layerset = self.build()
        override = layers.resolve_layers(layerset.project, layers=[f'core-en={layerset.layers[1].path}'])
        self.assertTrue(next(item for item in override.layers if item.id == 'core-en').translation)

    def test_layers_add_with_the_flag_writes_the_consent(self):
        self.set_git_identity('jan@acme.example')
        bare, _ = self.source_repo('en', 'core-en', 'org', EN_FILES, extra_meta=EN)
        code, report = self.cli_json('layers', 'add', 'core-en', '--kind', 'org', '--source', str(bare), '--translation')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        entry = next(item for item in yaml_load(self.config_file)['layers'] if item['id'] == 'core-en')
        self.assertIs(entry['translation'], True)
        code, report = self.cli_json('layers', 'add', 'me', '--kind', 'personal', '--source', str(bare), '--translation')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)


class FilesAndFrontmatterTest(TranslationCase):
    def errors(self, layerset):
        return sorted(item['code'] for item in self.findings(layerset) if item['severity'] in ('blocked', 'error'))

    def test_only_markdown_is_exempt_scripts_and_assets_need_consent(self):
        core = {**CORE_FILES, 'skills/feature/scripts/run.sh': 'echo core\n'}
        translation = {**EN_FILES, 'skills/feature/scripts/run.sh': 'echo changed\n', 'skills/feature/assets/logo.txt': 'other\n'}
        layerset = self.build(core=core, translation=translation)
        refused = {item['message'].split()[0] for item in self.findings(layerset) if item['code'] == 'override-without-consent'}
        self.assertEqual(refused, {'skills/feature/scripts/run.sh', 'skills/feature/assets/logo.txt'})

    def test_a_new_file_of_a_translated_kind_is_an_orphan_whatever_its_extension(self):
        layerset = self.build(translation={**EN_FILES, 'skills/feature/scripts/new.sh': 'echo\n'})
        self.assertIn('translation-orphan', self.errors(layerset))

    def test_behaviour_in_the_frontmatter_cannot_differ_but_words_can(self):
        core = {**CORE_FILES, 'agents/reviewer.md': '---\nname: reviewer\ndescription: Opis\ntools: Read\n---\n# Recenzent\n'}
        same = {**EN_FILES, 'agents/reviewer.md': '---\nname: reviewer\ndescription: Description\ntools: Read\n---\n# Reviewer\n'}
        self.assertEqual(self.errors(self.build(core=core, translation=same)), [])
        wider = {**EN_FILES, 'agents/reviewer.md': '---\nname: reviewer\ndescription: Description\ntools: Bash, Write\n---\n# Reviewer\n'}
        self.assertEqual(self.errors(self.build(core=core, translation=wider)), ['translation-structure'])

    def test_the_letter_case_of_a_path_cannot_hide_a_core_file(self):
        # Only the differently cased path is written: on a case-insensitive file system (Windows, macOS) a second `skills/` directory next to the
        # `Skills/` one cannot exist, so files of both spellings would land in one directory and the case difference would vanish from the test data.
        # With a single file the directory keeps the spelling it was created with on every file system, and the check must see it.
        layerset = self.build(translation={'Skills/feature/SKILL.md': EN_SKILL}, lock=False)
        found = [item['message'] for item in self.findings(layerset) if item['code'] == 'translation-case-mismatch']
        self.assertEqual(len(found), 1, found)
        self.assertIn('Skills/feature/SKILL.md', found[0])
        self.assertIn('skills/feature/SKILL.md', found[0])

    def test_a_translation_cannot_change_a_role_key_or_a_check_of_the_core(self):
        core = {**CORE_FILES, 'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'read-only'}}}),
                'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'c'}]}})}
        translation = {**EN_FILES, 'codex.yaml': yaml_text({'agents': {'reviewer': {'sandbox_mode': 'workspace-write'}}}),
                       'workflow/defaults.yaml': yaml_text({'validation': {'checks': [{'id': 'unit', 'command': 'o'}]}})}
        layerset = self.build(core=core, translation=translation)
        refused = [item['message'] for item in self.findings(layerset) if item['code'] == 'override-without-consent']
        self.assertEqual(len(refused), 2, refused)

    def test_the_instruction_collision_noise_is_not_reported_for_a_translation(self):
        layerset = self.build(translation={**EN_FILES, 'instructions/java.md': '# Java\n'})
        self.assertNotIn('instruction-collision', self.codes(layerset))

    def test_the_last_of_two_translation_layers_decides_file_by_file(self):
        pt = {'skills/feature/SKILL.md': EN_SKILL.replace('Feature', 'Funkcja pt')}
        layerset = self.build(other=[('core-pt', 'org', pt, {'translation': True}, {'translation_of': 'core', 'language': 'pt'})], lock=False)
        self.assertEqual(translations.skill_language(layerset), 'pt')
        self.assertEqual(layerset.resolve('skills/feature/SKILL.md')[0].id, 'core-pt')
        self.assertEqual(layerset.resolve('agents/reviewer.md')[0].id, 'core-en')


class LockLifecycleTest(TranslationCase):
    def test_update_drops_the_stale_marks_and_a_mark_for_an_untranslated_file_is_extra(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        lock_path = layer.root / translations.LOCK_FILE
        data = yaml_load(lock_path)
        data['stale'] = {'agents/reviewer.md': 'later', 'agents/ghost.md': 'never translated'}
        lock_path.write_text(yaml_text(data), encoding='utf-8')
        self.assertEqual(sorted(item['code'] for item in self.findings(layerset) if item['severity'] == 'error'), ['translation-lock-extra'])
        self.update_lock(layerset)
        self.assertNotIn('stale', yaml_load(lock_path))

    @require_os_symlinks
    def test_a_lock_that_is_a_symlink_is_neither_read_nor_written(self):
        layerset = self.build()
        layer = self.translation_layer(layerset)
        victim = child(self.work, 'victim.txt')
        victim.write_text('mine\n', encoding='utf-8')
        lock_path = layer.root / translations.LOCK_FILE
        lock_path.unlink()
        lock_path.symlink_to(victim)
        self.assertIn('translation-lock-invalid', self.codes(layerset))
        with self.assertRaises(layers.LayerError):
            translations.update_lock(layer, layerset.core)
        self.assertEqual(victim.read_text(encoding='utf-8'), 'mine\n')

    def test_a_read_only_layer_stops_the_command_before_any_lock_is_written(self):
        layerset = self.build(lock=False, other=[('core-pt', 'org', {}, {'translation': True, 'writable': False}, {'translation_of': 'core', 'language': 'pt'})])
        report = layers.layers_translations(str(layerset.project), update=True)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertFalse((self.translation_layer(layerset).root / translations.LOCK_FILE).exists())


class LanguageFindingsTest(TranslationCase):
    def test_a_manifest_in_the_language_of_the_layers_is_quiet(self):
        with_translation = self.build()
        self.assertEqual(translations.skill_language(with_translation), 'en')
        self.assertEqual(translations.language_findings(with_translation, 'en'), [])
        core_only = self.build(extra={}, lock=False)
        self.assertEqual(translations.skill_language(core_only), 'pl')
        self.assertEqual(translations.language_findings(core_only, 'pl'), [])

    def test_a_mismatch_is_a_warning_that_names_both_languages(self):
        for layerset, manifest in ((self.build(), 'pl'), (self.build(extra={}, lock=False), 'en')):
            found = translations.language_findings(layerset, manifest)
            self.assertEqual([(item['code'], item['severity']) for item in found], [('language-mismatch', 'warning')])
            self.assertIn(manifest, found[0]['message'])

    def test_the_last_translation_layer_decides(self):
        layerset = self.build(other=[('core-pt', 'org', {})])
        self.assertEqual(translations.skill_language(layerset), 'en')


class CommandTest(TranslationCase):
    def test_without_a_translation_layer_the_command_has_nothing_to_do(self):
        make = make_layer(child(self.work, 'solo'), 'core', 'core', CORE_FILES, version=generator_version())
        self.write_config([('core', 'core', make)], [(['**'], ['core'])])
        report = layers.layers_translations(str(self.project('none')))
        self.assertEqual(report['status'], 'NOT_RUN')

    def test_update_writes_the_lock_and_the_report_is_green(self):
        layerset = self.build(lock=False)
        project = str(layerset.project)
        self.assertEqual(layers.layers_translations(project)['status'], 'FAIL')
        report = layers.layers_translations(project, update=True)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['translations'][0]['layer'], 'core-en')
        self.assertEqual(report['translations'][0]['language'], 'en')
        self.assertEqual(len(report['translations'][0]['changed']), report['translations'][0]['files'])
        self.assertEqual(layers.layers_translations(project)['status'], 'PASS')

    def test_a_read_only_layer_is_not_updated(self):
        layerset = self.build(lock=False, config=None)
        self.write_config([('core', 'core', layerset.core.path), ('core-en', 'org', self.translation_layer(layerset).path, {'writable': False, 'translation': True})],
                          [(['**'], ['core', 'core-en'])])
        report = layers.layers_translations(str(layerset.project), update=True)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertFalse((self.translation_layer(layerset).root / translations.LOCK_FILE).exists())

    def test_layers_check_reports_a_stale_translation_as_a_failure(self):
        layerset = self.build()
        (layerset.core.root / 'skills/feature/references/plan.md').write_text('# Plan\n\nnowy\n', encoding='utf-8')
        report = layers.layers_check(str(layerset.project))
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('translation-stale', {item['code'] for item in report['findings']})


if __name__ == '__main__':
    unittest.main()
