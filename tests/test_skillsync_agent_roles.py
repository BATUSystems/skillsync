"""Structure of the roles of the library (CLAUDE-2): every role has a `maxTurns` limit that agrees with the table of `references/claude.md`, and
`disallowedTools` appears only when the live probe F0 proved a narrow, working restriction (the marker `F0-disallowedTools:` of the same file)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import skill_frontmatter  # noqa: E402

ROLES = ('explore', 'implementer', 'mechanic', 'reviewer', 'scribe', 'test-runner', 'test-writer', 'verifier')
AGENTS = ROOT / 'library' / 'agents'
REFERENCE = ROOT / 'library' / 'skills' / 'feature' / 'references' / 'claude.md'
MARKER = re.compile(r'^F0-disallowedTools: (skuteczne|nieskuteczne|nierozstrzygnięte)\s*$', re.M)
BASIC = {'name', 'description', 'tools', 'model'}
MAX_LIMIT = 200


def role(name):
    metadata = skill_frontmatter.read_role_frontmatter(AGENTS / f'{name}.md')
    assert isinstance(metadata, dict), f'role {name} has no readable frontmatter'
    return metadata


def table_limits():
    """`{role: maxTurns}` from the table of roles in `references/claude.md` (the last cell of every row whose first cell is a role)."""
    limits = {}
    for line in REFERENCE.read_text(encoding='utf-8').splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if line.startswith('|') and cells and cells[0] in ROLES:
            limits[cells[0]] = cells[-1]
    return limits


class RoleLimits(unittest.TestCase):
    def test_the_library_has_exactly_the_eight_roles(self):
        self.assertEqual(sorted(path.stem for path in AGENTS.glob('*.md')), sorted(ROLES))

    def test_every_role_has_a_positive_integer_max_turns_within_the_cap(self):
        for name in ROLES:
            with self.subTest(role=name):
                value = role(name).get('maxTurns')
                self.assertIs(type(value), int, f'{name}: maxTurns must be an integer, not {value!r}')
                self.assertTrue(1 <= value <= MAX_LIMIT, f'{name}: maxTurns {value} outside 1..{MAX_LIMIT}')

    def test_the_numbers_agree_with_the_table_in_the_reference(self):
        limits = table_limits()
        self.assertEqual(sorted(limits), sorted(ROLES), 'the table of roles in references/claude.md must list every role with its limit')
        for name in ROLES:
            with self.subTest(role=name):
                self.assertEqual(limits[name], str(role(name)['maxTurns']))

    def test_the_basic_fields_of_every_role_are_kept(self):
        for name in ROLES:
            with self.subTest(role=name):
                metadata = role(name)
                self.assertEqual(metadata['name'], name)
                self.assertTrue(BASIC <= set(metadata), f'{name} lost one of {sorted(BASIC)}')
                self.assertIn('effort', metadata)
                self.assertNotIn('Agent', str(metadata['tools']), 'a role must not delegate further')

    def test_every_extra_field_has_a_version_entry(self):
        for name in ROLES:
            for key in set(role(name)) - BASIC:
                with self.subTest(role=name, field=key):
                    self.assertIn(key, skill_frontmatter.ROLE_FIELDS)


class RoleRestrictions(unittest.TestCase):
    def marker(self):
        found = MARKER.findall(REFERENCE.read_text(encoding='utf-8'))
        self.assertEqual(len(found), 1, 'references/claude.md must state the result of the probe once: `F0-disallowedTools: skuteczne|nieskuteczne|nierozstrzygnięte`')
        return found[0]

    def test_disallowed_tools_only_after_an_effective_probe(self):
        result = self.marker()
        carriers = [name for name in ROLES if 'disallowedTools' in role(name)]
        if result != 'skuteczne':
            self.assertEqual(carriers, [], f'the probe is `{result}`: no role may carry disallowedTools')
        else:
            for name in carriers:
                self.assertRegex(str(role(name)['tools']), r'\bBash\b', f'{name}: disallowedTools without Bash in tools restricts nothing')


if __name__ == '__main__':
    unittest.main()
