"""
Measure what the host is doing when the benchmark is NOT running.

The measurement host is a shared Colab runtime with the operator's own GPU jobs
on it. Those jobs are not pinned, so they can land on the app cores. This
records how much of the app-tier and generator-tier CPU is already spoken for
before any load is applied, so the app CPU figures in RESULTS.md can be read
against a known floor instead of an assumed one.
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from monitor import busy_pct, read_stat  # noqa: E402

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
app = set(range(0, 8))
gen = set(range(8, 12))

prev = read_stat(app | gen)
a_s, g_s = [], []
t_end = time.monotonic() + DUR
while time.monotonic() < t_end:
    time.sleep(0.25)
    cur = read_stat(app | gen)
    a_s.append(busy_pct(prev, cur, app))
    g_s.append(busy_pct(prev, cur, gen))
    prev = cur

top = subprocess.run(
    ["ps", "-eo", "pcpu,rss,comm", "--sort=-pcpu"],
    capture_output=True, text=True).stdout.splitlines()[:8]

def st(x):
    y = sorted(x)
    return {"avg": round(sum(x) / len(x), 1), "max": round(y[-1], 1),
            "p95": round(y[int(0.95 * (len(y) - 1))], 1)}

print(json.dumps({
    "duration_s": DUR,
    "idle_app_cores_busy_pct": st(a_s),
    "idle_gen_cores_busy_pct": st(g_s),
    "top_processes": [l.strip() for l in top],
}, indent=2))
