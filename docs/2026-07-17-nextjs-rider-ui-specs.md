# Next.js Rider UI Migration Specification

**Date:** 2026-07-17  
**Status:** Validated design specification — implementation has not started

## Goal

Replace the current vanilla HTML/CSS/JavaScript rider interface with a mobile-first Next.js application that provides a modern premium dashboard experience and makes the journey state, live tracking, and Reitti transfer progress easier to understand. Preserve the FastAPI backend, its JSON API contract, SQLite persistence, tracking state machine, and the existing static `/debug.html` diagnostic tool.

## Scope

### In scope

- Next.js rider UI at `/`.
- Mobile-first responsive interaction design.
- Current journey flows: station search, route choice, train selection, live tracking, manual alight/missed/cancel actions, Reitti transfer progress, transfer failure and retry.
- Retaining Leaflet for live and completed-route maps.
- A static production export served from the existing FastAPI process.
- A development proxy from Next.js to FastAPI for `/api/*`.
- Replacing the current rider frontend tests with frontend-appropriate component and browser tests.

### Out of scope

- Changes to FastAPI API endpoints or response models.
- Changes to the journey manager, realtime/timer tracking, SQLite persistence, or Reitti ingestion behavior.
- Migrating `/debug.html` or `/debug.js` to Next.js.
- Multi-user support, authentication, SSR, or an additional production web server/process.

## Architecture

Create a dedicated `frontend/` Next.js project using the App Router, TypeScript, Tailwind CSS, and locally owned reusable UI primitives. Keep FastAPI as the authoritative backend and the source of all journey state.

```text
Browser
  ├─ Next.js rider UI at /
  ├─ Legacy diagnostic UI at /debug.html
  └─ Same-origin /api/* requests
       └─ FastAPI + SQLite + Seoul/Tmap/Reitti clients
```

During development, the Next dev server proxies `/api/*` to FastAPI. In production, Next statically exports the rider application; a multi-stage Docker build copies the export into the directory FastAPI serves. This retains a single-origin, single-service deployment and avoids CORS, SSR, and a second long-running production process.

Leaflet must live behind client-only React components to prevent server/build access to browser globals. The existing debug page and its assets remain separately served.

## Rider UI states and components

The UI is server-state-driven and must resume correctly after browser reload or temporary network interruption.

| Backend state | UI | Core content |
|---|---|---|
| `idle` | Search | Origin/destination autocomplete and route search |
| `awaiting_board` | Boarding | Current leg, direction-safe arrival cards, timer fallback |
| `on_train` | Live journey | Stepper, current status, Leaflet map, manual controls |
| `pushing` | Transfer progress | Confirmed sent/total/remaining values, progress bar, full route map |
| `completed` | Completion | Delivered confirmation and entire journey map |
| `push_failed` | Recoverable failure | Retained-data message, technical detail, retry action |

Primary components:

1. Journey shell with a persistent `Search → Route → Board → Ride → Complete` stepper.
2. Station search form and accessible autocomplete list.
3. Route option cards, with duration, transfers, walk time, fare, and realtime availability.
4. Train picker, emphasizing train number, direction, terminal station, and arrival context.
5. Client-only live and completed-journey Leaflet maps.
6. Transfer progress and retry panel.
7. Shared loading, inline-error, and confirmation patterns.

## Design and mobile requirements

The visual style is a modern premium dashboard: deep transit navy, neutral surfaces, white elevated cards, restrained route-color accents, and semantic status colors. Typography must be Korean-first and make times, train identifiers, station names, and transfer percentages highly legible.

Mobile is the primary target:

- Start from narrow viewports and expand to a readable desktop max width.
- Use 44px minimum touch targets and never depend on hover.
- Keep the primary action prominent and reachable; visually isolate cancellation/destructive actions.
- Avoid dense multi-column content on phones; stack or collapse supporting data.
- Size maps deliberately so journey status and critical actions stay available.
- Handle iOS safe areas, dynamic viewport height, and keyboard/auto-complete overlap.
- Respect `prefers-reduced-motion`; motion should be short, purposeful, and inexpensive.
- Provide semantic controls, focus states, labels, and non-color-only status communication.

## Polling, concurrency, and error behavior

The new UI preserves existing server-driven cadence:

- Arrival refresh: every 15 seconds while awaiting boarding.
- Journey snapshot refresh: every 5 seconds while tracking.
- Transfer snapshot refresh: every 500 ms only while uploading.

Requests must be aborted or sequenced so late responses cannot overwrite a newer UI state. FastAPI remains authoritative for stale-train rejection and other 409 conflicts.

Errors are rendered inline and actionably rather than through browser alerts. Required cases include network failure, unavailable arrivals, stale train selection, timer-mode information, transfer progress, and retryable transfer failure. A failure must state that unsent records remain retained and show confirmed sent/total progress.

## Build and deployment

The existing Python-only Dockerfile becomes a multi-stage build:

1. Node build stage installs locked `frontend/` dependencies and builds the static export.
2. Python runtime stage retains the existing `uv` environment and backend dependencies.
3. Copy the Next export plus preserved legacy debug assets into FastAPI's static directory.
4. Continue exposing only FastAPI on port 8000.

The deployed routes are:

- `/` — Next rider UI
- `/api/*` — unchanged FastAPI API
- `/debug.html` and `/debug.js` — existing diagnostic UI

## Testing and acceptance criteria

Keep the existing API and journey test suite. Replace the Python Node-VM harness currently tied to `static/app.js` with frontend tests that cover:

- component rendering for route cards, train cards, status labels, transfer progress, and recoverable errors;
- mocked API flows for each backend journey state;
- browser smoke tests at mobile viewport sizes for search, route selection, boarding, tracking, pushing, retry, and reload recovery;
- production build output and FastAPI static serving of `/` while `/debug.html` remains available.

A migration is complete only when:

- all existing Python tests pass;
- frontend tests pass;
- the production static export builds;
- a built container can serve the rider UI and API from the same origin;
- `/debug.html` retains its current behavior;
- no journey, tracking, upload, or retry behavior regresses.
