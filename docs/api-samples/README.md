# API response samples

Live responses captured 2026-07-09, pretty-printed JSON. API keys not included.

| File | API | Request |
|---|---|---|
| `tmap-transit-routes.json` | Tmap Transit `POST /transit/routes` | 강남(127.028104, 37.496837) → 서울역(126.97296, 37.55569), count=3. Subway legs carry `passStopList.stations` (ordered stations w/ coords) and `passShape.linestring` ("lon,lat lon,lat …" track geometry). |
| `seoul-realtime-position.json` | Seoul `realtimePosition/0/200/3호선` | All trains on line 3. Key fields: `trainNo`, `statnNm`, `trainSttus` (0 진입 / 1 도착 / 2 출발 / 3 전역출발), `updnLine`, `statnTnm` (terminus). |
| `seoul-realtime-arrival.json` | Seoul `realtimeStationArrival/0/30/선릉` | Trains approaching 선릉, all lines. Key fields: `subwayId` (line code), `btrainNo` (matches position `trainNo`), `barvlDt` (ETA sec), `trainLineNm` ("종착행 - 방면"), `bstatnNm` (terminus), `arvlMsg2`. |
