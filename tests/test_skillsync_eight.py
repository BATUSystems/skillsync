"""Behavioral coverage of the eight workflow additions with isolated local fixtures."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import yaml
from tests.test_skillsync_workflow import WorkflowFixture, CLI

sys.path.insert(0, str(CLI.parent))
from skillsync_workflow.runs import fingerprint
from skillsync_workflow.storage import state_path
from skillsync_workflow import claims, maintenance, delivery, tracker_http, environment
from tests.layers_helpers import needs_os_symlinks  # noqa: E402
from tests.cli_module import skillsync as skillsync_cli  # noqa: E402


class EightContracts(WorkflowFixture):
    def git(self):
        result = subprocess.run(['git', 'init', '-q', str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def configure(self, section, value):
        self.manifest['workflow'][section] = value
        self.save()

    def provider(self, **changes):
        read = {'effect': 'read', 'required_params': ['id'],
                'argv': [sys.executable, '-c', 'import json; print(json.dumps(dict(id="X",title="new")))'],
                'result': {'type': 'object', 'required': ['id', 'title']}}
        write = dict(read, effect='write', required_params=['id', 'title'], verify={'operation': 'read', 'match': ['id', 'title']})
        descriptor = {'version': 1, 'capabilities': ['read', 'update-title'], 'operations': {'read': read, 'update-title': write}}
        descriptor.update(changes)
        self.write('.skillsync/providers/fake.yaml', yaml.safe_dump(descriptor))
        self.configure('delivery', {'provider': 'fake'})
        return descriptor

    def artifact(self, text='upstream'):
        source = self.library / 'workflow/assets/tool.txt'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(text)
        self.configure('upgrade', {'revision': 'v2', 'artifacts': [{'source': 'workflow/assets/tool.txt', 'target': 'docs/workflow/tool.txt'}]})
        return source

    def observe(self):
        self.git()
        check = self.check(kind='e2e')
        self.configure('e2e', {'checks': [check]})
        self.write('.skillsync/e2e/artifacts/flow.txt', 'Actually observed local fixture')
        data = {'id': 'flow', 'URL': 'http://127.0.0.1:8000/form', 'observed_at': datetime.now(timezone.utc).isoformat(),
                'source_fingerprint': fingerprint(self.root),
                'steps': [{'action': 'click', 'locator': 'observed-id', 'expected': 'observed-message'}],
                'evidence': ['.skillsync/e2e/artifacts/flow.txt']}
        self.write('.skillsync/e2e/input.json', json.dumps(data))
        return data

    def env_recipe(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.write('app.txt', 'v1')
        recipe = {'start': [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'],
                  'inputs': ['app.txt'], 'health': {'url': f'http://127.0.0.1:{port}/'},
                  'startup_timeout': 5, 'ttl': 100, 'resources': ['fixture-' + self.root.name + str(port)]}
        self.configure('environment', recipe)
        def cleanup():
            path = self.root / '.skillsync/environments/default.json'
            if path.exists():
                record = json.loads(path.read_text())
                if environment.owned(record):
                    environment.stop(record)
        self.addCleanup(cleanup)
        return recipe

    def test_unconfigured_commands_report_not_run(self):
        for name in ('env', 'e2e', 'upgrade', 'guard'):
            self.assertEqual(self.report(name)['status'], 'NOT_RUN')

    def test_claim_plan_creates_no_lease(self):
        self.report('claim', '--task-id', 'T', '--run-id', 'a', '--action', 'acquire')
        self.assertFalse((self.root / '.skillsync/claims/T.json').exists())

    def test_claim_foreign_owner_blocked_and_reentry_idempotent(self):
        options = ['--task-id', 'T', '--run-id', 'a', '--action', 'acquire', '--run']
        first = self.report('claim', *options)['lease']
        self.assertEqual(self.report('claim', *options)['lease'], first)
        self.assertEqual(self.report('claim', '--task-id', 'T', '--run-id', 'b', '--action', 'acquire', '--run', code=2)['status'], 'BLOCKED')
        self.report('claim', '--task-id', 'T', '--run-id', 'b', '--action', 'release', '--run', code=2)

    def test_claim_transfer_renew_and_release(self):
        claims.claim(self.root, 'T', 'a', 'acquire', run=True)
        moved = claims.claim(self.root, 'T', 'a', 'transfer', to_run_id='b', run=True)
        self.assertEqual(moved['lease']['run_id'], 'b')
        self.assertEqual(claims.claim(self.root, 'T', 'a', 'renew', run=True)['status'], 'BLOCKED')
        self.assertEqual(claims.claim(self.root, 'T', 'b', 'renew', run=True)['status'], 'PASS')
        self.assertEqual(claims.claim(self.root, 'T', 'b', 'release', run=True)['status'], 'PASS')

    def test_claim_expiry_needs_explicit_takeover_even_same_run(self):
        with patch('skillsync_workflow.claims.time.time', return_value=100):
            claims.claim(self.root, 'T', 'a', 'acquire', ttl=1, run=True)
        with patch('skillsync_workflow.claims.time.time', return_value=102):
            self.assertEqual(claims.claim(self.root, 'T', 'a', 'acquire', run=True)['status'], 'BLOCKED')
            self.assertEqual(claims.claim(self.root, 'T', 'b', 'acquire', takeover=True, run=True)['status'], 'PASS')

    def test_claim_competing_processes_have_one_winner(self):
        processes = [subprocess.Popen([sys.executable, str(CLI), 'claim', str(self.root), '--task-id', 'T', '--run-id', name, '--action', 'acquire', '--run'], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ('a', 'b')]
        results = [p.communicate(timeout=15) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 2], results)

    @needs_os_symlinks
    def test_storage_and_claim_reject_symlink_and_traversal(self):
        with self.assertRaises(ValueError):
            state_path(self.root, '../outside')
        (self.root / '.skillsync').symlink_to(self.library, target_is_directory=True)
        self.report('claim', '--task-id', 'T', '--run-id', 'a', '--action', 'acquire', '--run', code=2)
        self.assertFalse((self.library / 'claims').exists())

    def test_profiles_additive_and_generator_agrees(self):
        for name in ('first', 'core-skill'):
            path = self.library / 'skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('---\nname: ' + name + '\ndescription: useful skill\n---\n')
        path = self.library / 'workflow/skill-profiles.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({'version': 1, 'profiles': {'core': ['core-skill']}}))
        self.manifest['skills'] = ['first']
        self.configure('skill_profiles', {'selected': ['core']})
        report = self.report('profiles')
        names = skillsync_cli.expand_skills(['first'], self.library / 'skills', {}, self.manifest)
        self.assertEqual(names, report['skills'])
        self.assertEqual(set(names), {'first', 'core-skill'})
        self.configure('skill_profiles', {'selected': ['core'], 'catalog_budget_bytes': 0})
        self.assertEqual(self.report('profiles', code=1)['status'], 'FAIL')

    def test_profiles_invalid_selection_is_blocked(self):
        self.configure('skill_profiles', {'selected': ['missing']})
        self.report('profiles', code=2)

    def test_profiles_no_selection_preserves_legacy(self):
        self.configure('skill_profiles', {'selected': []})
        self.assertEqual(self.report('profiles')['skills'], [])

    def test_upgrade_plan_no_mutation_apply_and_rollback(self):
        self.artifact()
        plan = self.report('upgrade')
        self.assertEqual(plan['artifacts'][0]['decision'], 'create')
        target = self.root / 'docs/workflow/tool.txt'
        self.assertFalse(target.exists())
        applied = self.report('upgrade', '--action', 'apply', '--run')
        self.assertEqual(target.read_text(), 'upstream')
        self.report('upgrade', '--action', 'rollback', '--backup', applied['backup'], '--run')
        self.assertFalse(target.exists())

    def test_upgrade_first_adoption_conflict_preserves_local(self):
        self.artifact()
        target = self.write('docs/workflow/tool.txt', 'local')
        self.report('upgrade', '--action', 'apply', '--run', code=2)
        self.assertEqual(target.read_text(), 'local')

    def test_upgrade_three_way_local_and_remote_conflict(self):
        source = self.artifact()
        self.report('upgrade', '--action', 'apply', '--run')
        target = self.write('docs/workflow/tool.txt', 'local')
        self.assertEqual(self.report('upgrade')['artifacts'][0]['decision'], 'preserve-local')
        source.write_text('remote v3')
        self.report('upgrade', '--action', 'apply', '--run', code=2)
        self.assertEqual(target.read_text(), 'local')

    def test_upgrade_updates_clean_target_and_rollback_restores(self):
        source = self.artifact()
        self.report('upgrade', '--action', 'apply', '--run')
        source.write_text('v3')
        applied = self.report('upgrade', '--action', 'apply', '--run')
        target = self.root / 'docs/workflow/tool.txt'
        self.assertEqual(target.read_text(), 'v3')
        self.report('upgrade', '--action', 'rollback', '--run')
        self.assertEqual(target.read_text(), 'upstream')

    def test_rollback_preserves_later_edits(self):
        self.artifact()
        self.report('upgrade', '--action', 'apply', '--run')
        target = self.write('docs/workflow/tool.txt', 'later')
        self.report('upgrade', '--action', 'rollback', '--run', code=2)
        self.assertEqual(target.read_text(), 'later')

    @needs_os_symlinks
    def test_upgrade_rejects_app_targets_and_symlinks(self):
        self.artifact()
        self.manifest['workflow']['upgrade']['artifacts'][0]['target'] = 'app.py'
        self.save()
        self.report('upgrade', '--action', 'apply', '--run', code=2)
        self.manifest['workflow']['upgrade']['artifacts'][0]['target'] = 'docs/workflow/tool.txt'
        self.save()
        (self.root / 'docs').symlink_to(self.library, target_is_directory=True)
        self.report('upgrade', '--action', 'apply', '--run', code=2)

    def test_guard_reports_file_line_without_contents(self):
        self.configure('guardrails', {'rules': [{'id': 'debug', 'paths': ['src/*.ts'], 'forbidden': r'\bdebugger\s*;'}]})
        self.write('src/app.ts', 'let a = 1;\ndebugger;\n')
        result = self.report('guard', '--files', 'src/app.ts', '--run', code=1)
        self.assertEqual(result['findings'][0]['line'], 2)
        self.assertNotIn('debugger;', json.dumps(result))

    def test_guard_warning_required_and_plan(self):
        self.configure('guardrails', {'rules': [{'id': 'require', 'paths': ['*.py'], 'required': 'typing', 'severity': 'warning'}]})
        self.write('a.py', 'print(1)')
        self.assertEqual(self.report('guard', '--files', 'a.py')['status'], 'NOT_RUN')
        self.assertEqual(self.report('guard', '--files', 'a.py', '--run')['status'], 'PASS')

    def test_guard_path_escape_blocked(self):
        self.configure('guardrails', {'rules': [{'id': 'rule', 'paths': ['**'], 'forbidden': 'X'}]})
        self.report('guard', '--files', '../private', '--run', code=2)

    def test_guard_integrated_in_validation(self):
        self.configure('guardrails', {'rules': [{'id': 'rule', 'paths': ['*.py'], 'forbidden': 'bad'}]})
        self.write('a.py', 'bad')
        plan = self.report('validate', '--profile', 'final', '--files', 'a.py')
        self.assertEqual(plan['checks'][0]['id'], 'project-guard')
        from skillsync_workflow.selfcmd import self_argv
        argv = plan['checks'][0]['argv']
        self.assertEqual(argv[:len(self_argv()) + 1], self_argv() + ['guard'])
        self.assertEqual(argv[-2:], ['--files', 'a.py'])
        self.assertIn('--json', argv)
        self.report('validate', '--profile', 'final', '--files', 'a.py', '--run', code=1)

    def test_guard_native_checks_without_recursion(self):
        self.configure('guardrails', {'checks': [self.check(kind='lint', output='lint passed')]})
        self.assertEqual(self.report('guard', '--files', '--run')['status'], 'PASS')

    def test_validate_guard_native_checks_avoid_nested_lock(self):
        self.configure('guardrails', {'checks': [self.check(kind='lint', output='lint passed')]})
        self.assertEqual(self.report('validate', '--profile', 'final', '--files', '--run')['status'], 'PASS')

    def test_upgrade_interruption_requires_rollback(self):
        self.artifact()
        with patch('skillsync_workflow.maintenance._replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                maintenance.upgrade(self.root, self.manifest, self.layerset(), 'apply', True)
        self.assertEqual(self.report('upgrade', code=2)['status'], 'BLOCKED')
        self.report('upgrade', '--action', 'rollback', '--run')
        self.assertEqual(self.report('upgrade')['status'], 'NOT_RUN')

    def test_e2e_scenario_tampering_rejected(self):
        self.observe()
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')
        path = self.root / '.skillsync/e2e/scenarios/flow.json'
        data = json.loads(path.read_text())
        data['steps'][0]['expected'] = 'made up'
        path.write_text(json.dumps(data))
        self.report('e2e', '--action', 'run', '--run', code=2)

    def test_e2e_source_changed_during_runner_is_blocked(self):
        self.observe()
        check = self.check(kind='e2e')
        check['argv'] = [sys.executable, '-c', 'from pathlib import Path; Path("application.py").write_text("changed"); print("1 passed")']
        self.configure('e2e', {'checks': [check]})
        self.observe_input_after_change()
        result = self.report('e2e', '--action', 'run', '--run', code=2)
        self.assertIn('Source changed', result['reason'])

    def test_tracker_timeout_cleans_descendants(self):
        data = self.provider()
        pid_file = self.root / '.skillsync/child-pids.json'
        data['operations']['read']['timeout'] = .2
        data['operations']['read']['argv'] = [sys.executable, '-c', self.sleeping_tree_script(pid_file)]
        self.write('.skillsync/providers/fake.yaml', yaml.safe_dump(data))
        result = self.report('tracker', '--operation', 'read', '--params', '{"id":"X"}', '--run', code=2)
        self.assertIn('cleaned up', result['reason'])
        pids = json.loads(pid_file.read_text())
        for pid in pids:
            self.assertIsNone(environment.identity(pid), str(pid))

    def test_tracker_no_redirects_with_auth(self):
        handler = tracker_http.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, 'redirect', {}, 'https://other.example'))

    def test_builtin_write_plan_never_contacts_server(self):
        path = self.library / 'workflow/trackers/github.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump({'version': 1, 'capabilities': ['read'], 'operations': {
            'read': {'effect': 'read', 'builtin': 'github', 'required_params': ['repo', 'id'],
                     'result': {'type': 'object', 'required': ['id', 'title']}}}}))
        with patch('skillsync_workflow.tracker_http.invoke', side_effect=AssertionError('network')):
            result = delivery.tracker(self.root, self.manifest, self.layerset(), 'read', {'repo': 'o/r', 'id': '1'}, provider='github')
        self.assertEqual(result['status'], 'NOT_RUN')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux ownership backend')
    def test_environment_prepare_can_share_parent_resource(self):
        recipe = self.env_recipe()
        recipe['prepare_checks'] = [self.check(kind='lint', resources=recipe['resources'])]
        self.configure('environment', recipe)
        self.assertEqual(self.report('env', '--action', 'up', '--run')['status'], 'PASS')
        self.report('env', '--action', 'down', '--run')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux ownership backend')
    def test_environment_child_resisting_term_is_cleaned(self):
        recipe = self.env_recipe()
        script = ('import subprocess,sys,time; from pathlib import Path; '
                  'child=subprocess.Popen([sys.executable,"-c","import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"]); '
                  'Path(".skillsync/child-id").write_text(str(child.pid)); time.sleep(60)')
        recipe['start'] = [sys.executable, '-c', script]
        recipe['health'] = {'argv': [sys.executable, '-c', 'from pathlib import Path; raise SystemExit(0 if Path(".skillsync/child-id").exists() else 1)']}
        self.configure('environment', recipe)
        self.report('env', '--action', 'up', '--run')
        pid = int((self.root / '.skillsync/child-id').read_text())
        self.addCleanup(lambda: os.kill(pid, signal.SIGKILL) if environment.identity(pid) else None)
        self.report('env', '--action', 'down', '--run')
        deadline = time.monotonic() + 2
        while environment.identity(pid) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(environment.identity(pid))

    def test_tracker_plan_and_write_guard(self):
        self.provider()
        self.assertEqual(self.report('tracker', '--operation', 'read', '--params', '{"id":"X"}')['status'], 'NOT_RUN')
        self.report('tracker', '--operation', 'update-title', '--params', '{"id":"X","title":"new"}', '--run', code=2)

    def test_tracker_result_and_readback_verified(self):
        self.provider()
        read = self.report('tracker', '--operation', 'read', '--params', '{"id":"X"}', '--run')
        self.assertEqual(read['data']['title'], 'new')
        write = self.report('tracker', '--operation', 'update-title', '--params', '{"id":"X","title":"new"}', '--run', '--allow-write')
        self.assertTrue(write['verified'])

    def test_tracker_wrong_requested_title_cannot_pass_readback(self):
        self.provider()
        result = self.report('tracker', '--operation', 'update-title', '--params', '{"id":"X","title":"different"}', '--run', '--allow-write', code=2)
        self.assertTrue(result['mutation_completed'])

    def test_tracker_capability_and_strict_parameters(self):
        self.provider()
        self.report('tracker', '--operation', 'unsupported', code=2)
        self.report('tracker', '--operation', 'read', '--params', '{"id":"X","token":"never"}', code=2)
        self.manifest['workflow']['delivery']['required_capabilities'] = ['comment']
        self.save()
        self.report('tracker', '--operation', 'read', '--params', '{"id":"X"}', code=2)

    def test_tracker_invalid_result_contract(self):
        data = self.provider()
        data['operations']['read']['argv'] = [sys.executable, '-c', 'print("[]")']
        self.write('.skillsync/providers/fake.yaml', yaml.safe_dump(data))
        self.report('tracker', '--operation', 'read', '--params', '{"id":"X"}', '--run', code=2)

    def test_tracker_secret_result_redacted(self):
        self.provider()
        with patch.dict(os.environ, {'TEST_SECRET': 'sensitive-value'}):
            self.assertEqual(delivery._scrub({'token': 'abc', 'data': 'sensitive-value'}), {'token': '[REDACTED]', 'data': '[REDACTED]'})

    def test_builtin_tracker_normalizes_three_providers(self):
        fixtures = {
            'github': ({'repo': 'owner/repo', 'id': '1'}, {'number': 1, 'title': 'T', 'state': 'open', 'html_url': 'https://github.com/owner/repo/issues/1'}),
            'jira': ({'id': 'ABC-1'}, {'key': 'ABC-1', 'fields': {'summary': 'T', 'status': {'name': 'Open'}}}),
            'azure': ({'organization': 'org', 'project': 'project', 'id': '1'}, {'id': 1, 'fields': {'System.Title': 'T', 'System.State': 'New'}, '_links': {'html': {'href': 'https://dev.azure.com/item/1'}}})}
        with patch.dict(os.environ, {'JIRA_URL': 'https://jira.example', 'JIRA_TOKEN': 'fixture', 'AZURE_DEVOPS_EXT_PAT': 'fixture'}):
            for provider, (params, response) in fixtures.items():
                with patch('skillsync_workflow.tracker_http._request', return_value=response):
                    actual = tracker_http.invoke(provider, 'read', params)
                    self.assertEqual(set(actual), {'provider', 'id', 'title', 'state', 'url'})
                    self.assertEqual(actual['provider'], provider)

    def test_builtin_tracker_rejects_http_and_path_injection(self):
        with self.assertRaises(ValueError):
            tracker_http._request('http://example.com', {})
        with self.assertRaises(ValueError):
            tracker_http.invoke('github', 'read', {'repo': '../bad', 'id': '1'})
        with patch.dict(os.environ, {'JIRA_URL': 'https://user:pass@example.com'}):
            with self.assertRaises(ValueError):
                tracker_http.invoke('jira', 'read', {'id': 'ABC-1'})

    def test_ci_diagnosis_is_candidate_and_stale_report_blocked(self):
        self.git()
        self.write('.skillsync/ci/check.log', 'connection refused')
        report = {'source': fingerprint(self.root), 'checks': [{'name': 'unit', 'status': 'FAIL', 'log': '.skillsync/ci/check.log'}]}
        self.write('.skillsync/ci/input.json', json.dumps(report))
        actual = self.report('ci', '--report', '.skillsync/ci/input.json')
        self.assertEqual(actual['checks'][0]['cause'], 'environment')
        self.assertFalse(actual['checks'][0]['cause_verified'])
        self.write('changed.py', 'print(1)')
        self.report('ci', '--report', '.skillsync/ci/input.json', code=2)

    def test_ci_assertion_not_automatically_test_bug(self):
        self.assertEqual(delivery._diagnosis('AssertionError: expected 1 got 2'), 'implementation')
        self.assertEqual(delivery._diagnosis('stale locator'), 'test')
        self.assertEqual(delivery._diagnosis('flaky retry succeeded'), 'flaky')

    def test_ci_local_rerun_does_not_attest_remote_ci(self):
        self.git()
        self.checks(self.check())
        self.configure('delivery', {'ci': {'max_iterations': 1, 'rerun_check_ids': ['unit']}})
        self.write('.skillsync/ci/check.log', 'AssertionError')
        self.write('.skillsync/ci/input.json', json.dumps({'source': fingerprint(self.root), 'checks': [{'name': 'remote-unit', 'status': 'FAIL', 'log': '.skillsync/ci/check.log'}]}))
        result = self.report('ci', '--report', '.skillsync/ci/input.json', '--run')
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['ci_status'], 'NOT_RUN')
        self.assertEqual(len(result['iterations']), 1)

    def test_ci_iteration_limit_and_untrusted_commands_blocked(self):
        self.configure('delivery', {'ci': {'max_iterations': 4}})
        self.report('ci', '--report', 'input.json', '--run', code=2)

    def test_e2e_record_is_not_run_then_native_execution(self):
        self.observe()
        recorded = self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')
        self.assertEqual(recorded['status'], 'NOT_RUN')
        result = self.report('e2e', '--action', 'run', '--run')
        self.assertEqual(result['results'][0]['tests_run'], 1)

    def test_e2e_rejects_stale_sources_evidence_and_scenario(self):
        self.observe()
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')
        self.write('.skillsync/e2e/artifacts/flow.txt', 'changed')
        self.report('e2e', '--action', 'run', '--run', code=2)

    def test_e2e_requires_native_e2e_check_and_nonzero_tests(self):
        self.observe()
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')
        self.write('.skillsync/e2e/observations/flow.json', json.dumps(dict(self.observe(), source_fingerprint=fingerprint(self.root))))
        # Changes to configuration require a newly recorded observation.
        self.configure('e2e', {'checks': [self.check(kind='e2e', output='Ran 0 tests')]})
        self.observe_input_after_change()
        self.report('e2e', '--action', 'run', '--run', code=2)

    def observe_input_after_change(self):
        path = self.root / '.skillsync/e2e/input.json'
        data = json.loads(path.read_text())
        data['source_fingerprint'] = fingerprint(self.root)
        path.write_text(json.dumps(data))
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')

    def test_e2e_rejects_commands_in_observation(self):
        data = self.observe()
        data['steps'][0]['argv'] = ['echo', 'danger']
        self.write('.skillsync/e2e/input.json', json.dumps(data))
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run', code=2)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux ownership backend')
    def test_environment_cold_warm_freshness_and_cleanup(self):
        self.env_recipe()
        first = self.report('env', '--action', 'up', '--run')
        self.assertEqual(first['start'], 'cold')
        self.assertEqual(self.report('env', '--action', 'up', '--run')['start'], 'warm')
        self.write('app.txt', 'changed')
        self.assertFalse(self.report('env', '--action', 'status', code=2)['fresh'])
        self.assertEqual(self.report('env', '--action', 'up', '--run')['start'], 'cold')
        self.report('env', '--action', 'down', '--run')
        self.assertFalse((self.root / '.skillsync/environments/default.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux ownership backend')
    def test_environment_failed_health_cleans_own_process(self):
        recipe = self.env_recipe()
        recipe['health'] = {'argv': [sys.executable, '-c', 'raise SystemExit(1)']}
        recipe['startup_timeout'] = .2
        self.configure('environment', recipe)
        self.report('env', '--action', 'up', '--run', code=1)
        self.assertFalse((self.root / '.skillsync/environments/default.json').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'Linux ownership backend')
    def test_environment_foreign_record_never_signalled(self):
        self.env_recipe()
        path = self.write('.skillsync/environments/default.json', json.dumps({'owner': environment.identity(os.getpid()), 'created_at': time.time(), 'fingerprint': 'fake'}))
        self.report('env', '--action', 'down', '--run', code=2)
        self.assertTrue(path.exists())

    def test_environment_windows_is_explicitly_blocked(self):
        self.env_recipe()
        with patch('skillsync_workflow.environment.sys.platform', 'win32'):
            result = environment.environment(self.root, self.manifest, self.layerset(), 'up', True)
        self.assertEqual(result['status'], 'BLOCKED')

    def test_invalid_regex_and_health_url_report_blocked(self):
        self.configure('guardrails', {'rules': [{'id': 'bad', 'paths': ['*.py'], 'forbidden': '['}]})
        self.report('guard', '--files', 'x.py', '--run', code=2)
        recipe = self.env_recipe()
        recipe['health'] = {'url': 123}
        self.configure('environment', recipe)
        self.report('env', code=2)

    def test_malformed_yaml_reports_blocked_without_traceback(self):
        self.write('.github/.skillsync.yaml', 'workflow: [invalid')
        self.report('claim', '--task-id', 'T', code=2)
        self.report('tracker', '--operation', 'read', code=2)

    def test_builtin_cannot_misdeclare_write_as_read(self):
        data = self.provider()
        data['operations']['update-title'].update(effect='read', builtin='github')
        del data['operations']['update-title']['argv']
        self.write('.skillsync/providers/fake.yaml', yaml.safe_dump(data))
        self.report('tracker', '--operation', 'update-title', '--params', '{"id":"X","title":"new"}', '--run', code=2)

    def test_e2e_redacts_sensitive_step_keys(self):
        data = self.observe()
        data['steps'][0]['expected'] = {'message': 'ok', 'password': 'unlisted-secret'}
        self.write('.skillsync/e2e/input.json', json.dumps(data))
        self.report('e2e', '--action', 'record', '--observation', '.skillsync/e2e/input.json', '--run')
        text = (self.root / '.skillsync/e2e/observations/flow.json').read_text()
        self.assertNotIn('unlisted-secret', text)
        self.assertIn('[REDACTED]', text)


if __name__ == '__main__':
    unittest.main()
