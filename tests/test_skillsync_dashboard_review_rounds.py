"""Review rounds on the status page: only a frozen report `<step>-N.md` is a round; decision files, logs and other files are not."""
import unittest

from skillsync_workflow import dashboard as engine

SPEC = {"exists": True, "status": "approved", "path": "docs/specs/x.md"}
PLAN = {"status": "in_progress", "path": "docs/plans/x.md"}
PROGRESS = {"total": 1, "phases": [], "current": None}
RUN = ".skillsync/runs/2026-01-10-demo/"


def labels(files):
    acts = engine.compute_acts(SPEC, PLAN, PROGRESS, [RUN + name for name in files])
    return ([s["label"] for s in acts[0]["stages"] if s["key"] == "stage_spec_review"][0],
            [s["label"] for s in acts[1]["stages"] if s["key"] == "stage_plan_review"][0],
            [s["label"] for s in acts[2]["stages"] if s["key"] == "code_review"][0])


class ReviewRoundsTests(unittest.TestCase):
    def test_decision_files_and_logs_are_not_rounds(self) -> None:
        files = ["spec-review-1.md", "spec-review-1-decyzje.md", "plan-review-1.md", "plan-review-1-decyzje.md",
                 "code-review-1.md", "code-review-F1-1.md", "code-review-F1-1-decyzje.md", "code-review-1.log",
                 "code-review-1.json", "spec-review-notes.md"]
        self.assertEqual(labels(files), ("Review", "Review", "Review kodu ×2"))

    def test_without_decision_files_unchanged(self) -> None:
        files = ["spec-review-1.md", "spec-review-2.md", "plan-review-1.md", "code-review-1.md", "code-review-2.md", "code-review-3.md"]
        self.assertEqual(labels(files), ("Review ×2", "Review", "Review kodu ×3"))

    def test_legacy_names_still_count(self) -> None:
        files = ["spec-review-r1.md", "spec-review-2-r1.md", "spec-review-r1-decisions.md", "final-review.md", "final-review-decyzje.md"]
        self.assertEqual(labels(files), ("Review ×2", "Review", "Review kodu"))

    def test_historical_final_review_names_count_as_code_rounds(self) -> None:
        files = ["final-review-guard-1.md", "final-review-guard-2.md", "final-review-F1-r1.md", "final-review-1.md", "final-review.md"]
        self.assertEqual(labels(files), ("Review", "Review", "Review kodu ×5"))

    def test_final_review_with_other_extension_or_decisions_is_not_a_round(self) -> None:
        files = ["final-review-guard-1.log", "final-review-1.json", "final-review-decyzje.md", "final-review-1-decisions.md",
                 "final-review-guard-decyzje-1.md"]
        self.assertEqual(labels(files), ("Review", "Review", "Review kodu"))

    def test_decision_segment_anywhere_excludes_the_file(self) -> None:
        files = ["code-review-final-decisions-1.md", "code-review-decyzje-2.md", "code-review-F1-decisions-1.md", "code-review-F1-1.md"]
        self.assertEqual(labels(files), ("Review", "Review", "Review kodu"))

    def test_no_files(self) -> None:
        self.assertEqual(labels([]), ("Review", "Review", "Review kodu"))


if __name__ == "__main__":
    unittest.main()
