"""docs/upgrading.md: nagłówki `## Przejście …` są unikalne, a sekcja przejścia na bieżącą wersję generatora istnieje (CORE-1142).

W 1.14.0 sekcja „Przejście 1.13.0 → 1.14.0” była podwójna (dwie gałęzie dopisały ją osobno; połączona w 1.14.1): scalenie tekstowe nie zgłosiło
konfliktu, a czytelnik dostał dwie sekcje. Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_upgrading_headings.py`.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import __version__  # noqa: E402

HEADING = re.compile(r'^## (Przejście .+?)\s*$', re.M)
PAIR = re.compile(r'^Przejście (\d+\.\d+\.\d+) → (\d+\.\d+\.\d+)$')


def transitions(text):
    """Teksty nagłówków `## Przejście …` w kolejności pliku."""
    return HEADING.findall(text)


def duplicates(headings):
    """Nagłówki występujące więcej niż raz (po zwinięciu białych znaków)."""
    seen, repeated = set(), []
    for heading in headings:
        key = ' '.join(heading.split())
        if key in seen and key not in repeated:
            repeated.append(key)
        seen.add(key)
    return repeated


def targets(headings):
    """Wersje docelowe z nagłówków `Przejście X → Y`."""
    return [match.group(2) for match in (PAIR.match(' '.join(heading.split())) for heading in headings) if match]


class UpgradingHeadingsTests(unittest.TestCase):
    TEXT = (ROOT / 'docs' / 'upgrading.md').read_text(encoding='utf-8')

    def test_the_transition_headings_are_unique(self):
        self.assertEqual(duplicates(transitions(self.TEXT)), [])

    def test_no_two_sections_lead_to_the_same_version(self):
        goals = targets(transitions(self.TEXT))
        self.assertEqual(sorted({goal for goal in goals if goals.count(goal) > 1}), [])

    def test_the_section_for_the_current_version_exists(self):
        self.assertIn(__version__, targets(transitions(self.TEXT)),
                      f'docs/upgrading.md nie ma sekcji „Przejście … → {__version__}” (podbicie wersji wymaga sekcji przejścia)')


class HelperTests(unittest.TestCase):
    """Strażnik łapie podwójną sekcję (przypadek z 1.14.0), a nie przepuszcza tylko dlatego, że nic nie sprawdza."""

    def test_a_doubled_section_is_reported(self):
        text = '# U\n\n## Przejście 1.13.0 → 1.14.0\ntekst\n\n## Przejście 1.12.0 → 1.13.0\n\n## Przejście 1.13.0 → 1.14.0\ninny tekst\n'
        self.assertEqual(duplicates(transitions(text)), ['Przejście 1.13.0 → 1.14.0'])

    def test_unique_sections_pass_and_other_headings_are_ignored(self):
        text = '## Kiedy podbić wersję\n\n## Przejście 1.0.0 → 1.1.0\n\n## Przejście 1.1.0 → 1.2.0\n\n## Kiedy podbić wersję\n'
        self.assertEqual(duplicates(transitions(text)), [])
        self.assertEqual(targets(transitions(text)), ['1.1.0', '1.2.0'])

    def test_extra_spaces_do_not_hide_a_duplicate(self):
        text = '## Przejście 1.0.0 → 1.1.0\n\n## Przejście  1.0.0 →  1.1.0 \n'
        self.assertEqual(len(duplicates(transitions(text))), 1)

    def test_two_sections_for_the_same_target_version_are_seen(self):
        goals = targets(transitions('## Przejście 1.0.0 → 1.1.0\n\n## Przejście 1.0.5 → 1.1.0\n'))
        self.assertEqual(goals, ['1.1.0', '1.1.0'])


if __name__ == '__main__':
    unittest.main()
