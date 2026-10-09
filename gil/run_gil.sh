#!/usr/bin/env bash
# Experiment 3 — run the GIL microbenchmarks on stock 3.14 and free-threaded
# 3.14t (and 3.13 for reference). The delay server runs in its own process on
# the load-generator cores so it never competes with the threads under test.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../bench/env.sh"
OUT="$BENCH_ROOT/results/gil"; mkdir -p "$OUT"
SECS="${GIL_SECONDS:-5}"

UVX="$(command -v uv || echo "$HOME/.local/bin/uv")"

run_for() {  # run_for <label> <python-spec>
  local label="$1" spec="$2"
  local py
  py="$("$UVX" python find "$spec" 2>/dev/null)" || { echo "SKIP $label: $spec not installed"; return; }
  echo "=== $label -> $py"
  "$py" -VV

  GIL_PORT=9911 taskset -c "$GEN_CORES" "$py" "$HERE/delay_server.py" &
  local srv=$!
  sleep 1.5
  GIL_PORT=9911 taskset -c "$APP_CORES" "$py" "$HERE/gil_bench.py" \
      --seconds "$SECS" --out "$OUT/${label}.json"
  kill "$srv" 2>/dev/null; wait "$srv" 2>/dev/null
  echo
}

run_for "py313"  "3.13"
run_for "py314"  "3.14"
run_for "py314t" "3.14t"
echo "results in $OUT"
