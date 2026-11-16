"""Secrets never leave the status page in the text of a document or of a run: `/api/doc` masks the whole document before it renders it
(161-F, F1). Later steps of the package add the guard over `/api/status`, `dashboard show` and `skillsync status`."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import test_skillsync_dashboard_server as base  # noqa: E402
from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402

# Environment secret that holds the separators the parsers cut at, plus `*` and a backtick that some of them strip.
ENV_SECRET = "zq9x,wv8y;ut7s*rp6`mn5k"
PATTERN_SECRET = "hunter2hunter2"
ENV_NAME = "STATUSBOARD_TEST_TOKEN"
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

    def test_ordinary_text_and_structure_are_unchanged(self) -> None:
        doc = self.board.doc(base.SPEC)
        self.assertEqual(doc["title"], engine.parse_header((base.FIX / "spec.md").read_text(encoding="utf-8"))[0] or Path(base.SPEC).stem)
        self.assertNotIn("[REDACTED]", doc["html"])


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

    def test_an_expert_question_is_masked_before_it_is_cut_at_160_characters(self) -> None:
        text = "y" * (engine.QUESTION_TEXT_LIMIT - 8) + f" {ENV_SECRET} koniec"   # the secret straddles the cut
        asked = {"Q1": {"text": text, "at": "2026-01-10T10:00:00+01:00", "blocking": True}}
        items = engine._question_items("demo", asked, {})
        self.assert_masked(json.dumps(items, ensure_ascii=False), "expertQuestions.items")


if __name__ == "__main__":
    unittest.main()
