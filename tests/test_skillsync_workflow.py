"""Behavioral contracts for local quality planning, routing and test selection."""
import hashlib
import json
import os
import shutil
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unittest import mock  # noqa: E402
from layers_helpers import generator_version, make_layer, real_state_snapshot, transitional_config  # noqa: E402
from layers_helpers import needs_fifo, needs_flock, needs_os_symlinks, needs_posix  # noqa: E402

CLI = Path(__file__).resolve().parents[1] / 'skillsync'


class WorkflowFixture(unittest.TestCase):
    """Project + one-layer library. Since step 2.3 the workflow modules read layers only from the machine
    config (K3), so the fixture points `SKILLSYNC_CONFIG` (inherited by the CLI subprocesses) at its own
    config: `core` = `self.library` (valid `layer.yaml`, README, CHANGELOG; K12) and a rule for the
    fixture directory. The manifest still carries `sync.library` (same path), but no module reads it: layers come only
    from the machine config (K3). The per-user cache and data
    dirs (`XDG_CACHE_HOME`, `XDG_DATA_HOME`: composites, layer checkouts, skills index) point inside the
    fixture too (created on demand); a cleanup proves the real `~/.cache/skillsync` and
    `~/.local/share/skillsync` stay untouched. `HOME` stays real, except on macOS, where the per-user dirs live
    under `~/Library` and only `HOME` can move them into the fixture."""

    def setUp(self):
        real_state = real_state_snapshot()
        self.addCleanup(lambda: self.assertEqual(real_state_snapshot(), real_state,
                                                 'test touched real Skillsync state dirs'))
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-contract-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.library = self.base / 'library'
        self.root.mkdir()
        self.library.mkdir()
        self.machine_config = self.base / 'skillsync-config.yaml'
        self.use_layers()
        isolated = {'SKILLSYNC_CONFIG': str(self.machine_config),
                    'XDG_CACHE_HOME': str(self.base / 'xdg-cache'),
                    'XDG_DATA_HOME': str(self.base / 'xdg-data')}
        if sys.platform == 'darwin':
            # macOS keeps the per-user dirs under `~/Library` (not XDG): only `HOME` moves them into the fixture.
            home = self.base / 'machine-home'
            home.mkdir()
            isolated['HOME'] = str(home)
        environ = mock.patch.dict(os.environ, isolated)
        environ.start()
        self.addCleanup(environ.stop)
        self.manifest = {'sync': {'library': str(self.library)}, 'workflow': {}}
        self.save()

    def use_layers(self, *layers, **extra):
        """Machine config of the fixture. `layers`: (id, kind, path); default = `core` at `self.library`.
        Each layer gets a valid `layer.yaml` (README/CHANGELOG too) when it has none; one rule matches
        everything under the fixture directory."""
        layers = layers or (('core', 'core', self.library),)
        entries = []
        for layer_id, kind, path in layers:
            path = Path(path)
            if not (path / 'layer.yaml').exists():
                make_layer(path, layer_id, kind, version=generator_version() if kind == 'core' else '1.0.0',
                           readme=not (path / 'README.md').exists(), changelog=not (path / 'CHANGELOG.md').exists())
            entries.append({'id': layer_id, 'kind': kind, 'path': str(path), 'source': str(path)})
        data = {'layers': entries,
                'layer_rules': [{'match': [self.base.resolve().as_posix() + '/**'], 'use': [e['id'] for e in entries]}]}
        data.update(extra)
        self.machine_config.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
        return self.machine_config

    def save(self):
        self.write('.github/.skillsync.yaml', yaml.safe_dump(self.manifest))

    def layerset(self):
        """The `LayerSet` the CLI resolves for the fixture project (in-process calls of workflow modules, K1)."""
        from skillsync_workflow.common import load_layer_project
        return load_layer_project(self.root)[2]

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def cli(self, command, *args):
        return subprocess.run([sys.executable, str(CLI), command, str(self.root), *map(str, args), '--json'],
                              capture_output=True, text=True, timeout=20, cwd=self.root)

    def report(self, command, *args, code=0):
        result = self.cli(command, *args)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)

    def rejected(self, command, *args):
        result = self.cli(command, *args)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        return result

    def check(self, ident='unit', output='1 passed', **fields):
        return {'id': ident, 'argv': [sys.executable, '-c', f'print({output!r})'], 'kind': 'test', **fields}

    def checks(self, *checks):
        self.manifest['workflow']['validation'] = {'checks': list(checks)}
        self.save()

    def cancel_cli_with_tree(self, command, args, pid_file):
        process = subprocess.Popen([sys.executable, str(CLI), command, str(self.root), *map(str, args), '--json'],
                                   cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        children = []
        try:
            deadline = time.monotonic() + 8
            while not pid_file.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pid_file.exists(), 'Test child did not become ready')
            children = json.loads(pid_file.read_text())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=12)
            self.assertNotEqual(process.returncode, 0, stdout + stderr)
            for pid in children:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    try:
                        state = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0]
                    except FileNotFoundError:
                        break
                    if state == 'Z':
                        break
                    time.sleep(.02)
                else:
                    self.fail(f'Cancellation left live process {pid}')
            return stdout, stderr
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            for pid in children:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def sleeping_tree_script(self, pid_file):
        return ('import os, json, subprocess, sys, time; from pathlib import Path; '
                'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"]); '
                f'Path({str(pid_file)!r}).write_text(json.dumps([os.getpid(),child.pid])); '
                'time.sleep(120)')

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def init_git(self):
        self.git('init', '-q')
        self.git('config', 'user.email', 'tests@example.invalid')
        self.git('config', 'user.name', 'Contract Tests')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture baseline')


class FixtureIsolation(WorkflowFixture):
    def test_the_cli_reads_machine_state_only_inside_the_fixture(self):
        probe = ('import json, sys; sys.path.insert(0, sys.argv[1]); '
                 'from skillsync_workflow import common, layers; '
                 'print(json.dumps([str(common.config_file()), str(layers.cache_home()), str(layers.composed_root()), '
                 'str(layers.data_home())]))')
        result = subprocess.run([sys.executable, '-c', probe, str(CLI.parent)], capture_output=True, text=True,
                                timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in json.loads(result.stdout):
            self.assertTrue(Path(path).is_relative_to(self.base), path)


class ValidationContracts(WorkflowFixture):
    def test_plan_does_not_execute_and_preserves_manifest_order(self):
        sentinel = self.root / 'executed'
        argv = [sys.executable, '-c', f'from pathlib import Path; Path({str(sentinel)!r}).touch(); print("1 passed")']
        self.checks(self.check('z', argv=argv), self.check('a'))
        report = self.report('validate')
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertEqual([c['id'] for c in report['checks']], ['z', 'a'])
        self.assertFalse(sentinel.exists())
        self.assertFalse((self.root / '.skillsync/logs').exists())

    def test_execution_is_ordered_and_records_logs_counts(self):
        self.checks(self.check('first', argv=[sys.executable, '-c', 'from pathlib import Path; Path("first").touch(); print("2 passed")']),
                    self.check('second', argv=[sys.executable, '-c', 'from pathlib import Path; assert Path("first").exists(); print("3 passed")']))
        report = self.report('validate', '--run')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([c['tests_run'] for c in report['results']], [2, 3])
        self.assertTrue(all(Path(c['log']).is_file() for c in report['results']))
        self.assertTrue(all(c['exit_code'] == 0 for c in report['results']))

    def test_zero_skipped_and_unknown_test_counts_cannot_pass(self):
        for output in ['Ran 0 tests\nOK', 'Ran 2 tests\nOK (skipped=2)', '1 passed, 1 skipped', 'all good']:
            with self.subTest(output=output):
                self.checks(self.check(output=output))
                self.assertEqual(self.report('validate', '--run', code=2)['status'], 'BLOCKED')

    @needs_flock
    def test_fail_missing_environment_timeout_and_busy_resource(self):
        cases = [self.check(argv=[sys.executable, '-c', 'raise SystemExit(7)']),
                 self.check(requires_env=['SKILLSYNC_TEST_MISSING_' + uuid.uuid4().hex]),
                 self.check(argv=[sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.03)]
        for check, status, code in zip(cases, ['FAIL', 'BLOCKED', 'BLOCKED'], [1, 2, 2]):
            with self.subTest(status=status):
                self.checks(check)
                self.assertEqual(self.report('validate', '--run', code=code)['status'], status)
        resource = 'isolated-test-' + uuid.uuid4().hex
        directory = Path(tempfile.gettempdir()) / 'skillsync-resource-locks'
        directory.mkdir(exist_ok=True)
        path = directory / (hashlib.sha256(resource.encode()).hexdigest() + '.lock')
        import fcntl  # POSIX only: the test carries @needs_flock
        with path.open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.checks(self.check(resources=[resource]))
            self.assertEqual(self.report('validate', '--run', code=2)['status'], 'BLOCKED')
        path.unlink()

    def test_legacy_test_command_still_plans_and_runs(self):
        import shlex
        self.manifest['tests'] = {'command': shlex.join([sys.executable, '-c', 'print("Ran 1 test\\nOK")'])}
        self.save()
        self.assertEqual(self.report('validate')['checks'][0]['id'], 'legacy-tests')
        self.assertEqual(self.report('validate', '--run')['status'], 'PASS')

    def test_unknown_check_no_checks_and_invalid_timeout_block(self):
        self.checks(self.check())
        self.assertEqual(self.report('validate', '--check', 'missing', code=2)['status'], 'BLOCKED')
        self.checks()
        self.assertEqual(self.report('validate', '--run', code=2)['status'], 'BLOCKED')
        for timeout in [-1, 0, float('nan'), float('inf'), 86401]:
            with self.subTest(timeout=timeout):
                self.checks(self.check(timeout=timeout))
                self.rejected('validate', '--run')

    def test_scalar_sections_and_unsafe_yaml_fail_gracefully(self):
        for manifest in [{'sync': 4}, {'workflow': {'validation': 4}}, {'workflow': {'routing': 4}}]:
            with self.subTest(manifest=manifest):
                self.manifest = manifest
                self.save()
                self.rejected('route' if 'routing' in manifest.get('workflow', {}) else 'validate')
        self.write('.github/.skillsync.yaml', '!!python/object/apply:os.system ["touch NEVER"]')
        self.rejected('validate')
        self.assertFalse((self.root / 'NEVER').exists())

    def test_missing_executable_and_success_evidence_block(self):
        self.checks(self.check(requires_executables=['skillsync-missing-' + uuid.uuid4().hex]))
        self.assertEqual(self.report('validate', '--run', code=2)['status'], 'BLOCKED')
        self.checks(self.check(kind='build', output='build complete', success_pattern='VERIFIED'))
        self.assertEqual(self.report('validate', '--run', code=2)['status'], 'BLOCKED')

    def test_unsafe_check_id_and_working_directory_are_refused(self):
        for fields in [{'id': '../outside'}, {'cwd': '..'}, {'cwd': str(self.base)},
                       {'required': 'false'}, {'profiles': 'quick'}, {'resources': 4}]:
            with self.subTest(fields=fields):
                self.checks(self.check(**fields))
                self.rejected('validate', '--run')

    def test_all_scalar_workflow_sections_and_version_are_rejected(self):
        for key in ['context', 'routing', 'validation', 'testing', 'readiness', 'review']:
            with self.subTest(key=key):
                self.manifest['workflow'] = {key: None}
                self.save()
                self.rejected('validate')
        self.manifest['workflow'] = {'version': 999}
        self.save()
        self.rejected('validate')

    def test_optional_failure_does_not_hide_required_failure(self):
        self.checks(self.check('optional', argv=[sys.executable, '-c', 'raise SystemExit(3)'], required=False),
                    self.check('required', argv=[sys.executable, '-c', 'raise SystemExit(5)']))
        report = self.report('validate', '--run', code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual([r['exit_code'] for r in report['results']], [3, 5])

    def test_plan_redacts_secret_without_mutating_command_execution(self):
        key = 'SKILLSYNC_TEST_SECRET'
        secret = 'plan-private-' + uuid.uuid4().hex
        target = self.root / 'actual-argument'
        self.checks(self.check(argv=[sys.executable, '-c', f'from pathlib import Path; Path({str(target)!r}).write_text({secret!r}); print("1 passed")']))
        env = {**os.environ, key: secret}
        planned = subprocess.run([sys.executable, str(CLI), 'validate', str(self.root), '--json'],
                                 env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        self.assertNotIn(secret, planned.stdout + planned.stderr)
        self.assertFalse(target.exists())
        executed = subprocess.run([sys.executable, str(CLI), 'validate', str(self.root), '--run', '--json'],
                                  env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        self.assertEqual(target.read_text(), secret)
        self.assertNotIn(secret, executed.stdout + executed.stderr)

    @needs_posix
    def test_interrupt_terminates_child_tree_before_resource_is_available(self):
        resource = 'interrupt-contract-' + uuid.uuid4().hex
        pid_file = self.root / 'child-pids.json'
        self.checks(self.check(argv=[sys.executable, '-c', self.sleeping_tree_script(pid_file)],
                               resources=[resource], timeout=180))
        stdout, stderr = self.cancel_cli_with_tree('validate', ['--run'], pid_file)
        self.assertNotIn('Traceback', stderr)
        self.assertEqual(json.loads(stdout)['status'], 'BLOCKED')
        self.checks(self.check(resources=[resource]))
        self.assertEqual(self.report('validate', '--run')['status'], 'PASS')

    @needs_posix
    def test_interrupt_does_not_start_later_checks(self):
        pid_file = self.root / 'interrupted-pids.json'
        marker = self.root / 'later-check-ran'
        self.checks(self.check('first', argv=[sys.executable, '-c', self.sleeping_tree_script(pid_file)], timeout=180),
                    self.check('later', argv=[sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch(); print("1 passed")']))
        stdout, stderr = self.cancel_cli_with_tree('validate', ['--run'], pid_file)
        self.assertFalse(marker.exists(), 'Cancellation must stop the whole validation batch')
        self.assertNotIn('Traceback', stderr)
        self.assertEqual(json.loads(stdout)['status'], 'BLOCKED')

    def test_secret_values_are_redacted_from_execution_logs(self):
        key = 'SKILLSYNC_TEST_SECRET'
        secret = 'unique-private-value-' + uuid.uuid4().hex
        self.checks(self.check(argv=[sys.executable, '-c', f'import os; print(os.environ[{key!r}]); print("1 passed")']))
        result = subprocess.run([sys.executable, str(CLI), 'validate', str(self.root), '--run', '--json'],
                                env={**os.environ, key: secret}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertNotIn(secret, Path(report['results'][0]['log']).read_text())


class ContextContracts(WorkflowFixture):
    def test_routing_and_lessons_filter_by_paths_and_topics(self):
        self.manifest['workflow']['routing'] = {'rules': [
            {'id': 'api', 'paths': ['src/api/**'], 'topics': ['contracts']},
            {'id': 'ui', 'paths': ['ui/**'], 'topics': ['visual']} ]}
        self.save()
        lessons = self.library / 'workflow/lessons'
        lessons.mkdir(parents=True)
        (lessons / 'api.md').write_text('---\ntags: [contracts]\n---\nUseful API lesson')
        (lessons / 'ui.md').write_text('---\ntags: [visual]\n---\nUseful UI lesson')
        (lessons / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'lessons': [
            {'path': 'api.md', 'title': 'API', 'tags': ['contracts']},
            {'path': 'ui.md', 'title': 'UI', 'tags': ['visual']}]}))
        report = self.report('route', '--files', 'src/api/item.py', '--topics', 'contracts')
        self.assertEqual([r['id'] for r in report['routes']], ['api'])
        self.assertEqual([Path(p['path']).name for p in report['lessons']], ['api.md'])
        self.assertEqual(report['status'], 'NOT_RUN')

    def test_route_selects_project_domain_records(self):
        domain = self.root / 'docs/domain'
        domain.mkdir(parents=True)
        (domain / 'billing.md').write_text('---\ntags: [billing]\n---\nInvoice numbering rule')
        (domain / 'roles.md').write_text('---\ntags: [permissions]\n---\nCoach vs player')
        (domain / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'billing.md', 'title': 'Billing', 'tags': ['billing'], 'paths': ['src/billing/**']},
            {'path': 'roles.md', 'title': 'Roles', 'tags': ['permissions']}]}))
        by_path = self.report('route', '--files', 'src/billing/invoice.py', '--topics', 'implementation')
        self.assertEqual([Path(p['path']).name for p in by_path['domain']], ['billing.md'])
        everything = self.report('route', '--topics', 'domain')
        self.assertEqual(sorted(Path(p['path']).name for p in everything['domain']), ['billing.md', 'roles.md'])

    def test_shared_parent_domain_matches_paths_relative_to_shared_base(self):
        shared = self.base / 'docs/domain'
        shared.mkdir(parents=True)
        (shared / 'grades.md').write_text('---\ntags: [grades]\n---\nGrading rule')
        (shared / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'grades.md', 'title': 'Grades', 'paths': ['project/src/grades/**', 'api/Grades/**']}]}))
        self.manifest['workflow']['knowledge'] = {'domain': '../docs/domain'}
        self.save()
        hit = self.report('route', '--files', 'src/grades/grading.ts', '--topics', 'implementation')
        self.assertEqual([Path(p['path']).name for p in hit['domain']], ['grades.md'])
        miss = self.report('route', '--files', 'src/other.ts', '--topics', 'implementation')
        self.assertEqual(miss['domain'], [])

    @needs_os_symlinks
    def test_shared_domain_behind_symlink_keeps_logical_base(self):
        elsewhere = self.base.parent / (self.base.name + '-docs-target')
        self.addCleanup(shutil.rmtree, elsewhere, True)
        (elsewhere / 'domain').mkdir(parents=True)
        (elsewhere / 'domain/grades.md').write_text('---\ntags: [grades]\n---\nGrading rule')
        (elsewhere / 'domain/index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'grades.md', 'title': 'Grades', 'paths': ['project/src/grades/**']}]}))
        (self.base / 'docs').symlink_to(elsewhere)
        self.manifest['workflow']['knowledge'] = {'domain': '../docs/domain'}
        self.save()
        hit = self.report('route', '--files', 'src/grades/grading.ts', '--topics', 'implementation')
        self.assertEqual([Path(p['path']).name for p in hit['domain']], ['grades.md'])
        self.assertEqual(self.report('doctor')['status'], 'PASS')

    def test_route_always_includes_bootstrap_domain_records(self):
        self.write('docs/domain/glossary.md', '---\ntags: [terminology]\n---\nTransfer means delegating classes')
        self.write('docs/domain/billing.md', '---\ntags: [billing]\n---\nInvoice numbering rule')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'glossary.md', 'title': 'Glossary', 'load': 'bootstrap',
             'tags': ['terminology'], 'paths': ['src/transfers/**']},
            {'path': 'billing.md', 'title': 'Billing', 'tags': ['billing'], 'paths': ['src/billing/**']}]}))
        cases = [(), ('--topics', 'implementation'), ('--files', 'src/unrelated.py'),
                 ('--files', 'src/unrelated.py', '--topics', 'transferami'),
                 ('--files', 'src/transfers/item.py', '--topics', 'terminology')]
        for args in cases:
            with self.subTest(args=args):
                report = self.report('route', *args)
                self.assertEqual([Path(record['path']).name for record in report['domain']], ['glossary.md'])
                self.assertEqual(report['status'], 'NOT_RUN')
        by_path = self.report('route', '--files', 'src/billing/invoice.py')
        self.assertEqual(sorted(Path(record['path']).name for record in by_path['domain']),
                         ['billing.md', 'glossary.md'])

    def test_route_loads_bootstrap_from_shared_parent_domain(self):
        shared = self.base / 'docs/domain'
        shared.mkdir(parents=True)
        (shared / 'glossary.md').write_text('---\ntags: [terminology]\n---\nProject terminology')
        (shared / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'glossary.md', 'title': 'Shared glossary', 'load': 'bootstrap'}]}))
        self.manifest['workflow']['knowledge'] = {'domain': '../docs/domain'}
        self.save()
        report = self.report('route', '--topics', 'implementation')
        self.assertEqual([record['path'] for record in report['domain']], [str(shared.resolve() / 'glossary.md')])

    def test_domain_records_without_load_keep_existing_routing_and_pass_doctor(self):
        self.write('docs/domain/billing.md', '---\ntags: [billing]\n---\nInvoice numbering rule')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'billing.md', 'title': 'Billing', 'tags': ['billing'], 'paths': ['src/billing/**']}]}))
        self.assertEqual(self.report('route')['domain'], [])
        self.assertEqual(self.report('route', '--topics', 'implementation')['domain'], [])
        for args in [('--topics', 'billing'), ('--files', 'src/billing/invoice.py'), ('--topics', 'domain')]:
            with self.subTest(args=args):
                report = self.report('route', *args)
                self.assertEqual([Path(record['path']).name for record in report['domain']], ['billing.md'])
        self.assertEqual(self.report('doctor')['status'], 'PASS')

    def test_doctor_accepts_bootstrap_domain_load(self):
        self.write('docs/domain/glossary.md', '---\ntags: [terminology]\n---\nProject terminology')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'glossary.md', 'title': 'Glossary', 'load': 'bootstrap'}]}))
        self.assertEqual(self.report('doctor')['status'], 'PASS')

    def test_doctor_reports_invalid_domain_load_as_error(self):
        self.write('docs/domain/glossary.md', '---\ntags: [terminology]\n---\nProject terminology')
        for value in ['always', '', None, True, ['bootstrap'], {'mode': 'bootstrap'}]:
            with self.subTest(value=value):
                self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
                    {'path': 'glossary.md', 'title': 'Glossary', 'load': value}]}))
                report = self.report('doctor', code=1)
                self.assertEqual(report['status'], 'FAIL')
                self.assertTrue(any(finding['level'] == 'error' and 'load' in finding['reason'].lower()
                                    for finding in report['findings']), report)

    def test_domain_location_cannot_escape_project_parent(self):
        self.manifest['workflow']['knowledge'] = {'domain': '../../docs/domain'}
        self.save()
        self.rejected('route', '--topics', 'domain')

    def test_domain_location_must_be_docs_domain(self):
        self.manifest['workflow']['knowledge'] = {'domain': 'notes'}
        self.save()
        self.rejected('route', '--topics', 'domain')

    def test_domain_record_missing_from_disk_is_rejected(self):
        domain = self.root / 'docs/domain'
        domain.mkdir(parents=True)
        (domain / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'ghost.md', 'title': 'Ghost'}]}))
        self.rejected('route', '--topics', 'domain')

    def test_doctor_flags_domain_record_without_frontmatter(self):
        domain = self.root / 'docs/domain'
        domain.mkdir(parents=True)
        (domain / 'raw.md').write_text('no frontmatter')
        (domain / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'raw.md', 'title': 'Raw'}]}))
        report = self.report('doctor', code=1)
        self.assertTrue(any('Domain record requires YAML frontmatter' in f['reason'] for f in report['findings']))

    @needs_os_symlinks
    def test_lesson_symlink_outside_library_is_rejected(self):
        outside = self.base / 'outside.md'
        outside.write_text('---\ntags: [private]\n---\nprivate')
        lessons = self.library / 'workflow/lessons'
        lessons.mkdir(parents=True)
        (lessons / 'escape.md').symlink_to(outside)
        (lessons / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'lessons': [
            {'path': 'escape.md', 'title': 'Private', 'tags': ['private']}]}))
        self.rejected('route', '--topics', 'private')

    @needs_os_symlinks
    def test_entire_lesson_directory_symlink_cannot_escape_library(self):
        outside = self.base / 'external-lessons'
        outside.mkdir()
        (outside / 'private.md').write_text('private content')
        (outside / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'lessons': [
            {'path': 'private.md', 'title': 'Private', 'tags': ['private']}]}))
        workflow = self.library / 'workflow'
        workflow.mkdir()
        (workflow / 'lessons').symlink_to(outside, target_is_directory=True)
        # D73: a link inside a layer is never followed (skipped by every read), so nothing from outside the
        # layer is routed; `doctor` reports the link (`symlink-in-layer`, mapped to a warning, decision 2.3 Q1).
        report = self.report('route', '--topics', 'private')
        self.assertEqual(report['lessons'], [])
        self.assertNotIn('external-lessons', json.dumps(report))
        doctor = json.loads(self.cli('doctor').stdout)
        links = [f for f in doctor['findings'] if f['path'] == 'layer:core' and 'workflow/lessons' in f['reason']]
        self.assertEqual([f['level'] for f in links], ['warning'], doctor['findings'])

    def test_high_risk_override_requires_plan_for_otherwise_plain_file(self):
        self.write('helper.py', 'plain helper')
        report = self.report('readiness', '--files', 'helper.py', '--risk', 'high', code=2)
        self.assertEqual(report['status'], 'BLOCKED')

    def test_doctor_reports_missing_local_guide(self):
        self.manifest['workflow']['guides'] = ['docs/missing.md']
        self.save()
        report = self.report('doctor', code=1)
        self.assertTrue(any('guide' in f['reason'].lower() for f in report['findings']))

    def test_defaults_project_source_and_manifest_precedence(self):
        self.manifest['project'] = {'name': 'Team/Service'}
        directory = self.library / 'workflow/projects'
        directory.mkdir(parents=True)
        (directory.parent / 'defaults.yaml').write_text(yaml.safe_dump({'review': {
            'core': ['default core'], 'risks': {'security': ['default security'], 'high': ['default high']}}}))
        (directory / 'Team-Service.yaml').write_text(yaml.safe_dump({'review': {
            'core': ['project core'], 'risks': {'security': ['project security'], 'high': ['project high']}}}))
        self.manifest['workflow']['review'] = {'core': ['manifest core'], 'risks': {'high': ['manifest high']}}
        self.save()
        report = self.report('review', '--files', 'authentication.py', '--risk', 'high')
        self.assertIn('manifest core', report['checklist'])
        self.assertIn('manifest high', report['checklist'])
        self.assertIn('project security', report['checklist'])
        self.assertNotIn('default core', report['checklist'])
        self.assertNotIn('project high', report['checklist'])

    @needs_os_symlinks
    def test_doctor_rejects_missing_feature_install_and_foreign_live_symlink(self):
        self.manifest['skills'] = ['feature']
        self.save()
        source = self.library / 'skills/feature'
        (source / 'references').mkdir(parents=True)
        (source / 'SKILL.md').write_text('---\nname: feature\ndescription: Feature workflow\n---\nFeature')
        for host in ['claude', 'codex']:
            (source / 'references' / (host + '.md')).write_text('Host adapter')
        missing = self.report('doctor', code=1)
        self.assertTrue(any('Missing installed skill' in f['reason'] for f in missing['findings']))
        outside = self.base / 'foreign-feature'
        outside.mkdir()
        (outside / 'SKILL.md').write_text('foreign workflow')
        target = self.root / '.github/skills/feature'
        target.parent.mkdir(parents=True)
        target.symlink_to(outside, target_is_directory=True)
        foreign = self.report('doctor', code=1)
        self.assertTrue(any('Foreign installed skill symlink' in f['reason'] for f in foreign['findings']))

    def test_doctor_warns_feature_project_without_domain_index(self):
        self.manifest['skills'] = ['feature']
        self.save()
        report = self.report('doctor', code=1)
        warning = [f for f in report['findings'] if f['path'].endswith('docs/domain/index.yaml')]
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0]['level'], 'warning')

    def test_doctor_warns_about_scattered_domain_index_in_subrepository(self):
        self.manifest['skills'] = ['feature']
        self.save()
        stray = self.root / 'backend/docs/domain'
        stray.mkdir(parents=True)
        (stray / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': []}))
        report = self.report('doctor', code=1)
        self.assertTrue(any('Scattered domain knowledge' in f['reason'] and 'backend' in f['path'] for f in report['findings']))

    def test_doctor_matches_generated_host_exclusions_and_renamed_role(self):
        self.manifest.update({'skills': ['feature', 'global-only', 'local-only'], 'claude': False,
                              'agents': ['reviewer'], 'codex': {'enabled': True, 'exclude_skills': ['local-only'],
                              'agent_overrides': {'reviewer': {'name': 'custom_reviewer'}}}})
        self.save()
        for name in self.manifest['skills']:
            source = self.library / 'skills' / name
            source.mkdir(parents=True)
            (source / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Fixture workflow\n---\nSkill')
        adapters = self.library / 'skills/feature/references'
        adapters.mkdir()
        (adapters / 'codex.md').write_text('Codex adapter')
        instructions = self.library / 'instructions'
        instructions.mkdir()
        (instructions / '_base.md').write_text('# Shared instructions\nFixture base')
        agents = self.library / 'agents'
        agents.mkdir()
        (agents / 'reviewer.md').write_text('---\nname: reviewer\ndescription: Review fixture\n---\nRead-only review')
        (self.library / 'codex.yaml').write_text(yaml.safe_dump({'max_concurrent_threads_per_session': 3,
            'exclude_skills': ['global-only'], 'agents': {'reviewer': {'name': 'reviewer', 'model': 'gpt-6.1-sol',
            'model_reasoning_effort': 'high', 'sandbox_mode': 'read-only'}}}))
        config = self.base / 'config.yaml'
        config.write_text(yaml.safe_dump(transitional_config(self.library, tags={})))
        linked = subprocess.run([sys.executable, str(CLI), 'link', str(self.root)],
                                env={**os.environ, 'SKILLSYNC_CONFIG': str(config)}, capture_output=True, text=True, timeout=20)
        self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
        self.assertTrue((self.root / '.codex/agents/custom_reviewer.toml').is_file())
        self.assertFalse((self.root / '.agents/skills/global-only').exists())
        self.assertFalse((self.root / '.agents/skills/local-only').exists())
        self.assertTrue((self.root / '.github/skills/global-only').exists())
        self.assertTrue((self.root / '.github/skills/local-only').exists())
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)

    def test_doctor_budget_frontmatter_and_preserves_manual_guide(self):
        self.manifest['skills'] = ['broken']
        skill = self.library / 'skills/broken/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: broken\n---\nMissing description')
        guide = self.write('LOCAL.md', 'manually maintained guide')
        self.manifest['workflow'].update({'guides': ['LOCAL.md'], 'context': {'budgets': {'LOCAL.md': 2}}})
        self.save()
        report = self.report('doctor', code=1)
        self.assertEqual(report['status'], 'FAIL')
        reasons = [f['reason'] for f in report['findings']]
        self.assertTrue(any('frontmatter' in r for r in reasons))
        self.assertTrue(any('budget' in r for r in reasons))
        self.assertEqual(guide.read_text(), 'manually maintained guide')

    def test_doctor_instruction_traversal_is_rejected(self):
        outside = self.base / 'outside.md'
        outside.write_text('private guide')
        self.manifest['workflow']['routing'] = {'rules': [{'topics': ['x'], 'instructions': ['../outside.md']}]}
        self.save()
        self.rejected('doctor')

    def test_readiness_requires_concrete_sections_for_risky_change(self):
        self.write('src/api/orders.py', 'def orders(): return []')
        self.assertEqual(self.report('readiness', '--files', 'src/api/orders.py', code=2)['status'], 'BLOCKED')
        self.write('plan.md', '# Contracts\nTODO\n# Risks\nTBD\n# Evidence\n-\n')
        self.assertEqual(self.report('readiness', '--files', 'src/api/orders.py', '--plan', 'plan.md', code=2)['status'], 'BLOCKED')
        self.write('plan.md', '# Contracts\nExisting consumer src/api/orders.py keeps returning a list.\n# Risks\nEmpty orders remain supported.\n# Evidence\nRun regression tests for empty results.\n')
        report = self.report('readiness', '--files', 'src/api/orders.py', '--plan', 'plan.md')
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(report['files'][0]['exists'])

    @needs_os_symlinks
    def test_readiness_plan_symlink_escape_is_rejected(self):
        outside = self.base / 'outside.md'
        outside.write_text('# Contracts\ncontract\n# Risks\nrisk\n# Evidence\nevidence\n')
        (self.root / 'plan.md').symlink_to(outside)
        self.rejected('readiness', '--files', 'src/api/file.py', '--plan', 'plan.md')

    def test_review_ui_includes_real_browser_error_and_keyboard_evidence(self):
        report = self.report('review', '--files', 'ui/page.tsx')
        checklist = '\n'.join(report['checklist']).lower()
        self.assertIn('browser', checklist)
        self.assertIn('error', checklist)
        self.assertIn('keyboard', checklist)
        self.assertEqual(report['status'], 'NOT_RUN')


class SelectionContracts(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.checks(*(self.check(c) for c in ['core', 'service', 'web']))
        self.manifest['workflow']['testing'] = {'modules': [
            {'id': 'core', 'paths': ['core/**'], 'checks': ['core']},
            {'id': 'service', 'paths': ['service/**'], 'checks': ['service'], 'depends_on': ['core']},
            {'id': 'web', 'paths': ['web/**'], 'checks': ['web'], 'depends_on': ['service']} ]}
        self.save()
        self.write('core/base.py', 'old core')
        self.write('web/page.py', 'old web')
        self.init_git()

    def test_reverse_transitive_dependency_selection(self):
        report = self.report('smart-test', '--files', 'core/base.py')
        self.assertEqual(set(report['affected_modules']), {'core', 'service', 'web'})
        self.assertEqual({c['id'] for c in report['checks']}, {'core', 'service', 'web'})
        self.assertFalse(report['fallback'])

    def test_unknown_wide_and_unknown_check_use_full_test_fallback(self):
        for name in ['unmapped.py', 'package.json']:
            with self.subTest(name=name):
                report = self.report('smart-test', '--files', name)
                self.assertTrue(report['fallback'])
                self.assertEqual({c['id'] for c in report['checks']}, {'core', 'service', 'web'})
        self.manifest['workflow']['testing']['rules'] = [{'paths': ['misc/**'], 'checks': ['does-not-exist']}]
        self.save()
        report = self.report('smart-test', '--files', 'misc/file.py')
        self.assertTrue(report['fallback'])
        self.assertEqual(len(report['checks']), 3)

    def test_local_staged_unstaged_and_untracked_changes_detected(self):
        self.write('core/base.py', 'staged change')
        self.git('add', 'core/base.py')
        self.write('web/page.py', 'unstaged change')
        self.write('service/new.py', 'untracked change')
        report = self.report('smart-test')
        self.assertEqual(set(report['files']), {'core/base.py', 'web/page.py', 'service/new.py'})
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertFalse((self.root / '.skillsync/logs').exists())

    def test_base_includes_committed_changes_and_dirty_untracked(self):
        baseline = self.git('rev-parse', 'HEAD')
        self.write('core/base.py', 'committed change')
        self.git('add', '.')
        self.git('commit', '-qm', 'new core')
        self.write('web/page.py', 'dirty change')
        self.write('service/new.py', 'untracked')
        report = self.report('smart-test', '--base', baseline)
        self.assertEqual(set(report['files']), {'core/base.py', 'web/page.py', 'service/new.py'})

    def test_cache_key_invalidates_for_dirty_and_untracked_bytes(self):
        self.write('core/base.py', 'one')
        first = self.report('smart-test')['cache_key']
        self.write('core/base.py', 'two')
        second = self.report('smart-test')['cache_key']
        self.assertNotEqual(first, second)
        self.write('service/new.py', 'one')
        third = self.report('smart-test')['cache_key']
        self.write('service/new.py', 'two')
        fourth = self.report('smart-test')['cache_key']
        self.assertNotEqual(second, third)
        self.assertNotEqual(third, fourth)
        self.assertEqual(fourth, self.report('smart-test')['cache_key'])

    @needs_os_symlinks
    def test_explicit_files_cannot_escape_through_symlinked_parent(self):
        outside = self.base / 'external-source'
        outside.mkdir()
        (outside / 'private.py').write_text('external bytes')
        (self.root / 'linkdir').symlink_to(outside, target_is_directory=True)
        report = self.report('smart-test', '--files', 'linkdir/private.py', code=2)
        self.assertEqual(report['status'], 'BLOCKED')

    def test_no_changes_missing_command_invalid_base_and_traversal_block(self):
        self.assertEqual(self.report('smart-test', code=2)['status'], 'BLOCKED')
        self.assertEqual(self.report('smart-test', '--base', 'nonexistent', code=2)['status'], 'BLOCKED')
        self.assertEqual(self.report('smart-test', '--files', '../outside.py', code=2)['status'], 'BLOCKED')
        self.manifest['workflow']['validation']['checks'] = []
        self.save()
        self.assertEqual(self.report('smart-test', '--files', 'core/base.py', code=2)['status'], 'BLOCKED')


class ReadinessSpecContracts(WorkflowFixture):
    FULL = '# Contracts\ncontract text\n# Risks\nrisk text\n# Evidence\nevidence text\n'
    ARGS = ('readiness', '--files', 'helper.py', '--plan', 'plan.md')

    def plan(self, spec_line=None, body=None):
        lines = '' if spec_line is None else spec_line + '\n'
        self.write('plan.md', '# Plan\n' + lines + (self.FULL if body is None else body))

    def spec(self, body=None, name='x.md'):
        return self.write(f'docs/specs/{name}', self.FULL if body is None else body)

    def passes(self, spec='docs/specs/x.md'):
        report = self.report(*self.ARGS)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['spec'], spec)
        return report

    def blocked(self):
        report = self.report(*self.ARGS, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIsNone(report['spec'])
        self.assertTrue(any('spec' in issue.lower() for issue in report['issues']), report['issues'])
        return report

    def test_spec_line_refusals_keep_their_messages(self):
        for line, message in (('Spec: docs/specs/x.md\nSpec: docs/specs/y.md', 'Spec rejected: more than one Spec: line in plan'),
                              ('Spec: docs/specs/x.md oraz y', 'Spec rejected: Spec: line must be a single docs/specs/<file>.md path'),
                              ('Spec: docs/x.md', 'Spec rejected: expected docs/specs/<file>.md (not README.md), got docs/x.md')):
            self.plan(line)
            self.assertIn(message, self.blocked()['issues'])

    def test_sections_only_in_spec_pass(self):
        self.spec()
        self.plan('Spec: docs/specs/x.md', body='# Notes\nplain\n')
        report = self.passes()
        self.assertEqual(sorted(report['sections']), ['contracts', 'evidence', 'risks'])

    def test_sections_only_in_plan_pass(self):
        self.spec(body='# Notes\nplain\n')
        self.plan('Spec: docs/specs/x.md')
        self.passes()

    def test_section_empty_in_plan_full_in_spec_passes(self):
        self.spec()
        self.plan('Spec: docs/specs/x.md', body='# Contracts\n# Risks\n-\n# Evidence\nn/a\n')
        self.passes()

    def test_section_full_in_plan_empty_in_spec_passes(self):
        self.spec(body='# Contracts\n# Risks\n-\n# Evidence\nn/a\n')
        self.plan('Spec: docs/specs/x.md')
        self.passes()

    def test_section_missing_in_both_is_blocked(self):
        self.spec(body='# Contracts\nc\n# Risks\nr\n')
        self.plan('Spec: docs/specs/x.md', body='# Contracts\nc\n# Risks\nr\n')
        report = self.report(*self.ARGS, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertTrue(any('evidence' in i.lower() for i in report['issues']), report['issues'])

    def test_section_empty_in_both_is_blocked(self):
        empty = '# Contracts\nc\n# Risks\nr\n# Evidence\n-\n'
        self.spec(body=empty)
        self.plan('Spec: docs/specs/x.md', body=empty)
        report = self.report(*self.ARGS, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertTrue(any('evidence' in i.lower() for i in report['issues']), report['issues'])

    def test_todo_or_tbd_in_spec_is_blocked(self):
        for marker in ('TODO', 'TBD'):
            self.spec(body=self.FULL + f'# Extra\n{marker} later\n')
            self.plan('Spec: docs/specs/x.md')
            report = self.report(*self.ARGS, code=2)
            self.assertEqual(report['status'], 'BLOCKED', marker)
            self.assertTrue(report['issues'], marker)

    def test_invalid_spec_bindings_are_blocked_with_reason(self):
        self.spec()
        self.write('docs/specs/README.md', self.FULL)
        self.write('docs/specs/readme.md', self.FULL)
        self.write('docs/specs/README.MD', self.FULL)
        self.write('docs/secret.md', self.FULL)
        (self.root / 'docs/specs/dir.md').mkdir()
        self.write('secret.md', self.FULL)
        self.write('.env', 'X=1')
        self.write('docs/specs/a/b.md', self.FULL)
        for line in ['Spec:', 'Spec: docs/specs/missing.md', 'Spec: ../secret.md', 'Spec: docs/specs/../../secret.md',
                     'Spec: .env', 'Spec: secret.md', f'Spec: {self.root / "docs/specs/x.md"}', 'Spec: docs/specs/a/b.md',
                     'Spec: docs/specs/README.md', 'Spec: docs/specs/README.MD', 'Spec: docs/specs/readme.md',
                     'Spec: docs/specs/dir.md', 'Spec: docs/specs/../secret.md', 'Spec: docs/specs/./x.md', 'Spec: docs//specs/x.md', 'Spec: docs/specs/x.md (draft)', 'Spec: docs/specs/x.md extra']:
            with self.subTest(line=line):
                self.plan(line)
                self.blocked()

    def test_spec_not_utf8_is_blocked(self):
        (self.root / 'docs/specs').mkdir(parents=True)
        (self.root / 'docs/specs/x.md').write_bytes(b'# Contracts\n\xff\xfe\x80\n')
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    def test_oversized_spec_is_blocked(self):
        from skillsync_workflow.status import MAX_PLAN_BYTES
        self.spec(body=self.FULL + 'x' * (MAX_PLAN_BYTES + 1) + '\n')
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    def test_overlong_file_name_is_blocked(self):
        self.spec()
        self.plan('Spec: docs/specs/' + 'a' * 300 + '.md')
        self.blocked()

    @needs_fifo
    def test_fifo_spec_is_blocked_without_hanging(self):
        (self.root / 'docs/specs').mkdir(parents=True)
        os.mkfifo(self.root / 'docs/specs/x.md')
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    def test_dot_md_file_name_is_blocked(self):
        self.spec(name='.md')
        self.plan('Spec: docs/specs/.md')
        self.blocked()

    @needs_os_symlinks
    def test_symlinked_spec_file_inside_project_is_blocked(self):
        self.write('docs/other/real.md', self.FULL)
        (self.root / 'docs/specs').mkdir(parents=True)
        (self.root / 'docs/specs/x.md').symlink_to(self.root / 'docs/other/real.md')
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    @needs_os_symlinks
    def test_symlinked_spec_file_outside_project_is_blocked(self):
        outside = self.base / 'outside.md'
        outside.write_text(self.FULL)
        (self.root / 'docs/specs').mkdir(parents=True)
        (self.root / 'docs/specs/x.md').symlink_to(outside)
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    @needs_os_symlinks
    def test_symlinked_specs_directory_is_blocked(self):
        self.write('elsewhere/x.md', self.FULL)
        (self.root / 'docs').mkdir()
        (self.root / 'docs/specs').symlink_to(self.root / 'elsewhere', target_is_directory=True)
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    @needs_os_symlinks
    def test_symlinked_docs_directory_is_blocked(self):
        self.write('realdocs/specs/x.md', self.FULL)
        (self.root / 'docs').symlink_to(self.root / 'realdocs', target_is_directory=True)
        self.plan('Spec: docs/specs/x.md')
        self.blocked()

    def test_two_spec_lines_are_blocked(self):
        self.spec()
        self.spec(name='y.md')
        self.plan('Spec: docs/specs/x.md\nSpec: docs/specs/y.md')
        self.blocked()

    def test_empty_spec_does_not_take_next_line_as_path(self):
        self.spec()
        self.plan('Spec:\ndocs/specs/x.md')
        self.blocked()

    def test_backticks_and_crlf_are_accepted(self):
        self.spec()
        self.plan('Spec: `docs/specs/x.md`')
        self.passes()
        self.plan('Spec: docs/specs/x.md\r')
        self.passes()
        (self.root / 'plan.md').write_bytes(('# Plan\r\nSpec: `docs/specs/x.md`\r\n' + self.FULL.replace('\n', '\r\n')).encode())
        self.passes()

    def test_spec_text_not_at_line_start_is_not_a_binding(self):
        self.spec()
        self.plan()
        baseline = self.report(*self.ARGS)
        for line in ['| Spec: docs/specs/x.md | note |', 'See Spec: docs/specs/x.md here', 'Spec-sha: abc123']:
            with self.subTest(line=line):
                self.plan(line)
                report = self.report(*self.ARGS)
                self.assertEqual(report['status'], baseline['status'])
                self.assertIsNone(report['spec'])
                self.assertEqual(report['issues'], baseline['issues'])

    def test_spec_sha_does_not_replace_spec_line(self):
        self.spec()
        self.plan('Spec-sha: abc\nSpec: docs/specs/x.md', body='# Notes\nplain\n')
        self.passes()

    def test_spec_field_null_without_spec_line_and_without_plan(self):
        self.plan()
        self.assertIsNone(self.passes(spec=None)['spec'])
        report = self.report('readiness', '--files', 'helper.py')
        self.assertIn('spec', report)
        self.assertIsNone(report['spec'])

    def test_plan_without_spec_keeps_existing_messages_and_fields(self):
        self.write('plan.md', '# Contracts\nTODO\n# Risks\n-\n')
        report = self.report(*self.ARGS, code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['issues'], ['Empty plan section: contracts', 'Empty plan section: risks',
                                            'Missing plan section: evidence', 'Unresolved TODO/TBD in plan'])
        self.assertIsNone(report['spec'])
        self.assertEqual(report['sections'], [])
        self.assertEqual(report['risk'], 'low')
        self.assertEqual(report['files'], [{'file': 'helper.py', 'exists': False}])
        self.assertIn('note', report)
        self.plan()
        report = self.passes(spec=None)
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['sections'], ['contracts', 'risks', 'evidence'])


class ReadinessPrototypeGate(WorkflowFixture):
    """F3 (K26, kontrakt G): `readiness --plan` woła bramkę prototypu, gdy nagłówek planu ma linię `Prototyp:` albo obejścia."""
    FULL = '# Contracts\ncontract text\n# Risks\nrisk text\n# Evidence\nevidence text\n'
    ARGS = ('readiness', '--files', 'helper.py', '--plan', 'plan.md')
    BYPASS = 'Bramka prototypu: pominięta — pilna poprawka — Alex, 2026-11-05'

    def prototype(self, slug='alpha', accepted=True, v1=False):
        from skillsync_workflow import prototype_server as ps
        from tests.test_skillsync_prototype_accept import page_for
        directory = self.root / 'docs' / 'prototypes' / slug
        directory.mkdir(parents=True)
        (directory / 'prototype.json').write_text('{"title": "%s"}\n' % slug, encoding='utf-8')
        (directory / 'index.html').write_text(page_for(slug), encoding='utf-8')
        (directory / 'screens.css').write_text('body { margin: 0 }\n', encoding='utf-8')
        if v1:
            (directory / 'comments.js').write_text('window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"version":1,"operations":[]};\n',
                                                   encoding='utf-8')
            return directory
        store = ps.CommentsStore(str(directory), slug)
        operations = []
        if accepted:
            hashes = ps.screen_hashes(str(directory))['screens']
            operations.append({'type': 'accept', 'by': 'agent', 'payload': {
                'scope': 'all', 'screens': hashes, 'iteration': 1, 'via': 'cli', 'who': 'celina@example.com'}})
        if operations:
            store.append(operations)
        else:
            (directory / 'comments.json').write_text(ps.serialize_document(slug, []), encoding='utf-8')
        return directory

    def plan(self, header=(), body=None):
        text = '# Plan\n' + ''.join(line + '\n' for line in header) + (self.FULL if body is None else body)
        self.write('plan.md', text)

    def run_readiness(self, code):
        report = self.report(*self.ARGS, code=code)
        self.assertEqual(report['status'], 'PASS' if code == 0 else 'BLOCKED')
        return report

    def gate_issues(self, report):
        return [issue for issue in report['issues'] if issue.startswith('Prototype gate')]

    def test_plan_without_the_lines_has_no_gate_field_and_readiness_is_unchanged(self):
        self.prototype('alpha', accepted=False)
        self.plan()
        report = self.run_readiness(0)
        self.assertNotIn('prototypeGate', report)
        self.assertEqual(report['issues'], [])
        self.plan(header=['Prototyp: docs/prototypes/alpha'])  # control: with the line the field appears
        self.assertIn('prototypeGate', self.run_readiness(2))

    def test_the_lines_after_the_first_section_heading_do_not_trigger_the_gate(self):
        self.prototype('alpha', accepted=False)
        self.plan(body='## Etap 1\nPrototyp: docs/prototypes/alpha\n' + self.BYPASS + '\n' + self.FULL)
        report = self.run_readiness(0)
        self.assertNotIn('prototypeGate', report)

    def test_gate_fail_blocks_readiness_with_one_issue_per_missing_screen(self):
        self.prototype('alpha', accepted=False)
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        report = self.run_readiness(2)
        self.assertEqual(self.gate_issues(report), ['Prototype gate FAIL: alpha s2 (brak)', 'Prototype gate FAIL: alpha s3 (brak)'])
        self.assertEqual(report['prototypeGate']['status'], 'FAIL')
        self.assertEqual([item['screen'] for item in report['prototypeGate']['missing']], ['s2', 's3'])

    def test_gate_fail_of_a_v1_prototype_uses_a_dash_for_the_screen(self):
        self.prototype('stary', v1=True)
        self.plan(header=['Prototyp: docs/prototypes/stary'])
        report = self.run_readiness(2)
        self.assertEqual(self.gate_issues(report), ['Prototype gate FAIL: stary — (needs_migration)'])

    def test_gate_pass_adds_no_issue(self):
        self.prototype('alpha', accepted=True)
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        report = self.run_readiness(0)
        self.assertEqual(self.gate_issues(report), [])
        self.assertEqual(report['prototypeGate']['status'], 'PASS')
        self.assertEqual([item['who'] for item in report['prototypeGate']['acceptances']], ['celina@example.com'] * 2)

    def test_stale_acceptance_blocks_and_a_fresh_one_unblocks(self):
        directory = self.prototype('alpha', accepted=True)
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        self.run_readiness(0)
        page = directory / 'index.html'
        page.write_text(page.read_text(encoding='utf-8').replace('Tydzień', 'Tydzień zmieniony'), encoding='utf-8')
        report = self.run_readiness(2)
        self.assertEqual(self.gate_issues(report), ['Prototype gate FAIL: alpha s2 (nieaktualna)'])

    def test_bypass_gives_no_gate_issue_and_keeps_the_missing_list(self):
        self.prototype('alpha', accepted=False)
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        self.run_readiness(2)  # control: without the bypass it is blocked
        self.plan(header=['Prototyp: docs/prototypes/alpha', self.BYPASS])
        report = self.run_readiness(0)
        self.assertEqual(self.gate_issues(report), [])
        self.assertEqual(report['prototypeGate']['status'], 'PASS')
        self.assertEqual(report['prototypeGate']['bypassed'], {'reason': 'pilna poprawka', 'by': 'Alex', 'date': '2026-11-05'})
        self.assertEqual(len(report['prototypeGate']['missing']), 2)

    def test_bypass_alone_runs_the_gate_and_passes(self):
        self.plan(header=[self.BYPASS])
        report = self.run_readiness(0)
        self.assertEqual(report['prototypeGate']['bypassed']['by'], 'Alex')

    def test_gate_blocked_gives_the_blocked_issue(self):
        self.prototype('alpha', accepted=True)
        cases = {'two paths on a line': 'Prototyp: docs/prototypes/alpha docs/prototypes/beta',
                 'path outside the project': 'Prototyp: ../x', 'unknown prototype': 'Prototyp: docs/prototypes/nie-ma',
                 'bad bypass line': 'Bramka prototypu: pominięta - zły myślnik - Alex, 2026-11-05'}
        for name, line in cases.items():
            with self.subTest(name):
                self.plan(header=[line])
                report = self.run_readiness(2)
                issues = self.gate_issues(report)
                self.assertEqual(len(issues), 1, issues)
                self.assertTrue(issues[0].startswith('Prototype gate BLOCKED: '), issues)
                self.assertEqual(report['prototypeGate']['status'], 'BLOCKED')
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        self.assertEqual(self.gate_issues(self.run_readiness(0)), [])  # control: a proper line

    def test_near_miss_lines_block_readiness_and_ordinary_lines_do_not_trigger_the_gate(self):
        self.prototype('alpha', accepted=True)
        near = ['Prototype: docs/prototypes/alpha', 'Prototyp : docs/prototypes/alpha', '- Prototyp: docs/prototypes/alpha',
                '**Prototyp:** docs/prototypes/alpha', 'Bramka prototypu pominięta — pilne — Alex, 2026-11-05',
                'Bramka prototypu: pominięta — pilne — Alex, 2026-13-45']
        for line in near:
            with self.subTest(line=line):
                self.plan(header=[line])
                report = self.run_readiness(2)
                issues = self.gate_issues(report)
                self.assertEqual(len(issues), 1, issues)
                self.assertTrue(issues[0].startswith('Prototype gate BLOCKED: '), issues)
                self.assertEqual(report['prototypeGate']['status'], 'BLOCKED')
        for line in ('Przebieg: prototyp i testy', 'Spec: docs/specs/x.md', 'Repozytoria: a, prototyp b', 'Prototypy zrobione.'):
            with self.subTest(ordinary=line):
                self.plan(header=[line])
                body = self.report(*self.ARGS, code=0 if not line.startswith('Spec:') else 2)
                self.assertNotIn('prototypeGate', body)
                self.assertEqual(self.gate_issues(body), [])

    def test_a_gate_exception_becomes_a_blocked_issue(self):
        from unittest import mock
        from skillsync_workflow import prototype as prototype_module
        from skillsync_workflow import quality
        self.prototype('alpha', accepted=True)
        self.plan(header=['Prototyp: docs/prototypes/alpha'])
        args = (str(self.root), self.manifest, self.layerset(), ['helper.py'])

        def run():
            return quality.readiness(*args, plan='plan.md')

        self.assertEqual(run()['status'], 'PASS', 'control: the real gate passes')
        for error in (prototype_module.PrototypeError('boom'), ValueError('zła wartość'), OSError('dysk')):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(prototype_module, 'prototype_gate', side_effect=error):
                    report = run()
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertEqual(report['prototypeGate']['status'], 'BLOCKED')
                self.assertEqual(report['prototypeGate']['error'], 'gate_failed')
                issues = self.gate_issues(report)
                self.assertEqual(len(issues), 1, issues)
                self.assertTrue(issues[0].startswith('Prototype gate BLOCKED: '), issues)
                self.assertIn(str(error), issues[0])

    def test_title_and_similar_words_do_not_trigger_the_gate_but_a_near_miss_still_does(self):
        self.prototype('alpha', accepted=False)
        for line in ('# Prototyp: nowy ekran', 'Prototypy: nie dotyczy', 'Prototypowanie: etap 2'):
            with self.subTest(ordinary=line):
                self.write('plan.md', line + '\n' + self.FULL)  # a plan with single-hash headings only, the title first
                report = self.run_readiness(0)
                self.assertNotIn('prototypeGate', report)
                self.assertEqual(self.gate_issues(report), [])
        for line in ('- Prototyp: docs/prototypes/alpha', 'Prototype: docs/prototypes/alpha'):
            with self.subTest(near=line):
                self.write('plan.md', '# Plan\n' + line + '\n' + self.FULL)
                report = self.run_readiness(2)
                self.assertEqual(report['prototypeGate']['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
