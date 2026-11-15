"""Start of `dashboard serve` in a subprocess for tests, robust against the race of `free_port()` (a port that WAS free can be taken before the bind).

A server that lost the bind exits with BLOCKED, while the OTHER server on that port still answers `/healthz`, so an answer proves nothing. The
start is ready only when the state file of our own pid and port exists (written after the bind, also with `--no-approvals`); a lost bind is retried
on another port. Tests only: no production code is involved."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from unittest import mock

ATTEMPTS = 4


def state_files(env):
    """State files of the servers started with `env` (the directory `dashboard_token.state_dir()` resolves to under that environment)."""
    from skillsync_workflow import dashboard_token
    with mock.patch.dict(os.environ, env):
        directory = dashboard_token.state_dir()
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def own_state(env, proc, port):
    """Is there a state file of THIS process? Only the server that holds the port writes one with its pid."""
    for path in state_files(env):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("pid") == proc.pid and record.get("port") == port:
            return True
    return False


def start_serve(case, cli, root, layer, env, free_port, *extra, prepare=None, ready=None, deadline_s=20):
    """Start `dashboard serve <root> --port <p> *extra *layer` and wait until it is ready; returns `(proc, port)` or fails the test.

    `prepare(port)` runs before every try; `ready(port)` is an extra condition. A try that fails with BLOCKED (lost bind) is repeated on another
    port, any other failure ends the start."""
    last, port = "", None
    for _attempt in range(ATTEMPTS):
        port = free_port()
        if prepare:
            prepare(port)
        proc = subprocess.Popen([sys.executable, str(cli), "dashboard", "serve", str(root), "--port", str(port), *extra, *layer],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        case.addCleanup(lambda proc=proc: proc.poll() is None and proc.kill())
        deadline = time.time() + deadline_s
        while time.time() < deadline and proc.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).close()
                answered = own_state(env, proc, port) and (ready is None or ready(port))
            except OSError:
                answered = False
            if answered:
                return proc, port
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
        out, err = proc.communicate(timeout=10)
        last = f"{out}\n{err}"   # a lost bind is reported as BLOCKED on stdout, stderr stays empty
        if "BLOCKED" not in last:
            break
    case.fail(f"dashboard serve did not start (last port {port}): {last.strip()[-600:]}")
