# AGENTS.md

Reference for coding agents working this repo. Single-user Korean subway
tracker: FastAPI + SQLite backend and a statically exported Next.js rider UI.
The legacy static debug view remains separate from the rider UI.

## Run / test

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000   # dev server
uv run pytest                                      # Python tests (respx mocks httpx)
uv run pytest tests/test_journey.py -k board       # focused Python test

cd frontend
npm ci
npm test                                           # Vitest units; script enforces NODE_ENV=test
npm run typecheck
npm run build                                      # static export to frontend/out
npm run test:e2e                                   # Chromium + Pixel mobile flows
```

The rider UI runs from Next on `http://127.0.0.1:3000` during development;
its rewrite proxies `/api/*` to FastAPI on port 8000. Do not expect FastAPI's
native-development root to serve the rider UI. Production Docker builds
`frontend/out` and copies it to `/app/static` for same-origin delivery.

No lint/format config exists beyond TypeScript/Vitest/Playwright checks —
match existing style and do not add tooling unasked.

## Documentation maintenance

Whenever making major changes to the codebase—architecture, user-facing flows,
API contracts, deployment, development workflow, or test strategy—update both
`README.md` and `AGENTS.md` accordingly. Keep the README useful to operators
and users, and keep AGENTS.md accurate for future coding agents.

## Layout

```
app/
  main.py      FastAPI app, lifespan (loads stations, opens db, resumes journey)
  api.py       all HTTP routes, prefix /api (including SQLite-backed route history)
  journey.py   state machine + background tracking loop (the core logic)
  db.py        sqlite3 persistence, sync, short transactions
  models.py    pydantic models shared by api/journey/db
  seoul.py     Seoul realtime position/arrival API client + key rotation
  tmap.py      Tmap transit route search client, itinerary parsing
  stations.py  station CSV registry, name normalization/autocomplete
  lines.py     Tmap route name <-> Seoul line-name/subwayId mapping
  reitti.py    OwnTracks push to Reitti, retries, dedup by timestamp
  config.py    pydantic-settings, reads .env
frontend/      Next.js App Router static-export workspace (rider UI)
  app/         application shell, global styles, page
  components/  search, boarding, tracking, transfer, and map UI
  hooks/       authoritative current-journey polling
  lib/         typed API client and shared frontend types
  e2e/         mocked browser rider workflows
static/        debug.html/debug.js/debug.css diagnostic map+timeline view only
tests/         pytest, respx for HTTP mocks, one file per app/ module roughly
  test_static_delivery.py  production API-before-static-mount regression test
docs/api-samples/  recorded raw API responses used as test fixtures
data/          stations.csv + tracker.db (gitignored except sample CSV)
```

## Frontend and delivery architecture

FastAPI remains authoritative for persisted journeys, tracking, transfers,
retry state, and every `/api/*` contract. React owns only transient UI state.
`useCurrentJourney` is the sole source for an active journey after reload;
never introduce a second persisted journey state in the frontend.

The completed dashboard's **"새 여정 시작하기"** action and a failed-transfer
dashboard's **"데이터 나중에 다시 보내기"** action are narrow presentation
exceptions: either may locally reveal the initial station search while the
authoritative snapshot remains `completed` or `push_failed`. The latter does
not retry or discard retained points; reload returns to the durable failed
transfer. Clear this mode only when the server reports another state, and do
not fabricate an idle snapshot. The eventual route start still uses
`POST /api/journeys`; because the manager supports only one active journey, a
new route cancels a prior `push_failed` journey.

The static debug page is a separate diagnostic surface. Its Reitti resend
button is enabled for every `push_failed` or `cancelled` journey returned by
`GET /api/debug/locations` (`can_retry`), including zero-point records; it
previews the selected journey's point count and uses a browser confirmation
before calling `POST /api/debug/journeys/{journey_id}/retry-push`. Keep resend
behavior on that manager/API path so `_start_push()` persists `pushing` plus
transfer progress in SQLite before any outbound transmission. A cancelled
record's debug resend must not resume its tracker or replace another active ride.

The rider UI is a Next static export, not an SSR or standalone production
Next server. `next.config.ts` rewrites are development-only. `Dockerfile`
builds `frontend/out` in a Node stage, then the Python runtime copies that
export and the retained debug assets into `/app/static`. `app.main` includes
the API router before mounting root static files; preserve that order.

Production Compose binds the app to `127.0.0.1:8081`. On this host, Tailscale
Serve owns the tailnet HTTPS listener on port 8081 and proxies it to that
loopback target; do not change the Compose binding to all interfaces, which
prevents OrbStack from publishing the port after Tailscale has claimed it.

Browser and install branding use Next's App Router metadata-file conventions:
`frontend/app/icon.png` is the square favicon/manifest icon,
`frontend/app/apple-icon.png` is the 180×180 Apple Touch icon, and
`frontend/app/manifest.ts` declares standalone app metadata. Keep the two PNGs
square and update the manifest dimensions if either icon size changes.

## Web Push destination notifications

The persistent **알림 설정** control feature-detects browser APIs and registers
root `/service-worker.js`; it must request permission only after a direct user
tap. The browser may receive the VAPID public key, but never the private key or
subscription endpoint/encryption keys. The Service Worker must focus an
existing same-origin client or open `/` after a notification click.

FastAPI owns eligibility and atomically claims `(journey_id, leg_idx)` before
non-blocking delivery, snapshotting subscriptions and preserving retry state
across restart. Alert only `mode == "SUBWAY"` legs with at least two stations:
realtime `departed` indexes the station just left, local `between` indexes the
next station, and timer `estimated` indexes the active segment start. Include
transfer subway legs; exclude bus/train/ferry. Evaluate the immediate observed
status in retroactive onboarding. Delete permanent 404/410 subscriptions and
bound transient retries. Push acceptance is delivery-attempt evidence only.

Configure `WEB_PUSH_VAPID_PUBLIC_KEY`, `WEB_PUSH_VAPID_PRIVATE_KEY`, and
`WEB_PUSH_VAPID_SUBJECT` together. Production validation requires HTTPS, a
Home Screen-installed iOS/iPadOS 16.4+ app, and Apple Push egress. Manually
verify realtime/timer/transfer exact-once behavior and notification-tap focus;
record only non-secret diagnostics.

Leaflet is client-only. Keep browser globals and Leaflet imports inside the
component effect boundary, validate map geometry, and clean maps/layers on
identity changes and unmount. Do not let volatile polling counters recreate
an imperative map.

## Core flow (read journey.py first for anything tracking-related)

One `ActiveJourney` at a time, held in `JourneyManager.active`, mirrored to
SQLite so state survives restarts (`resume_from_db`).

Per leg: `AWAITING_BOARD` -> user picks train -> `board()` -> `ON_TRAIN` ->
background `_track_loop` polls until leg end -> `_complete_leg` -> next leg's
`AWAITING_BOARD` or `_push_to_reitti` on the last leg.

For a rider who opens the app after boarding, the covered-leg picker may also
offer a train from `realtimePosition` that is already between the leg origin
and destination. It must still match direction using normalized station names,
and selection is revalidated against a fresh position response before state
changes. Seoul exposes a current station-relative observation, not historical
stop events: reconstruct the origin-to-current geometry from scheduled segment
time, mark every backfilled point estimated, and make the subsequent live
anchor authoritative. Never present the reconstructed station pauses as actual
provider timestamps.

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

## Route-history chooser

The initial rider search view calls `GET /api/routes/history`. It returns
`most_used` and `recent` arrays of resolved `Station` pairs, each capped at
five. `Database` derives the route key from the persisted itinerary's first
leg start and final leg end, including their line keys: most-used groups all
journeys by that key, while recent keeps the latest distinct keys. The API
resolves those saved name/line pairs through `StationRegistry` so the frontend
receives exact station IDs and can prefill both autocomplete selections without
losing an interchange's selected line. Malformed historical itineraries or
unresolvable station pairs are skipped safely.

## Data sources / external APIs

| Client | Notes |
|---|---|
| `tmap.py` | POST `transit/routes`, needs `appKey` header. Parses `passShape.linestring` ("lon,lat lon,lat...") into `[lat,lon]` shape points. Errors surface via `result.message`, not HTTP status. |
| `seoul.py` | Two endpoints share one key: `realtimePosition` (poll boarded train), `realtimeStationArrival` (arrivals picker). Key rotation on 429/rate-limit error codes (`ERROR-337`) via `SEOUL_API_KEY_TWO`. Top-level error shapes vary — see `_check()`. `realtimeStationArrival` is inconsistent about parenthetical station-name suffixes (unlike `realtimePosition`, which always matches normalized names): some stations only match `normalize_name()`'s stripped form, others (e.g. `광나루` → only `광나루(장신대)` works) only match the CSV's full display name. `fetch_arrivals()` takes an `alt_station_name` fallback, retried once when the normalized query returns no results; `api._fetch_leg_arrivals()` supplies it from `StationRegistry.find()`. |
| `stations.py` | CSV-based, `normalize_name()` strips parens/whitespace/trailing 역 to reconcile name spelling differences across Tmap/CSV/Seoul APIs — use this whenever comparing station names across sources. It is not a safe *query* string for `realtimeStationArrival` (see `seoul.py` row above); it is fine for `realtimePosition` matching and everywhere else. |
| `reitti.py` | One OwnTracks point per HTTP request, 3 retries w/ backoff, dedup is server-side by timestamp so re-pushing all points on retry is fine. |

`docs/api-samples/*.json` are real recorded responses — check these before
guessing a field name or shape when touching `tmap.py`/`seoul.py`.

## Gotchas

- `lines.py` `tmap_route_to_line_key` returns `None` for uncovered lines —
  always handle that case (falls back to timer tracking), don't assume every
  leg has realtime coverage.
- Station name matching must always go through `normalize_name()` — raw
  string equality across Tmap/Seoul/CSV names will silently fail to match.
  Exception: `seoul.fetch_arrivals()`'s *query string* to Seoul's
  `realtimeStationArrival` — some stations only accept the normalized/stripped
  form, others only accept the CSV's full parenthetical name. Always pass
  `alt_station_name` (from `StationRegistry`) rather than assuming
  `normalize_name()` alone is a safe query for that one endpoint.
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
- Single active journey by design — `start_journey` cancels an existing
  non-completed journey. A completed journey is terminal history: preserve its
  database state when beginning the next journey so the debug view retains it.
  A `push_failed` journey can temporarily show the station search through
  "데이터 나중에 다시 보내기", but beginning a route still cancels that failed
  journey; don't add multi-journey support without discussing scope first.
- Covered arrival cards must only expose `matches_direction === true`; never
  offer an opposite-direction fallback. Uncovered legs board with a null train
  in timer mode.
- `ArrivingTrain.status` preserves the feed's station-relative state for the
  boarding diagram: render `arrived` on its reported station node, `approaching`
  in the preceding segment, and `departed` in the following segment. Do not
  flatten every arrival into an in-between marker.
- Keep an action success lock until the authoritative journey/leg snapshot
  changes. An in-flight flag alone permits duplicate actions during refresh.
- `pushing` polling is frequent. Use stable journey/leg identity for map
  lifecycle keys; keep progress metrics out of those keys.
- Vitest must exclude both `test/e2e/**` and root `e2e/**`; Playwright owns
  browser specs. Rider E2E fixtures must route external map tiles locally.

## Tests

`respx` mocks all outbound httpx calls (Tmap/Seoul/Reitti) — no real network
in Python tests. `tests/test_frontend_map.py` and `test_debug_locations.py`
cover the retained debug view; `tests/test_static_delivery.py` protects static
and API route ordering. Frontend units run in Vitest; Playwright verifies
desktop and Pixel flows with stateful API mocks and locally fulfilled map tiles.
Run a real static build and container smoke test after delivery changes. Add
fixtures to `docs/api-samples/` if a backend test needs a new recorded API
shape.
