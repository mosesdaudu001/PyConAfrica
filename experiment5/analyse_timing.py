"""
Turn the qgs.timing JSON-lines log into the three figures slide 4 needs.

Usage:  python analyse_timing.py qgs-timing.log [--min-requests 300]
"""
import argparse
import json
import statistics as st
import sys


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--min-requests", type=int, default=300)
    ap.add_argument("--path-prefix", default="/generate")
    a = ap.parse_args()

    rows = []
    with open(a.logfile) as f:
        for line in f:
            line = line.strip()
            i = line.find("{")
            if i < 0:
                continue
            try:
                d = json.loads(line[i:])
            except json.JSONDecodeError:
                continue
            if "total_ms" not in d or not str(d.get("path", "")).startswith(a.path_prefix):
                continue
            if d.get("status") != 200:
                continue
            rows.append(d)

    n = len(rows)
    print(f"requests parsed: {n}")
    if n < a.min_requests:
        print(f"NOT ENOUGH DATA — the brief requires at least {a.min_requests} "
              f"real requests. Do not put these numbers on a slide.", file=sys.stderr)

    total = [r["total_ms"] for r in rows]
    model = [r["model_ms"] for r in rows]
    py = [r["python_ms"] for r in rows]
    queue = [r.get("queue_ms", 0.0) for r in rows]
    ratio = [r["model_ms"] / r["total_ms"] for r in rows if r["total_ms"] > 0]

    print("\n-- separate percentiles of separate distributions --")
    print(f"{'':10}{'p50':>12}{'p99':>12}{'mean':>12}")
    for name, xs in (("total_ms", total), ("model_ms", model),
                     ("python_ms", py), ("queue_ms", queue)):
        print(f"{name:10}{pct(xs,0.50):>12.1f}{pct(xs,0.99):>12.1f}"
              f"{st.fmean(xs):>12.1f}")

    print("\n-- per-request ratio model_ms/total_ms (use THIS for slide 4's %) --")
    print(f"  p50 {100*pct(ratio,0.50):.1f}%   p99 {100*pct(ratio,0.99):.1f}%   "
          f"mean {100*st.fmean(ratio):.1f}%")

    print("\n-- SLIDE 4 --")
    print(f"  [ __ ms ] in your Python      = {pct(py,0.99):.0f} ms   (p99 of python_ms)")
    print(f"  [ __ ms ] waiting on the model = {pct(model,0.99):.0f} ms  (p99 of model_ms)")
    print(f"  [ __ % ]  you cannot compile away = {100*pct(ratio,0.99):.0f}%  "
          f"(p99 of the per-request ratio)")
    print("\n  NOTE: the first two are percentiles of different distributions and "
          "do not sum to p99(total_ms). The % is computed per-request, then "
          "percentiled — it is the only one of the three that is a single "
          "well-defined quantity.")


if __name__ == "__main__":
    main()
