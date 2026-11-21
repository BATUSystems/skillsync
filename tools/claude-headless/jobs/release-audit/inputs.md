# Inputs of the job `release-audit`

Public repository of the core only, built from a git tag (never the working tree, never the operations repository, the organization or personal layers,
`.skillsync/`, `docs/plans`, `docs/specs`). The host builds the copy with an allowlist of paths and adds `_audit/diff.patch` and `_audit/diffstat.txt`.
Parameters: `--tag-from` and `--tag-to` (two existing tags). The result is advisory.
