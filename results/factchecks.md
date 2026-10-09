# Experiment 4 — fact checks

All sources accessed 2026-08-26.

| # | claim | slide | verdict | source | source date |
|---|---|---|---|---|---|
| 1 | Postgres defaults to `max_connections = 100` | 5 | **SUPPORTED** | [PostgreSQL 17 docs, 19.3.1 Connection Settings](https://www.postgresql.org/docs/17/runtime-config-connection.html) — *"The default is typically 100 connections, but might be less if your kernel settings will not support it (as determined during initdb)."* Confirmed empirically: `SHOW max_connections` on the stock PostgreSQL 17.11 used here returns `100`. | docs current; measured 2026-08-26 |
| 2 | Free-threading officially supported in 3.14 under PEP 779 | 7 | **SUPPORTED, with a caveat worth saying out loud** | [PEP 779 — *Criteria for supported status for free-threaded Python*](https://peps.python.org/pep-0779/), Status: Final, Python-Version: 3.14 | resolved 2025-06-16 |
| 3 | Instagram: ~2 billion monthly users on Django | 7, 8 | **NOT SUPPORTED — the number is stale, and stale in the direction that weakens the talk** | 3 billion MAU announced by Mark Zuckerberg (Threads) and Adam Mosseri (Instagram): [TechCrunch](https://techcrunch.com/2025/09/24/instagram-now-has-3-billion-monthly-active-users-will-test-features-to-help-users-control-their-feeds/), [CNBC](https://www.cnbc.com/2025/09/24/instagram-now-has-3-billion-monthly-active-users.html). The 2 billion figure dates from an October 2022 earnings call. | 2025-09-24 |
| 3b | …*on Django* | 7, 8 | **SUPPORTED** | [Meta Engineering](https://engineering.fb.com/2022/05/02/open-source/cinder-jits-instagram/) — *"Since Instagram runs one of the world's largest deployments of the Django web framework…"*; [CinderX README](https://github.com/facebookincubator/cinderx) — *"It is used in production at Meta for use-cases like the Instagram Django service."* | 2022-05-02; CinderX current |
| 4 | Instagram runs **hundreds** of processes per host | 8 | **NOT SUPPORTED — the primary source says *dozens***  | Instagram Engineering, *Dismissing Python Garbage Collection at Instagram* ([archived](http://web.archive.org/web/20230930224158/https://instagram-engineering.com/dismissing-python-garbage-collection-at-instagram-4dca40b29172)) — *"Instagram's web server runs on Django in a multi-process mode with a master process that forks itself to create **dozens of worker processes** that take incoming user requests. For the application server, we use uWSGI with pre-fork mode…"* No public source gives a per-host figure in the hundreds. | 2017-01-17 |
| 5 | Instagram runs a forked CPython | 8 | **SUPPORTED** | [Cinder README](https://github.com/facebookincubator/cinder/blob/cinder/3.8/README.rst) — *"Cinder is Meta's internal performance-oriented production version of CPython 3.8… Cinder is powering Instagram, where it started, and is increasingly used across more and more Python applications in Meta."* [CinderX](https://github.com/facebookincubator/cinderx) is the current, actively released successor. | Cinder open-sourced 2021-05; CinderX current |
| 6 | `workers = 2 × cores + 1` | 9, 5 | **SUPPORTED as a starting point — but the docs call it a guess** | [Gunicorn design docs 23.0.0](https://docs.gunicorn.org/en/23.0.0/design.html) — *"Generally we recommend `(2 x $num_cores) + 1` as the number of workers to start off with. While not overly scientific, the formula is based on the assumption that for a given core, one worker will be reading or writing from the socket while the other worker is processing a request… Our recommendation is to start with the above guess and tune using TTIN and TTOU signals while the application is under load."* | Gunicorn 23.0.0, current |
| 7 | Flask async views since 2.0 | 9 | **SUPPORTED** | [Flask CHANGES.rst, Version 2.0.0](https://github.com/pallets/flask/blob/main/CHANGES.rst) — *"Support async views, error handlers, before and after request, and teardown functions."* Released to PyPI 2021-05-11. | 2021-05-11 |

## What to change on the slides

**Slide 8 — two edits, both cheap now.**

* `2 billion` → **`3 billion`**. This is free: the number went up, the source is
  four months more recent than the deck, and it is a stronger proof point.
  Say "3 billion monthly users, as of September 2025" and the date does the work.
* `Hundreds of processes per host` → **`Dozens of worker processes per host`**.
  The only public primary source says dozens, and it is a 2017 post — so it is
  also nine years old. The safest phrasing keeps the *mechanism*, which is what
  the slide actually needs, and drops the unverifiable magnitude:
  *"uWSGI pre-fork, dozens of worker processes per host. The GIL never enters
  the conversation."* If someone challenges the number, the answer is a quote,
  not a guess.

**Slide 7 — one sentence to add, because it is the honest version and it is
also the more interesting one.** "Officially supported" under PEP 779 means
*Phase II*: supported, but still **not the default build**. Phase III — making
free-threading the default — is explicitly out of scope in the PEP and is not
scheduled. Also worth knowing: PEP 779's own acceptance criteria allow up to a
**15% single-threaded slowdown** (it was ~10% at the time). Experiment 3 measures
what that actually costs on this hardware, which turns a slide assertion into a
number the speaker owns.

**Slides 5 and 9 — soften `2 × cores + 1` from a rule into what Gunicorn
actually calls it.** The docs say "start off with", "not overly scientific", and
"a guess… then tune". This is not a weakening: slide 10's item 5 already says
*"Set worker count deliberately. Not by default. Measure, then choose."*
Quoting Gunicorn's own hedge makes slides 5, 9 and 10 say the same thing instead
of two different things. Suggested wording: *"Gunicorn's own starting point is
2 × cores + 1 — their word is 'guess'. Then measure."*
