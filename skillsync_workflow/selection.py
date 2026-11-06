"""Conservative test selection from repository changes and declared dependencies."""
import hashlib
import json
import subprocess
from pathlib import Path

from .common import emit, load_layer_project, matches, workflow_config
from .layers import add_layer_argument
from .quality import plan_validation, run_validation


SHARED_PATHS = [
    "package.json", "*lock*", "pyproject.toml", "requirements*.txt", "setup.cfg",
    "setup.py", "pytest.ini", "tox.ini", "tsconfig*.json", "Makefile",
    ".github/.skillsync.yaml", ".skillsync.yaml", ".github/workflows/**",
]


def _git(root, *args):
    if args and args[0] == "diff":
        args = (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError("Git nie może ustalić zmian: " + " ".join(args))
    return result.stdout


def _paths(data):
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]


def _local_changes(root, base=None):
    git_root = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if git_root != root.resolve():
        raise ValueError("Projekt musi wskazywać katalog główny repozytorium Git")
    if base:
        revision = _git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
        commands = [("diff", "--name-only", "-z", "--no-renames", revision, "--")]
    else:
        revision = "local"
        commands = [("diff", "--name-only", "-z", "--no-renames", "--"),
                    ("diff", "--cached", "--name-only", "-z", "--no-renames", "--")]
    files = set()
    for command in commands:
        files.update(_paths(_git(root, *command)))
    files.update(_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    files = sorted(p for p in files if p != ".skillsync" and not p.startswith(".skillsync/"))
    return files, revision


def _normal_files(root, files):
    normalized = []
    for name in files:
        candidate = Path(name)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("Zmieniony plik poza projektem") from exc
        if ".." in candidate.parts:
            raise ValueError("Zmieniony plik poza projektem")
        try:
            (root / candidate).resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("Zmieniony plik poza projektem przez dowiązanie") from exc
        name = candidate.as_posix().removeprefix("./")
        if name and name != ".skillsync" and not name.startswith(".skillsync/"):
            normalized.append(name)
    return sorted(set(normalized))


def _modules(testing):
    modules = testing.get("modules", [])
    if isinstance(modules, dict):
        modules = [dict(value, id=name) for name, value in modules.items()]
    if not isinstance(modules, list):
        raise ValueError("testing.modules musi być listą albo mapą")
    mapping = {}
    for module in modules:
        if not isinstance(module, dict) or not isinstance(module.get("id"), str):
            raise ValueError("Każdy moduł wymaga id")
        if module["id"] in mapping:
            raise ValueError("Powtórzone id modułu")
        for key in ("paths", "checks", "depends_on"):
            value = module.get(key, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("Moduł wymaga listy " + key)
        mapping[module["id"]] = module
    for module in mapping.values():
        if set(module.get("depends_on", [])) - mapping.keys():
            raise ValueError("Nieznana zależność modułu " + module["id"])
    return mapping


def _cache_key(root, files, revision, config, profile):
    digest = hashlib.sha256()
    digest.update(json.dumps(config, sort_keys=True, ensure_ascii=True).encode())
    digest.update(str(root).encode())
    digest.update(revision.encode())
    digest.update(profile.encode())
    try:
        digest.update(_git(root, "rev-parse", "HEAD"))
        digest.update(_git(root, "diff", "--binary", "HEAD", "--", ".", ":(exclude).skillsync"))
        digest.update(b"index:")
        digest.update(_git(root, "diff", "--cached", "--binary", "HEAD", "--", ".", ":(exclude).skillsync"))
        digest.update(b"working:")
        digest.update(_git(root, "diff", "--binary", "--", ".", ":(exclude).skillsync"))
    except ValueError:
        digest.update(b"no-head")
    dirty_files = set(files)
    try:
        dirty_files.update(_paths(_git(root, "ls-files", "--others", "--exclude-standard", "-z")))
    except ValueError:
        pass
    for name in sorted(dirty_files):
        if name == ".skillsync" or name.startswith(".skillsync/"):
            continue
        digest.update(name.encode("utf-8", "surrogateescape"))
        path = root / name
        try:
            path.parent.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("Cache nie może czytać pliku poza projektem") from exc
        if path.is_symlink():
            digest.update(b"symlink:")
            digest.update(str(path.readlink()).encode())
        elif path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
        else:
            digest.update(b"deleted")
    return digest.hexdigest()


def _cache_path(root, key):
    destination = Path(root) / ".skillsync" / "cache" / "smart-test" / (key + ".json")
    relative = destination.relative_to(root)
    current = Path(root)
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Cache nie może zapisywać przez dowiązanie: " + str(current))
    return destination


def plan_selection(root, manifest, library, *, base=None, files=None, profile="quick"):
    root = Path(root).resolve()
    report = {"status": "NOT_RUN", "files": [], "reasons": [], "checks": [],
              "fallback": False, "profile": profile, "base": base, "affected_modules": [],
              "uncovered": [], "test_only": None, "direct_files": []}
    try:
        config = workflow_config(root, manifest, library)
        if files is None:
            changed, revision = _local_changes(root, base)
        else:
            changed, revision = _normal_files(root, files), base or "explicit-files"
            if base:
                revision = _git(root, "rev-parse", "--verify", base + "^{commit}").decode().strip()
        report["files"] = changed
        testing = config.get("testing", {})
        if not isinstance(testing, dict):
            raise ValueError("workflow.testing musi być mapą")
        rules = testing.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("testing.rules musi być listą")
        test_paths = testing.get("test_paths")
        if test_paths is not None and (not isinstance(test_paths, list)
                                       or any(not isinstance(item, str) for item in test_paths)):
            raise ValueError("testing.test_paths musi być listą napisów")
        modules = _modules(testing)
        selected, affected = set(), set()
        for name in changed:
            if matches(name, SHARED_PATHS + testing.get("wide_paths", [])):
                report["fallback"] = True
                report["reasons"].append({"path": name, "reason": "shared-or-wide-path"})
            matched = False
            for rule in rules:
                if not isinstance(rule, dict) or not isinstance(rule.get("paths"), list) or not isinstance(rule.get("checks"), list):
                    raise ValueError("Reguła testów wymaga paths oraz checks")
                if matches(name, rule["paths"]):
                    selected.update(rule["checks"])
                    matched = bool(rule["checks"]) or matched
                    report["reasons"].append({"path": name, "reason": "rule", "checks": rule["checks"]})
            for identifier, module in modules.items():
                if matches(name, module.get("paths", [])):
                    affected.add(identifier)
                    matched = bool(module.get("checks")) or matched
            if not matched:
                report["fallback"] = True
                report["reasons"].append({"path": name, "reason": "unmapped-or-deleted-unknown"})
                report["uncovered"] = sorted({*report["uncovered"], name})
        if test_paths is not None:
            wide = any(item.get("reason") == "shared-or-wide-path" for item in report["reasons"])
            report["test_only"] = bool(changed) and not wide and all(matches(n, test_paths) for n in changed)
            if report["test_only"]:
                report["direct_files"] = sorted(changed)
        while True:
            expanded = affected | {name for name, module in modules.items()
                                   if affected.intersection(module.get("depends_on", []))}
            if expanded == affected:
                break
            affected = expanded
        report["affected_modules"] = sorted(affected)
        for identifier in sorted(affected):
            checks = modules[identifier].get("checks", [])
            if not checks:
                report["fallback"] = True
                report["reasons"].append({"module": identifier, "reason": "module-without-checks"})
            selected.update(checks)
        # Quality normalizes legacy tests.command as a test check as well.
        full_plan = plan_validation(root, manifest, library, profile="all")
        tests = [check for check in full_plan.get("checks", [])
                 if check.get("kind", "test") in ("test", "tests", "integration", "e2e")]
        available = {check["id"] for check in full_plan.get("checks", [])}
        if selected - available:
            report["fallback"] = True
            report["reasons"].append({"reason": "unknown-checks", "checks": sorted(selected - available)})
        if report["fallback"]:
            report["checks"] = tests
            report["warnings"] = full_plan.get("warnings", [])
        else:
            chosen_plan = plan_validation(root, manifest, library, profile=profile, check_ids=sorted(selected))
            report["checks"] = chosen_plan.get("checks", [])
            report["warnings"] = chosen_plan.get("warnings", [])
            if chosen_plan.get("status") == "BLOCKED":
                report["status"] = "BLOCKED"
            if report["checks"] and not any(check.get("kind", "test") in
                    ("test", "tests", "integration", "e2e") for check in report["checks"]):
                report["fallback"] = True
                report["checks"] = tests
                report["reasons"].append({"reason": "mapping-without-test-checks"})
        if not report["checks"]:
            report["status"] = "BLOCKED"
            report["reasons"].append({"reason": "no-usable-test-command" if changed else "no-changes-no-test-evidence"})
        if any(not check.get("argv") and not check.get("command") for check in report["checks"]):
            report["status"] = "BLOCKED"
            report["reasons"].append({"reason": "invalid-test-command"})
        report["cache_key"] = _cache_key(root, changed, revision, config, profile)
    except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        report["status"] = "BLOCKED"
        report["reasons"].append({"reason": str(exc)})
    return report


def command(args):
    # LayerError (no layer rule, legacy config) like a missing manifest: `skillsync: ...`, exit 2.
    root, manifest, library = load_layer_project(args.path, getattr(args, "layers", None))
    report = plan_selection(root, manifest, library, base=args.base, files=args.files, profile=args.profile)
    if report.get("cache_key"):
        # Persist selection plans, never execution outcomes. Recompute before use:
        # environment and command availability may change independently of Git.
        try:
            cache_file = _cache_path(root, report["cache_key"])
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(report, ensure_ascii=True, indent=2, default=str) + "\n", encoding="utf-8")
        except ValueError as exc:
            report["status"] = "BLOCKED"
            report["reasons"].append({"reason": str(exc)})
        except OSError as exc:
            report.setdefault("warnings", []).append("Nie zapisano cache planu: " + str(exc))
    if args.run and report["status"] != "BLOCKED":
        report["execution"] = run_validation(root, report["checks"])
        report["status"] = report["execution"]["status"]
        results = report["execution"].get("results", [])
        count = lambda status: sum(1 for item in results if item.get("status") == status)
        passed, failed, blocked = count("PASS"), count("FAIL"), count("BLOCKED")
        planned = len(report["checks"])
        executed = [item for item in results if item.get("exit_code") is not None]
        if not results:
            blocked = planned
        counts = [item.get("tests_run") for item in executed]
        known = bool(counts) and all(isinstance(n, int) and not isinstance(n, bool) for n in counts)
        report["summary"] = {
            "planned": planned, "ran": len(executed),
            "passed": passed, "failed": failed, "blocked": blocked,
            "not_run": 0 if not results else planned - passed - failed - blocked,
            "tests_counted": sum(counts) if known else None,
        }
    return emit(report, args.json)


def register(subparsers):
    parser = subparsers.add_parser("smart-test", help="Plan konserwatywnej selekcji testów")
    parser.add_argument("path", nargs="?", default=".")
    add_layer_argument(parser)
    parser.add_argument("--base", help="Jawna rewizja bazowa; domyślnie zmiany lokalne")
    parser.add_argument("--files", nargs="+", help="Jawna lista zmienionych ścieżek")
    parser.add_argument("--profile", choices=("quick", "final"), default="quick")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=command)
