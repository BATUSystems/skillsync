"""CORE-2000-UNIFIED U-7, K2: what a request without a session gets from the tab of the tests (ANON-4, with its warning of raw results and the
command of a run, ANON-4A…D) and from the origin of the prototypes
(ANON-5: threads, people, the marker of writing, files of a prototype). Canaries are put into every source; each one is looked for in the
answers of the API and in the DOM of a real browser. The signed-in view is the control.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_anonymous_more_e2e.py'
"""
from __future__ import annotations

import html
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, descend, prototype_origin, write  # noqa: E402
from unified_helpers import LAYER, RUN_ID, Browser  # noqa: E402

SLUG = "demo"
# the beginning of an absolute path of a POSIX or Windows machine (`~/x`, `…/x` and `dir/x` are not one)
ABSOLUTE_PATH = r"(?<![\w.~\u2026-])(?:/(?:home|tmp|usr|var|opt|srv|private|Users)/|[A-Za-z]:[\\/])"
APPROVER_NAME = "pseudonim-kanarek-zatwierdzajacy"


def secret(kind: str) -> str:
    """A fake secret put together at run time (secret scanners)."""
    return {"token": "gh" + "p_" + "Kanarek0" * 4 + "abcd", "password": "-".join(("Tajne", "Kanarkowe", "Haslo", "91"))}[kind]


def where(canaries: dict, texts: dict) -> dict:
    """`{canary: [places]}` of the canaries found in the texts of `texts` (`{place: text}`)."""
    found: dict = {}
    for name, value in canaries.items():
        places = sorted(place for place, text in texts.items() if value in text)
        if places:
            found[name] = places
    return found


def leaves(value, path: str = "") -> dict:
    if isinstance(value, dict):
        return {k: v for key, item in value.items() for k, v in leaves(item, f"{path}.{key}" if path else str(key)).items()}
    if isinstance(value, list):
        return {k: v for item in value for k, v in leaves(item, path + "[]").items()} if value else {}
    return {path: str(value)} if value is not None else {}


def flat(name: str, body) -> dict:
    """`{<name>:<json path>: all texts of that path}`."""
    found: dict = {}

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                found[f"{name}:{path}{{key}}"] = found.get(f"{name}:{path}{{key}}", "") + " " + str(key)
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for item in value:
                walk(item, path + "[]")
        elif value is not None:
            found[f"{name}:{path}"] = found.get(f"{name}:{path}", "") + " " + str(value)

    walk(body, "")
    return found


def report(case: str, value: dict) -> None:
    evidence = os.environ.get("SKILLSYNC_E2E_EVIDENCE")
    if evidence:
        write(Path(evidence) / f"{case}.json", json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True))


class AnonymousTestsTab(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, APPROVER_NAME),)

    def test_anon_4_the_tab_of_the_tests(self) -> None:
        self.session_waits_for("CP")
        raw = {"mail-test": "kanarek.test@example.com", "path-test": str(self.project / "kanarek" / "z-testu.txt"),
               "token-test": secret("token"), "password-test": "password=" + secret("password"), "login-in-test": APPROVER}
        fields = {"suite": "KAN-suite- ", "cls": "KAN-class- ", "name": "KAN-name- ", "message": "KAN-message- ", "trace": "KAN-trace- ",
                  "output": "KAN-output- "}
        canaries, values = {}, {}
        for field, mark in fields.items():
            parts = []
            for kind, value in raw.items():   # one marked value per field and kind, so the answer tells which field showed it
                parts.append(f"[{field}:{kind}] {value}")
                canaries[f"{field}:{kind}"] = f"[{field}:{kind}] {value}"
            values[field] = mark + " ; ".join(parts)
        report_file = write(self.tmp / "junit.xml", (
            '<?xml version="1.0" encoding="UTF-8"?><testsuites><testsuite name="{suite}" tests="2" failures="1">'
            '<testcase classname="{cls}" name="{name}" time="0.1"><failure message="{message}">{trace}</failure></testcase>'
            '<testcase classname="k" name="ok" time="0.1"/></testsuite></testsuites>').format(
                **{key: html.escape(values[key]) for key in ("suite", "cls", "name", "message", "trace")}))
        script = write(self.tmp / "fake_tests.py", "import shutil, sys\n"
                       f"shutil.copy({str(report_file)!r}, 'junit-out.xml')\nprint({values['output']!r})\nsys.exit(1)\n")
        done = self.cli("test-run", str(self.project), "--run", RUN_ID, "--step", "S2a", "--junit", str(self.project / "junit-out.xml"),
                        "--", sys.executable, str(script))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        # what the server composes itself: the project, the directory of the run, the account
        composed = {"project": str(self.project), "machine-home": str(self.tmp / "machine"), "approver-login": APPROVER,
                    "approver-account": APPROVER_NAME, "script": str(script)}
        server = self.serve()

        def answers(browser: Browser) -> dict:
            status = browser.get("/api/status")
            self.assertEqual(status.status, 200, status.text)
            texts = flat("status.tests", status.data.get("tests"))
            texts["status:everything"] = status.text
            listed = status.data.get("tests")
            self.assertTrue(listed, "the status names the test run")
            body = browser.get(f"/api/tests?run={RUN_ID}&step=S2a&attempt=1")
            self.assertEqual(body.status, 200, body.text)
            texts.update(flat("api.tests", body.data))
            return texts

        anonymous = server.browser()
        signed = server.browser()
        self.assertEqual(signed.login().status, 200)
        shown, control = answers(anonymous), answers(signed)
        result = {"raw": where(canaries, shown), "composed": where(composed, shown),
                  "control_signed_in": sorted(where(canaries, control))}
        dom = self.run_script("anonymous_dom", {"urls": [f"http://127.0.0.1:{server.port}/"], "tabs": ["tab-tests"],
                                                "canaries": {**canaries, **composed}}, self.start_chrome())
        self.assertEqual(dom["DOM-1"]["status"], "PASS", dom)
        result["dom"] = dom["DOM-1"]["evidence"]["seen"]
        report("ANON-4", result)
        # the server never composes a path or an account into the anonymous answer: a canary of its own fields outside the raw test results
        own = {name: [p for p in places if not any(raw_value in shown.get(p, "") for raw_value in canaries.values()) and p != "status:everything"]
               for name, places in result["composed"].items()}
        self.assertEqual({k: v for k, v in own.items() if v}, {}, "fields composed by the server")
        self.assertTrue(result["control_signed_in"], "control: the canaries reach the tab")

    def test_anon_4_the_warning_of_raw_results_and_the_command(self) -> None:
        """`tests.publicRaw` and its warning only for a reader without a session while the results are not masked; the command line of a run,
        a field the server composes, carries no absolute path for that reader (control: the signed-in one reads it as it was typed)."""
        self.session_waits_for("CP")
        report_file = write(self.tmp / "junit.xml", '<?xml version="1.0" encoding="UTF-8"?><testsuites><testsuite name="s" tests="1" failures="0">'
                                                    '<testcase classname="k" name="ok" time="0.1"/></testsuite></testsuites>')
        script = write(self.tmp / "fake_tests.py", f"import shutil\nshutil.copy({str(report_file)!r}, 'junit-out.xml')\n")
        argument = str(self.project / "kanarek" / "argument.txt")
        done = self.cli("test-run", str(self.project), "--run", RUN_ID, "--step", "S2a", "--junit", str(self.project / "junit-out.xml"),
                        "--", sys.executable, str(script), argument)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        absolute = {"interpreter": sys.executable, "script": str(script), "argument": argument, "temporary": str(self.tmp)}
        inside = os.path.join("kanarek", "argument.txt")   # the path inside the project, in the separators of the machine

        def answers(browser: Browser) -> tuple:
            """`({place: text}` of every `command` of the status, the whole answer of `/api/tests` by the place)."""
            status = browser.get("/api/status")
            body = browser.get(f"/api/tests?run={RUN_ID}&step=S2a&attempt=1")
            self.assertEqual((status.status, body.status), (200, 200), status.text + body.text)
            commands = {place: text for place, text in flat("status.tests", status.data["tests"]).items() if place.endswith("command")}
            self.assertTrue(commands, "the status carries the command of the run")
            listed = flat("api.tests", body.data)
            self.assertIn("api.tests:tests[].name", listed, "the tests of the run are listed")
            return commands, listed

        server = self.serve()
        anonymous, signed = server.browser(), server.browser()
        self.assertEqual(signed.login().status, 200)
        self.assertIs(anonymous.status()["tests"].get("publicRaw"), True)
        self.assertNotIn("publicRaw", signed.status()["tests"])
        (hidden, listed), (shown, _) = answers(anonymous), answers(signed)
        self.assertEqual(where(absolute, {**hidden, **listed}), {}, {**hidden, **listed})
        for place, text in hidden.items():
            self.assertNotRegex(text, ABSOLUTE_PATH, place)
            self.assertIn(inside, text, place)   # the command is there, with the path inside the project
        for place, text in shown.items():
            for name, value in absolute.items():
                self.assertRegex(text, ABSOLUTE_PATH, place)
                self.assertIn(value, text, f"control: {place} of the signed-in session names the {name}")
        port = self.start_chrome()
        page = f"http://127.0.0.1:{server.port}"
        found = self.run_script("anonymous_tests", {"page": page, "masked": False, "absolute": absolute, "inside": inside}, port)
        self.assert_all_pass(found, ("ANON-4A", "ANON-4B", "ANON-4C"))

        server.stop()
        manifest = Path(descend(self.project, ".github/.skillsync.yaml"))
        manifest.write_text(manifest.read_text(encoding="utf-8") + "tests:\n  mask: true\n", encoding="utf-8")
        server = self.serve()
        listed = server.browser().status()["tests"]
        self.assertTrue(listed["sections"], listed)
        self.assertNotIn("publicRaw", listed)
        found = self.run_script("anonymous_tests", {"page": f"http://127.0.0.1:{server.port}", "masked": True, "absolute": absolute,
                                                    "inside": inside}, port)
        self.assert_all_pass(found, ("ANON-4D",))


class AnonymousPrototypes(BrowserCase):
    AUTHOR = "kanarek.autor@example.com"
    ACCOUNTS = ((APPROVER, "approver,acceptor", APPROVER_PASSWORD, APPROVER_NAME), (AUTHOR, "commenter", COMMENTER_PASSWORD, None))

    def test_anon_5_the_origin_of_the_prototypes(self) -> None:
        directory = self.init_prototype(SLUG)
        self.add_screens(directory, '''
  <section class="screen" id="s2" aria-labelledby="s2-h">
    <header class="screen-meta"><h2 id="s2-h">2. Ekran</h2><p>Opis</p></header>
    <div class="frame"><button type="button" data-proto-id="s2-ok">Dalej</button></div>
  </section>''')
        server = self.serve()
        page = server.browser()
        port = page.status()["prototype"]["port"]
        author = server.browser()
        self.assertEqual(author.login(self.AUTHOR, COMMENTER_PASSWORD).status, 200)
        proto = self.handed_over(author, port)
        route = f"/_api/v1/prototypes/{SLUG}/operations"
        before = proto.get(route + "?after=0")
        self.assertEqual(before.status, 200, before.text)
        iteration = before.data.get("iteration")
        iteration = iteration.get("current", 1) if isinstance(iteration, dict) else iteration or 1
        headers = {**proto.base_headers(), "Content-Type": "application/json", "Origin": prototype_origin(port), "X-Skillsync-CSRF": proto.csrf}
        content_mail = "kanarek.wtresci@example.com"
        content_path = "/srv/kanarek/z-komentarza.txt"   # not under the project: found only where the content is shown
        created = proto.post(route, {"cid": "c-anon-create-1", "type": "create", "threadId": "th-anon-thread-1", "payload": {
            "screen": "s2", "anchor": {"kind": "element", "protoId": "s2-ok", "x": 0.5, "y": 0.5, "iteration": iteration},
            "message": {"id": "m-anon-message-1", "text": f"KAN-comment- {content_mail} {content_path}"}}}, headers)
        self.assertEqual(created.status, 201, created.text)
        # a screen accepted on the page by the approver: an operation that carries who and how
        self.assertEqual(page.login().status, 200)
        proto_ops = proto.get(route + "?after=0").data
        body = {"cid": "c-anon-accept-1", "scope": "screen", "screens": {"s2": (proto_ops.get("screens") or {}).get("s2")},
                "version": proto_ops.get("version")}
        reply = page.post(f"/api/prototypes/{SLUG}/accept", body)
        accepted = {"status": reply.status, "code": reply.data.get("code") or reply.data.get("error")}
        writing_mail = "kanarek.pisze@example.com"
        marker = self.cli("prototype", "writing", str(self.project), SLUG, "start", "--by", writing_mail, *LAYER)
        self.assertEqual(marker.returncode, 0, marker.stdout + marker.stderr)

        stored = (directory / "comments.json").read_text(encoding="utf-8")
        users = json.loads(descend(self.project, ".skillsync/prototype/users.json").read_text(encoding="utf-8"))
        hashes = [value for key, value in leaves(users).items() if key.endswith("password")]
        self.assertTrue(hashes)
        canaries = {"author-login": self.AUTHOR, "approver-login": APPROVER, "writing-by": writing_mail, "project-path": str(self.project),
                    "password-approver": APPROVER_PASSWORD, "password-author": COMMENTER_PASSWORD,
                    **{f"password-hash-{n}": value for n, value in enumerate(hashes)},
                    "session-page": author.token, "session-proto": proto.token, "csrf-proto": proto.csrf,
                    "comment-mail": content_mail, "comment-path": content_path}
        local_part = self.AUTHOR.partition("@")[0]

        names = sorted(p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file())
        paths = ["/", "/_experts", f"/{SLUG}/index.html", "/_api/v1/session", route + "?after=0", f"/_api/v1/prototypes/{SLUG}",
                 "/_api/v1/prototypes", "/_experts/" + SLUG] + [f"/{SLUG}/{name}" for name in names]
        outside = ["/users.json", "/.skillsync/prototype/users.json", "/.skillsync/prototype/run/audit.jsonl",
                   f"/{SLUG}/../../../.skillsync/prototype/users.json"]
        anonymous = Browser(port)
        texts, statuses = {}, {}
        for path in paths + outside:
            reply = anonymous.get(path)
            statuses[path] = reply.status
            if reply.status == 200:
                texts[path] = reply.text
        signed = proto.get(route + "?after=0")
        control = where(canaries, {"signed": signed.text, "stored": stored})
        seen = where(canaries, texts)
        dom = self.run_script("anonymous_dom", {"urls": [prototype_origin(port) + "/", f"{prototype_origin(port)}/{SLUG}/index.html",
                                                         prototype_origin(port) + "/_experts"],
                                                "panel": True, "canaries": canaries}, self.start_chrome())
        self.assertEqual(dom["DOM-1"]["status"], "PASS", dom)
        anonymous_ops = texts.get(route + "?after=0", "")
        parsed = json.loads(anonymous_ops or "{}")
        pseudonyms = {"authors": sorted({str(item.get("by")) for item in parsed.get("operations", [])}), "people": parsed.get("people"),
                      "acceptance": parsed.get("acceptance"), "via": sorted({json.dumps((item.get("payload") or {}).get("via")) for item in parsed.get("operations", [])}),
                      "writing": parsed.get("writing"), "session": texts.get("/_api/v1/session"),
                      "email_local_part_visible": local_part in anonymous_ops.replace(content_mail, "")}
        result = {"pseudonyms": pseudonyms, "accept_on_page": accepted, "served": sorted(texts), "statuses": statuses, "seen_api": seen,
                  "seen_dom": dom["DOM-1"]["evidence"]["seen"], "control": sorted(control),
                  "operations_keys": sorted(leaves(json.loads(anonymous_ops or "{}")))}
        report("ANON-5", result)
        self.assertEqual({path: code for path, code in statuses.items() if path in outside and code == 200}, {}, "files outside the prototypes")
        self.assertIn("KAN-comment-", anonymous_ops, "the content of a comment is open")
        allowed = {"comment-mail", "comment-path"}   # content of a comment: open by Q2
        leaked = {k: v for k, v in {**seen, **{f"dom:{k}": v for k, v in result["seen_dom"].items()}}.items() if k.split(":")[-1] not in allowed}
        self.assertEqual(leaked, {}, "an identity, a path or a secret outside the content of a comment")
        self.assertTrue({"author-login"} <= set(control), control)


if __name__ == "__main__":
    unittest.main()
