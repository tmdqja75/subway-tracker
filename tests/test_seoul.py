import pytest

from app.seoul import SeoulApiError, _check


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
