"""K13: the skill `feature` tells the orchestrator to open and freeze every review round with `skillsync review-round` (PL and core-en)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'library/skills/feature/SKILL.md', 'library/skills/feature/references/review-loop.md', 'library/skills/feature/references/worker-mode.md',
    'layers/core-en/skills/feature/SKILL.md', 'layers/core-en/skills/feature/references/review-loop.md',
    'layers/core-en/skills/feature/references/worker-mode.md',
)


class ReviewRoundText(unittest.TestCase):
    def test_every_file_names_both_commands(self):
        for name in FILES:
            with self.subTest(name):
                text = (ROOT / name).read_text(encoding='utf-8')
                self.assertIn('review-round start', text)
                if 'worker-mode' not in name:
                    self.assertIn('review-round freeze', text)
                self.assertIn('Review ×N', text)

    def test_the_skill_names_the_report_pattern_and_agent_run(self):
        for name in ('library/skills/feature/SKILL.md', 'layers/core-en/skills/feature/SKILL.md'):
            with self.subTest(name):
                text = (ROOT / name).read_text(encoding='utf-8')
                self.assertIn('-review-N.md', text)
                self.assertIn('agent-run', text)


if __name__ == '__main__':
    unittest.main()
