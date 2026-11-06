"""Folder-open task in .vscode/tasks.json (K6): shapes of the task, `is_managed_link_task`, refresh of our own stale task."""
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skillsync import SKILLSYNC_PATH, TempProjectMixin, skillsync
from tests.layers_helpers import needs_os_symlinks  # noqa: E402
from skillsync_workflow import selfcmd  # noqa: E402

OLD_SCRIPT = "/home/runner/.local/scripts/dev/skillsync"
TAIL = ["link", "${workspaceFolder}"]
PYTHON = "/opt/py/bin/python"
CHECKOUT = [PYTHON, "/work/Skillsync/skillsync"]
INSTALLED = [PYTHON, "-P", "-m", "skillsync_workflow"]
ENTRY = ["/home/runner/.local/bin/skillsync"]  # the user's entry: no interpreter in the task


def generated_task(script, python="/usr/bin/python3"):
    """The legacy shape: command = python, args = [wrapper, link, folder]."""
    return {"label": "skillsync: link", "type": "process", "command": python,
            "args": [script, "link", "${workspaceFolder}"], "runOptions": {"runOn": "folderOpen"},
            "presentation": {"reveal": "silent", "panel": "shared"}, "problemMatcher": []}


def task_for(argv):
    """The current shape for `argv`: `self_argv()` forms (Windows without an entry, installation) or ENTRY."""
    return {"label": "skillsync: link", "type": "process", "command": argv[0], "args": argv[1:] + TAIL,
            "runOptions": {"runOn": "folderOpen"}, "presentation": {"reveal": "silent", "panel": "shared"},
            "problemMatcher": []}


# Every shape that the generator ever wrote, as (name, task).
MANAGED_SHAPES = {
    "legacy (3 arguments)": generated_task(OLD_SCRIPT, python=PYTHON),
    "legacy, Windows path": generated_task("C:\\Users\\user\\scripts\\dev\\skillsync", python=PYTHON),
    "checkout": task_for([PYTHON, OLD_SCRIPT]),
    "checkout, Windows path": task_for([PYTHON, "C:\\Users\\user\\Skillsync\\skillsync"]),
    "checkout, interpreter flags": {**task_for([PYTHON, OLD_SCRIPT]), "args": ["-X", "utf8", OLD_SCRIPT] + TAIL},
    "installation": task_for([PYTHON, "-P", "-m", "skillsync_workflow"]),
    "installation, other interpreter": task_for(["/usr/bin/python3.12", "-P", "-m", "skillsync_workflow"]),
    "entry": task_for(ENTRY),
    "entry, Windows path": task_for(["C:\\Users\\user\\.local\\bin\\skillsync.exe"]),
    "entry found on PATH": task_for(["/usr/bin/skillsync"]),
}


# `selfcmd.os` as on Windows (only what selfcmd uses), without touching the real `os.name`.
NT_OS = types.SimpleNamespace(name="nt", path=os.path, access=os.access, X_OK=os.X_OK)


class IsManagedLinkTaskTests(unittest.TestCase):
    def test_every_generated_shape_is_managed(self):
        for name, task in MANAGED_SHAPES.items():
            with self.subTest(name):
                self.assertTrue(skillsync.is_managed_link_task(task))

    def test_user_tasks_and_near_misses_are_not_managed(self):
        base = task_for(CHECKOUT)
        shapes = {
            "wrapper under another name, new shape": {**base, "args": ["/x/my-wrapper"] + TAIL},
            "wrapper under another name, with flags": {**base, "args": ["-X", "utf8", "/x/my-wrapper"] + TAIL},
            "module under another name": {**base, "args": ["-P", "-m", "other_module"] + TAIL},
            "module without -P": {**base, "args": ["-m", "skillsync_workflow"] + TAIL},
            "-m without the module": {**base, "args": ["-P", "-m"] + TAIL},
            "only the tail": {**base, "args": list(TAIL)},
            "wrapper, link without the folder": {**base, "args": [OLD_SCRIPT, "link"]},
            "trailing argument after the tail": {**base, "args": CHECKOUT[1:] + TAIL + ["--dry-run"]},
            "another subcommand": {**base, "args": CHECKOUT[1:] + ["doctor", "${workspaceFolder}"]},
            "wrong folder variable": {**base, "args": CHECKOUT[1:] + ["link", "${fileDirname}"]},
            "args not a list": {**base, "args": "link ${workspaceFolder}"},
            "args with a non-string": {**base, "args": [OLD_SCRIPT, "link", 3]},
            "command not a string": {**base, "command": None},
            "no args": {k: v for k, v in base.items() if k != "args"},
            "different trigger": {**base, "runOptions": {"runOn": "default"}},
            "no trigger": {k: v for k, v in base.items() if k != "runOptions"},
            "shell type": {**base, "type": "shell"},
            "legacy with extra args": {**generated_task(OLD_SCRIPT), "args": [OLD_SCRIPT, "link", "${workspaceFolder}", "-v"]},
            "legacy with another name": {**generated_task(OLD_SCRIPT), "args": ["/x/my-wrapper"] + TAIL},
            "shell command line": {"label": "skillsync: link", "command": "skillsync link ${workspaceFolder}"},
            "tail only, command is an interpreter": {**base, "command": PYTHON, "args": list(TAIL)},
            "tail only, command is node": {**base, "command": "node", "args": list(TAIL)},
            "tail only, command name merely contains skillsync": {**base, "command": "/x/not-skillsync", "args": list(TAIL)},
            "tail only, command with a suffix": {**base, "command": "/x/skillsync.sh", "args": list(TAIL)},
            "tail only, shell type": {**task_for(ENTRY), "type": "shell"},
            "tail only, different trigger": {**task_for(ENTRY), "runOptions": {"runOn": "default"}},
        }
        for name, task in shapes.items():
            with self.subTest(name):
                self.assertFalse(skillsync.is_managed_link_task(task))


class FolderOpenTaskTests(TempProjectMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.library_dir / "skills" / "feature").mkdir()
        self.manifest = {"skills": ["feature"]}
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.target = self.project_dir / ".vscode" / "tasks.json"
        self.backup = self.target.with_name("tasks.json.skillsync-backup")

    def run_hook(self, argv=None, **kwargs):
        """argv None: real environment (HOME is a tmp dir, PATH lookup is mocked away).
        ENTRY: the user's entry is found. Any other argv: no entry, Windows-like, `self_argv()` is argv."""
        with mock.patch.object(selfcmd.shutil, "which", return_value=None):
            if argv is None:
                skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config, **kwargs)
            elif argv == ENTRY:
                with mock.patch.object(selfcmd, "_user_entry", return_value=ENTRY[0]):
                    skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config, **kwargs)
            else:
                with mock.patch.object(selfcmd, "os", NT_OS), \
                        mock.patch.object(selfcmd, "self_argv", return_value=list(argv)):
                    skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config, **kwargs)

    def install_entry(self, symlink=True):
        """Put `~/.local/bin/skillsync` into the tmp HOME: a symlink to the wrapper or a plain executable file."""
        entry = self.home / ".local" / "bin" / "skillsync"
        entry.parent.mkdir(parents=True, exist_ok=True)
        if symlink:
            entry.symlink_to(SKILLSYNC_PATH)
        else:
            entry.write_text("#!/bin/sh\n")
            entry.chmod(0o755)
        return entry

    def write_tasks(self, tasks, **extra):
        self.target.parent.mkdir(exist_ok=True)
        text = json.dumps({"version": "2.0.0", **extra, "tasks": tasks}, indent=2) + "\n"
        self.target.write_text(text)
        return text

    def test_new_task_has_the_expected_shape_for_every_form(self):
        for name, argv in (("checkout, no entry, Windows", CHECKOUT), ("installation", INSTALLED), ("entry", ENTRY)):
            with self.subTest(name):
                if self.target.parent.exists():
                    self.target.unlink()
                self.run_hook(argv)
                task = json.loads(self.target.read_text())["tasks"][0]
                self.assertEqual(task, task_for(argv))
                self.assertEqual((task["command"], task["args"][-2:]), (argv[0], TAIL))
                self.assertTrue(skillsync.is_managed_link_task(task))

    @unittest.skipIf(os.name == "nt", "POSIX: the wrapper is the command")
    def test_checkout_without_entry_runs_the_wrapper_itself(self):
        self.run_hook()
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual(task["args"], TAIL)
        self.assertNotEqual(task["command"], sys.executable)
        self.assertEqual(Path(task["command"]).resolve(), SKILLSYNC_PATH.resolve())
        self.assertTrue(os.access(task["command"], os.X_OK))
        self.assertTrue(skillsync.is_managed_link_task(task))

    @needs_os_symlinks
    def test_entry_in_home_is_written_unresolved_without_an_interpreter(self):
        entry = self.install_entry()
        self.run_hook()
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual(task, task_for([str(entry)]))
        self.assertNotIn(sys.executable, [task["command"]] + task["args"])
        self.assertNotEqual(task["command"], str(SKILLSYNC_PATH.resolve()))  # the symlink itself, not its target

    def test_entry_found_on_path_is_used_when_home_has_none(self):
        found = self.tmp / "bin" / "skillsync"
        found.parent.mkdir()
        found.write_text("#!/bin/sh\n")
        found.chmod(0o755)
        with mock.patch.object(selfcmd.shutil, "which", return_value=str(found)):
            skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual(task, task_for([str(found)]))

    def test_every_managed_shape_is_refreshed_to_every_current_form_and_the_rest_stays(self):
        for current_name, current in (("checkout", CHECKOUT), ("installation", INSTALLED), ("entry", ENTRY)):
            for name, stale in MANAGED_SHAPES.items():
                with self.subTest(current=current_name, stale=name):
                    build = {"label": "build", "command": "make"}
                    original = self.write_tasks([build, stale, {"label": "test", "command": "pytest"}],
                                                inputs=[{"id": "x"}])
                    if self.backup.exists():
                        self.backup.unlink()
                    self.run_hook(current)
                    if stale == task_for(current):  # already current: untouched, no backup
                        self.assertEqual(self.target.read_text(), original)
                        self.assertFalse(self.backup.exists())
                        continue
                    data = json.loads(self.target.read_text())
                    self.assertEqual(data["tasks"][0], build)
                    self.assertEqual(data["tasks"][2], {"label": "test", "command": "pytest"})
                    self.assertEqual(data["inputs"], [{"id": "x"}])
                    # command and the beginning of args are replaced, the tail and the rest of the task stay
                    self.assertEqual(data["tasks"][1], {**stale, "command": current[0], "args": current[1:] + TAIL})
                    self.assertEqual(self.backup.read_text(), original)

    def test_refresh_replaces_the_interpreter_of_a_legacy_task(self):
        self.write_tasks([generated_task(OLD_SCRIPT, python="/opt/old/bin/python")])
        self.run_hook(CHECKOUT)
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual((task["command"], task["args"]), (PYTHON, CHECKOUT[1:] + TAIL))

    def test_refresh_drops_the_interpreter_of_a_legacy_task_for_the_entry(self):
        self.write_tasks([generated_task(OLD_SCRIPT, python="/opt/old/bin/python")])
        self.run_hook(ENTRY)
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual((task["command"], task["args"]), (ENTRY[0], TAIL))

    def test_dry_run_reports_refresh_without_writing(self):
        original = self.write_tasks([generated_task(OLD_SCRIPT)])
        self.run_hook(dry_run=True)
        self.assertEqual(self.target.read_text(), original)
        self.assertFalse(self.backup.exists())

    def test_custom_tasks_with_our_label_are_left_alone(self):
        shapes = {
            "wrapper": {"label": "skillsync: link", "command": "/home/user/.local/bin/skillsync link ${workspaceFolder}"},
            "other script name": {**generated_task(OLD_SCRIPT), "args": ["/x/my-wrapper", "link", "${workspaceFolder}"]},
            "other script name, new shape": {**task_for(CHECKOUT), "args": ["/x/my-wrapper"] + TAIL},
            "other module": {**task_for(INSTALLED), "args": ["-P", "-m", "my_module"] + TAIL},
            "extra args": {**generated_task(OLD_SCRIPT), "args": [OLD_SCRIPT, "link", "${workspaceFolder}", "--dry-run"]},
            "extra args, new shape": {**task_for(CHECKOUT), "args": CHECKOUT[1:] + TAIL + ["--dry-run"]},
            "different trigger": {**generated_task(OLD_SCRIPT), "runOptions": {"runOn": "default"}},
            "different trigger, installation": {**task_for(INSTALLED), "runOptions": {"runOn": "default"}},
            "shell type": {**generated_task(OLD_SCRIPT), "type": "shell"},
            "node task": {"label": "skillsync: link", "type": "process", "command": "node", "args": ["sync.js"],
                          "runOptions": {"runOn": "folderOpen"}},
            "link tail, command is not a skillsync entry": {**task_for(ENTRY), "command": "node", "args": list(TAIL)},
            "link tail, command name merely contains skillsync": {**task_for(ENTRY), "command": "/x/not-skillsync"},
            "entry shape, shell type": {**task_for(ENTRY), "type": "shell"},
            "entry shape, different trigger": {**task_for(ENTRY), "runOptions": {"runOn": "default"}},
        }
        for current in (CHECKOUT, INSTALLED, ENTRY):
            for name, task in shapes.items():
                with self.subTest(name, current=current[-1]):
                    if self.backup.exists():
                        self.backup.unlink()
                    original = self.write_tasks([task])
                    self.run_hook(current)
                    self.assertEqual(self.target.read_text(), original)
                    self.assertFalse(self.backup.exists())

    def test_a_user_task_with_our_label_next_to_nothing_managed_does_not_get_a_second_task(self):
        user = {"label": "skillsync: link", "command": "my-sync"}
        original = self.write_tasks([user])
        self.run_hook(CHECKOUT)
        self.assertEqual(self.target.read_text(), original)

    def test_current_managed_task_is_idempotent_for_every_form_and_makes_no_new_backup(self):
        for name, argv in (("checkout", CHECKOUT), ("installation", INSTALLED), ("entry", ENTRY)):
            with self.subTest(name):
                if self.target.parent.exists():
                    self.target.unlink()
                self.run_hook(argv)
                self.assertFalse(self.backup.exists())  # nothing to back up on first creation
                content, mtime = self.target.read_bytes(), self.target.stat().st_mtime_ns
                self.run_hook(argv)
                self.assertEqual((self.target.read_bytes(), self.target.stat().st_mtime_ns), (content, mtime))
                self.assertFalse(self.backup.exists())

    def test_switching_the_form_refreshes_once_and_then_stays(self):
        self.run_hook(CHECKOUT)
        self.run_hook(INSTALLED)
        self.assertEqual(json.loads(self.target.read_text())["tasks"][0], task_for(INSTALLED))
        content = self.target.read_bytes()
        self.run_hook(INSTALLED)
        self.assertEqual(self.target.read_bytes(), content)
        self.run_hook(ENTRY)
        self.assertEqual(json.loads(self.target.read_text())["tasks"][0], task_for(ENTRY))
        content = self.target.read_bytes()
        self.run_hook(ENTRY)
        self.assertEqual(self.target.read_bytes(), content)

    def test_refresh_is_idempotent_and_does_not_rewrite_the_backup(self):
        original = self.write_tasks([generated_task(OLD_SCRIPT)])
        self.run_hook(CHECKOUT)
        self.assertEqual(self.backup.read_text(), original)
        content, backup_mtime = self.target.read_bytes(), self.backup.stat().st_mtime_ns
        self.run_hook(CHECKOUT)
        self.assertEqual(self.target.read_bytes(), content)
        self.assertEqual(self.backup.stat().st_mtime_ns, backup_mtime)
        self.assertEqual(self.backup.read_text(), original)

    @needs_os_symlinks
    def test_a_home_symlink_to_the_wrapper_is_the_command_of_the_task(self):
        link = self.install_entry()
        self.run_hook()
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual((task["command"], task["args"]), (str(link), TAIL))
        self.assertTrue(skillsync.is_managed_link_task(task))

    def _two_runs(self, interpreters):
        """Run the hook once per interpreter on the same project; return the bytes after each run."""
        results = []
        for exe in interpreters:
            with mock.patch.object(sys, "executable", exe), mock.patch.object(selfcmd.shutil, "which", return_value=None):
                skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
            results.append(self.target.read_bytes())
        return results

    def _assert_independent_of_interpreter(self):
        first, second = self._two_runs(["/opt/py-one/bin/python3.11", "/home/user/.venv/bin/python3.13"])
        self.assertEqual(first, second)
        self.assertFalse(self.backup.exists())  # the second run changed nothing, so nothing was backed up
        for exe in ("py-one", ".venv"):
            self.assertNotIn(exe, first.decode())
        self.assertTrue(skillsync.is_managed_link_task(json.loads(first)["tasks"][0]))

    @needs_os_symlinks
    def test_task_does_not_depend_on_the_interpreter_with_a_home_symlink_entry(self):
        self.install_entry()
        self._assert_independent_of_interpreter()

    @unittest.skipIf(os.name == "nt", "POSIX: the wrapper is the command")
    def test_task_does_not_depend_on_the_interpreter_in_a_checkout_without_entry(self):
        self._assert_independent_of_interpreter()

    def test_task_does_not_depend_on_the_interpreter_in_an_installation_with_an_entry_file(self):
        self.install_entry(symlink=False)
        with mock.patch.object(selfcmd, "_wrapper", return_value=None):
            self._assert_independent_of_interpreter()

    @needs_os_symlinks
    def test_a_python_plus_wrapper_task_from_the_repo_is_refreshed_once_to_the_entry_shape(self):
        entry = self.install_entry()
        legacy = generated_task(str(entry), python="/home/user/Repos/x/.venv/bin/python")
        original = self.write_tasks([{"label": "build", "command": "make"}, legacy])
        self.run_hook()
        data = json.loads(self.target.read_text())
        self.assertEqual(data["tasks"][1], {**legacy, "command": str(entry), "args": TAIL})
        self.assertEqual(data["tasks"][0], {"label": "build", "command": "make"})
        self.assertEqual(self.backup.read_text(), original)
        content, backup_mtime = self.target.read_bytes(), self.backup.stat().st_mtime_ns
        for exe in ("/opt/a/python", "/opt/b/python"):
            with mock.patch.object(sys, "executable", exe):
                self.run_hook()
            self.assertEqual(self.target.read_bytes(), content)
            self.assertEqual(self.backup.stat().st_mtime_ns, backup_mtime)
            self.assertEqual(self.backup.read_text(), original)

    @needs_os_symlinks
    def test_a_user_task_with_our_label_is_untouched_when_an_entry_exists(self):
        self.install_entry()
        for task in ({"label": "skillsync: link", "command": "node", "args": ["sync.js"]},
                     {"label": "skillsync: link", "type": "process", "command": "node", "args": list(TAIL),
                      "runOptions": {"runOn": "folderOpen"}}):
            with self.subTest(task["args"]):
                original = self.write_tasks([task])
                if self.backup.exists():
                    self.backup.unlink()
                self.run_hook()
                self.assertEqual(self.target.read_text(), original)
                self.assertFalse(self.backup.exists())

    def test_without_entry_and_wrapper_on_windows_the_task_is_the_self_command(self):
        with mock.patch.object(selfcmd, "os", NT_OS), mock.patch.object(selfcmd, "_wrapper", return_value=None), \
                mock.patch.object(selfcmd.shutil, "which", return_value=None), \
                mock.patch.object(sys, "executable", PYTHON):
            self.assertEqual(selfcmd.link_task_argv(), selfcmd.self_argv())
            self.assertEqual(selfcmd.link_task_argv(), INSTALLED)
            skillsync.ensure_link_on_open(self.project_dir, self.manifest, self.config)
        task = json.loads(self.target.read_text())["tasks"][0]
        self.assertEqual(task, task_for(INSTALLED))

    def test_without_entry_on_windows_a_checkout_uses_interpreter_and_wrapper(self):
        with mock.patch.object(selfcmd, "os", NT_OS), mock.patch.object(selfcmd.shutil, "which", return_value=None), \
                mock.patch.object(sys, "executable", PYTHON):
            argv = selfcmd.link_task_argv()
        self.assertEqual(argv[0], PYTHON)
        self.assertEqual(Path(argv[1]).resolve(), SKILLSYNC_PATH.resolve())
        self.assertEqual(len(argv), 2)


if __name__ == "__main__":
    unittest.main()
