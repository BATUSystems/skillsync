"""JSON contract for IDE integrations: `skills list`, `skills remove` and `status` (schema 1)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import transitional_config  # noqa: E402
from tests.test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402
from layers_helpers import needs_mode_enforcement, needs_os_symlinks  # noqa: E402

COMPANIONS = ('workflow', 'pre-implement', 'smart-test', 'bugfix', 'codereview')


class IdeContract(WorkflowFixture):
    def setUp(self):
        super().setUp()
        for name in ('feature', *COMPANIONS, 'tagged', 'glob-a', 'glob-b', 'profiled', 'solo', 'techy'):
            self.skill(name)
        (self.library / 'skills/solo/references').mkdir()
        (self.library / 'skills/solo/references/extra.md').write_text('x', encoding='utf-8')
        self.write_library('workflow/skill-catalog.yaml', yaml.safe_dump({'version': 1, 'orgs': {}, 'skills': {
            'feature': {'scope': 'workflow'}, 'solo': {'scope': 'tool'}, 'techy': {'scope': 'tech', 'tech': ['django']}}}))
        self.write_library('workflow/skill-profiles.yaml', yaml.safe_dump({'version': 1, 'profiles': {'extra': ['profiled']}}))
        config = self.base / 'config.yaml'
        config.write_text(yaml.safe_dump(transitional_config(self.library, legacy_key=False, tags={'grp': ['tagged']})), encoding='utf-8')
        self.env = {**os.environ, 'SKILLSYNC_CONFIG': str(config)}
        self.manifest.update({'skills': ['feature', '@grp', 'glob-*', 'agentflow'], 'project': {'name': 'Org/proj'}})
        self.manifest['workflow'] = {'skill_profiles': {'selected': ['extra']}}
        self.save()

    def skill(self, name):
        folder = self.library / 'skills' / name
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: opis {name}\n---\nBody\n', encoding='utf-8')

    def write_library(self, name, content):
        path = self.library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=30,
                                cwd=self.root, env=self.env)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip().startswith('{') else result.stdout

    def rows(self, report):
        return {row['name']: row for row in report['skills']}

    def test_skills_list_membership_matches_the_authoritative_expansion(self):
        from skillsync_workflow import common
        report = self.run_cli('skills', 'list', self.root, '--json')
        self.assertEqual((report['schema'], report['status'], report['project']), (1, 'PASS', 'Org/proj'))
        members = {row['name'] for row in report['skills'] if row['status'] in ('active', 'companion')}
        with patch.dict(os.environ, {'SKILLSYNC_CONFIG': self.env['SKILLSYNC_CONFIG']}):
            _root, _manifest, layerset = common.load_layer_project(self.root)
            expected = set(common.expand_project_skills(self.manifest, layerset))
        self.assertEqual(members, expected)

    def test_skills_list_explains_origin_status_and_metadata(self):
        rows = self.rows(self.run_cli('skills', 'list', self.root, '--json'))
        self.assertEqual((rows['feature']['status'], rows['feature']['origin']), ('active', 'manifest'))
        self.assertEqual(rows['tagged']['origin'], 'tag')
        self.assertEqual(rows['glob-a']['origin'], 'wildcard')
        self.assertEqual(rows['profiled']['origin'], 'profile')
        self.assertEqual((rows['workflow']['status'], rows['workflow']['origin']), ('companion', 'companion'))
        self.assertEqual((rows['solo']['status'], rows['solo']['origin']), ('available', None))
        self.assertEqual(rows['solo']['scope'], 'tool')
        self.assertEqual(rows['solo']['references'], ['references/extra.md'])
        self.assertEqual(rows['solo']['descriptionBytes'], len('opis solo'.encode()))
        self.assertEqual(rows['profiled']['profiles'], ['extra'])
        self.assertEqual(rows['feature']['path'], 'skills/feature/SKILL.md')

    def test_skills_list_status_filter_and_errors(self):
        only = self.run_cli('skills', 'list', self.root, '--json', '--status', 'available')
        self.assertTrue(only['skills'] and all(row['status'] == 'available' for row in only['skills']))
        bad = self.run_cli('skills', 'list', self.root, '--json', '--status', 'nope', code=2)
        self.assertEqual((bad['schema'], bad['status']), (1, 'BLOCKED'))
        missing = self.run_cli('skills', 'list', self.base / 'nowhere', '--json', code=2)
        self.assertEqual(missing['status'], 'BLOCKED')

    def test_skills_list_is_read_only(self):
        before = (self.root / '.github/.skillsync.yaml').read_text()
        self.run_cli('skills', 'list', self.root, '--json')
        self.assertEqual((self.root / '.github/.skillsync.yaml').read_text(), before)
        self.assertFalse((self.root / '.claude').exists())

    def isolated_link_env(self):
        cache = self.base / 'cache-home'
        self.env.update({'HOME': str(cache), 'USERPROFILE': str(cache), 'XDG_CACHE_HOME': str(cache / 'xdg'),
                         'LOCALAPPDATA': str(cache / 'local')})

    def test_skills_remove_reports_the_link_it_ran(self):
        self.isolated_link_env()
        self.manifest['skills'].append('solo')
        self.save()
        result = self.run_cli('skills', 'remove', self.root, 'solo', '--json')
        self.assertEqual((result['status'], result['linked'], result['link_status']), ('PASS', True, 'PASS'))
        self.manifest['skills'].append('solo')
        self.save()
        skipped = self.run_cli('skills', 'remove', self.root, 'solo', '--no-link', '--json')
        self.assertEqual((skipped['status'], skipped['linked'], skipped['link_status']), ('PASS', False, 'NOT_RUN'))

    def test_skills_remove_with_a_blocked_link_is_not_reported_as_linked(self):
        self.isolated_link_env()
        self.manifest['skills'].append('solo')
        self.save()
        layer_yaml = self.library / 'layer.yaml'
        layer_yaml.write_text(layer_yaml.read_text(encoding='utf-8').replace(
            'core_min:', 'core_min: 99.0.0\n#', 1), encoding='utf-8')
        before = sorted(p.name for p in (self.root / '.github').glob('*'))
        result = self.run_cli('skills', 'remove', self.root, 'solo', '--json', code=2)
        self.assertEqual((result['status'], result['linked'], result['link_status']), ('BLOCKED', False, 'BLOCKED'))
        self.assertIn('link', result['reason'])
        self.assertNotIn('solo', yaml.safe_load((self.root / '.github/.skillsync.yaml').read_text(encoding='utf-8'))['skills'])
        self.assertEqual(sorted(p.name for p in (self.root / '.github').glob('*')), before, 'no artefacts regenerated')

    def test_skills_remove_edits_only_explicit_entries(self):
        self.manifest['skills'] = ['feature', 'solo']
        self.manifest['workflow'] = {}
        self.save()
        result = self.run_cli('skills', 'remove', self.root, 'solo', '--no-link', '--json')
        self.assertEqual((result['schema'], result['status'], result['removed'], result['linked']), (1, 'PASS', 'solo', False))
        self.assertEqual(yaml.safe_load((self.root / '.github/.skillsync.yaml').read_text())['skills'], ['feature'])
        again = self.run_cli('skills', 'remove', self.root, 'solo', '--no-link', '--json', code=2)
        self.assertEqual(again['status'], 'BLOCKED')
        companion = self.run_cli('skills', 'remove', self.root, 'workflow', '--no-link', '--json', code=2)
        self.assertIn('companion', companion['reason'])
        self.assertEqual(yaml.safe_load((self.root / '.github/.skillsync.yaml').read_text())['skills'], ['feature'])

    def plan(self, name, text):
        path = self.root / 'docs/plans' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_status_parses_plans_progress_links_and_needs_human(self):
        self.plan('2026-10-01-a.md', '# Plan A\n\nStatus: draft (tylko plan)\n\n## Progress\n\n> Konwencja\nPR: #12 (link: https://example/pr/12)\n'
                  'Ticket: ABC-7 (link: https://example/browse/ABC-7)\n\n### Faza 1\n- [x] 1.1 pierwszy — a1b2c3d\n- [ ] 1.2 drugi\n\n## Inne\n- [ ] nie krok\n')
        self.plan('2026-10-02-b.md', '# Plan B\nStatus: done  \n')
        self.plan('2026-10-03-c.md', '# Plan C\nbrak statusu\n')
        self.plan('README.md', '# Indeks\nStatus: draft\n')
        report = self.run_cli('status', self.root, '--json')
        by_name = {Path(plan['path']).name: plan for plan in report['plans']}
        self.assertNotIn('README.md', by_name)
        a = by_name['2026-10-01-a.md']
        self.assertEqual((a['title'], a['status'], a['steps'], a['needsHuman']), ('Plan A', 'draft', {'total': 2, 'done': 1}, ['approval']))
        self.assertEqual(a['pr'], {'number': 12, 'link': 'https://example/pr/12'})
        self.assertEqual(a['ticket'], {'key': 'ABC-7', 'link': 'https://example/browse/ABC-7'})
        self.assertEqual((by_name['2026-10-02-b.md']['status'], by_name['2026-10-02-b.md']['steps']), ('done', None))
        self.assertEqual(by_name['2026-10-03-c.md']['status'], 'unknown')
        self.assertEqual([Path(p['path']).name for p in report['plans']][0], '2026-10-03-c.md')

    def test_status_flags_closure_for_in_progress_plan_with_all_steps_checked(self):
        self.plan('2026-10-01-a.md', '# A\nStatus: in_progress\n\n## Progress\n- [x] 1 a\n- [x] 2 b\n')
        self.plan('2026-10-02-b.md', '# B\nStatus: in_progress (blocked — czeka)\n\n## Progress\n- [x] 1 a\n- [ ] 2 b\n')
        self.plan('2026-10-03-c.md', '# C\nStatus: done\n\n## Progress\n- [x] 1 a\n')
        self.plan('2026-10-04-d.md', '# D\nStatus: in_progress\n')
        self.plan('2026-10-05-e.md', '# E\nStatus: in_progress\n\n## Progress\nTicket: ABC-1 (link: https://example/ABC-1)\n- [x] 1 a\n')
        rows = {Path(p['path']).stem[-1]: p['needsHuman'] for p in self.run_cli('status', self.root, '--json')['plans']}
        self.assertEqual(rows, {'a': ['closure'], 'b': [], 'c': [], 'd': [], 'e': ['pull_request']})

    def test_status_tolerates_older_plan_status_styles(self):
        styles = {'d1': '**Status:** done; drift pogodzony', 'd2': 'Status: **in_progress**', 'd3': 'Status: `done` (GO)',
                  'd4': '- Status: approved', 'd5': '> Status: **done** z dowodami', 'd6': '---\nstatus: draft\n---',
                  'd7': 'Status: implemented', 'd8': 'Status:\nin_progress', 'd9': '| Status: done |'}
        for name, line in styles.items():
            self.plan(f'2026-10-01-{name}.md', f'{line}\n\n# Plan {name}\n')
        by_name = {Path(p['path']).stem[-2:]: p for p in self.run_cli('status', self.root, '--json')['plans']}
        self.assertEqual({k: v['status'] for k, v in by_name.items()},
                         {'d1': 'done', 'd2': 'in_progress', 'd3': 'done', 'd4': 'approved', 'd5': 'done', 'd6': 'draft',
                          'd7': 'unknown', 'd8': 'unknown', 'd9': 'unknown'})
        self.assertEqual((by_name['d1']['statusText'], by_name['d7']['statusText'], by_name['d9']['statusText']),
                         ('done; drift pogodzony', 'implemented', None))
        self.assertEqual(by_name['d6']['needsHuman'], ['approval'])

    def test_status_steps_fall_back_to_kroki_checklist_without_progress(self):
        self.plan('2026-10-01-k.md', '# K\nStatus: done\n\n## Kryteria\n- [ ] kryterium\n\n## Kroki\n- [x] **1.** a\n- [x] **2.** b\n'
                  '- [ ] **3.** c\n\n## Ryzyka\n- [ ] nie krok\n')
        self.plan('2026-10-02-s.md', '# S\nStatus: draft\n\n## Steps / tests\n- [x] one\n')
        self.plan('2026-10-03-p.md', '# P\nStatus: done\n\n## Kroki\n- [ ] stary\n\n## Progress\n- [x] 1.1 nowy\n')
        self.plan('2026-10-04-n.md', '# N\nStatus: done\n\n## Kroki\n1. bez checklisty\n')
        steps = {Path(p['path']).stem[-1]: p['steps'] for p in self.run_cli('status', self.root, '--json')['plans']}
        self.assertEqual(steps, {'k': {'total': 3, 'done': 2}, 's': {'total': 1, 'done': 1},
                                 'p': {'total': 1, 'done': 1}, 'n': None})

    def test_status_reads_runs_and_claims_and_is_empty_without_state(self):
        empty = self.run_cli('status', self.root, '--json')
        self.assertEqual((empty['plans'], empty['runs'], empty['claims'], empty['findings']), ([], [], [], []))
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.run_cli('checkpoint', self.root, '--run-id', 'r1', '--step', 'verify', '--status', 'NOT_RUN', '--next', 'review', '--json')
        self.run_cli('claim', self.root, '--task-id', 'T', '--run-id', 'a', '--action', 'acquire', '--run', '--json')
        report = self.run_cli('status', self.root, '--json')
        self.assertEqual((report['runs'][0]['id'], report['runs'][0]['state'], report['runs'][0]['lastStep']), ('r1', 'NOT_RUN', 'verify'))
        self.assertEqual((report['claims'][0]['task'], report['claims'][0]['owner'], report['claims'][0]['active']), ('T', 'a', True))

    def test_status_reports_unreadable_state_instead_of_failing(self):
        self.write('.skillsync/runs/bad.json', '{not json')
        report = self.run_cli('status', self.root, '--json')
        self.assertEqual(report['status'], 'PASS')
        self.assertTrue(any('bad' in finding for finding in report['findings']))
        self.assertEqual(self.run_cli('status', self.base / 'nowhere', '--json', code=2)['status'], 'BLOCKED')

    def status(self):
        return self.run_cli('status', self.root, '--json')

    def test_status_specs_list_fields_order_and_statuses(self):
        self.write('docs/specs/2026-11-01-a.md', '# Spec: Pierwszy\n\nStatus: draft\nPlan: docs/plans/2026-11-01-a.md\n')
        self.write('docs/specs/2026-11-02-b.md', '# Spec: Drugi\n\nStatus: superseded przez docs/specs/x.md\n')
        self.write('docs/specs/2026-11-03-c.md', 'bez tytułu\n\nStatus: gotowy\n')
        self.write('docs/specs/README.md', '# Indeks\nStatus: draft\n')
        self.write('docs/specs/.ukryty.md', '# Ukryty\nStatus: draft\n')
        self.write('docs/specs/notatka.txt', 'nie spec\n')
        report = self.status()
        self.assertEqual([row['path'] for row in report['specs']],
                         ['docs/specs/2026-11-03-c.md', 'docs/specs/2026-11-02-b.md', 'docs/specs/2026-11-01-a.md'])
        c, b, a = report['specs']
        self.assertEqual(sorted(a), ['path', 'plan', 'status', 'statusText', 'title', 'updated'])
        self.assertEqual((a['title'], a['status'], a['plan']), ('Spec: Pierwszy', 'draft', 'docs/plans/2026-11-01-a.md'))
        self.assertEqual((b['status'], b['statusText'], b['plan']), ('superseded', 'superseded przez docs/specs/x.md', None))
        self.assertEqual((c['title'], c['status'], c['statusText']), ('2026-11-03-c', 'unknown', 'gotowy'))
        self.assertTrue(a['updated'].endswith('Z'))
        self.assertEqual(report['findings'], [])

    def test_status_spec_plan_line_must_have_plan_path_shape(self):
        lines = {'a': 'Plan: <docs/plans/RRRR-MM-DD-slug.md, po utworzeniu>', 'b': 'Plan: docs/plans/README.md', 'c': 'Plan: ../x.md',
                 'd': 'Plan: docs/plans/sub/x.md', 'e': 'Plan: docs/plans/x.md tekst', 'f': 'Plan: docs/specs/x.md', 'g': 'bez linii',
                 'h': 'Plan: docs/plans/ok.md\nPlan: docs/plans/inny.md', 'i': 'Plan: `docs/plans/ok.md`'}
        for name, line in lines.items():
            self.write(f'docs/specs/{name}.md', f'# S\nStatus: draft\n{line}\n')
        plans = {Path(row['path']).stem: row['plan'] for row in self.status()['specs']}
        self.assertEqual({key for key, value in plans.items() if value}, {'h', 'i'})
        self.assertEqual((plans['h'], plans['i']), ('docs/plans/ok.md', 'docs/plans/ok.md'))

    def test_status_oversized_and_undecodable_spec_are_findings_without_row(self):
        self.write('docs/specs/ok.md', '# Ok\nStatus: draft\n')
        self.write('docs/specs/duzy.md', '# Duży\n' + 'x' * (512 * 1024 + 1))
        (self.root / 'docs/specs/zly.md').write_bytes(b'# Z\xff\n')
        report = self.status()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([row['path'] for row in report['specs']], ['docs/specs/ok.md'])
        self.assertTrue(any('duzy.md' in finding for finding in report['findings']), report['findings'])
        self.assertTrue(any('zly.md' in finding for finding in report['findings']), report['findings'])

    @needs_mode_enforcement
    def test_status_frds_list_only_names_without_reading_content(self):
        self.write('docs/frd/DEMO-2 Zmiana łącza.docx', 'x')
        self.write('docs/frd/DEMO-1.md', '# Tytuł z treści\nStatus: draft\n')
        self.write('docs/frd/README.md', '# Indeks\n')
        self.write('docs/frd/.gitkeep', '')
        self.write('docs/frd/sub/DEMO-3.md', 'w podkatalogu\n')
        closed = self.root / 'docs/frd/DEMO-4.pdf'
        closed.write_bytes(b'%PDF\xff')
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o600)
        report = self.status()
        self.assertEqual([row['name'] for row in report['frds']], ['DEMO-1.md', 'DEMO-2 Zmiana łącza.docx', 'DEMO-4.pdf'])
        row = report['frds'][1]
        self.assertEqual(sorted(row), ['name', 'path', 'updated'])
        self.assertEqual(row['path'], 'docs/frd/DEMO-2 Zmiana łącza.docx')
        self.assertNotIn('Tytuł z treści', json.dumps(report, ensure_ascii=False))
        self.assertEqual(report['findings'], [])

    @needs_os_symlinks
    def test_status_skips_symlinked_documents_without_findings(self):
        outside = self.base / 'poza.md'
        outside.write_text('# Poza\nStatus: draft\n', encoding='utf-8')
        self.write('docs/specs/ok.md', '# Ok\nStatus: draft\n')
        self.write('docs/frd/ok.md', 'x')
        (self.root / 'docs/specs/link.md').symlink_to(outside)
        (self.root / 'docs/frd/link.md').symlink_to(outside)
        report = self.status()
        self.assertEqual([row['path'] for row in report['specs']], ['docs/specs/ok.md'])
        self.assertEqual([row['path'] for row in report['frds']], ['docs/frd/ok.md'])
        self.assertEqual(report['findings'], [])

    @needs_os_symlinks
    def test_status_symlinked_document_directories_give_empty_lists(self):
        for name in ('specs', 'frd'):
            target = self.base / f'poza-{name}'
            target.mkdir()
            (target / 'x.md').write_text('# X\nStatus: draft\n', encoding='utf-8')
            (self.root / 'docs').mkdir(exist_ok=True)
            (self.root / 'docs' / name).symlink_to(target)
        report = self.status()
        self.assertEqual((report['specs'], report['frds'], report['findings']), ([], [], []))

    @needs_os_symlinks
    def test_status_symlinked_docs_directory_gives_empty_document_lists(self):
        target = self.base / 'poza-docs'
        (target / 'specs').mkdir(parents=True)
        (target / 'frd').mkdir()
        (target / 'specs/x.md').write_text('# X\nStatus: draft\n', encoding='utf-8')
        (target / 'frd/x.md').write_text('x', encoding='utf-8')
        (self.root / 'docs').symlink_to(target)
        report = self.status()
        self.assertEqual((report['specs'], report['frds']), ([], []))

    def test_status_document_lists_are_empty_without_directories(self):
        report = self.status()
        self.assertEqual((report['specs'], report['frds']), ([], []))

    def test_status_document_limit_gives_finding(self):
        (self.root / 'docs/specs').mkdir(parents=True)
        (self.root / 'docs/frd').mkdir()
        for index in range(201):
            (self.root / f'docs/specs/{index:03}.md').write_text('# S\nStatus: draft\n', encoding='utf-8')
            (self.root / f'docs/frd/{index:03}.md').write_text('x', encoding='utf-8')
        report = self.status()
        self.assertEqual((len(report['specs']), len(report['frds'])), (200, 200))
        self.assertEqual(report['specs'][0]['path'], 'docs/specs/200.md')
        self.assertEqual(report['frds'][0]['path'], 'docs/frd/000.md')
        self.assertEqual(sum('200' in finding for finding in report['findings']), 2, report['findings'])

    def test_status_bad_utf8_file_names_become_findings_and_keep_report(self):
        self.write('docs/specs/ok.md', '# Ok\nStatus: draft\n')
        self.write('docs/frd/ok.md', 'x')
        bad = b'\xff'.decode('utf-8', 'surrogateescape')
        try:
            (self.root / 'docs/specs' / f'{bad}.md').write_text('# Zły\nStatus: draft\n', encoding='utf-8')
            (self.root / 'docs/frd' / f'{bad}.docx').write_text('x', encoding='utf-8')
        except OSError:
            self.skipTest('system plików odrzuca bajty spoza UTF-8')
        report = self.status()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([row['path'] for row in report['specs']], ['docs/specs/ok.md'])
        self.assertEqual([row['path'] for row in report['frds']], ['docs/frd/ok.md'])
        self.assertEqual(len(report['findings']), 2, report['findings'])

    def test_status_plan_spec_field_has_path_or_null(self):
        lines = {'ok': 'Spec: docs/specs/x.md', 'ticks': 'Spec: `docs/specs/x.md`', 'readme': 'Spec: docs/specs/README.md', 'sub': 'Spec: docs/specs/a/x.md',
                 'outside': 'Spec: docs/x.md', 'parent': 'Spec: docs/specs/../x.md', 'words': 'Spec: docs/specs/x.md oraz y', 'two': 'Spec: docs/specs/x.md\nSpec: docs/specs/y.md',
                 'empty': 'Spec:', 'none': 'bez linii'}
        for name, line in lines.items():
            self.plan(f'{name}.md', f'# P\nStatus: draft\n{line}\n')
        specs = {Path(row['path']).stem: row['spec'] for row in self.status()['plans']}
        self.assertEqual({key for key, value in specs.items() if value}, {'ok', 'ticks'})
        self.assertEqual((specs['ok'], specs['ticks']), ('docs/specs/x.md', 'docs/specs/x.md'))

    @needs_mode_enforcement
    def test_status_unreadable_document_directory_is_a_finding_and_keeps_plans(self):
        for mode in (0o400, 0o300, 0):
            self.plan('a.md', '# A\nStatus: draft\n')
            self.write('docs/frd/x.md', 'x')
            self.write('docs/specs/x.md', '# X\nStatus: draft\n')
            for name in ('frd', 'specs'):
                (self.root / 'docs' / name).chmod(mode)
                self.addCleanup((self.root / 'docs' / name).chmod, 0o700)
            report = self.run_cli('status', self.root, '--json')
            self.assertEqual(report['status'], 'PASS', mode)
            self.assertEqual((report['specs'], report['frds']), ([], []), mode)
            self.assertEqual([Path(row['path']).name for row in report['plans']], ['a.md'], mode)
            self.assertEqual(len(report['findings']), 2, (mode, report['findings']))
            for name in ('frd', 'specs'):
                (self.root / 'docs' / name).chmod(0o700)

    def test_document_removed_during_listing_is_skipped_without_finding(self):
        from skillsync_workflow.status import _doc_files
        self.write('docs/frd/a.docx', 'x')
        self.write('docs/frd/~$znika.docx', 'x')
        real = Path.lstat

        def vanishing(path, *args, **kwargs):
            if path.name == '~$znika.docx':
                raise FileNotFoundError(path)
            return real(path, *args, **kwargs)

        findings = []
        with patch.object(Path, 'lstat', vanishing):
            rows = _doc_files(self.root, 'frd', findings)
        self.assertEqual(([path.name for path in rows], findings), (['a.docx'], []))

    def test_document_path_uses_forward_slashes_on_windows_paths(self):
        from pathlib import PureWindowsPath
        from skillsync_workflow.status import _doc_path
        self.assertEqual(_doc_path(PureWindowsPath('C:/repo'), PureWindowsPath('C:/repo/docs/specs/x.md')), 'docs/specs/x.md')


if __name__ == '__main__':
    unittest.main()
