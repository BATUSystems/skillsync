"""CORE-1170 B-4: independent tests of `feature-explainer/scripts/check_page.py` (K7: PASS on the model page, FAIL with a specific code per defect)."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmp_paths import child  # noqa: E402

SKILL = ROOT / 'library' / 'skills' / 'feature-explainer'
SCRIPT = SKILL / 'scripts' / 'check_page.py'
TEMPLATE = SKILL / 'assets' / 'template.html'
# a minimal valid PNG header followed by padding: the checker only tests that the file exists
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
ALL_CODES = ['data-uri', 'missing-image', 'external-resource', 'missing-alt', 'external-script', 'no-viewport', 'toc-anchor', 'themes', 'sections']


def replace_once(text, old, new):
    assert text.count(old) == 1, 'template changed: %r occurs %d times' % (old, text.count(old))
    return text.replace(old, new)


def add_body(text, snippet):
    return replace_once(text, '</body>', snippet + '\n</body>')


class CheckPage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.template = TEMPLATE.read_text(encoding='utf-8')
        child(self.dir, 'ok.png').write_bytes(PNG)

    def run_check(self, page, *flags):
        proc = subprocess.run([sys.executable, str(SCRIPT), *flags, str(page)], capture_output=True, text=True, timeout=60)
        return proc

    def page(self, html, name='page.html'):
        path = child(self.dir, name)
        path.write_text(html, encoding='utf-8')
        return path

    def codes(self, html):
        proc = self.run_check(self.page(html), '--json')
        data = json.loads(proc.stdout)
        return proc.returncode, data, sorted({f['code'] for f in data['findings']})

    def assertOnly(self, html, code):
        rc, data, codes = self.codes(html)
        self.assertEqual(rc, 1, proc_msg(data))
        self.assertEqual(data['result'], 'FAIL')
        self.assertEqual(codes, [code], proc_msg(data))

    # (1) model page
    def test_template_passes(self):
        proc = self.run_check(TEMPLATE)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('RESULT: PASS', proc.stdout)
        self.assertNotIn('FAIL ', proc.stdout)

    # (2) one isolated defect per code
    def test_data_uri(self):
        self.assertOnly(add_body(self.template, '<img src="data:image/png;base64,iVBORw0KGgo=" alt="x">'), 'data-uri')

    def test_data_uri_in_css_url(self):
        html = add_body(self.template, '<div style="background:url(data:image/png;base64,iVBORw0KGgo=)"></div>')
        self.assertOnly(html, 'data-uri')

    def test_missing_image(self):
        self.assertOnly(add_body(self.template, '<img src="brak.png" alt="x">'), 'missing-image')

    def test_external_resource(self):
        self.assertOnly(add_body(self.template, '<img src="https://example.com/a.png" alt="x">'), 'external-resource')

    def test_missing_alt(self):
        self.assertOnly(add_body(self.template, '<img src="ok.png">'), 'missing-alt')

    def test_external_script(self):
        self.assertOnly(add_body(self.template, '<script src="app.js"></script>'), 'external-script')

    def test_no_viewport(self):
        html = re.sub(r'<meta name="viewport"[^>]*>', '', self.template)
        self.assertNotEqual(html, self.template)
        self.assertOnly(html, 'no-viewport')

    def test_toc_anchor(self):
        self.assertOnly(add_body(self.template, '<a href="#nie-istnieje">x</a>'), 'toc-anchor')

    def test_themes(self):
        html = replace_once(self.template, 'prefers-color-scheme: dark', 'prefers-color-scheme: light')
        self.assertOnly(html, 'themes')

    def test_sections(self):
        # the page header with an id is one of the nine parts and nothing links to it
        self.assertEqual(self.template.count('id="naglowek"'), 1)
        self.assertNotIn('#naglowek', self.template)
        self.assertOnly(replace_once(self.template, '<header id="naglowek">', '<header>'), 'sections')

    def test_external_stylesheet_link(self):
        self.assertOnly(add_body(self.template, '<link rel="stylesheet" href="https://example.com/a.css">'), 'external-script')

    def test_css_import_url(self):
        rc, data, codes = self.codes(add_body(self.template, '<style>@import url(https://example.com/a.css);</style>'))
        self.assertEqual(rc, 1)
        self.assertIn('external-script', codes)

    def test_font_face_external_url(self):
        html = add_body(self.template, "<style>@font-face { font-family: X; src: url(https://example.com/x.woff2); }</style>")
        rc, data, codes = self.codes(html)
        self.assertEqual(rc, 1)
        self.assertIn('external-resource', codes)

    def test_srcset_missing_image(self):
        self.assertOnly(add_body(self.template, '<img src="ok.png" srcset="ok.png 1x, brak2.png 2x" alt="x">'), 'missing-image')

    def test_source_missing_image(self):
        html = add_body(self.template, '<picture><source srcset="brak3.png"><img src="ok.png" alt="x"></picture>')
        self.assertOnly(html, 'missing-image')

    def test_absolute_path_image(self):
        rc, data, codes = self.codes(add_body(self.template, '<img src="/img/x.png" alt="x">'))
        self.assertEqual((rc, codes), (1, ['missing-image']))
        self.assertIn('absolute path; use a relative path next to the page', data['findings'][0]['message'])

    def test_external_embeds(self):
        for snippet in ('<iframe src="https://example.com/"></iframe>', '<video src="//example.com/v.mp4"></video>',
                        '<object data="http://example.com/o.swf"></object>', '<embed src="https://example.com/e">'):
            with self.subTest(snippet=snippet):
                self.assertOnly(add_body(self.template, snippet), 'external-resource')

    def test_each_defect_has_a_distinct_failing_line_in_text_output(self):
        page = self.page(add_body(self.template, '<img src="brak.png" alt="x">'))
        proc = self.run_check(page)
        self.assertEqual(proc.returncode, 1)
        self.assertRegex(proc.stdout, r'(?m)^FAIL missing-image: ')
        self.assertTrue(proc.stdout.rstrip().endswith('RESULT: FAIL'))

    # (3) alt rules
    def test_image_with_alt_and_existing_file_passes(self):
        rc, data, codes = self.codes(add_body(self.template, '<img src="ok.png" alt="opis">'))
        self.assertEqual((rc, data['result'], codes), (0, 'PASS', []))

    def test_empty_alt_with_role_presentation_passes(self):
        rc, data, codes = self.codes(add_body(self.template, '<img src="ok.png" alt="" role="presentation">'))
        self.assertEqual((rc, data['result'], codes), (0, 'PASS', []))

    def test_empty_alt_without_role_is_missing_alt(self):
        self.assertOnly(add_body(self.template, '<img src="ok.png" alt="">'), 'missing-alt')

    # (4) usage error
    def test_missing_file_is_usage_error(self):
        proc = self.run_check(child(self.dir, 'nie-ma.html'))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn('RESULT: PASS', proc.stdout)

    def test_no_argument_is_usage_error(self):
        proc = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)

    # (5) JSON contract
    def test_json_pass_shape(self):
        proc = self.run_check(TEMPLATE, '--json')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {'result': 'PASS', 'findings': []})

    def test_json_fail_shape(self):
        proc = self.run_check(self.page(add_body(self.template, '<img src="brak.png" alt="x">')), '--json')
        self.assertEqual(proc.returncode, 1)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data), {'result', 'findings'})
        self.assertEqual(data['result'], 'FAIL')
        self.assertEqual([f['code'] for f in data['findings']], ['missing-image'])
        self.assertTrue(data['findings'][0]['message'])

    def test_several_defects_are_all_reported(self):
        html = add_body(self.template, '<img src="brak.png"><script src="a.js"></script>')
        rc, data, codes = self.codes(html)
        self.assertEqual(rc, 1)
        self.assertEqual(codes, ['external-script', 'missing-alt', 'missing-image'])
        self.assertTrue(set(codes) <= set(ALL_CODES))


def proc_msg(data):
    return json.dumps(data, ensure_ascii=False)


if __name__ == '__main__':
    unittest.main()
