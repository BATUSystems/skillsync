#!/usr/bin/env python3
"""Wrapper around `claude plugin eval` for the skills library (CLAUDE-4).

The only supported entry point for behaviour evals of the skills: it forces the safe flags, passes a clean environment to the child, validates the cases
and stages a COPY of the skills, so the library itself is never touched. The real `claude` binary is called once, after all checks pass."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_SKILLS = ('feature', 'bugfix', 'pre-implement', 'workflow')
MAX_RUNS, MAX_TURNS, MAX_TIMEOUT = 3, 10, 300
READ_ONLY_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'Skill'})
PROMPT_KEYS = frozenset({'description', 'tags', 'runs', 'max_turns', 'timeout_seconds', 'allowed_tools'})
GRADER_TYPES = frozenset({'regex', 'tool_used', 'tool_order', 'file_exists'})
ENV_ALLOWLIST = ('PATH', 'HOME', 'LANG')


def positive_cost(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not a number: {text}')
    if not value > 0 or value != value or value == float('inf'):
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return value


def absolute_dir(text):
    if not os.path.isabs(text):
        raise argparse.ArgumentTypeError(f'must be an absolute path: {text}')
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
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-cost-usd', required=True, type=positive_cost)
    parser.add_argument('--config-dir', required=True, type=absolute_dir, help='absolute path of the Claude profile (account) to use')
    parser.add_argument('--runs', type=runs_count, default=MAX_RUNS)
    parser.add_argument('--case', default=None, help='case name or glob')
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


def build_env(environ, config_dir):
    env = {key: value for key, value in environ.items() if key in ENV_ALLOWLIST or key.startswith('LC_')}
    env['CLAUDE_CONFIG_DIR'] = config_dir
    return env


def frontmatter(path):
    """(dict, error) of the YAML header of a markdown file."""
    text = Path(path).read_text(encoding='utf-8')
    if not text.startswith('---'):
        return None, 'no frontmatter'
    parts = text.split('---', 2)
    if len(parts) < 3:
        return None, 'unterminated frontmatter'
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as error:
        return None, f'invalid YAML ({str(error).splitlines()[0]})'
    return (data, None) if isinstance(data, dict) else (None, 'frontmatter is not a mapping')


def check_prompt(path, model):
    data, error = frontmatter(path)
    if error:
        return [f'{path}: {error}']
    errors = []
    for key in sorted(data):
        if key == 'model' and model and data[key] == model:
            continue
        if key not in PROMPT_KEYS:
            errors.append(f'{path}: key {key!r} is not allowed (closed list: {", ".join(sorted(PROMPT_KEYS))})')
    for key, limit in (('runs', MAX_RUNS), ('max_turns', MAX_TURNS), ('timeout_seconds', MAX_TIMEOUT)):
        if key in data and (not isinstance(data[key], int) or isinstance(data[key], bool) or not 1 <= data[key] <= limit):
            errors.append(f'{path}: {key} must be an integer 1..{limit}')
    if 'allowed_tools' in data:
        tools = data['allowed_tools']
        bad = sorted(str(t) for t in tools if t not in READ_ONLY_TOOLS) if isinstance(tools, list) else ['(not a list)']
        if bad:
            errors.append(f'{path}: allowed_tools outside the read-only set: {", ".join(bad)}')
    return errors


def check_grader(path):
    data, error = frontmatter(path)
    if error:
        return [f'{path}: {error}']
    if data.get('type') not in GRADER_TYPES:
        return [f'{path}: grader type {data.get("type")!r} is not allowed (type must be one of {", ".join(sorted(GRADER_TYPES))})']
    return []


def validate_cases(evals_dir, model=None):
    evals_dir = Path(evals_dir)
    cases = sorted(p for p in evals_dir.iterdir() if p.is_dir()) if evals_dir.is_dir() else []
    if not cases:
        return [f'{evals_dir}: no case found']
    errors = []
    for case in cases:
        if (case / 'case.yaml').exists():
            errors.append(f'{case}: case.yaml is not allowed, the case is defined by prompt.md')
        if not (case / 'prompt.md').is_file():
            errors.append(f'{case}: prompt.md is missing')
        else:
            errors += check_prompt(case / 'prompt.md', model)
        graders = sorted((case / 'graders').glob('*.md')) if (case / 'graders').is_dir() else []
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


def _copy_tree(source, target):
    """Copy a directory without following or recreating symbolic links."""
    def skip_links(directory, names):
        return [n for n in names if (Path(directory) / n).is_symlink()]
    shutil.copytree(source, target, symlinks=True, ignore=skip_links)


def stage_plugin(library, evals_dir, stage, skills):
    plugin = Path(stage) / 'plugin'
    for name in skills:
        if not (Path(library) / 'skills' / name / 'SKILL.md').is_file():
            raise SystemExit(f'error: skill {name!r} is missing in {library}')
    if plugin.exists():
        raise SystemExit(f'error: {plugin} already exists')
    for name in skills:
        _copy_tree(Path(library) / 'skills' / name, plugin / 'skills' / name)
    _copy_tree(evals_dir, plugin / 'evals')
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
    stage_plugin(args.library, args.evals_dir, stage, skills_for(args.library))
    return subprocess.run(command, env=env, cwd=out).returncode


if __name__ == '__main__':
    sys.exit(main())
