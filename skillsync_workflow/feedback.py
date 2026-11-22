"""Controlled channel for change requests to the owner of a layer (CORE-1140-FEEDBACK).

F-2 (this part): the trust model of the target. The `feedback:` section of a `layer.yaml` only PROPOSES where requests go (the file may come from an
untrusted repository, and a logged-in `az` can reach every organization of the user); the machine config key `feedback_targets` holds the target a human
approved per layer. Nothing is sent to an unapproved or changed target. Names in tests and docs are neutral examples.
"""
from . import layers

TRUSTED, NOT_TRUSTED, CHANGED, NO_SECTION = 'trusted', 'not-trusted', 'changed', 'no-section'


def target_entry(layer_id, section):
    """The `feedback_targets` entry a section proposes (the part a human approves: kind, organization, project)."""
    return {'layer': layer_id, 'kind': section['kind'], 'organization': section['organization'], 'project': section['project']}


def target_state(config, layer_id, section):
    """`{state, entry}` of the layer's target against the machine config: `no-section` (the layer proposes nothing), `not-trusted` (no approved entry),
    `changed` (the approved entry differs from the section: a changed organization or project after `layers update` cancels the approval), `trusted`.
    `entry` is the approved entry or None. Area and type may change without a new approval (they change neither organization nor project)."""
    if not section:
        return {'state': NO_SECTION, 'entry': None}
    approved = next((item for item in config.feedback_targets if item['layer'] == layer_id), None)
    if approved is None:
        return {'state': NOT_TRUSTED, 'entry': None}
    same = all(approved[key] == section[key] for key in ('kind', 'organization', 'project'))
    return {'state': TRUSTED if same else CHANGED, 'entry': approved}


def trust_target(config, layer_id, section, *, apply=True):
    """Approve the proposed target of a layer in the machine config (adds the entry, or replaces the one for that layer; a backup is kept).

    The caller (the `skillsync feedback trust` command) shows the target and asks the human first; this function only writes."""
    if not section:
        raise layers.LayerError(f'Layer {layer_id} has no valid `feedback:` section')
    if layer_id not in config.layers:
        raise layers.LayerError(f'Unknown layer: {layer_id}')
    return layers.edit_config(config.path, [layers.feedback_target_op(target_entry(layer_id, section))], apply=apply)


# --------------------------------------------------------------------------- F-3: draft, render, sha, store

import contextlib
import hashlib
import html
import json
import os
import re
import time
import unicodedata
from pathlib import Path

DRAFT_KINDS = ('bug', 'change', 'tool', 'docs')
MAX_FIELD = 4000
MAX_TOTAL = 20000
MAX_TITLE = 120
MAX_LIST = 20
MAX_TARGET = 200
_CAUSE_KEY = re.compile(r'[a-z0-9][a-z0-9-]{0,47}')
_RETRO = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}/P[0-9]{1,4}')
_TARGET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/@ -]{0,199}')
_DRAFT_ID = re.compile(r'F[1-9][0-9]{0,5}')
DRAFT_KEYS = ('layer', 'kind', 'title', 'target', 'problem', 'evidence', 'proposal', 'acceptance', 'cause_key', 'core_candidate', 'retro')
STORE_DIR = ('.skillsync', 'feedback')


class FeedbackError(ValueError):
    """A draft, a store or a request that cannot be used (the message is safe to show: no draft text is quoted)."""


def clean_text(value):
    """The text without control characters (a new line and a tab stay as spaces and new lines) and without bidi/format characters (category Cf)."""
    out = []
    for char in str(value).replace('\r\n', '\n').replace('\r', '\n'):
        if char in '\n\t':
            out.append(char)
        elif unicodedata.category(char) in ('Cc', 'Cf', 'Cs', 'Co'):
            continue
        else:
            out.append(char)
    return ''.join(out).strip()


def _field(draft, key, limit=MAX_FIELD, required=True):
    value = draft.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise FeedbackError(f'draft: `{key}` must be a text')
    value = clean_text(value)
    if required and not value:
        raise FeedbackError(f'draft: `{key}` must not be empty')
    if len(value) > limit:
        raise FeedbackError(f'draft: `{key}` is longer than {limit} characters')
    return value


def _pairs(draft, key, first, second):
    items = draft.get(key)
    if not isinstance(items, list) or not items or len(items) > MAX_LIST:
        raise FeedbackError(f'draft: `{key}` must be a list of 1 to {MAX_LIST} entries')
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {first, second}:
            raise FeedbackError(f'draft: `{key}[{index}]` must be a mapping with `{first}` and `{second}`')
        result.append({first: _field(item, first, 500), second: _field(item, second)})
    return result


def validate_draft(draft):
    """The cleaned draft (a new dict) or FeedbackError. Texts are untrusted data: control and bidi characters are removed, sizes limited."""
    if not isinstance(draft, dict):
        raise FeedbackError('draft must be a mapping')
    unknown = sorted(str(key) for key in draft if key not in DRAFT_KEYS)
    if unknown:
        raise FeedbackError(f'draft: unknown keys: {", ".join(unknown)[:120]}')
    layer = draft.get('layer')
    if not isinstance(layer, str) or not layers._ID.fullmatch(layer):
        raise FeedbackError('draft: `layer` must match [a-z0-9][a-z0-9-]*')
    if draft.get('kind') not in DRAFT_KINDS:
        raise FeedbackError(f'draft: `kind` must be one of: {", ".join(DRAFT_KINDS)}')
    cause = draft.get('cause_key')
    if not isinstance(cause, str) or not _CAUSE_KEY.fullmatch(cause):
        raise FeedbackError('draft: `cause_key` must match [a-z0-9][a-z0-9-]{0,47}')
    candidate = draft.get('core_candidate', False)
    if not isinstance(candidate, bool):
        raise FeedbackError('draft: `core_candidate` must be true or false')
    retro = draft.get('retro')
    if retro is not None and (not isinstance(retro, str) or not _RETRO.fullmatch(retro)):
        raise FeedbackError('draft: `retro` must look like <run-id>/P<n>')
    target = _field(draft, 'target', MAX_TARGET)
    if not _TARGET.fullmatch(target) or '..' in target.split('/') or target.startswith('/') or target != target.strip():
        raise FeedbackError('draft: `target` must be a relative path of a layer file or a skill name')
    result = {'layer': layer, 'kind': draft['kind'], 'title': _field(draft, 'title', MAX_TITLE), 'target': target,
              'problem': _field(draft, 'problem'), 'evidence': _pairs(draft, 'evidence', 'source', 'note'), 'proposal': _field(draft, 'proposal'),
              'acceptance': _pairs(draft, 'acceptance', 'input', 'expected'), 'cause_key': cause, 'core_candidate': candidate}
    if '\n' in result['title']:
        raise FeedbackError('draft: `title` must be one line')
    if retro is not None:
        result['retro'] = retro
    total = len(json.dumps(result, ensure_ascii=False))
    if total > MAX_TOTAL + 4000:
        raise FeedbackError(f'draft is longer than {MAX_TOTAL} characters of text')
    return result


def dedup_key(draft):
    """`fbk-<hash12>`: the stable key of a cause (layer, target, cause key); an open item with it receives a comment instead of a duplicate."""
    text = '|'.join((draft['layer'], draft['target'], draft['cause_key']))
    return 'fbk-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]


def render(draft, section, *, mode='create', item=None):
    """What would be sent, without the part that depends on the sha: `{organization, project, area, type, mode, item, title, body, tags}`.

    `draft` is a validated draft, `section` the proposed (and approved) target of the layer. The body is HTML with every field escaped (`create`), or the
    plain text of a comment (`comment`, about the open item `item`); `tags` are sorted and WITHOUT `fb-<sha12>` (it is derived from the content)."""
    if mode not in ('create', 'comment') or (mode == 'comment') != (item is not None):
        raise FeedbackError('render: mode `comment` needs an item and mode `create` has none')
    esc = html.escape
    evidence = ''.join(f'<li>{esc(entry["source"])}: {esc(entry["note"])}</li>' for entry in draft['evidence'])
    acceptance = ''.join(f'<li>{esc(entry["input"])} &rarr; {esc(entry["expected"])}</li>' for entry in draft['acceptance'])
    if mode == 'create':
        body = (f'<h3>Problem</h3><p>{_paragraphs(draft["problem"])}</p><h3>Evidence</h3><ul>{evidence}</ul>'
                f'<h3>Proposal</h3><p>{_paragraphs(draft["proposal"])}</p><h3>Acceptance</h3><ul>{acceptance}</ul>'
                f'<p>Layer: {esc(draft["layer"])} &middot; target: {esc(draft["target"])} &middot; kind: {esc(draft["kind"])}</p>')
    else:
        body = (f'Another report with the same cause ({draft["cause_key"]}).\nProblem: {draft["problem"]}\n'
                + '\n'.join(f'Evidence: {entry["source"]}: {entry["note"]}' for entry in draft['evidence']))
    tags = {'feedback', f'layer:{draft["layer"]}', dedup_key(draft), *section.get('labels', [])}
    if draft['core_candidate']:
        tags.add('core-candidate')
    return {'organization': section['organization'], 'project': section['project'], 'area': section.get('area'), 'type': section.get('type') or 'Task',
            'mode': mode, 'item': item, 'title': f'[feedback] {draft["title"]}', 'body': body, 'tags': sorted(tags)}


def _paragraphs(text):
    return '</p><p>'.join(html.escape(part) for part in text.split('\n') if part.strip())


def content_sha(rendered):
    """The full sha256 (hex) of the canonical serialization of what is sent: target, mode, item, title, body and tags without `fb-`. The tag
    `fb-<sha12>` and the metadata are added AFTER this hash, so the hash does not depend on itself."""
    payload = {'v': 1, 'organization': rendered['organization'], 'project': rendered['project'], 'area': rendered['area'], 'type': rendered['type'],
               'mode': rendered['mode'], 'item': rendered['item'], 'title': rendered['title'], 'body': rendered['body'],
               'tags': sorted(tag for tag in rendered['tags'] if not tag.startswith('fb-'))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def sha12(sha):
    return sha[:12]


def final_payload(rendered):
    """`{sha, sha12, title, body, tags}` as sent: the tag `fb-<sha12>` and the marker are appended to the rendered content (for a comment: the marker
    `[fb-<sha12>]` as the first line, so a repeated `send` can find its own comment)."""
    sha = content_sha(rendered)
    tag = f'fb-{sha12(sha)}'
    if rendered['mode'] == 'create':
        body = rendered['body'] + f'<p>{tag}</p>'
    else:
        body = f'[{tag}]\n' + rendered['body']
    return {'sha': sha, 'sha12': sha12(sha), 'title': rendered['title'], 'body': body, 'tags': sorted({*rendered['tags'], tag}), 'tag': tag}


# --- store: drafts and results live in the MAIN checkout (a worktree does not bypass the limits)

def store_root(project):
    """`<main checkout>/.skillsync/feedback` for a project path (a linked worktree maps to its main repository)."""
    project = Path(project).resolve()
    main = layers.main_worktree(project) or project
    return main.joinpath(*STORE_DIR)


def draft_path(store, draft_id):
    if not _DRAFT_ID.fullmatch(draft_id or ''):
        raise FeedbackError('draft id must look like F<n>')
    return Path(store) / f'{draft_id}.json'


def _safe_dir(store, create=False):
    store = Path(store)
    if create:
        store.mkdir(parents=True, exist_ok=True)
    if store.is_symlink() or store.parent.is_symlink():
        raise FeedbackError('the feedback store must not be a symlink')
    return store


def write_json_atomic(path, data):
    """Atomic JSON write, mode 0600 (a temporary file in the same directory, `os.replace`)."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write('\n')
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def read_json_file(path, limit=256 * 1024):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return None
    if path.stat().st_size > limit:
        raise FeedbackError(f'{path.name} is too large')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise FeedbackError(f'{path.name} is not valid JSON') from None


def next_draft_id(store):
    """`F<n>` with the next number free in the store (drafts, `.sent.json` and `.sending.json` all hold their numbers)."""
    store = Path(store)
    used = [int(m.group(1)) for path in store.glob('F*') if (m := re.match(r'^F([1-9][0-9]*)[.]', path.name))] if store.is_dir() else []
    return f'F{max(used, default=0) + 1}'


def save_draft(store, draft, draft_id=None):
    """Validate and save a draft in the store; returns `(id, cleaned draft)`. Without `draft_id` a new number is taken."""
    cleaned = validate_draft(draft)
    store = _safe_dir(store, create=True)
    draft_id = draft_id or next_draft_id(store)
    write_json_atomic(draft_path(store, draft_id), cleaned)
    return draft_id, cleaned


def load_draft(store, draft_id):
    data = read_json_file(draft_path(store, draft_id))
    if data is None:
        raise FeedbackError(f'no draft {draft_id}')
    return validate_draft(data)


@contextlib.contextmanager
def store_lock(store, timeout=15.0):
    """An exclusive lock on the store (`O_EXCL` file; a lock older than 120 s is taken over as stale)."""
    store = _safe_dir(store, create=True)
    path = store / '.lock'
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - path.stat().st_mtime > 120:
                    os.unlink(path)
                    continue
            if time.monotonic() > deadline:
                raise FeedbackError('the feedback store is locked by another process') from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


# --------------------------------------------------------------------------- F-4: leak scan of the exact bytes that are sent

_SECRET_PATTERNS = (
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'), re.compile(r'github_pat_[A-Za-z0-9_]{20,}'), re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'), re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}'),
    re.compile(r'\b[a-z2-7]{52}\b'),   # the shape of an Azure DevOps personal access token
    re.compile(r'(?i)\b(?:password|passwd|secret|token|api[_-]?key|pat)\b\s*[:=]\s*[^\s,;]{8,}'),
    re.compile(r'(?i)\b(?:https?|ssh)://[^/\s:@]+:[^/\s@]{3,}@'),   # credentials in a URL
)


def secret_findings(texts):
    """`[{label, category: 'secret'}]` for the texts `[(label, text)]` that contain a secret-shaped string (never the string itself).

    The identity scan of the leak guard does not look for credentials, so this is the second part of the scan (conservative: a false positive stops the
    send, a miss does not exist for the shapes listed)."""
    return [{'label': label, 'category': 'secret'} for label, text in texts if any(pattern.search(text) for pattern in _SECRET_PATTERNS)]


def leak_scan(config, layer_id, payload, *, directory):
    """The leak guard scan of the payload as it is sent (`final_payload`: title, every tag, body or comment), against the identities of the OTHER layers.

    The scan target is the id of the destination layer given explicitly (the owner of the tracker the text goes to), never chosen from a repository.
    Returns `{status, coverage, findings, warnings, reason}`: `status` PASS, FAIL (a finding) or BLOCKED (bad input: also a stop); a finding carries the
    label of the text and the category, never the text. `coverage` `generic` means no identity files were found, so client names are NOT checked
    (the preview says so); `partial` and `full` follow the leak guard. `directory` is any existing directory (the scan needs no repository)."""
    from . import leakguard
    try:
        inputs = layers.leakguard_inputs(config, target=layer_id)
        texts = [('title', payload['title']), ('tags', '\n'.join(payload['tags'])), ('body', payload['body'])]
        report = leakguard.leakcheck(str(directory), mode='text', texts=texts, target=layer_id, targets=inputs['targets'],
                                     identity_files=inputs['identity_files'], target_patterns=layers.target_patterns_file(config, layer_id),
                                     trailer_allow=inputs['trailer_allow'], select=False)
    except (layers.LayerError, leakguard.LeakguardError) as exc:
        return {'status': 'BLOCKED', 'coverage': 'generic', 'findings': [], 'warnings': [], 'reason': str(exc)[:200]}
    findings = [{'label': item['path'], 'category': item['category']} for item in report['findings']] + secret_findings(texts)
    status = report['status']
    if status == 'PASS' and findings:
        status = 'FAIL'
    return {'status': status, 'coverage': report['coverage'], 'findings': findings,
            'warnings': list(report.get('warnings', [])), 'reason': report.get('reason')}
