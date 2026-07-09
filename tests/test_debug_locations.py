from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.db import Database
from app.models import Itinerary, LegStation, SubwayLeg, TrackPoint


def make_itinerary() -> Itinerary:
    stations = [
        LegStation(index=0, name="선릉", lat=37.505253, lon=127.048661),
        LegStation(index=1, name="왕십리", lat=37.561186, lon=127.038769),
    ]
    leg = SubwayLeg(
        route="수인분당선",
        line_key="수인분당선",
        section_time=660,
        start_name="선릉",
        end_name="왕십리",
        stations=stations,
        shape=[[37.505253, 127.048661], [37.561186, 127.038769]],
    )
    return Itinerary(
        total_time=660,
        transfer_count=0,
        total_walk_time=0,
        fare=1400,
        legs=[leg],
        summary=["🚇 수인분당선: 선릉 → 왕십리"],
    )


def test_debug_locations_returns_journey_points_and_route_context(tmp_path):
    db = Database(tmp_path / "tracker.db")
    journey_id = db.create_journey(make_itinerary(), "on_train")
    db.update_journey(journey_id, train_no="6106", tracking_mode="realtime")
    db.add_point(
        journey_id,
        0,
        TrackPoint(lat=37.505253, lon=127.048661, ts=1_783_573_682, estimated=False),
    )
    db.add_point(
        journey_id,
        0,
        TrackPoint(lat=37.561186, lon=127.038769, ts=1_783_574_226, estimated=True),
    )

    app = FastAPI()
    app.state.manager = SimpleNamespace(db=db)
    app.include_router(router)

    response = TestClient(app).get("/api/debug/locations")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["journeys"]) == 1
    journey = payload["journeys"][0]
    assert journey["journey_id"] == journey_id
    assert journey["state"] == "on_train"
    assert journey["train_no"] == "6106"
    assert journey["summary"] == ["🚇 수인분당선: 선릉 → 왕십리"]
    assert journey["legs"][0]["route"] == "수인분당선"
    assert journey["legs"][0]["shape"] == [[37.505253, 127.048661], [37.561186, 127.038769]]
    assert journey["points"] == [
        {
            "lat": 37.505253,
            "lon": 127.048661,
            "ts": 1_783_573_682,
            "estimated": False,
            "leg_idx": 0,
        },
        {
            "lat": 37.561186,
            "lon": 127.038769,
            "ts": 1_783_574_226,
            "estimated": True,
            "leg_idx": 0,
        },
    ]


def test_debug_locations_supports_limit(tmp_path):
    db = Database(tmp_path / "tracker.db")
    for _ in range(3):
        db.create_journey(make_itinerary(), "cancelled")

    app = FastAPI()
    app.state.manager = SimpleNamespace(db=db)
    app.include_router(router)

    response = TestClient(app).get("/api/debug/locations?limit=2")

    assert response.status_code == 200
    journey_ids = [journey["journey_id"] for journey in response.json()["journeys"]]
    assert journey_ids == [3, 2]
