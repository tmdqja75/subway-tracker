import pytest
import respx
from httpx import Response

from app.seoul import SeoulApiError, _check, fetch_arrivals, fetch_positions


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
