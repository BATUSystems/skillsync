"""`skillsync dashboard serve --detach` and `skillsync dashboard stop` (CORE-2000-UNIFIED, U-6a).

`--detach` runs the SAME `dashboard serve` command line (without the flag) in a process of its own that outlives the terminal, waits until the status
page answers and prints its address; the console of the server goes to a log file in the machine state directory. A directory of a project whose server
already runs is only added as a tab (as without the flag), so nothing is started then. There is no own stop mechanism: `dashboard stop` is
`servers stop --kind dashboard` for the directories of this project (`servers.stop_servers`), and a start sweeps the leftovers of dead servers first
(`servers.sweep_dead`). On Windows the server gets a hidden console and a process group of its own, so the gentle stop (CTRL_BREAK) reaches it and nothing else, and leaves
the job object of the terminal (an SSH session ends every process of its job when it closes).
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import dashboard_ports, dashboard_token, dashboard_worktrees, i18n, pathfmt, servers

READY_TIMEOUT_S = 30.0       # the wait for the page of the started server
READY_POLL_S = 0.2
END_WAIT_S = 5.0             # how long a server that did not become ready gets to end gently
KEPT_LOGS = 5                # logs of one project directory that stay (the newest)
TAIL_LINES = 20
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
ACCESS_DENIED = 5            # Windows: `CreateProcess` in a job that does not let a process leave it


def child_command(argv, executable=None) -> list[str]:
    """The command of the server: this command line without `--detach` (also abbreviated: whatever the parser takes for it, else the server would detach
    again, without end). The entry point is run the way it is a file: a Windows launcher (`skillsync.exe`) directly, `__main__.py` as the module
    (`-m skillsync_workflow`), another script through this interpreter. The tokens `skillsync ... dashboard serve` stay, `servers` checks them."""
    entry, rest = argv[0], [item for item in argv[1:] if not (len(item) >= len("--d") and "--detach".startswith(item))]
    launcher = Path(entry)
    if launcher.name == "__main__.py":
        return [executable or sys.executable, "-m", launcher.parent.name, *rest]
    if launcher.suffix.lower() != ".exe" and not launcher.is_file() and Path(entry + ".exe").is_file():
        launcher = Path(entry + ".exe")
    if launcher.suffix.lower() == ".exe":
        return [str(launcher), *rest]
    return [executable or sys.executable, str(launcher), *rest]


def spawn_options(windows: bool, breakaway: bool = True) -> dict:
    """The `Popen` keywords that cut the server off from the terminal of the command. Windows: an SSH session and some terminals put their processes
    in a job object that ends them all when it closes, so the server leaves that job (`breakaway`)."""
    if windows:
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | (CREATE_BREAKAWAY_FROM_JOB if breakaway else 0)}
    return {"start_new_session": True}


def start_server(popen, command, windows: bool, **keywords):
    """`(process, tied)` of the started server. `tied`: Windows refused to let it leave the job of the terminal (access denied), so it was started
    inside and ends with that terminal or session."""
    try:
        return popen(command, **keywords, **spawn_options(windows)), False
    except OSError as error:
        if not windows or getattr(error, "winerror", None) != ACCESS_DENIED:
            raise
    return popen(command, **keywords, **spawn_options(windows, breakaway=False)), True


def open_log(root, rotate: bool = True):
    """A new log file `<state dir>/<digest of the root>-detach-<time>-<pid>.log` (0600, never through a link); older logs of the directory are removed. While a server of the directory runs (`rotate` False) nothing is removed."""
    directory = dashboard_token.state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if not dashboard_token._own_directory(directory):
        raise OSError("the state directory is a link or not the user's own")
    dashboard_token._restrict(directory)
    prefix = f"{dashboard_token._digest(root)}-detach-"
    old = sorted(name for name in os.listdir(directory) if name.startswith(prefix) and name.endswith(".log"))
    for name in old[:max(0, len(old) - (KEPT_LOGS - 1))] if rotate else ():
        try:
            os.unlink(directory / name)
        except OSError:
            pass   # a running server holds it (Windows)
    path = directory / f"{prefix}{time.time_ns():020d}-{os.getpid()}.log"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    dashboard_token._restrict(path)
    return path, os.fdopen(handle, "wb")


def log_tail(path, lines: int = TAIL_LINES) -> str:
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 16384))
            text = stream.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def running_here(plan) -> list[dict]:
    """The live servers (their state files, `/healthz` answered) that show the directory of the plan: its own root, or a tab of a server of the project."""
    found = list(dashboard_token.servers(plan.root))
    if plan.project is not None:
        found += dashboard_token.servers_for_project(plan.project.common_dir, kinds=None)
    own_root = dashboard_token.root_text(plan.root)
    key = dashboard_worktrees.normalize_key(plan.root)
    unique = {}
    for item in found:
        if item.get("root") == own_root or any(dashboard_worktrees.normalize_key(tab["path"]) == key for tab in item["tabs"]):
            unique[(item["port"], item["pid"], item["role"])] = item
    return list(unique.values())


def _identity(item) -> tuple:
    return item["port"], item["pid"], item.get("serverId")


def parent_pid(pid) -> int | None:
    """Windows: the parent of a process (None when it cannot be read)."""
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                               f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').ParentProcessId"], capture_output=True, timeout=20, check=False)
        return int(done.stdout.decode("utf-8", "replace").strip())
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def started_by_us(pid, child_pid, windows: bool, parent=parent_pid) -> bool:
    """Is the server of a state file the process this command started? On Windows a launcher (`skillsync.exe`, the redirector of a virtual environment)
    stands between: there a descendant of the started process counts too."""
    if pid == child_pid:
        return True
    if not windows:
        return False
    for _ in range(3):
        pid = parent(pid) if pid else None
        if pid == child_pid:
            return True
    return False


def child_answer(text: str, as_json: bool) -> tuple[str | None, str]:
    """`(answer, rest)` of a server process that only added a tab: the JSON report or the address lines, and the other lines of its log."""
    if as_json:
        position = len(text)
        while (position := text.rfind("{", 0, position)) >= 0:
            if position and text[position - 1] != "\n":
                continue
            try:
                value, end = json.JSONDecoder().raw_decode(text, position)
            except ValueError:
                continue
            if isinstance(value, dict) and "status" in value:
                return text[position:end], (text[:position] + text[end:]).strip()
        return None, text.strip()
    lines = text.splitlines()
    found = [line for line in lines if line.startswith(("http://", "https://"))]
    return ("\n".join(found) or None), "\n".join(line for line in lines if line not in found).strip()


def interrupt_on_signals() -> list:
    """While the command waits, SIGTERM and SIGHUP (`timeout`, a closed terminal) end it like Ctrl+C, so the server it started is ended too. A signal that
    is ignored (`nohup`) stays ignored. Returns what `restore_signals` needs; nothing outside the main thread."""
    if threading.current_thread() is not threading.main_thread():
        return []

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    numbers = [getattr(signal, name, None) for name in ("SIGINT", "SIGTERM", "SIGHUP")]
    return [(number, signal.signal(number, interrupt if number != signal.SIGINT else signal.getsignal(number)))
            for number in numbers if number is not None and signal.getsignal(number) != signal.SIG_IGN]


def ignore_signals(previous) -> None:
    for number, _handler in previous:
        signal.signal(number, signal.SIG_IGN)


def restore_signals(previous) -> None:
    for number, handler in previous:
        signal.signal(number, handler if handler is not None else signal.SIG_DFL)


def all_sockets(records) -> bool:
    """Two-port mode: the state file of one socket names the other one (`peer`); the server is ready when that one is there too."""
    ports_seen = {item["port"] for item in records}
    return all(item.get("peer") in (None, *ports_seen) for item in records)


def wait_until_ready(child, plan, before: set, timeout: float, find=running_here, sleep=time.sleep, monotonic=time.monotonic):
    """The records (all its sockets) of the new server of this directory, `[]` when the process ended first, None when the time is over."""
    deadline = monotonic() + timeout
    while True:
        new = [item for item in find(plan) if _identity(item) not in before]
        if new and all_sockets(new):
            sleep(READY_POLL_S)   # several tabs or sockets write their files one after the other
            return [item for item in find(plan) if _identity(item) not in before] or new
        if child.poll() is not None:
            return []
        if monotonic() >= deadline:
            return None
        sleep(READY_POLL_S)


def end_child(child, deps=None) -> None:
    """End the own child that did not become a server: the gentle stop first, then the kill."""
    if child.poll() is not None:
        return
    (deps or servers.Deps()).terminate(child.pid)
    try:
        child.wait(END_WAIT_S)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def page_address(records, plan) -> tuple[dict, str]:
    """`(record, address)` to print: the socket with the consents when there is one; a tab of a project server by its id."""
    record = next((item for item in records if item.get("approvals")), records[0])
    host = dashboard_token.address_host(record.get("host") or "127.0.0.1", record.get("hosts") or ())
    key = dashboard_worktrees.normalize_key(plan.root)
    tab = next((item for item in record["tabs"] if dashboard_worktrees.normalize_key(item["path"]) == key), None)
    if record.get("kind") == "project" and tab is not None:
        return record, dashboard_token.tab_address(record["port"], tab["id"], host)
    return record, dashboard_token.address(record["port"], host)


def serve_detached(args, argv=None, popen=subprocess.Popen, timeout: float = READY_TIMEOUT_S) -> int:
    """`dashboard serve --detach`. Exit codes: 0 the page runs (or the directory was added as a tab), 2 BLOCKED (`detach_failed`: the server ended before it
    answered, its last lines go to stderr; `detach_timeout`; `invalid_project`)."""
    from .common import emit

    plan = dashboard_token.plan_serve(args)
    if plan.mode == "append":
        code = dashboard_token.append_command(args, plan)
        if code is not None:
            return code
        plan = dashboard_token.plan_serve(args)   # the server stopped between the two looks
    t = lambda key, **params: i18n.t(plan.lang, key, **params)   # noqa: E731

    def blocked(code: str, reason: str) -> int:
        return dashboard_token.blocked_report(args, code, reason)

    if plan.legacy:   # a server of an earlier version: the same refusal as `dashboard serve` gives, and no process is started
        return blocked("legacy_server", t("srv.serve.legacy", port=plan.legacy[0]["port"], pid=plan.legacy[0]["pid"]))
    if plan.root is None:
        return blocked("invalid_project", t("srv.detach.invalidProject", path=args.path))
    try:
        servers.sweep_dead(kind="dashboard", roots=(str(plan.root),))
        before = {_identity(item) for item in running_here(plan)}
        log_path, log = open_log(plan.root, rotate=not before)
    except OSError as error:
        return blocked("detach_failed", t("srv.detach.noLog", reason=" ".join(str(error).split())[:300]))
    command = child_command(list(argv if argv is not None else sys.argv))
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        with log:
            child, tied = start_server(popen, command, os.name == "nt", stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=environment)
    except OSError as error:
        return blocked("detach_failed", t("srv.detach.noStart", reason=" ".join(str(error).split())[:300]))
    windows = os.name == "nt"

    parent = functools.lru_cache(maxsize=None)(parent_pid)

    def own(current):   # only the server of this command: another start may run at the same moment
        return [item for item in running_here(current) if _identity(item) not in before and started_by_us(item["pid"], child.pid, windows, parent)]

    previous = interrupt_on_signals()
    try:
        records = wait_until_ready(child, plan, before, timeout, find=own)
        if records is None:
            end_child(child)
    except BaseException:
        ignore_signals(previous)   # a second Ctrl+C must not interrupt the ending of the server
        end_child(child)   # Ctrl+C of the waiting command: no server is left behind that nobody was told about
        raise
    finally:
        restore_signals(previous)
    if records is None:
        print(log_tail(log_path), file=sys.stderr)
        return blocked("detach_timeout", t("srv.detach.timeout", seconds=f"{timeout:g}", log=str(log_path)))
    if not records:
        code = child.poll()
        if code == 0:   # the child added a tab to a server that had only just started: its answer is the answer
            answer, rest = child_answer(log_tail(log_path, 200), bool(getattr(args, "json", False)))
            if answer is not None:
                if rest:
                    print(rest, file=sys.stderr)
                print(answer)
                return 0
        print(log_tail(log_path), file=sys.stderr)
        return blocked("detach_failed", t("srv.detach.ended", code=code, log=str(log_path)))
    record, url = page_address(records, plan)
    print(t("srv.detach.running", pid=record["pid"], port=record["port"]), file=sys.stderr)
    print(t("srv.detach.log", log=str(log_path)), file=sys.stderr)
    for item in records:   # a socket of the page on an address without a local sign-in also listens on 127.0.0.1 (the address printed below)
        bound = item.get("host")
        if item.get("role") != dashboard_ports.ROLE_READ and bound and dashboard_ports.loopback_companion(bound):
            shown = f"[{bound}]" if ":" in bound else bound
            print(t("srv.serve.loopbackBeside", local=dashboard_ports.LOOPBACK_BIND, port=item["port"],
                    url=dashboard_token.address(item["port"], shown)), file=sys.stderr)
    directory = str(plan.root)
    shown = f'"{directory}"' if " " in directory else directory
    print(t("srv.detach.stop", path=f"{shown} --port {record['port']}"), file=sys.stderr)   # the port: only this server, not the shared one beside it
    if tied:
        print(t("srv.detach.tied"), file=sys.stderr)
    if getattr(args, "json", False):
        return emit({"schema": 1, "status": "PASS", "mode": "detached", "url": url, "port": record["port"], "pid": record["pid"], "log": pathfmt.posix(str(log_path))})
    print(url)
    return 0


def stop_command(args) -> int:
    """`dashboard stop`: `servers stop --kind dashboard` for this directory and the main checkout of its project (the project server is recorded there).
    Output and exit codes are the ones of `servers stop` (0 stopped or nothing to stop, 1 still running, 2 BLOCKED, 3 dry run)."""
    from .common import read_project

    try:
        root, manifest = read_project(args.path)[:2]
    except Exception as error:  # noqa: BLE001 - any broken project is BLOCKED, as for the other `dashboard` commands
        return dashboard_token.blocked_report(args, "invalid_project", " ".join(str(error).split())[:300])
    roots = [str(root)]
    project = dashboard_worktrees.project_identity(root)
    main = project.main_path if project is not None else None
    if main and dashboard_worktrees.normalize_key(main) != dashboard_worktrees.normalize_key(root):
        roots.append(str(main))
        main_text = dashboard_token.root_text(main)
        # read only (`--dry-run` changes nothing, and a leftover file stays for the stop to report): the project server is recorded under the main checkout
        if any(item["alive"] and item.get("root") == main_text and args.port in (None, item["port"]) for item in servers.list_servers("dashboard")):
            print(i18n.t(i18n.resolve_language(manifest)[0], "srv.detach.shared"), file=sys.stderr)
    return servers.cmd_servers_stop(argparse.Namespace(root=roots, port=args.port, all=False, kind="dashboard", timeout=args.timeout, force=args.force,
                                                       dry_run=args.dry_run, json=args.json))


def register(actions, serve) -> None:
    """Adds `--detach` to the parser of `dashboard serve` and the action `dashboard stop`."""
    serve.add_argument("--detach", action="store_true",
                       help="run the server in the background: wait until the page answers, print its address and return; its console goes to a log file in "
                            "the machine state directory (`--json`: {schema, status, mode, url, port, pid, log}); stop it with `dashboard stop`")
    stop = actions.add_parser("stop", help="Gently stop the `dashboard serve` of the project (in the foreground or detached)",
                              description="Stop the status page servers of this project, from any of its directories: `servers stop --kind dashboard` for the "
                                          "directory and the main checkout (SIGTERM; Windows CTRL_BREAK), after the process was checked against its record. "
                                          "A project server is shared by every worktree of the project. Exit codes: 0 stopped or nothing to stop, "
                                          "1 a server still runs, 2 BLOCKED, 3 dry run.")
    stop.add_argument("path", nargs="?", default=".", help="Project path (default: .)")
    stop.add_argument("--port", type=int, default=None, help="only the server of the project on this port")
    stop.add_argument("--timeout", type=servers._timeout, default=servers.DEFAULT_TIMEOUT, metavar="SECONDS",
                      help=f"seconds to wait for the stop (default {servers.DEFAULT_TIMEOUT})")
    stop.add_argument("--force", action="store_true", help="end a server that did not stop in time, only after the identity check")
    stop.add_argument("--dry-run", action="store_true", help="print what would be stopped and change nothing (exit code 3)")
    stop.add_argument("--json", action="store_true", help="print the result as JSON")
    stop.set_defaults(func=stop_command)
