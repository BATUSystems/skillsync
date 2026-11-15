"""Two sockets of the status page (`dashboard serve --read-port N --approve-port M`): the role comes from the PORT, not from a header.

Pure helpers without HTTP: the resolution and validation of the flags, the rule of the `Host` header for a server that listens beyond
loopback, the strict `Origin`, the headers of an intermediary and the counter of refused tokens. `dashboard_http` builds the servers.

Listening address (decision of the owner, 15.11.2026: "oba w sieci"): in the two-port mode both sockets listen on every interface unless
`--host`, `--read-bind` or `--approve-bind` says otherwise; the single-port mode (`--port`) keeps listening on 127.0.0.1, byte for byte as before.
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
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
# Headers an intermediary (a tunnel, a reverse proxy) adds: a write that carries one is refused unless `--allow-proxy-headers`.
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-Port", "X-Forwarded-Server",
                 "X-Real-IP", "X-Client-IP", "True-Client-IP", "CF-Connecting-IP", "Via")
FAILED_TOKEN_REPORT_S = 60.0


def proxy_headers(headers) -> list[str]:
    """The names of the intermediary headers present in the request (`headers`: an `email.message.Message`-like object)."""
    return [name for name in PROXY_HEADERS if headers.get(name) is not None]


def is_local_ip(name: str) -> bool:
    """Is `name` (a `Host` without port) an IP literal of THIS machine? A literal cannot come from DNS rebinding (the browser sends the
    name it was given), so it is safe to accept under a server that listens beyond loopback. Tested by binding a throwaway UDP socket:
    it succeeds only for an address that belongs to an interface of this machine."""
    try:
        ip = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        return False
    if ip.is_unspecified:   # 0.0.0.0 / :: reach this machine from a browser but name no interface
        return False
    if ip.is_loopback:
        return True
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


def resolve(args, check_port) -> dict | None:
    """The two-port mode of the parsed flags: None for the single-port mode (nothing of the new flags is given), else
    `{read: {port, bind} | None, approve: {port, bind} | None, allow_proxy: bool}`. Raises ValueError (BLOCKED, exit 2) for a combination
    that is not allowed. `check_port(port)` returns a problem text or None."""
    read_port = getattr(args, "read_port", None)
    approve_port = getattr(args, "approve_port", None)
    bind_flags = (("--read-bind", getattr(args, "read_bind", None)), ("--approve-bind", getattr(args, "approve_bind", None)))
    allow_proxy = bool(getattr(args, "allow_proxy_headers", False))
    if read_port is None and approve_port is None:
        for flag, value in bind_flags:
            if value is not None:
                raise ValueError(f"{flag} działa tylko z --read-port / --approve-port.")
        if allow_proxy:
            raise ValueError("--allow-proxy-headers działa tylko z --approve-port.")
        return None
    if getattr(args, "port", None) is not None:
        raise ValueError("--port nie łączy się z --read-port ani --approve-port (niejednoznaczne): użyj tylko jednego trybu.")
    if getattr(args, "no_approvals", False) and approve_port is not None:
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
    if approve_port is not None and "*" in {str(name).strip() for name in (getattr(args, "approve_host", None) or ())}:
        raise ValueError("--approve-host '*' jest niedozwolone: nazwa zgód musi być jawna.")
    host = getattr(args, "host", None)
    default = host if host not in (None, "") else ALL_INTERFACES
    read_bind = getattr(args, "read_bind", None) or default
    approve_bind = getattr(args, "approve_bind", None) or default
    return {"read": {"port": read_port, "bind": read_bind} if read_port is not None else None,
            "approve": {"port": approve_port, "bind": approve_bind} if approve_port is not None else None,
            "allow_proxy": allow_proxy}


def is_loopback_bind(bind: str) -> bool:
    return bind in LOOPBACK_NAMES or bind.startswith("127.")
