"""Hooki Claude Code w ignorowanym `settings.local.json`: scalanie wpisu, specyfikacje nowych hookow, instalacja i `doctor`.

 * `merge_entry`: wspolne scalanie wpisu (cala grupa {matcher, hooks} buduje wolajacy); cudze hooki, uprawnienia i inne klucze zostaja,
   blad pliku to [warn] (nigdy wyjatek), plik sledzony przez git nie jest zapisywany;
 * `SPECS`, `resolve`, `build_entry`, `spec_same`: nowe hooki (kontekst sesji, straznik plikow generowanych, raport podagentow) i granica
   commitow podagentow (`subagent_commit` to argument straznika polecen); porownanie wpisu pomija argv CLI, chyba ze wskazuje nieistniejaca sciezke;
 * `sync_hooks`, `remove_ours`: instalacja i usuniecie (`claude: false`) wpisow i skryptow; pliki sledzone przez git zostaja;
 * `doctor_findings`: stan hookow wzgledem tego, co zapisalby `link` (info/warning, nigdy `error`, bez ech wartosci z manifestu)."""
import datetime
import json
import os
import shlex
import shutil
from collections import namedtuple
from pathlib import Path

from . import guardhook


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


def merge_entry(settings_path, event, entry, ours, want, dry_run, backup="overwrite", extra=None, same=None, drop=None) -> bool:
    """Dopisuje (`want`) albo usuwa nasz wpis zdarzenia `event`; True, gdy po wywolaniu nie zostaje w pliku nasz wpis.

    `ours(hook)` rozpoznaje nasz hook (slownik); `backup`: `overwrite` nadpisuje kopie
    `settings.local.json.skillsync-backup`, `earliest` zachowuje najwczesniejsza. `extra` (slownik kluczy najwyzszego
    poziomu) jest stosowane tylko przy `want` w tym samym odczycie i zapisie; inna wartosc sama wymusza zapis.
    `same(groups)` (domyslnie: nasze hooki rowne `entry["hooks"]`) rozstrzyga, czy wpis jest aktualny (np. takze matcher);
    `drop` (slownik) usuwa przy `not want` klucze najwyzszego poziomu o dokladnie tych wartosciach (nasze `attribution`)."""
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
    present = [hook for group in groups for hook in group.get("hooks", []) if ours(hook)]
    extra = extra or {}
    current = want and (same(groups) if same is not None else present == entry["hooks"])
    if current and all(key in existing and existing[key] == value for key, value in extra.items()):
        return False  # wpis (i dodatkowe klucze) aktualny; nie przestawiamy ani nie nadpisujemy pliku
    if want:
        for key, value in extra.items():
            updated[key] = json.loads(json.dumps(value))
    else:
        for key, value in (drop or {}).items():
            if key in updated and updated[key] == value:
                del updated[key]
    kept = []
    for group in groups:
        if "hooks" not in group:
            kept.append(group)
            continue
        remaining = [hook for hook in group["hooks"] if not ours(hook)]
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


# --------------------------------------------------------------------------------------------------------------------------
# The hooks of CLAUDE-3 (spec K7, K9, K10): session context, guard of generated files, report of sub-agents, commit boundary.
# --------------------------------------------------------------------------------------------------------------------------
HOOKS_DIR = ".claude/hooks"
SETTINGS = guardhook.SETTINGS
Spec = namedtuple("Spec", "key script event matcher timeout default values")
# Order = order of the merges: guard of generated files after the guard of commands (same event), then SessionStart, then SubagentStop.
SPECS = (
    Spec("guard_generated", "guard-generated.py", "PreToolUse", "Write|Edit|NotebookEdit", 10, "warn", ("warn", "deny", "off")),
    Spec("session_context", "session-context.py", "SessionStart", "startup|resume|clear|compact", 3, True, None),
    Spec("subagent_report", "check-subagent-report.py", "SubagentStop", "", 10, "warn", ("warn", "off")),
)
COMMIT_KEY = "subagent_commit"
COMMIT_MODES = ("warn", "deny", "off")
COMMIT_DEFAULT = "deny"
OWN_FILES = ("check-closure.py", "guard-commands.py", "guard-tests.py", "attribution-patterns.json", "test-tools.json") + tuple(spec.script for spec in SPECS)
EVENT_DAYS = 14   # the window and the parser of `session-context.py`
EVENT_LINES = 500
EVENT_TAIL_BYTES = 512 * 1024
EVENTS_FILE = ".skillsync/hook-events.jsonl"


def is_on(value) -> bool:
    return value not in (False, "off")


def _parse(raw, values):
    """The normalised value of a manifest entry, `KeyError` when unusable (a bool is a switch only for `session_context`)."""
    if values is None:
        if isinstance(raw, bool):
            return raw
    elif raw is False:
        return "off"
    elif isinstance(raw, str) and raw in values:
        return raw
    raise KeyError(raw)


def resolve(manifest, skills):
    """`(values, problems)`: the effective value of every key (`session_context`, `guard_generated`, `subagent_report`, `subagent_commit`) and the list of keys
    whose manifest value is unusable (the hook is then off). Absent key: the default of an agent-flow project, off elsewhere."""
    hooks = manifest.get("hooks") if isinstance(manifest.get("hooks"), dict) else {}
    flow = guardhook.agent_flow(skills)
    values, problems = {}, []
    table = [(spec.key, spec.default, spec.values) for spec in SPECS] + [(COMMIT_KEY, COMMIT_DEFAULT, COMMIT_MODES)]
    for key, default, allowed in table:
        off = False if allowed is None else "off"
        raw = hooks.get(key)
        if raw is None:
            values[key] = default if flow else off
            continue
        try:
            values[key] = _parse(raw, allowed)
        except KeyError:
            values[key] = off
            problems.append(key)
    return values, problems


def problem_text(key):
    return f"hooks.{key}: unusable value, the hook stays off"


def effective_values(manifest, config) -> dict:
    """The effective values of the hook keys of a manifest read with the skills of `config`."""
    from . import cli
    return resolve(manifest, cli.project_skills(manifest, config))[0]


def commit_mode(manifest, skills):
    """`warn` | `deny` | `off`: the argument `--subagent-commit` of the guard of commands."""
    return resolve(manifest, skills)[0][COMMIT_KEY]


def wanted(manifest, config) -> bool:
    """True when `link` installs at least one of the hooks of this module or the commit boundary (so `.skillsync/` has to be ignored)."""
    from . import cli
    if not cli.claude_enabled(manifest):
        return False
    values = effective_values(manifest, config)
    return any(is_on(value) for value in values.values())


def gitignore_entries(manifest, config) -> list:
    from . import cli
    if not cli.claude_enabled(manifest):
        return []
    values = effective_values(manifest, config)
    entries = [f"hooks/{spec.script}" for spec in SPECS if is_on(values[spec.key])]
    return entries + ["settings.local.json", "settings.local.json.skillsync-backup"] if entries else []


def build_command(python_command, script, args) -> str:
    python = shlex.quote(python_command)
    return (f'f="$(git -C "${{CLAUDE_PROJECT_DIR:-.}}" rev-parse --show-toplevel 2>/dev/null)/{HOOKS_DIR}/{script}"; '
            f'if [ -f "$f" ]; then {python} "$f" {shlex.join(args)}; fi')


def spec_args(spec, value):
    if spec.key == "session_context":
        from .selfcmd import self_argv
        return ["--cli", json.dumps(self_argv())]
    return ["--mode", value]


def build_entry(spec, python_command, value) -> dict:
    command = build_command(python_command, spec.script, spec_args(spec, value))
    return {"matcher": spec.matcher, "hooks": [{"type": "command", "command": command, "timeout": spec.timeout}]}


def is_ours(spec):
    needle = f"{HOOKS_DIR}/{spec.script}"
    return lambda hook: isinstance(hook, dict) and hook.get("type") == "command" and needle in str(hook.get("command", ""))


def split_command(command):
    """`(head, args)` of a command `f=...; if [ -f "$f" ]; then <python> "$f" <args>; fi`, `None` when it has another shape."""
    head, sep, tail = str(command).rpartition('"$f" ')
    tail = tail.rstrip()
    if not sep or not tail.endswith("; fi"):
        return None
    try:
        return head, shlex.split(tail[:-4])
    except ValueError:
        return None


def without_cli(args):
    """The argv of the CLI (`--cli <json>`) differs between checkouts and is not part of the comparison."""
    out, skip = [], False
    for item in args:
        if skip:
            skip = False
        elif item == "--cli":
            skip = True
        else:
            out.append(item)
    return out


def stale_cli(hook) -> bool:
    """True when the argv of the CLI saved in a command of `session-context.py` holds an absolute path that no longer exists (e.g. a removed worktree).
    Paths that exist are never compared: `link` from different checkouts must not flip the entry."""
    parts = split_command(hook.get("command", "")) if isinstance(hook, dict) else None
    if parts is None or "--cli" not in parts[1] or parts[1].index("--cli") + 1 >= len(parts[1]):
        return False
    try:
        argv = json.loads(parts[1][parts[1].index("--cli") + 1])
    except ValueError:
        return False
    return isinstance(argv, list) and any(isinstance(item, str) and os.path.isabs(item) and not os.path.exists(item) for item in argv)


def fixed_command(command):
    parts = split_command(command)
    return str(command) if parts is None else json.dumps([parts[0], without_cli(parts[1])])


def group_same(entry, ours, normalize=None):
    """`same` of `merge_entry`: every group with a hook of ours has the matcher of `entry` and together our hooks equal `entry["hooks"]`."""
    norm = normalize or (lambda hook: hook)
    wanted_hooks = [norm(hook) for hook in entry["hooks"]]

    def same(groups):
        found, matchers = [], []
        for group in groups:
            mine = [hook for hook in group.get("hooks", []) if ours(hook)]
            if mine:
                found += [norm(hook) for hook in mine]
                matchers.append(group.get("matcher"))
        return bool(found) and found == wanted_hooks and all(matcher == entry.get("matcher") for matcher in matchers)
    return same


def _fixed_hook(hook):
    return {**hook, "command": fixed_command(hook.get("command", ""))}


def spec_same(spec, entry):
    """`same` for one of the new hooks: the matcher, the hook (script, modes, timeout) and, for the guard of generated files, its place after the guard of commands.
    The argv of the CLI of `session-context.py` is not compared."""
    base = group_same(entry, is_ours(spec), _fixed_hook if spec.key == "session_context" else None)
    mine = is_ours(spec)

    def same(groups):
        if not base(groups):
            return False
        if spec.key == "session_context" and any(stale_cli(hook) for group in groups for hook in group.get("hooks", []) if mine(hook)):
            return False
        if spec.event != "PreToolUse":
            return True
        last = max(index for index, group in enumerate(groups) if any(mine(hook) for hook in group.get("hooks", [])))
        return not any(guardhook.is_guard_hook(hook) for group in groups[last + 1:] for hook in group.get("hooks", []))
    return same


def guard_same(entry):
    """`same` for the guard of commands: also the matcher counts (the hook entry itself is compared as before)."""
    return group_same(entry, guardhook.is_guard_hook)


def copy_script(project_dir, config, name, dry_run=False) -> bool:
    """Copies a hook from the library (core layer); True when the script is (in dry-run: would be) in place."""
    from . import cli
    found = cli.config_layers(config).resolve(f"hooks/{name}")
    if found is None:
        print(f"[warn] Missing hook script in library: hooks/{name} (core layer)")
        return False
    dest = Path(project_dir) / HOOKS_DIR / name
    if dest.is_symlink():
        print(f"[warn] {dest} jest dowiązaniem symbolicznym, nie nadpisuję jego celu")
        return False
    try:
        if dest.is_file() and os.access(dest, os.X_OK) and dest.read_bytes() == Path(found[1]).read_bytes():
            return True
        if dry_run:
            print(f"[dry-run] Would write: {dest}")
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found[1], dest)
        dest.chmod(dest.stat().st_mode | 0o111)
    except OSError as error:
        print(f"[warn] Nie udalo sie zapisac {dest}: {error}")
        return False
    return True


def remove_script(project_dir, name, dry_run=False) -> None:
    from . import cli
    dest = Path(project_dir) / HOOKS_DIR / name
    if dest.is_file() and not dest.is_symlink():
        if cli.is_tracked(dest):
            print(f"[warn] {dest} jest śledzony przez git, pomijam usunięcie")
            return
        if dry_run:
            print(f"[dry-run] Would remove: {dest}")
            return
        try:
            dest.unlink()
        except OSError as error:
            print(f"[warn] Nie udalo sie usunac {dest}: {error}")


def _has_entry(settings_path, spec) -> bool:
    return bool(_our_hooks(peek_settings(settings_path) or {}, spec.event, is_ours(spec)))


def sync_hooks(project_dir, manifest, config, dry_run=False) -> None:
    """Installs or removes the new hooks: the entry only with its script, the script goes after its entry is removed. The entries are merged with the
    earliest backup policy; an off hook that has no entry and no script is not touched at all (a broken settings file stays silent)."""
    from . import cli
    if not cli.claude_enabled(manifest):
        return
    project_dir = Path(project_dir)
    values, problems = resolve(manifest, cli.project_skills(manifest, config))
    for key in problems:
        print(f"[warn] {problem_text(key)}")
    settings_path = project_dir / SETTINGS
    python_command = config.get("python_command", "python3")
    for spec in SPECS:
        value = values[spec.key]
        ours = is_ours(spec)
        if is_on(value):
            copied = copy_script(project_dir, config, spec.script, dry_run)
            entry = build_entry(spec, python_command, value)
            merge_entry(settings_path, spec.event, entry, ours, copied, dry_run, backup="earliest", same=spec_same(spec, entry))
        elif (project_dir / HOOKS_DIR / spec.script).is_file() or _has_entry(settings_path, spec):
            if merge_entry(settings_path, spec.event, None, ours, False, dry_run, backup="earliest"):
                remove_script(project_dir, spec.script, dry_run)


def remove_ours(project_dir, dry_run=False) -> None:
    """`claude: false` (K10): removes every entry of ours (closure, guards, the new hooks), the host setting `attribution` when it is ours, and then our scripts.
    Foreign entries, keys and files stay; a tracked or unreadable settings file is not written and the scripts then stay (no dangling entry)."""
    from . import cli
    project_dir = Path(project_dir)
    settings_path = project_dir / SETTINGS
    attribution = {"attribution": dict(guardhook.ATTRIBUTION_SETTING)}
    targets = [("Stop", cli.is_closure_hook, None), ("PreToolUse", guardhook.is_guard_hook, attribution)] + [(spec.event, is_ours(spec), None) for spec in SPECS]
    if settings_path.exists():
        for event, ours, drop in targets:
            if not merge_entry(settings_path, event, None, ours, False, dry_run, backup="earliest", drop=drop):
                return
    for name in OWN_FILES:
        remove_script(project_dir, name, dry_run)


# --------------------------------------------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------------------------------------------
def count_recent_events(root) -> int:
    """`subagent-report` events of the last 14 days among the last 500 lines of the events file (the parser of `session-context.py`)."""
    try:
        with open(Path(root) / EVENTS_FILE, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - EVENT_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return 0
    now = datetime.datetime.now().astimezone()
    count = 0
    for line in raw.decode("utf-8", "replace").splitlines()[-EVENT_LINES:]:
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("hook") != "subagent-report":
                continue
            stamp = datetime.datetime.fromisoformat(str(event.get("time")))
            if stamp.tzinfo is None:
                stamp = stamp.astimezone()
            if 0 <= (now - stamp).total_seconds() <= EVENT_DAYS * 86400:
                count += 1
        except (ValueError, TypeError, OverflowError):
            continue
    return count


def _our_hooks(data, event, ours):
    groups = (data.get("hooks") or {}).get(event, [])
    if not groups_valid(groups):
        return []
    return [(group, hook) for group in groups for hook in group.get("hooks", []) if ours(hook)]


def _source_differs(layerset, script, copy) -> bool:
    if layerset is None:
        return False
    try:
        found = layerset.resolve(f"hooks/{script}")
        return found is not None and Path(found[1]).read_bytes() != copy.read_bytes()
    except Exception:   # a layer problem is reported by the layer checks; here only the comparison is skipped
        return False


def _python_command(python_command):
    if python_command is not None:
        return python_command
    try:
        from . import cli
        return cli.load_config(optional=True).get("python_command", "python3")
    except Exception:   # no readable machine config: the default of `link`
        return "python3"


def doctor_findings(root, manifest, skills, layerset=None, python_command=None):
    """`[(level, path, reason)]` of the new hooks: info with the mode of an installed hook, warning for an outdated copy of a script, a missing script,
    a drift of the fixed part of the entry (matcher, modes, timeout, the interpreter; the argv of the CLI only when it names a path that no longer exists), an unusable manifest value and an installed hook that is off;
    info with the number of sub-agent reports missing in the last 14 days. Never an `error`; nothing for `claude: false`."""
    claude = manifest.get("claude")
    if claude is False or (isinstance(claude, dict) and claude.get("enabled") is False):
        return []
    values, problems = resolve(manifest, skills)
    found = [("warning", ".github/.skillsync.yaml", problem_text(key)) for key in problems]
    root = Path(root)
    python_command = _python_command(python_command)
    if guardhook.never_linked(root):
        return found   # the guard hook finding already says: run `skillsync link`
    data = peek_settings(root / SETTINGS)
    if data is None:
        return found   # unreadable settings are reported by the guard hook findings
    for spec in SPECS:
        value = values[spec.key]
        rel = f"{HOOKS_DIR}/{spec.script}"
        installed = _our_hooks(data, spec.event, is_ours(spec))
        copy = root / rel
        if not is_on(value):
            if installed or copy.is_file():
                found.append(("warning", rel, f"The hook is off in the manifest (hooks.{spec.key}) but still installed; run `skillsync link`"))
            continue
        if not installed:
            found.append(("warning", rel, f"The hook is on (hooks.{spec.key}) but not installed in {SETTINGS}; run `skillsync link`"))
            continue
        if not copy.is_file():
            found.append(("warning", rel, f"The entry of the hook exists but its script is missing: the hook does nothing; run `skillsync link`"))
            continue
        if _source_differs(layerset, spec.script, copy):
            found.append(("warning", rel, "The copy of the hook script differs from the library (outdated or edited copy); run `skillsync link`"))
            continue
        expected = build_entry(spec, python_command, value)
        wanted_fixed = (spec.matcher, spec.timeout, *split_command(expected["hooks"][0]["command"]))
        drift = len(installed) != 1
        for group, hook in installed:
            parts = split_command(hook.get("command", ""))
            drift = drift or parts is None or stale_cli(hook) or (group.get("matcher"), hook.get("timeout"), parts[0], without_cli(parts[1])) != (
                wanted_fixed[0], wanted_fixed[1], wanted_fixed[2], without_cli(wanted_fixed[3]))
        if drift:
            found.append(("warning", rel, "The entry of the hook differs from what `skillsync link` writes (matcher, mode, timeout, interpreter or a path that no longer exists); run `skillsync link`"))
            continue
        shown = "on" if spec.key == "session_context" else f"mode {value}"
        found.append(("info", rel, f"Hook {spec.event} active ({spec.key}): {shown}"))
    command = guardhook.installed(root / SETTINGS)
    if isinstance(command, str):
        parts = split_command(command)
        args = parts[1] if parts else []
        have = args[args.index("--subagent-commit") + 1] if "--subagent-commit" in args and args.index("--subagent-commit") + 1 < len(args) else "off"
        want = values[COMMIT_KEY]
        if have != want:
            found.append(("warning", guardhook.GUARD_HOOK, "The commit boundary of sub-agents (hooks.subagent_commit, argument --subagent-commit of the guard of commands) differs from the manifest; run `skillsync link`"))
        elif is_on(want):
            found.append(("info", guardhook.GUARD_HOOK, f"Commit boundary of sub-agents (hooks.subagent_commit, guard-commands.py --subagent-commit): mode {want}"))
    if is_on(values["subagent_report"]):
        count = count_recent_events(root)
        if count:
            found.append(("info", EVENTS_FILE, f"subagent-report: {count} writing sub-agent(s) ended without a report in the last {EVENT_DAYS} days"))
    return found
