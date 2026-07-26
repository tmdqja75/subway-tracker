# Retroactive Train Tracking Specification

## Goal

Allow a rider who boarded before opening Subway Tracker to select the same train after it has passed the leg origin. The tracker must reconstruct the origin-to-current-position portion as clearly labelled estimates, then continue live realtime tracking.

## Candidate eligibility

For a realtime-covered leg, the picker returns both approaching arrival candidates and `already_onboard` candidates from Seoul's `realtimePosition` feed. An onboard candidate must:

- be on the leg's configured line;
- have a normalized terminus (`statnTnm`) among the leg's stations after the origin;
- resolve to a location on the leg after the origin and before its final station; and
- have a supported station-relative state (`0`, `1`, `2`, or `3`).

The board endpoint must independently fetch and validate the selected train again. A candidate that has moved outside the leg, changed direction, disappeared, or reached the alighting station is rejected with a 409 response.

## Historical reconstruction

Seoul's position API provides an observation time and current station-relative state, not a historic timetable of station events. Therefore all data before the initial live observation is estimated.

The schedule budget for one segment is `max(section_time / segment_count, 30 seconds)`. Each internal stop reserves `min(30 seconds, 25% of its segment budget)` for a dwell, and the remaining time is running time. A `departed` observation anchors the departure time; an `arrived`/`approaching` observation anchors station arrival; a `between` observation uses the midpoint of its preceding segment. The engine works backwards to calculate origin departure and intermediate arrivals/departures.

The tracker backfills the existing Tmap geometry with `estimated=True` points. It emits station coordinates at estimated arrival and departure times, which creates a pause in the recorded trace. The live observation/current position is non-estimated; subsequent position polling continues with the normal realtime state machine.

## UX

The picker uses separate Korean headings for trains approaching the platform and trains already past the origin. Cards in the latter group disclose the current station/state and that origin-to-current history is estimated. The active journey snapshot exposes a `history_estimated` flag so tracking UI can state that early recorded history was reconstructed.

## Verification

- Unit tests cover direction/position eligibility, departure/arrival/between reconstruction, past-path timestamps and estimated flags, and stale-board revalidation.
- UI tests cover the onboard section, explanatory copy, card selection, and existing cancellation/locking behavior.
- Run focused backend/frontend suites, full Python tests, frontend typecheck/tests/build, and the relevant E2E suite.
