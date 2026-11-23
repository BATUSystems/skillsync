"""Controlled channel for change requests to the owner of a layer (CORE-1140-FEEDBACK).

Parts: the trust model of the target (F-2), draft, render and store (F-3), the leak scan (F-4), the internal Azure provider and the send engine (F-5), the
consent and the command `skillsync feedback` (F-6). The `feedback:` section of a `layer.yaml` only PROPOSES where requests go (the file may come from an
untrusted repository, and a logged-in `az` can reach every organization of the user); the machine config key `feedback_targets` holds the target a human
approved per layer. Nothing is sent to an unapproved or changed target. Names in tests and docs are neutral examples.
"""
import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
import uuid
import yaml
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from . import delivery, layers, winacl
from .common import add_project_arguments, emit, load_layer_project, redact

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

DRAFT_KINDS = ('bug', 'change', 'tool', 'docs')
MAX_FIELD = 4000
MAX_TOTAL = 20000
MAX_TITLE = 120
MAX_LIST = 20
MAX_TARGET = 200
_FB_TAG = re.compile(r'fb-[0-9a-f]{12}')
_CAUSE_KEY = re.compile(r'[a-z0-9][a-z0-9-]{0,47}')
_RETRO = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}/P[0-9]{1,4}')
_TARGET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/@ -]{0,199}')
_DRAFT_ID = re.compile(r'F[1-9][0-9]{0,5}')
DRAFT_KEYS = ('layer', 'kind', 'title', 'target', 'problem', 'evidence', 'proposal', 'acceptance', 'cause_key', 'core_candidate', 'retro')
STORE_DIR = ('.skillsync', 'feedback')


class FeedbackError(ValueError):
    """A draft, a store or a request that cannot be used (the message is safe to show: no draft text is quoted).

    `code` names a refusal of the consent path for a caller that must tell the cases apart (`card`, `no_preview`, `content_changed`, `stale`, `sent`, `scan`)."""

    def __init__(self, message='', code=None):
        super().__init__(message)
        self.code = code


def clean_text(value):
    """The text without control characters (a new line and a tab stay as spaces and new lines) and without bidi/format characters (category Cf)."""
    out = []
    for char in str(value).replace('\r\n', '\n').replace('\r', '\n'):
        if char in '\n\t':
            out.append(char)
        elif unicodedata.category(char) in ('Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp'):
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
    if not isinstance(layer, str) or not layers._ID.fullmatch(layer) or len(layer) > 54:
        raise FeedbackError('draft: `layer` must match [a-z0-9][a-z0-9-]* (at most 54 characters)')
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
    if (not _TARGET.fullmatch(target) or target != target.strip() or target.startswith('/') or target.endswith('/')
            or any(part in ('', '.', '..') for part in target.split('/'))):
        raise FeedbackError('draft: `target` must be a relative path of a layer file or a skill name')
    result = {'layer': layer, 'kind': draft['kind'], 'title': _field(draft, 'title', MAX_TITLE), 'target': target,
              'problem': _field(draft, 'problem'), 'evidence': _pairs(draft, 'evidence', 'source', 'note'), 'proposal': _field(draft, 'proposal'),
              'acceptance': _pairs(draft, 'acceptance', 'input', 'expected'), 'cause_key': cause, 'core_candidate': candidate}
    if '\n' in result['title']:
        raise FeedbackError('draft: `title` must be one line')
    if retro is not None:
        result['retro'] = retro
    total = len(json.dumps(result, ensure_ascii=False))
    if total > MAX_TOTAL + 4000:   # the text of all fields (20 000) plus the JSON overhead of the keys and the lists
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
    if item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 1):
        raise FeedbackError('render: the item must be a positive integer')
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
               'tags': sorted(tag for tag in rendered['tags'] if not _FB_TAG.fullmatch(tag))}
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


def _private(path):
    """Owner-only access for a file or a directory of the store: POSIX has the mode 0600 from `os.open`, Windows the ACL through the shared helper (never raises)."""
    return winacl.restrict_to_owner(path, emit=False)


def _safe_dir(store, create=False):
    store = Path(store)
    if store.is_symlink() or store.parent.is_symlink():
        raise FeedbackError('the feedback store must not be a symlink')
    if create:
        store.mkdir(parents=True, exist_ok=True)
        _private(store)   # Windows: the directory's ACL is inherited by the files created in it
    return store


def write_json_atomic(path, data):
    """Atomic JSON write, mode 0600 (a temporary file in the same directory, `os.replace`)."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    _private(tmp)   # Windows: `os.replace` hands the ACL of the source to the target, so it is set before the rename
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
    except (OSError, ValueError, RecursionError):
        raise FeedbackError(f'{path.name} is not valid JSON') from None


def next_draft_id(store):
    """`F<n>` with the next number free in the store (drafts, `.sent.json` and `.sending.json` all hold their numbers)."""
    store = Path(store)
    used = [int(m.group(1)) for path in store.glob('F*') if (m := re.match(r'^F([1-9][0-9]{0,5})[.]', path.name))] if store.is_dir() else []
    return f'F{max(used, default=0) + 1}'


def save_draft(store, draft, draft_id=None):
    """Validate and save a draft in the store; returns `(id, cleaned draft)`. Without `draft_id` a new number is taken."""
    cleaned = validate_draft(draft)
    store = _safe_dir(store, create=True)
    with store_lock(store):   # two sessions (the main checkout and a worktree) share the store: one number, one writer
        draft_id = draft_id or next_draft_id(store)
        write_json_atomic(draft_path(store, draft_id), cleaned)
    return draft_id, cleaned


def load_draft(store, draft_id):
    _safe_dir(store)
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
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'), re.compile(r'github_pat_[A-Za-z0-9_]{20,}'), re.compile(r'glpat-[A-Za-z0-9_-]{15,}'),
    re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}'), re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'), re.compile(r'\bAIza[0-9A-Za-z_-]{30,}'),
    re.compile(r'\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}'), re.compile(r'\bnpm_[A-Za-z0-9]{30,}'),
    re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----'), re.compile(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}'),
    re.compile(r'(?i)(?:accountkey|sharedaccesskey|sig)=[A-Za-z0-9+/%=_-]{16,}'),
    re.compile(r'(?<![A-Za-z0-9])[a-z2-7]{52}(?![A-Za-z0-9])'),   # the shape of an Azure DevOps personal access token (52 base32 characters)
    # a name that says "secret" followed by a value: `client_secret=...`, `"token": "..."`, `AZURE_DEVOPS_EXT_PAT=...`, `x-api-key: ...`
    re.compile(r'(?i)(?<![a-z0-9])[\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential|_pat)[\w.-]*["\']?\s*[:=]\s*["\']?[^\s,;"\'&<>]{8,}'),
    re.compile(r'(?i)\b(?:https?|ssh)://[^/\s:@]+:[^/\s@]{3,}@'),   # credentials in a URL
)


def secret_findings(texts):
    """`[{label, category: 'secret'}]` for the texts `[(label, text)]` that contain a secret-shaped string (never the string itself).

    The identity scan of the leak guard does not look for credentials, so this is the second part of the scan (conservative: a false positive stops the
    send). It is a list of known shapes, not a proof: the human preview remains the main control)."""
    # `redact` is what the status output masks (secrets of the environment of this machine, authorization headers): a text it would change is shown masked in
    # the preview but SENT as written, so the preview would not be the sent text: such a text is a finding too.
    return [{'label': label, 'category': 'secret'} for label, text in texts
            if any(pattern.search(text) for pattern in _SECRET_PATTERNS) or redact(text) != text]


def leak_scan(config, layer_id, payload, *, directory):
    """The leak guard scan of the payload as it is sent (`final_payload`: title, every tag, body or comment), against the identities of the OTHER layers.

    The scan target is the id of the destination layer given explicitly (the owner of the tracker the text goes to), never chosen from a repository.
    Returns `{status, coverage, findings, warnings, reason}`: `status` PASS, FAIL (a finding) or BLOCKED (bad input: also a stop); a finding carries the
    label of the text and the category, never the text. `coverage` `generic` means no identity files were found, so client names are NOT checked
    (the preview says so); `partial` and `full` follow the leak guard. `directory` is any existing directory (the scan needs no repository)."""
    from . import leakguard
    try:
        inputs = layers.leakguard_inputs(config, target=layer_id)
        plain = html.unescape(re.sub(r'<[^>]+>', ' ', payload['body']))   # the same text without the HTML escaping (`&#x27;` splits a name)
        texts = [('title', payload['title']), ('tags', '\n'.join(payload['tags'])), ('body', payload['body']), ('body-text', plain)]
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


# --------------------------------------------------------------------------- F-5: Azure Boards through the internal provider `azure-feedback`

PROVIDER = 'azure-feedback'
Context = namedtuple('Context', 'root manifest library')   # what `delivery.tracker` needs (`common.load_layer_project`)
_TAG = re.compile(r'(?:fbk|fb)-[0-9a-f]{12}')
CLOSED_STATES = ("'Done'", "'Closed'", "'Removed'")   # the end states of the Basic, Agile, Scrum and CMMI processes (`System.StateCategory` is not a WIQL field)


def build_wiql(project, tag, *, open_only):
    """The WIQL for the items that carry `tag` (a `fb-`/`fbk-` tag): the values are validated here, so no quote or operator of a value reaches the query.
    `open_only`: only items not completed or removed (the deduplication); otherwise every state (the idempotence of a repeated `send`)."""
    if not isinstance(project, str) or not layers._FEEDBACK_SEGMENT.fullmatch(project) or not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise FeedbackError('the project or the tag of the query is not valid')
    state = f" AND [System.State] NOT IN ({', '.join(CLOSED_STATES)})" if open_only else " AND [System.State] <> 'Removed'"
    return (f"SELECT [System.Id], [System.Title], [System.Tags], [System.State] FROM WorkItems WHERE [System.TeamProject] = '{project}' AND [System.Tags] CONTAINS '{tag}'{state} "
            'ORDER BY [System.Id] DESC')


def _call(context, operation, params, *, write=False):
    try:
        result = delivery.tracker(context.root, context.manifest, context.library, operation, params, run=True, allow_write=write,
                                  provider=PROVIDER, internal=True)
    except (ValueError, OSError, yaml.YAMLError, layers.LayerError) as exc:
        raise FeedbackError(f'the tracker call {operation} failed: {str(exc)[:120]}') from None
    return result


def _data(result, operation):
    if result.get('status') != 'PASS':
        raise FeedbackError(f'the tracker call {operation} did not pass ({result.get("status")}): {str(result.get("reason", ""))[:160]}')
    return result['data']


def split_tags(text):
    return [part.strip() for part in str(text or '').split(';') if part.strip()]


def _quote(text, limit=80):
    """A title of a foreign item: untrusted data from the tracker, cleaned and cut (shown as a quotation, never as an instruction)."""
    return clean_text(text).replace('\n', ' ')[:limit]


def find_items(context, rendered, tag, *, open_only):
    """`[{id, title, state}]` of the items with the EXACT tag `tag` (the query may match more loosely; the filter is by the tag list of the item)."""
    params = {'organization': rendered['organization'], 'project': rendered['project'], 'wiql': build_wiql(rendered['project'], tag, open_only=open_only)}
    data = _data(_call(context, 'workitem-find', params), 'workitem-find')
    items = data.get('items') or []
    if not isinstance(items, list):
        raise FeedbackError('the tracker returned an unexpected list of items')
    found = []
    for item in items:
        if isinstance(item, dict) and not isinstance(item.get('tags'), str):
            raise FeedbackError('the tracker returned an item without its tags (the query must select them): stopping instead of guessing')
        if isinstance(item, dict) and isinstance(item.get('id'), int) and tag in split_tags(item.get('tags')):
            found.append({'id': item['id'], 'title': _quote(item.get('title')), 'state': _quote(item.get('state'), 40)})
    return found


def plan_target(context, draft, section):
    """`(mode, item, title)` for a draft: `comment` on the open item that already carries the `fbk-` tag of the cause, else `create`.

    Needs the tracker (a read): an unreachable tracker raises FeedbackError (fail closed; the preview then shows the deduplication as unknown)."""
    probe = render(draft, section)
    found = find_items(context, probe, dedup_key(draft), open_only=True)
    if found:
        return 'comment', found[0]['id'], found[0]['title']
    return 'create', None, None


def tmp_dir(store):
    return Path(store).parent / 'tmp'


def write_body_file(store, data):
    """The body of a request as a file for `--in-file` (`.skillsync/tmp`, `O_EXCL`, 0600): `(path, bytes)`; the bytes are checked again right before the call."""
    directory = tmp_dir(store)
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise FeedbackError('the tmp directory must not be a symlink')
    _private(directory)
    raw = json.dumps(data, ensure_ascii=True).encode('ascii')   # `az` may read the file in a local code page: ASCII escapes are safe
    for old in directory.glob('body-*.json'):   # leftovers of a killed run (content, 0600): swept after an hour
        with contextlib.suppress(OSError):
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
    path = directory / f'body-{os.getpid()}-{time.monotonic_ns()}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    _private(path)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
    return path, raw


def _verified(path, raw):
    try:
        if Path(path).is_symlink() or Path(path).read_bytes() != raw:
            raise FeedbackError('the request file changed after it was written')
    except OSError:
        raise FeedbackError('the request file is unreadable') from None


def _patch(rendered, payload):
    fields = [('System.Title', payload['title']), ('System.Description', payload['body']), ('System.Tags', '; '.join(payload['tags']))]
    if rendered.get('area'):
        fields.append(('System.AreaPath', rendered['project'] + '\\' + rendered['area'].replace('/', '\\')))
    return [{'op': 'add', 'path': f'/fields/{name}', 'value': value} for name, value in fields]


def _comment_text(payload):
    return html.escape(payload['body']).replace('\n', '<br>')


def _sent_path(store, draft_id):
    return draft_path(store, draft_id).with_name(f'{draft_id}.sent.json')


def _sending_path(store, draft_id):
    return draft_path(store, draft_id).with_name(f'{draft_id}.sending.json')


def _finish(store, draft_id, payload, mode, item, approval, item_id, url, retro=None):
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    record = {'id': item_id, 'url': url, 'mode': mode, 'item': item, 'sha': payload['sha'], 'tag': payload['tag'], 'approval': approval, 'at': now}
    if retro:   # the link to a retro proposal lives here, not in the retro registers (their contract does not change)
        record['retro'] = retro
    write_json_atomic(_sent_path(store, draft_id), record)
    with contextlib.suppress(OSError):
        os.unlink(_sending_path(store, draft_id))
    return {'status': 'PASS', 'mode': mode, 'id': item_id, 'url': url, 'tag': payload['tag']}


SEND_LOCK_STALE = 900   # seconds: longer than the sum of the timeouts of one send (a write 120 s, its readback 60 s, a lookup 60 s)


@contextlib.contextmanager
def _send_lock(store, draft_id):
    """One send of a draft at a time (`F<n>.sending.lock`, `O_EXCL`); a lock older than `SEND_LOCK_STALE` is taken over (a killed process)."""
    path = _sending_path(store, draft_id).with_name(f'{draft_id}.sending.lock')
    for _ in range(2):
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - path.stat().st_mtime > SEND_LOCK_STALE:
                    os.unlink(path)
                    continue
            raise FeedbackError(f'another send of {draft_id} is in progress') from None
    else:
        raise FeedbackError(f'another send of {draft_id} is in progress')
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def read_intent(store, draft_id):
    """The intent file `F<n>.sending.json` of an interrupted send (`{sha, tag, mode, item, approval, uncertain}`) or None."""
    intent = read_json_file(_sending_path(store, draft_id))
    if intent is None:
        return None
    if (not isinstance(intent, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(intent.get('sha'))) or intent.get('mode') not in ('create', 'comment')
            or (intent.get('item') is not None and (isinstance(intent['item'], bool) or not isinstance(intent['item'], int) or intent['item'] < 1))):
        raise FeedbackError(f'{draft_id}.sending.json is not valid: check the tracker for the tag it names, then remove it')
    return intent


def send_payload(context, store, draft_id, rendered, *, approval_id=None, retro=None):
    """Create the work item (or comment on the open one) for `rendered`, once and verifiably. Returns `{status, ...}`; raises FeedbackError before any
    mutation when it cannot go on (fail closed).

    The caller (`skillsync feedback send`) has already checked the human approval of THIS content. Here: one send of a draft at a time (a lock file); an
    atomic intent file `F<n>.sending.json` (the sha, the mode and the item, and `uncertain`: a write was started and its outcome is not known); the request
    file is verified before the call; the write goes through the guarded tracker with a readback; the result is `F<n>.sent.json`. After an interrupted send
    the intent is found again: the new run continues with the sha, the mode and the item of the intent (see `read_intent`: the deduplication would see the
    own element), looks for its own tag or marker, and when the outcome was uncertain and nothing is found it STOPS for a human instead of writing again."""
    with _send_lock(store, draft_id):
        return _send_locked(context, store, draft_id, rendered, approval_id, retro)


def _send_locked(context, store, draft_id, rendered, approval_id, retro=None):
    payload = final_payload(rendered)
    sending = _sending_path(store, draft_id)
    intent = read_intent(store, draft_id)
    sent = read_json_file(_sent_path(store, draft_id))
    if intent is None and isinstance(sent, dict) and sent.get('sha') == payload['sha']:   # a send that ended while this one waited for the lock
        raise FeedbackError(f'{draft_id} was just sent (see {draft_id}.sent.json)')
    if intent is None and approval_id and approval_id in consumed_approvals(store):   # (a resumed send holds its own consent through the intent)
        raise FeedbackError('the consent was already used')
    if intent is not None:
        if intent['sha'] != payload['sha']:
            raise FeedbackError('an interrupted send of this draft exists for different content: check the tracker for the tag in '
                                f'{draft_id}.sending.json, then remove that file')
        done = _already_sent(context, rendered, payload)
        if done:
            return _finish(store, draft_id, payload, rendered['mode'], rendered['item'], intent.get('approval') or approval_id, done['id'], done.get('url'), retro)
        if intent.get('uncertain', True):   # a missing key (an intent of an older version) counts as uncertain: fail closed; `uncertain: false` = nothing was written
            raise FeedbackError(f'a previous attempt may have written to the tracker and its result cannot be confirmed (tag {payload["tag"]}): check '
                                f'the tracker, then remove {draft_id}.sending.json to allow a new send')
    else:
        intent = {'sha': payload['sha'], 'tag': payload['tag'], 'mode': rendered['mode'], 'item': rendered['item'], 'approval': approval_id,
                  'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'uncertain': False}
    base = {'organization': rendered['organization'], 'project': rendered['project']}
    if rendered['mode'] == 'create':
        path, raw = write_body_file(store, _patch(rendered, payload))
        params = {**base, 'type': rendered['type'], 'body_file': str(path), 'wiql': build_wiql(rendered['project'], payload['tag'], open_only=False)}
        operation = 'workitem-create'
    else:
        path, raw = write_body_file(store, {'text': _comment_text(payload)})
        params = {**base, 'item': rendered['item'], 'body_file': str(path)}
        operation = 'workitem-comment'
    try:
        _verified(path, raw)
        write_json_atomic(sending, {**intent, 'uncertain': True})   # from here a write may have happened, whatever the outcome
        result = _call(context, operation, params, write=True)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    if result.get('status') == 'PASS':
        data = result['data']
        return _finish(store, draft_id, payload, rendered['mode'], rendered['item'], intent.get('approval') or approval_id, data.get('id'), data.get('url'), retro)
    if result.get('status') == 'FAIL':   # `az` exited with an error: nothing was written, the retry is safe
        write_json_atomic(sending, {**intent, 'uncertain': False})
    return {'status': result.get('status', 'BLOCKED'), 'reason': str(result.get('reason', ''))[:200], 'tag': payload['tag'],
            'mutation_completed': bool(result.get('mutation_completed')), 'uncertain': result.get('status') != 'FAIL'}


def _already_sent(context, rendered, payload):
    """For a resumed send: `{id, url}` of the element or comment that carries our tag or marker, else None."""
    if rendered['mode'] == 'create':
        probe = {**rendered, 'tags': []}
        found = find_items(context, probe, payload['tag'], open_only=False)
        return {'id': found[0]['id'], 'url': None} if found else None
    data = _data(_call(context, 'workitem-last-comment', {'organization': rendered['organization'], 'project': rendered['project'],
                                                           'item': rendered['item']}), 'workitem-last-comment')
    if f'[{payload["tag"]}]' in str(data.get('text', '')):
        return {'id': data.get('id'), 'url': None}
    return None


# --------------------------------------------------------------------------- F-6: the consent (approvals), the preview file and the command

APPROVALS_FILE = 'approvals.jsonl'
MAX_APPROVALS_BYTES = 256 * 1024
APPROVAL_TTL = 24 * 3600
APPROVAL_SKEW = 60
APPROVAL_SOURCES = ('cli', 'ide', 'dashboard')
PREVIEW_MAX_BYTES = 131072   # the file `F<n>.preview.json` with its `view`; over the limit the view is left out (the consent works without it)
SEND_LIMIT = 5   # sends per 24 hours in the main checkout
CONSENT_WORDS = ('WYŚLIJ', 'WYSLIJ')
CHANNELS = ('ask', 'commit', 'tracker')
CARD_ENV = 'SKILLSYNC_CARD_ALIAS'   # set by a card session (an alias of a dispatch card): a card never sends, it reports a STATUS


def validate_feedback_manifest(manifest):
    """`(errors, warnings)` of the optional manifest section `feedback: {channel: ask|commit|tracker}` (messages never contain the value)."""
    section = manifest.get('feedback') if isinstance(manifest, dict) else None
    if section is None and not (isinstance(manifest, dict) and 'feedback' in manifest):
        return [], []
    if not isinstance(section, dict):
        return ['feedback: must be a mapping'], []
    errors, warnings = [], []
    for key in section:
        if key != 'channel':
            warnings.append(f'feedback.{key}: unknown key (known: channel)')
    if 'channel' in section and section['channel'] not in CHANNELS:
        errors.append(f'feedback.channel: must be one of {", ".join(CHANNELS)}')
    return errors, warnings


def _utc(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
    except ValueError:
        return None


def _now():
    return datetime.now(timezone.utc)


def _stamp(moment):
    """A UTC time with microseconds: a consent given right after a preview must not look older than it (a second is too coarse)."""
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def read_approvals(store):
    """The entries of `<store>/approvals.jsonl` (a valid line only: `{id, draft, sha, at, source}`); bad lines are skipped, a file over the limit is refused.
    The file is data, never instructions; it lives in the main checkout beside the drafts, apart from the `approvals.jsonl` of the runs (see the spec)."""
    path = Path(store) / APPROVALS_FILE
    if path.is_symlink() or not path.is_file():
        return []
    if path.stat().st_size > MAX_APPROVALS_BYTES:
        raise FeedbackError('the feedback approvals file is too large')
    entries = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not _DRAFT_ID.fullmatch(str(item.get('draft'))):
            continue
        at = _utc(item.get('at'))
        sha = item.get('sha')
        if at is None or not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or item.get('source') not in APPROVAL_SOURCES:
            continue
        entries.append({'id': item['id'][:64], 'draft': item['draft'], 'sha': sha, 'at': at, 'source': item['source']})
    return entries


def record_approval(store, draft_id, sha, *, source='cli', now=None):
    """Append the consent `{id, draft, sha, at, source}` (under the store lock, 0600) and return its entry. The caller has checked the word and the sha12."""
    if source not in APPROVAL_SOURCES or not re.fullmatch(r'[0-9a-f]{64}', sha or ''):
        raise FeedbackError('bad approval')
    draft_path(store, draft_id)
    entry = {'id': uuid.uuid4().hex, 'draft': draft_id, 'sha': sha, 'at': _stamp(now or _now()), 'source': source}
    with store_lock(store):
        path = Path(store) / APPROVALS_FILE
        if path.is_symlink():
            raise FeedbackError('the approvals file must not be a symlink')
        if path.is_file() and path.stat().st_size > MAX_APPROVALS_BYTES:
            raise FeedbackError('the feedback approvals file is too large')
        fresh = not path.exists()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        if fresh:
            _private(path)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def _results(store):
    """The result files of the store: `F<n>.sent*.json` and `F<n>.sending.json` (records)."""
    store = Path(store)
    records = []
    for path in sorted(store.glob('F*.json')) if store.is_dir() else []:
        if re.match(r'^F[1-9][0-9]{0,5}[.](?:sent|sending)', path.name):
            with contextlib.suppress(FeedbackError):
                record = read_json_file(path)
                if isinstance(record, dict):
                    records.append((path.name, record))
    return records


def consumed_approvals(store):
    return {record.get('approval') for _, record in _results(store) if record.get('approval')}


def preview_path(store, draft_id):
    return draft_path(store, draft_id).with_name(f'{draft_id}.preview.json')


def find_approval(store, draft_id, sha, preview_at, *, now=None, entries=None, used=None):
    """The latest consent that lets `send` go on, or None: for THIS draft and the full sha of the current content, not used yet, given after the
    last preview of this content, at most 24 hours old and not from the future. `entries` and `used` (optional) are the already read approvals
    and used consent ids (the status page reads them once for all drafts)."""
    now = now or _now()
    used = consumed_approvals(store) if used is None else used
    good = [item for item in (read_approvals(store) if entries is None else entries)
            if item['draft'] == draft_id and item['sha'] == sha and item['id'] not in used and item['at'] >= preview_at
            and (now - item['at']).total_seconds() <= APPROVAL_TTL and (item['at'] - now).total_seconds() <= APPROVAL_SKEW]
    return max(good, key=lambda item: item['at']) if good else None


def sends_in_window(store, *, now=None):
    now = now or _now()
    count = 0
    for name, record in _results(store):
        at = _utc(record.get('at'))
        if '.sent' in name and '.sending' not in name and at and (now - at).total_seconds() <= APPROVAL_TTL:
            count += 1
    return count


class _Session:
    """What the commands share: the project, the main-checkout store, the machine config, the draft and its target."""

    def __init__(self, args):
        self.root, self.manifest, self.library = load_layer_project(args.path, args.layers)
        self.store = store_root(self.root)
        self.config = layers.load_machine_config()

    def layer(self, layer_id):
        return next((layer for layer in self.library.layers if layer.id == layer_id), None)

    def section(self, layer_id):
        layer = self.layer(layer_id)
        return ((layer.meta or {}).get('feedback')) if layer else None

    def trusted_section(self, layer_id):
        section = self.section(layer_id)
        state = target_state(self.config, layer_id, section)['state']
        if state != TRUSTED:
            hint = {NO_SECTION: 'the layer has no valid `feedback:` section', NOT_TRUSTED: 'the target is not approved on this machine: run `skillsync feedback trust '
                    f'{layer_id}` in a terminal', CHANGED: 'the organization or project of the layer changed: run `skillsync feedback trust '
                    f'{layer_id}` again'}[state]
            raise FeedbackError(f'layer {layer_id}: {hint}')
        return section

    def context(self):
        return Context(self.root, self.manifest, self.library)


def open_session(path, layers=None):
    """The session of a project outside the CLI (the status page): the same project, store and machine config as the commands use."""
    return _Session(argparse.Namespace(path=path, layers=layers))


def _prepare(session, draft_id):
    """`(draft, section, mode, item, item_title, rendered, payload, scan)` of a draft against the current tracker state (raises FeedbackError)."""
    draft = load_draft(session.store, draft_id)
    section = session.trusted_section(draft['layer'])
    intent = read_intent(session.store, draft_id)
    if intent is not None:   # an interrupted send: the same mode and item as before (the deduplication would see its own element)
        mode, item, item_title = intent['mode'], intent['item'], None
    else:
        mode, item, item_title = plan_target(session.context(), draft, section)
    rendered = render(draft, section, mode=mode, item=item)
    payload = final_payload(rendered)
    scan = leak_scan(session.config, draft['layer'], payload, directory=Path(session.store).parent)
    return draft, section, rendered, payload, scan, item_title


def _scan_view(scan):
    return {'status': scan['status'], 'coverage': scan['coverage'], 'findings': scan['findings'], 'warnings': scan['warnings'][:5],
            **({'reason': scan['reason']} if scan.get('reason') else {})}


def cmd_new(args):
    session = _Session(args)
    text = sys.stdin.read(200_001) if args.file == '-' else Path(args.file).read_text(encoding='utf-8')[:200_001]
    if len(text) > 200_000:
        raise FeedbackError('the draft file is too large')
    draft_id, draft = save_draft(session.store, json.loads(text))
    return {'status': 'PASS', 'draft': draft_id, 'layer': draft['layer'], 'next': f'skillsync feedback draft {draft_id}'}


def cmd_targets(args):
    session = _Session(args)
    rows = []
    for layer in session.library.layers:
        section = (layer.meta or {}).get('feedback')
        if section or (layer.meta or {}).get('feedback_errors'):
            rows.append({'layer': layer.id, 'target': ({k: section[k] for k in ('kind', 'organization', 'project', 'area', 'type')} if section else None),
                         'state': target_state(session.config, layer.id, section)['state']})
    return {'status': 'PASS', 'targets': rows}


def cmd_trust(args):
    session = _Session(args)
    section = session.section(args.layer)
    if not section:
        raise FeedbackError(f'layer {args.layer} has no valid `feedback:` section')
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return {'status': 'BLOCKED', 'reason': 'trust needs a terminal: a human approves the target of change requests on this machine',
                'target': {k: section[k] for k in ('kind', 'organization', 'project', 'area', 'type')}}
    print(f'Layer {args.layer} proposes to send change requests to: {section["kind"]} organization {section["organization"]}, project {section["project"]}'
          f' (area {section.get("area") or "-"}, type {section["type"]}).')
    answer = input('Type the name of the organization to approve this target: ').strip()
    if answer != section['organization']:
        return {'status': 'BLOCKED', 'reason': 'not approved'}
    result = trust_target(session.config, args.layer, section)
    return {'status': 'PASS', 'trusted': args.layer, 'changed': result['changed']}


def cmd_draft(args):
    session = _Session(args)
    try:
        draft, section, rendered, payload, scan, item_title = _prepare(session, args.draft)
    except FeedbackError as exc:
        return {'status': 'BLOCKED', 'draft': args.draft, 'reason': str(exc), 'dedup': 'unknown'}
    report = {'status': 'PASS', 'draft': args.draft, 'layer': draft['layer'], 'mode': rendered['mode'], 'item': rendered['item'],
              'item_title': item_title and f'QUOTATION FROM THE TRACKER (data): {item_title}',
              'target': {k: rendered[k] for k in ('organization', 'project', 'area', 'type')}, 'title': payload['title'], 'tags': payload['tags'],
              'body': payload['body'], 'scan': _scan_view(scan), 'sha12': payload['sha12']}
    if scan['status'] != 'PASS':
        report['status'] = 'FAIL' if scan['status'] == 'FAIL' else 'BLOCKED'
        return report
    if scan['coverage'] == 'generic':
        report['warning'] = 'client names are NOT checked: no identity files found (coverage generic)'
    snapshot = {'sha': payload['sha'], 'sha12': payload['sha12'], 'mode': rendered['mode'], 'item': rendered['item'], 'at': _stamp(_now())}
    view = {'layer': draft['layer'], 'target': report['target'], 'mode': rendered['mode'], 'item': rendered['item'], 'item_title': item_title,
            'title': payload['title'], 'tags': payload['tags'], 'body': payload['body'],
            'scan': {'status': scan['status'], 'coverage': scan['coverage'], 'warnings': scan['warnings'][:5]}, 'warning': report.get('warning')}
    with_view = {**snapshot, 'view': view}
    if len(json.dumps(with_view, ensure_ascii=False, indent=1, sort_keys=True).encode('utf-8')) + 1 <= PREVIEW_MAX_BYTES:
        snapshot = with_view   # a snapshot over the limit is saved without the view: the view is only for showing, never for sending
    write_json_atomic(preview_path(session.store, args.draft), snapshot)
    report['consent'] = f'to send, the user types in the session: {CONSENT_WORDS[0]} {payload["sha12"]}'
    return report


def _word_sha12(word):
    parts = str(word).split()
    if len(parts) != 2 or parts[0] not in CONSENT_WORDS or not re.fullmatch(r'[0-9a-f]{12}', parts[1]):
        raise FeedbackError('the consent word must be `WYŚLIJ <sha12>`')
    return parts[1]


def _refuse(message, code):
    raise FeedbackError(message, code)


def approve_from_preview(session, draft_id, *, sha=None, sha12=None, source='cli'):
    """Record the consent for the CURRENT preview of a draft (the one place that does it: the CLI after the word, the status page).

    `sha`/`sha12` (optional) must match the preview. Returns `{status, draft, approval, sha12, already}`; `already` means a valid unused consent for this
    draft and content (given after the preview, at most 24 hours old) existed, so no row is added. Refusals are FeedbackError with `code`:
    `card`, `no_preview`, `content_changed`, `stale` (the target or the preview too old), `scan` (the preview did not pass), `sent`."""
    if os.environ.get(CARD_ENV):
        _refuse('a card session does not record a consent: the user types the word in the session that sends', 'card')
    if source not in APPROVAL_SOURCES:
        raise FeedbackError('bad approval source')
    store = session.store
    preview = read_json_file(preview_path(store, draft_id))
    if not isinstance(preview, dict) or not isinstance(preview.get('sha'), str) or not re.fullmatch(r'[0-9a-f]{64}', preview['sha']):
        _refuse(f'no preview of {draft_id}: run `skillsync feedback draft {draft_id}` first', 'no_preview')
    view = preview.get('view')
    if isinstance(view, dict) and isinstance(view.get('scan'), dict) and view['scan'].get('status') != 'PASS':
        _refuse('the preview did not pass the leak scan: run `draft` again', 'scan')
    preview_at = _utc(preview.get('at'))
    if preview_at is None or (_now() - preview_at).total_seconds() > APPROVAL_TTL:
        _refuse('the preview is too old: run `draft` again', 'stale')
    if (sha is not None and sha != preview['sha']) or (sha12 is not None and sha12 != preview['sha'][:12]):
        _refuse('the consent does not match the previewed content', 'content_changed')
    draft = load_draft(store, draft_id)
    try:
        section = session.trusted_section(draft['layer'])
    except FeedbackError as exc:
        raise FeedbackError(str(exc), 'stale') from None
    rendered = render(draft, section, mode=preview.get('mode', 'create'), item=preview.get('item'))
    if content_sha(rendered) != preview['sha']:
        _refuse('the draft or the target changed after the preview: run `draft` again', 'content_changed')
    used = consumed_approvals(store)
    sent_here = any('.sent' in name and '.sending' not in name and record.get('sha') == preview['sha'] for name, record in _results(store))
    if sent_here or any(item['draft'] == draft_id and item['sha'] == preview['sha'] and item['id'] in used and item['at'] >= preview_at
                        for item in read_approvals(store)):
        _refuse(f'{draft_id} with this content was already sent: change the draft and run `draft` again to send it once more', 'sent')
    existing = find_approval(store, draft_id, preview['sha'], preview_at)
    if existing is not None:
        return {'status': 'PASS', 'draft': draft_id, 'approval': existing['id'], 'sha12': preview['sha'][:12], 'already': True}
    entry = record_approval(store, draft_id, preview['sha'], source=source)
    return {'status': 'PASS', 'draft': draft_id, 'approval': entry['id'], 'sha12': preview['sha'][:12], 'already': False}


def cmd_approve(args):
    if os.environ.get(CARD_ENV):
        raise FeedbackError('a card session does not record a consent: the user types the word in the session that sends', 'card')
    session = _Session(args)
    sha12_word = _word_sha12(args.word)
    result = approve_from_preview(session, args.draft, sha12=sha12_word, source=args.source)
    return {key: result[key] for key in ('status', 'draft', 'approval', 'sha12')}


def cmd_send(args):
    session = _Session(args)
    if os.environ.get(CARD_ENV):
        return {'status': 'BLOCKED', 'reason': 'a card session does not send: report a STATUS to the user with the preview and the sha12'}
    from . import retro_scan
    try:
        if retro_scan.linked_worktree(session.root):
            return {'status': 'BLOCKED', 'reason': 'send runs in the main checkout, not in a worktree'}
    except ValueError as exc:
        return {'status': 'BLOCKED', 'reason': str(exc)}
    store = session.store
    sent_file = _sent_path(store, args.draft)
    if read_json_file(sent_file) is not None and not args.replace:
        return {'status': 'BLOCKED', 'reason': f'{args.draft} was already sent (use --replace and a new consent to send it again)'}
    if sends_in_window(store) >= SEND_LIMIT:
        return {'status': 'BLOCKED', 'reason': f'the limit of {SEND_LIMIT} sends per 24 hours is reached'}
    draft, section, rendered, payload, scan, _ = _prepare(session, args.draft)
    if scan['status'] != 'PASS':
        return {'status': 'FAIL' if scan['status'] == 'FAIL' else 'BLOCKED', 'reason': 'the leak scan did not pass', 'scan': _scan_view(scan)}
    preview = read_json_file(preview_path(store, args.draft))
    preview_at = _utc((preview or {}).get('at')) if isinstance(preview, dict) and preview.get('sha') == payload['sha'] else None
    if preview_at is None:
        return {'status': 'BLOCKED', 'reason': f'no preview of this content: run `skillsync feedback draft {args.draft}`', 'sha12': payload['sha12']}
    intent = read_intent(store, args.draft)
    if intent is not None and intent['sha'] == payload['sha']:   # an interrupted send continues with the consent it started with (used by the intent itself)
        approval = next((item for item in read_approvals(store) if item['id'] == intent.get('approval') and item['draft'] == args.draft
                         and item['sha'] == payload['sha']), None)
    else:
        approval = find_approval(store, args.draft, payload['sha'], preview_at)
    if approval is None:
        return {'status': 'BLOCKED', 'reason': 'no valid consent for this content', 'sha12': payload['sha12'],
                'consent': f'the user types: {CONSENT_WORDS[0]} {payload["sha12"]}'}
    if args.replace and read_json_file(sent_file) is not None:
        os.replace(sent_file, sent_file.with_name(f'{args.draft}.sent.{time.strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:6]}.json'))
    result = send_payload(session.context(), store, args.draft, rendered, approval_id=approval['id'], retro=draft.get('retro'))
    return result


def cmd_list(args):
    session = _Session(args)
    rows = []
    store = Path(session.store)
    for path in sorted(store.glob('F*.json')) if store.is_dir() else []:
        match = re.match(r'^(F[1-9][0-9]{0,5})[.]json$', path.name)
        if match:
            draft_id = match.group(1)
            state = 'sent' if _sent_path(store, draft_id).is_file() else 'sending' if _sending_path(store, draft_id).is_file() else \
                'previewed' if preview_path(store, draft_id).is_file() else 'draft'
            row = {'draft': draft_id, 'state': state}
            sent = read_json_file(_sent_path(store, draft_id)) if state == 'sent' else None
            if isinstance(sent, dict):
                row.update({key: sent[key] for key in ('id', 'mode', 'retro') if key in sent})
            rows.append(row)
    return {'status': 'PASS', 'drafts': rows}



# --------------------------------------------------------------------------- the state of the drafts for the status page and `wait` (local files only)

STATE_LIMIT = 50   # elements of the state (the newest drafts); `total` counts all of them
_DRAFT_FILE = re.compile(r'(F[1-9][0-9]{0,5})[.]json')
_SHA = re.compile(r'[0-9a-f]{64}')


def _shared_reads(session, now):
    """What every draft of one state computation shares: the approvals, the result records (and the consents they used), the sends in the window."""
    store = session.store
    try:
        entries = read_approvals(store)
    except (FeedbackError, OSError):
        entries = []
    records = _results(store)
    return {'now': now, 'entries': entries, 'records': records, 'used': {record.get('approval') for _, record in records if record.get('approval')},
            'sends': sends_in_window(store, now=now)}


def _inspect(session, draft_id, shared):
    """`{state, reason, sha, preview, approval}` of one draft: `sent`, `stale`, `blocked`, `after` or `waiting` (checked in this order), from local files only
    (never the tracker). `sha` is the full sha of the preview, `preview` the preview (both None when it is missing or unusable), `approval` the valid unused
    consent for the current preview or None."""
    store, now = session.store, shared['now']
    hint = f'run `skillsync feedback draft {draft_id}`'

    def out(state, reason=None, sha=None, preview=None, approval=None):
        return {'state': state, 'reason': reason, 'sha': sha, 'preview': preview, 'approval': approval}

    try:
        preview = read_json_file(preview_path(store, draft_id))
    except FeedbackError:
        return out('stale', 'unreadable')
    sha = preview.get('sha') if isinstance(preview, dict) else None
    if preview is not None and not (isinstance(sha, str) and _SHA.fullmatch(sha)):
        return out('stale', 'unreadable')
    if preview is None:
        sha = None
    preview_at = _utc(preview.get('at')) if sha else None
    if sha:
        if any('.sent' in name and '.sending' not in name and record.get('sha') == sha for name, record in shared['records']) or (
                preview_at and any(item['draft'] == draft_id and item['sha'] == sha and item['id'] in shared['used'] and item['at'] >= preview_at
                                   for item in shared['entries'])):
            return out('sent', None, sha, preview)
    try:
        draft = load_draft(store, draft_id)
    except FeedbackError:
        return out('stale', 'unreadable', sha)
    if not sha:
        return out('stale', 'no preview')
    view = preview.get('view')
    if not isinstance(view, dict):
        return out('stale', 'no view: run feedback draft', sha, preview)
    if preview_at is None or (now - preview_at).total_seconds() > APPROVAL_TTL:
        return out('stale', 'the preview is too old', sha, preview)
    try:
        section = session.trusted_section(draft['layer'])
        current = content_sha(render(draft, section, mode=preview.get('mode', 'create'), item=preview.get('item')))
    except (FeedbackError, KeyError, TypeError, ValueError) as exc:
        return out('stale', str(exc)[:200] if isinstance(exc, FeedbackError) else 'unreadable', sha, preview)
    if current != sha:
        return out('stale', 'the draft or the target changed after the preview', sha, preview)
    approval = find_approval(store, draft_id, sha, preview_at, now=now, entries=shared['entries'], used=shared['used'])
    scan = view.get('scan')
    if isinstance(scan, dict) and scan.get('status') != 'PASS':
        return out('blocked', 'the leak scan did not pass', sha, preview, approval)
    if shared['sends'] >= SEND_LIMIT:
        return out('blocked', f'the limit of {SEND_LIMIT} sends per 24 hours is reached', sha, preview, approval)
    return out('after' if approval else 'waiting', None, sha, preview, approval)


def _state_row(draft_id, info, full):
    if not full:
        return {'draft': draft_id, 'state': info['state']}
    row = {'draft': draft_id, 'state': info['state'], 'reason': info['reason']}
    if info['sha']:
        row['sha12'] = info['sha'][:12]
    if info['state'] == 'stale':
        return row
    view = info['preview'].get('view') if isinstance(info['preview'], dict) else None
    scan = view.get('scan') if isinstance(view, dict) else None
    approval = info['approval']
    row.update({'sha': info['sha'], 'generic': not isinstance(scan, dict) or scan.get('coverage') == 'generic',
                'given': {'at': _stamp(approval['at']), 'source': approval['source']} if approval and info['state'] == 'after' else None})
    if info['state'] != 'sent':
        row['view'] = view
    return row


def drafts_state(session, *, full=True, now=None):
    """`{enabled, total, drafts}` for the status page: the newest `STATE_LIMIT` drafts (by number, the newest first) with their state, from local files only
    (no tracker, no write; damaged files make a draft `stale` with the reason `unreadable`). `full=False` (the read-only socket) gives each element only
    `{draft, state}`: no hash, no content, no target. `enabled`: a draft exists or a layer proposes a `feedback:` target."""
    store, now = Path(session.store), now or _now()
    numbers = []
    try:
        _safe_dir(store)
        if store.is_dir():
            numbers = sorted((int(match.group(1)[1:]) for path in store.glob('F*.json') if (match := _DRAFT_FILE.fullmatch(path.name))), reverse=True)
    except (FeedbackError, OSError):
        numbers = []
    shared = _shared_reads(session, now) if numbers else None
    rows = []
    for number in numbers[:STATE_LIMIT]:
        draft_id = f'F{number}'
        rows.append(_state_row(draft_id, _inspect(session, draft_id, shared), full))
    enabled = bool(numbers) or any((layer.meta or {}).get('feedback') for layer in session.library.layers)
    return {'enabled': enabled, 'total': len(numbers), 'drafts': rows}


def read_form(state):
    """The state of `drafts_state` reduced to the read-only form (`{draft, state}` per draft), for a role that must not see the content."""
    return {'enabled': bool(state.get('enabled')), 'total': state.get('total', 0),
            'drafts': [{'draft': row['draft'], 'state': row['state']} for row in state.get('drafts', []) if isinstance(row, dict)]}


def cmd_wait(args):
    """Waits for a valid consent of a draft (the same check as `send`: the draft, the full sha, unused, after the preview, at most 24 hours old). Read-only:
    PASS `{approval, sha12, source}` (exit 0), NOT_RUN after the timeout (exit 3, `_cmd`), BLOCKED for a bad use (an unknown, stale or sent draft; exit 2)."""
    if not _DRAFT_ID.fullmatch(args.draft or ''):
        raise FeedbackError('draft id must look like F<n>')
    if not (args.timeout >= 0 and args.interval > 0):
        raise FeedbackError('--timeout must not be negative and --interval must be positive')
    session = _Session(args)
    deadline = time.monotonic() + args.timeout
    if not (Path(session.store) / f'{args.draft}.json').is_file():
        raise FeedbackError(f'no draft {args.draft}')
    while True:
        info = _inspect(session, args.draft, _shared_reads(session, _now()))
        if info['state'] in ('stale', 'sent'):
            reason = f'{args.draft} was already sent' if info['state'] == 'sent' else f'{args.draft} is stale ({info["reason"]}): run `skillsync feedback draft {args.draft}`'
            return {'status': 'BLOCKED', 'draft': args.draft, 'reason': reason}
        if info['approval'] is not None:
            return {'status': 'PASS', 'draft': args.draft, 'approval': info['approval']['id'], 'sha12': info['sha'][:12], 'source': info['approval']['source']}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {'status': 'NOT_RUN', 'draft': args.draft, 'reason': 'no valid consent before the timeout'}
        time.sleep(min(args.interval, remaining))


_COMMANDS = {'new': cmd_new, 'targets': cmd_targets, 'trust': cmd_trust, 'draft': cmd_draft, 'approve': cmd_approve, 'send': cmd_send, 'list': cmd_list, 'wait': cmd_wait}


def _cmd(args):
    try:
        report = _COMMANDS[args.feedback_action](args)
    except layers.LayerError as exc:
        report = {'status': 'BLOCKED', 'reason': str(exc)}
    except (FeedbackError, OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError, yaml.YAMLError) as exc:   # a message of ours never quotes a draft; other errors are reported by type only
        report = {'status': 'BLOCKED', 'reason': str(exc)[:200] if isinstance(exc, FeedbackError) else f'feedback failed ({type(exc).__name__})'}
    code = emit(report, getattr(args, 'json', False))
    return 3 if report.get('status') == 'NOT_RUN' else code   # `common.exit_code` maps NOT_RUN to 0; a wait that timed out is 3


def register(subparsers):
    parser = subparsers.add_parser('feedback', help='Change requests to the owner of a layer: draft, preview, consent, send (CORE-1140-FEEDBACK)',
                                   description='Drafts and results live in the MAIN checkout (.skillsync/feedback). `send` needs the user\'s consent word '
                                               '`WYŚLIJ <sha12>` for the exact content and an approved target.')
    actions = parser.add_subparsers(dest='feedback_action', required=True)
    for name, help_text in (('new', 'Save a draft from a JSON file (`--file -` reads stdin) as F<n>'), ('targets', 'Layers that propose a target and its state on this machine'),
                            ('trust', 'Approve the proposed target of a layer on this machine (terminal only)'),
                            ('draft', 'Check a draft, scan for leaks, look for an open item and print the preview'),
                            ('approve', 'Record the consent word typed by the user for the previewed content'),
                            ('send', 'Send the draft: a new work item or a comment on the open one (needs the consent)'), ('list', 'Drafts and their state'),
                            ('wait', 'Wait for a valid consent of the draft (read-only): 0 consent, 3 timeout, 2 bad use')):
        sub = actions.add_parser(name, help=help_text)
        if name == 'trust':
            sub.add_argument('layer')
        elif name in ('draft', 'approve', 'send', 'wait'):
            sub.add_argument('draft', metavar='F<n>')   # before the optional project path: `skillsync feedback draft F1 [path]`
        add_project_arguments(sub)
        if name == 'new':
            sub.add_argument('--file', required=True)
        if name == 'approve':
            sub.add_argument('--word', required=True, help='the consent word typed by the user: `WYŚLIJ <sha12>`')
            sub.add_argument('--source', choices=('cli', 'ide'), default='cli')
        if name == 'wait':
            sub.add_argument('--timeout', type=float, default=1800.0, help='seconds (default 1800 = 30 min)')
            sub.add_argument('--interval', type=float, default=5.0, help='poll interval in seconds (default 5)')
        if name == 'send':
            sub.add_argument('--replace', action='store_true', help='send a draft that was already sent (needs a new consent)')
        sub.set_defaults(func=_cmd)
