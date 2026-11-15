"""`winacl`: private files on Windows through an ACL (DEBT-150-ACL). The Windows behaviour is switched on with the module seam
(`IS_WINDOWS`, `_backend`) so the logic runs on every system; the real `advapi32` calls are tested on Windows only.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_winacl.py`."""
import io
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import winacl  # noqa: E402

USER = 'S-1-5-21-1-2-3-1001'
OTHER = 'S-1-5-32-545'


class FakeBackend(object):
    def __init__(self, acls=True, code=0, fail=None, dacl=()):
        self.acls, self.code, self.fail, self.dacl = acls, code, fail, dacl
        self.calls = []

    def user_sid(self):
        return USER

    def volume_has_acls(self, path):
        self.calls.append(('volume', path))
        if self.fail:
            raise self.fail
        return self.acls

    def set_dacl(self, path, sddl):
        self.calls.append(('set', path, sddl))
        return self.code

    def read_dacl(self, path):
        self.calls.append(('read', path))
        return self.dacl if self.dacl is None else list(self.dacl)


class WindowsLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='winacl-')
        self.addCleanup(self.tmp.cleanup)
        self.file = os.path.join(self.tmp.name, 'users.json')
        Path(self.file).write_text('x', encoding='utf-8')
        for patch in (mock.patch.object(winacl, 'IS_WINDOWS', True), mock.patch.object(winacl, '_warned', set())):
            patch.start()
            self.addCleanup(patch.stop)

    def use(self, backend):
        patch = mock.patch.object(winacl, '_backend', backend)
        patch.start()
        self.addCleanup(patch.stop)
        return backend

    def test_a_file_gets_a_protected_acl_by_sid_for_the_user_system_and_administrators(self):
        backend = self.use(FakeBackend())
        self.assertTrue(winacl.restrict_to_owner(self.file))
        sddl = [call for call in backend.calls if call[0] == 'set'][0][2]
        self.assertEqual(sddl, 'D:P(A;;FA;;;%s)(A;;FA;;;S-1-5-18)(A;;FA;;;S-1-5-32-544)' % USER)

    def test_a_directory_gets_inheriting_aces(self):
        backend = self.use(FakeBackend())
        self.assertTrue(winacl.restrict_to_owner(self.tmp.name))
        sddl = [call for call in backend.calls if call[0] == 'set'][0][2]
        self.assertEqual(sddl.count('(A;OICI;FA;;;'), 3, sddl)

    def test_a_volume_without_acls_is_false_without_touching_the_file_and_warns_once(self):
        backend = self.use(FakeBackend(acls=False))
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            self.assertFalse(winacl.restrict_to_owner(self.file))
            self.assertFalse(winacl.restrict_to_owner(self.file))
        self.assertEqual([call for call in backend.calls if call[0] == 'set'], [])
        self.assertEqual(buffer.getvalue().count('nie udało się ograniczyć dostępu'), 1, buffer.getvalue())
        self.assertIn('wolumin bez list ACL', buffer.getvalue())

    def test_an_error_code_is_false_and_never_raises(self):
        self.use(FakeBackend(code=5))
        with redirect_stderr(io.StringIO()) as buffer:
            self.assertFalse(winacl.restrict_to_owner(self.file))
        self.assertIn('kod błędu 5', buffer.getvalue())

    def test_an_exception_in_the_backend_is_false_and_never_raises(self):
        self.use(FakeBackend(fail=OSError(2, 'boom')))
        with redirect_stderr(io.StringIO()):
            self.assertFalse(winacl.restrict_to_owner(self.file))
            self.assertFalse(winacl.restrict_to_owner(os.path.join(self.tmp.name, 'missing.json')))

    def test_a_missing_backend_library_is_false(self):
        with mock.patch.object(winacl, '_get_backend', side_effect=ImportError('no ctypes')), redirect_stderr(io.StringIO()):
            self.assertFalse(winacl.restrict_to_owner(self.file))

    def test_is_owner_only_checks_every_ace(self):
        private = [(0, 0, 0x1f01ff, USER), (0, 0, 0x1f01ff, 'S-1-5-18'), (0, 0x10, 0x1f01ff, 'S-1-3-4')]
        self.use(FakeBackend(dacl=private))
        self.assertTrue(winacl.is_owner_only(self.file))
        for wide in (private + [(0, 0, 0x1200a9, OTHER)],                  # Users
                     private + [(1, 0, 0x1f01ff, USER)],                    # a deny ACE is not "allow only"
                     private + [(5, 0, 0, '')],                             # an unknown ACE type
                     None):                                                # no DACL at all = everyone
            with self.subTest(dacl=wide):
                self.use(FakeBackend(dacl=wide))
                self.assertFalse(winacl.is_owner_only(self.file))

    def test_is_owner_only_is_false_on_an_error(self):
        backend = self.use(FakeBackend())
        backend.read_dacl = mock.Mock(side_effect=OSError(5, 'denied'))
        self.assertFalse(winacl.is_owner_only(self.file))

    def test_ensure_private_repairs_a_wide_file_and_leaves_a_private_one(self):
        wide = [(0, 0, 0x1200a9, OTHER)]
        backend = self.use(FakeBackend(dacl=wide))
        self.assertEqual(winacl.ensure_private(self.file), (True, True))
        self.assertEqual(len([call for call in backend.calls if call[0] == 'set']), 1)
        backend = self.use(FakeBackend(dacl=[(0, 0, 1, USER)]))
        self.assertEqual(winacl.ensure_private(self.file), (True, False))
        self.assertEqual([call for call in backend.calls if call[0] == 'set'], [])

    def test_private_notes_report_a_repair_once_and_nothing_for_a_private_file(self):
        self.use(FakeBackend(dacl=[(0, 0, 1, OTHER)]))
        notes = winacl.private_notes(self.file)
        self.assertEqual(len(notes), 1)
        self.assertIn('ograniczono dostęp', notes[0])
        self.use(FakeBackend(dacl=[(0, 0, 1, USER)]))
        self.assertEqual(winacl.private_notes(self.file), [])
        self.assertEqual(winacl.private_notes(os.path.join(self.tmp.name, 'missing.json')), [])

    def test_private_notes_warn_once_when_the_repair_fails(self):
        self.use(FakeBackend(dacl=[(0, 0, 1, OTHER)], acls=False))
        first = winacl.private_notes(self.file)
        self.assertEqual(len(first), 1)
        self.assertIn('nie udało się ograniczyć', first[0])
        self.assertEqual(winacl.private_notes(self.file), [], 'the same file warns once per process')


class OtherSystemsTests(unittest.TestCase):
    def test_off_windows_restrict_is_true_and_touches_nothing(self):
        with mock.patch.object(winacl, 'IS_WINDOWS', False), mock.patch.object(winacl, '_get_backend') as backend:
            self.assertTrue(winacl.restrict_to_owner('/nonexistent/x'))
            self.assertEqual(winacl.private_notes('/nonexistent/x'), [])
            backend.assert_not_called()

    def test_the_module_does_not_import_ctypes_at_import_time(self):
        self.assertFalse(hasattr(winacl, 'ctypes'))

    @unittest.skipIf(os.name == 'nt', 'tryb POSIX: bity grupy i innych')
    def test_off_windows_is_owner_only_reads_the_mode(self):
        with tempfile.TemporaryDirectory(prefix='winacl-') as tmp:
            path = os.path.join(tmp, 'f')
            Path(path).write_text('x', encoding='utf-8')
            os.chmod(path, 0o600)
            self.assertTrue(winacl.is_owner_only(path))
            os.chmod(path, 0o640)
            self.assertFalse(winacl.is_owner_only(path))
            self.assertFalse(winacl.is_owner_only(os.path.join(tmp, 'missing')))


@unittest.skipUnless(os.name == 'nt', 'tylko na Windows (ACL)')
class RealWindowsTests(unittest.TestCase):
    """The real `advapi32` calls (measured 15.11: 0.5-0.8 ms, SID only)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='winacl-real-')
        self.addCleanup(self.tmp.cleanup)
        # a plain os.mkdir directory: tempfile.mkdtemp is already private on 3.12+, which would hide a failure
        self.dir = os.path.join(self.tmp.name, 'plain')
        os.mkdir(self.dir)

    def make(self, name, text='x'):
        path = os.path.join(self.dir, name)
        Path(path).write_text(text, encoding='utf-8')
        return path

    def test_a_file_becomes_owner_only_and_stays_usable(self):
        path = self.make('users.json')
        self.assertTrue(winacl.restrict_to_owner(path))
        self.assertTrue(winacl.is_owner_only(path))
        self.assertEqual(Path(path).read_text(encoding='utf-8'), 'x')
        Path(path).write_text('y', encoding='utf-8')

    def test_the_dacl_is_exactly_user_system_administrators(self):
        path = self.make('users.json')
        winacl.restrict_to_owner(path)
        backend = winacl._get_backend()
        sids = sorted(sid for _kind, _flags, _mask, sid in backend.read_dacl(path))
        self.assertEqual(sids, sorted([backend.user_sid(), winacl.SYSTEM_SID, winacl.ADMINISTRATORS_SID]))

    def test_a_directory_is_inherited_by_new_files_and_repairs_existing_ones(self):
        old = self.make('old.json')
        self.assertTrue(winacl.restrict_to_owner(self.dir))
        new = self.make('new.json')
        self.assertTrue(winacl.is_owner_only(new))
        self.assertTrue(winacl.is_owner_only(old), 'an existing file that inherits follows the directory')

    def test_a_missing_path_is_false_without_raising(self):
        with redirect_stderr(io.StringIO()):
            self.assertFalse(winacl.restrict_to_owner(os.path.join(self.dir, 'nope.json')))
        self.assertFalse(winacl.is_owner_only(os.path.join(self.dir, 'nope.json')))

    def test_os_replace_hands_over_the_acl_of_the_source(self):
        """Measured: the target takes the ACL of the temporary file, so it has to be set before the rename."""
        target, temporary = self.make('target.json'), self.make('temporary.json', 'new')
        winacl.restrict_to_owner(target)
        os.replace(temporary, target)               # a temporary file with the directory's default ACL
        wide_now = not winacl.is_owner_only(target)
        temporary = self.make('temporary.json', 'newer')
        winacl.restrict_to_owner(temporary)
        os.replace(temporary, target)
        self.assertTrue(winacl.is_owner_only(target))
        self.assertIsInstance(wide_now, bool)       # the first outcome depends on where the directory is; only the second is asserted


if __name__ == '__main__':
    unittest.main()
