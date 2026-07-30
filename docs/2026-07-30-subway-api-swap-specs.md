# Subway Realtime API Swap Specification

## Goal

Replace the Seoul Open API realtime client (`app/seoul.py`) with a client for the
self-hosted subway API at `~/Documents/subway` (run separately, reached over
HTTP at a configurable base URL). Tmap route search/geometry is unaffected.
Old Seoul-specific code is archived, not deleted.

## Archiving

Move to `archive/seoul-api/` (new top-level directory, kept in git, removed
from active imports):

- `app/seoul.py`
- `tests/test_seoul.py`
- `docs/api-samples/seoul-realtime-arrival.json`
- `docs/api-samples/seoul-realtime-position.json`

`app/lines.py` is not archived — it's rewritten in place (see below), since
`tmap_route_to_line_key` and the `line_key` values it produces are still
needed and unchanged.

## What the new API actually returns

One endpoint, `GET {SUBWAY_API_URL}/subway/seoul?lineId={id}`, returns the
**entire line** as an ordered station array:

```json
{
  "isTimeTable": false,
  "data": [
    {"stn": "연천", "up": [], "dn": []},
    {"stn": "소요산", "up": [], "dn": [
      {"status": "출발", "type": "일반", "dest": "구로", "no": "0809"}
    ]}
  ]
}
```

`status` is one of 접근(approaching)/도착(arrived)/출발(departed). This is a
superset of Seoul's old `realtimePosition` feed (every running train, pinned
to its current/adjacent station) — there is no separate arrivals endpoint.
Seoul's `realtimeStationArrival` (with its per-station arrival ranking and
distance codes) has no equivalent; it must be reconstructed by scanning the
whole line snapshot.

No timestamp field exists anywhere in the response.

## Client module: `app/subway_feed.py`

Replaces both of `seoul.py`'s fetch functions with one fetch plus two pure
derivation functions:

- `fetch_line_snapshot(base_url, line_id) -> list[StationSnapshot]` — HTTP
  GET, retried once on JSON decode failure before raising (see Error
  handling). `StationSnapshot = {name, up: list[TrainEntry], dn: list[TrainEntry]}`,
  `TrainEntry = {status, type, dest, no}`.
- `locate_train(snapshot, train_no) -> TrainLocation | None` — flattens the
  snapshot once into `{train_no: (station_idx, bucket, status, dest)}` and
  looks up a specific train. Replaces `fetch_positions` +
  `find_onboard_trains`'s job of finding where a specific train currently is.
- `arrivals_at(snapshot, station_name, upcoming_names) -> list[ArrivingTrain]`
  — replaces `fetch_arrivals`. Scans every station's `up`/`dn` entries,
  keeps trains whose direction resolves toward `upcoming_names` (see Direction
  algorithm), computes `stations_away` as an **exact** index distance (no more
  `avg_seconds_per_station` estimation), sorted same as today (matching
  direction first, then fewest stations away).

`ArrivingTrain`/`OnboardTrain`/`TrainStatus` (in `app/models.py`) are
unchanged — same fields, same consumers. Two fields get fixed values the new
source can't populate:

- `eta_seconds` is always `0` (no seconds-based ETA exists in this feed).
- `arrival_msg` is `"운행 중"` when `stations_away` is `None` (a matched train
  whose current position can't be resolved to an index — should be rare,
  since the whole line is visible, but keeps the field non-empty for the one
  frontend branch that reads it, `train-picker.tsx:46`).
- `stations_away_estimated` is always `False` (every count is now exact).

## Line coverage: `line_key` → `lineId`

`app/lines.py` replaces `SUBWAY_ID_TO_LINE`/`LINE_TO_SUBWAY_ID` (which mapped
to Seoul's `subwayId`) with `LINE_KEY_TO_API_ID`, mapping the **same
`line_key` strings already produced by `tmap_route_to_line_key`** to the new
API's numeric `lineId`. `tmap_route_to_line_key` itself is unchanged, so
`SubwayLeg.line_key` values, DB rows, and the frontend contract don't change.

| line_key | lineId | | line_key | lineId |
|---|---|---|---|---|
| 1호선 | 1 | | 7호선 | 7 |
| 2호선 | 2 | | 8호선 | 8 |
| 3호선 | 3 | | 9호선 | 9 |
| 4호선 | 4 | | GTX-A | 151 |
| 5호선 | 5 | | 중앙선 | 101 |
| 6호선 | 6 | | 경의중앙선 | 101 |
| 수인분당선 | 102 | | 신분당선 | 103 |
| 경춘선 | 104 | | 경강선 | 105 |
| 우이신설선 | 106 | | 서해선 | 107 |
| 공항철도 | 108 | | | |

`중앙선` has no separate id in the new API (folded into `경의중앙선`'s corridor)
— both map to `101`. This is the only line without a 1:1 id; every other
currently-covered line has a direct equivalent.

Lines the new API covers but this codebase doesn't currently resolve a
`line_key` for (e.g. 신림선) are **intentionally left uncovered** — no new
`tmap_route_to_line_key` mapping is added, since that's a scope decision, not
a defect. Those legs keep falling back to timer-mode tracking, exactly as
they do today.

## Direction algorithm

Verified empirically against live traffic (not assumed from source reading —
the deployed container's behavior was checked directly).

**Universal rule, holds across every line including appended branches:**
`dn` = increasing snapshot-array index, `up` = decreasing snapshot-array
index. No per-line station-order table is hardcoded on our side — the order
comes fresh from each response, and this rule is used to compute
`stations_away` and to check whether a candidate train's position is between
the queried station and the leg's upcoming stations in the leg's own travel
direction.

Two lines need one explicit wrap rule each, both confirmed against live
loop traffic:

- **Line 2** (whole line is a loop): `dn` past the last main-loop index (42,
  뚝섬) wraps to index 0 (성수); `up` past index 0 wraps to 42. Loop trains'
  `dest` was observed as the literal string `"성수"` (not `내선순환`/`외선순환`
  as the upstream source code suggests it should become — the deployed
  container doesn't appear to apply that label conversion). The algorithm
  does not parse `dest` text for loop matching at all; it uses `up`/`dn`
  bucket membership only, which is unaffected by which label variant a given
  deployment produces.
- **Line 6** (small loop at one end): `up` reaching index 0 (역촌) re-enters
  at index 5 (응암) and continues as `dn` from there. Matches the loop
  behavior described in the brainstorming notes (a train bound for 응암 keeps
  running, re-emerging toward 봉화산 or 신내, shown as `dest="응암순환"` under
  `up` for the whole trunk approach — confirmed live).

Line 1's two single-station branches (금천구청→광명, 병점→서동탄) and its two
long branches (가산디지털단지-side, 구일-side) need no special rule: they are
appended to the array as plain continuations, and the universal `up`/
`dn` = index-decreasing/increasing rule already held true across every branch
observed live, no exceptions.

## Station name disambiguation

Line 2's `성수` and `신도림` each appear twice in the snapshot array: once as
the trunk station, once as the branch-junction entry (`"성수 (지선)"`,
`"신도림 (지선)"`). When resolving a leg's station name against the snapshot,
if a name matches more than one index, pick the occurrence adjacent (in
snapshot index) to another of the leg's own station names. No other line has
a name collision within its own snapshot array.

## Consumer changes

- `app/journey.py`: `_realtime_update`'s poll calls `locate_train` instead of
  `fetch_positions` + a linear scan for `train_no`. Status vocabulary
  (`approaching`/`arrived`/`departed`/`between`) is unchanged — the first
  three map 1:1 from 접근/도착/출발; `between` is derived the same way it
  partially already is today (a departed-from-previous-station train is
  "between" until it posts arrived/approaching at the next station).
- `app/api.py`: `_fetch_leg_arrivals`/`_fetch_leg_positions` call
  `arrivals_at`/`locate_train` (via `fetch_line_snapshot`) instead of
  `fetch_arrivals`/`fetch_positions`. Return shapes are unchanged, so
  `find_onboard_trains`'s callers in `api.py` keep their current signatures;
  `find_onboard_trains` itself moves into `subway_feed.py` and is rewritten
  against `TrainLocation` instead of raw Seoul position dicts.
- `app/config.py`: remove `seoul_api_key`, `seoul_api_key_two`; add
  `subway_api_url: str = "http://localhost:8000"`.
- `docker-compose.yml`: add `SUBWAY_API_URL` to the `environment` block,
  default `http://localhost:8000`, overridable via `.env`. The new API's own
  `docker-compose.yml` in `~/Documents/subway` is unchanged and run
  independently, per the decision to keep it a separate stack.

## Frontend impact

None. The frontend only calls this backend's own `/api/*` routes, never the
subway data source directly, and every response shape it consumes
(`ArrivingTrain`, `OnboardTrain`, `TrainStatus`, `SubwayLeg`, `CurrentArrivalsResponse`)
is unchanged. Two already-existing frontend behaviors shift as a side effect
of the source swap, both cosmetic:

- `train-picker.tsx:44`'s `"약 "` (approx.) prefix, shown when
  `stations_away_estimated` is true, stops appearing — the new source's
  counts are always exact.
- `train-picker.tsx:46`'s `arrival_msg` fallback text now reads `"운행 중"`
  instead of Seoul's `arvlMsg2` text (e.g. "전역 진입") for the rare
  unresolved-position case.

## Error handling

- The new API returned truncated/malformed JSON under concurrent request
  load during testing (line 1 specifically, roughly 1 in 4 rapid parallel
  requests). `fetch_line_snapshot` retries the request once on a JSON decode
  error before raising a `SubwayApiError`.
- No API key or rate-limit handling is needed (no auth on this endpoint,
  unlike Seoul's key rotation) — that logic is deleted, not ported.
- Unreachable/erroring line fetches surface the same way uncovered lines do
  today: the leg falls back to timer-mode tracking. No new failure mode is
  introduced.

## Verification

- New `tests/test_subway_feed.py` replacing `tests/test_seoul.py`'s intent:
  fixture snapshots for lines 1, 2, and 6 (captured from the live API,
  including the branch/loop shapes above) driving `locate_train`,
  `arrivals_at`, the direction/wrap rules, and the 성수/신도림 disambiguation.
- Update `tests/test_journey.py` and `tests/test_api_routes.py` for the new
  client's call signatures (mock `subway_feed` instead of `seoul`).
- Run the full Python test suite; no frontend test changes expected since no
  frontend files change.
