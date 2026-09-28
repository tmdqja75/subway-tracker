# Via-Station Route Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return Tmap's full 10-route result set and let the rider force a route through up to 3 ordered via stations (경유역).

**Architecture:** Tmap transit has no waypoint parameter, so `POST /api/routes` splits `start → V1 → … → end` into segments, fetches each through the existing SQLite route cache, and folds them together with a pure `join_itineraries()` in `app/tmap.py`, trimming to the fastest 10 distinct routes after each fold. The rider UI adds removable via rows between 출발역 and 도착역.

**Tech Stack:** FastAPI + pydantic + sqlite3 (pytest, respx, monkeypatch); Next.js static export + React (Vitest, Testing Library).

**Spec:** `docs/2026-09-28-via-station-route-search-specs.md`

## Global Constraints

- Max 3 non-blank vias; more → HTTP `400`.
- Joined results capped at 10, deduped by leg sequence `(route, start_name, end_name)` keeping the fastest.
- Joined itinerary `fare` is `None`; via transfer walk is `0`.
- No-via search behaviour is unchanged (including reversed-cache appending).
- Bus/train legs are kept, never filtered.
- UI copy: button `+ 경유역 추가`, row labels `경유역 1..3`, remove button aria-label `경유역 N 삭제`, placeholder `경유역을 입력하세요`.
- No new dependencies. Match existing style; no lint tooling.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Via typed but never picked from autocomplete (no `station_id`) → resolved by name like start/end. Test: Task 3 two-via test sends names only.
2. Tmap fails on one segment while others succeed → whole request `502`, no partial list. Test: Task 3.
3. Interchange via picked on a specific line → that segment is cached under that line, not an arbitrary one. Test: Task 3 one-via test asserts the `건대입구/2호선` cache row.
4. Same line reversing at the via (U-turn) → stays two legs so tracking re-boards. Test: Task 2.
5. Rider picks a history route after adding via rows → rows cleared so a stale via isn't silently sent. Test: Task 4.

---

## File Structure

| File | Change |
|---|---|
| `app/tmap.py` | drop `count`; extract `_summary_from_legs`; add `join_itineraries` |
| `app/db.py` | bump `ROUTE_OPTIONS_CACHE_FORMAT_VERSION` |
| `app/api.py` | `ViaStop`, `vias` field, `_resolve_station`, `_search_segment`, `_join_segments`, via branch in `routes` |
| `frontend/lib/types.ts` | `ViaStop`, `RouteSearchRequest.vias` |
| `frontend/components/journey-search.tsx` | via rows UI + submit/history wiring |
| `frontend/app/globals.css` | `.journey-search__via` row layout |
| `tests/test_tmap.py`, `tests/test_api_routes.py`, `frontend/components/journey-search.test.tsx` | tests |
| `README.md`, `AGENTS.md` | docs |

---

### Task 1: Request Tmap's full result set and flush the cache

**Files:**
- Modify: `app/tmap.py` (`search_routes_with_raw_response`, `search_routes`)
- Modify: `app/db.py:11`
- Test: `tests/test_tmap.py`

**Interfaces:**
- Produces: `search_routes_with_raw_response(app_key, start_lon, start_lat, end_lon, end_lat)` and `search_routes(...)` — same signatures minus `count`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_tmap.py`)

```python
@pytest.mark.asyncio
async def test_search_routes_uses_tmap_default_result_count():
    data = {"metaData": {"plan": {"itineraries": []}}}

    with respx.mock:
        route = respx.post(TRANSIT_URL).mock(return_value=Response(200, json=data))
        await search_routes("key", 127.0, 37.0, 126.0, 37.5)

    assert "count" not in json.loads(route.calls[0].request.content)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_tmap.py::test_search_routes_uses_tmap_default_result_count -v`
Expected: FAIL (`assert 'count' not in {...'count': 5...}`)

- [ ] **Step 3: Implement**

In `app/tmap.py`, delete the `count: int = 5,` parameter from both `search_routes_with_raw_response` and `search_routes`, delete `"count": count,` from the request JSON, and delete `count=count,` from the inner call in `search_routes`.

In `app/db.py` line 11:

```python
ROUTE_OPTIONS_CACHE_FORMAT_VERSION = "5"
```

(The existing startup migration deletes `route_options_cache` when the version changes, so every pair refetches 10 routes.)

- [ ] **Step 4: Run the Python suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/tmap.py app/db.py tests/test_tmap.py
git commit -m "fix: request Tmap's full 10-route result set

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `join_itineraries`

**Files:**
- Modify: `app/tmap.py`
- Test: `tests/test_tmap.py`

**Interfaces:**
- Produces: `join_itineraries(a: Itinerary, b: Itinerary) -> Itinerary` in `app.tmap`; private `_summary_from_legs(legs: list[SubwayLeg]) -> list[str]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_tmap.py`; add `join_itineraries` to the `from app.tmap import ...` line)

```python
def _leg(route, names, section_time=300, mode="SUBWAY"):
    return SubwayLeg(
        route=route,
        line_key=None,
        mode=mode,
        section_time=section_time,
        start_name=names[0],
        end_name=names[-1],
        stations=[
            LegStation(index=i, name=n, lat=37.5 + i / 100, lon=127.0)
            for i, n in enumerate(names)
        ],
        shape=[[37.5 + i / 100, 127.0] for i in range(len(names))],
    )


def _trip(*legs, walk=60):
    return Itinerary(
        total_time=sum(leg.section_time for leg in legs) + walk,
        transfer_count=len(legs) - 1,
        total_walk_time=walk,
        fare=1400,
        legs=list(legs),
        summary=["stale"],
    )


def test_join_itineraries_transfers_at_the_via():
    a = _trip(_leg("수도권7호선", ["논현", "건대입구"], 1500))
    b = _trip(_leg("수도권2호선", ["건대입구", "잠실"], 480))

    joined = join_itineraries(a, b)

    assert [leg.route for leg in joined.legs] == ["수도권7호선", "수도권2호선"]
    assert joined.total_time == a.total_time + b.total_time
    assert joined.total_walk_time == 120
    assert joined.transfer_count == 1
    assert joined.fare is None
    assert joined.is_reversed is False
    assert joined.legs[0].transfer_walk_time == 0
    assert joined.summary == [
        "🚇 수도권7호선: 논현 → 건대입구",
        "🚇 수도권2호선: 건대입구 → 잠실",
    ]


def test_join_itineraries_merges_a_ride_through_the_via_on_the_same_line():
    a = _trip(_leg("수도권2호선", ["강남", "삼성", "건대입구"], 900))
    b = _trip(_leg("수도권2호선", ["건대입구", "구의", "잠실"], 480))

    joined = join_itineraries(a, b)

    assert len(joined.legs) == 1
    leg = joined.legs[0]
    assert [s.name for s in leg.stations] == ["강남", "삼성", "건대입구", "구의", "잠실"]
    assert [s.index for s in leg.stations] == [0, 1, 2, 3, 4]
    assert (leg.start_name, leg.end_name) == ("강남", "잠실")
    assert leg.section_time == 1380
    assert joined.transfer_count == 0
    assert joined.summary == ["🚇 수도권2호선: 강남 → 잠실"]


def test_join_itineraries_keeps_a_same_line_u_turn_as_two_legs():
    a = _trip(_leg("수도권2호선", ["성수", "건대입구"]))
    b = _trip(_leg("수도권2호선", ["건대입구", "성수", "뚝섬"]))

    joined = join_itineraries(a, b)

    assert len(joined.legs) == 2
    assert joined.transfer_count == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tmap.py -k join_itineraries -v`
Expected: FAIL with `ImportError: cannot import name 'join_itineraries'`

- [ ] **Step 3: Implement** in `app/tmap.py`

Add after `_transit_summary`:

```python
def _summary_from_legs(legs: list[SubwayLeg]) -> list[str]:
    summary = []
    for leg in legs:
        summary.append(_transit_summary(leg.mode, leg))
        if leg.transfer_walk_time >= 60:
            summary.append(f"🚶 도보 {leg.transfer_walk_time // 60}분")
    return summary
```

In `reverse_itinerary`, replace the `summary = [] / for leg in new_legs: ...` block with nothing and pass `summary=_summary_from_legs(new_legs),` to the `Itinerary(...)` call.

Add after `reverse_itinerary`:

```python
def join_itineraries(a: Itinerary, b: Itinerary) -> Itinerary:
    """Chain two itineraries that meet at a via station into one trip.

    Tmap transit has no waypoint parameter, so via routes are separate
    searches glued together here. Staying on the same line through the via
    (not a U-turn) becomes one leg so tracking doesn't ask to re-board.
    Fare is unknown: Korean fares are distance-based, so halves don't add.
    """
    last, first = a.legs[-1], b.legs[0]
    ride_through = (
        last.route == first.route
        and len(last.stations) >= 2
        and len(first.stations) >= 2
        and last.stations[-2].name != first.stations[1].name
    )
    if ride_through:
        stations = [*last.stations, *first.stations[1:]]
        merged = SubwayLeg(
            route=last.route,
            line_key=last.line_key,
            mode=last.mode,
            section_time=last.section_time + first.section_time,
            start_name=last.start_name,
            end_name=first.end_name,
            stations=[
                LegStation(index=i, name=s.name, lat=s.lat, lon=s.lon)
                for i, s in enumerate(stations)
            ],
            shape=[*last.shape, *first.shape],
            transfer_walk_shape=first.transfer_walk_shape,
            transfer_walk_time=first.transfer_walk_time,
        )
        legs = [*a.legs[:-1], merged, *b.legs[1:]]
    else:
        legs = [*a.legs, *b.legs]
    return Itinerary(
        total_time=a.total_time + b.total_time,
        transfer_count=a.transfer_count + b.transfer_count + (0 if ride_through else 1),
        total_walk_time=a.total_walk_time + b.total_walk_time,
        fare=None,
        legs=legs,
        summary=_summary_from_legs(legs),
    )
```

(`a`'s last leg already has `transfer_walk_time == 0`: `_parse_itineraries` only sets it when another transit leg follows.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tmap.py -v`
Expected: all PASS (existing `reverse_itinerary` tests confirm the summary extraction)

- [ ] **Step 5: Commit**

```bash
git add app/tmap.py tests/test_tmap.py
git commit -m "feat: join itineraries that meet at a via station

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `vias` on `POST /api/routes`

**Files:**
- Modify: `app/api.py` (imports, `RouteSearchRequest`, `routes`)
- Test: `tests/test_api_routes.py`

**Interfaces:**
- Consumes: `join_itineraries(a, b) -> Itinerary` (Task 2); `search_routes_with_raw_response(app_key, start_lon, start_lat, end_lon, end_lat)` (Task 1).
- Produces: request body `vias: [{"name": str, "station_id": str | null}]` (default `[]`); response unchanged (`list[Itinerary]`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_api_routes.py`; add `from app.tmap import TmapError` to the existing `app.tmap` import)

```python
VIA_STATIONS = [
    Station(station_id="gangnam-2", name="강남", line="2호선", lat=37.10, lon=127.0),
    Station(station_id="konkuk-2", name="건대입구", line="2호선", lat=37.20, lon=127.0),
    Station(station_id="konkuk-7", name="건대입구", line="7호선", lat=37.21, lon=127.0),
    Station(station_id="jamsil-2", name="잠실", line="2호선", lat=37.30, lon=127.0),
    Station(station_id="sadang-2", name="사당", line="2호선", lat=37.40, lon=127.0),
]
NAME_BY_LAT = {s.lat: s.name for s in VIA_STATIONS}


def via_leg(route, start, end, section_time, mode="SUBWAY"):
    return SubwayLeg(
        route=route,
        line_key=None,
        mode=mode,
        section_time=section_time,
        start_name=start,
        end_name=end,
        stations=[
            LegStation(index=0, name=start, lat=37.0, lon=127.0),
            LegStation(index=1, name=end, lat=37.1, lon=127.0),
        ],
    )


def via_trip(route, start, end, section_time, walk=0, mode="SUBWAY"):
    return Itinerary(
        total_time=section_time + walk,
        transfer_count=0,
        total_walk_time=walk,
        fare=1400,
        legs=[via_leg(route, start, end, section_time, mode)],
        summary=[],
    )


def make_via_client(tmp_path, monkeypatch, segments):
    """segments: {(from_name, to_name): list[Itinerary] | Exception}"""
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    app.state.stations = StationRegistry(VIA_STATIONS)
    calls = []

    async def fake_search(app_key, start_lon, start_lat, end_lon, end_lat):
        key = (NAME_BY_LAT[start_lat], NAME_BY_LAT[end_lat])
        calls.append(key)
        result = segments[key]
        if isinstance(result, Exception):
            raise result
        return TmapRouteSearchResult(itineraries=result, raw_response_json="{}")

    monkeypatch.setattr("app.api.search_routes_with_raw_response", fake_search)
    return TestClient(app), db, calls


def test_routes_joins_segments_through_one_via_fastest_first(tmp_path, monkeypatch):
    client, db, calls = make_via_client(tmp_path, monkeypatch, {
        ("강남", "건대입구"): [
            via_trip("수도권7호선", "강남", "건대입구", 1500),
            via_trip("수도권2호선", "강남", "건대입구", 1300),
        ],
        ("건대입구", "잠실"): [
            via_trip("수도권2호선", "건대입구", "잠실", 480),
            via_trip("2415", "건대입구", "잠실", 900, mode="BUS"),
        ],
    })

    response = client.post("/api/routes", json={
        "start": "강남", "start_id": "gangnam-2",
        "end": "잠실", "end_id": "jamsil-2",
        "vias": [{"name": "건대입구", "station_id": "konkuk-2"}],
    })

    assert response.status_code == 200
    body = response.json()
    assert [it["total_time"] for it in body] == [1780, 1980, 2200, 2400]
    assert len(body[0]["legs"]) == 1  # 2호선 ride-through merged
    assert body[2]["legs"][1]["mode"] == "BUS"  # bus combos kept
    assert all(it["fare"] is None and it["is_reversed"] is False for it in body)
    assert sorted(calls) == [("강남", "건대입구"), ("건대입구", "잠실")]
    assert db.get_cached_route_options("강남", "2호선", "건대입구", "2호선") is not None
    assert db.get_cached_route_options("건대입구", "2호선", "잠실", "2호선") is not None


def test_routes_via_dedupes_same_leg_sequence_and_caps_at_ten(tmp_path, monkeypatch):
    client, _, _ = make_via_client(tmp_path, monkeypatch, {
        ("강남", "건대입구"): [
            via_trip("7-slow", "강남", "건대입구", 600, walk=300),
            *(via_trip(f"A{i}", "강남", "건대입구", 600 + i) for i in range(4)),
            via_trip("7-slow", "강남", "건대입구", 600, walk=0),
        ],
        ("건대입구", "잠실"): [via_trip(f"B{i}", "건대입구", "잠실", 300 + i) for i in range(4)],
    })

    body = client.post("/api/routes", json={
        "start": "강남", "end": "잠실", "vias": [{"name": "건대입구"}],
    }).json()

    assert len(body) == 10
    times = [it["total_time"] for it in body]
    assert times == sorted(times)
    sequences = [tuple(leg["route"] for leg in it["legs"]) for it in body]
    assert len(set(sequences)) == 10
    slow = [it for it in body if it["legs"][0]["route"] == "7-slow"]
    assert all(it["total_walk_time"] == 0 for it in slow)  # faster duplicate kept


def test_routes_with_two_vias_resolves_names_and_chains_three_segments(tmp_path, monkeypatch):
    client, _, calls = make_via_client(tmp_path, monkeypatch, {
        ("강남", "건대입구"): [via_trip("L1", "강남", "건대입구", 100)],
        ("건대입구", "잠실"): [via_trip("L2", "건대입구", "잠실", 200)],
        ("잠실", "사당"): [via_trip("L3", "잠실", "사당", 300)],
    })

    body = client.post("/api/routes", json={
        "start": "강남", "end": "사당",
        "vias": [{"name": "건대입구"}, {"name": "잠실"}],
    }).json()

    assert len(calls) == 3
    assert [leg["route"] for leg in body[0]["legs"]] == ["L1", "L2", "L3"]
    assert body[0]["transfer_count"] == 2
    assert body[0]["total_time"] == 600


def test_routes_ignores_blank_vias(tmp_path, monkeypatch):
    client, _, calls = make_via_client(tmp_path, monkeypatch, {
        ("강남", "잠실"): [via_trip("수도권2호선", "강남", "잠실", 900)],
    })

    response = client.post("/api/routes", json={
        "start": "강남", "end": "잠실", "vias": [{"name": "  "}],
    })

    assert response.status_code == 200
    assert calls == [("강남", "잠실")]
    assert response.json()[0]["fare"] == 1400  # plain search, not joined


def test_routes_rejects_more_than_three_vias(tmp_path, monkeypatch):
    client, _, calls = make_via_client(tmp_path, monkeypatch, {})

    response = client.post("/api/routes", json={
        "start": "강남", "end": "사당",
        "vias": [{"name": "건대입구"}, {"name": "잠실"}, {"name": "건대입구"}, {"name": "잠실"}],
    })

    assert response.status_code == 400
    assert calls == []


def test_routes_rejects_consecutive_identical_stops(tmp_path, monkeypatch):
    client, _, calls = make_via_client(tmp_path, monkeypatch, {})

    response = client.post("/api/routes", json={
        "start": "강남", "end": "잠실", "vias": [{"name": "강남"}],
    })

    assert response.status_code == 400
    assert calls == []


def test_routes_via_404_names_the_empty_segment(tmp_path, monkeypatch):
    client, _, _ = make_via_client(tmp_path, monkeypatch, {
        ("강남", "건대입구"): [via_trip("L1", "강남", "건대입구", 100)],
        ("건대입구", "잠실"): [],
    })

    response = client.post("/api/routes", json={
        "start": "강남", "end": "잠실", "vias": [{"name": "건대입구"}],
    })

    assert response.status_code == 404
    assert response.json()["detail"] == "no routes 건대입구 → 잠실"


def test_routes_via_502_when_any_segment_fails(tmp_path, monkeypatch):
    client, _, _ = make_via_client(tmp_path, monkeypatch, {
        ("강남", "건대입구"): [via_trip("L1", "강남", "건대입구", 100)],
        ("건대입구", "잠실"): TmapError("Tmap: boom"),
    })

    response = client.post("/api/routes", json={
        "start": "강남", "end": "잠실", "vias": [{"name": "건대입구"}],
    })

    assert response.status_code == 502
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_api_routes.py -k "via or blank" -v`
Expected: FAIL (vias ignored → wrong call counts / 200s instead of 400s)

- [ ] **Step 3: Implement** in `app/api.py`

Imports: add `import asyncio`; change the tmap import to

```python
from .tmap import TmapError, join_itineraries, reverse_itinerary, search_routes_with_raw_response
```

Replace `RouteSearchRequest` with:

```python
class ViaStop(BaseModel):
    name: str
    station_id: str | None = None


class RouteSearchRequest(BaseModel):
    start: str
    end: str
    # station_id from the autocomplete pick: pins the exact line's coordinates
    start_id: str | None = None
    end_id: str | None = None
    # ordered 경유역; Tmap has no waypoints, so each hop is its own search
    vias: list[ViaStop] = []


MAX_VIAS = 3
MAX_JOINED_ROUTES = 10
```

Add after `_route_cache_key`:

```python
def _resolve_station(registry, name: str, station_id: str | None):
    station = (registry.get(station_id) if station_id else None) or registry.find(name)
    if not station:
        raise HTTPException(404, f"station not found: {name}")
    return station


async def _search_segment(db, settings, start, end) -> list[Itinerary]:
    """Cached Tmap search for one start→end hop."""
    cache_key = _route_cache_key(start, end)
    cached = db.get_cached_route_options(*cache_key)
    if cached is not None:
        log.debug(
            "route cache hit start=%s/%s end=%s/%s",
            cache_key[0], cache_key[1], cache_key[2], cache_key[3],
        )
        db.touch_cached_route_options(*cache_key)
        return cached
    log.info("Tmap poll start=%s end=%s", start.name, end.name)
    try:
        route_search = await search_routes_with_raw_response(
            settings.tmap_app_key, start.lon, start.lat, end.lon, end.lat
        )
    except TmapError as e:
        raise HTTPException(502, str(e))
    if not route_search.itineraries:
        raise HTTPException(404, f"no routes {start.name} → {end.name}")
    db.cache_route_options(
        *cache_key,
        route_search.itineraries,
        raw_tmap_response=route_search.raw_response_json,
    )
    return route_search.itineraries


def _join_segments(segments: list[list[Itinerary]]) -> list[Itinerary]:
    # ponytail: full cross product per fold is ≤10×10; trimming after each
    # fold keeps 3 vias at 300 joins instead of 10^4.
    itineraries = segments[0]
    for segment in segments[1:]:
        joined = sorted(
            (join_itineraries(a, b) for a in itineraries for b in segment),
            key=lambda it: it.total_time,
        )
        distinct: dict[tuple, Itinerary] = {}
        for it in joined:  # fastest first, so setdefault keeps the fastest
            distinct.setdefault(
                tuple((leg.route, leg.start_name, leg.end_name) for leg in it.legs), it
            )
        itineraries = list(distinct.values())[:MAX_JOINED_ROUTES]
    return itineraries
```

Replace the body of `routes` with:

```python
    registry = request.app.state.stations
    settings = request.app.state.settings
    db = request.app.state.manager.db
    vias = [via for via in body.vias if via.name.strip()]
    if len(vias) > MAX_VIAS:
        raise HTTPException(400, f"at most {MAX_VIAS} via stations")
    stops = [
        _resolve_station(registry, body.start, body.start_id),
        *(_resolve_station(registry, via.name.strip(), via.station_id) for via in vias),
        _resolve_station(registry, body.end, body.end_id),
    ]
    hops = list(zip(stops, stops[1:]))
    for a, b in hops:
        if normalize_name(a.name) == normalize_name(b.name):
            raise HTTPException(400, f"consecutive stops are the same station: {a.name}")

    if vias:
        segments = await asyncio.gather(*(_search_segment(db, settings, a, b) for a, b in hops))
        return [it.model_dump() for it in _join_segments(segments)]

    start, end = stops
    itineraries = await _search_segment(db, settings, start, end)

    # A same-route-opposite-direction cache hit is an extra option, never a
    # replacement for the itineraries above — riders should see both.
    reverse_key = _route_cache_key(end, start)
    reverse_cached = db.get_cached_route_options(*reverse_key)
    if reverse_cached is not None:
        log.debug(
            "route cache hit (reversed, appended) start=%s/%s end=%s/%s",
            reverse_key[0], reverse_key[1], reverse_key[2], reverse_key[3],
        )
        db.touch_cached_route_options(*reverse_key)
        itineraries = [*itineraries, *(reverse_itinerary(it) for it in reverse_cached)]

    return [it.model_dump() for it in itineraries]
```

(Note: the old no-route 404 text `no subway routes found` becomes `no routes {start} → {end}`; nothing depends on it.)

- [ ] **Step 4: Run the Python suite**

Run: `uv run pytest -q`
Expected: all PASS (existing cache/reversed-route tests guard the no-via path)

- [ ] **Step 5: Commit**

```bash
git add app/api.py tests/test_api_routes.py
git commit -m "feat: route search through up to three via stations

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Via rows in the rider UI

**Files:**
- Modify: `frontend/lib/types.ts:180-185`
- Modify: `frontend/components/journey-search.tsx`
- Modify: `frontend/app/globals.css` (after `.journey-search__fields`, ~line 460)
- Test: `frontend/components/journey-search.test.tsx`

**Interfaces:**
- Consumes: `POST /api/routes` `vias` field (Task 3).
- Produces: `ViaStop` type; `RouteSearchRequest.vias?: ViaStop[]`.

- [ ] **Step 1: Write the failing tests** (add inside `describe("JourneySearch", ...)`)

```tsx
  it("adds up to three labelled via rows and removes a row", () => {
    vi.mocked(searchStations).mockResolvedValue([]);
    render(<JourneySearch onRoutes={vi.fn()} />);
    const add = screen.getByRole("button", { name: "+ 경유역 추가" });

    fireEvent.click(add);
    fireEvent.click(add);
    fireEvent.click(add);

    expect(screen.getByRole("combobox", { name: "경유역 3" })).toBeVisible();
    expect(add).toBeDisabled();

    fireEvent.change(screen.getByRole("combobox", { name: "경유역 3" }), { target: { value: "잠실" } });
    fireEvent.click(screen.getByRole("button", { name: "경유역 2 삭제" }));

    expect(screen.queryByRole("combobox", { name: "경유역 3" })).toBeNull();
    expect(screen.getByRole("combobox", { name: "경유역 2" })).toHaveValue("잠실");
    expect(add).toBeEnabled();
  });

  it("sends picked via stations in order and drops blank via rows", async () => {
    vi.useFakeTimers();
    const konkuk: Station = { ...station, station_id: "0212", name: "건대입구" };
    // only the via query suggests anything, so exactly one listbox opens
    vi.mocked(searchStations).mockImplementation(async (query) =>
      query.includes("건대") ? [konkuk] : [],
    );
    vi.mocked(searchRoutes).mockResolvedValue([itinerary]);
    render(<JourneySearch onRoutes={vi.fn()} />);

    fireEvent.change(screen.getByRole("combobox", { name: "출발역" }), { target: { value: "강남" } });
    fireEvent.change(screen.getByRole("combobox", { name: "도착역" }), { target: { value: "잠실" } });
    fireEvent.click(screen.getByRole("button", { name: "+ 경유역 추가" }));
    fireEvent.click(screen.getByRole("button", { name: "+ 경유역 추가" }));
    fireEvent.change(screen.getByRole("combobox", { name: "경유역 1" }), { target: { value: "건대" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    const option = screen.getByRole("option", { name: "건대입구 2호선" });
    fireEvent.mouseDown(option);
    fireEvent.click(option);

    fireEvent.click(screen.getByRole("button", { name: "경로 찾기" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(searchRoutes).toHaveBeenCalledWith(
      {
        start: "강남",
        end: "잠실",
        vias: [{ name: "건대입구", station_id: "0212" }],
      },
      expect.any(AbortSignal),
    );
  });

  it("clears via rows when a saved route is chosen", async () => {
    vi.mocked(getRouteHistory).mockResolvedValue({
      most_used: [{ start: station, end: destination }],
      recent: [],
    });
    render(<JourneySearch onRoutes={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 경유역 추가" }));

    fireEvent.click(await screen.findByRole("button", { name: "강남 (2호선) → 홍대입구 (2호선)" }));

    expect(screen.queryByRole("combobox", { name: "경유역 1" })).toBeNull();
  });
```

If the picked option's accessible name or the post-pick input value differs from the above, copy the exact pattern from the existing test "submits the exact selected names and station IDs to route search" in the same file — it is the reference for picking a suggestion.

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- journey-search`
Expected: FAIL (`Unable to find an accessible element with the role "button" and name "+ 경유역 추가"`)

- [ ] **Step 3: Implement**

`frontend/lib/types.ts` — replace `RouteSearchRequest` with:

```ts
export interface ViaStop {
  name: string;
  station_id?: string | null;
}

export interface RouteSearchRequest {
  start: string;
  end: string;
  start_id?: string | null;
  end_id?: string | null;
  vias?: ViaStop[];
}
```

`frontend/components/journey-search.tsx`:

After the `FieldErrors` type add:

```tsx
type ViaRow = {
  key: number;
  value: string;
  station: Station | null;
};

const MAX_VIAS = 3;
```

Inside `JourneySearch`, after the `destinationStation` state:

```tsx
  const [vias, setVias] = useState<ViaRow[]>([]);
  const nextViaKey = useRef(0);
  const updateVia = (key: number, patch: Partial<ViaRow>) =>
    setVias((rows) => rows.map((row) => (row.key === key ? { ...row, ...patch } : row)));
```

In `applyRouteHistory`, add `setVias([]);`.

In `submit`, before `requestController.current?.abort();`:

```tsx
    const viaStops = vias
      .filter((row) => row.value.trim())
      .map((row) => ({
        name: row.value.trim(),
        ...(row.station ? { station_id: row.station.station_id } : {}),
      }));
```

and in the `searchRoutes` request object, after the `end_id` spread:

```tsx
          ...(viaStops.length ? { vias: viaStops } : {}),
```

In the JSX, between the origin and destination `StationAutocomplete`s:

```tsx
        {vias.map((row, index) => (
          <div className="journey-search__via" key={row.key}>
            <StationAutocomplete
              disabled={isLoading}
              id={`via-station-${row.key}`}
              label={`경유역 ${index + 1}`}
              onStationSelect={(station) => updateVia(row.key, { station })}
              onValueChange={(value) => updateVia(row.key, { value })}
              placeholder="경유역을 입력하세요"
              selectedStation={row.station}
              value={row.value}
            />
            <Button
              aria-label={`경유역 ${index + 1} 삭제`}
              disabled={isLoading}
              onClick={() => setVias((rows) => rows.filter((other) => other.key !== row.key))}
              variant="ghost"
            >
              ×
            </Button>
          </div>
        ))}
        <Button
          disabled={isLoading || vias.length >= MAX_VIAS}
          onClick={() =>
            setVias((rows) => [...rows, { key: nextViaKey.current++, value: "", station: null }])
          }
          variant="secondary"
        >
          + 경유역 추가
        </Button>
```

`frontend/app/globals.css`, after the `.journey-search__fields` rule:

```css
.journey-search__via {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
}
```

- [ ] **Step 4: Run frontend checks**

Run: `cd frontend && npm test && npm run typecheck`
Expected: all PASS. The existing exact-object `searchRoutes` assertion must still pass (no `vias` key when no rows).

- [ ] **Step 5: Eyeball it**

Run the dev servers (`uv run uvicorn app.main:app --reload --port 8000` and `cd frontend && npm run dev`), open `http://127.0.0.1:3000` at phone width, add 3 via rows: the × sits beside each input without horizontal scroll. Adjust only the new CSS rule if it doesn't.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/types.ts frontend/components/journey-search.tsx frontend/components/journey-search.test.tsx frontend/app/globals.css
git commit -m "feat: add 경유역 rows to journey search

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs

**Files:**
- Modify: `README.md` (rider features list near line 266)
- Modify: `AGENTS.md` (after the "Route-history chooser" section; `tmap.py` row ~line 190)

- [ ] **Step 1: README** — add after the saved-routes bullet:

```markdown
- Route search takes up to three optional **경유역** (via stations) via
  `+ 경유역 추가`. Tmap has no waypoint support, so each hop is searched
  (and cached) separately and the fastest 10 distinct combinations are shown.
  Joined routes show no fare, since Korean fares depend on total distance.
```

- [ ] **Step 2: AGENTS.md** — add a section after "Route-history chooser":

```markdown
## Via-station search

`POST /api/routes` accepts `vias: [{name, station_id?}]` (≤3 non-blank, else
400; consecutive identical stops → 400). Each hop goes through
`api._search_segment` (the shared cache-or-Tmap path, so hops land in
`route_options_cache` and in Recent Route history). `api._join_segments` folds
hops with `tmap.join_itineraries`, dedupes by `(route, start, end)` leg
sequence and keeps the fastest 10 after each fold. `join_itineraries` merges a
same-line ride through the via into one leg unless it U-turns, sets
`fare=None`, and leaves the via transfer walk at 0. Via searches never append
reversed cache hits.
```

and append to the `tmap.py` row: ` Transit API has no waypoint parameter; `count` is omitted so Tmap's default/max of 10 applies.`

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q && cd frontend && npm test && npm run typecheck && npm run build`
Expected: all PASS / build succeeds

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: describe via-station route search

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
