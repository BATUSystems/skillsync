"""Wspolne scalanie wpisu hooka Claude Code w ignorowanym `settings.local.json`.

Wpis (cala grupa {matcher, hooks}) buduje wolajacy; tu zostaje wylacznie scalanie: cudze hooki, uprawnienia
i inne klucze zostaja, blad pliku to [warn] (nigdy wyjatek), plik sledzony przez git nie jest zapisywany."""
import json
from pathlib import Path


def load_settings(settings_path: Path):
    """Zwraca (existing, ok); przy bledzie drukuje [warn] jak scalacze i zwraca (None, False)."""
    existing = {}
    try:
        if settings_path.exists():
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
    except ValueError:
        print(f"[warn] Nieprawidlowy JSON w {settings_path}, pomijam scalanie")
        return None, False
    except OSError as error:
        print(f"[warn] Nie udalo sie odczytac {settings_path}: {error}")
        return None, False
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        print(f"[warn] Invalid hook settings structure: {settings_path}")
        return None, False
    return existing, True


def peek_settings(settings_path: Path):
    """Cichy odczyt: slownik ustawien albo None (brak pliku, blad, zla struktura)."""
    try:
        existing = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (ValueError, OSError):
        return None
    if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
        return None
    return existing


def groups_valid(groups) -> bool:
    return isinstance(groups, list) and all(isinstance(group, dict) and isinstance(group.get("hooks", []), list) for group in groups)


def merge_entry(settings_path, event, entry, is_ours, want, dry_run, backup="overwrite", extra=None) -> bool:
    """Dopisuje (`want`) albo usuwa nasz wpis zdarzenia `event`; True, gdy po wywolaniu nie zostaje w pliku nasz wpis.

    `is_ours(hook)` rozpoznaje nasz hook (slownik); `backup`: `overwrite` nadpisuje kopie
    `settings.local.json.skillsync-backup`, `earliest` zachowuje najwczesniejsza. `extra` (slownik kluczy najwyzszego
    poziomu) jest stosowane tylko przy `want` w tym samym odczycie i zapisie; inna wartosc sama wymusza zapis."""
    from .cli import is_tracked, json_file_text, read_user_text, write_text_lf  # cli importuje ten modul

    settings_path = Path(settings_path)
    if not want and not settings_path.exists():
        return True
    existing, ok = load_settings(settings_path)
    if not ok:
        return False
    updated = dict(existing)
    hooks_section = dict(updated.get("hooks") or {})
    groups = hooks_section.get(event, [])
    if not groups_valid(groups):
        print(f"[warn] Invalid {event} settings structure: {settings_path}")
        return False
    present = [hook for group in groups for hook in group.get("hooks", []) if is_ours(hook)]
    extra = extra or {}
    if want and present == entry["hooks"] and all(key in existing and existing[key] == value for key, value in extra.items()):
        return False  # wpis (i dodatkowe klucze) aktualny; nie przestawiamy ani nie nadpisujemy pliku
    if want:
        for key, value in extra.items():
            updated[key] = json.loads(json.dumps(value))
    kept = []
    for group in groups:
        if "hooks" not in group:
            kept.append(group)
            continue
        remaining = [hook for hook in group["hooks"] if not is_ours(hook)]
        if remaining or not group["hooks"]:
            kept.append({**group, "hooks": remaining})
    if want:
        kept.append(entry)
    if kept:
        hooks_section[event] = kept
    else:
        hooks_section.pop(event, None)
    if hooks_section or "hooks" in existing:
        updated["hooks"] = hooks_section

    if updated == existing:
        return not want
    if settings_path.exists() and is_tracked(settings_path):
        print(f"[warn] {settings_path} jest śledzony przez git, pomijam zapis")
        return False
    if dry_run:
        print(f"[dry-run] Would update: {settings_path}")
        return not want
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if settings_path.exists():
            backup_path = settings_path.with_name(settings_path.name + ".skillsync-backup")
            if backup != "earliest" or not backup_path.exists():
                write_text_lf(backup_path, read_user_text(settings_path))
        write_text_lf(settings_path, json_file_text(updated, read_user_text(settings_path) if settings_path.exists() else ""))
    except OSError as error:
        print(f"[warn] Nie udalo sie zapisac {settings_path}: {error}")
        return False
    print(f"Updated: {settings_path}")
    return not want
