"""CORE-2000 U-8 (R2B-1): the patterns that recognise a copy of a prototype file run on a name from the URL of an anonymous request, so a long
name must not take seconds (the interpreter lock stops every other request meanwhile)."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402

PATTERNS = ("_JOURNAL_COPY_RE", "_SPARE_NAME_RE", "_DATA_COPY_RE")
SIZE = 64000
BUDGET = 0.5   # seconds for one name; the quadratic form took more than a second


def shapes():
    return ["prototype." + "1" * SIZE, "prototype" + "." * SIZE + "x", "prototype." + "-1" * (SIZE // 2), "a.json" + " " * SIZE + "x",
            "a." * (SIZE // 2), "a.b " * (SIZE // 4) + "x", "x.json" + "(" * SIZE, "x.json bak" + " 1" * (SIZE // 2) + "x",
            "x.json.bak" + "1" * SIZE, "#" + "prototype.json" * (SIZE // 14), "copy of " * (SIZE // 8), "comments.json" + "." * SIZE]


class CopyNameTests(unittest.TestCase):
    def test_a_long_name_is_judged_quickly(self) -> None:
        for name in PATTERNS:
            pattern = getattr(ps, name)
            for text in shapes():
                with self.subTest(pattern=name, shape=text[:16]):
                    started = time.perf_counter()
                    pattern.fullmatch(text)
                    self.assertLess(time.perf_counter() - started, BUDGET)

    def test_numbered_and_named_copies_are_still_recognised(self) -> None:
        for text in ("prototype.2.json", "prototype 12.json", "prototype (1).json", "prototype-001-old.json", "prototype.bak.json",
                     "prototype_copy.json", "comments.json 2", "Copy of prototype.json", "questions.json.1"):
            with self.subTest(text=text):
                self.assertIsNotNone(ps._DATA_COPY_RE.fullmatch(text), text)
        for text in ("prototype.json", "prototype.js", "prototypes.json"):
            with self.subTest(control=text):
                self.assertIsNone(ps._DATA_COPY_RE.fullmatch(text), text)


if __name__ == "__main__":
    unittest.main()
