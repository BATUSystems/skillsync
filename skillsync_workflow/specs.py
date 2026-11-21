"""Tryb przechowywania speców i planów: `route.specs {tracked, archive, source}` i walidacja `workflow.specs`.

Wartości czyta się z surowego manifestu projektu (jak `workflow.ceremony`; nie z `defaults.yaml` ani `projects/<slug>.yaml`).
Wykrywanie: `git check-ignore --no-index -v` dla `docs/specs/x.md` i `docs/plans/x.md` w środowisku izolowanym od maszyny; liczy się
tylko reguła z pliku `.gitignore` o ścieżce względnej w drzewie repozytorium (negacja `!`, `.git/info/exclude`, `core.excludesFile` nie).
Komunikaty nie zawierają wartości z manifestu.
"""
import os
import subprocess
from pathlib import Path

from . import fsutil
from .layers import _quote_arg
from .pathfmt import posix

PROBES = ('docs/specs/x.md', 'docs/plans/x.md')
KNOWN = ('tracked', 'archive')
MANIFEST_PATH = '.github/.skillsync.yaml'
_MISSING = object()


def _raw(manifest):
    section = manifest.get('workflow') if isinstance(manifest, dict) else None
    if not isinstance(section, dict) or 'specs' not in section:
        return _MISSING
    return section['specs']


def _archive_error(value):
    if not isinstance(value, str) or not value.strip():
        return 'workflow.specs.archive: musi być niepustym napisem (ścieżką)'
    parts = value.replace('\\', '/').split('/')
    if not (value.startswith('~/') or fsutil.isabs(value)) or '..' in parts:
        return 'workflow.specs.archive: musi zaczynać się od ~/ albo być bezwzględną ścieżką, bez `..`'
    return None


def validate_specs(manifest):
    """`(errors, warnings)` dla samego manifestu (bez gita). Brak klucza jest poprawny."""
    value = _raw(manifest)
    if value is _MISSING:
        return [], []
    if not isinstance(value, dict):
        return ['workflow.specs: musi być mapą z kluczami tracked i archive'], []
    errors, warnings = [], []
    for key in value:
        if key not in KNOWN:
            text = f'workflow.specs: nieznany klucz {str(key)!r} (znane: tracked, archive)'
            if text not in warnings:
                warnings.append(text)
    if 'tracked' in value and not isinstance(value['tracked'], bool):
        errors.append('workflow.specs.tracked: musi być wartością logiczną (true albo false)')
    if 'archive' in value:
        problem = _archive_error(value['archive'])
        if problem:
            errors.append(problem)
        elif not value['archive'].startswith('~/'):
            warnings.append('workflow.specs.archive: ścieżka bezwzględna w śledzonym manifeście zdradza nazwę użytkownika; użyj ~/')
    return errors, warnings


def _git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update({'HOME': '/nonexistent', 'XDG_CONFIG_HOME': '/nonexistent', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                'GIT_TERMINAL_PROMPT': '0'})
    return env


def _is_repo_root(repo):
    """Czy `repo` jest korzeniem repozytorium git (projekt zagnieżdżony nie dziedziczy reguł z nadrzędnego `.gitignore`)."""
    try:
        done = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--show-toplevel'], capture_output=True, timeout=15, check=False,
                              env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode:
        return False
    try:
        return Path(os.fsdecode(done.stdout).strip()).resolve() == Path(repo).resolve()
    except OSError:
        return False


def _ignored_by_repo_rule(repo, probe):
    try:
        done = subprocess.run(['git', '-C', str(repo), 'check-ignore', '--no-index', '-v', '--stdin', '-z'], input=os.fsencode(probe) + b'\0',
                              capture_output=True, timeout=15, check=False, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode != 0:
        return False
    fields = [os.fsdecode(raw) for raw in done.stdout.split(b'\0')]
    if len(fields) < 4:
        return False
    source, _line, pattern = fields[:3]
    return not (pattern.startswith('!') or fsutil.isabs(source) or os.path.basename(source) != '.gitignore')


def ignore_rules(repo):
    """Liczba katalogów (0-2) ignorowanych regułą z `.gitignore` drzewa repozytorium (0, gdy `repo` nie jest korzeniem repozytorium)."""
    if not _is_repo_root(repo):
        return 0
    return sum(1 for probe in PROBES if _ignored_by_repo_rule(repo, probe))


def specs_info(repo, manifest):
    """Pole `route.specs`: `{tracked, archive, source[, error]}`; `source` = manifest | gitignore | default | invalid."""
    errors, _ = validate_specs(manifest)
    if errors:
        return {'tracked': None, 'archive': None, 'source': 'invalid', 'error': errors[0]}
    value = _raw(manifest)
    value = value if isinstance(value, dict) else {}
    archive = value.get('archive')
    archive = posix(os.path.expanduser(archive)) if archive is not None else None
    rules = ignore_rules(repo)
    if value.get('tracked') is True and rules == 2:
        return {'tracked': None, 'archive': archive, 'source': 'invalid',
                'error': 'workflow.specs.tracked: true koliduje z regułą ignorowania docs/specs i docs/plans w .gitignore '
                         '(usuń regułę albo klucz; git add -f na tych katalogach jest zakazany)'}
    if 'tracked' in value:
        return {'tracked': value['tracked'], 'archive': archive, 'source': 'manifest'}
    if rules == 2:
        return {'tracked': False, 'archive': archive, 'source': 'gitignore'}
    return {'tracked': True, 'archive': archive, 'source': 'default'}


def _tracked_files(repo):
    try:
        done = subprocess.run(['git', '-C', str(repo), 'ls-files', '-z', '--', 'docs/specs', 'docs/plans'], capture_output=True, timeout=15,
                              check=False, env=_git_env())
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode:
        return []
    return sorted(os.fsdecode(raw) for raw in done.stdout.split(b'\0') if raw)


def specs_findings(repo, manifest):
    """Wpisy `doctor` jako `(level, path, message)` (K3, K9)."""
    errors, warnings = validate_specs(manifest)
    found = [('error', MANIFEST_PATH, text) for text in errors] + [('warning', MANIFEST_PATH, text) for text in warnings]
    if errors:
        return found
    value = _raw(manifest)
    value = value if isinstance(value, dict) else {}
    rules = ignore_rules(repo)
    info = specs_info(repo, manifest)
    if info['source'] == 'invalid':
        return found + [('error', MANIFEST_PATH, info['error'])]
    if value.get('archive') is not None and info['tracked']:
        found.append(('warning', MANIFEST_PATH, 'workflow.specs.archive: archiwum ma sens tylko przy tracked: false (przy śledzonych specach '
                                                'historię trzyma git)'))
    if value.get('tracked') is False and rules < 2:
        found.append(('warning', '.gitignore', 'workflow.specs.tracked: false, ale .gitignore nie ignoruje obu katalogów; dopisz do .gitignore '
                                              'wpisy `/docs/specs/` i `/docs/plans/` (inaczej `git add -A` je zacommituje)'))
    elif rules == 1:
        found.append(('warning', '.gitignore', 'Reguła ignorowania w .gitignore dotyczy tylko jednego z katalogów docs/specs i docs/plans; '
                                              'tryb nieśledzony wymaga obu (`/docs/specs/` i `/docs/plans/`) albo żadnego'))
    if info['tracked'] is False:
        tracked = _tracked_files(repo)
        if tracked:
            shown = ' '.join(_quote_arg(item) for item in tracked[:40])
            text = (f'{len(tracked)} plik(ów) w docs/specs lub docs/plans jest śledzonych mimo trybu nieśledzonego; '
                    f'odepnij je (pliki zostają na dysku): git -C {_quote_arg(str(Path(repo)))} rm --cached -- {shown}')
            if len(tracked) > 40:
                text += f'\n(i {len(tracked) - 40} więcej: uruchom `skillsync doctor` ponownie po tym poleceniu)'
            found.append(('warning', 'docs/specs', text))
    return found
