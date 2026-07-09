"""Line-name mapping between Tmap route names and Seoul realtime API identifiers.

The Seoul realtime position API takes a Korean line name path parameter;
the realtime arrival API returns numeric subwayId codes. Tmap route names
carry a 수도권 prefix and express suffixes. line_key is the position-API name.
"""

import re

# subwayId (arrival API) -> position API line name
SUBWAY_ID_TO_LINE = {
    "1001": "1호선",
    "1002": "2호선",
    "1003": "3호선",
    "1004": "4호선",
    "1005": "5호선",
    "1006": "6호선",
    "1007": "7호선",
    "1008": "8호선",
    "1009": "9호선",
    "1032": "GTX-A",
    "1061": "중앙선",
    "1063": "경의중앙선",
    "1065": "공항철도",
    "1067": "경춘선",
    "1075": "수인분당선",
    "1077": "신분당선",
    "1081": "경강선",
    "1092": "우이신설선",
    "1093": "서해선",
}

LINE_TO_SUBWAY_ID = {v: k for k, v in SUBWAY_ID_TO_LINE.items()}

_COVERED = set(SUBWAY_ID_TO_LINE.values())

# Tmap route spellings that don't reduce to a covered name mechanically
_TMAP_ALIASES = {
    "수도권광역급행철도": "GTX-A",
    "GTX-A": "GTX-A",
    "경의선": "경의중앙선",
    "분당선": "수인분당선",
    "수인선": "수인분당선",
}


def tmap_route_to_line_key(route: str) -> str | None:
    """Map a Tmap route name (e.g. "수도권3호선", "수도권9호선(급행)") to a
    Seoul position-API line name. None means the realtime API doesn't cover it."""
    name = re.sub(r"\(.*?\)", "", route).strip()
    name = name.removeprefix("수도권").strip()
    if name in _TMAP_ALIASES:
        return _TMAP_ALIASES[name]
    if name in _COVERED:
        return name
    return None
