"""
Experiment 2 — Flask + Gunicorn + gevent, architecturally equivalent to C4.

Same database, same dataset, same SQL, same Redis cache-aside, same random-id
load. The only thing that differs from C4 is the framework and its concurrency
model: greenlets under gunicorn instead of an asyncio event loop under uvicorn.

gevent monkey-patching must happen before anything imports socket/ssl, so it is
done in bench/gunicorn_gevent_conf.py via the `post_fork`/`when_ready` hooks and
again defensively at the top of this module.
"""

from gevent import monkey  # noqa: E402

monkey.patch_all()

from psycogreen.gevent import patch_psycopg  # noqa: E402

patch_psycopg()

import json  # noqa: E402
import os  # noqa: E402

import redis  # noqa: E402
from flask import Flask, Response  # noqa: E402
from gevent.lock import BoundedSemaphore  # noqa: E402
from psycopg2 import pool as pgpool  # noqa: E402

PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PGB_PORT = int(os.environ.get("PGB_PORT", "6432"))
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_DB = os.environ.get("PG_DB", "bench")
PG_USER = os.environ.get("PG_USER", "bench")
PG_PASS = os.environ.get("PG_PASS", "bench")
TARGET = os.environ.get("BENCH_TARGET", "pgbouncer")
CACHE = os.environ.get("BENCH_CACHE", "on")

POOL_MIN = int(os.environ.get("BENCH_POOL_MIN", "5"))
POOL_MAX = int(os.environ.get("BENCH_POOL_MAX", "10"))

_port = PGB_PORT if TARGET == "pgbouncer" else PG_PORT

SQL = "SELECT id, title, payload FROM exams WHERE id = %s"

app = Flask(__name__)

_pool = None
_r = None
_slots = None   # see _get_pool()


def _get_pool():
    # Built lazily so each gunicorn worker owns its own pool after fork, matching
    # C4's one-pool-per-uvicorn-worker shape.
    #
    # The semaphore is what makes this actually equivalent to C4 rather than
    # merely similar. asyncpg's pool BLOCKS on acquire when every connection is
    # checked out; psycopg2's ThreadedConnectionPool RAISES PoolError instead.
    # Without this, a burst of greenlets past POOL_MAX turns into 500s rather
    # than into queueing, and the Flask column would be measuring a different
    # architecture from the FastAPI one. Measured cost of getting this wrong:
    # 0.5-4.4% of requests returned 500. See RESULTS.md, Experiment 2.
    global _pool, _r, _slots
    if _pool is None:
        _slots = BoundedSemaphore(POOL_MAX)
        _pool = pgpool.ThreadedConnectionPool(
            POOL_MIN, POOL_MAX, host=PG_HOST, port=_port, dbname=PG_DB,
            user=PG_USER, password=PG_PASS,
        )
        if CACHE == "on":
            _r = redis.Redis(host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                             port=int(os.environ.get("REDIS_PORT", "6379")),
                             decode_responses=False)
    return _pool


@app.get("/healthz")
def healthz():
    return {"ok": True, "stack": "flask-gunicorn-gevent"}


@app.get("/exam/<int:exam_id>")
def get_exam(exam_id: int):
    p = _get_pool()
    if CACHE == "on":
        hit = _r.get(f"exam:{exam_id}")
        if hit is not None:
            return Response(hit, mimetype="application/json")
    _slots.acquire()          # block, like asyncpg's pool.acquire(), never raise
    try:
        conn = p.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(SQL, (exam_id,))
                row = cur.fetchone()
            conn.rollback()
        finally:
            p.putconn(conn)
    finally:
        _slots.release()
    body = {"id": row[0], "title": row[1], "payload": row[2]} if row else {}
    raw = json.dumps(body).encode()
    if CACHE == "on":
        _r.set(f"exam:{exam_id}", raw)
    return Response(raw, mimetype="application/json")
