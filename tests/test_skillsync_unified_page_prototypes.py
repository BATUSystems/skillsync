"""CORE-2000-UNIFIED U-4.2: the tab of the prototypes of the status page (criteria K2, K6, K7, K9, K18, K20, K22):
python3 -m unittest tests.test_skillsync_unified_page_prototypes

A real `skillsync dashboard serve` over the project of `test_skillsync_unified_prototype_actions` (two prototypes, six accounts). The views the page
reads (`GET /api/prototypes[/<slug>]`) and the facts of `/api/status` are asked over HTTP; the page itself (`app.js` + `dashboard-render.js` on the
shadow DOM of the renderer tests, in node) is the harness of `test_skillsync_unified_page_session`, extended by what this tab shows."""
from __future__ import annotations

import json
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import test_skillsync_unified_page_session as page_session  # noqa: E402  (the module, not its test classes: they must not run twice)
import test_skillsync_unified_prototype_actions as actions  # noqa: E402
from tests.test_skillsync_dashboard_renderer import ADAPTER, HARNESS, NODE_MAJOR, RENDERER, fixture_files  # noqa: E402
from tests.test_skillsync_dashboard_renderer_i18n import run_node  # noqa: E402
from unified_helpers import descend, http_request, write  # noqa: E402

from skillsync_workflow import dashboard  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402

ACCEPTOR, DEVELOPER, COMMENTER, APPROVER, MUSTCHANGE = (actions.ACCEPTOR, actions.DEVELOPER, actions.COMMENTER, actions.APPROVER,
                                                        actions.MUSTCHANGE)
PSEUDONYM, DOMAIN = actions.PSEUDONYM, actions.DOMAIN
SANDBOX = "allow-scripts allow-same-origin allow-forms"


class ViewCase(actions.ActionCase):
    def view(self, browser, slug: str | None = "demo", query: str = ""):
        return browser.get("/api/prototypes" + (f"/{slug}" if slug else "") + query)

    def status_row(self, browser, slug: str, ready) -> tuple:
        """`(status, row of the prototype)` once `ready(row)`: the observer of the server reads a changed file within its poll."""
        end = time.monotonic() + 30
        while True:
            data = browser.status()
            row = next(item for item in data["prototypes"] if item["slug"] == slug)
            if ready(row) or time.monotonic() > end:
                return data, row
            time.sleep(0.2)


class PrototypeViewTests(ViewCase):
    """`GET /api/prototypes[/<slug>]`: what a signed-in person is shown before an action."""

    def test_only_a_signed_in_session_reads_a_view_and_a_refusal_names_nobody(self) -> None:
        _server, anonymous = self.start()
        for slug in (None, "demo", "nothing-here"):
            reply = self.view(anonymous, slug)
            self.assert_refused(reply, 401, "login_required")
            self.assertEqual(sorted(reply.data), ["code", "error"])
        stale = self.page_server.browser()
        stale.cookie = self.signed(COMMENTER).cookie.split("=")[0] + "=" + "x" * 43
        self.assert_refused(self.view(stale), 401, "session_expired")
        pending = self.page_server.browser()
        self.assertEqual(pending.login(MUSTCHANGE, actions.NEW_PASSWORD).status, 200)
        self.assert_refused(self.view(pending), 403, "password_change_required")
        self.assertEqual(self.view(self.signed(COMMENTER)).status, 200, "control")
        self.assertEqual(self.files(), self.saved, "a view writes nothing")
        self.assertEqual(self.consent_lines(), [])

    def test_the_view_gives_the_hashes_and_the_version_of_the_files_and_what_the_session_may_do(self) -> None:
        self.start()
        hashes = ps.screen_hashes(str(self.root / "demo"))
        view = self.view(self.signed(ACCEPTOR)).data
        self.assertEqual((view["slug"], view["title"], view["version"], view["format"], view["iterations"], view["sourceWrites"]),
                         ("demo", "Tytul demo", hashes["version"], 2, [1], True))
        self.assertEqual({row["id"]: row["hash"] for row in view["screens"]}, hashes["screens"])
        first, second = view["screens"]
        self.assertEqual((first["title"], first["state"], first["acceptedBy"], first["canUnaccept"]),
                         ("Pierwszy po zmianie", "accepted", PSEUDONYM[ACCEPTOR], True))
        self.assertEqual((second["state"], second["acceptedBy"], second["canUnaccept"]), ("pending", None, False))
        self.assertEqual(view["can"], {"accept": True, "restore": True, "project": True})
        self.assertEqual({(row["id"], row["author"], row["canResolve"], row["canDelete"]) for row in view["threads"]},
                         {("th-kom", PSEUDONYM[COMMENTER], False, False), ("th-dev", PSEUDONYM[DEVELOPER], False, False)})
        commenter = self.view(self.signed(COMMENTER)).data
        self.assertEqual(commenter["can"], {"accept": False, "restore": False, "project": False})
        self.assertEqual({row["id"]: row["canResolve"] for row in commenter["threads"]}, {"th-kom": True, "th-dev": False})
        self.assertFalse(commenter["screens"][0]["canUnaccept"])
        developer = self.view(self.signed(DEVELOPER)).data
        self.assertTrue(developer["screens"][0]["canUnaccept"])
        self.assertTrue(all(row["canDelete"] and row["canResolve"] for row in developer["threads"]))
        linked = self.view(self.linked(DEVELOPER)).data
        self.assertEqual(linked["can"], {"accept": False, "restore": False, "project": False}, "a login link carries no privileged role")
        for reply in (view, commenter, developer):
            self.assertNotIn(DOMAIN, json.dumps(reply), "the authors are pseudonyms")
            self.assertNotIn(str(self.project), json.dumps(reply))

    def test_the_listing_gives_the_version_of_every_prototype(self) -> None:
        self.start()
        data = self.view(self.signed(ACCEPTOR), None).data
        self.assertEqual({row["slug"]: row["version"] for row in data["prototypes"]},
                         {slug: ps.screen_hashes(str(self.root / slug))["version"] for slug in ("demo", "other")})
        self.assertNotIn(DOMAIN, json.dumps(data))
        self.assertNotIn(str(self.project), json.dumps(data))

    def test_what_was_shown_is_accepted_and_after_a_change_it_is_stale(self) -> None:   # K22
        self.start()
        page = self.signed(ACCEPTOR)
        shown = self.view(page).data
        body = {"cid": "view-1", "screens": {"s2": shown["screens"][1]["hash"]}, "version": shown["version"]}
        index = self.root / "demo" / "index.html"
        before = index.read_bytes()
        write(index, actions.index_html("demo", first="Zmienione po wyswietleniu"))
        self.assert_refused(page.post("/api/prototypes/demo/accept", body), 409, "stale_view")
        self.assertEqual(self.written(), [])
        actions.write_newer(index, before)
        self.assertEqual(page.post("/api/prototypes/demo/accept", body).status, 200, "control: the view is current again")

    def test_a_writing_agent_is_shown_in_the_view_and_in_the_status(self) -> None:
        self.start()
        page = self.signed(ACCEPTOR)
        self.assertIsNone(self.view(page).data["writing"], "control")
        marker = self.cli("prototype", "writing", str(self.project), "demo", "start", *actions.LAYER)
        self.assertEqual(marker.returncode, 0, marker.stdout + marker.stderr)
        view = self.view(page).data
        self.assertEqual(sorted(view["writing"]), ["expiresAt", "renewedAt", "since"], "never `by`")
        self.assertTrue(self.status_row(page, "demo", lambda row: row["writing"])[1]["writing"])

    def test_an_unknown_prototype_tab_or_another_directory_is_refused(self) -> None:
        self.start()
        page = self.signed(ACCEPTOR)
        self.assert_refused(self.view(page, "nothing-here"), 404, "not_found")
        self.assert_refused(self.view(page, "demo", "?tab=0123456789ab"), 404, "unknownTab")
        self.assertIs(page.status()["prototype"]["served"], True)
        manifest = descend(self.project, ".github/.skillsync.yaml")
        descend(self.project, "docs/other-prototypes").mkdir(parents=True)
        write(manifest, manifest.read_text(encoding="utf-8") + "prototype:\n  root: docs/other-prototypes\n")
        self.assert_refused(self.view(page), 409, "prototypes_not_served")
        self.assert_refused(self.view(page, None), 409, "prototypes_not_served")
        facts = page.status()["prototype"]
        self.assertEqual((facts["served"], facts["frame"]), (False, False))

    def test_no_text_of_the_repository_becomes_markup_or_a_secret_in_a_view(self) -> None:
        self.start()
        secret = "".join(("Sekretne", "Haslo", "7319"))
        hostile = f"<img src=x onerror=alert(1)> password={secret}"
        directory = self.root / "demo"
        write(directory / "prototype.json", json.dumps({"slug": "demo", "title": hostile}) + "\n")
        page = self.signed(ACCEPTOR)
        for reply in (self.view(page), self.view(page, None)):
            self.assertNotIn(secret, reply.text)
            self.assertEqual(reply.header("Content-Type"), "application/json; charset=utf-8")
        self.assertIn("<img src=x onerror=alert(1)>", self.view(page).data["title"], "text for the renderer, which never parses it")
        self.assertNotIn(secret, json.dumps(page.status()))


class StatusFactsTests(ViewCase):
    """`prototype.url|served|frame`, `prototypes[]` and the questions without prototypes (K2, K6, K7)."""

    def test_the_origin_of_the_prototypes_is_the_one_the_requester_sees(self) -> None:
        server, anonymous = self.start()
        facts = anonymous.status()["prototype"]
        self.assertEqual((facts["url"], facts["served"], facts["frame"]), (f"http://127.0.0.1:{self.proto_port}", True, True))
        named = http_request("127.0.0.1", server.port, "GET", "/api/status", {"Host": f"localhost:{server.port}"})
        self.assertEqual(named.data["prototype"]["url"], f"http://localhost:{self.proto_port}")
        proxied = http_request("127.0.0.1", server.port, "GET", "/api/status", {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual((proxied.data["prototype"]["url"], proxied.data["prototype"]["frame"]), (None, False),
                         "behind an intermediary without --prototype-url the origin is not guessed")

    def test_the_prototype_rows_carry_a_title_and_the_gate_and_stay_redacted_for_an_anonymous_reader(self) -> None:
        _server, anonymous = self.start()
        write(self.root / "other" / "prototype.json",
              json.dumps({"slug": "other", "title": f"Autor {ACCEPTOR} w {self.project}/docs/x.md"}) + "\n")
        data, other = self.status_row(anonymous, "other", lambda row: "docs/x.md" in row["title"])
        self.assertIn("docs/x.md", other["title"], "control: the rest of the title is there")
        demo = next(row for row in data["prototypes"] if row["slug"] == "demo")
        self.assertEqual((demo["title"], demo["gate"]), ("Tytul demo", "fail"))
        self.assertNotIn(ACCEPTOR, json.dumps(data))
        self.assertNotIn(str(self.project), json.dumps(data))
        hashes = ps.screen_hashes(str(self.root / "demo"))
        self.write_journal("demo", self.journal() + [actions.op(21, "accept", ACCEPTOR, {"scope": "screen", "screens": {"s2": hashes["screens"]["s2"]},
                                                                                         "iteration": 2, "via": "ui"})])
        self.assertEqual(self.status_row(anonymous, "demo", lambda row: row["gate"] == "pass")[1]["gate"], "pass")
        index = self.root / "demo" / "index.html"
        write(index, index.read_text(encoding="utf-8").replace("</body>", '<section class="screen" id="s3"><h2>x</h2></section>\n'
                                                               '<section class="screen" id="s3"><h2>y</h2></section>\n</body>'))
        ambiguous = self.status_row(anonymous, "demo", lambda row: row["gate"] == "fail")[1]
        self.assertEqual((ambiguous["gate"], ambiguous["screens"], ambiguous["accepted"]), ("fail", 2, 2), "an ambiguous screen, every other accepted")

    def test_a_title_is_filtered_for_an_anonymous_reader_before_it_is_cut(self) -> None:
        _server, anonymous = self.start()
        limit = dashboard.PROTOTYPE_TITLE_LIMIT
        inside = f"{self.project}/docs/x.md"
        cases = {"demo": ("y" * (limit - len(str(self.project)) // 2) + " ", inside), "other": ("z" * (limit - 10) + " ", ACCEPTOR)}
        for slug, (filler, secret) in cases.items():
            write(descend(self.root, f"{slug}/prototype.json"), json.dumps({"slug": slug, "title": filler + secret + " koniec"}) + "\n")
        data, demo = self.status_row(anonymous, "demo", lambda row: row["title"].startswith("yyy"))
        other = self.status_row(anonymous, "other", lambda row: row["title"].startswith("zzz"))[1]
        for row in (demo, other):
            self.assertLessEqual(len(row["title"]), limit)
        for piece in (str(self.project)[:len(str(self.project)) // 2], str(self.tmp)[:12], ACCEPTOR.partition("@")[0], DOMAIN[:6]):
            self.assertNotIn(piece, demo["title"] + other["title"])
        self.assertIn("docs/x.md", demo["title"], "the path inside the project stays, filtered before the cut")
        signed = self.signed(COMMENTER).status()
        own = next(row for row in signed["prototypes"] if row["slug"] == "demo")["title"]
        self.assertEqual(own, (cases["demo"][0] + inside + " koniec")[:limit], "control: a signed-in reader gets the plain cut")

    def test_single_origin_names_the_page_as_the_origin_and_offers_no_frame(self) -> None:
        server, anonymous = self.start("--single-origin")
        data = anonymous.status()
        self.assertIs(data["singleOrigin"], True)
        self.assertEqual((data["prototype"]["url"], data["prototype"]["served"], data["prototype"]["frame"]),
                         (f"http://127.0.0.1:{server.port}", True, False))
        shown = anonymous.get("/").text
        for part in ("data-single-origin", "GO implement", "CLI"):
            self.assertIn(part, shown)


# --------------------------------------------------------------------------- the real page

EXTRA = """proto: clean(app.querySelector('#view-prototypes')), notes: clean(app.querySelector('#protoNotes')),
    frames: all('iframe').map((f) => ({ src: f.getAttribute('src'), sandbox: f.getAttribute('sandbox'), referrer: f.getAttribute('referrerpolicy') })),
    links: all('#view-prototypes a').map((a) => ({ href: a.getAttribute('href'), rel: a.getAttribute('rel'), target: a.getAttribute('target') })),
    buttons: all('#view-prototypes button').map((b) => b.textContent),
    window: dialog ? Object.fromEntries(dialog.querySelectorAll('dd').__asArray().map((dd) => [dd.getAttribute('data-proto-row') || '', clean(dd)])) : null,
    history: clean(byId.whistory),"""
PAGE = page_session.PAGE.replace("history: clean(byId.whistory),", EXTRA)
assert PAGE != page_session.PAGE
wait, click, answered, shot = page_session.wait, page_session.click, page_session.answered, page_session.shot
TAB = [wait("#tab-prototypes"), click("#tab-prototypes")]


def manage(slug: str = "demo") -> list:
    return [wait(f"button[data-proto-manage=\"{slug}\"]"), click(f"button[data-proto-manage=\"{slug}\"]"), wait("[data-proto-version]")]


def act(scope: str, name: str) -> list:
    selector = f"{scope} button[data-proto-act=\"{name}\"]"
    return [wait(selector), click(selector), wait(".dlg-modal")]


CONFIRM = click("button[data-proto-confirm]")


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class PrototypePageTests(ViewCase):
    def page(self, steps, cookie: str = "", **scenario) -> dict:
        original = page_session.PAGE
        page_session.PAGE = PAGE
        try:
            return page_session.PageCase.page(self, self.page_server, steps, cookie=cookie, **scenario)
        finally:
            page_session.PAGE = original

    writes = staticmethod(page_session.PageCase.writes)
    assert_page_headers = page_session.PageCase.assert_page_headers

    def reads(self, out) -> list:
        return [item for item in out["requests"] if item["url"].startswith("/api/prototypes")]

    def test_an_anonymous_page_lists_every_prototype_and_offers_only_to_open_it(self) -> None:
        self.start()
        out = self.page(TAB + [wait("[data-proto=\"other\"]"), shot("list")])
        shown = out["shots"]["list"]
        for part in ("demo", "Tytul demo", "other", "bramka planu: brak akceptacji", "Czytasz bez logowania"):
            self.assertIn(part, shown["proto"])
        self.assertEqual([link["href"] for link in shown["links"]],
                         [f"http://127.0.0.1:{self.proto_port}/{slug}/" for slug in ("demo", "other")])
        self.assertEqual({(link["rel"], link["target"]) for link in shown["links"]}, {("noopener noreferrer", "_blank")})
        self.assertNotIn("Akceptacje i wątki", shown["buttons"])
        self.assertEqual(self.reads(out), [], "an anonymous page asks for no view")
        self.assertEqual([item for item in out["requests"] if item["method"] != "GET"], [])

    def test_a_signed_in_page_enters_the_prototypes_by_a_top_level_handover(self) -> None:
        self.start()
        browser = self.signed(COMMENTER)
        out = self.page(TAB + [wait("a[data-proto-handoff]"), shot("list")], cookie=browser.cookie)
        links = out["shots"]["list"]["links"]
        self.assertIn({"href": f"http://127.0.0.1:{self.proto_port}/_handoff/begin", "rel": "noopener noreferrer", "target": "_blank"}, links)
        self.assertEqual([frame for frame in out["shots"]["list"]["frames"]], [], "the handover is never framed")
        self.assertNotIn("Akceptacja całego projektu", out["shots"]["list"]["buttons"])
        self.assertEqual([item for item in out["requests"] if "handoff" in item["url"]], [], "the page itself asks for no code")

    def test_accept_sends_the_hash_and_the_version_of_the_view_and_the_server_writes_it(self) -> None:
        self.start()
        browser = self.signed(ACCEPTOR)
        hashes = ps.screen_hashes(str(self.root / "demo"))
        out = self.page(TAB + manage() + [shot("view"), *act("[data-screen=\"s2\"]", "accept"), shot("window"), CONFIRM,
                                          answered("/api/prototypes/demo/accept", 200), wait(".wait-item.is-ok"), {"op": "sleep", "ms": 800}, shot("done")],
                        cookie=browser.cookie)
        self.assertEqual(out["shots"]["view"]["frames"], [])
        window = out["shots"]["window"]
        self.assertEqual((window["dialog"]["passwordFields"], window["dialog"]["focused"]), (0, "Anuluj"))
        self.assertEqual((window["window"]["proto"], window["window"]["version"], window["window"]["hash"]),
                         ("demo", hashes["version"][:12], hashes["screens"]["s2"][:12]))
        call, = self.writes(out, "/api/prototypes/demo/accept")
        self.assert_page_headers(call, browser.csrf)
        self.assertEqual(sorted(call["body"]), ["cid", "screens", "version"])
        self.assertEqual((call["body"]["screens"], call["body"]["version"]), ({"s2": hashes["screens"]["s2"]}, hashes["version"]))
        self.assertEqual([(item["type"], item["by"]) for _slug, item in self.written()], [("accept", ACCEPTOR)])
        self.assertGreaterEqual(len(self.reads(out)), 2, "the view is read again after the write")

    def test_a_screen_changed_under_the_window_is_refused_and_the_view_is_read_again(self) -> None:   # K22
        self.start()
        browser = self.signed(ACCEPTOR)
        old = ps.screen_hashes(str(self.root / "demo"))
        index = self.root / "demo" / "index.html"
        changed = actions.index_html("demo", first="Pierwszy po zmianie", second="Drugi zmieniony")
        out = self.page(TAB + manage() + [*act("[data-screen=\"s2\"]", "accept"), {"op": "write", "path": str(index), "text": changed},
                                          {"op": "sleep", "ms": 2500}, CONFIRM, answered("/api/prototypes/demo/accept", 409),
                                          wait(".wait-item.is-err"), {"op": "sleep", "ms": 800}, shot("refused"),
                                          *act("[data-screen=\"s2\"]", "accept"), shot("again"), CONFIRM,
                                          answered("/api/prototypes/demo/accept", 200)], cookie=browser.cookie)
        first, second = self.writes(out, "/api/prototypes/demo/accept")
        self.assertEqual((first["answer"]["code"], first["body"]["version"]), ("stale_view", old["version"]),
                         "the page sent what it showed, not what the status had learnt meanwhile")
        self.assertIn("Prototyp zmienił się od wyświetlenia", out["shots"]["refused"]["banner"])
        new = ps.screen_hashes(str(self.root / "demo"))
        self.assertEqual((second["body"]["version"], second["body"]["screens"]), (new["version"], {"s2": new["screens"]["s2"]}))
        self.assertNotEqual(second["body"]["cid"], first["body"]["cid"])

    def test_unaccept_restore_resolve_and_delete_from_the_view(self) -> None:
        self.start()
        browser = self.signed(DEVELOPER)
        reason = {"op": "type", "selector": "input[data-proto-reason]", "value": "powrot"}
        done = lambda route: [answered(route, 200), {"op": "sleep", "ms": 900}]   # noqa: E731
        out = self.page(TAB + manage() + [
            *act("[data-screen=\"s1\"]", "restore"), CONFIRM, shot("noReason"), reason, CONFIRM, *done("/api/prototypes/demo/restore"),
            *act("[data-thread=\"th-kom\"]", "resolve"), CONFIRM, *done("/api/prototypes/demo/resolve"),
            *act("[data-thread=\"th-dev\"]", "delete"), CONFIRM, *done("/api/prototypes/demo/delete"),
            *act("[data-screen=\"s1\"]", "unaccept"), CONFIRM, *done("/api/prototypes/demo/unaccept")], cookie=browser.cookie)
        self.assertIn("Podaj powód", out["shots"]["noReason"]["dialog"]["text"])
        posts = [item for item in out["requests"] if item["method"] == "POST"]
        self.assertEqual([(item["url"].rsplit("/", 1)[1], item["status"], sorted(item["body"])) for item in posts], [
            ("restore", 200, ["cid", "from", "reason", "screen", "version"]), ("resolve", 200, ["cid", "threadId"]),
            ("delete", 200, ["cid", "threadId"]), ("unaccept", 200, ["cid", "screens"])])
        self.assertEqual((posts[0]["body"]["from"], posts[0]["body"]["reason"]), (1, "powrot"))
        self.assertEqual([item["type"] for _slug, item in self.written()], ["iteration", "restore", "resolve", "delete", "unaccept"])

    def test_with_several_tabs_the_view_and_the_action_name_the_tab(self) -> None:
        self.start()
        browser = self.signed(ACCEPTOR)
        main = self.page_server.browser().get("/api/tabs").data["current"]
        out = self.page([wait(f"button[data-wt-tab=\"{main}\"]", root="wtabs"), *TAB, *manage(), *act("[data-screen=\"s2\"]", "accept"), CONFIRM,
                         answered("/api/prototypes/demo/accept", 200)], cookie=browser.cookie, fakeTab=page_session.FAKE_TAB, search="?tab=" + main)
        self.assertIn((f"/api/prototypes/demo?tab={main}", 200), [(item["url"], item["status"]) for item in self.reads(out)])
        self.assertEqual(self.writes(out, "/api/prototypes/demo/accept")[0]["body"]["tab"], main)

    def test_the_whole_project_is_accepted_with_the_versions_the_page_listed(self) -> None:
        self.start()
        browser = self.signed(ACCEPTOR)
        versions = {slug: ps.screen_hashes(str(self.root / slug))["version"] for slug in ("demo", "other")}
        out = self.page(TAB + [wait("button[data-proto-project]"), click("button[data-proto-project]"), wait("[data-project-proto=\"other\"]"),
                               click("button[data-proto-act=\"project/accept\"]"), shot("window"), CONFIRM, answered("/api/project/accept", 200)],
                        cookie=browser.cookie)
        call, = self.writes(out, "/api/project/accept")
        self.assertEqual((sorted(call["body"]), call["body"]["versions"]), (["cid", "versions"], versions))
        for version in versions.values():
            self.assertIn(version[:12], out["shots"]["window"]["window"]["versions"])
        self.assertEqual(sorted(slug for slug, _item in self.written()), ["demo", "other"])

    def test_no_consent_is_drawn_while_a_frame_is_on_the_screen_and_the_view_of_acceptance_has_no_frame(self) -> None:
        self.start()
        self.spec_waits_for_go_plan()   # a consent the page offers without a commit
        approver = self.signed(APPROVER)
        embed = [wait("button[data-proto-embed=\"demo\"]"), click("button[data-proto-embed=\"demo\"]"), wait("iframe")]
        out = self.page([wait("button[data-approve=\"spec\"]"), shot("before"), *TAB, *embed, shot("framed"), click("#tab-progress"),
                         {"op": "gone", "selector": "iframe"}, wait("button[data-approve=\"spec\"]"), shot("after")], cookie=approver.cookie)
        self.assertTrue(out["shots"]["before"]["gates"])
        framed = out["shots"]["framed"]
        self.assertEqual(framed["frames"], [{"src": f"http://127.0.0.1:{self.proto_port}/demo/", "sandbox": SANDBOX, "referrer": "no-referrer"}])
        self.assertEqual(framed["gates"], [])
        self.assertIn("Przyciski zgód są ukryte", framed["banner"])
        self.assertEqual(out["shots"]["after"]["frames"], [])
        self.assertTrue(out["shots"]["after"]["gates"])
        acceptor = self.signed(ACCEPTOR)
        out = self.page(TAB + embed + manage() + [shot("view")], cookie=acceptor.cookie)
        self.assertEqual(out["shots"]["view"]["frames"], [], "the frame is closed for the view of an acceptance")

    def test_a_session_that_ends_closes_the_view_and_its_window(self) -> None:
        self.start()
        browser = self.signed(ACCEPTOR)
        out = self.page(TAB + manage() + [*act("[data-screen=\"s2\"]", "accept"), {"op": "logoutOutside"}, CONFIRM,
                                          answered("/api/prototypes/demo/accept", 401), wait("#sessionBar button[data-session=\"login\"]"),
                                          {"op": "sleep", "ms": 800}, shot("out")], cookie=browser.cookie)
        shown = out["shots"]["out"]
        self.assertIsNone(shown["dialog"])
        self.assertNotIn("Akceptuj ekran", shown["buttons"])
        self.assertEqual(self.written(), [])

    def test_single_origin_shows_why_nothing_can_be_consented_here(self) -> None:
        self.start("--single-origin")
        self.spec_waits_for_go_plan()   # a consent the page offers without a commit
        browser = self.signed(ACCEPTOR)
        out = self.page([wait("#waitBanner .wait-item"), *TAB, wait("[data-proto=\"demo\"]"), shot("single")], cookie=browser.cookie)
        shown = out["shots"]["single"]
        self.assertEqual([gate for gate in shown["gates"] if not gate["disabled"]], [], "no consent can be clicked")
        self.assertIn("GO plan", shown["banner"], "control: a consent is awaited")
        self.assertIn("zgody ze strony są wyłączone", shown["bar"])
        self.assertIn("--single-origin", shown["notes"])
        self.assertNotIn("Akceptacje i wątki", shown["buttons"])
        self.assertNotIn("Pokaż w zakładce", shown["buttons"])
        self.assertEqual({link["href"].rsplit("/", 2)[0] for link in shown["links"]}, {f"http://127.0.0.1:{self.page_server.port}"})


class SourceTests(unittest.TestCase):
    def test_the_page_listens_to_no_message_of_a_frame(self) -> None:
        for path in (ADAPTER, RENDERER, ADAPTER.parent / "theme.js"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"""addEventListener\(\s*["']message["']|\bonmessage\b""", path.name)
        self.assertEqual(re.findall(r'"(allow-[a-z- ]+)"', RENDERER.read_text(encoding="utf-8")), [SANDBOX])


# --------------------------------------------------------------------------- a host without the new deps (K9), the renderer alone
HOST = r"""
(async () => {
  const out = { violations };
  const data = () => { const j = JSON.parse(JSON.stringify(input.fixture));
    j.prototype = { slug: 'run-alpha', port: 5130, up: true, writing: null };
    j.prototypes[0].title = '<img src=x onerror=alert(1)>'; j.prototypes[1].slug = 'x/../y'; return j; };
  const deps = { prototypeUrl: (port, slug) => `http://127.0.0.1:${port}/${slug}/` };
  const m = mount(data(), { now: Date.parse(input.fixture.generatedAt), deps });
  const tags = []; walk(m.root, (n) => { if (n instanceof E) tags.push(n.localName); });
  const key = (k) => { m.board.handleKey({ key: k, target: null }); return m.board.state().view; };
  out.keys = [key('p'), key('d'), key('P')];
  out.tab = text(m.root.querySelector('#tab-prototypes'));
  out.rows = m.root.querySelectorAll('#protoList .q-proto').length;
  out.buttons = m.root.querySelectorAll('#view-prototypes button').length;
  out.links = m.root.querySelectorAll('#view-prototypes a').__asArray().map((a) => a.getAttribute('href'));
  out.images = tags.filter((t) => t === 'img' || t === 'iframe').length;
  out.title = text(m.root.querySelector('#protoList .q-name'));
  const none = data(); none.prototypes = [];
  m.board.update(none);
  out.gone = { view: m.board.state().view, hidden: m.root.querySelector('#tab-prototypes').hidden, p: key('p') };
  m.board.destroy();
  for (const [name, url] of [['hostileLinks', 'javascript:alert(1)'], ['controlLinks', 'http://127.0.0.1:5142']]) {
    const named = data(); named.prototype.url = url;
    const h = mount(named, { now: Date.parse(input.fixture.generatedAt), deps });
    out[name] = h.root.querySelectorAll('#view-prototypes a').__asArray().map((a) => a.getAttribute('href'));
    h.board.destroy();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class HostWithoutNewDepsTests(unittest.TestCase):
    def test_the_tab_lists_and_links_with_the_address_of_the_host_and_offers_nothing_else(self) -> None:
        tail = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
        fixture = fixture_files()["questions"]
        out = run_node(tail + HOST, {"fixtures": {}, "now": {}, "fixture": fixture})
        self.assertEqual(out["violations"], [])
        self.assertEqual(out["keys"], ["prototypes", "docs", "prototypes"])
        self.assertEqual((out["rows"], out["buttons"], out["images"]), (4, 0, 0))
        self.assertIn("4", out["tab"])
        self.assertEqual(out["title"], "<img src=x onerror=alert(1)>")
        self.assertEqual(len(out["links"]), 4, "the host builds the address itself")
        self.assertEqual(out["gone"], {"view": "progress", "hidden": True, "p": "progress"})
        self.assertEqual(out["hostileLinks"], [], "an address of the status that is no http(s) draws no link")
        self.assertEqual(out["controlLinks"][0], "http://127.0.0.1:5142/run-alpha/", "control: an http address of the status draws the links")
        self.assertEqual(len(out["controlLinks"]), 3, "the row whose slug is no slug gets no address built from the status")


# --------------------------------------------------------------------------- the windows, the frame and the focus (the renderer alone)
VIEW = r"""
(async () => {
  const out = { violations };
  const settle = async () => { await tick(); await tick(); await tick(); };
  const status = (change) => { const j = JSON.parse(JSON.stringify(input.fixture)); j.approvals.here = true; j.approvals.viewer = false;
    j.delivery = Object.assign({}, j.delivery, { mode: 'direct', base: 'main' });
    j.prototype = { slug: 'run-alpha', port: 5142, up: true, writing: null, url: 'http://127.0.0.1:5142', served: true, frame: true };
    if (change) change(j); return j; };
  const detail = { schema: 1, slug: 'run-alpha', title: 'A', format: 2, version: 'a'.repeat(64), iteration: 1, writing: null, sourceWrites: true, iterations: [], problems: [], threads: [],
    can: { accept: true }, screens: [{ id: 's1', title: 'One', hash: 'b'.repeat(64), state: 'pending' }, { id: 's2', title: 'Two', hash: 'c'.repeat(64), state: 'pending' }] };
  const open = (opts) => { opts = opts || {};
    const state = { account: { state: 'user', login: 'a@x', name: 'Ola', roles: ['acceptor'], approver: false, mustChange: false, loginAllowed: true }, detail, sent: [] };
    const actions = { loadPrototype: ({ slug }) => Promise.resolve({ ok: true, data: slug ? state.detail : { prototypes: [] } }),
      prototypeAction: (r) => { state.sent.push(r); return Promise.resolve({ ok: true }); }, changePassword: () => Promise.resolve({ ok: true }), login: () => {} };
    const m = mount(status(), { now: Date.parse(input.fixture.generatedAt), actions, deps: opts.noSession ? {} : { session: () => state.account, prototypeFrame: true } });
    const q = (sel) => m.root.querySelector(sel);
    const press = (sel) => q(sel).click();
    const focus = (sel) => { q(sel).dispatchEvent(new Ev('focusin')); };
    const focused = () => { const f = m.root.ownerDocument.focused; if (!f) return null;
      return ['data-proto-manage', 'data-proto-embed', 'data-proto-open', 'data-proto-project', 'data-proto-act', 'data-proto-back', 'data-proto-target', 'data-proto-cancel']
        .filter((a) => f.hasAttribute(a)).map((a) => a + '=' + f.getAttribute(a)).join(' ') + (f.isConnected ? '' : ' detached'); };
    press('#tab-prototypes');
    return { m, state, q, press, focus, focused, frames: () => m.root.querySelectorAll('iframe').length };
  };

  // the word that hands the work over, by the mode of the delivery
  out.word = {};
  for (const [name, mode, noSession] of [['direct', 'direct'], ['pr', 'pr'], ['invalid', 'invalid'], ['noSession', 'pr', true]]) {
    const p = open({ noSession });
    p.m.board.update(status((j) => { j.delivery.mode = mode; }));
    out.word[name] = p.m.root.querySelectorAll('[data-delivery-word]').__asArray().map((x) => [x.getAttribute('data-delivery-word'), text(x)]);
    out.word[name + 'Chip'] = p.m.root.querySelectorAll('[data-delivery]').length;
    p.m.board.destroy();
  }

  // what removes a frame
  out.frame = {};
  const ways = { same: (p) => p.m.board.update(status()), closeDialogs: (p) => p.m.board.closeDialogs(),
    served: (p) => p.m.board.update(status((j) => { j.prototype.served = false; })), frameOff: (p) => p.m.board.update(status((j) => { j.prototype.frame = false; })),
    url: (p) => p.m.board.update(status((j) => { j.prototype.url = 'http://127.0.0.1:5199'; })), password: (p) => p.press('#account button[data-session="password"]') };
  for (const [name, way] of Object.entries(ways)) {
    const p = open();
    const project = () => Boolean(p.q('button[data-proto-project]'));
    const before = { project: project() };
    p.press('button[data-proto-embed="run-alpha"]');
    const framed = { frames: p.frames(), project: project() };
    way(p);
    out.frame[name] = { before, framed, after: { frames: p.frames(), project: project(), password: Boolean(p.q('input[data-pw]')) } };
    p.m.board.destroy();
  }

  // another account or other roles: the view and the window of an action are closed
  out.account = {};
  const changes = { none: {}, login: { login: 'b@x' }, roles: { roles: ['developer'] } };
  for (const [name, change] of Object.entries(changes)) {
    const p = open();
    p.press('button[data-proto-manage="run-alpha"]'); await settle();
    p.press('[data-screen="s1"] button[data-proto-act="accept"]');
    p.state.account = Object.assign({}, p.state.account, change); p.m.board.sessionChanged();
    out.account[name] = { dialog: Boolean(p.q('.dlg-modal')), view: Boolean(p.q('[data-screen="s1"]')), sent: p.state.sent.length };
    p.m.board.destroy();
  }

  // the focus: back to the button after a window, and kept across a refresh of the status
  {
    const p = open();
    p.press('button[data-proto-manage="run-alpha"]'); await settle();
    const accept = '[data-screen="s2"] button[data-proto-act="accept"]';
    p.press(accept); const inWindow = p.focused();
    p.press('button[data-proto-cancel]'); const cancelled = p.focused();
    p.press(accept); p.m.board.handleKey({ key: 'Escape', target: null }); const escaped = p.focused();
    p.press(accept); p.press('button[data-proto-confirm]'); await settle(); const confirmed = p.focused();
    p.press(accept); p.state.detail = Object.assign({}, detail, { screens: detail.screens.slice(0, 1) }); p.press('button[data-proto-confirm]'); await settle(); const gone = p.focused();
    out.dialogFocus = { inWindow, cancelled, escaped, confirmed, gone };
    p.m.board.destroy();
  }
  out.refresh = {};
  for (const sel of ['button[data-proto-manage="run-beta"]', 'button[data-proto-embed="run-alpha"]', 'a[data-proto-open="run-beta"]', 'button[data-proto-project]']) {
    const p = open();
    p.focus(sel); p.m.root.ownerDocument.focused = null;
    p.m.board.update(status()); const kept = p.focused();
    p.q(sel).dispatchEvent(new Ev('focusout')); p.m.root.ownerDocument.focused = null;
    p.m.board.update(status()); const left = p.focused();
    p.focus(sel); p.focus('#tab-prototypes'); p.m.root.ownerDocument.focused = null;
    p.m.board.update(status()); const elsewhere = p.focused();
    out.refresh[sel] = { kept, left, elsewhere };
    p.m.board.destroy();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE_MAJOR >= 18, "set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18")
class ViewBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tail = HARNESS[:HARNESS.index("(async () => {\n  const out = { fixtures: {}")]
        cls.out = run_node(tail + VIEW, {"fixtures": {}, "now": {}, "fixture": fixture_files()["questions"]})

    def test_no_violation_of_the_dom_rules(self):
        self.assertEqual(self.out["violations"], [])

    def test_the_header_names_the_word_that_hands_the_work_over(self):
        word = self.out["word"]
        self.assertEqual(word["direct"], [["CP", "zgoda na oddanie: CP"]])
        self.assertEqual(word["pr"], [["READY", "zgoda na oddanie: READY"]])
        self.assertEqual((word["invalid"], word["noSession"]), ([], []))
        self.assertEqual((word["invalidChip"], word["noSessionChip"]), (1, 1), "control: the chip of the mode is drawn there too")

    def test_a_frame_goes_with_the_page_the_facts_of_the_status_and_the_password_window(self):
        frames = self.out["frame"]
        for name, got in frames.items():
            with self.subTest(way=name):
                self.assertEqual(got["before"], {"project": True}, "control")
                self.assertEqual(got["framed"], {"frames": 1, "project": False}, "no acceptance of the project beside a frame")
                self.assertEqual(got["after"]["frames"], 1 if name == "same" else 0)
        self.assertTrue(frames["password"]["after"]["password"])
        for name in ("closeDialogs", "frameOff", "url", "password"):
            self.assertTrue(frames[name]["after"]["project"], name)

    def test_another_account_closes_the_view_and_the_window(self):
        got = self.out["account"]
        self.assertEqual(got["none"], {"dialog": True, "view": True, "sent": 0}, "control")
        for name in ("login", "roles"):
            self.assertEqual(got[name], {"dialog": False, "view": False, "sent": 0}, name)

    def test_a_closed_window_gives_the_focus_back(self):
        got = self.out["dialogFocus"]
        self.assertEqual(got["inWindow"], "data-proto-cancel=")
        for name in ("cancelled", "escaped", "confirmed"):
            self.assertEqual(got[name], "data-proto-act=accept data-proto-target=s2", name)
        self.assertEqual(got["gone"], "data-proto-back=", "the button no longer exists: the way back to the list")

    def test_a_refresh_of_the_status_keeps_the_focus_of_a_button_of_the_list(self):
        for selector, got in self.out["refresh"].items():
            with self.subTest(button=selector):
                name, value = re.findall(r"\[(data-proto-[a-z]+)(?:=\"([^\"]*)\")?\]", selector)[0]
                self.assertIn(f"{name}={value}", got["kept"] or "")
                self.assertNotIn("detached", got["kept"])
                self.assertEqual((got["left"], got["elsewhere"]), (None, None), "a focus that has left is never taken back")


if __name__ == "__main__":
    unittest.main()
