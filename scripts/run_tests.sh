#!/usr/bin/env bash
# Run each test suite in its OWN pytest process (fully isolated — sidesteps the
# shared `app` package name entirely). Mirrors the CI matrix. A single
# `python -m pytest` also works (see pytest.ini); this is the belt-and-braces run.
#
# Usage: bash scripts/run_tests.sh
set -uo pipefail

cd "$(dirname "$0")/.."

SUITES=(
  "shared/tests"
  "services/ai-filter/tests"
  "services/backtest/tests"
  "services/executor/tests"
  "services/gateway/tests"
  "services/journal/tests"
  "services/market-data/tests"
  "services/risk/tests"
)

fail=0
for suite in "${SUITES[@]}"; do
  echo "──────────────────────────────────────────────────────────────"
  echo "▶ $suite"
  echo "──────────────────────────────────────────────────────────────"
  if ! python -m pytest "$suite" -q; then
    fail=1
    echo "✗ FAILED: $suite"
  fi
done

echo "──────────────────────────────────────────────────────────────"
if [ "$fail" -ne 0 ]; then
  echo "✗ One or more suites failed."
  exit 1
fi
echo "✓ All suites passed."
