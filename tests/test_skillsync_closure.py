"""R2b E3 (K16, K17): zamknięcie planu nie do pominięcia.

K16: `doctor` ostrzega o planie `done` bez rekordu `final` w przebiegu z linii `Przebieg:`.
K17: Stop hook `.claude/hooks/check-closure.py` z biblioteki (blokada raz) oraz jego generowanie przez `link`.
Plik hooka pochodzi z biblioteki wspólnej (`$SKILLSYNC_TEST_LIBRARY/hooks/check-closure.py`; bez zmiennej testy hooka są pomijane)."""
import json
import os
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import transitional_config  # noqa: E402
from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402
from layers_helpers import needs_os_symlinks, needs_sh  # noqa: E402
from cli_module import skillsync as skillsync_cli  # noqa: E402

# The hook belongs to the shared library. The test reads it only from `SKILLSYNC_TEST_LIBRARY`: no fallback to the
# machine library in `~`, so a run does not depend on whatever happens to be installed there (K13: explicit skip).
_TEST_LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')
HOOK_SOURCE = Path(_TEST_LIBRARY) / 'hooks' / 'check-closure.py' if _TEST_LIBRARY else None
needs_hook = unittest.skipUnless(HOOK_SOURCE is not None and HOOK_SOURCE.is_file(),
                                 'set SKILLSYNC_TEST_LIBRARY to a library with hooks/check-closure.py')
PLAN = 'docs/plans/2026-11-05-x.md'


def plan_text(status='done', run='abc', title='X'):
    lines = [f'# {title}', '', f'Status: {status}']
    if run is not None:
        lines.append(f'Przebieg: {run}')
    return '\n'.join(lines) + '\n'


def run_json(run_id='abc', final=True):
    records = [{'step': 'a', 'status': 'PASS', 'next': 'n'}, {'step': 'b', 'status': 'PASS', 'next': 'n'}]
    if final:
        records[-1]['final'] = True
    return json.dumps({'schema_version': 1, 'run_id': run_id, 'checkpoints': records})


# ---------------------------------------------------------------------------------------------------------------
# K16: doctor
# ---------------------------------------------------------------------------------------------------------------
class DoctorClosure(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.manifest['skills'] = ['feature']  # ostrzeżenie o braku linii `Przebieg:` wymaga skillu workflow
        skill = self.library / 'skills/feature'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        installed = self.root / '.github/skills/feature'
        installed.mkdir(parents=True)
        (installed / 'SKILL.md').write_bytes((skill / 'SKILL.md').read_bytes())
        self.manifest['agents'] = []
        self.manifest['claude'] = False
        self.manifest['codex'] = False
        self.save()

    def plan_warnings(self, name):
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)
        return [f for f in report['findings'] if f['level'] == 'warning' and name in f['path']]

    def put_plan(self, name='2026-11-05-x.md', **kw):
        self.write(f'docs/plans/{name}', plan_text(**kw))

    def test_done_plan_without_final_record_warns_and_keeps_status(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_done_plan_with_missing_run_file_warns(self):
        self.put_plan()
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_final_record_in_attempt_file_clears_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.write('.skillsync/runs/abc.2.json', run_json(run_id='abc.2', final=True))
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_final_record_in_the_main_file_clears_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', run_json())
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_unrelated_run_with_final_does_not_clear_the_warning(self):
        self.put_plan()
        self.write('.skillsync/runs/abcd.json', run_json(run_id='abcd'))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_in_progress_plan_is_not_checked(self):
        self.put_plan(status='in_progress')
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_in_progress_plan_with_all_steps_checked_warns_about_closure(self):
        self.write('docs/plans/2026-11-05-x.md', plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [x] A2 b\n\n## Wynik\n')
        warnings = self.plan_warnings('2026-11-05-x.md')
        self.assertEqual(len(warnings), 1)
        self.assertIn('still in_progress', warnings[0]['reason'])

    def test_ticket_plan_without_pr_is_information_not_warning(self):
        self.write('docs/plans/2026-11-05-x.md', plan_text(status='in_progress')
                   + '\n## Progress\nTicket: ABC-1 (link: https://example/ABC-1)\n- [x] A1 a\n')
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_ticket_plan_with_pr_warns_about_closure(self):
        self.write('docs/plans/2026-11-05-x.md', plan_text(status='in_progress')
                   + '\n## Progress\nPR: #5 (link: https://example/pr/5)\nTicket: ABC-1 (link: https://example/ABC-1)\n- [x] A1 a\n')
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_in_progress_plan_with_open_step_is_not_flagged(self):
        self.write('docs/plans/2026-11-05-x.md', plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [ ] A2 b\n')
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])

    def test_done_without_run_line_warns_from_2026_11_01(self):
        self.put_plan(name='2026-11-01-x.md', run=None)
        self.assertEqual(len(self.plan_warnings('2026-11-01-x.md')), 1)

    def test_done_without_run_line_is_not_judged_without_the_workflow_skill(self):
        self.manifest['skills'] = []
        self.save()
        self.put_plan(name='2026-11-01-x.md', run=None)
        self.assertEqual(self.plan_warnings('2026-11-01-x.md'), [])

    def test_missing_final_is_judged_without_the_workflow_skill(self):
        self.manifest['skills'] = []
        self.save()
        self.put_plan()
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    @needs_os_symlinks
    def test_skillsync_symlink_is_treated_as_a_missing_run(self):
        self.put_plan()
        other = self.base / 'elsewhere'
        (other / 'runs').mkdir(parents=True)
        (other / 'runs/abc.json').write_text(run_json(), encoding='utf-8')
        (self.root / '.skillsync').symlink_to(other)
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_done_without_run_line_is_not_judged_before_2026_11_01(self):
        self.put_plan(name='2026-10-31-x.md', run=None)
        self.assertEqual(self.plan_warnings('2026-10-31-x.md'), [])

    def test_done_without_run_line_and_date_in_name_is_not_judged(self):
        self.put_plan(name='x.md', run=None)
        self.assertEqual(self.plan_warnings('x.md'), [])

    def test_backticked_run_id_is_read(self):
        self.put_plan(run='`abc`')
        self.write('.skillsync/runs/abc.json', run_json())
        self.assertEqual(self.plan_warnings('2026-11-05-x.md'), [])
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertEqual(len(self.plan_warnings('2026-11-05-x.md')), 1)

    def test_traversal_run_id_warns_about_invalid_id_without_reading_outside(self):
        self.put_plan(run='../x')
        # Gdyby doctor wyszedł poza magazyn, ten plik z rekordem final zdjąłby ostrzeżenie.
        self.write('.skillsync/x.json', run_json(run_id='x'))
        found = self.plan_warnings('2026-11-05-x.md')
        self.assertEqual(len(found), 1, found)
        self.assertIn('id', found[0]['reason'].lower())

    def test_corrupt_run_file_does_not_crash_doctor(self):
        self.put_plan()
        self.write('.skillsync/runs/abc.json', '{ not json')
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS', report)

    def put_fresh(self, with_checkpoint=True):
        self.write(FRESH_PLAN, fresh_plan_text())
        past = time.time() - 3600
        os.utime(self.root / FRESH_PLAN, (past, past))
        if with_checkpoint:
            directory = self.root / '.skillsync' / 'runs' / RUN_ID
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'checkpoints.md').write_text(f'- 10:00 zrobione: A-1 ({SHA})\n', encoding='utf-8')

    def test_a_stale_plan_is_a_doctor_warning(self):
        self.put_fresh()
        warnings = self.plan_warnings('2026-11-23-core-1150-autokeep.md')
        self.assertEqual(len(warnings), 1)
        self.assertIn('step-reported-unchecked A-1', warnings[0]['reason'])

    def test_a_fresh_plan_has_no_warning(self):
        self.put_fresh(with_checkpoint=False)
        self.assertEqual(self.plan_warnings('2026-11-23-core-1150-autokeep.md'), [])

    def test_the_doctor_status_stays_pass(self):
        self.put_fresh()
        self.assertEqual(self.report('doctor')['status'], 'PASS')


# ---------------------------------------------------------------------------------------------------------------
# K17: hook
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class ClosureHook(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.hook = self.root / '.claude/hooks/check-closure.py'
        self.hook.parent.mkdir(parents=True)
        self.hook.write_bytes(HOOK_SOURCE.read_bytes())
        self.write('README.md', 'fixture\n')
        self.write('.gitignore', '.claude/\n.skillsync/\n')

    def fire(self, payload=None, cwd=None, raw=None, root=None):
        root = root or self.root
        if raw is None:
            raw = json.dumps({'cwd': str(root), 'stop_hook_active': False, **(payload or {})})
        result = subprocess.run([sys.executable, str(self.hook)], input=raw, capture_output=True, text=True,
                                timeout=20, cwd=cwd or root)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def blocked(self, **kw):
        result = self.fire(**kw)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        return result.stderr

    def allowed(self, **kw):
        result = self.fire(**kw)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def commit_plan(self, **kw):
        self.write(PLAN, plan_text(**kw))
        self.init_git()

    def test_untracked_done_plan_without_final_blocks_with_actionable_message(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        message = self.blocked()
        for fragment in ('2026-11-05-x.md', 'abc', '--final', 'retro'):
            self.assertIn(fragment, message)

    def test_plan_changed_against_head_blocks(self):
        self.commit_plan(status='in_progress')
        self.write(PLAN, plan_text(status='done'))
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertIn('abc', self.blocked())

    def test_missing_run_file_blocks(self):
        # assertions changed on purpose (CORE-1170-STOPHOOK): no run file and no Result in the plan still blocks, but asks for the Result
        # (the run may be another machine's: no `--final` is suggested); a local run without `final` suggests it (next test)
        self.init_git()
        self.write(PLAN, plan_text())
        message = self.blocked()
        self.assertIn('sekcje Wynik', message)
        self.assertNotIn('--final', message)

    def test_a_local_run_directory_without_final_blocks_and_suggests_final(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc/checkpoints.md', 'RUN=2026-11-05T09:00:00+01:00\n')
        self.assertIn('--final', self.blocked())

    def test_final_record_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=True))
        self.allowed()

    def test_final_record_in_attempt_file_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.write('.skillsync/runs/abc.2.json', run_json(run_id='abc.2', final=True))
        self.allowed()

    def test_backticked_run_id_is_read(self):
        self.init_git()
        self.write(PLAN, plan_text(run='`abc`'))
        self.assertIn('abc', self.blocked())
        self.write('.skillsync/runs/abc.json', run_json())
        self.allowed()

    def test_stop_hook_active_never_blocks(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.allowed(payload={'stop_hook_active': True})

    def test_in_progress_plan_with_all_steps_checked_blocks(self):
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [x] A2 b\n')
        self.assertIn('in_progress ze wszystkimi krokami', self.blocked())

    def test_in_progress_plan_with_open_step_allows(self):
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [ ] A2 b\n')
        self.allowed()

    def test_ticket_plan_waiting_for_pull_requests_allows(self):
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress') + '\n## Progress\nTicket: ABC-1 (link: https://example/ABC-1)\n'
                   '- [x] A1 a\n')
        self.allowed()

    def test_done_plan_unchanged_against_head_is_ignored(self):
        self.commit_plan(status='done')
        self.allowed()

    def test_in_progress_plan_is_ignored(self):
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress'))
        self.allowed()

    def test_plan_without_run_line_is_ignored(self):
        self.init_git()
        self.write(PLAN, plan_text(run=None))
        self.allowed()

    def test_directory_without_git_is_allowed(self):
        self.write(PLAN, plan_text())
        self.allowed()

    def test_broken_stdin_json_is_allowed(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.allowed(raw='{ not json')

    def test_empty_stdin_is_allowed(self):
        self.allowed(raw='')

    def test_broken_run_json_is_allowed(self):
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', '{ not json')
        self.allowed()

    def test_traversal_run_id_is_not_read_and_does_not_block(self):
        self.init_git()
        self.write(PLAN, plan_text(run='../x'))
        # Plik poza magazynem bez rekordu final: odczyt zablokowałby turę.
        self.write('.skillsync/x.json', run_json(run_id='x', final=False))
        self.allowed()

    def test_payload_cwd_decides_where_the_hook_looks(self):
        self.init_git()
        self.write(PLAN, plan_text())
        with tempfile.TemporaryDirectory(prefix='skillsync-closure-elsewhere-') as elsewhere:
            self.assertIn('2026-11-05-x.md', self.blocked(cwd=elsewhere))

    @needs_os_symlinks
    def test_skillsync_symlink_is_not_read_and_allows(self):
        self.init_git()
        self.write(PLAN, plan_text())
        other = self.base / 'elsewhere'
        (other / 'runs').mkdir(parents=True)
        (other / 'runs/abc.json').write_text(run_json(final=False), encoding='utf-8')
        (self.root / '.skillsync').symlink_to(other)
        self.allowed()

    def test_only_flat_plans_without_readme_are_read(self):
        self.init_git()
        self.write('docs/plans/README.md', plan_text())
        self.write('docs/plans/sub/2026-11-05-y.md', plan_text())
        self.allowed()

    def test_hook_is_read_only(self):
        self.init_git()
        self.write(PLAN, plan_text())
        before = self.git('status', '--porcelain', '--ignored')
        self.blocked()
        self.assertEqual(self.git('status', '--porcelain', '--ignored'), before)

    def test_hook_uses_only_the_standard_library(self):
        source = HOOK_SOURCE.read_text(encoding='utf-8')
        self.assertNotIn('import yaml', source)
        self.assertNotIn('skillsync_workflow', source)

    # K5 (CORE-SPECSTORE): the plans of a project with `/docs/specs/`, `/docs/plans/` in `.gitignore` (`workflow.specs.tracked: false`).
    IGNORE_RULE = '.claude/\n.skillsync/\n/docs/specs/\n/docs/plans/\n'

    def ignore_plans(self):
        self.write('.gitignore', self.IGNORE_RULE)

    def test_a_force_tracked_plan_under_the_ignore_rule_is_compared_with_head(self):
        self.ignore_plans()
        self.write(PLAN, plan_text(status='in_progress'))
        self.init_git()
        self.git('add', '-f', PLAN)
        self.git('commit', '-qm', 'plan')
        self.allowed()
        self.write(PLAN, plan_text(status='done'))
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        self.assertIn('abc', self.blocked())

    def test_an_ignored_plan_with_a_final_record_allows(self):
        self.ignore_plans()
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=True))
        self.allowed()

    def test_ignored_readme_and_nested_plans_are_not_read(self):
        self.ignore_plans()
        self.init_git()
        self.write('docs/plans/README.md', plan_text())
        self.write('docs/plans/sub/2026-11-05-y.md', plan_text())
        self.allowed()

    def test_an_ignored_done_plan_without_final_blocks(self):
        """F5.2: an untracked and ignored plan is as visible as an untracked one (`git status --ignored`, entries `!!`).
        Before F5.2 the hook was silent here (the F5.1 baseline of this test asserted `allowed`)."""
        self.ignore_plans()
        self.init_git()
        self.write(PLAN, plan_text())
        self.write('.skillsync/runs/abc.json', run_json(final=False))
        message = self.blocked()
        for fragment in ('2026-11-05-x.md', 'abc', '--final'):
            self.assertIn(fragment, message)

    def test_an_ignored_in_progress_plan_with_all_steps_checked_blocks(self):
        self.ignore_plans()
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [x] A2 b\n')
        self.assertIn('in_progress ze wszystkimi krokami', self.blocked())

    def test_an_ignored_in_progress_plan_with_an_open_step_allows(self):
        self.ignore_plans()
        self.init_git()
        self.write(PLAN, plan_text(status='in_progress') + '\n## Progress\n- [x] A1 a\n- [ ] A2 b\n')
        self.allowed()


# ---------------------------------------------------------------------------------------------------------------
# K17: generowanie przez link
# ---------------------------------------------------------------------------------------------------------------
@needs_hook
class ClosureLink(WorkflowFixture):
    HOOK_PATH = '.claude/hooks/check-closure.py'
    LOCAL = '.claude/settings.local.json'

    def setUp(self):
        super().setUp()
        (self.library / 'hooks').mkdir()
        (self.library / 'hooks/check-closure.py').write_bytes(HOOK_SOURCE.read_bytes())
        skill = self.library / 'skills/feature'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        (self.library / 'instructions').mkdir()
        (self.library / 'instructions/_base.md').write_text('# Base\n', encoding='utf-8')
        self.home = self.base / 'home'
        self.home.mkdir()
        self.config = self.base / 'config.yaml'
        self.config.write_text(yaml.safe_dump(transitional_config(self.library, legacy_key=False, tags={})), encoding='utf-8')
        self.manifest['skills'] = ['feature']
        self.save()

    def link(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), 'link', str(self.root), *args], capture_output=True,
                                text=True, timeout=30, cwd=self.root,
                                env={**os.environ, 'SKILLSYNC_CONFIG': str(self.config), 'HOME': str(self.home)})
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def local(self):
        return json.loads((self.root / self.LOCAL).read_text(encoding='utf-8'))

    def closure_commands(self, data):
        found = []
        for group in (data.get('hooks') or {}).get('Stop', []):
            for hook in group.get('hooks', []):
                if 'check-closure.py' in json.dumps(hook):
                    found.append(hook)
        return found

    def tree(self):
        result = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            for name in dirnames:
                result[os.path.relpath(os.path.join(dirpath, name), self.root)] = None
            for name in filenames:
                path = Path(dirpath, name)
                result[os.path.relpath(path, self.root)] = path.read_bytes() if not path.is_symlink() else 'link'
        return result

    def test_feature_project_gets_hook_and_local_stop_entry(self):
        self.link()
        self.assertTrue((self.root / self.HOOK_PATH).is_file())
        self.assertEqual((self.root / self.HOOK_PATH).read_bytes(), HOOK_SOURCE.read_bytes())
        self.assertEqual(len(self.closure_commands(self.local())), 1)

    def test_shared_settings_json_never_gets_the_entry(self):
        self.link()
        shared = self.root / '.claude/settings.json'
        if shared.exists():
            self.assertEqual(self.closure_commands(json.loads(shared.read_text(encoding='utf-8'))), [])

    def test_second_link_does_not_duplicate_the_entry(self):
        self.link()
        self.link()
        self.assertEqual(len(self.closure_commands(self.local())), 1)

    def test_existing_permissions_and_foreign_hooks_survive(self):
        existing = {'permissions': {'allow': ['Bash(git fetch *)']},
                    'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'pre'}]}],
                              'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]}}
        self.write(self.LOCAL, json.dumps(existing))
        self.link()
        data = self.local()
        self.assertEqual(data['permissions'], existing['permissions'])
        self.assertEqual(data['hooks']['PreToolUse'], existing['hooks']['PreToolUse'])
        self.assertIn('echo foreign', json.dumps(data['hooks']['Stop']))
        self.assertEqual(len(self.closure_commands(data)), 1)

    def test_stop_closure_false_removes_only_our_entry(self):
        foreign = {'hooks': {'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo foreign'}]}]},
                   'permissions': {'allow': ['Read(x)']}}
        self.write(self.LOCAL, json.dumps(foreign))
        self.link()
        self.assertEqual(len(self.closure_commands(self.local())), 1)
        self.manifest['hooks'] = {'stop_closure': False}
        self.save()
        self.link()
        data = self.local()
        self.assertEqual(self.closure_commands(data), [])
        self.assertIn('echo foreign', json.dumps(data['hooks']['Stop']))
        self.assertEqual(data['permissions'], foreign['permissions'])

    def test_project_without_feature_gets_no_hook(self):
        self.manifest['skills'] = []
        self.save()
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        local = self.root / self.LOCAL
        if local.exists():
            self.assertEqual(self.closure_commands(self.local()), [])

    def test_claude_gitignore_covers_local_settings_backup_and_hook(self):
        self.link()
        lines = (self.root / '.claude/.gitignore').read_text(encoding='utf-8').splitlines()
        for entry in ('settings.local.json', 'settings.local.json.skillsync-backup', 'hooks/check-closure.py'):
            self.assertIn(entry, lines)

    def test_invalid_local_settings_json_is_left_byte_identical(self):
        raw = b'{ "permissions": [broken'
        path = self.root / self.LOCAL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        self.link()
        self.assertEqual(path.read_bytes(), raw)

    def test_dry_run_writes_nothing(self):
        before = self.tree()
        self.link('--dry-run')
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / self.HOOK_PATH).exists())

    def entry_command(self):
        return self.closure_commands(self.local())[0]['command']

    def run_entry(self, cwd, env_dir):
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_PROJECT_DIR'}
        if env_dir is not None:
            env['CLAUDE_PROJECT_DIR'] = str(env_dir)
        return subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps({'cwd': str(cwd)}),
                              capture_output=True, text=True, timeout=20, cwd=cwd, env=env)

    @needs_sh
    def test_entry_command_works_from_a_subdirectory_and_blocks(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        (self.root / 'sub').mkdir()
        result = self.run_entry(self.root / 'sub', self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def run_session(self, session_id, tmpdir):
        env = dict(os.environ, TMPDIR=str(tmpdir), CLAUDE_PROJECT_DIR=str(self.root))
        payload = {'cwd': str(self.root), 'session_id': session_id, 'stop_hook_active': False}
        return subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=20, cwd=self.root, env=env)

    def session_fixture(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        return tmpdir

    @needs_sh
    def test_one_session_is_blocked_once_per_plan(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        # stop_hook_active po użyciu narzędzia jest znów false; blokada się nie powtarza.
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 0)

    @needs_sh
    def test_without_session_id_the_block_is_not_repeated(self):
        tmpdir = self.session_fixture()
        env = dict(os.environ, TMPDIR=str(tmpdir), CLAUDE_PROJECT_DIR=str(self.root))
        codes = [subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps({'cwd': str(self.root)}),
                                capture_output=True, text=True, timeout=20, cwd=self.root, env=env).returncode
                 for _ in range(2)]
        self.assertEqual(codes, [2, 0])

    @needs_sh
    def test_another_session_is_blocked_again(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        self.assertEqual(self.run_session('s2', tmpdir).returncode, 2)

    @needs_sh
    def test_another_plan_in_the_same_session_is_blocked(self):
        tmpdir = self.session_fixture()
        self.assertEqual(self.run_session('s1', tmpdir).returncode, 2)
        self.write('docs/plans/2026-11-06-y.md', plan_text())
        result = self.run_session('s1', tmpdir)
        self.assertEqual(result.returncode, 2)
        self.assertIn('2026-11-06-y.md', result.stderr)
        self.assertNotIn(PLAN, result.stderr)

    @needs_sh
    def test_marker_write_failure_lets_the_turn_end(self):
        tmpdir = self.session_fixture()
        uid = getattr(os, 'getuid', lambda: 'user')()
        with open(os.path.join(tmpdir, f'skillsync-closure-{uid}'), 'w', encoding='utf-8') as handle:
            handle.write('plik zamiast katalogu')
        result = self.run_session('s1', tmpdir)
        self.assertEqual((result.returncode, result.stderr), (0, ''))

    @needs_sh
    def test_entry_command_blocks_when_the_session_started_in_a_subdirectory(self):
        # Prawdziwy Claude Code ustawia CLAUDE_PROJECT_DIR na katalog startu sesji, a settings czyta z katalogu wyżej.
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        (self.root / 'sub').mkdir()
        self.assertEqual(self.run_entry(self.root / 'sub', self.root / 'sub').returncode, 2)

    @needs_sh
    def test_entry_command_without_variable_uses_the_current_directory(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        self.assertEqual(self.run_entry(self.root, None).returncode, 2)

    @needs_sh
    def test_entry_command_without_script_or_outside_git_exits_zero(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.assertEqual(self.run_entry(outside, outside).returncode, 0)
        (self.root / self.HOOK_PATH).unlink()
        self.assertEqual(self.run_entry(self.root, self.root).returncode, 0)

    def test_old_relative_entry_is_replaced_by_one_current_entry(self):
        path = self.root / self.LOCAL
        path.parent.mkdir(parents=True, exist_ok=True)
        old = {'matcher': '', 'hooks': [{'type': 'command', 'command': 'python3 .claude/hooks/check-closure.py'}]}
        path.write_text(json.dumps({'hooks': {'Stop': [old]}}), encoding='utf-8')
        self.link()
        commands = self.closure_commands(self.local())
        self.assertEqual(len(commands), 1)
        self.assertIn('rev-parse --show-toplevel', commands[0]['command'])

    def test_python_command_with_a_space_is_quoted(self):
        entry = skillsync_cli.build_closure_hook_entry({"python_command": "C:/Program Files/py.exe"})
        self.assertIn("'C:/Program Files/py.exe' \"$f\"", entry["hooks"][0]["command"])

    def test_missing_library_script_removes_the_entry_with_a_warning(self):
        self.link()
        (self.library / 'hooks/check-closure.py').unlink()
        result = self.link()
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(self.closure_commands(self.local()), [])

    def test_disable_removes_entry_then_script(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False, 'plan_nudge': False}
        self.save()
        self.link()
        self.assertEqual(self.closure_commands(self.local()), [])
        self.assertFalse((self.root / self.HOOK_PATH).exists())

    def test_disable_keeps_the_script_when_the_entry_cannot_be_removed(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False, 'plan_nudge': False}
        self.save()
        path = self.root / self.LOCAL
        os.chmod(path, 0o444)
        try:
            if os.access(path, os.W_OK):
                self.skipTest('uprawnienia nie są egzekwowane (root)')
            result = self.link()
        finally:
            os.chmod(path, 0o644)
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(len(self.closure_commands(self.local())), 1)   # wpis Stop zostaje, bo plik nie dal sie zapisac
        self.assertIn('PostToolUse', self.local()['hooks'])
        self.assertTrue((self.root / self.HOOK_PATH).exists())

    def test_read_only_local_settings_warn_and_link_continues(self):
        path = self.write(self.LOCAL, '{"permissions": {}}\n')
        os.chmod(path, 0o444)
        try:
            if os.access(path, os.W_OK):
                self.skipTest('uprawnienia nie są egzekwowane (root)')
            result = self.link()
        finally:
            os.chmod(path, 0o644)
        self.assertIn('[warn]', result.stdout)
        self.assertEqual(self.closure_commands(self.local()), [])

    def test_tracked_local_settings_are_not_written(self):
        self.write(self.LOCAL, '{"permissions": {}}\n')
        self.init_git()
        self.git('add', '-f', self.LOCAL)
        # `init_git` already tracks the file when no global ignore hides it; --allow-empty keeps the commit independent of that
        self.git('commit', '-qm', 'track local settings', '--allow-empty')
        before = (self.root / self.LOCAL).read_bytes()
        result = self.link()
        self.assertIn('[warn]', result.stdout)
        self.assertEqual((self.root / self.LOCAL).read_bytes(), before)

    def test_stop_group_without_hooks_key_is_left_alone(self):
        existing = {'hooks': {'Stop': [{'matcher': 'x'}]}}
        self.write(self.LOCAL, json.dumps(existing))
        self.link()
        self.assertIn({'matcher': 'x'}, self.local()['hooks']['Stop'])

    def test_generated_hook_works_in_the_linked_project(self):
        self.link()
        self.init_git()
        self.write(PLAN, plan_text())
        result = subprocess.run([sys.executable, str(self.root / self.HOOK_PATH)],
                                input=json.dumps({'cwd': str(self.root), 'stop_hook_active': False}),
                                capture_output=True, text=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def stop_hooks(self):
        return self.closure_commands(self.local())

    def parts(self):
        import shlex
        command = self.stop_hooks()[0]['command']
        return shlex.split(command.rpartition('"$f" ')[2].rstrip().removesuffix('; fi'))

    def test_the_entry_carries_the_cli_of_this_generator(self):
        self.link()
        parts = self.parts()
        self.assertEqual(parts[0], '--cli')
        argv = json.loads(parts[1])
        self.assertEqual(Path(argv[0]), Path(sys.executable))   # on Windows the path is written with `/`
        self.assertTrue(Path(argv[1]).is_file())

    def test_the_entry_without_cli_is_replaced(self):
        self.link()
        data = self.local()
        hook = self.stop_hooks()[0]
        hook['command'] = hook['command'].split(' --cli ')[0] + '; fi'
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        self.link()
        self.assertEqual(len(self.stop_hooks()), 1)
        self.assertEqual(self.parts()[0], '--cli')

    def test_another_cli_that_exists_is_not_rewritten(self):
        self.link()
        data = self.local()
        hook = self.stop_hooks()[0]
        import shlex
        head = hook['command'].split(' --cli ')[0]
        hook['command'] = head + ' ' + shlex.join(['--cli', json.dumps([sys.executable, str(CLI.parent / 'README.md')])]) + '; fi'
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        before = (self.root / self.LOCAL).read_bytes()
        self.link()
        self.assertEqual((self.root / self.LOCAL).read_bytes(), before)

    def test_a_cli_saved_with_backslashes_of_a_windows_path_is_replaced(self):
        self.link()
        data = self.local()
        hook = self.stop_hooks()[0]
        import shlex
        head = hook['command'].split(' --cli ')[0]
        legacy = json.dumps([sys.executable, 'C:\\Tools\\sk\\skillsync'])      # the old form: backslashes in the JSON
        hook['command'] = head + ' ' + shlex.join(['--cli', legacy]) + '; fi'
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        self.link()
        self.assertEqual(len(self.stop_hooks()), 1)
        argv = json.loads(self.parts()[1])
        self.assertNotIn('C:\\Tools', ' '.join(argv))

    def test_a_cli_with_a_vanished_path_is_replaced(self):
        self.link()
        data = self.local()
        hook = self.stop_hooks()[0]
        import shlex
        head = hook['command'].split(' --cli ')[0]
        hook['command'] = head + ' ' + shlex.join(['--cli', json.dumps([sys.executable, str(self.base / 'gone' / 'skillsync')])]) + '; fi'
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        self.link()
        self.assertTrue(Path(json.loads(self.parts()[1])[1]).is_file())

    def test_second_link_keeps_one_current_entry(self):
        self.link()
        before = (self.root / self.LOCAL).read_bytes()
        self.link()
        self.assertEqual((self.root / self.LOCAL).read_bytes(), before)

    @needs_sh
    def test_the_generated_entry_blocks_a_stale_plan_through_the_shell(self):
        self.link()
        self.init_git()
        self.write(FRESH_PLAN, fresh_plan_text())
        past = time.time() - 3600
        os.utime(self.root / FRESH_PLAN, (past, past))
        directory = self.root / '.skillsync' / 'runs' / RUN_ID
        directory.mkdir(parents=True)
        (directory / 'checkpoints.md').write_text(f'- 10:00 zrobione: A-1 ({SHA})\n', encoding='utf-8')
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, TMPDIR=tmp, CLAUDE_PROJECT_DIR=str(self.root))
        result = subprocess.run(['sh', '-c', self.entry_command()], input=json.dumps({'cwd': str(self.root), 'session_id': 'x'}),
                                capture_output=True, text=True, timeout=60, cwd=self.root, env=env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('step-reported-unchecked', result.stderr)

    def test_disabling_the_hook_removes_the_entry_with_the_cli(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False, 'plan_nudge': False}
        self.save()
        self.link()
        self.assertEqual(self.closure_commands(self.local()) if (self.root / self.LOCAL).exists() else [], [])
        self.assertFalse((self.root / self.HOOK_PATH).exists())


    def post_hooks(self, data=None):
        found = []
        for group in ((data or self.local()).get('hooks') or {}).get('PostToolUse', []):
            for hook in group.get('hooks', []):
                if 'check-closure.py' in json.dumps(hook):
                    found.append((group.get('matcher'), hook))
        return found

    def test_link_writes_the_post_tool_use_entry(self):
        self.link()
        found = self.post_hooks()
        self.assertEqual(len(found), 1)
        matcher, hook = found[0]
        self.assertEqual(matcher, 'Agent|Task')
        self.assertEqual(hook['timeout'], 5)
        import shlex
        parts = shlex.split(hook['command'].rpartition('"$f" ')[2].rstrip().removesuffix('; fi'))
        self.assertEqual(parts[0], '--cli')
        self.assertTrue(Path(json.loads(parts[1])[1]).is_file())
        self.assertEqual(len(self.stop_hooks()), 1)

    def test_second_link_is_idempotent_for_the_post_tool_use_entry(self):
        self.link()
        before = (self.root / self.LOCAL).read_bytes()
        self.link()
        self.assertEqual((self.root / self.LOCAL).read_bytes(), before)
        self.assertEqual(len(self.post_hooks()), 1)

    def test_foreign_post_tool_use_entries_survive_and_plan_nudge_false_removes_only_ours(self):
        foreign = {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'echo obcy'}]}
        (self.root / '.claude').mkdir(exist_ok=True)
        (self.root / self.LOCAL).write_text(json.dumps({'hooks': {'PostToolUse': [foreign]}}), encoding='utf-8')
        self.link()
        self.assertIn(foreign, self.local()['hooks']['PostToolUse'])
        self.assertEqual(len(self.post_hooks()), 1)
        self.manifest['hooks'] = {'plan_nudge': False}
        self.save()
        self.link()
        self.assertEqual(self.local()['hooks']['PostToolUse'], [foreign])
        self.assertEqual(len(self.stop_hooks()), 1)
        self.assertTrue((self.root / self.HOOK_PATH).is_file())

    def test_a_changed_matcher_is_repaired(self):
        self.link()
        data = self.local()
        data['hooks']['PostToolUse'][0]['matcher'] = 'Bash'
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        self.link()
        self.assertEqual([matcher for matcher, _ in self.post_hooks()], ['Agent|Task'])

    def test_stop_closure_false_keeps_the_script_for_the_nudge(self):
        self.link()
        self.manifest['hooks'] = {'stop_closure': False}
        self.save()
        self.link()
        self.assertEqual(self.stop_hooks(), [])
        self.assertEqual(len(self.post_hooks()), 1)
        self.assertTrue((self.root / self.HOOK_PATH).is_file())
        self.manifest['hooks'] = {'stop_closure': False, 'plan_nudge': False}
        self.save()
        self.link()
        self.assertFalse((self.root / self.HOOK_PATH).exists())
        self.assertEqual(self.post_hooks(), [])

    @needs_sh
    def test_the_generated_post_tool_use_entry_returns_additional_context_through_the_shell(self):
        self.link()
        self.init_git()
        self.write(FRESH_PLAN, fresh_plan_text())
        past = time.time() - 3600
        os.utime(self.root / FRESH_PLAN, (past, past))
        directory = self.root / '.skillsync' / 'runs' / RUN_ID
        directory.mkdir(parents=True)
        (directory / 'checkpoints.md').write_text('RUN=2026-11-23T09:00:00+01:00\n- 10:00 w toku: nic\n', encoding='utf-8')
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = dict(os.environ, TMPDIR=tmp, CLAUDE_PROJECT_DIR=str(self.root))
        command = self.post_hooks()[0][1]['command']
        result = subprocess.run(['sh', '-c', command], input=json.dumps({**POST, 'cwd': str(self.root), 'session_id': 'x'}),
                                capture_output=True, text=True, timeout=60, cwd=self.root, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('wolna pojemnosc', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])


# ---------------------------------------------------------------------------------------------------------------
# CORE-1150-AUTOKEEP: aktualnosc planu w haku Stop (plan-fresh) i wpis z `--cli`
# ---------------------------------------------------------------------------------------------------------------
RUN_ID = '2026-11-23-core-1150-autokeep'
FRESH_PLAN = 'docs/plans/2026-11-23-core-1150-autokeep.md'
SHA = 'abc1234def0'


def fresh_plan_text(steps=('[ ] A-1 jeden', '[ ] A-2 dwa'), status='in_progress'):
    return '\n'.join(['# Plan', '', f'Status: {status}', f'Przebieg: {RUN_ID}', '', '## Kroki', ''] + [f'- {item}' for item in steps]) + '\n'


class FreshnessCase(WorkflowFixture):
    """A project with a committed in_progress plan; the checkpoint file is newer than the plan (the window of `plan-fresh`)."""

    def setUp(self):
        super().setUp()
        self.hook = self.root / '.claude/hooks/check-closure.py'
        self.hook.parent.mkdir(parents=True)
        self.hook.write_bytes(HOOK_SOURCE.read_bytes())
        self.write('README.md', 'fixture\n')
        self.write('.gitignore', '.claude/\n.skillsync/\n')
        self.write(FRESH_PLAN, fresh_plan_text())
        self.init_git()
        past = time.time() - 3600
        os.utime(self.root / FRESH_PLAN, (past, past))
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cli_json = json.dumps([sys.executable, str(CLI)])

    def checkpoint(self, *lines):
        directory = self.root / '.skillsync' / 'runs' / RUN_ID
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'checkpoints.md').write_text('\n'.join(['RUN=2026-11-23T09:00:00+01:00', *lines]) + '\n', encoding='utf-8')

    def agent_start(self, step):
        directory = self.root / '.skillsync' / 'runs' / RUN_ID
        directory.mkdir(parents=True, exist_ok=True)
        done = subprocess.run([sys.executable, str(CLI), 'agent-run', 'start', str(self.root), '--run-id', RUN_ID, '--role', 'implementer',
                               '--kind', 'run', '--step', step], capture_output=True, text=True, timeout=30, cwd=self.root)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def fire(self, session='s1', cli='default', extra=None, payload=None):
        args = [sys.executable, str(self.hook)]
        if cli == 'default':
            args += ['--cli', self.cli_json]
        elif cli is not None:
            args += ['--cli', cli]
        raw = json.dumps({'cwd': str(self.root), 'session_id': session, 'stop_hook_active': False, **(payload or {})})
        result = subprocess.run(args, input=raw, capture_output=True, text=True, timeout=60, cwd=self.root,
                                env={**os.environ, 'TMPDIR': self.tmp, 'TMP': self.tmp, 'TEMP': self.tmp, **(extra or {})})
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def stale(self):
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA}); w toku: A-2')


@needs_hook
class FreshnessHook(FreshnessCase):
    def test_stale_plan_blocks_once_per_session_and_plan(self):
        self.stale()
        first = self.fire()
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        for fragment in ('step-reported-unchecked', 'A-1', FRESH_PLAN, '[x]', 'w toku: A-1 (review)', 'agent-run end'):
            self.assertIn(fragment, first.stderr)
        self.assertEqual(self.fire().returncode, 0)                 # the same session and list: once
        self.assertEqual(self.fire(session='s2').returncode, 2)     # another session: again

    def test_without_a_session_id_the_block_is_once_per_repo_and_list(self):
        self.stale()
        self.assertEqual(self.fire(session=None).returncode, 2)
        self.assertEqual(self.fire(session=None).returncode, 0)

    def test_untrusted_text_in_the_message_is_cleaned_and_cut(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_under_test', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.clean_text('a\x1b[31mb\nc\u0105', 40), 'a?[31mb?c?')
        self.assertEqual(len(module.clean_text('x' * 500, 40)), 40)

    def events(self):
        path = self.root / '.skillsync' / 'hook-events.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []

    def test_an_unreadable_cli_argument_leaves_one_event_per_session_and_never_blocks(self):
        self.stale()
        for fire in (self.fire, lambda **kw: self.fire(payload={'hook_event_name': 'PostToolUse', 'tool_name': 'Agent'}, **kw)):
            before = len(self.events())
            result = fire(cli='["C:\\Tools\\x\\python.exe"', session='bad-' + str(before))   # not valid JSON (the Windows backslash loss, truncated here)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            self.assertEqual(len(self.events()), before + 1)
            self.assertEqual(self.events()[-1]['event'], 'cli-unreadable')
            self.assertNotIn('python.exe', json.dumps(self.events()[-1]))      # the content of the argument is never written
            fire(cli='["C:\\Tools\\x\\python.exe"', session='bad-' + str(before))
            self.assertEqual(len(self.events()), before + 1)              # once per session

    def test_a_missing_cli_argument_is_not_an_event(self):
        self.stale()
        self.assertEqual(self.fire(cli=None).returncode, 0)
        self.assertEqual(self.events(), [])

    def test_a_changed_list_blocks_again_in_the_same_session(self):
        self.stale()
        self.assertEqual(self.fire().returncode, 2)
        self.checkpoint(f'- 10:30 zrobione: A-2 ({SHA})')
        self.assertEqual(self.fire().returncode, 2)

    def test_running_unmarked_blocks_with_the_agent_run_hint(self):
        self.agent_start('A-2')
        self.checkpoint('- 10:00 w toku: A-1')
        result = self.fire()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('step-running-unmarked', result.stderr)
        self.assertIn('w toku: A-2', result.stderr)

    def test_a_fresh_plan_does_not_block(self):
        self.checkpoint('- 10:00 w toku: A-1, A-2')
        result = self.fire()
        self.assertEqual((result.returncode, result.stderr), (0, ''))

    def test_a_session_without_a_plan_is_not_blocked(self):
        (self.root / FRESH_PLAN).unlink()
        self.git('add', '-A')
        self.git('commit', '-qm', 'no plan')
        self.stale()
        result = self.fire()
        self.assertEqual((result.returncode, result.stderr), (0, ''))

    def test_stop_hook_active_never_blocks(self):
        self.stale()
        self.assertEqual(self.fire(payload={'stop_hook_active': True}).returncode, 0)

    def test_without_cli_it_is_skipped(self):
        self.stale()
        result = self.fire(cli=None)
        self.assertEqual((result.returncode, result.stderr), (0, ''))

    def test_an_unparseable_cli_value_is_skipped(self):
        self.stale()
        for bad in ('{not json', '"string"', '[]', '[1]', '[""]'):
            self.assertEqual(self.fire(cli=bad).returncode, 0, bad)

    def test_an_unparseable_result_is_skipped(self):
        self.stale()
        garbage = json.dumps([sys.executable, '-c', 'print("not json")'])
        self.assertEqual(self.fire(cli=garbage).returncode, 0)
        wrong = json.dumps([sys.executable, '-c', 'print("[1, 2]")'])
        self.assertEqual(self.fire(cli=wrong).returncode, 0)

    def test_a_failing_cli_is_skipped(self):
        self.stale()
        failing = json.dumps([sys.executable, '-c', 'import sys; sys.exit(3)'])
        self.assertEqual(self.fire(cli=failing).returncode, 0)

    def test_a_missing_cli_program_is_skipped(self):
        self.stale()
        self.assertEqual(self.fire(cli=json.dumps(['/no/such/program-skillsync'])).returncode, 0)

    def test_the_shared_deadline_cuts_a_slow_cli(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_under_test', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        slow = json.dumps([sys.executable, '-c', 'import time; time.sleep(30)'])
        module.TOTAL_BUDGET = 1.5
        module._STARTED = time.monotonic()
        old = sys.argv
        sys.argv = ['check-closure.py', '--cli', slow]
        try:
            started = time.monotonic()
            self.assertIsNone(module.plan_fresh(str(self.root)))
            self.assertLess(time.monotonic() - started, 5)
            module._STARTED = time.monotonic() - 10   # the budget is already spent: the CLI is not even started
            self.assertIsNone(module.plan_fresh(str(self.root)))
        finally:
            sys.argv = old

    def test_the_deadline_is_a_total_one_with_git(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_budget', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertLessEqual(module.TOTAL_BUDGET, 7.0)
        self.assertLess(module.TOTAL_BUDGET, 10)   # the entry has a 10 s timeout

    def test_the_soft_reminder_does_not_block_and_comes_once(self):
        self.checkpoint('- 10:00 w toku: nic')
        first = self.fire()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('wolna pojemnosc:', first.stderr)
        self.assertIn('A-1, A-2', first.stderr)
        again = self.fire()
        self.assertEqual((again.returncode, again.stderr), (0, ''))
        self.assertIn('wolna pojemnosc:', self.fire(session='s2').stderr)

    def test_the_soft_reminder_is_silent_with_one_open_step_or_a_step_in_progress(self):
        self.checkpoint('- 10:00 w toku: A-1')
        self.assertEqual(self.fire().stderr, '')

    def test_a_block_does_not_consume_the_reminder(self):
        self.stale()   # A-1 reported with sha, A-2 in progress: one open step in w toku -> no idle anyway
        self.write(FRESH_PLAN, fresh_plan_text(('[ ] A-1 a', '[ ] A-2 b', '[ ] A-3 c')))
        past = time.time() - 3600
        os.utime(self.root / FRESH_PLAN, (past, past))
        self.checkpoint(f'- 10:00 zrobione: A-1 ({SHA}); w toku: A-9')
        blocked = self.fire()
        self.assertEqual(blocked.returncode, 2)
        self.assertNotIn('wolna pojemnosc', blocked.stderr)
        self.checkpoint('- 10:10 w toku: A-9')
        self.assertIn('wolna pojemnosc', self.fire().stderr)

    def test_closure_and_freshness_are_reported_together(self):
        self.stale()
        self.write('docs/plans/2026-11-05-x.md', plan_text())   # a done plan without a final record
        # assertions changed on purpose (CORE-1170-STOPHOOK): the run must have a local trace without `final` to be "unclosed" on this machine
        (self.root / '.skillsync' / 'runs' / 'abc').mkdir(parents=True, exist_ok=True)
        result = self.fire()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Plan done bez zamkniecia przebiegu', result.stderr)
        self.assertIn('step-reported-unchecked', result.stderr)


@needs_hook
class ForeignRunHook(FreshnessCase):
    """CORE-1170-STOPHOOK: a plan merged from another machine (its `.skillsync/runs` is outside git) is not an unclosed run here."""

    def done_plan(self, extra=''):
        self.write('docs/plans/2026-11-05-x.md', plan_text() + extra)

    def test_a_done_plan_with_a_result_and_no_local_run_does_not_block(self):
        self.done_plan('\n## Wynik\n\nGotowe, szczegoly w PR.\n')
        result = self.fire()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('Plan done', result.stderr)   # (the soft reminder about free capacity is another message)

    def test_without_a_result_the_hook_asks_for_the_result_and_never_for_a_final_of_a_foreign_run(self):
        self.done_plan()
        result = self.fire()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('sekcje Wynik', result.stderr)
        self.assertIn('przebieg abc', result.stderr)
        self.assertNotIn('--final', result.stderr)
        self.assertNotIn('skillsync retro', result.stderr)
        self.assertNotIn('Plan done bez zamkniecia przebiegu', result.stderr)
        self.assertEqual(self.fire().returncode, 0, 'once per session')

    def test_the_template_placeholder_is_not_a_result(self):
        self.done_plan('\n## Wynik\n\n(po wykonaniu)\n')
        self.assertEqual(self.fire().returncode, 2)

    def test_a_local_run_without_a_final_record_still_blocks_and_suggests_final(self):
        self.done_plan('\n## Wynik\n\nGotowe.\n')
        (self.root / '.skillsync' / 'runs' / 'abc').mkdir(parents=True)
        result = self.fire()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('Plan done bez zamkniecia przebiegu', result.stderr)
        self.assertIn('--final', result.stderr)

    def test_a_local_run_with_a_final_record_is_closed(self):
        self.done_plan()
        runs = self.root / '.skillsync' / 'runs'
        runs.mkdir(parents=True)
        (runs / 'abc.json').write_text(run_json('abc', final=True), encoding='utf-8')
        self.assertEqual(self.fire().returncode, 0)


@needs_hook
class ResultSectionParity(unittest.TestCase):
    """CORE-1170-STOPHOOK: the hook is a standalone script, so its `plan_has_result` is a copy of `quality._plan_has_result`; the same corpus must give the same answer."""

    CORPUS = (
        ('# X\n\nStatus: done\n\n## Wynik\n\nGotowe.\n', True),
        ('# X\n\n## Wynik\n\n## Inna\n\ntresc\n', False),                 # an empty Result before the next section
        ('# X\n\n## Wynik\n\n(po wykonaniu)\n', False),                       # the template
        ('# X\n\n## Wynik\n\n### Testy\n\n### Ryzyka\n', False),              # the empty template: only `###` headings
        ('# X\n\n## Wynik\n\n### Testy\n\nPASS 12 testow.\n', True),
        ('# X\n\n### Wynik\n\nGotowe.\n', False),                            # `###` is not the section
        ('# X\n\n## Wyniki pomiarow\n\ncos\n', False),                       # another word
        ('# X\n\n## Wynik\n   \n', False),                                    # white space only
        ('# X\n\n## Result\n\nDone, see the PR.\n', True),
        ('# X\n\nbez sekcji\n', False),
    )

    def test_the_hook_copy_and_the_doctor_function_agree(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_parity', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from skillsync_workflow import quality
        for text, expected in self.CORPUS:
            with self.subTest(text=text):
                self.assertEqual(module.plan_has_result(text), expected)
                self.assertEqual(quality._plan_has_result(text), expected)


# ---------------------------------------------------------------------------------------------------------------
# CORE-1150-NUDGE: kanal widoczny dla modelu (PostToolUse `Agent|Task`, `additionalContext`)
# ---------------------------------------------------------------------------------------------------------------
POST = {'hook_event_name': 'PostToolUse', 'tool_name': 'Agent', 'tool_input': {}, 'tool_response': {}, 'tool_use_id': 't1'}


@needs_hook
class NudgeHook(FreshnessCase):
    def post(self, **kwargs):
        kwargs['payload'] = {**POST, **kwargs.get('payload', {})}
        return self.fire(**kwargs)

    def context(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(list(data), ['hookSpecificOutput'])
        self.assertEqual(data['hookSpecificOutput']['hookEventName'], 'PostToolUse')
        return data['hookSpecificOutput']['additionalContext']

    def assertSilent(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_it_never_blocks_where_stop_blocks_and_survives_a_non_ascii_payload(self):
        self.stale()
        self.assertEqual(self.fire(session='stop').returncode, 2)            # Stop blokuje ten plan
        result = self.post(session='post', payload={'tool_response': 'zażółć gęślą jaźń \U0001F642 \x1b[31m'})
        text = self.context(result)                                           # exit 0 i poprawny JSON mimo nie-ASCII w stdin
        self.assertTrue(text.startswith('skillsync plan-fresh:'), text)
        self.assertNotIn('\x1b', result.stdout)

    def test_idle_capacity_goes_to_additional_context_once_per_fingerprint(self):
        self.checkpoint('- 10:00 w toku: nic')
        first = self.post()
        text = self.context(first)
        self.assertIn('wolna pojemnosc:', text)
        self.assertIn('A-1, A-2', text)
        self.assertEqual(first.stderr, '')
        self.assertSilent(self.post())
        self.assertIn('wolna pojemnosc:', self.context(self.post(session='s2')))

    def test_stale_plan_adds_one_short_line_per_finding_and_never_blocks(self):
        self.stale()
        text = self.context(self.post())
        self.assertIn('step-reported-unchecked A-1', text)
        self.assertIn(FRESH_PLAN, text)
        self.assertIn('[x]', text)
        self.assertSilent(self.post())
        self.checkpoint(f'- 10:30 zrobione: A-2 ({SHA})')   # another list: reported again
        self.assertIn('A-2', self.context(self.post()))

    def test_the_nudge_does_not_consume_the_stop_marker(self):
        self.stale()
        self.context(self.post())
        self.assertEqual(self.fire().returncode, 2)

    def test_stop_does_not_consume_the_nudge_marker(self):
        self.stale()
        self.assertEqual(self.fire().returncode, 2)
        self.assertIn('step-reported-unchecked', self.context(self.post()))

    def test_the_idle_reminder_is_independent_in_both_channels(self):
        self.checkpoint('- 10:00 w toku: nic')
        self.assertIn('wolna pojemnosc', self.fire().stderr)
        self.assertIn('wolna pojemnosc', self.context(self.post()))

    def test_silent_inside_a_subagent(self):
        self.stale()
        self.assertSilent(self.post(payload={'agent_id': 'a1', 'agent_type': 'implementer'}))
        self.assertIn('step-reported-unchecked', self.context(self.post()))   # the marker was not spent

    def test_silent_without_a_plan(self):
        (self.root / FRESH_PLAN).unlink()
        self.git('add', '-A')
        self.git('commit', '-qm', 'no plan')
        self.stale()
        self.assertSilent(self.post())

    def test_silent_without_docs_plans(self):
        shutil.rmtree(self.root / 'docs')
        self.stale()
        self.assertSilent(self.post())

    def test_silent_when_there_is_nothing_to_say(self):
        self.checkpoint('- 10:00 w toku: A-1, A-2')
        self.assertSilent(self.post())

    def test_silent_on_errors_and_without_cli(self):
        self.stale()
        self.assertSilent(self.post(cli=None))
        for bad in ('{not json', '[]', json.dumps(['/no/such/program-skillsync']),
                    json.dumps([sys.executable, '-c', 'import sys; sys.exit(3)']),
                    json.dumps([sys.executable, '-c', 'print("not json")']),
                    json.dumps([sys.executable, '-c', 'print("[1]")'])):
            self.assertSilent(self.post(cli=bad))

    def test_a_slow_cli_is_cut_by_the_nudge_deadline(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_nudge', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertLessEqual(module.NUDGE_BUDGET, 3.0)
        self.stale()
        started = time.monotonic()
        result = self.post(cli=json.dumps([sys.executable, '-c', 'import time; time.sleep(30)']))
        self.assertLess(time.monotonic() - started, 5)
        self.assertSilent(result)

    def test_broken_stdin_is_silent(self):
        result = subprocess.run([sys.executable, str(self.hook), '--cli', self.cli_json], input='{nie json', capture_output=True,
                                text=True, timeout=30, cwd=self.root)
        self.assertSilent(result)

    def test_stop_hook_active_does_not_silence_post_tool_use(self):
        self.checkpoint('- 10:00 w toku: nic')
        self.assertIn('wolna pojemnosc', self.context(self.post(payload={'stop_hook_active': True})))

    def test_the_untrusted_text_is_cleaned(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_closure_clean', HOOK_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        data = {'findings': [{'code': 'step-reported-unchecked', 'blocking': True, 'step': 'A\x1b[31m\n' + 'x' * 100, 'plan': 'p\u0105' * 100}]}
        line = module.nudge_fresh_lines(data, 'owner-' + str(time.time()))[0]
        self.assertTrue(line.isprintable() and line.isascii())
        self.assertLess(len(line), 400)


class HookCliArgument(unittest.TestCase):
    """Windows: a hook command runs through Git bash, which loses one level of backslashes in an argument passed to a native program,
    so the JSON of `--cli` must carry `/` in absolute Windows paths (the live failure: the hook parsed no argv and did nothing)."""

    def test_windows_paths_are_written_with_forward_slashes_and_posix_argv_is_unchanged(self):
        from unittest import mock
        from skillsync_workflow import selfcmd
        fake = ['C:\\Tools\\a b\\python.exe', 'D:/x/skillsync', '/usr/bin/p\\q', 'rel\\x', '-P']
        with mock.patch.object(selfcmd, 'self_argv', return_value=fake):
            text = selfcmd.hook_argv_json()
        self.assertEqual(json.loads(text), ['C:/Tools/a b/python.exe', 'D:/x/skillsync', '/usr/bin/p\\q', 'rel\\x', '-P'])
        self.assertNotIn('C:\\', text)

    def test_unc_and_non_ascii_paths_have_neither_backslashes_nor_unicode_escapes(self):
        from unittest import mock
        from skillsync_workflow import selfcmd
        fake = ['\\\\serwer\\udzial\\python.exe', 'C:\\Tools\\\u0141ukasz\\skillsync']
        with mock.patch.object(selfcmd, 'self_argv', return_value=fake):
            text = selfcmd.hook_argv_json()
        self.assertNotIn('\\', text)
        self.assertEqual(json.loads(text), ['//serwer/udzial/python.exe', 'C:/Tools/\u0141ukasz/skillsync'])

    def test_the_generated_entries_use_the_same_argument(self):
        from unittest import mock
        from skillsync_workflow import cli, selfcmd
        with mock.patch.object(selfcmd, 'self_argv', return_value=['C:\\Py\\python.exe', 'C:\\sk\\skillsync']):
            command = cli.build_closure_hook_entry({})['hooks'][0]['command']
        self.assertIn('C:/Py/python.exe', command)
        self.assertNotIn('\\\\', command)


class DoctorStepIdWarnings(WorkflowFixture):
    """CORE-1160-STEPID K3: `doctor` warns about step ids the parser cannot read; never an error, never in the closure findings."""

    PATH = 'docs/plans/2026-11-23-core-1150-autokeep.md'

    def setUp(self):
        super().setUp()
        self.manifest['skills'] = ['feature']
        skill = self.library / 'skills/feature'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: Fixture\n---\nSkill', encoding='utf-8')
        installed = self.root / '.github/skills/feature'
        installed.mkdir(parents=True)
        (installed / 'SKILL.md').write_bytes((skill / 'SKILL.md').read_bytes())
        self.manifest['agents'] = []
        self.manifest['claude'] = False
        self.manifest['codex'] = False
        self.save()

    plan_warnings = DoctorClosure.plan_warnings

    def put_plan_steps(self, steps, status='in_progress', checkpoint=None):
        self.write(self.PATH, fresh_plan_text(steps=steps, status=status))
        past = time.time() - 3600
        os.utime(self.root / self.PATH, (past, past))
        if checkpoint is not None:
            directory = self.root / '.skillsync' / 'runs' / RUN_ID
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'checkpoints.md').write_text(checkpoint, encoding='utf-8')

    def unparsed(self):
        return [f for f in self.plan_warnings('core-1150-autokeep.md') if 'step-id-unparsed' in f['reason']]

    def test_unreadable_step_id_gives_one_warning_and_status_stays_pass(self):
        self.put_plan_steps(['[ ] FT.1 x', '[ ] A-2 dwa'])
        found = self.unparsed()  # plan_warnings asserts status PASS
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]['level'], 'warning')
        self.assertEqual([f for f in self.plan_warnings('core-1150-autokeep.md') if 'step-id-unparsed' not in f['reason']], [])

    def test_many_unreadable_steps_give_one_aggregate_warning(self):
        self.put_plan_steps(['[ ] FT.1 x', '[ ] FT.2 y', '[ ] FT.3 z', '[ ] A-2 dwa'])
        self.assertEqual(len(self.unparsed()), 1)
        self.assertEqual(len(self.plan_warnings('core-1150-autokeep.md')), 1)

    def test_done_plan_with_unreadable_step_is_not_warned(self):
        self.put_plan_steps(['[x] FT.1 x'], status='done')
        self.assertEqual(self.unparsed(), [])

    def test_checkpoint_element_without_step_id_warns(self):
        self.put_plan_steps(['[ ] A-1 jeden', '[ ] F1.2 dwa'], checkpoint='- 10:00 w toku: review R1, F1.2\n')
        found = [f for f in self.plan_warnings('core-1150-autokeep.md') if 'checkpoint-id-skipped' in f['reason']]
        self.assertEqual(len(found), 1, found)

    def test_plan_without_problems_has_no_step_id_finding(self):
        self.put_plan_steps(['[ ] A-1 jeden', '[ ] A-2 dwa'])
        self.assertEqual(self.plan_warnings('core-1150-autokeep.md'), [])

    def test_closure_and_blocking_findings_do_not_contain_the_new_ones(self):
        from skillsync_workflow import plan_fresh, quality
        self.put_plan_steps(['[ ] FT.1 x', '[ ] A-2 dwa'], checkpoint='- 10:00 w toku: review R1, A-2\n')
        self.assertEqual(quality.plan_closure_findings(self.root), [])
        self.assertEqual(plan_fresh.plan_findings(self.root), [])


if __name__ == '__main__':
    unittest.main()
