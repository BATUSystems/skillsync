"""Zgodność workflow CI (ci.yml, release-smoke.yml) z kontraktami K9-K13 i skryptami w scripts/.

Bez sieci i bez GitHuba: tylko parsowanie YAML, `--help` skryptów i `bash -n`.
Funkcje `problems_*` zwracają listę błędów; testy mutacyjne dowodzą, że kontrola potrafi paść.
"""
import copy
import itertools
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child  # noqa: E402
WORKFLOWS = ROOT / ".github" / "workflows"
HELP_SCRIPTS = ("check_skips.py", "check_wheel.py", "check_release.py")
SCRIPT_RE = re.compile(r"((?:scripts|tests)/[\w.-]+\.(?:py|sh|ps1))\b(.*)")
GLOB_RE = re.compile(r"[\w./$-]*\*[\w.*-]*")
EXPR_RE = re.compile(r"\$\{\{.*?\}\}", re.S)


def load(name):
    with open(WORKFLOWS / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def triggers(wf):
    # PyYAML (YAML 1.1) parsuje klucz `on` jako True.
    return wf.get("on", wf.get(True))


def expand_matrix(matrix):
    """Rozwija strategy.matrix: iloczyn osi, exclude (podzbiór kluczy), include (dodanie kombinacji)."""
    axes = {k: v for k, v in matrix.items() if k not in ("include", "exclude")}
    combos = [dict(zip(axes, vals)) for vals in itertools.product(*axes.values())]
    for ex in matrix.get("exclude", []):
        combos = [c for c in combos if not all(c.get(k) == v for k, v in ex.items())]
    for inc in matrix.get("include", []):
        combos.append(dict(inc))
    return combos


def steps_of(job):
    return job.get("steps", [])


def effective_shell(job, step):
    if "shell" in step:
        return step["shell"]
    return (job.get("defaults", {}).get("run", {}) or {}).get("shell")


def is_fixed_ubuntu(job):
    ro = str(job.get("runs-on", ""))
    return ro.startswith("ubuntu-")


def bash_steps(job):
    """Kroki `run` wykonywane w bashu (jawnie albo domyślnie na stałym ubuntu)."""
    for step in steps_of(job):
        if "run" not in step:
            continue
        shell = effective_shell(job, step)
        if shell == "bash" or (shell is None and is_fixed_ubuntu(job)):
            yield step


def script_calls(wf):
    """(job, krok, ścieżka skryptu, reszta linii z argumentami) dla każdego wywołania."""
    calls = []
    for jname, job in wf["jobs"].items():
        for step in steps_of(job):
            run = step.get("run")
            if not run:
                continue
            # sklej kontynuacje linii
            text = re.sub(r"\\\n\s*", " ", run)
            for line in text.splitlines():
                for m in SCRIPT_RE.finditer(line):
                    calls.append((jname, step.get("name", "?"), m.group(1), m.group(2)))
    return calls


_help_cache = {}


def help_text(script):
    if script not in _help_cache:
        res = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--help"],
                             capture_output=True, text=True, cwd=ROOT, timeout=60)
        _help_cache[script] = res.stdout + res.stderr
    return _help_cache[script]


# ---- kontrole (listy błędów) -------------------------------------------------

def problems_job_names(ci, smoke):
    errs = []
    for name in ("test", "install-smoke", "e2e", "release-check", "leakcheck"):
        if name not in ci["jobs"]:
            errs.append(f"ci.yml: brak zadania {name}")
    tags = (triggers(smoke) or {}).get("push", {}).get("tags", [])
    if "v*" not in tags:
        errs.append(f"release-smoke.yml: wyzwalacz tagiem v* nieobecny ({tags})")
    return errs


# Time limits of the `test` jobs: Windows 75, macOS 60, Ubuntu 45 minutes (about 1.4-1.6 times the longest job of five runs); the job names stay.
TEST_TIMEOUT = "${{ matrix.os == 'windows-latest' && 75 || matrix.os == 'macos-latest' && 60 || 60 }}"


def problems_matrix(ci):
    errs = []
    job = ci["jobs"].get("test")
    if job is None:
        return ["brak zadania test"]
    matrix = job["strategy"]["matrix"]
    for key in ("os", "python", "mode"):
        if key not in matrix:
            errs.append(f"macierz bez klucza {key}")
    if errs:
        return errs
    if sorted(matrix["os"]) != ["macos-latest", "ubuntu-latest", "windows-latest"]:
        errs.append(f"os: {matrix['os']}")
    if sorted(map(str, matrix["python"])) != ["3.11", "3.12", "3.13", "3.14"]:
        errs.append(f"python: {matrix['python']}")
    if sorted(matrix["mode"]) != ["copy", "symlink"]:
        errs.append(f"mode: {matrix['mode']}")
    combos = expand_matrix(matrix)
    if len(combos) != 17:
        errs.append(f"liczba kombinacji {len(combos)} != 17")
    if any(c["os"] == "windows-latest" and c["mode"] == "symlink" for c in combos):
        errs.append("kombinacja windows x symlink nie jest wykluczona")
    keys = {(c["os"], str(c["python"]), c["mode"]) for c in combos}
    # 3.13 blocks like 3.11 and 3.12 on every system; 3.14 is informational and runs on Ubuntu only (both modes).
    want = ({(o, p, m) for o in ("ubuntu-latest", "macos-latest") for p in ("3.11", "3.12", "3.13") for m in ("symlink", "copy")}
            | {("windows-latest", p, "copy") for p in ("3.11", "3.12", "3.13")}
            | {("ubuntu-latest", "3.14", m) for m in ("symlink", "copy")})
    if keys != want:
        errs.append(f"zbiór kombinacji różni się od oczekiwanego: brak {sorted(want - keys)}, nadmiar {sorted(keys - want)}")
    if job.get("timeout-minutes") != TEST_TIMEOUT:
        errs.append(f"timeout test: {job.get('timeout-minutes')}")
    if ci["jobs"].get("e2e", {}).get("timeout-minutes") != 45:
        errs.append("timeout e2e != 45")
    conc = ci.get("concurrency") or {}
    if conc.get("cancel-in-progress") is not True:
        errs.append("concurrency bez cancel-in-progress: true")
    return errs


def problems_script_calls(workflows):
    """workflows: dict nazwa -> sparsowany workflow."""
    errs = []
    for wname, wf in workflows.items():
        for jname, step, script, rest in script_calls(wf):
            path = ROOT / script
            if not path.is_file():
                errs.append(f"{wname}/{jname}/{step}: {script} nie istnieje")
                continue
            base = script.split("/")[-1]
            if script.startswith("scripts/") and base in HELP_SCRIPTS:
                helptxt = help_text(base)
                for flag in re.findall(r"(?<![\w-])(--[a-z][\w-]*)", rest):
                    if flag not in helptxt:
                        errs.append(f"{wname}/{jname}/{step}: flaga {flag} nieznana w --help {base}")
    return errs


def problems_shell(wf, wname):
    errs = []
    for jname, job in wf["jobs"].items():
        if is_fixed_ubuntu(job):
            continue
        for step in steps_of(job):
            run = step.get("run")
            if run and GLOB_RE.search(run) and effective_shell(job, step) not in ("bash", "pwsh"):
                errs.append(f"{wname}/{jname}/{step.get('name')}: wzorzec ścieżki bez shell: bash")
            if run and GLOB_RE.search(run) and effective_shell(job, step) == "pwsh":
                errs.append(f"{wname}/{jname}/{step.get('name')}: wzorzec ścieżki w pwsh")
    return errs


def dump(obj):
    return yaml.safe_dump(obj)


def problems_contracts(ci, smoke):
    errs = []
    test = ci["jobs"]["test"]
    text = dump(test)
    env = test.get("env", {})
    if "SKILLSYNC_NO_SYMLINK" not in env or "".join(str(env["SKILLSYNC_NO_SYMLINK"]).split()) \
            != "${{matrix.mode=='copy'&&'1'||''}}":
        errs.append("test: SKILLSYNC_NO_SYMLINK nie zależy od matrix.mode")
    lib = str(env.get("SKILLSYNC_TEST_LIBRARY", ""))
    if not lib.endswith("/library") or "github.workspace" not in lib:
        errs.append(f"test: SKILLSYNC_TEST_LIBRARY={lib}")
    if "SKILLSYNC_TEST_NODE" not in env:
        errs.append("test: brak SKILLSYNC_TEST_NODE")
    if "SKILLSYNC_E2E_CHROME" in text:
        errs.append("test: macierz nie może ustawiać SKILLSYNC_E2E_CHROME (K11)")
    for need in ("py_compile", "unittest discover", "check_skips.py", "uv pip install --system -e .",
                 "tests/k1_grep.py", "--durations 30", "sys.version_info >= (3, 12)"):   # durations: Python 3.12+ only (3.11 rejects the option)
        if need not in text:
            errs.append(f"test: brak {need}")
    unit = next((str(s.get("run", "")) for s in steps_of(test) if s.get("name") == "Unit tests"), "")
    if not any("unittest discover" in line and "$DURATIONS" in line for line in unit.splitlines()):
        errs.append("test: the `unittest discover` line must carry $DURATIONS (the slowest tests in every log)")
    uses = [s.get("uses", "") for s in steps_of(test)]
    for a in ("actions/setup-python", "astral-sh/setup-uv", "actions/setup-node"):
        if not any(u.startswith(a) for u in uses):
            errs.append(f"test: brak {a}")
    inst = dump(ci["jobs"]["install-smoke"])
    for need in ("check_wheel.py", "uv tool install", "uv tool uninstall"):
        if need not in inst:
            errs.append(f"install-smoke: brak {need}")
    rel = dump(smoke["jobs"])
    if not re.search(r"check_release\.py\s+--tag", rel):
        errs.append("release-smoke: brak check_release.py --tag")
    if not re.search(r"uv tool install \"?git\+https://github\.com/", rel):
        errs.append("release-smoke: brak uv tool install git+https://github.com/")
    e2e = ci["jobs"]["e2e"]
    if e2e.get("runs-on") != "ubuntu-latest":
        errs.append(f"e2e: runs-on {e2e.get('runs-on')}")
    if "SKILLSYNC_E2E_CHROME" not in dump(e2e):
        errs.append("e2e: brak SKILLSYNC_E2E_CHROME")
    return errs


def problems_uses(workflows):
    errs = []
    for wname, wf in workflows.items():
        for jname, job in wf["jobs"].items():
            for step in steps_of(job):
                u = step.get("uses")
                if not u:
                    continue
                if "@" not in u:
                    errs.append(f"{wname}/{jname}: {u} bez wersji")
                    continue
                ref = u.split("@", 1)[1]
                if not re.fullmatch(r"v\d+", ref) and not re.fullmatch(r"[0-9a-f]{40}", ref):
                    errs.append(f"{wname}/{jname}: {u} nie ma przypiętej wersji głównej")
    return errs


def problems_bash_syntax(workflows, bash):
    errs = []
    with tempfile.TemporaryDirectory() as tmp:
        for wname, wf in workflows.items():
            for jname, job in wf["jobs"].items():
                for i, step in enumerate(bash_steps(job)):
                    body = EXPR_RE.sub("X", step["run"])
                    f = child(Path(tmp), f"{wname}-{jname}-{i}.sh")
                    f.write_text(body, encoding="utf-8")
                    res = subprocess.run([bash, "-n", str(f)], capture_output=True, text=True)
                    if res.returncode != 0:
                        errs.append(f"{wname}/{jname}/{step.get('name')}: {res.stderr.strip()}")
    return errs


# ---- testy -------------------------------------------------------------------

class CiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ci = load("ci.yml")
        cls.smoke = load("release-smoke.yml")
        cls.wfs = {"ci.yml": cls.ci, "release-smoke.yml": cls.smoke}

    def test_parse_and_on_key(self):
        self.assertIsInstance(self.ci, dict)
        self.assertIsInstance(self.smoke, dict)
        self.assertIn(True, self.ci, "klucz `on` ma się parsować jako True (YAML 1.1)")
        self.assertNotIn("on", self.ci)

    def test_job_names_and_tag_trigger(self):
        self.assertEqual(problems_job_names(self.ci, self.smoke), [])

    def test_matrix_and_limits(self):
        self.assertEqual(problems_matrix(self.ci), [])

    def test_script_calls_match_argparse(self):
        calls = script_calls(self.ci) + script_calls(self.smoke)
        found = {c[2].split("/")[-1] for c in calls}
        self.assertTrue({"check_skips.py", "check_wheel.py", "check_release.py"} <= found, found)
        self.assertEqual(problems_script_calls(self.wfs), [])

    def test_emitted_and_used_flags_exist(self):
        # twarde dowody na pomocy rzeczywistych skryptów
        for flag in ("--system", "--mode", "--emit"):
            self.assertIn(flag, help_text("check_skips.py"))
        for flag in ("--tag", "--version"):
            self.assertIn(flag, help_text("check_release.py"))

    def test_path_patterns_have_bash(self):
        for name, wf in self.wfs.items():
            self.assertEqual(problems_shell(wf, name), [])

    def test_contracts_k9_k13(self):
        self.assertEqual(problems_contracts(self.ci, self.smoke), [])

    def test_uses_pinned(self):
        self.assertEqual(problems_uses(self.wfs), [])

    @unittest.skipUnless(shutil.which("bash"), "brak bash w PATH: nie można uruchomić bash -n")
    def test_embedded_bash_syntax(self):
        self.assertEqual(problems_bash_syntax(self.wfs, shutil.which("bash")), [])


class MutationTests(unittest.TestCase):
    """Dowód, że kontrole potrafią paść: mutacje kopii w pamięci."""

    @classmethod
    def setUpClass(cls):
        cls.ci = load("ci.yml")
        cls.smoke = load("release-smoke.yml")

    def mut(self):
        return copy.deepcopy(self.ci), copy.deepcopy(self.smoke)

    def test_expand_matrix_has_seventeen(self):
        self.assertEqual(len(expand_matrix(self.ci["jobs"]["test"]["strategy"]["matrix"])), 17)

    def test_python_314_is_informational_and_the_other_pythons_are_not(self):
        self.assertIn("matrix.python == '3.14'", self.ci["jobs"]["test"]["continue-on-error"])

    def test_drop_matrix_combination(self):
        ci, _ = self.mut()
        ci["jobs"]["test"]["strategy"]["matrix"]["exclude"].append({"os": "macos-latest", "mode": "copy", "python": "3.12"})
        self.assertTrue(problems_matrix(ci))

    def test_remove_windows_exclusion(self):
        ci, _ = self.mut()
        ci["jobs"]["test"]["strategy"]["matrix"]["exclude"] = []
        self.assertTrue(problems_matrix(ci))

    def test_rename_job(self):
        ci, smoke = self.mut()
        ci["jobs"]["install-smoke-x"] = ci["jobs"].pop("install-smoke")
        self.assertTrue(problems_job_names(ci, smoke))

    def test_tag_trigger_removed(self):
        ci, smoke = self.mut()
        key = True if True in smoke else "on"
        smoke[key]["push"]["tags"] = ["release-*"]
        self.assertTrue(problems_job_names(ci, smoke))

    def test_timeout_and_concurrency(self):
        ci, _ = self.mut()
        ci["jobs"]["test"]["timeout-minutes"] = 75
        self.assertTrue(problems_matrix(ci))
        ci, _ = self.mut()
        ci["jobs"]["test"]["timeout-minutes"] = 45
        self.assertTrue(problems_matrix(ci))
        ci, _ = self.mut()
        ci["jobs"]["test"]["timeout-minutes"] = "${{ matrix.os == 'windows-latest' && 75 || 45 }}"  # macOS back at 45
        self.assertTrue(problems_matrix(ci))
        ci, _ = self.mut()
        ci["jobs"]["test"]["timeout-minutes"] = "${{ matrix.os == 'windows-latest' && 60 || matrix.os == 'macos-latest' && 45 || 30 }}"  # the limits of 1.7.0
        self.assertTrue(problems_matrix(ci))
        ci, _ = self.mut()
        ci["concurrency"]["cancel-in-progress"] = False
        self.assertTrue(problems_matrix(ci))

    def test_nonexistent_flag(self):
        ci, smoke = self.mut()
        for step in ci["jobs"]["test"]["steps"]:
            if "check_skips.py" in step.get("run", "") and "--emit" not in step["run"]:
                step["run"] += " --nieistniejaca-flaga"
        self.assertTrue(problems_script_calls({"ci.yml": ci, "release-smoke.yml": smoke}))

    def test_nonexistent_script(self):
        ci, smoke = self.mut()
        ci["jobs"]["release-check"]["steps"][-1]["run"] += "\npython scripts/brak_takiego.py"
        self.assertTrue(problems_script_calls({"ci.yml": ci}))

    def test_missing_bash_shell(self):
        ci, _ = self.mut()
        del ci["jobs"]["install-smoke"]["defaults"]
        self.assertTrue(problems_shell(ci, "ci.yml"))

    def test_contract_mutations(self):
        ci, smoke = self.mut()
        del ci["jobs"]["test"]["env"]["SKILLSYNC_NO_SYMLINK"]
        self.assertTrue(problems_contracts(ci, smoke))
        ci, smoke = self.mut()
        ci["jobs"]["e2e"]["runs-on"] = "windows-latest"
        self.assertTrue(problems_contracts(ci, smoke))
        ci, smoke = self.mut()
        smoke["jobs"]["release-smoke"]["steps"][3]["run"] = "python scripts/check_release.py"
        self.assertTrue(problems_contracts(ci, smoke))

    def test_unpinned_uses(self):
        ci, smoke = self.mut()
        ci["jobs"]["test"]["steps"][0]["uses"] = "actions/checkout@main"
        self.assertTrue(problems_uses({"ci.yml": ci}))

    @unittest.skipUnless(shutil.which("bash"), "brak bash w PATH")
    def test_bash_syntax_error_detected(self):
        ci, _ = self.mut()
        ci["jobs"]["leakcheck"]["steps"][-1]["run"] = "if [ -f x ]; then echo"
        self.assertTrue(problems_bash_syntax({"ci.yml": ci}, shutil.which("bash")))


class PrTextWorkflowTests(unittest.TestCase):
    """DEBT-142 A4 (D49): `pr-text.yml` scans the title and the description of a pull request with `leakcheck --text`."""

    @classmethod
    def setUpClass(cls):
        cls.wf = load("pr-text.yml")
        cls.job = cls.wf["jobs"]["pr-text"]

    def test_it_runs_on_edits_of_the_description_and_has_no_write_permissions(self):
        pull = triggers(self.wf)["pull_request"]
        self.assertTrue({"opened", "edited", "reopened", "synchronize"} <= set(pull["types"]), pull)
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        # `edited` also fires when the base branch changes; whatever starts it, the job has no secrets and no write token
        dump = yaml.safe_dump(self.wf)
        self.assertNotIn("secrets.", dump)
        self.assertNotIn("GITHUB_TOKEN", dump)
        self.assertNotIn("pull_request_target", dump)
        self.assertNotIn("permissions", self.job, "the job does not widen the workflow permissions")

    def test_the_text_reaches_the_script_only_through_environment_variables(self):
        env = self.job["env"]
        self.assertIn("github.event.pull_request.title", env["PR_TITLE"])
        self.assertIn("github.event.pull_request.body", env["PR_BODY"])
        for step in steps_of(self.job):
            run = step.get("run", "")
            self.assertNotIn("github.event", run, step)      # no interpolation of attacker-controlled text in a script
            self.assertNotIn("${{", run, step)

    def test_the_tool_and_the_additions_come_from_the_base_branch(self):
        checkout = next(step for step in steps_of(self.job) if str(step.get("uses", "")).startswith("actions/checkout"))
        self.assertIn("pull_request.base.sha", checkout["with"]["ref"])
        self.assertIs(checkout["with"]["persist-credentials"], False)
        script = " ".join(step.get("run", "") for step in steps_of(self.job))
        self.assertIn("leakcheck", script)
        self.assertIn("--text", script)
        self.assertIn("--no-config", script)
        self.assertIn(".leakguard/patterns.yaml", script)

    def test_it_runs_only_in_the_public_repository_and_does_not_start_the_test_matrix(self):
        self.assertIn("github.repository == 'BATUSystems/skillsync'", self.job["if"])
        self.assertNotIn("matrix", self.job.get("strategy", {}))
        self.assertLessEqual(self.job["timeout-minutes"], 15)

    def test_actions_are_pinned_like_in_the_other_workflows(self):
        self.assertEqual(problems_uses({"pr-text.yml": self.wf}), [])

    def test_the_script_is_valid_bash_and_the_command_flags_exist(self):
        script = "\n".join(step.get("run", "") for step in steps_of(self.job))
        bash = shutil.which("bash")
        if bash:
            self.assertEqual(subprocess.run([bash, "-n"], input=script, text=True, capture_output=True).returncode, 0)
        help_text = subprocess.run([sys.executable, str(ROOT / "skillsync"), "leakcheck", "--help"], capture_output=True,
                                   text=True).stdout
        for flag in ("--text", "--no-config", "--json"):                 # --target-patterns is a hidden flag (ci.yml uses it too)
            self.assertIn(flag, help_text)


if __name__ == "__main__":
    unittest.main()
