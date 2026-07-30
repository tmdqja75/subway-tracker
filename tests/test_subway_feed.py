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
