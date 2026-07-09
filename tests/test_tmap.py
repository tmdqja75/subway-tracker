import pytest
import respx
from httpx import Response

from app.tmap import TRANSIT_URL, search_routes


@pytest.mark.asyncio
async def test_search_routes_preserves_subway_transfer_walk_linestring():
    data = {
        "metaData": {
            "plan": {
                "itineraries": [
                    {
                        "fare": {"regular": {"totalFare": 1600}},
                        "totalTime": 1200,
                        "transferCount": 1,
                        "totalWalkTime": 120,
                        "legs": [
                            {
                                "mode": "SUBWAY",
                                "route": "수도권2호선",
                                "routeId": "110021006",
                                "sectionTime": 300,
                                "start": {"name": "강남", "lon": 127.0277, "lat": 37.4980},
                                "end": {"name": "사당", "lon": 126.9816, "lat": 37.4766},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "강남", "lon": "127.0277", "lat": "37.4980"},
                                        {"index": 1, "stationName": "사당", "lon": "126.9816", "lat": "37.4766"},
                                    ]
                                },
                                "passShape": {
                                    "linestring": "127.0277,37.4980 126.9816,37.4766"
                                },
                            },
                            {
                                "mode": "WALK",
                                "sectionTime": 120,
                                "distance": 100,
                                "start": {"name": "사당", "lon": 126.9816, "lat": 37.4766},
                                "end": {"name": "사당", "lon": 126.9817, "lat": 37.4768},
                                "passShape": {
                                    "linestring": "126.981600,37.476600 126.981300,37.476700 126.981700,37.476800"
                                },
                            },
                            {
                                "mode": "SUBWAY",
                                "route": "수도권4호선",
                                "routeId": "110041008",
                                "sectionTime": 600,
                                "start": {"name": "사당", "lon": 126.9817, "lat": 37.4768},
                                "end": {"name": "서울역", "lon": 126.9728, "lat": 37.5535},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "사당", "lon": "126.9817", "lat": "37.4768"},
                                        {"index": 1, "stationName": "서울역", "lon": "126.9728", "lat": "37.5535"},
                                    ]
                                },
                                "passShape": {
                                    "linestring": "126.9817,37.4768 126.9728,37.5535"
                                },
                            },
                        ],
                    }
                ]
            }
        }
    }

    with respx.mock:
        respx.post(TRANSIT_URL).mock(return_value=Response(200, json=data))
        itineraries = await search_routes("key", 127.0, 37.0, 126.0, 37.5)

    assert len(itineraries) == 1
    first_leg = itineraries[0].legs[0]
    assert first_leg.transfer_walk_time == 120
    assert first_leg.transfer_walk_shape == [
        [37.476600, 126.981600],
        [37.476700, 126.981300],
        [37.476800, 126.981700],
    ]
