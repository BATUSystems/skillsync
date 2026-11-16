"""Two sockets of the status page (`dashboard serve --read-port N --approve-port M`): the role comes from the PORT, not from a header.

Pure helpers without HTTP: the resolution and validation of the flags, the rule of the `Host` header for a server that listens beyond
loopback, the strict `Origin`, the headers of an intermediary and the counter of refused tokens. `dashboard_http` builds the servers.

Listening address (1.8.0): without a port flag the two-port mode is the default (read 5140 on every interface, consents 5141 on this
machine only); `--host`, `--read-bind`, `--approve-bind` and the `dashboard:` setting (`dashboard_listen`) narrow or move it; an explicit
`--port N` keeps the single-port mode (127.0.0.1), byte for byte as before.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time

ROLE_READ = "read"
ROLE_APPROVE = "approve"
ROLE_BOTH = "both"
ALL_INTERFACES = "0.0.0.0"
LOOPBACK_BIND = "127.0.0.1"
DEFAULT_READ_PORT = 5140       # the read socket of the default mode (also the single-port default of `--port`)
DEFAULT_APPROVE_PORT = 5141    # the consents socket of the default mode
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
# Headers an intermediary (a tunnel, a reverse proxy) adds: a write that carries one is refused unless `--allow-proxy-headers`. A closed list is a
# heuristic (an intermediary can add none of them): the barrier is the token and the strict `Origin`, not this list.
PROXY_HEADERS = ("Forwarded", "Forwarded-For", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-Port",
                 "X-Forwarded-Server", "X-Forwarded-Scheme", "X-Forwarded-Prefix", "X-Original-Forwarded-For", "X-Original-Host",
                 "X-Real-IP", "X-Client-IP", "X-Cluster-Client-IP", "True-Client-IP", "CF-Connecting-IP", "Fastly-Client-IP",
                 "X-Envoy-External-Address", "Via")
FAILED_TOKEN_REPORT_S = 60.0


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


def strict_origin_ok(origins, host_header: str | None) -> bool:
    """The `Origin` of a write must be exactly `http://<Host header>` (scheme, host AND port; case does not matter): a page of the read
    port, another name, another scheme, `null`, an empty or a repeated header is another origin. No `Origin` at all (a client that is not
    a browser) is decided by the other checks and the token. `origins`: the list of the values of the header (`headers.get_all`)."""
    values = [origins] if isinstance(origins, str) else list(origins or [])
    if not values:
        return True
    host = (host_header or "").strip().lower()
    return len(values) == 1 and bool(host) and values[0].strip().lower() == f"http://{host}"


class TokenFailures:
    """Counts refused tokens of the approve socket; `report()` says (at most once a minute) how many came since the last report."""

    def __init__(self, interval: float = FAILED_TOKEN_REPORT_S, clock=time.monotonic):
        self._lock = threading.Lock()
        self._count = 0
        self._reported_at = None
        self._interval = interval
        self._clock = clock

    def add(self, peer: str) -> str | None:
        """Register one failure; returns a console line when it is time to report, else None. The line never holds the token."""
        with self._lock:
            self._count += 1
            now = self._clock()
            if self._reported_at is not None and now - self._reported_at < self._interval:
                return None
            self._reported_at = now
            count, self._count = self._count, 0
            return f"UWAGA: {count} nieudanych prób tokenu zgód na porcie zgód (ostatnia z {peer})"


def _port_problem(flag: str, port, check) -> None:
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(f"{flag}: podaj numer portu.")
    problem = check(port)
    if problem:
        raise ValueError(f"{flag}: {problem}")


def resolve(args, check_port, settings=None) -> dict | None:
    """The two-port mode of the parsed flags: None for the single-port mode (only an explicit `--port`), else
    `{read: {port, bind} | None, approve: {port, bind} | None, allow_proxy: bool}`. Without any port flag the mode is the default one:
    read on `DEFAULT_READ_PORT` (every interface), consents on `DEFAULT_APPROVE_PORT` (this machine only; `--no-approvals`: no consents
    socket). Binds, per socket: `--read-bind`/`--approve-bind`, else (read only) an explicit `--host`, else `settings` (`dashboard:` of the manifest
    over the machine config), else the default. Raises ValueError (BLOCKED, exit 2) for a combination that is not allowed.
    `check_port(port)` returns a problem text or None."""
    settings = settings or {}
    read_port = getattr(args, "read_port", None)
    approve_port = getattr(args, "approve_port", None)
    bind_flags = (("--read-bind", getattr(args, "read_bind", None)), ("--approve-bind", getattr(args, "approve_bind", None)))
    allow_proxy = bool(getattr(args, "allow_proxy_headers", False))
    no_approvals = bool(getattr(args, "no_approvals", False))
    explicit = read_port is not None or approve_port is not None
    if not explicit and getattr(args, "port", None) is not None:   # `--port N`: the single-port mode of 1.5.0, unchanged
        for flag, value in bind_flags:
            if value is not None:
                raise ValueError(f"{flag} nie łączy się z --port (tryb jednego portu): użyj --read-port / --approve-port albo usuń --port.")
        if allow_proxy:
            raise ValueError("--allow-proxy-headers działa tylko w trybie dwóch portów (bez --port).")
        return None
    if getattr(args, "port", None) is not None:
        raise ValueError("--port nie łączy się z --read-port ani --approve-port (niejednoznaczne): użyj tylko jednego trybu.")
    if not explicit:
        # Calls that meant "one port with a Host gate" until 1.7.0 must not silently become two sockets (a token would leave this machine,
        # or consents through a tunnel would stop working): they are refused with the way to the old mode and the way to the new one.
        if getattr(args, "host", None) is not None:
            raise ValueError("--host bez --port zmieniłby znaczenie (do 1.7.0: jeden port z bramką Host). Stary tryb: --port N --host ADR. "
                             "Nowy tryb dwóch gniazd: --read-bind ADR (odczyt) i --approve-bind ADR (zgody, domyślnie tylko ta maszyna).")
        if getattr(args, "approve_host", None):
            raise ValueError("--approve-host bez --port zmieniłby znaczenie (do 1.7.0: jeden port z bramką Host i tokenem zgód). "
                             "Stary tryb, także za tunelem: --port N --approve-host NAZWA. Nowy tryb: zgody tylko na tej maszynie "
                             "(--approve-port M --approve-host NAZWA, gdy nazwa ma otwierać zgody).")
        read_port = DEFAULT_READ_PORT
        approve_port = None if no_approvals else DEFAULT_APPROVE_PORT
        if no_approvals and bind_flags[1][1] is not None:
            raise ValueError("--approve-bind z --no-approvals: port zgód nie startuje bez zgód (usuń jedno z nich).")
        if no_approvals and allow_proxy:
            raise ValueError("--allow-proxy-headers z --no-approvals: port zgód nie startuje bez zgód (usuń jedno z nich).")
    if no_approvals and approve_port is not None and explicit:
        raise ValueError("--no-approvals z --approve-port: port zgód nie startuje bez zgód (usuń jedno z nich).")
    if read_port is None and getattr(args, "read_bind", None) is not None:
        raise ValueError("--read-bind bez --read-port.")
    if approve_port is None and getattr(args, "approve_bind", None) is not None:
        raise ValueError("--approve-bind bez --approve-port.")
    if approve_port is None and allow_proxy:
        raise ValueError("--allow-proxy-headers działa tylko z --approve-port.")
    for flag, port in (("--read-port", read_port), ("--approve-port", approve_port)):
        if port is not None:
            _port_problem(flag, port, check_port)
    if read_port is not None and read_port == approve_port:
        raise ValueError("--read-port i --approve-port muszą być różnymi portami.")
    if read_port is None and "*" in {str(name).strip() for name in (getattr(args, "read_host", None) or ())}:
        raise ValueError("--read-host '*' działa tylko na gnieździe odczytu (--read-port).")
    if approve_port is not None and "*" in {str(name).strip() for name in (getattr(args, "approve_host", None) or ())}:
        raise ValueError("--approve-host '*' jest niedozwolone: nazwa zgód musi być jawna.")
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
    return {"read": {"port": read_port, "bind": read_bind} if read_port is not None else None,
            "approve": {"port": approve_port, "bind": approve_bind} if approve_port is not None else None,
            "allow_proxy": allow_proxy, "read_default": read_default}


LISTEN_NOTE_UNTIL = (1, 10)   # the sentence about the changed default listen address is printed by releases before this one


def port_taken(bind: str, port: int) -> bool:
    """Does something already accept connections where a socket bound to `bind:port` would listen? Checked BEFORE the bind on every system:
    macOS/BSD (`SO_REUSEADDR`) and Windows let a narrower address bind next to somebody else's wildcard one, so the bind alone does not
    tell that a page is already served there (and requests from this machine would reach the other one)."""
    target = {"0.0.0.0": "127.0.0.1", "": "127.0.0.1", "::": "::1"}.get(bind, bind)
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
