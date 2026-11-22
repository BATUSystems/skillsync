# Plan and checkpoints

A plan has a goal, measurable criteria, scope/non-goals, phases, contracts, tests, resources, risks,
documentation and knowledge, and open decisions. A small fix may take five sentences; a multi-layer change requires phases.
A command to carry out a specific list presented earlier approves that scope.
A plan with a `Spec:` line does not repeat the scope, contracts, migrations or risks: they are in the spec ([spec](../../feature/references/spec.md)).

In the TESTS section specify each layer, the command/cwd, the environment, the resource grant and the evidence.
Contracts: name the file and the real consumers. Migration: existing data and rollback,
or a justified “not applicable”. Risks: failure, effect, severity, control, owner and residual risk (why acceptable).
Documentation and knowledge: documents to update, expected domain knowledge, the place
of the write and the owner ([knowledge.md](knowledge.md)); “not applicable” only with a justification.
Domain terminology: after reading the `load: bootstrap` records from `skillsync route <project>`
name the confirmed terms to add/update and the candidates requiring clarification
(hypothesis of the meaning, source/question). When there are no new terms, record that with a short justification.
Only confirmed definitions go into the glossary, according to `knowledge.md`.

A step has a stable id, an Exec column (`inline|dispatch|group` + model class) and an entry in the `Progress` section with a `PR:` line
([convention](../../feature/references/plan-details.md)). The orchestrator updates progress only after checking the evidence;
the checkpoint ties it to the fingerprint of the current staged/unstaged/untracked changes.
Before resuming, check the plan, the instruction sources, the baseline, old failures and open reviews.
Do not repeat successful checks on an unchanged state, do not keep a PASS after a code change.

Choice of models and roles: weigh the cost/capability trade-off. A cheaper role for locating and running checks, a stronger one for implementation and review,
escalation only of a hard step. The session model is chosen by the user; a text instruction does not switch it.
