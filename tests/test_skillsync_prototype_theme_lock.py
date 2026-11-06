"""Static theme lock (`color-scheme`, `darkreader-lock`) added to index.html by `prototype migrate` (F1-fix)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype  # noqa: E402
from tests.test_skillsync_prototype_cli import V1Fixture  # noqa: E402

HEAD = ('<!doctype html>\n<html lang="pl">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n<title>T</title>\n</head>\n'
        '<body><p>x</p></body>\n</html>\n')
COLOR = '<meta name="color-scheme" content="light dark" />'
LOCK = '<meta name="darkreader-lock" />'


def head_of(html):
    return html[html.index('<head>'):html.index('</head>')]


class EnsureThemeLockMetasTests(unittest.TestCase):
    def test_inserts_both_after_viewport(self):
        html, added = prototype.ensure_theme_lock_metas(HEAD)
        self.assertEqual(added, ['color-scheme', 'darkreader-lock'])
        self.assertIn('initial-scale=1" />\n' + COLOR + '\n' + LOCK + '\n<title>', html)
        self.assertEqual(html.replace(COLOR + '\n', '').replace(LOCK + '\n', ''), HEAD, 'nothing else changes')

    def test_idempotent(self):
        once, _ = prototype.ensure_theme_lock_metas(HEAD)
        twice, added = prototype.ensure_theme_lock_metas(once)
        self.assertEqual(twice, once)
        self.assertEqual(added, [])

    def test_adds_only_the_missing_one_and_keeps_existing_spelling(self):
        source = HEAD.replace('<title>', "<META NAME='color-scheme' content='dark'>\n<title>")
        html, added = prototype.ensure_theme_lock_metas(source)
        self.assertEqual(added, ['darkreader-lock'])
        self.assertIn("<META NAME='color-scheme' content='dark'>", html)
        self.assertEqual(head_of(html).lower().count('color-scheme'), 1)

    def test_meta_outside_head_does_not_count(self):
        source = HEAD.replace('<p>x</p>', '<p>x</p>' + LOCK)
        _, added = prototype.ensure_theme_lock_metas(source)
        self.assertEqual(added, ['color-scheme', 'darkreader-lock'])

    def test_similar_names_do_not_count(self):
        source = HEAD.replace('<title>', '<meta name="darkreader-lock-x" />\n<meta name="color-scheme-old" />\n<title>')
        _, added = prototype.ensure_theme_lock_metas(source)
        self.assertEqual(added, ['color-scheme', 'darkreader-lock'])

    def test_falls_back_to_charset_then_head(self):
        no_viewport = HEAD.replace('<meta name="viewport" content="width=device-width, initial-scale=1" />\n', '')
        html, _ = prototype.ensure_theme_lock_metas(no_viewport)
        self.assertIn('<meta charset="utf-8" />\n' + COLOR, html)
        bare = '<html><head><title>T</title></head><body></body></html>'
        html, _ = prototype.ensure_theme_lock_metas(bare)
        self.assertTrue(html.startswith('<html><head>\n' + COLOR + '\n' + LOCK + '<title>'), html)

    def test_crlf_kept(self):
        html, _ = prototype.ensure_theme_lock_metas(HEAD.replace('\n', '\r\n'))
        self.assertIn('\r\n' + COLOR + '\r\n' + LOCK + '\r\n', html)
        self.assertNotIn('\n<meta name="darkreader', html.replace('\r\n', ''))

    def test_without_head_nothing_changes(self):
        html, added = prototype.ensure_theme_lock_metas('<p>fragment</p>')
        self.assertEqual(html, '<p>fragment</p>')
        self.assertIsNone(added)


class MigrateThemeLockTests(V1Fixture):
    def test_migrate_adds_static_metas_once(self):
        before = self.read('stary', 'index.html')
        self.assertNotIn('darkreader-lock', before, 'fixture precondition: v1 page without the lock')
        report = self.migrate()
        html = self.read('stary', 'index.html')
        self.assertEqual(head_of(html).count('name="darkreader-lock"'), 1)
        self.assertEqual(head_of(html).count('name="color-scheme"'), 1)
        steps = [step for step in report['steps'] if step['action'] == 'theme_lock_meta']
        self.assertEqual([step['added'] for step in steps], [['color-scheme', 'darkreader-lock']])
        again = self.migrate()
        self.assertEqual(self.read('stary', 'index.html'), html, 'second run is a no-op')
        self.assertFalse([step for step in again['steps'] if step['action'] == 'theme_lock_meta'])
        self.assertEqual(again['result'], 'already_migrated')

    def test_rerun_on_migrated_page_without_lock_adds_it(self):
        self.migrate()
        path = self.proto_dir('stary') / 'index.html'
        stripped = path.read_text(encoding='utf-8').replace(COLOR + '\n', '').replace(LOCK + '\n', '')
        path.write_text(stripped, encoding='utf-8')
        report = self.migrate()
        self.assertEqual(report['result'], 'migrated')
        self.assertIn(LOCK, head_of(path.read_text(encoding='utf-8')))

    def test_dry_run_writes_nothing(self):
        self.migrate()
        path = self.proto_dir('stary') / 'index.html'
        stripped = path.read_text(encoding='utf-8').replace(LOCK + '\n', '')
        path.write_text(stripped, encoding='utf-8')
        report = self.migrate('--dry-run')
        self.assertIn('theme_lock_meta', [step['action'] for step in report['steps']])
        self.assertEqual(path.read_text(encoding='utf-8'), stripped)


if __name__ == '__main__':
    unittest.main()
