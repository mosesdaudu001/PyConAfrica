"""
Experiment 3 — is the GIL released around I/O, and does free-threading help
I/O-bound web work?

Two microbenchmarks at thread counts 1, 2, 4, 8, 16:

  io   each thread owns a TCP connection to delay_server.py and does blocking
       send/recv round trips. A real blocking socket read, not time.sleep().
       If the GIL is released around I/O this scales close to linearly.

  cpu  each thread runs a pure-Python integer loop. On a stock build this is
       serialised by the GIL and should stay flat.

Also reports a single-threaded, no-threads-created baseline for the CPU loop,
which is what the free-threaded build's single-thread overhead is measured from.

Run the same file under python3.14 and python3.14t and diff the tables.
"""

import argparse
import json
import os
import platform
import socket
import struct
import sys
import threading
import time

PORT = int(os.environ.get("GIL_PORT", "9911"))


def cpu_work(deadline: float, counter: list, idx: int) -> None:
    """Pure-Python integer work. No C library that could drop the GIL."""
    n = 0
    x = 1
    while time.monotonic() < deadline:
        # a fixed 1000-iteration chunk, so the clock check is amortised
        for i in range(1000):
            x = (x * 1103515245 + 12345) & 0x7FFFFFFF
            x ^= (x >> 7)
        n += 1000
    counter[idx] = n


def io_work(deadline: float, counter: list, idx: int) -> None:
    s = socket.create_connection(("127.0.0.1", PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    payload = struct.pack("!q", idx)
    n = 0
    try:
        while time.monotonic() < deadline:
            s.sendall(payload)
            got = b""
            while len(got) < 8:
                chunk = s.recv(8 - len(got))   # real blocking read
                if not chunk:
                    raise ConnectionError("server closed")
                got += chunk
            n += 1
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    counter[idx] = n


def run(kind: str, threads: int, seconds: float) -> float:
    fn = cpu_work if kind == "cpu" else io_work
    counter = [0] * threads
    deadline = time.monotonic() + seconds
    ts = [threading.Thread(target=fn, args=(deadline, counter, i)) for i in range(threads)]
    t0 = time.monotonic()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.monotonic() - t0
    return sum(counter) / wall


def single_thread_cpu(seconds: float) -> float:
    """No threading module involvement at all — the free-threaded build's
    single-threaded overhead is measured against this."""
    counter = [0]
    cpu_work(time.monotonic() + seconds, counter, 0)
    return counter[0] / seconds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--threads", default="1,2,4,8,16")
    ap.add_argument("--out", default="-")
    a = ap.parse_args()

    counts = [int(x) for x in a.threads.split(",")]
    gil_enabled = None
    try:
        gil_enabled = sys._is_gil_enabled()          # 3.13+ on free-threaded builds
    except AttributeError:
        gil_enabled = True

    res = {
        "python": sys.version,
        "version_info": list(sys.version_info[:3]),
        "free_threaded_build": bool(getattr(sys, "_is_gil_enabled", None)) and not gil_enabled,
        "gil_enabled": gil_enabled,
        "implementation": platform.python_implementation(),
        "seconds_per_point": a.seconds,
        "delay_server_port": PORT,
        "single_thread_cpu_ops_per_s": None,
        "io": {}, "cpu": {},
    }

    res["single_thread_cpu_ops_per_s"] = round(single_thread_cpu(a.seconds), 1)
    print(f"single-thread cpu: {res['single_thread_cpu_ops_per_s']:,.0f} ops/s", flush=True)

    for kind in ("io", "cpu"):
        for n in counts:
            r = run(kind, n, a.seconds)
            res[kind][str(n)] = round(r, 1)
            unit = "req/s" if kind == "io" else "ops/s"
            print(f"{kind:>3} threads={n:>2}: {r:>14,.1f} {unit}", flush=True)

    out = json.dumps(res, indent=2)
    if a.out == "-":
        print(out)
    else:
        with open(a.out, "w") as f:
            f.write(out)


if __name__ == "__main__":
    main()
