"""`skillsync doctor`: a reference `../<other-skill>/...` in a SKILL.md is resolved across layers.

A skill of an org layer links a file of a core skill (`../beta/extra.md`); in an installed project the skills are siblings, so the link
works, and `doctor` must not report `Unresolved skill reference`. A reference that no layer provides, a path that leaves `skills/` and a
skill that no layer has stay warnings. All layers are synthetic; the machine state is isolated (`ComposeCase`).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_skillsync_layers_doctor as base  # noqa: E402
from layers_helpers import SKILL_TEXT  # noqa: E402

UNRESOLVED = 'Unresolved skill reference'


class CrossLayerReferences(base.DoctorCase):
    def project_with_reference(self, name, reference, core_extra=None, skills=('beta', 'x')):
        """Core provides `beta` (with `extra.md`) and an instruction; the org skill `x` links `reference`."""
        core_files = dict(base.CORE_FILES)
        core_files['skills/beta/extra.md'] = 'core extra\n'
        core_files.update(core_extra or {})
        org_files = dict(base.ORG_FILES)
        org_files['skills/x/SKILL.md'] = SKILL_TEXT.format('x') + f'See [t]({reference}).\n'
        self.layer_paths['core'] = self.layer_dir('core', 'core', core_files, version=self.version)
        self.layer_paths['org'] = self.layer_dir('org', 'org', org_files)
        entries = [('core', 'core', self.layer_paths['core']), ('org', 'org', self.layer_paths['org'])]
        self.write_config(layers=entries, rules=[(['**'], ['core', 'org'])])
        project = self.new_project(name, skills=list(skills))
        self.link_ok(project)
        return project

    def unresolved(self, report):
        return [item['reason'] for item in report['findings'] if UNRESOLVED in item['reason']]

    def test_a_file_of_another_skill_from_another_layer_resolves(self):
        report = self.doctor(self.project_with_reference('cross', '../beta/extra.md'))
        self.assertEqual(self.unresolved(report), [], report)

    def test_a_file_in_no_layer_is_still_reported(self):
        report = self.doctor(self.project_with_reference('missing-file', '../beta/nope.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ../beta/nope.md'], report)

    def test_a_skill_in_no_layer_is_still_reported(self):
        report = self.doctor(self.project_with_reference('ghost-skill', '../ghost/extra.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ../ghost/extra.md'], report)

    def test_a_path_leaving_skills_is_not_resolved_by_the_layerset(self):
        # `instructions/x.md` exists only in core; relative to the org skill the path points into the org layer, which lacks it.
        report = self.doctor(self.project_with_reference('outside', '../../instructions/x.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ../../instructions/x.md'], report)

    def test_a_parent_segment_after_the_skill_name_is_not_resolved(self):
        report = self.doctor(self.project_with_reference('dotdot', '../beta/../alpha/core.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ../beta/../alpha/core.md'], report)

    def test_a_skill_the_project_does_not_install_is_still_reported(self):
        # `beta` has the file in core, but is not in the skills of the project: after `link` the sibling is missing.
        report = self.doctor(self.project_with_reference('not-installed', '../beta/extra.md', skills=('x',)))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ../beta/extra.md'], report)

    def test_a_backslash_reference_is_not_normalised(self):
        report = self.doctor(self.project_with_reference('backslash', '..\\beta\\extra.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: ..\\beta\\extra.md'], report)

    def test_a_reference_inside_the_own_skill_is_unchanged(self):
        report = self.doctor(self.project_with_reference('own', 'org.md'))
        self.assertEqual(self.unresolved(report), [f'{UNRESOLVED}: org.md'], report)


class Resolver:
    """A layerset stand-in that records what is asked: the function must not ask for anything it rejects."""

    def __init__(self, present=()):
        self.asked, self.present = [], set(present)

    def resolve(self, rel):
        self.asked.append(rel)
        return object() if rel in self.present else None


class OtherSkillReferenceUnit(unittest.TestCase):
    def check(self, reference, skills=('beta',), present=('skills/beta/extra.md',)):
        from skillsync_workflow.quality import other_skill_reference_resolves
        layerset = Resolver(present)
        return other_skill_reference_resolves(layerset, skills, reference), layerset.asked

    def test_resolves_through_the_layerset(self):
        self.assertEqual(self.check('../beta/extra.md'), (True, ['skills/beta/extra.md']))
        self.assertEqual(self.check('../beta/references/a/b.md', present=('skills/beta/references/a/b.md',))[0], True)

    def test_rejected_forms_never_reach_the_layerset(self):
        for reference in ('../beta/../alpha/x.md', '../../instructions/x.md', '../beta/./extra.md', '../beta//extra.md',
                          '../beta/', '../beta', 'beta/extra.md', './beta/extra.md', '/beta/extra.md', '..\\beta\\extra.md',
                          '../ghost/extra.md'):
            with self.subTest(reference=reference):
                self.assertEqual(self.check(reference, present=('skills/alpha/x.md', 'skills/beta/extra.md')), (False, []))


if __name__ == '__main__':
    unittest.main()
