import httpx
import pytest
import respx

from app.subway_feed import SubwayApiError, TrainEntry, fetch_line_snapshot


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
