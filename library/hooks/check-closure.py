#!/usr/bin/env python3
"""Stop hook: przypomnienie o zamknieciu przebiegu. Uruchamiane przez Claude Code przed zakonczeniem tury.

Blokuje (exit 2) raz na sesje i plan, gdy plan z docs/plans zmieniony wzgledem HEAD (takze niesledzony albo ignorowany) ma status done, a przebieg z linii
`Przebieg:` nie ma rekordu `final`, albo ma status in_progress i wszystkie kroki `Progress` odhaczone (brak zamkniecia
przebiegu; plan z linia `Ticket:` bez `PR:` czeka na PR-y i nie blokuje). Tylko biblioteka standardowa, bez sieci i testow; kazdy blad przepuszcza ture (exit 0).

Drugie sprawdzenie (aktualnosc planu): wpis ma `--cli '<JSON: argv skillsync>'` (jak session-context.py); hook uruchamia
`<cli> plan-fresh <projekt> --json` i blokuje raz na (sesje, odcisk listy), gdy plan in_progress jest nieaktualny wzgledem checkpointow
i dziennika biegow (kody `step-reported-unchecked`, `step-running-unmarked`). Gdy jest wolna pojemnosc na podumysly, a pierwsza faza ma co
najmniej dwa kroki bez `w toku:`, wypisuje jedno zdanie przypomnienia na stderr i konczy exit 0 (nieblokujace). Brak `--cli`, blad albo
nieparsowalny wynik = pominiecie. Caly hook (git i CLI) ma wspolny termin monotoniczny TOTAL_BUDGET (wpis ma 10 s).

Kanal widoczny dla modelu: to samo zdarzenie `PostToolUse` (wpis z matcherem `Agent|Task`, timeout 5 s, termin NUDGE_BUDGET 3 s; rozpoznane po
`hook_event_name` ze stdin) wypisuje na stdout jeden JSON `hookSpecificOutput.additionalContext` z tym samym przypomnieniem o wolnej pojemnosci i
liniami o nieaktualnosci planu. Raz na (sesje, odcisk) w osobnej przestrzeni znacznikow (`nudge-`), nigdy exit 2; cisza bez planu, w podagencie
(`agent_id` w stdin), przy bledzie, bez `--cli` i po terminie."""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

GIT_TIMEOUT = 1.5
TOTAL_BUDGET = 7.0
NUDGE_BUDGET = 3.0
CLI_RESERVE = 0.3
_STARTED = time.monotonic()
MAX_PLAN_BYTES = 512 * 1024
MAX_RUN_BYTES = 5 * 1024 * 1024
# Wzorzec statusu skopiowany z `_STATUS` (status.py generatora, zrodlo prawdy); hook niczego stamtad nie importuje.
_STATUS = re.compile(r'^(?:[-*>][ \t]+)?(?:\*\*|__)?status(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*'
                     r'(?P<raw>[`*_]*(?P<word>[A-Za-z_]+)[^\n]*)', re.MULTILINE | re.IGNORECASE)
# Linia przebiegu: samodzielna kopia logiki `protocol_tokens.RUN_LINE` (zrodlo prawdy; hook niczego nie importuje z generatora).
# Zgodnosc pilnuje test roznicowy tests/test_skillsync_run_alias.py. Polskie `Przebieg:` w calym tekscie, angielskie `Run:` tylko
# w naglowku (przed pierwsza linia `## `), wygrywa linia polozona wczesniej; tolerancja `\r` (plan z koncami linii Windows).
_RUN_PL = re.compile(r'^Przebieg:[ \t]*`?([^\s`]*)`?[ \t\r]*$', re.MULTILINE)
_RUN_EN = re.compile(r'^Run:[ \t]*`?([^\s`]*)`?[ \t\r]*$', re.MULTILINE)
_SECTION_LINE = re.compile(r'^## ', re.MULTILINE)


def find_run(text):
    pl_match = _RUN_PL.search(text)
    section = _SECTION_LINE.search(text)
    en_match = _RUN_EN.search(text, 0, section.start() if section else len(text))
    if pl_match is None:
        return en_match
    if en_match is None:
        return pl_match
    return en_match if en_match.start() < pl_match.start() else pl_match


# Kroki i linie PR/Ticket jak `_STEP`, `_PR`, `_TICKET` w status.py generatora (zrodlo prawdy).
_STEP = re.compile(r'^\s*-\s\[([ xX])\]\s+\S', re.MULTILINE)
_PR = re.compile(r'^PR:\s*#(\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
_TICKET = re.compile(r'^Ticket:\s*([A-Z][A-Z0-9_]+-\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
# Id przebiegu jak `_slug` (runs.py generatora).
_RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def time_left():
    return TOTAL_BUDGET - (time.monotonic() - _STARTED)


def git(cwd, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    result = subprocess.run(['git', '-C', cwd, *args], capture_output=True, timeout=max(0.1, min(GIT_TIMEOUT, time_left())), env=env)
    if result.returncode != 0:
        raise OSError('git')
    return result.stdout.decode('utf-8', 'replace')


def changed_plans(top):
    """Sciezki plikow .md w docs/plans zmienionych wzgledem HEAD (takze niesledzonych i ignorowanych: wpisy `!!`, bo projekt
    z `workflow.specs.tracked: false` ignoruje docs/plans w .gitignore); usuniete pomijamy."""
    out = git(top, 'status', '--porcelain', '-z', '--untracked-files=all', '--ignored', '--', 'docs/plans')
    parts = out.split('\0')
    paths, index = [], 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if 'R' in code or 'C' in code:
            index += 1  # nastepny element to sciezka zrodlowa
        name = path[len('docs/plans/'):]
        # Tylko plaski docs/plans/*.md bez README.md, jak w doctorze.
        if ('D' not in code and path.startswith('docs/plans/') and '/' not in name and name.endswith('.md')
                and name.lower() != 'readme.md'):
            paths.append(path)
    return paths


def read_plan(path):
    info = os.lstat(path)
    if not os.path.isfile(path) or os.path.islink(path) or info.st_size > MAX_PLAN_BYTES:
        return None
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _section_marks(text, heading):
    match = re.search(heading, text, re.MULTILINE)
    if not match:
        return []
    rest = text[match.end():]
    end = re.search(r'^##\s+\S', rest, re.MULTILINE)
    return _STEP.findall(rest[:end.start()] if end else rest)


def all_steps_checked(text):
    """Wszystkie kroki `## Progress` (starsze plany: `## Kroki`/`## Steps`) odhaczone; brak krokow = False."""
    marks = (_section_marks(text, r'^##\s+Progress\s*$')
             or _section_marks(text, r'^##\s+(?:Kroki|Steps)\b[^\n]*$'))
    return bool(marks) and all(mark in 'xX' for mark in marks)


_RESULT_HEAD = re.compile(r'^##\s+(?:Wynik|Result)\b[^\n]*$', re.MULTILINE)
_RESULT_PLACEHOLDER = re.compile(r'^\(?\s*(?:po wykonaniu|after (?:the )?(?:run|delivery)|tbd|todo)\s*\)?\.?$', re.IGNORECASE)


def plan_has_result(text):
    """Sekcja `## Wynik` (albo `## Result`) planu ma tresc (sam szablon `(po wykonaniu)` nie liczy sie). To sladem sledzonego planu
    (git), a nie lokalnego `.skillsync/runs` (poza gitem), potwierdza sie zamkniecie przebiegu z innej maszyny."""
    match = _RESULT_HEAD.search(text)
    if not match:
        return False
    rest = text[match.end():]
    end = re.search(r'^##\s+\S', rest, re.MULTILINE)
    body = (rest[:end.start()] if end else rest).strip()
    return bool(body) and not _RESULT_PLACEHOLDER.match(body)


def run_state(top, run_id):
    """'final' gdy jakis plik przebiegu ma rekord final, 'unknown' gdy nie da sie ocenic, 'absent' gdy lokalnie nie ma
    zadnego sladu przebiegu (plik `<id>.json`, `<id>.*.json` ani katalog `<id>`: `.skillsync/runs` jest poza gitem, przebieg
    bywa z innej maszyny), inaczej 'open'."""
    directory = os.path.join(top, '.skillsync', 'runs')
    if any(os.path.islink(item) for item in (os.path.join(top, '.skillsync'), directory)):
        return 'unknown'  # magazyn przez dowiazanie nie jest czytany; tury nie blokujemy
    if not os.path.isdir(directory):
        return 'absent'
    state = 'absent'
    if os.path.lexists(os.path.join(directory, run_id)):
        state = 'open'
    for name in sorted(os.listdir(directory)):
        if not (name == run_id + '.json' or name.startswith(run_id + '.') and name.endswith('.json')):
            continue
        if state == 'absent':
            state = 'open'
        path = os.path.join(directory, name)
        try:
            if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > MAX_RUN_BYTES:
                state = 'unknown'
                continue
            with open(path, encoding='utf-8') as handle:
                records = json.load(handle).get('checkpoints')
            if not isinstance(records, list):
                state = 'unknown'
            elif any(isinstance(item, dict) and item.get('final') is True for item in records):
                return 'final'
        except (OSError, ValueError, AttributeError, RecursionError):
            state = 'unknown'
    return state


def unseen(session_id, pending):
    """Pozycje jeszcze niezgloszone w tej sesji. `stop_hook_active` zeruje sie po uzyciu narzedzia, wiec sam nie daje
    gwarancji "raz"; blad zapisu znacznika przepuszcza ture (wyjatek konczy sie w main exit 0)."""
    directory = os.path.join(tempfile.gettempdir(), 'skillsync-closure-' + str(getattr(os, 'getuid', lambda: 'user')()))
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fresh = []
    for item in pending:
        key = hashlib.sha256('\0'.join((session_id, *item)).encode('utf-8', 'replace')).hexdigest()
        try:
            os.close(os.open(os.path.join(directory, key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        fresh.append(item)
    return fresh


def closure_lines(top, owner):
    """Linie blokady zamkniecia przebiegu (jak dotad); pusta lista = nie blokujemy. Znaczniki `unseen` jak zawsze."""
    pending, unclosed, noresult = [], [], []
    for rel in changed_plans(top):
        try:
            text = read_plan(os.path.join(top, rel))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if text is None:
            continue
        head = text[:4000]
        status, line = _STATUS.search(head), find_run(head)
        word = status.group('word').lower() if status else ''
        if (word == 'in_progress' and all_steps_checked(text)
                and not (_TICKET.search(text) and not _PR.search(text))):
            unclosed.append(rel)
            continue
        if word != 'done' or not line:
            continue
        run_id = line.group(1)
        if not _RUN_ID.fullmatch(run_id):
            continue
        state = run_state(top, run_id)
        if state == 'open':
            pending.append((rel, run_id))
        elif state == 'absent' and not plan_has_result(text):
            noresult.append((rel, run_id))   # przebieg bez sladu lokalnie (inna maszyna) i plan bez Wyniku: brak dowodu zamkniecia
    pending = unseen(owner, pending)
    noresult = unseen(owner, noresult)
    unclosed = [rel for (rel,) in unseen(owner, [(rel,) for rel in unclosed])]
    lines = []
    if unclosed:
        lines.append('Plan in_progress ze wszystkimi krokami odhaczonymi: ' + ', '.join(unclosed[:3])
                     + '. Zamknij przebieg: sekcja Wynik, retro-quality, `skillsync checkpoint ... --final`, '
                       'Status: done (spec implemented); albo odznacz niedokonczony krok.')
    if pending:
        lines += ['Plan done bez zamkniecia przebiegu: ' + ', '.join(f'{rel} (przebieg {run_id})' for rel, run_id in pending[:3])
                  + '. Zamknij go (jedno polecenie, potem retro):']
        run_id = pending[0][1]
        lines.append(f'  skillsync checkpoint <projekt> --run-id {run_id} --step final --status PASS --next koniec '
                     f'--final --evidence <plik>')
        lines.append(f'  skillsync retro <projekt> --run-id {run_id}')
    if noresult:
        lines.append('Plan done bez sekcji Wynik, a przebiegu nie ma lokalnie (' + ', '.join(f'{rel} (przebieg {run_id})' for rel, run_id in noresult[:3])
                     + '): dopisz do planu sekcje Wynik. Nie zamykaj cudzego przebiegu: rekord zamykajacy tworzy maszyna, ktora go prowadzila.')
    return lines


def cli_raw():
    """Surowa wartosc `--cli` albo None, gdy argumentu nie podano."""
    args = sys.argv[1:]
    raw = None
    for index, arg in enumerate(args):
        if arg == '--cli' and index + 1 < len(args):
            raw = args[index + 1]
        elif arg.startswith('--cli='):
            raw = arg[len('--cli='):]
    return raw


def note_unreadable_cli(top, owner):
    """`--cli` podano, ale nie da sie go odczytac (np. zgubione backslashe na Windows): hak jest wtedy martwy, wiec zostaje jeden slad w
    `.skillsync/hook-events.jsonl` (raz na sesje; nigdy blokada, nigdy tresc argumentu)."""
    try:
        if owner is None or not unseen(owner, [('cli-unreadable',)]):
            return
        folder = os.path.join(top, '.skillsync')
        if os.path.islink(folder):
            return
        os.makedirs(folder, exist_ok=True)
        event = {'time': datetime.datetime.now().astimezone().isoformat(), 'hook': 'check-closure', 'event': 'cli-unreadable',
                 'reason': 'the --cli argument is not a JSON list of strings; run `skillsync link`', 'session_id': owner if not owner.startswith('repo:') else ''}
        with open(os.path.join(folder, 'hook-events.jsonl'), 'ab') as handle:
            handle.write((json.dumps(event, ensure_ascii=True) + '\n').encode('utf-8'))
    except Exception:
        pass


def cli_argv():
    """argv skillsync z `--cli` (lista JSON niepustych napisow) albo None."""
    raw = cli_raw()
    try:
        value = json.loads(raw) if raw is not None else None
    except ValueError:
        return None
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return value
    return None


def plan_fresh(top, owner=None):
    """Wynik `plan-fresh --json` (slownik) albo None: brak `--cli`, brak planow, termin, blad, nieparsowalny wynik."""
    argv = cli_argv()
    if argv is None and cli_raw() is not None:
        note_unreadable_cli(top, owner)
    left = time_left() - CLI_RESERVE
    if argv is None or left < 0.5 or not os.path.isdir(os.path.join(top, 'docs', 'plans')):
        return None
    try:
        done = subprocess.run(argv + ['plan-fresh', top, '--json'], capture_output=True, timeout=left, cwd=top,
                              stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            return None
        data = json.loads(done.stdout.decode('utf-8', 'replace'))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    findings = data.get('findings') if isinstance(data, dict) else None
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        return None
    return data


HINTS = {
    'step-reported-unchecked': 'checkpoint ma `zrobione:` z sha, a krok ma [ ] w planie: odhacz [x] z sha po PASS review; '
                               'jesli krok czeka na review, przenies go do `w toku: {step} (review)`',
    'step-running-unmarked': 'otwarty bieg podumyslu bez `w toku: {step}` w najnowszym checkpoincie: dopisz `w toku:` '
                             'albo zamknij bieg (`skillsync agent-run end --run-id <id> --agent <a-N> --result <wynik>`)',
}


def fingerprint(items):
    return hashlib.sha256('\0'.join(sorted('|'.join(str(item.get(key, '')) for key in ('code', 'plan', 'step')) for item in items))
                          .encode('utf-8', 'replace')).hexdigest()


def clean_text(value, limit):
    """Tekst z danych (nazwa planu, id kroku) do stderr: tylko drukowalny ASCII, obciety."""
    return re.sub(r'[^\x20-\x7e]', '?', str(value))[:limit]


SHORT_HINTS = {
    'step-reported-unchecked': 'odhacz [x] z sha po PASS review albo przenies krok do `w toku: {step} (review)`',
    'step-running-unmarked': 'dopisz `w toku: {step}` w checkpoincie albo zamknij bieg (`agent-run end`)',
}


def nudge_fresh_lines(data, owner):
    """Krotkie linie do `additionalContext` (jedna na znalezisko blokujace, raz na odcisk listy w sesji, przestrzen `nudge-`)."""
    blocking = [item for item in data['findings'] if item.get('blocking') is True and item.get('code') in SHORT_HINTS]
    if not blocking or not unseen(owner, [('nudge-plan-fresh', fingerprint(blocking))]):
        return []
    lines = []
    for item in blocking[:8]:
        step = clean_text(item.get('step', ''), 40)
        lines.append(f"plan nieaktualny: {clean_text(item['code'], 40)} {step} ({clean_text(item.get('plan', ''), 80)}): "
                     + SHORT_HINTS[item['code']].format(step=step))
    return lines


def freshness_lines(data, owner):
    """Blokujace linie planu nieaktualnego (raz na odcisk listy w sesji); pusta lista = nie blokujemy."""
    blocking = [item for item in data['findings'] if item.get('blocking') is True and item.get('code') in HINTS]
    if not blocking or not unseen(owner, [('plan-fresh', fingerprint(blocking))]):
        return []
    lines = ['Plan nieaktualny wzgledem checkpointow i dziennika biegow (napraw przed zakonczeniem tury):']
    for item in blocking[:8]:
        step = clean_text(item.get('step', ''), 40)
        lines.append(f"  - {item['code']} {step} ({clean_text(item.get('plan', ''), 80)}): " + HINTS[item['code']].format(step=step))
    lines.append('  Po PASS review kroku: [x] z sha, `agent-run end`, `zrobione:`/`w toku:` w checkpoincie.')
    return lines


def idle_note(data, owner, space=''):
    """Jedno zdanie o wolnej pojemnosci (nieblokujace, raz na odcisk planu i kroków w sesji) albo None."""
    idle = data.get('idle') if isinstance(data.get('idle'), dict) else {}
    steps, free = idle.get('steps'), idle.get('allowed')
    if (isinstance(free, int) and not isinstance(free, bool) and free >= 1 and isinstance(steps, list) and len(steps) >= 2
            and all(isinstance(step, str) for step in steps)
            and unseen(owner, [(space + 'plan-idle', str(idle.get('plan', '')), ','.join(steps))])):
        return f"wolna pojemnosc: {free} podumyslow; sprawdz, czy kroki {', '.join(steps[:6])} moga isc rownolegle"
    return None


def nudge(payload, top, owner):
    """PostToolUse: jeden JSON z `additionalContext` (stdout, exit 0) albo cisza; nigdy blokada."""
    if payload.get('agent_id'):
        return 0   # wywolanie wewnatrz podagenta: przypomnienie jest dla orkiestratora
    data = plan_fresh(top, owner)
    if data is None:
        return 0
    lines = nudge_fresh_lines(data, owner)
    note = idle_note(data, owner, 'nudge-')
    if note:
        lines.insert(0, note)
    if lines:
        sys.stdout.write(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': 'skillsync plan-fresh:\n' + '\n'.join(lines)}}) + '\n')
    return 0


def main():
    global TOTAL_BUDGET
    try:
        payload = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))   # UTF-8 niezaleznie od locale (cp1252 na Windows psulby wynik podagenta)
        if not isinstance(payload, dict):
            return 0
        post = payload.get('hook_event_name') == 'PostToolUse'
        if post:
            TOTAL_BUDGET = NUDGE_BUDGET
        if payload.get('stop_hook_active') and not post:
            return 0
        top = git(payload.get('cwd') or '.', 'rev-parse', '--show-toplevel').strip()
        session_id = payload.get('session_id')
        # Bez identyfikatora sesji znacznik idzie na repo: lepiej przemilczec niz wrocic do petli blokad.
        owner = session_id if isinstance(session_id, str) and session_id else 'repo:' + top
    except Exception:
        return 0
    if post:
        try:
            return nudge(payload, top, owner)
        except Exception:
            return 0
    lines = []
    try:
        lines = closure_lines(top, owner)
    except Exception:
        lines = []
    note = None
    try:
        data = plan_fresh(top, owner)
        if data is not None:
            lines += freshness_lines(data, owner)
            if not lines:
                note = idle_note(data, owner)
    except Exception:
        pass
    if lines:
        sys.stderr.write('\n'.join(lines) + '\n')
        return 2
    if note:
        sys.stderr.write(note + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
