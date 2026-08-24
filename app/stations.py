"""Station registry loaded from the Seoul station master CSV.

CSV columns: 역사_ID, 역사명, 호선, 위도, 경도
Used for station-name autocomplete and geocoding route-search endpoints.
Coordinates along a chosen route come from Tmap's passStopList instead.
"""

import csv
import re
from pathlib import Path

from .models import Station

_PAREN = re.compile(r"\(.*?\)")
_DISPLAY_LINE_BY_RAW = {
    # The station master keeps older Korail corridor/branch names for segments
    # folded into a branded line; Tmap/journeys always use the branded name
    # (app/lines.py's _TMAP_ALIASES), so leaving these raw here makes the same
    # physical route key differently in route_options_cache vs. journeys —
    # route_history() then shows it as two separate "duplicate" history
    # entries that both resolve to the same station. Mirrors the frontend's
    # equivalent table (frontend/lib/line-colors.ts SEGMENT_ALIASES).
    "경원선": "1호선",
    "경인선": "1호선",
    "경부선": "1호선",
    "장항선": "1호선",
    "일산선": "3호선",
    "안산선": "4호선",
    "과천선": "4호선",
    "진접선": "4호선",
    "별내선": "8호선",
    "분당선": "수인분당선",
    "수인선": "수인분당선",
}


def normalize_name(name: str) -> str:
    """Normalize a station name for matching across data sources.

    Tmap says "서울역", the CSV says "서울", the realtime API says "서울" —
    strip parentheticals, whitespace and a trailing 역.
    """
    name = _PAREN.sub("", name).strip()
    if name.endswith("역") and len(name) > 1:
        name = name[:-1]
    return name


class StationRegistry:
    def __init__(self, stations: list[Station]):
        self.stations = stations
        self._by_norm: dict[str, list[Station]] = {}
        self._by_id: dict[str, Station] = {}
        for s in stations:
            self._by_norm.setdefault(normalize_name(s.name), []).append(s)
            self._by_id[s.station_id] = s

    @classmethod
    def from_csv(cls, path: Path) -> "StationRegistry":
        rows: list[tuple[str, str, str, str, float, float]] = []
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    raw_line = row["호선"].strip()
                    rows.append((
                        row["역사_ID"].strip(),
                        row["역사명"].strip(),
                        raw_line,
                        _DISPLAY_LINE_BY_RAW.get(raw_line, raw_line),
                        float(row["위도"]),
                        float(row["경도"]),
                    ))
                except (KeyError, ValueError):
                    continue  # skip malformed rows

        # A folded legacy name (_DISPLAY_LINE_BY_RAW) can land on the same
        # (name, line) as a station that already has its own literal row for
        # that line — e.g. 서울역's "경부선" row now also reads "1호선", ~200m
        # from its literal "1호선" row. Keep the literal row and drop the
        # folded duplicate so find()/search() aren't left picking between two
        # entries for what Tmap only ever calls one line.
        literal_keys = {(name, line) for _, name, raw_line, line, _, _ in rows if raw_line == line}
        stations = [
            Station(station_id=station_id, name=name, line=line, lat=lat, lon=lon)
            for station_id, name, raw_line, line, lat, lon in rows
            if raw_line == line or (name, line) not in literal_keys
        ]
        return cls(stations)

    def search(self, query: str, limit: int = 10) -> list[Station]:
        q = normalize_name(query)
        if not q:
            return []
        exact, prefix, contains = [], [], []
        for s in self.stations:
            n = normalize_name(s.name)
            if n == q:
                exact.append(s)
            elif n.startswith(q):
                prefix.append(s)
            elif q in n:
                contains.append(s)
        return (exact + prefix + contains)[:limit]

    def get(self, station_id: str) -> Station | None:
        return self._by_id.get(station_id)

    def find(self, name: str, line: str | None = None) -> Station | None:
        candidates = self._by_norm.get(normalize_name(name), [])
        if not candidates:
            return None
        if line:
            # exact match first: some interchanges have two candidates whose
            # line only matches loosely once _DISPLAY_LINE_BY_RAW folds a
            # legacy corridor name onto a modern one (e.g. 서울역's "경부선"
            # and "1호선" rows sit ~200m apart) — an exact match must win over
            # that fallback rather than depend on candidate list order.
            for s in candidates:
                if line == s.line:
                    return s
            for s in candidates:
                if line in s.line or s.line in line:
                    return s
        return candidates[0]
