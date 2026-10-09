"""
Slide 4 instrumentation for the Question Generation Service — PROPOSED, NOT DEPLOYED.

Drop this file next to app.py. It splits each request's wall time into three
buckets and records them per request:

    model_ms  time awaited on an outbound LLM call (all attempts, all retries)
    queue_ms  time blocked on the service's own per-model semaphore
    python_ms everything else: validation, prompt assembly, schema coercion,
              LaTeX finalisation, JSON serialisation — the part slide 4 is about

Why not the contextvars sketch from the brief
---------------------------------------------
The brief's version does `_model_ms.set(_model_ms.get() + delta)`. Against THIS
codebase that reports 0.0 ms, for two independent reasons:

1. The LLM call is dispatched with `loop.run_in_executor(...)`
   (utils.py:817 and utils.py:883). The callable runs on an executor thread
   whose context is not the request's context, so a `.set()` performed there is
   never visible to middleware running on the event loop.

2. The endpoints fan out with `asyncio.gather(...)` (app.py:334, app.py:408).
   Each child task gets a copy-on-write snapshot of the context, so a `.set()`
   inside a child task does not propagate back to the parent either.

Rebinding a ContextVar is what breaks. Mutating an object the ContextVar already
points at does not: every child task and every thread that inherits the context
holds a reference to the SAME dict. So the var is bound exactly once per
request, in middleware, and every call site mutates in place.

Mutation happens only on the event-loop thread (the timers wrap the `await`,
not the executor body), so no lock is needed. `_Timing.add` is written to be
safe anyway in case a future call site times from inside a worker thread.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Iterator

_timing: contextvars.ContextVar["_Timing | None"] = contextvars.ContextVar(
    "qgs_timing", default=None
)


class _Timing:
    __slots__ = ("model_ms", "queue_ms", "model_calls", "_lock")

    def __init__(self) -> None:
        self.model_ms = 0.0
        self.queue_ms = 0.0
        self.model_calls = 0
        self._lock = threading.Lock()

    def add(self, field: str, ms: float, calls: int = 0) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + ms)
            self.model_calls += calls


def begin_request() -> _Timing:
    """Bind a fresh accumulator. Call once per request, in middleware."""
    t = _Timing()
    _timing.set(t)
    return t


@contextlib.contextmanager
def track_model_call() -> Iterator[None]:
    """Wrap the `await` on an outbound model call — NOT the sync body that runs
    on the executor thread."""
    t = _timing.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if t is not None:
            t.add("model_ms", (time.perf_counter() - start) * 1000.0, calls=1)


def note_queue_wait(start_perf: float) -> None:
    """Record time spent waiting to acquire the per-model semaphore.

    Called immediately after `async with sem:` is entered, with the perf_counter
    value taken just before it. Recorded separately because this time is neither
    Python work nor model latency — it is the service throttling itself — so
    slide 4 would overstate one of its two numbers if it were folded into
    either. Keeping `async with sem` untouched also means the change cannot
    alter the concurrency semantics of the service.
    """
    t = _timing.get()
    if t is not None:
        t.add("queue_ms", (time.perf_counter() - start_perf) * 1000.0)


def snapshot() -> tuple[float, float, int]:
    t = _timing.get()
    return (0.0, 0.0, 0) if t is None else (t.model_ms, t.queue_ms, t.model_calls)
