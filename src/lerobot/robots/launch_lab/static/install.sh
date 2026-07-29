#!/usr/bin/env bash
# LeRobot Launch Lab -- local helper installer.
#
# Run with:
#   curl -fsSL https://pearllelab.web.app/install.sh | bash
#
# What this does, in order:
#   1. Installs `uv` (Python package/version manager) if it's not already present.
#   2. Clones (or updates) the app into ~/lerobot-launch-lab.
#   3. Installs dependencies and starts the local helper, opening your browser to it.
#
# This script itself never needs sudo -- it only touches ~/lerobot-launch-lab and
# ~/.local/bin (uv's own installer target), both undone by deleting those two
# directories. It may still prompt for your password once, to fix ownership of the
# venv if an earlier run left root-owned files in it (see the check below).

set -euo pipefail

REPO_URL="https://github.com/ad-1astra/PearllelabsApp.git"
INSTALL_DIR="${LAUNCH_LAB_INSTALL_DIR:-$HOME/lerobot-launch-lab}"

echo "== LeRobot Launch Lab installer =="

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but wasn't found. Install it first (e.g. 'sudo apt install git' or 'brew install git'), then re-run this."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "-- Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # The installer drops uv in ~/.local/bin; make it available in this script without
  # requiring the user to open a new shell.
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "-- Updating existing install at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "-- Cloning into $INSTALL_DIR..."
  git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# The in-app Calibrate/Set Motor IDs steps run under sudo (serial port access
# sometimes needs it) -- older versions of this app didn't stop Python from writing
# bytecode cache while running as root, leaving root-owned files inside .venv that
# `uv sync` then can't clean up as your normal user ("Permission denied" removing a
# __pycache__ dir). Reclaim ownership first if that's happened; a no-op, no password
# prompt, on a venv that's already fine.
if [ -d "$INSTALL_DIR/.venv" ] && find "$INSTALL_DIR/.venv" -not -user "$(whoami)" -print -quit 2>/dev/null | grep -q .; then
  echo "-- Fixing ownership of some root-owned files left in .venv by an earlier run (needs your password)..."
  sudo chown -R "$(whoami)" "$INSTALL_DIR/.venv"
fi

echo "-- Installing dependencies (first run downloads a few GB -- torch, opencv, etc. Grab a coffee.)"
uv sync --quiet

echo "-- Starting Launch Lab..."
LAUNCH_LAB_OPEN_BROWSER=1 exec uv run lerobot-launch-lab
