#!/usr/bin/env bash
# Start / stop the data tier and pin it to the app-tier cores.
#
# Pinning note: CPU affinity is inherited across fork(), so pinning the Postgres
# postmaster also pins every backend it forks later. The same trick pins
# PgBouncer and Redis. Applied identically before every configuration, so it can
# never be the thing that differs between two runs (ground rule 8).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../bench/env.sh"

PGBIN=/usr/lib/postgresql/17/bin
PGDATA_DIR=/var/lib/postgresql/17/main
PGCONF=/etc/postgresql/17/main
RUN=/var/run/bench
mkdir -p "$RUN"

pin() {  # pin <pid> <cores>
  [ -n "${1:-}" ] && taskset -acp "$2" "$1" >/dev/null 2>&1 || true
}

start_pg() {
  echo "--- postgres"
  pg_ctlcluster 17 main start 2>/dev/null || true
  for _ in $(seq 1 60); do
    pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break; sleep 0.5
  done
  PGPID=$(head -1 "$PGDATA_DIR/postmaster.pid" 2>/dev/null || echo "")
  pin "$PGPID" "$APP_CORES"
  echo "postmaster pid=$PGPID pinned to $APP_CORES"
  su - postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\"" \
    | grep -q 1 || su - postgres -c \
    "psql -c \"CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASS'\""
  su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\"" \
    | grep -q 1 || su - postgres -c "createdb -O $PG_USER $PG_DB"
  echo "max_connections = $(su - postgres -c 'psql -tAc "SHOW max_connections"')  (stock, NOT raised)"
}

write_pgb_conf() {
  cat > /etc/pgbouncer/pgbouncer.ini <<PGB
[databases]
$PG_DB = host=127.0.0.1 port=5432 dbname=$PG_DB

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = $PGB_PORT
auth_type = plain
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 2000
default_pool_size = 20
reserve_pool_size = 0
ignore_startup_parameters = extra_float_digits
logfile = /var/log/pgbouncer.log
pidfile = $RUN/pgbouncer.pid
PGB
  printf '"%s" "%s"\n' "$PG_USER" "$PG_PASS" > /etc/pgbouncer/userlist.txt
  chmod 640 /etc/pgbouncer/userlist.txt
  # PgBouncer refuses to run as root, so it runs as postgres (-u postgres) and
  # needs to own its config, pid and log paths.
  chown -R postgres:postgres /etc/pgbouncer "$RUN"
  touch /var/log/pgbouncer.log && chown postgres:postgres /var/log/pgbouncer.log
}

start_pgb() {
  echo "--- pgbouncer"
  write_pgb_conf
  rm -f "$RUN/pgbouncer.pid"
  pgbouncer -d -u postgres /etc/pgbouncer/pgbouncer.ini
  sleep 1.5
  PID=$(cat "$RUN/pgbouncer.pid" 2>/dev/null || pgrep -x pgbouncer | head -1)
  pin "$PID" "$APP_CORES"
  echo "pgbouncer pid=$PID pinned to $APP_CORES (transaction mode, default_pool_size=20)"
}

start_redis() {
  echo "--- redis"
  redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --daemonize yes \
      --pidfile "$RUN/redis.pid" --dir /var/tmp --logfile /var/log/redis-bench.log
  sleep 1
  PID=$(cat "$RUN/redis.pid" 2>/dev/null || pgrep -x redis-server | head -1)
  pin "$PID" "$APP_CORES"
  echo "redis pid=$PID pinned to $APP_CORES  $(redis-cli -p $REDIS_PORT INFO server | grep redis_version)"
}

case "${1:-start}" in
  start)
    start_pg; start_pgb; start_redis
    echo "--- connectivity"
    PGPASSWORD=$PG_PASS psql -h 127.0.0.1 -p 5432 -U $PG_USER -d $PG_DB -tAc "SELECT 'direct ok'"
    PGPASSWORD=$PG_PASS psql -h 127.0.0.1 -p $PGB_PORT -U $PG_USER -d $PG_DB -tAc "SELECT 'pgbouncer ok'"
    redis-cli -p $REDIS_PORT PING
    ;;
  stop)
    pkill -x pgbouncer 2>/dev/null || true
    redis-cli -p "$REDIS_PORT" SHUTDOWN NOSAVE 2>/dev/null || true
    pg_ctlcluster 17 main stop 2>/dev/null || true
    echo "stopped"
    ;;
  status)
    pg_isready -h 127.0.0.1 -p 5432; pgrep -x pgbouncer >/dev/null && echo "pgbouncer up"
    redis-cli -p "$REDIS_PORT" PING 2>/dev/null
    ;;
  *) echo "usage: services.sh {start|stop|status}" >&2; exit 1 ;;
esac
