"""Client for the self-hosted subway realtime API (~/Documents/subway).

One endpoint returns a whole line's stations in order, each carrying its
current up/dn train lists. This is a superset of Seoul's old separate
position + arrival feeds: a single snapshot fetch, plus the pure derivation
functions in this module, reconstruct both "where is train X right now" and
"what's approaching station Y" without a second endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .models import SubwayLeg
from .stations import normalize_name

log = logging.getLogger(__name__)


class SubwayApiError(Exception):
    pass


@dataclass
class TrainEntry:
    status: str  # "접근" | "도착" | "출발"
    kind: str  # e.g. "일반" | "급행"
    dest: str
    no: str


@dataclass
class StationSnapshot:
    name: str
    up: list[TrainEntry]
    dn: list[TrainEntry]


# up = decreasing snapshot index, dn = increasing snapshot index: verified
# against live traffic across every line, including every appended branch.
# Only these two lines loop and need an explicit wrap rule; every other
# line uses the plain +/-1 rule.
_LOOP_WRAP = {
    "2호선": {"forward_wrap": (42, 0), "backward_wrap": (0, 42)},
    "6호선": {"backward_wrap": (0, 5)},
}
_MAX_LINE_STATIONS = 200  # generous bound; no real line snapshot is this long


def _step(line_key: str, idx: int, direction: int) -> int:
    """One physical hop from idx in `direction` (+1 dn, -1 up), honoring
    this line's loop topology (see _LOOP_WRAP)."""
    rules = _LOOP_WRAP.get(line_key, {})
    if direction == 1 and rules.get("forward_wrap") and idx == rules["forward_wrap"][0]:
        return rules["forward_wrap"][1]
    if direction == -1 and rules.get("backward_wrap") and idx == rules["backward_wrap"][0]:
        return rules["backward_wrap"][1]
    return idx + direction


def _stations_between(line_key: str, start: int, target: int, direction: int) -> int | None:
    """Number of _step hops from start to target following `direction`.

    None if target isn't reached within _MAX_LINE_STATIONS hops (target is
    not behind start in this direction, e.g. it's the wrong way or on an
    unrelated branch).
    """
    idx = start
    for count in range(_MAX_LINE_STATIONS):
        if idx == target:
            return count
        idx = _step(line_key, idx, direction)
    return None


def _parse_entries(raw: list[dict]) -> list[TrainEntry]:
    return [
        TrainEntry(
            status=entry.get("status") or "",
            kind=entry.get("type") or "",
            dest=entry.get("dest") or "",
            no=entry.get("no") or "",
        )
        for entry in raw
    ]


def _parse_snapshot(payload: dict) -> list[StationSnapshot]:
    return [
        StationSnapshot(
            name=entry.get("stn", ""),
            up=_parse_entries(entry.get("up") or []),
            dn=_parse_entries(entry.get("dn") or []),
        )
        for entry in payload.get("data") or []
    ]


async def fetch_line_snapshot(base_url: str, line_id: str) -> list[StationSnapshot]:
    """Fetch one line's full station snapshot.

    Retries once on malformed JSON: this API returned truncated responses
    under concurrent request load during manual testing.
    """
    url = f"{base_url}/subway/seoul"
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(2):
            try:
                resp = await client.get(url, params={"lineId": line_id})
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                last_error = e
                log.warning("subway feed lineId=%s attempt=%d failed: %s", line_id, attempt, e)
                continue
            return _parse_snapshot(payload)
    raise SubwayApiError(f"lineId={line_id}: {last_error}")


def _find_all(snapshot: list[StationSnapshot], name: str) -> list[int]:
    target = normalize_name(name)
    return [i for i, s in enumerate(snapshot) if normalize_name(s.name) == target]


def _leg_station_indices(snapshot: list[StationSnapshot], leg: SubwayLeg) -> list[int | None]:
    """Resolve every leg station name to a snapshot index.

    Only 성수/신도림 (2호선's branch junctions) occur twice in a snapshot.
    Unambiguous names resolve directly; an ambiguous one is anchored to
    whichever of its occurrences sits closest (by snapshot index) to any
    *other*, already-unambiguous station in this same leg — not just the
    previous one, since the ambiguous station can be the leg's first stop.
    """
    all_matches = [_find_all(snapshot, station.name) for station in leg.stations]
    resolved: list[int | None] = [m[0] if len(m) == 1 else None for m in all_matches]

    for i, matches in enumerate(all_matches):
        if len(matches) <= 1:
            continue
        anchors = [idx for idx in resolved if idx is not None]
        if not anchors:
            resolved[i] = matches[0]
            continue
        resolved[i] = min(matches, key=lambda cand: min(abs(cand - anchor) for anchor in anchors))

    return resolved


def _leg_direction(line_key: str, indices: list[int | None]) -> int | None:
    """+1 (dn) or -1 (up): whichever direction reaches indices[1] from
    indices[0] in one _step hop. None if unresolved or not one hop apart."""
    if len(indices) < 2 or indices[0] is None or indices[1] is None:
        return None
    for direction in (1, -1):
        if _step(line_key, indices[0], direction) == indices[1]:
            return direction
    return None
