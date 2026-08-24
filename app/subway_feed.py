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

from .lines import LINE_KEY_TO_API_ID
from .models import ArrivingTrain, OnboardTrain, SubwayLeg
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


# The source feed's up/dn labels generally map to decreasing/increasing
# snapshot indices respectively. Line 2 is the exception: its labels run in
# the opposite direction (up = increasing, dn = decreasing), verified against
# live 내선/외선 circulation. Only these two lines loop and need an explicit
# wrap rule; every other line uses the plain +/-1 rule.
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
    """+1 (dn) or -1 (up): the shortest direction to indices[1].

    An express leg's stations list only holds actual stops, so the second
    station can sit beyond a skipped local-only stop. On a loop, both
    directions can eventually reach the next stop; choosing the shorter route
    prevents the direction from being inverted merely because +1 is checked
    first. None means the stations are unresolved or unreachable.
    """
    if len(indices) < 2 or indices[0] is None or indices[1] is None:
        return None
    distances = [
        (distance, direction)
        for direction in (1, -1)
        if (distance := _stations_between(line_key, indices[0], indices[1], direction)) is not None
    ]
    return min(distances, default=(None, None))[1]


def _train_bucket(line_key: str, station: StationSnapshot, direction: int) -> list[TrainEntry]:
    """Return entries moving in a leg's snapshot-index direction.

    The feed's up/dn labeling follows the original mapping on every line
    except Line 2, whose circular 내선/외선 labels are inverted relative to
    the snapshot order.
    """
    if line_key == "2호선":
        return station.up if direction == 1 else station.dn
    return station.dn if direction == 1 else station.up


_STATUS_BY_RAW = {"접근": "approaching", "도착": "arrived", "출발": "departed"}


@dataclass
class LegTrainStatus:
    leg_index: int | None  # None: on this line, but not resolvable within the leg's span
    status: str  # "approaching" | "arrived" | "departed"
    station_name: str


def _flatten(snapshot: list[StationSnapshot]) -> dict[str, tuple[int, TrainEntry]]:
    """First occurrence per train number, scanning both buckets at every station."""
    found: dict[str, tuple[int, TrainEntry]] = {}
    for idx, station in enumerate(snapshot):
        for entry in (*station.up, *station.dn):
            if entry.no and entry.no not in found:
                found[entry.no] = (idx, entry)
    return found


async def locate_train(base_url: str, leg: SubwayLeg, train_no: str) -> LegTrainStatus | None:
    line_id = LINE_KEY_TO_API_ID.get(leg.line_key or "")
    if line_id is None:
        return None
    snapshot = await fetch_line_snapshot(base_url, line_id)
    found = _flatten(snapshot).get(train_no)
    if found is None:
        return None
    raw_idx, entry = found
    status = _STATUS_BY_RAW.get(entry.status, "approaching")

    indices = _leg_station_indices(snapshot, leg)
    direction = _leg_direction(leg.line_key or "", indices)
    boarding_idx = indices[0] if indices else None
    if direction is None or boarding_idx is None:
        return LegTrainStatus(leg_index=None, status=status, station_name=snapshot[raw_idx].name)

    leg_index = _stations_between(leg.line_key or "", boarding_idx, raw_idx, direction)
    if leg_index is not None and leg_index >= len(leg.stations):
        # Resolvable, but past the leg's own final station — treat the same
        # as unreachable so journey.py never indexes past leg.stations.
        leg_index = None
    return LegTrainStatus(leg_index=leg_index, status=status, station_name=snapshot[raw_idx].name)


async def fetch_boarding_context(base_url: str, leg: SubwayLeg, window: int = 3) -> list[str]:
    """Station names immediately before the boarding station, farthest to
    nearest, for the boarding-line diagram's "before" segment.

    leg.stations only spans from the boarding station onward (Tmap gives us
    one leg per ride, not the whole physical line), so this walks the full
    line snapshot backwards from boarding_idx instead. Empty (or shorter than
    `window`) if the line/direction can't be resolved or the boarding station
    is near the line's own end.
    """
    line_id = LINE_KEY_TO_API_ID.get(leg.line_key or "")
    if line_id is None:
        return []
    snapshot = await fetch_line_snapshot(base_url, line_id)
    line_key = leg.line_key or ""

    indices = _leg_station_indices(snapshot, leg)
    direction = _leg_direction(line_key, indices)
    boarding_idx = indices[0] if indices else None
    if direction is None or boarding_idx is None:
        return []

    names: list[str] = []
    idx = boarding_idx
    for _ in range(window):
        idx = _step(line_key, idx, -direction)
        if not (0 <= idx < len(snapshot)):
            break
        names.append(snapshot[idx].name)
    return list(reversed(names))


async def fetch_arrivals(base_url: str, leg: SubwayLeg, limit: int = 3) -> list[ArrivingTrain]:
    line_id = LINE_KEY_TO_API_ID.get(leg.line_key or "")
    if line_id is None:
        return []
    snapshot = await fetch_line_snapshot(base_url, line_id)
    line_key = leg.line_key or ""

    indices = _leg_station_indices(snapshot, leg)
    direction = _leg_direction(line_key, indices)
    boarding_idx = indices[0] if indices else None
    if direction is None or boarding_idx is None:
        return []

    ranked: list[tuple[int, TrainEntry, bool]] = []
    for raw_idx, station in enumerate(snapshot):
        bucket = _train_bucket(line_key, station, direction)
        for entry in bucket:
            if raw_idx == boarding_idx and entry.status == "출발":
                continue  # already left the boarding station
            distance = _stations_between(line_key, raw_idx, boarding_idx, direction)
            if distance is None:
                continue  # not behind the boarding station in this direction
            ranked.append((distance, entry, False))

    # At the physical end of a line, the upstream feed still labels a train
    # arriving at the platform as inbound until it has completed its turnback.
    # That train is boardable for this leg once it departs, so show it as a
    # clearly labelled option. Do not generalize this to opposite-direction
    # trains at ordinary stations.
    preceding_idx = _step(line_key, boarding_idx, -direction)
    at_terminal = not (0 <= preceding_idx < len(snapshot))
    if at_terminal:
        boarding = snapshot[boarding_idx]
        forward_bucket = _train_bucket(line_key, boarding, direction)
        reverse_bucket = boarding.dn if forward_bucket is boarding.up else boarding.up
        for entry in reverse_bucket:
            if (
                entry.status in {"접근", "도착"}
                and normalize_name(entry.dest) == normalize_name(boarding.name)
            ):
                ranked.append((0, entry, True))

    ranked.sort(key=lambda pair: pair[0])
    return [
        ArrivingTrain(
            train_no=entry.no,
            line_name=line_key,
            terminus=leg.end_name if turnback else entry.dest,
            direction_label=(f"회차 후 {leg.end_name} 방면" if turnback else f"{entry.dest}행"),
            eta_seconds=0,
            arrival_msg="회차 준비 중" if turnback else "운행 중",
            status="arrived" if turnback else _STATUS_BY_RAW.get(entry.status, "approaching"),
            stations_away=distance,
            stations_away_estimated=False,
            matches_direction=True,
            is_express=entry.kind == "급행",
        )
        for distance, entry, turnback in ranked[:limit]
    ]


async def fetch_onboard_candidates(base_url: str, leg: SubwayLeg, *, now: float) -> list[OnboardTrain]:
    line_id = LINE_KEY_TO_API_ID.get(leg.line_key or "")
    if line_id is None or len(leg.stations) < 2:
        return []
    snapshot = await fetch_line_snapshot(base_url, line_id)
    line_key = leg.line_key or ""

    indices = _leg_station_indices(snapshot, leg)
    direction = _leg_direction(line_key, indices)
    boarding_idx = indices[0] if indices else None
    if direction is None or boarding_idx is None:
        return []
    last_leg_index = len(leg.stations) - 1

    candidates: list[OnboardTrain] = []
    for raw_idx, station in enumerate(snapshot):
        bucket = _train_bucket(line_key, station, direction)
        for entry in bucket:
            leg_index = _stations_between(line_key, boarding_idx, raw_idx, direction)
            if leg_index is None:
                continue
            status = _STATUS_BY_RAW.get(entry.status, "approaching")
            in_bounds = (
                0 <= leg_index < last_leg_index
                if status == "departed"
                else 0 < leg_index < last_leg_index
            )
            if not in_bounds:
                continue
            candidates.append(
                OnboardTrain(
                    train_no=entry.no,
                    line_name=line_key,
                    terminus=entry.dest,
                    direction_label=f"{entry.dest}행",
                    station_name=station.name,
                    station_index=leg_index,
                    status=status,
                    observed_at=int(now),
                    matches_direction=True,
                    is_express=entry.kind == "급행",
                )
            )
    return candidates
