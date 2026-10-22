#!/usr/bin/env bash
# Seahorse installer for macOS and Linux. Usage:
#   curl -fsSL https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh | bash
# It installs uv (the Python tool manager) and Seahorse, then starts the setup. Seahorse comes from the zip of its
# release, so the install needs no git.
# The setup uses SEAHORSE_NO_SETUP=1 and SEAHORSE_VERSION=vX.Y.Z when it installs Seahorse on a server.
set -euo pipefail

REPO="lorenzoa7/seahorse"
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m%s\033[0m\n' "$1" >&2; exit 1; }

case "$(uname -s)" in
  Linux|Darwin) ;;
  *) fail "On Windows, run this in PowerShell: irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex" ;;
esac

# On Linux, Seahorse keeps the notes with git. On a Mac, /usr/bin/git can be only a stub that asks for the
# Command Line Tools: the setup checks for them when it needs git.
if [ "$(uname -s)" = Linux ] && ! command -v git >/dev/null 2>&1; then
  say "Installing git…"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq git || fail "Could not install git. Install it, and run this again."
  else
    fail "This system has no apt-get. On a server, Seahorse needs Ubuntu or Debian. On your own computer, install git, and run this again."
  fi
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  say "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Could not install uv. See https://docs.astral.sh/uv/ and run this again."
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || fail "uv is installed, but this shell cannot find it. Open a new terminal, and run this again."

# The newest release. The page of the latest release sends to the page of its tag, with no limit. The GitHub API
# allows only 60 requests an hour for each IP, and many people can share one IP (a phone network, an office).
latest_tag() {
  local url
  url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null || true)"
  case "$url" in
    */releases/tag/*) echo "${url##*/}"; return 0 ;;
  esac
  curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1 || true
}

if [ -n "${SEAHORSE_SOURCE_DIR:-}" ]; then
  # A local copy of the code, to test it before a release.
  say "Installing Seahorse from $SEAHORSE_SOURCE_DIR…"
  uv tool install --force --python 3.12 "$SEAHORSE_SOURCE_DIR" || fail "Could not install Seahorse from $SEAHORSE_SOURCE_DIR."
else
  TAG="${SEAHORSE_VERSION:-}"
  if [ -z "$TAG" ]; then TAG="$(latest_tag)"; fi
  if [ -z "$TAG" ]; then
    TAG="main"
    say "Could not find the newest release on GitHub. Installing the main version."
  fi
  say "Installing Seahorse $TAG…"
  uv tool install --force --python 3.12 "https://github.com/$REPO/archive/$TAG.zip" \
    || fail "Could not install Seahorse $TAG (see the error above). Check the internet connection, and run this again."
fi

# The folder of the uv tools goes on the PATH of new terminals too (uv from Homebrew does not do it).
uv tool update-shell >/dev/null 2>&1 || true
BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
export PATH="$BIN:$PATH"
command -v seahorse >/dev/null 2>&1 || fail "Seahorse is installed in $BIN, but this shell cannot find it. Open a new terminal, and run: seahorse setup"

if [ -n "${SEAHORSE_NO_SETUP:-}" ]; then
  exit 0
fi
say "Starting the setup…"
seahorse setup < /dev/tty
