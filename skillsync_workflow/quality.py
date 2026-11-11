"""Planning and evidence-bearing execution of configured quality checks."""
import contextlib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import tomllib
import uuid
import sys
import yaml
from .common import add_project_arguments, changed_files, emit, load_layer_project, matches, workflow_config, contained, normalize_files, read_yaml, strings, redact, expand_project_skills, require_layerset, WORKFLOW_SKILL, LEGACY_WORKFLOW_SKILL
from .selfcmd import self_argv
from . import layers as _layers
from .ceremony import ceremony_info, validate_ceremony
from .delivery_mode import delivery_info, validate_delivery_mode
from .dispatch import validate_dispatch
from .prototype import validate_prototype
from .status import MAX_PLAN_BYTES, SPEC_FULL, SPEC_LINE, _STATUS, is_spec_path, plan_text_needs_human


def plan_validation(root, manifest, library, profile='quick', files=None, check_ids=None):
    config = workflow_config(root, manifest, library)
    checks = list(config.get('validation', {}).get('checks', []))
    legacy_warning = []
    if not checks and manifest.get('tests', {}).get('command'):
        command = manifest['tests']['command']
        build_only = bool(re.match(r'^\s*hugo(?:\s|$)', command))
        checks = [{'id': 'legacy-tests', 'command': command, 'kind': 'build' if build_only else 'test', 'required': True}]
        if build_only:
            legacy_warning.append('Legacy hugo command is build evidence, not executed tests')
    guards = config.get('guardrails', {})
    if guards.get('rules') or guards.get('checks'):
        if any(item.get('id') == 'project-guard' for item in checks):
            raise ValueError('project-guard is reserved for configured guardrails')
        # Own child process: the same layers explicitly in argv (`--layer id=root`, K3).
        argv = self_argv() + ['guard', str(root)] + _layers.layer_argv(library) + ['--run', '--json']
        if files is not None:
            argv += ['--files'] + list(files)
        checks.append({'id': 'project-guard', 'argv': argv, 'kind': 'lint',
                       'profiles': ['quick', 'final'], 'required': True, 'timeout': 60,
                       'resources': ['guard-wrapper:' + str(root)],
                       'success_pattern': '"status": "PASS"'})
    if not isinstance(checks, list):
        raise ValueError('validation.checks must be a list')
    selected, seen = [], set()
    for item in checks:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in seen:
            raise ValueError('Validation checks require unique string ids')
        seen.add(item['id'])
        if check_ids is not None:
            if item['id'] not in check_ids:
                continue
        elif profile != 'all':
            if profile not in item.get('profiles', ['quick', 'final']):
                continue
            if files is not None and item.get('paths') and not any(matches(f, item['paths']) for f in files):
                continue
        check = dict(item)
        argv, command = check.get('argv'), check.get('command')
        if bool(argv) == bool(command):
            raise ValueError(f"Check {check['id']} requires exactly one of argv or command")
        if argv is not None and (not isinstance(argv, list) or not all(isinstance(x, str) for x in argv)):
            raise ValueError('argv must be a list of strings')
        if command is not None and not isinstance(command, str):
            raise ValueError('command must be a trusted manifest string')
        check['cwd'] = str(contained(root, check.get('cwd', '.')))
        check['timeout'] = float(check.get('timeout', 300))
        if not 0 < check['timeout'] <= 86400:
            raise ValueError('timeout must be between 0 and 86400 seconds')
        check.setdefault('required', True)
        selected.append(check)
    unknown = sorted(set(check_ids or []) - seen)
    warnings = legacy_warning + ([f'Unknown check ids: {unknown}'] if unknown else [])
    if not selected:
        warnings.append('No checks selected; no validation was performed')
    return {'status': 'BLOCKED' if unknown or not selected else 'NOT_RUN', 'checks': selected, 'warnings': warnings}


def _redact(text):
    return redact(text)


def _test_counts(output):
    run = skipped = None
    found = re.search(r'Ran (\d+) tests?', output)
    if found:
        run = int(found.group(1))
        skip = re.search(r'skipped=(\d+)', output)
        skipped = int(skip.group(1)) if skip else 0
        run -= skipped
    passed = re.search(r'(\d+) passed', output)
    if passed:
        run = int(passed.group(1))
        skip = re.search(r'(\d+) skipped', output)
        skipped = int(skip.group(1)) if skip else 0
    total = re.search(r'Tests:\s+(\d+) passed,\s+(\d+) total', output)
    if total:
        run, skipped = int(total.group(1)), int(total.group(2)) - int(total.group(1))
    tap = re.search(r'(?m)^1\.\.(\d+)\s*$', output)
    if tap:
        skipped = len(re.findall(r'(?im)^ok\b.*#\s*skip', output))
        run = int(tap.group(1)) - skipped
    if re.search(r'(?i)no tests (?:ran|collected|found)|collected 0 items', output):
        run = 0
    dotnet = re.search(r'Passed:\s*(\d+).*?Skipped:\s*(\d+).*?Total:\s*(\d+)', output)
    if dotnet:
        run, skipped = int(dotnet.group(1)), int(dotnet.group(2))
    karma = re.search(r'Executed (\d+) of (\d+)', output)
    if karma:
        run, skipped = int(karma.group(1)), int(karma.group(2)) - int(karma.group(1))
    return run, skipped


@contextlib.contextmanager
def _locks(resources):
    handles = []
    directory = Path(tempfile.gettempdir()) / 'skillsync-resource-locks'
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError('Resource lock directory must be a real directory')
    if hasattr(os, 'getuid') and metadata.st_uid != os.getuid():
        raise ValueError('Resource lock directory has different owner')
    if os.name != 'nt' and metadata.st_mode & 0o077:
        raise ValueError('Resource lock directory must be private')
    try:
        for resource in sorted(set(resources)):
            lock_path = directory / (hashlib.sha256(str(resource).encode()).hexdigest() + '.lock')
            if lock_path.is_symlink():
                raise ValueError('Resource lock symlink is forbidden')
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or (hasattr(os, 'getuid') and metadata.st_uid != os.getuid()):
                os.close(fd)
                raise ValueError('Resource lock must be an owned regular file')
            handle = os.fdopen(fd, 'a+')
            handles.append(handle)
            if os.name == 'nt':
                import msvcrt
                handle.seek(0); handle.write('0'); handle.flush(); handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


def _terminate_tree(process, taskkill=None):
    if os.name == 'nt':
        cleanup = subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'],
                                 capture_output=True, timeout=10)
        if cleanup.returncode and process.poll() is None:
            raise OSError('Windows process-tree termination failed')
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _reap_after_termination(process, taskkill=None):
    cleanup_error = None
    try:
        _terminate_tree(process, taskkill)
    except BaseException as error:
        cleanup_error = error
    try:
        output = process.communicate(timeout=10)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if process.poll() is None:
            process.kill()
        for pipe in (process.stdout, process.stderr):
            if pipe:
                pipe.close()
        process.wait(timeout=10)
        raise OSError('Process-tree cleanup did not drain pipes') from None
    if cleanup_error is not None:
        raise cleanup_error
    return output


def _execute(check):
    taskkill = shutil.which('taskkill') if os.name == 'nt' else None
    if os.name == 'nt' and not taskkill:
        raise OSError('Windows requires taskkill for enforceable process-tree timeouts')
    process = subprocess.Popen(check.get('argv') or check['command'], cwd=check['cwd'],
        shell='command' in check, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name != 'nt')
    try:
        stdout, stderr = process.communicate(timeout=check.get('timeout', 300))
    except subprocess.TimeoutExpired:
        stdout, stderr = _reap_after_termination(process, taskkill)
        return process.returncode, stdout + b'\n' + stderr, True
    except BaseException:
        _reap_after_termination(process, taskkill)
        raise
    return process.returncode, stdout + b'\n' + stderr, False


def log_directory(root, log_dir=None):
    root = Path(root).resolve()
    path = Path(log_dir) if log_dir is not None else Path('.skillsync/logs') / uuid.uuid4().hex
    if '..' in path.parts:
        raise ValueError('Log directory traversal forbidden')
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            raise ValueError('Log directory must be inside project') from None
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Log storage symlinks are forbidden')
    return contained(root, path)


def run_validation(root, checks, *, log_dir=None):
    try:
        directory = log_directory(root, log_dir)
    except (OSError, ValueError) as error:
        return {'status': 'BLOCKED', 'results': [], 'reason': str(error)}
    results = []
    interrupted = False
    for check in checks:
        result = {'id': check['id'], 'status': 'BLOCKED', 'exit_code': None,
                  'required': check.get('required', True), 'tests_run': None, 'tests_skipped': None}
        start = time.monotonic()
        if interrupted:
            result.update(status='NOT_RUN', reason='Validation series interrupted; check not started', duration_seconds=0)
            results.append(result)
            continue
        missing = [key for key in check.get('requires_env', []) if not os.environ.get(key)]
        executables = check.get('requires_executables', check.get('executables', []))
        missing += [f'executable:{x}' for x in executables if not shutil.which(x)]
        if check.get('argv') and not shutil.which(check['argv'][0]):
            local = Path(check['cwd']) / check['argv'][0]
            if not local.is_file():
                missing.append('command executable')
        if missing:
            result['reason'] = f'Missing prerequisites: {", ".join(missing)}'
        elif not Path(check['cwd']).is_dir():
            result['reason'] = 'Missing working directory'
        else:
            try:
                with _locks(check.get('resources') or ['project:' + str(Path(root).resolve())]):
                    code, raw, timeout = _execute(check)
                output = raw.decode('utf-8', errors='replace')
                log_directory(root, directory)
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                log = directory / (hashlib.sha256(check['id'].encode()).hexdigest()[:12] + '.log')
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                    handle.write(_redact(output))
                result.update(exit_code=code, log=str(log))
                result['tests_run'], result['tests_skipped'] = _test_counts(output)
                if timeout:
                    result['reason'] = 'Timeout; process group terminated'
                elif code != 0:
                    result.update(status='FAIL', reason='Nonzero exit code')
                elif check.get('success_pattern') and not re.search(check['success_pattern'], output):
                    result['reason'] = 'Required success evidence missing'
                elif check.get('kind', 'test') in ('test', 'tests', 'integration', 'e2e') and result['tests_run'] == 0:
                    result['reason'] = 'Zero executed tests'
                elif check.get('kind', 'test') in ('test', 'tests', 'integration', 'e2e') and result['tests_run'] is None and not check.get('success_pattern'):
                    result['reason'] = 'Test count unknown; configure success_pattern for this runner'
                elif check.get('kind', 'test') in ('test', 'tests', 'integration', 'e2e') and result['tests_skipped'] and not check.get('allow_skips', False):
                    result['reason'] = 'Skipped tests require explicit disposition'
                else:
                    result.update(status='PASS', reason='Check completed with required evidence')
            except KeyboardInterrupt:
                interrupted = True
                result['reason'] = 'Interrupted; process tree terminated and reaped; series stopped'
            except (OSError, ValueError, re.error) as error:
                result['reason'] = _redact(str(error))
        result['duration_seconds'] = round(time.monotonic() - start, 6)
        results.append(result)
    required = [r for r in results if r['required']]
    status = 'BLOCKED' if not required else ('FAIL' if any(r['status'] == 'FAIL' for r in required) else ('BLOCKED' if any(r['status'] != 'PASS' for r in required) else 'PASS'))
    return {'status': 'BLOCKED' if interrupted else status, 'results': results, 'interrupted': interrupted}


_LAYER_REFERENCE = re.compile(r'layer:([a-z0-9][a-z0-9-]*):(.*)', re.DOTALL)
SKILL_HOSTS = ('.github/skills', '.agents/skills', '.claude/skills')


def _owner_reference(layerset, name):
    """(Layer, relative path) of a layer reference: `layer:<id>:<path>` (an active layer) or the legacy
    `library:<path>` (= the core layer); None for a project path."""
    if name.startswith('library:'):
        layer = layerset.core
        if layer is None:
            raise ValueError(f'No active core layer for instruction path: {name}')
        return layer, name[len('library:'):]
    if name.startswith('layer:'):
        match = _LAYER_REFERENCE.fullmatch(name)
        if not match:
            raise ValueError(f'Invalid layer reference (expected layer:<id>:<path>): {name}')
        layer = layerset.get(match.group(1))
        if layer is None:
            raise ValueError(f'Layer {match.group(1)} of instruction path is not active for this project: {name}')
        return layer, match.group(2)
    return None


def _composite_matches(target, files):
    """True when the directory `target` holds exactly the files of `files` ({rel: (Layer, Path)}) and every
    entry resolves to its owner's file (a composite built by `link`, K4)."""
    present = set()
    for current, _dirs, names in os.walk(target):
        for name in names:
            present.add((Path(current) / name).relative_to(target).as_posix())
    if present != set(files):
        return False
    return all(os.path.realpath(target / rel) == os.path.realpath(path) for rel, (_layer, path) in files.items())


def skill_link_state(layerset, name, linked):
    """State of an installed skill symlink: `ok` (the skill directory of its only providing layer, or a
    composite of its layers in the cache, K4), `stale` (a composite of this skill that no longer matches
    the layers) or `foreign`."""
    target = Path(os.path.realpath(linked))
    try:
        files = layerset.skill_files(name)
    except _layers.LayerError:
        return 'foreign'
    owners = {layer.id for layer, _path in files.values()}
    providers = layerset.skill_providers(name)
    if len(owners) <= 1 and providers:
        owner = next((layer for layer, _path in files.values()), providers[-1])
        if target == Path(os.path.realpath(owner.root / 'skills' / name)):
            return 'ok'
    composed = Path(os.path.realpath(_layers.composed_root() / name))
    if target.parent == composed and not target.name.endswith('.orphans'):
        return 'ok' if files and _composite_matches(target, files) else 'stale'
    return 'foreign'


def _installed_source(root, layerset, parts):
    """Layer file behind a path inside an installed skill symlink (`.github/skills/<n>/...`), or None.

    `ok` and `stale` links (a composite that no longer matches the layers; `doctor` reports it) map to
    the owner's file from `skill_files`; a file the layers do not have gives a path that does not exist
    in the last providing layer (a missing file, not an escape). Foreign links: None."""
    for prefix in SKILL_HOSTS:
        base = Path(prefix).parts
        if parts[:len(base)] == base and len(parts) > len(base):
            skill = parts[len(base)]
            installed = Path(root) / prefix / skill
            if not installed.is_symlink():
                return None
            state = skill_link_state(layerset, skill, installed)
            if state not in ('ok', 'stale'):
                return None
            rest = Path(*parts[len(base) + 1:]).as_posix() if len(parts) > len(base) + 1 else ''
            files = layerset.skill_files(skill)
            if rest in files:
                return files[rest][1]
            if state == 'ok':
                return contained(installed.resolve(), rest or '.')
            providers = layerset.skill_providers(skill)
            owner = providers[-1].root / 'skills' / skill if providers else installed.resolve()
            return contained(owner, rest or '.')
    return None


def source_path(root, library, name):
    """File of an instruction path: `layer:<id>:<path>` (a layer), `library:<path>` (the core layer),
    a path inside an installed skill symlink (the owning layer's file) or a project path."""
    if not isinstance(name, str) or not name:
        raise ValueError('Instruction path must be nonempty string')
    layerset = require_layerset(library, 'source_path')
    reference = _owner_reference(layerset, name)
    source = reference[1] if reference else name
    if not source or Path(source).is_absolute() or '..' in Path(source).parts or '\\' in source:
        raise ValueError(f'Instruction path escapes its owner: {name}')
    if reference:
        layer = reference[0]
        candidate = contained(layer.root, source)
        if not _layers._clean(layer.root / source, layer.root):
            raise ValueError(f'Instruction path is a symlink/junction in layer {layer.id}: {name}')
        return candidate
    found = _installed_source(root, layerset, Path(source).parts)
    return found if found is not None else contained(root, source)


KNOWLEDGE_SOURCES = {
    'lesson': ('lessons', [('layers', 'workflow/lessons'), ('lessons', '.')]),
    'domain': ('entries', [('domain', '.')]),
}


def _logical_path(project, setting):
    """Join and normalise without following symlinks, so a `docs` symlink keeps its logical parent."""
    return Path(os.path.normpath(project / setting))


def domain_location(root, manifest, library):
    """One shared domain knowledge directory per project (backend and frontend share it).

    `workflow.knowledge.domain` may point to `docs/domain` in the project or in its parent
    directory (e.g. `../docs/domain` for separate backend/frontend projects). Record `paths`
    are relative to the directory that contains `docs/`.
    """
    setting = workflow_config(root, manifest or {}, library).get('knowledge', {}).get('domain', 'docs/domain')
    if not isinstance(setting, str) or not setting or Path(setting).is_absolute() or '\\' in setting:
        raise ValueError('workflow.knowledge.domain must be a relative path')
    if Path(setting).parts[-2:] != ('docs', 'domain'):
        raise ValueError('workflow.knowledge.domain must end with docs/domain')
    project = Path(root).resolve()
    directory = _logical_path(project, setting)
    if not directory.is_relative_to(project.parent):
        raise ValueError(f'Domain knowledge path escapes the project parent: {setting}')
    return directory, directory.parent.parent


def lessons_location(root, manifest, library):
    """Project lessons directory: `workflow.knowledge.lessons`, default next to the domain directory."""
    domain_dir, _ = domain_location(root, manifest, library)
    setting = workflow_config(root, manifest or {}, library).get('knowledge', {}).get('lessons')
    if setting is None:
        return domain_dir.parent / 'lessons'
    if not isinstance(setting, str) or not setting or Path(setting).is_absolute() or '\\' in setting:
        raise ValueError('workflow.knowledge.lessons must be a relative path')
    if Path(setting).parts[-2:] != ('docs', 'lessons'):
        raise ValueError('workflow.knowledge.lessons must end with docs/lessons')
    project = Path(root).resolve()
    directory = _logical_path(project, setting)
    if not directory.is_relative_to(project.parent):
        raise ValueError(f'Lessons path escapes the project parent: {setting}')
    return directory


def _validated_record(record, kind, index):
    if not isinstance(record, dict) or not isinstance(record.get('path'), str) or not isinstance(record.get('title'), str):
        raise ValueError(f'Invalid {kind} record: {index}')
    strings(record.get('tags', []), f'{kind}.tags')
    strings(record.get('paths', []), f'{kind}.paths')
    return record


def _layer_lessons(layerset, kind, key):
    """Lesson records of the layers: union of `workflow/lessons/index.yaml` + `index.d/` by `path` (K2);
    the lesson file per path from the layer that owns it (later wins)."""
    merged = require_layerset(layerset, 'knowledge_indexes').merged_file('workflow/lessons/index.yaml')
    last_source = {}
    for layer_id, index in merged.sources:
        data = read_yaml(index)
        if data.get('version', 1) != 1 or not isinstance(data.get(key, []), list):
            raise ValueError(f'Invalid {kind} index: {index}')
        last_source[layer_id] = index
    selected = []
    for record in merged.data.get(key, []):
        owner = merged.owners.get(f'lessons:{record.get("path")}') if isinstance(record, dict) else None
        index = last_source.get(owner) or (merged.sources[-1][1] if merged.sources else 'workflow/lessons/index.yaml')
        _validated_record(record, kind, index)
        rel = record['path']
        if Path(rel).is_absolute() or '..' in Path(rel).parts or '\\' in rel:
            raise ValueError(f'Path escapes project: {rel}')
        found = layerset.lesson_file(rel)
        if not found:
            raise ValueError(f'Missing indexed {kind}: workflow/lessons/{rel}')
        selected.append({**record, 'path': str(found[1]), 'index': str(index)})
    return selected


def knowledge_indexes(root, library, kind, manifest=None):
    key, sources = KNOWLEDGE_SOURCES[kind]
    owners = {'root': root}
    if kind == 'domain':
        owners['domain'] = domain_location(root, manifest, library)[0]
    else:
        owners['lessons'] = lessons_location(root, manifest, library)
    selected = []
    for owner, relative in sources:
        if owner == 'layers':
            selected.extend(_layer_lessons(library, kind, key))
            continue
        directory = contained(owners[owner], relative)
        index = contained(directory, 'index.yaml')
        if not index.exists():
            continue
        data = read_yaml(index)
        if data.get('version', 1) != 1 or not isinstance(data.get(key, []), list):
            raise ValueError(f'Invalid {kind} index: {index}')
        for record in data.get(key, []):
            _validated_record(record, kind, index)
            path = contained(directory, record['path'])
            if not path.is_file():
                raise ValueError(f'Missing indexed {kind}: {path}')
            selected.append({**record, 'path': str(path), 'index': str(index)})
    return selected


def lesson_indexes(root, library, manifest=None):
    return knowledge_indexes(root, library, 'lesson', manifest)


def domain_indexes(root, library, manifest=None):
    return knowledge_indexes(root, library, 'domain', manifest)


def uninstalled_skill(name, skills):
    """Skill name when `name` points into an installed-skills directory of a skill the project does not use."""
    parts = Path(name).parts if isinstance(name, str) else ()
    for prefix in ('.github/skills', '.agents/skills', '.claude/skills'):
        base = Path(prefix).parts
        if parts[:len(base)] == base and len(parts) > len(base) and parts[len(base)] not in skills:
            return parts[len(base)]
    return None


def route(root, manifest, library, files, topics):
    files = normalize_files(root, files)
    config = workflow_config(root, manifest, library)
    selected = [rule for rule in config.get('routing', {}).get('rules', []) if
        any(matches(f, rule.get('paths', [])) for f in files) or set(topics) & set(rule.get('topics', []))]
    skills = expand_project_skills(manifest, library)
    skipped = []
    routed = []
    for rule in selected:
        kept = []
        for name in rule.get('instructions', []):
            skill = uninstalled_skill(name, skills)
            if skill:
                skipped.append({'instruction': name, 'reason': f'skill not installed: {skill}'})
            else:
                kept.append(name)
        routed.append({**rule, 'instructions': kept} if 'instructions' in rule else rule)
    selected = routed
    def relevant(record):
        return set(topics) & set(record.get('tags', [])) or any(matches(f, record.get('paths', [])) for f in files)
    lessons = [record for record in lesson_indexes(root, library, manifest) if relevant(record)]
    _, base = domain_location(root, manifest, library)
    shared_files = [(Path(root).resolve() / f).relative_to(base).as_posix() for f in files]
    domain = [record for record in domain_indexes(root, library, manifest) if record.get('load') == 'bootstrap'
              or 'domain' in topics
              or set(topics) & set(record.get('tags', []))
              or any(matches(f, record.get('paths', [])) for f in shared_files)]
    return {'status': 'NOT_RUN', 'files': files, 'topics': topics, 'routes': selected, 'skipped': skipped,
            'lessons': lessons, 'domain': domain, 'delivery': delivery_info(manifest),
            'ceremony': ceremony_info(manifest),
            'note': 'Context selection only; instructions, lessons and domain records were not executed'}


def _tree_hash(path):
    digest = hashlib.sha256()
    for file in sorted(item for item in Path(path).rglob('*') if item.is_file()):
        relative = file.relative_to(path)
        if '.git' in relative.parts:
            continue
        data = file.read_bytes()
        if b'\0' not in data[:8000]:
            data = data.replace(b'\r\n', b'\n')
        digest.update(relative.as_posix().encode() + b'\0')
        digest.update(data + b'\0')
    return digest.hexdigest()


INSTRUCTION_FILES = ['CLAUDE.md', 'AGENTS.md', '.github/copilot-instructions.md']
AGENTS_BEGIN, AGENTS_END = '<!-- SKILLSYNC:CODEX-BEGIN -->', '<!-- SKILLSYNC:CODEX-END -->'
CODEX_NOTE_BEGIN = '<!-- SKILLSYNC:CODEX-NOTE-BEGIN -->'


CLAUDE_MD_HEADER = '# CLAUDE.md\n\nThis file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.\n\n'


def generated_claude_import(root):
    """Generated CLAUDE.md (skillsync header) that only imports AGENTS.md. A handwritten `@AGENTS.md` is not ours."""
    path = Path(root) / 'CLAUDE.md'
    if not path.is_file() or path.is_symlink():
        return False
    text = path.read_text(encoding='utf-8')
    return text.startswith(CLAUDE_MD_HEADER) and text[len(CLAUDE_MD_HEADER):].strip() == '@AGENTS.md'


def claude_imports_agents(root):
    """`agents` entrypoint mode, inferred from artifacts: the generated import, or the Codex note that only the
    `agents` block carries (also with `claude: false`)."""
    path = Path(root) / 'AGENTS.md'
    note = path.is_file() and CODEX_NOTE_BEGIN in path.read_text(encoding='utf-8')
    return generated_claude_import(root) or note


def agents_block_body(root):
    """Instruction body of the skillsync block in AGENTS.md (without the Codex note), '' when missing or malformed."""
    path = Path(root) / 'AGENTS.md'
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    if text.count(AGENTS_BEGIN) != 1 or text.count(AGENTS_END) != 1:
        return ''
    return text.split(AGENTS_BEGIN, 1)[1].split(AGENTS_END, 1)[0].split(CODEX_NOTE_BEGIN, 1)[0].strip()


INSTRUCTION_PATH_EXTENSIONS = ('md', 'yaml', 'yml', 'json', 'py', 'sh', 'ps1', 'toml', 'ts', 'cs', 'html')
INSTRUCTION_WARNING_LIMIT = 30


def _outside_fences(text):
    """Drop fenced code blocks (also indented, ``` or ~~~) line by line."""
    kept, fence = [], None
    for line in text.splitlines():
        marker = re.match(r'\s*(`{3,}|~{3,})', line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
        elif marker:
            fence = marker.group(1)
        else:
            kept.append(line)
    return '\n'.join(kept)


def instruction_ignores(config):
    """Return (path globs, skill globs, error) from workflow.doctor.ignore_instruction_drift."""
    entries = config.get('doctor', {}).get('ignore_instruction_drift', [])
    if not isinstance(entries, list) or len(entries) > 50:
        return [], [], 'workflow.doctor.ignore_instruction_drift must be a list of at most 50 strings'
    paths, skill_globs = [], []
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip() or len(entry) > 200:
            return [], [], 'workflow.doctor.ignore_instruction_drift entries must be non-empty strings of at most 200 characters'
        if entry.startswith('skill:'):
            skill_globs.append(entry[6:])
        else:
            paths.append(entry[5:] if entry.startswith('path:') else entry)
    return paths, skill_globs, None


def instruction_drift(root, library, skills, ignore_paths=(), ignore_skills=()):
    """Warnings about paths and skills named in generated instructions that do not exist (in the project
    or in any active layer)."""
    layerset = require_layerset(library, 'instruction_drift')
    root = Path(root)
    layer_roots = list(layerset.roots)
    bases = [layer / base for layer in layer_roots for base in ('', 'skills', 'skills/workflow', 'skills/workflow/references')]
    installed = set(skills)
    missing, skill_problems = {}, {}
    for name in INSTRUCTION_FILES:
        path = root / name
        try:
            if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                continue
            text = _outside_fences(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError):
            continue
        candidates = [(item.split('#', 1)[0], True) for item in re.findall(r'\]\(([^)\s]+)\)', text)]
        for token in re.findall(r'`([^`\n]+)`', text):
            if re.fullmatch(r'[\w./-]+', token):
                candidates.append((token, False))
        skill_names = re.findall(r'(?i)\bskill(?:a|u|e|i|em)?\s+`([A-Za-z0-9][\w.-]*)`', text)
        for candidate, is_link in candidates:
            if not candidate or re.match(r'[a-z][a-z0-9+.-]*:', candidate, re.I) or candidate.startswith(('/', '#')):
                continue
            link = re.search(r'skills/([A-Za-z0-9][\w.-]*)/SKILL\.md$', candidate)
            if link:
                skill_names.append(link.group(1))
                continue
            relative = candidate[2:] if candidate.startswith('./') else candidate
            if '..' in relative.split('/') or re.search(r'[<>{}*$~\s]', relative) or relative.startswith(('.skillsync/', '.claude/', '.agents/', '.codex/')):
                continue
            if relative.rsplit('.', 1)[-1] not in INSTRUCTION_PATH_EXTENSIONS and not (is_link and relative.endswith('/')):
                continue
            if not is_link:
                first = relative.split('/')[0]
                if '/' not in relative or relative.endswith('/') or not any((base / first).is_dir() for base in [root] + layer_roots):
                    continue
            if any(fnmatch.fnmatchcase(candidate, glob) for glob in ignore_paths):
                continue
            known = [root / relative, path.parent / relative] + [base / relative for base in bases]
            if not any(item.exists() for item in known):
                missing.setdefault(candidate, []).append(name)
        for skill in skill_names:
            if any(fnmatch.fnmatchcase(skill, glob) for glob in ignore_skills):
                continue
            if '..' in skill or not layerset.providers(f'skills/{skill}/SKILL.md'):
                skill_problems.setdefault(f'Skill named in instructions is not in the library: {skill}', [])
            elif skill not in installed:
                skill_problems.setdefault(f'Skill named in instructions is not installed in the project: {skill}', [])
    messages = [f'Missing path referenced in instructions: {candidate} ({", ".join(dict.fromkeys(files))})'
                for candidate, files in missing.items()] + list(skill_problems)
    if len(messages) > INSTRUCTION_WARNING_LIMIT:
        extra = len(messages) - INSTRUCTION_WARNING_LIMIT
        messages = messages[:INSTRUCTION_WARNING_LIMIT] + [f'… and {extra} more']
    return messages


_PLAN_RUN = re.compile(r'^Przebieg:[ \t]*`?([^\s`]*)`?[ \t]*$', re.MULTILINE)
_PLAN_DAY = re.compile(r'^(\d{4}-\d{2}-\d{2})-')
# Wzorzec id jak `_slug` w runs.py (import cykliczny: runs importuje quality).
_RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
MAX_RUN_BYTES = 5 * 1024 * 1024
RUN_LINE_FROM = '2026-11-01'


def _has_final_record(root, run_id):
    """Czy `<id>.json` albo `<id>.*.json` ma rekord `final`; dowiązania, duże i nieczytelne pliki są pomijane."""
    directory = Path(root) / '.skillsync' / 'runs'
    if directory.parent.is_symlink() or directory.is_symlink() or not directory.is_dir():
        return False
    for path in sorted(directory.iterdir()):
        name = path.name
        if not (name == f'{run_id}.json' or name.startswith(f'{run_id}.') and name.endswith('.json')):
            continue
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RUN_BYTES:
                continue
            records = json.loads(path.read_text(encoding='utf-8')).get('checkpoints')
        except (OSError, ValueError, AttributeError, RecursionError):
            continue
        if isinstance(records, list) and any(isinstance(item, dict) and item.get('final') is True for item in records):
            return True
    return False


def plan_closure_findings(root, agentflow=False):
    """Ostrzeżenia o niezamkniętych planach: `done` bez zamknięcia przebiegu albo `in_progress` ze wszystkimi krokami
    `Progress` odhaczonymi: [(ścieżka, powód)]. Tylko odczyt."""
    directory = Path(root) / 'docs' / 'plans'
    if directory.is_symlink() or not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob('*.md')):
        if path.is_symlink() or not path.is_file() or path.name.lower() == 'readme.md':
            continue
        try:
            if path.stat().st_size > MAX_PLAN_BYTES:
                continue
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        head = text[:4000]
        status = _STATUS.search(head)
        word = status.group('word').lower() if status else ''
        label = f'docs/plans/{path.name}'
        if plan_text_needs_human(word, text) == ['closure']:
            found.append((label, 'All Progress steps are checked but the plan is still in_progress; close it: Result section, '
                                 '`retro-quality`, `skillsync checkpoint --final`, Status done (spec implemented), '
                                 'or uncheck the unfinished step'))
            continue
        if word != 'done':
            continue
        line = _PLAN_RUN.search(head)
        day = _PLAN_DAY.match(path.name)
        if not line:
            if agentflow and day and day.group(1) >= RUN_LINE_FROM:
                found.append((label, 'Plan is done but has no `Przebieg:` line; add the run id and close the run'))
            continue
        run_id = line.group(1)
        if not _RUN_ID.fullmatch(run_id):
            found.append((label, 'Invalid run id in the `Przebieg:` line'))
            continue
        if not _has_final_record(root, run_id):
            found.append((label, f'Plan is done but run `{run_id}` has no `final` record; '
                                 'close it with `skillsync checkpoint --final` and run `skillsync retro`'))
    return found


MANIFEST_PATH = '.github/.skillsync.yaml'
MACHINE_ONLY_KEYS = ('layers', 'layer_rules')


def _files_hash(files):
    """`_tree_hash` of the directory a per-file copy of `files` ({rel: (Layer, Path)}) gives."""
    digest = hashlib.sha256()
    for rel in sorted(files, key=lambda item: Path(item)):
        if '.git' in Path(rel).parts:
            continue
        data = Path(files[rel][1]).read_bytes()
        if b'\0' not in data[:8000]:
            data = data.replace(b'\r\n', b'\n')
        digest.update(Path(rel).as_posix().encode() + b'\0')
        digest.update(data + b'\0')
    return digest.hexdigest()


def _name_mismatch(root, name, layerset):
    """`project.name` vs the project path (K3) when the layers came from `--layer` (resolution by rule
    already reports `project-name-mismatch`)."""
    if layerset.origin == 'rule' or not isinstance(name, str) or not name.strip('/'):
        return False
    system = _layers.current_system()
    wanted = '/' + _layers._norm_text(name.strip('/'), system)
    project = Path(root).resolve()
    candidates = {project, _layers.main_worktree(project)} - {None}  # a linked worktree: its main repo too
    return not any(_layers.normalize_path(item, system).endswith(wanted) for item in candidates)


def layer_findings(root, manifest, layerset):
    """K3/K12 part of `doctor`: [(level, path, reason)].

    Manifest: `layers`/`layer_rules` = error (machine config only), the manifest library setting under
    `sync` = warning (ignored), `project.name` vs the project path = warning. Layers (`check_layers`
    without hooks and orphans): `blocked` (core_min, requires, missing version/core_min, invalid
    layer.yaml...; `link` refuses these) = error, other findings = warning (README/CHANGELOG, generator vs
    core version...), `info` (absent layer) omitted. Details: `skillsync layers check`."""
    found = []
    for key in MACHINE_ONLY_KEYS:
        if key in manifest:
            found.append(('error', MANIFEST_PATH, f'`{key}` is not a manifest key: layers and layer_rules live only in '
                                                  'the machine config (`skillsync layers use`); remove it'))
    sync = manifest.get('sync')
    if isinstance(sync, dict) and 'library' in sync:
        found.append(('warning', MANIFEST_PATH, 'Manifest `sync: {library: ...}` is ignored: layers come only from the '
                                                'machine config (`skillsync layers list`); remove the key'))
    project = manifest.get('project')
    name = project.get('name') if isinstance(project, dict) else None
    if _name_mismatch(root, name, layerset):
        found.append(('warning', MANIFEST_PATH, f'project.name {name!r} does not match the project path; layers are '
                                                'chosen by path only'))
    for item in _layers.check_layers(layerset, hooks=False, trust=True, orphans=False):
        severity = item.get('severity')
        if severity == 'info':
            continue
        if item.get('code') == 'project-name-mismatch':
            found.append(('warning', MANIFEST_PATH, item['message']))
            continue
        label = f'layer:{item["layer"]}' if item.get('layer') else 'layers'
        found.append(('error' if severity == 'blocked' else 'warning', label,
                      f'{item["message"]} (details: `skillsync layers check`)'))
    return found


def doctor(root, manifest, library):
    layerset = require_layerset(library, 'doctor')
    config = workflow_config(root, manifest, layerset)
    findings = []
    def finding(level, path, reason):
        findings.append({'level': level, 'path': str(path), 'reason': reason})
    for level, path, reason in layer_findings(root, manifest, layerset):
        finding(level, path, reason)
    skills = expand_project_skills(manifest, library)
    agentflow = WORKFLOW_SKILL in skills or LEGACY_WORKFLOW_SKILL in skills
    declared = manifest.get('skills')
    if isinstance(declared, list) and LEGACY_WORKFLOW_SKILL in declared:
        finding('warning', '.github/.skillsync.yaml',
                f'Deprecated skill name `{LEGACY_WORKFLOW_SKILL}` in skills:; replace it with `{WORKFLOW_SKILL}` '
                f'(the old name still works as an alias)')
    if isinstance(declared, list) and 'go-jira' in declared:
        finding('warning', '.github/.skillsync.yaml',
                'Deprecated skill `go-jira` in skills: it was removed from the library; replace it with `acli` '
                '(the old name resolves to `acli` when the library has it)')
    roles = manifest.get('agents')
    if isinstance(roles, list) and 'Explore' in roles:
        finding('warning', '.github/.skillsync.yaml',
                'Renamed role `Explore` in agents: replace it with `explore` (the old name still works as an alias '
                'for one release); then run `skillsync link`')
    dispatch_errors, dispatch_warnings = validate_dispatch(manifest.get('dispatch'))
    for message in dispatch_errors:
        finding('error', '.github/.skillsync.yaml', message)
    for message in dispatch_warnings:
        finding('warning', '.github/.skillsync.yaml', message)
    delivery_errors, delivery_warnings = validate_delivery_mode(manifest)
    for message in delivery_errors:
        finding('error', '.github/.skillsync.yaml', message)
    for message in delivery_warnings:
        finding('warning', '.github/.skillsync.yaml', message)
    ceremony_errors, _ = validate_ceremony(manifest)
    for message in ceremony_errors:
        finding('error', '.github/.skillsync.yaml', message)
    prototype_errors, prototype_warnings = validate_prototype(manifest.get('prototype'), root)
    for message in prototype_errors:
        finding('error', '.github/.skillsync.yaml', message)
    for message in prototype_warnings:
        finding('warning', '.github/.skillsync.yaml', message)
    codex_defaults = layerset.merged_file('codex.yaml').data
    codex_local = manifest.get('codex') if isinstance(manifest.get('codex'), dict) else {}
    codex_excluded = strings(codex_defaults.get('exclude_skills', []), 'codex.exclude_skills') + strings(codex_local.get('exclude_skills', []), 'codex.exclude_skills')
    def enabled(engine, default):
        setting = manifest.get(engine, default)
        return setting is not False and (not isinstance(setting, dict) or setting.get('enabled', True))
    hosts = [('.github/skills', 'github-skill')]
    if enabled('claude', True):
        hosts.append(('.claude/skills', 'claude-skill'))
    if enabled('codex', agentflow):
        hosts.append(('.agents/skills', 'codex-skill'))
    for name in skills:
        found = layerset.resolve(f'skills/{name}/SKILL.md')
        if not found:
            finding('error', f'skills/{name}/SKILL.md', 'Missing skill'); continue
        path = found[1]
        content = path.read_text(encoding='utf-8')
        try:
            metadata = yaml.safe_load(content.split('---', 2)[1]) if content.startswith('---\n') else {}
            if not isinstance(metadata, dict) or not metadata.get('name') or not metadata.get('description'):
                finding('error', path, 'Missing name/description frontmatter')
            elif len(str(metadata['description']).encode('utf-8')) > 200:
                finding('warning', path, 'Skill description longer than 200 bytes')
        except (yaml.YAMLError, IndexError):
            finding('error', path, 'Invalid skill frontmatter')
        for prefix, state_key in hosts:
            if prefix == '.agents/skills' and name in codex_excluded:
                continue
            linked = Path(root) / prefix / name
            if not linked.exists():
                finding('error', linked, 'Missing installed skill')
            elif linked.is_symlink():
                state = skill_link_state(layerset, name, linked)
                if state == 'stale':
                    finding('error', linked, 'Installed skill symlink points to a stale composite; run `skillsync link`')
                elif state != 'ok':
                    finding('error', linked, 'Foreign installed skill symlink')
            elif not linked.is_dir() or any(item.is_symlink() for item in linked.rglob('*')):
                finding('error', linked, 'Invalid installed skill copy')
            elif _tree_hash(linked) != _files_hash(layerset.skill_files(name)):
                finding('error', linked, 'Installed skill copy differs from source')
    from .catalog import catalog_findings
    for reason in catalog_findings(root, manifest, library, skills):
        finding('warning', '.github/.skillsync.yaml', reason)
    if agentflow:
        roles = manifest.get('agents', list(codex_defaults.get('agents', {})))
        strings(roles, 'agents')
        from .common import RENAMED_ROLE_ALIASES
        roles = list(dict.fromkeys(RENAMED_ROLE_ALIASES.get(role, role) for role in roles))  # link instaluje nową nazwę
        for role in roles:
            if Path(role).is_absolute() or '..' in Path(role).parts or '\\' in role:
                raise ValueError(f'Path escapes project: agents/{role}.md')
        domain_dir, _ = domain_location(root, manifest, library)
        if not (domain_dir / 'index.yaml').is_file():
            finding('warning', str(domain_dir / 'index.yaml'), 'Missing domain knowledge index; create it when the first verified domain record is written')
        lessons_dir = lessons_location(root, manifest, library)
        if not (lessons_dir / 'index.yaml').is_file():
            finding('warning', str(lessons_dir / 'index.yaml'), 'Missing lessons index; run `skillsync knowledge init` before the first lesson is written')
        for stray in sorted(Path(root).glob('*/docs/domain/index.yaml')) + sorted(Path(root).glob('*/*/docs/domain/index.yaml')):
            if 'node_modules' not in stray.parts and stray.parent.resolve() != domain_dir.resolve():
                finding('warning', str(stray), f'Scattered domain knowledge; keep one shared location: {domain_dir}')
        if enabled('claude', True):
            for role in roles:
                role_path = Path(root) / '.claude/agents' / f'{role}.md'
                source = layerset.resolve(f'agents/{role}.md')
                if not role_path.is_file():
                    finding('error', role_path, 'Missing Claude role')
                elif role_path.is_symlink() and (source is None or role_path.resolve() != source[1].resolve()):
                    finding('error', role_path, 'Foreign Claude role symlink')
            if not (Path(root) / 'CLAUDE.md').is_file():
                finding('error', 'CLAUDE.md', 'Missing enabled Claude instructions')
        if enabled('codex', agentflow):
            for role in roles:
                settings = {**codex_defaults.get('agents', {}).get(role, {}), **codex_local.get('agent_overrides', {}).get(role, {})}
                if not settings:
                    continue
                role_name = settings.get('name', '')
                if not isinstance(role_name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', role_name) or Path(role).name != role:
                    raise ValueError(f'Invalid Codex role name: {role} -> {role_name}')
                role_path = contained(root, Path('.codex/agents') / (role_name + '.toml'))
                if not role_path.is_file():
                    finding('error', role_path, 'Missing enabled Codex role')
            required = ['AGENTS.md'] if claude_imports_agents(root) else ['AGENTS.md', '.codex/skillsync-instructions.md']
            for instruction in required:
                if not (Path(root) / instruction).is_file():
                    finding('error', instruction, 'Missing enabled Codex entrypoint/instructions')
    if (Path(root) / 'AGENTS.md').is_symlink():
        finding('warning', 'AGENTS.md', 'Symlinked AGENTS.md: skillsync does not write its instructions block there')
    if generated_claude_import(root) and not agents_block_body(root):
        finding('error', 'AGENTS.md', 'CLAUDE.md imports @AGENTS.md, but AGENTS.md has no skillsync instructions block')
    for name in skills:
        found = layerset.resolve(f'skills/{name}/SKILL.md')
        if not found:
            continue
        path = found[1]
        content = path.read_text(encoding='utf-8')
        skill_dirs = [layer.root / 'skills' / name for layer in layerset.skill_providers(name)]
        for reference in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', content):
            if '://' in reference or reference.startswith('#') or ' ' in reference:
                continue
            if not any((directory / reference).exists() for directory in skill_dirs):
                finding('warning', path, f'Unresolved skill reference: {reference}')
        if name in (WORKFLOW_SKILL, LEGACY_WORKFLOW_SKILL, 'dispatch'):
            for engine in ['claude', 'codex']:
                setting = manifest.get(engine, True)
                engine_enabled = setting is not False and (not isinstance(setting, dict) or setting.get('enabled', True))
                adapter = f'skills/{name}/references/{engine}.md'
                if engine_enabled and not layerset.resolve(adapter):
                    finding('error', adapter, f'Missing enabled {name} adapter')
    for kind, records in [('Lesson', lesson_indexes(root, library, manifest)), ('Domain record', domain_indexes(root, library, manifest))]:
        for record in records:
            path = Path(record['path'])
            if kind == 'Domain record' and 'load' in record and record['load'] != 'bootstrap':
                finding('error', record['index'], f'Domain record load must be bootstrap or omitted: {path.name}')
            content = path.read_text(encoding='utf-8')
            if not content.startswith('---\n'):
                finding('error', path, f'{kind} requires YAML frontmatter')
                continue
            try:
                metadata = yaml.safe_load(content.split('---', 2)[1])
                if not isinstance(metadata, dict):
                    finding('error', path, f'{kind} frontmatter must be a mapping')
                else:
                    strings(metadata.get('tags', []), f'{kind.lower()}.tags')
            except (yaml.YAMLError, IndexError, ValueError) as error:
                finding('error', path, str(error))
    for path in (Path(root) / '.codex/agents').glob('*.toml'):
        try:
            with path.open('rb') as handle:
                tomllib.load(handle)
        except (ValueError, OSError) as error:
            finding('error', path, str(error))
    context = config.get('context', {})
    budgets = dict(context.get('budgets', {}))
    if context.get('base_max_bytes'):
        budgets.setdefault('library:instructions/_base.md', context['base_max_bytes'])  # `library:` = core layer
    if context.get('max_bytes'):
        names = ['.github/copilot-instructions.md', 'CLAUDE.md', '.codex/skillsync-instructions.md']
        for name in names + (['AGENTS.md'] if claude_imports_agents(root) else []):
            budgets.setdefault(name, context['max_bytes'])
    if not isinstance(budgets, dict):
        raise ValueError('context.budgets must be a mapping of path to byte limit')
    sizes = []
    for name, maximum in budgets.items():
        path = source_path(root, library, name)
        if path.is_file():
            # AGENTS.md in `agents` mode: count the instruction body only (as CLAUDE.md did), not markers or the Codex note.
            agents_body = name == 'AGENTS.md' and claude_imports_agents(root)
            size = len(agents_block_body(root).encode('utf-8')) if agents_body else path.stat().st_size
            sizes.append({'path': str(path), 'bytes': size, 'limit': int(maximum)})
            if size > int(maximum):
                finding('error', path, 'Instruction budget exceeded')
    for rule in config.get('routing', {}).get('rules', []):
        for name in rule.get('instructions', []):
            if uninstalled_skill(name, skills):
                continue  # route skips instructions of skills the project does not install
            path = source_path(root, library, name)
            if not path.is_file():
                finding('error', path, 'Missing routed instruction')
    for guide in config.get('guides', []):
        path = source_path(root, library, guide)
        if not path.is_file():
            finding('error', path, 'Missing guide')
    try:
        validation = plan_validation(root, manifest, library, profile='all')
        for warning in validation['warnings']:
            finding('warning', '.github/.skillsync.yaml', warning)
    except (ValueError, TypeError) as error:
        finding('error', '.github/.skillsync.yaml', str(error))
    for section, names in [('instructions', [manifest.get('instructions', {}).get('template')])]:
        for name in names:
            if not name:
                continue
            try:
                source = layerset.resolve(f'{section}/{name}.md')
            except ValueError:  # LayerError: path escapes the layer
                source = None
            if source is None:
                finding('error', name, 'Missing instruction source')
    if config.get('skill_profiles', {}).get('selected'):
        from .maintenance import profiles
        catalog = profiles(root, manifest, library)
        sizes.append({'path': 'skill catalog (names/descriptions)', 'bytes': catalog['bytes'], 'limit': catalog['limit']})
        if catalog['status'] != 'PASS':
            finding('error', 'workflow.skill_profiles', 'Skill catalog budget exceeded')
    ignore_paths, ignore_skills, ignore_error = instruction_ignores(config)
    if ignore_error:
        finding('error', '.github/.skillsync.yaml', ignore_error)
    for message in instruction_drift(root, library, skills, ignore_paths, ignore_skills):
        finding('warning', 'instructions', message)
    for plan_path, reason in plan_closure_findings(root, agentflow):
        finding('warning', plan_path, reason)
    return {'status': 'FAIL' if any(f['level'] == 'error' for f in findings) else 'PASS', 'findings': findings, 'budgets': sizes}


def detected_areas(root, config, files):
    defaults = {'api': ['**/api/**', '**/*schema*'], 'data': ['**/migrations/**'], 'security': ['**/*auth*', '**/*permission*'], 'ui': ['**/*.tsx', '**/*.vue']}
    patterns = config.get('readiness', {}).get('risk_paths', defaults)
    areas = [area for area, paths in patterns.items() if any(matches(f, paths) for f in files)]
    if any(matches(f, config.get('readiness', {}).get('contract_paths', [])) for f in files) and 'contracts' not in areas:
        areas.append('contracts')
    if any(matches(f, config.get('review', {}).get('ui_paths', [])) for f in files) and 'ui' not in areas:
        areas.append('ui')
    return areas


def _read_spec(root, plan_text):
    """Read the spec bound by the plan's `Spec:` line. Returns (relative posix path or None, text, reasons)."""
    lines = SPEC_LINE.findall(plan_text)
    if not lines:
        return None, '', []
    if len(lines) > 1:
        return None, '', ['Spec rejected: more than one Spec: line in plan']
    match = SPEC_FULL.match('Spec:' + lines[0])
    name = match.group(1) if match else ''
    if not name:
        return None, '', ['Spec rejected: Spec: line must be a single docs/specs/<file>.md path']
    if not is_spec_path(name):
        return None, '', [f'Spec rejected: expected docs/specs/<file>.md (not README.md), got {name}']
    root = Path(root)
    path = root / name
    try:
        for part in (root / 'docs', root / 'docs' / 'specs', path):
            if part.is_symlink():
                return None, '', [f'Spec rejected: symlink in path: {part.relative_to(root).as_posix()}']
        if not path.is_file():
            return None, '', [f'Spec rejected: not found: {name}']
        if path.stat().st_size > MAX_PLAN_BYTES:
            return None, '', [f'Spec rejected: over {MAX_PLAN_BYTES // 1024} KiB: {name}']
        return name, path.read_text(encoding='utf-8'), []
    except (OSError, UnicodeDecodeError) as exc:
        return None, '', [f'Spec rejected: cannot read {name}: {type(exc).__name__}']


def _section_state(text, section):
    """Classify a section in one text as 'missing', 'empty' or 'ok'."""
    header = re.search(r'(?im)^#{1,6}\s+[^\n]*' + re.escape(section) + r'[^\n]*(?:\n|$)', text)
    if not header:
        return 'missing'
    body = re.split(r'(?m)^#{1,6}\s', text[header.end():], maxsplit=1)[0].strip()
    return 'empty' if not body or re.fullmatch(r'(?i)(TODO|TBD|none|n/a|[-—])\.?', body) else 'ok'


def readiness(root, manifest, library, files, plan=None, risk=None):
    files = normalize_files(root, files)
    workflow = workflow_config(root, manifest, library)
    config = workflow.get('readiness', {})
    areas = detected_areas(root, workflow, files)
    risk = risk or ('high' if any(a in areas for a in ['security', 'data', 'contracts', 'api']) else 'medium' if areas else 'low')
    if risk not in ('low', 'medium', 'high'):
        raise ValueError('risk must be low, medium or high')
    issues, sections, spec, gate = [], [], None, None
    ui_phases, phase_info, gate_ui_waiting = [], {'phases': [], 'stray_ui': 0}, False
    if not files:
        issues.append('No changed files supplied or detected')
    required = list(config.get('required_sections', ['contracts', 'risks', 'evidence']))
    risk_sections = config.get('risk_sections', {})
    if risk == 'high' or areas:
        if isinstance(risk_sections, list):
            required.extend(risk_sections)
        else:
            for area in areas + [risk]:
                required.extend(risk_sections.get(area, []))
    if plan:
        path = contained(root, plan)
        text = path.read_text(encoding='utf-8')
        spec, spec_text, reasons = _read_spec(root, text)
        issues.extend(reasons)
        label = 'plan or spec' if spec else 'plan'
        for section in dict.fromkeys(required):
            states = [_section_state(text, section)] + ([_section_state(spec_text, section)] if spec else [])
            if 'ok' in states:
                sections.append(section)
            elif 'empty' in states:
                issues.append(f'Empty {label} section: {section}')
            else:
                issues.append(f'Missing {label} section: {section}')
        if re.search(r'(?i)\b(?:TODO|TBD)\b', text):
            issues.append('Unresolved TODO/TBD in plan')
        if spec and re.search(r'(?i)\b(?:TODO|TBD)\b', spec_text):
            issues.append('Unresolved TODO/TBD in spec')
        from . import prototype as prototype_module  # lazy: the gate lives with the prototype commands
        from .plan_phases import parse_plan_phases
        phase_info = parse_plan_phases(text)
        ui_phases = phase_info['ui_phases']
        if prototype_module.plan_has_gate_lines(text) or ui_phases:
            try:
                gate = prototype_module.prototype_gate(root, manifest, plan=plan)
            except (prototype_module.PrototypeError, ValueError, OSError) as error:
                gate = {'status': 'BLOCKED', 'error': 'gate_failed', 'reason': str(error)}
            if gate['status'] == 'FAIL' and ui_phases:
                gate_ui_waiting = True
            elif gate['status'] == 'FAIL':
                issues.extend(f'Prototype gate FAIL: {item["slug"]} {item["screen"] or "—"} ({item["reason"]})'
                              for item in gate['missing'])
            elif gate['status'] == 'BLOCKED':
                issues.append(f'Prototype gate BLOCKED: {gate.get("reason") or gate.get("error")}')
    elif risk != 'low' or areas:
        issues.append('Risky change requires a plan with contract, risk and evidence sections')
    evidence = [{'file': f, 'exists': (Path(root) / f).is_file()} for f in files]
    report = {'status': 'BLOCKED' if issues else 'PASS', 'risk': risk, 'risks': areas, 'areas': areas, 'issues': issues,
              'files': evidence, 'sections': sections, 'spec': spec, 'note': 'Structural readiness only; contract correctness requires human/agent review'}
    if gate is not None:
        report['prototypeGate'] = gate
    if ui_phases:
        report['uiPhases'] = ui_phases
        report['nonUiPhases'] = [item for item in phase_info['phases'] if item not in ui_phases]
        if gate_ui_waiting:
            report['uiPhasesWaiting'] = list(ui_phases)
    if plan:
        gate_warnings = []
        if phase_info['stray_ui']:
            gate_warnings.append(f'[UI] w wierszu tabelowym poza tabelą kroków planu ({phase_info["stray_ui"]}): '
                                 'faza nie jest rozpoznana jako [UI]')
        if ui_phases and gate is not None and gate.get('bypassed'):
            gate_warnings.append('bramka pominięta przez właściciela')
        if gate_warnings:
            report['warnings'] = gate_warnings
    return report


def review(root, manifest, library, files, risk=None):
    files = normalize_files(root, files)
    workflow = workflow_config(root, manifest, library)
    config = workflow.get('review', {})
    result = readiness(root, manifest, library, files, risk=risk)
    areas, risk = result['areas'], result['risk']
    checklist = list(config.get('core', ['Correctness and acceptance criteria', 'Regression evidence', 'Compatibility and error handling']))
    additions = config.get('risks', {})
    checklist.extend(additions.get(risk, []))
    for area in areas:
        checklist.extend(additions.get(area, []))
    checklist.extend(config.get('project', []))
    ui_evidence = config.get('ui_evidence', ['Critical UI path executed in browser', 'Error states verified', 'Keyboard accessibility verified']) if 'ui' in areas else []
    checklist.extend(ui_evidence)
    return {'status': 'NOT_RUN', 'risk': risk, 'risks': areas, 'areas': areas, 'checklist': checklist, 'ui_evidence': ui_evidence,
            'note': 'Review checklist only; independent review and UI evidence still required'}


def _cmd(args):
    try:
        root, manifest, library = load_layer_project(args.path, args.layers)
        files = getattr(args, 'files', None)
        if files is None and args.command in ('readiness', 'review'):
            files = changed_files(root, getattr(args, 'base', None))
        if files is not None:
            files = normalize_files(root, files)
        if args.command == 'validate':
            report = plan_validation(root, manifest, library, args.profile, files, args.check)
            if args.run and report['status'] != 'BLOCKED':
                report = run_validation(root, report['checks'])
        elif args.command == 'route':
            report = route(root, manifest, library, files or [], args.topics or [])
        elif args.command == 'doctor':
            report = doctor(root, manifest, library)
        elif args.command == 'readiness':
            report = readiness(root, manifest, library, files, args.plan, args.risk)
        else:
            report = review(root, manifest, library, files, args.risk)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, subprocess.SubprocessError) as error:
        report = {'status': 'BLOCKED', 'reason': _redact(str(error))}
    return emit(report, args.json)


def register(subparsers):
    for name in ['route', 'doctor', 'readiness', 'validate', 'review']:
        parser = subparsers.add_parser(name, help=f'Workflow {name}')
        add_project_arguments(parser)
        if name != 'doctor':
            parser.add_argument('--files', nargs='*')
        if name == 'route':
            parser.add_argument('--topics', '--topic', nargs='*')
        if name in ('readiness', 'review'):
            parser.add_argument('--base')
            parser.add_argument('--risk', choices=['low', 'medium', 'high'])
        if name == 'readiness':
            parser.add_argument('--plan')
        if name == 'validate':
            parser.add_argument('--profile', default='quick')
            parser.add_argument('--check', action='append')
            parser.add_argument('--run', action='store_true')
        parser.set_defaults(func=_cmd)
