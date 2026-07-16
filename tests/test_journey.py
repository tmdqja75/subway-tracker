"""End-to-end state machine test with a scripted train feed and a fake Reitti."""

import asyncio
from pathlib import Path

import pytest

from app import journey as journey_mod
from app.config import Settings
from app.db import Database
from app.journey import ActiveJourney, JourneyManager
from app.models import Itinerary, JourneyState, LegStation, SubwayLeg, TrackPoint
from app.reitti import ReittiError


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


async def test_failed_push_exposes_details_and_retry_resends_all_points(manager, monkeypatch):
    attempts = []

    async def fake_push(url, token, points):
        attempts.append(points)
        if len(attempts) == 1:
            raise ReittiError(
                "Reitti auth failed (401)",
                reason="authentication",
                sent_points=0,
            )
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    j = await manager.start_journey(make_itinerary())
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False))

    await manager._push_to_reitti(j)

    assert j.state == JourneyState.PUSH_FAILED
    assert manager.snapshot()["transfer"] == {
        "reason": "authentication",
        "message": "Reitti 인증이 거부됐어요. 서버 토큰을 확인하세요.",
        "detail": "Reitti auth failed (401)",
        "sent_points": 0,
        "total_points": 2,
        "can_retry": True,
    }

    await manager.retry_push()

    assert j.state == JourneyState.COMPLETED
    assert len(attempts) == 2
    assert attempts[1] == attempts[0]
    assert manager.snapshot()["transfer"] is None


def test_failed_push_is_resumed_after_restart(manager):
    j = asyncio.run(manager.start_journey(make_itinerary()))
    manager.db.update_journey(
        j.id,
        state=JourneyState.PUSH_FAILED,
        error="Reitti unreachable after 1 points: connection reset",
        error_reason="connection",
        error_sent_points=1,
        error_total_points=3,
    )
    resumed = JourneyManager(manager.db, manager.settings)

    resumed.resume_from_db()

    assert resumed.active is not None
    assert resumed.active.state == JourneyState.PUSH_FAILED
    assert resumed.snapshot()["transfer"] == {
        "reason": "connection",
        "message": "Reitti 서버에 연결하지 못했어요. 네트워크와 서버 상태를 확인하세요.",
        "detail": "Reitti unreachable after 1 points: connection reset",
        "sent_points": 1,
        "total_points": 3,
        "can_retry": True,
    }


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


async def test_waiting_for_train_does_not_reset_linestring_log_clock(manager, monkeypatch):
    shape = [
        [37.4837, 127.0354],
        [37.4850, 127.0400],
        [37.4862, 127.0440],
        [37.4870, 127.0468],
        [37.4885, 127.0500],
        [37.4909, 127.0553],
    ]

    async def fake_positions(key, line):
        return [{"trainNo": "3001", "statnNm": "개포동", "trainSttus": "1"}]

    class FakeTime:
        @staticmethod
        def time():
            return 1_000_040.0

    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)
    monkeypatch.setattr(journey_mod, "time", FakeTime)

    j = await manager.start_journey(make_itinerary(shape=shape))
    j.state = JourneyState.ON_TRAIN
    j.train_no = "3001"
    j.tracking_mode = "realtime"
    j.leg_started_at = 1_000_000
    j.prepare_leg()
    j.last_arrival_time = 1_000_000.0
    manager._emit_at(j, shape[0][0], shape[0][1], 1_000_000, estimated=False)

    await manager._realtime_update(j)
    await manager._complete_leg(j)

    logged = {
        (round(point.lat, 4), round(point.lon, 4))
        for point in manager.db.get_points(j.id)
    }
    assert (37.4850, 127.0400) in logged
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


def test_realtime_polling_slows_while_cruising_and_speeds_near_stations(manager, monkeypatch):
    manager.settings.poll_interval_seconds = 5

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    monkeypatch.setattr(journey_mod, "time", FakeTime)

    j = ActiveJourney(1, make_itinerary())
    j.state = JourneyState.ON_TRAIN
    j.train_no = "3001"
    j.tracking_mode = "realtime"
    j.prepare_leg()
    j.anchor_idx = 0
    j.anchor_phase = "segment"
    j.anchor_time = 1_000_000.0

    FakeTime._t = 1_000_030.0
    assert manager._next_poll_delay(j) == 30

    FakeTime._t = 1_000_070.0
    assert manager._next_poll_delay(j) == 5


def test_short_segments_do_not_spend_entire_segment_in_fast_poll_window(manager, monkeypatch):
    manager.settings.poll_interval_seconds = 5

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    monkeypatch.setattr(journey_mod, "time", FakeTime)

    itinerary = make_itinerary()
    itinerary.legs[0].section_time = 80  # 40s per station hop
    j = ActiveJourney(1, itinerary)
    j.state = JourneyState.ON_TRAIN
    j.train_no = "3001"
    j.tracking_mode = "realtime"
    j.prepare_leg()
    j.anchor_idx = 0
    j.anchor_phase = "segment"
    j.anchor_time = 1_000_000.0

    FakeTime._t = 1_000_010.0
    assert manager._next_poll_delay(j) == 14

    FakeTime._t = 1_000_025.0
    assert manager._next_poll_delay(j) == 5


async def test_realtime_tick_skips_seoul_fetch_until_next_poll_deadline(manager, monkeypatch):
    manager.settings.poll_interval_seconds = 5
    calls = []

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    async def fake_positions(key, line):
        calls.append(FakeTime._t)
        return [{"trainNo": "3001", "statnNm": "양재", "trainSttus": "2"}]

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)

    j = ActiveJourney(1, make_itinerary())
    j.state = JourneyState.ON_TRAIN
    j.train_no = "3001"
    j.tracking_mode = "realtime"
    j.prepare_leg()

    await manager._tick(j)
    assert calls == [1_000_000.0]

    FakeTime._t = 1_000_005.0
    await manager._tick(j)
    assert calls == [1_000_000.0]

    FakeTime._t = 1_000_030.0
    await manager._tick(j)
    assert calls == [1_000_000.0, 1_000_030.0]


async def test_missing_train_fallback_is_based_on_elapsed_time(manager, monkeypatch):
    manager.settings.poll_interval_seconds = 5

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    async def fake_positions(key, line):
        return []

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(journey_mod, "fetch_positions", fake_positions)

    j = ActiveJourney(1, make_itinerary())
    j.state = JourneyState.ON_TRAIN
    j.train_no = "9999"
    j.tracking_mode = "realtime"
    j.prepare_leg()

    for elapsed in (0, 5, 10, 15):
        FakeTime._t = 1_000_000.0 + elapsed
        await manager._tick(j)
        assert j.tracking_mode == "realtime"

    FakeTime._t = 1_000_091.0
    await manager._tick(j)
    assert j.tracking_mode == "timer"
