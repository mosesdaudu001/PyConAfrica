# Experiment 5 — slide 4, the real inference endpoint

**Status: PROPOSED. NOT DEPLOYED. NOT RUN.**
No file in `Path/Question-Generation-Service` has been modified. `proposed.diff`
was verified with `git apply --check` (a dry run that writes nothing) and applies
cleanly to the current tree.

## The service

`Path/Question-Generation-Service` — FastAPI, four generation endpoints
(`/generate-questions-passage`, `/generate-questions-non-passage`,
`/generate-crq-set`, `/generate-pcr-set`), backed by a provider abstraction in
`llm_provider.py` (Gemini / Claude / Fable / OpenAI).

Every outbound model call funnels through exactly two `await` sites:

| file | line | call |
|---|---|---|
| `utils.py` | 817 | `run_in_executor(self.executor, self._provider.generate_content_sync, …)` |
| `utils.py` | 883 | `run_in_executor(self.executor, self._generate_content_raw_sync, …)` |

Two sites is the whole surface. That is why this is a small change.

## Two defects in the instrumentation sketch from the brief

The brief's snippet does `_model_ms.set(_model_ms.get() + delta)` inside a
`track_model_call()` wrapped around each call site. Against **this** codebase it
would report `model_ms = 0.0` on every request, for two independent reasons:

1. **The model call runs on an executor thread.** Both sites dispatch via
   `loop.run_in_executor(...)`. The callable executes on a thread whose context
   is not the request's context, so a `ContextVar.set()` performed inside it is
   never visible to middleware running on the event loop.

2. **The endpoints fan out with `asyncio.gather`** (`app.py:334`, `app.py:408`).
   Each child task receives a copy-on-write snapshot of the context. A `.set()`
   inside a child task does not propagate back to the parent, so the
   contributions of every concurrent model call are silently discarded.

Both failures are silent. The endpoint would return a confident `0.0 ms` and the
slide would claim 100% of p99 is Python — the exact opposite of the talk's point.

**The fix:** bind the ContextVar exactly once per request, in middleware, to a
*mutable* accumulator, and have every call site mutate it in place. Rebinding is
what child tasks and executor threads cannot propagate; mutating a shared object
they already hold a reference to works in both cases. See `request_timing.py`.

## A third bucket the brief does not account for

Both call sites sit inside `async with sem:` — a per-model semaphore the service
uses to stay inside provider quotas. Time spent waiting on that semaphore is
neither Python work nor model latency; it is the service throttling itself. Fold
it into `python_ms` and slide 4 overstates the part you "cannot compile away" in
the wrong direction; fold it into `model_ms` and it overstates the other. It is
recorded separately as `queue_ms`, and `python_ms = total - model - queue`.

## What it produces

One JSON line per request on the `qgs.timing` logger, plus `X-Total-Ms`,
`X-Model-Ms` and `X-Python-Ms` response headers:

```json
{"path":"/generate-questions-passage","status":200,"total_ms":8421.55,
 "model_ms":8102.31,"queue_ms":12.04,"python_ms":307.20,
 "model_calls":3,"model_share":0.9621}
```

## How to run it, once approved

1. Copy `request_timing.py` into the service directory.
2. `git apply experiment5/proposed.diff`
3. Deploy to **one** instance; the change is read-only apart from a log write.
4. Let it take **at least several hundred real requests** (the brief's bar).
5. `python analyse_timing.py qgs-timing.log` → the three slide-4 figures.

## The percentile caveat, stated plainly

Percentiles do not decompose additively: `p99(total) ≠ p99(model) + p99(python)`.
The request that is 99th-percentile slowest overall is usually not the one with
the 99th-percentile slowest Python time. `analyse_timing.py` therefore reports
**both**:

* the separate percentiles of each distribution, labelled as such, and
* `p99` of the **per-request ratio** `model_ms / total_ms`, which is a single
  well-defined number and the honest source for slide 4's `[ __ % ]`.

Use the ratio figure on the slide. Someone will ask.
