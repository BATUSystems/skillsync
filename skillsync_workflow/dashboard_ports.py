"""The sockets of the status page: the role comes from the PORT, not from a header.

Pure helpers without HTTP: the resolution and validation of the flags, the rule of the `Host` header for a server that listens beyond
loopback, the exact `Origin` of a write, and the headers of an intermediary. `dashboard_http` builds the servers.

Default (CORE-2000-UNIFIED): ONE socket, the `page` role, on 5140 (every interface): everybody reads, a session with the role `approver` consents
(there is no consents socket and no token). `--host`, `--read-bind` and the `dashboard:` setting (`dashboard_listen`) narrow or move it. An explicit
`--port N` keeps the single-port mode (127.0.0.1); explicit `--read-port` / `--approve-port` keep the two sockets of 1.8.0 (the consent socket
is then the page of the session routes). `--page-port N` moves the one socket of the default mode.

`Origins` names the two origins of a server (the page and the prototypes) for one request: `frame-src`, `frame-ancestors`, the targets of the
session handover and the redirect to the canonical host.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit

from . import i18n
from .prototype_server import PROXY_HEADERS, link_has_proxy_header, link_is_loopback_host_header, origin_authority as authority

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


def resolve(args, check_port, settings=None, lang: str = i18n.DEFAULT_LANGUAGE) -> dict | None:
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
    page_port = getattr(args, "page_port", None)
    no_approvals = bool(getattr(args, "no_approvals", False))
    explicit = read_port is not None or approve_port is not None
    if allow_proxy:   # removed: a consent comes locally or through TLS (a trusted proxy that forwards HTTPS), never through a plain intermediary
        raise ValueError("--allow-proxy-headers zostało usunięte: zgody przychodzą lokalnie albo przez TLS. Za odwrotnym proxy z TLS użyj "
                         "--trust-proxy z --proxy-address ADRES.")
    if page_port is not None and (explicit or getattr(args, "port", None) is not None):
        raise ValueError(i18n.t(lang, "srv.serve.pagePortModes"))
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
            raise ValueError("--approve-host bez --port zmieniłby znaczenie (do 1.7.0: jeden port z bramką Host). "
                             "Stary tryb, także za tunelem: --port N --approve-host NAZWA. Nazwa, pod którą strona ma odpowiadać: --read-host NAZWA.")
        if bind_flags[1][1] is not None:
            raise ValueError("--approve-bind bez --approve-port: zgody idą tym samym portem co strona (sesja z rolą approver), osobnego gniazda nie ma.")
        read_port = DEFAULT_READ_PORT if page_port is None else page_port
        if page_port is not None:
            _port_problem("--page-port", page_port, check_port)
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


def loopback_companion(bound: str) -> str | None:
    """The loopback address a server listening on `bound` has to listen on as well, None when it needs no second socket. A password and a
    consent are `local` only through 127.0.0.1 or ::1, so a socket on any other address (also another one of 127/8) would leave this machine
    without a way to sign in; a wildcard address covers the loopback already."""
    return None if bound in ("", ALL_INTERFACES, "::") or bound in LOOPBACK_NAMES else LOOPBACK_BIND


# --------------------------------------------------------------------------- the two origins of one server

def host_key(host_header: str | None, scheme: str = "http") -> str:
    """`name:port` for comparisons, the default port of `scheme` filled in; "" for an unusable `Host`."""
    found = authority(host_header)
    if found is None:
        return ""
    name, port = found
    return f"{name}:{port if port is not None else (443 if scheme == 'https' else 80)}"


def origin_key(origin: str | None) -> str:
    """`host_key` of an origin `scheme://host[:port]`; "" without one."""
    if not origin:
        return ""
    parts = urlsplit(origin)
    return host_key(parts.netloc, parts.scheme)


REDIRECT_MARKER_SECONDS = 30


class Origins:
    """The origin of the page and the origin of the prototypes as ONE request sees them (`handler`: `headers` and `_origin_kind()`, `'page'` for
    a socket of the page, `'proto'` for the socket of the prototypes).

    `direct`: no header of an intermediary, and the request does not name a configured origin: either no `--public-url` / `--prototype-url`
    was given, or the `Host` is a loopback literal. Both origins then have the `Host` name of the request (so they are same-site) and the ports
    of the sockets. Otherwise the flags are the origins; an origin of the prototypes that cannot be known (an `https` page without
    `--prototype-url`, an intermediary without the flag) is None: no `frame-src`, no handover."""

    def __init__(self, public_url: str | None, prototype_url: str | None, page_ports, prototype_port: int):
        self.public_url = public_url
        self.prototype_url = prototype_url
        self.page_ports = tuple(page_ports)   # the sockets that serve the page; the FIRST carries the sessions
        self.prototype_port = prototype_port

    def _own_flag(self, handler) -> str | None:
        return self.prototype_url if handler._origin_kind() == "proto" else self.public_url

    def _name(self, handler) -> tuple[str, int | None]:
        return authority(handler.headers.get("Host")) or ("127.0.0.1", None)

    def direct(self, handler) -> bool:
        headers = handler.headers
        if link_has_proxy_header(headers):
            return False
        own = self._own_flag(handler)
        if own and host_key(headers.get("Host"), urlsplit(own).scheme) == origin_key(own):
            return False
        if self.public_url is None and self.prototype_url is None:
            return True
        return link_is_loopback_host_header(headers.get_all("Host") or [])

    def _derived_page(self, handler) -> str:
        name, port = self._name(handler)
        if handler._origin_kind() != "proto":   # a request on a socket of the page: its own authority (also behind a forwarded port)
            return f"http://{name}:{port}" if port is not None else f"http://{name}"
        return f"http://{name}:{self.page_ports[0]}"

    def _derived_prototype(self, handler) -> str:
        name, port = self._name(handler)
        if handler._origin_kind() == "proto":
            return f"http://{name}:{port}" if port is not None else f"http://{name}"
        return f"http://{name}:{self.prototype_port}"

    def page(self, handler) -> str:
        if self.direct(handler) or self.public_url is None:
            return self._derived_page(handler)
        return self.public_url

    def _configured_prototype(self, handler) -> str | None:
        if self.prototype_url:
            return self.prototype_url
        if self.public_url is None:
            return None if link_has_proxy_header(handler.headers) else self._derived_prototype(handler)
        parts = urlsplit(self.public_url)
        public = authority(parts.netloc)
        if parts.scheme != "http" or public is None:   # an https port is never invented: TLS is a proxy of its own
            return None
        return f"http://{public[0]}:{self.prototype_port}"

    def prototype(self, handler) -> str | None:
        found = self._derived_prototype(handler) if self.direct(handler) else self._configured_prototype(handler)
        if found is None or origin_key(found) == origin_key(self.page(handler)):   # never the origin of the page: that would end the isolation
            return None
        return found

    def frame_unavailable(self, handler) -> bool:
        """The page or the prototypes of this request have an address a CSP source cannot name (an IPv6 literal, a name with `_`): the browser
        would drop it from `frame-src` / `frame-ancestors`, so no prototype frame works there."""
        return any(origin is not None and not csp_source(origin) for origin in (self.page(handler), self.prototype(handler)))

    def ancestors(self, handler) -> list[str]:
        """The page origins that may frame the prototypes (the two sockets of the explicit mode both serve the page)."""
        if not self.direct(handler) and self.public_url:
            return [self.public_url]
        name, _port = self._name(handler)
        first = self.page(handler)
        found = [first]
        for port in self.page_ports:
            origin = f"http://{name}:{port}"
            if origin_key(origin) != origin_key(first):
                found.append(origin)
        return found

    def secure(self, handler) -> bool:
        """Is the origin of this request configured as `https` (its cookies are then `Secure`)?"""
        own = self._own_flag(handler)
        return bool(own and own.startswith("https://") and not self.direct(handler))

    def redirect(self, handler) -> str | None:
        """The canonical origin a document request belongs to, or None to answer it here. Only for a name that is not the loopback and has no
        header of an intermediary, only when a flag names the origin, and never for a request that carries the marker of a redirect just made
        (`redirect_marker`): a proxy that rewrites `Host` and adds no header brings the browser back under the same other name, and must not loop."""
        headers = handler.headers
        if link_has_proxy_header(headers) or link_is_loopback_host_header(headers.get_all("Host") or []) or self._marked(handler):
            return None
        if handler._origin_kind() == "proto":
            canonical = self.prototype_url or (self._configured_prototype(handler) if self.public_url else None)
        else:
            canonical = self.public_url
        if not canonical or host_key(headers.get("Host"), urlsplit(canonical).scheme) == origin_key(canonical):
            return None
        return canonical

    @staticmethod
    def _marker_name(handler) -> str:
        return f"skillsync_canonical_{handler._origin_kind()}"   # cookies do not separate the ports of a host: one name per kind of origin

    def redirect_marker(self, handler) -> str:
        """The `Set-Cookie` of the redirect to the canonical origin. The browser sends it back only to the host that set it, so it returns here
        exactly when the redirect did not change the name the browser asks for."""
        return f"{self._marker_name(handler)}=1; Max-Age={REDIRECT_MARKER_SECONDS}; Path=/; HttpOnly; SameSite=Lax"

    def _marked(self, handler) -> bool:
        wanted = f"{self._marker_name(handler)}=1"
        return any(part.strip() == wanted for line in handler.headers.get_all("Cookie") or [] for part in line.split(";"))


def csp_source(origin: str) -> bool:
    """Can `origin` be written as a source of a Content-Security-Policy? The grammar of a host has no IPv6 literal and no `_`."""
    netloc = urlsplit(origin).netloc
    return not netloc.startswith("[") and "_" not in netloc


def _site(name: str) -> str:
    try:
        ipaddress.ip_address(name.strip("[]"))
        return name
    except ValueError:
        return ".".join(name.split(".")[-2:])


def likely_cross_site(first: str, second: str) -> bool:
    """A cheap look (no public suffix list): another scheme, another IP literal or other last two labels. Only for a warning."""
    one, two = urlsplit(first), urlsplit(second)
    a, b = authority(one.netloc), authority(two.netloc)
    return one.scheme != two.scheme or a is None or b is None or _site(a[0]) != _site(b[0])
