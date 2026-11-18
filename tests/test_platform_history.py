from app.models import LegStation, SubwayLeg
from app.platform_history import infer_station_events, merge_event_times, train_progress
from app.subway_feed import StationSnapshot, TrainEntry


def _entries(raw: list[tuple[str, str, str]]) -> list[TrainEntry]:
    return [TrainEntry(status=status, kind="일반", dest=dest, no=no) for status, dest, no in raw]


def _snap(*stations: tuple[str, list, list]) -> list[StationSnapshot]:
    """Each station is (name, up, dn); each train is (status, dest, train_no)."""
    return [StationSnapshot(name=name, up=_entries(up), dn=_entries(dn)) for name, up, dn in stations]


def _leg(line_key: str, *names: str) -> SubwayLeg:
    return SubwayLeg(
        route=f"수도권{line_key}",
        line_key=line_key,
        section_time=60 * (len(names) - 1),
        start_name=names[0],
        end_name=names[-1],
        stations=[LegStation(index=i, name=n, lat=0.0, lon=0.0) for i, n in enumerate(names)],
    )


def at_in(names: tuple[str, ...], train_at: str, status: str) -> list[StationSnapshot]:
    return _snap(*((n, [], [(status, names[-1], "t")] if n == train_at else []) for n in names))


def at(train_at: str, status: str) -> list[StationSnapshot]:
    return at_in(("O", "B", "C"), train_at, status)


def empty() -> list[StationSnapshot]:
    return _snap(("O", [], []), ("B", [], []), ("C", [], []))


LEG = _leg("3호선", "O", "B", "C")


def test_train_progress_counts_status_steps_from_origin():
    snap = _snap(("O", [], []), ("B", [], [("도착", "C", "t")]), ("C", [], []))

    assert train_progress(snap, LEG, "t") == 4


def test_train_progress_is_negative_before_origin():
    snap = _snap(("A", [], [("출발", "C", "t")]), ("O", [], []), ("B", [], []), ("C", [], []))

    assert train_progress(snap, LEG, "t") == -1


def test_train_progress_line2_prefers_shorter_direction():
    # 43-name main loop S0..S42; leg S5 → S6 → S7 runs in snapshot +1, which is
    # Line 2's `up` bucket. A train one stop behind the origin is offset -1,
    # not the ~42-hop forward route around the loop: 3 * -1 + 2 == -1.
    names = [f"S{i}" for i in range(43)]
    snap = _snap(*((n, [("출발", "S7", "t")] if n == "S4" else [], []) for n in names))

    assert train_progress(snap, _leg("2호선", "S5", "S6", "S7"), "t") == -1


def test_train_progress_terminus_turnback_counts_as_origin_arrival():
    snap = _snap(("O", [("도착", "O", "t")], []), ("B", [], []))

    assert train_progress(snap, _leg("3호선", "O", "B"), "t") == 1


def test_train_progress_ignores_opposite_direction_elsewhere():
    snap = _snap(("O", [], []), ("B", [("도착", "Z", "t")], []), ("C", [], []))

    assert train_progress(snap, LEG, "t") is None


def test_infer_station_events_uses_bracket_midpoints():
    history = [
        (1000, at("O", "접근")), (1015, at("O", "도착")), (1030, at("O", "도착")),
        (1045, at("O", "출발")), (1060, at("B", "접근")), (1075, at("B", "도착")),
    ]

    events = infer_station_events(history, LEG, "t")

    assert events[0] == (1007.5, 1037.5)
    assert events[1] == (1067.5, None)
    assert events[2] == (None, None)


def test_infer_station_events_unknown_without_earlier_sample():
    history = [(1000, at("O", "도착")), (1015, at("O", "출발"))]

    assert infer_station_events(history, LEG, "t")[0] == (None, 1007.5)


def test_infer_station_events_unknown_when_bracket_too_wide():
    history = [(1000, at("O", "접근")), (1100, at("O", "도착")), (1110, at("O", "출발"))]

    assert infer_station_events(history, LEG, "t")[0] == (None, 1105.0)


def test_infer_station_events_skips_snapshots_without_train():
    history = [(1000, at("O", "접근")), (1005, empty()), (1010, at("O", "도착"))]

    assert infer_station_events(history, LEG, "t")[0][0] == 1005.0


def test_infer_station_events_uses_latest_run_when_progress_resets():
    # 900: the same train number on an earlier pass, already at C.
    history = [(900, at("C", "도착")), (1000, at("O", "접근")), (1010, at("O", "도착"))]

    assert infer_station_events(history, LEG, "t")[0][0] == 1005.0


def test_infer_station_events_unknown_for_express_leg_with_skipped_stops():
    names = ("O", "skip", "B")  # leg O → B passes a physical station it doesn't stop at
    history = [(1000, at_in(names, "O", "접근")), (1010, at_in(names, "O", "도착"))]

    assert infer_station_events(history, _leg("3호선", "O", "B"), "t") == [(None, None), (None, None)]


def test_merge_event_times_shifts_unknown_by_nearest_known():
    assert merge_event_times([910, 940], [None, 1007.5], upper=1030) == [977.5, 1007.5]


def test_merge_event_times_returns_schedule_when_nothing_known():
    assert merge_event_times([910, 940], [None, None], upper=1030) == [910, 940]


def test_merge_event_times_is_strictly_increasing_below_upper():
    times = merge_event_times([10, 20, 30], [50.0, 40.0, None], upper=45)

    assert all(b - a >= 1 for a, b in zip(times, times[1:]))
    assert times[-1] <= 44
