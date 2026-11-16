#!/usr/bin/env python3
"""PreToolUse hook (Bash): refuses permanent deletion that was probably a mistake.

Guardrail against a slip, not a security boundary: it reads the text of the command, so it does not see what hides in
scripts, variables it cannot resolve or deeper nesting. It never blocks because of its own failure (fail-open).

Refused (exit 2, the reason and a ready replacement go to stderr, which the model reads):
  rm -r/-R/--recursive (also `command rm`, `\\rm`, `/bin/rm`, `sudo rm`), git clean -f, git worktree remove --force,
  find ... -delete or -exec rm -r, `rmtree` in python -c, PowerShell Remove-Item -Recurse and rd/rmdir /s,
unless every target is under a temporary directory (/tmp, $TMPDIR, $XDG_RUNTIME_DIR, /run/user/<uid>, the session scratchpad;
$SKILLSYNC_GUARD_TMP_ROOTS, a list separated like PATH, replaces the first five),
under a regenerable directory of the project (node_modules, .venv, __pycache__, dist, build, plus --allow entries), or the
command carries a one-time exception that the user approved: `SKILLSYNC_GUARD_EXCEPT='<reason>' <command>`; every exception
is appended to <project>/.skillsync/hook-exceptions.jsonl (time, command, reason, agent).

Usage (set by `skillsync link`): guard-commands.py [--mode deny|off] [--allow <name-or-path> ...]; stdin = the hook JSON.
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
KEYWORDS = {'{', '}', '!', 'if', 'then', 'else', 'elif', 'do', 'while', 'until', 'fi', 'done', 'time'}
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh'}
SUBST = '\x01'  # placeholder of a command substitution inside a word
ASSIGN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')
GLOB = re.compile(r'[*?\[]')


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
            while words and words[0].startswith('-'):
                takes = words[0] in ('-u', '-g', '-C', '-h', '-p', '-U', '-r', '-t')
                words = words[2:] if takes else words[1:]
        elif name == 'env':
            words = rest
            while words and (words[0].startswith('-') or ASSIGN.match(words[0])):
                if ASSIGN.match(words[0]):
                    key, _, value = words[0].partition('=')
                    assigns.append((key, value))
                    words = words[1:]
                else:
                    words = words[2:] if words[0] in ('-u', '-C', '-S') else words[1:]
        elif name in ('nice', 'ionice'):
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-n', '-c', '-p') and len(words[0]) == 2 else words[1:]
        elif name == 'timeout':
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-s', '-k') else words[1:]
            words = words[1:]  # the duration
        elif name == 'stdbuf':
            words = rest
            while words and words[0].startswith('-'):
                words = words[1:]
        elif name == 'xargs':
            from_stdin = True
            words = rest
            while words and words[0].startswith('-'):
                words = words[2:] if words[0] in ('-I', '-n', '-P', '-d', '-L', '-s', '-E', '-a') else words[1:]
        elif name == 'flock':
            words = rest
            while words and words[0].startswith('-'):
                if words[0] == '-c' and len(words) > 2:
                    return ['sh', '-c', words[2]], assigns, from_stdin
                words = words[2:] if words[0] in ('-w', '-E', '--timeout', '--conflict-exit-code') else words[1:]
            words = words[1:]  # the lock file
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


class Guard:
    def __init__(self, cwd, scratchpad, allow, environ):
        self.cwd = os.path.realpath(cwd or os.getcwd())
        self.env = dict(environ)
        self.vars = {}
        self.safe = set()  # variables assigned from mktemp
        self.root = self.project_root(self.cwd)
        if self.env.get('SKILLSYNC_GUARD_TMP_ROOTS'):  # override of the default temporary roots (tests, unusual machines)
            roots = self.env['SKILLSYNC_GUARD_TMP_ROOTS'].split(os.pathsep) + [scratchpad]
        else:
            roots = ['/tmp', '/var/tmp', tempfile.gettempdir(), self.env.get('TMPDIR'), self.env.get('XDG_RUNTIME_DIR'), scratchpad]
            if hasattr(os, 'getuid'):
                roots.append('/run/user/%d' % os.getuid())
        self.tmp_roots = [os.path.realpath(r) for r in roots if r]
        self.names, self.paths = set(DEFAULT_NAMES), []
        for entry in allow:
            if os.path.isabs(entry):
                self.tmp_roots.append(os.path.realpath(entry))
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
                return os.path.join(tempfile.gettempdir(), 'mktemp-' + (inner[2] or '').lstrip('/'))
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
        word = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)', var, word)
        if '\x02' in word or '$' in word:
            return None
        return word

    def resolve(self, word, base=None):
        """Absolute normalised path of a target (glob tails cut), or None when unresolvable."""
        path = self.expand(word)
        if path is None:
            return None
        match = GLOB.search(path)
        if match:
            path = path[:match.start()]
            path = path if path.endswith('/') or not path else os.path.dirname(path) or '.'
        path = path or '.'
        if not os.path.isabs(path):
            path = os.path.join(base or self.cwd, path)
        return os.path.realpath(path)

    def allowed(self, path):
        if path is None:
            return False
        for root in self.tmp_roots:
            if path == root or path.startswith(root.rstrip('/') + '/'):
                return True
        if path == self.root or not path.startswith(self.root.rstrip('/') + '/'):
            return False
        relative = os.path.relpath(path, self.root).split(os.sep)
        if any(part in self.names for part in relative):
            return True
        joined = '/'.join(relative)
        return any(joined == p or joined.startswith(p + '/') for p in self.paths)

    def note_assignments(self, cmd, assigns):
        """Remember `NAME=value` of an assignment-only command (also `NAME=$(mktemp ...)`)."""
        for name, value in assigns:
            expanded = self.expand(value)
            if expanded is not None and SUBST not in value:
                self.vars[name] = expanded
            elif SUBST in value and value.split(SUBST)[1].split()[:1] == ['mktemp']:
                self.safe.add(name)
            else:
                self.vars.pop(name, None)
                self.safe.discard(name)

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
        if name in SHELLS and depth < MAX_DEPTH:
            for k, arg in enumerate(args):
                if arg.startswith('-') and not arg.startswith('--') and 'c' in arg[1:] and k + 1 < len(args):
                    return self.walk(args[k + 1], depth + 1, reason), reason
        if name == 'eval' and depth < MAX_DEPTH:
            return self.walk(' '.join(args), depth + 1, reason), reason
        if name == 'rm':
            shorts, longs, operands = _flags(args)
            if shorts & {'r', 'R'} or '--recursive' in longs:
                found.append(('rm', words, operands, from_stdin))
        elif name == 'git':
            found += self.git(words, args)
        elif name == 'find':
            found += self.find(words, args)
        elif name in ('python', 'python3', 'py') or re.fullmatch(r'python3(\.\d+)?', name):
            if '-c' in args and 'rmtree' in ' '.join(args):
                found.append(('rmtree', words, [], False))
        elif low in ('remove-item', 'ri'):
            if any(a.lower() in ('-recurse', '-r') for a in args):
                found.append(('powershell', words, [a for a in args if not a.startswith('-')], False))
        elif low in ('rd', 'rmdir'):
            if any(a.lower() == '/s' for a in args):
                found.append(('windows-rd', words, [a for a in args if not a.startswith('/')], False))
        # judge the targets: only what is NOT allowed is a finding
        bad = []
        for kind, fwords, targets, stdin in found:
            if kind in ('rmtree', 'powershell', 'windows-rd') or stdin:
                bad.append((kind, fwords, targets, stdin))
            elif targets and any(not self.allowed(self.resolve(t)) for t in targets):
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
        shorts, longs, operands = _flags(tail)
        if sub == 'clean' and ('f' in shorts or '--force' in longs) and not ('n' in shorts or '--dry-run' in longs):
            where = [base]
            return [('git-clean', words, where, False)]
        if sub == 'worktree' and tail[:1] == ['remove']:
            shorts, longs, operands = _flags(tail[1:])
            if 'f' in shorts or '--force' in longs:
                return [('worktree-force', words, [self.resolve(o, base) or o for o in operands], False)]
        return []

    def find(self, words, args):
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
        if deleting:
            return [('find-delete', words, roots or ['.'], False)]
        return []

    def walk(self, text, depth=0, inherited=None):
        """`[(kind, words, targets, from_stdin, exception reason or None)]`: the exception of a command covers only that command."""
        findings = []
        for cmd in parse(text):
            for sub in cmd.subs:
                if depth < MAX_DEPTH:
                    findings += self.walk(sub, depth + 1, inherited)
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
    shown = shlex.join(words) if all(isinstance(w, str) for w in words) else ' '.join(map(str, words))
    quoted = ' '.join(shlex.quote(t) for t in targets) if targets else '<sciezki>'
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


def log_exception(root, payload, command, reason, findings):
    path = os.path.join(root, '.skillsync', 'hook-exceptions.jsonl')
    record = {'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'command': command[:2000], 'reason': reason,
              'kind': [f[0] for f in findings], 'agent_id': payload.get('agent_id'), 'session_id': payload.get('session_id'),
              'cwd': payload.get('cwd')}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError as error:
        print('guard-commands: nie udalo sie zapisac sladu wyjatku: %s' % error, file=sys.stderr)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode, allow = 'deny', []
    i = 0
    while i < len(argv):
        if argv[i] == '--mode' and i + 1 < len(argv):
            mode = argv[i + 1]
            i += 2
        elif argv[i] == '--allow' and i + 1 < len(argv):
            allow.append(argv[i + 1])
            i += 2
        else:
            i += 1
    if mode != 'deny':
        return 0
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict) or payload.get('tool_name') != 'Bash':
            return 0
        tool_input = payload.get('tool_input') or {}
        command = tool_input.get('command') if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not command.strip():
            return 0
        guard = Guard(payload.get('cwd'), payload.get('scratchpad_dir'), allow, os.environ)
        findings = guard.walk(command)
    except Exception as error:  # fail-open: the hook's own failure never blocks the work
        print('guard-commands: blad hooka, komenda przepuszczona (%s: %s)' % (type(error).__name__, error), file=sys.stderr)
        return 0
    if not findings:
        return 0
    refused = [item for item in findings if not item[4]]
    if not refused:
        log_exception(guard.root, payload, command, '; '.join(dict.fromkeys(item[4] for item in findings)), findings)
        return 0
    print(message(refused, payload.get('agent_id')), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
