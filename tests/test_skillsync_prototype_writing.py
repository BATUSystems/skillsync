"""Znacznik zapisu ekranów `.writing.json` (K1, K2, K6): helper odczytu, zapisu i usuwania.

Kontrakt: spec zadania („Terminologia: ważny marker", K6). Zegar jest wstrzykiwany
(`now_ms`), odczyt niczego nie zapisuje; każdy marker spoza definicji „ważny" ma być traktowany jak brak.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_writing.py`.
Zgodność ze składnią Pythona 3.8.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

NOW = 1_800_000_000_000
MINUTE = 60_000


def marker_text(since=NOW, expires=NOW + 900_000, **extra):
    value = {'version': 1, 'since': since, 'expiresAt': expires, 'by': 'agent'}
    value.update(extra)
    return json.dumps(value)


class WritingHelperTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, ps.WRITING_MARKER)

    def put(self, text):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write(text)

    def test_name_is_a_dot_file_and_excluded(self):
        self.assertEqual(ps.WRITING_MARKER, '.writing.json')
        self.assertTrue(ps._excluded_segment(ps.WRITING_MARKER))

    def test_no_file_means_none(self):
        self.assertIsNone(ps.read_writing(self.dir, NOW))

    def test_write_then_read_roundtrip_with_ttl(self):
        written = ps.write_writing(self.dir, NOW, 900, 'ala@example.com')
        self.assertEqual(written, {'since': NOW, 'expiresAt': NOW + 900_000, 'by': 'ala@example.com'})
        self.assertEqual(ps.read_writing(self.dir, NOW + 1), written)
        self.assertEqual(json.loads(Path(self.path).read_text(encoding='utf-8'))['version'], 1)

    def test_expiry_is_exclusive_and_reading_writes_nothing(self):
        ps.write_writing(self.dir, NOW, 60, 'agent')
        before = Path(self.path).read_bytes()
        mtime = os.stat(self.path).st_mtime_ns
        self.assertIsNotNone(ps.read_writing(self.dir, NOW + 59_999))
        self.assertIsNone(ps.read_writing(self.dir, NOW + 60_000))
        self.assertIsNone(ps.read_writing(self.dir, NOW + 10 * MINUTE))
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.assertEqual(os.stat(self.path).st_mtime_ns, mtime)
        self.assertEqual(sorted(os.listdir(self.dir)), [ps.WRITING_MARKER])

    def test_injected_clock_decides_not_the_system_clock(self):
        ps.write_writing(self.dir, NOW, 900, 'agent')
        self.assertIsNotNone(ps.read_writing(self.dir, NOW + 1))
        self.assertIsNone(ps.read_writing(self.dir, NOW + 900_000))

    def test_renewal_replaces_both_fields_also_shortening(self):
        ps.write_writing(self.dir, NOW, 3600, 'agent')
        ps.write_writing(self.dir, NOW + MINUTE, 60, 'agent')
        value = ps.read_writing(self.dir, NOW + MINUTE)
        self.assertEqual((value['since'], value['expiresAt']), (NOW + MINUTE, NOW + 2 * MINUTE))
        self.assertIsNone(ps.read_writing(self.dir, NOW + 2 * MINUTE))

    def test_upper_limit_of_the_window(self):
        self.put(marker_text(expires=NOW + 3_600_000))
        self.assertIsNotNone(ps.read_writing(self.dir, NOW))
        self.put(marker_text(expires=NOW + 3_600_001))
        self.assertIsNone(ps.read_writing(self.dir, NOW))

    def test_far_future_expiry_does_not_block(self):
        self.put(marker_text(since=NOW, expires=NOW + 10 ** 12))
        self.assertIsNone(ps.read_writing(self.dir, NOW))
        self.put(marker_text(since=NOW, expires=NOW + 900_000))  # control: a sane window blocks
        self.assertIsNotNone(ps.read_writing(self.dir, NOW))

    def test_non_positive_window_is_none(self):
        self.put(marker_text(since=NOW, expires=NOW))
        self.assertIsNone(ps.read_writing(self.dir, NOW - 1))
        self.put(marker_text(since=NOW, expires=NOW - 1))
        self.assertIsNone(ps.read_writing(self.dir, NOW - 5))

    def test_since_from_the_future(self):
        self.put(marker_text(since=NOW + 60_000, expires=NOW + 60_000 + 900_000))
        self.assertIsNotNone(ps.read_writing(self.dir, NOW), 'exactly the 60 s tolerance is valid')
        self.put(marker_text(since=NOW + 60_001, expires=NOW + 60_001 + 900_000))
        self.assertIsNone(ps.read_writing(self.dir, NOW))

    def test_wrong_types_are_none(self):
        for text in (marker_text(since=True), marker_text(expires=True),
                     marker_text(since=float(NOW)), marker_text(expires=float(NOW + 900_000)),
                     marker_text(since=str(NOW)), marker_text(since=None),
                     marker_text(version=2), marker_text(version=True), marker_text(version='1'),
                     '[]', '1', 'null', '"x"'):
            with self.subTest(text=text):
                self.put(text)
                self.assertIsNone(ps.read_writing(self.dir, NOW))
        self.put(marker_text())  # control
        self.assertIsNotNone(ps.read_writing(self.dir, NOW))

    def test_by_is_not_required_but_kept(self):
        self.put(json.dumps({'version': 1, 'since': NOW, 'expiresAt': NOW + 1000}))
        self.assertEqual(ps.read_writing(self.dir, NOW)['by'], 'agent')

    def test_invalid_by_falls_back_to_agent(self):
        for by in ('a b', 'x' * 65, 5, None, 'line\nbreak', '<script>'):
            with self.subTest(by=by):
                self.put(json.dumps({'version': 1, 'since': NOW, 'expiresAt': NOW + 1000, 'by': by}))
                self.assertEqual(ps.read_writing(self.dir, NOW)['by'], 'agent')
        self.put(json.dumps({'version': 1, 'since': NOW, 'expiresAt': NOW + 1000, 'by': 'ala@example.com'}))
        self.assertEqual(ps.read_writing(self.dir, NOW)['by'], 'ala@example.com')

    def test_float_clock_is_truncated_not_fail_open(self):
        self.put(json.dumps({'version': 1, 'since': NOW, 'expiresAt': NOW + 1000}))
        self.assertIsNotNone(ps.read_writing(self.dir, float(NOW) + 0.7), 'zegar w ms jako float nie wyłącza blokady')
        self.assertIsNone(ps.read_writing(self.dir, True), 'bool nie jest czasem')

    def test_damaged_files_are_none(self):
        for data in (b'', b'{', b'\xff\xfe\x00', b'{"version": NaN}',
                     b'{"version":1,"since":NaN,"expiresAt":Infinity}', b'[' * 5000):
            with self.subTest(data=data[:20]):
                with open(self.path, 'wb') as handle:
                    handle.write(data)
                self.assertIsNone(ps.read_writing(self.dir, NOW))

    def test_too_big_file_is_none(self):
        self.put(marker_text(by='x' * 5000))
        self.assertGreater(os.path.getsize(self.path), 4096)
        self.assertIsNone(ps.read_writing(self.dir, NOW))
        self.put(marker_text(by='x' * 100))  # control
        self.assertIsNotNone(ps.read_writing(self.dir, NOW))

    def test_symlink_is_none(self):
        target = os.path.join(self.dir, 'target.json')
        Path(target).write_text(marker_text(), encoding='utf-8')
        try:
            os.symlink(target, self.path)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are not available')
        self.assertIsNone(ps.read_writing(self.dir, NOW))

    def test_directory_is_none(self):
        os.mkdir(self.path)
        self.assertIsNone(ps.read_writing(self.dir, NOW))

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'no FIFO on this platform')
    def test_fifo_does_not_hang(self):
        os.mkfifo(self.path)
        result = []
        thread = threading.Thread(target=lambda: result.append(ps.read_writing(self.dir, NOW)), daemon=True)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'read_writing hangs on a FIFO')
        self.assertEqual(result, [None])

    def test_remove_is_idempotent_and_leaves_links_alone(self):
        self.assertFalse(ps.remove_writing(self.dir))
        ps.write_writing(self.dir, NOW, 60, 'agent')
        self.assertTrue(ps.remove_writing(self.dir))
        self.assertFalse(os.path.lexists(self.path))
        self.assertFalse(ps.remove_writing(self.dir))
        os.mkdir(self.path)
        self.assertFalse(ps.remove_writing(self.dir))
        self.assertTrue(os.path.isdir(self.path))

    def test_write_refuses_a_symlink_in_place_of_the_marker(self):
        target = os.path.join(self.dir, 'target.json')
        Path(target).write_text('keep', encoding='utf-8')
        try:
            os.symlink(target, self.path)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks are not available')
        with self.assertRaises(OSError):
            ps.write_writing(self.dir, NOW, 60, 'agent')
        self.assertEqual(Path(target).read_text(encoding='utf-8'), 'keep')

    def test_by_pattern(self):
        for good in ('agent', 'ala.kot@example.com', 'a+b-c_d', 'x' * 64):
            self.assertTrue(ps.WRITING_BY_RE.fullmatch(good), good)
        for bad in ('', ' ', 'a b', 'x' * 65, '.hidden', 'a\nb', 'zażółć', '-x'):
            self.assertFalse(ps.WRITING_BY_RE.fullmatch(bad), repr(bad))


class WritingExcludedFromHashesTests(unittest.TestCase):
    """K2: the marker never changes hashes, never enters `assets_hash` and is not frozen into `iterations/`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        html = ('<!doctype html>\n<html lang="pl" data-prototype-id="demo">\n<head>\n<meta charset="utf-8">\n'
                '<title>Demo</title>\n<link rel="stylesheet" href="screens.css">\n</head>\n<body>\n'
                '<section class="screen" id="s2">\n  <div class="frame">A</div>\n</section>\n'
                '<script src="prototype.js"></script>\n</body>\n</html>\n')
        Path(self.dir, 'index.html').write_text(html, encoding='utf-8')
        Path(self.dir, 'screens.css').write_text('.frame{color:red}\n', encoding='utf-8')
        Path(self.dir, 'prototype.js').write_text('// engine\n', encoding='utf-8')

    def test_hashes_version_and_assets_do_not_change(self):
        before = ps.screen_hashes(self.dir)
        before_files = ps.asset_files(self.dir)
        ps.write_writing(self.dir, NOW, 900, 'agent')
        after = ps.screen_hashes(self.dir)
        self.assertEqual((after['assets'], after['version'], after['screens']),
                         (before['assets'], before['version'], before['screens']))
        self.assertEqual(ps.asset_files(self.dir), before_files)
        self.assertNotIn(ps.WRITING_MARKER, ps.asset_files(self.dir))
        self.assertEqual(ps.assets_hash(self.dir), before['assets'])

    def test_freeze_does_not_copy_the_marker(self):
        ps.write_writing(self.dir, NOW, 900, 'agent')
        final, _digest, _warnings = ps.freeze_iteration(self.dir, 1)
        self.assertTrue(os.path.isfile(os.path.join(final, 'index.html')), 'control: the freeze copied files')
        self.assertFalse(os.path.lexists(os.path.join(final, ps.WRITING_MARKER)))
        self.assertTrue(os.path.lexists(os.path.join(self.dir, ps.WRITING_MARKER)), 'the original stays')


if __name__ == '__main__':
    unittest.main()
