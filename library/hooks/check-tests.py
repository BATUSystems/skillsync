#!/usr/bin/env python3
"""Stop hook: bramka testow. Uruchamiane przez Claude Code przed zakonczeniem tury."""
import json
import subprocess
import sys


def has_pending_changes(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "-C", cwd, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if payload.get("stop_hook_active"):
        return 0

    if len(sys.argv) < 2 or not sys.argv[1].strip():
        return 0

    test_command = sys.argv[1]
    cwd = payload.get("cwd") or "."

    if not has_pending_changes(cwd):
        return 0

    result = subprocess.run(
        test_command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return 0

    tail_lines = 40
    output = (result.stdout + result.stderr).splitlines()
    tail = "\n".join(output[-tail_lines:])
    sys.stderr.write(
        f"Testy nie przechodza (`{test_command}`, exit {result.returncode}). "
        f"Ostatnie {min(tail_lines, len(output))} linii:\n{tail}\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
