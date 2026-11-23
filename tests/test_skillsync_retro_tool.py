"""Owner `tool` and the cause keys `tool-duplication` / `tool-candidate` (CORE-1140-SUP-TOOLS, TOOLS-4).

The owners of a fix have ONE source (`runs.OWNERS`); the proposals of the retrospective add only `docs`. The two keys are in the list of cause keys of the core,
so `retro` and `retro-scan --cross-project` count them like any other key.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillsync_workflow import retro, runs  # noqa: E402

LIBRARY = Path(__file__).resolve().parents[1] / 'library'


class OwnersTest(unittest.TestCase):
    def test_one_source_and_proposals_differ_only_by_docs(self):
        self.assertIn('tool', runs.OWNERS)
        self.assertNotIn('docs', runs.OWNERS)
        self.assertEqual(set(runs.PROPOSAL_OWNERS), set(runs.OWNERS) | {'docs'})
        self.assertEqual(len(runs.PROPOSAL_OWNERS), len(set(runs.PROPOSAL_OWNERS)))

    def test_a_record_with_owner_tool_is_valid_and_docs_still_is_not_for_a_failure(self):
        runs._extra_fields({'cause_key': 'tool-duplication', 'owner': 'tool'})
        with self.assertRaises(ValueError):
            runs._extra_fields({'cause_key': 'k', 'owner': 'docs'})


class KeysTest(unittest.TestCase):
    def test_the_core_list_is_valid_and_has_both_keys(self):
        data = retro._cause_keys_file(LIBRARY / 'workflow' / 'cause-keys.yaml')
        self.assertIsNotNone(data)
        for key in ('tool-duplication', 'tool-candidate'):
            self.assertIn(key, data['keys'])
            self.assertTrue(data['keys'][key]['description'])


if __name__ == '__main__':
    unittest.main()
