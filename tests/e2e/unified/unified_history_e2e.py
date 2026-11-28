"""CORE-2000-UNIFIED U-7: the buttons of the tab "Historia" of a server with several worktrees, clicked in a real browser: archive, bring
back and abandon through the session and CSRF, the audit with the account of the session, the refusals after the file or the session changed.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_history_e2e.py'

The project is a real git repository with one linked worktree (git is required; a missing git is an error).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, descend, write  # noqa: E402
import unified_helpers  # noqa: E402

ACCOUNT = "pseudonim-ap"
OLD = "2026-01-02T12:00:00+00:00"
HOSTILE_TITLE = "<img src=x onerror=\"__pwn('histitle')\">KAN-histitle-"


def plan(title: str, status: str, body: str = "") -> str:
    done = "x" if status in ("done", "archived") else " "
    return f"# Plan: {title}\n\nStatus: {status}\nData: 2026-01-05\n\n## Progress\n\n- [{done}] S1 Krok\n\n{body}\n"


class HistoryTests(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, ACCOUNT), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def git(self, *args: str, date: str | None = None) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "test", "GIT_COMMITTER_NAME": "test",
                    "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_EMAIL": "test@example.com"})
        if date:
            env.update({"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
        done = subprocess.run(["git", *args], cwd=str(self.project), env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, check=False)
        self.assertEqual(done.returncode, 0, f"git {' '.join(args)}: {done.stderr}")

    def test_the_buttons_of_the_history(self) -> None:
        rows = {
            "done": ("docs/plans/2026-01-05-gotowy.md", "Plan gotowy", "done"),
            "archived": ("docs/plans/2026-01-06-zarchiwizowany.md", "Plan z archiwum", "archived"),
            "approved": ("docs/plans/2026-01-07-zatwierdzony.md", "Plan zatwierdzony", "approved"),
            "stale": ("docs/plans/2026-01-08-zmieniany.md", "Plan zmieniany", "done"),
            "session": ("docs/plans/2026-01-09-sesja.md", "Plan sesji", "done"),
            "hostile": ("docs/plans/2026-01-04-wrogi.md", HOSTILE_TITLE, "done"),
        }
        for key, (path, title, status) in rows.items():
            body = "<script>__pwn('histbody')</script> KAN-histbody-" if key == "hostile" else ""
            write(descend(self.project, path), plan(title, status, body))
        # a real repository instead of the stub of the fixture, then a second working copy of the same project
        shutil.rmtree(self.project / ".git")
        write(self.project / ".gitignore", ".skillsync/\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        # an old commit and old files: an `approved` plan is listed in the History only as abandoned (no change for more than 14 days)
        self.git("commit", "-q", "-m", "stan", date=OLD)
        worktree = self.tmp / "wt-drugi"
        self.git("worktree", "add", "-q", "-b", "feature/drugi", str(worktree))
        # the row that belongs to the other copy only: its own plan; `owned` is the copy of an archived plan in the main directory
        owned = "docs/plans/2026-01-06-zarchiwizowany.md"
        own = "docs/plans/2026-01-11-tylko-drugi.md"
        write(descend(worktree, own), plan("Plan tylko w drugim katalogu", "archived"))
        old = datetime.fromisoformat(OLD).timestamp()
        for root in (self.project, worktree):
            for found in (root / "docs" / "plans").glob("*.md"):
                if found.name != Path(own).name:
                    os.utime(found, (old, old))
        context = {}
        for key, (path, title, _status) in rows.items():
            context[key] = {"path": path, "title": title, "file": str(descend(self.project, path))}
        context["archived"] = {"path": own, "title": "Plan tylko w drugim katalogu", "file": str(descend(worktree, own)),
                               "mainCopy": str(descend(self.project, owned)),
                               "audit": str(worktree / ".skillsync" / "audit" / "plan-status.jsonl")}
        # no `--layer`: with it `dashboard serve` is a `standalone` server of one tab; the layer comes from the config of the isolated machine
        library = (unified_helpers.ROOT / "library").as_posix()
        write(Path(self.env["SKILLSYNC_CONFIG"]), json.dumps({
            "layers": [{"id": "core", "kind": "core", "path": library, "source": library}], "layer_rules": [{"match": ["**"], "use": ["core"]}]}))
        with mock.patch.object(unified_helpers, "LAYER", []):
            server = self.serve()
        tabs = server.browser().get("/api/tabs").data["tabs"]
        self.assertEqual(sorted(tab["kind"] for tab in tabs), ["main", "worktree"], tabs)
        found = self.run_script("history", {"page": f"http://127.0.0.1:{server.port}", "account": ACCOUNT, "rows": context,
                                            "audits": [str(root / ".skillsync" / "audit" / "plan-status.jsonl") for root in (self.project, worktree)]},
                                self.start_chrome())
        self.assert_all_pass(found, tuple(f"HIS-{number}" for number in range(1, 9)))
        # the audit of a change lies in the working copy that owns the plan
        self.assertIn("tylko-drugi", (worktree / ".skillsync" / "audit" / "plan-status.jsonl").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
