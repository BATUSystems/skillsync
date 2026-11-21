#!/usr/bin/env python3
"""Wrapper around `claude plugin eval` for the skills library (CLAUDE-4).

The only supported entry point for behaviour evals of the skills: it forces the safe flags, passes a clean environment to the child, validates the cases
and stages a COPY of the skills, so the library itself is never touched. The real `claude` binary is called once, after all checks pass."""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'claude-headless'))   # the shared core of the wrappers (the directory name has a hyphen: no `import` by package)
from claude_headless_core import (ENV_ALLOWLIST, HEADER_RE, MAX_COST_CEILING_USD, MODEL_RE, UniqueKeyLoader, absolute_dir, build_env,  # noqa: E402,F401
                                  frontmatter, model_name, positive_cost)
DEFAULT_SKILLS = ('feature', 'bugfix', 'pre-implement', 'workflow')
MAX_RUNS, MAX_TURNS, MAX_TIMEOUT = 3, 10, 300
READ_ONLY_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'Skill'})
PROMPT_KEYS = frozenset({'description', 'tags', 'runs', 'max_turns', 'timeout_seconds', 'allowed_tools'})
GRADER_TYPES = frozenset({'regex', 'tool_used', 'tool_order', 'file_exists'})
CASE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._*?-]*$')


def case_filter(text):
    if not CASE_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f'not a plain case name or glob: {text!r}')
    return text


def runs_count(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not an integer: {text}')
    if not 1 <= value <= MAX_RUNS:
        raise argparse.ArgumentTypeError(f'must be 1..{MAX_RUNS}')
    return value


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--model', required=True, type=model_name)
    parser.add_argument('--max-cost-usd', required=True, type=positive_cost)
    parser.add_argument('--config-dir', required=True, type=absolute_dir, help='absolute path of the Claude profile (account) to use')
    parser.add_argument('--runs', type=runs_count, default=MAX_RUNS)
    parser.add_argument('--case', default=None, type=case_filter, help='case name or glob')
    parser.add_argument('--claude-bin', default='claude')
    parser.add_argument('--evals-dir', default=str(HERE / 'evals'))
    parser.add_argument('--library', default=str(ROOT / 'library'))
    parser.add_argument('--out', default=None, help='output directory (default .skillsync/claude-eval/<time>)')
    parser.add_argument('--dry-run', action='store_true', help='print the command and environment, run nothing')
    return parser.parse_args(argv)


def build_command(args, stage, out):
    argv = [args.claude_bin, 'plugin', 'eval', str(Path(stage) / 'plugin'), '--no-publish', '--trust-plugin',
            '--max-cost-usd', str(args.max_cost_usd), '--model', args.model, '--runs', str(args.runs),
            '--concurrency', '1', '--ablation', 'with-without',
            '--json', str(Path(out) / 'aggregate.json'), '--output-dir', str(Path(out) / 'results'), '--report', str(Path(out) / 'report.html')]
    if args.case:
        argv += ['--case', args.case]
    return argv


def check_prompt(path, model):
    data, error = frontmatter(path)
    if error:
        return [f'{path}: {error}']
    errors = []
    for key in sorted(data, key=str):
        if key == 'model' and model and data[key] == model:
            continue
        if key not in PROMPT_KEYS:
            errors.append(f'{path}: key {key!r} is not allowed (closed list: {", ".join(sorted(PROMPT_KEYS))})')
    for key, limit in (('runs', MAX_RUNS), ('max_turns', MAX_TURNS), ('timeout_seconds', MAX_TIMEOUT)):
        if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool) or not 1 <= data[key] <= limit):
            errors.append(f'{path}: {key} must be an integer 1..{limit}')
    if 'allowed_tools' in data:
        tools = data['allowed_tools']
        bad = sorted(str(t) for t in tools if not isinstance(t, str) or t not in READ_ONLY_TOOLS) if isinstance(tools, list) else ['(not a list)']
        if bad:
            errors.append(f'{path}: allowed_tools outside the read-only set: {", ".join(bad)}')
    return errors


def check_grader(path):
    data, error = frontmatter(path)
    if error:
        return [f'{path}: {error}']
    kind = data.get('type')
    if not isinstance(kind, str) or kind not in GRADER_TYPES:
        return [f'{path}: grader type {data.get("type")!r} is not allowed (type must be one of {", ".join(sorted(GRADER_TYPES))})']
    return []


def validate_cases(evals_dir, model=None):
    evals_dir = Path(evals_dir)
    cases = sorted(p for p in evals_dir.iterdir() if p.is_dir()) if evals_dir.is_dir() else []
    if not cases:
        return [f'{evals_dir}: no case found']
    errors = []
    for case in cases:
        if case.is_symlink():
            errors.append(f'{case}: a symbolic link is not allowed')
            continue
        for entry in sorted(case.iterdir()):
            if entry.name not in ('prompt.md', 'graders'):
                errors.append(f'{case}: unexpected file {entry.name} (only prompt.md and graders/*.md are allowed)')
        if (case / 'graders').is_symlink():
            errors.append(f'{case}: graders must not be a symbolic link')
        elif (case / 'graders').is_dir():
            for entry in sorted((case / 'graders').iterdir()):
                if not (entry.suffix == '.md' and entry.is_file() and not entry.is_symlink()):
                    errors.append(f'{case}: unexpected file graders/{entry.name} (only regular *.md)')
        if (case / 'prompt.md').is_symlink():
            errors.append(f'{case}: prompt.md must not be a symbolic link')
        if not (case / 'prompt.md').is_file():
            errors.append(f'{case}: prompt.md is missing')
        else:
            errors += check_prompt(case / 'prompt.md', model)
        graders = sorted((case / 'graders').glob('*.md')) if (case / 'graders').is_dir() and not (case / 'graders').is_symlink() else []
        if not graders:
            errors.append(f'{case}: at least one grader is required')
        for grader in graders:
            errors += check_grader(grader)
    return errors


def skills_for(library):
    """Default skills, plus `dispatch` only once the model cannot invoke it on its own (the flag)."""
    skills = list(DEFAULT_SKILLS)
    path = Path(library) / 'skills' / 'dispatch' / 'SKILL.md'
    if path.is_file():
        data, _ = frontmatter(path)
        if data and data.get('disable-model-invocation') is True:
            skills.append('dispatch')
    return tuple(skills)


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise SystemExit(f'error: {path} is not a plain single-link file')
    return info


def _copy_skill(source, target):
    """Copy a skill directory file by file; refuse links (also as the root), special files and hard links."""
    if os.path.islink(source) or not os.path.isdir(source):
        raise SystemExit(f'error: {source} is not a plain directory')
    target.mkdir(parents=True)
    for entry in sorted(Path(source).iterdir()):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            _copy_skill(entry, target / entry.name)
        else:
            _regular(entry)
            shutil.copyfile(entry, target / entry.name)


def _copy_evals(evals_dir, target):
    """Only <case>/prompt.md and <case>/graders/*.md reach the stage."""
    for case in sorted(p for p in Path(evals_dir).iterdir() if p.is_dir() and not p.is_symlink()):
        graders = case / 'graders'
        for source in [case / 'prompt.md', *(sorted(graders.glob('*.md')) if not graders.is_symlink() else [])]:
            if source.is_file() and not source.is_symlink():
                _regular(source)
                destination = target / source.relative_to(evals_dir)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)


def stage_plugin(library, evals_dir, stage, skills):
    plugin = Path(stage) / 'plugin'
    for name in skills:
        if not (Path(library) / 'skills' / name / 'SKILL.md').is_file():
            raise SystemExit(f'error: skill {name!r} is missing in {library}')
    if Path(stage).is_symlink() or plugin.exists():
        raise SystemExit(f'error: {plugin} already exists or the stage is a link')
    for name in skills:
        _copy_skill(Path(library) / 'skills' / name, plugin / 'skills' / name)
    _copy_evals(evals_dir, plugin / 'evals')
    manifest = plugin / '.claude-plugin' / 'plugin.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'name': 'skillsync-eval', 'version': '0.0.0', 'description': 'Copies of Skillsync skills for behaviour evals'}, indent=2) + '\n', encoding='utf-8')
    return plugin


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    errors = validate_cases(args.evals_dir, args.model)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path.cwd() / '.skillsync' / 'claude-eval' / datetime.now().strftime('%Y%m%d-%H%M%S')
    out = out.resolve()
    stage = out / 'stage'
    command, env = build_command(args, stage, out), build_env(os.environ, args.config_dir)
    if args.dry_run:
        print(json.dumps({'argv': command, 'env': env}, indent=2))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    plugin = stage_plugin(args.library, args.evals_dir, stage, skills_for(args.library))
    errors = validate_cases(plugin / 'evals', args.model)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix='claude-eval-cwd-') as empty:
        code = subprocess.run(command, env=env, cwd=empty).returncode
    return 128 - code if code < 0 else code


if __name__ == '__main__':
    sys.exit(main())
