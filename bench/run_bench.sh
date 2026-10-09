#!/usr/bin/env bash
# Run one configuration: REPS repetitions of (start app, flush cache, warm up,
# measure, stop app). Every command is echoed into results/logs so that every
# number in RESULTS.md is traceable to a command (ground rule 9).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/configs.sh"

CFG="${1:?usage: run_bench.sh <CONFIG_ID>}"
config_env "$CFG" || exit 1

LOG="$LOG_DIR/$CFG.log"
: > "$LOG"
: > "$LOG_DIR/$CFG.app.log"   # never inherit a previous run's tracebacks
say() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "config=$CFG driver=$BENCH_DRIVER target=$BENCH_TARGET cache=$BENCH_CACHE workers=$BENCH_WORKERS"
say "load: c=$CONCURRENCY warmup=${WARMUP_S}s measure=${MEASURE_S}s reps=$REPS url=$URL_REGEX"
say "cores: app=$APP_CORES gen=$GEN_CORES"

start_app() {
  taskset -c "$APP_CORES" env \
    BENCH_DRIVER="$BENCH_DRIVER" BENCH_TARGET="$BENCH_TARGET" BENCH_CACHE="$BENCH_CACHE" \
    PG_HOST="$PG_HOST" PG_PORT="$PG_PORT" PGB_PORT="$PGB_PORT" PG_DB="$PG_DB" \
    PG_USER="$PG_USER" PG_PASS="$PG_PASS" REDIS_HOST="$REDIS_HOST" REDIS_PORT="$REDIS_PORT" \
    "$VENV/bin/uvicorn" app.main:app \
      --host 127.0.0.1 --port "$APP_PORT" --workers "$BENCH_WORKERS" \
      --http httptools --loop uvloop --log-level warning \
      >> "$LOG_DIR/$CFG.app.log" 2>&1 &
  APP_PID=$!
}

stop_app() {
  [ -n "${APP_PID:-}" ] || return 0
  kill -TERM "$APP_PID" 2>/dev/null
  for _ in $(seq 1 50); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.2; done
  kill -KILL "$APP_PID" 2>/dev/null
  pkill -f "uvicorn app.main:app" 2>/dev/null
  wait "$APP_PID" 2>/dev/null
  sleep 1
}
trap stop_app EXIT

wait_ready() {
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:$APP_PORT/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 0.5
  done
  say "FATAL: app did not become ready"; return 1
}

for rep in $(seq 1 "$REPS"); do
  say "--- rep $rep/$REPS ---"

  if [ "$BENCH_CACHE" = "on" ]; then
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" FLUSHALL >>"$LOG" 2>&1
    say "redis flushed (cache starts cold; the 10s warm-up primes it)"
  fi
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" CONFIG RESETSTAT >>"$LOG" 2>&1

  start_app
  wait_ready || { stop_app; continue; }
  say "app up pid=$APP_PID -> $(curl -fsS http://127.0.0.1:$APP_PORT/healthz)"

  # Pre-flight. A harness bug (bad URL pattern, wrong port, empty table) shows up
  # as a wall of 4xx that still produces a plausible-looking req/s number. Assert
  # one real row comes back before spending 40s measuring nothing.
  PROBE_URL="$(oha --rand-regex-url --dump-urls 1 "$URL_REGEX" 2>/dev/null | head -1)"
  PROBE_CODE="$(curl -s -o "$LOG_DIR/$CFG.probe.json" -w '%{http_code}' "$PROBE_URL")"
  PROBE_ID="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('id'))" \
      "$LOG_DIR/$CFG.probe.json" 2>/dev/null)"
  say "pre-flight $PROBE_URL -> HTTP $PROBE_CODE id=$PROBE_ID"
  if [ "$PROBE_CODE" != "200" ] || [ -z "$PROBE_ID" ] || [ "$PROBE_ID" = "None" ]; then
    say "FATAL: pre-flight did not return a row. Refusing to record a bogus run."
    stop_app; exit 2
  fi

  say "warm-up ${WARMUP_S}s (discarded)"
  taskset -c "$GEN_CORES" oha -z "${WARMUP_S}s" -c "$CONCURRENCY" --no-tui \
      --rand-regex-url "$URL_REGEX" >>"$LOG" 2>&1
  # the warm-up process must be reaped before the monitor samples generator CPU
  for _ in $(seq 1 20); do pgrep -x oha >/dev/null || break; sleep 0.25; done

  MON_OUT="$RAW_DIR/${CFG}_rep${rep}.monitor.json"
  OHA_OUT="$RAW_DIR/${CFG}_rep${rep}.oha.json"

  say "measure ${MEASURE_S}s -> $OHA_OUT"
  taskset -c "$GEN_CORES" "$PY" "$HERE/monitor.py" \
      --duration "$MEASURE_S" --app-cores "$APP_CORES" --gen-cores "$GEN_CORES" \
      --dsn "$SEED_DSN" --redis "$REDIS_HOST:$REDIS_PORT" --gen-proc oha \
      --app-port "$APP_PORT" --out "$MON_OUT" >>"$LOG" 2>&1 &
  MON_PID=$!

  taskset -c "$GEN_CORES" oha -z "${MEASURE_S}s" -c "$CONCURRENCY" --no-tui --output-format json \
      --rand-regex-url "$URL_REGEX" > "$OHA_OUT" 2>>"$LOG"
  wait "$MON_PID" 2>/dev/null

  "$PY" - "$OHA_OUT" "$MON_OUT" <<'PYX' | tee -a "$LOG"
import json, sys
o = json.load(open(sys.argv[1]))
try: m = json.load(open(sys.argv[2]))
except Exception: m = {}
s = o.get("summary", {}); p = o.get("latencyPercentiles", {})
codes = o.get("statusCodeDistribution", {})
ok = sum(v for k, v in codes.items() if str(k).startswith("2"))
tot = sum(codes.values()) + len(o.get("errorDistribution", {}) or {})
print(f"  req/s={s.get('requestsPerSec',0):.1f} "
      f"p50={p.get('p50',0)*1000:.1f}ms p99={p.get('p99',0)*1000:.1f}ms "
      f"codes={codes} errs={o.get('errorDistribution')} "
      f"appcpu={(m.get('app_cpu_busy_pct') or {}).get('avg')}% "
      f"gencpu={m.get('loadgen_proc_cpu_pct_of_its_cores')}% "
      f"pgconn_max={(m.get('pg_connections') or {}).get('max')} "
      f"redis={m.get('redis')}")
PYX

  stop_app
  sleep 2
done

say "done $CFG"
