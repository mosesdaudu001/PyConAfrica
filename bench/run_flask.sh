#!/usr/bin/env bash
# Experiment 2 — Flask + Gunicorn + gevent, architecturally equivalent to C4.
# Identical dataset, SQL, cache, load profile, cores and duration as C4.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"

CFG=FLASK
LOG="$LOG_DIR/$CFG.log"; : > "$LOG"
: > "$LOG_DIR/$CFG.app.log"   # never inherit a previous run's tracebacks
say() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Gunicorn's documented rule, applied to the 8 vCPUs the app tier is pinned to.
NCORES=$(python3 - <<'PY'
import os
s=os.environ["APP_CORES"]; n=0
for p in s.split(","):
    if "-" in p: a,b=p.split("-"); n+=int(b)-int(a)+1
    elif p: n+=1
print(n)
PY
)
WORKERS=$((2 * NCORES + 1))
say "flask: gunicorn -k gevent -w $WORKERS (2 x $NCORES + 1), cache=on, target=pgbouncer"

start_app() {
  taskset -c "$APP_CORES" env \
    BENCH_TARGET=pgbouncer BENCH_CACHE=on \
    PG_HOST="$PG_HOST" PG_PORT="$PG_PORT" PGB_PORT="$PGB_PORT" PG_DB="$PG_DB" \
    PG_USER="$PG_USER" PG_PASS="$PG_PASS" REDIS_HOST="$REDIS_HOST" REDIS_PORT="$REDIS_PORT" \
    "$VENV/bin/gunicorn" app.flask_app:app -k gevent -w "$WORKERS" \
      -b "127.0.0.1:$APP_PORT" --worker-connections 200 --log-level warning \
      >> "$LOG_DIR/$CFG.app.log" 2>&1 &
  APP_PID=$!
}
stop_app() {
  [ -n "${APP_PID:-}" ] || return 0
  kill -TERM "$APP_PID" 2>/dev/null
  for _ in $(seq 1 50); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.2; done
  kill -KILL "$APP_PID" 2>/dev/null; pkill -f "app.flask_app:app" 2>/dev/null
  wait "$APP_PID" 2>/dev/null; sleep 1
}
trap stop_app EXIT

for rep in $(seq 1 "$REPS"); do
  say "--- rep $rep/$REPS ---"
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" FLUSHALL >>"$LOG" 2>&1
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" CONFIG RESETSTAT >>"$LOG" 2>&1
  start_app
  ready=0
  for _ in $(seq 1 120); do
    curl -fsS "http://127.0.0.1:$APP_PORT/healthz" >/dev/null 2>&1 && { ready=1; break; }
    sleep 0.5
  done
  [ "$ready" = 1 ] || { say "FATAL: flask app not ready"; stop_app; continue; }
  say "app up pid=$APP_PID"

  say "warm-up ${WARMUP_S}s (discarded)"
  taskset -c "$GEN_CORES" oha -z "${WARMUP_S}s" -c "$CONCURRENCY" --no-tui \
      --rand-regex-url "$URL_REGEX" >>"$LOG" 2>&1
  for _ in $(seq 1 20); do pgrep -x oha >/dev/null || break; sleep 0.25; done

  MON_OUT="$RAW_DIR/${CFG}_rep${rep}.monitor.json"
  OHA_OUT="$RAW_DIR/${CFG}_rep${rep}.oha.json"
  taskset -c "$GEN_CORES" "$PY" "$HERE/monitor.py" --duration "$MEASURE_S" \
      --app-cores "$APP_CORES" --gen-cores "$GEN_CORES" --dsn "$SEED_DSN" \
      --redis "$REDIS_HOST:$REDIS_PORT" --gen-proc oha --app-port "$APP_PORT" --out "$MON_OUT" >>"$LOG" 2>&1 &
  MON_PID=$!
  taskset -c "$GEN_CORES" oha -z "${MEASURE_S}s" -c "$CONCURRENCY" --no-tui --output-format json \
      --rand-regex-url "$URL_REGEX" > "$OHA_OUT" 2>>"$LOG"
  wait "$MON_PID" 2>/dev/null
  "$PY" -c "
import json,sys
o=json.load(open('$OHA_OUT')); s=o.get('summary',{}); p=o.get('latencyPercentiles',{})
print('  req/s=%.1f p50=%.1fms p99=%.1fms codes=%s'%(s.get('requestsPerSec',0),p.get('p50',0)*1000,p.get('p99',0)*1000,o.get('statusCodeDistribution')))" | tee -a "$LOG"
  stop_app; sleep 2
done
say "done FLASK"
