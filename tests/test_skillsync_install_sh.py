"""Black-box tests of install.sh (INST-1) in a fully artificial environment.

PATH contains only a directory of fakes (git, curl, uv, skillsync, uname, sudo)
and symlinks to a few real coreutils, so a missing tool is really missing.
Every fake appends its argv to a log; nothing touches the real uv/skillsync.
"""
import sys
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.platform == 'win32':
    raise unittest.SkipTest('install.sh is a POSIX sh script; native Windows is out of scope (WSL2)')

INSTALL = ROOT / "install.sh"
REAL_TOOLS = ["sh", "env", "awk", "grep", "mktemp", "rm", "cat", "mkdir", "chmod", "cp", "printf", "dirname"]

LOGGER = '''
log() { n=$1; shift; line="$n"; for a in "$@"; do line="$line <$a>"; done; printf '%s\\n' "$line" >> "$FAKE_LOG"; }
'''

FAKE_GIT = "#!/bin/sh\n" + LOGGER + 'log git "$@"\nexit 0\n'
FAKE_SUDO = "#!/bin/sh\n" + LOGGER + 'log sudo "$@"\nexit 0\n'
FAKE_UNAME = '#!/bin/sh\nprintf "%s\\n" "${FAKE_UNAME:-Linux}"\n'
FAKE_CURL = "#!/bin/sh\n" + LOGGER + '''log curl "$@"
[ -n "$CURL_FAIL" ] && exit 22
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out=$2; shift; done
cat > "$out" <<'EOS'
[ -n "$UVINSTALL_FAIL" ] && exit 1
printf '%s\\n' "uv-installer-ran" >> "$FAKE_LOG"
[ -n "$UVINSTALL_NOPATH" ] && exit 0
mkdir -p "$HOME/.local/bin"
cp "$FAKE_DIR/uv.template" "$HOME/.local/bin/uv"
chmod +x "$HOME/.local/bin/uv"
EOS
exit 0
'''
FAKE_UV = "#!/bin/sh\n" + LOGGER + '''log uv "$@"
case "$1 $2" in
  "tool list") [ -f "$FAKE_STATE/uvtool-skillsync" ] && echo "skillsync v1.0.0"; exit 0;;
  "tool dir") echo "$HOME/.local/bin"; exit 0;;
  "tool install")
    [ -n "$UVTOOL_FAIL" ] && exit 1
    mkdir -p "$HOME/.local/bin"
    cp "$FAKE_DIR/skillsync.template" "$HOME/.local/bin/skillsync"
    chmod +x "$HOME/.local/bin/skillsync"
    : > "$FAKE_STATE/uvtool-skillsync"
    exit 0;;
esac
exit 0
'''
FAKE_SKILLSYNC = "#!/bin/sh\n" + LOGGER + '''if [ "$1" = "--version" ]; then echo "skillsync ${SS_VERSION:-1.0.0}"; exit 0; fi
log skillsync "$@"
exit "${SS_SETUP_RC:-0}"
'''


def write_exec(path, text):
    Path(path).write_text(text)
    os.chmod(path, 0o755)


class Env:
    """One artificial machine: fakes dir, HOME, TMPDIR, log."""

    def __init__(self, tools=("git", "curl", "uv")):
        self.root = Path(tempfile.mkdtemp(prefix="inst-test-"))
        self.fakes = self.root / "fakes"
        self.bin = self.root / "realbin"
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.tmp = self.root / "tmp"
        for d in (self.fakes, self.bin, self.home, self.state, self.tmp):
            d.mkdir()
        self.log = self.root / "log"
        self.log.write_text("")
        for t in REAL_TOOLS:
            real = shutil.which(t)
            if real:
                os.symlink(real, self.bin / t)
        write_exec(self.fakes / "uname", FAKE_UNAME)
        write_exec(self.fakes / "sudo", FAKE_SUDO)
        write_exec(self.fakes / "uv.template", FAKE_UV)
        write_exec(self.fakes / "skillsync.template", FAKE_SKILLSYNC)
        if "git" in tools:
            write_exec(self.fakes / "git", FAKE_GIT)
        if "curl" in tools:
            write_exec(self.fakes / "curl", FAKE_CURL)
        if "uv" in tools:
            write_exec(self.fakes / "uv", FAKE_UV)

    def add_foreign_skillsync(self):
        write_exec(self.fakes / "skillsync", FAKE_SKILLSYNC)

    def mark_uv_tool(self):
        (self.state / "uvtool-skillsync").write_text("")

    def run(self, args=(), script=INSTALL, extra=None, path_extra=""):
        env = {
            "PATH": f"{path_extra}{self.fakes}:{self.bin}" if not path_extra else f"{path_extra}:{self.fakes}:{self.bin}",
            "HOME": str(self.home),
            "TMPDIR": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "FAKE_DIR": str(self.fakes),
            "FAKE_STATE": str(self.state),
        }
        env.update(extra or {})
        return subprocess.run(
            [str(self.bin / "sh"), str(script), *args],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=60, start_new_session=True,  # no controlling tty: /dev/tty unavailable
        )

    def calls(self):
        return self.log.read_text().splitlines()

    def calls_starting(self, prefix):
        return [c for c in self.calls() if c.startswith(prefix)]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class InstallShTestBase(unittest.TestCase):
    def make_env(self, **kw):
        e = Env(**kw)
        self.addCleanup(e.cleanup)
        return e

    def run_ok(self, e, **kw):
        r = e.run(**kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def tearDown(self):
        # sudo must never be called in any scenario
        for e in getattr(self, "_envs", []):
            pass


def _no_sudo(test, e):
    test.assertEqual(e.calls_starting("sudo"), [], "install.sh must never call sudo")


class TestPrerequisites(InstallShTestBase):
    def test_missing_git(self):
        e = self.make_env(tools=("curl", "uv"))
        r = e.run()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("missing: git", r.stderr)
        self.assertNotIn("curl", r.stderr.split("missing:")[1].split(".")[0])
        self.assertIn("package manager", r.stderr)
        self.assertEqual(e.calls_starting("uv <"), [])
        self.assertEqual(e.calls_starting("curl"), [])
        _no_sudo(self, e)

    def test_missing_curl(self):
        e = self.make_env(tools=("git", "uv"))
        r = e.run()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("missing: curl", r.stderr)
        self.assertEqual(e.calls_starting("uv <"), [])

    def test_missing_both(self):
        e = self.make_env(tools=("uv",))
        r = e.run()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("missing: git curl", r.stderr)
        self.assertEqual(e.calls(), [])

    def test_hint_never_runs_sudo_with_apt(self):
        e = self.make_env(tools=("uv",))
        write_exec(e.fakes / "apt-get", "#!/bin/sh\nexit 0\n")
        r = e.run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("apt-get install", r.stderr)
        _no_sudo(self, e)

    def test_windows_like_systems(self):
        for name in ("MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"):
            with self.subTest(uname=name):
                e = self.make_env()
                r = e.run(extra={"FAKE_UNAME": name})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("WSL2", r.stderr)
                self.assertEqual(e.calls(), [])

    def test_unsupported_system(self):
        e = self.make_env()
        r = e.run(extra={"FAKE_UNAME": "FreeBSD"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unsupported", r.stderr)
        self.assertEqual(e.calls(), [])

    def test_macos_is_supported(self):
        e = self.make_env()
        self.run_ok(e, args=["--yes"], extra={"FAKE_UNAME": "Darwin", "SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"})


class TestUvBootstrap(InstallShTestBase):
    ENV = {"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"}

    def test_installs_uv_via_downloaded_file(self):
        e = self.make_env(tools=("git", "curl"))
        r = self.run_ok(e, extra=self.ENV)
        curls = e.calls_starting("curl")
        self.assertEqual(len(curls), 1, e.calls())
        m = re.fullmatch(r"curl <--proto> <=https> <--tlsv1\.2> <-fsSL> <-o> <(.+)> <https://astral\.sh/uv/install\.sh>", curls[0])
        self.assertTrue(m, curls[0])
        self.assertIn("uv-installer-ran", e.calls())
        self.assertTrue((e.home / ".local/bin/uv").exists())
        self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 1)
        # temp installer is cleaned up
        self.assertEqual(list(e.tmp.iterdir()), [])
        _no_sudo(self, e)

    def test_uv_not_installed_when_present(self):
        e = self.make_env()
        self.run_ok(e, extra=self.ENV)
        self.assertFalse([c for c in e.calls() if "astral.sh" in c])
        self.assertNotIn("uv-installer-ran", e.calls())

    def test_download_failure_exit_3(self):
        e = self.make_env(tools=("git", "curl"))
        r = e.run(extra={**self.ENV, "CURL_FAIL": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("could not download the uv installer", r.stderr)
        self.assertEqual(e.calls_starting("uv <"), [])

    def test_installer_failure_exit_3(self):
        e = self.make_env(tools=("git", "curl"))
        r = e.run(extra={**self.ENV, "UVINSTALL_FAIL": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("uv installer failed", r.stderr)
        self.assertEqual(e.calls_starting("uv <"), [])

    def test_uv_still_missing_after_install_exit_3(self):
        e = self.make_env(tools=("git", "curl"))
        r = e.run(extra={**self.ENV, "UVINSTALL_NOPATH": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("not on PATH", r.stderr)
        self.assertEqual(e.calls_starting("uv <"), [])


class TestToolInstall(InstallShTestBase):
    def test_default_spec_uses_tag(self):
        e = self.make_env()
        self.run_ok(e, extra={"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"})
        self.assertEqual(e.calls_starting("uv <tool> <install>"),
                         ["uv <tool> <install> <--force> <git+https://github.com/BATUSystems/skillsync@v9.9.9>"])

    def test_custom_spec(self):
        e = self.make_env()
        self.run_ok(e, extra={"SKILLSYNC_INSTALL_SPEC": "/some/path/skillsync.whl", "SS_VERSION": "0.0.1"})
        self.assertEqual(e.calls_starting("uv <tool> <install>"), ["uv <tool> <install> <--force> </some/path/skillsync.whl>"])

    def test_install_failure_exit_3_mentions_tag(self):
        e = self.make_env()
        r = e.run(extra={"SKILLSYNC_TAG": "v9.9.9", "UVTOOL_FAIL": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("uv tool install", r.stderr)
        self.assertIn("tag", r.stderr)
        self.assertEqual(e.calls_starting("skillsync"), [])

    def test_version_mismatch(self):
        e = self.make_env()
        r = e.run(extra={"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "1.2.3"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("does not match", r.stderr)
        self.assertEqual(e.calls_starting("skillsync"), [])

    def test_version_match_passes(self):
        e = self.make_env()
        self.run_ok(e, extra={"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"})

    def test_version_check_skipped_with_spec(self):
        e = self.make_env()
        self.run_ok(e, extra={"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "1.2.3", "SKILLSYNC_INSTALL_SPEC": "/x.whl"})


class TestForeignSkillsync(InstallShTestBase):
    ENV = {"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"}

    def test_foreign_blocks(self):
        e = self.make_env()
        e.add_foreign_skillsync()
        r = e.run(extra=self.ENV)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("was not installed by uv", r.stderr)
        self.assertIn("SKILLSYNC_INSTALL_FORCE=1", r.stderr)
        self.assertEqual(e.calls_starting("uv <tool> <install>"), [])

    def test_foreign_with_force_continues(self):
        e = self.make_env()
        e.add_foreign_skillsync()
        self.run_ok(e, extra={**self.ENV, "SKILLSYNC_INSTALL_FORCE": "1"})
        self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 1)

    def test_force_other_value_does_not_force(self):
        e = self.make_env()
        e.add_foreign_skillsync()
        r = e.run(extra={**self.ENV, "SKILLSYNC_INSTALL_FORCE": "yes"})
        self.assertEqual(r.returncode, 2)

    def test_uv_tool_is_updated_without_warning(self):
        e = self.make_env()
        e.add_foreign_skillsync()
        e.mark_uv_tool()
        r = self.run_ok(e, extra=self.ENV)
        self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 1)
        self.assertNotIn("not a uv tool", r.stdout + r.stderr)
        self.assertNotIn("not installed by uv", r.stdout + r.stderr)


class TestSetupArguments(InstallShTestBase):
    ENV = {"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"}

    def test_arguments_forwarded(self):
        e = self.make_env()
        self.run_ok(e, args=["--yes", "--org", "acme", "--org-source", "https://example.com/org.git"], extra=self.ENV)
        self.assertEqual(e.calls_starting("skillsync"),
                         ["skillsync <setup> <--yes> <--org> <acme> <--org-source> <https://example.com/org.git>"])

    def test_no_arguments(self):
        e = self.make_env()
        self.run_ok(e, extra=self.ENV)
        self.assertEqual(e.calls_starting("skillsync"), ["skillsync <setup>"])

    def test_argument_with_spaces_kept_intact(self):
        e = self.make_env()
        self.run_ok(e, args=["--match", "a b/**"], extra=self.ENV)
        self.assertEqual(e.calls_starting("skillsync"), ["skillsync <setup> <--match> <a b/**>"])

    def test_setup_exit_code_propagated(self):
        for rc, done in ((2, False), (1, False), (0, True)):
            with self.subTest(rc=rc):
                e = self.make_env()
                r = e.run(extra={**self.ENV, "SS_SETUP_RC": str(rc)})
                self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
                self.assertEqual(e.calls_starting("skillsync <setup>"), ["skillsync <setup>"])
                self.assertEqual("install.sh: done." in r.stdout, done, r.stdout)

    def test_no_setup(self):
        e = self.make_env()
        self.run_ok(e, args=["--yes"], extra={**self.ENV, "SKILLSYNC_NO_SETUP": "1"})
        self.assertEqual(e.calls_starting("skillsync"), [])
        self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 1)


class TestIdempotence(InstallShTestBase):
    ENV = {"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"}

    def test_rerun_with_uv_in_path(self):
        e = self.make_env()
        self.run_ok(e, extra=self.ENV)
        self.run_ok(e, extra=self.ENV)
        self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 2)
        self.assertFalse([c for c in e.calls() if "astral.sh" in c])
        _no_sudo(self, e)

    def test_uv_in_home_dirs_outside_initial_path(self):
        for sub in (".local/bin", ".cargo/bin"):
            with self.subTest(dir=sub):
                e = self.make_env(tools=("git", "curl"))
                d = e.home / sub
                d.mkdir(parents=True)
                shutil.copy(e.fakes / "uv.template", d / "uv")
                os.chmod(d / "uv", 0o755)
                self.run_ok(e, extra=self.ENV)
                self.assertEqual(e.calls_starting("curl"), [])
                self.assertNotIn("uv-installer-ran", e.calls())
                self.assertEqual(len(e.calls_starting("uv <tool> <install>")), 1)

    def test_rerun_after_uv_bootstrap(self):
        e = self.make_env(tools=("git", "curl"))
        self.run_ok(e, extra=self.ENV)
        self.assertEqual(len(e.calls_starting("curl")), 1)
        # new shell: uv now on PATH via ~/.local/bin
        self.run_ok(e, extra=self.ENV, path_extra=str(e.home / ".local/bin"))
        self.assertEqual(len(e.calls_starting("curl")), 1, "uv must not be installed again")
        self.assertEqual(e.calls().count("uv-installer-ran"), 1)


class TestTruncation(InstallShTestBase):
    def _run_truncated(self, e, data):
        p = e.root / "truncated.sh"
        p.write_bytes(data)
        return e.run(script=p, extra={"SKILLSYNC_TAG": "v9.9.9"})

    def test_truncated_scripts_do_nothing(self):
        raw = INSTALL.read_bytes()
        last = raw.rstrip(b"\n").rfind(b"\n") + 1
        self.assertTrue(raw[last:].startswith(b"main "), "last line of install.sh must call main")
        cuts = {"half": len(raw) // 2, "without_last_line": last, "inside_main": raw.find(b"main() {") + 40}
        for name, n in cuts.items():
            with self.subTest(cut=name):
                e = self.make_env()
                r = self._run_truncated(e, raw[:n])
                self.assertEqual(e.calls(), [], r.stdout + r.stderr)
                self.assertEqual(list(e.tmp.iterdir()), [])
                self.assertFalse((e.home / ".local").exists())
                self.assertNotIn("installing", r.stdout)

    def test_full_script_does_act(self):
        # proves the truncation test can fail: the untruncated script produces calls
        e = self.make_env()
        e.run(extra={"SKILLSYNC_TAG": "v9.9.9", "SS_VERSION": "9.9.9"})
        self.assertTrue(e.calls())

    def test_all_code_inside_functions(self):
        # only `set -eu`, assignments, function definitions and the final main call are top-level
        top = []
        for line in INSTALL.read_text().splitlines():
            if not line.strip() or line.startswith("#") or line.startswith((" ", "\t", "}")):
                continue
            top.append(line)
        for line in top:
            ok = (re.match(r"^[A-Z_]+=", line) or re.match(r"^\w+\(\) \{", line) or line in ('main "$@"', 'set -eu'))
            self.assertTrue(ok, f"top-level statement outside main(): {line}")


class TestStatic(unittest.TestCase):
    def test_set_eu_is_first_statement(self):
        code = [l for l in INSTALL.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(code[0].strip(), "set -eu")

    def test_default_tag_matches_version(self):
        sh = INSTALL.read_text()
        tag = re.search(r'^SKILLSYNC_DEFAULT_TAG="([^"]+)"', sh, re.M)
        ver = re.search(r'^__version__\s*=\s*"([^"]+)"', (ROOT / "skillsync_workflow/__init__.py").read_text(), re.M)
        self.assertTrue(tag and ver)
        self.assertEqual(tag.group(1), "v" + ver.group(1))

    def test_shellcheck(self):
        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck is not installed (CI has it)")
        r = subprocess.run([sc, "--shell=sh", str(INSTALL)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_dash_syntax(self):
        dash = shutil.which("dash")
        if not dash:
            self.skipTest("dash is not installed")
        r = subprocess.run([dash, "-n", str(INSTALL)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sh_syntax(self):
        r = subprocess.run(["sh", "-n", str(INSTALL)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sudo_only_in_text(self):
        # sudo may appear only inside quoted hint text, never as a command word
        for n, line in enumerate(INSTALL.read_text().splitlines(), 1):
            if line.lstrip().startswith("#") or "sudo" not in line:
                continue
            before = line.split("sudo")[0]
            self.assertEqual(before.count('"') % 2, 1, f"line {n} may call sudo: {line}")


if __name__ == "__main__":
    unittest.main()
