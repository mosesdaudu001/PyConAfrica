#!/usr/bin/env bash
# Harness ceiling: GET /healthz — no database, no cache, no I/O at all.
#
# This is the answer to "how do I know you weren't just measuring your load
# generator?". It is the same app, the same uvicorn, the same core split, the
# same concurrency, the same keep-alive and the same 10s+30s protocol as every
# measured config; the only difference is that the handler does no work. Any
# measured config sitting well below this number is app-limited. A config that
# approaches it is at the ceiling of the harness AND of FastAPI's own
# per-request overhead, and its req/s should be read as a lower bound.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
OUT="$BENCH_ROOT/results/ceiling.json"
HEALTH_URL="http://127.0.0.1:$APP_PORT/healthz"

echo "[" > "$OUT"
first=1
for W in 1 8; do
  for rep in 1 2 3; do
    taskset -c "$APP_CORES" env BENCH_DRIVER=async BENCH_TARGET=pg BENCH_CACHE=off \
      PG_HOST="$PG_HOST" PG_PORT="$PG_PORT" PGB_PORT="$PGB_PORT" PG_DB="$PG_DB" \
      PG_USER="$PG_USER" PG_PASS="$PG_PASS" \
      "$VENV/bin/uvicorn" app.main:app --host 127.0.0.1 --port "$APP_PORT" \
        --workers "$W" --http httptools --loop uvloop --log-level warning \
        >/dev/null 2>&1 &
    APP_PID=$!
    for _ in $(seq 1 120); do
      curl -fsS "$HEALTH_URL" >/dev/null 2>&1 && break; sleep 0.5
    done

    taskset -c "$GEN_CORES" oha -z "${WARMUP_S}s" -c "$CONCURRENCY" --no-tui \
        "$HEALTH_URL" >/dev/null 2>&1
    for _ in $(seq 1 20); do pgrep -x oha >/dev/null || break; sleep 0.25; done

    J="$(taskset -c "$GEN_CORES" oha -z "${MEASURE_S}s" -c "$CONCURRENCY" --no-tui \
        --output-format json "$HEALTH_URL" 2>/dev/null)"
    RPS="$(printf '%s' "$J" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["summary"]["requestsPerSec"])')"
    echo "workers=$W rep=$rep -> $RPS req/s"
    [ $first = 1 ] || echo "," >> "$OUT"; first=0
    printf '{"workers":%s,"rep":%s,"requests_per_sec":%s,"concurrency":%s,"measure_s":%s}' \
        "$W" "$rep" "$RPS" "$CONCURRENCY" "$MEASURE_S" >> "$OUT"

    kill -TERM "$APP_PID" 2>/dev/null
    for _ in $(seq 1 50); do kill -0 "$APP_PID" 2>/dev/null || break; sleep 0.2; done
    kill -KILL "$APP_PID" 2>/dev/null; pkill -f "uvicorn app.main:app" 2>/dev/null
    sleep 2
  done
done
echo "]" >> "$OUT"
"$PY" -c "
import json,statistics as st
d=json.load(open('$OUT'))
for w in (1,8):
    v=[r['requests_per_sec'] for r in d if r['workers']==w]
    print(f'ceiling {w} worker(s): median {st.median(v):,.0f} req/s  (runs: '+', '.join(f'{x:,.0f}' for x in v)+')')
"
