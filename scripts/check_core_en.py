#!/usr/bin/env python3
"""Check (and with `--update` lock) the English translation layer `layers/core-en` of this repository against `library/`.

    python3 scripts/check_core_en.py [--only PREFIX ...] [--update]

Prints the findings of `skillsync_workflow.translations` (structure, completeness, orphans, case, lock) and the Polish diacritics left in the
translated files (a translation writes the protocol tokens in English, see the token map of CORE-1130-I18N). `--only skills/feature` limits
the report to paths with that prefix. Exit 0 when nothing is reported, 1 otherwise. Needs no machine config.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from skillsync_workflow import layers, translations  # noqa: E402

POLISH = re.compile('[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--only', action='append', default=[], metavar='PREFIX', help='report only paths starting with PREFIX (repeatable)')
    parser.add_argument('--update', action='store_true', help='write translations.lock.yaml first')
    args = parser.parse_args(argv)
    core = layers.Layer('core', 'core', ROOT, 'library')
    english = layers.Layer('core-en', 'org', ROOT, 'layers/core-en', translation=True)
    if args.update:
        path, changed = translations.update_lock(english, core)
        print(f'lock {path.relative_to(ROOT)}: {len(changed)} entries changed')
    report = []
    for finding in translations.layer_findings(english, core):
        report.append((finding['code'], finding['message']))
    for rel in layers.layer_files(english):
        if rel.endswith('.md') and (english.root / rel).read_text(encoding='utf-8').strip():
            text = (english.root / rel).read_text(encoding='utf-8')
            found = sorted(set(POLISH.findall(text)))
            if found:
                lines = [str(number) for number, line in enumerate(text.splitlines(), 1) if POLISH.search(line)][:8]
                report.append(('polish-left', f'{rel}: Polish letters on lines {", ".join(lines)}'))
    if args.only:
        report = [item for item in report if any(prefix in item[1] for prefix in args.only)]
    for code, message in sorted(report):
        print(f'[{code}] {message}')
    print(f'{len(report)} finding(s); {len(translations.translated_files(english, core))} translated file(s)')
    return 1 if report else 0


if __name__ == '__main__':
    sys.exit(main())
