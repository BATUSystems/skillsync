"""`prototypes[]`, `commentsAfterAcceptance` and `result` of the dashboard contract (STATUS-160-A, docs/dashboard-contract.md).

The prototypes are built from real files: `index.html` with screens, `prototype.json` and a `comments.json` written with the server's
own serializer (operations with fixed `at`), the acceptance hash comes from `prototype_server.screen_hashes`.  Every negative case has
a control case that counts."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child  # noqa: E402
from skillsync_workflow import dashboard  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
T = '2026-11-05T10:00:00.000Z'      # acceptance time
BEFORE = '2026-11-05T09:00:00.000Z'
AFTER = '2026-11-05T11:00:00.000Z'
AFTER2 = '2026-11-05T12:00:00.000Z'
HEAD = ('<!doctype html>\n<html lang="pl" data-prototype-id="%s">\n<head>\n<meta charset="utf-8">\n<title>Demo</title>\n</head>\n<body>\n')
TAIL = '</body>\n</html>\n'


def screen(name, text='tekst'):
    return '<section class="screen" id="%s">\n  <div class="frame">%s</div>\n</section>\n' % (name, text)


def write_text(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text)


def op(op_id, kind, at, payload, thread=None, by='anna@example.com'):
    item = {'id': op_id, 'type': kind, 'at': at, 'by': by, 'payload': payload}
    if thread:
        item['threadId'] = thread
    return item


def create(thread, at, screen_id='s1', by='anna@example.com'):
    return op('c-' + thread, 'create', at, {'message': {'id': 'm-' + thread, 'text': 'Uwaga'}, 'screen': screen_id,
                                            'anchor': {'kind': 'screen', 'x': 0.5, 'y': 0.5, 'iteration': 1}}, thread, by)


def reply(thread, number, at, by='bartek@example.com'):
    return op('r-%s-%d' % (thread, number), 'reply', at, {'message': {'id': 'mr-%s-%d' % (thread, number), 'text': 'Odpowiedź'}}, thread, by)


class PrototypeCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-protos-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'demo'
        (self.root / 'docs' / 'prototypes').mkdir(parents=True)
        shutil.copy(FIXTURES / 'plan-in-progress' / 'input' / '.github' / '.skillsync.yaml', self.root / '.skillsync.yaml.tmp')
        (self.root / '.github').mkdir()
        os.replace(str(self.root / '.skillsync.yaml.tmp'), str(self.root / '.github' / '.skillsync.yaml'))

    def make(self, slug='alfa', screens=('s1', 's2')):
        directory = child(self.root / 'docs' / 'prototypes', slug)
        write_text(directory / 'prototype.json', '{"title": "Demo"}\n')
        write_text(directory / 'index.html', HEAD % slug + ''.join(screen(name) for name in screens) + TAIL)
        return directory

    def journal(self, directory, *operations):
        write_text(directory / 'comments.json', ps.serialize_document(directory.name, list(operations)))

    def accept(self, directory, screens=('s1',), at=T, op_id='acc-1'):
        hashes = ps.screen_hashes(str(directory))['screens']
        return op(op_id, 'accept', at, {'scope': 'screen', 'screens': {name: hashes[name] for name in screens},
                                         'iteration': 1, 'via': 'ui'})

    def after(self, directory):
        return dashboard._comments_after_acceptance(directory, directory.name)

    def overview(self, slug='alfa'):
        return dashboard.prototype_overview(self.root, slug)


class CommentsAfterAcceptance(PrototypeCase):
    def test_no_comments_file_is_zero(self):
        directory = self.make()
        self.assertEqual(self.after(directory), {'count': 0, 'screens': []})

    def test_message_after_valid_acceptance_counts_and_earlier_does_not(self):
        directory = self.make()
        self.journal(directory, self.accept(directory), create('t1', AFTER))
        self.assertEqual(self.after(directory), {'count': 1, 'screens': [{'id': 's1', 'count': 1}]})
        self.journal(directory, self.accept(directory), create('t1', BEFORE))
        self.assertEqual(self.after(directory), {'count': 0, 'screens': []})

    def test_reply_after_acceptance_counts_separately(self):
        directory = self.make()
        self.journal(directory, self.accept(directory), create('t1', AFTER), reply('t1', 1, AFTER2))
        self.assertEqual(self.after(directory), {'count': 2, 'screens': [{'id': 's1', 'count': 2}]})
        # an old opening message with a new reply: only the reply counts
        self.journal(directory, self.accept(directory), create('t1', BEFORE), reply('t1', 1, AFTER2))
        self.assertEqual(self.after(directory)['count'], 1)

    def test_decided_and_closed_threads_do_not_count(self):
        directory = self.make()
        base = [self.accept(directory), create('t1', AFTER)]
        self.journal(directory, *base)
        self.assertEqual(self.after(directory)['count'], 1, 'control: the open thread counts')
        decided = op('d1', 'decision', AFTER2, {'kind': 'developer', 'outcome': 'apply', 'who': 'anna@example.com'}, 't1')
        closed = op('x1', 'resolve', AFTER2, {'reason': 'ok'}, 't1')
        for finishing in (decided, closed):
            with self.subTest(finishing=finishing['type']):
                self.journal(directory, *base, finishing)
                self.assertEqual(self.after(directory), {'count': 0, 'screens': []})
        # reopening makes the thread count again (stateless)
        self.journal(directory, *base, closed, op('o1', 'reopen', '2026-11-05T13:00:00.000Z', {}, 't1'))
        self.assertEqual(self.after(directory)['count'], 1)

    def test_awaiting_thread_counts(self):
        directory = self.make()
        self.journal(directory, self.accept(directory), create('t1', AFTER),
                     op('tr1', 'triage', AFTER2, {'class': 'disputed', 'note': 'spór'}, 't1'))
        self.assertEqual(self.after(directory)['count'], 1)

    def test_invalid_acceptance_gives_zero(self):
        directory = self.make()
        self.journal(directory, self.accept(directory), create('t1', AFTER))
        self.assertEqual(self.after(directory)['count'], 1, 'control: valid acceptance')
        index = directory / 'index.html'
        write_text(index, index.read_text(encoding='utf-8').replace('tekst', 'zmieniony tekst', 1))
        self.assertEqual(self.after(directory), {'count': 0, 'screens': []})
        self.assertEqual(self.overview()['stale'], 1)

    def test_retracted_message_does_not_count(self):
        directory = self.make()
        base = [self.accept(directory), create('t1', AFTER), reply('t1', 1, AFTER2)]
        self.journal(directory, *base)
        self.assertEqual(self.after(directory)['count'], 2, 'control')
        retract = op('rt1', 'retract', '2026-11-05T13:00:00.000Z', {'messageId': 'mr-t1-1'}, 't1', by='bartek@example.com')
        self.journal(directory, *base, retract)
        self.assertEqual(self.after(directory)['count'], 1)

    def test_screen_without_acceptance_does_not_count(self):
        directory = self.make()
        self.journal(directory, self.accept(directory, ('s1',)), create('t1', AFTER, 's2'))
        self.assertEqual(self.after(directory), {'count': 0, 'screens': []})
        self.journal(directory, self.accept(directory, ('s1', 's2')), create('t1', AFTER, 's2'))
        self.assertEqual(self.after(directory), {'count': 1, 'screens': [{'id': 's2', 'count': 1}]})

    def test_new_acceptance_clears_the_count(self):
        directory = self.make()
        self.journal(directory, self.accept(directory), create('t1', AFTER))
        self.assertEqual(self.after(directory)['count'], 1)
        self.journal(directory, self.accept(directory), create('t1', AFTER),
                     self.accept(directory, at=AFTER2, op_id='acc-2'))
        self.assertEqual(self.after(directory)['count'], 0)

    def test_screens_are_sorted_with_per_screen_counts(self):
        directory = self.make()
        self.journal(directory, self.accept(directory, ('s1', 's2')), create('t2', AFTER, 's2'), create('t1', AFTER, 's1'),
                     reply('t1', 1, AFTER2))
        self.assertEqual(self.after(directory), {'count': 3, 'screens': [{'id': 's1', 'count': 2}, {'id': 's2', 'count': 1}]})


class PrototypesList(PrototypeCase):
    def build(self, writing=None):
        return dashboard.build_prototypes(self.root, lambda slug: dashboard.prototype_overview(self.root, slug), writing)

    def test_two_prototypes_sorted_by_slug_with_writing_probe(self):
        beta = self.make('beta')
        alfa = self.make('alfa')
        self.journal(alfa, self.accept(alfa), create('t1', AFTER))
        marker = {'since': 1, 'expiresAt': 2, 'renewedAt': 1}
        listed = self.build(lambda slug: marker if slug == 'beta' else None)
        self.assertEqual([item['slug'] for item in listed], ['alfa', 'beta'])
        self.assertEqual({key for key in listed[0]},
                         {'slug', 'iteration', 'screens', 'accepted', 'stale', 'threads', 'commentsAfterAcceptance', 'writing'})
        self.assertEqual((listed[0]['screens'], listed[0]['accepted'], listed[0]['stale']), (2, 1, 0))
        self.assertEqual(listed[0]['threads'].get('open'), 1)
        self.assertEqual(listed[0]['commentsAfterAcceptance'], {'count': 1, 'screens': [{'id': 's1', 'count': 1}]})
        self.assertIsNone(listed[0]['writing'])
        self.assertEqual(listed[1]['writing'], marker)
        self.assertEqual(listed[1]['commentsAfterAcceptance'], {'count': 0, 'screens': []})
        self.assertTrue(beta.is_dir())

    def test_without_probe_writing_is_none(self):
        self.make('alfa')
        self.assertIsNone(self.build()[0]['writing'])

    def test_directory_without_prototype_json_and_symlink_are_skipped(self):
        self.make('alfa')
        plain = self.root / 'docs' / 'prototypes' / 'plain'
        write_text(plain / 'index.html', HEAD % 'plain' + screen('s1') + TAIL)
        outside = Path(self.temp.name) / 'outside'
        shutil.copytree(self.root / 'docs' / 'prototypes' / 'alfa', outside)
        link = self.root / 'docs' / 'prototypes' / 'linked'
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
            linked = True
        except (OSError, NotImplementedError):
            linked = False
        self.assertEqual([item['slug'] for item in self.build()], ['alfa'])
        # control: the same directory with prototype.json is listed
        write_text(plain / 'prototype.json', '{}\n')
        self.assertEqual([item['slug'] for item in self.build()], ['alfa', 'plain'])
        if not linked:
            self.skipTest('symlinks are not available on this machine (the other checks ran)')

    def test_unreadable_comments_do_not_break_the_page(self):
        self.make('alfa')
        broken = self.make('beta')
        write_text(broken / 'comments.json', '{ to nie jest JSON')
        listed = self.build()
        self.assertIn('alfa', [item['slug'] for item in listed])
        status = dashboard.build_status(self.root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None,
                                        ahead=lambda *args: {})
        self.assertIn('alfa', [item['slug'] for item in status['prototypes']])

    def test_limit_is_50(self):
        for number in range(52):
            self.make('p%02d' % number)
        listed = self.build()
        self.assertEqual(len(listed), dashboard.PROTOTYPES_LIMIT)
        self.assertEqual(listed[0]['slug'], 'p00')

    def test_no_prototypes_directory(self):
        shutil.rmtree(self.root / 'docs' / 'prototypes')
        self.assertEqual(self.build(), [])

    def test_build_status_carries_prototypes_and_the_viewer_view_keeps_only_counts(self):
        alfa = self.make('alfa')
        self.journal(alfa, self.accept(alfa), create('t1', AFTER))
        status = dashboard.build_status(self.root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None,
                                        ahead=lambda *args: {})
        entry = next(item for item in status['prototypes'] if item['slug'] == 'alfa')
        self.assertEqual(entry['commentsAfterAcceptance']['count'], 1)


class ScreensDoneOnRealFiles(PrototypeCase):
    """T2 (review): `screensDone` comes from the real `index.html` (the acceptance count of `prototype list`), not from a mock."""

    def acceptance(self, slug='alfa'):
        return dashboard.prototype_acceptance(self.root, slug)

    def test_screens_of_a_real_index_html_are_counted(self):
        self.journal(self.make(screens=('s1', 's2', 's3')))
        self.assertEqual(self.acceptance()['screens'], 3)
        self.assertEqual(self.overview()['screens'], 3)

    def test_prototype_without_screens_counts_zero_and_a_missing_one_is_none(self):
        directory = self.make(screens=())
        self.assertIsNone(self.acceptance(), 'no comment log yet: no acceptance, so no screensDone (the page omits the key)')
        self.journal(directory)
        self.assertEqual(self.acceptance()['screens'], 0)
        self.assertIsNone(self.acceptance('nie-ma'))
        self.assertIsNone(dashboard.prototype_overview(self.root, 'nie-ma'))

    def test_marker_and_real_file_give_n_of_m(self):
        directory = self.make(screens=('s1', 's2'))
        self.journal(directory)
        now = 1_800_000_000_000
        ps.write_writing(str(directory), now, 900, None, 12)
        marker = dashboard.prototype_writing(self.root, 'alfa', now + 1000)
        self.assertEqual(marker['screensPlanned'], 12)
        self.assertEqual(self.acceptance()['screens'], 2)


class Result(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-result-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'demo'
        shutil.copytree(FIXTURES / 'plan-in-progress' / 'input', self.root)
        self.plan = next((self.root / 'docs' / 'plans').glob('*.md'))

    def edit(self, status=None, wynik=None):
        text = self.plan.read_text(encoding='utf-8')
        if status:
            text = text.replace('Status: in_progress', 'Status: ' + status)
        if wynik is not None:
            text = text.rstrip('\n') + '\n\n## Wynik\n' + wynik
        with open(self.plan, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)

    def result(self):
        return dashboard.build_status(self.root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None,
                                      ahead=lambda *args: {})['result']

    def test_in_progress_plan_with_section_gives_text_and_path(self):
        self.edit(wynik='Zrobione: A i B.\n')
        result = self.result()
        self.assertEqual(result['text'], 'Zrobione: A i B.')
        self.assertEqual(Path(result['path']).as_posix(), 'docs/plans/' + self.plan.name)

    def test_draft_plan_gives_none_but_in_progress_gives_result(self):
        self.edit(status='draft', wynik='Zrobione: A i B.\n')
        self.assertIsNone(self.result())
        self.plan.write_text(self.plan.read_text(encoding='utf-8').replace('Status: draft', 'Status: in_progress'), encoding='utf-8', newline='')
        self.assertIsNotNone(self.result(), 'control: the same section in a started plan')

    def test_empty_or_missing_section_gives_none(self):
        self.assertIsNone(self.result(), 'no section')
        self.edit(wynik='\n\n')
        self.assertIsNone(self.result(), 'empty section')

    def test_text_is_cut_to_4096_bytes(self):
        self.edit(wynik='ż' * 3000 + '\n')   # 6000 bytes
        text = self.result()['text']
        self.assertLessEqual(len(text.encode('utf-8')), dashboard.RESULT_MAX_BYTES)
        self.assertEqual(len(text.encode('utf-8')), 4096)
        self.assertEqual(set(text), {'ż'}, 'the cut does not split a character')


if __name__ == '__main__':
    unittest.main()
