"""CORE-2000-UNIFIED U-7, K2 in a real browser: the page without a session shows no e-mail address, no absolute path and no author of a
consent, in any view, tooltip or document; the signed-in page shows them (control), and a sign-out removes them from the open page.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_anonymous_e2e.py'
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, BrowserCase, descend, write  # noqa: E402
from unified_helpers import PLAN, RUN_ID, SPEC  # noqa: E402

BY = "-".join(("pseudonim", "autora", "zgody", "73"))


class AnonymousTests(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"),)

    def test_the_anonymous_page_hides_people_and_paths(self) -> None:
        addresses = {name: f"kanarek.{name}@example.com" for name in ("plan", "spec", "checkpoint", "question")}
        paths = {name: str(self.project / "kanarek" / f"plik-{name}.txt") for name in ("plan", "spec", "checkpoint")}
        self.session_waits_for("CP")
        with descend(self.project, PLAN).open("a", encoding="utf-8") as handle:
            handle.write(f"\n## Uwagi\n\nKAN-plandoc- pisze {addresses['plan']} o `{paths['plan']}`.\n")
        with descend(self.project, SPEC).open("a", encoding="utf-8") as handle:
            handle.write(f"\n## Pytania otwarte\n\n- Q1: KAN-question- czy {addresses['question']} widzi?\n\n## Dalej\n\n"
                         f"KAN-specdoc- {addresses['spec']} w {paths['spec']}\n")
        with descend(self.project, f".skillsync/runs/{RUN_ID}/checkpoints.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- 11:30 zrobione: KAN-checkpoint- {addresses['checkpoint']} {paths['checkpoint']}; w toku: —; czeka: CP\n")
        write(descend(self.project, f".skillsync/runs/{RUN_ID}/approvals.jsonl"), json.dumps({
            "word": "GO plan", "at": "2026-01-10T12:00:00+01:00", "sha": "abc1234def5678", "source": "dashboard", "by": BY,
            "role": "approver", "path": PLAN, "worktree": str(self.project)}) + "\n")
        server = self.serve()
        forbidden = {**{f"mail-{name}": value for name, value in addresses.items()}, **{f"path-{name}": value for name, value in paths.items()},
                     "project": str(self.project), "home": str(self.tmp), "login": APPROVER, "approval-by": BY}
        found = self.run_script("anonymous", {
            "page": f"http://127.0.0.1:{server.port}", "forbidden": forbidden,
            "control": ["mail-plan", "mail-spec", "path-plan", "path-spec", "mail-checkpoint", "project"],
            "reached": ["plandoc", "specdoc", "checkpoint", "question"]}, self.start_chrome())
        self.assert_all_pass(found, ("ANON-1", "ANON-2", "ANON-3"))


if __name__ == "__main__":
    unittest.main()
