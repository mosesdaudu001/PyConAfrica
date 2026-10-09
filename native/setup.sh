#!/usr/bin/env bash
# Provision the benchmark host WITHOUT Docker.
#
# The reference stack is docker-compose.yml. The measurement host used for
# RESULTS.md (a Colab A100 runtime) has no Docker daemon and cannot get one, so
# this script installs byte-identical upstream packages natively: Postgres 17
# from PGDG, PgBouncer from PGDG, Redis 7 from packages.redis.io. Versions are
# printed at the end and recorded in RESULTS.md.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
export DEBIAN_FRONTEND=noninteractive

log() { echo "=== $*"; }

log "apt prerequisites"
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg lsb-release build-essential \
    libpq-dev pkg-config >/dev/null

CODENAME="$(lsb_release -cs)"

log "PGDG repo (Postgres 17 + PgBouncer)"
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list

log "redis.io repo (Redis 7)"
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] \
https://packages.redis.io/deb ${CODENAME} main" > /etc/apt/sources.list.d/redis.list

apt-get update -qq
log "installing postgresql-17, pgbouncer, redis"
# Redis pinned to 7.x: the brief specifies Redis 7, and packages.redis.io now
# defaults to 8.x. Pinning keeps the stack exactly as specified.
REDIS_VER="${REDIS_VER:-6:7.4.11-1rl1~jammy1}"
apt-get install -y -qq postgresql-17 postgresql-client-17 pgbouncer \
    "redis-server=$REDIS_VER" "redis-tools=$REDIS_VER" >/dev/null
apt-mark hold redis-server redis-tools >/dev/null

log "oha (load generator)"
OHA_VER="${OHA_VER:-1.11.0}"
curl -fsSL -o /usr/local/bin/oha \
  "https://github.com/hatoo/oha/releases/download/v${OHA_VER}/oha-linux-amd64" \
  || curl -fsSL -o /usr/local/bin/oha \
  "https://github.com/hatoo/oha/releases/latest/download/oha-linux-amd64"
chmod +x /usr/local/bin/oha

log "uv + python toolchains"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv python install 3.13 3.14 3.14t

log "app venv"
cd "$ROOT"
APP_PY="${APP_PY:-3.13}"
uv venv --python "$APP_PY" .venv
VENV_PY=".venv/bin/python"
uv pip install --python "$VENV_PY" -r requirements.txt

log "versions"
{
  echo "kernel: $(uname -r)"
  echo "distro: $(lsb_release -ds)"
  echo "nproc: $(nproc)"
  echo "postgres: $(/usr/lib/postgresql/17/bin/postgres --version)"
  echo "pgbouncer: $(pgbouncer --version | head -1)"
  echo "redis: $(redis-server --version)"
  echo "oha: $(oha --version)"
  echo "app python: $($VENV_PY --version)"
  echo "uv: $(uv --version)"
  $VENV_PY -m pip list 2>/dev/null | grep -Ei 'fastapi|uvicorn|asyncpg|psycopg|redis|gunicorn|gevent|flask|httptools|uvloop' || true
} | tee "$ROOT/results/environment.txt"
log "done"
