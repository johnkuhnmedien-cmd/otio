#!/usr/bin/env bash
# Per-boot startup for the OTIO Schnittplaner Streamlit dev server.
# Runs attached (foreground) so Cloud Agents can see logs and restart it.
set -euo pipefail

if [ -d .venv ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

if [ -f app.py ]; then
  exec streamlit run app.py --server.port 8501 --server.address 0.0.0.0
else
  echo "start.sh: no app.py on this branch; nothing to start."
fi
