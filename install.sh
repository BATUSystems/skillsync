#!/bin/sh
# skillsync installer for Linux, WSL2 and macOS (INST-1).
#
#   curl --proto '=https' --tlsv1.2 -LsSf \
#     https://raw.githubusercontent.com/BATUSystems/skillsync/<tag>/install.sh | sh -s -- [skillsync setup options]
#
# What it does (no sudo, safe to run again):
#   1. checks git and curl (it does not install them);
#   2. installs uv with the official installer when uv is missing;
#   3. installs skillsync as a uv tool from the tag baked into this script;
#   4. runs `skillsync setup` with the options you passed (e.g. --org <id> --org-source <url>).
#
# Read the script before you run it; it is served from an immutable release tag.
#
# Environment (mostly for tests and CI):
#   SKILLSYNC_TAG            release tag to install (default: the tag baked into this script)
#   SKILLSYNC_INSTALL_SPEC   full requirement for `uv tool install` (default: git+https://github.com/BATUSystems/skillsync@<tag>)
#   SKILLSYNC_NO_SETUP=1     install the tool only; do not run `skillsync setup`
#   SKILLSYNC_INSTALL_FORCE=1  replace a `skillsync` on PATH that is not a uv tool
# Exit codes: 0 ok, 2 environment problem (unsupported system, missing git/curl, foreign skillsync), 3 install failed;
# after a successful install the exit code of `skillsync setup` is passed through (0 PASS, 1 FAIL, 2 BLOCKED;
# the messages say which step failed).
# Docs: https://github.com/BATUSystems/skillsync/blob/main/docs/installation.md (English) and README.pl.md (Polish).
#
# The whole body is in main(): a truncated download then runs nothing.

set -eu

SKILLSYNC_DEFAULT_TAG="v1.2.3"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

say() { printf '%s\n' "install.sh: $*"; }
fail() { code=$1; shift; printf '%s\n' "install.sh: error: $*" >&2; exit "$code"; }

have() { command -v "$1" >/dev/null 2>&1; }

check_system() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    Linux|Darwin) ;;
    MINGW*|MSYS*|CYGWIN*)
      fail 2 "native Windows is not supported by this installer. Use WSL2 (Ubuntu): run 'wsl --install -d Ubuntu', open the Ubuntu shell and run this command there."
      ;;
    *)
      fail 2 "unsupported system '$(uname -s 2>/dev/null)': this installer supports Linux, WSL2 and macOS."
      ;;
  esac
}

install_hint() {
  if have apt-get; then printf '%s' "sudo apt-get install -y git curl"
  elif have dnf; then printf '%s' "sudo dnf install -y git curl"
  elif have brew; then printf '%s' "brew install git curl"
  else printf '%s' "install git and curl with your package manager"
  fi
}

check_tools() {
  missing=""
  have git || missing="$missing git"
  have curl || missing="$missing curl"
  if [ -n "$missing" ]; then
    fail 2 "missing:$missing. Install them (for example: $(install_hint)) and run this installer again. It never uses sudo itself."
  fi
}

ensure_uv() {
  if have uv; then
    say "uv found: $(command -v uv)"
    return 0
  fi
  say "uv not found: installing it with the official installer ($UV_INSTALLER_URL)"
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/skillsync-install.XXXXXX") || fail 3 "cannot create a temporary directory"
  if ! curl --proto '=https' --tlsv1.2 -fsSL -o "$tmp/uv-install.sh" "$UV_INSTALLER_URL"; then
    rm -rf "$tmp"
    fail 3 "could not download the uv installer from $UV_INSTALLER_URL (network or proxy?). Install uv yourself (https://docs.astral.sh/uv/) and run this installer again."
  fi
  if ! sh "$tmp/uv-install.sh"; then
    rm -rf "$tmp"
    fail 3 "the uv installer failed. Install uv yourself (https://docs.astral.sh/uv/) and run this installer again."
  fi
  rm -rf "$tmp"
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
  have uv || fail 3 "uv was installed but is not on PATH. Open a new shell (or run 'uv tool update-shell') and run this installer again."
}

extend_path() {
  bindir=$(uv tool dir --bin 2>/dev/null || true)
  if [ -n "$bindir" ]; then PATH="$bindir:$PATH"; fi
  PATH="$HOME/.local/bin:$PATH"
  export PATH
}

check_foreign_skillsync() {
  if ! have skillsync; then return 0; fi
  if uv tool list 2>/dev/null | grep -q '^skillsync '; then return 0; fi
  if [ "${SKILLSYNC_INSTALL_FORCE:-}" = "1" ]; then
    say "a skillsync that is not a uv tool is on PATH ($(command -v skillsync)); SKILLSYNC_INSTALL_FORCE=1, continuing"
    return 0
  fi
  fail 2 "'skillsync' on PATH ($(command -v skillsync)) was not installed by uv (a checkout, a symlink or pip). Installing over it could break that setup. Remove it, or run again with SKILLSYNC_INSTALL_FORCE=1 to let uv install its own copy."
}

install_tool() {
  say "installing skillsync $tag as a uv tool"
  if ! uv tool install --force "$spec"; then
    fail 3 "'uv tool install' failed for $spec. Check that the tag exists (https://github.com/BATUSystems/skillsync/tags) and that you can reach github.com."
  fi
  extend_path
  have skillsync || fail 3 "skillsync was installed but is not on PATH. Run 'uv tool update-shell', open a new shell and run 'skillsync setup'."
  if [ -z "${SKILLSYNC_INSTALL_SPEC:-}" ]; then
    installed=$(skillsync --version 2>/dev/null | awk '{print $2}')
    if [ "v$installed" != "$tag" ]; then
      fail 3 "installed version 'skillsync $installed' does not match the tag $tag."
    fi
  fi
  say "installed: $(skillsync --version 2>/dev/null)"
}

run_setup() {
  if [ "${SKILLSYNC_NO_SETUP:-}" = "1" ]; then
    say "SKILLSYNC_NO_SETUP=1: skipping 'skillsync setup'. Run it when you are ready."
    return 0
  fi
  # `curl ... | sh` leaves stdin on the pipe: read the answers from the terminal when there is one.
  if [ ! -t 0 ] && ( : </dev/tty ) 2>/dev/null; then
    skillsync setup "$@" </dev/tty
  else
    skillsync setup "$@"
  fi
}

main() {
  tag="${SKILLSYNC_TAG:-$SKILLSYNC_DEFAULT_TAG}"
  spec="${SKILLSYNC_INSTALL_SPEC:-git+https://github.com/BATUSystems/skillsync@$tag}"
  check_system
  check_tools
  # A uv installed by an earlier run lives in ~/.local/bin, which a fresh shell may not have on PATH yet.
  PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin"
  export PATH
  ensure_uv
  extend_path
  check_foreign_skillsync
  install_tool
  run_setup "$@"   # with set -e a failing `skillsync setup` ends the script with its exit code
  say "done. If 'skillsync' is not found in a new shell, run 'uv tool update-shell'."
}

main "$@"
