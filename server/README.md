# GroundTruth sync server

The backend agents sync to, and the segmentation logic that turns their raw events into
the sessions a dashboard renders.

Implements `docs/API.md` exactly. That document was frozen before parallel work started, so
treat it rather than this README as the contract.

## What is here

| Module | Role |
|--------|------|
| `segment.py` | **The interesting part.** A pure function from events to session spans. |
| `summarize.py` | Totals for a window, including what an app-name tracker would have said. |
| `store.py` | SQLite. Events are append-only and idempotent on `(device_id, ts)`. |
| `derive.py` | The read path: events → resolution overlay → segmenter → filters. |
| `app.py` | The five endpoints, and nothing else. |
| `models.py` | Domain types, mirroring `agent/groundtruth/types.py` field for field. |
| `schemas.py` | The wire shapes from `docs/API.md`. |
| `fixtures.py` | Loader for `fixtures/day.json`. |

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Seed a database with the fixture day, then serve it:

```bash
python -m groundtruth_server.fixtures --db demo.db
```

```bash
GROUNDTRUTH_DB=demo.db uvicorn groundtruth_server.app:app --reload
```

Or start empty and POST the fixture in over HTTP, which exercises `POST /events` the way an
agent would:

```bash
python -m groundtruth_server.fixtures --server http://127.0.0.1:8000
```

```bash
curl -s "http://127.0.0.1:8000/summary?from=2026-10-28T00:00:00Z&to=2026-10-28T23:59:59Z" | python -m json.tool
```

Interactive docs are at `/docs`.

## Tests

```bash
pytest
```

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `GROUNDTRUTH_DB` | `groundtruth.db` | SQLite path, or `:memory:`. |
| `GROUNDTRUTH_IDLE_GAP_S` | `120` | Gap beyond which a session closes. |
| `GROUNDTRUTH_SAMPLE_COVERAGE_S` | `60` | Most seconds one sample may be credited with. |

The thresholds are configuration because they are claims about a person's habits rather than
facts about software. `segment.py` never reads the environment itself — the values are read
at the edge and passed down, which is what lets the segmenter be tested without one.

## Two things worth knowing before changing anything

**Sessions are not stored.** They are recomputed from events on every request. That is what
makes a taxonomy change cheap: a user renaming or splitting a category re-segments history
instead of losing it. A `sessions` table would become the thing clients trusted, and then
renaming a category would be a migration. If you add a cache, make sure the events stay the
source of truth.

**The server never sees pixels.** No endpoint accepts an image, and nothing here can decode
one. Tier 2 looks at the screen on the device and discards the frame; what syncs is a label,
a confidence and a sentence of evidence. An upload endpoint would not be a new feature, it
would be the privacy model failing. `tests/test_api.py` asserts this against the generated
OpenAPI schema so that adding one fails the suite.

## The fixture day

`fixtures/day.json` is one realistic eight-hour day — 480 events across two devices. It is
the dashboard's development data, so its shape is an interface: each entry of `batches` is
exactly a `POST /events` body as the contract defines it, and the loader validates it through
the same models the API uses.

Regenerate it with `python fixtures/build_day.py`. The day is a table of thirteen blocks in
that script, which is where to read it from; the JSON is what it expands to.

It derives twelve sessions totalling exactly eight hours, and the comparison it is built to
show is this:

```
  What an app-name tracker reports        What GroundTruth reports
  ────────────────────────────────        ────────────────────────
  chrome.exe .............. 3h 28m        deep work ............... 2h 15m
  code.exe ................ 1h 40m        doomscrolling ........... 1h 58m
  (off-screen) ............ 0h 55m        gym (voice-logged) ...... 0h 55m
  windowsterminal.exe ..... 0h 35m        research ................ 0h 55m
  outlook.exe ............. 0h 30m        admin ................... 0h 30m
  slack.exe ............... 0h 25m        devotion ................ 0h 28m
  vlc.exe ................. 0h 20m        communication ........... 0h 25m
  msedge.exe .............. 0h 07m        entertainment ........... 0h 20m
                                          uncertain (2 spans) ..... 0h 14m
```

Same 28,800 seconds, grouped two ways. Chrome is 43% of the day and means five different
things; the morning's 90-minute work block spans an editor and a terminal, which the naive
view splits into 55 minutes of one application and 35 of another.
