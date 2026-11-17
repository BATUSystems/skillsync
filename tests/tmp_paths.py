"""Test helper: create directories and files under a temporary directory from a name that comes from data.

`base / name` is unsafe when `name` is data (a fixture key, a parsed title, a JSON value): on Windows a name such as `\\n`
(backslash, n) is a path from the drive root, `C:x` has a drive, `..` leaves the directory and `CON` is a device. The helper
refuses such names and returns only a path that `relative_to` the base confirms. Names that are literals written by the test
itself do not need it. `tests/test_skillsync_tmp_path_guard.py` reports `<tmp path> / <variable>` expressions outside this helper.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$', re.IGNORECASE)
FORBIDDEN = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class UnsafeName(ValueError):
    pass


def check_name(name):
    """The name when it is one safe path component on every system, else `UnsafeName`."""
    if not isinstance(name, str) or not name:
        raise UnsafeName(f'name must be a nonempty string: {name!r}')
    if FORBIDDEN.search(name):
        raise UnsafeName(f'name has a separator, a drive colon, a control or a reserved character: {name!r}')
    if name in ('.', '..') or name != name.rstrip(' .'):
        raise UnsafeName(f'name is a dot component or ends with a dot or space: {name!r}')
    if RESERVED.match(name.split('.')[0].rstrip(' ')):
        raise UnsafeName(f'name is a reserved Windows device name: {name!r}')
    for kind in (PureWindowsPath, PurePosixPath):
        parsed = kind(name)
        if parsed.drive or parsed.root or parsed.parts != (name,):
            raise UnsafeName(f'name is not a single relative component for {kind.__name__}: {name!r}')
    return name


def child(base, name):
    """`base / name` checked with `relative_to` on the resolved paths."""
    base = Path(base)
    path = base / check_name(name)
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise UnsafeName(f'{name!r} resolves outside {base}')
    return path


def descend(base, relative):
    """`base / relative` for a relative path written with `/` that comes from data (`docs/plans/x.md`): every component is checked like `child` does, an absolute
    path, a backslash, a drive or a `..` component is refused, and the result must stay under the base."""
    if not isinstance(relative, str) or not relative or relative.startswith('/') or '\\' in relative:
        raise UnsafeName(f'relative path must be a nonempty string with / and no leading / or backslash: {relative!r}')
    path = Path(base)
    for part in relative.split('/'):
        path = path / check_name(part)
    try:
        path.resolve().relative_to(Path(base).resolve())
    except ValueError:
        raise UnsafeName(f'{relative!r} resolves outside {base}')
    return path


def make_dir(base, name):
    path = child(base, name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(base, name, text='', encoding='utf-8'):
    path = child(base, name)
    path.write_text(text, encoding=encoding, newline='')
    return path
