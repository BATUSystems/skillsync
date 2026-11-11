"""Validation of the optional manifest key `workflow.ceremony` (`full` | `demo`).

The value is read only from the project manifest (never from `workflow/defaults.yaml` or
`workflow/projects/<slug>.yaml`), so this module takes the raw manifest and does not use
`workflow_config`. It only tells agents how much process ceremony to apply; it never changes
generated artifacts.
"""
VALUES = ('full', 'demo')
DEFAULT = 'full'
_MISSING = object()


def _raw_value(manifest):
    section = manifest.get('workflow') if isinstance(manifest, dict) else None
    if not isinstance(section, dict) or 'ceremony' not in section:
        return _MISSING
    return section['ceremony']


def validate_ceremony(manifest):
    """Return `(errors, warnings)` as lists of readable messages that start with the key path.

    A missing key is fine (default `full`); a present key must be exactly `full` or `demo`
    (lowercase). Messages never contain the offending value.
    """
    value = _raw_value(manifest)
    if value is _MISSING:
        return [], []
    if value is None:
        return ['workflow.ceremony: klucz bez wartości (użyj full albo demo)'], []
    if not isinstance(value, str) or value not in VALUES:
        return [f'workflow.ceremony: musi być jednym z: {", ".join(VALUES)} (małymi literami)'], []
    return [], []


def ceremony_info(manifest):
    """Return the `route` field `{value, source[, error]}` for a manifest.

    `source` is `manifest` (valid value), `default` (no key; value `full`) or `invalid` (`value` is
    `None` and `error` carries the message). The agent must ask when the source is `invalid`.
    """
    errors, _ = validate_ceremony(manifest)
    if errors:
        return {'value': None, 'source': 'invalid', 'error': errors[0]}
    value = _raw_value(manifest)
    if value is _MISSING:
        return {'value': DEFAULT, 'source': 'default'}
    return {'value': value, 'source': 'manifest'}
