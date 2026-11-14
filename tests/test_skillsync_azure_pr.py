"""Azure DevOps pull-request operations of the library tracker descriptor, run against a fake `az`.

The fake CLI proves our argv templates, result contracts and readback guards; it does not prove
the real `az` output (no live Azure access in tests). The descriptor is the one of the library under test
(`SKILLSYNC_TEST_LIBRARY` or the checkout's `library/`), not of the machine: the tests never depend on an installed
library or on a real `az`. The fake runs as `python fake_az.py …` (argv[0] of every `az` operation is replaced in the copy
of the descriptor), so it needs neither a shebang nor `az` on PATH and works the same on Windows.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

from tests.test_skillsync_workflow import WorkflowFixture

LIBRARY_DESCRIPTOR = (Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or Path(__file__).resolve().parents[1] / 'library')
                      / 'workflow/trackers/azure.yaml')

FAKE_AZ = '''import json, os, sys
state_path = os.environ['FAKE_AZ_STATE']
state = json.load(open(state_path)) if os.path.exists(state_path) else {{'prs': [], 'threads': [], 'calls': []}}
args = sys.argv[1:]
state['calls'].append(args)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
def pr_view(pr):
    shown = dict(pr)
    if os.environ.get('FAKE_AZ_BREAK') == 'title':
        shown['title'] = 'someone else'
    return shown
out = None
if args[:3] == ['repos', 'pr', 'list']:
    prs = [pr_view(p) for p in state['prs']]
    out = (prs[0] if prs else None) if opt('--query') == '[0]' else prs
elif args[:3] == ['repos', 'pr', 'show']:
    out = next(p for p in state['prs'] if str(p['pullRequestId']) == opt('--id'))
elif args[:3] == ['repos', 'pr', 'create']:
    pr = {{'pullRequestId': len(state['prs']) + 1, 'title': opt('--title'), 'description': opt('--description'),
          'status': 'active', 'isDraft': opt('--draft') == 'true', 'sourceRefName': opt('--source-branch')}}
    state['prs'].append(pr)
    out = pr
elif args[:3] == ['repos', 'pr', 'update']:
    pr = next(p for p in state['prs'] if str(p['pullRequestId']) == opt('--id'))
    pr['isDraft'] = opt('--draft') == 'true'
    out = pr
elif args[:2] == ['devops', 'invoke'] and opt('--http-method') == 'POST':
    body = json.load(open(opt('--in-file')))
    thread = {{'id': 100 + len(state['threads']), 'comments': body['comments']}}
    state['threads'].append(thread)
    out = thread
elif args[:2] == ['devops', 'invoke'] and opt('--http-method') == 'GET':
    out = state['threads'][-1] if state['threads'] else None
json.dump(state, open(state_path, 'w'))
print(json.dumps(out))
'''


@unittest.skipUnless(LIBRARY_DESCRIPTOR.is_file(), 'Azure tracker descriptor of the library under test is absent')
class AzurePullRequestOperations(WorkflowFixture):
    PARAMS = {'organization': 'org', 'project': 'proj', 'repository': 'repo', 'source': 'feat/x', 'target': 'main',
              'title': 'ABC-1: zmiana', 'description': 'Opis\nz nową linią; $(echo nie)'}

    def setUp(self):
        super().setUp()
        target = self.library / 'workflow/trackers/azure.yaml'
        target.parent.mkdir(parents=True)
        bin_dir = self.base / 'bin'
        bin_dir.mkdir()
        az = bin_dir / 'fake_az.py'
        az.write_text(FAKE_AZ.format(), encoding='utf-8')   # `{{`: the template is a format string
        descriptor = yaml.safe_load(LIBRARY_DESCRIPTOR.read_text(encoding='utf-8'))
        for spec in descriptor['operations'].values():
            if spec.get('argv') and spec['argv'][0] == 'az':
                spec['argv'] = [sys.executable, str(az)] + spec['argv'][1:]
        target.write_text(yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True), encoding='utf-8')
        self.state = self.base / 'az-state.json'
        env = patch.dict(os.environ, {'FAKE_AZ_STATE': str(self.state)})
        env.start()
        self.addCleanup(env.stop)

    def tracker(self, operation, params, *flags, code=0):
        return self.report('tracker', '--provider', 'azure', '--operation', operation,
                           '--params', json.dumps(params), *flags, code=code)

    def calls(self):
        return json.loads(self.state.read_text())['calls'] if self.state.exists() else []

    def test_plan_and_write_guard_never_call_az(self):
        self.assertEqual(self.tracker('pr-create', self.PARAMS, code=2)['status'], 'BLOCKED')
        self.assertEqual(self.tracker('pr-list', {k: self.PARAMS[k] for k in ('organization', 'project', 'repository', 'source', 'target')})['status'], 'NOT_RUN')
        self.tracker('pr-create', self.PARAMS, '--run', code=2)
        self.assertEqual(self.calls(), [])

    def test_create_draft_passes_readback_with_separate_argv_elements(self):
        result = self.tracker('pr-create', self.PARAMS, '--run', '--allow-write')
        self.assertTrue(result['verified'])
        self.assertTrue(result['data']['isDraft'])
        create = next(call for call in self.calls() if call[:3] == ['repos', 'pr', 'create'])
        self.assertEqual(create[create.index('--title') + 1], self.PARAMS['title'])
        self.assertEqual(create[create.index('--description') + 1], self.PARAMS['description'])
        self.assertEqual(create[create.index('--organization') + 1], 'https://dev.azure.com/org')
        self.assertEqual(create[create.index('--draft') + 1], 'true')

    def test_readback_mismatch_blocks_after_mutation(self):
        with patch.dict(os.environ, {'FAKE_AZ_BREAK': 'title'}):
            result = self.tracker('pr-create', self.PARAMS, '--run', '--allow-write', code=2)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(result['mutation_completed'])

    def test_ready_flips_draft_and_is_verified(self):
        self.tracker('pr-create', self.PARAMS, '--run', '--allow-write')
        result = self.tracker('pr-ready', {'organization': 'org', 'id': '1'}, '--run', '--allow-write')
        self.assertTrue(result['verified'])
        self.assertFalse(result['data']['isDraft'])

    def test_empty_pr_list_is_a_pass(self):
        params = {k: self.PARAMS[k] for k in ('organization', 'project', 'repository', 'source', 'target')}
        result = self.tracker('pr-list', params, '--run')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['data'], [])

    def test_comment_thread_is_verified_by_last_thread(self):
        self.tracker('pr-create', self.PARAMS, '--run', '--allow-write')
        body = self.write('.skillsync/tmp/comment.json', json.dumps(
            {'comments': [{'parentCommentId': 0, 'content': '<!-- pr-run:summary -->\nOK', 'commentType': 1}], 'status': 1}))
        params = {'organization': 'org', 'project': 'proj', 'repository': 'repo', 'pr': '1', 'body_file': str(body)}
        result = self.tracker('pr-comment', params, '--run', '--allow-write')
        self.assertTrue(result['verified'])
        self.assertEqual(result['data']['id'], 100)  # id wątku różni się od numeru PR (regresja: kolizja nazwy `id` w verify.match)
        self.assertEqual(result['data']['comments'][0]['content'].splitlines()[0], '<!-- pr-run:summary -->')
        route = next(call for call in self.calls() if call[:2] == ['devops', 'invoke'] and '--in-file' in call)
        self.assertIn('pullRequestId=1', route)

    def test_credential_like_parameters_are_rejected(self):
        self.tracker('pr-show', {'organization': 'org', 'id': '1', 'token': 'never'}, code=2)


if __name__ == '__main__':
    unittest.main()
