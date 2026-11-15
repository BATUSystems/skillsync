"""Token of the consents of the status page (`dashboard serve`) and the file that lets `dashboard url` find it.

`POST /api/approve` needs the header `X-Approve-Token` (checked last by `dashboard_http`). The token is made per server process and
never goes into `/api/status`, an asset, a log or an error report. It reaches the user in the address `http://127.0.0.1:<port>/#zgoda=<token>`:
a fragment is never sent to the server or to a proxy in between, so a client that only forges the `Host` header does not get it.

The address comes from the console line of `dashboard serve` or from `skillsync dashboard url <project>`, which reads the state file
written here: `<data home>/dashboard/<sha256 of the resolved project root, 16 hex>-<port>.json` (0600 in a 0700 directory on POSIX; on
Windows `winacl.restrict_to_owner` when that helper exists), outside the project tree (never committed or synchronised). The file is
written atomically (a temporary file next to it, then `os.replace`: the ACL of the temporary file is what the target gets on Windows),
removed when the server stops, and a leftover of a killed process is ignored: the recorded server must answer `/healthz` of the same project.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import layers

SCHEMA = 1
TOKEN_BYTES = 24
FRAGMENT = "zgoda"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def state_dir() -> Path:
    return layers.data_home() / "dashboard"


def _resolved(root) -> Path:
    return Path(root).resolve()


def state_path(root, port: int) -> Path:
    digest = hashlib.sha256(str(_resolved(root)).encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return state_dir() / f"{digest}-{int(port)}.json"


def _restrict(path: Path):
    """Owner-only access: POSIX mode here, Windows through the shared ACL helper (`winacl.restrict_to_owner`) when it exists.
    Returns False when the restriction could not be made (the caller warns once); never raises."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        except OSError:
            return False
        return True
    try:
        from . import winacl  # type: ignore[attr-defined]
    except ImportError:
        return False
    try:
        return bool(winacl.restrict_to_owner(path))
    except Exception:  # noqa: BLE001 - an ACL helper must never stop the server
        return False


def write_state(root, port: int, token: str, host: str = "127.0.0.1", warn=None):
    """Write the state file of the server; returns its path, or None when it could not be written (the page still works)."""
    path = state_path(root, port)
    try:
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        restricted = _restrict(directory)
        record = {"schema": SCHEMA, "pid": os.getpid(), "port": int(port), "host": host, "root": str(_resolved(root)),
                  "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "token": token}
        handle, temporary = tempfile.mkstemp(dir=str(directory), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.write("\n")
            restricted = _restrict(Path(temporary)) and restricted   # BEFORE the replace: Windows moves the ACL of the temporary file
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        if not restricted and warn:
            warn("plik tokenu zgód nie ma ograniczonego dostępu do właściciela (ACL/uprawnienia): ustaw je ręcznie albo użyj `--no-approvals`")
        return path
    except OSError as error:
        if warn:
            warn(f"nie zapisano pliku tokenu zgód ({error.strerror or error}); adres z tokenem jest tylko w tej linii konsoli")
        return None


def remove_state(root, port: int) -> None:
    try:
        state_path(root, port).unlink()
    except OSError:
        pass


def _pid_alive(pid) -> bool:
    from .prototype_server import link_pid_alive
    return link_pid_alive(pid)


def _healthy(host: str, port: int, timeout: float = 1.0) -> bool:
    """Does a status page answer `/healthz` on this port? (A leftover file of a killed server must not be offered.)"""
    target = "[::1]" if host == "::1" else "127.0.0.1"
    try:
        with urllib.request.urlopen(f"http://{target}:{port}/healthz", timeout=timeout) as response:
            return response.status == 200 and bool(json.load(response).get("ok"))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def servers(root, port: int | None = None, healthy=None) -> list[dict]:
    """The running servers of the project that have a state file: `[{port, pid, host, token, startedAt}]`, by port."""
    healthy = healthy or _healthy
    root_text = str(_resolved(root))
    prefix = hashlib.sha256(root_text.encode("utf-8", "surrogatepass")).hexdigest()[:16] + "-"
    found = []
    try:
        names = sorted(name for name in os.listdir(state_dir()) if name.startswith(prefix) and name.endswith(".json"))
    except OSError:
        return []
    for name in names:
        try:
            record = json.loads((state_dir() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("root") != root_text
                or not isinstance(record.get("port"), int) or not isinstance(record.get("token"), str) or not record["token"]):
            continue
        if port is not None and record["port"] != port:
            continue
        if os.name != "nt" and not _pid_alive(record.get("pid")):   # Windows: `os.kill(pid, 0)` would terminate the process; `/healthz` decides there
            continue
        if not healthy(record.get("host") or "127.0.0.1", record["port"]):
            continue
        found.append({key: record.get(key) for key in ("port", "pid", "host", "token", "startedAt")})
    return found


def address(port: int, token: str, host: str = "127.0.0.1") -> str:
    """The address with the token in the fragment (`host` = a name allowed by `--approve-host`, else loopback)."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/#{FRAGMENT}={token}"


def url_command(args) -> int:
    """`skillsync dashboard url <project> [--port N] [--host NAME] [--json]`: the address with the token of a running server."""
    from .common import emit, load_layer_project

    def blocked(code: str, reason: str) -> int:
        if args.json:
            return emit({"schema": 1, "status": "BLOCKED", "code": code, "reason": reason})
        print(f"{code}: {reason}", file=sys.stderr)
        return 2

    try:
        root, _manifest, _layerset = load_layer_project(args.path, getattr(args, "layers", None))
    except Exception as error:  # noqa: BLE001 - any broken project is BLOCKED, as for the other `dashboard` commands
        return blocked("invalid_project", " ".join(str(error).split())[:300])
    running = servers(root, getattr(args, "port", None))
    if not running:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu z zapisanym tokenem (albo serwer z --no-approvals)")
    if len(running) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in running) + ")")
    chosen = running[0]
    url = address(chosen["port"], chosen["token"], getattr(args, "host", None) or "127.0.0.1")
    if args.json:
        return emit({"schema": 1, "status": "PASS", "url": url, "port": chosen["port"], "pid": chosen["pid"]})
    print(url)
    return 0
