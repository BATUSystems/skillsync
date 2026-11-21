"""Generates the golden trees of the hook settings merge with the code that is in the working tree RIGHT NOW (plan CLAUDE-3, step F1).

The patterns in `golden/` were written by the code BEFORE the shared merge `skillsync_workflow/hooksync.py` (step F2). They are the contract of
K2: `link` must keep producing them. Do not regenerate them with a refactored generator to make a failing test pass; regenerate only to record
an intended, documented change of behaviour (e.g. step F6, `claude: false`), from the commit that introduces it, and review the diff.

Run (cwd anywhere; takes ~1 min; changes only a temporary directory):
    lslocks | grep heavy-run        # must be empty: `link` is a state-changing command
    python3 tests/fixtures/hooksync/generate_golden.py [case ...]       # all cases, or the named ones
    python3 tests/fixtures/hooksync/generate_golden.py --check          # compares instead of writing (exit 1 on a difference)

Isolation like the tests (`tests/layers_helpers.py`): a temporary HOME, XDG, LOCALAPPDATA and SKILLSYNC_CONFIG (a config with the repository
`library/` as the only core layer); the real `~/.cache/skillsync`, `~/.local/share/skillsync` and `~/.config/skillsync` are never touched
(`real_state_snapshot()` is compared before and after).
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cases  # noqa: E402
from layers_helpers import isolated_environment, real_state_snapshot  # noqa: E402
from tmp_paths import child  # noqa: E402


def golden_path(name):
    return child(cases.GOLDEN, f'{name}.json')


def dump(pattern):
    # sorted keys, ensure_ascii False, LF: the file has no raw CR byte (CRLF of a settings file is the escape `\r\n` inside a string)
    return json.dumps(pattern, indent=2, ensure_ascii=False, sort_keys=True) + '\n'


def generate(names):
    patterns = {}
    before = real_state_snapshot()
    with tempfile.TemporaryDirectory(prefix='skillsync-golden-') as temp:
        base = Path(temp).resolve()
        workdir = child(base, 'work')
        workdir.mkdir()
        with isolated_environment(child(base, 'machine')) as env:
            cases.write_machine_config(env['SKILLSYNC_CONFIG'])
            for name in names:
                print(f'case {name}', flush=True)
                patterns[name] = cases.run_case(name, cases.CASES[name], workdir)
    if real_state_snapshot() != before:
        raise SystemExit('the run touched the real Skillsync state directories')
    return patterns


def main(argv):
    check = '--check' in argv
    names = [arg for arg in argv if not arg.startswith('--')] or list(cases.CASES)
    unknown = [name for name in names if name not in cases.CASES]
    if unknown:
        raise SystemExit(f'unknown case: {", ".join(unknown)}; known: {", ".join(cases.CASES)}')
    patterns = generate(names)
    cases.GOLDEN.mkdir(exist_ok=True)
    different = []
    for name, pattern in patterns.items():
        text = dump(pattern)
        assert '\r' not in text
        target = golden_path(name)
        if check:
            if not target.is_file() or target.read_bytes().decode('utf-8') != text:
                different.append(name)
        else:
            target.write_bytes(text.encode('utf-8'))
    if check:
        print('different: ' + (', '.join(different) or 'none'))
        return 1 if different else 0
    print(f'written {len(patterns)} patterns to {cases.GOLDEN}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
