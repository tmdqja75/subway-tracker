# Per-leg destination notifications

## Goal

Enable the installed iPhone Home Screen web app to send a single notification when a rider is one stop away from the destination of each eligible subway leg.

## Rider experience

- Add a persistent `알림 설정` control in the rider UI.
- The rider enables notifications by tapping that control. Permission must never be requested automatically on initial page load.
- The UI shows enabled, disabled, unsupported, or permission-denied status clearly and does not repeatedly prompt after a denial.
- Notifications are supported only when the app is installed to the iPhone Home Screen, the device runs iOS/iPadOS 16.4 or newer, and the deployed app uses HTTPS.
- Each notification uses the exact body:

  `다음 역은 [station-name]이에요. 하차를 준비하세요.`

- The title identifies Subway Tracker.
- Tapping a notification opens or focuses the installed app at the active journey screen.
- No app-icon badge is part of the first version.

## Eligibility and trigger

- Eligible legs are only `SUBWAY` legs with at least two known stations.
- Every eligible subway leg has its own alert, including a subway leg after a transfer.
- Bus, train, and ferry legs are never eligible, even if their route data has stops.
- Realtime tracking sends the alert after the selected train departs the penultimate station. The leg destination is then the next stop.
- Timer tracking, including fallback after a realtime feed loss, sends the same alert at the itinerary-derived equivalent progress boundary.
- Both realtime and timer alerts intentionally use the same wording. Timer-derived timing is not labelled as estimated in the user-visible notification.
- A new journey creates new eligibility. Cancelling, stopping, or completing a journey prevents future unsent leg notifications.

## Architecture

### Frontend

- Add a root-scoped Service Worker to the static Next.js export.
- Register the worker only through browser feature detection.
- On the user’s explicit settings action, request Notification permission and create a standard Push API subscription using the server-provided public VAPID key.
- Send the push endpoint and browser-provided encryption keys to FastAPI.
- The worker handles `push` by immediately showing a visible notification and handles `notificationclick` by focusing an existing app window or opening the app route.

### API and server

- Add a public configuration endpoint that exposes only the VAPID public key and notification capability status.
- Add subscription registration, deletion, and status endpoints for the settings UI.
- Keep the VAPID private key in runtime environment configuration only. It must never enter API responses, logs, browser output, Git, or documentation examples.
- Add a Python Web Push dependency that supports standard VAPID payload encryption and delivery to Safari/APNs Push API endpoints.
- Add a notification service with an injected sender boundary so journey tests can verify notification behavior without a real outbound Push API call.
- Invoke the notification check from the existing background tracker, after it has updated authoritative station-relative state. It must never block tracking, leg completion, or Reitti transfer.

## Persistence and retries

- Store subscriptions separately from journeys, unique by push endpoint. This safely handles renewed subscriptions and allows multiple opted-in devices in this single-user app.
- Store a durable notification record for each `(journey_id, leg_idx)` to enforce at most one delivery attempt for that leg across repeat polls and process restarts.
- Persist the claim before sending and persist the result after sending.
- Remove subscriptions when the Push service reports they are permanently invalid or expired.
- Retry transient send failures with a bounded retry policy. After the retry budget is exhausted, record the failed attempt and do not replay it later due to another station poll.
- A successful Push API response confirms a delivery attempt, not that iOS displayed the notification. Focus mode, connectivity, and OS power policies can delay or suppress display.

## Security and operations

- Treat subscription endpoints and encryption keys as sensitive operational data. Do not emit them in application logs or browser-visible diagnostics.
- Configure the VAPID private key, public key, and contact subject through environment variables. Document generation and rotation without including real values.
- Ensure outbound network policy permits Apple Web Push endpoints such as `https://*.push.apple.com`.
- Update README.md and AGENTS.md with the new architecture, configuration, deployment requirements, and the manual iPhone validation procedure.

## Verification

- Database migration tests against an existing tracker database.
- API tests for public configuration and subscription registration, refresh, deletion, and status.
- Journey tests for realtime penultimate departure, timer-mode timing, per-transfer-leg independence, non-subway exclusion, duplicate prevention, restart recovery, cancellation, stop, and completion.
- Sender tests for valid payload content, permanent invalid-subscription deletion, and bounded transient failure handling.
- Frontend unit tests for capability states, explicit permission action, Service Worker registration, Push API subscription, and API request contracts.
- Run Python tests, frontend unit tests, typecheck, and a static build.
- Perform a manual HTTPS iPhone Home Screen acceptance test, because Chromium automation cannot prove APNs/iOS notification delivery.
