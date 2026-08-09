#!/usr/bin/env bash
# First run: install everything. Later runs: start immediately.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ] || [ ! -f .venv/.setup_complete ]; then
  echo "=============================================================="
  echo " First launch detected - starting one-time installation"
  echo " This downloads several GB and can take 10-60 minutes."
  echo "=============================================================="
  bash setup.sh
fi

exec .venv/bin/python -m app.main "$@"
