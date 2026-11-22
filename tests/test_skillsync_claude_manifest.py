"""Manifest key `claude:` (CLAUDE-7, step F1.1).

Group A (`ClaudeReadCharacterization`): characterizes how the three readers of the key behave TODAY. Green on the code before the
refactor; it pins the behavior that must stay (K8) while F1.2 replaces the readers with one function.
  * `cli.claude_enabled`           : `claude: false` or `claude: {enabled: false}` (identity with False) switches off; everything else is on.
  * `guardhook.doctor_findings`    : the same identity reading (observed: with the key off, no finding for `.claude/settings.local.json`).
  * `quality.doctor` (`enabled()`) : a TRUTHINESS reading of `enabled`: `setting is not False and (not dict or setting.get('enabled', True))`.
    Differs from the other two for a falsy non-bool `enabled` (`0`, `null`, `''`, `[]`): quality switches Claude off, cli and guardhook
    keep it on. The canonical semantics of the plan (only `false` switches off) removes this difference at F1.2:
    `test_quality_truthiness_difference_today` is the one test in this file that F1.2 must replace on purpose (contract change).

Group B (`ClaudeManifestModule`, `ClaudeManifestDoctor`): the new module `skillsync_workflow.claude_manifest`; RED until it exists.
Interface PROPOSED by the test writer (the plan names only `KNOWN_KEYS` and `enabled`; the rest is a decision to be confirmed):
  KNOWN_KEYS                      -> ('enabled', 'statusline', 'output_styles')
  enabled(manifest)               -> bool; False only for `claude: false` or `claude: {enabled: false}` (identity); anything else True
  statusline(manifest)            -> bool; opt-in: True only for `claude: {statusline: true}`; non-bool = False; False when Claude is off
  output_styles(manifest)         -> bool; same as `statusline` for `output_styles`
  findings(manifest)              -> [(severity, message)]; `error` for a non-bool `enabled`/`statusline`/`output_styles`, `warning` for an unknown
                                     key; messages start with the key path (`claude.statusline`) and never contain the offending value;
                                     an absent or untouched key yields `[]` (K8).
`quality.doctor` adds these as findings with path `.github/.skillsync.yaml` (as it does for `delivery`).
"""
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skillsync_workflow import cli, guardhook, quality
from tests.test_skillsync_workflow import WorkflowFixture

SECRET_VALUE = 'zzz-value-that-must-not-be-echoed'
MANIFEST_PATH = '.github/.skillsync.yaml'

# (label, manifest, expected result of the identity reading used by cli and guardhook)
CASES = [
    ('no key', {}, True),
    ('false', {'claude': False}, False),
    ('true', {'claude': True}, True),
    ('empty mapping', {'claude': {}}, True),
    ('enabled false', {'claude': {'enabled': False}}, False),
    ('enabled true', {'claude': {'enabled': True}}, True),
    ('enabled string', {'claude': {'enabled': 'maybe'}}, True),
    ('enabled string false', {'claude': {'enabled': 'false'}}, True),   # only the boolean False switches off
    ('null', {'claude': None}, True),
    ('zero', {'claude': 0}, True),
    ('number', {'claude': 1}, True),
    ('string', {'claude': 'off'}, True),
    ('empty list', {'claude': []}, True),
    ('list', {'claude': ['x']}, True),
    ('mapping, other key only', {'claude': {'statusline': True}}, True),
    ('enabled false with a new key', {'claude': {'enabled': False, 'statusline': True}}, False),
]
FALSY_NON_BOOL = [('enabled zero', {'claude': {'enabled': 0}}), ('enabled null', {'claude': {'enabled': None}}),
                  ('enabled empty string', {'claude': {'enabled': ''}}), ('enabled empty list', {'claude': {'enabled': []}})]


def load_module():
    """Import of the module under test; fails readably (an assertion, not a collection error) while it does not exist."""
    try:
        return importlib.import_module('skillsync_workflow.claude_manifest')
    except ImportError as error:
        raise AssertionError(f'module skillsync_workflow.claude_manifest does not exist yet: {error}') from None


class IsolatedHome(WorkflowFixture):
    """The fixture project plus a HOME inside the temporary directory (the real `~/.claude/settings.json` must not change a result)."""

    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        self.home.mkdir()
        patch = mock.patch.dict(os.environ, {'HOME': str(self.home), 'USERPROFILE': str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, claude=None, drop=False, **extra):
        self.manifest.pop('claude', None)
        if not drop:
            self.manifest['claude'] = claude
        self.manifest.update(extra)
        self.save()

    def install_skill(self):
        skill = self.library / 'skills' / 'feature'
        (skill / 'references').mkdir(parents=True, exist_ok=True)
        (skill / 'SKILL.md').write_text('---\nname: feature\ndescription: t\n---\n', encoding='utf-8')
        self.manifest['skills'] = ['feature']

    def doctor_findings(self):
        return quality.doctor(self.root, self.manifest, self.layerset())['findings']

    def claude_skill_reported(self, manifest_claude):
        """True when `quality.doctor` still expects the Claude skill host (`.claude/skills/feature` missing = a finding)."""
        self.install_skill()
        self.manifest['claude'] = manifest_claude
        self.save()
        return any(f['path'].endswith('.claude/skills/feature') for f in self.doctor_findings())

    def guard_reports_claude(self, manifest):
        """True when the guard hook doctor evaluates Claude settings (it returns early for a disabled key)."""
        found = guardhook.doctor_findings(self.root, manifest, [])
        return any(path == guardhook.SETTINGS for _level, path, _reason in found)


class ClaudeReadCharacterization(IsolatedHome):
    """Group A: green on the current code (the readers before F1.2)."""

    def test_cli_claude_enabled(self):
        for label, manifest, expected in CASES:
            with self.subTest(label):
                self.assertIs(cli.claude_enabled(manifest), expected)

    def test_cli_reading_leaves_the_manifest_untouched(self):
        manifest = {'claude': {'enabled': True, 'statusline': True}}
        cli.claude_enabled(manifest)
        self.assertEqual(manifest, {'claude': {'enabled': True, 'statusline': True}})

    def test_guardhook_reading(self):
        for label, manifest, expected in CASES:
            with self.subTest(label):
                self.assertIs(self.guard_reports_claude(manifest), expected)

    def test_guardhook_keeps_claude_on_for_falsy_non_bool_enabled(self):
        for label, manifest in FALSY_NON_BOOL:
            with self.subTest(label):
                self.assertTrue(self.guard_reports_claude(manifest))

    def test_quality_reading_matches_identity_reading_for_bool_enabled(self):
        for label, manifest, expected in CASES:
            claude = manifest.get('claude')
            if isinstance(claude, dict) and 'enabled' in claude and not isinstance(claude['enabled'], bool):
                continue   # a non-bool `enabled` is characterized separately
            if 'claude' not in manifest:
                continue   # covered by the missing key test
            with self.subTest(label):
                self.assertIs(self.claude_skill_reported(claude), expected)

    def test_quality_missing_key_equals_claude_on(self):
        self.install_skill()
        self.put(drop=True)
        self.assertTrue(any(f['path'].endswith('.claude/skills/feature') for f in self.doctor_findings()))

    def test_quality_non_bool_enabled_strings_keep_claude_on(self):
        for value in ('maybe', 'false'):
            with self.subTest(value):
                self.assertTrue(self.claude_skill_reported({'enabled': value}))

    def test_quality_reads_falsy_non_bool_enabled_like_the_others(self):
        """Canonical semantics (F1.2): only `false` switches Claude off; a falsy non-bool `enabled` keeps it on in cli, guardhook and doctor."""
        for label, manifest in FALSY_NON_BOOL:
            with self.subTest(label):
                self.assertTrue(cli.claude_enabled(manifest))
                self.assertTrue(self.claude_skill_reported(manifest['claude']))


class ClaudeManifestModule(unittest.TestCase):
    """Group B: the pure functions of the new module (RED until `skillsync_workflow.claude_manifest` exists)."""

    def test_known_keys(self):
        self.assertEqual(set(load_module().KNOWN_KEYS), {'enabled', 'statusline', 'output_styles'})

    def test_enabled_canonical_semantics(self):
        module = load_module()
        for label, manifest, expected in CASES:
            with self.subTest(label):
                self.assertIs(module.enabled(manifest), expected)

    def test_enabled_does_not_switch_off_for_falsy_non_bool(self):
        module = load_module()
        for label, manifest in FALSY_NON_BOOL:
            with self.subTest(label):
                self.assertIs(module.enabled(manifest), True)

    def test_enabled_agrees_with_cli_reader(self):
        module = load_module()
        for label, manifest, _expected in CASES + [(label, manifest, None) for label, manifest in FALSY_NON_BOOL]:
            with self.subTest(label):
                self.assertIs(module.enabled(manifest), cli.claude_enabled(manifest))

    def test_new_switches_are_opt_in(self):
        module = load_module()
        for name in ('statusline', 'output_styles'):
            function = getattr(module, name)
            for label, manifest in [('no key', {}), ('claude true', {'claude': True}), ('empty', {'claude': {}}), ('null', {'claude': None}),
                                    ('other key', {'claude': {'enabled': True}}), ('explicit false', {'claude': {name: False}})]:
                with self.subTest(f'{name}: {label}'):
                    self.assertIs(function(manifest), False)
            with self.subTest(f'{name}: true'):
                self.assertIs(function({'claude': {name: True}}), True)

    def test_new_switches_are_independent(self):
        module = load_module()
        self.assertEqual((module.statusline({'claude': {'statusline': True}}), module.output_styles({'claude': {'statusline': True}})), (True, False))
        self.assertEqual((module.statusline({'claude': {'output_styles': True}}), module.output_styles({'claude': {'output_styles': True}})), (False, True))

    def test_non_bool_new_switch_is_not_on(self):
        module = load_module()
        for name in ('statusline', 'output_styles'):
            for value in ('true', 'yes', 1, [], {}, None):
                with self.subTest(f'{name}={value!r}'):
                    self.assertIs(getattr(module, name)({'claude': {name: value}}), False)

    def test_new_switches_are_off_when_claude_is_off(self):
        module = load_module()
        for manifest in ({'claude': False}, {'claude': {'enabled': False, 'statusline': True, 'output_styles': True}}):
            with self.subTest(manifest):
                self.assertIs(module.statusline(manifest), False)
                self.assertIs(module.output_styles(manifest), False)

    def test_findings_absent_for_valid_or_missing_keys(self):
        module = load_module()
        for manifest in ({}, {'claude': True}, {'claude': False}, {'claude': None}, {'claude': {}}, {'claude': {'enabled': False}},
                         {'claude': {'enabled': True, 'statusline': True, 'output_styles': False}}):
            with self.subTest(manifest):
                self.assertEqual(list(module.findings(manifest)), [])

    def test_findings_error_for_non_bool_value(self):
        module = load_module()
        for key in ('enabled', 'statusline', 'output_styles'):
            for value in ('yes', 1, [], SECRET_VALUE, None):
                with self.subTest(f'{key}={value!r}'):
                    found = list(module.findings({'claude': {key: value}}))
                    self.assertEqual([severity for severity, _ in found], ['error'])
                    message = found[0][1]
                    self.assertTrue(message.startswith(f'claude.{key}'), message)
                    self.assertNotIn(SECRET_VALUE, message)

    def test_findings_one_error_per_bad_key(self):
        found = list(load_module().findings({'claude': {'statusline': 'x', 'output_styles': 'y', 'enabled': True}}))
        self.assertEqual(sorted(message.split(':')[0].split()[0] for _s, message in found), ['claude.output_styles', 'claude.statusline'])
        self.assertEqual({severity for severity, _ in found}, {'error'})

    def test_findings_warning_for_unknown_key_without_value_echo(self):
        found = list(load_module().findings({'claude': {'statusline': True, 'colour_scheme': SECRET_VALUE}}))
        self.assertEqual([severity for severity, _ in found], ['warning'])
        self.assertIn('colour_scheme', found[0][1])
        self.assertNotIn(SECRET_VALUE, found[0][1])

    def test_findings_never_mutate_the_manifest(self):
        manifest = {'claude': {'statusline': 'x', 'other': SECRET_VALUE}}
        load_module().findings(manifest)
        self.assertEqual(manifest, {'claude': {'statusline': 'x', 'other': SECRET_VALUE}})


class ClaudeManifestDoctor(IsolatedHome):
    """Group B: integration with `quality.doctor` (RED until the module exists and doctor calls it)."""

    def claude_findings(self):
        return [f for f in self.doctor_findings() if f['reason'].startswith('claude')]

    def test_non_bool_values_are_manifest_errors(self):
        for key in ('enabled', 'statusline', 'output_styles'):
            with self.subTest(key):
                self.put({key: 'yes'})
                found = self.claude_findings()
                self.assertEqual([(f['level'], f['path']) for f in found], [('error', MANIFEST_PATH)], found)
                self.assertTrue(found[0]['reason'].startswith(f'claude.{key}'))

    def test_unknown_key_is_a_warning_without_echo(self):
        self.put({'colour_scheme': SECRET_VALUE})
        found = self.claude_findings()
        self.assertEqual([(f['level'], f['path']) for f in found], [('warning', MANIFEST_PATH)], found)
        self.assertNotIn(SECRET_VALUE, found[0]['reason'])

    def test_valid_new_keys_add_no_error(self):
        self.put({'statusline': True, 'output_styles': True})
        self.assertEqual([f for f in self.claude_findings() if f['level'] == 'error'], [])

    def test_one_canonical_reader_in_doctor(self):
        """`{enabled: 0}` is an error of the manifest and leaves Claude ON in doctor (the canonical `enabled`)."""
        self.assertTrue(self.claude_skill_reported({'enabled': 0}))
        self.assertEqual([f['level'] for f in self.claude_findings()], ['error'])

    def test_control_for_the_k8_check(self):
        """Proof that the K8 check below can fail: a bad key does produce a `claude*` finding."""
        self.put({'statusline': 'x'})
        self.assertTrue(self.claude_findings())

    def test_k8_no_new_keys_no_doctor_entry(self):
        """K8: without the new keys doctor adds neither a warning nor an info about `claude:`, and the findings equal the baseline."""
        self.install_skill()
        self.put(drop=True)
        baseline = self.doctor_findings()
        for label, claude in [('empty', {}), ('true', True), ('enabled true', {'enabled': True}), ('null', None)]:
            with self.subTest(label):
                self.put(claude)
                self.assertEqual(self.doctor_findings(), baseline)
                self.assertEqual(self.claude_findings(), [])
        for label, claude in [('false', False), ('enabled false', {'enabled': False})]:
            with self.subTest(label):
                self.put(claude)
                self.assertEqual(self.claude_findings(), [])


if __name__ == '__main__':
    unittest.main()
