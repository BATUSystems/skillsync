"""db_snapshot.py: stdout and stderr are UTF-8 whatever the console code page says (a redirected stream on Windows uses cp1250 or cp852).

A real child process: a faked `sqlcmd` fails the backup with a Polish message, the script prints it together with its own Polish line,
and the test reads the raw bytes. PYTHONIOENCODING forces the code page of the redirected streams the way Windows does by default.
Secrets are assembled at run time.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = os.path.join(str(ROOT), 'library', 'skills', 'test-env', 'scripts', 'db_snapshot.py')
POLISH_FROM_SERVER = 'Nie można otworzyć urządzenia kopii „zażółć gęślą jaźń”.'
POLISH_FROM_SCRIPT = 'stan bazy mógł się zmienić'

FAKE_SQLCMD = '''#!%(python)s
import platform, sys
args = sys.argv[1:]
sql = args[args.index('-Q') + 1] if '-Q' in args else ''
node = platform.node()


def table(columns, row):
    print('|'.join(columns))
    print('|'.join('-' * len(c) for c in columns))
    print('|'.join(row))
    print()
    print('(1 rows affected)')


if sql.startswith('SELECT @@SERVERNAME'):
    table(['server_name', 'machine_name', 'instance_name'], [node, node, 'NULL'])
elif sql.startswith('SELECT state_desc'):
    table(['state_desc', 'user_access_desc', 'recovery_model_desc'], ['ONLINE', 'MULTI_USER', 'SIMPLE'])
else:
    sys.stdout.buffer.write(('Msg 3201, Level 16, State 1, Server %%s, Line 1 %%s\\n' %% (node, %(message)r)).encode('utf-8'))
    sys.exit(1)
'''


@unittest.skipIf(os.name == 'nt', 'the faked sqlcmd is a shebang script')
class OutputEncodingTests(unittest.TestCase):
    def run_backup(self, code_page):
        tmp = tempfile.TemporaryDirectory(prefix='dbsnap-enc-')
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        bin_dir = os.path.join(root, 'bin')
        os.mkdir(bin_dir)
        fake = os.path.join(bin_dir, 'sqlcmd')
        with open(fake, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(FAKE_SQLCMD % {'python': sys.executable, 'message': POLISH_FROM_SERVER})
        os.chmod(fake, 0o755)
        env = {'PATH': bin_dir + os.pathsep + '/usr/bin' + os.pathsep + '/bin', 'HOME': root, 'LANG': 'C.UTF-8',
               'PYTHONIOENCODING': code_page, 'SQLCMDUSER': 'sa_test', 'SQLCMDPASSWORD': 'p' + 'w' + '-' + code_page}
        return subprocess.run([sys.executable, SCRIPT, 'backup', '--database', 'appdb', '--label', 'ab', '--dir', os.path.join(root, 'copies')],
                              env=env, cwd=root, capture_output=True, timeout=60)

    def check(self, code_page):
        done = self.run_backup(code_page)
        self.assertEqual(done.returncode, 1, done.stderr)
        for name, data in (('stderr', done.stderr), ('stdout', done.stdout)):
            try:
                data.decode('utf-8')
            except UnicodeDecodeError as error:
                self.fail('%s is not UTF-8 under %s: %s %r' % (name, code_page, error, data[:200]))
        text = (done.stderr + done.stdout).decode('utf-8')
        self.assertIn('zażółć gęślą jaźń', text)
        self.assertIn(POLISH_FROM_SCRIPT, text)

    def test_utf8_under_cp1250(self):
        self.check('cp1250')

    def test_utf8_under_cp852(self):
        self.check('cp852')

    def test_utf8_under_utf8(self):
        self.check('utf-8')


if __name__ == '__main__':
    unittest.main()
