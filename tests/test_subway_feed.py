import httpx
import pytest
import respx

from app.subway_feed import SubwayApiError, TrainEntry, _stations_between, _step, fetch_line_snapshot


@respx.mock
async def test_fetch_line_snapshot_parses_stations_and_trains():
    respx.get("http://subway.test/subway/seoul", params={"lineId": "6"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "isTimeTable": False,
                "data": [
                    {"stn": "역촌", "up": [], "dn": []},
                    {
                        "stn": "불광",
                        "up": [{"status": "출발", "type": "급행", "dest": "응암순환", "no": "6142"}],
                        "dn": [],
                    },
                ],
            },
        )
    )

    snapshot = await fetch_line_snapshot("http://subway.test", "6")

    assert [s.name for s in snapshot] == ["역촌", "불광"]
    assert snapshot[1].up == [TrainEntry(status="출발", kind="급행", dest="응암순환", no="6142")]
    assert snapshot[1].dn == []


@respx.mock
async def test_fetch_line_snapshot_retries_once_on_malformed_json():
    route = respx.get("http://subway.test/subway/seoul", params={"lineId": "1"})
    route.side_effect = [
        httpx.Response(200, content=b'{"data": [{"stn": "\x01broken'),
        httpx.Response(200, json={"isTimeTable": False, "data": [{"stn": "연천", "up": [], "dn": []}]}),
    ]

    snapshot = await fetch_line_snapshot("http://subway.test", "1")

    assert [s.name for s in snapshot] == ["연천"]
    assert route.call_count == 2


@respx.mock
async def test_fetch_line_snapshot_raises_after_two_failures():
    route = respx.get("http://subway.test/subway/seoul", params={"lineId": "1"})
    route.mock(return_value=httpx.Response(200, content=b'{"data": [{"stn": "\x01broken'))

    with pytest.raises(SubwayApiError):
        await fetch_line_snapshot("http://subway.test", "1")

    assert route.call_count == 2


def test_step_is_plain_increment_for_non_looping_lines():
    assert _step("3호선", 5, 1) == 6
    assert _step("3호선", 5, -1) == 4


def test_step_wraps_line_2_main_loop_at_both_seams():
    # Verified live: dn past index 42 (뚝섬) wraps to 0 (성수); up past 0 wraps to 42.
    assert _step("2호선", 42, 1) == 0
    assert _step("2호선", 0, -1) == 42
    # A branch station (index > 42) never wraps.
    assert _step("2호선", 47, 1) == 48


def test_step_wraps_line_6_loop_reentry():
    # Verified live: up past index 0 (역촌) re-enters at 5 (응암), continuing as dn.
    assert _step("6호선", 0, -1) == 5
    # dn direction inside the loop block is a plain increment.
    assert _step("6호선", 2, 1) == 3


def test_stations_between_counts_hops_in_direction():
    assert _stations_between("3호선", 5, 5, 1) == 0
    assert _stations_between("3호선", 5, 8, 1) == 3
    assert _stations_between("3호선", 8, 5, 1) is None  # wrong direction, unreachable


def test_stations_between_counts_through_line_2_wrap():
    # From index 40, dn-ward to index 2 must cross the 42->0 seam:
    # 40->41 (1) ->42 (2) ->0 (3, wrap) ->1 (4) ->2 (5).
    assert _stations_between("2호선", 40, 2, 1) == 5


from app.models import LegStation, SubwayLeg
from app.subway_feed import StationSnapshot, _leg_direction, _leg_station_indices


def _snap(*names: str) -> list[StationSnapshot]:
    return [StationSnapshot(name=n, up=[], dn=[]) for n in names]


def _leg(line_key: str, *names: str) -> SubwayLeg:
    return SubwayLeg(
        route=f"수도권{line_key}",
        line_key=line_key,
        section_time=60 * (len(names) - 1),
        start_name=names[0],
        end_name=names[-1],
        stations=[LegStation(index=i, name=n, lat=0.0, lon=0.0) for i, n in enumerate(names)],
    )


def test_leg_station_indices_resolves_plain_names():
    snapshot = _snap("연천", "전곡", "청량리", "구로")
    leg = _leg("1호선", "전곡", "청량리", "구로")

    assert _leg_station_indices(snapshot, leg) == [1, 2, 3]


def test_leg_station_indices_returns_none_for_unresolvable_name():
    snapshot = _snap("연천", "전곡")
    leg = _leg("1호선", "전곡", "없는역")

    assert _leg_station_indices(snapshot, leg) == [1, None]


def test_leg_station_indices_disambiguates_line_2_branch_junction():
    # 성수 appears twice: index 0 (main loop) and index 6 (지선 branch start).
    snapshot = _snap("성수", "건대입구", "뚝섬", "x", "y", "z", "성수 (지선)", "용답", "신설동")
    leg = _leg("2호선", "성수 (지선)", "용답", "신설동")

    assert _leg_station_indices(snapshot, leg) == [6, 7, 8]


def test_leg_direction_detects_dn_and_up():
    snapshot = _snap("A", "B", "C")

    assert _leg_direction("3호선", [0, 1]) == 1
    assert _leg_direction("3호선", [2, 1]) == -1


def test_leg_direction_none_when_stations_are_not_one_hop_apart():
    assert _leg_direction("3호선", [0, 2]) is None


def test_leg_direction_none_when_first_station_unresolved():
    assert _leg_direction("3호선", [None, 1]) is None


from app.subway_feed import locate_train


def _payload(*stations: tuple[str, list[dict], list[dict]]) -> dict:
    return {
        "isTimeTable": False,
        "data": [{"stn": name, "up": up, "dn": dn} for name, up, dn in stations],
    }


@respx.mock
async def test_locate_train_returns_leg_relative_index_and_status(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                ("원점역", [], []),
                ("중간역", [], [{"status": "도착", "type": "일반", "dest": "종점역", "no": "3001"}]),
                ("종점역", [], []),
            ),
        )
    )
    leg = _leg("3호선", "원점역", "중간역", "종점역")

    result = await locate_train("http://subway.test", leg, "3001")

    assert result.leg_index == 1
    assert result.status == "arrived"
    assert result.station_name == "중간역"


@respx.mock
async def test_locate_train_returns_none_when_train_not_in_feed(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(200, json=_payload(("원점역", [], []), ("종점역", [], [])))
    )
    leg = _leg("3호선", "원점역", "종점역")

    assert await locate_train("http://subway.test", leg, "no-such-train") is None


@respx.mock
async def test_locate_train_marks_leg_index_none_when_outside_leg_span(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                ("이전역", [], [{"status": "출발", "type": "일반", "dest": "종점역", "no": "3001"}]),
                ("원점역", [], []),
                ("종점역", [], []),
            ),
        )
    )
    leg = _leg("3호선", "원점역", "종점역")

    result = await locate_train("http://subway.test", leg, "3001")

    assert result.leg_index is None
    assert result.status == "departed"


@respx.mock
async def test_locate_train_marks_leg_index_none_when_train_has_run_past_the_leg(monkeypatch):
    # The train is a real, resolvable distance from boarding_idx (not
    # "unreachable" like the previous test) but that distance overshoots
    # the leg's own final station — it must not come back as an in-bounds
    # index, which would make journey.py index past the end of leg.stations.
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                ("원점역", [], []),
                ("종점역", [], []),
                ("다음역", [], [{"status": "도착", "type": "일반", "dest": "다다음역", "no": "3001"}]),
            ),
        )
    )
    leg = _leg("3호선", "원점역", "종점역")  # only 2 stations; index 2 is past it

    result = await locate_train("http://subway.test", leg, "3001")

    assert result.leg_index is None
    assert result.status == "arrived"


from app.subway_feed import fetch_arrivals


@respx.mock
async def test_fetch_arrivals_ranks_by_exact_stations_away(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                ("A", [], [{"status": "출발", "type": "일반", "dest": "D", "no": "far"}]),
                ("B", [], [{"status": "도착", "type": "급행", "dest": "D", "no": "near"}]),
                ("C", [], []),
                ("D", [], []),
            ),
        )
    )
    leg = _leg("3호선", "C", "D")

    trains = await fetch_arrivals("http://subway.test", leg)

    assert [t.train_no for t in trains] == ["near", "far"]
    assert [t.stations_away for t in trains] == [1, 2]
    assert trains[0].is_express is True
    assert trains[0].stations_away_estimated is False
    assert trains[0].eta_seconds == 0


@respx.mock
async def test_fetch_arrivals_excludes_departed_from_boarding_station_and_opposite_direction(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {"3호선": "3"})
    respx.get("http://subway.test/subway/seoul", params={"lineId": "3"}).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                ("A", [], []),
                (
                    "B",
                    [{"status": "도착", "type": "일반", "dest": "A", "no": "opposite"}],
                    [{"status": "출발", "type": "일반", "dest": "D", "no": "already-left"}],
                ),
                ("C", [], []),
            ),
        )
    )
    leg = _leg("3호선", "B", "C")

    trains = await fetch_arrivals("http://subway.test", leg)

    assert trains == []


@respx.mock
async def test_fetch_arrivals_returns_empty_for_uncovered_line(monkeypatch):
    monkeypatch.setattr("app.subway_feed.LINE_KEY_TO_API_ID", {})
    leg = _leg("정체불명선", "A", "B")

    assert await fetch_arrivals("http://subway.test", leg) == []
