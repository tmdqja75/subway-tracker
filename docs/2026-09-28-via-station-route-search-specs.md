# Via-station route search

## Goal

Let the rider get the route they actually want when Tmap ranks it low or
never returns it, by (1) requesting Tmap's full result set and (2) letting the
rider force the route through up to three ordered via stations (경유역).

## Background

- `POST /transit/routes` accepts only origin/destination coordinates plus
  `count` (1–10, default 10), `lang`, `format`, `searchDttm`. It has no
  waypoint, `passList`, or mode-filter parameter, so via routing must be
  composed server-side from multiple searches.
- The app currently sends `count=5`, discarding half of Tmap's options.
- `route_options_cache` never expires, so a route pair searched once keeps
  its original 5-result list indefinitely.

## Part 1: full Tmap result set

- Stop sending `count` from `app/tmap.py`; Tmap's default (10) applies.
- Bump `ROUTE_OPTIONS_CACHE_FORMAT_VERSION` so the existing startup migration
  clears `route_options_cache`, forcing every pair to refetch 10 results.

## Part 2: via stations

### API contract

`POST /api/routes` request gains an optional ordered list:

```json
{
  "start": "강남", "start_id": "...",
  "end": "잠실", "end_id": "...",
  "vias": [{ "name": "건대입구", "station_id": "..." }]
}
```

- `vias` defaults to `[]`. `station_id` is optional and resolved exactly like
  `start_id` (registry `get`, falling back to `find(name)`).
- Vias with a blank `name` are ignored.
- More than 3 non-blank vias → `400`.
- An unresolvable via → `404 station not found: {name}`, same as start/end.
- Any two consecutive stops in `[start, *vias, end]` resolving to the same
  station name → `400` (Tmap otherwise fails with an opaque error surfaced as
  `502`).
- Response shape is unchanged: a list of `Itinerary`.

### Behavior

- **No vias:** identical to today, including appending reversed
  opposite-direction cache hits.
- **With vias:**
  1. Split into segments `start→V1, V1→V2, …, Vk→end`.
  2. Fetch segments one at a time (Tmap answers concurrent bursts with HTTP
     429) through one shared cache-or-Tmap helper
     (the current `/routes` cache logic, extracted). Each segment is cached
     under its own key, exactly like a normal search.
  3. Fold segments left to right: combine every itinerary so far with every
     itinerary of the next segment via `join_itineraries`, drop duplicates
     with the same leg sequence (`(route, start_name, end_name)` per leg,
     keeping the fastest), sort by `total_time`, keep the fastest 10 before
     the next fold.
  4. Return the final list (≤10). No reversed-cache appending for via
     searches. Joined results are not cached themselves.
- A segment with no itineraries, or a Tmap answer without a `plan` (e.g. a
  hop too short to route) → `404 {from} → {to} 구간 경로를 찾지 못했어요.`
- The search form shows the `detail` of any 4xx response to the rider;
  5xx keeps the generic retry message.
- A Tmap failure on any segment → `502`, as today.

### `join_itineraries(a, b) -> Itinerary` (in `app/tmap.py`)

- `legs = a.legs + b.legs`. Legs are never merged at the via, even with the
  same route name: branch junctions (2호선 성수/신도림, 1호선 구로, 5호선 강동,
  …) share a Tmap route name across trains that don't run through. A pure
  pass-through via therefore shows one extra transfer and asks the rider to
  confirm boarding at the via.
- `total_time`, `total_walk_time`: summed.
- `transfer_count`: `a + b + 1`.
- `fare`: `None` (Korean fares are distance-based; summing halves overstates).
- The last leg of `a` keeps `transfer_walk_time = 0` and an
  empty `transfer_walk_shape`: Tmap does not describe the walk at the via.
- `summary`: rebuilt from the joined legs with the same helper used by
  `reverse_itinerary` (extracted, not duplicated).
- `is_reversed`: `False`.

## Part 3: rider UI

- Between 출발역 and 도착역, an initially empty list of via rows plus a
  `+ 경유역 추가` button.
- Each row is a `StationAutocomplete` labelled `경유역 1`, `경유역 2`, … with
  an `×` remove button. The add button is disabled at 3 rows.
- Submit sends non-blank rows as `vias` (with `station_id` when a suggestion
  was picked); blank rows are dropped and the field is omitted when empty.
- Selecting a Most Used / Recent route clears all via rows.
- `RouteSearchRequest` in `frontend/lib/types.ts` gains
  `vias?: { name: string; station_id?: string | null }[]`.

Itineraries with a `BUS`/`EXPRESSBUS` leg are dropped server-side for every
search (the route list never offered them), before via joins so the
fastest-10 slots go to showable routes. The cache keeps Tmap's full answer;
filtering happens on read. A hop with only bus options → the same `404`
naming the hop. Joined itineraries render in the existing route list
unchanged, with no extra "경유" badge.

## Known side effect

Each via segment is written to `route_options_cache`, so segments appear in
Recent Route history. Accepted: they are real, re-searchable routes.

## Testing

- pytest (respx-mocked Tmap):
  - request body no longer contains `count`;
  - `join_itineraries`: transfer at via, same-route legs never merged, fare
    `None`, summary rebuilt;
  - `/routes` with 1 and 2 vias: segments fetched and cached, duplicate leg
    sequences collapsed, results sorted and capped at 10;
  - `400` for >3 vias and for consecutive identical stops; `404` naming the
    empty segment;
  - no-via path unchanged (existing tests stay green).
- Vitest (`journey-search.test.tsx`): add/remove rows, add button disabled at
  3, blank rows omitted from the request, `station_id` sent when picked,
  history selection clears rows.

## Docs

Update `README.md` and `AGENTS.md` for the new `vias` field and UI.
