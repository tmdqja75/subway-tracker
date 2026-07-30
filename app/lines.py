"""Line-name mapping between Tmap route names and the subway feed API.

Tmap route names carry a 수도권 prefix and express suffixes; line_key is the
normalized name used throughout this codebase (SubwayLeg.line_key, DB rows,
the frontend contract). The subway feed API takes a numeric lineId instead,
so LINE_KEY_TO_API_ID bridges the two without changing line_key's values.
"""

import re

LINE_KEY_TO_API_ID = {
    "1호선": "1", "2호선": "2", "3호선": "3", "4호선": "4", "5호선": "5",
    "6호선": "6", "7호선": "7", "8호선": "8", "9호선": "9",
    "GTX-A": "151",
    # 중앙선 has no separate id in the new API; it's the same corridor as 경의중앙선.
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

_COVERED = set(LINE_KEY_TO_API_ID)

_TMAP_ALIASES = {
    "수도권광역급행철도": "GTX-A",
    "GTX-A": "GTX-A",
    "경의선": "경의중앙선",
    "분당선": "수인분당선",
    "수인선": "수인분당선",
}


def tmap_route_to_line_key(route: str) -> str | None:
    """Map a Tmap route name (e.g. "수도권3호선", "수도권9호선(급행)") to this
    codebase's line_key. None means no covered subway feed for it."""
    name = re.sub(r"\(.*?\)", "", route).strip()
    name = name.removeprefix("수도권").strip()
    if name in _TMAP_ALIASES:
        return _TMAP_ALIASES[name]
    if name in _COVERED:
        return name
    return None
