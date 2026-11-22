# Prototype comment format (v2)

Source of the contract for the CLI and the server (`skillsync prototype …`). The description derives from `assets/repo/prototype.js`
(engine v2) and from the `skillsync_workflow/prototype_server.py` module in Skillsync; when they differ, the code wins
and this file must be corrected. The Python fold is a 1:1 port of the JS fold (differential test in the Skillsync repo).
The pure engine functions (no DOM) are in `module.exports`: `foldDocument`, `foldOperations`, `normalizeOperations`,
`sortOperations`, `mergeOperations`, `computeLabels`, `peopleFromOperations`, `canonicalOperation`,
`serializeCommentsDocument`, `normalizeCommentsDocument`, `convertV1Operations`, the iteration functions (`iterationList`,
`iterationForThread`, `restoreAvailability`, `restoreSubmitState`, `classifyRestoreResult`, `restoreEffects`) and others, so tests run them in Node.
Prototypes v1 (`comments.js`) are described at the end in the "Format v1" section.

## The comments.json file

```text
{"format":"skillsync-prototype-comments","version":2,"prototypeId":"<slug>","operations":[
{"id":"op-…","type":"create","threadId":"th-…","at":"2026-11-05T09:12:03.120Z","by":"jan.kowalski@example.com","cid":"c-…","payload":{…}},
{"id":"op-…","type":"vote","threadId":"th-…","at":"…","by":"…","payload":{"messageId":"m-…","value":"up"}}
]}
```

- UTF-8 without BOM (a BOM is tolerated on read), strict JSON without `NaN`/`Infinity`. Line 1 is a compact header, then
  one operation per line (keys `id`, `type`, `threadId`, `at`, `by`, `cid`, `payload`, compact), `,` at the end of every line except the
  last, then `]}` and a newline. An empty log: the header and `]}`. The template `assets/repo/comments.json` has `"prototypeId":"{{SLUG}}"`.
- The log **only grows**: the server and the CLI append under a lock (a process lock and the `comments.json.lock` file, wait limit 5 s), the write is atomic
  (temporary file, `fsync`, `os.replace`). The `comments.json.lock` and `*.tmp` files are in the prototype's `.gitignore`, and `.gitattributes`
  points to the `skillsync-comments` merge driver (registering it in git is optional; without it git shows an ordinary conflict, which a manual `merge-comments` on both versions of the file resolves).
- Operation `id`: `op-` + 26 Crockford base32 characters (10 of time + 16 of counter and randomness), assigned by the server under the lock, increasing. `threadId`: `th-…`, message: `m-…`
  (assigned by the client on `create`/`reply`); the v1 identifiers (`<prefix>-<base36>-<random>`, `[A-Za-z0-9_-]{1,80}`) remain valid.
- `at`: server time in UTC `YYYY-MM-DDTHH:MM:SS.mmmZ`, `max(now, last + 1 ms)` (monotonic even after the clock goes back); compared as a string.
- `by`: the login from the session (an e-mail address), `agent` (CLI), `import` (migration) or `server` (recovery at `serve` start). `agent`, `import` and `server` are reserved logins. The client does not send `by`. `by: agent` marks an AI author (e.g. a `reply` from `skillsync prototype reply`): the UI shows an "AI" badge on the message and on the thread card, and `prototype wait` does not treat such messages as a person's comment. The format does not store the agent's name (old copies of the engine read the log unchanged).
- `cid`: a client identifier for deduplication (`by`, `cid`); required in requests from the browser.
- Limits: message text ≤ 4000 UTF-16 units, request body ≤ 64 KiB, a file ≥ 10 MiB gives a warning in `list`, at ≥ 20 MiB the server refuses to write (507).
- A document with a different `format`, `version` or a non-empty foreign `prototypeId` is rejected by the engine as empty and by the server as a file error.

## Operations

| `type` | `payload` | Who |
|---|---|---|
| `create` | `message {id, text}`, `screen`, `anchor {kind: element\|screen\|lost, protoId?, x, y, iteration}`, `label?`, `hint {text, legacyPath?}?`, `view?` (`"desktop"\|"mobile"`; the engine writes it only for mobile) | every role (`lost` only migration) |
| `reply` | `message {id, text}` | every role; also the agent CLI (`prototype reply`, `by: agent`) |
| `edit` | `messageId`, `text` | the message author |
| `retract` | `messageId` | the message author |
| `vote` | `messageId`, `value: up\|down\|none` | every role |
| `resolve` | `reason: done\|kept\|out_of_scope\|author`, `iteration?` | the thread author, the developer (from the browser only `author`) |
| `reopen` | `{}` | the thread author, the developer |
| `reanchor` | `screen`, `anchor`, `label` | the thread author, the developer |
| `delete` | `{}` | the developer |
| `triage` | `class: clear\|disputed\|out_of_scope`, `note?` | the agent CLI only (`triage`); the browser gets 403 `cli_only` |
| `decision` | `kind: agent\|developer`, `who?` (the developer's login for `developer`), `outcome: apply\|keep\|variant\|defer\|out_of_scope`, `text`, `iteration?` | the agent CLI only (`decide`) |
| `iteration` | `number` (the frozen iteration), `path` (`iterations/N/`), `hash` (SHA-256 of `assets_hash`, `head_hash` and the hashes of the copy's screens), `commit?` (sha of the commit before the iteration) | CLI `iterate` and `restore`; the server only paired with `restore` |
| `restore` | `screen`, `from` (the source iteration), `frozen` (the iteration frozen before the replacement), `reason` (1–500 characters), `who?` (with the CLI), `hashBefore`, `hashAfter` (the screen hash) | the `restore` endpoint (acceptor, developer) or CLI `restore --who` |
| `accept` | `scope` (`screen`, `all`, `project`), `screens {sN: hash}`, `iteration`, `via` (`ui` from the browser, `cli` from the CLI), `who?` (only with `via: cli`) | browser: role `acceptor`/`developer`, `scope: screen` with a single screen; CLI `accept` (password from the TTY); project scope: `POST …/project/accept` |
| `unaccept` | `screens [sN]` (at least one) | browser: `acceptor` only their own (`who` equal to the login), `developer` any; the CLI does not do this |

The schema is strict: unknown fields in `payload` give 400, and `view` accepts only the string `"desktop"` or `"mobile"`; the session (`GET /_api/v1/session`) reports support for the field in `server.features` (a list, contains `"views"`), and an engine without this flag does not create comments in the mobile view; an `element`/`screen` `anchor` requires `x`, `y`, `iteration`; `lost` comes only from a file/migration.
An `element` `anchor` points to the `data-proto-id` of the nearest ancestor of the clicked element; `screen` is a position relative to the screen frame.

## Folding algorithm (fold)

It is implemented by `foldDocument` (JS) and `fold_operations` (Python), identically. The state does not depend on the order in the file, only on `(at, id)`.

1. **Normalization**: discard operations without non-empty string `id`, `at` (and `threadId` for thread types) or with an unknown `type`; for a repeated `id` the first valid one wins.
   `by` missing/invalid = `null`. A non-object `payload` (an array too) = `{}`.
2. **Sorting** by `(at, id)` in UTF-16 units like the `<` operator in JS.
3. **Thread creation**: from the first `create` with a truthy (in the JS sense) `payload.message` and a string `payload.screen`. The thread **number** = the position of such a `create` among all threads
   (including deleted and hidden ones: a gap). `delete` removes the whole thread (a tombstone); `retract` of the opening message hides the thread if at the moment of that operation there is no non-retracted reply from another person.
   The thread **view** (`view`): `"mobile"` only when the `create` the thread comes from has `payload.view === "mobile"`;
   in every other case (field missing, `"desktop"`, another type or value) `"desktop"`; later operations do not change it. Reading the file is tolerant: a `view` with an invalid value in the file gives `desktop`. `merge-comments` validates operations strictly, so an operation being merged with an invalid `view` gives a conflict.
4. **Messages**: `reply` appends a message (a repeated `message.id` is skipped); `edit` and `retract` only by the author, `edit` and `vote` on a retracted message are skipped; `vote`: `up`/`down` sets, `none` removes the vote.
5. **Status**: `open` (start), `awaiting`, `decided`, `closed`, `deferred`. `resolve` always closes (`statusReason` = `reason` or `null`), `reopen` opens; `triage disputed` → `awaiting`;
   `decision` (CLI only): `keep`/`out_of_scope` → `closed`, `apply`/`variant` → `decided`, `defer` → `deferred`. `reanchor` changes `screen`, `anchor`, `label`.
6. **Current iteration** (`currentIteration`): `iteration.number` ≥ 1 → `max + 1`. `restore` changes neither the counter nor the thread
   statuses: it always comes paired with the freezing `iteration` operation (`frozen`) written in the same write, and whether a pin is shown is decided by
   the presence of `protoId` in the current `index.html`.
7. **Open** (for counters): `open`, `awaiting`, `decided`. **A thread without a handle** (`lost`): a `lost` anchor, an `element` anchor with a `protoId` absent from the screen's current HTML, or a thread whose screen (`screen`) is not in the current version; a `screen` anchor with an existing screen always has a handle.
8. **Acceptance**: `accept` sets `acceptance[sN] = {hash, by, who, at, iteration, via, scope, opId}` for every screen in `screens`; `unaccept` removes the entries of the listed screens. Skipped are: `accept` with `screens` that is not an object, `unaccept` with `screens` that is not a list, and screen values that are not strings. `who` = `payload.who` only with `via: "cli"`, otherwise `by`. The fold does not compute currency: `valid` (the hash equal to the screen's current hash) is computed by the server.

**Person labels** (`computeLabels`, `label` in the API): only the logins from `by`; the first and last name from `name` in the users file or from the address (the part before `@`, dots = words, the first code point capitalized);
a unique first name → the first name alone; a first-name collision → `First name X.` (the initial of the last name); a repeated collision → the full first and last name. First names are compared exactly (`Paweł` ≠ `Pawel`).

## Serialization

`serializeCommentsDocument(prototypeId, ops)` (JS) and `serialize_document` (Python) give **identical bytes**. Do not use `json.dumps`: Python serializes through `js_stringify`
(numbers like `Number::toString`, lone surrogates as `\udxxx`, index keys first in ascending order), because `json.dumps` and `JSON.stringify` differ for `2.0`, exponents, numbers > 2^53 and surrogates.

## Browser storage (localStorage v2)

| Key | Content |
|---|---|
| `skillsync-prototype-queue:v2:<slug>:<login>` | the offline queue: an array `[{cid, type, threadId, payload}]`; only 8 queueable types; a message goes here first, then to the server; a message without `view` is desktop |
| `skillsync-prototype-rejected:v2:<slug>:<login>` | rejected by the server (4xx), for preview, copying, download and deletion |
| `skillsync-prototype-seen:v2:<slug>:<login\|anon>` | the greatest `at` after the last read (the "New" filter); data loss = everything read |
| `skillsync-prototype-comments:v1:<slug>` | data from engine v1 (read only); engine v2 offers "Send as <first name>…" (available after the first successful read of operations; the messages go to the v2 queue, and the v1 key is removed without an additional question, because the content is already in the queue) and "Download file" (then "Remove from browser", without a confirmation window); without one of these actions nothing is removed. `localStorage` is per origin (scheme, host, port): v1 messages are visible only under the former address, e.g. `serve --port <former port>` on `127.0.0.1`; keep the downloaded file or send it to the developer |
| `skillsync-prototype-theme` | `light` or `dark` |
| `skillsync-prototype-shortcuts` | `on` or `off` (the keyboard shortcuts switch) |

Every access to `localStorage` is in `try/catch`. Polling `GET /_api/v1/prototypes/<slug>/operations?after=<n>&prefix=<fingerprint>` every 5 s (30 s in the background, ×2 on errors); `reset: true` or a mismatched prefix
fingerprint forces a full reload.

## Iterations in the log and in the engine

- `iterate` copies the current version to `iterations/N/` (without `comments.json`, `iterations/` and lock files), sets in the copy
  `<html data-prototype-frozen data-prototype-iteration="N">` and appends `iteration {number: N, path, hash}`; the current iteration
  grows to N+1. The write order of `restore`: freezing C (`iterations/C/`), a single write of `comments.json` with `iteration {number: C}`
  and `restore {…}`, then replacing the `sN` section in `index.html`. The log is not edited retroactively: undoing a restore is another
  `restore --from C`.
- `GET /_api/v1/prototypes/<slug>/operations` also returns `version` (SHA-256 of the screen hashes), `screens {sN: hash}` and
  `iterations: [{number, path, exists}]` (frozen iterations; `exists` = `iterations/N/index.html` is a regular file). The engine remembers `version`
  from the first response; a different value later = the "Prototype changed, reload" bar and "Restore…" disabled.
- The same response has `acceptance` `{sN: {valid, who, at, iteration, via}}` (only screens with an entry on the current list). The "Accept…" window and the "Acceptance k/n" chip read it; the "all prototypes" scope goes through `GET /_api/v1/project` and `POST /_api/v1/project/accept` `{cid, versions}` (409 `partial` on a version change or a lock, and 507 `file_too_large`, 409 `invalid_log`/`needs_migration` and 500 `write_failed` with the `accepted`/`pending` list; on `partial`, 507 and 500 retry with the same `cid`). Before writing, the server returns 409 `stale_view` with `{stale: [slug]}` when the version of any prototype is different or missing; retrying with the same `cid` does not help, the page must be reloaded and new `versions` sent.
- **A frozen page** (`iterations/N/index.html`, a copy with its own `prototype.js`; iterations frozen before an engine update,
  e.g. before `migrate`, run on the old engine without the elements below): an "Iteration N · frozen" bar, a read-only banner with "Back to current";
  no new threads, no moving anchors, no acceptances; replies and votes in existing threads work in full mode (the same
  log, API with the `slug` from `data-prototype-id`); in read-only mode it reads `../../comments.json`. `#thread=<id>` opens the thread
  and highlights the pin.
- **"Show in iteration N"** on a thread without a handle: N = the newest frozen iteration with an existing directory whose `index.html`
  has the thread's `data-proto-id` in the thread's screen (a `screen` anchor: the screen itself); the engine fetches `iterations/K/index.html` starting from the newest
  and keeps the result in memory for the lifetime of the page; no such iteration = no button. In read-only mode the list of iterations comes
  from the `iteration` operations, and existence is checked by a `GET` request for `iterations/N/index.html` (status 200).
- **"Compare with iteration N"** (the screen header, also read-only and without logging in): a window with frames
  `iterations/N/index.html?embed=compare#sN` and `index.html?embed=compare#sN`. In `?embed=compare` mode the engine shows only the screen
  from the hash (no bar, panel, commenting or server polling); Esc in a frame closes the window.
- **"Restore…"** (full mode, roles `acceptor`/`developer`, `session.server.sourceWrites`): the effects from
  `GET …/restore-preview?screen=sN&from=N` (`threadsBack`, `threadsLost`, `acceptanceLost`, `unchanged`), then
  `POST …/restore {cid, screen, from, reason, version}`; 409 `stale_view` = the "Prototype changed" bar, success = a page reload.
  `unchanged: true` in the preview (the screen in iteration N has the same hash as the current one, D27) = the message "The screen in iteration N is the same
  as the current one." and an inactive save button (the reason field stays active; changing the iteration fetches the preview anew);
  200 `{unchanged: true, version}` on POST (a race) = the same message, without a write, reload or error.
  A restore does not go to the offline queue. With `serve --no-source-writes` the `restore` endpoints return 403 `source_writes_disabled`.

## Engine modes

`file://` (no server: a mock-up, a message and the `serve` command), a static server without an API (`GET /_api/v1/session` = 404/non-JSON: read-only `comments.json`), full (server API: login, write, queue).
Without a session the engine shows "Log in to comment"; `mustChange` opens a window for setting one's own password.
The acceptance chip and "Accept…" do not appear in `file://`, on a frozen iteration page or in `?embed=compare`. On an ordinary file server the chip works read-only with the description "currency is checked by the server".

## Fixtures and checks

- `scripts/fixtures/v1/`: v1 `comments.js` exports (`export-comments{,-2}.js`) with the expected fold `*.expected.json`. `export-comments-2.js` deliberately contains a raw, unsorted array with a duplicate `id` (the first wins).
- `scripts/fixtures/v2/`: `comments-v2.json` with `comments-v2.expected.json` (votes, edits, `retract`, `delete`, decisions, `triage`, `reanchor`, `at` ties, duplicate `id`s, three "Pawel" people).
  Change the input and the `*.expected.json` file together; consistency is checked by `check_tokens.py` and the differential test in Skillsync.
- `scripts/check_tokens.py` checks the v2 templates (the `comments.json` layout, `.gitignore`, `.gitattributes`, no `comments.js`, no reference in `index.html`), the v1 fixtures (fold v1) and an independent v2 fold with labels in Python
  for `fixtures/v2`; a mismatch = code 1. `--fixtures` points to the directory with `v1/` and `v2/`, `--comments` to the `comments.json` template.

## Format v1 (`comments.js`, for migration only)

```text
window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"version":1,"prototypeId":"<slug>","operations":[…]};
```

Rule C1-U1a: the fragment from `window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = ` up to the **last** `;` is strict JSON. The v1 operations: `create`, `reply`, `reanchor`, `resolve`, `reopen`, `delete` with `message {id, author, text, at}`
and an anchor as an element path (`tag:n`, 1-based index, without `:n` when unique). `skillsync prototype migrate` preserves `id`/`threadId`/`at`, sets `by: import` and `importedAuthor`, and converts the path to `data-proto-id`
only when it is unambiguous (otherwise a `lost` anchor with `hint.legacyPath`). `list` reads `comments.js` until migration (`commentsFormat: 1`).
