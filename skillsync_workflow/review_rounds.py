"""`skillsync review-round start|freeze`: a review round saved without the memory of the session.

`start` picks the run directory (creating it when missing), numbers the round from the report FILES of the run and opens a run of the
`reviewer` in the log `agents.jsonl` (`agent_runs`); `freeze` saves the report as `<prefix>-N.md` (never overwriting) and closes the run
with the result. The number N comes only from the files: the page counts `Review ×N` from them. Counting N, closing the earlier open
round of the same profile and step, and appending `start` happen in one critical section under the lock of `agent_runs`.
"""
import os
import re
import sys
from pathlib import Path

from . import agent_runs
from .common import read_project

KINDS = ("spec", "plan", "code")
MAX_REPORT = 1024 * 1024
FREEZE_RESULTS = ("PASS", "CHANGES_REQUIRED", "BLOCKED", "NOT_RUN")
RESULT_LINE = re.compile(r"^[ \t]*(?:[-*>#]+[ \t]+)?(?:\*\*|__)?(?:Wynik|Result|Status)(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*([A-Z_]+)", re.M)
SPEC_OPEN = ("draft", "approved")


def report_prefix(kind, step):
    if kind == "code":
        return "code-review" + (f"-{step}" if step else "")
    return f"{kind}-review"


def _check_kind_step(kind, step):
    if kind not in KINDS:
        raise ValueError(f"--kind: jedna z {', '.join(KINDS)}")
    if step is not None and kind != "code":
        raise ValueError("--step dotyczy tylko --kind code")
    return agent_runs.check_step(step, "review")


def used_numbers(directory, kind, step):
    """Numbers N of the report files `<prefix>-N.md` (also `-rN`, any letter case, as the page counts them); `-decyzje.md`,
    `-verifier.md` and other suffixes do not match."""
    pattern = re.compile(re.escape(report_prefix(kind, step)) + r"-r?([0-9]+)\.md$", re.I)
    found = []
    for item in Path(directory).iterdir():
        match = pattern.fullmatch(item.name)
        if match and item.is_file() and not item.is_symlink():
            found.append(int(match.group(1)))
    return found


def next_number(directory, kind, step):
    number = max(used_numbers(directory, kind, step), default=0) + 1
    if number > 99:
        raise ValueError("Numer rundy ponad 99: zamknij przebieg i zacznij nowy")
    return number


def _inside_root(root, value, what):
    if value is None:
        return None
    path = Path(value)
    if ".." in path.parts:
        raise ValueError(f"{what}: ścieżka z `..`")
    full = (path if path.is_absolute() else Path(root) / path).resolve()
    if not full.is_relative_to(Path(root).resolve()) or not full.is_file():
        raise ValueError(f"{what}: plik musi istnieć wewnątrz projektu")
    return full


def resolve_run_id(root, kind, run_id, spec=None, plan=None):
    """Run id as the status page finds it: `--run-id`; spec round: the run of the spec (its `Przebieg:`, the directory of the `Plan:`
    line, the directory named like the spec; the first one that exists, else a new one under the first name); plan and code rounds:
    the `Przebieg:` of the current plan (`choose_plan`) or of `--plan`."""
    if run_id is not None:
        if not agent_runs.RUN_ID_RE.fullmatch(run_id):
            raise ValueError(f"--run-id: wzorzec {agent_runs.RUN_ID_RE.pattern}")
        return run_id
    from . import dashboard
    base = Path(root).resolve()
    spec_path, plan_path = _inside_root(base, spec, "--spec"), _inside_root(base, plan, "--plan")
    if kind == "spec":
        if spec_path is None:
            plans, specs = dashboard.list_plans(base), dashboard.list_specs(base)
            chosen = dashboard.choose_plan(plans)
            candidate = dashboard.choose_spec_act(plans, specs, None)
            rel = candidate["path"] if candidate else (chosen or {}).get("spec")
            spec_path = base / rel if rel and (base / rel).is_file() else None
        if spec_path is None:
            raise ValueError("Nie wybrano speca: podaj --spec <plik> albo --run-id")
        fields = dashboard.parse_header(dashboard.read_masked(spec_path, 400_000))[1]
        stem = spec_path.stem
        names = []
        for value in (fields.get("przebieg") or fields.get("run"), fields.get("plan")):
            token = dashboard.first_token(value)
            if token:
                names.append(Path(token).stem if value is fields.get("plan") else token)
        names.append(stem)
        names = [name for name in names if agent_runs.RUN_ID_RE.fullmatch(name)]
        if not names:
            raise ValueError("Nie ustalono przebiegu speca: podaj --run-id")
        runs = base / ".skillsync" / "runs"
        for name in names:
            if (runs / name).is_dir():
                return name
        return names[0]
    if plan_path is not None:
        fields = dashboard.parse_header(dashboard.read_masked(plan_path, 400_000))[1]
        token = dashboard.first_token(fields.get("przebieg") or fields.get("run"))
    else:
        chosen = dashboard.choose_plan(dashboard.list_plans(base))
        token = chosen.get("run") if chosen else None
    if not token or not agent_runs.RUN_ID_RE.fullmatch(token):
        raise ValueError("Nie ustalono przebiegu planu (brak planu albo linii `Przebieg:`): podaj --run-id")
    return token


def start(root, kind, step=None, run_id=None, spec=None, plan=None, eta=None):
    """(run id, round number, agent id, report file name) of the opened round."""
    _check_kind_step(kind, step)
    run = resolve_run_id(root, kind, run_id, spec, plan)
    directory = agent_runs.run_directory(root, run, create=True)
    with agent_runs.locked(directory):
        number = next_number(directory, kind, step)
        agent_runs.validate_start("reviewer", "review", kind, step, number, eta)
        agent = agent_runs.start_locked(directory, "reviewer", "review", kind, step, number, eta)
    return run, number, agent, f"{report_prefix(kind, step)}-{number}.md"


def result_of(text, given):
    if given is not None:
        found = given
    else:
        match = RESULT_LINE.search(text)
        found = match.group(1) if match else None
    if found not in FREEZE_RESULTS:
        raise ValueError("Wynik rundy: podaj --result " + "|".join(FREEZE_RESULTS) + " albo linię `Wynik: <wynik>` w raporcie")
    return found


def _write_new(directory, name, text):
    """Creates `<directory>/<name>` atomically and never over an existing file (`FileExistsError` when it exists)."""
    final = Path(directory) / name
    data = text.encode("utf-8")
    temporary = Path(directory) / f".{name}.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.link(temporary, final)
    except FileExistsError:
        raise
    except OSError:   # a file system without hard links: exclusive create
        try:
            handle = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise
        try:
            os.write(handle, data)
        finally:
            os.close(handle)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def freeze(root, kind, text, step=None, run_id=None, spec=None, plan=None, result=None):
    """(run id, round number, agent id, result, report file name) of the closed round."""
    _check_kind_step(kind, step)
    if not text.strip():
        raise ValueError("Raport jest pusty")
    if len(text.encode("utf-8")) > MAX_REPORT:
        raise ValueError("Raport przekracza 1 MiB")
    final_result = result_of(text, result)
    run = resolve_run_id(root, kind, run_id, spec, plan)
    directory = agent_runs.run_directory(root, run, create=True)
    with agent_runs.locked(directory):
        opened = [(agent, item) for agent, item in agent_runs.open_runs(agent_runs.read_events(directory)).items()
                  if item.get("kind") == "review" and item.get("profile") == kind and item.get("step") == step]
        number = next_number(directory, kind, step)
        wanted = opened[-1][1].get("round") if opened else None
        if isinstance(wanted, int) and wanted not in used_numbers(directory, kind, step):
            number = wanted
        while True:
            name = f"{report_prefix(kind, step)}-{number}.md"
            try:
                _write_new(directory, name, text)
                break
            except FileExistsError:   # written by hand meanwhile: the next free number, never an overwrite
                number = max(number, next_number(directory, kind, step) - 1) + 1
                if number > 99:
                    raise ValueError("Numer rundy ponad 99: zamknij przebieg i zacznij nowy") from None
        if opened:
            agent = opened[-1][0]
        else:
            agent = agent_runs.start_locked(directory, "reviewer", "review", kind, step, number, None)
        agent_runs.finish_locked(directory, agent, final_result, name)
    return run, number, agent, final_result, name


# --- command line -----------------------------------------------------------------------------------------------------------

def _print(args, items):
    if args.json:
        import json
        print(json.dumps(items, ensure_ascii=False))
    else:
        print(" ".join(f"{key}={value}" for key, value in items.items() if value is not None))


def cmd_review_round(args):
    root, _ = read_project(args.path, getattr(args, "manifest", None))
    root = Path(root).resolve()
    if args.action == "start":
        run, number, agent, name = start(root, args.kind, args.step, args.run_id, args.spec, args.plan, args.eta)
        _print(args, {"round": number, "agent": agent, "run": run,
                      "report": f".skillsync/runs/{run}/{name}"})
        return 0
    if args.file is not None and args.file != "-":
        source = _regular_file(args.file)
        if source.stat().st_size > MAX_REPORT:
            raise ValueError("Raport przekracza 1 MiB")
        text = source.read_bytes().decode("utf-8", "replace")
    else:
        text = sys.stdin.buffer.read(MAX_REPORT * 2).decode("utf-8", "replace")
    run, number, agent, final, name = freeze(root, args.kind, text, args.step, args.run_id, args.spec, args.plan, args.result)
    _print(args, {"round": number, "agent": agent, "run": run, "result": final, "report": f".skillsync/runs/{run}/{name}"})
    return 0


def _regular_file(value):
    path = Path(value)
    if not path.is_file() or path.is_symlink():
        raise ValueError("--file: zwykły plik (nie dowiązanie)")
    return path


def register(subparsers):
    parser = subparsers.add_parser("review-round", help="Start and freeze a review round (spec, plan, code): numbers it, saves the report, feeds the status page")
    actions = parser.add_subparsers(dest="action", required=True)
    for name, text in (("start", "open a round (prints round=N agent=<id> report=<path>)"), ("freeze", "save the report of the round and close it")):
        item = actions.add_parser(name, help=text)
        item.add_argument("path", nargs="?", default=".")
        item.add_argument("--kind", required=True, choices=KINDS)
        item.add_argument("--step", help="only for --kind code: the step of the plan (code-review-<step>-N.md)")
        item.add_argument("--run-id")
        item.add_argument("--spec", metavar="PLIK", help="spec whose run is meant (for --kind spec)")
        item.add_argument("--plan", metavar="PLIK", help="plan whose run is meant (for --kind plan and code)")
        item.add_argument("--json", action="store_true")
        item.add_argument("--manifest", metavar="PLIK", help="manifest projektu spoza repozytorium")
        item.set_defaults(func=cmd_review_round)
        if name == "start":
            item.add_argument("--eta", help="HH:MM from `date +%%H:%%M`")
        else:
            item.add_argument("--file", help="the report file, or - (default) for stdin")
            item.add_argument("--result", choices=FREEZE_RESULTS, help="otherwise the line `Wynik: <result>` of the report")
