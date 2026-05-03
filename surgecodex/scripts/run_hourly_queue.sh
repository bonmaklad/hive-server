#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/surgecodex"
cd "$ROOT"

source "$ROOT/scripts/surge_git_env.sh"

python3 "$ROOT/scripts/run_surge_queue.py" --once
