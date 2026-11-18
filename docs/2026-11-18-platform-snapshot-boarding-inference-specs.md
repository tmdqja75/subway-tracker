# Platform Snapshot Boarding Inference Specification

## Goal

When a rider selects an `already_onboard` train (retroactive boarding), infer
when that train actually arrived at and departed from the leg origin — and each
station after it — from line snapshots observed while the leg was waiting at the
platform, instead of spreading Tmap `section_time` evenly backwards from the
current observation.

This matters most at transfers: the origin arrival time ends the preceding
transfer walk (`_log_transfer_walk`), so a schedule guess there distorts both the
walk and the ride start.

## Background (current behaviour)

- `app/subway_feed.py` fetches a whole-line snapshot from the self-hosted feed
  (`SUBWAY_API_URL` → `~/Documents/server/subway` `/subway/seoul?lineId=`):
  every station in order with its `up`/`dn` trains (`no`, `status` 접근/도착/출발,
  `dest`, `type`). The feed carries no per-train timestamp.
- Snapshots are only fetched when the rider's picker polls
  `GET /api/journeys/current/arrivals` (every 15 s, browser-driven). That one
  request fetches the line **three times** (`fetch_arrivals`,
  `fetch_onboard_candidates`, `fetch_boarding_context`), each a live Seoul call.
  Nothing is retained.
- `begin_realtime_tracking_from_onboard` (`app/journey.py`) computes
  `origin_arrival` from `_reconstruction_timing` (uniform per-segment budget,
  dwell `min(30, 25% budget)`) and `_reconstruct_history` writes
  `estimated=True` points from origin to the live anchor. See
  `docs/2026-07-21-retroactive-train-tracking-specs.md`.

## Goals

- Record line snapshots server-side for the whole time a realtime-covered leg is
  `awaiting_board`, whether or not the rider's app is open (riders both watch
  the picker and open the app only after boarding; both must work).
- Use those snapshots to infer per-station arrival/departure times for the
  selected retroactive train, from the leg origin up to its current observation.
- Fetch the line once per picker refresh instead of three times.

## Non-goals

- No change to `~/Documents/server/subway`. Its Seoul `recptnDt` per-train time
  is not exposed and will not be; snapshot time is our own receive time.
- No persistence. Snapshots are not written to SQLite, not shown in the debug
  view, and not kept after the inference runs.
- No change to live tracking after boarding (`_track_loop`, `locate_train`),
  timer-mode legs, normal (non-retroactive) boarding, or frontend UI/copy.
- No change to `_rebuild_if_train_fell_behind`.

## Anti-goals

- Inferred times must never be presented as provider timestamps: every
  reconstructed point stays `estimated=True`; the selected live observation
  remains the only non-estimated anchor (resume logic in `resume_from_db`
  depends on this).
- Server polling must not raise Seoul load above today's picker load.
- Missing or partial snapshot history must never block or fail boarding; it
  degrades to today's schedule estimate.

## Approaches considered

- **A. Store raw snapshots in SQLite** (table per journey/leg, pruned after
  7 days). Survives restarts and allows later re-analysis, but adds schema,
  pruning and storage for data used exactly once. Rejected by the rider: the
  snapshots are only needed for this one calculation.
- **B. Store only filtered train observations.** Smaller, but filters before the
  selected train is known; a filtering mistake loses data permanently.
- **C. In-memory snapshot buffer on the active journey (chosen).** Parsed
  snapshots kept in a bounded list on `ActiveJourney`, discarded once the leg is
  boarded or left. Simplest; the only cost is that a server restart while
  waiting loses history captured before the restart, which falls back to the
  schedule estimate.

Snapshot timestamps: the feed has no usable timestamp, so each snapshot is
stamped with our server's receive time (`time.time()` after the response is
parsed). Changing the feed service to pass Seoul's `recptnDt` was considered
and declined.

## Design

### Snapshot buffer

- `ActiveJourney` gains an in-memory buffer of `(fetched_at: float,
  snapshot: list[StationSnapshot])`, bounded (builder's call; ~240 entries = 1 h
  at 15 s).
- The buffer belongs to the current leg: cleared when the leg is boarded
  (normal or retroactive), when the journey leaves `awaiting_board` for any
  reason, and when `leg_idx` changes. `missed_train` returns to
  `awaiting_board`; recording resumes with an empty buffer is acceptable, but
  keeping the buffer is equally fine (builder's call).

### Platform watcher (server-side polling)

- A new `JourneyManager` background task polls the active leg's line every
  15 s while `state == awaiting_board` and `leg.line_key` is covered
  (`LINE_KEY_TO_API_ID`). Uncovered legs are not polled.
- Started on every entry to `awaiting_board`: `start_journey`, `_complete_leg`
  (transfer), `missed_train`, and `resume_from_db` when the resumed journey is
  awaiting boarding. Stopped on board, cancel, stop, and any other transition
  out of `awaiting_board`. Lifecycle mirrors `_start_tracker`/`_stop_tracker`
  and must respect the same "don't cancel the current task" rule if it ever
  triggers a transition itself.
- Fetch errors (`SubwayApiError`) are logged and the loop continues; no extra
  retries beyond `fetch_line_snapshot`'s own.
- *(Author's call)* The watcher stops by itself after 60 minutes of continuous
  polling for the same leg (the buffer horizon). Nothing expires a journey left
  in `awaiting_board`, so without this cap an abandoned journey would poll every
  15 s indefinitely, including after every restart. After the cap, picker-driven
  fetches (single fetch path below) still append to the buffer. A resumed
  journey starts a fresh 60-minute watcher budget.

### Single fetch path

- `JourneyManager` exposes one way to get the leg's current snapshot: return the
  newest buffered snapshot if younger than a max age, otherwise fetch, append to
  the buffer, and return it. Concurrent callers share one in-flight fetch
  (e.g. an `asyncio.Lock`), so the watcher and the picker never hit the feed at
  the same moment (the feed truncates responses under concurrent load).
- `GET /journeys/current/arrivals` uses it with a ~15 s max age and derives
  arrivals, onboard candidates and boarding context from that **one** snapshot.
- `POST /journeys/current/board` revalidation (both normal and retroactive)
  must use a fresh fetch (max age 0) that is also appended to the buffer.
- `subway_feed.py`'s `fetch_arrivals`, `fetch_onboard_candidates` and
  `fetch_boarding_context` are split so the derivation is a pure function of
  `(snapshot, leg[, now])`; `locate_train` and the tracking loop are untouched.

### Inference

Run inside `begin_realtime_tracking_from_onboard`, before the transfer walk and
history are written, using the buffer plus the fresh revalidation snapshot.

1. For each buffered snapshot, find the selected `train_no` and express its
   position as a monotone progress value along the leg:
   `progress = 3 * leg_index + {접근: 0, 도착: 1, 출발: 2}`, where `leg_index` is
   the train's station offset from the leg origin in the leg direction
   (negative when still before the origin; use the existing
   `_leg_station_indices` / `_leg_direction` / `_stations_between` helpers).
   Snapshots without the train are skipped.
   *(Author's call)* Match `train_no` only in the leg-direction bucket
   (`_train_bucket`), plus the one terminus exception `fetch_arrivals` already
   makes: when the origin is a line terminus, an opposite-direction entry at the
   origin with status 접근/도착 and `dest` equal to the origin counts as
   arriving at the origin (progress 0/1). No other opposite-direction entries
   count, so a previous run of the same train number in the buffer can't form a
   false bracket.
2. For each station `i` from the origin (0) up to, but **not including**, the
   candidate's `station_index`, plus the origin itself when
   `station_index == 0`:
   - **arrival** happens between the last snapshot with `progress < 3i + 1` and
     the first with `progress >= 3i + 1`;
   - **departure** happens between the last snapshot with `progress < 3i + 2`
     and the first with `progress >= 3i + 2`.
   The event time is the midpoint of that bracket (≈ ±7.5 s at 15 s polling).
   An event is unknown when either side of the bracket is missing, or when the
   bracket spans more than ~45 s (missed polls) — builder may tune.
   *(Author's call)* The event at the train's **current** station is never
   inferred: it stays pinned to `observed_at` exactly as today (arrival for
   도착/접근, departure for 출발), and `anchor_idx`/`anchor_phase`/`anchor_time`/
   `last_arrival_time` are set exactly as today. An inferred point at the live
   station before the live point would make `_restore_leg_progress` resume an
   arrived train as already departed. For a 출발 candidate at `station_index`
   0, only the origin *arrival* may be inferred; the departure stays the
   observation. This keeps live tracking and resume out of scope.
3. Unknown events are filled from the existing schedule model
   (`_reconstruction_timing` budget/dwell), anchored to the nearest known event
   or to the live observation, keeping all times strictly increasing and before
   the live observation. When no event is known, behaviour is exactly today's.
4. The inferred origin arrival replaces `origin_arrival` for
   `_log_transfer_walk` and `leg_started_at`.
5. `_reconstruct_history` is generalised to take per-station arrival/departure
   times instead of a uniform budget; geometry between stations stays
   distance-proportional along the Tmap shape (`_distance_fractions`). All
   reconstructed points remain `estimated=True`; `history_estimated` stays
   `True`.
6. The buffer is discarded after the inference.
7. Inferred times are persisted only as track points (`points` table: origin
   station coordinates at the inferred arrival and departure `ts`, plus the
   geometry between stations) and as `journeys.leg_started_at` (inferred origin
   arrival). No new column records the departure time or which events came from
   snapshots versus the schedule fallback.

## Constraints

- Points use integer unix seconds and dedupe on `(journey_id, ts)`; inferred
  times must stay monotone so distinct events don't collapse onto the live
  anchor.
- Station-name matching goes through `normalize_name()` (via the existing
  `subway_feed` helpers). Line 2 bucket inversion and loop wrap rules in
  `subway_feed.py` apply unchanged.
- Single active journey; one watcher task at most.

## Verification

- Pure-function tests for the snapshot derivations (arrivals / onboard /
  context from one snapshot) — existing behaviour preserved.
- Pure inference tests: full history (all events known), history starting after
  the train left the origin (origin falls back), a gap > bracket limit, a train
  that disappears and reappears, Line 2 direction.
- Inference tests also cover: a terminus origin (turnback entry counts as
  origin arrival), a stale opposite-direction run of the same train number
  (ignored), and the current station's event staying at `observed_at`.
- Manager tests: watcher starts on journey start, transfer, missed train and
  resume; stops on board/cancel and after the 60-minute cap; skips uncovered
  legs; buffer cleared on board.
- Resume test: a retroactively boarded 도착 train restarted mid-leg resumes with
  `anchor_phase="station"` (unchanged behaviour).
- API test: one picker refresh makes one feed request (respx call count).
- Retroactive board end-to-end: transfer walk ends at the inferred origin
  arrival; reconstructed points estimated; live anchor non-estimated.
- Update `README.md` and `AGENTS.md` (core flow / retroactive section) per the
  repo's documentation rule.

## Left to the builder

Buffer bound, bracket limit, max-age values, whether `missed_train` keeps the
buffer, and the exact shape of the generalised `_reconstruct_history`.
