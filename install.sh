#!/usr/bin/env bash
# Seahorse installer for macOS and Linux. Usage:
#   curl -fsSL https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh | bash
# It installs uv (the Python tool manager) and Seahorse, then starts the setup.
# The setup uses SEAHORSE_NO_SETUP=1 and SEAHORSE_VERSION=vX.Y.Z when it installs Seahorse on a server.
set -euo pipefail

REPO="lorenzoa7/seahorse"
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo "On Windows, run this in PowerShell: irm https://raw.githubusercontent.com/$REPO/main/install.ps1 | iex"; exit 1 ;;
esac

if ! command -v git >/dev/null 2>&1; then
  say "Installing git…"
  if command -v apt-get >/dev/null 2>&1; then $SUDO apt-get update -qq && $SUDO apt-get install -y -qq git
  elif command -v brew >/dev/null 2>&1; then brew install git
  else echo "Please install git and run this again."; exit 1; fi
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

TAG="${SEAHORSE_VERSION:-}"
if [ -z "$TAG" ]; then
  TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
fi
TAG="${TAG:-main}"
say "Installing Seahorse $TAG…"
uv tool install --force --python 3.12 "git+https://github.com/$REPO@$TAG"

if [ -n "${SEAHORSE_NO_SETUP:-}" ]; then
  exit 0
fi
say "Starting the setup…"
seahorse setup < /dev/tty
