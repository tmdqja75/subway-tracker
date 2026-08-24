from app.stations import StationRegistry


def test_from_csv_exposes_line_1_label_for_gyeongwon_line_stations(tmp_path):
    csv_path = tmp_path / "stations.csv"
    csv_path.write_text(
        "역사_ID,역사명,호선,위도,경도\n"
        "1010,한남,경원선,37.52943,126.988537\n"
        "0222,강남,2호선,37.49795,127.027619\n",
        encoding="utf-8",
    )

    registry = StationRegistry.from_csv(csv_path)

    gyeongwon_station = registry.get("1010")
    line_2_station = registry.get("0222")

    assert gyeongwon_station is not None
    assert line_2_station is not None
    assert gyeongwon_station.line == "1호선"
    assert line_2_station.line == "2호선"


def test_from_csv_folds_legacy_corridor_names_into_the_branded_line_tmap_uses(tmp_path):
    """Station master 호선 values must agree with app.lines' Tmap-derived
    line_key vocabulary, or the same physical route keys differently in
    route_options_cache vs. journeys and route_history() shows duplicates."""
    csv_path = tmp_path / "stations.csv"
    csv_path.write_text(
        "역사_ID,역사명,호선,위도,경도\n"
        "1023,선릉,분당선,37.504856,127.048807\n"
        "1850,선정릉,분당선,37.510735,127.043677\n"
        "1099,오이도,수인선,37.375479,126.727844\n",
        encoding="utf-8",
    )

    registry = StationRegistry.from_csv(csv_path)

    assert registry.get("1023").line == "수인분당선"
    assert registry.get("1850").line == "수인분당선"
    assert registry.get("1099").line == "수인분당선"


def test_find_prefers_exact_line_match_over_a_folded_legacy_alias(tmp_path):
    """서울역 has both a literal "1호선" row and a "경부선" row (now folded to
    "1호선" too) at different coordinates; a "1호선" query must deterministically
    land on the literal row, not whichever alias happens to sort first."""
    csv_path = tmp_path / "stations.csv"
    csv_path.write_text(
        "역사_ID,역사명,호선,위도,경도\n"
        "1001,서울역,경부선,37.554337,126.971134\n"
        "0150,서울역,1호선,37.556228,126.972135\n",
        encoding="utf-8",
    )

    registry = StationRegistry.from_csv(csv_path)

    found = registry.find("서울역", "1호선")

    assert found is not None
    assert found.station_id == "0150"
