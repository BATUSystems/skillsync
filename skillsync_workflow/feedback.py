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
_FB_TAG = re.compile(r'fb-[0-9a-f]{12}')
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


def _safe_dir(store, create=False):
    store = Path(store)
    if store.is_symlink() or store.parent.is_symlink():
        raise FeedbackError('the feedback store must not be a symlink')
    if create:
        store.mkdir(parents=True, exist_ok=True)
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

from collections import namedtuple

from . import delivery

PROVIDER = 'azure-feedback'
Context = namedtuple('Context', 'root manifest library')   # what `delivery.tracker` needs (`common.load_layer_project`)
_TAG = re.compile(r'(?:fbk|fb)-[0-9a-f]{12}')
OPEN_CATEGORIES = ("'Proposed'", "'InProgress'", "'Resolved'")


def build_wiql(project, tag, *, open_only):
    """The WIQL for the items that carry `tag` (a `fb-`/`fbk-` tag): the values are validated here, so no quote or operator of a value reaches the query.
    `open_only`: only items not completed or removed (the deduplication); otherwise every state (the idempotence of a repeated `send`)."""
    if not isinstance(project, str) or not layers._FEEDBACK_SEGMENT.fullmatch(project) or not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise FeedbackError('the project or the tag of the query is not valid')
    state = f" AND [System.StateCategory] IN ({', '.join(OPEN_CATEGORIES)})" if open_only else ''
    return (f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = '{project}' AND [System.Tags] CONTAINS '{tag}'{state} "
            'ORDER BY [System.Id] DESC')


def _call(context, operation, params, *, write=False):
    try:
        result = delivery.tracker(context.root, context.manifest, context.library, operation, params, run=True, allow_write=write,
                                  provider=PROVIDER, internal=True)
    except (ValueError, OSError) as exc:
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
    raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
    path = directory / f'body-{os.getpid()}-{time.monotonic_ns()}.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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


def _finish(store, draft_id, intent, item_id, url):
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    record = {'id': item_id, 'url': url, 'mode': intent['mode'], 'item': intent.get('item'), 'sha': intent['sha'], 'tag': intent['tag'],
              'approval': intent.get('approval'), 'at': now}
    write_json_atomic(_sent_path(store, draft_id), record)
    with contextlib.suppress(OSError):
        os.unlink(_sending_path(store, draft_id))
    return {'status': 'PASS', 'mode': intent['mode'], 'id': item_id, 'url': url, 'tag': intent['tag']}


def send_payload(context, store, draft_id, rendered, *, approval_id=None):
    """Create the work item (or comment on the open one) for `rendered`, once and verifiably. Returns `{status, ...}`; raises FeedbackError before any
    mutation when it cannot go on (fail closed).

    The caller (`skillsync feedback send`) has already checked the human approval of THIS content. Here: an atomic intent file `F<n>.sending.json`
    (`O_EXCL`: a second send of the same draft stops) with the sha; the request file is verified before the call; the write goes through the guarded
    tracker with a readback; the result is `F<n>.sent.json`. After an interrupted send the intent file is found again: the NEW run continues with the
    sha of the intent (never recomputes the deduplication, which would see its own element) and looks for its own tag or marker before it writes again."""
    payload = final_payload(rendered)
    sending = _sending_path(store, draft_id)
    resumed = False
    intent = {'sha': payload['sha'], 'tag': payload['tag'], 'mode': rendered['mode'], 'item': rendered['item'], 'approval': approval_id,
              'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        fd = os.open(sending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        resumed = True
        old = read_json_file(sending)
        if not isinstance(old, dict) or old.get('sha') != payload['sha']:
            raise FeedbackError('an interrupted send of this draft exists for different content: check the tracker for the tag in '
                                f'{draft_id}.sending.json, then remove that file') from None
        intent = old
    else:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(intent, handle, sort_keys=True)
    if resumed:
        done = _already_sent(context, rendered, payload)
        if done:
            return _finish(store, draft_id, intent, done['id'], done.get('url'))
        if rendered['mode'] == 'comment':
            raise FeedbackError('the interrupted comment cannot be confirmed (the last comment does not carry the marker): check the work item, '
                                f'then remove {draft_id}.sending.json')
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
        result = _call(context, operation, params, write=True)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
    if result.get('status') != 'PASS':
        return {'status': result.get('status', 'BLOCKED'), 'reason': str(result.get('reason', ''))[:200], 'mutation_completed': bool(result.get('mutation_completed')),
                'tag': payload['tag']}
    data = result['data']
    return _finish(store, draft_id, intent, data.get('id'), data.get('url'))


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
