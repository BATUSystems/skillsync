# Workflow v2 extensions

Read when the task is shared, or touches integrations/UI/CI or the tracker.

The skills `task-claim`, `project-guard`, `ci-stabilize`, `tracker-workflow`, `workflow-upgrade` and `skill-profiles`
belong to the `workflow-extended` profile; use them only if they are installed.
For a shared task, use `task-claim` before writing, and renew/release during and after.
Before integration, learn the `test-env` recipe; test the UI through `observed-e2e` on actual
observation. In the plan, name the configured `project-guard` and the resources. `ci-stabilize` runs
bounded attempts after a failure. `tracker-workflow` handles only authorized external
operations. Pick `workflow-upgrade` and `skill-profiles` when changing the workflow.
Do not run all the tools on a small task; report missing configuration explicitly.
Contracts: [workflow v2](../../workflow/references/eight.md).
