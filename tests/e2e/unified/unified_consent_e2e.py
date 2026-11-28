"""CORE-2000-UNIFIED U-7: the consents `CP`, `READY`, `GO plan` and `GO implement` clicked in a real browser by an `approver`: the window with
the decision, its refusals when what the human saw has changed, a sign-out elsewhere, an account without the role.

    python3 -m unittest discover -s tests/e2e/unified -p 'unified_consent_e2e.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import APPROVER, APPROVER_PASSWORD, COMMENTER, COMMENTER_PASSWORD, BrowserCase, descend, write  # noqa: E402
from unified_helpers import BRANCH, HEAD, HEAD_NEXT, PLAN2, RUN2_ID, RUN_ID, SPEC2  # noqa: E402

OTHER_BRANCH = "feature/inna"


class ConsentCase(BrowserCase):
    ACCOUNTS = ((APPROVER, "approver", APPROVER_PASSWORD, "pseudonim-ap"), (COMMENTER, "commenter", COMMENTER_PASSWORD, None))

    def clicks(self, kind: str, word: str, changes: list, run: str = RUN_ID, **context) -> dict:
        server = self.serve()
        approvals = descend(self.project, f".skillsync/runs/{run}/approvals.jsonl")
        found = self.run_script("consent", {"page": f"http://127.0.0.1:{server.port}", "kind": kind, "word": word, "approvals": str(approvals),
                                            "change": changes, **context}, self.start_chrome())
        count = 5 + 2 * len(changes)
        self.assert_all_pass(found, tuple(f"{word}-{number}" for number in range(1, count + 1)))
        self.assertEqual(len(self.rows(run)), 1, self.rows(run))
        return found

    def ref(self, branch: str = BRANCH) -> str:
        return str(descend(self.project, f".git/refs/heads/{branch}"))

    def manifest(self) -> str:
        return str(descend(self.project, ".github/.skillsync.yaml"))

    def head_changes(self) -> list:
        write(self.project / ".git" / "refs" / "heads" / OTHER_BRANCH, HEAD + "\n")   # the same commit under another name
        return [{"id": "nowy HEAD", "files": {self.ref(): HEAD_NEXT + "\n"}, "code": "headChanged"},
                {"id": "inna gałąź", "files": {str(self.project / ".git" / "HEAD"): f"ref: refs/heads/{OTHER_BRANCH}\n"}, "code": "delivery_changed"}]


class CpTests(ConsentCase):
    def test_cp_in_direct_mode(self) -> None:
        self.session_waits_for("CP")
        self.clicks("cp", "CP", self.head_changes(), shown={"sha": HEAD[:7], "target": BRANCH, "mode": "direct"})
        row, = self.rows()
        self.assertEqual((row["sha"], row["mode"], row["target"], row["branch"]), (HEAD, "direct", BRANCH, BRANCH))


class ReadyTests(ConsentCase):
    def test_ready_in_pr_mode(self) -> None:
        self.set_delivery(self.project, "pr")
        self.session_waits_for("READY")
        self.clicks("ready", "READY", [*self.head_changes(), self.base_change()],
                    shown={"sha": HEAD[:7], "target": "main", "branch": BRANCH, "mode": "pr"}, labels=["Baza PR (cel)"])
        row, = self.rows()
        self.assertEqual((row["sha"], row["mode"], row.get("target"), row["branch"]), (HEAD, "pr", None, BRANCH))

    def base_change(self) -> dict:
        """The base of the PR (`delivery.base` of the manifest) moves while the window is open: same HEAD, same branch, same mode."""
        manifest = Path(self.manifest()).read_text(encoding="utf-8")
        self.assertEqual(manifest.count("base: main\n"), 1, manifest)
        return {"id": "inna baza PR", "files": {self.manifest(): manifest.replace("base: main\n", "base: develop\n")}, "code": "delivery_changed"}


class GoPlanTests(ConsentCase):
    def test_go_plan(self) -> None:
        self.spec_waits_for_go_plan()
        spec = descend(self.project, SPEC2)
        changed = spec.read_text(encoding="utf-8") + "\nDopisane po otwarciu okna.\n"
        self.clicks("spec", "GO plan", [{"id": "zmieniony spec", "files": {str(spec): changed}, "code": "fileChanged"}], run=RUN2_ID,
                    shown={"doc": SPEC2})
        self.assertIn("Status: approved", spec.read_text(encoding="utf-8"))


class GoImplementTests(ConsentCase):
    def test_go_implement(self) -> None:
        self.spec_waits_for_go_plan()
        spec = descend(self.project, SPEC2)
        spec.write_text(spec.read_text(encoding="utf-8").replace("Status: draft (czeka na GO plan)", "Status: approved"), encoding="utf-8")
        self.plan_waits_for_go_implement()
        plan = descend(self.project, PLAN2)
        changed = plan.read_text(encoding="utf-8") + "- [ ] S2 Dopisane po otwarciu okna\n"
        self.clicks("plan", "GO implement", [{"id": "zmieniony plan", "files": {str(plan): changed}, "code": "fileChanged"}], run=RUN2_ID,
                    shown={"doc": PLAN2}, storage=["dashboard:plan", PLAN2])   # the page of that plan, as picked in its menu
        self.assertIn("Status: approved", plan.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
