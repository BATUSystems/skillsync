"""CLAUDE-6: `tools/claude-headless/run.py`, the read-only jobs for the real `claude -p` (release audit, documentation drift).

Nothing here calls the real `claude`: the arguments, the forced flags, the copy built from a git ref, the ledger and the classification of the result are
tested directly, and one subprocess runs a fake `claude` (a script with a shebang, so those tests are skipped on Windows)."""
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tmp_paths import child  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402

RUN_PY = ROOT / 'tools' / 'claude-headless' / 'run.py'
JOBS = ROOT / 'tools' / 'claude-headless' / 'jobs'
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}
GOOD = {'findings': [{'id': 'F1', 'severity': 'warning', 'kind': 'docs-stale', 'file': 'docs/cli.md', 'claim': 'c', 'evidence': 'e'}], 'summary': 's'}


def load():
    spec = importlib.util.spec_from_file_location('claude_headless_run_under_test', RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding='utf-8')
    return Path(path)


class Tmp(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = tempfile.TemporaryDirectory(prefix='claude-headless-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        (self.base / 'profile').mkdir()
        self.profiles = write(self.base / 'profiles.txt', f'# allowed\n{self.base / "profile"}\n')

    def git(self, repo, *argv):
        done = subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false', *argv], capture_output=True, text=True,
                              env={**os.environ, **GIT_ENV})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def make_repo(self):
        """A repository with two tags: public paths, and paths that must never reach the copy (plans, specs, a nested state dir, tests, tools)."""
        repo = self.base / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        for name, text in (('CHANGELOG.md', '# Changelog\n## [1.0.0]\n'), ('README.md', 'readme\n'), ('skillsync', 'print(1)\n'),
                           ('docs/cli.md', 'cli 1\n'), ('docs/domain/a.md', 'domain\n'), ('library/CHANGELOG.md', '## [1.0.0]\n'),
                           ('library/skills/x/SKILL.md', 'skill\n'), ('skillsync_workflow/mod.py', 'v = 1\n'),
                           ('docs/plans/p.md', 'SECRET PLAN\n'), ('docs/specs/s.md', 'SECRET SPEC\n'), ('.skillsync/runs/r.json', 'STATE\n'),
                           ('tests/test_a.py', 'x\n'), ('tools/t.py', 'x\n'), ('library/.skillsync/nested.txt', 'NESTED\n')):
            write(repo / name, text)
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'one')
        self.git(repo, 'tag', 'v1')
        write(repo / 'CHANGELOG.md', '# Changelog\n## [1.1.0]\n- new\n')
        write(repo / 'docs/cli.md', 'cli 2\n')
        write(repo / 'docs/extra.md', 'extra\n')
        write(repo / 'skillsync_workflow/mod.py', 'v = 2\n')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'two')
        self.git(repo, 'tag', 'v2')
        return repo

    def args(self, *extra, task='release-audit', tags=True):
        base = [task, '--model', 'sonnet', '--max-cost-usd', '1.5', '--config-dir', str(self.base / 'profile'), '--profiles-file', str(self.profiles)]
        if tags:
            base += ['--tag-from', 'v1', '--tag-to', 'v2'] if task == 'release-audit' else ['--ref', 'v2']
        return base + list(extra)


class Refusals(Tmp):
    def refused(self, argv):
        with self.assertRaises(SystemExit) as caught:
            self.mod.parse_args(argv)
        self.assertEqual(caught.exception.code, 2)

    def test_a_cost_limit_is_required_and_bounded(self):
        self.refused(['release-audit', '--model', 'sonnet', '--config-dir', str(self.base / 'profile'), '--tag-from', 'v1', '--tag-to', 'v2'])
        for bad in ('0', '-1', '5.01', 'x'):
            with self.subTest(bad=bad):
                self.refused(['release-audit', '--model', 'sonnet', '--max-cost-usd', bad, '--config-dir', str(self.base / 'profile'), '--tag-from', 'v1', '--tag-to', 'v2'])

    def test_the_profile_must_be_absolute_and_existing(self):
        for bad in ('relative', str(self.base / 'missing')):
            with self.subTest(bad=bad):
                self.refused(['docs-drift', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', bad, '--ref', 'v2'])

    def test_model_refs_and_task_parameters_are_validated(self):
        self.refused(self.args('--model', 'a b'))
        self.refused(['release-audit', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'), '--tag-from', 'v1'])
        self.refused(['docs-drift', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile')])
        self.refused(self.args(task='docs-drift') + ['--tag-from', 'v1'])
        self.refused(['no-such-job', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile')])
        for ref in ('-x', 'a..b', 'a b', ';rm', ''):
            with self.subTest(ref=ref):
                self.refused(['docs-drift', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'), '--ref', ref])

    def test_the_total_budget_can_only_be_lowered(self):
        for bad in ('12.5', '0', '-1', 'nan', 'x'):
            with self.subTest(bad=bad):
                self.refused(self.args('--total-budget-usd', bad))
        for good, expected in (('3', 3.0), ('6', 6.0), ('12', 12.0)):
            self.assertEqual(self.mod.parse_args(self.args('--total-budget-usd', good)).total_budget_usd, expected)
        self.assertEqual(self.mod.parse_args(self.args()).total_budget_usd, 12.0)

    def test_the_turn_limit_is_bounded(self):
        self.refused(self.args('--max-turns', '0'))
        self.refused(self.args('--max-turns', '31'))
        self.assertEqual(self.mod.parse_args(self.args()).max_turns, 25)


class ProfileAllowlist(Tmp):
    def test_only_a_listed_profile_is_accepted_whatever_its_name(self):
        self.mod.check_profile(str(self.base / 'profile'), str(self.profiles))
        other = child(self.base, 'another-profile')
        other.mkdir()
        with self.assertRaises(self.mod.Refusal):
            self.mod.check_profile(str(other), str(self.profiles))
        with self.assertRaises(self.mod.Refusal):
            self.mod.check_profile(str(self.base / 'profile'), str(self.base / 'no-such-file.txt'))   # a missing file allows nothing
        write(self.profiles, '# only comments\n\n')
        with self.assertRaises(self.mod.Refusal):
            self.mod.check_profile(str(self.base / 'profile'), str(self.profiles))

    @needs_os_symlinks
    def test_a_link_is_judged_by_its_target(self):
        link_to_unlisted = child(self.base, 'innocent-name')
        other = child(self.base, 'unlisted')
        other.mkdir()
        link_to_unlisted.symlink_to(other)
        with self.assertRaises(self.mod.Refusal):
            self.mod.check_profile(str(link_to_unlisted), str(self.profiles))
        link_to_listed = child(self.base, 'alias')
        link_to_listed.symlink_to(self.base / 'profile')
        self.mod.check_profile(str(link_to_listed), str(self.profiles))

    def test_a_relative_line_of_the_profiles_file_allows_nothing(self):
        write(self.profiles, 'profile\n')
        with self.assertRaises(self.mod.Refusal):
            self.mod.check_profile(str(self.base / 'profile'), str(self.profiles))
        self.assertEqual(self.mod.allowed_profiles(self.profiles), set())

    def test_an_unlisted_profile_stops_the_command_even_in_a_dry_run(self):
        other = child(self.base, 'unlisted-profile')
        other.mkdir()
        repo = self.make_repo()
        done = subprocess.run([sys.executable, str(RUN_PY), 'docs-drift', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(other), '--ref', 'v2',
                               '--profiles-file', str(self.profiles), '--repo', str(repo), '--dry-run'], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('is not listed', done.stderr)
        self.assertEqual(done.stdout, '')


class ForcedFlags(Tmp):
    def dry_run(self, task='release-audit'):
        repo = self.make_repo()
        done = subprocess.run([sys.executable, str(RUN_PY), *self.args('--repo', str(repo), '--dry-run', task=task)], capture_output=True, text=True,
                              env={**os.environ, 'SECRET_TOKEN': 'x', 'ANTHROPIC_API_KEY': 'k'}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_command_has_exactly_the_forced_flags(self):
        plan = self.dry_run()
        argv = plan['argv']
        for flag in ('-p', '--no-session-persistence', '--restricted', '--strict-mcp-config', '--disable-slash-commands'):
            self.assertEqual(argv.count(flag), 1, flag)
        for flag, value in (('--output-format', 'json'), ('--permission-prompts', 'none'), ('--tools', 'Read,Grep,Glob'), ('--max-budget-usd', '1.5'),
                            ('--max-turns', '25'), ('--model', 'sonnet')):
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)
        schema = argv[argv.index('--json-schema') + 1]
        self.assertEqual(json.loads(schema), json.loads((JOBS / 'release-audit' / 'schema.json').read_text(encoding='utf-8')))   # the schema itself, not a path
        for forbidden in ('--bare', '--dangerously-skip-permissions', '--allowedTools', '--allowed-tools', '--setting-sources', '--mcp-config', '--add-dir'):
            self.assertNotIn(forbidden, argv)

    def test_the_whole_argv_is_exactly_the_expected_list(self):
        schema = json.dumps(json.loads((JOBS / 'release-audit' / 'schema.json').read_text(encoding='utf-8')), separators=(',', ':'))
        expected = ['claude', '-p', '--output-format', 'json', '--no-session-persistence', '--permission-prompts', 'none', '--restricted', '--tools', 'Read,Grep,Glob',
                    '--strict-mcp-config', '--disable-slash-commands', '--json-schema', schema, '--max-budget-usd', '1.5', '--max-turns', '25', '--model', 'sonnet']
        self.assertEqual(self.dry_run()['argv'], expected)

    def test_the_prompt_goes_through_stdin_with_the_tags_filled_in(self):
        plan = self.dry_run()
        self.assertIn('`v2`', plan['stdin'])
        self.assertIn('`v1`', plan['stdin'])
        self.assertNotIn('{TO}', plan['stdin'])
        self.assertNotIn('You audit one release', ' '.join(plan['argv']))
        self.assertEqual(set(plan['refs']), {'v1', 'v2'})

    def test_the_environment_is_the_allowlist_plus_the_profile(self):
        env = self.dry_run()['env']
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.base / 'profile'))
        self.assertEqual(env['HOME'], '<temporary home>')
        self.assertTrue(set(env) <= {'PATH', 'HOME', 'LANG', 'CLAUDE_CONFIG_DIR'} | {k for k in env if k.startswith('LC_')}, sorted(env))

    def test_the_other_job_takes_a_ref_and_has_its_own_prompt(self):
        plan = self.dry_run('docs-drift')
        self.assertIn('`v2`', plan['stdin'])
        self.assertIn('docs/cli.md', plan['stdin'])
        self.assertEqual(list(plan['refs']), ['v2'])


class Schema(Tmp):
    def test_the_validator_covers_the_subset_of_our_schemas(self):
        schema = json.loads((JOBS / 'release-audit' / 'schema.json').read_text(encoding='utf-8'))
        self.assertEqual(self.mod.validate(GOOD, schema), [])
        bad = json.loads(json.dumps(GOOD))
        bad['findings'][0]['severity'] = 'fatal'
        del bad['summary']
        bad['findings'][0]['extra'] = 1
        errors = ' | '.join(self.mod.validate(bad, schema))
        for part in ("'fatal' is not one of", "missing required 'summary'", "unexpected property 'extra'"):
            self.assertIn(part, errors)
        self.assertTrue(self.mod.validate({'findings': 'x', 'summary': 's'}, schema))
        self.assertTrue(self.mod.validate([], schema))

    def test_numbers_booleans_lengths_and_items(self):
        validate = self.mod.validate
        self.assertTrue(validate(True, {'type': 'integer'}))
        self.assertEqual(validate(3, {'type': 'integer', 'minimum': 1, 'maximum': 5}), [])
        self.assertTrue(validate(9, {'type': 'integer', 'maximum': 5}))
        self.assertTrue(validate('', {'type': 'string', 'minLength': 1}))
        self.assertTrue(validate([1, 2, 3], {'type': 'array', 'maxItems': 2}))
        self.assertTrue(validate([1, 'x'], {'type': 'array', 'items': {'type': 'integer'}}))
        self.assertEqual(validate(None, {'type': 'null'}), [])

    def test_a_schema_with_an_unknown_keyword_is_refused_and_ours_have_none(self):
        self.assertTrue(self.mod.check_schema_keywords({'type': 'object', 'pattern': 'x'}))
        self.assertTrue(self.mod.check_schema_keywords({'type': 'object', 'properties': {'a': {'oneOf': []}}}))
        for job in sorted(path.name for path in JOBS.iterdir() if path.is_dir()):
            with self.subTest(job=job):
                self.assertEqual(self.mod.check_schema_keywords(json.loads((JOBS / job / 'schema.json').read_text(encoding='utf-8'))), [])
                for name in ('prompt.md', 'inputs.md', 'schema.json'):
                    self.assertTrue((JOBS / job / name).is_file(), name)
        self.assertEqual(sorted(path.name for path in JOBS.iterdir() if path.is_dir()), sorted(self.mod.TASKS))

    def test_classification_of_the_output_of_claude(self):
        schema = json.loads((JOBS / 'release-audit' / 'schema.json').read_text(encoding='utf-8'))
        classify = self.mod.classify
        ok = json.dumps({'subtype': 'success', 'is_error': False, 'structured_output': GOOD, 'total_cost_usd': 0.42})
        self.assertEqual(classify(0, ok, schema), (0, 'ok', GOOD, 0.42))
        cases = {
            'not json': (0, 'garbage', 1), 'a list': (0, '[]', 1),
            'budget': (1, json.dumps({'subtype': 'error_max_budget_usd', 'is_error': True, 'total_cost_usd': 0.5}), 3),
            'turns': (1, json.dumps({'subtype': 'error_max_turns', 'is_error': True}), 1),
            'error': (1, json.dumps({'subtype': 'success', 'is_error': True}), 1),
            'no structured output (an unsatisfiable schema ends with success and text only)': (0, json.dumps({'subtype': 'success', 'is_error': False, 'result': 'text'}), 1),
            'structured output outside the schema': (0, json.dumps({'subtype': 'success', 'is_error': False, 'structured_output': {'findings': 3, 'summary': 's'}}), 1),
        }
        for name, (code, text, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify(code, text, schema)[0], expected)
        self.assertEqual(classify(1, json.dumps({'subtype': 'error_max_budget_usd', 'total_cost_usd': 0.5}), schema)[3], 0.5)
        for text in ('{"subtype": "success", "total_cost_usd": NaN}', '{"subtype": "success", "total_cost_usd": -1}', '{"subtype": "success", "total_cost_usd": Infinity}',
                     '{"subtype": "success", "total_cost_usd": true}', '{"subtype": "success"}', 'garbage', '[]'):
            with self.subTest(text=text):
                self.assertIsNone(classify(0, text, schema)[3], 'an unknown or invalid cost is None: the caller books the limit')


class TheCopy(Tmp):
    def copy_of(self, repo, ref):
        dest = child(self.base, 'copy-' + ref)
        dest.mkdir()
        self.mod.build_copy(repo, self.mod.resolve_ref(repo, ref), dest)
        return dest

    def test_only_the_allowlisted_public_paths_are_in_the_copy(self):
        dest = self.copy_of(self.make_repo(), 'v2')
        files = sorted(path.relative_to(dest).as_posix() for path in dest.rglob('*') if path.is_file())
        self.assertEqual(files, ['CHANGELOG.md', 'README.md', 'docs/cli.md', 'docs/domain/a.md', 'docs/extra.md', 'library/CHANGELOG.md',
                                 'library/skills/x/SKILL.md', 'skillsync', 'skillsync_workflow/mod.py'])
        text = ' '.join(path.read_text(encoding='utf-8') for path in dest.rglob('*') if path.is_file())
        for secret in ('SECRET PLAN', 'SECRET SPEC', 'STATE', 'NESTED'):
            self.assertNotIn(secret, text)
        self.assertFalse((dest / '.git').exists())
        self.assertEqual(self.mod.scan_copy(dest), [])

    def test_a_path_missing_in_an_old_tag_does_not_break_the_archive(self):
        repo = self.base / 'old'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        write(repo / 'CHANGELOG.md', 'c\n')
        write(repo / 'skillsync_workflow/m.py', 'x\n')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'old')
        self.git(repo, 'tag', 'v0')
        dest = self.copy_of(repo, 'v0')
        self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file()), ['CHANGELOG.md', 'skillsync_workflow/m.py'])

    def test_a_tag_without_any_allowlisted_path_is_refused(self):
        repo = self.base / 'empty'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        write(repo / 'other.txt', 'x\n')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'x')
        self.git(repo, 'tag', 'v0')
        with self.assertRaises(self.mod.Refusal):
            self.copy_of(repo, 'v0')

    def test_the_canary_finds_a_path_outside_the_allowlist(self):
        dest = self.copy_of(self.make_repo(), 'v2')
        for relative in ('docs/plans/leak.md', '.skillsync/x', 'tools/t.py', 'library/.skillsync/n.txt', 'docs/sub/deep.md'):
            with self.subTest(relative=relative):
                write(dest / relative, 'x')
                self.assertEqual(self.mod.scan_copy(dest), [relative])
                (dest / relative).unlink()
        self.assertEqual(self.mod.scan_copy(dest), [])

    def test_names_with_special_characters_are_taken_literally(self):
        repo = self.make_repo()
        write(repo / 'docs' / 'zażółć.md', 'unicode\n')
        write(repo / 'docs' / 'a[1].md', 'bracket\n')
        write(repo / 'docs' / 'sp ace.md', 'space\n')
        self.git(repo, '-c', 'core.quotePath=true', 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'names')
        self.git(repo, 'tag', 'v3')
        dest = self.copy_of(repo, 'v3')
        for name in ('zażółć.md', 'a[1].md', 'sp ace.md'):
            self.assertTrue((dest / 'docs' / name).is_file(), name)
        write(repo / 'docs' / 'zażółć.md', 'unicode 2\n')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'names 2')
        self.git(repo, 'tag', 'v4')
        self.mod.write_audit_files(repo, dest, self.mod.resolve_ref(repo, 'v3'), self.mod.resolve_ref(repo, 'v4'))
        self.assertIn('+unicode 2', (dest / '_audit' / 'diff.patch').read_text(encoding='utf-8'))

    @needs_os_symlinks
    def test_a_link_inside_an_allowed_directory_never_reaches_the_copy(self):
        repo = self.make_repo()
        (repo / 'library' / 'sneaky').symlink_to('../docs/plans/p.md')
        (repo / 'docs' / 'sneaky.md').symlink_to('../.skillsync/runs/r.json')
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'links')
        self.git(repo, 'tag', 'v5')
        dest = self.copy_of(repo, 'v5')
        self.assertFalse(os.path.lexists(dest / 'library' / 'sneaky'))
        self.assertFalse(os.path.lexists(dest / 'docs' / 'sneaky.md'))
        text = ' '.join(path.read_text(encoding='utf-8') for path in dest.rglob('*') if path.is_file())
        self.assertNotIn('SECRET PLAN', text)
        self.assertNotIn('STATE', text)

    def test_the_audit_files_hold_the_patch_of_docs_and_only_the_statistics_of_the_code(self):
        repo = self.make_repo()
        dest = self.copy_of(repo, 'v2')
        size = self.mod.write_audit_files(repo, dest, self.mod.resolve_ref(repo, 'v1'), self.mod.resolve_ref(repo, 'v2'))
        patch = (dest / '_audit' / 'diff.patch').read_text(encoding='utf-8')
        stat_text = (dest / '_audit' / 'diffstat.txt').read_text(encoding='utf-8')
        self.assertIn('+- new', patch)
        self.assertIn('-cli 1', patch)
        self.assertIn('docs/extra.md', patch)
        self.assertNotIn('v = 2', patch)
        self.assertIn('skillsync_workflow/mod.py', stat_text)
        for name in ('SECRET', 'plans', 'specs', '.skillsync', 'tests/', 'tools/'):
            self.assertNotIn(name, patch + stat_text)
        self.assertEqual(size, len(patch.encode()) + len(stat_text.encode()))
        self.assertEqual(self.mod.scan_copy(dest), [])   # `_audit/` is allowed

    def test_an_unknown_ref_is_a_refusal_that_names_it(self):
        repo = self.make_repo()
        with self.assertRaises(self.mod.Refusal) as caught:
            self.mod.resolve_ref(repo, 'no-such-tag')
        self.assertIn("'no-such-tag' does not exist", str(caught.exception))
        self.assertEqual(len(self.mod.resolve_ref(repo, 'v1')), 40)


class ExtractTar(Tmp):
    """The first layer of the allowlist, on a synthetic archive (works on every system)."""

    def archive(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            def regular(name, text=b'x'):
                info = tarfile.TarInfo(name)
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text))
            regular('docs/ok.md')
            regular('library/ok.md')
            regular('docs/plans/p.md')
            regular('docs/specs/s.md')
            regular('.git/config')
            regular('.skillsync/x')
            regular('library/.skillsync/y')
            regular('_audit/fake.patch')
            regular('tests/t.py')
            regular('/abs.txt')
            regular('../escape.txt')
            regular('docs/../../escape2.md')
            for name, kind in (('library/symlink', tarfile.SYMTYPE), ('library/hardlink', tarfile.LNKTYPE), ('library/device', tarfile.CHRTYPE), ('library/fifo', tarfile.FIFOTYPE)):
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = 'docs/ok.md'
                tar.addfile(info)
        return buffer.getvalue()

    def test_only_regular_allowlisted_members_are_written(self):
        dest = child(self.base, 'dest')
        dest.mkdir()
        self.mod.extract_tar(self.archive(), dest)
        self.assertEqual(sorted(p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file() or p.is_symlink()), ['docs/ok.md', 'library/ok.md'])
        self.assertFalse((self.base / 'escape.txt').exists())
        self.assertFalse((self.base.parent / 'escape.txt').exists())
        self.assertFalse((self.base / 'escape2.md').exists())

    def test_the_audit_directory_is_only_for_the_host(self):
        self.assertFalse(self.mod.allowed_relative(Path('_audit/diff.patch')))
        self.assertTrue(self.mod.allowed_relative(Path('_audit/diff.patch'), audit=True))


class Ledger(Tmp):
    def test_the_ledger_refuses_over_the_calls_and_over_the_sum(self):
        path = self.base / 'ledger.json'
        with self.mod.Ledger(path) as ledger:
            ledger.check(1.5, 12.0)
            for index in range(self.mod.MAX_CALLS):
                ledger.record({'n': index}, 0.1)
            with self.assertRaises(self.mod.Refusal):
                ledger.check(0.1, 12.0)
        with self.mod.Ledger(self.base / 'sum.json') as ledger:
            ledger.record({'n': 1}, 10.0)
            ledger.check(2.0, 12.0)
            with self.assertRaises(self.mod.Refusal):
                ledger.check(2.01, 12.0)
            with self.assertRaises(self.mod.Refusal):
                ledger.check(1.0, 10.5)

    def test_a_corrupt_ledger_is_a_refusal_not_a_traceback(self):
        for text in ('{broken', '[]', '{"calls": "x", "spent": 1}', ''):
            with self.subTest(text=text):
                path = write(self.base / 'ledger.json', text)
                with self.assertRaises(self.mod.Refusal):
                    with self.mod.Ledger(path) as ledger:
                        ledger.check(1.0, 12.0)

    def test_the_write_is_atomic(self):
        path = self.base / 'ledger.json'
        with self.mod.Ledger(path) as ledger:
            ledger.record({'n': 1}, 0.1)
        self.assertFalse((self.base / 'ledger.json.tmp').exists())
        before = path.read_text(encoding='utf-8')
        real_replace = os.replace

        def interrupted(source, target):   # the new content is written to the side file, the swap fails: the ledger must stay as it was
            raise OSError('interrupted')
        self.mod.os.replace = interrupted
        try:
            with self.assertRaises(OSError):
                with self.mod.Ledger(path) as ledger:
                    ledger.record({'n': 2}, 0.2)
        finally:
            self.mod.os.replace = real_replace
        self.assertEqual(path.read_text(encoding='utf-8'), before)
        self.assertEqual(json.loads(before)['calls'], 1)

    @unittest.skipIf(os.name == 'nt', 'POSIX only: file locks (fcntl)')
    def test_two_runs_are_serialized_by_the_lock(self):
        path = self.base / 'ledger.json'
        order = []
        release = threading.Event()
        entered = threading.Event()

        def first():
            with self.mod.Ledger(path) as ledger:
                entered.set()
                order.append('first in')
                release.wait(5)
                ledger.record({'n': 1}, 0.1)
                order.append('first out')

        def second():
            entered.wait(5)
            with self.mod.Ledger(path) as ledger:
                order.append('second in')
                ledger.check(1.0, 12.0)
                ledger.record({'n': 2}, 0.1)

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        entered.wait(5)
        threads[1].join(0.3)
        self.assertTrue(threads[1].is_alive(), 'the second run must wait for the lock')
        release.set()
        for thread in threads:
            thread.join(10)
        self.assertEqual(order, ['first in', 'first out', 'second in'])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['calls'], 2)

    def test_the_record_accumulates_and_survives_a_new_instance(self):
        path = self.base / 'ledger.json'
        for cost in (0.25, 0.5):
            with self.mod.Ledger(path) as ledger:
                ledger.record({'task': 't'}, cost)
        data = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual((data['calls'], data['spent'], len(data['entries'])), (2, 0.75, 2))


class FakeClaude(Tmp):
    def fake(self, answer):
        launcher = child(self.base, 'claude')
        if os.name == 'nt':
            self.skipTest('a fake executable needs a shebang')
        record = self.base / 'record.json'
        body = ('import json, os, sys\n'
                'if sys.argv[1:] == ["--version"]:\n'
                f'    json.dump({{"cwd": os.getcwd(), "env": dict(os.environ)}}, open({str(self.base / "version.json")!r}, "w"))\n'
                '    print("9.9.9 (Claude Code)"); sys.exit(0)\n'
                f'import time\ntime.sleep({float(answer.get("sleep", 0))})\n'
                'stdin = sys.stdin.read()\n'
                'files = sorted(os.path.relpath(os.path.join(d, f), ".") for d, _, fs in os.walk(".") for f in fs)\n'
                f'json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ), "stdin": stdin, "files": files}}, open({str(record)!r}, "w"))\n'
                f'print({json.dumps(answer["raw"] if "raw" in answer else json.dumps(answer))})\n'
                f'sys.exit({1 if answer.get("is_error") else 0})\n')
        launcher.write_text(f'#!{sys.executable}\n' + body, encoding='utf-8')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
        return launcher, record

    def run_job(self, answer, *extra, repo=None):
        launcher, record = self.fake(answer)
        repo = repo or self.make_repo()
        done = subprocess.run([sys.executable, str(RUN_PY), *self.args('--repo', str(repo), '--claude-bin', str(launcher), '--ledger', str(self.base / 'ledger.json'),
                                                                       '--out', str(self.base / 'out'), *extra)],
                              capture_output=True, text=True, env={**os.environ, 'ANTHROPIC_API_KEY': 'must-not-reach-the-child', 'SECRET_TOKEN': 'x'}, timeout=120)
        return done, record

    def test_a_valid_answer_is_stored_booked_and_the_child_sees_a_clean_copy(self):
        done, record = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD, 'total_cost_usd': 0.3})
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(record.read_text(encoding='utf-8'))
        self.assertNotEqual(Path(seen['cwd']).resolve(), ROOT)
        self.assertIn('_audit/diff.patch', seen['files'])
        self.assertFalse([f for f in seen['files'] if f.startswith(('docs/plans', 'docs/specs', '.skillsync', 'tests', 'tools'))], seen['files'])
        self.assertEqual(seen['env']['CLAUDE_CONFIG_DIR'], str(self.base / 'profile'))
        self.assertNotIn('ANTHROPIC_API_KEY', seen['env'])
        self.assertNotIn('SECRET_TOKEN', seen['env'])
        self.assertNotEqual(seen['env']['HOME'], os.environ.get('HOME'))
        self.assertIn('`v2`', seen['stdin'])
        self.assertIn('--restricted', seen['argv'])
        out = self.base / 'out'
        self.assertEqual(json.loads((out / 'result.json').read_text(encoding='utf-8')), GOOD)
        meta = json.loads((out / 'metadata.json').read_text(encoding='utf-8'))
        self.assertEqual((meta['exit'], meta['claude_version'], meta['cost_usd'], meta['limit_usd']), (0, '9.9.9 (Claude Code)', 0.3, 1.5))
        ledger = json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual((ledger['calls'], ledger['spent']), (1, 0.3))
        self.assertEqual(json.loads(done.stdout)['findings'], 1)

    def test_a_result_that_does_not_fit_the_schema_is_exit_1_and_the_raw_output_is_kept(self):
        done, _ = self.run_job({'subtype': 'success', 'is_error': False, 'result': 'only text', 'total_cost_usd': 0.1})
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertTrue((self.base / 'out' / 'raw.json').is_file())
        self.assertFalse((self.base / 'out' / 'result.json').exists())
        self.assertEqual(json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))['calls'], 1)

    def test_the_cost_limit_is_exit_3_and_is_booked(self):
        done, _ = self.run_job({'subtype': 'error_max_budget_usd', 'is_error': True, 'total_cost_usd': 0.6})
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertEqual(json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))['spent'], 0.6)

    def test_the_turn_limit_is_exit_1(self):
        done, _ = self.run_job({'subtype': 'error_max_turns', 'is_error': True})
        self.assertEqual(done.returncode, 1, done.stderr)

    def test_an_unparsable_answer_is_booked_at_the_limit(self):
        done, _ = self.run_job({'raw': 'garbage after the money was spent'})
        self.assertEqual(done.returncode, 1, done.stderr)
        ledger = json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual((ledger['calls'], ledger['spent']), (1, 1.5))

    def test_a_call_that_takes_too_long_is_exit_1_and_booked_at_the_limit(self):
        done, _ = self.run_job({'subtype': 'success', 'sleep': 5}, '--timeout-seconds', '1')
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('no answer within 1 s', json.loads(done.stdout)['reason'])
        ledger = json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual((ledger['calls'], ledger['spent']), (1, 1.5))
        self.assertIsNone(ledger['entries'][0]['claude_exit'], 'no exit code is invented for a call that returned none')
        self.assertEqual(ledger['entries'][0]['exit'], 'timeout')

    def test_an_interrupt_after_the_start_books_the_limit_and_is_not_swallowed(self):
        from unittest import mock
        repo = self.make_repo()
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == 'fake-claude-bin':
                if '--version' in cmd:
                    return subprocess.CompletedProcess(cmd, 0, '9.9.9 (Claude Code)\n', '')
                raise KeyboardInterrupt
            return real_run(cmd, *args, **kwargs)
        with mock.patch.object(self.mod.subprocess, 'run', fake_run), self.assertRaises(KeyboardInterrupt):
            self.mod.main(self.args('--repo', str(repo), '--claude-bin', 'fake-claude-bin', '--ledger', str(self.base / 'ledger.json'), '--out', str(self.base / 'out')))
        ledger = json.loads((self.base / 'ledger.json').read_text(encoding='utf-8'))
        self.assertEqual((ledger['calls'], ledger['spent'], ledger['entries'][0]['exit']), (1, 1.5, 'interrupted'))

    def test_a_corrupt_ledger_stops_before_the_binary_without_a_traceback(self):
        write(self.base / 'ledger.json', '{broken')
        done, record = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        self.assertFalse(record.exists())

    def test_the_version_probe_also_runs_in_the_clean_environment_inside_the_copy(self):
        done, _ = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD, 'total_cost_usd': 0.1})
        self.assertEqual(done.returncode, 0, done.stderr)
        probe = json.loads((self.base / 'version.json').read_text(encoding='utf-8'))
        self.assertNotIn('ANTHROPIC_API_KEY', probe['env'])
        self.assertNotIn('SECRET_TOKEN', probe['env'])
        self.assertEqual(probe['env']['CLAUDE_CONFIG_DIR'], str(self.base / 'profile'))
        self.assertNotEqual(Path(probe['cwd']).resolve(), Path.cwd().resolve())

    @needs_os_symlinks
    def test_the_child_gets_the_resolved_profile_directory(self):
        alias = child(self.base, 'alias')
        alias.symlink_to(self.base / 'profile')
        launcher, record = self.fake({'subtype': 'success', 'is_error': False, 'structured_output': {'findings': [], 'summary': 'all matches'}, 'total_cost_usd': 0.1})
        done = subprocess.run([sys.executable, str(RUN_PY), 'docs-drift', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(alias), '--ref', 'v2',
                               '--profiles-file', str(self.profiles), '--repo', str(self.make_repo()), '--claude-bin', str(launcher), '--ledger', str(self.base / 'l.json'),
                               '--out', str(self.base / 'out')], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(record.read_text(encoding='utf-8'))['env']['CLAUDE_CONFIG_DIR'], os.path.realpath(self.base / 'profile'))

    def test_a_non_empty_output_directory_is_refused_before_the_binary(self):
        write(self.base / 'out' / 'earlier.txt', 'x')
        done, record = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(record.exists())
        self.assertEqual((self.base / 'out' / 'earlier.txt').read_text(encoding='utf-8'), 'x')

    def test_a_full_ledger_stops_before_the_binary(self):
        write(self.base / 'ledger.json', json.dumps({'calls': 16, 'spent': 1.0, 'entries': []}))
        done, record = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('refused', done.stderr)
        self.assertFalse(record.exists())

    def test_the_sum_over_the_total_budget_stops_before_the_binary(self):
        write(self.base / 'ledger.json', json.dumps({'calls': 2, 'spent': 11.0, 'entries': []}))
        done, record = self.run_job({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertFalse(record.exists())

    def test_an_input_over_the_size_threshold_stops_before_the_binary(self):
        repo = self.make_repo()
        write(repo / 'docs/big.md', 'x' * 500000)
        self.git(repo, 'add', '-A')
        self.git(repo, 'commit', '-q', '-m', 'big')
        self.git(repo, 'tag', 'v3')
        launcher, record = self.fake({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        done = subprocess.run([sys.executable, str(RUN_PY), 'release-audit', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'),
                               '--tag-from', 'v2', '--tag-to', 'v3', '--profiles-file', str(self.profiles), '--repo', str(repo), '--claude-bin', str(launcher), '--ledger', str(self.base / 'ledger.json'),
                               '--out', str(self.base / 'out')], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn('bytes', done.stderr)
        self.assertFalse(record.exists())
        self.assertFalse((self.base / 'ledger.json').exists())

    def test_an_unknown_tag_and_a_missing_binary_are_refusals(self):
        launcher, record = self.fake({'subtype': 'success', 'is_error': False, 'structured_output': GOOD})
        repo = self.make_repo()
        done = subprocess.run([sys.executable, str(RUN_PY), 'release-audit', '--model', 'sonnet', '--max-cost-usd', '1', '--config-dir', str(self.base / 'profile'),
                               '--tag-from', 'v1', '--tag-to', 'v9', '--profiles-file', str(self.profiles), '--repo', str(repo), '--claude-bin', str(launcher), '--ledger', str(self.base / 'l.json')],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("'v9' does not exist", done.stderr)
        self.assertFalse(record.exists())
        done = subprocess.run([sys.executable, str(RUN_PY), *self.args('--repo', str(repo), '--claude-bin', str(self.base / 'nope'), '--ledger', str(self.base / 'l2.json'),
                                                                       '--out', str(self.base / 'out2'))], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertNotIn('Traceback', done.stderr)


if __name__ == '__main__':
    unittest.main()
