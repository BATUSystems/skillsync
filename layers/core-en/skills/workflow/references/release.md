# Documentation, changelog and release version

Applies to `feature`, `bugfix` (and every skill that finishes a code change). Complements
[knowledge.md](knowledge.md); does not replace the delivery rules (the `delivery.mode` mode, the words `CP`, `CPD`, `READY`; see AGENTS). “CP” pushes the working branch,
not the base; handing over to the base requires `CP <branch>` or a `Handoff:` line in the plan.

1. **Identify** (in the plan/brief, the “Documentation and knowledge” + “Version and changelog” sections): the project's changelog
   (`CHANGELOG*`, `docs/**/changelog*`, `RELEASE*`, entries in `docs/`), the sources of the version number
   (`package.json`, `*.csproj`/`Directory.Build.props`, `pyproject.toml`, `VERSION`, application
   manifests, UI configuration files) and the versioning scheme from the project instruction (e.g. SemVer,
   `X.Y.Z` with a hotfix). Assume nothing. A missing changelog/version = explicit information and a question
   whether to create it; do not create it silently.
2. **Documentation and changelog as part of the change** — always, in the same scope as the code:
   update the affected documents and add a changelog entry in the existing format and language
   (category Added/Changed/Fixed/Security or the project's equivalent; ticket/PR number, if known;
   a description for the reader of the release, not for the author of the code). The entry goes into the “Unreleased” section
   until the version is confirmed. Without an entry (or a justified “not applicable”) the change is not `done`;
   the reviewer checks this like code.
3. **Version — always propose and ask.** Present: the current version (from the source and file:line),
   the proposed new one with a justification (bug fix → patch; new backward-compatible feature → minor;
   a change breaking a contract/data → major; exceptions according to the project's scheme), the list of files that
   will change the number, and the option “no version change”. **Ask the user which version to give this release**
   (Claude: `AskUserQuestion` or a plain question; Codex: a plain question) and wait for the answer.
   Ask at the time of the plan, if possible (so the version is known before implementation), and at the latest
   before `done`. Do not change the version number without an answer; after the answer set it in all
   sources consistently, replace “Unreleased” with `## <version> — <date>` and check consistency (grep the old number).
   One question per release: further changes of the same release keep the version already decided.
4. **Boundaries:** tag, release, package publication and deploy — only on a separate command; commit and push according to the delivery mode.
   In multi-session work (`dispatch`) cards do not change the version number or the changelog heading;
   they add entries under “Unreleased”, and the dispatcher asks about the version once for the whole run.
5. **The final report** lists: updated documents, the changelog entry, the decided version (or “no change”,
   or “awaiting decision” → then the task is `blocked` at this point, not `done`).
