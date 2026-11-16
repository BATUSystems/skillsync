"""180-A: the `tests` field and `GET /api/tests` on BOTH sockets of the two-port mode (the helper of 180-B), same rule as `/api/status`."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from serve_start import start_serve_ports  # noqa: E402
from skillsync_workflow import test_events as ev  # noqa: E402
from test_skillsync_dashboard_server import free_port, make_project  # noqa: E402

CLI = ROOT / "skillsync"
RUN_ID = "2026-01-10-demo-etap-1"


def secret():
    return "-".join(["zq9x", "wv8y", "ut7s", "rp6q"])


class BothSockets(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dash-tests-sockets-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = make_project(self.tmp)
        manifest = self.root / ".github/.skillsync.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("skills: [dashboard]\n", encoding="utf-8")
        path, _ = ev.new_attempt(self.root, RUN_ID, "F1.1")
        w = ev.EventWriter(path)
        w.start(run=RUN_ID, step="F1.1", stepSource="flag", command="tool --token=" + secret(), tool="unittest", pid=os.getpid())
        w.test("mod::a", "a " + secret(), "failed", group="mod", msg="boom " + secret())
        w.end("FAIL", 1, reason="failed " + secret(), counts={"total": 1, "failed": 1})
        w.close()
        self.env = {**os.environ, "DEPLOY_TOKEN": secret(), "SKILLSYNC_CONFIG": str(self.tmp / "machine-config.yaml"), "XDG_CACHE_HOME": str(self.tmp / "xdg-cache"),
                    "XDG_DATA_HOME": str(self.tmp / "xdg-data"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.layer = ["--layer", f"core={ROOT / 'library'}"]

    def start(self):
        proc, read_port, approve_port = start_serve_ports(self, CLI, self.root, self.layer, self.env, free_port, "--read-bind", "127.0.0.1")
        return read_port, approve_port

    def get(self, port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
            return json.load(r)

    def test_the_same_data_and_the_same_masking_on_both_sockets(self) -> None:
        for port in self.start():
            with self.subTest(port=port):
                data = self.get(port, "/api/status")
                attempt = data["tests"]["sections"][0]["attempts"][0]
                test = attempt["groups"][0]["tests"][0]
                self.assertIn(secret(), test["name"])
                self.assertIn(secret(), test["msg"])
                rest = json.dumps({k: v for k, v in data.items() if k != "tests"}, ensure_ascii=False)
                for masked in (attempt["command"], attempt["reason"], rest):
                    self.assertNotIn("zq9x", masked)
                body = self.get(port, f"/api/tests?run={RUN_ID}&step=F1.1&attempt=1&group=mod")
                self.assertIn(secret(), body["tests"][0]["msg"])

    def test_the_route_has_no_write_method_on_either_socket_and_refuses_bad_names(self) -> None:
        for port in self.start():
            with self.subTest(port=port):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/tests", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as post:
                    urllib.request.urlopen(req, timeout=10)
                self.assertIn(post.exception.code, (403, 404, 405))
                for query in (f"run=..&step=F1.1&attempt=1", f"run={RUN_ID}&step=%2e%2e&attempt=1", f"run={RUN_ID}&step=a%5Cb&attempt=1"):
                    with self.assertRaises(urllib.error.HTTPError) as bad:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tests?{query}", timeout=10)
                    self.assertIn(bad.exception.code, (400, 404))

    def test_the_mask_setting_masks_on_both_sockets(self) -> None:
        (self.root / ".github/.skillsync.yaml").write_text("skills: [dashboard]\ntests:\n  mask: true\n", encoding="utf-8")
        for port in self.start():
            with self.subTest(port=port):
                self.assertNotIn("zq9x", json.dumps(self.get(port, "/api/status")["tests"], ensure_ascii=False))
                self.assertNotIn("zq9x", json.dumps(self.get(port, f"/api/tests?run={RUN_ID}&step=F1.1&attempt=1"), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
