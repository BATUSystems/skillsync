"""Log of the runs of sub-agents (`agent-run`): `.skillsync/runs/<run-id>/agents.jsonl`.

One line per event (`start`, `eta`, `end`), written ONLY by the orchestrator (a sub-agent never calls it). The status page reads the log
to show what runs now (role, round, start, term, result); a run is "open" while it has a `start` and no `end`. Nothing of a session or of a
report is stored: every field is a short identifier from a closed list, a number, a time or a file name.

Writers are serialised by a blocking lock (`.agents.lock` in the run directory, retried up to `LOCK_TIMEOUT` seconds) and every event is
one `O_APPEND` write below 4 KiB. `review_rounds` (the `review-round` command) uses `start_locked`/`finish_locked` under the same lock.
"""
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .common import read_project

ROLES = ("reviewer", "verifier", "implementer", "test-writer", "test-runner", "scribe", "mechanic", "explore")
KINDS = ("review", "run")
PROFILES = ("spec", "plan", "code", "final", "docs", "prototype", "retro", "pre-implement")
# the results a role may report (the roles' own vocabulary: `verifier` gives verdicts, the others a status); `ABORTED` is for any
RESULTS_BY_ROLE = {
    "reviewer": ("PASS", "CHANGES_REQUIRED", "BLOCKED", "NOT_RUN", "ABORTED"),
    "verifier": ("CONFIRMED", "REFUTED", "UNDETERMINED", "BLOCKED", "NOT_RUN", "ABORTED"),
    "test-runner": ("PASS", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
    "implementer": ("DONE", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
    "test-writer": ("DONE", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
    "scribe": ("DONE", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
    "mechanic": ("DONE", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
    "explore": ("DONE", "FAIL", "BLOCKED", "NOT_RUN", "ABORTED"),
}
RESULTS = tuple(dict.fromkeys(item for values in RESULTS_BY_ROLE.values() for item in values))
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")   # the same pattern as the status page (`dashboard.RUN_ID_RE`)
STEP_RUN_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
STEP_REVIEW_RE = re.compile(r"^[A-Za-z0-9.]+(-[A-Za-z0-9.]+)*$")   # what the page counts in `code-review-<step>-N.md`
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
REPORT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.md$")
LOG_NAME = "agents.jsonl"
LOCK_NAME = ".agents.lock"
LOCK_TIMEOUT = 10.0
MAX_LOG_BYTES = 4 * 1024 * 1024   # the writer refuses to grow the log beyond this (a runaway loop, not a normal run)
_clock = lambda: datetime.now().astimezone()   # noqa: E731 (tests replace it)


def now_text():
    return _clock().isoformat(timespec="seconds")


def check_step(value, kind):
    if value is None:
        return None
    pattern = STEP_REVIEW_RE if kind == "review" else STEP_RUN_RE
    if not isinstance(value, str) or len(value) > 32 or not pattern.fullmatch(value):
        raise ValueError(f"--step: wzorzec {'[A-Za-z0-9.]+(-[A-Za-z0-9.]+)*' if kind == 'review' else '[A-Za-z0-9._-]{1,32}'} (otrzymano: {str(value)[:40]!r})")
    return value


def run_directory(root, run_id, create=False):
    """`<root>/.skillsync/runs/<run-id>` (created when `create`); a symlink anywhere on the way is a refusal."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"--run-id: wzorzec {RUN_ID_RE.pattern} (otrzymano: {str(run_id)[:40]!r})")
    base = Path(root).resolve()
    chain = [base / ".skillsync", base / ".skillsync" / "runs", base / ".skillsync" / "runs" / run_id]
    for item in chain:
        if item.is_symlink():
            raise ValueError("Katalog przebiegu nie może być dowiązaniem")
    directory = chain[-1]
    if not directory.is_dir():
        if not create:
            raise ValueError(f"Brak katalogu przebiegu {run_id}: załóż go (`mkdir -p .skillsync/runs/{run_id}`) albo otwórz rundę review `review-round start`")
        directory.mkdir(parents=True, exist_ok=True)
    return directory


class _Lock:
    """Blocking exclusive lock on `.agents.lock` of a run directory (fcntl, msvcrt on Windows), retried until the timeout."""

    def __init__(self, directory):
        path = Path(directory) / LOCK_NAME
        if path.is_symlink():
            raise ValueError("Plik blokady nie może być dowiązaniem")
        self.handle = open(path, "a+b")

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    raise ValueError(f"Dziennik biegów jest zablokowany przez inny proces dłużej niż {LOCK_TIMEOUT:g} s") from None
                time.sleep(0.02)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.handle.close()


def locked(directory):
    return _Lock(directory)


def read_events(directory):
    """All valid events of the log in order (a bad line is skipped); the whole file is read: ids come from it."""
    path = Path(directory) / LOG_NAME
    if path.is_symlink():
        raise ValueError("Dziennik biegów nie może być dowiązaniem")
    if not path.is_file():
        return []
    events = []
    for line in path.read_bytes().decode("utf-8", "replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("v") == 1 and item.get("ev") in ("start", "eta", "end") and isinstance(item.get("agent"), str):
            events.append(item)
    return events


def open_runs(events):
    """{agent id: its `start` event} of the runs that have no `end`, in log order."""
    opened = {}
    for item in events:
        if item["ev"] == "start":
            opened[item["agent"]] = item
        elif item["ev"] == "end":
            opened.pop(item["agent"], None)
    return opened


def next_agent_id(events):
    numbers = [int(match.group(1)) for item in events if item["ev"] == "start" for match in [re.fullmatch(r"a-([0-9]{1,6})", item["agent"])] if match]
    return f"a-{max(numbers, default=0) + 1}"


def append_event(directory, event):
    """One `O_APPEND` line; the caller holds the lock."""
    path = Path(directory) / LOG_NAME
    if path.is_symlink():
        raise ValueError("Dziennik biegów nie może być dowiązaniem")
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(line) > 4096:
        raise ValueError("Zdarzenie jest zbyt długie")
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError("Dziennik biegów przekroczył limit rozmiaru")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _event(name, agent, **fields):
    event = {"v": 1, "t": now_text(), "ev": name, "agent": agent}
    event.update({key: value for key, value in fields.items() if value is not None})
    return event


def validate_start(role, kind, profile, step, round_number, eta):
    if role not in ROLES:
        raise ValueError(f"--role: jedna z {', '.join(ROLES)}")
    if kind not in KINDS:
        raise ValueError(f"--kind: jedna z {', '.join(KINDS)}")
    if kind == "review" and role not in ("reviewer", "verifier"):
        raise ValueError("--kind review dotyczy roli reviewer albo verifier")
    if profile is not None and (kind != "review" or profile not in PROFILES):
        raise ValueError(f"--profile (tylko dla --kind review): jedna z {', '.join(PROFILES)}")
    check_step(step, kind)
    if round_number is not None and (not isinstance(round_number, int) or isinstance(round_number, bool) or not 1 <= round_number <= 99):
        raise ValueError("--round: liczba 1-99")
    if eta is not None and not TIME_RE.fullmatch(eta):
        raise ValueError("--eta: HH:MM (godzina z `date +%H:%M`)")


def _same_run(opened, role, kind, profile, step, round_number):
    """Does the open run `opened` count as the earlier run of the same work? Reviews: same profile and step, any round (the rule of
    `review-round`); other runs: same role, step and round."""
    if kind == "review":
        return (opened.get("kind") == "review" and opened.get("role") == role and opened.get("profile") == profile
                and opened.get("step") == step)
    return (opened.get("kind") == "run" and opened.get("role") == role and opened.get("step") == step
            and opened.get("round") == round_number)


def start_locked(directory, role, kind, profile=None, step=None, round_number=None, eta=None, measured=True):
    """Appends `start` (the caller holds the lock and has validated); closes an earlier open run of the same work as `ABORTED`.
    `measured=False` marks a run that was opened only to be closed at once (`review-round freeze` without `start`): its duration was not measured
    and is no duration (`"measured":false` in the event; every other `start` has no such field). Returns the new agent id."""
    events = read_events(directory)
    opened = open_runs(events)
    for agent, item in opened.items():
        if _same_run(item, role, kind, profile, step, round_number):
            append_event(directory, _event("end", agent, result="ABORTED"))
    agent = next_agent_id(events)
    append_event(directory, _event("start", agent, role=role, kind=kind, profile=profile, step=step, round=round_number, eta=eta,
                                   measured=None if measured else False))
    return agent


def start(root, run_id, role, kind, profile=None, step=None, round_number=None, eta=None, create=False):
    """`create` is for `review-round` (it makes the run directory of a spec round); `agent-run start` refuses a run without a directory."""
    validate_start(role, kind, profile, step, round_number, eta)
    directory = run_directory(root, run_id, create=create)
    with locked(directory):
        return start_locked(directory, role, kind, profile, step, round_number, eta)


def move_eta(root, run_id, agent, eta):
    if not isinstance(eta, str) or not TIME_RE.fullmatch(eta):
        raise ValueError("--eta: HH:MM (godzina z `date +%H:%M`)")
    directory = run_directory(root, run_id)
    with locked(directory):
        if agent not in open_runs(read_events(directory)):
            raise ValueError(f"Bieg {agent} nie jest otwarty")
        append_event(directory, _event("eta", agent, eta=eta))


def pick_open(events, agent=None, role=None, step=None, round_number=None):
    """The agent id of the one open run that matches the selectors (`--agent` or `--role` with the optional `--step`, `--round`)."""
    opened = open_runs(events)
    if agent is not None:
        if agent not in opened:
            raise ValueError(f"Bieg {agent} nie jest otwarty (nie ma go w dzienniku albo już się zakończył)")
        return agent
    matches = [name for name, item in opened.items() if (role is None or item.get("role") == role)
               and (step is None or item.get("step") == step) and (round_number is None or item.get("round") == round_number)]
    if len(matches) != 1:
        raise ValueError("Wskaż bieg przez --agent: " + ("żaden otwarty bieg nie pasuje" if not matches else f"pasuje {len(matches)} otwartych biegów"))
    return matches[0]


def finish_locked(directory, agent, result, report=None):
    """Appends `end` for an open run (the caller holds the lock). Returns the role of the run."""
    opened = open_runs(read_events(directory))
    if agent not in opened:
        raise ValueError(f"Bieg {agent} nie jest otwarty (nie ma go w dzienniku albo już się zakończył)")
    role = opened[agent].get("role")
    if result not in RESULTS_BY_ROLE.get(role, RESULTS):
        raise ValueError(f"--result dla roli {role}: jedna z {', '.join(RESULTS_BY_ROLE.get(role, RESULTS))}")
    if report is not None and not REPORT_RE.fullmatch(report):
        raise ValueError("--report: sama nazwa pliku .md, bez ścieżki")
    append_event(directory, _event("end", agent, result=result, report=report))
    return role


def end(root, run_id, result, agent=None, role=None, step=None, round_number=None, report=None):
    directory = run_directory(root, run_id)
    with locked(directory):
        chosen = pick_open(read_events(directory), agent, role, step, round_number)
        finish_locked(directory, chosen, result, report)
    return chosen


# --- command line -----------------------------------------------------------------------------------------------------------

def _root(args):
    root, _ = read_project(args.path, getattr(args, "manifest", None))
    return root


def _print(args, result):
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(" ".join(f"{key}={value}" for key, value in result.items() if value is not None))


def cmd_agent_run(args):
    root = _root(args)
    if args.action == "start":
        agent = start(root, args.run_id, args.role, args.kind, args.profile, args.step, args.round, args.eta)
        _print(args, {"agent": agent})
    elif args.action == "eta":
        move_eta(root, args.run_id, args.agent, args.eta)
        _print(args, {"agent": args.agent, "eta": args.eta})
    else:
        agent = end(root, args.run_id, args.result, args.agent, args.role, args.step, args.round, args.report)
        _print(args, {"agent": agent, "result": args.result})
    return 0


def register(subparsers):
    parser = subparsers.add_parser("agent-run", help="Log of the runs of sub-agents (start, eta, end) for the status page; the orchestrator only",
                                   description="Keep the log of sub-agent runs (.skillsync/runs/<run-id>) that feeds the status page: open a run (start), move its term (eta) "
                                               "or close it with a result (end). Writes the log; meant for the orchestrator only.")
    actions = parser.add_subparsers(dest="action", required=True)
    descriptions = {
        "start": "Open a run of a sub-agent in the log of the run and print its id (agent=<id>). Appends to the log.",
        "eta": "Move the expected end time of an open run of a sub-agent. Appends to the log.",
        "end": "Close an open run of a sub-agent with its result. Appends to the log; the run is chosen by --agent or by --role, --step and --round.",
    }
    for name, text in (("start", "open a run (prints agent=<id>)"), ("eta", "move the term of an open run"), ("end", "close a run with its result")):
        item = actions.add_parser(name, help=text, description=descriptions[name])
        item.add_argument("path", nargs="?", default=".", help="project directory (default: .)")
        item.add_argument("--run-id", required=True, help="run id: the directory .skillsync/runs/<run-id> that holds the log")
        item.add_argument("--json", action="store_true", help="print the result as JSON instead of key=value pairs")
        item.add_argument("--manifest", metavar="FILE", help="project manifest file kept outside the repository")
        item.set_defaults(func=cmd_agent_run)
        if name == "start":
            item.add_argument("--role", required=True, choices=ROLES, help="role of the sub-agent")
            item.add_argument("--kind", required=True, choices=KINDS, help="kind of the run: review (reviewer or verifier role) or run")
            item.add_argument("--profile", choices=PROFILES, help="review profile; only for --kind review")
            item.add_argument("--step", help="step of the plan the run belongs to")
            item.add_argument("--round", type=int, help="number of the review round, 1-99")
            item.add_argument("--eta", help="expected end time as HH:MM from `date +%%H:%%M`")
        elif name == "eta":
            item.add_argument("--agent", required=True, help="id of the open run to move (as printed by start, e.g. a-1)")
            item.add_argument("--eta", required=True, help="new expected end time as HH:MM from `date +%%H:%%M`")
        else:
            item.add_argument("--agent", help="id of the open run to close (as printed by start); without it the run is picked by --role, --step and --round")
            item.add_argument("--role", choices=ROLES, help="role of the run to close (used when --agent is not given)")
            item.add_argument("--step", help="step of the run to close (used when --agent is not given)")
            item.add_argument("--round", type=int, help="review round of the run to close (used when --agent is not given)")
            item.add_argument("--result", required=True, choices=RESULTS, help="result of the run; the allowed values depend on the role")
            item.add_argument("--report", help="name of the report file in the run directory")
