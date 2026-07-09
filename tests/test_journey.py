"""End-to-end state machine test with a scripted train feed and a fake Reitti."""

import asyncio
from pathlib import Path

import pytest

from app import journey as journey_mod
from app.config import Settings
from app.db import Database
from app.journey import JourneyManager
from app.models import Itinerary, JourneyState, LegStation, SubwayLeg


def make_itinerary(shape: list | None = None) -> Itinerary:
    stations = [
        LegStation(index=0, name="양재", lat=37.4837, lon=127.0354),
        LegStation(index=1, name="매봉", lat=37.4870, lon=127.0468),
        LegStation(index=2, name="도곡", lat=37.4909, lon=127.0553),
    ]
    leg = SubwayLeg(
        route="수도권3호선", line_key="3호선", section_time=240,
        start_name="양재", end_name="도곡", stations=stations,
        shape=shape or [],
    )
    return Itinerary(
        total_time=240, transfer_count=0, total_walk_time=0, fare=1400,
        legs=[leg], summary=["🚇 수도권3호선: 양재 → 도곡"],
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch):
    settings = Settings(
        seoul_api_key="k", tmap_app_key="k",
        reitti_url="http://reitti.test", reitti_token="t",
        poll_interval_seconds=0, db_path=tmp_path / "t.db",
        _env_file=None,
    )
    return JourneyManager(Database(settings.db_path), settings)


async def test_full_leg_completes_and_pushes(manager, monkeypatch):
    # scripted feed: train 3001 walks the leg then arrives at the end station
    feed = [
        [{"trainNo": "3001", "statnNm": "양재", "trainSttus": "1"}],
        [{"trainNo": "3001", "statnNm": "양재", "trainSttus": "2"}],   # departed
        [{"trainNo": "3001", "statnNm": "매봉", "trainSttus": "1"}],
        [{"trainNo": "3001", "statnNm": "도곡", "trainSttus": "0"}],   # approaching end
    ]
    calls = iter(feed)

    async def fake_positions(key, line):
        return next(calls, feed[-1])

    pushed = []

    async def fake_push(url, token, points):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)
    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    j = await manager.start_journey(make_itinerary())
    assert j.state == JourneyState.AWAITING_BOARD

    await manager.board("3001")
    assert j.state == JourneyState.ON_TRAIN
    assert j.tracking_mode == "realtime"

    for _ in range(50):
        await asyncio.sleep(0.05)
        if j.state != JourneyState.ON_TRAIN:
            break
    assert j.state == JourneyState.COMPLETED
    assert pushed, "points must be pushed to Reitti"
    assert pushed[-1].lat == pytest.approx(37.4909)  # ends at 도곡


async def test_uncovered_line_uses_timer_mode(manager, monkeypatch):
    async def fake_push(url, token, points):
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    itinerary = make_itinerary()
    itinerary.legs[0].line_key = None  # not covered by realtime API

    j = await manager.start_journey(itinerary)
    await manager.board(None)
    assert j.tracking_mode == "timer"
    j.leg_started_at -= 3600  # pretend the ride already ran its course

    for _ in range(50):
        await asyncio.sleep(0.05)
        if j.state != JourneyState.ON_TRAIN:
            break
    assert j.state == JourneyState.COMPLETED


async def test_arrivals_log_linestring_path(manager, monkeypatch):
    # track geometry with curve vertices between the three stations
    shape = [
        [37.4837, 127.0354],           # 양재
        [37.4850, 127.0400],           # curve vertex
        [37.4862, 127.0440],           # curve vertex
        [37.4870, 127.0468],           # 매봉
        [37.4885, 127.0500],           # curve vertex
        [37.4909, 127.0553],           # 도곡
    ]
    feed = [
        [{"trainNo": "3001", "statnNm": "양재", "trainSttus": "2"}],
        [{"trainNo": "3001", "statnNm": "매봉", "trainSttus": "1"}],  # arrival -> log segment
        [{"trainNo": "3001", "statnNm": "도곡", "trainSttus": "1"}],
    ]
    calls = iter(feed)

    async def fake_positions(key, line):
        return next(calls, feed[-1])

    pushed = []

    async def fake_push(url, token, points):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)
    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    # advance the clock 5s per lookup so points don't collide on epoch seconds
    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            FakeTime._t += 5
            return FakeTime._t

    monkeypatch.setattr(journey_mod, "time", FakeTime)

    j = await manager.start_journey(make_itinerary(shape=shape))
    await manager.board("3001")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if j.state != JourneyState.ON_TRAIN:
            break
    assert j.state == JourneyState.COMPLETED
    logged = {(round(p.lat, 4), round(p.lon, 4)) for p in pushed}
    # curve vertices from the linestring must appear in the logged path
    assert (37.4850, 127.0400) in logged or (37.4862, 127.0440) in logged
    assert (37.4885, 127.0500) in logged


async def test_transfer_walk_linestring_logged_before_next_subway_leg(manager, monkeypatch):
    first = SubwayLeg(
        route="수도권2호선", line_key=None, section_time=120,
        start_name="강남", end_name="사당",
        stations=[
            LegStation(index=0, name="강남", lat=37.4980, lon=127.0277),
            LegStation(index=1, name="사당", lat=37.4766, lon=126.9816),
        ],
        shape=[[37.4980, 127.0277], [37.4766, 126.9816]],
        transfer_walk_time=120,
        transfer_walk_shape=[
            [37.4766, 126.9816],
            [37.4767, 126.9813],
            [37.4768, 126.9817],
        ],
    )
    second = SubwayLeg(
        route="수도권4호선", line_key=None, section_time=120,
        start_name="사당", end_name="서울역",
        stations=[
            LegStation(index=0, name="사당", lat=37.4768, lon=126.9817),
            LegStation(index=1, name="서울역", lat=37.5535, lon=126.9728),
        ],
        shape=[[37.4768, 126.9817], [37.5535, 126.9728]],
    )
    itinerary = Itinerary(
        total_time=360, transfer_count=1, total_walk_time=120, fare=1600,
        legs=[first, second],
        summary=[
            "🚇 수도권2호선: 강남 → 사당",
            "🚶 도보 2분",
            "🚇 수도권4호선: 사당 → 서울역",
        ],
    )
    pushed = []

    async def fake_push(url, token, points):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            FakeTime._t += 10
            return FakeTime._t

    monkeypatch.setattr(journey_mod, "time", FakeTime)

    j = await manager.start_journey(itinerary)
    await manager.board(None)
    await manager.alight()
    assert j.state == JourneyState.AWAITING_BOARD

    await manager.board(None)
    await manager.alight()

    assert j.state == JourneyState.COMPLETED
    logged = {(round(p.lat, 4), round(p.lon, 4)) for p in pushed}
    assert (37.4767, 126.9813) in logged


async def test_missed_train_returns_to_picker(manager, monkeypatch):
    async def fake_positions(key, line):
        return []

    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)

    j = await manager.start_journey(make_itinerary())
    await manager.board("9999")
    await manager.missed_train()
    assert j.state == JourneyState.AWAITING_BOARD
    assert j.train_no is None
