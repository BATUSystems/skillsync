"""Tool index (CORE-1140-SUP-TOOLS, TOOLS-1): the schema of `tools/index.yaml`, the merge of the layers (`tools-index`) and its trust model.

The texts of a record are data from a layer (possibly org or personal): they are validated, cut and cleaned, never run. A broken index is skipped with a
warning and must not block anything else; a layer above the core cannot replace or hide a record of the core without `allow_override`.
"""
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer, write, yaml_text  # noqa: E402
from skillsync_workflow import layers, tools_index  # noqa: E402
from skillsync_workflow.pathfmt import posix  # noqa: E402
from tmp_paths import child, descend  # noqa: E402


def record(record_id='tool-a', **extra):
    base = {'id': record_id, 'path': f'skills/x/scripts/{record_id}.mjs', 'purpose': 'does a thing', 'topics': ['ui'], 'usage': f'node {record_id}.mjs <out>'}
    base.update(extra)
    return base


def index(*records, schema=1):
    return yaml_text({'schema': schema, 'tools': list(records)})


class SchemaTest(unittest.TestCase):
    def parse(self, *records, **kwargs):
        return tools_index.parse_index(index(*records, **kwargs))

    def test_a_valid_record_is_read_with_its_defaults(self):
        records, errors = self.parse(record())
        self.assertEqual(errors, [])
        self.assertEqual(records, [{'id': 'tool-a', 'path': 'skills/x/scripts/tool-a.mjs', 'purpose': 'does a thing', 'topics': ['ui'], 'files': [],
                                    'usage': 'node tool-a.mjs <out>', 'selftest': None, 'limits': [], 'status': 'active', 'supersedes': [], 'origin': None}])

    def test_all_the_fields_are_kept(self):
        full = record(files=['**/*.css'], selftest='node tool-a.mjs --selftest', limits=['no animations'], status='candidate', supersedes=['old-tool'],
                      origin='an earlier stage')
        records, errors = self.parse(full)
        self.assertEqual(errors, [])
        self.assertEqual({key: records[0][key] for key in full}, full)

    def test_version_is_read_as_an_alias_of_schema(self):
        body = yaml.safe_dump({'tools': [record('a')]}, sort_keys=False)
        self.assertEqual(len(tools_index.parse_index('version: 1\n' + body)[0]), 1)
        self.assertEqual(len(tools_index.parse_index('schema: 1\nversion: 1\n' + body)[0]), 1)
        self.assertEqual(tools_index.parse_index('schema: 1\nversion: 2\n' + body)[0], [])   # both given and different
        self.assertEqual(tools_index.parse_index('version: 2\n' + body)[0], [])
        self.assertEqual(tools_index.parse_index(body)[0], [])

    def test_unknown_fields_such_as_planned_replacement_are_ignored(self):
        records, errors = self.parse(record('a', planned_replacement='skill x', layer='core'))
        self.assertEqual((len(records), errors), (1, []))
        self.assertNotIn('planned_replacement', records[0])

    def test_limits_must_be_a_list_of_texts(self):
        self.assertEqual(self.parse(record('a', limits='one text'))[0], [])
        self.assertEqual(len(self.parse(record('a', limits=['one text']))[0]), 1)

    def test_empty_topics_are_allowed_but_the_key_is_required(self):
        self.assertEqual(self.parse(record(topics=[]))[1], [])
        without = record()
        del without['topics']
        self.assertEqual(len(self.parse(without)[1]), 1)

    def test_a_bad_record_is_skipped_and_the_others_stay(self):
        bad = {
            'not a mapping': 'text', 'id missing': {k: v for k, v in record().items() if k != 'id'}, 'id pattern': record('Bad Id'),
            'path missing': {k: v for k, v in record().items() if k != 'path'}, 'path with a parent': record(path='a/../b.sh'),
            'path absolute': record(path='/usr/bin/x'), 'path with a drive': record(path='C:/x.sh'), 'path with a backslash root': record(path='\\\\h\\s\\x'),
            'purpose missing': {k: v for k, v in record().items() if k != 'purpose'}, 'usage empty': record(usage='  '),
            'text too long': record(purpose='x' * 2001), 'status unknown': record(status='gone'), 'topics not a list': record(topics='ui'),
            'files with a parent': record(files=['../x']), 'supersedes with a bad id': record(supersedes=['Bad Id']),
            'limits not a list': record(limits='none'),
        }
        for label, item in bad.items():
            with self.subTest(label):
                records, errors = self.parse(record('good'), item)
                self.assertEqual([entry['id'] for entry in records], ['good'], label)
                self.assertEqual(len(errors), 1, errors)
                self.assertIn('tools[1]', errors[0])

    def test_a_duplicate_id_in_one_file_keeps_the_first(self):
        records, errors = self.parse(record('same', purpose='first'), record('same', purpose='second'))
        self.assertEqual([item['purpose'] for item in records], ['first'])
        self.assertEqual(len(errors), 1)

    def test_a_file_that_is_not_an_index_gives_no_record_and_never_raises(self):
        cases = {'not yaml': 'a: [unclosed\n', 'a list': '- a\n', 'no schema': 'tools: []\n', 'bool schema': 'schema: true\ntools: []\n',
                 'newer schema': index(record(), schema=2), 'schema zero': index(record(), schema=0), 'tools not a list': 'schema: 1\ntools: {a: 1}\n'}
        for label, text in cases.items():
            with self.subTest(label):
                records, errors = tools_index.parse_index(text)
                self.assertEqual((records, len(errors)), ([], 1), errors)
        self.assertEqual(tools_index.parse_index(''), ([], []))
        self.assertEqual(tools_index.parse_index('schema: 1\n'), ([], []))

    def test_the_limit_of_records(self):
        many = [record(f'tool-{number}') for number in range(tools_index.MAX_RECORDS + 3)]
        records, errors = self.parse(*many)
        self.assertEqual((len(records), len(errors)), (tools_index.MAX_RECORDS, 1))

    def test_unknown_fields_are_ignored(self):
        records, errors = self.parse(dict(record(), futureField='x'))
        self.assertEqual((len(records), errors), (1, []))

    def test_clean_text_removes_control_characters_and_escape_sequences_and_cuts(self):
        dirty = 'ok\x1b[31m red\x1b[0m\x07 bell\x00 nul\r\nnext\tline \x1b]0;title\x07end'
        self.assertEqual(tools_index.clean_text(dirty), 'ok red bell nul next line end')
        self.assertEqual(len(tools_index.clean_text('x' * 1000)), 300)
        self.assertTrue(tools_index.clean_text('x' * 1000).endswith('…'))
        self.assertEqual(tools_index.clean_text('short'), 'short')

    def test_a_file_on_disk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = child(directory, 'index.yaml')
            self.assertEqual(tools_index.read_index_file(path), ([], []))   # missing
            path.write_text(index(record()), encoding='utf-8')
            self.assertEqual(len(tools_index.read_index_file(path)[0]), 1)
            path.write_bytes(b'\xff\xfe\x00bad')
            self.assertEqual(len(tools_index.read_index_file(path)[1]), 1)
            path.write_text('schema: 1\n# ' + 'x' * tools_index.MAX_FILE_BYTES, encoding='utf-8')
            self.assertEqual(len(tools_index.read_index_file(path)[1]), 1)


class MergeCase(LayersCase):
    sets = 0

    def build(self, *specs, allow_override=None):
        """specs: (id, kind, {rel: text}[, extra layer.yaml keys]) in the order of the rule `**`."""
        MergeCase.sets += 1
        base = child(self.work, f'tools{MergeCase.sets}')
        entries = []
        for spec in specs:
            layer_id, kind, files = spec[:3]
            extra = spec[3] if len(spec) > 3 else {}
            options = {'version': generator_version()} if kind == 'core' else {}
            entries.append((layer_id, kind, make_layer(child(base, layer_id), layer_id, kind, files, extra_meta=extra, **options)))
        extra_config = {} if allow_override is None else {'allow_override': allow_override}
        self.write_config(entries, [(['**'], [item[0] for item in specs])], **extra_config)
        return layers.resolve_layers(self.project(f'tproj{MergeCase.sets}'))

    @staticmethod
    def ids(merged):
        return [item['id'] for item in merged.data['tools']]


class MergeTest(MergeCase):
    def test_the_rule_and_the_api(self):
        self.assertEqual(layers.merge_rule('tools/index.yaml'), 'tools-index')
        self.assertEqual(layers.merge_rule('tools/other.md'), 'per-file')   # only the index is aggregated
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}))
        self.assertEqual(self.ids(layerset.merged_file('tools/index.yaml')), ['a'])

    def test_the_union_in_the_order_of_the_layers_and_of_the_files(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('b'), record('a'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('d'), record('c'))}),
                              ('me', 'personal', {'tools/index.yaml': index(record('e'))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['b', 'a', 'd', 'c', 'e'])
        self.assertEqual([(item['id'], item['layer'], item['layer_kind']) for item in merged.data['tools']],
                         [('b', 'core', 'core'), ('a', 'core', 'core'), ('d', 'org', 'org'), ('c', 'org', 'org'), ('e', 'me', 'personal')])
        self.assertEqual(self.ids(layers.merged_tools_index(layerset)), self.ids(merged), 'deterministic')

    def test_a_record_carries_the_root_of_the_layer_that_wrote_it(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}), ('org', 'org', {'tools/index.yaml': index(record('b'))}))
        merged = layers.merged_tools_index(layerset)
        roots = {item['id']: item['root'] for item in merged.data['tools']}
        self.assertEqual(roots['a'], posix(layerset.layers[0].root))   # the record keeps the root as a POSIX path on every system (`pathfmt.posix`)
        self.assertEqual(roots['b'], posix(layerset.layers[1].root))

    def test_the_same_id_in_a_later_layer_replaces_the_record_and_keeps_the_place(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a', purpose='core one'), record('b'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('a', purpose='org one'))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['a', 'b'])
        self.assertEqual((merged.data['tools'][0]['purpose'], merged.data['tools'][0]['layer']), ('org one', 'org'))
        self.assertEqual([item['code'] for item in merged.conflicts], ['tool-id-shadowed'])
        self.assertEqual(merged.conflicts[0]['severity'], 'info')

    def test_supersedes_hides_the_records_of_lower_layers(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('old-a'), record('keep'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('new-a', supersedes=['old-a']))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['keep', 'new-a'])
        self.assertEqual([(item['id'], item['hidden_by']) for item in merged.data['hidden']], [('old-a', {'id': 'new-a', 'layer': 'org'})])

    def test_a_hidden_record_applies_nothing_and_there_are_no_chains_or_cycles(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('b', supersedes=['a']))}),
                              ('me', 'personal', {'tools/index.yaml': index(record('c', supersedes=['b']))}))
        merged = layers.merged_tools_index(layerset)
        # `c` hides `b`, so `b` no longer hides `a`
        self.assertEqual(self.ids(merged), ['a', 'c'])
        self.assertEqual([item['id'] for item in merged.data['hidden']], ['b'])

    def test_supersedes_of_itself_of_an_unknown_id_or_of_the_same_or_a_higher_layer_is_ignored(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('low', supersedes=['org-one', 'ghost', 'low', 'core-two']),
                                                                            record('core-two'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('org-one'))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['low', 'core-two', 'org-one'])
        ignored = [item for item in merged.conflicts if item['code'] == 'tool-supersedes-ignored']
        self.assertEqual(len(ignored), 4)
        self.assertTrue(all(item['severity'] == 'info' for item in ignored))

    def test_a_broken_index_is_skipped_with_a_warning_and_the_rest_stays(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}),
                              ('org', 'org', {'tools/index.yaml': 'schema: 1\ntools: [unclosed\n'}),
                              ('me', 'personal', {'tools/index.yaml': index(record('c'), 'text', schema=1)}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['a', 'c'])
        warnings = [item for item in merged.conflicts if item['code'] == 'tools-index-invalid']
        self.assertEqual([item['layer'] for item in warnings], ['org', 'me'])   # org: unclosed YAML; me: the record 'text' is not a mapping
        self.assertTrue(all(item['severity'] == 'warning' for item in warnings))

    def test_a_newer_schema_is_skipped_with_a_warning(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}), ('org', 'org', {'tools/index.yaml': index(record('b'), schema=2)}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['a'])
        self.assertEqual([item['code'] for item in merged.conflicts], ['tools-index-invalid'])

    def test_no_index_anywhere_gives_an_empty_result(self):
        merged = layers.merged_tools_index(self.build(('core', 'core', {'skills/a/SKILL.md': 'x\n'})))
        self.assertEqual((merged.data, merged.conflicts, merged.sources), ({'tools': [], 'hidden': []}, [], []))

    def test_the_findings_of_a_layer_check_include_the_index_problems_and_not_the_infos_as_failures(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('a', purpose='mine'), 'junk')}))
        found = layers.check_layers(layerset, hooks=False, orphans=False)
        codes = {item['code'] for item in found}
        self.assertIn('tools-index-invalid', codes)
        self.assertIn('override-without-consent', codes)   # the org layer replaced a record of the core
        self.assertNotEqual(layers.findings_status(found), 'BLOCKED')


class RobustnessTest(MergeCase):
    def test_exotic_yaml_never_raises_out_of_the_reader(self):
        texts = ['schema: 1\ntools:\n  - {id: a, path: p, purpose: x, topics: [], usage: u, origin: 2026-13-45}\n',
                 'schema: !!int x\ntools: []\n', 'schema: 1\ntools: ' + '[' * 5000 + '\n', '\x00\x01', '!!python/object:os.system x\n']
        for text in texts:
            with self.subTest(text[:40]):
                records, errors = tools_index.parse_index(text)
                self.assertIsInstance(records, list)
                self.assertIsInstance(errors, list)
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}), ('org', 'org', {'tools/index.yaml': texts[2]}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['a'])
        self.assertEqual([item['code'] for item in merged.conflicts], ['tools-index-invalid'])

    def test_a_symlink_or_a_directory_instead_of_the_file_is_not_read(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a'))}), ('org', 'org', {'skills/x/SKILL.md': 'x\n'}))
        org = layerset.layers[1].root
        outside = child(self.work, 'outside-index.yaml')
        outside.write_text(index(record('evil')), encoding='utf-8')
        (org / 'tools').mkdir()
        (org / 'tools' / 'index.yaml').symlink_to(outside)
        merged = layers.merged_tools_index(layers.resolve_layers(self.project('tproj-link')))
        self.assertEqual(self.ids(merged), ['a'])
        self.assertTrue([item for item in merged.conflicts if item['code'] == 'tools-index-invalid'])
        (org / 'tools' / 'index.yaml').unlink()
        (org / 'tools' / 'index.yaml').mkdir()
        self.assertEqual(self.ids(layers.merged_tools_index(layers.resolve_layers(self.project('tproj-dir')))), ['a'])

    def test_a_file_over_the_size_limit_is_ignored_and_one_at_the_limit_is_read(self):
        records, errors = tools_index.read_index_file(self.write_sized(tools_index.MAX_FILE_BYTES + 1), 'big')
        self.assertEqual((records, len(errors)), ([], 1))
        records, errors = tools_index.read_index_file(self.write_sized(tools_index.MAX_FILE_BYTES), 'edge')
        self.assertEqual((len(records), errors), (1, []))

    def write_sized(self, size):
        head = index(record('a', origin='x'))
        path = child(self.work, f'sized-{size}.yaml')
        path.write_bytes((head + '#' * (size - len(head.encode('utf-8')) - 1) + '\n').encode('utf-8'))   # bytes: `write_text` turns `\n` into `\r\n` on Windows and the file would be over the limit
        return path

    def test_three_layers_with_one_id_keep_the_highest_and_say_so_twice(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a', usage='one'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('a', usage='two'))}),
                              ('me', 'personal', {'tools/index.yaml': index(record('a', usage='three'))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual([item['usage'] for item in merged.data['tools']], ['three'])
        self.assertEqual([item['code'] for item in merged.conflicts].count('tool-id-shadowed'), 2)

    def test_a_cycle_across_layers_hides_only_downwards(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('a', supersedes=['b']))}),
                              ('org', 'org', {'tools/index.yaml': index(record('b', supersedes=['a']))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['b'])

    def test_a_personal_record_hides_an_org_one_without_consent(self):
        layerset = self.build(('core', 'core', {'tools/index.yaml': index(record('c'))}),
                              ('org', 'org', {'tools/index.yaml': index(record('o'))}),
                              ('me', 'personal', {'tools/index.yaml': index(record('p', supersedes=['o']))}))
        merged = layers.merged_tools_index(layerset)
        self.assertEqual(self.ids(merged), ['c', 'p'])
        self.assertEqual([item for item in layers.check_layers(layerset, hooks=False, orphans=False) if item['code'] == 'override-without-consent'], [])

    def test_removed_in_an_org_layer_on_a_core_id_needs_consent(self):
        core = {'tools/index.yaml': index(record('a'))}
        layerset = self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('a', status='removed'))}))
        self.assertTrue([item for item in layers.check_layers(layerset, hooks=False, orphans=False) if item['code'] == 'override-without-consent'])


class TrustTest(MergeCase):
    def consent(self, layerset):
        return [item['message'] for item in layers.check_layers(layerset, hooks=False, orphans=False) if item['code'] == 'override-without-consent']

    def test_replacing_a_record_of_the_core_needs_consent(self):
        core = {'tools/index.yaml': index(record('a'))}
        layerset = self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('a', usage='sh evil.sh'))}))
        self.assertTrue(self.consent(layerset))
        allowed = self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('a', usage='sh evil.sh'))}), allow_override=['tools:a'])
        self.assertEqual(self.consent(allowed), [])

    def test_hiding_a_record_of_the_core_needs_consent(self):
        core = {'tools/index.yaml': index(record('a'))}
        layerset = self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('b', supersedes=['a']))}))
        self.assertTrue(self.consent(layerset))
        allowed = self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('b', supersedes=['a']))}), allow_override=['tools:*'])
        self.assertEqual(self.consent(allowed), [])

    def test_new_records_an_identical_copy_and_the_personal_layer_need_no_consent(self):
        core = {'tools/index.yaml': index(record('a'))}
        self.assertEqual(self.consent(self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('new'))}))), [])
        self.assertEqual(self.consent(self.build(('core', 'core', core), ('org', 'org', {'tools/index.yaml': index(record('a'))}))), [])
        self.assertEqual(self.consent(self.build(('core', 'core', core), ('me', 'personal', {'tools/index.yaml': index(record('a', usage='sh mine.sh'))}))), [])


if __name__ == '__main__':
    unittest.main()
