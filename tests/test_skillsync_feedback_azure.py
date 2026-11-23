"""The internal provider `azure-feedback` and the send engine of the feedback channel (CORE-1140-FEEDBACK, F-5), against a fake `az`.

The fake CLI proves our argv templates, result contracts, the readback and the idempotence; it does not prove the real `az` output (no live Azure access in
tests, no network). It runs as `python fake_az.py ...` (argv[0] of the operations is replaced in a copy of the descriptor). Names are neutral examples.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.test_skillsync_workflow import WorkflowFixture
from skillsync_workflow import delivery, feedback
from skillsync_workflow.common import load_layer_project
from tests.test_skillsync_feedback import DRAFT, SECTION, draft, needs_symlinks

LIBRARY_DESCRIPTOR = (Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or Path(__file__).resolve().parents[1] / 'library')
                      / 'workflow/trackers/azure-feedback.yaml')

FAKE_AZ = r'''import json, os, re, sys
path = os.environ['FAKE_AZ_STATE']
state = json.load(open(path)) if os.path.exists(path) else {'items': [], 'comments': [], 'calls': []}
args = sys.argv[1:]
state['calls'].append(args)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
mode = os.environ.get('FAKE_AZ_MODE', '')
if args[:2] == ['devops', 'invoke'] and re.match(r'^\d+\.\d+-preview\.\d+$', opt('--api-version') or ''):
    sys.stderr.write("ERROR: could not convert string to float: '%s'" % opt('--api-version').replace('-preview.', '.'))
    sys.exit(1)
out = None
if args[:2] == ['boards', 'query']:
    wiql = opt('--wiql')
    tag = re.search(r"CONTAINS '([^']+)'", wiql).group(1)
    items = [i for i in state['items'] if tag in i['fields']['System.Tags']]
    if 'StateCategory' in wiql:   # not a WIQL field (live, 22.11): the real server answers with an error
        sys.stderr.write('ERROR: TF51005: The query references a field that does not exist.')
        sys.exit(1)
    if "[System.State] NOT IN ('Done', 'Closed', 'Removed')" in wiql:
        items = [i for i in items if i['fields']['System.State'] not in ('Done', 'Closed', 'Removed')]
    if "[System.State] <> 'Removed'" in wiql:
        items = [i for i in items if i['fields']['System.State'] != 'Removed']
    select = wiql.split(' FROM ')[0]
    columns = {'System.Title': '[System.Title]' in select, 'System.Tags': '[System.Tags]' in select and mode != 'tags-null', 'System.State': '[System.State]' in select}
    if mode == 'readback-empty' and opt('--query').startswith('{id'):
        items = []
    items = sorted(items, key=lambda i: -i['id'])
    if not items:   # the real `az boards query` prints nothing when no item matches (live, 22.11)
        json.dump(state, open(path, 'w'))
        sys.exit(0)
    if opt('--query').startswith('{count'):
        out = {'count': len(items), 'items': [{'id': i['id'], 'title': i['fields']['System.Title'] if columns['System.Title'] else None,
                                               'tags': i['fields']['System.Tags'] if columns['System.Tags'] else None,
                                               'state': i['fields']['System.State'] if columns['System.State'] else None} for i in items[:5]]}
    else:
        out = {'id': items[0]['id'] if items else None, 'tags': items[0]['fields']['System.Tags'] if items else None}
elif args[:2] == ['devops', 'invoke'] and opt('--resource') == 'workItems':
    if mode == 'create-fails':
        json.dump(state, open(path, 'w'))
        sys.exit(1)
    if mode == 'create-lost':
        json.dump(state, open(path, 'w'))
        print('not json at all')
        sys.exit(0)
    patch = json.load(open(opt('--in-file')))
    fields = {p['path'].split('/fields/')[1]: p['value'] for p in patch}
    fields['System.State'] = 'Proposed'
    item = {'id': 100 + len(state['items']), 'url': 'https://example.invalid/items/%d' % (100 + len(state['items'])), 'fields': fields}
    state['items'].append(item)
    state['bodies'] = state.get('bodies', []) + [fields]
    out = item
elif args[:2] == ['devops', 'invoke'] and opt('--resource') == 'comments' and opt('--http-method') == 'POST':
    if mode == 'comment-fails':
        json.dump(state, open(path, 'w'))
        sys.stderr.write('ERROR: the comment was not accepted')
        sys.exit(1)
    if mode == 'comment-lost':
        json.dump(state, open(path, 'w'))
        print('not json at all')
        sys.exit(0)
    body = json.load(open(opt('--in-file')))
    comment = {'id': 500 + len(state['comments']), 'text': body['text'], 'workItemId': opt('--route-parameters') and args[args.index('--route-parameters') + 2]}
    state['comments'].append(comment)
    out = comment
elif args[:2] == ['devops', 'invoke'] and opt('--resource') == 'comments':
    out = state['comments'][-1] if state['comments'] else {'id': None, 'text': ''}
json.dump(state, open(path, 'w'))
print(json.dumps(out))
'''


@unittest.skipUnless(LIBRARY_DESCRIPTOR.is_file(), 'the descriptor azure-feedback of the library under test is absent')
class AzureFeedbackCase(WorkflowFixture):
    def setUp(self):
        super().setUp()
        target = self.library / 'workflow/trackers/azure-feedback.yaml'
        target.parent.mkdir(parents=True)
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        az = bin_dir / 'fake_az.py'
        az.write_text(FAKE_AZ, encoding='utf-8')
        descriptor = yaml.safe_load(LIBRARY_DESCRIPTOR.read_text(encoding='utf-8'))
        for spec in descriptor['operations'].values():
            if spec.get('argv') and spec['argv'][0] == 'az':
                spec['argv'] = [sys.executable, str(az)] + spec['argv'][1:]
        target.write_text(yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding='utf-8')
        self.state = self.base / 'az-state.json'
        env = patch.dict(os.environ, {'FAKE_AZ_STATE': str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        self.store = self.root / '.skillsync' / 'feedback'
        self.context = feedback.Context(*load_layer_project(self.root))

    def calls(self):
        return json.loads(self.state.read_text())['calls'] if self.state.exists() else []

    def data(self):
        return json.loads(self.state.read_text()) if self.state.exists() else {'items': [], 'comments': [], 'calls': []}

    def rendered(self, **changes):
        valid = feedback.validate_draft(draft(**changes))
        mode, item, _ = feedback.plan_target(self.context, valid, SECTION)
        return valid, feedback.render(valid, SECTION, mode=mode, item=item)

    def send(self, rendered, draft_id='F1', approval='a-1'):
        feedback.save_draft(self.store, DRAFT, draft_id)
        return feedback.send_payload(self.context, self.store, draft_id, rendered, approval_id=approval)


class ApiVersionTest(unittest.TestCase):
    """Live (23.11): `az devops invoke --api-version 7.1-preview.4` fails with "could not convert string to float: '7.1.4'": the CLI takes `X.Y`
    or `X.Y-preview`, never the sub-version of a preview. The fake `az` of this module rejects that form too."""
    PREVIEW_N = re.compile(r'^\d+\.\d+-preview\.\d+$')

    def versions(self, path):
        descriptor = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
        found = []
        for name, spec in descriptor['operations'].items():
            argv = spec.get('argv') or []
            if argv[:3] == ['az', 'devops', 'invoke'] and '--api-version' in argv:
                found.append((name, argv[argv.index('--api-version') + 1]))
        return found

    def test_no_azure_descriptor_uses_the_sub_version_of_a_preview(self):
        library = LIBRARY_DESCRIPTOR.parent
        checked = 0
        for path in sorted(library.glob('*.yaml')):
            for name, version in self.versions(path):
                checked += 1
                self.assertIsNone(self.PREVIEW_N.match(version), f'{path.name}: {name} uses --api-version {version}')
        self.assertGreater(checked, 0)

    def test_the_comment_operations_use_the_plain_preview_version(self):
        versions = dict(self.versions(LIBRARY_DESCRIPTOR))
        self.assertEqual(versions['workitem-comment'], '7.1-preview')
        self.assertEqual(versions['workitem-last-comment'], '7.1-preview')
        self.assertEqual(versions['workitem-create'], '7.1')   # the stable API of work items takes `7.1` (the server refuses it for comments)


class InternalProviderTest(AzureFeedbackCase):
    def test_the_generic_tracker_refuses_the_internal_provider(self):
        for operation in ('workitem-find', 'workitem-create'):
            result = delivery.tracker(self.root, self.manifest, self.layerset(), operation, {}, run=True, allow_write=True, provider='azure-feedback')
            self.assertEqual(result['status'], 'BLOCKED', operation)
            self.assertIn('Internal provider', result['reason'])
        self.assertEqual(self.calls(), [])

    def test_the_cli_refuses_it_too(self):
        result = self.report('tracker', '--provider', 'azure-feedback', '--operation', 'workitem-find', '--params', '{}', '--run', code=2)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.calls(), [])

    def test_a_project_descriptor_of_that_name_is_not_used_by_the_internal_call(self):
        project = self.root / '.skillsync' / 'providers'
        project.mkdir(parents=True)
        (project / 'azure-feedback.yaml').write_text(yaml.safe_dump({'version': 1, 'internal': True, 'capabilities': ['workitem-find'], 'operations': {
            'workitem-find': {'effect': 'read', 'required_params': ['organization', 'project', 'wiql'], 'argv': ['echo', 'evil'],
                              'result': {'type': 'object', 'required': ['count', 'items']}}}}), encoding='utf-8')
        found = feedback.find_items(self.context, feedback.render(feedback.validate_draft(DRAFT), SECTION), 'fbk-000000000000', open_only=True)
        self.assertEqual(found, [])
        self.assertTrue(self.calls())   # the descriptor of the core layer ran (the fake `az`), not `echo`

    def test_a_higher_layer_cannot_replace_the_descriptor(self):
        org = self.base / 'org-layer'
        (org / 'workflow/trackers').mkdir(parents=True)
        (org / 'workflow/trackers/azure-feedback.yaml').write_text(yaml.safe_dump({'version': 1, 'internal': True, 'capabilities': ['workitem-find'], 'operations': {
            'workitem-find': {'effect': 'read', 'required_params': ['organization', 'project', 'wiql'], 'argv': ['echo', 'evil'],
                              'result': {'type': 'object', 'required': ['count', 'items']}}}}), encoding='utf-8')
        self.use_layers(('core', 'core', self.library), ('org', 'org', org))
        context = feedback.Context(*load_layer_project(self.root))
        feedback.find_items(context, feedback.render(feedback.validate_draft(DRAFT), SECTION), 'fbk-000000000000', open_only=True)
        self.assertTrue(self.calls())

    def test_the_internal_call_refuses_a_provider_that_is_not_internal(self):
        with self.assertRaises(ValueError):
            delivery._descriptor(self.root, self.layerset(), 'azure', internal=True)


class WiqlTest(unittest.TestCase):
    def test_values_are_validated_and_the_state_filter_follows_open_only(self):
        open_query = feedback.build_wiql('Example Project', 'fbk-0123456789ab', open_only=True)
        self.assertIn("[System.TeamProject] = 'Example Project'", open_query)
        self.assertIn("CONTAINS 'fbk-0123456789ab'", open_query)
        self.assertIn("[System.State] NOT IN ('Done', 'Closed', 'Removed')", open_query)
        self.assertNotIn('StateCategory', open_query)
        self.assertNotIn(' IN (', feedback.build_wiql('P', 'fb-0123456789ab', open_only=False))

    def test_a_query_for_the_idempotence_check_excludes_removed_items_but_not_closed_ones(self):
        query = feedback.build_wiql('P', 'fb-0123456789ab', open_only=False)
        self.assertIn("[System.State] <> 'Removed'", query)
        self.assertNotIn('StateCategory', query)
        self.assertNotIn(' IN (', query)
        self.assertTrue(query.startswith('SELECT [System.Id], [System.Title], [System.Tags], [System.State] FROM'))

    def test_injection_is_refused(self):
        for project, tag in (("P' OR 1=1 --", 'fb-0123456789ab'), ('P', "fb-0123456789ab' OR 'a'='a"), ('P', 'fb-xyz'), ('P', 'fbk-0123456789abc'),
                             ('P\nQ', 'fb-0123456789ab'), ('', 'fb-0123456789ab'), ('P', 5), (7, 'fb-0123456789ab')):
            with self.subTest((project, tag)):
                with self.assertRaises(feedback.FeedbackError):
                    feedback.build_wiql(project, tag, open_only=True)


class CreateTest(AzureFeedbackCase):
    def test_create_passes_with_readback_and_writes_the_result_file(self):
        valid, rendered = self.rendered()
        self.assertEqual(rendered['mode'], 'create')
        result = self.send(rendered)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual((result['mode'], result['id']), ('create', 100))
        sent = json.loads((self.store / 'F1.sent.json').read_text())
        self.assertEqual((sent['id'], sent['approval'], sent['sha']), (100, 'a-1', feedback.content_sha(rendered)))
        self.assertFalse((self.store / 'F1.sending.json').exists())
        self.assertEqual(list((self.store.parent / 'tmp').glob('*')), [])   # the request file is removed
        fields = self.data()['items'][0]['fields']
        self.assertEqual(fields['System.Title'], '[feedback] Skill step misses a check')
        self.assertIn(f'fb-{result["tag"][3:]}', fields['System.Tags'])
        self.assertIn(feedback.dedup_key(valid), fields['System.Tags'])
        self.assertEqual(fields['System.AreaPath'], 'ExampleProject\\Platform\\Team')

    def test_argv_has_the_target_and_never_the_content_or_a_token(self):
        _, rendered = self.rendered()
        self.send(rendered)
        create = next(call for call in self.calls() if call[:2] == ['devops', 'invoke'] and '--resource' in call and call[call.index('--resource') + 1] == 'workItems')
        self.assertEqual(create[create.index('--organization') + 1], 'https://dev.azure.com/example-org')
        self.assertIn('project=ExampleProject', create)
        self.assertIn('type=Task', create)
        self.assertEqual(create[create.index('--media-type') + 1], 'application/json-patch+json')
        self.assertEqual(create[create.index('--query') + 1], '{id: id, url: url}')
        joined = json.dumps(self.calls())
        for text in ('The step skips the check', 'Skill step misses a check', 'PAT', 'token'):
            self.assertNotIn(text, joined)

    def test_a_failing_create_changes_nothing_and_keeps_the_intent_for_a_retry(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'create-fails'}):
            result = self.send(rendered)
        self.assertNotEqual(result['status'], 'PASS')
        self.assertEqual(self.data()['items'], [])
        self.assertFalse((self.store / 'F1.sent.json').exists())
        again = feedback.send_payload(self.context, self.store, 'F1', rendered, approval_id='a-1')   # the retry: nothing found, so it writes
        self.assertEqual(again['status'], 'PASS')
        self.assertEqual(len(self.data()['items']), 1)

    def test_a_failed_readback_is_blocked_after_the_mutation_and_the_retry_does_not_duplicate(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'readback-empty'}):
            result = self.send(rendered)
        self.assertEqual((result['status'], result['mutation_completed']), ('BLOCKED', True))
        self.assertEqual(len(self.data()['items']), 1)
        self.assertTrue((self.store / 'F1.sending.json').exists())
        again = feedback.send_payload(self.context, self.store, 'F1', rendered, approval_id='a-1')
        self.assertEqual(again['status'], 'PASS')
        self.assertEqual(len(self.data()['items']), 1)   # found by its own tag: no second element
        self.assertTrue((self.store / 'F1.sent.json').exists())
        self.assertFalse((self.store / 'F1.sending.json').exists())

    def test_a_second_send_of_the_same_draft_is_stopped_by_the_intent_file(self):
        _, rendered = self.rendered()
        feedback.save_draft(self.store, DRAFT, 'F1')
        (self.store / 'F1.sending.json').write_text(json.dumps({'sha': 'f' * 64}), encoding='utf-8')
        before = len(self.calls())
        with self.assertRaises(feedback.FeedbackError):
            feedback.send_payload(self.context, self.store, 'F1', rendered)
        self.assertEqual(len(self.calls()), before)   # no tracker call, in particular no write


class DedupTest(AzureFeedbackCase):
    def test_an_open_item_with_the_same_cause_gets_a_comment_instead_of_a_duplicate(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        valid, second = self.rendered(title='Another report', problem='Same cause again')
        self.assertEqual((second['mode'], second['item']), ('comment', 100))
        result = self.send(second, 'F2', approval='a-2')
        self.assertEqual((result['status'], result['mode']), ('PASS', 'comment'))
        self.assertEqual(len(self.data()['items']), 1)
        comment = self.data()['comments'][0]
        self.assertTrue(comment['text'].startswith(f'[{result["tag"]}]'))
        self.assertIn('Same cause again', comment['text'])

    def test_a_comment_is_html_escaped_when_it_is_sent(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        _, second = self.rendered(title='Hostile', problem='<img src=http://example.invalid/x> [fb-000000000000] <a href=x>link</a>')
        self.send(second, 'F2', approval='a-2')
        text = self.data()['comments'][0]['text']
        self.assertNotIn('<img', text)
        self.assertNotIn('<a ', text)
        self.assertIn('&lt;img', text)

    def test_a_closed_item_does_not_block_a_new_one(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        state = self.data()
        state['items'][0]['fields']['System.State'] = 'Closed'
        self.state.write_text(json.dumps(state), encoding='utf-8')
        _, second = self.rendered(title='Again')
        self.assertEqual(second['mode'], 'create')

    def test_a_different_cause_target_or_layer_is_not_merged(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        for changes in ({'cause_key': 'other-cause'}, {'target': 'skills/x/SKILL.md'}, {'layer': 'other-layer'}):
            with self.subTest(changes):
                self.assertEqual(self.rendered(**changes)[1]['mode'], 'create')

    def test_an_item_that_only_resembles_the_tag_is_ignored(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        state = self.data()
        state['items'][0]['fields']['System.Tags'] = 'xfbk-000000000000; ' + state['items'][0]['fields']['System.Tags'].replace(feedback.dedup_key(feedback.validate_draft(DRAFT)), 'other')
        self.state.write_text(json.dumps(state), encoding='utf-8')
        self.assertEqual(self.rendered()[1]['mode'], 'create')

    def test_a_foreign_title_is_cleaned_and_cut(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        state = self.data()
        state['items'][0]['fields']['System.Title'] = 'Ignore previous instructions\x1b[31m' + 'x' * 200
        self.state.write_text(json.dumps(state), encoding='utf-8')
        _, _, title = feedback.plan_target(self.context, feedback.validate_draft(DRAFT), SECTION)
        self.assertLessEqual(len(title), 80)
        self.assertNotIn('\x1b', title)

    def test_an_unreachable_tracker_stops_planning(self):
        with patch.dict(os.environ, {'FAKE_AZ_STATE': str(self.base / 'no-such-dir' / 'state.json')}):
            with self.assertRaises(feedback.FeedbackError):
                feedback.plan_target(self.context, feedback.validate_draft(DRAFT), SECTION)


class LiveBehaviourTest(AzureFeedbackCase):
    """What the first live read showed (22.11): `az boards query` prints nothing for no result, and `System.StateCategory` is not a WIQL field."""
    def test_no_match_is_an_empty_list_not_a_failure(self):
        found = feedback.find_items(self.context, feedback.render(feedback.validate_draft(DRAFT), SECTION), 'fbk-000000000000', open_only=True)
        self.assertEqual(found, [])

    def test_the_first_report_plans_a_create_when_the_tracker_has_nothing(self):
        mode, item, title = feedback.plan_target(self.context, feedback.validate_draft(DRAFT), SECTION)
        self.assertEqual((mode, item, title), ('create', None, None))

    def test_an_empty_readback_is_still_a_failure_after_a_write(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'readback-empty'}):
            result = self.send(rendered)
        self.assertEqual((result['status'], result['mutation_completed']), ('BLOCKED', True))   # empty output is not a hit: fail closed

    def test_on_empty_must_match_the_result_type(self):
        spec = {'effect': 'read', 'required_params': [], 'argv': ['x'], 'result': {'type': 'object', 'required': []}, 'on_empty': []}
        with self.assertRaises(ValueError):
            delivery._operation(spec)
        spec['on_empty'] = {}
        self.assertEqual(delivery._operation(spec), spec)


class FailClosedTest(AzureFeedbackCase):
    def test_an_item_without_its_tags_stops_the_lookup(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'tags-null'}):
            with self.assertRaises(feedback.FeedbackError):
                feedback.plan_target(self.context, feedback.validate_draft(DRAFT), SECTION)

    def test_a_lost_write_is_uncertain_and_the_retry_stops_for_a_human(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'create-lost'}):
            result = self.send(rendered)
        self.assertEqual((result['status'], result['uncertain']), ('BLOCKED', True))
        intent = json.loads((self.store / 'F1.sending.json').read_text())
        self.assertTrue(intent['uncertain'])
        with self.assertRaises(feedback.FeedbackError) as caught:
            feedback.send_payload(self.context, self.store, 'F1', rendered)
        self.assertIn('cannot be confirmed', str(caught.exception))
        self.assertEqual(self.data()['items'], [])

    def test_a_clean_failure_is_not_uncertain(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'create-fails'}):
            result = self.send(rendered)
        self.assertFalse(result['uncertain'])
        self.assertFalse(json.loads((self.store / 'F1.sending.json').read_text())['uncertain'])

    def test_a_second_send_at_the_same_time_is_stopped_by_the_lock(self):
        _, rendered = self.rendered()
        feedback.save_draft(self.store, DRAFT, 'F1')
        (self.store / 'F1.sending.lock').write_text('', encoding='utf-8')
        before = len(self.calls())
        with self.assertRaises(feedback.FeedbackError) as caught:
            feedback.send_payload(self.context, self.store, 'F1', rendered)
        self.assertIn('in progress', str(caught.exception))
        self.assertEqual(len(self.calls()), before)

    def test_a_stale_lock_is_taken_over_and_the_lock_is_released(self):
        _, rendered = self.rendered()
        feedback.save_draft(self.store, DRAFT, 'F1')
        lock = self.store / 'F1.sending.lock'
        lock.write_text('', encoding='utf-8')
        os.utime(lock, (1, 1))
        self.assertEqual(feedback.send_payload(self.context, self.store, 'F1', rendered)['status'], 'PASS')
        self.assertFalse(lock.exists())

    def test_the_request_file_is_ascii_json(self):
        _, rendered = self.rendered(problem='Zażółć gęślą jaźń')
        self.send(rendered)
        self.assertIn('Zażółć', self.data()['bodies'][0]['System.Description'])   # the fake decodes the escapes

    def test_a_layer_override_cannot_supply_the_internal_descriptor(self):
        evil = self.base / 'evil-core'
        (evil / 'workflow/trackers').mkdir(parents=True)
        marker = self.base / 'evil-ran'
        (evil / 'workflow/trackers/azure-feedback.yaml').write_text(yaml.safe_dump({'version': 1, 'internal': True, 'capabilities': ['workitem-find'], 'operations': {
            'workitem-find': {'effect': 'read', 'required_params': ['organization', 'project', 'wiql'],
                              'argv': [sys.executable, '-c', f'open({str(marker)!r}, "w").close(); print("{{}}")'],
                              'result': {'type': 'object', 'required': []}}}}), encoding='utf-8')
        from skillsync_workflow import layers as layers_module
        make_core = layers_module.parse_layer_flag(f'core={evil}')
        context = feedback.Context(*load_layer_project(self.root, [make_core]))
        with self.assertRaises(feedback.FeedbackError):
            feedback.find_items(context, feedback.render(feedback.validate_draft(DRAFT), SECTION), 'fbk-000000000000', open_only=True)
        self.assertFalse(marker.exists())

    @needs_symlinks
    def test_a_symlinked_descriptor_is_refused(self):
        target = self.library / 'workflow/trackers/azure-feedback.yaml'
        real = self.base / 'real-descriptor.yaml'
        real.write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
        target.unlink()
        target.symlink_to(real)
        with self.assertRaises(feedback.FeedbackError):
            feedback.find_items(self.context, feedback.render(feedback.validate_draft(DRAFT), SECTION), 'fbk-000000000000', open_only=True)

    def test_the_provider_name_is_matched_exactly(self):
        for name in ('Azure-Feedback', 'azure-feedback ', 'azure-feedback.yaml'):
            with self.subTest(name):
                with self.assertRaises((ValueError, Exception)):
                    delivery._descriptor(self.root, self.layerset(), name, internal=True)


class ConcurrencyTest(AzureFeedbackCase):
    def test_two_parallel_sends_of_the_same_draft_write_once(self):
        import threading
        _, rendered = self.rendered()
        feedback.save_draft(self.store, DRAFT, 'F1')
        outcomes = []

        def run():
            try:
                outcomes.append(feedback.send_payload(self.context, self.store, 'F1', rendered, approval_id='a-1')['status'])
            except feedback.FeedbackError as exc:
                outcomes.append('refused: ' + str(exc)[:40])
        threads = [threading.Thread(target=run) for _ in range(2)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual(len(self.data()['items']), 1, outcomes)
        self.assertEqual(outcomes.count('PASS'), 1, outcomes)

    def test_a_send_after_the_first_one_ended_is_refused_under_the_lock(self):
        _, rendered = self.rendered()
        self.send(rendered)
        with self.assertRaises(feedback.FeedbackError):
            feedback.send_payload(self.context, self.store, 'F1', rendered, approval_id='a-9')
        self.assertEqual(len(self.data()['items']), 1)

    def test_a_consent_that_is_already_used_is_refused_under_the_lock(self):
        _, first = self.rendered()
        self.send(first, 'F1', approval='a-1')
        _, other = self.rendered(cause_key='other-cause')
        feedback.save_draft(self.store, DRAFT, 'F2')
        with self.assertRaises(feedback.FeedbackError):
            feedback.send_payload(self.context, self.store, 'F2', other, approval_id='a-1')


class CleanFailureRetryTest(AzureFeedbackCase):
    """Live (23.11): a comment that `az` refused (nonzero exit, nothing written) left `F2.sending.json` with `uncertain: false`, and the next `send` was
    stopped as if the outcome were unknown. A clean failure may be retried; an uncertain one stays for a human."""

    def first_item(self):
        _, first = self.rendered()
        self.send(first, 'F1')

    def test_a_comment_that_failed_cleanly_can_be_sent_again(self):
        self.first_item()
        _, second = self.rendered(title='Again')
        self.assertEqual((second['mode'], second['item']), ('comment', 100))
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'comment-fails'}):
            failed = self.send(second, 'F2', approval='a-2')
        self.assertEqual((failed['status'], failed['uncertain'], failed['mutation_completed']), ('FAIL', False, False))
        self.assertEqual(self.data()['comments'], [])
        self.assertFalse(json.loads((self.store / 'F2.sending.json').read_text())['uncertain'])
        again = feedback.send_payload(self.context, self.store, 'F2', second, approval_id='a-2')   # the same consent, the same content
        self.assertEqual((again['status'], again['mode']), ('PASS', 'comment'))
        self.assertEqual(len(self.data()['comments']), 1)
        self.assertTrue((self.store / 'F2.sent.json').exists())
        self.assertFalse((self.store / 'F2.sending.json').exists())

    def test_an_uncertain_comment_stays_blocked_for_a_human(self):
        self.first_item()
        _, second = self.rendered(title='Again')
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'comment-lost'}):
            lost = self.send(second, 'F2', approval='a-2')
        self.assertEqual((lost['status'], lost['uncertain']), ('BLOCKED', True))
        self.assertTrue(json.loads((self.store / 'F2.sending.json').read_text())['uncertain'])
        with self.assertRaises(feedback.FeedbackError) as caught:
            feedback.send_payload(self.context, self.store, 'F2', second, approval_id='a-2')
        self.assertIn('cannot be confirmed', str(caught.exception))
        self.assertEqual(self.data()['comments'], [])

    def test_an_intent_without_the_flag_is_treated_as_uncertain(self):
        self.first_item()
        _, second = self.rendered(title='Again')
        feedback.save_draft(self.store, DRAFT, 'F2')
        payload = feedback.final_payload(second)
        (self.store / 'F2.sending.json').write_text(json.dumps({'sha': payload['sha'], 'tag': payload['tag'], 'mode': 'comment', 'item': 100}), encoding='utf-8')
        with self.assertRaises(feedback.FeedbackError):
            feedback.send_payload(self.context, self.store, 'F2', second)

    def test_a_create_that_failed_cleanly_is_still_retried(self):
        _, rendered = self.rendered()
        with patch.dict(os.environ, {'FAKE_AZ_MODE': 'create-fails'}):
            self.assertEqual(self.send(rendered)['status'], 'FAIL')
        self.assertEqual(feedback.send_payload(self.context, self.store, 'F1', rendered, approval_id='a-1')['status'], 'PASS')


class CommentResumeTest(AzureFeedbackCase):
    def test_an_interrupted_comment_is_confirmed_by_its_marker_and_not_repeated(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        _, second = self.rendered(title='Again')
        feedback.save_draft(self.store, DRAFT, 'F2')
        payload = feedback.final_payload(second)
        # the comment was written but the run died before `F2.sent.json`
        state = self.data()
        state['comments'].append({'id': 500, 'text': f'[{payload["tag"]}]\nbody'})
        self.state.write_text(json.dumps(state), encoding='utf-8')
        (self.store / 'F2.sending.json').write_text(json.dumps({'sha': payload['sha'], 'tag': payload['tag'], 'mode': 'comment', 'item': 100}), encoding='utf-8')
        result = feedback.send_payload(self.context, self.store, 'F2', second, approval_id='a-2')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(len(self.data()['comments']), 1)

    def test_an_unconfirmed_interrupted_comment_stops_for_a_human(self):
        _, first = self.rendered()
        self.send(first, 'F1')
        _, second = self.rendered(title='Again')
        feedback.save_draft(self.store, DRAFT, 'F2')
        payload = feedback.final_payload(second)
        (self.store / 'F2.sending.json').write_text(json.dumps({'sha': payload['sha'], 'tag': payload['tag'], 'mode': 'comment', 'item': 100}), encoding='utf-8')
        with self.assertRaises(feedback.FeedbackError):
            feedback.send_payload(self.context, self.store, 'F2', second)
        self.assertEqual(self.data()['comments'], [])


if __name__ == '__main__':
    unittest.main()
