"""Local checkpoints and measured, offline tool benchmarks."""

import hashlib
import json
import math
import os
import shlex
from pathlib import Path
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from .common import load_layer_project, read_project
from . import layers as _layers
from .quality import _redact
from .selfcmd import self_argv

STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
MAX_RECORDS = 1000
MAX_METADATA = 2 * 1024 * 1024
OWNERS = ("instruction", "skill", "hook", "route", "test")
RETRO_QUALITY_SINCE = "2026-11-05"  # od tego dnia UTC (rekord `final`) brak retrospektywy jest liczony w `retro`
_RUN_ATTEMPT = re.compile(r"\.[0-9]+$")


def run_identity(run_id):
    """Tożsamość przebiegu: `run_id` bez końcowego `.<liczba>` (`x.2` to ten sam przebieg co `x`)."""
    return _RUN_ATTEMPT.sub("", run_id)


def _recorded_at(record):
    """`recorded_at` rekordu jako aware UTC albo None (brak, zły format, poza zakresem)."""
    try:
        moment = datetime.fromisoformat(record["recorded_at"])
        return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


def retrospective(records):
    """K9: (czas pierwszego rekordu `final` albo None, czy przed nim jest rekord `retro-quality` PASS).

    `records` to rekordy całej tożsamości (`<id>.json` i `<id>.N.json`). Wspólne dla `checkpoint --final`
    (ostrzeżenie) i `retro` (`coverage.retro.final_without_retro`). Rekord bez poprawnego `recorded_at` nie liczy się."""
    finals = [moment for record in records if isinstance(record, dict) and record.get("final") is True
              for moment in [_recorded_at(record)] if moment is not None]
    if not finals:
        return None, False
    final_at = min(finals)
    return final_at, any(isinstance(record, dict) and record.get("step") == "retro-quality"
                         and record.get("status") == "PASS" and _recorded_at(record) is not None
                         and _recorded_at(record) < final_at for record in records)


def _retro_warnings(path, run_id, records):
    """Ostrzeżenia K9 dla zapisywanego rekordu `final`: brak retrospektywy w tożsamości (z plikami siostrzanymi)."""
    identity = run_identity(run_id)
    pattern = re.compile(re.escape(identity) + r"(\.[0-9]+)?\.json")
    combined, unreadable = list(records), False
    try:
        names = sorted(os.listdir(path.parent))
    except OSError:
        names, unreadable = [], True
    for name in names:
        if name == path.name or not pattern.fullmatch(name):
            continue
        try:
            sibling = _read(path.parent / name)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            unreadable = True
            continue
        if "benchmark" not in sibling:
            combined += sibling["checkpoints"]
    if retrospective(combined)[1]:
        return []
    if unreadable:
        return ["retro-quality-unverified: nie udało się odczytać pliku siostrzanego przebiegu, więc brak retrospektywy "
                "nie jest pewny"]
    return ["retro-quality-missing: przebieg nie ma rekordu retro-quality PASS przed final"]


def _slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("Id musi być krótkim identyfikatorem bez separatorów ścieżki")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Pomiar musi być liczbą")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError("Pomiar musi być skończoną nieujemną liczbą") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError("Pomiar musi być skończoną nieujemną liczbą")
    return value


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(root, value, existing=True):
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("Ścieżka zawiera traversal")
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Ścieżka wychodzi poza projekt")
    if existing and not resolved.is_file():
        raise ValueError("Brak pliku: " + str(value))
    return resolved


def fingerprint(root):
    """Hash HEAD, both diffs and nonignored untracked bytes; never follow links."""
    root = Path(root).resolve()
    def git(*argv, allow_empty=False):
        result = subprocess.run(["git", "-C", str(root), *argv], capture_output=True)
        if result.returncode and not allow_empty:
            raise ValueError("Fingerprint wymaga dostępnego repozytorium git")
        return result.stdout if not result.returncode else b""
    head = git("rev-parse", "HEAD", allow_empty=True).decode().strip() or None
    git("rev-parse", "--show-toplevel")
    digest = hashlib.sha256()
    for staged in (False, True):
        command = ["diff", "--binary", "--no-ext-diff", "--no-textconv"]
        if staged:
            command.append("--cached")
        command.extend(["--", ".", ":(exclude).skillsync/**"])
        digest.update(b"staged" if staged else b"worktree")
        digest.update(git(*command))
    names = git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    for raw in sorted(filter(None, names)):
        name = os.fsdecode(raw)
        if Path(name).parts[0] == ".skillsync":
            continue
        path = root / name
        digest.update(raw + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif not path.resolve().is_relative_to(root):
            raise ValueError("Plik git wychodzi poza projekt")
        elif path.is_file():
            digest.update(_hash_file(path).encode())
        else:
            raise ValueError("Nie można odczytać pliku fingerprintu")
    return {"head": head, "digest": digest.hexdigest()}


def _store_path(root, run_id):
    path = root / ".skillsync" / "runs" / (_slug(run_id) + ".json")
    for parent in (root / ".skillsync", path.parent, path):
        if parent.is_symlink():
            raise ValueError("Magazyn przebiegów nie może być dowiązaniem")
    return path


def _extra_fields(record):
    """Opcjonalne pola R2 (final, cause_key, owner, denied, inherited); wspólne dla zapisu i odczytu."""
    if "final" in record and (record["final"] is not True or record.get("status") != "PASS"):
        raise ValueError("Znacznik final jest dozwolony tylko jako true przy statusie PASS")
    if "cause_key" in record:
        key = record["cause_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", key):
            raise ValueError("Klucz przyczyny musi pasować do [a-z0-9][a-z0-9-]{0,47}")
        # Długi segment bez myślnika wygląda jak token, nie jak klucz.
        if any(len(segment) >= 32 for segment in key.split("-")):
            raise ValueError("Klucz przyczyny wygląda jak token")
    if "owner" in record:
        if not isinstance(record["owner"], str) or record["owner"] not in OWNERS:
            raise ValueError("Właściciel musi być jednym z: " + ", ".join(OWNERS))
        if "cause_key" not in record:
            raise ValueError("Właściciel wymaga klucza przyczyny")
    for name in ("denied", "inherited"):
        if name in record:
            value = record[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Pole " + name + " musi być liczbą całkowitą nieujemną")


def _unique_pairs(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("Zduplikowany klucz w pliku przebiegu")
    return dict(pairs)


def _read(path):
    if path.is_symlink():
        raise ValueError("Przebieg nie może być dowiązaniem")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"schema_version": 1, "checkpoints": []}
    # Przed otwarciem: FIFO lub urządzenie zawiesiłoby odczyt.
    if not stat.S_ISREG(mode):
        raise ValueError("Przebieg nie jest zwykłym plikiem")
    if path.stat().st_size > MAX_METADATA:
        raise ValueError("Przekroczono limit metadanych")
    try:
        data = json.loads(path.read_text(), object_pairs_hook=_unique_pairs)
    except RecursionError:
        raise ValueError("Przebieg jest zbyt głęboko zagnieżdżony") from None
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("checkpoints"), list):
        raise ValueError("Nieobsługiwany schemat przebiegu")
    if len(data["checkpoints"]) > MAX_RECORDS:
        raise ValueError("Przekroczono limit rekordów")
    for record in data["checkpoints"]:
        if not isinstance(record, dict):
            raise ValueError("Nieprawidłowy rekord checkpointu")
        _slug(record.get("step"))
        if record.get("status") not in STATUSES or not isinstance(record.get("next"), str):
            raise ValueError("Nieprawidłowy status lub następny krok")
        if len(record["next"]) > 2000 or not isinstance(record.get("recorded_at"), str):
            raise ValueError("Nieprawidłowe metadane checkpointu")
        try:
            datetime.fromisoformat(record["recorded_at"])
        except ValueError:
            raise ValueError("Nieprawidłowa data checkpointu") from None
        if not isinstance(record.get("provenance"), str):
            raise ValueError("Brak źródła pomiaru")
        if "sources_digest" in record and not re.fullmatch(r"[0-9a-f]{64}", str(record["sources_digest"])):
            raise ValueError("Nieprawidłowy fingerprint źródeł")
        cause = record.get("cause")
        if cause is not None and (not isinstance(cause, str) or len(cause) > 500):
            raise ValueError("Nieprawidłowa przyczyna")
        attempt = record.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("Nieprawidłowy numer próby")
        for key in ("cost", "duration_seconds"):
            if key not in record:
                raise ValueError("Brak pola pomiaru")
            if record[key] is not None:
                record[key] = _number(record[key])
        if "cost_unit" in record and not re.fullmatch(r"[A-Z]{3}", str(record["cost_unit"])):
            raise ValueError("Jednostka kosztu musi być trzyznakowym kodem waluty")
        _extra_fields(record)
        state = record.get("fingerprint")
        if not isinstance(state, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(state.get("digest", ""))):
            raise ValueError("Nieprawidłowy fingerprint")
        if state.get("head") is not None and not re.fullmatch(r"[0-9a-f]{40,64}", str(state["head"])):
            raise ValueError("Nieprawidłowy HEAD")
        evidence = record.get("evidence")
        if not isinstance(evidence, list) or (record["status"] == "PASS" and not evidence):
            raise ValueError("Nieprawidłowe dowody checkpointu")
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("Nieprawidłowy dowód")
            if "\x00" in item["path"]:
                raise ValueError("Nieprawidłowa ścieżka dowodu")
            if Path(item["path"]).is_absolute() or ".." in Path(item["path"]).parts:
                raise ValueError("Nieprawidłowa ścieżka dowodu")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise ValueError("Nieprawidłowy hash dowodu")
    if "benchmark" in data:
        if data["checkpoints"]:
            raise ValueError("Plik benchmarku nie może zawierać checkpointów")
        bench = data["benchmark"]
        if not isinstance(bench, dict) or not isinstance(bench.get("cases"), list):
            raise ValueError("Nieprawidłowy rekord benchmarku")
        for case in bench["cases"]:
            if not isinstance(case, dict) or case.get("status") not in STATUSES:
                raise ValueError("Nieprawidłowy wynik benchmarku")
            if "duration_seconds" in case:
                case["duration_seconds"] = _number(case["duration_seconds"])
    return data


def _write(path, data):
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if len(payload.encode()) > MAX_METADATA:
        raise ValueError("Przekroczono limit metadanych")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def _emit(args, result):
    def clean(value):
        if isinstance(value, str):
            return _redact(value)
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    result = clean(result)
    # Suma pomiarów poza zakresem float to błąd, nie `Infinity` (niepoprawny JSON) przy exit 0.
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def _sources(root, layers=None):
    """Skrót źródeł instrukcji workflow projektu: pliki projektu i aktywnych warstw (`LayerSet`, K1).

    Dozwolone położenia: projekt, korzeń każdej aktywnej warstwy i pamięć kompozytów skilli (wpisy
    kompozytu to dowiązania do plików warstw). Pliki warstw czytane przez `layer_files` (bez dowiązań, D73)."""
    _, manifest, layerset = load_layer_project(root, layers)
    allowed = [Path(root).resolve()] + [Path(os.path.realpath(path)) for path in layerset.roots]
    composed = Path(os.path.realpath(_layers.composed_root()))

    def inside(path, composite=False):
        return any(path.is_relative_to(base) for base in allowed) or (composite and path.is_relative_to(composed))

    digest = hashlib.sha256()
    sources = [root / ".github/.skillsync.yaml", root / "AGENTS.md", root / "CLAUDE.md",
               root / ".github/copilot-instructions.md", root / ".codex/skillsync-instructions.md"]
    sources.extend(path for _layer, path in layerset.providers("codex.yaml"))  # each layer's own file, no links
    for layer in layerset.layers:
        for directory in ("workflow", "instructions", "agents"):
            sources.extend(layer.root / directory / rel for rel in _layers.layer_files(layer, directory)
                           if Path(rel).suffix in (".md", ".yaml", ".json", ".toml"))
        for skill in manifest.get("skills", []):
            if isinstance(skill, str) and re.fullmatch(r"[A-Za-z0-9_-]+", skill):
                sources.extend(layer.root / "skills" / skill / rel for rel in _layers.layer_files(layer, f"skills/{skill}")
                               if Path(rel).suffix in (".md", ".yaml", ".json"))
    # Installed adapters are the canonical expanded skill set (tags, wildcards, companions).
    for adapter in (".agents/skills", ".github/skills", ".claude/skills"):
        directory = root / adapter
        if directory.exists():
            for skill in directory.iterdir():
                actual = skill.resolve()
                if not inside(actual, composite=True):
                    raise ValueError("Zainstalowany skill wychodzi poza projekt, warstwy lub kompozyt")
                if actual.is_dir():
                    sources.extend(p for p in actual.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for source in sorted(set(sources)):
        digest.update(str(source).encode())
        if source.exists():
            resolved = source.resolve()
            if not inside(resolved):
                if resolved.is_relative_to(composed):
                    # Zwykły plik w kompozycie skilla (przed adopcją, K4): BLOCKED z instrukcją, plik zostaje.
                    raise ValueError(f"Plik {resolved} w kompozycie skilla nie należy jeszcze do żadnej warstwy; "
                                     "uruchom `skillsync link` (adopcja do warstwy właściciela) i powtórz")
                raise ValueError("Źródło instrukcji wychodzi poza warstwy lub projekt")
            digest.update(_hash_file(source).encode())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def checkpoint(args):
    root, _ = read_project(args.path)
    path = _store_path(Path(root).resolve(), args.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError("Przebieg jest zablokowany przez inny zapis; po awarii sprawdź właściciela blokady") from None
    try:
        return _checkpoint_locked(args)
    finally:
        lock.rmdir()


def _checkpoint_locked(args):
    root, _ = read_project(args.path)
    root = Path(root).resolve()
    path = _store_path(root, args.run_id)
    data = _read(path)
    if "benchmark" in data:
        raise ValueError("Plik benchmarku nie przyjmuje checkpointów")
    if len(data["checkpoints"]) >= MAX_RECORDS:
        raise ValueError("Przekroczono limit checkpointów")
    evidence = []
    for name in args.evidence or []:
        item = _inside(root, name)
        evidence.append({"path": str(item.relative_to(root)), "sha256": _hash_file(item)})
    if args.status == "PASS" and not evidence:
        raise ValueError("PASS wymaga pliku z rzeczywistym dowodem; sama deklaracja nie wystarcza")
    if len(args.next) > 2000 or len(args.cause or "") > 500:
        raise ValueError("Opis przekracza limit metadanych")
    record = {"step": _slug(args.step), "status": args.status, "evidence": evidence,
              "next": _redact(args.next), "fingerprint": fingerprint(root), "sources_digest": _sources(root, getattr(args, "layers", None)),
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "duration_seconds": None if args.duration is None else _number(args.duration),
              "cost": None if args.cost is None else _number(args.cost),
              "cost_unit": getattr(args, "cost_unit", "USD"),
              "attempt": args.attempt, "cause": _redact(args.cause) if args.cause else None,
              "provenance": "operator-recorded; evidence content is not automatically validated"}
    # Pola opcjonalne tylko, gdy podane; brak flagi = brak pola.
    for name in ("denied", "inherited", "cause_key", "owner"):
        if getattr(args, name, None) is not None:
            record[name] = getattr(args, name)
    if getattr(args, "final", False):
        record["final"] = True
    _extra_fields(record)
    if args.attempt < 1:
        raise ValueError("Numer próby musi być dodatni")
    if not re.fullmatch(r"[A-Z]{3}", record["cost_unit"]):
        raise ValueError("Jednostka kosztu musi być trzyznakowym kodem waluty")
    data.update({"run_id": args.run_id, "project": str(root)})
    data["checkpoints"].append(record)
    warnings = _write_warnings(data["checkpoints"], record["step"], record.get("final") is True)
    if record.get("final") is True:
        warnings += _retro_warnings(path, args.run_id, data["checkpoints"])
    _write(path, data)
    result = {"status": args.status, "checkpoint": str(path), "record": record}
    if warnings:
        result["warnings"] = warnings
    _emit(args, result)
    return 0


def resume(args):
    root, _ = read_project(args.path)
    root = Path(root).resolve()
    data = _read(_store_path(root, args.run_id))
    if not data["checkpoints"]:
        raise ValueError("Brak checkpointu do wznowienia")
    last = data["checkpoints"][-1]
    stale = []
    if last["fingerprint"] != fingerprint(root):
        stale.append("Zmienił się HEAD lub staged/unstaged/untracked stan projektu")
    if last.get("sources_digest") != _sources(root, getattr(args, "layers", None)):
        stale.append("Zmieniły się źródła konfiguracji lub instrukcji workflow")
    for item in last["evidence"]:
        try:
            actual = _hash_file(_inside(root, item["path"]))
        except (ValueError, OSError):
            actual = None
        if actual != item["sha256"]:
            stale.append("Dowód zmieniony lub niedostępny: " + item["path"])
    incomplete = last["status"] != "PASS"
    reasons = list(stale)
    if incomplete:
        reasons.append("Poprzedni krok nie został zaliczony: " + last["status"])
    result = {"run_id": args.run_id, "status": "BLOCKED" if stale or incomplete else "PASS",
              "previous_status": last["status"],
              "stale": bool(stale), "reasons": reasons, "step": last["step"],
              "next": "Wykonaj brakującą weryfikację i zapisz nowy checkpoint. " + last["next"] if stale or incomplete else last["next"]}
    _emit(args, result)
    return 2 if stale or incomplete else 0


def _label(record):
    """Etykieta przyczyny rekordu: tekst po strip, a gdy pusty, klucz; inaczej None."""
    cause = record.get("cause")
    if isinstance(cause, str) and cause.strip():
        return cause.strip()
    key = record.get("cause_key")
    return key if isinstance(key, str) and key else None


def _is_problem(record, repeat):
    """Rekord problemu: FAIL/BLOCKED/NOT_RUN, powtórzenie kroku albo attempt > 1 (wspólne dla klas i `retro`)."""
    return repeat or record["status"] in ("FAIL", "BLOCKED", "NOT_RUN") or record["attempt"] > 1


def _write_warnings(records, current, final):
    """Ostrzeżenia dla zapisu: zaliczony krok, który `_classify_run` uzna za niewyjaśniony. Rekord `--final` obejmuje wszystkie kroki."""
    _, steps = _classify_run(None, records)
    warnings = []
    for name, step in steps.items():
        if (not final and name != current) or not step["problem"] or step["last_status"] != "PASS" or step["explained"]:
            continue
        if step["reused"]:
            reason = "dowód PASS jest ten sam co przy porażce"
            fix = "nowym dowodem" if step["labels"] else "nowym dowodem i --cause lub --cause-key"
        else:
            reason = "problem bez przyczyny"
            fix = "--cause lub --cause-key"
        warnings.append(f"Krok '{name}': {reason}, więc tak zakończony przebieg dostanie klasę unexplained; "
                        f"dopisz rekord tego kroku z {fix}")
    return warnings


def _classify_run(ident, records):
    """Klasyfikacja jednego przebiegu; zwraca (pozycja, kroki) dla `_classify` i `retro`.

    Krok: {"records", "problem", "labels", "explained", "last_status"}. Rekord problemu to FAIL/BLOCKED/NOT_RUN,
    powtórzenie kroku albo attempt > 1; przyczyna kroku wystarcza na poziomie kroku."""
    steps = {}
    repeated = failed = not_run = 0
    for record in records:
        step = steps.get(record["step"])
        repeat = step is not None
        if step is None:
            step = steps[record["step"]] = {"records": [], "problem": False, "labels": set(),
                                            "explained": False, "last_status": None, "reused": False,
                                            "_seen": set()}
        problem = _is_problem(record, repeat)
        repeated += repeat
        failed += record["status"] in ("FAIL", "BLOCKED")
        not_run += record["status"] == "NOT_RUN"
        # Końcowy PASS z dowodami bajtowo równymi dowodom wcześniejszych rekordów innych niż PASS nie dowodzi odzysku.
        step["reused"] = (record["status"] == "PASS" and bool(record["evidence"]) and
                          {item["sha256"] for item in record["evidence"]} <= step["_seen"])
        if problem and record["status"] != "PASS":
            step["_seen"].update(item["sha256"] for item in record["evidence"])
        step["records"].append(record)
        step["last_status"] = record["status"]
        if problem:
            step["problem"] = True
            label = _label(record)
            if label is not None:
                step["labels"].add(label)
    for step in steps.values():
        step["labels"] = sorted(step["labels"])
        step["explained"] = bool(step["labels"]) and not step["reused"]
        del step["_seen"]
    causes = sorted({label for step in steps.values() for label in step["labels"]})
    complete = bool(records) and all(record.get("duration_seconds") is not None for record in records)
    final = records[-1]["status"] if records else None
    problems = [step for step in steps.values() if step["problem"]]
    if not records:
        kind = "no_data"
    elif any(step["last_status"] != "PASS" for step in steps.values()) or records[-1].get("final") is not True:
        kind = "incomplete"
    elif any(not step["explained"] for step in problems):
        kind = "unexplained"
    else:
        kind = "recovery" if problems else "clean"
    item = {"id": ident, "class": kind, "steps": len(steps), "repeated_steps": repeated,
            "failed_records": failed, "not_run_records": not_run, "final_status": final,
            "duration_seconds": sum(r["duration_seconds"] for r in records) if complete else None,
            "causes": causes, "_cost": bool(records) and all(r.get("cost") is not None for r in records)}
    return item, steps


def _classify(runs, skipped, empty_dirs=(), symlinks=0, invalid_dirs=0):
    """Deterministic classification from recorded checkpoints only; no causal claims.

    `runs` to lista (id, dane przebiegu); `empty_dirs` to id katalogów bez przypisanego pliku (no_data)."""
    items = [_classify_run(ident, run["checkpoints"])[0] for ident, run in runs]
    items.extend(_classify_run(ident, [])[0] for ident in empty_dirs)
    clean = sorted(item["duration_seconds"] for item in items
                   if item["class"] == "clean" and item["duration_seconds"] is not None)
    baseline = None
    if clean:
        mid = len(clean) // 2
        baseline = clean[mid] if len(clean) % 2 else (clean[mid - 1] + clean[mid]) / 2
    ranking = {}
    for item in items:
        if item["class"] != "recovery":
            continue
        excess = None
        if baseline is not None and item["duration_seconds"] is not None:
            excess = max(0, item["duration_seconds"] - baseline) / len(item["causes"])
        for cause in item["causes"]:
            entry = ranking.setdefault(cause, {"cause": cause, "runs": 0, "runs_with_excess": 0,
                                                    "excess_seconds": None})
            entry["runs"] += 1
            if excess is not None:
                entry["runs_with_excess"] += 1
                entry["excess_seconds"] = (entry["excess_seconds"] or 0) + excess
    ordered = sorted(ranking.values(), key=lambda e: (e["excess_seconds"] is None,
                     -(e["excess_seconds"] or 0), -e["runs"], e["cause"]))
    coverage = {"runs": len(items),
                "with_complete_durations": sum(item["duration_seconds"] is not None for item in items),
                "with_cost": sum(item.pop("_cost") for item in items),
                "with_causes": sum(bool(item["causes"]) for item in items),
                "benchmark_runs_skipped": skipped,
                "no_data": sum(item["class"] == "no_data" for item in items),
                "symlinks_skipped": symlinks, "invalid_dirs": invalid_dirs}
    return {"runs": items, "baseline_seconds": baseline, "cause_ranking": ordered, "coverage": coverage,
            "notes": "Wyłącznie zapisane pomiary; klasyfikacja nie ustala przyczyn ani oszczędności."}


def _run_id(path):
    """Id przebiegu z nazwy pliku; nazwa niezgodna z `_slug` nie trafia do wyjścia, tylko jej skrót."""
    try:
        return _slug(path.stem)
    except ValueError:
        return "invalid-name:" + hashlib.sha256(os.fsencode(path.name)).hexdigest()[:12]


def _scan_dirs(directory):
    """Katalogi magazynu: (poprawne nazwy posortowane, nazwy dowiązań, nazwy katalogów o złej nazwie).

    Pomija `*.json` i `*.lock`; dowiązania-katalogi nie są czytane."""
    names, symlinks, invalid = [], [], []
    for entry in sorted(directory.iterdir()):
        if entry.name.endswith((".json", ".lock")):
            continue
        if entry.is_symlink():
            symlinks.append(entry.name)
        elif entry.is_dir():
            try:
                names.append(_slug(entry.name))
            except ValueError:
                invalid.append(entry.name)
    return names, symlinks, invalid


def _owner_dir(stem, names):
    """Katalog o najdłuższej nazwie D, gdzie stem == D albo zaczyna się od `D.`; inaczej None."""
    matching = [name for name in names if stem == name or stem.startswith(name + ".")]
    return max(matching, key=len) if matching else None


def _empty_dirs(directory, stems):
    """Katalogi bez przypisanego pliku: (id posortowane, liczba dowiązań, liczba katalogów o złej nazwie).

    Plik `<stem>.json` należy do katalogu o najdłuższej nazwie D, gdzie stem == D albo zaczyna się od `D.`."""
    names, symlinks, invalid = _scan_dirs(directory)
    owned = {_owner_dir(stem, names) for stem in stems}
    return [name for name in names if name not in owned], len(symlinks), len(invalid)


def metrics(args):
    root, _ = read_project(args.path)
    directory = _store_path(Path(root).resolve(), "metrics").parent
    runs, ids, stems = [], [], []
    if directory.exists() and not directory.is_dir():
        raise ValueError("Magazyn przebiegów nie jest katalogiem")
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink():
                raise ValueError("Przebieg jest dowiązaniem")
            runs.append(_read(path))
            ids.append(_run_id(path))
            stems.append(path.stem)
    all_runs = runs
    benchmarks = [run["benchmark"] for run in runs if "benchmark" in run]
    named = [(ident, run) for ident, run in zip(ids, runs) if "benchmark" not in run]
    runs = [run for run in runs if "benchmark" not in run]
    classified = None
    if getattr(args, "classify", False):
        owners = [stem for stem, run in zip(stems, all_runs) if "benchmark" not in run]
        extra = _empty_dirs(directory, owners) if directory.is_dir() else ([], 0, 0)
        classified = _classify(named, len(benchmarks), *extra)
    records = [record for run in runs for record in run["checkpoints"]]
    costs = [record["cost"] for record in records if record.get("cost") is not None]
    cost_totals = {}
    for record in records:
        if record.get("cost") is not None:
            unit = record.get("cost_unit", "unspecified")
            cost_totals[unit] = cost_totals.get(unit, 0) + record["cost"]
    durations = [record["duration_seconds"] for record in records
                 if record.get("duration_seconds") is not None]
    causes = {}
    repeat_steps = 0
    for run in runs:
        seen = set()
        for record in run["checkpoints"]:
            if record["step"] in seen:
                repeat_steps += 1
                cause = record.get("cause") or "unknown"
                causes[cause] = causes.get(cause, 0) + 1
            seen.add(record["step"])
    result = {"runs": len(runs), "checkpoints": len(records), "cost_known_total": cost_totals.get("USD", 0),
              "cost_known_total_unit": "USD", "cost_totals_by_unit": cost_totals,
              "offline_benchmark_runs": len(benchmarks),
              "offline_benchmark_case_seconds": sum(case.get("duration_seconds", 0)
                                                     for run in benchmarks for case in run["cases"]),
              "cost_unknown_count": len(records) - len(costs),
              "duration_known_seconds": sum(durations),
              "duration_unknown_count": len(records) - len(durations),
              "repeated_step_records": repeat_steps, "operator_reported_causes": causes,
              "interpretation": "Recorded measurements only; repeated steps do not establish causality or savings."}
    if classified is not None:
        result["classification"] = classified
    _emit(args, result)
    return 0


def _suite(path):
    import yaml
    path = Path(path)
    if path.stat().st_size > MAX_METADATA:
        raise ValueError("Suite przekracza limit rozmiaru")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("Suite wymaga niepustej listy cases")
    if len(data["cases"]) > 100:
        raise ValueError("Suite przekracza 100 przypadków")
    return data


def _terminate(proc, windows_backend=None):
    """Terminate the benchmark tree and reap its leader with bounded waits."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        if not windows_backend:
            raise ValueError("Brak backendu kończącego drzewo procesów")
        try:
            subprocess.run([windows_backend, "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise ValueError("Nie udało się zakończyć procesu benchmarku") from None


def benchmark(args):
    root, _ = read_project(args.path)
    suite = _suite(args.suite)
    planned = []
    ids = set()
    for case in suite["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Przypadek musi być mapą")
        ident = _slug(case.get("id"))
        if ident in ids:
            raise ValueError("Powtórzony identyfikator przypadku")
        ids.add(ident)
        argv = case.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("Przypadek wymaga listy argv bez shell")
        if not isinstance(case.get("cwd", "."), str):
            raise ValueError("cwd musi być ścieżką tekstową")
        if not isinstance(case.get("expected_exit", 0), int) or not isinstance(case.get("expected_output", ""), str):
            raise ValueError("expected_exit musi być liczbą, expected_output tekstem")
        timeout = _number(case.get("timeout", 30))
        if timeout <= 0 or timeout > 300:
            raise ValueError("Timeout musi mieścić się w (0, 300]")
        fixture = case.get("fixture", {})
        if not isinstance(fixture, dict) or len(fixture) > 100:
            raise ValueError("Nieprawidłowa fixture")
        for name, content in fixture.items():
            if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Fixture wychodzi poza izolowany katalog")
            if not isinstance(content, str) or len(content.encode()) > MAX_METADATA:
                raise ValueError("Nieprawidłowa zawartość fixture")
        planned.append((case, timeout))
    results = []
    for case, timeout in planned:
        result = {"id": case["id"], "status": "NOT_RUN"}
        if args.run:
            windows_backend = shutil.which("taskkill") if os.name == "nt" else None
            if os.name != "posix" and not windows_backend:
                result.update({"status": "BLOCKED", "reason": "Process-tree termination backend unavailable"})
                results.append(result)
                continue
            with tempfile.TemporaryDirectory(prefix="skillsync-benchmark-") as temporary:
                fixture_root = Path(temporary)
                self_cmd = self_argv()
                replacements = {"{project}": temporary, "{python}": sys.executable}
                def replace(value):
                    # `{python} {skillsync}` and a bare `{skillsync}` in text become the whole self command.
                    value = value.replace("{python} {skillsync}", shlex.join(self_cmd))
                    value = value.replace("{skillsync}", shlex.join(self_cmd))
                    for key, target in replacements.items():
                        value = value.replace(key, target)
                    return value
                def expand(items):
                    # List forms: the pair `{python}`, `{skillsync}` and a lone `{skillsync}` become argv elements.
                    out, index = [], 0
                    while index < len(items):
                        if items[index] == "{python}" and index + 1 < len(items) and items[index + 1] == "{skillsync}":
                            out += self_cmd
                            index += 2
                        elif items[index] == "{skillsync}":
                            out += self_cmd
                            index += 1
                        else:
                            out.append(replace(items[index]))
                            index += 1
                    return out
                for name, content in case.get("fixture", {}).items():
                    target = _inside(fixture_root, name, existing=False)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                cwd = _inside(fixture_root, replace(case.get("cwd", ".")), existing=False)
                if not cwd.is_dir():
                    raise ValueError("cwd benchmarku musi istnieć w fixture")
                argv = expand(case["argv"])
                started = time.monotonic()
                with tempfile.TemporaryFile() as output:
                    try:
                        proc = subprocess.Popen(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                                start_new_session=(os.name == "posix"))
                    except OSError:
                        result.update({"status": "BLOCKED", "reason": "Executable unavailable",
                                       "duration_seconds": time.monotonic() - started})
                        results.append(result)
                        continue
                    timed_out = False
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                    finally:
                        # Covers timeout, cancellation and successful leaders with background children.
                        _terminate(proc, windows_backend)
                    output.seek(0)
                    raw = output.read(MAX_METADATA + 1)
                overflow = len(raw) > MAX_METADATA
                matches = case.get("expected_output", "") in raw.decode("utf-8", errors="replace")
                passed = not timed_out and not overflow and matches and proc.returncode == case.get("expected_exit", 0)
                result.update({"status": "BLOCKED" if timed_out else ("PASS" if passed else "FAIL"), "exit_code": proc.returncode,
                               "duration_seconds": time.monotonic() - started,
                               "timeout": timed_out, "output_overflow": overflow,
                               "output_sha256": hashlib.sha256(raw).hexdigest(), "output_matches": matches})
        results.append(result)
    result = {"kind": "offline-local-tool-benchmark", "model_scores": None,
              "status": "NOT_RUN" if not args.run else ("BLOCKED" if any(c["status"] == "BLOCKED" for c in results)
                         else ("PASS" if all(c["status"] == "PASS" for c in results) else "FAIL")),
              "suite_sha256": _hash_file(Path(args.suite)), "cases": results,
              "cost": None, "project": str(root)}
    if args.run:
        path = _store_path(Path(root).resolve(), "benchmark-" + str(time.time_ns()))
        _write(path, {"schema_version": 1, "checkpoints": [], "benchmark": result})
    _emit(args, result)
    return 2 if result["status"] == "BLOCKED" else (1 if result["status"] == "FAIL" else 0)


def register(subparsers):
    for name, handler in (("checkpoint", checkpoint), ("resume", resume),
                          ("metrics", metrics), ("benchmark", benchmark)):
        parser = subparsers.add_parser(name, help=handler.__name__)
        parser.add_argument("path", nargs="?", default=".")
        parser.add_argument("--json", action="store_true")
        parser.set_defaults(func=handler)
        if name in ("checkpoint", "resume"):
            parser.add_argument("--run-id", required=True)
            _layers.add_layer_argument(parser)  # warstwy skrótu źródeł (`sources_digest`, K3)
        if name in ("metrics", "benchmark"):
            _layers.add_layer_argument(parser)  # przyjęta i ignorowana: komenda nie czyta biblioteki
        if name == "checkpoint":
            parser.add_argument("--step", required=True)
            parser.add_argument("--status", required=True, choices=STATUSES)
            parser.add_argument("--evidence", nargs="+", action="extend")
            parser.add_argument("--next", required=True)
            parser.add_argument("--duration", type=float)
            parser.add_argument("--cost", type=float)
            parser.add_argument("--cost-unit", default="USD", help="Three-letter cost currency (default USD)")
            parser.add_argument("--attempt", type=int, default=1)
            parser.add_argument("--cause")
            parser.add_argument("--denied", type=int, help="Odmowy w tym rekordzie (przyrost, >= 0)")
            parser.add_argument("--inherited", type=int, help="Odziedziczone w tym rekordzie (przyrost, >= 0)")
            parser.add_argument("--cause-key", help="Klucz przyczyny: [a-z0-9][a-z0-9-]{0,47}")
            parser.add_argument("--owner", choices=OWNERS, help="Właściciel poprawki; wymaga --cause-key")
            parser.add_argument("--final", action="store_true", help="Oznacz rekord jako końcowy przebiegu (tylko PASS)")
        if name == "metrics":
            parser.description = ("Agreguje zapisane pomiary. Uwaga: z --classify wynik zawiera wolny tekst `cause` "
                                  "(runs[].causes, cause_ranking, operator_reported_causes) i nie maskuje `id`, "
                                  "więc nie publikuj go; zakaz wolnego tekstu dotyczy tylko `retro`.")
            parser.add_argument("--classify", action="store_true",
                                help="Dodaj deterministyczną klasyfikację przebiegów (clean/recovery/unexplained/"
                                     "incomplete/no_data); wynik zawiera wolny tekst `cause`, nie publikuj go")
        if name == "benchmark":
            parser.add_argument("--suite", required=True)
            parser.add_argument("--run", action="store_true")
