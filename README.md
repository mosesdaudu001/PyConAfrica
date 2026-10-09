# Python at Scale — benchmark harness

Measurements behind the PyCon Africa 2026 talk *"Python at Scale: A Practical
Guide to Serving 1 Million Users with FastAPI and Flask"* (Moses Daudu Osigbodi,
Kampala, 7–11 October 2026).

The talk's argument is that teams rewrite Python systems in Go or Rust for load
they never see, and that architecture — not language — determines throughput.
This repo is the evidence. **[RESULTS.md](RESULTS.md) has the numbers, the
verdicts, and the list of claims the measurements do *not* support.**

Everything here is reproducible. If a figure in the talk is not traceable to a
command in this repo, it should not be in the talk.

---

## What is measured

| experiment | question | fills |
|---|---|---|
| 1a | How much does each change add, cumulatively? | slide 6, before/after |
| 1b | Applied to the baseline *alone*, what actually moves the needle? | slide 5, ranking |
| 2 | Is Flask+gevent the same order of magnitude as FastAPI? | slide 9 |
| 3 | Is the GIL released around I/O? Does free-threading help I/O-bound web work? | slide 7 |
| 4 | Are the factual assertions sourceable? | slides 5, 7, 8, 9 |
| 5 | Where does a real inference request actually spend its time? | slide 4 |

One endpoint, `GET /exam/{exam_id}`: a single indexed primary-key lookup
returning JSON. One app file, [app/main.py](app/main.py), with the four knobs
read from the environment — so between any two runs *only the configuration
changes*. The C0 handler body is the brief's baseline verbatim; it is not
"improved".

| config | driver | target | cache | workers |
|---|---|---|---|---|
| C0 | sync `def`, `psycopg.connect()` per request | Postgres | — | 1 |
| C1 | `async def`, asyncpg pool | Postgres | — | 1 |
| C2 | `async def`, asyncpg pool | PgBouncer | — | 1 |
| C3 | `async def`, asyncpg pool | PgBouncer | — | 8 |
| C4 | `async def`, asyncpg pool | PgBouncer | Redis | 8 |
| B1 | `async def`, asyncpg pool | Postgres | — | 1 |
| B2 | sync `def`, connect per request | PgBouncer | — | 1 |
| B3 | sync `def`, connect per request | Postgres | — | 8 |
| B4 | sync `def`, connect per request | Postgres | Redis | 1 |

B1–B4 are each *one* change applied to C0. B1 is by construction identical to
C1; it is run separately anyway, which gives six independent replicates of that
configuration.

## Method

* **Dataset** 200,000 rows, ~1.5 KB jsonb each, 228 MB total — larger than the
  stock 128 MB `shared_buffers`, so the query does real buffer-pool work.
* **Random key every request** `oha --rand-regex-url .../exam/1[0-9]{5}` draws
  uniformly from a 100,000-key range. Hammering one id would serve everything
  from `shared_buffers` and give a 100% cache hit rate.
* **Warm up, then measure** 10 s discarded, then a 30 s measured window.
* **Three repetitions**, median reported, spread flagged above 15%.
* **Postgres `max_connections` left at the stock 100.** Not raised. The ceiling
  is part of what is being measured.
* **Identical everything else** — same host, same cores, same concurrency (100),
  same keep-alive, same kernel settings for every run. No tuning applied to one
  side only.
* **Failures counted as failures**, and separated from `oha`'s end-of-window
  in-flight aborts, which are a stopwatch artefact rather than a server error.
* **The load generator is proven not to be the bottleneck** — `bench/ceiling.sh`
  measures the same stack serving a no-work endpoint, and `bench/monitor.py`
  records generator CPU and the live keep-alive connection count during every
  run.

## Run it

```bash
make setup     # provision the host (root): Postgres 17, PgBouncer, Redis 7, oha, Python
make up        # start the data tier, pinned to APP_CORES
make seed      # 200k rows
make bench     # Experiment 1 (9 configs x 3 reps) + Experiment 2 + collect
make gil       # Experiment 3
make collect   # re-aggregate results/raw -> results/tables.md
```

`make smoke` is a 2-minute sanity pass (1 rep, short windows, C0 and C4) — run
it first on any new host.

### Two ways to bring the stack up

* **`docker-compose.yml`** is the reference stack and what the brief specifies.
  Use it if you have a Docker daemon.
* **`native/setup.sh` + `native/services.sh`** install the same upstream
  packages directly. The measurement host had no Docker daemon available, so
  **this is the path that produced the numbers in RESULTS.md.** Both run
  identical app code, SQL, seed and load profile.

Core pinning: the app tier (uvicorn, Postgres, PgBouncer, Redis) is confined to
`APP_CORES` (default `0-7`) so the measured box is an 8-vCPU box; the load
generator gets `GEN_CORES` (default `8-11`) and nothing else. CPU affinity is
inherited across `fork()`, so pinning the Postgres postmaster pins every backend
it forks. Override both for different hardware:

```bash
APP_CORES=0-3 GEN_CORES=4-5 make bench
```

## Layout

```
app/main.py           the one app; four knobs, one SQL query, C0 body verbatim
app/flask_app.py      Experiment 2: Flask + Gunicorn + gevent, C4-equivalent
bench/env.sh          load profile and topology — every script sources this
bench/configs.sh      the configuration matrix
bench/seed.py         dataset
bench/run_bench.sh    one config: 3 x (start, flush, warm up, measure, stop)
bench/run_all.sh      the full sweep
bench/monitor.py      CPU, Postgres connections, Redis hit ratio, keep-alive
bench/ceiling.sh      harness ceiling — proves what the generator can deliver
bench/collect.py      raw JSON -> results/tables.md
gil/                  Experiment 3
experiment5/          Experiment 4 slide-4 instrumentation — PROPOSED, NOT DEPLOYED
results/              raw oha + monitor JSON, logs, aggregated tables
```
