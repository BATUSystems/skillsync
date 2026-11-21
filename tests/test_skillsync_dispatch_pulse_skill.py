"""CLAUDE-5 (P3): the pulse of the dispatcher in the skill `dispatch`: the closed list of actions, the prompt of the loop, the exception of the rule about polling."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'library' / 'skills' / 'dispatch'   # relative to the repository: no environment variable (a skipped test proves nothing)
PULSE = SKILL / 'references' / 'pulse.md'


def read(path):
    return Path(path).read_text(encoding='utf-8')


def section(text, title):
    """The text of the `## title` section (up to the next `## `)."""
    match = re.search(rf'^## {re.escape(title)}\n(.*?)(?=^## |\Z)', text, re.S | re.M)
    assert match is not None, f'no section {title!r}'
    return match.group(1)


class PulseReference(unittest.TestCase):
    def setUp(self):
        self.text = read(PULSE)

    def test_the_sections_are_there(self):
        for title in ('Krok 0: bramka modelu', 'Start i życie pętli', 'Prompt pętli', 'Tick', 'Ustalenia i akcje', 'Czego tick nie robi', 'Noop i interwał',
                      'Plik księgowości (sidecar)', 'Uprawnienia bez nadzoru'):
            with self.subTest(title=title):
                self.assertTrue(section(self.text, title).strip())

    def test_the_prompt_of_the_loop_points_to_the_file_and_never_to_the_skill(self):
        block = re.search(r'```text\n(.*?)\n```', section(self.text, 'Prompt pętli'), re.S)
        self.assertIsNotNone(block, 'the prompt of the loop is a ```text block')
        prompt = block.group(1)
        self.assertIn('references/pulse.md', prompt)
        self.assertIn('dane', prompt.lower())
        self.assertFalse(prompt.lstrip().startswith('/'), 'the prompt of the loop does not start with a slash command')
        self.assertNotIn('/dispatch', prompt)
        self.assertNotRegex(prompt, r'(?i)narzędz\w+ Skill|Skill tool|\bSkill\(')

    def test_the_forbidden_actions_are_listed_by_name(self):
        forbidden = section(self.text, 'Czego tick nie robi')
        for name in ('LEASE GRANT', 'LEASE RELEASE', 'PUSH GRANT', 'GO', 'CP', 'READY', 'AskUserQuestion', 'testów'):
            with self.subTest(name=name):
                self.assertIn(name, forbidden)

    def test_the_allowed_actions_are_a_closed_list_of_three(self):
        actions = section(self.text, 'Ustalenia i akcje')
        self.assertIn('lista zamknięta', actions)
        for finding in ('idle-card', 'user-wait', 'lease-overdue', 'stale-state', 'age-unknown'):
            with self.subTest(finding=finding):
                self.assertIn(finding, actions)
        self.assertIn('STATUS?', actions)
        self.assertIn('PushNotification', actions)

    def test_every_tick_reads_the_state_through_the_cli_and_the_time_from_date(self):
        tick = section(self.text, 'Tick')
        self.assertIn('skillsync dispatch', tick)
        self.assertIn('--pulse', tick)
        self.assertRegex(tick, r'\bdate\b')

    def test_the_model_gate_is_repeated_because_the_tick_reads_this_file(self):
        gate = section(self.text, 'Krok 0: bramka modelu')
        self.assertIn('Opus 5.5', gate)
        self.assertIn('Fable 5.1', gate)

    def test_the_sidecar_is_a_json_file_beside_the_state_and_its_example_parses(self):
        text = section(self.text, 'Plik księgowości (sidecar)')
        self.assertIn('.pulse.json', text)
        block = re.search(r'```json\n(.*?)\n```', text, re.S)
        self.assertIsNotNone(block)
        data = json.loads(block.group(1))
        for key in ('version', 'run_id', 'armed_at', 'last_tick', 'interval_minutes', 'last_findings', 'notified', 'probed'):
            self.assertIn(key, data)

    def test_the_permissions_for_an_unattended_tick_are_named(self):
        text = section(self.text, 'Uprawnienia bez nadzoru')
        for name in ('skillsync dispatch', 'date', 'SendMessage', 'PushNotification', '.pulse.json'):
            with self.subTest(name=name):
                self.assertIn(name, text)


class PollingException(unittest.TestCase):
    """The rule "do not poll the cards in a loop" is kept and gets ONE named exception in every place that repeats it."""

    def test_the_rule_stays_and_names_the_exception_in_the_skill(self):
        text = read(SKILL / 'SKILL.md')
        line = next(item for item in text.split('\n') if item.startswith('- **Brak pollingu**'))
        self.assertIn('Nie odpytuj kart w pętli', line)
        self.assertIn('Jedyny wyjątek', line)
        self.assertIn('references/pulse.md', line)
        self.assertIn('STATUS?', line)

    def test_the_adapters_and_the_loop_mention_the_pulse_where_they_forbid_polling(self):
        self.assertRegex(read(SKILL / 'references' / 'claude.md'), r'nie odpytuj `ListAgents` w pętli \(wyjątek: tick \[pulsu\]\(pulse\.md\)')
        self.assertIn('pulse.md', read(SKILL / 'references' / 'codex.md'))
        loop = read(SKILL / 'references' / 'loop.md')
        self.assertIn('pulse.md', loop)
        self.assertIn('karty nie ponaglasz', loop)

    def test_the_config_and_the_state_describe_the_pulse(self):
        config = read(SKILL / 'references' / 'config.md')
        for key in ('pulse:', 'enabled: false', 'interval_minutes', 'idle_card_minutes', 'user_wait_notify_minutes', 'lease_overdue_minutes'):
            with self.subTest(key=key):
                self.assertIn(key, config)
        state = read(SKILL / 'references' / 'state.md')
        self.assertIn('.pulse.json', state)
        self.assertIn('ISO z offsetem', state)

    def test_every_new_reference_of_the_pulse_resolves_to_a_file(self):
        for name in ('SKILL.md', 'references/claude.md', 'references/codex.md', 'references/loop.md', 'references/state.md', 'references/config.md'):
            text = read(SKILL / name)
            for target in re.findall(r'\]\(((?:references/)?pulse\.md)(?:#[^)]*)?\)', text):
                with self.subTest(file=name, target=target):
                    self.assertTrue((SKILL / name).parent.joinpath(target).resolve().is_file() or (SKILL / target).is_file())


class PulseConsistency(unittest.TestCase):
    """What the reference says matches the code (`dispatch_state._pulse`) and the places that must start, end and renew the pulse."""

    def setUp(self):
        self.text = read(PULSE)

    def test_the_kinds_of_the_table_are_the_kinds_of_the_code(self):
        table = section(self.text, 'Ustalenia i akcje')
        documented = set(re.findall(r'^\| `([a-z-]+)` \|', table, re.M))
        code = set(re.findall(r"'kind': '([a-z-]+)'", read(ROOT / 'skillsync_workflow' / 'dispatch_state.py')))
        self.assertEqual(documented, code)

    def test_the_skill_starts_ends_and_renews_the_pulse_from_its_entry_points(self):
        skill = read(SKILL / 'SKILL.md')
        start = section(skill, 'Start (`/dispatch <cel>`)')
        self.assertIn('references/pulse.md', start)
        self.assertIn('dispatch.pulse.enabled', start)
        drain = next(line for line in skill.split('\n') if line.startswith('- `/dispatch drain`'))
        self.assertIn('pulse.md', skill[skill.index(drain):skill.index(drain) + 400])
        state = section(read(SKILL / 'references' / 'state.md'), 'Wznowienie')
        self.assertIn('pulse.md', state)
        self.assertIn('/loop', state)

    def test_a_tick_ends_the_loop_for_a_finished_dispatch_and_survives_an_unreadable_state(self):
        tick = section(self.text, 'Tick')
        self.assertIn('dispatches[].status', tick)
        self.assertIn('stop: true', tick)
        self.assertIn('state-unreadable', tick)
        self.assertIn('puls się NIE kończy', tick)

    def test_the_run_id_is_checked_against_the_pattern_before_any_path_is_built(self):
        self.assertIn('[A-Za-z0-9][A-Za-z0-9._:-]{0,79}', self.text)
        self.assertIn('bez zapisu sidecara', section(self.text, 'Tick'))

    def test_the_way_the_sidecar_is_written_agrees_with_the_permissions(self):
        tick = section(self.text, 'Tick')
        permissions = section(self.text, 'Uprawnienia bez nadzoru')
        self.assertIn('narzędziem zapisu', tick)
        self.assertNotIn('rename', tick)
        self.assertNotIn('plik tymczasowy', tick)
        self.assertIn('bez `mv` i plików tymczasowych', permissions)

    def test_the_probe_needs_a_session_id_from_the_state_file_and_never_lists_the_agents(self):
        actions = section(self.text, 'Ustalenia i akcje')
        self.assertIn('session_id', actions)
        self.assertIn('bez `ListAgents`', actions)
        self.assertIn('BEZ sondy', actions)

    def test_the_notifications_carry_only_the_whitelisted_values(self):
        actions = section(self.text, 'Ustalenia i akcje')
        self.assertIn('wyłącznie `kind`, `subject`, `ageMinutes` i `run-id`', actions)

    def test_the_markers_are_set_after_a_successful_action(self):
        self.assertIn('PO udanej akcji', section(self.text, 'Tick'))

    def test_the_prompt_forbids_choice_windows_and_the_path_is_absolute(self):
        prompt = section(self.text, 'Prompt pętli')
        self.assertIn('Nie otwieraj okien wyboru', prompt)
        self.assertIn('ścieżka bezwzględna', prompt)


if __name__ == '__main__':
    unittest.main()
