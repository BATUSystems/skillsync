"""Start of `dashboard serve` in a subprocess for tests, robust against the race of `free_port()` (a port that WAS free can be taken before the bind).

A server that lost the bind exits with BLOCKED, while the OTHER server on that port still answers `/healthz`, so an answer proves nothing. The
start is ready only when the state file of our own pid and port exists (written after the bind, also with `--no-approvals`); a lost bind is retried
on another port. Tests only: no production code is involved."""
from datetime import datetime, timedelta, timezone
import errno
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from unittest import mock

ATTEMPTS = 4
ADDRESS_IN_USE = {errno.EADDRINUSE, 10048}   # 10048 = WSAEADDRINUSE (Windows reports its own code)


def bind_free(factory, free_port, attempts=5):
    """Bind a server in the test process on a port from `free_port()`, retrying when the port was taken before the bind.

    `factory(port)` creates the bound server (e.g. `make_server`); an `OSError` "address in use" moves on to the next port from the pool, any other
    error is raised at once. Returns `(server, port)`. For servers in a thread: a subprocess uses `start_serve`."""
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    last, tried = None, []
    for _attempt in range(attempts):
        port = free_port()
        tried.append(port)
        try:
            return factory(port), port
        except OSError as error:
            if error.errno not in ADDRESS_IN_USE:
                raise
            last = error
    raise OSError(last.errno, f"no free port after {attempts} tries (ports {tried}): {last.strerror}") from last


def state_files(env):
    """State files of the servers started with `env` (the directory `dashboard_token.state_dir()` resolves to under that environment)."""
    from skillsync_workflow import dashboard_token
    with mock.patch.dict(os.environ, env):
        directory = dashboard_token.state_dir()
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def state_snapshot(env):
    """`{state file: mtime_ns}` of the servers started with `env`, taken before `Popen` (SV1): an unchanged file is a leftover, never ours."""
    snapshot = {}
    for path in state_files(env):
        try:
            snapshot[path] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def _started_at(record):
    try:
        return datetime.fromisoformat(str(record.get("startedAt")))
    except ValueError:
        return None


def own_state(env, proc, port, root=None, started=None, windows=None, before=None):
    """Is there a state file of THIS server? Only the server that holds the port writes one with its pid. POSIX: the pid must be ours, strictly.
    Windows (`windows=True`, default `os.name == "nt"`): `proc.pid` is the launcher of the venv `python.exe`, not the interpreter that serves, so
    the identity is the port, the project root of the file (the unique temporary directory of the test) and a `startedAt` not older than
    the start of our process (`started`, an aware `datetime` taken before `Popen`; one second of slack for the whole seconds of the file).
    `before` (`state_snapshot` taken before `Popen`) rules out a file that is still the one it was before the start: the killed predecessor of
    the same root and port leaves exactly such a file (SV1)."""
    from skillsync_workflow import dashboard_token
    windows = (os.name == "nt") if windows is None else windows
    for path in state_files(env):
        try:
            if before is not None and before.get(path) == path.stat().st_mtime_ns:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("port") != port:
            continue
        if not windows:
            if record.get("pid") == proc.pid:
                return True
            continue
        at = _started_at(record)
        # A project server started in a worktree keeps the MAIN checkout as `root` and lists the directory it was started in among `tabs[]` (state schema 2).
        wanted = dashboard_token.root_text(root) if root is not None else None
        roots = {record.get("root"), *(tab.get("path") for tab in record.get("tabs") or [] if isinstance(tab, dict))}
        if (wanted is not None and started is not None and at is not None and wanted in {r for r in roots if isinstance(r, str)}
                and at >= started - timedelta(seconds=1)):
            return True
    return False


def release(proc):
    """The cleanup of a test that started a server: kill it, wait for it and close its pipes (an unclosed pipe is a `ResourceWarning`)."""
    kill_tree(proc)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    for stream in (proc.stdout, proc.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def kill_tree(proc, windows=None):
    """Kill the process of the test and, on Windows, its whole tree: `proc.pid` is the launcher of the venv `python.exe`, `Popen.kill()` ends only
    that launcher and the interpreter that serves would keep the port. `taskkill /T /F` by our OWN pid, by the full path under the system
    directory (no PATH lookup); POSIX and every failure of taskkill fall back to `proc.kill()`."""
    windows = (os.name == "nt") if windows is None else windows
    if windows and proc.poll() is None:
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
    if proc.poll() is None:
        proc.kill()


def stop_serve(proc, windows=None):
    """Stop the server of a test and collect its output: returns `(out, err, graceful)`.

    POSIX: SIGTERM, the server stops itself (exit 0, JSON report on stdout, state file removed) and `graceful` is True. Windows: the production
    code handles only SIGTERM, so `terminate()` is `TerminateProcess` (no report, exit code, or cleanup of the state file); the tree is killed
    and `graceful` is False, which tells the test to skip the assertions about a graceful stop (a candidate for a separate production item)."""
    windows = (os.name == "nt") if windows is None else windows
    if windows:
        kill_tree(proc, windows=True)
    else:
        proc.terminate()
    out, err = proc.communicate(timeout=15)
    return out, err, not windows


def lost_bind(output):
    """Does the output of a finished server say that the BIND was lost (retry on another port)? Another BLOCKED (a configuration error) is not retried (SV4).

    It matches the Polish word of the production message ("nie można nasłuchiwać") in the decoded stdout. A Windows run without `PYTHONUTF8=1`
    (CI sets it) can decode the word wrongly: the retry is then off and the start fails at once, which is safe but not obvious."""
    return "BLOCKED" in output and "nasłuchiwać" in output


# The variables that decide where a server writes its state, as the REAL user has them: read when this module is imported, before any test patches
# `os.environ` (`home_env` patches it with the isolated values, so a comparison with the live `os.environ` would call the isolated value "the real one").
REAL_ENV = {name: os.environ.get(name) for name in ('HOME', 'LOCALAPPDATA')}


def require_isolated_state(case, env, real_env=None):
    """A server of a test writes its state file to the data directory of the user: Linux honours `XDG_DATA_HOME`, macOS follows `HOME` (`~/Library`) and
    Windows `LOCALAPPDATA`. An environment that leaves the real one of THIS system in place would write into the real directory (and be seen by the real
    pages of the user), so the test fails before the start (`home_env` in tests/layers_helpers.py moves all of them). The real value is the one of
    `REAL_ENV` (taken at import), never the live `os.environ`, which a test may already have moved."""
    real = {'darwin': 'HOME', 'win32': 'LOCALAPPDATA'}.get(sys.platform)
    original = (REAL_ENV if real_env is None else real_env).get(real) if real else None
    if real and (not env.get(real) or env.get(real) == original):
        case.fail(f'the environment of the server does not move {real}: it would write the state file into the real data directory (use home_env)')


def start_serve(case, cli, root, layer, env, free_port, *extra, prepare=None, ready=None, deadline_s=20):
    """Start `dashboard serve <root> --port <p> *extra *layer` and wait until it is ready; returns `(proc, port)` or fails the test.

    `prepare(port)` runs before every try; `ready(port)` is an extra condition. A try that fails with BLOCKED (lost bind) is repeated on another
    port, any other failure ends the start."""
    require_isolated_state(case, env)
    last, port = "", None
    for _attempt in range(ATTEMPTS):
        port = free_port()
        if prepare:
            prepare(port)
        before = state_snapshot(env)
        started = datetime.now(timezone.utc)
        proc = subprocess.Popen([sys.executable, str(cli), "dashboard", "serve", str(root), "--port", str(port), *extra, *layer],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env={**env, "PYTHONUTF8": "1"})   # the server writes UTF-8; the harness decodes UTF-8 (a Windows console code page would turn "nasłuch" into mojibake)
        case.addCleanup(lambda proc=proc: release(proc))
        deadline = time.time() + deadline_s
        while time.time() < deadline and proc.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).close()
                answered = own_state(env, proc, port, root, started, before=before) and (ready is None or ready(port))
            except OSError:
                answered = False
            if answered:
                return proc, port
            time.sleep(0.1)
        kill_tree(proc)
        out, err = proc.communicate(timeout=10)
        last = f"{out}\n{err}"   # a lost bind is reported as BLOCKED on stdout, stderr stays empty
        if not lost_bind(last):
            break
    case.fail(f"dashboard serve did not start (last port {port}): {last.strip()[-600:]}")


def two_free_ports(free_port):
    """Two DIFFERENT free ports of the test slice (`free_port` alone gives the same one twice: nothing listens on it yet)."""
    import socket
    first = free_port()
    with socket.socket() as hold:
        if os.name != "nt":
            hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # the port may be in TIME_WAIT (free_port allows it)
        hold.bind(("127.0.0.1", first))
        hold.listen(1)
        second = free_port()
    return first, second


def start_serve_ports(case, cli, root, layer, env, free_port, *extra, prepare=None, ready=None, deadline_s=20):
    """The two-port counterpart of `start_serve` (the DEFAULT mode of 1.8.0 on ports of the test slice, never 5140/5141): starts
    `dashboard serve <root> --read-port <r> --approve-port <a> *extra *layer`, waits until both sockets answer `/healthz` and both state files
    are ours; returns `(proc, read_port, approve_port)` or fails the test. Pass `--read-bind`/`--approve-bind` in `extra` to narrow; the sockets
    listen on the default addresses otherwise (read: every interface, consents: loopback) and a test must not keep the server longer than it needs.
    `prepare(read_port, approve_port)` runs before every try; `ready(read_port, approve_port)` is an extra condition. A lost bind (BLOCKED)
    is repeated on other ports."""
    require_isolated_state(case, env)
    last, ports_ = "", None
    for _attempt in range(ATTEMPTS):
        ports_ = two_free_ports(free_port)
        read_port, approve_port = ports_
        if prepare:
            prepare(read_port, approve_port)
        before = state_snapshot(env)
        started = datetime.now(timezone.utc)
        proc = subprocess.Popen([sys.executable, str(cli), "dashboard", "serve", str(root), "--read-port", str(read_port),
                                 "--approve-port", str(approve_port), *extra, *layer],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env={**env, "PYTHONUTF8": "1"})   # the server writes UTF-8; the harness decodes UTF-8 (a Windows console code page would turn "nasłuch" into mojibake)
        case.addCleanup(lambda proc=proc: release(proc))
        deadline = time.time() + deadline_s
        while time.time() < deadline and proc.poll() is None:
            try:
                for port in ports_:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).close()
                answered = (all(own_state(env, proc, port, root, started, before=before) for port in ports_)
                            and (ready is None or ready(read_port, approve_port)))
            except OSError:
                answered = False
            if answered:
                return proc, read_port, approve_port
            time.sleep(0.1)
        kill_tree(proc)
        out, err = proc.communicate(timeout=10)
        last = f"{out}\n{err}"
        if not lost_bind(last):
            break
    case.fail(f"dashboard serve (two ports) did not start (last ports {ports_}): {last.strip()[-600:]}")
