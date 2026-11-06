"""skillsync retro-scan (R2, kroki 1.1t): K1 (wejście `--signals`, BLOCKED) i K2 (algorytm progu i stanu z
`retro-quality.md`, kroki (a)-(f), rejestr jako źródło stanu, `merge-base`, kolejność, przebiegi nieczytelne, limity,
kształt wyniku). Fixture'y w katalogu tymczasowym poza repozytorium; wynik czytany z prawdziwego CLI (podproces)."""
import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

from test_skillsync_workflow import CLI, WorkflowFixture
from layers_helpers import commit_file, git, needs_fifo, needs_os_symlinks, needs_posix

LIB = 'skills/feature/references/x.md'
PRJ = 'docs/workflow.md'
SHA_A = 'a' * 40
SHA_UNKNOWN = '1234567890abcdef1234567890abcdef12345678'
RESULT_KEYS = {'schema', 'status', 'run_id', 'worktree', 'cross_project', 'signals', 'groups', 'sources',
               'truncated', 'warnings'}
SIGNAL_KEYS = {'id', 'repeat_of', 'cross_project_repeats', 'attached_to', 'group'}
GROUP_KEYS = {'id', 'repo', 'target', 'signals', 'previously_rejected', 'recurrence_of'}
SOURCES_KEYS = {'runs', 'unreadable', 'registry', 'main_checkout', 'projects'}
UNRESOLVED_STATES = ('open', 'revising', 'approved', 'deferred', 'handoff', 'failed', 'stale')
MB = 1024 * 1024


def sig(ident, key='flaky', repo='library', target=LIB, from_failure=False, **extra):
    return {'id': ident, 'key': key, 'repo': repo, 'target': target, 'from_failure': from_failure, **extra}


def tree_state(root):
    """Pełny stan drzewa: rodzaj, rozmiar, mtime i skrót treści każdej pozycji (bez podążania za dowiązaniami)."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [''] + dirnames + filenames:
            path = os.path.join(dirpath, name) if name else dirpath
            st = os.lstat(path)
            digest = None
            if stat.S_ISREG(st.st_mode):
                digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            result[os.path.relpath(path, root)] = (stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns, digest)
    return result


class ScanBase(WorkflowFixture):
    run_id = 'cur'

    def setUp(self):
        super().setUp()
        self.n_inputs = 0

    # ---- zapis fixture'ów ----
    def rel(self, run_id=None, name='retro-scan-input.json'):
        return f'.skillsync/runs/{run_id or self.run_id}/{name}'

    def put_input(self, signals, run_id=None, name='retro-scan-input.json', top=None, raw=None):
        """Plik wejściowy skanu w katalogu przebiegu; zwraca ścieżkę względną do korzenia projektu."""
        run_id = run_id or self.run_id
        data = {'schema': 1, 'run_id': run_id, 'signals': signals, **(top or {})}
        path = self.root / self.rel(run_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
        else:
            path.write_text(json.dumps(data), encoding='utf-8')
        return self.rel(run_id, name)

    def past(self, name, signals=(), proposals=(), decisions=None, created='2026-11-01T10:00:00Z', retro_decisions=True):
        """Wcześniejszy przebieg z `retro-signals.json` i `retro-decisions.json`.

        signals: (key, repo, target); proposals: (repo, target, state[, ref]) jako P1, P2, ...; `state` None = brak wpisu
        decyzji. `decisions` (lista (P<n>, state[, ref])) nadpisuje wpisy wyliczone z `proposals`."""
        folder = self.root / '.skillsync/runs' / name
        folder.mkdir(parents=True, exist_ok=True)
        listed = [{'id': f'S{i}', 'key': s[0], 'repo': s[1], 'target': s[2]} for i, s in enumerate(signals, 1)]
        props = [{'id': f'P{i}', 'signals': [], 'repo': p[0], 'target': p[1]} for i, p in enumerate(proposals, 1)]
        (folder / 'retro-signals.json').write_text(json.dumps(
            {'schema': 1, 'run_id': name, 'created_at': created, 'signals': listed, 'proposals': props}))
        if decisions is None:
            decisions = [(f'P{i}',) + tuple(p[2:]) for i, p in enumerate(proposals, 1) if p[2] is not None]
        if retro_decisions:
            entries = [{'proposal': d[0], 'state': d[1], 'date': '2026-11-01', 'diff': None,
                        'ref': d[2] if len(d) > 2 else None, 'note': None} for d in decisions]
            (folder / 'retro-decisions.json').write_text(json.dumps(
                {'schema': 1, 'run_id': name, 'decisions': entries}))
        return folder

    def registry(self, proposals=None, decisions=None, raw=None):
        data = {'version': 1, 'decisions': decisions or []}
        if proposals is not None:
            data['proposals'] = proposals
        self.write('docs/retro/decisions.yaml', raw if raw is not None else yaml.safe_dump(data, sort_keys=False))

    @staticmethod
    def entry(proposal, decision='rejected', repo='library', target=LIB, date='2026-11-05', ref=None):
        item = {'proposal': proposal, 'repo': repo, 'target': target, 'decision': decision, 'date': date}
        if ref is not None:
            item['ref'] = ref
        return item

    def keys_file(self, keys):
        (self.library / 'workflow').mkdir(parents=True, exist_ok=True)
        (self.library / 'workflow/cause-keys.yaml').write_text(yaml.safe_dump(
            {'version': 1, 'keys': {k: {'description': 'opis ' + k, 'aliases': list(v)} for k, v in keys.items()}}),
            encoding='utf-8')

    # ---- git ----
    def init_repo(self, path, target=None):
        """Repozytorium git z jednym commitem; zwraca sha. `target`: plik tworzony w repozytorium."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        git(path, 'init', '-q', '-b', 'main')
        for name, value in (('user.email', 'dev@example.test'), ('user.name', 'Dev'), ('commit.gpgsign', 'false')):
            git(path, 'config', name, value)
        return commit_file(path, target or 'seed.txt', 'one\n', 'one')

    def repo_with_history(self, path, target=LIB):
        """(sha w historii HEAD, sha commitu spoza historii HEAD, sha HEAD)."""
        first = self.init_repo(path, target)
        git(path, 'checkout', '-q', '-b', 'side')
        outside = commit_file(path, 'side.txt', 'side\n', 'side')
        git(path, 'checkout', '-q', 'main')
        head = commit_file(path, 'second.txt', 'two\n', 'two')
        return first, outside, head

    # ---- uruchomienie ----
    def run_scan(self, rel=None, run_id=None, extra=(), cwd=None, env=None, timeout=30, with_signals=True,
                 with_run_id=True):
        command = [sys.executable, str(CLI), 'retro-scan', str(self.root)]
        if with_run_id:
            command += ['--run-id', self.run_id if run_id is None else run_id]
        if with_signals:
            command += ['--signals', rel if rel is not None else self.rel(run_id)]
        command += [*map(str, extra), '--json']
        full_env = {**os.environ, **(env or {})}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                    timeout=timeout, cwd=cwd or self.root, env=full_env)
        except subprocess.TimeoutExpired:
            self.fail('retro-scan zawisł (limit ' + str(timeout) + ' s)')
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result

    def parsed(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except ValueError:
            self.fail('stdout nie jest JSON-em: ' + (result.stdout + result.stderr)[:300])

    def scan(self, signals=None, code=0, rel=None, **kw):
        """Zapisuje wejście (gdy podano `signals`), uruchamia skan i sprawdza kod oraz kształt wyniku."""
        if signals is not None:
            rel = self.put_input(signals, kw.get('run_id'))
        data = self.parsed(self.run_scan(rel, **kw), code)
        if code == 0:
            self.check_shape(data)
        return data

    def blocked(self, signals=None, rel=None, **kw):
        data = self.scan(signals, code=2, rel=rel, **kw)
        self.assertEqual(set(data), {'schema', 'status', 'reason'}, data)
        self.assertEqual((data['schema'], data['status']), (1, 'BLOCKED'))
        self.assertTrue(isinstance(data['reason'], str) and data['reason'].strip(), data)
        return data

    def check_shape(self, data):
        self.assertEqual(set(data), RESULT_KEYS)
        self.assertEqual((data['schema'], data['status']), (1, 'PASS'))
        for item in data['signals']:
            self.assertEqual(set(item), SIGNAL_KEYS)
        for item in data['groups']:
            self.assertEqual(set(item), GROUP_KEYS)
        self.assertEqual(set(data['sources']), SOURCES_KEYS)

    def one(self, data, ident='S1'):
        return next(item for item in data['signals'] if item['id'] == ident)

    @contextlib.contextmanager
    def at(self, root):
        """Chwilowo przełącza `self.root` (zapis przebiegów i rejestrów do innego projektu albo checkoutu)."""
        previous, self.root = self.root, Path(root)
        try:
            yield
        finally:
            self.root = previous

    def in_process(self, signals, patches=(), run_id=None, extra=()):
        """Skan w tym samym procesie (z podmienionymi stałymi): (kod, wynik)."""
        rel = self.put_input(signals, run_id)
        parser = argparse.ArgumentParser()
        from skillsync_workflow import retro_scan
        retro_scan.register(parser.add_subparsers())
        args = parser.parse_args(['retro-scan', str(self.root), '--run-id', run_id or self.run_id,
                                  '--signals', rel, *extra, '--json'])
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out):
                code = args.func(args)
        data = json.loads(out.getvalue())
        return code, data


# ===================================================================================================================
# K1: wejście
# ===================================================================================================================
class InputAccepted(ScanBase):
    def test_valid_file_passes(self):
        data = self.scan([sig('S1', 'user-correction'), sig('S2', 'flaky', repo=None, target=None)])
        self.assertEqual([item['id'] for item in data['signals']], ['S1', 'S2'])
        self.assertEqual(data['run_id'], 'cur')

    def test_signals_path_is_relative_to_project_root_not_cwd(self):
        rel = self.put_input([sig('S1', 'user-correction')])
        data = self.parsed(self.run_scan(rel, cwd=self.base), 0)
        self.assertEqual(data['groups'][0]['signals'], ['S1'])

    def test_empty_signal_list_passes(self):
        data = self.scan([])
        self.assertEqual((data['signals'], data['groups']), ([], []))

    def test_target_null_with_repo_set_passes(self):
        data = self.scan([sig('S1', 'user-correction', repo='library', target=None)])
        self.assertIsNone(self.one(data)['group'])

    def test_extra_fields_are_ignored(self):
        plain = self.scan([sig('S1', 'user-correction')])
        rel = self.put_input([sig('S1', 'user-correction', summary='parafraza', source='plan', nested={'a': [1]})],
                             top={'created_at': '2026-11-05T10:00:00Z', 'assessor': 'reviewer', 'proposals': []})
        extra = self.parsed(self.run_scan(rel), 0)
        self.assertEqual(extra, plain)

    def test_exactly_256_kb_passes(self):
        base = json.dumps({'schema': 1, 'run_id': 'cur', 'signals': [sig('S1', 'user-correction')]})
        raw = base + ' ' * (256 * 1024 - len(base.encode()))
        self.assertEqual(len(raw.encode()), 256 * 1024)
        rel = self.put_input(None, raw=raw)
        self.assertEqual(self.parsed(self.run_scan(rel), 0)['groups'][0]['signals'], ['S1'])

    def test_one_byte_over_256_kb_is_blocked(self):
        base = json.dumps({'schema': 1, 'run_id': 'cur', 'signals': [sig('S1', 'user-correction')]})
        raw = base + ' ' * (256 * 1024 + 1 - len(base.encode()))
        rel = self.put_input(None, raw=raw)
        self.blocked(rel=rel)

    def test_command_writes_nothing(self):
        first, outside, head = self.repo_with_history(self.library)
        self.registry([self.entry('old/P1', 'applied', ref=first)])
        self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        rel = self.put_input([sig('S1', 'flaky'), sig('S2', 'user-correction', target=PRJ, repo='project')])
        before = tree_state(self.base)
        self.parsed(self.run_scan(rel), 0)
        self.assertEqual(tree_state(self.base), before)

    def test_blocked_command_writes_nothing(self):
        rel = self.put_input([sig('S1', 'flaky', from_failure='no')])
        before = tree_state(self.base)
        self.blocked(rel=rel)
        self.assertEqual(tree_state(self.base), before)


class InputRefused(ScanBase):
    def test_location_outside_run_directory(self):
        other = self.put_input([sig('S1')], run_id='other')  # poprawna treść, ale inny run-id w ścieżce
        self.write('docs/in.json', json.dumps({'schema': 1, 'run_id': 'cur', 'signals': []}))
        (self.base / 'escape.json').write_text(json.dumps({'schema': 1, 'run_id': 'cur', 'signals': []}))
        self.put_input([], run_id='cur')
        cases = {
            'inny run-id': other,
            'poza .skillsync/runs': 'docs/in.json',
            'kropki wychodzące na zewnątrz': '../escape.json',
            'kropki do innego przebiegu': '.skillsync/runs/cur/../other/retro-scan-input.json',
            'bezwzględna': str(self.root / self.rel()),
            'sam katalog przebiegu': '.skillsync/runs/cur',
            'plik w magazynie poza katalogiem': '.skillsync/runs/cur.json',
            'plik nie istnieje': '.skillsync/runs/cur/nie-ma.json',
            'pusta': '',
        }
        for label, rel in cases.items():
            with self.subTest(label):
                self.blocked(rel=rel)

    def test_other_run_id_is_blocked_even_when_file_run_id_matches_flag(self):
        # plik leży w katalogu `other`, run_id w pliku i flaga to `other`, ale polecenie dostaje --run-id other:
        # to poprawny przypadek; z --run-id cur ten sam plik ma być odrzucony przez położenie
        rel = self.put_input([sig('S1')], run_id='other')
        self.assertEqual(self.parsed(self.run_scan(rel, run_id='other'), 0)['run_id'], 'other')
        self.blocked(rel=rel, run_id='cur')

    @needs_os_symlinks
    def test_symlink_to_file_in_allowed_directory(self):
        real = self.put_input([sig('S1')], name='real.json')
        os.symlink(self.root / real, self.root / self.rel())
        self.blocked(rel=self.rel())
        # odwrotnie: ten sam plik bez dowiązania przechodzi
        self.assertEqual(self.parsed(self.run_scan(real), 0)['status'], 'PASS')

    @needs_os_symlinks
    def test_symlink_to_file_outside_the_directory(self):
        outside = self.base / 'outside.json'
        outside.write_text(json.dumps({'schema': 1, 'run_id': 'cur', 'signals': []}))
        folder = self.root / '.skillsync/runs/cur'
        folder.mkdir(parents=True)
        os.symlink(outside, folder / 'retro-scan-input.json')
        self.blocked(rel=self.rel())

    @needs_os_symlinks
    def test_run_directory_symlinked_elsewhere(self):
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        (elsewhere / 'retro-scan-input.json').write_text(json.dumps({'schema': 1, 'run_id': 'cur', 'signals': []}))
        runs = self.root / '.skillsync/runs'
        runs.mkdir(parents=True)
        os.symlink(elsewhere, runs / 'cur')
        self.blocked(rel=self.rel())

    def test_directory_instead_of_file(self):
        (self.root / self.rel()).mkdir(parents=True)
        self.blocked(rel=self.rel())

    @needs_fifo
    def test_fifo_does_not_hang(self):
        path = self.root / self.rel()
        path.parent.mkdir(parents=True)
        os.mkfifo(path)
        self.blocked(rel=self.rel(), timeout=15)

    def test_bad_json_forms(self):
        forms = {'urwany': '{"schema": 1,', 'pusty': '', 'tablica': '[]', 'liczba': '1', 'NaN': '{"schema": NaN}',
                 'nie-utf8': b'\xff\xfe{"schema": 1}', 'tekst': 'to nie json'}
        for label, raw in forms.items():
            with self.subTest(label):
                self.blocked(rel=self.put_input(None, raw=raw))

    def test_run_id_must_equal_the_flag(self):
        rel = self.put_input([sig('S1')], top={'run_id': 'inny'})
        self.blocked(rel=rel)
        # odwrotnie: zgodny run_id przechodzi
        self.assertEqual(self.parsed(self.run_scan(self.put_input([sig('S1')])), 0)['status'], 'PASS')

    def test_missing_flags_are_blocked(self):
        self.put_input([])
        self.blocked(with_signals=False)
        self.blocked(with_run_id=False)

    def test_run_id_with_path_separators_is_blocked(self):
        self.put_input([])
        for bad in ('../x', 'a/b', '', 'a b'):
            with self.subTest(bad):
                self.blocked(rel=self.rel(), run_id=bad)

    def test_missing_top_level_fields(self):
        for field in ('schema', 'run_id', 'signals'):
            with self.subTest(field):
                data = {'schema': 1, 'run_id': 'cur', 'signals': []}
                del data[field]
                self.blocked(rel=self.put_input(None, raw=json.dumps(data)))

    def test_bad_top_level_values(self):
        cases = {'schema 2': {'schema': 2}, 'schema tekst': {'schema': '1'}, 'schema bool': {'schema': True},
                 'schema null': {'schema': None}, 'signals nie lista': {'signals': {}}, 'signals null': {'signals': None},
                 'sygnał nie obiekt': {'signals': ['S1']}}
        for label, patch in cases.items():
            with self.subTest(label):
                data = {'schema': 1, 'run_id': 'cur', 'signals': [], **patch}
                self.blocked(rel=self.put_input(None, raw=json.dumps(data)))

    def test_each_required_signal_field_is_required(self):
        for field in ('id', 'key', 'repo', 'target', 'from_failure'):
            with self.subTest(field):
                item = sig('S1', 'user-correction')
                del item[field]
                self.blocked([item])
            if field in ('repo', 'target'):  # brak pola to nie to samo co `null`
                with self.subTest(field + ' przy sygnale bez celu'):
                    item = sig('S1', 'user-correction', repo=None, target=None)
                    del item[field]
                    self.blocked([item])

    def test_bad_signal_values(self):
        cases = {
            'id bez S': dict(id='X1'), 'id bez liczby': dict(id='S'), 'id z sufiksem': dict(id='S1x'),
            'id liczba': dict(id=1), 'id null': dict(id=None), 'id małe s': dict(id='s1'),
            'key wielka litera': dict(key='Flaky'), 'key spacja': dict(key='a b'), 'key pusty': dict(key=''),
            'key podkreślenie': dict(key='a_b'), 'key liczba': dict(key=1), 'key null': dict(key=None),
            'key za długi': dict(key='a' * 49), 'key z ukośnikiem': dict(key='a/b'),
            'repo spoza listy': dict(repo='other'), 'repo wielka litera': dict(repo='Library'),
            'repo liczba': dict(repo=1),
            'repo null z celem': dict(repo=None, target=LIB),
            'target z kropkami': dict(target='skills/../x.md'), 'target zaczyna się od kropek': dict(target='../x.md'),
            'target z wiodącym ukośnikiem': dict(target='/etc/x.md'), 'target z backslashem': dict(target='a\\b.md'),
            'target pusty': dict(target=''), 'target liczba': dict(target=1),
            'from_failure tekst': dict(from_failure='false'), 'from_failure liczba 0': dict(from_failure=0),
            'from_failure liczba 1': dict(from_failure=1), 'from_failure null': dict(from_failure=None),
        }
        for label, patch in cases.items():
            with self.subTest(label):
                self.blocked([{**sig('S1', 'user-correction'), **patch}])

    def test_duplicated_signal_id_is_blocked(self):
        self.blocked([sig('S1'), sig('S2'), sig('S1')])
        # odwrotnie: różne id przechodzą
        self.assertEqual(self.scan([sig('S1'), sig('S2')])['status'], 'PASS')

    def test_one_bad_signal_blocks_the_whole_scan(self):
        self.blocked([sig('S1', 'user-correction'), sig('S2', repo='nope')])


class CauseKeys(ScanBase):
    def test_unknown_key_warns_but_signal_is_counted(self):
        self.keys_file({'user-correction': [], 'flaky': ['flaky-old']})
        self.past('older', [('brand-new', 'library', LIB)])
        data = self.scan([sig('S1', 'user-correction'), sig('S2', 'brand-new', target=PRJ, repo='project'),
                          sig('S3', 'brand-new')])
        self.assertEqual(data['warnings'], ['unknown-key:S2', 'unknown-key:S3'])
        self.assertEqual(self.one(data, 'S3')['repeat_of'], 'older/S1')  # sygnał nadal liczony (krok f)
        self.assertEqual(self.one(data, 'S3')['group'], 'G1')  # ta sama para co S1
        self.assertEqual(self.one(data, 'S1')['group'], 'G1')
        self.assertIsNone(self.one(data, 'S2')['group'])

    def test_alias_resolves_to_listed_key_without_warning(self):
        self.keys_file({'user-correction': [], 'flaky': ['flaky-old']})
        data = self.scan([sig('S1', 'flaky-old'), sig('S2', 'flaky')])
        self.assertEqual(data['warnings'], [])

    def test_alias_and_canonical_key_count_as_a_repeat(self):
        self.keys_file({'flaky': ['flaky-old']})
        self.past('older', [('flaky-old', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.one(data)['repeat_of'], 'older/S1')
        self.assertEqual(self.one(data)['group'], 'G1')

    def test_alias_in_input_matches_canonical_key_in_the_past(self):
        self.keys_file({'flaky': ['flaky-old']})
        self.past('older', [('flaky', 'library', LIB)])
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky-old')]))['repeat_of'], 'older/S1')

    def test_alias_of_user_correction_triggers_step_e(self):
        self.keys_file({'user-correction': ['corrected-by-user']})
        data = self.scan([sig('S1', 'corrected-by-user')])
        self.assertEqual(self.one(data)['group'], 'G1')

    def test_without_the_list_warning_no_cause_keys(self):
        data = self.scan([sig('S1', 'whatever-key')])
        self.assertEqual(data['warnings'], ['no-cause-keys'])

    def test_with_the_list_no_cause_keys_warning_is_absent(self):
        self.keys_file({'flaky': []})
        self.assertNotIn('no-cause-keys', self.scan([sig('S1', 'flaky')])['warnings'])


# ===================================================================================================================
# K2: algorytm, kroki (a)-(f)
# ===================================================================================================================
class StepA(ScanBase):
    def test_from_failure_gives_no_proposal(self):
        data = self.scan([sig('S1', 'user-correction', from_failure=True)])
        self.assertEqual((self.one(data)['group'], data['groups']), (None, []))

    def test_same_signal_without_from_failure_gives_a_group(self):
        self.assertEqual(self.one(self.scan([sig('S1', 'user-correction')]))['group'], 'G1')

    def test_target_null_gives_no_proposal(self):
        data = self.scan([sig('S1', 'user-correction', repo=None, target=None)])
        self.assertEqual((self.one(data)['group'], data['groups']), (None, []))

    def test_target_null_is_never_a_repeat(self):
        self.past('older', [('flaky', None, None)])
        self.assertIsNone(self.one(self.scan([sig('S1', 'flaky', repo=None, target=None)]))['repeat_of'])

    def test_a_precedes_b(self):
        self.past('older', [], [('library', LIB, 'open')])
        data = self.scan([sig('S1', 'user-correction', from_failure=True), sig('S2', 'user-correction')])
        self.assertEqual((self.one(data)['attached_to'], self.one(data)['group']), (None, None))
        self.assertEqual((self.one(data, 'S2')['attached_to'], self.one(data, 'S2')['group']), ('older/P1', None))


class StepB(ScanBase):
    def test_unresolved_proposal_attaches_the_signal(self):
        for state in UNRESOLVED_STATES:
            with self.subTest(state):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.past('older', [], [('library', LIB, state)])
                data = self.scan([sig('S1', 'user-correction')])
                self.assertEqual((self.one(data)['attached_to'], self.one(data)['group']), ('older/P1', None))
                self.assertEqual(data['groups'], [])

    def test_resolved_proposals_do_not_attach(self):
        for state, ref in (('rejected', None), ('applied', SHA_A)):
            with self.subTest(state):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.past('older', [], [('library', LIB, state, ref)])
                self.assertIsNone(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'])

    def test_last_decision_entry_decides_the_state(self):
        self.past('older', [], [('library', LIB, None)], decisions=[('P1', 'open'), ('P1', 'rejected')])
        self.assertIsNone(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'])
        shutil.rmtree(self.root / '.skillsync')
        self.past('older', [], [('library', LIB, None)], decisions=[('P1', 'open'), ('P1', 'approved')])
        self.assertEqual(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'], 'older/P1')

    def test_proposal_without_decision_entry_has_no_state_and_is_skipped(self):
        self.past('older', [], [('library', LIB, None)])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual((self.one(data)['attached_to'], self.one(data)['group']), (None, 'G1'))

    def test_proposal_without_decisions_file_is_skipped(self):
        self.past('older', [], [('library', LIB, 'open')], retro_decisions=False)
        self.assertIsNone(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'])

    def test_other_pair_does_not_attach(self):
        self.past('older', [], [('library', 'skills/other.md', 'open'), ('project', LIB, 'open')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual((self.one(data)['attached_to'], self.one(data)['group']), (None, 'G1'))

    def test_project_pair_attaches_only_for_project_repo(self):
        self.past('older', [], [('project', PRJ, 'open')])
        data = self.scan([sig('S1', 'user-correction', repo='project', target=PRJ),
                          sig('S2', 'user-correction', repo='library', target=PRJ)])
        self.assertEqual(self.one(data)['attached_to'], 'older/P1')
        self.assertIsNone(self.one(data, 'S2')['attached_to'])

    def test_newest_unresolved_proposal_by_created_at_not_by_name(self):
        self.past('zzz-old', [], [('library', LIB, 'open')], created='2026-11-01T10:00:00Z')
        self.past('aaa-new', [], [('library', LIB, 'open')], created='2026-11-03T10:00:00Z')
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'aaa-new/P1')

    def test_newest_unresolved_proposal_flipped(self):
        self.past('zzz-old', [], [('library', LIB, 'open')], created='2026-11-03T10:00:00Z')
        self.past('aaa-new', [], [('library', LIB, 'open')], created='2026-11-01T10:00:00Z')
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'zzz-old/P1')

    def test_created_at_offsets_are_compared_as_instants(self):
        # 2026-11-03T01:00+02:00 = 2026-11-02T23:00Z, czyli wcześniej niż 2026-11-02T23:30Z
        self.past('aaa', [], [('library', LIB, 'open')], created='2026-11-03T01:00:00+02:00')
        self.past('bbb', [], [('library', LIB, 'open')], created='2026-11-02T23:30:00Z')
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'bbb/P1')
        shutil.rmtree(self.root / '.skillsync')
        self.past('aaa', [], [('library', LIB, 'open')], created='2026-11-03T01:00:00+02:00')
        self.past('bbb', [], [('library', LIB, 'open')], created='2026-11-02T22:30:00Z')
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'aaa/P1')

    def test_tie_in_created_at_is_resolved_by_run_id(self):
        # deterministyczne niezależnie od kolejności tworzenia katalogów; zakładamy, że późniejsze id jest „nowsze”
        for order in (('a-run', 'b-run'), ('b-run', 'a-run')):
            shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
            for name in order:
                self.past(name, [], [('library', LIB, 'open')], created='2026-11-02T10:00:00Z')
            self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'b-run/P1', order)

    def test_higher_proposal_number_wins_inside_one_run(self):
        self.past('older', [], [('library', LIB, 'open'), ('library', LIB, 'open')])
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['attached_to'], 'older/P2')


class StepC(ScanBase):
    def test_rejected_suppresses_non_correction_even_with_a_repeat(self):
        self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'rejected')])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.one(data)['repeat_of'], 'older/S1')
        self.assertEqual((self.one(data)['group'], data['groups']), (None, []))

    def test_rejected_does_not_suppress_user_correction(self):
        self.past('older', [], [('library', LIB, 'rejected')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual(self.one(data)['group'], 'G1')
        self.assertEqual(data['groups'][0]['previously_rejected'], 'older/P1')
        self.assertIsNone(data['groups'][0]['recurrence_of'])

    def test_without_rejection_previously_rejected_is_null(self):
        data = self.scan([sig('S1', 'user-correction')])
        self.assertIsNone(data['groups'][0]['previously_rejected'])

    def test_rejected_for_another_pair_does_not_suppress(self):
        self.past('older', [('flaky', 'library', LIB)], [('library', 'skills/other.md', 'rejected')])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.one(data)['group'], 'G1')

    def test_rejected_older_than_applied_does_not_suppress(self):
        # ostatnia propozycja pary to applied (ref w historii) -> krok (d), nie (c)
        first, _, _ = self.repo_with_history(self.library)
        self.past('old-a', [], [('library', LIB, 'rejected')], created='2026-11-01T10:00:00Z')
        self.past('old-b', [], [('library', LIB, 'applied', first)], created='2026-11-02T10:00:00Z')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'old-b/P1')
        self.assertIsNone(data['groups'][0]['previously_rejected'])

    def test_applied_older_than_rejected_suppresses(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('old-a', [], [('library', LIB, 'applied', first)], created='2026-11-01T10:00:00Z')
        self.past('old-b', [], [('library', LIB, 'rejected')], created='2026-11-02T10:00:00Z')
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        self.assertIsNone(self.one(data)['group'])
        self.assertEqual(self.one(data, 'S2')['group'], 'G1')
        self.assertEqual(data['groups'][0]['previously_rejected'], 'old-b/P1')

    def test_tie_decided_by_run_id_between_rejected_and_applied(self):
        first, _, _ = self.repo_with_history(self.library)
        for rejected, applied, expected in (('a-run', 'b-run', 'applied'), ('b-run', 'a-run', 'rejected')):
            shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
            self.past(rejected, [], [('library', LIB, 'rejected')], created='2026-11-02T10:00:00Z')
            self.past(applied, [], [('library', LIB, 'applied', first)], created='2026-11-02T10:00:00Z')
            data = self.scan([sig('S1', 'flaky')])
            self.assertEqual(self.one(data)['group'] is not None, expected == 'applied', (rejected, applied))


class StepD(ScanBase):
    def test_applied_with_ref_in_history_gives_recurrence_without_threshold(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])
        data = self.scan([sig('S1', 'flaky')])  # klucz inny niż user-correction, brak powtórzenia
        self.assertIsNone(self.one(data)['repeat_of'])
        self.assertEqual(self.one(data)['group'], 'G1')
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')
        self.assertEqual(data['warnings'], ['no-cause-keys'])

    def test_applied_ref_equal_to_head_counts_as_in_history(self):
        _, _, head = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', head)])
        self.assertEqual(self.scan([sig('S1', 'flaky')])['groups'][0]['recurrence_of'], 'older/P1')

    def test_ref_outside_history_is_treated_as_without_applied(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', outside)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((self.one(data)['group'], data['groups']), (None, []))
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])

    def test_ref_outside_history_with_correction_gives_group_without_recurrence(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', outside)])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual(self.one(data)['group'], 'G1')
        self.assertIsNone(data['groups'][0]['recurrence_of'])
        self.assertIsNone(data['groups'][0]['previously_rejected'])
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])

    def test_ref_outside_history_with_repeat_gives_group_via_step_f(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'applied', outside)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.one(data)['group'], 'G1')
        self.assertIsNone(data['groups'][0]['recurrence_of'])

    def test_unknown_commit_in_a_working_repo_is_not_in_history(self):
        self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', SHA_UNKNOWN)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])
        self.assertIsNone(self.one(data)['group'])

    def test_warning_is_not_repeated_for_two_signals_of_one_pair(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', outside)])
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        self.assertEqual([w for w in data['warnings'] if w.startswith('ref-')], ['ref-not-in-history:older/P1'])

    def test_applied_without_ref_is_unverified(self):
        self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied')])  # wpis `applied` bez ref
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertIsNone(self.one(data)['group'])

    def test_applied_with_short_ref_is_unverified(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first[:12])])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertIsNone(self.one(data)['group'])

    def test_unverifiable_ref_with_correction_behaves_as_without_applied(self):
        self.past('older', [], [('library', LIB, 'applied')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual(self.one(data)['group'], 'G1')
        self.assertIsNone(data['groups'][0]['recurrence_of'])
        self.assertIn('ref-unverified:older/P1', data['warnings'])

    def test_target_repo_without_git_is_unverified(self):
        # korzeń celu `library` (warstwa z plikiem) nie jest repozytorium git
        (self.library / LIB).parent.mkdir(parents=True, exist_ok=True)
        (self.library / LIB).write_text('x\n')
        self.past('older', [], [('library', LIB, 'applied', SHA_A)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertNotIn('ref-not-in-history:older/P1', data['warnings'])

    def test_project_target_uses_the_project_root(self):
        first, outside, _ = self.repo_with_history(self.root, target=PRJ)
        self.past('older', [], [('project', PRJ, 'applied', first)])
        data = self.scan([sig('S1', 'flaky', repo='project', target=PRJ)])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')
        shutil.rmtree(self.root / '.skillsync')
        self.past('older', [], [('project', PRJ, 'applied', outside)])
        data = self.scan([sig('S1', 'flaky', repo='project', target=PRJ)])
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])

    def test_library_ref_is_not_checked_against_the_project_repo(self):
        # sha jest w historii repozytorium PROJEKTU, ale cel to `library` (inne repozytorium)
        first_project = self.init_repo(self.root)
        self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first_project)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])

    def test_library_with_no_layer_containing_target_is_unverified(self):
        first, _, _ = self.repo_with_history(self.library, target='other/y.md')
        self.past('older', [], [('library', LIB, 'applied', first)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertIsNone(self.one(data)['group'])

    def test_library_with_two_layers_containing_target_is_unverified(self):
        first, _, _ = self.repo_with_history(self.library)
        org = self.base / 'org'
        self.repo_with_history(org)
        self.use_layers(('core', 'core', self.library), ('org', 'org', org))
        self.past('older', [], [('library', LIB, 'applied', first)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertIsNone(self.one(data)['group'])

    def test_library_with_single_providing_non_core_layer_uses_that_layer(self):
        org = self.base / 'org'
        first, _, _ = self.repo_with_history(org)
        self.use_layers(('core', 'core', self.library), ('org', 'org', org))
        self.past('older', [], [('library', LIB, 'applied', first)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')
        self.assertNotIn('ref-unverified:older/P1', data['warnings'])

    @needs_posix
    def test_command_is_merge_base_is_ancestor_ref_head_in_the_target_root(self):
        first, _, _ = self.repo_with_history(self.library)
        log = self.base / 'git-calls.log'
        shim = self.base / 'shim'
        shim.mkdir()
        real = shutil.which('git')
        (shim / 'git').write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n')
        (shim / 'git').chmod(0o755)
        self.past('older', [], [('library', LIB, 'applied', first)])
        rel = self.put_input([sig('S1', 'flaky')])
        data = self.parsed(self.run_scan(rel, env={'PATH': f'{shim}{os.pathsep}{os.environ["PATH"]}'}), 0)
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')
        calls = log.read_text().splitlines()
        self.assertIn(f'-C {self.library.resolve()} merge-base --is-ancestor {first} HEAD',
                      [line.replace(str(self.library), str(self.library.resolve())) for line in calls], calls)

    def test_git_timeout_leaves_the_ref_unverified(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])
        from skillsync_workflow import retro
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro, 'GIT_TIMEOUT', 1e-6)])
        self.assertEqual(code, 0)
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        self.assertEqual(data['groups'], [])
        # odwrotnie: normalny limit potwierdza historię
        code, data = self.in_process([sig('S1', 'flaky')])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')

    def test_git_budget_exceeded_marks_truncated(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])
        from skillsync_workflow import retro
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro, 'GIT_BUDGET', 0)])
        self.assertEqual(code, 0)
        self.assertTrue(data['truncated'])
        self.assertIn('truncated', data['warnings'])
        self.assertIn('ref-unverified:older/P1', data['warnings'])
        code, data = self.in_process([sig('S1', 'flaky')])
        self.assertFalse(data['truncated'])
        self.assertNotIn('truncated', data['warnings'])


    def _foreign_repo(self):
        other = self.base / 'foreign'
        first, _, _ = self.repo_with_history(other, target='foreign-only.md')
        return other, first

    def _git_env_scan(self, other):
        rel = self.put_input([sig('S1', 'flaky')])
        env = {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}
        return self.parsed(self.run_scan(rel, env=env), 0)

    def test_foreign_git_dir_in_environment_does_not_hide_a_missing_ref(self):
        other, foreign_first = self._foreign_repo()
        self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', foreign_first)])  # w historii cudzego repo, nie celu
        data = self._git_env_scan(other)
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])
        self.assertEqual(data['groups'], [])

    def test_foreign_git_dir_in_environment_does_not_hide_a_present_ref(self):
        other, _ = self._foreign_repo()
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])  # w historii celu, nie cudzego repo
        data = self._git_env_scan(other)
        self.assertEqual(data['groups'][0]['recurrence_of'], 'older/P1')
        self.assertFalse([w for w in data['warnings'] if w.startswith('ref-')])

    def _fake_git(self, behaviour):
        real = subprocess.run

        def run(args, *a, **kw):
            if isinstance(args, (list, tuple)) and ('--is-ancestor' in args or '--verify' in args):
                return behaviour(args, kw)
            return real(args, *a, **kw)
        from skillsync_workflow import retro_scan
        return mock.patch.object(retro_scan.subprocess, 'run', run)

    def test_non_utf8_git_output_does_not_break_the_scan(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])

        def behaviour(args, kw):
            if kw.get('text') or kw.get('universal_newlines') or kw.get('encoding') or kw.get('errors'):
                raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')  # ścisłe dekodowanie wyjścia
            return subprocess.CompletedProcess(args, 128, b'fatal: \xff\xfe', b'\xff\xfe')
        code, data = self.in_process([sig('S1', 'flaky')], [self._fake_git(behaviour)])
        self.assertEqual((code, data['status']), (0, 'PASS'))
        self.assertIn('ref-unverified:older/P1', data['warnings'])

    def test_timeout_when_less_budget_than_call_limit_remains_marks_truncated(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])
        from skillsync_workflow import retro

        def behaviour(args, kw):
            raise subprocess.TimeoutExpired(args, kw.get('timeout', 0))
        patches = [self._fake_git(behaviour), mock.patch.object(retro, 'GIT_BUDGET', 2),
                   mock.patch.object(retro, 'GIT_TIMEOUT', 50)]
        code, data = self.in_process([sig('S1', 'flaky')], patches)
        self.assertEqual(code, 0)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['warnings'].count('truncated'), 1)
        self.assertIn('ref-unverified:older/P1', data['warnings'])

    def test_timeout_of_a_single_call_within_budget_does_not_mark_truncated(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', first)])
        from skillsync_workflow import retro

        def behaviour(args, kw):
            raise subprocess.TimeoutExpired(args, kw.get('timeout', 0))
        patches = [self._fake_git(behaviour), mock.patch.object(retro, 'GIT_BUDGET', 1000),
                   mock.patch.object(retro, 'GIT_TIMEOUT', 5)]
        code, data = self.in_process([sig('S1', 'flaky')], patches)
        self.assertFalse(data['truncated'])
        self.assertNotIn('truncated', data['warnings'])
        self.assertIn('ref-unverified:older/P1', data['warnings'])


class StepsEF(ScanBase):
    def test_user_correction_gives_a_group(self):  # (e)
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual((self.one(data)['group'], data['groups'][0]['signals']), ('G1', ['S1']))
        self.assertEqual(data['groups'][0]['repo'], 'library')
        self.assertEqual(data['groups'][0]['target'], LIB)

    def test_other_key_without_repeat_gives_no_group(self):  # (e) odwrotnie
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((self.one(data)['group'], data['groups']), (None, []))

    def test_other_key_with_repeat_gives_a_group(self):  # (f)
        self.past('older', [('flaky', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((self.one(data)['repeat_of'], self.one(data)['group']), ('older/S1', 'G1'))

    def test_repeat_requires_the_same_key(self):  # (f) odwrotnie
        self.past('older', [('other-key', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((self.one(data)['repeat_of'], self.one(data)['group']), (None, None))

    def test_repeat_requires_the_same_target(self):
        self.past('older', [('flaky', 'library', 'skills/other.md')])
        self.assertIsNone(self.one(self.scan([sig('S1', 'flaky')]))['repeat_of'])

    def test_repeat_requires_the_same_repo(self):
        self.past('older', [('flaky', 'project', LIB)])
        self.assertIsNone(self.one(self.scan([sig('S1', 'flaky')]))['repeat_of'])

    def test_repeat_points_to_the_newest_run_and_lowest_signal_number(self):
        self.past('zzz-old', [('flaky', 'library', LIB)], created='2026-11-01T10:00:00Z')
        self.past('aaa-new', [('x', 'library', LIB), ('flaky', 'library', LIB), ('flaky', 'library', LIB)],
                  created='2026-11-03T10:00:00Z')
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['repeat_of'], 'aaa-new/S2')

    def test_repeat_of_is_set_also_for_user_correction(self):
        self.past('older', [('user-correction', 'library', LIB)])
        self.assertEqual(self.one(self.scan([sig('S1', 'user-correction')]))['repeat_of'], 'older/S1')

    def test_signals_of_one_pair_merge_into_one_group(self):
        data = self.scan([sig('S1', 'user-correction'), sig('S2', 'user-correction', target=PRJ, repo='project'),
                          sig('S3', 'user-correction'), sig('S4', 'flaky')])
        self.assertEqual([(g['id'], g['repo'], g['signals']) for g in data['groups']],
                         [('G1', 'library', ['S1', 'S3']), ('G2', 'project', ['S2'])])
        self.assertEqual([item['group'] for item in data['signals']], ['G1', 'G2', 'G1', None])

    def test_same_target_in_two_repos_makes_two_groups(self):
        data = self.scan([sig('S1', 'user-correction', repo='library', target=PRJ),
                          sig('S2', 'user-correction', repo='project', target=PRJ)])
        self.assertEqual([g['repo'] for g in data['groups']], ['library', 'project'])

    def test_attached_signals_do_not_join_a_group_with_a_newly_created_one(self):
        self.past('older', [], [('library', LIB, 'open')])
        data = self.scan([sig('S1', 'user-correction'), sig('S2', 'user-correction', repo='project', target=PRJ)])
        self.assertEqual([(i['attached_to'], i['group']) for i in data['signals']], [('older/P1', None), (None, 'G1')])
        self.assertEqual(data['groups'][0]['target'], PRJ)


# ===================================================================================================================
# K2: rejestr jako źródło stanu
# ===================================================================================================================
class RegistrySource(ScanBase):
    def test_registry_only_rejected_suppresses_and_marks_previously_rejected(self):
        self.registry([self.entry('gone/P1', 'rejected')])
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        self.assertIsNone(self.one(data)['group'])
        self.assertEqual(self.one(data, 'S2')['group'], 'G1')
        self.assertEqual(data['groups'][0]['previously_rejected'], 'gone/P1')
        self.assertEqual(data['sources']['runs'], 0)

    def test_without_the_registry_entry_the_same_signals_behave_differently(self):
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        self.assertIsNone(data['groups'][0]['previously_rejected'])

    def test_registry_only_applied_gives_recurrence(self):
        first, _, _ = self.repo_with_history(self.library)
        self.registry([self.entry('gone/P1', 'applied', ref=first)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'gone/P1')

    def test_registry_only_applied_outside_history_warns(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.registry([self.entry('gone/P1', 'applied', ref=outside)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertIn('ref-not-in-history:gone/P1', data['warnings'])
        self.assertEqual(data['groups'], [])

    def test_registry_entry_for_another_pair_is_ignored(self):
        self.registry([self.entry('gone/P1', 'rejected', repo='project', target=LIB),
                       self.entry('gone/P2', 'rejected', target='skills/other.md')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertIsNone(data['groups'][0]['previously_rejected'])

    def test_registry_state_wins_over_run_state_for_the_same_id(self):
        self.past('older', [], [('library', LIB, 'open')])
        self.registry([self.entry('older/P1', 'rejected')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertIsNone(self.one(data)['attached_to'])
        self.assertEqual(data['groups'][0]['previously_rejected'], 'older/P1')

    def test_same_run_state_without_registry_entry_attaches(self):
        self.past('older', [], [('library', LIB, 'open')])
        self.registry([self.entry('other/P1', 'rejected', target='skills/other.md')])
        self.assertEqual(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'], 'older/P1')

    def test_registry_applies_to_run_proposal_without_decision_entry(self):
        self.past('older', [], [('library', LIB, None)])
        self.registry([self.entry('older/P1', 'rejected')])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual(data['groups'][0]['previously_rejected'], 'older/P1')

    def test_registry_decision_for_one_proposal_does_not_leak_to_the_next(self):
        self.past('older', [], [('library', LIB, 'open'), ('library', LIB, 'open')])
        self.registry([self.entry('older/P2', 'rejected')])
        # P2 (nowsza) jest rejected w rejestrze, P1 pozostaje nierozstrzygnięta
        self.assertEqual(self.one(self.scan([sig('S1', 'user-correction')]))['attached_to'], 'older/P1')

    def test_registry_entry_without_run_is_dated_end_of_day(self):
        first, _, _ = self.repo_with_history(self.library)
        # przebieg z 2026-11-05T12:00Z jest starszy niż wpis rejestru z datą 2026-11-05 (T23:59:59Z)
        self.past('runx', [], [('library', LIB, 'rejected')], created='2026-11-05T12:00:00Z')
        self.registry([self.entry('gone/P1', 'applied', ref=first, date='2026-11-05')])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['groups'][0]['recurrence_of'], 'gone/P1')

    def test_run_created_right_after_the_registry_day_is_newer_than_the_entry(self):
        first, _, _ = self.repo_with_history(self.library)
        self.past('runx', [], [('library', LIB, 'rejected')], created='2026-11-06T00:00:01Z')
        self.registry([self.entry('gone/P1', 'applied', ref=first, date='2026-11-05')])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['groups'], [])  # ostatnia: rejected -> brak grupy bez korekty

    def test_registry_date_as_yaml_date_object(self):
        first, _, _ = self.repo_with_history(self.library)
        self.registry(raw=('version: 1\ndecisions: []\nproposals:\n  - proposal: gone/P1\n    repo: library\n'
                           f'    target: {LIB}\n    decision: applied\n    date: 2026-11-05\n    ref: {first}\n'))
        self.assertEqual(self.scan([sig('S1', 'flaky')])['groups'][0]['recurrence_of'], 'gone/P1')

    def test_registry_entries_of_the_current_run_identity_are_excluded(self):
        for proposal in ('cur/P1', 'cur.2/P1'):
            with self.subTest(proposal):
                self.registry([self.entry(proposal, 'rejected')])
                data = self.scan([sig('S1', 'user-correction')])
                self.assertIsNone(data['groups'][0]['previously_rejected'])
        # odwrotnie: wpis cudzego przebiegu o podobnej nazwie liczy się
        self.registry([self.entry('curious/P1', 'rejected')])
        self.assertEqual(self.scan([sig('S1', 'user-correction')])['groups'][0]['previously_rejected'], 'curious/P1')

    def test_registry_state_reported_in_sources(self):
        self.assertEqual(self.scan([])['sources']['registry'], 'absent')
        self.registry(None)
        self.assertEqual(self.scan([])['sources']['registry'], 'available')
        self.registry([])
        self.assertEqual(self.scan([])['sources']['registry'], 'available')

    def test_registry_without_proposals_section_is_accepted(self):
        self.registry(None, decisions=[{'cause_key': 'flaky', 'decision': 'rejected', 'date': '2026-11-05'}])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertEqual(data['sources']['registry'], 'available')

    def test_invalid_registry_blocks(self):
        good = self.entry('gone/P1', 'rejected')
        cases = {
            'pole nieznane': {**good, 'extra': 1},
            'applied bez ref': self.entry('gone/P1', 'applied'),
            'applied z krótkim ref': self.entry('gone/P1', 'applied', ref='abc1234'),
            'applied z ref wielkimi literami': self.entry('gone/P1', 'applied', ref='A' * 40),
            'rejected z ref': {**good, 'ref': SHA_A},
            'zła decyzja': {**good, 'decision': 'approved'},
            'zły format id': {**good, 'proposal': 'gone-P1'},
            'id bez numeru': {**good, 'proposal': 'gone/P0'},
            'zła data': {**good, 'date': '05.11.2026'},
            'brak daty': {k: v for k, v in good.items() if k != 'date'},
            'zły repo': {**good, 'repo': 'other'},
            'zły target': {**good, 'target': '../x.md'},
            'target bezwzględny': {**good, 'target': '/x.md'},
            'brak decyzji': {k: v for k, v in good.items() if k != 'decision'},
        }
        for label, item in cases.items():
            with self.subTest(label):
                self.registry([item])
                self.blocked([sig('S1', 'user-correction')])
        with self.subTest('duplikat id'):
            self.registry([good, good])
            self.blocked([sig('S1', 'user-correction')])
        with self.subTest('proposals nie jest listą'):
            self.registry(raw='version: 1\ndecisions: []\nproposals: {}\n')
            self.blocked([sig('S1', 'user-correction')])
        with self.subTest('zła wersja'):
            self.registry(raw='version: 2\ndecisions: []\n')
            self.blocked([sig('S1', 'user-correction')])
        with self.subTest('niepoprawny YAML'):
            self.registry(raw='version: [1\n')
            self.blocked([sig('S1', 'user-correction')])
        with self.subTest('pole najwyższego poziomu'):
            self.registry(raw='version: 1\ndecisions: []\nother: 1\n')
            self.blocked([sig('S1', 'user-correction')])

    def test_valid_registry_with_the_same_content_passes(self):
        self.registry([self.entry('gone/P1', 'rejected')])
        self.assertEqual(self.scan([sig('S1', 'user-correction')])['status'], 'PASS')

    @needs_os_symlinks
    def test_registry_symlink_blocks(self):
        real = self.base / 'real-decisions.yaml'
        real.write_text('version: 1\ndecisions: []\n')
        (self.root / 'docs/retro').mkdir(parents=True)
        os.symlink(real, self.root / 'docs/retro/decisions.yaml')
        self.blocked([sig('S1', 'user-correction')])


# ===================================================================================================================
# K2: przebiegi (tożsamość, nieczytelne, limity)
# ===================================================================================================================
class RunSources(ScanBase):
    def test_current_run_and_its_attempts_are_not_another_run(self):
        for current in ('cur', 'cur.2'):
            with self.subTest(current):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                self.run_id = current
                for name in ('cur', 'cur.2', 'cur.3'):
                    self.past(name, [('flaky', 'library', LIB)], [('library', LIB, 'open')])
                data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction')])
                self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), (None, None))
                self.assertEqual(data['sources']['runs'], 0)

    def test_run_with_similar_name_is_another_run(self):
        self.past('curious', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        self.past('cur-x', [('flaky', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.one(data)['attached_to'], 'curious/P1')
        self.assertEqual(data['sources']['runs'], 2)

    def test_attempt_of_another_run_is_a_run(self):
        self.past('other.2', [('flaky', 'library', LIB)])
        self.assertEqual(self.one(self.scan([sig('S1', 'flaky')]))['repeat_of'], 'other.2/S1')

    def test_directories_without_retro_files_and_plain_files_are_ignored(self):
        (self.root / '.skillsync/runs/empty').mkdir(parents=True)
        (self.root / '.skillsync/runs/empty/notes.md').write_text('x')
        (self.root / '.skillsync/runs/x.json').write_text('{}')
        self.past('older', [('flaky', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['runs'], data['sources']['unreadable'], data['warnings']),
                         (1, 0, ['no-cause-keys']))

    def test_unreadable_run_is_skipped_counted_and_warned(self):
        def broken_json(folder):
            (folder / 'retro-signals.json').write_text('{"schema": 1,')

        def wrong_dir(folder):
            data = json.loads((folder / 'retro-signals.json').read_text())
            data['run_id'] = 'inna-nazwa'
            (folder / 'retro-signals.json').write_text(json.dumps(data))

        def edit(**patch):
            def apply(folder):
                data = json.loads((folder / 'retro-signals.json').read_text())
                data.update(patch)
                (folder / 'retro-signals.json').write_text(json.dumps(data))
            return apply

        def edit_decisions(**patch):
            def apply(folder):
                data = json.loads((folder / 'retro-decisions.json').read_text())
                data.update(patch)
                (folder / 'retro-decisions.json').write_text(json.dumps(data))
            return apply

        def decisions_without_signals(folder):
            (folder / 'retro-signals.json').unlink()

        def bad_state(folder):
            data = json.loads((folder / 'retro-decisions.json').read_text())
            data['decisions'][0]['state'] = 'weird'
            (folder / 'retro-decisions.json').write_text(json.dumps(data))

        def broken_decisions(folder):
            (folder / 'retro-decisions.json').write_text('nie json')

        def oversized(folder):
            (folder / 'retro-signals.json').write_bytes(b' ' * (MB + 10))

        def empty_file(folder):
            (folder / 'retro-signals.json').write_bytes(b'')

        mutations = {
            'zły JSON sygnałów': broken_json, 'run_id niezgodny z katalogiem': wrong_dir,
            'schema 2': edit(schema=2), 'brak created_at': edit(created_at=None),
            'created_at nie data': edit(created_at='wczoraj'), 'signals nie lista': edit(signals={}),
            'proposals nie lista': edit(proposals={}),
            'propozycja bez celu': edit(proposals=[{'id': 'P1', 'repo': 'library', 'target': None}]),
            'sygnał bez id': edit(signals=[{'key': 'flaky', 'repo': 'library', 'target': LIB}]),
            'decyzje bez sygnałów': decisions_without_signals, 'zły stan decyzji': bad_state,
            'zły JSON decyzji': broken_decisions, 'run_id decyzji niezgodny': edit_decisions(run_id='x'),
            'schema decyzji 2': edit_decisions(schema=2),
            'plik > 1 MB': oversized, 'pusty plik': empty_file,
        }
        for label, mutate in mutations.items():
            with self.subTest(label):
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)
                folder = self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
                self.past('fine', [])
                mutate(folder)
                data = self.scan([sig('S1', 'flaky')])
                self.assertEqual(data['sources']['runs'], 1, label)
                self.assertEqual(data['sources']['unreadable'], 1, label)
                self.assertEqual([w for w in data['warnings'] if w.startswith('unreadable-run:')],
                                 ['unreadable-run:older'], label)
                # nieczytelny przebieg niczego nie wnosi: ani powtórzenia, ani przypięcia
                self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), (None, None), label)

    def test_readable_run_has_no_warning_and_contributes(self):
        self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['unreadable'], self.one(data)['attached_to']), (0, 'older/P1'))
        self.assertFalse([w for w in data['warnings'] if w.startswith('unreadable-run:')])

    def test_invalid_run_directory_name_is_unreadable_and_not_leaked(self):
        folder = self.root / '.skillsync/runs/bad name'
        folder.mkdir(parents=True)
        (folder / 'retro-signals.json').write_text('{}')
        result = self.run_scan(self.put_input([sig('S1', 'flaky')]))
        data = self.parsed(result, 0)
        self.assertEqual(data['sources']['unreadable'], 1)
        labels = [w for w in data['warnings'] if w.startswith('unreadable-run:')]
        self.assertEqual(len(labels), 1)
        self.assertRegex(labels[0], r'^unreadable-run:invalid-name:[0-9a-f]{12}$')
        self.assertNotIn('bad name', result.stdout)
        self.assertNotIn('bad%20name', result.stdout)

    def _padded_run(self, name, size):
        folder = self.past(name, [('flaky', 'library', LIB)])
        path = folder / 'retro-signals.json'
        text = path.read_text()
        path.write_text(text + ' ' * (size - len(text.encode())))
        return path

    def test_run_file_exactly_at_the_limit_is_read(self):
        path = self._padded_run('older', MB)  # limit 1 MB z kontraktu, literalnie
        self.assertEqual(path.stat().st_size, MB)
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['runs'], data['sources']['unreadable']), (1, 0))
        self.assertEqual(self.one(data)['repeat_of'], 'older/S1')

    def test_run_file_one_byte_over_the_limit_is_unreadable(self):
        self._padded_run('older', MB + 1)
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['runs'], data['sources']['unreadable']), (0, 1))
        self.assertIsNone(self.one(data)['repeat_of'])

    def test_base_path_is_absent_from_pass_and_blocked_results(self):
        self.past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        results = [self.run_scan(self.put_input([sig('S1', 'flaky'), sig('S2', 'user-correction')]))]
        self.assertEqual(results[0].returncode, 0)
        results.append(self.run_scan(self.put_input([sig('S1', repo='nope')], name='bad.json'), ))
        results.append(self.run_scan('../escape.json'))
        self.write('docs/in.json', json.dumps({'schema': 1, 'run_id': 'cur', 'signals': []}))
        results.append(self.run_scan('docs/in.json'))
        results.append(self.run_scan(self.rel(name='absent.json')))
        self.registry(raw='version: 1\ndecisions: []\nproposals: {}\n')
        results.append(self.run_scan(self.rel()))
        self.assertEqual([r.returncode for r in results], [0, 2, 2, 2, 2, 2])
        for result in results:
            self.assertNotIn(str(self.base), result.stdout)
            self.assertNotIn(self.base.name, result.stdout)

    def test_total_scan_byte_budget_truncates_with_one_warning(self):
        from skillsync_workflow import retro_scan
        for name in ('r1', 'r2', 'r3'):
            self.past(name, [('onlyold' if name == 'r1' else 'x', 'library', LIB)])
        size = lambda n: sum((self.root / '.skillsync/runs' / n / f).stat().st_size
                             for f in ('retro-signals.json', 'retro-decisions.json')
                             if (self.root / '.skillsync/runs' / n / f).exists())
        limit = size('r2') + size('r3')  # kolejność odczytu: najnowsze nazwy pierwsze, r1 się nie mieści
        code, data = self.in_process([sig('S1', 'onlyold')], [mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', limit)])
        self.assertEqual(code, 0)
        self.assertTrue(data['truncated'])
        self.assertEqual(data['warnings'].count('truncated'), 1)
        self.assertEqual(data['sources']['runs'], 2)
        self.assertIsNone(data['signals'][0]['repeat_of'])
        # odwrotnie: budżet o bajt większy od sumy trzech przebiegów nie obcina
        code, data = self.in_process([sig('S1', 'onlyold')], [mock.patch.object(
            retro_scan, 'MAX_SCAN_BYTES', limit + size('r1'))])
        self.assertFalse(data['truncated'])
        self.assertNotIn('truncated', data['warnings'])
        self.assertEqual(data['signals'][0]['repeat_of'], 'r1/S1')

    @needs_os_symlinks
    def test_symlinked_retro_signals_is_unreadable(self):
        folder = self.past('older', [('flaky', 'library', LIB)])
        real = self.base / 'real-signals.json'
        shutil.move(str(folder / 'retro-signals.json'), real)
        os.symlink(real, folder / 'retro-signals.json')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['unreadable'], self.one(data)['repeat_of']), (1, None))

    def test_unreadable_runs_do_not_hide_readable_ones_and_do_not_block(self):
        self.past('good', [('flaky', 'library', LIB)])
        (self.root / '.skillsync/runs/bad').mkdir()
        (self.root / '.skillsync/runs/bad/retro-signals.json').write_text('x')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['status'], self.one(data)['repeat_of']), ('PASS', 'good/S1'))

    def test_run_limit_marks_truncated(self):
        from skillsync_workflow import retro_scan
        for name in ('r1', 'r2', 'r3'):
            self.past(name, [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro_scan, 'MAX_RUNS', 2)])
        self.assertEqual(code, 0)
        self.assertTrue(data['truncated'])
        self.assertIn('truncated', data['warnings'])
        self.assertEqual(data['sources']['runs'], 2)

    def test_exactly_the_run_limit_is_not_truncated(self):
        from skillsync_workflow import retro_scan
        for name in ('r1', 'r2'):
            self.past(name, [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro_scan, 'MAX_RUNS', 2)])
        self.assertEqual((code, data['truncated'], data['sources']['runs']), (0, False, 2))
        self.assertNotIn('truncated', data['warnings'])

    def test_default_run_limit_is_2000(self):
        from skillsync_workflow import retro_scan
        self.assertEqual(retro_scan.MAX_RUNS, 2000)

    def test_current_run_directory_does_not_count_toward_the_limit(self):
        from skillsync_workflow import retro_scan
        self.past('cur', [('flaky', 'library', LIB)])
        self.past('r1', [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro_scan, 'MAX_RUNS', 1)])
        self.assertEqual((data['truncated'], data['sources']['runs']), (False, 1))


# ===================================================================================================================
# Kształt wyniku
# ===================================================================================================================
class ResultShape(ScanBase):
    def test_full_result_matches_the_contract(self):
        self.past('older', [('flaky', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction', repo=None, target=None)])
        self.assertEqual(data, {
            'schema': 1, 'status': 'PASS', 'run_id': 'cur', 'worktree': False, 'cross_project': False,
            'signals': [{'id': 'S1', 'repeat_of': 'older/S1', 'cross_project_repeats': 0, 'attached_to': None,
                         'group': 'G1'},
                        {'id': 'S2', 'repeat_of': None, 'cross_project_repeats': 0, 'attached_to': None,
                         'group': None}],
            'groups': [{'id': 'G1', 'repo': 'library', 'target': LIB, 'signals': ['S1'],
                        'previously_rejected': None, 'recurrence_of': None}],
            'sources': {'runs': 1, 'unreadable': 0, 'registry': 'absent', 'main_checkout': None, 'projects': None},
            'truncated': False, 'warnings': ['no-cause-keys']})

    def test_signal_order_follows_the_input(self):
        data = self.scan([sig('S3', 'flaky'), sig('S1', 'flaky'), sig('S2', 'flaky')])
        self.assertEqual([item['id'] for item in data['signals']], ['S3', 'S1', 'S2'])

    def test_result_is_deterministic(self):
        self.past('a-run', [('flaky', 'library', LIB)], [('library', LIB, 'open')], created='2026-11-02T10:00:00Z')
        self.past('b-run', [('flaky', 'library', LIB)], [('library', LIB, 'open')], created='2026-11-02T10:00:00Z')
        rel = self.put_input([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        first = self.run_scan(rel).stdout
        self.assertEqual(self.run_scan(rel).stdout, first)

    def test_run_id_attempt_is_echoed(self):
        self.run_id = 'cur.2'
        self.assertEqual(self.scan([])['run_id'], 'cur.2')


# ===================================================================================================================
# Regresja: rejestr bez sekcji `proposals:` w poleceniu `retro`
# ===================================================================================================================
class RetroRegistryRegression(ScanBase):
    def test_retro_parses_a_registry_without_proposals_as_before(self):
        self.registry(None, decisions=[{'cause_key': 'flaky', 'decision': 'rejected', 'date': '2026-11-05'},
                                       {'cause_key': 'slow', 'decision': 'approved', 'date': '2026-11-04',
                                        'owner': 'test', 'case': 'x/y', 'ref': 'abc1234'}])
        result = subprocess.run([sys.executable, str(CLI), 'retro', str(self.root), '--json'],
                                capture_output=True, text=True, timeout=30, cwd=self.root)
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data['decisions'], {'status': 'available', 'path': 'docs/retro/decisions.yaml', 'count': 2})

    def test_retro_still_reports_an_invalid_registry(self):
        self.registry(raw='version: 1\ndecisions:\n  - cause_key: flaky\n    decision: maybe\n    date: 2026-11-05\n')
        result = subprocess.run([sys.executable, str(CLI), 'retro', str(self.root), '--json'],
                                capture_output=True, text=True, timeout=30, cwd=self.root)
        self.assertEqual(json.loads(result.stdout)['decisions']['status'], 'invalid')


# ===================================================================================================================
# K3: powiązany worktree czyta przebiegi i rejestr głównego checkoutu
# ===================================================================================================================
class WorktreeBase(ScanBase):
    """Prawdziwe repozytorium git (`self.repo`) z powiązanym worktree (`self.wt`); `self.root` = projekt w worktree."""

    def setUp(self):
        super().setUp()
        self.repo = self.base / 'repo'
        self.wt = self.base / 'wt'
        self.main_root = self.repo

    def make_worktree(self, prefix=''):
        """Projekt (manifest zatwierdzony w gałęzi) w `self.repo/<prefix>`; worktree dostaje ten sam układ."""
        self.init_repo(self.repo)
        self.main_root = self.repo / prefix if prefix else self.repo
        rel = (prefix + '/' if prefix else '') + '.github/.skillsync.yaml'
        commit_file(self.repo, rel, yaml.safe_dump(self.manifest), 'manifest')
        git(self.repo, 'worktree', 'add', '-q', '-b', 'feat', str(self.wt))
        self.root = self.wt / prefix if prefix else self.wt
        self.assertTrue((self.root / '.github/.skillsync.yaml').is_file())

    def main_past(self, *args, **kw):
        with self.at(self.main_root):
            return self.past(*args, **kw)

    def main_registry(self, *args, **kw):
        with self.at(self.main_root):
            return self.registry(*args, **kw)

    def outside_run(self, name='outside-run', created='2026-11-03T10:00:00Z'):
        """Poprawny przebieg poza drzewem źródła (cel dowiązań), z sygnałem flaky/library/LIB."""
        with self.at(self.base / 'outside'):
            return self.past(name, [('flaky', 'library', LIB)], [('library', LIB, 'open')], created=created)

    def assert_main_skipped(self, data, signal='S1'):
        self.assertTrue(data['worktree'])
        self.assertIsNone(data['sources']['main_checkout'])
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')],
                         ['source-skipped:main-checkout'])
        self.assertEqual((self.one(data, signal)['repeat_of'], self.one(data, signal)['attached_to']), (None, None))


class MainCheckoutReading(WorktreeBase):
    def test_worktree_reads_runs_and_registry_of_the_main_checkout(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        self.main_past('prev', [])
        self.main_registry([self.entry('gone/P1', 'rejected', target='skills/other.md')])
        data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction', target='skills/other.md')])
        self.assertTrue(data['worktree'])
        self.assertEqual(data['sources']['main_checkout'], 2)
        self.assertEqual(data['sources']['runs'], 0, 'runs liczy przebiegi bieżącego drzewa, nie głównego')
        self.assertEqual(data['sources']['registry'], 'available')
        self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), ('older/S1', 'older/P1'))
        self.assertEqual(data['groups'][0]['previously_rejected'], 'gone/P1')

    def test_main_checkout_without_runs_counts_zero_not_null(self):
        self.make_worktree()
        data = self.scan([sig('S1', 'flaky')])
        self.assertTrue(data['worktree'])
        self.assertEqual(data['sources']['main_checkout'], 0)
        self.assertEqual(data['warnings'], ['no-cause-keys'])

    def test_project_in_a_subdirectory_uses_the_matching_prefix_in_the_main_checkout(self):
        self.make_worktree('services/api')
        self.main_past('real', [('flaky', 'library', LIB)], created='2026-11-01T10:00:00Z')
        # magazyn w korzeniu repozytorium to nie projekt: nie wolno go czytać
        with self.at(self.repo):
            self.past('decoy', [('flaky', 'library', LIB)], created='2026-11-09T10:00:00Z')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['worktree'], data['sources']['main_checkout']), (True, 1))
        self.assertEqual(self.one(data)['repeat_of'], 'real/S1')

    def test_main_checkout_runs_of_the_current_run_are_not_another_run(self):
        self.make_worktree()
        self.main_past('curious', [('flaky', 'library', LIB)], created='2026-11-01T10:00:00Z')
        for current in ('cur', 'cur.2'):
            with self.subTest(current):
                self.run_id = current
                shutil.rmtree(self.main_root / '.skillsync/runs/cur', ignore_errors=True)
                for name in ('cur', 'cur.2', 'cur.3'):
                    self.main_past(name, [('flaky', 'library', LIB)], [('library', LIB, 'open')])
                self.main_registry([self.entry('cur/P1', 'rejected', target='skills/other.md')])
                data = self.scan([sig('S1', 'flaky'), sig('S2', 'user-correction', target='skills/other.md')])
                self.assertEqual(data['sources']['main_checkout'], 1, 'tylko "curious" jest innym przebiegiem')
                self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), ('curious/S1', None))
                self.assertIsNone(data['groups'][0]['previously_rejected'], 'wpis rejestru bieżącego przebiegu')
                shutil.rmtree(self.root / '.skillsync', ignore_errors=True)

    def test_registries_sum_and_the_main_checkout_wins_on_the_same_proposal_id(self):
        self.make_worktree()
        self.main_registry([self.entry('m/P1', 'rejected', target=LIB),
                            self.entry('shared/P1', 'rejected', target='skills/third.md')])
        self.registry([self.entry('w/P1', 'rejected', target='skills/other.md'),
                       self.entry('shared/P1', 'applied', target='skills/third.md', ref=SHA_A)])
        data = self.scan([sig('S1', 'user-correction'), sig('S2', 'user-correction', target='skills/other.md'),
                          sig('S3', 'user-correction', target='skills/third.md')])
        by_target = {g['target']: g for g in data['groups']}
        self.assertEqual(by_target[LIB]['previously_rejected'], 'm/P1', 'rejestr głównego')
        self.assertEqual(by_target['skills/other.md']['previously_rejected'], 'w/P1', 'rejestr worktree')
        # ten sam id: główny mówi rejected, worktree applied; wygrywa główny (applied z ref nie do sprawdzenia dałby
        # ostrzeżenie ref-unverified i brak previously_rejected)
        self.assertEqual(by_target['skills/third.md']['previously_rejected'], 'shared/P1')
        self.assertFalse([w for w in data['warnings'] if w.startswith('ref-')], data['warnings'])

    def test_main_checkout_wins_also_when_it_is_the_applied_one(self):
        self.make_worktree()
        self.main_registry([self.entry('shared/P1', 'applied', target=LIB, ref=SHA_A)])
        self.registry([self.entry('shared/P1', 'rejected', target=LIB)])
        data = self.scan([sig('S1', 'user-correction')])
        self.assertIsNone(data['groups'][0]['previously_rejected'])
        self.assertIn('ref-unverified:shared/P1', data['warnings'])

    def test_regular_checkout_is_not_a_worktree(self):
        self.init_repo(self.root)
        self.past('older', [('flaky', 'library', LIB)])
        data = self.scan([sig('S1', 'flaky')])
        self.assertFalse(data['worktree'])
        self.assertIsNone(data['sources']['main_checkout'])
        self.assertFalse([w for w in data['warnings'] if w.startswith('source-skipped:')])

    def test_directory_without_git_is_not_a_worktree(self):
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['worktree'], data['sources']['main_checkout']), (False, None))

    def test_main_checkout_that_has_a_linked_worktree_does_not_read_it(self):
        self.make_worktree()
        with self.at(self.wt):
            self.past('wtrun', [('flaky', 'library', LIB)])
        self.root = self.main_root
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['worktree'], data['sources']['main_checkout']), (False, None))
        self.assertIsNone(self.one(data)['repeat_of'])

    def test_scan_writes_nothing_in_the_main_checkout(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)], [('library', LIB, 'open')])
        self.main_registry([self.entry('gone/P1', 'rejected')])
        rel = self.put_input([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        before = (tree_state(self.repo), tree_state(self.wt))
        data = self.parsed(self.run_scan(rel), 0)
        self.assertEqual(data['sources']['main_checkout'], 1)
        self.assertEqual((tree_state(self.repo), tree_state(self.wt)), before)

    def test_foreign_git_environment_does_not_change_the_main_checkout_choice(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        self.init_repo(self.base / 'other-repo')
        rel = self.put_input([sig('S1', 'flaky')])
        plain = self.parsed(self.run_scan(rel), 0)
        self.assertEqual((plain['worktree'], plain['sources']['main_checkout']), (True, 1))
        other = self.base / 'other-repo'
        for env in ({'GIT_DIR': str(other / '.git')}, {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)},
                    {'GIT_COMMON_DIR': str(other / '.git')}):
            with self.subTest(env):
                self.assertEqual(self.parsed(self.run_scan(rel, env=env), 0), plain)

    def test_foreign_git_dir_of_a_worktree_does_not_make_a_regular_checkout_a_worktree(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        plain_root = self.base / 'plain'
        self.init_repo(plain_root)
        self.root = plain_root
        self.save()
        env = {'GIT_DIR': str(self.repo / '.git/worktrees/wt')}
        self.assertTrue(Path(env['GIT_DIR']).is_dir())
        data = self.parsed(self.run_scan(self.put_input([sig('S1', 'flaky')]), env=env), 0)
        self.assertEqual((data['worktree'], data['sources']['main_checkout']), (False, None))
        self.assertIsNone(self.one(data)['repeat_of'])

    @needs_posix
    def test_slow_git_does_not_hang_the_scan(self):
        import time
        from skillsync_workflow import retro
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        fake = self.base / 'fakebin'
        fake.mkdir()
        real = shutil.which('git')
        (fake / 'git').write_text('#!/bin/sh\ncase "$*" in *--show-prefix*) sleep 4;; esac\nexec %s "$@"\n' % real)
        (fake / 'git').chmod(0o755)
        started = time.monotonic()
        with mock.patch.dict(os.environ, {'PATH': str(fake) + os.pathsep + os.environ['PATH']}):
            code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro, 'GIT_TIMEOUT', 0.3)])
        self.assertLess(time.monotonic() - started, 3.5, 'polecenia git muszą mieć limit GIT_TIMEOUT')
        self.assertEqual((code, data['status']), (0, 'PASS'))


BAD_TIMES = ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00')  # astimezone(UTC) rzuca OverflowError


class OutOfRangeTimestamps(WorktreeBase):
    """`created_at` na krańcach zakresu daty nie przerywa skanu: przebieg jest nieczytelny, reszta jest czytana."""

    def test_local_run_is_unreadable_and_the_scan_passes(self):
        for bad in BAD_TIMES:
            with self.subTest(bad):
                shutil.rmtree(self.root / '.skillsync/runs', ignore_errors=True)
                self.past('older', [('flaky', 'library', LIB)], created=bad)
                self.past('fine', [('flaky', 'library', LIB)])
                data = self.scan([sig('S1', 'flaky')])
                self.assertEqual((data['sources']['runs'], data['sources']['unreadable']), (1, 1))
                self.assertIn('unreadable-run:older', data['warnings'])
                self.assertEqual(self.one(data)['repeat_of'], 'fine/S1')

    def test_main_checkout_with_such_a_run_is_still_read(self):
        self.make_worktree()
        for bad in BAD_TIMES:
            with self.subTest(bad):
                shutil.rmtree(self.main_root / '.skillsync', ignore_errors=True)
                self.main_past('bad-run', [('flaky', 'library', LIB)], created=bad)
                self.main_past('good-run', [('flaky', 'library', LIB)])
                data = self.scan([sig('S1', 'flaky')])
                self.assertEqual(data['sources']['main_checkout'], 1)
                self.assertFalse([w for w in data['warnings'] if w.startswith('source-skipped:')], data['warnings'])
                self.assertIn('unreadable-run:bad-run', data['warnings'])
                self.assertEqual(self.one(data)['repeat_of'], 'good-run/S1')

    def test_cross_project_with_such_a_run_is_still_read(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.home = self.base / 'home'
        self.repos = self.home / 'Repos'
        self.repos.mkdir(parents=True)
        self.use_layers(discover_paths=[str(self.repos)], tags={})
        self.root = self.repos / 'current'
        self.root.mkdir()
        self.save()
        for bad in BAD_TIMES:
            with self.subTest(bad):
                shutil.rmtree(self.repos / 'alpha', ignore_errors=True)
                alpha = self.repos / 'alpha'
                alpha.mkdir()
                with self.at(alpha):
                    self.save()
                    self.past('bad-run', [('flaky', 'library', LIB)], created=bad)
                    self.past('good-run', [('flaky', 'library', LIB)])
                with mock.patch.dict(os.environ, {'HOME': str(self.home)}):
                    data = self.scan([sig('S1', 'flaky')], extra=['--cross-project'])
                self.assertEqual((self.one(data)['cross_project_repeats'], data['sources']['projects']), (1, 1))
                self.assertFalse([w for w in data['warnings'] if w.startswith('source-skipped:')], data['warnings'])


class MainCheckoutGitFailures(WorktreeBase):
    """Błędy pierwszego `git rev-parse`: tylko rc 128 (nie repozytorium) jest ciche."""

    def fake_git(self, rev_parse):
        """Katalog z atrapą `git`: `rev-parse --show-prefix` wykonuje `rev_parse`, reszta idzie do prawdziwego gita."""
        fake = self.base / 'fakebin'
        fake.mkdir(exist_ok=True)
        (fake / 'git').write_text('#!/bin/sh\ncase "$*" in *--show-prefix*) %s;; esac\nexec %s "$@"\n'
                                  % (rev_parse, shutil.which('git')))
        (fake / 'git').chmod(0o755)
        return fake

    def scan_with(self, fake, patches=()):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        with mock.patch.dict(os.environ, {'PATH': str(fake) + os.pathsep + os.environ['PATH']}):
            code, data = self.in_process([sig('S1', 'flaky')], list(patches))
        self.assertEqual((code, data['status']), (0, 'PASS'))
        return data

    def assert_skipped(self, data, truncated):
        self.assertIsNone(data['sources']['main_checkout'])
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')],
                         ['source-skipped:main-checkout'])
        self.assertIsNone(data['signals'][0]['repeat_of'])
        self.assertIs(data['truncated'], truncated)
        self.assertEqual('truncated' in data['warnings'], truncated)

    def test_directory_outside_a_git_repository_is_silently_a_plain_project(self):
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['worktree'], data['sources']['main_checkout'], data['truncated']), (False, None, False))
        self.assertEqual(data['warnings'], ['no-cause-keys'])

    @needs_posix
    def test_rc_128_is_silent_and_not_a_worktree(self):
        data = self.scan_with(self.fake_git('exit 128'))
        self.assertEqual((data['worktree'], data['sources']['main_checkout'], data['truncated']), (False, None, False))
        self.assertFalse([w for w in data['warnings'] if w.startswith('source-skipped:')])

    @needs_posix
    def test_other_nonzero_rc_skips_the_main_checkout_with_a_warning(self):
        for rc in (1, 2, 127, 129):
            with self.subTest(rc):
                shutil.rmtree(self.base / 'repo', ignore_errors=True)
                shutil.rmtree(self.wt, ignore_errors=True)
                shutil.rmtree(self.base / 'fakebin', ignore_errors=True)
                self.root = self.base / 'project'
                self.assert_skipped(self.scan_with(self.fake_git('exit %d' % rc)), False)

    @needs_posix
    def test_too_short_output_skips_the_main_checkout_with_a_warning(self):
        self.assert_skipped(self.scan_with(self.fake_git('echo only-one-line; exit 0')), False)

    @needs_posix
    def test_spawn_error_skips_the_main_checkout_with_a_warning(self):
        fake = self.base / 'fakebin'
        fake.mkdir()
        (fake / 'git').write_text('#!/bin/sh\nexit 0\n')  # bez bitu wykonania: PermissionError przy uruchomieniu
        (fake / 'git').chmod(0o644)
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        with mock.patch.dict(os.environ, {'PATH': str(fake)}):
            code, data = self.in_process([sig('S1', 'flaky')])
        self.assertEqual(code, 0)
        self.assert_skipped(data, False)

    @needs_posix
    def test_single_call_timeout_skips_without_truncation(self):
        from skillsync_workflow import retro
        data = self.scan_with(self.fake_git('sleep 4'), [mock.patch.object(retro, 'GIT_TIMEOUT', 0.3)])
        self.assert_skipped(data, False)

    @needs_posix
    def test_timeout_caused_by_the_scan_budget_also_truncates(self):
        from skillsync_workflow import retro
        data = self.scan_with(self.fake_git('sleep 4'), [mock.patch.object(retro, 'GIT_TIMEOUT', 5),
                                                         mock.patch.object(retro, 'GIT_BUDGET', 0.3)])
        self.assert_skipped(data, True)

    def test_exhausted_budget_skips_and_truncates(self):
        from skillsync_workflow import retro
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro, 'GIT_BUDGET', 0)])
        self.assertEqual(code, 0)
        self.assert_skipped(data, True)

    def test_untouched_budget_reads_the_main_checkout_without_truncation(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['main_checkout'], data['truncated']), (1, False))
        self.assertNotIn('source-skipped:main-checkout', data['warnings'])


class MainCheckoutRefusals(WorktreeBase):
    def test_main_checkout_without_manifest_is_skipped_with_a_warning(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        os.remove(self.main_root / '.github/.skillsync.yaml')
        self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    def test_main_checkout_with_a_failing_store_is_skipped(self):
        cases = {
            '.skillsync jest plikiem': lambda: (shutil.rmtree(self.main_root / '.skillsync'),
                                                (self.main_root / '.skillsync').write_text('x')),
            '.skillsync/runs jest plikiem': lambda: (shutil.rmtree(self.main_root / '.skillsync/runs'),
                                                     (self.main_root / '.skillsync/runs').write_text('x')),
        }
        for label, break_store in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.base / 'repo', ignore_errors=True)
                shutil.rmtree(self.wt, ignore_errors=True)
                self.root = self.base / 'project'
                self.make_worktree()
                self.main_past('older', [('flaky', 'library', LIB)])
                break_store()
                self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    @needs_os_symlinks
    def test_main_checkout_store_symlinked_outside_is_skipped(self):
        self.make_worktree()
        self.outside_run()
        shutil.rmtree(self.main_root / '.skillsync', ignore_errors=True)
        os.symlink(self.base / 'outside/.skillsync', self.main_root / '.skillsync')
        self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    @needs_os_symlinks
    def test_main_checkout_runs_directory_symlinked_outside_is_skipped(self):
        self.make_worktree()
        self.outside_run()
        (self.main_root / '.skillsync').mkdir()
        os.symlink(self.base / 'outside/.skillsync/runs', self.main_root / '.skillsync/runs')
        self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    @needs_os_symlinks
    def test_run_directory_symlinked_outside_is_not_read(self):
        self.make_worktree()
        outside = self.outside_run('linked')
        self.main_past('real', [])
        os.symlink(outside, self.main_root / '.skillsync/runs/linked')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(data['sources']['main_checkout'], 1)
        self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), (None, None))

    @needs_os_symlinks
    def test_run_file_symlinked_outside_is_unreadable_and_not_read(self):
        self.make_worktree()
        outside = self.outside_run('linked')
        folder = self.main_past('linked', [])
        (folder / 'retro-signals.json').unlink()
        os.symlink(outside / 'retro-signals.json', folder / 'retro-signals.json')
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual((data['sources']['main_checkout'], data['sources']['unreadable']), (0, 1))
        self.assertEqual((self.one(data)['repeat_of'], self.one(data)['attached_to']), (None, None))

    @needs_os_symlinks
    def test_registry_symlinked_outside_skips_the_main_checkout(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        with self.at(self.base / 'outside'):
            outside = self.registry([self.entry('gone/P1', 'rejected')])
        (self.main_root / 'docs/retro').mkdir(parents=True)
        os.symlink(self.base / 'outside/docs/retro/decisions.yaml', self.main_root / 'docs/retro/decisions.yaml')
        self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    @needs_os_symlinks
    def test_registry_behind_a_symlinked_directory_skips_the_main_checkout(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        with self.at(self.base / 'outside'):
            self.registry([self.entry('gone/P1', 'rejected')])
        os.symlink(self.base / 'outside/docs', self.main_root / 'docs')
        self.assert_main_skipped(self.scan([sig('S1', 'flaky')]))

    @needs_os_symlinks
    def test_run_behind_a_symlinked_intermediate_directory_is_not_read(self):
        # `.skillsync` jako dowiązanie do katalogu z prawidłowym magazynem: kontrola magazynu odmawia
        self.make_worktree()
        self.outside_run()
        shutil.rmtree(self.main_root / '.skillsync', ignore_errors=True)
        os.symlink(self.base / 'outside/.skillsync', self.main_root / '.skillsync')
        data = self.scan([sig('S1', 'flaky')])
        self.assertIsNone(data['sources']['main_checkout'])
        self.assertIsNone(self.one(data)['repeat_of'])

    def test_invalid_main_registry_skips_the_source_with_a_warning_and_does_not_block(self):
        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        self.main_registry(raw='version: 1\ndecisions: []\nproposals: {}\n')
        data = self.scan([sig('S1', 'flaky')])  # kod 0
        self.assert_main_skipped(data)

    def test_invalid_local_registry_still_blocks_in_a_worktree(self):
        self.make_worktree()
        self.registry(raw='version: 1\ndecisions: []\nproposals: {}\n')
        self.blocked([sig('S1', 'flaky')])

    def test_skipped_main_checkout_does_not_leak_its_path(self):
        self.make_worktree()
        os.remove(self.main_root / '.github/.skillsync.yaml')
        result = self.run_scan(self.put_input([sig('S1', 'flaky')]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for text in (str(self.base), self.base.name, 'repo'):
            self.assertNotIn(text, result.stdout)


# ===================================================================================================================
# K4: --cross-project
# ===================================================================================================================
class CrossBase(ScanBase):
    def setUp(self):
        super().setUp()
        self.home = self.base / 'home'
        self.repos = self.home / 'Repos'
        self.repos.mkdir(parents=True)
        patch = mock.patch.dict(os.environ, {'HOME': str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.use_layers(discover_paths=[str(self.repos)], tags={})
        self.root = self.repos / 'current'
        self.root.mkdir()
        self.save()

    def other(self, name, *runs, under=None):
        """Inny projekt z manifestem; `runs`: argumenty `past` (nazwa, sygnały, propozycje, ...)."""
        root = (under or self.repos) / name
        root.mkdir(parents=True)
        with self.at(root):
            self.save()
            for run in runs:
                self.past(*run[:3], **(run[3] if len(run) > 3 else {}))
        return root

    def cross(self, signals, code=0, **kw):
        return self.scan(signals, code=code, extra=['--cross-project'], **kw)

    def local(self, data, ident='S1'):
        item = self.one(data, ident)
        return item['repeat_of'], item['cross_project_repeats'], item['group']


class CrossProjectRepeats(CrossBase):
    def test_same_key_and_target_in_another_project_counts_and_gives_a_group(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 1, 'G1'))
        self.assertTrue(data['cross_project'])
        self.assertEqual(data['sources']['projects'], 1)
        self.assertEqual(data['groups'][0]['signals'], ['S1'])

    def test_count_is_the_number_of_projects_not_runs_or_signals(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)] * 2, ()), ('r2', [('flaky', 'library', LIB)], ()))
        self.other('beta', ('r1', [('flaky', 'library', LIB)], ()))
        self.other('gamma', ('r1', [('flaky', 'library', 'skills/other.md'), ('flaky2', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 2, 'G1'))
        self.assertEqual(data['sources']['projects'], 3)

    def test_without_the_flag_there_is_no_cross_project_reading(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 0, None))
        self.assertIs(data['cross_project'], False)
        self.assertIsNone(data['sources']['projects'])

    def test_no_other_projects_reads_zero(self):
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual((self.local(data), data['cross_project'], data['sources']['projects']),
                         ((None, 0, None), True, 0))

    def test_alias_in_another_project_counts_as_the_same_key(self):
        self.keys_file({'flaky-tests': ['flaky-test']})
        self.other('alpha', ('r1', [('flaky-test', 'library', LIB)], ()))
        self.other('beta', ('r1', [('flaky-tests', 'library', LIB)], ()))
        for key in ('flaky-tests', 'flaky-test'):
            with self.subTest(key):
                self.assertEqual(self.local(self.cross([sig('S1', key)])), (None, 2, 'G1'))

    def test_other_key_target_or_repo_does_not_count(self):
        self.other('alpha', ('r1', [('other-key', 'library', LIB), ('flaky', 'library', 'skills/other.md')], ()))
        self.assertEqual(self.local(self.cross([sig('S1', 'flaky')])), (None, 0, None))

    def test_local_repeat_keeps_repeat_of_and_adds_the_count(self):
        self.past('older', [('flaky', 'library', LIB)])
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        self.assertEqual(self.local(self.cross([sig('S1', 'flaky')])), ('older/S1', 1, 'G1'))

    def test_cross_project_repeat_alone_leaves_repeat_of_null(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        item = self.one(self.cross([sig('S1', 'flaky')]))
        self.assertIsNone(item['repeat_of'])
        self.assertEqual(item['cross_project_repeats'], 1)

    def test_signals_of_project_scope_never_leave_the_project(self):
        self.other('alpha', ('r1', [('flaky', 'project', PRJ), ('flaky', 'project', LIB)], ()),
                   ('r2', [('flaky', 'library', PRJ)], ()))
        self.other('beta', ('r1', [('flaky', 'project', PRJ)], ()))
        data = self.cross([sig('S1', 'flaky', repo='project', target=PRJ), sig('S2', 'flaky', repo='project', target=LIB),
                           sig('S3', 'flaky', repo='library', target=LIB)])
        self.assertEqual([self.local(data, i) for i in ('S1', 'S2', 'S3')],
                         [(None, 0, None), (None, 0, None), (None, 0, None)])
        self.assertEqual(data['groups'], [])

    def test_signal_without_target_or_repo_is_never_counted(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky', repo=None, target=None)])
        self.assertEqual(self.local(data), (None, 0, None))

    def test_user_correction_key_counts_across_projects_too(self):
        self.other('alpha', ('r1', [('user-correction', 'library', LIB)], ()))
        self.assertEqual(self.local(self.cross([sig('S1', 'user-correction')])), (None, 1, 'G1'))

    def test_suppression_and_attachment_in_another_project_do_not_apply(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], [('library', LIB, 'open')]),
                   ('r2', [], [('library', LIB, 'rejected')]))
        with self.at(self.repos / 'alpha'):
            self.registry([self.entry('x/P1', 'rejected')])
        data = self.cross([sig('S1', 'flaky'), sig('S2', 'user-correction')])
        self.assertEqual(self.local(data), (None, 1, 'G1'), 'rejected/open obcego projektu nie tłumi ani nie przypina')
        self.assertIsNone(self.one(data)['attached_to'])
        self.assertEqual(data['groups'][0]['signals'], ['S1', 'S2'])
        self.assertEqual((data['groups'][0]['previously_rejected'], data['groups'][0]['recurrence_of']), (None, None))

    def test_local_rejection_still_suppresses_despite_a_cross_project_repeat(self):
        self.past('older', [], [('library', LIB, 'rejected')])
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 1, None))

    def test_local_open_proposal_still_attaches_despite_a_cross_project_repeat(self):
        self.past('older', [], [('library', LIB, 'open')])
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        item = self.one(self.cross([sig('S1', 'flaky')]))
        self.assertEqual((item['attached_to'], item['group'], item['cross_project_repeats']), ('older/P1', None, 1))

    def test_step_d_with_unusable_ref_forms_a_group_via_a_cross_project_repeat(self):
        _, outside, _ = self.repo_with_history(self.library)
        self.past('older', [], [('library', LIB, 'applied', outside)])
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 1, 'G1'))
        self.assertIn('ref-not-in-history:older/P1', data['warnings'])
        self.assertIsNone(data['groups'][0]['recurrence_of'])
        # bez powtórzenia w innym projekcie: jak bez `applied`, czyli bez grupy
        bare = self.scan([sig('S1', 'flaky')])
        self.assertEqual(self.local(bare), (None, 0, None))

    def test_from_failure_signal_is_still_excluded_from_groups(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky', from_failure=True)])
        self.assertEqual(self.local(data), (None, 1, None))


class CrossProjectSelection(CrossBase):
    def excluding(self, value):
        self.use_layers(discover_paths=[str(self.repos)], tags={}, discover_exclude=value)

    def archive_fixture(self):
        self.other('live', ('r1', [('flaky', 'library', LIB)], ()))
        self.other('old', ('r1', [('flaky', 'library', LIB)], ()), under=self.repos / 'archive' / 'org')

    def test_nothing_is_excluded_by_default_even_under_repos_archive(self):
        self.archive_fixture()
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual((self.local(data), data['sources']['projects']), ((None, 2, 'G1'), 2))

    def test_excluded_path_and_its_subdirectories_are_not_read(self):
        self.archive_fixture()
        self.excluding([str(self.repos / 'archive')])
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual((self.local(data), data['sources']['projects']), ((None, 1, 'G1'), 1))

    def test_excluding_a_project_path_itself(self):
        self.archive_fixture()
        self.excluding([str(self.repos / 'archive' / 'org' / 'old')])
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(data['sources']['projects'], 1)

    def test_excluded_path_expands_tilde(self):
        self.archive_fixture()
        self.excluding(['~/Repos/archive'])
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(data['sources']['projects'], 1)

    def test_nonexistent_exclusion_excludes_nothing_without_error(self):
        self.archive_fixture()
        self.excluding([str(self.repos / 'no-such-dir')])
        self.assertEqual(self.cross([sig('S1', 'flaky')])['sources']['projects'], 2)

    def test_exclusion_given_as_a_symlink_excludes_its_target(self):
        self.archive_fixture()
        link = self.base / 'link-to-archive'
        os.symlink(self.repos / 'archive', link)
        self.excluding([str(link)])
        self.assertEqual(self.cross([sig('S1', 'flaky')])['sources']['projects'], 1)

    def test_unresolvable_exclusion_is_blocked_with_a_fixed_reason(self):
        self.archive_fixture()
        self.excluding(['~no-such-user-zz/x'])
        data = self.cross([sig('S1', 'flaky')], code=2)
        self.assertEqual(data['reason'],
                         'Konfiguracja maszyny: discover_exclude zawiera ścieżkę, której nie da się rozwiązać')
        self.assertNotIn(str(self.base), json.dumps(data))

    def test_wrong_discover_exclude_type_is_blocked_with_a_fixed_reason(self):
        self.archive_fixture()
        for bad in ('archive', [1], ['']):
            self.excluding(bad)
            data = self.cross([sig('S1', 'flaky')], code=2)
            self.assertEqual(data['reason'], 'Konfiguracja maszyny: discover_exclude musi być listą niepustych ścieżek')
            self.assertNotIn(str(self.base), json.dumps(data))

    def test_project_and_its_worktree_count_once(self):
        main = self.repos / 'multi'
        self.init_repo(main)
        with self.at(main):
            self.save()
        commit_file(main, '.github/.skillsync.yaml', yaml.safe_dump(self.manifest), 'manifest')
        git(main, 'worktree', 'add', '-q', '-b', 'feat', str(self.repos / 'multi-wt'))
        for root in (main, self.repos / 'multi-wt'):
            with self.at(root):
                self.past('r-' + root.name, [('flaky', 'library', LIB)])
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual((self.local(data), data['sources']['projects']), ((None, 1, 'G1'), 1))

    def test_current_project_as_a_worktree_does_not_count_its_main_checkout_as_another_project(self):
        shutil.rmtree(self.root)
        main = self.repos / 'primary'
        self.init_repo(main)
        with self.at(main):
            self.save()
        commit_file(main, '.github/.skillsync.yaml', yaml.safe_dump(self.manifest), 'manifest')
        git(main, 'worktree', 'add', '-q', '-b', 'feat', str(self.repos / 'current'))
        with self.at(main):
            self.past('mainrun', [('flaky', 'library', LIB)])
        self.other('beta', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), ('mainrun/S1', 1, 'G1'), 'główny checkout czytany raz, jako główny')
        self.assertEqual((data['worktree'], data['sources']['main_checkout'], data['sources']['projects']),
                         (True, 1, 1))

    def test_current_project_is_not_counted_as_another_project(self):
        self.past('older', [('flaky', 'library', LIB)])
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual((self.local(data), data['sources']['projects']), (('older/S1', 0, 'G1'), 0))

    @needs_os_symlinks
    def test_symlinked_project_directory_is_not_discovered(self):
        real = self.other('real', ('r1', [('flaky', 'library', LIB)], ()), under=self.base / 'elsewhere')
        os.symlink(real, self.repos / 'linked')
        self.assertEqual(self.local(self.cross([sig('S1', 'flaky')])), (None, 0, None))

    def test_unreadable_project_is_skipped_with_a_nameless_warning_and_the_rest_is_read(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        broken = self.other('zebra-secret-name')
        (broken / '.skillsync').write_text('plik zamiast katalogu')
        result = self.run_scan(self.put_input([sig('S1', 'flaky')]), extra=['--cross-project'])
        data = self.parsed(result, 0)
        self.check_shape(data)
        self.assertEqual(self.local(data), (None, 1, 'G1'))
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')], ['source-skipped:project'])
        self.assertNotIn('zebra-secret-name', result.stdout)

    @needs_os_symlinks
    def test_symlinks_in_another_project_are_not_followed(self):
        alpha = self.other('alpha')
        outside = self.base / 'outside'
        with self.at(outside):
            self.past('linked', [('flaky', 'library', LIB)])
        (alpha / '.skillsync/runs').mkdir(parents=True)
        os.symlink(outside / '.skillsync/runs/linked', alpha / '.skillsync/runs/linked-dir')
        file_run = alpha / '.skillsync/runs/file-run'
        file_run.mkdir()
        os.symlink(outside / '.skillsync/runs/linked/retro-signals.json', file_run / 'retro-signals.json')
        self.assertEqual(self.local(self.cross([sig('S1', 'flaky')])), (None, 0, None))
        beta = self.other('beta')
        os.symlink(outside / '.skillsync', beta / '.skillsync')
        data = self.cross([sig('S1', 'flaky')])
        self.assertEqual(self.local(data), (None, 0, None))


class CrossProjectHardening(CrossBase):
    def test_unreadable_machine_config_gives_a_constant_reason(self):
        import yaml as yaml_module
        from skillsync_workflow import retro
        secret = str(self.base) + '/secret-config.yaml'
        for error in (ValueError('Expected YAML mapping: ' + secret), yaml_module.YAMLError('while parsing ' + secret),
                      OSError(secret), UnicodeDecodeError('utf-8', b'x', 0, 1, secret)):
            with self.subTest(type(error).__name__):
                code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro, 'read_yaml',
                                                                                   side_effect=error)],
                                             extra=['--cross-project'])
                self.assertEqual((code, data['status']), (2, 'BLOCKED'))
                self.assertEqual(data['reason'], 'Konfiguracja maszyny jest nieczytelna')
                self.assertNotIn(str(self.base), json.dumps(data))
                self.assertNotIn('secret-config', json.dumps(data))

    def test_foreign_git_environment_does_not_break_worktree_deduplication(self):
        self.init_repo(self.base / 'other-repo')
        main = self.repos / 'multi'
        self.init_repo(main)
        with self.at(main):
            self.save()
        commit_file(main, '.github/.skillsync.yaml', yaml.safe_dump(self.manifest), 'manifest')
        git(main, 'worktree', 'add', '-q', '-b', 'feat', str(self.repos / 'multi-wt'))
        for root in (main, self.repos / 'multi-wt'):
            with self.at(root):
                self.past('r-' + root.name, [('flaky', 'library', LIB)])
        rel = self.put_input([sig('S1', 'flaky')])
        other = self.base / 'other-repo'
        for env in ({}, {'GIT_DIR': str(other / '.git')}, {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}):
            with self.subTest(env):
                data = self.parsed(self.run_scan(rel, extra=['--cross-project'], env=env), 0)
                self.assertEqual((self.local(data), data['sources']['projects']), ((None, 1, 'G1'), 1))

    def test_foreign_git_environment_keeps_the_main_checkout_of_the_current_worktree_out_of_other_projects(self):
        shutil.rmtree(self.root)
        self.init_repo(self.base / 'other-repo')
        main = self.repos / 'primary'
        self.init_repo(main)
        with self.at(main):
            self.save()
        commit_file(main, '.github/.skillsync.yaml', yaml.safe_dump(self.manifest), 'manifest')
        git(main, 'worktree', 'add', '-q', '-b', 'feat', str(self.repos / 'current'))
        with self.at(main):
            self.past('mainrun', [('flaky', 'library', LIB)])
        rel = self.put_input([sig('S1', 'flaky')])
        env = {'GIT_DIR': str(self.base / 'other-repo/.git')}
        data = self.parsed(self.run_scan(rel, extra=['--cross-project'], env=env), 0)
        self.assertEqual(self.local(data), ('mainrun/S1', 0, 'G1'), 'główny checkout nie jest "innym projektem"')
        self.assertEqual(data['sources']['projects'], 0)


class UnexpectedForeignErrors(WorktreeBase):
    """Wyjątek spoza `(OSError, ValueError)` z obcego źródła pomija źródło, nie przerywa skanu."""

    def test_main_checkout_read_failure_skips_the_source(self):
        from skillsync_workflow import retro_scan
        original = retro_scan._read_source

        def failing(root, identity, budget=None, resolved=False):
            if resolved:
                raise RuntimeError('nieoczekiwany ' + str(root))
            return original(root, identity, budget, resolved)

        self.make_worktree()
        self.main_past('older', [('flaky', 'library', LIB)])
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro_scan, '_read_source', failing)])
        self.assertEqual((code, data['status']), (0, 'PASS'))
        self.assertTrue(data['worktree'])
        self.assertIsNone(data['sources']['main_checkout'])
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')],
                         ['source-skipped:main-checkout'])
        self.assertNotIn(str(self.base), json.dumps(data))

    def test_cross_project_read_failure_skips_the_project_and_keeps_the_scan(self):
        from skillsync_workflow import retro_scan
        self.cross_setup()
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(
            retro_scan, 'library_signals', side_effect=RuntimeError('nieoczekiwany'))], extra=['--cross-project'])
        self.assertEqual((code, data['status']), (0, 'PASS'))
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')], ['source-skipped:project'])
        self.assertEqual((self.local(data), data['sources']['projects']), ((None, 0, None), 0))

    def test_unverifiable_project_directory_is_skipped_with_a_warning(self):
        self.cross_setup()
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        self.other('zed', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.in_process([sig('S1', 'flaky')], [self.broken_is_file('zed')], extra=['--cross-project'])[1]
        self.assertEqual([w for w in data['warnings'] if w.startswith('source-skipped:')], ['source-skipped:project'])
        self.assertEqual((self.local(data), data['sources']['projects']), ((None, 1, 'G1'), 1))
        self.assertNotIn('zed', json.dumps(data))

    def test_oserror_in_the_scan_gives_a_constant_reason_without_the_path(self):
        from skillsync_workflow import retro_scan
        error = OSError(13, 'Permission denied', str(self.base / 'charakterystyczny-fragment'))
        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(retro_scan, '_scan', side_effect=error)])
        self.assertEqual((code, data['status']), (2, 'BLOCKED'))
        self.assertEqual(data['reason'], 'Błąd odczytu pliku')
        self.assertNotIn('charakterystyczny', json.dumps(data))

    def cross_setup(self):
        self.home = self.base / 'home'
        self.repos = self.home / 'Repos'
        self.repos.mkdir(parents=True)
        patch = mock.patch.dict(os.environ, {'HOME': str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.use_layers(discover_paths=[str(self.repos)], tags={})
        self.root = self.repos / 'current'
        self.root.mkdir()
        self.save()

    def other(self, name, *runs):
        root = self.repos / name
        root.mkdir(parents=True)
        with self.at(root):
            self.save()
            for run in runs:
                self.past(*run[:3])
        return root

    def local(self, data, ident='S1'):
        item = self.one(data, ident)
        return item['repeat_of'], item['cross_project_repeats'], item['group']

    @staticmethod
    def broken_is_file(name):
        real = Path.is_file

        def is_file(path, *args, **kwargs):
            if name in path.parts and path.name == '.skillsync.yaml':
                raise PermissionError(13, 'Permission denied', str(path))
            return real(path, *args, **kwargs)

        return mock.patch.object(Path, 'is_file', is_file)


class CrossProjectRace(CrossBase):
    def test_directory_swapped_for_a_symlink_between_check_and_open_is_refused(self):
        alpha = os.path.realpath(self.other('alpha', ('r1', [('other', 'library', 'skills/z.md')], ())))
        with self.at(self.base / 'outside'):
            self.past('r1', [('flaky', 'library', LIB)])
        run_dir = os.path.join(alpha, '.skillsync/runs/r1')
        target = os.path.join(run_dir, 'retro-signals.json')
        real_open, swapped = os.open, []

        def swapping_open(path, *args, **kwargs):
            if os.fspath(path) == target and not swapped:
                swapped.append(True)
                os.rename(run_dir, run_dir + '-moved')
                os.symlink(self.base / 'outside/.skillsync/runs/r1', run_dir)
            return real_open(path, *args, **kwargs)

        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(os, 'open', swapping_open)],
                                     extra=['--cross-project'])
        self.assertEqual(swapped, [True], 'podmiana katalogu musiała nastąpić')
        self.assertEqual(code, 0)
        self.assertEqual(self.local(data), (None, 0, None), 'treść spoza drzewa projektu nie może być czytana')


class CrossProjectRaceAfterOpen(CrossBase):
    """Podmiana między otwarciem pliku a `realpath` (hook na `os.path.realpath` dla ścieżki pliku)."""

    def setup_alpha(self):
        alpha = os.path.realpath(self.other('alpha', ('r1', [('flaky', 'library', LIB)], ())))
        run_dir = os.path.join(alpha, '.skillsync/runs/r1')
        return run_dir, os.path.join(run_dir, 'retro-signals.json')

    def scan_with_hook(self, target, action):
        real, calls = os.path.realpath, []

        def hooked(path, *args, **kwargs):
            if os.fspath(path) == target and not calls:
                calls.append(True)
                action()
            return real(path, *args, **kwargs)

        code, data = self.in_process([sig('S1', 'flaky')], [mock.patch.object(os.path, 'realpath', hooked)],
                                     extra=['--cross-project'])
        self.assertEqual((calls, code), ([True], 0), 'podmiana musiała nastąpić')
        return data

    def test_directory_swapped_after_open_is_refused(self):
        run_dir, target = self.setup_alpha()
        with self.at(self.base / 'outside'):
            self.past('r1', [('flaky', 'library', LIB)])

        def swap():
            os.rename(run_dir, run_dir + '-moved')
            os.symlink(self.base / 'outside/.skillsync/runs/r1', run_dir)

        self.assertEqual(self.local(self.scan_with_hook(target, swap)), (None, 0, None))

    def test_directory_swapped_to_a_symlink_onto_the_same_inode_is_refused_by_path(self):
        run_dir, target = self.setup_alpha()
        evil = self.base / 'outside-same-inode'
        evil.mkdir()
        os.link(target, evil / 'retro-signals.json')

        def swap():
            os.rename(run_dir, run_dir + '-moved')
            os.symlink(evil, run_dir)

        self.assertEqual(self.local(self.scan_with_hook(target, swap)), (None, 0, None))

    def test_file_replaced_after_open_is_refused(self):
        run_dir, target = self.setup_alpha()

        def replace():
            Path(target + '.new').write_text(Path(target).read_text())
            os.replace(target + '.new', target)

        self.assertEqual(self.local(self.scan_with_hook(target, replace)), (None, 0, None))

    def test_unswapped_read_counts(self):
        run_dir, target = self.setup_alpha()
        self.assertEqual(self.local(self.scan_with_hook(target, lambda: None)), (None, 1, 'G1'))

    def test_hard_linked_signals_file_is_an_ordinary_file(self):
        run_dir, target = self.setup_alpha()
        outside = self.base / 'outside-file.json'
        shutil.move(target, outside)
        os.link(outside, target)
        self.assertEqual(os.stat(target).st_nlink, 2)
        self.assertEqual(self.local(self.cross([sig('S1', 'flaky')])), (None, 1, 'G1'),
                         'twarde dowiązanie ujawnia tylko parę klucz/cel, jak zwykły plik')


class CrossProjectPrivacy(CrossBase):
    def test_result_names_no_other_project_path_or_run_id(self):
        self.other('zebra-secret-name', ('secret-run-id', [('flaky', 'library', LIB)], [('library', LIB, 'open')]))
        self.other('yak-hidden-name', ('2026-11-04-hidden-run', [('flaky', 'library', LIB)], ()))
        self.keys_file({'flaky': []})
        result = self.run_scan(self.put_input([sig('S1', 'flaky'), sig('S2', 'user-correction')]),
                               extra=['--cross-project'])
        data = self.parsed(result, 0)
        self.check_shape(data)
        self.assertEqual(self.local(data), (None, 2, 'G1'))
        text = json.dumps(data) + result.stdout + result.stderr
        for secret in ('zebra-secret-name', 'yak-hidden-name', 'secret-run-id', 'hidden-run', str(self.base),
                       self.base.name, str(self.repos), 'Repos', '/home'):
            self.assertNotIn(secret, text)

    def test_cross_project_reading_writes_nothing(self):
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], [('library', LIB, 'open')]))
        rel = self.put_input([sig('S1', 'flaky')])
        before = tree_state(self.home)
        data = self.parsed(self.run_scan(rel, extra=['--cross-project']), 0)
        self.assertEqual(self.local(data), (None, 1, 'G1'))
        self.assertEqual(tree_state(self.home), before)


class CrossProjectLimits(CrossBase):
    def run_limited(self, patches, signals=None):
        code, data = self.in_process(signals or [sig('S1', 'flaky')], patches, extra=['--cross-project'])
        self.assertEqual(code, 0)
        return data

    def test_default_project_limit_is_200(self):
        from skillsync_workflow import retro_scan
        self.assertEqual(retro_scan.MAX_PROJECTS, 200)

    def test_project_limit_truncates_with_a_warning(self):
        from skillsync_workflow import retro_scan
        for name in ('alpha', 'beta'):
            self.other(name, ('r1', [('flaky', 'library', LIB)], ()))
        data = self.run_limited([mock.patch.object(retro_scan, 'MAX_PROJECTS', 1)])
        self.assertTrue(data['truncated'])
        self.assertEqual(data['warnings'].count('truncated'), 1)
        self.assertEqual((self.local(data)[1], data['sources']['projects']), (1, 1))

    def test_exactly_the_project_limit_is_not_truncated(self):
        from skillsync_workflow import retro_scan
        for name in ('alpha', 'beta'):
            self.other(name, ('r1', [('flaky', 'library', LIB)], ()))
        data = self.run_limited([mock.patch.object(retro_scan, 'MAX_PROJECTS', 2)])
        self.assertFalse(data['truncated'])
        self.assertNotIn('truncated', data['warnings'])
        self.assertEqual((self.local(data)[1], data['sources']['projects']), (2, 2))

    def test_shared_byte_budget_truncates(self):
        from skillsync_workflow import retro_scan
        for name in ('alpha', 'beta'):
            self.other(name, ('r1', [('flaky', 'library', LIB)], ()))
        size = (self.repos / 'alpha/.skillsync/runs/r1/retro-signals.json').stat().st_size
        data = self.run_limited([mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', size)])
        self.assertTrue(data['truncated'])
        self.assertIn('truncated', data['warnings'])
        self.assertEqual(self.local(data)[1], 1, 'drugi projekt nie mieści się w budżecie')
        enough = self.run_limited([mock.patch.object(retro_scan, 'MAX_SCAN_BYTES', 2 * size)])
        self.assertFalse(enough['truncated'])
        self.assertEqual(self.local(enough)[1], 2)

    def test_run_limit_per_project_truncates(self):
        from skillsync_workflow import retro_scan
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()), ('r2', [('zzz', 'library', LIB)], ()))
        data = self.run_limited([mock.patch.object(retro_scan, 'MAX_RUNS', 1)])
        self.assertTrue(data['truncated'])
        self.assertEqual(self.local(data)[1], 0, 'najnowszy przebieg r2 nie ma sygnału flaky, r1 nie jest czytany')
        enough = self.run_limited([mock.patch.object(retro_scan, 'MAX_RUNS', 2)])
        self.assertFalse(enough['truncated'])
        self.assertEqual(self.local(enough)[1], 1)

    def test_exhausted_git_budget_marks_the_result_truncated(self):
        from skillsync_workflow import retro
        self.other('alpha', ('r1', [('flaky', 'library', LIB)], ()))
        data = self.run_limited([mock.patch.object(retro, 'GIT_BUDGET', 0)])
        self.assertTrue(data['truncated'])
        self.assertIn('truncated', data['warnings'])


if __name__ == '__main__':
    unittest.main()
