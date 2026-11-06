"""Command line that re-runs this very skillsync (K5): `self_argv()` uses the same interpreter and code and no PATH lookup;
`link_task_argv()` (VS Code task) prefers the user's own entry instead."""
import os
import shutil
import sys
from pathlib import Path


def _wrapper():
    # Path.absolute(), not resolve(): this module's own path is not resolved here. When the wrapper
    # was started through a symlink, it resolves its directory before importing the package, so the
    # path then is the real checkout path (moving the checkout needs `skillsync link`, spec "Dane i migracja").
    # Fallback to resolve() only when the package itself is reached through a symlink and the
    # wrapper is not found beside the unresolved location (developer mode via linked package).
    here = Path(__file__).absolute()
    for package in (here, here.resolve()):
        candidate = package.parent.parent / 'skillsync'
        if candidate.is_file():
            return candidate
    return None


def self_argv():
    """['python', wrapper] in a checkout, ['python', '-P', '-m', 'skillsync_workflow'] when installed."""
    wrapper = _wrapper()
    if wrapper is not None:
        return [sys.executable, str(wrapper)]
    return [sys.executable, '-P', '-m', 'skillsync_workflow']


def _user_entry():
    """The command the user types, unresolved: `~/.local/bin/skillsync` (a symlink to the wrapper or the
    `uv tool` console script), else `skillsync` found on PATH. None when neither exists."""
    suffix = '.exe' if os.name == 'nt' else ''
    fixed = Path.home() / '.local' / 'bin' / f'skillsync{suffix}'
    candidates = [fixed]
    found = shutil.which('skillsync')
    # Only an absolute hit counts: a relative one (`.` or an empty PATH entry) would bind the task to the
    # current directory, i.e. to somebody else's file; on Windows only a real `.exe`.
    if found and os.path.isabs(found) and (os.name != 'nt' or found.lower().endswith('.exe')):
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return None


def link_task_argv():
    """argv of the persistent `skillsync: link` task of VS Code: independent of who runs `link`.

    Stable on purpose: the task lives in tracked `.vscode/tasks.json`, so it must not change with the
    interpreter or checkout that happened to run `link`. Order: the user's entry (`_user_entry`, no
    interpreter, survives moving the checkout), else on POSIX the wrapper itself (its shebang picks the
    interpreter), else `self_argv()` (installation form, Windows without an entry).
    """
    entry = _user_entry()
    if entry:
        return [entry]
    wrapper = _wrapper()
    if wrapper is not None and os.name != 'nt':
        return [str(wrapper)]
    return self_argv()
