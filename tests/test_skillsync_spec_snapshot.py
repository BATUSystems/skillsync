"""`skillsync spec snapshot` i `--check` (K4, K15): tabela stanów, compare-and-swap, archiwum, ścieżki, atomowość, kody wyjścia."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import spec_snapshot  # noqa: E402
from skillsync_workflow.common import redact  # noqa: E402
from skillsync_workflow.spec_snapshot import check, snapshot  # noqa: E402
from skillsync_workflow.status import git_blob_sha  # noqa: E402

GIT = shutil.which('git')
CLI = Path(__file__).resolve().parents[1] / 'skillsync'
RUN = 'r-one'
SPEC_TEXT = 'Status: approved\n\n# Spec\ntreść speca\n'
PLAN_TEXT = f'Status: in_progress\nPrzebieg: {RUN}\nSpec: docs/specs/s.md\n\n- [ ] F1\n'
needs_symlinks = unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlinki niedostępne')


def sha(text):
    return git_blob_sha(text.encode())


def run_git(repo, *args):
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
    subprocess.run([GIT, '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t' + chr(64) + 'e.test', *args], check=True, stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='specsnap-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'
        self.archive = self.base / 'archive'
        self.archive.mkdir()
        self.root = self.make_project(self.repo)
        self.manifest = {'project': {'name': 'proj'}, 'workflow': {'specs': {'archive': str(self.archive)}}}

    def make_project(self, path, git=True):
        path.mkdir(parents=True, exist_ok=True)
        if git:
            run_git(path, 'init', '-q')
        (path / 'docs/specs').mkdir(parents=True)
        (path / 'docs/plans').mkdir(parents=True)
        (path / 'docs/specs/s.md').write_text(SPEC_TEXT, encoding='utf-8')
        (path / 'docs/plans/p.md').write_text(PLAN_TEXT, encoding='utf-8')
        return path

    def snap(self, manifest=None, **kw):
        return snapshot(self.root, self.manifest if manifest is None else manifest, kw.pop('run_id', RUN), **kw)

    def run_docs(self, root=None):
        return Path(root or self.root) / '.skillsync/runs' / RUN / 'docs'

    def arch(self, name='proj'):
        return self.archive / name / RUN

    def targets(self, report):
        return {target['target']: target for target in report['targets']}

    def saved(self):
        return json.loads((self.run_docs() / '.saved.json').read_text())['saved']


@unittest.skipUnless(GIT, 'brak gita')
class WriteTests(Base):
    def test_first_write_creates_run_and_archive_copies(self):
        report, code = self.snap()
        self.assertEqual((code, report['status']), (0, 'PASS'))
        for base in (self.run_docs(), self.arch()):
            self.assertEqual((base / 'specs/s.md').read_text(), SPEC_TEXT)
            self.assertEqual((base / 'plans/p.md').read_text(), PLAN_TEXT)
        self.assertEqual(self.saved(), {'run': {'specs/s.md': sha(SPEC_TEXT), 'plans/p.md': sha(PLAN_TEXT)},
                                        'archive': {'specs/s.md': sha(SPEC_TEXT), 'plans/p.md': sha(PLAN_TEXT)}})
        self.assertEqual({entry['file']: entry['sha'] for entry in report['files']},
                         {'specs/s.md': sha(SPEC_TEXT), 'plans/p.md': sha(PLAN_TEXT)})
        self.assertEqual({entry['status'] for target in report['targets'] for entry in target['files']}, {'written'})

    @unittest.skipIf(os.name == 'nt', 'prawa POSIX')
    def test_permissions_dirs_0700_files_0600(self):
        self.snap()
        for base in (self.run_docs(), self.arch()):
            for path in (base, base / 'specs', base / 'specs/s.md', base / 'plans/p.md'):
                expected = 0o600 if path.is_file() else 0o700
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path))
        self.assertEqual(stat.S_IMODE((self.run_docs() / '.saved.json').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / '.skillsync/runs' / RUN).stat().st_mode), 0o700)

    def test_repeat_is_idempotent_noop(self):
        self.snap()
        before = {path: path.stat().st_mtime_ns for path in self.run_docs().rglob('*') if path.is_file()}
        report, code = self.snap()
        self.assertEqual(code, 0)
        self.assertEqual({entry['status'] for target in report['targets'] for entry in target['files']}, {'skipped'})
        self.assertEqual(before, {path: path.stat().st_mtime_ns for path in before})

    def test_changed_source_is_refreshed_without_replace(self):
        self.snap()
        (self.root / 'docs/plans/p.md').write_text(PLAN_TEXT + '- [x] F1\n', encoding='utf-8')
        report, code = self.snap()
        self.assertEqual(code, 0)
        self.assertIn('[x] F1', (self.arch() / 'plans/p.md').read_text())
        self.assertEqual(self.saved()['run']['plans/p.md'], sha((self.root / 'docs/plans/p.md').read_text()))

    def test_target_changed_by_someone_is_blocked_and_untouched(self):
        self.snap()
        edited = self.run_docs() / 'specs/s.md'
        edited.write_text('ręczna zmiana\n')
        (self.root / 'docs/specs/s.md').write_text(SPEC_TEXT + 'nowa linia\n', encoding='utf-8')
        report, code = self.snap()
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertEqual(edited.read_text(), 'ręczna zmiana\n')
        self.assertEqual(self.targets(report)['run']['status'], 'BLOCKED')
        self.assertEqual(self.targets(report)['archive']['status'], 'PASS')   # CAS osobno dla celu
        self.assertIn('nowa linia', (self.arch() / 'specs/s.md').read_text())
        self.assertEqual(self.saved()['run']['specs/s.md'], sha(SPEC_TEXT))   # wpis celu bez zmiany

    def test_replace_overwrites_changed_target(self):
        self.snap()
        edited = self.run_docs() / 'specs/s.md'
        edited.write_text('ręczna zmiana\n')
        report, code = self.snap(replace=True)
        self.assertEqual(code, 0)
        self.assertEqual(edited.read_text(), SPEC_TEXT)
        self.assertEqual(self.saved()['run']['specs/s.md'], sha(SPEC_TEXT))

    def test_target_without_record_and_other_sha_is_blocked(self):
        (self.run_docs() / 'specs').mkdir(parents=True)
        (self.run_docs() / 'specs/s.md').write_text('cudze\n')
        report, code = self.snap({'project': {'name': 'proj'}})
        self.assertEqual(code, 2)
        self.assertEqual((self.run_docs() / 'specs/s.md').read_text(), 'cudze\n')
        # plan (cel nie istnieje) zapisany niezależnie
        self.assertTrue((self.run_docs() / 'plans/p.md').exists())

    def test_target_without_record_with_source_sha_is_skipped_and_recorded(self):
        (self.run_docs() / 'specs').mkdir(parents=True)
        (self.run_docs() / 'specs/s.md').write_text(SPEC_TEXT)
        report, code = self.snap({'project': {'name': 'proj'}})
        self.assertEqual(code, 0)
        entries = {entry['file']: entry['status'] for entry in self.targets(report)['run']['files']}
        self.assertEqual(entries, {'specs/s.md': 'skipped', 'plans/p.md': 'written'})
        self.assertEqual(self.saved()['run']['specs/s.md'], sha(SPEC_TEXT))

    def test_corrupt_saved_json_blocks_differing_cells_and_replace_rebuilds(self):
        self.snap()
        (self.run_docs() / '.saved.json').write_text('{ nie json')
        (self.root / 'docs/specs/s.md').write_text(SPEC_TEXT + 'v2\n', encoding='utf-8')
        report, code = self.snap()
        self.assertEqual(code, 2)
        self.assertEqual((self.run_docs() / 'specs/s.md').read_text(), SPEC_TEXT)
        self.assertEqual((self.run_docs() / '.saved.json').read_text(), '{ nie json')   # odtwarzany tylko z --replace
        report, code = self.snap(replace=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.saved()['run']['specs/s.md'], sha(SPEC_TEXT + 'v2\n'))

    def test_corrupt_saved_json_with_identical_cells_is_noop(self):
        self.snap()
        (self.run_docs() / '.saved.json').write_text('[]')
        report, code = self.snap()
        self.assertEqual(code, 0)

    def test_no_plan_and_no_spec_is_blocked_and_writes_nothing(self):
        (self.root / 'docs/plans/p.md').write_text('Status: draft\nPrzebieg: inny\n')
        report, code = self.snap()
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertFalse((self.root / '.skillsync').exists())
        self.assertEqual(list(self.archive.iterdir()), [])

    def test_spec_without_plan_saves_only_spec(self):
        (self.root / 'docs/plans/p.md').unlink()
        report, code = self.snap(spec='docs/specs/s.md')
        self.assertEqual(code, 0)
        self.assertTrue((self.run_docs() / 'specs/s.md').exists())
        self.assertFalse((self.run_docs() / 'plans').exists())
        self.assertEqual([entry['file'] for entry in report['files']], ['specs/s.md'])

    def test_explicit_spec_overrides_plan_line(self):
        (self.root / 'docs/specs/other.md').write_text('Status: draft\ninny\n')
        report, code = self.snap(spec='docs/specs/other.md')
        self.assertEqual(code, 0)
        self.assertTrue((self.run_docs() / 'specs/other.md').exists())
        self.assertFalse((self.run_docs() / 'specs/s.md').exists())

    def test_plan_without_spec_line_saves_plan_with_note(self):
        (self.root / 'docs/plans/p.md').write_text(f'Status: draft\nPrzebieg: {RUN}\n')
        report, code = self.snap()
        self.assertEqual(code, 0)
        self.assertEqual([entry['file'] for entry in report['files']], ['plans/p.md'])
        self.assertTrue(report['notes'])

    def test_run_directory_is_created_with_docs(self):
        self.assertFalse((self.root / '.skillsync').exists())
        self.snap({'project': {'name': 'proj'}})
        self.assertTrue((self.run_docs() / 'specs/s.md').exists())

    def test_invalid_run_id_and_paths_are_blocked(self):
        for run_id in ('../x', 'a/b', '', '.hidden'):
            report, code = snapshot(self.root, self.manifest, run_id)
            self.assertEqual(code, 2, run_id)
        for spec in ('../outside.md', '/etc/passwd', 'docs/specs/../../x.md', 'docs/plans/p.md'):
            report, code = self.snap(spec=spec)
            self.assertEqual(code, 2, spec)
        self.assertFalse((self.root / '.skillsync').exists())

    def test_invalid_archive_in_manifest_is_blocked_without_value_in_message(self):
        for value in ('relative/dir', '~/a/../b', '', 5):
            manifest = {'workflow': {'specs': {'archive': value}}}
            report, code = self.snap(manifest)
            self.assertEqual(code, 2, repr(value))
            self.assertNotIn('relative/dir', json.dumps(report))
        self.assertFalse((self.root / '.skillsync').exists())

    def test_secret_like_content_is_not_echoed(self):
        # test ma potrafić paść: znacznik/token w treści pliku i sekret wzorcowy (`password=...`, maskowany przez `redact`) w nazwie wskazanej przez --spec.
        # `redact` nie zna wzorca ghp_ (maskuje sekrety ze środowiska i wzorce typu password=), więc część komunikatowa używa wzorca, który zna.
        # Dowód mutacyjny: usunięcie `redact(report)` z `_cmd_snapshot` albo `redact` w `_message` czerwieni drugą część (sekret w komunikacie).
        token = 'gh' + 'p_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'
        marker = 'ZNACZNIK-TRESCI-7431'
        (self.root / 'docs/specs/s.md').write_text(f'Status: draft\n{marker} token {token}\n')
        report, code = self.snap()
        self.assertEqual(code, 0)
        dumped = json.dumps(report) + spec_snapshot._render(report)
        self.assertNotIn(token, dumped)
        self.assertNotIn(marker, dumped)
        pattern_secret = 'Zq9xK2mV7pL4'
        report, code = self.snap(spec=f'docs/specs/password={pattern_secret}.md')
        self.assertEqual(code, 2)
        self.assertIn('BLOCKED', json.dumps(report))
        self.assertNotIn(pattern_secret, json.dumps(redact(report)) + spec_snapshot._render(redact(report)))

    def test_sanitize_neutralizes_dots_and_empty_names(self):
        for name in ('..', '.', '...', ''):
            self.assertEqual(spec_snapshot._sanitize(name), '-', name)
        self.assertEqual(spec_snapshot._sanitize('a/../b c'), 'a-..-b-c')
        self.assertEqual(spec_snapshot._sanitize('..x'), '..x')

    def test_archive_tilde_is_expanded(self):
        with mock.patch.dict(os.environ, {'HOME': str(self.base)}):
            (self.base / 'home-arch').mkdir()
            manifest = {'project': {'name': 'proj'}, 'workflow': {'specs': {'archive': '~/home-arch'}}}
            report, code = self.snap(manifest)
        self.assertEqual(code, 0)
        self.assertTrue((self.base / 'home-arch/proj' / RUN / 'specs/s.md').exists())
        self.assertNotIn(str(self.base), json.dumps(report))

    def test_blocked_report_has_mode_run_and_no_absolute_paths(self):
        report, code = snapshot(self.root, self.manifest, 'inny-przebieg')
        self.assertEqual((code, report['mode'], report['run']), (2, 'snapshot', 'inny-przebieg'))
        report, code = check(self.root, self.manifest, 'x y')
        self.assertEqual((code, report['mode']), (2, 'check'))
        report, code = snapshot(self.root, self.manifest, RUN, spec=str(self.root / 'docs/specs/nie-ma.md'))
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.base), json.dumps(report))

    def test_mkdir_race_is_tolerated(self):
        real = os.mkdir

        def racing(path, mode=0o777):
            real(path, mode)
            raise FileExistsError(path)
        with mock.patch.object(spec_snapshot.os, 'mkdir', side_effect=racing):
            report, code = self.snap()
        self.assertEqual(code, 0)

    @needs_symlinks
    def test_symlinked_docs_in_source_is_refused(self):
        real = self.root / 'docs-real'
        (self.root / 'docs').rename(real)
        (self.root / 'docs').symlink_to(real)
        report, code = self.snap()
        self.assertEqual(code, 2)
        self.assertFalse((self.root / '.skillsync').exists())

    def test_non_utf8_name_does_not_traceback(self):
        report, code = snapshot(self.root, self.manifest, RUN, spec='docs/specs/\udcff.md')
        self.assertEqual(code, 2)
        self.assertNotIn('Traceback', json.dumps(report))

    def test_shared_archive_separates_projects_by_name(self):
        self.snap()
        other = self.make_project(self.base / 'repo2')
        (other / 'docs/specs/s.md').write_text('Status: draft\ninny projekt\n')
        report, code = snapshot(other, {'project': {'name': 'proj2'}, 'workflow': self.manifest['workflow']}, RUN)
        self.assertEqual(code, 0)
        self.assertIn('inny projekt', (self.arch('proj2') / 'specs/s.md').read_text())
        self.assertEqual((self.arch('proj') / 'specs/s.md').read_text(), SPEC_TEXT)

    def test_project_name_sanitized_and_falls_back_to_main_repo_dir(self):
        self.snap({'project': {'name': 'Org/pro j'}, 'workflow': self.manifest['workflow']})
        self.assertTrue((self.archive / 'Org-pro-j' / RUN / 'specs/s.md').exists())
        run_git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'x')
        worktree = self.base / 'wt-dir'
        run_git(self.repo, 'worktree', 'add', '-q', str(worktree), '-b', 'wt')
        self.make_project(worktree, git=False) if not (worktree / 'docs').exists() else None
        (worktree / 'docs/specs').mkdir(parents=True, exist_ok=True)
        (worktree / 'docs/plans').mkdir(parents=True, exist_ok=True)
        (worktree / 'docs/specs/s.md').write_text(SPEC_TEXT)
        (worktree / 'docs/plans/p.md').write_text(PLAN_TEXT)
        report, code = snapshot(worktree, {'workflow': self.manifest['workflow']}, RUN)
        self.assertEqual(code, 0)
        self.assertTrue((self.archive / 'repo' / RUN / 'specs/s.md').exists())   # katalog głównego repo, nie worktree

    def test_archive_unavailable_is_fail_but_run_target_written(self):
        manifest = {'project': {'name': 'proj'}, 'workflow': {'specs': {'archive': str(self.base / 'nie-ma')}}}
        report, code = self.snap(manifest)
        self.assertEqual((code, report['status']), (1, 'FAIL'))
        self.assertEqual(self.targets(report)['archive']['status'], 'FAIL')
        self.assertEqual(self.targets(report)['run']['status'], 'PASS')
        self.assertTrue((self.run_docs() / 'specs/s.md').exists())
        self.assertFalse((self.base / 'nie-ma').exists())   # korzeń archiwum nie jest tworzony
        self.assertEqual(set(self.saved()), {'run'})

    def test_mixed_blocked_and_fail_exits_with_highest_weight(self):
        self.snap()
        (self.run_docs() / 'specs/s.md').write_text('zmiana\n')
        (self.root / 'docs/specs/s.md').write_text(SPEC_TEXT + 'v2\n')
        shutil.rmtree(self.archive)
        report, code = self.snap()
        self.assertEqual(({target['status'] for target in report['targets']}, code), ({'BLOCKED', 'FAIL'}, 2))

    @needs_symlinks
    def test_symlinked_destination_file_is_refused(self):
        self.snap()
        outside = self.base / 'outside.md'
        outside.write_text('cel poza\n')
        dest = self.arch() / 'specs/s.md'
        dest.unlink()
        dest.symlink_to(outside)
        (self.root / 'docs/specs/s.md').write_text(SPEC_TEXT + 'v2\n')
        report, code = self.snap()
        self.assertEqual(code, 2)
        self.assertEqual(outside.read_text(), 'cel poza\n')
        self.assertTrue(dest.is_symlink())

    @needs_symlinks
    def test_symlinked_directories_are_refused(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.arch('proj').parent).mkdir()
        self.arch().symlink_to(outside, target_is_directory=True)
        report, code = self.snap()
        self.assertEqual(self.targets(report)['archive']['status'], 'BLOCKED')
        self.assertEqual(list(outside.iterdir()), [])
        # katalog docs/ w przebiegu jako dowiązanie
        shutil.rmtree(self.root / '.skillsync')
        (self.root / '.skillsync/runs' / RUN).mkdir(parents=True)
        (self.root / '.skillsync/runs' / RUN / 'docs').symlink_to(outside, target_is_directory=True)
        report, code = self.snap()
        self.assertEqual(code, 2)
        self.assertEqual(list(outside.iterdir()), [])

    @needs_symlinks
    def test_symlinked_archive_root_is_refused(self):
        real = self.base / 'real'
        real.mkdir()
        link = self.base / 'link'
        link.symlink_to(real, target_is_directory=True)
        report, code = self.snap({'project': {'name': 'p'}, 'workflow': {'specs': {'archive': str(link)}}})
        self.assertEqual(self.targets(report)['archive']['status'], 'BLOCKED')
        self.assertEqual(list(real.iterdir()), [])

    @needs_symlinks
    def test_symlinked_source_is_refused(self):
        outside = self.base / 'secret.md'
        outside.write_text('Status: draft\n')
        (self.root / 'docs/specs/link.md').symlink_to(outside)
        report, code = self.snap(spec='docs/specs/link.md')
        self.assertEqual(code, 2)

    def test_replace_failure_midway_leaves_target_and_no_temp(self):
        self.snap()
        (self.root / 'docs/specs/s.md').write_text(SPEC_TEXT + 'v2\n')
        with mock.patch.object(spec_snapshot.os, 'replace', side_effect=OSError('boom')):
            report, code = self.snap()
        self.assertEqual(code, 1)
        self.assertEqual((self.run_docs() / 'specs/s.md').read_text(), SPEC_TEXT)
        self.assertEqual([p.name for p in self.run_docs().rglob('*.tmp')], [])
        self.assertEqual(self.saved()['run']['specs/s.md'], sha(SPEC_TEXT))
        self.assertNotIn('boom', json.dumps(report))

    def test_source_larger_than_limit_is_blocked(self):
        (self.root / 'docs/specs/s.md').write_text('x' * (512 * 1024 + 1))
        report, code = self.snap()
        self.assertEqual(code, 2)


@unittest.skipUnless(GIT, 'brak gita')
class CheckTests(Base):
    def setUp(self):
        super().setUp()
        run_git(self.repo, 'commit', '-q', '--allow-empty', '-m', 'x')
        self.worktree = self.base / 'wt'
        run_git(self.repo, 'worktree', 'add', '-q', str(self.worktree), '-b', 'wt')
        self.make_project(self.worktree, git=False)

    def tree_state(self, *roots):
        out = {}
        for root in roots:
            for path in sorted(Path(root).rglob('*')):
                if '.git' not in path.parts:
                    out[str(path)] = path.stat().st_mtime_ns if path.is_file() else None
        return out

    def test_main_checkout_without_targets_passes_with_note(self):
        self.snap({'project': {'name': 'proj'}})
        report, code = check(self.root, {'project': {'name': 'proj'}}, RUN)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertTrue(report['notes'])

    def test_stale_run_snapshot_is_blocked(self):
        manifest = {'project': {'name': 'proj'}}
        report, code = check(self.root, manifest, RUN)   # brak migawki
        self.assertEqual(code, 2)
        self.snap(manifest)
        (self.root / 'docs/plans/p.md').write_text(PLAN_TEXT + 'v2\n')
        report, code = check(self.root, manifest, RUN)
        self.assertEqual((code, report['targets'][0]['mismatched']), (2, ['plans/p.md']))

    def test_linked_worktree_without_outside_target_is_blocked(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        report, code = check(self.worktree, manifest, RUN)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertIn('poza worktree', report['reason'])

    def test_linked_worktree_with_to_passes_and_does_not_write(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        snapshot(self.repo, manifest, RUN)   # kopia przebiegu w głównym repozytorium
        before = self.tree_state(self.worktree, self.repo)
        report, code = check(self.worktree, manifest, RUN, to=self.repo)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertEqual({target['target'] for target in report['targets']}, {'run', 'to'})
        self.assertEqual(before, self.tree_state(self.worktree, self.repo))

    def test_to_with_different_sha_or_missing_is_blocked_with_list(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        report, code = check(self.worktree, manifest, RUN, to=self.base / 'nie-ma')
        self.assertEqual(code, 2)
        snapshot(self.repo, manifest, RUN)
        (self.repo / '.skillsync/runs' / RUN / 'docs/specs/s.md').write_text('inne\n')
        report, code = check(self.worktree, manifest, RUN, to=self.repo)
        self.assertEqual(code, 2)
        self.assertEqual([t for t in report['targets'] if t['target'] == 'to'][0]['mismatched'], ['specs/s.md'])

    def test_to_inside_the_worktree_does_not_count_as_outside(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        report, code = check(self.worktree, manifest, RUN, to=self.worktree)
        self.assertEqual(code, 2)

    def test_archive_satisfies_linked_worktree(self):
        snapshot(self.worktree, self.manifest, RUN)
        report, code = check(self.worktree, self.manifest, RUN)
        self.assertEqual((code, report['status']), (0, 'PASS'))
        (self.arch() / 'plans/p.md').write_text('zmiana\n')
        report, code = check(self.worktree, self.manifest, RUN)
        self.assertEqual(code, 2)

    def test_archive_unavailable_is_blocked(self):
        snapshot(self.worktree, self.manifest, RUN)
        shutil.rmtree(self.archive)
        report, code = check(self.worktree, self.manifest, RUN)
        self.assertEqual(code, 2)
        self.assertIn('archive', report['reason'])

    def test_missing_plan_is_blocked(self):
        report, code = check(self.worktree, self.manifest, 'inny')
        self.assertEqual(code, 2)

    def test_git_failure_in_linked_checkout_is_fail_closed(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        with mock.patch.object(spec_snapshot, '_git_dirs', return_value=None):
            report, code = check(self.worktree, manifest, RUN)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertIn('typu checkoutu', report['reason'])

    def test_dangling_git_file_is_blocked_and_missing_git_is_not(self):
        manifest = {'project': {'name': 'proj'}}
        plain = self.make_project(self.base / 'plain', git=False)
        snapshot(plain, manifest, RUN)
        report, code = check(plain, manifest, RUN)   # brak .git: nie ma czego sprzątać
        self.assertEqual(code, 0)
        (plain / '.git').write_text('gitdir: ' + str(self.base / 'nie-ma-repo') + '\n')
        report, code = check(plain, manifest, RUN)
        self.assertEqual((code, report['status']), (2, 'BLOCKED'))
        self.assertIn('typu checkoutu', report['reason'])

    def test_archive_inside_worktree_is_not_outside(self):
        inner = self.worktree / 'arch'
        inner.mkdir()
        manifest = {'project': {'name': 'proj'}, 'workflow': {'specs': {'archive': str(inner)}}}
        snapshot(self.worktree, manifest, RUN)
        report, code = check(self.worktree, manifest, RUN)
        self.assertEqual(code, 2)
        self.assertFalse(self.targets(report)['archive']['outside_worktree'])

    def test_relative_and_tilde_to(self):
        manifest = {'project': {'name': 'proj'}}
        snapshot(self.worktree, manifest, RUN)
        snapshot(self.repo, manifest, RUN)
        with mock.patch.dict(os.environ, {'HOME': str(self.base)}):
            report, code = check(self.worktree, manifest, RUN, to='~/repo')
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertNotIn(str(self.base), json.dumps(report))
        cwd = os.getcwd()
        try:
            os.chdir(self.base)
            report, code = check(self.worktree, manifest, RUN, to='repo')
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)

    @needs_symlinks
    def test_symlinked_kind_directory_in_target_is_unavailable(self):
        snapshot(self.worktree, self.manifest, RUN)
        specs = self.arch() / 'specs'
        real = self.arch() / 'specs-real'
        specs.rename(real)
        specs.symlink_to(real)
        report, code = check(self.worktree, self.manifest, RUN)
        self.assertEqual(code, 2)
        self.assertEqual(self.targets(report)['archive']['status'], 'BLOCKED')


@unittest.skipUnless(GIT, 'brak gita')
class CliTests(Base):
    def cli(self, *args):
        env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull}
        return subprocess.run([sys.executable, str(CLI), 'spec', 'snapshot', *map(str, args)], capture_output=True, text=True, timeout=60, env=env)

    def setUp(self):
        super().setUp()
        self.manifest_file = self.base / 'manifest.yaml'
        self.manifest_file.write_text(f'project:\n  name: proj\nworkflow:\n  specs:\n    archive: {self.archive}\n', encoding='utf-8')

    def test_cli_write_check_and_exit_codes(self):
        done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file, '--json')
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (0, 'PASS'), done.stderr)
        done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file, '--check', '--json')
        self.assertEqual((done.returncode, json.loads(done.stdout)['mode']), (0, 'check'))
        (self.arch() / 'specs/s.md').write_text('zmiana\n')
        done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file, '--check', '--json')
        self.assertEqual(done.returncode, 2)
        done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file)
        self.assertEqual(done.returncode, 2)
        self.assertIn('BLOCKED', done.stdout)
        done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file, '--replace')
        self.assertEqual(done.returncode, 0)

    def test_cli_invalid_combinations_and_missing_manifest_are_blocked(self):
        for extra in (['--to', self.base], ['--check', '--replace']):
            done = self.cli(self.root, '--run-id', RUN, '--manifest', self.manifest_file, '--json', *extra)
            self.assertEqual(done.returncode, 2, extra)
        done = self.cli(self.root, '--run-id', RUN, '--json')   # brak manifestu w projekcie
        self.assertEqual(done.returncode, 2)
        self.assertEqual(json.loads(done.stdout)['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
