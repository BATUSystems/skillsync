"""Fixture of the Chrome E2E of the feedback screen on the status page (tests/ui_verify/feedback-dashboard-e2e.test.mjs; CORE-1142-FB-BUTTON, S6).

    python3 tests/fixtures/feedback_dashboard_fixture.py <empty-base-dir> <kind> [<kind> ...]

Builds under `<base>` a project (manifest only; no git, no network, no real `az`), an `org` layer with a `feedback:` section, a machine config
(`SKILLSYNC_CONFIG`, per-user dirs inside `<base>`) that trusts the target, and one draft per `kind` (F1, F2, ... in the given order), each with a real
preview written by `feedback.cmd_draft` (the tracker read is replaced by "no open item", so the sha of the preview is the one the server recomputes).
Kinds: `full` (coverage full: no generic warning), `generic` (the default coverage of a fixture without identity files), `stale` (the draft is
changed after its preview), `xss` (markup-shaped text in the body, the title and a tag). Prints one JSON object: `{root, env, drafts: {F1: {kind, sha, sha12}}}`;
`env` is what the server and the commands of the test need (the machine config and the per-user dirs). Names are neutral examples.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))

import yaml  # noqa: E402

from layers_helpers import make_layer  # noqa: E402
from skillsync_workflow import feedback  # noqa: E402

SECTION = {'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject', 'area': 'Platform/Team', 'type': 'Task'}
TRUSTED = {'layer': 'org', 'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject'}
BASE_DRAFT = {'layer': 'org', 'kind': 'change', 'title': 'Skill step misses a check', 'target': 'skills/feature/SKILL.md',
              'problem': 'The step skips the check.\nSecond line.', 'evidence': [{'source': 'run-1', 'note': 'the check was not run'}],
              'proposal': 'Add the check to the step.', 'acceptance': [{'input': 'a project without tests', 'expected': 'the step reports NOT_RUN'}],
              'cause_key': 'missing-check', 'core_candidate': False}
KINDS = ('full', 'generic', 'stale', 'xss')


def library_path():
    return Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or REPO / 'library')


def build(base, kinds):
    base = Path(base).resolve()
    root, org = base / 'project', base / 'org-layer'
    root.mkdir(parents=True)
    library = library_path()
    make_layer(org, 'org', 'org', extra_meta={'feedback': SECTION})
    entries = [{'id': 'core', 'kind': 'core', 'path': str(library), 'source': str(library)},
               {'id': 'org', 'kind': 'org', 'path': str(org), 'source': str(org)}]
    config = base / 'skillsync-config.yaml'
    config.write_text(yaml.safe_dump({'layers': entries, 'layer_rules': [{'match': [base.as_posix() + '/**'], 'use': ['core', 'org']}],
                                      'feedback_targets': [TRUSTED]}, sort_keys=False), encoding='utf-8')
    manifest = root / '.github' / '.skillsync.yaml'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(yaml.safe_dump({'sync': {'library': str(library)}, 'workflow': {}}), encoding='utf-8')
    env = {'SKILLSYNC_CONFIG': str(config), 'XDG_CACHE_HOME': str(base / 'xdg-cache'), 'XDG_DATA_HOME': str(base / 'xdg-data')}
    if sys.platform == 'darwin':
        (base / 'machine-home').mkdir()
        env['HOME'] = str(base / 'machine-home')
    elif os.name == 'nt':
        (base / 'machine-home' / 'localappdata').mkdir(parents=True)
        env.update(USERPROFILE=str(base / 'machine-home'), LOCALAPPDATA=str(base / 'machine-home' / 'localappdata'))
    store = root / '.skillsync' / 'feedback'
    drafts = {}
    with mock.patch.dict(os.environ, env), mock.patch.object(feedback, '_call', return_value={'status': 'PASS', 'data': {'count': 0, 'items': []}}):
        for index, kind in enumerate(kinds, start=1):
            if kind not in KINDS:
                raise SystemExit(f'unknown kind {kind!r}')
            draft = dict(BASE_DRAFT, cause_key=f'cause-{index}')
            if kind == 'xss':
                draft.update(title='Markup </pre><img src=x onerror="window.__xss=1"> in a title',
                             proposal='</pre><img src=x onerror="window.__xss=1"><script>window.__xss=1</script> text', cause_key='cause-xss')
            draft_id, _ = feedback.save_draft(store, draft)
            report = feedback.cmd_draft(argparse.Namespace(path=str(root), layers=None, draft=draft_id, json=False))
            if report.get('status') != 'PASS':
                raise SystemExit(f'preview of {draft_id} did not pass: {json.dumps(report)[:300]}')
            preview_file = store / f'{draft_id}.preview.json'
            preview = json.loads(preview_file.read_text(encoding='utf-8'))
            if kind in ('full', 'xss'):
                preview['view']['scan']['coverage'] = 'full'   # the sha does not cover `view`; this is what an identity file would give
                preview_file.write_text(json.dumps(preview), encoding='utf-8')
            if kind == 'stale':
                feedback.save_draft(store, dict(draft, proposal='Something else entirely, written after the preview.'), draft_id)
            drafts[draft_id] = {'kind': kind, 'sha': preview['sha'], 'sha12': preview['sha'][:12]}
    return {'root': str(root), 'env': env, 'drafts': drafts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('base')
    parser.add_argument('kinds', nargs='+', choices=KINDS)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.base, args.kinds)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
