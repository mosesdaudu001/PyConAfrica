#!/usr/bin/env bash
# Full Experiment 1 + 2 sweep. Everything below runs on the same host, same
# services, same dataset and same load profile; only the config changes.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/configs.sh"
cd "$BENCH_ROOT"

MASTER="$LOG_DIR/run_all.log"
echo "=== sweep started $(date -u +%FT%TZ) ===" | tee -a "$MASTER"
echo "concurrency=$CONCURRENCY warmup=${WARMUP_S}s measure=${MEASURE_S}s reps=$REPS" | tee -a "$MASTER"
echo "app_cores=$APP_CORES gen_cores=$GEN_CORES" | tee -a "$MASTER"

for cfg in ${CONFIGS:-$ALL_CONFIGS}; do
  echo "" | tee -a "$MASTER"
  echo "########## $cfg ##########" | tee -a "$MASTER"
  bash "$HERE/run_bench.sh" "$cfg" 2>&1 | tee -a "$MASTER"
done

if [ "${SKIP_FLASK:-0}" != "1" ]; then
  echo "" | tee -a "$MASTER"
  echo "########## FLASK ##########" | tee -a "$MASTER"
  bash "$HERE/run_flask.sh" 2>&1 | tee -a "$MASTER"
fi

echo "=== sweep finished $(date -u +%FT%TZ) ===" | tee -a "$MASTER"
"$PY" "$HERE/collect.py" 2>&1 | tee -a "$MASTER"
