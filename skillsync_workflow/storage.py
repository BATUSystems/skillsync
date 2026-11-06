"""Private, confined state for optional workflow services."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def state_path(root, relative, create=False):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('Unsafe workflow state path')
    current = root
    parts = ('.skillsync',) + relative.parts
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink():
            raise ValueError('Workflow state symlinks are forbidden')
        if index < len(parts) - 1:
            if create:
                current.mkdir(exist_ok=True, mode=0o700)
            if current.exists() and not current.is_dir():
                raise ValueError('Workflow state parent must be a directory')
    if not current.resolve().is_relative_to(root):
        raise ValueError('Workflow state escapes project')
    return current


def read_json(path, default=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Workflow state symlinks are forbidden')
    if not path.exists():
        return default
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Invalid or oversized workflow JSON')
    with path.open(encoding='utf-8') as handle:
        return json.load(handle)


def write_json(path, data):
    path = Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError('Unsafe workflow state storage')
    fd, temporary = tempfile.mkstemp(prefix='.skillsync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        if path.is_symlink():
            raise ValueError('Workflow state symlinks are forbidden')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
