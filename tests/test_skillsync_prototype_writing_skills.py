"""Strażnik fraz skilla `prototype` dla markera zapisu (PROTO-READY, K13) i parsowanie przykładów `prototype writing`.

Biblioteka z SKILLSYNC_TEST_LIBRARY; dokumentacja z katalogu `docs/` repozytorium.
"""
import argparse
import io
import os
import re
import shlex
import unittest
from contextlib import redirect_stderr
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the PROTO-READY skill text')
REPO = Path(__file__).resolve().parent.parent

SKILL = 'skills/prototype/SKILL.md'
SKILLSYNC = 'skills/skillsync/SKILL.md'
PLAN_DETAILS = 'skills/feature/references/plan-details.md'
DECISIONS = 'skills/prototype/references/decisions.md'

REQUIRED = [
    ('skill-start', SKILL, r'`skillsync prototype writing <projekt> <slug> start` PRZED edycją ekranów'),
    ('skill-done', SKILL, r'`skillsync prototype writing <projekt> <slug> done`\. DOPIERO POTEM zgłoś „prototyp gotowy”'),
    ('skill-iterate-przed-start', SKILL, r'W rundzie po przeglądzie najpierw `iterate` \(zamrożenie rundy\), dopiero potem `writing start`'),
    ('skill-odnowienie', SKILL, r'Przy każdej większej partii zmian wykonaj `start` ponownie \(odnawia czas\) albo od razu `start --ttl 1800`'),
    ('skill-ttl', SKILL, r'TTL 60–3600 s, domyślnie 900 s'),
    ('skill-ids-przed-done', SKILL, r'Po zakończeniu pracy: `ids --check`, sprawdzenie, że slug jest na stronie głównej `serve`, `skillsync prototype writing'),
    ('skill-lock-timeout', SKILL, r'`error: lock_timeout` \(zajęta blokada prototypu, np\. trwa `iterate`\) ponów komendę'),
    ('skill-needs-migration', SKILL, r'`error: needs_migration` \(prototyp w starym formacie\) = najpierw `skillsync prototype migrate <projekt> <slug> --dry-run`'),
    ('skill-procedura-rundy-start', SKILL, r'3\. `skillsync prototype writing <projekt> <slug> start` \(odnawiaj przy większych partiach\)'),
    ('skill-procedura-rundy-done', SKILL, r'4\. Dopiero potem zmiany HTML, ponownie `ids --check`, sprawdzenie, że slug jest na stronie głównej `serve`, i `skillsync prototype writing <projekt> <slug> done`'),
    ('dec-writing-start', DECISIONS, r'3\. `writing <projekt> <slug> start` \(marker „agent pisze”'),
    ('dec-writing-done', DECISIONS, r'5\. ponownie `ids <projekt> <slug> --check`, sprawdzenie, że slug jest na stronie głównej `serve`, i `writing <projekt> <slug> done`'),
    ('skill-sygnal', SKILL, r'Marker to sygnał koordynacji, nie autoryzacja'),
    ('skill-dwóch-agentow', SKILL, r'wygrywa ostatni `start`, a pierwszy `done` zdejmuje marker dla obu'),
    ('skill-accept-zakaz', SKILL, r'Agent nadal nigdy nie uruchamia `accept`'),
    ('skill-wait-writing', SKILL, r'`gate` zwraca powód `writing`, a `wait` czeka dalej'),
    ('skillsync-komenda', SKILLSYNC, r'`writing <projekt> <slug> start\|done\|status \[--ttl <s>\] \[--by <login>\]`'),
    ('skillsync-kody', SKILLSYNC, r'`BLOCKED` z `error`: `needs_migration` \(prototyp bez `comments\.json`\), `lock_timeout`.*`invalid_args`'),
    ('skillsync-accept', SKILLSYNC, r'`accept` kończy się `BLOCKED` `writing_in_progress`'),
    ('pd-writing', PLAN_DETAILS, r'`writing` = agent jeszcze pisze ekrany prototypu'),
]


def norm(text):
    return re.sub(r'\s+', ' ', text)


def read(rel):
    return norm((Path(LIB) / rel).read_text(encoding='utf-8'))


def writing_examples():
    """Przykłady `skillsync prototype writing …` w skillu i dokumentacji (bez opisów składni z `|`, `[…]`)."""
    files = [Path(LIB) / SKILL, REPO / 'docs' / 'prototypes.md', REPO / 'docs' / 'cli.md']
    out = []
    for path in files:
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for match in re.finditer(r'skillsync prototype writing[^`\n]*', line):
                text = re.sub(r'\s+#.*$', '', match.group(0)).strip()
                if re.search(r'[\[\]|…]', text) or text.endswith('writing'):
                    continue
                text = re.sub(r'<projekt>', '.', text)
                text = re.sub(r'<slug>', 'plan-zajec', text)
                text = re.sub(r'<s>', '600', text)
                text = re.sub(r'<login>', 'agent', text)
                if '<' in text or '>' in text or not text.split()[-1] in ('start', 'done', 'status') and '--' not in text:
                    continue
                out.append((str(path), number, text))
    return out


def decision_end(text):
    return text.index('6. **Podsumowanie dla użytkownika**')


def parser():
    from skillsync_workflow import prototype
    root = argparse.ArgumentParser(prog='skillsync')
    prototype.register(root.add_subparsers(dest='command'))
    return root


@SKIP
class WritingSkillPhraseTests(unittest.TestCase):
    def test_required_phrases_present(self):
        missing = [pid for pid, rel, rx in REQUIRED if not re.search(rx, read(rel))]
        self.assertEqual(missing, [])

    def test_each_phrase_can_fail(self):
        """Mutacja treści: zmiana słowa kluczowego w znalezionej frazie (PRZED→PO, start→stop, done→skip) łamie dopasowanie."""
        swaps = (('PRZED', 'PO'), ('start', 'stop'), ('done', 'skip'), ('ponów', 'pomiń'), ('iterate', 'iterat'), ('writing', 'wirting'))
        for pid, rel, rx in REQUIRED:
            with self.subTest(phrase=pid):
                text = read(rel)
                match = re.search(rx, text)
                self.assertIsNotNone(match)
                found = match.group(0)
                words = re.findall(r'[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{5,}', found)
                candidates = [(o, n) for o, n in swaps if o in found] + [(w, 'XXXXX') for w in sorted(set(words), key=len, reverse=True)]
                broken = [(o, n) for o, n in candidates
                          if re.search(rx, text[:match.start()] + found.replace(o, n) + text[match.end():]) is None]
                self.assertTrue(broken, f'{pid}: no single-word mutation breaks the phrase')

    def test_order_of_steps(self):
        """Kolejność: `iterate` przed `writing start`; `ids --check` przed `writing done`; `done` przed „prototyp gotowy”."""
        text = read(SKILL)
        section = text[text.index('## Zapis ekranów'):text.index('## Pliki prototypu')]
        self.assertLess(section.index('najpierw `iterate`'), section.index('dopiero potem `writing start`'))
        self.assertLess(section.index('PRZED edycją ekranów'), section.index('Po zakończeniu pracy: `ids --check`'))
        self.assertLess(section.index('`ids --check`'), section.index('`skillsync prototype writing <projekt> <slug> done`'))
        self.assertLess(section.index('<slug> done`'), section.index('zgłoś „prototyp gotowy”'))
        creation = text[text.index('## Tworzenie prototypu w repo'):text.index('## Zapis ekranów')]
        self.assertLess(creation.index('writing <projekt> <slug> start'), creation.index('zastąp ekran przykładowy'))
        self.assertLess(creation.index('ids <projekt> <slug> --check'), creation.index('writing <projekt> <slug> done'))

    def test_round_procedure_knows_the_marker(self):
        """Procedura po rundzie (SKILL i decisions.md): iterate → writing start → zmiany HTML → ids --check → writing done."""
        skill = read(SKILL)
        part = skill[skill.index('Procedura po rundzie'):skill.index('Przed każdą zmianą HTML')]
        order = ['prototype iterate <projekt> <slug>', 'writing <projekt> <slug> start', 'zmiany HTML', 'ponownie `ids --check`', 'writing <projekt> <slug> done']
        positions = [part.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        decisions = read(DECISIONS)
        part = decisions[decisions.index('**Zmiany HTML.**'):decision_end(decisions)]
        order = ['`iterate <projekt> <slug>`: zamraża', '`writing <projekt> <slug> start`', 'dopiero potem zmiany HTML', 'ponownie `ids <projekt> <slug> --check`', '`writing <projekt> <slug> done`']
        positions = [part.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))

    def test_agent_never_accepts(self):
        text = read(SKILL)
        self.assertIn('agent nigdy nie uruchamia `skillsync prototype accept`', text)
        self.assertNotRegex(text, r'agent (uruchamia|wykonuje) `(skillsync prototype )?accept`')


@SKIP
class WritingExamplesParseTests(unittest.TestCase):
    def test_examples_exist_and_parse_with_generator_parser(self):
        examples = writing_examples()
        self.assertGreaterEqual(len(examples), 6)
        self.assertTrue(any('--ttl 1800' in line for _, _, line in examples), 'the commented example is checked')
        root = parser()
        failures = []
        for path, number, line in examples:
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    args = root.parse_args(shlex.split(line, comments=True)[1:])
                self.assertIn(args.writing_action, ('start', 'done', 'status'))
            except SystemExit:
                failures.append(f'{path}:{number}: {line} -> {err.getvalue().strip().splitlines()[-1:]}')
        self.assertEqual(failures, [])

    def test_the_checker_rejects_a_wrong_example(self):
        root = parser()
        for bad in ('skillsync prototype writing . plan-zajec stop', 'skillsync prototype writing . plan-zajec'):
            with self.subTest(bad=bad), self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                root.parse_args(shlex.split(bad)[1:])


if __name__ == '__main__':
    unittest.main()
