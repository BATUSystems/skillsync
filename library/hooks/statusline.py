#!/usr/bin/env python3
# skillsync-managed: hooks/statusline.py (the signature by which `skillsync link` recognises its own copy; do not remove)
"""Claude Code status line: one short line about the session, the project and the plan in progress.

Standalone (standard library only), read-only: it starts no process (no git), opens no socket, reads at most the
project manifest, `.git` / `HEAD` and up to 200 plans of `docs/plans` (512 KiB each, symlinks skipped).
Input: the JSON of Claude Code on stdin (UTF-8). Output: exactly one UTF-8 line. The exit code is always 0.
`NO_COLOR` switches colour off; `COLUMNS` (default and maximum 120) limits the width in characters.
The plan parser is a copy of the tolerant one of `skillsync status` (parity is a requirement).
"""
import json
import os
import re
import stat
import sys
import unicodedata

MAX_WIDTH = 120
MAX_PLANS = 200
MAX_PLAN_BYTES = 512 * 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_HEAD_BYTES = 4096
MAX_LEVELS = 64
SEPARATOR = ' | '
MODES = ('pr', 'direct')

_STATUS = re.compile(r'^(?:[-*>][ \t]+)?(?:\*\*|__)?status(?:\*\*|__)?[ \t]*:[ \t]*(?:(?:\*\*|__)[ \t]*)?'
                     r'(?P<raw>[`*_]*(?P<word>in[ \t_-]+progress\b|[A-Za-z_]+)[^\n]*)', re.MULTILINE | re.IGNORECASE)
_STEP = re.compile(r'^\s*-\s\[([ xX])\]\s+\S', re.MULTILINE)
_OSC = re.compile('(?:\x1b[\\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x9c]*(?:\x07|\x1b\\\\|\x9c)?')
_CSI = re.compile('(?:\x1b\\[|\x9b)[0-?]*[ -/]*[@-~]?')
_BLANKS = '\t\n\r\x0b\x0c\x85  '
_HEX_HEAD = re.compile(r'^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$')
_DATE_PREFIX = re.compile(r'^\d{4}-\d{2}-\d{2}-')
COLOURS = {'session': '1;36', 'project': '1', 'branch': '32', 'mode': '33', 'plan': '35'}


def clean(value):
    """Text without terminal sequences, control, format and separator characters; whitespace collapsed."""
    if not isinstance(value, str):
        return ''
    value = _OSC.sub('', value)
    value = _CSI.sub('', value)
    kept = []
    for char in value:
        if char in _BLANKS:
            kept.append(' ')
        elif unicodedata.category(char) not in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp', 'Co'):
            kept.append(char)
    return ' '.join(''.join(kept).split())


def cap(value, limit):
    return value if len(value) <= limit else value[:limit - 1] + '…'


HEAD_BYTES = 16384   # at least 4000 characters in any of the accepted encodings (UTF-8: 4 bytes a character at most)


def read_bytes(path, limit):
    """Up to `limit` bytes of a regular file or None (any error)."""
    try:
        with open(path, 'rb') as handle:
            return handle.read(limit)
    except (OSError, ValueError):
        return None


def read_in_progress(path):
    """The whole file (up to MAX_PLAN_BYTES) of a plan whose status in the head is `in_progress`; None for any other plan, a too big one or an error."""
    try:
        with open(path, 'rb') as handle:
            head = handle.read(HEAD_BYTES)
            if plan_status(decode_text(head)) != 'in_progress':
                return None
            data = head + handle.read(MAX_PLAN_BYTES + 1 - len(head))
    except (OSError, ValueError):
        return None
    return data if len(data) <= MAX_PLAN_BYTES else None


def decode_text(data):
    text = data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8', errors='replace')
    return text.replace('\x00', '')


def text_field(mapping, *keys):
    """A non-empty cleaned string from nested dict keys, or ''."""
    for key in keys:
        if not isinstance(mapping, dict):
            return ''
        mapping = mapping.get(key)
    return clean(mapping)


def find_root(start):
    """First directory upwards (including `start`) with `.git` (file or directory) or `.github/.skillsync.yaml`."""
    try:
        current = os.path.abspath(start)
    except (OSError, ValueError):
        return None
    for _ in range(MAX_LEVELS):
        try:
            if os.path.lexists(os.path.join(current, '.git')) or os.path.isfile(os.path.join(current, '.github', '.skillsync.yaml')):
                return current
        except (OSError, ValueError):
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def read_head(git_dir):
    """Branch name or short sha from `<git_dir>/HEAD`; None when unreadable."""
    data = read_bytes(os.path.join(git_dir, 'HEAD'), MAX_HEAD_BYTES)
    if data is None:
        return None
    text = decode_text(data).strip()
    if text.startswith('ref:'):
        ref = text[4:].strip()
        branch = ref[len('refs/heads/'):] if ref.startswith('refs/heads/') else ref
        return clean(branch) or None
    if _HEX_HEAD.match(text):
        return text[:7].lower()
    return None


def git_info(root):
    """(branch, worktree name) from `.git` of the root; each None when unknown. A `.git` FILE is `gitdir: <path>`."""
    dot_git = os.path.join(root, '.git')
    git_dir, worktree = None, None
    if os.path.isdir(dot_git):
        git_dir = dot_git
    elif os.path.isfile(dot_git):
        data = read_bytes(dot_git, MAX_HEAD_BYTES)
        text = decode_text(data) if data is not None else ''
        match = re.match(r'gitdir:[ \t]*(.+)', text.strip())
        if match:
            target = match.group(1).strip().splitlines()[0].strip()
            rooted = target[:1] in ('/', '\\') or (len(target) > 2 and target[1] == ':' and target[2] in ('/', '\\'))   # a path rooted with a slash is absolute (Windows, Python 3.13)
            git_dir = os.path.normpath(target if rooted else os.path.join(root, target))
            if os.path.basename(os.path.dirname(git_dir)) == 'worktrees':
                worktree = clean(os.path.basename(git_dir)) or None
    branch = read_head(git_dir) if git_dir else None
    return branch, worktree


def delivery_mode(root):
    """`pr` or `direct` from `delivery.mode` in the manifest (line scan, no YAML library); None otherwise."""
    data = read_bytes(os.path.join(root, '.github', '.skillsync.yaml'), MAX_MANIFEST_BYTES)
    if data is None:
        return None
    in_section, indent = False, None
    for raw in decode_text(data).splitlines():
        stripped = raw.lstrip(' \t')
        if not stripped or stripped.startswith('#'):
            continue
        depth = len(raw) - len(stripped)
        if depth == 0:
            in_section = re.match(r'delivery[ \t]*:[ \t]*(?:#.*)?$', raw) is not None
            indent = None
            continue
        if not in_section:
            continue
        if indent is None:
            indent = depth
        if depth != indent:
            continue
        match = re.match(r'mode[ \t]*:[ \t]*(.*)$', stripped)
        if not match:
            continue
        value = match.group(1).strip()
        quoted = re.match(r'''(["'])(.*?)\1''', value)
        value = quoted.group(2) if quoted else re.split(r'[ \t]#', value, maxsplit=1)[0].strip()
        return value if value in MODES else None
    return None


def _section_marks(text, heading):
    match = re.search(heading, text, re.MULTILINE)
    if not match:
        return []
    rest = text[match.end():]
    end = re.search(r'^##\s+\S', rest, re.MULTILINE)
    return _STEP.findall(rest[:end.start()] if end else rest)


def progress(text):
    """(done, total) from `## Progress`, else a checklist in `## Kroki` / `## Steps`; None without steps (as status.py)."""
    marks = (_section_marks(text, r'^##\s+Progress\s*$')
             or _section_marks(text, r'^##\s+(?:Kroki|Steps)\b[^\n]*$'))
    return (sum(1 for mark in marks if mark in 'xX'), len(marks)) if marks else None


def plan_status(text):
    match = _STATUS.search(text[:4000])
    return re.sub(r'[ \t-]+', '_', match.group('word').lower()) if match else 'unknown'   # `in progress` and `in-progress` are `in_progress` (as in `status.status_word`)


def current_plan(root):
    """(slug, progress or None) of the newest `in_progress` plan, or None."""
    docs = os.path.join(root, 'docs')
    directory = os.path.join(docs, 'plans')
    if os.path.islink(docs) or os.path.islink(directory) or not os.path.isdir(directory):
        return None
    with os.scandir(directory) as entries:
        names = sorted((entry.name for entry in entries if entry.name.endswith('.md')), reverse=True)[:MAX_PLANS]
    candidates = []
    for name in names:
        if name.lower() == 'readme.md':
            continue
        path = os.path.join(directory, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PLAN_BYTES:
            continue
        candidates.append((info.st_mtime, name, path))
    candidates.sort(key=lambda item: -item[0])
    for _, name, path in candidates:
        data = read_in_progress(path)   # one open: the head decides, the rest is read only for the chosen plan
        if data is None:
            continue
        stem = _DATE_PREFIX.sub('', name[:-3])
        return cap(clean(stem), 28), progress(decode_text(data))
    return None


def build_segments(payload):
    segments = []
    if not isinstance(payload, dict):
        return segments
    session = text_field(payload, 'session_name')
    if session:
        segments.append(('session', 'sesja: ' + cap(session, 40)))
    start = text_field(payload, 'workspace', 'project_dir') or text_field(payload, 'cwd')
    root = None
    try:
        if start and os.path.isdir(start):
            root = find_root(start) or os.path.abspath(start)
    except Exception:
        root = None
    if root:
        name = clean(os.path.basename(root.rstrip('/\\')) or root)
        if name:
            segments.append(('project', cap(name, 30)))
    branch = text_field(payload, 'worktree', 'branch')
    worktree = text_field(payload, 'worktree', 'name') or text_field(payload, 'workspace', 'git_worktree')
    if root and not (branch and worktree):
        try:
            file_branch, file_worktree = git_info(root)
        except Exception:
            file_branch, file_worktree = None, None
        branch = branch or file_branch or ''
        worktree = worktree or file_worktree or ''
    if branch:
        label = cap(branch, 40)
        if worktree:
            label += ' [wt: ' + cap(worktree, 30) + ']'
        segments.append(('branch', label))
    elif worktree:
        segments.append(('branch', '[wt: ' + cap(worktree, 30) + ']'))
    if root:
        try:
            mode = delivery_mode(root)
        except Exception:
            mode = None
        if mode:
            segments.append(('mode', mode))
        try:
            plan = current_plan(root)
        except Exception:
            plan = None
        if plan and plan[0]:
            slug, steps = plan
            segments.append(('plan', 'plan ' + slug + (' %d/%d' % steps if steps else '')))
    return segments


def columns():
    try:
        value = int(os.environ.get('COLUMNS', '').strip())
    except ValueError:
        return MAX_WIDTH
    return min(value, MAX_WIDTH) if value > 0 else MAX_WIDTH


def render(segments, limit, colour):
    parts, used = [], 0
    for kind, text in segments:
        room = limit - used - (len(SEPARATOR) if parts else 0)
        if room <= 0:
            break
        cut = len(text) > room
        parts.append((kind, cap(text, room)))
        used += len(parts[-1][1]) + (len(SEPARATOR) if len(parts) > 1 else 0)
        if cut:
            break
    pieces = [('\x1b[%sm%s\x1b[0m' % (COLOURS[kind], text)) if colour else text for kind, text in parts]
    return SEPARATOR.join(pieces)


def main():
    segments = []
    try:
        data = sys.stdin.buffer.read(MAX_STDIN_BYTES)
        payload = json.loads(data.decode('utf-8'))
        segments = build_segments(payload)
    except Exception:
        segments = []
    try:
        line = render(segments, columns(), not os.environ.get('NO_COLOR'))
    except Exception:
        line = ''
    try:
        sys.stdout.buffer.write(line.encode('utf-8', errors='replace') + b'\n')
        sys.stdout.buffer.flush()
    except Exception:
        try:   # a broken pipe: point stdout at the null device so that the flush at interpreter exit cannot turn the exit code into 120
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass


if __name__ == '__main__':
    main()
