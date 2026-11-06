#!/usr/bin/env python3
"""Check the skipped tests of a `python -m unittest -v` run against `tests/skip-baseline.json` (K13).

A skip outside the baseline is a FAIL: a test that quietly stopped running must not look like a green run.

    python -m unittest discover -s tests -p 'test_skillsync*.py' -v 2>&1 | python scripts/check_skips.py
    python scripts/check_skips.py run.log --system ubuntu-latest --mode copy
    python scripts/check_skips.py run.log --emit        # print the baseline entries for this run

Baseline: `{"baselines": {"<system>/<mode>": [{"id": ..., "category": ...}, ...] | null}, "categories": {...}}`.
`null` = not measured yet (the first CI run on that runner): a warning and exit 0.

Exit codes: 0 = within the baseline (or `null`), 1 = FAIL (skip outside the baseline or with another reason),
2 = unusable input (bad JSON, missing key, no test results, skip count differs from the summary).
Standard library only.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_BASELINE = Path(__file__).resolve().parent.parent / 'tests' / 'skip-baseline.json'
ID_LINE = re.compile(r'^(?P<name>test\w*|setUpClass|setUpModule) \((?P<id>[\w.]+)\)(?: \.\.\. (?P<rest>.*))?$')
# repr() of the reason uses "..." when the text has an apostrophe, so both quotes are valid
REASON = r"""(?:'(?P<single>.*)'|"(?P<double>.*)")\s*$"""
SKIP_INLINE = re.compile(r'\.\.\. skipped ' + REASON)
SKIP_ALONE = re.compile(r'^skipped ' + REASON)
SKIP_REST = re.compile(r'skipped ' + REASON)
RAN = re.compile(r'^Ran (?P<count>\d+) tests? in ')
SUMMARY_SKIPPED = re.compile(r'^(?:OK|FAILED) \(.*\bskipped=(?P<count>\d+)')
SUMMARY = re.compile(r'^(?:OK|FAILED)\b')


class InputError(Exception):
    """The input cannot be judged (exit 2)."""


def normalise_id(raw):
    """`tests.test_x.Class.test_y` and `test_x.Class.test_y` are the same test."""
    return raw[len('tests.'):] if raw.startswith('tests.') else raw


def reason_of(match):
    return match['single'] if match['single'] is not None else match['double']


def parse_skips(text):
    """Return `(skips, tests_ran, skipped_in_summary)`; `skips` = [(test id, reason)] in log order."""
    skips, ran, summary_skipped, current = [], None, None, None
    for line in text.splitlines():
        match = RAN.match(line)
        if match:
            ran = int(match['count'])
            continue
        if SUMMARY.match(line):
            found = SUMMARY_SKIPPED.match(line)
            summary_skipped = int(found['count']) if found else 0
            continue
        ident = ID_LINE.match(line)
        if ident:
            current = normalise_id(ident['id'])  # a docstring test prints its result on the NEXT line
            if ident['name'] in ('setUpClass', 'setUpModule'):
                current = f"{ident['name']}:{current}"  # a fixture skip is its own entry, never a test id
            found = SKIP_REST.match(ident['rest'] or '')
            if found:
                skips.append((current, reason_of(found)))
            continue
        found = SKIP_INLINE.search(line) or SKIP_ALONE.match(line)
        if found and current is not None:
            skips.append((current, reason_of(found)))
    return skips, ran, summary_skipped


def load_baseline(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise InputError(f'baseline {path} is unreadable or not JSON: {error}')
    if not isinstance(data, dict) or not isinstance(data.get('baselines'), dict):
        raise InputError(f'baseline {path}: expected an object with a "baselines" object')
    categories = data.get('categories', {})
    if not isinstance(categories, dict):
        raise InputError(f'baseline {path}: "categories" must be an object of category -> [reason fragments]')
    for key, entries in data['baselines'].items():
        if entries is None:
            continue
        if not isinstance(entries, list) or not all(
                isinstance(item, dict) and isinstance(item.get('id'), str) and isinstance(item.get('category'), str)
                for item in entries):
            raise InputError(f'baseline {path}: "{key}" must be null or a list of {{"id", "category"}} objects')
        ids = [item['id'] for item in entries]
        if len(ids) != len(set(ids)):
            raise InputError(f'baseline {path}: "{key}" lists a test id twice')
    return data


def classify(reason, categories):
    for category, fragments in categories.items():
        if any(fragment in reason for fragment in fragments):
            return category
    return 'unclassified'


def default_system():
    return {'linux': 'linux', 'win32': 'windows', 'darwin': 'macos'}.get(sys.platform, sys.platform)


def default_mode():
    return 'copy' if os.environ.get('SKILLSYNC_NO_SYMLINK') else 'symlink'


def judge(text, baseline, key):
    """Return `(exit_code, report_lines)`."""
    skips, ran, summary_skipped = parse_skips(text)
    if not ran:
        raise InputError('no "Ran N tests" line in the input: zero collected tests is not a result')
    if summary_skipped is None:
        raise InputError('no OK/FAILED summary line in the input (run interrupted?)')
    if summary_skipped != len(skips):
        raise InputError(f'the summary says skipped={summary_skipped}, the lines show {len(skips)}: the log is cut or '
                         'a result line was not recognised')
    if key not in baseline['baselines']:
        known = ', '.join(sorted(baseline['baselines'])) or 'none'
        raise InputError(f'no baseline key "{key}" (known: {known}); add it, with null if not measured yet')
    allowed = baseline['baselines'][key]
    lines = [f'[skips] {key}: {len(skips)} skipped of {ran} tests']
    if allowed is None:
        lines.append(f'[warn] baseline "{key}" is null (not measured yet): skips are NOT enforced. Measure with '
                     '`--emit` after the first CI run and put the list in the baseline.')
        lines.extend(f'  skipped: {ident}  ({reason})' for ident, reason in skips)
        return 0, lines
    wanted = {item['id']: item['category'] for item in allowed}
    categories = baseline.get('categories', {})
    outside, changed, seen = [], [], set()
    for ident, reason in skips:
        seen.add(ident)
        if ident not in wanted:
            outside.append((ident, reason))
        elif classify(reason, categories) != wanted[ident]:
            changed.append((ident, reason, wanted[ident]))
    stale = sorted(set(wanted) - seen)
    if outside:
        lines.append(f'[FAIL] {len(outside)} test(s) skipped outside the baseline:')
        lines.extend(f'  + {ident}  ({reason})' for ident, reason in outside)
    if changed:
        lines.append(f'[FAIL] {len(changed)} test(s) skipped for another reason than their category:')
        lines.extend(f'  ~ {ident}  ({reason}); baseline category: {category}' for ident, reason, category in changed)
    if stale:
        lines.append(f'[info] {len(stale)} baseline entr{"y" if len(stale) == 1 else "ies"} not skipped in this run '
                     '(fine for a partial run; otherwise tighten the baseline):')
        lines.extend(f'  - {ident}' for ident in stale)
    if outside or changed:
        return 1, lines
    lines.append('[ok] every skip is in the baseline')
    return 0, lines


def emit(text, baseline_path):
    skips, _ran, _summary = parse_skips(text)
    categories = {}
    if baseline_path and Path(baseline_path).is_file():
        try:
            categories = load_baseline(baseline_path).get('categories', {})
        except InputError:
            pass
    return json.dumps([{'id': ident, 'category': classify(reason, categories)} for ident, reason in sorted(set(skips))],
                      indent=1, ensure_ascii=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('log', nargs='?', default='-', help='file with `unittest -v` output, `-` = stdin')
    parser.add_argument('--baseline', default=str(DEFAULT_BASELINE))
    parser.add_argument('--system', help='baseline key part (linux, ubuntu-latest, windows-latest, macos-latest...); '
                                          'default from the platform')
    parser.add_argument('--mode', choices=('symlink', 'copy'), help='default: copy when SKILLSYNC_NO_SYMLINK is set')
    parser.add_argument('--emit', action='store_true', help='print the baseline entries for this run and exit 0')
    args = parser.parse_args(argv)
    try:
        if args.log == '-':
            text = sys.stdin.buffer.read().decode('utf-8', 'replace')
        else:
            text = Path(args.log).read_bytes().decode('utf-8', 'replace')
    except OSError as error:
        print(f'[error] cannot read the input: {error}', file=sys.stderr)
        return 2
    if args.emit:
        print(emit(text, args.baseline))
        return 0
    key = f'{args.system or default_system()}/{args.mode or default_mode()}'
    try:
        code, lines = judge(text, load_baseline(args.baseline), key)
    except InputError as error:
        print(f'[error] {error}', file=sys.stderr)
        return 2
    print('\n'.join(lines))
    return code


if __name__ == '__main__':
    sys.exit(main())
