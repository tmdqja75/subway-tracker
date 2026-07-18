# Next.js Rider UI Implementation Plan

> **For Hermes:** Execute only after the user explicitly approves this plan. Use the validated specification at `docs/2026-07-17-nextjs-rider-ui-specs.md` as the acceptance contract.

**Goal:** Replace the vanilla rider UI with a mobile-first Next.js static export while preserving the FastAPI API, journey behavior, and legacy `/debug.html` diagnostic page.

**Architecture:** A TypeScript Next.js App Router project in `frontend/` owns the rider UI. It calls the existing same-origin `/api/*` endpoints; FastAPI remains the state authority. Development uses a Next rewrite to FastAPI, and Docker builds a static Next export that FastAPI serves from its existing root mount. The old debug page stays static and is separated from legacy rider assets.

**Tech stack:** Next.js `16.2.10`, React `19.2.7`, TypeScript `7.0.2`, Tailwind CSS `4.3.3`, Leaflet `1.9.4`, Vitest `4.1.10`, React Testing Library `16.3.2`, Playwright `1.61.1`, FastAPI, Docker multi-stage builds.

**Constraints:** Do not alter API schemas or journey/tracking logic. Do not migrate `/debug.html`. Do not add SSR, CORS, authentication, or a second production server. Do not commit unless explicitly asked.

---

## Task 1: Establish the frontend workspace and deterministic toolchain

**Objective:** Create an independently buildable Next.js TypeScript project that exports static files and proxies development API calls.

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/postcss.config.mjs`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/test/setup.ts`
- Create: `frontend/.gitignore`
- Modify: `.gitignore`

**Step 1: Add the failing build-contract test.**

Create `frontend/test/build-contract.test.ts` that checks the Next configuration exposes `output: "export"` and a development rewrite for `/api/:path*` to `http://127.0.0.1:8000/api/:path*`.

**Step 2: Run the test to verify failure.**

Run: `cd frontend && npm test -- build-contract.test.ts`

Expected: failure because the workspace/configuration does not exist.

**Step 3: Create the workspace.**

Pin the runtime and test dependencies to the versions listed in this plan; include `leaflet`, `@types/leaflet`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, and `@playwright/test`. Add scripts for `dev`, `build`, `start`, `test`, `test:watch`, `test:e2e`, and `typecheck`. Configure static output and the local API rewrite in `next.config.ts`. Ignore `frontend/node_modules/`, `frontend/.next/`, `frontend/out/`, Playwright reports, and test artifacts at the repository root.

**Step 4: Verify baseline tooling.**

Run:

```bash
cd frontend
npm ci
npm run typecheck
npm test -- build-contract.test.ts
npm run build
```

Expected: typecheck, test, and static export build pass.

---

## Task 2: Define API contracts and a safe JSON client

**Objective:** Encode every currently consumed FastAPI payload in TypeScript without changing the backend.

**Files:**
- Create: `frontend/lib/types.ts`
- Create: `frontend/lib/api.ts`
- Create: `frontend/test/fixtures.ts`
- Create: `frontend/lib/api.test.ts`

**Step 1: Write failing API-client tests.**

Cover successful JSON requests, server error detail extraction, network failures, `cache: "no-store"`, request bodies, and abort signal forwarding.

**Step 2: Implement type contracts.**

Model `Station`, `LegStation`, `SubwayLeg`, `Itinerary`, `ArrivingTrain`, `TrainStatus`, transfer status, and the full current-journey snapshot. Match `JourneyManager.snapshot()` in `app/journey.py:729-791` exactly, including `leg.covered`, `tracking_mode`, `trip.legs`, and retry metadata. Keep all route coordinate values as `[lat, lon]` tuples.

**Step 3: Implement explicit endpoint functions.**

Provide typed wrappers for all current rider endpoints:

- `GET /api/stations/search?q=`
- `POST /api/routes`
- `POST /api/journeys`
- `GET /api/journeys/current`
- `GET /api/journeys/current/arrivals`
- `POST /api/journeys/current/board`
- `POST /api/journeys/current/alight`
- `POST /api/journeys/current/missed`
- `POST /api/journeys/current/cancel`
- `POST /api/journeys/current/retry-push`

Map network failures to a Korean action-oriented `ApiError`; preserve backend `detail` strings for actionable 4xx/5xx responses.

**Step 4: Verify.**

Run: `cd frontend && npm test -- lib/api.test.ts && npm run typecheck`

Expected: all API client tests pass.

---

## Task 3: Build the mobile-first shell and premium design system

**Objective:** Establish semantic, accessible, responsive primitives before implementing journey states.

**Files:**
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/page.tsx`
- Create: `frontend/app/globals.css`
- Create: `frontend/components/journey-app.tsx`
- Create: `frontend/components/ui/button.tsx`
- Create: `frontend/components/ui/card.tsx`
- Create: `frontend/components/ui/status-banner.tsx`
- Create: `frontend/components/journey-stepper.tsx`
- Create: `frontend/components/journey-stepper.test.tsx`

**Step 1: Write failing tests for the shell.**

Assert Korean document metadata, persistent journey steps, an active-state indicator, keyboard-visible focus behavior, semantic button variants, and reduced-motion CSS coverage.

**Step 2: Implement the visual foundation.**

Use Tailwind/global CSS tokens for navy/neutral/semantic palettes, Korean-first font fallback, safe-area padding, 44px minimum interactive targets, responsive content width, focus rings, and reduced-motion handling. Implement the premium card hierarchy with restrained shadows and route/status accents. Keep the page itself server-renderable but render the interactive `JourneyApp` as a client component.

**Step 3: Verify at narrow width.**

Run: `cd frontend && npm test -- components/journey-stepper.test.tsx && npm run build`

Expected: unit tests and static build pass.

---

## Task 4: Implement station search and route selection

**Objective:** Replace the legacy search and route list with touch-friendly, race-safe React components.

**Files:**
- Create: `frontend/components/station-autocomplete.tsx`
- Create: `frontend/components/station-autocomplete.test.tsx`
- Create: `frontend/components/journey-search.tsx`
- Create: `frontend/components/journey-search.test.tsx`
- Create: `frontend/components/route-list.tsx`
- Create: `frontend/components/route-list.test.tsx`
- Modify: `frontend/components/journey-app.tsx`

**Step 1: Write failing tests.**

Cover a 250 ms debounced station query, invalidating a selected station after typing, station+line display, keyboard selection, required origin/destination validation, loading/inline error state, every returned route option, and exact itinerary submission.

**Step 2: Implement search behavior.**

Store selected station IDs separately from input labels. Abort or sequence autocomplete requests so stale results cannot replace current input results. Maintain the existing backend payload fields `start`, `end`, `start_id`, and `end_id`.

**Step 3: Implement route cards.**

Present duration first, then transfers, walking time, fare, summary, and a clearly labelled realtime-support caveat. Make the complete card a semantic button; do not use click-only non-interactive containers.

**Step 4: Verify.**

Run: `cd frontend && npm test -- components/station-autocomplete.test.tsx components/journey-search.test.tsx components/route-list.test.tsx`

Expected: tests cover the existing station-ID and all-route-selection regressions from `tests/test_frontend_map.py`.

---

## Task 5: Implement authoritative journey polling and the boarding flow

**Objective:** Render `idle` and `awaiting_board` states from backend snapshots without stale arrival data.

**Files:**
- Create: `frontend/hooks/use-current-journey.ts`
- Create: `frontend/hooks/use-current-journey.test.tsx`
- Create: `frontend/components/train-picker.tsx`
- Create: `frontend/components/train-picker.test.tsx`
- Modify: `frontend/components/journey-app.tsx`

**Step 1: Write failing polling tests.**

Use fake timers to verify no polling on idle, a 15-second arrival cadence while awaiting boarding, cleanup on state change/unmount, and request sequencing/abort behavior.

**Step 2: Implement current-journey polling.**

Fetch immediately on mount and after actions. Use a state-aware schedule: 15 seconds for arrival data while awaiting boarding, five seconds for the active ride, and 500 ms only while pushing. Never allow a late response to overwrite a newer snapshot.

**Step 3: Implement train picker behavior.**

Show route, boarding station, destination, and leg count. Render only trains with `matches_direction === true`; display API-supplied `arrival_msg` whenever no station count is supplied. Show an explicit timer-mode option if `leg.covered` is false. Re-fetch arrivals and submit `train_no` through the existing board endpoint; surface 409 stale-selection errors inline.

**Step 4: Verify.**

Run: `cd frontend && npm test -- hooks/use-current-journey.test.tsx components/train-picker.test.tsx`

Expected: coverage replaces the old wrong-direction and stale-response browser-script assertions.

---

## Task 6: Implement client-only live tracking and mobile journey actions

**Objective:** Present the active ride without accessing Leaflet or browser globals during static generation.

**Files:**
- Create: `frontend/components/maps/live-journey-map.tsx`
- Create: `frontend/components/maps/live-journey-map.test.tsx`
- Create: `frontend/components/live-journey.tsx`
- Create: `frontend/components/live-journey.test.tsx`
- Modify: `frontend/components/journey-app.tsx`

**Step 1: Write failing UI tests.**

Cover realtime versus timer labels, no-train waiting state, station/leg display, recorded-point count, primary alight action, missed-train recovery, and cancellation confirmation.

**Step 2: Implement the map boundary.**

Mark the map component client-only. Initialize Leaflet inside an effect, add OSM tiles, draw Tmap shape or station fallback geometry, reuse a single train marker, and clean up the map on unmount or leg transition. Do not import/use `window`, `document`, or Leaflet map APIs outside the client/effect boundary.

**Step 3: Implement safe journey controls.**

Keep “I got off” primary. Require an accessible confirmation for cancellation, keep wrong-train secondary, prevent duplicate submissions while an action is active, then refresh the authoritative snapshot after success.

**Step 4: Verify.**

Run: `cd frontend && npm test -- components/live-journey.test.tsx components/maps/live-journey-map.test.tsx && npm run build`

Expected: tests pass and static export has no browser-global build failure.

---

## Task 7: Implement transfer progress, completion, and recoverable failure states

**Objective:** Make Reitti delivery status understandable and retry-safe.

**Files:**
- Create: `frontend/components/maps/completed-journey-map.tsx`
- Create: `frontend/components/transfer-status.tsx`
- Create: `frontend/components/transfer-status.test.tsx`
- Modify: `frontend/components/journey-app.tsx`

**Step 1: Write failing tests.**

Cover pushing progress values; completed 100% state; failure message/detail; retained-data explanation; retry disabling during request; retry success refresh; and full route coordinate composition including transfer-walk shapes.

**Step 2: Implement the transfer screen.**

Use confirmed backend values only: `sent_points`, `total_points`, `remaining_points`, and `progress_percent`. Add accessible progress-bar attributes. In failure, show the backend’s safe Korean message, technical detail, and the retained-record summary. Disable the retry button during the request; retry only through the existing `POST /api/journeys/current/retry-push` endpoint.

**Step 3: Implement the completed map.**

Use the full `trip.legs` data, route shapes with station fallbacks, and post-leg walking geometry. Fit start/end markers to the full trip bounds and clean up on journey change.

**Step 4: Verify.**

Run: `cd frontend && npm test -- components/transfer-status.test.tsx && npm run typecheck`

Expected: transfer/retry behavior matches current backend snapshot semantics.

---

## Task 8: Preserve the debug page while removing legacy rider assets

**Objective:** Decouple the retained static diagnostic page from rider UI files so the Next export can own `/`.

**Files:**
- Create: `static/debug.css`
- Modify: `static/debug.html`
- Delete: `static/index.html`
- Delete: `static/app.js`
- Delete: `static/style.css`
- Delete: `static/transfer-preview.html`
- Modify: `tests/test_frontend_map.py`

**Step 1: Write/adjust the debug asset test.**

Replace assertions that inspect `static/app.js`/`static/index.html` with assertions that `/debug.html` references `debug.css` and `debug.js` and preserves the current map/timeline markup.

**Step 2: Split debug styles.**

Move only the CSS selectors used by `static/debug.html` and `static/debug.js` into `static/debug.css`, including the shared header/card/control/map/timeline/legend styles. Update the debug page link from `style.css` to `debug.css`.

**Step 3: Remove obsolete rider files.**

Delete the vanilla rider document, script, combined stylesheet, and one-off transfer preview once equivalent Next test coverage exists. Retain `static/debug.html` and `static/debug.js` unchanged except for their stylesheet reference.

**Step 4: Verify.**

Run: `uv run pytest tests/test_frontend_map.py tests/test_debug_locations.py`

Expected: the debug view contract remains tested without the legacy rider harness.

---

## Task 9: Wire production static export into Docker and document development workflow

**Objective:** Serve the Next export and retained debug assets from the existing FastAPI process in production.

**Files:**
- Modify: `Dockerfile`
- Modify: `README.md`
- Create: `tests/test_static_delivery.py`

**Step 1: Write failing static-delivery tests.**

Add a focused test that uses a temporary static directory with a generated root `index.html`, `_next` asset path, and `debug.html`, mounts the application’s static-serving configuration, and proves `/`, `/_next/...`, `/debug.html`, and `/api/*` routing coexist. The API route must win over the root static mount.

**Step 2: Implement multi-stage Docker build.**

Add a Node builder stage that copies frontend manifests first, uses `npm ci`, copies frontend source, and runs `npm run build`. In the Python runtime stage, retain existing `uv` installation and runtime behavior. Copy only `static/debug.html`, `static/debug.js`, and `static/debug.css`, then copy `frontend/out/` into `/app/static/`. Continue exposing port 8000 and preserve the current health check and unprivileged user.

**Step 3: Document commands.**

Update README development instructions to run FastAPI on port 8000 and `cd frontend && npm run dev` for the rider UI. Document production’s single-origin behavior, `npm ci`, `npm run build`, frontend tests, and the preserved `/debug.html` URL.

**Step 4: Verify Docker behavior.**

Run:

```bash
docker build -t subway-tracker:next-ui .
docker run --rm -d --name subway-tracker-next-ui -p 18000:8000 \
  --env-file .env -e DB_PATH=/tmp/tracker.db -e STATIONS_CSV=/app/data/stations.csv \
  subway-tracker:next-ui
curl --fail http://127.0.0.1:18000/
curl --fail http://127.0.0.1:18000/debug.html
curl --fail http://127.0.0.1:18000/api/stations/search?q=%EA%B0%95
```

Expected: root is the Next app, debug remains available, and API routing still works. Stop/remove the test container afterward.

---

## Task 10: Add browser-level mobile flow coverage

**Objective:** Verify the journey workflow at phone dimensions against mocked API responses and production-safe client behavior.

**Files:**
- Create: `frontend/e2e/fixtures.ts`
- Create: `frontend/e2e/rider-flow.spec.ts`
- Modify: `frontend/playwright.config.ts`

**Step 1: Define route fixtures.**

Mock existing `/api/*` endpoint shapes rather than inventing frontend-only APIs. Include snapshots for `idle`, `awaiting_board`, `on_train`, `pushing`, `completed`, and `push_failed`.

**Step 2: Write browser tests at an iPhone-sized viewport.**

Cover:

1. station selection retains the exact station ID and submits route search;
2. every returned route option is selectable;
3. only direction-eligible trains are actionable;
4. timer fallback is clear and boardable;
5. live screen shows journey status and actions without horizontal overflow;
6. transfer progress updates correctly;
7. retry failure disables duplicate requests and refreshes the current state;
8. a reload resumes from the mocked current snapshot;
9. reduced-motion mode does not block state updates.

**Step 3: Verify.**

Run: `cd frontend && npx playwright install --with-deps chromium && npm run test:e2e`

Expected: browser suite passes at the mobile viewport.

---

## Task 11: Run final verification and review the migration diff

**Objective:** Prove the change meets the specification without touching backend tracking behavior.

**Files:**
- Modify only files required by prior tasks.

**Step 1: Run frontend verification.**

```bash
cd frontend
npm ci
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

**Step 2: Run backend verification.**

```bash
uv run pytest
```

**Step 3: Run build and delivery verification.**

```bash
git diff --check
docker build -t subway-tracker:next-ui .
```

Run the Task 9 smoke checks against the container and remove it afterward.

**Step 4: Review scope.**

Inspect `git diff --stat` and `git diff`. Confirm no changes to `app/api.py`, `app/journey.py`, `app/models.py`, `app/db.py`, `app/seoul.py`, `app/tmap.py`, or the debug page behavior beyond extracting its CSS.

**Step 5: Report evidence.**

Report exact commands and pass/fail output, the tested mobile viewport flows, and any blocker. Do not claim completion without passing the frontend build, backend tests, and production static-serving checks.
