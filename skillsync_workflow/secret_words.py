"""THE definition of what makes a name or a piece of text a secret, for every mask and secret scan of the generator (`common.redact`, report keys,
the flags of a recorded command, the secret scans of `feedback` and `prototype`, the URL parameters in the messages of `layers`).

A leaf module (it imports nothing of the package), so that `layers` and `common` can both use it. Everything here is a HEURISTIC.
"""
from __future__ import annotations

import re

# Words that make a name secret. `pat` and `pwd` count only as a whole part of a name (`GITHUB_PAT`, `db-pwd`, `githubPat`), not as a piece of
# `PATH`, `compat`, `xPath` or `--pattern`; `PWD` itself is the working directory.
SECRET_WORDS = r'token|secret|password|passwd|credential'
SHORT_SECRET_WORDS = r'(?:pat|pwd)'
KEY_WORDS = r'api[-_]?key|access[-_]?key|private[-_]?key|secret[-_]?key'   # names of keys that are secrets although none of the words is in them
ENVIRONMENT_NAME = re.compile(rf'(?i){SECRET_WORDS}|api_?key|(?:^|_){SHORT_SECRET_WORDS}(?:_|$)')
_REPORT_NAME = re.compile(rf'(?i){SECRET_WORDS}|api[_-]?key|authorization|(?:^|[_. -]){SHORT_SECRET_WORDS}(?:$|[_. -])')
# a piece of the name of a command line flag; wider on purpose (`--pass`, `--auth`, `--key`): the value is only the word after the flag
FLAG_WORD = rf'(?:{SECRET_WORDS}|key|pass|auth|(?<=[_.-]){SHORT_SECRET_WORDS}(?![A-Za-z0-9])|(?-i:(?:(?<=[a-z0-9])(?:Pat|PAT|Pwd|PWD)|(?<=[A-Z])(?:Pat|Pwd))(?![a-z])))'
_QUERY_ONLY = frozenset({'pass', 'pwd', 'auth', 'key', 'sig', 'signature'})   # parameters of a URL that carry a secret under a name too common elsewhere
_CAMEL_PART = re.compile(r'(?:(?<=[a-z0-9])(?=(?:Pat|PAT|Pwd|PWD)(?![a-z]))|(?<=[A-Z])(?=(?:Pat|Pwd)(?![a-z])))')
WORKDIR_NAMES = frozenset({'PWD', 'OLDPWD', 'OLD_PWD', 'INIT_PWD'})   # variables of shells and of npm that hold a working directory
_PATH_VALUE = re.compile(r'[\x22\x27]?(?:[/~\\]|[A-Za-z]:[\\/]|\$(?:[({]|PWD\b)|\.{1,2}(?:[\s/\\\x22\x27]|$))')
_CODE_START = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*([(\[])')   # a call or an item of a name
_SPACED_EQUALS = re.compile(r'[ \t]=[ \t]+$')
_WORD = re.compile(r'[^\s,;]*')
_NEXT_KEY = re.compile(r'([A-Za-z_][\w-]*):(?=\s|$)')


def name_parts(name):
    """`name` with the camelCase parts `Pat` and `Pwd` set apart (`githubPat` -> `github_Pat`), so that they count as parts of the name."""
    return _CAMEL_PART.sub('_', str(name))


def environment_name(name):
    """True when the environment variable `name` says that it holds a secret."""
    return bool(ENVIRONMENT_NAME.search(name_parts(name)))


def secret_key_name(name):
    """True when the value under the key `name` of a report or of operation parameters is a secret."""
    name = str(name)
    return name != 'PWD' and bool(_REPORT_NAME.search(name_parts(name)))


def secret_query_name(name):
    """True when the value of the parameter `name` of a URL is a secret."""
    return secret_key_name(name) or name.lower() in _QUERY_ONLY or bool(re.search(rf'(?i){KEY_WORDS}', name))


def is_path(value):
    """True when `value` (possibly quoted) reads as a path or a reference to the shell: `/x`, `~`, `C:\\x`, `.`, `./x`, `$(pwd)`."""
    return bool(_PATH_VALUE.match(value))


def harmless_assignment(name, delimiter, text, position):
    """True for the two readings of `name <delimiter> value` (the value begins at `position` of `text`) that look like a secret and are none:
    an old working directory (`OLD_PWD=/path`), and code that assigns a call or an item to a name (`url_pat = re.compile(...)`: spaces around `=`,
    nothing quoted, the word ends with the closing bracket, or with the opening one of a dotted name when the call goes on in the next line)."""
    if name in WORKDIR_NAMES and _PATH_VALUE.match(text, position):
        return True
    if not _SPACED_EQUALS.search(delimiter) or not _CODE_START.match(text, position):
        return False
    word = _WORD.match(text, position).group(0)   # read once for each space before it: linear
    code = _CODE_START.match(word)
    if code is None:
        return False
    if code.end() == len(word):
        return '.' in word
    return word.endswith({'(': ')', '[': ']'}[code.group(1)])


# Quoted: the whole quoted text up to the first closing quote that no backslash escapes (an unterminated quote takes the rest of the line, also one
# that ends in a backslash); unquoted: a word up to a space, `,` or `;`. The quoted alternatives start every part with a different character or end
# the line, so the match is linear (no catastrophic backtracking).
VALUE_PATTERN = (r'(?:\x22(?:\\[^\r\n]|[^\x22\\\r\n])*(?:\x22|\\?(?=[\r\n]|$))|\x27(?:\\[^\r\n]|[^\x27\\\r\n])*(?:\x27|\\?(?=[\r\n]|$))'
                 r'|[^\s\x22\x27,;][^\s,;]*)')
# The short keys `pat` and `pwd`: the key and the delimiter (the value, `VALUE_PATTERN`, follows on the same line; a lone `{` or `[` opens a
# structure and is no value).
#   alone (`pat=`, `--pwd=`, `;Pwd=`): only before `=` or as a quoted key (`"pat":`), because `pwd: ...` and `PAT: ...` are ordinary prose;
#     `PWD` (upper case) before a path, `.` or `$(` / `${` is the working directory, `$PWD` a reference to it;
#   at the end of a longer name (`DB_PWD`, `azure-pat`, `githubPat`, `ADOPat`): also before `:`;
#   followed by more parts, in upper case (`GITHUB_PAT_RO`, `DB_PWD_PROD`) or after camelCase (`githubPat_ro`): also before `:`; not when a part
#     says that the value is a place (`_PATH`, `_DIR`, `_FILE`, `_HOME`, `_URL`). A further part with `pat` / `pwd` is matched at its own place
#     (which also keeps the search linear).
# The pattern begins with the letters of the key and looks behind them afterwards: the engine then jumps from `p` to `p` and a large document costs little.
_COMPOUND_END = r'[\x22\x27]?[ \t]*(?::|=(?!=))'
_PLACE = r'(?:PATH|DIR|FILE|HOME|URL)(?![A-Za-z0-9])'
_SHORT_KEY = re.compile(
    r'(?m)[Pp](?:[Aa][Tt]|[Ww][Dd])'
    r'(?:(?<=P(?:AT|WD))(?<![A-Za-z0-9]...)(?:_(?!(?:PAT|PWD)(?![A-Z0-9])|' + _PLACE + r')[A-Z0-9]+)+' + _COMPOUND_END +
    r'|(?:(?<=[a-z0-9]P(?:AT|WD|at|wd))|(?<=[A-Z]P(?:at|wd)))(?:_(?![A-Za-z0-9]*(?i:pat|pwd)(?![A-Za-z0-9])|(?i:' + _PLACE + r'))[A-Za-z0-9]+)*' + _COMPOUND_END +
    r'|(?<=[A-Za-z0-9][_-]...)' + _COMPOUND_END +
    r'|(?<![A-Za-z0-9_$]...)(?!(?<=PWD)[\x22\x27]?[ \t]*[=:][ \t]*[\x22\x27]?(?:[/~\\]|[A-Za-z]:[\\/]|\$[({]|\.(?:[\s/\x22\x27]|$)))'
    r'(?:[\x22\x27][ \t]*[=:]|[ \t]*=(?!=)))[ \t]*(?![{\[](?:[\x22\x27\s]|$))(?=[^\s,;])')
_VALUE = re.compile(VALUE_PATTERN)


def short_pairs(text):
    """`(start, value_start, end)` of every `pat` / `pwd` pair of `text` that hides a secret. Skipped: `harmless_assignment`, the flag
    `--pwd <path>`, and a "value" that is the next secret key (`githubPat: DB_PWD: x`). The text after the delimiter of a skipped one is
    searched again, so a secret inside it is still found. A skipped pair costs no walk through its value: the whole search stays linear."""
    position = 0
    while True:
        found = _SHORT_KEY.search(text, position)
        if found is None:
            return
        begin = found.end()
        position = begin
        start = found.start()
        while start and (text[start - 1].isalnum() or text[start - 1] in '_-'):
            start -= 1
        name = text[start:found.start() + 3]
        head = found.group(0)
        # the next key can only stand where a value is expected after `:` or after a space; `PWD=Word:` is a password that ends with a colon
        following = _NEXT_KEY.match(text, begin) if head.rstrip().endswith(':') or head != head.rstrip() else None
        if ((following and secret_key_name(following.group(1))) or harmless_assignment(name, head, text, begin)
                or (name == '--pwd' and _PATH_VALUE.match(text, begin))):
            continue
        value = _VALUE.match(text, begin)
        if value is None:
            continue
        position = value.end()
        yield found.start(), begin, value.end()


def has_short_pair(text, minimum):
    """True when `text` holds a `pat` / `pwd` pair whose value (without its quotes) has at least `minimum` characters."""
    return any(len(text[begin:end].strip('\x22\x27')) >= minimum for _start, begin, end in short_pairs(text))


# --- searches that stay linear on a very long word (the input of the scans comes from the network): a plain regex that begins with `*` before the
# literal it needs tries every start of the word and walks to its end from each of them. Here the literal (or the run of the name) is found
# first, once, and what stands before it is checked within that one run.

_SCHEME_CHAR = re.compile(r'(?i)[a-z0-9+.-]')
_LETTER = re.compile(r'(?i)[a-z]')
_LETTER_AT_BOUNDARY = re.compile(r'(?i)\b[a-z]')
_USERINFO = re.compile(r'://[^/\s]*@')              # up to the last `@` of the authority
_USER_PASSWORD = re.compile(r'://[^/\s:@]+:[^/\s@]+@')
_JWT_TAIL = re.compile(r'\.(?=[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5})')
_JWT_CHAR = re.compile(r'[A-Za-z0-9_-]')
_JWT_HEAD = re.compile(r'\beyJ')
_BOUNDARY = re.compile(r'\b')


def _run_start(text, end, character):
    """Where the run of characters of `character` that ends at `end` begins."""
    start = end
    while start and character.match(text, start - 1):
        start -= 1
    return start


def _after_scheme(text, found, letter):
    return letter.search(text, _run_start(text, found.start(), _SCHEME_CHAR), found.start()) is not None


def mask_url_userinfo(text, mask='***'):
    """`scheme://user:secret@host` -> `scheme://***@host`."""
    pieces, done = [], 0
    for found in _USERINFO.finditer(text):
        if _after_scheme(text, found, _LETTER_AT_BOUNDARY):
            pieces += [text[done:found.start() + 3], mask]
            done = found.end() - 1
    return ''.join(pieces) + text[done:] if pieces else text


def has_url_password(text):
    """True when `text` holds `scheme://user:password@`."""
    return any(_after_scheme(text, found, _LETTER) for found in _USER_PASSWORD.finditer(text))


_PRIVATE_KEY = re.compile(r'-----BEGIN ([A-Z ]*)(?=-----)')


def has_private_key(text):
    """True when `text` holds `-----BEGIN ... PRIVATE KEY...-----`."""
    return any('PRIVATE KEY' in found.group(1) for found in _PRIVATE_KEY.finditer(text))


def has_jwt(text):
    """True when `text` holds a token of the shape `eyJ<10+>.<10+>.<5+>` that begins at a word boundary."""
    return any(_JWT_HEAD.search(text, _run_start(text, found.start(), _JWT_CHAR), max(0, found.start() - 10)) is not None
               for found in _JWT_TAIL.finditer(text))


def named_values(text, run, word, tail, limit=None):
    """`(name_start, tail_match)` for every run of `run` (the characters of a name) that holds `word` and is followed by `tail` (matched at the end
    of the run). `limit`: at most that many characters of the name before the word (counted from a word boundary) and after it."""
    for name in run.finditer(text):
        start, end = name.span()
        followed = tail.match(text, end)
        if followed is None:
            continue
        if limit is None:
            if word.search(text, start, end):
                yield start, followed
            continue
        position = max(start, end - limit - 40)   # a word further from the end cannot have `limit` characters or fewer after it
        while True:
            found = word.search(text, position, end)
            if found is None:
                break
            position = found.start() + 1
            if end - found.end() > limit:
                continue
            boundary = _BOUNDARY.search(text, max(start, found.start() - limit))
            if boundary is not None and boundary.start() <= found.start():
                yield boundary.start(), followed
                break
