"""Secrets never leave the status page in the text of a document or of a run: `/api/doc` masks the whole document before it renders it
(161-F, F1). Later steps of the package add the guard over `/api/status`, `dashboard show` and `skillsync status`."""
from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import home_env  # noqa: E402
from tests import test_skillsync_dashboard_server as base  # noqa: E402
from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import status  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402

# Environment secret that holds the separators the parsers cut at, plus `*` and a backtick that some of them strip.
ENV_SECRET = "zq9x,wv8y;ut7s*rp6`mn5k"
PATTERN_SECRET = "hunter2hunter2"
ENV_NAME = "STATUSBOARD_TEST_TOKEN"
PEM_SECRET = "-----BEGIN KEY-----\nabcdefghijklmnop\n-----END KEY-----"   # a multi-line secret
SIMPLE_SECRET = "".join(("Zq9w", "Xk3L", "mNp7"))   # no separators: it can be part of a file name; put together at run time so that no literal looks like a key
DOC_ROOTS = ("docs/specs", "docs/plans", "docs/design", "docs/domain", base.RUN)
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402


def secret_text(title: str) -> str:
    return f"# {title}\n\nStatus: draft\n\npassword: {PATTERN_SECRET}\n\nzmienna: {ENV_SECRET} koniec\n\n- punkt z `token: {PATTERN_SECRET}` w kodzie\n"


class SecretFiles:
    """Mixin: documents with secrets in every root that `/api/doc` serves, and an environment secret that holds separators."""

    def plant(self) -> None:
        patch = mock.patch.dict("os.environ", {ENV_NAME: ENV_SECRET})
        patch.start()
        self.addCleanup(patch.stop)
        self.rels = []
        for directory in DOC_ROOTS:
            rel = f"{directory}/zzz-secret.md"
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(secret_text(f"Dokument {directory}"), encoding="utf-8", newline="\n")
            self.rels.append(rel)

    def assert_masked(self, payload: str, where: str) -> None:
        assert_secrets_masked(self, payload, where)


def path_forms(*paths) -> tuple[str, ...]:
    """Every spelling of a path that can stand in a payload: native, POSIX, and escaped for JSON (`\\` on Windows)."""
    forms = set()
    for path in paths:
        for text in {str(path), Path(path).as_posix()}:
            forms.update({text, json.dumps(text)[1:-1]})
    return tuple(sorted(forms, key=len, reverse=True))   # the longest first: a parent must not eat a part of a child


def assert_secrets_masked(case, payload: str, where: str) -> None:
    """The whole secrets are searched in the WHOLE payload. The short fragments (3-4 characters: what a parser leaves when it cuts at
    a separator) are searched in the payload WITHOUT the paths of the test's own temporary directory: their names are random (`mkdtemp`)
    and a fragment such as `rp6` turns up in them by chance (CI macOS). A fragment in the text itself is still found."""
    for secret in (PATTERN_SECRET, ENV_SECRET):
        case.assertNotIn(secret, payload, f"{where}: {secret!r}")
    text = payload
    for form in path_forms(*(getattr(case, name) for name in ("tmp", "root") if hasattr(case, name))):
        text = text.replace(form, "<path>")
    for fragment in ("zq9x", "wv8y", "ut7s", "rp6", "mn5k"):
        case.assertNotIn(fragment, text, f"{where}: {fragment!r}")


class DocRouteMasksSecrets(SecretFiles, base.HttpCase):
    def setUp(self) -> None:
        super().setUp()
        self.plant()

    def test_every_document_root_is_masked_in_the_board(self) -> None:
        for rel in self.rels:
            with self.subTest(rel=rel):
                doc = self.board.doc(rel)
                self.assertIsNotNone(doc)
                self.assert_masked(json.dumps(doc, ensure_ascii=False), rel)
                self.assertIn("[REDACTED]", doc["html"])

    def test_the_http_route_masks_every_document_root(self) -> None:
        for rel in self.rels:
            with self.subTest(rel=rel), self.get("/api/doc?path=" + rel) as response:
                payload = response.read().decode("utf-8")
                self.assert_masked(payload, rel)
                self.assertIn("[REDACTED]", payload)

    def test_the_read_socket_masks_the_document_without_a_login(self) -> None:
        port = base.free_port()
        server = srv.make_server(self.board, "127.0.0.1", port, role=srv.ports.ROLE_READ)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for rel in self.rels:
            with self.subTest(rel=rel), urllib.request.urlopen(f"http://127.0.0.1:{port}/api/doc?path={rel}", timeout=5) as response:
                self.assert_masked(response.read().decode("utf-8"), rel)

    def test_the_file_name_is_masked_in_path_and_in_the_fallback_title(self) -> None:
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}):
            rel = f"docs/plans/x-{SIMPLE_SECRET}.md"
            (self.root / rel).write_text("bez nagłówka, sam tekst\n", encoding="utf-8", newline="\n")   # no `#` heading: the title falls back to the file name
            doc = self.board.doc(rel)
            self.assertIsNotNone(doc)
            self.assertNotIn(SIMPLE_SECRET, json.dumps(doc, ensure_ascii=False))
            with self.get("/api/doc?path=" + urllib.parse.quote(rel)) as response:
                self.assertNotIn(SIMPLE_SECRET, response.read().decode("utf-8"))

    def test_utf16_documents_and_nul_characters_do_not_hide_a_secret(self) -> None:
        text = f"# Raport\n\nwartość {SIMPLE_SECRET} koniec\npassword: {PATTERN_SECRET}\n"
        samples = {
            "utf16-le-bom": codecs.BOM_UTF16_LE + text.encode("utf-16-le"),
            "utf16-be-bom": codecs.BOM_UTF16_BE + text.encode("utf-16-be"),
            "nul-inside": text.replace(SIMPLE_SECRET, SIMPLE_SECRET[:5] + "\x00" + SIMPLE_SECRET[5:]).encode("utf-8"),
            "nul-between-every-character": "\x00".join(text).encode("utf-8"),
        }
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}):
            for name, data in samples.items():
                with self.subTest(name):
                    rel = f"docs/plans/zzz-{name}.md"
                    descend(self.root, rel).write_bytes(data)
                    doc = self.board.doc(rel)
                    payload = json.dumps(doc, ensure_ascii=False)
                    self.assertNotIn(SIMPLE_SECRET, payload)
                    self.assertNotIn(PATTERN_SECRET, payload)
                    self.assertIn("[REDACTED]", payload)
                    if name.startswith("utf16"):
                        self.assertEqual(doc["title"], "Raport")   # decoded as UTF-16, not as a string of NUL-separated bytes

    def test_ordinary_text_and_structure_are_unchanged(self) -> None:
        doc = self.board.doc(base.SPEC)
        self.assertEqual(doc["title"], engine.parse_header((base.FIX / "spec.md").read_text(encoding="utf-8"))[0] or Path(base.SPEC).stem)
        self.assertNotIn("[REDACTED]", doc["html"])


class ReaderDecodingAndMasking(base.ProjectCase):
    """`decode_text` and `read_masked`: UTF-16 with a byte order mark, NUL characters, Windows line ends, invalid bytes (161-F, I5, D8, D9)."""

    TEXT = f"# Plan\n\nStatus: draft\n\nwartość {SIMPLE_SECRET} koniec\n"

    def env(self):
        patch = mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET, "STATUSBOARD_TEST_KEY_TOKEN": PEM_SECRET})
        patch.start()
        self.addCleanup(patch.stop)

    def test_decode_text_directly(self) -> None:
        text = "zażółć gęślą jaźń\n"
        self.assertEqual(engine.decode_text(codecs.BOM_UTF16_LE + text.encode("utf-16-le")), text)
        self.assertEqual(engine.decode_text(codecs.BOM_UTF16_BE + text.encode("utf-16-be")), text)
        self.assertEqual(engine.decode_text(text.encode("utf-8")), text)   # ordinary UTF-8 is unchanged
        self.assertEqual(engine.decode_text("a\x00b\x00c".encode("utf-8")), "abc")   # NUL dropped
        self.assertEqual(engine.decode_text(b"ok \xff\xfe bad"), "ok \ufffd\ufffd bad")   # invalid bytes become the replacement character, no exception
        self.assertEqual(engine.decode_text(b""), "")
        self.assertEqual(engine.decode_text(codecs.BOM_UTF16_LE + b"a"), "\ufffd")   # a lone byte after the mark does not raise

    def test_read_masked_masks_utf16_and_nul_split_secrets_in_the_reader_itself(self) -> None:
        self.env()
        samples = {"utf16-le": codecs.BOM_UTF16_LE + self.TEXT.encode("utf-16-le"), "utf16-be": codecs.BOM_UTF16_BE + self.TEXT.encode("utf-16-be"),
                   "nul-inside": self.TEXT.replace(SIMPLE_SECRET, SIMPLE_SECRET[:5] + "\x00" + SIMPLE_SECRET[5:]).encode("utf-8")}
        for name, data in samples.items():
            with self.subTest(name):
                path = child(self.tmp, f"{name}.md")
                path.write_bytes(data)
                text = engine.read_masked(path)   # the first layer alone: no second pass here
                self.assertNotIn(SIMPLE_SECRET, text)
                self.assertNotIn("\x00", text)
                self.assertIn("[REDACTED]", text)
                self.assertTrue(text.startswith("# Plan"), text[:20])

    def test_a_multi_line_secret_in_a_crlf_file_is_masked_by_the_reader_and_the_doc_route(self) -> None:
        self.env()
        text = f"# Klucz\r\n\r\n{PEM_SECRET.replace(chr(10), chr(13) + chr(10))}\r\nkoniec\r\n"
        rel = "docs/plans/zzz-crlf.md"
        (self.root / rel).write_bytes(text.encode("utf-8"))
        self.assertNotIn("abcdefghijklmnop", engine.read_masked(self.root / rel))
        doc = self.board.doc(rel)
        self.assertNotIn("abcdefghijklmnop", json.dumps(doc, ensure_ascii=False))
        self.assertNotIn("\x00", json.dumps(doc, ensure_ascii=False))

    def test_status_reads_a_utf16_plan_and_masks_nul_split_checkpoints(self) -> None:
        self.env()
        plan = self.root / base.PLAN
        text = plan.read_text(encoding="utf-8")
        before = self.board.status()["progress"]["total"]
        plan.write_bytes(codecs.BOM_UTF16_LE + text.replace("S4 Dokumentacja", f"S4 Dokumentacja {SIMPLE_SECRET}").encode("utf-16-le"))
        checkpoints = self.root / base.RUN / "checkpoints.md"
        glued = SIMPLE_SECRET[:4] + "\x00" + SIMPLE_SECRET[4:]
        checkpoints.write_text(checkpoints.read_text(encoding="utf-8") + f"- 11:30 w toku: S2b; czeka: GO plan {glued}\n", encoding="utf-8", newline="\n")
        board = base.srv.Board(self.root, prototype_port=1)
        board.prototype_acceptance = lambda slug: None
        data = board.status()
        payload = json.dumps(data, ensure_ascii=False)
        self.assertEqual(data["progress"]["total"], before)   # the UTF-16 plan still parses into the same steps
        self.assertNotIn(SIMPLE_SECRET, payload)
        self.assertNotIn(SIMPLE_SECRET[:4], payload.replace("[REDACTED]", ""))
        self.assertNotIn("\x00", payload)

    def test_skillsync_status_reads_plan_and_spec_through_the_decoder(self) -> None:
        self.env()
        for folder, name in (("plans", "zzz-nul.md"), ("specs", "zzz-nul.md")):
            path = self.root / "docs" / folder / name
            path.write_bytes(f"# Plan: x\n\nStatus: draft \u2014 {SIMPLE_SECRET[:4]}\x00{SIMPLE_SECRET[4:]}\n".encode("utf-8"))
        record = status.plan_record(self.root, self.root / "docs/plans/zzz-nul.md")
        self.assertNotIn(SIMPLE_SECRET, json.dumps(record, ensure_ascii=False))
        self.assertNotIn(SIMPLE_SECRET[:4], json.dumps(record, ensure_ascii=False).replace("[REDACTED]", ""))
        self.assertNotIn("\x00", json.dumps(record, ensure_ascii=False))
        specs = status.spec_records(self.root) if hasattr(status, "spec_records") else None
        if specs is not None:
            self.assertNotIn(SIMPLE_SECRET[:4], json.dumps(specs, ensure_ascii=False).replace("[REDACTED]", ""))


class ApproveRefusalMessageIsMasked(base.ProjectCase):
    """D9: the 409 text of a refused status change is masked before it is cut at 300 characters."""
    SPEC2 = "docs/specs/demo-etap-2.md"
    PLAN2 = "docs/plans/2026-01-20-demo-etap-2.md"
    spec_act = base.ApprovalTests.spec_act
    actions = base.ApprovalTests.actions

    def test_the_refusal_does_not_show_a_prefix_of_a_secret_on_the_cut(self) -> None:
        run = self.root / ".skillsync/runs/2026-01-20-demo-etap-2"
        run.mkdir(parents=True)
        self.spec_act()
        offer = self.actions()["spec"]
        error = ValueError("x " * 145 + SIMPLE_SECRET)   # the secret begins just before character 300
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}), \
                mock.patch.object(srv.status_core, "set_doc_status", side_effect=error):
            code, body = self.board.approve("spec", self.SPEC2, offer["sha"])
        self.assertEqual(code, 409, body)
        self.assertIn("Odmowa zmiany statusu", body["error"])
        self.assertNotIn(SIMPLE_SECRET[:4], body["error"])
        self.assertNotIn(SIMPLE_SECRET[:4], body["error"][-12:])


class ReaderLimitsMaskBeforeCutting(base.ProjectCase):
    """F-2: a secret that lies on a byte limit of the reader is masked whole, the cut comes after the masking."""

    LIMITS = (("MAX_DOC_BYTES", engine.MAX_DOC_BYTES), ("spec", 400_000), ("HEADER_BYTES", engine.HEADER_BYTES), ("readiness", 64_000))

    def test_a_secret_on_each_limit_leaves_no_prefix(self) -> None:
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}):
            for name, limit in self.LIMITS:
                for before in (1, 5, 9, 11):   # how many bytes of the secret lie before the limit (the mask is 10 characters long)
                    with self.subTest(limit=name, before=before):
                        path = child(self.tmp, f"limit-{name}-{before}.md")
                        path.write_text("x" * (limit - before) + SIMPLE_SECRET + " koniec\n" + "y" * 50, encoding="utf-8", newline="\n")
                        text = engine.read_masked(path, limit)
                        self.assertLessEqual(len(text.encode("utf-8")), limit)
                        self.assertFalse(any(text.endswith(SIMPLE_SECRET[:k]) for k in range(2, len(SIMPLE_SECRET) + 1)), text[-15:])   # no prefix of the secret at the cut
                        self.assertNotIn(SIMPLE_SECRET[:4], text)
                        self.assertTrue(text.rstrip().endswith("[REDACTED]"[:before]), text[-12:])   # the cut falls inside the mask (or after it), where the secret began

    def test_a_file_shorter_than_the_limit_is_read_whole(self) -> None:
        path = self.tmp / "short.md"
        path.write_text("# T\n\npassword: " + PATTERN_SECRET + "\n", encoding="utf-8", newline="\n")
        self.assertEqual(engine.read_masked(path, 1000), "# T\n\npassword: [REDACTED]\n")


class StatusSecretsGuard(SecretFiles, base.HttpCase):
    """The guard of 161-F: secrets planted on the boundaries where the parsers cut, split or trim text (a segment `;`, an element `,`, a
    note ` — `, the 4 KiB of `result`, the 160 characters of an expert question) never reach `/api/status`, `/api/doc` or `dashboard show`.
    `board.status()` is checked BEFORE the final `redact(data)`: the text is masked at the source, the final pass is only the second layer."""

    def setUp(self) -> None:
        super().setUp()
        self.plant()
        checkpoints = self.root / base.RUN / "checkpoints.md"
        checkpoints.write_text(checkpoints.read_text(encoding="utf-8") + "\n".join([
            f"- 11:00 w toku: F1.1 (a; b); czeka: GO plan {ENV_SECRET}",   # an older wait: the newest line below replaces it
            f"- 11:01 w toku: F1.2 opis {ENV_SECRET}, F1.3 (x; {ENV_SECRET}, F1.4",
            f"- 11:02 zrobione: F0.1; eta: bieg {ENV_SECRET} do 17:30; trwa: plan",
            f"- 11:03 w toku: F1.5; ryzyko: password: {PATTERN_SECRET} (a; token: {PATTERN_SECRET}; czeka: GO plan {ENV_SECRET}",   # the newest line: it sets `waiting`
        ]) + "\n", encoding="utf-8", newline="\n")
        plan = self.root / base.PLAN
        text = plan.read_text(encoding="utf-8").replace("- [ ] S3a API terminów", f"- [ ] S3a API terminów — {ENV_SECRET}")
        plan.write_text(text + f"\n## Wynik\n\n{'x' * 4089} {ENV_SECRET} koniec\n", encoding="utf-8", newline="\n")   # the secret straddles byte 4096 of the section
        self.board.stop.set()   # the board of the HttpCase watches the old content: a fresh one below reads the planted files
        self.fresh = base.srv.Board(self.root, prototype_port=1)
        self.fresh.prototype_acceptance = lambda slug: None

    def test_the_status_is_masked_at_the_source_before_the_final_pass(self) -> None:
        data = self.fresh.status()
        self.assert_masked(json.dumps(data, ensure_ascii=False), "board.status()")
        waiting = [item["what"] for item in data["waiting"]]
        self.assertTrue(any(what.startswith("GO plan") for what in waiting), waiting)   # the segment is still read, with the mask in place of the secret
        self.assertTrue(data["result"]["text"].endswith("[REDAC"), data["result"]["text"][-20:])   # the mask, cut by the 4 KiB limit, where the secret began

    def test_api_status_and_the_documents_of_the_route_are_masked(self) -> None:
        with self.get("/api/status") as response:
            self.assert_masked(response.read().decode("utf-8"), "/api/status")
        for rel in (base.PLAN, base.SPEC, *self.rels):
            with self.get("/api/doc?path=" + rel) as response:
                self.assert_masked(response.read().decode("utf-8"), "/api/doc " + rel)

    def test_dashboard_show_is_masked(self) -> None:
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8", newline="\n")
        env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), **home_env(self.tmp)}
        done = subprocess.run([sys.executable, str(base.CLI), "dashboard", "show", str(self.root), "--json", "--layer", f"core={base.ROOT / 'library'}"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_masked(done.stdout, "dashboard show")

    def test_the_final_pass_of_api_status_is_a_second_layer_that_is_tested(self) -> None:
        """`approvals.jsonl` is not read through the masking reader: only the final `redact(data)` of the route keeps its fields clean."""
        entry = {"word": "GO plan", "at": "2026-01-10T10:30:00+01:00", "sha": "abc1234def5678", "by": f"ktoś password: {PATTERN_SECRET}", "source": "przycisk"}
        (self.root / base.RUN / "approvals.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8", newline="\n")
        board = base.srv.Board(self.root, prototype_port=1)
        board.prototype_acceptance = lambda slug: None
        self.assertIn(PATTERN_SECRET, json.dumps(board.status(), ensure_ascii=False))   # the source layer does not see this field ...
        self.board.stop.set()
        with self.get("/api/status") as response:
            self.assertNotIn(PATTERN_SECRET, response.read().decode("utf-8"))   # ... the route masks it: removing that pass fails here

    def test_skillsync_status_is_masked(self) -> None:
        plan = self.root / base.PLAN
        text = plan.read_text(encoding="utf-8").replace("Status: in_progress", f"Status: in_progress — {ENV_SECRET}", 1)
        summary = f"## Streszczenie dla człowieka\n{'z' * 3990} {ENV_SECRET} koniec\npassword: {PATTERN_SECRET}\n\n"
        plan.write_text(text.replace("## Cel", summary + "## Cel", 1), encoding="utf-8", newline="\n")
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8", newline="\n")
        env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), **home_env(self.tmp)}
        done = subprocess.run([sys.executable, str(base.CLI), "status", str(self.root), "--json", "--layer", f"core={base.ROOT / 'library'}"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertIn(done.returncode, (0, 1, 2), done.stdout + done.stderr)
        self.assert_masked(done.stdout, "skillsync status")
        self.assertIn('"summary"', done.stdout)

    def test_an_expert_question_is_masked_before_it_is_cut_at_160_characters(self) -> None:
        text = "y" * (engine.QUESTION_TEXT_LIMIT - 8) + f" {ENV_SECRET} koniec"   # the secret straddles the cut
        asked = {"Q1": {"text": text, "at": "2026-01-10T10:00:00+01:00", "blocking": True}}
        items = engine._question_items("demo", asked, {})
        self.assert_masked(json.dumps(items, ensure_ascii=False), "expertQuestions.items")


class StatusModuleMasksBeforeCutting(base.ProjectCase):
    """F4: `status._status_fields`, `plan_summary` and the error messages mask the whole text before they strip markers, cut or collapse it."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.dict("os.environ", {ENV_NAME: ENV_SECRET})
        patch.start()
        self.addCleanup(patch.stop)

    def assert_masked(self, payload: str, where: str) -> None:
        assert_secrets_masked(self, payload, where)

    def test_status_text_is_masked_before_the_markers_are_stripped_and_the_text_cut(self) -> None:
        for name, line in (("secret holds * and a backtick", f"Status: draft — {ENV_SECRET}"),
                           ("secret on the 80 character cut", "Status: draft — " + "w" * 58 + f" {ENV_SECRET}"),
                           ("key before the cut", f"Status: draft — password: {PATTERN_SECRET} (a; b)")):
            with self.subTest(name):
                word, text = status._status_fields(f"# Plan\n\n{line}\n", {"draft"})
                self.assertEqual(word, "draft")
                self.assert_masked(text or "", name)
        word, text = status._status_fields("x" * 3990 + f"\nStatus: draft — {ENV_SECRET}\n", {"draft"})   # the status line itself straddles the 4000-character window
        self.assert_masked(text or "", "window")

    def test_plan_summary_is_masked_before_the_section_is_cut_and_trimmed(self) -> None:
        for name, body in (("char limit", "x" * (status.SUMMARY_MAX_CHARS - 6) + f" {ENV_SECRET} koniec"),
                           ("line limit", "\n".join(f"l{n}" for n in range(status.SUMMARY_MAX_LINES - 1)) + f"\n{ENV_SECRET} i\nreszta"),
                           ("heading marker", f"# {ENV_SECRET}"),
                           ("key before the cut", f"opis\npassword: {PATTERN_SECRET}\n" + "y" * 5000)):
            with self.subTest(name):
                summary = status.plan_summary(f"# Plan: x\n\nStatus: draft\n\n## Streszczenie dla człowieka\n{body}\n\n## Cel\nc\n")
                self.assertIsNotNone(summary)
                self.assert_masked(summary, name)

    def test_approval_fields_are_masked_before_they_are_cut(self) -> None:
        run = self.root / base.RUN
        entry = {"word": "GO plan", "at": "2026-01-10T10:30:00+01:00", "sha": "abc1234def5678", "by": "ktoś",
                 "source": "s" * 36 + SIMPLE_SECRET, "file": "f" * 295 + SIMPLE_SECRET}
        (run / "approvals.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8", newline="\n")
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}):
            rows = status.read_approvals(self.root)
            self.assertTrue(rows)
            for row in rows:
                for field in ("source", "file"):
                    self.assertNotIn(SIMPLE_SECRET[:4], row[field], field)   # no prefix of the secret at the cut
            record = status.record_approval(self.root, "CP", run.name, sha="0123456789abcdef", by="b" * 75 + SIMPLE_SECRET, source="cli")
            self.assertEqual(record["status"], "PASS", record)
            written = (run / "approvals.jsonl").read_text(encoding="utf-8").splitlines()[-1]   # the line the writer appended (the first one is the planted entry)
            self.assertNotIn(SIMPLE_SECRET[:4], written)

    def test_skillsync_status_decodes_utf16_and_keeps_invalid_utf8_a_finding(self) -> None:
        """`skillsync status` reads UTF-16 with a byte order mark as text, but bytes that are not valid UTF-8 stay a finding without a row (the contract of the IDE consumer)."""
        for folder, kind in (("plans", "Plan"), ("specs", "Spec")):
            text = f"# {kind}: zażółć\nStatus: draft\n"
            (self.root / "docs" / folder / "zzz-utf16-le.md").write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
            (self.root / "docs" / folder / "zzz-utf16-be.md").write_bytes(codecs.BOM_UTF16_BE + text.encode("utf-16-be"))
            (self.root / "docs" / folder / "zzz-bad.md").write_bytes(b"# Z\xff\nStatus: draft\n")
        for name, read in (("plans", status._plans), ("specs", status._specs)):
            with self.subTest(name):
                findings: list[str] = []
                rows = read(self.root, findings)
                by_name = {Path(row["path"]).name: row for row in rows}
                for decoded in ("zzz-utf16-le.md", "zzz-utf16-be.md"):
                    self.assertEqual((by_name[decoded]["title"], by_name[decoded]["status"]), (f"{name[:-1].capitalize()}: zażółć", "draft"), decoded)
                self.assertNotIn("zzz-bad.md", by_name)
                self.assertTrue(any("zzz-bad.md" in finding and "UnicodeDecodeError" in finding for finding in findings), findings)

    def test_dashboard_blocked_reasons_are_masked_before_the_cut(self) -> None:
        error = ValueError("x " * 245 + f"{SIMPLE_SECRET}")
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}), mock.patch.object(engine, "read_project", side_effect=error):
            for command in (engine._cmd_show, engine._cmd_metrics):
                with self.subTest(command=command.__name__), mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                    self.assertEqual(command(SimpleNamespace(path=str(self.root), plan=None, run_id=None)), 2)
                    self.assertNotIn(SIMPLE_SECRET[:4], out.getvalue())

    def test_error_messages_are_masked_before_the_cut(self) -> None:
        message = status._message(OSError("x " * 245 + f" {ENV_SECRET} password: {PATTERN_SECRET}"), 500)
        self.assert_masked(message, "message")
        self.assertLessEqual(len(message), 500)


class FragmentCheckIgnoresRandomPaths(unittest.TestCase):
    """Regression (CI macOS): the random name of the temporary directory held `rp6` and failed `test_dashboard_show_is_masked`."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.gettempdir()) / "status-board-yn0drp6x"   # a name with a fragment of the secret, like `mkdtemp` can give
        self.root = self.tmp / "projekt"

    def test_a_fragment_inside_the_temporary_path_is_not_a_leak(self) -> None:
        for path in (self.root, self.tmp):
            for form in path_forms(path):
                assert_secrets_masked(self, '{"root": "%s", "plan": "%s/docs/plans/a.md"}' % (form, form), "path only")

    def test_a_fragment_in_the_text_or_the_whole_secret_is_still_found(self) -> None:
        here = path_forms(self.root)[0]
        for leak in (f'{{"root": "{here}", "text": "zmienna: rp6 koniec"}}', f'{{"root": "{here}", "text": "{ENV_SECRET}"}}',
                     f'{{"root": "{here}", "text": "{PATTERN_SECRET}"}}', f'{{"root": "{here}", "text": "mn5k"}}',
                     '{"path": "elsewhere/rp6"}'):
            with self.subTest(leak=leak), self.assertRaises(AssertionError):
                assert_secrets_masked(self, leak, "leak")


if __name__ == "__main__":
    unittest.main()
