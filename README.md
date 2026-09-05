# Subway Tracker

Korean subway journey tracker. Pick a route, pick your train, and the server
logs your position along the ride (interpolated between stations) and pushes
the trace to your [Reitti](https://www.dedicatedcode.com/projects/reitti/)
instance when you arrive.

Single-user, single process: FastAPI + SQLite + a statically exported Next.js mobile
frontend served from the same app in production.

## Data sources

| Source | Used for |
|---|---|
| Tmap Transit API (`transit/routes`) | route search, per-leg station lists + coordinates, leg travel times |
| Seoul realtime subway position (OA-12764, `realtimePosition`) | tracking the boarded train and finding a correctly directed train already past the boarding station |
| Seoul realtime station arrival (`realtimeStationArrival`, same key) | "closest approaching trains" picker |
| Station master CSV (OA-21232) | station-name autocomplete + geocoding search endpoints |
| Reitti `POST /api/v1/ingest/owntracks` | final trace upload (one OwnTracks point per request; deduped by timestamp, so retries are safe) |

## Setup

1. **Full station CSV**: `data/stations.csv` ships with a 10-row sample.
   Download the full file from
   [data.seoul.go.kr OA-21232](https://data.seoul.go.kr/dataList/OA-21232/S/1/datasetView.do)
   and replace `data/stations.csv` (keep the header:
   `역사_ID,역사명,호선,위도,경도`, UTF-8).
2. **Keys**: `cp .env.example .env` and fill in `TMAP_APP_KEY`,
   `SEOUL_API_KEY`, optional fallback `SEOUL_API_KEY_TWO`, `REITTI_URL`,
   `REITTI_TOKEN`.
3. **Run the development servers**:

```bash
# Terminal 1: FastAPI API
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Next.js rider UI (proxies /api requests to FastAPI during development)
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:3000` for the rider UI. In native development, FastAPI
at `http://127.0.0.1:8000` provides `/api/*` and `/debug.html`; use the Next.js
server for the rider UI rather than its root. Only the production Docker image
builds the rider static export and serves it at `/`.
If the shared Grafana LGTM container is running on this machine, copy the
OpenTelemetry variables from `.env.example` to `.env` to export telemetry from
a native run.

### Tests and frontend build

```bash
uv run pytest

cd frontend
npm ci
npm test
npm run typecheck
npm run build
```

Outbound HTTP (Tmap/Seoul/Reitti) is mocked with `respx`; no real network or
keys are needed to run Python tests.

## Production deployment

The production image builds the Next.js static export and serves it from the
same FastAPI process as `/api/*`. This provides a single origin: the rider UI
is at `/`, generated `/_next/...` assets are served by FastAPI, and browser API
requests use the same origin without a separate Next production server.

### Docker Compose (recommended)

Compose keeps runtime data in `./data/`, binds the application to
`127.0.0.1:8081`, and rebuilds the frontend as part of the image build.

```bash
cp .env.example .env
# Edit .env with Tmap, Seoul, and Reitti credentials. Do not commit it.

docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs --tail=100 app
```

Verify the deployed service from the host:

```bash
curl -f http://127.0.0.1:8081/
curl -f http://127.0.0.1:8081/debug.html
curl -f 'http://127.0.0.1:8081/api/stations/search?q=강'
```

The rider UI is at `http://127.0.0.1:8081/` and the retained diagnostic page
is at `http://127.0.0.1:8081/debug.html`. On this deployment, Tailscale Serve
owns the tailnet HTTPS listener on port 8081 and proxies it to this loopback
target; keep the Compose binding on `127.0.0.1` so it does not conflict with
the tailnet listener. Use an HTTPS reverse proxy or Tailscale Serve rather
than exposing the app directly.

### App icon and iPhone home screen

The Next.js static export supplies its browser favicon from
`frontend/app/icon.png` and its iPhone Home Screen icon from
`frontend/app/apple-icon.png`. The accompanying `frontend/app/manifest.ts`
declares standalone installation metadata.

To replace the icon everywhere:

1. Replace `frontend/app/icon.png` with the new **square 1024×1024 PNG**. This
   is the favicon and the icon listed in the web manifest.
2. Regenerate the matching 180×180 Apple Touch icon:

   ```bash
   sips -z 180 180 frontend/app/icon.png --out frontend/app/apple-icon.png
   ```

3. Keep the two `icons` entries in `frontend/app/manifest.ts` aligned with the
   files and dimensions above. Update `name`, `short_name`, `description`, or
   the color fields there if the overall product branding has also changed.
4. From `frontend/`, run `npm test`, `npm run typecheck`, and `npm run build`,
   then rebuild and deploy the Docker image.
5. Browsers and iOS cache icons aggressively. Hard-refresh browser tabs; on an
   iPhone, remove the old Home Screen shortcut and use Safari's **Add to Home
   Screen** again after the deployment is reachable over HTTPS.

### iPhone destination notifications

The persistent **알림 설정** control enables alerts for future journeys and is
the only place that requests permission. Web Push needs HTTPS, a Safari Home
Screen-installed app, and iOS/iPadOS 16.4+. Tapping an alert focuses or opens
the active journey app; this version does not set an app-icon badge.

Set all three VAPID values in `.env` to enable delivery. Generate and rotate a
new pair together with `uv run vapid --gen --json`; never commit the private
key or log Push subscription endpoints/encryption keys. Permit outbound HTTPS
to Apple Web Push hosts such as `*.push.apple.com`.

```dotenv
WEB_PUSH_VAPID_PUBLIC_KEY=
WEB_PUSH_VAPID_PRIVATE_KEY=
WEB_PUSH_VAPID_SUBJECT=mailto:you@example.com
```

The tracker durably claims one non-blocking delivery attempt per eligible
`SUBWAY` leg (including transfers) when its destination becomes the next stop.
Bus, train, and ferry legs are excluded. Realtime alerts follow departure from
the penultimate station; timer/fallback tracking uses the equivalent boundary.
Push acceptance is an attempt, not proof iOS displayed an alert.

For manual HTTPS iPhone acceptance: install/open the app from Home Screen, tap
**알림 설정** and grant permission, test realtime/timer/transfer legs for exactly
one `다음 역은 [station-name]이에요. 하차를 준비하세요.` alert each, confirm
non-subway legs do not alert, then tap an alert to focus the journey. Record
only pass/fail and non-secret diagnostics.

To deploy an update, pull the intended revision and rebuild the service. The
`./data` bind mount preserves the station CSV and SQLite database.

```bash
git pull --ff-only
docker compose config
docker compose up -d --build --remove-orphans
docker compose ps
```

Back up `data/tracker.db` before major upgrades. Do not use `docker compose
down -v` unless intentionally discarding persistent Docker volumes.

### Direct Docker run

For a one-container deployment, mount `data/` explicitly so the database
survives container replacement:

```bash
docker build -t subway-tracker:next-ui .
docker run --rm -p 8000:8000 --env-file .env \
  -v "$PWD/data:/app/data" \
  -e DB_PATH=/app/data/tracker.db -e STATIONS_CSV=/app/data/stations.csv \
  subway-tracker:next-ui
```

Use a process manager or `--restart unless-stopped` instead of `--rm` for a
long-lived direct deployment.

## Debug view

`http://<server>:8000/debug.html` (or `http://localhost:8081/debug.html` when
using Compose) lists recent journeys with route geometry, logged points on a
horizontal timeline — useful for checking interpolation/
logging behavior without re-riding a train. Every terminal `push_failed` or
`cancelled` journey enables **"Reitti로 데이터 다시 보내기"**, including a
zero-point record. The button previews the selected journey and point count,
then requires a confirmation before calling
`POST /api/debug/journeys/{journey_id}/retry-push`.
That flow first records `pushing` and durable transfer progress in SQLite, then
resends the retained points to Reitti. It never resumes the cancelled tracker.

## Docker + shared Grafana LGTM

The Compose project runs only the tracker. It exports FastAPI request traces,
HTTP metrics, and application logs directly to the already-running shared
Grafana LGTM stack over OTLP/HTTP; it does not start separate Loki, Promtail,
or Grafana containers.

1. Copy `.env.example` to `.env` and fill in the API keys as described above.
2. Start and verify the stack using [Docker Compose (recommended)](#docker-compose-recommended).

The Docker default for `OTEL_EXPORTER_OTLP_ENDPOINT` is
`http://host.docker.internal:4318`, which reaches the host's shared LGTM
container from Docker Desktop. Override it only when LGTM runs elsewhere.

| Service | URL | Purpose |
|---|---|---|
| `app` | http://localhost:8081 | FastAPI app + static frontend |
| shared Grafana | http://localhost:3000 | Explore/query the tracker telemetry |

In Grafana, open **Explore**, select the `Loki` datasource, and query the
application logs with:

```logql
{service_name="subway-tracker"}
```

Traces use the `subway-tracker` service name in Tempo, and FastAPI emits HTTP
request metrics to the shared Prometheus instance. Runtime data is
bind-mounted from `./data` to `/app/data`, so the SQLite DB and station CSV
persist outside the container.

## How tracking works

- You normally pick a train from the arrivals list at your boarding station.
  If you already boarded before opening the tracker, you can instead choose a
  correctly directed train whose live position is between the leg origin and
  destination. The server then polls the Seoul position API for that train
  number and interpolates lat/lon between station coordinates by elapsed time
  (interpolated points are marked lower-accuracy for Reitti).
- On the **탑승** line, the source feed's station-relative state is retained:
  an arrived train is shown on that station, an approaching train in the
  preceding segment, and a departed train in the following segment. The view
  does not present an arrived train as if it were still between stations.
- The Seoul position feed provides only a current station-relative observation,
  not a historic departure/stop timeline. For a train selected after the
  origin, the tracker reconstructs the origin-to-current path from the leg's
  scheduled time, including estimated station pauses. Those backfilled points
  are explicitly marked as estimated; tracking after the live observation
  continues normally.
- Realtime tracking uses adaptive polling: `POLL_INTERVAL_SECONDS` is the fast
  cadence near station events (default 5 s), while cruising between stations
  slows to about 30 s. The server starts polling fast again during the next
  station's expected arrival window: 40% of the estimated station-to-station
  time, capped between 15 s and 60 s. Missing trains are retried every 10 s, and
  the server falls back to timer mode only after the train has been absent for
  90 s.
- Arrival at the leg's last station auto-advances to the next leg's train
  picker (transfer) or finishes the journey and pushes to Reitti.
- Manual overrides: "지금 내렸어요" (force alight), "열차 잘못 탔어요" (back to
  picker), cancel.
- Lines the realtime API doesn't cover (and trains that vanish from the feed)
  fall back to time-based estimation using Tmap's leg travel time.
- Journey state lives in SQLite; the server resumes tracking after a restart,
  and the frontend resumes whatever state the server reports on reload.
- The first route-search screen also reads SQLite-backed saved routes. It
  shows up to five **Most Used Route** choices (frequency-ranked) and five
  distinct **Recent Route** choices (newest first). Selecting one restores
  both stations and their saved line-specific station IDs before searching.
- Once a journey's trace has been fully delivered, its completion dashboard
  offers **"새 여정 시작하기"**. This returns the rider to the first
  departure/destination search screen. Starting the next route preserves the
  completed journey record for the debug history instead of marking it
  cancelled.
- If a Reitti delivery fails, the failure dashboard retains its retry control
  and also offers **"데이터 나중에 다시 보내기"**. This temporarily returns to
  the departure/destination station-selection screen without changing the
  backend's `push_failed` snapshot; reload to return to the retained transfer
  and retry it. Starting a different journey follows the single-active-journey
  rule and cancels the prior failed journey.

## Notes / limitations

- One journey at a time (single-user by design).
- Interpolation is straight-line between adjacent stations; curves are cut.
- Express trains skip stations; interpolation still anchors on the realtime
  feed, so positions stay roughly correct, but per-segment timing is off.
- The realtime position feed identifies trains by `trainNo`; numbers can
  change at line boundaries (through-running trains) — tracking then falls
  back to timer mode.

## For coding agents

See [AGENTS.md](AGENTS.md) for architecture, the journey state machine, and
known gotchas before making changes.
