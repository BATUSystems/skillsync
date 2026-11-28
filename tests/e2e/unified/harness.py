"""Browser end-to-end harness of CORE-2000-UNIFIED (U-7): a real `skillsync dashboard serve`, a headless Chrome of its own and node
scripts that drive it over CDP (`tests/e2e/prototype/lib.mjs`). The fixtures and accounts come from `tests/unified_helpers.py`.

Not part of `unittest discover -s tests`: run it by hand (Linux or macOS, node >= 22, Chrome in `SKILLSYNC_E2E_CHROME` or on PATH):

    python3 -m unittest discover -s tests/e2e/unified -p '*_e2e.py'

Linux, macOS and Windows; Chrome or Edge. A missing browser or node is an ERROR, never a skip. The browser gets a temporary profile and
picks a free debugging port itself (never 9222); only processes started here are ended, by their own PID (POSIX: the process group of our
own browser; Windows: its tree). Passwords go to node through a file of the temporary directory (0600 on POSIX), removed after the script,
and are never printed.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent.parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

os.environ.setdefault("SKILLSYNC_TEST_DASHBOARD_PORTS", "5180-5189")

from serve_start import kill_tree  # noqa: E402
from tmp_paths import descend  # noqa: E402
from unified_helpers import (  # noqa: E402
    APPROVER, APPROVER_PASSWORD, CLI, COMMENTER, COMMENTER_PASSWORD, LAYER, ROOT, Browser, UnifiedCase, http_request, write)

CHROME_NAMES = ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
WINDOWS_BROWSERS = (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe")
WINDOWS_ROOTS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
NODE_TIMEOUT = 240
SOCKET_DIR_LIMIT = 60   # Chrome puts a unix socket under TMPDIR (about 100 characters for the whole path)


def chrome_binary() -> str:
    """`SKILLSYNC_E2E_CHROME`, else the first Chrome or Edge of the usual places."""
    asked = os.environ.get("SKILLSYNC_E2E_CHROME")
    if asked:
        found = shutil.which(asked)
        if not found:
            raise RuntimeError(f"SKILLSYNC_E2E_CHROME names no program: {asked}")
        return found
    names = list(CHROME_NAMES)
    if os.name == "nt":
        names = [os.path.join(os.environ[root], path) for path in WINDOWS_BROWSERS for root in WINDOWS_ROOTS if os.environ.get(root)]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no Chrome or Edge found: set SKILLSYNC_E2E_CHROME")


def prototype_origin(port: int) -> str:
    return f"http://127.0.0.1:{port}"


class BrowserCase(UnifiedCase):
    """One project, one server and one Chrome per test."""

    def setUp(self) -> None:
        super().setUp()
        self.runs = 0

    # --- prototypes

    def init_prototype(self, slug: str, title: str = "Demo") -> Path:
        write(self.project / "docs" / "wymagania.md", "# Wymagania\n\n- WYM-1 Lista\n")
        done = self.cli("prototype", "init", str(self.project), slug, "--requirements", "docs/wymagania.md", "--title", title, *LAYER)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return descend(self.project, f"docs/prototypes/{slug}")

    def add_screens(self, directory: Path, sections: str) -> None:
        """Puts `sections` after the example screen of the template (which is not counted)."""
        page = directory / "index.html"
        text = page.read_text(encoding="utf-8")
        self.assertIn("</main>", text)
        page.write_text(text.replace("</main>", sections + "\n</main>", 1), encoding="utf-8")

    # --- server side clients

    def handed_over(self, page: Browser, port: int) -> Browser:
        """A session of the prototype origin made through the handover, over plain HTTP (the browser flow has its own scenarios)."""
        proto = Browser(port)
        begun = proto.get("/_handoff/begin")
        self.assertEqual(begun.status, 303, begun.text)
        nonce = begun.set_cookie.split(";")[0]
        challenge = (begun.header("Location") or "").partition("challenge=")[2]
        issued = page.post("/_api/v1/handoff", {"challenge": challenge})
        self.assertEqual(issued.status, 200, issued.text)
        done = http_request("127.0.0.1", port, "POST", "/_handoff",
                            {"Origin": prototype_origin(port), "Cookie": nonce, "Content-Type": "application/x-www-form-urlencoded"},
                            ("code=" + quote(issued.data["code"], safe="")).encode("ascii"))
        self.assertEqual(done.status, 303, done.text)
        pairs = [value.split(";")[0] for key, value in done.headers if key.lower() == "set-cookie"]
        proto.cookie = next(pair for pair in pairs if pair.startswith("skillsync_proto_"))
        proto.csrf = proto.get("/_api/v1/session").data.get("csrf")
        return proto

    # --- the browser

    def start_chrome(self) -> int:
        profile = self.tmp / "chrome-profile"
        sandbox = ["--no-sandbox"] if os.environ.get("CI") == "true" else []
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        env = dict(os.environ) if os.name == "nt" else dict(os.environ, TMPDIR=str(self.chrome_scratch()))
        log = (self.tmp / "chrome.log").open("wb")
        with log:
            proc = subprocess.Popen(
                [chrome_binary(), "--headless=new", *sandbox, "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                 f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-gpu",
                 "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                 "--window-size=1440,900", "about:blank"],
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env, **extra)
        self.addCleanup(self.stop_chrome, proc)
        active = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                lines = active.read_text(encoding="utf-8").split()
            except OSError:   # not there yet, or (Windows) still held by Chrome while it writes: read again
                lines = []
            if len(lines) >= 2:
                port = int(lines[0])
                self.assertNotEqual(port, 9222)
                self.record_browser(port)
                return port
            time.sleep(0.1)
        self.fail("Chrome did not start: " + (self.tmp / "chrome.log").read_text(encoding="utf-8", errors="replace")[-600:])

    def chrome_scratch(self) -> Path:
        """TMPDIR of the browser: inside the temporary directory, through a short link when that path is too long (macOS `/var/folders/...`).
        The link lives in `SKILLSYNC_E2E_SHORT_DIR` (default: the system `/tmp`) and is removed with the test."""
        scratch = self.tmp / "ct"
        scratch.mkdir(exist_ok=True)
        if len(str(scratch)) <= SOCKET_DIR_LIMIT:
            return scratch
        base = Path(os.environ.get("SKILLSYNC_E2E_SHORT_DIR") or "/tmp")
        short = Path(tempfile.mkdtemp(prefix="sk", dir=base))
        self.addCleanup(shutil.rmtree, short, True)   # `rmtree` removes the link, not what it points at
        (short / "t").symlink_to(scratch)
        return short / "t"

    def record_browser(self, port: int) -> None:
        """Which browser ran the scenarios, beside their results."""
        evidence = os.environ.get("SKILLSYNC_E2E_EVIDENCE")
        if evidence:
            reply = http_request("127.0.0.1", port, "GET", "/json/version")
            write(Path(evidence) / "browser.json", json.dumps({"browser": reply.data.get("Browser"), "platform": sys.platform}) + "\n")

    def stop_chrome(self, proc) -> None:
        if os.name == "nt":
            kill_tree(proc)   # the tree of OUR browser, by its PID
            proc.wait(timeout=20)
            time.sleep(1.0)   # the files of the profile stay locked for a moment
            return
        # the group of OUR browser (`start_new_session`): its renderers end with it
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.poll() is None:
            proc.wait(timeout=10)
        time.sleep(0.3)   # the helpers of the group leave the profile before it is removed

    def run_script(self, script: str, context: dict, chrome_port: int) -> dict:
        """Runs `<script>.mjs`; returns the `ids` of its last line of stdout (`{ID: {status, evidence}}`)."""
        self.runs += 1
        work = descend(self.tmp, f"node-{self.runs}")
        work.mkdir()
        secrets_file = work / "secrets.json"
        descriptor = os.open(secrets_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"approver": APPROVER_PASSWORD, "commenter": COMMENTER_PASSWORD}, handle)
        env_file = write(work / "env.json", json.dumps({
            "secrets": str(secrets_file), "accounts": {"approver": APPROVER, "commenter": COMMENTER}, "chromePort": chrome_port,
            "ports": [0, 0], "workdir": str(work), "cli": str(CLI), "config": self.env.get("SKILLSYNC_CONFIG", ""),
            "shots": str(work / "shots"), "results": str(work / "results")}))
        try:
            done = subprocess.run(
                ["node", str(HERE / f"{script}.mjs")], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=NODE_TIMEOUT, cwd=str(work), check=False,
                env=dict(os.environ, SKILLSYNC_E2E_ENV=str(env_file), SKILLSYNC_UNIFIED_CTX=json.dumps(context)))
        finally:
            secrets_file.unlink(missing_ok=True)
        last = (done.stdout.strip().splitlines() or [""])[-1]
        self.assertEqual(done.returncode, 0, f"{script}: rc={done.returncode}\n{done.stderr[-2000:]}")
        results = json.loads(last)["ids"]
        evidence = os.environ.get("SKILLSYNC_E2E_EVIDENCE")
        if evidence:
            write(Path(evidence) / f"{script}-{self.id().rsplit('.', 1)[-1]}.json", json.dumps(results, ensure_ascii=False, indent=1))
        return results

    def assert_all_pass(self, results: dict, expected: tuple) -> None:
        self.assertEqual(sorted(results), sorted(expected), "the script reports exactly the planned scenarios")
        failed = {key: value for key, value in results.items() if value["status"] != "PASS"}
        self.assertEqual(failed, {}, json.dumps(failed, ensure_ascii=False, indent=1)[:4000])


__all__ = ["APPROVER", "APPROVER_PASSWORD", "COMMENTER", "COMMENTER_PASSWORD", "ROOT", "BrowserCase", "descend", "prototype_origin", "write"]
