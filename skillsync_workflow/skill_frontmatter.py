"""Lint of the frontmatter of a `SKILL.md`: known keys of the hosts (Claude Code, Copilot), their types and enumerated values.

`check(metadata)` is a pure and total function: it never raises, whatever YAML produced (not a mapping, `None`, a key that is not text such as `1:`
or `yes:`). The caller decides the level: `doctor` reports every remark as a warning (the skills of the organization and personal layers are not ours),
the test of the core library accepts none. Claude Code ignores unknown keys without a word, so the lint is the only place where a typo shows.

Sources (checked 2026-11-21): https://code.claude.com/docs/en/skills, "Frontmatter reference" (Claude Code); `user-invokable` is the key of Copilot;
`applyTo` is used by two skills of the library and its host is not established (it is accepted silently)."""

UNKNOWN_KEY = 'unknown-key'
BAD_TYPE = 'bad-type'
BAD_VALUE = 'bad-value'

STRING, BOOLEAN, STRING_OR_LIST, MAPPING = 'string', 'boolean', 'string or list of strings', 'mapping'
MAX_COMPATIBILITY = 500
MAX_SHOWN = 60

# key -> (type, allowed values or None)
CLAUDE_CODE = {
    'name': (STRING, None), 'description': (STRING, None), 'when_to_use': (STRING, None), 'argument-hint': (STRING, None),
    'arguments': (STRING_OR_LIST, None), 'disable-model-invocation': (BOOLEAN, None), 'user-invocable': (BOOLEAN, None),
    'allowed-tools': (STRING_OR_LIST, None), 'disallowed-tools': (STRING_OR_LIST, None), 'model': (STRING, None),
    'effort': (STRING, ('low', 'medium', 'high', 'xhigh', 'max')), 'context': (STRING, ('fork',)), 'agent': (STRING, None), 'background': (BOOLEAN, None),
    'hooks': (MAPPING, None), 'paths': (STRING_OR_LIST, None), 'shell': (STRING, ('bash', 'powershell')), 'metadata': (MAPPING, None),
    'license': (STRING, None), 'compatibility': (STRING, None),
}
COPILOT = {'user-invokable': (BOOLEAN, None)}   # Claude Code does not know this spelling and ignores it: the skill stays invocable by the user
UNKNOWN_HOST = {'applyTo': (STRING, None)}
KNOWN = {**CLAUDE_CODE, **COPILOT, **UNKNOWN_HOST}


def _shown(value):
    text = str(value)
    return text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN] + '...'


def _is_boolean(value):
    return isinstance(value, bool) or (isinstance(value, int) and value in (0, 1))   # YAML: true/false, yes/no, on/off are bool; 1/0 are int


def _type_ok(kind, value):
    if kind == STRING:
        return isinstance(value, str)
    if kind == BOOLEAN:
        return _is_boolean(value)
    if kind == STRING_OR_LIST:
        return isinstance(value, str) or (isinstance(value, list) and all(isinstance(item, str) for item in value))
    if kind == MAPPING:
        return isinstance(value, dict)
    return False


def check(metadata):
    """The remarks on a parsed frontmatter, `[(class, reason)]`; the class is `unknown-key`, `bad-type` or `bad-value`; an empty list means clean."""
    if not isinstance(metadata, dict):
        return []   # a frontmatter that is not a mapping is the error of `doctor` ("Missing name/description"), not a remark of the lint
    remarks = []
    for key, value in metadata.items():
        shown = _shown(key)
        if not isinstance(key, str) or key not in KNOWN:
            remarks.append((UNKNOWN_KEY, f"Unknown frontmatter key '{shown}' (Claude Code ignores it without a word)"))
            continue
        kind, allowed = KNOWN[key]
        if not _type_ok(kind, value):
            remarks.append((BAD_TYPE, f"Frontmatter key '{key}' must be {kind}, not {type(value).__name__}"))
        elif allowed is not None and value not in allowed:
            remarks.append((BAD_VALUE, f"Frontmatter key '{key}' has an unsupported value '{_shown(value)}' (expected one of: {', '.join(allowed)})"))
        elif key == 'compatibility' and len(value) > MAX_COMPATIBILITY:
            remarks.append((BAD_VALUE, f"Frontmatter key 'compatibility' is longer than {MAX_COMPATIBILITY} characters"))
    return remarks
