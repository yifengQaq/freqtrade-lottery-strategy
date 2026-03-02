#!/usr/bin/env bash
# Launch the V2ex-Agent Dashboard
#
# Usage:
#   ./scripts/run_dashboard.sh         # default port 8501
#   ./scripts/run_dashboard.sh 8080    # custom port
#   DASHBOARD_PORT=8511 ./scripts/run_dashboard.sh

set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${1:-${DASHBOARD_PORT:-8501}}"

echo "🚀 Starting V2ex-Agent Dashboard on http://localhost:${PORT}"
echo "   Press Ctrl+C to stop"
echo ""

source .venv/bin/activate
exec uvicorn dashboard.app:app --host 0.0.0.0 --port "$PORT" --reload
