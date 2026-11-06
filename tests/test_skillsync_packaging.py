"""K1/K10/K12: packaging contract. `scripts/check_wheel.py`, `scripts/check_release.py` and `pyproject.toml`.

Everything runs offline on temporary wheels and project trees; the build smoke needs `hatchling` and is skipped
without it.
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK_WHEEL = ROOT / 'scripts' / 'check_wheel.py'
CHECK_RELEASE = ROOT / 'scripts' / 'check_release.py'
PYPROJECT = ROOT / 'pyproject.toml'

GOOD = [
    'skillsync_workflow/__init__.py',
    'skillsync_workflow/cli.py',
    'skillsync_workflow/__main__.py',
    'skillsync_workflow/selfcmd.py',
    'skillsync_workflow/quality.py',
    'skillsync_workflow/data/skills.yaml',
    'skillsync-0.9.0.dist-info/METADATA',
    'skillsync-0.9.0.dist-info/WHEEL',
    'skillsync-0.9.0.dist-info/entry_points.txt',
    'skillsync-0.9.0.dist-info/RECORD',
    'skillsync-0.9.0.dist-info/licenses/LICENSE',
]


def run_script(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, timeout=60)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)


class CheckWheelTests(TempDirCase):
    def wheel(self, names, name='skillsync-0.9.0-py3-none-any.whl'):
        path = self.tmp / name
        with zipfile.ZipFile(path, 'w') as archive:
            for entry in names:
                archive.writestr(entry, '' if entry.endswith('/') else 'x = 1\n')
        return path

    def test_a_correct_wheel_passes(self):
        result = run_script(CHECK_WHEEL, self.wheel(GOOD))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('OK', result.stdout)

    def test_directory_entries_are_allowed(self):
        result = run_script(CHECK_WHEEL, self.wheel(GOOD + ['skillsync_workflow/data/', 'skillsync-0.9.0.dist-info/']))
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_extra_files_fail_and_are_named(self):
        extras = ['tests/x.py', 'skillsync_workflow/__pycache__/a.pyc', 'docs/x.md', 'skillsync']
        for extra in extras:
            with self.subTest(extra=extra):
                result = run_script(CHECK_WHEEL, self.wheel(GOOD + [extra]))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f'extra: {extra}', result.stdout)

    def test_only_the_offending_names_are_reported(self):
        result = run_script(CHECK_WHEEL, self.wheel(GOOD + ['docs/x.md']))
        self.assertEqual(result.stdout.strip().splitlines(), ['extra: docs/x.md'])

    def test_other_extras_that_look_almost_allowed_fail(self):
        extras = [
            'skillsync_workflow/notes.txt',                 # non-.py outside data
            'skillsync_workflow/data/sub/deep.yaml',        # data is flat
            'skillsync_workflow/__pycache__/cli.cpython-311.pyc',
            'skillsync_workflow/pkg/__pycache__/m.py',      # .py inside a cache dir
            'other_pkg/mod.py',
            'skillsync_workflow_extra/mod.py',              # prefix lookalike
            'scripts/check_wheel.py',
            'skillsync-0.9.0.dist-info/sub/dir/file',       # dist-info nested beyond licenses/
        ]
        for extra in extras:
            with self.subTest(extra=extra):
                result = run_script(CHECK_WHEEL, self.wheel(GOOD + [extra]))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f'extra: {extra}', result.stdout)

    def test_missing_files_fail_with_the_list(self):
        required = ['__init__.py', 'cli.py', '__main__.py', 'selfcmd.py']
        for missing in required:
            with self.subTest(missing=missing):
                names = [n for n in GOOD if n != f'skillsync_workflow/{missing}']
                result = run_script(CHECK_WHEEL, self.wheel(names))
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(result.stdout.strip().splitlines(), [f'missing: skillsync_workflow/{missing}'])

    def test_missing_data_fails(self):
        for names in ([n for n in GOOD if 'data/' not in n], [n for n in GOOD if 'data/' not in n] + ['skillsync_workflow/data/']):
            result = run_script(CHECK_WHEEL, self.wheel(names))
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('missing: skillsync_workflow/data/*', result.stdout)

    def test_extra_and_missing_are_both_listed(self):
        names = [n for n in GOOD if not n.endswith('selfcmd.py')] + ['tests/x.py']
        result = run_script(CHECK_WHEEL, self.wheel(names))
        self.assertEqual(result.returncode, 1)
        self.assertIn('extra: tests/x.py', result.stdout)
        self.assertIn('missing: skillsync_workflow/selfcmd.py', result.stdout)

    def test_bad_arguments_exit_2(self):
        not_zip = self.tmp / 'broken.whl'
        not_zip.write_text('not a zip', encoding='utf-8')
        good = self.wheel(GOOD)
        cases = {
            'no argument': [],
            'missing file': [self.tmp / 'absent.whl'],
            'not a zip': [not_zip],
            'two arguments': [good, good],
            'directory': [self.tmp],
        }
        for label, args in cases.items():
            with self.subTest(label):
                result = run_script(CHECK_WHEEL, *args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')


class CheckReleaseTests(TempDirCase):
    def project(self, init_line='__version__ = "1.0.1"\n', changelog='# Changelog\n\n## [1.0.1] - 2026-11-06\n- x\n'):
        package = self.tmp / 'skillsync_workflow'
        package.mkdir(exist_ok=True)
        (package / '__init__.py').write_text(init_line, encoding='utf-8')
        (self.tmp / 'CHANGELOG.md').write_text(changelog, encoding='utf-8')
        return self.tmp

    def check(self, *args):
        return run_script(CHECK_RELEASE, '--root', self.tmp, *args)

    def test_current_version_with_a_dated_section_passes(self):
        self.project()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('1.0.1', result.stdout)

    def test_a_section_without_a_date_passes(self):
        self.project(changelog='## [1.0.1]\n- x\n')
        self.assertEqual(self.check().returncode, 0)

    def test_missing_section_fails(self):
        self.project(changelog='## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n')
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('1.0.1', result.stdout)

    def test_a_version_that_is_only_a_substring_does_not_pass(self):
        for changelog in ('## [1.0.10] - 2026-11-06\n', '## [11.0.1] - 2026-11-06\n', '## [1.0.1-rc1] - 2026-11-06\n',
                          '## [1.0.1.5] - 2026-11-06\n', '## [1.0.1]x\n', '## [1.0.1]-rc\n', '- see 1.0.1 and ## [1.0.1] in text\n'):
            with self.subTest(changelog=changelog):
                self.project(changelog=changelog)
                self.assertEqual(self.check().returncode, 1)

    def test_the_dot_in_the_version_is_literal(self):
        self.project(changelog='## [1x0x1] - 2026-11-06\n')
        self.assertEqual(self.check().returncode, 1)

    def test_the_heading_must_start_the_line(self):
        self.project(changelog='  ## [1.0.1] - 2026-11-06\ntext ## [1.0.1]\n')
        self.assertEqual(self.check().returncode, 1)

    def test_tag_matching_the_version_passes(self):
        self.project()
        self.assertEqual(self.check('--tag', 'v1.0.1').returncode, 0)

    def test_tag_differing_from_the_version_fails(self):
        self.project(changelog='## [1.0.1]\n## [1.0.2]\n')
        result = self.check('--tag', 'v1.0.2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('1.0.2', result.stdout)

    def test_tag_without_v_is_a_usage_error(self):
        self.project()
        self.assertEqual(self.check('--tag', '1.0.1').returncode, 2)

    def test_explicit_version(self):
        self.project(changelog='## [1.0.1]\n## [1.0.2]\n')
        self.assertEqual(self.check('--version', '1.0.1').returncode, 0)
        self.assertEqual(self.check('--version', '1.0.2').returncode, 1)

    def test_version_and_tag_together_are_a_usage_error(self):
        self.project()
        self.assertEqual(self.check('--version', '1.0.1', '--tag', 'v1.0.1').returncode, 2)

    def test_bad_version_format_is_a_usage_error(self):
        self.project(changelog='## [1.0]\n## [1.0.0.0]\n## [1.0.1]\n')
        for args in (('--version', '1.0'), ('--version', '1.0.0.0'), ('--version', 'v1.0.1'), ('--version', ''),
                     ('--version', '1.0.x'), ('--tag', 'v1.0'), ('--tag', 'v1.0.0.0'), ('--tag', 'vv1.0.1'),
                     ('--tag', 'v1.0.1-rc1')):
            with self.subTest(args=args):
                self.assertEqual(self.check(*args).returncode, 2)

    def test_version_with_single_and_double_quotes(self):
        for line in ('__version__ = "1.0.1"\n', "__version__ = '1.0.1'\n", '"""doc"""\n__version__ = "1.0.1"\nother = 2\n'):
            with self.subTest(line=line):
                self.project(init_line=line)
                self.assertEqual(self.check().returncode, 0)

    def test_unreadable_sources_fail_with_1(self):
        self.project(init_line='x = 1\n')
        self.assertEqual(self.check().returncode, 1)
        self.project(init_line='__version__ = 5\n')
        self.assertEqual(self.check().returncode, 1)
        self.project()
        (self.tmp / 'CHANGELOG.md').unlink()
        self.assertEqual(self.check().returncode, 1)


class PyprojectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with PYPROJECT.open('rb') as handle:
            cls.data = tomllib.load(handle)

    def test_build_system_is_hatchling(self):
        self.assertEqual(self.data['build-system']['build-backend'], 'hatchling.build')
        self.assertTrue(any(r.startswith('hatchling') for r in self.data['build-system']['requires']))

    def test_project_metadata(self):
        project = self.data['project']
        self.assertEqual(project['name'], 'skillsync')
        self.assertEqual(project['requires-python'], '>=3.11')
        self.assertIn('PyYAML>=6', project['dependencies'])
        self.assertEqual(project['license'], 'MIT')
        self.assertEqual(project['dynamic'], ['version'])
        self.assertNotIn('version', project)

    def test_entry_point(self):
        self.assertEqual(self.data['project']['scripts'], {'skillsync': 'skillsync_workflow.cli:run'})

    def test_version_comes_from_the_package(self):
        self.assertEqual(self.data['tool']['hatch']['version']['path'], 'skillsync_workflow/__init__.py')
        self.assertTrue((ROOT / 'skillsync_workflow' / '__init__.py').is_file())

    def test_wheel_contains_only_the_package(self):
        self.assertEqual(self.data['tool']['hatch']['build']['targets']['wheel']['packages'], ['skillsync_workflow'])

    def test_entry_point_target_exists(self):
        module, _, function = self.data['project']['scripts']['skillsync'].partition(':')
        self.assertTrue((ROOT / (module.replace('.', '/') + '.py')).is_file())
        sys.path.insert(0, str(ROOT))
        try:
            import skillsync_workflow.cli as cli
        finally:
            sys.path.remove(str(ROOT))
        self.assertTrue(callable(getattr(cli, function)))


def hatchling_command():
    """argv of a hatchling build backend runner: the module of this interpreter, else a `hatchling` on PATH."""
    if importlib.util.find_spec('hatchling') is not None:
        return [sys.executable, '-m', 'hatchling']
    found = shutil.which('hatchling')
    return [found] if found else None


class BuildSmokeTests(TempDirCase):
    @unittest.skipUnless(hatchling_command(), 'hatchling not installed')
    def test_built_wheel_passes_the_checker_and_carries_the_package_version(self):
        sys.path.insert(0, str(ROOT))
        try:
            import skillsync_workflow
        finally:
            sys.path.remove(str(ROOT))
        out = self.tmp / 'dist'
        build = subprocess.run(hatchling_command() + ['build', '-t', 'wheel', '-d', str(out)],
                               cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        wheels = list(out.glob('*.whl'))
        self.assertEqual(len(wheels), 1, wheels)
        checked = run_script(CHECK_WHEEL, wheels[0])
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        match = re.match(r'skillsync-([^-]+)-', wheels[0].name)
        self.assertIsNotNone(match, wheels[0].name)
        self.assertEqual(match.group(1), skillsync_workflow.__version__)


if __name__ == '__main__':
    unittest.main()
