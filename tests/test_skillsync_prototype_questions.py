"""Magazyn pytań do eksperta (EXPERT-Q, E2): `QuestionsStore`, walidacja, fold, widoczność, scalanie, eksport.

Kryteria: .skillsync/runs/EXPERT-Q/spec.md (K3, K5 widoczność, K11, K13, K14, K15, K17, D5-D8, D13, D15,
„Kontrakty -> Plik pytań", „Widoczność i kolejność kontroli", „Kontrakt dla klienta zewnętrznego").
Testy dotyczą wyłącznie magazynu (bez HTTP; API i CLI w kolejnych krokach). Format operacji w asercjach jest
płaski (pola obok `id/by/cid/at`), tak jak w opisie operacji w specu. Kody błędów spoza specu (np. `withdrawn`)
są sprawdzane tylko statusem. Każdy test negatywny ma przypadek kontrolny, który przechodzi.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_questions.py`.
Zgodność ze składnią Pythona 3.8.
"""
import itertools
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import FakeClock, setUpModule, tearDownModule  # noqa: E402,F401

EWA = 'ewa.ekspert@example.com'
OLA = 'ola.ekspert@example.com'
IWO = 'iwo.ekspert@example.com'
DEV = 'dawid.dev@example.com'
ACC = 'adam.akceptor@example.com'
COM = 'kasia.komentuje@example.com'
Q1 = 'qn-000000000001'
Q2 = 'qn-000000000002'
Q3 = 'qn-000000000003'
Q4 = 'qn-000000000004'
Q5 = 'qn-000000000005'
NOON = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc).timestamp()


_COUNTER = itertools.count(1)


def _safe(login):
    return login.replace('@', '_')


def viewer(login, *roles):
    return {'login': login, 'roles': list(roles)}


def ask(qid=Q1, cid=None, **over):
    op = {'type': 'ask', 'questionId': qid, 'by': 'agent', 'cid': cid or 'ask-' + qid, 'text': 'Czy faktura korygująca nosi nowy numer?',
          'blocking': False, 'assumed': 'tak', 'addressees': 'all', 'refs': [], 'options': [],
          'origin': {'spec': 'docs/specs/a.md', 'q': 'Q1'}}
    op.update(over)
    return op


def answer(qid=Q1, by=EWA, cid=None, **over):
    op = {'type': 'answer', 'questionId': qid, 'by': by, 'cid': cid or 'ans-%s-%s' % (qid, _safe(by))}
    if not any(key in over for key in ('optionId', 'optionIds', 'text', 'confirm')):
        op['text'] = 'tak, nowy numer'
    op.update(over)
    return op


def comment(qid=Q1, by=EWA, cid=None, text='uwaga', **over):
    op = {'type': 'comment', 'questionId': qid, 'by': by, 'cid': cid or 'com-%s-%s-%d' % (qid, _safe(by), next(_COUNTER)), 'text': text}
    op.update(over)
    return op


def withdraw(qid=Q1, by='agent', cid=None):
    return {'type': 'withdraw', 'questionId': qid, 'by': by, 'cid': cid or 'wd-' + qid}


def recorded(qid=Q1, by='agent', cid=None, spec='docs/specs/a.md', line=12):
    return {'type': 'recorded', 'questionId': qid, 'by': by, 'cid': cid or 'rec-' + qid, 'spec': spec, 'line': line}


def full(partial, n=1):
    """Pełna operacja do `validate_question_operation` (id i at nadaje magazyn, tu ręcznie)."""
    op = dict(partial)
    op['id'] = 'op-test%03d' % n
    op['at'] = '2026-11-11T12:00:00.%03dZ' % n
    return op


def code_of(test, call, status=None):
    with test.assertRaises(ps.PrototypeServerError) as caught:
        call()
    if status is not None:
        test.assertEqual(caught.exception.status, status, caught.exception.code)
    return caught.exception.code


class QuestionsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='proto-questions-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.directory = os.path.join(self.tmp, 'root')   # celowo nieistniejący: tworzy go pierwszy zapis
        self.clock = FakeClock(NOON)
        self.store = ps.QuestionsStore(self.directory, clock=self.clock)

    def new_store(self, **kwargs):
        return ps.QuestionsStore(self.directory, clock=self.clock, **kwargs)

    def add(self, *partials):
        return self.store.append(list(partials))

    def file_bytes(self):
        with open(os.path.join(self.directory, 'questions.json'), 'rb') as handle:
            return handle.read()

    def file_ops(self):
        return json.loads(self.file_bytes().decode('utf-8'))['operations']

    def state(self, qid=Q1):
        return self.store.snapshot().fold['byId'][qid]

    def write_ops(self, operations):
        os.makedirs(self.directory, exist_ok=True)
        with open(os.path.join(self.directory, 'questions.json'), 'wb') as handle:
            handle.write(ps.serialize_questions(operations).encode('utf-8'))

    def fill(self, target_bytes):
        """Wpisuje wprost poprawne pytania, aż plik ma co najmniej `target_bytes` (omija limit `ask`)."""
        operations, index = [], 0
        while len(ps.serialize_questions(operations).encode('utf-8')) < target_bytes:
            index += 1
            op = ask('qn-%012x' % (0x100 + index), refs=[{'title': 't', 'href': 'docs/a.md', 'excerpt': 'x' * 4000}] * 5)
            operations.append(full(op, index % 900 + 1))
            operations[-1]['id'] = 'op-fill%05d' % index
        self.write_ops(operations)
        return len(self.file_bytes())


# ── walidacja ───────────────────────────────────────────────────────────────

class ValidationTests(unittest.TestCase):
    def check(self, op, known=None):
        ps.validate_question_operation(full(op), known)

    def reject(self, op, known=None, code=None):
        got = code_of(self, lambda: self.check(op, known), 400)
        if code is not None:
            self.assertEqual(got, code)
        return got

    def test_valid_operation_of_every_type_passes(self):
        for op in (ask(), ask(blocking=True, assumed=None), answer(), comment(), recorded(), withdraw()):
            self.check(op)
        self.check(answer(optionId='o1'), {Q1: {'options': [{'id': 'o1', 'label': 'a'}], 'assumed': 'x'}})
        self.check(answer(confirm=True), {Q1: {'options': [], 'assumed': 'x'}})

    def test_unknown_type_field_and_number(self):
        for bad in ('edit', 'invalidate', 'retract', 'reopen', '', None, 5):
            self.reject(dict(ask(), type=bad))
        self.assertEqual(self.reject(dict(ask(), type='edit')), 'invalid_type')
        self.reject(dict(ask(), number=3))          # rekord nie ma pola `number`
        self.reject(dict(ask(), extra='x'))
        self.reject(dict(answer(), extra='x'))
        self.reject(dict(comment(), optionId='o1'))
        self.reject(dict(withdraw(), text='x'))
        self.check(ask())                            # kontrola

    def test_missing_required_fields(self):
        for key in ('questionId', 'by', 'cid', 'text', 'blocking', 'assumed', 'addressees', 'refs', 'options', 'origin'):
            op = ask()
            del op[key]
            self.reject(op)
        for key in ('text',):
            op = comment()
            del op[key]
            self.reject(op)
        for key in ('spec', 'line'):
            op = recorded()
            del op[key]
            self.reject(op)
        for op in (ask(), comment(), answer(), recorded(), withdraw()):
            broken = full(op)
            del broken['id']
            self.assertRaises(ps.PrototypeServerError, ps.validate_question_operation, broken)

    def test_wrong_types(self):
        self.reject(ask(blocking='false'))
        self.reject(ask(blocking=1))
        self.reject(ask(refs={}))
        self.reject(ask(options='x'))
        self.reject(ask(text=7))
        self.reject(ask(origin='docs/a.md'))
        self.reject(ask(addressees='everyone'))
        self.reject(ask(addressees=[]))
        self.reject(ask(addressees=[5]))
        self.reject(recorded(line='3'))
        self.reject(recorded(line=1.5))
        self.reject(ask(questionId='q-1'))
        self.reject(ask(questionId='qn-XYZ'))
        self.check(ask(addressees=[EWA]))

    def test_utf16_limits_at_boundary_and_one_over(self):
        cases = [
            ('ask.text', lambda s: ask(text=s), 2000),
            ('ask.assumed', lambda s: ask(assumed=s), 500),
            ('ref.title', lambda s: ask(refs=[{'title': s, 'href': 'docs/a.md', 'excerpt': 'e'}]), 200),
            ('ref.excerpt', lambda s: ask(refs=[{'title': 't', 'href': 'docs/a.md', 'excerpt': s}]), 4000),
            ('option.label', lambda s: ask(options=[{'id': 'o1', 'label': s}]), 200),
            ('answer.text', lambda s: answer(text=s), 4000),
            ('answer.comment', lambda s: answer(text='t', comment=s), 2000),
            ('comment.text', lambda s: comment(text=s), 2000),
        ]
        for name, build, limit in cases:
            with self.subTest(name):
                self.check(build('a' * limit))
                self.assertEqual(self.reject(build('a' * (limit + 1))), 'text_too_long')
                # znaki spoza BMP liczą się podwójnie (jednostki UTF-16)
                self.check(build('\U0001F600' * (limit // 2)))
                self.assertEqual(self.reject(build('\U0001F600' * (limit // 2) + 'a' * 1 + 'b')), 'text_too_long')

    def test_href_limit(self):
        base = 'https://example.com/'
        self.check(ask(refs=[{'title': 't', 'href': base + 'a' * (500 - len(base)), 'excerpt': 'e'}]))
        self.reject(ask(refs=[{'title': 't', 'href': base + 'a' * (501 - len(base)), 'excerpt': 'e'}]), code='text_too_long')

    def test_refs_options_counts_and_recommended(self):
        ref = {'title': 't', 'href': 'docs/a.md', 'excerpt': 'e'}
        self.check(ask(refs=[ref] * 5))
        self.reject(ask(refs=[ref] * 6))
        opts = lambda n: [{'id': 'o%d' % i, 'label': 'etykieta %d' % i} for i in range(n)]   # noqa: E731
        self.check(ask(options=opts(8)))
        self.reject(ask(options=opts(9)))
        rec = opts(3)
        rec[1]['recommended'] = True
        self.check(ask(options=rec))
        rec[2]['recommended'] = True
        self.reject(ask(options=rec))
        rec[2]['recommended'] = False                # false nie liczy się jako rekomendacja
        self.check(ask(options=rec))

    def test_href_relative_or_https_only(self):
        for href in ('https://example.com/x', 'docs/a.md', 'docs/a.md#sekcja', '../docs/a.md'):
            self.check(ask(refs=[{'title': 't', 'href': href, 'excerpt': 'e'}]))
        for href in ('javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'http://example.com/x', 'data:text/html,x',
                     'ftp://example.com/x', 'file:///etc/passwd', '//example.com/x', 'vbscript:x', ' javascript:x', ''):
            with self.subTest(href):
                self.reject(ask(refs=[{'title': 't', 'href': href, 'excerpt': 'e'}]))

    def test_origin_spec_is_a_safe_relative_path(self):
        self.check(ask(origin={'spec': 'docs/specs/a.md', 'q': 'Q12'}))
        for spec in ('../a.md', 'docs/../../a.md', 'a/../b.md', '/etc/passwd', 'https://example.com/a.md',
                     'file:a.md', 'docs\\a.md', 'C:/a.md', ''):
            with self.subTest(spec):
                self.reject(ask(origin={'spec': spec, 'q': 'Q1'}))
        for q in ('q1', 'Q', 'Q1x', '1', ''):
            with self.subTest(q):
                self.reject(ask(origin={'spec': 'docs/a.md', 'q': q}))
        self.reject(ask(origin={'spec': 'docs/a.md'}))
        self.reject(ask(origin={'spec': 'docs/a.md', 'q': 'Q1', 'extra': 1}))
        self.reject(recorded(spec='../a.md'))
        self.reject(recorded(spec='/a.md'))
        self.check(recorded(spec='docs/a.md'))

    def test_blocking_xor_assumed(self):
        self.check(ask(blocking=False, assumed='x'))
        self.check(ask(blocking=True, assumed=None))
        self.reject(ask(blocking=True, assumed='x'))    # blokujące nie niesie wartości przyjętej
        self.reject(ask(blocking=False, assumed=None))  # bez założenia musi blokować (K13)

    def test_answer_xor_optionid_text_confirm(self):
        known = {Q1: {'options': [{'id': 'o1', 'label': 'a'}], 'assumed': 'x'}}
        self.check(answer(optionId='o1'), known)
        self.check(answer(confirm=True), known)
        self.check(answer(text='własna'), known)
        for combo in (('optionId', 'o1', 'text', 'x'), ('optionId', 'o1', 'confirm', True), ('text', 'x', 'confirm', True)):
            op = answer(**{combo[0]: combo[1], combo[2]: combo[3]})
            self.reject(op, known)
        bare = answer()
        del bare['text']
        self.reject(bare, known)
        self.reject(answer(confirm=False), known)
        self.reject(answer(confirm='yes'), known)
        self.reject(answer(optionId='nie-ma'), known, code='unknown_option')

    def test_confirm_only_with_assumed(self):
        self.check(answer(confirm=True), {Q1: {'options': [], 'assumed': 'wartość'}})
        self.reject(answer(confirm=True), {Q1: {'options': [], 'assumed': None}})

    def test_answer_by_agent_is_rejected(self):
        self.reject(answer(by='agent'))
        self.check(answer(by=EWA))

    def test_unhashable_type_and_question_id_are_validation_errors(self):
        for bad in ([], {}, ['ask'], {'a': 1}):
            self.reject(dict(ask(), type=bad))
            self.reject(dict(ask(), questionId=bad))
            self.reject(dict(comment(), questionId=bad))

    def test_line_must_be_an_int_not_a_bool(self):
        self.check(recorded(line=3))
        self.reject(recorded(line=True))
        self.reject(recorded(line=False))

    def test_refers_to_must_exist_when_known(self):
        known = {Q1: {'options': [], 'assumed': 'x'}}
        self.check(ask(Q2, refersTo=Q1), known)
        self.assertEqual(self.reject(ask(Q2, refersTo=Q3), known), 'unknown_reference')
        self.reject(ask(Q2, refersTo=Q2), known)
        self.reject(ask(Q2, refersTo='x'), known)


# ── magazyn: zapis, dedup, stany ────────────────────────────────────────────

class StoreBasicsTests(QuestionsCase):
    def test_root_missing_until_first_write_then_created(self):
        snap = self.store.snapshot()
        self.assertFalse(snap.exists)
        self.assertEqual(snap.operations, [])
        self.assertFalse(os.path.exists(self.directory), 'odczyt nie tworzy katalogu root')
        self.add(ask())
        self.assertTrue(os.path.isfile(os.path.join(self.directory, 'questions.json')))

    def test_file_shape_ids_and_monotonic_at(self):
        self.add(ask(), ask(Q2))
        self.add(comment(Q1))
        doc = json.loads(self.file_bytes().decode('utf-8'))
        self.assertEqual(doc['questionsFormat'], 1)
        ops = doc['operations']
        self.assertEqual(len(ops), 3)
        for op in ops:
            self.assertRegex(op['id'], r'^op-')
            self.assertRegex(op['at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')
        self.assertEqual([op['at'] for op in ops], sorted(set(op['at'] for op in ops)), 'at rośnie ściśle, także przy stałym zegarze')
        self.assertEqual(len(set(op['id'] for op in ops)), 3)
        self.assertNotIn('number', ops[0])

    def test_atomic_write_leaves_no_tmp_file_and_valid_json(self):
        self.add(ask())
        self.add(answer())
        names = os.listdir(self.directory)
        self.assertEqual([n for n in names if n.endswith('.tmp')], [])
        self.assertIn('questions.json', names)
        json.loads(self.file_bytes().decode('utf-8'))

    def test_questions_file_mode_follows_umask_not_0600(self):
        self.add(ask())
        mode = os.stat(os.path.join(self.directory, 'questions.json')).st_mode & 0o777
        self.assertEqual(mode, 0o644 & ~ps.current_umask())

    def test_symlink_as_questions_file_is_refused_and_not_followed(self):
        os.makedirs(self.directory)
        target = os.path.join(self.tmp, 'poza.json')
        with open(target, 'wb') as handle:
            handle.write(ps.serialize_questions([]).encode('utf-8'))
        os.symlink(target, os.path.join(self.directory, 'questions.json'))
        with self.assertRaises(ps.QuestionsFileError):
            self.new_store().snapshot()
        code_of(self, lambda: self.new_store().append([ask()]), 409)
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), ps.serialize_questions([]).encode('utf-8'))

    def test_failed_transaction_on_missing_root_does_not_create_it(self):
        code_of(self, lambda: self.add(answer(Q3)), 404)
        code_of(self, lambda: self.add(ask(Q1, text='')), 400)
        self.assertFalse(os.path.exists(self.directory))
        self.add(ask())                              # kontrola: zapis tworzy katalog
        self.assertTrue(os.path.isdir(self.directory))

    def test_failed_write_keeps_old_file(self):
        self.add(ask())
        before = self.file_bytes()
        from unittest import mock
        with mock.patch.object(ps.os, 'replace', side_effect=OSError('przerwanie')):
            with self.assertRaises(OSError):
                self.add(comment())
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual([n for n in os.listdir(self.directory) if n.endswith('.tmp')], [])
        self.add(comment())                         # kontrola: bez przerwania przechodzi
        self.assertNotEqual(self.file_bytes(), before)

    def test_corrupted_file_is_never_overwritten(self):
        os.makedirs(self.directory)
        path = os.path.join(self.directory, 'questions.json')
        for junk in (b'{not json', b'[]', b'{"questionsFormat": 2, "operations": []}', b'{"questionsFormat": 1}',
                     b'{"questionsFormat": 1, "operations": [5]}', b'\xff\xfe\x00'):
            with open(path, 'wb') as handle:
                handle.write(junk)
            with self.subTest(junk=junk):
                code_of(self, lambda: self.new_store().append([ask()]), 409)
                with open(path, 'rb') as handle:
                    self.assertEqual(handle.read(), junk)
        os.remove(path)
        self.new_store().append([ask()])            # kontrola: poprawny (brakujący) plik przyjmuje zapis

    def test_invalid_operation_is_not_written(self):
        self.add(ask())
        before = self.file_bytes()
        code_of(self, lambda: self.add(ask(Q2, number=4)), 400)
        code_of(self, lambda: self.add(comment(text='')), 400)
        self.assertEqual(self.file_bytes(), before)

    def test_unknown_question_is_404(self):
        for partial in (answer(Q3), comment(Q3), withdraw(Q3), recorded(Q3)):
            self.assertEqual(code_of(self, lambda p=partial: self.add(p), 404), 'not_found')
        self.assertFalse(os.path.exists(os.path.join(self.directory, 'questions.json')))

    def test_ask_with_existing_id_is_rejected(self):
        self.add(ask())
        code_of(self, lambda: self.add(ask(Q1, cid='inny-cid')), 409)
        self.assertEqual(len(self.file_ops()), 1)

    def test_ask_blocking_versus_assumed_roundtrip(self):
        self.add(ask(Q1, blocking=False, assumed='tak'), ask(Q2, blocking=True, assumed=None))
        self.assertFalse(self.state(Q1)['blocking'])
        self.assertEqual(self.state(Q1)['assumed'], 'tak')
        self.assertTrue(self.state(Q2)['blocking'])
        self.assertIsNone(self.state(Q2)['assumed'])
        code_of(self, lambda: self.add(ask(Q3, blocking=True, assumed='x')), 400)
        code_of(self, lambda: self.add(ask(Q3, blocking=False, assumed=None)), 400)

    def test_refers_to_existing_ok_missing_error_and_old_answer_stays(self):
        self.add(ask(Q1), answer(Q1, EWA, text='stara odpowiedź'))
        self.add(ask(Q2, refersTo=Q1))
        self.assertEqual(self.state(Q2)['refersTo'], Q1)
        self.assertEqual(code_of(self, lambda: self.add(ask(Q3, refersTo=Q5)), 400), 'unknown_reference')
        self.assertEqual(self.state(Q1)['answer']['text'], 'stara odpowiedź')
        self.assertEqual(self.state(Q1)['state'], 'answered')
        exported = ps.export_questions(self.store.snapshot(), 'root')
        first = [q for q in exported['questions'] if q['id'] == Q1][0]
        self.assertEqual(first['answer']['text'], 'stara odpowiedź')

    def test_no_operation_edits_or_invalidates_an_answer(self):
        self.add(ask(), answer())
        self.assertEqual(sorted(ps.QUESTION_TYPES), ['answer', 'ask', 'comment', 'recorded', 'withdraw'])
        for kind in ('edit', 'edit_answer', 'invalidate', 'retract', 'revoke', 'reopen', 'unanswer'):
            code_of(self, lambda k=kind: self.add({'type': k, 'questionId': Q1, 'by': EWA, 'cid': 'x-' + k, 'text': 'z'}), 400)
        self.assertEqual(self.state()['answer']['text'], 'tak, nowy numer')


class DedupAndClosingTests(QuestionsCase):
    def setUp(self):
        QuestionsCase.setUp(self)
        self.add(ask(Q1, options=[{'id': 'o1', 'label': 'A', 'recommended': True}, {'id': 'o2', 'label': 'B'}]), ask(Q2))

    def test_same_by_cid_same_question_is_idempotent(self):
        first = self.add(ask(Q3, cid='staly'))
        again = self.add(ask(Q3, cid='staly'))
        self.assertEqual(len(first.operations), 1)
        self.assertEqual(again.operations, [])
        self.assertEqual(again.existing['id'], first.operations[0]['id'])
        self.assertEqual(len(self.file_ops()), 3)

    def test_same_by_cid_with_another_type_is_cid_conflict(self):
        self.add(ask(Q3, cid='X'))
        before = self.file_bytes()
        for partial in (withdraw(Q3, by='agent', cid='X'), comment(Q3, 'agent', cid='X')):
            self.assertEqual(code_of(self, lambda p=partial: self.add(p), 409), 'cid_conflict')
        self.assertEqual(self.file_bytes(), before)
        again = self.add(ask(Q3, cid='X'))           # kontrola: powtórka tego samego ask jest idempotentna
        self.assertEqual(again.operations, [])
        self.assertIsNotNone(again.existing)

    def test_ask_with_cid_used_on_another_question_is_cid_conflict(self):
        self.add(ask(Q3, cid='zajety'))
        self.assertEqual(code_of(self, lambda: self.add(ask(Q4, cid='zajety')), 409), 'cid_conflict')
        self.assertNotIn(Q4, self.store.snapshot().fold['byId'])

    def test_same_cid_on_another_question_is_cid_conflict(self):
        self.add(comment(Q1, EWA, cid='wspolny'))
        before = self.file_bytes()
        self.assertEqual(code_of(self, lambda: self.add(comment(Q2, EWA, cid='wspolny')), 409), 'cid_conflict')
        self.assertEqual(self.file_bytes(), before)
        self.add(comment(Q2, OLA, cid='wspolny'))   # kontrola: dedup jest per (by, cid)

    def test_first_answer_closes_second_author_is_409(self):
        self.add(answer(Q1, EWA, optionId='o1'))
        self.assertEqual(self.state()['state'], 'answered')
        self.assertEqual(self.state()['answer']['by'], EWA)
        before = self.file_bytes()
        self.assertEqual(code_of(self, lambda: self.add(answer(Q1, OLA, optionId='o2')), 409), 'already_answered')
        self.assertEqual(code_of(self, lambda: self.add(answer(Q1, EWA, cid='nowy-cid', text='druga')), 409), 'already_answered')
        self.assertEqual(self.file_bytes(), before)

    def test_repeating_the_same_answer_is_idempotent_even_after_closing(self):
        first = self.add(answer(Q1, EWA, cid='a1', optionId='o1'))
        again = self.add(answer(Q1, EWA, cid='a1', optionId='o1'))
        self.assertEqual(again.operations, [])
        self.assertEqual(again.existing['id'], first.operations[0]['id'])
        self.assertEqual(len(self.file_ops()), 3)

    def test_option_confirm_and_text_answers(self):
        self.add(answer(Q1, EWA, optionId='o2', comment='bo tak'))
        self.assertEqual(self.state()['answer']['optionId'], 'o2')
        self.assertEqual(self.state()['answer']['comment'], 'bo tak')
        code_of(self, lambda: self.add(answer(Q3, EWA, optionId='o1')), 404)
        self.add(ask(Q3, blocking=True, assumed=None, options=[{'id': 'o1', 'label': 'A'}]))
        self.assertEqual(code_of(self, lambda: self.add(answer(Q3, EWA, optionId='zly')), 400), 'unknown_option')
        code_of(self, lambda: self.add(answer(Q3, EWA, confirm=True)), 400)      # confirm bez założenia
        self.assertEqual(self.state(Q3)['state'], 'open')
        self.add(answer(Q2, EWA, confirm=True))                                   # kontrola: z założeniem
        self.assertTrue(self.state(Q2)['answer']['confirm'])

    def test_comment_is_allowed_after_closing(self):
        self.add(answer(Q1, EWA, optionId='o1'))
        self.add(comment(Q1, OLA, text='po zamknięciu'), comment(Q1, DEV, text='dev też'))
        self.assertEqual([c['text'] for c in self.state()['comments']], ['po zamknięciu', 'dev też'])
        self.assertEqual(self.state()['state'], 'answered')

    def test_withdraw_open_ok_blocking_too(self):
        self.add(ask(Q3, blocking=True, assumed=None))
        self.add(withdraw(Q3))
        self.assertEqual(self.state(Q3)['state'], 'withdrawn')
        self.add(withdraw(Q2, by=DEV))
        self.assertEqual(self.state(Q2)['state'], 'withdrawn')

    def test_withdraw_after_answer_is_rejected(self):
        self.add(answer(Q1, EWA, optionId='o1'))
        before = self.file_bytes()
        code_of(self, lambda: self.add(withdraw(Q1)), 409)
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(self.state()['state'], 'answered')

    def test_answer_after_withdraw_is_rejected(self):
        self.add(withdraw(Q2))
        before = self.file_bytes()
        code_of(self, lambda: self.add(answer(Q2, EWA, text='za późno')), 409)
        self.assertEqual(self.file_bytes(), before)
        self.assertIsNone(self.state(Q2)['answer'])

    def test_recorded_after_answer_is_kept_once(self):
        self.add(answer(Q1, EWA, optionId='o1'))
        self.add(recorded(Q1, spec='docs/specs/a.md', line=14))
        again = self.add(recorded(Q1, cid='inny', spec='docs/specs/a.md', line=99))
        self.assertEqual(again.operations, [])        # idempotentnie: pierwszy zapis zostaje
        self.assertEqual(self.state()['recorded']['line'], 14)
        self.assertEqual(self.state()['recorded']['spec'], 'docs/specs/a.md')
        self.assertEqual(len([o for o in self.file_ops() if o['type'] == 'recorded']), 1)

    def test_recorded_before_answer_is_not_accepted(self):
        code_of(self, lambda: self.add(recorded(Q2)), 409)
        self.assertEqual(self.state(Q2)['state'], 'open')
        self.assertIsNone(self.state(Q2)['recorded'])


# ── limity rozmiaru pliku ───────────────────────────────────────────────────

class FileSizeTests(QuestionsCase):
    def test_limits_are_768_kib_and_1_mib(self):
        self.assertEqual(ps.QUESTIONS_ASK_LIMIT_BYTES, 768 * 1024)
        self.assertEqual(ps.QUESTIONS_MAX_FILE_BYTES, 1024 * 1024)

    def test_ask_refused_above_768_kib_answer_and_comment_use_the_reserve(self):
        self.fill(770 * 1024)
        before = self.file_bytes()
        self.assertEqual(code_of(self, lambda: self.add(ask(Q1)), 507), 'file_too_large')
        self.assertEqual(self.file_bytes(), before)
        target = 'qn-%012x' % 0x101
        self.add(answer(target, EWA, text='odpowiedź w rezerwie'))
        self.add(comment(target, OLA, text='komentarz w rezerwie'))
        self.assertGreater(len(self.file_bytes()), len(before))
        self.assertLessEqual(len(self.file_bytes()), 1024 * 1024)

    def test_below_768_kib_ask_is_accepted(self):
        self.fill(700 * 1024)
        self.add(ask(Q1))                           # kontrola przypadku przeciwnego
        self.assertEqual(self.state(Q1)['state'], 'open')

    def test_limit_boundary_exactly_limit_ok_one_over_is_507(self):
        self.write_ops([full(ask(Q1), 1), full(ask(Q2), 2)])
        size = len(self.file_bytes())
        self.add(comment(Q1, EWA, cid='c1'))         # kontrola: bez limitu przechodzi
        self.write_ops([full(ask(Q1), 1), full(ask(Q2), 2)])
        self.store = self.new_store(max_bytes=size)
        self.add(comment(Q1, EWA, cid='c2'))        # plik ma dokładnie limit: OK
        self.write_ops([full(ask(Q1), 1), full(ask(Q2), 2)])
        self.store = self.new_store(max_bytes=size - 1)
        self.assertEqual(code_of(self, lambda: self.add(comment(Q1, EWA, cid='c3')), 507), 'file_too_large')

    def test_ask_limit_boundary_is_strictly_above(self):
        from unittest import mock
        self.write_ops([full(ask(Q1), 1)])
        size = len(self.file_bytes())
        with mock.patch.object(ps, 'QUESTIONS_ASK_LIMIT_BYTES', size):
            self.add(ask(Q2))                        # plik ma dokładnie limit: OK
        self.write_ops([full(ask(Q1), 1)])
        with mock.patch.object(ps, 'QUESTIONS_ASK_LIMIT_BYTES', size - 1):
            self.assertEqual(code_of(self, lambda: self.add(ask(Q2)), 507), 'file_too_large')

    def test_ask_limit_counts_operations_added_in_the_same_transaction(self):
        from unittest import mock
        self.write_ops([full(ask(Q1), 1)])
        size = len(self.file_bytes())
        with mock.patch.object(ps, 'QUESTIONS_ASK_LIMIT_BYTES', size):
            self.assertEqual(code_of(self, lambda: self.add(ask(Q2), ask(Q3)), 507), 'file_too_large')
        self.assertEqual(len(self.file_ops()), 1)

    def test_comment_limit_constant_and_boundary(self):
        from unittest import mock
        self.assertEqual(ps.QUESTIONS_COMMENT_LIMIT_BYTES, 896 * 1024)
        self.write_ops([full(ask(Q1), 1)])
        size = len(self.file_bytes())
        with mock.patch.object(ps, 'QUESTIONS_COMMENT_LIMIT_BYTES', size):
            self.add(comment(Q1, EWA, cid='c1'))     # plik ma dokładnie limit: OK
        self.write_ops([full(ask(Q1), 1)])
        with mock.patch.object(ps, 'QUESTIONS_COMMENT_LIMIT_BYTES', size - 1):
            self.assertEqual(code_of(self, lambda: self.add(comment(Q1, EWA, cid='c2')), 507), 'file_too_large')
            self.add(answer(Q1, EWA, text='odpowiedź nadal wchodzi'))   # answer nie podlega progowi komentarza

    def test_above_896_kib_comment_refused_answer_recorded_withdraw_still_fit(self):
        self.fill(897 * 1024)
        before = self.file_bytes()
        self.assertGreater(len(before), 896 * 1024)
        t1, t2, t3 = ('qn-%012x' % (0x100 + n) for n in (1, 2, 3))
        self.assertEqual(code_of(self, lambda: self.add(comment(t1, OLA, text='x')), 507), 'file_too_large')
        self.assertEqual(self.file_bytes(), before)
        self.add(answer(t1, EWA, text='odpowiedź w rezerwie'))
        self.add(recorded(t1))
        self.add(withdraw(t2))
        self.assertEqual(self.state(t1)['state'], 'answered')
        self.assertIsNotNone(self.state(t1)['recorded'])
        self.assertEqual(self.state(t2)['state'], 'withdrawn')
        self.assertEqual(code_of(self, lambda: self.add(ask(Q1)), 507), 'file_too_large')
        self.assertEqual(self.state(t3)['state'], 'open')

    def test_answer_fits_when_comments_stopped_at_the_threshold(self):
        # symulacja pełnego pliku: komentarze przyjmowane aż do progu 896 KiB, potem odpowiedź i recorded
        self.fill(880 * 1024)
        target = 'qn-%012x' % 0x101
        accepted = 0
        while True:
            try:
                self.add(comment(target, OLA, text='k' * 2000, cid='fill-%d' % accepted))
            except ps.PrototypeServerError as error:
                self.assertEqual((error.status, error.code), (507, 'file_too_large'))
                break
            accepted += 1
            self.assertLess(accepted, 400)
        self.assertGreater(accepted, 0)
        self.assertLessEqual(len(self.file_bytes()), 896 * 1024 + 4096)
        self.add(answer(target, EWA, text='x' * 4000, comment='y' * 2000))
        self.add(recorded(target))
        self.assertLessEqual(len(self.file_bytes()), 1024 * 1024)

    def test_everything_refused_at_or_above_1_mib(self):
        self.fill(1024 * 1024 + 1)
        before = self.file_bytes()
        target = 'qn-%012x' % 0x101
        for partial in (answer(target, EWA, text='x'), comment(target, EWA, text='x'), ask(Q1)):
            self.assertEqual(code_of(self, lambda p=partial: self.add(p), 507), 'file_too_large')
        self.assertEqual(self.file_bytes(), before)


# ── współbieżność ───────────────────────────────────────────────────────────

class WriteRateLimiterTests(unittest.TestCase):
    def make(self, **kwargs):
        self.clock = FakeClock(1000.0)
        return ps.WriteRateLimiter(clock=self.clock, **kwargs)

    def test_limit_then_exact_retry_after(self):
        limiter = self.make(window=60, limit=3)
        self.assertEqual(limiter.hit('a'), 0)
        self.clock.advance(30)
        self.assertEqual([limiter.hit('a'), limiter.hit('a')], [0, 0])
        self.assertEqual(limiter.hit('a'), 30)          # najstarszy wpis wygasa za 30 s
        self.clock.advance(10)
        self.assertEqual(limiter.hit('a'), 20)
        self.clock.advance(19.5)
        self.assertEqual(limiter.hit('a'), 1)           # zaokrąglenie w górę, min 1
        self.clock.advance(0.5)
        self.assertEqual(limiter.hit('a'), 0)

    def test_refused_attempt_is_not_counted(self):
        limiter = self.make(window=60, limit=2)
        limiter.hit('a'), limiter.hit('a')
        for _ in range(5):
            self.assertEqual(limiter.hit('a'), 60)
        self.clock.advance(60)
        self.assertEqual([limiter.hit('a'), limiter.hit('a'), limiter.hit('a')], [0, 0, 60])

    def test_entries_expire_one_by_one(self):
        limiter = self.make(window=60, limit=2)
        limiter.hit('a')
        self.clock.advance(20)
        limiter.hit('a')
        self.assertEqual(limiter.hit('a'), 40)
        self.clock.advance(40)                          # wygasł tylko pierwszy wpis
        self.assertEqual(limiter.hit('a'), 0)
        self.assertEqual(limiter.hit('a'), 20)          # drugi (t=20) wygasa za 20 s

    def test_clock_jump_back_clears_the_window(self):
        limiter = self.make(window=60, limit=2)
        limiter.hit('a'), limiter.hit('a')
        self.assertEqual(limiter.hit('a'), 60)
        self.clock.advance(-3600)
        self.assertEqual(limiter.hit('a'), 0)           # stare stemple z przyszłości nie blokują
        self.assertEqual(limiter.hit('a'), 0)
        self.assertEqual(limiter.hit('a'), 60)          # retryAfter nie przekracza okna

    def test_logins_are_independent(self):
        limiter = self.make(window=60, limit=1)
        self.assertEqual(limiter.hit('a'), 0)
        self.assertEqual(limiter.hit('a'), 60)
        self.assertEqual(limiter.hit('b'), 0)

    def test_max_keys_evicts_expired_then_oldest_and_never_the_caller(self):
        limiter = self.make(window=60, limit=1, max_keys=4)
        for login in ('a', 'b', 'c', 'd'):
            limiter.hit(login)
            self.clock.advance(1)
        self.assertEqual(len(limiter._items), 4)
        self.assertEqual(limiter.hit('d'), 59)          # znany login przy pełnej tabeli: bez ewikcji, licznik zachowany
        self.assertEqual(len(limiter._items), 4)
        self.assertEqual(limiter.hit('e'), 0)           # nowy klucz: wypada najstarszy ('a'), tabela ograniczona
        self.assertLessEqual(len(limiter._items), 4)
        self.assertNotIn('a', limiter._items)
        self.assertIn('d', limiter._items)
        self.clock.advance(100)                         # wszystko wygasło: ewikcja czyści wygasłe
        self.assertEqual(limiter.hit('f'), 0)
        self.assertEqual(list(limiter._items), ['f'])


class ConcurrencyTests(QuestionsCase):
    def run_threads(self, count, work):
        results, barrier = {}, threading.Barrier(count)

        def target(index):
            barrier.wait()
            try:
                results[index] = ('ok', work(index))
            except ps.PrototypeServerError as error:
                results[index] = ('err', error)
            except Exception as error:                  # noqa: BLE001 - wynik wątku trafia do asercji
                results[index] = ('crash', error)

        threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual(len(results), count)
        return results

    def test_many_experts_answering_at_once_close_exactly_once(self):
        self.add(ask(options=[{'id': 'o1', 'label': 'A'}]))
        count = 16
        results = self.run_threads(count, lambda i: self.new_store().append(
            [answer(Q1, 'e%d@example.com' % i, optionId='o1')]))
        crashes = [r for r in results.values() if r[0] == 'crash']
        self.assertEqual(crashes, [])
        winners = [r for r in results.values() if r[0] == 'ok']
        losers = [r[1] for r in results.values() if r[0] == 'err']
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), count - 1)
        for error in losers:
            self.assertEqual((error.status, error.code), (409, 'already_answered'))
        ops = self.file_ops()                           # plik jest poprawnym JSON-em
        self.assertEqual([o['type'] for o in ops].count('answer'), 1)
        self.assertEqual(self.state()['state'], 'answered')
        self.assertEqual(self.state()['answer']['by'], winners[0][1].operations[0]['by'])
        self.assertEqual([n for n in os.listdir(self.directory) if n.endswith('.tmp')], [])

    def test_same_author_same_cid_retries_are_idempotent(self):
        self.add(ask())
        results = self.run_threads(8, lambda i: self.new_store().append([answer(Q1, EWA, cid='ten-sam', text='x')]))
        self.assertEqual([r[0] for r in results.values()], ['ok'] * 8)
        self.assertEqual(len([o for o in self.file_ops() if o['type'] == 'answer']), 1)

    def test_parallel_asks_lose_nothing(self):
        results = self.run_threads(12, lambda i: self.new_store().append([ask('qn-%012x' % (0x500 + i))]))
        self.assertEqual([r[0] for r in results.values()], ['ok'] * 12)
        ops = self.file_ops()
        self.assertEqual(len(ops), 12)
        self.assertEqual(len(set(o['id'] for o in ops)), 12)
        self.assertEqual(len(set(o['at'] for o in ops)), 12)


# ── fold i widoczność ───────────────────────────────────────────────────────

class VisibilityTests(QuestionsCase):
    def setUp(self):
        QuestionsCase.setUp(self)
        self.add(ask(Q1))                                              # do wszystkich
        self.add(ask(Q2, addressees=[EWA], refersTo=Q1))               # do Ewy, odnosi się do widocznego
        self.add(ask(Q3, addressees=[OLA]))                            # do Oli
        self.add(ask(Q4, addressees=[EWA], refersTo=Q3))               # do Ewy, odnosi się do niewidocznego
        self.add(ask(Q5))
        self.add(withdraw(Q5))                                         # wycofane
        self.snap = self.store.snapshot()

    def ids(self, who):
        return [v['id'] for v in ps.visible_questions(self.snap, who)]

    def test_developer_and_acceptor_see_everything_with_addressees(self):
        for who in (viewer(DEV, 'developer'), viewer(ACC, 'acceptor'), viewer(DEV, 'developer', 'expert')):
            self.assertEqual(self.ids(who), [Q1, Q2, Q3, Q4, Q5])
        view = ps.get_question_view(self.snap, Q3, viewer(DEV, 'developer'))
        self.assertEqual(view['addressees'], [OLA])
        self.assertEqual(ps.get_question_view(self.snap, Q4, viewer(ACC, 'acceptor'))['refersTo'], Q3)
        self.assertEqual(ps.get_question_view(self.snap, Q5, viewer(DEV, 'developer'))['state'], 'withdrawn')

    def test_expert_sees_all_and_own_but_not_foreign_or_withdrawn(self):
        self.assertEqual(self.ids(viewer(EWA, 'expert')), [Q1, Q2, Q4])
        self.assertEqual(self.ids(viewer(OLA, 'expert')), [Q1, Q3])
        self.assertEqual(self.ids(viewer(IWO, 'expert')), [Q1])
        self.assertIsNone(ps.get_question_view(self.snap, Q3, viewer(EWA, 'expert')))      # cudze
        self.assertIsNone(ps.get_question_view(self.snap, Q5, viewer(EWA, 'expert')))      # wycofane
        self.assertIsNone(ps.get_question_view(self.snap, 'qn-0000deadbeef', viewer(EWA, 'expert')))  # nieistniejące: to samo

    def test_expert_view_has_no_addressees_only_for_all(self):
        mine = ps.get_question_view(self.snap, Q2, viewer(EWA, 'expert'))
        everyone = ps.get_question_view(self.snap, Q1, viewer(EWA, 'expert'))
        for view in (mine, everyone):
            self.assertNotIn('addressees', view)
            self.assertNotIn('withdrawn', view)
        self.assertFalse(mine['forAll'])
        self.assertTrue(everyone['forAll'])
        self.assertNotIn(OLA, json.dumps(ps.visible_questions(self.snap, viewer(EWA, 'expert'))))
        self.assertNotIn('number', mine)

    def test_recorded_view_is_only_time_for_the_expert_and_full_for_the_admin(self):
        self.add(answer(Q1, by=OLA), recorded(Q1, spec='docs/specs/tajny.md', line=7))
        snap = self.store.snapshot()
        expert = ps.get_question_view(snap, Q1, viewer(EWA, 'expert'))
        self.assertEqual(set(expert['recorded']), {'at'})
        self.assertNotIn('docs/specs/tajny.md', json.dumps(ps.visible_questions(snap, viewer(EWA, 'expert'))))
        admin = ps.get_question_view(snap, Q1, viewer(DEV, 'developer'))
        self.assertEqual((admin['recorded']['spec'], admin['recorded']['line']), ('docs/specs/tajny.md', 7))
        self.assertEqual(admin['recorded']['at'], expert['recorded']['at'])
        self.assertIsNone(ps.get_question_view(snap, Q2, viewer(EWA, 'expert'))['recorded'])

    def test_expert_view_has_no_origin_spec_but_admin_view_keeps_it(self):
        expert = viewer(EWA, 'expert')
        for view in ps.visible_questions(self.snap, expert):
            self.assertEqual(set(view['origin']) - {'q'}, set(), view['id'])
        self.assertNotIn('docs/specs', json.dumps(ps.visible_questions(self.snap, expert)))
        self.assertEqual(ps.get_question_view(self.snap, Q1, expert)['origin'], {'q': 'Q1'})
        admin = ps.get_question_view(self.snap, Q1, viewer('adm', 'developer'))
        self.assertEqual(admin['origin'], {'spec': 'docs/specs/a.md', 'q': 'Q1'})

    def test_refers_to_invisible_question_is_omitted(self):
        who = viewer(EWA, 'expert')
        self.assertEqual(ps.get_question_view(self.snap, Q2, who)['refersTo'], Q1)
        self.assertIsNone(ps.get_question_view(self.snap, Q4, who)['refersTo'])
        self.assertNotIn(Q3, json.dumps(ps.visible_questions(self.snap, who)))

    def test_refers_to_withdrawn_question_is_none_for_expert(self):
        snap = self.store.snapshot()
        self.assertEqual(ps.get_question_view(snap, Q2, viewer(EWA, 'expert'))['refersTo'], Q1)
        self.add(withdraw(Q1))
        snap = self.store.snapshot()
        self.assertIsNone(ps.get_question_view(snap, Q2, viewer(EWA, 'expert'))['refersTo'])
        self.assertEqual(ps.get_question_view(snap, Q2, viewer(DEV, 'developer'))['refersTo'], Q1)

    def test_commenter_and_unknown_roles_see_nothing(self):
        for who in (viewer(COM, 'commenter'), viewer(COM), {'login': COM}, None, {}):
            self.assertEqual(ps.visible_questions(self.snap, who), [])
            self.assertIsNone(ps.get_question_view(self.snap, Q1, who))

    def test_single_role_string_form_works(self):
        self.assertEqual(self.ids({'login': EWA, 'role': 'developer'}), [Q1, Q2, Q3, Q4, Q5])

    def test_can_answer_only_expert_who_is_an_addressee(self):
        fold = self.snap.fold['byId']
        self.assertTrue(ps.can_answer(fold[Q1], viewer(EWA, 'expert')))
        self.assertTrue(ps.can_answer(fold[Q2], viewer(EWA, 'expert')))
        self.assertFalse(ps.can_answer(fold[Q3], viewer(EWA, 'expert')))               # cudze
        self.assertFalse(ps.can_answer(fold[Q1], viewer(DEV, 'developer')))            # developer bez expert tylko komentuje
        self.assertFalse(ps.can_answer(fold[Q1], viewer(ACC, 'acceptor')))
        self.assertFalse(ps.can_answer(fold[Q1], viewer(COM, 'commenter')))
        self.assertTrue(ps.can_answer(fold[Q1], viewer(DEV, 'developer', 'expert')))   # kontrola: z rolą expert
        self.assertFalse(ps.can_answer(fold[Q3], viewer(DEV, 'developer', 'expert')))  # nie jest adresatem
        self.assertTrue(ps.get_question_view(self.snap, Q1, viewer(EWA, 'expert'))['canAnswer'])
        self.assertFalse(ps.get_question_view(self.snap, Q1, viewer(DEV, 'developer'))['canAnswer'])

    def test_answered_question_cannot_be_answered_in_view(self):
        self.add(answer(Q1, EWA, text='x'))
        snap = self.store.snapshot()
        self.assertFalse(ps.get_question_view(snap, Q1, viewer(OLA, 'expert'))['canAnswer'])
        self.assertEqual(ps.get_question_view(snap, Q1, viewer(OLA, 'expert'))['answer']['by'], EWA)


class FoldTests(unittest.TestCase):
    def build(self, specs):
        """specs: lista (n, partial); `at` rośnie z n, `id` z n (kolejność plikowa nie ma znaczenia)."""
        return [full(p, n) for n, p in specs]

    def test_order_is_by_at_then_id_regardless_of_file_order(self):
        a, b, c = full(ask(Q1), 1), full(ask(Q2), 1), full(ask(Q3), 2)
        a['id'], b['id'] = 'op-aaa', 'op-bbb'
        for ops in itertools.permutations([a, b, c]):
            self.assertEqual([q['id'] for q in ps.fold_questions(list(ops))['questions']], [Q1, Q2, Q3])

    def test_first_answer_by_at_id_closes_and_the_rest_are_late_comments(self):
        ops = self.build([(1, ask(Q1)), (3, answer(Q1, OLA, text='druga')), (2, answer(Q1, EWA, text='pierwsza'))])
        question = ps.fold_questions(ops)['byId'][Q1]
        self.assertEqual(question['answer']['by'], EWA)
        self.assertEqual(question['answer']['text'], 'pierwsza')
        self.assertEqual([c['by'] for c in question['comments']], [OLA])
        self.assertIn('druga', question['comments'][0]['text'])         # późna odpowiedź jak comment, nie ginie

    def test_equal_at_is_broken_by_id(self):
        x, y = full(answer(Q1, OLA, text='o'), 2), full(answer(Q1, EWA, text='e'), 2)
        x['id'], y['id'] = 'op-zzz', 'op-aaa'
        question = ps.fold_questions([full(ask(Q1), 1), x, y])['byId'][Q1]
        self.assertEqual(question['answer']['by'], EWA)

    def test_answer_after_withdraw_is_ignored_like_a_comment(self):
        ops = self.build([(1, ask(Q1)), (2, withdraw(Q1)), (3, answer(Q1, EWA, text='spóźniona'))])
        question = ps.fold_questions(ops)['byId'][Q1]
        self.assertEqual(question['state'], 'withdrawn')
        self.assertIsNone(question['answer'])
        self.assertEqual(len(question['comments']), 1)
        # kontrola: odpowiedź przed wycofaniem zamyka, a wycofanie jest wtedy ignorowane
        ops = self.build([(1, ask(Q1)), (2, answer(Q1, EWA, text='x')), (3, withdraw(Q1))])
        question = ps.fold_questions(ops)['byId'][Q1]
        self.assertEqual(question['state'], 'answered')
        self.assertIsNone(question['withdrawn'])

    def test_recorded_without_answer_is_ignored(self):
        ops = self.build([(1, ask(Q1)), (2, recorded(Q1))])
        question = ps.fold_questions(ops)['byId'][Q1]
        self.assertIsNone(question['recorded'])
        self.assertEqual(question['state'], 'open')
        ops = self.build([(1, ask(Q1)), (2, answer(Q1, EWA, text='x')), (3, recorded(Q1))])      # kontrola
        self.assertIsNotNone(ps.fold_questions(ops)['byId'][Q1]['recorded'])

    def test_operation_earlier_than_its_ask_still_counts(self):
        early = full(answer(Q1, EWA, text='wczesna'), 1)
        late_ask = full(ask(Q1), 2)
        for ops in ([early, late_ask], [late_ask, early]):
            question = ps.fold_questions(ops)['byId'][Q1]
            self.assertEqual(question['state'], 'answered')
            self.assertEqual(question['answer']['text'], 'wczesna')

    def test_operations_of_unknown_question_are_skipped(self):
        ops = self.build([(1, answer(Q3, EWA, text='x')), (2, ask(Q1))])
        self.assertEqual([q['id'] for q in ps.fold_questions(ops)['questions']], [Q1])


# ── scalanie (sterownik gita) ───────────────────────────────────────────────

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.base = [full(ask(Q1, options=[{'id': 'o1', 'label': 'A'}]), 1)]
        self.mine = [full(answer(Q1, EWA, cid='m', optionId='o1'), 3), full(comment(Q1, EWA, cid='mc'), 5)]
        self.theirs = [full(answer(Q1, OLA, cid='t', text='inna'), 2), full(ask(Q2), 4)]
        for index, op in enumerate(self.mine + self.theirs):
            op['id'] = 'op-side%02d' % index

    def merged(self, a, b):
        return ps.merge_question_operations(a, b)[0]

    def test_commutative_byte_for_byte_and_sorted(self):
        a, b = self.base + self.mine, self.base + self.theirs
        ab, ba = self.merged(a, b), self.merged(b, a)
        self.assertEqual(ps.serialize_questions(ab), ps.serialize_questions(ba))
        self.assertEqual([(o['at'], o['id']) for o in ab], sorted((o['at'], o['id']) for o in ab))
        self.assertEqual(len(ab), 5)

    def test_idempotent(self):
        a, b = self.base + self.mine, self.base + self.theirs
        once = self.merged(a, b)
        twice, added = ps.merge_question_operations(once, once)
        self.assertEqual(ps.serialize_questions(once), ps.serialize_questions(twice))
        self.assertEqual(added, [])
        again, added = ps.merge_question_operations(once, a)
        self.assertEqual(ps.serialize_questions(again), ps.serialize_questions(once))
        self.assertEqual(added, [])

    def test_added_lists_only_missing_operations(self):
        a, b = self.base + self.mine, self.base + self.theirs
        _merged, added = ps.merge_question_operations(a, b)
        self.assertEqual(sorted(o['id'] for o in added), sorted(o['id'] for o in self.theirs))

    def test_second_answer_in_merged_set_is_late_first_by_at_id_closes(self):
        merged = self.merged(self.base + self.mine, self.base + self.theirs)
        question = ps.fold_questions(merged)['byId'][Q1]
        self.assertEqual(question['answer']['by'], OLA)                 # at 2 < at 3
        late = [c for c in question['comments'] if c.get('late')]
        self.assertEqual([c['by'] for c in late], [EWA])
        self.assertEqual(question['state'], 'answered')
        # ta sama odpowiedź po drugiej stronie kolejności nie zmienia wyniku
        swapped = ps.fold_questions(self.merged(self.base + self.theirs, self.base + self.mine))['byId'][Q1]
        self.assertEqual(swapped['answer']['by'], OLA)

    def test_answer_without_ask_is_a_conflict(self):
        orphan = [full(answer(Q3, EWA, cid='o', text='x'), 2)]
        with self.assertRaises(ps.MergeConflict):
            ps.merge_question_operations(self.base, orphan)
        with self.assertRaises(ps.MergeConflict):
            ps.merge_question_operations(orphan, self.base)
        ps.merge_question_operations(self.base, [full(ask(Q3), 2)])    # kontrola

    def test_same_id_different_content_is_a_conflict(self):
        other = [dict(self.base[0], text='zmieniony tekst')]
        with self.assertRaises(ps.MergeConflict):
            ps.merge_question_operations(self.base, other)
        ps.merge_question_operations(self.base, [dict(self.base[0])])  # kontrola: identyczna kopia

    def test_invalid_operation_is_a_conflict(self):
        with self.assertRaises(ps.MergeConflict):
            ps.merge_question_operations(self.base, [dict(full(ask(Q2), 2), number=2)])

    def test_withdraw_then_answer_after_merge_ignored_recorded_without_answer_ignored(self):
        a = self.base + [full(withdraw(Q1), 2)]
        b = self.base + [full(answer(Q1, EWA, cid='x', text='później'), 3), full(recorded(Q1, cid='r'), 4)]
        for index, op in enumerate(a[1:] + b[1:]):
            op['id'] = 'op-wd%02d' % index
        question = ps.fold_questions(self.merged(a, b))['byId'][Q1]
        self.assertEqual(question['state'], 'withdrawn')
        self.assertIsNone(question['answer'])
        self.assertIsNone(question['recorded'])

    def test_answer_timestamped_before_its_ask_is_not_a_conflict(self):
        merged = self.merged([full(ask(Q1), 3)], [full(answer(Q1, EWA, cid='e', text='x'), 1)])
        self.assertEqual(ps.fold_questions(merged)['byId'][Q1]['state'], 'answered')

    def test_file_one_operation_over_the_limit_merges_with_itself(self):
        ops = self.base + self.mine
        size = len(ps.serialize_questions(ops).encode('utf-8'))
        merged, added = ps.merge_question_operations(ops, list(ops), max_bytes=size - 1)
        self.assertEqual(len(merged), len(ops))
        self.assertEqual(added, [])
        left, right = self.base + self.mine, self.base + self.theirs
        biggest = max(len(ps.serialize_questions(side).encode('utf-8')) for side in (left, right))
        with self.assertRaises(ps.MergeConflict):    # wynik większy od obu wejść i ponad limit
            ps.merge_question_operations(left, right, max_bytes=biggest)

    def test_size_over_limit_is_a_conflict(self):
        with self.assertRaises(ps.MergeConflict):
            ps.merge_question_operations(self.base, self.theirs, max_bytes=200)


# ── eksport expert-questions/1 ──────────────────────────────────────────────

class ExportTests(QuestionsCase):
    def setUp(self):
        QuestionsCase.setUp(self)
        ref = {'title': 'Sekcja 3', 'href': 'docs/specs/a.md#s3', 'excerpt': 'Fragment speca.'}
        self.add(ask(Q1, refs=[ref], options=[{'id': 'o1', 'label': 'Tak', 'recommended': True}, {'id': 'o2', 'label': 'Nie'}],
                     addressees=[EWA, OLA], origin={'spec': 'docs/specs/a.md', 'q': 'Q7'}))
        self.add(answer(Q1, EWA, optionId='o1', comment='uzasadnienie'))
        self.add(comment(Q1, OLA, text='dodatek'))
        self.add(recorded(Q1, spec='docs/specs/a.md', line=21))
        self.add(ask(Q2, blocking=True, assumed=None, refersTo=Q1))
        self.add(ask(Q3), withdraw(Q3))
        self.snap = self.store.snapshot()

    def export(self, **kwargs):
        return ps.export_questions(self.snap, 'root', **kwargs)

    def by_id(self, result):
        return dict((q['id'], q) for q in result['questions'])

    def test_schema_project_and_url(self):
        result = self.export()
        self.assertEqual(result['schema'], 'expert-questions/1')
        self.assertEqual(result['project'], 'root')
        self.assertNotIn('url', result)
        self.assertEqual(self.export(url='https://example.com/_experts')['url'], 'https://example.com/_experts')
        json.dumps(result)

    def test_question_fields_and_order(self):
        result = self.export()
        self.assertEqual([q['id'] for q in result['questions']], [Q1, Q2, Q3])
        first = self.by_id(result)[Q1]
        for key in ('id', 'text', 'state', 'blocking', 'assumed', 'addressees', 'refs', 'options', 'answer', 'comments', 'origin', 'recorded'):
            self.assertIn(key, first)
        self.assertEqual(first['state'], 'answered')
        self.assertEqual(first['addressees'], [EWA, OLA])
        self.assertEqual(first['origin'], {'spec': 'docs/specs/a.md', 'q': 'Q7'})
        self.assertEqual(first['refs'][0]['excerpt'], 'Fragment speca.')
        self.assertNotIn('number', first)
        second = self.by_id(result)[Q2]
        self.assertEqual(second['state'], 'open')
        self.assertEqual(second['refersTo'], Q1)
        self.assertNotIn('answer', second)
        self.assertNotIn('recorded', second)
        self.assertTrue(second['blocking'])
        self.assertEqual(self.by_id(result)[Q3]['state'], 'withdrawn')

    def test_answer_name_comes_from_users_or_falls_back_to_login(self):
        self.assertEqual(self.by_id(self.export())[Q1]['answer']['name'], EWA)
        users = {EWA: {'name': 'Ewa Ekspert'}, OLA: {'login': OLA}}
        result = self.by_id(self.export(users=users))[Q1]
        self.assertEqual(result['answer']['name'], 'Ewa Ekspert')
        self.assertEqual(result['answer']['by'], EWA)
        self.assertEqual(result['answer']['optionId'], 'o1')
        self.assertEqual(result['answer']['comment'], 'uzasadnienie')
        self.assertEqual(result['answer']['at'], self.state_at(Q1))
        self.assertEqual(result['comments'][0]['name'], OLA)                 # brak `name` w koncie: login
        users[OLA] = {'name': 'Ola O.'}
        self.assertEqual(self.by_id(self.export(users=users))[Q1]['comments'][0]['name'], 'Ola O.')

    def state_at(self, qid):
        return self.snap.fold['byId'][qid]['answer']['at']

    def test_comments_and_recorded(self):
        first = self.by_id(self.export())[Q1]
        self.assertEqual(len(first['comments']), 1)
        entry = first['comments'][0]
        for key, value in (('by', OLA), ('text', 'dodatek')):
            self.assertEqual(entry[key], value)
        self.assertIn('at', entry)
        self.assertIn('name', entry)
        self.assertEqual(sorted(first['recorded']), ['at', 'line', 'spec'])
        self.assertEqual((first['recorded']['spec'], first['recorded']['line']), ('docs/specs/a.md', 21))
        self.assertRegex(first['recorded']['at'], r'^\d{4}-\d\d-\d\dT')

    def test_export_is_readable_without_a_server_from_a_list_of_operations(self):
        self.assertEqual(ps.export_questions(self.snap.operations, 'root'), self.export())
        empty = ps.export_questions(ps.QuestionsStore(os.path.join(self.tmp, 'brak'), clock=self.clock).snapshot(), 'brak')
        self.assertEqual(empty['questions'], [])
        self.assertEqual(empty['schema'], 'expert-questions/1')

    def test_export_keeps_old_answer_next_to_a_dispute(self):
        questions = self.by_id(self.export())
        self.assertEqual(questions[Q1]['answer']['optionId'], 'o1')
        self.assertEqual(questions[Q2]['refersTo'], Q1)


MULTI_OPTIONS = [{'id': 'o1', 'label': 'CSV', 'recommended': True}, {'id': 'o2', 'label': 'XLSX', 'recommended': True},
                 {'id': 'o3', 'label': 'PDF'}]


class MultiChoiceSchemaTests(unittest.TestCase):
    """Pytanie wielokrotnego wyboru (`multi`): schemat `ask` i `answer` (addytywnie do `expert-questions/1`)."""

    def check(self, op, known=None):
        ps.validate_question_operation(full(op), known)

    def reject(self, op, known=None, code=None):
        got = code_of(self, lambda: self.check(op, known), 400)
        if code is not None:
            self.assertEqual(got, code)

    def known(self, **over):
        question = {'options': MULTI_OPTIONS, 'assumed': 'CSV', 'multi': True}
        question.update(over)
        return {Q1: question}

    def test_ask_multi_allows_several_recommendations_and_needs_two_options(self):
        self.check(ask(multi=True, options=MULTI_OPTIONS))
        self.reject(ask(multi=False, options=MULTI_OPTIONS))      # dwie rekomendacje tylko przy multi
        self.reject(ask(options=MULTI_OPTIONS))
        self.reject(ask(multi=True, options=MULTI_OPTIONS[:1]), code='multi_needs_options')
        self.reject(ask(multi=True, options=[]), code='multi_needs_options')
        self.reject(ask(multi='tak', options=MULTI_OPTIONS))
        self.check(ask(multi=False, options=MULTI_OPTIONS[1:]))

    def test_answer_optionids_for_a_multi_question(self):
        known = self.known()
        self.check(answer(optionIds=['o1', 'o3']), known)
        self.check(answer(optionIds=['o2']), known)
        self.check(answer(optionIds=['o1'], comment='dwa formaty'), known)
        self.check(answer(text='żaden z nich: JSON'), known)           # „żaden” = własna odpowiedź
        self.check(answer(confirm=True), known)

    def test_empty_selection_is_refused_with_its_own_code(self):
        self.reject(answer(optionIds=[]), self.known(), code='empty_selection')
        self.reject(answer(optionIds=[]), code='empty_selection')

    def test_bad_optionids_are_refused(self):
        known = self.known()
        self.reject(answer(optionIds=['o1', 'o1']), known)
        self.reject(answer(optionIds=['o1', 'o9']), known, code='unknown_option')
        self.reject(answer(optionIds='o1'), known)
        self.reject(answer(optionIds=[1]), known)
        self.reject(answer(optionIds=['o1', '']), known)
        self.reject(answer(optionIds=['o%d' % n for n in range(9)]), code='too_many')

    def test_optionids_xor_the_other_answer_kinds(self):
        known = self.known()
        for over in ({'text': 'x'}, {'confirm': True}, {'optionId': 'o1'}):
            op = answer(optionIds=['o1'], **over)
            self.reject(op, known)

    def test_single_and_multi_questions_take_their_own_kind(self):
        self.reject(answer(optionId='o1'), self.known(), code='wrong_answer_kind')
        self.reject(answer(optionIds=['o1']), self.known(multi=False), code='wrong_answer_kind')
        self.check(answer(optionId='o1'), self.known(multi=False))
        self.check(answer(optionIds=['o1']))                   # bez znajomości pytania (np. scalanie): tylko kształt


class MultiChoiceFoldTests(QuestionsCase):
    def setUp(self):
        QuestionsCase.setUp(self)
        self.add(ask(Q1, multi=True, options=MULTI_OPTIONS, text='W jakich formatach eksport?'))
        self.add(ask(Q2, options=[{'id': 'o1', 'label': 'Tak'}, {'id': 'o2', 'label': 'Nie'}]))

    def test_answer_keeps_the_selection_and_exports_ids_and_labels_in_option_order(self):
        self.add(answer(Q1, EWA, optionIds=['o3', 'o1'], comment='oba'))
        item = dict((q['id'], q) for q in ps.export_questions(self.store.snapshot(), 'root')['questions'])[Q1]
        self.assertTrue(item['multi'])
        self.assertEqual(item['answer']['optionIds'], ['o3', 'o1'])
        self.assertEqual(item['answer']['optionLabels'], ['CSV', 'PDF'])
        self.assertNotIn('optionId', item['answer'])
        self.assertNotIn('optionLabel', item['answer'])
        self.assertEqual(item['answer']['comment'], 'oba')

    def test_single_question_export_is_unchanged(self):
        self.add(answer(Q2, EWA, optionId='o2'))
        item = dict((q['id'], q) for q in ps.export_questions(self.store.snapshot(), 'root')['questions'])[Q2]
        self.assertNotIn('multi', item)
        self.assertEqual((item['answer']['optionId'], item['answer']['optionLabel']), ('o2', 'Nie'))
        self.assertNotIn('optionIds', item['answer'])

    def test_view_carries_multi_and_the_stored_selection(self):
        self.add(answer(Q1, EWA, optionIds=['o2']))
        snap = self.store.snapshot()
        view = ps.get_question_view(snap, Q1, viewer(EWA, 'expert'))
        self.assertTrue(view['multi'])
        self.assertEqual(view['answer']['optionIds'], ['o2'])
        self.assertFalse(ps.get_question_view(snap, Q2, viewer(EWA, 'expert'))['multi'])

    def test_free_text_and_confirm_still_work_on_a_multi_question(self):
        self.add(answer(Q1, EWA, text='żaden: JSON'))
        item = dict((q['id'], q) for q in ps.export_questions(self.store.snapshot(), 'root')['questions'])[Q1]
        self.assertEqual(item['answer']['text'], 'żaden: JSON')
        self.assertNotIn('optionIds', item['answer'])

    def test_server_side_store_refuses_wrong_kind_and_empty_selection(self):
        self.assertEqual(code_of(self, lambda: self.add(answer(Q1, EWA, optionIds=[])), 400), 'empty_selection')
        self.assertEqual(code_of(self, lambda: self.add(answer(Q1, EWA, optionId='o1')), 400), 'wrong_answer_kind')
        self.assertEqual(code_of(self, lambda: self.add(answer(Q2, EWA, optionIds=['o1'])), 400), 'wrong_answer_kind')

    def test_late_multi_answer_becomes_a_comment_with_all_labels(self):
        folded = ps.fold_questions([full(ask(Q1, multi=True, options=MULTI_OPTIONS), 1), full(answer(Q1, EWA, text='x'), 2),
                                    full(answer(Q1, OLA, optionIds=['o1', 'o2']), 3)])
        late = folded['byId'][Q1]['comments'][0]
        self.assertTrue(late['late'])
        self.assertEqual(late['text'], 'CSV, XLSX')

    def test_old_log_without_multi_folds_as_a_single_choice_question(self):
        folded = ps.fold_questions([full(ask(Q1, options=[{'id': 'o1', 'label': 'a'}]), 1)])
        self.assertIs(folded['byId'][Q1]['multi'], False)


if __name__ == '__main__':
    unittest.main()
