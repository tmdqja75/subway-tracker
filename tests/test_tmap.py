import json

import pytest
import respx
from httpx import Response

from app.tmap import TRANSIT_URL, search_routes, search_routes_with_raw_response


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


@pytest.mark.asyncio
async def test_search_routes_returns_every_tmap_itinerary_with_timer_only_bus_legs():
    data = {
        "metaData": {
            "plan": {
                "itineraries": [
                    {
                        "fare": {"regular": {"totalFare": 1600}},
                        "totalTime": 1200,
                        "transferCount": 0,
                        "totalWalkTime": 120,
                        "legs": [
                            {
                                "mode": "SUBWAY",
                                "route": "수도권2호선",
                                "sectionTime": 900,
                                "start": {"name": "강남"},
                                "end": {"name": "사당"},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "강남", "lon": "127.0277", "lat": "37.4980"},
                                        {"index": 1, "stationName": "사당", "lon": "126.9816", "lat": "37.4766"},
                                    ]
                                },
                            }
                        ],
                    },
                    {
                        "fare": {"regular": {"totalFare": 1400}},
                        "totalTime": 900,
                        "transferCount": 0,
                        "totalWalkTime": 60,
                        "legs": [
                            {
                                "mode": "BUS",
                                "route": "간선:360",
                                "sectionTime": 780,
                                "start": {"name": "강남역"},
                                "end": {"name": "서울역"},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "강남역", "lon": "127.0294", "lat": "37.4987"},
                                        {"index": 1, "stationName": "서울역", "lon": "126.9728", "lat": "37.5535"},
                                    ]
                                },
                                "passShape": {
                                    "linestring": "127.0294,37.4987 126.9728,37.5535"
                                },
                            }
                        ],
                    },
                    {
                        "fare": {"regular": {"totalFare": 1700}},
                        "totalTime": 1500,
                        "transferCount": 1,
                        "totalWalkTime": 60,
                        "legs": [
                            {
                                "mode": "BUS",
                                "route": "간선:146",
                                "sectionTime": 360,
                                "start": {"name": "강남역"},
                                "end": {"name": "신논현역"},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "강남역", "lon": "127.0294", "lat": "37.4987"},
                                        {"index": 1, "stationName": "신논현역", "lon": "127.0250", "lat": "37.5045"},
                                    ]
                                },
                            },
                            {
                                "mode": "WALK",
                                "sectionTime": 60,
                                "passShape": {
                                    "linestring": "127.0250,37.5045 127.0252,37.5047"
                                },
                            },
                            {
                                "mode": "SUBWAY",
                                "route": "수도권9호선",
                                "sectionTime": 960,
                                "start": {"name": "신논현"},
                                "end": {"name": "서울역"},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "신논현", "lon": "127.0252", "lat": "37.5047"},
                                        {"index": 1, "stationName": "서울역", "lon": "126.9728", "lat": "37.5535"},
                                    ]
                                },
                            },
                        ],
                    },
                ]
            }
        }
    }

    with respx.mock:
        respx.post(TRANSIT_URL).mock(return_value=Response(200, json=data))
        itineraries = await search_routes("key", 127.0, 37.0, 126.0, 37.5)

    assert len(itineraries) == len(data["metaData"]["plan"]["itineraries"])
    assert itineraries[1].legs[0].route == "간선:360"
    assert itineraries[1].legs[0].line_key is None
    assert itineraries[1].legs[0].shape == [[37.4987, 127.0294], [37.5535, 126.9728]]
    assert itineraries[1].summary == ["🚌 간선:360: 강남역 → 서울역"]
    assert [leg.route for leg in itineraries[2].legs] == ["간선:146", "수도권9호선"]
    assert itineraries[2].legs[0].transfer_walk_time == 60


@pytest.mark.asyncio
async def test_search_routes_with_raw_response_returns_body_text():
    data = {
        "metaData": {
            "plan": {
                "itineraries": [
                    {
                        "fare": {"regular": {"totalFare": 1400}},
                        "totalTime": 660,
                        "transferCount": 0,
                        "totalWalkTime": 0,
                        "legs": [
                            {
                                "mode": "SUBWAY",
                                "route": "수도권2호선",
                                "sectionTime": 660,
                                "start": {"name": "강남"},
                                "end": {"name": "사당"},
                                "passStopList": {
                                    "stations": [
                                        {"index": 0, "stationName": "강남", "lon": "127.0277", "lat": "37.4980"},
                                        {"index": 1, "stationName": "사당", "lon": "126.9816", "lat": "37.4766"},
                                    ]
                                },
                            }
                        ],
                    }
                ]
            }
        }
    }
    raw_body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    with respx.mock:
        respx.post(TRANSIT_URL).mock(
            return_value=Response(200, content=raw_body, headers={"content-type": "application/json"})
        )
        result = await search_routes_with_raw_response("key", 127.0, 37.0, 126.0, 37.5)

    assert result.raw_response_json == raw_body
    assert len(result.itineraries) == 1
