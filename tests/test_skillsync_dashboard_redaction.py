"""Secrets never leave the status page in the text of a document or of a run: `/api/doc` masks the whole document before it renders it
(161-F, F1). Later steps of the package add the guard over `/api/status`, `dashboard show` and `skillsync status`."""
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
