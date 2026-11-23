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
        self.fs.files[DIR + '/' + orphan[:-4].upper() + '.BAK'] = b'x'    # a foreign X.BAK on a case-insensitive file system
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


# ---------------------------------------------------------------- P3: wykonanie (B4), wynik i kody (B6), obejście wyjątków


class RecordingFs(object):
    """Wraps the in-memory fs: records unlink calls and lets a test raise on chosen paths."""

    def __init__(self, inner, fail=None, mutate=None):
        self.inner, self.unlinked, self.fail, self.mutate, self.lstat_calls = inner, [], dict(fail or {}), dict(mutate or {}), {}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def lstat(self, path):
        self.lstat_calls[path] = self.lstat_calls.get(path, 0) + 1
        if path in self.mutate and self.lstat_calls[path] == 2:     # the 1st call is the plan, the 2nd the re-check right before the deletion
            self.mutate.pop(path)()
        return self.inner.lstat(path)

    def unlink(self, path):
        if path in self.fail:
            error = self.fail[path]
            if isinstance(error, type):
                raise error('boom')
            raise error
        self.unlinked.append(path)
        self.inner.unlink(path)


def opts(m, **kw):
    values = {'database': 'appdb', 'confirm': 'appdb', 'dir': DIR, 'keep': 1}
    values.update(kw)
    return m.PruneOptions(**values)


class PruneExecution(PruneBase):
    def do_prune(self, fs=None, progress=None, **kw):
        progress = progress or self.m.PruneProgress()
        fs = fs or self.fs
        return self.m.run_prune(opts(self.m, **kw), fs=fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress), progress

    def pairs(self, count=3):
        return [self.snap(age_hours=24 * (i + 2)) for i in range(count)]    # index 0 is the newest

    def test_deletes_the_bak_first_then_its_sidecar_oldest_copy_first(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs)
        result, progress = self.do_prune(fs=fs)
        self.assertEqual([p.rsplit('/', 1)[1] for p in fs.unlinked], [oldest, oldest + '.json', middle, middle + '.json'])
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['deleted'], [oldest, oldest + '.json', middle, middle + '.json'])
        self.assertIn(newest, self.fs.listdir(DIR))
        self.assertEqual(result['failed'], [])

    def test_an_orphan_sidecar_alone_is_deleted(self):
        self.snap(age_hours=48)
        orphan = self.snap(age_hours=900)
        del self.fs.files[DIR + '/' + orphan]
        result, _ = self.do_prune()
        self.assertEqual(result['deleted'], [orphan + '.json'])

    def test_enoent_on_the_bak_is_not_a_failure_and_the_sidecar_is_still_deleted(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, mutate={DIR + '/' + old: lambda: self.fs.files.pop(DIR + '/' + old)})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual((result['status'], result['failed']), ('PASS', []))
        self.assertEqual(result['deleted'], [old + '.json'])

    def test_a_failed_bak_unlink_keeps_the_sidecar_and_continues_with_the_next_copy(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs, fail={DIR + '/' + oldest: PermissionError(1, 'Operation not permitted', DIR + '/' + oldest)})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual(result['status'], 'FAIL')    # something was deleted (the middle copy), something failed
        self.assertEqual(result['deleted'], [middle, middle + '.json'])
        self.assertIn(oldest + '.json', self.fs.listdir(DIR))
        self.assertEqual([entry['file'] for entry in result['failed']], [oldest])
        self.assertNotIn(DIR, repr(result['failed']), 'the error text must not carry a path')
        self.assertIn('Operation not permitted', repr(result['failed']))

    def test_every_deletion_failing_is_blocked_not_fail(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old: PermissionError(13, 'Permission denied')})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual((result['status'], result['deleted']), ('BLOCKED', []))
        self.assertEqual(len(result['failed']), 1)

    def test_a_failed_sidecar_unlink_after_the_bak_is_deleted_counts_as_a_failure_with_a_deletion(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old + '.json': PermissionError(13, 'Permission denied')})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual((result['status'], result['deleted']), ('FAIL', [old]))

    def test_a_file_changed_between_plan_and_deletion_is_skipped_and_its_sidecar_kept(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, mutate={DIR + '/' + old: lambda: self.fs.files.__setitem__(DIR + '/' + old, b'grown!!')})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual((result['status'], result['deleted'], result['failed']), ('PASS', [], []))
        self.assertIn({'file': old, 'reason': 'changed-since-planned'}, result['skipped'])
        self.assertIn(old + '.json', self.fs.listdir(DIR))

    def test_a_file_replaced_by_a_link_between_plan_and_deletion_is_not_deleted(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, mutate={DIR + '/' + old: lambda: self.fs.links.add(DIR + '/' + old)})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual(result['deleted'], [])
        self.assertIn({'file': old, 'reason': 'changed-since-planned'}, result['skipped'])

    def test_result_keys_and_the_skipped_list_limit(self):
        self.pairs(2)
        for i in range(70):
            self.fs.files['%s/foreign%03d.txt' % (DIR, i)] = b''
        result, _ = self.do_prune()
        self.assertEqual(sorted(result), ['database', 'deleted', 'dir', 'dry_run', 'failed', 'kept', 'label', 'older_than_days', 'skipped', 'skipped_total', 'status'])
        self.assertEqual(result['skipped_total'], 70)
        self.assertEqual(len(result['skipped']), 50)
        self.assertIs(result['dry_run'], False)
        self.assertTrue(all(set(entry) == {'file', 'reason'} for entry in result['kept'] + result['skipped']))

    def test_dry_run_changes_nothing_and_reports_the_plan_as_not_run(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs)
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_prune(fs=fs, dry_run=True)
        self.assertEqual(fs.unlinked, [])
        result = ctx.exception.result
        self.assertEqual((result['status'], result['dry_run'], result['would_delete']), ('NOT_RUN', True, [old, old + '.json']))
        self.assertTrue(any('every label' in warning for warning in result['warnings']), 'without --label the dry run warns about all labels')

    def test_dry_run_with_a_label_has_no_all_labels_warning(self):
        self.pairs(2)
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_prune(dry_run=True, label='pre')
        self.assertEqual(ctx.exception.result['warnings'], [])

    def test_confirm_must_equal_the_database(self):
        self.pairs(2)
        for confirm in (None, 'other', 'APPDB', ''):
            with self.assertRaises(self.m.Refusal):
                self.do_prune(confirm=confirm)

    def test_dry_run_also_needs_the_confirm(self):
        with self.assertRaises(self.m.Refusal):
            self.do_prune(dry_run=True, confirm=None)

    def test_a_missing_directory_is_a_refusal(self):
        with self.assertRaises(self.m.Refusal):
            self.do_prune(dir='/h/nowhere')

    def test_system_database_bad_label_and_bad_numbers_are_refused(self):
        for kw in ({'database': 'master', 'confirm': 'master'}, {'label': 'Bad Label'}, {'keep': 0}, {'keep': 1001}, {'older_than_days': 0}, {'older_than_days': 36501}):
            with self.assertRaises(self.m.Refusal, msg=repr(kw)):
                self.do_prune(**kw)

    def test_a_directory_inside_a_repository_is_refused(self):
        self.fs.dirs.add(DIR + '/.git')
        with self.assertRaises(self.m.Refusal):
            self.do_prune()

    def test_the_directory_is_resolved_once_and_the_real_path_is_used(self):
        newest, old = self.pairs(2)
        self.fs.makedirs('/h/real', 0o700)
        for name in list(self.fs.files):
            self.fs.files['/h/real/' + name.rsplit('/', 1)[1]] = self.fs.files.pop(name)
            self.fs.mtimes['/h/real/' + name.rsplit('/', 1)[1]] = self.fs.mtimes.pop(name, 0.0)

        class Linked(self.m._MemFs):
            def realpath(inner, path):
                return '/h/real' if path == DIR else path
        linked = Linked()
        linked.files, linked.dirs, linked.mtimes, linked.links = self.fs.files, self.fs.dirs, self.fs.mtimes, self.fs.links
        fs = RecordingFs(linked)
        result, _ = self.do_prune(fs=fs)
        self.assertEqual(result['dir'], '/h/real')
        self.assertEqual(sorted(p for p in fs.unlinked), sorted(['/h/real/' + old, '/h/real/' + old + '.json']))


class PruneGuard(PruneBase):
    """B6: one wrapper around the whole prune; nothing but {0,1,2,3}, never a traceback."""

    def guarded(self, fs, **kw):
        progress = self.m.PruneProgress()
        out, err = __import__('io').StringIO(), __import__('io').StringIO()

        def work():
            result = self.m.run_prune(opts(self.m, **kw), fs=fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress)
            out.write(self.m.format_result(result, kw.get('as_json', False), self.m.Masker([])) + '\n')
            return self.m.EXIT[result['status']]
        code = self.m.run_prune_guarded(work, progress, out, err, self.m.Masker([]), False)
        return code, out.getvalue(), err.getvalue()

    def pairs(self, count=3):
        return [self.snap(age_hours=24 * (i + 2)) for i in range(count)]

    def test_an_interrupt_after_the_first_deletion_is_exit_1_with_the_partial_result_and_the_line(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs, fail={DIR + '/' + middle: KeyboardInterrupt})
        code, out, err = self.guarded(fs)
        self.assertEqual(code, 1)
        self.assertIn('KeyboardInterrupt', err)
        self.assertIn('pliki kopii zostały częściowo usunięte', err)
        self.assertNotIn('stan bazy mógł się zmienić', err)
        self.assertNotIn('Traceback', err)
        self.assertIn(oldest, out)       # the partial result names what was deleted so far
        self.assertEqual(out.splitlines()[0], 'FAIL')

    def test_an_interrupt_before_any_deletion_is_exit_2_not_130(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old: KeyboardInterrupt})
        code, out, err = self.guarded(fs)
        self.assertEqual(code, 2)
        self.assertNotIn('pliki kopii', err)
        self.assertEqual(out, '')

    def test_an_unexpected_exception_after_a_deletion_is_exit_1_and_names_the_type(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs, fail={DIR + '/' + middle: ValueError})
        code, out, err = self.guarded(fs)
        self.assertEqual(code, 1)
        self.assertIn('ValueError', err)

    def test_memory_error_before_any_deletion_is_exit_2(self):
        self.pairs(2)

        class Broken(self.m._MemFs):
            def listdir(inner, path):
                raise MemoryError('x')
        broken = Broken()
        broken.dirs.add(DIR)
        code, out, err = self.guarded(broken)
        self.assertEqual(code, 2)
        self.assertIn('MemoryError', err)

    def test_a_listing_error_is_a_refusal_exit_2(self):
        class Broken(self.m._MemFs):
            def listdir(inner, path):
                raise PermissionError(path)
        broken = Broken()
        broken.dirs.add(DIR)
        code, out, err = self.guarded(broken)
        self.assertEqual(code, 2)
        self.assertIn('Refusal', err)

    def test_partial_failure_result_is_exit_1_and_all_failed_is_exit_2(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs, fail={DIR + '/' + oldest: PermissionError(13, 'Permission denied')})
        code, out, err = self.guarded(fs)
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[0], 'FAIL')

    def test_dry_run_notrun_is_exit_3_and_is_not_swallowed(self):
        self.pairs(2)
        progress = self.m.PruneProgress()
        out, err = __import__('io').StringIO(), __import__('io').StringIO()

        def work():
            try:
                self.m.run_prune(opts(self.m, dry_run=True), fs=self.fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress)
            except self.m.NotRun as exc:
                out.write(self.m.format_result(exc.result, False, self.m.Masker([])) + '\n')
                return self.m.EXIT['NOT_RUN']
        self.assertEqual(self.m.run_prune_guarded(work, progress, out, err, self.m.Masker([]), False), 3)
        self.assertEqual(out.getvalue().splitlines()[0], 'NOT_RUN')

    def test_an_exception_in_dry_run_is_exit_2_there_is_nothing_deleted(self):
        class Broken(self.m._MemFs):
            def listdir(inner, path):
                raise ValueError('x')
        broken = Broken()
        broken.dirs.add(DIR)
        code, out, err = self.guarded(broken, dry_run=True)
        self.assertEqual(code, 2)


# ---------------------------------------------------------------- P4: wiersz poleceń `prune`


class PruneCli(PruneBase):
    def cli(self, *argv, fs=None, environ=None):
        import io
        out, err = io.StringIO(), io.StringIO()
        code = self.m.main(list(argv), environ=environ if environ is not None else {'HOME': '/h/u'}, stdout=out, stderr=err, platform='linux',
                           fs=fs or self.fs, clock=lambda: NOW)
        return code, out.getvalue(), err.getvalue()

    def base(self, *extra):
        return ('prune', '--database', 'appdb', '--confirm', 'appdb', '--dir', DIR) + extra

    def pairs(self, count):
        return [self.snap(age_hours=24 * (i + 2)) for i in range(count)]

    def test_prune_deletes_and_reports_text_with_the_status_first(self):
        newest, old = self.pairs(2)
        code, out, err = self.cli(*self.base('--keep', '1'))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[0], 'PASS')
        self.assertIn(old, out)
        self.assertNotIn(DIR + '/' + old, self.fs.files)
        self.assertIn(DIR + '/' + newest, self.fs.files)

    def test_json_result_has_the_contract_keys(self):
        import json
        self.pairs(2)
        code, out, err = self.cli(*self.base('--keep', '1', '--json'))
        document = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(document), ['database', 'deleted', 'dir', 'dry_run', 'failed', 'kept', 'label', 'older_than_days', 'skipped', 'skipped_total', 'status'])
        self.assertEqual(document['status'], 'PASS')

    def test_without_keep_the_defaults_are_5_and_3(self):
        self.pairs(7)
        for i in range(7):
            self.snap(label='pre-restore', age_hours=24 * (i + 2))
        code, out, err = self.cli(*self.base())
        remaining = [name for name in self.fs.listdir(DIR) if name.endswith('.bak')]
        self.assertEqual((code, len([n for n in remaining if '-pre-2' in n]), len([n for n in remaining if '-pre-restore-' in n])), (0, 5, 3))

    def test_missing_confirm_is_exit_2_and_deletes_nothing(self):
        self.pairs(3)
        before = dict(self.fs.files)
        code, out, err = self.cli('prune', '--database', 'appdb', '--dir', DIR, '--keep', '1')
        self.assertEqual(code, 2)
        self.assertIn('Refusal', err)
        self.assertEqual(self.fs.files, before)
        self.assertEqual(out, '')

    def test_dry_run_with_confirm_is_exit_3_and_changes_nothing(self):
        self.pairs(3)
        before = dict(self.fs.files)
        code, out, err = self.cli(*self.base('--keep', '1', '--dry-run'))
        self.assertEqual(code, 3)
        self.assertEqual(out.splitlines()[0], 'NOT_RUN')
        self.assertEqual(self.fs.files, before)

    def test_dry_run_without_confirm_is_refused(self):
        self.pairs(2)
        code, out, err = self.cli('prune', '--database', 'appdb', '--dir', DIR, '--dry-run')
        self.assertEqual(code, 2)

    def test_argument_errors_are_exit_2(self):
        self.pairs(2)
        for argv in (('prune', '--confirm', 'appdb'), self.base('--keep', 'abc'), self.base('--older-than-days', 'x'), self.base('--conf', 'appdb'),
                     self.base('--auth', 'env'), self.base('--server', 'localhost'), self.base('--unknown'), self.base('--keep', '0'), self.base('--keep', '1001')):
            code, out, err = self.cli(*argv)
            self.assertEqual(code, 2, argv)

    def test_the_subcommand_is_required_message_names_prune(self):
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertIn('prune', err)

    def test_prune_never_looks_for_sqlcmd_or_starts_a_process(self):
        self.pairs(2)
        with mock.patch.object(self.m, 'find_sqlcmd', side_effect=AssertionError('sqlcmd must not be looked up')), \
                mock.patch.object(self.m.subprocess, 'Popen', side_effect=AssertionError('no process')), \
                mock.patch.object(self.m.subprocess, 'run', side_effect=AssertionError('no process')):
            code, out, err = self.cli(*self.base('--keep', '1'), environ={'HOME': '/h/u', 'SQLCMDPASSWORD': 'x' * 12})
        self.assertEqual(code, 0, err)

    def test_a_partial_failure_is_exit_1_with_the_line_on_stderr_and_the_result_on_stdout(self):
        newest, middle, oldest = self.pairs(3)
        fs = RecordingFs(self.fs, fail={DIR + '/' + oldest: PermissionError(13, 'Permission denied')})
        code, out, err = self.cli(*self.base('--keep', '1'), fs=fs)
        self.assertEqual(code, 1)
        self.assertIn('pliki kopii zostały częściowo usunięte', err)
        self.assertEqual(out.splitlines()[0], 'FAIL')

    def test_every_deletion_failing_is_exit_2_and_says_nothing_was_deleted(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old: PermissionError(13, 'Permission denied')})
        code, out, err = self.cli(*self.base('--keep', '1'), fs=fs)
        self.assertEqual(code, 2)
        self.assertIn('żaden plik kopii nie został usunięty', err)
        self.assertEqual(out.splitlines()[0], 'BLOCKED')

    def test_the_output_goes_through_the_masker(self):
        secret = 'sEcr3t-' + 'value-9'
        self.pairs(2)
        code, out, err = self.cli(*self.base('--keep', '1'), environ={'HOME': '/h/u', 'SQLCMDPASSWORD': secret})
        self.assertEqual(code, 0)
        self.assertNotIn(secret, out + err)


# ---------------------------------------------------------------- po przeglądzie R1: luki testowe i prawdziwy system plików


class ReviewGaps(PruneBase):
    def do_prune(self, fs=None, **kw):
        progress = self.m.PruneProgress()
        result = self.m.run_prune(opts(self.m, **kw), fs=fs or self.fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress)
        return result, progress

    def pairs(self, count=2):
        return [self.snap(age_hours=24 * (i + 2)) for i in range(count)]

    def test_dry_run_result_has_the_same_core_keys_as_a_run_plus_would_delete(self):
        self.pairs(2)
        with self.assertRaises(self.m.NotRun) as ctx:
            self.do_prune(dry_run=True)
        self.assertEqual(sorted(ctx.exception.result), ['database', 'deleted', 'dir', 'dry_run', 'failed', 'keep', 'kept', 'label', 'older_than_days',
                                                          'skipped', 'skipped_total', 'status', 'warnings', 'would_delete'])
        self.assertEqual((ctx.exception.result['deleted'], ctx.exception.result['failed']), ([], []))

    def test_enoent_raised_by_unlink_itself_is_not_a_failure(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old: FileNotFoundError})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual((result['status'], result['failed'], result['deleted']), ('PASS', [], [old + '.json']))

    def test_a_change_of_only_the_mtime_between_plan_and_deletion_is_detected(self):
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, mutate={DIR + '/' + old: lambda: self.fs.mtimes.__setitem__(DIR + '/' + old, self.fs.mtimes[DIR + '/' + old] + 5)})
        result, _ = self.do_prune(fs=fs)
        self.assertEqual(result['deleted'], [])
        self.assertIn({'file': old, 'reason': 'changed-since-planned'}, result['skipped'])

    def test_older_than_days_counts_from_the_newer_of_created_and_mtime(self):
        newest = self.snap(age_hours=30)
        touched = self.snap(age_hours=200)
        self.fs.mtimes[DIR + '/' + touched] = (NOW - datetime.timedelta(hours=10)).timestamp()
        plan = self.m.plan_prune(self.fs, DIR, 'appdb', None, 1, 4, NOW)
        self.assertEqual(plan.delete, [])
        self.assertEqual([e['reason'] for e in plan.kept if e['file'] == touched], ['newer-than-days'])
        self.fs.mtimes[DIR + '/' + touched] = (NOW - datetime.timedelta(hours=200)).timestamp()
        self.assertEqual([i.bak for i in self.m.plan_prune(self.fs, DIR, 'appdb', None, 1, 4, NOW).delete], [touched])

    def test_an_unreadable_bak_is_reported_as_unreadable_not_as_not_regular(self):
        self.snap(age_hours=10)
        name = self.snap(age_hours=900)

        class Denied(self.m._MemFs):
            def lstat(inner, path):
                if path.endswith(name):
                    raise PermissionError(path)
                return super().lstat(path)
        fs = Denied()
        fs.files, fs.dirs, fs.mtimes, fs.links = self.fs.files, self.fs.dirs, self.fs.mtimes, self.fs.links
        plan = self.m.plan_prune(fs, DIR, 'appdb', None, 1, None, NOW)
        self.assertEqual(plan.delete, [])
        self.assertEqual([e['reason'] for e in plan.skipped if e['file'] == name], ['bak-unreadable'])

    def test_an_interrupt_between_the_bak_and_its_sidecar_leaves_an_orphan_that_the_next_prune_removes(self):
        import io
        newest, old = self.pairs(2)
        fs = RecordingFs(self.fs, fail={DIR + '/' + old + '.json': KeyboardInterrupt})
        progress = self.m.PruneProgress()
        out, err = io.StringIO(), io.StringIO()

        def work():
            return self.m.EXIT[self.m.run_prune(opts(self.m), fs=fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress)['status']]
        code = self.m.run_prune_guarded(work, progress, out, err, self.m.Masker([]), False)
        self.assertEqual(code, 1)
        self.assertIn('database: appdb', out.getvalue())                      # the partial result carries its context
        self.assertIn('dir: ' + DIR, out.getvalue())
        self.assertNotIn(DIR + '/' + old, self.fs.files)
        self.assertIn(DIR + '/' + old + '.json', self.fs.files)
        result, _ = self.do_prune()
        self.assertEqual(result['deleted'], [old + '.json'])                   # the orphan (old enough by its mtime) goes at the next prune

    def test_a_closed_output_pipe_does_not_turn_the_handler_into_a_traceback(self):
        import io
        newest, middle, oldest = self.snap(age_hours=48), self.snap(age_hours=72), self.snap(age_hours=96)
        fs = RecordingFs(self.fs, fail={DIR + '/' + middle: ValueError})
        progress = self.m.PruneProgress()

        class Closed(object):
            def write(inner, text):
                raise BrokenPipeError('closed')

        def work():
            self.m.run_prune(opts(self.m), fs=fs, clock=lambda: NOW, environ={'HOME': '/h/u'}, platform='linux', progress=progress)
        code = self.m.run_prune_guarded(work, progress, Closed(), Closed(), self.m.Masker([]), False)
        self.assertEqual(code, 1)
        self.assertEqual(progress.deleted, [oldest, oldest + '.json'])

    def test_windows_paths_use_the_ntpath_rules(self):
        newest, old = self.pairs(2)
        win = 'C:\\h\\c'

        class WinFs(self.m._MemFs):
            def listdir(inner, path):
                if path != win:
                    raise FileNotFoundError(path)
                prefix = win + '\\'
                return sorted({k[len(prefix):] for k in inner.files if k.startswith(prefix)})
        fs = WinFs()
        for key, value in self.fs.files.items():
            fs.files[win + '\\' + key.rsplit('/', 1)[1]] = value
        for key, value in self.fs.mtimes.items():
            fs.mtimes[win + '\\' + key.rsplit('/', 1)[1]] = value
        fs.dirs.add(win)
        progress = self.m.PruneProgress()
        result = self.m.run_prune(opts(self.m, dir=win), fs=fs, clock=lambda: NOW, environ={'USERPROFILE': 'C:\\profile'}, platform='win32', progress=progress)
        self.assertEqual(result['deleted'], [old, old + '.json'])
        self.assertNotIn(win + '\\' + old, fs.files)
        self.assertIn(win + '\\' + newest, fs.files)


class PruneHelp(Base):
    def test_help_of_prune_lists_the_flags_and_exits_0(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        code = self.m.main(['prune', '--help'], environ={}, stdout=out, stderr=err, platform='linux')
        self.assertEqual(code, 0)
        for flag in ('--database', '--confirm', '--label', '--keep', '--older-than-days', '--dir', '--dry-run', '--json'):
            self.assertIn(flag, out.getvalue())
        for absent in ('--server', '--auth'):
            self.assertNotIn(absent, out.getvalue())


class RealDirectory(Base):
    """The production path of `main`: the real file system (`RealFs`), a real temporary directory and the default `fs=None`.
    On Linux and macOS the real `validate_server_path` runs; on Windows it is replaced by the identity only because the whitelist of server paths
    refuses the short names of temporary directories (`RUNNER~1`); the rules themselves are covered with the in-memory file system above.
    The test with symbolic links runs on the real file system where the OS allows links (Linux, macOS, Windows with the privilege); otherwise the
    links are covered by the in-memory tests only."""

    def setUp(self):
        super().setUp()
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        self.copies = tmp_paths.make_dir(self.root, 'copies')

    def write(self, db='appdb', label='pre', age_hours=48.0, token='a00000', size=3, directory=None):
        created = NOW - datetime.timedelta(hours=age_hours)
        name = '%s-%s-%s-%s-%s.bak' % (db, label, created.strftime('%Y%m%d'), created.strftime('%H%M%S'), token)
        base = str(directory or self.copies)
        path = os.path.join(base, name)
        with open(path, 'wb') as handle:
            handle.write(b'x' * size)
        body = {'schema': 1, 'database': db, 'label': label, 'file': name, 'size': size, 'created_utc': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'backup_set_guid': '00000000-0000-0000-0000-000000000001', 'first_lsn': '1', 'last_lsn': '2', 'backup_finish_date': '2026-11-21T10:00:00',
                'remote': False, 'copy_only': True, 'server_path': path,
                'server': {'raw': 'localhost', 'canonical': 'localhost', 'engine_server_name': 'H', 'machine_name': 'H', 'instance_name': None}}
        with open(path + '.json', 'w', encoding='utf-8') as handle:
            handle.write(__import__('json').dumps(body))
        for target in (path, path + '.json'):
            os.utime(target, (created.timestamp(), created.timestamp()))
        return name

    def main(self, *argv):
        import io
        out, err = io.StringIO(), io.StringIO()
        import contextlib
        import sys
        patch = (mock.patch.object(self.m, 'validate_server_path', side_effect=lambda path, **kw: path) if sys.platform == 'win32'
                 else contextlib.nullcontext())
        with patch:
            code = self.m.main(list(argv), environ={'HOME': str(self.root)}, stdout=out, stderr=err, platform=sys.platform, clock=lambda: NOW)
        return code, out.getvalue(), err.getvalue()

    def listing(self, directory=None):
        return sorted(os.listdir(str(directory or self.copies)))

    def test_prune_on_a_real_directory_deletes_the_old_pairs_and_leaves_foreign_files(self):
        newest = self.write(age_hours=48, token='a00001')
        old = self.write(age_hours=96, token='a00002')
        oldest = self.write(age_hours=144, token='a00003')
        tmp_paths.write_file(self.copies, 'notes.txt', 'mine')
        code, out, err = self.main('prune', '--database', 'appdb', '--confirm', 'appdb', '--dir', str(self.copies), '--keep', '1')
        self.assertEqual((code, out.splitlines()[0]), (0, 'PASS'), err)
        self.assertEqual(self.listing(), sorted([newest, newest + '.json', 'notes.txt']))
        for gone in (old, oldest):
            self.assertIn(gone, out)

    def test_dry_run_on_a_real_directory_changes_nothing(self):
        self.write(age_hours=48, token='a00001')
        self.write(age_hours=96, token='a00002')
        before = self.listing()
        code, out, err = self.main('prune', '--database', 'appdb', '--confirm', 'appdb', '--dir', str(self.copies), '--keep', '1', '--dry-run')
        self.assertEqual((code, out.splitlines()[0]), (3, 'NOT_RUN'))
        self.assertEqual(self.listing(), before)

    def test_without_confirm_nothing_is_deleted_on_a_real_directory(self):
        self.write(age_hours=48, token='a00001')
        self.write(age_hours=96, token='a00002')
        before = self.listing()
        code, out, err = self.main('prune', '--database', 'appdb', '--dir', str(self.copies), '--keep', '1')
        self.assertEqual(code, 2)
        self.assertEqual(self.listing(), before)

    def test_a_link_named_like_a_copy_and_a_link_directory_are_handled_on_a_real_file_system(self):
        newest = self.write(age_hours=48, token='a00001')
        victim = tmp_paths.make_dir(self.root, 'elsewhere')
        target = tmp_paths.write_file(victim, 'precious.bak', 'keep me')
        link_name = 'appdb-pre-20200101-000000-ffffff.bak'
        link_dir = str(self.root / 'linked')
        try:
            os.symlink(str(target), os.path.join(str(self.copies), link_name))
            os.symlink(str(self.copies), link_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            return    # no symbolic links on this system (Windows without the privilege): covered with the in-memory file system above
        sidecar = {'schema': 1}
        tmp_paths.write_file(self.copies, link_name + '.json', __import__('json').dumps(sidecar))
        old = self.write(age_hours=96, token='a00002')
        code, out, err = self.main('prune', '--database', 'appdb', '--confirm', 'appdb', '--dir', link_dir, '--keep', '1')
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(str(target)), 'the file behind the link must not be touched')
        self.assertIn(link_name, self.listing(), 'the link itself is not ours and stays')
        self.assertIn(newest, self.listing())
        self.assertNotIn(old, self.listing(), 'the directory link is resolved once and the real directory is cleaned')


if __name__ == '__main__':
    unittest.main()
