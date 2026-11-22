#!/usr/bin/env python3
"""Rewrites the built-in Polish catalog of the dashboard renderer from `skillsync_workflow/data/i18n/pl.json`.

The renderer `library/skills/dashboard/assets/dashboard-render.js` carries the `ui.*` keys of `pl.json` as `DEFAULT_TEXTS` between the markers
`// <i18n-defaults>` and `// </i18n-defaults>` (the text of a status without `texts`). This script is the generator of that block:

    python3 scripts/sync_renderer_defaults.py            # rewrite the block when it differs
    python3 scripts/sync_renderer_defaults.py --check    # exit code 1 when the block differs from pl.json, nothing is written

`--renderer PATH` points at another copy of the renderer (the tests use a broken copy in a temporary directory).
Exit codes: 0 = in sync (or rewritten), 1 = `--check` found a difference, 2 = a usage or file error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import i18n  # noqa: E402

RENDERER = ROOT / "library" / "skills" / "dashboard" / "assets" / "dashboard-render.js"
START, END = "  // <i18n-defaults>\n", "  // </i18n-defaults>\n"


def render_block(catalog: dict) -> str:
    """The text between the markers: `const DEFAULT_TEXTS = {...};` with sorted keys; plural objects in one line."""
    lines = ["  const DEFAULT_TEXTS = {\n"]
    for key in sorted(catalog):
        value = catalog[key]
        if isinstance(value, dict):
            forms = ", ".join(f"{json.dumps(name)}: {json.dumps(text, ensure_ascii=False)}" for name, text in sorted(value.items()))
            rendered = "{ " + forms + " }"
        else:
            rendered = json.dumps(value, ensure_ascii=False)
        lines.append(f"    {json.dumps(key)}: {rendered},\n")
    lines.append("  };\n")
    return "".join(lines)


def sync(source: str, catalog: dict) -> str:
    """`source` with the block between the markers replaced; ValueError when the markers are missing or out of order."""
    start, end = source.find(START), source.find(END)
    if start < 0 or end < start:
        raise ValueError("the markers `// <i18n-defaults>` and `// </i18n-defaults>` were not found (in this order)")
    head = start + len(START)
    return source[:head] + render_block(catalog) + source[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the block differs from pl.json; write nothing")
    parser.add_argument("--renderer", type=Path, default=RENDERER, help="renderer file (default: the library skill)")
    args = parser.parse_args(argv)
    try:
        source = args.renderer.read_text(encoding="utf-8")
        updated = sync(source, i18n.catalog_for_client("pl"))
    except (OSError, ValueError) as error:
        print(f"sync_renderer_defaults: {error}", file=sys.stderr)
        return 2
    if updated == source:
        print("in sync")
        return 0
    if args.check:
        print(f"{args.renderer}: the DEFAULT_TEXTS block differs from pl.json; run scripts/sync_renderer_defaults.py", file=sys.stderr)
        return 1
    with args.renderer.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(updated)
    print("rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
