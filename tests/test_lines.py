from app.lines import LINE_KEY_TO_API_ID, tmap_route_to_line_key


def test_line_key_to_api_id_covers_every_currently_supported_line():
    assert LINE_KEY_TO_API_ID == {
        "1호선": "1", "2호선": "2", "3호선": "3", "4호선": "4", "5호선": "5",
        "6호선": "6", "7호선": "7", "8호선": "8", "9호선": "9",
        "GTX-A": "151",
        "중앙선": "101",
        "경의중앙선": "101",
        "수인분당선": "102",
        "신분당선": "103",
        "경춘선": "104",
        "경강선": "105",
        "우이신설선": "106",
        "서해선": "107",
        "공항철도": "108",
    }


def test_tmap_route_to_line_key_unchanged_for_covered_and_aliased_routes():
    assert tmap_route_to_line_key("수도권3호선") == "3호선"
    assert tmap_route_to_line_key("수도권9호선(급행)") == "9호선"
    assert tmap_route_to_line_key("수도권분당선") == "수인분당선"
    assert tmap_route_to_line_key("수도권신림선") is None
