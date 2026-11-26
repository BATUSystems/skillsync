"""CORE-1180 M-1: the registry of worktrees of one git project (`skillsync_workflow/dashboard_worktrees.py`), tested against the
contract of the spec (R1 trace of work, R2 back-link, R3 exclusion, tab id, limit and order), never against the implementation.

The layout of a repository is built from plain files (HEAD, `.git` files, `worktrees/<id>/{gitdir,commondir,HEAD,locked}`): no test
runs `git` except `RealGitRelativePaths` (the real `git worktree add`). Nothing here touches the real state directories of Skillsync (`guard_real_state`).
Symlink scenarios create real symlinks; where the system refuses to (Windows without the privilege) the one test says so with
`skipTest`; `RealGitRelativePaths` skips without git or with git older than 2.48."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from skillsync_workflow import dashboard_worktrees as dw  # noqa: E402
from layers_helpers import guard_real_state  # noqa: E402
from tmp_paths import child  # noqa: E402

NOW = 1_800_000_000.0
DAY = 86400.0
SHA_A = "a" * 40
SHA_B = "0123456789abcdef" * 2 + "01234567"


def real(path) -> str:
    return os.path.realpath(str(path))


def write(path, text="x\n", *, mtime=None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def same(a, b) -> bool:
    return dw.normalize_path(str(a)) == dw.normalize_path(str(b))


def base(entry) -> str:
    return dw.normalize_path(entry.path).rstrip("/").rsplit("/", 1)[-1]


def path_of(item) -> str:
    """`Registry.excluded` may hold entries or plain paths: the contract only says "excluded"."""
    return item if isinstance(item, str) else item.path


def excluded_bases(registry) -> list:
    return [dw.normalize_path(path_of(item)).rstrip("/").rsplit("/", 1)[-1] for item in registry.excluded]


def ids(entries) -> list:
    return [entry.id for entry in entries]


def bases(entries) -> list:
    return [base(entry) for entry in entries]


def id_matches(value) -> bool:
    pattern = dw.ID_PATTERN
    return re.fullmatch(getattr(pattern, "pattern", pattern), value) is not None


class Layout(unittest.TestCase):
    """A main directory with a `.git` directory in a temporary tree; worktrees added file by file."""

    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(real(tempfile.mkdtemp(prefix="dashboard-worktrees-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "main"
        self.common = self.main / ".git"
        write(self.common / "HEAD", "ref: refs/heads/main\n")
        write(self.common / "config", "[core]\n\trepositoryformatversion = 0\n")
        (self.common / "refs" / "heads").mkdir(parents=True)
        write(self.common / "refs" / "heads" / "main", SHA_A + "\n")
        self.warnings: list = []

    def add_worktree(self, name, *, entry_id=None, relative=False, head=None, locked=False, make_dir=True, backlink=True, parent=None):
        """Adds `<parent or tmp>/<name>` as a worktree the way git lays it out; returns the working directory."""
        entry_id = entry_id or name
        wt = (Path(parent) if parent else self.tmp) / name
        entry = child(self.common / "worktrees", entry_id)
        if make_dir:
            wt.mkdir(parents=True, exist_ok=True)
        write(entry / "HEAD", head if head is not None else f"ref: refs/heads/{name}\n")
        write(entry / "commondir", "../..\n")
        if relative:
            write(entry / "gitdir", os.path.relpath(wt / ".git", entry).replace(os.sep, "/") + "\n")
            pointer = "gitdir: " + os.path.relpath(entry, wt).replace(os.sep, "/") + "\n"
        else:
            write(entry / "gitdir", str(wt / ".git") + "\n")
            pointer = f"gitdir: {entry}\n"
        if make_dir and backlink:
            write(wt / ".git", pointer)
        if locked:
            write(entry / "locked", "reason\n")
        return wt

    def entry_dir(self, entry_id) -> Path:
        return child(self.common / "worktrees", entry_id)

    def project(self, root=None):
        project = dw.project_identity(str(root or self.main))
        self.assertIsNotNone(project)
        return project

    def listed(self, root=None) -> list:
        return dw.list_entries(self.project(root), warn=self.warnings.append)

    def by_base(self, entries=None) -> dict:
        return {base(entry): entry for entry in (entries if entries is not None else self.listed())}

    def run_dir(self, wt, name="r1", *, mtime=None) -> Path:
        return write(child(Path(wt) / ".skillsync" / "runs", name) / "status.md", "run\n", mtime=mtime)

    def symlink(self, target, link) -> None:
        try:
            os.symlink(str(target), str(link), target_is_directory=Path(target).is_dir())
        except (OSError, NotImplementedError):
            self.skipTest("this system refuses to create symlinks for the user")


def git_version():
    """The version of the `git` on PATH as a tuple of ints, or None without git."""
    try:
        text = subprocess.run(["git", "--version"], capture_output=True, text=True, encoding="utf-8", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(part or 0) for part in found.groups()) if found else None


class RealGitRelativePaths(unittest.TestCase):
    """A worktree made by the REAL `git worktree add --relative-paths` (git >= 2.48, `worktree.useRelativePaths`) is listed with its resolved path: the
    other tests build the files by hand, so only this one proves that what git writes is what `dashboard_worktrees` reads."""

    def setUp(self) -> None:
        guard_real_state(self)
        if git_version() is None:
            self.skipTest("git is not installed")
        self.tmp = Path(real(tempfile.mkdtemp(prefix="dashboard-realgit-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "main"
        self.git("init", "-q", "-b", "main", str(self.main), cwd=self.tmp)
        (self.main / "a.txt").write_text("a\n", encoding="utf-8")
        self.git("add", "a.txt")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")

    def git(self, *args, cwd=None):
        done = subprocess.run(["git", *args], cwd=str(cwd or self.main), capture_output=True, text=True, encoding="utf-8", timeout=60,
                              env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_a_worktree_added_with_relative_paths_is_listed_and_its_back_link_confirmed(self) -> None:
        version = git_version()
        if version < (2, 48):
            self.skipTest(f"git >= 2.48 is needed for --relative-paths (found {'.'.join(map(str, version))})")
        wt = self.tmp / "wt-rel"
        self.git("worktree", "add", "--relative-paths", "-q", "-b", "feature/rel", str(wt))
        warnings: list = []
        project = dw.project_identity(str(self.main))
        self.assertIsNotNone(project)
        entries = dw.list_entries(project, warn=warnings.append)
        self.assertEqual([same(entry.path, wt) for entry in entries if base(entry) == "wt-rel"], [True])
        self.assertEqual(warnings, [])
        pointer = (wt / ".git").read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp).replace(os.sep, "/"), pointer.replace("\\", "/"), "the pointer file is relative, as the test needs it")

    def test_a_worktree_added_with_absolute_paths_is_listed_too(self) -> None:
        wt = self.tmp / "wt-abs"
        self.git("worktree", "add", "-q", "-b", "feature/abs", str(wt))
        entries = dw.list_entries(dw.project_identity(str(self.main)), warn=lambda text: None)
        self.assertEqual([same(entry.path, wt) for entry in entries if base(entry) == "wt-abs"], [True])


class NormalizeAndId(unittest.TestCase):
    def setUp(self) -> None:
        guard_real_state(self)
        self.tmp = Path(real(tempfile.mkdtemp(prefix="dashboard-worktrees-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_constants_of_the_contract(self) -> None:
        self.assertEqual(dw.MAX_VISIBLE, 16)
        self.assertEqual(dw.DEFAULT_INACTIVE_DAYS, 7)
        for good in ("main", "0123456789ab", "ffffffffffff"):
            self.assertTrue(id_matches(good), good)
        for bad in ("", "../etc", "Main", "ABCDEF012345", "0123456789a", "0123456789abc", "0123456789ag", "main/x", "mainx"):
            self.assertFalse(id_matches(bad), bad)

    def test_normalize_path_is_posix_and_keeps_the_case(self) -> None:
        folder = self.tmp / "MiXed" / "Dir"
        folder.mkdir(parents=True)
        result = dw.normalize_path(str(folder))
        self.assertNotIn("\\", result)
        self.assertTrue(result.endswith("/MiXed/Dir"), result)

    def test_normalize_path_collapses_dots_and_follows_symlinks(self) -> None:
        (self.tmp / "a").mkdir()
        (self.tmp / "b").mkdir()
        self.assertEqual(dw.normalize_path(str(self.tmp / "a" / ".." / "b")), dw.normalize_path(str(self.tmp / "b")))
        try:
            os.symlink(str(self.tmp / "b"), str(self.tmp / "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this system refuses to create symlinks for the user")
        self.assertEqual(dw.normalize_path(str(self.tmp / "link")), dw.normalize_path(str(self.tmp / "b")))

    def test_normalize_path_of_a_missing_path_does_not_raise(self) -> None:
        self.assertIsInstance(dw.normalize_path(str(self.tmp / "nope" / "deeper")), str)

    def test_key_of_the_same_path_is_stable(self) -> None:
        self.assertEqual(dw.normalize_key(str(self.tmp / "x")), dw.normalize_key(str(self.tmp / "x")))

    def test_key_folds_the_case_on_macos(self) -> None:
        # Platform-neutral text paths ("C:/Work/x/Repo") are compared as text on any system, so only the case fold differs.
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(dw.normalize_key("C:/Work/x/Repo"), dw.normalize_key("c:/work/X/REPO"))
            self.assertEqual(dw.tab_id("C:/Work/x/Repo"), dw.tab_id("c:/work/x/repo"))

    def test_key_folds_the_case_on_windows(self) -> None:
        # The switch for Windows is the OS itself (not patchable from a test on another system): asserted where it is real.
        if os.name == "nt":
            self.assertEqual(dw.normalize_key("C:/Work/x/Repo"), dw.normalize_key("c:/work/X/REPO"))
            self.assertEqual(dw.tab_id("C:/Work/x/Repo"), dw.tab_id("c:/work/x/repo"))
        self.assertEqual(dw.normalize_key(dw.normalize_key("Q/Z")), dw.normalize_key("Q/Z"))   # idempotent on every system

    def test_key_keeps_the_case_on_linux(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows folds the letter case of paths; the case-keeping key is asserted on Linux")
        with mock.patch.object(sys, "platform", "linux"):
            self.assertNotEqual(dw.normalize_key("/srv/Repo"), dw.normalize_key("/srv/repo"))
            self.assertNotEqual(dw.tab_id("/srv/Repo"), dw.tab_id("/srv/repo"))

    def test_windows_style_separators_are_one_path(self) -> None:
        # Backslashes are separators only where the OS says so: asserted on Windows, on other systems nothing portable can be claimed.
        if os.name == "nt":
            self.assertEqual(dw.normalize_key("C:\\Work\\x\\Repo"), dw.normalize_key("C:/Work/x/Repo"))
            self.assertEqual(dw.tab_id("C:\\Work\\x\\Repo"), dw.tab_id("C:/Work/x/Repo"))
        self.assertNotIn("\\", dw.normalize_path("C:/Work/x/Repo"))

    def test_tab_id_is_12_lowercase_hex_and_stable(self) -> None:
        one = dw.tab_id(str(self.tmp / "wt"))
        self.assertEqual(one, dw.tab_id(str(self.tmp / "wt")))
        self.assertRegex(one, r"^[0-9a-f]{12}$")
        self.assertTrue(id_matches(one))

    def test_tab_id_is_the_prefix_of_the_sha256_of_the_key(self) -> None:
        path = str(self.tmp / "wt")
        self.assertEqual(dw.tab_id(path), hashlib.sha256(dw.normalize_key(path).encode("utf-8")).hexdigest()[:12])

    def test_tab_id_differs_per_directory(self) -> None:
        self.assertNotEqual(dw.tab_id(str(self.tmp / "a")), dw.tab_id(str(self.tmp / "b")))

    def test_tab_id_of_the_main_directory_is_main(self) -> None:
        (self.tmp / "main").mkdir()
        self.assertEqual(dw.tab_id(str(self.tmp / "main"), str(self.tmp / "main")), "main")
        self.assertEqual(dw.tab_id(str(self.tmp / "main" / "sub" / ".."), str(self.tmp / "main")), "main")
        self.assertEqual(dw.tab_id(str(self.tmp / "main") + "/", main_path=str(self.tmp / "main")), "main")

    def test_tab_id_of_another_directory_is_never_main(self) -> None:
        self.assertNotEqual(dw.tab_id(str(self.tmp / "wt"), str(self.tmp / "main")), "main")
        self.assertNotEqual(dw.tab_id(str(self.tmp / "main")), "main")   # without a main path nothing is "main"


class ProjectIdentity(Layout):
    def test_main_directory_of_a_plain_repository(self) -> None:
        project = self.project()
        self.assertTrue(same(project.root, self.main))
        self.assertTrue(same(project.common_dir, self.common))
        self.assertTrue(same(project.main_path, self.main))

    def test_worktree_directory_points_at_the_main_directory(self) -> None:
        wt = self.add_worktree("wt1")
        project = self.project(wt)
        self.assertTrue(same(project.root, wt))
        self.assertTrue(same(project.common_dir, self.common))
        self.assertTrue(same(project.main_path, self.main))

    def test_relative_gitdir_and_commondir(self) -> None:
        wt = self.add_worktree("wt1", relative=True)
        self.assertTrue((wt / ".git").read_text(encoding="utf-8").startswith("gitdir: .."))
        project = self.project(wt)
        self.assertTrue(same(project.common_dir, self.common))
        self.assertTrue(same(project.main_path, self.main))

    def test_absolute_commondir_file(self) -> None:
        wt = self.add_worktree("wt1")
        write(self.entry_dir("wt1") / "commondir", str(self.common) + "\n")
        self.assertTrue(same(self.project(wt).common_dir, self.common))

    def test_a_directory_without_git_is_not_a_project(self) -> None:
        (self.tmp / "plain").mkdir()
        self.assertIsNone(dw.project_identity(str(self.tmp / "plain")))
        self.assertIsNone(dw.project_identity(str(self.tmp / "missing")))

    def test_corrupt_git_entries_are_none_not_an_exception(self) -> None:
        for name, payload in (("binary", b"\xff\xfe\x00\x01\x80garbage\x00\x00"), ("empty", b""), ("nul", b"gitdir: /x\x00/y\n"),
                              ("noprefix", b"just text\n"), ("missing-target", f"gitdir: {self.tmp}/no/such\n".encode())):
            with self.subTest(case=name):
                folder = child(self.tmp, "corrupt-" + name)
                write(folder / ".git", payload)
                self.assertIsNone(dw.project_identity(str(folder)))

    def test_git_directory_that_is_empty_is_none(self) -> None:
        (self.tmp / "hollow" / ".git").mkdir(parents=True)
        self.assertIsNone(dw.project_identity(str(self.tmp / "hollow")))

    def test_bare_repository_is_none(self) -> None:
        bare = self.tmp / "bare.git"          # the git directory itself is the folder: no `.git` in it
        write(bare / "HEAD", "ref: refs/heads/main\n")
        write(bare / "config", "[core]\n\tbare = true\n")
        (bare / "refs").mkdir()
        self.assertIsNone(dw.project_identity(str(bare)))

    def test_bare_flag_in_a_dot_git_directory_is_none(self) -> None:
        repo = self.tmp / "flagged"
        write(repo / ".git" / "HEAD", "ref: refs/heads/main\n")
        write(repo / ".git" / "config", "[core]\n\tbare = true\n")
        self.assertIsNone(dw.project_identity(str(repo)))

    def test_worktree_of_a_bare_repository_is_none(self) -> None:
        bare = self.tmp / "bare.git"
        write(bare / "HEAD", "ref: refs/heads/main\n")
        write(bare / "config", "[core]\n\tbare = true\n")
        wt = self.tmp / "from-bare"
        entry = bare / "worktrees" / "from-bare"
        write(entry / "HEAD", "ref: refs/heads/x\n")
        write(entry / "commondir", "../..\n")
        write(entry / "gitdir", str(wt / ".git") + "\n")
        write(wt / ".git", f"gitdir: {entry}\n")
        self.assertIsNone(dw.project_identity(str(wt)))

    def test_submodule_is_none(self) -> None:
        modules = self.common / "modules" / "sub"
        write(modules / "HEAD", "ref: refs/heads/main\n")
        sub = self.main / "sub"
        write(sub / ".git", "gitdir: ../.git/modules/sub\n")
        self.assertIsNone(dw.project_identity(str(sub)))

    def test_submodule_with_an_absolute_gitdir_is_none(self) -> None:
        modules = self.common / "modules" / "sub2"
        write(modules / "HEAD", "ref: refs/heads/main\n")
        sub = self.main / "sub2"
        write(sub / ".git", f"gitdir: {modules}\n")
        self.assertIsNone(dw.project_identity(str(sub)))


class ListEntries(Layout):
    def test_plain_repository_has_only_the_main_directory(self) -> None:
        entries = self.listed()
        self.assertEqual(ids(entries), ["main"])
        self.assertTrue(same(entries[0].path, self.main))
        self.assertEqual(entries[0].branch, "main")
        self.assertFalse(entries[0].detached)
        self.assertFalse(entries[0].locked)
        self.assertEqual(self.warnings, [])

    def test_main_entry_reports_the_head_commit(self) -> None:
        self.assertEqual(self.listed()[0].head, SHA_A[:12])

    def test_worktree_with_a_back_link_is_accepted(self) -> None:
        wt = self.add_worktree("wt1")
        entries = self.listed()
        self.assertEqual(ids(entries)[0], "main")
        self.assertEqual(len(entries), 2)
        entry = entries[1]
        self.assertTrue(same(entry.path, wt))
        self.assertEqual(entry.id, dw.tab_id(str(wt), str(self.main)))
        self.assertRegex(entry.id, r"^[0-9a-f]{12}$")
        self.assertEqual(entry.branch, "wt1")
        self.assertNotEqual(entry.kind, entries[0].kind)
        self.assertEqual(self.warnings, [])

    def test_listing_from_a_worktree_directory_gives_the_same_project(self) -> None:
        wt = self.add_worktree("wt1")
        from_wt = dw.list_entries(self.project(wt), warn=self.warnings.append)
        self.assertEqual(ids(from_wt), ids(self.listed()))

    def test_entries_without_a_back_link_are_rejected_with_a_warning(self) -> None:
        self.add_worktree("lonely", backlink=False)       # the working directory has no `.git` file
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)
        self.assertTrue(all(isinstance(message, str) for message in self.warnings))

    def test_the_git_file_pointing_at_another_entry_is_rejected(self) -> None:
        wt = self.add_worktree("wt1")
        self.add_worktree("wt2")
        write(wt / ".git", f"gitdir: {self.entry_dir('wt2')}\n")     # wt1 claims wt2's entry; wt2 entry claims wt2's dir
        entries = self.by_base()
        self.assertNotIn("wt1", entries)
        self.assertIn("wt2", entries)
        self.assertTrue(self.warnings)

    def test_gitdir_pointing_outside_the_project_is_rejected(self) -> None:
        wt = self.add_worktree("wt1")
        foreign = Path(real(tempfile.mkdtemp(prefix="dashboard-foreign-")))
        self.addCleanup(shutil.rmtree, foreign, ignore_errors=True)
        other = foreign / "other"
        other_entry = foreign / "otherrepo" / ".git" / "worktrees" / "other"
        write(other_entry / "gitdir", str(other / ".git") + "\n")
        write(other_entry / "commondir", "../..\n")
        write(other_entry / "HEAD", "ref: refs/heads/other\n")
        write(other / ".git", f"gitdir: {other_entry}\n")
        write(self.entry_dir("wt1") / "gitdir", str(other / ".git") + "\n")     # our entry points into another repository
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)
        self.assertTrue(wt.is_dir())

    def test_gitdir_pointing_at_an_unrelated_file_is_rejected(self) -> None:
        self.add_worktree("wt1")
        victim = write(self.tmp / "elsewhere" / "passwd", "root:x:0:0\n")
        write(self.entry_dir("wt1") / "gitdir", str(victim) + "\n")
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)

    def test_commondir_of_another_repository_is_rejected(self) -> None:
        self.add_worktree("wt1")
        other_common = self.tmp / "other" / ".git"
        write(other_common / "HEAD", "ref: refs/heads/main\n")
        write(self.entry_dir("wt1") / "commondir", str(other_common) + "\n")
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)

    def test_relative_paths_in_both_files_are_resolved_against_the_file(self) -> None:
        wt = self.add_worktree("wt1", relative=True)
        entries = self.listed()
        self.assertEqual(len(entries), 2)
        self.assertTrue(same(entries[1].path, wt))
        self.assertEqual(self.warnings, [])

    def test_relative_paths_do_not_resolve_against_the_working_directory(self) -> None:
        wt = self.add_worktree("wt1", relative=True)
        elsewhere = self.tmp / "cwd-elsewhere"
        elsewhere.mkdir()
        previous = os.getcwd()
        os.chdir(str(elsewhere))
        self.addCleanup(os.chdir, previous)
        entries = dw.list_entries(dw.project_identity(str(self.main)), warn=self.warnings.append)
        self.assertEqual(len(entries), 2)
        self.assertTrue(same(entries[1].path, wt))

    def test_relative_entry_gitdir_with_an_absolute_pointer_file(self) -> None:
        wt = self.add_worktree("wt1", relative=True)
        write(wt / ".git", f"gitdir: {self.entry_dir('wt1')}\n")
        self.assertEqual(len(self.listed()), 2)

    def test_relative_path_that_leads_to_the_wrong_directory_is_rejected(self) -> None:
        self.add_worktree("wt1", relative=True)
        write(self.entry_dir("wt1") / "gitdir", "../../../wt1-not-there/.git\n")
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)

    def test_missing_worktree_directory_is_rejected(self) -> None:
        wt = self.add_worktree("gone", make_dir=False)
        self.assertFalse(wt.exists())
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)

    def test_prunable_entry_whose_directory_vanished_is_rejected(self) -> None:
        wt = self.add_worktree("wt1")
        shutil.rmtree(str(wt))
        self.assertEqual(ids(self.listed()), ["main"])
        self.assertTrue(self.warnings)

    def test_locked_worktree_is_flagged(self) -> None:
        self.add_worktree("held", locked=True)
        self.add_worktree("free")
        entries = self.by_base()
        self.assertTrue(entries["held"].locked)
        self.assertFalse(entries["free"].locked)

    def test_detached_head_is_flagged_and_has_no_branch(self) -> None:
        self.add_worktree("det", head=SHA_B + "\n")
        entry = self.by_base()["det"]
        self.assertTrue(entry.detached)
        self.assertIsNone(entry.branch)
        self.assertEqual(entry.head, SHA_B[:12])

    def test_branch_with_a_slash_and_its_commit(self) -> None:
        self.add_worktree("feat", head="ref: refs/heads/feat/deep\n")
        write(self.common / "refs" / "heads" / "feat" / "deep", SHA_B + "\n")
        entry = self.by_base()["feat"]
        self.assertFalse(entry.detached)
        self.assertEqual(entry.branch, "feat/deep")
        self.assertEqual(entry.head, SHA_B[:12])

    def test_head_of_an_unknown_branch_is_none_not_an_error(self) -> None:
        self.add_worktree("fresh", head="ref: refs/heads/unborn\n")
        entry = self.by_base()["fresh"]
        self.assertEqual(entry.branch, "unborn")
        self.assertIsNone(entry.head)

    def test_head_outside_refs_heads_counts_as_detached(self) -> None:
        self.add_worktree("odd", head="ref: refs/remotes/origin/main\n")
        self.assertTrue(self.by_base()["odd"].detached)

    def test_corrupt_entries_are_skipped_with_a_warning_and_good_ones_survive(self) -> None:
        self.add_worktree("good")
        for name, payload in (("bin", b"\xff\xfe\x00\x80\x81"), ("empty", b""), ("nul", b"/tmp/x\x00/y/.git\n")):
            entry = self.common / "worktrees" / ("bad-" + name)
            write(entry / "gitdir", payload)
            write(entry / "commondir", "../..\n")
            write(entry / "HEAD", "ref: refs/heads/x\n")
        entries = self.listed()
        self.assertEqual(sorted(bases(entries)), ["good", "main"])
        self.assertGreaterEqual(len(self.warnings), 3)

    def test_corrupt_files_inside_an_otherwise_good_entry(self) -> None:
        for name, filename, payload in (("c1", "commondir", b"\xff\x00"), ("c2", "commondir", b""), ("c3", "HEAD", b"\xff\xfe\x00"),
                                        ("c4", "HEAD", b""), ("c5", "locked", b"")):
            self.add_worktree(name)
            write(self.entry_dir(name) / filename, payload)
        entries = self.listed()          # must not raise; whatever is accepted must still be a real worktree of this project
        self.assertEqual(ids(entries)[0], "main")
        for entry in entries[1:]:
            self.assertRegex(entry.id, r"^[0-9a-f]{12}$")

    def test_entry_that_is_a_file_not_a_directory_is_ignored(self) -> None:
        write(self.common / "worktrees" / "stray.txt", "x\n")
        self.assertEqual(ids(self.listed()), ["main"])

    def test_directory_swapped_for_a_symlink_after_the_registry_was_built(self) -> None:
        wt = self.add_worktree("wt1")
        self.assertEqual(len(self.listed()), 2)                  # the registry as built
        decoy = self.tmp / "decoy"
        wt.rename(decoy)
        self.symlink(decoy, wt)                                  # same path, now a link to the moved directory
        entries = self.listed()                                  # the next call must refuse the path that leaves through a link
        self.assertEqual(ids(entries), ["main"])
        self.assertTrue(self.warnings)

    def test_git_file_that_is_itself_a_symlink_is_rejected(self) -> None:
        wt = self.add_worktree("wt1")
        pointer = wt / ".git"
        real_pointer = write(self.tmp / "pointers" / "git-file", pointer.read_text(encoding="utf-8"))
        pointer.unlink()
        self.symlink(real_pointer, pointer)
        self.assertEqual(ids(self.listed()), ["main"])

    def test_parent_of_the_worktree_replaced_by_a_symlink_fails_the_revalidation(self) -> None:
        # a stateless listing cannot tell (legitimate setups have linked ancestors); the check right before a write does
        parent = self.tmp / "parent"
        wt = self.add_worktree("wt1", parent=parent)
        project = dw.project_identity(str(self.main))
        registered = [e for e in dw.list_entries(project) if e.kind == "worktree"][0]
        self.assertTrue(dw.revalidate(project, registered))
        moved = self.tmp / "parent-moved"
        parent.rename(moved)
        self.symlink(moved, parent)
        self.assertTrue(wt.parent.is_symlink())
        self.assertFalse(dw.revalidate(project, registered))

    def test_revalidate_is_false_for_a_vanished_worktree(self) -> None:
        wt = self.add_worktree("wt1")
        project = dw.project_identity(str(self.main))
        registered = [e for e in dw.list_entries(project) if e.kind == "worktree"][0]
        shutil.rmtree(wt)
        self.assertFalse(dw.revalidate(project, registered))

    def test_id_collision_keeps_the_first_and_warns_about_the_second(self) -> None:
        self.add_worktree("wt1")
        self.add_worktree("wt2")
        original = dw.tab_id

        def colliding(path, main_path=None):
            value = original(path, main_path)
            return value if value == "main" else "abcdefabcdef"
        with mock.patch.object(dw, "tab_id", side_effect=colliding):
            entries = self.listed()
        self.assertEqual(ids(entries), ["main", "abcdefabcdef"])
        self.assertTrue(self.warnings)

    def test_main_directory_is_listed_first_with_the_id_main(self) -> None:
        self.add_worktree("a")
        self.add_worktree("b")
        self.assertEqual(ids(self.listed())[0], "main")
        self.assertEqual(ids(self.listed()).count("main"), 1)

    def test_main_directory_missing_means_no_main_entry(self) -> None:
        wt = self.add_worktree("wt1")
        project = dw.Project(root=str(wt), common_dir=str(self.common), main_path=str(self.tmp / "gone"))
        entries = dw.list_entries(project, warn=self.warnings.append)
        self.assertNotIn("main", ids(entries))
        self.assertEqual(bases(entries), ["wt1"])

    def test_worktree_manifests_and_ports_are_not_read(self) -> None:
        wt = self.add_worktree("wt1")
        write(wt / ".github" / ".skillsync.yaml", "skills: [dashboard]\nprototype:\n  port: 9999\n")
        other = self.add_worktree("wt2")
        write(other / ".github" / ".skillsync.yaml", ": : not yaml [\n")
        third = self.add_worktree("wt3")      # no manifest at all
        self.assertTrue(third.is_dir())
        self.assertEqual(sorted(bases(self.listed())), ["main", "wt1", "wt2", "wt3"])
        registry = dw.discover(self.project(), now=NOW)
        self.assertIsNotNone(registry)

    def test_warn_is_optional(self) -> None:
        self.add_worktree("lonely", backlink=False)
        self.assertEqual(ids(dw.list_entries(self.project())), ["main"])


class HasTrace(Layout):
    def setUp(self) -> None:
        super().setUp()
        write(self.main / "docs" / "plans" / "tracked.md", "# tracked\n")
        write(self.main / "docs" / "specs" / "spec.md", "# spec\n")
        self.wt = self.add_worktree("wt1")

    def trace(self, wt=None, main=...) -> bool:
        return dw.has_trace(str(wt or self.wt), str(self.main) if main is ... else main)

    def test_fresh_worktree_with_tracked_plans_has_no_trace(self) -> None:
        write(self.wt / "docs" / "plans" / "tracked.md", "# tracked\n")
        write(self.wt / "docs" / "specs" / "spec.md", "# spec\n")
        self.assertFalse(self.trace())

    def test_empty_worktree_has_no_trace(self) -> None:
        self.assertFalse(self.trace())

    def test_new_plan_file_is_a_trace(self) -> None:
        write(self.wt / "docs" / "plans" / "new.md", "# new\n")
        self.assertTrue(self.trace())

    def test_new_spec_file_is_a_trace(self) -> None:
        write(self.wt / "docs" / "specs" / "new.md", "# new\n")
        self.assertTrue(self.trace())

    def test_changed_plan_file_is_a_trace(self) -> None:
        write(self.wt / "docs" / "plans" / "tracked.md", "# tracked\nchanged\n")
        self.assertTrue(self.trace())

    def test_same_content_under_another_name_is_a_trace(self) -> None:
        write(self.wt / "docs" / "plans" / "renamed.md", "# tracked\n")
        self.assertTrue(self.trace())

    def test_non_markdown_files_are_not_a_trace(self) -> None:
        write(self.wt / "docs" / "plans" / "notes.txt", "new\n")
        write(self.wt / "docs" / "specs" / "data.json", "{}\n")
        self.assertFalse(self.trace())

    def test_a_run_entry_is_a_trace(self) -> None:
        self.run_dir(self.wt)
        self.assertTrue(self.trace())

    def test_skillsync_directory_without_runs_is_not_a_trace(self) -> None:
        write(self.wt / ".skillsync" / "something.json", "{}\n")
        (self.wt / ".skillsync" / "runs").mkdir(parents=True)     # an empty runs directory is no entry either
        self.assertFalse(self.trace())

    def test_without_a_main_directory_only_runs_count(self) -> None:
        write(self.wt / "docs" / "plans" / "new.md", "# new\n")
        for main in (None, str(self.tmp / "gone")):
            with self.subTest(main=main):
                self.assertFalse(self.trace(main=main))
        self.run_dir(self.wt)
        for main in (None, str(self.tmp / "gone")):
            with self.subTest(main=main, runs=True):
                self.assertTrue(self.trace(main=main))

    def test_oversized_file_is_ignored(self) -> None:
        write(self.wt / "docs" / "plans" / "huge.md", b"x" * (1024 * 1024 + 1))
        self.assertFalse(self.trace())

    def test_file_at_the_size_limit_still_counts(self) -> None:
        write(self.wt / "docs" / "plans" / "edge.md", b"y" * (1024 * 1024))
        self.assertTrue(self.trace())

    def test_symlinked_plan_file_is_ignored(self) -> None:
        outside = write(self.tmp / "outside" / "evil.md", "# not in this tree\n")
        (self.wt / "docs" / "plans").mkdir(parents=True)
        self.symlink(outside, self.wt / "docs" / "plans" / "evil.md")
        self.assertFalse(self.trace())

    def test_symlinked_plans_directory_is_ignored(self) -> None:
        outside = write(self.tmp / "outside-plans" / "p.md", "# elsewhere\n")
        (self.wt / "docs").mkdir(parents=True)
        self.symlink(outside.parent, self.wt / "docs" / "plans")
        self.assertFalse(self.trace())

    def test_symlinked_docs_directory_is_ignored(self) -> None:
        outside = write(self.tmp / "outside-docs" / "plans" / "p.md", "# elsewhere\n")
        self.symlink(outside.parents[1], self.wt / "docs")
        self.assertFalse(self.trace())

    def test_mtime_is_not_the_criterion(self) -> None:
        identical = write(self.wt / "docs" / "plans" / "tracked.md", "# tracked\n", mtime=NOW)
        self.assertFalse(self.trace())
        for stamp in (1.0, NOW * 2):          # long before and far after the creation of the worktree
            os.utime(identical, (stamp, stamp))
            self.assertFalse(self.trace(), stamp)
        fresh = write(self.wt / "docs" / "plans" / "new.md", "# new\n", mtime=1.0)
        self.assertTrue(self.trace())
        os.utime(fresh, (NOW * 2, NOW * 2))
        self.assertTrue(self.trace())

    def test_mtime_of_the_gitdir_files_is_not_the_criterion(self) -> None:
        for name in ("gitdir", "HEAD", "commondir"):
            os.utime(self.entry_dir("wt1") / name, (NOW * 2, NOW * 2))
        self.assertFalse(self.trace())

    def test_switching_the_branch_does_not_change_the_result(self) -> None:
        write(self.wt / "docs" / "plans" / "new.md", "# new\n")
        before = self.trace()
        write(self.entry_dir("wt1") / "HEAD", "ref: refs/heads/other\n")
        self.assertEqual(self.trace(), before)
        clean = self.add_worktree("clean")
        quiet = self.trace(clean)
        write(self.entry_dir("clean") / "HEAD", SHA_B + "\n")
        self.assertEqual(self.trace(clean), quiet)
        self.assertFalse(quiet)

    def test_main_directory_content_wins_over_a_stale_cache(self) -> None:
        write(self.wt / "docs" / "plans" / "tracked.md", "# tracked\n")
        self.assertFalse(self.trace())
        write(self.main / "docs" / "plans" / "tracked.md", "# tracked, edited in the main directory\n")
        self.assertTrue(self.trace())           # now the worktree differs from the main directory

    def test_unreadable_or_missing_worktree_does_not_raise(self) -> None:
        self.assertFalse(self.trace(self.tmp / "no-such-dir"))


class LastActivity(Layout):
    def test_none_without_any_work_files(self) -> None:
        wt = self.add_worktree("wt1")
        self.assertIsNone(dw.last_activity(str(wt)))
        self.assertIsNone(dw.last_activity(str(self.tmp / "missing")))

    def test_newest_run_file_wins(self) -> None:
        wt = self.add_worktree("wt1")
        self.run_dir(wt, "old", mtime=NOW - 5 * DAY)
        self.run_dir(wt, "new", mtime=NOW - 1 * DAY)
        self.assertAlmostEqual(dw.last_activity(str(wt)), NOW - 1 * DAY, delta=2)

    def test_plan_file_counts(self) -> None:
        wt = self.add_worktree("wt1")
        write(wt / "docs" / "plans" / "p.md", "# p\n", mtime=NOW - 3 * DAY)
        self.assertAlmostEqual(dw.last_activity(str(wt)), NOW - 3 * DAY, delta=2)


class IsExcluded(Layout):
    def setUp(self) -> None:
        super().setUp()
        self.wt = self.add_worktree("wt1")

    def excluded(self, **kwargs) -> bool:
        kwargs.setdefault("main_path", str(self.main))
        return dw.is_excluded(str(self.wt), **kwargs)

    def test_not_excluded_by_default(self) -> None:
        self.assertFalse(self.excluded())

    def test_marker_file_excludes(self) -> None:
        write(self.wt / ".skillsync" / "no-dashboard", "")
        self.assertTrue(self.excluded())
        self.assertTrue(dw.is_excluded(str(self.wt)))           # works without a manifest and without a main directory

    def test_symlink_marker_does_not_count(self) -> None:
        target = write(self.tmp / "elsewhere" / "marker", "")
        (self.wt / ".skillsync").mkdir()
        self.symlink(target, self.wt / ".skillsync" / "no-dashboard")
        self.assertFalse(self.excluded())

    def test_marker_directory_does_not_count(self) -> None:
        (self.wt / ".skillsync" / "no-dashboard").mkdir(parents=True)
        self.assertFalse(self.excluded())

    def test_absolute_path_from_the_manifest_excludes(self) -> None:
        self.assertTrue(self.excluded(manifest_exclude=(str(self.wt),)))
        self.assertTrue(self.excluded(manifest_exclude=(str(self.wt) + "/",)))
        self.assertTrue(self.excluded(manifest_exclude=[str(self.tmp / "other"), str(self.wt)]))

    def test_relative_path_is_taken_against_the_main_directory(self) -> None:
        nested = self.add_worktree("nested", parent=self.main / "wts")
        self.assertTrue(dw.is_excluded(str(nested), manifest_exclude=("wts/nested",), main_path=str(self.main)))
        self.assertTrue(dw.is_excluded(str(nested), manifest_exclude=("./wts/nested",), main_path=str(self.main)))
        self.assertFalse(dw.is_excluded(str(nested), manifest_exclude=("wts/other",), main_path=str(self.main)))

    def test_relative_path_without_a_main_directory_is_ignored_not_fatal(self) -> None:
        self.assertFalse(dw.is_excluded(str(self.wt), manifest_exclude=("wt1",), main_path=None))

    def test_a_directory_name_alone_does_not_exclude(self) -> None:
        self.assertFalse(self.excluded(manifest_exclude=("wt1",)))

    def test_other_paths_do_not_exclude(self) -> None:
        self.assertFalse(self.excluded(manifest_exclude=(str(self.tmp / "other"), str(self.main))))


class Discover(Layout):
    def make(self, name, *, age=0.0, run=True, **kwargs) -> Path:
        wt = self.add_worktree(name, **kwargs)
        if run:
            self.run_dir(wt, mtime=NOW - age * DAY)
        return wt

    def discover(self, **kwargs):
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("warn", self.warnings.append)
        return dw.discover(self.project(), **kwargs)

    def test_plain_repository_has_just_the_main_tab(self) -> None:
        registry = self.discover()
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(list(registry.hidden), [])
        self.assertEqual(list(registry.excluded), [])

    def test_main_is_always_first_even_when_it_is_the_least_active(self) -> None:
        self.make("busy", age=0.1)
        self.run_dir(self.main, mtime=NOW - 6 * DAY)
        self.assertEqual(ids(self.discover().tabs)[0], "main")

    def test_main_is_a_tab_without_any_trace_or_activity(self) -> None:
        self.make("busy")
        self.assertEqual(ids(self.discover().tabs)[0], "main")

    def test_worktree_with_a_trace_is_a_tab(self) -> None:
        wt = self.make("wt1")
        registry = self.discover()
        self.assertEqual(len(registry.tabs), 2)
        self.assertTrue(same(registry.tabs[1].path, wt))

    def test_worktree_without_a_trace_is_hidden(self) -> None:
        self.make("quiet", run=False)
        registry = self.discover()
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(bases(registry.hidden), ["quiet"])

    def test_pinned_worktree_without_a_trace_is_a_tab(self) -> None:
        wt = self.make("quiet", run=False)
        registry = self.discover(pinned=(str(wt),))
        self.assertEqual(bases(registry.tabs), ["main", "quiet"])
        self.assertEqual(list(registry.hidden), [])

    def test_inactive_worktree_is_hidden_after_the_threshold(self) -> None:
        self.make("old", age=8)
        self.make("recent", age=1)
        registry = self.discover()
        self.assertEqual(bases(registry.tabs), ["main", "recent"])
        self.assertEqual(bases(registry.hidden), ["old"])

    def test_threshold_is_configurable_and_default_is_seven_days(self) -> None:
        self.make("mid", age=4)
        self.assertEqual(bases(self.discover().tabs), ["main", "mid"])
        self.assertEqual(bases(self.discover(inactive_days=3).tabs), ["main"])
        self.assertEqual(bases(self.discover(inactive_days=3).hidden), ["mid"])
        self.assertEqual(bases(self.discover(inactive_days=30).tabs), ["main", "mid"])

    def test_hidden_holds_everything_that_show_all_must_reveal(self) -> None:
        self.make("old", age=20)
        self.make("quiet", run=False)
        self.make("fresh", age=0.5)
        registry = self.discover()
        self.assertEqual(sorted(bases(registry.hidden)), ["old", "quiet"])
        self.assertEqual(sorted(bases(registry.tabs) + bases(registry.hidden)), ["fresh", "main", "old", "quiet"])

    def test_hidden_reasons_name_trace_inactive_and_limit(self) -> None:
        self.make("quiet", run=False)
        self.make("old", age=20)
        for index in range(dw.MAX_VISIBLE + 1):
            self.make(f"w{index:02d}", age=0.1 + index / 100)
        registry = self.discover()
        by_name = {Path(entry.path).name: registry.hidden_reasons[entry.id] for entry in registry.hidden}
        self.assertEqual(by_name["quiet"], "trace")
        self.assertEqual(by_name["old"], "inactive")
        self.assertEqual(sorted(set(by_name.values())), ["inactive", "limit", "trace"])
        self.assertEqual({name: reason for name, reason in by_name.items() if reason == "limit"}, {f"w{dw.MAX_VISIBLE:02d}": "limit", f"w{dw.MAX_VISIBLE - 1:02d}": "limit"})
        self.assertEqual(set(registry.hidden_reasons), {entry.id for entry in registry.hidden})

    def test_pinned_inactive_worktree_stays_visible(self) -> None:
        wt = self.make("old", age=30)
        registry = self.discover(pinned=(str(wt),))
        self.assertEqual(bases(registry.tabs), ["main", "old"])

    def test_order_is_most_recent_activity_first(self) -> None:
        self.make("aa", age=3)
        self.make("bb", age=1)
        self.make("cc", age=2)
        self.assertEqual(bases(self.discover().tabs), ["main", "bb", "cc", "aa"])

    def test_ties_are_broken_by_name(self) -> None:
        self.make("b-wt", age=2)
        self.make("a-wt", age=2)
        self.make("c-wt", age=2)
        self.assertEqual(bases(self.discover().tabs), ["main", "a-wt", "b-wt", "c-wt"])

    def test_excluded_by_marker_is_never_a_tab(self) -> None:
        wt = self.make("private-wt")
        write(wt / ".skillsync" / "no-dashboard", "")
        registry = self.discover()
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(excluded_bases(registry), ["private-wt"])
        self.assertNotIn("private-wt", bases(registry.hidden))

    def test_excluded_by_the_manifest_is_never_a_tab(self) -> None:
        wt = self.make("private-wt")
        self.make("open")
        registry = self.discover(manifest_exclude=(str(wt),))
        self.assertEqual(bases(registry.tabs), ["main", "open"])
        self.assertEqual(excluded_bases(registry), ["private-wt"])

    def test_manifest_exclusion_by_a_relative_path(self) -> None:
        self.make("private-wt", parent=self.main / "wts")
        registry = self.discover(manifest_exclude=("wts/private-wt",))
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(excluded_bases(registry), ["private-wt"])

    def test_excluded_and_pinned_is_excluded_with_a_warning(self) -> None:
        wt = self.make("private-wt")
        write(wt / ".skillsync" / "no-dashboard", "")
        registry = self.discover(pinned=(str(wt),))
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(excluded_bases(registry), ["private-wt"])
        self.assertTrue(self.warnings or registry.warnings)

    def test_pinned_path_that_fails_the_back_link_is_not_a_tab(self) -> None:
        stray = self.tmp / "stray"
        write(stray / ".git", f"gitdir: {self.common / 'worktrees' / 'ghost'}\n")
        self.run_dir(stray)
        registry = self.discover(pinned=(str(stray),))
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertNotIn("stray", bases(registry.hidden))

    def test_pinned_unregistered_directory_is_not_a_tab(self) -> None:
        folder = self.tmp / "plain-folder"
        folder.mkdir()
        registry = self.discover(pinned=(str(folder),))
        self.assertEqual(ids(registry.tabs), ["main"])

    def test_limit_is_sixteen_visible_tabs_and_the_rest_is_hidden(self) -> None:
        for number in range(20):
            self.make(f"w{number:02d}", age=number / 24.0)       # w00 is the most recent
        registry = self.discover()
        self.assertEqual(len(registry.tabs), dw.MAX_VISIBLE)
        self.assertEqual(ids(registry.tabs)[0], "main")
        self.assertEqual(bases(registry.tabs)[1:], [f"w{number:02d}" for number in range(15)])
        self.assertEqual(bases(registry.hidden), [f"w{number:02d}" for number in range(15, 20)])

    def test_exactly_sixteen_fit_without_hiding(self) -> None:
        for number in range(15):
            self.make(f"w{number:02d}", age=number / 24.0)
        registry = self.discover()
        self.assertEqual(len(registry.tabs), 16)
        self.assertEqual(list(registry.hidden), [])

    def test_pinned_wins_the_limit_over_more_active_tabs(self) -> None:
        for number in range(20):
            self.make(f"w{number:02d}", age=number / 24.0)
        pinned = self.tmp / "w19"
        registry = self.discover(pinned=(str(pinned),))
        shown = bases(registry.tabs)
        self.assertEqual(len(shown), dw.MAX_VISIBLE)
        self.assertIn("w19", shown)
        self.assertNotIn("w14", shown)                           # the least active unpinned tab gave way
        self.assertIn("w14", bases(registry.hidden))
        self.assertEqual(shown[0], "main")

    def test_pinned_beyond_the_limit_overflow_into_hidden_with_a_warning(self) -> None:
        pinned = []
        for number in range(18):
            pinned.append(str(self.make(f"p{number:02d}", age=number / 24.0)))
        registry = self.discover(pinned=tuple(pinned))
        self.assertEqual(ids(registry.tabs)[0], "main")
        self.assertEqual(len(registry.tabs), dw.MAX_VISIBLE)
        self.assertEqual(len(registry.tabs) + len(registry.hidden), 19)
        self.assertTrue(self.warnings or registry.warnings)

    def test_r2_and_r3_still_bind_pinned_directories(self) -> None:
        orphan = self.add_worktree("orphan", backlink=False)
        marked = self.make("marked")
        write(marked / ".skillsync" / "no-dashboard", "")
        registry = self.discover(pinned=(str(orphan), str(marked)))
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(excluded_bases(registry), ["marked"])

    def test_registry_collects_the_warnings_of_rejected_entries(self) -> None:
        self.add_worktree("lonely", backlink=False)
        registry = self.discover()
        self.assertTrue(registry.warnings or self.warnings)
        self.assertTrue(all(isinstance(message, str) for message in list(registry.warnings)))

    def test_discover_without_optional_arguments(self) -> None:
        self.make("wt1")
        registry = dw.discover(self.project())
        self.assertEqual(ids(registry.tabs)[0], "main")

    def test_main_directory_removed_leaves_only_worktree_tabs_and_runs_are_the_trace(self) -> None:
        with_run = self.make("with-run")
        only_plan = self.make("only-plan", run=False)
        write(only_plan / "docs" / "plans" / "new.md", "# new\n")
        project = dw.Project(root=str(with_run), common_dir=str(self.common), main_path=str(self.tmp / "gone"))
        registry = dw.discover(project, now=NOW, warn=self.warnings.append)
        self.assertNotIn("main", ids(registry.tabs))
        self.assertEqual(bases(registry.tabs), ["with-run"])
        self.assertEqual(bases(registry.hidden), ["only-plan"])

    def test_worktrees_with_other_manifests_and_ports_do_not_break_discovery(self) -> None:
        one = self.make("one")
        write(one / ".github" / ".skillsync.yaml", "prototype:\n  port: 4321\n")
        two = self.make("two")
        write(two / ".github" / ".skillsync.yaml", "\x00\x01 not yaml: [")
        self.make("three")
        registry = self.discover()
        self.assertEqual(sorted(bases(registry.tabs)), ["main", "one", "three", "two"])

    def test_fresh_worktree_in_a_repository_with_tracked_plans_stays_hidden(self) -> None:
        write(self.main / "docs" / "plans" / "p.md", "# p\n")
        wt = self.make("fresh", run=False)
        write(wt / "docs" / "plans" / "p.md", "# p\n")
        registry = self.discover()
        self.assertEqual(ids(registry.tabs), ["main"])
        self.assertEqual(bases(registry.hidden), ["fresh"])

    def test_worktree_that_changes_its_branch_keeps_its_tab_and_id(self) -> None:
        wt = self.make("wt1")
        before = self.discover().tabs[1]
        write(self.entry_dir("wt1") / "HEAD", "ref: refs/heads/another\n")
        after = self.discover().tabs[1]
        self.assertEqual(before.id, after.id)
        self.assertEqual(after.branch, "another")
        self.assertTrue(same(after.path, wt))

    def test_ids_of_tabs_are_unique_and_valid(self) -> None:
        for number in range(6):
            self.make(f"w{number}")
        found = ids(self.discover().tabs)
        self.assertEqual(len(found), len(set(found)))
        self.assertTrue(all(id_matches(value) for value in found))

    def test_registry_is_rebuilt_from_the_files_on_every_call(self) -> None:
        self.assertEqual(ids(self.discover().tabs), ["main"])
        self.make("late")
        self.assertEqual(bases(self.discover().tabs), ["main", "late"])
        shutil.rmtree(str(self.tmp / "late"))
        self.assertEqual(ids(self.discover().tabs), ["main"])

    def test_symlink_swap_after_registration_is_dropped_by_the_next_discover(self) -> None:
        wt = self.make("wt1")
        self.assertEqual(len(self.discover().tabs), 2)
        decoy = self.tmp / "decoy"
        wt.rename(decoy)
        self.symlink(decoy, wt)
        registry = self.discover(pinned=(str(wt),))
        self.assertEqual(ids(registry.tabs), ["main"])


class FeedbackStoreRoot(Layout):
    """`feedback_store_root` is the store of feedback (`<main>/.skillsync/feedback`), like `feedback.store_root`, without running git."""

    def expected(self) -> Path:
        return self.main / ".skillsync" / "feedback"

    def test_common_dir_of_a_plain_repository_gives_the_main_directory(self) -> None:
        self.assertTrue(same(dw.feedback_store_root(str(self.common)), self.expected()))

    def test_main_directory_gives_itself(self) -> None:
        self.assertTrue(same(dw.feedback_store_root(str(self.main)), self.expected()))

    def test_worktree_directory_gives_the_main_directory(self) -> None:
        wt = self.add_worktree("wt1")
        self.assertTrue(same(dw.feedback_store_root(str(wt)), self.expected()))

    def test_relative_worktree_gives_the_main_directory(self) -> None:
        wt = self.add_worktree("wt1", relative=True)
        self.assertTrue(same(dw.feedback_store_root(str(wt)), self.expected()))

    def test_submodule_has_no_store_root(self) -> None:
        write(self.common / "modules" / "sub" / "HEAD", "ref: refs/heads/main\n")
        write(self.main / "sub" / ".git", "gitdir: ../.git/modules/sub\n")
        self.assertIsNone(dw.feedback_store_root(str(self.main / "sub")))
        self.assertIsNone(dw.feedback_store_root(str(self.common / "modules" / "sub")))

    def test_bare_repository_has_no_store_root(self) -> None:
        bare = self.tmp / "bare.git"
        write(bare / "HEAD", "ref: refs/heads/main\n")
        write(bare / "config", "[core]\n\tbare = true\n")
        self.assertIsNone(dw.feedback_store_root(str(bare)))

    def test_not_a_repository_has_no_store_root(self) -> None:
        (self.tmp / "plain").mkdir()
        self.assertIsNone(dw.feedback_store_root(str(self.tmp / "plain")))


if __name__ == "__main__":
    unittest.main()
