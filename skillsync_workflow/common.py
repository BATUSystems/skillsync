"""Shared local workflow configuration and report helpers."""
import copy
import errno
import fnmatch
import json
import math
import re
import os
from pathlib import Path, PurePosixPath
import subprocess
import yaml

from . import layers as _layers


def config_file():
    """Machine config path; an empty `SKILLSYNC_CONFIG=` means the default (as `skillsync:CONFIG_PATH`)."""
    return Path(os.environ.get('SKILLSYNC_CONFIG') or str(Path.home() / '.config/skillsync/config.yaml'))


def read_yaml(path):
    if not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f'Expected YAML mapping: {path}')
    return data


def _read_manifest(path, manifest_file=None):
    """`(root, manifest)`; `manifest_file` (`--manifest PLIK`) replaces `<root>/.github/.skillsync.yaml`: the project
    root stays `path`, the manifest comes from a file outside it (e.g. a public core repository without a manifest)."""
    path = Path(path or '.').expanduser().resolve()
    if manifest_file is not None:
        file = Path(manifest_file).expanduser()
        if not file.is_file():
            raise ValueError(f'--manifest {manifest_file}: not a file')
        if not path.is_dir():
            raise ValueError('--manifest needs a project directory as the path')
        root, manifest_path = path, file
    elif path.is_file():
        if path.name != '.skillsync.yaml':
            raise ValueError('Project file must be .skillsync.yaml')
        root, manifest_path = path.parent.parent, path
    else:
        root, manifest_path = path, path / '.github/.skillsync.yaml'
    if not manifest_path.is_file():
        raise ValueError(f'Missing manifest: {manifest_path}')
    manifest = read_yaml(manifest_path)
    if manifest_file is not None and not isinstance(manifest, dict):
        raise ValueError(f'--manifest {manifest_file}: must be a mapping')
    for section in ('sync', 'instructions', 'tests'):
        if section in manifest and not isinstance(manifest[section], dict):
            raise ValueError(f'{section} must be a mapping')
    if manifest.get('version', 1) != 1:
        raise ValueError('Unsupported manifest version')
    return root, manifest


def load_layer_project(path='.', layers=None, *, config=None, manifest_file=None):
    """(root, manifest, LayerSet): layers from the machine config (`layer_rules` by the project path) or
    the `--layer id=path` overrides. Raises `layers.LayerError` (BLOCKED) when nothing matches.
    The manifest's own library setting is not read (`doctor` warns about it, K3)."""
    root, manifest = _read_manifest(path, manifest_file)
    return root, manifest, _layers.resolve_layers(root, layers=list(layers or []) or None, config=config)


def read_project(path='.', manifest_file=None):
    """(root, manifest) without resolving layers: commands that never read a layer (`status`, `plan`,
    `claim`, `dispatch`, `checkpoint`...) do not depend on the machine config."""
    return _read_manifest(path, manifest_file)


def require_layerset(layerset, what='This function'):
    """The `LayerSet` itself; anything else (e.g. a library `Path`) is a TypeError (K1)."""
    if not isinstance(layerset, _layers.LayerSet):
        raise TypeError(f'{what} needs a LayerSet (resolve the layers with `load_layer_project`), '
                        f'got {type(layerset).__name__}')
    return layerset


def as_layerset(library):
    """`LayerSet` as is; a library `Path` as a one-layer `LayerSet` (`core` at that path, origin `path`,
    no K12 checks). Only for the `Path` form of `skillsync:expand_skills` (generator entry script,
    e.g. without the workflow package); workflow modules pass a resolved `LayerSet`."""
    if isinstance(library, _layers.LayerSet):
        return library
    path = Path(library).expanduser().resolve()
    config = _layers.parse_machine_config({}, config_file(), exists=False)
    return _layers.LayerSet((_layers.Layer('core', 'core', path),), config, 'path')


def safe_relative(name, label='path'):
    """`name` as a relative posix path without `..`, backslashes or an absolute prefix (ValueError)."""
    if not isinstance(name, str) or not name or '\\' in name or Path(name).is_absolute() \
            or re.match(r'[A-Za-z]:', name) or '..' in PurePosixPath(name).parts:
        raise ValueError(f'Expected a relative {label}: {name!r}')
    return PurePosixPath(name).as_posix()


def layer_file(layerset, rel):
    """File of the per-file path `rel` from the layer that owns it (later layer wins, links skipped,
    D73), or None when no active layer has it."""
    found = require_layerset(layerset, 'layer_file').resolve(safe_relative(rel))
    return found[1] if found else None


def deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _append_review_prototype(result, *levels):
    """`review.prototype` only grows: layers (already appended by `merged_defaults`), then the per-file project settings, then the
    manifest. A project adds items, never removes the core ones. Order: core, layers, project, manifest; duplicates (equal text,
    no normalisation) keep the first occurrence. A level whose value is not a list of nonempty strings (null, a scalar, a map, a
    non-text or empty item) is an error at that level: it is never skipped, because the plain merge would then hand the result to a
    later valid level and lose the core items without a word."""
    items, seen = [], set()
    for level in levels:
        review = level.get('review') if isinstance(level, dict) else None
        if not isinstance(review, dict) or 'prototype' not in review:
            continue
        value = review['prototype']
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError('review.prototype must be a list of nonempty strings')
        for item in value:
            if item not in seen:
                seen.add(item)
                items.append(item)
    if items:
        result.setdefault('review', {})['prototype'] = items


def workflow_config(root, manifest, layerset):
    """Effective workflow settings: merged `workflow/defaults.yaml` of the layers, the per-file
    `workflow/projects/<slug>.yaml` (later layer wins), then the manifest `workflow:`."""
    layerset = require_layerset(layerset, 'workflow_config')
    defaults = layerset.merged_file('workflow/defaults.yaml').data
    override = manifest.get('workflow', {})
    if not isinstance(defaults, dict) or not isinstance(override, dict):
        raise ValueError('workflow must be a mapping')
    project = manifest.get('project', {})
    if not isinstance(project, dict):
        raise ValueError('project must be a mapping')
    name = project.get('name')
    source = {}
    if name is not None:
        if not isinstance(name, str) or not name or '..' in name or name.startswith('/') or '\\' in name:
            raise ValueError('Unsafe project configuration name')
        slug = name.replace('/', '-')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', slug):
            raise ValueError('Unsafe project configuration slug')
        found = layerset.resolve(f'workflow/projects/{slug}.yaml')
        source = read_yaml(found[1]) if found else {}
        if 'workflow' in source:
            source = source['workflow']
        if not isinstance(source, dict):
            raise ValueError('Project workflow settings must be mapping')
    result = deep_merge(deep_merge(defaults, source), override)
    _append_review_prototype(result, defaults, source, override)
    validate_workflow(result)
    return result


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(str(path), str(pattern)) or (str(pattern).startswith('**/') and fnmatch.fnmatchcase(str(path), str(pattern)[3:])) for pattern in patterns)


def changed_files(root, base=None):
    root = Path(root)
    commands = [['git', 'diff', '--name-only', '-z', base, '--']] if base else [
        ['git', 'diff', '--name-only', '-z', 'HEAD', '--']]
    commands.append(['git', 'ls-files', '--others', '--exclude-standard', '-z'])
    files = set()
    for command in commands:
        process = subprocess.run(command, cwd=root, capture_output=True, timeout=15)
        if process.returncode:
            raise ValueError('Cannot inspect git changes; supply explicit --files')
        files.update(os.fsdecode(x) for x in process.stdout.split(b'\0') if x)
    return sorted(files)


def exit_code(report):
    return {'PASS': 0, 'NOT_RUN': 0, 'FAIL': 1, 'BLOCKED': 2, 'TIMEOUT': 3}.get(report.get('status'), 2)


def emit(report, json_output=False):
    print(json.dumps(redact(report), ensure_ascii=False, indent=2))
    return exit_code(report)


def add_project_arguments(parser):
    parser.add_argument('path', nargs='?', default='.')
    _layers.add_layer_argument(parser)
    parser.add_argument('--json', action='store_true')


def strings(value, label):
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f'{label} must be a list of nonempty strings')
    return value


def contained(root, path):
    root = Path(root).resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f'Path escapes project: {path}')
    return candidate


def normalize_files(root, files):
    """Project-relative POSIX names of a list of files (changed files from git, `--files`). The containment is checked on the path ITSELF: the
    parent directory is resolved physically (a directory that is a link to the outside is an escape), the last component is not followed, so a
    link that lies in the project (for example a tracked `.github/skills/<skill>` pointing into a layer) is a file of the project, not an escape.
    `contained` (follows every link) stays the check for the paths a command opens."""
    root = Path(root).resolve()
    return [project_file(root, path) for path in files]


def project_file(root, path):
    """Project-relative POSIX name of ONE file of a list (see `normalize_files`); ValueError "Path escapes project" for an escape."""
    root = Path(root).resolve()
    candidate = root / path
    if candidate.name in ('', '.', '..'):   # a directory or a `..` ending: the plain check
        return contained(root, path).relative_to(root).as_posix()
    entry = candidate.parent.resolve() / candidate.name
    if not entry.is_relative_to(root):
        raise ValueError(f'Path escapes project: {path}')
    return entry.relative_to(root).as_posix()


def validate_workflow(config):
    if config.get('version', 1) != 1:
        raise ValueError('Unsupported workflow version')
    for name in ('context', 'routing', 'validation', 'testing', 'readiness', 'review',
                 'environment', 'e2e', 'delivery', 'upgrade', 'skill_profiles', 'guardrails', 'knowledge', 'doctor'):
        if name in config and not isinstance(config[name], dict):
            raise ValueError(f'workflow.{name} must be a mapping')
    checks = config.get('validation', {}).get('checks', [])
    if not isinstance(checks, list):
        raise ValueError('validation.checks must be a list')
    ids = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError('Each validation check must be a mapping')
        identity = check.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', identity) or identity in ids:
            raise ValueError('Checks require unique safe ids')
        ids.add(identity)
        for key in ('profiles', 'paths', 'resources', 'requires_env', 'requires_executables', 'executables'):
            if key in check:
                strings(check[key], f'check.{key}')
        for key in ('required', 'allow_skips'):
            if key in check and not isinstance(check[key], bool):
                raise ValueError(f'check.{key} must be boolean')
        if 'cwd' in check and not isinstance(check['cwd'], str):
            raise ValueError('check.cwd must be string')
        kind = check.get('kind', 'test')
        if not isinstance(kind, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', kind):
            raise ValueError('check.kind must be safe string')
        timeout = check.get('timeout', 300)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 86400:
            raise ValueError('check.timeout must be finite between 0 and 86400')
        if check.get('success_pattern') is not None:
            if not isinstance(check['success_pattern'], str):
                raise ValueError('success_pattern must be a string')
            try:
                re.compile(check['success_pattern'])
            except re.error as error:
                raise ValueError(f'Invalid success_pattern: {error}') from None
    rules = config.get('routing', {}).get('rules', [])
    if not isinstance(rules, list):
        raise ValueError('routing.rules must be a list')
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError('routing rule must be a mapping')
        for key in ('paths', 'topics', 'instructions', 'skills'):
            if key in rule:
                strings(rule[key], f'routing.{key}')
    for section, key in [('readiness', 'risk_paths'), ('review', 'risks')]:
        mapping = config.get(section, {}).get(key, {})
        if not isinstance(mapping, dict):
            raise ValueError(f'{section}.{key} must be a mapping')
        for name, value in mapping.items():
            strings(value, f'{section}.{key}.{name}')
    for section, keys in [('readiness', ('required_sections', 'contract_paths')), ('review', ('core', 'project', 'ui_paths', 'ui_evidence', 'docs_paths', 'docs_checklist', 'prototype', 'prototype_paths'))]:
        for key in keys:
            if key in config.get(section, {}):
                strings(config[section][key], f'{section}.{key}')
    risk_sections = config.get('readiness', {}).get('risk_sections', {})
    if isinstance(risk_sections, dict):
        for key, value in risk_sections.items():
            strings(value, f'readiness.risk_sections.{key}')
    else:
        strings(risk_sections, 'readiness.risk_sections')


SECRET_MIN_LENGTH = 8


def plan_section(text, name):
    """Body of the `## <name>` section of a Markdown document (heading compared without case, up to the next `## ` heading;
    fenced blocks are skipped when looking for headings), stripped of blank lines at both ends; None when the section is absent.
    The one reader of plan sections (`## Wynik`, the plan summary): `dashboard`, `status` and the prototype tools use it."""
    wanted = ' '.join(str(name).split()).lower()
    lines, inside, in_fence, body = str(text).splitlines(), False, False, []
    for line in lines:
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        elif not in_fence and line.startswith('## '):
            if inside:
                break
            inside = ' '.join(line[3:].split()).lower() == wanted
            continue
        if inside:
            body.append(line)
    if not inside:
        return None
    return '\n'.join(body).strip('\n') if '\n'.join(body).strip() else ''


def detect_eol(text):
    """Line ending of the last line of `text` ("\\r\\n" or "\\n"); LF for an empty text or one without a newline."""
    last_lf = text.rfind("\n")
    return "\r\n" if last_lf > 0 and text[last_lf - 1] == "\r" else "\n"


def append_missing_lines(text, entries, key=None):
    """`text` with the `entries` that are not yet lines of it appended, or None when nothing is missing.

    The existing text stays byte for byte; the added lines use the line ending of the last line (LF for an
    empty text) and a missing final newline is completed in the same style. `key` normalizes lines before
    they are compared (default: exact match)."""
    key = key or (lambda line: line)
    present = {key(line) for line in text.splitlines()}
    missing = [entry for entry in dict.fromkeys(entries) if key(entry) not in present]
    if not missing:
        return None
    eol = detect_eol(text)
    prefix = "" if not text or text.endswith("\n") else eol
    return text + prefix + eol.join(missing) + eol


def decode_text(data):
    """Text of the bytes of a file: UTF-16 with a byte order mark (the default output of `>` and `Out-File` in Windows PowerShell 5) is
    decoded as such, anything else as UTF-8; NUL characters are dropped (the renderer drops them too, so a secret that a NUL splits
    would otherwise be glued together again AFTER the masking)."""
    text = data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8', errors='replace')
    return text.replace('\x00', '')


def redact(value, _key=False):
    """Masks secrets in strings. A secret from the environment is substituted only when it has 8+ characters, in keys and
    in text alike (D6): a short value (the Windows runner has one equal to `root`) must not rename structural keys or
    ordinary words. Pattern-based masking (URL credentials, `Authorization`, `password=`...) has no length floor."""
    if isinstance(value, dict):
        return {redact(key, True): redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if not isinstance(value, str):
        return value
    text = value
    for name, secret in os.environ.items():
        if len(secret) >= SECRET_MIN_LENGTH and re.search(r'(?i)token|secret|password|passwd|api_?key|credential', name):
            text = text.replace(secret, '[REDACTED]')
            if '\n' in secret:   # a multi-line secret (a PEM key) in a file with Windows line ends
                text = text.replace(secret.replace('\n', '\r\n'), '[REDACTED]')
    text = re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*(?:(?:bearer|basic|digest)\s+)?)[^\s\x22\x27,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(\bbearer\s+)[^\s\x22\x27,;]+', r'\1[REDACTED]', text)
    return _SECRET_KEY_VALUE.sub(_mask_value, text)


# Key names whose value is masked: `password`, `passwd`, `token`, `secret`, `api_key`/`api-key`/`apikey`, `secret_key`, `access_key`,
# `private_key` (a longer name that ends in one of them, e.g. `client_secret`, matches too). After the key: an optional quote (JSON),
# `=` or `:`, and a value that is quoted (the whole quoted text, spaces included, up to the first closing quote that no backslash escapes, so
# `\\"` inside a JSON string stays in the value and `\\\\"` closes it; an unterminated quote takes the rest of the line, before `\\n` or `\\r\\n`) or
# a word up to a space, `,` or `;` (quotes inside it belong to the value). Every part of the quoted alternatives starts with a different
# character, so the match is linear (no catastrophic backtracking).
_SECRET_KEY_VALUE = re.compile(
    r'(?im)((?:password|passwd|token|secret|api[-_]?key|secret[-_]?key|access[-_]?key|private[-_]?key)[\x22\x27]?\s*[=:]\s*)'
    r'(?:\x22(?:\\.|[^\x22\\\r\n])*(?:\x22|(?=\r?$))|\x27(?:\\.|[^\x27\\\r\n])*(?:\x27|(?=\r?$))|[^\s\x22\x27,;][^\s,;]*)')


def _mask_value(match):
    value = match.group(0)[len(match.group(1)):]
    quote = value[0] if value[:1] in ('"', "'") else ''
    escapes = len(value) - len(value[:-1].rstrip('\\')) - 1 if quote and len(value) > 1 else 0   # backslashes right before the last character
    closed = bool(quote) and len(value) > 1 and value.endswith(quote) and escapes % 2 == 0   # an unterminated quote masks the rest of the line and adds no closing one
    return f'{match.group(1)}{quote}[REDACTED]{quote if closed else ""}'


# Mirrors the constants in the `skillsync` entry script (a test keeps them identical).
WORKFLOW_SKILL = 'feature'
LEGACY_WORKFLOW_SKILL = 'agentflow'
WORKFLOW_COMPANIONS = ('workflow', 'pre-implement', 'smart-test', 'bugfix', 'codereview')
# Removed skills kept as manifest aliases (mirror of `skillsync:REMOVED_SKILL_ALIASES`).
REMOVED_SKILL_ALIASES = {'go-jira': 'acli'}
# Renamed skills whose old name resolves to the new one only while NO layer ships the old name any more:
# a layer that still has `skills/status-board` keeps it (and `prune_managed` keeps its links) until the layer
# is updated. Single source for both expanders: `cli.py:expand_skills` imports it from here.
CONDITIONAL_SKILL_ALIASES = {'status-board': 'dashboard'}


def conditional_skill_alias(name, available):
    """`name` after a conditional rename: the new name when the old one is in no layer and the new one is."""
    target = CONDITIONAL_SKILL_ALIASES.get(name)
    if target is not None and name not in available and target in available:
        return target
    return name
# Renamed roles kept as manifest aliases for one release (mirror of `skillsync:RENAMED_ROLE_ALIASES`).
RENAMED_ROLE_ALIASES = {'Explore': 'explore'}


def expand_project_skills(manifest, layerset):
    """Expanded skill list (mirror of `skillsync:expand_skills`) for a `LayerSet`.
    Skills = union of `skills/<n>/SKILL.md` across the layers."""
    from .maintenance import resolve_profile_skills
    layerset = require_layerset(layerset, 'expand_project_skills')
    specs = strings(manifest.get('skills', []), 'skills') + resolve_profile_skills(manifest, layerset)
    config = read_yaml(config_file())
    tags = config.get('tags', {})
    if not isinstance(tags, dict):
        raise ValueError('Skill tags must be a mapping')
    available = layerset.skill_names(require_skill_md=True)
    expanded = []
    for spec in specs:
        if spec.startswith('@'):
            names = strings(tags.get(spec[1:], []), f'tag {spec}')
        elif '*' in spec or '?' in spec:
            names = [name for name in available if fnmatch.fnmatch(name, spec)
                     and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) and '..' not in name]  # unsafe: skipped
        else:
            names = [spec]
        for name in names:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or '..' in name:
                raise ValueError(f'Unsafe skill name: {name}')
            if name == LEGACY_WORKFLOW_SKILL and WORKFLOW_SKILL in available:
                name = WORKFLOW_SKILL
            if name in REMOVED_SKILL_ALIASES and REMOVED_SKILL_ALIASES[name] in available:
                name = REMOVED_SKILL_ALIASES[name]
            name = conditional_skill_alias(name, available)
            if name not in expanded:
                expanded.append(name)
    if WORKFLOW_SKILL in expanded or LEGACY_WORKFLOW_SKILL in expanded:
        for name in WORKFLOW_COMPANIONS:
            if name in available and name not in expanded:
                expanded.append(name)
    return expanded


def resolve_loop_checked(path):
    """`Path(path).resolve()` that reports a symlink loop on every Python version.

    Older Pythons raise `OSError`/`RuntimeError` from `resolve()` on a loop; newer ones (POSIX from 3.13, Windows from
    3.12) return the path they got stuck at instead. A fully resolved path has no symlink among its components, so one
    that still has a link has not been resolved: a loop. A path that merely does not exist still resolves.
    A Windows junction is no symlink (`is_symlink()` is False) but loops the same way (measured: Python 3.12 and 3.13 return the stuck path
    of a loop of two junctions, 3.11 raises), so `os.path.isjunction` (3.12+) counts as a link.
    A junction whose target is gone (dangling) is not a loop: `resolve()` returns the missing target path (measured on 3.11-3.13), which has no link."""
    resolved = Path(path).resolve()
    isjunction = getattr(os.path, 'isjunction', None)
    for part in (resolved, *resolved.parents):
        if part.is_symlink() or (isjunction is not None and isjunction(part)):
            raise OSError(errno.ELOOP, 'Too many levels of symbolic links', str(path))
    return resolved


def discover_excludes(config):
    """Ścieżki z klucza `discover_exclude` configu maszyny (lista, `~` rozwijane, domyślnie pusta), jako rozwiązane `Path`.

    Zły typ wartości albo ścieżka, której nie da się rozwiązać, to `ValueError` ze stałym komunikatem."""
    value = config.get("discover_exclude")
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError("Konfiguracja maszyny: discover_exclude musi być listą niepustych ścieżek")
    try:  # wykluczenia rozwiązujemy raz; `~nieznany` i pętla dowiązań nie kończą się tracebackiem
        return [resolve_loop_checked(Path(item).expanduser()) for item in value]
    except (OSError, RuntimeError):
        raise ValueError("Konfiguracja maszyny: discover_exclude zawiera ścieżkę, której nie da się rozwiązać") from None


def is_excluded(path, excludes):
    """Czy `path` jest równa wykluczeniu (już rozwiązanemu przez `discover_excludes`) albo leży pod nim.

    Ścieżka, której nie da się rozwiązać, jest traktowana jak wykluczona (projekt pominięty, bez tracebacku)."""
    if not excludes:
        return False
    try:
        resolved = resolve_loop_checked(path)
    except (OSError, RuntimeError):
        return True
    return any(resolved == excluded or excluded in resolved.parents for excluded in excludes)
