"""CORE-1142-T6, Dostawa 1: retencja migawek (`prune`) w db_snapshot.py i seam systemu plików (K9).

Spec: docs/specs/2026-11-23-db-snapshot-move-i-retencja.md (rewizja 5), część B. Wszystko jest wstrzykiwane (system plików, zegar, środowisko);
żaden proces ani `sqlcmd` nie jest uruchamiany. Symlinki i błędy `unlink` testujemy atrapą (bez pominięć testów na Windows: `check_skips`).
"""
import datetime
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import tmp_paths
from tests.test_skillsync_db_snapshot import Base


class RealFsSeam(Base):
    """K9: `listdir`, `lstat` (rodzaj, rozmiar, mtime), `isdir`, `unlink` w `RealFs`."""

    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.fs = self.m.RealFs()

    def path(self, name):
        return str(tmp_paths.child(self.root, name))

    def test_listdir_returns_the_names_of_the_direct_entries(self):
        tmp_paths.write_file(self.root, 'a.bak', 'x')
        tmp_paths.write_file(self.root, 'b.bak.json', '{}')
        tmp_paths.make_dir(self.root, 'sub')
        self.assertEqual(sorted(self.fs.listdir(str(self.root))), ['a.bak', 'b.bak.json', 'sub'])

    def test_listdir_of_a_missing_directory_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fs.listdir(self.path('missing'))

    def test_lstat_of_a_file_has_kind_file_size_and_mtime(self):
        target = tmp_paths.write_file(self.root, 'f.bak', '12345')
        os.utime(str(target), (1_700_000_000, 1_700_000_000))
        entry = self.fs.lstat(str(target))
        self.assertEqual((entry.kind, entry.size), ('file', 5))
        self.assertEqual(entry.mtime, 1_700_000_000)

    def test_lstat_of_a_directory_is_dir(self):
        self.assertEqual(self.fs.lstat(str(tmp_paths.make_dir(self.root, 'd.bak'))).kind, 'dir')

    def test_lstat_of_a_missing_path_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fs.lstat(self.path('missing.bak'))

    def test_lstat_maps_the_mode_bits_to_the_kind_without_following_links(self):
        for mode, expected in ((stat.S_IFLNK, 'link'), (stat.S_IFREG, 'file'), (stat.S_IFDIR, 'dir'), (stat.S_IFIFO, 'other'), (stat.S_IFCHR, 'other')):
            fake = os.stat_result((mode | 0o644, 0, 0, 1, 0, 0, 7, 0, 1_700_000_000, 0))
            with mock.patch.object(os, 'lstat', return_value=fake) as patched:
                entry = self.fs.lstat('/any/path')
            patched.assert_called_once_with('/any/path')
            self.assertEqual((entry.kind, entry.size, entry.mtime), (expected, 7, 1_700_000_000), expected)

    def test_isdir_distinguishes_directories_files_and_missing_paths(self):
        target = tmp_paths.write_file(self.root, 'f.bak', 'x')
        self.assertIs(self.fs.isdir(str(self.root)), True)
        self.assertIs(self.fs.isdir(str(target)), False)
        self.assertIs(self.fs.isdir(self.path('missing')), False)

    def test_unlink_removes_the_file_and_only_the_file(self):
        target = tmp_paths.write_file(self.root, 'f.bak', 'x')
        other = tmp_paths.write_file(self.root, 'g.bak', 'y')
        self.fs.unlink(str(target))
        self.assertFalse(os.path.exists(str(target)))
        self.assertTrue(os.path.exists(str(other)))

    def test_unlink_of_a_missing_file_raises_filenotfounderror(self):
        with self.assertRaises(FileNotFoundError):
            self.fs.unlink(self.path('missing.bak'))

    def test_unlink_does_not_remove_a_directory(self):
        directory = tmp_paths.make_dir(self.root, 'd.bak')
        with self.assertRaises(OSError):
            self.fs.unlink(str(directory))
        self.assertTrue(os.path.isdir(str(directory)))


class MemFsSeam(Base):
    """The in-memory file system of the selftest has the same seam."""

    def test_listdir_lstat_isdir_and_unlink(self):
        fs = self.m._MemFs()
        fs.makedirs('/h/c', 0o700)
        fs.files['/h/c/a.bak'] = b'abc'
        fs.mtimes['/h/c/a.bak'] = 1_700_000_000.0
        fs.links.add('/h/c/l.bak')
        fs.files['/h/c/l.bak'] = b''
        fs.files['/h/other/x.bak'] = b'zzz'
        self.assertEqual(sorted(fs.listdir('/h/c')), ['a.bak', 'l.bak'])
        entry = fs.lstat('/h/c/a.bak')
        self.assertEqual((entry.kind, entry.size, entry.mtime), ('file', 3, 1_700_000_000.0))
        self.assertEqual(fs.lstat('/h/c/l.bak').kind, 'link')
        self.assertEqual(fs.lstat('/h/c').kind, 'dir')
        self.assertIs(fs.isdir('/h/c'), True)
        self.assertIs(fs.isdir('/h/c/a.bak'), False)
        with self.assertRaises(FileNotFoundError):
            fs.lstat('/h/c/missing.bak')
        with self.assertRaises(FileNotFoundError):
            fs.listdir('/h/nowhere')
        fs.unlink('/h/c/a.bak')
        self.assertNotIn('/h/c/a.bak', fs.files)
        with self.assertRaises(FileNotFoundError):
            fs.unlink('/h/c/a.bak')


# ---------------------------------------------------------------- P2: reguła „nasz plik” (B2) i retencja (B3)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
DIR = '/h/c'


class PruneBase(Base):
    def setUp(self):
        super().setUp()
        self.fs = self.m._MemFs()
        self.fs.makedirs(DIR, 0o700)
        self.counter = 0

    def stamp(self, created):
        return created.strftime('%Y%m%d'), created.strftime('%H%M%S')

    def snap(self, db='appdb', label='pre', age_hours=48.0, token=None, size=3, remote=False, sidecar=True, name=None, sidecar_size='same', side_label=None, **over):
        """A copy and its sidecar as `backup` writes them; `over` overrides sidecar fields; returns the file name."""
        created = NOW - datetime.timedelta(hours=age_hours)
        token = token or ('%06x' % (self.counter + 0xa00000))
        self.counter += 1
        date, time = self.stamp(created)
        name = name or '%s-%s-%s-%s-%s.bak' % (db, label, date, time, token)
        path = DIR + '/' + name
        self.fs.files[path] = b'x' * size
        self.fs.mtimes[path] = created.timestamp()
        if sidecar:
            body = {'schema': 1, 'database': db, 'label': label, 'file': name, 'size': size if sidecar_size == 'same' else sidecar_size, 'created_utc': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'backup_set_guid': '00000000-0000-0000-0000-000000000001', 'first_lsn': '1', 'last_lsn': '2',
                    'backup_finish_date': '2026-11-21T10:00:00', 'remote': remote, 'copy_only': True, 'server_path': path,
                    'server': {'raw': 'localhost', 'canonical': 'localhost', 'engine_server_name': 'H', 'machine_name': 'H', 'instance_name': None}}
            if side_label is not None:
                body['label'] = side_label
            body.update(over)
            self.fs.files[path + '.json'] = __import__('json').dumps(body).encode('utf-8')
            self.fs.mtimes[path + '.json'] = created.timestamp()
        return name

    def plan(self, database='appdb', label=None, keep=5, older_than_days=None, now=NOW, fs=None):
        return self.m.plan_prune(fs or self.fs, DIR, database, label, keep, older_than_days, now)

    def deleted(self, plan):
        return sorted(item.bak or item.sidecar for item in plan.delete)

    def reason_of(self, plan, name):
        for bucket in (plan.kept, plan.skipped):
            for entry in bucket:
                if entry['file'] == name:
                    return entry['reason']
        return None


class SnapshotName(Base):
    def test_parses_database_label_stamp_and_token_from_the_right(self):
        parsed = self.m.parse_snapshot_name('app_db-pre-migration-20261122-102030-a1b2c3.bak')
        self.assertEqual((parsed['database'], parsed['label'], parsed['date'], parsed['time'], parsed['token']),
                         ('app_db', 'pre-migration', '20261122', '102030', 'a1b2c3'))

    def test_a_label_that_looks_like_a_stamp_is_still_one_interpretation(self):
        parsed = self.m.parse_snapshot_name('app-20260101-000000-aaaaaa-20261122-102030-a1b2c3.bak')
        self.assertEqual((parsed['database'], parsed['label']), ('app', '20260101-000000-aaaaaa'))

    def test_foreign_names_do_not_parse(self):
        for bad in ('notes.txt', 'appdb-pre-20261122-102030-a1b2c3.BAK', 'appdb-Pre-20261122-102030-a1b2c3.bak', 'appdb-pre-2026112-102030-a1b2c3.bak',
                    'appdb-pre-20261122-102030-a1b2c.bak', 'appdb-pre-20261122-102030-a1b2c3.bak.json', '.appdb-pre-20261122-102030-a1b2c3.bak.x.tmp',
                    '-pre-20261122-102030-a1b2c3.bak', 'appdb--20261122-102030-a1b2c3.bak',
                    'appdb-pre-20261122-102030-a1b2c3.bak\n', 'ąppdb-pre-20261122-102030-a1b2c3.bak'):
            self.assertIsNone(self.m.parse_snapshot_name(bad), repr(bad))

    def test_a_hyphen_after_the_database_starts_the_label(self):
        parsed = self.m.parse_snapshot_name('app-db-pre-20261122-102030-a1b2c3.bak')
        self.assertEqual((parsed['database'], parsed['label']), ('app', 'db-pre'))

    def test_database_and_label_length_limits(self):
        self.assertIsNotNone(self.m.parse_snapshot_name('a' * 128 + '-' + 'b' * 40 + '-20261122-102030-a1b2c3.bak'))
        self.assertIsNone(self.m.parse_snapshot_name('a' * 129 + '-pre-20261122-102030-a1b2c3.bak'))
        self.assertIsNone(self.m.parse_snapshot_name('a-' + 'b' * 41 + '-20261122-102030-a1b2c3.bak'))


class RetentionRule(PruneBase):
    def test_keeps_the_newest_n_per_group_and_plans_the_rest_with_their_sidecars(self):
        names = [self.snap(age_hours=24 * (i + 1)) for i in range(7)]   # names[0] is the newest
        plan = self.plan(keep=5)
        self.assertEqual(self.deleted(plan), sorted(names[5:]))
        for item in plan.delete:
            self.assertEqual((item.kind, item.sidecar), ('copy', item.bak + '.json'))
        for name in names[:5]:
            self.assertEqual(self.reason_of(plan, name), 'within-newest')

    def test_keep_none_means_5_for_a_label_and_3_for_pre_restore(self):
        pre = [self.snap(label='pre', age_hours=24 * (i + 1)) for i in range(7)]
        safety = [self.snap(label='pre-restore', age_hours=24 * (i + 1)) for i in range(7)]
        plan = self.plan(keep=None)
        self.assertEqual(self.deleted(plan), sorted(pre[5:] + safety[3:]))

    def test_an_explicit_keep_overrides_the_default_for_every_label(self):
        safety = [self.snap(label='pre-restore', age_hours=24 * (i + 1)) for i in range(7)]
        self.assertEqual(self.deleted(self.plan(keep=6)), sorted(safety[6:]))

    def test_a_group_with_fewer_copies_than_n_loses_nothing(self):
        for i in range(3):
            self.snap(age_hours=24 * (i + 1))
        self.assertEqual(self.plan(keep=5).delete, [])

    def test_groups_are_per_database_and_label(self):
        a = [self.snap(label='pre', age_hours=24 * (i + 1)) for i in range(3)]
        b = [self.snap(label='post', age_hours=24 * (i + 1)) for i in range(3)]
        plan = self.plan(keep=2)
        self.assertEqual(self.deleted(plan), sorted([a[2], b[2]]))

    def test_database_is_matched_without_case_and_app_does_not_touch_app_x(self):
        upper = [self.snap(db='AppDb', age_hours=24 * (i + 1)) for i in range(3)]
        lower = [self.snap(db='appdb', age_hours=24 * (i + 1) + 1) for i in range(3)]
        other = [self.snap(db='app_x', age_hours=24 * (i + 1)) for i in range(3)]
        plan = self.plan(database='APPDB', keep=2)
        self.assertEqual(len(plan.delete), 4)    # AppDb and appdb are one group of six copies (the matching ignores case): the 2 newest stay
        for name in other:
            self.assertEqual(self.reason_of(plan, name), 'other-database')
        self.assertTrue(set(self.deleted(plan)) <= set(upper + lower))

    def test_label_filter_limits_the_plan_to_one_label(self):
        pre = [self.snap(label='pre', age_hours=24 * (i + 1)) for i in range(3)]
        post = [self.snap(label='post', age_hours=24 * (i + 1)) for i in range(3)]
        plan = self.plan(label='pre', keep=1)
        self.assertEqual(self.deleted(plan), sorted(pre[1:]))
        for name in post:
            self.assertEqual(self.reason_of(plan, name), 'other-label')

    def test_older_than_days_is_an_and_with_the_newest_n(self):
        names = [self.snap(age_hours=h) for h in (30, 50, 80, 120, 200)]   # 1.25, 2.1, 3.3, 5, 8.3 days
        plan = self.plan(keep=2, older_than_days=4)
        self.assertEqual(self.deleted(plan), sorted(names[3:]))
        self.assertEqual(self.reason_of(plan, names[2]), 'newer-than-days')

    def test_a_copy_younger_than_one_hour_is_never_planned_even_beyond_n(self):
        old = [self.snap(age_hours=24 * (i + 2)) for i in range(2)]
        young = self.snap(age_hours=0.5)
        plan = self.plan(keep=1)
        self.assertEqual(self.deleted(plan), sorted([old[0], old[1]]))   # the young copy takes the 1st place in N, both old ones go
        self.assertEqual(self.reason_of(plan, young), 'within-newest')

    def test_too_young_by_created_or_by_mtime_or_by_a_future_mtime(self):
        a = self.snap(age_hours=48)
        b = self.snap(age_hours=47)
        c = self.snap(age_hours=46)
        d = self.snap(age_hours=45)
        self.fs.mtimes[DIR + '/' + a] = (NOW - datetime.timedelta(minutes=10)).timestamp()      # touched recently
        self.fs.mtimes[DIR + '/' + b] = (NOW + datetime.timedelta(days=3)).timestamp()          # mtime in the future
        plan = self.plan(keep=1)
        self.assertEqual(self.reason_of(plan, a), 'too-young')
        self.assertEqual(self.reason_of(plan, b), 'too-young')
        self.assertEqual(self.deleted(plan), sorted([c]))
        self.assertEqual(self.reason_of(plan, d), 'within-newest')

    def test_a_created_utc_in_the_future_is_skipped_and_does_not_push_real_copies_out(self):
        future = self.snap(age_hours=-48)
        real = [self.snap(age_hours=24 * (i + 1)) for i in range(2)]
        plan = self.plan(keep=2)
        self.assertEqual(self.reason_of(plan, future), 'future-timestamp')
        self.assertEqual(plan.delete, [])
        for name in real:
            self.assertEqual(self.reason_of(plan, name), 'within-newest')

    def test_a_tie_on_created_utc_is_broken_by_the_file_name(self):
        a = self.snap(age_hours=48, token='aaaaaa')
        b = self.snap(age_hours=48, token='bbbbbb')
        self.assertEqual(self.deleted(self.plan(keep=1)), [a])
        self.assertEqual(self.deleted(self.plan(keep=1)), [a])   # stable

    def test_an_impossible_calendar_date_in_the_name_is_skipped_never_an_exception(self):
        name = 'appdb-pre-20261322-102030-a1b2c3.bak'
        self.snap(name=name, created_utc='2026-13-22T10:20:30Z')
        plan = self.plan(keep=1)
        self.assertEqual(self.reason_of(plan, name), 'impossible-date')
        self.assertEqual(plan.delete, [])

    def test_keep_and_days_limits_are_not_planned_here_but_zero_keep_is_refused(self):
        self.snap()
        for keep in (0, -1):
            with self.assertRaises(self.m.Refusal):
                self.plan(keep=keep)


class OwnFileRule(PruneBase):
    """B2: only a regular .bak with a matching local sidecar is ours; everything else is skipped with a reason and never planned."""

    def old_pair_plus_one(self):
        self.snap(age_hours=10)    # one newer copy, so that anything planned would be visible with keep=1

    def test_foreign_entries_are_skipped_and_never_planned(self):
        self.old_pair_plus_one()
        for name in ('notes.txt', 'readme.bak', 'appdb-pre-20261122-102030-a1b2c3.BAK', '.appdb-x.bak.abc.tmp', 'other.json'):
            self.fs.files[DIR + '/' + name] = b'x'
        self.fs.dirs.add(DIR + '/sub')
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        for name in ('notes.txt', 'readme.bak', 'appdb-pre-20261122-102030-a1b2c3.BAK', '.appdb-x.bak.abc.tmp', 'other.json', 'sub'):
            self.assertEqual(self.reason_of(plan, name), 'not-a-snapshot-file', name)

    def test_a_bak_without_a_sidecar_is_skipped(self):
        self.old_pair_plus_one()
        name = self.snap(age_hours=900, sidecar=False)
        plan = self.plan(keep=1)
        self.assertEqual((plan.delete, self.reason_of(plan, name)), ([], 'no-sidecar'))

    def test_a_directory_symlink_or_dangling_link_named_bak_is_not_deleted_and_its_sidecar_is_not_an_orphan(self):
        self.old_pair_plus_one()
        as_dir = self.snap(age_hours=900)
        as_link = self.snap(age_hours=901)
        dangling = self.snap(age_hours=902)
        self.fs.dirs.add(DIR + '/' + as_dir)
        del self.fs.files[DIR + '/' + as_dir]
        self.fs.links.add(DIR + '/' + as_link)
        del self.fs.files[DIR + '/' + dangling]
        self.fs.links.add(DIR + '/' + dangling)
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        for name in (as_dir, as_link, dangling):
            self.assertEqual(self.reason_of(plan, name), 'bak-not-regular-file', name)
            self.assertIsNone(self.reason_of(plan, name + '.json'), 'the sidecar of a living (not regular) .bak is not reported as an orphan')

    def test_a_sidecar_that_is_a_link_or_a_directory_is_not_accepted(self):
        self.old_pair_plus_one()
        a = self.snap(age_hours=900)
        b = self.snap(age_hours=901)
        self.fs.links.add(DIR + '/' + a + '.json')
        del self.fs.files[DIR + '/' + b + '.json']
        self.fs.dirs.add(DIR + '/' + b + '.json')
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        self.assertEqual(self.reason_of(plan, a), 'sidecar-not-regular-file')
        self.assertEqual(self.reason_of(plan, b), 'sidecar-not-regular-file')

    def test_unreadable_oversized_or_invalid_sidecars_are_skipped_not_fatal(self):
        self.old_pair_plus_one()
        unreadable = self.snap(age_hours=900)
        big = self.snap(age_hours=901)
        bad_json = self.snap(age_hours=902)
        bad_schema = self.snap(age_hours=903, schema=2)
        self.fs.files[DIR + '/' + big + '.json'] = b'{' + b' ' * 70000 + b'}'
        self.fs.files[DIR + '/' + bad_json + '.json'] = b'not json'

        class Unreadable(self.m._MemFs):
            def read_bytes(inner, path, limit):
                if path.endswith(unreadable + '.json'):
                    raise PermissionError(path)
                return super().read_bytes(path, limit)
        fs = Unreadable()
        fs.files, fs.dirs, fs.mtimes, fs.links = self.fs.files, self.fs.dirs, self.fs.mtimes, self.fs.links
        plan = self.plan(keep=1, fs=fs)
        self.assertEqual(plan.delete, [])
        self.assertEqual(self.reason_of(plan, unreadable), 'sidecar-unreadable')
        for name in (big, bad_json, bad_schema):
            self.assertEqual(self.reason_of(plan, name), 'sidecar-invalid', name)

    def test_a_remote_sidecar_is_never_ours_with_or_without_its_bak(self):
        self.old_pair_plus_one()
        with_bak = self.snap(age_hours=900, remote=True)
        orphan = self.snap(age_hours=901, remote=True)
        del self.fs.files[DIR + '/' + orphan]
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        self.assertEqual(self.reason_of(plan, with_bak), 'sidecar-remote')
        self.assertEqual(self.reason_of(plan, orphan + '.json'), 'sidecar-remote')

    def test_the_sidecar_must_match_the_name_database_label_file_size_and_time(self):
        self.old_pair_plus_one()
        cases = {
            'other-database': dict(database='other'),
            'other-label': dict(side_label='post'),
            'other-file': dict(file='appdb-pre-20200101-000000-ffffff.bak'),
            'wrong-size': dict(sidecar_size=4),
            'null-size': dict(sidecar_size=None),
        }
        names = {}
        for label, override in cases.items():
            names[label] = self.snap(age_hours=900 + len(names), **override)
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        for label, name in names.items():
            self.assertEqual(self.reason_of(plan, name), 'sidecar-mismatch', label)

    def test_created_utc_must_have_the_strict_format_and_equal_the_stamp_of_the_name(self):
        self.old_pair_plus_one()
        names = [self.snap(age_hours=900 + i, created_utc=value) for i, value in enumerate((
            '2020-01-01T00:00:00Z', '2026-11-23 12:00:00', '2026-11-23T12:00:00', '2026-11-23T12:00:00+00:00', '', '2026-1-3T1:0:0Z'))]
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])
        for name in names:
            self.assertEqual(self.reason_of(plan, name), 'created-utc-mismatch', name)

    def test_a_valid_pair_is_planned(self):
        self.old_pair_plus_one()
        name = self.snap(age_hours=900)
        plan = self.plan(keep=1)
        self.assertEqual(self.deleted(plan), [name])
        item = plan.delete[0]
        self.assertEqual((item.kind, item.bak, item.sidecar), ('copy', name, name + '.json'))
        self.assertEqual((item.bak_stat.kind, item.bak_stat.size), ('file', 3))

    def test_directory_listing_over_the_limit_is_refused(self):
        for i in range(10001):
            self.fs.files['%s/f%05d.txt' % (DIR, i)] = b''
        with self.assertRaises(self.m.Refusal):
            self.plan(keep=1)

    def test_a_listing_error_is_a_refusal_not_a_crash(self):
        class Broken(self.m._MemFs):
            def listdir(inner, path):
                raise PermissionError(path)
        with self.assertRaises(self.m.Refusal):
            self.plan(fs=Broken())


class OrphanSidecars(PruneBase):
    def make_orphan(self, age_hours=900, **kw):
        name = self.snap(age_hours=age_hours, **kw)
        del self.fs.files[DIR + '/' + name]
        return name

    def test_an_old_local_orphan_is_planned_and_not_counted_in_n(self):
        real = self.snap(age_hours=48)
        orphan = self.make_orphan()
        plan = self.plan(keep=1)
        self.assertEqual([(i.kind, i.bak, i.sidecar) for i in plan.delete], [('orphan', None, orphan + '.json')])
        self.assertEqual(self.reason_of(plan, real), 'within-newest')

    def test_an_orphan_younger_than_one_hour_by_its_mtime_is_kept(self):
        orphan = self.make_orphan(age_hours=900)
        self.fs.mtimes[DIR + '/' + orphan + '.json'] = (NOW - datetime.timedelta(minutes=5)).timestamp()
        plan = self.plan(keep=1)
        self.assertEqual((plan.delete, self.reason_of(plan, orphan + '.json')), ([], 'orphan-too-young'))

    def test_a_sidecar_whose_bak_exists_in_any_case_is_not_an_orphan(self):
        orphan = self.make_orphan()
        self.fs.files[DIR + '/' + orphan.upper().replace('.BAK', '.BAK')] = b'x'    # a foreign X.BAK on a case-insensitive file system
        plan = self.plan(keep=1)
        self.assertEqual(plan.delete, [])

    def test_an_orphan_of_another_database_or_label_or_foreign_name_is_skipped(self):
        other_db = self.make_orphan(db='other')
        other_label = self.make_orphan(label='post')
        self.fs.files[DIR + '/random.bak.json'] = b'{}'
        plan = self.plan(label='pre', keep=1)
        self.assertEqual(plan.delete, [])
        self.assertEqual(self.reason_of(plan, other_db + '.json'), 'other-database')
        self.assertEqual(self.reason_of(plan, other_label + '.json'), 'other-label')
        self.assertEqual(self.reason_of(plan, 'random.bak.json'), 'not-a-snapshot-file')

    def test_an_orphan_with_a_mismatching_sidecar_is_skipped(self):
        orphan = self.make_orphan(file='appdb-pre-20200101-000000-ffffff.bak')
        plan = self.plan(keep=1)
        self.assertEqual((plan.delete, self.reason_of(plan, orphan + '.json')), ([], 'sidecar-mismatch'))

    def test_an_orphan_with_an_invalid_sidecar_is_skipped(self):
        orphan = self.make_orphan()
        self.fs.files[DIR + '/' + orphan + '.json'] = b'nope'
        plan = self.plan(keep=1)
        self.assertEqual((plan.delete, self.reason_of(plan, orphan + '.json')), ([], 'sidecar-invalid'))


if __name__ == '__main__':
    unittest.main()
