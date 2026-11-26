"""Helpers of the end-to-end tests of CORE-2000-UNIFIED (`test_skillsync_unified_*.py`).

Everything here talks to REAL processes: the server is `skillsync dashboard serve <temporary project> ...` in a subprocess, accounts are made by the
real CLI `skillsync prototype user add` (the password of an approver is typed on a pseudoterminal: POSIX only), and requests are plain HTTP over a
socket. Nothing of the production code is patched, with ONE exception that is stated where it is used: the default (one socket, `page` role) mode has no
port flag and listens on 5140, which a test must never take, so `PAGE_LAUNCHER` moves that one constant (`dashboard_ports.DEFAULT_READ_PORT`) to a test
port before it runs the unchanged command line.

State is isolated (`layers_helpers.isolated_paths`: HOME, XDG_*, LOCALAPPDATA, SKILLSYNC_CONFIG under a temporary directory). Ports come from
`SKILLSYNC_TEST_DASHBOARD_PORTS` when set, else 5151-5169. Processes are ended by the PID of the test's own `Popen`. Secrets are put together at run time.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _entry in (str(ROOT), str(HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from layers_helpers import GIT_IDENTITY_VARS, guard_real_state, isolated_paths  # noqa: E402
from serve_start import ATTEMPTS, kill_tree, lost_bind, own_state, release, require_isolated_state, state_files, state_snapshot  # noqa: E402
from tmp_paths import descend  # noqa: E402

from skillsync_workflow.status import git_blob_sha  # noqa: E402

CLI = ROOT / "skillsync"
LAYER = ["--layer", f"core={ROOT / 'library'}"]
FIX = HERE / "fixtures" / "dashboard_server"
PORTS_ENV = "SKILLSYNC_TEST_DASHBOARD_PORTS"
DEFAULT_PORTS = "5151-5169"
FORBIDDEN_PORTS = (5140, 5141, 5142)
POSIX_ONLY = ("CLI czyta hasło konta z rolą approver wyłącznie z terminala (getpass), a test podaje je przez pseudoterminal POSIX; "
              "na Windows tego scenariusza nie da się wykonać prawdziwym CLI")

# passwords of the test accounts, put together at run time (secret scanners)
APPROVER_PASSWORD = "-".join(("Zatwierdzam", "Etap", "Scalony", "2026"))
APPROVER_NEW_PASSWORD = "-".join(("Zupelnie", "Inne", "Haslo", "Zatwierdzajace", "77"))
COMMENTER_PASSWORD = "-".join(("Komentuje", "Tylko", "4455"))
APPROVER, COMMENTER, ACCEPTOR = "ap.person@example.com", "com.person@example.com", "acc.person@example.com"
APPROVER_ACCOUNT = "pseudonim-kanarek-ap"

PLAN = "docs/plans/2026-01-10-demo-etap-1.md"
SPEC = "docs/specs/demo-etap-1.md"
RUN_ID = "2026-01-10-demo-etap-1"
SPEC2 = "docs/specs/demo-etap-2.md"
PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"
RUN2_ID = "2026-01-20-demo-etap-2"
DONE_PLAN = "docs/plans/2025-12-01-starszy.md"
BRANCH = "feature/demo"
HEAD = "abc1234def5678"
HEAD_NEXT = "fff0000aaa1111"

PAGE_LAUNCHER = '''import sys
sys.path.insert(0, {root!r})
from skillsync_workflow import dashboard_ports
dashboard_ports.DEFAULT_READ_PORT = int(sys.argv.pop(1))
from skillsync_workflow.cli import run
run()
'''


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def port_pool() -> range:
    raw = os.environ.get(PORTS_ENV) or DEFAULT_PORTS
    low, _, high = raw.partition("-")
    first, last = int(low), int(high or low)
    if not 5100 <= first <= last <= 5199:
        raise ValueError(f"{PORTS_ENV}={raw!r}: expected <from>-<to> within 5100-5199")
    return range(first, last + 1)


def free_port(address: str = "127.0.0.1", skip=()) -> int:
    for port in port_pool():
        if port in FORBIDDEN_PORTS or port in skip:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name != "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((address, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in the test pool {port_pool().start}-{port_pool().stop - 1}")


def lan_address() -> str | None:
    """A non-loopback IPv4 address of this machine (route probe, nothing is sent), None without one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found = probe.getsockname()[0]
        if found.startswith("127.") or found == "0.0.0.0":
            return None
        with socket.socket() as bindable:
            bindable.bind((found, 0))
        return found
    except OSError:
        return None


class Reply:
    def __init__(self, status: int, headers: list, raw: bytes) -> None:
        self.status, self.headers, self.raw = status, headers, raw
        try:
            self.data = json.loads(raw) if raw else {}
        except ValueError:
            self.data = {}

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key.lower() == name.lower()), None)

    @property
    def set_cookie(self) -> str:
        return self.header("Set-Cookie") or ""


def http_request(address: str, port: int, method: str, path: str, headers: dict | None = None, body=None) -> Reply:
    """One request; `Host` defaults to `address:port`. A header with the value None is left out."""
    conn = http.client.HTTPConnection(address, port, timeout=30)
    send = {"Host": f"{address}:{port}"}
    send.update(headers or {})
    send = {key: value for key, value in send.items() if value is not None}
    data = json.dumps(body).encode("utf-8") if isinstance(body, (dict, list)) else body
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for key, value in send.items():
            conn.putheader(key, value)
        if data is not None or method in ("POST", "PUT", "PATCH", "DELETE"):
            conn.putheader("Content-Length", str(len(data or b"")))
        conn.endheaders(data)
        response = conn.getresponse()
        raw = b"" if path.startswith("/events") else response.read()
        return Reply(response.status, response.getheaders(), raw)
    finally:
        conn.close()


class Browser:
    """A client of one origin of the server: the cookie jar of one session and its CSRF token."""

    def __init__(self, port: int, address: str = "127.0.0.1", host: str | None = None, scheme: str = "http", extra: dict | None = None) -> None:
        self.port, self.address, self.scheme = port, address, scheme
        self.host = host or f"{address}:{port}"
        self.extra = dict(extra or {})   # headers of a proxy in front of the server
        self.cookie: str | None = None
        self.csrf: str | None = None

    @property
    def origin(self) -> str:
        return f"{self.scheme}://{self.host}"

    @property
    def token(self) -> str:
        return (self.cookie or "").partition("=")[2]

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:8]

    def base_headers(self) -> dict:
        return {"Host": self.host, **self.extra, **({"Cookie": self.cookie} if self.cookie else {})}

    def get(self, path: str, headers: dict | None = None) -> Reply:
        return http_request(self.address, self.port, "GET", path, {**self.base_headers(), **(headers or {})})

    def post_headers(self, **override) -> dict:
        """The headers a browser of the page sends with a write; `override` uses `_` for `-`, None removes a header."""
        headers = {**self.base_headers(), "Content-Type": "application/json", "Origin": self.origin, "Sec-Fetch-Site": "same-origin",
                   "X-Status-Board": "1", "X-Skillsync-CSRF": self.csrf}
        headers.update({key.replace("_", "-"): value for key, value in override.items()})
        return headers

    def post(self, path: str, body=None, headers: dict | None = None) -> Reply:
        return http_request(self.address, self.port, "POST", path, headers if headers is not None else self.post_headers(),
                            {} if body is None else body)

    def login(self, login: str = APPROVER, password: str = APPROVER_PASSWORD) -> Reply:
        reply = self.post("/_api/v1/login", {"login": login, "password": password})
        if reply.status == 200:
            self.cookie = reply.set_cookie.split(";")[0]
            self.csrf = reply.data.get("csrf")
        return reply

    def change_password(self, current: str, new: str) -> Reply:
        reply = self.post("/_api/v1/password", {"current": current, "new": new})
        if reply.status == 204:
            self.cookie = reply.set_cookie.split(";")[0]
            self.csrf = self.get("/_api/v1/session").data.get("csrf")
        return reply

    def clone(self) -> "Browser":
        other = Browser(self.port, self.address, self.host, self.scheme, self.extra)
        other.cookie, other.csrf = self.cookie, self.csrf
        return other

    def status(self, plan: str | None = None) -> dict:
        reply = self.get("/api/status" + (f"?plan={plan}" if plan else ""))
        assert reply.status == 200, (reply.status, reply.text[:300])
        return reply.data

    def offer(self, kind: str, plan: str | None = None) -> dict:
        found = [item for item in self.status(plan)["approvals"]["actions"] if item["kind"] == kind]
        assert len(found) == 1, (kind, found)
        return found[0]


class Server:
    def __init__(self, proc, ports: tuple, address: str, out: Path, err: Path) -> None:
        self.proc, self.ports, self.address, self.out, self.err = proc, ports, address, out, err
        self.port = ports[-1]   # the socket with the session routes (the approve socket in the two-socket mode)

    def log(self) -> str:
        return self.out.read_text(encoding="utf-8", errors="replace") + self.err.read_text(encoding="utf-8", errors="replace")

    def browser(self, **kwargs) -> Browser:
        kwargs.setdefault("address", self.address)
        return Browser(self.port, **kwargs)

    def stop(self) -> None:
        if self.proc.poll() is None:
            if os.name == "nt":
                kill_tree(self.proc)
            else:
                self.proc.terminate()   # SIGTERM by the PID of our own process
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                kill_tree(self.proc)
                self.proc.wait(timeout=10)


class UnifiedCase(unittest.TestCase):
    """One temporary project and one isolated machine per test; accounts are made once per class by the real CLI and copied."""

    ACCOUNTS: tuple = ()          # (login, roles, password, account pseudonym or None)
    _templates: dict = {}

    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(tempfile.mkdtemp(prefix="unified-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        machine = self.tmp / "machine"
        machine.mkdir()
        self.env = {key: value for key, value in os.environ.items() if key not in GIT_IDENTITY_VARS}
        self.env.update(isolated_paths(machine))
        self.env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        self.project = self.tmp / "proj"
        self.build_project(self.project, "direct")
        self.servers = 0
        if self.ACCOUNTS:
            self.install_accounts(self.project)

    # --- project

    def build_project(self, root: Path, mode: str) -> None:
        for rel, source in ((PLAN, "plan.md"), (SPEC, "spec.md"), (DONE_PLAN, "plan-done.md"), (f".skillsync/runs/{RUN_ID}/checkpoints.md", "checkpoints.md")):
            write(descend(root, rel), (FIX / source).read_text(encoding="utf-8"))
        self.set_delivery(root, mode)
        write(root / ".git" / "HEAD", f"ref: refs/heads/{BRANCH}\n")
        self.set_head(HEAD, root)
        write(root / ".gitignore", ".skillsync/\n")

    def set_delivery(self, root: Path, mode: str) -> None:
        write(root / ".github" / ".skillsync.yaml", f"skills: [dashboard]\ndelivery:\n  mode: {mode}\n  base: main\n")

    def set_head(self, sha: str, root: Path | None = None) -> None:
        write(descend(root or self.project, f".git/refs/heads/{BRANCH}"), sha + "\n")

    def session_waits_for(self, word: str) -> None:
        with descend(self.project, f".skillsync/runs/{RUN_ID}/checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- 11:20 zrobione: S9; w toku: —; czeka: {word}\n")

    def spec_waits_for_go_plan(self) -> None:
        """A second spec that waits for `GO plan`, newer than the plan in progress, with a run directory for the trail."""
        spec = write(descend(self.project, SPEC2), f"# Spec: Demo 2\n\nStatus: draft (czeka na GO plan)\nPlan: {PLAN2}\nPrzebieg: {RUN2_ID}\n")
        later = descend(self.project, PLAN).stat().st_mtime + 100
        os.utime(spec, (later, later))
        descend(self.project, f".skillsync/runs/{RUN2_ID}").mkdir(parents=True, exist_ok=True)

    def plan_waits_for_go_implement(self) -> None:
        plan = write(descend(self.project, PLAN2), f"# Plan: Demo 2\n\nStatus: draft (czeka na GO implement)\nSpec: {SPEC2}\nPrzebieg: {RUN2_ID}\n\n"
                                                   "## Progress\n\n- [ ] S1 Krok\n")
        later = descend(self.project, SPEC2).stat().st_mtime + 100
        os.utime(plan, (later, later))

    def rows(self, run: str = RUN_ID) -> list:
        path = descend(self.project, f".skillsync/runs/{run}/approvals.jsonl")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def snapshot(self) -> dict:
        """Bytes of every document and every run file of the project (the audit of sign-ins lives elsewhere and is not part of it)."""
        found = {}
        for sub in ("docs", ".skillsync/runs", ".skillsync/audit", ".git", ".github"):
            base = descend(self.project, sub)
            for path in sorted(base.rglob("*")) if base.is_dir() else []:
                if path.is_file() and not path.name.endswith(".lock"):
                    found[path.relative_to(self.project).as_posix()] = path.read_bytes()
        return found

    def audit(self) -> list:
        path = self.project / ".skillsync" / "prototype" / "run" / "audit.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def blob_sha(self, rel: str) -> str:
        return git_blob_sha(descend(self.project, rel).read_bytes())

    # --- the real CLI

    def cli(self, *args: str, timeout: float = 90) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=self.env, check=False, cwd=str(self.tmp), stdin=subprocess.DEVNULL)

    def cli_with_stdin(self, line: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *args], input=line + "\n", capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90, env=self.env, check=False, cwd=str(self.tmp))

    def cli_on_terminal(self, password: str, *args: str, timeout: float = 90) -> tuple:
        """Runs the CLI with a pseudoterminal as stdin and stderr and types `password` at each of the two prompts of `--password-prompt`.
        Returns `(returncode, stdout, what the terminal showed)`."""
        if os.name == "nt":
            self.skipTest(POSIX_ONLY)
        master, slave = os.openpty()
        try:
            proc = subprocess.Popen([sys.executable, str(CLI), *args], stdin=slave, stderr=slave, stdout=subprocess.PIPE, env=self.env,
                                    cwd=str(self.tmp), start_new_session=True)   # no controlling terminal: getpass reads our pseudoterminal
        finally:
            os.close(slave)
        shown, out, typed, asked_from = b"", b"", 0, 0
        deadline = time.monotonic() + timeout
        streams = [master, proc.stdout.fileno()]
        try:
            while streams and time.monotonic() < deadline:
                ready, _, _ = select.select(streams, [], [], 0.2)
                for fd in ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:   # EIO: the other side of the terminal is closed
                        chunk = b""
                    if not chunk:
                        streams.remove(fd)
                    elif fd == master:
                        shown += chunk
                    else:
                        out += chunk
                # a prompt ends with a colon; getpass flushes pending input before it prints it, so type only after it is on the screen
                if typed < 2 and shown[asked_from:].rstrip().endswith(b":"):
                    asked_from = len(shown)
                    os.write(master, password.encode("utf-8") + b"\n")
                    typed += 1
                if proc.poll() is not None and not ready:
                    break
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            proc.stdout.close()
            os.close(master)
        return proc.returncode, out.decode("utf-8", "replace"), shown.decode("utf-8", "replace")

    def user_add_approver(self, root: Path, login: str, password: str, roles: str = "approver", account: str | None = None) -> tuple:
        extra = ["--account", account] if account else []
        return self.cli_on_terminal(password, "prototype", "user", "add", str(root), login, "--roles", roles, "--password-prompt",
                                    "--confirm-mixed-fleet", *extra, *LAYER)

    def install_accounts(self, root: Path) -> None:
        key = (type(self).__module__, self.ACCOUNTS)
        target = root / ".skillsync" / "prototype" / "users.json"
        template = UnifiedCase._templates.get(key)
        if template is None:
            for login, roles, password, account in self.ACCOUNTS:
                if "approver" in roles:
                    code, out, shown = self.user_add_approver(root, login, password, roles, account)
                    self.assertEqual(code, 0, f"user add {login}: {out}\n{shown}")
                else:
                    done = self.cli_with_stdin(password, "prototype", "user", "add", str(root), login, "--roles", roles, "--password-stdin", *LAYER)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            UnifiedCase._templates[key] = target.read_bytes()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(template)
            target.chmod(0o600)

    # --- the real server

    def serve(self, *extra: str, mode: str = "page", address: str = "127.0.0.1", port: int | None = None) -> Server:
        """Starts `dashboard serve` of the temporary project. `mode`: `page` (the default mode of 2.0.0, see `PAGE_LAUNCHER`), `port`
        (`--port N`) or `two` (`--read-port R --approve-port A`; `Server.ports` = (R, A))."""
        require_isolated_state(self, self.env)
        last = ""
        for _attempt in range(ATTEMPTS):
            first = port or free_port(address)
            if mode == "page":
                launcher = write(self.tmp / "page_launcher.py", PAGE_LAUNCHER.format(root=str(ROOT)))
                ports = (first,)
                command = [sys.executable, str(launcher), str(first), "dashboard", "serve", str(self.project), "--read-bind", address]
            elif mode == "port":
                ports = (first,)
                command = [sys.executable, str(CLI), "dashboard", "serve", str(self.project), "--port", str(first), "--host", address]
            else:
                ports = (first, free_port(address, skip=(first,)))
                command = [sys.executable, str(CLI), "dashboard", "serve", str(self.project), "--read-port", str(ports[0]), "--approve-port",
                           str(ports[1]), "--read-bind", address, "--approve-bind", address]
            self.servers += 1
            out, err = descend(self.tmp, f"serve-{self.servers}.out"), descend(self.tmp, f"serve-{self.servers}.err")
            before, started = state_snapshot(self.env), datetime.now(timezone.utc)
            with out.open("wb") as out_handle, err.open("wb") as err_handle:
                proc = subprocess.Popen([*command, *extra, *LAYER], stdout=out_handle, stderr=err_handle, env=self.env, cwd=str(self.tmp))
            self.addCleanup(release, proc)
            server = Server(proc, ports, address, out, err)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    alive = all(http_request(address, p, "GET", "/healthz", {"Host": f"127.0.0.1:{p}"}).status in (200, 403) for p in ports)
                except OSError:
                    alive = False
                if alive and all(own_state(self.env, proc, p, self.project, started, before=before) for p in ports):
                    self.addCleanup(server.stop)
                    return server
                time.sleep(0.1)
            kill_tree(proc)
            proc.wait(timeout=10)
            last = server.log()
            if port is not None or not lost_bind(last):
                break
        self.fail(f"dashboard serve did not start: {last.strip()[-800:]}")

    def state_texts(self) -> list:
        return [path.read_text(encoding="utf-8") for path in state_files(self.env)]
