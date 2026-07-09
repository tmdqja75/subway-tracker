# Subway Tracker

Korean subway journey tracker. Pick a route, pick your train, and the server
logs your position along the ride (interpolated between stations) and pushes
the trace to your [Reitti](https://www.dedicatedcode.com/projects/reitti/)
instance when you arrive.

Single-user, single process: FastAPI + SQLite + a vanilla-JS/Leaflet mobile
frontend served from the same app.

## Data sources

| Source | Used for |
|---|---|
| Tmap Transit API (`transit/routes`) | route search, per-leg station lists + coordinates, leg travel times |
| Seoul realtime subway position (OA-12764, `realtimePosition`) | tracking the boarded train |
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
   `SEOUL_API_KEY`, `REITTI_URL`, `REITTI_TOKEN`.
3. **Run**:

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://<server>:8000` on your phone.

## How tracking works

- You pick a train from the arrivals list at your boarding station; the server
  then polls the position API every 15 s (`POLL_INTERVAL_SECONDS`) for that
  train number and interpolates lat/lon between station coordinates by elapsed
  time (interpolated points are marked lower-accuracy for Reitti).
- Arrival at the leg's last station auto-advances to the next leg's train
  picker (transfer) or finishes the journey and pushes to Reitti.
- Manual overrides: "지금 내렸어요" (force alight), "열차 잘못 탔어요" (back to
  picker), cancel.
- Lines the realtime API doesn't cover (and trains that vanish from the feed)
  fall back to time-based estimation using Tmap's leg travel time.
- Journey state lives in SQLite; the server resumes tracking after a restart,
  and the frontend resumes whatever state the server reports on reload.

## Notes / limitations

- One journey at a time (single-user by design).
- Interpolation is straight-line between adjacent stations; curves are cut.
- Express trains skip stations; interpolation still anchors on the realtime
  feed, so positions stay roughly correct, but per-segment timing is off.
- The realtime position feed identifies trains by `trainNo`; numbers can
  change at line boundaries (through-running trains) — tracking then falls
  back to timer mode.
