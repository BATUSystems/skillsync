"""ban on attribution and advertising of AI tools (data file, scanner, leakguard integration).

All sample texts are composed at run time from fragments, so neither this file nor the data file matches its own patterns."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import commit_file  # noqa: E402
from skillsync_workflow import attribution, leakguard  # noqa: E402
from layers_helpers import SKILL_TEXT  # noqa: E402
from test_skillsync_guard_hook import GuardBase  # noqa: E402
from test_skillsync_layers_doctor import DoctorCase  # noqa: E402
from test_skillsync_leakguard import LeakTestCase  # noqa: E402
from skillsync_workflow import guardhook  # noqa: E402

ADDR = 'noreply' + '@' + 'anthro' + 'pic.com'
FOOTER = 'Gener' + 'ated with [Cla' + 'ude Code](https://cla' + 'ude.com/cla' + 'ude-code)'
ROBOT = '\U0001F916 ' + FOOTER
TRAILER = 'Co-Auth' + 'ored-By: Cla' + 'ude Sonnet 5.5 <' + ADDR + '>'
CURSOR = 'Made ' + 'with Cur' + 'sor'
SESSION = 'Cla' + 'ude-Ses' + 'sion: https://example.invalid/s/1'
HUMAN = 'Co-Authored-By: Jan Kowalski <jan' + '@' + 'example.com>'
SAMPLES = {
    'attr-claude-footer': FOOTER,
    'attr-ai-tool-footer': 'Gener' + 'ated by Cop' + 'ilot',
    'attr-made-with': CURSOR,
    'attr-tool-link': 'https://cla' + 'ude.com/cla' + 'ude-code',
    'attr-session-trailer': SESSION,
    'attr-coauthor-name': 'Co-Auth' + 'ored-By: Co' + 'dex <someone' + '@' + 'example.com>',
    'attr-coauthor-address': 'Co-Auth' + 'ored-By: Bot <x@open' + 'ai.com>',
}


class DataTests(unittest.TestCase):
    def test_every_pattern_has_a_sample_and_matches_it(self):
        data = attribution.load()
        self.assertEqual({p['id'] for p in data['patterns']}, set(SAMPLES))
        for pattern in data['patterns']:
            with self.subTest(pattern['id']):
                self.assertTrue(pattern['compiled'].search(SAMPLES[pattern['id']]))

    def test_data_file_does_not_match_its_own_patterns(self):
        self.assertEqual(attribution.scan_text(attribution.DATA.read_text(encoding='utf-8')), [])

    def test_session_link_of_the_status_page_is_not_attribution(self):
        for text in ('https://cla' + 'ude.ai/code/session_01abc', 'session_url: "https://cla' + 'ude.ai/code/session_x"', 'Claude Code session link'):
            self.assertEqual(attribution.scan_text(text), [], text)

    def test_humans_and_product_prose_are_not_flagged(self):
        for text in ('Assisted-By: Ada Lovelace <ada' + '@' + 'example.com>', 'Co-Authored-By: Claude Dupont <claude' + '@' + 'firma.example>', 'Co-Authored-By: Devin Smith <d' + '@' + 'firma.example>',
                     'Gener' + 'ated with cursor-based pagination', 'Gener' + 'ated by Gemini API client in the docs',
                     'Made ' + 'with Cursor rules for the editor', 'Co-Authored-By: Ada Lovelace <ada' + '@' + 'example.com>'):
            self.assertEqual(attribution.scan_text(text), [], text)
        for text in ('Co-Authored-By: Cla' + 'ude <' + ADDR + '>', 'Co-Authored-By: Cla' + 'ude Opus 4.1 <x' + '@' + 'example.com>',
                     'Gener' + 'ated with Cur' + 'sor', 'Gener' + 'ated by [Copi' + 'lot](https://example.invalid)'):
            self.assertTrue(attribution.scan_text(text), text)

    def test_neutral_text_and_human_trailer_pass(self):
        for text in ('fix: parser\n\nplain body', HUMAN, 'the model generated a list of options', 'Made with care'):
            with self.subTest(text):
                self.assertEqual(attribution.scan_text(text), [])

    def test_scan_reports_class_and_line(self):
        found = attribution.scan_text('subject\n\n' + ROBOT + '\n' + TRAILER)
        self.assertEqual({(c, n) for _, c, n in found if c == 'A'}, {('A', 3)})
        self.assertIn(('B', 4), [(c, n) for _, c, n in found])

    def test_enforce_switch_is_one_data_entry(self):
        data = json.loads(attribution.DATA.read_text(encoding='utf-8'))
        data['enforce'] = ['A']
        path = Path(self._testMethodName + '.json')
        try:
            path.write_text(json.dumps(data), encoding='utf-8')
            self.assertEqual(attribution.scan_text(TRAILER, path), [])
            self.assertTrue(attribution.scan_text(FOOTER, path))
            self.assertFalse(any(e['id'].startswith('attr-coauthor') for e in attribution.leakguard_entries(path)))
        finally:
            path.unlink()

    def test_invalid_data_is_refused(self):
        for bad in ({'schema': 2}, {'schema': 1, 'enforce': ['C'], 'patterns': []},
                    {'schema': 1, 'enforce': ['A'], 'patterns': [{'id': 'X', 'class': 'A', 'regex': 'a'}]},
                    {'schema': 1, 'enforce': ['A'], 'patterns': [{'id': 'x', 'class': 'A', 'regex': '('}]}):
            path = Path(self._testMethodName + '.json')
            try:
                path.write_text(json.dumps(bad), encoding='utf-8')
                with self.assertRaises(attribution.AttributionError):
                    attribution.load(path)
            finally:
                path.unlink()


class RuleTests(unittest.TestCase):
    BASE = Path(__file__).resolve().parent.parent / 'library' / 'instructions' / '_base.md'

    def test_rule_is_in_the_core_only_base_without_literal_phrases(self):
        from skillsync_workflow import layers
        text = self.BASE.read_text(encoding='utf-8')
        self.assertIn('Bez stopek i podpisów narzędzi AI', text)
        self.assertIn('instructions/_base.md', layers.CORE_ONLY_FILES)
        self.assertEqual(attribution.scan_text(text), [])

    def test_base_fits_the_budget(self):
        import yaml
        limit = yaml.safe_load((self.BASE.parent.parent / 'workflow' / 'defaults.yaml').read_text(encoding='utf-8'))['context']['base_max_bytes']
        self.assertLessEqual(len(self.BASE.read_bytes()), limit)


class UserCommandTests(unittest.TestCase):
    """`skillsync attribution`: explicit, opt-in write into the user settings; nothing else is touched."""
    CLI = Path(__file__).resolve().parent.parent / 'skillsync'

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix='attr-home-'))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.file = self.home / '.claude' / 'settings.json'

    def run_cli(self, *args):
        env = {**os.environ, 'HOME': str(self.home), 'USERPROFILE': str(self.home)}
        return subprocess.run([sys.executable, str(self.CLI), 'attribution', *args], capture_output=True, text=True, timeout=30, env=env)

    def test_status_without_flags_writes_nothing(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.file.exists())
        self.assertIn('on or unset', result.stdout)

    def test_user_writes_the_setting_keeps_other_keys_and_backs_up(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({'model': 'x', 'permissions': {'allow': ['Bash(ls)']}, 'attribution': {'commit': 'c'}}), encoding='utf-8')
        result = self.run_cli('--user')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['attribution'], guardhook.ATTRIBUTION_SETTING)
        self.assertEqual((data['model'], data['permissions']), ('x', {'allow': ['Bash(ls)']}))
        self.assertTrue((self.file.parent / 'settings.json.skillsync-backup').exists())
        self.assertEqual(self.run_cli().returncode, 0)
        again = self.run_cli('--user')
        self.assertIn('Up to date', again.stdout)

    def test_dry_run_and_unreadable_file_write_nothing(self):
        self.assertIn('Would update', self.run_cli('--user', '--dry-run').stdout)
        self.assertFalse(self.file.exists())
        self.file.parent.mkdir(parents=True)
        self.file.write_text('{broken', encoding='utf-8')
        result = self.run_cli('--user')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.file.read_text(encoding='utf-8'), '{broken')

    def test_print_managed_prints_the_object_only(self):
        result = self.run_cli('--print-managed')
        self.assertEqual(json.loads(result.stdout), {'attribution': guardhook.ATTRIBUTION_SETTING})
        self.assertFalse(self.file.exists())


class LayerCheckTests(DoctorCase):
    """A layer or a project instruction that carries the banned text is a FAIL in `layers check` and in `doctor`."""

    def org_with(self, rel, text):
        files = {'instructions/y.md': 'org instruction that is longer than ten bytes\n', 'skills/gamma/SKILL.md': SKILL_TEXT.format('gamma')}
        files[rel] = text
        return files

    def test_layers_check_fails_for_a_layer_that_tells_the_agent_to_add_a_footer(self):
        for rel in ('instructions/y.md', 'instructions/_base.d/50-x.md', 'skills/gamma/ref.md', 'workflow/defaults.yaml'):
            with self.subTest(rel):
                self.layers(org_files=self.org_with(rel, 'Append this line to every PR:\n' + FOOTER + '\n'))
                project = self.new_project('p-' + rel.replace('/', '-'), skills=['beta'])
                code, report = self.cli_json('layers', 'check', '--project', str(project))
                self.assertNotEqual(code, 0, report)
                self.assertEqual(report['status'], 'FAIL')
                hits = [f for f in report['findings'] if f['code'] == 'attribution-text']
                self.assertTrue(hits, report)
                self.assertTrue(any(rel in hit['message'] for hit in hits), hits)
                self.assertFalse(any('Gener' + 'ated' in hit['message'] for hit in hits))   # the message never repeats the text

    def test_a_clean_layer_passes_and_doctor_maps_the_finding_to_an_error(self):
        self.layers()
        project = self.new_project('clean2', skills=['alpha', 'beta', 'gamma'])
        self.link_ok(project)
        code, report = self.cli_json('layers', 'check', '--project', str(project))
        self.assertEqual([f for f in report['findings'] if f['code'] == 'attribution-text'], [])
        self.layers(org_files=self.org_with('skills/gamma/ref.md', TRAILER + '\n'))
        project = self.new_project('dirty', skills=['beta', 'gamma'])
        self.link_ok(project)
        report = self.doctor(project, code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(self.levels(report, 'banned AI attribution'), ['error'], report)

    def test_generated_instruction_files_with_the_text_fail_doctor(self):
        self.layers()
        project = self.new_project('inst', skills=['beta'])
        self.link_ok(project)
        with open(project / 'AGENTS.md', 'a', encoding='utf-8') as handle:
            handle.write('\n' + CURSOR + '\n')
        report = self.doctor(project, code=1)
        self.assertEqual(self.levels(report, 'banned AI attribution', 'AGENTS.md'), ['error'], report)


class InstructionFilesTests(DoctorCase):
    """The rule of `_base.md` reaches every generated instruction file, also with `_base.d` fragments of a layer."""

    def test_generated_files_carry_the_rule(self):
        from unittest import mock
        import test_skillsync_layers_doctor as doctor_module
        base = (Path(__file__).resolve().parent.parent / 'library' / 'instructions' / '_base.md').read_text(encoding='utf-8')
        files = dict(doctor_module.CORE_FILES, **{'instructions/_base.md': base + '\n<!-- SKILLSYNC:BASE-END -->\n'})
        org = dict(doctor_module.ORG_FILES, **{'instructions/_base.d/50-org.md': 'Org fragment.\n'})
        with mock.patch.dict(doctor_module.CORE_FILES, files, clear=True):
            self.layers(org_files=org)
            project = self.new_project('rule', skills=['beta'])
            self.link_ok(project)
        found = [rel for rel in ('CLAUDE.md', 'AGENTS.md', '.github/copilot-instructions.md', '.codex/skillsync-instructions.md')
                 if (project / rel).is_file() and 'Bez stopek i podpisów narzędzi AI' in (project / rel).read_text(encoding='utf-8')]
        self.assertTrue(found, 'no generated instruction file carries the rule')
        for rel in found:
            self.assertIn('Org fragment.', (project / rel).read_text(encoding='utf-8'))


class LinkSettingsTests(GuardBase):
    """`link` writes the host setting that switches the attribution of Claude Code off; `doctor` checks it."""

    def attribution_findings(self):
        report = self.cli('doctor')
        return [f for f in json.loads(report.stdout)['findings'] if 'host setting' in f['reason'] or 'AI attribution' in f['reason']]

    def test_link_writes_the_empty_form_and_is_idempotent(self):
        self.link()
        self.assertEqual(self.local()['attribution'], guardhook.ATTRIBUTION_SETTING)
        before = (self.root / self.LOCAL).read_text(encoding='utf-8')
        result = self.link()
        self.assertNotIn('Updated', result.stdout)
        self.assertEqual((self.root / self.LOCAL).read_text(encoding='utf-8'), before)

    def test_foreign_keys_stay_and_a_switched_on_value_is_replaced_with_a_warning(self):
        self.write(self.LOCAL, json.dumps({'permissions': {'allow': ['Bash(ls)']}, 'attribution': {'commit': 'x', 'pr': 'y'}, 'env': {'A': '1'}}))
        result = self.link()
        data = self.local()
        self.assertEqual(data['attribution'], guardhook.ATTRIBUTION_SETTING)
        self.assertEqual(data['permissions'], {'allow': ['Bash(ls)']})
        self.assertEqual(data['env'], {'A': '1'})
        self.assertIn('cannot be turned off', result.stdout)

    def test_every_project_gets_it_also_without_the_agent_flow(self):
        self.manifest['skills'] = ['plain']
        self.save()
        self.link()
        self.assertEqual(self.local()['attribution'], guardhook.ATTRIBUTION_SETTING)

    def test_doctor_errors_when_the_setting_is_missing_and_clears_after_link(self):
        found = self.attribution_findings()
        self.assertTrue(found and all(f['level'] == 'warning' for f in found), found)   # never linked: a hint
        self.link()
        self.assertEqual([f for f in self.attribution_findings() if f['level'] == 'error'], [])
        data = self.local()
        data['attribution'] = {'commit': 'Co', 'pr': ''}
        (self.root / self.LOCAL).write_text(json.dumps(data), encoding='utf-8')
        self.assertTrue(any(f['level'] == 'error' for f in self.attribution_findings()))

    def test_shared_and_user_settings_with_attribution_on_are_warnings(self):
        self.link()
        self.write('.claude/settings.json', json.dumps({'includeCoAuthoredBy': True}))
        found = self.attribution_findings()
        self.assertEqual({f['level'] for f in found}, {'warning'}, found)
        self.assertTrue(any(f['path'] == '.claude/settings.json' for f in found))

    def test_broken_or_outdated_pattern_copy_is_visible_in_doctor(self):
        self.link()
        copy = self.root / guardhook.ATTRIBUTION_HOOK
        copy.write_text('{not json', encoding='utf-8')
        found = self.attribution_findings_for(guardhook.ATTRIBUTION_HOOK)
        self.assertEqual([f['level'] for f in found], ['error'])   # a silent hook must never pass as healthy
        copy.unlink()
        self.assertEqual([f['level'] for f in self.attribution_findings_for(guardhook.ATTRIBUTION_HOOK)], ['error'])
        self.link()
        data = json.loads(attribution.DATA.read_text(encoding='utf-8'))
        data['enforce'] = ['A']
        copy.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual([f['level'] for f in self.attribution_findings_for(guardhook.ATTRIBUTION_HOOK)], ['error'])   # a weaker ban is a FAIL
        data = json.loads(attribution.DATA.read_text(encoding='utf-8'))
        data['_comment'] = 'older wording'
        copy.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual([f['level'] for f in self.attribution_findings_for(guardhook.ATTRIBUTION_HOOK)], ['warning'])   # same patterns: only outdated

    def attribution_findings_for(self, path):
        report = json.loads(self.cli('doctor').stdout)
        return [f for f in report['findings'] if f['path'] == path]

    def test_state_helper(self):
        for value, expected in ((False, True), ({'commit': '', 'pr': ''}, True), ({'commit': 'x', 'pr': ''}, False), ({}, False), (None, False), (True, False)):
            self.assertEqual(guardhook.attribution_state(value), expected, value)


class HookTests(unittest.TestCase):
    """The attribution part of `guard-commands.py`: refuses commands that WRITE such text, never commands that only read or quote it."""
    LIB = Path(__file__).resolve().parent.parent / 'library' / 'hooks' / 'guard-commands.py'

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='attr-hook-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.script = self.dir / 'guard-commands.py'
        shutil.copy2(self.LIB, self.script)
        shutil.copy2(attribution.DATA, self.dir / 'attribution-patterns.json')
        self.work = self.dir / 'work'
        self.work.mkdir()

    def run_hook(self, command, *args, env=None):
        payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': str(self.work)})
        return subprocess.run([sys.executable, str(self.script), '--mode', 'off', *args], input=payload, capture_output=True, text=True,
                              timeout=30, env={**os.environ, **(env or {})})

    def refused(self, command, **kwargs):
        result = self.run_hook(command, **kwargs)
        self.assertEqual(result.returncode, 2, (command, result.stderr))
        self.assertIn('stopkę, podpis albo trailer', result.stderr)
        return result

    def passes(self, command, **kwargs):
        result = self.run_hook(command, **kwargs)
        self.assertEqual(result.returncode, 0, (command, result.stderr))

    def test_writing_commands_with_a_footer_or_trailer_are_refused(self):
        (self.work / 'msg.txt').write_text('subject\n\n' + TRAILER + '\n', encoding='utf-8')
        for command in (
                f'git commit -m "feat: x\n\n{FOOTER}"',
                f"git commit -m 'feat: x' -m '{TRAILER}'",
                f"git commit -q -m \"$(cat <<'EOF'\nfeat: x\n\n{TRAILER}\nEOF\n)\"",
                'git commit -F msg.txt',
                'git -C . commit --file=msg.txt',
                f"git commit -F - <<'EOF'\nsubject\n\n{ROBOT}\nEOF",
                f'git tag -a v1 -m "{FOOTER}"',
                f'gh pr create --title t --body "{ROBOT}"',
                'gh pr create --title t --body-file msg.txt',
                f'gh pr comment 5 --body "{CURSOR}"',
                f'gh issue edit 3 -b "{FOOTER}"',
                f'gh api repos/o/r/issues -f body="{FOOTER}"',
                f'az repos pr create --title t --description "{ROBOT}"',
                f'az boards work-item update --id 5 --discussion "{TRAILER}"',
                f'glab mr create --description "{FOOTER}"',
                f'cd sub && git commit -m "{SESSION}"',
                f'timeout 5 git commit -m "{FOOTER}"'):
            with self.subTest(command[:50]):
                self.refused(command)

    def test_glued_option_and_escaped_newline_forms_are_refused(self):
        for command in (
                f'git commit --trailer="{TRAILER}"',
                f'git commit --message="{TRAILER}"',
                f'git commit "-m{TRAILER}"',
                "git commit -m $'subject\\n\\n" + TRAILER + "'",
                f'git commit --trailer "{TRAILER}"',
                f'acli jira workitem create --summary s --description "{FOOTER}"',
                f'acli jira workitem comment --key A-1 --body "{ROBOT}"',
                f'jira issue create -b "{FOOTER}"',
                f'glab mr note 5 --message "{FOOTER}"',
                f'glab mr accept 5 -m "{FOOTER}"',
                f'glab mr update 5 --description "{ROBOT}"'):
            with self.subTest(command[:60]):
                self.refused(command)

    def test_relative_files_follow_cd_and_dash_c_and_stdin_writers_see_the_pipeline(self):
        sub = self.work / 'sub'
        sub.mkdir()
        (sub / 'pr.md').write_text('body\n' + FOOTER + '\n', encoding='utf-8')
        (sub / 'msg.txt').write_text('subject\n\n' + TRAILER + '\n', encoding='utf-8')
        for command in ('cd sub && gh pr create --title t --body-file pr.md', 'git -C sub commit -F msg.txt',
                        f'printf "%s" "{TRAILER}" | git commit -F -', f'echo "{FOOTER}" | gh pr create --title t --body-file -'):
            with self.subTest(command[:60]):
                self.refused(command)
        self.passes(f'echo "{FOOTER}" | wc -l')
        self.passes('cd sub && git commit -m clean')

    def test_the_description_file_of_glab_is_read(self):
        (self.work / 'mr.md').write_text('body\n' + FOOTER + '\n', encoding='utf-8')
        (self.work / 'clean.md').write_text('body\n', encoding='utf-8')
        for command in ('glab mr create -t t --description-file mr.md', 'glab mr update 5 --description-file=mr.md',
                        f'echo "{FOOTER}" | glab mr create -t t --description-file -', f'echo "{FOOTER}" | glab mr update 5 --description-file=-',
                        'glab mr note 5 -m "$(cat mr.md)"'):
            with self.subTest(command[:60]):
                self.refused(command)
        self.passes('glab mr create -t t --description-file clean.md')

    def test_a_gitlab_quick_action_in_a_published_text_is_refused(self):
        (self.work / 'quick.md').write_text('Opis\n\n/merge\n', encoding='utf-8')
        (self.work / 'plain.md').write_text('Opis: ścieżka /srv/x i adres /api/v1\n/etc/hosts\na/merge\n', encoding='utf-8')
        for command in ('glab mr note 5 -m "/merge"', 'glab mr note 5 --message "$(cat quick.md)"', 'glab mr create -t t --description-file quick.md',
                        'glab mr update 5 --description-file=quick.md', 'glab mr note 5 -m "ok\n  /approve"', "glab mr note 5 -m $'x\\n/close'",
                        'glab mr update 5 --description "/ready"', 'glab issue note 3 -m "/close"', f'echo "/merge" | glab mr create -t t --description-file -',
                        'bash -c \'glab mr note 5 -m "/target_branch main"\'', 'glab mr note 5 -m "/Merge"',
                        "glab mr note 5 -m $'/merge'", "glab mr note 5 --message=$'/ready'"):
            with self.subTest(command[:70]):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, (command, result.stderr))
                self.assertIn('quick action', result.stderr)
        for command in ('glab mr create -t t --description-file plain.md', 'glab mr note 5 -m "zobacz /srv/x"', 'glab mr view 5', 'grep -n "/merge" notes.md',
                        'gh pr comment 5 --body "/merge"', 'git commit -m "docs: /merge"'):
            with self.subTest(command[:70]):
                self.passes(command)

    def test_a_body_file_under_the_home_directory_is_read(self):
        home = self.work / 'home'
        home.mkdir()
        (home / 'q.md').write_text('/merge\n', encoding='utf-8')
        result = self.run_hook('glab mr create -t t --description-file ~/q.md', env={'HOME': str(home), 'USERPROFILE': str(home)})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('quick action', result.stderr)

    def test_here_strings_variables_process_substitution_and_cat_are_refused(self):
        (self.work / 'pr.md').write_text('body\n' + FOOTER + '\n', encoding='utf-8')
        (self.work / 'm.txt').write_text('s\n\n' + TRAILER + '\n', encoding='utf-8')
        for command in (f'git commit -F - <<< "{TRAILER}"',
                        f'MSG="{TRAILER}"; git commit -m "$MSG"',
                        f'git commit -F <(echo "{TRAILER}")',
                        'gh pr create --title t --body "$(cat pr.md)"',
                        'MSG=$(cat m.txt); git commit -m "$MSG"',
                        'az repos pr create --title t --description @pr.md',
                        f'az boards work-item create --title t --type Task --description "{FOOTER}"',
                        f'true && git commit -m "{FOOTER}"', f'(git commit -m "{FOOTER}")', f'bash -c \'git commit -m "{FOOTER}"\'',
                        f'env A=1 git commit -m "{FOOTER}"', f'command git commit -m "{FOOTER}"', f'git commit --amend -m "{FOOTER}"'):
            with self.subTest(command[:60]):
                self.refused(command)

    def test_printed_text_wrappers_and_bot_trailers_are_refused(self):
        (self.work / 'sub').mkdir(exist_ok=True)
        (self.work / 'sub' / 'f.json').write_text(json.dumps({'body': FOOTER}), encoding='utf-8')
        (self.work / 'sub' / 'b.md').write_text(FOOTER + '\n', encoding='utf-8')
        bot = 'Co-Auth' + 'ored-By: Cla' + 'ude <cla' + 'ude[bot]@users.noreply.github.com>'
        for command in (f"git commit -m s -m \"$(echo '{TRAILER}')\"", f"git commit -m s -m \"$(printf '{TRAILER}')\"",
                        f"printf '{TRAILER}' > m.txt && git commit -F m.txt",
                        f'git commit -m "{bot}"', 'git commit -m "' + 'Assis' + 'ted-By: Cla' + 'ude Code"',
                        'env git -C sub commit -F b.md', 'timeout 5 git -C sub commit --file=b.md',
                        'gh api repos/o/r/issues -F body=@sub/b.md', 'gh api repos/o/r/issues --input sub/f.json'):
            with self.subTest(command[:60]):
                self.refused(command)

    def test_files_of_the_core_and_quoting_text_do_not_block_ordinary_work(self):
        for command in ('git add skillsync_workflow/data/attribution-patterns.json tests/test_skillsync_attribution.py && git commit -m "feat: patterns"',
                        'git commit -m "test: the footer patterns of the core"',
                        f'git log --oneline | grep -i "{FOOTER}"', f'cat notes.md | grep "{TRAILER}"',
                        f'echo "{FOOTER}" > /dev/null', f'git commit -m "docs: example" -- docs/x.md'):
            with self.subTest(command[:60]):
                self.passes(command)

    def test_the_message_names_the_pattern_and_the_class(self):
        result = self.refused(f'git commit -m "{TRAILER}"')
        self.assertIn('attr-coauthor-name (klasa B)', result.stderr)
        self.assertIn('Usuń te linie', result.stderr)

    def test_reading_and_quoting_commands_pass(self):
        for command in (
                'git commit -m "fix: parser"',
                f'git commit -m "fix: x\n\n{HUMAN}"',
                f'grep -rn "{FOOTER}" .',
                f'git log --grep="{TRAILER}"',
                f'echo "{FOOTER}"',
                f"python3 - <<'EOF'\nprint('{FOOTER}')\nEOF",
                f'gh pr list --search "{FOOTER}"',
                f'az repos pr list --query "[?contains(title, `{CURSOR}`)]"',
                f"cat > note.md <<'EOF'\n{FOOTER}\nEOF",
                'ls -la'):
            with self.subTest(command[:50]):
                self.passes(command)

    def test_no_exception_prefix_and_no_manifest_mode_switch_it_off(self):
        self.refused(f'SKILLSYNC_GUARD_EXCEPT=\'reason\' git commit -m "{FOOTER}"', env={'SKILLSYNC_GUARD_EXCEPT': 'reason'})
        self.assertEqual(self.run_hook(f'git commit -m "{FOOTER}"', '--tests', 'off').returncode, 2)

    def test_commits_without_text_in_the_command_pass(self):
        for command in ('git commit', 'git commit --amend --no-edit', 'git commit -a', 'git commit --allow-empty -C HEAD',
                        'skillsync checkpoint .', 'skillsync pr-run .', 'git -C repo commit', 'git status && git commit'):
            with self.subTest(command):
                self.passes(command)

    def test_missing_or_broken_data_is_fail_open(self):
        (self.dir / 'attribution-patterns.json').write_text('{not json', encoding='utf-8')
        self.passes(f'git commit -m "{FOOTER}"')
        (self.dir / 'attribution-patterns.json').unlink()
        self.passes(f'git commit -m "{FOOTER}"')

    def test_the_hook_reads_the_enforce_switch_of_its_copy(self):
        data = json.loads(attribution.DATA.read_text(encoding='utf-8'))
        data['enforce'] = ['A']
        (self.dir / 'attribution-patterns.json').write_text(json.dumps(data), encoding='utf-8')
        self.passes(f'git commit -m "{TRAILER}"')
        self.refused(f'git commit -m "{FOOTER}"')


class LeakguardTests(LeakTestCase):
    def test_text_mode_finds_footer_and_trailer(self):
        repo = self.make_repo()
        for text in (FOOTER, ROBOT, TRAILER, CURSOR, SESSION):
            with self.subTest(text[:12]):
                report = self.check(repo, texts=[('pr-body', 'Opis zmiany\n\n' + text)], mode='text')
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual({f['category'] for f in report['findings']} & {'attribution'}, {'attribution'})

    def test_text_mode_passes_clean_text_and_human_trailer(self):
        repo = self.make_repo()
        self.assertEqual(self.check(repo, texts=[('pr-body', 'Opis\n\n' + HUMAN)], mode='text')['status'], 'PASS')

    def test_new_commit_is_refused_in_range_and_staged_files_in_every_class(self):
        repo = self.make_repo()
        base = commit_file(repo, 'a.md', 'x\n', 'base')
        head = commit_file(repo, 'b.md', 'y\n', 'feat: b\n\n' + TRAILER)
        report = self.check(repo, mode='range', rev_range=f'{base}..{head}')
        self.assertEqual(report['status'], 'FAIL')
        for target_kind in ('core', 'org', 'personal'):
            with self.subTest(target_kind):
                self.assertEqual(self.check(repo, mode='range', rev_range=f'{base}..{head}', target_kind=target_kind)['status'], 'FAIL')
        for rel in ('docs/x.md', 'skills/x/SKILL.md', 'CHANGELOG.md', 'tests/t.py'):
            with self.subTest(rel):
                tree = self.tree({rel: 'note\n' + FOOTER + '\n'})
                self.assertEqual(self.check(tree)['status'], 'FAIL')

    def test_trailer_allow_does_not_open_the_ai_trailer(self):
        repo = self.make_repo()
        base = commit_file(repo, 'a.md', 'x\n', 'base')
        head = commit_file(repo, 'b.md', 'y\n', 'feat\n\n' + TRAILER)
        report = self.check(repo, mode='range', rev_range=f'{base}..{head}', trailer_allow=[ADDR])
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('attribution', {f['category'] for f in report['findings']})

    def test_history_ignores_old_trailers_but_range_does_not_hide_a_new_commit(self):
        repo = self.make_repo()
        old = commit_file(repo, 'a.md', 'x\n', 'old\n\n' + TRAILER)
        self.assertEqual(self.check(repo, mode='history')['status'], 'PASS')
        new = commit_file(repo, 'b.md', 'y\n', 'new\n\n' + TRAILER)
        self.assertEqual(self.check(repo, mode='history')['status'], 'PASS')
        self.assertEqual(self.check(repo, mode='range', rev_range=f'{old}..{new}')['status'], 'FAIL')
        self.assertEqual(self.check(repo, mode='range', rev_range=f'{old}~0')['status'], 'FAIL')

    def test_history_still_finds_other_leaks_in_a_trailer(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', 'old\n\nCo-Auth' + 'ored-By: Cla' + 'ude ' + 'Brenk' + 'or <' + ADDR + '>')
        report = self.check(repo, [{'term': 'Brenk' + 'or', 'kind': 'org'}], mode='history')
        self.assertEqual(report['status'], 'FAIL')

    def test_builtin_patterns_carry_the_category_and_the_old_allow_is_gone(self):
        patterns = leakguard.load_patterns()
        self.assertEqual({r.category for r in patterns.patterns if r.id.startswith('attr-')}, {'attribution'})
        self.assertNotIn('trailer-noreply', {r.id for r in patterns.allow})


if __name__ == '__main__':
    unittest.main()
