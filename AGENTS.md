# AGENTS.md

Reference for coding agents working this repo. Single-user Korean subway
tracker: FastAPI + SQLite backend, vanilla-JS/Leaflet frontend, no build step.

## Run / test

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000   # dev server
uv run pytest                                        # tests (respx mocks httpx)
uv run pytest tests/test_journey.py -k board         # single test
```

No lint/format config in repo — match existing style, don't add tooling
unasked.

## Layout

```
app/
  main.py      FastAPI app, lifespan (loads stations, opens db, resumes journey)
  api.py       all HTTP routes, prefix /api
  journey.py   state machine + background tracking loop (the core logic)
  db.py        sqlite3 persistence, sync, short transactions
  models.py    pydantic models shared by api/journey/db
  seoul.py     Seoul realtime position/arrival API client + key rotation
  tmap.py      Tmap transit route search client, itinerary parsing
  stations.py  station CSV registry, name normalization/autocomplete
  lines.py     Tmap route name <-> Seoul line-name/subwayId mapping
  reitti.py    OwnTracks push to Reitti, retries, dedup by timestamp
  config.py    pydantic-settings, reads .env
static/        index.html/app.js (rider UI), debug.html/debug.js (map+timeline debug view)
tests/         pytest, respx for HTTP mocks, one file per app/ module roughly
docs/api-samples/  recorded raw API responses used as test fixtures
data/          stations.csv + tracker.db (gitignored except sample CSV)
```

## Core flow (read journey.py first for anything tracking-related)

One `ActiveJourney` at a time, held in `JourneyManager.active`, mirrored to
SQLite so state survives restarts (`resume_from_db`).

Per leg: `AWAITING_BOARD` -> user picks train -> `board()` -> `ON_TRAIN` ->
background `_track_loop` polls until leg end -> `_complete_leg` -> next leg's
`AWAITING_BOARD` or `_push_to_reitti` on the last leg.

Two tracking modes per leg, chosen in `board()`:
- **realtime**: line has a `line_key` (see `lines.py`) and user picked a
  train number. Poll `seoul.fetch_positions`, match by `trainNo`, interpolate
  lat/lon between stations by elapsed time. Polling cadence is adaptive
  (`_next_poll_delay`): fast near station events, slows to ~30s cruising.
  Train missing from feed >90s -> falls back to timer mode.
- **timer**: uncovered line or no train picked. Advance along stations by
  Tmap `sectionTime` elapsed fraction, no live data.

Points are written incrementally as the train passes each station
(`_log_segment`), not just at leg end — timestamps are spread across a
segment proportional to distance along the Tmap `shape` linestring
(`_distance_fractions`), so a restart mid-leg doesn't lose already-logged
path. `db.add_point` dedups by `(journey_id, ts)`, so re-emitting is safe.

Manual overrides in `api.py` bypass tracking: alight (force leg complete),
missed_train (back to picker, journey kept), cancel.

## Data sources / external APIs

| Client | Notes |
|---|---|
| `tmap.py` | POST `transit/routes`, needs `appKey` header. Parses `passShape.linestring` ("lon,lat lon,lat...") into `[lat,lon]` shape points. Errors surface via `result.message`, not HTTP status. |
| `seoul.py` | Two endpoints share one key: `realtimePosition` (poll boarded train), `realtimeStationArrival` (arrivals picker). Key rotation on 429/rate-limit error codes (`ERROR-337`) via `SEOUL_API_KEY_TWO`. Top-level error shapes vary — see `_check()`. |
| `stations.py` | CSV-based, `normalize_name()` strips parens/whitespace/trailing 역 to reconcile name spelling differences across Tmap/CSV/Seoul APIs — use this whenever comparing station names across sources. |
| `reitti.py` | One OwnTracks point per HTTP request, 3 retries w/ backoff, dedup is server-side by timestamp so re-pushing all points on retry is fine. |

`docs/api-samples/*.json` are real recorded responses — check these before
guessing a field name or shape when touching `tmap.py`/`seoul.py`.

## Gotchas

- `lines.py` `tmap_route_to_line_key` returns `None` for uncovered lines —
  always handle that case (falls back to timer tracking), don't assume every
  leg has realtime coverage.
- Station name matching must always go through `normalize_name()` — raw
  string equality across Tmap/Seoul/CSV names will silently fail to match.
- `journey.py` timestamps are unix seconds (`int`); `points` table has a
  unique constraint on `(journey_id, ts)`, so sub-second events collapse —
  this is intentional (Reitti dedups the same way).
- Route search results are cached in `route_options_cache`, keyed by
  normalized station+line; bump `ROUTE_OPTIONS_CACHE_FORMAT_VERSION` in
  `db.py` if the `Itinerary`/`SubwayLeg` model shape changes, or old cached
  rows will fail to deserialize.
- `_stop_tracker` deliberately refuses to cancel the task it's currently
  running inside — leg completion is triggered from within the tracking loop
  itself; don't "simplify" this by always cancelling.
- Single active journey by design — `start_journey` cancels any existing one.
  Don't add multi-journey support without discussing scope first.

## Tests

`respx` mocks all outbound httpx calls (Tmap/Seoul/Reitti) — no real network
in tests. `tests/test_frontend_map.py` and `test_debug_locations.py` test the
static frontend/debug view logic. Add fixtures to `docs/api-samples/` if a
test needs a new recorded API shape.
