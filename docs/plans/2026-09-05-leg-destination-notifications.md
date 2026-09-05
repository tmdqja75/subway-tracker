# Per-leg destination notifications Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Deliver one iPhone Web Push alert per eligible subway leg when its destination becomes the next stop.

**Architecture:** A root Service Worker and a persistent settings control subscribe the installed Home Screen app through the standard Push API. FastAPI stores subscriptions and durable notification/delivery records in SQLite. `JourneyManager` claims notification eligibility from authoritative tracker state and dispatches Web Push in separately tracked background tasks, never blocking station tracking or Reitti upload.

**Tech Stack:** Next.js static export, browser Service Worker/Push API/Notifications API, FastAPI, SQLite, Pydantic, `pywebpush` with VAPID, pytest/respx, Vitest.

---

## Preconditions

- Work only on branch `feat/leg-destination-notifications`.
- Preserve the existing local WIP in `stash@{0}` until the owner restores or drops it.
- Do not read, log, commit, or expose the database backup held in that stash.
- Keep `/api` registered before the root static mount.

## Task 1: Add notification configuration and Web Push dependency

**Objective:** Make Web Push server credentials explicit without exposing private material.

**Files:**
- Modify: `pyproject.toml:6-15`
- Modify: `app/config.py:9-25`
- Modify: `.env.example:1-22`
- Test: `tests/test_notification_config.py` (create)

**Step 1: Write failing configuration tests.**

Assert that `Settings` defaults notifications to disabled when no VAPID public key, private key, or contact subject is configured, and reports enabled only when all three values are non-empty. Use `_env_file=None`; do not use real keys.

**Step 2: Run the focused test.**

Run: `uv run pytest tests/test_notification_config.py -v`
Expected: FAIL because no notification settings model exists.

**Step 3: Implement the minimal configuration.**

Add `pywebpush` as a runtime dependency. Add `web_push_vapid_public_key`, `web_push_vapid_private_key`, and `web_push_vapid_subject` to `Settings`, plus a derived `web_push_enabled` property. Add blank placeholder keys and generation/rotation guidance to `.env.example`; never add a real value.

**Step 4: Re-run the focused test.**

Run: `uv run pytest tests/test_notification_config.py -v`
Expected: PASS.

## Task 2: Add additive subscription and delivery persistence

**Objective:** Persist subscriptions and per-leg eligibility/delivery state safely across restarts.

**Files:**
- Modify: `app/db.py:13-107, 307-389`
- Test: `tests/test_notifications.py` (create)
- Test: `tests/test_journey.py:35-54`

**Step 1: Write failing migration and helper tests.**

Create an old-schema SQLite database, open it with `Database`, then assert the new tables exist. Test endpoint upsert, delete, listing without secret logging, and transactional creation of one event and one delivery row per subscribed endpoint.

**Step 2: Implement additive schema and helpers.**

Create:

```sql
push_subscriptions(endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)
journey_leg_notifications(journey_id INTEGER NOT NULL, leg_idx INTEGER NOT NULL,
                          state TEXT NOT NULL, created_at INTEGER NOT NULL,
                          PRIMARY KEY (journey_id, leg_idx))
journey_leg_notification_deliveries(journey_id INTEGER NOT NULL, leg_idx INTEGER NOT NULL,
                                    endpoint TEXT NOT NULL, state TEXT NOT NULL,
                                    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                                    updated_at INTEGER NOT NULL,
                                    PRIMARY KEY (journey_id, leg_idx, endpoint))
```

Use `INSERT ... ON CONFLICT` for subscription refresh and `INSERT OR IGNORE` for the notification claim. In one transaction, a successful claim snapshots all current endpoints into delivery rows. Return only values needed by the service; endpoint/key values must never be included in log messages.

**Step 3: Run the focused tests.**

Run: `uv run pytest tests/test_notifications.py tests/test_journey.py -k 'migrate or notification' -v`
Expected: PASS.

## Task 3: Implement a testable notification sender service

**Objective:** Send a visible Korean notification through standard Web Push without blocking the event loop.

**Files:**
- Create: `app/notifications.py`
- Test: `tests/test_notifications.py`

**Step 1: Write sender tests.**

Inject a fake synchronous transport and assert it receives this UTF-8 JSON payload:

```json
{"title":"Subway Tracker","body":"다음 역은 도곡이에요. 하차를 준비하세요.","url":"/"}
```

Cover success, permanent 404/410 endpoint removal, retryable failure retrying up to the fixed budget, and exhausted failure recorded as failed. Assert no sender exception propagates into the tracker.

**Step 2: Implement the service.**

Wrap `pywebpush.webpush(subscription_info=..., data=..., vapid_private_key=..., vapid_claims={"sub": ...})` behind a transport protocol. Use `asyncio.to_thread` for each synchronous send. Retry only transient status/network failures with bounded delay; delete only permanently invalid subscriptions. Mark each durable delivery result after each final response. Do not claim display confirmation.

**Step 3: Run the focused tests.**

Run: `uv run pytest tests/test_notifications.py -v`
Expected: PASS.

## Task 4: Add typed notification API endpoints

**Objective:** Give the frontend a minimal same-origin subscription contract.

**Files:**
- Modify: `app/api.py:1-35, 141-300`
- Modify: `app/models.py:1-122`
- Test: `tests/test_api_routes.py`

**Step 1: Write API contract tests.**

Use the existing `make_app()` pattern. Cover:

- `GET /api/notifications/config` returns `{enabled: false, public_key: null}` when server configuration is incomplete.
- Configured server returns only `{enabled: true, public_key: "..."}`.
- `GET /api/notifications/subscription` returns enabled status without endpoint/key fields.
- `POST /api/notifications/subscription` validates `endpoint`, `keys.p256dh`, and `keys.auth`, then upserts.
- `DELETE /api/notifications/subscription` removes the current endpoint and returns `{ok: true}`.

**Step 2: Implement request/response models and routes.**

Keep browser subscription data only in request models and database helpers. Define explicit response models for public config and opaque subscription status. Return 409 for registration when Web Push is server-disabled; malformed subscription payloads remain FastAPI validation errors.

**Step 3: Run tests.**

Run: `uv run pytest tests/test_api_routes.py -k notification -v`
Expected: PASS.

## Task 5: Connect notification claims to all tracker entry paths

**Objective:** Trigger each eligible subway-leg event exactly once per journey from authoritative state.

**Files:**
- Modify: `app/journey.py:154-194, 280-403, 627-791, 887-922, 943-965`
- Modify: `app/main.py:37-53`
- Test: `tests/test_journey.py`

**Step 1: Write failing state-machine tests.**

Add cases for:

- realtime `departed` at `len(stations)-2` dispatches one notification;
- repeat realtime polls, local interpolation, and manager restart do not create another event;
- a two-stop subway leg alerts after origin departure;
- timer mode alerts when it enters the final segment;
- `mode != "SUBWAY"` never alerts;
- transfer leg gets a separate event;
- retroactive onboarding already departing the penultimate station or already in the final segment evaluates immediate eligibility;
- cancel, stop, and completion prevent an unclaimed later event.

**Step 2: Implement one pure eligibility helper.**

Use a helper equivalent to:

```python
def _is_one_stop_remaining(leg, status) -> bool:
    return (
        leg.mode == "SUBWAY"
        and len(leg.stations) >= 2
        and status is not None
        and (
            status.status == "departed" and status.station_index == len(leg.stations) - 2
            or status.status == "between" and status.station_index == len(leg.stations) - 1
            or status.status == "estimated" and status.station_index == len(leg.stations) - 2
        )
    )
```

Review the final implementation against actual tracker index semantics: realtime `departed` indexes the station just left; local `between` indexes the next station; timer indexes the active segment start. Do not trigger from raw station names.

**Step 3: Add non-blocking dispatch lifecycle.**

Inject an optional notification service into `JourneyManager`. On a successful durable claim, create a tracked background task and remove it with a done callback. On startup, resume only durable delivery rows that were already claimed and remain pending/retryable. Cancel/await tracked notification tasks during app shutdown without cancelling the tracker from inside itself.

**Step 4: Run tests.**

Run: `uv run pytest tests/test_journey.py tests/test_notifications.py -v`
Expected: PASS.

## Task 6: Add the static Service Worker

**Objective:** Display visible notifications and route taps into the installed app.

**Files:**
- Create: `frontend/public/service-worker.js`
- Test: `frontend/test/service-worker.test.ts` (create; import worker logic through a small testable module if necessary)
- Modify: `Dockerfile:10-16` only if the static build does not already emit `public/` assets

**Step 1: Write worker behavior tests.**

Mock the Service Worker event APIs. Assert a `push` event calls `showNotification` with the exact Korean body and a `notificationclick` focuses an existing client or opens `/`.

**Step 2: Implement worker handlers.**

Parse JSON defensively, use safe defaults only for malformed payloads, call `event.waitUntil()`, show a visible notification immediately, close it on click, and focus/open a same-origin route. Do not fetch the current journey or include sensitive data in the payload.

**Step 3: Build verification.**

Run: `cd frontend && npm test -- service-worker && npm run build`
Expected: PASS and `frontend/out/service-worker.js` exists.

## Task 7: Add typed frontend client and notification settings control

**Objective:** Let the rider explicitly manage Web Push from the persistent application shell.

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Create: `frontend/hooks/use-notification-settings.ts`
- Create: `frontend/components/notification-settings.tsx`
- Modify: `frontend/components/journey-app.tsx:50-188`
- Test: `frontend/lib/api.test.ts`
- Test: `frontend/components/notification-settings.test.tsx` (create)
- Test: `frontend/components/journey-app.test.tsx`

**Step 1: Write API wrapper contract tests.**

Add wrappers for config, status, upsert, and deletion through `requestJson`. Assert exact method/path/body, `cache: "no-store"`, and AbortSignal forwarding.

**Step 2: Write settings component tests.**

Mock `navigator.serviceWorker`, `Notification`, and `PushManager`. Verify unsupported, server-disabled, denied, disabled, subscribing, enabled, server-error, and unsubscribe states. Assert permission is requested only after a click and a successful browser subscription is posted before enabled UI appears. Verify unmount aborts a pending status request and ignores late settlement.

**Step 3: Implement the hook and component.**

Feature-detect every browser API. Register `/service-worker.js`, obtain the registration, call `pushManager.subscribe({userVisibleOnly: true, applicationServerKey: ...})`, normalize `PushSubscription.toJSON()` into the typed API payload, and render the control near `JourneyApp`'s header without affecting authoritative journey state. Include a native button, Korean labels, and a visible status region.

**Step 4: Run focused frontend tests.**

Run: `cd frontend && npm test -- lib/api.test.ts components/notification-settings.test.tsx components/journey-app.test.tsx && npm run typecheck`
Expected: PASS.

## Task 8: Documentation, full verification, and manual iPhone acceptance

**Objective:** Make deployment and verification repeatable.

**Files:**
- Modify: `README.md:21-131`
- Modify: `AGENTS.md` notification/deployment/testing sections
- Modify: `.env.example`
- Modify: `docs/2026-09-05-leg-destination-notifications-specs.md` only if implementation decisions diverge

**Step 1: Document operation.**

Document VAPID key generation/rotation, required HTTPS/Home Screen/iOS version, Tailscale/egress allowance for Apple Push endpoints, no-automatic-prompt behavior, notification limitations, and the manual acceptance test. Do not document real credentials.

**Step 2: Run complete verification serially.**

Run:

```bash
uv run pytest
cd frontend
npm test
npm run typecheck
npm run build
```

Then inspect `git diff --check` and the generated `frontend/out/service-worker.js`. Do not run `npm ci`, builds, or Playwright concurrently in the shared worktree.

**Step 3: Manual production acceptance.**

After HTTPS deployment: remove/re-add the Home Screen app if needed, open it from the icon, enable notifications from the in-app control, board a test multi-leg subway route, verify exactly one notification per leg after penultimate departure, verify timer-mode wording is identical, tap the notification, and verify the app focuses the active journey. Record only pass/fail and non-secret diagnostics.

**Step 4: Review before delivery.**

Run `git status --short`, `git diff --check`, and inspect the final diff. Keep the pre-existing WIP only in the stash; do not include it in feature commits.
