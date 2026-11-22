# Codex adapter (VS Code extension and CLI)

Invocation: `$feature` (pick the skill through `$` or `/skills`). Do not assume the `/feature` command
exists in Codex. `skillsync` exposes the same skill in `.agents/skills/feature`.
The name `agentflow` (used temporarily) is retired: `$agentflow` stops working after `skillsync link`,
and `agentflow` in the manifest (`skills:`) works only as an alias of `feature`. In worker mode
(the `dispatch:` header), write messages to the dispatcher as a "card → dispatcher paste block"
(format in multi-session.md, "Message grammar"), unless the host has a tool for messages
between sessions (then use it, addressing the session `<id>` from the header).

Delegate through the native tools available in this session, choosing the configured agent type:

| Role in the plan | Codex type | Default model / effort |
|---|---|---|
| explore | explorer | gpt-6-luna / low |
| implementer | implementer | gpt-6.1-sol / medium |
| test-writer | test_writer | gpt-6.1-sol / medium |
| test-runner | test_runner | gpt-6-luna / low |
| reviewer | reviewer | gpt-6.1-sol / high |
| verifier | verifier | gpt-6-luna / low |
| scribe | scribe | gpt-6-luna / low |
| mechanic | mechanic | gpt-6-luna / low |

The source of the settings is the library `codex.yaml`, and of the role instructions the shared `agents/*.md`.
`skillsync` generates the project `.codex/agents/*.toml`. If model access is missing, report
the specific error; do not loop on retries. The right model can be set in the manifest through
`codex.agent_overrides.<source role>.model` and `model_reasoning_effort`.

Do not start an extra `codex exec` instead of a native subagent. If the delegation tool
does not support named types, pass the instructions and settings of the matching
role from the TOML explicitly (only parameters the tool supports). Do not then claim that all
TOML settings, sandbox in particular, were applied. If delegation is disabled,
report the lack of independent review as a limitation requiring a user decision.

Default limit: 3 subagents besides the orchestrator. Resume an agent for fixes within the same
step, but every round of the [review loop](review-loop.md) is a new `reviewer` (the reviewer model changes only through
`codex.agent_overrides`, so usually "the same model", and record that in the round report, in every profile: `spec`, `plan`, `kod`, `docs`); close finished threads when they occupy the limit. Do not pass the full history when
a work order with the plan is enough. When a model override requires an empty/short context,
apply that mode and pass the required instructions explicitly.

`explorer`, `reviewer` and `verifier` default to `sandbox_mode = "read-only"`. The other roles use
`workspace-write`, because tests write artifacts. Do not change global permissions or the
approval mode. The session's current permission settings may override the role defaults;
the write ban in a role's instructions still applies. A configured sandbox does not isolate
a shared database or ports.

The user approves the plan in the main conversation. Do not require Claude tools or switching the
mode to Plan. Explicit consent to a specific plan remains valid also after a change of turn/context.
