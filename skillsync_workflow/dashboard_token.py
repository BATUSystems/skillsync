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
import ipaddress
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import layers, pathfmt

SCHEMA = 1
TOKEN_BYTES = 24
FRAGMENT = "zgoda"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_id(token: str) -> str:
    """A short digest of the token for `/healthz` and the state file (never the token): 32 bits of a 192-bit hash tell nothing about it."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def bind_name(host: str) -> str:
    """The name of the address a server really listens on, for an address with the token: a wildcard bind answers on loopback."""
    if host in ("", "0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def address_host(host: str, approve_hosts=()) -> str:
    """The name for the address with the token: the page accepts consents only under loopback and the names of `--approve-host`, so a
    server bound to another IP is offered on loopback (a name the page refuses would give a dead address)."""
    name = bind_name(host)
    allowed = {item.lower() for item in LOOPBACK} | {str(item).lower() for item in approve_hosts}
    return name if name.lower() in allowed else "127.0.0.1"


def state_dir() -> Path:
    return layers.data_home() / "dashboard"


def _resolved(root) -> Path:
    return Path(root).resolve()


def root_text(root) -> str:
    """The resolved project root in the one form of the report paths (`/` on Windows, `pathfmt.posix`): the `root` field of the state
    file, the comparison in `servers` and the input of the digest all use THIS text, so the server and `dashboard url` name the same file
    (`C:\\x` and `C:/x` would be two files) and the field reads like the other path fields of the reports."""
    return pathfmt.posix(str(_resolved(root)))


def _digest(root) -> str:
    """16 hex of the SHA-256 of `root_text(root)`: the name of its state files."""
    return hashlib.sha256(root_text(root).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def state_path(root, port: int) -> Path:
    return state_dir() / f"{_digest(root)}-{int(port)}.json"


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
        try:
            return bool(winacl.restrict_to_owner(path, emit=False))   # we warn once ourselves (`write_state`)
        except TypeError:   # a helper without the `emit` parameter
            return bool(winacl.restrict_to_owner(path))
    except Exception:  # noqa: BLE001 - an ACL helper must never stop the server
        return False


def _own_directory(directory: Path) -> bool:
    """The state directory is a real directory of this user (POSIX): a link or a stranger's directory would take the token elsewhere."""
    if directory.is_symlink():
        return False
    if os.name != "nt":
        try:
            return directory.stat().st_uid == os.geteuid()
        except OSError:
            return False
    return True


def _sweep_temporaries(directory: Path, root, port: int) -> None:
    """Remove `.tmp-*` files of THIS project and port left by a killed server (they hold a token)."""
    prefix = f".tmp-{_digest(root)}-{int(port)}-"
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix) and name.endswith(".json"):
                try:
                    (directory / name).unlink()
                except OSError:
                    pass
    except OSError:
        pass


def write_state(root, port: int, token, host: str = "127.0.0.1", warn=None, hosts=(), role=None, peer=None, bind=None):
    """Write the state file of the server; returns its path, or None when it could not be written (the page still works).

    `token=None` (`--no-approvals`): the file says `approvals: false` and holds no token, so `dashboard url` can tell a server without
    consents from no server. `role` / `peer` (two-port mode: `read` or `approve`, and the port of the other socket) are added only when
    given, so a file of the single-port mode is the file of 1.5.0 (no `role` = `both`)."""
    path = state_path(root, port)
    try:
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if not _own_directory(directory):
            if warn:
                warn("katalog pliku tokenu zgód jest dowiązaniem albo nie należy do użytkownika: pliku nie zapisano; adres z tokenem jest tylko w tej linii konsoli"
                     if token is not None else "katalog pliku stanu jest dowiązaniem albo nie należy do użytkownika: pliku nie zapisano (`dashboard url` nie rozpozna tego serwera)")
            return None
        restricted = _restrict(directory)
        _sweep_temporaries(directory, root, port)
        record = {"schema": SCHEMA, "pid": os.getpid(), "port": int(port), "host": host, "root": root_text(root),
                  "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "approvals": token is not None,
                  "hosts": [str(name) for name in hosts]}
        if role is not None:
            record["role"] = role
        if peer is not None:
            record["peer"] = int(peer)
        if bind is not None:   # the address the socket really listens on (`host` is where this machine reaches it): `dashboard url --lan`
            record["bind"] = str(bind)
        if token is not None:
            record["token"] = token
            record["tokenId"] = token_id(token)
        handle, temporary = tempfile.mkstemp(dir=str(directory), prefix=f".tmp-{_digest(root)}-{int(port)}-", suffix=".json")
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
        if not restricted and warn and token is not None:
            warn("plik tokenu zgód nie ma ograniczonego dostępu do właściciela (ACL/uprawnienia): ustaw je ręcznie albo użyj `--no-approvals`")
        return path
    except OSError as error:
        if warn:
            warn(f"nie zapisano pliku tokenu zgód ({error.strerror or error}); adres z tokenem jest tylko w tej linii konsoli")
        return None


def remove_state(root, port: int, pid: int | None = None) -> None:
    """Remove the state file of the port. With `pid` (the server that stops) only when the file is its own: a server that lost the port to
    another one, or an old one that stops late, must not delete the file of the server that holds the port now."""
    path = state_path(root, port)
    if pid is not None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(record, dict) or record.get("pid") != pid:
            return
    try:
        path.unlink()
    except OSError:
        pass


def _pid_alive(pid) -> bool:
    from .prototype_server import link_pid_alive
    return link_pid_alive(pid)


def _healthy(host: str, port: int, token_id_expected: str | None = None, timeout: float = 1.0) -> bool:
    """Does the status page of THIS server answer `/healthz` on this address? A leftover file of a killed server must not be offered:
    a stranger on the port is told apart by `tokenId` (a digest, never the token); the token itself is never sent. `Host: localhost` is
    what the page accepts whatever address it is reached on."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = ipaddress.ip_address("127.0.0.1")   # a name from the file is never connected to
    target = f"[{ip}]" if ip.version == 6 else str(ip)
    request = urllib.request.Request(f"http://{target}:{port}/healthz", headers={"Host": "localhost"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
            return response.status == 200 and bool(data.get("ok")) and (token_id_expected is None or data.get("tokenId") == token_id_expected)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def port_owner(port: int) -> str | None:
    """Who of OUR servers holds `port` (from the state files, only live processes): `projekt <name> (odczyt|zgody|jeden port)`, else None.
    Only the directory name of the project and the role; no pid, no command, no token."""
    try:
        names = sorted(name for name in os.listdir(state_dir()) if name.endswith(f"-{int(port)}.json") and not name.startswith("."))
    except OSError:
        return None
    for name in names:
        try:
            record = json.loads((state_dir() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("port") != int(port) or not _pid_alive(record.get("pid")):
            continue
        project = pathfmt.posix(str(record.get("root", ""))).rstrip("/").rsplit("/", 1)[-1] or "?"
        role = {"read": "odczyt", "approve": "zgody"}.get(record.get("role"), "jeden port")
        return f"projekt {project} ({role})"
    return None


def servers(root, port: int | None = None, healthy=None) -> list[dict]:
    """The running servers of the project that have a state file: `[{port, pid, host, token, startedAt, hosts, approvals}]`, by port
    (`approvals` False: a `--no-approvals` server, no token)."""
    healthy = healthy or _healthy
    expected_root = root_text(root)
    prefix = _digest(root) + "-"
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
        if (not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("root") != expected_root
                or not isinstance(record.get("port"), int)):
            continue
        has_token = isinstance(record.get("token"), str) and bool(record["token"])
        if not has_token and record.get("approvals") is not False:
            continue
        if port is not None and record["port"] != port:
            continue
        if os.name != "nt" and not _pid_alive(record.get("pid")):   # Windows: `os.kill(pid, 0)` would terminate the process; `/healthz` decides there
            try:
                (state_dir() / name).unlink()   # the leftover of a killed server
            except OSError:
                pass
            continue
        if not healthy(record.get("host") or "127.0.0.1", record["port"], token_id(record["token"]) if has_token else None):
            continue
        found.append({**{key: record.get(key) for key in ("port", "pid", "host", "token", "startedAt", "hosts", "peer", "bind")}, "approvals": has_token,
                      "role": record.get("role") if record.get("role") in ("read", "approve") else "both"})
    return found


def address(port: int, token: str, host: str = "127.0.0.1") -> str:
    """The address with the token in the fragment (`host` = a name allowed by `--approve-host`, else loopback)."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/#{FRAGMENT}={token}"


def read_address(port: int, host: str = "127.0.0.1") -> str:
    """The address of the read socket: no fragment, no token (safe to hand to a person from outside)."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/"


def _read_address(args, found: list[dict], blocked) -> int:
    """`dashboard url --read`: the address of the read socket of a running two-port server (or of a single-port server, which reads too)."""
    from .common import emit
    readers = [item for item in found if item.get("role") in ("read", "both")]
    if not readers:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu z gniazdem odczytu (z zapisanym plikiem stanu)")
    if len(readers) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in readers) + ")")
    chosen = readers[0]
    name = getattr(args, "host", None) or None
    if name is not None:
        allowed = set(LOOPBACK) | {chosen.get("host")} | set(chosen.get("hosts") or [])
        if name.lower() not in {str(item).lower() for item in allowed if item}:
            return blocked("host_not_allowed", "--host: tylko loopback albo nazwa z --approve-host tego serwera")
    url = read_address(chosen["port"], name or address_host(chosen.get("host") or "127.0.0.1", chosen.get("hosts") or ()))
    if args.json:
        return emit({"schema": 1, "status": "PASS", "url": url, "port": chosen["port"], "pid": chosen["pid"], "role": chosen.get("role") or "both"})
    print(url)
    return 0


def _lan_addresses(args, found: list[dict], blocked) -> int:
    """`dashboard url --lan`: the addresses of the read socket for OTHER devices (one per line; `lanUrls` in JSON). BLOCKED
    `read_not_exposed` when the read socket listens on loopback only."""
    from .common import emit
    readers = [item for item in found if item.get("role") in ("read", "both")]
    if not readers:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu z gniazdem odczytu (z zapisanym plikiem stanu)")
    if len(readers) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in readers) + ")")
    chosen = readers[0]
    bind = chosen.get("bind")
    if chosen.get("role") != "read" or not bind or bind in LOOPBACK or str(bind).startswith("127."):
        return blocked("read_not_exposed", "gniazdo odczytu tego serwera słucha tylko na tej maszynie; uruchom `dashboard serve` bez "
                                          "--read-bind 127.0.0.1 / dashboard.local_only albo z --read-bind 0.0.0.0")
    from .dashboard_http import lan_addresses
    addresses = lan_addresses() if bind in ("0.0.0.0", "::", "") else [bind]
    if not addresses:
        return blocked("read_not_exposed", "serwer słucha w sieci, ale ta maszyna nie ma adresu poza loopbackiem")
    urls = [read_address(chosen["port"], ip) for ip in addresses]
    if args.json:
        return emit({"schema": 1, "status": "PASS", "port": chosen["port"], "pid": chosen["pid"], "role": "read", "lanUrls": urls})
    print("\n".join(urls))
    return 0


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
    found = servers(root, getattr(args, "port", None))
    if getattr(args, "lan", False):
        return _lan_addresses(args, found, blocked)
    if getattr(args, "read", False):
        return _read_address(args, found, blocked)
    running = [item for item in found if item.get("approvals")]    # servers with consents: only they have an address with a token
    if not running and found:
        return blocked("approvals_disabled", "serwer tego projektu działa z --no-approvals (port " + ", ".join(str(item["port"]) for item in found)
                       + "): nie ma zgód ani tokenu")
    if not running:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu (z zapisanym plikiem stanu)")
    if len(running) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in running) + ")")
    chosen = running[0]
    name = getattr(args, "host", None) or None
    if name is not None:
        # The name only makes text, but a browser sends the fragment to that site's script: only the loopback and the names the server was
        # started with (`--approve-host`, kept in its state file) are accepted.
        allowed = set(LOOPBACK) | {chosen.get("host")} | set(chosen.get("hosts") or [])
        if name.lower() not in {str(item).lower() for item in allowed if item}:
            return blocked("host_not_allowed", "--host: tylko loopback albo nazwa z --approve-host tego serwera (" + ", ".join(sorted(chosen.get("hosts") or [])) + ")")
    url = address(chosen["port"], chosen["token"], name or address_host(chosen.get("host") or "127.0.0.1", chosen.get("hosts") or ()))
    if args.json:
        answer = {"schema": 1, "status": "PASS", "url": url, "port": chosen["port"], "pid": chosen["pid"]}
        if chosen.get("role") == "approve":   # two-port mode: the address of the read socket (no token) and the role of this one
            answer["role"] = "approve"
            if chosen.get("peer"):
                answer["readUrl"] = read_address(chosen["peer"], name or address_host(chosen.get("host") or "127.0.0.1", chosen.get("hosts") or ()))
        return emit(answer)
    print(url)
    return 0
