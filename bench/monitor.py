"""
Sample the host while a load run is in flight.

Runs for --duration seconds, sampling every 250 ms, and prints a JSON summary:

  * per-core CPU busy% for the app-tier cores and the load-generator cores,
    computed from /proc/stat deltas (so it is real busy time, not a load average)
  * Postgres backend count from pg_stat_activity  -> "peak connections"
  * Redis keyspace_hits / keyspace_misses deltas   -> cache hit ratio
  * the load generator's own CPU%, to prove it was not the bottleneck (rule 7)

The monitor holds exactly one Postgres connection, opened before the measured
window and held for every configuration alike, so it never differs between runs.
"""

import argparse
import json
import os
import sys
import time


def read_stat(cores):
    out = {}
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            name = parts[0]
            if name == "cpu" or not name[3:].isdigit():
                continue
            n = int(name[3:])
            if n not in cores:
                continue
            vals = [int(v) for v in parts[1:]]
            idle = vals[3] + vals[4]          # idle + iowait
            total = sum(vals)
            out[n] = (total, idle)
    return out


def busy_pct(a, b, cores):
    tot = idl = 0
    for c in cores:
        if c in a and c in b:
            tot += b[c][0] - a[c][0]
            idl += b[c][1] - a[c][1]
    return 0.0 if tot <= 0 else 100.0 * (tot - idl) / tot


def proc_cpu_ticks(names):
    """{pid: utime+stime jiffies} for every process whose comm matches.

    Returned per-pid, not summed: the warm-up generator and the measured
    generator are different processes with the same comm, and summing across
    them produced negative deltas when the warm-up process exited mid-window.
    """
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                s = f.read()
            comm = s[s.index("(") + 1:s.rindex(")")]
            if comm not in names:
                continue
            rest = s[s.rindex(")") + 2:].split()
            out[int(pid)] = int(rest[11]) + int(rest[12])   # utime, stime
        except (OSError, ValueError):
            continue
    return out


class TickAccumulator:
    """CPU consumed by short-lived processes during the window.

    Sampling only at the two ends reads zero when the generator both starts and
    exits inside the window (which is exactly what happens: oha and the monitor
    are given the same duration). So every sample updates a first-seen and a
    last-seen tick count per pid, and the total is the sum of per-pid growth.
    Correct whether the process spans the window, starts inside it, or ends
    inside it."""

    def __init__(self):
        self.first = {}
        self.last = {}

    def sample(self, snapshot):
        for pid, ticks in snapshot.items():
            self.first.setdefault(pid, ticks)
            self.last[pid] = ticks

    def total(self):
        return sum(self.last[p] - self.first[p] for p in self.last)


def established_to_port(port):
    """Count ESTABLISHED TCP sockets whose local end is `port`. With keep-alive
    on this sits at ~the configured concurrency; without it, it churns far
    higher. Proves keep-alive rather than assuming it."""
    want = f"{port:04X}"
    n = 0
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    if parts[3] != "01":            # 01 = ESTABLISHED
                        continue
                    if parts[1].rsplit(":", 1)[-1] == want:
                        n += 1
        except OSError:
            pass
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--app-cores", default="0-7")
    ap.add_argument("--gen-cores", default="8-11")
    ap.add_argument("--dsn", default="postgresql://bench:bench@127.0.0.1:5432/bench")
    ap.add_argument("--redis", default="")
    ap.add_argument("--gen-proc", default="oha")
    ap.add_argument("--app-port", type=int, default=8000)
    ap.add_argument("--out", default="-")
    a = ap.parse_args()

    def parse(spec):
        s = set()
        for part in spec.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                s.update(range(int(lo), int(hi) + 1))
            elif part:
                s.add(int(part))
        return s

    app_cores, gen_cores = parse(a.app_cores), parse(a.gen_cores)
    hz = os.sysconf("SC_CLK_TCK")

    conn = cur = None
    try:
        import psycopg
        conn = psycopg.connect(a.dsn, autocommit=True, connect_timeout=5)
        cur = conn.cursor()
    except Exception as e:                                    # noqa: BLE001
        print(f"monitor: no pg connection ({e})", file=sys.stderr)

    r = None
    if a.redis:
        try:
            import redis
            host, _, port = a.redis.partition(":")
            r = redis.Redis(host=host or "127.0.0.1", port=int(port or 6379))
            r.ping()
        except Exception as e:                                # noqa: BLE001
            print(f"monitor: no redis ({e})", file=sys.stderr)
            r = None

    def redis_stats():
        if r is None:
            return None
        i = r.info("stats")
        return i.get("keyspace_hits", 0), i.get("keyspace_misses", 0)

    conns = []
    estab = []
    prev = read_stat(app_cores | gen_cores)
    app_series, gen_series = [], []
    gen_acc = TickAccumulator()
    gen_acc.sample(proc_cpu_ticks({a.gen_proc}))
    rs_start = redis_stats()

    t0 = time.monotonic()
    t_end = t0 + a.duration
    while time.monotonic() < t_end:
        time.sleep(0.25)
        cur_stat = read_stat(app_cores | gen_cores)
        app_series.append(busy_pct(prev, cur_stat, app_cores))
        gen_series.append(busy_pct(prev, cur_stat, gen_cores))
        prev = cur_stat
        gen_acc.sample(proc_cpu_ticks({a.gen_proc}))
        estab.append(established_to_port(a.app_port))
        if cur is not None:
            try:
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database()"
                )
                conns.append(cur.fetchone()[0])
            except Exception:                                 # noqa: BLE001
                pass

    wall = time.monotonic() - t0
    gen_acc.sample(proc_cpu_ticks({a.gen_proc}))
    gen_ticks = gen_acc.total()
    rs_end = redis_stats()

    def stat(xs):
        if not xs:
            return None
        s = sorted(xs)
        return {"avg": round(sum(xs) / len(xs), 1), "max": round(s[-1], 1),
                "p95": round(s[int(0.95 * (len(s) - 1))], 1)}

    res = {
        "wall_s": round(wall, 2),
        "samples": len(app_series),
        "app_cores": sorted(app_cores),
        "gen_cores": sorted(gen_cores),
        "app_cpu_busy_pct": stat(app_series),
        "gen_cpu_busy_pct": stat(gen_series),
        # % of ONE core; divide by len(gen_cores) for share of the generator's budget
        "loadgen_proc_cpu_pct_of_one_core": round(100.0 * gen_ticks / hz / wall, 1)
        if wall > 0 else None,
        "loadgen_proc_cpu_pct_of_its_cores": round(
            100.0 * gen_ticks / hz / wall / max(1, len(gen_cores)), 1) if wall > 0 else None,
        "pg_connections": {"max": max(conns), "avg": round(sum(conns) / len(conns), 1),
                           "samples": len(conns)} if conns else None,
        "http_established": {"max": max(estab), "avg": round(sum(estab) / len(estab), 1)}
        if estab else None,
    }
    if rs_start and rs_end:
        h = rs_end[0] - rs_start[0]
        m = rs_end[1] - rs_start[1]
        res["redis"] = {"hits": h, "misses": m,
                        "hit_ratio": round(h / (h + m), 4) if (h + m) else None}
    out = json.dumps(res, indent=2)
    if a.out == "-":
        print(out)
    else:
        with open(a.out, "w") as f:
            f.write(out)


if __name__ == "__main__":
    main()
