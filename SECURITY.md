# Security policy

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub private vulnerability reporting
("Security" → "Report a vulnerability") in this repository. We aim to acknowledge reports within a few working days.

## Supported versions

Only the latest MINOR release of the current MAJOR version receives security fixes.

## Scope

- the `skillsync` generator (the `skillsync_workflow` package and the repository scripts `skillsync`, `skillsync-ctl`,
  `skillsync-windows.ps1`);
- isolation between layers and `leakcheck` (see [docs/security.md](docs/security.md)).

Content of third-party organization layers is out of scope; report it to the layer owner.
