"""Fixed E2E suite of the prototype engine (`tests/e2e/prototype/*.mjs`).

Behaviour is observed from the UI (headless Chrome over CDP) and from the HTTP API and CLI of `skillsync prototype`; nothing
reads the engine source. Every scenario script prints `{"ids": {ID: {status, evidence}}}`; this module runs the scripts and
asserts the result of each scenario in a subTest named with its ID.

Environment (the Chrome tests are skipped, with the reason, when something is missing):
- `SKILLSYNC_E2E_CHROME`   Chrome/Chromium binary (headless).
- `SKILLSYNC_TEST_NODE`    node >= 22 (global WebSocket) or `node` from PATH.
- `SKILLSYNC_TEST_LIBRARY` library whose `skills/prototype/assets` the test project is created from (`prototype init`).
- `SKILLSYNC_E2E_PORTS`    `chrome,server1,server2`, e.g. `9333,4445,4446` (default 9333,4445,4446).
- `SKILLSYNC_E2E_RESULTS_DIR` optional: the per-script JSON results are copied there.
- `SKILLSYNC_E2E_ONLY`     optional: comma-separated scenario IDs (debugging only; the others are reported as skipped by the script).

Chrome and the servers are stopped by PID. Passwords of the test accounts live only in a 0600 file inside the private working
directory and never reach stdout, logs or screenshots.
"""
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
E2E_DIR = REPO / 'tests' / 'e2e' / 'prototype'
SCRIPT_TIMEOUT = 1000

# Known defects of the engine: a scenario that asserts the CORRECT behaviour and FAILs because of a defect can be listed here as an expected
# failure (XFAIL). Currently empty: no known defects. To add an entry for a future defect:
#   1. make the scenario compute `restOk` = every condition EXCEPT the defect one and return `defectObserved = defect && restOk` in its
#      evidence (a FAIL for any other reason, e.g. a missing hook or an error from a missing dependency, stays a plain FAIL);
#   2. add `'<scenario ID>': '<short description>'` to KNOWN_DEFECTS;
#   3. list the same ID, with the required behaviour, as a nested bullet `- <ID>: ...` under "Znane defekty" in section 10 of
#      docs/prototype-contract/scenariusze-e2e.md (KnownDefectsContractTests compares both).
# A PASS of a listed scenario ("unexpected PASS") and a NOT_RUN of a listed scenario are errors. Remove the entry in the same commit
# that fixes the defect.
# Running scenarios alone with SKILLSYNC_E2E_ONLY needs their predecessors in the list: F1 from F1-S03 (it opens S.A, and S16/S22/S17
# rely on the comments of S04 and the later state), E3-05 before E3-06, F06-A before F06-B, D23-lista (opens the list) and D23-szukaj
# before D23-klawiatura-listy, and in final.mjs Z1 before Z2/Z3 (shared state S.*). The other scenarios are independent.
KNOWN_DEFECTS = {}

CHROME = os.environ.get('SKILLSYNC_E2E_CHROME')
LIBRARY = os.environ.get('SKILLSYNC_TEST_LIBRARY')
NODE = os.environ.get('SKILLSYNC_TEST_NODE') or shutil.which('node')


def node_major(binary):
    if not binary:
        return 0
    try:
        out = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(re.match(r'v(\d+)', out).group(1))
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        return 0


NODE_MAJOR = node_major(NODE)


def ports():
    raw = os.environ.get('SKILLSYNC_E2E_PORTS', '9333,4445,4446')
    values = [int(x) for x in raw.split(',')]
    if len(values) != 3 or len(set(values)) != 3 or any(not 1024 < v < 65536 or v in (9222, 4444) for v in values):
        raise ValueError('SKILLSYNC_E2E_PORTS must be chrome,server1,server2 (three different ports, not 9222/4444)')
    return values


def load_env_module():
    spec = importlib.util.spec_from_file_location('skillsync_e2e_env', E2E_DIR / 'env.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario_scripts():
    return sorted(path for path in E2E_DIR.glob('*.mjs') if path.name != 'lib.mjs')


CONTRACT_E2E = REPO / 'docs' / 'prototype-contract' / 'scenariusze-e2e.md'


def spec_scenario_ids():
    """IDs of the scenario tables (sections 1-6, 8, 11, 12, 13, 14 and 15) of docs/prototype-contract/scenariusze-e2e.md."""
    ids, section = set(), None
    for line in CONTRACT_E2E.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):   # every heading, numbered or not, resets the section
            heading = re.match(r'## (\d+)\.', line)
            section = int(heading.group(1)) if heading else None
        if section in (1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 15) and line.startswith('|'):
            first = line.strip().strip('|').split('|')[0].strip()
            if first and first != 'ID' and not set(first) <= set('-'):
                ids.add(first)
    return ids


def spec_known_defect_ids():
    """IDs listed as nested bullets `- <ID>: ...` under 'Znane defekty' in section 10 of docs/prototype-contract/scenariusze-e2e.md."""
    ids, in_section, in_list, found = set(), False, False, False
    for line in CONTRACT_E2E.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            in_section, in_list = line.startswith('## 10.'), False
            continue
        if not in_section:
            continue
        if line.startswith('- '):
            in_list = line.startswith('- Znane defekty')
            found = found or in_list
            continue
        match = re.match(r'\s{2,}- ([A-Za-z0-9-]+):', line)
        if in_list and match:
            ids.add(match.group(1))
    spec_known_defect_ids.found_bullet = found
    return ids


def port_is_free(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) != 0


@unittest.skipUnless(NODE_MAJOR >= 18, 'set SKILLSYNC_TEST_NODE (or have node on PATH), node >= 18')
class ScriptStructureTests(unittest.TestCase):
    """Without Chrome: the scripts parse and declare unique scenario IDs."""

    def test_scripts_exist_and_pass_syntax_check(self):
        scripts = scenario_scripts()
        self.assertGreaterEqual(len(scripts), 7, 'zero scenario scripts found is not a pass')
        for path in scripts + [E2E_DIR / 'lib.mjs']:
            with self.subTest(script=path.name):
                result = subprocess.run([NODE, '--check', str(path)], capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(NODE_MAJOR >= 22 and LIBRARY, 'listing needs node >= 22 and SKILLSYNC_TEST_LIBRARY')
    def test_scenario_ids_are_unique_and_every_script_lists_some(self):
        env_module = load_env_module()
        with tempfile.TemporaryDirectory(prefix='skillsync-e2e-list-') as tmp:
            env = env_module.prepare(Path(tmp) / 'w', LIBRARY, 9333, 4445, 4446)
            seen = {}
            for path in scenario_scripts():
                listing = list_scenarios(path, Path(tmp) / 'w' / 'env.json')
                self.assertTrue(listing, '%s lists no scenarios' % path.name)
                for item in listing:
                    self.assertNotIn(item['id'], seen, '%s duplicates %s' % (path.name, seen.get(item['id'])))
                    seen[item['id']] = path.name
            expected = spec_scenario_ids()
            self.assertGreater(len(expected), 90, 'the scenario tables of docs/prototype-contract/scenariusze-e2e.md were not parsed')
            self.assertEqual(set(seen), expected, 'scenario IDs differ from docs/prototype-contract/scenariusze-e2e.md: only in scripts %s, only in the contract %s'
                             % (sorted(set(seen) - expected), sorted(expected - set(seen))))
            self.assertLessEqual(set(KNOWN_DEFECTS), set(seen), 'KNOWN_DEFECTS lists an unknown scenario')
            serialized = json.dumps(env)
            for value in json.loads(Path(env['secrets']).read_text()).values():
                self.assertNotIn(value, serialized, 'env.json carries no passwords')


class KnownDefectsContractTests(unittest.TestCase):
    """Without Chrome: KNOWN_DEFECTS equals the known defects listed in section 10 of the contract."""

    def test_known_defects_match_the_contract_section(self):
        documented = spec_known_defect_ids()
        self.assertTrue(getattr(spec_known_defect_ids, 'found_bullet', False),
                        'no "Znane defekty" bullet in section 10 of docs/prototype-contract/scenariusze-e2e.md')
        self.assertEqual(set(KNOWN_DEFECTS), documented,
                         'KNOWN_DEFECTS differs from the contract: only in code %s, only in the contract %s'
                         % (sorted(set(KNOWN_DEFECTS) - documented), sorted(documented - set(KNOWN_DEFECTS))))

    def test_id_parser_ignores_tables_outside_the_scenario_sections(self):
        ids = spec_scenario_ids()
        self.assertNotIn('ID', ids)
        self.assertTrue(all(re.match(r'^[A-Za-z0-9]+(-[A-Za-z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ.]+)*$', x) for x in ids), sorted(ids)[:5])


@unittest.skipUnless(LIBRARY and NODE_MAJOR >= 22, 'set SKILLSYNC_TEST_LIBRARY and use node >= 22')
class EnvironmentTests(unittest.TestCase):
    def test_accounts_exist_and_passwords_stay_in_a_private_file(self):
        env_module = load_env_module()
        with tempfile.TemporaryDirectory(prefix='skillsync-e2e-env-') as tmp:
            work = Path(tmp) / 'w'
            env = env_module.prepare(work, LIBRARY, 9333, 4445, 4446)
            secrets_path = Path(env['secrets'])
            self.assertEqual(secrets_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(env['config']).stat().st_mode & 0o777, 0o600)
            passwords = json.loads(secrets_path.read_text())
            self.assertGreaterEqual(len(passwords), 7)
            visible = (work / 'env.json').read_text()
            for value in passwords.values():
                self.assertNotIn(value, visible)
            users = json.loads((Path(env['base']) / '.skillsync' / 'prototype' / 'users.json').read_text())['users']
            roles = {user['login']: (user['role'], user['mustChange']) for user in users}
            self.assertEqual(roles['anna@example.com'], ('commenter', False))
            self.assertEqual(roles['celina@example.com'], ('acceptor', False))
            self.assertEqual(roles['dawid@example.com'], ('developer', False))
            self.assertEqual(roles['ewa@example.com'], ('commenter', True))
            self.assertEqual(roles['marta@example.com'], ('acceptor', True))
            for password in passwords.values():
                self.assertNotIn(password, json.dumps(users), 'the users file stores hashes only')


def list_scenarios(script, env_path):
    result = subprocess.run([NODE, str(script), '--list'], capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, SKILLSYNC_E2E_ENV=str(env_path)))
    if result.returncode != 0:
        raise AssertionError('%s --list failed: %s' % (script.name, result.stderr[-300:]))
    return json.loads(result.stdout.strip().splitlines()[-1])['ids']


@unittest.skipUnless(CHROME and os.path.exists(CHROME or ''), 'set SKILLSYNC_E2E_CHROME to a Chrome/Chromium binary')
@unittest.skipUnless(LIBRARY, 'set SKILLSYNC_TEST_LIBRARY')
@unittest.skipUnless(NODE_MAJOR >= 22, 'node >= 22 (global WebSocket) required: set SKILLSYNC_TEST_NODE')
class PrototypeE2ETests(unittest.TestCase):
    """One test per script; one subTest per scenario ID. FAIL fails; NOT_RUN needs a technical reason in the evidence."""

    work = None
    env = None
    env_module = None

    @classmethod
    def setUpClass(cls):
        cls.chrome_port, cls.server1, cls.server2 = ports()
        for port in (cls.chrome_port, cls.server1, cls.server2):
            if not port_is_free(port):
                raise unittest.SkipTest('port %d is in use (lease the E2E ports first)' % port)
        cls.env_module = load_env_module()
        cls.tmp = tempfile.TemporaryDirectory(prefix='skillsync-e2e-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name) / 'w'
        cls.env = cls.env_module.prepare(cls.work, LIBRARY, cls.chrome_port, cls.server1, cls.server2)
        cls.addClassCleanup(cls.env_module.stop_all, cls.env)
        cls.env_module.start_chrome(cls.env, CHROME)
        cls.results = {}
        cls.xfails = []

    @classmethod
    def tearDownClass(cls):
        if cls.xfails:
            print('XFAIL summary (%d): %s' % (len(cls.xfails), ', '.join(sorted(cls.xfails))), file=sys.stderr)
        target = os.environ.get('SKILLSYNC_E2E_RESULTS_DIR')
        if target and cls.work:
            Path(target).mkdir(parents=True, exist_ok=True)
            for path in (cls.work / 'results').glob('*.json'):
                shutil.copy(path, Path(target) / path.name)
        if cls.env:
            cls.env_module.stop_all(cls.env)

    def run_script(self, script):
        env_path = self.work / 'env.json'
        declared = list_scenarios(script, env_path)
        process = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=SCRIPT_TIMEOUT,
                                 env=dict(os.environ, SKILLSYNC_E2E_ENV=str(env_path)))
        lines = [line for line in process.stdout.strip().splitlines() if line.startswith('{')]
        self.assertTrue(lines, '%s printed no result (exit %s): %s' % (script.name, process.returncode, process.stderr[-400:]))
        results = json.loads(lines[-1])['ids']
        return declared, results

    def check_script(self, name):
        script = E2E_DIR / (name + '.mjs')
        self.assertTrue(script.exists(), script)
        declared, results = self.run_script(script)
        only = [x for x in os.environ.get('SKILLSYNC_E2E_ONLY', '').split(',') if x]
        executed = 0
        xfailed = 0
        for item in declared:
            scenario = item['id']
            with self.subTest(scenario=scenario):
                if only and scenario not in only:
                    self.skipTest('SKILLSYNC_E2E_ONLY')
                self.assertIn(scenario, results, 'scenario %s produced no result' % scenario)
                outcome = results[scenario]
                evidence = json.dumps(outcome.get('evidence', {}), ensure_ascii=False)[:900]
                status = outcome['status']
                observed = bool(outcome.get('evidence', {}).get('defectObserved'))
                if scenario in KNOWN_DEFECTS:
                    if status == 'PASS':
                        self.fail('unexpected PASS %s: defekt naprawiony, usuń wpis z KNOWN_DEFECTS' % scenario)
                    if status == 'FAIL' and observed:
                        print('XFAIL %s: %s | %s' % (scenario, KNOWN_DEFECTS[scenario], evidence[:300]), file=sys.stderr)
                        xfailed += 1
                        self.xfails.append(scenario)
                        continue
                elif observed and status == 'PASS':
                    self.fail('%s: defectObserved on a PASS is inconsistent' % scenario)
                if scenario in KNOWN_DEFECTS and status == 'NOT_RUN':
                    self.fail('%s: NOT_RUN of a scenario listed in KNOWN_DEFECTS hides the defect check: %s' % (scenario, evidence))
                self.assertIn(status, ('PASS', 'NOT_RUN'), '%s %s: %s' % (scenario, status, evidence))
                if status == 'NOT_RUN':
                    reason = outcome.get('evidence', {}).get('reason') or outcome.get('evidence', {}).get('notRun')
                    self.assertTrue(isinstance(reason, str) and len(reason.strip()) >= 30,
                                    '%s: NOT_RUN without a technical reason (>= 30 characters)' % scenario)
                else:
                    executed += 1
        if not only:
            # XFAIL counts only when the script has no other executed scenario (it still ran and failed as expected)
            self.assertGreater(executed or xfailed, 0, 'zero executed scenarios in %s is not a pass' % name)


def _make(name):
    def test(self):
        self.check_script(name)
    test.__name__ = 'test_' + name
    test.__doc__ = 'Scenarios of %s.mjs' % name
    return test


for _path in scenario_scripts():   # every tests/e2e/prototype/*.mjs becomes a test (a new script needs no change here)
    setattr(PrototypeE2ETests, 'test_' + _path.stem.replace('-', '_'), _make(_path.stem))


if __name__ == '__main__':
    unittest.main()
