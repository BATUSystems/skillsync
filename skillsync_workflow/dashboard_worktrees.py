"""Worktrees of one git project for the status page: identity, registration, trace of work, exclusion, tab ids (CORE-1180-MULTIWT).

A pure module: it only reads files (git is never run, nothing is written) and it never raises for a bad entry: every `OSError` or
`ValueError` means "absent". The list comes from `<common dir>/worktrees/*`; a worktree becomes a tab only when all of this holds:
- R2 (back link): `worktrees/<id>/gitdir` names `<wt>/.git`, that file says `gitdir: <worktrees/<id>>`, the entry's `commondir` is the
  common directory, the directory exists, and no path leaves its directory through a symbolic link. Relative paths (git >= 2.48) are
  resolved against the directory of the file they stand in. Other entries are skipped with a warning.
- R1 (trace of work, noise only, not security): `.skillsync/runs/` has an entry, or a plan/spec file under `docs/` has content (sha256)
  that the main checkout lacks at the same relative path. Modification time is only a cache key, never the criterion.
- R3 (exclusion): the file `.skillsync/no-dashboard` or the manifest list `dashboard.worktrees.exclude`. An excluded worktree has no tab.
The main checkout is always the first tab. Tab id: `main` or 12 hex of sha256 of the normalised path.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass, field

from . import fsutil, pathfmt

ID_PATTERN = re.compile(r'^(main|[0-9a-f]{12})$')
MAX_VISIBLE = 16
DEFAULT_INACTIVE_DAYS = 7
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES_PER_DIR = 200
MAX_ACTIVITY_FILES = 2000
ACTIVITY_DEPTH = 3
STORE_DIR = ('.skillsync', 'feedback')
DOC_FOLDERS = ('plans', 'specs')
_HEX = re.compile(r'^[0-9a-f]{40}([0-9a-f]{24})?$')
_GITDIR = re.compile(r'gitdir:\s*(.+)')
_CACHE_LIMIT = 4096
_cache = {}
_cache_lock = threading.Lock()


# --- paths

def _folds_case():
    return os.name == 'nt' or sys.platform == 'darwin'


def normalize_path(path):
    """`realpath` in the posix form (keeps the letter case)."""
    return pathfmt.posix(os.path.realpath(os.fspath(path)))


def normalize_key(path):
    """`normalize_path` lowered on Windows and macOS (case-insensitive file systems): the key to compare and hash."""
    text = normalize_path(path)
    return text.lower() if _folds_case() else text


def tab_id(path, main_path=None):
    """`main` for the main checkout, else the first 12 hex of sha256 of the normalised key of `path`."""
    key = normalize_key(path)
    if main_path is not None and key == normalize_key(main_path):
        return 'main'
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]


def _read_text(path, limit=4096):
    """Text of a small file, or None for any read problem."""
    try:
        with open(path, 'rb') as handle:
            return handle.read(limit).decode('utf-8', errors='replace')
    except (OSError, ValueError):
        return None


def _resolve(base_dir, value):
    """`value` as a path: absolute as is, relative against `base_dir`."""
    return os.path.join(base_dir, value) if not fsutil.isabs(value) else value


def _same(left, right):
    return os.path.realpath(left) == os.path.realpath(right)


def _gitdir_target(file_path):
    """Directory named by `gitdir: <path>` in `file_path` (relative: against the file's directory), or None."""
    text = _read_text(file_path)
    match = _GITDIR.match(text.strip()) if text else None
    if not match:
        return None
    return os.path.normpath(_resolve(os.path.dirname(file_path), match.group(1).strip()))


def _is_bare(common):
    text = _read_text(os.path.join(common, 'config'), 65536) or ''
    return bool(re.search(r'^\s*bare\s*=\s*true\s*$', text, re.IGNORECASE | re.MULTILINE))


# --- identity

@dataclass(frozen=True)
class Project:
    root: str
    common_dir: str
    main_path: str | None


def project_identity(root):
    """`Project` of a checkout from `.git` files, or None (no `.git`, damaged entry, bare, submodule, any read error)."""
    try:
        root_real = os.path.realpath(os.fspath(root))
        git = os.path.join(root_real, '.git')
        if os.path.isdir(git) and not os.path.islink(git):
            if not os.path.isfile(os.path.join(git, 'HEAD')):
                return None
            common = git
        elif os.path.isfile(git):
            git_dir = _gitdir_target(git)
            if git_dir is None or not os.path.isdir(git_dir):
                return None
            commondir_file = os.path.join(git_dir, 'commondir')
            text = _read_text(commondir_file)
            if not text or not text.strip():
                return None
            common = os.path.normpath(_resolve(git_dir, text.strip()))
        else:
            return None
        common = os.path.realpath(common)
        if not os.path.isdir(common) or os.path.basename(common) != '.git' or _is_bare(common):
            return None
        return Project(normalize_path(root_real), normalize_path(common), normalize_path(os.path.dirname(common)))
    except (OSError, ValueError):
        return None


# --- entries

@dataclass(frozen=True)
class Entry:
    id: str
    path: str
    name: str
    kind: str
    locked: bool
    detached: bool
    branch: str | None
    head: str | None
    last_activity: float | None


def _packed_ref(common, ref):
    text = _read_text(os.path.join(common, 'packed-refs'), 4 * 1024 * 1024) or ''
    for line in text.splitlines():
        parts = line.split(' ', 1)
        if len(parts) == 2 and parts[1].strip() == ref and _HEX.match(parts[0]):
            return parts[0]
    return None


def _read_head(git_dir, common):
    """`(branch, head12, detached)` from the HEAD file of `git_dir` (refs under `common`)."""
    text = (_read_text(os.path.join(git_dir, 'HEAD')) or '').strip()
    if text.startswith('ref:'):
        ref = text[4:].strip()
        if ref.startswith('refs/heads/') and '..' not in ref.split('/') and not fsutil.isabs(ref):
            ref_text = (_read_text(os.path.join(common, *ref.split('/'))) or '').strip()
            sha = ref_text if _HEX.match(ref_text) else _packed_ref(common, ref)
            return ref[len('refs/heads/'):], (sha[:12] if sha else None), False
        return None, None, True
    return None, (text[:12] if _HEX.match(text) else None), True


def _accept_worktree(common, entry_dir, name, warn):
    """Checkout directory (real path) of a `worktrees/<name>` entry that passes R2, else None (a warning is given)."""
    def reject(reason):
        warn(f'worktree entry {name}: skipped ({reason})')

    if os.path.realpath(entry_dir) != os.path.join(os.path.realpath(common), 'worktrees', name):
        return reject('entry directory is not inside the common directory')
    gitdir_text = _read_text(os.path.join(entry_dir, 'gitdir'))
    if not gitdir_text or not gitdir_text.strip():
        return reject('no gitdir file')
    dot_git = os.path.normpath(_resolve(entry_dir, gitdir_text.strip()))
    if os.path.basename(dot_git) != '.git':
        return reject('gitdir does not name a .git file')
    checkout = os.path.dirname(dot_git)
    if not os.path.isdir(checkout):
        return reject('directory is missing')
    if os.path.islink(checkout):
        return reject('the worktree directory is a link')
    if not os.path.isfile(dot_git):
        return reject('.git is not a file')
    checkout_real = os.path.realpath(checkout)
    if os.path.realpath(dot_git) != os.path.join(checkout_real, '.git'):
        return reject('.git leaves the directory through a link')
    back = _gitdir_target(dot_git)
    if back is None or not _same(back, entry_dir):
        return reject('no back link to the entry')
    commondir_text = _read_text(os.path.join(entry_dir, 'commondir'))
    if not commondir_text or not commondir_text.strip() or not _same(_resolve(entry_dir, commondir_text.strip()), common):
        return reject('commondir differs')
    return checkout_real


def list_entries(project, *, warn=None):
    """Main checkout (when present and not bare) and the worktrees that pass R2, as `Entry` (collisions of the 12-char id: the later one is skipped)."""
    emit = warn or (lambda _message: None)
    entries, seen_ids, seen_paths = [], set(), set()

    def add(path, kind, git_dir, locked):
        key = normalize_key(path)
        tid = tab_id(path, project.main_path)
        if key in seen_paths:
            return emit('worktree entry: skipped (same directory twice)')
        if tid in seen_ids:
            return emit(f'worktree {os.path.basename(path)}: skipped (tab id collision)')
        branch, head, detached = _read_head(git_dir, common)
        seen_ids.add(tid)
        seen_paths.add(key)
        entries.append(Entry(tid, normalize_path(path), os.path.basename(os.path.realpath(path)), kind, locked, detached, branch, head, last_activity(path)))

    try:
        common = os.path.realpath(project.common_dir)
        if project.main_path and os.path.isdir(project.main_path) and os.path.isdir(common) and not _is_bare(common):
            add(project.main_path, 'main', common, False)
        base = os.path.join(common, 'worktrees')
        names = sorted(os.listdir(base)) if os.path.isdir(base) else []
    except (OSError, ValueError):
        return entries
    for name in names:
        try:
            entry_dir = os.path.join(base, name)
            if not os.path.isdir(entry_dir):
                continue
            checkout = _accept_worktree(common, entry_dir, name, emit)
            if checkout is None:
                continue
            if project.main_path and normalize_key(checkout) == normalize_key(project.main_path):
                emit(f'worktree entry {name}: skipped (names the main checkout)')
                continue
            add(checkout, 'worktree', entry_dir, os.path.lexists(os.path.join(entry_dir, 'locked')))
        except (OSError, ValueError):
            emit(f'worktree entry {name}: skipped (read error)')
    return entries


# --- trace of work (R1) and activity

def _lstat_regular(path):
    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return None
    return info if stat.S_ISREG(info.st_mode) else None


def _real_dir(path):
    """True for a real directory (not a symlink)."""
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


def _sha(path, info):
    """sha256 of a regular file, cached by (path, size, mtime); None when unreadable or over the limit."""
    if info.st_size > MAX_FILE_BYTES:
        return None
    key = (path, info.st_size, info.st_mtime_ns)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(fd, 'rb') as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if len(data) > MAX_FILE_BYTES:
        return None
    digest = hashlib.sha256(data).hexdigest()
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[key] = digest
    return digest


def _doc_names(folder_path):
    """Names of `*.md` regular files (no symlinks, no README) of a docs folder, sorted, at most MAX_FILES_PER_DIR."""
    try:
        with os.scandir(folder_path) as scan:
            names = sorted(item.name for item in scan if item.name.lower().endswith('.md') and item.name.lower() != 'readme.md'
                           and item.is_file(follow_symlinks=False))
    except (OSError, ValueError):
        return []
    return names[:MAX_FILES_PER_DIR]


def _docs_dir(root, folder):
    """`<root>/docs/<folder>` when neither `docs` nor the folder is a symlink, else None."""
    docs = os.path.join(root, 'docs')
    folder_path = os.path.join(docs, folder)
    return folder_path if _real_dir(docs) and _real_dir(folder_path) else None


def has_trace(path, main_path):
    """R1: a `.skillsync/runs/` entry, or a plan/spec file new or changed against `main_path` (content, not time)."""
    path = os.fspath(path)
    skillsync, runs = os.path.join(path, '.skillsync'), os.path.join(path, '.skillsync', 'runs')
    if _real_dir(skillsync) and _real_dir(runs):
        try:
            with os.scandir(runs) as scan:
                if next(scan, None) is not None:
                    return True
        except (OSError, ValueError):
            pass
    if not main_path or not os.path.isdir(main_path):
        return False
    for folder in DOC_FOLDERS:
        here = _docs_dir(path, folder)
        if here is None:
            continue
        there = _docs_dir(main_path, folder)
        for name in _doc_names(here):
            mine = os.path.join(here, name)
            info = _lstat_regular(mine)
            if info is None or info.st_size > MAX_FILE_BYTES:
                continue
            theirs = os.path.join(there, name) if there else None
            other = _lstat_regular(theirs) if theirs else None
            if other is None or other.st_size != info.st_size:
                return True
            digest_mine, digest_theirs = _sha(mine, info), _sha(theirs, other)
            if digest_mine is not None and digest_mine != digest_theirs:
                return True
    return False


def is_excluded(path, *, manifest_exclude=(), main_path=None):
    """R3: the file `.skillsync/no-dashboard` (a regular file) or a path of `manifest_exclude` (absolute, or relative to `main_path`)."""
    if _lstat_regular(os.path.join(os.fspath(path), '.skillsync', 'no-dashboard')) is not None:
        return True
    key = normalize_key(path)
    for item in manifest_exclude or ():
        try:
            text = str(item).strip()
            if not text:
                continue
            if not fsutil.isabs(text):
                if not main_path:
                    continue
                text = os.path.join(main_path, text)
            if normalize_key(text) == key:
                return True
        except (OSError, ValueError):
            continue
    return False


def last_activity(path):
    """Newest mtime of a file in `.skillsync/runs/` (depth 3) or of `docs/plans|specs/*.md` (no symlinks), else None."""
    path = os.fspath(path)
    newest, budget = [None], [MAX_ACTIVITY_FILES]

    def note(info):
        if newest[0] is None or info.st_mtime > newest[0]:
            newest[0] = info.st_mtime

    def walk(directory, depth):
        try:
            with os.scandir(directory) as scan:
                for item in scan:
                    if budget[0] <= 0:
                        return
                    if item.is_symlink():
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if depth < ACTIVITY_DEPTH:
                            walk(item.path, depth + 1)
                    elif item.is_file(follow_symlinks=False):
                        budget[0] -= 1
                        note(item.stat(follow_symlinks=False))
        except (OSError, ValueError):
            return

    skillsync = os.path.join(path, '.skillsync')
    if _real_dir(skillsync) and _real_dir(os.path.join(skillsync, 'runs')):
        walk(os.path.join(skillsync, 'runs'), 1)
    for folder in DOC_FOLDERS:
        here = _docs_dir(path, folder)
        for name in _doc_names(here) if here else ():
            info = _lstat_regular(os.path.join(here, name))
            if info is not None:
                note(info)
    return newest[0]


# --- registry

@dataclass
class Registry:
    tabs: list = field(default_factory=list)
    hidden: list = field(default_factory=list)
    excluded: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _order(entry):
    return (entry.last_activity is None, -(entry.last_activity or 0.0), entry.name)


def discover(project, *, pinned=(), manifest_exclude=(), inactive_days=DEFAULT_INACTIVE_DAYS, now=None, warn=None):
    """`Registry`: visible tabs (main first, then by activity, at most MAX_VISIBLE), hidden entries, excluded paths and warnings."""
    registry = Registry()

    def note(message):
        registry.warnings.append(message)
        if warn:
            warn(message)

    now = time.time() if now is None else now
    pins = {normalize_key(item) for item in pinned or () if item}
    entries = list_entries(project, warn=note)
    found = set()
    main = None
    pinned_list, candidates = [], []
    for entry in entries:
        key = normalize_key(entry.path)
        if entry.kind == 'main':
            main = entry
            found.add(key)
            continue
        found.add(key)
        if is_excluded(entry.path, manifest_exclude=manifest_exclude, main_path=project.main_path):
            registry.excluded.append(entry.path)
            if key in pins:
                note(f'pinned worktree {entry.name}: excluded (R3), no tab')
            continue
        if key in pins:
            pinned_list.append(entry)
        elif not has_trace(entry.path, project.main_path):
            registry.hidden.append(entry)
        elif entry.last_activity is not None and now - entry.last_activity > inactive_days * 86400:
            registry.hidden.append(entry)
        else:
            candidates.append(entry)
    for pin in sorted(pins - found):
        note('pinned path is not a registered worktree of this project')
    room = MAX_VISIBLE - (1 if main else 0)
    pinned_list.sort(key=_order)
    if len(pinned_list) > room:
        note(f'more than {MAX_VISIBLE} pinned tabs: the least active are hidden')
        registry.hidden.extend(pinned_list[room:])
        pinned_list = pinned_list[:room]
    candidates.sort(key=_order)
    free = room - len(pinned_list)
    chosen = pinned_list + candidates[:free]
    registry.hidden.extend(candidates[free:])
    chosen.sort(key=_order)
    registry.tabs = ([main] if main else []) + chosen
    registry.hidden.sort(key=_order)
    return registry


# --- feedback store (no git)

def feedback_store_root(common_dir_or_root):
    """`<main checkout>/.skillsync/feedback` from `.git` files only, like `feedback.store_root` (a linked worktree maps to its main
    checkout); None for a submodule, a bare repository or anything unreadable (the caller keeps the old path)."""
    try:
        path = os.path.realpath(os.fspath(common_dir_or_root))
        if os.path.basename(path) == '.git' and os.path.isdir(path) and not os.path.exists(os.path.join(path, '.git')):
            project = project_identity(os.path.dirname(path))
        else:
            project = project_identity(path)
        if project is None or not project.main_path:
            return None
        return os.path.join(project.main_path, *STORE_DIR)
    except (OSError, ValueError):
        return None


def revalidate(project, entry):
    """True when `entry` (as registered) still passes R2 and still resolves to the same directory.

    A stateless `list_entries` cannot see that an ancestor of a registered path became a link after the registration;
    the server calls this right before every write, and the real path recorded in `Entry` must be unchanged.
    """
    try:
        return any(e.id == entry.id and normalize_key(e.path) == normalize_key(entry.path) for e in list_entries(project))
    except (OSError, ValueError):
        return False


def revalidate_entry(project, entry):
    """Like `revalidate`, but looks at the one entry only (no scan of the other worktrees, no activity walk): True when `entry` is still the main checkout
    or a worktree of `project` that passes R2 and resolves to the same directory with the same id."""
    try:
        key = normalize_key(entry.path)
        common = os.path.realpath(project.common_dir)
        if entry.kind == 'main':
            return bool(project.main_path and os.path.isdir(project.main_path) and os.path.isdir(common) and not _is_bare(common)
                        and normalize_key(project.main_path) == key and tab_id(project.main_path, project.main_path) == entry.id)
        base = os.path.join(common, 'worktrees')
        for name in os.listdir(base) if os.path.isdir(base) else ():
            entry_dir = os.path.join(base, name)
            if not os.path.isdir(entry_dir):
                continue
            checkout = _accept_worktree(common, entry_dir, name, lambda _message: None)
            if checkout is not None and normalize_key(checkout) == key and tab_id(checkout, project.main_path) == entry.id:
                return True
        return False
    except (OSError, ValueError):
        return False
