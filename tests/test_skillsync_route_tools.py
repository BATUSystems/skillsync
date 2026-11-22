"""`skillsync route` and the tool index (CORE-1140-SUP-TOOLS, TOOLS-2): the `tools` section.

The records come from layers and from the project; their texts are data (cleaned, cut, marked) and nothing is run. Without any index the key is absent
(the result is the same as before the feature); a broken index is a warning and never takes the lessons and the domain knowledge with it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import write, yaml_text  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402
from tmp_paths import child, descend  # noqa: E402


def rec(record_id, **extra):
    base = {'id': record_id, 'path': f'scripts/{record_id}.mjs', 'purpose': f'purpose of {record_id}', 'topics': ['ui'], 'usage': f'node {record_id}.mjs'}
    base.update(extra)
    return base


def idx(*records):
    return yaml_text({'schema': 1, 'tools': list(records)})


class RouteToolsCase(WorkflowFixture):
    def put(self, layer_dir, *records, text=None):
        write(descend(Path(layer_dir), 'tools/index.yaml'), text if text is not None else idx(*records))

    def with_layers(self):
        org, me = child(self.base, 'org'), child(self.base, 'me')
        org.mkdir()
        me.mkdir()
        self.use_layers(('core', 'core', self.library), ('org', 'org', org), ('me', 'personal', me))
        return org, me

    def route(self, *args, topic='ui'):
        return self.report('route', '--topics', topic, *args)

    def ids(self, report, key='tools'):
        return [item['id'] for item in report[key]]


class AbsenceTest(RouteToolsCase):
    def test_without_any_index_the_key_is_absent(self):
        result = self.route()
        for key in ('tools', 'tools_note', 'tools_warnings', 'tools_truncated', 'tools_hidden'):
            self.assertNotIn(key, result)

    def test_with_an_index_the_section_is_always_there_even_when_empty(self):
        self.put(self.library, rec('a', topics=['db']))
        result = self.route()
        self.assertEqual(result['tools'], [])
        self.assertIn('data from the layer', result['tools_note'])


class MatchTest(RouteToolsCase):
    def test_topic_files_glob_and_all(self):
        self.put(self.library, rec('by-topic'), rec('by-file', topics=[], files=['**/*.css']), rec('none', topics=['db']), rec('empty', topics=[]))
        self.assertEqual(self.ids(self.route()), ['by-topic'])
        self.assertEqual(self.ids(self.route('--files', 'web/app/a.css', 'x.css')), ['by-file', 'by-topic'])   # files before topics
        self.assertEqual(self.ids(self.route('--tools-all')), ['by-topic', 'by-file', 'empty', 'none'])   # the topic fits; the rest only through --tools-all, by id

    def test_fields_and_the_absolute_path_of_the_owner(self):
        self.put(self.library, rec('a', selftest='node a.mjs --selftest', limits=['no animations']))
        item = self.route()['tools'][0]
        self.assertEqual((item['layer'], item['layer_kind'], item['status'], item['matched_by']), ('core', 'core', 'active', 'topics'))
        self.assertEqual(item['path'], (self.library / 'scripts/a.mjs').as_posix())
        self.assertEqual((item['usage'], item['selftest'], item['limits']), ('node a.mjs', 'node a.mjs --selftest', ['no animations']))

    def test_texts_are_cleaned_and_cut(self):
        self.put(self.library, rec('a', purpose='x\x1b[31mred\x1b[0m' + 'y' * 500, usage='run\nit\x00'))
        item = self.route()['tools'][0]
        self.assertNotIn('\x1b', item['purpose'])
        self.assertLessEqual(len(item['purpose']), 300)
        self.assertEqual(item['usage'], 'run it')


class LayersTest(RouteToolsCase):
    def test_higher_layers_first_in_a_group_and_the_project_first_of_all(self):
        org, me = self.with_layers()
        self.put(self.library, rec('core-tool'))
        self.put(org, rec('org-tool'))
        self.put(me, rec('my-tool'))
        write(descend(self.root, 'docs/tools/index.yaml'), idx(rec('proj-tool'), rec('proj-other', topics=['db'])))
        self.assertEqual(self.ids(self.route()), ['proj-tool', 'my-tool', 'org-tool', 'core-tool'])

    def test_personal_records_are_marked_local(self):
        org, me = self.with_layers()
        self.put(me, rec('mine'))
        item = self.route()['tools'][0]
        self.assertIs(item['local'], True)
        self.assertIn('do not write', item['local_note'])

    def test_a_project_removed_record_does_not_hide_the_tool_of_a_layer(self):
        self.put(self.library, rec('a'))
        write(descend(self.root, 'docs/tools/index.yaml'), idx(rec('a', status='removed')))
        self.assertEqual([(item['layer']) for item in self.route()['tools']], ['core'])

    def test_superseded_removed_and_hidden_are_not_offered_but_listed_in_history(self):
        org, me = self.with_layers()
        self.put(self.library, rec('old'), rec('gone', status='removed'), rec('manual', status='superseded'))
        self.put(me, rec('new', supersedes=['old']))
        plain = self.route()
        self.assertEqual(self.ids(plain), ['new'])
        self.assertNotIn('tools_hidden', plain)
        history = self.route('--tools-history')
        self.assertEqual(self.ids(history), ['new'])
        by = {item['id']: item['hidden_by'] for item in history['tools_hidden']}
        self.assertEqual(by['old'], {'id': 'new', 'layer': 'me'})
        self.assertEqual(by['gone'], {'status': 'removed'})
        self.assertEqual(by['manual'], {'status': 'superseded'})


class LimitAndErrorsTest(RouteToolsCase):
    def test_at_most_ten_and_the_cut_is_reported(self):
        self.put(self.library, *[rec(f't{number:02d}') for number in range(12)])
        result = self.route()
        self.assertEqual(len(result['tools']), 10)
        self.assertIs(result['tools_truncated'], True)
        self.assertEqual(self.ids(result), [f't{number:02d}' for number in range(10)])

    def test_a_broken_layer_index_is_a_warning_and_the_rest_of_route_works(self):
        org, me = self.with_layers()
        self.put(self.library, rec('a'))
        self.put(org, text='schema: 1\ntools: [unclosed\n')
        result = self.route()
        self.assertEqual(self.ids(result), ['a'])
        self.assertTrue(result['tools_warnings'])
        self.assertEqual(result['status'], 'NOT_RUN')

    def test_a_broken_project_index_is_a_warning(self):
        write(descend(self.root, 'docs/tools/index.yaml'), 'schema: 7\ntools: []\n')
        result = self.route()
        self.assertEqual(result['tools'], [])
        self.assertIn('newer', result['tools_warnings'][0])

    def test_a_linked_project_index_is_not_followed(self):
        outside = child(self.base, 'outside.yaml')
        outside.write_text(idx(rec('evil')), encoding='utf-8')
        target = descend(self.root, 'docs/tools/index.yaml')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
        result = self.route()
        self.assertEqual(result['tools'], [])
        self.assertTrue(result['tools_warnings'])


if __name__ == '__main__':
    unittest.main()
