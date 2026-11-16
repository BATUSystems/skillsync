#!/usr/bin/env python3
"""Point check for Windows (180-A5): a `.cmd` shim (`npm`, `npx`, `ng`, `pnpm`, `yarn` are such shims) starts through `test_run.resolve_executable`.

Run on Windows from the repository root: `python scripts/windows_shim_check.py`. It creates a temporary `shimtool.cmd`, puts its directory on PATH and shows
(1) what happens with the bare name (CreateProcess does not start a `.cmd` by the name alone: FileNotFoundError), (2) that argv[0] resolved through
`shutil.which` (PATHEXT) does start and prints its output, (3) that a real `skillsync test-run` of the bare name works end to end.
Prints PASS / FAIL / NOT_RUN per item. Exit 0 = every item PASS, 1 = a FAIL, 3 = not Windows (NOT_RUN). Standard library only; no network."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import test_run  # noqa: E402


def main():
    if os.name != 'nt':
        print('NOT_RUN: this check needs Windows (.cmd shims)')
        return 3
    results = {}
    with tempfile.TemporaryDirectory(prefix='shim-check-') as tmp:
        shim = Path(tmp) / 'shimtool.cmd'
        shim.write_text('@echo off\r\necho Ran 1 test in 0.01s\r\necho OK\r\n', encoding='ascii')
        env = {**os.environ, 'PATH': tmp + os.pathsep + os.environ.get('PATH', '')}
        os.environ['PATH'] = env['PATH']
        try:
            subprocess.run(['shimtool'], capture_output=True, text=True, env=env, timeout=30)
            results['1 bare name fails (the problem is real)'] = 'FAIL (the bare name started: the premise does not hold on this machine)'
        except FileNotFoundError:
            results['1 bare name fails (the problem is real)'] = 'PASS'
        argv = test_run.resolve_executable(['shimtool'])
        done = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=30)
        results['2 resolved argv[0] starts the shim'] = 'PASS' if done.returncode == 0 and 'OK' in done.stdout and argv[0].lower().endswith('.cmd') else f'FAIL (argv={argv}, rc={done.returncode})'
        project = Path(tmp) / 'project'
        (project / '.github').mkdir(parents=True)
        (project / '.github' / '.skillsync.yaml').write_text('schema: 1\n', encoding='utf-8')
        (project / '.skillsync' / 'runs' / 'R1').mkdir(parents=True)
        env.update(XDG_DATA_HOME=str(Path(tmp) / 'd'), XDG_CACHE_HOME=str(Path(tmp) / 'c'), SKILLSYNC_CONFIG=str(Path(tmp) / 'cfg.yaml'))
        cli = [sys.executable, str(ROOT / 'skillsync'), 'test-run', str(project), '--run', 'R1', '--step', 'F1.1', '--', 'shimtool']
        done = subprocess.run(cli, capture_output=True, text=True, env=env, timeout=120)
        results['3 skillsync test-run of the bare name'] = 'PASS' if done.returncode == 0 and 'OK' in done.stdout else f'FAIL (rc={done.returncode}: {done.stderr.strip()[-300:]})'
    for name, value in results.items():
        print(f'{value[:4]}: {name}' + ('' if value == 'PASS' else f' -> {value}'))
    return 0 if all(v == 'PASS' for v in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
