# Benchmark results

Measurements for *"Python at Scale: A Practical Guide to Serving 1 Million Users
with FastAPI and Flask"* — PyCon Africa 2026, Kampala.

Run 2026-08-26. Every figure below is traceable to a command in this repo and to
raw JSON in `results/raw/`. Where something was not measured it says
**NOT MEASURED**, not a plausible-looking number.

**Read [Claims the measurements do NOT support](#claims-the-measurements-do-not-support)
first.** It is the most valuable section here, and four of its items change
slides.

---

## Environment

| | |
|---|---|
| Host | Google Colab runtime, a container (`systemd-detect-virt` → `docker`) on a Chromium OS host |
| CPU | Intel Xeon @ 2.20 GHz, **12 vCPU** = 6 physical cores × 2 threads |
| **App tier** | pinned to **cores 0–7 (8 vCPU)** — uvicorn/gunicorn, Postgres, PgBouncer, Redis |
| **Load generator** | pinned to **cores 8–11 (4 vCPU)**, nothing else on them |
| RAM | 167 GiB |
| Kernel | 6.6.122+, **`mitigations=off retbleed=off`** |
| Distro | Ubuntu 22.04.5 LTS |
| Postgres | 17.11 (PGDG), **stock config, `max_connections` = 100, `shared_buffers` = 128 MB** |
| PgBouncer | 1.25.2, transaction mode, `default_pool_size` = 20, `max_client_conn` = 2000 |
| Redis | 7.4.11 |
| App Python | 3.13.15 · fastapi 0.118.0 · uvicorn 0.37.0 (httptools + uvloop) · asyncpg 0.30.0 · psycopg 3.2.10 · redis 6.4.0 |
| Flask stack | flask 3.1.2 · gunicorn 23.0.0 · gevent 25.9.1 · psycogreen 1.0.2 · psycopg2-binary 2.9.10 |
| GIL experiment | CPython **3.14.7** stock and **3.14.7 free-threaded** (`3.14t`), both via `uv` |
| Load generator | **oha 1.11.0**, concurrency **100**, keep-alive on, random id per request |
| Protocol | 10 s warm-up discarded → **30 s measured window**, **3 repetitions**, median reported |
| Dataset | **200,000 rows**, `id / title / subject / status / payload jsonb / created_at`, **228 MB** total relation size |
| Key space | ids drawn uniformly from **100,000–199,999** via `oha --rand-regex-url .../exam/1[0-9]{5}` |
| **Kernel tuning applied** | **NONE.** No sysctls, no `ulimit -n` change (`1048576`, the container default, for every run), no keep-alive toggles. Identical for all runs. |

Full record: [`results/environment.txt`](results/environment.txt),
[`results/dataset.json`](results/dataset.json).

### Three environment caveats that belong in the speaker's head

1. **This is not an 8-vCPU cloud VM, and the load generator is not on a second
   host.** It is a 12-vCPU container with the app tier confined to 8 cores by
   `taskset` and the generator confined to the other 4. That is the closest
   available approximation, not the thing itself. Loopback networking is cheaper
   than a real NIC hop, which flatters every configuration equally.
2. **`mitigations=off`.** Spectre/Meltdown mitigations are disabled on this
   host. Syscall-heavy work — which is exactly what C0 does, one `connect()` per
   request — is faster here than on a standard cloud VM where mitigations are
   on. This flatters the *baseline* more than the tuned configs, so it makes the
   improvement multipliers **conservative**.
3. **The host was shared.** The operator's own GPU job ran throughout, consuming
   **9–15% of the app cores** even at idle
   ([`idle_before.json`](results/idle_before.json),
   [`idle_after.json`](results/idle_after.json)). It was present for every
   configuration equally, so relative multipliers are sound; absolute req/s is
   depressed by roughly that much.

### Was the load generator the bottleneck? No — and here is the proof

`bench/ceiling.sh` runs `GET /healthz` — same app, same uvicorn, same cores,
same concurrency, same keep-alive, same 10 s + 30 s protocol — with a handler
that does no work at all. That is the most this harness can deliver.

| workers | harness ceiling (median of 3) | runs |
|---|---|---|
| 1 | **4,782 req/s** | 4,782 · 4,755 · 4,824 |
| 8 | **20,001 req/s** | 20,252 · 19,907 · 20,001 |

The generator's own CPU never exceeded **16.9% of its four cores** in any run
(the peak was C4, the fastest config), and `http_established` averaged **94.9 to
99.8** against a configured concurrency of 100 in every run — so keep-alive is
**measured**, not assumed. At 18,764 req/s over ~100 connections, that is ~5,600
requests per connection per run; without keep-alive the count would churn far
higher. Every configuration except C4
sits far below the ceiling. C4 does not, and that is called out below.

---

## Experiment 1a — cumulative (slide 6)

| config | what changed | req/s (median of 3) | spread | p50 ms | p95 ms | p99 ms | p99.9 ms | failures | app CPU | peak pg conns | % of ceiling |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **C0** | baseline: sync `def`, connect per request, 1 worker | **232.8** | 1.5% | 426.7 | 509.1 | **605.8** | 2,077.4 | **0** | 72% | 42 / 100 | 4.9% |
| C1 | + async driver (asyncpg + pool) | 1,737.4 | 1.2% | 57.6 | 108.3 | 157.2 | 233.2 | 0 | 29% | 31 / 100 | 36.3% |
| C2 | + PgBouncer | 1,811.7 | 3.3% | 54.4 | 61.9 | 65.5 | 110.2 | 0 | 34% | 21 / 100 | 37.9% |
| C3 | + 8 uvicorn workers | 5,981.9 | 7.1% | 16.2 | 25.5 | 31.8 | 41.8 | 0 | 88% | 21 / 100 | 29.9% |
| **C4** | + Redis cache-aside | **18,764.0** | 5.7% | 4.3 | 16.1 | **22.9** | 29.3 | **0** | 96% | 21 / 100 | **93.8%** ⚠ |

**C0 → C4 is 80.6× on throughput and 26× on p99.** No repetition varied by more
than 7.1%, comfortably inside the 15% bar.

**Zero real errors in every configuration.** Not one. `oha` reports ~100
"aborted due to deadline" per run — that is one abort per open connection when
the 30 s stopwatch stops, a property of the measurement, not the server. They
are counted and reported separately
([`results/tables.md`](results/tables.md)) and excluded from the failure column.

**⚠ C4 sits at 93.8% of the harness ceiling.** With a 91.9% cache hit ratio the
database has essentially left the critical path, and what remains is FastAPI's
own per-request overhead — the same thing `/healthz` measures. So **18,764 req/s
is a floor for C4, not its capacity.** Quote it as "over 18,000" and say why, or
re-run on two hosts to find the real number. Do not present it as a ceiling.

## Experiment 1b — one change at a time (slide 5)

Each row is that single change applied to C0 and nothing else.

| change | config | req/s | **multiplier vs C0** | p99 ms | peak pg conns | cache hit | failures |
|---|---|---|---|---|---|---|---|
| **Async database driver** | B1 | 1,685.6 | **7.24×** | 167.4 | 31 | — | 0 |
| **Deliberate worker count (8)** | B3 | 379.7 | **1.63×** | 430.3 | 31 | — | 0 |
| **Cache the hot read path** | B4 | 239.3 | **1.03×** | 585.7 | 21 | **5.9%** | 0 |
| **A pooler in front of Postgres** | B2 | 69.6 | **0.30×** | 2,621.0 | **2** | — | 0 |

### Ranking, most to least effective

1. **Async driver — 7.24×.** Not close. Everything else is a rounding error next
   to it.
2. **Worker count — 1.63×.**
3. **Redis cache-aside — 1.03×.** Indistinguishable from nothing, at this speed.
4. **PgBouncer — 0.30×.** It made the baseline **3.3× slower**.

### Why the cache did nothing on its own

B4's hit ratio was **5.9%**. At 239 req/s a 30 s window is ~7,200 requests
spread over a 100,000-key space, so almost every request is a first touch. In
C4, at 18,764 req/s, the same window is ~560,000 requests over the same 100,000
keys and the hit ratio is **91.9%**.

**A cache does not make a slow service fast. It makes a fast service cheap.** You
have to already be quick enough to come back to a key before it pays. That is a
better line than "caching is cheating" and it is a measurement, not an opinion.

### Why the pooler made things *worse*

C0 opens a new connection per request. Pointing that at PgBouncer adds a
single-threaded proxy hop in front of an operation that was already the
expensive part — every request still pays a full client-side connect and auth
handshake, now through one process that must serialise all of them. Peak
Postgres backends fell to **2**, which is PgBouncer doing its job perfectly while
throughput collapsed to 69.6 req/s.

Put the same pooler behind an app that *holds* connections and it helps, mildly:
C1 → C2 is **+4.3%** throughput, and much more importantly **p99 falls from 157
ms to 66 ms** and p99.9 from 233 ms to 110 ms. That is the real case for
PgBouncer, and it is about tail latency and connection-count control, not
throughput.

**A pooler is not a speed-up. It is a stability mechanism, and it requires an
app that pools.**

### Cache hit ratios (stated openly, as the brief asks)

| config | hits | misses | hit ratio |
|---|---|---|---|
| C4 | ~515,000 | ~44,000 | **91.9%** |
| B4 | ~450 | ~6,800 | **5.9%** |
| Flask | — | — | **77.2%** |

At C4, **roughly 92% of requests never reached Postgres.** Say this before anyone
asks. The 18,764 figure is a number for a cached read path, and the honest
comparison for "how fast is Python at a database" is C3 — **5,982 req/s with
every single request hitting Postgres.**

### Peak Postgres connections per config

C0 42 · C1 31 · C2 21 · C3 21 · C4 21 · B1 31 · **B2 2** · B3 31 · B4 21 —
all against a stock `max_connections` of **100**.

---

## Experiment 2 — Flask on the same box (slide 9)

Same endpoint, same database, same dataset, same 200k rows, same random-id load,
same cores, same 10 s + 30 s × 3 protocol. Gunicorn + gevent, workers =
2 × 8 + 1 = **17**, connection pooling and Redis cache-aside equivalent to C4.

| stack | req/s (median of 3) | spread | p50 ms | p99 ms | failures | cache hit |
|---|---|---|---|---|---|---|
| FastAPI C4 (uvicorn, 8 workers) | **18,764.0** | 5.7% | 4.3 | 22.9 | 0 | 91.9% |
| Flask + Gunicorn + gevent (17 workers) | **9,024.6** | 3.8% | 7.1 | 49.2 | 0 | 77.2% |

**Ratio: 2.08×** in FastAPI's favour on throughput, **2.15×** on p99.

### Verdict on *"Same architecture, different syntax, same order of magnitude"*: **SUPPORTED**

2.08× is comfortably inside one order of magnitude. The slide stands.

But "different syntax" undersells it. A consistent **2× on throughput and 2× on
p99** is not nothing — it is the difference between four boxes and eight. The
honest version of the slide is stronger than the tidy one: *"same order of
magnitude — FastAPI came out about twice as fast, which matters at the margin
and does not matter at all compared to the 7× you get from fixing your driver."*

That framing also lands the talk's actual thesis: the framework gap (2×) is
**dwarfed** by the architecture gap (80×).

### A failure worth reporting: the first Flask run returned 500s

The first attempt scored 7,425–7,942 req/s but returned **1,291–9,868 HTTP 500s
per run (0.5–4.4%)**. Cause, from the logs
([`results/raw_flask_poolerror/`](results/raw_flask_poolerror/)):

```
psycopg2.pool.PoolError: connection pool exhausted
```

asyncpg's pool **blocks** when every connection is checked out. psycopg2's
`ThreadedConnectionPool` **raises**. So the first run was not C4-equivalent at
all — it was C4 with a fail-fast pool, and it was converting load into errors
rather than into queueing. Fixed by bounding pool access with a
`gevent.lock.BoundedSemaphore` so greenlets wait
([`app/flask_app.py`](app/flask_app.py)); the corrected run is the one in the
table, at 9,025 req/s with **zero** failures.

Worth a sentence on stage. It is a concrete example of "same architecture" being
harder than it sounds, and of why a config that "did 7,900 req/s" while erroring
on 4% of them did not do 7,900 req/s.

---

## Experiment 3 — the GIL (slide 7)

Thread counts 1/2/4/8/16. I/O-bound = each thread owns a TCP connection to a
local server that replies after a fixed 5 ms delay, and does **real blocking
socket reads** (not `time.sleep`). CPU-bound = pure-Python integer loop, no C
library that could drop the lock. Server runs in its own process on the
generator cores.

| threads | I/O req/s (3.14) | I/O req/s (3.14t) | CPU ops/s (3.14) | CPU ops/s (3.14t) |
|---|---|---|---|---|
| 1 | 192 | 193 | 4,451,219 | 4,025,908 |
| 2 | 384 | 383 | 4,414,991 | 8,017,839 |
| 4 | 763 | 760 | 4,406,432 | 15,631,507 |
| 8 | 1,496 | 1,456 | 4,067,436 | 21,860,175 |
| 16 | **2,772** | **2,781** | **4,214,068** | **21,411,966** |

(3.13.15 measured too, and tracks 3.14 closely: [`results/gil/`](results/gil/).)

### Verdict on *"It is released around I/O"*: **SUPPORTED**

I/O throughput scales **14.47× from 1 to 16 threads** — 90% of perfect linear.
Over the same range CPU-bound work scales **0.95×**: dead flat, and very
slightly negative from contention. Two curves, same interpreter, same run. That
divergence *is* the proof, and it is the single best slide in the deck to put a
chart on.

### Verdict on *"free-threading barely helps I/O-bound web work"*: **SUPPORTED, emphatically**

At 16 threads, free-threaded 3.14t delivers **+0.3%** on the I/O workload —
2,781 vs 2,772 req/s. Noise. Meanwhile the same build takes CPU-bound work from
4.03M to **21.86M ops/s at 8 threads, a 5.43× speed-up** on 6 physical cores.

Free-threading is transformative for the workload most web services do not have,
and irrelevant for the one they do. **The GIL was never what constrained
I/O-bound web work, and removing it does not speed that work up — because it was
already being released.**

### Single-threaded overhead of the free-threaded build

| build | single-thread CPU ops/s | vs stock 3.14 |
|---|---|---|
| 3.14 stock | 4,444,600 | — |
| 3.14t free-threaded | 4,096,200 | **−7.8%** |
| 3.13 stock | 4,530,200 | +1.9% |

**−7.8%**, inside the ≤15% that PEP 779 sets as its acceptance criterion. Safe to
state as "under 10% on this hardware" — and note it is one microbenchmark on one
machine, not a general figure.

---

## Experiment 4 — fact checks

Full table with quotes, URLs and dates: **[`results/factchecks.md`](results/factchecks.md)**.

| # | claim | slide | verdict |
|---|---|---|---|
| 1 | Postgres defaults to `max_connections = 100` | 5 | **SUPPORTED** (docs + measured: stock PG 17.11 returns 100) |
| 2 | Free-threading officially supported in 3.14 under PEP 779 | 7 | **SUPPORTED**, with a caveat — "supported" is Phase II, still **not the default build** |
| 3 | Instagram: ~2 billion monthly users | 7, 8 | **NOT SUPPORTED — stale.** It is **3 billion**, announced 2025-09-24 |
| 3b | …served by Django | 7, 8 | **SUPPORTED** (Meta Engineering; CinderX README, present tense) |
| 4 | Instagram runs **hundreds** of processes per host | 8 | **NOT SUPPORTED.** The primary source says **dozens** |
| 5 | Instagram runs a forked CPython | 8 | **SUPPORTED** (Cinder README: *"Cinder is powering Instagram"*) |
| 6 | `workers = 2 × cores + 1` | 9, 5 | **SUPPORTED as a starting point** — Gunicorn's own docs call it "not overly scientific" and "a guess" |
| 7 | Flask async views since 2.0 | 9 | **SUPPORTED** (CHANGES.rst; 2.0.0 released 2021-05-11) |

---

## Experiment 5 — the inference endpoint (slide 4)

**NOT RUN — awaiting approval.** The brief requires human approval before
deploying anything, so the change is proposed and stopped there.

Full proposal, ready to apply: **[`experiment5/`](experiment5/)** —
`proposed.diff` (verified with `git apply --check`, a dry run that writes
nothing), `request_timing.py`, `analyse_timing.py`, and a README.
**No file in `Path/Question-Generation-Service` has been modified.**

Two things found while writing it, both of which would have produced a
confidently wrong slide:

1. **The instrumentation sketch in the brief reports `0.0 ms` against this
   codebase.** It rebinds a `ContextVar` inside `track_model_call()`, but both
   outbound call sites dispatch through `loop.run_in_executor(...)`
   (`utils.py:817`, `utils.py:883`) — a thread whose context is not the
   request's — and the endpoints fan out with `asyncio.gather` (`app.py:334`,
   `app.py:408`), where each child task gets a copy-on-write context snapshot.
   A `.set()` in either place never reaches the middleware. The fix is to bind
   once per request to a **mutable** accumulator and mutate it in place.
2. **There is a third bucket.** Both call sites sit inside `async with sem:`, a
   per-model semaphore. That wait is neither Python work nor model latency, and
   folding it into either one skews slide 4 in a direction you cannot defend.
   It is recorded separately as `queue_ms`.

| figure | value |
|---|---|
| p99 `python_ms` | **NOT MEASURED — awaiting approval** |
| p99 `model_ms` | **NOT MEASURED — awaiting approval** |
| model share of p99 | **NOT MEASURED — awaiting approval** |

**On the arithmetic**, since someone will ask: percentiles do not decompose
additively — `p99(total) ≠ p99(model) + p99(python)`. The request that is 99th
percentile overall is usually not the one with the 99th-percentile Python time.
`analyse_timing.py` reports both the separate percentiles, labelled as such,
**and** the p99 of the per-request ratio `model_ms / total_ms`. Use the ratio for
slide 4's `[ __ % ]`: it is the only one of the three that is a single
well-defined quantity.

---

## SLIDE FILL-IN

### Slide 4
```
[ __ ms ] in your Python          = NOT MEASURED — Experiment 5 awaits approval
[ __ ms ] waiting on the model    = NOT MEASURED — Experiment 5 awaits approval
[ __ %  ] you cannot compile away = NOT MEASURED — Experiment 5 awaits approval
```

### Slide 5 — multiplier next to each of the four changes
```
Async database driver          7.2x
Deliberate worker count        1.6x
Cache the hot read path        1.0x     <- alone. 92% hit rate and huge, in combination.
A pooler in front of Postgres  0.3x     <- alone it is 3.3x SLOWER. See below.
```
The pooler and cache numbers need the slide reworded — see the next section.

### Slide 6
```
BEFORE   [ 233   ] requests/second      [ 606 ] ms p99
AFTER    [ 18,764 ] requests/second     [  23 ] ms p99
```
80.6× throughput, 26× p99. Two honest asterisks to say out loud:
**92% of AFTER's requests were served from Redis**, and **AFTER is at 94% of what
this harness can generate**, so it is a floor. If you want a number where every
request hits Postgres, it is **C3: 5,982 req/s at 32 ms p99** — still 25.7×.

### Slide 9
```
FastAPI C4  18,764 req/s   p99 22.9 ms
Flask       9,025 req/s    p99 49.2 ms
Ratio       2.08x — same order of magnitude: SUPPORTED
```

### Slide 3 sanity check — the talk's own arithmetic, verified
Slide 3 lands on **2,900 req/s at peak** for one million users. Against that:

* **C0, the naive version, does 233 req/s.** 8% of the target. The panic is not
  baseless — the *default* really is too slow.
* **C1 — one async driver change, still on ONE worker — does 1,737 req/s.** 60%
  of the target, from a one-line driver swap.
* **C3 does 5,982 req/s with every request hitting Postgres.** **2.06× the entire
  peak load of a million-user product**, on one 8-vCPU box, no cache.

That is the whole talk in three numbers, and it is now measured.

---

## Claims the measurements do NOT support

### 1. Slide 5 — "A pooler in front of Postgres" is not a throughput win
**Measured: 0.30× applied alone — it made the baseline 3.3× slower.** A pooler
in front of an app that opens a connection per request adds a single-threaded
hop to the most expensive operation in the request. It only helps once the app
holds connections, and then what it buys is **tail latency and connection
control**, not throughput: C1 → C2 is +4.3% req/s but **p99 157 ms → 66 ms**.

*Suggested rewording:* **"A pooler in front of Postgres — for the tail, not the
throughput. PgBouncer cut our p99 by 58%. Add it after the async driver; on its
own, in front of connect-per-request code, it made things 3× worse."*

That is a better slide. It is specific, it is counter-intuitive, and it is the
one number in the deck an audience of backend engineers will remember.

### 2. Slide 5 — "your pods will find that ceiling" (max_connections = 100)
**Not observed.** Peak Postgres backends across every configuration: **42, 31,
21, 21, 21, 31, 2, 31, 21** — against a stock limit of 100. **Zero connection
errors in any run.** The naive baseline is CPU-bound on connection *setup* (72%
app CPU at 233 req/s), so requests queue on CPU long before they can exhaust the
connection limit. The brief predicted `max_connections` exhaustion too; on one
box it did not happen.

*The claim is still true of a fleet* — many pods × many workers is how you find
that ceiling — but this benchmark does not demonstrate it, and it is a single
box. *Suggested rewording:* **"...and once you are running thirty pods, they will
find that ceiling between them."* Say "pods", plural, and it is defensible.

### 3. Slide 5 — "Cache the hot read path" is not a standalone win
**Measured: 1.03× applied alone, with a 5.9% hit ratio.** In C4 the same cache
carries 91.9% of traffic. Caching pays when you are fast enough to revisit a key
inside its lifetime. Keep the bullet — it is the largest single contributor in
the cumulative stack (C3 → C4 is 3.1×) — but it belongs **last**, and the slide
should say so.

### 4. Slide 6 — "AFTER" is a floor, not a ceiling
C4's 18,764 req/s is **93.8% of this harness's 20,001 req/s no-work ceiling**.
The measurement is bounded by the generator and by FastAPI's own per-request
overhead, not by the application. Present it as "over 18,000 req/s" and say the
harness ran out of room, or re-run on two hosts.

### 5. Slides 7 and 8 — "2 billion monthly users"
**Stale.** Instagram announced **3 billion** monthly active users on
**2025-09-24** (Zuckerberg on Threads, Mosseri on Instagram). The 2 billion
figure is from an October 2022 earnings call. Free upgrade: the number went up
and the citation is more recent than the deck.

### 6. Slide 8 — "Hundreds of processes per host"
**Not supported by any public source.** The primary source — Instagram
Engineering, *Dismissing Python Garbage Collection at Instagram* (2017-01-17) —
says: *"a master process that forks itself to create **dozens of worker
processes**."* No public source gives a per-host figure in the hundreds, and that
one is nine years old.

*Suggested rewording:* **"uWSGI pre-fork, dozens of worker processes per host."**
Then it is a quote, not a guess.

### 7. Slide 7 — "Free-threading landed in 3.14. PEP 779, officially supported"
**True but incomplete in a way that invites a correction from the audience.**
PEP 779 grants *Phase II* status: officially supported, **still not the default
build**. Phase III — free-threading as the default — is explicitly out of scope
and unscheduled. Add "supported, not default" and the sentence is bulletproof.
The measured **−7.8% single-threaded cost** is a good number to have ready.

### 8. Slides 5 and 9 — "2 × cores + 1" presented as a rule
Gunicorn's own docs call it *"not overly scientific"*, *"a guess"*, and say to
*"start with the above guess and tune"*. Presenting it as a rule contradicts
slide 10's own item 5 ("Set worker count deliberately. Not by default. Measure,
then choose."). Quote Gunicorn's hedge and the two slides agree.

### 9. Slide 9 — "different syntax" understates a real 2× gap
"Same order of magnitude" is **supported** and should stay. But FastAPI came out
**2.08× faster on throughput and 2.15× better at p99**. Saying so is stronger
than glossing it, because the point survives: a 2× framework gap next to an 80×
architecture gap is exactly the argument.

---

## Which configurations are safe to run live on stage

The demo plan is three escalating runs, C0 → C2 → C4.

| config | verdict | notes |
|---|---|---|
| **C0** | **Safe.** | Spread 1.5%, the most reproducible config measured. Ready ~1 s after launch. No cache, no warm-up dependency. 233 req/s is slow enough to *look* slow, which is the point. |
| **C2** | **Safe.** | Spread 3.3%. One worker, ready ~1 s after launch. Its real story is p99 (66 ms vs C1's 157 ms), so put the p99 on screen, not just req/s. |
| **C4** | **Safe, with one hard requirement.** | Spread 5.7%. **The 10 s warm-up is not optional** — it is what takes the cache from cold to 92%. This is measured, not theoretical: a short-window pass (3 s warm-up, 6 s measured) on the same box and the same code gave **7,936 req/s at a 35.0% hit ratio** — 42% of the full-protocol number. Cut the warm-up and you will have to explain live why the box does 8,000 when the slide says 18,764. `run_bench.sh` flushes Redis and warms up automatically; do not "save time" by skipping it. |
| C3 | Usable, slightly noisy. | Spread 7.1%, the highest measured. Fine as a talking point; if you need a live number, prefer C2 or C4. |
| **B2** | **Do not demo live.** | 69.6 req/s means a 30 s window produces under 2,100 requests, and a stage audience will read a slow demo as a broken demo. It is the most interesting result in the deck — show the recorded number and the explanation instead. |
| **Flask** | **Safe only with the current code.** | Came up in ~1 s, same as uvicorn. Without the `BoundedSemaphore` fix in `app/flask_app.py` it returns 500s under burst — that fix is what makes it C4-equivalent, do not revert it. Its cache reached only 77.2% against C4's 91.9%, so a side-by-side is not quite like-for-like on hit rate; mention it or run Flask longer. |

**Stage timing:** each config is 10 s warm-up + 30 s measured + ~3 s
start/stop ≈ 43 s (measured: every stack was serving within 1–2 s of launch).
Three configs ≈ **2 min 10 s** plus narration.

**Two rehearsal notes.** Run `make smoke` on the actual stage box the morning of
the talk — it caught two harness bugs here in under two minutes (a shell brace
expansion that made every request a 422 without failing, and a `oha` flag that
silently wrote no output). And if the
stage box is a real 8-vCPU cloud VM with mitigations **on** and a real NIC,
expect **lower absolute numbers than this report across the board**; the
multipliers should hold, the raw req/s will not. Re-run and re-cut slide 6 on
the actual hardware rather than letting the demo contradict the deck in front of
the room.

---

## Reproduction

```bash
git clone <repo> && cd python-at-scale-bench
make setup     # root: Postgres 17, PgBouncer, Redis 7, oha, Python toolchains
make up        # start the data tier, pinned to APP_CORES
make seed      # 200,000 rows, ~228 MB
make smoke     # 2-minute sanity pass — run this first on any new host
make bench     # Experiment 1 (9 configs x 3 reps) + Experiment 2 + collect
make gil       # Experiment 3
bash bench/ceiling.sh   # harness ceiling
```

Different hardware: `APP_CORES=0-3 GEN_CORES=4-5 make bench`.

With a Docker daemon, `docker-compose.yml` brings up the same stack; set
`APP_CPUSET` to pin it and override `PG_PUBLISH` / `APP_PUBLISH` if those host
ports are taken. The compose path was verified end to end (both the C0 and the
async + PgBouncer + Redis paths) but **did not produce these numbers** — the
measurement host had no Docker daemon. See [README.md](README.md).

### What is in `results/`

| path | contents |
|---|---|
| `raw/*.oha.json` | every `oha` run, verbatim |
| `raw/*.monitor.json` | per-run CPU, Postgres connections, Redis hits/misses, keep-alive connection count |
| `raw_flask_poolerror/` | the failed first Flask run, kept as evidence |
| `logs/` | full command log for every configuration, including pre-flight probes |
| `tables.md`, `summary.json` | aggregated |
| `ceiling.json` | harness ceiling runs |
| `gil/` | Experiment 3, per interpreter |
| `factchecks.md` | Experiment 4, with quotes and URLs |
| `environment.txt`, `dataset.json`, `idle_before.json`, `idle_after.json` | environment record |
