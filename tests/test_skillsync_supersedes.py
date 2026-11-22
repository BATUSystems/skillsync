"""`supersedes` in `layer.yaml` (CORE-1140-SUP-TOOLS, SUP-1): per-entry validation, the findings of `layers check`, backward compatibility.

A layer declares that its tool replaces local files of a project (`matches`: path patterns). The field is advisory, so a bad entry is skipped with a
warning and never blocks the layer (an error in `layer.yaml` would stop `link` for every project of the layer). Nothing here runs a text from a layer.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer  # noqa: E402
from skillsync_workflow import layers  # noqa: E402
from tmp_paths import child  # noqa: E402

GOOD = {'id': 'ui-measure-local', 'since': '1.14.0', 'replacement': 'skill ui-verify, command measure', 'matches': ['docs/design/tools/measure*.cjs'],
        'reported_by': ['project-a'], 'parity': 'node measure.mjs --selftest', 'migration': 'switch the calls, delete the script in one commit'}


def parse(**extra):
    return layers.parse_layer_yaml({'schema': 1, 'id': 'acme', 'kind': 'org', **extra})


class ParsingTest(unittest.TestCase):
    def test_a_valid_entry_is_read_as_given(self):
        meta = parse(supersedes=[GOOD])
        self.assertEqual(meta['supersedes_errors'], [])
        self.assertEqual(meta['supersedes'], [{**GOOD, 'matches': list(GOOD['matches']), 'reported_by': list(GOOD['reported_by'])}])

    def test_without_the_key_there_is_nothing_and_no_error(self):
        meta = parse()
        self.assertEqual((meta['supersedes'], meta['supersedes_errors']), ([], []))

    def test_optional_texts_may_be_missing(self):
        meta = parse(supersedes=[{'id': 'x', 'since': '1.0.0', 'matches': ['a/*.sh']}])
        self.assertEqual(meta['supersedes_errors'], [])
        entry = meta['supersedes'][0]
        self.assertEqual((entry['replacement'], entry['parity'], entry['migration'], entry['reported_by']), (None, None, None, []))

    def test_a_bad_entry_is_skipped_and_reported_while_the_good_ones_stay(self):
        bad = [
            ('not a mapping', 'text'),
            ('id missing', {'since': '1.0.0', 'matches': ['a']}),
            ('id pattern', {'id': 'Bad Id', 'since': '1.0.0', 'matches': ['a']}),
            ('since not semver', {'id': 'b', 'since': 'soon', 'matches': ['a']}),
            ('since missing', {'id': 'c', 'matches': ['a']}),
            ('matches empty', {'id': 'd', 'since': '1.0.0', 'matches': []}),
            ('matches not a list', {'id': 'e', 'since': '1.0.0', 'matches': 'a/*'}),
            ('matches with a parent segment', {'id': 'f', 'since': '1.0.0', 'matches': ['a/../b']}),
            ('matches absolute', {'id': 'g', 'since': '1.0.0', 'matches': ['/etc/passwd']}),
            ('matches with a drive', {'id': 'h', 'since': '1.0.0', 'matches': ['C:/x']}),
            ('matches with a backslash root', {'id': 'i', 'since': '1.0.0', 'matches': ['\\\\server\\share']}),
            ('matches with a control character', {'id': 'j', 'since': '1.0.0', 'matches': ['a\x00b']}),
            ('too long text', {'id': 'k', 'since': '1.0.0', 'matches': ['a'], 'parity': 'x' * 2001}),
            ('text of a wrong type', {'id': 'l', 'since': '1.0.0', 'matches': ['a'], 'migration': ['list']}),
            ('reported_by wrong', {'id': 'm', 'since': '1.0.0', 'matches': ['a'], 'reported_by': 'one'}),
        ]
        for label, entry in bad:
            with self.subTest(label):
                meta = parse(supersedes=[GOOD, entry])
                self.assertEqual([item['id'] for item in meta['supersedes']], ['ui-measure-local'], label)
                self.assertEqual(len(meta['supersedes_errors']), 1, meta['supersedes_errors'])
                self.assertIn('supersedes[1]', meta['supersedes_errors'][0])

    def test_a_second_entry_with_the_same_id_is_skipped(self):
        meta = parse(supersedes=[GOOD, dict(GOOD, since='2.0.0')])
        self.assertEqual([item['since'] for item in meta['supersedes']], ['1.14.0'])
        self.assertEqual(len(meta['supersedes_errors']), 1)

    def test_a_key_that_is_not_a_list_is_skipped_as_a_whole(self):
        for value in ('text', {'id': 'x'}, 5):
            with self.subTest(value=value):
                meta = parse(supersedes=value)
                self.assertEqual((meta['supersedes'], len(meta['supersedes_errors'])), ([], 1))

    def test_limits(self):
        many = [{'id': f'tool-{index}', 'since': '1.0.0', 'matches': ['a']} for index in range(105)]
        meta = parse(supersedes=many)
        self.assertEqual((len(meta['supersedes']), len(meta['supersedes_errors'])), (100, 1))   # one message for the whole rest
        too_many_patterns = {'id': 'p', 'since': '1.0.0', 'matches': [f'a{index}' for index in range(21)]}
        self.assertEqual(len(parse(supersedes=[too_many_patterns])['supersedes_errors']), 1)
        long_pattern = {'id': 'q', 'since': '1.0.0', 'matches': ['a' * 201]}
        self.assertEqual(len(parse(supersedes=[long_pattern])['supersedes_errors']), 1)
        too_many_reporters = {'id': 'r', 'since': '1.0.0', 'matches': ['a'], 'reported_by': [f'p{index}' for index in range(21)]}
        self.assertEqual(len(parse(supersedes=[too_many_reporters])['supersedes_errors']), 1)

    def test_other_keys_are_unaffected(self):
        meta = parse(supersedes=[GOOD], version='1.2.3', core_min='1.0.0', requires=['core'])
        self.assertEqual((meta['version'], meta['core_min'], meta['requires']), ('1.2.3', '1.0.0', ['core']))


class LayerCheckTest(LayersCase):
    def build(self, supersedes, *, changelog='# Changelog\n', readme=True):
        base = child(self.work, 'sup-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', {'skills/a/SKILL.md': 'x\n'}, version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org', {'skills/b/SKILL.md': 'y\n'}, extra_meta={'supersedes': supersedes}, changelog=False)
        if changelog is not None:
            (org / 'CHANGELOG.md').write_text(changelog, encoding='utf-8')
        self.write_config([('core', 'core', core), ('org', 'org', org)], [(['**'], ['core', 'org'])])
        return layers.resolve_layers(self.project('sup-project'))

    def findings(self, layerset):
        return layers.check_layers(layerset, hooks=False, orphans=False)

    def codes(self, layerset):
        return sorted({item['code'] for item in self.findings(layerset)})

    def test_a_bad_entry_gives_a_warning_and_the_layer_stays_usable(self):
        layerset = self.build([GOOD, {'id': 'Bad Id'}], changelog=f'# Changelog\n\n- {GOOD["id"]}: replaced\n')
        org = layerset.layers[1]
        self.assertIsNotNone(org.meta)
        self.assertEqual([item['id'] for item in org.meta['supersedes']], ['ui-measure-local'])
        found = [item for item in self.findings(layerset) if item['code'] == 'supersedes-invalid']
        self.assertEqual([(item['severity'], item['layer']) for item in found], [('warning', 'org')])
        self.assertIn('supersedes[1]', found[0]['message'])
        self.assertEqual(layers.findings_status(self.findings(layerset)), 'PASS')   # a warning never fails or blocks

    def test_the_changelog_must_name_the_id(self):
        layerset = self.build([GOOD], changelog='# Changelog\n\nnothing about the tool\n')
        found = [item for item in self.findings(layerset) if item['code'] == 'supersedes-changelog-missing']
        self.assertEqual([item['severity'] for item in found], ['warning'])
        self.assertIn(GOOD['id'], found[0]['message'])
        named = self.build([GOOD], changelog=f'# Changelog\n\n- {GOOD["id"]}: superseded\n')
        self.assertNotIn('supersedes-changelog-missing', self.codes(named))

    def test_a_layer_without_a_changelog_file_is_reported_for_the_entry_too(self):
        layerset = self.build([GOOD], changelog=None)
        self.assertIn('supersedes-changelog-missing', self.codes(layerset))

    def test_a_layer_without_supersedes_has_no_new_finding(self):
        layerset = self.build([])
        self.assertEqual([code for code in self.codes(layerset) if code.startswith('supersedes')], [])

    def test_a_layer_with_only_the_old_keys_reads_as_before(self):
        base = child(self.work, 'old-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', {'skills/a/SKILL.md': 'x\n'}, version=generator_version())
        self.write_config([('core', 'core', core)], [(['**'], ['core'])])
        layerset = layers.resolve_layers(self.project('old-project'))
        self.assertEqual((layerset.layers[0].meta['supersedes'], layerset.layers[0].meta['supersedes_errors']), ([], []))


if __name__ == '__main__':
    unittest.main()
