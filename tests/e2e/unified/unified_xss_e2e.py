"""CORE-2000-UNIFIED U-7, K8 and K21 in a real browser: hostile text of the repository, of a run, of prototypes and of comments runs on
neither origin, and does not approve anything. Negative control: the same payloads run when they are inserted as HTML without CSP.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_xss_e2e.py'
"""
from __future__ import annotations

import html
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, descend, prototype_origin, write)
from unified_helpers import DONE_PLAN, LAYER, PLAN, RUN_ID, SPEC  # noqa: E402

SLUG = "demo"


def payload(field: str) -> str:
    """An `onerror` image and a visible marker `KAN-<field>-`: the marker proves the text reached the page, `__pwn` that it ran."""
    return f"<img src=x onerror=\"__pwn('{field}')\">KAN-{field}-"


# markdown only: forms a renderer of documents could turn into a live link or element
DOC_EXTRAS = "\n".join((
    "<script>__pwn('docscript')</script>",
    "<svg onload=\"__pwn('docsvg')\"></svg>",
    "[KAN-jslink-](javascript:__pwn('jslink'))",
    "[KAN-tablink-](java\tscript:__pwn('tablink'))",
    "[KAN-datalink-](data:text/html,<script>__pwn('datalink')</script>)",
    "[[KAN-nested-](javascript:__pwn('nested'))](../specs/demo-etap-1.md)",
    "<javascript:__pwn('autolink')>",
    "![KAN-image-\" onerror=\"__pwn('image')](x)",
    "[KAN-attr-](x\"onmouseover=\"__pwn('attr') \"t\")",
    "\x00KAN-nul-<img src=x onerror=\"__pwn('nul')\">",
    "`<img src=x onerror=__pwn('code')>` KAN-code-",
    "",
    "| " + payload("cell") + " | b |",
    "|---|---|",
    "| 1 | 2 |",
))

CONTROL = ("<img src=x onerror=\"__pwn('ctlimg')\">", "<svg><animate onbegin=\"__pwn('ctlsvg')\" attributeName=x dur=1s></animate></svg>",
           "<a href=\"javascript:__pwn('ctllink')\">x</a>")
CONTROL_FIELDS = ["ctlimg", "ctlsvg", "ctllink"]


class XssCase(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def hostile_prototype(self) -> Path:
        directory = self.init_prototype(SLUG, title=payload("title")[:120])
        sections = f"""
  <section class="screen" id="s2" aria-labelledby="s2-h">
    <header class="screen-meta">
      <h2 id="s2-h">2. {html.escape(payload('screen'))}</h2>
      <p>{html.escape(payload('lead'))}</p>
    </header>
    <div class="frame">
      <button type="button" data-proto-id="s2-ok" aria-label="{html.escape(payload('label'))}">{html.escape(payload('button'))}</button>
      <button type="button" data-proto-id="{html.escape(payload('protoid'))}">KAN-evilid-</button>
    </div>
  </section>"""
        self.add_screens(directory, sections)
        return directory

    def post_comments(self, server) -> int:
        """Comments with payloads, written by a signed-in commenter through the real API of the prototype origin."""
        page = server.browser()
        port = page.status()["prototype"]["port"]
        self.assertEqual(page.login(COMMENTER, COMMENTER_PASSWORD).status, 200)
        proto = self.handed_over(page, port)
        route = f"/_api/v1/prototypes/{SLUG}/operations"
        before = proto.get(route + "?after=0")
        self.assertEqual(before.status, 200, before.text)
        iteration = before.data.get("iteration")
        iteration = iteration.get("current", 1) if isinstance(iteration, dict) else iteration or 1
        headers = {**proto.base_headers(), "Content-Type": "application/json", "Origin": prototype_origin(port), "X-Skillsync-CSRF": proto.csrf}
        anchor = {"kind": "element", "protoId": "s2-ok", "x": 0.5, "y": 0.5, "iteration": iteration}
        create = {"cid": "c-xss-create-1", "type": "create", "threadId": "th-xss-thread-1",
                  "payload": {"screen": "s2", "anchor": anchor, "message": {"id": "m-xss-message-1", "text": payload("comment")}}}
        reply = proto.post(route, create, headers)
        self.assertEqual(reply.status, 201, reply.text)
        answer = {"cid": "c-xss-reply-1", "type": "reply", "threadId": "th-xss-thread-1",
                  "payload": {"message": {"id": "m-xss-message-2", "text": payload("reply") + " [x](javascript:__pwn('replylink'))"}}}
        reply = proto.post(route, answer, headers)
        self.assertEqual(reply.status, 201, reply.text)
        # a hostile handle cannot even be written: the server refuses it
        bad = {"cid": "c-xss-create-2", "type": "create", "threadId": "th-xss-thread-2",
               "payload": {"screen": "s2", "anchor": {**anchor, "protoId": payload("apiid")}, "message": {"id": "m-xss-message-3", "text": "x"}}}
        refused = proto.post(route, bad, headers)
        self.assertEqual(refused.status, 400, refused.text)
        return port

    def hostile_repository(self) -> None:
        plan = descend(self.project, PLAN)
        text = plan.read_text(encoding="utf-8")
        text = (text.replace("„Moduł przykładowy”", payload("plantitle"))
                    .replace("### Faza 2: API", "### Faza 2: " + payload("phase"))
                    .replace("S2a Reguły (czyste funkcje)", "S2a " + payload("step"))
                    .replace("Opis celu z", payload("goal") + " z"))
        plan.write_text(text + "\n## Uwagi\n\n" + DOC_EXTRAS + "\n", encoding="utf-8")
        spec = descend(self.project, SPEC)
        spec.write_text(spec.read_text(encoding="utf-8") + "\n## Pytania otwarte\n\n- Q1: " + payload("question")
                        + "\n- Q2 [ZAŁOŻENIE]: " + payload("assumption") + " — przyjęte: " + payload("accepted") + "\n\n## Dalej\n\n"
                        + DOC_EXTRAS + "\n", encoding="utf-8")
        done = descend(self.project, DONE_PLAN)
        done.write_text(done.read_text(encoding="utf-8") + "\n\n" + payload("donedoc") + "\n", encoding="utf-8")
        with descend(self.project, f".skillsync/runs/{RUN_ID}/checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- 11:30 zrobione: {payload('checkpoint')}; w toku: {payload('inwork')}; czeka: CP {payload('waits')}\n")
        # a branch name with markup (git allows it; no spaces, so the attribute is separated by a slash)
        branch = "feature/<svg/onload=__pwn(`branch`)>KAN-branch-"
        write(self.project / ".git" / "HEAD", f"ref: refs/heads/{branch}\n")
        # in `packed-refs`: a loose ref would need a file with `<` in its name, which Windows refuses
        write(self.project / ".git" / "packed-refs", f"abc1234def5678 refs/heads/{branch}\n")
        # a consent entry of the run, as an agent could write it
        write(descend(self.project, f".skillsync/runs/{RUN_ID}/approvals.jsonl"), json.dumps({
            "word": "GO plan", "at": "2026-01-10T12:00:00+01:00", "sha": "abc1234def5678", "source": payload("source"),
            "by": payload("by"), "path": PLAN}) + "\n")

    def record_tests(self) -> None:
        report = write(self.tmp / "junit.xml", (
            '<?xml version="1.0" encoding="UTF-8"?><testsuites><testsuite name="{suite}" tests="2" failures="1">'
            '<testcase classname="{cls}" name="{name}" time="0.1"><failure message="{message}">{trace}</failure></testcase>'
            '<testcase classname="k" name="ok" time="0.1"/></testsuite></testsuites>').format(
                **{key: html.escape(payload(field)) for key, field in
                   (("suite", "testsuite"), ("cls", "testclass"), ("name", "testname"), ("message", "testmessage"), ("trace", "testtrace"))}))
        script = write(self.tmp / "fake_tests.py", "import shutil, sys\n"
                       f"shutil.copy({str(report)!r}, 'junit-out.xml')\n"
                       f"print({payload('testoutput')!r})\nsys.exit(1)\n")
        done = self.cli("test-run", str(self.project), "--run", RUN_ID, "--step", "S2a", "--junit",
                        str(self.project / "junit-out.xml"), "--", sys.executable, str(script))
        # `cli` starts in the temporary directory; the command itself runs in the project
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)


class PageOriginXssTests(XssCase):
    def test_hostile_text_never_runs_on_the_status_page(self) -> None:
        self.session_waits_for("CP")
        self.hostile_prototype()
        self.hostile_repository()
        self.record_tests()
        server = self.serve()
        self.post_comments(server)
        approvals = descend(self.project, f".skillsync/runs/{RUN_ID}/approvals.jsonl")
        before = approvals.read_bytes()
        status = server.browser().get("/api/status")
        self.assertEqual(status.status, 200)
        # fields the page of c2890919 shows; prototype names and comments join them when the views of U-4 land
        reached = ["accepted", "assumption", "attr", "branch", "cell", "checkpoint", "code", "datalink", "donedoc", "goal", "image", "inwork",
                   "jslink", "nested", "nul", "phase", "plantitle", "question", "step", "tablink", "testclass", "testmessage", "testname",
                   "testtrace", "waits"]
        found = self.run_script("xss_page", {
            "page": f"http://127.0.0.1:{server.port}", "payloads": list(CONTROL), "controlFields": CONTROL_FIELDS,
            "expected": {"anon": reached, "approver": reached}}, self.start_chrome())
        self.assert_all_pass(found, ("XSS-P1", "XSS-P2", "XSS-P3"))
        self.assertEqual(approvals.read_bytes(), before, "no payload gave a consent")


class PrototypeOriginXssTests(XssCase):
    def test_hostile_text_never_runs_on_the_prototype_origin(self) -> None:
        self.hostile_prototype()
        server = self.serve()
        port = self.post_comments(server)
        done = self.cli("prototype", "writing", str(self.project), SLUG, "start", "--by", payload("writingby")[:80], *LAYER)
        self.assertIn(done.returncode, (0, 2), done.stdout + done.stderr)
        found = self.run_script("xss_proto", {
            "proto": prototype_origin(port), "slug": SLUG, "payloads": list(CONTROL), "controlFields": CONTROL_FIELDS,
            "expected": ["screen", "lead", "button", "label", "evilid", "comment", "reply"], "expectedGallery": ["title"]}, self.start_chrome())
        self.assert_all_pass(found, ("XSS-O1", "XSS-O2", "XSS-O3"))


if __name__ == "__main__":
    unittest.main()
