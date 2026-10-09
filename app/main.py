"""
One FastAPI app, one endpoint, one SQL query.

Everything the benchmark varies is read from the environment, so that between any
two runs *only the configuration changes* (ground rule 1). There is exactly one
source file for the app tier; there is no "optimised copy" of the endpoint.

  BENCH_DRIVER = sync   -> the verbatim baseline handler from the brief
                 async  -> async def + asyncpg + connection pool
  BENCH_TARGET = pg         -> connect straight to Postgres
                 pgbouncer  -> connect through PgBouncer (transaction mode)
  BENCH_CACHE  = off | on   -> Redis cache-aside in front of the query

Worker count is not read here; it is passed to uvicorn on the command line.
"""

import json
import os
import contextlib

from fastapi import FastAPI, Response

DRIVER = os.environ.get("BENCH_DRIVER", "sync")
TARGET = os.environ.get("BENCH_TARGET", "pg")
CACHE = os.environ.get("BENCH_CACHE", "off")

PG_HOST = os.environ.get("PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PGB_PORT = int(os.environ.get("PGB_PORT", "6432"))
PG_DB = os.environ.get("PG_DB", "bench")
PG_USER = os.environ.get("PG_USER", "bench")
PG_PASS = os.environ.get("PG_PASS", "bench")

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# asyncpg's own default pool ceiling. Held identical across every async config so
# that pool size is never the thing that changed between two runs.
POOL_MIN = int(os.environ.get("BENCH_POOL_MIN", "5"))
POOL_MAX = int(os.environ.get("BENCH_POOL_MAX", "10"))

_port = PGB_PORT if TARGET == "pgbouncer" else PG_PORT
DSN = f"postgresql://{PG_USER}:{PG_PASS}@{PG_HOST}:{_port}/{PG_DB}"

# Same query in every configuration. The placeholder token is the only difference
# and it is forced by the driver's paramstyle, not by us.
SQL_SYNC = "SELECT id, title, payload FROM exams WHERE id = %s"
SQL_ASYNC = "SELECT id, title, payload FROM exams WHERE id = $1"

pool = None
aredis = None
sredis = None


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global pool, aredis, sredis
    if DRIVER == "async":
        import asyncpg

        async def _init(conn):
            # asyncpg hands back jsonb as str; psycopg3 hands back a dict. Decode
            # here so both drivers return the same Python object and pay the same
            # json parse cost. Without this the async config would look faster for
            # a reason that has nothing to do with the driver.
            await conn.set_type_codec(
                "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )

        pool = await asyncpg.create_pool(
            dsn=DSN, min_size=POOL_MIN, max_size=POOL_MAX, init=_init,
            # PgBouncer in transaction mode cannot support server-side prepared
            # statements shared across transactions.
            statement_cache_size=0 if TARGET == "pgbouncer" else 100,
        )
    if CACHE == "on":
        if DRIVER == "async":
            import redis.asyncio as _ar

            aredis = _ar.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
        else:
            import redis as _r

            sredis = _r.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
    yield
    if pool is not None:
        await pool.close()
    if aredis is not None:
        await aredis.aclose()
    if sredis is not None:
        sredis.close()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True, "driver": DRIVER, "target": TARGET, "cache": CACHE}


# ---------------------------------------------------------------------------
# C0 / Set-B sync handler.
#
# The body below is the baseline from the brief, used verbatim. Do not "improve"
# it. The cache branch is bolted on in front of it for config B4 only; when
# BENCH_CACHE=off the code path is exactly the brief's.
# ---------------------------------------------------------------------------
if DRIVER == "sync":
    import psycopg

    @app.get("/exam/{exam_id}")
    def get_exam(exam_id: int):                      # sync def, runs in threadpool
        if CACHE == "on":
            hit = sredis.get(f"exam:{exam_id}")
            if hit is not None:
                return Response(content=hit, media_type="application/json")
        conn = psycopg.connect(DSN)                  # new connection per request
        try:
            with conn.cursor() as cur:
                cur.execute(SQL_SYNC, (exam_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        body = {"id": row[0], "title": row[1], "payload": row[2]} if row else {}
        if CACHE == "on":
            raw = json.dumps(body).encode()
            sredis.set(f"exam:{exam_id}", raw)
            return Response(content=raw, media_type="application/json")
        return body

else:

    @app.get("/exam/{exam_id}")
    async def get_exam(exam_id: int):
        if CACHE == "on":
            hit = await aredis.get(f"exam:{exam_id}")
            if hit is not None:
                return Response(content=hit, media_type="application/json")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_ASYNC, exam_id)
        body = {"id": row[0], "title": row[1], "payload": row[2]} if row else {}
        if CACHE == "on":
            raw = json.dumps(body).encode()
            await aredis.set(f"exam:{exam_id}", raw)
            return Response(content=raw, media_type="application/json")
        return body
