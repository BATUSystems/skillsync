"""Theme tokens of the status page are part of the renderer contract: the list in the `dashboard` skill equals the tokens defined in the
`:root` block of `app.css`, the two dark blocks agree, and a token used by a rule is defined. Run: `python3 -m unittest tests.test_skillsync_dashboard_theme_tokens`."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent / "library" / "skills" / "dashboard"
CSS = (DASH / "assets" / "app.css").read_text(encoding="utf-8")
SKILL = (DASH / "SKILL.md").read_text(encoding="utf-8")


def block(selector: str) -> str:
    start = CSS.index(selector)
    return CSS[CSS.index("{", start) + 1:CSS.index("}", start)]


def names(text: str) -> set[str]:
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", text))


class ThemeTokens(unittest.TestCase):
    def test_the_skill_lists_exactly_the_tokens_of_the_light_block(self) -> None:
        listed = set(re.findall(r"--[a-z0-9-]+", SKILL[SKILL.index("**Tokeny motywu"):SKILL.index("Kontrakt `deps`")].split("\n")[1]))
        self.assertEqual(listed, names(block(":root {")), "a new token goes to the skill list and to the CHANGELOG")

    def test_the_two_dark_blocks_have_the_same_tokens(self) -> None:
        explicit = names(block(':root[data-theme="dark"]'))
        media = names(block(':root:not([data-theme="light"])'))
        self.assertEqual(explicit, media)
        self.assertTrue(explicit <= names(block(":root {")))

    def test_every_token_a_rule_uses_is_defined_somewhere(self) -> None:
        used = set(re.findall(r"var\((--[a-z0-9-]+)", CSS))
        self.assertEqual(used - names(CSS), set(), "an undefined token leaves its element without colour")


if __name__ == "__main__":
    unittest.main()
