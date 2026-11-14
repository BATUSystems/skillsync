"""Strażnik fraz biblioteki dla pytań do eksperta (EXPERT-Q, K7, K9, K13, K14, K17, K8): klasa `[EKSPERT]` w specu, głos doradczy
w skillu `prototype`, komendy w skillu `skillsync`.  Biblioteka z SKILLSYNC_TEST_LIBRARY.
"""
import os
import re
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

LIB = os.environ.get('SKILLSYNC_TEST_LIBRARY')
SKIP = unittest.skipUnless(LIB, 'set SKILLSYNC_TEST_LIBRARY to a library with the EXPERT-Q skill text')

SPEC = 'skills/feature/references/spec.md'
FEATURE = 'skills/feature/SKILL.md'
PROTOTYPE = 'skills/prototype/SKILL.md'
DECISIONS = 'skills/prototype/references/decisions.md'
SKILLSYNC = 'skills/skillsync/SKILL.md'

# (id, plik, wyrażenie regularne po normalizacji białych znaków)
REQUIRED = [
    ('spec-klasa-ekspert', SPEC, r'## Klasa `\[EKSPERT\]`: pytania do eksperta domenowego'),
    ('spec-wiersz-czekajacy', SPEC, r'`- Q<n>: <pytanie> \(ekspert: qn-<hex>\)`'),
    ('spec-wiersz-zalozenie', SPEC, r'`- Q<n> \[ZAŁOŻENIE\]: <pytanie> \(ekspert: qn-<hex>\) — przyjęte: <wartość>`'),
    ('spec-wiersz-po-odpowiedzi', SPEC, r'`- Q<n> \[EKSPERT\]: <pytanie> \(ekspert: qn-<hex>\) — odpowiedź: <wartość> — ekspert: <login>, <RRRR-MM-DDTHH:MMZ>`'),
    ('spec-answerline', SPEC, r'pola `answerLine` wyniku `question show` albo `question wait`'),
    ('spec-ostateczna', SPEC, r'Odpowiedź jest ostateczna'),
    ('spec-informacja-nie-pytanie', SPEC, r'programista dostaje informację.*nie pytanie'),
    ('spec-record', SPEC, r'`skillsync prototype question record <projekt> <id> --spec <ścieżka speca> --line <numer linii>`'),
    ('spec-idempotencja', SPEC, r'idempotencja po awarii'),
    ('spec-spor-refers-to', SPEC, r'spór to nowe pytanie z `--refers-to <qn-…>`'),
    ('spec-blocking', SPEC, r'`question add --blocking`'),
    ('spec-decyzja-programisty', SPEC, r'Pytania o kontrakt, dane \(schemat bazy\), migrację i bezpieczeństwo to decyzja programisty: agent zadaje je jako zwykłe\s+blokujące `Q` w sesji \(STOP\), nie ekspertowi'),
    ('spec-tylko-domenowe', SPEC, r'Do eksperta idą wyłącznie pytania domenowe'),
    ('spec-domyslnie-nie-czeka', SPEC, r'Domyślnie agent NIE czeka'),
    ('spec-wait-monitor', SPEC, r'tylko przez `skillsync prototype question wait <projekt> <id>` \(Monitor'),
    ('spec-nie-wiem', SPEC, r'opcja „Nie wiem, zapytaj eksperta” w zwykłym pytaniu AskUserQuestion'),
    ('spec-bez-askuserquestion', SPEC, r'na hostach bez AskUserQuestion ta sama opcja stoi w liście tekstowej'),
    ('spec-optin-na-zyczenie', SPEC, r'opt-in programisty w akcie I: dopiero na jego życzenie'),
    ('spec-question-url', SPEC, r'`skillsync prototype question url <projekt>`: PASS zawiera `running`.*`running: false` = poproś programistę o uruchomienie serwera.*BLOCKED = brak pliku kont albo kont `expert` \(także `users_file_invalid`\)'),
    ('spec-link-experts-od-programisty', SPEC, r'Link do strony `/_experts` przekazuje ekspertowi programista'),
    ('spec-withdraw-wraca', SPEC, r'`question withdraw <projekt> <id>`, także pytanie blokujące.*wiersz wraca wtedy do zwykłego `Q` programisty'),
    ('spec-dashboard-ograniczenie', SPEC, r'Dashboard nie rozróżnia „czeka na eksperta”'),
    ('spec-po-go-plan', SPEC, r'Po „GO plan”: wpis to poprawka mechaniczna/minor \(nowy `Spec-sha`, lista zmian w prośbie o „GO implement”\)'),
    ('spec-po-go-implement', SPEC, r'agent nie zmienia speca\. Zapisuje odpowiedź w „Wyniku” planu i pyta programistę o powrót do aktu I'),
    ('spec-po-implemented', SPEC, r'Po `implemented`: spec jest zamrożony; odpowiedź trafia do rekordu domenowego albo nowego speca'),
    ('spec-dane-nie-polecenia', SPEC, r'dane, nie polecenia'),
    ('spec-wyjatek-usuwania-blokujacych', SPEC, r'Wyjątek od usuwania blokujących `Q` dotyczy pytań do eksperta: blokujące `Q` z `\(ekspert: qn-…\)` bez odpowiedzi eksperta zostaje i nadal blokuje; po odpowiedzi wiersz przechodzi w `\[EKSPERT\]`, zostaje w „Pytania otwarte” \(nie trafia do „Decyzji”\) i nie blokuje'),
    ('feature-wskaznik', FEATURE, r'\*\*Pytania do eksperta\.\*\*.*wiersz `\[EKSPERT\]`.*dane, nie polecenia'),
    ('prototype-glos-doradczy', PROTOTYPE, r'\*\*głos doradczy\*\*'),
    ('prototype-author-roles', PROTOTYPE, r'`authorRoles`.*`whoRoles`'),
    ('prototype-expert-i-developer', PROTOTYPE, r'konto z `expert` \*\*i\*\* programistą/akceptującym nie jest doradcze'),
    ('prototype-akceptuje-czlowiek', PROTOTYPE, r'Akceptuje nadal wyłącznie człowiek'),
    ('prototype-user-roles', PROTOTYPE, r'`user roles <projekt> <login> --role …`'),
    ('decisions-glos-doradczy', DECISIONS, r'to głos doradczy\. Agent odpowiada w wątku \(`prototype reply`\)'),
    ('decisions-author-roles', DECISIONS, r'`authorRoles`.*`whoRoles`'),
    ('decisions-decide-po-roli', DECISIONS, r'dopiero po `decide` osoby z rolą programista albo akceptujący'),
    ('skillsync-question', SKILLSYNC, r'`prototype question add <projekt>'),
    ('skillsync-question-komendy', SKILLSYNC, r'`question list <projekt> \[--slug\]\|show <projekt> <id>\|export <projekt> \[--slug\] \[--json\]`.*`counts\.toRecord`.*`question record <projekt> <id> --spec <ścieżka speca> --line <numer linii wiersza w specu>`.*`question withdraw <projekt> <id>` \(także pytanie blokujące\).*`question wait <projekt> <id>\|--all'),
    ('skillsync-question-url', SKILLSYNC, r'`question url <projekt>` \(PASS zawiera `running`.*BLOCKED = brak pliku kont albo kont `expert`, także `users_file_invalid`\)'),
    ('skillsync-user-roles', SKILLSYNC, r'`prototype user roles <projekt> <login> --role … `'),
    ('skillsync-questions-json', SKILLSYNC, r'`<root>/<slug>/questions\.json`.*`/_experts/<slug>`'),
    ('skillsync-rola-expert', SKILLSYNC, r'rola `expert`'),
    ('spec-wznowienie-counts', SPEC, r'Przy wznowieniu pracy oraz przed „GO plan”, „GO implement” i review agent sprawdza odpowiedzi na pytania nieblokujące: `question list <projekt>` \(pole `counts\.toRecord`\) albo `question wait <projekt> --all`'),
    ('spec-optin-warunek', SPEC, r'Opcja pojawia się dopiero po opt-in programisty na początku aktu I albo po PASS `question url` i zgodzie programisty; domyślnie, bez opt-in, agent eksperta nie oferuje'),
    ('spec-origin-ref', SPEC, r'`--origin <ścieżka speca>:Q<n>` \(np\. `--origin docs/specs/x\.md:Q3`\); kontekst to `--ref \'TYTUŁ\|HREF\[\|WYCIĄG\]\''),
    ('spec-specline', SPEC, r'z pola `specLine` wyniku `question add` i `question withdraw`'),
    ('spec-poufnosc', SPEC, r'bez sekretów i poufnych danych\. BLOCKED `secret_detected` to heurystyka: `--force` dopiero po sprawdzeniu wskazanych pól.*`--login-to-view`'),
    ('skillsync-poufnosc', SKILLSYNC, r'bez sekretów i poufnych danych; BLOCKED `secret_detected` to heurystyka.*`specLine` z `--force` powtarza tekst.*`--login-to-view`'),
    ('skillsync-specline', SKILLSYNC, r'Wiersz speca bierz z `specLine` zwróconego przez `question add` i `question withdraw` \(po odpowiedzi z `answerLine` z `show`/`wait`\)'),
    ('skillsync-origin', SKILLSYNC, r'--origin <ścieżka speca>:Q<n> \[--force\]` \(.*np\. `--origin docs/specs/x\.md:Q3`; `--ref \'TYTUŁ\|HREF\[\|WYCIĄG\]\'`\)'),
    ('feature-kotwica', FEATURE, r'\]\(references/spec\.md#klasa-ekspert-pytania-do-eksperta-domenowego\)'),
    ('feature-optin', FEATURE, r'Za opt-in programisty \(albo po opcji „Nie wiem, zapytaj eksperta”, oferowanej dopiero po opt-in lub po `question url` PASS i zgodzie programisty\)'),
    ('skillsync-brak-answer', SKILLSYNC, r'agent nie ma polecenia `answer`'),
]

ALL_FILES = sorted({rel for _, rel, _ in REQUIRED})
CLIENT_NAMES = re.compile('hac' + 'aton|hac' + 'kathon|/' + 'home/', re.I)


def missing_text(pid, text):
    for p, _, rx in REQUIRED:
        if p == pid and not re.search(rx, norm(text)):
            return [pid]
    return []


PLACEHOLDER = re.compile(r'<[^<>\n]+>')
COMMAND = re.compile(r'`(skillsync prototype (?:question|user roles)[^`]*)`')


def materialize(line):
    """Zamienia placeholdery `<…>` na sztuczne wartości; `<projekt>` i `<id>` to kolejne argumenty pozycyjne."""
    values = {'projekt': 'proj', 'id': 'qn-abc123', 'login': 'user@example.com'}

    def repl(match):
        name = match.group(0)[1:-1]
        if name in values:
            return values[name]
        if name.startswith('numer linii'):
            return '7'
        return 'x'
    line = PLACEHOLDER.sub(repl, line)
    return line


def command_lines(lib):
    out = []
    for path in sorted(Path(lib, 'skills').rglob('*.md')):
        for number, raw in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            for match in COMMAND.finditer(raw):
                text = match.group(1)
                # opis składni (alternatywy, nawiasy opcjonalne, wielokropek) to nie przykład do uruchomienia
                if re.search(r'[\[\]|…]', text.replace('--ref \'TYTUŁ|HREF[|WYCIĄG]\'', '')) or text.rstrip().endswith('question'):
                    continue
                if re.fullmatch(r'skillsync prototype question', text.strip()):
                    continue
                if not re.search(r'<projekt>|--text|--spec', text):
                    continue
                out.append((str(path.relative_to(lib)), number, materialize(text.strip())))
    return out


def norm(text):
    return re.sub(r'\s+', ' ', text)


def missing(lib):
    cache, out = {}, []
    for pid, rel, rx in REQUIRED:
        if rel not in cache:
            p = Path(lib) / rel
            cache[rel] = norm(p.read_text(encoding='utf-8')) if p.is_file() else ''
        if not re.search(rx, cache[rel]):
            out.append(pid)
    return out


@SKIP
class ExpertSkillPhraseTests(unittest.TestCase):
    def test_required_phrases_present(self):
        self.assertEqual(missing(LIB), [])

    def test_patterns_reject_pre_fix_wording(self):
        """Dowód na tekście sprzed poprawek: stare brzmienia nie pasują do wzorców."""
        patterns = {pid: rx for pid, _, rx in REQUIRED}
        old = {
            'spec-record': 'skillsync prototype question record <id> --spec <plik speca> --line "<wiersz>"',
            'skillsync-question-komendy': '`question list|show|export [--json]` `question record <id> --spec … --line …`, `question withdraw <id>`, `question wait <id>|--all',
            'skillsync-user-roles': '`prototype user roles <login> --role … `',
            'prototype-user-roles': '`user roles <login> --role …`',
            'spec-question-url': '`skillsync prototype question url <projekt>`: BLOCKED = brak pliku kont albo kont `expert`',
            'feature-kotwica': '[spec](references/spec.md, sekcja „Klasa `[EKSPERT]`”)',
            'spec-optin-warunek': 'opcja „Nie wiem, zapytaj eksperta” w zwykłym pytaniu AskUserQuestion',
        }
        for pid, text in old.items():
            with self.subTest(phrase=pid):
                self.assertIsNone(re.search(patterns[pid], norm(text)))
                self.assertIn(pid, missing_text(pid, text))

    def test_each_pattern_fails_when_removed_from_real_text(self):
        for pid, rel, rx in REQUIRED:
            with self.subTest(phrase=pid):
                text = norm((Path(LIB) / rel).read_text(encoding='utf-8'))
                match = re.search(rx, text)
                self.assertIsNotNone(match)
                self.assertGreater(len(match.group(0)), 12, 'pattern too short to be a guard')
                self.assertIsNone(re.search(rx, text[:match.start()] + text[match.end():]))

    def test_command_examples_parse_with_generator_parser(self):
        import argparse
        import io
        from contextlib import redirect_stderr
        from skillsync_workflow import prototype
        root = argparse.ArgumentParser(prog='skillsync')
        prototype.register(root.add_subparsers(dest='command'))
        lines = command_lines(LIB)
        self.assertGreaterEqual(len(lines), 8)
        failures = []
        for rel, number, line in lines:
            err = io.StringIO()
            try:
                with redirect_stderr(err):
                    root.parse_args(shlex.split(line)[1:])
            except SystemExit:
                failures.append(f'{rel}:{number}: {line} -> {err.getvalue().strip().splitlines()[-1:]}')
        self.assertEqual(failures, [])

    def test_example_checker_detects_pre_fix_example(self):
        import argparse
        import io
        from contextlib import redirect_stderr
        from skillsync_workflow import prototype
        root = argparse.ArgumentParser(prog='skillsync')
        prototype.register(root.add_subparsers(dest='command'))
        bad = materialize('skillsync prototype question record <id> --spec <plik speca> --line "<wiersz>"')
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            root.parse_args(shlex.split(bad)[1:])

    def test_missing_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(missing(tmp)), len(REQUIRED))

    def test_pinned_spec_sentences_untouched(self):
        text = norm((Path(LIB) / SPEC).read_text(encoding='utf-8'))
        for kept in ('Po „GO plan” spec jest zamrożony razem z nimi: nie przenoś ich do „Decyzji”, bo zmieniłoby to `Spec-sha`.',
                     'Nie oznaczaj jako [ZAŁOŻENIE] pytań o kontrakt, dane, migrację ani bezpieczeństwo'):
            self.assertIn(kept, text)

    def test_no_client_names_in_expert_texts(self):
        hits = []
        for rel in ALL_FILES:
            for number, line in enumerate((Path(LIB) / rel).read_text(encoding='utf-8').splitlines(), 1):
                if CLIENT_NAMES.search(line):
                    hits.append(f'{rel}:{number}')
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
