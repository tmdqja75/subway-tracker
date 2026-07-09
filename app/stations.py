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
        stations: list[Station] = []
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    stations.append(
                        Station(
                            station_id=row["역사_ID"].strip(),
                            name=row["역사명"].strip(),
                            line=row["호선"].strip(),
                            lat=float(row["위도"]),
                            lon=float(row["경도"]),
                        )
                    )
                except (KeyError, ValueError):
                    continue  # skip malformed rows
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
            for s in candidates:
                if line in s.line or s.line in line:
                    return s
        return candidates[0]
