"""Private files on Windows through an ACL (DEBT-150-ACL).

POSIX keeps a private file private with the mode 0600; on Windows the mode means nothing, so the access control list does
that job: only the user of the process, `SYSTEM` and the local administrators (by SID, never by name: the names are
localized). The helper is stand-alone (standard library; `ctypes` is imported only on Windows and only when it is first
needed) and never raises: every error is `False` and, once per process and path, a warning.

    restrict_to_owner(path) -> bool    file or directory; a directory is restricted with inheritance (new files and the
                                       existing files that inherit from it follow); other systems: True, nothing done
    is_owner_only(path) -> bool        POSIX: no group/other bits; Windows: every ACE allows only the SIDs above
    ensure_private(path) -> (ok, repaired)
    private_notes(path) -> [str]       what a command reports about a private file (a repair, or a warning), [] when fine
    warn_once(path, reason) -> str|None

Measured on Windows (3.11, 3.12, 3.13): `os.replace` hands the ACL of the SOURCE file to the target, so the ACL of a
temporary file has to be set before it is renamed over the real one (see `prototype_server.atomic_write_bytes`).
"""
import os
import stat
import sys
import threading

# Test seams: the tests switch these instead of `os.name` (pathlib breaks under a patched `os.name`).
IS_WINDOWS = os.name == 'nt'
_backend = None            # an object with `user_sid`, `volume_has_acls`, `set_dacl`, `read_dacl` (the real one is built lazily)

SYSTEM_SID = 'S-1-5-18'
ADMINISTRATORS_SID = 'S-1-5-32-544'
# read-only ACEs that restrict nobody but the owner (inherited by default in a user profile)
_NEUTRAL_SIDS = frozenset(('S-1-3-4', 'S-1-3-0'))        # OWNER RIGHTS, CREATOR OWNER

_lock = threading.Lock()
_warned = set()


def _stderr(text):
    try:
        sys.stderr.write(text + '\n')
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def warn_once(path, reason, emit=True):
    """The warning text for `path` the first time in this process, else None; printed to stderr when `emit`."""
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _lock:
        if key in _warned:
            return None
        _warned.add(key)
    text = ('nie udało się ograniczyć dostępu do pliku %s (ACL Windows: %s); plik może być czytelny dla innych '
            'użytkowników tego komputera' % (path, reason))
    if emit:
        _stderr('ostrzeżenie: ' + text)
    return text


def _sddl(sid, is_dir):
    inherit = 'OICI' if is_dir else ''
    return 'D:P' + ''.join('(A;%s;FA;;;%s)' % (inherit, who) for who in (sid, SYSTEM_SID, ADMINISTRATORS_SID))


def _get_backend():
    global _backend
    if _backend is None:
        _backend = _Win32()
    return _backend


def restrict_to_owner(path, emit=True):
    """ACL of `path`: the user, SYSTEM and the administrators only, protected (no inheritance from the parent). A directory
    gets inheriting ACEs. Returns True on success (and off Windows, where nothing is done), False on any failure."""
    if not IS_WINDOWS:
        return True
    return _restrict(path, emit)[0]


def _restrict(path, emit):
    """`(ok, note)`: `note` is the warning text the first time a failure is seen, else None."""
    reason = 'nieznany błąd'
    try:
        path = os.path.abspath(os.fspath(path))
        backend = _get_backend()
        if not backend.volume_has_acls(path):
            reason = 'wolumin bez list ACL (FAT/exFAT lub udział bez obsługi)'
        else:
            code = backend.set_dacl(path, _sddl(backend.user_sid(), os.path.isdir(path)))
            if code == 0:
                return True, None
            reason = 'kod błędu %s' % code
    except Exception as error:   # never raises: a failed ACL must not fail the write
        reason = '%s: %s' % (type(error).__name__, error)
    return False, warn_once(path, reason, emit)


def is_owner_only(path):
    """POSIX: no group/other bits. Windows: the DACL exists and every ACE allows only the user, SYSTEM, the administrators
    (or an owner placeholder); a missing file or any error is False."""
    try:
        if not IS_WINDOWS:
            return stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0
        backend = _get_backend()
        aces = backend.read_dacl(os.path.abspath(os.fspath(path)))
        if aces is None:     # no DACL: everyone has full access
            return False
        allowed = {backend.user_sid(), SYSTEM_SID, ADMINISTRATORS_SID} | _NEUTRAL_SIDS
        return all(kind == 0 and sid in allowed for kind, _flags, _mask, sid in aces)
    except Exception:
        return False


def ensure_private(path, emit=False):
    """`(ok, repaired)`: private already -> (True, False); else a repair is tried -> (result, result)."""
    if not IS_WINDOWS:
        return is_owner_only(path), False
    if is_owner_only(path):
        return True, False
    ok, _note = _restrict(path, emit)
    return ok, ok


def private_notes(path):
    """Lines a command adds to its `warnings` for a private file: a note about a repair, or the warning of a failed ACL
    (once per process and path). Nothing off Windows, nothing for a file that is private already."""
    if not IS_WINDOWS or not os.path.lexists(path):
        return []
    if is_owner_only(path):
        return []
    ok, note = _restrict(path, False)
    if ok:
        return ['ACL Windows: ograniczono dostęp do pliku %s (tylko użytkownik, SYSTEM i administratorzy)' % path]
    return [note] if note else []


class _Win32(object):
    """The real backend: `advapi32`/`kernel32` through `ctypes` (imported here, i.e. only on Windows)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        adv = ctypes.WinDLL('advapi32', use_last_error=True)
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        void_p = ctypes.c_void_p
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            w.LPCWSTR, w.DWORD, ctypes.POINTER(void_p), ctypes.POINTER(w.DWORD)]
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
        adv.GetSecurityDescriptorDacl.argtypes = [
            void_p, ctypes.POINTER(w.BOOL), ctypes.POINTER(void_p), ctypes.POINTER(w.BOOL)]
        adv.GetSecurityDescriptorDacl.restype = w.BOOL
        adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, w.DWORD, w.DWORD, void_p, void_p, void_p, void_p]
        adv.SetNamedSecurityInfoW.restype = w.DWORD
        adv.GetNamedSecurityInfoW.argtypes = [
            w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(void_p), ctypes.POINTER(void_p), ctypes.POINTER(void_p),
            ctypes.POINTER(void_p), ctypes.POINTER(void_p)]
        adv.GetNamedSecurityInfoW.restype = w.DWORD
        adv.GetAclInformation.argtypes = [void_p, void_p, w.DWORD, w.DWORD]
        adv.GetAclInformation.restype = w.BOOL
        adv.GetAce.argtypes = [void_p, w.DWORD, ctypes.POINTER(void_p)]
        adv.GetAce.restype = w.BOOL
        adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
        adv.OpenProcessToken.restype = w.BOOL
        adv.GetTokenInformation.argtypes = [w.HANDLE, w.DWORD, void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
        adv.GetTokenInformation.restype = w.BOOL
        adv.ConvertSidToStringSidW.argtypes = [void_p, ctypes.POINTER(w.LPWSTR)]
        adv.ConvertSidToStringSidW.restype = w.BOOL
        k32.GetCurrentProcess.restype = w.HANDLE
        k32.CloseHandle.argtypes = [w.HANDLE]
        k32.LocalFree.argtypes = [void_p]
        k32.LocalFree.restype = void_p
        k32.GetVolumePathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        k32.GetVolumePathNameW.restype = w.BOOL
        k32.GetVolumeInformationW.argtypes = [
            w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD),
            w.LPWSTR, w.DWORD]
        k32.GetVolumeInformationW.restype = w.BOOL
        self.adv, self.k32 = adv, k32
        self._sid = None

    def _sid_text(self, sid_pointer):
        ctypes, w = self.ctypes, self.w
        text = w.LPWSTR()
        if not self.adv.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise OSError(ctypes.get_last_error(), 'ConvertSidToStringSidW')
        try:
            return text.value
        finally:
            self.k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def user_sid(self):
        """SID of the user of the process (`TokenUser`), cached."""
        if self._sid is None:
            ctypes, w = self.ctypes, self.w
            token = w.HANDLE()
            if not self.adv.OpenProcessToken(self.k32.GetCurrentProcess(), 0x8, ctypes.byref(token)):   # TOKEN_QUERY
                raise OSError(ctypes.get_last_error(), 'OpenProcessToken')
            try:
                need = w.DWORD()
                self.adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))                       # TokenUser
                buffer = ctypes.create_string_buffer(need.value)
                if not self.adv.GetTokenInformation(token, 1, buffer, need, ctypes.byref(need)):
                    raise OSError(ctypes.get_last_error(), 'GetTokenInformation')
                self._sid = self._sid_text(ctypes.c_void_p.from_buffer(buffer).value)   # TOKEN_USER starts with the SID pointer
            finally:
                self.k32.CloseHandle(token)
        return self._sid

    def volume_has_acls(self, path):
        ctypes, w = self.ctypes, self.w
        root = ctypes.create_unicode_buffer(1024)
        if not self.k32.GetVolumePathNameW(path, root, 1024):
            raise OSError(ctypes.get_last_error(), 'GetVolumePathNameW')
        flags = w.DWORD()
        if not self.k32.GetVolumeInformationW(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
            raise OSError(ctypes.get_last_error(), 'GetVolumeInformationW')
        return bool(flags.value & 0x8)       # FILE_PERSISTENT_ACLS

    def set_dacl(self, path, sddl):
        """Protected DACL from `sddl` on `path`; returns the Windows error code (0 = success)."""
        ctypes, w = self.ctypes, self.w
        descriptor = ctypes.c_void_p()
        if not self.adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            return ctypes.get_last_error() or -1
        try:
            present, defaulted, dacl = w.BOOL(), w.BOOL(), ctypes.c_void_p()
            if not self.adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                      ctypes.byref(defaulted)):
                return ctypes.get_last_error() or -1
            # DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION: no inheritance from the parent
            return int(self.adv.SetNamedSecurityInfoW(path, 1, 0x4 | 0x80000000, None, None, dacl, None))
        finally:
            self.k32.LocalFree(descriptor)

    def read_dacl(self, path):
        """`[(ace_type, ace_flags, mask, sid)]`, or None when the object has no DACL (full access for everyone)."""
        ctypes = self.ctypes
        descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        code = self.adv.GetNamedSecurityInfoW(path, 1, 0x4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code != 0:
            raise OSError(code, 'GetNamedSecurityInfoW')
        try:
            if not dacl.value:
                return None

            class AclSizeInformation(ctypes.Structure):
                _fields_ = [('AceCount', self.w.DWORD), ('AclBytesInUse', self.w.DWORD), ('AclBytesFree', self.w.DWORD)]
            info = AclSizeInformation()
            if not self.adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):   # AclSizeInformation
                raise OSError(ctypes.get_last_error(), 'GetAclInformation')
            aces = []
            for index in range(info.AceCount):
                ace = ctypes.c_void_p()
                if not self.adv.GetAce(dacl, index, ctypes.byref(ace)):
                    raise OSError(ctypes.get_last_error(), 'GetAce')
                address = ace.value
                kind = ctypes.c_ubyte.from_address(address).value
                flags = ctypes.c_ubyte.from_address(address + 1).value
                mask = ctypes.c_uint32.from_address(address + 4).value
                # ACCESS_ALLOWED/DENIED_ACE (types 0 and 1): the SID follows the mask; other ACE types stay unknown (kind kept)
                sid = self._sid_text(address + 8) if kind in (0, 1) else ''
                aces.append((kind, flags, mask, sid))
            return aces
        finally:
            self.k32.LocalFree(descriptor)
