"""Test launcher for Windows only: runs the real CLI with a stand-in for the keyboard of `getpass`.

`getpass` on Windows reads the console with `msvcrt.getwch`, so a test cannot type a password through a pipe. This script replaces
`getwch` and `putwch` in ITS OWN process and then runs the CLI as `__main__`; the product code is not changed. Linux and macOS use a real
pseudoterminal (`unified_helpers.cli_on_terminal`) and never run this file.

    python win_terminal.py <password file> <cli script> [arguments of the CLI]

The password comes from a file of the temporary directory of the test (never from the command line or the environment); the file is
removed as soon as it was read. It is typed at most `PROMPTS` times, like in the POSIX helper; a further read ends the CLI with an error.
Prompts go to stderr, which the test reads as "what the terminal showed".
"""
import msvcrt
import os
import runpy
import sys

PROMPTS = 2   # "password" and "repeat"


def install(password: str) -> None:
    keys = iter((password + "\r") * PROMPTS)

    def getwch() -> str:
        try:
            return next(keys)
        except StopIteration:
            raise EOFError("win_terminal: the CLI asked for the password more often than the test types it") from None

    def putwch(char: str) -> None:
        sys.stderr.write(char)
        sys.stderr.flush()

    msvcrt.getwch, msvcrt.putwch = getwch, putwch


def main() -> None:
    source, script = sys.argv[1], sys.argv[2]
    with open(source, encoding="utf-8") as handle:
        password = handle.read()
    os.remove(source)
    install(password)
    sys.argv = sys.argv[2:]
    sys.path[0] = os.path.dirname(os.path.abspath(script))
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
