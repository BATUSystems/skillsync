"""The `claude` key of the project manifest: one reader for every consumer (link, guard hook, doctor) and its validation.

Canonical semantics: Claude is off only for `claude: false` or `claude: {enabled: false}` (identity with False). Anything else is on,
including a non-bool `enabled` (doctor reports it as an error). `statusline` and `output_styles` are opt-in: only a boolean True switches them on,
and never when Claude is off."""

KNOWN_KEYS = ('enabled', 'statusline', 'output_styles')


def _section(manifest):
    claude = manifest.get('claude') if isinstance(manifest, dict) else None
    return claude if isinstance(claude, dict) else {}


def enabled(manifest) -> bool:
    claude = manifest.get('claude') if isinstance(manifest, dict) else None
    if claude is False:
        return False
    return not (isinstance(claude, dict) and claude.get('enabled') is False)


def _switch(manifest, key) -> bool:
    return enabled(manifest) and _section(manifest).get(key) is True


def statusline(manifest) -> bool:
    return _switch(manifest, 'statusline')


def output_styles(manifest) -> bool:
    return _switch(manifest, 'output_styles')


def findings(manifest):
    """`[(severity, message)]`; messages start with `claude.<key>` and never contain the value from the manifest."""
    claude = manifest.get('claude') if isinstance(manifest, dict) else None
    if claude is None or isinstance(claude, bool):
        return []
    if not isinstance(claude, dict):
        return [('warning', f'claude: expected a boolean or a mapping, got {type(claude).__name__}; Claude stays enabled')]
    found = []
    for key, value in claude.items():
        if key in KNOWN_KEYS:
            if not isinstance(value, bool):
                found.append(('error', f'claude.{key}: expected a boolean, got {type(value).__name__}'))
        else:
            found.append(('warning', f'claude.{key}: unknown key (known: {", ".join(KNOWN_KEYS)})'))
    return found
