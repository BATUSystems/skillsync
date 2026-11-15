"""Segments of a checkpoint (`;` outside brackets), the `‖` separator and `run.parseWarnings` (STATUS-160-A, P8)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard  # noqa: E402

REPORTED = ("w toku: F0.2 (kontrakty; testy), implementer: F1.1, F1.3 (opis; dalej), F1.4; "
            "zrobione: F0.1 (a; b); czeka: GO plan (spec; plan)")


class SplitSegments(unittest.TestCase):
    def test_semicolon_inside_brackets_does_not_end_a_segment(self):
        self.assertEqual(dashboard.split_segments("a (x; y); b [p; q]; c {r; s}"), ["a (x; y)", " b [p; q]", " c {r; s}"])

    def test_unbalanced_bracket_falls_back_to_every_semicolon(self):
        self.assertEqual(dashboard.split_segments("a (x; y; z"), ["a (x", " y", " z"])

    def test_text_without_brackets_splits_as_before(self):
        self.assertEqual(dashboard.split_segments("w toku: F1.1; czeka: nic"), ["w toku: F1.1", " czeka: nic"])
        self.assertEqual(dashboard.split_segments("bez średnika"), ["bez średnika"])


class IdsWithBracketedSemicolons(unittest.TestCase):
    def test_reported_example_keeps_the_ids_after_the_bracket(self):
        self.assertEqual(dashboard.ids_in_progress(REPORTED), ["F0.2", "F1.3", "F1.4"])
        self.assertEqual(dashboard.ids_reported_done(REPORTED), ["F0.1"])

    def test_wait_and_eta_survive_brackets(self):
        self.assertEqual(dashboard.wait_marker(REPORTED), "GO plan (spec; plan)")
        self.assertEqual(dashboard.parse_eta("eta: bieg (a; b) do 17:30; w toku: F1.1"), ("bieg (a; b)", "17:30"))

    def test_old_checkpoints_without_brackets_read_as_before(self):
        self.assertEqual(dashboard.ids_in_progress("zrobione: F0.2; w toku: F1.1 i F1.2"), ["F1.1", "F1.2"])
        self.assertEqual(dashboard.ids_in_progress("w toku: F1.1; F1.2"), ["F1.1"])
        self.assertIsNone(dashboard.ids_in_progress("restart sesji"))
        self.assertIsNone(dashboard.wait_marker("w toku: F1.1; czeka: nic"))

    def test_unbalanced_bracket_reads_as_before(self):
        self.assertEqual(dashboard.ids_in_progress("w toku: F1.1 (a; F1.2"), ["F1.1"])
        self.assertEqual(dashboard.wait_marker("w toku: F1.1 (a; czeka: GO plan"), "GO plan")


class DoubleBarSeparator(unittest.TestCase):
    def test_double_bar_and_pipes_join_step_ids(self):
        self.assertEqual(dashboard.ids_in_progress("w toku: F1.1 ‖ F1.2"), ["F1.1", "F1.2"])
        self.assertEqual(dashboard.ids_in_progress("w toku: F1.1||F1.2, F1.3"), ["F1.1", "F1.2", "F1.3"])
        self.assertEqual(dashboard.ids_in_progress("w toku: F1.1 / F1.2 + F1.3 & F1.4"), ["F1.1", "F1.2", "F1.3", "F1.4"])


class ParseWarnings(unittest.TestCase):
    def test_element_without_leading_id_and_a_later_id_are_reported(self):
        found = dashboard.report_warnings("w toku: implementer: F1.2, F1.5 opis + F1.6, F1.7 (po F1.6)")
        self.assertEqual([(w["segment"], w["reason"], w["fragment"]) for w in found],
                         [("w toku", "no-step-id", "implementer: F1.2"), ("w toku", "later-id", "F1.5 opis + F1.6")])

    def test_clean_lists_and_nothing_values_give_no_warning(self):
        for text in ("w toku: F1.1, F1.2 (opis; dalej)", "w toku: nic; zrobione: brak", "zrobione: -", "restart sesji"):
            with self.subTest(text=text):
                self.assertEqual(dashboard.report_warnings(text), [])

    def test_fragment_is_cut_and_whitespace_collapsed(self):
        found = dashboard.report_warnings("zrobione: " + "słowo   " * 40)
        self.assertEqual(found[0]["segment"], "zrobione")
        self.assertEqual(len(found[0]["fragment"]), dashboard.PARSE_WARNING_FRAGMENT)
        self.assertNotIn("  ", found[0]["fragment"])

    def test_run_warnings_are_the_newest_ten_with_times(self):
        items = [{"time": f"10:{i:02d}", "text": f"w toku: opis{i}"} for i in range(15)]
        found = dashboard.checkpoint_warnings(items)
        self.assertEqual(len(found), dashboard.PARSE_WARNING_LIMIT)
        self.assertEqual(found[0]["time"], "10:14")
        self.assertEqual(dashboard.checkpoint_warnings([{"time": "09:00", "text": "w toku: F1.1"}]), [])


class CrLfCheckpoints(unittest.TestCase):
    def test_windows_line_endings_give_the_same_result_as_lf(self):
        lf = "RUN=2026-11-15T09:00:00+01:00\n- 09:10 " + REPORTED + "\n- 09:20 w toku: F1.1 ‖ F1.2; eta: bieg do 10:00\n"
        crlf = lf.replace("\n", "\r\n")
        self.assertEqual(dashboard.parse_checkpoints(crlf), dashboard.parse_checkpoints(lf))
        _, items = dashboard.parse_checkpoints(crlf)
        self.assertEqual(dashboard.ids_in_progress(items[0]["text"]), ["F0.2", "F1.3", "F1.4"])
        self.assertEqual(dashboard.ids_in_progress(items[1]["text"]), ["F1.1", "F1.2"])
        self.assertEqual(dashboard.parse_eta(items[1]["text"]), ("bieg", "10:00"))


class ParseWarningsRedaction(unittest.TestCase):
    """M1 (review): the fragment is redacted BEFORE it is cut, so a secret split by the 80-character cut leaves no prefix."""

    def test_secret_on_the_cut_boundary_is_masked_whole(self):
        import os
        from unittest import mock
        secret = ''.join(('Zq9Xw3', 'Lm7Rt2', 'Vb5Nc8', 'Kd'))   # assembled at run time: no literal token for the secret scanners
        with mock.patch.dict(os.environ, {'SOME_API_TOKEN': secret}):
            for pad in range(40, 70):
                text = 'w toku: opis bez id ' + 'x' * pad + ' ' + secret + ' koniec'
                fragment = dashboard.report_warnings(text)[0]['fragment']
                with self.subTest(pad=pad):
                    self.assertNotIn(secret[:6], fragment)
                    self.assertLessEqual(len(fragment), dashboard.PARSE_WARNING_FRAGMENT)


if __name__ == "__main__":
    unittest.main()
