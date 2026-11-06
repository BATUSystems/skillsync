"""Validation of the optional `delivery:` manifest section (delivery mode `pr` | `direct`).

Not to be confused with `delivery.py`, which handles `workflow.delivery` (provider and CI). The
section only tells agents how to deliver work; it never changes generated artifacts.
"""
MODES = ('pr', 'direct')
KNOWN_KEYS = ('mode', 'base')
_FORBIDDEN_REF_CHARS = set('~^:?*[\\')


def _base_problem(base):
    """Return a reason (never the value itself) why `base` is not a usable git branch name, else None."""
    if not isinstance(base, str):
        return 'must be a string'
    if not base.strip():
        return 'must be a nonempty string'
    if base.startswith('-'):
        return 'must not start with "-"'
    if '..' in base or '@{' in base:
        return 'must not contain ".." or "@{"'
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch in _FORBIDDEN_REF_CHARS for ch in base):
        return 'contains a character not allowed in a git ref name'
    if base == '@' or base.startswith('/') or base.endswith('/') or base.endswith('.'):
        return 'is not a valid git ref name'
    for part in base.split('/'):
        if not part or part.startswith('.') or part.endswith('.lock') or part.endswith('.'):
            return 'is not a valid git ref name'
    return None


def validate_delivery_mode(manifest):
    """Return `(errors, warnings)` as lists of readable messages that start with the key path.

    Takes the whole manifest: a missing `delivery` key is a warning (the default is `pr`), while a
    present key without a value (`delivery:` or `mode:`) is an error. Messages never contain the
    offending value.
    """
    errors, warnings = [], []
    manifest = manifest if isinstance(manifest, dict) else {}
    if 'delivery' not in manifest:
        return errors, ['delivery.mode: brak, domyślnie pr']
    section = manifest['delivery']
    if not isinstance(section, dict):
        return ['delivery: must be a mapping'], warnings
    for key in section:
        if key not in KNOWN_KEYS:
            warnings.append(f'delivery.{key}: unknown key (known: {", ".join(KNOWN_KEYS)})')
    mode_valid = False
    if 'mode' not in section:
        warnings.append('delivery.mode: brak, domyślnie pr')
    elif section['mode'] is None:
        errors.append('delivery.mode: key present without a value (use pr or direct)')
    elif not isinstance(section['mode'], str) or section['mode'] not in MODES:
        errors.append(f'delivery.mode: must be one of {", ".join(MODES)}')
    else:
        mode_valid = True
    if 'base' in section:
        problem = _base_problem(section['base'])
        if problem:
            errors.append(f'delivery.base: {problem}')
    elif mode_valid and section['mode'] == 'direct':
        warnings.append('delivery.base: brak przy mode direct (CP zatrzyma się, agent zapyta o gałąź bazową)')
    return errors, warnings


def delivery_info(manifest):
    """Return the `route` field `{mode, base, source[, error]}` for a manifest.

    `source` is `manifest` (valid `mode`), `default` (no `delivery.mode` key; mode `pr`) or `invalid`
    (any validation error: `mode` and `base` are `None` and `error` carries the first message, which
    never contains values). The agent must ask when the source is `invalid`.
    """
    errors, _ = validate_delivery_mode(manifest)
    if errors:
        return {'mode': None, 'base': None, 'source': 'invalid', 'error': errors[0]}
    section = manifest.get('delivery') if isinstance(manifest, dict) else None
    section = section if isinstance(section, dict) else {}
    return {'mode': section.get('mode', 'pr'), 'base': section.get('base'),
            'source': 'manifest' if 'mode' in section else 'default'}
