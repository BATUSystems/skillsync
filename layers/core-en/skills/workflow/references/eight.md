# Workflow v2: eight extensions

Shared source: workflow/defaults.yaml → workflow/projects/<project-name>.yaml →
the manifest's workflow. CLI: skillsync <command> <project> [--layer core=<path>] [--json]
(bridge; after the content migration `--layer core=<path>` comes from `layer.yaml`).
PASS/FAIL/BLOCKED/NOT_RUN refer to the operation performed; a plan and a missing configuration
are not evidence that the application was tested. State, logs and backups stay in .skillsync/.

## Environment

```yaml
workflow:
  environment:
    start: [python3, server.py]
    cwd: .
    inputs: [server.py, requirements.txt]
    resources: [project-test-db, project-port-8000]
    ttl: 3600
    startup_timeout: 30
    health: {url: 'http://127.0.0.1:8000/health'}
    prepare_checks: []
```

Start/stop: env --action up/down --run. A repeated start reuses only a fresh,
healthy process of its own. The descriptor checks PID/UID/starttime/session; it is not evidence of
the database state. The port/database must be independent of other sessions. Launchers --generate --run
use the same CLI. The Linux lifecycle is available; the Windows backend is BLOCKED.

## Observation → E2E

```json
{"id":"checkout", "URL":"http://127.0.0.1:8000/checkout",
 "observed_at":"2026-10-01T12:00:00+00:00",
 "source_fingerprint":{"head":"actual HEAD", "digest":"actual source digest"},
 "steps":[{"action":"click","locator":"observed test id","expected":"observed result"}],
 "evidence":[".skillsync/e2e/artifacts/checkout.png"]}
```

Use the current fingerprint from the checkpoint/tool report (do not copy the example).
Record requires --observation .skillsync/e2e/input.json --run; it stores observations and
scenarios as data. Match a runner to the scenario, configure workflow.e2e.checks
like validation.checks, argv and kind: e2e, required: true. Run requires a current observation,
unchanged evidence, a scenario and tests that were actually executed. A data scenario
does not generate a test in the repo's language automatically. Disclose the lack of a real browser.

## CI and claims

CI: project report {source: fingerprint, checks: [{name,status,log: path}]}.
A diagnosis from a log is a candidate, cause_verified: false. delivery.ci configuration:
max_iterations (0..3), rerun_check_ids, fix_check_ids — only IDs of validation checks with argv.
Local PASS and ci_status: NOT_RUN do not mean the remote pipeline is green.
Claims: --task-id X --run-id R --action acquire/renew/release/transfer --run; TTL 1..86400,
transfer --to-run-id S, expiry requires --takeover. The write is atomic, a foreign active lease
blocks. Do not take over a task only because its time has expired.

## Migration and profiles

upgrade: revision: v2; artifacts: [{source: workflow/assets/example.yaml,
target: .skillsync/providers/example.yaml}]. The source is relative to the library, the target relative to
the project under .skillsync/providers/, .skillsync/scripts/, .scripts/skillsync/ or docs/workflow/.
The plan shows base/local/incoming and create/update/unchanged/preserve-local/conflict.
Apply with --run writes a backup and pending before the changes; rollback protects later edits.
The total transaction budget is 512 KiB, no symlinks. link refreshes generated artifacts.
Profile selected is additive; core/testing are shared, selected: [] is an explicit opt-out.
profiles shows the catalog of descriptions and the budget, doctor checks the same expansion as the generator.

## Trackers

The library's workflow/trackers/{github,jira,azure}.yaml declare read and update-title.
Common result: provider/id/title/state/url. github params: repo (owner/repo), id; jira: id
(issue key); azure: organization, project, id. update-title adds title. Parameters are
strict and contain no auth. GH_TOKEN/GITHUB_TOKEN; JIRA_URL + JIRA_TOKEN or
JIRA_USER/JIRA_API_TOKEN; AZURE_DEVOPS_EXT_PAT. HTTPS, no redirects and no secrets in argv.
Writes require explicit authorization of the effect and --run --allow-write. A read checks
the requested value. Adapter readiness does not confirm the account's authorization or a production test.
Project override: .skillsync/providers/<id>.yaml, version: 1, capabilities, operations.
Operation: effect read/write, required_params, argv (no shell) or builtin, result
(type object/list, required fields), timeout <=300. Write: verify {operation: read, match: fields}.
argv substitution supports only {param}; write literal curly braces as {{ }}.
REST contracts: [GitHub](https://docs.github.com/en/rest/issues/issues),
[Jira](https://developer.atlassian.com/server/jira/platform/updating-an-issue-via-the-jira-rest-apis-6848604/),
[Azure](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/update?view=azure-devops-rest-7.1).

## Convention guards

```yaml
workflow:
  guardrails:
    rules:
    - id: no-debugger
      paths: ['src/**/*.ts']
      forbidden: '\bdebugger\s*;'
      severity: error
    checks: []
```

required/forbidden is a string or a list of regexes, multiline. The result contains file/line/rule,
without the matched content. Trusted checks have argv and kind: lint. guard --files ... --run;
validate quick/final attaches project-guard when rules/checks are configured. Native
lint is the right tool for AST/import resolution; a regex does not replace a parser.
