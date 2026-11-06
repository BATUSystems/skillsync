"""Criteria tests of the leak guard: K6 (surfaces, modes, inflections), K7 (generic
patterns, coverage, target additions), K8 (path class table, publisher exception,
attributions, authors) and git hooks.

All names that the guard must catch are SYNTHETIC and composed at runtime (no real
client names, no literal D27 trace in this file). Every test runs in a temporary,
isolated environment (tests/layers_helpers.py); nothing is written outside it.
"""
import json
import unittest
import os
import subprocess
import sys
import unicodedata
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from layers_helpers import (IsolatedTestCase, LINK_MODES, REAL_HOME, commit, commit_file, git,  # noqa: E402
                            link_mode, real_state_snapshot, write)
from skillsync_workflow import leakguard  # noqa: E402
from layers_helpers import needs_os_symlinks, needs_sh  # noqa: E402
from layers_helpers import LayersCase  # noqa: E402

HOME = '/ho' + 'me'
AT = '@'
USERS = 'Us' + 'ers'
ROOT = Path(leakguard.__file__).resolve().parent.parent
from cli_module import SKILLSYNC_PATH  # noqa: E402

SCRIPT = SKILLSYNC_PATH  # wrapper; processes run [sys.executable, SCRIPT]

# ---- synthetic names, composed at runtime
CLIENT = 'Brenk' + 'or'              # client organisation term (stem ending in a consonant)
CLIENT_LOW = CLIENT.lower()
OTHER_CLIENT = 'Zulv' + 'ek'
NICK = 'Skrz' + 'at'                 # "owner nick"
PERSONA = 'Gno' + 'mek'              # "persona / card name"
OWNER_NAME = 'Zbig' + 'niew Wro' + 'na'
PUBLISHER = 'BATU' + 'Systems'       # publisher name (public); used in URL exception tests
SENSITIVE = 'Mr' + 'uk'              # ordinary word that is also a term: case: sensitive
SHORT = 'Qz' + 'v'                   # term shorter than 4 characters
DIAC = 'Żur' + 'awik'                # term with diacritics
D27_SOURCE = 'Open' + ' ' + 'Mer' + 'cato'
D27_SKILL = 'om-' + 'spec-auto'
D27_PERSON = 'Ce' + 'zar'
D27_ICON_A = 'Lu' + 'cide'
D27_ICON_B = 'Fea' + 'ther'
ZEROS = '0' * 40

# Polish endings from the spec (K7), independent of the shipped resource.
SPEC_SUFFIXES = ['a', 'u', 'e', 'y', 'i', 'ie', 'ze', 'ce', 'em', 'om', 'ów', 'ach', 'ami', 'owi', 'owie',
                 'ą', 'ę', 'ego', 'emu', 'ym', 'ich']


def term(value, kind='org', **extra):
    return {'term': value, 'kind': kind, **extra}


def signature(report):
    return sorted((item['path'], item['category']) for item in report['findings'])


def categories(report):
    return {item['category'] for item in report['findings']}


def surface_of(finding):
    if finding['path'].startswith('@commit') or finding['path'].startswith('@staged'):
        return 'commit'
    if 'filename' in finding['location']:
        return 'filename'
    return 'content'


class LeakTestCase(IsolatedTestCase):
    counter = 0

    def fresh_dir(self, prefix='case'):
        LeakTestCase.counter += 1
        path = self.work / f'{prefix}-{LeakTestCase.counter}'
        path.mkdir(parents=True)
        return path

    def tree(self, files):
        """Plain directory (no git) with `files` {rel: text}."""
        path = self.fresh_dir('tree')
        for rel, text in files.items():
            write(path / rel, text)
        return path

    def ident(self, layer, terms, name=None):
        path = self.root / 'idents' / (name or f'{layer}.yaml')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({'schema': 1, 'layer': layer, 'terms': terms}, allow_unicode=True),
                        encoding='utf-8')
        return path

    def targets_file(self, targets):
        path = self.root / 'targets.json'
        path.write_text(json.dumps(targets), encoding='utf-8')
        return path

    def check(self, path, terms=None, layer='other', target='mine', target_kind='org', targets=None, **kwargs):
        """leakcheck with one foreign identity layer holding `terms` (target `mine`)."""
        identity_files = list(kwargs.pop('identity_files', []))
        if terms is not None:
            identity_files.append(self.ident(layer, terms))
        if targets is None:
            targets = {target: {'scope': 'org-private', 'kind': target_kind}}
            if terms is not None:
                targets[layer] = {'scope': 'private'}
        return leakguard.leakcheck(path, target=target, targets=targets, identity_files=identity_files, **kwargs)

    def cli(self, *args, cwd=None):
        process = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                                 cwd=str(cwd or self.work), env=dict(os.environ))
        return process

    def status_of_cell(self, rel, text, term_kind, term_value, target_kind, **term_extra):
        path = self.tree({rel: text})
        return self.check(path, [term(term_value, term_kind, **term_extra)], target_kind=target_kind)['status']


# ====================================================================== K6: surfaces


class SurfacesTest(LeakTestCase):
    def test_content_leak_fails_with_category_and_no_value(self):
        path = self.tree({'skills/a.md': f'intro\nsee {CLIENT} here\n'})
        report = self.check(path, [term(CLIENT)])
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(len(report['findings']), 1)
        finding = report['findings'][0]
        self.assertEqual((finding['path'], finding['category']), ('skills/a.md', 'identity-org'))
        self.assertEqual(finding['location'].split(':')[0], '2')
        self.assertEqual(set(finding), {'path', 'category', 'location'})
        self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())

    def test_clean_tree_passes(self):
        path = self.tree({'skills/a.md': 'nothing to see\n', 'docs/b.md': 'plain\n'})
        self.assertEqual(self.check(path, [term(CLIENT)])['status'], 'PASS')

    def test_filename_leak_fails_on_filename_surface(self):
        path = self.tree({f'skills/notes-{CLIENT_LOW}.md': 'clean body\n'})
        report = self.check(path, [term(CLIENT)])
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual({surface_of(item) for item in report['findings']}, {'filename'})

    def test_filename_identifier_forms_fail(self):
        for name in (f'{CLIENT}-Portal.md', f'{CLIENT_LOW}.org.txt', f'{CLIENT}Portal.md'):
            with self.subTest(name=name):
                path = self.tree({f'skills/{name}': 'clean\n'})
                self.assertEqual(self.check(path, [term(CLIENT)])['status'], 'FAIL')

    def test_commit_metadata_fields_fail_in_history_and_range(self):
        cases = {
            'author-name': dict(author=(f'{CLIENT} Dev', f'a{AT}example.test'), message='clean'),
            'committer-name': dict(committer=(f'{CLIENT} Dev', f'c{AT}example.test'), message='clean'),
            'author-email': dict(author=('Dev', f'dev@{CLIENT_LOW}.org'), message='clean'),
            'message-subject': dict(message=f'fix for {CLIENT}a'),
            'message-body': dict(message=f'clean subject\n\nbody mentions {CLIENT} here'),
            'trailer-name': dict(message=f'clean\n\nCo-Authored-By: Jan {CLIENT} <jan{AT}example.test>'),
        }
        for label, fields in cases.items():
            with self.subTest(field=label):
                repo = self.make_repo(f'meta-{label}')
                base = commit_file(repo, 'a.md', 'base\n', 'base commit')
                head = commit_file(repo, 'b.md', 'clean\n', fields['message'],
                                   author=fields.get('author'), committer=fields.get('committer'))
                for mode, extra in (('history', {}), ('range', {'rev_range': f'{base}..{head}'})):
                    report = self.check(repo, [term(CLIENT)], mode=mode, **extra)
                    self.assertEqual(report['status'], 'FAIL', (mode, report))
                    self.assertEqual({surface_of(item) for item in report['findings']}, {'commit'}, mode)
                    self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())

    def test_clean_commit_metadata_passes(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'base\n', f'subject\n\nCo-Authored-By: Jan <jan{AT}example.test>')
        for mode in ('history', 'range'):
            extra = {'rev_range': 'HEAD'} if mode == 'range' else {}
            self.assertEqual(self.check(repo, [term(CLIENT)], mode=mode, **extra)['status'], 'PASS', mode)

    def test_report_has_no_values_for_any_text_output(self):
        path = self.tree({'skills/a.md': f'{CLIENT} and {D27_SOURCE} and {HOME}/alice/x and jan{AT}corp.test\n'})
        ident = self.ident('other', [term(CLIENT)])
        targets = self.targets_file({'mine': {'scope': 'org-private', 'kind': 'org'}, 'other': {'scope': 'private'}})
        for json_flag in ([], ['--json']):
            process = self.cli('leakcheck', str(path), '--identity', str(ident), '--targets-json', str(targets),
                               '--target', 'mine', *json_flag)
            self.assertEqual(process.returncode, 1, process.stderr)
            output = (process.stdout + process.stderr).lower()
            for secret in (CLIENT_LOW, D27_SOURCE.lower(), 'alice', 'corp.test', D27_SOURCE.split()[1].lower()):
                self.assertNotIn(secret, output, json_flag)


# ====================================================================== K6: modes


class ModesTest(LeakTestCase):
    def test_worktree_sees_untracked_and_modified_files(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'clean\n', 'init')
        self.assertEqual(self.check(repo, [term(CLIENT)])['status'], 'PASS')
        write(repo / 'new.md', f'{CLIENT}\n')
        self.assertEqual(self.check(repo, [term(CLIENT)])['status'], 'FAIL')
        (repo / 'new.md').unlink()
        write(repo / 'a.md', f'{CLIENT}\n')
        self.assertEqual(self.check(repo, [term(CLIENT)])['status'], 'FAIL')

    def test_staged_reads_the_index_not_the_worktree(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'clean\n', 'init')
        write(repo / 'a.md', f'{CLIENT}\n')
        self.assertEqual(self.check(repo, [term(CLIENT)], mode='staged')['status'], 'PASS')  # not staged
        git(repo, 'add', 'a.md')
        report = self.check(repo, [term(CLIENT)], mode='staged')
        self.assertEqual(report['status'], 'FAIL')
        write(repo / 'a.md', 'clean again\n')  # worktree clean, index still leaking
        self.assertEqual(self.check(repo, [term(CLIENT)])['status'], 'PASS')
        self.assertEqual(self.check(repo, [term(CLIENT)], mode='staged')['status'], 'FAIL')

    def test_staged_filename_leak(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'clean\n', 'init')
        write(repo / f'{CLIENT_LOW}.md', 'clean\n')
        git(repo, 'add', '--', f'{CLIENT_LOW}.md')
        report = self.check(repo, [term(CLIENT)], mode='staged')
        self.assertEqual({surface_of(item) for item in report['findings']}, {'filename'})

    def test_history_finds_leak_removed_from_worktree(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', f'{CLIENT}\n', 'leak')
        commit_file(repo, 'a.md', 'fixed\n', 'fix')
        self.assertEqual(self.check(repo, [term(CLIENT)])['status'], 'PASS')
        report = self.check(repo, [term(CLIENT)], mode='history')
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(all(item['path'] == 'a.md' for item in report['findings']))

    def test_range_only_scans_commits_in_the_range(self):
        repo = self.make_repo()
        old = commit_file(repo, 'a.md', f'{CLIENT}\n', 'old leak')
        mid = commit_file(repo, 'a.md', 'fixed\n', 'fix')
        new = commit_file(repo, 'b.md', 'clean\n', 'clean')
        self.assertEqual(self.check(repo, [term(CLIENT)], mode='range', rev_range=f'{mid}..{new}')['status'], 'PASS')
        self.assertEqual(self.check(repo, [term(CLIENT)], mode='range', rev_range=f'{old}..{new}')['status'], 'PASS')
        self.assertEqual(self.check(repo, [term(CLIENT)], mode='range', rev_range=f'{old}~0')['status'], 'FAIL')
        leaking = commit_file(repo, 'c.md', f'{CLIENT}\n', 'new leak')
        report = self.check(repo, [term(CLIENT)], mode='range', rev_range=f'{new}..{leaking}')
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual({item['path'] for item in report['findings']}, {'c.md'})

    def test_git_modes_outside_a_repository_are_blocked(self):
        path = self.tree({'a.md': 'clean\n'})
        for mode, extra in (('staged', {}), ('history', {}), ('range', {'rev_range': 'HEAD'})):
            with self.subTest(mode=mode):
                self.assertEqual(self.check(path, [term(CLIENT)], mode=mode, **extra)['status'], 'BLOCKED')

    def test_cli_modes_and_exit_codes(self):
        repo = self.make_repo()
        base = commit_file(repo, 'a.md', 'clean\n', 'init')
        head = commit_file(repo, 'b.md', f'{CLIENT}\n', 'leak')
        ident = self.ident('other', [term(CLIENT)])
        targets = self.targets_file({'mine': {'scope': 'org-private', 'kind': 'org'}, 'other': {'scope': 'private'}})
        common = ['--identity', str(ident), '--targets-json', str(targets), '--target', 'mine', '--json']
        cases = [(['--worktree'], 1), (['--history'], 1), (['--range', f'{base}..{head}'], 1),
                 (['--range', f'{head}..{head}'], 0), (['--staged'], 0)]
        for flags, expected in cases:
            with self.subTest(flags=flags):
                process = self.cli('leakcheck', str(repo), *flags, *common)
                self.assertEqual(process.returncode, expected, process.stderr)
                payload = json.loads(process.stdout)
                self.assertEqual(payload['schema'], 1)
                self.assertEqual(set(payload) >= {'status', 'coverage', 'target', 'findings'}, True)
                self.assertEqual(payload['status'], 'FAIL' if expected else 'PASS')
        blocked = self.cli('leakcheck', str(self.tree({'a.md': 'x\n'})), '--staged', '--json')
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(json.loads(blocked.stdout)['status'], 'BLOCKED')


# ====================================================================== K6: matching


class MatchingTest(LeakTestCase):
    def hit(self, text, terms=None, rel='skills/a.md'):
        terms = terms if terms is not None else [term(CLIENT)]
        return self.check(self.tree({rel: text + '\n'}), terms)['status'] == 'FAIL'

    def test_polish_inflections_table(self):
        for suffix in SPEC_SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertTrue(self.hit(f'bylismy u {CLIENT}{suffix} wczoraj'))
                self.assertTrue(self.hit(f'bylismy u {CLIENT.upper()}{suffix.upper()}'))

    def test_inflection_without_diacritics_and_non_inflections(self):
        self.assertTrue(self.hit(f'{CLIENT}ow'))
        self.assertTrue(self.hit(f'{CLIENT}ze'))
        for word in (f'{CLIENT}ium', f'a{CLIENT_LOW}', f'{CLIENT}xyz', f'{CLIENT}ski'):
            with self.subTest(word=word):
                self.assertFalse(self.hit(f'text {word} text'))

    def test_shipped_suffix_resource_equals_spec_list(self):
        def fold(value):
            return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c)).lower()
        self.assertEqual(set(leakguard.load_suffixes()), {fold(item) for item in SPEC_SUFFIXES})

    def test_suffix_list_drives_matching(self):
        reduced = self.root / 'suffixes.txt'
        reduced.write_text('# only one ending\nem\n', encoding='utf-8')
        path = self.tree({'skills/a.md': f'{CLIENT}owi\n{CLIENT}em\n'})
        report = self.check(path, [term(CLIENT)], suffixes_file=reduced)
        self.assertEqual([item['location'].split(':')[0] for item in report['findings']], ['2'])

    def test_identifiers(self):
        for text in (f'{CLIENT}-Portal', f'ACME-{CLIENT}', f'{CLIENT_LOW}.org', f'https://{CLIENT_LOW}.org/x',
                     f'{CLIENT}Portal', f'portal{CLIENT}', f'{CLIENT.upper()}_CONFIG', f'my_{CLIENT_LOW}_key',
                     f'{CLIENT_LOW}-portal-x', f'(\"{CLIENT}\")', f'**{CLIENT}**'):
            with self.subTest(text=text):
                self.assertTrue(self.hit(text))

    def test_multi_word_term_variants(self):
        terms = [term(f'{CLIENT} Corp')]
        for text in (f'{CLIENT} Corp', f'{CLIENT_LOW}-corp', f'{CLIENT}Corp', f'{CLIENT_LOW}corp',
                     f'{CLIENT}_corp', f'{CLIENT} Corpem'):
            with self.subTest(text=text):
                self.assertTrue(self.hit(text, terms))
        for text in (f'{CLIENT} only', 'Corp alone', f'{CLIENT} big Corp'):
            with self.subTest(text=text):
                self.assertFalse(self.hit(text, terms))

    def test_diacritics_both_directions(self):
        ascii_form = unicodedata.normalize('NFKD', DIAC).encode('ascii', 'ignore').decode()
        self.assertNotEqual(ascii_form, DIAC)
        for text in (DIAC, DIAC.upper(), ascii_form, ascii_form.upper(), f'{DIAC}a', f'{ascii_form}ow'):
            with self.subTest(text=text):
                self.assertTrue(self.hit(f'x {text} y', [term(DIAC)]))
                self.assertTrue(self.hit(f'x {text} y', [term(ascii_form)]))

    def test_decomposed_diacritics_are_matched(self):
        decomposed = unicodedata.normalize('NFD', DIAC)
        self.assertNotEqual(decomposed, DIAC)
        self.assertTrue(self.hit(f'x {decomposed} y', [term(DIAC)]))

    def test_case_sensitive_term_ignores_ordinary_word(self):
        terms = [term(SENSITIVE, 'domain-term', case='sensitive')]
        self.assertFalse(self.hit(f'a plain {SENSITIVE.lower()} remark', terms))
        self.assertFalse(self.hit(f'a plain {SENSITIVE.upper()} remark', terms))
        self.assertFalse(self.hit(f'a plain {SENSITIVE}a remark', terms))  # no endings when sensitive
        self.assertTrue(self.hit(f'card {SENSITIVE} says', terms))
        self.assertTrue(self.hit(f'card {SENSITIVE}.', terms))

    def test_short_term_matches_exactly_only(self):
        terms = [term(SHORT, 'domain-term')]
        for text in (SHORT.lower(), SHORT.upper(), f'{SHORT}em', f'{SHORT}x', f'x{SHORT.lower()}'):
            with self.subTest(text=text):
                self.assertFalse(self.hit(f'word {text} word', terms))
        self.assertTrue(self.hit(f'word {SHORT} word', terms))
        self.assertTrue(self.hit(f'word {SHORT}-x word', terms))

    def test_case_sensitive_false_hit_is_not_a_failure_in_report(self):
        path = self.tree({'skills/a.md': f'{SENSITIVE.lower()} {SENSITIVE.lower()}a\n'})
        report = self.check(path, [term(SENSITIVE, 'domain-term', case='sensitive')])
        self.assertEqual((report['status'], report['findings']), ('PASS', []))

    def test_own_layer_identity_is_not_a_leak(self):
        path = self.tree({'skills/a.md': f'{CLIENT}\n'})
        # even a term that would be forbidden for this target by the table (allow_in elsewhere)
        report = self.check(path, [term(CLIENT, allow_in=['elsewhere'])], layer='mine',
                            targets={'mine': {'scope': 'org-private'}})
        self.assertEqual(report['status'], 'PASS')
        report = self.check(path, [term(CLIENT, allow_in=['elsewhere'])], layer='foreign',
                            targets={'mine': {'scope': 'org-private'}, 'foreign': {'scope': 'private'}})
        self.assertEqual(report['status'], 'FAIL')


# ====================================================================== K7: generic patterns


class GenericPatternsTest(LeakTestCase):
    def scan(self, text, rel='skills/a.md', **kwargs):
        return leakguard.leakcheck(self.tree({rel: text + '\n'}), **kwargs)

    def test_d27_traces_in_any_file_fail(self):
        for text in (D27_SOURCE, D27_SOURCE.lower(), D27_SOURCE.replace(' ', '-'), D27_SOURCE.replace(' ', ''),
                     D27_SKILL, D27_PERSON, f'{D27_PERSON}em', D27_ICON_A, D27_ICON_B.lower()):
            for rel in ('skills/a.md', 'docs/x.md', 'notes/y.txt', 'z.py'):
                with self.subTest(text=text, rel=rel):
                    report = self.scan(f'see {text} now', rel)
                    self.assertEqual(report['status'], 'FAIL')
                    self.assertEqual(categories(report), {'d27'})
                    self.assertNotIn(text.lower(), json.dumps(report).lower())

    def test_d27_word_boundaries(self):
        for text in (f'{D27_PERSON}y', f'x{D27_ICON_A}', f'{D27_ICON_A}ity', f'{D27_ICON_B}s', f'x{D27_SOURCE}'):
            with self.subTest(text=text):
                self.assertEqual(self.scan(f'word {text} word')['status'], 'PASS')

    def generator_repo(self, value=True):
        return mock.patch.object(leakguard, '_generator_repo', return_value=value)

    def test_trace_inside_pattern_files_is_exempt_only_there(self):
        body = yaml.safe_dump({'schema': 1, 'patterns': [
            {'id': 'extra', 'category': 'custom', 'regex': D27_SOURCE.lower()}]}) + f'# note {D27_SOURCE}\n'
        # target additions: exempt in any repository
        self.assertEqual(self.scan(body, leakguard.TARGET_PATTERNS_REL)['status'], 'PASS')
        # generator resource: exempt only inside the generator repository
        with self.generator_repo(True):
            self.assertEqual(self.scan(body, leakguard.RESOURCE_REL)['status'], 'PASS')
        with self.generator_repo(False):
            report = self.scan(body, leakguard.RESOURCE_REL)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'d27'}))
        report = self.scan(body, leakguard.RESOURCE_REL)  # a foreign tree, no patch
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'d27'}))
        for rel in (leakguard.RESOURCE_REL, leakguard.TARGET_PATTERNS_REL):
            other = 'copy/' + Path(rel).name
            with self.subTest(other=other), self.generator_repo(True):
                report = self.scan(body, other)
                self.assertEqual(report['status'], 'FAIL')
                self.assertIn('d27', categories(report))

    def test_exemption_requires_a_valid_schema(self):
        body = yaml.safe_dump({'schema': 99, 'patterns': []}) + f'# {D27_SOURCE}\n'
        for rel in (leakguard.RESOURCE_REL, leakguard.TARGET_PATTERNS_REL):
            with self.subTest(rel=rel), self.generator_repo(True):
                report = self.scan(body, rel)
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'d27'}))

    def test_shipped_resource_carries_no_literal_trace(self):
        text = leakguard.PATTERNS_RESOURCE.read_text(encoding='utf-8')
        report = self.scan(text, 'elsewhere/patterns-copy.yaml')
        self.assertEqual((report['status'], report['findings']), ('PASS', []))
        suffixes = leakguard.SUFFIXES_RESOURCE.read_text(encoding='utf-8')
        self.assertEqual(self.scan(suffixes, 'elsewhere/suffixes-copy.txt')['status'], 'PASS')

    def test_home_paths_and_allowlist(self):
        for text in (f'{HOME}/alice/x', f'cd {HOME}/alice', f'{HOME}/user2/x', f'{HOME}/user.alice/x', f'{HOME}/useralice/x', f'{HOME}/Runner2/w',
                     f'C:\\{USERS}\\alice\\x', f'c:/{USERS.lower()}/alice/x', f'C:\\\\{USERS}\\\\alice\\\\x'):
            with self.subTest(text=text):
                report = self.scan(text)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual(categories(report), {'owner-path'})
        for text in (f'{HOME}/user/x', f'cd {HOME}/<user>/proj', f'{HOME}/runner/work/repo', f'{HOME}/user'):
            with self.subTest(text=text):
                self.assertEqual(self.scan(text)['status'], 'PASS')

    def test_allowlist_covers_only_the_placeholder_not_the_whole_line(self):
        report = self.scan(f'copy {HOME}/user/a to {HOME}/alice/b')
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(len(report['findings']), 1)

    def test_emails_and_placeholders(self):
        for text in (f'mail jan{AT}corp.test', f'jan.kowalski+tag{AT}sub.example-corp.com', f'x{AT}gmail.com'):
            with self.subTest(text=text):
                report = self.scan(text)
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'email'}))
                self.assertNotIn('corp', json.dumps(report))
        for text in (f'jan{AT}example.com', f'jan{AT}example.org', f'x{AT}host.example', f'x{AT}ci.invalid', f'a{AT}b.EXAMPLE'):
            with self.subTest(text=text):
                self.assertEqual(self.scan(text)['status'], 'PASS')

    def test_noreply_trailer_is_commit_scoped_and_trailer_allow_extends(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', f'subject\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply{AT}anthropic.com>')
        self.assertEqual(leakguard.leakcheck(repo, mode='history')['status'], 'PASS')
        commit_file(repo, 'b.md', 'x\n', f'more\n\nCo-Authored-By: Jan <jan{AT}corp.test>')
        report = leakguard.leakcheck(repo, mode='history')
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'email'}))
        report = leakguard.leakcheck(repo, mode='history', trailer_allow=[f'jan{AT}corp.test'])
        self.assertEqual(report['status'], 'PASS')
        # the exception is the address, not the whole line: a different address still fails
        commit_file(repo, 'c.md', 'x\n', f'third\n\nCo-Authored-By: Eva <eva{AT}corp.test>')
        self.assertEqual(leakguard.leakcheck(repo, mode='history', trailer_allow=[f'jan{AT}corp.test'])['status'],
                         'FAIL')
        # noreply address outside a commit trailer is an ordinary e-mail
        self.assertEqual(self.scan(f'contact noreply{AT}anthropic.com')['status'], 'FAIL')


# ====================================================================== K7: coverage


class CoverageTest(LeakTestCase):
    TARGETS = {
        'mine': {'scope': 'org-private', 'kind': 'org'},
        'alpha': {'scope': 'private'},
        'beta': {'scope': 'org-private'},
        'open': {'scope': 'public'},
    }

    def cov(self, layers, **kwargs):
        identities = [self.ident(layer, [term(f'{CLIENT_LOW}{layer}', 'org')]) for layer in layers]
        path = self.tree({'a.md': 'clean\n'})
        return leakguard.leakcheck(path, target=kwargs.pop('target', 'mine'), targets=kwargs.pop('targets', self.TARGETS),
                                   identity_files=identities, **kwargs)

    def test_full_partial_generic(self):
        self.assertEqual(self.cov(['alpha', 'beta'])['coverage'], 'full')
        self.assertEqual(self.cov(['alpha'])['coverage'], 'partial')
        self.assertEqual(self.cov([])['coverage'], 'generic')

    def test_public_targets_do_not_count_for_full(self):
        # `open` is public: no identity needed; adding its identity is not required for full
        self.assertEqual(self.cov(['alpha', 'beta'])['coverage'], 'full')
        # an identity of only a public target does not make coverage better than generic-or-partial
        report = self.cov(['open'])
        self.assertNotEqual(report['coverage'], 'full')

    def test_own_identity_does_not_count(self):
        self.assertEqual(self.cov(['mine'])['coverage'], 'generic')
        self.assertEqual(self.cov(['mine', 'alpha', 'beta'])['coverage'], 'full')

    def test_unknown_target_is_generic_with_warning_not_blocked(self):
        report = self.cov(['alpha', 'beta'], target='ghost')
        self.assertEqual((report['status'], report['coverage']), ('PASS', 'generic'))
        self.assertTrue(report['warnings'])
        self.assertEqual(report['findings'], [])

    def test_no_targets_configured_is_generic(self):
        report = leakguard.leakcheck(self.tree({'a.md': 'clean\n'}), target='mine')
        self.assertEqual((report['status'], report['coverage']), ('PASS', 'generic'))

    def test_require_full_blocks_without_full_coverage(self):
        for layers in ([], ['alpha']):
            with self.subTest(layers=layers):
                report = self.cov(layers, require='full')
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertTrue(report['reason'])
        self.assertEqual(self.cov(['alpha', 'beta'], require='full')['status'], 'PASS')
        self.assertEqual(self.cov([], require='full', target='ghost')['status'], 'BLOCKED')

    def test_require_full_cli_exit_codes(self):
        path = self.tree({'a.md': 'clean\n'})
        targets = self.targets_file(self.TARGETS)
        base = ['leakcheck', str(path), '--targets-json', str(targets), '--target', 'mine', '--require', 'full', '--json']
        blocked = self.cli(*base)
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertEqual(json.loads(blocked.stdout)['status'], 'BLOCKED')
        idents = []
        for layer in ('alpha', 'beta'):
            idents += ['--identity', str(self.ident(layer, [term(f'{CLIENT_LOW}{layer}')]))]
        ok = self.cli(*base, *idents)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['coverage'], 'full')

    def test_target_is_selected_from_remote(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', 'init')
        git(repo, 'remote', 'add', 'origin', f'git{AT}example.test:mine-org/repo.git')
        targets = dict(self.TARGETS)
        targets['mine'] = {'scope': 'org-private', 'kind': 'org', 'remote_match': ['*mine-org/*']}
        report = leakguard.leakcheck(repo, targets=targets)
        self.assertEqual(report['target'], 'mine')

    def test_invalid_targets_are_blocked(self):
        path = self.tree({'a.md': 'x\n'})
        for targets in ({'mine': {'scope': 'secret'}}, {'mine': 'nope'}, ['mine']):
            with self.subTest(targets=targets):
                self.assertEqual(leakguard.leakcheck(path, target='mine', targets=targets)['status'], 'BLOCKED')


# ====================================================================== D190: identity canary


class CanaryCoverageTest(LeakTestCase):
    """D190 (spec G1): a non-public layer whose loaded identity has no terms counts toward `full` only with `empty: <reason>`."""
    TARGETS = CoverageTest.TARGETS

    def identity(self, layer, terms=(), empty=None, name=None):
        data = {'schema': 1, 'layer': layer, 'terms': list(terms)}
        if empty is not None:
            data['empty'] = empty
        path = self.root / 'idents' / (name or f'{layer}.yaml')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
        return path

    def run_check(self, identities, **kwargs):
        return leakguard.leakcheck(self.tree({'a.md': 'clean\n'}), target=kwargs.pop('target', 'mine'),
                                   targets=kwargs.pop('targets', self.TARGETS), identity_files=identities, **kwargs)

    def empty_warnings(self, report):
        return [w for w in report['warnings'] if 'has no terms' in w]

    def test_zero_terms_without_empty_is_partial_with_a_warning(self):
        report = self.run_check([self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), self.identity('beta')])
        self.assertEqual(report['coverage'], 'partial')
        self.assertEqual(len(self.empty_warnings(report)), 1)
        self.assertIn('layer beta', self.empty_warnings(report)[0])
        self.assertIn('empty: <reason>', self.empty_warnings(report)[0])

    def test_every_needed_layer_empty_is_generic(self):
        report = self.run_check([self.identity('alpha'), self.identity('beta')])
        self.assertEqual(report['coverage'], 'generic')
        self.assertEqual(len(self.empty_warnings(report)), 2)

    def test_declared_empty_counts_toward_full(self):
        report = self.run_check([self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), self.identity('beta', empty='D179: cel bez terminów')])
        self.assertEqual(report['coverage'], 'full')
        self.assertEqual(self.empty_warnings(report), [])

    def test_terms_in_any_file_of_the_layer_count(self):
        report = self.run_check([self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), self.identity('beta', name='beta-empty.yaml'),
                                 self.identity('beta', [term(f'{CLIENT_LOW}beta')], name='beta-terms.yaml')])
        self.assertEqual(report['coverage'], 'full')
        self.assertEqual(self.empty_warnings(report), [])

    def test_require_full_is_blocked_by_an_undeclared_empty_layer(self):
        identities = [self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), self.identity('beta')]
        report = self.run_check(identities, require='full')
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('Coverage is partial', report['reason'])
        identities[1] = self.identity('beta', empty='celowo pusta')
        self.assertEqual(self.run_check(identities, require='full')['status'], 'PASS')

    def test_target_public_and_unknown_layers_do_not_warn(self):
        full = [self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), self.identity('beta', [term(f'{CLIENT_LOW}beta')])]
        extra = [self.identity('mine', name='mine.yaml'), self.identity('open', name='open.yaml'), self.identity('ghost', name='ghost.yaml')]
        report = self.run_check(full + extra)
        self.assertEqual(report['coverage'], 'full')
        self.assertEqual(self.empty_warnings(report), [])

    def test_compute_coverage_contract(self):
        cov = leakguard.compute_coverage
        self.assertEqual(cov(self.TARGETS, 'mine', {'alpha', 'beta'})[0], 'full')
        self.assertEqual(cov(self.TARGETS, 'mine', {'alpha'}, {'beta'})[0], 'partial')
        self.assertEqual(cov(self.TARGETS, 'mine', set(), {'alpha', 'beta'})[0], 'generic')
        # a layer listed as empty but also counted (terms in another file) is not warned about
        self.assertEqual(cov(self.TARGETS, 'mine', {'alpha', 'beta'}, {'beta'}), ('full', []))

    def test_empty_field_validation(self):
        path = self.tree({'a.md': 'clean\n'})
        cases = {
            'with-terms': {'schema': 1, 'layer': 'beta', 'terms': [term(f'{CLIENT_LOW}beta')], 'empty': 'powód'},
            'blank': {'schema': 1, 'layer': 'beta', 'terms': [], 'empty': '   '},
            'not-text': {'schema': 1, 'layer': 'beta', 'terms': [], 'empty': True},
            'number': {'schema': 1, 'layer': 'beta', 'terms': [], 'empty': 3},
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                file = self.root / 'idents' / f'bad-{name}.yaml'
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')
                with self.assertRaises(leakguard.LeakguardError):
                    leakguard.load_identity(file)
                report = leakguard.leakcheck(path, target='mine', targets=self.TARGETS, identity_files=[file])
                self.assertNotEqual(report['status'], 'PASS', name)
        for blank in ('', '\t'):
            with self.subTest(blank=repr(blank)):
                file = self.root / 'idents' / 'bad-empty-string.yaml'
                file.write_text(yaml.safe_dump({'schema': 1, 'layer': 'beta', 'terms': [], 'empty': blank}), encoding='utf-8')
                with self.assertRaises(leakguard.LeakguardError):
                    leakguard.load_identity(file)
        null = self.root / 'idents' / 'beta-null.yaml'
        null.write_text('schema: 1\nlayer: beta\nterms: []\nempty:\n', encoding='utf-8')
        self.assertIsNone(leakguard.load_identity(null).empty, 'empty: null = brak deklaracji')
        report = self.run_check([self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), null])
        self.assertEqual(report['coverage'], 'partial', 'empty: null nie liczy się do full')
        self.assertEqual(leakguard.load_identity(self.identity('beta', empty='  powód  ')).empty, 'powód')
        self.assertIsNone(leakguard.load_identity(self.identity('beta', [term(f'{CLIENT_LOW}beta')])).empty)

    def test_layers_init_skeleton_identity_is_partial_until_terms_or_empty(self):
        """R5: the identity written by `layers init` has no terms, so a fresh layer does not count toward `full`."""
        from skillsync_workflow import layers
        skeleton = self.root / 'idents' / 'beta-skeleton.yaml'
        skeleton.parent.mkdir(parents=True, exist_ok=True)
        skeleton.write_text(layers._skeleton('beta', 'org')[layers.DEFAULT_IDENTITY], encoding='utf-8')
        report = self.run_check([self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]), skeleton])
        self.assertEqual(report['coverage'], 'partial')
        self.assertEqual(len(self.empty_warnings(report)), 1)

    def test_cli_with_targets_json(self):
        path = self.tree({'a.md': 'clean\n'})
        base = ['leakcheck', str(path), '--targets-json', str(self.targets_file(self.TARGETS)), '--target', 'mine', '--require', 'full', '--json',
                '--identity', str(self.identity('alpha', [term(f'{CLIENT_LOW}alpha')]))]
        blocked = self.cli(*base, '--identity', str(self.identity('beta')))
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        report = json.loads(blocked.stdout)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('Coverage is partial', report['reason'])
        ok = self.cli(*base, '--identity', str(self.identity('beta', empty='celowo pusta')))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['coverage'], 'full')

    def test_cli_with_machine_config_identity_dir(self):
        """The same rule through `leakguard.identity_dir` of the machine config (`layers.leakguard_inputs`)."""
        identities = self.root / 'identity-dir'
        identities.mkdir()
        (identities / 'alpha.yaml').write_text(yaml.safe_dump({'schema': 1, 'layer': 'alpha', 'terms': [term(f'{CLIENT_LOW}alpha')]}), encoding='utf-8')
        (identities / 'beta.yaml').write_text(yaml.safe_dump({'schema': 1, 'layer': 'beta', 'terms': []}), encoding='utf-8')
        config = Path(os.environ['SKILLSYNC_CONFIG'])
        config.parent.mkdir(parents=True, exist_ok=True)
        targets = {k: {**v, 'authors': [], 'remote_match': []} for k, v in self.TARGETS.items()}
        config.write_text(yaml.safe_dump({'leakguard': {'identity_dir': str(identities), 'targets': targets}}), encoding='utf-8')
        path = self.tree({'a.md': 'clean\n'})
        blocked = self.cli('leakcheck', str(path), '--target', 'mine', '--require', 'full', '--json')
        self.assertEqual(blocked.returncode, 2, blocked.stdout + blocked.stderr)
        self.assertIn('Coverage is partial', json.loads(blocked.stdout)['reason'])
        (identities / 'beta.yaml').write_text(yaml.safe_dump({'schema': 1, 'layer': 'beta', 'terms': [], 'empty': 'celowo pusta'}), encoding='utf-8')
        ok = self.cli('leakcheck', str(path), '--target', 'mine', '--require', 'full', '--json')
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertEqual(json.loads(ok.stdout)['coverage'], 'full')


class HiddenInputsHelpTest(LeakTestCase):
    """D200: inputs for ops tools and diagnostics work, but `--help` does not show them."""
    HIDDEN = ('--identity', '--targets-json', '--trailer-allow', '--target-patterns')

    def test_help_hides_internal_inputs(self):
        for command in (['leakcheck'], ['leakguard', 'install-hook'], ['leakguard', 'patterns']):
            with self.subTest(command=command):
                shown = self.cli(*command, '--help')
                self.assertEqual(shown.returncode, 0, shown.stderr)
                self.assertIn('--target', shown.stdout)
                for flag in self.HIDDEN:
                    self.assertNotIn(flag, shown.stdout, f'{command}: {flag} visible in --help')

    def test_hidden_inputs_still_work(self):
        path = self.tree({'a.md': f'{CLIENT_LOW}x here\n'})
        targets = self.targets_file({'mine': {'scope': 'org-private', 'kind': 'org'}, 'other': {'scope': 'private'}})
        identity = self.ident('other', [term(f'{CLIENT_LOW}x')])
        additions = self.root / 'additions.yaml'
        additions.write_text('schema: 1\npatterns: []\n', encoding='utf-8')
        found = self.cli('leakcheck', str(path), '--no-config', '--targets-json', str(targets), '--target', 'mine',
                         '--identity', str(identity), '--trailer-allow', 'bot@example.invalid',
                         '--target-patterns', str(additions), '--json')
        self.assertEqual(found.returncode, 1, found.stdout + found.stderr)
        self.assertEqual(json.loads(found.stdout)['status'], 'FAIL')
        merged = self.cli('leakguard', 'patterns', '--target-patterns', str(additions), '--json')
        self.assertEqual(merged.returncode, 0, merged.stderr)


# ====================================================================== K7: patterns files


class PatternFilesTest(LeakTestCase):
    def repo_with_additions(self, additions, content='clean\n', rel='skills/a.md'):
        repo = self.make_repo()
        write(repo / leakguard.TARGET_PATTERNS_REL, additions if isinstance(additions, str)
              else yaml.safe_dump(additions))
        write(repo / rel, content)
        return repo

    def test_valid_addition_is_applied(self):
        additions = {'schema': 1, 'patterns': [{'id': 'internal-host', 'category': 'custom',
                                                'regex': r'internal-host-\d+'}],
                     'allow': [{'id': 'host-zero', 'regex': r'internal-host-0(?!\d)'}]}
        repo = self.repo_with_additions(additions, 'internal-host-0\n')
        self.assertEqual(leakguard.leakcheck(repo)['status'], 'PASS')
        write(repo / 'skills/a.md', 'internal-host-0 and internal-host-5\n')
        report = leakguard.leakcheck(repo)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'custom'}))
        self.assertEqual(len(report['findings']), 1)

    def test_additions_with_trace_literal_regex_are_not_themselves_a_leak(self):
        additions = {'schema': 1, 'patterns': [{'id': 'extra', 'category': 'custom', 'regex': D27_SOURCE.lower()}]}
        repo = self.repo_with_additions(additions)
        self.assertEqual(leakguard.leakcheck(repo)['status'], 'PASS')

    def test_too_broad_allow_entries_are_rejected(self):
        for regex in ('.*', '^.*$', r'\S+', r'\w*', 'x*', '', r'(?:.*)', '^.+$'):
            with self.subTest(regex=regex):
                repo = self.repo_with_additions({'schema': 1, 'allow': [{'id': 'wide', 'regex': regex}]})
                report = leakguard.leakcheck(repo)
                self.assertEqual(report['status'], 'BLOCKED', report)
                self.assertTrue(report['reason'])

    def test_explicit_identity_term_in_additions_is_rejected(self):
        additions = {'schema': 1, 'patterns': [{'id': 'bad', 'category': 'custom', 'regex': f'{CLIENT_LOW}-x'}]}
        repo = self.repo_with_additions(additions)
        report = self.check(repo, [term(CLIENT)])
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())
        # no identity files loaded: nothing to compare against, additions are accepted
        self.assertEqual(leakguard.leakcheck(repo)['status'], 'PASS')

    def test_invalid_schemas_are_blocked(self):
        base_pattern = {'id': 'p', 'category': 'custom', 'regex': 'zzz'}
        cases = {
            'schema-too-new': {'schema': 2, 'patterns': [base_pattern]},
            'schema-missing': {'patterns': [base_pattern]},
            'unknown-category': {'schema': 1, 'patterns': [{**base_pattern, 'category': 'weird'}]},
            'inline-flag': {'schema': 1, 'patterns': [{**base_pattern, 'regex': '(?i)zzz'}]},
            'bad-flag': {'schema': 1, 'patterns': [{**base_pattern, 'flags': ['dotall']}]},
            'bad-regex': {'schema': 1, 'patterns': [{**base_pattern, 'regex': '(zzz'}]},
            'missing-id': {'schema': 1, 'patterns': [{'category': 'custom', 'regex': 'zzz'}]},
            'bad-id': {'schema': 1, 'patterns': [{**base_pattern, 'id': 'Bad_ID'}]},
            'bad-surface': {'schema': 1, 'patterns': [{**base_pattern, 'applies_to': ['network']}]},
            'duplicate-id': {'schema': 1, 'patterns': [base_pattern, base_pattern]},
            'collides-with-builtin': {'schema': 1, 'patterns': [{**base_pattern, 'id': 'd27-source'}]},
            'not-a-mapping': ['x'],
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                repo = self.repo_with_additions(data)
                report = leakguard.leakcheck(repo)
                self.assertEqual(report['status'], 'BLOCKED', report)
                self.assertEqual(report['findings'], [])
        with self.subTest(case='broken-yaml'):
            repo = self.repo_with_additions('schema: [1\n  patterns: :\n')
            self.assertEqual(leakguard.leakcheck(repo)['status'], 'BLOCKED')

    def test_unknown_keys_are_ignored_and_defaults_apply(self):
        additions = {'schema': 1, 'future': {'a': 1}, 'patterns': [
            {'id': 'p', 'category': 'custom', 'regex': 'zzz-marker', 'comment': 'ignored'}]}
        repo = self.repo_with_additions(additions, 'zzz-marker\n')
        self.assertEqual(leakguard.leakcheck(repo)['status'], 'FAIL')

    def test_explicit_target_patterns_take_precedence_over_the_checked_out_file(self):
        pr_version = {'schema': 1, 'allow': [{'id': 'pr-allow', 'regex': rf'{HOME}/alice/ok'}]}
        repo = self.repo_with_additions(pr_version, f'see {HOME}/alice/ok\n')
        # checked-out file (PR branch) quietly allows a leak
        self.assertEqual(leakguard.leakcheck(repo)['status'], 'PASS')
        base_file = self.root / 'base-patterns.yaml'
        base_file.write_text(yaml.safe_dump({'schema': 1}), encoding='utf-8')
        self.assertEqual(leakguard.leakcheck(repo, target_patterns=base_file)['status'], 'FAIL')
        broken = self.root / 'broken.yaml'
        broken.write_text('schema: 5\n', encoding='utf-8')
        self.assertEqual(leakguard.leakcheck(repo, target_patterns=broken)['status'], 'BLOCKED')

    def test_missing_or_broken_builtin_resource_is_blocked(self):
        path = self.tree({'a.md': 'clean\n'})
        report = leakguard.leakcheck(path, patterns_file=self.root / 'nope.yaml')
        self.assertEqual(report['status'], 'BLOCKED')
        bad = self.root / 'bad.yaml'
        bad.write_text('schema: 7\npatterns: []\n', encoding='utf-8')
        self.assertEqual(leakguard.leakcheck(path, patterns_file=bad)['status'], 'BLOCKED')

    def test_patterns_json_contract_and_exit_codes(self):
        process = self.cli('leakguard', 'patterns', '--json')
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual((payload['schema'], payload['status']), (1, 'PASS'))
        self.assertTrue(payload['patterns'] and payload['allow'])
        for item in payload['patterns']:
            self.assertEqual({'id', 'category', 'regex', 'flags', 'applies_to', 'path_classes', 'paths'} - set(item),
                             set())
        for item in payload['allow']:
            self.assertEqual({'id', 'regex', 'flags', 'applies_to', 'path_classes', 'paths'} - set(item), set())
        text = process.stdout.lower()
        for trace in (D27_SOURCE, D27_SOURCE.replace(' ', '-'), D27_PERSON, D27_ICON_A, D27_ICON_B, 'om-' + 'spec'):
            self.assertNotIn(trace.lower(), text)
        additions = self.root / 'additions.yaml'
        additions.write_text(yaml.safe_dump({'schema': 1, 'patterns': [
            {'id': 'extra-one', 'category': 'custom', 'regex': 'zzz'}]}), encoding='utf-8')
        merged = json.loads(self.cli('leakguard', 'patterns', '--json', '--target-patterns', str(additions)).stdout)
        self.assertIn('extra-one', [item['id'] for item in merged['patterns']])
        self.assertEqual(len(merged['patterns']), len(payload['patterns']) + 1)
        additions.write_text('schema: 9\n', encoding='utf-8')
        broken = self.cli('leakguard', 'patterns', '--json', '--target-patterns', str(additions))
        self.assertEqual(broken.returncode, 2)
        self.assertEqual(json.loads(broken.stdout)['status'], 'BLOCKED')


# ====================================================================== K8: path classes and table

CLASS_PATHS = {'instrukcje': 'instructions/note.md', 'atrybucje': 'CHANGELOG.md', 'inne': 'tests/note.txt'}
T, F = True, False
# row -> {(target kind, class): allowed}; `inne` is checked against the `instrukcje` column.
TABLE = {
    'owner-nick': {('core', 'instrukcje'): F, ('core', 'atrybucje'): T, ('org', 'instrukcje'): F,
                   ('org', 'atrybucje'): T, ('personal', 'instrukcje'): T, ('personal', 'atrybucje'): T},
    'persona': {('core', 'instrukcje'): F, ('core', 'atrybucje'): F, ('org', 'instrukcje'): F,
                ('org', 'atrybucje'): F, ('personal', 'instrukcje'): T, ('personal', 'atrybucje'): T},
    'owner-name': {('core', 'instrukcje'): F, ('core', 'atrybucje'): T, ('org', 'instrukcje'): T,
                   ('org', 'atrybucje'): T, ('personal', 'instrukcje'): T, ('personal', 'atrybucje'): T},
    'publisher': {('core', 'instrukcje'): T, ('core', 'atrybucje'): T, ('org', 'instrukcje'): F,
                  ('org', 'atrybucje'): F, ('personal', 'instrukcje'): F, ('personal', 'atrybucje'): F},
    'org': {('core', 'instrukcje'): F, ('core', 'atrybucje'): F, ('org', 'instrukcje'): F,
            ('org', 'atrybucje'): F, ('personal', 'instrukcje'): F, ('personal', 'atrybucje'): F},
}
TABLE_TERMS = {'owner-nick': NICK, 'persona': PERSONA, 'owner-name': OWNER_NAME, 'publisher': PUBLISHER,
               'org': CLIENT}


class TargetAdditionsLinkTest(LeakTestCase):
    """D73/T14: the automatic read of `<git root>/.leakguard/patterns.yaml` never goes through a link."""
    SECRET = 'UNIQUEPATTERNTEXT'

    def additions_text(self):
        return f"schema: 1\npatterns:\n  - id: extra\n    category: custom\n    regex: '{self.SECRET}'\n"

    def run_leakcheck(self, repo):
        report = leakguard.leakcheck(repo)
        process = self.cli('leakcheck', str(repo), '--no-config', '--json')
        return report, process.returncode, json.loads(process.stdout)

    def test_a_regular_additions_file_is_used(self):
        repo = self.make_repo()
        write(repo / '.leakguard' / 'patterns.yaml', self.additions_text())
        write(repo / 'doc.md', f'has {self.SECRET} inside\n')
        report, code, cli = self.run_leakcheck(repo)
        self.assertEqual((report['status'], code, cli['status']), ('FAIL', 1, 'FAIL'))

    def test_additions_that_are_a_link_or_lie_under_one_are_blocked_without_content_or_target(self):
        if not hasattr(os, 'symlink'):
            self.skipTest('symlinks are not available on this host')
        real = self.work / 'real-patterns.yaml'
        write(real, self.additions_text())
        store = self.work / 'real-leakguard'
        write(store / 'patterns.yaml', self.additions_text())
        for shape in ('file', 'directory'):
            for mode in LINK_MODES:
                with self.subTest(shape=shape, mode=mode), link_mode(mode):
                    repo = self.make_repo(f'{shape}-{mode}')
                    write(repo / 'doc.md', f'has {self.SECRET} inside\n')
                    if shape == 'file':
                        (repo / '.leakguard').mkdir()
                        os.symlink(real, repo / '.leakguard' / 'patterns.yaml')
                    else:
                        os.symlink(store, repo / '.leakguard', target_is_directory=True)
                    report, code, cli = self.run_leakcheck(repo)
                    self.assertEqual((report['status'], code, cli['status']), ('BLOCKED', 2, 'BLOCKED'))
                    for text in (json.dumps(report), json.dumps(cli)):
                        self.assertNotIn(self.SECRET, text)
                        self.assertNotIn('real-patterns', text)
                        self.assertNotIn('real-leakguard', text)
                    self.assertEqual(report['findings'], [])


class PathClassTableTest(LeakTestCase):
    def test_every_cell_of_the_table_in_both_directions(self):
        for row, cells in TABLE.items():
            value = TABLE_TERMS[row]
            for (target_kind, cls), allowed in cells.items():
                classes = [cls] + (['inne'] if cls == 'instrukcje' else [])
                for used_class in classes:
                    with self.subTest(row=row, target=target_kind, path_class=used_class):
                        status = self.status_of_cell(CLASS_PATHS[used_class], f'about {value} here\n', row, value,
                                                     target_kind)
                        self.assertEqual(status, 'PASS' if allowed else 'FAIL')

    def test_unclassified_paths_default_to_the_strictest_class(self):
        for rel in ('misc/deep/unknown.dat', 'data/blob.bin.txt', 'config.yaml', '_base.d/frag.md', 'agents/x.md',
                    'skills/s/SKILL.md', 'nested/instructions/x.md'):
            with self.subTest(rel=rel):
                self.assertEqual(self.status_of_cell(rel, f'{NICK}\n', 'owner-nick', NICK, 'core'), 'FAIL')

    def test_tests_and_github_are_inne_before_the_code_suffixes(self):
        # K8: `inne` = `tests/**`, `.github/**`; `inne` is as strict as `instrukcje` in every column of the table
        for rel in ('tests/x.py', 'tests/a/b/c.py', 'tests/helper.sh', 'tests/data.ts', '.github/workflows/ci.yml',
                    '.github/scripts/run.py', '.github/x.sh'):
            with self.subTest(rel=rel):
                self.assertEqual(leakguard.path_class(rel), 'inne')
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'core'), 'FAIL')
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'personal'), 'PASS')
        for rel in ('skillsync_workflow/x.py', 'scripts/run.py', 'src/tests/x.py', 'docs/tests/a.md', 'mytests/x.py'):
            with self.subTest(rel=rel):                                   # `tests` only as the FIRST segment
                self.assertEqual(leakguard.path_class(rel), 'atrybucje')
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'core'), 'PASS')
        self.assertEqual(leakguard.path_class('tests/skills/x.py'), 'instrukcje')          # the instruction segments come first
        self.assertEqual(leakguard.path_class('tests'), 'inne')                            # a root file
        order = leakguard.PATH_CLASS_RULES['order']
        self.assertLess(order.index('inne.first_segments'), order.index('atrybucje'))
        self.assertLess(order.index('instrukcje.segments'), order.index('inne.first_segments'))

    def test_instruction_segments_are_case_folded_on_every_system(self):
        # Review 6.1-1 M2: the generator reads `Skills/` as `skills/` on case-insensitive systems, so a file
        # under `Skills/` is an instruction file everywhere; a code suffix or README* must not relax it.
        for rel in ('Skills/x/run.py', 'Skills/x/README.md', 'SKILLS/x/tool.sh', 'skills/x/run.py',
                    'skills/x/README.md', 'lib/Instructions/a.py', 'Agents/CHANGELOG.md', '_BASE.D/x.py'):
            with self.subTest(rel=rel):
                self.assertEqual(leakguard.path_class(rel), 'instrukcje')
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'core'), 'FAIL')
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'personal'),
                                 'PASS')                                          # the term itself is allowed there
        for rel, expected in (('docs/guide.md', 'atrybucje'), ('CHANGELOG.md', 'atrybucje'), ('README.md', 'atrybucje'),
                              ('src/README.md', 'atrybucje'), ('tool.py', 'atrybucje'), ('tests/x.py', 'inne'),
                              ('tests/a/b.md', 'inne')):
            with self.subTest(lowercase=rel):                                     # no regression for lower case
                self.assertEqual(leakguard.path_class(rel), expected)
        for rel in ('Tests/x.py', 'TESTS/a/b.sh', '.GitHub/workflows/a.yml', '.GITHUB/scripts/run.py'):
            with self.subTest(inne=rel):                                          # review 6.1-2: `inne` is folded too
                self.assertEqual(leakguard.path_class(rel), leakguard.path_class(rel.lower()))
                self.assertEqual(leakguard.path_class(rel), 'inne')

    def test_attribution_paths_allow_the_nick(self):
        for rel in ('CHANGELOG.md', 'CHANGELOG', 'README.md', 'docs/guide.md', 'docs/a/b/c.md', 'NOTICE', 'LICENSE',
                    'tool.py', 'src/app.ts', 'scripts/run.sh', 'lib/x.mjs', 'skillsync', 'skillsync-ctl'):
            with self.subTest(rel=rel):
                self.assertEqual(self.status_of_cell(rel, f'# decyzja {NICK}a\n', 'owner-nick', NICK, 'core'), 'PASS')

    def test_nick_in_code_comment_is_allowed_but_not_in_instructions_inflected(self):
        self.assertEqual(self.status_of_cell('tool.py', f'x = 1  # {NICK}a prosba\n', 'owner-nick', NICK, 'core'),
                         'PASS')
        self.assertEqual(self.status_of_cell('instructions/a.md', f'{NICK}a prosba\n', 'owner-nick', NICK, 'core'),
                         'FAIL')

    def test_nick_in_a_path_is_forbidden_even_for_attribution_files(self):
        path = self.tree({f'docs/{NICK.lower()}.md': 'clean\n'})
        report = self.check(path, [term(NICK, 'owner-nick')], target_kind='core')
        self.assertEqual({surface_of(item) for item in report['findings']}, {'filename'})

    def test_attributions_in_commits_are_allowed_for_nick_but_persona_is_not(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', f'decyzja {NICK}a\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply{AT}anthropic.com>')
        terms = [term(NICK, 'owner-nick'), term(PERSONA, 'persona')]
        self.assertEqual(self.check(repo, terms, mode='history', target_kind='core')['status'], 'PASS')
        commit_file(repo, 'b.md', 'x\n', f'{PERSONA} approved')
        self.assertEqual(self.check(repo, terms, mode='history', target_kind='core')['status'], 'FAIL')

    def test_allow_in_and_path_classes_narrow_or_widen_a_term(self):
        def run(extra, rel):
            return self.status_of_cell(rel, f'{CLIENT}\n', 'org', CLIENT, 'org', **extra)
        self.assertEqual(run({}, 'skills/a.md'), 'FAIL')
        self.assertEqual(run({'allow_in': ['mine']}, 'skills/a.md'), 'PASS')
        self.assertEqual(run({'allow_in': ['elsewhere']}, 'skills/a.md'), 'FAIL')
        self.assertEqual(run({'allow_in': ['mine'], 'path_classes': ['atrybucje']}, 'CHANGELOG.md'), 'PASS')
        self.assertEqual(run({'allow_in': ['mine'], 'path_classes': ['atrybucje']}, 'skills/a.md'), 'FAIL')
        self.assertEqual(run({'allow_in': ['mine'], 'path_classes': ['atrybucje']}, 'tests/a.txt'), 'FAIL')

    def test_invalid_identity_files_are_blocked(self):
        path = self.tree({'a.md': 'x\n'})
        bad_docs = {
            'schema': {'schema': 2, 'layer': 'o', 'terms': []},
            'layer-missing': {'schema': 1, 'terms': []},
            'kind': {'schema': 1, 'layer': 'o', 'terms': [{'term': 'x', 'kind': 'weird'}]},
            'case': {'schema': 1, 'layer': 'o', 'terms': [{'term': 'x', 'kind': 'org', 'case': 'maybe'}]},
            'path-class': {'schema': 1, 'layer': 'o', 'terms': [{'term': 'x', 'kind': 'org', 'path_classes': ['q']}]},
            'empty-term': {'schema': 1, 'layer': 'o', 'terms': [{'term': ' ', 'kind': 'org'}]},
        }
        for label, data in bad_docs.items():
            with self.subTest(case=label):
                file = self.root / f'bad-{label}.yaml'
                file.write_text(yaml.safe_dump(data), encoding='utf-8')
                report = leakguard.leakcheck(path, identity_files=[file])
                self.assertEqual(report['status'], 'BLOCKED', report)
        self.assertEqual(leakguard.leakcheck(path, identity_files=[self.root / 'missing.yaml'])['status'], 'BLOCKED')


class PublisherExceptionTest(LeakTestCase):
    URLS = (f'https://github.com/{PUBLISHER}/skillsync', f'github.com/{PUBLISHER}/skillsync.git',
            f'https://github.com/{PUBLISHER}/skippster/issues/12', f'github.com/{PUBLISHER}/skillsync/pull/3',
            f'https://github.com/{PUBLISHER.lower()}/skillsync/blob/main/README.md')

    def terms(self):
        return [term(PUBLISHER, 'publisher'), term(CLIENT, 'org')]

    def scan(self, rel, text, target_kind):
        return self.check(self.tree({rel: text + '\n'}), self.terms(), target_kind=target_kind)

    def test_publisher_urls_are_allowed_in_every_layer_and_class(self):
        for target_kind in ('core', 'org', 'personal'):
            for rel in ('instructions/a.md', 'CHANGELOG.md', 'docs/x.md', 'tests/t.txt'):
                for url in self.URLS:
                    with self.subTest(target=target_kind, rel=rel, url=url):
                        self.assertEqual(self.scan(rel, f'see {url} for more', target_kind)['status'], 'PASS')

    def test_license_line_is_allowed_everywhere_but_only_exactly_there(self):
        line = f'Copyright (c) 2026 {PUBLISHER}'
        for target_kind in ('core', 'org', 'personal'):
            with self.subTest(target=target_kind):
                self.assertEqual(self.scan('LICENSE', line, target_kind)['status'], 'PASS')
        self.assertEqual(self.scan('README.md', line, 'org')['status'], 'FAIL')
        self.assertEqual(self.scan('LICENSE', f'{line} and {CLIENT}', 'org')['status'], 'FAIL')
        self.assertEqual(self.scan('LICENSE', f'Contact {PUBLISHER}', 'org')['status'], 'FAIL')

    def test_other_publisher_mentions_are_forbidden_outside_core(self):
        for text in (f'by {PUBLISHER}', f'https://github.com/{PUBLISHER}/other-repo', f'{PUBLISHER}.com'):
            with self.subTest(text=text):
                self.assertEqual(self.scan('docs/x.md', text, 'org')['status'], 'FAIL')
                self.assertEqual(self.scan('docs/x.md', text, 'personal')['status'], 'FAIL')
                self.assertEqual(self.scan('docs/x.md', text, 'core')['status'], 'PASS')

    def test_client_term_appended_to_the_publisher_url_is_a_leak(self):
        base = f'https://github.com/{PUBLISHER}/skillsync'
        cases = (f'{base}-{CLIENT_LOW}', f'{base}/{CLIENT_LOW}/x', f'{base}.git-{CLIENT_LOW}', f'{base} {CLIENT}')
        for text in cases:
            for target_kind in ('core', 'org'):
                with self.subTest(text=text, target=target_kind):
                    report = self.scan('instructions/a.md', text, target_kind)
                    self.assertEqual(report['status'], 'FAIL', report)

    def test_repo_name_extended_by_a_word_is_not_the_exception(self):
        # in the core the publisher name itself is fine; in an org layer `skillsyncx` is another repo
        self.assertEqual(self.scan('docs/x.md', f'https://github.com/{PUBLISHER}/skillsyncx', 'org')['status'], 'FAIL')
        self.assertEqual(self.scan('docs/x.md', f'https://github.com/{PUBLISHER}/skillsync-docs', 'org')['status'],
                         'FAIL')

    def test_term_inside_an_allowed_url_or_placeholder_address_is_a_leak(self):
        base = f'https://github.com/{PUBLISHER}/skillsync'
        masked = (f'{base}/issues/{CLIENT_LOW}', f'{base}/blob/main/{CLIENT_LOW}.md', f'{base}/pull/{CLIENT}',
                  f'jan{AT}{CLIENT_LOW}.example', f'{CLIENT_LOW}{AT}example.com', f'x{AT}{CLIENT_LOW}.invalid',
                  f'{CLIENT_LOW}{AT}host.example')
        for target_kind in ('core', 'org'):
            for text in masked:
                with self.subTest(text=text, target=target_kind):
                    report = self.scan('docs/x.md', f'see {text} here', target_kind)
                    self.assertEqual(report['status'], 'FAIL', report)
                    self.assertIn('identity-org', categories(report))
        for text in (base, f'{base}/issues/12', f'jan{AT}acme.example', f'jan{AT}example.com', f'x{AT}ci.invalid'):
            with self.subTest(control=text):
                self.assertEqual(self.scan('docs/x.md', f'see {text} here', 'org')['status'], 'PASS')

    def test_class_preserving_filler_keeps_upper_case_terms_visible_to_key_allows(self):
        additions = self.root / 'ticket-allow.yaml'
        additions.write_text(yaml.safe_dump({'schema': 1, 'allow': [
            {'id': 'ticket-keys', 'regex': r'[A-Z]{2,}-\d+'}]}), encoding='utf-8')
        terms = [term(CLIENT)]
        ticket = self.tree({'docs/a.md': 'see PROJ-123 and ABC-9\n'})
        self.assertEqual(self.check(ticket, terms, target_patterns=additions)['status'], 'PASS')
        leaky = self.tree({'docs/a.md': f'see {CLIENT.upper()}-123 here\n'})
        report = self.check(leaky, terms, target_patterns=additions)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'identity-org'}))
        digits = [term(CLIENT_LOW + '7')]
        leaky = self.tree({'docs/a.md': f'see {CLIENT.upper()}7-123 here\n'})
        self.assertEqual(self.check(leaky, digits, target_patterns=additions)['status'], 'FAIL')

    def test_trailer_accepts_any_model_name_but_not_a_client_term(self):
        repo = self.make_repo()
        for number, name in enumerate(('Claude Sonnet 5.5', 'Claude Opus 4.7 (1M context)', 'Gemini Pro 3', 'Some Model-X')):
            commit_file(repo, f'f{number}.md', 'x\n', f'ok\n\nCo-Authored-By: {name} <noreply{AT}anthropic.com>')
        self.assertEqual(self.check(repo, self.terms(), mode='history')['status'], 'PASS')
        for number, name in enumerate((f'Claude {CLIENT} (1M context)', f'{CLIENT}-bot', f'Claude {CLIENT_LOW}a')):
            with self.subTest(name=name):
                repo = self.make_repo('bad')
                commit_file(repo, 'a.md', 'x\n', f'bad\n\nCo-Authored-By: {name} <noreply{AT}anthropic.com>')
                report = self.check(repo, self.terms(), mode='history')
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'identity-org'}))

    def test_trailer_with_a_term_in_the_name_is_a_leak(self):
        repo = self.make_repo()
        commit_file(repo, 'a.md', 'x\n', f'ok\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply{AT}anthropic.com>')
        self.assertEqual(self.check(repo, self.terms(), mode='history')['status'], 'PASS')
        commit_file(repo, 'b.md', 'x\n', f'bad\n\nCo-Authored-By: Claude {CLIENT} <noreply{AT}anthropic.com>')
        report = self.check(repo, self.terms(), mode='history')
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('identity-org', categories(report))
        self.assertEqual({surface_of(item) for item in report['findings']}, {'commit'})


# ====================================================================== K8: authors


class AuthorsTest(LeakTestCase):
    TARGETS = {'mine': {'scope': 'org-private', 'kind': 'org', 'authors': [f'dev{AT}corp.example', f'Other{AT}Corp.Example']}}

    def scan(self, repo, **kwargs):
        return leakguard.leakcheck(repo, target='mine', targets=self.TARGETS, mode='history', **kwargs)

    def test_local_committer_commits_must_use_listed_authors(self):
        repo = self.make_repo(email=f'dev{AT}corp.example')
        commit_file(repo, 'a.md', 'x\n', 'ok')
        self.assertEqual(self.scan(repo)['status'], 'PASS')
        commit_file(repo, 'b.md', 'x\n', 'listed other', author=('O', f'other{AT}corp.example'))
        self.assertEqual(self.scan(repo)['status'], 'PASS')
        commit_file(repo, 'c.md', 'x\n', 'stranger', author=('S', f'stranger{AT}else.example'))
        report = self.scan(repo)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))
        self.assertNotIn('stranger', json.dumps(report))

    def test_local_committer_with_unlisted_address_fails(self):
        repo = self.make_repo(email=f'me{AT}private.example')
        commit_file(repo, 'a.md', 'x\n', 'mine', author=('Me', f'me{AT}private.example'))
        report = self.scan(repo)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))

    def test_foreign_commits_are_information_not_a_failure_with_authors_scope_committer(self):
        """D86: only `authors_scope: committer` reports commits of other people as information (an
        `org-private` target defaults to `all`, see AuthorsScopeTest)."""
        repo = self.make_repo(email=f'dev{AT}corp.example')
        commit_file(repo, 'a.md', 'x\n', 'external', author=('Ext', f'ext{AT}else.example'),
                    committer=('Ext', f'ext{AT}else.example'))
        targets = {'mine': {**self.TARGETS['mine'], 'authors_scope': 'committer'}}
        report = leakguard.leakcheck(repo, target='mine', targets=targets, mode='history')
        self.assertEqual((report['status'], report['findings']), ('PASS', []))
        self.assertTrue(report['notices'])
        self.assertNotIn(f'ext{AT}else', json.dumps(report))

    def test_authors_are_checked_in_range_mode_too(self):
        repo = self.make_repo(email=f'dev{AT}corp.example')
        base = commit_file(repo, 'a.md', 'x\n', 'ok')
        head = commit_file(repo, 'b.md', 'x\n', 'bad', author=('S', f'stranger{AT}else.example'))
        report = leakguard.leakcheck(repo, target='mine', targets=self.TARGETS, mode='range', rev_range=f'{base}..{head}')
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(leakguard.leakcheck(repo, target='mine', targets=self.TARGETS, mode='range',
                                             rev_range=f'{base}..{base}')['status'], 'PASS')

    def test_target_without_authors_does_not_check_them(self):
        repo = self.make_repo(email=f'dev{AT}corp.example')
        commit_file(repo, 'a.md', 'x\n', 'bad', author=('S', f'stranger{AT}else.example'))
        targets = {'mine': {'scope': 'org-private', 'authors': []}}
        self.assertEqual(leakguard.leakcheck(repo, target='mine', targets=targets, mode='history')['status'], 'PASS')

    def test_author_addresses_are_not_reported_as_email_leaks(self):
        repo = self.make_repo(email=f'dev{AT}corp.example')
        commit_file(repo, 'a.md', 'x\n', 'ok')
        self.assertEqual(categories(self.scan(repo)), set())


class AuthorsScopeTest(LeakTestCase):
    """D86 (spec A, "Hooki: autorzy i cudze commity"): `authors_scope: all | committer` of a target; default
    `committer` for `public`, `all` for `private`/`org-private`; `all` = every commit in `--range`/`--history`
    (and in the hooks) needs author and committer from `authors`; `committer` = only commits of the local
    committer are checked, other people's commits are information."""

    DEV = f'dev{AT}corp.example'
    EXT = ('Ext', f'ext{AT}else.example')

    def targets(self, scope, authors_scope=None):
        entry = {'scope': scope, 'kind': 'org', 'authors': [self.DEV]}
        if authors_scope is not None:
            entry['authors_scope'] = authors_scope
        return {'mine': entry}

    def foreign_repo(self, committer=True):
        """Repo of the local developer with one clean commit and one commit of another person."""
        repo = self.make_repo(email=self.DEV)
        base = commit_file(repo, 'a.md', 'x\n', 'mine')
        head = commit_file(repo, 'b.md', 'y\n', 'external', author=self.EXT, committer=self.EXT if committer else None)
        return repo, base, head

    def scan(self, repo, targets, **kwargs):
        kwargs.setdefault('mode', 'history')
        return leakguard.leakcheck(repo, target='mine', targets=targets, **kwargs)

    def test_defaults_follow_the_scope_and_explicit_values_are_kept(self):
        result = leakguard.validate_targets({
            'pub': {'scope': 'public'}, 'org': {'scope': 'org-private'}, 'priv': {'scope': 'private'},
            'pub-all': {'scope': 'public', 'authors_scope': 'all'},
            'priv-committer': {'scope': 'private', 'authors_scope': 'committer'}})
        self.assertEqual({key: value['authors_scope'] for key, value in result.items()},
                         {'pub': 'committer', 'org': 'all', 'priv': 'all', 'pub-all': 'all',
                          'priv-committer': 'committer'})

    def test_unknown_value_is_a_schema_error_and_leakcheck_is_blocked(self):
        for value in ('everyone', '', 1, 'ALL'):
            with self.subTest(value=value):
                with self.assertRaises(leakguard.LeakguardError):
                    leakguard.validate_targets({'mine': {'scope': 'private', 'authors_scope': value}})
        repo, _base, _head = self.foreign_repo()
        report = self.scan(repo, {'mine': {**self.targets('private')['mine'], 'authors_scope': 'anyone'}})
        self.assertEqual(report['status'], 'BLOCKED')

    def test_history_with_scope_all_fails_on_a_foreign_commit_without_exposing_the_address(self):
        repo, _base, _head = self.foreign_repo()
        for scope, authors_scope in (('org-private', None), ('private', None), ('public', 'all')):
            with self.subTest(scope=scope, authors_scope=authors_scope):
                report = self.scan(repo, self.targets(scope, authors_scope))
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))
                self.assertEqual(report['authors_scope'], 'all')
                self.assertNotIn('ext' + AT, json.dumps(report))

    def test_history_with_scope_committer_reports_a_foreign_commit_as_information(self):
        repo, _base, _head = self.foreign_repo()
        for scope, authors_scope in (('public', None), ('org-private', 'committer'), ('private', 'committer')):
            with self.subTest(scope=scope, authors_scope=authors_scope):
                report = self.scan(repo, self.targets(scope, authors_scope))
                self.assertEqual((report['status'], report['findings']), ('PASS', []))
                self.assertEqual(report['authors_scope'], 'committer')
                self.assertTrue(report['notices'])

    def test_foreign_author_with_a_foreign_committer_and_a_listed_one(self):
        """`all` checks both identities: a foreign author fails even when committed by the local developer."""
        repo, _base, _head = self.foreign_repo(committer=False)
        for authors_scope in ('all', 'committer'):
            with self.subTest(authors_scope=authors_scope):
                report = self.scan(repo, self.targets('private', authors_scope))
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))

    def test_range_mode_obeys_the_scope(self):
        repo, base, head = self.foreign_repo()
        rev = f'{base}..{head}'
        self.assertEqual(self.scan(repo, self.targets('private'), mode='range', rev_range=rev)['status'], 'FAIL')
        self.assertEqual(self.scan(repo, self.targets('private', 'committer'), mode='range', rev_range=rev)['status'],
                         'PASS')
        self.assertEqual(self.scan(repo, self.targets('private'), mode='range', rev_range=f'{base}..{base}')['status'],
                         'PASS')

    def test_scope_all_checks_authors_even_without_a_local_user_email(self):
        repo, _base, _head = self.foreign_repo()
        git(repo, 'config', '--unset', 'user.email')
        report = self.scan(repo, self.targets('private'))
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))
        self.assertFalse(any('user.email' in item for item in report.get('warnings', [])), report.get('warnings'))
        committer = self.scan(repo, self.targets('private', 'committer'))
        self.assertTrue(any('user.email' in item for item in committer.get('warnings', [])), committer)

    def test_cli_history_exit_codes(self):
        repo, _base, _head = self.foreign_repo()
        for authors_scope, code in (('all', 1), ('committer', 0)):
            with self.subTest(authors_scope=authors_scope):
                targets = self.targets_file(self.targets('org-private', authors_scope))
                process = self.cli('leakcheck', str(repo), '--history', '--target', 'mine', '--targets-json',
                                   str(targets), '--no-config', '--json')
                self.assertEqual(process.returncode, code, process.stdout + process.stderr)
                self.assertNotIn('ext' + AT, process.stdout + process.stderr)

    @needs_sh
    def test_pre_push_hook_obeys_the_scope(self):
        for authors_scope, rejected in (('all', True), ('committer', False)):
            with self.subTest(authors_scope=authors_scope):
                repo, _base, head = self.foreign_repo()
                targets = self.targets_file(self.targets('org-private', authors_scope))
                report = leakguard.install_hook(repo, targets_json=targets, target='mine')
                self.assertEqual(report['status'], 'PASS', report)
                process = subprocess.run(['sh', str(repo / '.git' / 'hooks' / 'pre-push'), 'origin', 'url'],
                                         input=f'refs/heads/main {head} refs/heads/main {ZEROS}\n', text=True,
                                         capture_output=True, cwd=str(repo), env=dict(os.environ))
                self.assertEqual(process.returncode != 0, rejected, process.stdout + process.stderr)

    def test_staged_checks_the_identity_of_the_coming_commit_in_both_scopes(self):
        """D86 `--staged` (review 2.2b-1): the author and committer identity of the commit being made must be listed,
        whatever `authors_scope` is (the local developer is the committer of a staged commit)."""
        foreign = {'GIT_AUTHOR_NAME': self.EXT[0], 'GIT_AUTHOR_EMAIL': self.EXT[1]}
        for authors_scope in ('all', 'committer'):
            with self.subTest(authors_scope=authors_scope):
                repo = self.make_repo(email=self.DEV)
                commit_file(repo, 'a.md', 'x\n', 'mine')
                write(repo / 'b.md', 'y\n')
                git(repo, 'add', 'b.md')
                targets = self.targets('private', authors_scope)
                self.assertEqual(self.scan(repo, targets, mode='staged')['status'], 'PASS')
                with mock.patch.dict(os.environ, foreign):
                    report = self.scan(repo, targets, mode='staged')
                self.assertEqual((report['status'], categories(report)), ('FAIL', {'author'}))
                self.assertNotIn('ext' + AT, json.dumps(report))


# ====================================================================== D104: term match scope, glued terms

SNAKE = 'sna' + 'ke'
SURNAME = 'Kowal' + 'ski'
GLUED_A = 'x' + 'ABC'
GLUED_B = 'i' + 'PNG'


class TermMatchScopeTest(LeakTestCase):
    """D104 (spec A, "Zakres dopasowania terminu"): `match: part` (default) hits a segment of an identifier,
    `match: whole` only a whole token; a glued term (one-letter prefix + camelCase) hits only its glued form.
    Each case runs on the three surfaces: content, file name and commit metadata (message)."""

    SURFACES = ('content', 'filename', 'commit')

    def surfaces_hit(self, surface, text, terms):
        """Set of surfaces of the findings for `text` placed on `surface`."""
        identity = self.ident('other', terms)
        targets = {'mine': {'scope': 'org-private', 'kind': 'org'}, 'other': {'scope': 'private'}}
        if surface == 'content':
            path, kwargs = self.tree({'doc.md': f'intro\n{text}\n'}), {}
        elif surface == 'filename':
            path, kwargs = self.tree({f'docs/{text}.md': 'plain\n'}), {}
        else:
            path = self.make_repo('meta')
            commit_file(path, 'a.md', 'plain\n', f'note {text}')
            kwargs = {'mode': 'history'}
        report = leakguard.leakcheck(path, target='mine', targets=targets, identity_files=[identity], **kwargs)
        self.assertIn(report['status'], ('PASS', 'FAIL'), report)
        return {surface_of(item) for item in report['findings']}

    def assert_hits(self, terms, hits=(), misses=()):
        for surface in self.SURFACES:
            for text in hits:
                with self.subTest(surface=surface, text=text, expected='hit'):
                    self.assertEqual(self.surfaces_hit(surface, text, terms), {surface})
            for text in misses:
                with self.subTest(surface=surface, text=text, expected='miss'):
                    self.assertEqual(self.surfaces_hit(surface, text, terms), set())

    def test_whole_term_hits_only_a_whole_token(self):
        self.assert_hits([term(SNAKE, 'domain-term', match='whole')],
                         hits=[f'konta: {SNAKE}', SNAKE, f'{SNAKE}.v2'],
                         misses=[f'{SNAKE}_case', f'{SNAKE}Case', f'my-{SNAKE}', f'{SNAKE}_{SNAKE}x'])

    def test_part_term_hits_a_segment_of_an_identifier(self):
        """Contrast for the `whole` test above: the default `part` hits the same identifiers."""
        self.assert_hits([term(SNAKE, 'domain-term')], hits=[f'{SNAKE}_case', f'konta: {SNAKE}'])

    def test_surname_with_part_hits_file_names_and_identifiers(self):
        low = SURNAME.lower()
        self.assert_hits([term(SURNAME, 'person')], hits=[f'notatki-{low}', f'jan_{low}', f'Jan {SURNAME}'])
        self.assert_hits([term(SURNAME, 'person', match='part')], hits=[f'notatki-{low}', f'jan_{low}'])

    def test_surname_with_whole_misses_identifiers_but_hits_the_word(self):
        low = SURNAME.lower()
        self.assert_hits([term(SURNAME, 'person', match='whole')], hits=[f'Jan {SURNAME}'],
                         misses=[f'notatki-{low}', f'jan_{low}'])

    def test_glued_term_hits_only_its_glued_form(self):
        for value in (GLUED_A, GLUED_B):
            prefix, rest = value[0], value[1:]
            with self.subTest(term=value):
                self.assert_hits([term(value, 'domain-term')],
                                 hits=[value, value.upper(), value.lower(), f'{value} files'],
                                 misses=[f'{prefix} {rest}', f'{prefix}-{rest}', f'foo{value}', f'SVG {prefix} {rest}'])

    def test_svg_and_png_sentence_is_not_a_glued_term(self):
        self.assert_hits([term(GLUED_B, 'domain-term')], hits=['nowe ' + GLUED_B],
                         misses=['SVG ' + GLUED_B[0] + ' ' + GLUED_B[1:]])

    def test_glued_term_with_whole_also_needs_token_boundaries(self):
        self.assert_hits([term(GLUED_B, 'domain-term', match='whole')], hits=[GLUED_B],
                         misses=[f'{GLUED_B}_x', f'{GLUED_B[0]} {GLUED_B[1:]}'])

    def test_glued_term_with_part_hits_its_glued_form_continued_in_camel_case(self):
        """Review 2.2b-1 major 1 (D104: `part` hits a camelCase segment; the glued rule only excludes separated
        words): the glued form followed by more camelCase or `_x` is a hit, the separated words and a glued form
        inside a longer run are not."""
        self.assert_hits([term(GLUED_A, 'domain-term')], hits=[f'{GLUED_A}Service', f'{GLUED_A}_x'],
                         misses=[f'{GLUED_A[0]} {GLUED_A[1:]}', f'foo{GLUED_A}'])
        self.assert_hits([term(GLUED_B, 'domain-term')], hits=[f'{GLUED_B}Viewer'],
                         misses=['SVG ' + GLUED_B[0] + ' ' + GLUED_B[1:]])

    def test_glued_term_with_part_hits_inflections_and_a_start_at_a_token_boundary(self):
        """Review 2.2b-2 (minor): an inflection suffix (`xABCowi`, `XABCOWI`) and a start where a token starts inside
        a run (`pre-xABC` after a separator, `fooXABC` at a camelCase boundary) are hits with `part`."""
        self.assert_hits([term(GLUED_A, 'domain-term')],
                         hits=[f'{GLUED_A}owi', f'{GLUED_A}owi'.upper(), f'pre-{GLUED_A}', f'foo{GLUED_A.upper()}'],
                         misses=[f'foo{GLUED_A}'])

    def test_glued_term_with_whole_needs_a_whole_token(self):
        """`whole`: a glued form inside an identifier (`pre-xABC`, `fooXABC`) is not a hit. An inflected form that is
        the whole token (`xABCowi`, `XABCOWI`) is the same word in another case, so it stays a hit (K7 inflections,
        D104 "osobne słowo"), as for every other `whole` term."""
        self.assert_hits([term(GLUED_A, 'domain-term', match='whole')],
                         hits=[f'{GLUED_A}owi', f'{GLUED_A}owi'.upper()],
                         misses=[f'pre-{GLUED_A}', f'foo{GLUED_A.upper()}'])

    def test_glued_term_with_whole_misses_the_camel_case_continuation(self):
        self.assert_hits([term(GLUED_A, 'domain-term', match='whole')], hits=[GLUED_A, f'konta: {GLUED_A}'],
                         misses=[f'{GLUED_A}Service', f'{GLUED_A}_x', f'foo{GLUED_A}'])

    def test_unknown_match_value_is_a_schema_error(self):
        for value in ('token', 'WHOLE', 1, ''):
            with self.subTest(value=value):
                with self.assertRaises(leakguard.LeakguardError):
                    leakguard.parse_identity({'schema': 1, 'layer': 'other', 'terms': [term(SNAKE, 'org', match=value)]})
        report = self.check(self.tree({'a.md': 'x\n'}), [term(SNAKE, 'org', match='token')])
        self.assertEqual(report['status'], 'BLOCKED')


# ====================================================================== hooks


class HooksTest(LeakTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.hooks = self.repo / '.git' / 'hooks'
        self.ident_file = self.ident('other', [term(CLIENT)])
        self.targets = self.targets_file({'mine': {'scope': 'org-private', 'kind': 'org'},
                                          'other': {'scope': 'private'}})

    def install(self, **kwargs):
        return leakguard.install_hook(self.repo, identity_files=[self.ident_file], targets_json=self.targets,
                                      target='mine', **kwargs)

    def actions(self, report):
        return {item['name']: item['action'] for item in report['hooks']}

    def test_install_creates_executable_managed_hooks(self):
        report = self.install()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.actions(report), {'pre-commit': 'created', 'pre-push': 'created'})
        for name in ('pre-commit', 'pre-push'):
            text = (self.hooks / name).read_text(encoding='utf-8')
            self.assertEqual(text.count(leakguard.HOOK_BEGIN), 1)
            self.assertEqual(text.count(leakguard.HOOK_END), 1)
            self.assertTrue(os.access(self.hooks / name, os.X_OK))
        self.assertIn('--staged', (self.hooks / 'pre-commit').read_text(encoding='utf-8'))
        self.assertIn('--range', (self.hooks / 'pre-push').read_text(encoding='utf-8'))

    def test_existing_hook_is_preserved_and_install_is_idempotent(self):
        user_hook = '#!/bin/sh\necho user-hook-line\nexit 0\n'
        write(self.hooks / 'pre-commit', user_hook, executable=True)
        report = self.install()
        self.assertEqual(self.actions(report)['pre-commit'], 'inserted')
        text = (self.hooks / 'pre-commit').read_text(encoding='utf-8')
        self.assertIn('echo user-hook-line', text)
        self.assertIn('exit 0', text)
        self.assertEqual(text.count(leakguard.HOOK_BEGIN), 1)
        self.assertTrue(text.startswith('#!/bin/sh\n'))
        self.assertTrue(os.access(self.hooks / 'pre-commit', os.X_OK))
        again = self.install()
        self.assertEqual(self.actions(again), {'pre-commit': 'unchanged', 'pre-push': 'unchanged'})
        self.assertEqual((self.hooks / 'pre-commit').read_text(encoding='utf-8'), text)

    def test_hook_in_another_language_is_not_modified(self):
        original = '#!/usr/bin/env python3\nprint("mine")\n'
        write(self.hooks / 'pre-commit', original, executable=True)
        report = self.install()
        self.assertEqual(self.actions(report)['pre-commit'], 'skipped')
        self.assertEqual((self.hooks / 'pre-commit').read_text(encoding='utf-8'), original)

    def test_remove_restores_user_hook_and_deletes_own_files(self):
        user_hook = '#!/bin/sh\necho user-hook-line\n'
        write(self.hooks / 'pre-commit', user_hook, executable=True)
        self.install()
        report = leakguard.install_hook(self.repo, remove=True)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.actions(report), {'pre-commit': 'removed', 'pre-push': 'deleted'})
        text = (self.hooks / 'pre-commit').read_text(encoding='utf-8')
        self.assertNotIn(leakguard.HOOK_BEGIN, text)
        self.assertEqual(text, user_hook)
        self.assertFalse((self.hooks / 'pre-push').exists())
        again = leakguard.install_hook(self.repo, remove=True)
        self.assertEqual(set(self.actions(again).values()), {'absent'})

    def test_refuses_when_hooks_path_is_a_tracked_directory(self):
        write(self.repo / '.githooks' / 'pre-commit', '#!/bin/sh\necho team\n', executable=True)
        git(self.repo, 'add', '.githooks')
        commit(self.repo, 'team hooks')
        git(self.repo, 'config', 'core.hooksPath', '.githooks')
        before = (self.repo / '.githooks' / 'pre-commit').read_text(encoding='utf-8')
        report = self.install()
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertTrue(report.get('info'))
        self.assertIn('leakcheck', report['reason'])
        self.assertEqual((self.repo / '.githooks' / 'pre-commit').read_text(encoding='utf-8'), before)
        self.assertFalse((self.repo / '.githooks' / 'pre-push').exists())
        self.assertFalse((self.hooks / 'pre-commit').exists())
        process = self.cli('leakguard', 'install-hook', str(self.repo), '--identity', str(self.ident_file))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('[info]', process.stdout + process.stderr)

    def test_remove_also_refuses_for_tracked_hooks_path(self):
        write(self.repo / '.githooks' / 'pre-commit', f'#!/bin/sh\n{leakguard.hook_block("pre-commit")}', executable=True)
        git(self.repo, 'add', '.githooks')
        commit(self.repo, 'team hooks')
        git(self.repo, 'config', 'core.hooksPath', '.githooks')
        before = (self.repo / '.githooks' / 'pre-commit').read_text(encoding='utf-8')
        report = leakguard.install_hook(self.repo, remove=True)
        self.assertEqual(report['status'], 'NOT_RUN')
        self.assertEqual((self.repo / '.githooks' / 'pre-commit').read_text(encoding='utf-8'), before)

    def test_not_a_repository_is_blocked(self):
        self.assertEqual(leakguard.install_hook(self.work)['status'], 'BLOCKED')

    def test_invalid_inputs_are_blocked_before_any_write(self):
        bad = self.root / 'bad-identity.yaml'
        bad.write_text('schema: 9\n', encoding='utf-8')
        report = leakguard.install_hook(self.repo, identity_files=[bad])
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertFalse((self.hooks / 'pre-commit').exists())

    def test_pre_commit_hook_blocks_a_leaking_commit_and_allows_a_clean_one(self):
        self.install()
        write(self.repo / 'a.md', 'clean\n')
        git(self.repo, 'add', 'a.md')
        self.assertEqual(git(self.repo, 'commit', '-q', '-m', 'clean', check=False).returncode, 0)
        write(self.repo / 'b.md', f'{CLIENT}\n')
        git(self.repo, 'add', 'b.md')
        blocked = git(self.repo, 'commit', '-q', '-m', 'leak', check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertNotIn(CLIENT_LOW, (blocked.stdout + blocked.stderr).lower())
        self.assertEqual(git(self.repo, 'rev-list', '--count', 'HEAD').stdout.strip(), '1')

    def run_pre_push(self, lines, remote='origin'):
        return subprocess.run(['sh', str(self.hooks / 'pre-push'), remote, 'url'], input=lines, text=True,
                              capture_output=True, cwd=str(self.repo), env=dict(os.environ))

    def push_line(self, local, remote_sha=ZEROS):
        return f'refs/heads/main {local} refs/heads/main {remote_sha}\n'

    @needs_sh
    def test_pre_push_with_zero_remote_sha_scans_the_whole_branch(self):
        self.install()
        commit_file(self.repo, 'a.md', f'{CLIENT}\n', 'leak')
        head = commit_file(self.repo, 'b.md', 'clean\n', 'clean')
        self.assertNotEqual(self.run_pre_push(self.push_line(head)).returncode, 0)
        clean_repo = self.make_repo('clean')
        self.repo, self.hooks = clean_repo, clean_repo / '.git' / 'hooks'
        self.install()
        clean = commit_file(clean_repo, 'a.md', 'clean\n', 'clean')
        self.assertEqual(self.run_pre_push(self.push_line(clean)).returncode, 0)

    @needs_sh
    def test_pre_push_with_known_remote_sha_scans_only_new_commits(self):
        self.install()
        old = commit_file(self.repo, 'a.md', f'{CLIENT}\n', 'already pushed leak')
        fixed = commit_file(self.repo, 'a.md', 'fixed\n', 'fix')
        self.assertEqual(self.run_pre_push(self.push_line(fixed, old)).returncode, 0)
        leaking = commit_file(self.repo, 'c.md', f'{CLIENT}\n', 'new leak')
        self.assertNotEqual(self.run_pre_push(self.push_line(leaking, fixed)).returncode, 0)

    @needs_sh
    def test_pre_push_new_branch_starts_at_merge_base_with_the_default_branch(self):
        self.install()
        base = commit_file(self.repo, 'a.md', f'{CLIENT}\n', 'leak already on the default branch')
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', base)
        git(self.repo, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        git(self.repo, 'checkout', '-q', '-b', 'feature')
        head = commit_file(self.repo, 'b.md', 'clean\n', 'clean')
        line = f'refs/heads/feature {head} refs/heads/feature {ZEROS}\n'
        self.assertEqual(self.run_pre_push(line).returncode, 0)
        leaking = commit_file(self.repo, 'c.md', f'{CLIENT}\n', 'new leak')
        line = f'refs/heads/feature {leaking} refs/heads/feature {ZEROS}\n'
        self.assertNotEqual(self.run_pre_push(line).returncode, 0)

    @needs_os_symlinks
    def test_symlinked_hook_is_skipped_and_left_intact(self):
        shared = self.root / 'shared-hook'
        write(shared, '#!/bin/sh\necho shared\n', executable=True)
        os.symlink(shared, self.hooks / 'pre-commit')
        report = self.install()
        self.assertEqual(self.actions(report), {'pre-commit': 'skipped', 'pre-push': 'created'})
        self.assertTrue(report['hooks'][0].get('reason'))
        self.assertTrue((self.hooks / 'pre-commit').is_symlink())
        self.assertEqual(shared.read_text(encoding='utf-8'), '#!/bin/sh\necho shared\n')
        removed = leakguard.install_hook(self.repo, remove=True)
        self.assertEqual(self.actions(removed)['pre-commit'], 'skipped')
        self.assertTrue((self.hooks / 'pre-commit').is_symlink())

    def test_hook_that_is_not_utf8_blocks_without_modification(self):
        raw = b'#!/bin/sh\n# \xff\xfe not utf-8\n'
        (self.hooks / 'pre-commit').write_bytes(raw)
        report = self.install()
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('UTF-8', report['reason'])
        self.assertEqual((self.hooks / 'pre-commit').read_bytes(), raw)
        process = self.cli('leakguard', 'install-hook', str(self.repo), '--json')
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(json.loads(process.stdout)['status'], 'BLOCKED')
        self.assertEqual((self.hooks / 'pre-commit').read_bytes(), raw)

    @needs_sh
    def test_pre_push_of_a_merge_with_a_pushed_side_branch_is_not_rejected(self):
        self.install()
        commit_file(self.repo, 'a.md', 'base\n', 'base')
        git(self.repo, 'checkout', '-q', '-b', 'side')
        commit_file(self.repo, 's.md', f'{CLIENT}\n', 'old hit, already pushed')
        tip = commit_file(self.repo, 't.md', 'clean\n', 'clean')
        git(self.repo, 'checkout', '-q', 'main')
        commit_file(self.repo, 'm.md', 'clean\n', 'main')
        git(self.repo, 'merge', '--no-ff', '-q', '-m', 'merge side', 'side')
        merged = git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        # remote `main` stands at the pushed side tip: fast-forward to the merge
        self.assertEqual(self.run_pre_push(self.push_line(merged, tip)).returncode, 0)
        # the remote does not know the side branch at all: the hit is in the range
        base = git(self.repo, 'rev-list', '--max-parents=0', 'HEAD').stdout.strip()
        self.assertNotEqual(self.run_pre_push(self.push_line(merged, base)).returncode, 0)

    @needs_sh
    def test_pre_push_ignores_deleted_refs_and_forwards_stdin_to_later_hook_code(self):
        captured = self.root / 'captured-stdin.txt'
        write(self.hooks / 'pre-push', f'#!/bin/sh\ncat > "{captured}"\n', executable=True)
        self.install()
        head = commit_file(self.repo, 'a.md', 'clean\n', 'clean')
        lines = f'(delete) {ZEROS} refs/heads/old {head}\n' + self.push_line(head)
        process = self.run_pre_push(lines)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(captured.read_text(encoding='utf-8'), lines)

    def test_pre_push_with_a_strict_shell_hook_and_no_remote_head_is_not_rejected(self):
        bare = self.work / 'remote.git'
        git(self.work, 'init', '-q', '--bare', str(bare))
        git(self.repo, 'remote', 'add', 'origin', str(bare))
        self.assertFalse((self.repo / '.git' / 'refs' / 'remotes' / 'origin' / 'HEAD').exists())
        marker = self.root / 'user-hook-ran.txt'
        write(self.hooks / 'pre-push', f'#!/bin/sh -e\necho ran > "{marker}"\n', executable=True)
        self.install()
        commit_file(self.repo, 'a.md', 'clean\n', 'clean')
        pushed = git(self.repo, 'push', 'origin', 'main', check=False)
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        self.assertTrue(marker.exists())
        commit_file(self.repo, 'b.md', f'{CLIENT}\n', 'leak')
        rejected = git(self.repo, 'push', 'origin', 'main', check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(git(bare, 'rev-list', '--count', 'main').stdout.strip(), '1')


class AnnotatedTagScanTest(LeakTestCase):
    """D173/D174: annotated tags in `leakcheck --range` (the `pre-push` hook calls the same API)."""
    MARKER = '-----BEGIN PGP SIGNATURE-----'
    END = '-----END PGP SIGNATURE-----'

    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.head = commit_file(self.repo, 'a.md', 'clean\n', 'clean')

    def tag(self, name, message):
        message_file = self.root / f'tag-message-{name}.txt'
        message_file.write_text(message, encoding='utf-8')
        git(self.repo, 'tag', '-a', '--cleanup=verbatim', '-F', str(message_file), name, self.head)
        return name

    def range_check(self, tip, scope='org-private', kind='org'):
        targets = {'mine': {'scope': scope, 'kind': kind}, 'other': {'scope': 'private'}}
        return self.check(self.repo, [term(CLIENT)], targets=targets, mode='range', rev_range=tip)

    def test_text_between_the_first_and_the_last_signature_marker_is_scanned(self):
        # git takes the signature from the LAST marker; everything before it is visible tag text
        tip = self.tag('v1', f'release\n{self.MARKER}\nnot a signature\nabout {CLIENT} here\n{self.MARKER}\n'
                             f'c2lnbmF0dXJl\n{self.END}\n')
        report = self.range_check(tip)
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())

    def test_text_only_inside_the_signature_after_the_last_marker_is_not_a_hit(self):
        tip = self.tag('v1', f'release\n{self.MARKER}\nc2ln{CLIENT}bmF0dXJl\n{CLIENT}\n{self.END}\n')
        report = self.range_check(tip)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(report['findings'], [])

    def test_the_name_of_an_annotated_tag_is_scanned(self):
        tip = self.tag(f'v1-{CLIENT}', 'release notes\n')
        for scope, kind in (('public', 'core'), ('private', 'personal')):
            with self.subTest(scope=scope):
                report = self.range_check(tip, scope, kind)
                self.assertEqual(report['status'], 'FAIL', report)
                self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())
        clean = self.tag('v2-release', 'release notes\n')                # control: a clean name passes
        self.assertEqual(self.range_check(clean, 'public', 'core')['status'], 'PASS')


class ConfigPrePushHookTest(LayersCase):
    """Review 6.1-1: the `pre-push` block installed from the machine config, run by a real `git push`.

    M1: the block fails closed when its temporary file cannot be written (it also runs under a
    `#!/bin/bash` shebang of an existing hook). D173: annotated tags are scanned too."""

    def setUp(self):
        super().setUp()
        beta = self.git_layer('beta', 'org', identity_terms=[{'term': CLIENT, 'kind': 'org'}])
        self.mine = self.git_layer('mine', 'personal')
        self.write_config([('beta', 'org', beta), ('mine', 'personal', self.mine)],
                          leakguard={'targets': {'mine': {'scope': 'private', 'remote_match': [],
                                                          'authors': ['dev@example.test']},
                                                 'beta': {'scope': 'org-private'}}})
        self.bare = self.work / 'remote.git'
        git(self.work, 'init', '-q', '--bare', str(self.bare))
        git(self.mine, 'remote', 'add', 'origin', str(self.bare))
        self.tmp_ok = self.root / 'tmp-ok'
        self.tmp_ok.mkdir()
        self.tmp_missing = self.root / 'tmp-missing' / 'gone'          # never created: TMPDIR of a removed dir
        self.marker = self.root / 'user-hook-ran.txt'
        self.hook = self.mine / '.git' / 'hooks' / 'pre-push'

    def install(self, shebang=None):
        if shebang:
            write(self.hook, f'{shebang}\necho ran >> "{self.marker}"\nexit 0\n', executable=True)
        process = self.cli('leakguard', 'install-hook', str(self.mine))     # no explicit inputs: from the config
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(leakguard.hook_state(self.mine)['hooks']['pre-push'], 'managed')
        text = self.hook.read_text(encoding='utf-8')
        if shebang:
            self.assertTrue(text.startswith(f'{shebang}\n{leakguard.HOOK_BEGIN}\n'), text[:200])

    def push(self, tmpdir, *refs):
        return git(self.mine, 'push', 'origin', *refs, env={'TMPDIR': str(tmpdir)}, check=False)

    def remote(self, ref):
        found = git(self.bare, 'rev-parse', '-q', '--verify', ref, check=False)
        return found.stdout.strip() if found.returncode == 0 else None

    def assert_fails_closed(self, shebang):
        self.install(shebang)
        head = commit_file(self.mine, 'a.md', 'clean\n', 'clean')
        self.assertFalse(self.tmp_missing.parent.exists())
        rejected = self.push(self.tmp_missing, 'main')
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn('skillsync leakguard', rejected.stderr)
        self.assertIsNone(self.remote('refs/heads/main'))
        self.assertFalse(self.marker.exists())                              # the block stopped before the user code
        self.assertFalse(self.tmp_missing.parent.exists())
        # positive control: the same clean push with a usable TMPDIR passes and runs the user hook
        passed = self.push(self.tmp_ok, 'main')
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(self.remote('refs/heads/main'), head)
        self.assertTrue(self.marker.exists())
        self.assertEqual(list(self.tmp_ok.iterdir()), [])                   # the buffer file is removed
        # the block from the config still blocks a leak (end-to-end, review 6.1-1 point 4e)
        commit_file(self.mine, 'b.md', f'{CLIENT}\n', 'leak')
        leaking = self.push(self.tmp_ok, 'main')
        self.assertNotEqual(leaking.returncode, 0)
        self.assertNotIn(CLIENT_LOW, (leaking.stdout + leaking.stderr).lower())
        self.assertEqual(self.remote('refs/heads/main'), head)

    @unittest.skipUnless(Path('/bin/bash').exists(), 'no /bin/bash')
    def test_pre_push_under_a_bash_hook_fails_closed_without_a_temporary_file(self):
        self.assert_fails_closed('#!/bin/bash')

    @needs_sh
    @unittest.skipUnless(Path('/bin/sh').exists(), 'no /bin/sh')
    def test_pre_push_under_a_sh_hook_fails_closed_without_a_temporary_file(self):
        self.assert_fails_closed('#!/bin/sh')

    @unittest.skipUnless(Path('/bin/zsh').exists(), 'no /bin/zsh (the block under a zsh shebang needs zsh)')
    def test_pre_push_under_a_zsh_hook_fails_closed_without_a_temporary_file(self):
        self.assert_fails_closed('#!/bin/zsh')

    @unittest.skipUnless(Path('/bin/bash').exists(), 'no /bin/bash')
    def test_pre_push_fails_closed_when_buffering_into_an_existing_temporary_file_fails(self):
        # The temporary file is created (usable TMPDIR), but writing the ref list into it fails: a `cat`
        # stand-in first on PATH drains stdin, writes nothing and exits 1 (deterministic write error).
        fake_bin = self.root / 'fake-bin'
        write(fake_bin / 'cat', '#!/bin/sh\nwhile IFS= read -r _line; do :; done\nexit 1\n', executable=True)
        self.install('#!/bin/bash')
        commit_file(self.mine, 'a.md', 'clean\n', 'clean')
        env = {'TMPDIR': str(self.tmp_ok), 'PATH': f'{fake_bin}{os.pathsep}{os.environ.get("PATH", "")}'}
        rejected = git(self.mine, 'push', 'origin', 'main', env=env, check=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
        self.assertIn('skillsync leakguard', rejected.stderr)
        self.assertIsNone(self.remote('refs/heads/main'))
        self.assertFalse(self.marker.exists())                              # the block stopped before the user code
        self.assertEqual(list(self.tmp_ok.iterdir()), [])                   # the created buffer file is removed
        passed = self.push(self.tmp_ok, 'main')                             # control: the real `cat`
        self.assertEqual(passed.returncode, 0, passed.stderr)

    @needs_sh
    def test_pre_push_scans_annotated_tag_messages(self):
        self.install()
        head = commit_file(self.mine, 'a.md', 'clean\n', 'clean')
        self.assertEqual(self.push(self.tmp_ok, 'main').returncode, 0)
        # the commit is already on the remote: only the tag object carries the term
        git(self.mine, 'tag', '-a', 'v1', '-m', f'release for {CLIENT}', head)
        rejected = self.push(self.tmp_ok, 'refs/tags/v1')
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)
        output = rejected.stdout + rejected.stderr
        self.assertTrue('skillsync leakguard' in output or 'leakcheck: FAIL' in output, output)
        self.assertNotIn(CLIENT_LOW, output.lower())
        self.assertIsNone(self.remote('refs/tags/v1'))
        # the same commit with a clean annotated tag passes
        git(self.mine, 'tag', '-a', 'v2', '-m', 'release notes', head)
        clean = self.push(self.tmp_ok, 'refs/tags/v2')
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIsNotNone(self.remote('refs/tags/v2'))
        # lightweight tags are unchanged: a clean commit passes, an unpushed leaking commit is rejected
        git(self.mine, 'tag', 'v3', head)
        self.assertEqual(self.push(self.tmp_ok, 'refs/tags/v3').returncode, 0)
        leak = commit_file(self.mine, 'b.md', f'{CLIENT}\n', 'leak')
        git(self.mine, 'tag', 'v4', leak)
        self.assertNotEqual(self.push(self.tmp_ok, 'refs/tags/v4').returncode, 0)
        self.assertIsNone(self.remote('refs/tags/v4'))


# ====================================================================== in-memory API and edge inputs


class PrepareScanTextsTest(LeakTestCase):
    TARGETS = {'mine': {'scope': 'org-private', 'kind': 'core', 'authors': ['dev@corp.example']},
               'other': {'scope': 'private'}}

    def prepared(self, targets=None, **kwargs):
        ident = self.ident('other', [term(NICK, 'owner-nick'), term(CLIENT)])
        return leakguard.prepare(target='mine', targets=targets or self.TARGETS, identity_files=[ident], **kwargs)

    def test_path_class_follows_the_given_relative_path_and_target_kind(self):
        report = leakguard.scan_texts([('instructions/a.md', f'x {NICK} y'), ('CHANGELOG.md', f'x {NICK} y'),
                                       ('docs/b.md', f'x {CLIENT} y')], self.prepared())
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(signature(report), [('docs/b.md', 'identity-org'), ('instructions/a.md', 'identity-owner-nick')])
        self.assertEqual((report['coverage'], report['target']), ('full', 'mine'))
        self.assertNotIn(CLIENT_LOW, json.dumps(report).lower())

    def test_clean_texts_pass_and_generic_patterns_apply(self):
        prepared = self.prepared()
        self.assertEqual(leakguard.scan_texts([('a.md', 'clean')], prepared)['status'], 'PASS')
        report = leakguard.scan_texts([('a.md', f'see {HOME}/alice/x')], prepared)
        self.assertEqual((report['status'], categories(report)), ('FAIL', {'owner-path'}))

    def test_filename_surface_checks_the_path_only(self):
        prepared = self.prepared()
        report = leakguard.scan_texts([(f'docs/{NICK.lower()}.md', f'{CLIENT}')], prepared, surface='filename')
        self.assertEqual({surface_of(item) for item in report['findings']}, {'filename'})
        self.assertEqual(leakguard.scan_texts([('docs/ok.md', f'{CLIENT}')], prepared, surface='filename')['status'],
                         'PASS')
        with self.assertRaises(leakguard.LeakguardError):
            leakguard.scan_texts([('a.md', 'x')], prepared, surface='commit')

    def test_exemption_applies_to_target_additions_path_only(self):
        body = yaml.safe_dump({'schema': 1, 'patterns': [{'id': 'p', 'category': 'custom', 'regex': D27_SOURCE.lower()}]})
        prepared = self.prepared()
        self.assertEqual(leakguard.scan_texts([(leakguard.TARGET_PATTERNS_REL, body)], prepared)['status'], 'PASS')
        self.assertEqual(leakguard.scan_texts([('other/p.yaml', body)], prepared)['status'], 'FAIL')

    def test_target_without_authors_warns_and_target_with_authors_does_not(self):
        without = dict(self.TARGETS, mine={'scope': 'org-private', 'kind': 'core'})
        warned = leakguard.scan_texts([('a.md', 'x')], self.prepared(without))
        self.assertTrue(any('authors' in item.lower() for item in warned['warnings']), warned['warnings'])
        report = leakguard.scan_texts([('a.md', 'x')], self.prepared())
        self.assertFalse(any('authors' in item.lower() for item in report['warnings']), report['warnings'])
        full = leakguard.leakcheck(self.tree({'a.md': 'x\n'}), target='mine', targets=without)
        self.assertTrue(any('authors' in item.lower() for item in full['warnings']))

    def test_invalid_inputs_raise(self):
        bad = self.root / 'bad.yaml'
        bad.write_text('schema: 9\n', encoding='utf-8')
        with self.assertRaises(leakguard.LeakguardError):
            leakguard.prepare(target='mine', targets=self.TARGETS, identity_files=[bad])
        with self.assertRaises(leakguard.LeakguardError):
            leakguard.prepare(target='mine', targets={'mine': {'scope': 'nope'}})
        with self.assertRaises(leakguard.LeakguardError):
            self.prepared(target_patterns=bad)


class EdgeInputsTest(LeakTestCase):
    def check_repo(self, repo, **kwargs):
        return self.check(repo, [term(CLIENT)], **kwargs)

    @needs_os_symlinks
    def test_symlink_content_is_its_target_address_not_the_target_file(self):
        repo = self.make_repo()
        write(repo / 'real.txt', f'{CLIENT}\n')
        git(repo, 'add', 'real.txt')
        commit(repo, 'real')
        (repo / 'real.txt').unlink()
        write(self.work / 'outside.txt', f'{CLIENT}\n')
        os.symlink('../outside.txt', repo / 'clean-link')  # relative: independent of where TMPDIR lives
        self.assertEqual(self.check_repo(repo)['status'], 'PASS')  # not followed
        os.symlink(f'../{CLIENT_LOW}-dir/file', repo / 'leaky-link')
        report = self.check_repo(repo)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue({item['path'] for item in report['findings']} <= {'leaky-link', 'real.txt'})
        plain = self.fresh_dir('plain')
        write(self.work / 'outside2.txt', f'{CLIENT}\n')
        os.symlink('../outside2.txt', plain / 'clean-link')
        self.assertEqual(self.check(plain, [term(CLIENT)])['status'], 'PASS')
        os.symlink(f'{CLIENT_LOW}-target', plain / 'leaky-link')
        self.assertEqual(self.check(plain, [term(CLIENT)])['status'], 'FAIL')

    def test_binary_files_are_skipped_but_their_names_are_checked(self):
        path = self.fresh_dir('bin')
        (path / 'blob.bin').write_bytes(b'\x00\x01' + CLIENT.encode() + b'\x00')
        self.assertEqual(self.check(path, [term(CLIENT)])['status'], 'PASS')
        (path / f'{CLIENT_LOW}.bin').write_bytes(b'\x00')
        report = self.check(path, [term(CLIENT)])
        self.assertEqual({surface_of(item) for item in report['findings']}, {'filename'})

    def test_large_file_is_scanned_without_error(self):
        path = self.fresh_dir('big')
        filler = ('lorem ipsum dolor sit amet ' * 40 + '\n') * 2500  # about 2.7 MB
        write(path / 'big.txt', filler + f'tail {CLIENT}\n')
        report = self.check(path, [term(CLIENT)])
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['findings'][0]['location'].split(':')[0], '2501')

    def test_gitignored_files_are_not_scanned_but_untracked_ones_are(self):
        repo = self.make_repo()
        commit_file(repo, '.gitignore', 'ignored.txt\n', 'ignore')
        write(repo / 'ignored.txt', f'{CLIENT}\n')
        self.assertEqual(self.check_repo(repo)['status'], 'PASS')
        write(repo / 'untracked.txt', f'{CLIENT}\n')
        report = self.check_repo(repo)
        self.assertEqual({item['path'] for item in report['findings']}, {'untracked.txt'})

    def test_a_subdirectory_is_a_scan_root_in_every_mode(self):
        repo = self.make_repo()
        commit_file(repo, 'outside.md', f'{CLIENT}\n', 'leak outside')
        commit_file(repo, 'sub/inside.md', 'clean\n', 'clean inside')
        sub = repo / 'sub'
        self.assertEqual(self.check_repo(sub)['status'], 'PASS')
        self.assertEqual(self.check_repo(sub, mode='staged')['status'], 'PASS')
        write(sub / 'inside.md', f'{CLIENT}\n')
        report = self.check_repo(sub)
        self.assertEqual({item['path'] for item in report['findings']}, {'inside.md'})
        git(repo, 'add', 'sub/inside.md')
        report = self.check_repo(sub, mode='staged')
        self.assertEqual({item['path'] for item in report['findings']}, {'inside.md'})
        commit(repo, 'leak inside')
        report = self.check_repo(sub, mode='history')
        self.assertEqual({item['path'] for item in report['findings']}, {'inside.md'})

    def merge_scenario(self):
        """base; side: s1 (leak), s2 (clean); main: m1 (clean); M = merge of side into main."""
        repo = self.make_repo()
        base = commit_file(repo, 'a.md', 'base\n', 'base')
        git(repo, 'checkout', '-q', '-b', 'side')
        s1 = commit_file(repo, 's.md', f'{CLIENT}\n', 'side leak')
        s2 = commit_file(repo, 't.md', 'clean\n', 'side clean')
        git(repo, 'checkout', '-q', 'main')
        m1 = commit_file(repo, 'm.md', 'clean\n', 'main clean')
        git(repo, 'merge', '--no-ff', '-q', '-m', 'merge side', 'side')
        merged = git(repo, 'rev-parse', 'HEAD').stdout.strip()
        return repo, dict(base=base, s1=s1, s2=s2, m1=m1, merged=merged)

    def test_range_merge_of_a_pushed_side_branch_with_old_hit_outside_the_range_passes(self):
        repo, c = self.merge_scenario()
        report = self.check_repo(repo, mode='range', rev_range=f'{c["s2"]}..{c["merged"]}')
        self.assertEqual((report['status'], report['findings']), ('PASS', []), report)

    def test_range_containing_the_side_commit_reports_the_hit_exactly_once(self):
        repo, c = self.merge_scenario()
        for rev_range in (f'{c["base"]}..{c["merged"]}', c['merged']):
            with self.subTest(rev_range=rev_range[:12]):
                report = self.check_repo(repo, mode='range', rev_range=rev_range)
                self.assertEqual(report['status'], 'FAIL')
                self.assertEqual([(item['path'], item['category']) for item in report['findings']],
                                 [('s.md', 'identity-org')])
        self.assertEqual(len(self.check_repo(repo, mode='history')['findings']), 1)

    def test_range_from_the_first_parent_excludes_only_what_it_contains(self):
        repo, c = self.merge_scenario()
        # side commits are not reachable from m1, so they are in the range and the hit is reported once
        report = self.check_repo(repo, mode='range', rev_range=f'{c["m1"]}..{c["merged"]}')
        self.assertEqual([item['path'] for item in report['findings']], ['s.md'])

    def test_merge_commit_with_conflict_resolution_content_is_scanned(self):
        repo = self.make_repo()
        commit_file(repo, 'f.md', 'base\n', 'base')
        git(repo, 'checkout', '-q', '-b', 'side')
        commit_file(repo, 'f.md', 'side change\n', 'side')
        git(repo, 'checkout', '-q', 'main')
        main_tip = commit_file(repo, 'f.md', 'main change\n', 'main')
        merge = git(repo, 'merge', '--no-edit', 'side', check=False)
        self.assertNotEqual(merge.returncode, 0)  # conflict
        write(repo / 'f.md', f'resolved with {CLIENT}\n')
        git(repo, 'add', 'f.md')
        merged = commit(repo, 'merge side')
        self.assertEqual(self.check_repo(repo)['status'], 'FAIL')  # worktree sees it
        for mode, extra in (('history', {}), ('range', {'rev_range': f'{main_tip}..{merged}'})):
            with self.subTest(mode=mode):
                report = self.check_repo(repo, mode=mode, **extra)
                self.assertEqual(report['status'], 'FAIL', report)
                self.assertEqual([item['path'] for item in report['findings']], ['f.md'])  # exactly one report


# ====================================================================== isolation


class IsolationTest(LeakTestCase):
    def test_environment_is_redirected_into_the_temp_root(self):
        for name in ('HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'LOCALAPPDATA', 'APPDATA',
                     'GIT_CONFIG_GLOBAL', 'SKILLSYNC_CONFIG'):
            with self.subTest(var=name):
                self.assertTrue(Path(os.environ[name]).resolve().is_relative_to(self.root), name)
        self.assertEqual(os.environ['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertNotEqual(Path(os.environ['HOME']).resolve(), REAL_HOME.resolve())

    def test_a_full_run_leaves_the_real_state_directories_untouched(self):
        before = real_state_snapshot()
        repo = self.make_repo()
        commit_file(repo, 'a.md', f'{CLIENT}\n', 'x')
        ident = self.ident('other', [term(CLIENT)])
        leakguard.leakcheck(repo, identity_files=[ident], mode='history')
        leakguard.install_hook(repo, identity_files=[ident])
        self.cli('leakcheck', str(repo), '--identity', str(ident), '--history', '--json')
        self.cli('leakguard', 'patterns', '--json')
        self.assertEqual(real_state_snapshot(), before)

    def test_the_snapshot_detects_changes_in_the_watched_directories(self):
        watched = self.root / 'fake-real-state'
        with mock.patch('layers_helpers.REAL_STATE_DIRS', (watched,)):
            before = real_state_snapshot()
            watched.mkdir()
            (watched / 'cache.json').write_text('{}', encoding='utf-8')
            self.assertNotEqual(real_state_snapshot(), before)

    def test_link_modes_helper_sets_and_clears_the_variable(self):
        original = os.environ.get('SKILLSYNC_NO_SYMLINK')
        self.assertEqual(LINK_MODES, ('symlink', 'copy'))
        with link_mode('symlink'):
            self.assertIsNone(os.environ.get('SKILLSYNC_NO_SYMLINK'))
        with link_mode('copy'):
            self.assertEqual(os.environ.get('SKILLSYNC_NO_SYMLINK'), '1')
        self.assertEqual(os.environ.get('SKILLSYNC_NO_SYMLINK'), original)  # restored after the block


# ============================================================ hook command line (K5): the self command in the hook

TRICKY_ARGV = ["/opt/my py's/bin/python", "/srv/o'brien/my tools/skillsync"]
INSTALLED_ARGV = ['/opt/py/bin/python', '-P', '-m', 'skillsync_workflow']


def command_line(block):
    """The `leakcheck` command line of a managed block, split like the shell does."""
    import shlex
    lines = [line for line in block.splitlines() if ' leakcheck' in line and not line.lstrip().startswith(('#', 'echo'))]
    assert lines, block
    return shlex.split(lines[0])


class HookCommandFormTest(LeakTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()
        self.hooks = self.repo / '.git' / 'hooks'

    def block(self, name, argv):
        with mock.patch.object(leakguard, 'self_argv', return_value=list(argv)):
            return leakguard.hook_block(name)

    def test_every_element_of_the_self_command_is_shell_quoted(self):
        import shlex
        for name in ('pre-commit', 'pre-push'):
            for label, argv in (('tricky', TRICKY_ARGV), ('installed', INSTALLED_ARGV)):
                with self.subTest(hook=name, argv=label):
                    text = self.block(name, argv)
                    self.assertIn(' '.join(shlex.quote(item) for item in argv) + ' leakcheck', text)
                    self.assertEqual(command_line(text)[:len(argv) + 1], list(argv) + ['leakcheck'])

    @needs_sh
    def test_the_block_with_apostrophes_and_spaces_is_valid_shell(self):
        for label, argv in (('tricky', TRICKY_ARGV), ('installed', INSTALLED_ARGV)):
            with self.subTest(argv=label):
                path = self.work / f'hook-{label}.sh'
                write(path, '#!/bin/sh\n' + self.block('pre-commit', argv) + self.block('pre-push', argv))
                self.assertEqual(subprocess.run(['sh', '-n', str(path)], capture_output=True, text=True).returncode, 0)

    def test_the_real_self_command_is_what_the_block_calls(self):
        from skillsync_workflow.selfcmd import self_argv
        expected = [Path(item).as_posix() if os.path.isabs(item) else item for item in self_argv()]
        self.assertEqual(command_line(leakguard.hook_block('pre-commit'))[:len(expected) + 1], expected + ['leakcheck'])

    def test_managed_block_of_either_form_is_recognised_and_removed(self):
        for label, argv in (('checkout', [sys.executable, str(SCRIPT)]), ('installed', INSTALLED_ARGV),
                            ('spaces', TRICKY_ARGV)):
            with self.subTest(argv=label):
                with mock.patch.object(leakguard, 'self_argv', return_value=list(argv)):
                    self.assertEqual(leakguard.install_hook(self.repo)['status'], 'PASS')
                self.assertEqual(leakguard.hook_state(self.repo)['hooks'],
                                 {'pre-commit': 'managed', 'pre-push': 'managed'})
                self.assertEqual(leakguard.install_hook(self.repo, remove=True)['status'], 'PASS')
                self.assertEqual(leakguard.hook_state(self.repo)['state'], 'missing')

    def test_manual_call_in_a_hook_is_recognised_in_both_forms(self):
        calls = {
            'wrapper': f'{sys.executable} {SCRIPT} leakcheck "$(git rev-parse --show-toplevel)" --staged',
            'wrapper with spaces': "'/opt/my py/bin/python' '/srv/o brien/my tools/skillsync' leakcheck . --staged",
            'wrapper, Windows': 'python C:\\tools\\skillsync leakcheck . --staged',
            'module': f'{sys.executable} -P -m skillsync_workflow leakcheck . --staged',
            'module, quoted': "'/opt/my py/bin/python' -P -m 'skillsync_workflow' leakcheck . --staged",
            'installed shim': 'skillsync leakcheck . --staged',
            'chained': 'true && python -P -m skillsync_workflow leakcheck . --staged',
        }
        for label, line in calls.items():
            with self.subTest(label):
                for name in leakguard.HOOK_NAMES:
                    write(self.hooks / name, f'#!/bin/sh\n{line}\n', executable=True)
                state = leakguard.hook_state(self.repo)
                self.assertEqual((state['state'], state['hooks']), ('ok', {'pre-commit': 'call', 'pre-push': 'call'}))

    def test_look_alikes_are_not_a_leakcheck_call(self):
        lines = {
            'other module': 'python -P -m some_module leakcheck .',
            'module name only': 'echo skillsync_workflow',
            'longer module name': 'python -P -m skillsync_workflow_extra leakcheck .',
            'comment': '# python -P -m skillsync_workflow leakcheck .',
            'different command': 'python -P -m skillsync_workflow doctor .',
            'longer script name': '/x/myskillsync leakcheck .',
        }
        for label, line in lines.items():
            with self.subTest(label):
                for name in leakguard.HOOK_NAMES:
                    write(self.hooks / name, f'#!/bin/sh\n{line}\n', executable=True)
                state = leakguard.hook_state(self.repo)
                self.assertEqual((state['state'], state['hooks']), ('missing', {'pre-commit': 'missing', 'pre-push': 'missing'}))

    def test_the_leakcheck_pattern_itself(self):
        pattern = leakguard._LEAKCHECK_CALL
        for line in ('python -P -m skillsync_workflow leakcheck x', "/a b/skillsync' leakcheck x", 'skillsync.exe leakcheck',
                     'skillsync.py leakcheck x'):
            with self.subTest(line=line):
                self.assertTrue(pattern.search(line))
        for line in ('python -m skillsync_workflow_x leakcheck', 'skillsync_workflow', 'skillsync leakchecks'):
            with self.subTest(line=line):
                self.assertFalse(pattern.search(line))


if __name__ == '__main__':
    import unittest
    unittest.main()
