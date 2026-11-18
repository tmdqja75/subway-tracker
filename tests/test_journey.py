"""End-to-end state machine test with a scripted train feed and a fake Reitti."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app import journey as journey_mod
from app.config import Settings
from app.db import Database
from app.journey import ActiveJourney, JourneyManager, is_one_stop_remaining
from app.models import Itinerary, JourneyState, LegStation, OnboardTrain, SubwayLeg, TrackPoint, TrainStatus
from app.reitti import ReittiError
from app.subway_feed import LegTrainStatus, StationSnapshot, SubwayApiError, TrainEntry


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


class FakeNotificationSender:
    def __init__(self, gate: asyncio.Event | None = None):
        self.calls: list[tuple[int, int, str]] = []
        self.gate = gate

    async def send_claimed_deliveries(
        self, journey_id: int, leg_idx: int, destination_name: str,
    ) -> None:
        self.calls.append((journey_id, leg_idx, destination_name))
        if self.gate is not None:
            await self.gate.wait()


def test_history_estimated_column_migrates_existing_journeys(tmp_path: Path):
    db_path = tmp_path / "pre-history-estimated.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE journeys (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL, "
        "state TEXT NOT NULL, itinerary_json TEXT NOT NULL, current_leg_idx INTEGER NOT NULL DEFAULT 0, "
        "train_no TEXT, tracking_mode TEXT, leg_started_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO journeys (created_at, state, itinerary_json) VALUES (1, 'awaiting_board', '{}')"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)

    migrated = db.get_journey(1)
    assert migrated is not None
    assert migrated["history_estimated"] == 0


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        subway_api_url="http://subway.test", tmap_app_key="k",
        reitti_url="http://reitti.test", reitti_token="t",
        poll_interval_seconds=0, db_path=tmp_path / "t.db",
        _env_file=None,
    )


@pytest.fixture
def manager(tmp_path: Path, monkeypatch):
    manager = JourneyManager(Database(tmp_path / "t.db"), _settings(tmp_path))
    # Platform polling has its own tests (live_manager); keep the rest quiet.
    monkeypatch.setattr(manager, "_start_platform_watch", lambda: None)
    return manager


def _line3_snap(train_at: str | None = None, status: str = "") -> list[StationSnapshot]:
    """양재 → 매봉 → 도곡 line snapshot; train 3001 (dn bucket) at `train_at`."""
    return [
        StationSnapshot(
            name=name, up=[],
            dn=[TrainEntry(status=status, kind="일반", dest="도곡", no="3001")] if name == train_at else [],
        )
        for name in ("양재", "매봉", "도곡")
    ]


@pytest.fixture
async def live_manager(tmp_path: Path, monkeypatch):
    manager = JourneyManager(Database(tmp_path / "t.db"), _settings(tmp_path))
    manager.fetch_count = 0

    async def fake_fetch_line_snapshot(base_url, line_id):
        manager.fetch_count += 1
        return _line3_snap()

    monkeypatch.setattr(journey_mod, "fetch_line_snapshot", fake_fetch_line_snapshot)
    monkeypatch.setattr(journey_mod, "PLATFORM_POLL_SECONDS", 0.01)
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    yield manager
    manager._stop_platform_watch()


def _uncovered_then_covered_itinerary() -> Itinerary:
    leg = make_itinerary().legs[0]
    return Itinerary(
        total_time=480, transfer_count=1, total_walk_time=0, fare=1400,
        legs=[leg.model_copy(update={"line_key": None}), leg], summary=["timer", "3호선"],
    )


def _watching(manager: JourneyManager) -> bool:
    return manager._platform_task is not None and not manager._platform_task.done()


async def test_platform_watch_records_snapshots_while_awaiting_board(live_manager):
    j = await live_manager.start_journey(make_itinerary())
    await asyncio.sleep(0.05)
    assert len(j.platform_snapshots) >= 2

    await live_manager.board("3001")

    assert live_manager._platform_task is None
    assert len(j.platform_snapshots) == 0
    fetched = live_manager.fetch_count
    await asyncio.sleep(0.05)
    assert live_manager.fetch_count == fetched


async def test_platform_watch_not_started_for_uncovered_leg(live_manager):
    itinerary = make_itinerary()
    uncovered = itinerary.model_copy(update={"legs": [itinerary.legs[0].model_copy(update={"line_key": None})]})

    await live_manager.start_journey(uncovered)

    assert live_manager._platform_task is None


async def test_platform_watch_stops_after_max_duration(live_manager, monkeypatch):
    monkeypatch.setattr(journey_mod, "PLATFORM_WATCH_MAX_SECONDS", 0.03)
    await live_manager.start_journey(make_itinerary())
    await asyncio.sleep(0.1)

    assert not _watching(live_manager)
    fetched = live_manager.fetch_count
    await asyncio.sleep(0.05)
    assert live_manager.fetch_count == fetched


async def test_platform_watch_continues_after_feed_error(live_manager, monkeypatch):
    calls = []

    async def flaky_fetch(base_url, line_id):
        calls.append(line_id)
        if len(calls) == 1:
            raise SubwayApiError("feed hiccup")
        return _line3_snap()

    monkeypatch.setattr(journey_mod, "fetch_line_snapshot", flaky_fetch)
    j = await live_manager.start_journey(make_itinerary())
    await asyncio.sleep(0.05)

    assert len(calls) >= 2
    assert len(j.platform_snapshots) >= 1


async def test_platform_watch_restarts_on_transfer_missed_and_resume(live_manager):
    j = await live_manager.start_journey(_uncovered_then_covered_itinerary())
    assert not _watching(live_manager)  # first leg has no realtime feed

    await live_manager.board(None)
    await live_manager.alight()
    assert j.leg_idx == 1 and _watching(live_manager)

    await live_manager.board("3001")
    assert not _watching(live_manager)
    await live_manager.missed_train()
    assert _watching(live_manager)

    resumed = JourneyManager(live_manager.db, live_manager.settings)
    resumed.resume_from_db()
    assert _watching(resumed)
    resumed._stop_platform_watch()

    await live_manager.cancel()
    assert not _watching(live_manager)


async def test_leg_snapshot_reuses_recent_snapshot(live_manager, monkeypatch):
    monkeypatch.setattr(live_manager, "_start_platform_watch", lambda: None)
    j = await live_manager.start_journey(make_itinerary())

    await live_manager.leg_snapshot(max_age=10)
    await live_manager.leg_snapshot(max_age=10)
    assert live_manager.fetch_count == 1

    await live_manager.leg_snapshot(max_age=0)
    assert live_manager.fetch_count == 2
    assert len(j.platform_snapshots) == 2


async def test_concurrent_leg_snapshot_calls_share_one_fetch(live_manager, monkeypatch):
    async def slow_fetch(base_url, line_id):
        live_manager.fetch_count += 1
        await asyncio.sleep(0.02)
        return _line3_snap()

    monkeypatch.setattr(journey_mod, "fetch_line_snapshot", slow_fetch)
    monkeypatch.setattr(live_manager, "_start_platform_watch", lambda: None)
    await live_manager.start_journey(make_itinerary())

    await asyncio.gather(live_manager.leg_snapshot(max_age=10), live_manager.leg_snapshot(max_age=10))

    assert live_manager.fetch_count == 1


async def test_leg_snapshot_drops_result_when_leg_changed_mid_fetch(live_manager, monkeypatch):
    async def slow_fetch(base_url, line_id):
        await asyncio.sleep(0.02)
        return _line3_snap()

    monkeypatch.setattr(journey_mod, "fetch_line_snapshot", slow_fetch)
    monkeypatch.setattr(live_manager, "_start_platform_watch", lambda: None)
    first = await live_manager.start_journey(make_itinerary())
    pending = asyncio.create_task(live_manager.leg_snapshot(max_age=0))
    await asyncio.sleep(0.005)

    await live_manager.cancel()
    second = await live_manager.start_journey(make_itinerary())
    await pending

    assert len(first.platform_snapshots) == 0
    assert len(second.platform_snapshots) == 0


async def test_picker_snapshot_still_buffers_after_watch_cap(live_manager, monkeypatch):
    monkeypatch.setattr(journey_mod, "PLATFORM_WATCH_MAX_SECONDS", 0.03)
    j = await live_manager.start_journey(make_itinerary())
    await asyncio.sleep(0.1)
    assert not _watching(live_manager)
    buffered = len(j.platform_snapshots)

    await live_manager.leg_snapshot(max_age=0)

    assert len(j.platform_snapshots) == buffered + 1


async def test_platform_polling_never_reaches_a_real_feed(manager, no_live_subway_feed):
    await manager.start_journey(make_itinerary())
    resumed = JourneyManager(manager.db, manager.settings)
    resumed.resume_from_db()
    await asyncio.sleep(0.01)
    resumed._stop_platform_watch()

    assert no_live_subway_feed
    assert set(no_live_subway_feed) == {"http://subway.test"}


async def test_starting_next_journey_keeps_completed_history_completed(manager):
    completed = await manager.start_journey(make_itinerary())
    completed.state = JourneyState.COMPLETED
    manager.db.update_journey(completed.id, state=JourneyState.COMPLETED)

    next_journey = await manager.start_journey(make_itinerary())

    assert manager.db.get_journey(completed.id)["state"] == JourneyState.COMPLETED
    assert next_journey.state == JourneyState.AWAITING_BOARD
    assert manager.db.get_journey(next_journey.id)["state"] == JourneyState.AWAITING_BOARD


@pytest.mark.parametrize(
    "state",
    [
        JourneyState.AWAITING_BOARD,
        JourneyState.ON_TRAIN,
        JourneyState.PUSHING,
        JourneyState.PUSH_FAILED,
    ],
)
async def test_starting_next_journey_cancels_existing_active_journey(manager, state):
    existing = await manager.start_journey(make_itinerary())
    existing.state = state
    manager.db.update_journey(existing.id, state=state)

    next_journey = await manager.start_journey(make_itinerary())

    assert manager.db.get_journey(existing.id)["state"] == JourneyState.CANCELLED
    assert next_journey.state == JourneyState.AWAITING_BOARD


async def test_full_leg_completes_and_pushes(manager, monkeypatch):
    # scripted feed: train 3001 walks the leg then arrives at the end station
    feed = [
        LegTrainStatus(leg_index=0, status="arrived", station_name="양재"),
        LegTrainStatus(leg_index=0, status="departed", station_name="양재"),
        LegTrainStatus(leg_index=1, status="arrived", station_name="매봉"),
        LegTrainStatus(leg_index=2, status="approaching", station_name="도곡"),
    ]
    calls = iter(feed)

    async def fake_locate_train(base_url, leg, train_no):
        return next(calls, feed[-1])

    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)
    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    j = await manager.start_journey(make_itinerary())
    assert j.state == JourneyState.AWAITING_BOARD

    await manager.board("3001")
    assert j.state == JourneyState.ON_TRAIN
    assert j.tracking_mode == "realtime"

    for _ in range(50):
        await asyncio.sleep(0.05)
        if j.state in (JourneyState.COMPLETED, JourneyState.PUSH_FAILED):
            break
    assert j.state == JourneyState.COMPLETED
    assert pushed, "points must be pushed to Reitti"
    assert pushed[-1].lat == pytest.approx(37.4909)  # ends at 도곡


async def test_uncovered_line_uses_timer_mode(manager, monkeypatch):
    async def fake_push(url, token, points, *, on_progress=None):
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
        if j.state in (JourneyState.COMPLETED, JourneyState.PUSH_FAILED):
            break
    assert j.state == JourneyState.COMPLETED


async def test_local_realtime_interpolation_reports_the_next_station_for_between_status(manager):
    journey = await manager.start_journey(make_itinerary())
    journey.anchor_idx = 0
    journey.anchor_phase = "segment"
    journey.anchor_time = 0
    journey.last_status = TrainStatus(
        train_no="3001",
        station_name="양재",
        station_index=0,
        status="departed",
        lat=journey.leg.stations[0].lat,
        lon=journey.leg.stations[0].lon,
        updated_at=0,
    )

    manager._local_realtime_update(journey, now=30)

    assert journey.last_status is not None
    assert journey.last_status.status == "between"
    assert journey.last_status.station_index == 1


async def test_failed_push_exposes_details_and_retry_resends_all_points(manager, monkeypatch):
    attempts = []

    async def fake_push(url, token, points, *, on_progress=None):
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
        "remaining_points": 2,
        "progress_percent": 0,
        "can_retry": True,
    }

    await manager.retry_push()
    for _ in range(10):
        await asyncio.sleep(0)
        if j.state == JourneyState.COMPLETED:
            break

    assert j.state == JourneyState.COMPLETED
    assert len(attempts) == 2
    assert attempts[1] == attempts[0]
    assert manager.snapshot()["transfer"] == {
        "sent_points": 2,
        "total_points": 2,
        "remaining_points": 0,
        "progress_percent": 100,
    }


async def test_commit_to_timeline_sends_the_journey_time_range(manager, monkeypatch):
    calls = []

    async def fake_commit(url, token, device_id, t_start_ms, t_end_ms):
        calls.append((url, token, device_id, t_start_ms, t_end_ms))

    monkeypatch.setattr(journey_mod, "commit_workbench_patch", fake_commit)
    j = await manager.start_journey(make_itinerary())
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False))
    j.state = JourneyState.COMPLETED
    manager.db.update_journey(j.id, state=JourneyState.COMPLETED)

    await manager.commit_to_timeline()

    assert calls == [("http://reitti.test", "t", manager.settings.reitti_device_id, 1000, 3000)]


async def test_commit_to_timeline_requires_a_completed_journey(manager, monkeypatch):
    called = False

    async def fake_commit(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(journey_mod, "commit_workbench_patch", fake_commit)
    j = await manager.start_journey(make_itinerary())

    with pytest.raises(ValueError):
        await manager.commit_to_timeline()

    assert called is False


async def test_commit_to_timeline_propagates_reitti_errors(manager, monkeypatch):
    async def fake_commit(url, token, device_id, t_start_ms, t_end_ms):
        raise ReittiError("boom", reason="rejected")

    monkeypatch.setattr(journey_mod, "commit_workbench_patch", fake_commit)
    j = await manager.start_journey(make_itinerary())
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    j.state = JourneyState.COMPLETED
    manager.db.update_journey(j.id, state=JourneyState.COMPLETED)

    with pytest.raises(ReittiError):
        await manager.commit_to_timeline()


async def test_debug_retry_push_resends_cancelled_journey_points(manager, monkeypatch):
    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    journey = await manager.start_journey(make_itinerary())
    manager.db.add_point(journey.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    await manager.cancel()

    await manager.retry_debug_push(journey.id)
    assert manager._push_task is not None
    await manager._push_task

    assert pushed == [TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False)]
    assert manager.db.get_journey(journey.id)["state"] == JourneyState.COMPLETED


async def test_debug_retry_push_resends_failed_journey_points_without_active_tracker(manager, monkeypatch):
    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    journey_id = manager.db.create_journey(make_itinerary(), JourneyState.PUSH_FAILED)
    manager.db.add_point(
        journey_id,
        0,
        TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False),
    )

    await manager.retry_debug_push(journey_id)
    assert manager._push_task is not None
    await manager._push_task

    assert pushed == [TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False)]
    assert manager.db.get_journey(journey_id)["state"] == JourneyState.COMPLETED


async def test_debug_retry_push_completes_a_cancelled_journey_without_points(manager, monkeypatch):
    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    journey_id = manager.db.create_journey(make_itinerary(), JourneyState.CANCELLED)

    await manager.retry_debug_push(journey_id)
    assert manager._push_task is not None
    await manager._push_task

    assert pushed == []
    assert manager.db.get_journey(journey_id)["state"] == JourneyState.COMPLETED


async def test_alight_far_ahead_of_tracked_train_rebuilds_leg_on_schedule(manager, monkeypatch):
    async def fake_push(url, token, points, *, on_progress=None):
        return len(points)

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    monkeypatch.setattr(journey_mod, "time", FakeTime)

    j = await manager.start_journey(make_itinerary())
    await manager.board("3001")
    manager._stop_tracker()
    # the tracked (wrong) train is still at the origin when the rider alights
    # 30 minutes later; its last live point sits 5s before the alight press
    FakeTime._t += 1800
    manager._emit_at(j, 37.4837, 127.0354, int(FakeTime._t) - 5, estimated=False)
    j.last_arrival_time = FakeTime._t - 5

    await manager.alight()
    await manager._push_task

    points = manager.db.get_points(j.id)
    assert (points[0].ts, points[0].lat, points[0].estimated) == (1_000_000, 37.4837, False)
    assert (points[-1].ts, points[-1].lat, points[-1].estimated) == (1_001_800, 37.4909, False)
    # the middle station is passed halfway through, not squeezed into the last seconds
    middle = [p.ts for p in points if p.lat == 37.4870]
    assert middle and all(1_000_800 < ts < 1_001_000 for ts in middle)
    # the stale live point from the trailing train is gone
    assert not any(p.ts == 1_001_795 for p in points)


async def test_final_alight_starts_background_push_and_exposes_live_progress(manager, monkeypatch):
    first_point_sent = asyncio.Event()
    finish_push = asyncio.Event()

    async def fake_push(url, token, points, *, on_progress=None):
        assert on_progress is not None
        await on_progress(1)
        first_point_sent.set()
        await finish_push.wait()
        await on_progress(2)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    j = await manager.start_journey(make_itinerary())
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False))

    await manager.board(None)
    await manager.alight()
    await first_point_sent.wait()

    assert j.state == JourneyState.PUSHING
    assert manager.snapshot()["transfer"] == {
        "sent_points": 1,
        "total_points": 4,
        "remaining_points": 3,
        "progress_percent": 25,
    }

    finish_push.set()
    await manager._push_task

    assert j.state == JourneyState.COMPLETED
    assert manager.snapshot()["transfer"] == {
        "sent_points": 4,
        "total_points": 4,
        "remaining_points": 0,
        "progress_percent": 100,
    }


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
        "remaining_points": 2,
        "progress_percent": 33,
        "can_retry": True,
    }


async def test_in_progress_push_resumes_from_last_confirmed_point_after_restart(manager, monkeypatch):
    j = await manager.start_journey(make_itinerary())
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4837, lon=127.0354, ts=1, estimated=False))
    manager.db.add_point(j.id, 0, TrackPoint(lat=37.4909, lon=127.0553, ts=2, estimated=False))
    manager.db.update_journey(
        j.id,
        state=JourneyState.PUSHING,
        transfer_sent_points=1,
        transfer_total_points=2,
    )
    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        assert on_progress is not None
        await on_progress(1)
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)
    resumed = JourneyManager(manager.db, manager.settings)
    resumed.resume_from_db()

    assert resumed.active is not None
    assert resumed.active.state == JourneyState.PUSHING
    assert resumed._push_task is not None
    await resumed._push_task

    assert [point.ts for point in pushed] == [2]
    snapshot = resumed.snapshot()
    assert snapshot is not None
    assert snapshot["transfer"] == {
        "sent_points": 2,
        "total_points": 2,
        "remaining_points": 0,
        "progress_percent": 100,
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
        LegTrainStatus(leg_index=0, status="departed", station_name="양재"),
        LegTrainStatus(leg_index=1, status="arrived", station_name="매봉"),  # arrival -> log segment
        LegTrainStatus(leg_index=2, status="arrived", station_name="도곡"),
    ]
    calls = iter(feed)

    async def fake_locate_train(base_url, leg, train_no):
        return next(calls, feed[-1])

    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)
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
        if j.state in (JourneyState.COMPLETED, JourneyState.PUSH_FAILED):
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

    async def fake_locate_train(base_url, leg, train_no):
        return LegTrainStatus(leg_index=None, status="arrived", station_name="개포동")

    class FakeTime:
        @staticmethod
        def time():
            return 1_000_040.0

    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)
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

    async def fake_push(url, token, points, *, on_progress=None):
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

    for _ in range(10):
        await asyncio.sleep(0)
        if j.state == JourneyState.COMPLETED:
            break
    assert j.state == JourneyState.COMPLETED
    logged = {(round(p.lat, 4), round(p.lon, 4)) for p in pushed}
    assert (37.4767, 126.9813) in logged


async def test_log_transfer_walk_is_idempotent_across_retries(manager):
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
        summary=["🚇 수도권2호선: 강남 → 사당", "🚶 도보 2분", "🚇 수도권4호선: 사당 → 서울역"],
    )
    j = await manager.start_journey(itinerary)
    manager._emit_at(j, first.stations[-1].lat, first.stations[-1].lon, 1_000_000, estimated=False)

    # simulate a crashed/retried board() call re-running the same walk log
    manager._log_transfer_walk(j, 0, 1_000_100)
    manager._log_transfer_walk(j, 0, 1_000_200)

    points = manager.db.get_points(j.id, leg_idx=0)
    assert len(points) == len(first.transfer_walk_shape)


async def test_missed_train_returns_to_picker(manager, monkeypatch):
    async def fake_locate_train(base_url, leg, train_no):
        return None

    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)

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


async def test_realtime_tick_skips_subway_feed_fetch_until_next_poll_deadline(manager, monkeypatch):
    manager.settings.poll_interval_seconds = 5
    calls = []

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    async def fake_locate_train(base_url, leg, train_no):
        calls.append(FakeTime._t)
        return LegTrainStatus(leg_index=0, status="departed", station_name="양재")

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)

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

    async def fake_locate_train(base_url, leg, train_no):
        return None

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)

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


async def test_onboard_departure_backfills_estimated_history_and_station_dwell(manager, monkeypatch):
    shape = [
        [37.4837, 127.0354],
        [37.4850, 127.0400],
        [37.4870, 127.0468],
        [37.4885, 127.0500],
        [37.4909, 127.0553],
    ]
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary(shape=shape))

    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3001", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name="매봉", station_index=1,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )

    points = manager.db.get_points(journey.id)
    assert [point.ts for point in points] == sorted(point.ts for point in points)
    assert all(point.estimated for point in points[:-1])
    assert points[-1] == TrackPoint(lat=37.4870, lon=127.0468, ts=1_000_000, estimated=False)
    # The 120-second station budget gives a 30-second dwell at 매봉: its
    # estimated arrival and the later live departure share the station coords.
    assert TrackPoint(lat=37.4870, lon=127.0468, ts=999_970, estimated=True) in points
    assert any(
        point.lat == pytest.approx(37.4850) and point.lon == pytest.approx(127.0400) and point.estimated
        for point in points
    )
    assert journey.logged_idx == 1
    assert journey.last_arrival_time == 999_970
    assert journey.anchor_idx == 1
    assert journey.anchor_phase == "segment"
    assert manager.snapshot()["history_estimated"] is True


@pytest.mark.parametrize(
    ("status", "station_index", "station_name", "expected_phase", "expected_anchor_idx"),
    [
        ("arrived", 1, "매봉", "station", 1),
        ("between", 2, "도곡", "segment", 1),
    ],
)
async def test_onboard_station_and_between_anchors_continue_from_live_position(
    manager, monkeypatch, status, station_index, station_name, expected_phase, expected_anchor_idx,
):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())

    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3001", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name=station_name, station_index=station_index,
            status=status, observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )

    live = manager.db.get_points(journey.id)[-1]
    assert live.ts == 1_000_000
    assert live.estimated is False
    assert journey.anchor_idx == expected_anchor_idx
    assert journey.anchor_phase == expected_phase
    assert journey.state == JourneyState.ON_TRAIN
    assert journey.last_status is not None
    assert journey.last_status.status == status
    if status == "between":
        assert journey.logged_idx == 1
        assert (live.lat, live.lon) != (journey.leg.stations[1].lat, journey.leg.stations[1].lon)
        assert (live.lat, live.lon) != (journey.leg.stations[2].lat, journey.leg.stations[2].lon)
    else:
        assert journey.logged_idx == 1


async def test_history_estimated_persists_and_ordinary_board_remains_false(manager, monkeypatch):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    ordinary = await manager.start_journey(make_itinerary())
    await manager.board("3001")
    assert manager.db.get_journey(ordinary.id)["history_estimated"] == 0
    assert manager.snapshot()["history_estimated"] is False

    estimated = await manager.start_journey(make_itinerary())
    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3002", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name="매봉", station_index=1,
            status="arrived", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )
    resumed = JourneyManager(manager.db, manager.settings)
    monkeypatch.setattr(resumed, "_start_tracker", lambda: None)
    resumed.resume_from_db()

    assert manager.db.get_journey(estimated.id)["history_estimated"] == 1
    assert resumed.active is not None
    assert resumed.snapshot()["history_estimated"] is True


async def test_onboard_origin_departure_starts_from_live_anchor_without_backfilled_segment(
    manager, monkeypatch,
):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())

    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3001", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name="양재", station_index=0,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )

    points = manager.db.get_points(journey.id)
    assert points == [
        TrackPoint(lat=37.4837, lon=127.0354, ts=999_970, estimated=True),
        TrackPoint(lat=37.4837, lon=127.0354, ts=1_000_000, estimated=False),
    ]
    assert journey.state == JourneyState.ON_TRAIN
    assert journey.history_estimated is True
    assert manager.snapshot()["history_estimated"] is True
    assert journey.logged_idx == 0
    assert journey.anchor_idx == 0
    assert journey.anchor_phase == "segment"
    assert journey.last_status is not None
    assert journey.last_status.status == "departed"


async def test_resumed_onboard_history_keeps_live_anchor_and_does_not_replay_backfill(
    manager, monkeypatch,
):
    shape = [
        [37.4837, 127.0354],
        [37.4850, 127.0400],
        [37.4870, 127.0468],
        [37.4885, 127.0500],
        [37.4909, 127.0553],
    ]

    class FakeTime:
        _t = 1_000_000.0

        @staticmethod
        def time():
            return FakeTime._t

    async def fake_locate_train(base_url, leg, train_no):
        return LegTrainStatus(leg_index=1, status="departed", station_name="매봉")

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary(shape=shape))
    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3001", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name="매봉", station_index=1,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )
    reconstructed = manager.db.get_points(journey.id)

    resumed = JourneyManager(manager.db, manager.settings)
    monkeypatch.setattr(resumed, "_start_tracker", lambda: None)
    monkeypatch.setattr(resumed, "_start_push", lambda *args, **kwargs: None)
    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)
    resumed.resume_from_db()
    assert resumed.active is not None

    FakeTime._t = 1_000_005.0
    await resumed._realtime_update(resumed.active)

    # The same live departure may be the first update after restart, but it
    # must not replay the estimated origin-to-매봉 geometry or overwrite anchor.
    assert manager.db.get_points(journey.id) == reconstructed
    assert resumed.active.logged_idx == 1
    assert resumed.active.anchor_idx == 1
    assert resumed.active.anchor_phase == "segment"

    async def at_end(base_url, leg, train_no):
        return LegTrainStatus(leg_index=2, status="arrived", station_name="도곡")

    monkeypatch.setattr(journey_mod, "locate_train", at_end)
    FakeTime._t = 1_000_120.0
    await resumed._realtime_update(resumed.active)

    continued = manager.db.get_points(journey.id)
    assert [point.ts for point in continued] == sorted(point.ts for point in continued)
    assert (37.4850, 127.0400) not in {
        (round(point.lat, 4), round(point.lon, 4)) for point in continued[len(reconstructed):]
    }
    assert (37.4885, 127.0500) in {
        (round(point.lat, 4), round(point.lon, 4)) for point in continued
    }
    assert continued[-1] == TrackPoint(lat=37.4909, lon=127.0553, ts=1_000_120, estimated=False)


def _transfer_itinerary() -> Itinerary:
    """Timer-mode 2호선 leg, a walk, then a covered 4호선 사당 → 충무로 → 서울역 leg."""
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
        route="수도권4호선", line_key="4호선", section_time=240,
        start_name="사당", end_name="서울역",
        stations=[
            LegStation(index=0, name="사당", lat=37.4768, lon=126.9817),
            LegStation(index=1, name="충무로", lat=37.5614, lon=126.9940),
            LegStation(index=2, name="서울역", lat=37.5547, lon=126.9706),
        ],
        shape=[
            [37.4768, 126.9817], [37.5200, 126.9880], [37.5614, 126.9940],
            [37.5580, 126.9820], [37.5547, 126.9706],
        ],
    )
    return Itinerary(
        total_time=480, transfer_count=1, total_walk_time=120, fare=1600,
        legs=[first, second], summary=["2호선", "도보", "4호선"],
    )


async def test_onboard_later_leg_retains_transfer_walk_before_reconstructed_history(manager, monkeypatch):
    itinerary = _transfer_itinerary()

    class FakeTime:
        @staticmethod
        def time():
            return 999_700.0

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(itinerary)
    await manager.board(None)
    await manager.alight()
    assert journey.leg_idx == 1
    assert journey.state == JourneyState.AWAITING_BOARD
    # The real transfer clock can be later than the schedule-derived origin of
    # an already-progressed train.  It must not put walk geometry after that
    # reconstructed ride history.
    journey.transfer_started_at = 999_900.0

    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="4001", line_name="4호선", terminus="서울역",
            direction_label="서울역 방면", station_name="충무로", station_index=1,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )

    points = manager.db.get_points(journey.id)
    transfer_midpoint = next(
        point for point in points
        if point.lat == pytest.approx(37.4767) and point.lon == pytest.approx(126.9813)
    )
    assert transfer_midpoint.estimated is True
    assert 999_700 < transfer_midpoint.ts < 999_850
    assert [point.ts for point in points] == sorted(point.ts for point in points)
    # The walk ends no later than the reconstructed origin at 999850, then the
    # later-leg history reaches the live non-estimated onboard anchor at 1m.
    assert points[-1] == TrackPoint(
        lat=37.5614, lon=126.9940, ts=1_000_000, estimated=False,
    )


def _line2_itinerary() -> Itinerary:
    names = ["성수", "건대입구", "구의", "강변"]
    stations = [LegStation(index=i, name=n, lat=37.54 + i * 0.001, lon=127.05 + i * 0.01) for i, n in enumerate(names)]
    leg = SubwayLeg(
        route="수도권2호선", line_key="2호선", section_time=360,
        start_name="성수", end_name="강변", stations=stations, shape=[],
    )
    return Itinerary(
        total_time=360, transfer_count=0, total_walk_time=0, fare=1400,
        legs=[leg], summary=["🚇 수도권2호선: 성수 → 강변"],
    )


@pytest.mark.parametrize(
    ("itinerary", "line", "terminus", "station_index", "station_name"),
    [
        # Line 3: the train runs past the rider's stop to its own terminus.
        (make_itinerary, "3호선", "오금", 0, "양재"),
        # Line 2 loop: 성수-bound trains pass 성수 and come back a lap later.
        (_line2_itinerary, "2호선", "성수", 1, "건대입구"),
        (_line2_itinerary, "2호선", "외선순환", 0, "성수"),
    ],
)
async def test_onboard_tracking_accepts_train_whose_terminus_is_beyond_or_behind_the_leg(
    manager, monkeypatch, itinerary, line, terminus, station_index, station_name,
):
    """Direction comes from the feed's up/dn list, not from the train's terminus."""
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(itinerary())

    await manager.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="2001", line_name=line, terminus=terminus,
            direction_label=f"{terminus}행", station_name=station_name, station_index=station_index,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )

    assert journey.state == JourneyState.ON_TRAIN


@pytest.mark.parametrize(
    ("status", "station_index", "station_name"),
    [
        ("arrived", 0, "양재"),
        ("approaching", 0, "양재"),
        ("departed", 2, "도곡"),
        ("arrived", 2, "도곡"),
        ("approaching", 2, "도곡"),
    ],
)
async def test_onboard_tracking_rejects_origin_arrival_and_at_end_candidates_without_transition(
    manager, monkeypatch, status, station_index, station_name,
):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())

    with pytest.raises(ValueError, match="outside the active leg"):
        await manager.begin_realtime_tracking_from_onboard(
            OnboardTrain(
                train_no="3001", line_name="3호선", terminus="도곡",
                direction_label="도곡 방면", station_name=station_name, station_index=station_index,
                status=status, observed_at=1_000_000, matches_direction=True,
                is_express=False,
            )
        )

    assert journey.state == JourneyState.AWAITING_BOARD
    assert manager.db.get_journey(journey.id)["history_estimated"] == 0


async def test_stop_and_send_pushes_points_tracked_so_far_mid_leg(manager, monkeypatch):
    feed = [
        LegTrainStatus(leg_index=0, status="arrived", station_name="양재"),
        LegTrainStatus(leg_index=0, status="departed", station_name="양재"),
    ]
    calls = iter(feed)

    async def fake_locate_train(base_url, leg, train_no):
        return next(calls, feed[-1])

    pushed = []

    async def fake_push(url, token, points, *, on_progress=None):
        pushed.extend(points)
        return len(points)

    monkeypatch.setattr(journey_mod, "locate_train", fake_locate_train)
    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    j = await manager.start_journey(make_itinerary())
    await manager.board("3001")
    for _ in range(10):
        await asyncio.sleep(0.05)
        if j.anchor_phase == "segment":
            break
    assert j.anchor_phase == "segment"  # departed 양재, running toward 매봉

    await manager.stop_and_send()

    assert j.state == JourneyState.PUSHING
    await manager._push_task

    assert j.state == JourneyState.COMPLETED
    assert pushed, "points tracked before stopping must be pushed to Reitti"
    assert pushed[-1].lat != pytest.approx(37.4909)  # never reached 도곡
    assert pushed[-1].estimated is True


async def test_stop_and_send_uses_timer_progress_for_uncovered_lines(manager, monkeypatch):
    async def fake_push(url, token, points, *, on_progress=None):
        return len(points)

    monkeypatch.setattr(journey_mod, "push_points", fake_push)

    itinerary = make_itinerary()
    itinerary.legs[0].line_key = None  # timer mode
    j = await manager.start_journey(itinerary)
    await manager.board(None)
    j.leg_started_at -= 120  # halfway through the 240s leg

    await manager.stop_and_send()

    assert j.state == JourneyState.PUSHING
    await manager._push_task
    assert j.state == JourneyState.COMPLETED


async def test_stop_and_send_requires_an_active_ride(manager):
    await manager.start_journey(make_itinerary())

    with pytest.raises(ValueError):
        await manager.stop_and_send()


def test_one_stop_remaining_uses_only_authoritative_mode_status_and_indexes():
    leg = make_itinerary().legs[0]
    penultimate = len(leg.stations) - 2
    destination = len(leg.stations) - 1

    def status(kind: str, station_index: int | None) -> TrainStatus:
        return TrainStatus(
            train_no="3001", station_name="ignored", station_index=station_index,
            status=kind, lat=0, lon=0, updated_at=1,
        )

    assert is_one_stop_remaining(leg, status("departed", penultimate))
    assert is_one_stop_remaining(leg, status("between", destination))
    assert is_one_stop_remaining(leg, status("estimated", penultimate))
    assert not is_one_stop_remaining(leg, status("departed", 0))
    assert not is_one_stop_remaining(leg, status("between", penultimate))
    assert not is_one_stop_remaining(leg, status("arrived", penultimate))
    assert not is_one_stop_remaining(leg, status("departed", None))
    assert not is_one_stop_remaining(leg, None)

    leg.mode = "BUS"
    assert not is_one_stop_remaining(leg, status("departed", penultimate))
    leg.mode = "SUBWAY"
    leg.stations = leg.stations[:1]
    assert not is_one_stop_remaining(leg, status("departed", 0))


async def test_realtime_claims_before_nonblocking_dispatch_and_deduplicates(manager, monkeypatch):
    gate = asyncio.Event()
    sender = FakeNotificationSender(gate)
    tracked = JourneyManager(manager.db, manager.settings, notification_sender=sender)
    tracked.db.upsert_push_subscription("https://push.example/one", "key", "auth")
    monkeypatch.setattr(tracked, "_start_tracker", lambda: None)

    async def at_penultimate(base_url, leg, train_no):
        return LegTrainStatus(leg_index=1, status="departed", station_name="매봉")

    monkeypatch.setattr(journey_mod, "locate_train", at_penultimate)
    journey = await tracked.start_journey(make_itinerary())
    await tracked.board("3001")

    await tracked._realtime_update(journey)
    assert tracked.db.conn.execute(
        "SELECT COUNT(*) FROM journey_leg_notifications WHERE journey_id = ? AND leg_idx = 0",
        (journey.id,),
    ).fetchone()[0] == 1
    assert len(tracked._notification_tasks) == 1

    # The sender waits forever until released, proving the tracker did not await
    # Web Push work before returning from the realtime update.
    await asyncio.sleep(0)
    assert sender.calls == [(journey.id, 0, "도곡")]
    await tracked._realtime_update(journey)
    assert len(tracked._notification_tasks) == 1
    assert sender.calls == [(journey.id, 0, "도곡")]

    task = next(iter(tracked._notification_tasks))
    gate.set()
    await task
    assert not tracked._notification_tasks


async def test_local_timer_and_onboard_paths_claim_their_authoritative_status(manager, monkeypatch):
    sender = FakeNotificationSender()
    tracked = JourneyManager(manager.db, manager.settings, notification_sender=sender)
    tracked.db.upsert_push_subscription("https://push.example/one", "key", "auth")
    monkeypatch.setattr(tracked, "_start_tracker", lambda: None)

    local = await tracked.start_journey(make_itinerary())
    await tracked.board("3001")
    local.anchor_idx = 1
    local.anchor_phase = "segment"
    local.anchor_time = 0
    local.last_status = TrainStatus(
        train_no="3001", station_name="매봉", station_index=1, status="departed",
        lat=local.leg.stations[1].lat, lon=local.leg.stations[1].lon, updated_at=0,
    )
    tracked._local_realtime_update(local, now=1)

    timer = await tracked.start_journey(make_itinerary())
    await tracked.board(None)
    assert timer.leg_started_at is not None
    timer.leg_started_at -= 180
    await tracked._timer_update(timer)

    onboard = await tracked.start_journey(make_itinerary())
    await tracked.begin_realtime_tracking_from_onboard(
        OnboardTrain(
            train_no="3001", line_name="3호선", terminus="도곡",
            direction_label="도곡 방면", station_name="매봉", station_index=1,
            status="departed", observed_at=1_000_000, matches_direction=True,
            is_express=False,
        )
    )
    await asyncio.sleep(0)

    assert sender.calls == [
        (local.id, 0, "도곡"),
        (timer.id, 0, "도곡"),
        (onboard.id, 0, "도곡"),
    ]
    await tracked.shutdown()


async def test_two_stop_and_transfer_legs_claim_separately_but_non_subway_never_claims(manager, monkeypatch):
    sender = FakeNotificationSender()
    tracked = JourneyManager(manager.db, manager.settings, notification_sender=sender)
    tracked.db.upsert_push_subscription("https://push.example/one", "key", "auth")
    monkeypatch.setattr(tracked, "_start_tracker", lambda: None)
    two_stop = make_itinerary()
    two_stop.legs[0].stations = two_stop.legs[0].stations[:2]
    two_stop.legs[0].end_name = "매봉"
    journey = await tracked.start_journey(two_stop)
    await tracked.board("3001")

    async def at_origin_departure(base_url, leg, train_no):
        return LegTrainStatus(leg_index=0, status="departed", station_name="양재")

    monkeypatch.setattr(journey_mod, "locate_train", at_origin_departure)
    await tracked._realtime_update(journey)

    first, second = make_itinerary().legs[0], make_itinerary().legs[0].model_copy(deep=True)
    second.start_name, second.end_name = "도곡", "서울역"
    second.stations = [
        LegStation(index=0, name="도곡", lat=37.4909, lon=127.0553),
        LegStation(index=1, name="서울역", lat=37.5535, lon=126.9728),
    ]
    transfer = await tracked.start_journey(Itinerary(
        total_time=480, transfer_count=1, total_walk_time=0, fare=1400,
        legs=[first, second], summary=["first", "second"],
    ))
    await tracked.board(None)
    assert transfer.leg_started_at is not None
    transfer.leg_started_at -= 180
    await tracked._timer_update(transfer)
    await tracked.alight()
    await tracked.board(None)
    assert transfer.leg_started_at is not None
    transfer.leg_started_at -= 180
    await tracked._timer_update(transfer)

    excluded = await tracked.start_journey(make_itinerary())
    excluded.leg.mode = "TRAIN"
    await tracked.board(None)
    assert excluded.leg_started_at is not None
    excluded.leg_started_at -= 180
    await tracked._timer_update(excluded)
    await asyncio.sleep(0)

    claimed = tracked.db.conn.execute(
        "SELECT journey_id, leg_idx FROM journey_leg_notifications ORDER BY journey_id, leg_idx"
    ).fetchall()
    assert [tuple(row) for row in claimed] == [
        (journey.id, 0), (transfer.id, 0), (transfer.id, 1),
    ]
    assert all(call[0] != excluded.id for call in sender.calls)
    await tracked.shutdown()


async def test_restart_resumes_only_existing_pending_claim_and_terminal_state_cannot_claim(manager, monkeypatch):
    gate = asyncio.Event()
    first_sender = FakeNotificationSender(gate)
    tracked = JourneyManager(manager.db, manager.settings, notification_sender=first_sender)
    tracked.db.upsert_push_subscription("https://push.example/one", "key", "auth")
    monkeypatch.setattr(tracked, "_start_tracker", lambda: None)
    journey = await tracked.start_journey(make_itinerary())
    await tracked.board("3001")
    journey.last_status = TrainStatus(
        train_no="3001", station_name="매봉", station_index=1, status="departed",
        lat=journey.leg.stations[1].lat, lon=journey.leg.stations[1].lon, updated_at=1,
    )
    tracked._claim_one_stop_notification(journey)
    await asyncio.sleep(0)
    await tracked.shutdown()

    resumed_sender = FakeNotificationSender(gate)
    resumed = JourneyManager(manager.db, manager.settings, notification_sender=resumed_sender)
    monkeypatch.setattr(resumed, "_start_tracker", lambda: None)
    resumed.resume_from_db()
    await asyncio.sleep(0)
    assert resumed_sender.calls == [(journey.id, 0, "도곡")]
    assert resumed.db.conn.execute(
        "SELECT COUNT(*) FROM journey_leg_notifications WHERE journey_id = ?",
        (journey.id,),
    ).fetchone()[0] == 1

    assert resumed.active is not None
    for terminal_state in (JourneyState.CANCELLED, JourneyState.PUSHING, JourneyState.COMPLETED):
        resumed.active.state = terminal_state
        resumed._claim_one_stop_notification(resumed.active)
    assert resumed.db.conn.execute(
        "SELECT COUNT(*) FROM journey_leg_notifications WHERE journey_id = ?",
        (journey.id,),
    ).fetchone()[0] == 1
    gate.set()
    await resumed.shutdown()


def _onboard(station_name: str, station_index: int, status: str, observed_at: int, train_no: str = "3001",
             line: str = "3호선", terminus: str = "도곡") -> OnboardTrain:
    return OnboardTrain(
        train_no=train_no, line_name=line, terminus=terminus, direction_label=f"{terminus} 방면",
        station_name=station_name, station_index=station_index, status=status,
        observed_at=observed_at, matches_direction=True, is_express=False,
    )


YANGJAE = (37.4837, 127.0354)
MAEBONG = (37.4870, 127.0468)


async def test_onboard_uses_platform_snapshots_for_origin_arrival_and_departure(manager, monkeypatch):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    journey.platform_snapshots.extend([
        (1000, _line3_snap("양재", "접근")), (1015, _line3_snap("양재", "도착")),
        (1030, _line3_snap("양재", "도착")), (1045, _line3_snap("양재", "출발")),
        (1060, _line3_snap("매봉", "접근")), (1075, _line3_snap("매봉", "도착")),
    ])

    await manager.begin_realtime_tracking_from_onboard(_onboard("매봉", 1, "arrived", 1075))

    points = manager.db.get_points(journey.id)
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=1007, estimated=True) in points
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=1037, estimated=True) in points
    assert points[-1] == TrackPoint(lat=MAEBONG[0], lon=MAEBONG[1], ts=1075, estimated=False)
    assert all(point.estimated for point in points[:-1])
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 1007
    assert journey.anchor_phase == "station"
    assert len(journey.platform_snapshots) == 0


async def test_onboard_partial_snapshots_shift_schedule_for_unknown_origin_arrival(manager, monkeypatch):
    # Recording began with the train already 도착 at 양재: its arrival is unknown,
    # its departure brackets to 1007.5. Schedule (budget 120, dwell 30) from a
    # departure observed at 매봉 at 1060 puts 양재 at 910/940, so the unknown
    # arrival shifts by the same 67.5 s: 977.5.
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    journey.platform_snapshots.extend([
        (1000, _line3_snap("양재", "도착")), (1015, _line3_snap("양재", "출발")),
        (1030, _line3_snap("매봉", "접근")), (1045, _line3_snap("매봉", "도착")),
        (1060, _line3_snap("매봉", "출발")),
    ])

    await manager.begin_realtime_tracking_from_onboard(_onboard("매봉", 1, "departed", 1060))

    points = manager.db.get_points(journey.id)
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=977, estimated=True) in points
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=1007, estimated=True) in points
    # the current station keeps today's timing: arrival = observed_at - dwell
    assert TrackPoint(lat=MAEBONG[0], lon=MAEBONG[1], ts=1030, estimated=True) in points
    assert points[-1] == TrackPoint(lat=MAEBONG[0], lon=MAEBONG[1], ts=1060, estimated=False)
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 977
    assert journey.anchor_phase == "segment"


async def test_onboard_ignores_snapshot_bracket_wider_than_limit(manager, monkeypatch):
    # 접근 → 도착 took 100 s between snapshots (> 45 s): arrival unknown;
    # departure brackets to 1105, schedule 1080/1110 → arrival 1075.
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    journey.platform_snapshots.extend([
        (1000, _line3_snap("양재", "접근")), (1100, _line3_snap("양재", "도착")),
        (1110, _line3_snap("양재", "출발")),
    ])

    await manager.begin_realtime_tracking_from_onboard(_onboard("매봉", 1, "arrived", 1200))

    points = manager.db.get_points(journey.id)
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=1075, estimated=True) in points
    assert TrackPoint(lat=YANGJAE[0], lon=YANGJAE[1], ts=1105, estimated=True) in points


def _line4_snap(train_at: str, status: str) -> list[StationSnapshot]:
    return [
        StationSnapshot(
            name=name, up=[],
            dn=[TrainEntry(status=status, kind="일반", dest="서울역", no="4001")] if name == train_at else [],
        )
        for name in ("사당", "충무로", "서울역")
    ]


async def test_onboard_transfer_walk_ends_at_inferred_origin_arrival(manager, monkeypatch):
    class FakeTime:
        @staticmethod
        def time():
            return 900.0

    monkeypatch.setattr(journey_mod, "time", FakeTime)
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(_transfer_itinerary())
    await manager.board(None)
    await manager.alight()
    assert journey.leg_idx == 1
    journey.platform_snapshots.extend([
        (1000, _line4_snap("사당", "접근")), (1010, _line4_snap("사당", "도착")),
        (1030, _line4_snap("사당", "도착")), (1040, _line4_snap("사당", "출발")),
    ])

    # Schedule alone would put the 사당 arrival (and the walk's end) at 1080.
    await manager.begin_realtime_tracking_from_onboard(
        _onboard("충무로", 1, "arrived", 1200, train_no="4001", line="4호선", terminus="서울역"),
    )

    # The walk (leg 0) ends at the inferred origin arrival; leg-0 ride points are all at 900.
    assert max(point.ts for point in manager.db.get_points(journey.id, leg_idx=0)) == 1005
    assert manager.db.get_journey(journey.id)["leg_started_at"] == 1005


async def test_resumed_snapshot_inferred_arrival_keeps_station_anchor(manager, monkeypatch):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    journey.platform_snapshots.extend([
        (1000, _line3_snap("양재", "접근")), (1015, _line3_snap("양재", "도착")),
        (1030, _line3_snap("양재", "출발")), (1045, _line3_snap("매봉", "접근")),
        (1060, _line3_snap("매봉", "도착")),
    ])
    await manager.begin_realtime_tracking_from_onboard(_onboard("매봉", 1, "arrived", 1060))

    resumed = JourneyManager(manager.db, manager.settings)
    monkeypatch.setattr(resumed, "_start_tracker", lambda: None)
    resumed.resume_from_db()

    assert resumed.active.anchor_idx == 1
    assert resumed.active.anchor_phase == "station"


async def test_onboard_inference_logs_inferred_and_schedule_times(manager, monkeypatch, caplog):
    monkeypatch.setattr(manager, "_start_tracker", lambda: None)
    journey = await manager.start_journey(make_itinerary())
    # recording began after the train reached 양재: arrival falls back, departure is observed
    journey.platform_snapshots.extend([
        (1030, _line3_snap("양재", "도착")), (1045, _line3_snap("양재", "출발")),
    ])

    with caplog.at_level("INFO", logger="app.journey"):
        await manager.begin_realtime_tracking_from_onboard(_onboard("매봉", 1, "arrived", 1075))

    # epoch 955 = 09:15:55 KST (schedule origin), 1037.5 → 09:17:17, 1007.5 → 09:16:47
    line = next(r.getMessage() for r in caplog.records if "onboard inference" in r.getMessage())
    assert "train=3001" in line
    assert "양재 arrival 09:16:47 (shifted; schedule 09:15:55)" in line
    assert "양재 departure 09:17:17 (snapshot; schedule 09:16:25)" in line
