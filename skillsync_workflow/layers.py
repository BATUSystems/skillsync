"""Layers: machine config, layer selection, file resolution and merge semantics.

A *layer* is a directory with the library layout (``skills/``, ``instructions/``,
``agents/``, ``workflow/``, ``codex.yaml``...) plus ``layer.yaml``. Layers are listed
only in the machine config (``layers``) and selected for a project only by its resolved
path (``layer_rules``). ``LayerSet`` is the single source of library paths for all
consumers (spec A, K1-K3, K9, K12).

This module has no side effects on import. Commands that write (``layers init``,
``layers add``, ``layers update``) do so only when invoked.
"""
import argparse
import copy
import fnmatch
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath

import yaml

# `common` is imported inside functions only, so that `common` can import this module (2.2a).

SCHEMA = 1
LAYER_SCHEMA = 1
LAYER_KINDS = ('core', 'org', 'personal')
DEFAULT_WRITABLE = {'core': False, 'org': True, 'personal': True}
TARGET_SCOPE = {'core': 'public', 'org': 'org-private', 'personal': 'private'}
LAYER_FILE = 'layer.yaml'
DEFAULT_IDENTITY = 'leakguard/identity.yaml'
FRAGMENTS_DIR = 'instructions/_base.d'
FRAGMENT_BEGIN = '<!-- SKILLSYNC:FRAGMENT begin {layer}/{name} -->'
FRAGMENT_END = '<!-- SKILLSYNC:FRAGMENT end {layer}/{name} -->'
_PRE_FRAGMENT = re.compile(r'0[0-9]-')
_ID = re.compile(r'[a-z0-9][a-z0-9-]*')
_SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
_SKIP_DIRS = {'.git', '__pycache__'}
SEVERITIES = ('blocked', 'error', 'warning', 'info')
CONFIG_KEYS = ('layers', 'layer_rules', 'isolation', 'leakguard', 'allow_override', 'legacy_library')

# Merge rules (spec A, "Tabela semantyki scalania"); see `merge_rule`.
CORE_ONLY_FILES = ('instructions/_base.md', 'workflow/schema.json', 'workflow/benchmark.yaml')
AGGREGATES = {
    'codex': ('codex.yaml', None),
    'defaults': ('workflow/defaults.yaml', None),
    'catalog': ('workflow/skill-catalog.yaml', 'workflow/skill-catalog.d'),
    'profiles': ('workflow/skill-profiles.yaml', 'workflow/skill-profiles.d'),
    'cause-keys': ('workflow/cause-keys.yaml', 'workflow/cause-keys.d'),
    'lessons-index': ('workflow/lessons/index.yaml', 'workflow/lessons/index.d'),
}
PER_FILE_RULES = ('skill', 'instruction', 'agent', 'per-file', 'lesson-file')
DEFAULTS_ID_LISTS = (('validation', 'checks'), ('guardrails', 'rules'))
DEFAULTS_APPEND_LISTS = (('routing', 'rules'), ('guardrails', 'checks'))
CODEX_UNION_LISTS = (('exclude_skills',),)


class LayerError(ValueError):
    """Invalid layer input or configuration. `status` is the command status (BLOCKED)."""

    def __init__(self, message, code='layer-error', status='BLOCKED'):
        super().__init__(message)
        self.code = code
        self.status = status


def generator_version():
    from . import __version__
    return __version__


# --------------------------------------------------------------------------- versions

def parse_semver(value, label='version'):
    """`X.Y.Z` -> (X, Y, Z); raises LayerError otherwise."""
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise LayerError(f'{label} must be a version X.Y.Z (got a {type(value).__name__})')
    return tuple(int(part) for part in value.split('.'))


def compare_versions(left, right):
    """-1/0/1 comparing two `X.Y.Z` strings."""
    a, b = parse_semver(left), parse_semver(right)
    return (a > b) - (a < b)


# --------------------------------------------------------------------------- platform paths

def current_system():
    """`Linux`, `Darwin` or `Windows` (tests patch this function)."""
    return platform.system() or 'Linux'


def _env_dir(name):
    value = os.environ.get(name)
    return Path(value) if value and os.path.isabs(value) else None


def data_home(system=None):
    """Per-user data directory of skillsync (layer checkouts live under `layers/`)."""
    system = system or current_system()
    if system == 'Windows':
        return (_env_dir('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'skillsync'
    if system == 'Darwin':
        return Path.home() / 'Library' / 'Application Support' / 'skillsync'
    return (_env_dir('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'skillsync'


def cache_home(system=None):
    """Per-user cache directory of skillsync (composed skills live under `composed/`)."""
    system = system or current_system()
    if system == 'Windows':
        return (_env_dir('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'skillsync' / 'cache'
    if system == 'Darwin':
        return Path.home() / 'Library' / 'Caches' / 'skillsync'
    return (_env_dir('XDG_CACHE_HOME') or Path.home() / '.cache') / 'skillsync'


def default_layers_dir(system=None):
    return data_home(system) / 'layers'


def default_layer_path(layer_id, system=None):
    return default_layers_dir(system) / layer_id


def composed_root(system=None):
    return cache_home(system) / 'composed'


def config_path():
    """Machine config path: `SKILLSYNC_CONFIG` or `~/.config/skillsync/config.yaml` (as `skillsync:CONFIG_PATH`)."""
    return Path(os.environ.get('SKILLSYNC_CONFIG') or (Path.home() / '.config' / 'skillsync' / 'config.yaml'))


def _expand(value, label):
    if not isinstance(value, str) or not value.strip():
        raise LayerError(f'{label} must be a nonempty string')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LayerError(f'{label} must be an absolute path (or start with ~): {value}')
    return Path(os.path.abspath(path))


# --------------------------------------------------------------------------- layer.yaml

def parse_layer_yaml(data, source=LAYER_FILE):
    """Validate `layer.yaml` data -> normalized dict (`version`/`core_min` may be None).

    Raises LayerError for a wrong type, an unsupported schema or bad values. Missing
    `version`/`core_min` are not errors here: `check_layers` reports them (BLOCKED).
    """
    if not isinstance(data, dict):
        raise LayerError(f'{source} must be a mapping')
    schema = data.get('schema')
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise LayerError(f'{source} requires integer `schema`')
    if schema > LAYER_SCHEMA:
        raise LayerError(f'{source} uses a newer `schema`; this generator reads schema {LAYER_SCHEMA}')
    if schema != LAYER_SCHEMA:
        raise LayerError(f'{source} has an unsupported `schema` (expected {LAYER_SCHEMA})')
    layer_id = data.get('id')
    if not isinstance(layer_id, str) or not _ID.fullmatch(layer_id):
        raise LayerError(f'{source}: `id` must match [a-z0-9][a-z0-9-]*')
    kind = data.get('kind')
    if kind not in LAYER_KINDS:
        raise LayerError(f'{source}: `kind` must be one of: {", ".join(LAYER_KINDS)}')
    result = {'schema': schema, 'id': layer_id, 'kind': kind, 'version': None, 'core_min': None,
              'requires': [], 'identity': None}
    for key in ('version', 'core_min'):
        if data.get(key) is not None:
            parse_semver(data[key], f'{source}: `{key}`')
            result[key] = data[key]
    requires = data.get('requires', [])
    if not isinstance(requires, list) or not all(isinstance(item, str) and _ID.fullmatch(item) for item in requires):
        raise LayerError(f'{source}: `requires` must be a list of layer ids')
    result['requires'] = list(requires)
    identity = data.get('identity')
    if identity is not None:
        result['identity'] = _safe_rel(identity, f'{source}: `identity`')
    return result


def read_layer_yaml(root):
    """`(meta, error)` for `<root>/layer.yaml`; `(None, None)` when the file is absent."""
    path = Path(root) / LAYER_FILE
    if is_link(path):
        return None, f'{path}: layer.yaml is a symlink/junction; not read'
    if not path.is_file():
        return None, None
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        return parse_layer_yaml(data, str(path)), None
    except LayerError as exc:
        return None, str(exc)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f'{path} is unreadable ({type(exc).__name__})'


def _safe_rel(value, label):
    if not isinstance(value, str) or not value.strip():
        raise LayerError(f'{label} must be a nonempty relative path')
    text = value.replace('\\', '/').strip('/')
    pure = PurePosixPath(text)
    if value.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', value) or '..' in pure.parts:
        raise LayerError(f'{label} must be a relative path inside the layer (no absolute path, no `..`)')
    return '' if text in ('', '.') else pure.as_posix()


# --------------------------------------------------------------------------- layer objects

@dataclass(eq=False)
class Layer:
    """One layer: `path` = checkout, `root` = `path/subdir` (where `layer.yaml` lives)."""
    id: str
    kind: str
    path: Path
    subdir: str = ''
    source: str = None
    ref: str = None
    writable: bool = None
    synthetic: dict = None

    def __post_init__(self):
        self.path = Path(self.path)
        if self.writable is None:
            self.writable = False if self.synthetic else DEFAULT_WRITABLE[self.kind]

    @property
    def root(self):
        return self.path / self.subdir if self.subdir else self.path

    @property
    def present(self):
        return self.root.is_dir()

    @property
    def root_clean(self):
        """False when an entry on the `subdir` path (checkout -> root) is a link (D73)."""
        return _clean(self.root, self.path)

    @cached_property
    def _meta(self):
        if not self.root_clean:
            return None, (f'Layer {self.id}: the path to the layer root ({self.subdir}) contains a symlink/junction'
                          '; the layer is not read')
        return read_layer_yaml(self.root)

    @property
    def meta(self):
        """Normalized `layer.yaml`, None when absent/invalid."""
        return self._meta[0]

    @property
    def meta_error(self):
        return self._meta[1]

    @property
    def version(self):
        return (self.meta or {}).get('version')

    def identity_file(self):
        """Declared `identity` of `layer.yaml`, else `leakguard/identity.yaml` when present.

        Fail-closed: an invalid `layer.yaml` raises LayerError (`layer-yaml-invalid`) instead
        of silently falling back to the default file."""
        if self.meta_error:
            raise LayerError(f'Layer {self.id}: invalid layer.yaml, identity file unknown ({self.meta_error})',
                             'layer-yaml-invalid')
        rel = (self.meta or {}).get('identity')
        for candidate in ([rel] if rel else []) + [DEFAULT_IDENTITY]:
            path = self.root / candidate
            if not _clean(path, self.root):  # D73: a link anywhere on the path = fail-closed
                raise LayerError(f'Layer {self.id}: identity file {candidate} is (or lies under) a symlink/junction; '
                                 'links are not allowed in layers', 'symlink-in-layer')
            if path.is_file():
                return path
        return None

    def argv_value(self):
        return f'{self.id}={self.root}'

    def as_json(self):
        return {'id': self.id, 'kind': self.kind, 'path': str(self.path), 'subdir': self.subdir,
                'version': self.version, 'writable': bool(self.writable), 'root': str(self.root),
                'synthetic': bool(self.synthetic), 'present': self.present}

    def __repr__(self):
        return f'Layer({self.id!r}, {self.kind!r}, {str(self.root)!r})'


def _parse_layer_entry(entry, index):
    label = f'layers[{index}]'
    if not isinstance(entry, dict):
        raise LayerError(f'{label} must be a mapping')
    layer_id = entry.get('id')
    if not isinstance(layer_id, str) or not _ID.fullmatch(layer_id):
        raise LayerError(f'{label}.id must match [a-z0-9][a-z0-9-]*')
    label = f'layers.{layer_id}'
    kind = entry.get('kind')
    if kind not in LAYER_KINDS:
        raise LayerError(f'{label}.kind must be one of: {", ".join(LAYER_KINDS)}')
    source = entry.get('source')
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise LayerError(f'{label}.source must be a URL or a path')
    if source is None and entry.get('path') is None:
        raise LayerError(f'{label} requires `source` or `path`')
    subdir = _safe_rel(entry['subdir'], f'{label}.subdir') if entry.get('subdir') not in (None, '', '.') else ''
    ref = entry.get('ref')
    if ref is not None and (not isinstance(ref, str) or not ref.strip() or ref.startswith('-')):
        raise LayerError(f'{label}.ref must be a tag, branch or commit')
    path = _expand(entry['path'], f'{label}.path') if entry.get('path') is not None else default_layer_path(layer_id)
    synthetic = entry.get('synthetic')
    if synthetic is not None:
        if kind != 'core':
            raise LayerError(f'{label}.synthetic is allowed only for the core bridge layer')
        if not isinstance(synthetic, dict):
            raise LayerError(f'{label}.synthetic must be a mapping {{version, core_min}}')
        for key in ('version', 'core_min'):
            parse_semver(synthetic.get(key), f'{label}.synthetic.{key}')
        synthetic = {'version': synthetic['version'], 'core_min': synthetic['core_min']}
    writable = entry.get('writable')
    if writable is not None and not isinstance(writable, bool):
        raise LayerError(f'{label}.writable must be boolean')
    if synthetic and writable:
        raise LayerError(f'{label}: the legacy bridge layer is read-only (writable: false)')
    return Layer(layer_id, kind, path, subdir, source, ref, writable, synthetic)


def _string_list(value, label, pattern=None):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise LayerError(f'{label} must be a list of nonempty strings')
    if pattern is not None:
        for item in value:
            if not pattern.fullmatch(item):
                raise LayerError(f'{label}: invalid id {item!r}')
    return list(value)


@dataclass
class MachineConfig:
    """Parsed machine config (only the layer keys are validated; the rest stays in `raw`)."""
    path: Path
    raw: dict
    exists: bool
    layers: dict = field(default_factory=dict)          # id -> Layer, config order
    rules: list = field(default_factory=list)           # [{'match': [...], 'use': [...]}]
    allowed_layers: list = None                         # isolation.allowed_layers (None = not configured)
    identity_dir: Path = None
    targets: dict = field(default_factory=dict)         # leakguard.targets (raw entries)
    trailer_allow: list = field(default_factory=list)
    allow_override: list = field(default_factory=list)
    legacy_library: Path = None
    warnings: list = field(default_factory=list)

    @property
    def legacy_library_key(self):
        """Value of the old `library:` key (D41: detected, never used as a layer)."""
        return legacy_library_key(self.raw)

    def ordered_layers(self):
        """All configured layers: core, org (config order), personal (config order)."""
        return [layer for kind in LAYER_KINDS for layer in self.layers.values() if layer.kind == kind]

    def targets_with_kind(self):
        """`leakguard.targets` with `kind` filled from `layers[].kind` (input for `leakguard`)."""
        result = {}
        for target_id, entry in self.targets.items():
            entry = dict(entry) if isinstance(entry, dict) else entry
            if isinstance(entry, dict) and entry.get('kind') is None and target_id in self.layers:
                entry['kind'] = self.layers[target_id].kind
            result[target_id] = entry
        return result


def legacy_library_key(raw):
    """The old machine-config key `library:` (pre-layers). Only for detection and migration."""
    value = raw.get('library') if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def parse_machine_config(raw, path=None, exists=True):
    """Validate the layer keys of a machine config mapping -> MachineConfig (raises LayerError)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise LayerError(f'Machine config must be a mapping: {path}')
    config = MachineConfig(Path(path) if path else config_path(), raw, exists)
    entries = raw.get('layers') or []
    if not isinstance(entries, list):
        raise LayerError('layers must be a list')
    for index, entry in enumerate(entries):
        layer = _parse_layer_entry(entry, index)
        if layer.id in config.layers:
            raise LayerError(f'Duplicate layer id: {layer.id}')
        config.layers[layer.id] = layer
    cores = [layer.id for layer in config.layers.values() if layer.kind == 'core']
    if len(cores) > 1:
        raise LayerError(f'Exactly one core layer is allowed, found: {", ".join(cores)}')
    rules = raw.get('layer_rules') or []
    if not isinstance(rules, list):
        raise LayerError('layer_rules must be a list')
    for index, rule in enumerate(rules):
        label = f'layer_rules[{index}]'
        if not isinstance(rule, dict):
            raise LayerError(f'{label} must be a mapping {{match, use}}')
        match = _string_list(rule.get('match'), f'{label}.match')
        use = _string_list(rule.get('use'), f'{label}.use', _ID)
        unknown = [item for item in use if item not in config.layers]
        if unknown:
            raise LayerError(f'{label}.use refers to unknown layers: {", ".join(unknown)}')
        if len(set(use)) != len(use):
            raise LayerError(f'{label}.use lists a layer twice')
        personal = [item for item in use if config.layers[item].kind == 'personal']
        if len(personal) > 1:
            raise LayerError(f'{label}.use has more than one personal layer: {", ".join(personal)}')
        config.rules.append({'match': match, 'use': use, 'index': index})
    isolation = raw.get('isolation')
    if isolation is not None:
        if not isinstance(isolation, dict):
            raise LayerError('isolation must be a mapping')
        if isolation.get('allowed_layers') is not None:
            config.allowed_layers = _string_list(isolation['allowed_layers'], 'isolation.allowed_layers', _ID)
    guard = raw.get('leakguard')
    if guard is not None:
        if not isinstance(guard, dict):
            raise LayerError('leakguard must be a mapping')
        for key in guard:
            if key not in ('identity_dir', 'targets', 'trailer_allow'):
                config.warnings.append(f'Unknown key leakguard.{key} ignored')
        if guard.get('identity_dir') is not None:
            config.identity_dir = _expand(guard['identity_dir'], 'leakguard.identity_dir')
        targets = guard.get('targets') or {}
        if not isinstance(targets, dict):
            raise LayerError('leakguard.targets must be a mapping')
        config.targets = targets
        if guard.get('trailer_allow') is not None:
            config.trailer_allow = _string_list(guard['trailer_allow'], 'leakguard.trailer_allow')
        from . import leakguard
        try:
            leakguard.validate_targets(config.targets_with_kind())
        except leakguard.LeakguardError as exc:
            raise LayerError(f'leakguard.targets: {exc}') from None
    if raw.get('allow_override') is not None:
        config.allow_override = _string_list(raw['allow_override'], 'allow_override')
    if raw.get('legacy_library') is not None:
        config.legacy_library = _expand(raw['legacy_library'], 'legacy_library')
    return config


def read_config_text(path=None):
    path = Path(path) if path else config_path()
    try:
        return path.read_text(encoding='utf-8') if path.is_file() else None
    except (OSError, UnicodeDecodeError) as exc:
        raise LayerError(f'Machine config is unreadable: {path} ({type(exc).__name__})') from None


def load_machine_config(path=None):
    """Read and parse the machine config; a missing file gives an empty config (`exists=False`)."""
    path = Path(path) if path else config_path()
    text = read_config_text(path)
    if text is None:
        return parse_machine_config({}, path, exists=False)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LayerError(f'Machine config is not valid YAML: {path} ({type(exc).__name__})') from None
    return parse_machine_config(raw, path, exists=True)


# --------------------------------------------------------------------------- --layer flag

_SYNTHETIC_REFUSED = '--layer ...,synthetic (the legacy bridge) is no longer supported; pass a real layer directory'


@dataclass(frozen=True)
class LayerOverride:
    """Parsed `--layer id=path` (path = layer root). `synthetic` exists only to refuse the 0.9.x bridge form."""
    id: str
    path: Path
    synthetic: bool = False

    def argv_value(self):
        return f'{self.id}={self.path}'


def parse_layer_flag(value, *, cwd=None):
    """Parse one `--layer` value; the old bridge form `id=path,synthetic` is refused (`synthetic-refused`)."""
    if isinstance(value, LayerOverride):
        if value.synthetic:
            raise LayerError(_SYNTHETIC_REFUSED, 'synthetic-refused')
        return value
    if not isinstance(value, str) or '=' not in value:
        raise LayerError(f'--layer expects id=path, got {value!r}', 'bad-layer-flag')
    layer_id, _, rest = value.partition('=')
    synthetic = False
    if rest.endswith(',synthetic'):
        rest, synthetic = rest[:-len(',synthetic')], True
    if not _ID.fullmatch(layer_id):
        raise LayerError(f'--layer id must match [a-z0-9][a-z0-9-]*: {layer_id!r}', 'bad-layer-flag')
    if not rest:
        raise LayerError(f'--layer {layer_id}= needs a path', 'bad-layer-flag')
    if synthetic:
        raise LayerError(_SYNTHETIC_REFUSED, 'synthetic-refused')
    path = Path(rest).expanduser()
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    return LayerOverride(layer_id, Path(os.path.abspath(path)), synthetic)


def add_layer_argument(parser):
    """Shared repeatable `--layer id=path` flag (replaces the layers for one command)."""
    parser.add_argument('--layer', dest='layers', action='append', default=[], metavar='ID=PATH',
                        help='Use this layer instead of the configured ones (repeatable, ascending precedence)')


def layer_argv(layerset):
    """argv for own child `skillsync` processes: `['--layer', 'id=root', ...]` (K3)."""
    argv = []
    for layer in layerset.layers:
        argv += ['--layer', layer.argv_value()]
    return argv


# --------------------------------------------------------------------------- path matching

def _darwin_key(text):
    """macOS (APFS/HFS+): case-insensitive and NFC/NFD-insensitive names."""
    return unicodedata.normalize('NFD', unicodedata.normalize('NFD', text).casefold())


def path_case_key(text, system=None):
    """A local path string for comparison, case-insensitive on Windows and macOS.

    One mapping: Windows = `normcase` (lower case) with `/` separators; macOS = Unicode NFD,
    `casefold`, NFD again (`normcase` keeps the case there; separators unchanged); Linux and
    others = unchanged (a `\\` stays part of a name). `system=None` = the host (`sys.platform`;
    the case step on Windows goes through `os.path.normcase`); an explicit `system`
    (`layer_rules`, `current_system()`) gives the result for that system without the host.
    Shared by `layer_rules` matching, `_path_key` (isolation keys), the D73 link ban in
    `leakguard_inputs` and the leakguard local-path comparisons (`remote_match`, `select_target`)."""
    if system is None:
        text = os.path.normcase(text)
        if sys.platform == 'win32':
            return text.replace('\\', '/')
        return _darwin_key(text) if sys.platform == 'darwin' else text
    if system == 'Windows':
        return text.replace('\\', '/').lower()
    return _darwin_key(text) if system == 'Darwin' else text


def _key_within(inner, outer):
    """`inner` equals `outer` or lies below it; both are `path_case_key` strings."""
    return inner == outer or inner.startswith(outer.rstrip('/') + '/')


def _norm_text(text, system):
    return path_case_key(text, system)


def _is_abs_text(text):
    return text.startswith('/') or bool(re.match(r'^[A-Za-z]:/', text))


def normalize_path(path, system=None):
    """Resolved path as a `/`-separated string, case-folded on Windows and macOS."""
    system = system or current_system()
    return _norm_text(os.path.realpath(os.path.expanduser(str(path))), system)


def _segment_regex(segment):
    out = []
    for char in segment:
        if char == '*':
            out.append('[^/]*')
        elif char == '?':
            out.append('[^/]')
        else:
            out.append(re.escape(char))
    return ''.join(out)


def glob_regex(pattern, system=None):
    """Compile a `layer_rules` glob: `~` expanded, literal prefix resolved, `*`/`?` in a segment,
    `**` any number of segments (like `.gitignore`); a relative pattern may match anywhere."""
    system = system or current_system()
    text = os.path.expanduser(pattern)
    text = text.replace('\\', '/') if system == 'Windows' else text
    if text == '**':
        return re.compile(r'.*', re.S)
    segments = text.split('/')
    absolute = _is_abs_text(text)
    if absolute:
        count = next((index for index, segment in enumerate(segments) if any(char in segment for char in '*?')),
                     len(segments))
        literal = '/'.join(segments[:count]) or '/'
        if os.path.isabs(literal):  # resolve symlinks in the literal prefix (only for native paths)
            resolved = os.path.realpath(literal).replace('\\', '/')
            rest = segments[count:]
            text = resolved if not rest else resolved.rstrip('/') + '/' + '/'.join(rest)
    text = _norm_text(text, system)
    segments = text.split('/')
    parts = [] if absolute or segments[0] == '**' else ['(?:.*/)?']
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == '**':
            if index == 0:
                parts.append('(?:.*/)?')  # leading `**/`: any prefix, including the root `/` or `C:/`
            else:
                parts.append('.+' if last else '(?:[^/]+/)*')
            continue
        parts.append(_segment_regex(segment))
        if not last:
            parts.append('/')
    return re.compile(''.join(parts), re.S)


def path_matches(path, pattern, system=None):
    """True when the resolved `path` matches the `layer_rules` glob `pattern`."""
    system = system or current_system()
    return bool(glob_regex(pattern, system).fullmatch(normalize_path(path, system)))


def _git_text(cwd, *args, timeout=30):
    try:
        process = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                                 timeout=timeout, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C',
                                                       'LANGUAGE': ''})
    except (OSError, subprocess.SubprocessError):
        return None
    return process


def main_worktree(path):
    """Path of the main repository when `path` is inside a linked git worktree, else None."""
    common = _git_text(path, 'rev-parse', '--git-common-dir')
    own = _git_text(path, 'rev-parse', '--git-dir')
    if not common or not own or common.returncode or own.returncode:
        return None
    common_dir = (Path(path) / common.stdout.strip()).resolve()
    own_dir = (Path(path) / own.stdout.strip()).resolve()
    if common_dir == own_dir:
        return None
    return common_dir.parent if common_dir.name == '.git' else common_dir


def find_project_root(start):
    """Nearest directory (start or a parent) with `.github/.skillsync.yaml`, else None."""
    current = Path(start).expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / '.github' / '.skillsync.yaml').is_file():
            return candidate
    return None


def match_rule(config, path, system=None):
    """First `layer_rules` entry matching `path` (worktree: main repo path first) -> (rule, matched path)."""
    system = system or current_system()
    candidates = []
    main = main_worktree(path)
    if main is not None:
        candidates.append(main)
    candidates.append(Path(path))
    for candidate in candidates:
        for rule in config.rules:
            if any(path_matches(candidate, pattern, system) for pattern in rule['match']):
                return rule, Path(os.path.realpath(candidate))
    return None, None


# --------------------------------------------------------------------------- LayerSet

def _finding(code, severity, message, layer=None):
    return {'code': code, 'layer': layer, 'message': message, 'severity': severity}


@dataclass(eq=False)
class LayerSet:
    """Active layers for one command, ascending precedence (later wins)."""
    layers: tuple
    config: MachineConfig
    origin: str = 'rule'                 # rule | override | all
    project: Path = None
    rule: dict = None
    matched_path: Path = None
    notes: list = field(default_factory=list)   # findings from resolution (see `check_layers`)

    # ---- identity
    @property
    def ids(self):
        return [layer.id for layer in self.layers]

    def get(self, layer_id):
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        return None

    def index(self, layer_id):
        return self.ids.index(layer_id)

    @property
    def core(self):
        return next((layer for layer in self.layers if layer.kind == 'core'), None)

    @property
    def personal(self):
        return next((layer for layer in self.layers if layer.kind == 'personal'), None)

    @property
    def roots(self):
        return [layer.root for layer in self.layers]

    def argv(self):
        return layer_argv(self)

    # ---- files
    def providers(self, rel, kind='file'):
        """[(Layer, Path)] of layers that have `rel` (ascending precedence)."""
        rel = _rel(rel)
        found = []
        for layer in self.layers:
            path = layer.root / rel
            present = (kind == 'file' and path.is_file()) or (kind == 'dir' and path.is_dir()) or (kind == 'any' and path.exists())
            if present and layer.root_clean and _clean(path, layer.root):  # D73: links are never read
                found.append((layer, path))
        return found

    def resolve(self, rel):
        """(owner Layer, Path) of a per-file `rel` according to the merge table, or None.

        Aggregated files (`codex.yaml`, `workflow/defaults.yaml`, catalogs...) raise
        LayerError: use `merged_file`. Not composed files (`layer.yaml`, README...) give None.
        """
        rel = _rel(rel)
        rule = merge_rule(rel)
        if rule in ('not-composed', 'base-fragment'):
            return None
        if rule in AGGREGATES:
            raise LayerError(f'{rel} is merged across layers; use merged_file()', 'aggregate-file')
        found = self.providers(rel)
        if rule == 'core-only':
            found = [item for item in found if item[0].kind == 'core']
        return found[-1] if found else None

    def listing(self, rel_dir, suffix=None, dirs=False):
        """{name: (owner Layer, Path)} of entries in `rel_dir` across layers, later wins."""
        result = {}
        for layer, directory in self.providers(rel_dir, 'dir'):
            for entry in _entries(layer, directory):
                if (dirs and not entry.is_dir()) or (not dirs and not entry.is_file()):
                    continue
                if suffix and not entry.name.endswith(suffix):
                    continue
                result[entry.name] = (layer, entry)
        return dict(sorted(result.items()))

    def skill_names(self, require_skill_md=True):
        names = set()
        for layer, directory in self.providers('skills', 'dir'):
            for entry in _entries(layer, directory):
                if entry.is_dir() and (not require_skill_md or ((entry / 'SKILL.md').is_file()
                                                                and not is_link(entry / 'SKILL.md'))):
                    names.add(entry.name)
        return sorted(names)

    def skill_providers(self, name):
        return [layer for layer, _ in self.providers(f'skills/{name}', 'dir')]

    def skill_files(self, name):
        """{relative path in the skill: (owner Layer, Path)}; per file the later layer wins.

        Built from `layer_files` (links skipped, D73); the composite and copy mode (2.2a) must
        use this method (or `layer_files`), never their own directory walk. `name` must be one safe
        path segment (no `..`, separators or absolute paths): LayerError otherwise."""
        _skill_segment(name)
        result = {}
        for layer in self.layers:
            for rel in layer_files(layer, f'skills/{name}'):
                result[rel] = (layer, layer.root / 'skills' / name / rel)
        return dict(sorted(result.items()))

    def skill_new_file_owner(self, name):
        """Layer receiving a new file of skill `name` (K4): last provider of `SKILL.md`, else personal."""
        found = self.providers(f'skills/{name}/SKILL.md')
        return found[-1][0] if found else self.personal

    def instruction_files(self):
        return {name: value for name, value in self.listing('instructions', '.md').items() if name != '_base.md'}

    def agent_files(self):
        return self.listing('agents', '.md')

    def base_fragments(self):
        """Fragments of `instructions/_base.d/*.md` in order: `00-`..`09-` (before the core base)
        then the rest; within a group by layer order, then file name."""
        fragments = []
        for position, layer in enumerate(self.layers):
            directory = layer.root / FRAGMENTS_DIR
            if not directory.is_dir():
                continue
            for entry in _entries(layer, directory):
                if entry.is_file() and entry.name.endswith('.md'):
                    pre = bool(_PRE_FRAGMENT.match(entry.name))
                    fragments.append({'layer': layer.id, 'name': entry.name, 'path': entry,
                                      'position': 'pre' if pre else 'post', '_order': (0 if pre else 1, position, entry.name)})
        fragments.sort(key=lambda item: item['_order'])
        for item in fragments:
            del item['_order']
        return fragments

    def merged_file(self, rel):
        """MergeResult for an aggregated file (see AGGREGATES)."""
        rule = merge_rule(_rel(rel))
        if rule not in AGGREGATES:
            raise LayerError(f'{rel} is not an aggregated file', 'not-aggregate')
        return MERGERS[rule](self)

    def lesson_file(self, path):
        return self.resolve(f'workflow/lessons/{path}')

    # ---- reports
    def to_list_json(self, system=None):
        """`layers list --json` (K14) plus additive fields."""
        return {'schema': SCHEMA, 'status': 'PASS', 'generator_version': generator_version(),
                'layers': [layer.as_json() for layer in self.layers],
                'composed_root': str(composed_root(system)), 'origin': self.origin,
                'project': str(self.project) if self.project else None,
                'rule': self.rule['index'] if self.rule else None,
                'notes': list(self.notes)}


_SKILL_SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


def _skill_segment(name):
    """A skill name as one path segment (same rule as the skill expansion); LayerError otherwise."""
    if not isinstance(name, str) or not _SKILL_SEGMENT.fullmatch(name) or '..' in name:
        raise LayerError(f'Unsafe skill name: {name!r}', 'bad-argument')
    return name


def _rel_strict(rel):
    """`_rel` that also rejects absolute paths (POSIX, drive letters, UNC) instead of stripping them."""
    text = str(rel)
    if text.startswith(('/', '\\')) or re.match(r'[A-Za-z]:', text):
        raise LayerError(f'Path escapes the layer: {rel}')
    return _rel(text)


def _rel(rel):
    text = str(rel).replace('\\', '/').strip('/')
    if '..' in PurePosixPath(text).parts:
        raise LayerError(f'Path escapes the layer: {rel}')
    return text


_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def is_link(path):
    """True for a symlink, a junction or another reparse point (D73: not allowed in layers).

    POSIX: `os.lstat` + `S_ISLNK`; Windows also `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`
    and `os.path.isjunction` (Python 3.12+; absent on 3.11, where the attribute covers it)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _REPARSE_POINT:
        return True
    isjunction = getattr(os.path, 'isjunction', None)
    return bool(isjunction and isjunction(path))


def _clean(path, root):
    """True when no entry between `root` (exclusive) and `path` (inclusive) is a link."""
    path, root = Path(path), Path(root)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    current = root
    for part in parts:
        current = current / part
        if is_link(current):
            return False
    return True


def _entries(layer, directory):
    """Sorted entries of `directory` of `layer` without `.git`/`__pycache__` and without links."""
    if not layer.root_clean or not _clean(directory, layer.root):
        return []
    try:
        items = sorted(Path(directory).iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    return [item for item in items if item.name not in _SKIP_DIRS and not is_link(item)]


def _walk_files(directory, links=None):
    """Relative posix paths of regular files under `directory` (`followlinks=False`).

    D73: a link (symlink, junction, reparse point) is never read; a linked directory excludes
    its whole subtree. Paths of skipped links (relative to `directory`) go to `links`."""
    directory = Path(directory)
    found = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        kept = []
        for name in sorted(dirs):
            if name in _SKIP_DIRS:
                continue
            if is_link(base / name):
                if links is not None:
                    links.append((base / name).relative_to(directory).as_posix())
                continue
            kept.append(name)
        dirs[:] = kept
        for name in files:
            path = base / name
            if is_link(path):
                if links is not None:
                    links.append(path.relative_to(directory).as_posix())
                continue
            if not name.endswith('.pyc'):
                found.append(path.relative_to(directory).as_posix())
    return sorted(found)


def layer_files(layer, rel_dir=''):
    """API for all readers of a layer tree (generator, trust model, composite and copy mode in
    2.2a): relative paths of regular files under `<root>/<rel_dir>`, links skipped (D73). A link
    on the path to `rel_dir` itself gives no files."""
    rel_dir = _rel_strict(rel_dir) if rel_dir else ''
    directory = layer.root / rel_dir if rel_dir else layer.root
    if not layer.root_clean or not directory.is_dir() or not _clean(directory, layer.root):
        return []
    return _walk_files(directory)


def layer_links(layer):
    """D73 violations: paths (relative to the checkout `layer.path`) of links.

    - inside the layer root (`path/subdir`) and on the `subdir` path itself: every link,
      tracked or not (these are what readers would see);
    - elsewhere in the checkout: only links tracked by git (`git ls-files -s -z`, mode 120000),
      so untracked/ignored artefacts (e.g. `.claude/skills/*` written by `skillsync link`) are
      not violations; a checkout without git: every link outside `.git`.
    """
    if not layer.path.is_dir():
        return []
    links = set()
    prefix = f'{layer.subdir}/' if layer.subdir else ''
    current = layer.path
    for part in Path(layer.subdir).parts if layer.subdir else ():
        current = current / part
        if is_link(current):
            links.add(current.relative_to(layer.path).as_posix())
            break
    if layer.root_clean and layer.root.is_dir():
        found = []
        _walk_files(layer.root, found)
        links.update(prefix + rel for rel in found)
    if layer.subdir:
        listed = _git_text(layer.path, 'ls-files', '-s', '-z')
        if listed is not None and listed.returncode == 0:
            for record in listed.stdout.split('\0'):
                meta, _, rel = record.partition('\t')
                if meta.startswith('120000 ') and rel:
                    links.add(rel)
        else:
            found = []
            _walk_files(layer.path, found)
            links.update(found)
    return sorted(links)


def _manifest_name(project):
    path = Path(project) / '.github' / '.skillsync.yaml'
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    name = (data or {}).get('project', {}).get('name') if isinstance(data, dict) and isinstance(data.get('project', {}), dict) else None
    return name if isinstance(name, str) and name else None


def _no_layers_error(config):
    if not config.exists:
        return LayerError(f'Missing machine config: {config.path}. Create layers with `skillsync layers init`/'
                          '`layers add`, or run `skillsync layers migrate`', 'missing-config')
    if config.legacy_library_key:
        return LayerError('Machine config uses the legacy `library:` key, which this generator no longer reads. '
                          'Run `skillsync layers migrate --layer-source core=PATH` (dry-run by default) to convert it '
                          'to `layers`.', 'legacy-config')
    return LayerError('No layers configured. Add one with `skillsync layers add` or run `skillsync layers migrate`.',
                      'no-layers')


def _check_bridge(layer):
    """A config entry with `synthetic` (the legacy bridge of 0.9.x) is refused: BLOCKED with the migration."""
    if layer.synthetic:
        raise LayerError(f'Layer {layer.id} is the legacy bridge (`synthetic`), which this generator no longer '
                         'supports. Run `skillsync layers migrate --layer-source core=PATH --apply` to replace it '
                         'with a real core layer.', 'legacy-config')


def _order_notes(layers, notes):
    kinds = [layer.kind for layer in layers]
    if 'core' in kinds and kinds[0] != 'core':
        notes.append(_finding('core-not-first', 'warning', 'The core layer should be first in `use`', 'core'))
    if 'personal' in kinds and kinds[-1] != 'personal':
        personal = next(layer.id for layer in layers if layer.kind == 'personal')
        notes.append(_finding('personal-not-last', 'warning', 'The personal layer should be last in `use`', personal))


def resolve_layers(project=None, *, layers=None, config=None, cwd=None, system=None):
    """Resolve the active LayerSet.

    - `layers` (`--layer` values) replaces the configured layers for one command;
    - `project`: the first `layer_rules` entry matching the resolved project path (a linked
      worktree matches by its main repository first); no rule = LayerError `no-rule` (BLOCKED);
    - no project: the rule for `cwd` (or its project); inside a project without a rule =
      BLOCKED; outside any project and without a rule: all layers (core, org, personal).
    Absent layer directories are skipped with an `info` note (`layer-absent`).
    """
    system = system or current_system()
    config = config if config is not None else load_machine_config()
    notes = []
    if layers:
        return _override_set(layers, config, notes, project)
    if not config.layers:
        raise _no_layers_error(config)
    origin, rule, matched = 'rule', None, None
    if project is not None:
        project = Path(project).expanduser()
        if project.is_file() and project.name == '.skillsync.yaml':
            project = project.parent.parent
        if not project.exists():
            raise LayerError(f'Project path does not exist: {project}', 'bad-project')
        project = project.resolve()
        rule, matched = match_rule(config, project, system)
        if rule is None:
            raise LayerError(f'No layer_rules entry matches {project}. Add one with '
                             '`skillsync layers use <id> --match <glob>` (or a default rule `match: ["**"]`).', 'no-rule')
    else:
        here = Path(cwd or os.getcwd()).resolve()
        found = find_project_root(here)
        rule, matched = match_rule(config, found or here, system)
        if rule is None and found is not None:
            raise LayerError(f'No layer_rules entry matches {found}. Add one with '
                             '`skillsync layers use <id> --match <glob>`.', 'no-rule')
        project = found
        if rule is None:
            origin = 'all'
    chosen = [config.layers[item] for item in rule['use']] if rule else config.ordered_layers()
    active = []
    for layer in chosen:
        _check_bridge(layer)
        if layer.present:
            active.append(layer)
        else:
            notes.append(_finding('layer-absent', 'info', f'Layer {layer.id} is not present at {layer.root}; skipped', layer.id))
    if rule:
        _order_notes(chosen, notes)
    if project is not None and rule is not None:
        name = _manifest_name(project)
        if name:
            paths = [normalize_path(item, system) for item in {project, matched} if item]
            wanted = '/' + _norm_text(name.strip('/'), system)
            if not any(item.endswith(wanted) for item in paths):
                notes.append(_finding('project-name-mismatch', 'warning',
                                      f'project.name {name!r} does not match the project path; layers are chosen '
                                      'by path only'))
    return LayerSet(tuple(active), config, origin, project, rule, matched, notes)


def _override_set(values, config, notes, project):
    active, seen = [], set()
    for value in values:
        override = parse_layer_flag(value)
        if override.id in seen:
            raise LayerError(f'--layer {override.id} given twice', 'bad-layer-flag')
        seen.add(override.id)
        known = config.layers.get(override.id)
        meta, _ = read_layer_yaml(override.path)
        kind = known.kind if known else (meta or {}).get('kind') or ('core' if override.id == 'core' else None)
        if kind is None:
            raise LayerError(f'--layer {override.id}: kind unknown (no config entry and no layer.yaml)', 'bad-layer-flag')
        writable = known.writable if known and not known.synthetic else None
        layer = Layer(override.id, kind, override.path, '', known.source if known else None, None, writable)
        if layer.present:
            active.append(layer)
        else:
            notes.append(_finding('layer-absent', 'info', f'Layer {layer.id} is not present at {layer.root}; skipped', layer.id))
    personal = [layer.id for layer in active if layer.kind == 'personal']
    if len(personal) > 1:
        raise LayerError(f'More than one personal layer: {", ".join(personal)}', 'bad-layer-flag')
    cores = [layer.id for layer in active if layer.kind == 'core']
    if len(cores) > 1:
        raise LayerError(f'More than one core layer: {", ".join(cores)}', 'bad-layer-flag')
    _order_notes(active, notes)
    if project is not None:
        project = Path(project).expanduser().resolve()
    return LayerSet(tuple(active), config, 'override', project, None, None, notes)


# --------------------------------------------------------------------------- merge semantics

def merge_rule(rel):
    """Row of the merge table for a path relative to a layer root.

    Values: `not-composed`, `core-only`, `base-fragment`, `instruction`, `skill`, `agent`,
    `per-file`, `lesson-file` and the AGGREGATES keys (`codex`, `defaults`, `catalog`,
    `profiles`, `cause-keys`, `lessons-index`).
    """
    rel = _rel(rel)
    parts = rel.split('/')
    first = parts[0]
    if (rel == LAYER_FILE or first in ('leakguard', '.leakguard', '.git', '.github')
            or (len(parts) == 1 and re.match(r'(?i)(README|CHANGELOG|NOTICE|LICENSE)', rel))
            or (len(parts) == 1 and rel.startswith('.git'))):
        return 'not-composed'
    if rel in CORE_ONLY_FILES or first == 'hooks':
        return 'core-only'
    if rel.startswith(FRAGMENTS_DIR + '/'):
        return 'base-fragment'
    for name, (base, directory) in AGGREGATES.items():
        if rel == base or (directory and rel.startswith(directory + '/')):
            return name
    if first == 'instructions' and len(parts) == 2 and rel.endswith('.md'):
        return 'instruction'
    if first == 'skills':
        return 'skill'
    if first == 'agents':
        return 'agent'
    if rel.startswith('workflow/lessons/'):
        return 'lesson-file'
    return 'per-file'


@dataclass
class MergeResult:
    """Merged data of an aggregated file.

    `owners`: element key -> layer id (keys as in `allow_override`, e.g.
    `validation.checks:<id>`, `codex.yaml:agents.reviewer.sandbox_mode`); `sources`:
    [(layer id, Path)] in merge order; `overrides`: [(key, previous layer, new layer)] when a
    later layer replaced an element with a different value (informational, "later wins");
    the trust model does not use it (see `trust_findings`, which compares with core values);
    `conflicts`: findings (e.g. `alias-collision`).
    """
    data: dict
    owners: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    overrides: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


def _load_mapping(path, layer_id):
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LayerError(f'Layer {layer_id}: {path} is unreadable ({type(exc).__name__})', 'merge-invalid') from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LayerError(f'Layer {layer_id}: {path} must be a mapping', 'merge-invalid')
    return data


def _aggregate_sources(layerset, name):
    base, directory = AGGREGATES[name]
    sources = []
    for layer in layerset.layers:
        path = layer.root / base
        if path.is_file() and layer.root_clean and _clean(path, layer.root):
            sources.append((layer, path))
        if directory and (layer.root / directory).is_dir():
            for entry in _entries(layer, layer.root / directory):
                if entry.is_file() and entry.suffix in ('.yaml', '.yml'):
                    sources.append((layer, entry))
    return sources


def _content_key(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)


def _merge_owned(base, over, layer_id, prefix, result, special):
    """Deep merge `over` into `base` (maps merged, lists replaced) recording leaf owners."""
    for key, value in over.items():
        path = prefix + (key,)
        dotted = '.'.join(str(part) for part in path)
        rule = special(path)
        current = base.get(key)
        if rule == 'id-list' and isinstance(value, list):
            base[key] = _merge_id_list(current if isinstance(current, list) else [], value, layer_id, dotted, result)
        elif rule in ('append', 'union') and isinstance(value, list):
            items = list(current) if isinstance(current, list) else []
            seen = {_content_key(item) for item in items}
            for item in value:
                if _content_key(item) not in seen:
                    seen.add(_content_key(item))
                    items.append(copy.deepcopy(item))
            base[key] = items
        elif isinstance(value, dict):
            if not isinstance(current, dict):
                _record_override(result, result['prefix'] + dotted, layer_id, key in base and current != value)
                base[key] = {}
            _merge_owned(base[key], value, layer_id, path, result, special)
        else:
            owner_key = result['prefix'] + dotted
            _record_override(result, owner_key, layer_id, key in base and current != value)
            base[key] = copy.deepcopy(value)
            result['owners'][owner_key] = layer_id


def _record_override(result, owner_key, layer_id, changed):
    previous = result['owners'].get(owner_key)
    if changed and previous is not None and previous != layer_id:
        result['overrides'].append((owner_key, previous, layer_id))


def _merge_id_list(current, value, layer_id, dotted, result):
    items = [copy.deepcopy(item) for item in current]
    positions = {item.get('id'): index for index, item in enumerate(items) if isinstance(item, dict) and isinstance(item.get('id'), str)}
    for item in value:
        identity = item.get('id') if isinstance(item, dict) else None
        if not isinstance(identity, str):
            items.append(copy.deepcopy(item))
            continue
        key = f'{dotted}:{identity}'
        if identity in positions:
            _record_override(result, key, layer_id, items[positions[identity]] != item)
            items[positions[identity]] = copy.deepcopy(item)
        else:
            positions[identity] = len(items)
            items.append(copy.deepcopy(item))
        result['owners'][key] = layer_id
    return items


def _merge_generic(layerset, name, special, prefix, unwrap=None):
    merged = MergeResult({})
    state = {'owners': merged.owners, 'overrides': merged.overrides, 'prefix': prefix}
    for layer, path in _aggregate_sources(layerset, name):
        data = _load_mapping(path, layer.id)
        if unwrap and unwrap in data and isinstance(data[unwrap], dict):
            data = data[unwrap]
        merged.sources.append((layer.id, path))
        _merge_owned(merged.data, data, layer.id, (), state, special)
    return merged


def merged_defaults(layerset):
    """`workflow/defaults.yaml`: maps merged, lists replaced; `validation.checks[]` and
    `guardrails.rules[]` joined by `id`; `routing.rules[]` and `guardrails.checks[]` appended
    in layer order with deduplication by content. Owner keys: `<section>.<list>:<id>`."""
    def special(path):
        if path in DEFAULTS_ID_LISTS:
            return 'id-list'
        if path in DEFAULTS_APPEND_LISTS:
            return 'append'
        return None
    return _merge_generic(layerset, 'defaults', special, '', unwrap='workflow')


def merged_codex(layerset):
    """`codex.yaml`: maps merged deeply, `exclude_skills` = union; owner keys `codex.yaml:<dotted>`."""
    return _merge_generic(layerset, 'codex', lambda path: 'union' if path in CODEX_UNION_LISTS else None, 'codex.yaml:')


def _merge_named_maps(layerset, name, sections, prefix):
    merged = MergeResult({})
    for layer, path in _aggregate_sources(layerset, name):
        data = _load_mapping(path, layer.id)
        merged.sources.append((layer.id, path))
        for key, value in data.items():
            if key in sections and isinstance(value, dict):
                target = merged.data.setdefault(key, {})
                if not isinstance(target, dict):
                    target = merged.data[key] = {}
                for item, entry in value.items():
                    owner = f'{prefix}:{key}.{item}'
                    previous = merged.owners.get(owner)
                    if previous is not None and previous != layer.id and target.get(item) != entry:
                        merged.overrides.append((owner, previous, layer.id))
                    target[item] = copy.deepcopy(entry)
                    merged.owners[owner] = layer.id
            else:
                merged.data[key] = copy.deepcopy(value)
    return merged


def merged_skill_catalog(layerset):
    """`skill-catalog.yaml` + `skill-catalog.d/*.yaml`: union of `skills` and `orgs`; key collision: later."""
    return _merge_named_maps(layerset, 'catalog', ('skills', 'orgs'), 'skill-catalog')


def merged_skill_profiles(layerset):
    """`skill-profiles.yaml` + `.d/`: per profile (and `default`) union of skill lists, deduplicated."""
    merged = MergeResult({})
    for layer, path in _aggregate_sources(layerset, 'profiles'):
        data = _load_mapping(path, layer.id)
        merged.sources.append((layer.id, path))
        for key, value in data.items():
            if key == 'default' and isinstance(value, list):
                target = merged.data.setdefault('default', [])
                target.extend(item for item in value if item not in target)
            elif key == 'profiles' and isinstance(value, dict):
                profiles = merged.data.setdefault('profiles', {})
                for profile, skills in value.items():
                    target = profiles.setdefault(profile, [])
                    for item in skills if isinstance(skills, list) else []:
                        if item not in target:
                            target.append(item)
                    merged.owners.setdefault(f'skill-profiles:{profile}', layer.id)
            else:
                merged.data[key] = copy.deepcopy(value)
    return merged


def merged_cause_keys(layerset):
    """`cause-keys.yaml` + `.d/`: union of keys and aliases; a duplicate key: the later layer's
    `description`; an alias claimed by two keys (or equal to a key) across layers is a conflict
    (`alias-collision`, error in `layers check`) and is not added."""
    merged = MergeResult({})
    aliases = {}   # alias -> (key, layer)
    owners = {}    # key -> layer
    for layer, path in _aggregate_sources(layerset, 'cause-keys'):
        data = _load_mapping(path, layer.id)
        merged.sources.append((layer.id, path))
        for top, value in data.items():
            if top != 'keys':
                merged.data[top] = copy.deepcopy(value)
        keys = data.get('keys') or {}
        if not isinstance(keys, dict):
            raise LayerError(f'Layer {layer.id}: {path}: keys must be a mapping', 'merge-invalid')
        target = merged.data.setdefault('keys', {})
        for key, entry in keys.items():
            entry = entry if isinstance(entry, dict) else {}
            if key in aliases:
                holder = aliases[key]
                merged.conflicts.append(_finding('alias-collision', 'error', f'cause key {key} (layer {layer.id}) is '
                                                 f'already an alias of {holder[0]} (layer {holder[1]})', layer.id))
                continue
            current = target.setdefault(key, {'description': None, 'aliases': []})
            if 'description' in entry:
                current['description'] = entry['description']
            if key in owners and owners[key] != layer.id:
                merged.overrides.append((f'cause-keys:{key}', owners[key], layer.id))
            owners[key] = layer.id
            merged.owners[f'cause-keys:{key}'] = layer.id
            for alias in entry.get('aliases') or []:
                holder = aliases.get(alias)
                if alias in target and alias != key:
                    merged.conflicts.append(_finding('alias-collision', 'error', f'alias {alias} of {key} (layer '
                                                     f'{layer.id}) is a cause key of layer {owners.get(alias)}', layer.id))
                    continue
                if holder is not None and holder[0] != key:
                    merged.conflicts.append(_finding('alias-collision', 'error', f'alias {alias} of {key} (layer '
                                                     f'{layer.id}) already belongs to {holder[0]} (layer {holder[1]})',
                                                     layer.id))
                    continue
                if alias not in current['aliases']:
                    current['aliases'].append(alias)
                aliases.setdefault(alias, (key, layer.id))
    return merged


def merged_lessons_index(layerset):
    """`workflow/lessons/index.yaml` + `index.d/`: union of records by `path` (later replaces in place)."""
    merged = MergeResult({})
    records, positions = [], {}
    for layer, path in _aggregate_sources(layerset, 'lessons-index'):
        data = _load_mapping(path, layer.id)
        merged.sources.append((layer.id, path))
        for key, value in data.items():
            if key != 'lessons':
                merged.data[key] = copy.deepcopy(value)
        for record in data.get('lessons') or []:
            rel = record.get('path') if isinstance(record, dict) else None
            if not isinstance(rel, str):
                records.append(copy.deepcopy(record))
                continue
            key = f'lessons:{rel}'
            if rel in positions:
                previous = merged.owners.get(key)
                if previous is not None and previous != layer.id and records[positions[rel]] != record:
                    merged.overrides.append((key, previous, layer.id))
                records[positions[rel]] = copy.deepcopy(record)
            else:
                positions[rel] = len(records)
                records.append(copy.deepcopy(record))
            merged.owners[key] = layer.id
    merged.data['lessons'] = records
    return merged


MERGERS = {
    'codex': merged_codex,
    'defaults': merged_defaults,
    'catalog': merged_skill_catalog,
    'profiles': merged_skill_profiles,
    'cause-keys': merged_cause_keys,
    'lessons-index': merged_lessons_index,
}


def fragment_block(layer_id, name, text):
    """Text of one `_base.d` fragment wrapped in its markers."""
    body = text if text.endswith('\n') else text + '\n'
    return (FRAGMENT_BEGIN.format(layer=layer_id, name=name) + '\n' + body
            + FRAGMENT_END.format(layer=layer_id, name=name) + '\n')


# --------------------------------------------------------------------------- findings engine

def override_allowed(config, key):
    """True when `allow_override` contains the element key or core file path.

    Entries are `fnmatch.fnmatchcase` globs: `*` matches any characters **including `/` and
    `.`** (so `skills/feature/*` covers nested files, `codex.yaml:agents.*` every role key)."""
    return any(fnmatch.fnmatchcase(key, pattern) for pattern in config.allow_override)


def _element_allowed(config, key):
    """Consent for a guarded element key.

    `codex.yaml` keys are paths in a tree: consent for a key also covers all its descendants
    (`key + '.'` prefix), including after a change of type (leaf replaced by a subtree).
    Elements of lists with `id` (`validation.checks:<id>`, `guardrails.rules:<id>`) are atomic:
    consent for `a` does not cover `a.b`. Globs as in `override_allowed`.
    """
    if override_allowed(config, key):
        return True
    if _role_of(key) is None:
        return False
    parts = key.split('.')
    return any(override_allowed(config, '.'.join(parts[:end])) for end in range(len(parts) - 1, 1, -1))


TRUST_FILE_RULES = ('skill', 'agent')
TRUST_FILE_PREFIXES = ('workflow/trackers/', 'hooks/')


def _fold_rel(rel):
    """Case-insensitive identity of a relative path (same result on every system)."""
    return '/'.join(part.casefold() for part in _rel(rel).split('/'))


def _trusted_file(rel):
    """Core files guarded by the trust model: skill files, roles, tracker adapters, hooks.
    Classified on the case-folded path (`Skills/X/SKILL.md` is a skill file)."""
    folded = _fold_rel(rel)
    return merge_rule(folded) in TRUST_FILE_RULES or folded.startswith(TRUST_FILE_PREFIXES)


def _case_groups(names):
    groups = {}
    for name in names:
        groups.setdefault(name.casefold(), []).append(name)
    return [sorted(set(items)) for items in groups.values() if len(set(items)) > 1]


def case_collisions(layerset):
    """`case-collision` (error): names that differ only by letter case in what the generator
    lists across the active layers (skills, files of a skill, instructions, roles, tracker and
    project files). On a case-insensitive file system they would be the same file."""
    findings = []

    def report(where, names):
        for group in _case_groups(names):
            findings.append(_finding('case-collision', 'error',
                                     f'{where}: {", ".join(group)} differ only by letter case'))

    names = set()
    for layer, directory in layerset.providers('skills', 'dir'):
        names.update(entry.name for entry in _entries(layer, directory) if entry.is_dir())
    report('skills', names)
    variants = {}
    for name in sorted(names):
        variants.setdefault(name.casefold(), []).append(name)
    for group in variants.values():  # files of one skill (all its case variants) across layers
        files = [rel for name in group for layer, directory in layerset.providers(f'skills/{name}', 'dir')
                 for rel in layer_files(layer, f'skills/{name}')]
        report(f'skills/{group[0]}', files)
    for rel_dir in ('instructions', 'agents', 'workflow/trackers', 'workflow/projects'):
        entries = set()
        for layer, directory in layerset.providers(rel_dir, 'dir'):
            entries.update(entry.name for entry in _entries(layer, directory))
        report(rel_dir, entries)
    return findings


def _same_file(left, right):
    try:
        return Path(left).read_bytes() == Path(right).read_bytes()
    except OSError:
        return False


def _flat(value, prefix=()):
    """{dotted key: leaf value} of a mapping (lists are leaves)."""
    if isinstance(value, dict) and value:
        result = {}
        for key, item in value.items():
            result.update(_flat(item, prefix + (str(key),)))
        return result
    return {'.'.join(prefix): value}


def _protected_elements(layerset):
    """{element key: value} of trust-guarded elements in the merge result of `layerset`.

    Keys: `validation.checks:<id>`, `guardrails.rules:<id>` (merged `workflow/defaults.yaml`)
    and `codex.yaml:agents.<role>.<dotted key>` (leaves of each role in merged `codex.yaml`).
    Computed from the same MERGERS as the generator uses, so whatever a layer does to the
    containers (`null`, `{}`, a scalar, another type) shows up as a missing or changed element.
    """
    elements = {}
    defaults = merged_defaults(layerset).data
    for section, name in DEFAULTS_ID_LISTS:
        container = defaults.get(section)
        items = container.get(name) if isinstance(container, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get('id'), str):
                elements[f'{section}.{name}:{item["id"]}'] = item
    agents = merged_codex(layerset).data.get('agents')
    if isinstance(agents, dict):
        for role, value in agents.items():
            if isinstance(value, dict):
                # `_flat` keeps an empty role as `agents.<role>: {}`, so adding a key to an empty
                # core role is a change of that element and needs consent.
                for key, leaf in _flat(value, ('agents', str(role))).items():
                    elements[f'codex.yaml:{key}'] = leaf
            else:
                elements[f'codex.yaml:agents.{role}'] = value
    return elements


_ROLE_PREFIX = 'codex.yaml:agents.'


def _role_of(key):
    """Role name of a `codex.yaml:agents.<role>[.<key>]` element key, else None."""
    return key[len(_ROLE_PREFIX):].split('.')[0] if key.startswith(_ROLE_PREFIX) else None


def _changed_elements(core_elements, merged_elements):
    """Keys of guarded core elements that vanished or changed in a merge result, plus new keys
    in roles that exist in core (all role keys of core roles are guarded)."""
    changed = [key for key, value in core_elements.items()
               if key not in merged_elements or merged_elements[key] != value]
    roles = {_role_of(key) for key in core_elements} - {None}
    for key in merged_elements:
        if key not in core_elements and _role_of(key) in roles:
            changed.append(key)
    return sorted(set(changed))


def _collapse_role_keys(keys):
    """Report a leaf replaced by a subtree (or back) once: among `codex.yaml` role keys keep
    the outermost of prefix-related keys. Element keys with `id` are never collapsed (an id
    may contain dots)."""
    kept = []
    for key in sorted(set(keys), key=lambda item: (item.count('.'), item)):
        if _role_of(key) is not None and any(_role_of(other) is not None and key.startswith(other + '.')
                                             for other in kept):
            continue
        kept.append(key)
    return sorted(kept)


def merge_findings(layerset):
    """Findings of the merge semantics (independent of the trust model): `merge-invalid`,
    `alias-collision` and other merge conflicts, `core-only-ignored`, `instruction-collision`."""
    findings = []
    for layer in layerset.layers:
        if layer.kind == 'core':
            continue
        for rel in layer_files(layer):
            if merge_rule(rel) == 'core-only':
                findings.append(_finding('core-only-ignored', 'warning',
                                         f'{rel} is taken only from the core layer; ignored in {layer.id}', layer.id))
    seen = {}
    for layer in layerset.layers:
        for rel in layer_files(layer):
            if merge_rule(rel) == 'instruction':
                seen.setdefault(rel, []).append(layer.id)
    for rel, owners in sorted(seen.items()):
        if len(owners) > 1:
            findings.append(_finding('instruction-collision', 'warning',
                                     f'{rel} is provided by {", ".join(owners)}; {owners[-1]} wins', owners[-1]))
    findings.extend(case_collisions(layerset))
    for merger in MERGERS.values():
        try:
            findings.extend(merger(layerset).conflicts)
        except LayerError as exc:
            findings.append(_finding('merge-invalid', 'error', str(exc)))
    return findings


def trust_findings(layerset):
    """`override-without-consent` (error) for an org layer that changes a guarded core element
    without `allow_override`.

    Guarded (spec, "Model zaufania"): existing core skill files, roles `agents/*`, tracker
    adapters `workflow/trackers/*` (file content compared with core), checks with `id`
    (`validation.checks`, `guardrails.rules`) and every role key of a core role in `codex.yaml`
    (change, removal and addition; a new org role is allowed). Elements are compared on the
    merge result of [core, org] for each org layer separately, so a changed container type
    cannot hide a removal and an identical copy (or `{}`) is not an override. `hooks/*` are
    core-only; catalog entries and cause-key descriptions follow "later wins".
    """
    core = layerset.core
    if core is None:
        return []
    findings = []
    # Same lookup as the generator (`layer_files`: links are skipped, never followed, D73);
    # paths are compared case-insensitively on every system.
    core_files = {}
    for rel in layer_files(core):
        if _trusted_file(rel):
            core_files.setdefault(_fold_rel(rel), rel)
    try:
        core_elements = _protected_elements(LayerSet((core,), layerset.config, 'override'))
    except LayerError:
        core_elements = None  # reported by merge_findings
    for layer in layerset.layers:
        if layer.kind != 'org':
            continue
        for rel in layer_files(layer):
            core_rel = core_files.get(_fold_rel(rel))
            if core_rel is None or _same_file(layer.root / rel, core.root / core_rel):
                continue
            if not (override_allowed(layerset.config, core_rel) or override_allowed(layerset.config, rel)):
                findings.append(_finding('override-without-consent', 'error',
                                         f'{rel} overrides a core file without allow_override', layer.id))
        if core_elements is None:
            continue
        try:
            merged = _protected_elements(LayerSet((core, layer), layerset.config, 'override'))
        except LayerError:
            continue  # unreadable layer file: merge-invalid from merge_findings
        denied = [key for key in _changed_elements(core_elements, merged)
                  if not _element_allowed(layerset.config, key)]
        for key in _collapse_role_keys(denied):
            findings.append(_finding('override-without-consent', 'error',
                                     f'{key} overrides a core element without allow_override', layer.id))
    return findings


def _requires_cycles(layers):
    graph = {layer.id: list((layer.meta or {}).get('requires', [])) for layer in layers}
    cycles, state = [], {}

    def visit(node, stack):
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                cycles.append(stack[stack.index(nxt):] + [nxt])
            elif not state.get(nxt):
                visit(nxt, stack + [nxt])
        state[node] = 2

    for node in graph:
        if not state.get(node):
            visit(node, [node])
    return cycles


def _hook_findings(layer):
    from . import leakguard
    state = leakguard.hook_state(layer.path)
    broken = []
    if state['state'] in ('ok', 'legacy'):
        broken = [_finding('hook-command-missing', 'error',
                           f'The {item["hook"]} hook of layer {layer.id} runs a {item["kind"]} that does not exist '
                           f'({Path(item["path"]).name}); run `skillsync leakguard install-hook {layer.path}` '
                           f'(or `skillsync layers add` for the layer) or remove the managed block', layer.id)
                  for item in leakguard.hook_command_problems(layer.path)]
    if state['state'] == 'ok':
        return broken
    if state['state'] == 'legacy':
        return broken + [_finding('hook-legacy', 'warning', f'Hooks of layer {layer.id} use the old explicit-input block; '
                         f'run `skillsync leakguard install-hook {layer.path}`', layer.id)]
    if state['state'] == 'not-git':
        return [_finding('hook-missing', 'error', f'Writable layer {layer.id} is not a git repository; '
                         'pre-commit/pre-push hooks cannot guard it', layer.id)]
    hint = state.get('reason') or f'run `skillsync leakguard install-hook {layer.path}`'
    return [_finding('hook-missing', 'error', f'Writable layer {layer.id} lacks the leakguard pre-commit/pre-push '
                     f'hooks ({", ".join(state["missing"])}); {hint}', layer.id)]


def check_layers(layerset, *, version=None, hooks=True, trust=True, orphans=True, cache_dir=None):
    """Findings `[{code, layer, message, severity}]` for a LayerSet (severity: blocked,
    error, warning, info). Status mapping: `findings_status`. `layers check` (2.4), `link`
    (2.2a) and `doctor` (2.3) build on it. `trust=False` skips only `override-without-consent`
    (`merge_findings` always run)."""
    version = version or generator_version()
    findings = list(layerset.notes)
    if layerset.core is None:
        findings.append(_finding('core-missing', 'blocked', 'No core layer is active'))
    ids = set(layerset.ids)
    for layer in layerset.layers:
        meta = layer.meta
        if layer.meta_error:
            findings.append(_finding('layer-yaml-invalid', 'blocked', layer.meta_error, layer.id))
        elif meta is None:
            findings.append(_finding('layer-yaml-missing', 'blocked',
                                     f'Layer {layer.id} has no layer.yaml (version and core_min unknown)', layer.id))
        else:
            if meta['id'] != layer.id:
                findings.append(_finding('id-mismatch', 'blocked', f'layer.yaml id {meta["id"]} differs from the '
                                         f'configured id {layer.id}', layer.id))
            if meta['kind'] != layer.kind:
                findings.append(_finding('kind-mismatch', 'blocked', f'layer.yaml kind {meta["kind"]} differs from '
                                         f'the configured kind {layer.kind}', layer.id))
            if meta['version'] is None:
                findings.append(_finding('version-missing', 'blocked', f'Layer {layer.id}: layer.yaml has no version', layer.id))
            if meta['core_min'] is None:
                findings.append(_finding('core-min-missing', 'blocked', f'Layer {layer.id}: layer.yaml has no core_min', layer.id))
            elif compare_versions(meta['core_min'], version) > 0:
                findings.append(_finding('core-min-unmet', 'blocked', f'Layer {layer.id} needs generator '
                                         f'>= {meta["core_min"]}, this is {version}', layer.id))
            for required in meta['requires']:
                if required not in ids:
                    findings.append(_finding('requires-unmet', 'blocked',
                                             f'Layer {layer.id} requires layer {required}, which is not active', layer.id))
            if layer.kind == 'core' and meta['version'] and meta['version'] != version:
                findings.append(_finding('core-version-mismatch', 'warning', f'Core layer version {meta["version"]} '
                                         f'differs from the generator version {version}', layer.id))
        for label, pattern in (('README', 'README*'), ('CHANGELOG', 'CHANGELOG*')):
            if not any(any(directory.glob(pattern)) for directory in {layer.root, layer.path} if directory.is_dir()):
                findings.append(_finding(f'{label.lower()}-missing', 'warning', f'Layer {layer.id} has no {label}', layer.id))
        if hooks and layer.writable:
            findings.extend(_hook_findings(layer))
        for rel in layer_links(layer):
            findings.append(_finding('symlink-in-layer', 'error', f'Layer {layer.id}: {rel} is a symlink/junction; '
                                     'links are not allowed in layers and are never read', layer.id))
        try:
            identity = layer.identity_file()
        except LayerError:
            identity = None  # already reported: `layer-yaml-invalid` or `symlink-in-layer`
        if identity is not None:
            try:
                data = yaml.safe_load(identity.read_text(encoding='utf-8'))
                if isinstance(data, dict) and data.get('layer') != layer.id:
                    findings.append(_finding('identity-layer-mismatch', 'warning',
                                             f'{identity.name} of layer {layer.id} declares another layer', layer.id))
            except (OSError, UnicodeDecodeError, yaml.YAMLError):
                findings.append(_finding('identity-invalid', 'error', f'Identity file of layer {layer.id} is unreadable', layer.id))
    for cycle in _requires_cycles(layerset.layers):
        findings.append(_finding('requires-cycle', 'blocked', 'Cycle in requires: ' + ' -> '.join(cycle), cycle[0]))
    findings.extend(merge_findings(layerset))
    if trust:
        findings.extend(trust_findings(layerset))
    if orphans:
        root = Path(cache_dir) if cache_dir else composed_root()
        if root.is_dir():
            for directory in sorted(root.glob('*/*.orphans')):
                if directory.is_dir() and any(directory.iterdir()):
                    findings.append(_finding('orphans', 'warning', f'Unadopted files in {directory} (never removed '
                                             'automatically; move them to a layer or delete)'))
    return findings


def findings_status(findings):
    """BLOCKED when any `blocked`, FAIL when any `error`, else PASS."""
    severities = {item['severity'] for item in findings}
    if 'blocked' in severities:
        return 'BLOCKED'
    if 'error' in severities:
        return 'FAIL'
    return 'PASS'


# --------------------------------------------------------------------------- leak guard inputs

def leakguard_inputs(config=None, *, path=None, target=None):
    """Inputs for `leakguard.prepare`/`leakcheck` from the machine config (N5-2).

    identity files: every configured layer present locally (`layer.yaml` `identity`, else
    `leakguard/identity.yaml`) plus `leakguard.identity_dir/**/*.y*ml`; targets: config
    `leakguard.targets` with `kind` from `layers`; target: given, else a unique
    `remote_match` hit of the scanned repo, else the layer whose checkout is the scanned
    repo. A missing config gives empty inputs.
    """
    from . import leakguard
    config = config if config is not None else load_machine_config()
    identity_files = []
    for layer in config.ordered_layers():
        if layer.present:
            found = layer.identity_file()
            if found is not None and found not in identity_files:
                identity_files.append(found)
    if config.identity_dir is not None and config.identity_dir.is_dir():
        for item in sorted(config.identity_dir.rglob('*')):
            if item.is_file() and item.suffix in ('.yaml', '.yml') and '.git' not in item.parts:
                identity_files.append(item)
    targets = config.targets_with_kind() or None
    warnings = list(config.warnings)
    if target is None and path is not None and Path(path).expanduser().is_dir():
        checkouts = {layer.id: layer.path for layer in config.layers.values()}
        target, chosen = leakguard.select_target(Path(path).expanduser().resolve(), targets, checkouts)
        warnings.extend(chosen)
    if path is not None:
        # D73: scanning a layer whose tree contains links is BLOCKED (structural input error,
        # not a leak finding; FAIL stays reserved for leaks), fail-closed like a bad identity file.
        scanned = path_case_key(os.path.realpath(Path(path).expanduser()))
        for layer in config.layers.values():
            checkout = path_case_key(os.path.realpath(layer.path))
            if layer.path.is_dir() and (_key_within(scanned, checkout) or _key_within(checkout, scanned)):
                links = layer_links(layer)
                if links:
                    raise LayerError(f'Layer {layer.id} contains symlinks/junctions ({", ".join(links[:5])}'
                                     f'{", ..." if len(links) > 5 else ""}); links are not allowed in layers',
                                     'symlink-in-layer')
    return {'identity_files': [str(item) for item in identity_files], 'targets': targets,
            'trailer_allow': list(config.trailer_allow), 'target': target, 'warnings': warnings}


def target_patterns_file(config, target):
    """`.leakguard/patterns.yaml` in the git root of the checkout of layer `target`, if any."""
    from . import leakguard
    layer = config.layers.get(target)
    if layer is None or not layer.path.is_dir():
        return None
    top = Path(leakguard.git_root(layer.path) or layer.path)
    candidate = top / leakguard.TARGET_PATTERNS_REL
    if (candidate.exists() or is_link(candidate)) and not _clean(candidate, top):
        raise LayerError(f'Layer {target}: {leakguard.TARGET_PATTERNS_REL} is (or lies under) a symlink/junction'
                         '; not read', 'symlink-in-layer')
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------- config editing

def _yaml_scalar(value):
    text = yaml.safe_dump([value], default_flow_style=True, allow_unicode=True, width=10 ** 6).strip()
    return text[1:-1]


def _indent(text, prefix):
    return ''.join(prefix + line if line.strip() else line for line in text.splitlines(keepends=True))


_TOP = re.compile(r'^[^\s#-]')


def _top_block(lines, key):
    """(start, end, inline) of top-level `key:` block or None; inline = text after the colon."""
    pattern = re.compile(rf'^{re.escape(key)}:(.*)$')
    for index, line in enumerate(lines):
        match = pattern.match(line.rstrip('\n'))
        if match:
            end = index + 1
            while end < len(lines) and not _TOP.match(lines[end]):
                end += 1
            inline = match.group(1).split(' #')[0].strip()
            return index, end, inline
    return None


def _last_content(lines, start, end):
    last = start
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith('#'):
            last = index
    return last


def _ensure_newline(lines):
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'


def text_append_layer(text, entry):
    """Append an item to the top-level `layers:` list; None when the layout is uncertain."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    item = yaml.safe_dump([entry], sort_keys=False, default_flow_style=False, allow_unicode=True)
    block = _top_block(lines, 'layers')
    if block is None:
        return ''.join(lines) + 'layers:\n' + _indent(item, '  ')
    start, end, inline = block
    if inline:
        return None
    indent = '  '
    for index in range(start + 1, end):
        match = re.match(r'^(\s*)- ', lines[index])
        if match:
            indent = match.group(1)
            break
    position = _last_content(lines, start, end) + 1
    return ''.join(lines[:position]) + _indent(item, indent) + ''.join(lines[position:])


def text_add_target(text, target_id, stub):
    """Add `leakguard.targets.<id>`; None when the layout is uncertain."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    entry = yaml.safe_dump({target_id: stub}, sort_keys=False, default_flow_style=None, allow_unicode=True)
    block = _top_block(lines, 'leakguard')
    if block is None:
        return ''.join(lines) + 'leakguard:\n  targets:\n' + _indent(entry, '    ')
    start, end, inline = block
    if inline:
        return None
    child = '  '
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith('#'):
            child = re.match(r'^(\s*)', lines[index]).group(1)
            break
    target_line = None
    for index in range(start + 1, end):
        match = re.match(rf'^{re.escape(child)}targets:(.*)$', lines[index].rstrip('\n'))
        if match:
            if match.group(1).split(' #')[0].strip():
                return None
            target_line = index
            break
    if target_line is None:
        return (''.join(lines[:start + 1]) + f'{child}targets:\n' + _indent(entry, child * 2)
                + ''.join(lines[start + 1:]))
    sub_end = target_line + 1
    while sub_end < end:
        stripped = lines[sub_end].strip()
        if stripped and not stripped.startswith('#') and len(lines[sub_end]) - len(lines[sub_end].lstrip()) <= len(child):
            break
        sub_end += 1
    grand = child * 2
    for index in range(target_line + 1, sub_end):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith('#'):
            grand = re.match(r'^(\s*)', lines[index]).group(1)
            break
    position = _last_content(lines, target_line, sub_end) + 1
    return ''.join(lines[:position]) + _indent(entry, grand) + ''.join(lines[position:])


def text_set_layer_field(text, layer_id, key, value):
    """Set `key: value` in the `layers` item with `id: <layer_id>`; None when uncertain."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    block = _top_block(lines, 'layers')
    if block is None or block[2]:
        return None
    start, end, _ = block
    items = [index for index in range(start + 1, end) if re.match(r'^\s*-\s', lines[index])]
    id_pattern = re.compile(rf'^id:\s*(["\']?){re.escape(layer_id)}\1\s*(#.*)?$')
    for number, item_start in enumerate(items):
        item_end = items[number + 1] if number + 1 < len(items) else end
        dash = re.match(r'^\s*-\s+', lines[item_start].rstrip('\n'))
        if dash is None:
            return None  # `-` alone on its line: uncertain layout, caller rewrites
        dash = dash.group(0)
        column = ' ' * len(dash)

        def key_line(index):
            line = lines[index]
            if index == item_start:
                return dash, line[len(dash):]
            if line.startswith(column) and len(line) > len(column) and not line[len(column)].isspace():
                return column, line[len(column):]
            return None

        keyed = [(index, key_line(index)) for index in range(item_start, item_end)]
        keyed = [(index, item) for index, item in keyed if item is not None]
        id_line = next((index for index, (_, rest) in keyed if id_pattern.match(rest.rstrip('\n'))), None)
        if id_line is None:
            continue
        for index, (prefix, rest) in keyed:
            if re.match(rf'^{re.escape(key)}:', rest):
                lines[index] = f'{prefix}{key}: {_yaml_scalar(value)}\n'
                return ''.join(lines)
        lines.insert(id_line + 1, f'{column}{key}: {_yaml_scalar(value)}\n')
        return ''.join(lines)
    return None


def text_fill_remote_match(text, target_id, patterns):
    """Replace an empty `remote_match: []` of `leakguard.targets.<id>` (flow or block style);
    None when the layout is uncertain."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    block = _top_block(lines, 'leakguard')
    if block is None or block[2]:
        return None
    start, end, _ = block
    value = _yaml_scalar(list(patterns))
    key = re.escape(target_id)
    for index in range(start + 1, end):
        line = lines[index].rstrip('\n')
        flow = re.match(rf'^(\s+["\']?{key}["\']?:\s*\{{.*?)\bremote_match:\s*\[\s*\](.*\}}\s*(#.*)?)$', line)
        if flow:
            lines[index] = f'{flow.group(1)}remote_match: {value}{flow.group(2)}\n'
            return ''.join(lines)
        head = re.match(rf'^(\s+)["\']?{key}["\']?:\s*(#.*)?$', line)
        if head:
            indent = len(head.group(1))
            for inner in range(index + 1, end):
                stripped = lines[inner].strip()
                if not stripped or stripped.startswith('#'):
                    continue
                if len(lines[inner]) - len(lines[inner].lstrip()) <= indent:
                    break
                found = re.match(r'^(\s+)remote_match:\s*\[\s*\]\s*(#.*)?$', lines[inner].rstrip('\n'))
                if found:
                    lines[inner] = f'{found.group(1)}remote_match: {value}\n'
                    return ''.join(lines)
            return None
    return None


def text_insert_rule(text, rule, index):
    """Insert `rule` into the top-level `layer_rules:` list before item `index` (append when
    `index` = number of items); None when the layout is uncertain."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    item = yaml.safe_dump([rule], sort_keys=False, default_flow_style=None, allow_unicode=True, width=10 ** 6)
    block = _top_block(lines, 'layer_rules')
    if block is None:
        return ''.join(lines) + 'layer_rules:\n' + _indent(item, '  ') if index == 0 else None
    start, end, inline = block
    if inline:
        return None
    items = [number for number in range(start + 1, end) if re.match(r'^\s*-\s', lines[number])]
    if not items:
        return None
    indent = re.match(r'^(\s*)-', lines[items[0]]).group(1)
    items = [number for number in items if re.match(r'^(\s*)-', lines[number]).group(1) == indent]
    if index > len(items):
        return None
    position = items[index] if index < len(items) else _last_content(lines, start, end) + 1
    return ''.join(lines[:position]) + _indent(item, indent) + ''.join(lines[position:])


def text_append_top(text, key, value):
    """Append a new top-level `key:` (absent before); None when the key already exists."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    if _top_block(lines, key) is not None:
        return None
    if not isinstance(value, (dict, list)):
        return ''.join(lines) + f'{key}: {_yaml_scalar(value)}\n'
    dumped = yaml.safe_dump(value, sort_keys=False, default_flow_style=None, allow_unicode=True, width=10 ** 6)
    return ''.join(lines) + f'{key}:\n' + _indent(dumped, '  ')


def text_rename_top_key(text, old, new):
    """Rename the top-level key `old:` to `new:` (value and comments kept); None when absent or
    when `new` already exists."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    block = _top_block(lines, old)
    if block is None or _top_block(lines, new) is not None:
        return None
    lines[block[0]] = new + lines[block[0]][len(old):]
    return ''.join(lines)


def text_remove_top_key(text, key):
    """Remove the top-level `key:` with its value (comments after its last value line stay);
    None when absent."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    block = _top_block(lines, key)
    if block is None:
        return None
    start, end, _ = block
    last = _last_content(lines, start, end)
    return ''.join(lines[:start] + lines[last + 1:])


def _backup_path(path):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    candidate = path.with_name(f'{path.name}.bak-{stamp}')
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = path.with_name(f'{path.name}.bak-{stamp}-{counter}')
    return candidate


def _write_atomic(path, text):
    """Atomic write (temporary file + `os.replace`); a symlinked file (e.g. dotfiles) is written at its target,
    so the link stays."""
    if os.path.islink(path):
        path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        if path.exists():
            shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def _edit_text(original, operations, label):
    """(new text, expected data, rewritten) after `operations` [(text_function, args, mutate)].

    Each text function returns the new text or None (uncertain layout). The result is verified
    by parsing: when it differs from the expected data (or any step is uncertain), the whole
    YAML is rewritten with `yaml.safe_dump` (comments lost; the caller keeps a backup)."""
    text = original or ''
    try:
        data = (yaml.safe_load(text) if text.strip() else {}) or {}
    except yaml.YAMLError as exc:
        raise LayerError(f'{label} is not valid YAML ({type(exc).__name__})') from None
    if not isinstance(data, dict):
        raise LayerError(f'{label} must be a mapping')
    expected = copy.deepcopy(data)
    rewritten = False
    for function, args, mutate in operations:
        mutate(expected)
        if not rewritten:
            updated = function(text, *args)
            if updated is None:
                rewritten = True
            else:
                text = updated
    if not rewritten:
        try:
            rewritten = yaml.safe_load(text) != expected
        except yaml.YAMLError:
            rewritten = True
    if rewritten:
        text = yaml.safe_dump(expected, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return text, expected, rewritten


def _write_with_backup(path, original, text):
    """Backup of the existing file (`<name>.bak-<stamp>` next to `path`; for a symlink: the target's content next
    to the link) and an atomic write (a symlink is written at its target); returns the backup path."""
    backup = None
    if original is not None:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
    _write_atomic(path, text)
    return backup


def edit_config(path, operations, *, apply=True):
    """Apply `operations` [(text_function, args, mutate)] to the config text, keeping comments
    (see `_edit_text`). Returns `{config, backup, rewritten, changed}`; written atomically after a
    backup copy. `apply=False` (dry-run) writes nothing and adds `preview` (the new text). A result
    equal to the current text writes nothing (no backup either)."""
    path = Path(path)
    original = read_config_text(path)
    text, expected, rewritten = _edit_text(original, operations, f'Machine config {path}')
    parse_machine_config(expected, path)
    changed = text != (original or '')
    result = {'config': str(path), 'backup': None, 'rewritten': rewritten, 'changed': changed}
    if not apply:
        result['preview'] = text
        return result
    if changed:
        backup = _write_with_backup(path, original, text)
        result['backup'] = str(backup) if backup else None
    return result


def _add_layer_op(entry):
    def mutate(data):
        layers = data.get('layers')
        if not isinstance(layers, list):
            layers = data['layers'] = []
        layers.append(copy.deepcopy(entry))
    return (text_append_layer, (entry,), mutate)


def _add_target_op(target_id, stub):
    def mutate(data):
        guard = data.get('leakguard')
        if not isinstance(guard, dict):
            guard = data['leakguard'] = {}
        targets = guard.get('targets')
        if not isinstance(targets, dict):
            targets = guard['targets'] = {}
        targets[target_id] = copy.deepcopy(stub)
    return (text_add_target, (target_id, stub), mutate)


def _set_layer_op(layer_id, key, value):
    def mutate(data):
        for entry in data.get('layers') or []:
            if isinstance(entry, dict) and entry.get('id') == layer_id:
                entry[key] = value
    return (text_set_layer_field, (layer_id, key, value), mutate)


def _fill_remote_match_op(target_id, patterns):
    def mutate(data):
        data['leakguard']['targets'][target_id]['remote_match'] = list(patterns)
    return (text_fill_remote_match, (target_id, list(patterns)), mutate)


def _insert_rule_op(rule, index):
    def mutate(data):
        rules = data.get('layer_rules')
        if not isinstance(rules, list):
            rules = data['layer_rules'] = []
        rules.insert(index, copy.deepcopy(rule))
    return (text_insert_rule, (rule, index), mutate)


def _append_top_op(key, value):
    def mutate(data):
        data[key] = copy.deepcopy(value)
    return (text_append_top, (key, value), mutate)


def _rename_top_op(old, new):
    def mutate(data):
        data[new] = data.pop(old)
    return (text_rename_top_key, (old, new), mutate)


def _remove_top_op(key):
    def mutate(data):
        data.pop(key, None)
    return (text_remove_top_key, (key,), mutate)


# --------------------------------------------------------------------------- git helpers

def _git(cwd, *args, timeout=300):
    process = _git_text(cwd, *args, timeout=timeout)
    if process is None:
        raise LayerError(f'git {" ".join(args[:2])} could not run in {cwd}', 'git-error')
    return process


_URL_USERINFO = re.compile(r'(?i)\b([a-z][a-z0-9+.-]*://)[^/\s]*@')   # up to the last `@` of the authority
_URL_SECRET_QUERY = re.compile(r'(?i)([?&](?:access_token|private_token|token|password|passwd|pass|auth|key)=)[^&\s\'"]+')


def _mask_credentials(text):
    """Credentials in URLs (`scheme://user:secret@host`, `?token=...`) replaced by `***`."""
    return _URL_SECRET_QUERY.sub(r'\1***', _URL_USERINFO.sub(r'\1***@', text))


def _git_tail(process):
    lines = [line for line in (process.stderr or process.stdout or '').splitlines() if line.strip()]
    return _mask_credentials(lines[-1].strip()) if lines else f'exit {process.returncode}'


def _git_message(process, max_lines=6, max_chars=600):
    """Whole short git message (non-empty stderr/stdout lines joined with ` | `), with credentials of
    URLs (`scheme://user:secret@host`) replaced by `***`; limited to `max_lines` lines and `max_chars`."""
    lines = [line.strip() for line in (process.stderr or process.stdout or '').splitlines() if line.strip()]
    if not lines:
        return f'exit {process.returncode}'
    text = ' | '.join(_mask_credentials(line) for line in lines[:max_lines])
    if len(lines) > max_lines:
        text += ' | …'
    return text if len(text) <= max_chars else text[:max_chars - 1] + '…'


def _git_email(path):
    process = _git_text(path, 'config', 'user.email')
    return process.stdout.strip() if process and not process.returncode and process.stdout.strip() else None


def remote_patterns(source):
    """Exact `remote_match` patterns for a layer source: `[<base>, <base>.git]` (URL without
    scheme/user and `.git`), or `[<absolute path>]` for a local source. No `*`, so a stub never
    matches other repositories sharing a name prefix."""
    from . import leakguard
    if _looks_local(source):
        return [_glob_escape(Path(os.path.abspath(Path(source).expanduser())).as_posix())]
    stripped = leakguard._strip_remote(source).rstrip('/')
    if stripped.endswith('.git'):
        stripped = stripped[:-4]
    base = _glob_escape(stripped)
    return [base, base + '.git']


def _glob_escape(text):
    return re.sub(r'([*?\[])', r'[\1]', text)


def _looks_local(source):
    """True for a local path source (not `scheme://...` and not scp-like `[user@]host:path`)."""
    if re.match(r'^[a-z][a-z0-9+.-]*://', source, re.IGNORECASE):
        return False
    if re.match(r'^[A-Za-z]:[\\/]', source):
        return True
    if re.match(r'^[^/\\\s:]+:[^/\\]', source) or re.match(r'^[^/\\\s]+@[^/\\\s:]+:', source):
        return False
    return True


def target_stub(kind, checkout, source=None):
    """`leakguard.targets.<id>` stub for a new layer (K9)."""
    email = _git_email(checkout) if checkout and Path(checkout).is_dir() else None
    return {'scope': TARGET_SCOPE[kind], 'remote_match': remote_patterns(source) if source else [],
            'authors': [email] if email else []}


def ref_kind(path, ref):
    """`tag`, `branch`, `commit` or None for `ref` in the checkout `path`."""
    def exists(name):
        process = _git_text(path, 'show-ref', '--verify', '--quiet', name)
        return process is not None and process.returncode == 0
    if exists(f'refs/tags/{ref}'):
        return 'tag'
    if exists(f'refs/remotes/origin/{ref}') or exists(f'refs/heads/{ref}'):
        return 'branch'
    process = _git_text(path, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
    if process is not None and process.returncode == 0:
        return 'commit'
    return None


def _head(path):
    process = _git_text(path, 'rev-parse', 'HEAD')
    return process.stdout.strip() if process and not process.returncode else None


def apply_ref(path, ref, fetch=True):
    """Move checkout `path` to `ref` (tag: checkout; branch: ff-only; commit: detached;
    None: `pull --ff-only`). Returns `(status, action, reason)`."""
    status = _git(path, 'status', '--porcelain', '--untracked-files=no')
    if status.returncode:
        return 'BLOCKED', 'error', _git_tail(status)
    if status.stdout.strip():
        return 'BLOCKED', 'dirty', 'Working tree has uncommitted changes; commit or stash them first'
    if ref is None:
        pulled = _git(path, 'pull', '--ff-only', '--quiet')
        return ('PASS', 'pulled', None) if not pulled.returncode else ('BLOCKED', 'no-fast-forward', _git_tail(pulled))
    if fetch:
        fetched = _git(path, 'fetch', '--tags', '--quiet', 'origin')
        if fetched.returncode:
            return 'BLOCKED', 'fetch-failed', _git_tail(fetched)
    kind = ref_kind(path, ref)
    if kind is None:
        return 'BLOCKED', 'unknown-ref', f'Unknown ref {ref}'
    if kind in ('tag', 'commit'):
        target = f'refs/tags/{ref}^{{commit}}' if kind == 'tag' else f'{ref}^{{commit}}'
        moved = _git(path, '-c', 'advice.detachedHead=false', 'checkout', '--quiet', '--detach', target)
        return ('PASS', f'checkout-{kind}', None) if not moved.returncode else ('BLOCKED', 'checkout-failed', _git_tail(moved))
    current = _git_text(path, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if not current or current.returncode or current.stdout.strip() != ref:
        moved = _git(path, 'checkout', '--quiet', ref)
        if moved.returncode:
            return 'BLOCKED', 'checkout-failed', _git_tail(moved)
    if _git_text(path, 'show-ref', '--verify', '--quiet', f'refs/remotes/origin/{ref}').returncode == 0:
        merged = _git(path, 'merge', '--ff-only', '--quiet', f'origin/{ref}')
        if merged.returncode:
            return 'BLOCKED', 'no-fast-forward', _git_tail(merged)
    return 'PASS', 'fast-forward', None


def _install_hooks(layer):
    from . import leakguard
    return leakguard.install_hook(layer.path, target=layer.id)


# --------------------------------------------------------------------------- commands

def _report(status='PASS', **extra):
    return {'schema': SCHEMA, 'status': status, **extra}


def _blocked(exc, **extra):
    return _report('BLOCKED', reason=str(exc), code=getattr(exc, 'code', 'layer-error'), **extra)


def _skeleton(layer_id, kind):
    persona = ('<!-- Optional persona fragment of this layer. Text here is added to the base instructions\n'
               '     of every project using the layer. Leave it empty to keep neutral instructions. -->\n')
    return {
        LAYER_FILE: yaml.safe_dump({'schema': LAYER_SCHEMA, 'id': layer_id, 'kind': kind, 'version': '0.1.0',
                                    'core_min': generator_version(), 'requires': [], 'identity': DEFAULT_IDENTITY},
                                   sort_keys=False),
        'README.md': f'# {layer_id}\n\nskillsync layer (`kind: {kind}`). Layout: `skills/`, `instructions/`, '
                     '`agents/`, `workflow/`; metadata in `layer.yaml`.\n',
        'CHANGELOG.md': '# Changelog\n\n## [Unreleased]\n\n- Layer created with `skillsync layers init`.\n',
        f'{FRAGMENTS_DIR}/10-persona.md': persona,
        DEFAULT_IDENTITY: yaml.safe_dump({'schema': 1, 'layer': layer_id, 'terms': []}, sort_keys=False),
        '.gitignore': '__pycache__/\n*.pyc\n.DS_Store\n',
    }


def layers_init(directory, kind, layer_id=None, *, register_target=True):
    """`layers init <dir> --kind personal|org`: skeleton, `git init`, hooks, target stub."""
    directory = Path(directory).expanduser()
    directory = Path(os.path.abspath(directory))
    if kind not in ('personal', 'org'):
        return _blocked(LayerError('--kind must be personal or org', 'bad-argument'))
    layer_id = layer_id or re.sub(r'[^a-z0-9-]+', '-', directory.name.lower()).strip('-')
    if not _ID.fullmatch(layer_id or ''):
        return _blocked(LayerError(f'Invalid layer id {layer_id!r}; pass --id', 'bad-argument'))
    if (directory / LAYER_FILE).exists():
        return _blocked(LayerError(f'{directory} is already a layer', 'exists'))
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        return _blocked(LayerError(f'{directory} exists and is not empty', 'exists'))
    existed = directory.exists()
    try:
        return _init_layer(directory, kind, layer_id, register_target)
    except BaseException as exc:
        # Clean up only what this command created: the directory itself, or its new content.
        if not existed:
            shutil.rmtree(directory, ignore_errors=True)
        elif directory.is_dir():
            for entry in directory.iterdir():
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        if isinstance(exc, (LayerError, OSError)):
            return _blocked(exc)
        raise


def _init_layer(directory, kind, layer_id, register_target):
    config = load_machine_config()
    created = []
    for rel, text in _skeleton(layer_id, kind).items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
        created.append(rel)
    initialized = _git(directory, 'init', '--quiet')
    if initialized.returncode:
        raise LayerError(f'git init failed: {_git_tail(initialized)}', 'git-error')
    from . import leakguard
    hooks = leakguard.install_hook(directory, target=layer_id)
    report = _report('PASS', layer={'id': layer_id, 'kind': kind, 'path': str(directory)}, created=created,
                     hooks=hooks.get('hooks', []), warnings=[])
    if hooks['status'] not in ('PASS',):
        report['warnings'].append(f'hooks: {hooks.get("reason") or hooks["status"]}')
    if register_target and layer_id not in config.targets:
        stub = target_stub(kind, directory)
        report.update(edit_config(config.path, [_add_target_op(layer_id, stub)]))
        report['target'] = stub
    elif layer_id in config.targets:
        report['warnings'].append(f'leakguard.targets.{layer_id} already exists; left unchanged')
    quoted = shlex.quote(str(directory))
    report['next'] = (f'git -C {quoted} add -A && git -C {quoted} commit -m "Layer skeleton" && '
                      f'skillsync layers add {layer_id} --kind {kind} --source {quoted}')
    return report


def layers_add(layer_id, kind, source, *, subdir=None, ref=None, path=None, writable=None):
    """`layers add`: clone to the default path, config entry (textual edit + backup), target
    stub, hooks for writable layers."""
    try:
        if not isinstance(layer_id, str) or not _ID.fullmatch(layer_id):
            raise LayerError(f'Invalid layer id {layer_id!r}', 'bad-argument')
        if kind not in LAYER_KINDS:
            raise LayerError(f'--kind must be one of: {", ".join(LAYER_KINDS)}', 'bad-argument')
        if not source:
            raise LayerError('--source is required', 'bad-argument')
        if ref is not None and (not isinstance(ref, str) or not ref.strip() or ref.startswith('-')):
            raise LayerError('--ref must be a tag, branch or commit', 'bad-argument')
        config = load_machine_config()
        if layer_id in config.layers:
            raise LayerError(f'Layer {layer_id} already exists in {config.path}', 'conflict')
        configured_core = next((layer for layer in config.layers.values() if layer.kind == 'core'), None)
        if kind == 'core' and configured_core is not None:
            if configured_core.synthetic:
                raise LayerError(f'The configured core layer {configured_core.id} is the 0.9.x legacy bridge; replace it '
                                 'with `skillsync layers migrate --layer-source core=PATH --apply`', 'conflict')
            raise LayerError('A core layer is already configured', 'conflict')
        subdir = _safe_rel(subdir, '--subdir') if subdir not in (None, '', '.') else ''
        if path:
            path = str(Path(os.path.abspath(Path(path).expanduser()))) if not path.startswith('~') else path
        checkout = _expand(path, '--path') if path else default_layer_path(layer_id)
        clone_source = source
        if _looks_local(source):
            clone_source = str(Path(os.path.abspath(Path(source).expanduser())))
            if not source.startswith('~') and not os.path.isabs(source):
                source = clone_source
        if checkout.exists() and (not checkout.is_dir() or any(checkout.iterdir())):
            raise LayerError(f'Checkout path {checkout} exists and is not empty', 'conflict')
        checkout.parent.mkdir(parents=True, exist_ok=True)
        cloned = _git(checkout.parent, 'clone', '--quiet', '--', clone_source, str(checkout), timeout=900)
        if cloned.returncode:
            shutil.rmtree(checkout, ignore_errors=True)
            raise LayerError(f'git clone failed: {_git_message(cloned)}', 'git-error')
        try:
            return _register_clone(config, layer_id, kind, source, subdir, ref, path, writable, checkout)
        except BaseException:
            shutil.rmtree(checkout, ignore_errors=True)  # any failure after `clone`: no checkout left behind
            raise
    except (LayerError, OSError) as exc:
        return _blocked(exc)


def _register_clone(config, layer_id, kind, source, subdir, ref, path, writable, checkout):
    warnings = []
    head = _git_text(checkout, 'rev-parse', '--verify', '-q', 'HEAD')
    if not ref and (head is None or head.returncode):
        warnings.append(f'Source {source} has no commits (an empty repository): the checkout is empty; '
                        'commit the layer in its source (for a new layer: `git add -A && git commit`)')
    if ref:
        status, action, reason = apply_ref(checkout, ref, fetch=False)
        if status != 'PASS':
            raise LayerError(f'Cannot check out {ref}: {reason}', action)
    layer = Layer(layer_id, kind, checkout, subdir, source, ref, writable)
    if not layer.root.is_dir():
        raise LayerError(f'subdir {subdir} does not exist in the checkout', 'bad-argument')
    if layer.meta_error:
        raise LayerError(layer.meta_error, 'layer-yaml-invalid')
    if layer.meta is None:
        warnings.append(f'{layer.root / LAYER_FILE} is missing; `layers check` reports BLOCKED until it exists')
    elif layer.meta['id'] != layer_id or layer.meta['kind'] != kind:
        raise LayerError(f'layer.yaml declares {layer.meta["id"]}/{layer.meta["kind"]}, expected '
                         f'{layer_id}/{kind}', 'layer-yaml-invalid')
    entry = {'id': layer_id, 'kind': kind, 'source': source}
    if subdir:
        entry['subdir'] = subdir
    if ref:
        entry['ref'] = ref
    if path:
        entry['path'] = path
    if writable is not None:
        entry['writable'] = writable
    operations = [_add_layer_op(entry)]
    stub = None
    existing = config.targets.get(layer_id)
    if layer_id not in config.targets:
        stub = target_stub(kind, checkout, source)
        operations.append(_add_target_op(layer_id, stub))
    elif isinstance(existing, dict) and existing.get('remote_match') == [] and source:
        # Stub written by `layers init` (empty `remote_match`): filled from the source (2.4).
        stub = dict(existing, remote_match=remote_patterns(source))
        operations.append(_fill_remote_match_op(layer_id, stub['remote_match']))
        warnings.append(f'leakguard.targets.{layer_id}: empty remote_match filled from the source')
    else:
        warnings.append(f'leakguard.targets.{layer_id} already exists; left unchanged')
    hooks = None
    if layer.writable:
        hooks = _install_hooks(layer)
        if hooks['status'] == 'BLOCKED':
            raise LayerError(f'hooks: {hooks.get("reason")}', 'hook-error')
        if hooks['status'] != 'PASS':
            warnings.append(f'hooks: {hooks.get("reason") or hooks["status"]}')
    rules_updated = []
    if kind == 'core':
        # The core layer is part of every rule: rules written before it was added get it as the first layer.
        updates = {rule['index']: [layer_id] + [item for item in rule['use'] if item != layer_id]
                   for rule in config.rules if layer_id not in rule['use']}
        if updates:
            operations.append(_set_rule_use_op(updates))
            rules_updated = [{'match': list(config.rules[index]['match']), 'use': use} for index, use in sorted(updates.items())]
            warnings.append(f'Core layer {layer_id} added to {len(updates)} existing layer_rules '
                            f'entr{"y" if len(updates) == 1 else "ies"} (the core layer is part of every rule); '
                            'the config is rewritten without its comments, the backup keeps the original')
    edited = edit_config(config.path, operations)  # last step: the config changes only on success
    if not any(rule for rule in config.rules if layer_id in rule['use']) and kind != 'core':
        warnings.append(f'No layer_rules entry uses {layer_id}; add one with `skillsync layers use {layer_id} --match <glob>`')
    return _report('PASS', layer=layer.as_json(), target=stub, hooks=(hooks or {}).get('hooks', []),
                   warnings=warnings, rules_updated=rules_updated, **edited)


def layers_update(layer_id=None, *, ref=None):
    """`layers update [id] [--ref R]`: per layer tag/branch/commit/pull; dirty tree or no
    fast-forward = BLOCKED for that layer, the others continue. `--ref` updates the config."""
    try:
        config = load_machine_config()
        if ref is not None and layer_id is None:
            raise LayerError('--ref needs a layer id', 'bad-argument')
        if ref is not None and (not ref.strip() or ref.startswith('-')):
            raise LayerError('--ref must be a tag, branch or commit', 'bad-argument')
        if layer_id is not None and layer_id not in config.layers:
            raise LayerError(f'Unknown layer {layer_id}', 'bad-argument')
        selected = [config.layers[layer_id]] if layer_id else config.ordered_layers()
        for layer in selected:
            _check_bridge(layer)
        results, edited = [], None
        for layer in selected:
            wanted = ref if ref is not None else layer.ref
            item = {'id': layer.id, 'ref': wanted, 'from': None, 'to': None}
            git_dir = _git_text(layer.path, 'rev-parse', '--git-dir') if layer.path.is_dir() else None
            if not layer.path.is_dir():
                item.update(status='BLOCKED', action='missing',
                            reason=f'Checkout {layer.path} does not exist; remove the entry and run `layers add`')
            elif git_dir is None or git_dir.returncode:
                item.update(status='NOT_RUN', action='not-git', reason='Not a git checkout')
            else:
                item['from'] = _head(layer.path)
                status, action, reason = apply_ref(layer.path, wanted)
                item.update(status=status, action=action, to=_head(layer.path))
                if reason:
                    item['reason'] = reason
                if status == 'PASS' and layer.writable:
                    item['hooks'] = _install_hooks(layer).get('hooks', [])
            results.append(item)
        if ref is not None and results and results[0]['status'] == 'PASS' and ref != config.layers[layer_id].ref:
            edited = edit_config(config.path, [_set_layer_op(layer_id, 'ref', ref)])
        statuses = {item['status'] for item in results}
        if 'BLOCKED' in statuses:
            status = 'BLOCKED'
        elif 'PASS' not in statuses and 'NOT_RUN' in statuses:
            status = 'NOT_RUN'  # nothing was updated (e.g. only non-git checkouts)
        else:
            status = 'PASS'
        return _report(status, layers=results, **(edited or {}))
    except (LayerError, OSError) as exc:
        return _blocked(exc)


def layers_list(project=None, *, layers=None, isolation=False):
    try:
        report = resolve_layers(project, layers=layers).to_list_json()
    except LayerError as exc:
        return _blocked(exc, generator_version=generator_version())
    if isolation:  # additive field; the status of `list` stays the resolution status
        checked = layers_check(isolation=True)
        report['isolation'] = {'status': checked['status'], 'findings': checked.get('findings', []),
                               **({'reason': checked['reason']} if checked.get('reason') else {})}
    return report


# --------------------------------------------------------------------------- isolation (K10)

LEGACY_LIBRARY_DIR = ('.local', 'copilot-library')   # known location of the old library clone
LEGACY_REPO_NAME = 'copilotskills'                   # old library repository name under `discover_paths`
_WALK_SKIP = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}
_LEGACY_SEARCH_DEPTH = 4


def _path_key(path):
    return path_case_key(os.path.realpath(os.path.expanduser(str(path))))


def _library_roots(path):
    """[(root, meta, error)] of `layer.yaml` at `path`, else one level below (a checkout with `subdir`)."""
    path = Path(path)
    candidates = [path]
    if not ((path / LAYER_FILE).exists() or is_link(path / LAYER_FILE)):
        try:
            candidates = sorted(child for child in path.iterdir()
                                if child.is_dir() and not is_link(child) and child.name not in _WALK_SKIP)
        except OSError:
            candidates = []
    found = []
    for candidate in candidates:
        if (candidate / LAYER_FILE).exists() or is_link(candidate / LAYER_FILE):
            meta, error = read_layer_yaml(candidate)
            found.append((candidate, meta, error or (None if meta else f'{candidate / LAYER_FILE} is unreadable')))
    return found


def _legacy_repo_dirs(discover_paths, depth=_LEGACY_SEARCH_DEPTH):
    """Directories named like the old library repository (`CopilotSkills`, any case) under `discover_paths`
    (at most `depth` levels deep; hidden directories, `.git` and `node_modules` skipped)."""
    found = []
    if isinstance(discover_paths, str):
        discover_paths = [discover_paths]
    for value in discover_paths if isinstance(discover_paths, list) else []:
        if not isinstance(value, str) or not value.strip():
            continue
        root = Path(value).expanduser()
        if not root.is_absolute() or not root.is_dir():
            continue
        base = len(root.parts)
        for current, dirs, _ in os.walk(root, followlinks=False):
            here = Path(current)
            dirs[:] = sorted(name for name in dirs if name not in _WALK_SKIP and not name.startswith('.'))
            hits = [name for name in dirs if name.lower() == LEGACY_REPO_NAME]
            found.extend(here / name for name in hits)
            if len(here.parts) - base + 1 >= depth:
                dirs[:] = []
            else:
                dirs[:] = [name for name in dirs if name not in hits]
    return found


def _identity_layer(path):
    """`layer` declared by an identity file (`{schema, layer, terms}` or `{schema, layer, empty}`), else None."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if isinstance(data, dict) and ('terms' in data or 'empty' in data) and isinstance(data.get('layer'), str):
        return data['layer']
    return None


def _identity_files(tree, everywhere=False):
    """YAML files under `leakguard/` directories of `tree` (every YAML file when `everywhere`)."""
    found = []
    for current, dirs, files in os.walk(tree, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in _WALK_SKIP)
        if not everywhere and Path(current).name != 'leakguard':
            continue
        for name in sorted(files):
            path = Path(current) / name
            if name.endswith(('.yaml', '.yml')) and not is_link(path):
                found.append(path)
    return found


def isolation_findings(config, system=None):
    """Findings of `layers check --isolation` (K10); every violation is `error` (FAIL).

    (1) each configured layer id is in `isolation.allowed_layers` and each present layer has a
    `layer.yaml` with an allowed id (the legacy bridge has none: FAIL on purpose, K15);
    (2) the layers directory of the system, `legacy_library`, the old `library:` path, the known
    location `~/.local/copilot-library` and `CopilotSkills` directories under `discover_paths`
    hold no library with an id outside the list and no library directory without `layer.yaml`;
    (3) no identity file (`leakguard/*.yaml` of these trees, `leakguard.identity_dir`) declares
    a layer outside the list."""
    system = system or current_system()
    allowed = set(config.allowed_layers or [])
    findings, seen, trees = [], set(), []

    def error(code, message, layer=None):
        findings.append(_finding(code, 'error', message, layer))

    for layer in config.ordered_layers():
        if layer.id not in allowed:
            error('isolation-layer-not-allowed', f'Layer {layer.id} is configured but not in isolation.allowed_layers',
                  layer.id)
        if layer.synthetic:  # before the existence check: a bridge to a missing directory still fails (K15)
            error('isolation-legacy-bridge', f'Layer {layer.id} is the legacy bridge: an old library without '
                  'layer.yaml; isolation can pass only after the bridge is removed', layer.id)
        if not layer.path.exists():
            findings.append(_finding('isolation-layer-absent', 'info', f'Layer {layer.id} has no checkout at '
                                     f'{layer.path}', layer.id))
            continue
        seen.update({_path_key(layer.path), _path_key(layer.root)})
        trees.append(layer.root)  # the layer's library, not the whole clone (examples in `docs/` are no identity)
        if layer.synthetic:
            pass
        elif layer.meta_error:
            error('isolation-layer-yaml-invalid', layer.meta_error, layer.id)
        elif layer.meta is None:
            error('isolation-no-layer-yaml', f'Layer {layer.id}: {layer.root} has no layer.yaml', layer.id)
        elif layer.meta['id'] not in allowed:
            error('isolation-foreign-library', f'{layer.root}: layer.yaml declares {layer.meta["id"]}, which is not '
                  'in isolation.allowed_layers', layer.id)
    candidates = []
    layers_dir = default_layers_dir(system)
    if layers_dir.is_dir():
        try:
            candidates += [('checkout', child) for child in sorted(layers_dir.iterdir()) if child.is_dir()]
        except OSError:
            pass
    legacy = [config.legacy_library] if config.legacy_library else []
    old = legacy_library_key(config.raw)
    if old and Path(old).expanduser().is_absolute():
        legacy.append(Path(old).expanduser())
    legacy.append(Path.home().joinpath(*LEGACY_LIBRARY_DIR))
    legacy += _legacy_repo_dirs(config.raw.get('discover_paths'))
    candidates += [('legacy', path) for path in legacy]
    for origin, path in candidates:
        path = Path(path)
        if not path.is_dir() or _path_key(path) in seen:
            continue
        seen.add(_path_key(path))
        roots = _library_roots(path)
        # identity files are looked up in the library directory only; a tree without `layer.yaml` is scanned whole
        trees.extend([root for root, _meta, _problem in roots] or [path])
        if not roots:
            if origin == 'checkout':
                error('isolation-unknown-checkout', f'{path}: a checkout in the layers directory without layer.yaml')
            else:
                error('isolation-legacy-library', f'{path}: an old library clone (no layer.yaml); delete it after '
                      'moving any local changes')
            continue
        for root, meta, problem in roots:
            if problem:
                error('isolation-layer-yaml-invalid', problem)
            elif meta['id'] not in allowed:
                error('isolation-foreign-library', f'{root}: a library of layer {meta["id"]}, which is not in '
                      'isolation.allowed_layers', meta['id'])
    identity = [(tree, False) for tree in trees]
    if config.identity_dir is not None and config.identity_dir.is_dir():
        identity.append((config.identity_dir, True))
    reported = set()
    for tree, everywhere in identity:
        for path in _identity_files(tree, everywhere):
            owner = _identity_layer(path)
            if owner is not None and owner not in allowed and _path_key(path) not in reported:
                reported.add(_path_key(path))
                error('isolation-foreign-identity', f'{path}: identity file of layer {owner}, which is not in '
                      'isolation.allowed_layers', owner)
    return findings


# --------------------------------------------------------------------------- layers check / use

_SEVERITY_STATUS = {'blocked': 'BLOCKED', 'error': 'FAIL'}


def _public_finding(item):
    return {'code': item['code'], 'layer': item.get('layer'), 'message': item['message'],
            'severity': item.get('severity')}


def layers_check(project=None, *, layers=None, isolation=False, system=None):
    """`layers check` (K2, K4, K9, K12, K15): findings of `check_layers` for the effective layers
    (BLOCKED > FAIL > PASS); `--isolation` (K10): only the isolation findings (NOT_RUN without
    `isolation.allowed_layers`). Schema: `{schema, status, findings: [{code, layer, message}]}` plus
    additive `severity`, `layers` (per-layer status; absent layers NOT_RUN) and `project`."""
    try:
        if isolation:
            ignored = [flag for flag, value in (('--project', project), ('--layer', layers)) if value]
            warnings = ([f'{" and ".join(ignored)} ignored: --isolation checks the whole machine (machine config)']
                        if ignored else [])
            config = load_machine_config()
            if config.allowed_layers is None:
                return _report('NOT_RUN', isolation=True, findings=[], warnings=warnings,
                               reason='isolation.allowed_layers is not configured in the machine config')
            findings = isolation_findings(config, system)
            return _report(findings_status(findings), isolation=True, allowed_layers=list(config.allowed_layers),
                           findings=[_public_finding(item) for item in findings], warnings=warnings)
        layerset = resolve_layers(project, layers=layers, system=system)
        findings = check_layers(layerset, version=None)
        per_layer = []
        for layer in layerset.layers:
            severities = {item['severity'] for item in findings if item.get('layer') == layer.id}
            status = next((_SEVERITY_STATUS[key] for key in ('blocked', 'error') if key in severities), 'PASS')
            per_layer.append({**layer.as_json(), 'status': status})
        for note in layerset.notes:
            known = layerset.config.layers.get(note.get('layer') or '')
            if note['code'] == 'layer-absent' and known is not None:
                per_layer.append({**known.as_json(), 'status': 'NOT_RUN'})
        return _report(findings_status(findings), project=str(layerset.project) if layerset.project else None,
                       rule=layerset.rule['index'] if layerset.rule else None, layers=per_layer,
                       findings=[_public_finding(item) for item in findings])
    except (LayerError, OSError) as exc:
        code = getattr(exc, 'code', 'layer-error')
        return _blocked(exc, findings=[{'code': code, 'layer': None, 'message': str(exc), 'severity': 'blocked'}])


def _default_rule(config):
    return next((rule for rule in config.rules if rule['match'] == ['**']), None)


def _rule_use(config, layer_id, base=None, warnings=None):
    """`use` of a new rule for `layer_id`: the default rule's `use`, with `layer_id` inserted before the
    personal layer (a personal `layer_id` replaces the personal layer). Without a default rule (`**`) the
    base is `[core]` plus the only configured personal layer, so the order of `layers use` commands never
    drops it; with several personal layers none is added and `warnings` gets the instruction."""
    if base is None:
        default = _default_rule(config)
        if default:
            base = list(default['use'])
        else:
            core = next((layer.id for layer in config.layers.values() if layer.kind == 'core'), None)
            personal = [layer.id for layer in config.layers.values() if layer.kind == 'personal']
            base = ([core] if core else []) + (personal if len(personal) == 1 else [])
            if len(personal) > 1 and warnings is not None and config.layers.get(layer_id) is not None \
                    and config.layers[layer_id].kind != 'personal':
                warnings.append(f'No default rule (`**`) and several personal layers ({", ".join(personal)}): the new '
                                'rule has no personal layer. Choose one with `skillsync layers use <personal> --match '
                                "'**'` and add it to this rule if needed")
    kinds = {item: (config.layers[item].kind if item in config.layers else None) for item in base}
    use = [item for item in base if item != layer_id]
    kind = config.layers[layer_id].kind if layer_id in config.layers else None
    if kind == 'personal':
        return [item for item in use if kinds.get(item) != 'personal'] + [layer_id]
    position = next((index for index, item in enumerate(use) if kinds.get(item) == 'personal'), len(use))
    return use[:position] + [layer_id] + use[position:]


def layers_use(layer_id, match, *, position=None):
    """`layers use <id> --match G [--position N]` (K9): a new `layer_rules` entry with the `use` of the
    default rule (`**`) and `<id>` before the personal layer, inserted before the default rule (or at
    index N, 0-based). An identical existing rule = PASS without a change."""
    try:
        config = load_machine_config()
        if not config.layers:
            raise _no_layers_error(config)
        if layer_id not in config.layers:
            raise LayerError(f'Unknown layer {layer_id}; add it first with `skillsync layers add`', 'bad-argument')
        if config.layers[layer_id].kind == 'core':
            raise LayerError('The core layer is part of every rule; `layers use` takes an org or personal layer',
                             'bad-argument')
        globs = _string_list(list(match or []), '--match')
        warnings = []
        rule = {'match': globs, 'use': _rule_use(config, layer_id, warnings=warnings)}
        for existing in config.rules:
            if existing['match'] == rule['match'] and existing['use'] == rule['use']:
                return _report('PASS', changed=False, rule=rule, index=existing['index'], config=str(config.path),
                               warnings=warnings + ['An identical layer_rules entry exists; nothing changed'])
        default = _default_rule(config)
        if position is None:
            index = default['index'] if default else len(config.rules)
        elif isinstance(position, bool) or not isinstance(position, int) or not 0 <= position <= len(config.rules):
            raise LayerError(f'--position must be 0..{len(config.rules)} (0-based index in layer_rules)', 'bad-argument')
        else:
            index = position
        if default is not None and index > default['index']:
            warnings.append('The rule is placed after the default rule (`**`), so it never matches')
        edited = edit_config(config.path, [_insert_rule_op(rule, index)])
        return _report('PASS', rule=rule, index=index, warnings=warnings, **edited)
    except (LayerError, OSError) as exc:
        return _blocked(exc)


def layers_ensure_core_rule():
    """`skillsync setup`: a machine with only the core layer has no `layer_rules` entry, and `link` needs one
    (`no-rule`). With no rules at all this inserts the default rule `**: [core]`; existing rules are never touched."""
    try:
        config = load_machine_config()
        core = next((layer for layer in config.layers.values() if layer.kind == 'core'), None)
        if core is None:
            raise LayerError('No core layer is configured; add one with `skillsync layers add core --kind core ...`',
                             'bad-argument')
        if config.rules:
            return _report('PASS', changed=False, rules=len(config.rules), config=str(config.path))
        rule = {'match': ['**'], 'use': [core.id]}
        edited = edit_config(config.path, [_insert_rule_op(rule, 0)])
        return _report('PASS', rule=rule, **edited)   # `edited` carries `changed`
    except (LayerError, OSError) as exc:
        return _blocked(exc)


# --------------------------------------------------------------------------- layers migrate (K11, K15)

def _pairs(values, label):
    """`ID=VALUE` values of a repeatable flag -> {id: [values]} (LayerError `bad-argument` otherwise)."""
    result = {}
    for value in values or []:
        key, sep, rest = str(value).partition('=')
        if not sep or not _ID.fullmatch(key) or not rest.strip():
            raise LayerError(f'{label} expects ID=VALUE, got {value!r}', 'bad-argument')
        result.setdefault(key, []).append(rest.strip())
    return result


def _single(pairs, label):
    for key, values in pairs.items():
        if len(values) > 1:
            raise LayerError(f'{label} {key}= given twice', 'bad-argument')
    return {key: values[0] for key, values in pairs.items()}


def _stored_path(value):
    """Path as written in the config: `~` and absolute paths kept, relative paths made absolute."""
    if value.startswith('~') or os.path.isabs(value):
        return value
    return str(Path(os.path.abspath(value)))


def _origin_url(path):
    process = _git_text(path, 'remote', 'get-url', 'origin')
    url = process.stdout.strip() if process is not None and not process.returncode else ''
    return url or None


def migrate_layer_entry(layer_id, kind, stored):
    """Config entry of a local layer used in place (`path` = the directory, `source` = its git origin)."""
    checkout = _expand(stored, f'layers.{layer_id}.path')
    return {'id': layer_id, 'kind': kind, 'source': _origin_url(checkout) or stored, 'path': stored}


def _replace_bridge_op(bridge_id, entry):
    """Replace the bridge entry with a real core layer (rewrite with backup: no textual edit)."""
    def mutate(data):
        data['layers'] = [copy.deepcopy(entry) if isinstance(item, dict) and item.get('id') == bridge_id else item
                          for item in data.get('layers') or []]
        if entry['id'] != bridge_id:
            for rule in data.get('layer_rules') or []:
                if isinstance(rule, dict) and isinstance(rule.get('use'), list):
                    rule['use'] = [entry['id'] if item == bridge_id else item for item in rule['use']]
            isolation = data.get('isolation')
            if isinstance(isolation, dict) and isinstance(isolation.get('allowed_layers'), list):
                isolation['allowed_layers'] = [entry['id'] if item == bridge_id else item
                                               for item in isolation['allowed_layers']]
    return (lambda text: None, (), mutate)


def _set_rule_use_op(updates):
    """Rewrite `use` of existing `layer_rules` entries ({index: use}); a rewrite with backup (no textual edit)."""
    def mutate(data):
        rules = data.get('layer_rules') or []
        for index, use in updates.items():
            if 0 <= index < len(rules) and isinstance(rules[index], dict):
                rules[index]['use'] = list(use)
    return (lambda text: None, (), mutate)


def _extend_allowed_layers_op(layer_ids):
    """Append layer ids to `isolation.allowed_layers` (those missing); a rewrite with backup."""
    def mutate(data):
        isolation = data.get('isolation')
        if isinstance(isolation, dict) and isinstance(isolation.get('allowed_layers'), list):
            for layer_id in layer_ids:
                if layer_id not in isolation['allowed_layers']:
                    isolation['allowed_layers'].append(layer_id)
    return (lambda text: None, (), mutate)


def migrate_config(*, sources=(), kinds=(), matches=(), apply=False):
    """`layers migrate` (K11, D41): the machine config with the old single-library key -> `layers` +
    `layer_rules` (+ `isolation`, `leakguard.targets` stubs); the old key becomes `legacy_library`; every
    other key is kept. Dry-run unless `apply`; a backup before writing; idempotent (an already migrated
    config gives PASS without a change).

    Core layer: `--layer-source core=PATH`, else the old library when it has `layer.yaml`. A config with the
    0.9.x legacy bridge (a `synthetic` core entry) gets the bridge replaced by that core layer (D162). `--layer-source ID=PATH`
    registers local layers in place (`--kind ID=KIND` when the directory has no `layer.yaml`);
    `--match ID=GLOB` gives an org layer its own rule. Remote sources are added later with `layers add`.

    A config with the legacy bridge (synthetic core) behaves like the old `library:` key for the layers added in
    the same call (D166): a personal layer joins the default rule `**` (rewritten, not duplicated) and every org rule, and
    every added layer joins `isolation.allowed_layers` (the config is rewritten, comments are lost, a backup is
    kept). A config already on layers keeps its rules and `allowed_layers` (decision 2.4); the warnings say what to
    add with `layers use`."""
    path = config_path()
    text = read_config_text(path)
    source_map = _single(_pairs(sources, '--layer-source'), '--layer-source')
    kind_map = _single(_pairs(kinds, '--kind'), '--kind')
    match_map = _pairs(matches, '--match')
    if text is None and not source_map:
        raise LayerError(f'Missing machine config: {path}; nothing to migrate (pass --layer-source core=PATH to '
                         'create one)', 'missing-config')
    try:
        raw = (yaml.safe_load(text) if text and text.strip() else {}) or {}
    except yaml.YAMLError as exc:
        raise LayerError(f'Machine config is not valid YAML: {path} ({type(exc).__name__})') from None
    if not isinstance(raw, dict):
        raise LayerError(f'Machine config must be a mapping: {path}')
    config = parse_machine_config(raw, path, exists=text is not None)
    old = legacy_library_key(raw)
    for layer_id, kind in kind_map.items():
        if layer_id not in source_map:
            raise LayerError(f'--kind {layer_id}= needs --layer-source {layer_id}=PATH', 'bad-argument')
        if kind not in LAYER_KINDS:
            raise LayerError(f'--kind {layer_id}= must be one of: {", ".join(LAYER_KINDS)}', 'bad-argument')
    changes, warnings, operations, new_entries = [], [], [], []
    core = next((layer for layer in config.layers.values() if layer.kind == 'core'), None)
    replace_bridge = None

    for layer_id, value in source_map.items():
        kind = kind_map.get(layer_id)
        if not _looks_local(value):
            raise LayerError(f'--layer-source {layer_id}: a remote source is cloned with `skillsync layers add '
                             f'{layer_id} --kind <kind> --source {value}` (after the migration)', 'bad-argument')
        stored = _stored_path(value)
        checkout = _expand(stored, f'--layer-source {layer_id}')
        if not checkout.is_dir():
            raise LayerError(f'--layer-source {layer_id}: directory not found: {checkout}', 'bad-argument')
        meta, problem = read_layer_yaml(checkout)
        if problem:
            raise LayerError(problem, 'layer-yaml-invalid')
        if meta is not None:
            if meta['id'] != layer_id:
                raise LayerError(f'{checkout / LAYER_FILE} declares id {meta["id"]}, expected {layer_id}', 'layer-yaml-invalid')
            if kind is not None and kind != meta['kind']:
                raise LayerError(f'{checkout / LAYER_FILE} declares kind {meta["kind"]}, not {kind}', 'layer-yaml-invalid')
            kind = meta['kind']
        elif kind is None:
            raise LayerError(f'--layer-source {layer_id}: {checkout} has no layer.yaml; pass --kind '
                             f'{layer_id}=core|org|personal', 'bad-argument')
        else:
            warnings.append(f'{checkout} has no layer.yaml; `layers check` reports BLOCKED until it exists')
        existing = config.layers.get(layer_id)
        if existing is not None and not (existing.synthetic and kind == 'core'):
            if existing.kind == kind and _path_key(existing.path) == _path_key(checkout):
                continue
            raise LayerError(f'Layer {layer_id} is already configured differently ({existing.kind} at {existing.path})',
                             'conflict')
        entry = migrate_layer_entry(layer_id, kind, stored)
        if kind == 'core' and core is not None:
            if not core.synthetic:
                if _path_key(core.path) == _path_key(checkout) and core.id == layer_id:
                    continue
                raise LayerError(f'A core layer is already configured ({core.id} at {core.path})', 'conflict')
            if replace_bridge is not None:
                raise LayerError(f'Two core layers replace the legacy bridge {core.id}: {replace_bridge[1]["id"]} and '
                                 f'{layer_id}; pass one --layer-source of kind core', 'conflict')
            replace_bridge = (core.id, entry)
            continue
        new_entries.append(entry)

    if core is not None and core.synthetic and replace_bridge is None:
        raise LayerError(f'The machine config has the 0.9.x legacy bridge (core layer {core.id} is `synthetic`), which '
                         'this generator no longer supports; pass --layer-source core=PATH with a real core layer to '
                         'replace it', 'no-core')
    new_ids = [entry['id'] for entry in new_entries]
    if len(set(new_ids)) != len(new_ids) or any(item in config.layers for item in new_ids):
        raise LayerError('A layer id is defined twice', 'conflict')
    if core is None and not any(entry['kind'] == 'core' for entry in new_entries):
        if old:
            library = _expand(_stored_path(old), 'library')
            meta, problem = read_layer_yaml(library) if library.is_dir() else (None, None)
            if problem:
                raise LayerError(problem, 'layer-yaml-invalid')
            if meta is None or meta['kind'] != 'core':
                raise LayerError(f'The old library {library} has no core layer.yaml. Pass --layer-source core=PATH '
                                 'with a real core layer', 'no-core')
            new_entries.insert(0, migrate_layer_entry(meta['id'], 'core', _stored_path(old)))
        else:
            raise LayerError('No core layer: pass --layer-source core=PATH', 'no-core')

    all_kinds = {layer.id: layer.kind for layer in config.layers.values()}
    all_kinds.update({entry['id']: entry['kind'] for entry in new_entries})
    if replace_bridge is not None:
        all_kinds.pop(replace_bridge[0], None)
        all_kinds[replace_bridge[1]['id']] = 'core'
    personal = [item for item, kind in all_kinds.items() if kind == 'personal']
    if len(personal) > 1:
        raise LayerError(f'More than one personal layer: {", ".join(personal)}', 'conflict')
    for layer_id in match_map:
        if all_kinds.get(layer_id) != 'org':
            raise LayerError(f'--match {layer_id}= applies to org layers only', 'bad-argument')
    core_id = next(item for item, kind in all_kinds.items() if kind == 'core')
    personal_id = personal[0] if personal else None

    if old:
        if config.legacy_library is not None:
            if _path_key(config.legacy_library) != _path_key(_expand(_stored_path(old), 'library')):
                raise LayerError(f'legacy_library ({config.legacy_library}) differs from the old library key ({old})',
                                 'conflict')
            operations.append(_remove_top_op('library'))
        else:
            operations.append(_rename_top_op('library', 'legacy_library'))
        changes.append(f'library: {old} -> legacy_library (only to recognise old managed symlinks)')

    if replace_bridge is not None:
        operations.append(_replace_bridge_op(*replace_bridge))
        changes.append(f'layers: the bridge {replace_bridge[0]} replaced by the core layer {replace_bridge[1]["id"]} '
                       f'({replace_bridge[1]["path"]})')
    for entry in new_entries:
        operations.append(_add_layer_op(entry))
        changes.append(f'layers: + {entry["id"]} ({entry["kind"]}) at {entry["path"]}')

    bridge_config = bool(core is not None and core.synthetic)  # the legacy bridge is the core layer
    bridge_id = replace_bridge[0] if replace_bridge is not None else None

    def use_for(org=None):
        return [core_id] + ([org] if org else []) + ([personal_id] if personal_id else [])

    rule_updates = {}
    if not config.rules:
        rules = [{'match': list(globs), 'use': use_for(org)} for org, globs in match_map.items()]
        rules.append({'match': ['**'], 'use': use_for()})
        if 'layer_rules' in raw:
            operations.append(_remove_top_op('layer_rules'))
        operations.append(_append_top_op('layer_rules', rules))
        changes.extend(f'layer_rules: + {rule["match"]} -> {rule["use"]}' for rule in rules)
    else:
        default = _default_rule(config)
        index = default['index'] if default else len(config.rules)
        # A config with the legacy bridge (synthetic core) and a personal layer added in this call behaves like
        # the old `library:` key: the personal layer joins the default rule and the org rules (the default
        # rule is rewritten, not duplicated). A config already on layers is not touched (decision 2.4).
        joins_personal = bool(bridge_config and personal_id
                              and any(entry['kind'] == 'personal' for entry in new_entries))
        if joins_personal:
            for existing in config.rules:
                use = [core_id if item == bridge_id else item for item in existing['use']]
                if personal_id not in use:
                    use.append(personal_id)
                if use != list(existing['use']):
                    rule_updates[existing['index']] = use
        if rule_updates:
            operations.append(_set_rule_use_op(rule_updates))
            for rule_index, use in rule_updates.items():
                changes.append(f'layer_rules: {config.rules[rule_index]["match"]} -> {use} (personal layer added)')
        for org, globs in match_map.items():
            use = use_for(org) if joins_personal else _rule_use(config, org, warnings=warnings)
            rule = {'match': list(globs), 'use': use}
            if any(existing['match'] == rule['match']
                   and rule_updates.get(existing['index'], [core_id if item == bridge_id else item
                                                            for item in existing['use']]) == rule['use']
                   for existing in config.rules):
                continue  # the rule exists already (as rewritten by this call)
            operations.append(_insert_rule_op(rule, index))
            changes.append(f'layer_rules: + {rule["match"]} -> {rule["use"]} (at {index})')
            index += 1
    used = {item for rule in config.rules for item in rule['use']} if config.rules else set(use_for())
    used |= {item for use in rule_updates.values() for item in use}
    used |= set(match_map)
    for entry in new_entries:
        if entry['id'] not in used:
            warnings.append(f'No layer_rules entry uses {entry["id"]}; add one with `skillsync layers use '
                            f'{entry["id"]} --match <glob>`')

    if 'isolation' not in raw:
        ordered = [item for kind in LAYER_KINDS for item, value in all_kinds.items() if value == kind]
        operations.append(_append_top_op('isolation', {'allowed_layers': ordered}))
        changes.append(f'isolation.allowed_layers: {ordered}')
    elif config.allowed_layers is None:
        warnings.append('`isolation:` has no `allowed_layers`; left unchanged. Add `allowed_layers: [<layer ids>]` '
                        'under `isolation:` (needed by `layers check --isolation` and `layers migrate --finish`)')
    else:
        allowed = list(config.allowed_layers or [])
        if bridge_id is not None:
            allowed = [core_id if item == bridge_id else item for item in allowed]
        if bridge_config:
            extend = [entry['id'] for entry in sorted(new_entries, key=lambda item: LAYER_KINDS.index(item['kind']))
                      if entry['id'] not in allowed]
            if extend:  # the legacy bridge config: layers added in this call join the isolation list
                operations.append(_extend_allowed_layers_op(extend))
                changes.append(f'isolation.allowed_layers: + {", ".join(extend)}')
                allowed += extend
        missing = [item for item in all_kinds if item not in allowed]
        if missing:
            warnings.append(f'isolation.allowed_layers lacks {", ".join(missing)}; left unchanged')

    targets = dict(config.targets)
    for entry in new_entries + ([replace_bridge[1]] if replace_bridge else []):
        if entry['id'] in targets:
            continue
        stub = target_stub(entry['kind'], _expand(entry['path'], 'path'), entry.get('source'))
        targets[entry['id']] = stub
        operations.append(_add_target_op(entry['id'], stub))
        changes.append(f'leakguard.targets: + {entry["id"]} (stub, scope {stub["scope"]})')

    if not operations:
        return _report('PASS', dry_run=not apply, changed=False, changes=[], warnings=warnings, config=str(path),
                       message='The machine config is already migrated; nothing to do')
    edited = edit_config(path, operations, apply=apply)
    if apply:
        hint = ('Check with `skillsync layers check` and `skillsync link --dry-run`; then `skillsync layers migrate '
                '--manifests` for project manifests')
    else:
        hint = 'Dry-run: nothing written. Rerun with --apply to write (a backup copy is kept)'
    return _report('PASS', dry_run=not apply, changes=changes, warnings=warnings, next=hint, **edited)


def migrate_manifest_text(text):
    """Remove `library:` from the top-level `sync:` block of a manifest (the block too when it becomes
    empty); None when the layout is uncertain (inline mapping, multi-line value)."""
    lines = text.splitlines(keepends=True)
    _ensure_newline(lines)
    block = _top_block(lines, 'sync')
    if block is None or block[2]:
        return None
    start, end, _ = block
    content = [index for index in range(start + 1, end)
               if lines[index].strip() and not lines[index].lstrip().startswith('#')]
    if not content:
        return None
    child = len(lines[content[0]]) - len(lines[content[0]].lstrip())
    keyed = [index for index in content if len(lines[index]) - len(lines[index].lstrip()) == child]
    target = next((index for index in keyed if re.match(r'^\s*library:\s*\S', lines[index])), None)
    if target is None:
        return None
    following = [index for index in content if index > target]
    if following and len(lines[following[0]]) - len(lines[following[0]].lstrip()) > child:
        return None  # multi-line value
    del lines[target]
    if len(keyed) == 1:  # `library` was the only key: drop the whole block
        last = _last_content(lines, start, end - 1)
        del lines[start:last + 1]
    return ''.join(lines)


def migrate_discover_manifests(raw, projects=()):
    """Manifests for `--manifests`: the given projects, else every `.github/.skillsync.yaml` under
    `discover_paths` (as `skillsync link` without a path)."""
    found, warnings = [], []
    if projects:
        for value in projects:
            path = Path(value).expanduser()
            manifest = path if path.is_file() else path / '.github' / '.skillsync.yaml'
            if manifest.is_file():
                found.append(manifest)
            else:
                warnings.append(f'No manifest at {manifest}')
    else:
        roots = raw.get('discover_paths') or []
        for root in [roots] if isinstance(roots, str) else roots:
            root_path = Path(str(root)).expanduser()
            if root_path.is_dir():
                found.extend(root_path.rglob('.github/.skillsync.yaml'))
    unique = []
    for manifest in found:
        if manifest.resolve() not in [item.resolve() for item in unique]:
            unique.append(manifest)
    return sorted(unique), warnings


def migrate_encode_path(path):
    """One safe directory name for a project path: readable characters kept, the rest `_`, plus a short hash
    of the resolved path (distinct projects never share a backup directory)."""
    resolved = os.path.realpath(os.path.expanduser(str(path)))
    readable = re.sub(r'[^A-Za-z0-9._-]+', '_', resolved).strip('_.')[-80:] or 'project'
    digest = hashlib.sha256(resolved.encode('utf-8', 'surrogateescape')).hexdigest()[:10]
    return f'{readable}-{digest}'


def migrate_backup_dir(system=None):
    """New `<data dir>/backups/manifests/<stamp>` for the manifest backups of one `migrate --manifests --apply`."""
    base = data_home(system) / 'backups' / 'manifests'
    stamp = time.strftime('%Y%m%d-%H%M%S')
    candidate, counter = base / stamp, 1
    while candidate.exists():
        counter += 1
        candidate = base / f'{stamp}-{counter}'
    candidate.mkdir(parents=True)
    return candidate


def migrate_manifests(projects=(), *, apply=False):
    """`layers migrate --manifests` (K11): remove the ignored library setting (`sync: {library}`) from the
    project manifests. Backups never land in the project repositories (an untracked `.bak` could be committed and
    would be scanned by the leak guard): `<data dir>/backups/manifests/<stamp>/<encoded project path>/.skillsync.yaml`
    (`migrate_backup_dir`); the report gives each backup path."""
    raw = {}
    text = read_config_text()
    if text and text.strip():
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise LayerError(f'Machine config is not valid YAML ({type(exc).__name__})') from None
    if not isinstance(raw, dict):
        raise LayerError('Machine config must be a mapping')
    manifests, warnings = migrate_discover_manifests(raw, projects)
    results, changes = [], []
    backup_root = None  # created only when a manifest is written

    def drop(data):
        data['sync'].pop('library', None)
        if not data['sync']:
            data.pop('sync')

    for manifest in manifests:
        try:
            original = manifest.read_text(encoding='utf-8')
            data = yaml.safe_load(original)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            warnings.append(f'{manifest}: unreadable ({type(exc).__name__}); skipped')
            continue
        sync = data.get('sync') if isinstance(data, dict) else None
        if not isinstance(sync, dict) or 'library' not in sync:
            continue
        new_text, _, rewritten = _edit_text(original, [(migrate_manifest_text, (), drop)], str(manifest))
        item = {'manifest': str(manifest), 'rewritten': rewritten, 'backup': None}
        if apply:
            try:
                backup_root = backup_root or migrate_backup_dir()
                project = manifest.parent.parent if manifest.parent.name == '.github' else manifest.parent
                backup = backup_root / migrate_encode_path(project) / manifest.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(manifest, backup)  # a symlinked manifest: the target's content
                item['backup'] = str(backup)
                _write_atomic(manifest, new_text)  # a symlinked manifest is written at its target
            except OSError as exc:
                # Stop at the first IO error; the report keeps every backup written so far (and this one, if any).
                item['error'] = f'{type(exc).__name__}: {exc}'
                results.append(item)
                return _report('BLOCKED', code='io-error', dry_run=False, changed=any('error' not in x for x in results),
                               reason=f'Writing {manifest} failed ({type(exc).__name__}); earlier manifests were '
                                      'written, their backups are listed', manifests=results, changes=changes,
                               warnings=warnings)
        results.append(item)
        changes.append(f'{manifest}: remove the ignored library setting from `sync`'
                       + (' (whole file rewritten, comments only in the backup)' if rewritten else ''))
    hint = None if apply or not results else 'Dry-run: nothing written. Rerun with --apply to write (with backups)'
    report = _report('PASS', dry_run=not apply, changed=bool(results), manifests=results, changes=changes,
                     warnings=warnings)
    if hint:
        report['next'] = hint
    return report


def migrate_finish(*, apply=False, system=None):
    """`layers migrate --finish` (K11): remove `legacy_library` once the old clone is deleted and
    `layers check --isolation` gives PASS; BLOCKED with the reasons otherwise."""
    config = load_machine_config()
    if not config.layers:
        raise _no_layers_error(config)
    if config.legacy_library_key:
        raise LayerError('The old `library:` key is still in the machine config; run `skillsync layers migrate` first',
                         'legacy-config')
    if config.legacy_library is None:
        return _report('PASS', dry_run=not apply, changed=False, changes=[], config=str(config.path),
                       message='No legacy_library in the machine config; nothing to do')
    reasons, findings = [], []
    in_use = {_path_key(path) for layer in config.layers.values() for path in (layer.path, layer.root)}
    if _path_key(config.legacy_library) in in_use:
        pass  # the old library became a configured layer: not an old clone to delete
    elif config.legacy_library.exists() or is_link(config.legacy_library):
        reasons.append(f'the old library clone still exists at {config.legacy_library}; move any local changes and '
                       'delete it first')
    if config.allowed_layers is None:
        reasons.append('isolation.allowed_layers is not configured, so `layers check --isolation` cannot PASS')
    else:
        findings = [_public_finding(item) for item in isolation_findings(config, system)]
        if findings_status(findings) != 'PASS':
            reasons.append('`skillsync layers check --isolation` does not PASS')
    if reasons:
        return _report('BLOCKED', code='finish-blocked', reason='Cannot finish the migration: ' + '; '.join(reasons),
                       findings=findings)
    edited = edit_config(config.path, [_remove_top_op('legacy_library')], apply=apply)
    report = _report('PASS', dry_run=not apply, changes=[f'legacy_library: {config.legacy_library} removed'], **edited)
    if not apply:
        report['next'] = 'Dry-run: nothing written. Rerun with --apply to write (a backup copy is kept)'
    return report


def layers_migrate(*, adopt_legacy=None, sources=(), kinds=(), matches=(), manifests=False, finish=False,
                   projects=(), apply=False):
    """Entry of `layers migrate`: config (default), `--manifests` or `--finish`; dry-run unless `apply`."""
    try:
        if adopt_legacy is not None:
            raise LayerError('--adopt-legacy (the 0.9.x legacy bridge) was removed in 1.0; pass '
                             '--layer-source core=PATH with a real core layer', 'bad-argument')
        if manifests or finish:
            extra = [name for name, value in (('--layer-source', sources), ('--kind', kinds), ('--match', matches)) if value]
            if extra or (manifests and finish):
                raise LayerError(f'--manifests and --finish are separate steps (not with {", ".join(extra) or "each other"})',
                                 'bad-argument')
            if finish:
                if projects:
                    raise LayerError('--project applies to --manifests only', 'bad-argument')
                return migrate_finish(apply=apply)
            return migrate_manifests(projects, apply=apply)
        if projects:
            raise LayerError('--project applies to --manifests only', 'bad-argument')
        return migrate_config(sources=sources, kinds=kinds, matches=matches, apply=apply)
    except (LayerError, OSError) as exc:
        return _blocked(exc)


# --------------------------------------------------------------------------- CLI

def _print(report, json_output, title):
    from .common import exit_code, redact
    if json_output:
        print(json.dumps(redact(report), ensure_ascii=False, indent=2, default=str))
        return exit_code(report)
    stream = sys.stdout if report['status'] in ('PASS', 'NOT_RUN') else sys.stderr
    suffix = ' (dry-run)' if report.get('dry_run') else ''
    print(f'{title}: {report["status"]}{suffix}', file=stream)
    for key in ('reason', 'message'):
        if report.get(key):
            print(f'  {report[key]}', file=stream)
    for layer in report.get('layers', []):
        if 'kind' in layer:
            flags = 'rw' if layer.get('writable') else 'ro'
            status = f'{layer["status"]:<8} ' if layer.get('status') else ''
            print(f'  {status}{layer["id"]:<16} {layer["kind"]:<9} {layer.get("version") or "-":<9} {flags:<14} '
                  f'{layer["root"]}', file=stream)
        else:
            line = f'  {layer["id"]}: {layer["status"]} ({layer.get("action")})'
            if layer.get('reason'):
                line += f' {layer["reason"]}'
            print(line, file=stream)
    for note in report.get('notes', []):
        print(f'  [{note["severity"]}] {note["message"]}', file=stream)
    for finding in report.get('findings', []):
        if finding.get('code') == report.get('code') and finding.get('message') == report.get('reason'):
            continue
        where = f' ({finding["layer"]})' if finding.get('layer') else ''
        print(f'  [{finding.get("severity") or "info"}] {finding["code"]}{where}: {finding["message"]}', file=stream)
    isolation = report.get('isolation')
    if isinstance(isolation, dict):
        print(f'  isolation: {isolation["status"]}', file=stream)
        for finding in isolation.get('findings', []):
            print(f'    [{finding.get("severity") or "info"}] {finding["code"]}: {finding["message"]}', file=stream)
    for change in report.get('changes', []):
        print(f'  {"would change" if report.get("dry_run") else "changed"}: {change}', file=stream)
    for key in ('config', 'backup', 'next'):
        if report.get(key):
            print(f'  {key}: {report[key]}', file=stream)
    for item in report.get('manifests', []):
        if item.get('backup'):
            print(f'  backup: {item["backup"]}', file=stream)
    for hook in report.get('hooks', []):
        print(f'  hook {hook["name"]}: {hook["action"]}', file=stream)
    for warning in report.get('warnings', []):
        print(f'  [warn] {warning}', file=stream)
    return exit_code(report)


def _cmd_init(args):
    return _print(layers_init(args.directory, args.kind, args.id), args.json, 'layers init')


def _cmd_add(args):
    writable = None if args.writable is None else args.writable
    report = layers_add(args.id, args.kind, args.source, subdir=args.subdir, ref=args.ref, path=args.path,
                        writable=writable)
    return _print(report, args.json, 'layers add')


def _cmd_update(args):
    return _print(layers_update(args.id, ref=args.ref), args.json, 'layers update')


def _cmd_list(args):
    return _print(layers_list(args.project, layers=args.layers, isolation=args.isolation), args.json, 'layers list')


def _cmd_check(args):
    return _print(layers_check(args.project, layers=args.layers, isolation=args.isolation), args.json, 'layers check')


def _cmd_use(args):
    return _print(layers_use(args.id, args.match, position=args.position), args.json, 'layers use')


def _cmd_migrate(args):
    report = layers_migrate(adopt_legacy=args.adopt_legacy, sources=args.layer_sources, kinds=args.kinds,
                            matches=args.matches, manifests=args.manifests, finish=args.finish, projects=args.projects,
                            apply=args.apply)
    return _print(report, args.json, 'layers migrate')


def register(subparsers):
    parser = subparsers.add_parser('layers', help='Layers of the library (machine config)')
    actions = parser.add_subparsers(dest='layers_command', required=True)
    init = actions.add_parser('init', help='Create a new layer skeleton (git repo with leakguard hooks)')
    init.add_argument('directory')
    init.add_argument('--kind', required=True, choices=['personal', 'org'])
    init.add_argument('--id', help='Layer id (default: directory name)')
    init.add_argument('--json', action='store_true')
    init.set_defaults(func=_cmd_init)
    add = actions.add_parser('add', help='Clone a layer and register it in the machine config')
    add.add_argument('id')
    add.add_argument('--kind', required=True, choices=list(LAYER_KINDS))
    add.add_argument('--source', required=True, help='Repository URL or local path')
    add.add_argument('--subdir', help='Directory with the library layout inside the repository')
    add.add_argument('--ref', help='Tag, branch or commit')
    add.add_argument('--path', help='Checkout path (default: per-user data dir)')
    access = add.add_mutually_exclusive_group()
    access.add_argument('--writable', dest='writable', action='store_const', const=True, default=None)
    access.add_argument('--read-only', dest='writable', action='store_const', const=False)
    add.add_argument('--json', action='store_true')
    add.set_defaults(func=_cmd_add)
    use = actions.add_parser('use', help='Add a layer_rules entry: use a layer for projects matching a glob')
    use.add_argument('id')
    use.add_argument('--match', action='append', required=True, metavar='GLOB',
                     help='Project path glob (repeatable; `~` expanded, `*` in a segment, `**` any segments)')
    use.add_argument('--position', type=int, metavar='N',
                     help='0-based index in layer_rules (default: before the default rule `**`)')
    use.add_argument('--json', action='store_true')
    use.set_defaults(func=_cmd_use)
    update = actions.add_parser('update', help='Update layer checkouts (tag, branch ff-only, commit, pull)')
    update.add_argument('id', nargs='?')
    update.add_argument('--ref', help='New ref for the layer (also stored in the config)')
    update.add_argument('--json', action='store_true')
    update.set_defaults(func=_cmd_update)
    listing = actions.add_parser('list', help='Effective layers (for a project or the current directory)')
    listing.add_argument('--project', help='Project path')
    listing.add_argument('--isolation', action='store_true', help='Add the isolation check to the report')
    add_layer_argument(listing)
    listing.add_argument('--json', action='store_true')
    listing.set_defaults(func=_cmd_list)
    check = actions.add_parser('check', help='Check the effective layers (versions, requires, hooks, collisions, '
                                             'orphans) or, with --isolation, the machine isolation')
    check.add_argument('--project', help='Project path')
    check.add_argument('--isolation', action='store_true',
                       help='Only the isolation check: no checkouts, ids or old library clones outside '
                            'isolation.allowed_layers')
    add_layer_argument(check)
    check.add_argument('--json', action='store_true')
    check.set_defaults(func=_cmd_check)
    migrate = actions.add_parser('migrate', help='Convert the machine config to layers (dry-run unless --apply)')
    mode = migrate.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Write the changes (a backup copy is kept)')
    mode.add_argument('--dry-run', action='store_true', help='Only show the planned changes (default)')
    # Removed in 1.0 (the 0.9.x bridge): kept hidden only to answer with the migration instruction (BLOCKED).
    migrate.add_argument('--adopt-legacy', nargs='?', const=True, default=None, help=argparse.SUPPRESS)
    migrate.add_argument('--layer-source', dest='layer_sources', action='append', default=[], metavar='ID=PATH',
                         help='Register a local layer directory in place (repeatable)')
    migrate.add_argument('--kind', dest='kinds', action='append', default=[], metavar='ID=KIND',
                         help='Kind of a --layer-source without layer.yaml (core, org, personal)')
    migrate.add_argument('--match', dest='matches', action='append', default=[], metavar='ID=GLOB',
                         help='Project path glob for an org layer (repeatable)')
    migrate.add_argument('--manifests', action='store_true',
                         help='Remove the ignored library setting from project manifests (with backups)')
    migrate.add_argument('--project', dest='projects', action='append', default=[], metavar='PATH',
                         help='With --manifests: only these projects (default: discover_paths)')
    migrate.add_argument('--finish', action='store_true',
                         help='Remove legacy_library after the old clone is deleted and isolation passes')
    migrate.add_argument('--json', action='store_true')
    migrate.set_defaults(func=_cmd_migrate)
    return parser
