"""Skan progu retrospektywy tylko do odczytu (`skillsync retro-scan`): sygnały bieżącego przebiegu zestawione
z propozycjami z wcześniejszych przebiegów i rejestru (`docs/retro/decisions.yaml`, sekcja `proposals:`).

Stosuje deterministycznie „Algorytm progu i stanu” z `retro-quality.md` (kroki 1-5). Poza zakresem zostają
reguła „cel już zawiera regułę” i łączenie grup z diffami recenzenta: robi je orkiestrator. Nic nie jest zapisywane.

Źródła przebiegów i rejestrów to lista `sources` (kolejność = pierwszeństwo przy tym samym id propozycji), więc
dołożenie głównego checkoutu albo innych projektów nie wymaga przebudowy algorytmu."""

from datetime import date, datetime, time as day_time, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time

import yaml

from .common import add_project_arguments, emit, load_layer_project
from . import layers as _layers
from . import retro as _retro
from .retro import (FULL_SHA, REPOS, _ATTEMPT_SUFFIX, _CAUSE_KEY, _cause_keys, _discover, _git_env, _identity, _json_error,
                    _machine_config, _mask, _open_regular, safe_cause_key, _opaque, _project_identities, _registry_full, _store,
                    _target_ok)
from .runs import _extra_fields, _slug, _unique_pairs

MAX_INPUT_BYTES = 256 * 1024
MAX_RUN_FILE_BYTES = 1024 * 1024
MAX_RUNS = 2000
MAX_PROJECTS = 200  # inne projekty przy --cross-project
MAX_SCAN_BYTES = 64 * 1024 * 1024  # łączny rozmiar plików retro czytanych w jednym poleceniu (wszystkie źródła)
SIGNALS_FILE = "retro-signals.json"
DECISIONS_FILE = "retro-decisions.json"
STATES = ("open", "revising", "rejected", "approved", "applied", "deferred", "handoff", "failed", "stale")
UNRESOLVED = ("open", "revising", "approved", "deferred", "stale", "failed", "handoff")
_SIGNAL_ID = re.compile(r"S[0-9]{1,4}")
_PROPOSAL_N = re.compile(r"P[1-9][0-9]{0,3}")
_UTC = timezone.utc


class ScanBudget:
    """Łączny budżet bajtów plików retro czytanych w jednym poleceniu (wszystkie źródła i projekty)."""

    def __init__(self):
        self.bytes = 0
        self.invalid_files = {}  # {nazwa przebiegu: liczba nieczytelnych albo niepoprawnych plików retro}
        self.truncated = False  # któryś odczyt przebiegów został obcięty (limit przebiegów albo bajtów)


class _Unreadable(ValueError):
    """Plik retro przebiegu nieczytelny albo niepoprawny (przebieg jest pomijany)."""


# ---------------------------------------------------------------------------------------------- odczyt plików

def _json(path, limit, resolved=False):
    try:
        text = _open_regular(path, limit, resolved).decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_no_constant)
    except RecursionError:
        raise ValueError("Plik jest zbyt głęboko zagnieżdżony") from None


def _no_constant(_value):
    raise ValueError("Niedozwolona stała JSON")


def _read_input(root, run_id, rel):
    """Plik `--signals` (K1): względny do korzenia, po rozwiązaniu w `.skillsync/runs/<run-id>/`, zwykły plik
    bez dowiązania na trasie, do 256 KB. Zwraca sparsowany JSON."""
    if not isinstance(rel, str) or not rel or "\0" in rel:
        raise ValueError("Wymagany --signals (ścieżka względna do korzenia projektu)")
    if "\\" in rel or Path(rel).is_absolute() or re.match(r"[A-Za-z]:", rel):
        raise ValueError("--signals musi być ścieżką względną do korzenia projektu")
    parts = PurePosixPath(rel).parts
    if not parts or ".." in parts:
        raise ValueError("--signals nie może wychodzić poza katalog przebiegu")
    candidate = root
    for part in parts:  # dowiązanie na trasie znaczy zmianę drzewa: odmowa
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except OSError:
            raise ValueError("Plik --signals nie istnieje albo jest nieczytelny") from None
        if stat.S_ISLNK(mode):
            raise ValueError("--signals nie może przechodzić przez dowiązanie")
    base = os.path.realpath(root / ".skillsync" / "runs" / run_id)
    real = os.path.realpath(candidate)
    if os.path.commonpath([base, real]) != base or real == base:
        raise ValueError("--signals musi leżeć w .skillsync/runs/<run-id>/")
    try:
        return _json(candidate, MAX_INPUT_BYTES)
    except ValueError as error:
        raise ValueError("Plik --signals: " + str(error)) from None


def _parse_input(data, run_id):
    """Wejście skanu (K1): lista sygnałów `{id, key, repo, target, from_failure}`; pola dodatkowe ignorowane."""
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("Plik --signals: wymagane schema: 1")
    if data.get("run_id") != run_id:
        raise ValueError("Plik --signals: run_id różni się od --run-id")
    items = data.get("signals")
    if not isinstance(items, list):
        raise ValueError("Plik --signals: signals musi być listą")
    signals, seen = [], set()
    for index, item in enumerate(items, 1):
        label = "Plik --signals: sygnał #%d: " % index
        if not isinstance(item, dict):
            raise ValueError(label + "oczekiwany obiekt")
        missing = [name for name in ("id", "key", "repo", "target", "from_failure") if name not in item]
        if missing:
            raise ValueError(label + "brak pola wymaganego: " + ", ".join(missing))
        ident, key, repo, target = item["id"], item["key"], item["repo"], item["target"]
        if not isinstance(ident, str) or not _SIGNAL_ID.fullmatch(ident) or ident in seen:
            raise ValueError(label + "id musi mieć postać S<n> i być unikalne")
        if not isinstance(key, str) or not _CAUSE_KEY.fullmatch(key):
            raise ValueError(label + "key nie pasuje do wzorca klucza przyczyny")
        try:
            _extra_fields({"cause_key": key})
        except ValueError:
            raise ValueError(label + "key wygląda jak token") from None
        if repo is not None and repo not in REPOS:
            raise ValueError(label + "repo musi być library, project albo null")
        if target is not None and not _target_ok(target):
            raise ValueError(label + "target musi być ścieżką względną z / albo null")
        if repo is None and target is not None:
            raise ValueError(label + "repo: null wymaga target: null")
        if type(item.get("from_failure")) is not bool:
            raise ValueError(label + "from_failure musi być typu bool")
        seen.add(ident)
        signals.append({"id": ident, "key": key, "repo": repo, "target": target, "from_failure": item["from_failure"]})
    return signals


# --------------------------------------------------------------------------------------- przebiegi i rejestry

def _moment(value):
    """Znacznik czasu ISO-8601 jako aware UTC; `ValueError` przy niepoprawnym."""
    if not isinstance(value, str):
        raise ValueError
    moment = datetime.fromisoformat(value)
    try:
        return moment.replace(tzinfo=_UTC) if moment.tzinfo is None else moment.astimezone(_UTC)
    except OverflowError:  # krańce zakresu daty z przesunięciem strefy
        raise ValueError from None


def _run_signal(item):
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _SIGNAL_ID.fullmatch(item["id"]):
        raise _Unreadable
    key, repo, target = item.get("key"), item.get("repo"), item.get("target")
    if (not isinstance(key, str) or repo not in (*REPOS, None) or (target is not None and not _target_ok(target))
            or (repo is None and target is not None)):
        raise _Unreadable
    return {"id": item["id"], "key": key, "repo": repo, "target": target}


def _parse_signals_file(name, signals):
    """`retro-signals.json` przebiegu `name`: częściowy dict przebiegu (bez stanów) albo `_Unreadable`."""
    try:
        if (not isinstance(signals, dict) or signals.get("schema") != 1 or type(signals.get("schema")) is not int
                or signals.get("run_id") != name):
            raise _Unreadable
        created = _moment(signals.get("created_at"))
        run_signals = signals.get("signals")
        listed = signals.get("proposals", [])
        if not isinstance(run_signals, list) or not isinstance(listed, list):
            raise _Unreadable
        proposals = {}
        for item in listed:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not _PROPOSAL_N.fullmatch(item["id"]) or item.get("repo") not in REPOS
                    or not _target_ok(item.get("target"))):
                raise _Unreadable
            signal_ids = item.get("signals")
            title, owner = item.get("title"), item.get("owner")
            proposals[item["id"]] = {
                "repo": item["repo"], "target": item["target"], "state": None, "ref": None, "diff": None,
                "title": title if isinstance(title, str) else None,
                "owner": owner if isinstance(owner, str) else None,
                "signal_ids": [ident for ident in signal_ids if isinstance(ident, str)]
                if isinstance(signal_ids, list) else []}
        return {"id": name, "created": created, "signals": [_run_signal(item) for item in run_signals],
                "proposals": proposals}
    except (_Unreadable, ValueError, TypeError):
        raise _Unreadable from None


def _parse_decisions_file(name, decisions):
    """`retro-decisions.json` przebiegu `name`: {P<n>: (stan, ref, diff)} (ostatni wpis wygrywa) albo `_Unreadable`.
    Nie zależy od pliku sygnałów, więc można go ocenić osobno."""
    if (not isinstance(decisions, dict) or decisions.get("schema") != 1 or type(decisions.get("schema")) is not int
            or decisions.get("run_id") != name or not isinstance(decisions.get("decisions"), list)):
        raise _Unreadable
    states = {}
    for entry in decisions["decisions"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("proposal"), str)
                or not _PROPOSAL_N.fullmatch(entry["proposal"]) or entry.get("state") not in STATES):
            raise _Unreadable
        states[entry["proposal"]] = (entry["state"], entry.get("ref"), entry.get("diff"))
    return states


def _combine_run(run, states):
    for ident, (state, ref, diff) in states.items():
        if ident in run["proposals"]:
            run["proposals"][ident]["state"], run["proposals"][ident]["ref"] = state, ref
            run["proposals"][ident]["diff"] = diff if isinstance(diff, str) else None
    return run


def _parse_run(name, signals, decisions):
    """Przebieg z `retro-signals.json` i (opcjonalnie) `retro-decisions.json`: dict albo `_Unreadable`."""
    run = _parse_signals_file(name, signals)
    return _combine_run(run, {} if decisions is None else _parse_decisions_file(name, decisions))


def _unreadable_label(name):
    try:
        return _mask(_slug(name))
    except ValueError:
        return _opaque(name)


def _read_runs(root, skip_identity, budget=None, resolved=False, decisions=True):
    """Przebiegi z `.skillsync/runs/*` jednego źródła: (przebiegi, nazwy nieczytelnych, obcięcie do limitu).

    Katalogi bez plików retro są pomijane. Przebiegi o tożsamości bieżącego przebiegu (także `.N`) nie są czytane:
    nie są „innym” przebiegiem. Najnowsze nazwy idą pierwsze, a po `MAX_RUNS` skan się obcina.

    `resolved=True`: obce drzewo (główny checkout, inny projekt) z rozwiązanym korzeniem, pliki otwierane odpornie
    na podmianę katalogu pośredniego. `decisions=False`: tylko `retro-signals.json` (odczyt międzyprojektowy)."""
    budget = budget or ScanBudget()
    directory = _store(root)
    if directory is None:
        return [], [], False
    try:
        names = sorted(os.listdir(directory), reverse=True)
    except OSError:
        raise ValueError("Nie można odczytać magazynu przebiegów") from None
    runs, unreadable, count, truncated = [], [], 0, False
    files = (SIGNALS_FILE, DECISIONS_FILE) if decisions else (SIGNALS_FILE,)
    for name in names:
        folder = directory / name
        try:
            if not stat.S_ISDIR(folder.lstat().st_mode):
                continue  # pliki `*.json`/`*.lock` magazynu i dowiązania-katalogi nie są przebiegami retro
        except OSError:
            continue
        if _ATTEMPT_SUFFIX.sub("", name) == skip_identity:
            continue
        has = [(folder / file).is_symlink() or (folder / file).exists() for file in files]
        has += [False] * (2 - len(has))
        if not any(has):
            continue
        if count >= MAX_RUNS:
            truncated = True
            break
        size = 0
        for file in files:
            try:
                size += min((folder / file).lstat().st_size, MAX_RUN_FILE_BYTES)
            except OSError:
                pass
        if budget.bytes + size > MAX_SCAN_BYTES:
            truncated = True
            break
        budget.bytes += size
        count += 1
        # Pliki retro są oceniane osobno, żeby `budget.invalid_files[name]` liczył pliki nieczytelne albo niepoprawne
        # (plik sygnałów i plik decyzji); drugi plik jest czytany także po porażce pierwszego.
        try:
            _slug(name)
            named = True
        except ValueError:
            named = False
        bad, run, states = 0, None, {}
        if has[0]:
            try:
                run = _parse_signals_file(name, _json(folder / SIGNALS_FILE, MAX_RUN_FILE_BYTES, resolved)) \
                    if named else None
            except (_Unreadable, ValueError):
                run = None
            bad += run is None
        if has[1]:
            try:
                states = _parse_decisions_file(name, _json(folder / DECISIONS_FILE, MAX_RUN_FILE_BYTES, resolved)) \
                    if named else None
            except (_Unreadable, ValueError):
                states = None
            bad += states is None
        if not has[0] and has[1] and states is not None:
            bad += 1  # decyzje bez pliku sygnałów: przebieg niepoprawny, nawet gdy sam plik decyzji jest dobry
        if bad:
            unreadable.append(name)
            budget.invalid_files[name] = bad
        else:
            runs.append(_combine_run(run, states))
    budget.truncated = budget.truncated or truncated
    return runs, unreadable, truncated


def _read_source(root, skip_identity, budget=None, resolved=False):
    """Jedno źródło (korzeń projektu): przebiegi i rejestr. Rejestr `invalid` to `ValueError` (BLOCKED)."""
    runs, unreadable, truncated = _read_runs(root, skip_identity, budget, resolved)
    state, registry = _registry_full(root, resolved)
    if state == "invalid":
        raise ValueError("Rejestr decyzji jest niepoprawny; popraw go ręcznie")
    return {"root": root, "runs": runs, "unreadable": unreadable, "truncated": truncated, "registry_state": state,
            "registry": [entry for entry in registry
                         if _ATTEMPT_SUFFIX.sub("", entry["proposal"].rsplit("/", 1)[0]) != skip_identity]}


def library_signals(root, aliases, budget):
    """{(klucz kanoniczny, target)} sygnałów `repo: library` ze wszystkich przebiegów projektu (obce drzewo, tylko
    `retro-signals.json`). Wspólny czytnik `retro-scan --cross-project` i `retro --all` (`library_pairs`)."""
    runs, _, _ = _read_runs(root, None, budget, resolved=True, decisions=False)
    # Klucz z obcego pliku trafia do wyniku (`library_pairs`), więc po aliasie musi przejść `safe_cause_key`.
    # `_run_signal` go nie wymaga (porównania w skanie); lokalne klucze w wyniku `retro` (`proposals[].keys`)
    # przechodzą ten sam filtr w `retro._retro_section`.
    pairs = {(aliases.get(item["key"], item["key"]), item["target"]) for run in runs for item in run["signals"]
             if item["repo"] == "library" and item["target"] is not None}
    return {pair for pair in pairs if safe_cause_key(pair[0])}


def _proposals(sources):
    """Wszystkie propozycje ze stanem, {id: dict}. Źródła w kolejności pierwszeństwa; rejestr decyduje o stanie.

    Propozycja przebiegu bez wpisu w `retro-decisions.json` i bez wpisu w rejestrze nie ma stanu i odpada.
    Czas porządkujący: `created_at` przebiegu, a dla wpisu rejestru bez przebiegu `date` jako `T23:59:59Z`."""
    found = {}
    for source in sources:
        for run in source["runs"]:
            for number, item in run["proposals"].items():
                ident = run["id"] + "/" + number
                if ident not in found:
                    found[ident] = {"id": ident, "run": run["id"], "n": int(number[1:]), "created": run["created"],
                                    **item}
    for source in sources:
        for entry in source["registry"]:
            ident, run_id = entry["proposal"], entry["proposal"].rsplit("/", 1)[0]
            current = found.get(ident)
            if current is not None and current.get("registry"):
                continue  # wcześniejsze źródło (główne) wygrywa
            created = (current["created"] if current is not None else
                       datetime.combine(entry["date"], day_time(23, 59, 59), tzinfo=_UTC))
            found[ident] = {**(current or {}), "id": ident, "run": run_id, "n": int(ident.rsplit("/P", 1)[1]),
                            "created": created, "repo": entry["repo"], "target": entry["target"], "state": entry["decision"],
                            "ref": entry["ref"], "registry": True}
    return {ident: item for ident, item in found.items() if item["state"] is not None}


def _order(item):
    return item["created"], item["run"], item["n"]


# ------------------------------------------------------------------------------------------------- git (krok d)

class _Git:
    """Sprawdzenia `merge-base --is-ancestor` z limitem `GIT_TIMEOUT` na wywołanie i budżetem `GIT_BUDGET` na skan."""

    def __init__(self):
        self.deadline = time.monotonic() + _retro.GIT_BUDGET
        self.exceeded = False
        self.cache = {}

    def _run(self, root, *args):
        result = self._call(root, *args)
        return None if result is None else result.returncode

    def output(self, root, *args):
        """Standardowe wyjście (tekst) udanego polecenia git albo None (błąd, limit czasu, budżet)."""
        result = self._call(root, *args, capture=True)
        if result is None or result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", "replace")

    def _call(self, root, *args, capture=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.exceeded = True
            return None
        try:
            return subprocess.run(["git", "-C", str(root), *args],
                                  stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, env=_git_env(),
                                  timeout=min(_retro.GIT_TIMEOUT, remaining))
        except subprocess.TimeoutExpired:
            if remaining < _retro.GIT_TIMEOUT:  # wygasł budżet skanu, nie limit jednego wywołania
                self.exceeded = True
            return None
        except (OSError, subprocess.SubprocessError):
            return None

    def verdict(self, root, ref):
        """`yes` (ref w historii HEAD), `no` (nie należy do historii) albo `unverified`."""
        if root is None or not isinstance(ref, str) or not FULL_SHA.fullmatch(ref):
            return "unverified"
        key = (str(root), ref)
        if key not in self.cache:
            code = self._run(root, "merge-base", "--is-ancestor", ref, "HEAD")
            if code == 0:
                result = "yes"
            elif code == 1:
                result = "no"
            elif code is None:
                result = "unverified"
            else:  # np. nieznany obiekt: `no` tylko, gdy to repozytorium działa i ref nie jest w nim commitem
                result = "no" if self._run(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}") == 1 \
                    else "unverified"
            self.cache[key] = result
        return self.cache[key]


def _target_root(pair, project, layerset):
    """Korzeń repozytorium celu: dla `project` korzeń projektu, dla `library` jedyna warstwa zawierająca `target`."""
    repo, target = pair
    if repo == "project":
        return project
    try:
        found = layerset.providers(target)
    except (_layers.LayerError, ValueError, OSError):
        return None
    return found[0][0].root if len(found) == 1 else None


# ------------------------------------------------------------------------------ źródła spoza bieżącego drzewa

def _main_source(root, git, identity, budget):
    """K3: (czy powiązany worktree, źródło głównego checkoutu albo None, ostrzeżenia).

    Główny checkout to pierwszy wpis `git worktree list --porcelain` z prefiksem projektu. Wymaga manifestu
    `.github/.skillsync.yaml`, poprawnego magazynu (`_store`) i rejestru; inaczej `source-skipped:main-checkout`."""
    skipped = ["source-skipped:main-checkout"]
    result = git._call(root, "rev-parse", "--git-dir", "--git-common-dir", "--show-prefix", capture=True)
    if result is not None and result.returncode == 128:
        return False, None, []  # rc 128 (fatal: not a git repository), rozpoznane po kodzie: zwykły projekt bez gita
    if result is None or result.returncode != 0:
        return False, None, skipped  # błąd, limit czasu albo budżet: nie wiemy, czy to worktree
    lines = result.stdout.decode("utf-8", "replace").split("\n")
    if len(lines) < 3:
        return False, None, skipped
    try:
        if (root / lines[0]).resolve() == (root / lines[1]).resolve():
            return False, None, []
    except (OSError, RuntimeError, ValueError):
        return False, None, skipped
    listing = git.output(root, "worktree", "list", "--porcelain")
    first = (listing or "").split("\n", 1)[0]
    if not first.startswith("worktree "):
        return True, None, skipped
    try:
        main_root = (Path(first[len("worktree "):]) / lines[2]).resolve()
        manifest = main_root / ".github" / ".skillsync.yaml"
        if main_root == root or not main_root.is_dir() or manifest.is_symlink() or not manifest.is_file():
            return True, None, skipped
        return True, _read_source(main_root, identity, budget, resolved=True), []
    except Exception:  # błąd obcego źródła nigdy nie przerywa skanu: źródło jest pomijane
        return True, None, skipped


def linked_worktree(root):
    """True, gdy `root` jest powiązanym worktree (git-dir różny od common-dir); False także dla katalogu bez gita
    (rc 128). Inny błąd git to `ValueError`: bez pewności decyzje nie zapadają."""
    git = _Git()
    result = git._call(root, "rev-parse", "--git-dir", "--git-common-dir", capture=True)
    if result is not None and result.returncode == 128:
        # To strażnik zapisu: 128 znaczy "nie repozytorium" tylko, gdy w korzeniu ani przodkach nie ma wpisu `.git`
        # (np. `dubious ownership` też daje 128 przy istniejącym `.git`: brak pewności = blokada).
        for directory in (root, *root.parents):
            try:
                os.lstat(directory / ".git")
            except FileNotFoundError:
                continue
            except OSError:
                pass
            raise ValueError("Nie można ustalić, czy projekt jest worktree")
        return False
    if result is None or result.returncode != 0:
        raise ValueError("Nie można ustalić, czy projekt jest worktree")
    lines = result.stdout.decode("utf-8", "replace").split("\n")
    if len(lines) < 2:
        raise ValueError("Nie można ustalić, czy projekt jest worktree")
    try:
        return (root / lines[0]).resolve() != (root / lines[1]).resolve()
    except (OSError, RuntimeError):
        raise ValueError("Nie można ustalić, czy projekt jest worktree") from None


def _cross_project(root, git, aliases, budget):
    """K4: ({(klucz, target): liczba innych projektów}, liczba przeczytanych projektów, ostrzeżenia).

    Projekty jak w `retro --all` (`discover_paths`, bez ścieżek z `discover_exclude`, worktree raz), bez bieżącego projektu; najwyżej
    `MAX_PROJECTS`. Wynik zawiera tylko liczby: żadnych nazw, ścieżek ani id przebiegów."""
    unchecked = []
    chosen = _project_identities(_discover(_machine_config(), unchecked), git.deadline)  # stały `reason` przy błędzie konfiguracji
    if time.monotonic() > git.deadline:
        git.exceeded = True
    chosen.pop(_identity(root, git.deadline)[0], None)
    counts, read, warnings = {}, 0, ["source-skipped:project"] if unchecked else []
    paths = []
    for path, _ in chosen.values():
        try:
            if Path(path).resolve() != root:
                paths.append(path)
        except (OSError, RuntimeError):
            warnings = ["source-skipped:project"]
    paths.sort()
    if len(paths) > MAX_PROJECTS:
        budget.truncated = True
    for path in paths[:MAX_PROJECTS]:
        try:
            pairs = library_signals(Path(path).resolve(), aliases, budget)
        except Exception:  # błąd jednego obcego projektu nie przerywa reszty
            warnings = ["source-skipped:project"]
            continue
        read += 1
        for pair in pairs:
            counts[pair] = counts.get(pair, 0) + 1
    return counts, read, warnings


# ---------------------------------------------------------------------------------------------------- algorytm

def _scan(args):
    root, _, layerset = load_layer_project(args.path, args.layers)
    root = Path(root).resolve()
    run_id = _slug(args.run_id) if isinstance(args.run_id, str) else None
    if run_id is None:
        raise ValueError("Wymagany --run-id")
    signals = _parse_input(_read_input(root, run_id, args.signals), run_id)
    keys = _cause_keys(layerset)
    aliases = keys[0] if keys else {}
    warnings = [] if keys else ["no-cause-keys"]
    if keys:
        warnings += ["unknown-key:" + item["id"] for item in signals if aliases.get(item["key"], item["key"]) not in keys[1]]

    identity = _ATTEMPT_SUFFIX.sub("", run_id)
    git, budget = _Git(), ScanBudget()
    local = _read_source(root, identity, budget)
    worktree, main, skipped = _main_source(root, git, identity, budget)
    warnings += skipped
    sources = [local] if main is None else [main, local]  # kolejność = pierwszeństwo: główny checkout wygrywa
    unreadable = sorted({name for source in sources for name in source["unreadable"]})
    warnings += ["unreadable-run:" + _unreadable_label(name) for name in unreadable]
    cross, projects = {}, None
    if getattr(args, "cross_project", False):
        cross, projects, skipped = _cross_project(root, git, aliases, budget)
        warnings += skipped
    truncated = budget.truncated or any(source["truncated"] for source in sources)

    unique = {}
    for source in sources:
        for run in source["runs"]:
            unique.setdefault(run["id"], run)
    runs = sorted(unique.values(), key=lambda run: (run["created"], run["id"]))
    by_pair = {}
    for item in sorted(_proposals(sources).values(), key=_order):
        by_pair.setdefault((item["repo"], item["target"]), []).append(item)

    results, groups, group_of = [], [], {}
    for signal in signals:
        canonical = aliases.get(signal["key"], signal["key"])
        pair = (signal["repo"], signal["target"])
        repeat_of = _repeat_of(runs, canonical, pair, aliases) if signal["target"] is not None else None
        # K4: powtórzenie w innym projekcie dotyczy tylko `repo: library`; `repeat_of` zostaje lokalne.
        elsewhere = cross.get((canonical, signal["target"]), 0) if signal["repo"] == "library" else 0
        result = {"id": signal["id"], "repeat_of": repeat_of, "cross_project_repeats": elsewhere,
                  "attached_to": None, "group": None}
        results.append(result)
        if signal["from_failure"] or signal["target"] is None:  # (a)
            continue
        history = by_pair.get(pair, [])
        unresolved = [item for item in history if item["state"] in UNRESOLVED]
        if unresolved:  # (b)
            result["attached_to"] = unresolved[-1]["id"]
            continue
        last = history[-1] if history else None
        correction, repeated = canonical == "user-correction", repeat_of is not None or elsewhere > 0
        rejected = recurrence = None
        if last is not None and last["state"] == "rejected":  # (c)
            if not correction:
                continue
            rejected = last["id"]
        elif last is not None and last["state"] == "applied":  # (d)
            verdict = git.verdict(_target_root(pair, root, layerset), last["ref"])
            if verdict == "yes":
                recurrence = last["id"]
            else:
                code = "ref-not-in-history:" if verdict == "no" else "ref-unverified:"
                if code + _mask(last["id"]) not in warnings:
                    warnings.append(code + _mask(last["id"]))
                if not (correction or repeated):  # jak bez `applied`: (e)-(f)
                    continue
        elif not (correction or repeated):  # (e), (f)
            continue
        if pair not in group_of:
            group_of[pair] = {"id": "G%d" % (len(groups) + 1), "repo": pair[0], "target": _mask(pair[1]),
                              "signals": [], "previously_rejected": None, "recurrence_of": None}
            groups.append(group_of[pair])
        group = group_of[pair]
        group["signals"].append(signal["id"])
        group["previously_rejected"] = group["previously_rejected"] or (rejected and _mask(rejected))
        group["recurrence_of"] = group["recurrence_of"] or (recurrence and _mask(recurrence))
        result["group"] = group["id"]

    if git.exceeded or truncated:
        warnings.append("truncated")
    for result in results:
        for field in ("repeat_of", "attached_to"):
            result[field] = _mask(result[field]) if result[field] else None
    states = [source["registry_state"] for source in sources]
    return {"schema": 1, "status": "PASS", "run_id": _mask(run_id), "worktree": worktree,
            "cross_project": projects is not None,
            "signals": results, "groups": groups,
            "sources": {"runs": len(local["runs"]), "unreadable": len(unreadable),
                        "registry": "available" if "available" in states else "absent",
                        "main_checkout": None if main is None else len(main["runs"]), "projects": projects},
            "truncated": bool(git.exceeded or truncated), "warnings": warnings}


def _repeat_of(runs, key, pair, aliases):
    """`<run-id>/S<n>` sygnału o tym samym kluczu i celu w innym przebiegu: najnowszy przebieg (`created_at`, potem
    id), w nim sygnał o najniższym numerze."""
    best = None
    for run in runs:  # rosnąco po (created, id), więc ostatni dopasowany jest najnowszy
        found = [item["id"] for item in run["signals"]
                 if aliases.get(item["key"], item["key"]) == key and (item["repo"], item["target"]) == pair]
        if found:
            best = run["id"] + "/" + min(found, key=lambda ident: int(ident[1:]))
    return best


def retro_scan(args):
    try:
        report = _scan(args)
    except OSError:  # komunikat OSError zawiera ścieżkę: stały powód
        report = {"schema": 1, "status": "BLOCKED", "reason": "Błąd odczytu pliku"}
    except (ValueError, TypeError, KeyError, RecursionError, yaml.YAMLError) as error:
        report = {"schema": 1, "status": "BLOCKED", "reason": " ".join(str(error).split())[:500] or "Odmowa"}
    return emit(report, args.json)


def register(subparsers):
    parser = subparsers.add_parser(
        "retro-scan", help="Read-only threshold scan of retrospective signals against earlier proposals (JSON)",
        description="Tylko do odczytu. Czyta plik sygnałów bieżącego przebiegu (--signals, ścieżka względna do "
                    "korzenia projektu, w .skillsync/runs/<run-id>/, do 256 KB), zestawia go z "
                    "retro-signals.json i retro-decisions.json innych przebiegów oraz z sekcją proposals: rejestru "
                    "docs/retro/decisions.yaml i stosuje algorytm progu i stanu z retro-quality.md (kroki 1-5). "
                    "Wynik: grupy sygnałów (propozycje do utworzenia), repeat_of, attached_to i ostrzeżenia. "
                    "Błędne wejście, niepoprawny rejestr: BLOCKED z kodem 2.")
    add_project_arguments(parser)
    parser.add_argument("--run-id", help="Id bieżącego przebiegu (musi równać się run_id w pliku --signals)")
    parser.add_argument("--signals", help="Plik wejściowy sygnałów, względny do korzenia projektu")
    parser.add_argument("--cross-project", action="store_true",
                        help="Sygnał repo: library liczy się jako powtórzony także przy tym samym kluczu i celu w "
                             "innym projekcie z discover_paths (wynik zawiera tylko liczby)")
    parser.error = _json_error
    parser.set_defaults(func=retro_scan)
