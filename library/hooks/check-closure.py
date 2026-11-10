#!/usr/bin/env python3
"""Stop hook: przypomnienie o zamknieciu przebiegu. Uruchamiane przez Claude Code przed zakonczeniem tury.

Blokuje (exit 2) raz na sesje i plan, gdy plan z docs/plans zmieniony wzgledem HEAD ma status done, a przebieg z linii
`Przebieg:` nie ma rekordu `final`, albo ma status in_progress i wszystkie kroki `Progress` odhaczone (brak zamkniecia
przebiegu; plan z linia `Ticket:` bez `PR:` czeka na PR-y i nie blokuje). Tylko biblioteka standardowa, bez sieci i testow; kazdy blad przepuszcza ture (exit 0)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

GIT_TIMEOUT = 1.5
MAX_PLAN_BYTES = 512 * 1024
MAX_RUN_BYTES = 5 * 1024 * 1024
# Wzorzec statusu skopiowany z `_STATUS` (status.py generatora, zrodlo prawdy); hook niczego stamtad nie importuje.
_STATUS = re.compile(r'^(?:[-*>][ \t]+)?(?:\*\*|__)?status(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*'
                     r'(?P<raw>[`*_]*(?P<word>[A-Za-z_]+)[^\n]*)', re.MULTILINE | re.IGNORECASE)
_RUN = re.compile(r'^Przebieg:[ \t]*`?([^\s`]*)`?[ \t]*$', re.MULTILINE)
# Kroki i linie PR/Ticket jak `_STEP`, `_PR`, `_TICKET` w status.py generatora (zrodlo prawdy).
_STEP = re.compile(r'^\s*-\s\[([ xX])\]\s+\S', re.MULTILINE)
_PR = re.compile(r'^PR:\s*#(\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
_TICKET = re.compile(r'^Ticket:\s*([A-Z][A-Z0-9_]+-\d+)\s*\(link:\s*(\S+?)\)\s*$', re.MULTILINE)
# Id przebiegu jak `_slug` (runs.py generatora).
_RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def git(cwd, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    result = subprocess.run(['git', '-C', cwd, *args], capture_output=True, timeout=GIT_TIMEOUT, env=env)
    if result.returncode != 0:
        raise OSError('git')
    return result.stdout.decode('utf-8', 'replace')


def changed_plans(top):
    """Sciezki plikow .md w docs/plans zmienionych wzgledem HEAD (takze nieSledzonych); usuniete pomijamy."""
    out = git(top, 'status', '--porcelain', '-z', '--untracked-files=all', '--', 'docs/plans')
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


def run_state(top, run_id):
    """'final' gdy jakis plik przebiegu ma rekord final, 'unknown' gdy nie da sie ocenic, inaczej 'open'."""
    directory = os.path.join(top, '.skillsync', 'runs')
    if any(os.path.islink(item) for item in (os.path.join(top, '.skillsync'), directory)):
        return 'unknown'  # magazyn przez dowiazanie nie jest czytany; tury nie blokujemy
    if not os.path.isdir(directory):
        return 'open'
    state = 'open'
    for name in sorted(os.listdir(directory)):
        if not (name == run_id + '.json' or name.startswith(run_id + '.') and name.endswith('.json')):
            continue
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


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get('stop_hook_active'):
            return 0
        top = git(payload.get('cwd') or '.', 'rev-parse', '--show-toplevel').strip()
        pending, unclosed = [], []
        for rel in changed_plans(top):
            try:
                text = read_plan(os.path.join(top, rel))
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            if text is None:
                continue
            head = text[:4000]
            status, line = _STATUS.search(head), _RUN.search(head)
            word = status.group('word').lower() if status else ''
            if (word == 'in_progress' and all_steps_checked(text)
                    and not (_TICKET.search(text) and not _PR.search(text))):
                unclosed.append(rel)
                continue
            if word != 'done' or not line:
                continue
            run_id = line.group(1)
            if _RUN_ID.fullmatch(run_id) and run_state(top, run_id) == 'open':
                pending.append((rel, run_id))
        session_id = payload.get('session_id')
        # Bez identyfikatora sesji znacznik idzie na repo: lepiej przemilczec niz wrocic do petli blokad.
        owner = session_id if isinstance(session_id, str) and session_id else 'repo:' + top
        pending = unseen(owner, pending)
        unclosed = [rel for (rel,) in unseen(owner, [(rel,) for rel in unclosed])]
        if not pending and not unclosed:
            return 0
        lines = []
        if unclosed:
            lines.append('Plan in_progress ze wszystkimi krokami odhaczonymi: ' + ', '.join(unclosed[:3])
                         + '. Zamknij przebieg: sekcja Wynik, retro-quality, `skillsync checkpoint ... --final`, '
                           'Status: done (spec implemented); albo odznacz niedokonczony krok.')
        if not pending:
            sys.stderr.write('\n'.join(lines) + '\n')
            return 2
        lines += ['Plan done bez zamkniecia przebiegu: ' + ', '.join(f'{rel} (przebieg {run_id})' for rel, run_id in pending[:3])
                 + '. Zamknij go (jedno polecenie, potem retro):']
        run_id = pending[0][1]
        lines.append(f'  skillsync checkpoint <projekt> --run-id {run_id} --step final --status PASS --next koniec '
                     f'--final --evidence <plik>')
        lines.append(f'  skillsync retro <projekt> --run-id {run_id}')
        sys.stderr.write('\n'.join(lines) + '\n')
        return 2
    except Exception:
        return 0


if __name__ == '__main__':
    sys.exit(main())
