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
import stat as stat_module
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
import yaml

REAL_HOME = Path.home()
def _real_state_dirs():
    """The data and cache directories of the real user on EVERY system (the same layouts as `layers.data_home`/`cache_home`, computed from
    the real home at import, before any test patches the environment): a test run on Linux, macOS or Windows must leave all of them alone."""
    dirs = [REAL_HOME / '.cache' / 'skillsync', REAL_HOME / '.local' / 'share' / 'skillsync',
            REAL_HOME / 'Library' / 'Caches' / 'skillsync', REAL_HOME / 'Library' / 'Application Support' / 'skillsync',
            REAL_HOME / 'AppData' / 'Local' / 'skillsync']
    if os.name == 'nt' and os.environ.get('LOCALAPPDATA'):   # the real LOCALAPPDATA of this Windows user (the cache lives below it)
        dirs.append(Path(os.environ['LOCALAPPDATA']) / 'skillsync')
    unique = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return tuple(unique)


# Directories that must stay untouched by tests (machine state of the real user).
REAL_STATE_DIRS = _real_state_dirs()


def home_env(base):
    """Environment that moves the machine state of EVERY system under `base`: `XDG_DATA_HOME` (Linux), `HOME` (macOS: `~/Library/...`),
    `LOCALAPPDATA`/`USERPROFILE` (Windows). `XDG_DATA_HOME` alone leaves the real home to the tests on macOS and Windows (state files of the
    server pile up there, and two runs at once see each other's)."""
    base = Path(base)
    return {"XDG_DATA_HOME": str(base / "xdg-data"), "XDG_CACHE_HOME": str(base / "xdg-cache"), "LOCALAPPDATA": str(base / "xdg-data"),
            "HOME": str(base / "home"), "USERPROFILE": str(base / "home")}


def guard_real_state(case):
    """Proves at the end of `case` that nothing was written to the real state directories (`REAL_STATE_DIRS`): the control of
    `IsolatedTestCase`, for the test classes that build their own environment. The comparison covers the whole directory, so a REAL
    server of another session that starts or stops while the tests run (its state file under `dashboard/`) fails the guard too: run the
    guarded tests when no other `dashboard serve` of this user changes state (on the station: under the `heavy-run` lock)."""
    before = real_state_snapshot()
    case.addCleanup(lambda: assert_real_state_unchanged(case, before))

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


def machine_data_dir(env=None):
    """Per-user data directory of the host platform inside an isolated machine (`layers.data_home()`).

    Linux honours `XDG_DATA_HOME`; macOS uses `~/Library/Application Support/skillsync` under the isolated
    `HOME`; Windows uses `LOCALAPPDATA`. Tests must not assume the Linux layout."""
    return _platform_dir('data_home', env)


def machine_cache_dir(env=None):
    """Per-user cache directory of the host platform inside an isolated machine (`layers.cache_home()`)."""
    return _platform_dir('cache_home', env)


def _platform_dir(function, env):
    sys.path.insert(0, str(ROOT))
    from skillsync_workflow import layers
    with mock.patch.dict(os.environ, dict(env or {})):
        return getattr(layers, function)()


def _refreshed_by_read_only_git(path, relative=None):
    """`FETCH_HEAD` and `.git/index` are touched by `git fetch`/`git status` of other processes (another session, the watch
    service) on the real layer clones: no write of the test under guard, so the snapshot leaves them out (DEBT-142 D67).
    The same holds for the transient lock files of git inside `.git` (`objects/maintenance.lock` of the automatic maintenance
    that `git fetch` starts, `gc.pid`, `*.lock` of refs and index): a process state that exists for moments, never a result
    of a write. A write under guard still changes a ref, an object, `HEAD` or a layer file, and those stay compared; a
    `*.lock` outside `.git` (a layer file) stays compared too."""
    if path.name == 'FETCH_HEAD' or (path.name == 'index' and path.parent.name == '.git'):
        return True
    parts = (relative if relative is not None else path).parts
    return '.git' in parts[:-1] and (path.name.endswith('.lock') or path.name == 'gc.pid')


# The composite cache (`composed`, on Windows `cache/composed`) is rewritten by the `skillsync link` of ANY other session while a test
# runs: the mtime of the DIRECTORIES in it and the registry `.projects.json` change without a write of the test under guard. The
# snapshot therefore leaves out the metadata of those directories (a new or removed directory and every file stay visible) and compares
# the registry only for projects below the temporary directory (a test that links a REAL project there still fails the guard).
_COMPOSED_SCOPES = (('composed',), ('cache', 'composed'))
_COMPOSED_REGISTRY = '.projects.json'
_TMP_ROOT = Path(tempfile.gettempdir()).resolve()   # once, at import: the cleanup of a test runs under a substituted environment


def _composed_parts(parts):
    """Parts after the composite cache directory when `parts` (relative to a state dir) is that directory or lies in it, else None."""
    for scope in _COMPOSED_SCOPES:
        if parts[:len(scope)] == scope:
            return parts[len(scope):]
    return None


def _registry_entries(path):
    """The registry entries (`{project: {skill: hash}}`) of projects below the temporary directory; a missing or damaged file is empty."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    entries = {}
    for project, value in (data.items() if isinstance(data, dict) else ()):
        try:
            inside = Path(project).resolve().is_relative_to(_TMP_ROOT)
        except (OSError, ValueError):
            continue
        if inside:
            entries[project] = value
    return entries


def _link_target(path, stat):
    """The target of a symlink or a Windows junction (a reparse point), else None. The target is part of the entry because the size and the
    modification time of a replaced link can be identical (Windows keeps no size for a link, and its clock ticks in milliseconds)."""
    if stat_module.S_ISLNK(stat.st_mode) or getattr(stat, 'st_file_attributes', 0) & getattr(stat_module, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        try:
            target = os.readlink(path)
        except (OSError, ValueError):
            return None
        return target[4:] if target.startswith('\\\\?\\') and not target.startswith('\\\\?\\UNC\\') else target   # Windows: no `\\?\` in the report
    return None


def real_state_snapshot():
    """Description of the real per-user state dirs: `{str(directory): None (absent) | {path relative to it: value}}`, without the
    files that read-only git commands of other processes refresh (`_refreshed_by_read_only_git`). A value is `(kind, size, mtime_ns)`, `('dir', None, None)` for a directory of the composite cache (metadata left out) or `('entry', json)` for a registry entry of a project below the temporary directory (the registry file itself is not listed);
    `real_state_difference` turns two snapshots into readable lines."""
    snapshot = {}
    for directory in REAL_STATE_DIRS:
        if not directory.exists():
            snapshot[str(directory)] = None
            continue
        entries = {}
        for path in sorted(directory.rglob('*')):
            relative = path.relative_to(directory)
            if _refreshed_by_read_only_git(path, relative):
                continue
            inside = _composed_parts(relative.parts)
            if inside is not None and inside[:1] and inside[0].startswith(_COMPOSED_REGISTRY + '.tmp-') and len(inside) == 1:
                continue   # the temporary file of the atomic write of the registry
            if inside == (_COMPOSED_REGISTRY,) and path.is_file():
                for project, value in sorted(_registry_entries(path).items()):
                    entries[f'{relative.as_posix()}[{project}]'] = ('entry', json.dumps(value, sort_keys=True))
                continue
            try:
                stat = path.lstat()
            except OSError:
                continue
            link = _link_target(path, stat)
            is_dir = stat_module.S_ISDIR(stat.st_mode) and link is None
            if is_dir and ('.git' in relative.parts):
                # `git status`/`git fetch` create and rename lock and temporary files inside `.git` (`index.lock`, `objects`,
                # `refs/remotes`), which moves the mtime and size of those DIRECTORIES; a real write still shows up as a
                # changed or new file (a ref, an object, a layer file), so inside `.git` only files are compared
                entries[relative.as_posix()] = ('dir', 0, 0)
                continue
            if is_dir and inside is not None:
                entries[relative.as_posix()] = ('dir', None, None)   # metadata of the composite cache directories: see above
                continue
            kind = 'dir' if is_dir else 'file' if link is None else f'link -> {link}'
            entries[relative.as_posix()] = (kind, stat.st_size, stat.st_mtime_ns)
        snapshot[str(directory)] = entries
    return snapshot


STATE_DIFFERENCE_LIMIT = 10


def _describe_state_value(value):
    if value is None:
        return 'brak'
    kind, size, mtime = value if len(value) == 3 else (value[0], value[1], None)
    if kind == 'entry':
        return size
    if size is None:
        return kind
    return f'{kind} {size} B, mtime {mtime}'


def real_state_difference(before, after, limit=STATE_DIFFERENCE_LIMIT):
    """Lines `<path>: <before> -> <after>` for what differs between two `real_state_snapshot()` results (at most `limit`, then
    `i N innych`); an empty list means the snapshots are equal. A directory that appeared or disappeared is one line."""
    lines = []
    for directory in sorted(set(before) | set(after)):
        old, new = before.get(directory), after.get(directory)
        if old is None and new is None:
            continue
        if old is None or new is None:
            lines.append(f'{directory}: {"brak" if old is None else "katalog"} -> {"brak" if new is None else "katalog"}')
            continue
        for relative in sorted(set(old) | set(new)):
            if old.get(relative) != new.get(relative):
                lines.append(f'{directory}/{relative}: {_describe_state_value(old.get(relative))} -> '
                             f'{_describe_state_value(new.get(relative))}')
    if len(lines) > limit:
        lines = lines[:limit] + [f'i {len(lines) - limit} innych']
    return lines


def assert_real_state_unchanged(case, before):
    """Fails `case` with the changed paths (before -> after) when the real state dirs differ from the snapshot `before`."""
    lines = real_state_difference(before, real_state_snapshot())
    if lines:
        case.fail('test touched real Skillsync state dirs:\n' + '\n'.join(lines))


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
        assert_real_state_unchanged(self, self._real_state)

    def subprocess_env(self, **extra):
        env = dict(os.environ)
        env.update(extra)
        return env

    def make_repo(self, name='repo', email='dev@example.test', user='Dev Example'):
        """Create an empty git repository (branch `main`) with a local identity."""
        path = child(self.work, name)
        suffix = 1
        while path.exists():
            suffix += 1
            path = child(self.work, f'{name}-{suffix}')
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
IS_WINDOWS = os.name == 'nt'


def skip_on_windows(mechanism):
    """Skip for a mechanism that Windows does not have (never for a defect): the reason names it, and the
    `windows-no-mechanism` category of `tests/skip-baseline.json` allows exactly these reasons."""
    return unittest.skipIf(IS_WINDOWS, f'Windows has no {mechanism}')


def skip_open_file(detail):
    """Skip for what Windows refuses on an open file (unlink, rename or replace while a handle is open)."""
    return unittest.skipIf(IS_WINDOWS, f'Windows nie pozwala usunąć ani podmienić otwartego pliku: {detail}')


def readlink_text(path):
    """`os.readlink` without the `\\\\?\\` prefix that Windows puts before an absolute target."""
    text = os.readlink(path)
    return text[4:] if text.startswith('\\\\?\\') else text


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
        write(descend(root, rel), text)
    return root


def yaml_text(data):
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


class LayersCase(IsolatedTestCase):
    """Isolated machine plus helpers for synthetic layers, the machine config and the CLI."""

    @property
    def config_file(self):
        return Path(self.env['SKILLSYNC_CONFIG'])

    def layer_dir(self, layer_id, kind, files=None, **kwargs):
        return make_layer(child(self.work / 'layers', layer_id), layer_id, kind, files, **kwargs)

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
        path = descend(self.work, rel)
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
        bare = child(self.work, f'{name}.git')
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
        path = child(self.work / 'projects', name)
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
