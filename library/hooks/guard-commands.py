#!/usr/bin/env python3
"""PreToolUse hook (Bash): refuses permanent deletion that was probably a mistake.

Guardrail against a slip, not a security boundary: it reads the text of the command, so it does not see what hides in
scripts, variables it cannot resolve or deeper nesting. It never blocks because of its own failure (fail-open).

Refused (exit 2, the reason and a ready replacement go to stderr, which the model reads):
  rm -r/-R/--recursive (also `command rm`, `\\rm`, `/bin/rm`, `sudo rm`), git clean -f, git worktree remove --force,
  find ... -delete or -exec rm -r, `rmtree` in python -c, PowerShell Remove-Item -Recurse and rd/rmdir /s,
unless every target is under a temporary directory (/tmp, $TMPDIR, $XDG_RUNTIME_DIR, /run/user/<uid>, the session scratchpad;
$SKILLSYNC_GUARD_TMP_ROOTS, a list separated like PATH, replaces them all except the scratchpad; the temp root itself is never deletable, only what is below it),
under a regenerable directory of the project (node_modules, .venv, __pycache__, dist, build, plus --allow entries), or the
command carries a one-time exception that the user approved: `SKILLSYNC_GUARD_EXCEPT='<reason>' <command>`; every exception
is appended to <project>/.skillsync/hook-exceptions.jsonl (time, command, reason, agent).

Windows: the command is read as the Bash tool's shell (Git Bash) reads it: an UNQUOTED backslash path (`rm -rf C:\\work\\x`) loses its backslashes there (it
really removes `C:workx` relative to the cwd), so the hook sees the same relative path and refuses; quote it (`'C:\\work\\x'`) or use `C:/work/x` or
`/c/work/x` (also `/cygdrive/c/...`; mapped to the drive; this assumes Git Bash, whose default mounts make `/c` the drive C:, not WSL or a custom fstab). Doubt means refusal: `\\\\?\\C:\\...`, UNC paths and anything else unresolvable stay refused.

Test commands (180-J2, a second part of the same hook; it only WARNS, never blocks): with `--tests warn` a wide run of the tests of a project outside
`skillsync test-run` (or another wrapper) adds a warning to the context of the model. The classifier is `guard-tests.py` and the table of the tools is
`test-tools.json`, both next to this script (copies written by `skillsync link`); a missing or broken file, an error of the classifier, or anything unexpected
silences ONLY this part: it can never switch the refusal of deletion off (that part runs first and does not depend on it). The text reaches the model together
with the result of the command, i.e. AFTER it ran (documented behaviour of `additionalContext`; whether it arrives is NOT_VERIFIED until measured on a live session).
On Windows the command is read as Git Bash reads it, like for the deletion part. One-time exception: the same `SKILLSYNC_GUARD_EXCEPT='<reason>'` prefix.

Usage (set by `skillsync link`): guard-commands.py [--mode deny|off] [--allow <name-or-path> ...] [--tests warn|off] [--wide-modules N] [--wrapper <prefix> ...] [--subagent-commit warn|deny|off];
stdin = the hook JSON.
Commit boundary of a sub-agent (K6, `--subagent-commit`, default off = the behaviour of old entries): a call whose payload has a non-empty string `agent_id` is a sub-agent (the main
session, a falsy or non-string `agent_id` and a lone `agent_type` are not touched; a whitespace-only string is a sub-agent). `deny` refuses (exit 2, "podumysły nie commitują ... zgłoś zmiany orkiestratorowi") git commit|push|tag|merge|rebase|reset|
stash (not `stash list|show`)|restore|clean|cherry-pick|revert|`checkout --`|`checkout .`|`add -A|--all|.` and `gh pr create|merge|edit|close|ready`; `warn` only adds `additionalContext`. It does not
depend on `--mode`, `--tests`, `--attribution`, and `SKILLSYNC_GUARD_EXCEPT` does not lift it for a sub-agent.
Known limits of the boundary: scripts (`sh run.sh`), a pipe into a shell, aliases and variables (`$CMD`) hide the command: it is a guardrail against a slip, not a security boundary.
Known limit: `cd` is applied to the commands that follow it even inside `( ... )` groups and after `|`/`&`/`||` (a refusal-safe error only in one direction: such a `cd` may make a later relative path look temporary).
`find ... -delete` whose every -name is a regenerable artifact (*.pyc, *.pyo, __pycache__, .pytest_cache, .mypy_cache, .ruff_cache, .DS_Store, Thumbs.db) and has no -o/!/-not/groups passes.
Always refused when seen (no target check; use the exception): rmtree in `python -c`, xargs rm -r, PowerShell and rd forms.
The wrapper list is open (timeout, nice, env, sudo, xargs, flock, systemd-run, stdbuf ...): an unknown wrapper hides the command.
An exception prefix on `bash -c '...'`/`eval` covers the whole inner text.
Parsing: quotes, heredoc bodies, comments, redirections, `$(...)`/backticks, one level of `bash -c '...'` and `eval`,
wrappers (timeout, nice, env, sudo, xargs, flock, systemd-run ...). Text in arguments (`grep "rm -rf"`, `git commit -m ...`) is not a command.
"""
import datetime
import json
import os
import re
import shlex
import sys
import tempfile

EXCEPT_VAR = 'SKILLSYNC_GUARD_EXCEPT'
DEFAULT_NAMES = ('node_modules', '.venv', '__pycache__', 'dist', 'build')
MAX_DEPTH = 4
KEYWORDS = {'{', '}', '!', 'if', 'then', 'else', 'elif', 'do', 'while', 'until', 'fi', 'done'}
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh'}
RECOVERABLE = {'*.pyc', '*.pyo', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.DS_Store', 'Thumbs.db'}
SUBST = '\x01'  # placeholder of a command substitution inside a word
ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
GLOB = re.compile(r'[*?\[]')



MSYS_DRIVE = re.compile(r'^/(?:cygdrive/)?([A-Za-z])(?:/|$)')


def msys_to_native(path, windows=None):
    """Git Bash / MSYS form of a Windows drive (`/c/work/x`, `/cygdrive/c/x`) as the drive path the shell means (`C:/work/x`).
    Only on Windows (`windows=None`: `os.name == 'nt'`); every other text, also on other systems, comes back unchanged."""
    if not (os.name == 'nt' if windows is None else windows):
        return path
    match = MSYS_DRIVE.match(path)
    return path if not match else match.group(1).upper() + ':/' + path[match.end():]


def isabs(path):
    """`os.path.isabs` with the meaning of Python <= 3.12 on Windows (the same as `skillsync_workflow.fsutil.isabs`, repeated here
    because the hook is a standalone script): a path rooted with `/` or `\\` but without a drive is absolute, it is the root of the
    current drive (Git Bash may mean its own MSYS root by it: write `C:/...` or `/c/...` in `hooks.delete_allow`). Python 3.13 stopped taking it for absolute, which made the three callers below treat `/data/x` as relative.
    POSIX: no change."""
    if os.path.isabs(path):
        return True
    return os.name == 'nt' and path[:1] in ('/', '\\')


class Cmd:
    def __init__(self, words, assigns, subs):
        self.words = words
        self.assigns = assigns  # [(name, raw value)]
        self.subs = subs  # texts of `$(...)` / backticks found in the words


def _match_paren(text, i):
    """Index of the `)` closing the `(` before position i (text[i-1] == '(')."""
    depth, n = 1, len(text)
    while i < n:
        c = text[i]
        if c == '\\':
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            i = (j if j >= 0 else n) + 1
            continue
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == '\\' else 1
            i += 1
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def parse(text):
    """Commands of a shell text: `[Cmd]` (words after quote removal; redirections and heredoc bodies dropped)."""
    commands, words, subs = [], [], []
    cur = None
    pending = []  # heredoc delimiters waiting for their body
    i, n = 0, len(text)

    def end_word():
        nonlocal cur
        if cur is not None:
            words.append(''.join(cur))
            cur = None

    def end_cmd():
        nonlocal words, subs
        end_word()
        if words or subs:
            assigns = []
            while words and ASSIGN.match(words[0]) and not words[0].startswith('='):
                name, _, value = words.pop(0).partition('=')
                assigns.append((name, value))
            while words and words[0] in KEYWORDS:
                words.pop(0)
            if words and words[0] == 'function' and '{' in words:
                words = words[words.index('{') + 1:]
            commands.append(Cmd(words, assigns, subs))
        words, subs = [], []

    def add(chunk):
        nonlocal cur
        if cur is None:
            cur = []
        cur.append(chunk)

    def read_word_after(j):
        """A word starting at j (for redirection targets and heredoc delimiters); returns (word, new index)."""
        while j < n and text[j] in ' \t':
            j += 1
        k, out, quote = j, [], None
        while k < n:
            ch = text[k]
            if quote:
                if ch == quote:
                    quote = None
                else:
                    out.append(ch)
            elif ch == '\\' and k + 1 < n:
                out.append(text[k + 1])
                k += 2
                continue
            elif ch in '\'"':
                quote = ch
            elif ch in ' \t\n;&|()<>':
                break
            else:
                out.append(ch)
            k += 1
        return ''.join(out), k

    while i < n:
        c = text[i]
        if c in ' \t':
            end_word()
        elif c == '\n':
            end_cmd()
            i += 1
            while pending:  # skip the heredoc bodies that start after this line
                delimiter, strip = pending.pop(0)
                while i < n:
                    j = text.find('\n', i)
                    line = text[i:j if j >= 0 else n]
                    i = (j + 1) if j >= 0 else n
                    if (line.lstrip('\t') if strip else line) == delimiter:
                        break
            continue
        elif c == '#' and cur is None:
            j = text.find('\n', i)
            i = j if j >= 0 else n
            continue
        elif c == '\\':
            if i + 1 < n and text[i + 1] == '\n':
                i += 2
                continue
            add(text[i + 1] if i + 1 < n else '')
            i += 2
            continue
        elif c == "'":
            j = text.find("'", i + 1)
            j = j if j >= 0 else n
            add(text[i + 1:j])
            i = j + 1
            continue
        elif c == '"':
            i += 1
            add('')
            while i < n and text[i] != '"':
                ch = text[i]
                if ch == '\\' and i + 1 < n:
                    nxt = text[i + 1]
                    add(nxt if nxt in '"\\$`' else '\\' + nxt)
                    i += 2
                elif ch == '$' and text[i + 1:i + 2] == '(' and text[i + 1:i + 3] != '((':
                    end = _match_paren(text, i + 2)
                    subs.append(text[i + 2:end])
                    add(SUBST + text[i + 2:end].strip() + SUBST)
                    i = end + 1
                elif ch == '`':
                    j = text.find('`', i + 1)
                    j = j if j >= 0 else n
                    subs.append(text[i + 1:j])
                    add(SUBST + text[i + 1:j].strip() + SUBST)
                    i = j + 1
                else:
                    add(ch)
                    i += 1
            i += 1
            continue
        elif c == '$' and text[i + 1:i + 3] == '((':
            end = _match_paren(text, i + 2)
            add(text[i:end + 1])
            i = end + 1
            continue
        elif c == '$' and text[i + 1:i + 2] == '(' and text[i + 1:i + 3] != '((':
            end = _match_paren(text, i + 2)
            subs.append(text[i + 2:end])
            add(SUBST + text[i + 2:end].strip() + SUBST)
            i = end + 1
            continue
        elif c == '`':
            j = text.find('`', i + 1)
            j = j if j >= 0 else n
            subs.append(text[i + 1:j])
            add(SUBST + text[i + 1:j].strip() + SUBST)
            i = j + 1
            continue
        elif c in '<>' or (c == '&' and text[i + 1:i + 2] == '>'):
            # a redirection: `>`, `>>`, `2>`, `&>`, `>&2`, `<`, `<<EOF`, `<<<`; the target is not an argument
            if cur is not None and ''.join(cur).isdigit():
                cur = None
            else:
                end_word()
            j = i
            while j < n and text[j] in '<>&|':
                j += 1
            operator = text[i:j]
            if operator.startswith('<<') and operator != '<<<':
                strip = operator == '<<' and text[j:j + 1] == '-'
                if strip:
                    j += 1
                delimiter, j = read_word_after(j)
                pending.append((delimiter, strip))
            else:
                if text[j:j + 1] == '-' and operator.endswith('&'):
                    j += 1
                _, j = read_word_after(j)
            i = j
            continue
        elif c in ';&|':
            end_cmd()
            while i < n and text[i] in ';&|':
                i += 1
            continue
        elif c == '(' and text[i + 1:i + 2] == '(' and cur is None and not words:
            i = _match_paren(text, i + 1) + 1  # an arithmetic command ((...)): its `<<` is not a heredoc
            continue
        elif c in '()':
            end_cmd()
        else:
            add(c)
        i += 1
    end_cmd()
    return commands


def strip_wrappers(words, assigns):
    """`(argv, assigns, from_stdin)` after the transparent wrappers; argv[0] is the command that really runs."""
    assigns = list(assigns)
    from_stdin = False
    for _ in range(12):
        while words and ASSIGN.match(words[0]):
            name, _, value = words.pop(0).partition('=')
            assigns.append((name, value))
        if not words:
            break
        name = os.path.basename(words[0])
        rest = words[1:]
        if name in ('command', 'builtin', 'exec', 'nohup', 'setsid', 'time', 'noglob'):
            words = rest
            while words and words[0].startswith('-') and name in ('command', 'exec', 'setsid', 'time'):
                words = words[1:]
        elif name in ('sudo', 'doas'):
            words = rest
            while words and words[0].startswith('-') and words[0] != '--':
                token = words[0]
                if token.startswith('--'):
                    takes = '=' not in token and token in ('--user', '--group', '--host', '--prompt', '--chdir', '--role', '--type', '--close-from', '--other-user')
                else:
                    takes = len(token) >= 2 and token[-1] in 'ugCUhprt'
                words = words[2:] if takes else words[1:]
            if words[:1] == ['--']:
                words = words[1:]
        elif name == 'env':
            words = rest
            while words and (words[0].startswith('-') or ASSIGN.match(words[0])):
                token = words[0]
                if ASSIGN.match(token):
                    key, _, value = token.partition('=')
                    assigns.append((key, value))
                    words = words[1:]
                elif token in ('-S', '--split-string') and len(words) > 1:
                    try:
                        words = shlex.split(words[1]) + words[2:]
                    except ValueError:
                        words = words[2:]
                elif token in ('-u', '--unset', '-C', '--chdir'):
                    words = words[2:]
                elif token == '--':
                    words = words[1:]
                    break
                else:
                    words = words[1:]
        elif name in ('nice', 'ionice'):
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-n', '-c', '-p') and len(words[0]) == 2 else words[1:]
        elif name == 'timeout':
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-s', '-k', '--signal', '--kill-after') else words[1:]
            words = words[1:]  # the duration
        elif name == 'stdbuf':
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-i', '-o', '-e', '--input', '--output', '--error') else words[1:]
        elif name == 'xargs':
            from_stdin = True
            words = rest
            while words and words[0].startswith('-'):
                takes = words[0] in ('-I', '-n', '-P', '-d', '-L', '-s', '-E', '-a', '--max-args', '--max-procs', '--max-lines', '--delimiter',
                                     '--arg-file', '--eof', '--max-chars')
                words = words[2:] if takes else words[1:]
        elif name == 'flock':
            words, command = rest, None
            while words and words[0].startswith('-'):
                if words[0] in ('-c', '--command') and len(words) > 1:
                    command, words = words[1], words[2:]
                else:
                    words = words[2:] if words[0] in ('-w', '-E', '--timeout', '--wait', '--conflict-exit-code') else words[1:]
            words = words[1:]  # the lock file
            while words and words[0] in ('-c', '--command') and len(words) > 1:
                command, words = words[1], words[2:]
            if command is not None:
                return ['sh', '-c', command], assigns, from_stdin
        elif name == 'systemd-run':
            words = rest
            while words and words[0].startswith('-'):
                if words[0] == '--':
                    words = words[1:]
                    break
                takes = words[0] in ('-p', '--property', '-u', '--unit', '--slice', '-E', '--setenv', '-G', '--description', '--working-directory', '-d') or (
                    len(words[0]) == 2 and words[0][1] in 'puEGd')
                words = words[2:] if takes else words[1:]
        else:
            break
    return words, assigns, from_stdin


def _flags(args):
    """`(short flag letters, long flags, operands)` of an argv tail (stops flag parsing at `--`)."""
    shorts, longs, operands, flags_over = set(), set(), [], False
    for arg in args:
        if flags_over or not arg.startswith('-') or arg == '-':
            operands.append(arg)
        elif arg == '--':
            flags_over = True
        elif arg.startswith('--'):
            longs.add(arg.split('=', 1)[0])
        else:
            shorts.update(arg[1:])
    return shorts, longs, operands


def brace_expand(word, limit=64):
    """`a{b,c}d` -> [abd, acd] (nested, bounded); a word without a top-level `{x,y}` is returned as it is."""
    depth, start, commas = 0, -1, []
    for index, ch in enumerate(word):
        if ch == '{':
            if depth == 0:
                start, commas = index, []
            depth += 1
        elif ch == ',' and depth == 1:
            commas.append(index)
        elif ch == '}' and depth > 0:
            depth -= 1
            if depth == 0 and commas:
                pieces, last = [], start + 1
                for comma in commas + [index]:
                    pieces.append(word[last:comma])
                    last = comma + 1
                out = []
                for piece in pieces:
                    out += brace_expand(word[:start] + piece + word[index + 1:], limit)
                    if len(out) > limit:
                        return out[:limit]
                return out
    return [word]


class Guard:
    def __init__(self, cwd, scratchpad, allow, environ):
        self.cwd = os.path.realpath(cwd or os.getcwd())
        self.env = dict(environ)
        self.vars = {}
        self.safe = set()  # variables assigned from mktemp
        self.root = self.project_root(self.cwd)
        if self.env.get('SKILLSYNC_GUARD_TMP_ROOTS'):  # override of the default temporary roots (tests, unusual machines)
            roots = self.env['SKILLSYNC_GUARD_TMP_ROOTS'].split(os.pathsep)
        else:
            roots = ['/tmp', '/var/tmp', tempfile.gettempdir(), self.env.get('TMPDIR'), self.env.get('XDG_RUNTIME_DIR')]
            if hasattr(os, 'getuid'):
                roots.append('/run/user/%d' % os.getuid())
        # (root, exact): the temp roots themselves are not deletable; the scratchpad and absolute allow entries are
        self.tmp_roots = [(os.path.realpath(msys_to_native(r)), False) for r in roots if r]
        if scratchpad and isinstance(scratchpad, str):
            self.tmp_roots.append((os.path.realpath(msys_to_native(scratchpad)), True))
        self.names, self.paths = set(DEFAULT_NAMES), []
        for entry in allow:
            if isabs(msys_to_native(entry)):
                self.tmp_roots.append((os.path.realpath(msys_to_native(entry)), True))
            elif '/' in entry.strip('/'):
                self.paths.append(entry.strip('/'))
            else:
                self.names.add(entry.strip('/'))

    @staticmethod
    def project_root(start):
        path = start
        while True:
            if os.path.exists(os.path.join(path, '.git')):
                return path
            parent = os.path.dirname(path)
            if parent == path:
                return start
            path = parent

    def expand(self, word, base=None):
        """The path a word stands for, or None when it cannot be resolved (variable, substitution)."""
        if SUBST in word:
            inner = word.split(SUBST)
            # `$(mktemp ...)` (optionally followed by a suffix) is a fresh temporary path
            if len(inner) == 3 and inner[0] == '' and inner[1].split()[:1] == ['mktemp']:
                if '..' in inner[2].split('/'):
                    return None
                return os.path.join(tempfile.gettempdir(), 'mktemp-' + (inner[2] or '').lstrip('/'))
            if len(inner) == 3 and inner[1] == 'pwd':
                return self.cwd + inner[2]
            return None
        if word.startswith('~'):
            home = self.env.get('HOME') or os.path.expanduser('~')
            word = home + word[1:] if word == '~' or word.startswith('~/') else None
            if word is None:
                return None

        def var(match):
            name = match.group(1) or match.group(2)
            if name in self.vars:
                return self.vars[name]
            if name in self.safe:
                return os.path.join(tempfile.gettempdir(), 'mktemp-var')
            return self.env.get(name) if name in self.env else '\x02'
        word = word.replace('$$', '1')
        word = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', var, word)
        if '\x02' in word or '$' in word:
            return None
        return word

    def resolve(self, word, base=None):
        """Absolute normalised path of a target (glob tails cut), or None when unresolvable."""
        path = self.expand(word)
        if path is None:
            return None
        path = msys_to_native(path)
        match = GLOB.search(path)
        if match:
            if '..' in path[match.start():].split('/'):
                return None  # a glob followed by `..` can reach anything
            path = path[:match.start()]
            path = path if path.endswith('/') or not path else os.path.dirname(path) or '.'
        path = path or '.'
        if not isabs(path):
            path = os.path.join(base or self.cwd, path)
        return os.path.realpath(path)

    def target_ok(self, word):
        """A target is fine when its (glob-cut) path is allowed, or when the part before a glob inside a name (`/tmp/foo-*`)
        is a strict child of a temporary root (for project directories the cut path decides: `build*` is not `build`)."""
        path = self.resolve(word)
        if self.allowed(path):
            return True
        expanded = self.expand(word)
        match = GLOB.search(expanded) if expanded else None
        if path is None or not match or '..' in expanded[match.start():].split('/'):
            return False
        prefix = msys_to_native(expanded[:match.start()])
        if not prefix or prefix.endswith('/'):
            return False
        full = os.path.realpath(prefix if isabs(prefix) else os.path.join(self.cwd, prefix))
        return any(self.under(full, root, exact) for root, exact in self.tmp_roots)

    def allowed(self, path):
        if path is None:
            return False
        for root, exact in self.tmp_roots:
            if self.under(path, root, exact):
                return True
        if not self.under(path, self.root, False):
            return False
        relative = os.path.relpath(path, self.root).split(os.sep)
        if any(part in self.names for part in relative):
            return True
        joined = '/'.join(relative)
        return any(joined == p or joined.startswith(p + '/') for p in self.paths)

    @staticmethod
    def under(path, root, exact):
        try:
            if os.path.commonpath([path, root]) != root:
                return False
        except ValueError:  # another drive (Windows) or a mix of absolute and relative
            return False
        return exact or path != root

    def note_assignments(self, cmd, assigns):
        """Remember `NAME=value` of an assignment-only command (also `NAME=$(mktemp ...)`)."""
        for name, value in assigns:
            expanded = self.expand(value)
            if expanded is not None and SUBST not in value:
                self.vars[name] = expanded
                self.safe.discard(name)
            elif SUBST in value and value.split(SUBST)[1].split()[:1] == ['mktemp']:
                self.safe.add(name)
                self.vars.pop(name, None)
            else:
                self.vars.pop(name, None)
                self.safe.discard(name)

    @staticmethod
    def expand_targets(targets):
        out = []
        for target in targets:
            out += brace_expand(target)
        return out

    def classify(self, cmd, depth=0):
        """Findings of one parsed command: `[(kind, words, targets, hint)]` plus the exception reason (or None)."""
        found = []
        words, assigns, from_stdin = strip_wrappers(list(cmd.words), cmd.assigns)
        if not words:
            self.note_assignments(cmd, assigns)
            return found, None
        name = os.path.basename(words[0])
        low = name.lower()
        args = words[1:]
        reason = None
        for key, value in assigns:
            if key == EXCEPT_VAR and value.strip():
                reason = value.strip()
        if name == 'cd':
            target = self.resolve(args[0]) if args and args[0] != '-' else None
            if target:
                self.cwd = target
            return found, None
        if name in ('export', 'local', 'declare', 'readonly', 'typeset'):
            self.note_assignments(cmd, [tuple(a.partition('=')[::2]) for a in args if ASSIGN.match(a)])
            return found, None
        low_name = name.lower()
        if low_name in ('powershell', 'pwsh', 'powershell.exe', 'pwsh.exe') and depth < MAX_DEPTH:
            for k, arg in enumerate(args):
                if arg.lower() in ('-c', '-command') and k + 1 < len(args):
                    return self.walk(' '.join(args[k + 1:]), depth + 1, reason), reason
        if low_name in ('cmd', 'cmd.exe') and depth < MAX_DEPTH:
            for k, arg in enumerate(args):
                if arg.lower() in ('/c', '/k') and k + 1 < len(args):
                    return self.walk(' '.join(args[k + 1:]), depth + 1, reason), reason
        if name in SHELLS and depth < MAX_DEPTH:
            for k, arg in enumerate(args):
                if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:] and k + 1 < len(args):
                    saved, self.cwd = self.cwd, self.cwd
                    inner = self.walk(args[k + 1], depth + 1, reason)
                    self.cwd = saved
                    return inner, reason
        if name == 'eval' and depth < MAX_DEPTH:
            return self.walk(' '.join(args), depth + 1, reason), reason
        if name == 'rm':
            shorts, longs, operands = _flags(args)
            if shorts & {'r', 'R'} or any(flag.startswith('--rec') for flag in longs):
                found.append(('rm', words, operands, from_stdin))
        elif name == 'git':
            found += self.git(words, args)
        elif name == 'find':
            found += self.find(words, args)
        elif name in ('python', 'python3', 'py') or re.fullmatch(r'python3(\.\d+)?', name):
            if args[:1] == ['-c'] and len(args) > 1 and 'rmtree' in args[1]:
                found.append(('rmtree', words, [], False))
        elif low in ('remove-item', 'ri'):
            if any(a.lower() == '-r' or (len(a) >= 4 and '-recurse'.startswith(a.lower())) for a in args):
                found.append(('powershell', words, [a for a in args if not a.startswith('-')], False))
        elif low in ('rd', 'rmdir'):
            if any(a.lower() == '/s' for a in args):
                found.append(('windows-rd', words, [a for a in args if not a.startswith('/')], False))
        # judge the targets: only what is NOT allowed is a finding
        bad = []
        for kind, fwords, targets, stdin in found:
            if kind in ('rmtree', 'powershell', 'windows-rd') or stdin:
                bad.append((kind, fwords, targets, stdin))
            elif targets and any(not self.target_ok(t) for t in self.expand_targets(targets)):
                bad.append((kind, fwords, targets, stdin))
        return bad, reason

    def git(self, words, args):
        base = self.cwd
        i = 0
        while i < len(args):
            a = args[i]
            if a == '-C' and i + 1 < len(args):
                base = self.resolve(args[i + 1]) or base
                i += 2
            elif a in ('-c', '--git-dir', '--work-tree', '--namespace') and i + 1 < len(args):
                i += 2
            elif a.startswith('-'):
                i += 1
            else:
                break
        rest = args[i:]
        if not rest:
            return []
        sub, tail = rest[0], rest[1:]
        kept, skip = [], False
        for token in tail:  # the value of -e/--exclude is a pattern, not a target
            if skip:
                skip = False
            elif token in ('-e', '--exclude'):
                skip = True
            else:
                kept.append(token)
        shorts, longs, operands = _flags(kept)
        if sub == 'clean' and ('f' in shorts or '--force' in longs) and not ('n' in shorts or '--dry-run' in longs):
            where = [self.resolve(o, base) or o for o in operands] or [base]
            return [('git-clean', words, where, False)]
        if sub == 'worktree' and tail[:1] == ['remove']:
            shorts, longs, operands = _flags(tail[1:])
            if 'f' in shorts or '--force' in longs:
                return [('worktree-force', words, [self.resolve(o, base) or o for o in operands], False)]
        return []

    def find(self, words, args):
        while args and (args[0] in ('-H', '-L', '-P') or args[0].startswith('-O')):
            args = args[1:]
        if args[:1] == ['-D']:
            args = args[2:]
        roots = []
        for a in args:
            if a.startswith('-') or a in ('(', ')', '!'):
                break
            roots.append(a)
        text = ' '.join(args)
        deleting = '-delete' in args
        if '-exec' in args or '-execdir' in args:
            for k, a in enumerate(args):
                if a in ('-exec', '-execdir') and os.path.basename(args[k + 1:k + 2][0] if args[k + 1:k + 2] else '') == 'rm':
                    shorts, longs, _ = _flags(args[k + 2:])
                    deleting = deleting or bool(shorts & {'r', 'R'}) or '--recursive' in longs
        if deleting and not ('-exec' in args or '-execdir' in args) and self.only_artifacts(args):
            return []  # the everyday cleanup of regenerable files (`find . -name '*.pyc' -delete`)
        if deleting:
            return [('find-delete', words, roots or ['.'], False)]
        return []

    @staticmethod
    def only_artifacts(args):
        """True when every name test of a `find ... -delete` is a regenerable artifact and nothing can widen the match."""
        if any(a in ('-o', '-or', '!', '-not', '(', ')') for a in args):
            return False
        names = [args[k + 1] for k, a in enumerate(args[:-1]) if a in ('-name', '-iname')]
        return bool(names) and all(name in RECOVERABLE for name in names)

    def walk(self, text, depth=0, inherited=None):
        """`[(kind, words, targets, from_stdin, exception reason or None)]`: the exception of a command covers only that command."""
        findings = []
        for cmd in parse(text):
            for sub in cmd.subs:
                if depth < MAX_DEPTH:
                    saved = self.cwd  # a `cd` inside a substitution does not change the directory of the rest
                    findings += self.walk(sub, depth + 1, inherited)
                    self.cwd = saved
            found, own = self.classify(cmd, depth)
            findings += [item if len(item) == 5 else item + (own or inherited,) for item in found]
        return findings


HINTS = {
    'rm': 'trash -- {targets}',
    'git-clean': 'git clean -n  (podglad; potem usun do kosza konkretne pliki: trash -- <sciezki>)',
    'worktree-force': 'git worktree remove {targets}  (bez --force; gdy brudny, zglos uzytkownikowi)',
    'find-delete': 'find {targets} <te same warunki> -print  (podglad; potem trash -- <sciezki>)',
    'rmtree': 'trash -- <sciezka>  (zamiast rmtree w python -c)',
    'powershell': 'usun do kosza (SendToRecycleBin), nie Remove-Item -Recurse',
    'windows-rd': 'usun do kosza (SendToRecycleBin), nie rd /s',
}


def message(findings, agent):
    kind, words, targets, from_stdin = findings[0][:4]
    shown = (shlex.join(words) if all(isinstance(w, str) for w in words) else ' '.join(map(str, words))).replace(SUBST, '`')
    # a glob stays unquoted (quotes would switch the expansion off); the other targets are quoted for the shell
    quoted = ' '.join(t if (GLOB.search(t) and re.fullmatch(r'[\w@%+=:,./*?\[\]-]+', t)) else shlex.quote(t) for t in targets) if targets else '<sciezki>'
    hint = HINTS.get(kind, 'trash -- <sciezki>').format(targets=quoted)
    lines = [
        'Zablokowano trwale usuniecie (skillsync guard-commands): ' + shown[:300],
        'Zamiast tego: ' + hint,
        'Wyjatek: tylko za zgoda uzytkownika w oknie wyboru (AskUserQuestion). Po zgodzie powtorz komende z prefiksem '
        + EXCEPT_VAR + "='<powod>' <komenda>; slad trafia do .skillsync/hook-exceptions.jsonl.",
    ]
    if agent:
        lines.append('Jestes podumyslem: nie ponawiaj, zglos to sesji glownej (ona pyta uzytkownika).')
    if len(findings) > 1:
        lines.append('Wiecej niz jedno trafienie w tej komendzie (%d); pierwsze pokazano.' % len(findings))
    return '\n'.join(lines)


def command_words(text, depth=0, inherited=None):
    """`[(words, exception reason or None)]` of every command of the text, also of `$(...)` and one level of `bash -c '...'`/`eval` (the same parser as the deletion
    part). The words are the RAW words of a command (the wrappers are for the classifier to read: `flock ... systemd-run ...` must stay a wrapper)."""
    out = []
    for cmd in parse(text):
        for sub in cmd.subs:
            if depth < MAX_DEPTH:
                out += command_words(sub, depth + 1, inherited)
        words = list(cmd.words)
        reason = inherited
        for key, value in cmd.assigns:
            if key == EXCEPT_VAR and value.strip():
                reason = value.strip()
        if not words:
            continue
        name = os.path.basename(words[0])
        if name in SHELLS and depth < MAX_DEPTH:
            inner = [k for k, arg in enumerate(words[1:], 1) if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:] and k + 1 < len(words)]
            if inner:
                out += command_words(words[inner[0] + 1], depth + 1, reason)
                continue
        if name == 'eval' and depth < MAX_DEPTH:
            out += command_words(' '.join(words[1:]), depth + 1, reason)
            continue
        low = name.lower()
        if low in ('powershell', 'pwsh', 'powershell.exe', 'pwsh.exe') and depth < MAX_DEPTH:
            inner = [k for k, arg in enumerate(words[1:], 1) if arg.lower() in ('-c', '-command') and k + 1 < len(words)]
            if inner:
                out += command_words(' '.join(words[inner[0] + 1:]), depth + 1, reason)
                continue
        if low in ('cmd', 'cmd.exe') and depth < MAX_DEPTH:
            inner = [k for k, arg in enumerate(words[1:], 1) if arg.lower() in ('/c', '/k') and k + 1 < len(words)]
            if inner:
                out += command_words(' '.join(words[inner[0] + 1:]), depth + 1, reason)
                continue
        out.append((words, reason))
    return out


def load_test_part(wide_modules, wrappers):
    """`(classifier module, table)` of the test commands part, or `None` when its files are missing or unusable: this part then stays silent."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True   # the hook directory must not collect a __pycache__ (it is not ignored in every project)
    try:
        spec = importlib.util.spec_from_file_location('guard_tests', os.path.join(here, 'guard-tests.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        table = module.load_table(os.path.join(here, 'test-tools.json'))
    except BaseException:  # noqa: BLE001 - any failure of the classifier files (even a SystemExit) silences the test part only
        return None
    finally:
        sys.dont_write_bytecode = saved
    return (module, table) if table else None


def test_warning(command, payload, wide_modules, wrappers):
    """`(text or None, [(reason)] exceptions used)`: the warning for the wide test runs of a command, or None. Never raises."""
    try:
        loaded = load_test_part(wide_modules, wrappers)
        if loaded is None:
            return None, []
        module, table = loaded
        known = [word for word, _ in table['tools']] + list(table['modules']) + ['make', 'pnpm', 'yarn']
        if not any(word in command for word in known):
            return None, []   # no word of any tool anywhere in the text: nothing to classify (the cheap filter of the spec)
        extra = []
        for item in wrappers:
            try:
                extra.append(tuple(shlex.split(item)))
            except ValueError:
                extra.append(tuple(item.split()))   # an open quote in a prefix: its words as they stand, the rest of the part keeps working
        warned, excused = [], []
        for words, reason in command_words(command):
            verdict = module.classify(words, table, wide_modules, extra)
            if verdict['verdict'] != 'wide':
                continue
            if reason:
                excused.append(reason)
                continue
            warned.append((' '.join(str(w) for w in words)[:300], verdict))
        if not warned:
            return None, excused
        root = Guard.project_root(os.path.realpath(payload.get('cwd') or os.getcwd()))
        text = module.warning_text(warned[0][0], warned[0][1], root.replace('\\', '/'))
        if len(warned) > 1:
            text += ' The command holds %d wide test runs; the first is shown.' % len(warned)
        if payload.get('agent_id'):
            text += ' This message concerns a sub-agent: the main session reports an exception.'
        return text, excused
    except Exception:  # noqa: BLE001 - the test part never takes the hook down
        return None, []


def emit_warning(text, strategy='context'):
    """THE ONE PLACE where the strategy of delivering the warning is chosen. `context`: `hookSpecificOutput.additionalContext` on stdout, exit 0, no
    `permissionDecision` and no `updatedInput` (documented: added to the context of the model next to the result of the tool; whether the model sees it without a
    permission decision is NOT_VERIFIED). Another strategy (after the live measurement) is one more branch here, the classifier does not change."""
    if strategy == 'context':
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': text[:9000]}}))
        return True
    return False


def log_exception(root, payload, command, reason, findings):
    path = os.path.join(root, '.skillsync', 'hook-exceptions.jsonl')
    record = {'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'command': command[:500], 'reason': reason[:300],
              'kind': [f[0] for f in findings], 'agent_id': payload.get('agent_id'), 'session_id': payload.get('session_id'),
              'cwd': payload.get('cwd')}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(record) + '\n')
    except Exception as error:
        print('guard-commands: nie udalo sie zapisac sladu wyjatku: %s' % error, file=sys.stderr)


ATTRIBUTION_DATA = 'attribution-patterns.json'
WRITER_TOOLS = {'gh', 'glab', 'az', 'tea', 'hub', 'acli', 'jira'}
WRITER_VERBS = {'create', 'edit', 'update', 'comment', 'review', 'add', 'new', 'set', 'post', 'reply', 'merge', 'close', 'reopen', 'submit', 'note', 'transition', 'assign'}
GIT_WRITERS = {'commit', 'tag', 'merge', 'notes', 'revert', 'cherry-pick'}
GIT_VALUE_FLAGS = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--config-env', '--super-prefix'}
API_FIELD_FLAGS = {'-f', '-F', '--field', '--raw-field', '--input', '-d', '--data'}
FILE_FLAGS = {'-F', '--file', '--body-file', '-f', '--input'}
FILE_LIMIT = 65536
HERESTRING = re.compile(r"<<<[ \t]*(\"(?:[^\"\\\\]|\\\\.)*\"|'[^']*'|[^\s;&|)]+)")
ASSIGN_VALUE = re.compile(r"(?:^|[\s;&|(])[A-Za-z_][A-Za-z0-9_]*=(\"(?:[^\"\\\\]|\\\\.)*\"|'[^']*')")
PROCESS_SUBST = re.compile(r"<\((.*?)\)", re.DOTALL)
READERS = {'cat', 'tac', 'type', 'head', 'tail', 'less', 'more'}
PRINTERS = {'echo', 'printf'}
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def load_attribution(script_dir=None):
    """`[(id, class, compiled)]` of the enforced patterns of `attribution-patterns.json` next to this script; `[]` when the file is missing or
    unusable (fail-open: only this part goes silent, `skillsync doctor` reports the missing file)."""
    path = os.path.join(script_dir or os.path.dirname(os.path.abspath(__file__)), ATTRIBUTION_DATA)
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        flags = {'ignorecase': re.IGNORECASE, 'multiline': re.MULTILINE}
        rules = []
        for entry in data.get('patterns') or []:
            if entry.get('class') in (data.get('enforce') or []):
                bits = 0
                for flag in entry.get('flags') or []:
                    bits |= flags.get(flag, 0)
                rules.append((entry['id'], entry['class'], re.compile(entry['regex'], bits)))
        return rules
    except Exception:  # noqa: BLE001 - fail-open
        return []


def heredoc_bodies(text):
    """Bodies of the here-documents of a shell text (the parser drops them)."""
    bodies, lines, i = [], text.split('\n'), 0
    while i < len(lines):
        for match in HEREDOC.finditer(lines[i]):
            delim, body = match.group(2), []
            j = i + 1
            while j < len(lines) and lines[j].strip() != delim:
                body.append(lines[j])
                j += 1
            bodies.append('\n'.join(body))
        i += 1
    return bodies


def writer_words(words):
    """The words of a command that WRITES text somewhere visible (commit, tag, PR/issue/work item create/edit/comment ...), else `None`.
    Reading commands (`git log --grep`, `gh pr list --search`) are not writers: they may quote a phrase."""
    for index, word in enumerate(words[:8]):
        name = os.path.basename(word).lower()
        if name.endswith('.exe'):
            name = name[:-4]
        rest = words[index + 1:]
        if name == 'git':
            k = 0
            while k < len(rest) and rest[k].startswith('-'):
                k += 2 if rest[k] in GIT_VALUE_FLAGS else 1
            if k < len(rest) and rest[k] in GIT_WRITERS:
                return rest[k:]
            return None
        if name in WRITER_TOOLS:
            if name == 'gh' and rest[:1] == ['api']:
                return rest if any(arg in API_FIELD_FLAGS or arg.split('=')[0] in API_FIELD_FLAGS for arg in rest) else None
            return rest if any(arg in WRITER_VERBS for arg in rest[:5]) else None
    return None


def word_variants(word):
    """The text forms of one argument that a rule anchored at a line start must see: the word, its `\\n` read as a line break (`$'a\\nb'`), the part after
    `--option=` and the text glued to `-m` (`-mTEXT`)."""
    forms = [word]
    if word.startswith('-') and '=' in word:
        forms.append(word.split('=', 1)[1])
    if word.startswith('-') and not word.startswith('--') and len(word) > 2 and not word[2:3].isspace():
        forms.append(word[2:])
    return forms + [form.replace('\\n', '\n') for form in forms if '\\n' in form]


def _join_dir(base, target):
    if os.name == 'nt':
        target = msys_to_native(target)
    return target if isabs(target) else os.path.join(base, target)


def attribution_findings(command, cwd, rules):
    """`[(pattern id, class, line)]` found in the text that the command writes: the words of its writer commands (also `--option=TEXT`, `-mTEXT`, `$'..\\n..'`),
    the files named by `-F`/`--file`/`--body-file` (read from the directory that `cd` and `git -C` give, MSYS paths mapped on Windows; up to 64 KiB) and,
    when some writer is present, the here-document bodies of the command. A writer that reads its text from the standard input (`-F -`, `--body-file -`)
    also scans the words of the other commands of the text (`printf ... | git commit -F -`). Text built in variables is not visible."""
    found, texts, writers, files = [], [], 0, []
    stdin_reader, others = False, []
    here = cwd or os.getcwd()
    for words, _ in command_words(command):
        if words and os.path.basename(words[0]) == 'cd' and len(words) > 1:
            here = _join_dir(here, words[1])
            continue
        rest = writer_words(words)
        if rest is None:
            others.append(words)
            continue
        writers += 1
        base = here
        for k, arg in enumerate(words[:-1]):
            if arg == '-C' and 'git' in [os.path.basename(w) for w in words[:k + 1]]:
                base = _join_dir(base, words[k + 1])
        for word in rest:
            texts.extend(word_variants(word))
            if word.startswith('@') and len(word) > 1:   # `az ... --description @file`
                files.append(_join_dir(base, word[1:]))
        for arg in rest:   # `gh api -F body=@file`
            if '=@' in arg:
                files.append(_join_dir(base, arg.split('=@', 1)[1]))
        for k, arg in enumerate(rest):
            target = rest[k + 1] if k + 1 < len(rest) else None
            if arg in FILE_FLAGS and target:
                if target == '-':
                    stdin_reader = True
                else:
                    files.append(_join_dir(base, target))
            elif arg.startswith('--body-file=') or arg.startswith('--file='):
                value = arg.split('=', 1)[1]
                if value == '-':
                    stdin_reader = True
                else:
                    files.append(_join_dir(base, value))
    if not writers:
        return found
    texts += heredoc_bodies(command)
    # text that reaches the writer in other ways: here-strings, values of shell variables, process substitutions (`-F <(echo ...)`)
    for pattern in (HERESTRING, ASSIGN_VALUE):
        texts += [match.group(1).strip('"\'') for match in pattern.finditer(command)]
    for match in PROCESS_SUBST.finditer(command):
        texts.append(match.group(1))
        for inner, _ in command_words(match.group(1)):
            for word in inner:
                texts.extend(word_variants(word))
    for words in others:   # `--body "$(cat pr.md)"`: the files that `cat` and its kin read; `-m "$(echo ...)"`, `printf ... > f && git commit -F f`: what echo and printf print
        if words and os.path.basename(words[0]) in READERS:
            files.extend(_join_dir(here, word) for word in words[1:] if not word.startswith('-'))
        elif words and os.path.basename(words[0]) in PRINTERS:
            for word in words[1:]:
                texts.extend(word_variants(word))
    if stdin_reader:
        for words in others:
            for word in words:
                texts.extend(word_variants(word))
    for path in files:
        try:
            with open(path, encoding='utf-8', errors='replace') as handle:
                texts.append(handle.read(FILE_LIMIT))
        except OSError:
            pass
    for text in texts:
        for number, line in enumerate(text.splitlines(), 1):
            for rule_id, klass, compiled in rules:
                if compiled.search(line):
                    found.append((rule_id, klass, number))
    return found


def attribution_message(found):
    ids = ', '.join(dict.fromkeys('%s (klasa %s)' % (rule_id, klass) for rule_id, klass, _ in found))
    return ('guard-commands: odmowa. Tekst tego polecenia zawiera stopkę, podpis albo trailer narzędzia AI: %s.\n'
            'Reguła rdzenia: w tekstach wychodzących (commit, PR, zadanie, dokument) nie dopisuje się stopek ani podpisów narzędzi AI. '
            'Usuń te linie z treści (także z heredoc i plików -F/--body-file) i uruchom polecenie ponownie. '
            'To zakaz bez jednorazowego wyjątku; jego sprawdzenie nie dotyczy poleceń, które tylko czytają lub cytują tekst.' % ids)


SUBAGENT_GIT = {'commit', 'push', 'tag', 'merge', 'rebase', 'reset', 'restore', 'clean', 'cherry-pick', 'revert'}
SUBAGENT_GH_PR = {'create', 'merge', 'edit', 'close', 'ready'}
GH_VALUE_FLAGS = {'-R', '--repo', '--hostname'}


def _boundary_words(words, depth=0):
    """The forbidden-to-a-sub-agent command (`git commit`, `gh pr merge` ...) that the words of ONE command stand for, else `None`. The wrappers are stripped first."""
    argv = list(words)
    argv, _, _ = strip_wrappers(argv, [])
    if not argv:
        return None
    name = os.path.basename(argv[0]).lower()
    if name.endswith('.exe'):
        name = name[:-4]
    rest = argv[1:]
    if name == 'eval' and depth < MAX_DEPTH:
        for sub, _ in command_words(' '.join(rest), depth + 1):
            found = _boundary_words(sub, depth + 1)
            if found:
                return found
        return None
    if name in SHELLS and depth < MAX_DEPTH:   # `flock f -c '...'` turns into `sh -c '...'` in strip_wrappers
        inner = [k for k, arg in enumerate(rest) if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:] and k + 1 < len(rest)]
        if inner:
            for sub, _ in command_words(rest[inner[0] + 1], depth + 1):
                found = _boundary_words(sub, depth + 1)
                if found:
                    return found
        return None
    if name == 'git':
        k = 0
        while k < len(rest) and rest[k].startswith('-'):
            k += 2 if rest[k] in GIT_VALUE_FLAGS else 1
        if k >= len(rest):
            return None
        sub, args = rest[k], rest[k + 1:]
        if sub == 'tag' and (not args or any(a in ('-l', '--list', '-v', '--verify', '--contains', '--points-at') or a.startswith(('-n', '--contains=', '--points-at='))
                                          for a in args)):
            return None   # listing tags is read-only
        if sub == 'clean' and any(a == '--dry-run' or (a.startswith('-') and not a.startswith('--') and 'n' in a[1:]) for a in args):
            return None   # a dry run deletes nothing
        if sub in SUBAGENT_GIT:
            return 'git ' + sub
        if sub == 'stash':
            return None if args[:1] and args[0] in ('list', 'show') else 'git stash'
        if sub == 'checkout':   # `checkout <branch>` and `-b` switch; `--` (restore paths) and `.` (discard everything) destroy changes
            return 'git checkout --' if ('--' in args or '.' in args) else None
        if sub == 'add':
            for arg in args:
                if arg in ('-A', '--all', '.') or (arg.startswith('-') and not arg.startswith('--') and 'A' in arg[1:]):
                    return 'git add ' + arg
        return None
    if name == 'gh':
        k = 0
        while k < len(rest) and rest[k].startswith('-'):
            k += 2 if rest[k] in GH_VALUE_FLAGS else 1
        if rest[k:k + 1] == ['pr']:
            j = k + 1
            while j < len(rest) and rest[j].startswith('-'):   # `gh pr -R o/r merge 5`
                j += 2 if rest[j] in GH_VALUE_FLAGS else 1
            if rest[j:j + 1] and rest[j] in SUBAGENT_GH_PR:
                return 'gh pr ' + rest[j]
    return None


def subagent_violation(command):
    """The first command of the text that a sub-agent must not run (`git commit`, `gh pr merge` ...), else `None`. Never raises (fail-open)."""
    try:
        for words, _ in command_words(command):   # the exception prefix (`reason`) is deliberately ignored: a sub-agent has no consent of the user
            found = _boundary_words(words)
            if found:
                return found
    except Exception:  # noqa: BLE001
        return None
    return None


def subagent_message(found, executed=False):
    if executed:
        return ('Uwaga (guard-commands, granica podagenta): polecenie zostało już wykonane; podumysły nie commitują ani nie zmieniają stanu repozytorium (%s). '
                'Zgłoś zmiany orkiestratorowi (sesji głównej), a on je zacommituje.' % found)
    return ('Zablokowano (guard-commands, granica podagenta): podumysły nie commitują ani nie zmieniają stanu repozytorium (%s). '
            'Zgłoś zmiany orkiestratorowi (sesji głównej), a on je zacommituje; wyjątek SKILLSYNC_GUARD_EXCEPT nie dotyczy podagenta.' % found)


_FEEDBACK_PROVIDER = re.compile(r'azure-feedback')


def feedback_provider_violation(command):
    """True when a line of the command text calls the internal provider `azure-feedback` (through `skillsync tracker` or by hand with `az devops`/`az boards`):
    only `skillsync feedback` may write to a tracker for a change request, and only after the consent of the user. A text match per line, so a heuristic
    (the boundary is the machine); a line that only mentions the name (a commit message, `echo`, `grep`) without a tracker call passes."""
    for line in command.splitlines():
        if _FEEDBACK_PROVIDER.search(line) and re.search(r'\bskillsync\b[^\n]*\btracker\b|(?:^|[\s;&|(])az\s+(?:devops|boards)\b', line):
            return True
    return False


def feedback_provider_message():
    return ('guard-commands: the provider `azure-feedback` is internal. Send a change request with `skillsync feedback send F<n>` after the user typed '
            'the consent word `WYŚLIJ <sha12>` for the previewed content; do not call the tracker directly.')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode, allow = 'deny', []
    tests_mode, wide_modules, wrappers = 'off', 25, []
    attribution_mode = 'on'
    sub_mode = 'off'
    i = 0
    while i < len(argv):
        if argv[i] == '--mode' and i + 1 < len(argv):
            mode = argv[i + 1]
            i += 2
        elif argv[i] == '--allow' and i + 1 < len(argv):
            allow.append(argv[i + 1])
            i += 2
        elif argv[i] == '--tests' and i + 1 < len(argv):
            tests_mode = argv[i + 1]
            i += 2
        elif argv[i] == '--wide-modules' and i + 1 < len(argv):
            try:
                wide_modules = int(argv[i + 1]) if int(argv[i + 1]) >= 2 else 25
            except ValueError:
                wide_modules = 25
            i += 2
        elif argv[i] == '--attribution' and i + 1 < len(argv):
            attribution_mode = argv[i + 1]
            i += 2
        elif argv[i] == '--subagent-commit' and i + 1 < len(argv) and not argv[i + 1].startswith('--'):
            sub_mode = argv[i + 1] if argv[i + 1] in ('warn', 'deny') else 'off'
            i += 2
        elif argv[i] == '--wrapper' and i + 1 < len(argv):
            wrappers.append(argv[i + 1])
            i += 2
        else:
            i += 1
    if mode != 'deny' and tests_mode != 'warn' and attribution_mode != 'on' and sub_mode == 'off':
        return 0
    boundary = None
    try:
        payload = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))
        if not isinstance(payload, dict) or payload.get('tool_name') != 'Bash':
            return 0
        tool_input = payload.get('tool_input') or {}
        command = tool_input.get('command') if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return 0
        agent = payload.get('agent_id')
        boundary = subagent_violation(command) if sub_mode != 'off' and isinstance(agent, str) and agent else None   # a non-string `agent_id` is not a sub-agent
        if attribution_mode == 'on':   # the ban on AI attribution does not depend on `--mode`, takes no exception and never takes the rest down
            try:
                rules = load_attribution()
                hits = attribution_findings(command, payload.get('cwd'), rules) if rules else []
            except Exception:  # noqa: BLE001 - fail-open
                hits = []
            if hits:
                print(attribution_message(hits), file=sys.stderr)
                if boundary and sub_mode == 'deny':
                    print(subagent_message(boundary), file=sys.stderr)
                return 2
        if feedback_provider_violation(command):   # does not depend on `--mode`: a request to a foreign tracker needs the consent of the user
            print(feedback_provider_message(), file=sys.stderr)
            return 2
        findings = []
        if mode == 'deny':
            guard = Guard(payload.get('cwd'), payload.get('scratchpad_dir'), allow, os.environ)
            findings = guard.walk(command)
    except Exception as error:  # fail-open: the hook's own failure never blocks the work
        if boundary and sub_mode == 'deny':   # the boundary was already found: fail-open only where nothing was detected
            print(subagent_message(boundary), file=sys.stderr)
            return 2
        print('guard-commands: blad hooka, komenda przepuszczona (%s: %s)' % (type(error).__name__, error), file=sys.stderr)
        return 0
    if findings:
        refused = [item for item in findings if not item[4]]
        if refused:
            print(message(refused, payload.get('agent_id')), file=sys.stderr)
            return 2   # the refusal of deletion comes first and does not depend on the test part
        if boundary and sub_mode == 'deny':   # the boundary comes before the exception log: a refused command must not leave an entry
            print(subagent_message(boundary), file=sys.stderr)
            return 2
        log_exception(guard.root, payload, command, '; '.join(dict.fromkeys(item[4] for item in findings)), findings)
    if boundary and sub_mode == 'deny':
        print(subagent_message(boundary), file=sys.stderr)
        return 2
    text = subagent_message(boundary, executed=True) if boundary else None   # sub_mode == 'warn'
    if tests_mode == 'warn':
        tests_text, excused = test_warning(command, payload, wide_modules, wrappers)
        try:
            if excused:
                log_exception(Guard.project_root(os.path.realpath(payload.get('cwd') or os.getcwd())), payload, command, '; '.join(dict.fromkeys(excused)), [('tests',)])
        except Exception:  # noqa: BLE001 - the test part never takes the hook down
            pass
        if tests_text:
            text = text + ' ' + tests_text if text else tests_text   # ONE output: two JSON documents on stdout would not parse
    try:
        if text:
            emit_warning(text)
    except Exception:  # noqa: BLE001 - the warning never takes the hook down
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
