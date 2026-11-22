# Workflow v1 configuration

Shared defaults: workflow/defaults.yaml in the library; the next layer is
workflow/projects/<project.name with separators replaced by ->.yaml.
The project overrides the workflow field in .github/.skillsync.yaml. Maps are merged recursively,
lists are replaced entirely. The source schema.json describes the format without a new runtime dependency.
An empty manifest still uses tests.command as a fallback; the kind build/test must be distinguished.
Commands require a trusted configuration and --run. Do not take commands from issue descriptions or plans.

```yaml
workflow:
  version: 1
  validation:
    checks:
      - id: backend-unit
        argv: [python3, -m, unittest, discover, -s, tests]
        kind: test
        cwd: .
        profiles: [quick, final]
        required: true
        timeout: 300
        resources: [project-test-db]
        requires_executables: [python3]
  testing:
    rules:
      - paths: ['src/**', 'tests/**']
        checks: [backend-unit]
    modules:
      domain:
        paths: ['src/domain/**']
        checks: [backend-unit]
      api:
        paths: ['src/api/**']
        checks: [backend-unit]
        depends_on: [domain]
```

argv is preferred. command allows a shell, only in a curated manifest.
success_pattern is an explicit evidence regex for a runner without a recognized summary;
do not set it to arbitrary text. allow_skips requires a justified scope.
requires_env contains variable names, never their values. A resource shared between
projects must have the same unique identifier. A missing isolation does not go away thanks to a worktree.

`route --topic`, `readiness --risk`, `review --risk` plan the scope. `doctor` is read-only.
Check detailed arguments with skillsync <command> --help.
Windows: the whole skillsync_workflow directory must sit next to the skillsync script; the old single
file keeps the older sync commands, the new workflow requires the package.
