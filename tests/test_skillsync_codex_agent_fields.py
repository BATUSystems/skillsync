"""The Codex role files do not depend on the Claude-only fields of a role (CLAUDE-2): the same role with and without `maxTurns`, `omitClaudeMd` and
`disallowedTools` in its frontmatter compiles to the identical `.codex/agents/<role>.toml`, while a change that Codex does read (the description) shows."""
import tomllib
import unittest

from tests.test_skillsync import TempProjectMixin, skillsync
from tests.tmp_paths import child  # noqa: E402

BODY = 'Read the code and report.\nPolish: ąę.\n'
PLAIN = '---\nname: explore\ndescription: Locate files\ntools: Read, Grep, Glob\nmodel: haiku\neffort: low\n---\n' + BODY
CLAUDE_ONLY = ('---\nname: explore\ndescription: Locate files\ntools: Read, Grep, Glob\nmodel: haiku\neffort: low\n'
               'maxTurns: 40\nomitClaudeMd: true\ndisallowedTools: Write, Edit\n---\n' + BODY)
OTHER_DESCRIPTION = PLAIN.replace('Locate files', 'Find files and symbols')


class CodexIgnoresClaudeOnlyFields(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manifest = {"skills": ["feature"], "agents": ["explore"]}
        folder = child(self.library_dir / "skills", "feature")
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: feature\ndescription: test\n---\nBody\n")
        (self.library_dir / "instructions" / "_base.md").write_text("Base {{ project }}\n")
        self.config["variables"] = {"project": "example"}
        skillsync.save_yaml(self.library_dir / "codex.yaml", {
            "max_concurrent_threads_per_session": 3,
            "agents": {"explore": {"name": "explorer", "model": "gpt-6-luna", "sandbox_mode": "read-only"}},
        })

    def compiled(self, role_text):
        child(self.library_dir / "agents", "explore.md").write_text(role_text, encoding="utf-8")
        skillsync.build_codex(self.project_dir, self.manifest, self.config)
        path = child(self.project_dir / ".codex" / "agents", "explorer.toml")
        text = path.read_text(encoding="utf-8")
        tomllib.loads(text)
        return text

    def test_the_toml_is_identical_with_and_without_the_claude_only_fields(self):
        plain = self.compiled(PLAIN)
        self.assertEqual(self.compiled(CLAUDE_ONLY), plain)
        for word in ('maxTurns', 'omitClaudeMd', 'disallowedTools'):
            self.assertNotIn(word, plain)

    def test_a_field_that_codex_reads_changes_the_toml(self):
        self.assertNotEqual(self.compiled(OTHER_DESCRIPTION), self.compiled(PLAIN))


if __name__ == '__main__':
    unittest.main()
