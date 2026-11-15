"""`addresseesNode(question, viewer)` of the expert questions page `/_experts` (K8 of STATUS-160-A): who the question is addressed to,
by role of the viewer. The function is cut out of the page script and run in node with a tiny `el` shim."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402

NODE = shutil.which('node')


def function_source():
    script = ps.EXPERTS_SCRIPT if hasattr(ps, 'EXPERTS_SCRIPT') else None
    if script is None:   # the script constant has a different name: find the one that holds the function
        for name in dir(ps):
            value = getattr(ps, name)
            if isinstance(value, str) and 'function addresseesNode' in value:
                script = value
                break
    start = script.index('  function addresseesNode')
    end = script.index('  function refNode')
    return script[start:end]


class AddresseesNode(unittest.TestCase):
    def test_the_page_script_calls_it_from_the_question_card_and_keeps_the_viewer(self):
        source = '\n'.join(getattr(ps, name) for name in dir(ps) if isinstance(getattr(ps, name), str) and 'function addresseesNode' in getattr(ps, name))
        self.assertIn('addresseesNode(question, state.viewer)', source)
        self.assertIn('state.viewer = data.user;', source)

    @unittest.skipUnless(NODE, 'node >= 18 is not available (set SKILLSYNC_TEST_NODE)')
    def test_text_by_role(self):
        harness = ("var el = function (tag, props) { return { tag: tag, text: props.text, cls: props['class'] }; };\n" + function_source() + """
var cases = [
  [{ forAll: true }, { roles: ['expert'] }], [{ forAll: true }, { roles: ['developer'] }],
  [{ forAll: false, addressees: ['ewa.nowak', 'jan'] }, { roles: ['developer'] }], [{ forAll: false, addressees: ['ewa.nowak'] }, { roles: ['acceptor'] }],
  [{ forAll: false }, { roles: ['expert'] }], [{ forAll: false, addressees: ['ewa.nowak'] }, { roles: ['commenter'] }],
  [{ forAll: false }, { roles: ['commenter'] }], [{ forAll: false, addressees: ['a'] }, null]
];
process.stdout.write(JSON.stringify(cases.map(function (c) { var n = addresseesNode(c[0], c[1]); return n ? n.text : null; })));
""")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'h.js'
            path.write_text(harness, encoding='utf-8')
            proc = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30, cwd=tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        self.assertEqual(got, ['Do wszystkich ekspertów', 'Do wszystkich ekspertów', 'Do: ewa.nowak, jan', 'Do: ewa.nowak', 'Do Ciebie',
                               'Do wskazanych ekspertów', None, 'Do wskazanych ekspertów'])
        self.assertNotIn('ewa.nowak', ' '.join(t for t in got[5:7] if t), 'a view without the privileged role never shows the logins')


if __name__ == '__main__':
    unittest.main()
