"""Małe helpery systemu plików i strumieni bez zależności od reszty pakietu (bezpieczne do importu z każdego modułu)."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path


def ensure_utf8_streams() -> None:
    """Przełącza stdout i stderr na UTF-8 (`errors='replace'`), gdy mają inne kodowanie.

    Windows z przekierowanym wyjściem (rura, zadanie bez konsoli, CI) daje strumień w cp1252, a komunikaty po polsku
    (np. `\\u0144`) kończą się `UnicodeEncodeError`. Strumień już UTF-8, bez `reconfigure` albo niedostępny zostaje bez zmian.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, 'encoding', None) or '').lower().replace('-', '').replace('_', '')
        if encoding == 'utf8':
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (OSError, ValueError):
            pass


def _retry_after_chmod(func, path, _exc, ignore_errors: bool = False) -> None:
    """Callback `rmtree` (podpis `onerror` i `onexc`): zdejmuje ochronę zapisu i ponawia operację, która zawiodła.

    Windows odmawia usunięcia pliku z atrybutem tylko do odczytu (obiekty `.git`), a POSIX usunięcia wpisu z katalogu bez prawa
    zapisu; poprawiamy oba i próbujemy raz jeszcze. Gdy ponowienie też zawiedzie, błąd wraca (albo jest połknięty przy `ignore_errors`).
    """
    try:
        for target in (path, os.path.dirname(path)):
            try:
                mode = os.stat(target, follow_symlinks=False).st_mode
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
    def handler(func, failed, _exc):
        _retry_after_chmod(func, failed, _exc, ignore_errors)

    if sys.version_info >= (3, 12):
        shutil.rmtree(Path(path), onexc=handler)
    else:
        shutil.rmtree(Path(path), onerror=handler)
