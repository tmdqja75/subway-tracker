# Platform Snapshot Boarding Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a rider picks an already-departed train, infer its real arrival/departure times at the leg origin (and stations up to its current one) from line snapshots the server recorded while the leg waited at the platform.

**Architecture:** A `JourneyManager` background task polls the leg's whole-line snapshot every 15 s while `awaiting_board` and keeps `(receive_time, snapshot)` pairs in an in-memory deque on `ActiveJourney`. The picker endpoint reads the same snapshot path (one feed fetch per refresh instead of three). A pure module turns the buffered snapshots into per-station event times; retroactive boarding merges them with today's schedule estimate and writes the same `estimated=True` history, then discards the buffer.

**Tech Stack:** Python 3.12, FastAPI, asyncio, sqlite3, pytest + pytest-asyncio (`asyncio_mode = "auto"`), respx.

**Spec:** `docs/2026-11-18-platform-snapshot-boarding-inference-specs.md`

Work on a branch (`feat/platform-snapshot-inference`), never directly on `main`. Run tests with `uv run pytest`.

## Global Constraints

- Snapshot timestamp = our server's receive time, `time.time()` right after `fetch_line_snapshot` returns. No change to `~/Documents/server/subway`.
- Snapshots live only in memory (`ActiveJourney.platform_snapshots`); no SQLite schema change, no new column, no frontend change.
- Constants (in `app/journey.py` unless noted): `PLATFORM_POLL_SECONDS = 15`, `PLATFORM_WATCH_MAX_SECONDS = 3600`, `PLATFORM_BUFFER_MAX = 240`; `MAX_BRACKET_SECONDS = 45.0` in `app/platform_history.py`; `ARRIVALS_SNAPSHOT_MAX_AGE = 10` in `app/api.py`.
- Every reconstructed point stays `estimated=True`; the selected live observation is the only non-estimated anchor; `history_estimated` stays `True`.
- The current station's event is never inferred (pinned to `observed_at` exactly as today). `between` candidates get no inference.
- Missing/partial snapshot history degrades to today's schedule estimate; it never fails boarding.
- Station-name matching only via existing `subway_feed` helpers (`_leg_station_indices`, `_leg_direction`, `_stations_between`, `_train_bucket`, `normalize_name`).
- Live tracking (`_track_loop`, `locate_train`), timer mode, normal boarding history, and `_rebuild_if_train_fell_behind` behave exactly as before.
- **Zero real Seoul / feed calls while testing.** Every test and verification step uses synthetic snapshots (`_payload`/`_parse_snapshot` or hand-built `StationSnapshot`s) or fakes; nothing reads `SUBWAY_API_URL` from `.env`, runs the dev server against the real feed, or records new samples into `docs/api-samples/`. Test `Settings` always use `subway_api_url="http://subway.test"` with `_env_file=None`. The autouse `tests/conftest.py` guard (Task 3, Step 1) makes the new background polling network-free even in tests that build their own `JourneyManager`. Live validation is one real ride by the rider after merge, not a plan step.

## Review Focus

- **Same train number seen on an earlier run in the 1 h buffer** — inference must use only the latest continuous run (progress never goes backwards within it). Test in Task 2.
- **Express leg whose stops skip physical stations** — hop counts don't match `leg.stations` indices; inference must return all-unknown rather than misassign times. Test in Task 2.
- **Line 2 loop: a train just behind the origin** — must resolve to a small negative offset, not ~40 stations ahead. Test in Task 2.
- **Leg changes or journey is cancelled while a fetch is in flight** — the late snapshot must not land in the new leg's buffer. Test in Task 3.
- **Rider waits past the 60-minute watcher cap** — picker-driven refreshes must still append snapshots so inference keeps working. Test in Task 3.

---

### Task 1: Pure snapshot derivations in `subway_feed`

**Files:**
- Modify: `app/subway_feed.py:231-367`
- Test: `tests/test_subway_feed.py`

**Interfaces:**
- Produces (pure, no I/O):
  - `arrivals_from_snapshot(snapshot: list[StationSnapshot], leg: SubwayLeg, limit: int = 3) -> list[ArrivingTrain]`
  - `onboard_candidates_from_snapshot(snapshot: list[StationSnapshot], leg: SubwayLeg, *, now: float) -> list[OnboardTrain]`
  - `boarding_context_from_snapshot(snapshot: list[StationSnapshot], leg: SubwayLeg, window: int = 3) -> list[str]`
- Existing `fetch_arrivals` / `fetch_onboard_candidates` / `fetch_boarding_context` keep their signatures for now and become: uncovered-line check → `fetch_line_snapshot` → the matching `*_from_snapshot`. (Task 3 deletes them.)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_subway_feed.py`, reusing `_payload`, `_leg` and `_parse_snapshot`:

```python
from app.subway_feed import (
    _parse_snapshot, arrivals_from_snapshot, boarding_context_from_snapshot,
    onboard_candidates_from_snapshot,
)

def test_arrivals_from_snapshot_needs_no_network():
    snapshot = _parse_snapshot(_payload(
        ("A", [], [{"status": "출발", "type": "일반", "dest": "D", "no": "far"}]),
        ("B", [], [{"status": "도착", "type": "급행", "dest": "D", "no": "near"}]),
        ("C", [], []), ("D", [], []),
    ))
    trains = arrivals_from_snapshot(snapshot, _leg("3호선", "C", "D"))
    assert [t.train_no for t in trains] == ["near", "far"]

def test_onboard_candidates_from_snapshot_stamps_given_now():
    snapshot = _parse_snapshot(_payload(
        ("A", [], []), ("B", [], [{"status": "도착", "type": "일반", "dest": "C", "no": "x"}]), ("C", [], []),
    ))
    [candidate] = onboard_candidates_from_snapshot(snapshot, _leg("3호선", "A", "B", "C"), now=1_234.9)
    assert (candidate.train_no, candidate.station_index, candidate.observed_at) == ("x", 1, 1_234)

def test_boarding_context_from_snapshot_lists_preceding_stations():
    snapshot = _parse_snapshot(_payload(("P1", [], []), ("P2", [], []), ("A", [], []), ("B", [], [])))
    assert boarding_context_from_snapshot(snapshot, _leg("3호선", "A", "B")) == ["P1", "P2"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_subway_feed.py -k from_snapshot -v`
Expected: FAIL with `ImportError: cannot import name 'arrivals_from_snapshot'`

- [ ] **Step 3: Extract the three pure functions**

Move each `fetch_*` body after the `fetch_line_snapshot` call into the matching `*_from_snapshot`; the `fetch_*` wrappers keep the `LINE_KEY_TO_API_ID` uncovered check and delegate. No behaviour change.

- [ ] **Step 4: Run the whole feed suite**

Run: `uv run pytest tests/test_subway_feed.py -v`
Expected: all PASS (old `fetch_*` tests unchanged and green).

- [ ] **Step 5: Commit**

```bash
git add app/subway_feed.py tests/test_subway_feed.py
git commit -m "refactor: derive arrivals, onboard and context from one line snapshot"
```

---

### Task 2: Snapshot history inference (pure)

**Files:**
- Create: `app/platform_history.py`
- Test: `tests/test_platform_history.py`

**Interfaces:**
- Consumes: `StationSnapshot`, `_leg_station_indices`, `_leg_direction`, `_stations_between`, `_step`, `_train_bucket` from `app.subway_feed`; `normalize_name`.
- Produces:
  - `MAX_BRACKET_SECONDS = 45.0`
  - `train_progress(snapshot: list[StationSnapshot], leg: SubwayLeg, train_no: str) -> int | None`
  - `infer_station_events(history: Sequence[tuple[float, list[StationSnapshot]]], leg: SubwayLeg, train_no: str, *, max_bracket: float = MAX_BRACKET_SECONDS) -> list[tuple[float | None, float | None]]` — one `(arrival, departure)` per `leg.stations` entry.
  - `merge_event_times(schedule: list[float], inferred: list[float | None], upper: float) -> list[float]`

Rules (from spec):
- `train_progress`: `3 * offset + {"접근": 0, "도착": 1, "출발": 2}[status]`. Match `train_no` only in `_train_bucket(line_key, station, direction)`. `offset` = forward hops `_stations_between(line_key, origin, raw, direction)` or minus backward hops `_stations_between(line_key, raw, origin, direction)`, whichever has the smaller absolute value (forward on ties). Terminus exception: if `_step(line_key, origin, -direction)` is outside the snapshot, an opposite-bucket entry **at the origin** with status 접근/도착 and `normalize_name(dest) == normalize_name(origin name)` gives progress 0/1. Returns `None` when the train is absent or the leg can't be resolved.
- `infer_station_events`: sort by time, drop `None` progress, then keep only samples after the last point where progress decreased (latest run). If `_stations_between(origin, last_station_idx)` ≠ `len(leg.stations) - 1` (express skipping), return all `(None, None)`. For threshold `k`, find the first sample with `progress >= k`; the event is unknown if it is the first kept sample or if `t_first - t_before > max_bracket`; otherwise it is `(t_before + t_first) / 2`. Arrival at `i` uses `k = 3i + 1`, departure `k = 3i + 2`.
- `merge_event_times` (algorithm the tests don't fully pin):

```python
known = [k for k, x in enumerate(inferred) if x is not None]
times = []
for k, s in enumerate(schedule):
    if inferred[k] is not None:
        times.append(inferred[k])
    elif known:
        m = min(known, key=lambda i: abs(i - k))  # nearest known event; earlier wins ties
        times.append(s + inferred[m] - schedule[m])
    else:
        times.append(s)
for k in range(len(times) - 1, -1, -1):  # strictly increasing, below upper, ≥1 s apart
    ceiling = upper if k == len(times) - 1 else times[k + 1]
    times[k] = min(times[k], ceiling - 1)
return times
```

- [ ] **Step 1: Write the failing tests**

Helpers in the test file: `_snap(*stations)` building `StationSnapshot(name, up, dn)` with `TrainEntry(status, "일반", dest, no)`, and `_leg(line_key, *names)` like `tests/test_subway_feed.py`. Line 3 legs ordered along the snapshot use the `dn` bucket; Line 2 uses `up`.

```python
def test_train_progress_counts_status_steps_from_origin():
    snap = _snap(("O", [], []), ("B", [], [("도착", "C", "t")]), ("C", [], []))
    assert train_progress(snap, _leg("3호선", "O", "B", "C"), "t") == 4

def test_train_progress_is_negative_before_origin():
    snap = _snap(("A", [], [("출발", "C", "t")]), ("O", [], []), ("B", [], []), ("C", [], []))
    assert train_progress(snap, _leg("3호선", "O", "B", "C"), "t") == -1

def test_train_progress_line2_prefers_shorter_direction():
    # 43-name 2호선 snapshot S0..S42, leg S5 → S6 → S7, train `t` in the `up` bucket at S4 with 출발.
    # Backward offset -1 beats the ~41-hop forward route: 3 * -1 + 2 == -1.
    assert train_progress(snap, leg, "t") == -1

def test_train_progress_terminus_turnback_counts_as_origin_arrival():
    snap = _snap(("O", [("도착", "O", "t")], []), ("B", [], []))
    assert train_progress(snap, _leg("3호선", "O", "B"), "t") == 1

def test_train_progress_ignores_opposite_direction_elsewhere():
    snap = _snap(("O", [], []), ("B", [("도착", "Z", "t")], []), ("C", [], []))
    assert train_progress(snap, _leg("3호선", "O", "B", "C"), "t") is None

def test_infer_station_events_uses_bracket_midpoints():
    leg = _leg("3호선", "O", "B", "C")
    history = [(1000, at("O", "접근")), (1015, at("O", "도착")), (1030, at("O", "도착")),
               (1045, at("O", "출발")), (1060, at("B", "접근")), (1075, at("B", "도착"))]
    events = infer_station_events(history, leg, "t")
    assert events[0] == (1007.5, 1037.5)
    assert events[1] == (1067.5, None)

def test_infer_station_events_unknown_without_earlier_sample():
    history = [(1000, at("O", "도착")), (1015, at("O", "출발"))]
    assert infer_station_events(history, leg, "t")[0] == (None, 1007.5)

def test_infer_station_events_unknown_when_bracket_too_wide():
    history = [(1000, at("O", "접근")), (1100, at("O", "도착")), (1110, at("O", "출발"))]
    assert infer_station_events(history, leg, "t")[0] == (None, 1105.0)

def test_infer_station_events_skips_snapshots_without_train():
    history = [(1000, at("O", "접근")), (1005, empty()), (1010, at("O", "도착"))]
    assert infer_station_events(history, leg, "t")[0][0] == 1005.0

def test_infer_station_events_uses_latest_run_when_progress_resets():
    history = [(900, at("C", "도착")), (1000, at("O", "접근")), (1010, at("O", "도착"))]
    assert infer_station_events(history, leg, "t")[0][0] == 1005.0

def test_infer_station_events_unknown_for_express_leg_with_skipped_stops():
    snap_names = ("O", "skip", "B")  # leg O → B skips a physical station
    history = [(1000, at_in(snap_names, "O", "접근")), (1010, at_in(snap_names, "O", "도착"))]
    assert infer_station_events(history, _leg("3호선", "O", "B"), "t") == [(None, None), (None, None)]

def test_merge_event_times_shifts_unknown_by_nearest_known():
    assert merge_event_times([910, 940], [None, 1007.5], upper=1030) == [977.5, 1007.5]

def test_merge_event_times_returns_schedule_when_nothing_known():
    assert merge_event_times([910, 940], [None, None], upper=1030) == [910, 940]

def test_merge_event_times_is_strictly_increasing_below_upper():
    times = merge_event_times([10, 20, 30], [50.0, 40.0, None], upper=45)
    assert all(b - a >= 1 for a, b in zip(times, times[1:]))
    assert times[-1] <= 44
```

Test helpers: `at(name, status)` returns a 3-station `O, B, C` snapshot with train `t` in the `dn` bucket at `name`; `empty()` returns it with no trains; `at_in(names, name, status)` does the same over an explicit station-name tuple.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_platform_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.platform_history'`

- [ ] **Step 3: Implement the three functions in `app/platform_history.py`** per the rules above.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_platform_history.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/platform_history.py tests/test_platform_history.py
git commit -m "feat: infer train station events from recorded line snapshots"
```

---

### Task 3: Snapshot buffer, platform watcher, single-fetch API

**Files:**
- Modify: `app/journey.py` (imports, constants, `ActiveJourney.__init__`, `JourneyManager.__init__`, `resume_from_db`, `start_journey`, `board`, `missed_train`, `cancel`, `_complete_leg`; new methods next to `_start_tracker`)
- Modify: `app/api.py:21,128-150,265-330`
- Modify: `app/subway_feed.py` (delete `fetch_arrivals`, `fetch_onboard_candidates`, `fetch_boarding_context`)
- Create: `tests/conftest.py`
- Test: `tests/test_journey.py`, `tests/test_api_routes.py`, `tests/test_subway_feed.py`

**Interfaces:**
- Consumes: Task 1's `*_from_snapshot` functions.
- Produces:
  - `ActiveJourney.platform_snapshots: collections.deque[tuple[float, list[StationSnapshot]]]` (`maxlen=PLATFORM_BUFFER_MAX`)
  - `async JourneyManager.leg_snapshot(max_age: float) -> list[StationSnapshot]` — raises `SubwayApiError` on feed failure, `ValueError` if no active journey or the leg has no `LINE_KEY_TO_API_ID`.
  - `JourneyManager._start_platform_watch() -> None`, `JourneyManager._stop_platform_watch() -> None`, `async JourneyManager._platform_watch_loop() -> None`, attribute `JourneyManager._platform_task: asyncio.Task | None`
  - `journey_mod.fetch_line_snapshot` is imported into `app/journey.py` (tests monkeypatch it there).

`leg_snapshot` behaviour: under one `asyncio.Lock`, return the newest buffered snapshot if `time.time() - fetched_at < max_age` (strict, so `max_age=0` always fetches); otherwise fetch, then append `(time.time(), snapshot)` only if `self.active is j`, `j.leg_idx` is unchanged and `j.state == AWAITING_BOARD`; return the snapshot.

Watcher: started at every entry to `awaiting_board` on a covered leg (`start_journey`, `_complete_leg` non-last branch, `missed_train`, `resume_from_db` when the resumed state is `AWAITING_BOARD`); stopped in `board`, `begin_realtime_tracking_from_onboard`, `cancel`. Loop: `deadline = time.time() + PLATFORM_WATCH_MAX_SECONDS`; while the journey/leg is unchanged, still `AWAITING_BOARD` and before the deadline, `await self.leg_snapshot(max_age=PLATFORM_POLL_SECONDS)` (log and continue on `SubwayApiError`), then `await asyncio.sleep(PLATFORM_POLL_SECONDS)`. Use `time.time()`, not `time.monotonic()` (tests replace `journey_mod.time` with fakes exposing only `time()`). `_stop_platform_watch` mirrors `_stop_tracker` (never cancels the current task). `board` and `begin_realtime_tracking_from_onboard` clear `platform_snapshots` (Task 4 reads it first).

API: `arrivals` → `snapshot = await manager.leg_snapshot(max_age=ARRIVALS_SNAPSHOT_MAX_AGE)` (`SubwayApiError` → 502), then all three lists from that one snapshot. `board` (both branches) → `await manager.leg_snapshot(max_age=0)`. Delete `_fetch_leg_arrivals`, `_fetch_leg_onboard`, `_fetch_leg_context` and the "optional … lookup failed" fallbacks (derivations can no longer fail separately).

- [ ] **Step 1: Add the no-live-feed guard, make the `manager` fixture disable the watcher, add a `live_manager` fixture**

Create `tests/conftest.py` with an autouse fixture `no_live_subway_feed(monkeypatch)` that replaces `app.journey.fetch_line_snapshot` with an async fake returning `[]` (an empty line: no trains, so inference falls back) and appends each call's `base_url` to a list it yields. Patch only `app.journey`'s imported name, not `app.subway_feed.fetch_line_snapshot`, so the existing respx tests of `fetch_line_snapshot`/`locate_train` keep exercising the real parser against mocked HTTP. Tests that need specific snapshots override it with their own `monkeypatch.setattr(journey_mod, "fetch_line_snapshot", …)` (later patch wins). Add `test_platform_polling_never_reaches_a_real_feed(manager, no_live_subway_feed)` asserting every recorded `base_url == "http://subway.test"` after a directly constructed `JourneyManager(manager.db, manager.settings)` resumes an `awaiting_board` journey (watcher enabled on that instance).

Then in `tests/test_journey.py`, the existing `manager` fixture also does `monkeypatch.setattr(manager, "_start_platform_watch", lambda: None)` so existing tests stay network-free. Add `live_manager` (same construction, watcher enabled, `journey_mod.PLATFORM_POLL_SECONDS` patched to `0.01`, `journey_mod.fetch_line_snapshot` patched to a fake returning a 3-station 3호선 snapshot and incrementing `manager.fetch_count`, which the fixture initialises to 0) that calls `manager._stop_platform_watch()` on teardown.

- [ ] **Step 2: Write the failing manager tests** (`tests/test_journey.py`)

```python
async def test_platform_watch_records_snapshots_while_awaiting_board(live_manager, monkeypatch):
    monkeypatch.setattr(live_manager, "_start_tracker", lambda: None)
    j = await live_manager.start_journey(make_itinerary())
    await asyncio.sleep(0.05)
    assert len(j.platform_snapshots) >= 2
    await live_manager.board("3001")
    assert live_manager._platform_task is None and len(j.platform_snapshots) == 0
    fetched = live_manager.fetch_count; await asyncio.sleep(0.05)
    assert live_manager.fetch_count == fetched

async def test_platform_watch_not_started_for_uncovered_leg(live_manager): ...  # line_key=None → _platform_task is None
async def test_platform_watch_stops_after_max_duration(live_manager, monkeypatch): ...  # MAX=0.03 → task done after 0.1 s
async def test_platform_watch_continues_after_feed_error(live_manager, monkeypatch): ...  # first fetch raises SubwayApiError, later one buffered
async def test_platform_watch_restarts_on_transfer_missed_and_resume(live_manager, monkeypatch): ...  # task running after alight→next leg, after missed_train, and on a fresh manager's resume_from_db; stopped after cancel
async def test_leg_snapshot_reuses_recent_snapshot(live_manager): ...  # two leg_snapshot(10) → 1 fetch; leg_snapshot(0) → 2
async def test_concurrent_leg_snapshot_calls_share_one_fetch(live_manager): ...  # slow fake fetch; gather two leg_snapshot(10) → 1 fetch
async def test_leg_snapshot_drops_result_when_leg_changed_mid_fetch(live_manager): ...  # cancel journey during slow fetch → new journey's buffer stays empty
async def test_picker_snapshot_still_buffers_after_watch_cap(live_manager, monkeypatch): ...  # after cap, leg_snapshot(0) appends while AWAITING_BOARD
```

- [ ] **Step 3: Rewrite the API tests** (`tests/test_api_routes.py`)

Add a `FakeFeedManager` (attributes `active=SimpleNamespace(leg=…)`, `snapshot_calls: list[float]`; `async leg_snapshot(max_age)` records `max_age` and returns a configured parsed snapshot or raises a configured error; optional `board` / `begin_realtime_tracking_from_onboard` recorders). Monkeypatch `app.api.arrivals_from_snapshot` / `onboard_candidates_from_snapshot` / `boarding_context_from_snapshot` where a test only checks wiring. Then:
- `test_arrivals_returns_stable_arrival_and_onboard_candidate_lists` → assert `snapshot_calls == [10]` and the same response body.
- Replace both `..._when_optional_..._lookup_fails` tests with `test_arrivals_returns_502_when_feed_fails`.
- `test_board_rechecks_that_selected_train_is_still_approaching`, `test_retroactive_board_*` → feed via `leg_snapshot`; assert every board call used `max_age == 0`.

- [ ] **Step 4: Convert feed tests and delete the wrappers**

In `tests/test_subway_feed.py`, rewrite each `fetch_arrivals` / `fetch_boarding_context` / `fetch_onboard_candidates` test to call the `*_from_snapshot` function on `_parse_snapshot(_payload(...))`, same assertions; drop the respx mocks and the `..._returns_empty_for_uncovered_line` tests (the API guards uncovered legs). Delete the three wrappers from `app/subway_feed.py`.

- [ ] **Step 5: Run to verify the new tests fail**

Run: `uv run pytest tests/test_journey.py -k "platform_watch or leg_snapshot or watch_cap" tests/test_api_routes.py -v`
Expected: FAIL (`AttributeError: ... 'platform_snapshots'` / `'leg_snapshot'`)

- [ ] **Step 6: Implement buffer, `leg_snapshot`, watcher lifecycle and the API switch** as specified above.

- [ ] **Step 7: Run the full Python suite**

Run: `uv run pytest`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add app/journey.py app/api.py app/subway_feed.py tests/conftest.py tests/test_journey.py tests/test_api_routes.py tests/test_subway_feed.py
git commit -m "feat: record platform line snapshots server-side and share one fetch per picker refresh"
```

---

### Task 4: Use inferred events in retroactive boarding

**Files:**
- Modify: `app/journey.py:335-475` (`begin_realtime_tracking_from_onboard`), `app/journey.py:516-552` (`_reconstruct_history`)
- Test: `tests/test_journey.py`

**Interfaces:**
- Consumes: `infer_station_events`, `merge_event_times` (Task 2); `ActiveJourney.platform_snapshots` (Task 3).
- Produces: `JourneyManager._write_history(j: ActiveJourney, arrivals: list[float], departures: list[float]) -> None` — requires `len(departures) == len(arrivals) - 1`; emits the origin at `arrivals[0]`, then for each `s` the station at `departures[s]` and the Tmap geometry of hop `s → s+1` spread by `_distance_fractions` between `departures[s]` and `arrivals[s+1]`; all `estimated=True`. `_reconstruct_history` keeps its signature and delegates its full-segment part to `_write_history` (the partial `between` segment stays as is), so `_rebuild_if_train_fell_behind` is unchanged.

Algorithm in `begin_realtime_tracking_from_onboard` for `departed` / `arrived` / `approaching` (`between` unchanged, buffer still cleared):
1. Compute today's `origin_arrival` and `budget, dwell` as now. Schedule events: `arr_s = origin_arrival + s * budget`, `dep_s = arr_s + dwell`.
2. Past events = `[arr_0, dep_0, …, arr_{c-1}, dep_{c-1}]` for current station `c = station_index`; for `departed` at `c == 0` it is `[arr_0]` only. `upper` = current arrival (`observed_at - dwell` for departed, `observed_at` otherwise); for `departed` at `c == 0`, `upper = observed_at`.
3. `events = infer_station_events(list(j.platform_snapshots), j.leg, candidate.train_no)`; inferred list = the same interleaving of `events[s][0]`, `events[s][1]`; `merged = merge_event_times(schedule, inferred, upper)`; clear the buffer.
4. `origin_arrival = merged[0]` feeds `_log_transfer_walk` and `leg_started_at` (`int(...)`).
5. `_write_history(j, arrivals=merged[0::2] + ([] if c == 0 else [current_arrival]), departures=merged[1::2])` — for `c == 0` departed: arrivals `[merged[0]]`, departures `[]`.
6. Anchor, `logged_idx`, `last_arrival_time`, live point and DB update exactly as today.

- [ ] **Step 1: Write the failing tests** (`tests/test_journey.py`; `make_itinerary()` has budget 120 s, dwell 30 s)

```python
def _line3_snap(train_at: str, status: str) -> list[StationSnapshot]:
    names = ["양재", "매봉", "도곡"]
    return [StationSnapshot(n, [], [TrainEntry(status, "일반", "도곡", "3001")] if n == train_at else []) for n in names]

async def test_onboard_uses_platform_snapshots_for_origin_arrival_and_departure(manager, monkeypatch):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    journey.platform_snapshots.extend([
        (1000, _line3_snap("양재", "접근")), (1015, _line3_snap("양재", "도착")),
        (1030, _line3_snap("양재", "도착")), (1045, _line3_snap("양재", "출발")),
        (1060, _line3_snap("매봉", "접근")), (1075, _line3_snap("매봉", "도착")),
    ])
    await manager.begin_realtime_tracking_from_onboard(OnboardTrain(
        train_no="3001", line_name="3호선", terminus="도곡", direction_label="도곡 방면",
        station_name="매봉", station_index=1, status="arrived", observed_at=1075,
        matches_direction=True, is_express=False))
    points = manager.db.get_points(journey.id)
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=1007, estimated=True) in points
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=1037, estimated=True) in points
    assert points[-1] == TrackPoint(lat=37.4870, lon=127.0468, ts=1075, estimated=False)
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 1007
    assert journey.anchor_phase == "station" and len(journey.platform_snapshots) == 0

async def test_onboard_partial_snapshots_shift_schedule_for_unknown_origin_arrival(manager, monkeypatch):
    # buffer starts with the train already 도착 at 양재: arrival unknown, departure 1007.5
    # departed at 매봉, observed 1060 → arr0 = 910 + (1007.5 - 940) = 977.5
    ...
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=977, estimated=True) in points
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=1007, estimated=True) in points
    assert TrackPoint(lat=37.4870, lon=127.0468, ts=1030, estimated=True) in points  # current arrival still observed_at - dwell
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 977

async def test_onboard_ignores_snapshot_bracket_wider_than_limit(manager, monkeypatch):
    # 접근@1000, 도착@1100, 출발@1110; arrived at 매봉 observed 1200 → arr0 = 1080 + (1105 - 1110) = 1075
    ...
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=1075, estimated=True) in points
    assert TrackPoint(lat=37.4837, lon=127.0354, ts=1105, estimated=True) in points

async def test_onboard_transfer_walk_ends_at_inferred_origin_arrival(manager, monkeypatch):
    # Reuse the two-leg 2호선(uncovered)→4호선 itinerary from
    # test_onboard_later_leg_retains_transfer_walk_before_reconstructed_history (extract it to
    # a module helper `_transfer_itinerary()` and use it in both tests). FakeTime.time() = 900.
    # 4호선 snapshots [사당, 충무로, 서울역], train 4001 in `dn`:
    # 접근@1000, 도착@1010 → arrival 1005; 도착@1030, 출발@1040 → departure 1035.
    # Candidate: arrived at 충무로 (index 1), observed_at 1200. Schedule alone would put the origin at 1080.
    # The walk (leg_idx 0) ends at the origin timestamp; leg-0 ride points are all ≤ 900.
    ...
    assert max(p.ts for p in manager.db.get_points(journey.id, leg_idx=0)) == 1005
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 1005

async def test_resumed_snapshot_inferred_arrival_keeps_station_anchor(manager, monkeypatch):
    # same setup as the first test, then a fresh JourneyManager.resume_from_db()
    assert resumed.active.anchor_idx == 1 and resumed.active.anchor_phase == "station"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_journey.py -k "platform_snapshots or partial_snapshots or bracket_wider or inferred_origin or snapshot_inferred" -v`
Expected: FAIL (origin points at schedule times, e.g. `ts=955` instead of `1007`)

- [ ] **Step 3: Implement `_write_history`, delegate `_reconstruct_history` to it, and wire steps 1–6 above into `begin_realtime_tracking_from_onboard`.**

- [ ] **Step 4: Run the full Python suite**

Run: `uv run pytest`
Expected: all PASS — including every existing `test_onboard_*`, `test_resumed_onboard_*` and `test_alight_far_ahead_*` test unchanged (an empty buffer must reproduce today's history exactly).

- [ ] **Step 5: Commit**

```bash
git add app/journey.py tests/test_journey.py
git commit -m "feat: infer retroactive boarding times from recorded platform snapshots"
```

---

### Task 5: Documentation

**Files:**
- Modify: `AGENTS.md` (Layout, "Core flow" retroactive paragraph, Data sources table, Gotchas)
- Modify: `README.md:235-250`

- [ ] **Step 1: Update `AGENTS.md`**
  - Layout: replace the stale `seoul.py` line with `subway_feed.py` (self-hosted line-snapshot feed client + pure derivations) and add `platform_history.py` (snapshot → station-event inference).
  - Core flow: after "Per leg: `AWAITING_BOARD` …", add: while a covered leg is `awaiting_board`, `_platform_watch_loop` fetches the line every `PLATFORM_POLL_SECONDS` (cap `PLATFORM_WATCH_MAX_SECONDS`) into the in-memory `ActiveJourney.platform_snapshots`; `/arrivals` and `/board` read through `leg_snapshot()` (one fetch per picker refresh).
  - Retroactive paragraph: snapshot-inferred origin/past-station times (bracket midpoints, ≤ `MAX_BRACKET_SECONDS`), schedule fallback shifted to the nearest known event, current station pinned to the observation, buffer discarded after use and lost on restart; every reconstructed point still `estimated=True`.
  - Gotchas: tests' `manager` fixture disables the watcher; use `live_manager` for watcher tests. `tests/conftest.py`'s autouse `no_live_subway_feed` keeps platform polling off the network in every test — never remove it or point test `Settings` at a real `SUBWAY_API_URL`, since each feed request is a live Seoul API call.
- [ ] **Step 2: Update `README.md:235-250`** — the "already boarded" bullet: when the tracker was watching the platform beforehand, the reconstructed boarding time comes from recorded train positions (±~8 s); otherwise it is the schedule estimate as before.
- [ ] **Step 3: Verify**

Run: `uv run pytest` — Expected: all PASS.
Run: `grep -n "seoul.py" AGENTS.md` — Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: document platform snapshot recording and boarding inference"
```
