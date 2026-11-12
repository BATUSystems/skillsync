"""Małe helpery systemu plików i strumieni bez zależności od reszty pakietu (bezpieczne do importu z każdego modułu)."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def ensure_utf8_streams() -> None:
    """Przełącza stdout i stderr na UTF-8, gdy mają inne kodowanie (stdout `errors='replace'`, stderr `backslashreplace`).

    Windows z przekierowanym wyjściem (rura, zadanie bez konsoli, CI) daje strumień w cp1252, a komunikaty po polsku
    (np. `\\u0144`) kończą się `UnicodeEncodeError`. Strumień już UTF-8, bez `reconfigure` albo niedostępny zostaje bez zmian.
    """
    for stream, errors in ((sys.stdout, 'replace'), (sys.stderr, 'backslashreplace')):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, 'encoding', None) or '').lower().replace('-', '').replace('_', '')
        if encoding == 'utf8':
            continue
        try:
            reconfigure(encoding='utf-8', errors=errors)
        except (OSError, ValueError):
            pass


# Operacje `rmtree`, których nie naprawi zmiana uprawnień: wynik jest ten sam co bez helpera (błąd wraca albo jest połknięty).
_NOT_FIXABLE = (os.path.islink, os.lstat, os.open, os.scandir, os.listdir)


def _inside(path, root) -> bool:
    try:
        return root is not None and os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:  # inne dyski (Windows)
        return False


def _retry_after_chmod(func, path, exc, ignore_errors: bool = False, root=None) -> None:
    """Callback `rmtree` (podpis `onerror` i `onexc`): zdejmuje ochronę zapisu i ponawia operację, która zawiodła.

    Windows odmawia usunięcia pliku z atrybutem tylko do odczytu (obiekty `.git`), a POSIX usunięcia wpisu z katalogu bez prawa
    zapisu. Poprawiamy tylko po `PermissionError`, nie idziemy za symlinkiem (jego cel zostaje nietknięty) i zmieniamy
    uprawnienia wyłącznie wewnątrz usuwanego drzewa `root` (katalog nadrzędny korzenia zostaje). Gdy ponowienie zawiedzie,
    błąd wraca (albo jest połknięty przy `ignore_errors`).
    """
    try:
        if func in _NOT_FIXABLE or not isinstance(exc, PermissionError):
            raise exc
        targets = []
        if os.path.lexists(path) and not os.path.islink(path) and (root is None or _inside(path, root)):
            targets.append(path)
        parent = os.path.dirname(path)
        if parent and root is not None and os.path.abspath(path) != os.path.abspath(root) and _inside(parent, root):
            targets.append(parent)
        for target in targets or [None]:
            if target is not None:
                try:
                    mode = os.stat(target).st_mode
                    os.chmod(target, mode | stat.S_IWRITE | (stat.S_IEXEC if stat.S_ISDIR(mode) else 0))
                except OSError:
                    pass
            try:
                func(path)
                return
            except OSError:
                continue
        func(path)
    except OSError:
        if not ignore_errors:
            raise


def rmtree_force(path, ignore_errors: bool = False) -> None:
    """`shutil.rmtree`, który usuwa też pliki i katalogi tylko do odczytu (klon git na Windows).

    `ignore_errors=True` zachowuje dawny kontrakt (brak wyjątków, także dla nieistniejącej ścieżki), ale najpierw próbuje
    wymusić usunięcie; bez niego błąd, którego nie da się obejść, jest zgłaszany jak w `shutil.rmtree`.
    """
    root = Path(path)

    def handler(func, failed, exc):
        _retry_after_chmod(func, failed, exc, ignore_errors, root)

    if sys.version_info >= (3, 12):
        shutil.rmtree(root, onexc=handler)
    else:
        shutil.rmtree(root, onerror=lambda func, failed, info: handler(func, failed, info[1]))
