import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skillsync_workflow import test_reports as tr

FIX = Path(__file__).parent / 'fixtures' / 'test-runs'


def fixture(name):
    """Fixture text; the recorded files have anonymized placeholders (`<host>`, `<root>`) that are not valid XML, so they are restored."""
    return (FIX / name).read_text(encoding='utf-8-sig').replace('<host>', 'host').replace('<root>', 'root')


class JunitTests(unittest.TestCase):
    def test_node_fixture(self):
        res = tr.parse_junit(fixture('node-junit.xml'))
        self.assertEqual([r['name'] for r in res], ['ok', 'boom', 'skip'])
        self.assertEqual([r['state'] for r in res], ['passed', 'failed', 'skipped'])
        self.assertEqual(res[0]['id'], 'test::ok')
        self.assertEqual(res[0]['ms'], 1)
        self.assertIn('1 == 2', res[1]['msg'])
        self.assertEqual(res[2]['msg'], 'x')

    def test_nested_suites_error_and_time(self):
        xml = ('<testsuites><testsuite name="a"><testsuite name="b"><testcase classname="C" name="t1" time="1.5"/>'
               '<testcase name="t2"><error message="bad">trace</error></testcase></testsuite></testsuite></testsuites>')
        res = tr.parse_junit(xml)
        self.assertEqual(res[0], {'id': 'C::t1', 'name': 't1', 'group': 'C', 'state': 'passed', 'ms': 1500, 'msg': None})
        self.assertEqual((res[1]['id'], res[1]['group'], res[1]['state'], res[1]['ms']), ('t2', None, 'failed', None))
        self.assertEqual(res[1]['msg'], 'bad\ntrace')

    def test_failure_attribute_only_and_bytes_input(self):
        res = tr.parse_junit(b'<testsuite><testcase name="x" failure="nope"/></testsuite>')
        self.assertEqual((res[0]['state'], res[0]['msg']), ('failed', 'nope'))

    def test_utf8_polish(self):
        xml = '<?xml version="1.0" encoding="utf-8"?><testsuite><testcase classname="Zażółć" name="gęślą jaźń"><skipped message="powód"/></testcase></testsuite>'
        res = tr.parse_junit(xml.encode('utf-8'))
        self.assertEqual((res[0]['id'], res[0]['msg']), ('Zażółć::gęślą jaźń', 'powód'))

    def test_utf16_bom_and_doctype_in_utf16(self):
        ok = '<testsuite><testcase name="a"/></testsuite>'
        self.assertEqual(len(tr.parse_junit(ok.encode('utf-16'))), 1)
        with self.assertRaises(ValueError):
            tr.parse_junit(('<!DOCTYPE x>' + ok).encode('utf-16'))

    def test_truncated_pytest_fixture(self):
        with self.assertRaises(ValueError):
            tr.parse_junit(FIX / 'pytest-junit.xml')

    def test_pytest_fixture_completed(self):
        text = fixture('pytest-junit.xml')
        text = text[text.index('<?xml'):]  # the recording starts with pytest's console progress line
        head = text[:text.rindex('<testcase')]
        res = tr.parse_junit(head + '</testsuite></testsuites>')
        self.assertTrue(res)
        self.assertIn('failed', {r['state'] for r in res})

    def test_empty_and_malformed(self):
        for data in (b'', b'   ', b'<testsuite><testcase', b'not xml'):
            with self.assertRaises(ValueError):
                tr.parse_junit(data)

    def test_nul_and_illegal_chars_do_not_crash(self):
        res = tr.parse_junit('<testsuite><testcase name="a\x00b\x01"/></testsuite>')
        self.assertEqual(res[0]['name'], 'ab')

    def test_error_message_hides_content(self):
        with self.assertRaises(ValueError) as cm:
            tr.parse_junit('<secret-token-123>')
        self.assertNotIn('secret-token-123', str(cm.exception))

    def test_limit(self):
        xml = '<testsuite>' + '<testcase name="t"/>' * 5 + '</testsuite>'
        with mock.patch.object(tr, 'MAX_TESTS', 3):
            res, info = tr.parse_junit(xml, with_info=True)
            self.assertEqual((len(res), info), (3, {'truncated': True}))
        res, info = tr.parse_junit(xml, with_info=True)
        self.assertEqual((len(res), info), (5, {'truncated': False}))


class SecurityTests(unittest.TestCase):
    def test_billion_laughs_and_xxe_rejected(self):
        laughs = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
                  '<testsuite><testcase name="&lol2;"/></testsuite>')
        xxe = '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><testsuite><testcase name="&xxe;"/></testsuite>'
        for fn in (tr.parse_junit, tr.parse_trx):
            for doc in (laughs, xxe, laughs.lower(), xxe.replace('DOCTYPE', 'DocType'), '<!ENTITY a "b"><testsuite/>'):
                with self.assertRaises(ValueError) as cm:
                    fn(doc)
                self.assertNotIn('passwd', str(cm.exception))

    def test_too_large(self):
        with mock.patch.object(tr, 'MAX_BYTES', 50):
            with self.assertRaises(ValueError):
                tr.parse_junit('<testsuite>' + '<testcase name="t"/>' * 10 + '</testsuite>')
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'big.xml'
                p.write_text('<testsuite>' + ' ' * 100 + '</testsuite>')
                with self.assertRaises(ValueError):
                    tr.parse_any(p)


class TrxTests(unittest.TestCase):
    def test_dotnet_fixture(self):
        res = {r['name']: r for r in tr.parse_trx(fixture('dotnet.trx'))}
        self.assertEqual(len(res), 4)
        self.assertEqual(res['demo.UnitTest1.Ok']['state'], 'passed')
        self.assertEqual(res['demo.UnitTest1.Boom']['state'], 'failed')
        self.assertEqual(res['demo.UnitTest1.Param(x: 1)']['state'], 'passed')
        self.assertEqual(res['demo.UnitTest1.Param(x: 2)']['state'], 'failed')
        self.assertIn('Values differ', res['demo.UnitTest1.Boom']['msg'])
        self.assertNotIn('at demo', res['demo.UnitTest1.Boom']['msg'])
        self.assertEqual(res['demo.UnitTest1.Ok']['group'], 'demo.UnitTest1')
        self.assertEqual(res['demo.UnitTest1.Ok']['id'], 'demo.UnitTest1::demo.UnitTest1.Ok')
        self.assertEqual(res['demo.UnitTest1.Boom']['ms'], 10)

    def test_outcomes_without_definitions(self):
        rows = ''.join(f'<UnitTestResult testId="{i}" testName="t{i}" outcome="{o}" duration="00:00:01.5000000"/>'
                       for i, o in enumerate(['Passed', 'Failed', 'NotExecuted', 'Skipped', 'Inconclusive', 'Timeout', 'Aborted']))
        res = tr.parse_trx(f'<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010"><Results>{rows}</Results></TestRun>')
        self.assertEqual([r['state'] for r in res], ['passed', 'failed', 'skipped', 'skipped', 'failed', 'failed', 'failed'])
        self.assertTrue(all(r['group'] is None and r['ms'] == 1500 for r in res))

    def test_limit(self):
        rows = '<UnitTestResult testId="1" testName="t" outcome="Passed"/>' * 4
        with mock.patch.object(tr, 'MAX_TESTS', 2):
            res, info = tr.parse_trx(f'<TestRun><Results>{rows}</Results></TestRun>', with_info=True)
        self.assertEqual((len(res), info['truncated']), (2, True))


class ParseAnyTests(unittest.TestCase):
    def test_detects_both(self):
        with tempfile.TemporaryDirectory() as d:
            for name, count in (('dotnet.trx', 4), ('node-junit.xml', 3)):
                (Path(d) / name).write_text(fixture(name), encoding='utf-8')
                self.assertEqual(len(tr.parse_any(Path(d) / name)), count)
                self.assertEqual(len(tr.parse_any(str(Path(d) / name))), count)

    def test_unknown_root_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.xml'
            p.write_text('<html/>')
            with self.assertRaises(ValueError):
                tr.parse_any(p)
            with self.assertRaises(ValueError):
                tr.parse_any(Path(d) / 'missing.xml')


if __name__ == '__main__':
    unittest.main()
