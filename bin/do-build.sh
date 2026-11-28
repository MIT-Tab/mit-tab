#!/usr/bin/env bash
set -euo pipefail
set +x

cd "$(dirname "$0")/.."
npm ci --no-audit
npm exec -- webpack --config webpack.config.js --mode production
