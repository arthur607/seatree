#!/usr/bin/env bash
# Seahorse installer. Usage:
#   curl -fsSL https://raw.githubusercontent.com/lorenzoa7/seahorse/main/install.sh | bash
# It installs uv (the Python tool manager), installs the latest Seahorse release, and starts the setup.
set -euo pipefail

REPO="lorenzoa7/seahorse"

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

case "$(uname -s)" in
  Linux|Darwin) ;;
  *) echo "Seahorse runs on Linux and macOS. On Windows, install it inside WSL."; exit 1 ;;
esac

if ! command -v git >/dev/null 2>&1; then
  say "Installing git…"
  if command -v apt-get >/dev/null 2>&1; then sudo apt-get update -qq && sudo apt-get install -y -qq git
  elif command -v brew >/dev/null 2>&1; then brew install git
  else echo "Please install git and run this again."; exit 1; fi
fi

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)"
TAG="${TAG:-main}"
say "Installing Seahorse $TAG…"
uv tool install --force --python 3.12 "git+https://github.com/$REPO@$TAG"

say "Starting the setup…"
seahorse setup < /dev/tty
