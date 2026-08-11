#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the OTIO Schnittplaner Streamlit app.
# Safe to run on any branch, including the (empty) `main` branch where no
# requirements files exist yet.
set -euo pipefail

# Ensure the venv module is available (no-op when the base snapshot already has it).
if ! python3 -m venv --help >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip

if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi
if [ -f requirements-dev.txt ]; then
  pip install -r requirements-dev.txt
fi

# Seed a local .env from the template so the app runs without cloud API keys.
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

echo "install.sh: environment ready"
