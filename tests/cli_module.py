"""Shared access to the skillsync CLI under test.

The CLI body lives in the package module `skillsync_workflow.cli`; `skillsync` in
the repo root is a thin wrapper. Tests import the module directly and start
processes through the wrapper (`[sys.executable, SKILLSYNC_PATH]`).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import skillsync_workflow.cli as skillsync  # noqa: E402

SKILLSYNC_PATH = ROOT / "skillsync"
CLI_MODULE_PATH = ROOT / "skillsync_workflow" / "cli.py"

def fresh_cli(name="skillsync_workflow._cli_fresh"):
    """A new copy of `skillsync_workflow.cli`, executed now.

    Module-level state (`CONFIG_PATH` follows `SKILLSYNC_CONFIG` and `Path.home()` at import time) is then
    computed from the environment of the calling test, as when the script was loaded per test.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, CLI_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


__all__ = ["skillsync", "SKILLSYNC_PATH", "CLI_MODULE_PATH", "ROOT", "fresh_cli"]
