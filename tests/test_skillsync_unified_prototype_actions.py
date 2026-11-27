"""CORE-2000-UNIFIED U-3b: what an anonymous reader of the prototypes sees (K2, decision Q2: pseudonyms, no addresses, no absolute paths) and the
actions on prototypes from the origin of the status page (Q11, K22: accept, unaccept, restore, resolve, delete, project accept):
python3 -m unittest tests.test_skillsync_unified_prototype_actions

A real `skillsync dashboard serve` (and, as the control of the redaction, a real `skillsync prototype serve`) over a temporary project with
accounts, a comment journal, expert questions and a writing marker full of canaries: unique values that must not reach an anonymous reader. Every
canary is also shown to a signed-in reader or sits in an allowed field, so its absence can fail. Every refusal is followed by the same request done
right."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import unified_helpers  # noqa: E402
from serve_start import kill_tree  # noqa: E402
from path_text import flat  # noqa: E402
from tmp_paths import descend  # noqa: E402
from unified_helpers import CLI, LAYER, Browser, UnifiedCase, free_port, http_request, lan_address, self_reachable, write  # noqa: E402

from skillsync_workflow import dashboard_http as dh  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402

PASSWORD = "-".join(("Haslo", "Testowe", "Prototypow", "2026"))
LONG_PASSWORD = "-".join(("Zatwierdzajace", "Haslo", "Prototypow", "77"))
NEW_PASSWORD = "-".join(("Calkiem", "Nowe", "Haslo", "Konta", "88"))

DOMAIN = "kanarek-domena.example"
COMMENTER = f"kom.kanarek@{DOMAIN}"
EXPERT = f"eks.kanarek@{DOMAIN}"
ACCEPTOR = f"acc.kanarek@{DOMAIN}"
DEVELOPER = f"dev.kanarek@{DOMAIN}"
APPROVER = f"app.kanarek@{DOMAIN}"
MUSTCHANGE = f"nowy.kanarek@{DOMAIN}"
PSEUDONYM = {COMMENTER: "pseudo-kom", EXPERT: "pseudo-eks", ACCEPTOR: "pseudo-acc", DEVELOPER: "pseudo-dev", APPROVER: "pseudo-app"}
FORMER = f"dawny.autor@{DOMAIN}"               # not in the users file
FORMER_TWIN = "dawny.autor@kanarek-druga.example"   # the same part before `@`
FULL_NAME = "Imie Nazwiskokanarkowe"           # the display name of the commenter account


def canary(label: str) -> str:
    return "-".join(("kanarek", label, "7319"))


SECRETS = {name: canary(name) for name in ("cid", "importedauthor", "unknownkey", "unknownpayload", "whofree", "origin", "writingby",
                                          "dirpath")}
TEXT = "jawna-tresc-komentarza-5150"
QUESTION_TEXT = "jawne-pytanie-do-eksperta-5151"
ANSWER_TEXT = "jawna-odpowiedz-eksperta-5152"


def section(number: int, text: str) -> str:
    return (f'<section class="screen" id="s{number}">\n  <div class="frame"><h2 data-proto-id="s{number}-h">{text}</h2></div>\n</section>\n')


def index_html(slug: str, first: str = "Pierwszy", second: str = "Drugi") -> str:
    return f'<!doctype html>\n<html data-prototype-id="{slug}">\n<body>\n{section(1, first)}{section(2, second)}</body>\n</html>\n'


def op(number: int, kind: str, by: str, payload: dict, thread: str | None = None, **extra) -> dict:
    found = {"id": "op-" + str(number).zfill(26), "type": kind, "at": "2026-11-05T09:%02d:00.000Z" % number, "by": by,
             "cid": f"{SECRETS['cid']}-{number}", "payload": payload, **extra}
    if thread is not None:
        found["threadId"] = thread
    return found


def create(number: int, thread: str, by: str, text: str = TEXT, **message) -> dict:
    return op(number, "create", by, {"message": {"id": f"m-{number}", "text": text, **message}, "screen": "s1",
                                     "anchor": {"kind": "screen", "x": 0.5, "y": 0.5, "iteration": 1}}, thread)


def write_newer(path: Path, data: bytes) -> None:
    """Writes a file the server caches: its mtime moves even with a coarse clock."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if path.stat().st_mtime_ns <= before:
        os.utime(path, ns=(before + 2_000_000, before + 2_000_000))


def journal_text(slug: str, operations: list) -> str:
    head = '{"format":"skillsync-prototype-comments","version":2,"prototypeId":"%s","operations":[' % slug
    return head + "\n" + ",\n".join(json.dumps(item, separators=(",", ":"), ensure_ascii=False) for item in operations) + "\n]}\n"


class PrototypeCase(UnifiedCase):
    """A project with the prototype `demo` (two screens, threads `th-kom`, `th-dev`) and `other`, and six accounts."""

    ACCOUNTS: tuple = ()
    _users: bytes | None = None

    def setUp(self) -> None:
        super().setUp()
        self.root = descend(self.project, "docs/prototypes")
        for slug in ("demo", "other"):
            directory = self.root / slug
            write(directory / "prototype.json", json.dumps({"slug": slug, "title": f"Tytul {slug}",
                                                           "requirements": f"/home/{SECRETS['dirpath']}/wymagania-{slug}.md",
                                                           "created": "2026-11-05", "generated_by": "test"}) + "\n")
            write(directory / "index.html", index_html(slug))
            write(directory / "prototype.js", "/* silnik */\n")
        self.operations = [create(1, "th-kom", COMMENTER), create(2, "th-dev", DEVELOPER, "jawny-watek-programisty")]
        self.write_journal("demo", self.operations)
        self.write_journal("other", [])
        self.install_users()

    def install_users(self) -> None:
        path = descend(self.project, ".skillsync/prototype/users.json")
        if PrototypeCase._users is None:
            users = ps.UsersFile(str(path), iterations=1000)
            for login, roles in ((COMMENTER, ["commenter"]), (EXPERT, ["expert"]), (ACCEPTOR, ["acceptor"]), (DEVELOPER, ["developer"])):
                ps.add_user(users, login, roles=roles, password=PASSWORD, account=PSEUDONYM[login],
                            name=FULL_NAME if login == COMMENTER else None)
            ps.add_user(users, APPROVER, roles=["approver"], password=LONG_PASSWORD, account=PSEUDONYM[APPROVER], via_prompt=True)
            ps.add_user(users, MUSTCHANGE, roles=["developer"], default_password=NEW_PASSWORD)
            PrototypeCase._users = path.read_bytes()
        else:
            write(path, PrototypeCase._users.decode("utf-8"))
            path.chmod(0o600)

    # --- files

    def write_journal(self, slug: str, operations: list) -> None:
        write_newer(self.root / slug / "comments.json", journal_text(slug, operations).encode("utf-8"))

    def journal(self, slug: str = "demo") -> list:
        return json.loads((self.root / slug / "comments.json").read_text(encoding="utf-8"))["operations"]

    def files(self) -> dict:
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in sorted(self.root.rglob("*"))
                if path.is_file() and not path.name.endswith(".lock")}

    # --- server and people

    def start(self, *extra: str, **kwargs) -> tuple:
        server = self.serve(*extra, **kwargs)
        self.page_server = server
        anonymous = server.browser()
        single = "--single-origin" in extra
        self.proto_port = server.port if single else anonymous.status()["prototype"]["port"]
        return server, anonymous

    def signed(self, login: str, password: str | None = None) -> Browser:
        browser = self.page_server.browser()
        reply = browser.login(login, password or (LONG_PASSWORD if login == APPROVER else PASSWORD))
        self.assertEqual(reply.status, 200, reply.text)
        return browser

    def handed(self, page: Browser) -> Browser:
        """The session of `page` handed over to the origin of the prototypes."""
        proto = Browser(self.proto_port, address=page.address)
        begun = proto.get("/_handoff/begin")
        self.assertEqual(begun.status, 303, begun.text)
        challenge = re.search(r"challenge=([0-9a-f]{64})", begun.header("Location")).group(1)
        issued = page.post("/_api/v1/handoff", {"challenge": challenge})
        self.assertEqual(issued.status, 200, issued.text)
        done = http_request(proto.address, proto.port, "POST", "/_handoff",
                            {"Origin": proto.origin, "Cookie": begun.set_cookie.split(";")[0],
                             "Content-Type": "application/x-www-form-urlencoded"}, ("code=" + issued.data["code"]).encode("ascii"))
        self.assertEqual(done.status, 303, done.text)
        proto.cookie = next(value.split(";")[0] for key, value in done.headers
                            if key.lower() == "set-cookie" and value.startswith("skillsync_proto_"))
        proto.csrf = proto.get("/_api/v1/session").data["csrf"]
        return proto

    def linked(self, login: str) -> Browser:
        """A session of the page opened by a one-time login link (no password)."""
        browser = self.page_server.browser()
        issued = self.cli("prototype", "login-link", str(self.project), "--user", login, "--port", str(self.page_server.port), *LAYER)
        self.assertEqual(issued.returncode, 0, issued.stdout + issued.stderr)
        url = json.loads(issued.stdout)["url"]
        followed = browser.get(url[url.index("/_login/link"):])
        self.assertEqual(followed.status, 302, followed.text)
        browser.cookie = followed.set_cookie.split(";")[0]
        browser.csrf = browser.get("/_api/v1/session").data["csrf"]
        self.assertTrue(browser.csrf, "the link did not open a session")
        return browser

    def shown(self, slug: str = "demo") -> dict:
        """What the person is shown: the operations answer of the origin of the prototypes (hashes of the screens and the version)."""
        reply = http_request(self.page_server.address, self.proto_port, "GET", f"/_api/v1/prototypes/{slug}/operations")
        self.assertEqual(reply.status, 200, reply.text)
        return reply.data


# --------------------------------------------------------------------------- K2: the anonymous view


class AnonymousViewCase(PrototypeCase):
    def setUp(self) -> None:
        super().setUp()
        extra = [
            create(3, "th-old", FORMER, "jawny-watek-dawnego", importedAuthor=SECRETS["importedauthor"]),
            create(4, "th-twin", FORMER_TWIN, "jawny-watek-blizniaka"),
            op(5, "reply", EXPERT, {"message": {"id": "m-5", "text": "jawna-odpowiedz-w-watku"}, SECRETS["unknownpayload"]: SECRETS["unknownkey"]},
               "th-kom", **{SECRETS["unknownkey"]: SECRETS["unknownkey"]}),
            op(6, "vote", ACCEPTOR, {"messageId": "m-1", "value": "up"}, "th-kom"),
            op(7, "decision", "agent", {"kind": "answer", "outcome": "accepted", "text": "jawna-decyzja", "who": DEVELOPER, "iteration": 1}, "th-kom"),
            op(8, "decision", "agent", {"kind": "answer", "outcome": "accepted", "text": "jawna-decyzja-druga",
                                        "who": SECRETS["whofree"], "iteration": 1}, "th-dev"),
        ]
        extra[-2]["payload"]["who"] = DEVELOPER
        reply_hint = create(9, "th-hint", COMMENTER, "jawny-watek-z-podpowiedzia")
        reply_hint["payload"]["hint"] = {"text": "jawna-podpowiedz", "legacyPath": "div:2/p:1"}
        self.operations += extra + [reply_hint]
        self.write_journal("demo", self.operations)
        self.accept_screen()
        app = ps.PrototypeApp(ps.ServerConfig(str(self.root), str(descend(self.project, ".skillsync/prototype/users.json")),
                                              git_checks=False, recover=False, pbkdf2_iterations=1000))
        question = {"type": "ask", "questionId": "qn-000000000001", "by": "agent", "cid": SECRETS["cid"] + "-q", "text": QUESTION_TEXT,
                    "blocking": False, "assumed": "tak", "addressees": [EXPERT, FORMER], "options": [],
                    "refs": [{"title": "Sekcja", "href": f"/home/{SECRETS['dirpath']}/a.md", "excerpt": "wyciag-publiczny"}],
                    "origin": {"spec": f"docs/specs/{SECRETS['origin']}.md", "q": "Q1"}}
        app.questions_store("demo").append([question, {"type": "answer", "questionId": "qn-000000000001", "by": EXPERT,
                                                       "cid": SECRETS["cid"] + "-a", "text": ANSWER_TEXT}])
        directory = str(self.root / "demo")
        ps.write_writing(directory, int(time.time() * 1000), 3000, by=f"{SECRETS['writingby']}@{DOMAIN}")
        # a frozen iteration with its own description
        frozen = self.root / "demo" / "iterations" / "1"
        write(frozen / "index.html", index_html("demo"))
        write(frozen / "prototype.json", (self.root / "demo" / "prototype.json").read_text(encoding="utf-8"))

    def accept_screen(self) -> None:
        hashes = ps.screen_hashes(str(self.root / "demo"))
        self.operations.append(op(10, "accept", ACCEPTOR, {"scope": "screen", "screens": {"s1": hashes["screens"]["s1"]},
                                                           "iteration": 1, "via": "ui"}))
        self.write_journal("demo", self.operations)

    PATHS = ("/", "/_api/v1/session", "/_api/v1/prototypes/demo/operations", "/_api/v1/questions", "/_api/v1/questions/qn-000000000001",
             "/_api/v1/runs/demo/questions", "/_api/v1/project", "/_api/v1/prototypes/demo/restore-preview?screen=s1&from=1",
             "/demo/prototype.json", "/demo/iterations/1/prototype.json", "/demo/", "/demo/index.html", "/_experts", "/_experts/demo")
    CLOSED = ("/demo/comments.json", "/demo/questions.json", "/demo/.writing.json", "/.skillsync/prototype/users.json",
              "/_api/v1/users", "/demo/iterations/1/comments.json")

    def sensitive(self) -> list:
        return [DOMAIN, "kanarek-druga.example", FULL_NAME, "pbkdf2", str(self.project), str(self.tmp), *SECRETS.values()]

    def crawl(self, port: int, address: str = "127.0.0.1", cookie: str | None = None) -> dict:
        headers = {"Cookie": cookie} if cookie else {}
        return {path: http_request(address, port, "GET", path, dict(headers)) for path in (*self.PATHS, *self.CLOSED)}

    def assert_redacted(self, replies: dict, gallery: bool = True) -> None:
        for path, reply in replies.items():
            body = reply.text + json.dumps(reply.data, ensure_ascii=False) + json.dumps(reply.headers)
            for value in self.sensitive():
                with self.subTest(path=path, canary=value):
                    self.assertNotIn(flat(value), flat(body))
        for path in self.CLOSED:
            self.assertEqual(replies[path].status, 404, path)
        operations = replies["/_api/v1/prototypes/demo/operations"]
        self.assertEqual(operations.status, 200, operations.text)
        # the controls: what is open stays open, under pseudonyms
        for value in (TEXT, "jawna-odpowiedz-w-watku", "jawna-decyzja", "jawna-podpowiedz", PSEUDONYM[COMMENTER], PSEUDONYM[ACCEPTOR]):
            self.assertIn(value, operations.text, value)
        questions = replies["/_api/v1/questions"]
        self.assertEqual(questions.status, 200, questions.text)
        for value in (QUESTION_TEXT, ANSWER_TEXT, PSEUDONYM[EXPERT], "wyciag-publiczny"):
            self.assertIn(value, questions.text, value)
        for path in ("/demo/prototype.json", "/demo/iterations/1/prototype.json"):
            self.assertEqual(replies[path].data.get("requirements"), "wymagania-demo.md", path)
        if gallery:   # with `--single-origin` "/" is the status page
            self.assertIn("Tytul demo", replies["/"].text)
            self.assertIn("wymagania-demo.md", replies["/"].text)


class AnonymousViewTests(AnonymousViewCase):
    def test_no_canary_reaches_an_anonymous_reader_of_either_origin(self) -> None:
        for flags in ((), ("--single-origin",)):
            with self.subTest(flags=flags):
                server, _anonymous = self.start(*flags)
                replies = self.crawl(self.proto_port)
                self.assert_redacted(replies, gallery=not flags)
                # a cookie that opens nothing is anonymous too
                self.assert_redacted(self.crawl(self.proto_port, cookie="skillsync_proto_00000000=" + "a" * 43), gallery=not flags)
                server.stop()

    def test_the_operations_of_an_anonymous_reader_are_a_white_list_with_one_pseudonym_per_person(self) -> None:
        self.start()
        data = self.shown()
        allowed = {"id", "type", "threadId", "at", "by", "payload"}
        by_id = {}
        for item in data["operations"]:
            self.assertEqual(set(item) - allowed, set(), item)
            by_id[int(item["id"][3:])] = item
        self.assertEqual(len(by_id), len(self.operations), "redaction hides fields, not operations")
        self.assertEqual((by_id[1]["by"], by_id[2]["by"], by_id[5]["by"], by_id[7]["by"]),
                         (PSEUDONYM[COMMENTER], PSEUDONYM[DEVELOPER], PSEUDONYM[EXPERT], "agent"))
        former, twin = by_id[3]["by"], by_id[4]["by"]
        self.assertNotEqual(former, twin, "two people never become one author")
        self.assertTrue(former.startswith("dawny.autor") and twin.startswith("dawny.autor") and "@" not in former + twin, (former, twin))
        self.assertEqual(by_id[7]["payload"]["who"], PSEUDONYM[DEVELOPER])
        self.assertNotIn(SECRETS["whofree"], json.dumps(by_id[8]))
        self.assertTrue(by_id[8]["payload"]["who"])
        self.assertNotIn("importedAuthor", json.dumps(data))
        self.assertEqual(data["acceptance"]["s1"]["who"], PSEUDONYM[ACCEPTOR])
        authors = {item["by"] for item in data["operations"]}
        self.assertTrue(authors <= set(data["people"]), (authors, set(data["people"])))
        self.assertEqual(set(data["writing"]) - {"since", "expiresAt", "renewedAt", "screensPlanned"}, set())
        self.assertNotIn("@", json.dumps(data["people"]))

    def test_a_signed_in_reader_sees_what_the_engine_always_showed(self) -> None:
        self.start()
        for login in (COMMENTER, EXPERT, ACCEPTOR, DEVELOPER):
            with self.subTest(login=login):
                page = self.signed(login)
                proto = self.handed(page)
                data = proto.get("/_api/v1/prototypes/demo/operations").data
                text = json.dumps(data, ensure_ascii=False)
                for value in (COMMENTER, FORMER, FORMER_TWIN, SECRETS["cid"], SECRETS["importedauthor"], FULL_NAME):
                    self.assertIn(value, text, value)
                self.assertIn(SECRETS["dirpath"], proto.get("/demo/prototype.json").text)
                questions = proto.get("/_api/v1/questions")
                self.assertEqual(questions.status, 200, questions.text)
                if login == EXPERT:
                    self.assertIn(SECRETS["dirpath"], questions.text)
                else:
                    self.assertIn(QUESTION_TEXT, questions.text)
        developer = self.handed(self.signed(DEVELOPER))   # handed over: without the role `developer`, as any account
        self.assertIn(COMMENTER, developer.get("/_api/v1/prototypes/demo/operations").text)

    def test_an_anonymous_reader_writes_nothing_and_a_session_change_resets_the_view(self) -> None:
        self.start()
        before = self.files()
        anonymous = Browser(self.proto_port)
        for path, body in (("/_api/v1/prototypes/demo/operations", {"cid": "c-1", "type": "reply", "threadId": "th-kom",
                                                                    "payload": {"message": {"id": "m-x", "text": "x"}}}),
                           ("/_api/v1/questions/qn-000000000001/comment", {"cid": "c-2", "text": "x"}),
                           ("/_api/v1/project/accept", {"cid": "c-3", "versions": {}})):
            reply = anonymous.post(path, body)
            self.assertEqual(reply.status, 401, (path, reply.text))
        self.assertEqual(self.files(), before)
        first = self.shown()
        again = anonymous.get(f"/_api/v1/prototypes/demo/operations?after={first['total']}&prefix={first['prefix']}").data
        self.assertEqual((again["reset"], again["operations"]), (False, []))   # the control: the same view continues
        proto = self.handed(self.signed(COMMENTER))
        signed = proto.get(f"/_api/v1/prototypes/demo/operations?after={first['total']}&prefix={first['prefix']}").data
        self.assertEqual((signed["reset"], len(signed["operations"])), (True, first["total"]))
        back = anonymous.get(f"/_api/v1/prototypes/demo/operations?after={signed['total']}&prefix={signed['prefix']}").data
        self.assertEqual((back["reset"], len(back["operations"])), (True, first["total"]))
        self.assertNotIn(DOMAIN, json.dumps(back))

    def test_a_session_that_must_change_its_password_gets_the_redacted_view(self) -> None:
        self.start("--single-origin")   # the one mode where such a session can open on the origin that serves the prototypes
        must = self.signed(MUSTCHANGE, NEW_PASSWORD)
        self.assertTrue(must.get("/_api/v1/session").data["user"]["mustChange"], "the control: it is such a session")
        operations = must.get("/_api/v1/prototypes/demo/operations")
        self.assertEqual(operations.status, 200, operations.text)
        for value in (DOMAIN, SECRETS["cid"], SECRETS["importedauthor"], FULL_NAME):
            self.assertNotIn(value, operations.text, value)
        self.assertIn(PSEUDONYM[COMMENTER], operations.text)
        self.assertNotIn(SECRETS["dirpath"], must.get("/demo/prototype.json").text)
        self.assertEqual(must.get("/demo/comments.json.orig").status, 404)
        questions = must.get("/_api/v1/questions")
        self.assertEqual((questions.status, questions.data.get("error")), (403, "password_change_required"), questions.text)
        self.assertIn(COMMENTER, self.signed(COMMENTER).get("/_api/v1/prototypes/demo/operations").text)

    def test_only_one_whole_address_gives_a_pseudonym_of_its_own(self) -> None:
        free = (f"Jan Tajnyautor jan.tajny@{DOMAIN}", f"Jan Tajnyautor <jan.tajny@{DOMAIN}>", f"jan.tajny@{DOMAIN}, drugi@{DOMAIN}",
                "Jan Tajnyautor")
        self.write_journal("demo", self.operations + [create(40 + index, f"th-free-{index}", author) for index, author in enumerate(free)])
        self.start()
        data = self.shown()
        found = {item["threadId"]: item["by"] for item in data["operations"] if item.get("threadId", "").startswith("th-free-")}
        self.assertEqual(len(found), len(free), found)
        for author in found.values():
            self.assertRegex(author, r"^uczestnik(-[0-9]+)?$")
        self.assertEqual(len(set(found.values())), len(free), "different people stay different")
        for value in ("Tajnyautor", "jan.tajny", "drugi@", DOMAIN):
            self.assertNotIn(value, json.dumps(data, ensure_ascii=False), value)

    COPIES = ("comments.json.orig", "comments.json.bak", "comments_BASE_12.json", "comments (1).json", "questions.json~", "#comments.json#",
              "questions.old", "comments.js")

    def test_copies_of_the_journals_are_not_served_to_a_reader_without_a_session(self) -> None:
        places = ("", "demo/", "demo/iterations/1/")
        paths = []
        for place in places:
            for name in self.COPIES:
                if name == "comments.js" and not place.endswith("1/"):
                    continue
                write(descend(self.root, place + name), json.dumps({"by": COMMENTER}))
                paths.append("/" + quote(place + name))
        write(self.root / "demo" / "data" / "comments.json", '{"demo": "dane-prototypu"}')   # deeper: data of the prototype itself
        for flags in ((), ("--single-origin",)):
            server, _anonymous = self.start(*flags)
            for path in paths:
                with self.subTest(flags=flags, path=path):
                    reply = http_request("127.0.0.1", self.proto_port, "GET", path)
                    self.assertEqual(reply.status, 404, reply.text)
                    self.assertNotIn(DOMAIN, reply.text)
            self.assertIn("dane-prototypu", http_request("127.0.0.1", self.proto_port, "GET", "/demo/data/comments.json").text)
            page = self.signed(COMMENTER)
            reader = page if flags else self.handed(page)
            for path in paths:
                with self.subTest(flags=flags, path=path, signed_in=True):
                    reply = reader.get(path)
                    self.assertEqual(reply.status, 200, reply.text)   # the control: the file is there
                    self.assertIn(COMMENTER, reply.text)
            server.stop()

    def test_the_network_reads_the_redacted_view(self) -> None:
        address = lan_address()
        if not address or not self_reachable(address):
            self.skipTest("brak adresu spoza loopbacku, z którym ta maszyna połączy się sama")
        server = self.serve(address=address)
        port = server.browser().status()["prototype"]["port"]
        self.assert_redacted(self.crawl(port, address=address))

    def test_the_stand_alone_server_keeps_its_behaviour(self) -> None:
        """The control of the whole class: `prototype serve` (no status page) shows logins to a reader without a session and asks for one
        before the questions, as before."""
        for _attempt in range(4):
            port = free_port()
            out = self.tmp / "proto-serve.out"
            err = self.tmp / "proto-serve.err"
            with out.open("wb") as out_handle, err.open("wb") as err_handle:
                proc = subprocess.Popen([sys.executable, str(CLI), "prototype", "serve", str(self.project), "--port", str(port), *LAYER],
                                        stdout=out_handle, stderr=err_handle, env=self.env, cwd=str(self.tmp))
            self.addCleanup(kill_tree, proc)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and proc.poll() is None and "Serwer prototypów:" not in err.read_text(encoding="utf-8", errors="replace") \
                    + out.read_text(encoding="utf-8", errors="replace"):
                time.sleep(0.1)
            if proc.poll() is None:
                break
        self.assertIsNone(proc.poll(), err.read_text(encoding="utf-8", errors="replace"))
        replies = self.crawl(port)
        operations = replies["/_api/v1/prototypes/demo/operations"]
        for value in (COMMENTER, SECRETS["cid"], SECRETS["importedauthor"], FORMER):
            self.assertIn(value, operations.text, value)
        for path in ("/", "/demo/prototype.json", "/demo/iterations/1/prototype.json"):
            self.assertIn(SECRETS["dirpath"], replies[path].text, path)
        self.assertEqual(replies["/_api/v1/questions"].status, 401)
        proc.terminate()
        proc.wait(timeout=20)


# --------------------------------------------------------------------------- the actions on the origin of the page


ACTIONS = ("accept", "unaccept", "restore", "resolve", "delete", "project")


class ActionCase(PrototypeCase):
    """The state before every action: the screen `s1` of `demo` accepted by the acceptor, an iteration 1 to restore from (`s1` changed since)."""

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.project / ".git")   # the fake `.git` of the helper: a restore needs a real working copy
        for command in (("init", "-q"), ("config", "user.email", "t@example.com"), ("config", "user.name", "t")):
            subprocess.run(["git", "-C", str(self.project), *command], check=True, capture_output=True, env=self.env)
        directory = self.root / "demo"
        ps.perform_iterate(ps.CommentsStore(str(directory), "demo"), str(directory))
        write(directory / "index.html", index_html("demo", first="Pierwszy po zmianie"))
        hashes = ps.screen_hashes(str(directory))
        self.write_journal("demo", self.journal() + [op(20, "accept", ACCEPTOR, {"scope": "screen", "screens": {"s1": hashes["screens"]["s1"]},
                                                                                "iteration": 2, "via": "ui"})])
        self.saved = self.files()
        self.number = 0

    def reset(self) -> None:
        """The prototypes as `setUp` left them."""
        for rel, data in self.saved.items():
            path = descend(self.root, rel)
            if not path.exists() or path.read_bytes() != data:
                write_newer(path, data)
        for path in sorted(self.root.rglob("*"), reverse=True):
            rel = path.relative_to(self.root).as_posix()
            if rel not in self.saved and path.is_file() and not path.name.endswith(".lock"):
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    def request(self, action: str, thread: str = "th-kom") -> tuple:
        """`(route, body)` of the action as the page sends it for what it shows now."""
        self.number += 1
        cid = f"akcja-{self.number}"
        demo = self.shown("demo")
        if action == "accept":
            return "/api/prototypes/demo/accept", {"cid": cid, "screens": {"s2": demo["screens"]["s2"]}, "version": demo["version"]}
        if action == "unaccept":
            return "/api/prototypes/demo/unaccept", {"cid": cid, "screens": ["s1"]}
        if action == "restore":
            return "/api/prototypes/demo/restore", {"cid": cid, "screen": "s1", "from": 1, "reason": "powrot", "version": demo["version"]}
        if action in ("resolve", "delete"):
            return f"/api/prototypes/demo/{action}", {"cid": cid, "threadId": thread}
        other = self.shown("other")
        return "/api/project/accept", {"cid": cid, "versions": {"demo": demo["version"], "other": other["version"]}}

    def assert_refused(self, reply, status: int, code: str) -> None:
        self.assertEqual((reply.status, reply.data.get("code")), (status, code), reply.text)
        self.assertTrue(reply.data.get("error"))
        for secret in (DOMAIN, PASSWORD):
            self.assertNotIn(secret, reply.text)

    def written(self) -> list:
        """The operations the actions added to the journals."""
        before = {json.loads(self.saved[f"{slug}/comments.json"])["prototypeId"]: len(json.loads(self.saved[f"{slug}/comments.json"])["operations"])
                  for slug in ("demo", "other")}
        return [(slug, item) for slug in ("demo", "other") for item in self.journal(slug)[before[slug]:]]

    def consent_lines(self) -> list:
        return [item for item in self.audit() if item["event"] == "consent"]


class RoleMatrixTests(ActionCase):
    # (login, own thread) -> the actions the matrix of the engine allows
    MATRIX = {COMMENTER: {"resolve"}, EXPERT: set(), ACCEPTOR: {"accept", "unaccept", "restore", "project"},
              DEVELOPER: {"accept", "unaccept", "restore", "resolve", "delete", "project"}, APPROVER: set()}
    TYPES = {"accept": ["accept"], "unaccept": ["unaccept"], "restore": ["iteration", "restore"], "resolve": ["resolve"],
             "delete": ["delete"], "project": ["accept", "accept"]}

    def run_action(self, who: Browser, login: str, action: str, allowed: bool, thread: str = "th-kom") -> None:
        self.reset()
        route, body = self.request(action, thread)
        lines = len(self.consent_lines())
        reply = who.post(route, body)
        if not allowed:
            self.assert_refused(reply, 403, "forbidden_role")
            self.assertEqual(self.files(), self.saved, "a refusal writes nothing")
            return
        self.assertEqual(reply.status, 200, reply.text)
        self.assertIs(reply.data.get("ok"), True)
        written = self.written()
        self.assertEqual([item["type"] for _slug, item in written], self.TYPES[action], written)
        self.assertEqual({item["by"] for _slug, item in written}, {login})
        added = self.consent_lines()[lines:]
        self.assertEqual({(item["word"], item["account"]) for item in added},
                         {("prototype-project-accept" if action == "project" else f"prototype-{action}", PSEUDONYM[login])}, added)
        self.assertEqual(len(added), 2 if action == "project" else 1, "one audit line for each prototype written")
        self.assertNotIn(DOMAIN, json.dumps(added))

    def test_every_action_against_every_role(self) -> None:
        self.start()
        for login, allowed in self.MATRIX.items():
            browser = self.signed(login)
            for action in ACTIONS:
                with self.subTest(login=login, action=action):
                    self.run_action(browser, login, action, action in allowed)

    def test_resolve_of_a_thread_of_somebody_else_needs_a_developer_and_the_own_one_any_account(self) -> None:
        own = {COMMENTER: "th-kom", DEVELOPER: "th-dev"}
        self.operations = self.journal() + [create(30, "th-eks", EXPERT), create(31, "th-acc", ACCEPTOR), create(32, "th-app", APPROVER)]
        self.write_journal("demo", self.operations)
        self.saved = self.files()
        own.update({EXPERT: "th-eks", ACCEPTOR: "th-acc", APPROVER: "th-app"})
        self.start()
        for login, thread in own.items():
            browser = self.signed(login)
            with self.subTest(login=login, thread="own"):
                self.run_action(browser, login, "resolve", True, thread)
            other = "th-kom" if login != COMMENTER else "th-dev"
            with self.subTest(login=login, thread="of somebody else"):
                self.run_action(browser, login, "resolve", login == DEVELOPER, other)

    def test_an_acceptor_undoes_only_the_own_acceptance(self) -> None:
        data = self.journal()
        data[-1]["by"] = DEVELOPER
        self.write_journal("demo", data)
        self.saved = self.files()
        self.start()
        self.run_action(self.signed(ACCEPTOR), ACCEPTOR, "unaccept", False)
        self.run_action(self.signed(DEVELOPER), DEVELOPER, "unaccept", True)

    def test_a_login_link_session_resolves_only_its_own_thread(self) -> None:
        self.start()
        linked = self.linked(DEVELOPER)
        for action in ACTIONS:
            with self.subTest(action=action):
                self.run_action(linked, DEVELOPER, action, False)   # `th-kom` is a thread of somebody else
        self.run_action(linked, DEVELOPER, "resolve", True, "th-dev")
        self.run_action(self.signed(DEVELOPER), DEVELOPER, "delete", True)   # the control: with the password

    def test_a_handed_session_acts_on_neither_origin(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        proto = self.handed(page)
        page_name = page.cookie.partition("=")[0]
        for action in ACTIONS:
            route, body = self.request(action)
            with self.subTest(action=action):
                for name in (proto.cookie.partition("=")[0], page_name):
                    stolen = page.clone()
                    stolen.cookie, stolen.csrf = f"{name}={proto.token}", proto.csrf
                    reply = stolen.post(route, body)
                    self.assertEqual(reply.status, 401, reply.text)
                for who in (proto, self.as_page_on_prototypes(page)):
                    reply = who.post(route, body)
                    self.assertIn(reply.status, (404, 405), (route, reply.text))
        self.assertEqual(self.files(), self.saved)
        self.run_action(page, DEVELOPER, "delete", True)   # the control

    def as_page_on_prototypes(self, page: Browser) -> Browser:
        """The strongest attempt on the origin of the prototypes: the cookie and the CSRF token of the page."""
        other = page.clone()
        other.port, other.host = self.proto_port, f"127.0.0.1:{self.proto_port}"
        return other

    def test_the_read_socket_has_no_actions(self) -> None:
        server, _anonymous = self.start(mode="two")
        page = self.signed(DEVELOPER)
        reader = page.clone()
        reader.port, reader.host = server.ports[0], f"127.0.0.1:{server.ports[0]}"
        for action in ACTIONS:
            route, body = self.request(action)
            reply = reader.post(route, body)
            self.assertIn(reply.status, (403, 404, 405), (route, reply.text))
        self.assertEqual(self.files(), self.saved)
        self.run_action(page, DEVELOPER, "delete", True)


class RequestGateTests(ActionCase):
    def test_without_csrf_origin_context_or_session_nothing_is_written(self) -> None:
        server, _anonymous = self.start()
        page = self.signed(DEVELOPER)
        proto_origin = f"http://127.0.0.1:{self.proto_port}"
        must = self.signed(MUSTCHANGE, NEW_PASSWORD)
        cases = [("no CSRF", page.post_headers(X_Skillsync_CSRF=None), 403, "consent_refused"),
                 ("wrong CSRF", page.post_headers(X_Skillsync_CSRF="x" * 43), 403, "consent_refused"),
                 ("Origin of the prototypes", page.post_headers(Origin=proto_origin, Sec_Fetch_Site="same-site"), 403, "consent_refused"),
                 ("only the Origin is foreign", page.post_headers(Origin=proto_origin), 403, "consent_refused"),
                 ("no Origin", page.post_headers(Origin=None), 403, "consent_refused"),
                 ("Origin null", page.post_headers(Origin="null"), 403, "consent_refused"),
                 ("no Sec-Fetch-Site", page.post_headers(Sec_Fetch_Site=None), 403, "consent_refused"),
                 ("same-site", page.post_headers(Sec_Fetch_Site="same-site"), 403, "consent_refused"),
                 ("text/plain", page.post_headers(Content_Type="text/plain"), 403, "consent_refused"),
                 ("no board header", page.post_headers(X_Status_Board=None), 403, "consent_refused"),
                 ("forwarded", page.post_headers(X_Forwarded_For="203.0.113.7"), 403, "consent_context"),
                 ("anonymous", page.post_headers(Cookie=None), 401, "login_required"),
                 ("dead cookie", page.post_headers(Cookie=page.cookie[:-4] + "AAAA"), 401, "session_expired"),
                 ("password to change", must.post_headers(), 403, "password_change_required")]
        for action in ACTIONS:
            route, body = self.request(action)
            for label, headers, status, code in cases:
                with self.subTest(action=action, case=label):
                    sent = {key: value for key, value in headers.items() if value is not None}
                    self.assert_refused(page.post(route, body, sent), status, code)
            self.assertEqual(self.files(), self.saved, action)
            with self.subTest(action=action, case="GET"):
                self.assertEqual(page.get(route).status, 404)
            reply = page.post(route, body)
            self.assertEqual(reply.status, 200, (action, reply.text))   # the control
            self.reset()

    def test_no_approvals_and_single_origin_switch_the_actions_off(self) -> None:
        for flag in ("--no-approvals", "--single-origin"):
            with self.subTest(flag=flag):
                server, _anonymous = self.start(flag)
                page = self.signed(DEVELOPER)
                for action in ACTIONS:
                    route, body = self.request(action)
                    self.assert_refused(page.post(route, body), 403, "approvals_disabled")
                self.assertEqual(self.files(), self.saved)
                server.stop()

    def test_unknown_routes_slugs_tabs_and_fields(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        _route, body = self.request("accept")
        for route, status in (("/api/prototypes/missing/accept", 404), ("/api/prototypes/../accept", 404), ("/api/prototypes/%2e%2e/accept", 404),
                              ("/api/prototypes/demo/iterate", 404), ("/api/prototypes/demo/accept/", 404),
                              ("/api/prototypes/project/accept/x", 404)):
            with self.subTest(route=route):
                reply = page.post(route, body)
                self.assertEqual(reply.status, status, reply.text)
        self.assert_refused(page.post("/api/prototypes/demo/accept", {**body, "tab": "0123456789ab"}), 404, "unknownTab")
        for label, route, sent in (("author in the body", "/api/prototypes/demo/accept", {**body, "by": ACCEPTOR}),
                                   ("who in the body", "/api/prototypes/demo/accept", {**body, "who": ACCEPTOR}),
                                   ("another scope", "/api/prototypes/demo/accept", {**body, "scope": "project"}),
                                   ("two screens", "/api/prototypes/demo/accept", {**body, "screens": self.shown()["screens"]}),
                                   ("no version", "/api/prototypes/demo/accept", {k: v for k, v in body.items() if k != "version"}),
                                   ("no cid", "/api/prototypes/demo/accept", {k: v for k, v in body.items() if k != "cid"}),
                                   ("payload of a delete", "/api/prototypes/demo/delete", {"cid": "c-9", "threadId": "th-kom", "payload": {}}),
                                   ("reason of a resolve", "/api/prototypes/demo/resolve", {"cid": "c-8", "threadId": "th-kom", "reason": "done"}),
                                   ("author of a project", "/api/project/accept", {"cid": "c-7", "versions": {}, "by": ACCEPTOR})):
            with self.subTest(label):
                reply = page.post(route, sent)
                self.assertEqual(reply.status, 400, reply.text)
                self.assertTrue(reply.data.get("code"))
        self.assertEqual(self.files(), self.saved)
        self.assertEqual(page.post("/api/prototypes/demo/accept", body).status, 200)

    def test_a_repeated_cid_writes_once_and_a_cid_of_another_action_conflicts(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("accept")
        self.assertEqual(page.post(route, body).status, 200)
        after = self.files()
        again = page.post(route, body)
        self.assertEqual(again.status, 200, again.text)
        self.assertEqual(self.files(), after)
        for other, sent in (("delete", {"cid": body["cid"], "threadId": "th-kom"}), ("resolve", {"cid": body["cid"], "threadId": "th-kom"}),
                            ("unaccept", {"cid": body["cid"], "screens": ["s1"]})):
            with self.subTest(other=other):
                self.assert_refused(page.post(f"/api/prototypes/demo/{other}", sent), 409, "cid_conflict")
        self.assertEqual(self.files(), after)


class RepeatedCidTests(ActionCase):
    """m3: a repeated `cid` is the answer to the SAME request only."""

    def check(self, page: Browser, action: str, changes: list) -> None:
        self.reset()
        route, body = self.request(action)
        self.assertEqual(page.post(route, body).status, 200, action)
        after = self.files()
        again = page.post(route, body)
        self.assertEqual(again.status, 200, (action, again.text))
        for label, change in changes:
            with self.subTest(action=action, change=label):
                self.assert_refused(page.post(route, {**body, **change}), 409, "cid_conflict")
        self.assertEqual(self.files(), after, action)

    def test_the_same_cid_with_other_content_conflicts(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        shown = self.shown()
        other = "0" * 64
        self.check(page, "accept", [("another screen", {"screens": {"s1": shown["screens"]["s1"]}}), ("another hash", {"screens": {"s2": other}})])
        self.check(page, "unaccept", [("another screen", {"screens": ["s2"]}), ("one more screen", {"screens": ["s1", "s2"]})])
        self.check(page, "restore", [("another screen", {"screen": "s2"}), ("another source", {"from": 2}), ("another reason", {"reason": "inny"})])
        self.check(page, "resolve", [("another thread", {"threadId": "th-dev"})])
        self.check(page, "delete", [("another thread", {"threadId": "th-dev"})])
        self.reset()
        _route, project = self.request("project")
        self.check(page, "project", [("another version", {"versions": {**project["versions"], "demo": other}}),
                                     ("a version left out", {"versions": {"other": project["versions"]["other"]}})])

    def test_the_cid_of_one_screen_is_not_the_cid_of_the_project_and_back(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("accept")
        self.assertEqual(page.post(route, body).status, 200)
        _route, project = self.request("project")
        after = self.files()
        self.assert_refused(page.post("/api/project/accept", {**project, "cid": body["cid"]}), 409, "cid_conflict")
        self.assertEqual(self.files(), after)
        self.assertEqual(page.post("/api/project/accept", project).status, 200)
        route, again = self.request("accept")
        self.assert_refused(page.post(route, {**again, "cid": project["cid"]}), 409, "cid_conflict")


    def test_a_restore_with_the_cid_of_an_operation_of_another_type_conflicts(self) -> None:   # O1
        self.start()
        page = self.signed(DEVELOPER)
        for action in ("accept", "unaccept", "resolve", "delete", "project"):
            with self.subTest(earlier=action):
                self.reset()
                route, earlier = self.request(action)
                self.assertEqual(page.post(route, earlier).status, 200)
                route, restore = self.request("restore")
                after = self.files()
                self.assert_refused(page.post(route, {**restore, "cid": earlier["cid"]}), 409, "cid_conflict")
                self.assertEqual(self.files(), after)
                self.assertEqual(page.post(route, restore).status, 200)   # the control: a `cid` of its own


class RecoveryTests(ActionCase):
    """O2: finishing an interrupted restore writes too: the session and the audit line come before it."""

    def setUp(self) -> None:
        super().setUp()
        directory = self.root / "demo"
        store = ps.CommentsStore(str(directory), "demo")
        real = store.append_locked

        def interrupted(partials, create=True):
            real(partials, create)
            raise KeyboardInterrupt()   # Ctrl+C right after the journal: the marker stays, the page is not written yet

        store.append_locked = interrupted
        with self.assertRaises(KeyboardInterrupt):
            ps.perform_restore(store, str(directory), "s1", 1, "przerwane", by="agent", who=ACCEPTOR)
        self.assertTrue((directory / ps.RESTORE_MARKER).exists())
        self.assertIn("Pierwszy po zmianie", (directory / "index.html").read_text(encoding="utf-8"))
        self.saved = self.files()

    def test_without_an_audit_line_nothing_of_the_recovery_is_written(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("restore")
        log = self.project / ".skillsync" / "prototype" / "run" / "audit.jsonl"
        self.assertFalse(log.exists(), "the control below counts the lines of a new file")
        log.mkdir(parents=True)
        try:
            self.assert_refused(page.post(route, body), 503, "audit_unavailable")
            self.assertEqual(self.files(), self.saved)
        finally:
            log.rmdir()
        reply = page.post(route, body)   # the control: with the audit the recovery runs; the view shown before it is stale then
        self.assert_refused(reply, 409, "stale_view")
        self.assertTrue(reply.data.get("recovery"), reply.data)
        self.assertFalse((self.root / "demo" / ps.RESTORE_MARKER).exists())
        self.assertIn("Pierwszy</h2>", (self.root / "demo" / "index.html").read_text(encoding="utf-8"))
        self.assertEqual([item["word"] for item in self.consent_lines()], ["prototype-restore"])

    def test_a_logout_before_the_recovery_stops_it(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("restore")
        outcome: list = []
        thread = threading.Thread(target=lambda: outcome.append(page.post(route, body)))
        with ps.CommentsStore(str(self.root / "demo"), "demo").lock():
            thread.start()
            time.sleep(0.8)
            self.assertEqual(outcome, [], "control: the restore waits for the lock")
            self.assertEqual(page.clone().post("/_api/v1/logout").status, 204)
        thread.join(timeout=20)
        self.assert_refused(outcome[0], 401, "session_expired")
        self.assertEqual((self.files(), self.consent_lines()), (self.saved, []))


class RestoreAuditTests(ActionCase):
    """n2: the audit line of a restore is written right before its write: a restore that writes nothing leaves none."""

    def test_a_refused_or_unchanged_restore_leaves_no_consent_line(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("restore")
        self.assert_refused(page.post(route, {**body, "version": "0" * 64}), 409, "stale_view")
        ps.write_writing(str(self.root / "demo"), int(time.time() * 1000), 600, by="agent")
        reply = page.post(route, body)
        self.assertEqual((reply.status, reply.data.get("code")), (409, "writing_in_progress"), reply.text)
        (self.root / "demo" / ps.WRITING_MARKER).unlink()
        reply = page.post(route, {**body, "from": 9})
        self.assertGreaterEqual(reply.status, 400, reply.text)
        self.assertEqual((self.consent_lines(), self.files()), ([], self.saved))
        self.assertEqual(page.post(route, body).status, 200)   # the control: a restore that writes
        self.assertEqual([item["word"] for item in self.consent_lines()], ["prototype-restore"])
        after = self.files()
        _route, same = self.request("restore")   # the screen is already the one of iteration 1
        reply = page.post(route, same)
        self.assertEqual((reply.status, reply.data.get("unchanged")), (200, True), reply.text)
        self.assertEqual((len(self.consent_lines()), self.files()), (1, after))


class DisplayedViewTests(ActionCase):
    """K22: the hash and the version are those the person was shown."""

    def test_a_view_that_is_no_longer_current_is_refused(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        requests = {action: self.request(action) for action in ("accept", "restore", "project")}
        write(self.root / "demo" / "index.html", index_html("demo", first="Pierwszy po zmianie", second="Drugi podmieniony"))
        changed = self.files()
        for action, (route, body) in requests.items():
            with self.subTest(action=action):
                reply = page.post(route, body)
                self.assert_refused(reply, 409, "stale_view")
                if action == "project":
                    self.assertEqual(reply.data.get("stale"), ["demo"], reply.data)
        # a current version with a hash from elsewhere (an older view, another screen) is refused too
        route, body = self.request("accept")
        for digest in (requests["accept"][1]["screens"]["s2"], self.shown()["screens"]["s1"], "0" * 64):
            self.assert_refused(page.post(route, {**body, "cid": "inny-hash-" + digest[:6], "screens": {"s2": digest}}), 409, "stale_view")
        self.assertEqual(self.files(), changed)
        self.assertEqual(page.post(*self.request("accept")).status, 200)   # the control: the view as it is now

    def test_a_writing_agent_stops_accept_restore_and_the_project(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        requests = {action: self.request(action) for action in ("accept", "restore", "project")}
        ps.write_writing(str(self.root / "demo"), int(time.time() * 1000), 600, by="agent")
        before = self.files()
        for action, (route, body) in requests.items():
            with self.subTest(action=action):
                reply = page.post(route, body)
                self.assertEqual(reply.status, 409, reply.text)
                if action == "project":
                    self.assertIn("demo", json.dumps(reply.data), reply.text)
                    self.assertIn(reply.data.get("code"), ("writing_in_progress", "partial"))
                else:
                    self.assertEqual((reply.data.get("code"), reply.data.get("slug")), ("writing_in_progress", "demo"), reply.text)
                    self.assertTrue(reply.data.get("since") and reply.data.get("expiresAt"), reply.data)
        self.assertEqual(self.files(), before)
        (self.root / "demo" / ps.WRITING_MARKER).unlink()
        self.assertEqual(page.post(*requests["accept"]).status, 200)

    def test_restore_needs_a_git_working_copy(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("restore")
        shutil.move(str(self.project / ".git"), str(self.tmp / "git-aside"))
        write(self.project / ".git" / "HEAD", "ref: refs/heads/feature/demo\n")
        self.assert_refused(page.post(route, body), 403, "source_writes_disabled")
        self.assertEqual(self.files(), self.saved)
        shutil.rmtree(self.project / ".git")
        shutil.move(str(self.tmp / "git-aside"), str(self.project / ".git"))
        self.assertEqual(page.post(route, body).status, 200)
        self.assertIn("Pierwszy</h2>", (self.root / "demo" / "index.html").read_text(encoding="utf-8"))


class AuditTests(ActionCase):
    def test_an_audit_that_cannot_be_written_stops_every_action(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        requests = {action: self.request(action) for action in ACTIONS}
        log = self.project / ".skillsync" / "prototype" / "run" / "audit.jsonl"
        kept = log.read_bytes() if log.exists() else None
        if kept is not None:
            log.unlink()
        log.mkdir(parents=True)   # a directory in its place: no line can be appended
        try:
            for action, (route, body) in requests.items():
                with self.subTest(action=action):
                    self.assert_refused(page.post(route, body), 503, "audit_unavailable")
                    self.assertEqual(self.files(), self.saved)
        finally:
            log.rmdir()
            if kept is not None:
                log.write_bytes(kept)
        self.assertEqual(page.post(*requests["delete"]).status, 200)
        self.assertEqual([item["word"] for item in self.consent_lines()], ["prototype-delete"])


class RevalidationTests(ActionCase):
    """B1: the session and the account are asked again right before the write."""

    def split_post(self, browser: Browser, route: str, body: dict, between) -> tuple:
        """Sends the headers and half of the body, runs `between`, sends the rest."""
        data = json.dumps(body).encode("utf-8")
        headers = {**browser.post_headers(), "Content-Length": str(len(data)), "Connection": "close"}
        head = f"POST {route} HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()) + "\r\n"
        with socket.create_connection((browser.address, browser.port), timeout=20) as sock:
            sock.sendall(head.encode("latin-1") + data[:5])
            time.sleep(0.4)   # the request is past the gate and waits for its body
            between()
            sock.sendall(data[5:])
            raw = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
        status = int(raw.split(b" ", 2)[1])
        answer = raw.partition(b"\r\n\r\n")[2]
        return status, json.loads(answer) if answer else {}

    def change_roles(self, login: str, roles: list) -> None:
        path = descend(self.project, ".skillsync/prototype/users.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        for user in data["users"]:
            if user["login"] == login:
                user.update(ps.role_fields(roles))
        write_newer(path, json.dumps(data).encode("utf-8"))

    def test_a_logout_between_the_headers_and_the_body_writes_nothing(self) -> None:
        self.start()
        for action in ACTIONS:
            with self.subTest(action=action):
                self.reset()
                page = self.signed(DEVELOPER)
                route, body = self.request(action)
                out = lambda page=page: self.assertEqual(page.clone().post("/_api/v1/logout").status, 204)   # noqa: E731
                status, answer = self.split_post(page, route, body, out)
                self.assertEqual((status, answer.get("code")), (401, "session_expired"), answer)
                self.assertEqual(self.files(), self.saved)
                again = self.signed(DEVELOPER)
                status, answer = self.split_post(again, route, body, lambda: None)   # the control: the same split request
                self.assertEqual(status, 200, answer)

    def test_a_role_taken_away_between_the_headers_and_the_body_writes_nothing(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        for action, roles in (("delete", ["acceptor"]), ("accept", ["commenter"]), ("restore", ["commenter"]), ("project", ["commenter"]),
                              ("resolve", ["commenter"]), ("unaccept", ["commenter"])):
            with self.subTest(action=action):
                self.reset()
                self.change_roles(DEVELOPER, ["developer"])
                route, body = self.request(action)
                status, answer = self.split_post(page, route, body, lambda: self.change_roles(DEVELOPER, roles))
                self.assertEqual((status, answer.get("code")), (403, "forbidden_role"), answer)
                self.assertEqual(self.files(), self.saved)
                self.change_roles(DEVELOPER, ["developer"])
                status, answer = self.split_post(page, route, body, lambda: None)
                self.assertEqual(status, 200, answer)

    def test_a_session_that_ends_in_the_middle_of_a_project_says_what_was_written(self) -> None:
        self.start()
        page = self.signed(DEVELOPER)
        route, body = self.request("project")
        outcome: list = []
        thread = threading.Thread(target=lambda: outcome.append(page.post(route, body)))
        with ps.CommentsStore(str(self.root / "other"), "other").lock():   # `demo` is written first (order of the slugs)
            thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not self.written():
                time.sleep(0.05)
            self.assertEqual([slug for slug, _item in self.written()], ["demo"])
            self.assertEqual(outcome, [], "control: the project waits for the second prototype")
            self.assertEqual(page.clone().post("/_api/v1/logout").status, 204)
        thread.join(timeout=20)
        reply = outcome[0]
        self.assertEqual((reply.status, reply.data.get("code")), (401, "session_expired"), reply.text)
        self.assertEqual((reply.data.get("accepted"), reply.data.get("pending")), (["demo"], ["other"]), reply.data)
        self.assertEqual([slug for slug, _item in self.written()], ["demo"])

    def test_a_logout_while_the_action_waits_for_the_lock_of_the_prototype_writes_nothing(self) -> None:
        self.start()
        for action in ("accept", "restore", "project", "delete"):
            with self.subTest(action=action):
                self.reset()
                page = self.signed(DEVELOPER)
                route, body = self.request(action)
                outcome: list = []
                thread = threading.Thread(target=lambda: outcome.append(page.post(route, body)))
                with ps.CommentsStore(str(self.root / "demo"), "demo").lock():
                    thread.start()
                    time.sleep(0.8)
                    self.assertEqual(outcome, [], "control: the action really waits for the lock")
                    self.assertEqual(page.clone().post("/_api/v1/logout").status, 204)
                thread.join(timeout=20)
                self.assert_refused(outcome[0], 401, "session_expired")
                self.assertEqual(self.files(), self.saved)


class StrictBodyTests(PrototypeCase):
    """n2: a body with `NaN` or `Infinity` is not JSON: neither a consent nor an action is written from it."""

    def test_a_consent_and_an_action_with_nan_are_refused(self) -> None:
        self.session_waits_for("CP")
        server, _anonymous = self.start()
        approver, developer = self.signed(APPROVER), self.signed(DEVELOPER)
        offer = approver.offer("cp")
        delivery = approver.status()["delivery"]
        consent = {"kind": "cp", "path": offer["path"], "sha": offer["sha"], "branch": delivery["branch"], "target": delivery["target"],
                   "planState": offer.get("planState")}
        before = (self.snapshot(), self.files())
        action = {"cid": "c-nan", "threadId": "th-kom"}
        for who, route, body in ((approver, "/api/approve", consent), (developer, "/api/prototypes/demo/delete", action)):
            for constant in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(route=route, constant=constant):
                    # the extra field would be ignored by the consent; only the JSON itself is at fault
                    data = (json.dumps(body)[:-1] + ', "extra": ' + constant + "}").encode("utf-8")
                    reply = http_request("127.0.0.1", who.port, "POST", route, who.post_headers(), data)
                    self.assertEqual(reply.status, 400, reply.text)
                    self.assertTrue(reply.data.get("code"))
        self.assertEqual((self.snapshot(), self.files()), before)
        done = approver.post("/api/approve", {**consent, "extra": 1})   # the control: the same body with a number
        self.assertEqual(done.status, 200, done.text)
        self.assertEqual(developer.post("/api/prototypes/demo/delete", action).status, 200)


class CutAddressFilterTests(unittest.TestCase):
    """O3: the filter of the anonymous page also takes the piece of an address a cut text ends with."""

    def test_a_piece_of_an_address_before_the_ellipsis_is_masked(self) -> None:
        clean = dh.public_text_filter([])
        for piece in ("jan.tajny@domena…", "jan.tajny@dom.…", "jan.tajny@…", "x@dom…"):
            with self.subTest(piece=piece):
                self.assertEqual(clean(f"temat {piece}"), "temat uczestnik…")
        self.assertEqual(clean("temat jan@example.com dalej"), "temat uczestnik dalej")
        for kept in ("pakiet nazwa@wersja zostaje", "ciąg dalszy…", "@dekorator…"):   # the control: neither a whole address nor a cut one
            with self.subTest(kept=kept):
                self.assertEqual(clean(kept), kept)


class EngineProjectRoleTests(PrototypeCase):
    """The engine decides about the role of a project acceptance too, not only the gate of the page."""

    def test_accept_project_checks_the_role(self) -> None:
        app = ps.PrototypeApp(ps.ServerConfig(str(self.root), str(descend(self.project, ".skillsync/prototype/users.json")),
                                              git_checks=False, recover=False, pbkdf2_iterations=1000))
        versions = {slug: ps.screen_hashes(str(self.root / slug))["version"] for slug in ("demo", "other")}
        before = self.files()
        for login, role in ((COMMENTER, None), (EXPERT, None), (APPROVER, None), ("nikt@example.com", None), (ACCEPTOR, "commenter")):
            with self.subTest(login=login, role=role):
                with self.assertRaises(ps.PrototypeServerError) as caught:
                    ps.accept_project(app, login, "c-engine", versions, role=role)
                self.assertEqual((caught.exception.status, caught.exception.code), (403, "forbidden"))
        self.assertEqual(self.files(), before)
        status, result = ps.accept_project(app, ACCEPTOR, "c-engine", versions)   # the control
        self.assertEqual((status, result["accepted"]), (201, ["demo", "other"]), result)


# --------------------------------------------------------------------------- M1: the page for a reader without a session


PAGE_EMAIL = f"notatka.kanarek@{DOMAIN}"
WAITING_LOGIN = "jan.loginkanarek"


class PathSearchTests(unittest.TestCase):
    """The search of the privacy checks itself, on every system: a Windows path is found in each spelling an answer can carry."""

    def test_a_windows_path_is_found_in_every_spelling(self) -> None:
        path = "C:\\praca\\Kanarek\\Temp\\proj"
        for body in (path, "c:/Praca/kanarek/temp/proj/docs", json.dumps({"p": path}), json.dumps(json.dumps({"p": path}))):
            with self.subTest(body=body):
                self.assertIn(flat(path, windows=True), flat(body, windows=True))
        self.assertNotIn(flat(path, windows=True), flat(json.dumps({"p": "proj"}), windows=True))

    def test_outside_windows_the_letter_case_counts(self) -> None:
        self.assertNotIn(flat("/tmp/Proj", windows=False), flat("/tmp/proj", windows=False))


class PagePrivacyTests(PrototypeCase):
    """`/api/status`, `/api/tabs` and `/api/history` of a project with a worktree: the notes of its documents carry addresses and absolute paths."""

    def setUp(self) -> None:
        super().setUp()
        shutil.rmtree(self.project / ".git")
        git = lambda *args: subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True, env=self.env)   # noqa: E731
        git("init", "-q", "-b", "feature/demo")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        self.home_file = f"{self.env['HOME']}/notatki-domowe.md"
        self.inside = f"{self.project}/docs/specs/demo-etap-1.md"
        done = descend(self.project, "docs/plans/2025-12-01-starszy.md")
        text = done.read_text(encoding="utf-8").split("\n", 1)
        write(done, f"{text[0]} dla {PAGE_EMAIL} w {self.inside}\n{text[1]}")
        run = descend(self.project, ".skillsync/runs/2026-01-10-demo-etap-1/checkpoints.md")
        with run.open("a", encoding="utf-8") as handle:
            handle.write(f"- 11:15 notatka: {PAGE_EMAIL} patrz {self.inside} oraz {self.home_file}\n")
            handle.write(f"- 11:20 zrobione: S9; w toku: —; czeka: GO plan (kto: akceptujący {WAITING_LOGIN})\n")
        question = {"type": "ask", "questionId": "qn-000000000002", "by": "agent", "cid": "c-page", "text": "Czy tak?", "blocking": True,
                    "assumed": None, "addressees": [EXPERT, f"obcy.adresat@{DOMAIN}"], "refs": [], "options": [],
                    "origin": {"spec": "docs/specs/demo-etap-1.md", "q": "Q1"}}
        app = ps.PrototypeApp(ps.ServerConfig(str(self.root), str(descend(self.project, ".skillsync/prototype/users.json")),
                                              git_checks=False, recover=False))
        app.questions_store("demo").append([question])
        write(self.project / ".gitignore", ".skillsync/\n")
        git("add", "docs", ".github", ".gitignore")
        git("commit", "-q", "-m", f"start {PAGE_EMAIL}")
        self.tree = self.tmp / "drzewo-robocze"
        git("worktree", "add", "-q", "-b", "druga", str(self.tree))
        write(self.tree / ".skillsync" / "runs" / "2026-01-10-demo-etap-1" / "checkpoints.md", run.read_text(encoding="utf-8"))
        source = json.dumps(str(CLI.parent))
        write(Path(self.env["SKILLSYNC_CONFIG"]), f"layers:\n  - id: core\n    kind: core\n    source: {source}\n    subdir: library\n"
                                                  f"    path: {source}\nlayer_rules:\n  - match: ['**']\n    use: [core]\n")
        patch = mock.patch.object(unified_helpers, "LAYER", [])   # a project server (several tabs) takes the layer from the config
        patch.start()
        self.addCleanup(patch.stop)

    def replies(self, browser: Browser) -> dict:
        tabs = browser.get("/api/tabs")
        self.assertEqual(tabs.status, 200, tabs.text)
        self.assertEqual(len(tabs.data["tabs"]), 2, tabs.data)
        found = {"/api/tabs": tabs}
        for path in ("/api/history", "/api/status", *(f"/api/status?tab={tab['id']}" for tab in tabs.data["tabs"])):
            found[path] = browser.get(path)
            self.assertEqual(found[path].status, 200, (path, found[path].text))
        return found

    def canaries(self) -> list:
        return [DOMAIN, str(self.tmp), self.env["HOME"]]

    def assert_hidden(self, replies: dict) -> None:
        for path, reply in replies.items():
            text = json.dumps(reply.data, ensure_ascii=False)
            for value in self.canaries():
                with self.subTest(path=path, canary=value):
                    self.assertNotIn(flat(value), flat(text))
        status = replies["/api/status"].data
        project = status["project"]
        self.assertEqual(project["path"], project["name"])
        chip = project["worktree"]   # the chip of the working directory stays: its kind and names
        self.assertTrue(chip["kind"] and chip["name"], chip)
        self.assertEqual((chip["path"], chip["mainPath"]), (chip["name"], None))
        other = [data.data["project"]["worktree"] for path, data in replies.items() if "?tab=" in path]
        self.assertEqual({(item["path"], item["mainPath"]) for item in other if item["kind"] == "worktree"}, {("drzewo-robocze", "proj")}, other)
        self.assertEqual(sorted(tab["path"] for tab in replies["/api/tabs"].data["tabs"]), ["drzewo-robocze", "proj"])
        self.assertEqual(sorted(tab["path"] for tab in status["worktrees"]), ["drzewo-robocze", "proj"])
        self.assertEqual(status["waiting"][0]["who"]["login"], None)
        self.assertTrue(status["waiting"][0]["who"]["role"])
        # the field that names the person; the same words in the text of a checkpoint are content and stay (spec: checkpoints are open)
        self.assertNotIn(WAITING_LOGIN, json.dumps([item.get("who") for item in status["waiting"]]))
        notes = json.dumps(status, ensure_ascii=False)
        for kept in ("uczestnik", "docs/specs/demo-etap-1.md", "~/notatki-domowe.md", "Czy tak?", PSEUDONYM[EXPERT]):
            self.assertIn(kept, notes, kept)
        self.assertIn("uczestnik", json.dumps(replies["/api/history"].data, ensure_ascii=False))

    def assert_open(self, replies: dict) -> None:
        """The control: what is hidden above is really there for a signed-in account."""
        status = json.dumps(replies["/api/status"].data, ensure_ascii=False)
        for value in (PAGE_EMAIL, EXPERT, str(self.project), self.home_file, *self.canaries()):   # the search of `assert_hidden` is not blind
            self.assertIn(flat(value), flat(status), value)
        self.assertEqual(replies["/api/status"].data["waiting"][0]["who"]["login"], WAITING_LOGIN)
        self.assertIn(flat(self.tree), flat(json.dumps(replies["/api/tabs"].data, ensure_ascii=False)))
        self.assertIn(PAGE_EMAIL, json.dumps(replies["/api/history"].data, ensure_ascii=False))

    def test_without_a_session_and_with_a_password_to_change(self) -> None:
        server, anonymous = self.start()
        self.assert_hidden(self.replies(anonymous))
        must = self.signed(MUSTCHANGE, NEW_PASSWORD)
        replies = self.replies(must)
        own = replies["/api/status"].data.pop("session")
        self.assertEqual((own["account"], own["roles"]), (MUSTCHANGE.split("@")[0], ["developer"]), "the data of its own account stays")
        self.assert_hidden(replies)
        self.assert_open(self.replies(self.signed(COMMENTER)))

    def test_the_read_socket_is_redacted_also_with_a_session(self) -> None:
        server, anonymous = self.start(mode="two")
        page = self.signed(DEVELOPER)
        self.assert_open(self.replies(page))
        reader = page.clone()
        reader.port, reader.host = server.ports[0], f"127.0.0.1:{server.ports[0]}"
        replies = self.replies(reader)
        replies["/api/status"].data.pop("session", None)
        self.assert_hidden(replies)

    def test_a_text_cut_to_its_limit_never_ends_with_a_piece_of_an_address(self) -> None:   # O3
        local = "urwany.kanarek"
        # the limit of a question text is 160 characters: the address starts before it and ends after it, at several places
        questions = [{"type": "ask", "questionId": f"qn-0000000001{pad}", "by": "agent", "cid": f"c-cut-{pad}",
                      "text": "Q " + "x" * (pad - 3) + f" {local}@{DOMAIN} koniec", "blocking": True, "assumed": None, "addressees": [EXPERT],
                      "refs": [], "options": [], "origin": {"spec": "docs/specs/demo-etap-1.md", "q": f"Q{pad}"}} for pad in (130, 143, 150, 155)]
        app = ps.PrototypeApp(ps.ServerConfig(str(self.root), str(descend(self.project, ".skillsync/prototype/users.json")),
                                              git_checks=False, recover=False))
        app.questions_store("demo").append(questions)
        server, anonymous = self.start()
        for who in (anonymous, self.signed(DEVELOPER)):
            data = who.get("/api/status").data["expertQuestions"]
            text = json.dumps(data, ensure_ascii=False)
            self.assertIn("Q xxxxx", text, "the control: the cut texts are in the answer")
            self.assertNotIn("urwany", text, who.cookie is not None)

    def test_the_actions_of_another_tab_are_refused_while_only_one_directory_is_shown(self) -> None:
        # m5: the origin of the prototypes shows the directory the server started in; nobody saw what another tab would change
        write(self.tree / "docs" / "prototypes" / "demo" / "prototype.json", (self.root / "demo" / "prototype.json").read_text(encoding="utf-8"))
        write(self.tree / "docs" / "prototypes" / "demo" / "index.html", index_html("demo"))
        other_journal = write(self.tree / "docs" / "prototypes" / "demo" / "comments.json",
                              journal_text("demo", [create(1, "th-kom", COMMENTER)])).read_bytes()
        server, anonymous = self.start()
        page = self.signed(DEVELOPER)
        tabs = {tab["id"] == "main": tab["id"] for tab in page.get("/api/tabs").data["tabs"]}
        before = self.files()
        body = {"cid": "c-tab", "threadId": "th-kom"}
        reply = page.post("/api/prototypes/demo/delete", {**body, "tab": tabs[False]})
        self.assertEqual((reply.status, reply.data.get("code")), (409, "prototypes_not_served"), reply.text)
        reply = page.post("/api/project/accept", {"cid": "c-tab-2", "versions": {}, "tab": tabs[False]})
        self.assertEqual((reply.status, reply.data.get("code")), (409, "prototypes_not_served"), reply.text)
        self.assertEqual(self.files(), before)
        self.assertEqual((self.tree / "docs" / "prototypes" / "demo" / "comments.json").read_bytes(), other_journal)
        reply = page.post("/api/prototypes/demo/delete", body)   # several tabs: a write names its tab
        self.assertEqual(reply.status, 400, reply.text)
        reply = page.post("/api/prototypes/demo/delete", {**body, "tab": tabs[True]})
        self.assertEqual(reply.status, 200, reply.text)


if __name__ == "__main__":
    unittest.main()
