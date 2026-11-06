"""Raport retrospektywy tylko do odczytu z lokalnych checkpointów (`skillsync retro`): klasy, rubryka, sygnały raportów.

Nic nie jest zapisywane. Wynik powstaje z listy dozwolonych pól; wolny tekst rekordów (`cause`, `next`,
`project`), treść raportów i nazwy plików nie trafiają do wyjścia. Pola identyfikacyjne przechodzą najpierw
przez filtr wzorców (`_mask`, `_mask_path`)."""

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

import yaml

from .common import (add_project_arguments, discover_excludes, emit, is_excluded, load_layer_project, read_yaml, redact,
                     require_layerset)
from . import layers as _layers
from .runs import (OWNERS, RETRO_QUALITY_SINCE, retrospective, _classify_run, _extra_fields, _hash_file, _inside, _is_problem, _owner_dir, _read,
                   _run_id, _scan_dirs, _slug)

MAX_REPORT_FILES = 5000
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_MARKDOWN_BYTES = 1024 * 1024
SIGNALS = ("PASS", "FAIL", "BLOCKED", "NOT_RUN", "CHANGES_REQUIRED", "DONE")
RUN_FIELDS = ("id", "class", "steps", "repeated_steps", "failed_records", "not_run_records", "final_status")
CRITERIA = ("evidence", "repeats", "denied", "inherited", "plan_coverage")

# Wiersz kandydat (tani prefiltr na całym tekście) i dokładny wzorzec sygnału.
_STATUS_LINE = re.compile(r"^[^\S\n]*(?:\*\*)?(?:Status|Werdykt)(?:\*\*)?:[^\n]*", re.MULTILINE)
_SIGNAL = re.compile(r"^\s*(\*\*)?(Status|Werdykt)(\*\*)?:(\*\*)?\s*(\*\*)?"
                     r"(PASS|FAIL|BLOCKED|NOT_RUN|CHANGES_REQUIRED|DONE)\b")
_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:sk-ant-|sk-proj-|ghp_|gho_|github_pat_|glpat-|xox[abprs]-)[A-Za-z0-9_-]+"
                    r"|AKIA[0-9A-Z]{16}")
_LONG = re.compile(r"[A-Za-z0-9]{32,}")

NOT_DETECTED = ("Klasa wynika wyłącznie z rekordów; zdarzenia niezapisane jako checkpoint, niezapisane porażki "
                "i zgubione pliki paczek są niewidoczne.")
NOTES = "Wyłącznie zapisane pomiary; narzędzie nie ustala przyczyn."


def _mask(value):
    """Filtr wzorców tokenów po segmentach `/`; poprawne długie identyfikatory zostają."""
    segments = value.split("/")
    for index, segment in enumerate(segments):
        # Wyjątek: dokładnie 32 znaki hex bezpośrednio pod `.skillsync/logs/` (katalog uuid4.hex).
        if index >= 2 and segments[index - 2:index] == [".skillsync", "logs"] and re.fullmatch(r"[0-9a-f]{32}", segment):
            continue
        segment = _TOKEN.sub("[REDACTED]", segment)
        segments[index] = _LONG.sub("[REDACTED]", segment)
    return "/".join(segments)


def _opaque(value):
    return "invalid-name:" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def _mask_path(path):
    """Ścieżka dowodu pochodzi od operatora: segment spoza `[A-Za-z0-9._-]{1,80}` lub ścieżka > 240 znaków
    zamieniane są na skrót, a reszta przechodzi przez filtr tokenów."""
    if len(path) > 240:
        return _opaque(path)
    segments = [segment if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", segment) else _opaque(segment)
                for segment in path.split("/")]
    return _mask("/".join(segments))


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason}


def _probe(root, rel):
    """Stan pliku dowodu bez otwierania dowiązań, FIFO i katalogów: (stan, skrót).

    `_inside` rzuca ten sam ValueError w kilku przypadkach, więc kontrole są wykonywane tu, a `_inside` zostaje
    ostatnią zaporą przed wyjściem poza projekt."""
    parts = Path(rel).parts
    if not parts:
        return "missing", None
    path = root
    for part in parts:
        path = path / part
        try:
            mode = path.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            return "missing", None
        except ValueError:  # NUL w ścieżce, także UnicodeError (pojedynczy surogat)
            return "refused", None
        except OSError:
            return "unreadable", None
        # `checkpoint` zapisuje ścieżkę rozwiązaną, więc dowiązanie na trasie znaczy zmianę drzewa.
        if stat.S_ISLNK(mode):
            return "refused", None
    if not stat.S_ISREG(mode):
        return "missing", None
    try:
        _inside(root, rel, existing=False)
    except ValueError:
        return "refused", None
    try:
        return "hashed", _hash_file(path)
    except OSError:
        return "unreadable", None


def _evidence(records, root, cache):
    passes = [record for record in records if record["status"] == "PASS"]
    if not passes:
        return _unavailable("Brak rekordów PASS")
    counts = {"verified": 0, "changed": 0, "missing": 0, "refused": 0, "unreadable": 0}
    files = 0
    # Jednostką są pliki z rekordów PASS; dowody rekordów FAIL służą tylko kandydatom.
    for record in passes:
        for item in record["evidence"]:
            files += 1
            if item["path"] not in cache:
                cache[item["path"]] = _probe(root, item["path"])
            state, digest = cache[item["path"]]
            if state == "hashed":
                state = "verified" if digest == item["sha256"] else "changed"
            counts[state] += 1
    return {"status": "available" if counts["verified"] == files else "partial", "source": "checkpoints+sha256",
            "pass_records": len(passes), "evidence_files": files, **counts}


def _counter(records, name):
    given = [record[name] for record in records if name in record]
    if not given:
        return _unavailable("Brak pola w rekordach")
    return {"status": "available" if len(given) == len(records) else "partial", "source": "checkpoints",
            "value": sum(given), "records_with_value": len(given), "records_total": len(records)}


def _rubric(item, records, root, cache, steps):
    if not records:
        return {name: _unavailable("Brak rekordów" if name != "plan_coverage"
                                   else "Kroki checkpointu nie są powiązane z planem") for name in CRITERIA}
    evidence = _evidence(records, root, cache)
    if evidence["status"] != "unavailable":
        evidence["reused_from_problem"] = sum(step["reused"] for step in steps.values())
    return {"evidence": evidence,
            "repeats": {"status": "available", "source": "checkpoints", "repeated_steps": item["repeated_steps"],
                        "attempts_over_1": sum(record["attempt"] > 1 for record in records)},
            "denied": _counter(records, "denied"), "inherited": _counter(records, "inherited"),
            "plan_coverage": _unavailable("Kroki checkpointu nie są powiązane z planem")}


def _uncategorized(records):
    """Rekordy problemu z wolnym `cause` bez klucza; sygnał, że klucze nie są używane."""
    seen, count = set(), 0
    for record in records:
        repeat = record["step"] in seen
        seen.add(record["step"])
        cause = record.get("cause")
        if (_is_problem(record, repeat) and isinstance(cause, str) and cause.strip()
                and not record.get("cause_key")):
            count += 1
    return count


CAUSE_KEYS_PATH = Path("workflow/cause-keys.yaml")
MAX_CAUSE_KEYS_BYTES = 256 * 1024
_CAUSE_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


def _cause_keys_file(path):
    """Jeden plik listy kluczy przyczyn: dane albo None (brak, błąd odczytu, za duży, niepoprawny,
    alias będący kluczem lub należący do dwóch kluczy w tym pliku)."""
    try:
        if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_CAUSE_KEYS_BYTES:
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        return None
    keys = data.get("keys")
    if not isinstance(keys, dict):
        return None
    aliases = {}
    for key, item in keys.items():
        given = item.get("aliases", []) if isinstance(item, dict) else None
        if not isinstance(key, str) or not _CAUSE_KEY.fullmatch(key) or not isinstance(given, list):
            return None
        for alias in given:
            if not isinstance(alias, str) or not _CAUSE_KEY.fullmatch(alias) or alias in keys or alias in aliases:
                return None
            aliases[alias] = key
    return data


def _cause_keys(layerset):
    """Lista kluczy przyczyn scalona z warstw (`workflow/cause-keys.yaml` + `cause-keys.d/`, K2):
    ({alias: klucz kanoniczny}, {klucze kanoniczne}) albo None.

    None znaczy: żadna warstwa nie ma listy, błąd odczytu, niepoprawny plik którejś warstwy albo kolizja
    aliasów między warstwami (`alias-collision`); wtedy nie ma aliasów i `retro` zachowuje się jak bez listy."""
    try:
        merged = require_layerset(layerset, "retro").merged_file(CAUSE_KEYS_PATH.as_posix())
    except _layers.LayerError:
        return None
    if not merged.sources or merged.conflicts:
        return None
    if any(_cause_keys_file(Path(path)) is None for _layer, path in merged.sources):
        return None
    keys = merged.data.get("keys") or {}
    aliases = {}
    for key, item in keys.items():
        for alias in item.get("aliases", []):
            if alias in keys or alias in aliases:
                return None
            aliases[alias] = key
    return aliases, set(keys)


_STATUS_ORDER = {"recurred": 0, "ready": 1, "needs_case": 2, "needs_owner": 3, "decided": 4}
DECISIONS_PATH = Path("docs/retro/decisions.yaml")
DECISION_VALUES = ("approved", "rejected")
DECISION_FIELDS = {"cause_key", "decision", "date", "owner", "case", "ref"}
MAX_DECISIONS_BYTES = 1024 * 1024
_DAY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_ATTEMPT_SUFFIX = re.compile(r"\.[0-9]+$")
PROPOSAL = "Propozycja do zatwierdzenia przez użytkownika; narzędzie niczego nie zmienia."


def _failing_case(group, root, cache):
    """Pierwszy zweryfikowany dowód rekordu FAIL (kolejność: id surowy, rekord, dowód); sha256 bez filtra."""
    for stem, _, record in group:
        if record["status"] != "FAIL":
            continue
        for item in record["evidence"]:
            if item["path"] not in cache:
                cache[item["path"]] = _probe(root, item["path"])
            state, digest = cache[item["path"]]
            if state == "hashed" and digest == item["sha256"]:
                return {"run": _mask(stem), "step": _mask(record["step"]), "path": _mask_path(item["path"]),
                        "sha256": item["sha256"], "check": "verified"}
    return None


PROPOSAL_FIELDS = {"proposal", "repo", "target", "decision", "date", "ref"}
PROPOSAL_DECISIONS = ("rejected", "applied")
PROPOSAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}/P[1-9][0-9]{0,3}")
FULL_SHA = re.compile(r"[0-9a-f]{40}")
REPOS = ("library", "project")


def _target_ok(value):
    """Cel propozycji albo sygnału: ścieżka względna z `/`, bez `..`, `.`, pustych segmentów i początkowego `/`."""
    if not isinstance(value, str) or not value or len(value) > 300 or "\\" in value or value.startswith("/"):
        return False
    if re.match(r"[A-Za-z]:", value) or any(unicodedata.category(char) in ("Cc", "Cf") for char in value):
        return False
    if _TOKEN.search(value):
        return False
    return all(segment not in ("", ".", "..") for segment in value.split("/"))


def _registry_day(value):
    """Data wpisu rejestru (tekst `RRRR-MM-DD` albo data YAML) jako `date`; inaczej `ValueError`."""
    if isinstance(value, str):
        if not _DAY.match(value):
            raise ValueError
        return date.fromisoformat(value)
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError
    return value


def _parse_proposals(items):
    """Sekcja `proposals:` rejestru: [{proposal, repo, target, decision, date, ref}] z datą jako `date`."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError
    found, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - PROPOSAL_FIELDS:
            raise ValueError
        ident, value, ref = item.get("proposal"), item.get("decision"), item.get("ref")
        if (not isinstance(ident, str) or not PROPOSAL_ID.fullmatch(ident) or ident in seen
                or item.get("repo") not in REPOS or not _target_ok(item.get("target"))
                or value not in PROPOSAL_DECISIONS):
            raise ValueError
        if value == "applied":
            if not isinstance(ref, str) or not FULL_SHA.fullmatch(ref):
                raise ValueError
        elif "ref" in item:
            raise ValueError
        seen.add(ident)
        found.append({"proposal": ident, "repo": item["repo"], "target": item["target"], "decision": value,
                      "date": _registry_day(item.get("date")), "ref": ref})
    return found


def _parse_registry_full(text):
    """Rejestr z opcjonalną sekcją `proposals:`:
    (wpisy kandydatów, {cause_key: (decyzja, data, fixed)}, propozycje); błąd to `ValueError`."""
    data = yaml.safe_load(text)
    if (not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
            or set(data) - {"version", "decisions", "proposals"}):
        raise ValueError
    items = data.get("decisions")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError
    found = {}
    for item in items:
        if not isinstance(item, dict) or set(item) - DECISION_FIELDS:
            raise ValueError
        key, value, day = item.get("cause_key"), item.get("decision"), item.get("date")
        if isinstance(day, str):
            if not _DAY.match(day):
                raise ValueError
            day = date.fromisoformat(day)
        if (not isinstance(key, str) or not key.strip() or key in found or value not in DECISION_VALUES
                or not isinstance(day, date) or isinstance(day, datetime)):
            raise ValueError
        found[key] = (value, day, value == "approved" and bool(item.get("ref")))
    return items, found, _parse_proposals(data.get("proposals"))


def _parse_registry(text):
    """Waliduje tekst rejestru kandydatów: (lista surowych wpisów, {cause_key: (decyzja, data, fixed)});
    błąd to `ValueError`. Sekcja `proposals:` jest walidowana, ale tu pomijana; zapisy zachowują ją (`_decide`).

    `fixed` znaczy: decyzja `approved` z zapisanym `ref` (poprawka została wykonana)."""
    items, found, _ = _parse_registry_full(text)
    return items, found


def _open_regular(path, limit, resolved=False):
    """Zawartość zwykłego pliku bez dowiązania, do `limit` bajtów (ValueError przy każdym odstępstwie).

    `resolved=True` (odczyt cudzych drzew: główny checkout, inne projekty): `path` leży pod rozwiązanym korzeniem,
    więc po otwarciu jego `realpath` musi być równy jemu samemu, a otwarty deskryptor musi wskazywać ten sam plik
    (`os.path.samestat`). Podmiana katalogu pośredniego na dowiązanie między kontrolą a otwarciem kończy się
    odmową; `O_NOFOLLOW` chroni tylko ostatni segment. Rozwiązanie jest przenośne (Linux, macOS, Windows)."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        raise ValueError("Plik jest nieczytelny") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError("Plik nie jest zwykłym plikiem bez dowiązania")
    try:
        # NOFOLLOW i NONBLOCK: plik podmieniony po `lstat` na dowiązanie lub FIFO nie zawiesi odczytu.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Plik nie jest zwykłym plikiem bez dowiązania")
            if resolved:
                real = os.path.realpath(path)
                if (os.path.normcase(real) != os.path.normcase(os.path.normpath(path))
                        or not os.path.samestat(info, os.stat(real))):
                    raise ValueError("Ścieżka pliku zmieniła się podczas odczytu")
            data = stream.read(limit + 1)
    except OSError:
        raise ValueError("Plik jest nieczytelny") from None
    if len(data) > limit:
        raise ValueError("Plik przekracza limit rozmiaru")
    return data


def _load_registry(root, parse, resolved=False):
    """(`absent`|`available`|`invalid`, wynik `parse` albo None) dla `docs/retro/decisions.yaml`.

    `resolved=True`: korzeń jest rozwiązany i obcy, odczyt jak w `_open_regular(resolved=True)`."""
    path = root / DECISIONS_PATH
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "invalid", None
    try:
        if not stat.S_ISREG(mode):
            raise ValueError
        if resolved:
            text = _open_regular(path, MAX_DECISIONS_BYTES, resolved=True).decode("utf-8")
        else:
            if path.stat().st_size > MAX_DECISIONS_BYTES:
                raise ValueError
            text = path.read_text(encoding="utf-8")
        return "available", parse(text)
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        return "invalid", None


def _registry(root):
    """Rejestr decyzji użytkownika: (`absent`|`available`|`invalid`, wpisy, {cause_key: (decyzja, data, fixed)})."""
    state, parsed = _load_registry(root, _parse_registry)
    return (state, *(parsed or ([], {})))


def _parse_registry_doc(text):
    """Jak `_parse_registry_full`, plus surowa sekcja `proposals:` do zachowania przy zapisie:
    (wpisy, decyzje, propozycje, czy sekcja jest, jej surowa wartość)."""
    items, found, proposals = _parse_registry_full(text)
    data = yaml.safe_load(text)
    return items, found, proposals, "proposals" in data, data.get("proposals")


def _registry_doc(root):
    """Rejestr do zapisu: (stan, wpisy, decyzje, propozycje, czy sekcja `proposals:` jest, jej surowa wartość)."""
    state, parsed = _load_registry(root, _parse_registry_doc)
    return (state, *(parsed or ([], {}, [], False, None)))


def _registry_full(root, resolved=False):
    """Rejestr z sekcją `proposals:` (odczyt dla `retro-scan`): (stan, lista propozycji)."""
    state, parsed = _load_registry(root, _parse_registry_full, resolved)
    return state, (parsed[2] if parsed else [])


def _decisions(root):
    """Rejestr decyzji użytkownika: (`absent`|`available`|`invalid`, {cause_key: (decyzja, data, fixed)}).

    Do wyniku trafiają tylko decyzja, data i `fixed`; `owner`, `case` i `ref` służą ludziom i review."""
    state, _, found = _registry(root)
    return state, found


def _canonical_decisions(decisions, aliases):
    """Decyzje pod kluczami kanonicznymi; przy kolizji alias/klucz wygrywa późniejsza data."""
    merged = {}
    for key, value in decisions.items():
        key = aliases.get(key, key)
        if key not in merged or value[1] > merged[key][1]:
            merged[key] = value
    return merged


def _record_day(record):
    """Data UTC `recorded_at`; rekord bez poprawnej daty nie jest liczony jako późniejszy."""
    try:
        moment = datetime.fromisoformat(record["recorded_at"])
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError):  # krańce zakresu daty z przesunięciem strefy
        return None
    return moment.date()


def _cause_groups(entries, aliases):
    """Rekordy problemu z kluczem przyczyny z przebiegów `ok` całego projektu: {klucz kanoniczny: [(plik, id, rekord)]}."""
    groups = {}
    for entry in sorted((entry for entry in entries if entry["state"] == "ok"), key=lambda entry: entry["stem"]):
        seen = set()
        # Tożsamość: `run_id` z treści, gdy zgodny ze `_slug`, inaczej nazwa pliku; bez końcowego `.<liczba>`.
        identity = entry["data"].get("run_id")
        try:
            identity = _slug(identity)
        except ValueError:
            identity = entry["stem"]
        identity = _ATTEMPT_SUFFIX.sub("", identity)
        for record in entry["data"]["checkpoints"]:
            repeat = record["step"] in seen
            seen.add(record["step"])
            if record.get("cause_key") and _is_problem(record, repeat):
                groups.setdefault(aliases.get(record["cause_key"], record["cause_key"]), []).append(
                    (entry["stem"], identity, record))
    return groups


def _candidates(groups, root, cache, decisions=None, aliases=None):
    """Kandydaci zmiany z rekordów problemu przebiegów `ok` całego projektu, niezależnie od `--run-id`.

    Kandydat z decyzją w rejestrze (także zapisaną pod aliasem) ma status `recurred`, gdy po dniu decyzji (UTC)
    pojawił się rekord problemu z tym kluczem, inaczej `decided`."""
    aliases = aliases or {}
    decisions = _canonical_decisions(decisions or {}, aliases)
    found = []
    for key, group in groups.items():
        runs = {identity for _, identity, _ in group}
        if len(runs) < 2:
            continue
        owners = sorted({record["owner"] for _, _, record in group if record.get("owner")})
        case = _failing_case(group, root, cache)
        owner = owners[0] if len(owners) == 1 else None
        missing = (["owner"] if owner is None else []) + (["case"] if case is None else [])
        texts = {str(record["cause"]).strip() for _, _, record in group
                 if isinstance(record.get("cause"), str) and record["cause"].strip()}
        status = "needs_owner" if owner is None else "needs_case" if case is None else "ready"
        decision = None
        if key in decisions:
            value, day, fixed = decisions[key]
            after = sum(1 for _, _, record in group if (_record_day(record) or day) > day)
            decision = {"decision": value, "date": day.isoformat(), "records_after": after, "fixed": fixed}
            status = "recurred" if after else "decided"
        found.append({"cause_key": _mask(key), "status": status, "decision": decision,
                      "missing": missing, "runs": len(runs), "records": len(group), "owner": owner,
                      "owners_seen": owners, "failing_case": case,
                      "cause_sha256": sorted({hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]
                                              for text in texts}),
                      "proposal": PROPOSAL, "_sort": key})
    found.sort(key=lambda item: (_STATUS_ORDER[item["status"]], -item["runs"], item["_sort"]))
    for item in found:
        del item["_sort"]
    return found


def _load(directory, name):
    """Plik `*.json` magazynu: (id, stan, dane lub kategoria). Typ pliku jest sprawdzany przed otwarciem.

    Kategoria błędu pochodzi ze stałej listy; komunikat wyjątku (ścieżki, fragmenty JSON) nie wychodzi dalej."""
    ident = _run_id(Path(name))
    try:
        _slug(Path(name).stem)
    except ValueError:
        return ident, "unreadable", "invalid_name"
    path = directory / name
    try:
        mode = path.lstat().st_mode
    except OSError:
        return ident, "unreadable", "io_error"
    if stat.S_ISLNK(mode):
        return ident, "unreadable", "symlink"
    if not stat.S_ISREG(mode):
        return ident, "unreadable", "not_regular"
    try:
        data = _read(path)
    except OSError:
        return ident, "unreadable", "io_error"
    except (ValueError, KeyError, TypeError, RecursionError):  # także json.JSONDecodeError
        return ident, "unreadable", "malformed"
    return ident, ("benchmark" if "benchmark" in data else "ok"), data


def _report_dir(path):
    """Sygnały z plików raportów: tylko liczniki, bez treści i nazw; bez podążania za dowiązaniami."""
    files = {"md": 0, "log": 0, "diff": 0, "other": 0}
    signals = dict.fromkeys((*SIGNALS, "unrecognized"), 0)
    state = {"unreadable": 0, "truncated": False}
    count = total = 0

    def failed(_error):
        state["unreadable"] += 1

    for top, dirnames, filenames in os.walk(str(path), onerror=failed, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(top, name)
            try:
                info = os.lstat(full)
            except OSError:
                state["unreadable"] += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if count >= MAX_REPORT_FILES:
                state["truncated"] = True
                break
            count += 1
            extension = os.path.splitext(name)[1].lower().lstrip(".")
            files[extension if extension in ("md", "log", "diff") else "other"] += 1
            if extension == "md":
                # Limit bajtów liczy tylko to, co faktycznie czytane z `.md`; po jego przekroczeniu `.md` nie są czytane.
                read = min(info.st_size, MAX_MARKDOWN_BYTES)
                if total + read > MAX_REPORT_BYTES:
                    state["truncated"] = True
                    continue
                total += read
                _scan_markdown(full, info.st_size, signals, state)
        else:
            continue
        break
    return files, signals, state


def _scan_markdown(full, size, signals, state):
    try:
        # NOFOLLOW i NONBLOCK: plik podmieniony po `lstat` na dowiązanie lub FIFO nie zawiesi odczytu.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(full, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return
            data = stream.read(MAX_MARKDOWN_BYTES)
    except OSError:
        state["unreadable"] += 1
        return
    if size > MAX_MARKDOWN_BYTES:
        state["truncated"] = True
    for match in _STATUS_LINE.finditer(data.decode("utf-8", errors="replace")):
        found = _SIGNAL.match(match.group(0))
        signals[found.group(6) if found else "unrecognized"] += 1


def _in_runs(name, scope):
    return scope is None or name == scope or name.startswith(scope + ".")


def _in_dirs(name, scope):
    return _in_runs(name, scope) or scope.startswith(name + ".")


def _store(root):
    """Katalog magazynu albo None, gdy go brak; dowiązanie, zwykły plik i błąd odczytu to `ValueError`."""
    skillsync = root / ".skillsync"
    runs = skillsync / "runs"
    for path in (skillsync, runs):
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return None
        except OSError:
            raise ValueError("Magazyn przebiegów jest nieczytelny") from None
        if stat.S_ISLNK(mode):
            raise ValueError("Magazyn przebiegów nie może być dowiązaniem")
        if not stat.S_ISDIR(mode):
            raise ValueError("Magazyn przebiegów nie jest katalogiem")
    return runs


def _emit(report, json_output):
    """Wynik `retro` z polem `schema`: 1 na początku."""
    return emit({"schema": 1, **report}, json_output)


PROPOSAL_STATES = ("deferred", "open", "approved", "revising", "stale", "failed", "handoff", "rejected", "applied")
MAX_PROPOSALS = 50
MAX_TITLE = 120
_PROPOSAL_OWNERS = ("skill", "instruction", "docs", "route", "test", "hook")


def safe_cause_key(key):
    """Klucz przyczyny dopuszczony do wyniku: wzorzec klucza, bez wzorca tokenu (klucz z pliku retro bywa dowolnym tekstem)."""
    return bool(_CAUSE_KEY.fullmatch(key)) and not _TOKEN.search(key) and not _LONG.search(key)


def _retro_section(root, entries, aliases, scope, all_states):
    """K7/K9: (`proposals[]`, `proposals_truncated`, `coverage.retro`) z `retro-signals.json`, `retro-decisions.json`
    i sekcji `proposals:` rejestru. Stan i kolejność jak w `retro-scan` (wspólny czytnik i `_proposals`)."""
    from . import retro_scan
    budget = retro_scan.ScanBudget()
    runs, unreadable, cut = retro_scan._read_runs(root, None, budget)
    runs = [run for run in runs if _in_runs(run["id"], scope)]
    _, registry = _registry_full(root)
    registry = [entry for entry in registry if _in_runs(entry["proposal"].rsplit("/", 1)[0], scope)]
    by_run = {run["id"]: run for run in runs}
    wanted = PROPOSAL_STATES if all_states else PROPOSAL_STATES[:-2]
    found = [item for item in retro_scan._proposals([{"runs": runs, "registry": registry}]).values()
             if item["state"] in wanted]
    # W obrębie stanu od najnowszego: `created_at` malejąco, remis id przebiegu i numer P malejąco (jak w skanie).
    found.sort(key=lambda item: (item["run"], item["n"]), reverse=True)
    found.sort(key=lambda item: (PROPOSAL_STATES.index(item["state"]), -item["created"].timestamp()))
    items = []
    for item in found[:MAX_PROPOSALS]:
        run = by_run.get(item["run"])
        keys = sorted({aliases.get(signal["key"], signal["key"]) for signal in (run or {}).get("signals", [])
                       if signal["id"] in item.get("signal_ids", [])
                       and safe_cause_key(aliases.get(signal["key"], signal["key"]))})
        title = item.get("title")
        if title is not None:
            title = " ".join(_LONG.sub("[REDACTED]", _TOKEN.sub("[REDACTED]", title)).split())[:MAX_TITLE]
        owner = item.get("owner")
        items.append({"id": _mask(item["id"]), "owner": owner if owner in _PROPOSAL_OWNERS else None,
                      "repo": item["repo"], "target": _mask_path(item["target"]), "title": title,
                      "state": item["state"], "diff": _proposal_diff(root, item), "keys": keys,
                      "signals": len(item.get("signal_ids", [])),
                      "created_at": "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
                          item["created"].year, item["created"].month, item["created"].day,
                          item["created"].hour, item["created"].minute, item["created"].second)})
    groups = {}  # zakres wybiera tożsamości; grupa obejmuje wszystkie pliki `x.json` i `x.N.json`
    for entry in entries:
        groups.setdefault(_ATTEMPT_SUFFIX.sub("", entry["stem"]), []).append(entry)
    without = 0
    for group in groups.values():
        if not any(_in_runs(entry["stem"], scope) for entry in group):
            continue
        if any(entry["state"] == "unreadable" for entry in group):
            continue  # nie wiadomo, czy nieczytelny plik siostrzany nie ma retrospektywy
        records = [record for entry in group if entry["state"] == "ok" for record in entry["data"]["checkpoints"]]
        final_at, has = retrospective(records)
        if final_at is not None and not has and final_at.date().isoformat() >= RETRO_QUALITY_SINCE:
            without += 1
    invalid = sum(budget.invalid_files.get(name, 1) for name in unreadable if _in_runs(name, scope))
    coverage = {"runs": len(runs), "invalid_files": invalid, "final_without_retro": without}
    # Obcięcie czytnika (limit przebiegów albo bajtów) też oznacza niepełną listę.
    return items, len(found) > MAX_PROPOSALS or cut, coverage


def _proposal_diff(root, item):
    """Bieżący diff z ostatniego wpisu decyzji: tylko `retro-P<n>.diff` albo `retro-P<n>-<k>.diff`, zwykły plik
    (bez dowiązania) w katalogu przebiegu; inaczej None."""
    name = item.get("diff")
    if not isinstance(name, str) or not re.fullmatch(r"retro-P%d(-[0-9]+)?\.diff" % item["n"], name):
        return None
    try:
        path = root / ".skillsync" / "runs" / item["run"] / name
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
    except (OSError, ValueError):
        return None
    return _mask_path(".skillsync/runs/%s/%s" % (item["run"], name))


def _report(root, library, scope, retro_section=False, all_states=False):
    """Raport jednego projektu (dict, bez `schema`); wspólny dla `retro` i `retro --all`.

    `retro_section=True` (tylko `retro`): dodaje `proposals[]`, `proposals_truncated` i `coverage.retro`."""
    keys = _cause_keys(library)
    aliases = keys[0] if keys else {}
    directory = _store(root)
    entries, dirs, symlinks, invalid = [], [], [], []
    if directory is not None:
        try:
            names = sorted(os.listdir(directory))
            dirs, symlinks, invalid = _scan_dirs(directory)
        except OSError:
            raise ValueError("Nie można odczytać magazynu przebiegów") from None
        for name in names:
            if name.endswith(".json"):
                ident, state, data = _load(directory, name)
                entries.append({"stem": Path(name).stem, "id": ident, "state": state, "data": data})
    # Benchmark nie jest właścicielem katalogu.
    owners = {entry["stem"]: _owner_dir(entry["stem"], dirs) for entry in entries if entry["state"] != "benchmark"}
    owned = set(owners.values())
    scoped = [entry for entry in entries if _in_runs(entry["stem"], scope)]
    no_data_dirs = [name for name in dirs if name not in owned and _in_runs(name, scope)]
    if scope is not None and not any(entry["state"] != "benchmark" for entry in scoped) and not any(
            _in_runs(name, scope) for name in dirs):
        # Zakres bez żadnego przebiegu ani katalogu (sam benchmark się nie liczy).
        return {"status": "BLOCKED", "reason": "Brak przebiegu ani katalogu o podanym id", "run_id": _mask(scope)}

    cache, runs, uncategorized, benchmarks = {}, [], 0, 0
    for entry in scoped:
        if entry["state"] == "benchmark":
            benchmarks += 1
        elif entry["state"] == "unreadable":
            runs.append({"id": entry["id"], "class": "unreadable", "reason": entry["data"]})
        else:
            records = entry["data"]["checkpoints"]
            item, steps = _classify_run(entry["id"], records)
            uncategorized += _uncategorized(records)
            runs.append({**{key: item[key] for key in RUN_FIELDS},
                         "rubric": _rubric(item, records, root, cache, steps)})
    for name in no_data_dirs:
        item, _ = _classify_run(name, [])
        runs.append({**{key: item[key] for key in RUN_FIELDS}, "rubric": _rubric(item, [], root, cache, {})})
    runs.sort(key=lambda run: run["id"])
    for run in runs:
        run["id"] = _mask(run["id"])

    with_records = {owners[entry["stem"]] for entry in entries
                    if entry["state"] == "ok" and entry["data"]["checkpoints"]}
    report_dirs = []
    for name in dirs:
        if _in_dirs(name, scope):
            files, signals, state = _report_dir(directory / name)
            # Heurystyka: sygnały porażki w raportach ponad porażkami zapisanymi w przebiegach tego katalogu.
            failed = sum(record["status"] in ("FAIL", "BLOCKED") for entry in entries
                         if entry["state"] == "ok" and owners[entry["stem"]] == name
                         for record in entry["data"]["checkpoints"])
            report_dirs.append({"id": _mask(name), "has_records": name in with_records,
                                "provenance": "report-heuristic", "files": files, "status_signals": signals,
                                "unrecorded_signals": max(0, signals["FAIL"] + signals["CHANGES_REQUIRED"] - failed),
                                "truncated": state["truncated"], "unreadable_files": state["unreadable"]})

    unreadable_all = [entry for entry in entries if entry["state"] == "unreadable"]
    classes = [run["class"] for run in runs]
    coverage = {"runs": len(runs), "no_data": classes.count("no_data"),
                "with_records": len(runs) - classes.count("no_data") - classes.count("unreadable"),
                "unreadable": classes.count("unreadable"),
                "unreadable_outside_scope": sum(not _in_runs(entry["stem"], scope) for entry in unreadable_all),
                "uncategorized_causes": uncategorized,
                "symlinks_skipped": sum(_in_dirs(name, scope) for name in symlinks),
                "invalid_dirs": sum(_in_dirs(name, scope) for name in invalid),
                "benchmark_runs_skipped": benchmarks}
    decisions_state, decisions = _decisions(root)
    status = ("BLOCKED" if coverage["unreadable"] or decisions_state == "invalid"
              else "PASS" if coverage["with_records"] else "NOT_RUN")
    groups = _cause_groups(entries, aliases)
    candidates = _candidates(groups, root, cache, decisions, aliases)
    unknown = None if keys is None else sorted({_mask(key) for key in groups if key not in keys[1]})
    report = {"status": status, "runs": runs, "report_dirs": report_dirs, "candidates": candidates,
              "decisions": {"status": decisions_state, "path": DECISIONS_PATH.as_posix(), "count": len(decisions)},
              "candidates_complete": not unreadable_all, "coverage": coverage,
              "not_detected": NOT_DETECTED, "notes": NOTES, "unknown_cause_keys": unknown}
    if retro_section:
        proposals, truncated, retro_coverage = _retro_section(root, entries, aliases, scope, all_states)
        if decisions_state == "invalid":  # BLOCKED: bez propozycji z niepewnego stanu
            proposals, truncated = [], False
        report["proposals"], report["proposals_truncated"] = proposals, truncated
        report["coverage"] = {**coverage, "retro": retro_coverage}
    return report




def retro(args):
    if args.all:
        return _retro_all(args)
    root, _, library = load_layer_project(args.path, args.layers)  # LayerError: `skillsync:` exit 2
    root = Path(root).resolve()
    scope = None if args.run_id is None else _slug(args.run_id)
    return _emit(_report(root, library, scope, True, getattr(args, "all_states", False)), args.json)


PRUNE_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__"}
GIT_TIMEOUT = 5
GIT_BUDGET = 30
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")


def _git_env():
    """Środowisko dla `git`: kopia `os.environ` bez zmiennych przestawiających repozytorium, bez pytań o hasło."""
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ENV_DROP}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _discover(config, skipped=None):
    """Katalogi projektów z `discover_paths` (te same korzenie co `discover_manifests`), bez ścieżek z
    `discover_exclude` configu maszyny (te ścieżki i ich podkatalogi są pomijane).

    Czyta wyłącznie strukturę katalogów i istnienie manifestu `.github/.skillsync.yaml`."""
    excludes = discover_excludes(config)
    paths = config.get("discover_paths", [])
    found = set()
    for item in paths if isinstance(paths, list) else []:
        base = Path(str(item)).expanduser()
        if not base.is_dir():
            continue
        for top, dirnames, _ in os.walk(str(base), followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name not in PRUNE_DIRS
                                 and not is_excluded(Path(top, name), excludes))
            try:
                if Path(top, ".github", ".skillsync.yaml").is_file() and not is_excluded(Path(top), excludes):
                    found.add(Path(top))
            except OSError:  # katalog niesprawdzalny (np. nieczytelny `.github`): projekt pomijany
                if skipped is not None:
                    skipped.append(Path(top))
    return sorted(found)


def _identity(path, deadline):
    """(tożsamość repozytorium, czy główny checkout): wspólny katalog git i prefiks projektu w repozytorium.

    Katalog bez gita, błąd albo przekroczony czas dają własną tożsamość."""
    own = (str(path), ""), True
    if time.monotonic() > deadline:
        return own
    try:
        result = subprocess.run(["git", "rev-parse", "--git-common-dir", "--show-prefix", "--show-toplevel"],
                                cwd=str(path), capture_output=True, text=True, timeout=GIT_TIMEOUT,
                                env=_git_env())
        common, prefix, top = result.stdout.split("\n")[:3]
    except (OSError, ValueError, subprocess.SubprocessError):
        return own
    if result.returncode != 0 or not common or not top:
        return own
    return (str((path / common).resolve()), prefix), (Path(top) / ".git").is_dir()


def _project_identities(paths, deadline=None):
    """{tożsamość repozytorium: (ścieżka, czy główny checkout)}: worktree ustępuje głównemu checkoutowi (przy
    remisie wygrywa pierwszy). `deadline` (czas `time.monotonic`) pozwala współdzielić budżet git z wywołującym."""
    chosen = {}
    deadline = time.monotonic() + GIT_BUDGET if deadline is None else deadline
    for path in paths:
        identity, main = _identity(path, deadline)
        if identity not in chosen or main and not chosen[identity][1]:
            chosen[identity] = (path, main)
    return chosen


def _unique_projects(paths):
    """Jeden projekt na repozytorium: worktree ustępuje głównemu checkoutowi (przy remisie wygrywa pierwszy)."""
    return sorted(path for path, _ in _project_identities(paths).values())


def _machine_config():
    """Konfiguracja maszyny; błąd odczytu to `ValueError` ze stałym komunikatem (bez ścieżki i treści pliku)."""
    try:
        return read_yaml(Path(os.environ.get("SKILLSYNC_CONFIG") or Path.home() / ".config/skillsync/config.yaml"))
    except (OSError, ValueError, UnicodeError, RecursionError, yaml.YAMLError):
        raise ValueError("Konfiguracja maszyny jest nieczytelna") from None


def _project_name(path):
    try:
        return str(path.resolve().relative_to(Path.home() / "Repos"))
    except ValueError:
        return path.name


def _project_entry(path, layers):
    """Wpis `projects[]`: raport projektu albo `BLOCKED` z powodem; wyjątek jednego projektu nie przerywa reszty."""
    entry = {"project": _project_name(path)}
    try:
        root, _, source = load_layer_project(path, layers)
        report = _report(Path(root).resolve(), source, None)
    except Exception as error:  # jeden projekt nigdy nie przerywa całości
        if isinstance(error, yaml.YAMLError):
            reason = "Niepoprawny YAML"
        elif isinstance(error, (ValueError, OSError)):
            reason = " ".join(str(error).split())[:300] or type(error).__name__
        else:
            reason = "Błąd: " + type(error).__name__
        # Ścieżki lokalne nie wychodzą do wyniku: najpierw projekt (dłuższa), potem dom.
        reason = reason.replace(str(path), "<projekt>").replace(str(Path(path).resolve()), "<projekt>")
        reason = reason.replace(str(Path.home()), "~")
        return {**entry, "status": "BLOCKED", "reason": redact(reason), "candidates": [], "coverage": None}, None, None
    entry.update(status=report["status"], candidates=report["candidates"], coverage=report["coverage"])
    if report["status"] == "BLOCKED":
        entry["reason"] = ("Rejestr decyzji jest niepoprawny" if report["decisions"]["status"] == "invalid"
                           else "Część przebiegów jest nieczytelna")
    return entry, root, source


def _retro_all(args):
    if args.run_id is not None or args.path is not None:
        return _emit({"status": "BLOCKED", "reason": "--all nie przyjmuje --run-id ani ścieżki projektu"}, args.json)
    try:
        config = _machine_config()
        discover_excludes(config)
    except ValueError as error:
        return _emit({"status": "BLOCKED", "reason": str(error)}, args.json)
    from . import retro_scan  # wspólny czytnik sygnałów; import leniwy (retro_scan importuje ten moduł)
    projects, totals = [], {"approved": 0, "rejected": 0, "recurred": 0, "open": 0}
    scan_budget, pair_holders, pairs_read, pairs_cut = retro_scan.ScanBudget(), {}, 0, False
    deadline = time.monotonic() + GIT_BUDGET
    chosen = _project_identities(_discover(config), deadline)
    pairs_cut = time.monotonic() > deadline  # budżet git wyczerpany: deduplikacja worktree mogła się urwać
    for path in sorted(path for path, _ in chosen.values()):
        entry, root, source = _project_entry(path, args.layers)
        projects.append(entry)
        if entry["status"] == "BLOCKED":
            continue
        state, decisions = _decisions(Path(root).resolve())
        keys = _cause_keys(source)
        # K8: pary (klucz, cel) sygnałów `repo: library`; błąd odczytu jednego projektu nie przerywa reszty,
        # ale oznacza obcięcie. Limit `MAX_PROJECTS` projektów jak w `--cross-project`.
        if pairs_read >= retro_scan.MAX_PROJECTS:
            pairs_cut = True
        else:
            pairs_read += 1
            try:
                resolved = Path(root).resolve()
                for pair in retro_scan.library_signals(resolved, keys[0] if keys else {}, scan_budget):
                    pair_holders.setdefault(pair, set()).add(str(resolved))  # projekty liczone po ścieżce, nie po nazwie
            except Exception:  # jak `_project_entry`: jeden projekt nie przerywa reszty
                pairs_cut = True
        decisions = _canonical_decisions(decisions, keys[0] if keys else {})
        if state == "available":
            totals["approved"] += sum(value[0] == "approved" for value in decisions.values())
            totals["rejected"] += sum(value[0] == "rejected" for value in decisions.values())
        for item in entry["candidates"]:
            if item["status"] == "recurred":
                totals["recurred"] += 1
            elif item["status"] in ("ready", "needs_case", "needs_owner"):
                totals["open"] += 1
    # Podpowiedź: klucz kanoniczny kandydatów w co najmniej dwóch projektach (po deduplikacji worktree).
    holders = {}
    for entry in projects:
        for item in entry["candidates"]:
            holders.setdefault(item["cause_key"], set()).add(entry["project"])
    for entry in projects:
        for item in entry["candidates"]:
            if len(holders[item["cause_key"]]) >= 2 and "[REDACTED]" not in item["cause_key"]:
                item["suggested_owner_scope"] = "library"
    statuses = [entry["status"] for entry in projects]
    status = ("PASS" if "PASS" in statuses else "BLOCKED" if statuses and set(statuses) == {"BLOCKED"}
              else "NOT_RUN")
    # K8: liczby projektów zamiast nazw i id przebiegów; cel maskowany jak ścieżki dowodów.
    pairs = sorted(({"key": _mask(key), "target": _mask_path(target), "projects": len(holders)}
                    for (key, target), holders in pair_holders.items() if len(holders) >= 2),
                   key=lambda item: (-item["projects"], item["key"], item["target"]))
    return _emit({"status": status, "projects": projects, "totals": totals, "library_pairs": pairs,
                  "library_pairs_truncated": pairs_cut or scan_budget.truncated}, args.json)


def _check_text(value, label):
    """`case` i `ref`: niepusty tekst do 300 znaków, bez znaków sterujących i wzorców tokenów (wartość nie jest cytowana)."""
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise ValueError(label + " musi być niepustym tekstem do 300 znaków")
    if any(unicodedata.category(char) in ("Cc", "Cf") for char in value):
        raise ValueError(label + " nie może zawierać znaków sterujących")
    if _TOKEN.search(value):
        raise ValueError(label + " wygląda jak token")


def _safe_registry_path(root):
    """Odmawia przy dowiązaniu `docs`, `docs/retro` lub pliku, pliku nieregularnym i `nlink > 1`; niczego nie tworzy."""
    current = root
    for part in DECISIONS_PATH.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode):
            raise ValueError("Ścieżka rejestru nie może zawierać dowiązania")
        if current != root / DECISIONS_PATH and not stat.S_ISDIR(mode):
            raise ValueError("Ścieżka rejestru nie jest katalogiem")
    if not stat.S_ISREG(mode):
        raise ValueError("Rejestr decyzji nie jest zwykłym plikiem")
    if (root / DECISIONS_PATH).lstat().st_nlink > 1:
        raise ValueError("Rejestr decyzji nie może mieć dowiązań twardych")


def _write_registry(root, text):
    _write_atomic(root / DECISIONS_PATH, text)


def _write_atomic(path, text):
    """Zapis atomowy jak `set_plan_status`: plik tymczasowy w tym samym katalogu, `fsync`, `os.replace`."""
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _decide(args):
    """Zapisuje decyzję w rejestrze; zwraca raport PASS albo rzuca `ValueError` z powodem odmowy."""
    root, _, library = load_layer_project(args.path, args.layers)
    root = Path(root).resolve()
    key, value = args.cause_key, args.decision
    if not isinstance(key, str):
        raise ValueError("Wymagany --cause-key")
    _extra_fields({"cause_key": key})
    if _TOKEN.search(key):
        raise ValueError("Klucz przyczyny wygląda jak token")
    # Alias zamieniany jest dopiero po walidacji klucza; bez listy klucz zostaje taki, jaki podano.
    keys = _cause_keys(library)
    aliases = keys[0] if keys else {}
    key = aliases.get(key, key)
    if value not in DECISION_VALUES:
        raise ValueError("--decision musi być jedną z wartości: " + ", ".join(DECISION_VALUES))
    given = {"owner": args.owner, "case": args.case, "ref": args.ref}
    if value == "rejected" and any(item is not None for item in given.values()):
        raise ValueError("Decyzja rejected nie przyjmuje --owner, --case ani --ref")
    if args.owner is not None:
        _extra_fields({"cause_key": key, "owner": args.owner})
    for label in ("case", "ref"):
        if given[label] is not None:
            _check_text(given[label], "--" + label)
    _safe_registry_path(root)
    state, items, found, _, has_proposals, raw_proposals = _registry_doc(root)
    if state == "invalid":
        raise ValueError("Rejestr decyzji jest niepoprawny; popraw go ręcznie")
    group = [position for position, item in enumerate(items)
             if aliases.get(item["cause_key"], item["cause_key"]) == key]
    if group and not args.replace:
        raise ValueError("Wpis dla tego klucza już istnieje; użyj --replace")
    # Efektywny wpis grupy (klucz kanoniczny i jego aliasy): późniejsza data, przy remisie pierwszy.
    index = None
    for position in group:
        if index is None or found[items[position]["cause_key"]][1] > found[items[index]["cause_key"]][1]:
            index = position
    old = items[index] if index is not None else {}
    previous = None if index is None else {"decision": found[old["cause_key"]][0],
                                           "date": found[old["cause_key"]][1].isoformat()}
    day = datetime.now(timezone.utc).date()
    entry = {"cause_key": key, "decision": value, "date": day}
    if value == "approved":
        # Ponowna decyzja approved dziedziczy owner i case z poprzedniego approved; ref należy do bieżącego cyklu.
        inherited = old if old.get("decision") == "approved" else {}
        for label in ("owner", "case"):
            chosen = given[label] if given[label] is not None else inherited.get(label)
            if chosen is None:
                raise ValueError("Decyzja approved wymaga --owner i --case")
            entry[label] = chosen
        if given["ref"] is not None:
            entry["ref"] = given["ref"]
    if group:  # zamiana: jeden wpis w miejscu pierwszego wpisu grupy, reszta grupy znika
        items = [entry if position == group[0] else item for position, item in enumerate(items)
                 if position not in group[1:]]
    else:
        items = [*items, entry]
    document = {"version": 1, "decisions": items}
    if has_proposals:  # K6: sekcja `proposals:` przechodzi przez zapis bez zmian (wpisy, kolejność)
        document["proposals"] = raw_proposals
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    if len(text.encode("utf-8")) > MAX_DECISIONS_BYTES:
        raise ValueError("Rejestr decyzji przekroczyłby limit rozmiaru")
    try:
        _parse_registry(text)
    except ValueError:
        raise ValueError("Wynikowy rejestr nie przeszedłby walidacji") from None
    _write_registry(root, text)
    return {"schema": 1, "status": "PASS", "path": DECISIONS_PATH.as_posix(),
            "decision": {"cause_key": key, "decision": value, "date": day.isoformat()}, "previous": previous}


RUN_FINAL = ("rejected", "applied")


def _blocked(reason, writes=None):
    return {"schema": 1, "status": "BLOCKED", "reason": " ".join(reason.split())[:500] or "Odmowa",
            "writes": writes or {"run": "skipped", "registry": "skipped"}}


def _plan_proposal(args):
    """Walidacja i przygotowanie obu zapisów `retro-decide --proposal` bez żadnej zmiany na dysku (K5).

    Zwraca dict: katalog, teksty obu plików (`run_text` None = wpis przebiegu już jest, nic do zapisu) i wynik."""
    from . import retro_scan
    if any(item is not None for item in (args.cause_key, args.owner, args.case)):
        raise ValueError("--proposal wyklucza --cause-key, --owner i --case")
    ident, value = args.proposal, args.decision
    if not isinstance(ident, str) or not PROPOSAL_ID.fullmatch(ident):
        raise ValueError("--proposal musi mieć postać <run-id>/P<n>")
    run_id, number = ident.rsplit("/", 1)
    if value not in PROPOSAL_DECISIONS:
        raise ValueError("--decision musi być jedną z wartości: " + ", ".join(PROPOSAL_DECISIONS))
    ref = args.ref
    if value == "rejected" and ref is not None:
        raise ValueError("Decyzja rejected nie przyjmuje --ref")
    if value == "applied":
        if ref is None:
            raise ValueError("Decyzja applied wymaga --ref (pełny sha)")
        _check_text(ref, "--ref")
        if not FULL_SHA.fullmatch(ref):
            raise ValueError("--ref musi być pełnym sha (40 znaków hex)")
    root, _, _library = load_layer_project(args.path, args.layers)
    root = Path(root).resolve()
    if retro_scan.linked_worktree(root):
        raise ValueError("W worktree decyzje propozycji zapadają w głównym checkoucie")
    directory = _store(root)
    folder = None if directory is None else directory / run_id
    try:
        if folder is None or not stat.S_ISDIR(folder.lstat().st_mode):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError("Brak przebiegu o podanym id") from None
    decisions_path = folder / retro_scan.DECISIONS_FILE
    try:  # każdy błąd odczytu i parsowania plików retro przebiegu (JSON, rozmiar, dowiązanie, schemat) = jeden powód
        signals = retro_scan._json(folder / retro_scan.SIGNALS_FILE, retro_scan.MAX_RUN_FILE_BYTES, True)
        exists = decisions_path.is_symlink() or decisions_path.exists()
        document = (retro_scan._json(decisions_path, retro_scan.MAX_RUN_FILE_BYTES, True) if exists
                    else {"schema": 1, "run_id": run_id, "decisions": []})
        run = retro_scan._parse_run(run_id, signals, document if exists else None)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise retro_scan._Unreadable from None
    proposal = run["proposals"].get(number)
    if proposal is None:
        raise ValueError("Brak propozycji " + number + " w przebiegu")
    current = proposal["state"]
    if current is None:
        raise ValueError("Propozycja nie ma wpisu stanu w retro-decisions.json")
    _safe_registry_path(root)
    state, items, found, _, has_proposals, raw = _registry_doc(root)
    if state == "invalid":
        raise ValueError("Rejestr decyzji jest niepoprawny; popraw go ręcznie")
    position = next((index for index, entry in enumerate(raw or []) if isinstance(entry, dict)
                     and entry.get("proposal") == ident), None)
    if current in RUN_FINAL or position is not None:
        if not args.replace:
            raise ValueError("Propozycja ma już decyzję końcową; użyj --replace")
    elif value == "applied" and current not in ("approved", "handoff"):
        raise ValueError("applied wymaga stanu approved albo handoff (jest: " + current + ")")
    previous = None
    if position is not None:
        old = raw[position]
        previous = {"decision": old.get("decision"), "date": _registry_day(old.get("date")).isoformat()}
    day = datetime.now(timezone.utc).date()
    last = next(entry for entry in reversed(document["decisions"]) if entry.get("proposal") == number)
    entry = {"proposal": number, "state": value, "date": day.isoformat(), "diff": last.get("diff")}
    if value == "applied":
        entry["ref"] = ref
    entry["note"] = None
    same = last.get("state") == value and (value != "applied" or last.get("ref") == ref)
    run_text = None
    if not same:  # idempotentne ponowienie: wpis przebiegu już jest
        document["decisions"].append(entry)
        run_text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        if len(run_text.encode("utf-8")) > retro_scan.MAX_RUN_FILE_BYTES:
            raise ValueError("retro-decisions.json przekroczyłby limit rozmiaru")
    registry_entry = {"proposal": ident, "repo": proposal["repo"], "target": proposal["target"], "decision": value,
                      "date": day}
    if value == "applied":
        registry_entry["ref"] = ref
    listed = list(raw) if isinstance(raw, list) else []
    if position is not None:
        listed[position] = registry_entry
    else:
        listed.append(registry_entry)
    text = yaml.safe_dump({"version": 1, "decisions": items, "proposals": listed}, sort_keys=False,
                          allow_unicode=True)
    if len(text.encode("utf-8")) > MAX_DECISIONS_BYTES:
        raise ValueError("Rejestr decyzji przekroczyłby limit rozmiaru")
    try:
        _parse_registry_full(text)
    except ValueError:
        raise ValueError("Wynikowy rejestr nie przeszedłby walidacji") from None
    return {"root": root, "run_path": decisions_path, "run_text": run_text, "registry_text": text,
            "result": {"schema": 1, "status": "PASS", "path": DECISIONS_PATH.as_posix(),
                       "decision": {"proposal": ident, "decision": value, "date": day.isoformat()},
                       "previous": previous}}


def _decide_proposal(args):
    """`retro-decide --proposal`: najpierw wpis przebiegu, potem rejestr, każdy atomowo (K5)."""
    from . import retro_scan
    try:
        plan = _plan_proposal(args)
    except retro_scan._Unreadable:
        return _blocked("Pliki retro przebiegu są nieczytelne albo niepoprawne")
    except OSError:  # komunikat OSError zawiera ścieżkę
        return _blocked("Błąd odczytu pliku")
    except (ValueError, TypeError, KeyError, RecursionError, yaml.YAMLError) as error:
        return _blocked(str(error))
    writes = {"run": "skipped", "registry": "skipped"}
    if plan["run_text"] is not None:
        try:
            _write_atomic(plan["run_path"], plan["run_text"])
        except OSError:
            writes["run"] = "failed"
            return _blocked("Zapis retro-decisions.json nie powiódł się; rejestr nie został zmieniony", writes)
        writes["run"] = "written"
    try:
        _safe_registry_path(plan["root"])
        _write_registry(plan["root"], plan["registry_text"])
    except (OSError, ValueError):
        writes["registry"] = "failed"
        return _blocked("Zapis rejestru nie powiódł się; wpis w retro-decisions.json przebiegu "
                        + ("został zapisany" if writes["run"] == "written" else "już istniał")
                        + " (ponów z --replace)", writes)
    writes["registry"] = "written"
    return {**plan["result"], "writes": writes}


def retro_decide(args):
    if getattr(args, "proposal", None) is not None:
        return emit(_decide_proposal(args), args.json)
    try:
        report = _decide(args)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        report = {"schema": 1, "status": "BLOCKED", "reason": " ".join(str(error).split())[:500] or "Odmowa"}
    return emit(report, args.json)


def _json_error(message):
    print(json.dumps({"schema": 1, "status": "BLOCKED", "reason": " ".join(message.split())[:500]},
                     ensure_ascii=False, indent=2))
    sys.exit(2)


def register(subparsers):
    parser = subparsers.add_parser(
        "retro", help="Read-only retrospective report from checkpoints (classes, rubric, report signals; JSON)",
        description="Raport tylko do odczytu z .skillsync/runs. Klasa wynika wyłącznie z rekordów checkpointów; "
                    "raporty .md dają tylko sygnały pomocnicze. Pola identyfikacyjne (id, run_id, id katalogów, "
                    "cause_key, failing_case.run|step|path) są filtrowane po znanych wzorcach tokenów, ale filtr "
                    "nie łapie tekstu bez wzorca (np. w --cause-key), sekretu AWS z '/' ani tokenów z "
                    "separatorami bez znanego prefiksu: nie wpisuj tajemnic w identyfikatory. cause_sha256 to "
                    "niesolony skrót, więc krótki cause można potwierdzić zgadywaniem. Przy obu brakach "
                    "kandydat ma status needs_owner. Decyzje użytkownika z docs/retro/decisions.yaml dają statusy "
                    "decided (bez nawrotu) i recurred (rekord problemu po dniu decyzji, UTC); rejestr niepoprawny "
                    "daje BLOCKED.")
    add_project_arguments(parser)
    parser.add_argument("--run-id", help="Zawęź do ID i ID.* (przebiegi) oraz powiązanych katalogów raportów")
    parser.add_argument("--all", action="store_true",
                        help="Wszystkie projekty z discover_paths (bez ścieżek z discover_exclude, worktree raz); "
                             "bez --run-id i ścieżki")
    parser.add_argument("--all-states", action="store_true",
                        help="Propozycje retrospektywy także w stanach rejected i applied (domyślnie tylko "
                             "nierozstrzygnięte)")
    parser.set_defaults(path=None)
    parser.set_defaults(func=retro)

    decide = subparsers.add_parser(
        "retro-decide", help="Record a retrospective decision in docs/retro/decisions.yaml (JSON): "
                             "--cause-key ... approved|rejected, or --proposal ... rejected|applied",
        description="Dwa tryby. (1) --cause-key: dopisuje decyzję użytkownika (approved|rejected) do "
                    "docs/retro/decisions.yaml (zapis atomowy, data UTC dnia zapisu); approved wymaga --owner i "
                    "--case, rejected ich nie przyjmuje; istniejący klucz wymaga --replace (approved dziedziczy "
                    "wtedy owner i case z poprzedniego approved, ref nie). (2) --proposal <run-id>/P<n>: decyzja "
                    "propozycji retrospektywy (rejected|applied; applied wymaga --ref, pełnego sha), zapis najpierw "
                    "w retro-decisions.json przebiegu, potem w sekcji proposals: rejestru; w worktree BLOCKED. "
                    "Odmowa walidacji to JSON BLOCKED z kodem 2 i bez zmian na dysku.")
    add_project_arguments(decide)
    decide.add_argument("--cause-key", help="Klucz przyczyny [a-z0-9][a-z0-9-]{0,47}")
    decide.add_argument("--proposal", help="Decyzja propozycji retrospektywy <run-id>/P<n> (wyklucza --cause-key, "
                                           "--owner, --case): --decision rejected|applied, applied wymaga --ref "
                                           "(pełny sha); zapis w retro-decisions.json przebiegu i w proposals: "
                                           "rejestru; w worktree BLOCKED")
    decide.add_argument("--decision", help="approved albo rejected (z --proposal: rejected albo applied)")
    decide.add_argument("--owner", help="Właściciel poprawki: " + ", ".join(OWNERS))
    decide.add_argument("--case", help="Przypadek testowy lub dowód (do 300 znaków)")
    decide.add_argument("--ref", help="Odwołanie do wykonanej poprawki (np. commit); ustawia fixed")
    decide.add_argument("--replace", action="store_true", help="Zastąp istniejący wpis tego klucza")
    decide.error = _json_error
    decide.set_defaults(func=retro_decide)
