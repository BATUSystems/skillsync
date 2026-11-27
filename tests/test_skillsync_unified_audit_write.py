"""CORE-2000 U-4.3 (N3): a line of `run/audit.jsonl` is written whole or not at all. A consent is recorded only after its audit line, so a short
write that goes unnoticed would let a consent through with half a line of trail."""
from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402


class AuditLineTests(unittest.TestCase):
    def content(self) -> bytes:
        return self.path.read_bytes().replace(b"\r\n", b"\n")   # Windows opens the file in text mode

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="audit-write-")
        self.addCleanup(holder.cleanup)
        self.run_dir = holder.name
        self.path = Path(holder.name) / ps.LINK_AUDIT_FILE
        ps.link_audit_append(self.run_dir, {"event": "first"})
        self.before = self.content()

    def test_a_write_in_pieces_still_gives_the_whole_line(self) -> None:
        real = os.write
        with mock.patch.object(ps.os, "write", side_effect=lambda fd, data: real(fd, bytes(data[:5]))):
            ps.link_audit_append(self.run_dir, {"event": "second"})
        self.assertEqual(self.content(), self.before + b'{"event": "second"}\n')

    def test_a_write_that_fails_half_way_leaves_nothing_and_raises(self) -> None:
        real, calls = os.write, []

        def failing(fd, data):
            if calls:
                raise OSError(errno.ENOSPC, "No space left on device")
            calls.append(1)
            return real(fd, bytes(data[:5]))

        with mock.patch.object(ps.os, "write", side_effect=failing), self.assertRaises(OSError):
            ps.link_audit_append(self.run_dir, {"event": "second"})
        self.assertEqual(self.content(), self.before)

    def test_a_write_that_makes_no_progress_raises(self) -> None:
        with mock.patch.object(ps.os, "write", return_value=0), self.assertRaises(OSError):
            ps.link_audit_append(self.run_dir, {"event": "second"})
        self.assertEqual(self.content(), self.before)


if __name__ == "__main__":
    unittest.main()
