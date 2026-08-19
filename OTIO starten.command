#!/bin/bash
# Doppelklick im Finder: OTIO-Launcher (Start / Stop / git pull / Branch).
cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PY="$ROOT/.venv/bin/python3"
else
  PY="$(command -v python3)"
fi
if [[ -z "$PY" ]]; then
  echo "Python3 nicht gefunden. Bitte zuerst die Installation aus der README ausführen."
  read -r -p "Taste drücken …" _
  exit 1
fi
exec "$PY" "$ROOT/scripts/otio_launcher.py"
