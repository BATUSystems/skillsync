"""Testy silnika prototypów v2 (`prototype.js`): fold, etykiety, kolejka, akceptacja i czyste funkcje przez eksporty.

Silnik jest sprawdzany przez zachowanie: wywołania eksportów `module.exports` (przez `diff.cjs` i `queue.cjs`) oraz atrapy
DOM, nie przez tekst źródła. Plik silnika to `SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH` albo kopia w
tests/fixtures/prototype/engine-v2/ (SOURCE.md). Świeżość kopii względem biblioteki sprawdza
`SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE` (jawne pominięcie: `SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1`).
Node >= 18: SKILLSYNC_TEST_NODE albo `node` z PATH.
"""
import copy
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import prototype as engine  # noqa: E402
from skillsync_workflow import prototype_server as server  # noqa: E402


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def find_node():
    """Return (path, None) for node >= 18 or (None, reason)."""
    candidate = os.environ.get('SKILLSYNC_TEST_NODE') or shutil.which('node')
    if not candidate:
        return None, 'brak node (SKILLSYNC_TEST_NODE ani PATH)'
    try:
        out = subprocess.run([candidate, '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        return None, f'node {candidate} nie działa: {error}'
    match = re.match(r'v(\d+)\.', out)
    if not match or int(match.group(1)) < 18:
        return None, f'wymagany node >= 18, jest {out or "?"} ({candidate})'
    return candidate, None


# ── Silnik v2: kopia, test różnicowy foldu (K5) i etykiet (K41) Node <-> Python ──────────────────────────────
# Kontrakt: docs/specs/2026-11-04-prototype-komentarze-wspolne.md (tabela operacji, „Fold”, `people`/`label`).
# Kopia: tests/fixtures/prototype/engine-v2/ (SOURCE.md). Strona Pythona: skillsync_workflow/prototype_server.py.

ENGINE_V2_DIR = Path(__file__).resolve().parent / 'fixtures' / 'prototype' / 'engine-v2'
ENGINE_V2_SHA256 = 'e990f17fe7d10a93113fb7768d46c6800b7a0f3504f341bdc18f89dd1c779684'
REFRESH_V2 = 'Odśwież kopię wg tests/fixtures/prototype/engine-v2/SOURCE.md i zaktualizuj ENGINE_V2_SHA256.'
ENGINE_V2_PATH_ENV = 'SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH'
DIFF_SEEDS = (20261104, 7, 1234567, 99, 424242, 31337)
DIFF_PER_SEED = 600  # 6 x 600 = 3600 dzienników (K5: >= 3000)

# Typy operacji z tabeli speca (kolejność jak w tabeli).
SPEC_TYPES = ('create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen', 'reanchor', 'delete', 'triage',
              'decision', 'accept', 'unaccept', 'iteration', 'restore')
RESOLVE_REASONS = ('done', 'kept', 'out_of_scope', 'author')
TRIAGE_VALUES = ('clear', 'disputed', 'out_of_scope')
DECISION_OUTCOMES = ('apply', 'keep', 'variant', 'defer', 'out_of_scope')
GEN_LOGINS = ('pawel.kowalski@example.com', 'pawel.bak@example.com', 'pawel.bury@example.com', 'anna.lis@example.com', 'anna.lis-nowak@example.com',
              'zoe.ąb@example.com', 'jan@example.com', 'agent', 'import')
# `id` porównywane w jednostkach UTF-16: znak U+FF5E (BMP) vs astralny U+1F600 (surogaty D83D...) i samotny surogat.
GEN_IDS = tuple('op-%02d' % number for number in range(24)) + ('op-～', 'op-\U0001f600', 'op-\ud800', 'op-')
# Pole `view` w `create` (spec trybu widoku, D4): poprawne wartości, brak pola i wartości niepoprawne.
GEN_VIEWS = ('mobile', 'mobile', 'desktop', 'desktop', 'tablet', 5, None, 'Mobile', '', True, ['mobile'])
GEN_JUNK = (None, True, 0, 1, 2.0, 0.5, 1e-7, 1e21, 123456789012345678901, -0.0, '', 'x', 'ż\ud800', [], {},
            {'3': 1, 'b': 2, '1': 0})


def engine_v2_js():
    """Plik silnika, który ładują testy: `SKILLSYNC_PROTOTYPE_ENGINE_V2_PATH` albo kopia w `tests/fixtures/prototype/engine-v2/`."""
    configured = os.environ.get(ENGINE_V2_PATH_ENV)
    return Path(configured) if configured else ENGINE_V2_DIR / 'prototype.js'


def install_engine_v2(tmp):
    """Kopiuje do `tmp` skrypty pomocnicze node (`diff.cjs`, `queue.cjs`) i testowany silnik; zwraca ścieżkę silnika."""
    for item in ENGINE_V2_DIR.iterdir():
        if item.is_file() and item.name != 'prototype.js':
            shutil.copy2(item, tmp)
    engine_js = Path(tmp) / 'prototype.js'
    shutil.copy2(engine_v2_js(), engine_js)
    return engine_js


def assert_detects(test, check, good, bad):
    """Dowód, że asercja potrafi paść: `check` przechodzi na wyniku eksportu (`good`) i musi odrzucić `bad`, czyli wynik
    błędnej implementacji, zbudowany po stronie testu (atrapa błędu, nie zmiana źródła silnika)."""
    check(good)
    with test.assertRaises(AssertionError):
        check(bad)


def is_plain_object(value):
    return isinstance(value, dict)


# Akceptacje (F3, kontrakt A/A1): ekrany bez kluczy całkowitoliczbowych (obiekt JS porządkuje je numerycznie, nie jak `<`);
# `__proto__` i `constructor` sprawdzają, że klucze nie trafiają do prototypu.
GEN_SCREENS = ('s1', 's2', 's3', 's10', 'ż1', 'a\U0001f600', 'a～', '__proto__', 'constructor')
GEN_HASHES = ('h1', 'h2', 'h3', '0' * 64, 'ż', '', None, 5, True, 1.5, [], {})
GEN_ACCEPT_ITERATIONS = (1, 2, 3, 2.0, 2.5, 0, -1, True, False, None, '2', [], {}, -0.0)
GEN_ACCEPT_VIA = ('ui', 'ui', 'cli', 'cli', 'x', '', None, 5, True)
GEN_ACCEPT_SCOPES = ('screen', 'all', 'project', 'x', None, 5)
GEN_ACCEPT_WHO = ('jan@example.com', 'anna.lis@example.com', '', None, 5, [])


def accept_payload(rnd):
    """Payload `accept`: poprawny (obiekt ekran -> hash) albo zniekształcony wg A1."""
    roll = rnd.random()
    if roll < 0.12:
        screens = rnd.choice([[], ['s1'], None, 'x', 3, True, [{'s1': 'h'}]])
    else:
        screens = dict((rnd.choice(GEN_SCREENS), rnd.choice(GEN_HASHES[:5] * 3 + GEN_HASHES)) for _ in range(rnd.randint(0, 3)))
    payload = {'scope': rnd.choice(GEN_ACCEPT_SCOPES), 'screens': screens, 'iteration': rnd.choice(GEN_ACCEPT_ITERATIONS),
               'via': rnd.choice(GEN_ACCEPT_VIA)}
    if rnd.random() < 0.6:
        payload['who'] = rnd.choice(GEN_ACCEPT_WHO)
    for key in ('scope', 'iteration', 'via'):
        if rnd.random() < 0.05:
            del payload[key]
    return payload


def unaccept_payload(rnd):
    """Payload `unaccept`: lista ekranów (też z nie-napisami) albo zniekształcony `screens`."""
    if rnd.random() < 0.15:
        return {'screens': rnd.choice([{}, {'s1': 'h'}, None, 'x', 3, 's1'])}
    return {'screens': [rnd.choice(GEN_SCREENS + ('', None, 5, [])) for _ in range(rnd.randint(0, 3))]}


def generate_log(rnd):
    """Jeden losowy dziennik: spójne wątki plus kolizje `at`, duplikaty `id`, cudze edycje, błędne payloady."""
    ops = []
    thread_ids = ['th-%d' % number for number in range(rnd.randint(1, 4))]
    known = dict((thread_id, []) for thread_id in thread_ids)  # (id wpisu, autor) wpisów, które mogą istnieć
    message_pool = ['m-%d' % number for number in range(6)]

    def actor():
        if rnd.random() < 0.04:
            return rnd.choice([None, '', 7])
        return rnd.choice(GEN_LOGINS)

    def stamp():
        return '2026-11-04T10:00:%02d.%03dZ' % (rnd.randint(0, 4), rnd.choice((0, 0, 1, 500)))

    def message_ref(thread_id, author=None):
        pool = known[thread_id]
        if pool and rnd.random() < 0.85:
            own = [mid for mid, by in pool if by == author] if author is not None and rnd.random() < 0.6 else []
            return rnd.choice(own or [mid for mid, _ in pool])
        return rnd.choice(message_pool + [None, '', 5])

    def new_message(thread_id, author=None):
        message_id = rnd.choice(message_pool) if rnd.random() < 0.15 else 'm-%s-%d' % (thread_id, len(ops))
        known[thread_id].append((message_id, author))
        text = rnd.choice(['tekst', 'ż ó ł 😀', 'a' * 5, '', 4000 * 'x', 3, None])
        message = {'id': message_id, 'text': text}
        if rnd.random() < 0.15:
            message['importedAuthor'] = rnd.choice(['Jan K.', 3])
        return message

    # Większość dzienników zaczyna się poprawnymi wątkami; reszta operacji jest losowa (też błędna).
    for thread_id in thread_ids:
        if rnd.random() < 0.8:
            author = rnd.choice(GEN_LOGINS)
            ops.append({'id': 'op-c%s' % thread_id, 'type': 'create', 'threadId': thread_id,
                        'at': '2026-11-04T10:00:00.%03d' % rnd.choice((0, 0, 1)) + 'Z', 'by': author,
                        'payload': {'message': new_message(thread_id, author), 'screen': rnd.choice(['s1', 's2']),
                                    'anchor': {'kind': 'element', 'protoId': 's1-a', 'x': 1, 'y': 0, 'iteration': 1},
                                    'label': 'Przycisk', 'hint': {'text': 'Zapisz'}}})
            if rnd.random() < 0.7:
                ops[-1]['payload']['view'] = rnd.choice(GEN_VIEWS)
    for index in range(rnd.randint(0, 28)):
        kind = rnd.choice(SPEC_TYPES + ('create', 'create', 'reply', 'reply', 'reply', 'vote', 'edit', 'retract', 'retract', 'bogus', 'bogus',
                                        'accept', 'accept', 'unaccept'))
        thread_id = rnd.choice(thread_ids)
        by = actor()
        payload = {}
        if kind == 'create':
            payload = {'message': rnd.choice([new_message(thread_id, by)] * 6 + [{'id': 'm-x'}, 'str', None, {}, 0, []]),
                       'screen': rnd.choice(['s1', 's1', 's2', '', None, 5]),
                       'anchor': rnd.choice([{'kind': 'element', 'protoId': 's1-a', 'x': 1, 'y': 0, 'iteration': 1},
                                             {'kind': 'screen', 'x': 0.5, 'y': 0.25, 'iteration': 2},
                                             {'kind': 'lost'}, None, 'x', []]),
                       'label': rnd.choice(['Przycisk', 'L', 5, None]),
                       'hint': rnd.choice([{'text': 'Zapisz'}, {'text': 'x', 'legacyPath': 'a>b'}, None, 'x'])}
            if rnd.random() < 0.6:
                payload['view'] = rnd.choice(GEN_VIEWS)
        elif kind == 'reply':
            payload = {'message': rnd.choice([new_message(thread_id, by)] * 6 + [{'id': 'm-0'}, 'str', None, {}, 0])}
        elif kind in ('edit', 'retract', 'vote'):
            payload = {'messageId': message_ref(thread_id, by), 'text': rnd.choice(['nowy', 'ż', 1, None]),
                       'value': rnd.choice(['up', 'down', 'up', 'down', 'none', 'x', None])}
        elif kind == 'resolve':
            payload = {'reason': rnd.choice(RESOLVE_REASONS + ('', 3, None)), 'iteration': rnd.choice([1, 2, None])}
        elif kind == 'reanchor':
            payload = {'screen': rnd.choice(['s1', 's3', None, 4]),
                       'anchor': rnd.choice([{'kind': 'lost'}, {'kind': 'screen', 'x': 0, 'y': 0, 'iteration': 1}, None, 'x']),
                       'label': rnd.choice(['R', 7, None])}
        elif kind == 'triage':
            payload = {'class': rnd.choice(TRIAGE_VALUES + ('x', None)), 'note': rnd.choice(['n', None, 3])}
        elif kind == 'decision':
            payload = {'kind': rnd.choice(['agent', 'developer', 'x', None]),
                       'outcome': rnd.choice(DECISION_OUTCOMES + ('x', None)),
                       'who': rnd.choice(['jan@example.com', None]), 'iteration': rnd.choice([1, 2, 2.0, 2.5, None, '2']),
                       'text': rnd.choice(['t', 3, None])}
        elif kind == 'iteration':
            payload = {'number': rnd.choice([1, 2, 3, 3.0, 2.5, 0, -1, '4', None, 10 ** 6]),
                       'path': 'iterations/1/', 'hash': 'h'}
        elif kind == 'accept':
            payload = accept_payload(rnd)
        elif kind == 'unaccept':
            payload = unaccept_payload(rnd)
        elif kind == 'restore':
            payload = {'scope': 'screen', 'screens': {'s1': 'h'}, 'iteration': 1, 'via': 'ui'}
        if kind != 'bogus' and rnd.random() < 0.3:
            payload['extra'] = rnd.choice(GEN_JUNK)
        op_id = rnd.choice(GEN_IDS) if rnd.random() < 0.35 else 'op-%02d-%d' % (index, rnd.randint(0, 3))
        op = {'id': op_id, 'type': kind, 'at': stamp(), 'by': by,
              'payload': rnd.choice([payload] * 12 + [[], None, 'p', 3])}
        if kind in ('accept', 'unaccept', 'iteration', 'restore'):
            if rnd.random() < 0.3:
                op['threadId'] = thread_id
        elif rnd.random() < 0.96:
            op['threadId'] = thread_id if rnd.random() < 0.97 else rnd.choice(['', None, 3])
        roll = rnd.random()
        if roll < 0.02:
            op['at'] = rnd.choice(['', None, 5])
        elif roll < 0.04:
            op['id'] = rnd.choice(['', None, 5])
        if rnd.random() < 0.3:
            op['cid'] = 'c-%d' % index
        if rnd.random() < 0.05:
            op['7'] = 'n'
        ops.append(op)
        # Dublowanie operacji (ten sam `id`, inna treść) i junk zamiast obiektu.
        if rnd.random() < 0.04:
            ops.append(dict(op, at=stamp(), by=actor()))
        if rnd.random() < 0.02:
            ops.append(rnd.choice([None, 7, 'x', [], True]))
    # Kolejność tablicy nie jest kolejnością zdarzeń: czasem tasujemy.
    if rnd.random() < 0.3:
        rnd.shuffle(ops)
    return ops


def generate_corpus(seed, count):
    rnd = random.Random(seed)
    return [generate_log(rnd) for _ in range(count)]


def python_view(ops, module=None):
    """To samo, co liczy `diff.cjs`, ale modułem Pythona (rzutowane przez JSON dla porównania z Node).
    `module`: inna kopia modułu serwera (test mutacyjny); domyślnie `prototype_server`."""
    server = module or globals()['server']
    state = server.fold_operations(ops)
    view = {'fold': {'threads': state['threads'], 'currentIteration': state['currentIteration'],
                     'removed': state['removed'],
                     'acceptance': state['acceptance']},
            'ser': server.serialize_document('demo', [op for op in ops if is_plain_object(op)]),
            'people': server.people_from_operations(ops)}
    return json.loads(json.dumps(view))


def first_difference(left, right, path='$'):
    """Ścieżka pierwszej różnicy (None = równe); bool != liczba, 2.0 == 2 (JSON nie rozróżnia)."""
    if isinstance(left, bool) or isinstance(right, bool):
        same = isinstance(left, bool) and isinstance(right, bool) and left == right
        return None if same else '%s: %r != %r' % (path, left, right)
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                return '%s.%s: klucz tylko po %s stronie' % (path, key, 'prawej' if key not in left else 'lewej')
            found = first_difference(left[key], right[key], '%s.%s' % (path, key))
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return '%s: długość %d != %d' % (path, len(left), len(right))
        for index, (a, b) in enumerate(zip(left, right)):
            found = first_difference(a, b, '%s[%d]' % (path, index))
            if found:
                return found
        return None
    if type(left) in (int, float) and type(right) in (int, float):
        return None if left == right else '%s: %r != %r' % (path, left, right)
    if type(left) is not type(right) or left != right:
        return '%s: %r != %r' % (path, left, right)
    return None


def run_node_v2(node, engine_js, payload, tmp):
    """Uruchamia diff.cjs na zadanej kopii silnika; zwraca zdekodowane wyjście."""
    input_path, output_path = Path(tmp) / 'in.json', Path(tmp) / 'out.json'
    input_path.write_text(json.dumps(payload), encoding='utf-8')
    if output_path.exists():
        output_path.unlink()
    result = subprocess.run([node, str(Path(tmp) / 'diff.cjs'), str(engine_js), str(input_path), str(output_path)],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError('node diff.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
    return json.loads(output_path.read_text(encoding='utf-8'))


def corpus_differences(node, engine_js, tmp, corpora, seeds=DIFF_SEEDS, mutate=None, py_module=None):
    """[(ziarno, indeks, ścieżka, ops)] dla wszystkich rozjazdów Node vs Python (`mutate`: atrapa błędu na wyniku JS)."""
    found = []
    for seed in seeds:
        cases = corpora[seed]
        js = run_node_v2(node, engine_js, {'folds': cases, 'labels': []}, tmp)['folds']
        assert len(js) == len(cases)
        if mutate:
            js = [mutate(view) for view in js]
        for index, (ops, js_view) in enumerate(zip(cases, js)):
            difference = first_difference(python_view(ops, py_module), js_view)
            if difference:
                found.append((seed, index, difference, ops))
    return found


def describe_differences(found):
    seed, index, difference, ops = found[0]
    return ('%d rozjazdów Node<->Python; pierwszy: ziarno %d, dziennik #%d (odtworzenie: generate_corpus(%d, %d)[%d]), '
            'różnica %s; ops=%s' % (len(found), seed, index, seed, DIFF_PER_SEED, index, difference,
                                    json.dumps(ops, ensure_ascii=True)[:1500]))


class EngineV2CopyTests(unittest.TestCase):
    def test_v2_copy_sha256(self):
        actual = sha256(ENGINE_V2_DIR / 'prototype.js')
        self.assertEqual(actual, ENGINE_V2_SHA256, f'Kopia prototype.js v2 rozjechana z zapisanym sha256. {REFRESH_V2}')
        source_md = (ENGINE_V2_DIR / 'SOURCE.md').read_text(encoding='utf-8')
        self.assertIn(ENGINE_V2_SHA256, source_md)
        self.assertRegex(source_md, r'commit `[0-9a-f]{7,40}`')

    def test_v2_copy_matches_library_source(self):
        if os.environ.get('SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS') == '1':
            self.skipTest('SKILLSYNC_PROTOTYPE_ENGINE_SKIP_FRESHNESS=1: kontrola świeżości kopii pominięta jawnie')
        source = os.environ.get('SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE')
        if not source:
            self.skipTest('SKILLSYNC_PROTOTYPE_ENGINE_V2_SOURCE nie ustawione (biblioteka nie jest czytana); '
                          'wskaż skills/prototype/assets/repo/prototype.js biblioteki')
        self.assertEqual(Path(source).read_bytes(), (ENGINE_V2_DIR / 'prototype.js').read_bytes(),
                         f'kopia silnika v2 nieaktualna względem {source}. {REFRESH_V2}')

    def test_engine_under_test_is_an_existing_file(self):
        engine_js = engine_v2_js()
        self.assertTrue(engine_js.is_file(), f'{ENGINE_V2_PATH_ENV} nie wskazuje pliku silnika: {engine_js}')


class EngineV2DifferentialTests(unittest.TestCase):
    corpora = None

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)
        cls.corpora = dict((seed, generate_corpus(seed, DIFF_PER_SEED)) for seed in DIFF_SEEDS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need_node(self):
        if not self.node:
            self.skipTest(self.reason)

    def test_generator_is_deterministic_and_large_enough(self):
        self.assertGreaterEqual(len(DIFF_SEEDS) * DIFF_PER_SEED, 3000)
        again = generate_corpus(DIFF_SEEDS[0], 25)
        self.assertEqual(json.dumps(again, ensure_ascii=True),
                         json.dumps(self.corpora[DIFF_SEEDS[0]][:25], ensure_ascii=True))

    def test_corpus_exercises_the_fold_rules(self):
        # Bez tego zielony test różnicowy mógłby porównywać same puste wyniki.
        seen = {'statuses': set(), 'removed': set(), 'reasons': set(), 'votes': 0, 'edited': 0, 'retracted_msgs': 0,
                'triage': 0, 'decisions': 0, 'iteration>1': 0, 'gap': 0, 'imported': 0, 'dup_id': 0,
                'same_at': 0, 'vote_none': 0, 'no_threads': 0, 'logs': 0}
        for cases in self.corpora.values():
            for ops in cases:
                seen['logs'] += 1
                objects = [op for op in ops if is_plain_object(op)]
                ids = [op.get('id') for op in objects if isinstance(op.get('id'), str) and op.get('id')]
                seen['dup_id'] += len(ids) != len(set(ids))
                ats = [op.get('at') for op in objects if isinstance(op.get('at'), str) and op.get('at')]
                seen['same_at'] += len(ats) != len(set(ats))
                seen['vote_none'] += any(op.get('type') == 'vote' and is_plain_object(op.get('payload')) and
                                         op['payload'].get('value') == 'none' for op in objects)
                view = python_view(ops)['fold']
                seen['no_threads'] += not view['threads']
                seen['iteration>1'] += view['currentIteration'] > 1
                seen['removed'].update(view['removed'].values())
                numbers = [thread['number'] for thread in view['threads']]
                seen['gap'] += numbers != list(range(1, len(numbers) + 1))
                for thread in view['threads']:
                    seen['statuses'].add(thread['status'])
                    if thread['statusReason']:
                        seen['reasons'].add(thread['statusReason'])
                    seen['triage'] += thread['triage'] is not None
                    seen['decisions'] += len(thread['decisions'])
                    for message in thread['messages']:
                        seen['votes'] += len(message['votes'])
                        seen['edited'] += message['edited']
                        seen['retracted_msgs'] += message['retracted']
                        seen['imported'] += message['importedAuthor'] is not None
        self.assertEqual(seen['logs'], len(DIFF_SEEDS) * DIFF_PER_SEED)
        self.assertEqual(seen['statuses'], set(server.STATUSES), seen)
        self.assertEqual(seen['removed'], {'deleted', 'retracted'}, seen)
        for reason in ('author', 'kept', 'out_of_scope'):
            self.assertIn(reason, seen['reasons'], seen)
        for key in ('votes', 'edited', 'retracted_msgs', 'triage', 'decisions', 'iteration>1', 'gap', 'imported',
                    'dup_id', 'same_at', 'vote_none'):
            minimum = 20 if key in ('retracted_msgs', 'edited') else 50  # widoczne wycofane wpisy są z natury rzadsze
            self.assertGreater(seen[key], minimum, '%s zbyt rzadkie w zbiorze: %s' % (key, seen))
        self.assertGreater(seen['no_threads'], 0, seen)

    def test_fold_serialization_and_people_match_node(self):
        self.need_node()
        found = corpus_differences(self.node, self.engine_js, self.tmp, self.corpora)
        self.assertEqual(found, [], describe_differences(found) if found else '')

    def test_known_scenario_matches_node_and_contract(self):
        """Scenariusz z tabeli speca: cudza edycja, wycofanie, głosy none, decyzje, triage, iteracje, kolizja at, duplikat id."""
        self.need_node()
        at = '2026-11-04T10:00:00.000Z'
        ops = [
            {'id': 'op-01', 'type': 'create', 'threadId': 'th-1', 'at': at, 'by': 'anna@example.com',
             'payload': {'message': {'id': 'm-1', 'text': 'Start'}, 'screen': 's1', 'label': 'Przycisk',
                         'anchor': {'kind': 'screen', 'x': 0.5, 'y': 0.5, 'iteration': 1}}},
            {'id': 'op-02', 'type': 'edit', 'threadId': 'th-1', 'at': at, 'by': 'jan@example.com',
             'payload': {'messageId': 'm-1', 'text': 'cudza edycja'}},
            {'id': 'op-03', 'type': 'vote', 'threadId': 'th-1', 'at': at, 'by': 'jan@example.com',
             'payload': {'messageId': 'm-1', 'value': 'up'}},
            {'id': 'op-04', 'type': 'vote', 'threadId': 'th-1', 'at': '2026-11-04T10:00:01.000Z', 'by': 'jan@example.com',
             'payload': {'messageId': 'm-1', 'value': 'none'}},
            {'id': 'op-05', 'type': 'triage', 'threadId': 'th-1', 'at': '2026-11-04T10:00:02.000Z', 'by': 'agent',
             'payload': {'class': 'disputed', 'note': 'spór'}},
            {'id': 'op-06', 'type': 'decision', 'threadId': 'th-1', 'at': '2026-11-04T10:00:03.000Z', 'by': 'agent',
             'payload': {'kind': 'agent', 'outcome': 'keep', 'text': 'zostaje', 'iteration': 1}},
            {'id': 'op-07', 'type': 'iteration', 'at': '2026-11-04T10:00:04.000Z', 'by': 'agent',
             'payload': {'number': 1, 'path': 'iterations/1/', 'hash': 'h'}},
            {'id': 'op-08', 'type': 'create', 'threadId': 'th-2', 'at': '2026-11-04T10:00:05.000Z', 'by': 'jan@example.com',
             'payload': {'message': {'id': 'm-2', 'text': 'Drugi'}, 'screen': 's1', 'label': 'x'}},
            {'id': 'op-09', 'type': 'retract', 'threadId': 'th-2', 'at': '2026-11-04T10:00:06.000Z', 'by': 'jan@example.com',
             'payload': {'messageId': 'm-2'}},
            {'id': 'op-01', 'type': 'delete', 'threadId': 'th-1', 'at': '2026-11-04T10:00:07.000Z', 'by': 'jan@example.com',
             'payload': {}},
        ]
        js = run_node_v2(self.node, self.engine_js, {'folds': [ops], 'labels': []}, self.tmp)['folds'][0]
        py = python_view(ops)
        self.assertIsNone(first_difference(py, js), first_difference(py, js))
        fold = py['fold']
        # Kontrakt: edycja cudza pominięta, duplikat id 'op-01' (delete) odrzucony, wątek 2 ukryty przez retract.
        self.assertEqual(fold['removed'], {'th-2': 'retracted'})
        self.assertEqual(len(fold['threads']), 1)
        thread = fold['threads'][0]
        self.assertEqual((thread['status'], thread['statusReason']), ('closed', 'kept'))
        self.assertEqual(thread['messages'][0]['text'], 'Start')
        self.assertEqual(thread['messages'][0]['votes'], {})
        self.assertEqual(fold['currentIteration'], 2)

    def test_control_comparison_detects_difference(self):
        """Dowód, że porównanie potrafi paść: zmieniony wynik, zmienione wejście po jednej stronie, zmutowany silnik."""
        self.need_node()
        cases = self.corpora[DIFF_SEEDS[0]]
        js_all = run_node_v2(self.node, self.engine_js, {'folds': cases[:100], 'labels': []}, self.tmp)['folds']
        informative = [i for i in range(100) if python_view(cases[i])['fold']['threads']]
        self.assertGreater(len(informative), 20)
        index = informative[0]
        # 1) Zmieniony wynik po stronie Pythona.
        py = python_view(cases[index])
        self.assertIsNone(first_difference(py, js_all[index]))
        py['fold']['threads'][0]['status'] = 'bogus'
        self.assertIn('status', first_difference(py, js_all[index]))
        # 2) Dziennik zmieniony tylko po jednej stronie (Python bez ostatniej operacji).
        mismatched = [i for i in informative if python_view(cases[i][:-1]) != js_all[i]]
        self.assertTrue(mismatched, 'usunięcie operacji powinno zmienić wynik przynajmniej raz')
        self.assertIsNotNone(first_difference(python_view(cases[mismatched[0]][:-1]), js_all[mismatched[0]]))
        # 3) Atrapa błędu silnika: wynik JS z zamknięciem (resolve) zapisanym jako 'deferred'. Pełna procedura różnicowa
        #    musi wskazać ziarno i dziennik.
        def defer_closed(view):
            view = copy.deepcopy(view)
            for thread in view['fold']['threads']:
                if thread['status'] == 'closed':
                    thread['status'] = 'deferred'
            return view
        found = corpus_differences(self.node, self.engine_js, self.tmp, self.corpora, seeds=DIFF_SEEDS[:1], mutate=defer_closed)
        self.assertTrue(found, 'zmutowany wynik nie został wykryty przez test różnicowy')
        message = describe_differences(found)
        self.assertIn('ziarno %d' % DIFF_SEEDS[0], message)
        self.assertIn('status', message)


PY_VIEW_RULE = "'view': 'mobile' if payload.get('view') == 'mobile' else 'desktop',"
# Mutant reguły D4 po stronie Pythona: każda niepusta wartość inna niż "desktop" uchodzi za mobile.
PY_VIEW_RULE_MUTANT = "'view': 'mobile' if payload.get('view') not in (None, '', 'desktop') else 'desktop',"
ENGINE_V2_PREV_JS = Path(__file__).resolve().parent / 'fixtures' / 'prototype' / 'engine-v2-prev' / 'prototype.js'
# Reguła D4 w kodzie silnika (fold z `create`): tylko dokładnie "mobile" daje mobile.
VIEW_RULE_JS = "(isObject(payload) && payload.view === MOBILE ? MOBILE : DESKTOP)"
# Mutant reguły: każda niepusta, niedomyślna wartość uchodzi za mobile ("tablet", 5, true, ...).
VIEW_RULE_MUTANT_JS = "(isObject(payload) && payload.view != null && payload.view !== DESKTOP && payload.view !== '' ? MOBILE : DESKTOP)"


def install_engine_copy(tmp, engine_source, transform=None):
    """Kopiuje `diff.cjs` i wskazany plik silnika (opcjonalnie po zamianie tekstu) do `tmp`; zwraca ścieżkę silnika."""
    shutil.copy2(ENGINE_V2_DIR / 'diff.cjs', tmp)
    text = Path(engine_source).read_text(encoding='utf-8')
    if transform:
        text = transform(text)
    engine_js = Path(tmp) / 'prototype.js'
    engine_js.write_text(text, encoding='utf-8')
    return engine_js


def view_create_op(thread_id, number, view=None, with_view=True):
    payload = {'message': {'id': 'm-%d' % number, 'text': 'Tekst'}, 'screen': 's1', 'label': 'Przycisk',
               'anchor': {'kind': 'screen', 'x': 0.5, 'y': 0.5, 'iteration': 1}}
    if with_view:
        payload['view'] = view
    return {'id': 'op-%d' % number, 'type': 'create', 'threadId': thread_id, 'at': '2026-11-06T10:00:%02d.000Z' % number,
            'by': 'anna@example.com', 'payload': payload}


class EngineV2ViewModeTests(unittest.TestCase):
    """Tryb widoku (K8, D4): pole `view` w `create`, mutacja reguły D4, odczyt wsteczny poprzednim silnikiem, kolejka sprzed zmiany."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-view-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)
        cls.corpora = dict((seed, generate_corpus(seed, DIFF_PER_SEED)) for seed in DIFF_SEEDS)

    def need_node(self):
        if not self.node:
            self.skipTest(self.reason)

    def js(self, expression):
        """Wartość wyrażenia JS nad `api` (eksporty silnika) jako JSON; `expression` należy do testu."""
        self.need_node()
        script = 'const api=require(process.argv[1]);process.stdout.write(JSON.stringify(%s));' % expression
        result = subprocess.run([self.node, '-e', script, str(self.engine_js)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return json.loads(result.stdout)

    def test_corpus_emits_every_kind_of_view_and_python_folds_the_rule(self):
        seen = {'mobile': 0, 'desktop': 0, 'absent': 0, 'invalid': 0}
        for cases in self.corpora.values():
            for ops in cases:
                creates = [op for op in ops if is_plain_object(op) and op.get('type') == 'create' and is_plain_object(op.get('payload'))]
                for op in creates:
                    value = op['payload'].get('view', '<brak>')
                    seen['absent' if value == '<brak>' else value if value in ('mobile', 'desktop') else 'invalid'] += 1
                for thread in python_view(ops)['fold']['threads']:
                    self.assertIn(thread['view'], ('desktop', 'mobile'))
        for key in ('mobile', 'desktop', 'absent', 'invalid'):
            self.assertGreater(seen[key], 50, 'rodzaj pola view zbyt rzadki w korpusie: %s' % seen)
        # Reguła D4 na poszczególnych wartościach (po stronie Pythona, niezależnie od korpusu).
        for value, expected in (('mobile', 'mobile'), ('desktop', 'desktop'), ('tablet', 'desktop'), (5, 'desktop'),
                                (None, 'desktop'), ('Mobile', 'desktop'), (['mobile'], 'desktop')):
            fold = server.fold_operations([view_create_op('th-1', 1, value)])
            self.assertEqual(fold['threads'][0]['view'], expected, repr(value))
        fold = server.fold_operations([view_create_op('th-1', 1, with_view=False)])
        self.assertEqual(fold['threads'][0]['view'], 'desktop')

    def test_view_of_threads_matches_between_node_and_python(self):
        self.need_node()
        compared = 0
        for seed in DIFF_SEEDS:
            cases = self.corpora[seed]
            js = run_node_v2(self.node, self.engine_js, {'folds': cases, 'labels': []}, self.tmp)['folds']
            for index, (ops, js_view) in enumerate(zip(cases, js)):
                python_views = [(t['id'], t['number'], t['view']) for t in python_view(ops)['fold']['threads']]
                js_views = [(t['id'], t['number'], t.get('view')) for t in js_view['fold']['threads']]
                self.assertEqual(python_views, js_views, 'ziarno %d, dziennik #%d: view wątków' % (seed, index))
                compared += len(python_views)
        self.assertGreater(compared, 3000)

    def test_mutated_view_rule_is_caught_by_the_differential(self):
        """Mechanizm stały: zmiana reguły D4 w kopii silnika (tmp) musi dać rozjazd; kopia bez zmiany go nie daje."""
        self.need_node()
        seeds = DIFF_SEEDS[:2]
        for name, transform, expect_difference in (('control', None, False),
                                                    ('mutant', lambda text: text.replace(VIEW_RULE_JS, VIEW_RULE_MUTANT_JS), True)):
            directory = tempfile.mkdtemp(prefix='skillsync-engine-mut-%s-' % name)
            self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
            engine_js = install_engine_copy(directory, self.engine_js, transform)
            if expect_difference:
                self.assertEqual(engine_js.read_text(encoding='utf-8').count(VIEW_RULE_MUTANT_JS), 1,
                                 'reguła D4 w silniku nie ma oczekiwanej postaci; zaktualizuj VIEW_RULE_JS')
            found = corpus_differences(self.node, engine_js, directory, self.corpora, seeds=seeds)
            if expect_difference:
                self.assertTrue(found, 'zmutowana reguła D4 nie została wykryta przez test różnicowy')
                self.assertIn('view', describe_differences(found))
            else:
                self.assertEqual(found, [], describe_differences(found) if found else '')
        # Mutacja po stronie wyniku Pythona/JS (atrapa): mobile zapisane jako desktop też musi być widoczne.
        def forget_mobile(result):
            result = copy.deepcopy(result)
            for thread in result['fold']['threads']:
                if thread.get('view') == 'mobile':
                    thread['view'] = 'desktop'
            return result
        found = corpus_differences(self.node, self.engine_js, self.tmp, self.corpora, seeds=DIFF_SEEDS[:1], mutate=forget_mobile)
        self.assertTrue(found)
        self.assertIn('view', describe_differences(found))

    def test_python_fold_mutant_of_view_rule_is_caught_by_the_differential(self):
        """Prawdziwy mutant D4 po stronie Pythona: kopia `prototype_server.py` w tmp z podmienioną regułą, ładowana importlib."""
        self.need_node()
        import importlib.util
        source = Path(server.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count(PY_VIEW_RULE), 1, 'reguła D4 w prototype_server.py zmieniła postać; zaktualizuj PY_VIEW_RULE')
        directory = tempfile.mkdtemp(prefix='skillsync-server-mut-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        modules = {}
        for name, text in (('server_control', source), ('server_mutant', source.replace(PY_VIEW_RULE, PY_VIEW_RULE_MUTANT))):
            path = Path(directory) / (name + '.py')
            path.write_text(text, encoding='utf-8')
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            self.addCleanup(sys.modules.pop, name, None)
            spec.loader.exec_module(module)
            modules[name] = module
        seeds = DIFF_SEEDS[:2]
        control = corpus_differences(self.node, self.engine_js, self.tmp, self.corpora, seeds=seeds, py_module=modules['server_control'])
        self.assertEqual(control, [], describe_differences(control) if control else '')
        found = corpus_differences(self.node, self.engine_js, self.tmp, self.corpora, seeds=seeds, py_module=modules['server_mutant'])
        self.assertTrue(found, 'zmutowany fold Pythona nie został wykryty przez test różnicowy')
        self.assertIn('view', describe_differences(found))

    def test_panel_pure_functions_apply_view_filter_times_status_filter(self):
        """filterThreads/threadMatches/filterCounts z `viewFilter` all/desktop/mobile: iloczyn z każdym filtrem statusu."""
        self.need_node()
        threads, number = [], 0
        for status in server.STATUSES:
            for view in ('mobile', 'desktop', None, 'tablet'):
                for handle in (True, False):
                    number += 1
                    thread = {'id': 't%d%s' % (number, 'h' if handle else 'x'), 'number': number, 'screen': 's%d' % (number % 3 + 1),
                              'status': status, 'anchor': None, 'messages': [], 'lastAt': 'x',
                              'triage': {'class': 'clear'} if number % 4 == 0 else None,
                              'decisions': [{'outcome': 'keep'}] if number % 5 == 0 else [],
                              'events': [{'type': 'reply', 'by': 'jan@example.com', 'at': '2026-11-06T10:00:%02d.000Z' % (number % 60)}]}
                    if view is not None:
                        thread['view'] = view
                    threads.append(thread)
        expression = (
            '(()=>{const T=%s;const out={};'
            'for (const vf of ["all","desktop","mobile"]) {'
            'const ctx={viewFilter:vf,me:"anna@example.com",seenAt:"2026-11-06T10:00:20.000Z",hasHandle:(t)=>t.id.endsWith("h")};'
            'out[vf]={counts:api.filterCounts(T,ctx)};'
            'for (const f of ["open","new","agent","lost","all"]) {'
            'out[vf][f]={ids:T.filter((t)=>api.threadMatches(t,f,ctx)).map((t)=>t.id),'
            'grouped:api.filterThreads(T,f,ctx).flatMap((g)=>g.threads.map((t)=>t.id))};}}'
            'return out;})()' % json.dumps(threads))
        result = self.js(expression)
        by_id = dict((t['id'], t) for t in threads)
        for view_filter in ('all', 'desktop', 'mobile'):
            for status_filter in ('open', 'new', 'agent', 'lost', 'all'):
                matched = result[view_filter][status_filter]['ids']
                self.assertEqual(sorted(result[view_filter][status_filter]['grouped']), sorted(matched))
                self.assertEqual(result[view_filter]['counts'][status_filter], len(matched), (view_filter, status_filter))
                if view_filter != 'all':
                    for thread_id in matched:
                        self.assertEqual('mobile' if by_id[thread_id].get('view') == 'mobile' else 'desktop', view_filter)
        for status_filter in ('open', 'new', 'agent', 'lost', 'all'):
            everything = set(result['all'][status_filter]['ids'])
            mobile = set(result['mobile'][status_filter]['ids'])
            desktop = set(result['desktop'][status_filter]['ids'])
            self.assertEqual(mobile | desktop, everything, 'Mobile + Desktop = Wszystkie (%s)' % status_filter)
            self.assertEqual(mobile & desktop, set(), 'widoki rozłączne (%s)' % status_filter)
            self.assertEqual(result['mobile']['counts'][status_filter] + result['desktop']['counts'][status_filter],
                             result['all']['counts'][status_filter])
        # Filtr statusu zawęża tak samo niezależnie od widoku: otwarte i z uchwytem, wszystkie statusy.
        open_ids = set(result['all']['open']['ids'])
        self.assertEqual(open_ids, set(t['id'] for t in threads if t['status'] in server.OPEN_STATUSES and t['id'].endswith('h')))
        self.assertTrue(open_ids and set(result['mobile']['open']['ids']) < open_ids)
        self.assertEqual(set(result['all']['all']['ids']), set(by_id))
        # Dowód, że asercja potrafi paść: wątek mobile podmieniony na desktop zmienia zbiór mobile.
        self.assertTrue(result['mobile']['all']['ids'])
        self.assertNotEqual(set(result['mobile']['all']['ids']), set(result['desktop']['all']['ids']))

    def test_previous_engine_reads_log_with_view_without_losing_threads(self):
        """Odczyt wsteczny (Odwrócenie zmian): poprzedni silnik czyta `create` z `view` bez błędu i bez utraty wątków."""
        self.need_node()
        self.assertTrue(ENGINE_V2_PREV_JS.is_file(), 'brak kopii poprzedniego silnika: %s' % ENGINE_V2_PREV_JS)
        directory = tempfile.mkdtemp(prefix='skillsync-engine-prev-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        prev_js = install_engine_copy(directory, ENGINE_V2_PREV_JS)
        explicit = [view_create_op('th-1', 1, 'mobile'), view_create_op('th-2', 2, 'tablet'), view_create_op('th-3', 3, 5),
                    view_create_op('th-4', 4, None), view_create_op('th-5', 5, with_view=False), view_create_op('th-6', 6, 'desktop')]
        cases = [explicit] + self.corpora[DIFF_SEEDS[0]][:300]
        old = run_node_v2(self.node, prev_js, {'folds': cases, 'labels': []}, directory)['folds']
        new = run_node_v2(self.node, self.engine_js, {'folds': cases, 'labels': []}, self.tmp)['folds']
        self.assertEqual(len(old), len(cases))
        with_threads = 0
        for index, (old_view, new_view) in enumerate(zip(old, new)):
            old_threads, new_threads = old_view['fold']['threads'], new_view['fold']['threads']
            self.assertEqual([(t['id'], t['number']) for t in old_threads], [(t['id'], t['number']) for t in new_threads],
                             'dziennik #%d: poprzedni silnik zgubił albo przenumerował wątki' % index)
            self.assertEqual(old_view['fold']['removed'], new_view['fold']['removed'])
            for thread in old_threads:
                self.assertNotIn('view', thread, 'poprzedni silnik nie zna pola view')
            with_threads += len(old_threads)
        self.assertEqual([t['number'] for t in old[0]['fold']['threads']], [1, 2, 3, 4, 5, 6])
        self.assertEqual([t['view'] for t in new[0]['fold']['threads']], ['mobile', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop'])
        self.assertGreater(with_threads, 100)

    def test_offline_queue_entry_without_view_folds_to_desktop(self):
        """Wpis kolejki offline sprzed zmiany (`create` bez `view`) przechodzi przez scalenie bez zmian i daje wątek desktop."""
        self.need_node()
        entry = {'cid': 'c-old', 'type': 'create',
                 'payload': {'message': {'id': 'm-1', 'text': 'Z kolejki'}, 'screen': 's1', 'label': 'Przycisk',
                             'anchor': {'kind': 'screen', 'x': 0.5, 'y': 0.5, 'iteration': 1}}}
        input_path, output_path = Path(self.tmp) / 'vq-in.json', Path(self.tmp) / 'vq-out.json'
        input_path.write_text(json.dumps({'calls': [{'fn': 'mergeQueueItems', 'args': [[entry], []]}]}), encoding='utf-8')
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path), str(output_path)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        merged = json.loads(output_path.read_text(encoding='utf-8'))[0]
        self.assertEqual(merged, [entry])
        self.assertNotIn('view', merged[0]['payload'])
        # Serwer zapisuje payload wpisu bez zmian; fold w JS i w Pythonie daje desktop.
        op = {'id': 'op-q1', 'type': entry['type'], 'threadId': 'th-q1', 'at': '2026-11-06T10:00:00.000Z', 'by': 'anna@example.com',
              'cid': entry['cid'], 'payload': merged[0]['payload']}
        js = run_node_v2(self.node, self.engine_js, {'folds': [[op]], 'labels': []}, self.tmp)['folds'][0]
        self.assertEqual([t['view'] for t in js['fold']['threads']], ['desktop'])
        self.assertEqual([t['view'] for t in python_view([op])['fold']['threads']], ['desktop'])
        # Dowód, że asercja potrafi paść: ten sam wpis z `view: mobile` daje mobile.
        marked = dict(op, payload=dict(op['payload'], view='mobile'))
        js = run_node_v2(self.node, self.engine_js, {'folds': [[marked]], 'labels': []}, self.tmp)['folds'][0]
        self.assertEqual([t['view'] for t in js['fold']['threads']], ['mobile'])


LABEL_CASES = {
    'kolizja imion (Paweł K. / Paweł B.)': {
        'logins': ['pawel.kowalski@example.com', 'pawel.bak@example.com', 'anna.lis@example.com'], 'names': {},
        'labels': {'pawel.kowalski@example.com': 'Pawel K.', 'pawel.bak@example.com': 'Pawel B.', 'anna.lis@example.com': 'Anna'}},
    'kolizja imion z polskimi literami': {
        'logins': ['paweł.kowalski@example.com', 'paweł.bąk@example.com'], 'names': {},
        'labels': {'paweł.kowalski@example.com': 'Paweł K.', 'paweł.bąk@example.com': 'Paweł B.'}},
    'kolizja inicjałów (pełne nazwisko)': {
        'logins': ['pawel.kowalski@example.com', 'pawel.kot@example.com', 'pawel.bak@example.com'], 'names': {},
        'labels': {'pawel.kowalski@example.com': 'Pawel Kowalski', 'pawel.kot@example.com': 'Pawel Kot', 'pawel.bak@example.com': 'Pawel B.'}},
    'name z pliku (pierwszy wyraz)': {
        'logins': ['pk@example.com', 'anna.lis@example.com'], 'names': {'pk@example.com': 'Piotr Kowalczyk-Nowak'},
        'labels': {'pk@example.com': 'Piotr', 'anna.lis@example.com': 'Anna'}},
    'name z pliku w kolizji imion': {
        'logins': ['a@example.com', 'b@example.com', 'c@example.com'],
        'names': {'a@example.com': 'Jan  Kowalski', 'b@example.com': 'Jan Nowak', 'c@example.com': 'Jan'},
        'labels': {'a@example.com': 'Jan K.', 'b@example.com': 'Jan N.', 'c@example.com': 'Jan'}},
    'jedna osoba': {
        'logins': ['pawel.kowalski@example.com'], 'names': {}, 'labels': {'pawel.kowalski@example.com': 'Pawel'}},
    'ten sam login dwa razy': {
        'logins': ['anna.lis@example.com', 'anna.lis@example.com'], 'names': {}, 'labels': {'anna.lis@example.com': 'Anna'}},
    'login bez kropki i bez nazwiska w kolizji': {
        'logins': ['jan@example.com', 'jan.kos@example.com'], 'names': {}, 'labels': {'jan@example.com': 'Jan', 'jan.kos@example.com': 'Jan K.'}},
}


class EngineV2LabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-label-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def js_labels(self, items):
        if not self.node:
            self.skipTest(self.reason)
        return run_node_v2(self.node, self.engine_js, {'folds': [], 'labels': items}, self.tmp)['labels']

    def python_labels(self, item):
        return json.loads(json.dumps(server.compute_labels(item['logins'], item['names'])))

    def test_python_label_matches_spec_examples(self):
        # Kontrakt (spec, `people`): firstName; kolizja imion -> „Imię N.”; kolizja inicjałów -> imię i pełne nazwisko.
        for name, case in LABEL_CASES.items():
            with self.subTest(name):
                result = self.python_labels(case)
                self.assertEqual({login: entry['label'] for login, entry in result.items()}, case['labels'])

    def test_js_label_matches_spec_examples(self):
        items = list(LABEL_CASES.values())
        for (name, case), result in zip(LABEL_CASES.items(), self.js_labels(items)):
            with self.subTest(name):
                self.assertEqual({login: entry['label'] for login, entry in result.items()}, case['labels'])

    def test_labels_python_equal_js_including_full_entries(self):
        items = list(LABEL_CASES.values())
        for (name, case), js in zip(LABEL_CASES.items(), self.js_labels(items)):
            with self.subTest(name):
                self.assertIsNone(first_difference(self.python_labels(case), js), name)

    def test_people_from_log_match_js_for_collisions(self):
        """`people` z dziennika (tryb bez API) = to samo w obu językach, także przy obu kolizjach naraz."""
        if not self.node:
            self.skipTest(self.reason)
        logins = ['pawel.kowalski@example.com', 'pawel.bak@example.com', 'pawel.kot@example.com', 'pawel.kowalski@example.com', 'anna@example.com']
        ops = [{'id': 'op-%d' % i, 'type': 'vote', 'threadId': 'th-1', 'at': '2026-11-04T10:00:%02d.000Z' % i, 'by': by,
                'payload': {'messageId': 'm', 'value': 'up'}} for i, by in enumerate(logins)]
        js = run_node_v2(self.node, self.engine_js, {'folds': [ops], 'labels': []}, self.tmp)['folds'][0]['people']
        py = json.loads(json.dumps(server.people_from_operations(ops)))
        self.assertIsNone(first_difference(py, js))
        self.assertEqual(py['pawel.kowalski@example.com']['label'], 'Pawel Kowalski')
        self.assertEqual(py['pawel.kot@example.com']['label'], 'Pawel Kot')
        self.assertEqual(py['pawel.bak@example.com']['label'], 'Pawel B.')
        self.assertEqual(py['anna@example.com']['label'], 'Anna')

    def test_control_label_comparison_detects_difference(self):
        if not self.node:
            self.skipTest(self.reason)
        case = LABEL_CASES['kolizja imion (Paweł K. / Paweł B.)']
        python_side = self.python_labels(case)
        # Atrapa błędu silnika: wynik JS bez kropki po inicjale.
        dotless = {login: dict(entry, label=entry['label'].rstrip('.')) for login, entry in self.js_labels([case])[0].items()}
        difference = first_difference(python_side, dotless)
        self.assertIsNotNone(difference)
        self.assertIn('label', difference)
        # Przetasowane etykiety po jednej stronie też muszą dać różnicę.
        swapped = dict(python_side, **{'pawel.kowalski@example.com': python_side['pawel.bak@example.com'],
                                       'pawel.bak@example.com': python_side['pawel.kowalski@example.com']})
        self.assertIsNotNone(first_difference(swapped, self.js_labels([case])[0]))


QUEUEABLE = ['create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen', 'reanchor']


class EngineV2QueueTests(unittest.TestCase):
    """M2/M3 z code-review-1.9-eng-1: scalanie kolejki po cid, import v1 (K31), klasyfikacja wyniku zapisu."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-queue-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, calls):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 'q-in.json', Path(self.tmp) / 'q-out.json'
        input_path.write_text(json.dumps({'calls': calls}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js),
                                 str(input_path), str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def merge(self, stored, local, removed=None):
        return self.call([{'fn': 'mergeQueueItems', 'args': [stored, local, removed]}])[0]

    def convert(self, operations, known_ids=(), known_threads=(), deterministic=False):
        return self.call([{'fn': 'convertV1Operations',
                           'args': [operations, {'knownIds': list(known_ids), 'knownThreads': list(known_threads),
                                                 'deterministic': deterministic}]}])[0]

    def import_id(self, kind, source):
        return self.call([{'fn': 'v1ImportId', 'args': [kind, source]}])[0]

    # --- mergeQueueItems (M2) ---

    def test_two_tabs_both_entries_survive(self):
        a, b = {'cid': 'a', 'type': 'create'}, {'cid': 'b', 'type': 'reply'}
        # Karta B zna tylko [b]; w localStorage jest wpis karty A.
        self.assertEqual(self.merge([a], [b]), [a, b])
        # To samo dla listy „Odrzuconych” (ta sama funkcja, własny klucz).
        ra, rb = {'cid': 'ra', 'error': 'x'}, {'cid': 'rb', 'error': 'y'}
        self.assertEqual(self.merge([ra], [rb]), [ra, rb])

    def test_naive_overwrite_loses_entry_control(self):
        a, b = {'cid': 'a'}, {'cid': 'b'}
        naive = [b]  # dawne zachowanie: zapis pamięci karty nadpisuje klucz
        self.assertNotIn(a, naive)
        self.assertIn(a, self.merge([a], [b]))

    def test_merge_order_dedup_and_removed(self):
        s = [{'cid': 'a'}, {'cid': 'b'}]
        l = [{'cid': 'c'}, {'cid': 'a', 'text': 'lokalny'}, {'cid': 'd'}]
        # Najpierw zapisane (kolejność localStorage), potem tylko lokalne; duplikat cid = wersja zapisana.
        self.assertEqual([i['cid'] for i in self.merge(s, l)], ['a', 'b', 'c', 'd'])
        self.assertEqual(self.merge(s, l)[0], {'cid': 'a'})
        # Duplikaty wewnątrz jednej listy też nie dublują.
        self.assertEqual([i['cid'] for i in self.merge([{'cid': 'x'}, {'cid': 'x'}], [])], ['x'])
        # Pozycje wysłane/odrzucone/usunięte w tej karcie znikają z obu źródeł.
        self.assertEqual([i['cid'] for i in self.merge(s, l, ['b', 'c'])], ['a', 'd'])

    def test_merge_empty_and_corrupt_inputs(self):
        self.assertEqual(self.merge([], []), [])
        self.assertEqual(self.merge(None, None), [])
        self.assertEqual(self.merge('nie lista', {'cid': 'a'}), [])
        self.assertEqual(self.merge([{'cid': 'a'}], None), [{'cid': 'a'}])
        self.assertEqual(self.merge(None, [{'cid': 'a'}]), [{'cid': 'a'}])
        bad = [None, 5, 'x', [], {}, {'cid': ''}, {'cid': 7}, {'text': 'bez cid'}, {'cid': 'ok'}]
        self.assertEqual(self.merge(bad, bad), [{'cid': 'ok'}])
        self.assertEqual(self.merge([{'cid': 'ok'}], [], [None, 3, 'ok']), [])

    def test_control_merge_detects_lost_entry(self):
        # Atrapa błędu: scalanie, które pomija wpisy zapisane przez inną kartę (zostają tylko lokalne).
        out = self.merge([{'cid': 'a'}], [{'cid': 'b'}])
        assert_detects(self, lambda value: self.assertEqual(value, [{'cid': 'a'}, {'cid': 'b'}]), out, [{'cid': 'b'}])

    # --- convertV1Operations (M3 / K31) ---

    @staticmethod
    def v1(op_id, type_, thread, at, payload=None):
        return {'id': op_id, 'type': type_, 'threadId': thread, 'at': at, 'payload': payload or {}}

    def v1_create(self, op_id, thread, at, text='Treść', author='Recenzent A', screen='s1'):
        return self.v1(op_id, 'create', thread, at, {
            'screen': screen, 'anchor': 'div>button', 'label': 'Nowy',
            'message': {'id': 'm-' + op_id, 'author': author, 'text': text, 'at': at}})

    def v1_reply(self, op_id, thread, at, text='Odp', author='B'):
        return self.v1(op_id, 'reply', thread, at, {'message': {'id': 'm-' + op_id, 'author': author, 'text': text, 'at': at}})

    def test_convert_maps_threads_order_and_author(self):
        ops = [self.v1_reply('o2', 'T1', '2026-01-01T10:05:00Z', 'Tak', 'Anna'),  # celowo przed create w tablicy
               self.v1_create('o1', 'T1', '2026-01-01T10:00:00Z', 'Pytanie', 'Jan'),
               self.v1('o3', 'resolve', 'T1', '2026-01-01T10:06:00Z'),
               self.v1('o4', 'reopen', 'T1', '2026-01-01T10:07:00Z')]
        items = self.convert(ops)
        self.assertEqual([i['type'] for i in items], ['create', 'reply', 'resolve', 'reopen'])
        thread = items[0]['threadId']
        self.assertEqual(thread, 'th-1')
        self.assertNotEqual(thread, 'T1')  # nowy identyfikator, nie v1
        self.assertTrue(all(i['threadId'] == thread for i in items))
        self.assertEqual(items[0]['payload']['message']['text'],
                         'Wpis z poprzedniej wersji prototypu (autor: Jan):\nPytanie')
        self.assertIn('(autor: Anna)', items[1]['payload']['message']['text'])
        self.assertEqual(items[0]['payload']['screen'], 's1')
        self.assertEqual(items[2]['payload'], {'reason': 'author'})
        self.assertNotIn('importedAuthor', json.dumps(items))
        self.assertTrue(set(i['type'] for i in items) <= set(QUEUEABLE))

    def test_convert_skips_known_ids_deleted_and_unknown_threads(self):
        ops = [self.v1_create('o1', 'A', '2026-01-01T10:00:00Z'),
               self.v1_reply('o2', 'A', '2026-01-01T10:01:00Z'),
               self.v1_create('o3', 'B', '2026-01-01T10:02:00Z'),
               self.v1('o4', 'delete', 'B', '2026-01-01T10:03:00Z'),
               self.v1_reply('o5', 'ZZZ', '2026-01-01T10:04:00Z'),   # wątek nieznany
               self.v1('o6', 'resolve', 'ZZZ', '2026-01-01T10:05:00Z'),
               self.v1('o7', 'delete', 'A2', '2026-01-01T10:06:00Z')]
        items = self.convert(ops, known_ids=['o2'])
        self.assertEqual([i['type'] for i in items], ['create'])  # o2 znane, B usunięty, ZZZ nieznany, delete nie jest importowany
        self.assertNotIn('delete', [i['type'] for i in self.convert(ops)])
        self.assertEqual([i['type'] for i in self.convert(ops)], ['create', 'reply'])

    def test_convert_known_thread_in_v2_reply_keeps_thread_id(self):
        ops = [self.v1_reply('o1', 'T-v2', '2026-01-01T10:00:00Z'), self.v1_create('o2', 'T-v2', '2026-01-01T09:00:00Z')]
        items = self.convert(ops, known_threads=['T-v2'])
        # create istniejącego wątku nie jest powtarzany; reply trafia do istniejącego wątku.
        self.assertEqual([(i['type'], i['threadId']) for i in items], [('reply', 'T-v2')])
        self.assertEqual(self.convert([self.v1_reply('o1', 'T-v2', '2026-01-01T10:00:00Z')]), [])

    def test_convert_rejects_malformed_and_empty(self):
        self.assertEqual(self.convert([]), [])
        self.assertEqual(self.convert(None), [])
        bad = [{'id': 'x'}, 5, None, self.v1('o1', 'create', 'T', '2026-01-01T00:00:00Z', {'screen': 's1'}),  # bez message
               self.v1('o2', 'vote', 'T', '2026-01-01T00:00:01Z')]
        self.assertEqual(self.convert(bad), [])

    def test_convert_real_v1_export_counts(self):
        text = (Path(__file__).resolve().parent / 'fixtures' / 'prototype' / 'export-comments.js').read_text(encoding='utf-8')
        parsed = engine.parse_comments_document(text)
        ops = parsed['value']['operations']
        by_type = {}
        for op in ops:
            by_type[op['type']] = by_type.get(op['type'], 0) + 1
        deleted = set(op['threadId'] for op in ops if op['type'] == 'delete')
        live = [op for op in ops if op['threadId'] not in deleted and op['type'] != 'delete']
        creates = [op for op in live if op['type'] == 'create']
        replies = [op for op in live if op['type'] == 'reply']
        self.assertGreaterEqual(len(creates), 1)
        self.assertGreaterEqual(len(replies), 1)
        self.assertGreaterEqual(len(deleted), 1, 'fikstura ma mieć usunięty wątek (inaczej test niczego nie dowodzi)')
        items = self.convert(ops)
        counts = {}
        for item in items:
            counts[item['type']] = counts.get(item['type'], 0) + 1
        self.assertEqual(counts.get('create', 0), len(creates))
        self.assertEqual(counts.get('reply', 0), len(replies))
        self.assertEqual(len(items), len(live))  # wszystkie żywe operacje v1 mają odpowiednik
        self.assertTrue(set(counts) <= set(QUEUEABLE))
        self.assertEqual(len(set(i['threadId'] for i in items if i['type'] == 'create')), len(creates))
        # Wszystkie operacje wątków dostają nowe id; żadne id wątku v1 nie wycieka.
        v1_threads = set(op['threadId'] for op in ops)
        self.assertFalse(v1_threads & set(i['threadId'] for i in items))
        # Po zaimportowaniu tych samych id nie ma nic do zrobienia (idempotencja względem dziennika).
        self.assertEqual(self.convert(ops, known_ids=[op['id'] for op in ops]), [])

    # --- v1ImportId (N4): deterministyczne identyfikatory importu ---

    def test_v1_import_id_deterministic_distinct_and_server_patterns(self):
        pairs = [('th', 'thread-a'), ('m', 'op-0001'), ('c', 'op-0001'), ('c', 'op-0002'), ('th', 'thread-b'), ('m', 'op-0002')]
        first = self.call([{'fn': 'v1ImportId', 'args': list(p)} for p in pairs])
        second = self.call([{'fn': 'v1ImportId', 'args': list(p)} for p in pairs])
        self.assertEqual(first, second)
        self.assertEqual(len(set(first)), len(pairs))  # różne id v1 lub rodzaj -> różne wyniki
        self.assertEqual(first[0][:3], 'th-')
        self.assertEqual(first[1][:2], 'm-')
        self.assertEqual(first[2][:5], 'c-v1-')
        # Wzorce serwera (prototype_server.py): THREAD_ID_RE, MESSAGE_ID_RE, CID_RE.
        self.assertRegex(first[0], r'^' + server.THREAD_ID_RE.pattern + r'$')
        self.assertRegex(first[1], r'^' + server.MESSAGE_ID_RE.pattern + r'$')
        for value in (first[2], first[3]):
            self.assertTrue(server.CID_RE.fullmatch(value), value)
        for value, size in ((first[0], 26 + 3), (first[1], 26 + 2), (first[2], 26 + 5)):
            self.assertEqual(len(value), size)
        self.assertNotEqual(self.import_id('th', 'x'), self.import_id('m', 'x')[2:].join(['th-', '']))

    def v1_export_ops(self):
        text = (Path(__file__).resolve().parent / 'fixtures' / 'prototype' / 'export-comments.js').read_text(encoding='utf-8')
        return engine.parse_comments_document(text)['value']['operations']

    def test_convert_twice_gives_identical_cids(self):
        ops = self.v1_export_ops()
        one = self.convert(ops, deterministic=True)
        two = self.convert(ops, deterministic=True)
        self.assertTrue(one)
        self.assertEqual(one, two)  # serwer zdejmie duplikaty po (by, cid)
        cids = [i['cid'] for i in one]
        self.assertEqual(len(cids), len(set(cids)))
        for item in one:
            self.assertTrue(server.CID_RE.fullmatch(item['cid']), item['cid'])
            self.assertRegex(item['threadId'], r'^' + server.THREAD_ID_RE.pattern + r'$')
            if 'message' in item['payload']:
                self.assertRegex(item['payload']['message']['id'], r'^' + server.MESSAGE_ID_RE.pattern + r'$')

    def test_control_random_ids_would_duplicate(self):
        ops = self.v1_export_ops()
        one, two = self.convert(ops), self.convert(ops)  # bez idFor: newId + brak cid (zachowanie dawne)
        self.assertEqual(len(one), len(two))
        self.assertTrue(all('cid' not in i for i in one))
        # Losowe identyfikatory (newClientId) nie dałyby wspólnego cid: dwie próby = rozłączne zbiory, serwer zapisałby dwa razy.
        first = self.call([{'fn': 'newClientId', 'args': ['c', 1700000000000]}] * 1)
        self.assertTrue(first)
        draws = self.call([{'fn': 'newClientId', 'args': ['c', 1700000000000]} for _ in range(2)])
        self.assertEqual(len(draws), 2)
        self.assertNotEqual(draws[0], draws[1])

    def test_control_import_id_detects_nondeterminism(self):
        ops = self.v1_export_ops()
        one = [i['cid'] for i in self.convert(ops, deterministic=True)]
        two = [i['cid'] for i in self.convert(ops, deterministic=True)]
        # Atrapa błędu: losowy ostatni znak identyfikatora importu przy każdym wywołaniu.
        random_tail = [cid[:-1] + ('0' if cid[-1] != '0' else '1') for cid in two]
        assert_detects(self, lambda pair: self.assertEqual(pair[0], pair[1]), (one, two), (one, random_tail))

    # --- classifyWriteResult ---

    def test_classify_write_result(self):
        cases = [(200, None, 'done'), (201, None, 'done'), (204, None, 'done'), (0, None, 'offline'), (None, None, 'offline'),
                 (401, None, 'auth'), (401, 'invalid_csrf', 'auth'),
                 (403, 'invalid_csrf', 'csrf'), (403, 'invalid_origin', 'csrf'),
                 (403, 'forbidden', 'reject'), (403, None, 'reject'),
                 (403, 'password_change_required', 'password'),
                 (500, None, 'retry'), (502, None, 'retry'), (503, None, 'retry'), (429, None, 'retry'),
                 (507, None, 'blocked'), (400, None, 'reject'), (404, None, 'reject'), (409, 'needs_migration', 'blocked'), (409, 'invalid_log', 'blocked'),
                 (409, 'duplicate_id', 'reject'), (409, None, 'reject'),
                 (422, None, 'reject')]
        out = self.call([{'fn': 'classifyWriteResult', 'args': [s, c]} for s, c, _ in cases])
        for (status, code, expected), got in zip(cases, out):
            self.assertEqual(got, expected, 'status %r kod %r' % (status, code))
        for status, code in ((403, 'invalid_csrf'), (403, 'invalid_origin')):
            self.assertNotEqual(out[cases.index((status, code, 'csrf'))], 'reject')

    def test_control_classify_detects_csrf_as_reject(self):
        out = self.call([{'fn': 'classifyWriteResult', 'args': [403, 'invalid_csrf']}])
        assert_detects(self, lambda value: self.assertEqual(value, ['csrf']), out, ['reject'])


THEME_LOCK_HARNESS = r''''use strict';
// Ładuje silnik (require) na globalnej atrapie przeglądarki i zwraca stan <head> oraz color-scheme po applyTheme.
const fs = require('fs');
const [enginePath, casesPath] = process.argv.slice(2);
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
function makeNode(tag) {
  return {
    tagName: tag.toUpperCase(), attrs: {}, children: [], style: {},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; }, removeAttribute(k) { delete this.attrs[k]; },
    get firstChild() { return this.children[0] || null; },
    insertBefore(node, ref) { const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(node); else this.children.splice(i, 0, node); return node; },
    appendChild(node) { this.children.push(node); return node; },
    querySelector(sel) { const m = /^meta\[name="([^"]+)"\]$/.exec(sel); if (!m) return null; return this.children.find((c) => c.tagName === 'META' && c.attrs.name === m[1]) || null; },
    querySelectorAll() { return []; }, addEventListener() {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }
  };
}
const results = cases.map((testCase) => {
  const head = makeNode('head');
  (testCase.metas || []).forEach((meta) => { const node = makeNode('meta'); Object.assign(node.attrs, meta); head.children.push(node); });
  const html = makeNode('html');
  html.attrs['data-prototype-id'] = 'demo-a';
  const store = Object.assign({}, testCase.storage || {});
  const document = {
    readyState: 'loading', head, documentElement: html,
    createElement: (tag) => makeNode(tag), createElementNS: (ns, tag) => makeNode(tag), createTextNode: (text) => ({ text }),
    querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, addEventListener() {}, getElementsByTagName: (t) => (t === 'head' ? [head] : [])
  };
  const window = {
    document, location: { protocol: 'http:', hash: '' },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; }, key: () => null, length: 0 },
    matchMedia: () => ({ matches: !!testCase.systemDark, addEventListener() {} }),
    addEventListener() {}, setTimeout, clearTimeout
  };
  Object.assign(globalThis, { window, document });
  delete require.cache[require.resolve(enginePath)];
  require(enginePath);
  return {
    metas: head.children.filter((c) => c.tagName === 'META').map((c) => [c.attrs.name, c.attrs.content === undefined ? null : c.attrs.content]),
    theme: html.attrs['data-theme'] || null,
    colorScheme: html.style.colorScheme || null
  };
});
process.stdout.write(JSON.stringify(results));
'''


class EngineV2ThemeLockTests(unittest.TestCase):
    """F1-fix: blokada zewnętrznego dark mode (Dark Reader, schemat przeglądarki).

    Silnik ładowany na globalnej atrapie przeglądarki (`readyState: 'loading'`, więc start() nie rusza): sprawdza,
    że ensureThemeLockMetas dokłada brakujące meta do <head> bez dublowania istniejących, a applyTheme ustawia
    `color-scheme` na <html> według motywu prototypu (nie systemu)."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-lock-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.harness = Path(cls.tmp) / 'theme-lock.cjs'
        cls.harness.write_text(THEME_LOCK_HARNESS, encoding='utf-8')
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cases(self, cases):
        if not self.node:
            self.skipTest(self.reason)
        cases_path = Path(self.tmp) / 'cases.json'
        cases_path.write_text(json.dumps(cases), encoding='utf-8')
        result = subprocess.run([self.node, str(self.harness), str(self.engine_js), str(cases_path)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_missing_metas_are_added_at_head_start(self):
        out = self.run_cases([{'metas': [{'name': 'viewport', 'content': 'width=device-width'}], 'storage': {}}])[0]
        self.assertEqual(out['metas'], [['color-scheme', 'light dark'], ['darkreader-lock', None],
                                        ['viewport', 'width=device-width']])

    def test_existing_metas_are_not_duplicated(self):
        out = self.run_cases([
            {'metas': [{'name': 'color-scheme', 'content': 'light dark'}, {'name': 'darkreader-lock'}], 'storage': {}},
            {'metas': [{'name': 'darkreader-lock'}], 'storage': {}},
        ])
        self.assertEqual(out[0]['metas'], [['color-scheme', 'light dark'], ['darkreader-lock', None]])
        self.assertEqual(out[1]['metas'], [['color-scheme', 'light dark'], ['darkreader-lock', None]])
        for item in out:
            names = [name for name, _ in item['metas']]
            self.assertEqual(len(names), len(set(names)), item)

    def test_color_scheme_follows_engine_theme_not_system(self):
        out = self.run_cases([
            {'metas': [], 'storage': {'skillsync-prototype-theme': 'light'}, 'systemDark': True},
            {'metas': [], 'storage': {'skillsync-prototype-theme': 'dark'}, 'systemDark': False},
            {'metas': [], 'storage': {}, 'systemDark': True},
        ])
        self.assertEqual([(item['theme'], item['colorScheme']) for item in out],
                         [('light', 'light'), ('dark', 'dark'), ('dark', 'dark')])



PLAN_LINKS = [('#s%d' % number, '%d %s' % (number, name)) for number, name in enumerate([
    'Pusty', 'Tydzień grupy', 'Tydzień sali', 'Dzień', 'Konflikt sali', 'Konflikt prowadzącego', 'Zastępstwo',
    'Odwołanie', 'Przeniesienie', 'Szczegóły zajęć', 'Edycja zajęć', 'Nowe zajęcia', 'Lista sal', 'Sala',
    'Lista grup', 'Grupa', 'Prowadzący', 'Kalendarz', 'Święta', 'Dzień wolny', 'Eksport', 'Import', 'Uprawnienia',
    'Brak dostępu', 'Błąd zapisu', 'Łączenie grup', 'Podsumowanie', 'Ustawienia'], start=1)]


class EngineV2ScreenSwitcherTests(unittest.TestCase):
    """D23: przełącznik ekranów zamiast przewijanego paska (czyste funkcje silnika v2).

    Bez tych funkcji (przewijany pasek linków) wywołania padają."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-screens-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, calls):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 's-in.json', Path(self.tmp) / 's-out.json'
        input_path.write_text(json.dumps({'calls': calls}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path),
                                 str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def one(self, fn, *args):
        return self.call([{'fn': fn, 'args': list(args)}])[0]

    def plan_entries(self):
        return self.one('screenEntriesFromLinks', [{'href': href, 'text': text} for href, text in PLAN_LINKS])

    def test_entries_built_from_links(self):
        out = self.one('screenEntriesFromLinks', [
            {'href': '#s1', 'text': '1 Pusty'}, {'href': '#s2', 'text': '  2.  Tydzień\n grupy '},
            {'href': 'https://example.test/', 'text': 'obcy link'}, {'href': '#s9', 'text': 'Bez numeru'},
            {'href': '#', 'text': 'pusty hash'}])
        self.assertEqual([(e['id'], e['number'], e['name']) for e in out],
                         [('s1', 1, 'Pusty'), ('s2', 2, 'Tydzień grupy'), ('s9', 3, 'Bez numeru')])
        entries = self.plan_entries()
        self.assertEqual(len(entries), 28)
        self.assertEqual(entries[2], {'id': 's3', 'number': 3, 'name': 'Tydzień sali', 'label': '3 Tydzień sali'})

    def test_filter_ignores_case_and_polish_diacritics(self):
        entries = self.plan_entries()
        names = lambda query: [e['name'] for e in self.one('filterScreens', entries, query)]
        self.assertEqual(names('tydzien'), ['Tydzień grupy', 'Tydzień sali'])
        self.assertEqual(names('LACZENIE'), ['Łączenie grup'])
        self.assertEqual(names('swieta'), ['Święta'])
        self.assertEqual(names('3 tyd'), ['Tydzień sali'])
        self.assertEqual(names('nie ma takiego'), [])
        self.assertEqual(len(self.one('filterScreens', entries, '   ')), 28)

    def test_filter_number_is_prefix(self):
        entries = self.plan_entries()
        numbers = lambda query: [e['number'] for e in self.one('filterScreens', entries, query)]
        self.assertEqual(numbers('1'), [1] + list(range(10, 20)))
        self.assertEqual(numbers('2'), [2] + list(range(20, 29)))
        self.assertEqual(numbers('28'), [28])
        self.assertEqual(numbers('29'), [])

    def test_prev_next_at_edges(self):
        entries = self.plan_entries()
        out = self.call([{'fn': 'adjacentScreen', 'args': [entries, sid, delta]}
                         for sid, delta in (('s1', -1), ('s1', 1), ('s14', -1), ('s14', 1), ('s28', 1), ('s28', -1), ('s99', 1))])
        self.assertEqual([None if item is None else item['id'] for item in out],
                         [None, 's2', 's13', 's15', None, 's27', None])

    def test_shortcuts_do_not_fire_while_typing(self):
        base = {'typing': False, 'ctrlKey': False, 'metaKey': False, 'altKey': False, 'dialog': False, 'neutralFocus': True}
        def state(**changes):
            value = dict(base)
            value.update(changes)
            return value
        cases = [
            (state(key='g'), 'open'), (state(key='G'), 'open'),
            (state(key='ArrowLeft'), 'prev'), (state(key='ArrowRight'), 'next'),
            (state(key='g', typing=True), None), (state(key='ArrowLeft', typing=True), None),
            (state(key='ArrowRight', neutralFocus=False), None), (state(key='g', ctrlKey=True), None),
            (state(key='g', metaKey=True), None), (state(key='ArrowLeft', altKey=True), None),
            (state(key='g', dialog=True), None), (state(key='c'), None), (state(key=']'), None),
        ]
        out = self.call([{'fn': 'screenShortcut', 'args': [value]} for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_thread_counts_on_entries(self):
        entries = self.plan_entries()[:4]
        at = lambda minute: '2026-11-05T09:%02d:00.000Z' % minute
        ops = [
            {'id': 'op-1', 'type': 'create', 'threadId': 'th-1', 'at': at(1), 'by': 'a@example.com', 'payload': {'message': {'id': 'm-1', 'text': 'a'}, 'screen': 's2'}},
            {'id': 'op-2', 'type': 'create', 'threadId': 'th-2', 'at': at(2), 'by': 'a@example.com', 'payload': {'message': {'id': 'm-2', 'text': 'b'}, 'screen': 's2'}},
            {'id': 'op-3', 'type': 'create', 'threadId': 'th-3', 'at': at(3), 'by': 'a@example.com', 'payload': {'message': {'id': 'm-3', 'text': 'c'}, 'screen': 's4'}},
            {'id': 'op-4', 'type': 'resolve', 'threadId': 'th-3', 'at': at(4), 'by': 'a@example.com', 'payload': {'reason': 'author'}},
        ]
        threads = self.one('foldOperations', ops)
        counts = self.one('openCountByScreen', threads)
        out = self.one('screenEntriesWithCounts', entries, counts)
        self.assertEqual([(e['id'], e['count']) for e in out], [('s1', 0), ('s2', 2), ('s3', 0), ('s4', 0)])

    def test_list_keyboard_moves(self):
        cases = [((0, 'ArrowDown', 28, 4), 4), ((5, 'ArrowUp', 28, 4), 1), ((2, 'ArrowUp', 28, 4), 2),
                 ((26, 'ArrowDown', 28, 4), 26), ((3, 'ArrowRight', 28, 4), 4), ((0, 'ArrowLeft', 28, 4), 0),
                 ((27, 'ArrowRight', 28, 4), 27), ((9, 'Home', 28, 4), 0), ((9, 'End', 28, 4), 27),
                 ((-1, 'ArrowDown', 28, 4), 0), ((-1, 'ArrowUp', 28, 4), 27), ((0, 'ArrowDown', 0, 4), -1),
                 ((0, 'ArrowDown', 5, 1), 1)]
        out = self.call([{'fn': 'screenListMove', 'args': list(args)} for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_toolbar_one_row_only_when_it_fits(self):
        # Szerokości w px przy rem = 16: tytuł do min. 10rem (160), przełącznik 22rem (352), odstęp kolumn 16.
        def widths(bar, tools, title=220, one_row=False):
            return {'bar': bar, 'title': title, 'titleMin': 160, 'nav': 352, 'tools': tools, 'gap': 16,
                    'oneRow': one_row, 'hysteresis': 24}
        cases = [
            (widths(1232, 1000), False),   # 1280 px z serwerem i chipem użytkownika: stały próg 80rem dawał jeden wiersz i ucinał narzędzia
            (widths(1392, 1250), False),   # 1440 px z dłuższym chipem użytkownika
            (widths(1872, 1000), True),    # 1920 px: mieści się
            (widths(1872, 1400), False),   # 1920 px, bardzo długie narzędzia
            (widths(952, 300), True),      # 1000 px bez serwera (krótkie narzędzia): mieści się mimo szerokości < 80rem
            (widths(1000, 446, title=80), True),   # tytuł krótszy niż minimum liczy się w naturalnej szerokości
            (widths(1000, 470), False),    # 160 + 352 + 470 + 32 = 1014 > 1000
            (widths(1014, 470), False),    # dokładnie na granicy z dwóch wierszy: histereza 24 px nie przełącza
            (widths(1014, 470, one_row=True), True),   # w jednym wierszu zostaje (bez migotania)
            (widths(1037, 470), False), (widths(1038, 470), True),
            (widths(0, 10), False),
        ]
        out = self.call([{'fn': 'toolbarFitsOneRow', 'args': [value]} for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_visible_screen_follows_scroll(self):
        rects = [{'id': 's1', 'bottom': 40}, {'id': 's2', 'bottom': 300}, {'id': 's3', 'bottom': 900}]
        self.assertEqual(self.one('visibleScreenId', rects, 100), 's2')
        self.assertEqual(self.one('visibleScreenId', rects, 10), 's1')
        self.assertEqual(self.one('visibleScreenId', [{'id': 's1', 'bottom': -5}, {'id': 's2', 'bottom': 20}], 100), 's2')
        self.assertIsNone(self.one('visibleScreenId', [], 100))



DR_LEFTOVER_HARNESS = r''''use strict';
// Silnik (require) na globalnej atrapie przeglądarki: pozostałości Dark Readera przy starcie i dodane później (MutationObserver).
const enginePath = process.argv[2];
function el(tag, attrs) {
  return {
    nodeType: 1, tagName: tag.toUpperCase(), attrs: Object.assign({}, attrs || {}), childNodes: [], parentNode: null, style: {},
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; }, removeAttribute(k) { delete this.attrs[k]; },
    get children() { return this.childNodes.filter((n) => n.nodeType === 1); },
    get firstChild() { return this.childNodes[0] || null; },
    insertBefore(n, ref) { const i = ref ? this.childNodes.indexOf(ref) : -1; n.parentNode = this; if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n); return n; },
    appendChild(n) { n.parentNode = this; this.childNodes.push(n); return n; },
    removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) this.childNodes.splice(i, 1); n.parentNode = null; return n; },
    querySelector(sel) { const m = /^meta\[name="([^"]+)"\]$/.exec(sel); if (!m) return null; return this.childNodes.find((c) => c.tagName === 'META' && c.attrs.name === m[1]) || null; },
    querySelectorAll() { return []; }, addEventListener() {}, classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }
  };
}
const observers = [];
class FakeMutationObserver {
  constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
  observe(target, options) { this.targets.push({ target, options }); }
  disconnect() { this.targets = []; }
}
const html = el('html', { 'data-prototype-id': 'demo-a' });
const head = el('head');
html.appendChild(head);
head.appendChild(el('meta', { name: 'darkreader-lock' }));
head.appendChild(el('style', { class: 'darkreader darkreader--fallback', media: 'screen' }));
head.appendChild(el('style', {}));
head.appendChild(el('style', { class: 'proto-local other' }));
head.appendChild(el('style', { class: 'dark-theme my-darkreader' }));
head.appendChild(el('link', { class: 'darkreader darkreader--user-agent', rel: 'stylesheet' }));
head.appendChild(el('link', { rel: 'stylesheet', href: 'prototype.css' }));
head.appendChild({ nodeType: 3, nodeValue: 'tekst', parentNode: head });
html.appendChild(el('style', { class: 'darkreader darkreader--sync' }));
html.attrs['data-darkreader-mode'] = 'dynamic';
const document = {
  readyState: 'loading', head, documentElement: html,
  createElement: (tag) => el(tag), createElementNS: (ns, tag) => el(tag), createTextNode: (text) => ({ nodeType: 3, text }),
  querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, addEventListener() {}, getElementsByTagName: (t) => (t === 'head' ? [head] : [])
};
const store = {};
const listeners = [];
const window = {
  document, location: { protocol: 'http:', hash: '' }, MutationObserver: FakeMutationObserver,
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; }, key: () => null, length: 0 },
  matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener(type, fn) { listeners.push([type, fn]); }, setTimeout, clearTimeout
};
Object.assign(globalThis, { window, document });
const api = require(enginePath);
const describe = (node) => (node.nodeType === 1 ? node.tagName.toLowerCase() + (node.attrs.class ? '.' + node.attrs.class.split(/\s+/).join('.') : '') + (node.attrs.name ? '[' + node.attrs.name + ']' : '') : '#text');
const atStart = { head: head.childNodes.map(describe), html: html.childNodes.map(describe) };
// Później: rozszerzenie dopisuje fallback do <head> i do <html>, prototyp dopisuje własny styl; obserwatory dostają rekordy.
const late = [el('style', { class: 'darkreader darkreader--fallback' }), el('style', { class: 'app' })];
late.forEach((node) => head.appendChild(node));
const lateHtml = el('style', { class: 'darkreader' });
html.appendChild(lateHtml);
observers.forEach((observer) => observer.targets.forEach(({ target }) => {
  const added = target === head ? late : target === html ? [lateHtml] : [];
  if (added.length) observer.callback([{ type: 'childList', target, addedNodes: added }], observer);
}));
const after = { head: head.childNodes.map(describe), html: html.childNodes.map(describe) };
const observed = observers.flatMap((o) => o.targets.map(({ target, options }) => [target === html ? 'html' : target === head ? 'head' : '?', !!options.childList, !!options.subtree]));
// pagehide odpina obserwator: kolejny fallback już nie znika.
listeners.filter(([type]) => type === 'pagehide').forEach(([, fn]) => fn());
const afterHide = el('style', { class: 'darkreader' });
head.appendChild(afterHide);
observers.forEach((observer) => observer.targets.forEach(({ target }) => {
  if (target === head) observer.callback([{ type: 'childList', target, addedNodes: [afterHide] }], observer);
}));
const headAfterHide = head.childNodes.map(describe);
// Czyste funkcje (Node, bez DOM): dokument bez darkreader-lock niczego nie traci.
const bareHead = el('head');
bareHead.appendChild(el('style', { class: 'darkreader darkreader--fallback' }));
const noLockRemoved = api.removeDarkReaderLeftovers({ head: bareHead }, [bareHead]);
const leftover = (tag, cls) => api.isDarkReaderLeftover(el(tag, cls === null ? {} : { class: cls }));
const pure = [leftover('style', 'darkreader darkreader--fallback'), leftover('style', 'darkreader'), leftover('style', 'darkreader--sync'),
  leftover('link', 'darkreader'), leftover('style', 'dark-theme'), leftover('style', 'my-darkreader'), leftover('style', null),
  api.isDarkReaderLeftover({ nodeType: 3 }), api.isDarkReaderLeftover(null)];
process.stdout.write(JSON.stringify({ atStart, after,
  observed, htmlDataAttr: html.attrs['data-darkreader-mode'] || null, headAfterHide, noLockRemoved,
  noLockHead: bareHead.childNodes.map(describe), pure }));
'''


class EngineV2DarkReaderLeftoverTests(unittest.TestCase):
    """Krok C (F1-fix, część 2): silnik zdejmuje pozostawiony fallback Dark Readera.

    Atrapa DOM (globalna, start() wstrzymany): przy `darkreader-lock` znikają wyłącznie elementy `style` z klasą `darkreader…`
    w <head> i <html>, przy starcie i dodane później (MutationObserver na dzieciach <html> i <head>, odpinany na pagehide);
    inne style (także z „dark” w klasie), `link` i atrybuty `data-darkreader-*` zostają; bez lock nic nie jest usuwane."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-dr-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.harness = Path(cls.tmp) / 'dr-leftover.cjs'
        cls.harness.write_text(DR_LEFTOVER_HARNESS, encoding='utf-8')
        cls.engine_js = install_engine_v2(cls.tmp)
        cls.out = None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def result(self):
        if not self.node:
            self.skipTest(self.reason)
        if self.out is None:
            run = subprocess.run([self.node, str(self.harness), str(self.engine_js)], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            type(self).out = json.loads(run.stdout)
        return self.out

    KEPT = ['meta[color-scheme]', 'meta[darkreader-lock]', 'style', 'style.proto-local.other', 'style.dark-theme.my-darkreader',
            'link.darkreader.darkreader--user-agent', 'link', '#text']

    def test_leftover_removed_at_start(self):
        out = self.result()
        self.assertEqual(out['atStart']['head'], self.KEPT)
        self.assertEqual(out['atStart']['html'], ['head'])

    def test_leftover_added_later_is_removed_other_styles_stay(self):
        out = self.result()
        self.assertEqual(out['after']['head'], self.KEPT + ['style.app'])
        self.assertEqual(out['after']['html'], ['head'])
        self.assertEqual(out['observed'], [['html', True, False], ['head', True, False]])
        self.assertEqual(out['htmlDataAttr'], 'dynamic')

    def test_observer_released_on_pagehide(self):
        self.assertEqual(self.result()['headAfterHide'], self.KEPT + ['style.app', 'style.darkreader'])

    def test_without_lock_nothing_is_removed(self):
        out = self.result()
        self.assertEqual(out['noLockRemoved'], 0)
        self.assertEqual(out['noLockHead'], ['style.darkreader.darkreader--fallback'])

    def test_only_style_with_darkreader_class_matches(self):
        # style.darkreader.darkreader--fallback, style.darkreader, style.darkreader--sync → tak;
        # link.darkreader, style.dark-theme, style.my-darkreader, style bez klasy, tekst, null → nie.
        self.assertEqual(self.result()['pure'], [True, True, True, False, False, False, False, False, False])



class EngineV2RestoreUnchangedTests(unittest.TestCase):
    """D27: przywrócenie ekranu identycznego z bieżącym jest zablokowane w oknie „Przywróć…”.

    Czyste funkcje silnika na atrapie odpowiedzi serwera (pole `unchanged` w restore-preview, 200 `{unchanged: true}` na POST).
    Na silniku sprzed D27 (523389d) każdy test pada: brak `restoreSubmitState`, `restoreAvailability` ignoruje `unchanged`,
    `classifyRestoreResult` traktuje 200 `{unchanged: true}` jak udany zapis."""

    BASE = {'mode': 'api', 'frozen': False, 'embed': False, 'user': {'role': 'developer', 'mustChange': False},
            'sourceWrites': True, 'iterations': 2, 'stale': False, 'version': 'v-1', 'offline': False, 'expired': False}

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-d27-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, calls):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 'd27-in.json', Path(self.tmp) / 'd27-out.json'
        input_path.write_text(json.dumps({'calls': calls}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path),
                                 str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def availability(self, **changes):
        context = dict(self.BASE)
        context.update(changes)
        return {'fn': 'restoreAvailability', 'args': [context]}

    def test_availability_blocks_unchanged_with_message(self):
        out = self.call([self.availability(), self.availability(unchanged=True, **{'from': 3}),
                         self.availability(unchanged=False, **{'from': 3})])
        self.assertEqual(out[0], {'visible': True, 'enabled': True, 'reason': ''})
        self.assertEqual(out[1], {'visible': True, 'enabled': False, 'reason': 'Ekran w iteracji 3 jest taki sam jak bieżący.'})
        self.assertEqual(out[2], {'visible': True, 'enabled': True, 'reason': ''})

    def test_submit_state_disabled_when_unchanged_reason_irrelevant(self):
        ready = {'busy': False, 'previewOk': True, 'stale': False, 'reason': 'Powód', 'from': 2}
        cases = [
            (dict(ready), True, ''),
            (dict(ready, unchanged=True), False, 'Ekran w iteracji 2 jest taki sam jak bieżący.'),
            (dict(ready, unchanged=True, reason=''), False, 'Ekran w iteracji 2 jest taki sam jak bieżący.'),
            (dict(ready, unchanged=False), True, ''),          # zmiana iteracji na różną odblokowuje
            (dict(ready, reason='   '), False, 'Podaj powód przywrócenia.'),
            (dict(ready, previewOk=False), False, 'Poczekaj na podgląd skutków przywrócenia.'),   # E1-4: Ctrl+Enter bez podglądu
            (dict(ready, busy=True), False, ''),
            (dict(ready, stale=True), False, 'Prototyp zmieniono od wczytania strony: przeładuj stronę i spróbuj ponownie.'),
        ]
        out = self.call([{'fn': 'restoreSubmitState', 'args': [state]} for state, _, _ in cases])
        self.assertEqual([(item['enabled'], item['reason']) for item in out], [(enabled, reason) for _, enabled, reason in cases])

    def test_post_200_unchanged_is_not_success(self):
        cases = [((200, '', {'unchanged': True, 'version': 'v-1'}), 'unchanged'),
                 ((200, '', {'unchanged': False}), 'done'),
                 ((201, '', {'operations': []}), 'done'),
                 ((200, '', None), 'done'),
                 ((409, 'stale_view', None), 'stale'),
                 ((0, '', None), 'offline')]
        out = self.call([{'fn': 'classifyRestoreResult', 'args': list(args)} for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])



class EngineV2ReviewR1Tests(unittest.TestCase):
    """Poprawki po code-review-2.6-eng-1 (E1-3, E1-4, E1-7): czyste funkcje iteracji, porównania i przywracania oraz klawisze
    pola wyszukiwania listy ekranów. Dowody padania (mutacje poza repo) w raporcie `code-review-2.6-eng-1-poprawki.md`."""

    AVAILABLE = {'mode': 'api', 'frozen': False, 'embed': False, 'user': {'role': 'developer', 'mustChange': False},
                 'sourceWrites': True, 'iterations': 2, 'stale': False, 'version': 'v-1', 'offline': False, 'expired': False}

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-r1-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, calls):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 'r1-in.json', Path(self.tmp) / 'r1-out.json'
        input_path.write_text(json.dumps({'calls': calls}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path),
                                 str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def many(self, fn, argument_lists):
        return self.call([{'fn': fn, 'args': list(args)} for args in argument_lists])

    @staticmethod
    def iteration_op(op_id, number, minute):
        return {'id': op_id, 'type': 'iteration', 'at': '2026-11-05T10:%02d:00.000Z' % minute, 'by': 'agent', 'payload': {'number': number}}

    def test_iteration_list_from_log_and_api(self):
        ops = [self.iteration_op('op-i2', 2, 2), self.iteration_op('op-i1', 1, 1), self.iteration_op('op-i2b', 2, 30),
               self.iteration_op('op-i0', 0, 0)]
        from_log, from_api = self.many('iterationList', [
            [None, ops],
            [[{'number': 1, 'exists': True}, {'number': 3, 'exists': False}, {'number': 1, 'exists': False}, {'number': 'x'}], ops]])
        # Z dziennika: malejąco, bez duplikatu i bez numeru < 1, `at` z pierwszej operacji numeru, exists nieznane.
        self.assertEqual(from_log, [
            {'number': 2, 'path': 'iterations/2/', 'exists': None, 'at': '2026-11-05T10:02:00.000Z'},
            {'number': 1, 'path': 'iterations/1/', 'exists': None, 'at': '2026-11-05T10:01:00.000Z'}])
        # Z API: tylko exists === true to katalog; pierwsza pozycja numeru wygrywa; numery spoza API nie wchodzą.
        self.assertEqual(from_api, [
            {'number': 3, 'path': 'iterations/3/', 'exists': False, 'at': None},
            {'number': 1, 'path': 'iterations/1/', 'exists': True, 'at': '2026-11-05T10:01:00.000Z'}])

    def test_iteration_for_thread(self):
        thread = {'screen': 's2', 'anchor': {'kind': 'element', 'protoId': 's2-typ'}}
        iterations = [{'number': 4, 'exists': True}, {'number': 3, 'exists': False}, {'number': 2, 'exists': True}, {'number': 1, 'exists': True}]
        out = self.many('iterationForThread', [
            [iterations, {'4': {'s2': {}}, '2': {'s2': {'s2-typ': True}}}, thread],      # najnowsza z uchwytem; 3 bez katalogu pominięta
            [iterations, {'4': {'s2': {}}}, thread],                                    # 2 jeszcze nie wczytana → need
            [iterations, {'2': {'s2': {'s2-typ': True}}}, thread, 4],                   # skip własnej strony zamrożonej
            [iterations, {}, {'screen': 's2', 'anchor': {'kind': 'lost'}}],             # lost nie ma iteracji
            [iterations, {'4': {'s9': {}}}, {'screen': 's9', 'anchor': {'kind': 'screen'}}]])
        self.assertEqual(out, [{'number': 2, 'need': None}, {'number': None, 'need': 2}, {'number': 2, 'need': None},
                               {'number': None, 'need': None}, {'number': 4, 'need': None}])

    def test_embed_mode_and_thread_hash(self):
        embed = self.many('embedModeFrom', [['?embed=compare'], ['?a=1&embed=compare#x'], ['?embed=other'], ['?xembed=compare'], [''], [None]])
        self.assertEqual(embed, ['compare', 'compare', '', '', '', ''])
        ids = self.many('threadIdFromHash', [['#thread=th-1'], ['#thread=th-1&x'], ['#thread='], ['thread=th-1'],
                                             ['#thread=' + 'a' * 81], ['#thread=a-b_C9'], ['#thread=<b>']])
        self.assertEqual(ids, ['th-1', None, None, None, None, 'a-b_C9', None])

    def test_restore_effects(self):
        out = self.many('restoreEffects', [
            [{'threadsBack': 2, 'threadsLost': 1, 'acceptanceLost': True}, 5],
            [{'threadsBack': -1, 'threadsLost': 'x', 'acceptanceLost': 0}, 0],
            [None, 3],
            [{'acceptanceLost': 3}, 1]])
        self.assertEqual(out, [
            {'frozen': 5, 'created': 6, 'back': 2, 'lost': 1, 'acceptanceLost': True},
            {'frozen': None, 'created': None, 'back': None, 'lost': None, 'acceptanceLost': False},
            {'frozen': 3, 'created': 4, 'back': None, 'lost': None, 'acceptanceLost': None},
            {'frozen': 1, 'created': 2, 'back': None, 'lost': None, 'acceptanceLost': True}])

    def test_restore_availability_matrix(self):
        def ctx(**changes):
            value = dict(self.AVAILABLE)
            value.update(changes)
            return [value]
        hidden = {'visible': False, 'enabled': False, 'reason': ''}
        cases = [
            (ctx(), {'visible': True, 'enabled': True, 'reason': ''}),
            (ctx(mode='static'), hidden), (ctx(frozen=True), hidden), (ctx(embed=True), hidden),
            (ctx(user=None), hidden), (ctx(user={'role': 'developer', 'mustChange': True}), hidden),
            (ctx(user={'role': 'commenter', 'mustChange': False}), hidden),
            (ctx(user={'role': 'acceptor', 'mustChange': False}), {'visible': True, 'enabled': True, 'reason': ''}),
            (ctx(sourceWrites=False), hidden), (ctx(sourceWrites=None), hidden), (ctx(iterations=0), hidden),
            (ctx(stale=True), {'visible': True, 'enabled': False, 'reason': 'Prototyp zmieniono od wczytania strony: przeładuj.'}),
            (ctx(version=None), {'visible': True, 'enabled': False, 'reason': 'Serwer nie podał wersji prototypu: przeładuj stronę.'}),
            (ctx(offline=True), {'visible': True, 'enabled': False, 'reason': 'Przywrócenie wymaga połączenia z serwerem i nie trafia do kolejki.'}),
            (ctx(expired=True), {'visible': True, 'enabled': False, 'reason': 'Przywrócenie wymaga połączenia z serwerem i nie trafia do kolejki.'}),
            (ctx(stale=True, offline=True), {'visible': True, 'enabled': False, 'reason': 'Prototyp zmieniono od wczytania strony: przeładuj.'}),
        ]
        out = self.many('restoreAvailability', [args for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_classify_restore_result_cases(self):
        cases = [((201, ''), 'done'), ((200, ''), 'done'), ((409, 'stale_view'), 'stale'), ((503, ''), 'busy'), ((429, ''), 'busy'),
                 ((401, ''), 'auth'), ((403, 'password_change_required'), 'password'), ((403, 'invalid_csrf'), 'csrf'),
                 ((403, 'invalid_origin'), 'csrf'), ((403, 'forbidden'), 'error'), ((403, 'source_writes_disabled'), 'error'),
                 ((404, 'iteration_not_found'), 'error'), ((409, 'ambiguous_section'), 'error'), ((500, ''), 'error'),
                 ((507, ''), 'error'), ((0, ''), 'offline')]
        out = self.many('classifyRestoreResult', [args for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_search_field_keys(self):
        cases = [({'key': 'Escape', 'value': 'x'}, 'close'), ({'key': 'Enter', 'value': ''}, 'choose'),
                 ({'key': 'Tab', 'value': 'x'}, 'tab-forward'), ({'key': 'Tab', 'shiftKey': True, 'value': ''}, 'tab-back'),
                 ({'key': 'ArrowDown', 'value': 'x'}, 'move'), ({'key': 'ArrowUp', 'value': ''}, 'move'),
                 ({'key': 'Home', 'value': ''}, 'move'), ({'key': 'End', 'value': ''}, 'move'),
                 ({'key': 'ArrowLeft', 'value': ''}, 'move'), ({'key': 'ArrowRight', 'value': ''}, 'move'),
                 ({'key': 'Home', 'value': 'tyd'}, None), ({'key': 'End', 'value': 'tyd'}, None),       # E1-7: kursor w polu
                 ({'key': 'ArrowLeft', 'value': 'tyd'}, None), ({'key': 'ArrowRight', 'value': 'tyd'}, None),
                 ({'key': 'a', 'value': ''}, None), ({'key': 'g', 'value': ''}, None)]
        out = self.many('screenSearchKeyAction', [[state] for state, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # Home/End przy pustym polu przesuwają zaznaczenie listy (screenListMove).
        moves = self.many('screenListMove', [[9, 'Home', 28, 3], [9, 'End', 28, 3], [-1, 'End', 28, 3]])
        self.assertEqual(moves, [0, 27, 27])



FOCUS_HARNESS = r"""'use strict';
// Atrapa DOM dla focusableAfter / screenSearchTabTarget (E1-2, E2-2): elementy w kolejności dokumentu.
const fs = require('fs');
const [enginePath, casesPath] = process.argv.slice(2);
const api = require(enginePath);
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const results = cases.map((testCase) => {
  const nodes = testCase.items.map((spec) => ({
    name: spec.name, disabled: !!spec.disabled, tabIndex: spec.tabIndex === undefined ? 0 : spec.tabIndex,
    inPop: !!spec.inPop, hidden: !!spec.hidden,
    attrs: spec.ariaDisabled ? { 'aria-disabled': 'true' } : {},
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    getClientRects() { return this.hidden ? [] : [{ width: 10, height: 10 }]; }
  }));
  const doc = { querySelectorAll: () => nodes };
  const pop = { contains: (item) => item.inPop };
  const main = nodes.find((node) => node.name === 'main');
  const target = api.screenSearchTabTarget(doc, main, pop, testCase.action);
  return target ? target.name : null;
});
process.stdout.write(JSON.stringify(results));
"""


class EngineV2ReviewR2Tests(unittest.TestCase):
    """Poprawki po code-review-2.6-eng-2: E2-2 (Tab/Shift+Tab z listy ekranów na atrapie DOM), E2-3 (ponowienie sprawdzenia
    iteracji), E2-1 (podgląd skutków nieudany). Dowody padania (mutacje poza repo) w raporcie `code-review-2.6-eng-2-poprawki.md`."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-r2-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)
        (Path(cls.tmp) / 'focus.cjs').write_text(FOCUS_HARNESS, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need_node(self):
        if not self.node:
            self.skipTest(self.reason)

    def call(self, calls):
        self.need_node()
        input_path, output_path = Path(self.tmp) / 'r2-in.json', Path(self.tmp) / 'r2-out.json'
        input_path.write_text(json.dumps({'calls': calls}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path),
                                 str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def focus(self, cases):
        self.need_node()
        cases_path = Path(self.tmp) / 'focus-cases.json'
        cases_path.write_text(json.dumps(cases), encoding='utf-8')
        result = subprocess.run([self.node, str(Path(self.tmp) / 'focus.cjs'), str(self.engine_js), str(cases_path)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout)

    BAR = [{'name': 'title-link'}, {'name': 'prev'}, {'name': 'main'}, {'name': 'next'}, {'name': 'toolbar-first'}, {'name': 'toolbar-second'}]

    def bar(self, **changes):
        items = [dict(item) for item in self.BAR]
        for item in items:
            item.update(changes.get(item['name'], {}))
        return items

    def test_tab_from_search_stays_in_switcher(self):
        out = self.focus([
            {'action': 'tab-forward', 'items': self.bar()},                                            # › aktywny
            {'action': 'tab-forward', 'items': self.bar(next={'ariaDisabled': True})},                 # › na krańcu: aria-disabled zostaje w kolejności
            {'action': 'tab-forward', 'items': self.bar(next={'disabled': True})},                     # wyłączony natywnie → pierwszy przycisk paska
            {'action': 'tab-back', 'items': self.bar()},                                               # Shift+Tab → przycisk główny
            {'action': 'tab-forward', 'items': self.BAR[:3]},                                          # brak następnego → zostaje na głównym
            {'action': 'tab-forward', 'items': [{'name': 'main'}, {'name': 'opt', 'inPop': True}, {'name': 'hidden', 'hidden': True},
                                                {'name': 'minus', 'tabIndex': -1}, {'name': 'toolbar-first'}]},  # lista, ukryte, tabIndex −1 pominięte
        ])
        self.assertEqual(out, ['next', 'next', 'toolbar-first', 'main', 'main', 'toolbar-first'])

    def test_probe_retried_after_network_error(self):
        # (item, wynik sprawdzenia, trwa) → sprawdzić? Wynik null/brak = nieznany (np. po błędzie sieci: ponowienie).
        item = {'number': 2, 'exists': None}
        cases = [([item, None, False], True), ([item], True), ([item, True, False], False), ([item, False, False], False),
                 ([item, None, True], False), ([{'number': 2, 'exists': True}, None, False], False),
                 ([{'number': 2, 'exists': False}, None, False], False), ([None, None, False], False)]
        out = self.call([{'fn': 'shouldProbeIteration', 'args': args} for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_preview_failed_has_own_reason(self):
        ready = {'busy': False, 'previewOk': False, 'stale': False, 'reason': 'Powód', 'from': 2}
        out = self.call([{'fn': 'restoreSubmitState', 'args': [dict(ready, previewFailed=True)]},
                         {'fn': 'restoreSubmitState', 'args': [dict(ready, previewFailed=False)]},
                         {'fn': 'restoreSubmitState', 'args': [dict(ready, previewOk=True, previewFailed=False)]}])
        self.assertEqual(out[0], {'enabled': False, 'reason': 'Nie udało się pobrać podglądu skutków: użyj „Ponów podgląd” albo wybierz inną iterację.'})
        self.assertEqual(out[1], {'enabled': False, 'reason': 'Poczekaj na podgląd skutków przywrócenia.'})
        self.assertEqual(out[2], {'enabled': True, 'reason': ''})


# ── F3: akceptacja ekranów (K5 część F3, kontrakt A/A1, K24/K25 po stronie silnika) ─────────────────────────────

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def acc_op(number, kind, payload, by='anna@example.com', at=None):
    return {'id': 'op-%02d' % number, 'type': kind, 'at': at or '2026-11-04T10:00:%02d.000Z' % number, 'by': by,
            'payload': payload}


class EngineV2AcceptanceDifferentialTests(unittest.TestCase):
    """Fold `acceptance` w JS i w Pythonie na tych samych dziennikach (K5 część F3), włącznie z A1 (zniekształcone)."""

    corpora = None

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-accept-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)
        cls.corpora = dict((seed, generate_corpus(seed, DIFF_PER_SEED)) for seed in DIFF_SEEDS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need_node(self):
        if not self.node:
            self.skipTest(self.reason)

    def js_folds(self, cases):
        return run_node_v2(self.node, self.engine_js, {'folds': cases, 'labels': []}, self.tmp)['folds']

    def test_corpus_exercises_acceptance_rules(self):
        """Bez tego zgodność mogłaby dotyczyć samych pustych `acceptance` (zbiór ≥ 3000 dzienników)."""
        total = sum(len(cases) for cases in self.corpora.values())
        self.assertGreaterEqual(total, 3000)
        seen = {'non_empty': 0, 'cli_who': 0, 'ui_who_is_by': 0, 'iteration_null': 0, 'iteration_int': 0, 'scope_null': 0,
                'unaccept_removed': 0, 'multi_screen': 0, 'proto_key': 0, 'malformed_accept': 0, 'malformed_unaccept': 0,
                'bad_hash_skipped': 0}
        for cases in self.corpora.values():
            for ops in cases:
                accepted = python_view(ops)['fold']['acceptance']
                seen['non_empty'] += bool(accepted)
                seen['multi_screen'] += len(accepted) >= 2
                seen['proto_key'] += '__proto__' in accepted
                for entry in accepted.values():
                    seen['cli_who'] += entry['via'] == 'cli' and entry['who'] != entry['by']
                    seen['ui_who_is_by'] += entry['via'] == 'ui' and entry['who'] == entry['by']
                    seen['iteration_null'] += entry['iteration'] is None
                    seen['iteration_int'] += isinstance(entry['iteration'], int)
                    seen['scope_null'] += entry['scope'] is None
                objects = [op for op in ops if is_plain_object(op)]
                for op in objects:
                    payload = op.get('payload') if is_plain_object(op.get('payload')) else {}
                    if op.get('type') == 'accept' and not is_plain_object(payload.get('screens')):
                        seen['malformed_accept'] += 1
                    if op.get('type') == 'unaccept' and not isinstance(payload.get('screens'), list):
                        seen['malformed_unaccept'] += 1
                    if op.get('type') == 'accept' and is_plain_object(payload.get('screens')) and any(
                            not isinstance(hash_value, str) or not hash_value for hash_value in payload['screens'].values()):
                        seen['bad_hash_skipped'] += 1
                # `unaccept` faktycznie usuwa wpis: ten sam dziennik bez `unaccept` ma więcej ekranów.
                without = [op for op in objects if op.get('type') != 'unaccept']
                if without != objects and len(python_view(without)['fold']['acceptance']) > len(accepted):
                    seen['unaccept_removed'] += 1
        for key, value in seen.items():
            self.assertGreater(value, 30, '%s zbyt rzadkie w zbiorze: %s' % (key, seen))

    def test_acceptance_matches_node_on_canonical_serialization(self):
        self.need_node()
        compared = 0
        differences = []
        for seed in DIFF_SEEDS:
            cases = self.corpora[seed]
            js = self.js_folds(cases)
            self.assertEqual(len(js), len(cases))
            for index, (ops, js_view) in enumerate(zip(cases, js)):
                py_acceptance = python_view(ops)['fold']['acceptance']
                js_acceptance = js_view['fold']['acceptance']
                compared += 1
                # Kolejność kluczy (posortowane sN) i kanoniczna serializacja każdego wpisu: ints bez 2.0, bez -0.
                same = (list(py_acceptance) == list(js_acceptance) and
                        canonical(py_acceptance) == canonical(js_acceptance))
                if not same:
                    differences.append((seed, index, canonical(py_acceptance)[:300], canonical(js_acceptance)[:300]))
        self.assertGreaterEqual(compared, 3000)
        self.assertEqual(differences, [], 'rozjazd `acceptance` Node<->Python: %s' % (differences[:1],))

    def test_a1_normalisation_table_matches_node_and_contract(self):
        """Zniekształcone wg A1: oba foldy dają dokładnie ten sam, oczekiwany wynik (przypadek kontrolny: poprawny zapis)."""
        self.need_node()
        good = {'scope': 'screen', 'screens': {'s1': 'h1'}, 'iteration': 2, 'via': 'ui'}
        cases = [
            # (nazwa, payload, oczekiwany wpis s1 albo None)
            ('poprawny', good, {'hash': 'h1', 'by': 'anna@example.com', 'who': 'anna@example.com', 'at': '2026-11-04T10:00:01.000Z',
                                'iteration': 2, 'via': 'ui', 'scope': 'screen', 'opId': 'op-01'}),
            ('screens lista', dict(good, screens=['s1']), None),
            ('screens null', dict(good, screens=None), None),
            ('screens napis', dict(good, screens='s1'), None),
            ('pusty hash', dict(good, screens={'s1': ''}), None),
            ('hash nie napis', dict(good, screens={'s1': 5}), None),
            ('iteration bool', dict(good, iteration=True), {'iteration': None}),
            ('iteration float niecałkowity', dict(good, iteration=2.5), {'iteration': None}),
            ('iteration float całkowity', dict(good, iteration=2.0), {'iteration': 2}),
            ('iteration napis', dict(good, iteration='2'), {'iteration': None}),
            ('via liczba', dict(good, via=5), {'via': None, 'who': 'anna@example.com'}),
            ('scope liczba', dict(good, scope=5), {'scope': None}),
            ('cli z who', dict(good, via='cli', who='jan@example.com'), {'who': 'jan@example.com', 'by': 'anna@example.com', 'via': 'cli'}),
            ('cli z pustym who', dict(good, via='cli', who=''), {'who': 'anna@example.com'}),
            ('cli z who liczbą', dict(good, via='cli', who=5), {'who': 'anna@example.com'}),
            ('ui ignoruje who', dict(good, via='ui', who='jan@example.com'), {'who': 'anna@example.com'}),
        ]
        ops_list = [[acc_op(1, 'accept', payload)] for _, payload, _ in cases]
        js = self.js_folds(ops_list)
        for (name, _, expected), ops, js_view in zip(cases, ops_list, js):
            with self.subTest(name):
                py = python_view(ops)['fold']['acceptance']
                self.assertEqual(canonical(py), canonical(js_view['fold']['acceptance']))
                if expected is None:
                    self.assertEqual(py, {})
                else:
                    self.assertIn('s1', py)
                    for key, value in expected.items():
                        self.assertEqual(py['s1'][key], value, key)
                    self.assertIsNot(py['s1']['iteration'], True)

    def test_last_operation_per_screen_wins(self):
        self.need_node()
        p = lambda **extra: dict({'scope': 'screen', 'iteration': 1, 'via': 'ui'}, **extra)
        ops = [
            acc_op(1, 'accept', p(screens={'s1': 'a', 's2': 'b'})),
            acc_op(2, 'unaccept', {'screens': ['s1']}),
            acc_op(3, 'accept', p(screens={'s3': 'c'}), by='jan@example.com'),
            acc_op(4, 'accept', p(screens={'s2': 'b2'}, iteration=2), by='jan@example.com'),
            acc_op(5, 'unaccept', {'screens': ['s3', 'nieistnieje', '', 5]}),
            acc_op(6, 'accept', p(screens={'s1': 'a3'}, via='cli', who='celina@example.com'), by='agent'),
        ]
        # Operacje tablicowo w odwrotnej kolejności: kolejność (at, id), nie położenie w tablicy.
        for variant in (ops, list(reversed(ops))):
            py = python_view(variant)['fold']['acceptance']
            js = self.js_folds([variant])[0]['fold']['acceptance']
            self.assertEqual(canonical(py), canonical(js))
            self.assertEqual(list(py), ['s1', 's2'])
            self.assertEqual((py['s1']['hash'], py['s1']['by'], py['s1']['who'], py['s1']['via']), ('a3', 'agent', 'celina@example.com', 'cli'))
            self.assertEqual((py['s2']['hash'], py['s2']['by'], py['s2']['iteration'], py['s2']['opId']), ('b2', 'jan@example.com', 2, 'op-04'))
            self.assertNotIn('s3', py)
        # Kontrola: bez `unaccept` s3 zostaje, a s1 ma starszy hash.
        kept = python_view([op for op in ops if op['type'] != 'unaccept'])['fold']['acceptance']
        self.assertEqual(list(kept), ['s1', 's2', 's3'])

    def test_faulty_results_are_detected_by_the_differential(self):
        """Dowód, że porównanie `acceptance` potrafi paść: wynik JS z trzema typowymi błędami (atrapy po stronie testu) daje rozjazd."""
        self.need_node()

        def op_payloads(ops):
            return dict((op['id'], op['payload']) for op in ops
                        if is_plain_object(op) and isinstance(op.get('id'), str) and is_plain_object(op.get('payload')))

        def unaccept_removes_nothing(ops, accepted):
            return python_view([op for op in ops if not (is_plain_object(op) and op.get('type') == 'unaccept')])['fold']['acceptance']

        def who_always_from_payload(ops, accepted):
            payloads = op_payloads(ops)
            return dict((screen, dict(entry, who=payloads[entry['opId']]['who'])
                         if isinstance(payloads.get(entry['opId'], {}).get('who'), str) else entry)
                        for screen, entry in accepted.items())

        def bool_as_iteration(ops, accepted):
            payloads = op_payloads(ops)
            return dict((screen, dict(entry, iteration=1) if isinstance(payloads.get(entry['opId'], {}).get('iteration'), bool) else entry)
                        for screen, entry in accepted.items())

        faulty = {'unaccept nic nie usuwa': unaccept_removes_nothing, 'who zawsze z payloadu': who_always_from_payload,
                  'bool jako iteration': bool_as_iteration}
        for name, mutate in faulty.items():
            with self.subTest(name):
                bad = 0
                for seed in DIFF_SEEDS[:2]:
                    cases = self.corpora[seed]
                    for ops, js_view in zip(cases, self.js_folds(cases)):
                        bad += canonical(python_view(ops)['fold']['acceptance']) != canonical(mutate(ops, js_view['fold']['acceptance']))
                self.assertGreater(bad, 0, 'błędny wynik nie został wykryty')
        # Kontrola: niezmieniony wynik silnika na tych samych dziennikach = 0 rozjazdów.
        for seed in DIFF_SEEDS[:2]:
            cases = self.corpora[seed]
            for ops, js_view in zip(cases, self.js_folds(cases)):
                self.assertEqual(canonical(python_view(ops)['fold']['acceptance']), canonical(js_view['fold']['acceptance']))



class EngineV2AcceptancePureTests(unittest.TestCase):
    """Czyste funkcje okna i chipu akceptacji (eksportowane przez silnik; kontrakt E/F): przez queue.cjs."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-accept-pure-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def many(self, name, arg_lists):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 'a-in.json', Path(self.tmp) / 'a-out.json'
        input_path.write_text(json.dumps({'calls': [{'fn': name, 'args': args} for args in arg_lists]}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js),
                                 str(input_path), str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def test_summary_states_and_text(self):
        ids = ['s1', 's2', 's3']
        valid = lambda who='a': {'valid': True, 'who': who}
        cases = [
            (['s1', 's2', 's3'], {}, None, ('none', 0, 0, 'Akceptacja 0/3')),
            (ids, {'s1': valid()}, None, ('partial', 1, 0, 'Akceptacja 1/3')),
            (ids, {'s1': valid(), 's2': valid(), 's3': valid()}, None, ('full', 3, 0, 'Akceptacja 3/3')),
            (ids, {'s1': valid(), 's2': {'valid': False}}, None, ('stale', 1, 1, 'Akceptacja 1/3')),
            # Ekran przykładowy / usunięty (poza `ids`) nie liczy się do k/n.
            (ids, {'s1': valid(), 'przyklad': valid()}, None, ('partial', 1, 0, 'Akceptacja 1/3')),
        ]
        out = self.many('acceptanceSummary', [[i, a, f] for i, a, f, _ in cases])
        for (_, _, _, expected), got in zip(cases, out):
            self.assertEqual((got['state'], got['accepted'], got['stale'], got['text']), expected)
            self.assertTrue(got['known'])
        self.assertIn('nieaktualna: zmieniono pliki', out[3]['title'])
        self.assertNotIn('nieaktualna', out[1]['title'])

    def test_summary_readonly_mode_has_no_staleness(self):
        # Bez API (acceptance = null) liczy się wpis foldu i opis mówi, że aktualność sprawdza serwer.
        folded = {'s1': {'hash': 'h'}, 's2': {'hash': 'h2'}}
        got, empty, none_ids = self.many('acceptanceSummary', [[['s1', 's2', 's3'], None, folded], [['s1'], None, None], [[], {}, {}]])
        self.assertEqual((got['state'], got['known'], got['accepted'], got['stale']), ('partial', False, 2, 0))
        self.assertIn('Aktualność sprawdza serwer', got['title'])
        self.assertEqual((empty['state'], empty['known']), ('none', False))
        self.assertIsNone(none_ids)

    def test_availability_roles_and_blocks(self):
        base = {'mode': 'api', 'frozen': False, 'embed': False, 'example': False, 'version': 'v', 'hash': 'h',
                'user': {'role': 'acceptor', 'mustChange': False, 'login': 'c@example.com'}}
        def ctx(**extra):
            data = dict(base)
            data.update(extra)
            return [data]
        cases = [
            (ctx(), (True, True, '')),
            (ctx(user={'role': 'developer', 'mustChange': False}), (True, True, '')),
            (ctx(user={'role': 'commenter', 'mustChange': False}), (False, False, '')),
            (ctx(user=None), (False, False, '')),
            (ctx(mode='static'), (False, False, '')),
            (ctx(frozen=True), (False, False, '')),
            (ctx(embed=True), (False, False, '')),
            (ctx(example=True), (False, False, '')),
            (ctx(expired=True), (True, False, 'Sesja wygasła')),
            (ctx(user={'role': 'acceptor', 'mustChange': True}), (True, False, 'ustaw własne hasło')),
            (ctx(stale=True), (True, False, 'zmieniono')),
            (ctx(offline=True), (True, False, 'nie trafia do kolejki')),
            (ctx(hash=None), (True, False, 'nie podał wersji')),
        ]
        out = self.many('acceptAvailability', [args for args, _ in cases])
        for (args, (visible, enabled, reason)), got in zip(cases, out):
            self.assertEqual((got['visible'], got['enabled']), (visible, enabled), args)
            self.assertIn(reason, got['reason'])

    def test_accept_types_never_queueable(self):
        """K16: accept/unaccept nigdy do kolejki offline (kontrola: lista kolejki to dokładnie osiem typów)."""
        if not find_node()[0]:
            self.skipTest(find_node()[1])
        out = subprocess.run([find_node()[0], '-e', 'const a=require(process.argv[1]);process.stdout.write(JSON.stringify([a.QUEUEABLE_TYPES,a.PROTOTYPE_TYPES,a.ACCEPT_ROLES]))',
                              str(self.engine_js)], capture_output=True, text=True, timeout=60)
        queueable, prototype_types, roles = json.loads(out.stdout)
        self.assertNotIn('accept', queueable)
        self.assertNotIn('unaccept', queueable)
        self.assertIn('accept', prototype_types)
        self.assertEqual(roles, ['acceptor', 'developer'])
        self.assertEqual(sorted(queueable), sorted(QUEUEABLE))  # kontrola: lista kolejki to dokładnie K16

    def test_submit_state_blocks_and_ok(self):
        ok = {'version': 'v', 'hash': 'h', 'scope': 'screen'}
        cases = [
            (dict(ok), (True, '')),
            (dict(ok, expired=True), (False, 'Sesja wygasła')),
            (dict(ok, mustChange=True), (False, 'ustaw własne hasło')),
            (dict(ok, stale=True), (False, 'zmieniono')),
            (dict(ok, offline=True), (False, 'nie trafia do kolejki')),
            (dict(ok, busy=True), (False, '')),
            (dict(ok, version=None), (False, 'nie podał wersji')),
            (dict(ok, hash=None), (False, 'nie podał wersji')),
            (dict(ok, accepted=True), (False, 'już aktualną akceptację')),
            (dict(ok, scope='project', projectState='ready', targets=2), (True, '')),
            (dict(ok, scope='project', projectState='loading', targets=2), (False, 'Poczekaj')),
            (dict(ok, scope='project', projectState='failed', targets=2), (False, 'Nie udało się')),
            (dict(ok, scope='project', projectState='ready', targets=0), (False, 'Brak prototypów v2')),
        ]
        out = self.many('acceptSubmitState', [[state] for state, _ in cases])
        for (state, (enabled, reason)), got in zip(cases, out):
            self.assertEqual(got['enabled'], enabled, state)
            self.assertIn(reason, got['reason'], state)

    def test_can_unaccept_own_or_developer_only_when_current(self):
        acceptor = {'role': 'acceptor', 'login': 'c@example.com'}
        cases = [
            (acceptor, {'who': 'c@example.com', 'valid': True}, True),
            (acceptor, {'who': 'inny@example.com', 'valid': True}, False),   # cudza akceptacja
            ({'role': 'developer', 'login': 'd@example.com'}, {'who': 'inny@example.com', 'valid': True}, True),
            ({'role': 'commenter', 'login': 'c@example.com'}, {'who': 'c@example.com', 'valid': True}, False),
            (acceptor, {'who': 'c@example.com', 'valid': False}, False),     # nieaktualna: bez „Cofnij” (kontrakt F)
            (None, {'who': 'c@example.com', 'valid': True}, False),
            (acceptor, None, False),
        ]
        out = self.many('canUnaccept', [[user, entry] for user, entry, _ in cases])
        self.assertEqual(out, [expected for _, _, expected in cases])

    def test_project_summary(self):
        data = {'prototypes': [
            {'slug': 'alpha', 'title': 'Alfa', 'iteration': 2, 'screens': 3, 'open': 4, 'awaiting': 1, 'version': 'va', 'format': 2},
            {'slug': 'beta', 'title': 'Beta', 'iteration': 1, 'screens': 0, 'open': 0, 'awaiting': 0, 'version': 'vb', 'format': 2},
            {'slug': 'stary', 'title': 'Stary', 'iteration': None, 'screens': 2, 'open': None, 'awaiting': None, 'version': None, 'format': 1},
            {'title': 'bez sluga', 'format': 2}, None, 'x',
        ]}
        got = self.many('projectSummary', [[data, 'alpha', 'AKTUALNA'], [None, 'alpha', 'x']])
        summary, empty = got
        self.assertEqual([item['slug'] for item in summary['items']], ['alpha', 'beta', 'stary'])
        self.assertEqual((summary['v2'], summary['v1'], summary['withScreens']), (2, 1, 1))
        # Wersja bieżącego prototypu pochodzi z wczytania strony, pozostałych z GET /project.
        self.assertEqual(summary['versions'], {'alpha': 'AKTUALNA', 'beta': 'vb'})
        self.assertEqual((summary['open'], summary['awaiting']), (4, 1))
        self.assertEqual(summary['optionText'], 'Wszystkie prototypy v2 (pominięto 1)')
        alpha, beta, stary = summary['items']
        self.assertEqual((alpha['target'], beta['target'], stary['target']), (True, False, False))
        self.assertEqual((empty['items'], empty['v1']), ([], 0))
        self.assertEqual(empty['optionText'], 'Wszystkie prototypy w obecnej wersji')

    def test_bodies_and_result_classification(self):
        bodies = self.many('acceptScreenBody', [['c-1', 's2', 'hh', 'vv']]) + self.many('unacceptBody', [['c-2', 's2']])
        self.assertEqual(bodies[0], {'cid': 'c-1', 'type': 'accept', 'payload': {'scope': 'screen', 'screens': {'s2': 'hh'}, 'version': 'vv'}})
        self.assertEqual(bodies[1], {'cid': 'c-2', 'type': 'unaccept', 'payload': {'screens': ['s2']}})
        # Klient nie wysyła pól, które serwer odrzuca jako `field_not_allowed` (via, who, iteration).
        for forbidden in ('via', 'who', 'iteration'):
            self.assertNotIn(forbidden, bodies[0]['payload'])
        cases = [((201, None), 'done'), ((200, None), 'done'), ((409, 'stale_view'), 'stale'), ((409, 'partial'), 'partial'),
                 ((409, 'not_accepted'), 'not_accepted'), ((403, 'forbidden'), 'forbidden'), ((400, 'unknown_screen'), 'invalid'),
                 ((503, None), 'busy'), ((429, None), 'busy'), ((401, None), 'auth'), ((403, 'password_change_required'), 'password')]
        out = self.many('classifyAcceptResult', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_slug_list_and_accepted_line(self):
        self.assertEqual(self.many('slugList', [[['a', 5, None, 'b', '']], ['x'], [None]]), [['a', 'b'], [], []])
        entry = {'who': 'jan@example.com', 'at': '2026-11-04T10:00:00.000Z', 'iteration': 2, 'via': 'cli'}
        out = self.many('acceptedLine', [[entry, 'Jan', None], [dict(entry, via='ui', iteration=None), None, None], [None, None, None]])
        self.assertEqual(out[0], 'Zaakceptowano: Jan, 2026-11-04T10:00:00.000Z, iteracja 2 (CLI)')
        self.assertEqual(out[1], 'Zaakceptowano: jan@example.com, 2026-11-04T10:00:00.000Z (UI)')
        self.assertEqual(out[2], 'Zaakceptowano: nieznany')

    def test_pure_assertions_can_fail(self):
        """Atrapa błędu: acceptor cofa cudze akceptacje; asercja `canUnaccept` musi to wykryć."""
        args = [[{'role': 'acceptor', 'login': 'c@example.com'}, {'who': 'inny@example.com', 'valid': True}]]
        assert_detects(self, lambda out: self.assertEqual(out, [False]), self.many('canUnaccept', args), [True])

    # -- poprawki po przeglądzie F3 (runda 1): offline, cel chipu, wynik po zamknięciu okna, stan „łączenie” --

    def test_offline_is_the_engine_flag_or_navigator_online_false(self):
        cases = [((False, True), False), ((False, None), False), ((False, False), True), ((True, True), True),
                 ((True, None), True), ((None, False), True), ((False, 'false'), False), (('true', True), False)]
        out = self.many('acceptOffline', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: ignorowanie navigator.onLine przepuszcza „offline” bez nieudanego żądania
        assert_detects(self, lambda value: self.assertEqual(value, [True]), self.many('acceptOffline', [[False, False]]), [False])

    def test_chip_target_is_the_current_screen_or_the_first_acceptable_one(self):
        ids = ['s2', 's3', 's4']
        cases = [(('s3', ids), 's3'),            # zwykły bieżący ekran
                 (('s1', ids), 's2'),            # bieżący jest przykładowy (poza ids): pierwszy do akceptacji
                 ((None, ids), 's2'), (('nieznany', ids), 's2'), ((5, ids), 's2'),
                 (('s2', ids), 's2'), (('s3', []), None), (('s3', None), None), (('s3', 'nie lista'), None)]
        out = self.many('acceptTargetScreen', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: zawsze pierwszy ekran do akceptacji, bez uwzględnienia bieżącego
        assert_detects(self, lambda value: self.assertEqual(value, ['s3']), self.many('acceptTargetScreen', [['s3', ids]]), ['s2'])

    def test_loading_gives_its_own_reason_instead_of_reload(self):
        base = {'mode': 'api', 'user': {'role': 'acceptor', 'mustChange': False}, 'loading': True}
        loading, loaded_without_version, stale_wins, offline_wins, ready = self.many('acceptAvailability', [
            [base], [dict(base, loading=False)], [dict(base, stale=True)], [dict(base, offline=True)],
            [dict(base, loading=False, version='v', hash='h')]])
        self.assertEqual((loading['visible'], loading['enabled'], loading['reason']), (True, False, 'Łączenie z serwerem…'))
        self.assertIn('przeładuj', loaded_without_version['reason'], 'control: without loading the missing version is a real problem')
        self.assertIn('zmieniono', stale_wins['reason'])
        self.assertIn('nie trafia do kolejki', offline_wins['reason'], 'offline beats loading')
        self.assertEqual((ready['enabled'], ready['reason']), (True, ''))
        sub = {'version': None, 'scope': 'screen', 'loading': True}
        a, b, c, d, e = self.many('acceptSubmitState', [
            [sub], [dict(sub, loading=False)], [dict(sub, loading=False, version='v', hash='h')],
            [dict(sub, scope='project', version='v', loading=False, projectState='idle')],
            [dict(sub, scope='project', version='v', loading=False, projectState='ready', targets=1)]])
        self.assertEqual(a, {'enabled': False, 'reason': 'Łączenie z serwerem…'})
        self.assertIn('przeładuj', b['reason'])
        self.assertEqual(c, {'enabled': True, 'reason': ''})
        self.assertEqual(d, {'enabled': False, 'reason': 'Poczekaj na listę prototypów.'}, 'idle waits like loading')
        self.assertEqual(e, {'enabled': True, 'reason': ''})
        # offline w oknie: „Akceptuję” nieaktywne z powodem także przy wczytanym dzienniku
        off = self.many('acceptSubmitState', [[{'version': 'v', 'hash': 'h', 'scope': 'screen', 'offline': True}]])[0]
        self.assertEqual(off['enabled'], False)
        self.assertIn('nie trafia do kolejki', off['reason'])

    def test_done_message_names_the_screen_the_undo_or_the_project_counts(self):
        out = self.many('acceptDoneMessage', [['screen', {}, 'Tydzień'], ['undo', {}, 'Tydzień'],
                                              ['project', {'accepted': ['a'], 'skipped': []}, None],
                                              ['project', {'accepted': ['a', 'b'], 'skipped': ['c', 'd']}, None],
                                              ['project', {'accepted': ['a', 'b', 'c', 'd', 'e'], 'skipped': ['x']}, None],
                                              ['project', None, None]])
        self.assertEqual(out[0], 'Zaakceptowano ekran „Tydzień”.')
        self.assertEqual(out[1], 'Cofnięto akceptację ekranu „Tydzień”.')
        self.assertEqual(out[2], 'Zaakceptowano 1 prototyp.')
        self.assertEqual(out[3], 'Zaakceptowano 2 prototypy; pominięto 2.')
        self.assertEqual(out[4], 'Zaakceptowano 5 prototypów; pominięto 1.')
        self.assertEqual(out[5], 'Zaakceptowano 0 prototypów.', 'garbage in the response does not throw')
        self.assertNotEqual(out[0], out[1])

    def test_closed_window_outcome_polls_on_success_and_marks_stale_only_for_this_page(self):
        def outcome(action, kind='screen', data=None, title='Tydzień', current='demo'):
            return self.many('acceptClosedOutcome', [[action, kind, data, title, current]])[0]

        done = outcome('done')
        self.assertEqual((done['poll'], done['markStale'], done['auth']), (True, False, False))
        self.assertEqual(done['message'], 'Zaakceptowano ekran „Tydzień”.')
        partial = outcome('partial', 'project', {'pending': ['beta', 'demo', 5]})
        self.assertTrue(partial['poll'], 'part of it was written: the state must be refreshed')
        self.assertIn('czeka: beta, demo', partial['message'])
        self.assertIn('ponów', partial['message'])
        self.assertNotIn('5', partial['message'])
        self.assertNotIn('czeka', outcome('partial', 'project', {})['message'])
        # stale: inne prototypy projektu nie oznaczają oglądanej strony jako nieaktualnej
        others = outcome('stale', 'project', {'stale': ['beta']})
        self.assertEqual((others['markStale'], others['poll']), (False, False))
        self.assertIn('beta', others['message'])
        this_page = outcome('stale', 'project', {'stale': ['beta', 'demo']})
        self.assertTrue(this_page['markStale'], 'control: the page itself changed')
        screen_stale = outcome('stale', 'screen', {})
        self.assertTrue(screen_stale['markStale'])
        self.assertIn('przeładuj', screen_stale['message'])
        self.assertEqual((outcome('not_accepted')['poll'], outcome('not_accepted')['message']), (True, 'Ten ekran nie ma już akceptacji.'))
        auth = outcome('auth')
        self.assertEqual((auth['auth'], auth['poll']), (True, False))
        self.assertIn('nie trafia do kolejki', outcome('offline')['message'])
        other = outcome('cokolwiek')
        self.assertEqual((other['poll'], other['auth'], other['markStale']), (False, False, False))
        # runda 2: generyczna odmowa z przyczyną z serwera (jeśli jest) i informacją, że nic nie zapisano
        self.assertIn('Nic nie zapisano', other['message'])
        self.assertNotIn(':', other['message'].split('.')[0].replace('akceptacji', ''), 'no reason given: no reason in the text')
        with_reason = self.many('acceptClosedOutcome', [['forbidden', 'screen', {}, 't', 'demo', 'brak uprawnień']])[0]
        self.assertIn('brak uprawnień', with_reason['message'])
        for garbage in (None, 'x', [], 5):
            self.assertEqual(self.many('acceptClosedOutcome', [['stale', 'project', garbage, 't', 'demo']])[0]['markStale'], True)

    def test_outcome_faulty_results_are_detected(self):
        out = self.many('acceptClosedOutcome', [['done', 'screen', {}, 'T', 'demo']])[0]
        # atrapa błędu: udana akceptacja nie odświeża stanu (poll: false)
        assert_detects(self, lambda value: self.assertTrue(value['poll']), out, dict(out, poll=False))

    # -- poprawki po przeglądzie F3 (runda 2): serverOk, powód ładowania, zawijanie Tab, nowa sygnatura wyniku --

    def test_online_false_is_only_a_hint_once_the_server_answered(self):
        cases = [((False, False, True), False),    # localhost bez interfejsu: serwer odpowiedział
                 ((False, False, False), True), ((False, False, None), True), ((False, False), True),
                 ((False, True, False), False), ((False, None, False), False),
                 ((True, True, True), True),       # flaga silnika (nieudane żądanie) blokuje zawsze
                 ((True, False, True), True), ((False, False, 'true'), True)]
        out = self.many('acceptOffline', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: blokada także przy serwerze, który odpowiedział
        assert_detects(self, lambda value: self.assertEqual(value, [False]), self.many('acceptOffline', [[False, False, True]]), [True])

    def test_load_reason_names_the_real_problem_instead_of_connecting_forever(self):
        out = self.many('acceptLoadReason', [[None], [''], ['missing'], ['needs_migration'], ['invalid_log'], ['inny_kod']])
        self.assertEqual(out[0], 'Łączenie z serwerem…')
        self.assertEqual(out[1], out[0], 'no problem is the plain connecting state')
        self.assertIn('niedostępny', out[2])
        self.assertIn('migracji', out[3])
        self.assertIn('comments.json', out[4])
        self.assertIn('inny_kod', out[5])
        self.assertEqual(len(set(out[:1] + out[2:])), 5, 'every problem has its own text')
        # the reason reaches both buttons while the log is not loaded, and "loading" without a problem stays "connecting"
        avail = self.many('acceptAvailability', [[{'mode': 'api', 'user': {'role': 'acceptor'}, 'loading': True, 'problem': 'needs_migration'}],
                                                 [{'mode': 'api', 'user': {'role': 'acceptor'}, 'loading': True}]])
        self.assertIn('migracji', avail[0]['reason'])
        self.assertEqual(avail[1]['reason'], 'Łączenie z serwerem…')
        submit = self.many('acceptSubmitState', [[{'loading': True, 'problem': 'missing', 'scope': 'screen'}]])[0]
        self.assertEqual(submit['enabled'], False)
        self.assertIn('niedostępny', submit['reason'])

    def test_tab_wraps_inside_the_window(self):
        items = ['a', 'b', 'c']
        cases = [(('c', False), 'a'),    # Tab from the last
                 (('a', True), 'c'),     # Shift+Tab from the first
                 (('b', False), None), (('b', True), None), (('a', False), None), (('c', True), None),   # inside: browser default
                 (('x', False), 'a'), (('x', True), 'c'), ((None, False), 'a')]  # focus outside the list
        out = self.many('dialogTabTarget', [[items, current, shift] for (current, shift), _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        self.assertEqual(self.many('dialogTabTarget', [[['a'], 'a', False], [['a'], 'a', True], [[], 'a', False], [None, 'a', True]]),
                         ['a', 'a', None, None])
        # atrapa błędu: fokus opuszcza okno przy Tab z ostatniego elementu
        assert_detects(self, lambda value: self.assertEqual(value, ['a']), self.many('dialogTabTarget', [[items, 'c', False]]), [None])

    def test_closed_window_outcome_new_fields_and_reasons(self):
        def outcome(action, reason=None, kind='screen', data=None):
            return self.many('acceptClosedOutcome', [[action, kind, data, 'Tydzień', 'demo', reason]])[0]
        expected = {'password': 'password', 'csrf': 'renew'}
        for action, flag in expected.items():
            result = outcome(action)
            self.assertTrue(result[flag], action)
            self.assertFalse(result['poll'])
        for action in ('done', 'partial', 'stale', 'not_accepted', 'auth', 'offline', 'busy', 'forbidden', 'invalid', 'error'):
            self.assertFalse(outcome(action)['password'] or outcome(action)['renew'], 'control: only password and csrf set the new flags (%s)' % action)
        self.assertTrue(outcome('auth')['auth'])
        self.assertEqual(len({outcome(action)['message'] for action in ('password', 'csrf', 'busy', 'offline', 'auth')}), 5,
                         'every case has its own message')
        self.assertIn('zajęty', outcome('busy')['message'])
        for action in ('forbidden', 'invalid', 'error'):
            self.assertIn('Nic nie zapisano', outcome(action)['message'])
            self.assertIn('brak uprawnień', outcome(action, 'brak uprawnień')['message'])
            self.assertNotIn('brak uprawnień', outcome(action)['message'])
        self.assertNotIn('None', outcome('forbidden', 5)['message'], 'a reason that is not a string is ignored')


    def test_escape_is_refused_only_for_the_forced_password_window(self):
        cases = [(('password', True), False),                       # wymuszona zmiana hasła (K42): tylko zmiana albo „Wyloguj”
                 (('password', False), True), (('password', None), True), (('password', 'true'), True),   # dobrowolna zmiana
                 (('login', True), True), (('accept', True), True), (('restore', True), True), (('compare', False), True),
                 ((None, True), True), (('', True), True), (('Password', True), True)]
        out = self.many('dialogEscapeAllowed', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: dobrowolna zmiana hasła też pułapkuje (Esc zablokowany)
        assert_detects(self, lambda value: self.assertEqual(value, [True]), self.many('dialogEscapeAllowed', [['password', False]]), [False])

    # -- pętla końcowa W1 (F-ENG-6, F-ENG-4, F-ENG-8): kolejka, Esc w panelu, odpytywanie, filtry, dokument --

    def test_queue_accepts_exactly_the_eight_k16_types(self):
        good = [{'cid': 'c-%d' % number, 'type': kind, 'payload': {}} for number, kind in enumerate(QUEUEABLE)]
        self.assertEqual(len(good), 8)
        self.assertEqual(self.many('queueItemAccepted', [[item] for item in good]), [True] * 8)
        refused_types = ['delete', 'accept', 'unaccept', 'triage', 'decision', 'iteration', 'restore', 'bogus', '', None, 5, 'Create']
        refused = [{'cid': 'c-x', 'type': kind, 'payload': {}} for kind in refused_types]
        self.assertEqual(self.many('queueItemAccepted', [[item] for item in refused]), [False] * len(refused))
        broken = [None, 5, 'x', [], {}, {'type': 'create', 'payload': {}}, {'cid': '', 'type': 'create', 'payload': {}},
                  {'cid': 7, 'type': 'create', 'payload': {}}, {'cid': 'c', 'type': 'create'},
                  {'cid': 'c', 'type': 'create', 'payload': []}, {'cid': 'c', 'type': 'create', 'payload': None}]
        self.assertEqual(self.many('queueItemAccepted', [[item] for item in broken]), [False] * len(broken))

    def test_queue_items_filter_keeps_order_and_drops_everything_else(self):
        items = [{'cid': 'a', 'type': 'create', 'payload': {}}, {'cid': 'b', 'type': 'accept', 'payload': {}}, None,
                 {'cid': 'c', 'type': 'reply', 'payload': {'x': 1}}, {'cid': 'd', 'type': 'delete', 'payload': {}},
                 {'cid': 'e', 'type': 'vote', 'payload': {}}, {'cid': 'f', 'type': 'restore', 'payload': {}}, {'cid': 'g', 'type': 'unaccept', 'payload': {}}]
        out = self.many('queueItemsAccepted', [[items], ['nie lista'], [None], [{}], [[]]])
        self.assertEqual([item['cid'] for item in out[0]], ['a', 'c', 'e'])
        self.assertEqual(out[1:], [[], [], [], []])
        # atrapa błędu: filtr przepuszcza każdy element z typem (stan sprzed poprawki)
        assert_detects(self, lambda cids: self.assertEqual(cids, ['a', 'c', 'e']), [item['cid'] for item in out[0]],
                       ['a', 'b', 'c', 'd', 'e', 'f', 'g'])

    def test_escape_in_the_panel_returns_an_action_and_whether_to_ask_about_the_draft(self):
        """Sprzed 9.1b: `panelEscapeAction` zwracała napis ('blur'|'list'|'close'); teraz `{action, confirm}` wg 9.1-eng-poprawki-2.md: Esc
        opuszczający widok ze szkicem w dowolnym polu panelu (`anyDraft`) wymaga pytania „Porzucić szkic?”, bo szkic może być poza polem z fokusem."""
        cases = [({'inField': True, 'fieldDraft': True, 'anyDraft': True, 'threadView': True}, 'blur', False),    # niepuste pole: tylko blur
                 ({'inField': True, 'fieldDraft': False, 'anyDraft': False, 'threadView': True}, 'close', False),  # puste pole: zamknij
                 ({'inField': True, 'fieldDraft': False, 'anyDraft': True, 'threadView': True}, 'close', True),    # szkic w innym polu
                 ({'inField': False, 'fieldDraft': False, 'anyDraft': True, 'threadView': True}, 'list', True),
                 ({'inField': False, 'fieldDraft': False, 'anyDraft': False, 'threadView': True}, 'list', False),
                 ({'inField': False, 'fieldDraft': False, 'anyDraft': False, 'threadView': False}, 'close', False),
                 ({'inField': False, 'fieldDraft': False, 'anyDraft': True, 'threadView': False}, 'close', True),
                 ({}, 'close', False), (None, 'close', False),
                 ({'inField': 'true', 'fieldDraft': 'true', 'anyDraft': 'true', 'threadView': 'true'}, 'close', False)]  # tylko prawdziwe true
        out = self.many('panelEscapeAction', [[state] for state, _, _ in cases])
        self.assertEqual(out, [{'action': action, 'confirm': confirm} for _, action, confirm in cases])
        # atrapa błędu: opuszczenie widoku ze szkicem nie pyta „Porzucić szkic?”
        draft_state = [{'inField': False, 'anyDraft': True, 'threadView': True}]
        assert_detects(self, lambda value: self.assertEqual(value['confirm'], True), self.many('panelEscapeAction', [draft_state])[0],
                       {'action': 'list', 'confirm': False})

    def test_search_enter_during_ime_composition_is_ignored(self):
        out = self.many('screenSearchKeyAction', [[{'key': 'Enter', 'isComposing': True}], [{'key': 'Enter'}], [{'key': 'Enter', 'isComposing': False}],
                                                  [{'key': 'Escape', 'isComposing': True}], [{'key': 'ArrowDown', 'isComposing': True}]])
        self.assertEqual(out, [None, 'choose', 'choose', None, None])

    def test_poll_delay_backs_off_to_five_minutes(self):
        cases = [((True, 0), 5000), ((False, 0), 30000), ((True, None), 5000), ((True, 1), 10000), ((True, 3), 40000), ((True, 6), 300000),
                 ((True, 50), 300000), ((False, 1), 60000), ((False, 4), 300000), ((False, 50), 300000), ((True, -2), 5000)]
        out = self.many('pollDelay', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: brak sufitu (5000 * 2^50 zamiast 5 minut)
        assert_detects(self, lambda value: self.assertEqual(value, [300000]), self.many('pollDelay', [[True, 50]]), [5000 * 2 ** 50])

    def test_new_and_lost_filters(self):
        seen = '2026-11-05T10:00:00.000Z'
        def thread(status='open', events=(), triage=None, decisions=()):
            return {'id': 'th', 'status': status, 'events': [{'type': 'reply', 'by': by, 'at': at} for by, at in events],
                    'triage': triage, 'decisions': list(decisions)}
        mine_after = thread(events=[('ja@example.com', '2026-11-05T10:05:00.000Z')])
        theirs_after = thread(events=[('ktos@example.com', '2026-11-05T10:05:00.000Z')])
        theirs_before = thread(events=[('ktos@example.com', '2026-11-05T09:00:00.000Z')])
        same_instant = thread(events=[('ktos@example.com', seen)])
        context = {'seenAt': seen, 'me': 'ja@example.com'}
        pairs = [[theirs_after, 'new', context], [mine_after, 'new', context], [theirs_before, 'new', context], [same_instant, 'new', context],
                 [theirs_after, 'new', {'me': 'ja@example.com'}], [theirs_after, 'new', None], [theirs_after, 'new', {'seenAt': None, 'me': 'ja@example.com'}]]
        self.assertEqual(self.many('threadMatches', pairs), [True, False, False, False, False, False, False])
        # `hasHandle` is a function in the engine, so the handle cases run through `js_threads`/`js_counts` (a closure on the node side)
        handle_cases = [(thread('open'), False, True), (thread('awaiting'), False, True), (thread('decided'), False, True), (thread('closed'), False, False),
                        (thread('deferred'), False, False), (thread('open'), True, False)]
        for item, has, lost in handle_cases:
            got = self.js_threads([item], 'lost', has)
            self.assertEqual(got, 1 if lost else 0, (item['status'], has))
            self.assertEqual(self.js_threads([item], 'open', has), 1 if (item['status'] in ('open', 'awaiting', 'decided') and has) else 0)
        counts = self.js_counts([thread('open'), thread('closed'), thread('awaiting', triage={'class': 'disputed'})], False)
        self.assertEqual(counts, {'open': 0, 'new': 0, 'agent': 1, 'lost': 2, 'all': 3})
        counts = self.js_counts([thread('open'), thread('closed'), thread('awaiting', triage={'class': 'disputed'})], True)
        self.assertEqual(counts, {'open': 2, 'new': 0, 'agent': 1, 'lost': 0, 'all': 3})
        # atrapa błędu: „new” liczy też zdarzenia samego użytkownika
        assert_detects(self, lambda value: self.assertEqual(value, [False]), self.many('threadMatches', [[mine_after, 'new', context]]), [True])

    def js_threads(self, threads, filter_name, has_handle):
        script = ("const api=require(process.argv[1]);const d=JSON.parse(process.argv[2]);"
                  "const ctx={seenAt:'2026-11-05T10:00:00.000Z',me:'ja@example.com',hasHandle:()=>d.h};"
                  "process.stdout.write(String(api.filterThreads(d.t,d.f,ctx).length));")
        out = subprocess.run([self.node, '-e', script, str(self.engine_js), json.dumps({'t': threads, 'f': filter_name, 'h': has_handle})],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        return int(out.stdout)

    def js_counts(self, threads, has_handle):
        script = ("const api=require(process.argv[1]);const d=JSON.parse(process.argv[2]);"
                  "const ctx={seenAt:'2026-11-05T10:00:00.000Z',me:'ja@example.com',hasHandle:()=>d.h};"
                  "process.stdout.write(JSON.stringify(api.filterCounts(d.t,ctx)));")
        out = subprocess.run([self.node, '-e', script, str(self.engine_js), json.dumps({'t': threads, 'h': has_handle})],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        return json.loads(out.stdout)

    def test_normalize_comments_document_ignores_foreign_documents_whole(self):
        op = {'id': 'op-1', 'type': 'create', 'threadId': 'th-1', 'at': '2026-11-05T10:00:00.000Z', 'by': 'a@example.com', 'payload': {}}
        head = {'format': 'skillsync-prototype-comments', 'version': 2, 'prototypeId': 'demo', 'operations': [op, {'id': 5}, None, dict(op)]}
        cases = [(head, 'demo', 1),                                             # valid; junk and the duplicate id dropped
                 (dict(head, prototypeId=''), 'demo', 1), (dict(head, prototypeId=None), 'demo', 1),   # empty id matches any prototype
                 (dict(head, prototypeId='inny'), 'demo', 0), (dict(head, format='x'), 'demo', 0), (dict(head, version=1), 'demo', 0),
                 (dict(head, version='2'), 'demo', 0), (dict(head, operations={}), 'demo', 0), (dict(head, operations='x'), 'demo', 0),
                 (None, 'demo', 0), ([], 'demo', 0), ('x', 'demo', 0)]
        out = self.many('normalizeCommentsDocument', [[value, slug] for value, slug, _ in cases])
        self.assertEqual([len(item) for item in out], [expected for _, _, expected in cases])
        # atrapa błędu: dokument innego prototypu wczytany w całości
        foreign = len(self.many('normalizeCommentsDocument', [[dict(head, prototypeId='inny'), 'demo']])[0])
        assert_detects(self, lambda size: self.assertEqual(size, 0), foreign, 1)

    def test_interrupted_project_accept_is_partial_only_when_something_was_saved(self):
        done = {'accepted': ['alpha'], 'pending': ['beta']}
        none = {'accepted': [], 'pending': ['alpha', 'beta']}
        cases = [((500, 'write_failed', done), 'partial'), ((507, 'file_too_large', done), 'partial'),
                 ((409, 'invalid_log', done), 'partial'), ((409, 'needs_migration', done), 'partial'),
                 # contrast: the same responses with nothing saved stay errors (nothing to retry in part)
                 ((500, 'write_failed', none), 'error'), ((507, 'file_too_large', none), 'error'),
                 ((409, 'invalid_log', none), 'error'), ((409, 'needs_migration', none), 'error'),
                 ((500, 'write_failed', {}), 'error'), ((500, 'write_failed', None), 'error'), ((500, 'write_failed'), 'error'),
                 ((500, 'write_failed', {'accepted': 'alpha'}), 'error'), ((500, 'write_failed', {'accepted': [5, None, '']}), 'error'),
                 # other statuses ignore `accepted`
                 ((201, None, done), 'done'), ((409, 'stale_view', done), 'stale'), ((409, 'partial', none), 'partial'),
                 ((403, 'forbidden', done), 'forbidden'), ((400, 'unknown_screen', done), 'invalid'), ((503, None, done), 'busy')]
        out = self.many('classifyAcceptResult', [list(args) for args, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        # atrapa błędu: przerwana akceptacja projektu traci stan „częściowo”
        assert_detects(self, lambda value: self.assertEqual(value, ['partial']),
                       self.many('classifyAcceptResult', [[500, 'write_failed', done]]), ['error'])


# ── Kontrakt API eksportów (K4): zbiór nazw `module.exports` i wywołania nazw bez innego testu ────────────────────
# Kontrakt: docs/prototype-contract/api-eksportow.md; asercje wynikają z opisu słowami i ze speca W1.

EXPECTED_EXPORTS = (
   'ENGINE_VERSION', 'FORMAT_NAME', 'FORMAT_VERSION', 'API_ROOT', 'QUEUE_PREFIX', 'REJECTED_PREFIX', 'SEEN_PREFIX',
   'V1_COMMENTS_PREFIX', 'OPERATION_TYPES', 'THREAD_TYPES', 'PROTOTYPE_TYPES', 'QUEUEABLE_TYPES', 'STATUSES',
   'OPEN_STATUSES', 'FILTERS', 'MAX_TEXT', 'isOperation', 'normalizeOperations', 'sortOperations', 'mergeOperations',
   'foldDocument', 'foldOperations', 'isOpenStatus', 'openCountByScreen', 'voteSummary', 'anchorKind',
   'personFromLogin', 'computeLabels', 'peopleFromOperations', 'initialsFor', 'avatarTone', 'threadMatches',
   'filterThreads', 'filterCounts', 'classifyWriteResult', 'mergeQueueItems', 'pollDelay', 'newClientId',
   'canonicalOperation', 'serializeCommentsDocument', 'normalizeCommentsDocument', 'convertV1Operations',
   'v1ImportId', 'THEME_LOCK_METAS', 'ensureThemeLockMetas', 'isDarkReaderLeftover', 'hasDarkReaderLock',
   'removeDarkReaderLeftovers', 'screenEntriesFromLinks', 'normalizeSearch', 'filterScreens', 'adjacentScreen',
   'visibleScreenId', 'screenListMove', 'screenShortcut', 'screenSearchKeyAction', 'focusableAfter',
   'screenSearchTabTarget', 'shouldProbeIteration', 'screenEntriesWithCounts', 'toolbarFitsOneRow', 'RESTORE_ROLES',
   'MAX_RESTORE_REASON', 'iterationPath', 'iterationList', 'iterationForThread', 'embedModeFrom', 'threadIdFromHash',
   'restoreAvailability', 'restoreSubmitState', 'restoreUnchangedText', 'classifyRestoreResult', 'restoreEffects',
   'ACCEPT_ROLES', 'acceptanceSummary', 'acceptAvailability', 'acceptSubmitState', 'canUnaccept', 'acceptOffline',
   'acceptTargetScreen', 'acceptLoadReason', 'dialogEscapeAllowed', 'queueItemAccepted', 'queueItemsAccepted',
   'panelEscapeAction', 'dialogTabTarget', 'acceptDoneMessage', 'acceptClosedOutcome', 'projectSummary',
   'acceptScreenBody', 'unacceptBody', 'classifyAcceptResult', 'slugList', 'acceptedLine',
   'threadViewOf', 'createViewFields', 'serverSupportsViews', 'createViewDecision', 'normalizeViewFilter', 'threadMatchesView',
   'effectiveView', 'viewFrameWidth', 'parseViewMessage', 'viewMessageOriginAllowed', 'viewTargetOrigin', 'viewFrameThreads',
)


class EngineV2ExportContractTests(unittest.TestCase):
    """Zbiór nazw eksportów (dokładnie 106) oraz stałe i czyste funkcje, które nie mają testu w innych klasach."""

    @classmethod
    def setUpClass(cls):
        cls.node, cls.reason = find_node()
        cls.tmp = tempfile.mkdtemp(prefix='skillsync-engine-v2-api-')
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.engine_js = install_engine_v2(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def many(self, name, arg_lists):
        if not self.node:
            self.skipTest(self.reason)
        input_path, output_path = Path(self.tmp) / 'x-in.json', Path(self.tmp) / 'x-out.json'
        input_path.write_text(json.dumps({'calls': [{'fn': name, 'args': args} for args in arg_lists]}), encoding='utf-8')
        if output_path.exists():
            output_path.unlink()
        result = subprocess.run([self.node, str(Path(self.tmp) / 'queue.cjs'), str(self.engine_js), str(input_path),
                                 str(output_path)], capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError('node queue.cjs exit %s: %s' % (result.returncode, result.stderr[-2000:]))
        return json.loads(output_path.read_text(encoding='utf-8'))

    def one(self, name, *args):
        return self.many(name, [list(args)])[0]

    def js(self, expression):
        """Wartość wyrażenia JS nad `api` (eksporty silnika) jako JSON; `expression` należy do testu, nie do silnika."""
        if not self.node:
            self.skipTest(self.reason)
        script = 'const api=require(process.argv[1]);process.stdout.write(JSON.stringify(%s));' % expression
        result = subprocess.run([self.node, '-e', script, str(self.engine_js)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return json.loads(result.stdout)

    # --- zbiór nazw ---

    def test_export_names_are_exactly_the_contract_set(self):
        self.assertEqual(len(EXPECTED_EXPORTS), 106)
        self.assertEqual(len(set(EXPECTED_EXPORTS)), 106)
        names = self.js('Object.keys(api)')
        self.assertEqual(sorted(names), sorted(EXPECTED_EXPORTS), 'różnica: %s' % sorted(set(names) ^ set(EXPECTED_EXPORTS)))
        kinds = self.js('Object.fromEntries(Object.keys(api).map((k) => [k, typeof api[k] === "function" ? "function" : typeof api[k]]))')
        constants = sorted(name for name, kind in kinds.items() if kind != 'function')
        self.assertEqual(constants, sorted(name for name in EXPECTED_EXPORTS if name == name.upper()), 'stałe to nazwy WIELKIMI literami')
        self.assertEqual(len(constants), 20)

    def test_contract_document_lists_exactly_the_expected_exports(self):
        """Strażnik (bez silnika i przeglądarki): pierwsza kolumna tabeli w docs/prototype-contract/api-eksportow.md = EXPECTED_EXPORTS."""
        text = (Path(__file__).resolve().parents[1] / 'docs' / 'prototype-contract' / 'api-eksportow.md').read_text(encoding='utf-8')
        documented = re.findall(r'^\| `(\w+)` \|', text, re.M)
        missing, extra = sorted(set(EXPECTED_EXPORTS) - set(documented)), sorted(set(documented) - set(EXPECTED_EXPORTS))
        self.assertEqual((missing, extra), ([], []), 'brakuje w dokumencie: %s; nadmiarowe w dokumencie: %s' % (missing, extra))
        self.assertEqual(len(documented), len(set(documented)), 'powtórzona nazwa w tabeli')

    # --- widok wątku (K8, D4) ---

    def test_view_helpers_hold_the_contract(self):
        values = ['mobile', 'desktop', 'tablet', 5, None, 'Mobile', ['mobile'], True]
        self.assertEqual(self.many('threadViewOf', [[{'view': v}] for v in values] + [[{}], [None], ['mobile']]),
                         ['mobile', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop', 'desktop'])
        self.assertEqual(self.many('createViewFields', [['mobile'], ['desktop'], ['tablet'], [None]]), [{'view': 'mobile'}, {}, {}, {}])
        self.assertEqual(self.many('serverSupportsViews', [[{'features': ['views']}], [{'features': ['x', 'views']}], [{'features': []}],
                                                           [{'features': 'views'}], [{}], [None]]),
                         [True, True, False, False, False, False])
        decisions = self.many('createViewDecision', [['mobile', {'features': ['views']}], ['mobile', {'features': []}], ['mobile', None],
                                                     ['desktop', {'features': []}], ['desktop', None]])
        self.assertEqual([d['allowed'] for d in decisions], [True, False, False, True, True])
        self.assertIsNone(decisions[0]['reason'])
        self.assertIsNone(decisions[3]['reason'])
        self.assertTrue(decisions[1]['reason'] and decisions[1]['reason'] == decisions[2]['reason'])
        self.assertEqual(self.many('normalizeViewFilter', [['all'], ['desktop'], ['mobile'], ['tablet'], [None], [5]]),
                         ['all', 'desktop', 'mobile', 'all', 'all', 'all'])
        threads = [{'view': 'mobile'}, {'view': 'desktop'}, {}, {'view': 'tablet'}]
        self.assertEqual([self.one('threadMatchesView', t, 'mobile') for t in threads], [True, False, False, False])
        self.assertEqual([self.one('threadMatchesView', t, 'desktop') for t in threads], [False, True, True, True])
        self.assertEqual([self.one('threadMatchesView', t, 'all') for t in threads], [True, True, True, True])
        self.assertEqual([self.one('threadMatchesView', t, 'x') for t in threads], [True, True, True, True])

    def test_view_filter_in_context_multiplies_with_status_filter(self):
        threads = [{'id': 'a', 'number': 1, 'screen': 's1', 'status': 'open', 'view': 'mobile', 'anchor': None, 'messages': [], 'lastAt': 'x'},
                   {'id': 'b', 'number': 2, 'screen': 's1', 'status': 'open', 'view': 'desktop', 'anchor': None, 'messages': [], 'lastAt': 'x'},
                   {'id': 'c', 'number': 3, 'screen': 's1', 'status': 'closed', 'view': 'mobile', 'anchor': None, 'messages': [], 'lastAt': 'x'}]
        counts_all = self.one('filterCounts', threads, {})
        counts_mobile = self.one('filterCounts', threads, {'viewFilter': 'mobile'})
        self.assertEqual(counts_all['open'], 2)
        self.assertEqual(counts_mobile['open'], 1)
        self.assertEqual(counts_all['open'] - counts_mobile['open'], 1)

    # --- widok mobile: ramka, wiadomości, pochodzenie (B6, D2a) ---

    def test_effective_view_and_frame_width(self):
        # Ekran bez ramki zawsze desktop, niezależnie od lokalnego.
        cases = [[None, False], ['mobile', False], ['desktop', False]]
        self.assertEqual(self.many('effectiveView', cases), ['desktop'] * 3)
        # Z ramką: wygrywa widok lokalny; brak albo niepoprawny lokalny = desktop.
        cases = [[None, True], ['desktop', True], ['mobile', True], ['tablet', True], [5, True], ['MOBILE', True]]
        self.assertEqual(self.many('effectiveView', cases), ['desktop', 'desktop', 'mobile', 'desktop', 'desktop', 'desktop'])
        # Kontrola: ten sam zestaw nie przechodzi dla implementacji ignorującej `hasFrame` albo lokalny widok.
        good = self.many('effectiveView', [['desktop', True], ['mobile', False]])
        with self.assertRaises(AssertionError):
            self.assertEqual(good, ['mobile', 'mobile'])
        # Szerokość: min(375, dostępna), także poniżej 375 i dla wartości niepoprawnych.
        widths = self.many('viewFrameWidth', [[1000], [375], [374], [320], [200], [0], [-5]])
        self.assertEqual(widths, [375, 375, 374, 320, 200, 0, 0])
        self.assertEqual(self.many('viewFrameWidth', [[None], ['x']]), [375, 375])
        with self.assertRaises(AssertionError):
            self.assertEqual(widths, [375, 375, 375, 375, 375, 375, 375])  # stała 375 byłaby błędem

    def test_parse_view_message_allows_only_d2a_types_with_prefix(self):
        to_page = ['ready', 'height', 'pick', 'pin-open', 'navigate', 'escape']
        to_frame = ['state', 'threads', 'focus-return']
        valid = {
            'ready': {}, 'height': {'height': 640},
            'pick': {'anchor': {'kind': 'element', 'protoId': 's1-a', 'x': 0.5, 'y': 0.25}, 'label': 'Przycisk', 'hint': 'Zapisz',
                     'rect': {'left': 1, 'top': 2, 'right': 3, 'bottom': 4}},
            'pin-open': {'threadId': 'th-1'}, 'navigate': {'hash': '#s2'}, 'escape': {},
            'state': {'theme': 'dark', 'flags': {'hotspots': True}, 'commentMode': True, 'thread': 'th-1'},
            'threads': {'screen': 's1', 'threads': []}, 'focus-return': {'protoId': 's1-a'}}
        self.assertEqual(sorted(valid), sorted(to_page + to_frame))
        self.assertEqual(len(valid), 9)
        for direction, names in (('toPage', to_page), ('toFrame', to_frame)):
            parsed = self.many('parseViewMessage', [[dict(valid[n], type='skillsync-proto:' + n), direction] for n in names])
            self.assertEqual([m['type'] if m else None for m in parsed], names, direction)
            other = to_frame if direction == 'toPage' else to_page
            wrong = self.many('parseViewMessage', [[dict(valid[n], type='skillsync-proto:' + n), direction] for n in other])
            self.assertEqual(wrong, [None] * len(other), 'typ z przeciwnego kierunku odrzucony (%s)' % direction)
        junk = [{'type': 'ready'}, {'type': 'other:ready'}, {'type': 'skillsync-proto:'}, {'type': 'skillsync-proto:unknown'},
                {'type': 'skillsync-proto:Ready'}, {'type': 'skillsync-proto:ready '}, {'type': 5}, {}, None, 'skillsync-proto:ready', [],
                {'type': 'skillsync-proto:__proto__'}, {'type': 'skillsync-proto:constructor'}]
        self.assertEqual(self.many('parseViewMessage', [[m, 'toPage'] for m in junk]), [None] * len(junk))
        self.assertEqual(self.many('parseViewMessage', [[{'type': 'skillsync-proto:ready'}, d] for d in ('x', None, 'toPage ')]), [None] * 3)
        # Pola niepoprawne: wiadomość odrzucona zamiast przepuszczona.
        bad = [{'type': 'skillsync-proto:height', 'height': 'x'}, {'type': 'skillsync-proto:height', 'height': -1},
               {'type': 'skillsync-proto:navigate', 'hash': 'http://evil'}, {'type': 'skillsync-proto:pin-open'},
               {'type': 'skillsync-proto:pick', 'anchor': {'kind': 'lost'}, 'rect': {}}]
        self.assertEqual(self.many('parseViewMessage', [[m, 'toPage'] for m in bad]), [None] * len(bad))
        # Kontrola, że asercja o odrzuceniu potrafi paść: poprawna wiadomość nie jest None.
        ok = self.one('parseViewMessage', {'type': 'skillsync-proto:height', 'height': 640}, 'toPage')
        self.assertEqual(ok, {'type': 'height', 'height': 640})
        with self.assertRaises(AssertionError):
            self.assertIsNone(ok)

    def test_view_message_origin_and_target(self):
        own = 'http://localhost:8080'
        allowed = self.many('viewMessageOriginAllowed', [['http:', own, own], ['http:', 'http://evil.test', own], ['http:', 'null', own],
                                                         ['https:', None, 'https://x.test'], ['http:', 5, own], ['http:', own, ''],
                                                         ['http:', '', '']])
        self.assertEqual(allowed, [True, False, False, False, False, False, False])  # puste pochodzenie nigdy nie pasuje
        # file://: pochodzenie jest nieprzejrzyste, więc nie jest sprawdzane; źródło ustala `event.source` (strona silnika).
        file_cases = [['file:', 'null', 'null'], ['file:', 'http://evil.test', 'null'], ['file:', None, 'null']]
        self.assertEqual(self.many('viewMessageOriginAllowed', file_cases), [True, True, True])
        self.assertEqual(self.many('viewTargetOrigin', [['http:', own], ['https:', 'https://x.test'], ['file:', 'null']]),
                         [own, 'https://x.test', '*'])
        # Kontrola: wniosek „http przepuszcza obce pochodzenie” byłby błędny.
        with self.assertRaises(AssertionError):
            self.assertEqual(allowed, [True, True, True, True, True, True])

    def test_view_frame_threads_are_mobile_of_the_screen_without_message_text(self):
        def thread(thread_id, number, screen, view, secret='SEKRET'):
            item = {'id': thread_id, 'number': number, 'screen': screen, 'status': 'open',
                    'anchor': {'kind': 'element', 'protoId': 's1-a', 'x': 0.1, 'y': 0.2, 'iteration': 1},
                    'messages': [{'id': 'm-' + thread_id, 'text': secret, 'by': 'anna@example.com'}], 'label': secret, 'hint': {'text': secret},
                    'by': 'anna@example.com'}
            if view is not None:
                item['view'] = view
            return item
        threads = [thread('a', 1, 's1', 'mobile'), thread('b', 2, 's1', 'desktop'), thread('c', 3, 's1', None), thread('d', 4, 's2', 'mobile'),
                   thread('e', 5, 's1', 'tablet'), dict(thread('f', 6, 's1', 'mobile'), anchor=None), thread('g', 7, 's1', 'mobile')]
        result = self.one('viewFrameThreads', threads, 's1')
        self.assertEqual([t['id'] for t in result], ['a', 'g'])
        self.assertEqual(result[0], {'id': 'a', 'number': 1, 'status': 'open',
                                     'anchor': {'kind': 'element', 'protoId': 's1-a', 'x': 0.1, 'y': 0.2}})
        self.assertNotIn('SEKRET', json.dumps(result), 'wiadomości, etykiety i podpowiedzi nie wychodzą do ramki (D2a)')
        self.assertEqual([t['id'] for t in self.one('viewFrameThreads', threads, 's2')], ['d'])
        self.assertEqual(self.one('viewFrameThreads', threads, 's9'), [])
        self.assertEqual(self.one('viewFrameThreads', None, 's1'), [])
        # Kontrola: asercja o braku treści pada na wątku zwróconym w całości.
        with self.assertRaises(AssertionError):
            self.assertNotIn('SEKRET', json.dumps(threads[:1]))

    # --- stałe ---

    def test_constants_hold_the_contract_values(self):
        values = self.js('Object.fromEntries(["ENGINE_VERSION","FORMAT_NAME","FORMAT_VERSION","API_ROOT","QUEUE_PREFIX","REJECTED_PREFIX",'
                         '"SEEN_PREFIX","V1_COMMENTS_PREFIX","MAX_RESTORE_REASON","MAX_TEXT"].map((k) => [k, api[k]]))')
        self.assertEqual(values, {
            'ENGINE_VERSION': 2, 'FORMAT_NAME': 'skillsync-prototype-comments', 'FORMAT_VERSION': 2, 'API_ROOT': '/_api/v1',
            'QUEUE_PREFIX': 'skillsync-prototype-queue:v2:', 'REJECTED_PREFIX': 'skillsync-prototype-rejected:v2:',
            'SEEN_PREFIX': 'skillsync-prototype-seen:v2:', 'V1_COMMENTS_PREFIX': 'skillsync-prototype-comments:v1:',
            'MAX_RESTORE_REASON': 500, 'MAX_TEXT': 4000})
        self.assertEqual(values['MAX_TEXT'], server.MAX_TEXT)
        # Format dziennika zgadza się z serwerem (nagłówek zapisu i odczytu).
        self.assertEqual((values['FORMAT_NAME'], values['FORMAT_VERSION']), (server.COMMENTS_FORMAT, server.COMMENTS_VERSION))
        # Klucze kolejki, odrzuconych i ostatniej wizyty są rozłączne (inaczej jeden wpis nadpisywałby drugi).
        prefixes = [values[key] for key in ('QUEUE_PREFIX', 'REJECTED_PREFIX', 'SEEN_PREFIX', 'V1_COMMENTS_PREFIX')]
        self.assertEqual(len(set(prefixes)), 4)

    def test_constant_lists_hold_the_contract_values(self):
        lists = self.js('Object.fromEntries(["THREAD_TYPES","OPEN_STATUSES","FILTERS","RESTORE_ROLES","THEME_LOCK_METAS","STATUSES"].map((k) => [k, api[k]]))')
        self.assertEqual(sorted(lists['THREAD_TYPES']), sorted(['create', 'reply', 'edit', 'retract', 'vote', 'resolve', 'reopen',
                                                                 'reanchor', 'delete', 'triage', 'decision']))
        self.assertEqual(len(lists['THREAD_TYPES']), 11)
        self.assertEqual(lists['OPEN_STATUSES'], ['open', 'awaiting', 'decided'])
        self.assertEqual(lists['STATUSES'], list(server.STATUSES))
        self.assertEqual(lists['STATUSES'][:3], lists['OPEN_STATUSES'], 'statusy otwarte to trzy pierwsze')
        self.assertEqual(sorted(lists['FILTERS']), sorted(['open', 'new', 'agent', 'lost', 'all']))
        self.assertEqual(sorted(lists['RESTORE_ROLES']), ['acceptor', 'developer'])
        self.assertEqual(lists['RESTORE_ROLES'], self.js('api.ACCEPT_ROLES'))
        metas = {item['name']: item['content'] for item in lists['THEME_LOCK_METAS']}
        self.assertEqual(metas['color-scheme'], 'light dark')
        self.assertIn('darkreader-lock', metas)
        self.assertEqual(len(metas), 2)
        # Typy wątku i typy prototypu razem dają 15 typów operacji dziennika, bez powtórzeń.
        operation_types = self.js('api.OPERATION_TYPES')
        self.assertEqual(sorted(operation_types), sorted(lists['THREAD_TYPES'] + self.js('api.PROTOTYPE_TYPES')))
        self.assertEqual(sorted(server.OPERATION_TYPES), sorted(operation_types))
        self.assertEqual(sorted(server.THREAD_TYPES), sorted(lists['THREAD_TYPES']))

    # --- dziennik: kształt, normalizacja, kolejność, scalanie ---

    @staticmethod
    def op(op_id, at='2026-11-05T10:00:00.000Z', kind='vote', thread='th-1', **extra):
        value = {'id': op_id, 'type': kind, 'at': at, 'by': 'a@example.com', 'payload': {}}
        if thread is not None:
            value['threadId'] = thread
        value.update(extra)
        return value

    def test_is_operation_shape(self):
        good = self.op('op-1')
        prototype_op = self.op('op-2', kind='accept', thread=None)
        cases = [(good, True), (prototype_op, True),
                 (self.op('op-3', kind='iteration', thread=None), True),
                 (self.op('op-4', thread=None), False),                      # operacja wątku bez threadId
                 (self.op('op-5', kind='nieznany'), False), (self.op('', ), False), (dict(good, id=5), False),
                 (dict(good, at=''), False), ({k: v for k, v in good.items() if k != 'at'}, False),
                 (None, False), ('x', False), ([], False), (5, False)]
        out = self.many('isOperation', [[value] for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        assert_detects(self, lambda value: self.assertEqual(value, [True]), self.many('isOperation', [[good]]), [False])

    def test_normalize_operations_drops_invalid_and_duplicate_ids_first_wins(self):
        first, second = self.op('op-1'), dict(self.op('op-1'), by='b@example.com')
        invalid_then_valid = [{'id': 'op-9', 'type': 'bogus', 'at': 'x'}, self.op('op-9')]
        out = self.one('normalizeOperations', [first, second, self.op('op-2'), None, 5, {}, ] + invalid_then_valid)
        self.assertEqual([item['id'] for item in out], ['op-1', 'op-2', 'op-9'])
        self.assertEqual(out[0]['by'], 'a@example.com', 'przy duplikacie id wygrywa pierwsza operacja')
        self.assertEqual(out[2]['type'], 'vote', 'niepoprawna operacja nie rezerwuje id')
        self.assertEqual(self.many('normalizeOperations', [[None], ['x'], [{}], [[]]]), [[], [], [], []])

    def test_sort_operations_by_at_then_id_in_utf16_units(self):
        a, b = '2026-11-05T10:00:00.000Z', '2026-11-05T10:00:01.000Z'
        unsorted = [self.op('op-c', b), self.op('op-b', a), self.op('op-a', b), self.op('op-a', a)]
        out = self.one('sortOperations', unsorted)
        self.assertEqual([(item['at'], item['id']) for item in out], [(a, 'op-a'), (a, 'op-b'), (b, 'op-a'), (b, 'op-c')])
        # Jednostki UTF-16, nie punkty kodowe: znak spoza BMP (para zastępcza D83D) jest przed U+FF5E.
        tail = self.one('sortOperations', [self.op('op-～', a), self.op('op-\U0001f600', a)])
        self.assertEqual([item['id'] for item in tail], ['op-\U0001f600', 'op-～'])
        self.assertEqual(sorted('op-～ op-\U0001f600'.split(), key=lambda s: s.encode('utf-16-be')), ['op-\U0001f600', 'op-～'])
        self.assertEqual(self.one('sortOperations', []), [])
        # Wejście nie jest modyfikowane (kolejność w tablicy po stronie wywołującego).
        copy_in = [self.op('op-2', b), self.op('op-1', a)]
        self.assertEqual([item['id'] for item in self.many('sortOperations', [[copy_in]])[0]], ['op-1', 'op-2'])

    def test_merge_operations_is_union_by_id_with_first_list_winning(self):
        at = '2026-11-05T10:00:0%d.000Z'
        mine = [self.op('op-1', at % 1), self.op('op-2', at % 2, by='a@example.com')]
        theirs = [self.op('op-2', at % 2, by='inny@example.com'), self.op('op-3', at % 3)]
        out = self.one('mergeOperations', mine, theirs)
        self.assertEqual(sorted(item['id'] for item in out), ['op-1', 'op-2', 'op-3'])
        self.assertEqual([item['by'] for item in out if item['id'] == 'op-2'], ['a@example.com'], 'duplikat id: wersja z pierwszej listy')
        self.assertEqual(self.one('mergeOperations', None, theirs)[0]['id'], 'op-2')
        self.assertEqual(self.one('mergeOperations', mine, None), mine)
        self.assertEqual(self.one('mergeOperations', [], []), [])
        # Kolejność wyjścia = (at, id), także gdy wejście jest ułożone inaczej: późniejsza operacja z `a`, wcześniejsza z `b`.
        late, early = self.op('op-5', at % 5), self.op('op-4', at % 4)
        merged = self.one('mergeOperations', [late], [early])
        self.assertEqual([item['id'] for item in merged], ['op-4', 'op-5'])
        self.assertEqual(merged, self.one('sortOperations', merged))
        self.assertEqual(out, self.one('sortOperations', out))
        # Operacje niepoprawne z drugiej listy nie wchodzą do wyniku.
        self.assertEqual([item['id'] for item in self.one('mergeOperations', mine, [{'id': 'op-9'}, None])], ['op-1', 'op-2'])

    def test_canonical_operation_key_order_and_content(self):
        shuffled = {'payload': {'z': 1}, 'zz': 1, 'by': 'a@example.com', 'at': 't', 'id': 'i', 'type': 'vote', 'cid': 'c', 'threadId': 'T', 'aa': 2}
        out = self.one('canonicalOperation', shuffled)
        self.assertEqual(list(out)[:7], ['id', 'type', 'threadId', 'at', 'by', 'cid', 'payload'])
        self.assertEqual(sorted(list(out)[7:]), ['aa', 'zz'], 'pozostałe klucze po kluczach znanych')
        self.assertEqual(out, shuffled, 'zawartość bez zmian, zmienia się tylko kolejność')
        short = self.one('canonicalOperation', {'type': 'accept', 'id': 'i', 'at': 't'})
        self.assertEqual(list(short), ['id', 'type', 'at'])
        assert_detects(self, lambda value: self.assertEqual(list(value)[:2], ['id', 'type']), out, shuffled)

    # --- status, głosy, kotwica ---

    def test_is_open_status(self):
        cases = [('open', True), ('awaiting', True), ('decided', True), ('closed', False), ('deferred', False),
                 ('bogus', False), ('', False), (None, False), (5, False), ('Open', False)]
        out = self.many('isOpenStatus', [[value] for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    def test_vote_summary_lists_voters_sorted_and_ignores_other_values(self):
        message = {'votes': {'z@example.com': 'up', 'a@example.com': 'up', 'm@example.com': 'down', 'q@example.com': 'none', 'r@example.com': 'bogus'}}
        out = self.one('voteSummary', message)
        self.assertEqual(out, {'up': ['a@example.com', 'z@example.com'], 'down': ['m@example.com']})
        self.assertEqual((len(out['up']), len(out['down'])), (2, 1), 'liczby 👍/👎 to długości list')
        self.assertEqual(self.many('voteSummary', [[{}], [{'votes': None}], [{'votes': {}}]]), [{'up': [], 'down': []}] * 3)

    def test_anchor_kind(self):
        cases = [({'kind': 'element', 'protoId': 's1-a'}, 'element'), ({'kind': 'element'}, 'screen'),
                 ({'kind': 'element', 'protoId': ''}, 'screen'), ({'kind': 'lost'}, 'lost'), ({'kind': 'screen'}, 'screen'),
                 ({'kind': 'nieznany'}, 'screen'), (None, 'screen'), ('x', 'screen'), ([], 'screen')]
        out = self.many('anchorKind', [[value] for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])

    # --- osoby i awatary ---

    def test_person_from_login_and_name_override(self):
        anna = self.one('personFromLogin', 'anna.lis@example.com')
        self.assertEqual((anna['firstName'], anna['surname'], anna['fullName']), ('Anna', 'Lis', 'Anna Lis'))
        self.assertEqual(anna['login'], 'anna.lis@example.com')
        named = self.one('personFromLogin', 'pk@example.com', 'Piotr  Kowalczyk-Nowak')
        self.assertEqual((named['firstName'], named['fullName']), ('Piotr', 'Piotr Kowalczyk-Nowak'))
        self.assertEqual(self.one('personFromLogin', 'jan@example.com')['surname'], '')
        blank_name = self.one('personFromLogin', 'jan.kos@example.com', '   ')
        self.assertEqual(blank_name['firstName'], 'Jan', 'puste pole name: imię z loginu')
        # Zgodność z etykietą: firstName osoby to etykieta bez kolizji (computeLabels).
        label = self.one('computeLabels', ['anna.lis@example.com'], {})
        self.assertEqual(label['anna.lis@example.com']['label'], anna['firstName'])

    def test_initials_for_person(self):
        self.assertEqual(self.one('initialsFor', {'firstName': 'Anna', 'surname': 'Lis'}), 'AL')
        self.assertEqual(self.one('initialsFor', {'firstName': 'żaneta', 'surname': 'łuk'}), 'ŻŁ', 'wielkie litery, także polskie')
        single = self.one('initialsFor', {'firstName': 'Anna'})
        self.assertTrue(single.startswith('A') and single.isupper() and 1 <= len(single) <= 2, single)
        for empty in (None, {}, {'firstName': ''}):
            fallback = self.one('initialsFor', empty)
            self.assertTrue(isinstance(fallback, str) and fallback, 'zawsze niepusty napis dla awatara')

    def test_avatar_tone_is_deterministic_in_range_and_varies_by_login(self):
        logins = ['%s@example.com' % name for name in ('anna', 'jan', 'ewa', 'pawel', 'zofia', 'adam', 'ola', 'kuba', 'maria', 'piotr',
                                                  'celina', 'dawid', 'bartek', 'marta', 'iga', 'olek', 'nina', 'rafal', 'sara', 'tomek')]
        first = self.many('avatarTone', [[login] for login in logins])
        second = self.many('avatarTone', [[login] for login in logins])
        self.assertEqual(first, second, 'ten sam login = ten sam odcień')
        self.assertTrue(all(isinstance(tone, int) and not isinstance(tone, bool) and tone >= 0 for tone in first), first)
        self.assertGreater(len(set(first)), 1, 'różne loginy dają różne odcienie')
        self.assertLess(max(first), 64, 'odcień to mały indeks tonu, nie skrót')
        self.assertEqual(self.many('avatarTone', [[None], ['']])[0], self.one('avatarTone', ''), 'brak loginu jak pusty login')
        assert_detects(self, lambda tones: self.assertGreater(len(set(tones)), 1), first, [first[0]] * len(first))

    # --- blokada Dark Readera, wyszukiwanie, iteracje ---

    def test_has_dark_reader_lock(self):
        script = ("const api=require(process.argv[1]);"
                  "const doc=(metas)=>({head:{querySelector:(sel)=>{const m=/^meta\\[name=\"([^\"]+)\"\\]$/.exec(sel);"
                  "return m&&metas.indexOf(m[1])!==-1?{}:null;}}});"
                  "process.stdout.write(JSON.stringify([api.hasDarkReaderLock(doc(['darkreader-lock'])),"
                  "api.hasDarkReaderLock(doc(['color-scheme'])),api.hasDarkReaderLock(doc([])),"
                  "api.hasDarkReaderLock({}),api.hasDarkReaderLock(null),api.hasDarkReaderLock({head:{}})]));")
        if not self.node:
            self.skipTest(self.reason)
        result = subprocess.run([self.node, '-e', script, str(self.engine_js)], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertEqual(json.loads(result.stdout), [True, False, False, False, False, False])

    def test_normalize_search_ignores_case_diacritics_and_extra_spaces(self):
        cases = [('  ŁĄCZENIE   Grup ', 'laczenie grup'), ('Święta', 'swieta'), ('Tydzień sali', 'tydzien sali'), ('ZAŻÓŁĆ', 'zazolc'),
                 ('plain', 'plain'), ('', ''), (None, ''), ('   ', '')]
        out = self.many('normalizeSearch', [[value] for value, _ in cases])
        self.assertEqual(out, [expected for _, expected in cases])
        self.assertNotEqual(self.one('normalizeSearch', 'sala'), self.one('normalizeSearch', 'sali'))

    def test_iteration_path_and_unchanged_text(self):
        self.assertEqual(self.many('iterationPath', [[1], [12]]), ['iterations/1/', 'iterations/12/'])
        text = self.one('restoreUnchangedText', 4)
        self.assertEqual(text, 'Ekran w iteracji 4 jest taki sam jak bieżący.')
        self.assertNotEqual(text, self.one('restoreUnchangedText', 5))
        self.assertIn('taki sam', self.one('restoreUnchangedText', None), 'brak numeru nie wywraca komunikatu')


if __name__ == '__main__':
    unittest.main()
