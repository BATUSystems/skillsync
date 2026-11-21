"""180-A: the gate of the test steps: `[T]` marker, `checkpoint --status PASS` evidence, the `doctor` finding."""
import json
import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import plan_phases, test_gate  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_skillsync_workflow import WorkflowFixture  # noqa: E402

CLI = ROOT / 'skillsync'
PLAN = """# Plan: demo

Status: in_progress
Przebieg: R1

## Progress

### Faza 1
- [ ] F1.1 Kod
- [ ] F1.2 [T] Testy kodu
- [ ] F1.3 Podsumowanie [T]
"""


TABLE_PLAN = """# Plan: demo

Status: in_progress
Przebieg: R1

## Kroki

| Id | Krok | Rola |
|---|---|---|
| F1.1 | Kod | implementer |
| F1.2 [T] | Testy kodu | implementer |
"""


class Marker(unittest.TestCase):
    def test_marker_at_start_or_end_not_in_code(self):
        text = '- [ ] F1.1 A\n- [ ] F1.2 [T] B\n- [x] F1.3 C [T]\n- [ ] F1.4 `[T]` w kodzie\n```\n- [ ] F9.9 X [T]\n```\n'
        self.assertEqual(plan_phases.parse_test_steps(text), ['F1.2', 'F1.3'])
        self.assertEqual(plan_phases.strip_test_mark('[T] Testy'), 'Testy')
        self.assertEqual(plan_phases.strip_test_mark('Testy [T]'), 'Testy')
        self.assertEqual(plan_phases.strip_test_mark('Testy'), 'Testy')

    def test_the_marker_is_found_when_a_note_follows_the_title(self):
        """B-2: the usual line of a closed step is `- [x] F1.2 Tests [T] — abc1234` (the note is cut before the marker is looked for)."""
        text = '- [x] F1.1 A [T] — abc1234\n- [x] F1.2 [T] B -- a note\n- [ ] F1.3 C — a note [T] in the note\n- [x] F1.4 D [T] – abc1234\n'
        self.assertEqual(plan_phases.parse_test_steps(text), ['F1.1', 'F1.2', 'F1.4'])

    def test_table_rows(self):
        text = '| Id | Krok |\n|---|---|\n| F1.1 | Kod |\n| F1.2 | Testy [T] |\n| F1.3 [T] | Inne |\n'
        self.assertEqual(plan_phases.parse_test_steps(text), ['F1.2', 'F1.3'])
        info = plan_phases.parse_table_phases(text)
        self.assertEqual((info['phases'], info['test_phases'], info['ui_phases']), (['F1.1', 'F1.2', 'F1.3'], ['F1.2', 'F1.3'], []))


class Gate(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.write('.skillsync/runs/R1/checkpoints.md', '- 10:00 start\n')
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def run_tests(self, step='F1.2', code="print('Ran 2 tests in 0.01s'); print('OK')"):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', step, '--', sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=120)

    def checkpoint(self, step, *evidence, ok=True, extra=()):
        args = ['--run-id', 'R1', '--step', step, '--status', 'PASS', '--next', 'next', *extra]
        if evidence:
            args += ['--evidence', *evidence]
        if ok:
            self.report('checkpoint', *args)
            return None
        result = self.rejected('checkpoint', *args)
        return result.stderr + result.stdout

    def events(self, step='F1.2', n=1):
        return f'.skillsync/runs/R1/tests/{step}/{n}.jsonl'

    def test_a_test_step_needs_the_events_file_of_a_passing_run(self):
        self.write('proof.txt', 'anything\n')
        text = self.checkpoint('F1.2', 'proof.txt', ok=False)
        self.assertIn('test step ([T])', text)
        self.assertIn('skillsync test-run', text)
        self.assertEqual(self.run_tests().returncode, 0)
        self.checkpoint('F1.2', self.events())

    def test_a_step_without_the_marker_is_not_affected(self):
        self.write('proof.txt', 'anything\n')
        self.checkpoint('F1.1', 'proof.txt')

    def test_a_failing_run_is_not_a_proof(self):
        self.assertEqual(self.run_tests(code='import sys; sys.exit(1)').returncode, 1)
        self.assertIn('did not pass', self.checkpoint('F1.2', self.events(), ok=False))

    def test_the_events_of_another_step_are_not_a_proof(self):
        self.assertEqual(self.run_tests(step='F1.3').returncode, 0)
        self.assertIn('no events file of this step', self.checkpoint('F1.2', self.events('F1.3'), ok=False))

    def test_a_commit_after_the_tests_keeps_the_proof_but_a_change_breaks_it(self):
        self.write('code.txt', 'v2\n')
        self.assertEqual(self.run_tests().returncode, 0)
        self.git('add', '-A')
        self.git('-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-q', '-m', 'step')   # HEAD moved, the content is the same
        self.checkpoint('F1.2', self.events())
        self.write('code.txt', 'v3\n')   # the tests did not see this
        self.assertIn('different content', self.checkpoint('F1.2', self.events(), ok=False))

    def test_the_work_tree_id_ignores_skillsync_and_leaves_the_index_alone(self):
        before = test_gate.work_tree_id(self.root)
        self.write('.skillsync/runs/R1/tests/x.jsonl', '{}\n')
        self.assertEqual(test_gate.work_tree_id(self.root), before)
        self.write('code.txt', 'changed\n')
        self.assertNotEqual(test_gate.work_tree_id(self.root), before)
        self.assertEqual(self.git('status', '--porcelain'), 'M code.txt')   # the real index is untouched (nothing staged)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')

    def test_doctor_finding_for_a_checked_test_step_without_a_run(self):
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.2', '- [x] F1.2'))
        found = test_gate.plan_findings(self.root)
        self.assertEqual(len(found), 1)
        self.assertIn('tests-not-run', found[0][1])
        self.assertEqual(self.run_tests().returncode, 0)
        self.assertEqual(test_gate.plan_findings(self.root), [])

    def test_no_findings_for_an_unchecked_test_step_or_a_plan_without_a_run(self):
        self.assertEqual(test_gate.plan_findings(self.root), [])
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('Przebieg: R1\n', '').replace('- [ ] F1.2', '- [x] F1.2'))
        self.assertEqual(test_gate.plan_findings(self.root), [])

    def test_a_command_without_tests_is_not_a_proof(self):
        """A2: `test-run S -- true` exits 0 but is no proof (unknown count)."""
        self.assertEqual(self.run_tests(code='pass').returncode, 2)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
        self.assertIn('did not pass', self.checkpoint('F1.2', self.events(), ok=False))
        self.assertEqual(self.run_tests(code="print('Ran 0 tests in 0.0s'); print('OK')").returncode, 2)   # exit 0 without proof (status BLOCKED) is exit code 2 (CORE-TESTDEBT K3)
        self.assertIn('did not pass', self.checkpoint('F1.2', self.events(n=2), ok=False))

    def test_the_events_of_another_run_or_a_hand_made_file_for_another_step_are_not_a_proof(self):
        """A3: directory = step AND `start.step` = step AND `start.run` = run."""
        self.assertEqual(self.run_tests(step='F1.3').returncode, 0)
        src = self.root / self.events('F1.3')
        forged = self.root / '.skillsync/runs/R1/tests/F1.2'
        forged.mkdir(parents=True)
        (forged / '1.jsonl').write_bytes(src.read_bytes())   # the file of F1.3 copied into the directory of F1.2
        self.assertIn('another run or step', self.checkpoint('F1.2', self.events(), ok=False))

    def test_editing_the_plan_after_the_tests_keeps_the_proof(self):
        """A4: the tree without docs/plans: ticking the step in the plan does not invalidate the proof."""
        self.assertEqual(self.run_tests().returncode, 0)
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.1', '- [x] F1.1'))
        self.checkpoint('F1.2', self.events())

    def test_waiver_is_recorded_and_visible(self):
        """A5: `--no-tests <reason>` closes a test step without tests; `doctor` finding and `needsHuman: tests`."""
        self.write('proof.txt', 'something\n')
        self.checkpoint('F1.2', 'proof.txt', extra=['--no-tests', 'documentation only'])
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.2', '- [x] F1.2'))
        found = test_gate.plan_findings(self.root)
        self.assertTrue(any('tests-waived' in reason and 'documentation only' in reason for _, reason in found), found)
        from skillsync_workflow import status
        record = status.plan_record(self.root, self.root / 'docs/plans/2026-11-16-demo.md')
        self.assertIn('tests', record['needsHuman'])

    def test_a_step_that_ran_tests_without_the_marker_is_reported(self):
        """A5(4): `tests-step-not-marked`."""
        self.assertEqual(self.run_tests(step='F1.1').returncode, 0)
        self.assertTrue(any('tests-step-not-marked' in reason and 'F1.1' in reason for _, reason in test_gate.plan_findings(self.root)))

    def test_the_other_ways_of_closing_are_unchanged_without_the_marker(self):
        self.write('proof.txt', 'x\n')
        self.checkpoint('F1.1', 'proof.txt')

    def import_cli(self, source, *extra, step='F1.2'):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', step, '--import', str(source), *extra],
                              capture_output=True, text=True, timeout=120)

    def test_an_imported_run_is_valid_for_the_commit_it_names(self):
        """A5(2): a run of another machine (a copy without `.git`) comes with `--source-sha`; the gate wants exactly that commit."""
        self.assertEqual(self.run_tests().returncode, 0)
        events = self.root / self.events()
        copy = Path(self.temp.name) / 'remote.jsonl'
        copy.write_bytes(events.read_bytes().replace(socket.gethostname().encode(), b'remote-machine'))   # a run of ANOTHER machine
        events.unlink()
        head = self.git('rev-parse', 'HEAD')
        wrong = self.import_cli(copy, '--source-sha', '1234567')
        self.assertEqual(wrong.returncode, 0, wrong.stderr)
        self.assertIn('imported run was made for commit', self.checkpoint('F1.2', self.events(), ok=False))
        events.unlink()
        done = self.import_cli(copy, '--source-sha', head)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.checkpoint('F1.2', self.events())

    def test_an_import_is_validated(self):
        self.assertEqual(self.run_tests().returncode, 0)
        good = self.root / self.events()
        head = self.git('rev-parse', 'HEAD')
        base = Path(self.temp.name)
        (base / 'cut.jsonl').write_bytes(good.read_bytes()[:-1])   # ends in an incomplete line
        (base / 'junk.jsonl').write_bytes(good.read_bytes() + b'not an event\n')
        (base / 'other.jsonl').write_bytes(good.read_bytes().replace(b'"step":"F1.2"', b'"step":"F9.9"'))
        for name in ('cut.jsonl', 'junk.jsonl', 'other.jsonl'):
            data = (base / name).read_bytes().replace(socket.gethostname().encode(), b'remote-machine')
            (base / name).write_bytes(data)
        for name, reason in (('cut.jsonl', 'incomplete line'), ('junk.jsonl', 'not events of version'), ('other.jsonl', 'belongs to run')):
            with self.subTest(name):
                done = self.import_cli(base / name, '--source-sha', head)
                self.assertEqual(done.returncode, 2)
                self.assertIn(reason, done.stderr)
        local = self.import_cli(good, '--source-sha', head)   # written on THIS machine: not an import
        self.assertEqual(local.returncode, 2)
        self.assertIn('ANOTHER machine', local.stderr)
        self.assertEqual(self.import_cli(good, '--source-sha', 'not-a-sha').returncode, 2)
        self.assertEqual(self.import_cli(good, '--source-sha', head, step='F1.3').returncode, 2)   # another step than the file says

    def test_a_closed_step_with_a_commit_note_is_still_a_test_step_for_the_findings(self):
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.2 [T] Testy kodu', '- [x] F1.2 [T] Testy kodu — abc1234'))
        self.assertTrue(any('tests-not-run' in reason and 'F1.2' in reason for _, reason in test_gate.plan_findings(self.root)))

    def test_the_waiver_asks_a_human_only_while_the_plan_is_open_and_doctor_lists_every_waived_step(self):
        from skillsync_workflow import status
        self.write('proof.txt', 'x\n')
        self.checkpoint('F1.1', 'proof.txt', extra=['--no-tests', 'a note'])   # F1.1 has no [T]
        self.checkpoint('F1.2', 'proof.txt', extra=['--no-tests', 'documentation only'])
        found = [reason for _, reason in test_gate.plan_findings(self.root)]
        self.assertTrue(any('F1.1' in r and 'tests-waived' in r for r in found), found)
        plan = self.root / 'docs/plans/2026-11-16-demo.md'
        self.assertIn('tests', status.plan_record(self.root, plan)['needsHuman'])
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('Status: in_progress', 'Status: done'))
        self.assertNotIn('tests', status.plan_record(self.root, plan)['needsHuman'])

    def test_checkpoint_final_names_a_test_step_closed_without_a_run(self):
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.2', '- [x] F1.2'))
        self.write('proof.txt', 'x\n')
        result = self.report('checkpoint', '--run-id', 'R1', '--step', 'F1.9', '--status', 'PASS', '--next', 'n', '--evidence', 'proof.txt', '--final')
        self.assertTrue(any('tests-not-run' in w for w in result.get('warnings', [])), result)

    def test_doctor_findings_for_unignored_run_files_and_for_angular_without_a_reporter(self):
        self.assertEqual(test_gate.doctor_findings(self.root), [])
        self.assertEqual(self.run_tests().returncode, 0)
        self.assertEqual(test_gate.doctor_findings(self.root), [])   # .skillsync/ is ignored by .gitignore
        self.write('.gitignore', '')
        found = test_gate.doctor_findings(self.root)
        self.assertTrue(any('runs-not-ignored' in reason for _, reason in found), found)
        self.write('.gitignore', '.skillsync/\n')
        self.write('angular.json', '{"projects": {"app": {"architect": {"test": {"builder": "@angular/build:karma"}}}}}')
        self.write('package.json', '{"devDependencies": {}}')
        self.assertTrue(any('tests-reporter-missing' in reason for _, reason in test_gate.doctor_findings(self.root)))
        self.write('package.json', '{"devDependencies": {"karma-junit-reporter": "2.0.0"}}')
        self.assertEqual(test_gate.doctor_findings(self.root), [])
        self.write('angular.json', '{"test": {"options": {"reporters": ["default", "junit"]}}}')
        self.write('package.json', '{}')
        self.assertEqual(test_gate.doctor_findings(self.root), [])

    def test_tracked_run_files_are_reported_even_when_ignored(self):
        self.assertEqual(self.run_tests().returncode, 0)
        self.git('add', '-f', '.skillsync/runs')
        self.assertTrue(any('git tracks' in reason for _, reason in test_gate.doctor_findings(self.root)))

    def test_the_tree_id_works_without_a_real_index_and_in_a_repo_without_commits(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['git', '-C', tmp, 'init', '-q'], check=True)
            (Path(tmp) / 'a.txt').write_text('x', encoding='utf-8')
            self.assertTrue(test_gate.work_tree_id(tmp))
            self.assertIsNone(test_gate.work_tree_id(Path(tmp) / 'nope'))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(test_gate.work_tree_id(tmp))   # no git repository


class TreeObjects(WorkflowFixture):
    """CORE-TESTDEBT2 (e): `work_tree_id` leaves no object in the object database of the repository and gives the id the old way gave."""

    def setUp(self):
        super().setUp()
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def count(self, root=None):
        out = subprocess.run(['git', '-C', str(root or self.root), 'count-objects', '-v'], check=True, capture_output=True, text=True).stdout
        return int(out.split('count:')[1].split()[0])

    def legacy_id(self, root):
        """The id the previous implementation gave: a temporary index in the repository itself (it writes objects there), run on a COPY of the repository."""
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory(prefix='legacy-') as tmp:
            copy = Path(tmp) / 'copy'
            shutil.copytree(root, copy)
            env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
            run = lambda *a: subprocess.run(['git', '-C', str(copy), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()  # noqa: E731
            run('read-tree', 'HEAD')
            run('add', '-A', '--', '.')
            for excluded in test_gate.TREE_EXCLUDES:
                run('rm', '-r', '-q', '--cached', '--ignore-unmatch', '--', excluded)
            return run('write-tree')

    def test_no_object_is_written_to_the_repository_and_the_id_is_the_old_one(self):
        self.write('code.txt', 'fresh content that is in no object yet 1\n')
        self.write('new/file.txt', 'another fresh file 1\n')
        before = self.count()
        tree = test_gate.work_tree_id(self.root)
        head = test_gate.work_tree_id(self.root, from_head=True)
        self.assertEqual(self.count(), before, 'the working tree id wrote loose objects into the repository')
        self.assertEqual(tree, self.legacy_id(self.root))
        self.assertNotEqual(tree, head)
        self.assertEqual(test_gate.work_tree_id(self.root), tree)   # again: the same id

    def test_the_new_objects_go_to_the_directory_the_caller_gave(self):
        import tempfile
        self.write('code.txt', 'fresh content that is in no object yet 2\n')
        before = self.count()
        with tempfile.TemporaryDirectory(prefix='objs-') as objects:
            tree = test_gate.work_tree_id(self.root, objects=objects)
            self.assertTrue(tree)
            self.assertTrue(any(Path(objects).iterdir()), 'the blobs and trees of the run live in the given directory')
        self.assertEqual(self.count(), before)

    def test_a_linked_worktree_and_a_shared_clone_work_too(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='wt-') as tmp:
            linked = Path(tmp) / 'linked'
            subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-q', '-b', 'side', str(linked)], check=True, capture_output=True)
            (linked / 'code.txt').write_text('worktree content 1\n', encoding='utf-8')
            before = self.count()
            linked_tree = test_gate.work_tree_id(linked)
            self.assertEqual(self.count(), before)
            self.assertEqual(linked_tree, self.legacy_id(linked))   # after the count: the copy of a linked worktree writes into the real repository
            shared = Path(tmp) / 'shared'
            subprocess.run(['git', 'clone', '-q', '--shared', str(self.root), str(shared)], check=True, capture_output=True)   # objects/info/alternates
            (shared / 'code.txt').write_text('shared clone content 1\n', encoding='utf-8')
            self.assertTrue((shared / '.git/objects/info/alternates').is_file())
            before = self.count(shared)
            tree = test_gate.work_tree_id(shared)
            self.assertEqual(tree, self.legacy_id(shared))
            self.assertEqual(self.count(shared), before)

    def test_a_path_with_a_space_and_a_colon_works(self):
        import tempfile
        names = ['with space'] + ([] if os.name == 'nt' else ['with:colon', 'quote"d'])
        for name in names:
            with self.subTest(name), tempfile.TemporaryDirectory(prefix='odd-') as tmp:
                repo = Path(tmp) / name
                repo.mkdir()
                run = lambda *a: subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True)  # noqa: E731
                run('init', '-q')
                (repo / 'a.txt').write_text('odd path content 1\n', encoding='utf-8')
                run('add', '-A')
                run('commit', '-qm', 'one')   # with a commit `read-tree HEAD` needs the objects of the repository: only a right `GIT_ALTERNATE_OBJECT_DIRECTORIES` finds them
                (repo / 'a.txt').write_text('odd path content 2\n', encoding='utf-8')
                (repo / 'b.txt').write_text('odd path new file 2\n', encoding='utf-8')
                before = self.count(repo)
                tree = test_gate.work_tree_id(repo)
                self.assertTrue(tree)
                self.assertNotEqual(tree, test_gate.work_tree_id(repo, from_head=True))
                self.assertEqual(self.count(repo), before)

    def test_an_inherited_alternate_list_is_kept_after_the_repository_objects(self):
        """The id cannot tell whether the list was kept (a blob missing from the alternates is written to the temporary directory instead), so the test reads the environment
        every writing git command gets: the object database of the repository first, then what the caller already listed, and the writes go to a temporary directory."""
        seen = []
        real = subprocess.run
        with mock.patch.dict(os.environ, {'GIT_ALTERNATE_OBJECT_DIRECTORIES': '/inherited/one'}), \
                mock.patch.object(test_gate.subprocess, 'run', side_effect=lambda *a, **k: (seen.append((a[0], k.get('env') or {})), real(*a, **k))[1]):
            self.write('code.txt', 'fresh content that is in no object yet 3\n')
            tree = test_gate.work_tree_id(self.root)
        self.assertEqual(tree, self.legacy_id(self.root))
        writing = [env for argv, env in seen if any(a in argv for a in ('add', 'write-tree', 'read-tree', 'rm'))]
        self.assertTrue(writing)
        real_objects = str((self.root / '.git/objects').resolve())
        for env in writing:
            self.assertEqual(env['GIT_ALTERNATE_OBJECT_DIRECTORIES'].split(os.pathsep), [real_objects, '/inherited/one'])
            self.assertNotEqual(Path(env['GIT_OBJECT_DIRECTORY']).resolve(), (self.root / '.git/objects').resolve(), 'writes never go to the repository')


    def test_changed_paths_name_what_differs_between_two_trees_at_most_five(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='objs-') as objects:
            first = test_gate.work_tree_id(self.root, objects=objects)
            for i in range(8):
                self.write(f'gen{i}.txt', f'generated {i}\n')
            second = test_gate.work_tree_id(self.root, objects=objects)
            names = test_gate.changed_paths(self.root, first, second, objects)
            self.assertEqual(len(names), 5)
            self.assertTrue(all(n.startswith('gen') for n in names), names)
            self.write('code.txt', 'v2\n')
            third = test_gate.work_tree_id(self.root, objects=objects)
            self.assertEqual(test_gate.changed_paths(self.root, second, third, objects), ['code.txt'])
            self.assertEqual(test_gate.changed_paths(self.root, first, first, objects), [])

    def test_changed_paths_takes_only_tree_ids_and_never_hands_an_option_to_git(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='objs-') as objects, mock.patch.object(test_gate.subprocess, 'run') as run:
            for hostile in ('--output=/tmp/x', 'abc', '', None, 'g' * 40, '-p', 7, b'a' * 40, ['a' * 40]):
                self.assertEqual(test_gate.changed_paths(self.root, hostile, 'a' * 40, objects), [])
                self.assertEqual(test_gate.changed_paths(self.root, 'a' * 40, hostile, objects), [])
            run.assert_not_called()


class TablePlans(WorkflowFixture):
    """CORE-TESTDEBT2 (f): a plan with a step table has no `- [x]` rows; a step counts as done when the LAST `checkpoints` record of the step is PASS."""

    def setUp(self):
        super().setUp()
        self.write('docs/plans/2026-11-16-demo.md', TABLE_PLAN)
        self.write('.skillsync/runs/R1/checkpoints.md', '- 10:00 start\n')
        self.write('.gitignore', '.skillsync/\n')
        self.write('code.txt', 'v1\n')
        self.init_git()

    def run_tests(self, step='F1.2', code="print('Ran 2 tests in 0.01s'); print('OK')"):
        return subprocess.run([sys.executable, str(CLI), 'test-run', str(self.root), '--run', 'R1', '--step', step, '--', sys.executable, '-c', code],
                              capture_output=True, text=True, timeout=120)

    def records(self, *items):
        self.write('.skillsync/runs/R1.json', json.dumps({'checkpoints': [{'step': s, 'status': st} for s, st in items]}))

    def reasons(self):
        return [reason for _, reason in test_gate.plan_findings(self.root)]

    def test_a_table_plan_with_a_pass_checkpoint_and_no_run_is_a_finding(self):
        self.records(('F1.2', 'PASS'))
        found = self.reasons()
        self.assertEqual(len(found), 1, found)
        self.assertIn('tests-not-run', found[0])
        self.assertIn('F1.2', found[0])

    def test_a_table_plan_with_a_passing_run_has_no_finding(self):
        self.records(('F1.2', 'PASS'))
        self.assertEqual(self.run_tests().returncode, 0)
        self.assertEqual(self.reasons(), [])

    def test_a_table_plan_without_a_pass_checkpoint_is_silent(self):
        self.assertEqual(self.reasons(), [])
        self.records(('F1.2', 'FAIL'), ('F1.1', 'PASS'))   # a PASS of a step without [T] is not a test step finding
        self.assertEqual(self.reasons(), [])

    def test_only_the_last_record_of_a_step_decides(self):
        self.records(('F1.2', 'PASS'), ('F1.2', 'BLOCKED'))
        self.assertEqual(self.reasons(), [])
        self.records(('F1.2', 'FAIL'), ('F1.2', 'PASS'))
        self.assertEqual(len(self.reasons()), 1)

    def test_malformed_records_are_ignored(self):
        self.write('.skillsync/runs/R1.json', json.dumps({'checkpoints': ['x', 7, None, {'step': 5, 'status': 'PASS'}, {'status': 'PASS'}, {'step': 'F1.2'}]}))
        self.assertEqual(self.reasons(), [])
        self.write('.skillsync/runs/R1.json', '{not json')
        self.assertEqual(self.reasons(), [])

    def test_a_waived_step_stays_a_waiver_not_a_missing_run(self):
        self.write('.skillsync/runs/R1.json', json.dumps({'checkpoints': [{'step': 'F1.2', 'status': 'PASS', 'no_tests': 'documentation only'}]}))
        found = self.reasons()
        self.assertEqual(len(found), 1, found)
        self.assertIn('tests-waived', found[0])

    def test_a_checklist_plan_behaves_as_before_and_also_sees_a_pass_checkpoint(self):
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.assertEqual(self.reasons(), [])
        self.write('docs/plans/2026-11-16-demo.md', PLAN.replace('- [ ] F1.2', '- [x] F1.2'))
        self.assertEqual(len(self.reasons()), 1)
        self.write('docs/plans/2026-11-16-demo.md', PLAN)
        self.records(('F1.2', 'PASS'))
        self.assertEqual(len(self.reasons()), 1)


class TreeIdSpecStore(WorkflowFixture):
    """K6 (CORE-SPECSTORE): `work_tree_id` does not depend on the ignore rule of `docs/specs` and `docs/plans`: edits of a spec or a plan under
    `workflow.specs.tracked: false` leave the id of the gate alone, and with the specs tracked an edited spec still changes it."""

    def setUp(self):
        super().setUp()
        for name, value in (('HOME', str(self.base)), ('XDG_CONFIG_HOME', str(self.base / 'xdg-config')), ('GIT_CONFIG_GLOBAL', os.devnull),
                            ('GIT_CONFIG_NOSYSTEM', '1')):
            patcher = mock.patch.dict(os.environ, {name: value})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write('README.md', 'fixture\n')
        self.write('docs/specs/2026-11-19-s.md', '# S\n\nStatus: draft\n')
        self.write('docs/plans/2026-11-19-p.md', '# P\n\nStatus: draft\n')

    def commit_all(self):
        self.init_git()

    def exclude(self, *rules):
        info = self.root / '.git' / 'info' / 'exclude'
        info.parent.mkdir(parents=True, exist_ok=True)
        with info.open('a', encoding='utf-8') as handle:
            handle.write(''.join(rule + '\n' for rule in rules))

    def test_tree_excludes_do_not_grow(self):
        self.assertEqual(test_gate.TREE_EXCLUDES, ('.skillsync', 'docs/plans'))

    def test_a_rule_for_the_plans_does_not_change_the_id(self):
        self.write('.gitignore', '.skillsync/\n')
        self.commit_all()
        self.write('docs/plans/2026-11-19-new.md', '# N\n')   # an untracked plan
        before = test_gate.work_tree_id(self.root)
        self.exclude('/docs/plans/')   # a rule outside the tree (it is not a file of the tree), so only the rule changes
        self.assertEqual(test_gate.work_tree_id(self.root), before)

    def test_untracked_specs_and_plans_under_the_rule_do_not_move_the_id(self):
        self.write('.gitignore', '.skillsync/\n/docs/specs/\n/docs/plans/\n')
        self.commit_all()
        before = test_gate.work_tree_id(self.root)
        self.assertEqual(before, test_gate.work_tree_id(self.root, from_head=True))
        self.write('docs/specs/2026-11-19-s.md', '# S\n\nStatus: approved\n')
        self.write('docs/plans/2026-11-19-p.md', '# P\n\nStatus: approved\n')
        self.write('docs/specs/2026-11-20-new.md', '# New\n')
        self.assertEqual(test_gate.work_tree_id(self.root), before)
        self.assertEqual(test_gate.work_tree_id(self.root), test_gate.work_tree_id(self.root, from_head=True))
        self.write('code.txt', 'x\n')   # the code still moves it
        self.assertNotEqual(test_gate.work_tree_id(self.root), before)

    def test_an_untracked_spec_with_a_rule_outside_the_tree_is_ignored_too(self):
        self.write('.gitignore', '.skillsync/\n')
        self.commit_all()
        self.exclude('/docs/specs/', '/docs/plans/')
        before = test_gate.work_tree_id(self.root)
        self.write('docs/specs/2026-11-20-new.md', '# New\n')
        self.assertEqual(test_gate.work_tree_id(self.root), before)

    def test_tracked_specs_still_move_the_id_and_tracked_plans_do_not(self):
        self.write('.gitignore', '.skillsync/\n')
        self.commit_all()
        before = test_gate.work_tree_id(self.root)
        self.write('docs/plans/2026-11-19-p.md', '# P\n\nStatus: approved\n')
        self.assertEqual(test_gate.work_tree_id(self.root), before)   # the plans are excluded (edited at every step)
        self.write('docs/specs/2026-11-19-s.md', '# S\n\nStatus: approved\n')
        self.assertNotEqual(test_gate.work_tree_id(self.root), before)   # tracked: true, unchanged behaviour


if __name__ == '__main__':
    unittest.main()
