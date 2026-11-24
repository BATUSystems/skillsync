"""`skillsync spec snapshot`: migawka speca i planu przebiegu poza gitem (tryb `specs.tracked: false`) i jej kontrola (`--check`).

Zapis: spec (z linii `Spec:` planu o `Przebieg: <id>`) i plan trafiają do `.skillsync/runs/<id>/docs/specs|plans/` oraz, gdy manifest ma
`workflow.specs.archive`, do `<archiwum>/<projekt>/<id>/specs|plans/`. Każdy cel i plik ma własny compare-and-swap: `docs/.saved.json`
w katalogu przebiegu trzyma sha ostatniego zapisu na parę (cel, plik); cel zmieniony przez kogoś innego to `BLOCKED` (nadpisanie tylko z
`--replace`). Zapis jest atomowy (plik tymczasowy i `os.replace`), bez dowiązań symbolicznych w ścieżce docelowej, z prawami 0700/0600.
`--check` niczego nie zapisuje: porównuje sha źródeł z kopiami w katalogu przebiegu, archiwum i `--to`. Kody wyjścia: 0 PASS, 1 FAIL
(zapis do celu się nie udał), 2 BLOCKED; przy mieszanym wyniku wygrywa najwyższa waga (2 > 1 > 0).
Treść plików to dane: nie trafia do komunikatów, a komunikaty przechodzą przez `redact`.
"""
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from .catalog import project_name
from .common import read_project, redact
from .pathfmt import posix
from .specs import _archive_error, _raw, validate_specs
from .status import MAX_PLAN_BYTES, RUN_ID, SCHEMA, _doc_file, _message, _plan_spec, git_blob_sha
from .test_gate import plan_for_run

KINDS = ('specs', 'plans')
SAVED = '.saved.json'
_SHA = re.compile(r'[0-9a-f]{40}')
_UNSAFE_NAME = re.compile(r'[^A-Za-z0-9._-]')
_ABS_PATH = re.compile(r'(?<![\w.])/(?:[^\s\'":;,/]+/+)*([^\s\'":;,/]+)')


class _Refused(Exception):
    """Cel odmówiony z powodu bezpieczeństwa albo konfliktu (status `BLOCKED` celu)."""


def _sanitize(name):
    clean = _UNSAFE_NAME.sub('-', name)
    return '-' if not clean.strip('.') else clean


def _scrub(text):
    """Tekst do raportu bez pełnych ścieżek: katalog domowy to `~`, pozostałe ścieżki bezwzględne skracają się do `…/<nazwa>`."""
    text = str(text)
    home = posix(Path.home())
    if home not in ('', '/'):
        text = text.replace(home, '~')
    return _ABS_PATH.sub(lambda match: '…/' + match.group(1), text)


def _git_env():
    """Środowisko gita do rozpoznania typu checkoutu: bez zmiennych `GIT_*`, ale z prawdziwym HOME i konfiguracją użytkownika.

    Ustalenie typu checkoutu (`rev-parse`) nie wykonuje niczego z konfiguracji, a `safe.directory` żyje tylko w konfiguracji globalnej; środowisko
    „sterylne” (HOME=/nonexistent) udawałoby wtedy, że git zawodzi (dubious ownership), i `--check` przepuszczałby połączony worktree."""
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    return env


def _git_dirs(root):
    """`(git-dir, git-common-dir)` jako ścieżki bezwzględne albo None (brak gita / nie repozytorium)."""
    try:
        done = subprocess.run(['git', '-C', str(root), 'rev-parse', '--git-dir', '--git-common-dir'], capture_output=True, timeout=15,
                              check=False, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return None
    lines = os.fsdecode(done.stdout).splitlines()
    if done.returncode or len(lines) != 2:
        return None
    return tuple((Path(root) / line).resolve() for line in lines)


def _project_dir_name(root, manifest):
    """Nazwa projektu w archiwum: `project.name` z manifestu, inaczej katalog główny repozytorium (nie worktree), inaczej katalog projektu."""
    name = project_name(root, manifest)
    if name:
        return _sanitize(name)
    dirs = _git_dirs(root)
    if dirs:
        common = dirs[1]
        return _sanitize((common.parent if common.name == '.git' else common).name)
    return _sanitize(Path(root).resolve().name)


def _checkout_kind(root):
    """`'linked'` | `'main'` | `'none'` (brak `.git`); `_Refused`, gdy `.git` istnieje, a git nie potrafi ustalić typu checkoutu (fail-closed)."""
    dirs = _git_dirs(root)
    if dirs:
        return 'linked' if dirs[0] != dirs[1] else 'main'
    if os.path.lexists(Path(root) / '.git'):
        raise _Refused('nie da się ustalić typu checkoutu (git zawodzi przy istniejącym .git)')
    return 'none'


def _home(path):
    """Ścieżka do raportu: katalog domowy zwinięty do `~` (bez nazwy użytkownika)."""
    text = posix(path)
    home = posix(Path.home())
    return '~' + text[len(home):] if home not in ('', '/') and (text == home or text.startswith(home + '/')) else text


def _read_regular(path):
    """Bajty zwykłego pliku (bez podążania za dowiązaniem); None, gdy go nie ma. Dowiązanie lub nie-plik: `_Refused`."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        raise _Refused('cel jest dowiązaniem symbolicznym')
    if not stat.S_ISREG(info.st_mode):
        raise _Refused('cel nie jest zwykłym plikiem')
    with open(path, 'rb') as handle:
        return handle.read(MAX_PLAN_BYTES + 1)


def _ensure_dir(path, create):
    """Katalog bez dowiązania w ostatnim komponencie; z `create` tworzony (0700). Zwraca False, gdy go nie ma (bez `create`)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return False
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:   # wyścig z innym procesem: sprawdzamy, co tam powstało
            return _ensure_dir(path, False)
        os.chmod(path, 0o700)
        return True
    if stat.S_ISLNK(info.st_mode):
        raise _Refused('katalog docelowy jest dowiązaniem symbolicznym')
    if not stat.S_ISDIR(info.st_mode):
        raise _Refused('ścieżka docelowa nie jest katalogiem')
    return True


def _ensure_chain(base, parts, create):
    """Schodzi po komponentach `parts` pod istniejącym `base`; każdy bez dowiązania. Zwraca ścieżkę albo None (brak, gdy nie `create`)."""
    path = Path(base)
    for part in parts:
        path = path / part
        if not _ensure_dir(path, create):
            return None
    return path


def _atomic_write(directory, name, data):
    """Plik tymczasowy w tym samym katalogu i `os.replace`; prawa 0600; przy błędzie nic po sobie nie zostawia."""
    fd, temp = tempfile.mkstemp(prefix=f'.{name}.', suffix='.tmp', dir=str(directory))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, Path(directory) / name)
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):   # fsync katalogu to wzmocnienie trwałości, nie warunek (Windows)
            pass
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


# ---- źródła ----

def _sources(root, run_id, spec):
    """`([(kind, name, bytes)], notes)`; `ValueError`, gdy nie ma czego kopiować albo źródło jest niedozwolone."""
    notes = []
    for part in (root / 'docs', root / 'docs' / 'specs', root / 'docs' / 'plans'):
        if part.is_symlink():
            raise ValueError(f'dowiązanie symboliczne w ścieżce źródeł: {part.relative_to(root)}')
    found = plan_for_run(root, run_id)
    spec_name = None
    if spec:
        spec_path = _doc_file(root, spec, 'specs')
        spec_name = spec_path.name
    elif found:
        linked = _plan_spec(found[1])
        if linked:
            spec_name = Path(_doc_file(root, linked, 'specs')).name
        else:
            notes.append('plan nie ma poprawnej linii Spec: (zapisano sam plan)')
    if not found and not spec:
        raise ValueError(f'brak planu z linią Przebieg: {run_id} w docs/plans i brak --spec; nic nie zapisano')
    if not found:
        notes.append('brak planu przebiegu (zapisano sam spec)')
    files = []
    if spec_name:
        files.append(('specs', spec_name, (root / 'docs' / 'specs' / spec_name).read_bytes()))
    if found:
        plan_name = Path(found[0]).name
        data = (root / 'docs' / 'plans' / plan_name).read_bytes()
        if len(data) > MAX_PLAN_BYTES:
            raise ValueError('plan przekracza dozwolony rozmiar')
        files.append(('plans', plan_name, data))
    return files, notes


def _archive_root(root, manifest):
    """`(Path|None, error|None)`: katalog archiwum z manifestu po rozwinięciu `~`."""
    errors, _ = validate_specs(manifest)
    if errors:
        return None, errors[0]
    value = _raw(manifest)
    archive = value.get('archive') if isinstance(value, dict) else None
    if archive is None:
        return None, None
    problem = _archive_error(archive)
    if problem:
        return None, problem
    return Path(os.path.expanduser(archive)), None


# ---- .saved.json ----

def _load_saved(run_dir):
    """`(saved, ok)`: `saved` = {cel: {plik: sha}}; `ok` False, gdy plik jest nieczytelny albo uszkodzony (brak pliku to pusty, poprawny stan)."""
    path = Path(run_dir) / 'docs' / SAVED
    try:
        data = _read_regular(path)
    except (OSError, _Refused):
        return {}, False
    if data is None:
        return {}, True
    try:
        parsed = json.loads(data.decode('utf-8'))
        table = parsed['saved']
        if not isinstance(table, dict) or not all(isinstance(cell, dict) and all(isinstance(key, str) and isinstance(sha, str) and _SHA.fullmatch(sha)
                                                                                 for key, sha in cell.items()) for cell in table.values()):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return {}, False
    return table, True


def _store_saved(run_dir, saved):
    data = (json.dumps({'version': 1, 'saved': saved}, indent=2, sort_keys=True) + '\n').encode('utf-8')
    _atomic_write(Path(run_dir) / 'docs', SAVED, data)


# ---- zapis ----

def _write_cell(directory, kind, name, data, target, saved, saved_ok, replace):
    """Jeden plik jednego celu. Zwraca `(status, reason)`; status: written | skipped | BLOCKED."""
    sub = Path(directory) / kind
    dest = sub / name
    sha = git_blob_sha(data)
    key = f'{kind}/{name}'
    current = _read_regular(dest)
    current_sha = git_blob_sha(current) if current is not None else None
    if current_sha == sha:
        return 'skipped', None
    if current is not None and not replace:
        if not saved_ok:
            raise _Refused(f'{SAVED} nieczytelny, a kopia różni się od źródła; użyj --replace po decyzji człowieka')
        if saved.get(target, {}).get(key) != current_sha:
            raise _Refused('kopia została zmieniona poza komendą albo nie ma wpisu zapisu; nadpisanie tylko z --replace po decyzji człowieka')
    _ensure_dir(sub, True)
    _atomic_write(sub, name, data)
    return 'written', None


def _write_target(label, base, parts, create_base, files, saved, saved_ok, replace, run_dir):
    """Wynik jednego celu: `{target, status, files}`. Nie zapisuje poza `base`; nie podąża za dowiązaniami."""
    result = {'target': label, 'status': 'PASS', 'reason': None, 'files': []}
    try:
        if not _ensure_dir(base, create_base):
            raise OSError('katalog niedostępny')
        directory = _ensure_chain(base, parts, True)
    except _Refused as error:
        result.update(status='BLOCKED', reason=str(error))
        return result
    except OSError:
        result.update(status='FAIL', reason='katalog celu niedostępny (brak, brak praw albo niezamontowany)')
        return result
    for kind, name, data in files:
        entry = {'file': f'{kind}/{name}', 'sha': git_blob_sha(data), 'status': None, 'reason': None}
        result['files'].append(entry)
        try:
            entry['status'], entry['reason'] = _write_cell(directory, kind, name, data, label, saved, saved_ok, replace)
            if (saved_ok or replace) and saved.get(label, {}).get(entry['file']) != entry['sha']:
                saved.setdefault(label, {})[entry['file']] = entry['sha']
                _store_saved(run_dir, saved)
        except _Refused as error:
            entry['status'], entry['reason'] = 'BLOCKED', str(error)
        except OSError as error:
            entry['status'], entry['reason'] = 'FAIL', f'zapis nie powiódł się ({error.__class__.__name__})'
    statuses = {entry['status'] for entry in result['files']}
    result['status'] = 'BLOCKED' if 'BLOCKED' in statuses else 'FAIL' if 'FAIL' in statuses else 'PASS'
    return result


def _code(statuses):
    return 2 if 'BLOCKED' in statuses else 1 if 'FAIL' in statuses else 0


def _blocked(reason, mode='snapshot', run=None, **extra):
    return {'schema': SCHEMA, 'status': 'BLOCKED', 'mode': mode, 'run': run if isinstance(run, str) else None,
            'reason': _message(_scrub(reason), 500), 'targets': [], **extra}, 2


def snapshot(root, manifest, run_id, spec=None, replace=False):
    """Zapisuje migawkę. `(raport, kod wyjścia)`."""
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        return _blocked('nieprawidłowy --run-id', 'snapshot')
    try:
        files, notes = _sources(root, run_id, spec)
    except (OSError, ValueError) as error:
        return _blocked(error.__class__.__name__ if isinstance(error, OSError) else error, 'snapshot', run_id)
    archive, problem = _archive_root(root, manifest)
    if problem:
        return _blocked(problem, 'snapshot', run_id)
    run_dir = root / '.skillsync' / 'runs' / run_id
    try:
        _ensure_chain(root, ('.skillsync', 'runs', run_id), True)
        _ensure_chain(run_dir, ('docs',), True)
    except _Refused as error:
        return _blocked(f'katalog przebiegu: {error}', 'snapshot', run_id)
    except OSError as error:
        return _blocked(f'katalog przebiegu niedostępny ({error.__class__.__name__})', 'snapshot', run_id)
    saved, saved_ok = _load_saved(run_dir)
    if replace and not saved_ok:
        saved, saved_ok = {}, True
    targets = [_write_target('run', run_dir / 'docs', (), False, files, saved, saved_ok, replace, run_dir)]
    if archive is not None:
        # korzeń archiwum musi istnieć (nie tworzymy go); pod nim `<projekt>/<id>` i `specs|plans` tworzymy bez dowiązań
        targets.append(_write_target('archive', archive, (_project_dir_name(root, manifest), run_id), False, files, saved, saved_ok, replace,
                                     run_dir))
        targets[-1]['path'] = _home(archive / _project_dir_name(root, manifest) / run_id)
    targets[0]['path'] = posix(Path('.skillsync') / 'runs' / run_id / 'docs')
    code = _code({target['status'] for target in targets})
    report = {'schema': SCHEMA, 'status': {0: 'PASS', 1: 'FAIL', 2: 'BLOCKED'}[code], 'mode': 'snapshot', 'run': run_id, 'replace': replace,
              'files': [{'file': f'{kind}/{name}', 'sha': git_blob_sha(data)} for kind, name, data in files], 'targets': targets,
              'notes': notes}
    return report, code


# ---- kontrola ----

def _compare(base, files):
    """`(status, lista rozbieżnych plików)`; status: ok | mismatch | unavailable."""
    base = Path(base)
    try:
        if not base.is_dir() or base.is_symlink():
            return 'unavailable', []
    except OSError:
        return 'unavailable', []
    for kind in {kind for kind, _, _ in files}:
        if (base / kind).is_symlink():
            return 'unavailable', []
    bad = []
    for kind, name, data in files:
        try:
            current = _read_regular(base / kind / name)
        except (OSError, _Refused):
            current = None
        if current is None or git_blob_sha(current) != git_blob_sha(data):
            bad.append(f'{kind}/{name}')
    return ('mismatch' if bad else 'ok'), bad


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def check(root, manifest, run_id, spec=None, to=None):
    """Porównuje sha źródeł z kopiami; bez zapisu. `(raport, kod)`: 0 PASS, 2 BLOCKED."""
    root = Path(root).resolve()
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        return _blocked('nieprawidłowy --run-id', 'check')
    try:
        files, notes = _sources(root, run_id, spec)
    except (OSError, ValueError) as error:
        return _blocked(error.__class__.__name__ if isinstance(error, OSError) else error, 'check', run_id)
    archive, problem = _archive_root(root, manifest)
    if problem:
        return _blocked(problem, 'check', run_id)
    candidates = [('run', root / '.skillsync' / 'runs' / run_id / 'docs', False)]
    if archive is not None:
        candidates.append(('archive', archive / _project_dir_name(root, manifest) / run_id, True))
    if to:
        candidates.append(('to', Path(to).expanduser() / '.skillsync' / 'runs' / run_id / 'docs', True))
    targets, outside = [], 0
    for label, base, external in candidates:
        state, bad = _compare(base, files)
        counts = external and not _inside(base, root)
        outside += bool(counts and state == 'ok')
        targets.append({'target': label, 'status': 'PASS' if state == 'ok' else 'BLOCKED', 'outside_worktree': bool(counts),
                        'path': posix(Path('.skillsync') / 'runs' / run_id / 'docs') if label == 'run' else _home(base),
                        'reason': {'ok': None, 'mismatch': 'sha różni się od źródła', 'unavailable': 'katalog niedostępny, nie da się zweryfikować'}[state],
                        'mismatched': bad})
    reasons = []
    if any(target['status'] != 'PASS' for target in targets):
        reasons.append('są rozbieżne albo niedostępne cele: ' + ', '.join(target['target'] for target in targets if target['status'] != 'PASS'))
    else:
        try:
            kind = _checkout_kind(root)
        except _Refused as error:
            kind = None
            reasons.append(str(error))
        if kind == 'linked':
            if not outside:
                reasons.append('połączony worktree bez celu poza worktree (ustaw workflow.specs.archive albo podaj --to <korzeń kopii>)')
        elif kind and not any(target['outside_worktree'] for target in targets):
            notes.append('główny checkout bez celu poza nim: sprzątania worktree to nie dotyczy')
    code = 2 if reasons else 0
    report = {'schema': SCHEMA, 'status': 'BLOCKED' if code else 'PASS', 'mode': 'check', 'run': run_id,
              'reason': '; '.join(reasons) or None, 'files': [{'file': f'{kind}/{name}', 'sha': git_blob_sha(data)} for kind, name, data in files],
              'targets': targets, 'notes': notes}
    return report, code


# ---- CLI ----

def _render(report):
    lines = [f'spec snapshot {report.get("mode", "")}: {report["status"]}'.replace('  ', ' ')]
    if report.get('reason'):
        lines.append(f'  powód: {report["reason"]}')
    for target in report.get('targets', []):
        lines.append(f'  {target["target"]}: {target["status"]}' + (f' ({target["reason"]})' if target.get('reason') else ''))
        for entry in target.get('files', []):
            lines.append(f'    {entry["file"]}: {entry["status"]}' + (f' ({entry["reason"]})' if entry.get('reason') else ''))
        for name in target.get('mismatched', []):
            lines.append(f'    {name}: rozbieżne')
    for note in report.get('notes', []):
        lines.append(f'  uwaga: {note}')
    return '\n'.join(lines)


def _cmd_snapshot(args):
    mode = 'check' if args.check else 'snapshot'
    try:
        if not args.check and args.to:
            raise ValueError('--to dotyczy tylko --check')
        if args.check and args.replace:
            raise ValueError('--replace nie dotyczy --check (bez zapisu)')
        root, manifest = read_project(args.path, args.manifest)
        if args.check:
            report, code = check(root, manifest, args.run_id, args.spec, args.to)
        else:
            report, code = snapshot(root, manifest, args.run_id, args.spec, args.replace)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        report, code = _blocked(error.__class__.__name__ if isinstance(error, (OSError, RecursionError)) else error, mode, args.run_id)
    report = redact(report)
    if args.json:
        try:
            text = json.dumps(report, ensure_ascii=False, indent=2)
            text.encode('utf-8')
        except (UnicodeError, RecursionError):
            text = json.dumps(report, ensure_ascii=True, indent=2)
    else:
        text = _render(report).encode('utf-8', 'backslashreplace').decode('utf-8')
    print(text)
    return code


def register_snapshot(actions):
    sub = actions.add_parser('snapshot', help='Copy the run spec and plan to .skillsync/runs/<id>/docs (and the archive); --check verifies the copies',
                            description='Copy the spec and the plan of a run to .skillsync/runs/<id>/docs and to the archive of the manifest (workflow.specs.archive). '
                                        'Without --check it writes the copies and never overwrites a copy changed by someone else unless --replace is given; '
                                        'with --check it only verifies the copies against the sources and writes nothing.')
    sub.add_argument('path', nargs='?', default='.', help='project directory (default: .)')
    sub.add_argument('--run-id', required=True, help='run id: the directory .skillsync/runs/<id> that receives the copies')
    sub.add_argument('--spec', help='spec file (docs/specs/*.md) instead of the plan `Spec:` line')
    sub.add_argument('--manifest', help='manifest file outside the project (replaces .github/.skillsync.yaml)')
    sub.add_argument('--replace', action='store_true', help='overwrite copies changed by someone else (a human decision)')
    sub.add_argument('--check', action='store_true', help='verify the copies against the sources; no writes (0 PASS, 2 BLOCKED)')
    sub.add_argument('--to', help='--check: root of a copy of the project outside the worktree (<to>/.skillsync/runs/<id>/docs)')
    sub.add_argument('--json', action='store_true', help='print the report as JSON instead of text')
    sub.set_defaults(func=_cmd_snapshot)
    return sub
