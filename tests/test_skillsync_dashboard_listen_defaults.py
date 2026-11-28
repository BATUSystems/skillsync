"""180-B: the default listen address of `dashboard serve` (contract U-2: ONE `page` socket on every interface, reads for all, consents through a session; the explicit
two-socket mode of `--read-port/--approve-port` as before), the
`dashboard:` setting (flag > manifest > machine config), the owner of a busy port and the Host rule for the machine's own name.

No test here listens on a real port: `make_server` is replaced, the sockets of the process tests are in `test_skillsync_dashboard_ports`."""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_listen as listen  # noqa: E402
from skillsync_workflow import dashboard_ports as ports  # noqa: E402
from skillsync_workflow import dashboard_token  # noqa: E402
from layers_helpers import guard_real_state, home_env  # noqa: E402
from tests.test_skillsync_dashboard_server import free_port, make_project  # noqa: E402

SECRET_LOOKING = "10.99.99.99-not-a-bind-but-a-value"


def args(**kwargs):
    values = dict(path=".", layers=None, plan=None, session_url=None, approve_host=[], read_host=[], poll=1.0, verbose=False, prototype_port=None,
                  port=None, read_port=None, approve_port=None, read_bind=None, approve_bind=None, allow_proxy_headers=False, host=None,
                  no_approvals=False)
    values.update(kwargs)
    return SimpleNamespace(**values)


class Resolve(unittest.TestCase):
    def resolve(self, settings=None, **kwargs):
        return ports.resolve(args(**kwargs), lambda port: None, settings)

    def test_precedence_flag_over_host_over_setting_over_default(self) -> None:
        settings = {"read_bind": "127.0.0.1", "approve_bind": "10.0.0.5"}
        mode = self.resolve(settings)   # contract U-2: the one `page` socket takes the read bind; there is no consents socket for `approve_bind`
        self.assertEqual((mode["page"]["bind"], mode["read"], mode["approve"]), ("127.0.0.1", None, None))
        self.assertFalse(mode["read_default"])
        mode = self.resolve(settings, read_port=5151, approve_port=5152)
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("127.0.0.1", "10.0.0.5"))
        mode = self.resolve(settings, read_port=5151, approve_port=5152, host="192.168.1.2")   # --host: the read socket only
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("192.168.1.2", "10.0.0.5"))
        mode = self.resolve(settings, read_port=5151, approve_port=5152, host="192.168.1.2", read_bind="0.0.0.0", approve_bind="127.0.0.1")
        self.assertEqual((mode["read"]["bind"], mode["approve"]["bind"]), ("0.0.0.0", "127.0.0.1"))

    def test_the_consent_socket_is_never_beyond_loopback_without_a_flag_or_a_setting(self) -> None:
        for kwargs in ({}, {"read_port": 5151, "approve_port": 5152}, {"approve_port": 5152}, {"read_bind": "0.0.0.0"}, {"no_approvals": True}):
            with self.subTest(kwargs=kwargs):
                mode = self.resolve(**kwargs)
                if mode["approve"]:
                    self.assertTrue(ports.is_loopback_bind(mode["approve"]["bind"]), mode)


class PortTaken(unittest.TestCase):
    def test_something_listening_is_taken_whatever_the_bind_and_nothing_is_free(self) -> None:
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen(16)   # the probes are never accepted: a backlog of 1 refuses the second one on Windows
            port = holder.getsockname()[1]
            self.assertTrue(ports.port_taken("127.0.0.1", port))
            self.assertTrue(ports.port_taken("0.0.0.0", port))   # a wildcard bind is probed through loopback
        self.assertFalse(ports.port_taken("0.0.0.0", port))
        self.assertFalse(ports.port_taken("127.0.0.1", 0))

    def test_serve_refuses_a_port_that_answers_before_it_binds(self) -> None:
        with mock.patch.object(ports, "port_taken", return_value=True), mock.patch.object(srv, "make_server", side_effect=AssertionError("bound")):
            tmp = Path(tempfile.mkdtemp(prefix="dashboard-taken-"))
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            project = make_project(tmp)
            manifest = project / srv.MANIFEST
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
            out = io.StringIO()
            with mock.patch("sys.stdout", new=out), mock.patch.dict(os.environ, {**home_env(tmp), "SKILLSYNC_CONFIG": str(tmp / "c.yaml")}):
                srv.serve_command(args(path=str(project), layers=[f"core={ROOT / 'library'}"]))
        answer = json.loads(out.getvalue())
        self.assertEqual(answer["status"], "BLOCKED")
        for hint in ("--read-bind", "--port N"):   # contract U-2: the ways out of the default (one socket) mode
            self.assertIn(hint, answer["reason"])


class Setting(unittest.TestCase):
    def test_local_only_narrows_both_and_explicit_keys_win_over_the_shortcut(self) -> None:
        self.assertEqual(listen.settings({"dashboard": {"local_only": True}}, machine={}), {"read_bind": "127.0.0.1", "approve_bind": "127.0.0.1"})
        self.assertEqual(listen.settings({"dashboard": {"local_only": True, "read_bind": "localhost"}}, machine={}),
                         {"read_bind": "localhost", "approve_bind": "127.0.0.1"})
        self.assertEqual(listen.settings({}, machine={"local_only": True}), {"read_bind": "127.0.0.1", "approve_bind": "127.0.0.1"})

    def test_manifest_over_machine_and_false_is_no_preference(self) -> None:
        machine = {"local_only": True}
        self.assertEqual(listen.settings({"dashboard": {"read_bind": "127.0.0.1"}}, machine={"read_bind": "0.0.0.0"}),
                         {"read_bind": "127.0.0.1", "approve_bind": None})
        self.assertEqual(listen.settings({}, machine={"read_bind": "0.0.0.0", "approve_bind": "0.0.0.0"}),
                         {"read_bind": "0.0.0.0", "approve_bind": "0.0.0.0"})   # the machine config may widen
        self.assertEqual(listen.settings({"dashboard": {"local_only": False}}, machine=machine), {"read_bind": "127.0.0.1", "approve_bind": "127.0.0.1"})
        self.assertEqual(listen.settings({}, machine={}), {"read_bind": None, "approve_bind": None})

    def test_a_manifest_can_only_narrow_never_widen_the_machine_config(self) -> None:
        for section in ({"read_bind": "0.0.0.0"}, {"approve_bind": "0.0.0.0"}, {"approve_bind": "192.168.1.5"}, {"read_bind": "::"}):
            with self.subTest(section=section):
                self.assertTrue(listen.validate_dashboard(section, "dashboard", narrow_only=True)[0])
                with self.assertRaises(ValueError) as caught:
                    listen.settings({"dashboard": section}, machine={"local_only": True})
                self.assertIn("only narrow", str(caught.exception))
        self.assertEqual(listen.validate_dashboard({"read_bind": "::1", "approve_bind": "127.0.0.5"}, "dashboard", narrow_only=True)[0], [])

    def test_an_unreadable_machine_config_is_an_error_not_no_setting(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="dashboard-config-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        config = tmp / "config.yaml"
        with mock.patch.dict(os.environ, {"SKILLSYNC_CONFIG": str(config)}):
            self.assertIsNone(listen.machine_section())   # no file: no setting
            for text in ("dashboard: [unclosed", "- just\n- a list\n"):
                config.write_text(text, encoding="utf-8")
                with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                    listen.machine_section()
                self.assertNotIn("unclosed", str(caught.exception))
            config.write_text("", encoding="utf-8")
            self.assertIsNone(listen.machine_section())

    def test_bad_values_name_the_key_and_never_the_value(self) -> None:
        for section in ({"local_only": SECRET_LOOKING}, {"read_bind": SECRET_LOOKING}, {"approve_bind": ""}, {"read_bind": 5}, "text"):
            with self.subTest(section=section):
                errors, _ = listen.validate_dashboard(section)
                self.assertTrue(errors)
                self.assertTrue(all(SECRET_LOOKING not in message for message in errors), errors)
                with self.assertRaises(ValueError) as caught:
                    listen.settings({"dashboard": section}, machine={})
                self.assertNotIn(SECRET_LOOKING, str(caught.exception))

    def test_unknown_key_is_a_warning_of_doctor_and_blocks_serve(self) -> None:
        errors, warnings = listen.validate_dashboard({"local_only": True, "bind_all": True})
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("dashboard.bind_all: unknown key", warnings[0])
        self.assertIn("bind_all", listen.blocking_unknown_keys({"dashboard": {"bind_all": 1}}, machine={}))
        self.assertIsNone(listen.blocking_unknown_keys({"dashboard": {"local_only": True}}, machine={}))


class ServeCommand(unittest.TestCase):
    """`serve_command` without port flags, in this process, with `make_server` replaced (nothing listens)."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dashboard-listen-"))
        guard_real_state(self)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = make_project(self.tmp)
        self.manifest = self.project / srv.MANIFEST
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        self.write_manifest("")
        self.config = self.tmp / "machine.yaml"
        patch = mock.patch.dict(os.environ, {"SKILLSYNC_CONFIG": str(self.config), **home_env(self.tmp / "state"),
                                              "XDG_CACHE_HOME": str(self.tmp / "cache")})
        patch.start()
        self.addCleanup(patch.stop)
        taken = mock.patch.object(ports, "port_taken", return_value=False)   # the probe would touch the real pool ports
        taken.start()
        self.addCleanup(taken.stop)
        patcher = mock.patch.object(ports, "DEFAULT_READ_PORT", 5162)   # the pool of the station, never 5140
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_manifest(self, extra: str) -> None:
        self.manifest.write_text("skills: [dashboard]\n" + extra, encoding="utf-8")

    def run_serve(self, stop_at=1, **kwargs):
        """`serve_command` until the `stop_at`-th socket is made (that one raises "busy", so nothing is ever served)."""
        calls = []

        def fake(board, bind, port, verbose=False, role=ports.ROLE_BOTH, **rest):
            calls.append((role, bind, port))
            if len(calls) >= stop_at:
                raise OSError(98, "busy")
            return mock.MagicMock()

        out = io.StringIO()
        run = args(path=str(self.project), layers=[f"core={ROOT / 'library'}"], **kwargs)
        with mock.patch.object(srv, "make_server", fake), mock.patch("sys.stdout", new=out):
            srv.serve_command(run)
        return calls, json.loads(out.getvalue())

    def test_no_flags_one_page_socket_on_every_interface(self) -> None:
        calls, _ = self.run_serve()
        self.assertEqual(calls, [("page", "0.0.0.0", 5162)])

    def test_no_approvals_starts_the_same_one_socket(self) -> None:
        calls, _ = self.run_serve(no_approvals=True)
        self.assertEqual(calls, [("page", "0.0.0.0", 5162)])

    def test_local_only_in_the_manifest_or_the_machine_config_narrows_the_socket(self) -> None:
        self.write_manifest("dashboard:\n  local_only: true\n")
        calls, _ = self.run_serve()
        self.assertEqual(calls, [("page", "127.0.0.1", 5162)])
        calls, _ = self.run_serve(stop_at=2, read_port=5164, approve_port=5165)
        self.assertEqual(calls, [("read", "127.0.0.1", 5164), ("approve", "127.0.0.1", 5165)])
        self.write_manifest("")
        self.config.write_text("dashboard:\n  local_only: true\n", encoding="utf-8")
        calls, _ = self.run_serve()
        self.assertEqual(calls, [("page", "127.0.0.1", 5162)])

    def test_flag_over_manifest_over_machine_config(self) -> None:
        self.config.write_text("dashboard:\n  read_bind: 0.0.0.0\n  approve_bind: 0.0.0.0\n", encoding="utf-8")
        self.write_manifest("dashboard:\n  read_bind: 127.0.0.1\n")
        calls, _ = self.run_serve()
        self.assertEqual(calls, [("page", "127.0.0.1", 5162)])
        calls, _ = self.run_serve(read_bind="0.0.0.0")
        self.assertEqual(calls, [("page", "0.0.0.0", 5162)])
        calls, _ = self.run_serve(stop_at=2, read_port=5164, approve_port=5165)
        self.assertEqual(calls, [("read", "127.0.0.1", 5164), ("approve", "0.0.0.0", 5165)])
        calls, _ = self.run_serve(stop_at=2, read_port=5164, approve_port=5165, read_bind="0.0.0.0", approve_bind="127.0.0.1")
        self.assertEqual(calls, [("read", "0.0.0.0", 5164), ("approve", "127.0.0.1", 5165)])
        calls, answer = self.run_serve(approve_bind="127.0.0.1")   # contract U-2: no consents socket in the default mode: a clear refusal
        self.assertEqual((calls, answer["status"]), ([], "BLOCKED"))
        self.assertIn("--approve-port", answer["reason"])

    def stderr_of_serve(self, **kwargs) -> str:
        err = io.StringIO()
        with mock.patch("sys.stderr", new=err):
            self.run_serve(**kwargs)
        return err.getvalue()

    def test_approve_bind_in_the_default_mode_is_named_not_silently_ignored(self) -> None:
        self.write_manifest("dashboard:\n  approve_bind: 127.0.0.1\n")
        text = self.stderr_of_serve()
        self.assertIn("approve_bind (dashboard (manifest))", text)
        self.assertIn("--approve-port", text)
        self.config.write_text("dashboard:\n  approve_bind: 0.0.0.0\n", encoding="utf-8")
        self.write_manifest("")
        self.assertIn("approve_bind (dashboard (machine config))", self.stderr_of_serve())

    def test_no_note_for_local_only_or_for_the_two_socket_mode(self) -> None:
        self.write_manifest("dashboard:\n  local_only: true\n")
        self.assertNotIn("approve_bind", self.stderr_of_serve())
        self.write_manifest("dashboard:\n  approve_bind: 127.0.0.1\n")
        self.assertNotIn("nic nie zmienia", self.stderr_of_serve(stop_at=2, read_port=5164, approve_port=5165))

    def test_the_owner_of_a_prototype_origin_is_named(self) -> None:
        dashboard_token.write_state(self.tmp / "other-project", 5163, True, "127.0.0.1", role="page", prototype_port=5162)
        with mock.patch.object(dashboard_token, "_windows", return_value=False):
            self.assertEqual(dashboard_token.port_owner(5162), "projekt other-project (prototypy)")
            self.assertEqual(dashboard_token.port_owner(5163), "projekt other-project (strona)")
            _calls, answer = self.run_serve()
        self.assertIn("port 5162 trzyma projekt other-project (prototypy)", answer["reason"])

    def test_a_manifest_that_widens_the_machine_config_blocks_serve(self) -> None:
        self.config.write_text("dashboard:\n  local_only: true\n", encoding="utf-8")
        self.write_manifest("dashboard:\n  approve_bind: 0.0.0.0\n")
        calls, answer = self.run_serve()
        self.assertEqual((calls, answer["status"]), ([], "BLOCKED"))
        self.assertIn("approve_bind", answer["reason"])
        self.config.write_text("dashboard: [broken", encoding="utf-8")
        self.write_manifest("")
        calls, answer = self.run_serve()
        self.assertEqual((calls, answer["status"]), ([], "BLOCKED"))

    def test_unknown_key_and_bad_value_block_without_the_value(self) -> None:
        self.write_manifest("dashboard:\n  bind_all: true\n")
        calls, answer = self.run_serve()
        self.assertEqual((calls, answer["status"]), ([], "BLOCKED"))
        self.assertIn("bind_all", answer["reason"])
        self.write_manifest(f"dashboard:\n  read_bind: {SECRET_LOOKING}\n")
        calls, answer = self.run_serve()
        self.assertEqual((calls, answer["status"]), ([], "BLOCKED"))
        self.assertIn("read_bind", answer["reason"])
        self.assertNotIn(SECRET_LOOKING, answer["reason"])

    def test_an_explicit_port_keeps_the_single_port_mode(self) -> None:
        self.write_manifest("dashboard:\n  bind_all: true\n")   # not even read in the single-port mode
        calls, _ = self.run_serve(port=5164)
        self.assertEqual(calls, [("both", "127.0.0.1", 5164)])

    def test_a_busy_port_names_the_flags_and_the_owner_from_the_state_files(self) -> None:
        other = self.tmp / "other-project"
        other.mkdir()
        calls, answer = self.run_serve()
        self.assertEqual(answer["status"], "BLOCKED")
        self.assertIn("--read-bind", answer["reason"])
        self.assertIn("--port N", answer["reason"])
        self.assertIn("port 5162 zajęty przez inny proces", answer["reason"])   # nobody of ours holds it
        dashboard_token.write_state(other, 5162, True, "127.0.0.1", role="page")
        with mock.patch.object(dashboard_token, "_windows", return_value=False):   # on Windows an owner must also answer /healthz (own test)
            calls, answer = self.run_serve()
        self.assertIn("port 5162 trzyma projekt other-project (strona)", answer["reason"])
        self.assertNotIn(str(other), answer["reason"])
        dashboard_token.write_state(other, 5165, True, "127.0.0.1", role="approve", peer=5164)
        with mock.patch.object(dashboard_token, "_windows", return_value=False):
            calls, answer = self.run_serve(stop_at=2, read_port=5164, approve_port=5165)   # the second socket (consents) fails: the state file names its owner
        self.assertIn("port 5165 trzyma projekt other-project (zgody)", answer["reason"])
        with mock.patch.object(dashboard_token, "_windows", return_value=False):
            self.assertEqual(dashboard_token.port_owner(5165), "projekt other-project (zgody)")
            self.assertIsNone(dashboard_token.port_owner(5199))


class HostRefusedMessage(unittest.TestCase):
    """180-N: the 403 text names the flag of THIS socket and the (cleaned) Host of the request."""

    def test_each_socket_names_its_own_flag_and_the_host(self) -> None:
        read = srv.host_refused_message(ports.ROLE_READ, "Evil.Example:5140")
        approve = srv.host_refused_message(ports.ROLE_APPROVE, "evil.example")
        both = srv.host_refused_message(ports.ROLE_BOTH, "evil.example")
        self.assertIn("--read-host", read)
        self.assertNotIn("--approve-host", read)
        self.assertIn("(nagłówek Host: evil.example)", read)   # lower case, no port
        self.assertIn("--approve-host", approve)
        self.assertNotIn("--read-host", approve)
        self.assertIn("--approve-host", both)
        self.assertIn("--read-host", both)

    def test_the_host_is_cleaned_before_it_goes_into_the_answer(self) -> None:
        for raw in ("<script>alert(1)</script>", 'a"b', "a\r\nSet-Cookie: x=1", "a\x00b", "ev\u0131l.example", "a b"):
            with self.subTest(raw=raw):
                text = srv.host_refused_message(ports.ROLE_READ, raw)
                shown = text.split("(nagłówek Host: ")[1][:-2]
                self.assertRegex(shown, r"^[A-Za-z0-9._:?-]+$")
        self.assertIn("Host: (brak)", srv.host_refused_message(ports.ROLE_READ, ""))
        self.assertIn("Host: (brak)", srv.host_refused_message(ports.ROLE_READ, None))
        long = srv.host_refused_message(ports.ROLE_READ, "a" * 500 + ".example")
        self.assertLessEqual(len(long.split("Host: ")[1]), 70)
        self.assertIn("...", long)


class SmallRules(unittest.TestCase):
    def test_the_sentence_about_the_changed_default_ends_with_1_10(self) -> None:
        self.assertEqual(srv._version_tuple("1.8.0"), (1, 8))
        self.assertLess(srv._version_tuple("1.9.3"), ports.LISTEN_NOTE_UNTIL)
        self.assertFalse(srv._version_tuple("1.10.0") < ports.LISTEN_NOTE_UNTIL)
        self.assertFalse(srv._version_tuple("2.0.0") < ports.LISTEN_NOTE_UNTIL)

    def test_an_empty_bind_flag_is_refused_not_silently_the_default(self) -> None:
        for flag in ("read_bind", "approve_bind"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                ports.resolve(args(**{flag: ""}), lambda port: None)

    def test_a_bind_that_is_not_this_machine_is_not_probed(self) -> None:
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("probed a stranger")):
            self.assertFalse(ports.port_taken("203.0.113.9", 5162))

    def test_only_an_answering_server_is_named_as_the_owner_on_windows(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="dashboard-owner-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.dict(os.environ, home_env(tmp)):
            dashboard_token.write_state(tmp / "proj", 5167, True, "127.0.0.1", role="read", peer=5168)
            with mock.patch.object(dashboard_token, "_windows", return_value=True):
                self.assertIsNone(dashboard_token.port_owner(5167, healthy=lambda *a: False))
                self.assertEqual(dashboard_token.port_owner(5167, healthy=lambda *a: True), "projekt proj (odczyt)")

    def test_lan_without_an_address_of_this_machine_is_blocked(self) -> None:
        blocked = []
        out = io.StringIO()
        with mock.patch.object(srv, "lan_addresses", return_value=["<adres-tej-maszyny>"]), mock.patch("sys.stdout", new=out):
            code = dashboard_token._lan_addresses(SimpleNamespace(json=False), [{"port": 5162, "pid": 1, "role": "read", "bind": "0.0.0.0"}],
                                                  lambda code_, reason: blocked.append(code_) or 2)
        self.assertEqual((code, blocked, out.getvalue()), (2, ["read_not_exposed"], ""))


class Lan(unittest.TestCase):
    """`dashboard url --lan` (the function behind it; the server of a state file is not started)."""

    def run_lan(self, found, as_json=False, addresses=("192.0.2.7", "198.51.100.4")):
        out, err = io.StringIO(), io.StringIO()
        blocked = []

        def refuse(code, reason):
            blocked.append((code, reason))
            return 2

        with mock.patch.object(srv, "lan_addresses", return_value=list(addresses)), mock.patch("sys.stdout", new=out), mock.patch("sys.stderr", new=err):
            code = dashboard_token._lan_addresses(SimpleNamespace(json=as_json), found, refuse)
        return code, out.getvalue(), blocked

    def reader(self, bind, **extra):
        return {"port": 5162, "pid": 1, "role": "read", "bind": bind, "host": "127.0.0.1", **extra}

    def test_lists_the_lan_addresses_of_a_read_socket_on_every_interface(self) -> None:
        code, text, _ = self.run_lan([self.reader("0.0.0.0")])
        self.assertEqual((code, text.split()), (0, ["http://192.0.2.7:5162/", "http://198.51.100.4:5162/"]))
        code, text, _ = self.run_lan([self.reader("0.0.0.0")], as_json=True)
        self.assertEqual(json.loads(text)["lanUrls"], ["http://192.0.2.7:5162/", "http://198.51.100.4:5162/"])

    def test_a_specific_bind_lists_only_that_address(self) -> None:
        code, text, _ = self.run_lan([self.reader("192.0.2.9")])
        self.assertEqual(text.split(), ["http://192.0.2.9:5162/"])

    def test_loopback_unknown_bind_and_missing_server_are_blocked(self) -> None:
        for found, expected in (([self.reader("127.0.0.1")], "read_not_exposed"), ([self.reader(None)], "read_not_exposed"),
                                ([{"port": 5140, "pid": 1, "role": "both", "bind": "0.0.0.0"}], "read_not_exposed"), ([], "server_not_running")):
            with self.subTest(found=found):
                code, text, blocked = self.run_lan(found)
                self.assertEqual((code, blocked[0][0], text), (2, expected, ""))

    def test_the_state_file_of_the_read_socket_keeps_the_real_bind(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="dashboard-lan-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.dict(os.environ, home_env(tmp)):
            path = dashboard_token.write_state(tmp / "proj", 5162, None, "127.0.0.1", role="read", peer=5163, bind="0.0.0.0")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["bind"], "0.0.0.0")
            path = dashboard_token.write_state(tmp / "proj", 5164, None, "127.0.0.1")
            self.assertNotIn("bind", json.loads(path.read_text(encoding="utf-8")))


class Exclusive(unittest.TestCase):
    """D8/K10: the sockets of the two-port mode ask for the address exclusively (Windows); the single-port server does not (unchanged)."""

    def board(self):
        return SimpleNamespace()

    def test_the_two_port_sockets_set_the_option_and_the_single_port_one_does_not(self) -> None:
        asked = []
        real = srv.socket.socket.setsockopt

        class Spy(srv.socket.socket):
            def setsockopt(self, level, option, value, *rest):
                asked.append(option)
                if option == 4242:   # an invented value: the platform would refuse it, the spy only records that it was asked for
                    return None
                return real(self, level, option, value, *rest)

        for role, expected in ((ports.ROLE_READ, True), (ports.ROLE_APPROVE, True), (ports.ROLE_BOTH, False)):
            asked.clear()
            with self.subTest(role=role), mock.patch.object(srv, "exclusive_option", return_value=4242), mock.patch.object(srv.socket, "socket", Spy):
                server = srv.make_server(self.board(), "127.0.0.1", free_port(), role=role)
                server.server_close()
            # the platform may refuse the invented option value; the point is that it is asked for, for exactly these roles
            self.assertEqual(4242 in asked, expected, role)

    def test_a_second_bind_on_the_narrower_address_is_refused_on_windows(self) -> None:
        if os.name != "nt":
            self.assertIsNone(srv.exclusive_option())
            return
        server = srv.make_server(self.board(), "0.0.0.0", free_port(), role=ports.ROLE_READ)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        with self.assertRaises(OSError), __import__("socket").socket() as other:
            other.bind(("127.0.0.1", port))


class OwnName(unittest.TestCase):
    def handler(self, role):
        board = SimpleNamespace(approve_hosts=frozenset(), read_hosts=frozenset())
        return SimpleNamespace(board=board, role=role)

    def test_the_machine_own_name_is_accepted_on_the_read_socket_only(self) -> None:
        with mock.patch.object(ports, "_OWN_NAMES", None), mock.patch("socket.gethostname", return_value="Laptop.Example.Org"):
            self.assertEqual(ports.own_names(), frozenset({"laptop.example.org", "laptop", "laptop.local"}))
            read, approve = self.handler(ports.ROLE_READ), self.handler(ports.ROLE_APPROVE)
            for name in ("laptop", "laptop.local", "laptop.example.org"):
                with self.subTest(name=name):
                    self.assertTrue(srv.Handler._host_allowed(read, name))
                    self.assertFalse(srv.Handler._host_allowed(approve, name))
            self.assertFalse(srv.Handler._host_allowed(read, "evil.example"))
            self.assertFalse(srv.Handler._host_allowed(read, "laptop.evil.example"))

    def test_resolving_the_own_name_never_asks_dns(self) -> None:
        with mock.patch.object(ports, "_OWN_NAMES", None), mock.patch("socket.gethostname", return_value="box"), \
                mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS")), mock.patch("socket.gethostbyname", side_effect=AssertionError("DNS")):
            self.assertIn("box.local", ports.own_names())
            self.assertTrue(srv.Handler._host_allowed(SimpleNamespace(board=SimpleNamespace(approve_hosts=frozenset(), read_hosts=frozenset()),
                                                                      role=ports.ROLE_READ), "box"))


if __name__ == "__main__":
    unittest.main()
