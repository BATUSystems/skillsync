"""K13: `scripts/check_skips.py` judges the skipped tests of a `unittest -v` run against `tests/skip-baseline.json`.

Contract: a skip outside the baseline is a FAIL (exit 1); a `null` baseline key is a warning (exit 0); unusable input
(bad JSON, unknown key, no results, skip count differing from the summary) is exit 2, never a silent pass.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'check_skips.py'
REAL_BASELINE = ROOT / 'tests' / 'skip-baseline.json'

SYMLINKS = 'symlinks unavailable (Windows without Developer Mode)'
CHROME = 'set SKILLSYNC_E2E_CHROME to a Chrome/Chromium binary'
CATEGORIES = {'needs-symlinks': ['symlinks unavailable'], 'e2e-chrome': ['SKILLSYNC_E2E_CHROME']}
A = 'test_skillsync_a.Alpha.test_one'
B = 'test_skillsync_a.Alpha.test_two'
E2E = 'test_skillsync_prototype_e2e.PrototypeE2ETests.test_d23'


def verbose_line(test_id, outcome):
    name = test_id.rsplit('.', 1)[1]
    return f'{name} ({test_id}) ... {outcome}'


def run_log(*lines, skipped=None, ran=7):
    """A `unittest -v` log; `skipped` defaults to the number of `skipped '...'` lines."""
    count = sum('skipped \'' in line or 'skipped "' in line for line in lines) if skipped is None else skipped
    summary = 'OK' if not count else f'OK (skipped={count})'
    return '\n'.join([*lines, '', '-' * 70, f'Ran {ran} tests in 0.100s', '', summary, ''])


class CheckSkipsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='skillsync-checkskips-'))
        self.addCleanup(__import__('shutil').rmtree, self.tmp, ignore_errors=True)

    def baseline(self, entries, key='linux/copy', **extra):
        data = {'schema_version': 1, 'categories': CATEGORIES, 'baselines': {key: entries, **extra}}
        path = self.tmp / 'baseline.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def check(self, log, baseline, *args, stdin=False):
        command = [sys.executable, str(SCRIPT), '--baseline', str(baseline), '--system', 'linux', '--mode', 'copy', *args]
        if stdin:
            return subprocess.run(command + ['-'], input=log, capture_output=True, text=True, timeout=30)
        path = self.tmp / 'run.log'
        path.write_text(log, encoding='utf-8')
        return subprocess.run(command + [str(path)], capture_output=True, text=True, timeout=30)


class VerdictTests(CheckSkipsBase):
    def test_a_skip_listed_in_the_baseline_passes(self):
        log = run_log(verbose_line(A, 'ok'), verbose_line(B, f"skipped '{SYMLINKS}'"))
        result = self.check(log, self.baseline([{'id': B, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('every skip is in the baseline', result.stdout)

    def test_a_skip_outside_the_baseline_fails_and_is_named(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"), verbose_line(B, f"skipped '{SYMLINKS}'"))
        result = self.check(log, self.baseline([{'id': B, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f'+ {A}', result.stdout)
        self.assertNotIn(f'+ {B}', result.stdout)

    def test_an_empty_baseline_means_no_skip_is_allowed(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"))
        self.assertEqual(self.check(log, self.baseline([])).returncode, 1)

    def test_a_run_without_skips_passes_with_any_baseline(self):
        log = run_log(verbose_line(A, 'ok'))
        self.assertEqual(self.check(log, self.baseline([])).returncode, 0)

    def test_an_allowed_test_skipped_for_another_reason_fails(self):
        log = run_log(verbose_line(A, f"skipped '{CHROME}'"))
        result = self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('another reason', result.stdout)
        self.assertIn(CHROME, result.stdout)

    def test_a_baseline_entry_that_did_not_skip_is_only_information(self):
        log = run_log(verbose_line(A, 'ok'))
        result = self.check(log, self.baseline([{'id': B, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not skipped in this run', result.stdout)

    def test_the_log_may_come_from_stdin(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"))
        self.assertEqual(self.check(log, self.baseline([]), stdin=True).returncode, 1)
        self.assertEqual(self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}]), stdin=True).returncode, 0)


class NullBaselineTests(CheckSkipsBase):
    def test_a_null_key_warns_and_exits_zero_even_with_skips(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"))
        result = self.check(log, self.baseline(None))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[warn]', result.stdout)
        self.assertIn('NOT enforced', result.stdout)
        self.assertIn(A, result.stdout)

    def test_only_the_selected_key_decides(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"))
        baseline = self.baseline([], key='linux/copy', **{'ubuntu-latest/copy': None})
        self.assertEqual(self.check(log, baseline).returncode, 1)                           # measured key: enforced
        result = self.check(log, baseline, '--system', 'ubuntu-latest')                      # null key: warning only
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_key_that_is_absent_is_not_the_same_as_null(self):
        log = run_log(verbose_line(A, 'ok'))
        result = self.check(log, self.baseline([]), '--system', 'windows-latest')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('no baseline key "windows-latest/copy"', result.stderr)


class UnusableInputTests(CheckSkipsBase):
    def test_a_baseline_that_is_not_json_is_an_error(self):
        path = self.tmp / 'bad.json'
        path.write_text('{ "baselines": ', encoding='utf-8')
        result = self.check(run_log(verbose_line(A, 'ok')), path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('not JSON', result.stderr)

    def test_a_baseline_without_the_baselines_object_is_an_error(self):
        path = self.tmp / 'shape.json'
        path.write_text(json.dumps({'linux/copy': []}), encoding='utf-8')
        self.assertEqual(self.check(run_log(verbose_line(A, 'ok')), path).returncode, 2)

    def test_a_missing_baseline_file_is_an_error(self):
        result = self.check(run_log(verbose_line(A, 'ok')), self.tmp / 'missing.json')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_an_entry_without_a_category_is_an_error(self):
        result = self.check(run_log(verbose_line(A, 'ok')), self.baseline([{'id': A}]))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_a_duplicated_id_in_the_baseline_is_an_error(self):
        entry = {'id': A, 'category': 'needs-symlinks'}
        self.assertEqual(self.check(run_log(verbose_line(A, 'ok')), self.baseline([entry, entry])).returncode, 2)

    def test_zero_collected_tests_is_not_a_pass(self):
        result = self.check('Ran 0 tests in 0.000s\n\nNO TESTS RAN\n', self.baseline([]))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('zero collected tests', result.stderr)

    def test_a_log_without_any_unittest_output_is_an_error(self):
        self.assertEqual(self.check('hello\n', self.baseline([])).returncode, 2)

    def test_a_skip_count_that_differs_from_the_summary_is_an_error(self):
        log = run_log(verbose_line(A, f"skipped '{SYMLINKS}'"), skipped=2)
        result = self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('skipped=2', result.stderr)

    def test_a_log_cut_before_the_summary_is_an_error(self):
        log = verbose_line(A, f"skipped '{SYMLINKS}'") + '\n----\nRan 3 tests in 0.1s\n'
        self.assertEqual(self.check(log, self.baseline([])).returncode, 2)


class LogFormatTests(CheckSkipsBase):
    def test_a_docstring_test_prints_its_result_on_the_next_line(self):
        log = run_log(f'test_d23 ({E2E})', f"Scenarios of d23.mjs ... skipped '{CHROME}'")
        result = self.check(log, self.baseline([{'id': E2E, 'category': 'e2e-chrome'}]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outside = self.check(log, self.baseline([]))
        self.assertEqual(outside.returncode, 1)
        self.assertIn(f'+ {E2E}', outside.stdout)

    def test_stderr_noise_between_the_name_and_the_result_is_tolerated(self):
        log = run_log(f'test_one ({A}) ... [warn] something on stderr', f"skipped '{SYMLINKS}'")
        result = self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.check(log, self.baseline([])).returncode, 1)

    def test_the_tests_package_prefix_is_ignored(self):
        log = run_log(verbose_line('tests.' + A, f"skipped '{SYMLINKS}'"))
        self.assertEqual(self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}])).returncode, 0)

    def test_a_test_name_containing_skipped_is_not_a_skip(self):
        log = run_log(verbose_line('test_skillsync_a.Alpha.test_x_is_skipped_quietly', 'ok'))
        self.assertEqual(self.check(log, self.baseline([])).returncode, 0)

    def test_setupclass_skip_is_its_own_entry_not_the_previous_test(self):
        log = run_log(verbose_line(A, 'ok'), f"setUpClass (test_skillsync_a.Beta) ... skipped '{SYMLINKS}'")
        key = 'setUpClass:test_skillsync_a.Beta'
        listed = self.check(log, self.baseline([{'id': key, 'category': 'needs-symlinks'}]))
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        outside = self.check(log, self.baseline([{'id': A, 'category': 'needs-symlinks'}]))
        self.assertEqual(outside.returncode, 1, outside.stdout + outside.stderr)
        self.assertIn(f'+ {key}', outside.stdout)
        self.assertNotIn(f'+ {A}', outside.stdout)

    def test_setupmodule_skip_is_its_own_entry(self):
        log = run_log(verbose_line(A, 'ok'), f"setUpModule (test_skillsync_b) ... skipped '{SYMLINKS}'")
        result = self.check(log, self.baseline([]))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('+ setUpModule:test_skillsync_b', result.stdout)

    def test_a_reason_with_an_apostrophe_is_printed_by_unittest_in_double_quotes(self):
        reason = "environment doesn't allow it"
        log = run_log(verbose_line(A, f'skipped "{reason}"'), f'test_d23 ({E2E})', f'Scenarios ... skipped "{reason}"')
        baseline = self.baseline([{'id': A, 'category': 'x'}, {'id': E2E, 'category': 'x'}])
        data = json.loads(baseline.read_text())
        data['categories'] = {'x': ["doesn't"]}
        baseline.write_text(json.dumps(data), encoding='utf-8')
        result = self.check(log, baseline)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.check(log, self.baseline([])).returncode, 1)

    def test_emit_prints_the_entries_of_the_run_with_their_category(self):
        log = run_log(verbose_line(B, f"skipped '{SYMLINKS}'"), f'test_d23 ({E2E})', f"Scenarios ... skipped '{CHROME}'")
        result = self.check(log, self.baseline([]), '--emit')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), [{'id': B, 'category': 'needs-symlinks'},
                                                     {'id': E2E, 'category': 'e2e-chrome'}])


class RealBaselineTests(unittest.TestCase):
    """The committed baseline is usable by the script and keeps the agreed shape."""

    def setUp(self):
        self.data = json.loads(REAL_BASELINE.read_text(encoding='utf-8'))

    def test_every_runner_has_both_link_modes(self):
        for system in ('linux', 'ubuntu-latest', 'windows-latest', 'macos-latest'):
            for mode in ('symlink', 'copy'):
                self.assertIn(f'{system}/{mode}', self.data['baselines'])

    def test_unmeasured_runners_are_null_and_the_local_linux_is_a_list(self):
        for key, entries in self.data['baselines'].items():
            if key.startswith('linux/'):
                self.assertIsInstance(entries, list, key)
            else:
                self.assertIsNone(entries, key)

    def test_every_entry_has_a_known_category(self):
        for key, entries in self.data['baselines'].items():
            for entry in entries or []:
                self.assertIn(entry['category'], self.data['categories'], (key, entry))

    def test_every_chrome_e2e_script_is_allowed(self):
        for mode in ('symlink', 'copy'):
            chrome = [e['id'] for e in self.data['baselines'][f'linux/{mode}'] if e['category'] == 'e2e-chrome']
            scripts = sorted(p.stem.replace('-', '_') for p in (Path(__file__).parent / 'e2e' / 'prototype').glob('*.mjs') if p.name != 'lib.mjs')
            self.assertEqual(sorted(i.rsplit('.test_', 1)[1] for i in chrome), scripts, mode)
            self.assertTrue(all(i.startswith('test_skillsync_prototype_e2e.') for i in chrome), mode)

    def test_the_copy_baseline_is_the_symlink_one_plus_only_mode_dependent_skips(self):
        symlink = {e['id'] for e in self.data['baselines']['linux/symlink']}
        copy = {e['id']: e['category'] for e in self.data['baselines']['linux/copy']}
        self.assertLessEqual(symlink, set(copy))
        # copy may add only mode-dependent skips (none at all is the ideal state after 5.1)
        self.assertLessEqual({copy[i] for i in set(copy) - symlink}, {'needs-symlinks'})


if __name__ == '__main__':
    unittest.main()
