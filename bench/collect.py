"""
Aggregate results/raw/*.oha.json + *.monitor.json into summary.json and the
markdown tables that go into RESULTS.md.

Median of the three repetitions is the reported figure. Spread is reported as
(max - min) / median, and flagged when it exceeds 15% (ground rule 4).
"""

import glob
import json
import os
import re
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "results", "raw")

ORDER = ["C0", "C1", "C2", "C3", "C4", "B1", "B2", "B3", "B4", "FLASK"]
LABEL = {
    "C0": "C0 baseline (sync, 1 worker, no pool, no cache)",
    "C1": "C1 = C0 + async driver (asyncpg + pool)",
    "C2": "C2 = C1 + PgBouncer",
    "C3": "C3 = C2 + 8 uvicorn workers",
    "C4": "C4 = C3 + Redis cache-aside",
    "B1": "B1 = C0 + async driver only",
    "B2": "B2 = C0 + PgBouncer only",
    "B3": "B3 = C0 + 8 workers only",
    "B4": "B4 = C0 + Redis cache-aside only",
    "FLASK": "Flask + Gunicorn + gevent (C4-equivalent)",
}


def pct(o, key):
    p = o.get("latencyPercentiles") or {}
    for k in (key, key.replace("p", "p"), key + "0"):
        if k in p:
            return p[k] * 1000.0
    return None


def load():
    out = {}
    for f in sorted(glob.glob(os.path.join(RAW, "*.oha.json"))):
        m = re.match(r"(.+)_rep(\d+)\.oha\.json$", os.path.basename(f))
        if not m:
            continue
        cfg, rep = m.group(1), int(m.group(2))
        try:
            o = json.load(open(f))
        except Exception as e:                                   # noqa: BLE001
            print(f"WARN unreadable {f}: {e}", file=sys.stderr)
            continue
        mon_f = f.replace(".oha.json", ".monitor.json")
        mon = {}
        if os.path.exists(mon_f):
            try:
                mon = json.load(open(mon_f))
            except Exception:                                    # noqa: BLE001
                pass
        codes = {str(k): v for k, v in (o.get("statusCodeDistribution") or {}).items()}
        errs = o.get("errorDistribution") or {}
        n_ok = sum(v for k, v in codes.items() if k.startswith("2"))
        n_bad = sum(v for k, v in codes.items() if not k.startswith("2"))
        # oha aborts whatever is still in flight when -z expires. That is one
        # abort per connection, every run, and it is a property of the stopwatch,
        # not of the server. Counted separately so it can neither be mistaken for
        # a server failure nor hide one.
        deadline = sum(v for k, v in errs.items() if "deadline" in str(k).lower()) \
            if isinstance(errs, dict) else 0
        real_errs = {k: v for k, v in errs.items() if "deadline" not in str(k).lower()} \
            if isinstance(errs, dict) else {}
        n_err = sum(real_errs.values())
        total = n_ok + n_bad + n_err
        out.setdefault(cfg, []).append({
            "rep": rep, "file": os.path.basename(f),
            "rps": (o.get("summary") or {}).get("requestsPerSec"),
            "p50": pct(o, "p50"), "p95": pct(o, "p95"),
            "p99": pct(o, "p99"), "p999": pct(o, "p99.9"),
            "max_ms": (o.get("summary") or {}).get("slowest", 0) * 1000.0,
            "codes": codes, "errors": real_errs, "deadline_aborts": deadline,
            "ok": n_ok, "non2xx": n_bad, "err": n_err, "total": total,
            "err_pct": round(100.0 * (n_bad + n_err) / total, 2) if total else None,
            "monitor": mon,
        })
    return out


def agg(reps):
    def med(k):
        vals = [r[k] for r in reps if r.get(k) is not None]
        return st.median(vals) if vals else None

    rps = [r["rps"] for r in reps if r.get("rps") is not None]
    spread = None
    if len(rps) >= 2 and st.median(rps):
        spread = 100.0 * (max(rps) - min(rps)) / st.median(rps)
    mons = [r["monitor"] for r in reps if r.get("monitor")]

    def mon_med(path, default=None):
        vals = []
        for m in mons:
            cur = m
            for p in path:
                cur = (cur or {}).get(p) if isinstance(cur, dict) else None
            if isinstance(cur, (int, float)):
                vals.append(cur)
        return st.median(vals) if vals else default

    codes = {}
    for r in reps:
        for k, v in r["codes"].items():
            codes[k] = codes.get(k, 0) + v
    errs = {}
    for r in reps:
        for k, v in (r["errors"] or {}).items():
            errs[k] = errs.get(k, 0) + v

    return {
        "n_reps": len(reps),
        "rps_median": med("rps"), "rps_all": rps,
        "spread_pct": spread, "spread_flag": bool(spread and spread > 15.0),
        "p50": med("p50"), "p95": med("p95"), "p99": med("p99"),
        "p999": med("p999"), "max_ms": med("max_ms"),
        "codes_total": codes, "errors_total": errs,
        "err_pct_median": med("err_pct"),
        "ok_total": sum(r["ok"] for r in reps),
        "bad_total": sum(r["non2xx"] + r["err"] for r in reps),
        "deadline_aborts": sum(r.get("deadline_aborts", 0) for r in reps),
        "http_estab_avg": mon_med(["http_established", "avg"]),
        "http_estab_max": mon_med(["http_established", "max"]),
        "app_cpu_avg": mon_med(["app_cpu_busy_pct", "avg"]),
        "app_cpu_max": mon_med(["app_cpu_busy_pct", "max"]),
        "gen_cpu_pct_of_cores": mon_med(["loadgen_proc_cpu_pct_of_its_cores"]),
        "pg_conn_max": mon_med(["pg_connections", "max"]),
        "redis_hit_ratio": mon_med(["redis", "hit_ratio"]),
        "redis_hits": mon_med(["redis", "hits"]),
        "redis_misses": mon_med(["redis", "misses"]),
    }


def f(x, n=1):
    return "NOT MEASURED" if x is None else f"{x:,.{n}f}"


def main():
    data = load()
    if not data:
        print("no raw results found in " + RAW, file=sys.stderr)
        sys.exit(1)
    summ = {c: agg(r) for c, r in data.items()}
    keys = [c for c in ORDER if c in summ] + [c for c in summ if c not in ORDER]

    with open(os.path.join(ROOT, "results", "summary.json"), "w") as fh:
        json.dump({"per_config": summ, "raw": data}, fh, indent=2)

    lines = []
    lines.append("### All configurations (median of %d reps)\n" % max(
        s["n_reps"] for s in summ.values()))
    lines.append("| config | req/s | spread | p50 ms | p95 ms | p99 ms | p99.9 ms | max ms "
                 "| failures | app CPU% | gen CPU% | keep-alive conns | peak pg conns "
                 "| cache hit |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in keys:
        s = summ[c]
        sp = "NOT MEASURED" if s["spread_pct"] is None else \
            f"{s['spread_pct']:.1f}%" + (" ⚠" if s["spread_flag"] else "")
        hr = "n/a" if s["redis_hit_ratio"] is None else f"{100*s['redis_hit_ratio']:.1f}%"
        lines.append(
            f"| {c} | {f(s['rps_median'])} | {sp} | {f(s['p50'])} | {f(s['p95'])} | "
            f"{f(s['p99'])} | {f(s['p999'])} | {f(s['max_ms'])} | "
            f"{s['bad_total']} ({f(s['err_pct_median'],2)}%) | {f(s['app_cpu_avg'])} | "
            f"{f(s['gen_cpu_pct_of_cores'])} | {f(s['http_estab_avg'],0)} | "
            f"{f(s['pg_conn_max'],0)} | {hr} |")

    base = summ.get("C0", {}).get("rps_median")
    lines.append("\n### Set B — one change at a time, vs C0\n")
    lines.append("| change | config | req/s | multiplier vs C0 | p99 ms | non-2xx+err |")
    lines.append("|---|---|---|---|---|---|")
    for c in ["B1", "B2", "B3", "B4"]:
        if c not in summ:
            continue
        s = summ[c]
        mult = "NOT MEASURED" if not (base and s["rps_median"]) else \
            f"{s['rps_median']/base:.2f}x"
        lines.append(f"| {LABEL[c]} | {c} | {f(s['rps_median'])} | {mult} | "
                     f"{f(s['p99'])} | {s['bad_total']} |")

    lines.append("\n### Status-code / error breakdown (summed over reps)\n")
    lines.append("`deadline aborts` = requests still in flight when the 30s window "
                 "closed. One per connection per rep; a stopwatch artefact, not a "
                 "server failure. Excluded from the failure column above.\n")
    lines.append("| config | codes | real errors | deadline aborts |")
    lines.append("|---|---|---|---|")
    for c in keys:
        s = summ[c]
        lines.append(f"| {c} | `{json.dumps(s['codes_total'])}` | "
                     f"`{json.dumps(s['errors_total'])}` | {s['deadline_aborts']} |")

    md = "\n".join(lines)
    with open(os.path.join(ROOT, "results", "tables.md"), "w") as fh:
        fh.write(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
