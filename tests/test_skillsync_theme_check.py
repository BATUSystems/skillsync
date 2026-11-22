"""Kontrola motywu prototypów (`library/skills/prototype/scripts/theme_check.py`), kroki T4-1…T4-5 planu CORE-1140-T4.

Testy czytają `REPO/library` (nie zależą od SKILLSYNC_TEST_LIBRARY); fikstury są syntetyczne, w katalogach tymczasowych.

Spis parytetu przypadków źródłowych (załącznik K9; stan po T4-5, luki oznaczone jawnie jako GAP z powodem):

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
| pusty zbiór znanych tokenów                                   | strażnik rdzenia        | PrototypeKnownTokensTests.test_empty_known_token_set_is_a_fail_with_a_message |
| `font-family: inherit` i `var()` (pozytyw), literał (negatyw)  | skrypt projektowy       | PrototypeRulesTests.test_font_family_inherit_and_var_pass_but_literal_fails |
| `font-family: var(--f), serif`: różnica względem źródła (zaostrzenie), nie parytet | rdzeń | PrototypeRulesTests.test_font_family_inherit_and_var_pass_but_literal_fails |
| pusty `project.css`                                           | skrypt projektowy       | PrototypeRulesTests.test_empty_project_css_passes |
| zrównoważone `var(--x, fb)`, literał w fallbacku, fallback zagnieżdżony | skrypt projektowy | PrototypeRulesTests.test_balanced_var_fallback_is_scanned_like_a_value, test_fallback_with_calc_and_commas_inside_balanced_parentheses |
| maskowanie `url()` i napisów                                  | skrypt projektowy       | PrototypeRulesTests.test_url_and_strings_are_masked, test_font_family_inside_a_string_or_url_is_masked |
| długie formy promienia, dozwolone `0`/`50%` (lista `allow`)   | skrypt projektowy       | PrototypeRulesTests.test_radius_allow_list_and_long_forms, test_radius_true_allows_nothing_and_false_or_missing_is_off |
| numery linii stylu (CSS, `<style>`, atrybut `style`, CRLF)    | skrypt projektowy       | PrototypeRulesTests.test_line_numbers_in_css_html_style_blocks_and_attributes, test_crlf_files_keep_line_numbers |
| negatywy K5: `style=color:#ff0000`, `<style>` z `color:#f00`, `background:#fff`, `rgba()`, `color: red`, `var(--nieznany)` | skrypt projektowy | PrototypeRulesTests.test_color_literals_fail, test_style_attributes_and_style_blocks_in_html, test_unknown_var_is_a_fail_even_with_no_rules |
| negatywy K5: waga, interlinia (`700`, `bold`, `1.5`, `normal`), `font-size: 14px`, `z-index` | skrypt projektowy | PrototypeRulesTests.test_weight_and_line_height_accept_only_var, test_size_rule_forbids_literal_font_sizes_only_when_on, test_z_index_modes |
| pozytyw K5: `var()` istniejących tokenów (kolor, waga, interlinia) | skrypt projektowy | PrototypeRulesTests.test_clean_prototype_passes, test_weight_and_line_height_accept_only_var |
| pozytyw: prototyp bez ręcznego stylowania                     | skrypt projektowy       | PrototypeRulesTests.test_clean_prototype_passes |
| brak `project.css` / `index.html` = FAIL (`onMissingFile`)    | skrypt projektowy       | PrototypeDirsTests.test_missing_file_fails_by_default_and_skip_adds_a_note |
| tokeny prototypu z `tokens.css` i `theme.css` każdego katalogu | skrypt projektowy      | PrototypeKnownTokensTests.test_known_tokens_come_from_each_prototype_directory, test_known_tokens_from_several_files_and_dark_blocks |
| numery linii w findingach tokenów i forbid                    | strażnik / skrypt proj. | TokensTests.test_finding_has_file_and_line, ForbidTests.test_color_scheme_fails_with_line |
| selftest skryptu projektowego, część ogólna: dark mirror, forbid                | skrypt projektowy       | DarkTests (mirror), ForbidTests                |
| kontrast: pozytyw stanu faktycznego, negatyw poniżej minimum  | skrypt projektowy       | ContrastTests.test_pass_and_fail_by_the_minimum |
| kontrast: odstępstwo istniejące = WARN, nieistniejące = FAIL  | skrypt projektowy       | ContrastInputTests.test_deviation_turns_fail_into_warn, test_unknown_deviation_id_still_fails |
| selftest skryptu projektowego, część ogólna: kontrast i reguły stylu | skrypt projektowy | `theme_check.py --selftest` (contrast_cases, prototype_cases), SelftestTests |
| pozytyw: plik tokenów z CRLF; negatyw: pokrycie `required` + `--nowy` | skrypt projektowy | ParityCoreTests |
| D-1 token tylko ciemny uznany za zdefiniowany                 | defekt D-1              | TokensTests.test_token_only_in_dark_block_fails |
| D-2 brak listy: kod 2, nie pominięcie                         | defekt D-2              | ConfigTests.test_missing_required_list_is_exit_2 |
| D-4 drugi `:root`, `@theme`, `@theme inline`                  | defekt D-4              | TokensTests.test_second_root_and_theme_blocks   |
| D-5 formaty kolorów, UNVERIFIED, brak tracebacku              | defekt D-5              | ContrastTests.test_d5_color_formats_give_a_verdict_never_an_exception, ColorTests |
| kolory spoza sRGB (`oklch`, `lab`) w motywie domyślnym rdzenia | decyzja rewizji 3      | ColorTests, ContrastTests.test_default_theme_of_the_prototype_library_gets_a_verdict |
| selftest: weight, lineHeight, size, promień poza `allow`, oklch FAIL i `outOfGamut` w JSON | skrypt projektowy | `theme_check.py --selftest`, SelftestTests |
| GAP (specyfika projektu, zostaje u niego): równość z generatorem i mapa tokenów (`check_generated`, wartość różna od mapy, dopisany `:root{--primary:red}`) | skrypt projektowy | poza rdzeniem (R1) |
| GAP (specyfika projektu): arkusz motywu `prototype.css` (F3, ok. 25 negatywów: `@import`, selektory spoza `:root .screen`, blok fontu) | skrypt projektowy | poza rdzeniem (R1) |
| GAP (opakowanie warstwy, T4b): tryb aplikacji i manifest bez `root` jako budowa konfiguracji | testy warstwy | test opakowania w repo warstwy (CopiesTests.test_single_app_file_copy pokrywa rdzeń) |
| równość zbioru tokenów zasobów z listą                        | strażnik rdzenia        | AssetsTests                                    |
| zasób lub plik tokenów w złym kodowaniu, rozmiar, zagnieżdżenie | przegląd T4-1/T4-2    | SafetyNetTests                                 |
| D-6 deklaracja w atrybucie `style`                            | defekt D-6              | test_skillsync_css_tokens.UsedTokensTests      |

Różnice względem źródła projektowego (trafiają do dokumentacji migracji; to nie są parytety):

- `font-family` z `var()` i literałem (`var(--f), serif`): źródło PASS (wystarczyło jedno `var(`), rdzeń FAIL; sama lista `var()`
  rozdzielona przecinkami jest dozwolona.
- `size`: obejmuje także `%` oraz jednostki vw, vh, vmin, vmax, ch, ex, pc, cm, mm, in, dotyczy `font` i `font-size` (źródło: px,
  rem, em, pt, także dla `font-weight`; w rdzeniu `font-weight` nie podlega `size`).
- `zIndex`: tryby `tokens` (tylko `var(--z-index-*)`), `forbid`, `allow` zamiast zakazu każdego `z-index`.
- `radius`: lista `allow` z konfiguracji (źródło: stała `0`, `50%`, `999px`, `inherit`); `radius: true` = brak dozwolonych wartości;
  objęte także prefiksy vendor (`-webkit-border-radius`, `-moz-border-radius`).
- Reguły są opcjonalne (brak klucza = wyłączona); `var(--x)` spoza znanych tokenów to FAIL zawsze.
- Kontrast: wartość tokenu to ostatnia definicja w kolejności źródła; formaty `oklch`, `oklab`, `lab`, `lch` są konwertowane
  (źródło: tylko `#rrggbb`, reszta traceback); plik odstępstw czytany per linia do 500 znaków (dłuższe pomijane z WARN).
"""
import io
import json
import math
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
import theme_colors as tcol  # noqa: E402

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

    def test_contrast_and_prototype_are_known_checks_and_empty_input_is_unverified(self):
        for key, value in (('contrast', {}), ('prototype', {'dirs': ['x/*']})):
            with self.subTest(key=key):
                res = self.run_cfg({'version': 1, key: value})
                self.assertEqual((res.code, res.error), (3, None))
                self.assertEqual(self.statuses(res, key), [tc.UNVERIFIED])
        self.assertEqual(self.run_cfg({'version': 1, 'contrast': {}}, only=['contrast']).code, 3)
        self.assertEqual(self.run_cfg({'version': 1, 'contrast': {}}, only=['prototype']).code, 2)

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


def _ottosson_oklab_to_rgb255(lightness, a, b):
    """Niezależne odniesienie: bezpośrednia macierz LMS -> liniowy sRGB Ottossona (inne stałe niż w CSS Color 4)."""
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)
    return _encode255(lin)


def _lindbloom_lab_to_rgb255(lightness, a, b):
    """Niezależne odniesienie: Lab D50 -> XYZ D50 -> liniowy sRGB macierzą Lindbloom (adaptowaną do D50 wprost)."""
    fy = (lightness + 16) / 116
    fx, fz = a / 500 + fy, fy - b / 200
    eps, kappa = 0.008856, 903.3
    x = fx ** 3 if fx ** 3 > eps else (116 * fx - 16) / kappa
    y = fy ** 3 if lightness > kappa * eps else lightness / kappa
    z = fz ** 3 if fz ** 3 > eps else (116 * fz - 16) / kappa
    x, y, z = x * 0.96422, y * 1.0, z * 0.82521
    lin = (3.1338561 * x - 1.6168667 * y - 0.4906146 * z,
           -0.9787684 * x + 1.9161415 * y + 0.0334540 * z,
           0.0719453 * x - 0.2289914 * y + 1.4052427 * z)
    return _encode255(lin)


def _encode255(lin):
    out = []
    for v in lin:
        v = min(1.0, max(0.0, v))
        out.append(round(255 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055)))
    return tuple(out)


def _ref_ratio(fg255, bg255):
    def lum(c):
        def f(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (f(v) for v in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(fg255), lum(bg255)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class ColorTests(unittest.TestCase):
    """Konwersje kolorów do sRGB: wartości referencyjne CSS Color 4 (tolerancja ±1 na kanał) i drugie obliczenie."""

    def assertRGB(self, value, expected, tol=1):
        got = tcol.parse_color(value).as_255()
        for g, e in zip(got, expected):
            self.assertLessEqual(abs(g - e), tol, '%s: %s zamiast %s' % (value, got, expected))

    def test_oklch_reference_values(self):
        self.assertRGB('oklch(1 0 0)', (255, 255, 255), 0)
        self.assertRGB('oklch(0 0 0)', (0, 0, 0), 0)
        self.assertRGB('oklch(0.62796 0.25768 29.2339)', (255, 0, 0))
        self.assertRGB('oklch(0.86644 0.29483 142.495)', (0, 255, 0))
        self.assertRGB('oklch(0.45201 0.31321 264.052)', (0, 0, 255))

    def test_lab_and_lch_reference_values(self):
        self.assertRGB('lab(100 0 0)', (255, 255, 255), 0)
        self.assertRGB('lab(0 0 0)', (0, 0, 0), 0)
        self.assertRGB('lab(54.2905 80.8049 69.8910)', (255, 0, 0))
        self.assertRGB('lab(87.8185 -79.2711 80.9946)', (0, 255, 0))
        self.assertRGB('lab(29.5683 68.2986 -112.0294)', (0, 0, 255))
        self.assertRGB('lch(54.2905 106.8391 40.8526)', (255, 0, 0))

    def test_oklab_and_oklch_agree_with_an_independent_matrix(self):
        for lightness, a, b in ((0.145, 0, 0), (0.5, 0.05, -0.1), (0.7, -0.1, 0.1), (0.93, 0.02, 0.08), (0.3, 0.1, 0.05)):
            with self.subTest(lab=(lightness, a, b)):
                self.assertRGB('oklab(%s %s %s)' % (lightness, a, b), _ottosson_oklab_to_rgb255(lightness, a, b))
        for lightness, chroma, hue in ((0.53, 0.22, 27), (0.52, 0.2, 262), (0.965, 0.02, 250), (0.42, 0.1, 65)):
            a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
            with self.subTest(lch=(lightness, chroma, hue)):
                self.assertRGB('oklch(%s %s %s)' % (lightness, chroma, hue), _ottosson_oklab_to_rgb255(lightness, a, b))

    def test_lab_and_lch_agree_with_an_independent_matrix(self):
        for lightness, a, b in ((50, 0, 0), (70, 30, -40), (40, -20, 20), (90, 5, 5), (20, 10, -30)):
            with self.subTest(lab=(lightness, a, b)):
                self.assertRGB('lab(%s %s %s)' % (lightness, a, b), _lindbloom_lab_to_rgb255(lightness, a, b))
        chroma, hue = 50.0, 120.0
        a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
        self.assertRGB('lch(60 50 120deg)', _lindbloom_lab_to_rgb255(60, a, b))

    def test_hue_units_and_alpha_in_oklch(self):
        base = tcol.parse_color('oklch(0.6 0.1 90)').as_255()
        for hue in ('0.25turn', '100grad', '90deg', '1.5707963267948966rad'):
            self.assertEqual(tcol.parse_color('oklch(0.6 0.1 %s)' % hue).as_255(), base, hue)
        self.assertAlmostEqual(tcol.parse_color('oklch(0.145 0 0 / 0.5)').a, 0.5)
        self.assertAlmostEqual(tcol.parse_color('lab(50 0 0 / 25%)').a, 0.25)

    def test_out_of_gamut_is_clipped_and_flagged(self):
        flagged = tcol.parse_color('oklch(0.86644 0.29483 142.495)')
        self.assertTrue(flagged.out_of_gamut)
        for channel in flagged.rgb():
            self.assertTrue(0.0 <= channel <= 1.0)
        self.assertTrue(tcol.parse_color('oklch(0.9 0.4 150)').out_of_gamut)
        self.assertTrue(tcol.parse_color('lab(50 120 -120)').out_of_gamut)
        for inside in ('oklch(1 0 0)', 'oklch(0.145 0 0)', 'lab(50 10 10)', '#123456', 'rgb(0 0 0)', 'hsl(10 10% 10%)'):
            self.assertFalse(tcol.parse_color(inside).out_of_gamut, inside)

    def test_hex_forms(self):
        self.assertEqual(tcol.parse_color('#fff').as_255(), (255, 255, 255))
        self.assertEqual(tcol.parse_color('#1a2').as_255(), (17, 170, 34))
        self.assertEqual(tcol.parse_color('#1a2b').a, 0xbb / 255)
        self.assertEqual(tcol.parse_color('#11223344').as_255(), (17, 34, 51))
        self.assertAlmostEqual(tcol.parse_color('#11223380').a, 128 / 255)
        self.assertEqual(tcol.parse_color(' #FFFFFF ').as_255(), (255, 255, 255))

    def test_rgb_forms(self):
        for value in ('rgb(10, 20, 30)', 'rgb(10 20 30)', 'rgba(10,20,30,1)', 'rgb(10 20 30 / 100%)',
                      'RGB(10 20 30)', 'rgb(3.92157% 7.84314% 11.7647%)'):
            with self.subTest(value=value):
                self.assertEqual(tcol.parse_color(value).as_255(), (10, 20, 30))
        self.assertAlmostEqual(tcol.parse_color('rgba(0, 0, 0, .25)').a, 0.25)
        self.assertAlmostEqual(tcol.parse_color('rgb(0 0 0 / 40%)').a, 0.4)
        self.assertEqual(tcol.parse_color('rgb(300 -5 0)').as_255(), (255, 0, 0))

    def test_hsl_forms(self):
        for value, expected in (('hsl(0, 100%, 50%)', (255, 0, 0)), ('hsl(120 100% 50%)', (0, 255, 0)),
                                ('hsla(240, 100%, 50%, 0.5)', (0, 0, 255)), ('hsl(0.5turn 100% 50%)', (0, 255, 255)),
                                ('hsl(0 0% 100%)', (255, 255, 255)), ('hsl(480 100% 50%)', (0, 255, 0))):
            with self.subTest(value=value):
                self.assertEqual(tcol.parse_color(value).as_255(), expected)
        self.assertAlmostEqual(tcol.parse_color('hsla(240, 100%, 50%, 0.5)').a, 0.5)

    def test_keywords(self):
        self.assertEqual(tcol.parse_color('white').as_255(), (255, 255, 255))
        self.assertEqual(tcol.parse_color('BLACK').as_255(), (0, 0, 0))
        self.assertEqual(tcol.parse_color('transparent').a, 0.0)

    def test_unsupported_formats_raise_color_format(self):
        for value in ('color(srgb 1 0 0)', 'color-mix(in srgb, red, blue)', 'hwb(0 0% 0%)', 'red', 'currentcolor',
                      'oklch(none 0 0)', 'oklch(0.5 none 0)', 'oklch(calc(0.5) 0 0)', 'hsl(1e999 50% 50%)',
                      'oklch(.5 .1 1e999)', 'oklab(1e103 0 0)', 'oklch(1e999 0 0)', 'rgb(1e999 0 0)', 'lab(1e999 0 0)',
                      'oklab(-1e103 0 0)', 'lch(50 1e400 0)', 'rgb(0 0 0 / 1e999)', 'hsl(0 1e999% 50%)',
                      'rgb(var(--x) 0 0)', 'rgb(1 2)', 'rgb(1 2 3 4 5)', 'rgb(1, 2 3)', '#12', '#12345', '#ggg', 'oklch(0.5, 0.1, 20)',
                      'lab(1 2 3deg)', 'oklch(0.5 0.1 20%)', 'rgb()', '', 'light-dark(#fff, #000)', 'var(--x)'):
            with self.subTest(value=value):
                with self.assertRaises(tcol.ColorFormat):
                    tcol.parse_color(value)

    def test_percentages_follow_css_color_4(self):
        def same(percent, plain):
            self.assertEqual(tcol.parse_color(percent).as_255(), tcol.parse_color(plain).as_255(), percent)
        same('oklch(63.7% 0.237 25)', 'oklch(0.637 0.237 25)')
        same('oklch(0.5 100% 30)', 'oklch(0.5 0.4 30)')
        same('oklab(0.5 50% -25%)', 'oklab(0.5 0.2 -0.1)')
        same('lab(50% 20 30)', 'lab(50 20 30)')
        same('lab(100% 0 0)', 'lab(100 0 0)')
        same('lab(50 100% -40%)', 'lab(50 125 -50)')
        same('lch(50 100% 30)', 'lch(50 150 30)')
        same('lch(50% 50% 30)', 'lch(50 75 30)')

    def test_non_finite_and_overflowing_values_are_color_format_never_black(self):
        for value in ('hsl(1e999 50% 50%)', 'oklch(.5 .1 1e999)', 'oklab(1e103 0 0)', 'oklch(1e999 0 0)', 'lab(50 1e300 0)',
                      'oklab(0 1e110 0)'):
            with self.subTest(value=value):
                with self.assertRaises(tcol.ColorFormat):
                    tcol.parse_color(value)

    def test_important_on_an_alias_is_stripped(self):
        self.assertEqual(tcol.resolve_color('--a', {'--a': 'var(--b) !important', '--b': '#123456 !IMPORTANT'}).as_255(),
                         (18, 52, 86))

    def test_alias_limit_counts_the_same_for_a_name_and_a_var_expression(self):
        """Limit 5 liczy `var()` w wartościach tokenów; wejście (`--x` albo `var(--x)`) nie jest aliasem."""
        tokens = {'--c0': '#123456'}
        for n in range(1, 8):
            tokens['--c%d' % n] = 'var(--c%d)' % (n - 1)
        for entry in ('--c5', 'var(--c5)'):
            self.assertEqual(tcol.resolve_color(entry, tokens).as_255(), (18, 52, 86), entry)
        for entry in ('--c6', 'var(--c6)'):
            with self.assertRaises(tcol.ColorFormat):
                tcol.resolve_color(entry, tokens)

    def test_known_contrast_ratios(self):
        def ratio(fg, bg):
            return tcol.contrast_ratio(tcol.parse_color(fg), tcol.parse_color(bg))
        self.assertAlmostEqual(ratio('#000', '#fff'), 21.0, places=9)
        self.assertAlmostEqual(ratio('#fff', '#000'), 21.0, places=9)
        self.assertAlmostEqual(ratio('#fff', '#fff'), 1.0, places=9)
        self.assertAlmostEqual(ratio('#777', '#fff'), 4.478, places=3)
        self.assertAlmostEqual(ratio('#767676', '#fff'), 4.542, places=3)
        for fg, bg in (((12, 34, 56), (250, 240, 230)), ((200, 10, 10), (255, 255, 255)), ((90, 90, 90), (10, 10, 10))):
            self.assertAlmostEqual(ratio('rgb(%d %d %d)' % fg, 'rgb(%d %d %d)' % bg), _ref_ratio(fg, bg), places=6)

    def test_alpha_is_composited_over_the_background_and_the_background_over_white(self):
        half = tcol.parse_color('rgb(0 0 0 / 50%)')
        white, black = tcol.parse_color('#fff'), tcol.parse_color('#000')
        gray = tcol.parse_color('rgb(127.5 127.5 127.5)')
        self.assertAlmostEqual(tcol.contrast_ratio(half, white), tcol.contrast_ratio(gray, white), places=9)
        self.assertAlmostEqual(tcol.contrast_ratio(white, half), tcol.contrast_ratio(white, gray), places=9)
        self.assertAlmostEqual(tcol.contrast_ratio(half, black), 1.0 + 0.0, places=9)  # czarny na czarnym
        self.assertAlmostEqual(tcol.contrast_ratio(tcol.parse_color('transparent'), white), 1.0, places=9)

    def test_alias_chain_depth_and_cycles(self):
        tokens = {'--c0': '#123456'}
        for n in range(1, 8):
            tokens['--c%d' % n] = 'var(--c%d)' % (n - 1)
        self.assertEqual(tcol.resolve_color('--c5', tokens).as_255(), (18, 52, 86))
        with self.assertRaises(tcol.ColorFormat) as ctx:
            tcol.resolve_color('--c6', tokens)
        self.assertEqual(ctx.exception.reason, 'alias')
        with self.assertRaises(tcol.ColorFormat) as ctx:
            tcol.resolve_color('--a', {'--a': 'var(--b)', '--b': 'var(--a)'})
        self.assertEqual(ctx.exception.reason, 'alias')
        with self.assertRaises(tcol.MissingToken) as ctx:
            tcol.resolve_color('--a', {'--a': 'var(--zzz)'})
        self.assertEqual(ctx.exception.name, '--zzz')

    def test_threshold_is_0_04045(self):
        # 10/255 = 0.0392: powyżej 0,03928 i poniżej 0,04045: liniowo (v / 12.92), nie potęgowo.
        self.assertAlmostEqual(tcol.luminance((0.04, 0.04, 0.04)), 0.04 / 12.92, places=12)
        self.assertAlmostEqual(tcol.luminance((0.05, 0.05, 0.05)), ((0.05 + 0.055) / 1.055) ** 2.4, places=12)


class ContrastBase(Base):
    def contrast_cfg(self, css, pairs, **extra):
        self.write('t.css', css)
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'contrast': {'pairs': pairs}}
        cfg['contrast'].update(extra)
        return cfg

    def contrast(self, css, pairs, **extra):
        return self.run_cfg(self.contrast_cfg(css, pairs, **extra), only=['contrast'])

    def one(self, res, status=None):
        found = [f for f in res.findings if f['check'] == 'contrast']
        self.assertEqual(len(found), 1, res.findings)
        if status is not None:
            self.assertEqual(found[0]['status'], status, found[0])
        return found[0]


class ContrastTests(ContrastBase):
    CSS = ':root { --fg: #000; --bg: #fff; --lo: #777; --hi: #767676; }\n'

    def test_pass_and_fail_by_the_minimum(self):
        self.assertEqual(self.contrast(self.CSS, [{'fg': '--fg', 'bg': '--bg'}]).code, 0)
        res = self.contrast(self.CSS, [{'fg': '--lo', 'bg': '--bg'}])
        self.assertEqual(res.code, 1)
        self.assertIn('4.478', self.one(res, tc.FAIL)['message'])
        self.assertEqual(self.contrast(self.CSS, [{'fg': '--hi', 'bg': '--bg'}]).code, 0)
        self.assertEqual(self.contrast(self.CSS, [{'fg': '--lo', 'bg': '--bg', 'min': 3}]).code, 0)
        self.assertEqual(self.contrast(self.CSS, [{'fg': '--hi', 'bg': '--bg', 'min': 7}]).code, 1)

    def test_verdict_uses_the_unrounded_ratio(self):
        def gray_for(target):
            lo, hi = 0.0, 255.0
            for _ in range(60):
                mid = (lo + hi) / 2
                if _ref_ratio((mid, mid, mid), (255, 255, 255)) > target:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2
        below, above = gray_for(4.4999), gray_for(4.5001)
        self.assertGreater(below, above)
        res = self.contrast('', [{'fg': 'rgb(%.6f %.6f %.6f)' % (below, below, below), 'bg': '#fff'}])
        finding = self.one(res, tc.FAIL)
        self.assertEqual(res.code, 1)
        self.assertIn('4.499', finding['message'])
        self.assertLess(finding['ratio'], 4.5)
        res = self.contrast('', [{'fg': 'rgb(%.6f %.6f %.6f)' % (above, above, above), 'bg': '#fff'}])
        self.assertEqual((res.code, self.one(res)['status']), (0, tc.PASS))

    def test_literal_colors_and_token_names_can_be_mixed(self):
        res = self.contrast(self.CSS, [{'fg': '#000', 'bg': '--bg'}, {'fg': '--fg', 'bg': 'white'}])
        self.assertEqual((res.code, self.statuses(res, 'contrast')), (0, [tc.PASS, tc.PASS]))

    def test_literal_only_pairs_do_not_need_the_tokens_key(self):
        res = tc.run({'version': 1, 'contrast': {'pairs': [{'fg': '#000', 'bg': '#fff'}]}}, self.project)
        self.assertEqual(res.code, 0, res.error)

    def test_token_pairs_need_the_tokens_key(self):
        res = tc.run({'version': 1, 'contrast': {'pairs': [{'fg': '--fg', 'bg': '#fff'}]}}, self.project)
        self.assertEqual(res.code, 2)

    def test_alias_chain(self):
        css = ':root { --base: #000; --a: var(--base); --b: var( --a ); --bg: #fff; }\n'
        self.assertEqual(self.contrast(css, [{'fg': '--b', 'bg': '--bg'}]).code, 0)
        self.assertEqual(self.contrast(css, [{'fg': 'var(--b)', 'bg': '--bg'}]).code, 0)

    def test_percent_oklch_in_a_theme_gets_a_verdict(self):
        css = ':root { --fg: oklch(63.7% 0.237 25); --bg: oklch(100% 0 0); }\n'
        res = self.contrast(css, [{'fg': '--fg', 'bg': '--bg', 'min': 3}])
        self.assertEqual((res.code, self.one(res)['status']), (0, tc.PASS))

    def test_non_finite_values_are_unverified_not_an_error(self):
        for value in ('oklch(.5 .1 1e999)', 'hsl(1e999 50% 50%)', 'oklab(1e103 0 0)'):
            with self.subTest(value=value):
                res = self.contrast(':root { --fg: %s; --bg: #fff; }\n' % value, [{'fg': '--fg', 'bg': '--bg'}])
                self.assertEqual((res.code, res.error, self.one(res)['reason']), (3, None, 'color-format'))

    def test_important_alias_and_multiple_definitions_use_the_last_one(self):
        css = ':root { --fg: #fff; --bg: #fff; --a: var(--bg) !important; }\n@theme { --fg: #888; }\n:root { --fg: #000; }\n'
        res = self.contrast(css, [{'fg': '--fg', 'bg': '--a'}])
        self.assertEqual((res.code, self.one(res)['status']), (0, tc.PASS))
        self.write('t.css', ':root{--a:1} @theme{--a:2} :root{--a:3}')
        theme = tc.ThemeFile('t.css', (self.project / 't.css').read_text(encoding='utf-8'))
        self.assertEqual(tc._light_map([theme])['--a'][0], '3')
        self.write('t.css', ':root{--a:1} :root{--a:3} @theme{--a:2}')
        theme = tc.ThemeFile('t.css', (self.project / 't.css').read_text(encoding='utf-8'))
        self.assertEqual(tc._light_map([theme])['--a'][0], '2')

    def test_alias_cycle_and_too_deep_chain_are_unverified(self):
        css = ':root { --a: var(--b); --b: var(--a); --bg: #fff; }\n'
        res = self.contrast(css, [{'fg': '--a', 'bg': '--bg'}])
        self.assertEqual((res.code, self.one(res)['reason']), (3, 'alias'))
        chain = ':root { --c0: #000; %s --bg: #fff; }\n' % ' '.join('--c%d: var(--c%d);' % (i, i - 1) for i in range(1, 8))
        self.assertEqual(self.contrast(chain, [{'fg': '--c5', 'bg': '--bg'}]).code, 0)
        self.assertEqual(self.contrast(chain, [{'fg': '--c6', 'bg': '--bg'}]).code, 3)

    def test_missing_token_fails_with_its_name(self):
        res = self.contrast(self.CSS, [{'fg': '--brak', 'bg': '--bg'}])
        self.assertEqual(res.code, 1)
        self.assertIn('--brak', self.one(res, tc.FAIL)['message'])
        res = self.contrast(':root { --a: var(--zzz); --bg: #fff; }\n', [{'fg': '--a', 'bg': '--bg'}])
        self.assertEqual(res.code, 1)
        self.assertIn('--zzz', self.one(res)['message'])

    def test_token_only_in_the_dark_block_is_missing_for_contrast(self):
        css = ':root { --bg: #fff; }\n.dark { --fg: #000; }\n'
        self.assertEqual(self.contrast(css, [{'fg': '--fg', 'bg': '--bg'}]).code, 1)

    def test_values_are_read_from_the_light_block_only(self):
        css = ':root { --fg: #000; --bg: #fff; }\n.dark { --fg: #fff; --bg: #fff; }\n'
        self.assertEqual(self.contrast(css, [{'fg': '--fg', 'bg': '--bg'}]).code, 0)

    def test_zero_pairs_is_unverified(self):
        for contrast in ({'pairs': []}, {}):
            with self.subTest(contrast=contrast):
                self.write('t.css', self.CSS)
                cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'contrast': contrast}
                res = self.run_cfg(cfg, only=['contrast'])
                self.assertEqual((res.code, self.one(res)['status']), (3, tc.UNVERIFIED))

    def test_alpha_pairs(self):
        css = ':root { --fg: rgba(0, 0, 0, 0.5); --bg: #fff; --veil: rgb(0 0 0 / 50%); }\n'
        res = self.contrast(css, [{'fg': '--fg', 'bg': '--bg', 'min': 3.9}, {'fg': '#fff', 'bg': '--veil', 'min': 3.9}])
        self.assertEqual((res.code, self.statuses(res, 'contrast')), (0, [tc.PASS, tc.PASS]))
        res = self.contrast(css, [{'fg': '--fg', 'bg': '--bg', 'min': 4.5}])
        self.assertEqual(res.code, 1)

    def test_d5_color_formats_give_a_verdict_never_an_exception(self):
        """D-5: `#rgb`, `#rrggbbaa`, `rgba()`, `hsl()`: werdykt albo UNVERIFIED, nie traceback."""
        for value in ('#000', '#000f', '#000000ff', 'rgba(0,0,0,1)', 'rgb(0 0 0 / 1)', 'hsl(0 0% 0%)', 'hsla(0, 0%, 0%, 1)',
                      'oklch(0 0 0)', 'lab(0 0 0)', 'black'):
            with self.subTest(value=value):
                res = self.contrast(':root { --fg: %s; --bg: #fff; }\n' % value, [{'fg': '--fg', 'bg': '--bg'}])
                self.assertEqual((res.code, self.one(res)['status']), (0, tc.PASS), res.error)
        for value in ('color(display-p3 1 0 0)', 'color-mix(in srgb, red, blue)', 'hwb(0 0% 0%)', 'rebeccapurple',
                      'oklch(1e999 0 0)', 'oklch(none 0 0)', 'calc(1)', '1px', 'rgb(0, 0)', '#zzz'):
            with self.subTest(value=value):
                res = self.contrast(':root { --fg: %s; --bg: #fff; }\n' % value, [{'fg': '--fg', 'bg': '--bg'}])
                self.assertEqual((res.code, res.error), (3, None))
                self.assertEqual(self.one(res)['reason'], 'color-format')
                self.assertIn('UNVERIFIED contrast:', res.lines()[0])

    def test_out_of_gamut_flag_is_in_the_finding_and_the_json(self):
        css = ':root { --fg: oklch(0.86644 0.29483 142.495); --in: oklch(0.145 0 0); --bg: #000; }\n'
        res = self.contrast(css, [{'fg': '--fg', 'bg': '--bg', 'min': 1}, {'fg': '--in', 'bg': '--bg', 'min': 1}])
        first, second = [f for f in res.findings if f['check'] == 'contrast']
        self.assertIs(first['outOfGamut'], True)
        self.assertNotIn('outOfGamut', second)
        data = json.loads(json.dumps(res.to_dict()))
        self.assertIs(data['findings'][0]['outOfGamut'], True)
        self.assertGreater(data['findings'][0]['ratio'], 1)
        self.assertIn('poza gamutem', res.lines()[0])

    def test_default_theme_of_the_prototype_library_gets_a_verdict(self):
        default = REPO / 'library' / 'skills' / 'prototype' / 'assets' / 'theme' / 'default.css'
        self.write('t.css', default.read_bytes())
        pairs = [{'name': 'tekst', 'fg': '--foreground', 'bg': '--background'},
                 {'name': 'przycisk', 'fg': '--primary-foreground', 'bg': '--primary'},
                 {'name': 'wyciszony', 'fg': '--muted-foreground', 'bg': '--muted'},
                 {'name': 'błąd', 'fg': '--destructive-foreground', 'bg': '--destructive'}]
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'contrast': {'pairs': pairs}}
        res = self.run_cfg(cfg, only=['contrast'])
        self.assertEqual(self.statuses(res, 'contrast'), [tc.PASS] * 4, [f['message'] for f in res.findings])
        self.assertEqual(res.code, 0)
        ratios = [f['ratio'] for f in res.findings if f['check'] == 'contrast']
        self.assertGreater(ratios[0], 15)
        self.assertGreater(ratios[1], 15)

    def test_findings_keep_the_pair_name_in_the_line_output(self):
        res = self.contrast(self.CSS, [{'name': 'tekst na tle', 'fg': '--lo', 'bg': '--bg'}])
        self.assertIn('FAIL contrast: para "tekst na tle"', res.lines()[0])


class ContrastInputTests(ContrastBase):
    CSS = ContrastTests.CSS
    LOW = {'fg': '--lo', 'bg': '--bg'}

    def devs(self, content='### D12 odstępstwo\n### D13 inne\n', pattern=r'^###\s+(D\d+)\b'):
        if content is not None:
            self.write('deviations.md', content)
        return {'deviations': {'file': 'deviations.md', 'pattern': pattern}}

    def test_deviation_turns_fail_into_warn(self):
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs())
        self.assertEqual((res.code, self.one(res)['status']), (0, tc.WARN))
        self.assertIn('D12', res.findings[0]['message'])

    def test_unknown_deviation_id_still_fails(self):
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D99')], **self.devs())
        self.assertEqual((res.code, self.one(res)['status']), (1, tc.FAIL))
        self.assertIn('D99', res.findings[0]['message'])

    def test_deviation_does_not_hide_a_pair_that_passes(self):
        res = self.contrast(self.CSS, [{'fg': '--fg', 'bg': '--bg', 'deviation': 'D12'}], **self.devs())
        self.assertEqual(self.one(res)['status'], tc.PASS)

    def test_missing_deviation_file_is_exit_2(self):
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs(content=None))
        self.assertEqual(res.code, 2)
        res = self.contrast(self.CSS, [{'fg': '--fg', 'bg': '--bg', 'deviation': 'D12'}], **self.devs(content=None))
        self.assertEqual(res.code, 2)

    def test_deviation_without_deviations_config_is_exit_2(self):
        self.assertEqual(self.contrast(self.CSS, [dict(self.LOW, deviation='D12')]).code, 2)

    def test_overlong_lines_in_the_deviations_file_are_skipped_with_a_warn(self):
        content = '### D12 ' + 'x' * 600 + '\n### D13 krótka\n'
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs(content))
        self.assertEqual(self.statuses(res, 'contrast'), [tc.WARN, tc.FAIL])
        self.assertIn('pominięto 1 linii', res.findings[0]['message'])
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D13')], **self.devs(content))
        self.assertEqual(self.statuses(res, 'contrast'), [tc.WARN, tc.WARN])

    def test_unused_deviations_file_is_not_required(self):
        res = self.contrast(self.CSS, [{'fg': '--fg', 'bg': '--bg'}], **self.devs(content=None))
        self.assertEqual(res.code, 0)

    def test_bad_or_long_patterns_are_exit_2_without_quoting(self):
        sentinel = 'SENTINEL_PATTERN_5b'
        for pattern in ('(' + sentinel, '[' + sentinel, '(?P<' + sentinel, 'x' * 201):
            with self.subTest(pattern=pattern[:12]):
                res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs(pattern=pattern))
                self.assertEqual(res.code, 2)
                self.assertNotIn(sentinel, res.error)

    def test_pattern_without_a_group_uses_the_whole_match(self):
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs(pattern=r'^D\d+'))
        self.assertEqual(self.one(res)['status'], tc.FAIL)  # wiersze zaczynają się od "###", nie od "D12"
        res = self.contrast(self.CSS, [dict(self.LOW, deviation='D12')], **self.devs(pattern=r'D\d+'))
        self.assertEqual(self.one(res)['status'], tc.WARN)

    def test_deviations_file_outside_the_project_is_exit_2(self):
        self.write('x.md', 'D12', base=self.tmp)
        cfg = self.contrast_cfg(self.CSS, [dict(self.LOW, deviation='D12')],
                                deviations={'file': '../x.md', 'pattern': 'D12'})
        self.assertEqual(self.run_cfg(cfg, only=['contrast']).code, 2)

    def pairs_file(self, data, path='design.pairs'):
        self.write('pairs.json', json.dumps(data))
        return {'pairsFrom': {'file': 'pairs.json', 'path': path}}

    def test_pairs_from_a_json_file_with_a_dotted_path(self):
        data = {'design': {'pairs': [{'fg': '--fg', 'bg': '--bg'}, {'fg': '--lo', 'bg': '--bg', 'name': 'szary'}]}}
        res = self.contrast(self.CSS, [], **self.pairs_file(data))
        self.assertEqual((res.code, self.statuses(res, 'contrast')), (1, [tc.PASS, tc.FAIL]))
        self.assertEqual(res.findings[1]['file'], 'pairs.json')
        self.assertIn('szary', res.findings[1]['message'])

    def test_pairs_from_a_list_index_and_both_sources_together(self):
        data = {'a': [{'x': 1}, {'pairs': [{'fg': '--fg', 'bg': '--bg'}]}]}
        res = self.contrast(self.CSS, [{'fg': '--hi', 'bg': '--bg'}], **self.pairs_file(data, 'a.1.pairs'))
        self.assertEqual((res.code, self.statuses(res, 'contrast')), (0, [tc.PASS, tc.PASS]))

    def test_pairs_from_errors_are_exit_2(self):
        good = {'p': [{'fg': '--fg', 'bg': '--bg'}]}
        for data, path in ((good, 'p.q'), (good, 'q'), (good, 'p..x'), ({'p': {'fg': 'a'}}, 'p'), ({'p': ['x']}, 'p'),
                           ({'p': [{'fg': '--fg'}]}, 'p'), ({'p': [{'fg': 'a', 'bg': 'b', 'zzz': 1}]}, 'p'),
                           ({'p': [{'fg': 'a', 'bg': 'b', 'min': 0}]}, 'p'), ({'p': [{'fg': 'a', 'bg': 'b', 'min': True}]}, 'p')):
            with self.subTest(data=data, path=path):
                self.assertEqual(self.contrast(self.CSS, [], **self.pairs_file(data, path)).code, 2)

    def test_pairs_from_missing_invalid_or_escaping_file_is_exit_2(self):
        self.assertEqual(self.contrast(self.CSS, [], pairsFrom={'file': 'brak.json', 'path': 'p'}).code, 2)
        self.write('pairs.json', '{not json')
        self.assertEqual(self.contrast(self.CSS, [], pairsFrom={'file': 'pairs.json', 'path': 'p'}).code, 2)
        self.assertEqual(self.contrast(self.CSS, [], pairsFrom={'file': '../pairs.json', 'path': 'p'}).code, 2)

    def test_pair_shape_errors_are_exit_2(self):
        for pair in ({'fg': '--fg'}, {'bg': '--bg'}, {'fg': '--fg', 'bg': '--bg', 'extra': 1}, {'fg': '--fg', 'bg': '--bg', 'min': -1},
                     {'fg': '--fg', 'bg': '--bg', 'min': 'x'}, {'fg': '', 'bg': '--bg'}, 'x', {'fg': '--fg', 'bg': '--bg', 'deviation': 3}):
            with self.subTest(pair=pair):
                self.assertEqual(self.run_cfg(self.contrast_cfg(self.CSS, [pair])).code, 2)
        for contrast in ([], {'bogus': 1}, {'pairs': 'x'}, {'pairsFrom': {'file': 'a'}}, {'deviations': {'file': 'a'}}):
            with self.subTest(contrast=contrast):
                self.assertEqual(self.run_cfg(dict(self.contrast_cfg(self.CSS, []), contrast=contrast)).code, 2)


class PrototypeBase(Base):
    TOKENS = ':root { --fg: #000; --bg: #fff; --space: 4px; --font: serif; --fw: 600; --lh: 1.5; --z-index-top: 100; }\n'
    RULES = {'color': True, 'font': True, 'radius': {'allow': ['0', '50%']}, 'weight': True, 'lineHeight': True,
             'size': True, 'zIndex': 'tokens'}

    def cfg(self, rules=None, **extra):
        proto = {'dirs': ['protos/*'], 'knownTokensFrom': ['tokens.css'], 'rules': self.RULES if rules is None else rules}
        proto.update(extra)
        return {'version': 1, 'prototype': proto}

    def proto(self, css='', html='<p>x</p>\n', name='a', tokens=None):
        root = 'protos/' + name
        self.write(root + '/project.css', css)
        self.write(root + '/index.html', html)
        if tokens is not False:
            self.write(root + '/tokens.css', self.TOKENS if tokens is None else tokens)

    def check(self, css='', html='<p>x</p>\n', rules=None, **extra):
        self.proto(css, html)
        return self.run_cfg(self.cfg(rules, **extra))

    def fails(self, res):
        return [f for f in res.findings if f['status'] == tc.FAIL]


class PrototypeRulesTests(PrototypeBase):
    def test_clean_prototype_passes(self):
        res = self.check('.a { display: grid; color: var(--fg); gap: var(--space); border-radius: 0; }\n',
                         '<p style="color: var(--fg); display: grid">x</p>\n')
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))
        self.assertIn('protos/a', res.findings[0]['message'])

    def test_empty_project_css_passes(self):
        self.assertEqual(self.check('').code, 0)

    def test_color_literals_fail(self):
        for value in ('#f00', '#ff0000', '#ff000080', 'rgb(1 2 3)', 'rgba(0,0,0,.5)', 'hsl(1 2% 3%)', 'oklch(0.5 0.1 20)',
                      'lab(1 2 3)', 'color-mix(in srgb, red, blue)', 'color(srgb 1 0 0)', 'red', 'RED', 'CanvasText',
                      'ButtonFace', '1px solid #fff', 'light-dark(#fff, #000)', 'var(--fg) red'):
            with self.subTest(value=value):
                res = self.check('.a { border: %s; }\n' % value)
                self.assertEqual(res.code, 1, value)

    def test_color_keywords_and_functions_that_are_not_literals_pass(self):
        for value in ('transparent', 'currentcolor', 'inherit', 'none', 'var(--fg)', 'var(--bg) var(--fg)'):
            with self.subTest(value=value):
                self.assertEqual(self.check('.a { color: %s; }\n' % value).code, 0, value)

    def test_color_literals_in_custom_properties_fail_too(self):
        self.assertEqual(self.check('.a { --mine: #fff; }\n').code, 1)

    def test_font_family_inherit_and_var_pass_but_literal_fails(self):
        self.assertEqual(self.check('.a { font-family: inherit; }\n').code, 0)
        self.assertEqual(self.check('.a { font-family: var(--font); }\n').code, 0)
        self.assertEqual(self.check('.a { font-family: var(--font) !important; }\n').code, 0)
        self.assertEqual(self.check('.a { font-family: var(--font), var(--font); }\n').code, 0)
        for value in ('Arial, sans-serif', '"Helvetica Neue"', 'serif', 'var(--font), serif', 'var(--font, Arial)'):
            with self.subTest(value=value):
                self.assertEqual(self.check('.a { font-family: %s; }\n' % value).code, 1, value)

    def test_font_family_inside_a_string_or_url_is_masked(self):
        self.assertEqual(self.check('.a { content: "font-family: Arial"; font-family: var(--font); }\n').code, 0)

    def test_radius_allow_list_and_long_forms(self):
        self.assertEqual(self.check('.a { border-radius: 0; }\n').code, 0)
        self.assertEqual(self.check('.a { border-radius: 50%; }\n').code, 0)
        self.assertEqual(self.check('.a { border-radius: var(--space); }\n').code, 0)
        for decl in ('border-radius: 8px', 'border-radius: 0 8px', 'border-radius: 0 / 8px', 'border-top-left-radius: 8px',
                     'border-bottom-right-radius: 1em', 'border-start-start-radius: 4px', 'border-radius: 999px',
                     'border-radius: inherit'):
            with self.subTest(decl=decl):
                self.assertEqual(self.check('.a { %s; }\n' % decl).code, 1, decl)

    def test_vendor_prefixed_radius_is_covered(self):
        for decl in ('-webkit-border-radius: 8px', '-moz-border-radius: 8px', '-webkit-border-top-left-radius: 8px'):
            with self.subTest(decl=decl):
                self.assertEqual(self.check('.a { %s; }\n' % decl).code, 1, decl)
        self.assertEqual(self.check('.a { -webkit-border-radius: 0; }\n').code, 0)

    def test_radius_true_allows_nothing_and_false_or_missing_is_off(self):
        self.assertEqual(self.check('.a { border-radius: 0; }\n', rules={'radius': True}).code, 1)
        self.assertEqual(self.check('.a { border-radius: 8px; }\n', rules={'radius': False}).code, 0)
        self.assertEqual(self.check('.a { border-radius: 8px; }\n', rules={'color': True}).code, 0)

    def test_weight_and_line_height_accept_only_var(self):
        self.assertEqual(self.check('.a { font-weight: var(--fw); line-height: var(--lh); }\n').code, 0)
        self.assertEqual(self.check('.a { font-weight: inherit; line-height: inherit; }\n').code, 0)
        for decl in ('font-weight: 700', 'font-weight: bold', 'font-weight: normal', 'line-height: 1.5', 'line-height: normal',
                     'line-height: 20px', 'font-weight: var(--fw, 700)', 'line-height: var(--lh, 1.2)'):
            with self.subTest(decl=decl):
                self.assertEqual(self.check('.a { %s; }\n' % decl).code, 1, decl)

    def test_size_rule_forbids_literal_font_sizes_only_when_on(self):
        for decl in ('font-size: 14px', 'font-size: 1rem', 'font-size: 120%', 'font: 12px/1.5 var(--font)',
                     'font-size: .75em', 'font-size: 2vw'):
            with self.subTest(decl=decl):
                self.assertEqual(self.check('.a { %s; }\n' % decl).code, 1, decl)
                self.assertEqual(self.check('.a { %s; }\n' % decl, rules={'size': False}).code, 0)
        self.assertEqual(self.check('.a { font-size: var(--space); width: 14px; gap: 8px; }\n').code, 0)

    def test_z_index_modes(self):
        tokens = {'zIndex': 'tokens'}
        self.assertEqual(self.check('.a { z-index: var(--z-index-top); }\n', rules=tokens).code, 0)
        for value in ('5', 'auto', 'var(--space)', 'var(--z-index-top, 100)', '1 var(--z-index-top)'):
            with self.subTest(value=value):
                self.assertEqual(self.check('.a { z-index: %s; }\n' % value, rules=tokens).code, 1, value)
        self.assertEqual(self.check('.a { z-index: var(--z-index-top); }\n', rules={'zIndex': 'forbid'}).code, 1)
        self.assertEqual(self.check('.a { z-index: 7; }\n', rules={'zIndex': 'allow'}).code, 0)
        self.assertEqual(self.check('.a { z-index: 7; }\n', rules={'color': True}).code, 0)

    def test_unknown_var_is_a_fail_even_with_no_rules(self):
        res = self.check('.a { gap: var(--nie-ma); }\n', rules={})
        self.assertEqual(res.code, 1)
        self.assertIn('--nie-ma', self.fails(res)[0]['message'])

    def test_balanced_var_fallback_is_scanned_like_a_value(self):
        self.assertEqual(self.check('.a { color: var(--fg, var(--bg)); }\n').code, 0)
        self.assertEqual(self.check('.a { color: var(--fg, var(--bg, var(--fg))); }\n').code, 0)
        for value in ('var(--fg, #f00)', 'var(--fg, rgba(0, 0, 0, .5))', 'var(--fg, var(--bg, red))', 'var(--fg, red)',
                      'var(--fg, var(--zzz))', 'var(--zzz, var(--fg))', 'var(--fg, hsl(1 2% 3%)) red'):
            with self.subTest(value=value):
                self.assertEqual(self.check('.a { color: %s; }\n' % value).code, 1, value)

    def test_fallback_with_calc_and_commas_inside_balanced_parentheses(self):
        self.assertEqual(self.check('.a { margin: var(--space, calc(1px + 2px)); }\n').code, 0)
        self.assertEqual(self.check('.a { color: var(--fg, rgb(0 0 0)) ; }\n').code, 1)

    def test_url_and_strings_are_masked(self):
        css = ('.a { background: url(red.png); background-image: url("#fff"); content: "red #fff rgb(1 2 3)"; }\n'
               ".b { quotes: 'red' 'blue'; background: url( data:image/svg+xml;utf8,<svg fill='red'/> ); }\n")
        self.assertEqual(self.check(css).code, 0)

    def test_comments_are_ignored(self):
        css = '/* color: #f00; */ .a { /* border-radius: 8px; */ color: var(--fg); }\n'
        self.assertEqual(self.check(css).code, 0)

    def test_important_suffix_does_not_hide_a_literal(self):
        self.assertEqual(self.check('.a { color: #f00 !important; }\n').code, 1)
        self.assertEqual(self.check('.a { color: var(--fg) !important; }\n').code, 0)

    def test_last_declaration_without_a_semicolon_is_checked(self):
        self.assertEqual(self.check('.a { color: var(--fg); background: #fff }\n').code, 1)

    def test_nested_and_at_rules_are_scanned(self):
        css = '@media (min-width: 600px) { .a { color: #f00; } }\n@font-face { src: url(x.woff2); }\n'
        res = self.check(css)
        self.assertEqual(res.code, 1)
        self.assertEqual(self.fails(res)[0]['line'], 1)

    def test_selectors_with_colons_are_not_declarations(self):
        self.assertEqual(self.check('a:hover, .b:not(.c) { color: var(--fg); }\n@media (min-width: 1px) { .d { gap: 0; } }\n').code, 0)

    def test_style_attributes_and_style_blocks_in_html(self):
        res = self.check('', '<p style="color: #ff0000">x</p>\n')
        self.assertEqual(res.code, 1)
        res = self.check('', "<p style='background: red'>x</p>\n")
        self.assertEqual(res.code, 1)
        res = self.check('', '<style>.x { color: #f00 }</style><p>x</p>\n')
        self.assertEqual(res.code, 1)
        res = self.check('', '<p style="background: var(--nieznany)">x</p>\n')
        self.assertIn('--nieznany', self.fails(res)[0]['message'])
        res = self.check('', '<style>.x { color: var(--fg) }</style><p style="color: var(--fg)">x</p>\n')
        self.assertEqual(res.code, 0)

    def test_html_prose_with_an_apostrophe_and_comments_is_not_style(self):
        res = self.check('', "<!-- stare: color: #f00 -->\n<p>it's red and #fff</p>\n")
        self.assertEqual(res.code, 0)

    def test_line_numbers_in_css_html_style_blocks_and_attributes(self):
        css = '/* a\n b */\n.a {\n  color: var(--fg);\n  background: #fff;\n}\n'
        html = '<html>\n<head>\n<style>\n.x {\n  color: #f00;\n}\n</style>\n</head>\n<body>\n<p\n style="gap: 0;\n color: red">x</p>\n</body>\n</html>\n'
        res = self.check(css, html)
        lines = {(f['file'], f['line']) for f in self.fails(res)}
        self.assertEqual(lines, {('protos/a/project.css', 5), ('protos/a/index.html', 5), ('protos/a/index.html', 12)})
        self.assertIn('[protos/a/project.css:5]', '\n'.join(res.lines()))

    def test_crlf_files_keep_line_numbers(self):
        res = self.check('.a {\r\n  color: var(--fg);\r\n  color: #fff;\r\n}\r\n')
        self.assertEqual(self.fails(res)[0]['line'], 3)

    def test_deeply_nested_css_is_exit_2(self):
        res = self.check('.a {' * 201 + ' color: #fff; ' + '}' * 201)
        self.assertEqual(res.code, 2)


class PrototypeKnownTokensTests(PrototypeBase):
    def test_empty_known_token_set_is_a_fail_with_a_message(self):
        self.proto('.a { color: red; }\n', tokens=False)
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertTrue(any('brak znanych tokenów' in f['message'] for f in self.fails(res)))
        self.proto('', tokens='/* nic */\n')
        self.assertEqual(self.run_cfg(self.cfg()).code, 1)

    def test_known_tokens_come_from_each_prototype_directory(self):
        self.proto('.a { gap: var(--only-a); }\n', name='a', tokens=':root { --only-a: 1px; }\n')
        self.proto('.a { gap: var(--only-a); }\n', name='b', tokens=':root { --only-b: 1px; }\n')
        res = self.run_cfg(self.cfg())
        self.assertEqual({f['file'] for f in self.fails(res)}, {'protos/b/project.css'})

    def test_known_tokens_from_several_files_and_dark_blocks(self):
        self.proto('.a { gap: var(--x) var(--y) var(--z); }\n', tokens=':root { --x: 1; }\n')
        self.write('protos/a/theme.css', ':root { --y: 2; }\n.dark { --z: 3; }\n')
        cfg = self.cfg(knownTokensFrom=['tokens.css', 'theme.css'])
        self.assertEqual(self.run_cfg(cfg).code, 0)
        self.assertEqual(self.run_cfg(self.cfg(knownTokensFrom=['tokens.css'])).code, 1)

    def test_missing_known_tokens_file_is_not_an_error_by_itself(self):
        self.proto('.a { gap: var(--x); }\n', tokens=':root { --x: 1; }\n')
        cfg = self.cfg(knownTokensFrom=['tokens.css', 'brak.css'])
        self.assertEqual(self.run_cfg(cfg).code, 0)

    def test_tokens_files_of_the_project_are_known_in_every_prototype(self):
        self.proto('.a { gap: var(--shared); }\n', tokens=False)
        self.write('design.css', ':root { --shared: 1px; }\n')
        cfg = self.cfg(knownTokensFrom=[])
        cfg['tokens'] = {'files': ['design.css'], 'required': None}
        res = self.run_cfg(cfg, only=['prototype'])
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_own_declarations_in_project_css_are_not_known_tokens(self):
        self.assertEqual(self.check('.a { --mine: 1px; gap: var(--mine); }\n', rules={}).code, 1)


class PrototypeDirsTests(PrototypeBase):
    def test_no_matching_directory_is_unverified(self):
        res = self.run_cfg(self.cfg())
        self.assertEqual((res.code, self.statuses(res)), (3, [tc.UNVERIFIED]))

    def test_directory_without_the_marker_is_not_a_prototype(self):
        self.write('protos/fonts/a.woff2', b'x')
        self.write('protos/fonts/project.css', '.a { color: #f00; }\n')
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 3)
        self.proto('.a { color: var(--fg); }\n', name='ok')
        res = self.run_cfg(self.cfg())
        self.assertEqual((res.code, self.statuses(res)), (0, [tc.PASS]))

    def test_custom_marker_and_files(self):
        self.write('protos/z/proto.json', '{}')
        self.write('protos/z/own.css', '.a { color: #f00; }\n')
        self.write('protos/z/tokens.css', self.TOKENS)
        res = self.run_cfg(self.cfg(dirMarker='proto.json', files=['own.css']))
        self.assertEqual(res.code, 1)
        self.assertEqual(self.fails(res)[0]['file'], 'protos/z/own.css')

    def test_patterns_literal_glob_and_fnmatch_are_case_sensitive(self):
        self.proto('', name='Alpha')
        self.proto('.a { color: red; }\n', name='beta')
        self.assertEqual(self.run_cfg(self.cfg(dirs=['protos/Alpha'])).code, 0)
        self.assertEqual(self.run_cfg(self.cfg(dirs=['protos/alpha'])).code, 3)
        self.assertEqual(self.run_cfg(self.cfg(dirs=['protos/[A]*'])).code, 0)
        self.assertEqual(self.run_cfg(self.cfg(dirs=['protos/?eta'])).code, 1)
        self.assertEqual(self.run_cfg(self.cfg(dirs=['pro*/*'])).code, 1)
        self.assertEqual(self.run_cfg(self.cfg(dirs=['protos/*', 'protos/beta'])).code, 1)

    def test_directories_are_reported_independently_and_not_twice(self):
        self.proto('.a { color: red; }\n', name='bad')
        self.proto('', name='good')
        res = self.run_cfg(self.cfg(dirs=['protos/*', 'protos/good']))
        self.assertEqual(self.statuses(res), [tc.FAIL, tc.PASS])
        self.assertEqual(res.code, 1)

    def test_missing_file_fails_by_default_and_skip_adds_a_note(self):
        self.proto('', name='a')
        (self.project / 'protos' / 'a' / 'project.css').unlink()
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 1)
        self.assertEqual((self.fails(res)[0]['message'], self.fails(res)[0]['file']), ('brak pliku', 'protos/a/project.css'))
        res = self.run_cfg(self.cfg(onMissingFile='skip'))
        self.assertEqual(res.code, 0)
        notes = [f for f in res.findings if f['status'] == tc.INFO]
        self.assertEqual(len(notes), 1)
        self.assertIn('skip', notes[0]['message'])

    def test_nothing_checked_is_unverified(self):
        self.write('protos/a/proto.json', '{}')
        self.write('protos/a/tokens.css', self.TOKENS)
        res = self.run_cfg(self.cfg(dirMarker='proto.json', onMissingFile='skip'))
        self.assertEqual(res.code, 3)
        self.assertIn(tc.UNVERIFIED, self.statuses(res))

    def test_directory_symlink_leaving_the_project_is_exit_2(self):
        outside = self.tmp / 'outside'
        (outside / 'a').mkdir(parents=True)
        (outside / 'a' / 'index.html').write_text('<p>x</p>', encoding='utf-8')
        (self.project / 'protos').mkdir()
        try:
            os.symlink(outside / 'a', self.project / 'protos' / 'a')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_file_symlink_leaving_the_project_is_exit_2(self):
        self.proto('', name='a')
        outside = self.tmp / 'outside.css'
        outside.write_text('.a { color: #f00; }\n', encoding='utf-8')
        (self.project / 'protos' / 'a' / 'project.css').unlink()
        try:
            os.symlink(outside, self.project / 'protos' / 'a' / 'project.css')
        except OSError:
            self.skipTest('system nie pozwala tworzyć dowiązań')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_oversized_and_non_utf8_files_are_exit_2(self):
        self.proto('/*' + ' ' * (2 * 1024 * 1024) + '*/', name='a')
        res = self.run_cfg(self.cfg())
        self.assertEqual(res.code, 2)
        self.assertIn('2 MiB', res.error)
        self.proto(b'.a { color: \xff\xfe; }', name='a')
        self.assertEqual(self.run_cfg(self.cfg()).code, 2)

    def test_utf8_bom_is_accepted(self):
        self.proto(b'\xef\xbb\xbf.a { color: var(--fg); }\n', name='a')
        self.assertEqual(self.run_cfg(self.cfg()).code, 0)

    def test_config_errors_are_exit_2(self):
        for extra in ({'dirs': []}, {'dirs': ['../x']}, {'dirs': ['/x']}, {'dirs': ['C:x']}, {'dirs': 'protos/*'},
                      {'dirMarker': ''}, {'dirMarker': '../x'}, {'files': []}, {'files': ['../x.css']}, {'files': ['/x.css']},
                      {'knownTokensFrom': ['../x.css']}, {'onMissingFile': 'warn'}, {'bogus': 1},
                      {'rules': {'bogus': True}}, {'rules': {'zIndex': 'maybe'}}, {'rules': {'color': 'yes'}},
                      {'rules': {'radius': 'x'}}, {'rules': {'radius': {}}}, {'rules': {'radius': {'allow': [1]}}},
                      {'rules': {'radius': {'allow': [], 'x': 1}}}, {'rules': []}):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_cfg(self.cfg(**extra)).code, 2, extra)
        cfg = self.cfg()
        del cfg['prototype']['dirs']
        self.assertEqual(self.run_cfg(cfg).code, 2)
        self.assertEqual(self.run_cfg({'version': 1, 'prototype': []}).code, 2)

    def test_prototype_config_does_not_need_the_tokens_key(self):
        self.proto('', name='a')
        self.assertEqual(self.run_cfg(self.cfg()).code, 0)


class ParityCoreTests(Base):
    """Przypadki ogólne ze skryptu projektowego, które nie mają własnej klasy (K9)."""

    def test_theme_file_with_crlf_is_read_like_lf(self):
        cfg = self.tokens_cfg((LIGHT + DARK).replace('\n', '\r\n'), dark='separate')
        self.assertEqual(self.run_cfg(cfg).code, 0)

    def test_extra_required_token_in_an_unchanged_file_fails(self):
        cfg = self.tokens_cfg(LIGHT + DARK, required='--a\n--b\n--nowy\n')
        res = self.run_cfg(cfg)
        self.assertEqual(res.code, 1)
        self.assertIn('--nowy', res.findings[0]['message'])


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
        self.assertTrue({'tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'contrast', 'prototype', 'config'} <= checks)
        self.assertGreaterEqual(count, 45)

    def test_every_check_has_a_failing_case(self):
        failing = {c['check'] for c in tc.selftest_cases() if c['status'] in (tc.FAIL, tc.UNVERIFIED)}
        self.assertTrue({'tokens', 'dark', 'forbid', 'copies', 'copyDirs', 'contrast', 'prototype', 'config'} <= failing)

    def test_selftest_detects_a_broken_check(self):
        case = next(c for c in tc.selftest_cases() if c['name'] == 'forbid: color-scheme')
        self.assertEqual((case['code'], case['status']), (1, tc.FAIL))
        with tempfile.TemporaryDirectory(prefix='theme-check-forbid-') as tmp:
            for rel, content in case['files'].items():
                Path(tmp, *rel.split('/')).parent.mkdir(parents=True, exist_ok=True)
                Path(tmp, *rel.split('/')).write_text(content, encoding='utf-8')
            real = tc.run(case['config'], tmp, None, only=case['only'])
        self.assertEqual((real.code, real.error), (1, None))
        always_pass = lambda cfg, files, res, complete=True: res.add(tc.PASS, 'forbid', 'zawsze PASS')  # noqa: E731
        with mock.patch.object(tc, 'check_forbid', always_pass):
            problems, _ = tc.selftest()
        self.assertTrue(any(p.startswith('forbid: color-scheme: kod 0 zamiast 1') for p in problems), problems)


    def test_selftest_detects_a_broken_contrast_or_prototype_check(self):
        for name, check in (('contrast', 'check_contrast'), ('prototype', 'check_prototype')):
            with self.subTest(check=name):
                with mock.patch.object(tc, check, lambda ctx, cfg, files, res, n=name: res.add(tc.PASS, n, 'zawsze PASS')):
                    problems, _ = tc.selftest()
                self.assertTrue(any(name in p for p in problems), problems)

    def test_selftest_detects_a_contrast_that_ignores_the_minimum(self):
        with mock.patch.object(tcol, 'contrast_ratio', lambda fg, bg: 21.0):
            problems, _ = tc.selftest()
        self.assertTrue(any('contrast' in p for p in problems), problems)


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

    def test_contrast_exit_codes_0_1_2_3(self):
        self.write('t.css', ':root { --fg: #000; --lo: #777; --bg: #fff; --mix: color-mix(in srgb, red, blue); }\n')

        def code(pairs, **extra):
            cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None}, 'contrast': dict({'pairs': pairs}, **extra)}
            return self.cli('--config', self.config_file(cfg), '--project', self.project, '--only', 'contrast')
        ok, low = {'fg': '--fg', 'bg': '--bg'}, {'fg': '--lo', 'bg': '--bg'}
        proc = code([ok])
        self.assertEqual((proc.returncode, proc.stderr), (0, ''))
        self.assertIn('PASS contrast:', proc.stdout)
        self.assertEqual(code([low]).returncode, 1)
        self.assertEqual(code([dict(low, deviation='D1')]).returncode, 2)
        self.assertEqual(code([{'fg': '--fg'}]).returncode, 2)
        proc = code([{'fg': '--mix', 'bg': '--bg'}])
        self.assertEqual(proc.returncode, 3)
        self.assertIn('UNVERIFIED contrast:', proc.stdout)
        self.assertEqual(code([]).returncode, 3)

    def test_prototype_exit_codes_0_1_2_3(self):
        self.write('protos/a/tokens.css', ':root { --fg: #000; }\n')
        self.write('protos/a/index.html', '<p>x</p>\n')
        self.write('protos/a/project.css', '.a { color: var(--fg); }\n')
        proto = {'dirs': ['protos/*'], 'knownTokensFrom': ['tokens.css'], 'rules': {'color': True}}

        def run(**extra):
            return self.cli('--config', self.config_file({'version': 1, 'prototype': dict(proto, **extra)}),
                            '--project', self.project)
        proc = run()
        self.assertEqual((proc.returncode, proc.stderr), (0, ''))
        self.assertIn('PASS prototype:', proc.stdout)
        self.assertEqual(run(dirs=['nic/*']).returncode, 3)
        self.assertEqual(run(rules={'zIndex': 'maybe'}).returncode, 2)
        self.write('protos/a/project.css', '.a {\n  color: #f00;\n}\n')
        proc = run()
        self.assertEqual(proc.returncode, 1)
        self.assertIn('FAIL prototype: literał koloru w color: #f00 [protos/a/project.css:2]', proc.stdout)

    def test_json_carries_out_of_gamut_color_format_and_prototype_lines(self):
        self.write('t.css', ':root { --g: oklch(0.86644 0.29483 142.495); --bg: #000; --mix: color-mix(in srgb, red, blue); }\n')
        self.write('protos/a/tokens.css', ':root { --fg: #000; }\n')
        self.write('protos/a/index.html', '<p>x</p>\n')
        self.write('protos/a/project.css', '\n.a { color: #f00; }\n')
        cfg = {'version': 1, 'tokens': {'files': ['t.css'], 'required': None},
               'contrast': {'pairs': [{'fg': '--g', 'bg': '--bg', 'min': 1}, {'fg': '--mix', 'bg': '--bg'}]},
               'prototype': {'dirs': ['protos/*'], 'knownTokensFrom': ['tokens.css'], 'rules': {'color': True}}}
        path = self.config_file(cfg)
        proc = self.cli('--config', path, '--project', self.project, '--only', 'contrast,prototype', '--json')
        self.assertEqual(proc.returncode, 1, proc.stderr)
        data = json.loads(proc.stdout)
        by = [f for f in data['findings'] if f['check'] == 'contrast']
        self.assertIs(by[0]['outOfGamut'], True)
        self.assertEqual((by[1]['status'], by[1]['reason']), (tc.UNVERIFIED, 'color-format'))
        proto = [f for f in data['findings'] if f['check'] == 'prototype' and f['status'] == tc.FAIL]
        self.assertEqual((proto[0]['file'], proto[0]['line']), ('protos/a/project.css', 2))
        text = self.cli('--config', path, '--project', self.project, '--only', 'contrast,prototype').stdout.splitlines()
        self.assertEqual(len(text) - 1, len(data['findings']))

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
