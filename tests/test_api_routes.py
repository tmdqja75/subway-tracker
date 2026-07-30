import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.db import Database
from app.models import ArrivingTrain, Itinerary, LegStation, OnboardTrain, Station, SubwayLeg
from app.stations import StationRegistry
from app.subway_feed import SubwayApiError
from app.tmap import TmapRouteSearchResult


def make_itinerary(route: str = "수도권2호선") -> Itinerary:
    return Itinerary(
        total_time=660,
        transfer_count=0,
        total_walk_time=0,
        fare=1400,
        legs=[
            SubwayLeg(
                route=route,
                line_key="2호선",
                section_time=660,
                start_name="강남",
                end_name="사당",
                stations=[
                    LegStation(index=0, name="강남", lat=37.4980, lon=127.0277),
                    LegStation(index=1, name="사당", lat=37.4766, lon=126.9816),
                ],
                shape=[[37.4980, 127.0277], [37.4766, 126.9816]],
            )
        ],
        summary=[f"🚇 {route}: 강남 → 사당"],
    )


def make_app(db: Database) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(tmap_app_key="key")
    app.state.manager = SimpleNamespace(db=db)
    app.state.stations = StationRegistry(
        [
            Station(station_id="gangnam-2", name="강남", line="2호선", lat=37.4980, lon=127.0277),
            Station(station_id="gangnam-shin", name="강남", line="신분당선", lat=37.4979, lon=127.0280),
            Station(station_id="sadang-2", name="사당", line="2호선", lat=37.4766, lon=126.9816),
            Station(station_id="sadang-4", name="사당", line="4호선", lat=37.4768, lon=126.9817),
        ]
    )
    app.include_router(router)
    return app


def test_routes_reuses_cache_for_same_station_names_and_lines(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    calls = []

    async def fake_search_routes_with_raw_response(app_key, start_lon, start_lat, end_lon, end_lat):
        calls.append((start_lon, start_lat, end_lon, end_lat))
        return TmapRouteSearchResult(
            itineraries=[make_itinerary(route=f"수도권2호선-{len(calls)}")],
            raw_response_json=f'{{"call":{len(calls)}}}',
        )

    monkeypatch.setattr("app.api.search_routes_with_raw_response", fake_search_routes_with_raw_response)
    client = TestClient(app)

    first = client.post(
        "/api/routes",
        json={"start": "강남", "end": "사당", "start_id": "gangnam-2", "end_id": "sadang-2"},
    )
    second = client.post(
        "/api/routes",
        json={"start": "강남", "end": "사당", "start_id": "gangnam-2", "end_id": "sadang-2"},
    )
    different_line = client.post(
        "/api/routes",
        json={"start": "강남", "end": "사당", "start_id": "gangnam-shin", "end_id": "sadang-2"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert different_line.status_code == 200
    assert len(calls) == 2
    assert first.json() == second.json()
    assert different_line.json()[0]["summary"] == ["🚇 수도권2호선-2: 강남 → 사당"]
    cached_raw = db.conn.execute(
        "SELECT raw_response_json FROM route_options_cache "
        "WHERE start_name = ? AND start_line = ? AND end_name = ? AND end_line = ?",
        ("강남", "2호선", "사당", "2호선"),
    ).fetchone()["raw_response_json"]
    assert cached_raw == '{"call":1}'


def test_cache_route_options_stores_raw_tmap_response(tmp_path):
    db = Database(tmp_path / "tracker.db")
    raw_response = '{"metaData":{"plan":{"itineraries":[]}}}'

    db.cache_route_options(
        "강남",
        "2호선",
        "사당",
        "2호선",
        [make_itinerary()],
        raw_tmap_response=raw_response,
    )

    row = db.conn.execute(
        "SELECT raw_response_json FROM route_options_cache "
        "WHERE start_name = ? AND start_line = ? AND end_name = ? AND end_line = ?",
        ("강남", "2호선", "사당", "2호선"),
    ).fetchone()
    assert row["raw_response_json"] == raw_response


def test_database_migrates_existing_route_cache_to_raw_response_column(tmp_path):
    path = tmp_path / "tracker.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE route_options_cache (
            start_name TEXT NOT NULL,
            start_line TEXT NOT NULL,
            end_name TEXT NOT NULL,
            end_line TEXT NOT NULL,
            itineraries_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (start_name, start_line, end_name, end_line)
        );
        """
    )
    conn.close()

    db = Database(path)

    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(route_options_cache)")}
    assert "raw_response_json" in columns


def test_database_clears_stale_route_cache_when_cache_format_version_changes(tmp_path):
    path = tmp_path / "tracker.db"
    db = Database(path)
    db.cache_route_options("강남", "2호선", "사당", "2호선", [make_itinerary()])
    db.conn.execute("DELETE FROM app_meta WHERE key = 'route_options_cache_format_version'")
    db.conn.commit()
    db.conn.close()

    migrated = Database(path)

    assert migrated.get_cached_route_options("강남", "2호선", "사당", "2호선") is None
    version = migrated.conn.execute(
        "SELECT value FROM app_meta WHERE key = 'route_options_cache_format_version'"
    ).fetchone()["value"]
    assert version == "2"


def test_board_rechecks_that_selected_train_is_still_approaching(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    boarded = []

    class Manager:
        active = SimpleNamespace(leg=make_itinerary().legs[0])

        async def board(self, train_no):
            boarded.append(train_no)

    async def fake_fetch_arrivals(*args, **kwargs):
        return [
            ArrivingTrain(
                train_no="approaching",
                line_name="2호선",
                terminus="성수",
                direction_label="성수행 - 역삼방면",
                eta_seconds=60,
                arrival_msg="역삼 전역 출발",
                stations_away=1,
                stations_away_estimated=False,
                matches_direction=True,
                is_express=False,
            )
        ]

    async def unexpected_fetch_onboard_candidates(*args, **kwargs):
        raise AssertionError("ordinary boarding must not use onboard candidates")

    app.state.manager = Manager()
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")
    monkeypatch.setattr("app.api.fetch_arrivals", fake_fetch_arrivals)
    monkeypatch.setattr("app.api.fetch_onboard_candidates", unexpected_fetch_onboard_candidates)
    client = TestClient(app)

    stale = client.post("/api/journeys/current/board", json={"train_no": "departed"})
    current = client.post(
        "/api/journeys/current/board",
        json={"train_no": "approaching", "retroactive": False},
    )

    assert stale.status_code == 409
    assert current.status_code == 200
    assert boarded == ["approaching"]


def test_arrivals_on_uncovered_leg_returns_both_empty_train_lists(tmp_path):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    uncovered_leg = make_itinerary().legs[0].model_copy(update={"line_key": None})
    app.state.manager = SimpleNamespace(active=SimpleNamespace(leg=uncovered_leg))
    app.state.settings = SimpleNamespace(tmap_app_key="key")

    response = TestClient(app).get("/api/journeys/current/arrivals")

    assert response.status_code == 200
    assert response.json() == {"covered": False, "trains": [], "already_onboard": []}


def make_onboard_train(train_no: str = "onboard") -> OnboardTrain:
    return OnboardTrain(
        train_no=train_no,
        line_name="2호선",
        terminus="사당",
        direction_label="사당 방면",
        station_name="강남",
        station_index=0,
        status="departed",
        observed_at=1_000,
        matches_direction=True,
        is_express=False,
    )


def test_arrivals_returns_stable_arrival_and_onboard_candidate_lists(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    leg = make_itinerary().legs[0]
    app.state.manager = SimpleNamespace(active=SimpleNamespace(leg=leg))
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")
    arrival = ArrivingTrain(
        train_no="approaching", line_name="2호선", terminus="사당",
        direction_label="사당행", eta_seconds=0, arrival_msg="운행 중",
        stations_away=1, stations_away_estimated=False,
        matches_direction=True, is_express=False,
    )
    candidate = make_onboard_train()
    calls = []

    async def fake_fetch_arrivals(*args, **kwargs):
        return [arrival]

    async def fake_fetch_onboard_candidates(base_url, current_leg, *, now):
        calls.append((base_url, current_leg, now))
        return [candidate]

    monkeypatch.setattr("app.api.fetch_arrivals", fake_fetch_arrivals)
    monkeypatch.setattr("app.api.fetch_onboard_candidates", fake_fetch_onboard_candidates)
    monkeypatch.setattr("app.api.time.time", lambda: 1_234)

    response = TestClient(app).get("/api/journeys/current/arrivals")

    assert response.status_code == 200
    assert response.json() == {
        "covered": True,
        "trains": [arrival.model_dump()],
        "already_onboard": [candidate.model_dump()],
    }
    assert calls == [("http://subway.test", leg, 1_234)]


def test_arrivals_keeps_normal_trains_when_optional_onboard_lookup_fails(
    tmp_path, monkeypatch, caplog
):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    leg = make_itinerary().legs[0]
    app.state.manager = SimpleNamespace(active=SimpleNamespace(leg=leg))
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")
    arrival = ArrivingTrain(
        train_no="approaching", line_name="2호선", terminus="사당",
        direction_label="사당행", eta_seconds=0, arrival_msg="운행 중",
        stations_away=1, stations_away_estimated=False,
        matches_direction=True, is_express=False,
    )

    async def fake_fetch_arrivals(*args, **kwargs):
        return [arrival]

    async def failed_fetch_onboard_candidates(*args, **kwargs):
        raise SubwayApiError("onboard feed unavailable")

    monkeypatch.setattr("app.api.fetch_arrivals", fake_fetch_arrivals)
    monkeypatch.setattr("app.api.fetch_onboard_candidates", failed_fetch_onboard_candidates)

    with caplog.at_level("WARNING", logger="app.api"):
        response = TestClient(app).get("/api/journeys/current/arrivals")

    assert response.status_code == 200
    assert response.json() == {
        "covered": True,
        "trains": [arrival.model_dump()],
        "already_onboard": [],
    }
    assert "optional onboard-candidate lookup failed" in caplog.text


def test_retroactive_board_still_fails_when_onboard_lookup_fails(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    app.state.manager = SimpleNamespace(active=SimpleNamespace(leg=make_itinerary().legs[0]))
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")

    async def failed_fetch_onboard_candidates(*args, **kwargs):
        raise SubwayApiError("onboard feed unavailable")

    monkeypatch.setattr("app.api.fetch_onboard_candidates", failed_fetch_onboard_candidates)

    response = TestClient(app).post(
        "/api/journeys/current/board",
        json={"train_no": "onboard", "retroactive": True},
    )

    assert response.status_code == 502


def test_retroactive_board_uses_fresh_onboard_candidate_not_arrival_card(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    candidate = make_onboard_train("fresh-onboard")
    calls = {"board": [], "retroactive": []}

    class Manager:
        active = SimpleNamespace(leg=make_itinerary().legs[0])

        async def board(self, train_no):
            calls["board"].append(train_no)

        async def begin_realtime_tracking_from_onboard(self, onboard):
            calls["retroactive"].append(onboard)

    async def fake_fetch_onboard_candidates(base_url, leg, *, now):
        return [candidate]

    app.state.manager = Manager()
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")
    monkeypatch.setattr("app.api.fetch_onboard_candidates", fake_fetch_onboard_candidates)

    response = TestClient(app).post(
        "/api/journeys/current/board",
        json={"train_no": candidate.train_no, "retroactive": True},
    )

    assert response.status_code == 200
    assert calls == {"board": [], "retroactive": [candidate]}


def test_retroactive_board_rejects_candidate_that_disappeared_since_listing(tmp_path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    app = make_app(db)
    candidate = make_onboard_train("moved-on")
    calls = {"onboard": 0, "retroactive": []}

    class Manager:
        active = SimpleNamespace(leg=make_itinerary().legs[0])

        async def board(self, train_no):
            raise AssertionError("ordinary board must not run in retroactive mode")

        async def begin_realtime_tracking_from_onboard(self, onboard):
            calls["retroactive"].append(onboard)

    arrival_card = ArrivingTrain(
        train_no=candidate.train_no, line_name="2호선", terminus="사당",
        direction_label="사당행", eta_seconds=0, arrival_msg="운행 중",
        stations_away=1, stations_away_estimated=False,
        matches_direction=True, is_express=False,
    )

    async def fake_fetch_arrivals(*args, **kwargs):
        return [arrival_card]

    async def fake_fetch_onboard_candidates(*args, **kwargs):
        calls["onboard"] += 1
        return [candidate] if calls["onboard"] == 1 else []

    app.state.manager = Manager()
    app.state.settings = SimpleNamespace(tmap_app_key="key", subway_api_url="http://subway.test")
    monkeypatch.setattr("app.api.fetch_arrivals", fake_fetch_arrivals)
    monkeypatch.setattr("app.api.fetch_onboard_candidates", fake_fetch_onboard_candidates)
    client = TestClient(app)

    listing = client.get("/api/journeys/current/arrivals")
    stale = client.post(
        "/api/journeys/current/board",
        json={"train_no": candidate.train_no, "retroactive": True},
    )

    assert listing.status_code == 200
    assert listing.json()["trains"] == [arrival_card.model_dump()]
    assert listing.json()["already_onboard"] == [candidate.model_dump()]
    assert stale.status_code == 409
    assert calls == {"onboard": 2, "retroactive": []}


def make_history_itinerary(
    start_name: str,
    start_line: str,
    end_name: str,
    end_line: str,
    *,
    transfer_name: str | None = None,
) -> Itinerary:
    def leg(start: str, line: str, end: str) -> SubwayLeg:
        return SubwayLeg(
            route=f"수도권{line}",
            line_key=line,
            section_time=300,
            start_name=start,
            end_name=end,
            stations=[
                LegStation(index=0, name=start, lat=37.0, lon=127.0),
                LegStation(index=1, name=end, lat=37.1, lon=127.1),
            ],
        )

    legs = [leg(start_name, start_line, transfer_name or end_name)]
    if transfer_name:
        legs.append(leg(transfer_name, end_line, end_name))
    return Itinerary(
        total_time=600,
        transfer_count=int(transfer_name is not None),
        total_walk_time=0,
        fare=1400,
        legs=legs,
        summary=[],
    )


def test_route_history_uses_persisted_journeys_and_resolves_station_endpoints(tmp_path):
    db = Database(tmp_path / "tracker.db")
    routes = [
        ("강남", "2호선", "사당", "4호선"),
        ("B출발", "B선", "B도착", "B선"),
        ("C출발", "C선", "C도착", "C선"),
        ("D출발", "D선", "D도착", "D선"),
        ("E출발", "E선", "E도착", "E선"),
        ("F출발", "F선", "F도착", "F선"),
        ("G출발", "G선", "G도착", "G선"),
        ("H출발", "H선", "H도착", "H선"),
    ]
    stations = []
    for index, (start_name, start_line, end_name, end_line) in enumerate(routes):
        stations.extend(
            [
                Station(
                    station_id=f"start-{index}",
                    name=start_name,
                    line=start_line,
                    lat=37.0 + index,
                    lon=127.0 + index,
                ),
                Station(
                    station_id=f"end-{index}",
                    name=end_name,
                    line=end_line,
                    lat=37.5 + index,
                    lon=127.5 + index,
                ),
            ]
        )
    app = make_app(db)
    app.state.stations = StationRegistry(stations)

    def persist(itinerary: Itinerary, *, created_at: int, state: str) -> None:
        journey_id = db.create_journey(itinerary, state)
        db.update_journey(journey_id, created_at=created_at)

    # This transferred route proves the response uses the first and final
    # subway legs rather than the intermediate transfer station/line.
    first_route = make_history_itinerary(*routes[0], transfer_name="환승")
    persist(first_route, created_at=100, state="awaiting_board")
    persist(first_route, created_at=106, state="completed")
    persist(first_route, created_at=110, state="cancelled")
    for route, created_at, state in zip(
        routes[1:],
        [109, 109, 107, 105, 104, 103, 111],
        [
            "on_train",
            "pushing",
            "push_failed",
            "completed",
            "cancelled",
            "awaiting_board",
            "completed",
        ],
        strict=True,
    ):
        persist(make_history_itinerary(*route), created_at=created_at, state=state)

    # Bad persisted records must not prevent valid history from being served.
    persist(
        make_history_itinerary("없는출발", "없는선", "없는도착", "없는선"),
        created_at=120,
        state="completed",
    )
    persist(
        Itinerary(
            total_time=0,
            transfer_count=0,
            total_walk_time=0,
            fare=None,
            legs=[],
            summary=[],
        ),
        created_at=119,
        state="completed",
    )
    db.conn.execute(
        "INSERT INTO journeys (created_at, state, itinerary_json) VALUES (?, ?, ?)",
        (121, "completed", "not valid JSON"),
    )
    db.conn.commit()

    response = TestClient(app).get("/api/routes/history")

    assert response.status_code == 200
    payload = response.json()
    items = [
        {
            "start": stations[index * 2].model_dump(),
            "end": stations[index * 2 + 1].model_dump(),
        }
        for index in range(len(routes))
    ]
    # A is most-used. H is the newest tied route; C and B have the same
    # timestamp, so C wins the deterministic newer-id tie-break.
    assert payload == {
        "most_used": [items[index] for index in [0, 7, 2, 1, 3]],
        "recent": [items[index] for index in [7, 0, 2, 1, 3]],
    }
    assert [item["start"]["name"] for item in payload["recent"]] == [
        routes[index][0] for index in [7, 0, 2, 1, 3]
    ]
    assert payload["recent"][1]["start"] == {
        "station_id": "start-0",
        "name": "강남",
        "line": "2호선",
        "lat": 37.0,
        "lon": 127.0,
    }
    assert payload["recent"][1]["end"]["line"] == "4호선"