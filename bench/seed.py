"""
Seed the exams table.

200,000 rows with a realistic shape: text columns plus a ~1.5 KB jsonb blob, so
the table is comfortably larger than Postgres' stock 128 MB shared_buffers and
the benchmark does real buffer-pool work instead of serving everything from RAM.

Ids run 1..200000. The load generator draws uniformly from 100000..199999 (see
bench/run_bench.sh), which is a 100,000-key working set.
"""

import io
import json
import os
import random
import sys

import psycopg

DSN = os.environ.get(
    "SEED_DSN", "postgresql://bench:bench@127.0.0.1:5432/bench"
)
N = int(os.environ.get("SEED_ROWS", "200000"))
random.seed(20261007)  # deterministic dataset; same rows on every host

SUBJECTS = ["mathematics", "physics", "chemistry", "biology", "english",
            "economics", "geography", "history", "civics", "literature"]
STATUSES = ["draft", "review", "published", "archived"]
WORDS = ("the quick brown fox jumps over a lazy dog while students revise for "
         "their examinations under timed conditions").split()

DDL = """
DROP TABLE IF EXISTS exams;
CREATE TABLE exams (
    id          bigint PRIMARY KEY,
    title       text        NOT NULL,
    subject     text        NOT NULL,
    status      text        NOT NULL,
    payload     jsonb       NOT NULL,
    created_at  timestamptz NOT NULL
);
"""


def make_payload(i: int) -> dict:
    rng = random.Random(i)
    return {
        "version": 3,
        "exam_code": f"EX-{i:07d}",
        "duration_minutes": rng.choice([30, 45, 60, 90, 120]),
        "sections": [
            {
                "name": f"Section {chr(65 + s)}",
                "marks": rng.randint(10, 40),
                "questions": [
                    {
                        "qid": f"{i}-{s}-{q}",
                        "stem": " ".join(rng.choice(WORDS) for _ in range(18)),
                        "options": [" ".join(rng.choice(WORDS) for _ in range(4))
                                    for _ in range(4)],
                        "answer": rng.randint(0, 3),
                        "difficulty": round(rng.uniform(0.1, 0.95), 3),
                    }
                    for q in range(3)
                ],
            }
            for s in range(2)
        ],
        "tags": [rng.choice(SUBJECTS) for _ in range(3)],
    }


def main() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        print(f"seeding {N} rows ...", file=sys.stderr)
        with conn.cursor() as cur:
            with cur.copy(
                "COPY exams (id, title, subject, status, payload, created_at) "
                "FROM STDIN"
            ) as cp:
                for i in range(1, N + 1):
                    rng = random.Random(i * 7919)
                    row = (
                        str(i),
                        f"{rng.choice(SUBJECTS).title()} Mock Exam {i}",
                        rng.choice(SUBJECTS),
                        rng.choice(STATUSES),
                        json.dumps(make_payload(i)),
                        f"2026-0{rng.randint(1,9)}-{rng.randint(10,28)} "
                        f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:00+00",
                    )
                    cp.write_row(row)
                    if i % 25000 == 0:
                        print(f"  {i}", file=sys.stderr)
        with conn.cursor() as cur:
            cur.execute("ANALYZE exams")
            cur.execute("SELECT count(*) FROM exams")
            n = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_total_relation_size('exams')), "
                        "pg_total_relation_size('exams')")
            pretty, raw = cur.fetchone()
            cur.execute("SHOW max_connections")
            mc = cur.fetchone()[0]
            cur.execute("SHOW shared_buffers")
            sb = cur.fetchone()[0]
            cur.execute("SELECT version()")
            ver = cur.fetchone()[0]
    print(json.dumps({
        "rows": n, "total_relation_size_pretty": pretty,
        "total_relation_size_bytes": raw, "max_connections": mc,
        "shared_buffers": sb, "server_version": ver,
    }, indent=2))


if __name__ == "__main__":
    main()
