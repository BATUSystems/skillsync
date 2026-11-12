"""`skillsync prototype` v2 (shared comments): ids, migrate, list/index fields, user, serve refusal.

Contract: docs/specs/2026-11-04-prototype-komentarze-wspolne.md (K10, K22, K30, K32, K33, K36, K41,
"Kontrakty", "Migracja", "Dane"). Resources: tests/fixtures/prototype/assets (v2 shape), the v1 prototype
tests/fixtures/prototype/v1 (comments.js with threads anchored by position, see the comments in its index.html).
"""
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_skillsync_prototype import (  # noqa: E402
    ASSETS, ASSIGNMENT, CLI, FIXTURES, PROTOTYPE_FILES, PrototypeFixture, tree)

V1 = FIXTURES / 'v1'
POSIX_ONLY = unittest.skipUnless(os.name != 'nt', 'file modes and signals are POSIX-specific')
SECRET = 'Sekret-Haslo-9917'
ENGINE_FILES = ('prototype.js', 'prototype.css', 'components.css', 'screens.css')


def v2_log(slug, operations):
    """Canonical comments.json text: header line, one operation per line, `]}` and a newline."""
    lines = ',\n'.join(json.dumps(op, ensure_ascii=False, separators=(',', ':')) for op in operations)
    return ('{"format":"skillsync-prototype-comments","version":2,"prototypeId":"%s","operations":[\n' % slug
            + (lines + '\n' if lines else '') + ']}\n')


def v2_create(number, thread, at, screen='s1', proto_id='s1-button-1', text='uwaga'):
    return {'id': f'op-{number:04d}', 'type': 'create', 'threadId': thread, 'at': at, 'by': 'ala@example.pl',
            'payload': {'message': {'id': f'm-{number:04d}', 'text': text}, 'screen': screen,
                        'anchor': {'kind': 'element', 'protoId': proto_id, 'x': 0.5, 'y': 0.5, 'iteration': 1},
                        'label': 'Przycisk'}}


def v1_ops_of(text):
    return json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])['operations']


def v1_operations():
    text = (V1 / 'comments.js').read_text(encoding='utf-8')
    return json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])['operations']


# ---------------------------------------------------------------------------
# K22, K36: ids and the fresh v2 prototype
# ---------------------------------------------------------------------------
IDS_HTML = """<!doctype html>
<html lang="pl" data-prototype-id="demo"><body>
<section class="screen" id="s1" data-prototype-example>
  <div class="frame"><button type="button">Przykład bez id</button></div>
</section>
<section class="screen" id="s2">
  <div class="frame">
    <h1>Tytuł</h1>
    <button type="button">Pierwszy</button>
    <button type="button" data-proto-id="s2-button-1">Drugi</button>
    <button type="button">Trzeci</button>
    <a href="#s3">Dalej</a>
    <span>nie jest komentowalny</span>
  </div>
</section>
<section class="screen" id="s3">
  <div class="frame"><form><input type="text"></form></div>
</section>
</body></html>
"""


class IdsTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')

    def html(self, text):
        (self.proto_dir() / 'index.html').write_text(text, encoding='utf-8', newline='')

    def current(self):
        return (self.proto_dir() / 'index.html').read_bytes()

    def test_fresh_v2_prototype_has_the_v2_files_and_passes_ids_check(self):
        # K36: no comments.js, comments.json + .gitignore + .gitattributes, `ids --check` PASS
        self.assertEqual(set(os.listdir(self.proto_dir())), PROTOTYPE_FILES)
        self.assertFalse(os.path.lexists(self.proto_dir() / 'comments.js'))
        self.assertNotIn('comments.js', self.read('demo', 'index.html'))
        report = self.proto('ids', 'demo')
        self.assertEqual((report['status'], report['missing'], report['duplicates'], report['invalid']),
                         ('PASS', [], [], []))
        self.assertEqual(self.proto('ids', 'demo', '--check')['status'], 'PASS')

    def test_template_ignore_rules_and_merge_driver_are_copied_from_the_assets(self):
        for name in ('.gitignore', '.gitattributes', 'comments.json'):
            expected = (ASSETS / 'repo' / name).read_text(encoding='utf-8').replace('{{SLUG}}', 'demo')
            self.assertEqual(self.read('demo', name), expected, name)
        self.assertIn('comments.json merge=skillsync-comments', self.read('demo', '.gitattributes'))
        self.assertIn('comments.json.lock', self.read('demo', '.gitignore'))

    @unittest.skipUnless(os.environ.get('SKILLSYNC_TEST_LIBRARY'),
                         'set SKILLSYNC_TEST_LIBRARY to a v2 library to test its real prototype template')
    def test_fresh_prototype_from_the_real_library_template_passes_ids_check(self):
        assets = Path(os.environ['SKILLSYNC_TEST_LIBRARY']) / 'skills' / 'prototype' / 'assets'
        self.proto('init', 'real', '--requirements', 'docs/req.md', '--assets', str(assets), assets=False)
        self.assertFalse(os.path.lexists(self.proto_dir('real') / 'comments.js'))
        self.assertEqual(self.proto('ids', 'real')['status'], 'PASS')

    @unittest.skipUnless(os.environ.get('SKILLSYNC_TEST_LIBRARY'),
                         'set SKILLSYNC_TEST_LIBRARY to a v2 library to test its real prototype template')
    def test_migrate_of_a_fresh_prototype_from_the_real_library_leaves_the_icons_unchanged(self):
        """The template carries the whole library sprite: `migrate` right after `init` replaces and adds no icon."""
        assets = Path(os.environ['SKILLSYNC_TEST_LIBRARY']) / 'skills' / 'prototype' / 'assets'
        self.proto('init', 'real', '--requirements', 'docs/req.md', '--assets', str(assets), assets=False)
        report = self.proto('migrate', 'real', '--dry-run', '--assets', str(assets), assets=False)
        self.assertEqual((report['sprite']['status'], report['sprite']['replaced'], report['sprite']['added']),
                         ('unchanged', [], []))
        self.assertNotIn('sprite_merge', [step['action'] for step in report['steps']])

    def test_example_screen_is_skipped_by_check(self):
        self.html(IDS_HTML.replace('<button type="button">Pierwszy</button>', '')
                  .replace('<button type="button">Trzeci</button>', '')
                  .replace('<h1>Tytuł</h1>', '<h1 data-proto-id="s2-h1-1">Tytuł</h1>')
                  .replace('<a href="#s3">', '<a href="#s3" data-proto-id="s2-a-1">')
                  .replace('<form><input type="text"></form>',
                           '<form data-proto-id="s3-form-1"><input type="text" data-proto-id="s3-input-1"></form>'))
        report = self.proto('ids', 'demo')
        self.assertEqual((report['status'], report['missing']), ('PASS', []))

    def test_missing_id_on_a_commentable_element_fails_with_exit_1(self):
        self.html(IDS_HTML)
        report = self.proto('ids', 'demo', '--check', code=1)
        self.assertEqual(report['status'], 'FAIL')
        found = {(item['screen'], item['tag']) for item in report['missing']}
        self.assertEqual(found, {('s2', 'h1'), ('s2', 'button'), ('s2', 'a'), ('s3', 'form'), ('s3', 'input')})
        self.assertEqual(len([item for item in report['missing'] if item['tag'] == 'button']), 2)
        self.assertNotIn('s1', {item['screen'] for item in report['missing']}, 'the example screen is skipped')
        self.assertNotIn('span', {item['tag'] for item in report['missing']})

    def test_duplicated_id_fails_with_exit_1(self):
        clean = IDS_HTML.replace('<button type="button">Pierwszy</button>', '').replace(
            '<button type="button">Trzeci</button>', '')
        self.html(clean)
        self.proto('ids', 'demo', '--fix')
        self.assertEqual(self.proto('ids', 'demo')['status'], 'PASS', 'control: the same file without the duplicate passes')
        self.html(self.current().decode('utf-8').replace('data-proto-id="s2-a-1"', 'data-proto-id="s2-button-1"'))
        report = self.proto('ids', 'demo', code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('s2-button-1', json.dumps(report['duplicates']))

    def test_invalid_id_fails_with_exit_1(self):
        self.html(IDS_HTML.replace('<h1>', '<h1 data-proto-id="Zły ID">'))
        report = self.proto('ids', 'demo', code=1)
        self.assertIn('Zły ID', json.dumps(report['invalid'], ensure_ascii=False))

    def test_fix_assigns_screen_tag_counter_ids_in_document_order_and_keeps_existing(self):
        self.html(IDS_HTML)
        report = self.proto('ids', 'demo', '--fix')
        self.assertEqual(report['status'], 'PASS')
        fixed = self.current().decode('utf-8')
        self.assertIn('<h1 data-proto-id="s2-h1-1">Tytuł</h1>', fixed)
        self.assertIn('data-proto-id="s2-button-1">Drugi<', fixed, 'the existing id is untouched')
        # `Pierwszy` precedes the taken s2-button-1: the taken value is skipped, order stays the document order
        self.assertRegex(fixed, r'<button[^>]*data-proto-id="s2-button-2"[^>]*>Pierwszy<')
        self.assertRegex(fixed, r'<button[^>]*data-proto-id="s2-button-3"[^>]*>Trzeci<')
        self.assertRegex(fixed, r'<a [^>]*data-proto-id="s2-a-1"[^>]*>Dalej<')
        self.assertIn('data-proto-id="s3-form-1"', fixed)
        self.assertIn('data-proto-id="s3-input-1"', fixed)
        self.assertNotRegex(fixed, r'<span[^>]*data-proto-id')
        self.assertIn('<button type="button">Przykład bez id</button>', fixed, 'the example screen is untouched')
        self.assertEqual(self.proto('ids', 'demo', '--check')['status'], 'PASS')

    def test_fix_is_deterministic_and_idempotent(self):
        self.html(IDS_HTML)
        self.proto('ids', 'demo', '--fix')
        first = self.current()
        self.html(IDS_HTML)
        self.proto('ids', 'demo', '--fix')
        self.assertEqual(self.current(), first, 'the same input gives the same ids')
        before = tree(self.proto_dir())
        report = self.proto('ids', 'demo', '--fix')
        self.assertEqual((report['status'], report['added']), ('PASS', []))
        self.assertEqual(tree(self.proto_dir()), before, 'a second --fix writes nothing')

    def test_fix_keeps_line_endings_of_the_file(self):
        self.html(IDS_HTML.replace('\n', '\r\n'))
        self.proto('ids', 'demo', '--fix')
        data = self.current()
        self.assertEqual(data.count(b'\n'), data.count(b'\r\n'), 'CRLF file stays CRLF')

    def test_fix_never_renames_existing_ids_even_when_they_are_duplicated(self):
        html = IDS_HTML.replace('<h1>', '<h1 data-proto-id="s2-button-1">')
        self.html(html)
        report = self.proto('ids', 'demo', '--fix')
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(report.get('warnings'), 'remaining duplicates are reported, not rewritten')
        self.assertEqual(self.current().decode('utf-8').count('data-proto-id="s2-button-1"'), 2)
        self.proto('ids', 'demo', '--check', code=1)

    def test_check_and_fix_exclude_each_other(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'ids', str(self.root), 'demo', '--check', '--fix'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_unknown_slug_is_blocked(self):
        self.blocked('ids', 'brak')
        self.blocked('ids', 'Zly Slug')


# ---------------------------------------------------------------------------
# K30, K33: migrate
# ---------------------------------------------------------------------------
class V1Fixture(PrototypeFixture):
    """Project with the v1 prototype `stary` (docs/prototypes/stary) next to a v2 assets directory."""

    def setUp(self):
        super().setUp()
        shutil.copytree(V1, self.proto_dir('stary'))

    def migrate(self, *args, code=0, slug='stary'):
        return self.proto('migrate', slug, '--assets', str(self.assets), *args, code=code)

    def operations(self, slug='stary'):
        text = (self.proto_dir(slug) / 'comments.json').read_text(encoding='utf-8')
        lines = text.split('\n')
        self.assertEqual(lines[0], '{"format":"skillsync-prototype-comments","version":2,"prototypeId":"%s","operations":[' % slug)
        self.assertEqual(lines[-2:], [']}', ''])
        operations = [json.loads(line.rstrip(',')) for line in lines[1:-2]]
        self.assertEqual(operations, json.loads(text)['operations'], 'one operation per line')
        return operations

    def creates(self, slug='stary'):
        return {op['threadId']: op for op in self.operations(slug) if op['type'] == 'create'}


class MigrateTests(V1Fixture):
    def test_s2_1_migrate_waits_for_the_prototype_lock_and_writes_nothing(self):
        from skillsync_workflow import prototype_server as ps
        directory = self.proto_dir('stary')
        before = tree(directory)
        with ps.FileLock(str(directory / 'comments.json.lock')):
            report = self.migrate(code=2)
            self.assertIn('lock', report['reason'])
            after = tree(directory)
            after.pop('comments.json.lock', None)
            self.assertEqual(after, before, 'index.html and the rest stay as they were')
        self.assertEqual(self.migrate()['result'], 'migrated')   # control: a free lock

    def test_s2_1_replace_index_refuses_a_file_changed_since_it_was_read(self):
        from skillsync_workflow import prototype as generator
        directory = self.proto_dir('stary')
        original = (directory / 'index.html').read_bytes()
        with self.assertRaises(generator.PrototypeError) as caught:
            generator._replace_index(directory, 'not what is on disk', 'new text')
        self.assertIn('changed during migrate', str(caught.exception))
        self.assertEqual((directory / 'index.html').read_bytes(), original)
        text = original.decode('utf-8')
        generator._replace_index(directory, text, text + '<!-- ok -->')   # control: the expected text is replaced
        self.assertTrue((directory / 'index.html').read_text(encoding='utf-8').endswith('<!-- ok -->'))

    def test_operations_keep_ids_and_authors_become_imported_author(self):
        v1 = v1_operations()
        self.migrate()
        v2 = self.operations()
        self.assertEqual([op['id'] for op in v2], [op['id'] for op in v1])
        self.assertEqual([op['threadId'] for op in v2], [op['threadId'] for op in v1])
        self.assertEqual([op['at'] for op in v2], [op['at'] for op in v1])
        self.assertEqual({op['by'] for op in v2}, {'import'})
        for old, new in zip(v1, v2):
            self.assertEqual(old['type'], new['type'])
            message = old['payload'].get('message')
            if message:
                self.assertEqual(new['payload']['message']['id'], message['id'])
                self.assertEqual(new['payload']['message']['text'], message['text'])
                self.assertEqual(new['payload']['message']['importedAuthor'], message['author'])
                self.assertNotIn('author', new['payload']['message'])

    def test_resolve_without_a_reason_in_v1_becomes_done(self):
        self.migrate()
        resolves = [op for op in self.operations() if op['type'] == 'resolve']
        self.assertEqual([op['payload'] for op in resolves], [{'reason': 'done'}])

    def test_unambiguous_anchors_point_to_the_nearest_data_proto_id(self):
        self.migrate()
        creates = self.creates()
        html = self.read('stary', 'index.html')
        direct, via_ancestor = creates['th-a']['payload']['anchor'], creates['th-b']['payload']['anchor']
        for anchor in (direct, via_ancestor):
            self.assertEqual(anchor['kind'], 'element')
        self.assertEqual(direct['protoId'], via_ancestor['protoId'], 'the span inside the button resolves to the button')
        self.assertRegex(html, r'<button[^>]*data-proto-id="%s"' % re.escape(direct['protoId']))
        self.assertNotIn('hint', creates['th-a']['payload'])

    def test_ambiguous_anchors_become_lost_with_the_legacy_path(self):
        self.migrate()
        creates = self.creates()
        v1 = {op['threadId']: op['payload'] for op in v1_operations() if op['type'] == 'create'}
        cases = {'th-c': 'table/tr without tbody', 'th-d': 'li without end tags', 'th-e': 'p without end tags',
                 'th-g': 'nesting error'}
        for thread, why in cases.items():
            with self.subTest(thread=thread, why=why):
                payload = creates[thread]['payload']
                self.assertEqual(payload['anchor'], {'kind': 'lost'}, why)
                self.assertEqual(payload['hint']['legacyPath'], v1[thread]['anchor'])
                self.assertEqual(payload['hint']['text'], v1[thread]['label'])
                self.assertEqual(payload['screen'], v1[thread]['screen'])

    def test_empty_anchor_becomes_a_screen_anchor(self):
        self.migrate()
        payload = self.creates()['th-f']['payload']
        self.assertEqual(payload['anchor']['kind'], 'screen')
        self.assertNotIn('protoId', payload['anchor'])
        self.assertNotIn('hint', payload)

    def test_step_reports_count_anchor_kinds(self):
        report = self.migrate()
        convert = [step for step in report['steps'] if step['action'] == 'convert'][0]
        self.assertEqual(convert['anchors'], {'element': 3, 'screen': 1, 'lost': 4})
        self.assertEqual(report['result'], 'migrated')

    def test_ids_are_added_before_the_anchors_are_resolved(self):
        self.proto('ids', 'stary', code=1)
        self.migrate()
        self.assertEqual(self.proto('ids', 'stary')['status'], 'PASS')

    def test_engine_v2_is_copied_and_comments_js_with_its_script_is_removed(self):
        self.assertNotEqual((self.proto_dir('stary') / 'prototype.js').read_bytes(),
                            (ASSETS / 'repo' / 'prototype.js').read_bytes(), 'control: v1 engine differs before migrate')
        self.migrate()
        for name in ENGINE_FILES:
            self.assertEqual((self.proto_dir('stary') / name).read_bytes(), (ASSETS / 'repo' / name).read_bytes(), name)
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))
        html = self.read('stary', 'index.html')
        self.assertNotIn('comments.js', html)
        self.assertIn('<script src="prototype.js"></script>', html, 'the engine script stays')
        self.assertIn('comments.json.lock', self.read('stary', '.gitignore'))
        self.assertIn('*.tmp', self.read('stary', '.gitignore'))
        self.assertIn('comments.json merge=skillsync-comments', self.read('stary', '.gitattributes'))

    def test_script_reference_variants_are_removed(self):
        for variant in ("<script src='comments.js'></script>", '<script defer src="comments.js"></script>',
                        '<script src="comments.js" defer></script>'):
            with self.subTest(variant=variant):
                directory = self.proto_dir('stary')
                if directory.exists():
                    shutil.rmtree(directory)
                shutil.copytree(V1, directory)
                html = (directory / 'index.html').read_text(encoding='utf-8')
                (directory / 'index.html').write_text(html.replace('<script src="comments.js"></script>', variant),
                                                      encoding='utf-8')
                self.migrate()
                self.assertNotIn('comments.js', self.read('stary', 'index.html'))
                self.assertIn('<script src="prototype.js"></script>', self.read('stary', 'index.html'))

    def test_second_run_is_already_migrated_and_changes_nothing(self):
        self.migrate()
        before = tree(self.proto_dir('stary'))
        report = self.migrate()
        self.assertEqual((report['result'], report['files']), ('already_migrated', []))
        self.assertEqual(tree(self.proto_dir('stary')), before)

    def test_dry_run_writes_nothing_and_reports_the_same_steps(self):
        before = tree(self.proto_dir('stary'))
        dry = self.migrate('--dry-run')
        self.assertTrue(dry['dryRun'])
        self.assertEqual(tree(self.proto_dir('stary')), before, 'dry run leaves the directory untouched')
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.json'))
        real = self.migrate()
        self.assertEqual([(step['step'], step['action']) for step in dry['steps']],
                         [(step['step'], step['action']) for step in real['steps']])
        self.assertEqual(dry['files'], real['files'])
        self.assertTrue(os.path.lexists(self.proto_dir('stary') / 'comments.json'))

    def test_interrupted_migration_with_both_logs_keeps_comments_json_and_finishes_the_cleanup(self):
        self.migrate()
        kept = (self.proto_dir('stary') / 'comments.json').read_bytes()
        shutil.copyfile(V1 / 'comments.js', self.proto_dir('stary') / 'comments.js')
        report = self.migrate()
        self.assertEqual((self.proto_dir('stary') / 'comments.json').read_bytes(), kept, 'steps 1-4 are skipped')
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))
        self.assertIn('docs/prototypes/stary/comments.js', report['files'])

    def test_v2_prototype_from_init_is_already_migrated(self):
        self.init('demo')
        before = tree(self.proto_dir('demo'))
        report = self.migrate(slug='demo')
        self.assertEqual((report['result'], report['files']), ('already_migrated', []))
        self.assertEqual(tree(self.proto_dir('demo')), before)

    def test_unreadable_comments_js_blocks_without_changes(self):
        (self.proto_dir('stary') / 'comments.js').write_text('window.x = 1;', encoding='utf-8')
        before = tree(self.proto_dir('stary'))
        self.migrate(code=2)
        self.assertEqual(tree(self.proto_dir('stary')), before)

    def v1_with_unholdable_operation(self):
        text = (V1 / 'comments.js').read_text(encoding='utf-8')
        document = json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])
        document['operations'].append({'id': 'op-0099', 'type': 'create', 'threadId': 'th-z',
                                       'at': '2026-10-03T09:00:00.000Z',
                                       'payload': {'screen': 's2', 'anchor': '', 'label': 'x',
                                                   'message': {'author': 'Ala', 'text': 'bez identyfikatora'}}})
        (self.proto_dir('stary') / 'comments.js').write_text(ASSIGNMENT + json.dumps(document) + ';\n', encoding='utf-8')

    def test_operations_that_v2_cannot_hold_block_the_migration_without_any_write(self):
        self.v1_with_unholdable_operation()
        before = tree(self.proto_dir('stary'))
        report = self.migrate(code=2)
        self.assertEqual([item['id'] for item in report['skipped']], ['op-0099'])
        self.assertTrue(report['skipped'][0]['reason'])
        self.assertEqual(tree(self.proto_dir('stary')), before, 'BLOCKED leaves every file byte for byte')
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.json'))
        self.migrate(code=2)  # the second run is blocked the same way
        self.assertEqual(tree(self.proto_dir('stary')), before)

    def test_allow_skipped_migrates_the_rest_with_a_warning_and_keeps_comments_v1_js(self):
        self.v1_with_unholdable_operation()
        original = (self.proto_dir('stary') / 'comments.js').read_bytes()
        report = self.migrate('--allow-skipped')
        self.assertEqual([item['id'] for item in report['skipped']], ['op-0099'])
        self.assertTrue(report['warnings'])
        self.assertNotIn('op-0099', [op['id'] for op in self.operations()])
        self.assertEqual(len(self.operations()), len(v1_operations()))
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))
        self.assertEqual((self.proto_dir('stary') / 'comments.v1.js').read_bytes(), original,
                         'the skipped operation stays recoverable in comments.v1.js')

    def test_real_v1_engine_export_migrates_every_operation_with_the_same_ids(self):
        """B1: the v1 engine issues `thread-…`/`msg-…` ids (tests/fixtures/prototype/export-comments.js)."""
        # the export of the engine names its prototype; the fixture's `demo-a` becomes this prototype's slug
        # (a log of another prototype is ignored by the v1 engine as well), everything else stays byte for byte
        text = (FIXTURES / 'export-comments.js').read_text(encoding='utf-8')
        self.assertIn('"prototypeId": "demo-a"', text)
        text = text.replace('"prototypeId": "demo-a"', '"prototypeId": "stary"')
        (self.proto_dir('stary') / 'comments.js').write_text(text, encoding='utf-8')
        v1 = json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])['operations']
        self.assertTrue(all(op['threadId'].startswith('thread-') for op in v1), 'control: not th-… ids')
        report = self.migrate()
        self.assertEqual(report.get('skipped', []), [])
        v2 = self.operations()
        self.assertEqual([op['id'] for op in v2], [op['id'] for op in v1])
        self.assertEqual([op['threadId'] for op in v2], [op['threadId'] for op in v1])
        self.assertEqual([op['type'] for op in v2], [op['type'] for op in v1])
        self.assertEqual([op['payload']['message']['id'] for op in v2 if op['type'] in ('create', 'reply')],
                         [op['payload']['message']['id'] for op in v1 if op['type'] in ('create', 'reply')])
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))
        # the fold of the result has the same threads and replies as the v1 fold
        from skillsync_workflow import prototype as engine
        threads_v1 = engine.fold_operations(v1)
        threads_v2 = engine.fold_operations(v2)
        self.assertTrue(threads_v1, 'control: the export has a live thread')
        self.assertEqual([t['id'] for t in threads_v2], [t['id'] for t in threads_v1])
        self.assertEqual([[m['id'] for m in t['messages']] for t in threads_v2],
                         [[m['id'] for m in t['messages']] for t in threads_v1])
        self.assertEqual([t['resolved'] for t in threads_v2], [t['resolved'] for t in threads_v1])
        self.assertEqual(engine.count_open_threads(text, 'stary'), 1)
        entry = [item for item in self.list()['prototypes'] if item['slug'] == 'stary'][0]
        self.assertEqual((entry['commentsFormat'], entry['comments']), (2, 1), 'the same open thread count as v1')

    def foreign_log(self, operations=True):
        text = (FIXTURES / 'export-comments.js').read_text(encoding='utf-8')
        self.assertIn('"prototypeId": "demo-a"', text)
        if not operations:
            text = ASSIGNMENT + json.dumps({'version': 1, 'prototypeId': 'demo-a', 'operations': []}) + ';\n'
        (self.proto_dir('stary') / 'comments.js').write_text(text, encoding='utf-8')
        return len(v1_ops_of(text))

    def test_foreign_prototype_id_with_operations_blocks_without_any_write(self):
        count = self.foreign_log()
        before = tree(self.proto_dir('stary'))
        report = self.migrate(code=2)
        self.assertTrue(report['reason'].startswith(f'prototypeId demo-a != stary, {count} operations'), report['reason'])
        self.assertEqual(tree(self.proto_dir('stary')), before)
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.json'))

    def test_foreign_prototype_id_with_allow_skipped_keeps_the_original_as_comments_v1_js(self):
        self.foreign_log()
        original = (self.proto_dir('stary') / 'comments.js').read_bytes()
        self.migrate('--allow-skipped')
        self.assertEqual((self.proto_dir('stary') / 'comments.v1.js').read_bytes(), original)
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))

    def test_foreign_prototype_id_with_an_empty_log_migrates_with_a_warning(self):
        self.foreign_log(operations=False)
        report = self.migrate()
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(any('demo-a' in w for w in report['warnings']), report['warnings'])
        self.assertEqual(self.operations(), [])

    # -- R2-1: a leftover comments.js next to an already migrated comments.json ---------------------
    def leftover_js(self, extra=None):
        text = (V1 / 'comments.js').read_text(encoding='utf-8')
        document = json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])
        if extra:
            document['operations'].append(extra)
        (self.proto_dir('stary') / 'comments.js').write_text(ASSIGNMENT + json.dumps(document) + ';\n', encoding='utf-8')

    ORPHAN = {'id': 'op-9002', 'type': 'reply', 'threadId': 'th-a', 'at': '2026-10-04T09:00:00.000Z',
              'payload': {'message': {'id': 'm-9002', 'author': 'Ala', 'text': 'tylko w comments.js'}}}

    def test_control_leftover_comments_js_with_the_same_operations_is_removed(self):
        self.migrate()
        self.leftover_js()
        report = self.migrate()
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))
        self.assertEqual(report.get('skipped', []), [])

    def test_leftover_comments_js_with_operations_missing_from_comments_json_blocks_without_write(self):
        self.migrate()
        self.leftover_js(self.ORPHAN)
        before = tree(self.proto_dir('stary'))
        report = self.migrate(code=2)
        self.assertIn('op-9002', [item['id'] for item in report['skipped']])
        self.assertEqual(tree(self.proto_dir('stary')), before, 'BLOCKED: sha256 of every file unchanged')
        self.assertNotIn('op-9002', [op['id'] for op in self.operations()])

    def test_leftover_comments_js_with_allow_skipped_is_kept_as_comments_v1_js(self):
        self.migrate()
        self.leftover_js(self.ORPHAN)
        original = (self.proto_dir('stary') / 'comments.js').read_bytes()
        self.migrate('--allow-skipped')
        self.assertEqual((self.proto_dir('stary') / 'comments.v1.js').read_bytes(), original)
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.js'))

    def test_list_warns_about_a_leftover_comments_js(self):
        self.migrate()
        entry_warnings = lambda: [w for w in self.list().get('warnings', []) if 'comments.js left next to' in w]
        self.assertEqual(entry_warnings(), [], 'control: no warning after a clean migration')
        self.leftover_js(self.ORPHAN)
        self.assertEqual(len(entry_warnings()), 1)
        self.assertTrue(entry_warnings()[0].startswith('docs/prototypes/stary: '))

    # -- R2-3: losses of the v1 normalization are reported ---------------------------------------
    def test_v1_log_that_normalization_would_drop_blocks_with_a_reason(self):
        text = (V1 / 'comments.js').read_text(encoding='utf-8')
        base = json.loads(text[text.index(ASSIGNMENT) + len(ASSIGNMENT):text.rindex(';')])
        no_thread = {k: v for k, v in base['operations'][1].items() if k != 'threadId'}
        no_thread['id'] = 'op-0500'
        repeated = dict(base['operations'][1])    # the same `id` as an earlier operation
        cases = {
            'version 2': dict(base, version=2),
            'operations is not a list': dict(base, operations={'a': 1}),
            'operation without threadId': dict(base, operations=base['operations'] + [no_thread]),
            'repeated id': dict(base, operations=base['operations'] + [repeated]),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                directory = self.proto_dir('stary')
                if directory.exists():
                    shutil.rmtree(directory)
                shutil.copytree(V1, directory)
                (directory / 'comments.js').write_text(ASSIGNMENT + json.dumps(document) + ';\n', encoding='utf-8')
                before = tree(directory)
                report = self.migrate(code=2)
                self.assertTrue(report['skipped'], report)
                self.assertTrue(all(item.get('reason') for item in report['skipped']), report['skipped'])
                self.assertEqual(tree(directory), before)

    # -- R2-4: an existing comments.v1.js blocks before any write ----------------------------------
    def test_existing_comments_v1_js_blocks_the_migration_before_any_write(self):
        """The backup would be overwritten only when a skipped operation makes `--allow-skipped` keep comments.js."""
        self.v1_with_unholdable_operation()
        (self.proto_dir('stary') / 'comments.v1.js').write_text('// earlier backup\n', encoding='utf-8')
        before = tree(self.proto_dir('stary'))
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'migrate', str(self.root), 'stary',
                                 '--assets', str(self.assets), '--allow-skipped', '--json'],
                                capture_output=True, text=True, timeout=60, cwd=self.base)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')
        self.assertIn('comments.v1.js', result.stdout + result.stderr)
        self.assertEqual(tree(self.proto_dir('stary')), before, 'sha256 of all files unchanged (no ids --fix, no comments.json)')
        self.assertFalse(os.path.lexists(self.proto_dir('stary') / 'comments.json'))
        os.unlink(self.proto_dir('stary') / 'comments.v1.js')
        self.migrate('--allow-skipped')   # control: without the backup the same prototype migrates

    # -- R2-8 ------------------------------------------------------------------------------------
    def test_script_reference_with_query_or_dot_slash_is_removed(self):
        for variant in ('<script src="comments.js?v=3"></script>', '<script src="./comments.js"></script>',
                        '<script src="./comments.js?v=1&x=2" defer></script>'):
            with self.subTest(variant=variant):
                directory = self.proto_dir('stary')
                if directory.exists():
                    shutil.rmtree(directory)
                shutil.copytree(V1, directory)
                html = (directory / 'index.html').read_text(encoding='utf-8')
                self.assertIn('<script src="comments.js"></script>', html)
                (directory / 'index.html').write_text(html.replace('<script src="comments.js"></script>', variant),
                                                      encoding='utf-8')
                self.migrate()
                result = self.read('stary', 'index.html')
                self.assertNotIn('comments.js', result)
                self.assertIn('<script src="prototype.js"></script>', result, 'control: the engine script stays')

    def test_v1_label_with_emoji_and_long_text_are_kept_not_skipped(self):
        """m1: lengths are UTF-16 code units; the cut never splits a surrogate pair."""
        face = '\U0001F600'
        label = 'a' + face * 150            # 301 units; the 200-unit limit falls inside the 100th pair
        long_text = 'x' * 3000 + face * 1500  # 6000 units: over the 4000 limit for new entries
        document = {'version': 1, 'prototypeId': 'stary', 'operations': [
            {'id': 'op-0001', 'type': 'create', 'threadId': 'th-emoji', 'at': '2026-10-02T09:00:00.000Z',
             'payload': {'screen': 's2', 'anchor': 'div>nie-ma-takiego>span', 'label': label,
                         'message': {'id': 'm-0001', 'author': 'Ala', 'text': long_text}}},
            {'id': 'op-0002', 'type': 'reply', 'threadId': 'th-emoji', 'at': '2026-10-02T09:01:00.000Z',
             'payload': {'message': {'id': 'm-0002', 'author': 'Bob', 'text': face * 10}}}]}
        (self.proto_dir('stary') / 'comments.js').write_text(ASSIGNMENT + json.dumps(document) + ';\n', encoding='utf-8')
        report = self.migrate()
        self.assertEqual(report.get('skipped', []), [], 'control: nothing is skipped')
        operations = self.operations()
        self.assertEqual([op['id'] for op in operations], ['op-0001', 'op-0002'])

        def units(value):
            return len(value.encode('utf-16-le')) // 2   # strict: a lone surrogate would raise here
        payload = operations[0]['payload']
        self.assertEqual(payload['label'], 'a' + face * 99, 'cut before the pair that would cross the limit')
        self.assertLessEqual(units(payload['label']), 200)
        self.assertEqual(payload['anchor'], {'kind': 'lost'})
        self.assertLessEqual(units(payload['hint']['text']), 120)
        self.assertEqual(payload['hint']['text'], 'a' + face * 59)
        self.assertEqual(payload['message']['text'], long_text, 'imported text is not cut')

    def test_unknown_slug_is_blocked(self):
        self.migrate(code=2, slug='brak')


# ---------------------------------------------------------------------------
# K32, K33: list and index
# ---------------------------------------------------------------------------
CONTRACT_FIELDS = {'slug', 'title', 'requirements', 'created', 'generated_by', 'path', 'screens', 'comments'}
ADDED_FIELDS = {'commentsFormat', 'threads', 'iteration', 'acceptance', 'writing'}
THREAD_KEYS = {'open', 'awaiting', 'decided', 'closed', 'deferred', 'lost'}


SPRITE_HEAD = ('<!doctype html>\n<html lang="pl" data-prototype-id="demo">\n<head>\n<meta charset="utf-8">\n'
               '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
               '<meta name="color-scheme" content="light dark">\n<meta name="darkreader-lock">\n'
               '<title>Demo</title>\n<link rel="stylesheet" href="screens.css">\n'
               '<link rel="stylesheet" href="project.css">\n</head>\n<body>\n')
SPRITE_SECTIONS = ('<section class="screen" id="s1">\n  <h1>Jeden</h1>\n</section>\n'
                   '<section class="screen" id="s2">\n  <h1>Dwa</h1>\n</section>\n</body>\n</html>\n')
OLD_A = '<symbol id="ic-a" viewBox="0 0 24 24"><path d="M0 0"/></symbol>'
NEW_A = '<symbol id="ic-a" viewBox="0 0 24 24"><path d="M1 1"/></symbol>'
NEW_B = '<symbol id="ic-b" viewBox="0 0 24 24">\n  <path d="M2 2"/>\n</symbol>'
PROJECT_SYMBOL = '<symbol id="proj-logo" viewBox="0 0 8 8"><circle r="4"/></symbol>'
SPRITE_BLOCK = ('<svg xmlns="http://www.w3.org/2000/svg" hidden>\n  ' + OLD_A + '\n  ' + PROJECT_SYMBOL + '\n</svg>\n')
LIBRARY_SPRITE = ('<svg xmlns="http://www.w3.org/2000/svg">\n' + NEW_A + '\n' + NEW_B + '\n'
                  '<symbol id="other-x"><g/></symbol>\n</svg>\n')


class SpriteFixture(PrototypeFixture):
    """v2 prototype `demo` with two accepted screens, an icon sprite and helpers for `migrate`."""

    def prepare(self, body_extra=SPRITE_BLOCK, accept=True, head=SPRITE_HEAD, project_css=True):
        from skillsync_workflow import prototype_server as ps
        self.init()
        directory = self.proto_dir('demo')
        if not project_css:
            (directory / 'project.css').unlink()
        (directory / 'index.html').write_text(head + body_extra + SPRITE_SECTIONS, encoding='utf-8')
        operations = []
        if accept:
            hashes = ps.screen_hashes(str(directory))['screens']
            operations.append({'id': 'op-0001', 'type': 'accept', 'at': '2026-11-05T10:00:00.000Z', 'by': 'ala@example.pl',
                               'payload': {'scope': 'project', 'screens': dict(hashes), 'iteration': 1, 'via': 'ui'}})
        (directory / 'comments.json').write_text(v2_log('demo', operations), encoding='utf-8')
        return directory

    def library_sprite(self, text=LIBRARY_SPRITE):
        target = self.assets / 'icons' / 'sprite.svg'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')

    def migrate(self, *args, code=0):
        return self.proto('migrate', 'demo', '--assets', str(self.assets), *args, code=code)


class SpriteMigrateTests(SpriteFixture):
    """`migrate` of a v2 prototype: icon sprite merge and the report of acceptances that go stale."""

    def test_sprite_symbols_are_replaced_added_and_project_symbols_kept(self):
        directory = self.prepare()
        before = (directory / 'index.html').read_text(encoding='utf-8')
        self.library_sprite()
        report = self.migrate()
        after = (directory / 'index.html').read_text(encoding='utf-8')
        self.assertEqual(report['sprite'], {'status': 'merged', 'replaced': ['ic-a'], 'added': ['ic-b']})
        expected = before.replace(OLD_A, NEW_A + '\n  ' + NEW_B)
        self.assertEqual(after, expected, 'only the icon symbols changed, the rest is byte for byte the same')
        self.assertIn(PROJECT_SYMBOL, after)
        self.assertNotIn('other-x', after, 'only ic-* symbols come from the library')
        self.assertIn('sprite_merge', [step['action'] for step in report['steps']])
        again = self.migrate()
        self.assertEqual(again['result'], 'already_migrated')
        self.assertEqual(again['sprite']['status'], 'unchanged')
        self.assertEqual((directory / 'index.html').read_text(encoding='utf-8'), after)

    def test_sprite_missing_in_library_is_only_reported(self):
        directory = self.prepare()
        before = (directory / 'index.html').read_bytes()
        report = self.migrate()
        self.assertEqual(report['sprite']['status'], 'missing_in_library')
        self.assertEqual(report['sprite']['message'], 'sprite: missing in the library')
        self.assertIn('sprite: missing in the library', report['warnings'])
        self.assertEqual((directory / 'index.html').read_bytes(), before)

    def test_no_container_reports_and_does_not_insert_missing_symbols(self):
        directory = self.prepare(body_extra='')
        self.library_sprite()
        before = (directory / 'index.html').read_bytes()
        report = self.migrate()
        self.assertEqual(report['sprite']['status'], 'no_container')
        self.assertEqual(report['sprite']['added'], [])
        self.assertTrue(any(item.startswith('sprite:') for item in report['warnings']), report)
        self.assertEqual((directory / 'index.html').read_bytes(), before)

    def test_engine_script_change_leaves_acceptances_valid(self):
        directory = self.prepare()
        engine = self.assets / 'repo' / 'prototype.js'
        engine.write_text(engine.read_text(encoding='utf-8') + '\n// changed engine\n', encoding='utf-8')
        report = self.migrate()
        self.assertEqual(report['acceptance'], {'lost': 0, 'screens': []})
        self.assertEqual((directory / 'prototype.js').read_bytes(), engine.read_bytes(), 'control: the engine was copied')
        self.assertEqual(self.proto('list')['prototypes'][0]['acceptance']['stale'], 0)

    def test_changed_theme_css_reports_stale_acceptances_and_matches_list_afterwards(self):
        directory = self.prepare()
        css = self.assets / 'repo' / 'screens.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n.new-rule { color: red; }\n', encoding='utf-8')
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['acceptance'], {'lost': 2, 'screens': ['s1', 's2']})
        real = self.migrate()
        self.assertEqual(real['acceptance'], dry['acceptance'])
        listed = self.proto('list')['prototypes'][0]
        self.assertEqual(listed['acceptance'], {'screens': 2, 'accepted': 0, 'stale': 2, 'perScreen': [
            {'id': 's1', 'state': 'stale'}, {'id': 's2', 'state': 'stale'}]})
        self.assertEqual((directory / 'screens.css').read_bytes(), css.read_bytes())

    def test_dry_run_writes_nothing_and_reports_the_same_as_the_real_run(self):
        directory = self.prepare()
        self.library_sprite()
        css = self.assets / 'repo' / 'components.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n.x { top: 0; }\n', encoding='utf-8')
        before = tree(directory)
        dry = self.migrate('--dry-run')
        self.assertTrue(dry['dryRun'])
        self.assertEqual(tree(directory), before)
        self.assertEqual(dry['sprite']['status'], 'merged')
        self.assertEqual(dry['acceptance']['lost'], 2)
        real = self.migrate()
        self.assertEqual((real['sprite'], real['acceptance']), (dry['sprite'], dry['acceptance']))
        self.assertNotEqual(tree(directory), before)

    def test_v1_prototype_gets_no_sprite_merge(self):
        directory = self.proto_dir('stary')
        shutil.copytree(V1, directory)
        self.library_sprite()
        before = (directory / 'index.html').read_text(encoding='utf-8')
        report = self.proto('migrate', 'stary', '--assets', str(self.assets))
        self.assertEqual(report['sprite']['status'], 'skipped')
        self.assertEqual(report['acceptance'], {'lost': 0, 'screens': []})
        self.assertNotIn('ic-b', (directory / 'index.html').read_text(encoding='utf-8'))
        self.assertNotIn('ic-b', before, 'fixture: the v1 page has no ic-b before migrate')
        self.assertNotIn('sprite_merge', [step['action'] for step in report['steps']])

    def test_head_without_theme_meta_makes_every_accepted_screen_stale(self):
        bare = SPRITE_HEAD.replace('<meta name="color-scheme" content="light dark">\n<meta name="darkreader-lock">\n', '')
        directory = self.prepare(head=bare)
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['acceptance'], {'lost': 2, 'screens': ['s1', 's2']})
        self.assertIn('theme_lock_meta', [step['action'] for step in dry['steps']])
        self.assertEqual(self.migrate()['acceptance'], dry['acceptance'])
        self.assertIn('darkreader-lock', (directory / 'index.html').read_text(encoding='utf-8'))

    def test_unreadable_sprite_stops_before_any_write(self):
        directory = self.prepare()
        (self.assets / 'icons').mkdir(parents=True, exist_ok=True)
        (self.assets / 'icons' / 'sprite.svg').write_bytes(b'\xff\xfe not utf-8')
        engine = self.assets / 'repo' / 'prototype.css'
        engine.write_text(engine.read_text(encoding='utf-8') + '\n.x { top: 1px; }\n', encoding='utf-8')
        before = tree(directory)
        report = self.migrate(code=2)
        self.assertIn('sprite.svg', report['reason'])
        self.assertEqual(tree(directory), before)

    def test_v1_and_missing_sprite_reports_have_the_same_shape(self):
        directory = self.prepare()
        report = self.migrate()
        self.assertEqual(set(report['sprite']), {'status', 'replaced', 'added', 'message'})
        shutil.copytree(V1, self.proto_dir('stary'))
        v1 = self.proto('migrate', 'stary', '--assets', str(self.assets), '--dry-run')
        self.assertEqual(set(v1['sprite']), {'status', 'replaced', 'added', 'message'})
        self.assertIn('the next `migrate`', v1['sprite']['message'])
        self.assertTrue(directory.is_dir())


def sprite_of(*symbols):
    return '<svg xmlns="http://www.w3.org/2000/svg">\n' + '\n'.join(symbols) + '\n</svg>\n'


class MergeIconSpriteTests(unittest.TestCase):
    """`merge_icon_sprite` on strings: the parsing edge cases of the project's `index.html`."""

    def merge(self, html, *library):
        from skillsync_workflow import prototype as generator
        return generator.merge_icon_sprite(html, sprite_of(*library))

    def test_crlf_keeps_line_endings_and_indent(self):
        html = '<body>\r\n<svg>\r\n  <symbol id="ic-a"><g/></symbol>\r\n</svg>\r\n</body>\r\n'
        merged, replaced, added, status = self.merge(html, '<symbol id="ic-a"><g/></symbol>',
                                                     '<symbol id="ic-b">\n<path/>\n</symbol>')
        self.assertEqual((replaced, added, status), ([], ['ic-b'], 'merged'))
        self.assertEqual(merged, '<body>\r\n<svg>\r\n  <symbol id="ic-a"><g/></symbol>\r\n  '
                                 '<symbol id="ic-b">\r\n<path/>\r\n</symbol>\r\n</svg>\r\n</body>\r\n')
        self.assertNotIn('\n', merged.replace('\r\n', ''))

    def test_bom_is_kept(self):
        html = '\ufeff<svg><symbol id="ic-a"><g/></symbol></svg>'
        merged, replaced, _, _ = self.merge(html, '<symbol id="ic-a"><path/></symbol>')
        self.assertEqual(merged, '\ufeff<svg><symbol id="ic-a"><path/></symbol></svg>')
        self.assertEqual(replaced, ['ic-a'])

    def test_single_quoted_unquoted_and_standalone_id_only(self):
        html = ("<svg><symbol data-id=\"ic-a\" id='ic-a'><g/></symbol><symbol id=ic-b><g/></symbol>"
                "<symbol-x id=\"ic-c\"><g/></symbol-x><symbol title=\"id='ic-d'\" id=\"proj\"><g/></symbol></svg>")
        merged, replaced, added, _ = self.merge(html, '<symbol id="ic-a"><new/></symbol>', '<symbol id="ic-b"><new/></symbol>',
                                                '<symbol id="ic-c"><new/></symbol>', '<symbol id="ic-d"><new/></symbol>')
        self.assertEqual(replaced, ['ic-a', 'ic-b'])
        self.assertEqual(added, ['ic-c', 'ic-d'], 'symbol-x and a quoted look-alike are not symbols with that id')
        self.assertIn('<symbol-x id="ic-c"><g/></symbol-x>', merged)
        self.assertIn('<symbol title="id=\'ic-d\'" id="proj"><g/></symbol>', merged)

    def test_self_closing_symbol_ends_at_its_own_tag(self):
        html = '<svg><symbol id="proj-a"/><symbol id="proj-b"><g/></symbol><symbol id="ic-a"/></svg>'
        merged, replaced, _, status = self.merge(html, '<symbol id="ic-a"><path/></symbol>')
        self.assertEqual(merged, '<svg><symbol id="proj-a"/><symbol id="proj-b"><g/></symbol><symbol id="ic-a"><path/></symbol></svg>')
        self.assertEqual((replaced, status), (['ic-a'], 'merged'))

    def test_symbols_in_comments_are_ignored(self):
        html = '<svg><symbol id="ic-a"><g/></symbol></svg><!-- <symbol id="ic-b"><g/></symbol> -->'
        merged, replaced, added, _ = self.merge(html, '<symbol id="ic-b"><path/></symbol>')
        self.assertEqual((replaced, added), ([], ['ic-b']))
        self.assertTrue(merged.endswith('<!-- <symbol id="ic-b"><g/></symbol> -->'))
        self.assertIn('<symbol id="ic-a"><g/></symbol>\n<symbol id="ic-b"><path/></symbol></svg>', merged)

    def test_several_svgs_append_to_the_container_of_the_last_icon(self):
        html = '<svg><symbol id="ic-a"><g/></symbol></svg><p/><svg><symbol id="proj"><g/></symbol></svg>'
        merged, _, added, status = self.merge(html, '<symbol id="ic-b"><path/></symbol>')
        self.assertEqual((added, status), (['ic-b'], 'merged'))
        self.assertEqual(merged, '<svg><symbol id="ic-a"><g/></symbol>\n<symbol id="ic-b"><path/></symbol></svg><p/>'
                                 '<svg><symbol id="proj"><g/></symbol></svg>')

    def test_no_symbols_in_the_library(self):
        html = '<svg><symbol id="ic-a"><g/></symbol></svg>'
        from skillsync_workflow import prototype as generator
        self.assertEqual(generator.merge_icon_sprite(html, sprite_of('<symbol id="other"><g/></symbol>')),
                         (html, [], [], 'no_symbols'))

    def test_missing_closing_svg_still_replaces_existing_ids(self):
        html = '<svg><symbol id="ic-a"><g/></symbol>'
        merged, replaced, added, status = self.merge(html, '<symbol id="ic-a"><path/></symbol>',
                                                     '<symbol id="ic-b"><path/></symbol>')
        self.assertEqual(merged, '<svg><symbol id="ic-a"><path/></symbol>')
        self.assertEqual((replaced, added, status), (['ic-a'], [], 'no_container'))

    def test_symbol_outside_any_svg_is_no_container(self):
        html = '<p><symbol id="ic-a"><g/></symbol></p><svg></svg>'
        merged, _, added, status = self.merge(html, '<symbol id="ic-b"><path/></symbol>')
        self.assertEqual((merged, added, status), (html, [], 'no_container'))


# The recognised notice texts are built from fragments: the repository keeps no product names (rule of zero hits).
ADAPTATION_NOTE = '<!-- Adaptacja om' + '-mock' + 'up-prototype: wzorzec z ' + 'Open Mer' + 'cato, licencja MIT -->'
ICON_NOTE = ('<!--\n  Ikony: ' + 'Lu' + 'cide (ISC) i ' + 'Fea' + 'ther (MIT).\n  Pełna nota licencyjna w repozytorium zestawu.\n-->')
OWN_NOTE = '<!-- Własna nota projektu: nie ruszać -->'


class NoticeMigrateTests(SpriteFixture):
    """`migrate` removes the two recognised notice comments of an old template (`notices_removed`)."""

    def noted(self, **kwargs):
        head = SPRITE_HEAD.replace('<title>', '  ' + ADAPTATION_NOTE + '\n<title>')
        head = head.replace('<title>', OWN_NOTE + '\n<title>')
        return self.prepare(body_extra=ICON_NOTE + '\n' + SPRITE_BLOCK, head=head, **kwargs)

    def test_both_notes_are_removed_and_other_comments_stay(self):
        directory = self.noted()
        before = (directory / 'index.html').read_text(encoding='utf-8')
        self.library_sprite()
        report = self.migrate()
        self.assertEqual(report['notices_removed'], ['template-adaptation', 'icon-license'])
        after = (directory / 'index.html').read_text(encoding='utf-8')
        self.assertIn(OWN_NOTE, after)
        self.assertNotIn(ADAPTATION_NOTE, after)
        self.assertNotIn(ICON_NOTE, after)
        expected = before.replace('  ' + ADAPTATION_NOTE + '\n', '').replace(ICON_NOTE + '\n', '').replace(OLD_A, NEW_A + '\n  ' + NEW_B)
        self.assertEqual(after, expected, 'whole-line comments leave with their line, the rest is byte for byte')
        again = self.migrate()
        self.assertEqual((again['result'], again['notices_removed']), ('already_migrated', []))
        self.assertEqual((directory / 'index.html').read_text(encoding='utf-8'), after)

    def test_dry_run_reports_notes_without_writing(self):
        directory = self.noted()
        self.library_sprite()
        before = tree(directory)
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['notices_removed'], ['template-adaptation', 'icon-license'])
        self.assertEqual(tree(directory), before)
        self.assertEqual(dry['acceptance']['lost'], 2, 'the head changed, so the accepted screens go stale')

    def test_icon_note_stays_without_a_sprite_merge(self):
        directory = self.noted()
        report = self.migrate()   # no sprite in the library
        self.assertEqual(report['notices_removed'], ['template-adaptation'])
        text = (directory / 'index.html').read_text(encoding='utf-8')
        self.assertIn(ICON_NOTE, text)
        self.assertNotIn(ADAPTATION_NOTE, text)

    def test_no_notes_means_an_empty_list(self):
        self.prepare()
        self.library_sprite()
        self.assertEqual(self.migrate()['notices_removed'], [])

    def test_inline_comment_goes_alone_and_adaptation_outside_head_stays(self):
        from skillsync_workflow import prototype as generator
        html = ('<html><head><title>x</title> ' + ADAPTATION_NOTE + '<meta></head>\n<body>' + ADAPTATION_NOTE
                + '<p/>\r\n  ' + ICON_NOTE + '\r\n</body></html>')
        cleaned, kinds = generator.remove_template_notices(html)
        self.assertEqual(kinds, ['template-adaptation', 'icon-license'])
        self.assertEqual(cleaned, '<html><head><title>x</title> <meta></head>\n<body>' + ADAPTATION_NOTE + '<p/>\r\n</body></html>')
        self.assertEqual(generator.remove_template_notices(html, icons=False)[1], ['template-adaptation'])


README_ICON_LINE = "- Ikony pochodzą z osadzonego sprite'a SVG (" + 'Lu' + 'cide, licencja ISC).\n'
README_OWN_MENTION = 'Ikony z osadzonego sprite’a zamiast `' + 'lu' + 'cide-react` w produkcji.\n'


class ReadmeNoticeMigrateTests(SpriteFixture):
    """`migrate` removes the fixed icon licence line of the old template from `README.md` and only reports
    other mentions of the old icon library (`notices_found`)."""

    def readme(self, directory, text):
        (directory / 'README.md').write_bytes(text.encode('utf-8'))

    def test_fixed_line_is_removed_and_the_rest_stays_byte_for_byte(self):
        directory = self.prepare()
        self.readme(directory, '# Demo\r\n\r\n- Pierwsza uwaga.\n' + README_ICON_LINE + '- Ostatnia uwaga.\n')
        self.library_sprite()
        report = self.migrate()
        self.assertEqual(report['notices_removed'], ['readme-icon-license'])
        self.assertEqual(report['notices_found'], [])
        self.assertEqual((directory / 'README.md').read_bytes(), b'# Demo\r\n\r\n- Pierwsza uwaga.\n- Ostatnia uwaga.\n')
        self.assertIn('docs/prototypes/demo/README.md', report['files'])
        again = self.migrate()
        self.assertEqual((again['notices_removed'], again['notices_found']), ([], []))
        self.assertNotIn('docs/prototypes/demo/README.md', again['files'])

    def test_last_line_without_newline_and_typographic_apostrophe(self):
        directory = self.prepare()
        self.readme(directory, '# Demo\n' + README_ICON_LINE.replace("'", '’').rstrip('\n'))
        self.library_sprite()
        self.assertEqual(self.migrate()['notices_removed'], ['readme-icon-license'])
        self.assertEqual((directory / 'README.md').read_text(encoding='utf-8'), '# Demo\n')

    def test_other_mention_is_reported_not_changed(self):
        directory = self.prepare()
        text = '# Demo\n\n' + README_OWN_MENTION
        self.readme(directory, text)
        self.library_sprite()
        report = self.migrate()
        self.assertEqual(report['notices_removed'], [])
        self.assertEqual(report['notices_found'], [{'file': 'README.md', 'line': 3, 'kind': 'icon-library-mention'}])
        self.assertTrue(any(w.startswith('README.md:3 ') for w in report.get('warnings', [])))
        self.assertEqual((directory / 'README.md').read_text(encoding='utf-8'), text)

    def test_dry_run_reports_without_writing(self):
        directory = self.prepare()
        self.readme(directory, README_ICON_LINE)
        self.library_sprite()
        before = tree(directory)
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['notices_removed'], ['readme-icon-license'])
        self.assertEqual(tree(directory), before)

    def test_line_stays_without_a_sprite_merge_and_is_reported(self):
        directory = self.prepare()
        self.readme(directory, README_ICON_LINE)
        report = self.migrate()   # no sprite in the library: the icons are still the old ones
        self.assertEqual(report['notices_removed'], [])
        self.assertEqual(report['notices_found'], [{'file': 'README.md', 'line': 1, 'kind': 'icon-library-mention'}])
        self.assertEqual((directory / 'README.md').read_text(encoding='utf-8'), README_ICON_LINE)

    def test_no_readme_means_empty_lists(self):
        directory = self.prepare()
        if (directory / 'README.md').exists():
            (directory / 'README.md').unlink()
        self.library_sprite()
        report = self.migrate()
        self.assertEqual((report['notices_removed'], report['notices_found']), ([], []))


class TokensSyncMigrateTests(SpriteFixture):
    """D187: `migrate` also refreshes the generated theme files (`tokens.css`, `theme.css`) like `sync`."""

    def change_library_theme(self):
        theme = self.assets / 'theme' / 'default.css'
        theme.write_text(theme.read_text(encoding='utf-8').replace(':root {', ':root {\n  --d187-marker: 1px;', 1),
                         encoding='utf-8')

    def test_changed_library_theme_is_synced_by_migrate_alone(self):
        directory = self.prepare()
        self.migrate()
        self.change_library_theme()
        self.assertEqual(self.proto('sync', '--check', code=1)['stale'], ['docs/prototypes/demo/tokens.css'])
        report = self.migrate()
        self.assertEqual((report['tokens_synced'], report['tokens_files']), (True, ['docs/prototypes/demo/tokens.css']))
        self.assertIn('sync_tokens', [step['action'] for step in report['steps']])
        self.assertIn('--d187-marker', (directory / 'tokens.css').read_text(encoding='utf-8'))
        self.assertEqual(self.proto('sync', '--check')['status'], 'PASS', 'no separate sync is needed')
        self.assertEqual(report['acceptance']['lost'], 2, 'new tokens change the screen hashes')
        again = self.migrate()
        self.assertEqual((again['tokens_synced'], again['tokens_files'], again['result']), (True, [], 'already_migrated'))

    def test_dry_run_reports_the_token_files_without_writing(self):
        directory = self.prepare()
        self.migrate()
        self.change_library_theme()
        before = tree(directory)
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['tokens_files'], ['docs/prototypes/demo/tokens.css'])
        self.assertEqual(dry['acceptance']['lost'], 2)
        self.assertEqual(tree(directory), before)

    def test_symlinked_engine_file_blocks_before_any_write(self):
        directory = self.prepare()
        self.migrate()
        (directory / 'screens.css').unlink()
        (directory / 'screens.css').symlink_to(directory / 'theme.css')
        self.change_library_theme()
        before = tree(directory)
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'migrate', str(self.root), 'demo',
                                 '--assets', str(self.assets), '--json'], capture_output=True, text=True, timeout=60,
                                cwd=self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('screens.css', result.stdout + result.stderr)
        self.assertIn('nothing was written', result.stdout + result.stderr)
        self.assertEqual(tree(directory), before, 'tokens and other engine files are not written either')

    def test_symlinked_tokens_file_blocks_before_any_write(self):
        directory = self.prepare()
        self.migrate()
        (directory / 'tokens.css').unlink()
        (directory / 'tokens.css').symlink_to(directory / 'theme.css')
        self.change_library_theme()
        before = tree(directory)
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'migrate', str(self.root), 'demo',
                                 '--assets', str(self.assets), '--json'], capture_output=True, text=True, timeout=60,
                                cwd=self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('tokens.css', result.stdout + result.stderr)
        self.assertEqual(tree(directory), before)


class ProjectCssTests(SpriteFixture):
    """`project.css` belongs to the project: `init` creates it, `sync` and `migrate` leave it alone."""
    UNLINKED = SPRITE_HEAD.replace('<link rel="stylesheet" href="project.css">\n', '')

    def test_init_creates_project_css_with_a_short_comment(self):
        self.init()
        text = self.read('demo', 'project.css')
        self.assertEqual(text, '/* Style projektu — ten plik nie jest nadpisywany przez sync ani migrate */\n')
        self.assertNotIn('do not edit', text)

    def test_sync_and_migrate_keep_an_existing_project_css_byte_for_byte(self):
        directory = self.prepare()
        own = b'.pz-karta { color: red; }\r\n'
        (directory / 'project.css').write_bytes(own)
        self.proto('sync', 'demo')
        self.assertEqual((directory / 'project.css').read_bytes(), own)
        css = self.assets / 'repo' / 'screens.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n.x { top: 0; }\n', encoding='utf-8')
        report = self.migrate()
        self.assertEqual((directory / 'project.css').read_bytes(), own)
        self.assertNotIn('create_project_css', [step['action'] for step in report['steps']])

    def test_migrate_creates_project_css_and_link_once(self):
        directory = self.prepare(head=self.UNLINKED, project_css=False)
        before = (directory / 'index.html').read_text(encoding='utf-8')
        dry_tree = tree(directory)
        dry = self.migrate('--dry-run')
        self.assertEqual(tree(directory), dry_tree, '--dry-run writes nothing')
        self.assertEqual(dry['acceptance'], {'lost': 2, 'screens': ['s1', 's2']})
        report = self.migrate()
        actions = [step['action'] for step in report['steps']]
        self.assertIn('create_project_css', actions)
        self.assertIn('link_project_css', actions)
        self.assertEqual(report['acceptance'], dry['acceptance'])
        self.assertTrue((directory / 'project.css').read_text(encoding='utf-8').startswith('/* Style projektu'))
        after = (directory / 'index.html').read_text(encoding='utf-8')
        self.assertEqual(after, before.replace('<link rel="stylesheet" href="screens.css">\n',
                                                 '<link rel="stylesheet" href="screens.css">\n'
                                                 '<link rel="stylesheet" href="project.css">\n'))
        again = self.migrate()
        self.assertEqual(again['result'], 'already_migrated')
        self.assertEqual((directory / 'index.html').read_text(encoding='utf-8'), after)
        self.assertEqual(after.count('project.css'), 1)

    def test_warning_names_project_classes_missing_from_the_new_screens_css(self):
        directory = self.prepare(project_css=False)
        css = directory / 'screens.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n/* .w-komentarzu { } */\n.pz-karta:hover, .pz-lista > .pz-wiersz { margin: 0.5em; }\n'
                       '@media (min-width: 40em) { .pz-szeroka { width: 1px; } }\n', encoding='utf-8')
        dry = self.migrate('--dry-run')
        self.assertEqual(dry['project_styles_warning']['classes'], ['pz-karta', 'pz-lista', 'pz-szeroka', 'pz-wiersz'])
        self.assertIn('project.css', dry['project_styles_warning']['message'])
        self.assertIn('.pz-karta', css.read_text(encoding='utf-8'), 'dry-run keeps the old file')
        report = self.migrate()
        self.assertEqual(report['project_styles_warning'], dry['project_styles_warning'])
        self.assertNotIn('pz-karta', css.read_text(encoding='utf-8'), 'the rules are not copied anywhere')
        self.assertNotIn('pz-', (directory / 'project.css').read_text(encoding='utf-8'))
        self.assertNotIn('project_styles_warning', self.migrate())

    def test_link_goes_after_the_last_stylesheet_whatever_file_it_is(self):
        head = self.UNLINKED.replace('<link rel="stylesheet" href="screens.css">\n',
                                     '<link rel="stylesheet" href="screens.css">\n<link rel="stylesheet" href="prototype.css">\n')
        directory = self.prepare(head=head, project_css=False)
        self.migrate()
        text = (directory / 'index.html').read_text(encoding='utf-8')
        self.assertIn('href="prototype.css">\n<link rel="stylesheet" href="project.css">\n</head>', text)
        self.assertEqual(text.count('project.css'), 1)

    def test_external_or_absolute_project_css_is_not_the_local_link(self):
        from skillsync_workflow import prototype as generator
        for href in ('https://cdn.example/project.css', '//cdn.example/project.css', '/project.css', 'sub/project.css'):
            with self.subTest(href=href):
                html, status = generator.add_project_css_link(
                    f'<html><head><link rel="stylesheet" href="{href}"></head></html>')
                self.assertEqual(status, 'added')
                self.assertIn(f'href="{href}">\n<link rel="stylesheet" href="project.css">', html)
        for href in ('project.css', './project.css', 'project.css?v=2'):
            with self.subTest(href=href):
                self.assertEqual(generator.add_project_css_link(
                    f'<html><head><link rel="stylesheet" href="{href}"></head></html>')[1], 'present')

    def test_link_without_any_stylesheet_goes_before_the_end_of_head(self):
        from skillsync_workflow import prototype as generator
        html, status = generator.add_project_css_link('<html><head><title>x</title></head><body/></html>')
        self.assertEqual(status, 'added')
        self.assertIn('<link rel="stylesheet" href="project.css">\n</head>', html)

    def test_class_moved_inside_the_library_is_not_reported_but_project_class_is(self):
        directory = self.prepare()
        css = directory / 'screens.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n.kanban { display: grid; }\n.pz-karta { margin: 0; }\n', encoding='utf-8')
        components = self.assets / 'repo' / 'components.css'
        components.write_text(components.read_text(encoding='utf-8') + '\n.kanban { display: grid; gap: 1px; }\n', encoding='utf-8')
        report = self.migrate()
        self.assertEqual(report['project_styles_warning']['classes'], ['pz-karta'])

    def test_classes_already_in_project_css_are_not_reported(self):
        directory = self.prepare()
        css = directory / 'screens.css'
        css.write_text(css.read_text(encoding='utf-8') + '\n.pz-karta { margin: 0; }\n.pz-inna { margin: 0; }\n', encoding='utf-8')
        (directory / 'project.css').write_text('.pz-karta { margin: 0; }\n', encoding='utf-8')
        self.assertEqual(self.migrate()['project_styles_warning']['classes'], ['pz-inna'])


class ListAndIndexTests(V1Fixture):
    def entry(self, slug='stary'):
        return [item for item in self.list()['prototypes'] if item['slug'] == slug][0]

    def warnings_of(self, slug):
        return [w for w in self.list().get('warnings', []) if w.startswith(f'docs/prototypes/{slug}: ')]

    def test_v1_prototype_is_listed_without_migration_with_a_warning(self):
        before = tree(self.proto_dir('stary'))
        report = self.list()
        self.assertEqual(tree(self.proto_dir('stary')), before, 'K33: list does not touch a v1 prototype')
        entry = report['prototypes'][0]
        self.assertEqual((entry['comments'], entry['commentsFormat']), (6, 1))
        self.assertEqual(entry['screens'], 6, 'the example screen is not counted')
        self.assertIsNone(entry['threads'])
        self.assertIsNone(entry['iteration'])
        self.assertIsNone(entry['acceptance'])
        self.assertEqual(len([w for w in report['warnings'] if 'migrate' in w]), 1)
        self.assertTrue(report['warnings'][0].startswith('docs/prototypes/stary: '))

    def test_migrated_prototype_lists_v2_counts_and_threads(self):
        self.migrate()
        report = self.list()
        self.assertNotIn('warnings', report)
        entry = report['prototypes'][0]
        self.assertEqual((entry['comments'], entry['commentsFormat']), (6, 2))
        self.assertEqual(entry['threads'], {'open': 6, 'awaiting': 0, 'decided': 0, 'closed': 1, 'deferred': 0, 'lost': 4})
        self.assertEqual(entry['iteration'], 1, 'K32: the current iteration (no freeze yet)')
        # F3 (K20): v2 lists {screens, accepted, stale} (nothing accepted after the migration); v1 stays null (above)
        self.assertEqual(entry['acceptance'], {'screens': 6, 'accepted': 0, 'stale': 0, 'perScreen': [
            {'id': f's{n}', 'state': 'pending'} for n in range(2, 8)]})

    def test_iteration_grows_after_a_frozen_iteration(self):
        self.migrate()
        self.assertEqual(self.entry()['iteration'], 1, 'control: nothing frozen')
        self.proto('ids', 'stary', '--fix')
        frozen = self.proto('iterate', 'stary')
        self.assertEqual(frozen['iteration'], 1)
        self.assertEqual(self.entry()['iteration'], 2, 'K32: iteration 1 is frozen, the current one is 2')

    def test_lost_counts_threads_whose_element_vanished_from_the_current_html(self):
        self.migrate()
        self.assertEqual(self.entry()['threads']['lost'], 4)
        html = self.read('stary', 'index.html')
        (self.proto_dir('stary') / 'index.html').write_text(html.replace(' data-proto-id="s2-button-1"', ''), encoding='utf-8')
        entry = self.entry()
        self.assertEqual(entry['threads']['lost'], 6, 'two threads anchored to s2-button-1 lost their handle')
        self.assertEqual(entry['comments'], 6, 'lost threads stay open')

    def test_contract_fields_of_skippster_are_kept_and_only_new_fields_are_added(self):
        # skippster docs/contracts/skillsync-cli.md: screens and comments are int|null, wpis zwykły ma stałe pola
        self.init('demo')
        for slug in ('demo', 'stary'):
            entry = self.entry(slug)
            self.assertEqual(set(entry), CONTRACT_FIELDS | ADDED_FIELDS, slug)
            self.assertIsInstance(entry['screens'], int)
            self.assertIsInstance(entry['comments'], int)
            self.assertIsNot(entry['comments'], True)
        self.migrate()
        self.assertEqual(set(self.entry()), CONTRACT_FIELDS | ADDED_FIELDS)
        self.assertEqual(set(self.entry()['threads']), THREAD_KEYS)

    def test_fresh_v2_prototype_counts(self):
        self.init('demo')
        entry = self.entry('demo')
        self.assertEqual((entry['comments'], entry['commentsFormat'], entry['screens']), (0, 2, 1))  # fixture index: example screen + s2
        self.assertEqual(entry['threads'], {key: 0 for key in THREAD_KEYS})

    def test_missing_log_gives_nulls(self):
        self.init('demo')
        os.unlink(self.proto_dir('demo') / 'comments.json')
        entry = self.entry('demo')
        self.assertEqual((entry['comments'], entry['commentsFormat'], entry['threads']), (None, None, None))

    def test_comments_json_wins_over_a_leftover_comments_js(self):
        self.migrate()
        shutil.copyfile(V1 / 'comments.js', self.proto_dir('stary') / 'comments.js')
        entry = self.entry()
        self.assertEqual((entry['commentsFormat'], entry['comments']), (2, 6))

    def test_open_awaiting_and_decided_count_as_open(self):
        self.init('demo')
        at = '2026-11-05T09:{:02d}:00.000Z'
        ops = [v2_create(1, 'th-1', at.format(0)), v2_create(2, 'th-2', at.format(1)), v2_create(3, 'th-3', at.format(2)),
               v2_create(4, 'th-4', at.format(3))]
        ops.append({'id': 'op-0005', 'type': 'triage', 'threadId': 'th-2', 'at': at.format(4), 'by': 'agent',
                    'payload': {'class': 'disputed'}})
        ops.append({'id': 'op-0006', 'type': 'decision', 'threadId': 'th-3', 'at': at.format(5), 'by': 'agent',
                    'payload': {'kind': 'agent', 'outcome': 'apply', 'text': 'zrobione'}})
        ops.append({'id': 'op-0007', 'type': 'decision', 'threadId': 'th-4', 'at': at.format(6), 'by': 'agent',
                    'payload': {'kind': 'agent', 'outcome': 'keep', 'text': 'zostaje'}})
        (self.proto_dir() / 'comments.json').write_text(v2_log('demo', ops), encoding='utf-8')
        entry = self.entry('demo')
        self.assertEqual(entry['comments'], 3)
        self.assertEqual({key: entry['threads'][key] for key in ('open', 'awaiting', 'decided', 'closed')},
                         {'open': 1, 'awaiting': 1, 'decided': 1, 'closed': 1})

    def test_broken_comments_json_gives_null_with_a_warning(self):
        self.init('demo')
        (self.proto_dir() / 'comments.json').write_text('{"format": broken', encoding='utf-8')
        report = self.list()
        entry = report['prototypes'][0]
        self.assertEqual((entry['comments'], entry['commentsFormat'], entry['threads']), (None, 2, None))
        self.assertTrue(any('comments.json' in warning for warning in report['warnings']))

    def test_large_comments_json_warns_from_10_mib(self):
        self.init('demo')
        path = self.proto_dir() / 'comments.json'
        padding = ' ' * (10 * 1024 * 1024)
        path.write_text(v2_log('demo', [v2_create(1, 'th-1', '2026-11-05T09:00:00.000Z')]).rstrip('\n') + padding + '\n',
                        encoding='utf-8')
        report = self.list()
        self.assertTrue(any('10 MiB' in warning for warning in report['warnings']), report.get('warnings'))
        path.write_text(v2_log('demo', [v2_create(1, 'th-1', '2026-11-05T09:00:00.000Z')]), encoding='utf-8')
        self.assertEqual(self.warnings_of('demo'), [], 'control: the small file does not warn')

    def test_duplicated_iteration_number_warns(self):
        self.init('demo')
        base = [v2_create(1, 'th-1', '2026-11-05T09:00:00.000Z')]
        freeze = [{'id': f'op-000{n}', 'type': 'iteration', 'at': f'2026-11-05T09:0{n}:00.000Z', 'by': 'agent',
                   'payload': {'number': 1, 'path': 'iterations/1/', 'hash': str(n) * 64}} for n in (2, 3)]
        path = self.proto_dir() / 'comments.json'
        path.write_text(v2_log('demo', base + freeze[:1]), encoding='utf-8')
        self.assertEqual(self.warnings_of('demo'), [], 'control: one freeze of iteration 1 is fine')
        path.write_text(v2_log('demo', base + freeze), encoding='utf-8')
        warnings = self.warnings_of('demo')
        self.assertTrue(any('iteration' in warning for warning in warnings), warnings)

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlinks')
    def test_symlinked_comments_json_is_not_read(self):
        self.init('demo')
        secret = self.outside / 'comments.json'
        secret.write_text(v2_log('demo', [v2_create(1, 'th-1', '2026-11-05T09:00:00.000Z', text='sekret-poza-repo')]),
                          encoding='utf-8')
        os.unlink(self.proto_dir() / 'comments.json')
        os.symlink(secret, self.proto_dir() / 'comments.json')
        report = self.list()
        self.assertIsNone(report['prototypes'][0]['comments'])
        self.assertNotIn('sekret-poza-repo', json.dumps(report))

    def gallery_comments(self):
        text = (self.protos / 'index.html').read_text(encoding='utf-8')
        return re.findall(r'<dt>Otwarte komentarze</dt>\s*<dd>([^<]*)</dd>', text)

    def test_index_shows_open_comments_of_v1_and_of_v2(self):
        self.proto('index')
        self.assertEqual(self.gallery_comments(), ['6'], 'v1 (comments.js) is counted')
        self.migrate()
        self.proto('index')
        self.assertEqual(self.gallery_comments(), ['6'], 'v2 (comments.json) is counted')
        (self.proto_dir('stary') / 'comments.json').write_text(
            v2_log('stary', [v2_create(1, 'th-1', '2026-11-05T09:00:00.000Z')]), encoding='utf-8')
        os.unlink(self.protos / 'index.html')
        self.proto('index')
        self.assertEqual(self.gallery_comments(), ['1'], 'control: the column follows comments.json')


# ---------------------------------------------------------------------------
# K10, K41: user (default password from the machine config)
# ---------------------------------------------------------------------------
class MachineConfigPathTests(unittest.TestCase):
    """An empty `SKILLSYNC_CONFIG=` means the default path, the same as `common.machine_config_path`."""

    def test_empty_and_missing_variable_give_the_default_path(self):
        from skillsync_workflow import prototype as generator
        default = Path.home() / '.config/skillsync/config.yaml'
        self.assertEqual(generator.machine_config_path({}), default)
        self.assertEqual(generator.machine_config_path({'SKILLSYNC_CONFIG': ''}), default)

    def test_set_variable_is_used_and_tilde_expanded(self):
        from skillsync_workflow import prototype as generator
        self.assertEqual(generator.machine_config_path({'SKILLSYNC_CONFIG': '~/inny.yaml'}), Path.home() / 'inny.yaml')


class UserTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.config = self.base / 'machine.yaml'
        self.write_config(f'prototype:\n  default_password: {SECRET}\n')
        self.users = self.root / '.skillsync' / 'prototype' / 'users.json'
        self.outputs = []

    def write_config(self, text, mode=0o600):
        self.config.write_text(text, encoding='utf-8')
        os.chmod(self.config, mode)

    def user(self, *args, code=0, stdin=None, config=True, env=None):
        environ = {key: value for key, value in os.environ.items() if key != 'SKILLSYNC_CONFIG'}
        if config:
            environ['SKILLSYNC_CONFIG'] = str(self.config)
        environ.update(env or {})
        argv = [sys.executable, str(CLI), 'prototype', 'user', args[0], str(self.root), *args[1:], '--json']
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60, cwd=self.base, env=environ)
        self.outputs.append(result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn(report['status'], {0: ('PASS',), 2: ('BLOCKED',)}[code], report)
        return report

    def assertNoSecret(self, *texts):
        for text in texts:
            self.assertNotIn(SECRET, text, 'the default password leaked into the output')

    def tearDown(self):
        self.assertNoSecret(*self.outputs)
        super().tearDown()

    def users_text(self):
        return self.users.read_text(encoding='utf-8')

    def test_leak_check_detects_the_secret(self):
        # control: the helper used by every test below must fail when the secret is present
        with self.assertRaises(AssertionError):
            self.assertNoSecret(f'{{"reason": "x {SECRET} y"}}')

    def test_add_without_a_password_flag_uses_the_default_password_and_sets_must_change(self):
        report = self.user('add', 'Ala.Kowalska@Example.PL')
        self.assertEqual(report['user']['login'], 'ala.kowalska@example.pl')
        self.assertEqual((report['user']['role'], report['user']['mustChange']), ('commenter', True))
        self.assertNotIn('password', report['user'])
        self.assertNoSecret(self.users_text())
        self.assertIn('pbkdf2', self.users_text().lower(), 'the stored value is a hash')
        self.assertNotIn('warnings', report, 'a commenter is not a privileged role')

    def test_privileged_roles_get_a_warning_about_the_shared_password(self):
        for role in ('acceptor', 'developer'):
            report = self.user('add', f'{role}@example.pl', '--role', role)
            self.assertTrue(report['user']['mustChange'])
            self.assertTrue(any(f'{role}@example.pl' in warning for warning in report.get('warnings', [])), role)

    def test_add_with_password_from_stdin_does_not_set_must_change(self):
        report = self.user('add', 'a@example.pl', '--password-stdin', stdin='Wlasne-Haslo-12345\n')
        self.assertFalse(report['user']['mustChange'])
        self.assertNotIn('Wlasne-Haslo-12345', json.dumps(report) + self.users_text())

    def test_passwd_without_a_flag_resets_to_the_default_with_must_change(self):
        self.user('add', 'a@example.pl', '--password-stdin', stdin='Wlasne-Haslo-12345\n')
        report = self.user('passwd', 'a@example.pl')
        self.assertTrue(report['user']['mustChange'])

    def test_login_must_be_an_e_mail_address(self):
        report = self.user('add', 'nie-adres', code=2)
        self.assertIn('invalid_login', report['reason'])
        self.assertFalse(self.users.exists())

    def test_list_shows_must_change_and_no_hashes(self):
        self.user('add', 'a@example.pl')
        self.user('add', 'b@example.pl', '--password-stdin', stdin='Wlasne-Haslo-12345\n')
        text = json.dumps(self.user('list'))
        self.assertNotIn('pbkdf2', text.lower())
        self.assertNotIn('password"', text.replace('passwordChanged', ''))
        listed = {item['login']: item for item in self.user('list')['users']}
        self.assertEqual((listed['a@example.pl']['mustChange'], listed['b@example.pl']['mustChange']), (True, False))

    def test_remove_and_unknown_user(self):
        self.user('add', 'a@example.pl')
        self.user('remove', 'a@example.pl')
        self.assertEqual(self.user('list')['users'], [])
        self.user('remove', 'a@example.pl', code=2)

    def test_users_option_is_relative_to_the_project_and_import_skips_existing(self):
        self.write('lista.txt', 'ala@example.pl\n\nBob@Example.pl\nstary@example.pl\n')
        self.user('add', 'stary@example.pl', '--users', 'konta/u.json')
        report = self.user('import', 'lista.txt', '--users', 'konta/u.json', '--role', 'acceptor')
        self.assertEqual(report['added'], ['ala@example.pl', 'bob@example.pl'])
        self.assertEqual(report['skipped'], ['stary@example.pl'])
        listed = {item['login']: item for item in self.user('list', '--users', 'konta/u.json')['users']}
        self.assertEqual(listed['ala@example.pl']['role'], 'acceptor')
        self.assertTrue(listed['ala@example.pl']['mustChange'])
        self.assertEqual(listed['stary@example.pl']['role'], 'commenter', 'an existing account is not changed')
        self.assertTrue((self.root / 'konta' / 'u.json').exists())
        self.assertFalse(self.users.exists())

    def test_import_with_an_invalid_address_names_the_lines_and_writes_nothing(self):
        self.write('lista.txt', 'ala@example.pl\nzly adres\nbob@example.pl\n')
        report = self.user('import', 'lista.txt', code=2)
        self.assertEqual(report['errors'], ['line 2: invalid_login'])
        self.assertFalse(self.users.exists())

    def test_import_defaults_to_commenter(self):
        self.write('lista.txt', 'ala@example.pl\n')
        self.user('import', 'lista.txt')
        self.assertEqual(self.user('list')['users'][0]['role'], 'commenter')

    def test_password_flags_exclude_each_other(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'user', 'add', str(self.root), 'a@example.pl',
                                 '--password-prompt', '--password-stdin'], capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'SKILLSYNC_CONFIG': str(self.config)})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.users.exists())

    def test_missing_config_or_key_blocks_without_creating_the_users_file(self):
        self.config.unlink()
        self.user('add', 'a@example.pl', code=2)
        self.write_config('prototype:\n  inne: 1\n')
        report = self.user('add', 'a@example.pl', code=2)
        self.assertIn('default_password', report['reason'])
        self.write_config('prototype:\n  default_password: 12345\n')
        self.user('add', 'a@example.pl', code=2)
        self.assertFalse(self.users.exists())

    def test_yaml_error_in_the_config_does_not_quote_the_file(self):
        # the YAML parser's error prints an excerpt of the faulty line (the password); the general redaction of
        # `password: …` does not cover other keys, so the message itself must not quote the file
        self.write_config(f'prototype:\n  default_password: {SECRET}\n  zapas: {SECRET}: dalej\n')
        report = self.user('add', 'a@example.pl', code=2)
        self.assertNoSecret(json.dumps(report))
        self.assertFalse(self.users.exists())

    @POSIX_ONLY
    def test_config_wider_than_0600_is_refused_with_chmod_and_the_secret_stays_hidden(self):
        for mode in (0o644, 0o640, 0o660):
            with self.subTest(mode=oct(mode)):
                self.write_config(f'prototype:\n  default_password: {SECRET}\n', mode)
                report = self.user('add', 'a@example.pl', code=2)
                self.assertIn('chmod 600', report['reason'])
                self.assertIn(str(self.config), report['reason'])
                self.assertFalse(self.users.exists())
        self.write_config(f'prototype:\n  default_password: {SECRET}\n', 0o600)
        self.user('add', 'a@example.pl')

    @POSIX_ONLY
    def test_users_file_is_private_and_a_wider_one_is_refused(self):
        self.user('add', 'a@example.pl')
        self.assertEqual(stat.S_IMODE(os.stat(self.users).st_mode), 0o600)
        os.chmod(self.users, 0o644)
        before = self.users.read_bytes()
        for args in (('list',), ('add', 'b@example.pl'), ('remove', 'a@example.pl')):
            report = self.user(*args, code=2)
            self.assertIn('chmod 600', report['reason'], args)
        self.assertEqual(self.users.read_bytes(), before)
        os.chmod(self.users, 0o600)
        self.assertEqual(len(self.user('list')['users']), 1)


# ---------------------------------------------------------------------------
# m5: the default password never leaves the machine config through doctor/route
# ---------------------------------------------------------------------------
class DefaultPasswordLeakTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.config = self.base / 'machine.yaml'
        self.config.write_text(f'prototype:\n  default_password: {SECRET}\n', encoding='utf-8')
        os.chmod(self.config, 0o600)

    def run_cli(self, *args):
        environ = {**os.environ, 'SKILLSYNC_CONFIG': str(self.config)}
        result = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, timeout=60,
                                cwd=self.base, env=environ)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def test_control_the_value_is_in_the_config_and_the_scan_can_fail(self):
        self.assertIn(SECRET, self.config.read_text(encoding='utf-8'))
        probe = subprocess.run([sys.executable, '-c', 'import os; print(open(os.environ["SKILLSYNC_CONFIG"]).read())'],
                               capture_output=True, text=True, env={**os.environ, 'SKILLSYNC_CONFIG': str(self.config)})
        self.assertIn(SECRET, probe.stdout + probe.stderr, 'control: a command that prints the config is caught')

    def test_doctor_and_route_do_not_print_the_default_password(self):
        for command in ('doctor', 'route'):
            for extra in ((), ('--json',)):
                with self.subTest(command=command, extra=extra):
                    result = self.run_cli(command, str(self.root), *extra)
                    self.assertTrue(result.stdout.strip(), 'control: the command produced a report')
                    self.assertNotIn(SECRET, result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# serve: refusals and the v1 warning (K33)
# ---------------------------------------------------------------------------
class ServeTests(V1Fixture):
    def setUp(self):
        super().setUp()
        self.config = self.base / 'machine.yaml'
        self.config.write_text(f'prototype:\n  default_password: {SECRET}\n', encoding='utf-8')
        os.chmod(self.config, 0o600)
        self.env = {**os.environ, 'SKILLSYNC_CONFIG': str(self.config)}

    def serve_argv(self, *args):
        return [sys.executable, str(CLI), 'prototype', 'serve', str(self.root), '--port', '0', *args, '--json']

    def test_users_file_inside_the_served_root_is_refused_with_exit_2(self):
        for name in ('docs/prototypes/u.json', 'docs/prototypes/stary/u.json'):
            with self.subTest(users=name):
                result = subprocess.run(self.serve_argv('--users', name), capture_output=True, text=True, timeout=30,
                                        cwd=self.base, env=self.env)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertIn('użytkowników', ' '.join(report['errors']))
                self.assertIn(name, ' '.join(report['errors']))

    def test_missing_users_file_is_refused_with_exit_2(self):
        result = subprocess.run(self.serve_argv(), capture_output=True, text=True, timeout=30, cwd=self.base, env=self.env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'BLOCKED')

    @POSIX_ONLY
    def test_serve_starts_serves_a_v1_prototype_with_a_migrate_warning_and_stops_on_sigint(self):
        add = subprocess.run([sys.executable, str(CLI), 'prototype', 'user', 'add', str(self.root), 'dev@example.pl',
                              '--role', 'developer', '--json'], capture_output=True, text=True, timeout=60,
                             cwd=self.base, env=self.env)
        self.assertEqual(add.returncode, 0, add.stdout + add.stderr)
        before = tree(self.proto_dir('stary'))
        process = subprocess.Popen(self.serve_argv(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=self.base, env=self.env)
        watchdog = threading.Timer(30, process.kill)
        watchdog.start()
        lines = []
        try:
            for line in process.stderr:
                lines.append(line)
                if line.startswith('Serwer prototypów:'):
                    break
            else:
                self.fail('server did not announce its address: ' + ''.join(lines))
            process.send_signal(signal.SIGINT)
            stdout, rest = process.communicate(timeout=20)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.communicate()
        stderr = ''.join(lines) + rest
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(json.loads(stdout)['status'], 'PASS')
        self.assertRegex(stderr, r'prototyp v1, uruchom migrate: stary')
        self.assertNotIn(SECRET, stdout + stderr)
        self.assertNotIn('Traceback', stderr)
        self.assertEqual(tree(self.proto_dir('stary')), before, 'K33: serve does not migrate a v1 prototype')


# ---------------------------------------------------------------------------
# D28b (K8-K10): widok wątku w `comments`, `merge-comments` i po migracji v1
# ---------------------------------------------------------------------------
def view_create(number, thread, view='(brak)', at=None, text='uwaga'):
    operation = v2_create(number, thread, at or '2026-11-05T09:00:%02d.000Z' % number, text=text)
    if view != '(brak)':
        operation['payload']['view'] = view
    return operation


class ViewCommentsCliTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')

    def write_log(self, operations, name='comments.json', slug='demo'):
        (self.proto_dir() / name).write_text(v2_log(slug, operations), encoding='utf-8', newline='')

    def threads(self):
        report = self.proto('comments', 'demo')
        return {item['threadId']: item for item in report['threads']}

    def test_comments_reports_the_view_of_every_thread(self):
        self.write_log([view_create(1, 'th-d', 'desktop'), view_create(2, 'th-m', 'mobile'), view_create(3, 'th-n')])
        threads = self.threads()
        self.assertEqual({key: item['view'] for key, item in threads.items()},
                         {'th-d': 'desktop', 'th-m': 'mobile', 'th-n': 'desktop'})
        for item in threads.values():  # pole na poziomie wątku, nie w elemencie ani wiadomości
            self.assertNotIn('view', item['element'])
            self.assertTrue(all('view' not in message for message in item['messages']))

    def test_comments_treats_a_hand_written_invalid_view_as_desktop(self):
        self.write_log([view_create(1, 'th-t', 'tablet'), view_create(2, 'th-5', 5), view_create(3, 'th-0', None),
                        view_create(4, 'th-m', 'mobile')])
        self.assertEqual({key: item['view'] for key, item in self.threads().items()},
                         {'th-t': 'desktop', 'th-5': 'desktop', 'th-0': 'desktop', 'th-m': 'mobile'})

    def test_later_operations_keep_the_view(self):
        reply = {'id': 'op-0009', 'type': 'reply', 'threadId': 'th-m', 'at': '2026-11-05T10:00:00.000Z',
                 'by': 'ala@example.pl', 'payload': {'message': {'id': 'm-0009', 'text': 'odp'}}}
        vote = {'id': 'op-0010', 'type': 'vote', 'threadId': 'th-m', 'at': '2026-11-05T10:00:01.000Z',
                'by': 'ala@example.pl', 'payload': {'messageId': 'm-0001', 'value': 'up'}}
        self.write_log([view_create(1, 'th-m', 'mobile'), reply, vote])
        self.assertEqual(self.threads()['th-m']['view'], 'mobile')


class ViewMergeCommentsCliTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')
        self.base_ops = [view_create(1, 'th-1', 'desktop')]
        self.put(self.proto_dir() / 'comments.json', self.base_ops)

    def put(self, path, operations, slug='demo'):
        Path(path).write_text(v2_log(slug, operations), encoding='utf-8', newline='')

    def journal(self, path=None):
        text = (path or self.proto_dir() / 'comments.json').read_text(encoding='utf-8')
        return json.loads(text)['operations']

    def driver(self, ancestor, current, theirs, code=0):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'merge-comments', '--driver', str(ancestor),
                                 str(current), str(theirs), '--json'], capture_output=True, text=True, timeout=60,
                                cwd=str(self.root))
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_merge_comments_adds_a_create_with_view_without_losing_the_field(self):
        other = self.root / 'other.json'
        added = view_create(2, 'th-2', 'mobile')
        self.put(other, self.base_ops + [added])
        report = self.proto('merge-comments', 'demo', 'other.json')
        self.assertEqual((report['added'], report['total']), (1, 2))
        merged = self.journal()
        self.assertEqual(merged[-1], added)
        self.assertEqual(merged[-1]['payload']['view'], 'mobile')
        self.assertEqual(merged[0], self.base_ops[0])

    def test_git_driver_unions_both_sides_with_views_and_no_conflict(self):
        ancestor, current, theirs = self.root / 'O.json', self.root / 'A.json', self.root / 'B.json'
        mine_op, their_op = view_create(2, 'th-2', 'mobile'), view_create(3, 'th-3', 'desktop')
        self.put(ancestor, self.base_ops)
        self.put(current, self.base_ops + [mine_op])
        self.put(theirs, self.base_ops + [their_op])
        report = self.driver(ancestor, current, theirs)
        self.assertEqual(report['added'], 1)
        merged = {op['id']: op for op in self.journal(current)}
        self.assertEqual(set(merged), {'op-0001', 'op-0002', 'op-0003'})
        self.assertEqual(merged['op-0002']['payload']['view'], 'mobile')
        self.assertEqual(merged['op-0003']['payload']['view'], 'desktop')

    def test_git_driver_reports_a_conflict_when_the_same_id_has_a_different_view(self):
        ancestor, current, theirs = self.root / 'O.json', self.root / 'A.json', self.root / 'B.json'
        self.put(ancestor, [])
        self.put(current, [view_create(1, 'th-1', 'desktop')])
        self.put(theirs, [view_create(1, 'th-1', 'mobile')])
        before = current.read_bytes()
        report = self.driver(ancestor, current, theirs, code=2)
        self.assertEqual(report['error'], 'merge_conflict')
        self.assertEqual(current.read_bytes(), before)

    def test_git_driver_refuses_a_create_with_an_invalid_view_from_the_other_side(self):
        ancestor, current, theirs = self.root / 'O.json', self.root / 'A.json', self.root / 'B.json'
        self.put(ancestor, self.base_ops)
        self.put(current, self.base_ops)
        self.put(theirs, self.base_ops + [view_create(2, 'th-2', 'tablet')])
        before = current.read_bytes()
        report = self.driver(ancestor, current, theirs, code=2)
        self.assertEqual(report['error'], 'merge_conflict')
        self.assertEqual(current.read_bytes(), before)


class ViewAfterMigrateTests(V1Fixture):
    def test_threads_migrated_from_v1_fold_to_desktop(self):
        """Konwersję v1 -> v2 sprawdza MigrateTests; tu: żaden wątek nie dostaje widoku, fold daje `desktop`."""
        from skillsync_workflow import prototype_server as server
        self.migrate()
        creates = self.creates()
        self.assertTrue(creates, 'kontrola: migracja dała wątki')
        for operation in creates.values():
            self.assertNotIn('view', operation['payload'])
        folded = server.fold_operations(self.operations())
        threads = folded['threads'] if isinstance(folded, dict) else folded
        self.assertTrue(threads, 'kontrola: fold ma wątki')
        self.assertTrue({t['id'] for t in threads} <= set(creates))
        self.assertEqual({t['view'] for t in threads}, {'desktop'})
        listed = self.proto('comments', 'stary')['threads']
        self.assertTrue(listed, 'kontrola: comments ma wątki')
        self.assertTrue({item['threadId'] for item in listed} <= set(creates))
        self.assertEqual({item['view'] for item in listed}, {'desktop'})


if __name__ == '__main__':
    unittest.main()
