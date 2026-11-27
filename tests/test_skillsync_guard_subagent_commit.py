"""K6/A6 (plan CLAUDE-3, F5): the commit boundary of a sub-agent in `library/hooks/guard-commands.py --subagent-commit warn|deny|off`.

Contract pinned here (black box: JSON on stdin, argv, exit code, stdout, stderr):
- a call from a sub-agent is a PreToolUse payload with a non-empty `agent_id`; the main session (no `agent_id`, or a falsy one) is NOT touched by the
  new mode: its verdict equals the verdict of the same hook run WITHOUT the argument;
- `deny`: a forbidden command from a sub-agent -> exit 2 and a hint on stderr ("podumysly nie commituja; zglos zmiany orkiestratorowi");
  `warn`: exit 0 and `hookSpecificOutput.additionalContext` (information only); `off` and a missing, unknown or valueless argument: nothing;
- forbidden: git commit|push|tag|merge|rebase|reset|stash (but not `stash list|show`)|`checkout --`|restore|clean|`add -A`|`add .`|cherry-pick|revert and
  `gh pr create|merge|edit|close|ready` and `glab mr create|new|merge|accept|update|close`; allowed: read-only git, `git add <explicit file>`, `git checkout <branch>` / `-b`, `git switch`, `gh pr view|list|diff|checks`;
- recognized in every shell form (`-C`, `-c`, `bash -c`, `sh -c`, chains, `env`, `$(...)`, backticks, an absolute path);
- independent of `--mode`, `--tests`, `--attribution`; `SKILLSYNC_GUARD_EXCEPT` does not lift the boundary for a sub-agent;
- fail-open on garbage input.
Every "allowed" check lives in a test that also contains a forbidden control command, so a hook that ignores the argument fails it."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / 'library' / 'hooks'
DATA = ROOT / 'skillsync_workflow' / 'data'
HINT = 'podumysły nie commitują'
SUB = 'agent-7f3a'

FORBIDDEN = [
    'git commit -m x', 'git commit', 'git commit --amend --no-edit', 'git push', 'git push origin main', 'git push --force-with-lease',
    'git tag v1.0', 'git merge feature', 'git rebase main', 'git reset --hard', 'git reset HEAD file.py', 'git stash', 'git stash pop',
    'git stash drop', 'git stash push -m x', 'git checkout -- file.py', 'git checkout -- .', 'git restore file.py', 'git restore --staged file.py',
    'git clean -fd', 'git add -A', 'git add .', 'git cherry-pick abc123', 'git revert HEAD',
    'gh pr create --title x --body y', 'gh pr merge 5', 'gh pr edit 5 --title x', 'gh pr close 5', 'gh pr ready 5',
    # shell forms
    'git -C some/dir commit -m x', 'git -c user.name=x commit -m y', 'git -c user.name=x -c user.email=y@z push', 'git --no-pager commit -m x',
    "bash -c 'git commit -m x'", 'bash -c "git push origin main"', "sh -c 'git commit -m x'", "bash -lc 'cd x && git commit -m y'",
    "bash -c \"sh -c 'git commit -m x'\"", 'cd src && git commit -m x', 'true; git push', 'false || git push', 'git status && git commit -m x',
    'git diff | git commit -F -', 'echo x | git commit -F -', 'env FOO=1 git commit -m x', 'FOO=1 git commit -m x', 'FOO=1 BAR=2 git push',
    'timeout 30 git push', 'command git commit -m x', 'echo $(git commit -m x)', 'echo `git commit -m x`', 'x=$(git push); echo $x',
    '/usr/bin/git commit -m x', '/usr/bin/env git push', 'echo a\ngit commit -m x', 'gh --repo o/r pr merge 3', '  git   commit   -m   x  ',
    'git commit -m "zażółć gęślą jaźń"', 'git commit -m "tekst: git status"',
    # review F5 m4, m7
    'git tag v1', 'git tag -a v1 -m x', 'git clean -fd', 'git clean -f',
    'git --config-env n=V commit -m x', 'git --super-prefix p/ commit -m x', 'gh pr -R o/r merge 5', 'gh pr --repo o/r close 5',
    "command eval 'git commit -m x'", "eval 'git push'", "eval git commit -m x",
    # GitLab (`glab mr`): the same boundary as `gh pr`, with the documented aliases `new` and `accept`
    'glab mr create --draft --title x', 'glab mr new --title x', 'glab mr merge 5', 'glab mr accept 5', 'glab mr update 5 --ready', 'glab mr close 5',
    'glab -R g/r mr merge 5', 'glab mr -R g/r merge 5', 'glab mr --repo g/s/r close 5', 'glab --repo=g/r mr update 5', 'glab.exe mr merge 5',
    '/usr/bin/glab mr merge 5', "bash -c 'glab mr merge 5'", 'cd src && glab mr accept 5', 'env A=1 glab mr create',
]
ALLOWED = [
    'git status', 'git status --short', 'git diff', 'git diff --stat HEAD~1', 'git log --oneline -5', 'git show HEAD', 'git show HEAD:README.md',
    'git branch --show-current', 'git rev-parse HEAD', 'git rev-parse --show-toplevel', 'git ls-files', 'git blame README.md', 'git stash list',
    'git stash show', 'git add src/a.py', 'git add -- src/a.py', 'git add ./src/a.py', 'git add .gitignore', 'git add src/a.py tests/test_a.py',
    'git -C some/dir status', 'git -c core.pager=cat log -1', 'git --no-pager log -1', 'gh pr view 5', 'gh pr view', 'gh pr list', 'gh pr diff 5', 'gh pr checks 5',
    'git checkout main', 'git checkout -b feature/x', 'git switch main',
    'ls -la', 'python3 -m unittest tests.test_a', 'echo "git commit"', "echo 'git push origin main'", 'grep "git push" docs/x.md',
    "git log --grep='git commit'", 'git log --grep "zażółć"', 'cat <<EOF\ngit commit -m x\nEOF', 'bash -c "echo git commit"', "bash -c 'git status'",
    'printf "gh pr merge 5"', 'git commit-tree --help', 'echo github.com/o/r', 'ls | grep commit',
    # review F5 m4, m7: read-only forms
    'git tag -l', 'git tag --list', 'git tag -l "v*"', 'git tag -n', 'git tag --contains HEAD', 'git tag --points-at HEAD', 'git tag -v v1',
    'git clean -n', 'git clean --dry-run', 'git clean -nd', 'git clean -dn', 'gh pr -R o/r view 5', "eval 'git status'", 'git --config-env n=V status',
    'glab mr view 5', 'glab mr show 5', 'glab mr list', 'glab mr ls', 'glab mr -R g/r view 5', 'glab auth status', 'glab mr', 'printf "glab mr merge 5"',
    'grep "glab mr merge" docs/x.md',
]


def attribution_text():
    return 'Co-Authored' + '-By: Cla' + 'ude Code <x@y.z>'  # class B of the attribution patterns needs a product name after the model name


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='guard-subagent-commit-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = child(self.tmp, 'proj')
        descend(self.tmp, 'proj/.git').mkdir(parents=True)
        hooks = descend(self.tmp, 'proj/.claude/hooks')
        hooks.mkdir(parents=True)
        self.hook = child(hooks, 'guard-commands.py')
        shutil.copy2(HOOKS / 'guard-commands.py', self.hook)   # next to the script: the tests guard, its table and the attribution patterns, as `link` installs them
        shutil.copy2(HOOKS / 'guard-tests.py', child(hooks, 'guard-tests.py'))
        shutil.copy2(DATA / 'test_tools.json', child(hooks, 'test-tools.json'))
        shutil.copy2(DATA / 'attribution-patterns.json', child(hooks, 'attribution-patterns.json'))

    def payload(self, command, agent_id=None, **extra):
        data = {'tool_name': 'Bash', 'cwd': str(self.project), 'tool_input': {'command': command}}
        if agent_id is not None:
            data['agent_id'] = agent_id
            data['agent_type'] = 'general-purpose'
        data.update(extra)
        return data

    def raw(self, stdin, *args, env=None):
        environment = {key: value for key, value in os.environ.items() if key != 'SKILLSYNC_GUARD_EXCEPT'}
        environment.update(env or {})
        data = stdin if isinstance(stdin, bytes) else stdin.encode('utf-8')
        return subprocess.run([sys.executable, str(self.hook), *args], input=data, capture_output=True, timeout=60, env=environment)

    def run_hook(self, command, *args, agent_id=None, env=None, **extra):
        result = self.raw(json.dumps(self.payload(command, agent_id, **extra)), *args, env=env)
        return result.returncode, result.stdout.decode('utf-8', 'replace'), result.stderr.decode('utf-8', 'replace')

    def sub(self, command, mode, *args, **kwargs):
        return self.run_hook(command, '--subagent-commit', mode, *args, agent_id=SUB, **kwargs)

    def context(self, stdout):
        data = json.loads(stdout)
        self.assertEqual(set(data), {'hookSpecificOutput'})
        out = data['hookSpecificOutput']
        self.assertEqual(set(out), {'hookEventName', 'additionalContext'}, 'information only: no permissionDecision, no updatedInput')
        self.assertEqual(out['hookEventName'], 'PreToolUse')
        self.assertTrue(out['additionalContext'].strip())
        return out['additionalContext']

    def assert_denied(self, command, *args, **kwargs):
        rc, out, err = self.sub(command, 'deny', *args, **kwargs)
        self.assertEqual(rc, 2, 'not denied: %r (stdout=%r stderr=%r)' % (command, out, err))
        self.assertIn(HINT, err, command)
        self.assertIn('orkiestrator', err, command)
        self.assertNotIn('Traceback', err)

    def assert_silent_ok(self, rc_out_err, command=''):
        rc, out, err = rc_out_err
        self.assertEqual(rc, 0, '%r: %r' % (command, err))
        self.assertEqual(out.strip(), '', command)
        self.assertNotIn(HINT, err, command)


class Deny(HookCase):
    def test_every_forbidden_command_from_a_subagent_is_denied_with_the_hint(self):
        for command in FORBIDDEN:
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_allowed_commands_pass_while_the_forbidden_control_is_denied(self):
        self.assert_denied('git commit -m control')   # the control: a hook that ignores the argument fails here, so the negatives below are not vacuous
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assert_silent_ok(self.sub(command, 'deny'), command)

    def test_the_assumption_checkout_dash_dash_is_forbidden_but_branch_switching_is_not(self):
        self.assert_denied('git checkout -- src/a.py')
        for command in ('git checkout main', 'git checkout -b topic', 'git switch topic'):
            with self.subTest(command=command):
                self.assert_silent_ok(self.sub(command, 'deny'), command)

    def test_deny_does_not_depend_on_mode_tests_or_attribution(self):
        for extra in (('--mode', 'off'), ('--mode', 'deny'), ('--tests', 'off'), ('--tests', 'warn'), ('--attribution', 'off'),
                      ('--mode', 'off', '--tests', 'off', '--attribution', 'off')):
            with self.subTest(extra=extra):
                rc, out, err = self.run_hook('git commit -m x', *extra, '--subagent-commit', 'deny', agent_id=SUB)
                self.assertEqual(rc, 2, err)
                self.assertIn(HINT, err)
                rc, out, err = self.run_hook('git status', *extra, '--subagent-commit', 'deny', agent_id=SUB)
                self.assertEqual(rc, 0, err)

    def test_argument_order_does_not_matter(self):
        rc, _, err = self.run_hook('git push', '--subagent-commit', 'deny', '--mode', 'deny', '--tests', 'warn', agent_id=SUB)
        self.assertEqual(rc, 2, err)
        rc, _, err = self.run_hook('git push', '--tests', 'warn', '--mode', 'deny', '--subagent-commit', 'deny', agent_id=SUB)
        self.assertEqual(rc, 2, err)

    def test_the_one_time_exception_does_not_lift_the_boundary_for_a_subagent(self):
        for command in ("SKILLSYNC_GUARD_EXCEPT='user approved' git commit -m x", 'git commit -m x', "SKILLSYNC_GUARD_EXCEPT='r' git push"):
            with self.subTest(command=command, where='command prefix'):
                self.assert_denied(command, env={'SKILLSYNC_GUARD_EXCEPT': 'user approved'})
        with self.subTest(where='environment only'):
            self.assert_denied('git commit -m x', env={'SKILLSYNC_GUARD_EXCEPT': 'user approved'})

    def test_the_exception_prefix_keeps_its_old_meaning_for_the_main_session(self):
        command = "SKILLSYNC_GUARD_EXCEPT='r' git commit -m x"
        baseline = self.run_hook(command, '--mode', 'deny')
        self.assertEqual(self.run_hook(command, '--mode', 'deny', '--subagent-commit', 'deny'), baseline)

    def test_a_deny_message_for_an_attribution_commit_names_the_boundary_too(self):
        command = 'git commit -m x -m "%s"' % attribution_text()
        rc, out, err = self.sub(command, 'deny')
        self.assertEqual(rc, 2, err)
        self.assertIn(HINT, err, 'the sub-agent must learn about the boundary, not only about the attribution rule')
        rc, out, err = self.sub(command, 'deny', '--attribution', 'off')
        self.assertEqual(rc, 2, err)
        self.assertIn(HINT, err)

    def test_main_session_attribution_refusal_is_unchanged_and_has_no_boundary_hint(self):
        command = 'git commit -m x -m "%s"' % attribution_text()
        baseline = self.run_hook(command)
        self.assertEqual(baseline[0], 2, 'the control: the attribution guard refuses (needs library/hooks/attribution-patterns.json)')
        result = self.run_hook(command, '--subagent-commit', 'deny')
        self.assertEqual(result, baseline)
        self.assertNotIn(HINT, result[2])

    def test_quotes_heredocs_and_unicode(self):
        self.assert_denied('git commit -m "zażółć gęślą jaźń 日本語 🚀"')
        payload = json.dumps(self.payload('git commit -m "zażółć"', SUB), ensure_ascii=False)
        result = self.raw(payload, '--subagent-commit', 'deny')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assert_silent_ok(self.sub('git log --grep "zażółć gęślą"', 'deny'))

    def test_a_very_long_command_is_still_judged(self):
        padding = 'x' * 200000
        self.assert_denied('echo %s; git commit -m x' % padding)
        self.assert_denied('git commit -m "%s"' % padding)
        self.assert_silent_ok(self.sub('echo %s && git status' % padding, 'deny'))

    def test_a_tool_other_than_bash_and_a_non_string_command_are_ignored(self):
        data = self.payload('git commit -m x', SUB)
        data['tool_name'] = 'Read'
        result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b''))
        self.assert_denied('git commit -m x')   # the control
        for value in (None, 123, ['git', 'commit'], {'a': 1}, ''):
            with self.subTest(command=value):
                data = self.payload('x', SUB)
                data['tool_input']['command'] = value
                result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_deletion_refusal_of_the_old_guard_still_wins_in_a_subagent(self):
        rc, out, err = self.sub('rm -rf /', 'deny')
        self.assertEqual(rc, 2, err)


class ReviewF5(HookCase):
    def test_the_boundary_comes_before_the_exception_log(self):
        log = self.project / '.skillsync' / 'hook-exceptions.jsonl'
        child(self.project, 'src').mkdir()
        command = "SKILLSYNC_GUARD_EXCEPT='r' rm -rf src; git commit -m x"
        narrow = {'SKILLSYNC_GUARD_TMP_ROOTS': str(child(self.tmp, 'tmproot'))}   # the project lies under the system temp dir: narrow the temporary roots
        self.assert_denied(command, env=narrow)
        self.assertFalse(log.exists(), 'a command refused by the boundary left an exception entry')
        control = self.run_hook("SKILLSYNC_GUARD_EXCEPT='r' rm -rf src", '--subagent-commit', 'deny', env=narrow)   # a main session: the exception is logged
        self.assertEqual(control[0], 0, control[2])
        self.assertTrue(log.exists(), 'control: the exception log is written for the same deletion')

    def test_the_cases_of_m4_and_m7_in_one_place(self):
        self.assert_denied('git tag v1')
        for command in ('git tag -l', 'git clean -n', 'git clean -nd'):
            with self.subTest(command=command):
                self.assert_silent_ok(self.sub(command, 'deny'), command)
        for command in ('git --config-env n=V commit -m x', 'gh pr -R o/r merge 5', "command eval 'git commit -m x'"):
            with self.subTest(command=command):
                self.assert_denied(command)

    def test_a_whitespace_only_agent_id_is_a_subagent_and_a_truthy_non_string_is_the_main_session(self):
        for value in ('  ', '\t'):
            with self.subTest(agent_id=value):
                data = self.payload('git commit -m x')
                data['agent_id'] = value
                result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(HINT, result.stderr.decode('utf-8', 'replace'))
        for value in (123, ['a'], {'id': 1}, True, 1.5):
            with self.subTest(agent_id=value):
                data = self.payload('git commit -m x')
                data['agent_id'] = value
                result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
                self.assertEqual((result.returncode, result.stdout.strip()), (0, b''), result.stderr)
                self.assertNotIn(HINT, result.stderr.decode('utf-8', 'replace'))
        self.assert_denied('git commit -m x')   # the control


class Warn(HookCase):
    def test_a_forbidden_command_from_a_subagent_gives_information_and_exit_zero(self):
        for command in FORBIDDEN[:30] + ["bash -c 'git commit -m x'", 'git -C d commit -m x', 'echo $(git push)']:
            with self.subTest(command=command):
                rc, out, err = self.sub(command, 'warn')
                self.assertEqual(rc, 0, err)
                self.assertTrue(out.strip(), 'no additionalContext: %r' % command)
                self.assertTrue(self.context(out))

    def test_the_warning_for_allowed_commands_is_silent_and_a_control_warns(self):
        self.assertTrue(self.context(self.sub('git commit -m x', 'warn')[1]))
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assert_silent_ok(self.sub(command, 'warn'), command)

    def test_the_warning_says_that_the_command_was_already_executed(self):
        text = self.context(self.sub('git commit -m x', 'warn')[1])
        self.assertIn('już wykonane', text)
        self.assertIn('orkiestrator', text)
        self.assertNotIn('już wykonane', self.sub('git commit -m x', 'deny')[2], 'the denial is not a post-factum message')

    def test_warn_is_not_a_boundary_and_not_a_denial(self):
        rc, out, err = self.sub('git push', 'warn')
        self.assertEqual(rc, 0)
        self.assertNotIn('permissionDecision', out)
        self.assertNotIn('updatedInput', out)

    def test_warn_works_with_mode_off_and_next_to_the_test_warning(self):
        rc, out, err = self.sub('git commit -m x', 'warn', '--mode', 'off', '--attribution', 'off')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.context(out))
        both = self.sub('pytest && git commit -m x', 'warn', '--tests', 'warn')
        self.assertEqual(both[0], 0, both[2])
        text = self.context(both[1])
        self.assertIn('commit', text.lower())
        self.assertIn('skillsync test-run', text, 'the test-run warning is not lost')

    def test_a_wide_test_run_alone_is_not_a_commit_warning(self):
        rc, out, err = self.sub('pytest', 'deny', '--tests', 'warn')
        self.assertEqual(rc, 0, err)
        self.assertIn('skillsync test-run', self.context(out))
        self.assertNotIn(HINT, err)


class MainSession(HookCase):
    """The main session (no `agent_id`, or a falsy one) gets exactly what it gets without the argument."""

    def test_the_verdict_equals_the_one_without_the_argument_for_every_mode(self):
        commands = FORBIDDEN + ALLOWED
        for mode in ('deny', 'warn', 'off'):
            for command in commands:
                with self.subTest(mode=mode, command=command):
                    baseline = self.run_hook(command, '--mode', 'deny')
                    self.assertEqual(self.run_hook(command, '--mode', 'deny', '--subagent-commit', mode), baseline)
                    self.assertNotIn(HINT, baseline[2])

    def test_the_main_session_is_not_denied_for_a_commit_under_deny_while_a_subagent_is(self):
        self.assertEqual(self.run_hook('git commit -m x', '--subagent-commit', 'deny')[0], 0)
        self.assertEqual(self.run_hook('git push', '--subagent-commit', 'deny', '--mode', 'off')[0], 0)
        self.assert_denied('git commit -m x')   # the control: the same command from a sub-agent

    def test_falsy_agent_ids_are_the_main_session(self):
        for value in ('', None, 0, False, [], {}):
            with self.subTest(agent_id=value):
                data = self.payload('git commit -m x')
                data['agent_id'] = value
                result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
                self.assertEqual((result.returncode, result.stdout.strip()), (0, b''), result.stderr)
                self.assertNotIn(HINT, result.stderr.decode('utf-8', 'replace'))
        self.assert_denied('git commit -m x')   # the control

    def test_agent_type_alone_does_not_make_a_subagent(self):
        data = self.payload('git commit -m x')
        data['agent_type'] = 'general-purpose'
        result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b''), result.stderr)
        self.assert_denied('git commit -m x')   # the control

    def test_non_string_truthy_agent_ids_never_crash_the_hook(self):
        for value in (123, ['a'], {'id': 1}, True, 1.5):
            with self.subTest(agent_id=value):
                data = self.payload('git commit -m x')
                data['agent_id'] = value
                result = self.raw(json.dumps(data), '--subagent-commit', 'deny')
                self.assertIn(result.returncode, (0, 2), result.stderr)
                self.assertNotIn(b'Traceback', result.stderr)
        self.assert_denied('git commit -m x')   # the control: the hook runs at all


class Off(HookCase):
    def test_off_and_a_missing_argument_leave_a_subagent_command_alone(self):
        self.assert_denied('git commit -m x')   # the control
        for args in (('--subagent-commit', 'off'), (), ('--subagent-commit', 'bogus'), ('--subagent-commit',), ('--subagent-commit', '')):
            for command in ('git commit -m x', 'git push', "bash -c 'git commit -m x'", 'gh pr merge 5', 'git status'):
                with self.subTest(args=args, command=command):
                    rc, out, err = self.run_hook(command, *args, agent_id=SUB)
                    self.assertEqual(rc, 0, err)
                    self.assertEqual(out.strip(), '')
                    self.assertNotIn(HINT, err)

    def test_a_missing_argument_is_byte_identical_to_the_old_behaviour(self):
        for command in ('git commit -m x', 'git status', 'rm -rf /', 'pytest', 'git push'):
            for agent in (None, SUB):
                with self.subTest(command=command, agent=agent):
                    plain = self.run_hook(command, '--mode', 'deny', '--tests', 'warn', agent_id=agent)
                    off = self.run_hook(command, '--mode', 'deny', '--tests', 'warn', '--subagent-commit', 'off', agent_id=agent)
                    self.assertEqual(plain, off)
        self.assert_denied('git commit -m x')   # the control


class FailOpen(HookCase):
    def test_garbage_and_empty_input_exit_zero_in_every_mode(self):
        for stdin in (b'', b'   \n', b'{not json', b'[]', b'null', b'42', b'"git commit"', b'{}', b'{"tool_name":"Bash"}', b'\xff\xfe\x00', b'{"tool_name":"Bash","tool_input":null}'):
            for mode in ('deny', 'warn', 'off'):
                with self.subTest(stdin=stdin, mode=mode):
                    result = self.raw(stdin, '--subagent-commit', mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), b'')
        self.assert_denied('git commit -m x')   # the control: with valid input the same hook does refuse


if __name__ == '__main__':
    unittest.main()
