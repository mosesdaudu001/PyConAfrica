# Shared benchmark environment. Sourced by every script so that no run can
# accidentally differ from another in anything except the config under test.

export BENCH_ROOT="${BENCH_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export VENV="${VENV:-$BENCH_ROOT/.venv}"
export PY="${PY:-$VENV/bin/python}"

# --- topology -------------------------------------------------------------
# App tier (uvicorn/gunicorn + Postgres + PgBouncer + Redis) is confined to
# APP_CORES so the measured box is an 8-vCPU box, matching the planned stage
# hardware. The load generator gets GEN_CORES and nothing else.
export APP_CORES="${APP_CORES:-0-7}"
export GEN_CORES="${GEN_CORES:-8-11}"

export PG_HOST=127.0.0.1
export PG_PORT=5432
export PGB_PORT=6432
export PG_DB=bench
export PG_USER=bench
export PG_PASS=bench
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
export SEED_DSN="postgresql://$PG_USER:$PG_PASS@$PG_HOST:$PG_PORT/$PG_DB"

export APP_PORT="${APP_PORT:-8000}"

# --- load profile (identical for every configuration) ---------------------
export CONCURRENCY="${CONCURRENCY:-100}"
export WARMUP_S="${WARMUP_S:-10}"
export MEASURE_S="${MEASURE_S:-30}"
export REPS="${REPS:-3}"
# Seeded ids are 1..200000. Draw uniformly from 100000..199999 -> 100k keys.
# NB: assigned in two steps on purpose. Inside ${VAR:-default} the closing brace
# of the regex quantifier {5} terminates the parameter expansion, which silently
# produced /exam/1[0-9]{5} + a stray "}" and made every request 422.
if [ -z "${URL_REGEX:-}" ]; then
  URL_REGEX='http://127.0.0.1:'"$APP_PORT"'/exam/1[0-9]{5}'
fi
export URL_REGEX

export RAW_DIR="${RAW_DIR:-$BENCH_ROOT/results/raw}"
export LOG_DIR="${LOG_DIR:-$BENCH_ROOT/results/logs}"
mkdir -p "$RAW_DIR" "$LOG_DIR"
