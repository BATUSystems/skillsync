"""`common.plan_section(text, name)`: the one reader of `## <name>` sections of plans (STATUS-160-A, shared with the plan summary)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow.common import plan_section  # noqa: E402

DOC = "# Plan\n\n## Streszczenie\n\nLinia 1\n\n```\n## to nie nagłówek\n```\n~~~\n## ani to\n~~~\nLinia 2\n\n## Wynik\nOK\n### Podsekcja\nx\n## Dalej\nn\n"


class PlanSection(unittest.TestCase):
    def test_body_up_to_the_next_level_two_heading_skipping_fences(self):
        body = plan_section(DOC, "Streszczenie")
        self.assertTrue(body.startswith("Linia 1") and body.endswith("Linia 2"))
        self.assertIn("## to nie nagłówek", body)
        self.assertNotIn("Wynik", body)

    def test_level_three_headings_stay_inside_the_section(self):
        self.assertEqual(plan_section(DOC, "Wynik"), "OK\n### Podsekcja\nx")

    def test_name_is_compared_without_case_and_extra_spaces(self):
        self.assertEqual(plan_section(DOC, "  wynik "), "OK\n### Podsekcja\nx")

    def test_absent_section_is_none_and_an_empty_one_is_an_empty_string(self):
        self.assertIsNone(plan_section(DOC, "Brak"))
        self.assertEqual(plan_section("## Wynik\n\n\n## Inne\n", "Wynik"), "")

    def test_crlf_and_last_section(self):
        self.assertEqual(plan_section("## Wynik\r\nOK\r\n", "Wynik"), "OK")


if __name__ == "__main__":
    unittest.main()
