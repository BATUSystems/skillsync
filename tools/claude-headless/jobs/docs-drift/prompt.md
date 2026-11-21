You check one thing: whether the command-line documentation of the Skillsync generator matches the code. You may only read files (Read, Grep, Glob); you cannot run commands. The current directory is a copy of the public repository at `{REF}`.

Compare `docs/cli.md` (and the tables in `docs/workflow.md` that list the same commands) with the command definitions in the source: the argparse definitions in `skillsync_workflow/cli.py`, `skillsync_workflow/status.py` and the other modules that call `add_parser` or `add_argument`. Look for:
1. a command or a flag that exists in the code and is missing in the documentation, or the other way round;
2. a documented default, choice, required/optional status or exit code that differs from the code;
3. a documented command whose description contradicts the code.

Rules: report only what you verified by reading both sides; quote the document line and the code location in `evidence`. Do not report wording or style, do not report undocumented internal helpers, do not guess. If everything matches, return an empty list. Give each finding a short unique id (`D1`, `D2`, ...). Severity: `error` for a statement that misleads users (wrong flag, wrong default, wrong exit code), `warning` for a missing flag or command, `info` for a minor note.
