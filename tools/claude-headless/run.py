#!/usr/bin/env python3
"""Read-only jobs for the real `claude -p` outside the `feature` pipeline (CLAUDE-6): `release-audit` (J1) and `docs-drift` (J2).

The only supported entry point: it forces the safe flags (`--restricted`, only Read/Grep/Glob, no permission prompts, no MCP, no slash commands, no
persistent session, a hard cost limit and a turn limit, a JSON schema), builds a copy of the PUBLIC part of the core repository from a git ref (never the
working tree), keeps a cost ledger of the account and validates the result itself. The result is advisory: nothing here gates a merge or a release.

Exit codes: 0 PASS; 1 the job failed or its result does not fit the schema; 2 refused before the start (arguments, profile, ledger, size of the input);
3 interrupted by the cost limit. The real `claude` is started once, after every check has passed."""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from claude_headless_core import absolute_dir, build_env, model_name, positive_cost  # noqa: E402

try:
    import fcntl
except ImportError:   # Windows: the ledger is not locked there (the tool is a Linux developer tool)
    fcntl = None

TOTAL_BUDGET_USD = 12.0
MAX_CALLS = 16
DEFAULT_MAX_TURNS, MAX_TURNS_CEILING = 15, 30
MAX_INPUT_BYTES = 400 * 1024   # the text that the host puts in front of the model: `_audit/diff.patch` and `_audit/diffstat.txt`
CLAUDE_TIMEOUT_SECONDS = 900
DEFAULT_LEDGER = Path.home() / '.local' / 'state' / 'skillsync' / 'claude-headless-ledger.json'
DEFAULT_PROFILES = Path.home() / '.local' / 'state' / 'skillsync' / 'claude-headless-profiles.txt'   # one allowed profile directory per line
TASKS = {'release-audit': ('tag_from', 'tag_to'), 'docs-drift': ('ref',)}
AUDIT_DIR = '_audit'
# The allowlist of the public paths that reach the copy (never a denylist): the ops repository, the organization and personal layers, `.skillsync`,
# `docs/plans`, `docs/specs`, tests and tools are not on it.
ALLOWED_FILES = ('CHANGELOG.md', 'README.md', 'skillsync', 'skillsync-ctl')
ALLOWED_DIRS = ('docs/domain', 'docs/lessons', 'library', 'skillsync_workflow')
DOCS_LEVEL = 'docs'                                   # `docs/*.md` only (one level)
PATCH_FILES = ('CHANGELOG.md', 'library/CHANGELOG.md')   # plus `docs/*.md`: the full patch; everything else gets a diffstat only
REF_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
SCHEMA_KEYWORDS = frozenset({'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'minLength', 'maxLength', 'minimum', 'maximum',
                             'minItems', 'maxItems'})
ENFORCED_FLAGS = ('-p', '--output-format', 'json', '--no-session-persistence', '--permission-prompts', 'none', '--restricted', '--tools',
                  'Read,Grep,Glob', '--strict-mcp-config', '--disable-slash-commands')


class Refusal(Exception):
    """A refusal before the start (exit code 2)."""


def git_ref(text):
    if not REF_RE.fullmatch(text) or '..' in text:
        raise argparse.ArgumentTypeError(f'not a plain tag or branch name: {text!r}')
    return text


def allowed_profiles(path):
    """The real paths listed in the file of the allowed profiles (one per line, `#` starts a comment); a missing file allows nothing."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return set()
    return {os.path.realpath(line.strip()) for line in lines if line.strip() and not line.lstrip().startswith('#')}


def check_profile(config_dir, profiles_file):
    """Default deny: only a profile directory listed in the file of the allowed profiles may run these jobs (so the work account is never used by mistake,
    whatever its name or the link that points to it)."""
    if os.path.realpath(config_dir) not in allowed_profiles(profiles_file):
        raise Refusal(f'the profile {config_dir} is not listed in {profiles_file} (one allowed profile directory per line)')


def turns(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f'not an integer: {text}')
    if not 1 <= value <= MAX_TURNS_CEILING:
        raise argparse.ArgumentTypeError(f'must be 1..{MAX_TURNS_CEILING}')
    return value


def lowered_budget(text):
    value = positive_cost(text)
    if value > TOTAL_BUDGET_USD:
        raise argparse.ArgumentTypeError(f'the total budget can only be lowered, at most {TOTAL_BUDGET_USD} USD')
    return value


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('task', choices=sorted(TASKS))
    parser.add_argument('--model', required=True, type=model_name)
    parser.add_argument('--max-cost-usd', required=True, type=positive_cost, help='the limit of this call (required)')
    parser.add_argument('--config-dir', required=True, type=absolute_dir, help='absolute path of the Claude profile; it must be listed in the profiles file')
    parser.add_argument('--profiles-file', default=str(DEFAULT_PROFILES), help='the allowed profiles, one directory per line')
    parser.add_argument('--tag-from', type=git_ref)
    parser.add_argument('--tag-to', type=git_ref)
    parser.add_argument('--ref', type=git_ref)
    parser.add_argument('--repo', default=str(ROOT))
    parser.add_argument('--max-turns', type=turns, default=DEFAULT_MAX_TURNS)
    parser.add_argument('--total-budget-usd', type=lowered_budget, default=TOTAL_BUDGET_USD)
    parser.add_argument('--ledger', default=str(DEFAULT_LEDGER))
    parser.add_argument('--claude-bin', default='claude')
    parser.add_argument('--out', default=None, help='output directory (default .skillsync/claude-headless/<time> in the current directory)')
    parser.add_argument('--dry-run', action='store_true', help='print the command and the environment, build nothing, start nothing')
    args = parser.parse_args(argv)
    missing = [name for name in TASKS[args.task] if getattr(args, name) is None]
    if missing:
        parser.error(f'task {args.task} needs: ' + ', '.join('--' + name.replace('_', '-') for name in missing))
    extra = [name for name in ('tag_from', 'tag_to', 'ref') if name not in TASKS[args.task] and getattr(args, name) is not None]
    if extra:
        parser.error(f'task {args.task} does not take: ' + ', '.join('--' + name.replace('_', '-') for name in extra))
    return args


# --- schema: a small validator of the subset that our schemas use (the repository has no `jsonschema` library)

def check_schema_keywords(schema, where='$'):
    """Refuse a schema that uses a keyword the validator does not know (it would be silently ignored)."""
    errors = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in SCHEMA_KEYWORDS:
                errors.append(f'{where}: unsupported keyword {key!r}')
            elif key == 'properties' and isinstance(value, dict):
                for name, sub in value.items():
                    errors += check_schema_keywords(sub, f'{where}.{name}')
            elif key == 'items':
                errors += check_schema_keywords(value, f'{where}[]')
    return errors


def _type_ok(kind, value):
    if kind == 'object':
        return isinstance(value, dict)
    if kind == 'array':
        return isinstance(value, list)
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'boolean':
        return isinstance(value, bool)
    if kind == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'null':
        return value is None
    return False


def validate(instance, schema, where='$'):
    """The errors of `instance` against the supported subset of JSON Schema, `[text]`; an empty list means valid."""
    errors = []
    kind = schema.get('type')
    if kind is not None and not _type_ok(kind, instance):
        return [f'{where}: expected {kind}, got {type(instance).__name__}']
    if 'enum' in schema and instance not in schema['enum']:
        errors.append(f'{where}: {instance!r} is not one of {schema["enum"]}')
    if isinstance(instance, str):
        if 'minLength' in schema and len(instance) < schema['minLength']:
            errors.append(f'{where}: shorter than {schema["minLength"]}')
        if 'maxLength' in schema and len(instance) > schema['maxLength']:
            errors.append(f'{where}: longer than {schema["maxLength"]}')
    if _type_ok('number', instance):
        if 'minimum' in schema and instance < schema['minimum']:
            errors.append(f'{where}: below {schema["minimum"]}')
        if 'maximum' in schema and instance > schema['maximum']:
            errors.append(f'{where}: above {schema["maximum"]}')
    if isinstance(instance, dict):
        for name in schema.get('required', []):
            if name not in instance:
                errors.append(f'{where}: missing required {name!r}')
        properties = schema.get('properties', {})
        for name, value in instance.items():
            if name in properties:
                errors += validate(value, properties[name], f'{where}.{name}')
            elif schema.get('additionalProperties') is False:
                errors.append(f'{where}: unexpected property {name!r}')
    if isinstance(instance, list):
        if 'minItems' in schema and len(instance) < schema['minItems']:
            errors.append(f'{where}: fewer than {schema["minItems"]} items')
        if 'maxItems' in schema and len(instance) > schema['maxItems']:
            errors.append(f'{where}: more than {schema["maxItems"]} items')
        if 'items' in schema:
            for index, value in enumerate(instance):
                errors += validate(value, schema['items'], f'{where}[{index}]')
    return errors


# --- the job: prompt, schema, command

def load_job(task):
    folder = HERE / 'jobs' / task
    schema_text = (folder / 'schema.json').read_text(encoding='utf-8')
    schema = json.loads(schema_text)
    problems = check_schema_keywords(schema)
    if problems:
        raise Refusal('schema of the job: ' + '; '.join(problems))
    prompt = (folder / 'prompt.md').read_text(encoding='utf-8')
    return schema, json.dumps(schema, separators=(',', ':')), prompt


def render_prompt(prompt, args):
    values = {'{FROM}': args.tag_from or '', '{TO}': args.tag_to or '', '{REF}': args.ref or ''}
    for key, value in values.items():
        prompt = prompt.replace(key, value)
    return prompt


def build_command(args, schema_text):
    return [args.claude_bin, *ENFORCED_FLAGS, '--json-schema', schema_text, '--max-budget-usd', str(args.max_cost_usd), '--max-turns', str(args.max_turns),
            '--model', args.model]


# --- git: the copy of the public part of a ref

def git(repo, *argv, binary=False):
    result = subprocess.run(['git', '-C', str(repo), *argv], capture_output=True, env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode != 0:
        raise Refusal(f'git {argv[0]} failed: {result.stderr.decode("utf-8", "replace").strip()[:300]}')
    return result.stdout if binary else result.stdout.decode('utf-8')


def resolve_ref(repo, ref):
    return git(repo, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}').strip()


def present_paths(repo, commit):
    """The allowlisted paths that exist in the commit (a path missing in an old tag would make `git archive` fail), with `docs/*.md` as concrete files."""
    listed = git(repo, 'ls-tree', '--name-only', commit, '--', *ALLOWED_FILES, *ALLOWED_DIRS).splitlines()
    docs = []
    for line in git(repo, 'ls-tree', commit, f'{DOCS_LEVEL}/').splitlines():
        meta, _, name = line.partition('\t')
        if meta.split()[1] == 'blob' and name.endswith('.md'):
            docs.append(name)
    return sorted(set(listed) | set(docs))


def extract_tar(data, dest):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
        for member in archive:
            parts = Path(member.name).parts
            if member.isdir():
                continue
            if not member.isreg() or member.name.startswith('/') or '..' in parts:
                continue   # links, devices and unsafe names are never written
            if not allowed_relative(Path(member.name)):
                continue   # first layer: a path outside the allowlist (also one nested in an allowed directory) is never written; `scan_copy` is the second
            target = Path(dest) / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def allowed_relative(relative):
    if any(part in ('.git', '.skillsync') for part in relative.parts):
        return False
    text = relative.as_posix()
    if relative.parts[0] == AUDIT_DIR:
        return True
    if text in ALLOWED_FILES:
        return True
    if any(text == directory or text.startswith(directory + '/') for directory in ALLOWED_DIRS):
        return True
    return len(relative.parts) == 2 and relative.parts[0] == DOCS_LEVEL and text.endswith('.md')


def scan_copy(dest):
    """The canary after the build: every file must be on the allowlist (or in `_audit/`); a leftover refuses the start. `[path]`."""
    found = []
    for path in sorted(Path(dest).rglob('*')):
        if path.is_file() or path.is_symlink():
            relative = path.relative_to(dest)
            if not allowed_relative(relative):
                found.append(relative.as_posix())
    return found


def build_copy(repo, commit, dest):
    paths = present_paths(repo, commit)
    if not paths:
        raise Refusal(f'no allowlisted path exists in {commit}')
    extract_tar(git(repo, 'archive', '--format=tar', commit, '--', *paths, binary=True), dest)
    leftovers = scan_copy(dest)
    if leftovers:
        raise Refusal('the copy holds paths outside the allowlist: ' + ', '.join(leftovers[:5]))


def write_audit_files(repo, dest, from_commit, to_commit):
    """`_audit/diff.patch` (full patch of the changelogs and `docs/*.md`) and `_audit/diffstat.txt` (statistics of the rest); the size of both."""
    target = Path(dest) / AUDIT_DIR
    target.mkdir()
    docs = sorted(set(path for commit in (from_commit, to_commit) for path in present_paths(repo, commit) if path.startswith(DOCS_LEVEL + '/') and path.count('/') == 1 and path.endswith('.md')))
    patch_paths = list(PATCH_FILES) + docs
    rest = [path for path in (*ALLOWED_FILES, *ALLOWED_DIRS) if path not in PATCH_FILES]
    patch = git(repo, 'diff', '--no-color', '--no-ext-diff', from_commit, to_commit, '--', *patch_paths)
    stat = git(repo, 'diff', '--no-color', '--no-ext-diff', '--stat=200', from_commit, to_commit, '--', *rest)
    (target / 'diff.patch').write_text(patch, encoding='utf-8')
    (target / 'diffstat.txt').write_text(stat, encoding='utf-8')
    return len(patch.encode('utf-8')) + len(stat.encode('utf-8'))


# --- the ledger of the account

class Ledger:
    """`{calls, spent, entries}` of the account in one file, written under a file lock (the lock file lives next to it)."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(self.path) + '.lock', 'w')
        if fcntl:
            fcntl.flock(self.lock, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.lock.close()

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'calls': 0, 'spent': 0.0, 'entries': []}

    def check(self, limit, total_budget):
        data = self.read()
        if data['calls'] + 1 > MAX_CALLS:
            raise Refusal(f'the ledger holds {data["calls"]} calls, at most {MAX_CALLS}')
        if data['spent'] + limit > total_budget:
            raise Refusal(f'the ledger holds {data["spent"]} USD and this call may cost {limit}, over the total {total_budget} USD')

    def record(self, entry, cost):
        data = self.read()
        data['calls'] += 1
        data['spent'] = round(data['spent'] + cost, 6)
        data['entries'].append(entry)
        self.path.write_text(json.dumps(data, indent=1), encoding='utf-8')


# --- the result of the child

def classify(returncode, stdout, schema):
    """`(exit code, reason, structured result or None, cost)` of the output of `claude -p --output-format json`."""
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return 1, 'the output is not JSON', None, 0.0
    if not isinstance(parsed, dict):
        return 1, 'the output is not a JSON object', None, 0.0
    cost = parsed.get('total_cost_usd')
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0
    subtype = parsed.get('subtype')
    if subtype == 'error_max_budget_usd':
        return 3, 'interrupted by the cost limit', None, cost
    if subtype == 'error_max_turns':
        return 1, 'stopped at the turn limit', None, cost
    if parsed.get('is_error') or returncode != 0:
        return 1, f'the job ended with an error ({subtype})', None, cost
    result = parsed.get('structured_output')
    if not isinstance(result, dict):
        return 1, 'no structured output (the result does not fit the schema)', None, cost
    problems = validate(result, schema)
    if problems:
        return 1, 'the result does not fit the schema: ' + '; '.join(problems[:5]), None, cost
    return 0, 'ok', result, cost


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        check_profile(args.config_dir, args.profiles_file)
        schema, schema_text, prompt_template = load_job(args.task)
        prompt = render_prompt(prompt_template, args)
        command = build_command(args, schema_text)
        refs = [args.tag_from, args.tag_to] if args.task == 'release-audit' else [args.ref]
        commits = [resolve_ref(args.repo, ref) for ref in refs]
        if not all(commits):
            raise Refusal('a ref does not exist: ' + ', '.join(ref for ref, commit in zip(refs, commits) if not commit))
        if args.dry_run:
            env = build_env(os.environ, args.config_dir, home='<temporary home>')
            print(json.dumps({'argv': command, 'env': env, 'cwd': '<copy of the public paths of the ref>', 'stdin': prompt, 'refs': dict(zip(refs, commits))}, indent=2))
            return 0
        out = Path(args.out) if args.out else Path.cwd() / '.skillsync' / 'claude-headless' / datetime.now().strftime('%Y%m%d-%H%M%S')
        out = out.resolve()
        with tempfile.TemporaryDirectory(prefix='claude-headless-') as work:
            copy, home = Path(work) / 'copy', Path(work) / 'home'
            copy.mkdir()
            home.mkdir()
            build_copy(args.repo, commits[-1], copy)
            if args.task == 'release-audit':
                size = write_audit_files(args.repo, copy, commits[0], commits[1])
                if size > MAX_INPUT_BYTES:
                    raise Refusal(f'the input of the model is {size} bytes, over {MAX_INPUT_BYTES}: narrow the allowlist or skip this pair of tags')
            with Ledger(args.ledger) as ledger:
                ledger.check(args.max_cost_usd, args.total_budget_usd)
                started = now()
                try:
                    version = subprocess.run([args.claude_bin, '--version'], capture_output=True, text=True, timeout=60).stdout.strip()
                except (OSError, subprocess.SubprocessError) as error:
                    raise Refusal(f'cannot run {args.claude_bin!r}: {error.__class__.__name__}')
                try:
                    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_SECONDS, cwd=copy,
                                            env=build_env(os.environ, args.config_dir, home=home))
                    code, reason, structured, cost = classify(result.returncode, result.stdout, schema)
                except subprocess.TimeoutExpired:
                    result = subprocess.CompletedProcess(command, 124, '', 'timeout')
                    code, reason, structured, cost = 1, f'no answer within {CLAUDE_TIMEOUT_SECONDS} s (the call is booked at its limit)', None, args.max_cost_usd
                ledger.record({'at': started, 'task': args.task, 'model': args.model, 'limit': args.max_cost_usd, 'cost': cost, 'exit': code,
                               'claude_exit': result.returncode}, cost)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'raw.json').write_text(result.stdout, encoding='utf-8')
        (out / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        if structured is not None:
            (out / 'result.json').write_text(json.dumps(structured, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        (out / 'metadata.json').write_text(json.dumps({'task': args.task, 'claude_version': version, 'started': started, 'ended': now(), 'model': args.model,
                                                       'limit_usd': args.max_cost_usd, 'cost_usd': cost, 'max_turns': args.max_turns, 'refs': dict(zip(refs, commits)),
                                                       'exit': code, 'reason': reason}, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'out': str(out), 'exit': code, 'reason': reason, 'cost_usd': cost, 'findings': len(structured['findings']) if structured else None}))
        return code
    except Refusal as error:
        print(f'refused: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
