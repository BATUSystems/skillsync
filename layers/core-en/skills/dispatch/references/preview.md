# Live preview (preview): assignment template for a card

Read when you assign a card (or start yourself) a live preview of the status page (`skillsync dashboard serve`) or of the prototype server, so that the user can view it before
the merge. A preview in a separate server **writes a state file** and holds a port; without isolation it spoils other people's test runs.

## Why isolation is mandatory

`dashboard serve` writes the state file in the user's data directory (Linux `$XDG_DATA_HOME/skillsync/dashboard`, macOS `~/Library/Application Support/skillsync/dashboard`, Windows
`%LOCALAPPDATA%\skillsync\dashboard`). The core tests have a guard "test touched real Skillsync state dirs": a preview started on the real state fails a full run only after its
end (tens of station minutes, then a repeat). That is why a preview always gets its own, isolated state in the card's worktree directory, and before a wide run it is checked that no live preview
writes to the real state.

## A card's worktree status page: a tab, not a separate port

When a card works in a worktree of the same git project as a running project status-page server, it does not get a port from the pool: `skillsync dashboard serve` in its worktree, without server-settings flags,
adds a tab for that worktree to the project page and prints the address with the token of its own tab (the last line of stdout). It starts no process (no PID to stop) and opens no port.
The card gives this address in its report; `--all-tabs` (the addresses of all tabs with tokens) is for a human and cards do not use it. Without a running server the same command starts the project server
(then it is its process, as in the template below). The tab shows the page code of the server's layers, not of the card's worktree.
Adding a tab writes a pin file in the real data directory (`<data home>/dashboard/<serverId>-pins`), so it works only without an isolated `XDG_DATA_HOME` (data directory); a preview with isolation is a standalone server.

A preview with another layer (page code from `<worktree>/library`), without consents or with any server-settings flag is a separate (standalone) server on the card's pool port; adding a tab does not replace it.
The template below is for exactly such a preview and for the prototype server.

## Assignment template (paste into ASSIGN and fill in `<…>`)

```
PREVIEW <what>: start with an isolated state and a port from this card's pool; nothing leaves 127.0.0.1 until the dispatcher agrees otherwise.
Linux:   S=<worktree>/.preview-state; mkdir -p "$S"
         XDG_DATA_HOME="$S/data" XDG_CACHE_HOME="$S/cache" SKILLSYNC_CONFIG="$S/config.yaml" \
           skillsync dashboard serve <project> --port <port-from-pool> --no-approvals --layer core=<worktree>/library & echo $! > "$S/serve.pid"
macOS:   the same, plus HOME="$S/home" (the state lives in ~/Library/Application Support, not in XDG)
Windows: $env:LOCALAPPDATA="$S\data"; $env:USERPROFILE="$S\home"; $env:SKILLSYNC_CONFIG="$S\config.yaml"; Start-Process … (save the PID to $S\serve.pid)
Variables as `home_env` in the tests: XDG_DATA_HOME, XDG_CACHE_HOME, LOCALAPPDATA, HOME, USERPROFILE (all systems at once do no harm).
Check: (1) a response at http://127.0.0.1:<port>/healthz, (2) that `$S/data/skillsync/dashboard/` has a state file, and the real state directory did not get a new file
         (Linux: find ~/.local/share/skillsync/dashboard -newer "$S/serve.pid"; macOS and Windows: the matching paths from the paragraph above).
Stopping: kill "$(cat "$S/serve.pid")" (by the PID saved at start; not `pkill` by name: you would kill someone else's server), then check that the port is free.
Report: address, port, PID, state directory, the result of checks (1) and (2).
```

## Rules

- **Port from the card's pool** (a separate server: a preview with another layer, the prototype server). The dispatcher assigns the cards port pools (e.g. a range of a dozen consecutive numbers per card); a preview does not take a port from outside the pool and does not look for "the first free one".
- **By default only this machine** (`127.0.0.1`). The status page shows plans, specs and results (secrets masked heuristically, traffic unencrypted). Exposing it on the local network (`--read-bind 0.0.0.0` or
  reading from a phone) only at the user's explicit request and only for reading; consents (`approve`) stay on the loopback. Listening details: `docs/cli.md` of the skillsync core (section `dashboard serve`).
- **One preview per task and per port.** After viewing, stop it (by PID) and remove the state directory; do not leave a server after the package is finished.
- **Before a wide test run** the dispatcher (or the card that orders it) checks that no preview runs on the real state: the real state directory has no state file of a live process
  (`kill -0 <pid>` from the file). A live preview on the real state: stop it or restart it with isolation, only then the run.
- **A preview with pre-merge code** shows the state of the branch, not of the release: state in the report which SHA it runs from.
- The prototype server (`prototype serve`) has its own state directory and accounts file: the same isolated directory, port from the pool and stopping by PID; a demo without accounts is `prototype serve --demo`
  (read-only, by default on all interfaces: for a local-only preview add `--host 127.0.0.1`).
