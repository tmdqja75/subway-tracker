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
