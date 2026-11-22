"""Kontrola motywu prototypów (`library/skills/prototype/scripts/theme_check.py`), kroki T4-1 i T4-2 planu CORE-1140-T4.

Testy czytają `REPO/library` (nie zależą od SKILLSYNC_TEST_LIBRARY); fikstury są syntetyczne, w katalogach tymczasowych.

Spis parytetu przypadków źródłowych (załącznik K9; uzupełniany w kolejnych krokach, `(T4-n)` = jeszcze nie ten krok):

| przypadek                                                     | źródło                  | test                                           |
|---------------------------------------------------------------|-------------------------|------------------------------------------------|
| kopia równa po CRLF i BOM                                     | testy warstwy           | CopiesTests.test_crlf_and_bom_are_ignored      |
| kopia inna: FAIL z nazwą pliku                                | testy warstwy           | CopiesTests.test_changed_copy_fails_and_names_the_file |
| brak kopii                                                    | testy warstwy           | CopiesTests.test_missing_copy_fails            |
| wymagane tokeny muszą być zdefiniowane                        | testy warstwy           | TokensTests.test_missing_required_token_fails  |
| błędy wywołania (brak/zła konfiguracja) = kod 2               | testy warstwy           | ConfigTests, CliProcessTests.test_usage_errors |
| brak pliku fontu w kopii                                      | testy warstwy           | CopyDirsTests.test_missing_font_file_fails     |
| inne bajty fontu                                              | testy warstwy           | CopyDirsTests.test_changed_font_bytes_fail     |
| brak LICENSE                                                  | testy warstwy           | CopyDirsTests.test_missing_license_fails       |
| kopia kompletna                                               | testy warstwy           | CopyDirsTests.test_complete_copy_passes        |
| manifest bez `root`, tryb aplikacji (plik tokenów aplikacji)  | testy warstwy           | CopiesTests.test_single_app_file_copy (opakowanie: T4b) |
| token tylko w bloku ciemnym                                   | strażnik rdzenia        | TokensTests.test_token_only_in_dark_block_fails |
| bloki ciemne z różnymi wartościami                            | strażnik rdzenia        | DarkTests.test_separate_different_values_fail  |
| lista wymaganych w `utf-8-sig`                                | strażnik rdzenia        | TokensTests.test_required_list_and_theme_accept_bom |
| pusty zbiór znanych tokenów (T4-4)                            | strażnik rdzenia        | (T4-4: kontrola prototype)                     |
| `font-family: inherit`, pusty `project.css`, `var(--x, fb)`, maskowanie `url()` i napisów, długie formy promienia, numery linii stylu (T4-4) | strażnik / skrypt projektu | (T4-4: kontrola prototype) |
| numery linii w findingach tokenów i forbid                    | strażnik / skrypt proj. | TokensTests.test_finding_has_file_and_line, ForbidTests.test_color_scheme_fails_with_line |
| selftest skryptu projektowego, część ogólna: dark mirror, forbid                | skrypt projektowy       | DarkTests (mirror), ForbidTests                |
| selftest skryptu projektowego, część ogólna: kontrast (T4-3), reguły stylu (T4-4) | skrypt projektowy | (T4-3, T4-4)                           |
| D-1 token tylko ciemny uznany za zdefiniowany                 | defekt D-1              | TokensTests.test_token_only_in_dark_block_fails |
| D-2 brak listy: kod 2, nie pominięcie                         | defekt D-2              | ConfigTests.test_missing_required_list_is_exit_2 |
| D-4 drugi `:root`, `@theme`, `@theme inline`                  | defekt D-4              | TokensTests.test_second_root_and_theme_blocks   |
| D-5 formaty kolorów, UNVERIFIED (T4-3, kontrast)              | defekt D-5              | (T4-3)                                         |
| równość zbioru tokenów zasobów z listą                        | strażnik rdzenia        | AssetsTests                                    |
| zasób lub plik tokenów w złym kodowaniu, rozmiar, zagnieżdżenie | przegląd T4-1/T4-2    | SafetyNetTests                                 |
| D-6 deklaracja w atrybucie `style`                            | defekt D-6              | test_skillsync_css_tokens.UsedTokensTests      |
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)
import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import descend  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'library' / 'skills' / 'prototype' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import theme_check as tc  # noqa: E402

LIGHT = ':root { --a: 1; --b: 2; }\n'
DARK = '[data-theme="dark"] { --a: 3; --b: 4; }\n'
LINE_RE = re.compile(r'^(PASS|FAIL|WARN|UNVERIFIED|INFO) [A-Za-z]+: .+$')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='theme-check-'))
        self.addCleanup(self.cleanup)
        self.project = self.tmp / 'project'
        self.project.mkdir()

    def cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content, base=None):
        path = descend(base or self.project, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        return path

    def run_cfg(self, config, **kw):
        return tc.run(config, self.project, **kw)

    def statuses(self, res, check=None):
        return [f['status'] for f in res.findings if f['status'] != tc.INFO and (check is None or f['check'] == check)]

    def tokens_cfg(self, theme, required='--a\n--b\n', **extra):
        self.write('t.css', theme)
        self.write('req.txt', required)
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt'}}
        cfg.update(extra)
        return cfg


class ConfigTests(Base):
    def test_empty_config_is_unverified_with_code_3(self):
        res = self.run_cfg({'version': 1})
        self.assertEqual((res.code, self.statuses(res)), (3, [tc.UNVERIFIED]))

    def test_version_is_required_and_must_be_1(self):
        for config in ({}, {'version': 2}, {'version': True}, {'version': '1'}, []):
            with self.subTest(config=config):
                self.assertEqual(self.run_cfg(config).code, 2)

    def test_unknown_keys_are_exit_2_without_quoting_them(self):
        sentinel = 'SENTINEL_KEY_9f3a'
        for config in ({'version': 1, sentinel: 1}, {'version': 1, 'tokens': {'files': [], 'required': None, sentinel: 1}},
                       {'version': 1, 'copies': [{'source': 'a', 'copy': 'b', sentinel: 1}]}):
            with self.subTest(config=config):
                res = self.run_cfg(config)
                self.assertEqual(res.code, 2)
                self.assertNotIn(sentinel, res.error)

    def test_contrast_and_prototype_are_not_implemented_yet(self):
        for key in ('contrast', 'prototype'):
            with self.subTest(key=key):
                res = self.run_cfg({'version': 1, key: {}})
                self.assertEqual(res.code, 2)
                self.assertIn('not implemented yet', res.error)

    def test_missing_required_list_is_exit_2(self):
        """D-2: brak pliku listy wymaganych to kod 2, nie ciche pominięcie."""
        self.write('t.css', LIGHT)
        res = self.run_cfg({'version': 1, 'tokens': {'files': ['t.css'], 'required': 'brak.txt'}})
        self.assertEqual(res.code, 2)
        self.assertEqual(res.findings, [])

    def test_required_key_is_mandatory_and_null_is_an_explicit_opt_out(self):
        self.write('t.css', LIGHT + DARK)
        self.assertEqual(self.run_cfg({'version': 1, 'tokens': {'files': ['t.css']}}).code, 2)
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'dark': 'separate'}
        res = self.run_cfg(cfg)
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.WARN, tc.PASS]))
        self.assertIn('pokrycie wyłączone', res.findings[0]['message'])
        self.assertEqual(self.run_cfg(cfg, only=['tokens']).code, 2)

    def test_dark_and_forbid_need_the_tokens_key(self):
        self.assertEqual(self.run_cfg({'version': 1, 'dark': 'separate'}).code, 2)
        self.assertEqual(self.run_cfg({'version': 1, 'forbid': ['color-scheme']}).code, 2)

    def test_dark_mode_and_forbid_values_are_validated(self):
        cfg = self.tokens_cfg(LIGHT)
        self.assertEqual(self.run_cfg(dict(cfg, dark='invert')).code, 2)
        self.assertEqual(self.run_cfg(dict(cfg, forbid=['gradient'])).code, 2)

    def test_only_unknown_or_not_configured_is_exit_2(self):
        cfg = self.tokens_cfg(LIGHT + DARK)
        self.assertEqual(self.run_cfg(cfg, only=['bogus']).code, 2)
        self.assertEqual(self.run_cfg(cfg, only=['dark']).code, 2)
        self.assertEqual(self.run_cfg(cfg, only=['tokens']).code, 0)

    def test_copy_dirs_requires_an_explicit_nonempty_include(self):
        for item in ({'source': 's', 'copy': 'd'}, {'source': 's', 'copy': 'd', 'include': []},
                     {'source': 's', 'copy': 'd', 'include': '*'}):
            with self.subTest(item=item):
                self.assertEqual(self.run_cfg({'version': 1, 'copyDirs': [item]}).code, 2)

    def test_empty_forbid_copies_and_copy_dirs_lists_are_exit_2(self):
        for key in ('forbid', 'copies', 'copyDirs'):
            with self.subTest(key=key):
                cfg = self.tokens_cfg(LIGHT) if key == 'forbid' else {'version': 1}
                cfg[key] = []
                self.assertEqual(self.run_cfg(cfg).code, 2)

    def test_only_reports_skipped_checks_as_info(self):
        cfg = self.tokens_cfg(LIGHT + DARK, dark='separate', forbid=['color-scheme'])
        res = self.run_cfg(cfg, only=['tokens'])
        self.assertEqual(res.code, 0)
        info = [f for f in res.findings if f['status'] == tc.INFO]
        self.assertEqual(len(info), 1)
        self.assertIn('dark, forbid', info[0]['message'])
        self.assertEqual([f for f in self.run_cfg(cfg).findings if f['status'] == tc.INFO], [])

    def test_only_with_no_names_is_exit_2(self):
        cfg = self.tokens_cfg(LIGHT)
        for only in ([], ['']):
            with self.subTest(only=only):
                self.assertEqual(self.run_cfg(cfg, only=only).code, 2)

    def test_missing_project_dir_is_exit_2(self):
        self.assertEqual(tc.run({'version': 1}, self.tmp / 'brak').code, 2)


class PathTests(Base):
    def cfg_with_file(self, value):
        return {'version': 1, 'tokens': {'files': [value], 'required': None}, 'forbid': ['color-scheme']}

    def test_unsafe_project_paths_are_exit_2(self):
        for value in ('../x.css', 'a/../../x.css', '/etc/passwd', '\\x.css', 'C:x.css', 'C:\\x.css', 'a:b.css',
                      'a\\..\\..\\x.css', '', ' ', 'nul\x00.css'):
            with self.subTest(value=value):
                res = self.run_cfg(self.cfg_with_file(value))
                self.assertEqual(res.code, 2, value)

    def test_safe_relative_paths_pass_the_path_check(self):
        self.write('docs/t.css', LIGHT)
        for value in ('docs/t.css', 'docs\\t.css', './docs/t.css', 'docs//t.css'):
            with self.subTest(value=value):
                res = self.run_cfg({'version': 1, 'tokens': {'files': [value], 'required': None}, 'forbid': ['color-scheme']})
                self.assertEqual(res.code, 0, res.error)

    def test_is_absolute_path_recognizes_all_forms(self):
        for value in ('/x', '\\x', 'C:x', 'c:\\x', '\\\\srv\\share'):
            self.assertTrue(tc.is_absolute_path(value), value)
        for value in ('x', 'a/b', 'a\\b', './x', 'skill-x'):
            self.assertFalse(tc.is_absolute_path(value), value)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'brak dowiązań symbolicznych')
    def test_symlink_leaving_the_project_is_exit_2(self):
        outside = self.tmp / 'outside'
        outside.mkdir()
        (outside / 't.css').write_text(LIGHT, encoding='utf-8')
        try:
            os.symlink(outside, self.project / 'link')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(self.cfg_with_file('link/t.css')).code, 2)
        self.write('real/t.css', LIGHT)
        os.symlink(self.project / 'real', self.project / 'inside')
        self.assertEqual(self.run_cfg(self.cfg_with_file('inside/t.css')).code, 0)

    def skill_copy_cfg(self, source='skill:demo/assets/theme.css'):
        self.write('docs/theme.css', 'a { b: c; }\n')
        return {'version': 1, 'copies': [{'source': source, 'copy': 'docs/theme.css'}]}

    def test_bad_skill_names_and_paths_are_exit_2(self):
        for source in ('skill:Bad/x.css', 'skill:../x.css', 'skill:a/b/../../c', 'skill:demo', 'skill:/x.css',
                       'skill:de mo/x.css', 'skill:demo/../x.css', 'skill:demo/C:x.css', 'skill:-x/a.css'):
            with self.subTest(source=source):
                self.assertEqual(self.run_cfg(self.skill_copy_cfg(source), skill_root=self.tmp).code, 2, source)

    def test_skill_source_without_skill_root_and_without_project_skills_is_exit_2(self):
        self.assertEqual(self.run_cfg(self.skill_copy_cfg()).code, 2)

    def test_skill_root_is_found_in_the_project_in_the_documented_order(self):
        cfg = self.skill_copy_cfg()
        self.write('.github/skills/demo/assets/theme.css', 'ostatni\n')
        self.assertEqual(self.run_cfg(cfg).code, 1)
        self.write('.agents/skills/demo/assets/theme.css', 'a { b: c; }\n')
        self.assertEqual(self.run_cfg(cfg).code, 0)
        self.write('.claude/skills/demo/assets/theme.css', 'pierwszy\n')
        self.assertEqual(self.run_cfg(cfg).code, 1)

    def test_skill_is_chosen_per_skill_not_per_directory(self):
        self.write('.claude/skills/other/x', 'x')
        self.write('.agents/skills/demo/assets/theme.css', 'a { b: c; }\n')
        self.assertEqual(self.run_cfg(self.skill_copy_cfg()).code, 0)

    def test_explicit_skill_root_wins_and_is_not_searched_elsewhere(self):
        root = self.tmp / 'skills'
        self.write('demo/assets/theme.css', 'a { b: c; }\n', base=root)
        self.write('.claude/skills/demo/assets/theme.css', 'inne\n')
        self.assertEqual(self.run_cfg(self.skill_copy_cfg(), skill_root=root).code, 0)
        self.assertEqual(self.run_cfg(self.skill_copy_cfg(), skill_root=self.tmp / 'brak').code, 2)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'brak dowiązań symbolicznych')
    def test_skill_symlinked_to_a_library_is_the_normal_case(self):
        library = self.tmp / 'library' / 'demo'
        self.write('assets/theme.css', 'a { b: c; }\n', base=library)
        skills = self.project / '.claude' / 'skills'
        skills.mkdir(parents=True)
        try:
            os.symlink(library, skills / 'demo')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        res = self.run_cfg(self.skill_copy_cfg())
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_script_location_is_not_used_to_find_skills(self):
        """Skrypt nie szuka skilli obok siebie: bez --skill-root i bez skilli w projekcie wynik to kod 2."""
        self.assertEqual(self.run_cfg(self.skill_copy_cfg('skill:prototype/assets/theme/default.css')).code, 2)

    def test_required_list_can_come_from_a_skill(self):
        root = self.tmp / 'skills'
        self.write('demo/req.txt', '--a\n', base=root)
        self.write('t.css', LIGHT)
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': 'skill:demo/req.txt'}}
        self.assertEqual(self.run_cfg(cfg, skill_root=root).code, 0)
        self.assertEqual(self.run_cfg(cfg).code, 2)


class TokensTests(Base):
    def test_all_required_tokens_defined_passes(self):
        res = self.run_cfg(self.tokens_cfg(LIGHT + DARK))
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_missing_required_token_fails(self):
        res = self.run_cfg(self.tokens_cfg(':root { --a: 1; }\n'))
        self.assertEqual(res.code, 1)
        self.assertIn('--b', res.findings[0]['message'])

    def test_token_only_in_dark_block_fails(self):
        """D-1: token zdefiniowany tylko w bloku ciemnym nie spełnia wymagania."""
        res = self.run_cfg(self.tokens_cfg(':root { --a: 1; }\n[data-theme="dark"] { --b: 2; }\n'))
        self.assertEqual(res.code, 1)
        finding = res.findings[0]
        self.assertIn('tylko w bloku ciemnym', finding['message'])
        control = self.run_cfg(self.tokens_cfg(':root { --a: 1; --b: 2; }\n[data-theme="dark"] { --b: 2; }\n'))
        self.assertEqual(control.code, 0)

    def test_dark_only_token_outside_the_required_list_fails_too(self):
        res = self.run_cfg(self.tokens_cfg(LIGHT + '.dark { --c: 3; }\n'))
        self.assertEqual(res.code, 1)
        self.assertIn('--c', res.findings[0]['message'])

    def test_second_root_and_theme_blocks(self):
        """D-4: drugi `:root`, `@theme`, `@theme inline` i `@layer` są widoczne dla kontroli."""
        for name, css in (('second root', ':root { --a: 1; }\n.x { y: z; }\n:root { --b: 2; }\n'),
                          ('@theme', '@theme { --a: 1; --b: 2; }\n'),
                          ('@theme inline', '@theme inline { --a: 1; --b: 2; }\n'),
                          ('@layer', '@layer base { :root { --a: 1; } @theme { --b: 2; } }\n')):
            with self.subTest(name):
                self.assertEqual(self.run_cfg(self.tokens_cfg(css)).code, 0)

    def test_tokens_in_media_or_nested_rules_do_not_count_as_light(self):
        for css in ('@media (min-width: 1px) { :root { --a: 1; --b: 2; } }\n', ':root { .x { --a: 1; --b: 2; } }\n',
                    '.card-dark { --a: 1; --b: 2; }\n'):
            with self.subTest(css=css):
                self.assertEqual(self.run_cfg(self.tokens_cfg(css)).code, 1)

    def test_no_light_block_at_all_fails(self):
        res = self.run_cfg(self.tokens_cfg('body { margin: 0; }\n'))
        self.assertEqual(res.code, 1)
        self.assertTrue(any('brak jasnego bloku' in f['message'] for f in res.findings))

    def test_required_list_and_theme_accept_bom(self):
        cfg = self.tokens_cfg('\ufeff' + LIGHT, required='\ufeff# lista\n--a\n\n--b\n')
        self.assertEqual(self.run_cfg(cfg).code, 0)

    def test_comments_are_not_tokens(self):
        res = self.run_cfg(self.tokens_cfg('/* :root { --a: 1; --b: 2; } */\n:root { --a: 1; }\n'))
        self.assertEqual(res.code, 1)

    def test_empty_required_list_is_unverified(self):
        res = self.run_cfg(self.tokens_cfg(LIGHT, required='# tylko komentarz\n'))
        self.assertEqual((res.code, self.statuses(res)), (3, [tc.UNVERIFIED]))

    def test_empty_files_list_is_unverified(self):
        self.write('req.txt', '--a\n')
        res = self.run_cfg({'version': 1, 'tokens': {'files': [], 'required': 'req.txt'}})
        self.assertEqual((res.code, self.statuses(res)), (3, [tc.UNVERIFIED]))

    def test_missing_tokens_file_fails_with_its_name(self):
        self.write('req.txt', '--a\n')
        res = self.run_cfg({'version': 1, 'tokens': {'files': ['brak.css'], 'required': 'req.txt'}})
        self.assertEqual(res.code, 1)
        self.assertEqual(res.findings[0]['file'], 'brak.css')

    def test_missing_file_among_several_gives_no_pass(self):
        self.write('a.css', LIGHT)
        self.write('req.txt', '--a\n--b\n')
        for extra in ({}, {'dark': 'separate', 'forbid': ['color-scheme']}):
            cfg = {'version': 1, 'tokens': {'files': ['a.css', 'brak.css'], 'required': 'req.txt'}}
            cfg.update(extra)
            res = self.run_cfg(cfg)
            self.assertEqual(res.code, 1)
            self.assertNotIn(tc.PASS, self.statuses(res))

    def test_finding_has_file_and_line(self):
        res = self.run_cfg(self.tokens_cfg(':root { --a: 1;\n --b: 2; }\n\n.dark {\n  --c: 3;\n}\n'))
        finding = [f for f in res.findings if '--c' in f['message']][0]
        self.assertEqual((finding['file'], finding['line']), ('t.css', 5))
        self.assertIn('[t.css:5]', '\n'.join(res.lines()))

    def test_tokens_from_several_files_are_merged(self):
        self.write('a.css', ':root { --a: 1; }\n')
        self.write('b.css', ':root { --b: 2; }\n')
        self.write('req.txt', '--a\n--b\n')
        res = self.run_cfg({'version': 1, 'tokens': {'files': ['a.css', 'b.css'], 'required': 'req.txt'}})
        self.assertEqual(res.code, 0)

class AssetsTests(Base):
    """Równość zbioru tokenów używanych przez zasoby szablonu z listą wymaganych."""

    def assets_cfg(self, theme, assets, required='--a\n--b\n'):
        cfg = self.tokens_cfg(theme, required)
        cfg['tokens']['assets'] = 'assets'
        for rel, content in assets.items():
            self.write('assets/' + rel, content)
        return cfg

    def test_assets_set_equal_to_the_list_passes(self):
        cfg = self.assets_cfg(LIGHT, {'x.css': 'a { color: var(--a); }', 'i.html': '<p style="margin: var(--b)">'})
        self.assertEqual(self.run_cfg(cfg).code, 0)

    def test_assets_using_an_unlisted_token_fail(self):
        cfg = self.assets_cfg(LIGHT, {'x.css': 'a { color: var(--a) var(--b) var(--c); }'})
        res = self.run_cfg(cfg)
        self.assertEqual(res.code, 1)
        self.assertIn('--c', res.findings[0]['message'])

    def test_listed_token_unused_by_assets_fails(self):
        cfg = self.assets_cfg(LIGHT, {'x.css': 'a { color: var(--a); }'})
        self.assertEqual(self.run_cfg(cfg).code, 1)

    def test_assets_locally_defined_and_engine_tokens_are_ignored(self):
        cfg = self.assets_cfg(LIGHT, {'x.css': '.a { --local: 1; color: var(--local) var(--a) var(--b) var(--proto-x); }'})
        self.assertEqual(self.run_cfg(cfg).code, 0)

    def test_assets_missing_dir_is_exit_2(self):
        cfg = self.tokens_cfg(LIGHT)
        cfg['tokens']['assets'] = 'brak'
        self.assertEqual(self.run_cfg(cfg).code, 2)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'brak dowiązań symbolicznych')
    def test_asset_symlink_leaving_the_project_is_exit_2(self):
        outside = self.tmp / 'outside'
        outside.mkdir()
        (outside / 'x.css').write_text('a { color: var(--a); }\n', encoding='utf-8')
        cfg = self.assets_cfg(LIGHT, {'ok.css': 'a { color: var(--a) var(--b); }'})
        try:
            os.symlink(outside / 'x.css', self.project / 'assets' / 'link.css')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(cfg).code, 2)

    def test_asset_in_a_wrong_encoding_is_exit_2_with_its_name(self):
        cfg = self.assets_cfg(LIGHT, {'ok.css': 'a { color: var(--a) var(--b); }', 'zly.css': b'a { b: \xff\xfe; }'})
        res = self.run_cfg(cfg)
        self.assertEqual(res.code, 2)
        self.assertIn('zly.css', res.error)


class DarkTests(Base):
    def cfg(self, theme, mode):
        return self.tokens_cfg(theme, dark=mode)

    def test_separate_same_blocks_pass(self):
        res = self.run_cfg(self.cfg(LIGHT + DARK + '.dark { --a: 3; --b: 4; }\n', 'separate'), only=['dark'])
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_separate_different_values_fail(self):
        res = self.run_cfg(self.cfg(LIGHT + DARK + '.dark { --a: 9; --b: 4; }\n', 'separate'), only=['dark'])
        self.assertEqual(res.code, 1)
        self.assertIn('różne wartości', res.findings[0]['message'])
        self.assertIn('--a', res.findings[0]['message'])

    def test_separate_token_in_one_block_only_fails(self):
        res = self.run_cfg(self.cfg(LIGHT + DARK + '.dark { --a: 3; }\n', 'separate'), only=['dark'])
        self.assertEqual(res.code, 1)
        self.assertIn('tylko w jednym', res.findings[0]['message'])

    def test_separate_counts_the_media_block_as_a_dark_block(self):
        media = '@media (prefers-color-scheme: dark) { :root:not([data-theme]) { --a: 3; --b: 5; } }\n'
        res = self.run_cfg(self.cfg(LIGHT + DARK + media, 'separate'), only=['dark'])
        self.assertEqual(res.code, 1)
        self.assertIn('--b', res.findings[0]['message'])

    def test_separate_with_whitespace_differences_only_passes(self):
        res = self.run_cfg(self.cfg(LIGHT + '.dark { --a:   3;\n --b: 4 }\n' + DARK, 'separate'), only=['dark'])
        self.assertEqual(res.code, 0)

    def test_separate_without_any_dark_block_is_unverified(self):
        res = self.run_cfg(self.cfg(LIGHT, 'separate'), only=['dark'])
        self.assertEqual((res.code, self.statuses(res, 'dark')), (3, [tc.UNVERIFIED]))
        self.assertIn('czy selektor jest rozpoznawany przez silnik', res.findings[0]['message'])

    def test_separate_with_one_block_passes_with_a_note(self):
        res = self.run_cfg(self.cfg(LIGHT + DARK, 'separate'), only=['dark'])
        self.assertEqual((res.code, self.statuses(res, 'dark')), (0, [tc.PASS]))
        self.assertIn('nie ma czego porównywać', res.findings[0]['message'])

    def test_selector_with_dark_word_unknown_to_the_engine_warns_in_every_mode(self):
        for selector in ('html[data-theme=dark]', 'body.dark', ':root.dark'):
            for mode in ('separate', 'mirror', 'none'):
                with self.subTest(selector=selector, mode=mode):
                    res = self.run_cfg(self.cfg(LIGHT + DARK + selector + ' { --a: 9; }\n', mode), only=['dark'])
                    warns = [f for f in res.findings if f['status'] == tc.WARN]
                    self.assertEqual(len(warns), 1, res.lines())
                    self.assertEqual(warns[0]['line'], 3)
                    self.assertNotEqual(res.code, 2)

    def test_unrecognized_dark_selector_alone_gives_warn_and_unverified(self):
        res = self.run_cfg(self.cfg(LIGHT + 'html[data-theme=dark] { --a: 9; }\n', 'separate'), only=['dark'])
        self.assertEqual(sorted(self.statuses(res, 'dark')), [tc.UNVERIFIED, tc.WARN])
        self.assertEqual(res.code, 3)

    def test_none_with_plain_declarations_in_a_dark_block_warns(self):
        res = self.run_cfg(self.cfg(LIGHT + '.dark { background: #000; }\n', 'none'), only=['dark'])
        self.assertEqual(self.statuses(res, 'dark'), [tc.WARN, tc.PASS])
        self.assertEqual(res.code, 0)
        res = self.run_cfg(self.cfg(LIGHT + '.dark { background: #000; }\n', 'mirror'), only=['dark'])
        self.assertNotIn(tc.WARN, self.statuses(res))

    def test_mirror_identical_block_passes(self):
        res = self.run_cfg(self.cfg(LIGHT + '.dark { --a: 1; --b: 2; }\n', 'mirror'), only=['dark'])
        self.assertEqual(res.code, 0)

    def test_mirror_different_value_missing_and_extra_token_fail(self):
        for css, needle in ((LIGHT + '.dark { --a: 1; --b: 9; }\n', 'inną wartość'),
                            (LIGHT + '.dark { --a: 1; }\n', 'nie definiuje'),
                            (LIGHT + '.dark { --a: 1; --b: 2; --c: 3; }\n', 'którego nie ma w jasnym')):
            with self.subTest(needle):
                res = self.run_cfg(self.cfg(css, 'mirror'), only=['dark'])
                self.assertEqual(res.code, 1)
                self.assertIn(needle, res.findings[0]['message'])

    def test_mirror_without_a_dark_block_fails(self):
        res = self.run_cfg(self.cfg(LIGHT, 'mirror'), only=['dark'])
        self.assertEqual(res.code, 1)
        self.assertIn('brak bloku ciemnego', res.findings[0]['message'])

    def test_none_forbids_any_dark_block(self):
        self.assertEqual(self.run_cfg(self.cfg(LIGHT, 'none'), only=['dark']).code, 0)
        for dark in ('.dark { --a: 1; }\n', DARK,
                     '@media (prefers-color-scheme: dark) { :root { --a: 1; } }\n'):
            with self.subTest(dark=dark):
                res = self.run_cfg(self.cfg(LIGHT + dark, 'none'), only=['dark'])
                self.assertEqual(res.code, 1)

    def test_card_dark_selector_is_not_a_dark_block(self):
        res = self.run_cfg(self.cfg(LIGHT + '.card-dark { --a: 1; }\n', 'none'), only=['dark'])
        self.assertEqual(res.code, 0)

    def test_dark_without_the_key_is_exit_2_with_only(self):
        self.assertEqual(self.run_cfg(self.tokens_cfg(LIGHT), only=['dark']).code, 2)


class ForbidTests(Base):
    def cfg(self, theme, *names):
        return self.tokens_cfg(theme, forbid=list(names))

    def test_clean_files_pass(self):
        res = self.run_cfg(self.cfg(LIGHT, 'color-scheme', 'prefers-color-scheme'), only=['forbid'])
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_color_scheme_fails_with_line(self):
        res = self.run_cfg(self.cfg(':root {\n  color-scheme: light dark;\n  --a: 1; --b: 2; }\n', 'color-scheme'),
                           only=['forbid'])
        self.assertEqual(res.code, 1)
        self.assertEqual((res.findings[0]['file'], res.findings[0]['line']), ('t.css', 2))

    def test_prefers_color_scheme_fails_but_is_not_a_color_scheme_hit(self):
        css = '@media (prefers-color-scheme: dark) { :root { --a: 1; } }\n'
        res = self.run_cfg(self.cfg(css, 'prefers-color-scheme'), only=['forbid'])
        self.assertEqual(res.code, 1)
        res = self.run_cfg(self.cfg(css, 'color-scheme'), only=['forbid'])
        self.assertEqual(res.code, 0)

    def test_constructs_are_case_insensitive(self):
        css = ':root { COLOR-SCHEME: dark; --a: 1; --b: 2; }\n@MEDIA (PREFERS-COLOR-SCHEME: dark) { :root { --a: 1; } }\n'
        res = self.run_cfg(self.cfg(css, 'color-scheme', 'prefers-color-scheme'), only=['forbid'])
        self.assertEqual(self.statuses(res), [tc.FAIL, tc.FAIL])

    def test_constructs_in_comments_are_ignored(self):
        res = self.run_cfg(self.cfg('/* color-scheme: dark; prefers-color-scheme */\n' + LIGHT, 'color-scheme',
                                    'prefers-color-scheme'), only=['forbid'])
        self.assertEqual(res.code, 0)


class CopiesTests(Base):
    def cfg(self, **item):
        entry = {'source': 'src/theme.css', 'copy': 'dst/theme.css'}
        entry.update(item)
        return {'version': 1, 'copies': [entry]}

    def test_identical_copy_passes(self):
        self.write('src/theme.css', 'a { b: c; }\n')
        self.write('dst/theme.css', 'a { b: c; }\n')
        res = self.run_cfg(self.cfg())
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_crlf_and_bom_are_ignored(self):
        self.write('src/theme.css', 'a { b: c; }\nd { e: f; }\n')
        self.write('dst/theme.css', b'\xef\xbb\xbf' + 'a { b: c; }\nd { e: f; }\n'.replace('\n', '\r\n').encode('utf-8'))
        self.assertEqual(self.run_cfg(self.cfg()).code, 0)

    def test_changed_copy_fails_and_names_the_file(self):
        self.write('src/theme.css', 'a { b: c; }\n')
        self.write('dst/theme.css', 'a { b: c; }\n.x { color: red; }\n')
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertEqual(res.findings[0]['file'], 'dst/theme.css')

    def test_missing_copy_fails(self):
        self.write('src/theme.css', 'a\n')
        res = self.run_cfg(self.cfg())
        self.assertEqual((res.code, res.findings[0]['status']), (1, tc.FAIL))
        self.assertIn('brak kopii', res.findings[0]['message'])

    def test_source_and_copy_being_the_same_file_is_exit_2(self):
        self.write('src/theme.css', 'a\n')
        self.assertEqual(self.run_cfg(self.cfg(copy='src/theme.css')).code, 2)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'brak dowiązań symbolicznych')
    def test_copy_that_is_a_link_to_the_source_is_exit_2(self):
        self.write('src/theme.css', 'a\n')
        (self.project / 'dst').mkdir()
        try:
            os.symlink(self.project / 'src' / 'theme.css', self.project / 'dst' / 'theme.css')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_missing_source_is_exit_2_not_fail(self):
        self.write('dst/theme.css', 'a\n')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_bytes_mode_does_not_normalize(self):
        self.write('src/theme.css', 'a\nb\n')
        self.write('dst/theme.css', 'a\r\nb\r\n')
        self.assertEqual(self.run_cfg(self.cfg(mode='bytes')).code, 1)
        self.assertEqual(self.run_cfg(self.cfg(mode='text')).code, 0)

    def test_binary_source_is_compared_byte_for_byte(self):
        self.write('src/theme.css', b'\xff\xfe\x00\x01')
        self.write('dst/theme.css', b'\xff\xfe\x00\x01')
        self.assertEqual(self.run_cfg(self.cfg()).code, 0)
        self.write('dst/theme.css', b'\xff\xfe\x00\x02')
        self.assertEqual(self.run_cfg(self.cfg()).code, 1)

    def test_single_app_file_copy(self):
        """Pojedynczy plik tokenów aplikacji kopiowany z warstwy (tryb aplikacji kopii warstwowej)."""
        self.write('src/app-tokens.css', ':root { --x: 1; }\n')
        self.write('docs/app-tokens.css', ':root { --x: 2; }\n')
        res = self.run_cfg({'version': 1, 'copies': [{'source': 'src/app-tokens.css', 'copy': 'docs/app-tokens.css'}]})
        self.assertEqual(res.code, 1)

    def test_several_copies_are_reported_independently(self):
        self.write('src/a.css', 'a\n')
        self.write('src/b.css', 'b\n')
        self.write('dst/a.css', 'a\n')
        self.write('dst/b.css', 'zmieniony\n')
        res = self.run_cfg({'version': 1, 'copies': [{'source': 'src/a.css', 'copy': 'dst/a.css'},
                                                      {'source': 'src/b.css', 'copy': 'dst/b.css'}]})
        self.assertEqual(self.statuses(res), [tc.PASS, tc.FAIL])


class CopyDirsTests(Base):
    FONT_A = b'wOF2\x00\x01\xff\xfe\r\n\x80'
    FONT_B = b'wOF2\x02\x03\xfd\xfc\n\x81'

    def setUp(self):
        super().setUp()
        self.write('src/fonts/a.woff2', self.FONT_A)
        self.write('src/fonts/b.woff2', self.FONT_B)
        self.write('src/fonts/LICENSE', 'SIL OPEN FONT LICENSE\r\nlinia\r\n')
        self.write('src/fonts/SOURCE.txt', 'pochodzenie plików, nie kopiowane\n')
        for name in ('a.woff2', 'b.woff2', 'LICENSE'):
            self.write('dst/fonts/' + name, (self.tmp_source(name)))

    def tmp_source(self, name):
        return descend(self.project, 'src/fonts/' + name).read_bytes()

    def cfg(self, include=('*.woff2', 'LICENSE')):
        return {'version': 1, 'copyDirs': [{'source': 'src/fonts', 'copy': 'dst/fonts', 'include': list(include)}]}

    def test_complete_copy_passes(self):
        res = self.run_cfg(self.cfg())
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))
        self.assertIn('3 plików', res.findings[0]['message'])

    def test_source_file_outside_include_is_not_required(self):
        self.assertFalse((self.project / 'dst' / 'fonts' / 'SOURCE.txt').exists())
        self.assertEqual(self.run_cfg(self.cfg()).code, 0)
        self.assertEqual(self.run_cfg(self.cfg(include=('*',))).code, 1)

    def test_missing_font_file_fails(self):
        (self.project / 'dst' / 'fonts' / 'a.woff2').unlink()
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertEqual(res.findings[0]['file'], 'dst/fonts/a.woff2')

    def test_changed_font_bytes_fail(self):
        self.write('dst/fonts/b.woff2', self.FONT_B + b'x')
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertIn('różni się', res.findings[0]['message'])

    def test_font_with_crlf_like_bytes_is_not_normalized(self):
        self.write('dst/fonts/a.woff2', self.FONT_A.replace(b'\r\n', b'\n'))
        self.assertEqual(self.run_cfg(self.cfg()).code, 1)

    def test_missing_license_fails(self):
        (self.project / 'dst' / 'fonts' / 'LICENSE').unlink()
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertEqual(res.findings[0]['file'], 'dst/fonts/LICENSE')

    def test_license_is_compared_byte_for_byte(self):
        self.write('dst/fonts/LICENSE', 'SIL OPEN FONT LICENSE\nlinia\n')
        self.assertEqual(self.run_cfg(self.cfg()).code, 1)

    def test_css_files_in_the_directory_are_normalized(self):
        self.write('src/fonts/f.css', 'a { b: c; }\n')
        self.write('dst/fonts/f.css', '\ufeffa { b: c; }\r\n')
        self.assertEqual(self.run_cfg(self.cfg(include=('*.css',))).code, 0)

    def test_include_matching_nothing_is_unverified(self):
        res = self.run_cfg(self.cfg(include=('*.zzz',)))
        self.assertEqual((res.code, self.statuses(res)), (3, [tc.UNVERIFIED]))

    def test_missing_source_dir_is_exit_2(self):
        cfg = {'version': 1, 'copyDirs': [{'source': 'src/brak', 'copy': 'dst/fonts', 'include': ['*']}]}
        self.assertEqual(self.run_cfg(cfg).code, 2)

    @unittest.skipUnless(hasattr(os, 'symlink'), 'brak dowiązań symbolicznych')
    def test_copy_child_symlink_leaving_the_project_is_exit_2(self):
        outside = self.tmp / 'outside'
        outside.mkdir()
        (outside / 'a.woff2').write_bytes(self.FONT_A)
        (self.project / 'dst' / 'fonts' / 'a.woff2').unlink()
        try:
            os.symlink(outside / 'a.woff2', self.project / 'dst' / 'fonts' / 'a.woff2')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_source_and_copy_dirs_being_the_same_directory_is_exit_2(self):
        cfg = {'version': 1, 'copyDirs': [{'source': 'src/fonts', 'copy': 'src/fonts', 'include': ['*.woff2']}]}
        self.assertEqual(self.run_cfg(cfg).code, 2)

    def test_include_pattern_with_a_path(self):
        self.write('src/fonts/sub/x.woff2', self.FONT_A)
        self.write('dst/fonts/sub/x.woff2', self.FONT_A)
        self.assertEqual(self.run_cfg(self.cfg(include=('sub/*.woff2',))).code, 0)
        (self.project / 'dst' / 'fonts' / 'sub' / 'x.woff2').unlink()
        self.assertEqual(self.run_cfg(self.cfg(include=('sub/*.woff2',))).code, 1)


class ResultTests(Base):
    def test_codes_follow_the_worst_status(self):
        res = tc.Result()
        self.assertEqual(res.code, 0)
        res.add(tc.WARN, 'x', 'm')
        self.assertEqual(res.code, 0)
        res.add(tc.UNVERIFIED, 'x', 'm')
        self.assertEqual(res.code, 3)
        res.add(tc.FAIL, 'x', 'm')
        self.assertEqual(res.code, 1)
        res.error = 'e'
        self.assertEqual(res.code, 2)

    def test_lines_and_json_carry_the_same_findings(self):
        res = self.run_cfg(self.tokens_cfg(':root { --a: 1; }\n'))
        lines = res.lines()
        self.assertTrue(all(LINE_RE.match(line) or line.startswith('theme_check: ') for line in lines), lines)
        data = res.to_dict()
        self.assertEqual(len(data['findings']), len(lines) - 1)
        self.assertEqual(data['code'], 1)

    def test_run_accepts_a_config_built_in_memory_and_str_paths(self):
        self.write('a.css', 'x\n')
        self.write('b.css', 'x\n')
        res = tc.run({'version': 1, 'copies': [{'source': 'a.css', 'copy': 'b.css'}]}, str(self.project), None)
        self.assertEqual(res.code, 0)


class SelftestTests(unittest.TestCase):
    def test_every_case_passes_and_covers_each_implemented_check(self):
        problems, count = tc.selftest()
        self.assertEqual(problems, [])
        checks = {c['check'] for c in tc.selftest_cases() if c['check']}
        self.assertTrue({'tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'config'} <= checks)
        self.assertGreaterEqual(count, 25)

    def test_every_check_has_a_failing_case(self):
        failing = {c['check'] for c in tc.selftest_cases() if c['status'] in (tc.FAIL, tc.UNVERIFIED)}
        self.assertTrue({'tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'config'} <= failing)

    def test_selftest_detects_a_broken_check(self):
        with mock.patch.object(tc, 'check_forbid', lambda cfg, files, res: res.add(tc.PASS, 'forbid', 'zawsze PASS')):
            problems, _ = tc.selftest()
        self.assertTrue(any('forbid' in p for p in problems))


class SafetyNetTests(Base):
    def test_tokens_file_in_a_wrong_encoding_is_exit_2_with_its_name(self):
        cfg = self.tokens_cfg(LIGHT)
        self.write('t.css', b':root { --a: \xff\xfe; }')
        res = self.run_cfg(cfg)
        self.assertEqual(res.code, 2)
        self.assertIn('t.css', res.error)

    def test_oversized_css_file_is_exit_2(self):
        cfg = self.tokens_cfg(LIGHT)
        self.write('t.css', ':root { --a: 1; --b: 2; }\n/*' + ' ' * (2 * 1024 * 1024) + '*/\n')
        res = self.run_cfg(cfg)
        self.assertEqual(res.code, 2)
        self.assertIn('2 MiB', res.error)

    def test_deep_nesting_is_exit_2_and_depth_200_is_accepted(self):
        deep = '.a {' * 201 + ' --x: 1; ' + '}' * 201
        res = self.run_cfg(self.tokens_cfg(deep))
        self.assertEqual(res.code, 2)
        ok = '.a {' * 150 + ' --x: 1; ' + '}' * 150 + LIGHT
        self.assertEqual(self.run_cfg(self.tokens_cfg(ok)).code, 0)

    def test_unexpected_exception_is_exit_2_without_content(self):
        cfg = self.tokens_cfg(LIGHT)
        with mock.patch.object(tc, '_run', side_effect=PermissionError('SENTINEL_BODY')):
            res = self.run_cfg(cfg)
        self.assertEqual(res.code, 2)
        self.assertIn('błąd wewnętrzny', res.error)
        self.assertNotIn('SENTINEL_BODY', res.error)

    def test_main_turns_any_exception_into_exit_2(self):
        path = self.tmp / 'c.json'
        path.write_text(json.dumps({'version': 1}), encoding='utf-8')
        err = io.StringIO()
        with mock.patch.object(tc, 'run', side_effect=RuntimeError('SENTINEL_BODY')), mock.patch.object(sys, 'stderr', err):
            rc = tc.main(['--config', str(path), '--project', str(self.project)])
        self.assertEqual(rc, 2)
        self.assertIn('błąd wewnętrzny', err.getvalue())
        self.assertNotIn('SENTINEL_BODY', err.getvalue())


class CliProcessTests(Base):
    def cli(self, *args, stdin=None):
        proc = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'theme_check.py')] + [str(a) for a in args],
                              capture_output=True, text=True, encoding='utf-8', input=stdin, timeout=120)
        return proc

    def config_file(self, config):
        path = self.tmp / 'theme-check.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        return path

    def test_exit_codes_0_1_2_3(self):
        cfg = self.tokens_cfg(LIGHT)
        self.assertEqual(self.cli('--config', self.config_file(cfg), '--project', self.project).returncode, 0)
        cfg = self.tokens_cfg(':root { --a: 1; }\n')
        self.assertEqual(self.cli('--config', self.config_file(cfg), '--project', self.project).returncode, 1)
        self.assertEqual(self.cli('--config', self.config_file({'version': 1, 'x': 1}), '--project', self.project).returncode, 2)
        self.assertEqual(self.cli('--config', self.config_file({'version': 1}), '--project', self.project).returncode, 3)

    def test_output_format_and_final_line(self):
        cfg = self.tokens_cfg(':root { --a: 1; }\n')
        proc = self.cli('--config', self.config_file(cfg), '--project', self.project)
        lines = proc.stdout.splitlines()
        self.assertTrue(LINE_RE.match(lines[0]), lines[0])
        self.assertTrue(lines[-1].startswith('theme_check: FAIL (kod 1)'), lines[-1])
        self.assertEqual(proc.stderr, '')

    def test_json_matches_the_lines(self):
        cfg = self.tokens_cfg(':root { --a: 1; }\n')
        path = self.config_file(cfg)
        text = self.cli('--config', path, '--project', self.project)
        data = json.loads(self.cli('--config', path, '--project', self.project, '--json').stdout)
        self.assertEqual(data['code'], text.returncode)
        rebuilt = []
        for f in data['findings']:
            where = ' [%s%s]' % (f['file'], ':%d' % f['line'] if f['line'] else '') if f['file'] else ''
            rebuilt.append('%s %s: %s%s' % (f['status'], f['check'], f['message'], where))
        self.assertEqual(rebuilt, text.stdout.splitlines()[:-1])

    def test_project_defaults_to_the_config_directory(self):
        self.write('t.css', LIGHT)
        self.write('req.txt', '--a\n')
        path = self.project / 'theme-check.json'
        path.write_text(json.dumps({'version': 1, 'tokens': {'files': ['t.css'], 'required': 'req.txt'}}), encoding='utf-8')
        self.assertEqual(self.cli('--config', path).returncode, 0)

    def test_config_from_stdin_needs_a_project(self):
        cfg = json.dumps(self.tokens_cfg(LIGHT))
        self.assertEqual(self.cli('--config', '-', '--project', self.project, stdin=cfg).returncode, 0)
        proc = self.cli('--config', '-', stdin=cfg)
        self.assertEqual(proc.returncode, 2)

    def test_only_option(self):
        cfg = self.tokens_cfg(LIGHT, dark='mirror')
        path = self.config_file(cfg)
        self.assertEqual(self.cli('--config', path, '--project', self.project, '--only', 'tokens').returncode, 0)
        self.assertEqual(self.cli('--config', path, '--project', self.project, '--only', 'dark').returncode, 1)
        self.assertEqual(self.cli('--config', path, '--project', self.project, '--only', 'forbid').returncode, 2)

    def test_usage_errors(self):
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(self.cli('--config', self.tmp / 'brak.json').returncode, 2)
        self.assertEqual(self.cli('--bogus').returncode, 2)

    def test_invalid_json_and_unknown_key_errors_do_not_quote_the_file(self):
        sentinel = 'SENTINEL_BODY_77c1'
        broken = self.tmp / 'broken.json'
        broken.write_text('{"version": 1, "%s": [1, 2,}' % sentinel, encoding='utf-8')
        proc = self.cli('--config', broken, '--project', self.project)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(sentinel, proc.stdout + proc.stderr)
        unknown = self.config_file({'version': 1, sentinel: sentinel})
        proc = self.cli('--config', unknown, '--project', self.project, '--json')
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(sentinel, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['code'], 2)

    def test_only_with_no_names_is_exit_2(self):
        path = self.config_file(self.tokens_cfg(LIGHT))
        for value in ('', ',', ' , '):
            with self.subTest(value=value):
                self.assertEqual(self.cli('--config', path, '--project', self.project, '--only', value).returncode, 2)

    def test_only_prints_an_info_line_with_skipped_checks(self):
        path = self.config_file(self.tokens_cfg(LIGHT, dark='mirror'))
        proc = self.cli('--config', path, '--project', self.project, '--only', 'tokens')
        self.assertIn('INFO config: pominięto kontrole z konfiguracji (--only): dark', proc.stdout)
        self.assertEqual(proc.returncode, 0)

    def test_deeply_nested_json_is_exit_2_without_a_traceback(self):
        deep = self.tmp / 'deep.json'
        deep.write_text('[' * 200000, encoding='utf-8')
        proc = self.cli('--config', deep, '--project', self.project)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn('Traceback', proc.stderr)

    def test_output_survives_a_narrow_stream_encoding(self):
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'forbid': ['color-scheme']}
        self.write('t.css', LIGHT)
        path = self.config_file(cfg)
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'theme_check.py'), '--config', str(path), '--project',
                               str(self.project)], capture_output=True, env=env, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('wyłączone', proc.stdout.decode('utf-8'))

    def test_selftest_exits_0(self):
        proc = self.cli('--selftest')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('selftest OK', proc.stdout)

    def test_isolated_mode_imports_the_parser(self):
        """`-I` nie dodaje katalogu skryptu do sys.path; skrypt robi to jawnie (uruchomienie z obcego cwd)."""
        proc = subprocess.run([sys.executable, '-I', str(SCRIPTS / 'theme_check.py'), '--selftest'], cwd=str(self.tmp),
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_skill_root_option_and_skill_source(self):
        root = self.tmp / 'skills'
        self.write('demo/a.css', 'x\n', base=root)
        self.write('b.css', 'x\n')
        path = self.config_file({'version': 1, 'copies': [{'source': 'skill:demo/a.css', 'copy': 'b.css'}]})
        self.assertEqual(self.cli('--config', path, '--project', self.project, '--skill-root', root).returncode, 0)
        self.assertEqual(self.cli('--config', path, '--project', self.project).returncode, 2)


class NoProjectNamesTests(unittest.TestCase):
    """K8: biblioteka prototypów nie zawiera nazw warstwy ani projektu klienta; literały składane w czasie wykonania."""

    def test_prototype_skill_and_fixtures_have_no_layer_or_project_names(self):
        words = [''.join(('p', 'c', 'g')), ''.join(('pc', 'gacad', 'emia')), ''.join(('v', 'e', '-theme')),
                 ''.join(('check_', 'prototype_theme'))]
        roots = [REPO / 'library' / 'skills' / 'prototype', REPO / 'tests' / 'fixtures' / 'prototype' / 'css-tokens']
        hits, scanned = [], 0
        for root in roots:
            for path in sorted(root.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                try:
                    text = path.read_text(encoding='utf-8').lower()
                except UnicodeDecodeError:
                    continue
                scanned += 1
                hits += [(path.name, w) for w in words if w in text]
        self.assertGreater(scanned, 20)
        self.assertEqual(hits, [])


if __name__ == '__main__':
    unittest.main()
