#!/usr/bin/env bash
# TradingByZaaz setup — installs backend deps and starts the signal terminal.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install --quiet --break-system-packages -r requirements.txt 2>/dev/null \
  || python3 -m pip install --quiet --user -r requirements.txt \
  || python3 -m pip install --quiet -r requirements.txt

echo "Starting TradingByZaaz on http://0.0.0.0:8000 ..."
exec python3 -m uvicorn server.main:app --host 0.0.0.0 --port "${PORT:-8000}"
