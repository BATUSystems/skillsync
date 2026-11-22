# Claude Code adapter

Invocation: `/feature` (the skill from `.claude/skills/feature`). `skillsync` installs the same skill
as for Codex (`.agents/skills/feature`) and the same set of roles.
The name `agentflow` (used transitionally) is retired: `/agentflow` stops working after `skillsync link`,
and `agentflow` in the manifest (`skills:`) now works only as an alias of `feature`, not as a command.
In working mode (header `dispatch: <run-id> from Dyspozytor (session <id>)`, see
multi-session.md) send the report and requests to the dispatcher with the `SendMessage` tool to the `<id>`
from the header; without an `<id>` print the message in the paste format from multi-session.md.

Delegate with the native `Agent` tool (older versions: `Task`), explicitly giving `subagent_type`:

| Role in the plan | Claude type | Default model | Effort | CLAUDE.md | maxTurns |
|---|---|---|---|---|---|
| explore | explore | haiku | low | omitted (`omitClaudeMd`) | 40 |
| implementer | implementer | sonnet | medium | yes | 170 |
| test-writer | test-writer | sonnet | medium | yes | 165 |
| test-runner | test-runner | haiku | low | omitted | 125 |
| reviewer | reviewer | sonnet | high | yes | 85 |
| verifier | verifier | sonnet | low | omitted | 45 |
| scribe | scribe | haiku | low | yes | 115 |
| mechanic | mechanic | haiku | low | omitted | 40 |

`maxTurns` (the role's turn limit) protects against looping. A turn is one model response together with its parallel
tool calls. After the limit the subagent returns a result marked as partial ("stopped at its N-turn limit …
PARTIAL output", Claude Code ≥ 2.1.246), which you resume via `SendMessage`; this is not an error. The values are the p95 of the number of
turns from subagent transcripts (n = 1836, private account, 2026-11-21) × 1.5, rounded up to 5 (cap 200);
for `mechanic` the sample was too small (n = 1), so the starting value 40 stays. Resumes accumulate in one
transcript, so the limit may be looser than needed; whether the turn counter starts from zero after a resume via
`SendMessage` was not checked (unverified). A partial result of the `reviewer` or `verifier` role is not a verdict:
resume the subagent or repeat the step, do not accept a partial report as PASS. Correcting a limit is a change in this table and in the role's frontmatter
(their consistency is guarded by `tests/test_skillsync_agent_roles.py`).

The boundary "subagents do not commit" (AGENTS.md) exists only in the role text. A trial from 2026-11-21 (Claude Code 2.1.318) showed
that a Bash pattern in a subagent's `disallowedTools` (both forms: `Bash(git commit *)` and `Bash(git commit:*)`) removes the whole
Bash tool, not just the named command, so no role has `disallowedTools`. The subagent commit boundary
is provided by the `guard-commands.py` hook (PreToolUse `Bash`) with `hooks.subagent_commit` (default `deny` in projects with `feature` or `dispatch`):
it blocks `git commit|push|tag|merge|rebase|reset|stash|checkout --|restore|clean|add -A|add .|cherry-pick|revert` and `gh pr create|merge|edit|close|ready`,
when the call comes from a subagent (a non-empty `agent_id` in the payload). The main session is not covered, and `SKILLSYNC_GUARD_EXCEPT` does not bypass this boundary.
It is a guardrail against a mistake, not a security boundary: scripts, pipes to a shell, aliases and variables bypass it (`docs/security.md`, section "Session and subagent hooks").
A subagent that received a denial reports the changes to the orchestrator (the main session makes the commit on its side).
The session hooks (`session-context`: context after start, compaction and `clear`; `guard-generated`: a warning when editing generated files; `check-subagent-report`: a warning when a writing subagent returned no report) are described in `docs/architecture.md`, section "Claude Code hooks".

F0-disallowedTools: nieskuteczne

The source of the role instructions is the shared `agents/*.md` from the library; `skillsync` installs them in
`.claude/agents/`. The set of roles is the same as in Codex: a project with `feature` without `agents:`
gets all roles from `codex.yaml`; `agents:` in the manifest narrows the list for both hosts.
The role's model comes from the frontmatter; escalate a hard step with the `model` parameter (e.g. `opus`)
in a single `Agent` call, not the whole crew. Likewise `effort` (`low|medium|high|xhigh|max`):
the call parameter overrides the frontmatter only for that call; escalating a hard step
is `model` and/or `effort` in a single call, not a change to the role definition.
`omitClaudeMd: true` (Claude Code ≥ 2.1.271; older versions silently ignore the key) means that
the role does not see CLAUDE.md — the assignment must contain the full set of commands, paths and rules. The main session's model is chosen by the user.
When a role is missing (e.g. a session from before `skillsync link`) report it and ask for a session restart
or `/agents`; do not replace the role with another client launched through the shell.

Do not run `claude -p` or another client instead of a native subagent. If the delegation
tool is unavailable, report the lack of independent review as a limitation requiring
a user decision.

Limit: at most 3 parallel subagents besides the orchestrator; tests on shared resources
sequentially. For fixes of the same step resume the same agent (`SendMessage`
to its id), and launch independent review in a fresh context; every round of the [review loop](review-loop.md)
is a new `reviewer`. The reviewer model in the loop (every profile: `spec`, `plan`, `kod`, `docs`): a session on a model other than `sonnet` → the default, a session on `sonnet` → `model: opus`; the `kod` profile with plan risk high may always get `model: opus`. Do not pass the full
history; an assignment with the plan, scope and paths suffices.

`explore`, `reviewer` and `verifier` have only Read/Grep/Glob — the orchestrator prepares the diff and the list
of new files for review. `implementer` and `test-writer` have write and Bash; `scribe` write without Bash (documentation only),
`mechanic` edit and Bash (mapped replacements, grep); `test-runner` has
Bash for tests (test side effects, not read-only mode). Session permissions in
`.claude/settings.json` may require consent for test commands — do not change them yourself;
the write ban from the role instructions applies regardless of permissions.

The plan is approved by the user in the main conversation. Do not make the workflow depend on `AskUserQuestion`
or `EnterPlanMode`. Explicit consent to a specific plan also holds after a context change.
