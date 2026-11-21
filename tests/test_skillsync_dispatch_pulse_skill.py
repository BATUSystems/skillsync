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


if __name__ == '__main__':
    unittest.main()
