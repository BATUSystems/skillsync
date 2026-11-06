"""Validation of the optional `dispatch:` manifest section (consumed by the `dispatch` skill, not by the generator)."""
import math

KNOWN_KEYS = ('max_parallel', 'worktree_root', 'setup', 'isolated_resources', 'leased_resources',
              'owned_files', 'additive_files', 'id_registries', 'guards')
LEASE_KEYS = ('name', 'ports', 'databases', 'command')
GUARD_KEYS = ('min_free_disk_gb', 'max_load')


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _string_list(value, path, errors):
    if not isinstance(value, list):
        errors.append(f'{path}: must be a list of nonempty strings')
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f'{path}[{index}]: must be a nonempty string')


def validate_dispatch(section):
    """Return `(errors, warnings)` as lists of readable messages that start with the key path.

    A missing (or null) section is valid. The section only configures the `dispatch` skill, so
    nothing here changes generated artifacts.
    """
    errors, warnings = [], []
    if section is None:
        return errors, warnings
    if not isinstance(section, dict):
        return ['dispatch: must be a mapping'], warnings
    for key in section:
        if key not in KNOWN_KEYS:
            warnings.append(f'dispatch.{key}: unknown key (known: {", ".join(KNOWN_KEYS)})')
    if 'max_parallel' in section and (not _is_int(section['max_parallel']) or section['max_parallel'] < 1):
        errors.append('dispatch.max_parallel: must be an integer >= 1')
    if 'worktree_root' in section and (not isinstance(section['worktree_root'], str) or not section['worktree_root']):
        errors.append('dispatch.worktree_root: must be a nonempty string')
    for key in ('setup', 'owned_files', 'additive_files', 'id_registries'):
        if key in section:
            _string_list(section[key], f'dispatch.{key}', errors)
    if 'isolated_resources' in section:
        isolated = section['isolated_resources']
        if not isinstance(isolated, dict):
            errors.append('dispatch.isolated_resources: must be a mapping')
        else:
            for key, value in isolated.items():
                path = f'dispatch.isolated_resources.{key}'
                if key == 'env':
                    if not isinstance(value, dict):
                        errors.append(f'{path}: must be a mapping of string to string')
                        continue
                    for name, item in value.items():
                        if not isinstance(name, str) or not isinstance(item, str):
                            errors.append(f'{path}.{name}: environment names and values must be strings')
                elif not isinstance(value, (str, int)) or isinstance(value, bool):
                    errors.append(f'{path}: must be a string template (or integer)')
    if 'leased_resources' in section:
        leased = section['leased_resources']
        if not isinstance(leased, list):
            errors.append('dispatch.leased_resources: must be a list of mappings')
        else:
            names = set()
            for index, resource in enumerate(leased):
                path = f'dispatch.leased_resources[{index}]'
                if not isinstance(resource, dict):
                    errors.append(f'{path}: must be a mapping')
                    continue
                name = resource.get('name')
                if not isinstance(name, str) or not name:
                    errors.append(f'{path}.name: required nonempty string')
                elif name in names:
                    errors.append(f'{path}.name: duplicate resource name {name!r}')
                else:
                    names.add(name)
                for key in resource:
                    if key not in LEASE_KEYS:
                        warnings.append(f'{path}.{key}: unknown key (known: {", ".join(LEASE_KEYS)})')
                if 'ports' in resource:
                    ports = resource['ports']
                    if not isinstance(ports, list):
                        errors.append(f'{path}.ports: must be a list of integers')
                    else:
                        for port_index, port in enumerate(ports):
                            if not _is_int(port) or not 1 <= port <= 65535:
                                errors.append(f'{path}.ports[{port_index}]: must be an integer port 1-65535')
                if 'databases' in resource:
                    _string_list(resource['databases'], f'{path}.databases', errors)
                if 'command' in resource and (not isinstance(resource['command'], str) or not resource['command']):
                    errors.append(f'{path}.command: must be a nonempty string')
    if 'guards' in section:
        guards = section['guards']
        if not isinstance(guards, dict):
            errors.append('dispatch.guards: must be a mapping')
        else:
            for key, value in guards.items():
                if key not in GUARD_KEYS:
                    warnings.append(f'dispatch.guards.{key}: unknown key (known: {", ".join(GUARD_KEYS)})')
                elif not _is_number(value) or value < 0:
                    errors.append(f'dispatch.guards.{key}: must be a number >= 0')
    return errors, warnings
