from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.db import Database
from app.models import Itinerary, LegStation, Station, SubwayLeg
from app.stations import StationRegistry


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

    async def fake_search_routes(app_key, start_lon, start_lat, end_lon, end_lat):
        calls.append((start_lon, start_lat, end_lon, end_lat))
        return [make_itinerary(route=f"수도권2호선-{len(calls)}")]

    monkeypatch.setattr("app.api.search_routes", fake_search_routes)
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