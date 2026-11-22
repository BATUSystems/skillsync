# Pre-push matrix

Applies to Python projects with a skip baseline and a CI matrix (the skillsync core); in other projects apply the equivalent: the oldest supported
runtime version, every system with CI, a baseline check on the logs of all systems. `PUSH SLOT` occurs in `direct` mode with a dispatcher;
in `pr` mode the same evidence is a condition for pushing the PR branch.

Lesson from release 1.4.1: three red CI rounds detected what the station did not see (the newest interpreter, one system, no
skip baseline check). A package does not go to `PUSH SLOT` until there is evidence from the matrix below; a missing combination = `NOT_RUN`
with a reason in the report.

| The change concerns | Required before the push |
|---|---|
| a product function change | after EVERY function change re-run all modules that touch it (`grep` by name in `tests/`), also the guards (e.g. an AST test), not only the module written for this change; a change to a test file (helper, fixture, data table) also runs the guards that scan `tests/` (e.g. the temporary-paths guard), which a `grep` by function name will not find: include them in the phase's targeted rung |
| any code | Linux on the **oldest supported Python version** (3.11) and on the station's interpreter: `py_compile` of changed files and changed/new modules (uv: `uv python install 3.11` outside the repo, a venv with PyYAML) |
| tests that patch `os.name`, `sys.platform`, `os.path.isabs` | the full module on 3.11 and on the newest (Windows 3.13 changes `isabs`; `Path` under a patched `os.name` does not work on 3.11) |
| paths, files, line endings, symlinks, locks | Windows 3.11, 3.12 and 3.13 in an environment like the runner (empty profile, PATH without `skillsync`, TEMP in 8.3 form), changed modules in a targeted way; full run after LEASE |
| HTTP, files, signals | macOS (CI or a machine) for the changed modules |
| every new or changed skip | `scripts/check_skips.py` with the baseline after the change on the `-v` logs of **all systems** (Linux, macOS, Windows; key `<system>/<mode>`): rc=0; check the categories for capture (the first match wins) |
| every change to `tests/skip-baseline.json` (entry, category, system key, `null` → list) | the WHOLE module `test_skillsync_check_skips` (the `RealBaselineTests` shape guard expects a list or `null` according to the `measured` tuple) and `test_skillsync_ci_workflows` on the oldest and newest Python version, plus `check_skips.py` on the logs of all systems; the script alone on the logs is not enough (d6cabea, ubuntu-latest baseline: a red shape guard on origin) |
| a new skip category or fragment | self-consistency: every fragment classifies into its own category |
| full Linux loop before the push (mandatory step) | a loop with `-v` to a log file, then `scripts/check_skips.py` on that log (rc=0); commands in the block "Commands of the mandatory steps" under the table. A loop without `-v` is not evidence: when the log has skips (and a full loop does), `check_skips.py` exits with code 2, because the summary mentions skips and the log has no result rows; a log without skips without `-v` gives 0, but proves nothing |
| local prototype E2E | the same step as in CI (`.github/workflows/ci.yml`, job "Prototype E2E (Chrome)"): every skip in the `prototype_e2e` module log is a failure (a green `OK` with skips does not prove E2E); commands in the block under the table |

### Commands of the mandatory steps

```sh
# full Linux loop (with -v!), then the skip baseline
python3 -m unittest discover -s tests -p 'test_skillsync*.py' -v 2>&1 | tee unittest.log
python3 scripts/check_skips.py unittest.log --system linux --mode symlink

# local prototype E2E (as in CI): every skip is a failure
rc=0
SKILLSYNC_E2E_CHROME=<chrome> python3 -m unittest discover -s tests -p 'test_skillsync_prototype_e2e.py' -v > e2e.log 2>&1 || rc=$?
cat e2e.log
[ "$rc" -eq 0 ] || echo "E2E: run ended with code $rc: failure"      # as in CI: rc == 0 AND no skips
if grep -Eq '\.\.\. skipped|^OK \(skipped' e2e.log; then echo "E2E skipped: failure"; fi
```

Scope: the Windows and macOS machines are shared between projects, so the **full matrix before a push runs once, on the release branch** (the integration one); the working branch gets targeted evidence (changed modules on Windows 3.11, 3.12 and 3.13, macOS for HTTP, files and signals), not a full run.

Rules for runs: stop test processes **only by your own PID** (or your own process group or `systemd-run` scope), never by a name pattern (`pkill -f`): on a shared station you would kill someone else's run. Parallel runs on one machine (e.g. Windows 3.11, 3.12 and 3.13 at once) get disjoint slices of test server ports (`SKILLSYNC_TEST_DASHBOARD_PORTS=<from>-<to>`), otherwise modules with a server give `WinError 10048` (port in use), which is not a product failure.

## Runs on a remote machine

Lessons from matrix runs on remote machines (Windows, macOS):

- **Logs incrementally.** Copy module logs to the station during the run (every few minutes), not only after it ends: a hung or killed run then leaves evidence,
  and `check_skips` has something to read.
- **Death by the PIDs of the whole tree.** After `kill` check that all processes of the tree (runner, interpreter, test servers) are dead (by the PIDs recorded at
  start, not by name), and only then delete the working directories; otherwise a live process holds files and ports, and the deletion fails or breaks the next run.
- **One port pool = one process.** A slice of test ports belongs to one run at a time; two processes on the same slice give a port in use that
  looks like a product failure.
- **A module list without shell aliases.** Collect the list of modules for a run with a command that an alias does not replace (`command ls`, an absolute path or a `glob` in a
  script): an alias with colors and icons inserts control codes into the list, and the run launches empty or wrong module names.
- **Background start on macOS without `setsid`.** Standard macOS has no `setsid` command; start in the background with `nohup` with redirection and the PID written to a file, and
  within a minute check that the process is alive and the log is growing.

CI monitor: do not iterate the `gh pr checks` result with a `for` loop over a variable (zsh does not split a variable into lines, the whole result is one element); use `gh pr checks <nr> --json name,state` (with `jq`) or `gh pr checks <nr> | while read -r line; do …; done`.

After a heavy run on a shared resource (station, remote machine) check that the lock (`flock`, lease file) and the test processes are gone (`pgrep` by the recorded PIDs, `fuser`/`lsof` on the lock file, no listening ports from the pool), instead of trusting the run's declaration: only that releases the resource.

Helper scripts live in the operations repo `skillsync-ops` (`tools/win-shard/`): `prepush.sh` (the Windows matrix and `check_skips` from one command),
`runlike.sh`/`runlike.ps1` (Windows, an environment like the runner), `skips_union.py` and `apply_windows_baseline.py` (baseline from CI logs); the core has only
`scripts/check_skips.py`. The step report lists
the combinations with the result (PASS/FAIL/NOT_RUN), interpreter versions and commands.
