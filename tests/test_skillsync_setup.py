"""`skillsync setup` (INST-1): machine setup wizard. Tests are black-box: they run the CLI (or `run_setup`)
against local git repositories in an isolated machine (no network, no real HOME)."""
import argparse
import builtins
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import (SKILLSYNC, LayersCase, commit, git, make_layer, needs_os_symlinks)  # noqa: E402

CORE_TAG = 'vtest-1'
SECRET = 'sup3rs3cretpw'


class SetupCase(LayersCase):
    def setUp(self):
        super().setUp()
        self.set_git_identity('dev@example.test')
        self.core_src = self.make_repo('core-origin')
        make_layer(self.core_src / 'library', 'core', 'core')
        git(self.core_src, 'add', '-A')
        commit(self.core_src, 'core')
        git(self.core_src, 'tag', CORE_TAG)
        self.org_src = self.make_repo('org-origin')
        make_layer(self.org_src, 'acme', 'org')
        git(self.org_src, 'add', '-A')
        commit(self.org_src, 'org')

    # -- helpers
    def setup_cli(self, *args, env=None, json_out=True, stdin=subprocess.DEVNULL):
        full = dict(os.environ)
        full.update(env or {})
        argv = [sys.executable, str(SKILLSYNC), 'setup', *args] + (['--json'] if json_out else [])
        process = subprocess.run(argv, capture_output=True, text=True, cwd=str(self.work), env=full,
                                 stdin=stdin, timeout=120)
        report = None
        if json_out:
            try:
                report = json.loads(process.stdout)
            except ValueError:
                self.fail(f'not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')
        return process, report

    def base_args(self, org=True, match=('**',), extra=()):
        args = ['--yes', '--core-source', str(self.core_src), '--core-ref', CORE_TAG]
        if org:
            args += ['--org', 'acme', '--org-source', str(self.org_src)]
        for glob in match or ():
            args += ['--match', glob]
        return args + list(extra)

    def results(self, report):
        return {item['step']: item['result'] for item in report['steps']}

    def config(self):
        return yaml.safe_load(self.config_file.read_text(encoding='utf-8')) if self.config_file.exists() else {}

    def layer_entry(self, layer_id):
        return next((item for item in self.config().get('layers', []) if item['id'] == layer_id), None)

    def digest(self):
        return hashlib.sha256(self.config_file.read_bytes()).hexdigest() if self.config_file.exists() else None

    def probe_project(self, name='probe'):
        return self.project(f'projects/{name}', name)

    def layers_check_ok(self):
        project = self.probe_project()
        code, report = self.cli_json('layers', 'check', '--project', str(project))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)

    def org_checkout(self):
        from skillsync_workflow import layers
        return Path(layers.default_layer_path('acme'))

    def core_checkout(self):
        from skillsync_workflow import layers
        return Path(layers.default_layer_path('core'))


class SetupRegistrationTest(SetupCase):
    def test_help_and_registration(self):
        process = self.cli('setup', '--help')
        self.assertEqual(process.returncode, 0, process.stderr)
        for flag in ('--yes', '--dry-run', '--core-ref', '--org', '--org-source', '--match', '--json'):
            self.assertIn(flag, process.stdout)
        top = self.cli('--help')
        self.assertIn('setup', top.stdout + top.stderr)


class SetupFlowTest(SetupCase):
    def test_1_clean_machine_core_and_org(self):
        process, report = self.setup_cli(*self.base_args())
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['schema'], 1)
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule'], res['check']), ('done', 'done', 'done', 'done'))
        core, org = self.layer_entry('core'), self.layer_entry('acme')
        self.assertEqual(core['kind'], 'core')
        self.assertEqual(core['subdir'], 'library')
        self.assertEqual(core['ref'], CORE_TAG)
        self.assertIs(core['writable'], False)
        self.assertEqual(org['kind'], 'org')
        self.assertIs(org['writable'], False)
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core', 'acme']}])
        self.assertTrue((self.core_checkout() / 'library' / 'layer.yaml').is_file())
        self.layers_check_ok()

    def test_2_idempotent_second_run(self):
        self.setup_cli(*self.base_args())
        before = self.digest()
        process, report = self.setup_cli(*self.base_args())
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report['status'], 'PASS')
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule']), ('unchanged', 'unchanged', 'unchanged'))
        self.assertEqual(self.digest(), before)

    def test_3_core_only_creates_default_rule(self):
        args = self.base_args(org=False, match=())
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule']), ('done', 'skipped', 'done'))
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core']}])
        self.layers_check_ok()
        before = self.digest()
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 0)
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule']), ('unchanged', 'skipped', 'unchanged'))
        self.assertEqual(self.digest(), before)

    def test_3b_core_only_keeps_existing_rules(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        cfg = self.config()
        cfg['layer_rules'] = [{'match': ['~/special/**'], 'use': ['core']}]
        self.config_file.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
        before = self.digest()
        process, report = self.setup_cli(*self.base_args(org=False, match=()))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.results(report)['rule'], 'unchanged')
        self.assertEqual(self.config()['layer_rules'], [{'match': ['~/special/**'], 'use': ['core']}])
        self.assertEqual(self.digest(), before)

    def test_4_resume_after_core_only(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        self.assertIsNone(self.layer_entry('acme'))
        # the core-only run left `**: [core]`, so --yes needs an explicit --match (Q1)
        process, report = self.setup_cli(*self.base_args(match=('**',)))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        res = self.results(report)
        self.assertEqual((res['core'], res['org']), ('unchanged', 'done'))
        self.assertIsNotNone(self.layer_entry('acme'))
        rules = self.config()['layer_rules']
        self.assertTrue(any('acme' in rule['use'] for rule in rules), rules)

    def test_15_existing_config_preserved(self):
        personal = self.layer_dir('me', 'personal')
        self.write_config(layers=[('me', 'personal', personal, {'writable': False})],
                          rules=[(['~/mine/**'], ['me'])])
        before = self.config()
        process, report = self.setup_cli(*self.base_args(match=('~/work/**',)))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        after = self.config()
        self.assertEqual(after['layers'][0], before['layers'][0])
        self.assertEqual({item['id'] for item in after['layers']}, {'me', 'core', 'acme'})
        # the user's rule keeps its position and match; `layers add core` may put core in front of its `use`
        mine = after['layer_rules'][0]
        self.assertEqual(mine['match'], ['~/mine/**'])
        self.assertEqual([item for item in mine['use'] if item != 'core'], ['me'])
        self.assertNotIn('acme', mine['use'])
        self.assertTrue(any(rule['match'] == ['~/work/**'] and 'acme' in rule['use'] for rule in after['layer_rules']))


class SetupBlockedTest(SetupCase):
    def test_5_org_without_access(self):
        missing = self.work / 'does-not-exist.git'
        args = self.base_args()
        args[args.index(str(self.org_src))] = str(missing)
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'BLOCKED')
        org = next(item for item in report['steps'] if item['step'] == 'org')
        self.assertEqual(org['result'], 'blocked')
        self.assertIn('no access', org['detail'])
        self.assertIsNotNone(self.layer_entry('core'))
        self.assertIsNone(self.layer_entry('acme'))
        self.assertFalse(self.org_checkout().exists())
        self.assertNotIn('check', self.results(report))

    def test_6_dry_run_writes_nothing(self):
        process, report = self.setup_cli(*self.base_args(extra=['--dry-run']))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(report['dry_run'])
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule']), ('planned', 'planned', 'planned'))
        self.assertFalse(self.config_file.exists())
        self.assertFalse(self.core_checkout().exists())
        self.assertFalse(self.org_checkout().exists())

    def test_6b_dry_run_keeps_existing_config_bytes(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        before = self.digest()
        process, report = self.setup_cli(*self.base_args(extra=['--dry-run']))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.results(report)['org'], 'planned')
        self.assertEqual(self.digest(), before)
        self.assertFalse(self.org_checkout().exists())

    def test_6c_dry_run_still_checks_org_access(self):
        args = self.base_args(extra=['--dry-run'])
        args[args.index(str(self.org_src))] = str(self.work / 'nope.git')
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(self.results(report)['org'], 'blocked')
        self.assertFalse(self.config_file.exists())

    def test_7_non_interactive_without_org_skips(self):
        for yes in (['--yes'], []):
            with self.subTest(yes=bool(yes)):
                args = yes + ['--core-source', str(self.core_src), '--core-ref', CORE_TAG]
                process, report = self.setup_cli(*args)  # stdin is /dev/null: no TTY, any prompt would show up as EOF
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertEqual(self.results(report)['org'], 'skipped')
                self.assertIsNone(self.layer_entry('acme'))
                self.assertNotIn('?', process.stderr)

    def test_8_unknown_core_ref(self):
        args = self.base_args(org=False, match=())
        args[args.index(CORE_TAG)] = 'v0.0.0-missing'
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'BLOCKED')
        core = next(item for item in report['steps'] if item['step'] == 'core')
        self.assertEqual(core['result'], 'blocked')
        self.assertIn('--core-ref', core['detail'])
        self.assertIsNone(self.layer_entry('core'))
        self.assertFalse(self.core_checkout().exists())
        self.assertNotIn('org', self.results(report))

    def test_9_org_same_id_other_source_blocked(self):
        self.setup_cli(*self.base_args())
        before = self.digest()
        other = self.make_repo('org-other')
        make_layer(other, 'acme', 'org')
        git(other, 'add', '-A')
        commit(other, 'x')
        args = self.base_args()
        args[args.index(str(self.org_src))] = str(other)
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        org = next(item for item in report['steps'] if item['step'] == 'org')
        self.assertEqual(org['result'], 'blocked')
        self.assertIn('layers', org['detail'])
        self.assertEqual(self.digest(), before)

    def test_9b_org_same_source_unchanged_without_ls_remote(self):
        self.setup_cli(*self.base_args())
        # the source disappears: only a skipped ls-remote can still give `unchanged`
        import shutil
        shutil.rmtree(self.org_src)
        process, report = self.setup_cli(*self.base_args())
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.results(report)['org'], 'unchanged')

    def test_10_orphaned_org_checkout(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        orphan = self.org_checkout()
        orphan.mkdir(parents=True)
        (orphan / 'leftover.txt').write_text('x', encoding='utf-8')
        before = self.digest()
        process, report = self.setup_cli(*self.base_args(match=()))
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        org = next(item for item in report['steps'] if item['step'] == 'org')
        self.assertEqual(org['result'], 'blocked')
        self.assertIn('interrupted', org['detail'])
        self.assertIn('remove that directory', org['detail'])
        self.assertTrue((orphan / 'leftover.txt').is_file())
        self.assertEqual(self.digest(), before)
        self.assertIsNone(self.layer_entry('acme'))

    def test_12_yes_with_existing_rules_requires_match(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        cfg = self.config()
        cfg['layer_rules'] = [{'match': [str(self.work / 'work' / '**')], 'use': ['core']}]  # user-made rule
        self.config_file.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
        before = self.digest()
        process, report = self.setup_cli(*self.base_args(match=()))
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        rule = next(item for item in report['steps'] if item['step'] == 'rule')
        self.assertEqual(rule['result'], 'blocked')
        self.assertIn('--match', rule['detail'])
        rules = self.config().get('layer_rules', [])
        self.assertFalse(any(rule_['match'] == ['**'] and 'acme' in rule_['use'] for rule_ in rules), rules)
        self.assertEqual(rules, cfg['layer_rules'])

    def test_12b_resume_after_core_only_without_match(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        process, report = self.setup_cli(*self.base_args(match=()))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.results(report)['org'], 'done')
        self.assertIsNotNone(self.layer_entry('acme'))
        self.assertTrue(any(rule['match'] == ['**'] and rule['use'] == ['core', 'acme']
                            for rule in self.config()['layer_rules']), self.config()['layer_rules'])


class SetupEmailTest(SetupCase):
    def test_11_no_email_warns_and_still_passes(self):
        Path(self.env['GIT_CONFIG_GLOBAL']).write_text('', encoding='utf-8')
        process, report = self.setup_cli(*self.base_args(org=False, match=()))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.results(report)['author-email'], 'skipped')
        self.assertTrue(any('user.email' in warning for warning in report['warnings']), report['warnings'])
        authors = (self.config().get('leakguard', {}).get('targets', {}).get('core', {}) or {}).get('authors') or []
        self.assertEqual(authors, [])

    def test_11b_email_set_is_recorded_in_authors(self):
        process, report = self.setup_cli(*self.base_args(org=False, match=()))
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(self.results(report)['author-email'], 'unchanged')
        self.assertEqual(report['warnings'], [])
        authors = self.config()['leakguard']['targets']['core']['authors']
        self.assertIn('dev@example.test', authors)


class SetupRedactionTest(SetupCase):
    def test_14_json_shape_and_redaction(self):
        url = f'https://user:{SECRET}@setup-host.invalid/org/repo'
        args = self.base_args(org=False, match=()) + ['--org', 'acme', '--org-source', url, '--match', '**']
        process, report = self.setup_cli(*args)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        for key in ('schema', 'status', 'steps', 'next', 'warnings'):
            self.assertIn(key, report)
        self.assertEqual(report['schema'], 1)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertNotIn(SECRET, process.stdout)
        self.assertNotIn(SECRET, process.stderr)
        self.assertNotIn(SECRET, self.config_file.read_text(encoding='utf-8'))
        text, _ = self.setup_cli(*args, json_out=False)
        self.assertEqual(text.returncode, 2)
        self.assertNotIn(SECRET, text.stdout + text.stderr)
        self.assertIn('setup: BLOCKED', text.stderr)


class SetupInteractiveTest(SetupCase):
    def test_13_interactive_prompts(self):
        from skillsync_workflow import setup_wizard
        answers = {'Organisation layer id': 'acme', 'Repository URL': str(self.org_src),
                   'Project path glob': '**'}
        asked = []

        def fake_input(prompt=''):
            asked.append(prompt)
            for key, value in answers.items():
                if key in prompt:
                    return value
            self.fail(f'unexpected question: {prompt!r}')

        args = argparse.Namespace(yes=False, dry_run=False, core_source=str(self.core_src), core_ref=CORE_TAG,
                                  org=None, org_source=None, org_ref=None, match=None, json=False)
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        with mock.patch.object(sys, 'stdin', fake_stdin), mock.patch.object(builtins, 'input', fake_input):
            report = setup_wizard.run_setup(args)
        self.assertEqual(report['status'], 'PASS', report)
        res = self.results(report)
        self.assertEqual((res['core'], res['org'], res['rule']), ('done', 'done', 'done'))
        self.assertEqual(len(asked), 3, asked)
        self.assertEqual(self.config()['layer_rules'], [{'match': ['**'], 'use': ['core', 'acme']}])
        self.assertEqual(self.layer_entry('acme')['kind'], 'org')
        self.layers_check_ok()


class SetupCheckFailTest(SetupCase):
    @needs_os_symlinks
    def test_16_layers_check_fail_gives_exit_1(self):
        self.setup_cli(*self.base_args(org=False, match=()))
        planted = self.core_checkout() / 'library' / 'evil-link'
        os.symlink(self.work, planted, target_is_directory=True)
        process, report = self.setup_cli(*self.base_args(org=False, match=()))
        self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
        self.assertEqual(report['status'], 'FAIL')
        check = next(item for item in report['steps'] if item['step'] == 'check')
        self.assertIn('symlink-in-layer', check['detail'])


if __name__ == '__main__':
    unittest.main()
