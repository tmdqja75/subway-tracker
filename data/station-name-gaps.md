# Station name mismatches: subway server vs stations.csv

Compared `get_stn_list()` in `subway/topis.py` (hardcoded per-lineId station lists used by
`/subway/seoul?lineId=`) against `data/stations.csv`. All lines fully covered — every
initial "missing" was an old/renamed station name in the server code, not a real gap.

| lineId | 호선 | server name | stations.csv name | note |
|---|---|---|---|---|
| 1 | 1호선 | 평택지제 | 지제 | renamed station |
| 7 | 7호선 | 춘의역 | 춘의 | server-side typo ("역" suffix) |
| 101 | 경의중앙선 | 한국항공대 | 화전 | renamed station (position matches: 강매 → 화전 → 수색) |
| 105 | 경강선 | 세종왕릉 | 세종대왕릉 | server-side typo (missing "대") |
| 107 | 서해선 | 시우 | 원곡 | renamed station (position matches: 원시 → 원곡 → 초지) |

No stations present in the server's line lists are absent from `stations.csv`.
