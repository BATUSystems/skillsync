"""K1 per consumer module (step 2.3t): every workflow module reads the library through the `LayerSet` resolved from
the machine config (or `--layer`), never through a single library path or the manifest; plus the K1 control grep
(`tests/k1_grep.py`).

Pattern of a module test: the data the module needs lives only in the non-core layer `org` (so a reader of one
library path would miss it), the project is matched by a `layer_rules` entry, and a project without a rule is
BLOCKED for modules that read layers (decision 2.3 Q2: modules that never read a layer keep their exit codes).
All layers are synthetic, machine state is isolated (`LayersCase`). No mutation proofs (D113); the grep test has a
planted violation instead."""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

import k1_grep  # noqa: E402
from layers_helpers import SKILL_TEXT, SKILLSYNC, LayersCase, generator_version, git, link_mode, write, yaml_text  # noqa: E402

LESSON = '---\ntags: [{0}]\n---\n{0} lesson\n'
CORE_FILES = {
    'instructions/_base.md': '# base\n',
    'instructions/proj.md': '# proj\n',
    'skills/core-skill/SKILL.md': SKILL_TEXT.format('core-skill'),
    'workflow/lessons/index.yaml': yaml_text({'version': 1, 'lessons': [
        {'path': 'core-lesson.md', 'title': 'Core lesson', 'tags': ['shared-topic']}]}),
    'workflow/lessons/core-lesson.md': LESSON.format('shared-topic'),
    'workflow/cause-keys.yaml': yaml_text({'version': 1, 'keys': {
        'core-key': {'description': 'core key', 'aliases': ['ck']}}}),
}
ORG_FILES = {
    'skills/org-skill/SKILL.md': SKILL_TEXT.format('org-skill'),
    'workflow/lessons/index.d/org.yaml': yaml_text({'version': 1, 'lessons': [
        {'path': 'org-lesson.md', 'title': 'Org lesson', 'tags': ['shared-topic']}]}),
    'workflow/lessons/org-lesson.md': LESSON.format('shared-topic'),
    'workflow/cause-keys.d/org.yaml': yaml_text({'version': 1, 'keys': {
        'org-key': {'description': 'org key', 'aliases': ['ok-alias']}}}),
    'workflow/defaults.yaml': yaml_text({'workflow': {'guardrails': {'rules': [
        {'id': 'org-rule', 'paths': ['*.py'], 'forbidden': 'ORG_FORBIDDEN'}]}}}),
    'workflow/trackers/orgtracker.yaml': yaml_text({'version': 1, 'capabilities': ['read'], 'operations': {
        'read': {'effect': 'read', 'argv': [sys.executable, '-c', 'import json; print(json.dumps(dict(id="1", title="t")))'],
                 'required_params': ['id'], 'result': {'type': 'object', 'required': ['id', 'title']}}}}),
    'workflow/skill-profiles.yaml': yaml_text({'version': 1, 'profiles': {'org-profile': ['org-skill']}}),
    'upgrade/tool.txt': 'org tool v1\n',
}


class ConsumersCase(LayersCase):
    def setUp(self):
        super().setUp()
        self.core = self.layer_dir('core', 'core', dict(CORE_FILES), version=generator_version())
        self.org = self.layer_dir('org', 'org', dict(ORG_FILES))
        self.projects = self.work / 'projects'
        self.unruled = self.work / 'elsewhere'
        self.write_config(layers=[('core', 'core', self.core), ('org', 'org', self.org)],
                          rules=[([self.projects.resolve().as_posix() + '/**'], ['core', 'org'])])
        self.app = self.new_project(self.projects / 'app')
        self.stray = self.new_project(self.unruled / 'stray')

    def new_project(self, path, **workflow):
        manifest = {'skills': ['core-skill'], 'claude': False, 'codex': False, 'mcp': False, 'workflow': workflow}
        write(path / '.github' / '.skillsync.yaml', yaml_text(manifest))
        write(path / 'app.py', 'print("ok")\n')
        git(path, 'init', '-q', '-b', 'main')
        git(path, 'config', 'user.email', 'dev@example.test')
        git(path, 'config', 'user.name', 'Dev')
        git(path, 'add', '-A')
        git(path, 'commit', '-q', '--no-verify', '-m', 'init')
        return path

    def layer_flags(self):
        return ['--layer', f'core={self.core}', '--layer', f'org={self.org}']

    def run_json(self, *args, cwd=None, code=None):
        process = self.cli(*args, '--json', cwd=cwd or self.work)
        self.assertNotIn('Traceback', process.stderr)
        if code is not None:
            self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        try:
            return json.loads(process.stdout) if process.stdout.strip() else None
        except ValueError:
            self.fail(f'not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')

    def assert_blocked_without_rule(self, *args, extra=()):
        """`args` with `self.stray` (no `layer_rules` entry): exit 2 with the `layers use` hint (JSON or stderr)."""
        process = self.cli(*args[:1], str(self.stray), *args[1:], *extra, '--json', cwd=self.work)
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layers use', process.stdout + process.stderr)
        return process


class QualityConsumer(ConsumersCase):
    def test_route_sums_the_lesson_indexes_of_all_layers(self):
        report = self.run_json('route', str(self.app), '--topics', 'shared-topic', code=0)
        lessons = {item['title']: Path(item['path']).resolve() for item in report['lessons']}
        self.assertEqual(set(lessons), {'Core lesson', 'Org lesson'})
        self.assertEqual(lessons['Org lesson'], (self.org / 'workflow/lessons/org-lesson.md').resolve())
        self.assert_blocked_without_rule('route', '--topics', 'shared-topic')

    def test_validate_gives_the_guard_child_the_same_layers_in_argv(self):
        """K3: the guard rule exists only in the org defaults; the child `guard` gets `--layer id=root` for every
        active layer and nothing else carries the layers (here the parent itself runs with `--layer` on a
        project without a rule, so a child resolving layers on its own would be BLOCKED)."""
        report = self.run_json('validate', str(self.stray), *self.layer_flags(), '--files', 'app.py', code=0)
        guard = next(check for check in report['checks'] if check['id'] == 'project-guard')
        argv = guard['argv']
        from skillsync_workflow.selfcmd import self_argv
        self.assertEqual(argv[:len(self_argv()) + 2], self_argv() + ['guard', str(self.stray.resolve())], argv)
        self.assertTrue(all(isinstance(item, str) for item in argv))
        pairs = [argv[i + 1] for i, item in enumerate(argv) if item == '--layer']
        self.assertEqual(pairs, [f'core={self.core.resolve()}', f'org={self.org.resolve()}'])
        self.assertNotIn('--library', argv)
        passed = self.run_json('validate', str(self.stray), *self.layer_flags(), '--files', 'app.py', '--run', code=0)
        self.assertEqual(passed['status'], 'PASS', passed)
        write(self.stray / 'app.py', 'ORG_FORBIDDEN = 1\n')
        failed = self.run_json('validate', str(self.stray), *self.layer_flags(), '--files', 'app.py', '--run')
        self.assertEqual(failed['status'], 'FAIL', failed)

    def test_doctor_finds_a_skill_of_the_org_layer(self):
        manifest = self.app / '.github' / '.skillsync.yaml'
        manifest.write_text(manifest.read_text(encoding='utf-8').replace('- core-skill', '- org-skill'), encoding='utf-8')
        report = self.run_json('doctor', str(self.app))
        self.assertEqual([f for f in report['findings'] if f['reason'] == 'Missing skill'], [], report)
        self.assert_blocked_without_rule('doctor')


class RunsConsumer(ConsumersCase):
    def checkpoint(self, project, *extra, code=0):
        return self.run_json('checkpoint', str(project), '--run-id', 'r1', '--step', 's1', '--status', 'PASS',
                             '--evidence', 'app.py', '--next', 'n', *extra, code=code)

    def test_sources_digest_covers_files_of_every_layer(self):
        self.checkpoint(self.app)
        self.assertEqual(self.run_json('resume', str(self.app), '--run-id', 'r1', code=0)['status'], 'PASS')
        write(self.org / 'workflow' / 'lessons' / 'org-lesson.md', LESSON.format('changed'))
        report = self.run_json('resume', str(self.app), '--run-id', 'r1', code=2)
        self.assertTrue(report['stale'], report)
        self.assertTrue(any('źródła' in reason for reason in report['reasons']), report)

    def test_a_regular_file_in_a_composite_asks_for_skillsync_link(self):
        """Review 2.3 (minor): composite entries are links to layer files; a regular file in the composite (not
        made by `link`) makes `checkpoint`/`resume` refuse with the instruction to run `skillsync link`."""
        write(self.core / 'skills' / 'shared' / 'SKILL.md', SKILL_TEXT.format('shared'))
        write(self.org / 'skills' / 'shared' / 'org-extra.md', 'org extra\n')
        project = self.projects / 'composed'
        write(project / '.github' / '.skillsync.yaml', yaml_text({
            'skills': ['shared'], 'claude': False, 'codex': False, 'mcp': False,
            'instructions': {'merge': ['_base'], 'template': 'proj'}}))
        write(project / 'app.py', 'print("ok")\n')
        git(project, 'init', '-q', '-b', 'main')
        with link_mode('symlink'):
            linked = self.cli('link', str(project))
        self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
        installed = project / '.github' / 'skills' / 'shared'
        self.assertTrue(installed.is_symlink())
        self.checkpoint(project)
        write(Path(os.path.realpath(installed)) / 'planted.md', 'not a link\n')
        for command in ('resume', 'checkpoint'):
            with self.subTest(command=command):
                args = ['--run-id', 'r1'] if command == 'resume' else [
                    '--run-id', 'r1', '--step', 's2', '--status', 'PASS', '--evidence', 'app.py', '--next', 'n']
                process = self.cli(command, str(project), *args, '--json', cwd=self.work)
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn('skillsync link', process.stdout + process.stderr)

    def test_checkpoint_without_a_rule_is_blocked_and_with_layer_flags_works(self):
        self.assert_blocked_without_rule('checkpoint', '--run-id', 'r1', '--step', 's1', '--status', 'PASS',
                                         '--evidence', 'app.py', '--next', 'n')
        self.checkpoint(self.stray, *self.layer_flags())
        self.assertEqual(self.run_json('resume', str(self.stray), '--run-id', 'r1', *self.layer_flags(),
                                       code=0)['status'], 'PASS')


class CatalogAndMaintenanceConsumers(ConsumersCase):
    def test_skills_list_shows_skills_and_profiles_of_the_org_layer(self):
        report = self.run_json('skills', 'list', str(self.app), code=0)
        rows = {row['name']: row for row in report['skills']}
        self.assertIn('org-skill', rows)
        self.assertEqual(rows['org-skill']['profiles'], ['org-profile'])

    def test_profiles_selected_from_the_org_catalog(self):
        project = self.new_project(self.projects / 'profiled', skill_profiles={'selected': ['org-profile']})
        report = self.run_json('profiles', str(project), code=0)
        self.assertIn('org-skill', json.dumps(report))
        self.assert_blocked_without_rule('profiles')

    def test_upgrade_reads_its_source_from_the_owning_layer(self):
        project = self.new_project(self.projects / 'upgraded', upgrade={
            'revision': 'r1', 'artifacts': [{'target': 'docs/workflow/tool.txt', 'source': 'upgrade/tool.txt'}]})
        report = self.run_json('upgrade', str(project), code=0)
        self.assertEqual([(row['target'], row['decision']) for row in report['artifacts']], [('docs/workflow/tool.txt', 'create')])
        self.run_json('upgrade', str(project), '--action', 'apply', '--run', code=0)
        self.assertEqual((project / 'docs/workflow/tool.txt').read_text(encoding='utf-8'), 'org tool v1\n')


class DeliveryRetroKnowledgeConsumers(ConsumersCase):
    def test_tracker_descriptor_comes_from_the_org_layer(self):
        project = self.new_project(self.projects / 'tracked', delivery={'provider': 'orgtracker'})
        report = self.run_json('tracker', str(project), '--operation', 'read', '--params', '{"id": "1"}',
                               '--provider', 'orgtracker')
        self.assertNotIn('Unknown', json.dumps(report))
        self.assertEqual(report['status'], 'NOT_RUN', report)
        process = self.cli('tracker', str(self.stray), '--operation', 'read', '--params', '{"id": "1"}',
                           '--provider', 'orgtracker', '--json', cwd=self.work)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(json.loads(process.stdout)['status'], 'BLOCKED')

    def test_tracker_without_a_rule_gives_the_layers_use_instruction(self):
        """K3: no matching rule = BLOCKED *with the instruction* (`layers use`); `delivery._cmd` replaces every
        error with a generic reason, so the instruction is lost (reported to the orchestrator, step 2.3t)."""
        self.assert_blocked_without_rule('tracker', '--operation', 'read', '--params', '{"id": "1"}',
                                         '--provider', 'orgtracker')

    def test_retro_decide_resolves_an_alias_of_the_org_cause_keys(self):
        report = self.run_json('retro-decide', str(self.app), '--cause-key', 'ok-alias', '--decision', 'rejected', code=0)
        self.assertIn('org-key', json.dumps(report))
        text = (self.app / 'docs/retro/decisions.yaml').read_text(encoding='utf-8')
        self.assertIn('cause_key: org-key', text)
        self.assert_blocked_without_rule('retro-decide', '--cause-key', 'ck', '--decision', 'rejected')

    def test_knowledge_init_resolves_layers(self):
        self.run_json('knowledge', 'init', str(self.app), code=0)
        process = self.cli('knowledge', 'init', str(self.stray), '--json', cwd=self.work)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layers use', process.stdout + process.stderr)


class OtherLayerReadersConsumers(ConsumersCase):
    """Modules whose library input is the merged workflow config: they resolve layers (no rule = BLOCKED, exit 2)."""

    def test_smart_test_map_e2e_env_are_blocked_without_a_rule(self):
        cases = {'smart-test': (), 'map': (), 'e2e': ('--action', 'plan'), 'env': ('--action', 'plan')}
        for command, extra in cases.items():
            with self.subTest(command=command):
                process = self.cli(command, str(self.stray), *extra, '--json', cwd=self.work)
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertIn('layers use', process.stdout + process.stderr)
                ruled = self.cli(command, str(self.app), *extra, '--json', cwd=self.work)
                self.assertNotIn('layer_rules', ruled.stdout + ruled.stderr)
                self.assertNotIn('Traceback', ruled.stderr)


ORG_WORKFLOW = {'workflow': {
    'guardrails': {'rules': [{'id': 'org-rule', 'paths': ['*.py'], 'forbidden': 'ORG_FORBIDDEN'}]},
    'validation': {'checks': [{'id': 'org-unit', 'kind': 'test', 'argv': [sys.executable, '-c', 'print("org check ran")'],
                               'profiles': ['quick', 'final'], 'success_pattern': 'org check ran'}]},
    'testing': {'rules': [{'paths': ['*.py'], 'checks': ['org-unit']}]},
    'e2e': {'checks': [{'id': 'org-e2e', 'kind': 'e2e', 'argv': [sys.executable, '-c', 'print("e2e")']}]},
    'environment': {'start': [sys.executable, '-c', 'print("org env")'], 'health': {'argv': [sys.executable, '-c', 'pass']}},
    'delivery': {'ci': {'max_iterations': 1, 'rerun_check_ids': ['org-unit']}},
}}


class OrgDataPositiveControls(ConsumersCase):
    """Review 2.3 (minor test-gap K1, moved to 2.4t): each module that reads the merged workflow config sees data
    that exists only in the org layer. Control per module: the same call with `--layer core=<core>` (org not
    active) does not see it, so the positive result cannot come from the core or the manifest."""

    def setUp(self):
        super().setUp()
        write(self.org / 'workflow' / 'defaults.yaml', yaml_text(ORG_WORKFLOW))

    def core_only(self):
        return ['--layer', f'core={self.core}']

    def test_smart_test_selects_the_org_check(self):
        report = self.run_json('smart-test', str(self.app), '--files', 'app.py', code=0)
        self.assertEqual([check['id'] for check in report['checks']], ['org-unit'], report)
        self.assertFalse(report['fallback'], report)
        control = self.run_json('smart-test', str(self.app), *self.core_only(), '--files', 'app.py')
        self.assertNotIn('org-unit', json.dumps(control))

    def test_map_lists_the_org_check(self):
        self.run_json('map', str(self.app), code=0)
        text = (self.app / '.skillsync' / 'map.md').read_text(encoding='utf-8')
        self.assertIn('org-unit', text)
        self.run_json('map', str(self.app), *self.core_only(), code=0)
        self.assertNotIn('org-unit', (self.app / '.skillsync' / 'map.md').read_text(encoding='utf-8'))

    def test_e2e_and_env_plans_use_the_org_configuration(self):
        report = self.run_json('e2e', str(self.app), '--action', 'plan', code=0)
        self.assertEqual([check['id'] for check in report['checks']], ['org-e2e'], report)
        control = self.run_json('e2e', str(self.app), '--action', 'plan', *self.core_only(), code=0)
        self.assertEqual(control['checks'], [])
        report = self.run_json('env', str(self.app), '--action', 'plan', code=0)
        self.assertEqual(report['configuration']['start'], ORG_WORKFLOW['workflow']['environment']['start'], report)
        control = self.run_json('env', str(self.app), '--action', 'plan', *self.core_only(), code=0)
        self.assertIn('No environment recipe', control['reason'])

    def test_instruction_drift_looks_into_every_layer_root(self):
        write(self.app / 'AGENTS.md', '# Agents\n\nTracker: `workflow/trackers/orgtracker.yaml`, missing: '
                                      '`workflow/trackers/ghost.yaml`. Use skill `org-skill`.\n')
        reasons = [item['reason'] for item in self.run_json('doctor', str(self.app))['findings']]
        self.assertFalse([item for item in reasons if 'orgtracker.yaml' in item], reasons)
        self.assertTrue([item for item in reasons if 'Missing path' in item and 'ghost.yaml' in item], reasons)
        self.assertIn('Skill named in instructions is not installed in the project: org-skill', reasons)
        control = [item['reason'] for item in self.run_json('doctor', str(self.app), *self.core_only())['findings']]
        self.assertTrue([item for item in control if 'Missing path' in item and 'orgtracker.yaml' in item], control)
        self.assertIn('Skill named in instructions is not in the library: org-skill', control)

    def test_ci_reruns_the_org_check(self):
        from skillsync_workflow.runs import fingerprint
        write(self.app / '.skillsync' / 'ci' / 'unit.log', 'FAILED test_x\n')
        report_path = self.app / '.skillsync' / 'ci' / 'report.json'
        write(report_path, json.dumps({'source': fingerprint(self.app),
                                       'checks': [{'name': 'unit', 'status': 'FAIL', 'log': '.skillsync/ci/unit.log'}]}))
        report = self.run_json('ci', str(self.app), '--report', '.skillsync/ci/report.json', '--run', code=0)
        self.assertEqual(report['status'], 'PASS', report)
        evidence = report['iterations'][0]['evidence']
        self.assertEqual([item['id'] for item in evidence['results']], ['org-unit'], report)
        control = self.run_json('ci', str(self.app), '--report', '.skillsync/ci/report.json', '--run', *self.core_only())
        self.assertEqual((control['status'], control['iterations']), ('NOT_RUN', []), control)

    def test_benchmark_children_resolve_the_same_layers(self):
        suite = self.work / 'suite.yaml'
        write(suite, yaml_text({'cases': [{'id': 'skills', 'argv': ['{python}', '{skillsync}', 'skills', 'list',
                                                                    str(self.app), '--json'],
                                           'expected_output': '"org-skill"', 'timeout': 120}]}))
        report = self.run_json('benchmark', str(self.app), '--suite', str(suite), '--run', code=0)
        self.assertEqual(report['cases'][0]['status'], 'PASS', report)
        self.write_config(layers=[('core', 'core', self.core), ('org', 'org', self.org)],
                          rules=[([self.projects.resolve().as_posix() + '/**'], ['core'])])
        control = self.run_json('benchmark', str(self.app), '--suite', str(suite), '--run', code=1)
        self.assertEqual(control['cases'][0]['status'], 'FAIL', control)

    # K5: the four shapes of `{skillsync}` in benchmark suites. A child prints its own argv, so the test sees what the
    # shape expanded to; `skillsync --version` is the target of every shape.
    SHELLESS = ('import shlex, subprocess, sys; sys.exit(subprocess.call(shlex.split(sys.argv[1])))')
    SHAPES = {
        'python and skillsync in text': ['{python}', '-c', SHELLESS, '{python} {skillsync} --version'],
        'bare skillsync in text': ['{python}', '-c', SHELLESS, '{skillsync} --version'],
        'skillsync list element': ['{skillsync}', '--version'],
        'python and skillsync pair': ['{python}', '{skillsync}', '--version'],
    }

    def benchmark_shapes(self, base, env=None):
        from skillsync_workflow import __version__
        for name, argv in self.SHAPES.items():
            with self.subTest(name):
                suite = self.work / 'shape-suite.yaml'
                write(suite, yaml_text({'cases': [{'id': 'shape', 'argv': argv, 'timeout': 120,
                                                   'expected_output': f'skillsync {__version__}'}]}))
                process = subprocess.run(base + [str(self.app), '--suite', str(suite), '--run', '--json'],
                                         capture_output=True, text=True, cwd=str(self.work), timeout=300,
                                         env={**os.environ, **(env or {})})
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertEqual(json.loads(process.stdout)['cases'][0]['status'], 'PASS', process.stdout)

    def test_benchmark_expands_all_four_skillsync_shapes_in_a_checkout(self):
        self.benchmark_shapes([sys.executable, str(SKILLSYNC), 'benchmark'])

    def test_benchmark_expands_all_four_skillsync_shapes_when_installed(self):
        """The installation form (`python -P -m skillsync_workflow`): the package alone, no wrapper beside it."""
        site = self.work / 'site-packages'
        site.mkdir()
        shutil.copytree(SKILLSYNC.parent / 'skillsync_workflow', site / 'skillsync_workflow',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.assertFalse((site / 'skillsync').exists())
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        env['PYTHONPATH'] = str(site)
        self.benchmark_shapes([sys.executable, '-P', '-m', 'skillsync_workflow', 'benchmark'], env=env)

    def test_benchmark_text_shapes_become_the_shell_joined_self_command(self):
        """Control: the child echoes its argv; both text shapes arrive as `shlex.join(self_argv())`, the interpreter once."""
        import shlex
        from skillsync_workflow.selfcmd import self_argv
        joined = shlex.join(self_argv())
        echo = 'import json, sys; print(json.dumps(sys.argv[1:]))'
        suite = self.work / 'echo-suite.yaml'
        write(suite, yaml_text({'cases': [{'id': 'echo', 'argv': ['{python}', '-c', echo, '{python} {skillsync}', 'x {skillsync} y'],
                                           'expected_output': json.dumps([joined, f'x {joined} y']), 'timeout': 60}]}))
        report = self.run_json('benchmark', str(self.app), '--suite', str(suite), '--run', code=0)
        self.assertEqual(report['cases'][0]['status'], 'PASS', report)

    def test_plan_accepts_layer_flags_without_reading_layers(self):
        for project in (self.app, self.stray):
            for flags in ((), self.layer_flags()):
                with self.subTest(project=project.name, flags=bool(flags)):
                    write(project / 'docs' / 'plans' / '2026-11-05-x.md', '# Plan\n\nStatus: draft\n')
                    report = self.run_json('plan', 'approve', str(project), 'docs/plans/2026-11-05-x.md', *flags, code=0)
                    self.assertEqual((report['status'], report['to']), ('PASS', 'approved'), report)


class NonReadersKeepExitCodes(ConsumersCase):
    """Decision 2.3 Q2: `status`, `plan`, `claim`, `dispatch`, `metrics`, `map --check` read no layer, so the
    project without a rule gets the same exit code as the matched one; `--layer` is accepted."""

    def test_same_exit_code_with_and_without_a_rule(self):
        cases = {'status': (), 'claim': ('--action', 'list'), 'dispatch': (), 'metrics': (), 'map': ('--check',)}
        for command, extra in cases.items():
            with self.subTest(command=command):
                ruled = self.cli(command, str(self.app), *extra, '--json', cwd=self.work)
                stray = self.cli(command, str(self.stray), *extra, '--json', cwd=self.work)
                self.assertNotIn('Traceback', ruled.stderr + stray.stderr)
                self.assertEqual(stray.returncode, ruled.returncode, stray.stdout + stray.stderr)
                self.assertNotIn('layer_rules', stray.stdout + stray.stderr)

    def test_the_layer_flag_is_accepted_and_changes_no_exit_code(self):
        # K3.12: `--layer core=<path>` is accepted by every non-reader; the exit code equals the one without the flag.
        core = self.core
        cases = {'status': (), 'plan': (), 'claim': ('--action', 'list'), 'dispatch': (), 'metrics': (),
                 'benchmark': (), 'map': ('--check',)}
        for command, extra in cases.items():
            with self.subTest(command=command):
                plain = self.cli(command, str(self.app), *extra, '--json', cwd=self.work)
                flagged = self.cli(command, str(self.app), *extra, '--layer', f'core={core}', '--json', cwd=self.work)
                self.assertNotIn('unrecognized arguments', flagged.stderr, flagged.stderr)
                self.assertNotIn('Traceback', flagged.stderr)
                self.assertEqual(flagged.returncode, plain.returncode, flagged.stdout + flagged.stderr)


class K1Grep(unittest.TestCase):
    """`tests/k1_grep.py`: the spec regex over the generator sources, hits mapped to functions, allowlist."""

    def test_the_generator_has_no_hit_outside_the_allowlist(self):
        hits = k1_grep.scan(ROOT)
        self.assertTrue(hits, 'the allowlisted hits (D41 detection) are expected')
        self.assertEqual([h for h in hits if not h['allowed']], [])
        process = subprocess.run([sys.executable, str(TESTS / 'k1_grep.py')], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_the_grep_finds_every_form_of_the_regex_and_maps_it_to_its_function(self):
        """Planted case: a copy of the sources plus a module with each regex form; the allowlist is per function."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix='k1-grep-') as temp:
            root = Path(temp)
            shutil.copytree(ROOT / 'skillsync_workflow', root / 'skillsync_workflow',
                            ignore=shutil.ignore_patterns('__pycache__'))
            planted = root / 'skillsync_workflow' / 'planted.py'
            planted.write_text(
                'import argparse\n'
                'class Reader:\n'
                '    @staticmethod\n'
                '    def subscript(config):\n'
                '        return config["library"]\n'
                'def getter(config):\n'
                "    return config.get( 'library')\n"
                'def manifest_key():\n'
                '    return "sync.library"\n'
                "FLAG = '--library'\n", encoding='utf-8')
            layers = root / 'skillsync_workflow' / 'layers.py'
            layers.write_text(layers.read_text(encoding='utf-8') +
                              "\n\ndef migrate_planted(raw):\n    return raw['library']\n", encoding='utf-8')
            hits = [h for h in k1_grep.scan(root) if h['file'] in ('skillsync_workflow/planted.py',
                                                                     'skillsync_workflow/layers.py')]
            got = {(h['file'].rsplit('/', 1)[1], h['function'], h['allowed']) for h in hits}
            self.assertEqual(got, {('planted.py', 'Reader.subscript', False), ('planted.py', 'getter', False),
                                   ('planted.py', 'manifest_key', False), ('planted.py', '<module>', False),
                                   ('layers.py', 'legacy_library_key', True), ('layers.py', 'migrate_planted', True)})
            process = subprocess.run([sys.executable, str(TESTS / 'k1_grep.py'), '--root', str(root)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            self.assertIn('VIOLATION', process.stdout)

    def test_the_scanner_covers_the_cli_module_not_only_the_wrapper(self):
        names = {path.relative_to(ROOT).as_posix() for path in k1_grep.sources(ROOT)}
        self.assertIn('skillsync_workflow/cli.py', names)
        self.assertNotIn('skillsync', names)
        cli = ROOT / 'skillsync_workflow' / 'cli.py'
        self.assertGreater(len(cli.read_text(encoding='utf-8').splitlines()), 1000)

    def test_a_known_bypass_planted_in_a_copy_of_cli_py_is_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='k1-grep-cli-') as temp:
            root = Path(temp)
            shutil.copytree(ROOT / 'skillsync_workflow', root / 'skillsync_workflow',
                            ignore=shutil.ignore_patterns('__pycache__'))
            self.assertEqual([h for h in k1_grep.scan(root) if not h['allowed']], [], 'the copy starts clean')
            cli = root / 'skillsync_workflow' / 'cli.py'
            cli.write_text(cli.read_text(encoding='utf-8') +
                           "\n\ndef sneaky(config):\n    return config.get('library')\n", encoding='utf-8')
            violations = [h for h in k1_grep.scan(root) if not h['allowed']]
            self.assertEqual([(h['file'], h['function']) for h in violations],
                             [('skillsync_workflow/cli.py', 'sneaky')])
            process = subprocess.run([sys.executable, str(TESTS / 'k1_grep.py'), '--root', str(root)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)

    def test_a_tree_without_cli_py_cannot_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='k1-grep-nocli-') as temp:
            (Path(temp) / 'skillsync_workflow').mkdir()
            (Path(temp) / 'skillsync_workflow' / 'other.py').write_text('X = 1\n', encoding='utf-8')
            process = subprocess.run([sys.executable, str(TESTS / 'k1_grep.py'), '--root', temp],
                                     capture_output=True, text=True)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)

    def test_a_missing_tree_cannot_pass(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='k1-grep-empty-') as temp:
            process = subprocess.run([sys.executable, str(TESTS / 'k1_grep.py'), '--root', temp],
                                     capture_output=True, text=True)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)


if __name__ == '__main__':
    unittest.main()
