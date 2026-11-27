"""CORE-2000-UNIFIED U-3a: the second origin of `dashboard serve` (spec sections 1 and 7): the two sockets, the split of the routes, the headers of
both origins, the lists of `Host`, the cookies, what a script of the prototype origin cannot do, `--single-origin`, `--page-port` and the life of
the sockets; service workers and the loopback bind of the prototype origin (U-3a-2): python3 -m unittest tests.test_skillsync_unified_origins

A real `skillsync dashboard serve` process and plain HTTP. A script on the prototype origin is SIMULATED: the requests a browser would send for it
(its `Origin`, `Sec-Fetch-Site: same-site`, the cookies of the same host); no browser runs here. Every refusal has its control."""
from __future__ import annotations

import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from serve_start import release, state_files, state_snapshot, own_state  # noqa: E402
from tmp_paths import descend  # noqa: E402
from unified_helpers import (  # noqa: E402
    APPROVER, APPROVER_ACCOUNT, APPROVER_PASSWORD, CLI, COMMENTER, COMMENTER_PASSWORD, LAYER, PAGE_LAUNCHER, POSIX_ONLY, RUN_ID, Browser, Server,
    UnifiedCase, free_port, http_request, lan_address, port_pool, self_reachable, write)

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
import unified_helpers  # noqa: E402

ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_ACCOUNT), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))
PUBLIC = "status.example.test"
PROTO = "proto.example.test"
VIEWER = "viewer.example.test"
MARK = "-".join(("znacznik", "tresci", "prototypu", "81"))
OUTSIDE = "-".join(("kanarek", "spoza", "katalogu", "prototypow", "82"))
HIDDEN = "-".join(("kanarek", "pliku", "ukrytego", "83"))
LAN = lan_address()
NO_LAN = "brak adresu spoza loopbacku, z którym ta maszyna połączy się sama"
EXPOSED = "anonimowi widzą autorów"                  # the two start warnings of M2 (a project in Polish)
LOCAL_ONLY = "prototypy tylko na tej maszynie"
WORKER = {"Service-Worker": "script"}


def listening(port: int, address: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((address, port), timeout=1):
            return True
    except OSError:
        return False


def bindable(port: int, address: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((address, port))
        except OSError:
            return False
    return True


def hold(port: int) -> socket.socket:
    """A listener of another program on `port` (the caller closes it)."""
    other = socket.socket()
    if os.name != "nt":
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # the port may be in TIME_WAIT after an earlier test
    other.bind(("127.0.0.1", port))
    other.listen(1)
    return other


def page_port_with_free_neighbours() -> int:
    """A port of the pool whose `+2` and `+3` are free too (the default origin of the prototypes and its fallback)."""
    skip: list[int] = []
    for _attempt in range(len(port_pool())):
        port = free_port(skip=skip)
        if all(bindable(port + offset) for offset in (2, 3)):
            return port
        skip.append(port)
    raise RuntimeError("no port of the test pool with free neighbours")


def directives(policy: str) -> dict:
    found: dict = {}
    for part in (policy or "").split(";"):
        words = part.split()
        if words:
            assert words[0] not in found, ("a directive given twice", words[0], policy)
            found[words[0]] = words[1:]
    return found


def origin(port: int, name: str = "127.0.0.1") -> str:
    return f"http://{name}:{port}"


class OriginsCase(UnifiedCase):
    ACCOUNTS = ACCOUNTS

    def setUp(self) -> None:
        super().setUp()
        self.session_waits_for("CP")
        self.root = descend(self.project, "docs/prototypes")
        write(self.root / "demo" / "prototype.json", json.dumps({"slug": "demo", "title": "Demo"}))
        write(self.root / "demo" / "index.html", f"<!doctype html><html data-prototype-id=\"demo\"><body>{MARK}</body></html>\n")
        write(self.root / "demo" / "prototype.js", f"// {MARK}\n")

    # --- helpers

    def start(self, *extra: str, **kwargs) -> tuple:
        """`(server, browser of the page, port of the prototype origin)`."""
        server = self.serve(*extra, **kwargs)
        page = server.browser()
        port = page.status()["prototype"]["port"]
        self.assertNotIn(port, server.ports, "the origin of the prototypes is a socket of its own")
        return server, page, port

    def proto_get(self, port: int, path: str, headers: dict | None = None, address: str = "127.0.0.1"):
        return http_request(address, port, "GET", path, headers)

    def handoff(self, page: Browser, port: int) -> tuple:
        """The whole handover as a browser does it: `(browser of the prototype origin, the code)`."""
        proto = Browser(port)
        begun = proto.get("/_handoff/begin")
        self.assertEqual(begun.status, 303, begun.text)
        nonce = begun.set_cookie.split(";")[0]
        found = re.fullmatch(re.escape(origin(page.port)) + r"/_handoff\?challenge=([0-9a-f]{64})", begun.header("Location") or "")
        self.assertIsNotNone(found, begun.header("Location"))
        issued = page.post("/_api/v1/handoff", {"challenge": found.group(1)})
        self.assertEqual(issued.status, 200, issued.text)
        self.assertEqual(issued.data["target"], origin(port) + "/_handoff")
        done = self.exchange(port, issued.data["code"], nonce)
        self.assertEqual(done.status, 303, done.text)
        pairs = [value.split(";")[0] for key, value in done.headers if key.lower() == "set-cookie"]
        session = [pair for pair in pairs if pair.startswith("skillsync_proto_")]
        self.assertEqual(len(session), 1, ("the handover sets one session cookie of the prototype origin", [p.partition("=")[0] for p in pairs]))
        proto.cookie = session[0]
        proto.csrf = proto.get("/_api/v1/session").data.get("csrf")
        return proto, issued.data["code"]

    def exchange(self, port: int, code: str, nonce: str):
        return http_request("127.0.0.1", port, "POST", "/_handoff", {"Origin": origin(port), "Cookie": nonce,
                            "Content-Type": "application/x-www-form-urlencoded"}, ("code=" + quote(code, safe="")).encode("ascii"))

    def cp_body(self, browser: Browser) -> dict:
        offer = browser.offer("cp")
        self.assertEqual(offer["state"], "waiting", offer)
        delivery = browser.status()["delivery"]
        return {"kind": "cp", "path": RUN_ID, "sha": offer["sha"], "branch": delivery["branch"], "target": delivery["target"],
                "planState": offer.get("planState")}

    def blocked(self, *args: str, page_port: int | None = None) -> str:
        """A start that is refused: the reason. `page_port` = the default mode (the launcher keeps it off 5140)."""
        if page_port is None:
            command = [sys.executable, str(CLI), "dashboard", "serve", str(self.project)]
        else:
            launcher = write(self.tmp / "page_launcher.py", PAGE_LAUNCHER.format(root=str(CLI.parent)))
            command = [sys.executable, str(launcher), str(page_port), "dashboard", "serve", str(self.project), "--read-bind", "127.0.0.1"]
        done = subprocess.run([*command, *args, *LAYER], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
                              env=self.env, cwd=str(self.tmp), stdin=subprocess.DEVNULL, check=False)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report["status"], "BLOCKED", report)
        self.assertEqual(state_files(self.env), [], "a refused start leaves no state file")
        return report["reason"]


# --------------------------------------------------------------------------- the two sockets and their life


class SocketTests(OriginsCase):
    def test_the_default_origin_is_the_page_port_plus_two_in_every_mode(self) -> None:
        for mode in ("page", "port", "two"):
            with self.subTest(mode=mode):
                first = page_port_with_free_neighbours()
                if mode == "two":   # the helper picks the second port itself: `+2` counts from the read port
                    server = self.serve(mode=mode)
                    read = server.ports[0]
                    expected_free = read + 2 not in server.ports
                    found = server.browser().status()["prototype"]["port"]
                    self.assertNotIn(found, server.ports)
                    if expected_free and found != read + 2:
                        self.assertFalse(bindable(read + 2), (server.ports, found))
                else:
                    server = self.serve(mode=mode, port=first)
                    found = server.browser().status()["prototype"]["port"]
                    self.assertEqual(found, first + 2)
                reply = self.proto_get(found, "/demo/index.html")
                self.assertEqual(reply.status, 200, reply.text)
                self.assertIn(MARK, reply.text)
                self.assertIn(f":{found}", server.log(), "the start names the origin of the prototypes")
                server.stop()

    def test_a_taken_default_port_moves_to_the_next_free_one(self) -> None:
        port = page_port_with_free_neighbours()
        with hold(port + 2):
            server, page, found = self.start(port=port)
            self.assertEqual(found, port + 3)
            self.assertIn(MARK, self.proto_get(found, "/demo/index.html").text)

    def test_a_taken_or_unusable_explicit_prototype_port_refuses_the_start(self) -> None:
        port = page_port_with_free_neighbours()
        with hold(port + 2):
            for mode, args, kwargs in (("port", ("--port", str(port)), {}), ("page", (), {"page_port": port})):
                with self.subTest(mode=mode):
                    reason = self.blocked(*args, "--prototype-port", str(port + 2), **kwargs)
                    self.assertIn("--prototype-port", reason)
                    self.assertIn(str(port + 2), reason)
                    self.assertTrue(bindable(port), "the socket of the page was released")
        for value in (port, 5141, 4445):   # the page's own port, the old consents port, a forbidden one
            with self.subTest(value=value):
                self.assertIn("--prototype-port", self.blocked("--port", str(port), "--prototype-port", str(value)))
        server, page, found = self.start("--prototype-port", str(port + 2), port=port)   # the control: now free
        self.assertEqual(found, port + 2)

    def test_page_port_moves_the_one_socket_of_the_default_mode(self) -> None:
        wanted = page_port_with_free_neighbours()
        stand_in = free_port(skip=(wanted, wanted + 2))   # what the default would be (the launcher moves it off 5140)
        launcher = write(self.tmp / "page_launcher.py", PAGE_LAUNCHER.format(root=str(CLI.parent)))
        command = [sys.executable, str(launcher), str(stand_in), "dashboard", "serve", str(self.project), "--read-bind", "127.0.0.1",
                   "--page-port", str(wanted), *LAYER]
        out, err = self.tmp / "page-port.out", self.tmp / "page-port.err"
        before, started = state_snapshot(self.env), datetime.now(timezone.utc)
        with out.open("wb") as out_handle, err.open("wb") as err_handle:
            proc = subprocess.Popen(command, stdout=out_handle, stderr=err_handle, env=self.env, cwd=str(self.tmp))
        self.addCleanup(release, proc)
        server = Server(proc, (wanted,), "127.0.0.1", out, err)
        self.addCleanup(server.stop)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None and not own_state(self.env, proc, wanted, self.project, started, before=before):
            time.sleep(0.1)
        self.assertTrue(own_state(self.env, proc, wanted, self.project, started, before=before), server.log())
        self.assertFalse(listening(stand_in), "the default port is not used any more")
        self.assertEqual(server.browser().status()["prototype"]["port"], wanted + 2)

    def test_page_port_refusals(self) -> None:
        first, second = page_port_with_free_neighbours(), None
        second = free_port(skip=(first,))
        for args in (("--port", str(second)), ("--read-port", str(second)), ("--read-port", str(second), "--approve-port", str(second + 1))):
            with self.subTest(args=args):
                self.assertIn("--page-port", self.blocked("--page-port", str(first), *args))
        self.assertIn("--page-port", self.blocked("--page-port", "80", page_port=first))

    @unittest.skipIf(os.name == "nt", "SIGTERM i raport zakończenia: tylko POSIX")
    def test_sigterm_closes_both_origins_and_removes_the_state(self) -> None:
        for mode in ("page", "port", "two"):
            for how in (signal.SIGTERM, signal.SIGINT):
                with self.subTest(mode=mode, signal=how.name):
                    server, page, found = self.start(mode=mode)
                    records = [json.loads(text) for text in self.state_texts()]
                    self.assertEqual(sorted(item["port"] for item in records), sorted(server.ports), "no state file for the prototype origin")
                    for item in records:
                        self.assertEqual(item["schema"], 2)
                        self.assertEqual({"token", "tokenId"} & set(item), set())
                    self.assertTrue(listening(found))
                    server.proc.send_signal(how)
                    self.assertEqual(server.proc.wait(timeout=20), 0, server.log())
                    for port in (*server.ports, found):
                        self.assertFalse(listening(port), port)
                        self.assertTrue(bindable(port), port)
                    self.assertEqual(state_files(self.env), [])
                    self.assertNotIn("Traceback", server.log())

    def test_the_prototype_origin_stays_on_the_loopback_without_a_prototype_url(self) -> None:
        # kontrakt M2 (U-3a-2): do redakcji danych autorów (U-3b) origin prototypów nie wychodzi poza loopback, chyba że operator nazwie inny host
        server, page, found = self.start()
        self.assertTrue(listening(found))
        self.assertNotIn(EXPOSED, server.log())
        self.assertNotIn(LOCAL_ONLY, server.log(), "a page on the loopback needs no warning")
        server.stop()
        if not LAN or not self_reachable(LAN):
            self.skipTest(NO_LAN)
        port = page_port_with_free_neighbours()
        cases = (("no flag", (), False, LOCAL_ONLY),
                 ("a loopback --prototype-url does not widen", ("--prototype-url", origin(port + 2, "localhost")), False, LOCAL_ONLY),
                 ("an https --public-url alone", ("--public-url", f"https://{PUBLIC}"), False, LOCAL_ONLY),
                 ("a --prototype-url of another host", ("--prototype-url", f"http://{PROTO}:{port + 2}"), True, EXPOSED))
        for label, flags, reachable, warning in cases:
            with self.subTest(label):
                server = self.serve("--prototype-port", str(port + 2), *flags, address=LAN, port=port)
                self.assertEqual(listening(port + 2, LAN), reachable)
                self.assertEqual(listening(port + 2), not reachable)
                self.assertIn(warning, server.log())
                self.assertNotIn(LOCAL_ONLY if warning == EXPOSED else EXPOSED, server.log())
                server.stop()

    def test_a_named_prototype_url_does_not_widen_a_page_on_the_loopback(self) -> None:
        port = page_port_with_free_neighbours()
        server, page, found = self.start("--prototype-url", f"http://{PROTO}:{port + 2}", port=port)
        self.assertTrue(listening(found))
        self.assertNotIn(EXPOSED, server.log())
        if LAN:
            self.assertFalse(listening(found, LAN))

    def test_single_origin_beyond_the_loopback_warns_that_authors_are_visible(self) -> None:
        server = self.serve("--single-origin")
        self.assertNotIn(EXPOSED, server.log())
        server.stop()
        if not LAN or not self_reachable(LAN):
            self.skipTest(NO_LAN)
        server = self.serve("--single-origin", address=LAN)
        self.assertIn(EXPOSED, server.log())

    def test_requests_of_the_prototype_origin_are_logged_only_with_verbose_and_never_the_code(self) -> None:
        for extra, logged in (((), False), (("--verbose",), True)):
            with self.subTest(extra=extra):
                server, page, found = self.start(*extra)
                self.assertEqual(page.login().status, 200)
                proto, code = self.handoff(page, found)
                self.proto_get(found, "/demo/prototype.js")
                server.stop()
                self.assertEqual("/demo/prototype.js" in server.log(), logged, server.log()[-1500:])
                for secret in (code, proto.token, page.token, APPROVER_PASSWORD):
                    self.assertNotIn(secret, server.log())


# --------------------------------------------------------------------------- the split of the routes


class RouteSplitTests(OriginsCase):
    def test_no_consent_route_on_the_prototype_origin(self) -> None:
        for mode in ("page", "port", "two"):
            with self.subTest(mode=mode):
                server, page, found = self.start(mode=mode)
                self.assertEqual(page.login().status, 200)
                body = self.cp_body(page)
                before = self.snapshot()
                # the strongest attempt: the cookie and the CSRF token of the approver, exact headers of the origin asked
                thief = page.clone()
                thief.port, thief.host = found, f"127.0.0.1:{found}"
                as_page = thief.post_headers(Origin=page.origin)
                for path in ("/api/approve", "/api/feedback-approve", "/api/plan-status"):
                    for headers in (thief.post_headers(), as_page):
                        reply = thief.post(path, body, headers)
                        self.assertIn(reply.status, (404, 405), (path, reply.status, reply.text))
                    self.assertEqual(thief.get(path).status, 404, path)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.rows(), [])
                done = page.post("/api/approve", body)   # the control: the same body on the origin of the page
                self.assertEqual(done.status, 200, done.text)
                self.assertEqual(len(self.rows()), 1)
                descend(self.project, f".skillsync/runs/{RUN_ID}/approvals.jsonl").unlink()
                server.stop()

    def test_no_password_login_link_or_status_on_the_prototype_origin(self) -> None:
        server, page, found = self.start()
        proto = Browser(found)
        for login, password in ((APPROVER, APPROVER_PASSWORD), (COMMENTER, COMMENTER_PASSWORD)):
            reply = proto.login(login, password)
            self.assertEqual((reply.status, reply.data.get("error")), (403, "login_on_page"), reply.text)
            self.assertIsNone(proto.cookie)
        self.assertEqual(page.login().status, 200)
        handed, _code = self.handoff(page, found)
        change = handed.post("/_api/v1/password", {"current": APPROVER_PASSWORD, "new": APPROVER_PASSWORD + "-nowe"})
        self.assertEqual((change.status, change.data.get("error")), (403, "login_on_page"), change.text)
        form = proto.get("/_login?next=http://evil.example/")
        self.assertEqual((form.status, form.header("Location")), (302, origin(server.port) + "/_login"))
        self.assertNotIn("password", form.text.lower())
        self.assertEqual(proto.get("/_login/link?token=" + "a" * 43).status, 404)
        self.assertNotEqual(page.get("/_login/link?token=" + "a" * 43).status, 404)   # the control: the page has the route
        self.assertIn("login-link", page.get("/_api/v1/session").data["server"]["features"])
        self.assertNotIn("login-link", proto.get("/_api/v1/session").data["server"]["features"])
        for path in ("/api/status", "/events", "/api/doc?path=docs/specs/demo-etap-1.md", "/api/descriptions", "/assets/app.js"):
            with self.subTest(path=path):
                self.assertEqual(proto.get(path).status, 404)
        self.assertEqual(proto.post("/_api/v1/handoff", {"challenge": "0" * 64}).status, 404)

    def test_the_page_serves_no_file_and_no_api_of_the_prototypes(self) -> None:
        for mode in ("page", "two"):
            with self.subTest(mode=mode):
                server, page, found = self.start(mode=mode)
                self.assertEqual(page.login().status, 200)
                for port in server.ports:
                    for path in ("/demo/index.html", "/demo/", "/demo/prototype.js", "/demo/prototype.json", "/docs/prototypes/demo/index.html",
                                 "/prototypes/demo/index.html", "/assets/../demo/index.html", "/assets/%2e%2e/demo/prototype.js",
                                 "/_api/v1/prototypes/demo/operations", "/_api/v1/project", "/_experts", "/_handoff/begin"):
                        reply = http_request("127.0.0.1", port, "GET", path, page.base_headers() | {"Host": f"127.0.0.1:{port}"})
                        self.assertIn(reply.status, (404, 405), (port, path, reply.text[:200]))
                        self.assertNotIn(MARK, reply.text, (port, path))
                self.assertIn(MARK, self.proto_get(found, "/demo/prototype.js").text)   # the control
                self.assertEqual(self.proto_get(found, "/_api/v1/prototypes/demo/operations").status, 200)
                server.stop()


class FileTests(OriginsCase):
    def test_forbidden_files_traversal_and_links_stay_closed(self) -> None:
        write(self.root / "demo" / "comments.json", json.dumps({"x": HIDDEN}))
        write(self.root / "demo" / "questions.json", json.dumps({"x": HIDDEN}))
        write(self.root / "questions.json", json.dumps({"x": HIDDEN}))
        write(self.root / "demo" / ".env", HIDDEN)
        write(self.root / ".git" / "config", HIDDEN)
        outside = write(descend(self.project, "docs/outside.txt"), OUTSIDE)
        users = descend(self.project, ".skillsync/prototype/users.json")
        links = []
        try:
            for name, target in (("to-users.json", users), ("to-outside.txt", outside), ("to-hidden.txt", self.root / "demo" / ".env"),
                                 ("to-journal.txt", self.root / "demo" / "comments.json"), ("dir", descend(self.project, "docs"))):
                (self.root / "demo" / name).symlink_to(target, target_is_directory=target.is_dir())
                links.append(f"/demo/{name}")
        except (OSError, NotImplementedError):
            if os.name != "nt":
                raise
        server, page, found = self.start()
        self.assertEqual(page.login().status, 200)
        handed, _code = self.handoff(page, found)
        paths = [*links, "/demo/dir/outside.txt", "/demo/comments.json", "/demo/questions.json", "/questions.json", "/demo/.env", "/.git/config",
                 "/demo/../../outside.txt", "/demo/%2e%2e/%2e%2e/outside.txt", "/%2e%2e/outside.txt", "/..%2foutside.txt",
                 "/demo/..%2f..%2f..%2f.skillsync%2fprototype%2fusers.json", "/demo/..%5c..%5coutside.txt", "/demo/prototype.js%00.png",
                 "//etc/hostname", "/%2fetc%2fhostname", "/../.skillsync/prototype/users.json", "/.skillsync/prototype/users.json"]
        for who in (Browser(found), handed):
            for path in paths:
                with self.subTest(path=path, signed_in=who.cookie is not None):
                    reply = who.get(path)
                    self.assertIn(reply.status, (400, 403, 404), reply.text[:200])
                    for canary in (OUTSIDE, HIDDEN, APPROVER, "pbkdf2"):
                        self.assertNotIn(canary, reply.text)
        self.assertIn(MARK, handed.get("/demo/prototype.js").text)   # the control


# --------------------------------------------------------------------------- Host lists


FOREIGN = ("evil.example", "evil.example:5140", "127.0.0.1.evil.example", "localhost.evil.example", "", None)


class HostTests(OriginsCase):
    def assert_refused(self, port: int, host: str | None, path: str, method: str = "GET") -> None:
        reply = http_request("127.0.0.1", port, method, path, {"Host": host, "Content-Type": "application/json"},
                             {"login": COMMENTER, "password": COMMENTER_PASSWORD} if method == "POST" else None)
        self.assertEqual(reply.status, 403, (port, host, path, reply.text[:200]))
        self.assertEqual((sorted(reply.data), reply.data.get("code")), (["code", "error"], "host_refused"), reply.text)
        self.assertNotIn(MARK, reply.text)
        self.assertEqual(reply.set_cookie, "")

    def test_a_foreign_host_is_refused_on_both_origins(self) -> None:
        server, page, found = self.start()
        for host in FOREIGN:
            with self.subTest(host=host):
                for port, path in ((found, "/demo/index.html"), (found, "/"), (found, "/_api/v1/session"), (found, "/_handoff/begin"),
                                   (server.port, "/"), (server.port, "/api/status"), (server.port, "/_login")):
                    self.assert_refused(port, host, path)
                for port in (found, server.port):
                    self.assert_refused(port, host, "/_api/v1/login", "POST")
        for port, path in ((found, "/demo/index.html"), (server.port, "/api/status")):   # the control
            for name in ("127.0.0.1", "localhost", "[::1]"):
                self.assertEqual(http_request("127.0.0.1", port, "GET", path, {"Host": f"{name}:{port}"}).status, 200, (port, name))

    def test_each_origin_has_its_own_list_of_names(self) -> None:
        port = page_port_with_free_neighbours()
        for mode in ("page", "port"):
            with self.subTest(mode=mode):
                server, page, found = self.start("--public-url", f"http://{PUBLIC}:{port}", "--prototype-url", f"http://{PROTO}:{port + 2}",
                                                 "--read-host", VIEWER, mode=mode, port=port)
                ask = lambda to, name, path: http_request("127.0.0.1", to, "GET", path, {"Host": f"{name}:{to}"})   # noqa: E731
                self.assertEqual(ask(server.port, PUBLIC, "/api/status").status, 200)
                self.assertEqual(ask(found, PROTO, "/demo/index.html").status, 200)
                self.assert_refused(server.port, f"{PROTO}:{server.port}", "/api/status")
                self.assert_refused(found, f"{PUBLIC}:{found}", "/demo/index.html")
                self.assertIn(ask(found, VIEWER, "/demo/index.html").status, (200, 302))   # a listed name belongs to both
                self.assertIn(ask(server.port, VIEWER, "/api/status").status, (200,))
                server.stop()

    def test_ip_literals_on_the_prototype_origin_follow_the_mode_of_the_page(self) -> None:
        address, strange = "127.0.0.2", "198.51.100.23"   # a literal of this machine outside the loopback names; a literal of another one
        server, page, found = self.start()   # the default mode: addresses of the machine as literals
        self.assertEqual(self.proto_get(found, "/demo/index.html", {"Host": f"{address}:{found}"}).status, 200)
        self.assertEqual(page.get("/api/status", {"Host": f"{address}:{server.port}"}).status, 200)
        self.assert_refused(found, f"{strange}:{found}", "/demo/index.html")
        self.assert_refused(server.port, f"{strange}:{server.port}", "/api/status")
        server.stop()
        server, page, found = self.start(mode="port")   # beside `--port`: loopback and the listed names only
        self.assert_refused(found, f"{address}:{found}", "/demo/index.html")
        self.assert_refused(server.port, f"{address}:{server.port}", "/api/status")

    def test_local_only_covers_both_origins(self) -> None:
        if not LAN or not self_reachable(LAN):
            self.skipTest(NO_LAN)
        port = page_port_with_free_neighbours()
        found = port + 2
        # kontrakt M2: bez `--prototype-url` peer z sieci w ogóle nie ma gniazda prototypów
        server = self.serve("--local-only", "--prototype-port", str(found), address=LAN, port=port)
        reply = http_request(LAN, server.port, "GET", "/api/status")
        self.assertEqual((reply.status, reply.data.get("code")), (403, "local_only"), reply.text)
        with self.assertRaises(ConnectionRefusedError):
            http_request(LAN, found, "GET", "/demo/index.html")
        server.stop()
        # with an origin of another host the socket is open there, and --local-only refuses on it, whatever the method
        server = self.serve("--local-only", "--prototype-port", str(found), "--prototype-url", f"http://{PROTO}:{found}", address=LAN, port=port)
        for to, method, path in ((server.port, "GET", "/api/status"), (found, "GET", "/demo/index.html"), (found, "GET", "/_handoff/begin"),
                                 (found, "POST", "/_api/v1/login"), (found, "PUT", "/demo/index.html"), (found, "OPTIONS", "/api/approve"),
                                 (found, "DELETE", "/"), (found, "PATCH", "/")):
            with self.subTest(port=to, method=method, path=path):
                reply = http_request(LAN, to, method, path)
                self.assertEqual((reply.status, reply.data.get("code")), (403, "local_only"), reply.text)
                self.assertNotIn(MARK, reply.text)

    def test_every_method_on_the_prototype_origin_passes_the_host_list_first(self) -> None:
        server, page, found = self.start()
        for method in ("PUT", "OPTIONS", "DELETE", "PATCH", "TRACE", "PROPFIND"):
            with self.subTest(method=method):
                for host in ("evil.example", ""):
                    reply = http_request("127.0.0.1", found, method, "/demo/index.html", {"Host": host})
                    self.assertEqual((reply.status, reply.data.get("code")), (403, "host_refused"), (host, reply.text))
                    self.assertIsNone(reply.header("Allow"))
                self.assertEqual(http_request("127.0.0.1", found, method, "/demo/index.html").status, 405)   # the control: an own name

    def test_port_mode_takes_the_name_of_public_url_for_reading_only(self) -> None:
        port = page_port_with_free_neighbours()
        server, page, found = self.start("--public-url", f"http://{PUBLIC}:{port}", mode="port", port=port)
        self.assertEqual(page.login().status, 200)
        body = self.cp_body(page)
        for to, path in ((server.port, "/api/status"), (found, "/demo/index.html")):
            self.assertIn(http_request("127.0.0.1", to, "GET", path, {"Host": f"{PUBLIC}:{to}"}).status, (200, 302), (to, path))
            self.assert_refused(to, f"evil.example:{to}", path)
        self.assertEqual(http_request("127.0.0.1", server.port, "GET", "/api/status", {"Host": f"{PUBLIC}:{port}"}).status, 200)
        before = self.snapshot()
        named = page.clone()
        named.host = f"{PUBLIC}:{port}"
        reply = named.post("/api/approve", body)
        self.assertEqual((reply.status, reply.data.get("code")), (403, "consent_context"), reply.text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(page.post("/api/approve", body).status, 200)   # the control: the same session locally

# --------------------------------------------------------------------------- headers


class HeaderTests(OriginsCase):
    def assert_page_headers(self, reply, frame_src: list) -> None:
        found = directives(reply.header("Content-Security-Policy"))
        self.assertEqual(found.get("frame-ancestors"), ["'none'"], found)
        self.assertEqual(found.get("frame-src"), frame_src, found)
        self.assertEqual(reply.header("X-Frame-Options"), "DENY")
        self.assertEqual(reply.header("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertIn("no-store", reply.header("Cache-Control"))
        for name, values in found.items():
            self.assertNotIn("*", values, name)
        for name in ("script-src", "default-src"):
            self.assertNotIn("'unsafe-inline'", found.get(name, []), found)
            self.assertNotIn("'unsafe-eval'", found.get(name, []), found)

    def test_page_headers_frame_only_the_prototype_origin_and_cannot_be_framed(self) -> None:
        server, page, found = self.start()
        for name in ("127.0.0.1", "localhost"):   # both origins keep the name of the request: same site
            host = {"Host": f"{name}:{server.port}"}
            for path in ("/", "/_login", f"/_handoff?challenge={'0' * 64}"):
                with self.subTest(name=name, path=path):
                    reply = page.get(path, host)
                    self.assertEqual(reply.status, 200, reply.text[:200])
                    own = directives(reply.header("Content-Security-Policy"))
                    self.assertEqual(own.get("frame-ancestors"), ["'none'"])
                    self.assertEqual(reply.header("X-Frame-Options"), "DENY")
                    self.assertEqual(reply.header("Cross-Origin-Opener-Policy"), "same-origin")
                    for origins in (own.get("frame-src", []),):
                        self.assertTrue(set(origins) <= {origin(found, name), "'none'"}, own)
            with self.subTest(name=name, path="/ exact"):
                reply = page.get("/", host)
                self.assert_page_headers(reply, [origin(found, name)])
                policy = directives(reply.header("Content-Security-Policy"))
                self.assertEqual((policy["script-src"], policy["connect-src"], policy["form-action"]), (["'self'"], ["'self'"], ["'none'"]))

    def test_prototype_headers_admit_only_itself_and_the_page(self) -> None:
        for mode in ("page", "two"):
            with self.subTest(mode=mode):
                server, page, found = self.start(mode=mode)
                for name in ("127.0.0.1", "localhost"):
                    wanted = {"'self'", *(origin(port, name) for port in server.ports)}
                    for path in ("/demo/index.html", "/demo/prototype.js", "/", "/missing", "/_handoff", "/_handoff/begin", "/_login",
                                 "/_api/v1/session"):
                        with self.subTest(name=name, path=path):
                            reply = self.proto_get(found, path, {"Host": f"{name}:{found}"})
                            values = [value for key, value in reply.headers if key.lower() == "content-security-policy"]
                            self.assertTrue(values, (path, reply.headers))
                            for value in values:
                                ancestors = directives(value).get("frame-ancestors")
                                self.assertIsNotNone(ancestors, value)
                                self.assertEqual(sorted(ancestors), sorted(wanted), value)
                server.stop()

    def test_no_origin_answers_a_preflight_or_opens_cors(self) -> None:
        server, page, found = self.start()
        for port, other in ((server.port, found), (found, server.port)):
            for path in ("/api/approve", "/_api/v1/session", "/_api/v1/handoff"):
                with self.subTest(port=port, path=path):
                    headers = {"Origin": origin(other), "Access-Control-Request-Method": "POST",
                               "Access-Control-Request-Headers": "x-status-board,x-skillsync-csrf"}
                    for reply in (http_request("127.0.0.1", port, "OPTIONS", path, headers),
                                  http_request("127.0.0.1", port, "GET", path, {"Origin": origin(other)})):
                        self.assertEqual([key for key, _value in reply.headers if key.lower().startswith("access-control-")], [])
                    self.assertGreaterEqual(http_request("127.0.0.1", port, "OPTIONS", path, headers).status, 400)

    def test_configured_origins_drive_the_headers_and_the_handover(self) -> None:
        port = page_port_with_free_neighbours()
        public, proto_url = f"https://{PUBLIC}", f"https://{PROTO}"
        server, page, found = self.start("--public-url", public, "--prototype-url", proto_url, "--trust-proxy", "--proxy-address", "127.0.0.1",
                                         port=port)
        forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}
        self.assert_page_headers(page.get("/", {"Host": PUBLIC, **forwarded}), [proto_url])
        framed = self.proto_get(found, "/demo/index.html", {"Host": PROTO, **forwarded})
        self.assertEqual(framed.status, 200, framed.text)
        self.assertEqual(sorted(directives(framed.header("Content-Security-Policy"))["frame-ancestors"]), sorted(["'self'", public]))
        begun = self.proto_get(found, "/_handoff/begin", {"Host": PROTO, **forwarded})
        self.assertEqual(begun.status, 303, begun.text)
        self.assertTrue(begun.header("Location").startswith(public + "/_handoff?challenge="), begun.header("Location"))
        self.assertIn("Secure", begun.set_cookie)
        self.assert_page_headers(page.get("/"), [origin(found)])   # direct use on the loopback keeps the derived origins

    def test_an_https_page_without_the_prototype_url_frames_nothing_and_hands_nothing_over(self) -> None:
        server, page, found = self.start("--public-url", f"https://{PUBLIC}", "--trust-proxy", "--proxy-address", "127.0.0.1")
        remote = Browser(server.port, host=PUBLIC, scheme="https", extra={"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"})
        self.assert_page_headers(remote.get("/"), ["'none'"])
        self.assertEqual(remote.login().status, 200)
        reply = remote.post("/_api/v1/handoff", {"challenge": "0" * 64})
        self.assertEqual((reply.status, reply.data.get("error")), (404, "handoff_unavailable"), reply.text)
        self.assertNotIn("code", reply.data)

    def test_one_origin_given_twice_refuses_the_start(self) -> None:
        port = page_port_with_free_neighbours()
        same = f"http://{PUBLIC}:{port}"
        for args in (("--public-url", same, "--prototype-url", same), ("--prototype-url", origin(port)), ("--prototype-url", origin(port, "localhost"))):
            with self.subTest(args=args):
                self.assertIn("--prototype-url", self.blocked("--port", str(port), *args))
                self.assertTrue(bindable(port) and bindable(port + 2))

    def test_documents_are_redirected_to_the_configured_name_only_for_other_names(self) -> None:
        port = page_port_with_free_neighbours()
        public, proto_url = f"http://{PUBLIC}:{port}", f"http://{PROTO}:{port + 2}"
        server, page, found = self.start("--public-url", public, "--prototype-url", proto_url, "--read-host", VIEWER, port=port)
        for to, path, canonical in ((port, "/?plan=x", public), (found, "/demo/index.html?x=1", proto_url)):
            with self.subTest(to=to):
                reply = http_request("127.0.0.1", to, "GET", path, {"Host": f"{VIEWER}:{to}"})
                self.assertEqual((reply.status, reply.header("Location")), (302, canonical + path), reply.text)
                for host, extra in ((canonical.partition("//")[2], {}), (f"127.0.0.1:{to}", {}), (f"{VIEWER}:{to}", {"X-Forwarded-Host": "evil.example"})):
                    other = http_request("127.0.0.1", to, "GET", path, {"Host": host, **extra})
                    self.assertNotEqual(other.status, 302, (host, extra, other.header("Location")))
        for path in ("//evil.example/x", "/%2f%2fevil.example"):
            reply = http_request("127.0.0.1", found, "GET", path, {"Host": f"{VIEWER}:{found}"})
            location = reply.header("Location")
            self.assertTrue(location is None or location.startswith(proto_url + "/"), (path, location))


    def test_a_network_name_without_a_prototype_url_gets_no_frame_and_no_handover(self) -> None:
        port = page_port_with_free_neighbours()
        for flags in (("--read-host", VIEWER), ("--public-url", f"http://{VIEWER}:{port}")):
            with self.subTest(flags=flags):
                server, page, found = self.start(*flags, port=port)
                self.assertEqual(page.login().status, 200)
                challenge = re.search(r"challenge=([0-9a-f]{64})", Browser(found).get("/_handoff/begin").header("Location")).group(1)
                remote = page.clone()
                remote.host = f"{VIEWER}:{port}"
                self.assert_page_headers(remote.get("/"), ["'none'"])
                reply = remote.post("/_api/v1/handoff", {"challenge": challenge})
                self.assertEqual((reply.status, reply.data.get("error")), (404, "handoff_unavailable"), reply.text)
                self.assertNotIn("code", reply.data)
                self.assert_page_headers(page.get("/"), [origin(found)])   # the controls: on the machine itself both work
                issued = page.post("/_api/v1/handoff", {"challenge": challenge})
                self.assertEqual(issued.status, 200, issued.text)
                server.stop()

    def test_an_address_a_policy_cannot_name_says_so_without_a_redirect(self) -> None:
        named = "my_viewer.example"
        server, page, found = self.start("--read-host", named)
        for host in (f"[::1]:{server.port}", f"{named}:{server.port}"):
            with self.subTest(host=host):
                reply = page.get("/", {"Host": host})
                self.assertEqual(reply.status, 200, reply.header("Location"))
                self.assert_page_headers(reply, ["'none'"])
                self.assertEqual(reply.text.count("data-frame-unavailable"), 1)
                self.assertIn(origin(server.port) + "/", reply.text)
                framed = self.proto_get(found, "/demo/index.html", {"Host": host.rsplit(":", 1)[0] + f":{found}"})
                self.assertEqual(framed.status, 200, framed.header("Location"))
        for name in ("127.0.0.1", "localhost"):   # the control: no notice where the frame works
            reply = page.get("/", {"Host": f"{name}:{server.port}"})
            self.assertNotIn("data-frame-unavailable", reply.text, name)
            self.assert_page_headers(reply, [origin(found, name)])

    def test_an_origin_flag_with_text_a_header_could_carry_on_is_refused(self) -> None:
        port = page_port_with_free_neighbours()
        bad = (f"http://{PROTO};x", f"http://{PROTO}; frame-ancestors *", f"http://{PROTO} http://evil.example", f"http://{PROTO},evil.example",
               f"http://user@{PROTO}", "http://*.example.test", "http://'self'", f"http://{PROTO}:70000", "http://[fe80::1%25eth0]",
               f"http://{PROTO}:80;x")
        for flag in ("--prototype-url", "--public-url"):
            for value in bad:
                with self.subTest(flag=flag, value=value):
                    self.assertIn(flag, self.blocked("--port", str(port), flag, value))
        server, page, found = self.start("--prototype-url", f"http://{PROTO}:{port + 2}", mode="port", port=port)   # the control


# --------------------------------------------------------------------------- service workers


class ServiceWorkerTests(OriginsCase):
    def assert_refused_worker(self, port: int, method: str, path: str, host: str | None = None) -> None:
        headers = {**WORKER, **({"Host": host} if host else {})}
        reply = http_request("127.0.0.1", port, method, path, headers)
        self.assertEqual(reply.status, 403, (port, method, path, reply.text[:200]))
        if method != "HEAD":
            self.assertEqual(reply.data.get("code"), "service_worker_refused", reply.text)
        self.assertNotIn(MARK, reply.text)
        self.assertEqual(reply.set_cookie, "")

    def setUp(self) -> None:
        super().setUp()
        write(self.root / "demo" / "sw.js", f"// {MARK}\n")

    def test_the_prototype_origin_registers_no_service_worker(self) -> None:
        for mode in ("page", "port", "two"):
            with self.subTest(mode=mode):
                server, page, found = self.start(mode=mode)
                for method, path in (("GET", "/demo/sw.js"), ("HEAD", "/demo/sw.js"), ("GET", "/demo/prototype.js"), ("GET", "/"),
                                     ("GET", "/_api/v1/session"), ("GET", "/_handoff/begin"), ("GET", "/_login"), ("POST", "/_api/v1/login"),
                                     ("PUT", "/demo/sw.js"), ("OPTIONS", "/demo/sw.js")):
                    with self.subTest(method=method, path=path):
                        self.assert_refused_worker(found, method, path)
                empty = http_request("127.0.0.1", found, "GET", "/demo/sw.js", {"Service-Worker": ""})
                self.assertEqual(empty.status, 403, empty.text)
                self.assertIn(MARK, self.proto_get(found, "/demo/sw.js").text)   # the control: the file itself is served
                server.stop()

    def test_single_origin_refuses_it_on_the_page_routes_too(self) -> None:
        for mode in ("page", "port"):
            with self.subTest(mode=mode):
                server = self.serve("--single-origin", mode=mode)
                for method, path in (("GET", "/demo/sw.js"), ("GET", "/"), ("GET", "/api/status"), ("GET", "/healthz"), ("GET", "/_login"),
                                     ("GET", "/assets/app.js"), ("POST", "/api/approve"), ("POST", "/_api/v1/login")):   # the methods the socket of the page serves
                    with self.subTest(method=method, path=path):
                        self.assert_refused_worker(server.port, method, path)
                page = server.browser()
                self.assertIn(MARK, page.get("/demo/sw.js").text)
                self.assertEqual(page.get("/api/status").status, 200)
                server.stop()

    def engine_server(self, **settings):
        users = descend(self.project, ".skillsync/prototype/users.json")
        config = ps.ServerConfig(str(self.root), str(users), port=0, git_checks=False, source_writes=False, recover=False,
                                 log=io.StringIO(), **settings)
        server = ps.make_server(config, check=False)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_the_standalone_engine_and_its_show_refuse_it(self) -> None:
        for settings in ({}, {"demo": True}):
            with self.subTest(settings=settings):
                server = self.engine_server(**settings)
                for method in ("GET", "HEAD", "POST", "PUT", "OPTIONS"):
                    self.assert_refused_worker(server.port, method, "/demo/sw.js")
                self.assertIn(MARK, http_request("127.0.0.1", server.port, "GET", "/demo/prototype.js").text)

    def test_local_only_of_the_standalone_engine_covers_every_method(self) -> None:
        server = self.engine_server(local_only=True)
        with mock.patch.object(ps, "link_is_loopback_peer", lambda address: False):
            for method in ("GET", "POST", "PUT", "OPTIONS", "DELETE", "PATCH"):
                with self.subTest(method=method):
                    reply = http_request("127.0.0.1", server.port, method, "/demo/index.html")
                    self.assertEqual((reply.status, reply.data.get("code")), (403, "local_only"), reply.text)
        self.assertEqual(http_request("127.0.0.1", server.port, "PUT", "/demo/index.html").status, 405)   # the control

    def test_the_default_port_of_the_page_is_never_picked_for_the_prototypes(self) -> None:
        # a test never binds 5140, so the choice itself is asked: from 5140 on, and from a taken range back around to it
        for base in (5140, 5139, 5199):
            with self.subTest(base=base):
                found = dh.prototype_port_candidates(None, base, set())
                self.assertNotIn(5140, found)
                self.assertNotIn(5141, found)
                self.assertTrue({5139, 5143} <= set(found), "the neighbours stay candidates")


# --------------------------------------------------------------------------- tabs of other worktrees, and the sockets that close


class OtherTabTests(OriginsCase):
    def add_worktree(self, name: str, prototype_port: int) -> Path:
        """A worktree the way git lays it out, with a trace of work (a plan in progress) and its own `prototype.port`."""
        tree = self.tmp / name
        entry = descend(self.project, f".git/worktrees/{name}")
        write(entry / "HEAD", f"ref: refs/heads/{name}\n")
        write(entry / "commondir", "../..\n")
        write(entry / "gitdir", f"{tree / '.git'}\n")
        self.build_files(tree, prototype_port)
        write(tree / ".git", f"gitdir: {entry}\n")
        write(descend(self.project, f".git/refs/heads/{name}"), "b" * 40 + "\n")
        return tree

    def build_files(self, tree: Path, prototype_port: int) -> None:
        write(tree / ".github" / ".skillsync.yaml", f"skills: [dashboard]\ndelivery:\n  mode: direct\n  base: main\nprototype:\n  port: {prototype_port}\n")
        for rel in ("docs/plans/2026-01-10-demo-etap-1.md", "docs/specs/demo-etap-1.md", ".skillsync/runs/2026-01-10-demo-etap-1/checkpoints.md"):
            write(descend(tree, rel), descend(self.project, rel).read_text(encoding="utf-8"))

    def test_another_tab_does_not_take_the_origin_of_this_server_for_its_prototype_server(self) -> None:
        port = page_port_with_free_neighbours()
        mine = port + 2
        theirs = free_port(skip=(port, mine))
        write(self.project / ".git" / "config", "[core]\n\trepositoryformatversion = 0\n")
        self.add_worktree("same-port", mine)
        self.add_worktree("own-port", theirs)
        # a project server needs no `--layer` flag beside other settings: the layer comes from the config of the isolated machine
        source = json.dumps(str(CLI.parent))
        write(Path(self.env["SKILLSYNC_CONFIG"]), f"layers:\n  - id: core\n    kind: core\n    source: {source}\n    subdir: library\n"
                                                  f"    path: {source}\nlayer_rules:\n  - match: ['**']\n    use: [core]\n")
        with hold(theirs), mock.patch.object(unified_helpers, "LAYER", []):
            server, page, found = self.start("--prototype-port", str(mine), port=port)
            tabs = page.get("/api/tabs").data["tabs"]
            seen = {}
            for tab in tabs:
                data = page.get(f"/api/status?tab={tab['id']}").data
                seen[data["prototype"]["port"], tab["id"] == "main"] = data["prototype"]["up"]
            self.assertEqual(seen, {(mine, True): True, (mine, False): False, (theirs, False): True}, tabs)


class CloseTests(OriginsCase):
    def test_closing_the_prototype_socket_keeps_the_login_link_of_the_page(self) -> None:
        for kind, keeps in ((dh.PrototypeOriginServer, True), (ps.PrototypeHTTPServer, False)):   # the second: the control, the engine alone
            with self.subTest(server=kind.__name__):
                run = self.tmp / f"run-{kind.__name__}"
                config = ps.ServerConfig(str(self.root), str(descend(self.project, ".skillsync/prototype/users.json")), git_checks=False,
                                         source_writes=False, recover=False, log=io.StringIO(), runtime_dir=str(run))
                app = ps.PrototypeApp(config)
                app.enable_login_link(SimpleNamespace(server_address=("127.0.0.1", 5199), port=5199))
                self.assertEqual(len(list(run.glob("server-*.json"))), 1, app.warnings)
                kind(("127.0.0.1", 0), ps.PrototypeRequestHandler, app, origin_kind="proto").server_close()
                self.assertEqual(len(list(run.glob("server-*.json"))), 1 if keeps else 0)
                self.assertEqual(app.link_dir is not None, keeps)
                app.close_link()


# --------------------------------------------------------------------------- cookies, and a script of the prototype origin


class CookieTests(OriginsCase):
    def who(self, browser: Browser):
        return browser.get("/_api/v1/session").data.get("user")

    def test_each_origin_has_its_own_cookie_and_neither_works_on_the_other(self) -> None:
        server, page, found = self.start()
        self.assertEqual(page.login().status, 200)
        proto, _code = self.handoff(page, found)
        page_name, proto_name = page.cookie.partition("=")[0], proto.cookie.partition("=")[0]
        self.assertRegex(page_name, r"^skillsync_page_[0-9a-f]{8}$")
        self.assertRegex(proto_name, r"^skillsync_proto_[0-9a-f]{8}$")
        self.assertNotEqual(page.token, proto.token)
        self.assertEqual(self.who(page)["login"], APPROVER)
        self.assertEqual(self.who(proto)["login"], APPROVER)
        self.assertIn("approver", self.who(page)["roles"])
        self.assertNotIn("approver", self.who(proto)["roles"], "a handed session never consents")
        body = self.cp_body(page)
        before = self.snapshot()
        # a browser sends both cookies to both ports; a script may also rename them
        cases = (("page cookie on the prototypes", found, page.cookie, page.csrf),
                 ("page token under the prototype name", found, f"{proto_name}={page.token}", page.csrf),
                 ("prototype cookie on the page", server.port, proto.cookie, proto.csrf),
                 ("prototype token under the page name", server.port, f"{page_name}={proto.token}", proto.csrf))
        for label, port, cookie, csrf in cases:
            with self.subTest(label):
                other = Browser(port)
                other.cookie, other.csrf = cookie, csrf
                self.assertIsNone(self.who(other))
                if port == server.port:
                    reply = other.post("/api/approve", body)
                    self.assertEqual(reply.status, 401, reply.text)
                    issued = other.post("/_api/v1/handoff", {"challenge": "0" * 64})
                    self.assertEqual(issued.status, 401, issued.text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(page.post("/api/approve", body).status, 200)   # the control

    def test_logout_on_the_page_ends_the_handed_session(self) -> None:
        server, page, found = self.start()
        self.assertEqual(page.login().status, 200)
        proto, _code = self.handoff(page, found)
        self.assertIsNotNone(self.who(proto))
        self.assertEqual(page.post("/_api/v1/logout").status, 204)
        self.assertIsNone(self.who(proto))


class PrototypeScriptTests(OriginsCase):
    """What a script served by the prototype origin can send to the page: the cookie travels (same site), the rest must refuse it."""

    def attempts(self, page: Browser, port: int):
        evil = origin(port)
        full = page.post_headers(Origin=evil, Sec_Fetch_Site="same-site")
        yield "simple form, no custom header", {"Host": page.host, "Cookie": page.cookie, "Origin": evil, "Sec-Fetch-Site": "same-site",
                                                 "Content-Type": "text/plain"}
        yield "no-cors text/plain", {"Host": page.host, "Cookie": page.cookie, "Origin": evil, "Content-Type": "text/plain"}
        yield "every header and a stolen CSRF token", full
        yield "only the Origin is foreign", page.post_headers(Origin=evil)
        yield "only Sec-Fetch-Site says same-site", page.post_headers(Sec_Fetch_Site="same-site")
        yield "Origin null", page.post_headers(Origin="null", Sec_Fetch_Site="cross-site")

    def test_neither_consent_nor_handover_code_nor_session_change(self) -> None:
        server, page, found = self.start()
        self.assertEqual(page.login().status, 200)
        body = self.cp_body(page)
        challenge = re.search(r"challenge=([0-9a-f]{64})", Browser(found).get("/_handoff/begin").header("Location")).group(1)
        before = self.snapshot()
        for label, headers in self.attempts(page, found):
            with self.subTest(label):
                for path in ("/api/approve", "/api/plan-status", "/api/feedback-approve"):
                    reply = page.post(path, body, headers)
                    self.assertEqual((reply.status, reply.data.get("code")), (403, "consent_refused"), (path, reply.text))
                own_origin = headers.get("Origin") == page.origin   # no browser sends this with a foreign mark: only the strict routes check it
                routes = [("/_api/v1/handoff", {"challenge": challenge})]
                if not own_origin:
                    routes += [("/_api/v1/logout", {}), ("/_api/v1/password", {"current": APPROVER_PASSWORD, "new": APPROVER_PASSWORD + "-nowe"})]
                for path, sent in routes:
                    reply = page.post(path, sent, headers)
                    self.assertEqual(reply.status, 403, (path, reply.text))
                    self.assertNotIn("code", reply.data, path)
                    self.assertEqual(reply.set_cookie, "", path)
                login = page.post("/_api/v1/login", {"login": COMMENTER, "password": COMMENTER_PASSWORD}, {**headers, "Cookie": None})
                if not own_origin:   # login has no session to ride on: its guard is Origin and Content-Type
                    self.assertEqual((login.status, login.set_cookie), (403, ""), login.text)
        self.assertEqual(page.get("/_api/v1/handoff").status, 405)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.rows(), [])
        self.assertEqual(page.get("/_api/v1/session").data["user"]["login"], APPROVER, "the session is still there")
        issued = page.post("/_api/v1/handoff", {"challenge": challenge})   # the controls: the page itself
        self.assertEqual(issued.status, 200, issued.text)
        self.assertEqual(page.post("/api/approve", body).status, 200)

    def test_an_anonymous_request_gets_no_code(self) -> None:
        server, page, found = self.start()
        challenge = re.search(r"challenge=([0-9a-f]{64})", Browser(found).get("/_handoff/begin").header("Location")).group(1)
        reply = page.post("/_api/v1/handoff", {"challenge": challenge})
        self.assertEqual(reply.status, 401, reply.text)
        self.assertNotIn("code", reply.data)


# --------------------------------------------------------------------------- --single-origin


class SingleOriginTests(OriginsCase):
    def start_single(self, mode: str = "page", port: int | None = None) -> tuple:
        server = self.serve("--single-origin", mode=mode, port=port)
        return server, server.browser()

    def test_one_socket_serves_both_with_a_banner_and_without_consents(self) -> None:
        for mode in ("page", "port"):
            with self.subTest(mode=mode):
                port = page_port_with_free_neighbours()
                server, page = self.start_single(mode, port)
                self.assertTrue(bindable(port + 2), "no second socket")
                self.assertIn(MARK, page.get("/demo/index.html").text)
                for path in ("/", "/_login"):
                    reply = page.get(path)
                    self.assertEqual(reply.status, 200, path)
                    self.assertEqual(reply.text.count("data-single-origin"), 1, path)
                    self.assertIn("--single-origin", reply.text, path)
                    found = directives(reply.header("Content-Security-Policy"))
                    self.assertEqual((found.get("frame-ancestors"), reply.header("X-Frame-Options")), (["'none'"], "DENY"), path)
                self.assertEqual(directives(page.get("/").header("Content-Security-Policy"))["frame-src"], ["'self'"])
                status = page.status()
                self.assertEqual((status.get("singleOrigin"), status["approvals"]["enabled"], status["approvals"]["actions"]), (True, False, []))
                refused = page.login(APPROVER, APPROVER_PASSWORD)
                self.assertGreaterEqual(refused.status, 400, refused.text)
                self.assertEqual(refused.set_cookie, "")
                self.assertEqual(page.login(COMMENTER, COMMENTER_PASSWORD).status, 200)   # the control: others sign in
                before = self.snapshot()
                for path in ("/api/approve", "/api/plan-status", "/api/feedback-approve"):
                    reply = page.post(path, {"kind": "cp", "path": RUN_ID, "sha": "abc1234def5678"})
                    self.assertEqual((reply.status, reply.data.get("code")), (403, "approvals_disabled"), (path, reply.text))
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.rows(), [])
                server.stop()

    def test_the_banner_and_the_field_are_absent_without_the_flag(self) -> None:
        server, page, found = self.start()
        for path in ("/", "/_login"):
            self.assertNotIn("data-single-origin", page.get(path).text, path)
        self.assertNotIn("singleOrigin", page.status())

    def test_the_guards_hold_on_the_paths_given_to_the_prototype_engine(self) -> None:
        write(self.root / "demo" / ".env", HIDDEN)
        server, page = self.start_single()
        for path in ("/demo/.env", "/demo/comments.json", "/demo/%2e%2e/%2e%2e/outside.txt", "/.skillsync/prototype/users.json"):
            reply = page.get(path)
            self.assertEqual(reply.status, 404, (path, reply.text))
            self.assertNotIn(HIDDEN, reply.text)
        for host in ("evil.example", ""):
            reply = page.get("/demo/index.html", {"Host": host})
            self.assertEqual((reply.status, reply.data.get("code")), (403, "host_refused"), reply.text)
            self.assertNotIn(MARK, reply.text)
        accept = page.post("/_api/v1/project/accept", {})
        self.assertEqual(accept.status, 401, accept.text)
        self.assertIn(MARK, page.get("/demo/index.html").text)

    def test_flags_that_need_two_origins_refuse_the_start(self) -> None:
        port = page_port_with_free_neighbours()
        other = free_port(skip=(port,))
        for args in (("--allow-remote-approvals",), ("--prototype-port", str(other)), ("--prototype-url", f"http://{PROTO}:{other}")):
            for label, base, kwargs in (("page", (), {"page_port": port}), ("port", ("--port", str(port)), {})):
                with self.subTest(args=args, mode=label):
                    reason = self.blocked(*base, "--single-origin", *args, **kwargs)
                    self.assertIn("--single-origin", reason)
                    self.assertIn(args[0], reason)
        reason = self.blocked("--single-origin", "--read-port", str(port), "--approve-port", str(other))
        self.assertIn("--read-port", reason)
        self.assertIn("--approve-port", reason)


if __name__ == "__main__":
    unittest.main()
