"""Secrets never leave the status page in the text of a document or of a run: `/api/doc` masks the whole document before it renders it
(161-F, F1). Later steps of the package add the guard over `/api/status`, `dashboard show` and `skillsync status`."""
from __future__ import annotations

import codecs
import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import test_skillsync_dashboard_server as base  # noqa: E402
from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import status  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402

# Environment secret that holds the separators the parsers cut at, plus `*` and a backtick that some of them strip.
ENV_SECRET = "zq9x,wv8y;ut7s*rp6`mn5k"
PATTERN_SECRET = "hunter2hunter2"
ENV_NAME = "STATUSBOARD_TEST_TOKEN"
SIMPLE_SECRET = "".join(("Zq9w", "Xk3L", "mNp7"))   # no separators: it can be part of a file name; put together at run time so that no literal looks like a key
DOC_ROOTS = ("docs/specs", "docs/plans", "docs/design", "docs/domain", base.RUN)


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
            target.write_text(secret_text(f"Dokument {directory}"), encoding="utf-8")
            self.rels.append(rel)

    def assert_masked(self, payload: str, where: str) -> None:
        for secret in (PATTERN_SECRET, ENV_SECRET, "zq9x", "wv8y", "ut7s", "rp6", "mn5k"):
            self.assertNotIn(secret, payload, f"{where}: {secret!r}")


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
            (self.root / rel).write_text("bez nagłówka, sam tekst\n", encoding="utf-8")   # no `#` heading: the title falls back to the file name
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
                    (self.root / rel).write_bytes(data)
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


class ReaderLimitsMaskBeforeCutting(base.ProjectCase):
    """F-2: a secret that lies on a byte limit of the reader is masked whole, the cut comes after the masking."""

    LIMITS = (("MAX_DOC_BYTES", engine.MAX_DOC_BYTES), ("spec", 400_000), ("HEADER_BYTES", engine.HEADER_BYTES), ("readiness", 64_000))

    def test_a_secret_on_each_limit_leaves_no_prefix(self) -> None:
        with mock.patch.dict("os.environ", {"STATUSBOARD_TEST_PAT_TOKEN": SIMPLE_SECRET}):
            for name, limit in self.LIMITS:
                for before in (1, 5, 9, 11):   # how many bytes of the secret lie before the limit (the mask is 10 characters long)
                    with self.subTest(limit=name, before=before):
                        path = self.tmp / f"limit-{name}-{before}.md"
                        path.write_text("x" * (limit - before) + SIMPLE_SECRET + " koniec\n" + "y" * 50, encoding="utf-8")
                        text = engine.read_masked(path, limit)
                        self.assertLessEqual(len(text.encode("utf-8")), limit)
                        self.assertFalse(any(text.endswith(SIMPLE_SECRET[:k]) for k in range(2, len(SIMPLE_SECRET) + 1)), text[-15:])   # no prefix of the secret at the cut
                        self.assertNotIn(SIMPLE_SECRET[:4], text)
                        self.assertTrue(text.rstrip().endswith("[REDACTED]"[:before]), text[-12:])   # the cut falls inside the mask (or after it), where the secret began

    def test_a_file_shorter_than_the_limit_is_read_whole(self) -> None:
        path = self.tmp / "short.md"
        path.write_text("# T\n\npassword: " + PATTERN_SECRET + "\n", encoding="utf-8")
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
        ]) + "\n", encoding="utf-8")
        plan = self.root / base.PLAN
        text = plan.read_text(encoding="utf-8").replace("- [ ] S3a API terminów", f"- [ ] S3a API terminów — {ENV_SECRET}")
        plan.write_text(text + f"\n## Wynik\n\n{'x' * 4089} {ENV_SECRET} koniec\n", encoding="utf-8")   # the secret straddles byte 4096 of the section
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
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache")}
        done = subprocess.run([sys.executable, str(base.CLI), "dashboard", "show", str(self.root), "--json", "--layer", f"core={base.ROOT / 'library'}"],
                              capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assert_masked(done.stdout, "dashboard show")

    def test_the_final_pass_of_api_status_is_a_second_layer_that_is_tested(self) -> None:
        """`approvals.jsonl` is not read through the masking reader: only the final `redact(data)` of the route keeps its fields clean."""
        entry = {"word": "GO plan", "at": "2026-01-10T10:30:00+01:00", "sha": "abc1234def5678", "by": f"ktoś password: {PATTERN_SECRET}", "source": "przycisk"}
        (self.root / base.RUN / "approvals.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
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
        plan.write_text(text.replace("## Cel", summary + "## Cel", 1), encoding="utf-8")
        manifest = self.root / srv.MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        env = {**os.environ, "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache")}
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
        for secret in (PATTERN_SECRET, ENV_SECRET, "zq9x", "wv8y", "ut7s", "rp6", "mn5k"):
            self.assertNotIn(secret, payload, f"{where}: {secret!r}")

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
        (run / "approvals.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
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

    def test_error_messages_are_masked_before_the_cut(self) -> None:
        message = status._message(OSError("x " * 245 + f" {ENV_SECRET} password: {PATTERN_SECRET}"), 500)
        self.assert_masked(message, "message")
        self.assertLessEqual(len(message), 500)


if __name__ == "__main__":
    unittest.main()
