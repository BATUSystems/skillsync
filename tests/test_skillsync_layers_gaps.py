"""Gaps before the final verification of P1 (spec A `docs/specs/2026-11-05-warstwy-generator.md`):

1. K3: project commands run by `validate`, `guard` and `smart-test --run` get no information about the layers
   (no `--layer`, no layer path, no `SKILLSYNC_*` variable with the layers) in argv or in the environment; only the
   own child `guard` started by `validate` gets `--layer` (decision 2.3).
2. K9 / "Kontrakty": `skillsync list` shows every skill with the column of its providing layers (core, org, a skill
   overridden in personal); the description comes from the winning (last) provider.
3. K2/D73 (deviation from "error" in the spec, plan "Wynik", D73): `leakcheck` of a layer whose tree holds a link,
   and of a repository whose `.leakguard/patterns.yaml` is a link, is BLOCKED (exit 2, a structure error), never
   FAIL (exit 1, reserved for leaks); `--worktree` and `--staged`.
4. K15 (a): on the config after `layers migrate` of a library with `layer.yaml` (a real core layer), `validate` starts
   its child `guard` with `--layer core=<path>` (never `,synthetic`), the child evaluates the guard rules of the
   core (PASS, FAIL on a forbidden pattern), not BLOCKED; `guard --layer id=path,synthetic` is refused
   (`synthetic-refused`) and `validate` on a 0.9.x bridge config is BLOCKED `legacy-config`.
5. K15 (a), copy mode (`SKILLSYNC_NO_SYMLINK=1`): a local edit of a copied file owned by the (read-only) core is
   reported as a divergence by `link` and by `push` (skipped, BLOCKED with `--to-layer core`); neither writes into the
   library (tree snapshot before and after) and the local edit is kept.

The probe of gap 1 is a script that appends its argv and environment to a JSON-lines file. All names are synthetic;
machine state is isolated (layers_helpers)."""
import json
import os
import re
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT))

from layers_helpers import (SKILL_TEXT, ComposeCase, LayersCase, commit, each_mode, git, link_mode,  # noqa: E402
                            make_layer, needs_os_symlinks, tree_snapshot, write, yaml_text)

PROBE = '''import json, os, sys
with open(sys.argv[1], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps({'argv': sys.argv, 'env': dict(os.environ)}) + '\\n')
print('Ran 1 test in 0.001s')
print('OK')
'''


def git_project(path, manifest):
    """Project with a manifest, `app.py` and `probe.py`, committed in a fresh git repository."""
    write(path / '.github' / '.skillsync.yaml', yaml_text(manifest))
    write(path / 'app.py', 'print("ok")\n')
    write(path / 'probe.py', PROBE)
    git(path, 'init', '-q', '-b', 'main')
    git(path, 'config', 'user.email', 'dev@example.test')
    git(path, 'config', 'user.name', 'Dev')
    git(path, 'add', '-A')
    commit(path, 'init')
    return path


def run_json(case, *args, cwd=None):
    process = case.cli(*args, '--json', cwd=cwd)
    case.assertNotIn('Traceback', process.stderr)
    try:
        return process.returncode, json.loads(process.stdout)
    except ValueError:
        case.fail(f'not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')


# ====================================================================== gap 1: K3, project commands get no layers


class ProjectCommandsWithoutLayersTest(ComposeCase):
    """K3: "komendy projektu uruchamiane przez validate/guard/smart-test --run nie dostają żadnych informacji o
    warstwach". The probe runs as a `validation.checks` entry (`validate`, `smart-test`) and as a
    `guardrails.checks` entry (`guard`, also as the grandchild of `validate` through its child `guard`)."""

    def setUp(self):
        super().setUp()
        self.add_layers(core={'instructions/_base.md': '# base\n', 'instructions/proj.md': '# proj\n',
                              'skills/feature/SKILL.md': SKILL_TEXT.format('feature')},
                        org={'skills/org-skill/SKILL.md': SKILL_TEXT.format('org-skill')},
                        personal={'skills/mine/SKILL.md': SKILL_TEXT.format('mine')})
        self.out = self.work / 'probe-out.jsonl'
        manifest = {'skills': ['feature'], 'claude': False, 'codex': False, 'mcp': False, 'workflow': {
            'validation': {'checks': [self.check('probe-validate')]},
            'guardrails': {'checks': [self.check('probe-guard')]},
            'testing': {'rules': [{'paths': ['app.py'], 'checks': ['probe-validate']}]}}}
        self.app = git_project(self.work / 'projects' / 'app', manifest)
        self.roots = [str(path.resolve()) for path in self.layer_paths.values()]
        self.roots += [str(path) for path in self.layer_paths.values()]

    def check(self, marker):
        return {'id': marker, 'argv': [sys.executable, 'probe.py', str(self.out), marker], 'kind': 'test',
                'required': True, 'timeout': 60}

    def flags(self):
        return [item for layer_id, path in self.layer_paths.items() for item in ('--layer', f'{layer_id}={path}')]

    def records(self, *args, cwd=None):
        """Run a command, return (exit code, report, {marker: [probe records]})."""
        if self.out.exists():
            self.out.unlink()
        code, report = run_json(self, *args, cwd=cwd)
        found = {}
        if self.out.exists():
            for line in self.out.read_text(encoding='utf-8').splitlines():
                record = json.loads(line)
                found.setdefault(record['argv'][2], []).append(record)
        return code, report, found

    def assert_no_layers(self, record, label):
        parent = dict(os.environ)
        argv = record['argv']
        self.assertNotIn('--layer', argv, label)
        for root in self.roots:
            for item in argv:
                self.assertNotIn(root, item, f'{label}: layer path in argv')
        self.assertEqual(argv[1:], [str(self.out), argv[2]], f'{label}: argv of the project command changed: {argv}')
        for key, value in record['env'].items():
            for root in self.roots:
                self.assertNotIn(root, value, f'{label}: layer path in env {key}')
            if key.startswith('SKILLSYNC_'):
                self.assertEqual(parent.get(key), value, f'{label}: {key} set by skillsync for a project command')

    def test_validate_run_passes_no_layers_to_the_check_or_through_the_guard_child(self):
        for how, extra in (('config rule', ()), ('--layer flags', tuple(self.flags()))):
            with self.subTest(how=how):
                code, report, found = self.records('validate', str(self.app), *extra, '--files', 'app.py', '--run')
                self.assertEqual((code, report['status']), (0, 'PASS'), report)
                self.assertEqual(sorted(found), ['probe-guard', 'probe-validate'], report)
                self.assertEqual([len(found[key]) for key in sorted(found)], [1, 1])
                for marker, records in found.items():
                    self.assert_no_layers(records[0], f'validate/{how}/{marker}')

    def test_the_own_guard_child_of_validate_does_get_the_layers(self):
        """Decision 2.3 (contrast of gap 1): the child `skillsync guard` is not a project command."""
        code, report = run_json(self, 'validate', str(self.app), '--files', 'app.py')
        self.assertEqual(code, 0, report)
        guard = next(item for item in report['checks'] if item['id'] == 'project-guard')
        pairs = [guard['argv'][i + 1] for i, item in enumerate(guard['argv']) if item == '--layer']
        self.assertEqual(pairs, [f'{layer_id}={path.resolve()}' for layer_id, path in self.layer_paths.items()])
        probe = next(item for item in report['checks'] if item['id'] == 'probe-validate')
        self.assertEqual(probe['argv'], self.check('probe-validate')['argv'])

    def test_guard_run_passes_no_layers_to_its_checks(self):
        for how, extra in (('config rule', ()), ('--layer flags', tuple(self.flags()))):
            with self.subTest(how=how):
                code, report, found = self.records('guard', str(self.app), *extra, '--files', 'app.py', '--run')
                self.assertEqual((code, report['status']), (0, 'PASS'), report)
                self.assertEqual(list(found), ['probe-guard'], report)
                self.assert_no_layers(found['probe-guard'][0], f'guard/{how}')

    def test_smart_test_run_passes_no_layers_to_the_selected_check(self):
        for how, extra in (('config rule', ()), ('--layer flags', tuple(self.flags()))):
            with self.subTest(how=how):
                code, report, found = self.records('smart-test', str(self.app), *extra, '--files', 'app.py', '--run')
                self.assertEqual((code, report['status']), (0, 'PASS'), report)
                self.assertEqual(list(found), ['probe-validate'], report)
                self.assert_no_layers(found['probe-validate'][0], f'smart-test/{how}')

    def test_the_probe_sees_a_planted_layer_flag(self):
        """Control: the assertion can fail (a check whose argv carries `--layer` with a layer path is caught)."""
        planted = {'argv': ['probe.py', str(self.out), 'x', '--layer', 'core=elsewhere'], 'env': {}}
        with self.assertRaisesRegex(AssertionError, "'--layer' unexpectedly found"):
            self.assert_no_layers(planted, 'planted flag')
        planted = {'argv': ['probe.py', str(self.out), 'x'], 'env': {'EXTRA': self.roots[0]}}
        with self.assertRaisesRegex(AssertionError, 'layer path in env EXTRA'):
            self.assert_no_layers(planted, 'planted path')
        planted = {'argv': ['probe.py', str(self.out), 'x'], 'env': {'SKILLSYNC_LAYERS': 'core'}}
        with self.assertRaisesRegex(AssertionError, 'SKILLSYNC_LAYERS set by skillsync'):
            self.assert_no_layers(planted, 'planted env')
        clean = {'argv': ['probe.py', str(self.out), 'x'], 'env': dict(os.environ)}
        self.assert_no_layers(clean, 'clean')


# ====================================================================== gap 2: `skillsync list` with the layer column


class ListLayerColumnTest(ComposeCase):
    def setUp(self):
        super().setUp()
        self.add_layers(core={'skills/core-only/SKILL.md': SKILL_TEXT.format('core-only'),
                              'skills/shared/SKILL.md': 'core shared first line\n'},
                        org={'skills/org-only/SKILL.md': SKILL_TEXT.format('org-only')},
                        personal={'skills/shared/SKILL.md': 'personal shared first line\n',
                                  'skills/mine/SKILL.md': SKILL_TEXT.format('mine')})

    def rows(self, *args):
        process = self.cli('list', *args)
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        rows = {}
        for line in process.stdout.splitlines():
            found = re.match(r'^\s*•\s+(\S+)\s+\[([^\]]*)\]\s?(.*)$', line)
            if found:
                rows[found.group(1)] = (found.group(2), found.group(3))
        self.assertTrue(rows, process.stdout)
        return rows

    def test_every_skill_has_the_ids_of_its_layers(self):
        flags = [item for layer_id, path in self.layer_paths.items() for item in ('--layer', f'{layer_id}={path}')]
        for how, args in (('config rule', ()), ('--layer flags', tuple(flags))):
            with self.subTest(how=how):
                rows = self.rows(*args)
                self.assertEqual({name: layers for name, (layers, _text) in rows.items()},
                                 {'core-only': 'core', 'org-only': 'org', 'mine': 'personal',
                                  'shared': 'core,personal'})
                self.assertEqual(rows['shared'][1], 'personal shared first line')   # the winning provider

    def test_without_a_personal_override_the_skill_belongs_to_core_only(self):
        rows = self.rows('--layer', f'core={self.layer_paths["core"]}', '--layer', f'org={self.layer_paths["org"]}')
        self.assertEqual(rows['shared'], ('core', 'core shared first line'))
        self.assertNotIn('mine', rows)


# ====================================================================== gap 3: D73, leakcheck BLOCKED on links


@needs_os_symlinks
class LeakcheckLinkBlockedTest(LayersCase):
    SECRET = 'UNIQUEGAPSPATTERN'

    def setUp(self):
        super().setUp()
        self.core = self.git_layer('core', 'core', {'skills/x/SKILL.md': 'core skill\n'})
        self.org = self.git_layer('acme', 'org', {'skills/y/SKILL.md': 'org skill\n'})
        self.write_config(layers=[('core', 'core', self.core), ('acme', 'org', self.org)],
                          rules=[(['**'], ['core', 'acme'])],
                          leakguard={'targets': {'core': {'scope': 'public', 'authors': ['dev@example.test']},
                                                 'acme': {'scope': 'org-private', 'authors': ['dev@example.test']}}})
        self.outside = self.work / 'outside.md'
        write(self.outside, 'outside\n')

    def additions(self):
        return f"schema: 1\npatterns:\n  - id: extra\n    category: custom\n    regex: '{self.SECRET}'\n"

    def leakcheck(self, path, *args):
        return run_json(self, 'leakcheck', str(path), *args)

    def assert_blocked(self, path, args, rel):
        code, report = self.leakcheck(path, *args)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertRegex(report.get('reason', ''), r'symlink|junction', report)
        self.assertIn(rel, report.get('reason', ''), report)
        self.assertEqual(report.get('findings'), [], report)
        return report

    def test_a_link_in_the_layer_is_blocked_in_worktree_and_staged_mode(self):
        os.symlink(self.outside, self.org / 'skills' / 'y' / 'link.md')
        self.assertEqual(self.leakcheck(self.org, '--no-config')[0], 0, 'generic mode: the link alone is no leak')
        for state in ('untracked', 'staged'):
            if state == 'staged':
                git(self.org, 'add', '-A')
            for args in (('--worktree',), ('--staged',)):
                with self.subTest(state=state, mode=args[0]):
                    self.assert_blocked(self.org, args, 'skills/y/link.md')
        git(self.org, 'rm', '-q', '--cached', 'skills/y/link.md')
        (self.org / 'skills' / 'y' / 'link.md').unlink()
        for args in (('--worktree',), ('--staged',)):
            with self.subTest(after='link removed', mode=args[0]):
                self.assertEqual(self.leakcheck(self.org, *args)[0], 0)

    def test_a_linked_patterns_file_of_the_layer_is_blocked_in_both_modes(self):
        real = self.work / 'real-patterns.yaml'
        write(real, self.additions())
        (self.org / '.leakguard').mkdir()
        os.symlink(real, self.org / '.leakguard' / 'patterns.yaml')
        write(self.org / 'doc.md', f'has {self.SECRET} inside\n')
        git(self.org, 'add', 'doc.md')
        for args in (('--worktree',), ('--staged',)):
            with self.subTest(mode=args[0]):
                report = self.assert_blocked(self.org, args, '.leakguard/patterns.yaml')
                self.assertNotIn(self.SECRET, json.dumps(report))
                self.assertNotIn('real-patterns', json.dumps(report))

    def test_a_linked_patterns_file_of_a_target_repository_is_blocked_in_both_modes(self):
        real = self.work / 'real-patterns.yaml'
        write(real, self.additions())
        repo = self.make_repo('target-repo')
        write(repo / 'doc.md', f'has {self.SECRET} inside\n')
        git(repo, 'add', 'doc.md')
        (repo / '.leakguard').mkdir()
        os.symlink(real, repo / '.leakguard' / 'patterns.yaml')
        for extra in ((), ('--no-config',)):
            for args in (('--worktree',), ('--staged',)):
                with self.subTest(inputs=extra or 'config', mode=args[0]):
                    report = self.assert_blocked(repo, args + extra, '.leakguard/patterns.yaml')
                    self.assertNotIn(self.SECRET, json.dumps(report))
        (repo / '.leakguard' / 'patterns.yaml').unlink()
        write(repo / '.leakguard' / 'patterns.yaml', self.additions())
        for args in (('--worktree',), ('--staged',)):
            with self.subTest(after='regular file', mode=args[0]):
                code, report = self.leakcheck(repo, *args, '--no-config')
                self.assertEqual((code, report['status']), (1, 'FAIL'), report)   # the additions are read: a leak


# ====================================================================== gaps 4, 5: K15, the core layer instead of the bridge


OLD_LIBRARY_FILES = {
    'skills/feature/SKILL.md': SKILL_TEXT.format('feature'),
    'skills/feature/references/ref.md': 'reference\n',
    'agents/implementer.md': '---\nname: implementer\ndescription: role\n---\nImplement.\n',
    'instructions/_base.md': '# Base\n\nShared rules.\n\n<!-- SKILLSYNC:BASE-END -->\n',
    'instructions/proj.md': '# Template\n\nProject rules.\n',
    'workflow/defaults.yaml': yaml_text({'workflow': {'guardrails': {'rules': [
        {'id': 'bridge-rule', 'paths': ['*.py'], 'forbidden': 'BRIDGE_FORBIDDEN'}]}}}),
}


class CoreCase(LayersCase):
    """The old library holds a `layer.yaml`, so `layers migrate` registers it as a real core layer (the 0.9.x bridge
    is no longer created)."""

    def setUp(self):
        super().setUp()
        self.library = self.work / 'old-library'
        make_layer(self.library, 'core', 'core', OLD_LIBRARY_FILES, readme=False, changelog=False)
        self.projects = self.work / 'repos'
        self.projects.mkdir()
        self.config_file.write_text(f'library: {self.library}\n' + yaml_text({'discover_paths': [str(self.projects)]}),
                                    encoding='utf-8')
        code, report = run_json(self, 'layers', 'migrate', '--apply')
        self.assertEqual(code, 0, report)
        self.assertNotIn('synthetic', self.core_entry(), self.core_entry())

    def core_entry(self):
        import yaml
        config = yaml.safe_load(self.config_file.read_text(encoding='utf-8'))
        return next(entry for entry in config['layers'] if entry['id'] == 'core')


class CoreGuardChildTest(CoreCase):
    def test_validate_gives_the_guard_child_the_core_layer_and_the_child_passes(self):
        app = git_project(self.projects / 'app', {'skills': ['feature'], 'claude': False, 'codex': False,
                                                  'mcp': False})
        code, report = run_json(self, 'validate', str(app), '--files', 'app.py')
        self.assertEqual(code, 0, report)
        guard = next(item for item in report['checks'] if item['id'] == 'project-guard')   # rule only in the core
        pairs = [guard['argv'][i + 1] for i, item in enumerate(guard['argv']) if item == '--layer']
        self.assertEqual(pairs, [f'core={self.library.resolve()}'], guard['argv'])
        code, report = run_json(self, 'validate', str(app), '--files', 'app.py', '--run')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        result = next(item for item in report['results'] if item['id'] == 'project-guard')
        self.assertEqual((result['status'], result['exit_code']), ('PASS', 0), result)
        write(app / 'app.py', 'BRIDGE_FORBIDDEN = 1\n')
        code, report = run_json(self, 'validate', str(app), '--files', 'app.py', '--run')
        result = next(item for item in report['results'] if item['id'] == 'project-guard')
        self.assertEqual((report['status'], result['status']), ('FAIL', 'FAIL'), report)   # the core rule ran

    def test_the_child_command_accepts_a_plain_layer_flag_and_refuses_the_synthetic_marker(self):
        app = git_project(self.projects / 'direct', {'skills': ['feature'], 'claude': False, 'codex': False,
                                                     'mcp': False})
        code, report = run_json(self, 'guard', str(app), '--layer', f'core={self.library}', '--files', 'app.py', '--run')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)
        other = self.work / 'other-layer'
        make_layer(other, 'other', 'org')
        for label, flags in {'the core': ['--layer', f'core={self.library},synthetic'],
                             'a second layer': ['--layer', f'core={self.library}', '--layer', f'other={other},synthetic']}.items():
            with self.subTest(label):
                code, report = run_json(self, 'guard', str(app), *flags, '--files', 'app.py', '--run')
                # `,synthetic` is refused for every layer: the 0.9.x bridge is gone
                self.assertEqual((code, report.get('status')), (2, 'BLOCKED'), report)
                self.assertIn('synthetic', report.get('reason', ''), report)
                self.assertIn('no longer supported', report.get('reason', ''), report)


class BridgeConfigValidateTest(LayersCase):
    def test_validate_on_a_0_9_x_bridge_config_is_blocked_with_the_migration_instruction(self):
        from layers_helpers import generator_version
        library = self.work / 'old-library'
        for rel, text in OLD_LIBRARY_FILES.items():
            write(library / rel, text)
        projects = self.work / 'repos'
        projects.mkdir()
        version = generator_version()
        self.config_file.write_text(yaml_text({
            'discover_paths': [str(projects)], 'legacy_library': str(library),
            'layers': [{'id': 'core', 'kind': 'core', 'path': str(library), 'writable': False,
                        'synthetic': {'version': version, 'core_min': version}}],
            'layer_rules': [{'match': ['**'], 'use': ['core']}], 'isolation': {'allowed_layers': ['core']}}),
            encoding='utf-8')
        app = git_project(projects / 'app', {'skills': ['feature'], 'claude': False, 'codex': False, 'mcp': False})
        before = tree_snapshot(app)
        process = self.cli('validate', str(app), '--files', 'app.py', '--run', '--json')
        self.assertNotIn('Traceback', process.stderr)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('layers migrate --layer-source core=PATH --apply', process.stdout + process.stderr)
        self.assertEqual(tree_snapshot(app), before)


class CoreCopyModeTest(CoreCase):
    """K15 (a): in copy mode a local edit of a copied file is reported as a divergence and never written to the
    (read-only) core layer."""

    def test_a_local_edit_of_a_core_file_is_reported_and_never_written_to_the_core_layer(self):
        project = self.projects / 'copy-app'
        write(project / '.github' / '.skillsync.yaml', yaml_text({
            'skills': ['feature'], 'instructions': {'merge': ['_base'], 'template': 'proj'}, 'claude': False,
            'codex': False, 'mcp': False}))
        with link_mode('copy'):
            linked = self.cli('link', str(project))
        self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
        copied = project / '.github' / 'skills' / 'feature' / 'references' / 'ref.md'
        self.assertFalse(copied.is_symlink(), 'copy mode makes a copy')
        self.assertEqual(copied.read_text(encoding='utf-8'), 'reference\n')
        copied.write_text('edited locally\n', encoding='utf-8')
        before = tree_snapshot(self.library)
        with link_mode('copy'):
            relinked = self.cli('link', str(project))
        output = relinked.stdout + relinked.stderr
        self.assertNotIn('Traceback', relinked.stderr)
        self.assertEqual(relinked.returncode, 0, output)
        self.assertRegex(output, r'(?m)^\[info\] Local edits in .*feature', output)
        self.assertEqual(copied.read_text(encoding='utf-8'), 'edited locally\n', 'the local edit is kept')
        self.assertEqual(tree_snapshot(self.library), before, 'link wrote into the old library')
        # the core layer is read-only (`writable: false`): without `--to-layer core` the edit is only skipped (info),
        # with it `push` is BLOCKED; neither writes into the layer and the local edit stays
        for extra in ((), ('--dry-run',)):
            with self.subTest(args=extra), link_mode('copy'):
                pushed = self.cli('push', str(project), *extra)
                output = pushed.stdout + pushed.stderr
                self.assertNotIn('Traceback', pushed.stderr)
                self.assertEqual(pushed.returncode, 0, output)
                skipped = [line for line in output.splitlines() if line.startswith('[info]') and 'ref.md' in line]
                self.assertTrue(skipped, output)
                self.assertIn('written only with `--to-layer core`; skipped', skipped[0])
                self.assertEqual(tree_snapshot(self.library), before, 'push wrote into the core layer')
                self.assertEqual(copied.read_text(encoding='utf-8'), 'edited locally\n')
        with link_mode('copy'):
            pushed = self.cli('push', str(project), '--to-layer', 'core')
        output = pushed.stdout + pushed.stderr
        self.assertNotIn('Traceback', pushed.stderr)
        self.assertEqual(pushed.returncode, 2, output)
        blocked = [line for line in output.splitlines() if line.startswith('[blocked]') and 'ref.md' in line]
        self.assertTrue(blocked, output)
        self.assertIn('layer core is read-only (writable: false)', blocked[0])
        self.assertEqual(tree_snapshot(self.library), before, 'push wrote into the core layer')
        self.assertEqual(copied.read_text(encoding='utf-8'), 'edited locally\n')


if __name__ == '__main__':
    unittest.main()
