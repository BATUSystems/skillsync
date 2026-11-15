"""The viewer's view (`dashboard_view.apply_viewer_view`, K18 of STATUS-160-A): no logins and no question contents for `approvals.viewer`."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skillsync_workflow import dashboard_view  # noqa: E402

DATA = {
    'approvals': {'here': False, 'viewer': True},
    'waiting': [{'what': 'GO plan', 'source': 'checkpoint', 'who': {'role': 'acceptor', 'login': 'jan.kowalski', 'label': 'akceptujący'}},
                {'what': 'x', 'source': 'plan'}],
    'expertQuestions': {'open': 1, 'blocking': 1,
                        'runs': [{'slug': 'demo', 'open': 1, 'blocking': 1, 'url': '/_experts/demo', 'addressees': ['ewa.nowak']}],
                        'items': [{'id': 'qn-1', 'runSlug': 'demo', 'text': 'Czy tak?', 'blocking': True, 'addressees': ['ewa.nowak'],
                                   'state': 'open', 'at': '2026-01-01T00:00:00Z', 'url': '/_experts/demo'}]},
    'run': {'checkpoints': [{'time': '10:00', 'text': 'czeka: GO plan (kto: akceptujący jan.kowalski); w toku: F1.1'}]},
    'timing': {'windows': [{'what': 'GO plan (kto: akceptujący jan.kowalski)'}]},
    'prototypes': [{'slug': 'demo', 'threads': {'open': 2, 'decided': 1, 'by': 'x', 'logins': ['a']}}],
}


class ViewerView(unittest.TestCase):
    def test_host_that_gives_consents_gets_the_same_object(self):
        self.assertIs(dashboard_view.apply_viewer_view(DATA, False), DATA)

    def test_suffix_with_nested_parentheses_and_unknown_role(self):
        text = 'czeka: GO (kto: expert bob (x)); dalej (kto: nieznana) i (kto: ekspert)'
        self.assertEqual(dashboard_view.apply_viewer_view({'run': {'checkpoints': [{'text': text}]}}, True)['run']['checkpoints'][0]['text'],
                         'czeka: GO (kto: expert); dalej (kto: nieznana) i (kto: ekspert)')

    def test_viewer_gets_no_logins_and_no_question_texts(self):
        before = copy.deepcopy(DATA)
        view = dashboard_view.apply_viewer_view(DATA, True)
        self.assertEqual(DATA, before, 'the shared answer is never filtered in place')
        self.assertIsNone(view['waiting'][0]['who']['login'])
        self.assertEqual(view['waiting'][0]['who']['role'], 'acceptor')
        self.assertEqual(view['run']['checkpoints'][0]['text'], 'czeka: GO plan (kto: akceptujący); w toku: F1.1')
        self.assertEqual(view['timing']['windows'][0]['what'], 'GO plan (kto: akceptujący)')
        run = view['expertQuestions']['runs'][0]
        self.assertEqual((run['addressees'], run['open'], run['blocking']), ([], 1, 1))
        item = view['expertQuestions']['items'][0]
        self.assertNotIn('text', item)
        self.assertEqual((item['addressees'], item['state'], item['blocking']), ([], 'open', True))
        self.assertEqual(view['prototypes'][0]['threads'], {'open': 2, 'decided': 1})
        text = repr(view)
        for secret in ('jan.kowalski', 'ewa.nowak', 'Czy tak?', "'by'", "'logins'"):
            self.assertNotIn(secret, text)

    def test_missing_sections_do_not_break_it(self):
        self.assertEqual(dashboard_view.apply_viewer_view({'approvals': {}}, True), {'approvals': {}})
        self.assertEqual(dashboard_view.apply_viewer_view({'waiting': [{'what': 'x'}], 'expertQuestions': {}}, True)['waiting'], [{'what': 'x'}])


if __name__ == '__main__':
    unittest.main()
