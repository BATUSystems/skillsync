"""The sockets of the status page: the role comes from the PORT, not from a header.

Pure helpers without HTTP: the resolution and validation of the flags, the rule of the `Host` header for a server that listens beyond
loopback, the exact `Origin` of a write, and the headers of an intermediary. `dashboard_http` builds the servers.

Default (CORE-2000-UNIFIED): ONE socket, the `page` role, on 5140 (every interface): everybody reads, a session with the role `approver` consents
(there is no consents socket and no token). `--host`, `--read-bind` and the `dashboard:` setting (`dashboard_listen`) narrow or move it. An explicit
`--port N` keeps the single-port mode (127.0.0.1); explicit `--read-port` / `--approve-port` keep the two sockets of 1.8.0 (the consent socket
is then the page of the session routes).
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time

from .prototype_server import PROXY_HEADERS

ROLE_READ = "read"
ROLE_APPROVE = "approve"
ROLE_BOTH = "both"
ROLE_PAGE = "page"             # the one socket of the default mode: reads for every allowed Host, consents only through a session with `approver`
ALL_INTERFACES = "0.0.0.0"
LOOPBACK_BIND = "127.0.0.1"
DEFAULT_READ_PORT = 5140       # the page socket of the default mode (also the single-port default of `--port`)
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
# `PROXY_HEADERS` (headers an intermediary adds; a request with one is not `local`, so it takes no consent without a trusted TLS proxy) lives in
# `prototype_server`, the standalone engine that decides what is local for a login and a consent: one source of truth, imported above.


def proxy_headers(headers) -> list[str]:
    """The names of the intermediary headers present in the request (`headers`: an `email.message.Message`-like object)."""
    return [name for name in PROXY_HEADERS if headers.get(name) is not None]


def _nonlocal_bind_allowed() -> bool:
    """Linux `ip_nonlocal_bind` makes `bind` succeed for ANY address, so the bind probe proves nothing there."""
    for name in ("ipv4", "ipv6"):
        try:
            with open(f"/proc/sys/net/{name}/ip_nonlocal_bind", encoding="ascii") as handle:
                if handle.read().strip() not in ("", "0"):
                    return True
        except OSError:
            continue
    return False


HOST_LOOKUP_TIMEOUT = 2.0    # seconds a caller waits for the resolver (a name that does not resolve can take half a minute)
HOST_LOOKUP_TTL = 60.0       # a resolved answer is kept this long
HOST_LOOKUP_NEGATIVE_TTL = 15.0   # "the resolver was too slow" is kept this long, so a series of requests starts at most one lookup
HOST_LOOKUP_STALE_MAX = 300.0   # an older good answer serves a resolver that is too slow only this long after the last SUCCESSFUL lookup
_lookup_lock = threading.Lock()
_lookup = {"value": None, "until": 0.0, "pending": None, "good_at": None}


def _resolve_into_cache(done: threading.Event) -> None:
    found: set[str] = set()
    ttl = HOST_LOOKUP_NEGATIVE_TTL   # a failed lookup is remembered as briefly as a slow one
    good = False
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0].split("%")[0])
        ttl, good = HOST_LOOKUP_TTL, True
    except Exception:  # noqa: BLE001 - OSError (gaierror), UnicodeError, ValueError: whatever the resolver raises is "no addresses"
        found = set()
    finally:
        with _lookup_lock:
            if _lookup["pending"] is done:   # not after a reset (tests): a stale lookup never writes into a newer cache
                _lookup["value"], _lookup["until"] = found, time.monotonic() + ttl
                _lookup["good_at"] = time.monotonic() if good else None   # a failure is no "good answer" to fall back on
                _lookup["pending"] = None
        done.set()   # always: a waiter must never be left behind by a lookup that raised


def host_addresses() -> set[str]:
    """Addresses of the name of this machine, WITHOUT ever blocking the caller for longer than `HOST_LOOKUP_TIMEOUT`: the lookup runs in a
    daemon thread (one at a time, its answer cached for `HOST_LOOKUP_TTL`); when the resolver is too slow the answer is the empty set, kept for
    `HOST_LOOKUP_NEGATIVE_TTL` (an older good answer serves instead only up to `HOST_LOOKUP_STALE_MAX` after the last successful lookup), and a late answer still fills the cache. A resolver that blocks for tens of seconds (a name that does not
    resolve) therefore delays neither the start nor the stop of the server, and N requests start at most one lookup."""
    with _lookup_lock:
        if _lookup["value"] is not None and time.monotonic() < _lookup["until"]:
            return set(_lookup["value"])
        pending = _lookup["pending"]
        if pending is None:
            pending = _lookup["pending"] = threading.Event()
            threading.Thread(target=_resolve_into_cache, args=(pending,), name="host-lookup", daemon=True).start()
    pending.wait(HOST_LOOKUP_TIMEOUT)
    with _lookup_lock:
        if not pending.is_set():   # too slow: remember it for a while (the thread goes on and may still fill the cache)
            # an older good answer is better than none, but only for `HOST_LOOKUP_STALE_MAX` after the last successful lookup: a resolver that
            # stays slow must not keep an address that no longer belongs to this machine alive for ever (renewed every negative TTL)
            good_at = _lookup["good_at"]
            fresh = good_at is not None and time.monotonic() - good_at <= HOST_LOOKUP_STALE_MAX
            kept = set(_lookup["value"] or ()) if fresh else set()
            until = time.monotonic() + HOST_LOOKUP_NEGATIVE_TTL
            if kept:
                until = min(until, good_at + HOST_LOOKUP_STALE_MAX)   # a renewal never carries the old answer beyond the limit
            _lookup["value"], _lookup["until"] = kept, until
            return set(kept)
        return set(_lookup["value"] or ())


def _reset_host_lookup() -> None:   # tests
    with _lookup_lock:
        _lookup.update(value=None, until=0.0, pending=None, good_at=None)


def _enumerated_addresses() -> set[str]:
    """Addresses of this machine by name resolution (bounded, see `host_addresses`) plus the route probe: the fallback when the bind probe is
    unreliable. When the resolver is too slow only the route address counts: an address that is not found is refused (fail closed)."""
    found = host_addresses()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))   # TEST-NET-1: nothing is sent, the route only picks the local address
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return found


def is_local_ip(name: str) -> bool:
    """Is `name` (a `Host` without port) an IP literal of THIS machine? A literal cannot come from DNS rebinding (the browser sends the
    name it was given), so it is safe to accept under a server that listens beyond loopback. Tested by binding a throwaway UDP socket:
    it succeeds for an address that belongs to an interface of this machine, but also for multicast and broadcast ones and, with
    `ip_nonlocal_bind`, for any address: those are refused first (multicast, reserved, unspecified, the limited broadcast and, because
    the netmask is not known, every IPv4 address ending in .255 unless it is one of the enumerated addresses) and the probe is replaced by the enumerated addresses where it proves nothing."""
    try:
        ip = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    if ip.is_unspecified or ip.is_multicast:   # 0.0.0.0, ::, 224/4, ff00::/8
        return False
    if ip.is_loopback:
        return True
    if ip.is_reserved:   # 240/4 (also 255.255.255.255) and the reserved IPv6 blocks; checked after the loopback: `::1` lies in ::/8
        return False
    if ip.version == 4 and int(ip) & 0xFF == 0xFF:
        # A subnet broadcast looks the same (the mask is unknown), but in a /23 or wider the real address of this machine may end in .255:
        # only an address found by name resolution or by the route probe (never a broadcast) counts.
        return str(ip) in _enumerated_addresses()
    if _nonlocal_bind_allowed():
        return str(ip) in _enumerated_addresses()
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as probe:
            probe.bind((str(ip), 0))
    except OSError:
        return False
    return True


def origin_matches(origins, host_header: str | None, secure: bool = False) -> bool:
    """The `Origin` of a consent must be present exactly once and name the `Host` of the request (`http` or `https`, host AND port; case does not
    matter): another page, another name, `null`, an empty or a repeated header, or no `Origin` at all is refused (a consent comes from this page only).
    `secure` (the request came through TLS): only `https://` counts. `origins`: the list of the values of the header (`headers.get_all`)."""
    values = [origins] if isinstance(origins, str) else list(origins or [])
    host = (host_header or "").strip().lower()
    if len(values) != 1 or not host:
        return False
    value = values[0].strip().lower()
    return value == f"https://{host}" or (not secure and value == f"http://{host}")


def _port_problem(flag: str, port, check) -> None:
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(f"{flag}: podaj numer portu.")
    problem = check(port)
    if problem:
        raise ValueError(f"{flag}: {problem}")


def resolve(args, check_port, settings=None) -> dict | None:
    """The socket mode of the parsed flags: None for the single-port mode (only an explicit `--port`), else
    `{page, read, approve: {port, bind} | None, approvals: bool, allow_proxy: bool}`. Without any port flag the mode is the default one:
    ONE `page` socket on `DEFAULT_READ_PORT` (every interface; reads for all, consents through a session; `--no-approvals`: `approvals` False).
    With `--read-port` / `--approve-port` the two sockets of 1.8.0. Binds, per socket: `--read-bind`/`--approve-bind`, else (read only) an explicit `--host`, else `settings` (`dashboard:` of the manifest
    over the machine config), else the default. Raises ValueError (BLOCKED, exit 2) for a combination that is not allowed.
    `check_port(port)` returns a problem text or None."""
    settings = settings or {}
    read_port = getattr(args, "read_port", None)
    approve_port = getattr(args, "approve_port", None)
    bind_flags = (("--read-bind", getattr(args, "read_bind", None)), ("--approve-bind", getattr(args, "approve_bind", None)))
    allow_proxy = bool(getattr(args, "allow_proxy_headers", False))
    no_approvals = bool(getattr(args, "no_approvals", False))
    explicit = read_port is not None or approve_port is not None
    if allow_proxy:   # removed: a consent comes locally or through TLS (a trusted proxy that forwards HTTPS), never through a plain intermediary
        raise ValueError("--allow-proxy-headers zostało usunięte: zgody przychodzą lokalnie albo przez TLS. Za odwrotnym proxy z TLS użyj "
                         "--trust-proxy z --proxy-address ADRES.")
    if not explicit and getattr(args, "port", None) is not None:   # `--port N`: the single-port mode of 1.5.0, unchanged
        for flag, value in bind_flags:
            if value is not None:
                raise ValueError(f"{flag} nie łączy się z --port (tryb jednego portu): użyj --read-port / --approve-port albo usuń --port.")
        return None
    if getattr(args, "port", None) is not None:
        raise ValueError("--port nie łączy się z --read-port ani --approve-port (niejednoznaczne): użyj tylko jednego trybu.")
    default_mode = not explicit
    if default_mode:
        if "*" in {str(name).strip() for name in (getattr(args, "read_host", None) or ())}:
            raise ValueError("--read-host '*' działa tylko na gnieździe odczytu (--read-port): strona zgód zawsze sprawdza nagłówek Host.")
        # Calls that meant "one port with a Host gate" until 1.7.0 must not silently become another mode: they are refused with the way to the old one.
        if getattr(args, "host", None) is not None:
            raise ValueError("--host bez --port zmieniłby znaczenie (do 1.7.0: jeden port z bramką Host). Stary tryb: --port N --host ADR. "
                             "Adres jedynego gniazda: --read-bind ADR.")
        if getattr(args, "approve_host", None):
            raise ValueError("--approve-host bez --port zmieniłby znaczenie (do 1.7.0: jeden port z bramką Host i tokenem zgód). "
                             "Stary tryb, także za tunelem: --port N --approve-host NAZWA. Nazwa, pod którą strona ma odpowiadać: --read-host NAZWA.")
        if bind_flags[1][1] is not None:
            raise ValueError("--approve-bind bez --approve-port: zgody idą tym samym portem co strona (sesja z rolą approver), osobnego gniazda nie ma.")
        read_port = DEFAULT_READ_PORT
    if no_approvals and approve_port is not None and explicit:
        raise ValueError("--no-approvals z --approve-port: port zgód nie startuje bez zgód (usuń jedno z nich).")
    if read_port is None and getattr(args, "read_bind", None) is not None:
        raise ValueError("--read-bind bez --read-port.")
    if approve_port is None and getattr(args, "approve_bind", None) is not None:
        raise ValueError("--approve-bind bez --approve-port.")
    for flag, port in (("--read-port", read_port), ("--approve-port", approve_port)):
        if port is not None:
            _port_problem(flag, port, check_port)
    if read_port is not None and read_port == approve_port:
        raise ValueError("--read-port i --approve-port muszą być różnymi portami.")
    if read_port is None and "*" in {str(name).strip() for name in (getattr(args, "read_host", None) or ())}:
        raise ValueError("--read-host '*' działa tylko na gnieździe odczytu (--read-port).")
    if approve_port is not None and "*" in {str(name).strip() for name in (getattr(args, "approve_host", None) or ())}:
        raise ValueError("--approve-host '*' jest niedozwolone: nazwa zgód musi być jawna.")
    for flag, value in bind_flags:
        if value is not None and not str(value).strip():
            raise ValueError(f"{flag}: podaj adres (0.0.0.0 = wszystkie interfejsy, 127.0.0.1 = tylko ta maszyna).")
    host = getattr(args, "host", None)
    if host is not None and read_port is None:
        raise ValueError("--host dotyczy gniazda odczytu (--read-port); adres gniazda zgód ustawia --approve-bind.")
    if host is None:
        flag_default = None
    else:   # an explicit `--host X` is the bind of the READ socket; `--host ''` keeps meaning every interface
        flag_default = host if host != "" else ALL_INTERFACES
    read_default = not (getattr(args, "read_bind", None) or flag_default or settings.get("read_bind"))   # nothing chose it: the 1.8.0 default
    read_bind = getattr(args, "read_bind", None) or flag_default or settings.get("read_bind") or ALL_INTERFACES
    approve_bind = getattr(args, "approve_bind", None) or settings.get("approve_bind") or LOOPBACK_BIND   # `--host` never moves the consents socket
    if default_mode:   # the one socket of the page: the read bind is its bind; consents are on unless `--no-approvals`
        return {"page": {"port": read_port, "bind": read_bind}, "read": None, "approve": None, "approvals": not no_approvals,
                "allow_proxy": False, "read_default": read_default}
    return {"read": {"port": read_port, "bind": read_bind} if read_port is not None else None,
            "approve": {"port": approve_port, "bind": approve_bind} if approve_port is not None else None, "page": None,
            "approvals": approve_port is not None, "allow_proxy": allow_proxy, "read_default": read_default}


LISTEN_NOTE_UNTIL = (1, 10)   # the sentence about the changed default listen address is printed by releases before this one


def port_taken(bind: str, port: int) -> bool:
    """Does something already accept connections where a socket bound to `bind:port` would listen? Checked BEFORE the bind on every system:
    macOS/BSD (`SO_REUSEADDR`) and Windows let a narrower address bind next to somebody else's wildcard one, so the bind alone does not
    tell that a page is already served there (and requests from this machine would reach the other one)."""
    target = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(bind, bind)
    if target not in LOOPBACK_NAMES and not target.startswith("127.") and not is_local_ip(target):
        return False   # an address that is not this machine's: nothing to probe (and no packet to a stranger)
    try:
        with socket.create_connection((target, int(port)), timeout=0.3):
            return True
    except (OSError, ValueError):
        return False


_OWN_NAMES: frozenset[str] | None = None


def own_names() -> frozenset[str]:
    """The names this machine calls itself: `socket.gethostname()` (lower case), its short form and `<short>.local`. Text only, never a DNS
    lookup (the start and every request stay free of a resolver). The read socket accepts them as `Host` without `--read-host`: an attacker's
    domain cannot be the machine's own host name, so this is not the DNS rebinding the rule guards against."""
    global _OWN_NAMES
    if _OWN_NAMES is None:
        try:
            full = socket.gethostname().strip().lower().rstrip(".")
        except OSError:
            full = ""
        names = set()
        if full and not any(c in full for c in ":[]/*") and not any(c.isspace() for c in full):
            short = full.split(".", 1)[0]
            names = {full, short, f"{short}.local"} - {"", "localhost", "localhost.local", "localhost.localdomain"}
        _OWN_NAMES = frozenset(names)
    return _OWN_NAMES


def is_loopback_bind(bind: str) -> bool:
    return bind in LOOPBACK_NAMES or bind.startswith("127.")
