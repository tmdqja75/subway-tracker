# Retroactive Train Tracking Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Let a rider select a correctly directed train already past the current leg origin, reconstruct the earlier path and station pauses as estimates, and continue normal live tracking.

**Architecture:** Extend the current `arrivals` response with a second candidate collection sourced from Seoul realtime positions. Keep all eligibility and reconstruction logic in the backend so the frontend never authorizes a train itself. Persist the reconstructed path in the existing points table; expose an authoritative `history_estimated` snapshot flag rather than adding a new historical-event persistence surface in this increment.

**Tech Stack:** FastAPI, Pydantic, SQLite, httpx/respx, pytest; Next.js/React, TypeScript, Vitest, Playwright.

---

### Task 1: Model and select valid onboard candidates

**Objective:** Define a typed realtime-position candidate and extract only positions which are demonstrably on the active leg, travelling in its direction, after origin, and before the alighting station.

**Files:**
- Modify: `app/models.py`
- Modify: `app/seoul.py`
- Test: `tests/test_seoul.py`

**Step 1: Write failing tests**

Create fixtures containing same-line positions at origin, intermediate station, beyond destination, opposite terminus, and a between-station state. Assert that only intermediate/current-leg candidates are returned and that status `3` maps to the preceding segment.

**Step 2: Run red test**

`uv run pytest tests/test_seoul.py -k onboard -q`

**Step 3: Implement minimal code**

Add `OnboardTrain` with `train_no`, `terminus`, `direction_label`, `station_name`, `station_index`, `status`, `observed_at`, and `matches_direction`. Implement a pure `find_onboard_trains(positions, leg)` helper in `seoul.py`; normalize all station names and use `statnTnm` to verify direction. Require the resolved active location to be `0 < location < final_station_index`.

**Step 4: Run green test**

`uv run pytest tests/test_seoul.py -k onboard -q`

### Task 2: Reconstruct estimated history before realtime tracking starts

**Objective:** Seed a past-origin train with correctly ordered, estimated geometry points and an initial live anchor.

**Files:**
- Modify: `app/journey.py`
- Test: `tests/test_journey.py`

**Step 1: Write failing tests**

Test a train departed from an intermediate station: points begin at origin, have ordered timestamps, include repeated coordinate pause points at intermediate stations, and are estimated except the current live anchor. Add arrival and between-state tests with the expected current anchor semantics.

**Step 2: Run red test**

`uv run pytest tests/test_journey.py -k retroactive -q`

**Step 3: Implement minimal code**

Add a `board_retroactively(train_no, position)` path which:

```python
segment_budget = max(leg.section_time / max(len(leg.stations) - 1, 1), 30)
dwell = min(30, segment_budget * 0.25)
run = segment_budget - dwell
```

Uses the position observation as an anchor, walks backward over travelled segments, emits `estimated=True` points along the existing shape through `_log_segment`-compatible geometry, emits station-arrival/departure pause points, then sets `logged_idx`, `last_arrival_time`, `anchor_idx`, `anchor_phase`, and `last_status` for the normal tracker. Mark `ActiveJourney.history_estimated = True`; retain the live position anchor as non-estimated.

**Step 4: Run green test**

`uv run pytest tests/test_journey.py -k retroactive -q`

### Task 3: Return candidates and guard board requests against stale movement

**Objective:** Provide the expanded picker payload and accept only a revalidated candidate at board time.

**Files:**
- Modify: `app/api.py`
- Modify: `app/models.py`
- Test: `tests/test_api_routes.py`

**Step 1: Write failing tests**

Assert `GET /api/journeys/current/arrivals` returns `trains` and `already_onboard`. For a valid onboard board request, assert the manager receives the fresh live position and retroactive mode begins. Assert a candidate which moved past the destination or is absent produces 409.

**Step 2: Run red test**

`uv run pytest tests/test_api_routes.py -k onboard -q`

**Step 3: Implement minimal code**

Fetch arrivals and positions for covered legs; return an empty onboard list for uncovered legs. Extend `BoardRequest` with an explicit `retroactive: bool = False` flag. The backend re-fetches the selected source collection, uses the candidate helper for validation, and calls the dedicated manager method only for a valid onboard request.

**Step 4: Run green test**

`uv run pytest tests/test_api_routes.py -k onboard -q`

### Task 4: Render the separate onboarding group and estimated-history state

**Objective:** Let a rider distinguish approaching and already-onboard trains without weakening current direction/boarding locks.

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/train-picker.tsx`
- Modify: `frontend/components/train-picker.test.tsx`
- Modify: `frontend/app/globals.css`

**Step 1: Write failing UI tests**

Mock an arrivals response with one approaching and one onboard candidate. Assert separate named lists, Korean estimated-history explanation, correct `boardCurrentJourney(trainNo, true, signal)` request, and no opposite-direction card. Retain existing polling, stale response, board lock, and abort behavior.

**Step 2: Run red test**

`cd frontend && npm test -- --run components/train-picker.test.tsx`

**Step 3: Implement minimal UI**

Add `OnboardTrain`, `already_onboard`, `history_estimated`, and `retroactive` typed contracts. Keep native card buttons. Use a visually differentiated but accessible section heading and plain Korean copy explaining that only the earlier path is reconstructed; live tracking proceeds normally after selection.

**Step 4: Run green test**

`cd frontend && npm test -- --run components/train-picker.test.tsx`

### Task 5: Document and integrate

**Objective:** Keep operator/agent documentation accurate and prove production readiness.

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/2026-07-21-retroactive-train-tracking-specs.md` only if implementation changes a documented assumption

**Steps:**

1. Document candidate direction/position limits, reconstructed-point semantics, and source limitation: Seoul does not provide historical stop times.
2. Run:

```bash
uv run pytest
cd frontend && NODE_ENV=test npm test && npm run typecheck && npm run build && npm run test:e2e
```

3. Inspect `git diff --check` and the complete diff; do not commit unrelated local database backups.
