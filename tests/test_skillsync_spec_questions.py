"""U-4a: a question that a table of the open questions section shows as resolved is not blocking; in doubt it stays blocking."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import dashboard, spec_questions  # noqa: E402


def spec(*lines, heading="## Pytania otwarte"):
    return "\n".join(["# Spec", "", heading, "", *lines, "", "## Decyzje", ""])


def table(header, *rows):
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header), *("| " + " | ".join(row) + " |" for row in rows)]


def blocking(text):
    return [item["id"] for item in dashboard.parse_questions(text)[1]]


class Tag(unittest.TestCase):
    def test_a_tag_at_the_start_of_a_cell_resolves_the_row(self):
        for cell in ("[ROZSTRZYGNIĘTE 26.11] A", "[RESOLVED] A", "**[ROZSTRZYGNIĘTE]**", "[Rozstrzygnięta: PM] B", "[ROZSTRZYGNIETE 2026-11-26]"):
            with self.subTest(cell=cell):
                text = spec(*table(("#", "Pytanie", "Odpowiedź właściciela"), ("Q1", "A?", cell), ("Q2", "B?", "x")))
                self.assertEqual(blocking(text), ["Q2"])

    def test_the_tag_may_stand_in_the_question_cell_and_without_a_header(self):
        self.assertEqual(blocking(spec(*table(("#", "Pytanie"), ("Q1", "[RESOLVED] A?"), ("Q2", "B?")))), ["Q2"])
        self.assertEqual(blocking(spec("| Q1 | A? | [RESOLVED] yes |", "| Q2 | B? | yes |")), ["Q2"])
        for question in ("[RESOLVED?] A?", "[ROZSTRZYGNIĘTE częściowo] A?"):
            with self.subTest(question=question):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie"), ("Q1", question)))), ["Q1"])

    def test_a_tag_that_casts_doubt_keeps_the_question(self):
        for cell in ("patrz [ROZSTRZYGNIĘTE] Q2", "[ROZSTRZYGNIĘTE częściowo] CSV", "[ROZSTRZYGNIĘTE] częściowo", "[RESOLVED?] A", "[RESOLVED] A?",
                     "[ROZSTRZYGNIĘTE] kodowanie czeka", "[ROZSTRZYGNIĘTE] ponownie otwarte", "~~[ROZSTRZYGNIĘTE] A~~", "[ROZSTRZYGNIĘTE wstępnie]",
                     "[NIEROZSTRZYGNIĘTE]", "[UNRESOLVED]", "[rozstrzygnięcie: czeka]", "[RESOLVED] pending approval", "[RESOLVED] TBC"):
            with self.subTest(cell=cell):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Uwagi"), ("Q1", "A?", cell)))), ["Q1"])

    def test_an_open_word_or_an_empty_answer_in_another_column_wins_over_the_tag(self):
        for header, value in ((("Status pytania",), "otwarte"), (("Uwagi",), "czeka na właściciela"), (("Decyzja",), "—"), (("Decyzja",), ""),
                              (("Rozstrzygnięcie",), "brak"), (("Status",), "w analizie"), (("Notes",), "still open")):
            with self.subTest(header=header, value=value):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Odpowiedź", *header), ("Q1", "A?", "[ROZSTRZYGNIĘTE] A", value)))), ["Q1"])
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Odpowiedź"), ("Q1", "A?", "[ROZSTRZYGNIĘTE] A")))), [])   # the control


class TwoColumns(unittest.TestCase):
    """The second cell is the outcome when the header does not call it the question: it is checked for words of doubt like every other."""

    def test_the_second_cell_is_checked(self):
        for column, value in (("Rozstrzygnięcie", "[ROZSTRZYGNIĘTE] częściowo; kodowanie czeka"), ("Rozstrzygnięcie", "[RESOLVED] still open"),
                              ("Status", "rozstrzygnięte (częściowo)"), ("Status", "resolved (pending)"), ("Status", "[ROZSTRZYGNIĘTE] czeka"),
                              ("Status", "rozstrzygnięte (czeka na potwierdzenie)"), ("Status", "resolved (to be confirmed)"),
                              ("Status", "rozstrzygnięte (do zatwierdzenia)"), ("Status", "rozstrzygnięte (nie do końca)"), ("Notes", "[RESOLVED] A?")):
            with self.subTest(column=column, value=value):
                self.assertEqual(spec_questions.resolved_ids(spec(*table(("Q", column), ("Q1", value)))), set())

    def test_the_control(self):
        for column, value in (("Rozstrzygnięcie", "[ROZSTRZYGNIĘTE 26.11] A"), ("Status", "rozstrzygnięte"), ("Status", "resolved (PM)")):
            with self.subTest(column=column, value=value):
                self.assertEqual(spec_questions.resolved_ids(spec(*table(("Q", column), ("Q1", value)))), {"Q1"})

    def test_doubt_beside_the_tag_in_the_question_cell(self):
        self.assertEqual(blocking(spec(*table(("#", "Pytanie"), ("Q1", "[ROZSTRZYGNIĘTE] częściowo: A?")))), ["Q1"])
        self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Uwagi"), ("Q1", "A?", "[RESOLVED] A; oczekiwanie na PM")))), ["Q1"])


class AnswerColumn(unittest.TestCase):
    def test_a_filled_answer_column_alone_resolves_nothing(self):
        for column in ("Rozstrzygnięcie", "Decyzja", "Odpowiedź", "Resolution", "Decision", "Answer"):
            for value in ("A, bo prościej", "tak", "Nie, zostaje", "oczekuje na właściciela", "Jeszcze nie", "— czeka", "TBC", "Proponuję A", "A lub B"):
                with self.subTest(column=column, value=value):
                    self.assertEqual(blocking(spec(*table(("#", "Pytanie", column), ("Q1", "A?", value)))), ["Q1"])


class StatusColumn(unittest.TestCase):
    def test_only_an_exact_resolved_status_resolves(self):
        for value in ("rozstrzygnięte", "Rozstrzygnięte 26.11", "rozstrzygnięta", "Rozstrzygnięto 2026-11-26 (PM)", "resolved", "Resolved (PM).",
                      "**ROZSTRZYGNIETE**", "[ROZSTRZYGNIĘTE 26.11]"):
            with self.subTest(value=value):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Status"), ("Q1", "A?", value)))), [])
        for value in ("nierozstrzygnięte", "unresolved", "not resolved", "rozstrzygnięte częściowo", "resolved?", "resolved partly", "otwarte", "",
                      "zamknięte", "rozstrzygnięcie: czeka", "czeka", "rozstrzygnięte, ponownie otwarte", "Resolved – reopened", "rozstrzygnięte wstępnie",
                      "rozstrzygnięte warunkowo", "resolved (pending approval)", "rozstrzygnięte w części", "rozstrzygnięte przez A, B czeka", "✅"):
            with self.subTest(value=value):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Status"), ("Q1", "A?", value)))), ["Q1"])

    def test_an_answer_column_beside_the_status(self):
        for answer, left in (("A", []), ("nie, zostaje", []), ("", ["Q1"]), ("—", ["Q1"]), ("brak", ["Q1"]), ("A (do potwierdzenia)", ["Q1"])):
            with self.subTest(answer=answer):
                self.assertEqual(blocking(spec(*table(("#", "Pytanie", "Decyzja", "Status"), ("Q1", "A?", answer, "rozstrzygnięte")))), left)


class Doubt(unittest.TestCase):
    RESOLVED = table(("#", "Pytanie", "Status"), ("Q1", "A?", "rozstrzygnięte"))

    def test_the_control_of_this_class(self):
        self.assertEqual(spec_questions.resolved_ids(spec(*self.RESOLVED)), {"Q1"})

    def test_a_row_whose_cells_cannot_be_matched_with_the_columns(self):
        for row in ("| Q1 | A | B | resolved |", "| Q1 | resolved |", "| Q1 | `a|b` czy c | resolved |"):
            with self.subTest(row=row):
                self.assertEqual(spec_questions.resolved_ids(spec("| # | Pytanie | Status |", "|---|---|---|", row)), set())

    def test_an_escaped_border_stays_in_its_cell(self):
        text = spec("| # | Pytanie | Status |", "|---|---|---|", r"| Q1 | a \| b | resolved |")
        self.assertEqual(spec_questions.resolved_ids(text), {"Q1"})

    def test_two_tables_without_an_empty_line(self):
        text = spec(*table(("#", "Pytanie", "Status"), ("Q1", "A?", "resolved")), *table(("#", "Pytanie", "Rekomendacja"), ("Q2", "B?", "resolved")))
        self.assertEqual(spec_questions.resolved_ids(text), set())

    def test_a_lone_border_line_is_no_separator(self):
        text = spec("| Q1 | A? |", "|", "| Q1 | [RESOLVED] |")   # read as a header, the first row would not count as an open Q1
        self.assertEqual(spec_questions.resolved_ids(text), set())

    def test_the_same_question_open_elsewhere_stays(self):
        for other in (["- Q1: A?"], ["", *table(("#", "Pytanie", "Status"), ("Q1", "A?", "otwarte"))], ["", "| Q1 | A? |"]):
            for lines in (self.RESOLVED + [""] + other, other + [""] + self.RESOLVED):
                with self.subTest(lines=lines):
                    self.assertEqual(spec_questions.resolved_ids(spec(*lines)), set())

    def test_a_row_resolves_only_its_own_question(self):
        text = spec(*table(("#", "Pytanie", "Status"), ("Q10", "B?", "resolved"), ("Q2", "zależy od Q1", "resolved"), ("Q1 i Q3", "C?", "resolved")))
        self.assertEqual(spec_questions.resolved_ids(text), {"Q10", "Q2"})

    def test_another_section_and_a_code_fence_resolve_nothing(self):
        for text in (spec("tekst") + "\n".join(self.RESOLVED), spec("```", *self.RESOLVED, "```"),
                     "\n".join(["## Decyzje", *self.RESOLVED, "## Pytania otwarte", "tekst"])):
            with self.subTest(text=text):
                self.assertEqual(spec_questions.resolved_ids(text), set())

    def test_assumptions_and_the_list_form_are_unchanged(self):
        text = spec("- Q1 [ZAŁOŻENIE]: f — przyjęte: CSV", "- Q2: B?", "- Q3 [ROZSTRZYGNIĘTE]: C", "",
                    *table(("#", "Pytanie", "Status"), ("Q4", "D?", "resolved"), ("Q5", "E?", "otwarte")))
        assumptions, left = dashboard.parse_questions(text)
        self.assertEqual([item["id"] for item in assumptions], ["Q1"])
        self.assertEqual([item["id"] for item in left], ["Q2", "Q5"])
        self.assertEqual(spec_questions.resolved_ids(text), {"Q4"})


if __name__ == "__main__":
    unittest.main()
