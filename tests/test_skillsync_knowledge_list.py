"""CORE-KDOMAIN-LIST: `workflow.knowledge.domain` / `lessons` as a list of directories (repos A and B)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

from skillsync_workflow.quality import knowledge_locations
from tests.test_skillsync_workflow import CLI, WorkflowFixture, needs_os_symlinks


class KnowledgeListFixture(WorkflowFixture):
    """Project `project` (repo B) next to a sibling `repo-a` that owns domain records."""

    def owner(self, name='repo-a', records=None, kind='domain'):
        directory = self.base / name / 'docs' / kind
        directory.mkdir(parents=True, exist_ok=True)
        key = 'entries' if kind == 'domain' else 'lessons'
        listed = []
        for file, title, extra in records or []:
            (directory / file).write_text('---\ntags: [x]\n---\ntext', encoding='utf-8')
            listed.append({'path': file, 'title': title, **extra})
        (directory / 'index.yaml').write_text(yaml.safe_dump({'version': 1, key: listed}), encoding='utf-8')
        return directory

    def setting(self, **knowledge):
        self.manifest['workflow']['knowledge'] = knowledge
        self.save()

    def titles(self, *args):
        return [record['title'] for record in self.report('route', *args)['domain']]


class LocationParsing(KnowledgeListFixture):
    def locations(self, kind='domain'):
        return knowledge_locations(self.root, self.manifest, self.layerset(), kind)

    def test_string_equals_one_element_list(self):
        for value in ('docs/domain', '../docs/domain', '../repo-a/docs/domain'):
            with self.subTest(value=value):
                self.manifest['workflow']['knowledge'] = {'domain': value}
                as_string = self.locations()
                self.manifest['workflow']['knowledge'] = {'domain': [value]}
                self.assertEqual(self.locations(), as_string)

    def test_list_keeps_order_and_bases(self):
        self.manifest['workflow']['knowledge'] = {'domain': ['docs/domain', '../repo-a/docs/domain']}
        self.assertEqual(self.locations(), [(self.root / 'docs/domain', self.root),
                                            (self.base / 'repo-a/docs/domain', self.base / 'repo-a')])

    def test_invalid_lists_name_the_element(self):
        cases = [
            ([], 'must not be an empty list'),
            (['docs/domain', 7], 'domain[1] must be a relative path'),
            (['docs/domain', ''], 'domain[1] must be a relative path'),
            (['docs/domain', 'docs\\domain'], 'domain[1] must be a relative path'),
            (['docs/domain', '/abs/docs/domain'], 'domain[1] must be a relative path'),
            (['docs/domain', 'notes'], 'domain[1] must end with docs/domain'),
            (['docs/domain', '../../x/docs/domain'], 'Domain knowledge path escapes the project parent: ../../x/docs/domain (workflow.knowledge.domain[1])'),
            (['docs/domain', './docs/domain'], 'domain[1] repeats element 0'),
            (['docs/domain', '../project/docs/domain'], 'domain[1] repeats element 0'),
            ([f'../r{n}/docs/domain' for n in range(9)], 'at most 8'),
        ]
        for value, message in cases:
            with self.subTest(value=value):
                self.manifest['workflow']['knowledge'] = {'domain': value}
                with self.assertRaisesRegex(ValueError, __import__('re').escape(message)):
                    self.locations()

    def test_string_messages_are_unchanged(self):
        for value, message in (('notes', 'workflow.knowledge.domain must end with docs/domain'),
                               ('../../x/docs/domain', 'Domain knowledge path escapes the project parent: ../../x/docs/domain'),
                               ('a\\docs/domain', 'workflow.knowledge.domain must be a relative path')):
            with self.subTest(value=value):
                self.manifest['workflow']['knowledge'] = {'domain': value}
                with self.assertRaises(ValueError) as caught:
                    self.locations()
                self.assertEqual(str(caught.exception), message)

    @needs_os_symlinks
    def test_symlinked_repeat_is_a_duplicate(self):
        (self.base / 'alias').symlink_to(self.base / 'repo-a', target_is_directory=True)
        self.owner()
        self.manifest['workflow']['knowledge'] = {'domain': ['../repo-a/docs/domain', '../alias/docs/domain']}
        with self.assertRaisesRegex(ValueError, 'repeats element 0'):
            self.locations()

    def test_lessons_default_next_to_every_domain_directory(self):
        self.manifest['workflow']['knowledge'] = {'domain': ['docs/domain', '../repo-a/docs/domain']}
        self.assertEqual([item[0] for item in self.locations('lessons')],
                         [self.root / 'docs/lessons', self.base / 'repo-a/docs/lessons'])
        self.manifest['workflow']['knowledge'] = {'domain': ['docs/domain'], 'lessons': ['../repo-a/docs/lessons']}
        self.assertEqual([item[0] for item in self.locations('lessons')], [self.base / 'repo-a/docs/lessons'])

    def test_lessons_list_errors_name_the_element(self):
        self.manifest['workflow']['knowledge'] = {'lessons': ['docs/lessons', 'docs/domain']}
        with self.assertRaisesRegex(ValueError, r'lessons\[1\] must end with docs/lessons'):
            self.locations('lessons')


class RouteOrder(KnowledgeListFixture):
    def test_records_follow_list_then_index_order_with_two_bootstraps(self):
        self.write('docs/domain/glossary.md', '---\ntags: [x]\n---\nb')
        self.write('docs/domain/own.md', '---\ntags: [x]\n---\nb')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'own.md', 'title': 'B own'}, {'path': 'glossary.md', 'title': 'B glossary', 'load': 'bootstrap'}]}))
        self.owner(records=[('glossary.md', 'A glossary', {'load': 'bootstrap'}), ('rule.md', 'A rule', {})])
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        self.assertEqual(self.titles(), ['B glossary', 'A glossary'])
        self.assertEqual(self.titles('--topics', 'domain'), ['B own', 'B glossary', 'A glossary', 'A rule'])
        self.setting(domain=['../repo-a/docs/domain', 'docs/domain'])
        self.assertEqual(self.titles('--topics', 'domain'), ['A glossary', 'A rule', 'B own', 'B glossary'])

    def test_same_file_names_in_two_directories_do_not_collide(self):
        self.owner(records=[('glossary.md', 'A', {'load': 'bootstrap'})])
        self.write('docs/domain/glossary.md', '---\ntags: [x]\n---\nb')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'glossary.md', 'title': 'B', 'load': 'bootstrap'}]}))
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        paths = [record['path'] for record in self.report('route')['domain']]
        self.assertEqual(paths, [str(self.root / 'docs/domain/glossary.md'), str(self.base / 'repo-a/docs/domain/glossary.md')])

    def test_missing_owner_directory_is_skipped(self):
        self.write('docs/domain/own.md', '---\ntags: [x]\n---\nb')
        self.write('docs/domain/index.yaml', yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'own.md', 'title': 'B own', 'load': 'bootstrap'}]}))
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        self.assertEqual(self.titles(), ['B own'])

    def test_lessons_of_a_list_follow_layers_then_directories(self):
        self.owner(kind='lessons', records=[('one.md', 'A lesson', {'tags': ['topic-x']})])
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        report = self.report('route', '--topics', 'topic-x')
        self.assertEqual([record['title'] for record in report['lessons']], ['A lesson'])


class RouteCoordinates(KnowledgeListFixture):
    """Record `paths` are written in the coordinates of the owner (D2)."""

    def route_titles(self, *files):
        for name in files:
            self.write(name, 'x')
        return self.titles('--files', *files)

    def test_sibling_repository_record_matches_prefixed_paths(self):
        self.owner(records=[('b-code.md', 'matches B', {'paths': ['project/src/**']}),
                            ('a-code.md', 'matches A', {'paths': ['src/**']})])
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        self.assertEqual(self.route_titles('src/x.py'), ['matches B'])

    def test_file_outside_the_pattern_is_not_matched_and_never_blocked(self):
        self.owner(records=[('b-code.md', 'matches B', {'paths': ['project/src/**']})])
        self.setting(domain='../repo-a/docs/domain')
        self.assertEqual(self.route_titles('lib/y.py'), [])
        report = self.report('route', '--files', 'lib/y.py')
        self.assertEqual(report['status'], 'NOT_RUN')

    def test_string_form_pointing_at_a_sibling_repository_no_longer_blocks(self):
        self.owner(records=[('b-code.md', 'matches B', {'paths': ['project/src/**']})])
        self.setting(domain='../repo-a/docs/domain')
        self.assertEqual(self.route_titles('src/x.py'), ['matches B'])

    def test_shared_parent_keeps_the_project_name_prefix(self):
        shared = self.base / 'docs/domain'
        shared.mkdir(parents=True)
        (shared / 'r.md').write_text('---\ntags: [x]\n---\nt')
        (shared / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'r.md', 'title': 'shared', 'paths': ['project/src/**']}]}))
        for value in ('../docs/domain', ['../docs/domain']):
            with self.subTest(value=value):
                self.setting(domain=value)
                self.assertEqual(self.route_titles('src/x.py'), ['shared'])

    def test_base_nested_in_the_project_prefixes_files_outside_it(self):
        nested = self.root / 'apps/x/docs/domain'
        nested.mkdir(parents=True)
        (nested / 'r.md').write_text('---\ntags: [x]\n---\nt')
        (nested / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
            {'path': 'r.md', 'title': 'inside', 'paths': ['src/**']},
            {'path': 'r.md', 'title': 'outside', 'paths': ['project/lib/**']}]}))
        self.setting(domain='apps/x/docs/domain')
        self.assertEqual(self.route_titles('apps/x/src/a.py'), ['inside'])
        self.assertEqual(self.titles('--files', 'lib/b.py'), ['outside'])

    def test_prefix_is_case_sensitive(self):
        self.owner(records=[('b-code.md', 'matches B', {'paths': ['Project/src/**']})])
        self.setting(domain='../repo-a/docs/domain')
        self.assertEqual(self.route_titles('src/x.py'), [])

    def test_lessons_text_form_keeps_project_coordinates_and_list_form_uses_owner_coordinates(self):
        owner = self.owner(kind='lessons', records=[('one.md', 'A lesson', {'paths': ['src/**']}),
                                                    ('two.md', 'B-coordinates', {'paths': ['project/src/**']})])
        self.write('src/x.py', 'x')
        self.setting(lessons='../repo-a/docs/lessons')
        self.assertEqual([r['title'] for r in self.report('route', '--files', 'src/x.py')['lessons']], ['A lesson'])
        self.setting(lessons=['../repo-a/docs/lessons'])
        self.assertEqual([r['title'] for r in self.report('route', '--files', 'src/x.py')['lessons']], ['B-coordinates'])
        self.assertTrue(owner.is_dir())


class DoctorMessages(KnowledgeListFixture):
    def findings(self, text):
        self.manifest['skills'] = ['feature']
        self.save()
        result = self.cli('doctor')  # exit code ignored: the unlinked fixture has unrelated errors
        self.assertNotIn('Traceback', result.stderr)
        return [f for f in __import__('json').loads(result.stdout)['findings'] if text in f['reason']]

    def test_missing_index_is_reported_per_element_with_directory_state(self):
        (self.base / 'repo-a/docs/domain').mkdir(parents=True)
        self.setting(domain=['docs/domain', '../repo-a/docs/domain', '../repo-c/docs/domain'])
        reasons = [f['reason'] for f in self.findings('Missing domain knowledge index')]
        self.assertEqual(len(reasons), 3, reasons)
        self.assertIn('the directory does not exist (is the repository that owns it checked out?)', reasons[0])
        self.assertIn('the directory exists;', reasons[1])
        self.assertTrue(all(f['level'] == 'warning' for f in self.findings('Missing domain knowledge index')))
        self.assertEqual(len(self.findings('Missing lessons index')), 3)

    def test_string_form_keeps_the_single_message(self):
        reasons = [f['reason'] for f in self.findings('Missing domain knowledge index')]
        self.assertEqual(reasons, ['Missing domain knowledge index; create it when the first verified domain record is written'])

    def test_listed_directories_are_not_scattered_knowledge(self):
        self.owner()
        (self.root / 'backend/docs/domain').mkdir(parents=True)
        (self.root / 'backend/docs/domain/index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': []}))
        self.setting(domain=['docs/domain', 'backend/docs/domain', '../repo-a/docs/domain'])
        self.assertEqual(self.findings('Scattered domain knowledge'), [])
        self.setting(domain=['docs/domain', '../repo-a/docs/domain'])
        self.assertEqual(len(self.findings('Scattered domain knowledge')), 1)

    def test_bootstrap_sum_over_limit_warns_with_directories(self):
        big = 'x' * 14000
        for name in ('repo-a', 'repo-d'):
            directory = self.base / name / 'docs/domain'
            directory.mkdir(parents=True)
            (directory / 'g.md').write_text('---\ntags: [x]\n---\n' + big)
            (directory / 'index.yaml').write_text(yaml.safe_dump({'version': 1, 'entries': [
                {'path': 'g.md', 'title': 'G', 'load': 'bootstrap'}]}))
        self.setting(domain=['../repo-a/docs/domain', '../repo-d/docs/domain'])
        found = self.findings('Bootstrap domain records total')
        self.assertEqual(len(found), 1)
        self.assertIn('repo-a', found[0]['reason'])
        self.assertIn('repo-d', found[0]['reason'])
        self.setting(domain=['../repo-a/docs/domain'])
        self.assertEqual(self.findings('Bootstrap domain records total'), [])


class KnowledgeInitList(KnowledgeListFixture):
    def init(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), 'knowledge', 'init', str(self.root), *args, '--json'],
                                capture_output=True, text=True, timeout=20, cwd=self.root)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)

    def tree(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))

    def test_list_writes_inside_the_project_and_skips_foreign_directories(self):
        self.setting(domain=['docs/domain', '../repo-a/docs/domain', '../docs/domain'])
        report = self.init()
        self.assertEqual(sorted(Path(p).relative_to(self.root).as_posix() for p in report['created']),
                         ['docs/README.md', 'docs/domain/index.yaml', 'docs/lessons/index.yaml'])
        skipped = {Path(item['path']).relative_to(self.base).as_posix(): item['reason'] for item in report['skipped']}
        self.assertEqual(sorted(skipped), ['docs/domain/index.yaml', 'docs/lessons/index.yaml',
                                           'repo-a/docs/domain/index.yaml', 'repo-a/docs/lessons/index.yaml'])
        self.assertTrue(all('owned by another repository' in reason for reason in skipped.values()))
        self.assertFalse((self.base / 'repo-a').exists())
        self.assertFalse((self.base / 'docs').exists())

    def test_list_without_any_directory_in_the_project_creates_nothing(self):
        self.setting(domain=['../repo-a/docs/domain'])
        before = self.tree()
        report = self.init()
        self.assertEqual(report['created'], [])
        self.assertEqual(len(report['skipped']), 2)
        self.assertEqual(self.tree(), before)

    def test_string_form_is_unchanged_for_a_shared_parent(self):
        self.setting(domain='../docs/domain')
        report = self.init()
        self.assertEqual(report['skipped'], [])
        self.assertTrue((self.base / 'docs/domain/index.yaml').is_file())
        self.assertTrue((self.base / 'docs/README.md').is_file())

    def test_invalid_list_creates_nothing(self):
        for value in ([], ['docs/domain', 'notes'], ['docs/domain', '../../x/docs/domain'], ['docs/domain', 'docs/domain']):
            with self.subTest(value=value):
                self.setting(domain=value)
                before = self.tree()
                report = self.init(code=2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertEqual(self.tree(), before)

    def test_dry_run_creates_nothing_for_a_list(self):
        self.setting(domain=['docs/domain'])
        before = self.tree()
        report = self.init('--dry-run')
        self.assertEqual(len(report['created']), 3)
        self.assertEqual(self.tree(), before)


class WindowsCoordinates(unittest.TestCase):
    """Pure-path table (runs on every OS): separators in the result are `/`, the prefix is the directory name."""

    def test_table(self):
        from pathlib import PureWindowsPath as W
        from skillsync_workflow.quality import knowledge_coordinates
        project = W('C:/work/repo-b')
        cases = [
            (W('C:/work/repo-a'), ['src/x.py', 'lib/a/b.py'], ['repo-b/src/x.py', 'repo-b/lib/a/b.py']),
            (W('C:/work'), ['src/x.py'], ['repo-b/src/x.py']),
            (W('C:/WORK'), ['src/x.py'], ['repo-b/src/x.py']),
            (W('C:/work/repo-b'), ['src/x.py'], ['src/x.py']),
            (W('C:/work/repo-b/apps/x'), ['apps/x/src/a.py', 'lib/b.py'], ['src/a.py', 'repo-b/lib/b.py']),
            (None, ['src/x.py'], ['src/x.py']),
        ]
        for base, files, expected in cases:
            with self.subTest(base=base):
                result = knowledge_coordinates(project, base, files)
                self.assertEqual(result, expected)
                self.assertFalse(any('\\' in item for item in result))


if __name__ == '__main__':
    unittest.main()
