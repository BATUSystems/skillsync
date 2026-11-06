"""AGENTS.md as the single instruction source (`instructions.entrypoint: agents`) and the unchanged `inline` mode."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skillsync import TempProjectMixin, skillsync
from tests.layers_helpers import needs_os_symlinks  # noqa: E402

GOLDEN = Path(__file__).parent / "fixtures" / "agents_md_inline_golden.json"
WATCHED = ["CLAUDE.md", "AGENTS.md", "AGENTS.md.skillsync-backup", ".codex/skillsync-instructions.md",
           ".github/copilot-instructions.md", ".gitignore"]
BEGIN, END = "<!-- SKILLSYNC:CODEX-BEGIN -->", "<!-- SKILLSYNC:CODEX-END -->"
NOTE_BEGIN, NOTE_END = "<!-- SKILLSYNC:CODEX-NOTE-BEGIN -->", "<!-- SKILLSYNC:CODEX-NOTE-END -->"

# Inline variants: (name, manifest, files created before the build).
VARIANTS = [
    ("template_merge", {"skills": ["feature"], "instructions": {"merge": ["_base"], "template": "tmpl"}}, {}),
    ("merge_no_template_copilot", {"skills": ["feature"], "instructions": {"merge": ["_base"]}},
     {".github/copilot-instructions.md": "Handwritten copilot\n"}),
    ("feature_only", {"skills": ["feature"]}, {}),
    ("codex_off", {"skills": ["feature"], "codex": False, "instructions": {"merge": ["_base"], "template": "tmpl"}}, {}),
    ("claude_off", {"skills": ["feature"], "claude": False, "instructions": {"merge": ["_base"], "template": "tmpl"}}, {}),
    ("handwritten_claude", {"skills": ["feature"], "instructions": {"merge": ["_base"]}}, {"CLAUDE.md": "My own rules\n"}),
    ("own_agents", {"skills": ["feature"], "instructions": {"merge": ["_base"], "template": "tmpl"}},
     {"AGENTS.md": "# Team agents\n\nKeep this.\n"}),
    ("template_no_merge", {"skills": ["feature"], "instructions": {"template": "tmpl"}}, {}),
]


def build_all(project_dir, manifest, config, dry_run=False):
    """The instruction-file part of `link` in its real order (new helper when present, else the old sequence)."""
    if hasattr(skillsync, "build_instruction_files"):
        skillsync.build_instruction_files(project_dir, manifest, config, dry_run=dry_run)
        return
    skillsync.build_instructions(project_dir, manifest, config, dry_run=dry_run)
    skillsync.build_claude_md(project_dir, manifest, config, dry_run=dry_run)
    skillsync.build_codex(project_dir, manifest, config, dry_run=dry_run)


class Base(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        folder = self.library_dir / "skills" / "feature"
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: feature\ndescription: test\n---\nBody\n")
        (self.library_dir / "instructions" / "_base.md").write_text("Base {{ project }}\n")
        (self.library_dir / "instructions" / "tmpl.md").write_text("## Project\n\nTemplate rules\n")
        self.config["variables"] = {"project": "example"}
        skillsync.save_yaml(self.library_dir / "codex.yaml", {"max_concurrent_threads_per_session": 3, "agents": {}})
        home = self.tmp / "home"
        home.mkdir()
        # Hermetic git: no global/system config and no ~/.config/git/ignore of the person running the tests.
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                               "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self, root=None):
        root = root or self.project_dir
        return {name: (root / name).read_text() for name in WATCHED if (root / name).is_file() and not (root / name).is_symlink()}

    def run_build(self, manifest, files=None, project_dir=None, **kwargs):
        project_dir = project_dir or self.project_dir
        if not kwargs.get("dry_run"):
            (project_dir / ".github").mkdir(exist_ok=True)  # `link` creates it before the instruction files
        for name, text in (files or {}).items():
            path = project_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            build_all(project_dir, manifest, self.config, **kwargs)
        return out.getvalue()

    def agents(self, **instructions):
        return {"skills": ["feature"], "instructions": {"merge": ["_base"], "template": "tmpl", "entrypoint": "agents", **instructions}}

    def block(self):
        text = (self.project_dir / "AGENTS.md").read_text()
        return text.split(BEGIN, 1)[1].split(END, 1)[0]


class InlineGolden(Base):
    """`inline` (default) writes exactly what the generator wrote before this change."""

    def test_inline_matches_golden_snapshot(self):
        golden = json.loads(GOLDEN.read_text())
        for name, manifest, files in VARIANTS:
            with self.subTest(variant=name):
                project = self.tmp / name
                project.mkdir()
                self.run_build(manifest, files, project_dir=project)
                self.assertEqual(self.snapshot(project), golden[name])

    def test_explicit_inline_equals_default(self):
        name, manifest, files = VARIANTS[0]
        golden = json.loads(GOLDEN.read_text())[name]
        self.run_build({**manifest, "instructions": {**manifest["instructions"], "entrypoint": "inline"}}, files)
        self.assertEqual(self.snapshot(), golden)


class AgentsMode(Base):
    def test_content_once_in_agents_md_and_claude_imports_it(self):
        self.run_build(self.agents())
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        block = self.block()
        self.assertIn("Base example", block)
        self.assertIn("Template rules", block)
        self.assertIn(NOTE_BEGIN, block)
        self.assertIn("$feature", block.split(NOTE_BEGIN, 1)[1])
        self.assertFalse((self.project_dir / ".codex/skillsync-instructions.md").exists())
        self.assertFalse((self.project_dir / "AGENTS.md.skillsync-backup").exists())
        self.assertIn("AGENTS.md", (self.project_dir / ".gitignore").read_text().splitlines())

    def test_block_content_equals_todays_claude_md_body(self):
        name, manifest, files = VARIANTS[0]
        golden = json.loads(GOLDEN.read_text())[name]["CLAUDE.md"]
        self.run_build(self.agents())
        body = self.block().split(NOTE_BEGIN, 1)[0].strip("\n")
        self.assertEqual(body, golden[len(skillsync.CLAUDE_MD_HEADER):].strip("\n"))

    def test_copilot_instructions_unchanged(self):
        golden = json.loads(GOLDEN.read_text())["template_merge"][".github/copilot-instructions.md"]
        self.run_build(self.agents())
        self.assertEqual((self.project_dir / ".github/copilot-instructions.md").read_text(), golden)

    def test_managed_codex_copy_removed_local_edit_kept(self):
        self.run_build(self.agents(entrypoint="inline"))
        copy = self.project_dir / ".codex/skillsync-instructions.md"
        self.assertTrue(copy.is_file())
        self.run_build(self.agents())
        self.assertFalse(copy.exists())
        other = self.tmp / "edited"
        other.mkdir()
        self.run_build(self.agents(entrypoint="inline"), project_dir=other)
        (other / ".codex/skillsync-instructions.md").write_text("local edit\n")
        out = self.run_build(self.agents(), project_dir=other)
        self.assertEqual((other / ".codex/skillsync-instructions.md").read_text(), "local edit\n")
        self.assertIn("skillsync-instructions.md", out)

    def test_codex_failure_does_not_leave_import_without_block(self):
        (self.library_dir / "codex.yaml").unlink()
        self.run_build(self.agents())
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        self.assertIn("Template rules", self.block())

    def test_codex_off_block_without_codex_note(self):
        self.run_build({**self.agents(), "codex": False})
        self.assertNotIn(NOTE_BEGIN, self.block())
        self.assertIn("Template rules", self.block())
        self.assertFalse((self.project_dir / ".codex").exists())

    def test_claude_off_codex_reads_block(self):
        self.run_build({**self.agents(), "claude": False})
        self.assertFalse((self.project_dir / "CLAUDE.md").exists())
        self.assertIn("Template rules", self.block())
        self.assertIn(NOTE_BEGIN, self.block())
        self.assertFalse((self.project_dir / ".codex/skillsync-instructions.md").exists())

    def test_both_off_no_block(self):
        self.run_build({**self.agents(), "claude": False, "codex": False})
        self.assertFalse((self.project_dir / "AGENTS.md").exists())

    def test_handwritten_claude_without_codex_gets_no_block(self):
        manifest = {"skills": ["feature"], "codex": False, "instructions": {"merge": ["_base"], "entrypoint": "agents"}}
        self.run_build(manifest, {"CLAUDE.md": "My own rules\n"})
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), "My own rules\n")
        self.assertFalse((self.project_dir / "AGENTS.md").exists())

    def test_migration_from_inline_without_backup_and_idempotent(self):
        self.run_build(self.agents(entrypoint="inline"))
        self.run_build(self.agents())
        self.assertFalse((self.project_dir / "AGENTS.md.skillsync-backup").exists())
        before = {p: p.read_bytes() for p in self.project_dir.rglob("*") if p.is_file()}
        agents_mtime = (self.project_dir / "AGENTS.md").stat().st_mtime_ns
        self.run_build(self.agents())
        self.assertEqual(before, {p: p.read_bytes() for p in self.project_dir.rglob("*") if p.is_file()})
        # CLAUDE.md and copilot-instructions.md are rewritten with equal bytes on every build, also in `inline` (pre-existing).
        self.assertEqual(agents_mtime, (self.project_dir / "AGENTS.md").stat().st_mtime_ns)

    def test_template_without_merge_codex_loses_base_like_claude(self):
        self.run_build({"skills": ["feature"], "instructions": {"template": "tmpl", "entrypoint": "agents"}})
        self.assertNotIn("Base example", self.block())
        self.assertIn("Template rules", self.block())

    def test_dry_run_writes_nothing(self):
        out = self.run_build(self.agents(), dry_run=True)
        self.assertEqual(list(self.project_dir.iterdir()), [])
        self.assertIn("Would write instructions block", out)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(skillsync.build_instruction_files(self.project_dir, self.agents(), self.config, dry_run=True), "agents")

    def test_handwritten_claude_with_codex_gets_block_for_codex(self):
        manifest = {"skills": ["feature"], "instructions": {"merge": ["_base"], "entrypoint": "agents"}}
        self.run_build(manifest, {"CLAUDE.md": "My own rules\n"})
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), "My own rules\n")
        self.assertIn("Base example", self.block())
        self.assertIn(NOTE_BEGIN, self.block())
        self.assertFalse((self.project_dir / ".codex/skillsync-instructions.md").exists())

    def test_claude_off_still_warns_about_missing_template(self):
        # No template: copilot-instructions.md is not built, so only the AGENTS.md block can report the missing part.
        manifest = {"skills": ["feature"], "claude": False, "instructions": {"merge": ["_base", "gone"], "entrypoint": "agents"}}
        out = self.run_build(manifest)
        self.assertIn("Missing instructions template", out)
        self.assertIn("gone.md", out)

    def test_link_project_uses_agents_mode(self):
        manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        skillsync.save_yaml(manifest_path, self.agents())
        with contextlib.redirect_stdout(io.StringIO()):
            skillsync.link_project(manifest_path, self.config)
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        self.assertIn("Template rules", self.block())
        self.assertFalse((self.project_dir / ".codex/skillsync-instructions.md").exists())

    def test_instructions_command_with_and_without_path(self):
        manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        skillsync.save_yaml(manifest_path, self.agents())
        args = mock.Mock(path=str(self.project_dir), dry_run=False)
        with mock.patch.object(skillsync, "load_config", return_value=self.config), contextlib.redirect_stdout(io.StringIO()):
            skillsync.cmd_instructions(args)
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        (self.project_dir / "CLAUDE.md").unlink()
        (self.project_dir / "AGENTS.md").unlink()
        args.path = None
        with mock.patch.object(skillsync, "load_config", return_value=self.config), \
                mock.patch.object(skillsync, "discover_manifests", return_value=[manifest_path]), contextlib.redirect_stdout(io.StringIO()):
            skillsync.cmd_instructions(args)
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        self.assertIn("Template rules", self.block())

    def test_instructions_loop_keeps_old_order_in_inline(self):
        name, manifest, files = VARIANTS[0]
        old = self.tmp / "old"
        old.mkdir()
        (old / ".github").mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            skillsync.build_instructions(old, manifest, self.config)
            skillsync.build_codex(old, manifest, self.config)
            skillsync.build_claude_md(old, manifest, self.config)
        manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        skillsync.save_yaml(manifest_path, manifest)
        with mock.patch.object(skillsync, "load_config", return_value=self.config), \
                mock.patch.object(skillsync, "discover_manifests", return_value=[manifest_path]), contextlib.redirect_stdout(io.StringIO()):
            skillsync.cmd_instructions(mock.Mock(path=None, dry_run=False))
        self.assertEqual(self.snapshot(), self.snapshot(old))


class Fallback(Base):
    def assert_inline(self, out):
        claude = (self.project_dir / "CLAUDE.md").read_text()
        self.assertIn("Template rules", claude)
        self.assertNotIn("@AGENTS.md", claude)
        self.assertTrue((self.project_dir / ".codex/skillsync-instructions.md").is_file())
        self.assertIn("[warn]", out)

    @needs_os_symlinks
    def test_symlinked_agents_md(self):
        target = self.tmp / "shared-agents.md"
        target.write_text("shared\n")
        (self.project_dir / "AGENTS.md").symlink_to(target)
        self.assert_inline(self.run_build(self.agents()))
        self.assertEqual(target.read_text(), "shared\n")

    def test_broken_markers(self):
        out = self.run_build(self.agents(), {"AGENTS.md": BEGIN + "\nhalf\n"})
        self.assert_inline(out)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.project_dir), *args], check=True, capture_output=True)

    def test_tracked_agents_md_is_not_filled(self):
        self.git("init", "-q")
        (self.project_dir / "AGENTS.md").write_text("# Team\n")
        self.git("add", "AGENTS.md")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
        self.assert_inline(self.run_build(self.agents()))
        self.assertNotIn("Template rules", (self.project_dir / "AGENTS.md").read_text())

    def test_untracked_not_ignored_agents_md_is_not_filled(self):
        self.git("init", "-q")
        self.assert_inline(self.run_build(self.agents(), {"AGENTS.md": "# Team\n"}))

    def test_existing_agents_md_outside_git_is_not_filled(self):
        self.assert_inline(self.run_build(self.agents(), {"AGENTS.md": "# Team\n"}))

    def test_tracked_agents_md_with_only_the_block_is_not_filled(self):
        self.git("init", "-q")
        self.run_build(self.agents(entrypoint="inline"))
        self.git("add", "-f", "AGENTS.md")
        self.assert_inline(self.run_build(self.agents()))

    def test_empty_tracked_agents_md_is_not_filled(self):
        self.git("init", "-q")
        (self.project_dir / "AGENTS.md").write_text("")
        self.git("add", "AGENTS.md")
        self.assert_inline(self.run_build(self.agents()))
        self.assertNotIn("Template rules", (self.project_dir / "AGENTS.md").read_text())

    def test_reversed_markers_do_not_crash(self):
        out = self.run_build(self.agents(), {"AGENTS.md": END + "\n" + BEGIN + "\n"})
        self.assert_inline(out)

    def test_git_error_other_than_no_repository_counts_as_not_ignored(self):
        failure = subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership in repository")
        with mock.patch.object(skillsync.subprocess, "run", return_value=failure):
            self.assert_inline(self.run_build(self.agents(), {"AGENTS.md": BEGIN + "\nold\n" + END + "\n"}))

    def test_git_timeout_counts_as_not_ignored(self):
        self.git("init", "-q")
        (self.project_dir / ".gitignore").write_text("AGENTS.md\n")
        (self.project_dir / "AGENTS.md").write_text("# Mine\n")
        with mock.patch.object(skillsync.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 10)):
            self.assert_inline(self.run_build(self.agents()))

    def test_git_runs_with_c_locale(self):
        self.run_build(self.agents(entrypoint="inline"))
        calls = []
        real = skillsync.subprocess.run
        def spy(cmd, **kwargs):
            calls.append(kwargs.get("env") or {})
            return real(cmd, **kwargs)
        with mock.patch.object(skillsync.subprocess, "run", side_effect=spy):
            self.run_build(self.agents())
        self.assertTrue(calls)
        self.assertTrue(all(env.get("LC_ALL") == "C" for env in calls))

    def test_git_missing_counts_as_not_ignored(self):
        self.git("init", "-q")
        (self.project_dir / ".gitignore").write_text("AGENTS.md\n")
        (self.project_dir / "AGENTS.md").write_text("# Mine\n")
        with mock.patch.object(skillsync.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assert_inline(self.run_build(self.agents()))

    def test_ignored_agents_md_with_own_content_is_filled(self):
        self.git("init", "-q")
        out = self.run_build(self.agents(), {"AGENTS.md": "# Mine\n", ".gitignore": "AGENTS.md\n"})
        self.assertIn("Template rules", self.block())
        self.assertTrue((self.project_dir / "AGENTS.md").read_text().startswith("# Mine\n"))
        self.assertEqual((self.project_dir / "CLAUDE.md").read_text(), skillsync.CLAUDE_MD_HEADER + "@AGENTS.md\n")
        self.assertNotIn("[warn]", out)


class Switch(Base):
    def mode_after(self, manifest_mode, config_mode):
        instructions = {"merge": ["_base"], "template": "tmpl"}
        if manifest_mode is not None:
            instructions["entrypoint"] = manifest_mode
        if config_mode is not None:
            self.config["entrypoint"] = config_mode
        out = self.run_build({"skills": ["feature"], "instructions": instructions})
        return ("agents" if "@AGENTS.md" in (self.project_dir / "CLAUDE.md").read_text() else "inline"), out

    def test_precedence(self):
        cases = [((None, None), "inline"), (("agents", None), "agents"), ((None, "agents"), "agents"),
                 (("inline", "agents"), "inline"), (("agents", "inline"), "agents")]
        for (manifest_mode, config_mode), expected in cases:
            with self.subTest(manifest=manifest_mode, config=config_mode):
                self.setUp()
                self.assertEqual(self.mode_after(manifest_mode, config_mode)[0], expected)

    def test_unknown_value_warns_and_stays_inline(self):
        mode, out = self.mode_after("both", None)
        self.assertEqual(mode, "inline")
        self.assertIn("entrypoint", out)


class DoctorOnGenerated(Base):
    def test_doctor_header_matches_generator(self):
        from skillsync_workflow import quality
        self.assertEqual(quality.CLAUDE_MD_HEADER, skillsync.CLAUDE_MD_HEADER)

    def test_link_in_agents_mode_passes_doctor_checks_for_instructions(self):
        from skillsync_workflow import quality
        manifest = {"instructions": {"merge": ["_base"], "template": "tmpl", "entrypoint": "agents"}}
        self.run_build(manifest)
        self.assertTrue(quality.generated_claude_import(self.project_dir))
        self.assertIn("Template rules", quality.agents_block_body(self.project_dir))
        (self.project_dir / "AGENTS.md").write_text("")
        self.assertEqual(quality.agents_block_body(self.project_dir), "")


class Push(Base):
    def setUp(self):
        super().setUp()
        # Like the real `_base.md`: shared preamble ends with the marker that `push` cuts on.
        (self.library_dir / "instructions" / "_base.md").write_text("Base {{ project }}\n" + skillsync.BASE_PREAMBLE_END_MARKER + "\n")

    def push(self, manifest, consent=True):
        """The template `tmpl.md` belongs to the core layer (K4): it is written only with `--to-layer core`
        into a writable core, so the helper pushes with that consent unless `consent=False`."""
        manifest_path = self.project_dir / ".github" / ".skillsync.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        skillsync.save_yaml(manifest_path, manifest)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if consent:
                self.config["layers"][0]["writable"] = True
                writer = skillsync.LayerWriter(skillsync.config_layers(self.config), to_layer="core")
                skillsync.sync_claude_md_to_library(manifest_path, self.config, writer=writer)
                writer.commit()
            else:
                skillsync.sync_claude_md_to_library(manifest_path, self.config)
        return out.getvalue()

    def test_push_of_a_core_template_without_consent_is_info_and_writes_nothing(self):
        template = self.library_dir / "instructions" / "tmpl.md"
        original = template.read_text()
        self.run_build(self.agents())
        agents = self.project_dir / "AGENTS.md"
        agents.write_text(agents.read_text().replace("Template rules", "Edited rules"))
        out = self.push(self.agents(), consent=False)
        self.assertIn("[info]", out)
        self.assertEqual(template.read_text(), original)

    def test_push_reads_block_without_codex_note_and_is_idempotent(self):
        template = self.library_dir / "instructions" / "tmpl.md"
        original = template.read_text()
        self.run_build(self.agents())
        self.push(self.agents())
        self.assertEqual(template.read_text(), original)
        agents = self.project_dir / "AGENTS.md"
        agents.write_text(agents.read_text().replace("Template rules", "Edited rules"))
        self.push(self.agents())
        self.assertIn("Edited rules", template.read_text())
        self.assertNotIn("$feature", template.read_text())
        self.assertNotIn("SKILLSYNC", template.read_text())
        self.assertNotIn("Base example", template.read_text())

    def test_push_skips_note_markers_outside_block(self):
        template = self.library_dir / "instructions" / "tmpl.md"
        original = template.read_text()
        self.run_build(self.agents())
        agents = self.project_dir / "AGENTS.md"
        text = agents.read_text()
        note = text[text.index(NOTE_BEGIN):text.index(NOTE_END) + len(NOTE_END)]
        agents.write_text(text.replace(note, "") + "\n" + note + "\n")
        out = self.push(self.agents())
        self.assertEqual(template.read_text(), original)
        self.assertIn("[warn]", out)

    def test_push_skips_broken_inner_markers(self):
        template = self.library_dir / "instructions" / "tmpl.md"
        original = template.read_text()
        self.run_build(self.agents())
        agents = self.project_dir / "AGENTS.md"
        agents.write_text(agents.read_text().replace(NOTE_END, ""))
        out = self.push(self.agents())
        self.assertEqual(template.read_text(), original)
        self.assertIn("[warn]", out)


if __name__ == "__main__":
    unittest.main()
