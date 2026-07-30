import pytest
import respx
from httpx import Response

from app.models import LegStation, SubwayLeg
from app.seoul import (
    SeoulApiError,
    _check,
    _position_observed_at,
    fetch_arrivals,
    fetch_positions,
    find_onboard_trains,
)


def test_check_raises_for_top_level_seoul_api_error():
    data = {
        "status": 500,
        "code": "ERROR-337",
        "message": "데이터요청은 일일 호출건수 최대 1000건을 넘을 수 없습니다.",
        "total": 0,
    }

    with pytest.raises(SeoulApiError, match="ERROR-337"):
        _check(data, "fetch_arrivals station=선릉")


def test_check_allows_empty_info_200_result():
    _check(
        {
            "RESULT": {
                "code": "INFO-200",
                "message": "해당하는 데이터가 없습니다.",
            }
        },
        "fetch_arrivals station=선릉",
    )


def test_check_allows_success_payload_without_error_wrapper():
    _check({"realtimeArrivalList": []}, "fetch_arrivals station=선릉")


@respx.mock
async def test_fetch_positions_logs_actual_seoul_request_without_api_key(caplog):
    api_key = "secret-seoul-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{api_key}/json/realtimePosition/0/200/3호선"
    ).mock(return_value=Response(200, json={"realtimePositionList": []}))

    with caplog.at_level("INFO", logger="app.seoul"):
        await fetch_positions(api_key, "3호선")

    messages = [record.getMessage() for record in caplog.records]
    assert any("seoul api request endpoint=realtimePosition line=3호선" in msg for msg in messages)
    assert all(api_key not in msg for msg in messages)


@respx.mock
async def test_fetch_arrivals_logs_actual_seoul_request_without_api_key(caplog):
    api_key = "secret-seoul-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{api_key}/json/realtimeStationArrival/0/30/선릉"
    ).mock(return_value=Response(200, json={"realtimeArrivalList": []}))

    with caplog.at_level("INFO", logger="app.seoul"):
        await fetch_arrivals(api_key, "선릉", "2호선", ["역삼"])

    messages = [record.getMessage() for record in caplog.records]
    assert any("seoul api request endpoint=realtimeStationArrival station=선릉" in msg for msg in messages)
    assert all(api_key not in msg for msg in messages)


@respx.mock
async def test_fetch_positions_retries_rate_limited_key_with_fallback_key():
    primary_key = "rate-limited-key"
    fallback_key = "fallback-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{primary_key}/json/realtimePosition/0/200/3호선"
    ).mock(
        return_value=Response(
            200,
            json={
                "status": 500,
                "code": "ERROR-337",
                "message": "데이터요청은 일일 호출건수 최대 1000건을 넘을 수 없습니다.",
                "total": 0,
            },
        )
    )
    fallback_route = respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{fallback_key}/json/realtimePosition/0/200/3호선"
    ).mock(return_value=Response(200, json={"realtimePositionList": [{"trainNo": "1234"}]}))

    positions = await fetch_positions(primary_key, "3호선", fallback_api_key=fallback_key)

    assert positions == [{"trainNo": "1234"}]
    assert fallback_route.called


@respx.mock
async def test_fetch_arrivals_retries_rate_limited_key_with_fallback_key():
    primary_key = "rate-limited-key"
    fallback_key = "fallback-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{primary_key}/json/realtimeStationArrival/0/30/선릉"
    ).mock(
        return_value=Response(
            200,
            json={
                "status": 500,
                "code": "ERROR-337",
                "message": "데이터요청은 일일 호출건수 최대 1000건을 넘을 수 없습니다.",
                "total": 0,
            },
        )
    )
    fallback_route = respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{fallback_key}/json/realtimeStationArrival/0/30/선릉"
    ).mock(
        return_value=Response(
            200,
            json={
                "realtimeArrivalList": [
                    {
                        "subwayId": "1002",
                        "trainLineNm": "성수행 - 역삼방면",
                        "bstatnNm": "성수",
                        "barvlDt": "120",
                        "btrainNo": "1234",
                        "arvlMsg2": "2분 후",
                        "btrainSttus": "일반",
                    }
                ]
            },
        )
    )

    arrivals = await fetch_arrivals(primary_key, "선릉", "2호선", ["역삼"], fallback_api_key=fallback_key)

    assert [arrival.train_no for arrival in arrivals] == ["1234"]
    assert fallback_route.called


@respx.mock
async def test_fetch_arrivals_excludes_departed_and_opposite_direction_trains():
    api_key = "secret-seoul-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{api_key}/json/realtimeStationArrival/0/30/상계"
    ).mock(
        return_value=Response(
            200,
            json={
                "realtimeArrivalList": [
                    {
                        "subwayId": "1004",
                        "trainLineNm": "오이도행 - 노원방면",
                        "bstatnNm": "오이도",
                        "barvlDt": "60",
                        "btrainNo": "approaching",
                        "arvlMsg2": "노원 전역 출발",
                        "arvlCd": "3",
                    },
                    {
                        "subwayId": "1004",
                        "trainLineNm": "오이도행 - 노원방면",
                        "bstatnNm": "오이도",
                        "barvlDt": "0",
                        "btrainNo": "departed",
                        "arvlMsg2": "상계 출발",
                        "arvlCd": "2",
                    },
                    {
                        "subwayId": "1004",
                        "trainLineNm": "당고개행 - 상계방면",
                        "bstatnNm": "당고개",
                        "barvlDt": "90",
                        "btrainNo": "opposite",
                        "arvlMsg2": "창동 전역 출발",
                        "arvlCd": "3",
                    },
                ]
            },
        )
    )

    arrivals = await fetch_arrivals(api_key, "상계", "4호선", ["노원"])

    assert [arrival.train_no for arrival in arrivals] == ["approaching"]
    assert arrivals[0].stations_away == 1


@respx.mock
async def test_fetch_arrivals_retries_alt_station_name_when_normalized_query_is_empty():
    """Seoul's realtimeStationArrival is inconsistent about parenthetical

    station-name suffixes: some stations (e.g. 광나루(장신대)) only match
    their full CSV display name, even though normalize_name() strips the
    suffix for cross-source matching everywhere else. When the primary,
    normalized query returns zero results, fall back to the raw registry
    name before giving up.
    """
    api_key = "secret-seoul-key"
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{api_key}/json/realtimeStationArrival/0/30/광나루"
    ).mock(
        return_value=Response(
            200,
            json={
                "status": 500,
                "code": "INFO-200",
                "message": "해당하는 데이터가 없습니다.",
                "total": 0,
            },
        )
    )
    respx.get(
        f"http://swopenapi.seoul.go.kr/api/subway/{api_key}/json/realtimeStationArrival/0/30/광나루(장신대)"
    ).mock(
        return_value=Response(
            200,
            json={
                "realtimeArrivalList": [
                    {
                        "subwayId": "1005",
                        "trainLineNm": "방화행 - 아차산방면",
                        "bstatnNm": "방화",
                        "barvlDt": "60",
                        "btrainNo": "5602",
                        "arvlMsg2": "광나루(장신대) 전역출발",
                        "arvlCd": "3",
                    },
                ]
            },
        )
    )

    arrivals = await fetch_arrivals(
        api_key,
        "광나루",
        "5호선",
        ["아차산"],
        alt_station_name="광나루(장신대)",
    )

    assert [arrival.train_no for arrival in arrivals] == ["5602"]


def _onboard_leg() -> SubwayLeg:
    return SubwayLeg(
        route="수도권3호선",
        line_key="3호선",
        section_time=180,
        start_name="원점역",
        end_name="종점역",
        stations=[
            LegStation(index=0, name="원점역", lat=37.0, lon=127.0),
            LegStation(index=1, name="중간역", lat=37.1, lon=127.1),
            LegStation(index=2, name="다음역", lat=37.2, lon=127.2),
            LegStation(index=3, name="종점역", lat=37.3, lon=127.3),
        ],
    )


def _position(train_no: str, station: str, status: str, terminus: str, **extra: str) -> dict:
    return {
        "trainNo": train_no,
        "subwayId": "1003",
        "subwayNm": "3호선",
        "statnNm": station,
        "statnTnm": terminus,
        "trainSttus": status,
        "recptnDt": "2026-07-09 14:13:16",
        "directAt": "0",
        **extra,
    }


def test_find_onboard_trains_returns_intermediate_same_direction_position():
    trains = find_onboard_trains(
        [_position("3001", "중간역", "1", "종점역", directAt="1")],
        _onboard_leg(),
        now=1_000_000,
    )

    assert len(trains) == 1
    assert trains[0].model_dump() == {
        "train_no": "3001",
        "line_name": "3호선",
        "terminus": "종점역",
        "direction_label": "종점역 방면",
        "station_name": "중간역",
        "station_index": 1,
        "status": "arrived",
        "observed_at": 1_783_573_996,
        "matches_direction": True,
        "is_express": True,
    }


def test_find_onboard_trains_excludes_wrong_line_opposite_direction_and_past_end_positions():
    trains = find_onboard_trains(
        [
            _position("wrong-line", "중간역", "1", "종점역", subwayId="1002"),
            _position("opposite", "중간역", "1", "원점역"),
            _position("turned-around", "다음역", "1", "중간역"),
            _position("past-end", "종점역", "1", "종점역"),
        ],
        _onboard_leg(),
        now=1_000_000,
    )

    assert trains == []


def test_find_onboard_trains_excludes_train_at_origin_but_includes_departed_origin():
    trains = find_onboard_trains(
        [
            _position("at-origin", "원점역", "1", "종점역"),
            _position("departed-origin", "원점역", "2", "종점역", recptnDt="not-a-timestamp"),
        ],
        _onboard_leg(),
        now=1_000_000,
    )

    assert [(train.train_no, train.station_index, train.status, train.observed_at) for train in trains] == [
        ("departed-origin", 0, "departed", 1_000_000),
    ]


def test_find_onboard_trains_maps_status_three_to_the_segment_before_its_reported_station():
    trains = find_onboard_trains(
        [_position("between", "다음역", "3", "종점역")],
        _onboard_leg(),
        now=1_000_000,
    )

    assert [(train.station_name, train.station_index, train.status) for train in trains] == [
        ("다음역", 2, "between"),
    ]


def test_find_onboard_trains_resolves_tmap_style_subway_name_without_numeric_id():
    trains = find_onboard_trains(
        [
            _position("mapped", "중간역", "1", "종점역", subwayId="", subwayNm="수도권3호선"),
            _position("unknown", "중간역", "1", "종점역", subwayId="", subwayNm="알수없는노선"),
        ],
        _onboard_leg(),
        now=1_000_000,
    )

    assert [train.train_no for train in trains] == ["mapped"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_position_observed_at_returns_now_for_non_finite_numeric_values(value: float):
    assert _position_observed_at(value, now=1_000_000) == 1_000_000
