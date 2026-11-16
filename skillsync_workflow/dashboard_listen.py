"""The `dashboard:` setting (machine config and project manifest) that narrows or sets where `dashboard serve` listens.

Keys: `local_only` (true: both sockets on 127.0.0.1), `read_bind`, `approve_bind` (a literal address, `localhost`, `0.0.0.0`
or `::`). Precedence of the sources is flag > manifest > machine config > default (`dashboard_ports.resolve`); within one
source an explicit `read_bind`/`approve_bind` wins over the `local_only` shortcut. `local_only: false` means "no preference"
(it neither widens nor overrides a narrower lower source). Messages name the key, never the offending value.
"""
import ipaddress

import yaml

from . import layers

KNOWN_KEYS = ('local_only', 'read_bind', 'approve_bind')
LOOPBACK_BIND = '127.0.0.1'


def _bind_problem(value):
    if not isinstance(value, str) or not value.strip():
        return 'must be a nonempty address (a literal IP, localhost, 0.0.0.0 or ::)'
    text = value.strip()
    if text == 'localhost':
        return None
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return 'must be a literal IP address, localhost, 0.0.0.0 or ::'
    return None


def validate_dashboard(section, where='dashboard'):
    """`(errors, warnings)` for a `dashboard:` section (None = absent = fine)."""
    errors, warnings = [], []
    if section is None:
        return errors, warnings
    if not isinstance(section, dict):
        return [f'{where}: must be a mapping'], warnings
    for key in section:
        if key not in KNOWN_KEYS:
            warnings.append(f'{where}.{key}: unknown key (known: {", ".join(KNOWN_KEYS)})')
    if 'local_only' in section and not isinstance(section['local_only'], bool):
        errors.append(f'{where}.local_only: must be true or false')
    for key in ('read_bind', 'approve_bind'):
        if key in section:
            problem = _bind_problem(section[key])
            if problem:
                errors.append(f'{where}.{key}: {problem}')
    return errors, warnings


def _binds(section, where):
    """`(read_bind, approve_bind)` of one source; None = this source has no preference. Raises ValueError (key names only)."""
    errors, _warnings = validate_dashboard(section, where)
    if errors:
        raise ValueError('; '.join(errors))
    if not section:
        return None, None
    shortcut = LOOPBACK_BIND if section.get('local_only') is True else None
    read = section['read_bind'].strip() if 'read_bind' in section else shortcut
    approve = section['approve_bind'].strip() if 'approve_bind' in section else shortcut
    return read, approve


def machine_section():
    """The `dashboard:` section of the machine config (missing file or section: None). An unreadable file is no setting."""
    try:
        data = yaml.safe_load(layers.config_path().read_text(encoding='utf-8')) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    section = data.get('dashboard') if isinstance(data, dict) else None
    return section


def settings(manifest, machine=None):
    """`{read_bind, approve_bind}` (None = default) from the manifest over the machine config. Raises ValueError for a bad key or value."""
    machine = machine_section() if machine is None else machine
    project = manifest.get('dashboard') if isinstance(manifest, dict) else None
    machine_read, machine_approve = _binds(machine, 'dashboard (machine config)')
    project_read, project_approve = _binds(project, 'dashboard (manifest)')
    return {'read_bind': project_read or machine_read, 'approve_bind': project_approve or machine_approve}


def blocking_unknown_keys(manifest, machine=None):
    """A message naming the first unknown key of either source (`serve` is BLOCKED by it, as `doctor` warns), else None."""
    for where, section in (('dashboard (machine config)', machine_section() if machine is None else machine),
                           ('dashboard (manifest)', manifest.get('dashboard') if isinstance(manifest, dict) else None)):
        _errors, warnings = validate_dashboard(section, where)
        if warnings:
            return warnings[0]
    return None
