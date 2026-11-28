"""The state file of the status page (`dashboard serve`) that lets `dashboard url` find the server, and the pin files of the worktree tabs.

There is no consent token (CORE-2000-UNIFIED): a consent is given by a session with the role `approver` on the page (`dashboard_http`), so the
address is a plain `http://<host>:<port>/` (a tab: `/?tab=<id>`) and the state file holds no token, no `tokenId` and no per-tab token. Files of
servers of earlier versions (they have `token`) are still read to find and stop them; they are never written again.

The address comes from the console line of `dashboard serve` or from `skillsync dashboard url <project>`, which reads the state file
written here: `<data home>/dashboard/<sha256 of the resolved project root, 16 hex>-<port>.json` (0600 in a 0700 directory on POSIX; on
Windows `winacl.restrict_to_owner` when that helper exists), outside the project tree (never committed or synchronised). The file is
written atomically (a temporary file next to it, then `os.replace`: the ACL of the temporary file is what the target gets on Windows),
removed when the server stops, and a leftover of a killed process is ignored: the recorded server must answer `/healthz` of the same project.

Schema 2 (CORE-1180-MULTIWT) adds `kind` (`project`: one server for every worktree of a git project; `standalone`: one tab of its own),
`commonDir`, `serverId` (random per process, also the answer of `/healthz`), `tabs[]` (`{id, path}`) and `terminal`. The files of this
version are schema 2 without any token: `approvals: true` with no `token` is what a client of 1.18 skips (`dashboard url` of 1.18 then says
`server_not_running`, a readable BLOCKED, not a hang), so a new server never gets an address a client of 1.18 would take for its own. Schema 1 files
(older servers) are still read and count as standalone. A project server is found by `commonDir`
(`servers_for_project`), never by the name of the file. `dashboard serve` in another worktree of a running project server does not open
sockets: it writes a pin file `<data home>/dashboard/<serverId>-pins/<tab id>.json` (`{path, at[, plan]}`) that the server reads, validates
(R2/R3) and turns into a tab, then waits for the id in `tabs[]` (`append_command`).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import dashboard_worktrees, i18n, layers, pathfmt

SCHEMA = 1
SCHEMA_TABS = 2
LOOPBACK = ("127.0.0.1", "localhost", "::1")
KINDS = ("project", "standalone")
SERVER_ID_RE = re.compile(r"^[0-9a-f]{16}$")
PIN_NAME_RE = re.compile(r"^(main|[0-9a-f]{12})\.json$")
PINS_SUFFIX = "-pins"
MAX_PIN_BYTES = 4096
MAX_PIN_FILES = 64            # files looked at per refresh of the registry
MAX_PINS = dashboard_worktrees.MAX_VISIBLE
REGISTRATION_WAIT_S = 5.0     # how long `append_command` waits for the id in `tabs[]`
REGISTRATION_POLL_S = 0.1
RACE_RETRIES = 3              # a lost bind next to a server of this project: append again this many times ...
RACE_DELAY_S = 0.2            # ... this long apart (the state file appears after the bind)
TERMINAL_VARS = ("TMUX", "TERM_SESSION_ID")
SETTING_FLAGS = (("--port", "port"), ("--read-port", "read_port"), ("--approve-port", "approve_port"), ("--read-bind", "read_bind"),
                 ("--approve-bind", "approve_bind"), ("--read-host", "read_host"), ("--approve-host", "approve_host"),
                 ("--allow-proxy-headers", "allow_proxy_headers"), ("--allow-remote-approvals", "allow_remote_approvals"), ("--no-approvals", "no_approvals"), ("--layer", "layers"),
                 ("--allow-plain-login", "allow_plain_login"), ("--local-only", "local_only"), ("--trust-proxy", "trust_proxy"), ("--proxy-address", "proxy_address"),
                 ("--secure-cookies", "secure_cookies"), ("--public-url", "public_url"), ("--prototype-url", "prototype_url"),
                 ("--prototype-port", "prototype_port"), ("--page-port", "page_port"), ("--single-origin", "single_origin"), ("--host", "host"), ("--session-url", "session_url"), ("--poll", "poll"),
                 ("--verbose", "verbose"))
DEFAULT_POLL = 1.0
LEGACY_CONSENT_PORT = 5141    # the consent socket of the earlier versions: their servers may still run beside a server of this one
_state_lock = threading.Lock()


def token_id(token: str) -> str:
    """The short digest of the token that servers of schema 1 answered in `/healthz` (`tokenId`): only to recognise their state files; no server of this version has a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]


def bind_name(host: str) -> str:
    """The name of the address a server really listens on, for the address that is printed: a wildcard bind answers on loopback."""
    if host in ("", "0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def address_host(host: str, approve_hosts=()) -> str:
    """The name for the printed address: the page logs in and consents only under loopback (or through TLS), so a server bound to another IP
    is offered on loopback (a name the page refuses to sign in under would give a dead address)."""
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
    """Remove `.tmp-*` files of THIS project and port left by a killed server (they hold a token); `.json` is the suffix of versions up to 1.9.0."""
    prefix = f".tmp-{_digest(root)}-{int(port)}-"
    try:
        for name in os.listdir(directory):
            if name.startswith(prefix) and name.endswith((".tmp", ".json")):
                try:
                    (directory / name).unlink()
                except OSError:
                    pass
    except OSError:
        pass


def new_server_id() -> str:
    """The id of one server process (16 hex): the answer of `/healthz` that tells this server from a stranger on the port. Not a secret."""
    return secrets.token_hex(8)


def terminal_info(environ=None) -> dict:
    """`TMUX` / `TERM_SESSION_ID` of the server process, for the line that tells where the server runs (text only, short)."""
    environ = os.environ if environ is None else environ
    return {name: str(environ[name])[:200] for name in TERMINAL_VARS if environ.get(name)}


def _tabs_record(tabs) -> list[dict]:
    """The `tabs[]` of a state file: `{id, path[, current]}`, never a token."""
    out = []
    for tab in tabs or ():
        item = {"id": str(tab["id"]), "path": str(tab["path"])}
        if tab.get("current"):
            item["current"] = True   # the tab of the directory the server was started in: never evicted, counts as pinned
        out.append(item)
    return out


def _replace(source, target) -> None:
    """`os.replace` with the short retry of the project (`prototype_server.retry_transient`): on Windows a reader that has the target open refuses the replace
    with `PermissionError` for an instant."""
    from .prototype_server import retry_transient   # lazy: the module is large
    retry_transient(lambda: os.replace(source, target))


def _write_record(directory: Path, path: Path, record: dict, root, port: int) -> bool:
    """Write `record` atomically (a temporary file next to it, then `os.replace`); True when the owner-only restriction of the file held."""
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix=f".tmp-{_digest(root)}-{int(port)}-", suffix=".tmp")   # never `.json`: a reader that globs `*.json` (hidden files included) would meet an empty file
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.write("\n")
        restricted = _restrict(Path(temporary))   # BEFORE the replace: Windows moves the ACL of the temporary file
        _replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return restricted


def write_state(root, port: int, approvals, host: str = "127.0.0.1", warn=None, hosts=(), role=None, peer=None, bind=None, *,
                server_id=None, kind=None, common_dir=None, tabs=None, terminal=None, prototype_port=None):
    """Write the state file of the server; returns its path, or None when it could not be written (the page still works).

    `approvals` falsy (`--no-approvals`): the file says `approvals: false`, so `dashboard url` can tell a server without consents from no server.
    Whatever is given there, no token is ever written (a truthy value only says the consents are on). `role` / `peer` (`page`, or the two sockets
    `read` / `approve` and the port of the other one) are added only when given (no `role` = `both`). With `server_id` the file is schema 2: `kind`
    (`project` or `standalone`), `serverId`, `commonDir` (when the directory is in a git project), `tabs` (`[{id, path}]`) and `terminal`;
    without it, schema 1 (no token either)."""
    path = state_path(root, port)
    try:
        directory = path.parent
        directory.mkdir(parents=True, exist_ok=True)
        if not _own_directory(directory):
            if warn:
                warn("katalog pliku stanu jest dowiązaniem albo nie należy do użytkownika: pliku nie zapisano (`dashboard url` nie rozpozna tego serwera)")
            return None
        restricted = _restrict(directory)
        _sweep_temporaries(directory, root, port)
        record = {"schema": SCHEMA, "pid": os.getpid(), "port": int(port), "host": host, "root": root_text(root),
                  "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "approvals": bool(approvals),
                  "hosts": [str(name) for name in hosts]}
        if role is not None:
            record["role"] = role
        if peer is not None:
            record["peer"] = int(peer)
        if prototype_port is not None:   # the second socket of the server (the prototype origin), so a collision there can be named (`port_owner`)
            record["prototypePort"] = int(prototype_port)
        if bind is not None:   # the address the socket really listens on (`host` is where this machine reaches it): `dashboard url --lan`
            record["bind"] = str(bind)
        if server_id is not None:
            record["schema"] = SCHEMA_TABS
            record["kind"] = kind if kind in KINDS else "standalone"
            record["serverId"] = str(server_id)
            if common_dir:
                record["commonDir"] = str(common_dir)
            record["tabs"] = _tabs_record(tabs)
            if terminal:
                record["terminal"] = dict(terminal)
        with _state_lock:
            restricted = _write_record(directory, path, record, root, port) and restricted
        if not restricted and warn:
            warn("plik stanu serwera nie ma ograniczonego dostępu do właściciela (ACL/uprawnienia): ustaw je ręcznie")
        return path
    except OSError as error:
        if warn:
            warn(f"nie zapisano pliku stanu serwera ({error.strerror or error}); `dashboard url` nie rozpozna tego serwera")
        return None


def refresh_tabs(root, port: int, tabs, pid: int | None = None) -> bool:
    """Replace `tabs[]` of the own schema 2 state file atomically (the list of the tabs changed). With `pid` only a file of that process
    (a server that lost the port must not rewrite the file of the one that holds it now). False when nothing was written."""
    path = state_path(root, port)
    with _state_lock:
        try:
            directory = path.parent
            if not _own_directory(directory):
                return False
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("schema") != SCHEMA_TABS or (pid is not None and record.get("pid") != pid):
                return False
            record["tabs"] = _tabs_record(tabs)
            _write_record(directory, path, record, root, port)
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False


def remove_state(root, port: int, pid: int | None = None) -> None:
    """Remove the state file of the port. With `pid` (the server that stops) only when the file is its own: a server that lost the port to
    another one, or an old one that stops late, must not delete the file of the server that holds the port now."""
    path = state_path(root, port)
    with _state_lock:   # the same lock as `refresh_tabs`: a refresh that is running cannot put the file back after this
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


def _healthy(host: str, port: int, expected: str | None = None, timeout: float = 1.0) -> bool:
    """Does the status page of THIS server answer `/healthz` on this address? A leftover file of a killed server must not be offered:
    a stranger on the port is told apart by `serverId` (random per process, not a secret; `tokenId`, a digest of the token, is what servers
    of schema 1 answered). `Host: localhost` is what the page accepts whatever address it is reached on."""
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = ipaddress.ip_address("127.0.0.1")   # a name from the file is never connected to
    target = f"[{ip}]" if ip.version == 6 else str(ip)
    request = urllib.request.Request(f"http://{target}:{port}/healthz", headers={"Host": "localhost"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
            return (response.status == 200 and bool(data.get("ok"))
                    and (expected is None or expected in (data.get("serverId"), data.get("tokenId"))))
    except (OSError, ValueError, urllib.error.URLError, AttributeError):
        return False


def _windows() -> bool:
    return os.name == "nt"


def port_owner(port: int, healthy=None) -> str | None:
    """Who of OUR servers holds `port` (from the state files, only live processes): `projekt <name> (odczyt|zgody|strona|jeden port|prototypy)`, else None.
    Only the directory name of the project and the role; no pid, no command, no token."""
    try:
        names = sorted((name for name in os.listdir(state_dir()) if name.endswith(".json") and not name.startswith(".")),
                       key=lambda name: (not name.endswith(f"-{int(port)}.json"), name))   # a page on the port before a prototype origin on it
    except OSError:
        return None
    for name in names:
        try:
            record = json.loads((state_dir() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or int(port) not in (record.get("port"), record.get("prototypePort")) or not _pid_alive(record.get("pid")):
            continue
        page = record.get("port")
        if _windows() and not (isinstance(page, int) and (healthy or _healthy)(record.get("host") or "127.0.0.1", page, None)):
            continue   # Windows: a pid check says nothing about a killed server; only an answering page is named
        project = pathfmt.posix(str(record.get("root", ""))).rstrip("/").rsplit("/", 1)[-1] or "?"
        role = {"read": "odczyt", "approve": "zgody", "page": "strona"}.get(record.get("role"), "jeden port")
        return f"projekt {project} ({role if page == int(port) else 'prototypy'})"
    return None


def _collect(match, port, healthy) -> list[dict]:
    """The live servers whose state files `match(name, record)`, by file name: `[{port, pid, host, token, startedAt, hosts, peer, bind,
    approvals, role, schema, kind, serverId, commonDir, tabs, terminal, root, legacy}]`. A file of schema 1 has no `kind` (it counts as
    `standalone`). `legacy`: a server of an earlier version (its state file, or that of its other socket, holds a consent token)."""
    healthy = healthy or _healthy
    found = []
    try:
        names = sorted(name for name in os.listdir(state_dir()) if name.endswith(".json") and not name.startswith("."))
    except OSError:
        return []
    for name in names:
        try:
            record = json.loads((state_dir() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (not isinstance(record, dict) or record.get("schema") not in (SCHEMA, SCHEMA_TABS) or not isinstance(record.get("port"), int)
                or isinstance(record.get("port"), bool) or not match(name, record)):
            continue
        has_token = isinstance(record.get("token"), str) and bool(record["token"])   # a server of an earlier version; this version writes none
        if not has_token and not isinstance(record.get("approvals"), bool):   # a record of this version says `approvals`; one of an earlier version has a token (or `approvals: false`)
            continue
        if port is not None and record["port"] != port:
            continue
        if os.name != "nt" and not _pid_alive(record.get("pid")):   # Windows: `os.kill(pid, 0)` would terminate the process; `/healthz` decides there
            try:
                (state_dir() / name).unlink()   # the leftover of a killed server
            except OSError:
                pass
            continue
        if record["schema"] == SCHEMA_TABS:
            expected = record.get("serverId") if isinstance(record.get("serverId"), str) else None
        else:
            expected = token_id(record["token"]) if has_token else None
        if not healthy(record.get("host") or "127.0.0.1", record["port"], expected):
            continue
        tabs = [tab for tab in (record.get("tabs") or []) if isinstance(tab, dict) and isinstance(tab.get("id"), str) and isinstance(tab.get("path"), str)] \
            if record["schema"] == SCHEMA_TABS and isinstance(record.get("tabs"), list) else []
        found.append({**{key: record.get(key) for key in ("port", "pid", "host", "token", "startedAt", "hosts", "peer", "bind", "root")},
                      "approvals": has_token or record.get("approvals") is not False,
                      "role": record.get("role") if record.get("role") in ("read", "approve", "page") else "both",
                      "schema": record["schema"], "kind": record.get("kind") if record.get("kind") in KINDS else "standalone",
                      "serverId": record.get("serverId") if record["schema"] == SCHEMA_TABS else None,
                      "commonDir": record.get("commonDir") if isinstance(record.get("commonDir"), str) else None, "tabs": tabs,
                      "terminal": record.get("terminal") if isinstance(record.get("terminal"), dict) else {}, "legacy": has_token})
    old = {item["serverId"] for item in found if item["legacy"] and item["serverId"]}
    for item in found:   # the read socket of an earlier version holds no token: it is the same old server
        item["legacy"] = item["legacy"] or item["serverId"] in old
    return found


def servers(root, port: int | None = None, healthy=None) -> list[dict]:
    """The running servers whose state file names this project root (schema 1 and 2), by port (`approvals` False: a `--no-approvals`
    server, no token). A server of a project git is better found by `servers_for_project`."""
    expected_root = root_text(root)
    prefix = _digest(root) + "-"
    return _collect(lambda name, record: name.startswith(prefix) and record.get("root") == expected_root, port, healthy)


def servers_for_project(common_dir, port: int | None = None, healthy=None, kinds=("project",)) -> list[dict]:
    """The running servers of schema 2 whose `commonDir` is `common_dir` (searched in every state file, not by the name of the file),
    of the given `kinds` (None: any). A server of schema 1 never matches: it knows no tabs and is never a target of an append."""
    wanted = dashboard_worktrees.normalize_key(common_dir)

    def match(_name, record):
        return (record.get("schema") == SCHEMA_TABS and isinstance(record.get("commonDir"), str) and bool(record["commonDir"])
                and dashboard_worktrees.normalize_key(record["commonDir"]) == wanted and (kinds is None or record.get("kind") in kinds))

    return _collect(match, port, healthy)


def _appendable(common_dir) -> list[dict]:
    """The project servers of this version: the only ones a tab is added to."""
    return [item for item in servers_for_project(common_dir) if not item["legacy"]]


def address(port: int, host: str = "127.0.0.1") -> str:
    """The address of the page (`host` = loopback or a name the page answers under): `http://<host>:<port>/`, no fragment, no token."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/"


def tab_address(port: int, tab_id: str, host: str = "127.0.0.1") -> str:
    """The address of one tab: `http://<host>:<port>/?tab=<id>`."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/?tab={tab_id}"


def read_address(port: int, host: str = "127.0.0.1") -> str:
    """The address of the read socket: no fragment (safe to hand to a person from outside)."""
    name = host if host else "127.0.0.1"
    if name == "::1":
        name = "[::1]"
    return f"http://{name}:{port}/"


def _read_address(args, found: list[dict], blocked) -> int:
    """`dashboard url --read`: the address of the read socket of a running two-port server (or of a single-port server, which reads too)."""
    from .common import emit
    readers = [item for item in found if item.get("role") in ("read", "both", "page")]
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
    readers = [item for item in found if item.get("role") in ("read", "both", "page")]
    if not readers:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu z gniazdem odczytu (z zapisanym plikiem stanu)")
    if len(readers) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in readers) + ")")
    chosen = readers[0]
    bind = chosen.get("bind")
    if chosen.get("role") not in ("read", "page") or not bind or bind in LOOPBACK or str(bind).startswith("127."):
        return blocked("read_not_exposed", "gniazdo odczytu tego serwera słucha tylko na tej maszynie (rekord bez pola bind pochodzi z serwera "
                                          "sprzed 1.8.0: uruchom go ponownie); uruchom `dashboard serve` bez --read-bind 127.0.0.1 / "
                                          "dashboard.local_only albo z --read-bind 0.0.0.0")
    from .dashboard_http import lan_addresses
    addresses = [ip for ip in (lan_addresses() if bind in ("0.0.0.0", "::", "") else [bind]) if ip and not ip.startswith("<")]
    if not addresses:
        return blocked("read_not_exposed", "serwer słucha w sieci, ale ta maszyna nie ma adresu poza loopbackiem")
    urls = [read_address(chosen["port"], ip) for ip in addresses]
    if args.json:
        return emit({"schema": 1, "status": "PASS", "port": chosen["port"], "pid": chosen["pid"], "role": "read", "lanUrls": urls})
    print("\n".join(urls))
    return 0


# --- pin files: the way a command in another worktree asks the server to add its directory as a tab

def pins_dir(server_id) -> Path:
    """`<data home>/dashboard/<serverId>-pins` (ValueError for anything that is not 16 hex: the id is part of a path)."""
    if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id):
        raise ValueError("bad server id")
    return state_dir() / f"{server_id}{PINS_SUFFIX}"


def _unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def write_pin(server_id: str, tab_id: str, path, plan: str | None = None) -> Path:
    """Write `<serverId>-pins/<tab id>.json` (`{path, at[, plan]}`) atomically in a directory of this user (0700). Raises OSError / ValueError."""
    if not isinstance(tab_id, str) or not dashboard_worktrees.ID_PATTERN.fullmatch(tab_id):
        raise ValueError("bad tab id")
    directory = pins_dir(server_id)
    state_dir().mkdir(parents=True, exist_ok=True)
    if not _own_directory(state_dir()):
        raise OSError("the state directory is a link or not the user's own")
    directory.mkdir(mode=0o700, exist_ok=True)
    if not _own_directory(directory):
        raise OSError("the pin directory is a link or not the user's own")
    _restrict(directory)
    body = {"path": str(path), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if plan:
        body["plan"] = str(plan)
    data = (json.dumps(body, ensure_ascii=False) + "\n").encode("utf-8")
    if len(data) > MAX_PIN_BYTES:
        raise ValueError("pin too large")
    handle, temporary = tempfile.mkstemp(dir=str(directory), prefix=".tmp-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        _restrict(Path(temporary))
        target = directory / f"{tab_id}.json"
        _replace(temporary, target)
    except BaseException:
        _unlink(temporary)
        raise
    return target


def remove_pin(server_id: str, tab_id: str) -> None:
    try:
        _unlink(pins_dir(server_id) / f"{tab_id}.json")
    except ValueError:
        pass


def pin_count(server_id: str) -> int:
    """How many pin files (not the temporary ones) the directory of the server holds."""
    try:
        return sum(1 for name in os.listdir(pins_dir(server_id)) if PIN_NAME_RE.fullmatch(name))
    except (OSError, ValueError):
        return 0


def pin_ids(server_id: str) -> set[str]:
    """The tab ids that have a pin file (a directory that is a link or not the user's own counts as empty)."""
    try:
        directory = pins_dir(server_id)
        if os.path.islink(directory) or not _own_directory(directory):
            return set()
        return {name[:-len(".json")] for name in os.listdir(directory) if PIN_NAME_RE.fullmatch(name)}
    except (OSError, ValueError):
        return set()


def protected_ids(tabs, pinned) -> set[str]:
    """The tabs a new pin may not push out at the limit of `MAX_PINS`: the main checkout, the tab of the start directory (`current`) and the pinned ones.
    The one counter of the server (`TabRegistry.evictable`) and of the command (`append_command`)."""
    return {tab["id"] for tab in tabs if tab.get("id") == "main" or tab.get("current")} | set(pinned)


def _read_pin_file(path: Path) -> dict | None:
    """The object of a pin file, or None for anything wrong: a link, a stranger's or non-regular file, over MAX_PIN_BYTES, not JSON."""
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):   # before the open: a FIFO or a link is never opened (an open of a FIFO would block the reader)
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PIN_BYTES or (os.name != "nt" and info.st_uid != os.geteuid()):
            return None
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_PIN_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > MAX_PIN_BYTES:
        return None
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if (not isinstance(body, dict) or not isinstance(body.get("path"), str) or not body["path"] or len(body["path"]) > 2048
            or ("plan" in body and not (isinstance(body["plan"], str) and len(body["plan"]) <= 300))):
        return None
    return body


def read_pins(server_id: str, main_path=None, limit: int = MAX_PIN_FILES) -> list[dict]:
    """The pins of a server to look at now: `[{id, path, plan, at, file}]`. The reader of the server (CORE-1180 M-3): the directory must be a real directory
    of this user, a file is read with `O_NOFOLLOW` (at most 4 KiB), its name must be `<id>.json` and the id must equal the id of the path in
    it; a temporary file is skipped; at most `limit` files per call. A file that is wrong, or beyond the limit, is removed. Whether the path
    is a worktree of the project that may be shown (R2, R3) is the caller's decision."""
    try:
        directory = pins_dir(server_id)
        if os.path.islink(directory) or not directory.is_dir() or not _own_directory(directory):
            return []
        names = sorted(os.listdir(directory))
    except (OSError, ValueError):
        return []
    found = []
    seen = 0
    for name in names:
        if name.startswith(".tmp") or name.endswith(".tmp"):
            continue
        target = directory / name
        seen += 1
        if seen > limit:
            _unlink(target)
            continue
        body = _read_pin_file(target) if PIN_NAME_RE.fullmatch(name) else None
        tab = name[:-len(".json")]
        try:
            right = body is not None and dashboard_worktrees.tab_id(body["path"], main_path) == tab
        except (OSError, ValueError):
            right = False
        if not right:
            _unlink(target)
            continue
        found.append({"id": tab, "path": body["path"], "plan": body.get("plan"), "at": body.get("at"), "file": str(target)})
    return found


def remove_pins(server_id: str) -> None:
    """The server stops: its pin files and its directory go."""
    try:
        directory = pins_dir(server_id)
    except ValueError:
        return
    if not _own_directory(state_dir()):
        return
    if os.path.islink(directory):
        _unlink(directory)
        return
    shutil.rmtree(directory, ignore_errors=True)


def sweep_pins(healthy=None) -> None:
    """At the start of a server: remove the `<serverId>-pins` directories of servers that no longer run (a killed server cannot clean up)."""
    healthy = healthy or _healthy
    if not _own_directory(state_dir()):
        return
    try:
        names = [name for name in os.listdir(state_dir()) if name.endswith(PINS_SUFFIX) and SERVER_ID_RE.fullmatch(name[:-len(PINS_SUFFIX)])]
    except OSError:
        return
    if not names:
        return
    alive = set()
    try:
        files = [name for name in os.listdir(state_dir()) if name.endswith(".json") and not name.startswith(".")]
    except OSError:
        files = []
    for name in files:
        try:
            record = json.loads((state_dir() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = record.get("serverId") if isinstance(record, dict) else None
        if not isinstance(sid, str) or sid in alive:
            continue
        if os.name != "nt":
            if _pid_alive(record.get("pid")):
                alive.add(sid)
        elif healthy(record.get("host") or "127.0.0.1", record.get("port"), sid):
            alive.add(sid)
    for name in names:
        if name[:-len(PINS_SUFFIX)] not in alive:
            remove_pins(name[:-len(PINS_SUFFIX)])


# --- `dashboard serve` in a worktree of a running project: add a tab instead of a second server

def settings_flags(args) -> list[str]:
    """The flags of the settings of a server that were given (`--plan`, `--json`, `--all-tabs` and the path are not among them). `--poll` counts when it
    differs from its default (the parser cannot tell `--poll 1.0` from no flag; the effect is the same)."""
    given = []
    for flag, dest in SETTING_FLAGS:
        value = getattr(args, dest, None)
        if dest == "poll":
            if value is not None and value != DEFAULT_POLL:
                given.append(flag)
        elif dest == "host":
            if value is not None:   # `--host ''` is an explicit "every interface"
                given.append(flag)
        elif value is not None and value is not False and value != [] and value != ():
            given.append(flag)
    return given


class ServePlan:
    """What `dashboard serve` does in a directory: `mode` `append` (a project server of this git project runs and no flag of the settings was given)
    or `start`; `kind` of the server it starts (`project` or `standalone`); `project` (`dashboard_worktrees.Project` or None), `flags`, `live` (the
    project servers of this version), `legacy` (servers of an earlier version that stand in the way: a project server with a consent token, or one
    of schema 1 on the default ports of the same project), `race` (a lost bind may mean a server of this
    project that has just started: append again), `notes` (`(key, params)` of the messages to print at the start)."""

    def __init__(self):
        self.mode = "start"
        self.kind = "standalone"
        self.project = None
        self.root = None
        self.manifest = None
        self.lang = i18n.DEFAULT_LANGUAGE
        self.flags: list[str] = []
        self.live: list[dict] = []
        self.legacy: list[dict] = []
        self.race = False
        self.notes: list[tuple[str, dict]] = []


def _main_manifest(project, root, manifest):
    """The manifest the `dashboard.worktrees.*` keys are read from: the main checkout's, never a worktree's. Without a main checkout there is none (`{}`: only
    the marker `.skillsync/no-dashboard` excludes). An unreadable manifest of the main checkout is an error (ValueError): the exclusion list must not be
    dropped silently."""
    from .common import read_project
    main = project.main_path
    if not main or not os.path.isdir(main):
        return {}
    try:   # always read now: a running server must see a manifest that broke after its start
        return read_project(main)[1]
    except Exception as error:  # noqa: BLE001 - fail closed, one rule for the server and the command
        raise ValueError(f"manifest of the main checkout: {' '.join(str(error).split())[:200]}") from error


def worktree_settings(project, root, manifest) -> dict:
    """`{exclude, inactive_days}` from `dashboard.worktrees` of the main manifest (ValueError for a bad value)."""
    from . import dashboard_listen
    main = _main_manifest(project, root, manifest)
    unknown = dashboard_listen.blocking_unknown_keys(main)   # the validation that `--port` skips at the start must not be skipped for an exclusion decision
    if unknown:
        raise ValueError(str(unknown))
    return dashboard_listen.worktree_settings(main)


def check_tab(project, root, manifest):
    """`(entry, code)` of the directory `root` as a tab: `code` None when it is the main checkout or a worktree that passes R2 and is not excluded (R3),
    else `worktree_rejected` / `worktree_excluded` (R1, the trace of work, is skipped for the directory a command is run in)."""
    entry = None
    key = dashboard_worktrees.normalize_key(root)
    for item in dashboard_worktrees.list_entries(project):
        if dashboard_worktrees.normalize_key(item.path) == key:
            entry = item
            break
    if entry is None:
        return None, "worktree_rejected"
    if entry.kind != "main":
        exclude = worktree_settings(project, root, manifest)["exclude"]
        if dashboard_worktrees.is_excluded(entry.path, manifest_exclude=exclude, main_path=project.main_path):
            return entry, "worktree_excluded"
    return entry, None


def _legacy_servers(project, root) -> list[dict]:
    """Servers of schema 1 of this project on the default ports: they know no tabs, so a second server there would only fail to bind."""
    from . import dashboard_ports as ports
    wanted = {ports.DEFAULT_READ_PORT, LEGACY_CONSENT_PORT}
    seen, out = set(), []
    for base in (root, project.main_path):
        if not base:
            continue
        for item in servers(base):
            if item["schema"] == SCHEMA and item["port"] in wanted and (item["port"], item["pid"]) not in seen:
                seen.add((item["port"], item["pid"]))
                out.append(item)
    return out


def plan_serve(args) -> ServePlan:
    """Decide before anything is bound: append to a running project server, or start one (and of which `kind`). Never raises: a directory that cannot be
    read gives the plan of today (start, standalone) and the usual start reports the error; any other failure (a bug) gives the same plan and a warning on
    stderr with its text, never silently."""
    from .common import read_project
    plan = ServePlan()
    try:
        root, manifest = read_project(args.path)
        plan.root, plan.manifest = root, manifest
        plan.lang = i18n.resolve_language(manifest)[0]
        plan.flags = settings_flags(args)
        project = dashboard_worktrees.project_identity(root)
        plan.project = project
        if project is None or getattr(args, "standalone", False):
            return plan
        running = servers_for_project(project.common_dir)
        plan.live = [item for item in running if not item["legacy"]]
        if plan.live and not plan.flags:
            plan.mode = "append"
            return plan
        if plan.live:   # a project server runs, but settings were given: a server of its own, and the user is told
            first = plan.live[0]
            plan.notes.append(("srv.serve.standaloneNext", {"port": first["port"], "pid": first["pid"], "flags": " ".join(plan.flags)}))
            return plan
        no_consents = getattr(args, "no_approvals", False) or (getattr(args, "read_port", None) is not None and getattr(args, "approve_port", None) is None
                                                               and getattr(args, "port", None) is None)   # no consents socket: `ports.resolve` gives approve=None
        if plan.flags and (no_consents or getattr(args, "layers", None)):
            return plan   # a preview without consents or with another layer is never the server of the project
        try:
            entry, code = check_tab(project, root, manifest)
        except ValueError as error:   # the manifest of the main checkout (`dashboard:`) cannot be read: the start is standalone, and the user is told why
            plan.notes.append(("srv.serve.mainManifest", {"reason": " ".join(str(error).split())[:200]}))
            return plan
        if code is not None:
            plan.notes.append(("srv.serve.standaloneRejected", {}))
            return plan
        plan.kind = "project"
        if not plan.flags:
            plan.legacy = [item for item in running if item["legacy"]] or _legacy_servers(project, root)
            plan.race = not plan.legacy
    except Exception as error:  # noqa: BLE001 - see the docstring
        if not isinstance(error, (OSError, ValueError, yaml.YAMLError, layers.LayerError)):   # not a directory that cannot be read: say it
            print(f"UWAGA: dashboard serve: nie można ustalić serwera projektu ({type(error).__name__}: {' '.join(str(error).split())[:300]}); "
                  f"startuje osobny serwer tego katalogu", file=sys.stderr)
        plan.mode, plan.kind = "start", "standalone"
        plan.live, plan.legacy, plan.race = [], [], False
    return plan


def _terminal_text(terminal) -> str:
    if not isinstance(terminal, dict) or not terminal:
        return ""
    return "; " + ", ".join(f"{name}={terminal[name]}" for name in TERMINAL_VARS if terminal.get(name))


def blocked_report(args, code: str, reason: str) -> int:
    from .common import emit
    if getattr(args, "json", False):
        return emit({"schema": 1, "status": "BLOCKED", "code": code, "reason": reason})
    print(f"{code}: {reason}", file=sys.stderr)
    return 2


def _own_tab(tabs, tab_id):
    return next((tab for tab in tabs if tab.get("id") == tab_id), None)


def _wait_for_tab(common_dir, server_id: str, tab_id: str, timeout: float, approvals: bool = False):
    """Poll the state files until the server shows `tab_id` in `tabs[]`: the record that has it, else None (the server stopped: `False`). With `approvals`
    (the target has a consent socket) only the record of a consent socket is ready: the two state files of a server are rewritten one after the other."""
    deadline = time.monotonic() + timeout
    while True:
        mine = [item for item in servers_for_project(common_dir) if item.get("serverId") == server_id]
        if not mine:
            return False
        for item in mine:
            tab = _own_tab(item["tabs"], tab_id)
            if tab is not None and (item["approvals"] or not approvals):
                return item
        if time.monotonic() >= deadline:
            return None
        time.sleep(REGISTRATION_POLL_S)


def append_command(args, plan: ServePlan) -> int | None:
    """Add the directory of the command as a tab of the running project server and print the address of the tab (stdout, the last line; `--json`:
    an object); the messages go to stderr. None: no project server runs (the caller starts one). Exit codes: 0 PASS, 2 BLOCKED with `code`:
    `ambiguous_server`, `worktree_rejected` (R2), `worktree_excluded` (R3), `tab_limit`, `registration_timeout`, `server_not_running`, `invalid_project`."""
    from .common import emit
    t = lambda key, **params: i18n.t(plan.lang, key, **params)   # noqa: E731
    project, root = plan.project, plan.root
    found = _appendable(project.common_dir)
    if not found:
        return None
    ids = sorted({item["serverId"] for item in found if item.get("serverId")})
    if len(ids) != 1:
        return blocked_report(args, "ambiguous_server", t("srv.serve.ambiguous", ports=", ".join(str(item["port"]) for item in found)))
    server_id = ids[0]
    mine = [item for item in found if item["serverId"] == server_id]
    target = next((item for item in mine if item["approvals"]), mine[0])
    try:
        entry, code = check_tab(project, root, plan.manifest)
    except ValueError as error:
        return blocked_report(args, "invalid_project", " ".join(str(error).split())[:300])
    if code == "worktree_rejected":
        return blocked_report(args, code, t("srv.serve.notWorktree"))
    if code == "worktree_excluded":
        return blocked_report(args, code, t("srv.serve.excluded"))
    chosen_plan = None
    if getattr(args, "plan", None):
        from . import dashboard as engine
        chosen_plan = str(args.plan).replace(os.sep, "/").removeprefix("./")
        if engine.resolve_doc(root, chosen_plan) is None:
            return blocked_report(args, "invalid_project", f"Plan {args.plan} nie istnieje albo leży poza dozwolonymi katalogami dokumentów.")
    known = _own_tab(target["tabs"], entry.id)
    pending = known is None   # a tab the server does not list yet is not ready: wait, never print a dead address
    if pending or chosen_plan:
        if known is None and len(protected_ids(target["tabs"], pin_ids(server_id))) >= MAX_PINS:   # the same counter as the server's: nothing left to push out
            return blocked_report(args, "tab_limit", t("srv.serve.tabLimit", max=MAX_PINS))
        try:
            write_pin(server_id, entry.id, entry.path, chosen_plan)
        except (OSError, ValueError) as error:
            return blocked_report(args, "invalid_project", " ".join(str(error).split())[:300])
    if pending:
        result = _wait_for_tab(project.common_dir, server_id, entry.id, REGISTRATION_WAIT_S, bool(target["approvals"]))
        if result is None or result is False:
            remove_pin(server_id, entry.id)
            if result is False:
                return blocked_report(args, "server_not_running", t("srv.serve.serverGone"))
            return blocked_report(args, "registration_timeout", t("srv.serve.registrationTimeout", pid=target["pid"], seconds=REGISTRATION_WAIT_S))
        mine = [item for item in _appendable(project.common_dir) if item.get("serverId") == server_id] or mine
        target = next((item for item in mine if item["approvals"]), mine[0])
        known = _own_tab(target["tabs"], entry.id) or _own_tab(result["tabs"], entry.id)
        if known is None:
            remove_pin(server_id, entry.id)
            return blocked_report(args, "registration_timeout", t("srv.serve.registrationTimeout", pid=target["pid"], seconds=REGISTRATION_WAIT_S))
    host = address_host(target.get("host") or "127.0.0.1", target.get("hosts") or ())
    url = tab_address(target["port"], entry.id, host)
    pairs = [{"id": tab["id"], "path": tab["path"], "url": tab_address(target["port"], tab["id"], host)} for tab in target["tabs"]]
    print(t("srv.serve.appended", name=entry.name, id=entry.id, port=target["port"], pid=target["pid"], terminal=_terminal_text(target.get("terminal"))), file=sys.stderr)
    print(t("srv.serve.closeWarning", pid=target["pid"]), file=sys.stderr)
    print(t("srv.serve.serverSettings"), file=sys.stderr)
    if getattr(args, "json", False):
        answer = {"schema": 1, "status": "PASS", "mode": "append", "url": url, "tab": entry.id, "port": target["port"], "pid": target["pid"]}
        if getattr(args, "all_tabs", False):
            answer["tabs"] = pairs
        return emit(answer)
    if getattr(args, "all_tabs", False):
        for pair in pairs:
            if pair["id"] != entry.id:
                print(pair["url"])
    print(url)
    return 0


def retry_append(args, plan: ServePlan, attempts: int | None = None, delay: float | None = None) -> int | None:
    """A start lost its bind next to a server of the same project that has only just started (its state file appears after the bind): append again,
    `RACE_RETRIES` times `RACE_DELAY_S` apart. None: still no server of the project."""
    for _ in range(RACE_RETRIES if attempts is None else attempts):
        time.sleep(RACE_DELAY_S if delay is None else delay)
        plan.live = _appendable(plan.project.common_dir)
        if plan.live:
            return append_command(args, plan)
    return None


def url_command(args) -> int:
    """`skillsync dashboard url [path] [--port N] [--host NAME] [--all-tabs] [--json]`: the address of a running server (no token: consents go by a session).

    In a git project the default is the server of kind `project` of its `commonDir` (when none runs, the one server of this very directory, standalone
    or of an earlier version, with the address of earlier versions) and the address of the TAB of the directory
    (`/?tab=<id>`; `--all-tabs`: the pairs of all the tabs, the own one last); `--port N` names any server of
    the project, also a standalone one or one of schema 1 (`/`). BLOCKED codes:
    `invalid_project`, `server_not_running`, `approvals_disabled`, `ambiguous_server`, `unknown_tab` (the directory has no tab on that server),
    `host_not_allowed`, `read_not_exposed`."""
    from .common import emit, load_layer_project

    def blocked(code: str, reason: str) -> int:
        return blocked_report(args, code, reason)

    try:
        root, manifest, _layerset = load_layer_project(args.path, getattr(args, "layers", None))
    except Exception as error:  # noqa: BLE001 - any broken project is BLOCKED, as for the other `dashboard` commands
        return blocked("invalid_project", " ".join(str(error).split())[:300])
    lang = i18n.resolve_language(manifest)[0]
    wanted = getattr(args, "port", None)
    project = dashboard_worktrees.project_identity(root)
    if project is not None:
        found = servers_for_project(project.common_dir, wanted, kinds=None if wanted is not None else ("project",))
        if wanted is not None:   # a server of schema 1 (or one of this very directory) is found by the root
            known = {(item["port"], item["pid"], item["role"]) for item in found}
            found += [item for item in servers(root, wanted) if (item["port"], item["pid"], item["role"]) not in known]
    else:
        found = servers(root, wanted)
    if project is not None and wanted is None and not found:
        found = servers(root)   # no project server: a server of this directory that is not one (standalone or of an earlier version) is the answer, as in 1.17; several of them are `ambiguous_server` below
    if getattr(args, "lan", False):
        return _lan_addresses(args, found, blocked)
    if getattr(args, "read", False):
        return _read_address(args, found, blocked)
    running = [item for item in found if item.get("approvals")]    # servers with consents (the address is the same, the BLOCKED code of a server without them is kept)
    if not running and found:
        return blocked("approvals_disabled", "serwer tego projektu działa z --no-approvals (port " + ", ".join(str(item["port"]) for item in found)
                       + "): nie ma zgód")
    if not running:
        return blocked("server_not_running", "brak działającego `dashboard serve` tego projektu (z zapisanym plikiem stanu)")
    if len({item.get("serverId") or (item["port"], item["pid"]) for item in running}) > 1:
        return blocked("ambiguous_server", "kilka serwerów tego projektu: podaj --port (" + ", ".join(str(item["port"]) for item in running) + ")")
    chosen = running[0]
    name = getattr(args, "host", None) or None
    if name is not None:
        # Only the loopback and the names the server was started with (`--approve-host`, kept in its state file) are accepted.
        allowed = set(LOOPBACK) | {chosen.get("host")} | set(chosen.get("hosts") or [])
        if name.lower() not in {str(item).lower() for item in allowed if item}:
            return blocked("host_not_allowed", "--host: tylko loopback albo nazwa z --approve-host tego serwera (" + ", ".join(sorted(chosen.get("hosts") or [])) + ")")
    shown = name or address_host(chosen.get("host") or "127.0.0.1", chosen.get("hosts") or ())
    tabbed = chosen["schema"] == SCHEMA_TABS and chosen["kind"] == "project"
    own = None
    if chosen["schema"] == SCHEMA_TABS:
        key = dashboard_worktrees.normalize_key(root)
        own = next((tab for tab in chosen["tabs"] if dashboard_worktrees.normalize_key(tab["path"]) == key), None)
        if own is None and not getattr(args, "all_tabs", False):
            return blocked("unknown_tab", i18n.t(lang, "srv.url.unknownTab", tabs=", ".join(sorted(tab["id"] for tab in chosen["tabs"])) or "-"))
    if tabbed:
        pairs = [{"id": tab["id"], "path": tab["path"], "url": tab_address(chosen["port"], tab["id"], shown)} for tab in chosen["tabs"]]
        url = tab_address(chosen["port"], own["id"], shown) if own is not None else None
        print(i18n.t(lang, "srv.url.serverNote", port=chosen["port"], pid=chosen["pid"], terminal=_terminal_text(chosen.get("terminal"))), file=sys.stderr)
    else:   # standalone and schema 1: one tab
        url = address(chosen["port"], shown)
        pairs = [{"id": own["id"], "path": own["path"], "url": url}] if own is not None else []
    if args.json:
        answer = {"schema": 1, "status": "PASS", "url": url, "port": chosen["port"], "pid": chosen["pid"]}
        if own is not None:
            answer["tab"] = own["id"]
        if getattr(args, "all_tabs", False):
            answer["tabs"] = pairs
        if chosen.get("role") == "approve":   # two-port mode: the address of the read socket (no token) and the role of this one
            answer["role"] = "approve"
            if chosen.get("peer"):
                answer["readUrl"] = read_address(chosen["peer"], shown)
        return emit(answer)
    if getattr(args, "all_tabs", False):
        for pair in pairs:
            if own is None or pair["id"] != own["id"]:
                print(pair["url"])
    if url is not None:
        print(url)
    return 0
