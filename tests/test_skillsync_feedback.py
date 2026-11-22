"""Draft, render, sha and store of the feedback channel (CORE-1140-FEEDBACK, F-3). Pure logic: no network, neutral names."""
import copy
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skillsync_workflow import feedback  # noqa: E402
from tmp_paths import child  # noqa: E402
import tempfile  # noqa: E402

DRAFT = {'layer': 'example-layer', 'kind': 'change', 'title': 'Skill step misses a check', 'target': 'skills/feature/SKILL.md',
         'problem': 'The step skips the check.\nSecond line.', 'evidence': [{'source': 'run-1', 'note': 'the check was not run'}],
         'proposal': 'Add the check to the step.', 'acceptance': [{'input': 'a project without tests', 'expected': 'the step reports NOT_RUN'}],
         'cause_key': 'missing-check', 'core_candidate': False}
SECTION = {'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject', 'area': 'Platform/Team', 'type': 'Task', 'labels': ['extra']}


def draft(**changes):
    value = copy.deepcopy(DRAFT)
    value.update(changes)
    return value


class ValidationTest(unittest.TestCase):
    def test_a_good_draft_is_kept(self):
        self.assertEqual(feedback.validate_draft(DRAFT), DRAFT)

    def test_bad_drafts_are_refused(self):
        bad = {
            'not a mapping': ['x'], 'unknown key': draft(token='x'), 'bad layer': draft(layer='Bad Layer'), 'bad kind': draft(kind='other'),
            'bad cause key': draft(cause_key='Upper_case'), 'cause key too long': draft(cause_key='a' * 49), 'core_candidate not bool': draft(core_candidate='yes'),
            'bad retro': draft(retro='run 1'), 'title missing': draft(title=''), 'title too long': draft(title='x' * 121),
            'title with new line': draft(title='a\nb'), 'target with ..': draft(target='skills/../x'), 'target absolute': draft(target='/etc/passwd'),
            'target with quote': draft(target="a'b"), 'problem too long': draft(problem='x' * 4001), 'evidence empty': draft(evidence=[]),
            'evidence bad entry': draft(evidence=[{'source': 'a'}]), 'evidence too many': draft(evidence=[{'source': 'a', 'note': 'b'}] * 21),
            'acceptance missing': {k: v for k, v in DRAFT.items() if k != 'acceptance'}, 'problem not text': draft(problem=5),
        }
        for name, value in bad.items():
            with self.subTest(name):
                with self.assertRaises(feedback.FeedbackError):
                    feedback.validate_draft(value)

    def test_control_and_bidi_characters_are_removed(self):
        cleaned = feedback.validate_draft(draft(problem='a\x00b‮c​de\r\nf', title='t\x1b[31mred'))
        self.assertEqual(cleaned['problem'], 'abcde\nf')
        self.assertEqual(cleaned['title'], 't[31mred')

    def test_the_draft_is_not_changed_in_place(self):
        original = draft(problem='a\x00b')
        feedback.validate_draft(original)
        self.assertEqual(original['problem'], 'a\x00b')


class RenderTest(unittest.TestCase):
    def render(self, **changes):
        return feedback.render(feedback.validate_draft(draft(**changes)), SECTION)

    def test_html_is_escaped_in_every_field(self):
        hostile = '<script>alert(1)</script> & "x"'
        rendered = self.render(problem=hostile, proposal=hostile, evidence=[{'source': hostile, 'note': hostile}],
                               acceptance=[{'input': hostile, 'expected': hostile}], target='skills/a b')
        self.assertNotIn('<script>', rendered['body'])
        self.assertIn('&lt;script&gt;', rendered['body'])

    def test_tags_and_target(self):
        rendered = self.render(core_candidate=True)
        self.assertEqual(rendered['tags'], sorted(['feedback', 'layer:example-layer', feedback.dedup_key(feedback.validate_draft(DRAFT)), 'extra', 'core-candidate']))
        self.assertEqual((rendered['organization'], rendered['project'], rendered['area'], rendered['type']),
                         ('example-org', 'ExampleProject', 'Platform/Team', 'Task'))
        self.assertEqual(rendered['title'], '[feedback] Skill step misses a check')
        self.assertTrue(all(len(tag) <= 60 for tag in rendered['tags']))

    def test_no_core_candidate_tag_by_default(self):
        self.assertNotIn('core-candidate', self.render()['tags'])

    def test_dedup_key_depends_on_layer_target_and_cause_only(self):
        base = feedback.dedup_key(feedback.validate_draft(DRAFT))
        self.assertRegex(base, r'^fbk-[0-9a-f]{12}$')
        self.assertEqual(base, feedback.dedup_key(feedback.validate_draft(draft(title='another title', problem='other'))))
        for changes in ({'layer': 'other-layer'}, {'target': 'skills/x/SKILL.md'}, {'cause_key': 'other-cause'}):
            self.assertNotEqual(base, feedback.dedup_key(feedback.validate_draft(draft(**changes))), changes)

    def test_mode_comment_needs_an_item_and_is_plain_text(self):
        valid = feedback.validate_draft(DRAFT)
        with self.assertRaises(feedback.FeedbackError):
            feedback.render(valid, SECTION, mode='comment')
        with self.assertRaises(feedback.FeedbackError):
            feedback.render(valid, SECTION, item=5)
        comment = feedback.render(valid, SECTION, mode='comment', item=17)
        self.assertEqual(comment['item'], 17)
        self.assertNotIn('<', comment['body'])


class ShaTest(unittest.TestCase):
    def rendered(self, **kw):
        return feedback.render(feedback.validate_draft(DRAFT), {**SECTION, **kw})

    def test_the_sha_is_stable_and_covers_target_mode_item_and_content(self):
        base = feedback.content_sha(self.rendered())
        self.assertRegex(base, r'^[0-9a-f]{64}$')
        self.assertEqual(base, feedback.content_sha(self.rendered()))
        for kw in ({'organization': 'other-org'}, {'project': 'Other'}, {'area': 'Other/Area'}, {'type': 'Bug'}, {'labels': []}):
            self.assertNotEqual(base, feedback.content_sha(self.rendered(**kw)), kw)
        valid = feedback.validate_draft(DRAFT)
        self.assertNotEqual(base, feedback.content_sha(feedback.render(valid, SECTION, mode='comment', item=1)))
        self.assertNotEqual(feedback.content_sha(feedback.render(valid, SECTION, mode='comment', item=1)),
                            feedback.content_sha(feedback.render(valid, SECTION, mode='comment', item=2)))
        self.assertNotEqual(base, feedback.content_sha(feedback.render(feedback.validate_draft(draft(proposal='changed')), SECTION)))

    def test_the_sha_does_not_depend_on_itself(self):
        rendered = self.rendered()
        sha = feedback.content_sha(rendered)
        with_tag = {**rendered, 'tags': rendered['tags'] + [f'fb-{sha[:12]}']}
        self.assertEqual(sha, feedback.content_sha(with_tag))

    def test_the_payload_adds_the_tag_and_the_marker_after_the_hash(self):
        created = feedback.final_payload(self.rendered())
        self.assertIn(f'fb-{created["sha12"]}', created['tags'])
        self.assertTrue(created['body'].endswith(f'<p>fb-{created["sha12"]}</p>'))
        comment = feedback.final_payload(feedback.render(feedback.validate_draft(DRAFT), SECTION, mode='comment', item=3))
        self.assertTrue(comment['body'].startswith(f'[fb-{comment["sha12"]}]\n'))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = child(Path(self.tmp.name), 'store')

    def test_save_and_load_round_trip_with_numbering(self):
        first, _ = feedback.save_draft(self.store, DRAFT)
        second, _ = feedback.save_draft(self.store, draft(cause_key='other'))
        self.assertEqual((first, second), ('F1', 'F2'))
        self.assertEqual(feedback.load_draft(self.store, 'F1'), DRAFT)
        self.assertEqual(stat_mode(self.store / 'F1.json'), 0o600)

    def test_a_number_taken_by_a_result_file_is_not_reused(self):
        self.store.mkdir(parents=True)
        (self.store / 'F4.sent.json').write_text('{}', encoding='utf-8')
        self.assertEqual(feedback.next_draft_id(self.store), 'F5')

    def test_bad_ids_and_missing_drafts(self):
        for bad in ('1', 'F0', '../F1', 'F1.json', ''):
            with self.assertRaises(feedback.FeedbackError):
                feedback.draft_path(self.store, bad)
        with self.assertRaises(feedback.FeedbackError):
            feedback.load_draft(self.store, 'F9')

    def test_a_corrupt_or_invalid_file_is_refused_without_quoting_it(self):
        self.store.mkdir(parents=True)
        (self.store / 'F1.json').write_text('{not json', encoding='utf-8')
        with self.assertRaises(feedback.FeedbackError):
            feedback.load_draft(self.store, 'F1')
        (self.store / 'F2.json').write_text(json.dumps({'layer': 'x'}), encoding='utf-8')
        with self.assertRaises(feedback.FeedbackError):
            feedback.load_draft(self.store, 'F2')

    @unittest.skipIf(os.name == 'nt', 'symlinks need privileges on Windows')
    def test_a_symlinked_store_or_draft_is_refused(self):
        target = child(Path(self.tmp.name), 'elsewhere')
        target.mkdir()
        link = child(Path(self.tmp.name), 'link')
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(feedback.FeedbackError):
            feedback.save_draft(link, DRAFT)
        self.store.mkdir(parents=True)
        (target / 'x.json').write_text(json.dumps(DRAFT), encoding='utf-8')
        (self.store / 'F1.json').symlink_to(target / 'x.json')
        with self.assertRaises(feedback.FeedbackError):
            feedback.load_draft(self.store, 'F1')

    def test_the_lock_is_exclusive_and_released(self):
        with feedback.store_lock(self.store):
            with self.assertRaises(feedback.FeedbackError):
                with feedback.store_lock(self.store, timeout=0.2):
                    pass
        with feedback.store_lock(self.store, timeout=0.2):
            pass

    def test_a_stale_lock_is_taken_over(self):
        self.store.mkdir(parents=True)
        lock = self.store / '.lock'
        lock.write_text('', encoding='utf-8')
        os.utime(lock, (1, 1))
        with feedback.store_lock(self.store, timeout=0.5):
            pass

    def test_store_root_of_a_plain_directory(self):
        project = child(Path(self.tmp.name), 'proj')
        project.mkdir()
        self.assertEqual(feedback.store_root(project), project.resolve() / '.skillsync' / 'feedback')

    def test_store_root_of_a_linked_worktree_is_the_main_checkout(self):
        import subprocess
        main = child(Path(self.tmp.name), 'main')
        main.mkdir()
        git = ['git', '-C', str(main), '-c', 'user.email=t@example.org', '-c', 'user.name=t']
        subprocess.run([*git, 'init', '-q'], check=True)
        subprocess.run([*git, 'commit', '-q', '--allow-empty', '-m', 'x'], check=True)
        linked = child(Path(self.tmp.name), 'linked')
        subprocess.run([*git, 'worktree', 'add', '-q', str(linked), '-b', 'side'], check=True)
        self.assertEqual(feedback.store_root(linked), main.resolve() / '.skillsync' / 'feedback')


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


if __name__ == '__main__':
    unittest.main()
