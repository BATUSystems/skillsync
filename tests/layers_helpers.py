"""Shared helpers for layer, composite and leak guard tests (plan: TESTY, "Izolacja testów").

Every test of the layers feature should use `IsolatedTestCase` (or `isolated_environment`):
it redirects all places where Skillsync reads or writes machine state to a temporary
directory, so a test run never touches the real `~/.cache/skillsync`, `~/.local/share/skillsync`
or the machine config. `real_state_snapshot()` lets a control test prove it.

Usage:

    class MyTest(IsolatedTestCase):
        def test_x(self):
            repo = self.make_repo()
            commit_file(repo, 'a.md', 'text\\n', 'msg')

For both link modes (symlink and copy) use `LinkModesMixin.for_each_link_mode()`.
"""
import contextlib
import functools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REAL_HOME = Path.home()
# Directories that must stay untouched by tests (machine state of the real user).
REAL_STATE_DIRS = (REAL_HOME / '.cache' / 'skillsync', REAL_HOME / '.local' / 'share' / 'skillsync')

ISOLATED_VARS = ('HOME', 'USERPROFILE', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME',
                 'LOCALAPPDATA', 'APPDATA', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'SKILLSYNC_CONFIG')
# Git variables that would leak the caller's identity into temporary repositories.
GIT_IDENTITY_VARS = ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_AUTHOR_DATE', 'GIT_COMMITTER_NAME',
                     'GIT_COMMITTER_EMAIL', 'GIT_COMMITTER_DATE', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
                     'EMAIL')


def isolated_paths(root):
    """Mapping of isolated variable -> value, all inside `root`."""
    root = Path(root)
    home = root / 'home'
    values = {
        'HOME': home,
        'USERPROFILE': home,
        'XDG_CACHE_HOME': root / 'xdg-cache',
        'XDG_DATA_HOME': root / 'xdg-data',
        'XDG_CONFIG_HOME': root / 'xdg-config',
        'LOCALAPPDATA': root / 'localappdata',
        'APPDATA': root / 'appdata',
        'GIT_CONFIG_GLOBAL': root / 'gitconfig-global',
        'SKILLSYNC_CONFIG': root / 'skillsync-config.yaml',
    }
    for key, value in values.items():
        if key not in ('GIT_CONFIG_GLOBAL', 'SKILLSYNC_CONFIG'):
            value.mkdir(parents=True, exist_ok=True)
    values['GIT_CONFIG_GLOBAL'].write_text('', encoding='utf-8')
    result = {key: str(value) for key, value in values.items()}
    result['GIT_CONFIG_NOSYSTEM'] = '1'
    return result


@contextlib.contextmanager
def isolated_environment(root, extra=None):
    """Patch os.environ so machine state lives under `root`; yields the applied mapping."""
    values = isolated_paths(root)
    values.update(extra or {})
    with mock.patch.dict(os.environ, values):
        for name in GIT_IDENTITY_VARS:
            os.environ.pop(name, None)
        yield values


def real_state_snapshot():
    """Stable description of the real per-user state dirs (None = directory absent)."""
    snapshot = {}
    for directory in REAL_STATE_DIRS:
        if not directory.exists():
            snapshot[str(directory)] = None
            continue
        entries = []
        for path in sorted(directory.rglob('*')):
            try:
                stat = path.lstat()
            except OSError:
                continue
            entries.append((path.relative_to(directory).as_posix(), stat.st_size, stat.st_mtime_ns))
        snapshot[str(directory)] = tuple(entries)
    return snapshot


class IsolatedTestCase(unittest.TestCase):
    """Base class: isolated machine state plus a temporary working root (`self.root`)."""

    def setUp(self):
        super().setUp()
        self._real_state = real_state_snapshot()
        temp = tempfile.TemporaryDirectory(prefix='skillsync-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        stack = contextlib.ExitStack()
        self.env = stack.enter_context(isolated_environment(self.root / 'machine'))
        self.addCleanup(stack.close)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.addCleanup(self._assert_real_state_untouched)

    def _assert_real_state_untouched(self):
        # Runs as a cleanup, i.e. after the test body and tearDown.
        self.assertEqual(real_state_snapshot(), self._real_state, 'test touched real Skillsync state dirs')

    def subprocess_env(self, **extra):
        env = dict(os.environ)
        env.update(extra)
        return env

    def make_repo(self, name='repo', email='dev@example.test', user='Dev Example'):
        """Create an empty git repository (branch `main`) with a local identity."""
        path = self.work / name
        suffix = 1
        while path.exists():
            suffix += 1
            path = self.work / f'{name}-{suffix}'
        path.mkdir(parents=True)
        git(path, 'init', '-q', '-b', 'main')
        git(path, 'config', 'user.email', email)
        git(path, 'config', 'user.name', user)
        git(path, 'config', 'commit.gpgsign', 'false')
        return path


def git(cwd, *args, env=None, check=True, input=None):
    """Run git in `cwd` with the (isolated) process environment."""
    full = dict(os.environ)
    full.update(env or {})
    process = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True, env=full,
                             check=False, input=input)
    if check and process.returncode:
        raise AssertionError(f'git {" ".join(args)} failed ({process.returncode}): {process.stderr}')
    return process


def write(path, text, executable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')
    if executable:
        path.chmod(0o755)
    return path


def commit_file(repo, rel, text, message, *, author=None, committer=None):
    """Write, stage and commit one file. `author`/`committer`: (name, email) or None."""
    write(Path(repo) / rel, text)
    git(repo, 'add', '--', rel)
    return commit(repo, message, author=author, committer=committer)


def commit(repo, message, *, author=None, committer=None, allow_empty=False):
    env = {}
    if author:
        env.update(GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1])
    if committer:
        env.update(GIT_COMMITTER_NAME=committer[0], GIT_COMMITTER_EMAIL=committer[1])
    args = ['commit', '-q', '--no-verify', '-m', message]
    if allow_empty:
        args.append('--allow-empty')
    git(repo, *args, env=env)
    return git(repo, 'rev-parse', 'HEAD').stdout.strip()


# ---------------------------------------------------------------- host capabilities (skip decorators)
# Every system-dependent test carries one of these, so a skip always has an explicit reason (K13). They probe the
# host, never the link mode: `SKILLSYNC_NO_SYMLINK=1` must not skip a test that only plants a hostile symlink.

def _os_can_symlink():
    """True when this host lets the test process create a directory symlink (probe in a throw-away directory)."""
    if not hasattr(os, 'symlink'):
        return False
    with tempfile.TemporaryDirectory(prefix='skillsync-oscap-') as tmp:
        try:
            (Path(tmp) / 't').mkdir()
            os.symlink(Path(tmp) / 't', Path(tmp) / 'l', target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            return False


try:
    import fcntl as _fcntl
except ImportError:  # Windows: msvcrt locking is checked by tests/windows, not here
    _fcntl = None

OS_CAN_SYMLINK = _os_can_symlink()
NO_SYMLINKS_REASON = 'OS cannot create symlinks (Windows without Developer Mode)'
NO_MODE_ENFORCEMENT = os.name != 'posix' or (hasattr(os, 'geteuid') and os.geteuid() == 0)
needs_os_symlinks = unittest.skipUnless(OS_CAN_SYMLINK, NO_SYMLINKS_REASON)
needs_posix = unittest.skipUnless(os.name == 'posix', 'POSIX only (file modes, signals, /proc)')
needs_sh = unittest.skipUnless(__import__('shutil').which('sh'), 'no `sh` on PATH')
needs_flock = unittest.skipUnless(_fcntl is not None, 'fcntl.flock unavailable (Windows uses msvcrt)')
needs_fifo = unittest.skipUnless(hasattr(os, 'mkfifo'), 'mkfifo unavailable (FIFO needs POSIX)')
needs_mode_enforcement = unittest.skipIf(NO_MODE_ENFORCEMENT, 'chmod does not block reading (root or non-POSIX)')


def require_os_symlinks(test):
    """For helpers that plant symlinks for many tests: skip the calling test with the shared reason."""
    if not OS_CAN_SYMLINK:
        test.skipTest(NO_SYMLINKS_REASON)


def require_fifo(test):
    if not hasattr(os, 'mkfifo'):
        test.skipTest('mkfifo unavailable (FIFO needs POSIX)')


# ---------------------------------------------------------------- link modes (symlink / copy)

LINK_MODES = ('symlink', 'copy')


@contextlib.contextmanager
def link_mode(mode):
    """Run a block with `SKILLSYNC_NO_SYMLINK` set (copy mode) or cleared (symlink mode)."""
    if mode not in LINK_MODES:
        raise ValueError(mode)
    patch = {'SKILLSYNC_NO_SYMLINK': '1'} if mode == 'copy' else {}
    with mock.patch.dict(os.environ, patch):
        if mode == 'symlink':
            os.environ.pop('SKILLSYNC_NO_SYMLINK', None)
        yield mode


class LinkModesMixin:
    """Mixin for `unittest.TestCase`: `for_each_link_mode()` yields subTests for both modes."""

    def for_each_link_mode(self):
        for mode in LINK_MODES:
            with self.subTest(link_mode=mode), link_mode(mode):
                yield mode


# ---------------------------------------------------------------- synthetic layers and machine config (step 2.1t)

ROOT = Path(__file__).resolve().parents[1]
SKILLSYNC = ROOT / 'skillsync'  # the wrapper; the CLI body is skillsync_workflow/cli.py
LAYER_KINDS = ('core', 'org', 'personal')


def generator_version():
    """Version the tests compare layers with (`__version__` of the generator under test)."""
    sys.path.insert(0, str(ROOT))
    from skillsync_workflow import __version__
    return __version__


def make_layer(root, layer_id, kind, files=None, *, version='1.0.0', core_min=None, requires=None,
               readme=True, changelog=True, meta=True, extra_meta=None):
    """Synthetic layer directory: `layer.yaml` (unless `meta=False`), README, CHANGELOG and `files` {rel: text}."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if meta:
        data = {'schema': 1, 'id': layer_id, 'kind': kind}
        if version is not None:
            data['version'] = version
        data['core_min'] = core_min if core_min is not None else generator_version()
        if core_min is False:
            del data['core_min']
        if requires:
            data['requires'] = list(requires)
        data.update(extra_meta or {})
        write(root / 'layer.yaml', yaml.safe_dump(data, sort_keys=False))
    if readme:
        write(root / 'README.md', f'# {layer_id}\n')
    if changelog:
        write(root / 'CHANGELOG.md', '# Changelog\n')
    for rel, text in (files or {}).items():
        write(root / rel, text)
    return root


def yaml_text(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


class LayersCase(IsolatedTestCase):
    """Isolated machine plus helpers for synthetic layers, the machine config and the CLI."""

    @property
    def config_file(self):
        return Path(self.env['SKILLSYNC_CONFIG'])

    def layer_dir(self, layer_id, kind, files=None, **kwargs):
        return make_layer(self.work / 'layers' / layer_id, layer_id, kind, files, **kwargs)

    def write_config(self, layers=(), rules=(), **extra):
        """Machine config. `layers`: [(id, kind, path[, extra entry keys])]; `rules`: [(match list, use list)]."""
        entries = []
        for item in layers:
            layer_id, kind, path = item[:3]
            entry = {'id': layer_id, 'kind': kind, 'path': str(path), 'source': str(path)}
            entry.update(item[3] if len(item) > 3 else {})
            entries.append(entry)
        data = {}
        if entries:
            data['layers'] = entries
        if rules:
            data['layer_rules'] = [{'match': list(match), 'use': list(use)} for match, use in rules]
        data.update(extra)
        self.config_file.write_text(yaml_text(data), encoding='utf-8')
        return self.config_file

    def project(self, rel, name=None):
        """Project directory with a manifest under `self.work/<rel>`."""
        path = self.work / rel
        manifest = {'project': {'name': name}} if name else {}
        write(path / '.github' / '.skillsync.yaml', yaml_text(manifest) if manifest else '')
        return path

    def cli(self, *args, cwd=None, env=None):
        full = dict(os.environ)
        full.update(env or {})
        return subprocess.run([sys.executable, str(SKILLSYNC), *args], capture_output=True, text=True,
                              cwd=str(cwd or self.work), env=full, timeout=300)

    def cli_json(self, *args, cwd=None, env=None):
        """(exit code, parsed JSON) of a command run with `--json`."""
        process = self.cli(*args, '--json', cwd=cwd, env=env)
        try:
            return process.returncode, json.loads(process.stdout)
        except ValueError:
            raise AssertionError(f'not JSON (exit {process.returncode}): {process.stdout!r} {process.stderr!r}')

    def set_git_identity(self, email):
        """User email in the isolated global git config (used by `git config user.email` of new checkouts)."""
        Path(self.env['GIT_CONFIG_GLOBAL']).write_text(f'[user]\n\temail = {email}\n\tname = Layer Tester\n',
                                                       encoding='utf-8')

    def source_repo(self, name, layer_id, kind, files=None, **kwargs):
        """Bare repo with one commit holding a synthetic layer; returns (bare path, work clone path)."""
        work = self.make_repo(f'{name}-src')
        make_layer(work, layer_id, kind, files, **kwargs)
        git(work, 'add', '-A')
        commit(work, 'initial layer')
        bare = self.work / f'{name}.git'
        git(self.work, 'clone', '-q', '--bare', str(work), str(bare))
        git(work, 'remote', 'add', 'origin', str(bare))
        git(work, 'fetch', '-q', 'origin')
        git(work, 'branch', '-u', 'origin/main')
        return bare, work

    def push_commit(self, work, rel, text, message='update'):
        """Commit a file in the source work clone and push it to its bare origin."""
        commit_file(work, rel, text, message)
        git(work, 'push', '-q', 'origin', 'HEAD:main')
        return git(work, 'rev-parse', 'HEAD').stdout.strip()

    def git_layer(self, layer_id, kind, files=None, identity_terms=None, **kwargs):
        """Layer directory that is also a git repository with one commit (a possible scan root)."""
        files = dict(files or {})
        if identity_terms is not None:
            files['leakguard/identity.yaml'] = yaml_text({'schema': 1, 'layer': layer_id, 'terms': identity_terms})
        path = self.layer_dir(layer_id, kind, files, **kwargs)
        git(path, 'init', '-q', '-b', 'main')
        git(path, 'config', 'user.email', 'dev@example.test')
        git(path, 'config', 'user.name', 'Dev')
        git(path, 'add', '-A')
        commit(path, 'init')
        return path


# ---------------------------------------------------------------- transitional config (steps 2.2a -> 2.2b)

def ensure_core_layer(library, *, layer_id='core'):
    """Turn a legacy-shaped test library directory into a valid `core` layer (adds `layer.yaml` only)."""
    library = Path(library)
    if not (library / 'layer.yaml').exists():
        make_layer(library, layer_id, 'core', readme=False, changelog=False)
    return library


def transitional_config(library, *, layer_id='core', legacy_key=True, writable=None, **extra):
    """Machine config dict: one core layer at `library` and a default rule `**`.

    `legacy_key=True` also writes the legacy `library:` key with the same path. Since 2.2b the `skillsync`
    script never reads it (K1); it is ignored by every module and only checks that a stale key is harmless. Tests of the `skillsync` script pass
    `legacy_key=False`. `writable` sets the layer's `writable` flag (core is read-only by default, K4)."""
    ensure_core_layer(library, layer_id=layer_id)
    entry = {'id': layer_id, 'kind': 'core', 'path': str(library), 'source': str(library)}
    if writable is not None:
        entry['writable'] = writable
    data = {'library': str(library)} if legacy_key else {}
    data.update({'layers': [entry], 'layer_rules': [{'match': ['**'], 'use': [layer_id]}]})
    data.update(extra)
    return data


def write_transitional_config(path, library, **extra):
    """`transitional_config` serialized to `path` (YAML)."""
    Path(path).write_text(yaml_text(transitional_config(library, **extra)), encoding='utf-8')
    return Path(path)


# ---------------------------------------------------------------- composition tests (step 2.2at)

def each_mode(method):
    """Run `method(self, mode)` once per link mode, each as a `subTest` (a failure in one mode does not hide the other)."""
    @functools.wraps(method)
    def wrapper(self):
        for mode in LINK_MODES:
            with self.subTest(link_mode=mode):
                method(self, mode)
    return wrapper


SKILL_TEXT = '---\nname: {0}\ndescription: {0} skill\n---\n{0} body\n'


def composed_root():
    """Cache directory of composites for the (isolated) environment of the running test."""
    sys.path.insert(0, str(ROOT))
    from skillsync_workflow import layers
    return layers.composed_root()


def tree_snapshot(root, *, mtimes=True):
    """{relative path: (kind, detail)} of a tree without following links.

    kind `link` -> readlink text, `file` -> (content, mtime_ns), `dir` -> None. Used for "nothing changed" proofs."""
    root = Path(root)
    result = {}
    if not (root.exists() or root.is_symlink()):
        return result
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[rel] = ('link', os.readlink(path))
        elif path.is_dir():
            result[rel] = ('dir', None)
        else:
            stat = path.stat()
            result[rel] = ('file', (path.read_bytes(), stat.st_mtime_ns if mtimes else None))
    return result


class ComposeCase(LayersCase, LinkModesMixin):
    """Synthetic core/org/personal layers, a machine config using all three and `link` runs per mode.

    The link mode is selected per call (`mode='symlink'|'copy'`); every mode gets its own project, because a
    project that was linked with symlinks keeps them when `SKILLSYNC_NO_SYMLINK=1` is set later."""

    def setUp(self):
        super().setUp()
        self.layer_paths = {}

    def add_layers(self, core=None, org=None, personal=None, rules=True, **layer_kwargs):
        """Create the layers (`None` = layer absent) and write the config; `rules=True` -> one rule `**` using
        all created layers in core, org, personal order. Returns the dict id -> path."""
        specs = (('core', core), ('org', org), ('personal', personal))
        entries = []
        for layer_id, files in specs:
            if files is None:
                continue
            kwargs = dict(layer_kwargs.get(layer_id, {}))
            self.layer_paths[layer_id] = self.layer_dir(layer_id, layer_id, files, **kwargs)
            entries.append((layer_id, layer_id, self.layer_paths[layer_id]))
        use = [item[0] for item in entries]
        self.write_config(layers=entries, rules=[(['**'], use)] if rules else [])
        return self.layer_paths

    def new_project(self, name, skills=('feature',), **manifest_extra):
        """Project with a manifest (no Claude, no MCP: only the generic artefacts)."""
        path = self.work / 'projects' / name
        manifest = {'version': 1, 'skills': list(skills), 'instructions': {'merge': ['_base'], 'template': 'proj'},
                    'claude': False, 'mcp': False}
        manifest.update(manifest_extra)
        write(path / '.github' / '.skillsync.yaml', yaml_text(manifest))
        return path

    def link(self, project, *extra, mode='symlink', env=None):
        """`skillsync link <project>` in the given link mode; returns the CompletedProcess."""
        with link_mode(mode):
            return self.cli('link', str(project), *extra, env=env)

    def link_ok(self, project, *extra, mode='symlink'):
        process = self.link(project, *extra, mode=mode)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn('Traceback', process.stderr)
        return process

    def skill_dir(self, project, name):
        return Path(project) / '.github' / 'skills' / name

    def composites(self, skill):
        """Names of the composite `<hash>` directories of a skill (without `.orphans`)."""
        base = composed_root() / skill
        if not base.is_dir():
            return []
        return sorted(item.name for item in base.iterdir() if item.is_dir() and not item.name.endswith('.orphans'))
