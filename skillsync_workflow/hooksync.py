"""Hooki Claude Code w ignorowanym `settings.local.json`: scalanie wpisu, specyfikacje nowych hookow, instalacja i `doctor`.

 * `merge_entry`: wspolne scalanie wpisu (cala grupa {matcher, hooks} buduje wolajacy); cudze hooki, uprawnienia i inne klucze zostaja,
   blad pliku to [warn] (nigdy wyjatek), plik sledzony przez git nie jest zapisywany;
 * `merge_setting`: ten sam kontrakt dla jednego klucza najwyzszego poziomu (pasek statusu `statusLine`); cudzej wartosci klucza nie nadpisuje;
 * `sync_statusline`, `statusline_findings`: opcjonalny pasek statusu (`claude.statusline: true`), jego kopia skryptu, sprawdzenie cudzego paska
   i `disableAllHooks` w trzech plikach ustawien;
 * `SPECS`, `resolve`, `build_entry`, `spec_same`: nowe hooki (kontekst sesji, straznik plikow generowanych, raport podagentow) i granica
   commitow podagentow (`subagent_commit` to argument straznika polecen); porownanie wpisu pomija argv CLI, chyba ze wskazuje nieistniejaca sciezke;
 * `sync_hooks`, `remove_ours`: instalacja i usuniecie (`claude: false`) wpisow i skryptow; pliki sledzone przez git zostaja;
 * `doctor_findings`: stan hookow wzgledem tego, co zapisalby `link` (info/warning, nigdy `error`, bez ech wartosci z manifestu)."""
import datetime
import json
import os
import shlex
import shutil
import sys
from collections import namedtuple
from pathlib import Path

from . import claude_manifest, fsutil, guardhook


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


def merge_setting(settings_path, key, value, is_ours, want, dry_run, backup="earliest") -> bool:
    """Ustawia (`want`) albo usuwa klucz najwyzszego poziomu `key`; True, gdy po wywolaniu nie zostaje w pliku nasza wartosc (kontrakt `merge_entry`).

    `is_ours(wartosc)` rozpoznaje nasza wartosc klucza. Cudza wartosc nigdy nie jest nadpisana ani usuwana: przy `want` to [warn] z nazwa pliku i False.
    Sekcja `hooks` i inne klucze zostaja; plik sledzony przez git nie jest zapisywany; `backup`: jak w `merge_entry`."""
    from .cli import is_tracked, json_file_text, read_user_text, write_text_lf  # cli importuje ten modul

    settings_path = Path(settings_path)
    if not want and not settings_path.exists():
        return True
    existing, ok = load_settings(settings_path)
    if not ok:
        return False
    if key in existing:
        current = existing[key]
        if not is_ours(current):
            if want:
                print(f"[warn] {settings_path}: klucz {key} ma cudza wartosc, nie nadpisuje jej")
                return False
            return True
        if want and current == value:
            return False
    elif not want:
        return True
    updated = dict(existing)
    if want:
        updated[key] = json.loads(json.dumps(value))
    else:
        del updated[key]
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
STATUSLINE_KEY = "statusLine"
STATUSLINE_SCRIPT = "statusline.py"
STATUSLINE_REFRESH = 15   # seconds (`refreshInterval` needs Claude Code 2.1.97; an older one ignores it)
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
    return isinstance(argv, list) and any(isinstance(item, str) and fsutil.isabs(item) and not os.path.exists(item) for item in argv)


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
    sync_statusline(project_dir, manifest, config, dry_run)


def remove_ours(project_dir, dry_run=False, config=None) -> None:
    """`claude: false` (K10): removes every entry of ours (closure, guards, the new hooks, the status line), the host setting `attribution` when it is ours, and then our scripts.
    Foreign entries, keys and files stay (a status line script is removed only by the rule of `_remove_bar`); a tracked or unreadable settings file is not written
    and the scripts then stay (no dangling entry)."""
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
    _remove_bar(project_dir, config, settings_path, dry_run)


# --------------------------------------------------------------------------------------------------------------------------
# The status line (CLAUDE-7, opt-in `claude.statusline: true`): the entry `statusLine` of `settings.local.json` and the copy of the script.
# --------------------------------------------------------------------------------------------------------------------------
BAR_FILES = (
    (".claude/settings.local.json", 1, "the highest of the settings files"),
    (".claude/settings.json", 2, "overridden by the local file"),
    ("~/.claude/settings.json", 3, "the user level, overridden by both project files"),
    ("$CLAUDE_CONFIG_DIR/settings.json", 3, "the user level of the configuration directory, overridden by both project files"),
)
BAR_SUFFIX = f"/{HOOKS_DIR}/{STATUSLINE_SCRIPT}"
BAR_VERSION_NOTE = ("refreshInterval of the status line needs Claude Code >= 2.1.97 (an older one ignores it); showing the status line in the "
                    "VS Code extension is not confirmed (optional feature); the command assumes a POSIX shell (Git Bash, cmd; PowerShell fails to parse `\"py\" \"script\"`)")


def _quote(argument) -> str:
    return f'"{argument}"' if os.name == "nt" else shlex.quote(str(argument))


def _tokens(command) -> list:
    try:
        parts = shlex.split(str(command), posix=os.name != "nt")
    except ValueError:
        parts = str(command).split()
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in parts]


def bar_script(project_dir) -> Path:
    return Path(project_dir) / HOOKS_DIR / STATUSLINE_SCRIPT


def bar_command(project_dir) -> str:
    """The interpreter of this generator and the absolute path of the copy of the script: no shell, no `git`."""
    return f"{_quote(sys.executable)} {_quote(os.path.abspath(bar_script(project_dir)))}"


def bar_value(project_dir) -> dict:
    return {"type": "command", "command": bar_command(project_dir), "refreshInterval": STATUSLINE_REFRESH}


def _real(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def _script_token(value):
    """The absolute path of a script `.claude/hooks/statusline.py` named by a command value, else None."""
    if isinstance(value, dict) and value.get("type") == "command":
        for token in _tokens(value.get("command", "")):
            if token.replace("\\", "/").endswith(BAR_SUFFIX) and fsutil.isabs(token):
                return token
    return None


def bar_is_ours(project_dir):
    """Our `statusLine` value: the command names EXACTLY the script `<project>/.claude/hooks/statusline.py` (after `realpath`). The interpreter and other
    arguments do not count. A script of the same name elsewhere (the global one of the user, a moved or removed checkout) is foreign."""
    mine = _real(bar_script(project_dir))

    def ours(value) -> bool:
        token = _script_token(value)
        return token is not None and _real(token) == mine
    return ours


def bar_current(project_dir, value) -> bool:
    """Our entry is up to date: the same type, script and `refreshInterval`; the interpreter (and anything else) is not compared."""
    return bar_is_ours(project_dir)(value) and value.get("refreshInterval") == STATUSLINE_REFRESH


def _read_json(path):
    """`(data, problem)`: `problem` is None for a missing file or a mapping, else the reason."""
    try:
        if not path.exists():
            return {}, None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        return None, type(error).__name__
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def _user_files(home=None) -> list:
    """The user level settings files: `~/.claude/settings.json` and, when `CLAUDE_CONFIG_DIR` is an absolute path, its `settings.json`
    (a service without that variable does not see the second one)."""
    files = [(BAR_FILES[2], Path(home) / ".claude" / "settings.json" if home is not None else Path.home() / ".claude" / "settings.json")]
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    if config_dir and fsutil.isabs(config_dir):
        other = Path(config_dir) / "settings.json"
        if _real(other) != _real(files[0][1]):
            files.append((BAR_FILES[3], other))
    return files


def bar_blockers(project_dir, home=None) -> list:
    """`[(label, text)]` of what stops the installation: a foreign `statusLine` or `disableAllHooks: true` in any of the settings files (project, user,
    `CLAUDE_CONFIG_DIR`), or a file that cannot be read (a collision cannot be excluded). Empty = free."""
    project_dir = Path(project_dir)
    files = [(BAR_FILES[0], project_dir / SETTINGS), (BAR_FILES[1], project_dir / ".claude" / "settings.json")] + _user_files(home)
    ours = bar_is_ours(project_dir)
    found = []
    for (label, rank, note), path in files:
        data, problem = _read_json(path)
        prefix = f"{label} (priority {rank}/3, {note})"
        if data is None:
            found.append((label, f"statusLine: {prefix} is unreadable ({problem}); a collision cannot be excluded"))
            continue
        if STATUSLINE_KEY in data and not (rank == 1 and ours(data[STATUSLINE_KEY])):
            hint = ""
            if _script_token(data[STATUSLINE_KEY]) is not None:
                hint = f"; it names a script {BAR_SUFFIX[1:]} of another path: remove the entry by hand if this is a moved project"
            found.append((label, f"statusLine: another status line is set in {prefix}{hint}"))
        if data.get("disableAllHooks") is True:
            found.append((label, f"disableAllHooks: hooks (and the status line) are disabled in {prefix}"))
    return found


def _library_copy(config, name):
    from . import cli
    found = cli.config_layers(config).resolve(f"hooks/{name}") if config is not None else None
    return Path(found[1]) if found else None


SIGNATURE = b"skillsync-managed: hooks/statusline.py"
SIGNATURE_BYTES = 2048


def _script_is_ours(project_dir, config, name) -> bool:
    """A regular file that is a copy of the library or carries our signature near the top (an older version of our script); the path alone says nothing."""
    dest = Path(project_dir) / HOOKS_DIR / name
    source = _library_copy(config, name)
    try:
        if not dest.is_file() or dest.is_symlink():
            return False
        data = dest.read_bytes()
        return SIGNATURE in data[:SIGNATURE_BYTES] or (source is not None and data == source.read_bytes())
    except OSError:
        return False


def _entry_has_our_shape(project_dir, value) -> bool:
    """The entry is what `link` writes (the interpreter is not compared): `type`, `command` and `refreshInterval` only, the command being an interpreter
    and the absolute path of the script of this project."""
    if not isinstance(value, dict) or set(value) != {"type", "command", "refreshInterval"} or value["refreshInterval"] != STATUSLINE_REFRESH:
        return False
    tokens = _tokens(value["command"])
    return len(tokens) == 2 and bar_is_ours(project_dir)(value)


def _remove_bar(project_dir, config, settings_path, dry_run) -> bool:
    """Removal by CONTENT, never by the path alone. Our entry: it names the script of this project AND (the script is ours: a copy of the library or with the
    signature, or the entry has exactly the shape that `link` writes). The script goes only when it is ours (signature or the bytes of the library); an
    edited copy without the signature stays (with a warning when we have just removed our entry). A foreign entry and a foreign file stay untouched, silently.
    True when our entry was removed. Unreadable settings: nothing is removed (no dangling entry)."""
    ours = bar_is_ours(project_dir)
    data = peek_settings(settings_path)
    script_ours = _script_is_ours(project_dir, config, STATUSLINE_SCRIPT)
    if data is None and settings_path.exists():
        if script_ours:
            merge_setting(settings_path, STATUSLINE_KEY, None, ours, False, dry_run, backup="earliest")   # the warning of the unusable file
        return False
    entry = (data or {}).get(STATUSLINE_KEY)
    had = ours(entry) and (script_ours or _entry_has_our_shape(project_dir, entry))
    removed = had and merge_setting(settings_path, STATUSLINE_KEY, None, lambda value: value == entry, False, dry_run, backup="earliest")
    if had and not removed:
        return False
    if script_ours:
        remove_script(project_dir, STATUSLINE_SCRIPT, dry_run)
    elif removed and bar_script(project_dir).is_file():
        print(f"[warn] {bar_script(project_dir)}: the script of the status line looks edited (no skillsync-managed signature) and was kept; only our entry was removed")
    return removed


def sync_statusline(project_dir, manifest, config, dry_run=False) -> None:
    """`claude.statusline: true`: the copy of the script and the entry (never over a foreign one); otherwise our entry and then our copy are removed.
    A foreign status line or `disableAllHooks` that shows up later removes our entry (the copy by the rule of `_remove_bar`) with a warning.
    An off bar without an entry and without a script of ours is not touched (and the user files are not even read)."""
    project_dir = Path(project_dir)
    settings_path = project_dir / SETTINGS
    if not claude_manifest.statusline(manifest):
        _remove_bar(project_dir, config, settings_path, dry_run)
        return
    blockers = bar_blockers(project_dir)
    if blockers:
        removed = _remove_bar(project_dir, config, settings_path, dry_run)
        for _label, text in blockers:
            print(f"[warn] {text}; " + ("removed the skillsync status line entry" if removed else "the status line of skillsync is not installed"))
        return
    from . import cli
    script = bar_script(project_dir)
    if script.is_file() and not script.is_symlink() and not _script_is_ours(project_dir, config, STATUSLINE_SCRIPT):
        print(f"[warn] {script}: a foreign script of that name exists and was not overwritten; the status line of skillsync is not installed")
        return
    value = bar_value(project_dir)
    existing, ok = load_settings(settings_path)   # refuses an unusable file before anything is copied (no orphaned script)
    if not ok:
        return
    current = existing.get(STATUSLINE_KEY)
    if bar_current(project_dir, current):
        value = current   # up to date: the interpreter does not count, the file stays byte for byte
    elif settings_path.exists() and cli.is_tracked(settings_path):
        print(f"[warn] {settings_path} jest śledzony przez git, pomijam zapis")
        return
    copied = copy_script(project_dir, config, STATUSLINE_SCRIPT, dry_run)
    merge_setting(settings_path, STATUSLINE_KEY, value, bar_is_ours(project_dir), copied, dry_run, backup="earliest")
    if copied:
        cli.ensure_claude_gitignore(project_dir / ".claude", [f"hooks/{STATUSLINE_SCRIPT}", "settings.local.json", "settings.local.json.skillsync-backup"], dry_run=dry_run)


def statusline_findings(root, manifest, layerset=None, home=None):
    """`[(level, path, reason)]` for `claude.statusline: true`: a warning for a foreign bar, `disableAllHooks` or an unreadable file (the file and its
    priority), for our entry whose script is missing and for a copy that differs from the library. Nothing when the bar is off (K8)."""
    if not claude_manifest.statusline(manifest):
        return []
    root = Path(root)
    found = [("warning", label, text) for label, text in bar_blockers(root, home)]
    copy = root / HOOKS_DIR / STATUSLINE_SCRIPT
    rel = f"{HOOKS_DIR}/{STATUSLINE_SCRIPT}"
    if bar_is_ours(root)((peek_settings(root / SETTINGS) or {}).get(STATUSLINE_KEY)):
        if not copy.is_file():
            found.append(("warning", rel, "The entry statusLine exists but its script is missing: the status line shows nothing; run `skillsync link`"))
        elif _source_differs(layerset, STATUSLINE_SCRIPT, copy):
            found.append(("warning", rel, "The copy of the status line script differs from the library (outdated or edited copy); run `skillsync link`"))
    command = (peek_settings(root / SETTINGS) or {}).get(STATUSLINE_KEY)
    tokens = _tokens(command.get("command", "")) if isinstance(command, dict) else []
    if tokens and bar_is_ours(root)(command) and not (os.path.isfile(tokens[0]) or shutil.which(tokens[0])):
        found.append(("info", rel, f"the interpreter of the status line command was not found: {tokens[0]}"))
    found.append(("info", rel, BAR_VERSION_NOTE))
    return found


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
    if not claude_manifest.enabled(manifest):
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
            found.append(("warning", rel, "The entry of the hook exists but its script is missing: the hook does nothing; run `skillsync link`"))
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
