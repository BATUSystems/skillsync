"""Test helper: look for a path (or any canary) in a reply, whatever the spelling of the path in it.

On Windows `str(path)` has backslashes, the answers of the server mostly have `/`, and `json.dumps` doubles a backslash. A plain `assertNotIn`
of `str(path)` can then never fail there: the check is blind. Both sides go through `flat`, and a test proves with the same function that the
values it hides ARE found in a reply that is allowed to carry them.
"""
import os
import re

SEPARATORS = re.compile(r"[\\/]+")


def flat(text, windows: bool = os.name == "nt") -> str:
    """Every run of `/` and `\\` is one `/`; on Windows the letter case does not count either."""
    found = SEPARATORS.sub("/", str(text))
    return found.casefold() if windows else found
