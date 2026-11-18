"""Infer when a train reached and left each leg station from recorded line snapshots.

While a leg waits at the platform, JourneyManager keeps whole-line snapshots
stamped with our own receive time (the feed carries no per-train time). A
train's status flips (접근 → 도착 → 출발, station by station) between two
consecutive snapshots, so each station event is placed at the midpoint of the
two snapshots that bracket the flip.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import SubwayLeg
from .stations import normalize_name
from .subway_feed import (
    StationSnapshot,
    _leg_direction,
    _leg_station_indices,
    _stations_between,
    _step,
    _train_bucket,
)

MAX_BRACKET_SECONDS = 45.0

_STATUS_STEP = {"접근": 0, "도착": 1, "출발": 2}


def _resolve(snapshot: list[StationSnapshot], leg: SubwayLeg) -> tuple[str, list[int | None], int] | None:
    line_key = leg.line_key or ""
    indices = _leg_station_indices(snapshot, leg)
    direction = _leg_direction(line_key, indices)
    if direction is None or not indices or indices[0] is None:
        return None
    return line_key, indices, direction


def train_progress(snapshot: list[StationSnapshot], leg: SubwayLeg, train_no: str) -> int | None:
    """Monotone position of `train_no` along the leg: 3 * station offset + status step.

    Offset 0 is the leg origin; trains still before it are negative. Only the
    leg-direction bucket counts, plus the terminus turnback entry fetch_arrivals
    also accepts, so an opposite-direction run of the same number never matches.
    """
    resolved = _resolve(snapshot, leg)
    if resolved is None:
        return None
    line_key, indices, direction = resolved
    origin = indices[0]

    for raw_idx, station in enumerate(snapshot):
        for entry in _train_bucket(line_key, station, direction):
            if entry.no != train_no or entry.status not in _STATUS_STEP:
                continue
            forward = _stations_between(line_key, origin, raw_idx, direction)
            backward = _stations_between(line_key, raw_idx, origin, direction)
            offsets = [o for o in (forward, None if backward is None else -backward) if o is not None]
            if not offsets:
                continue
            # On a loop both routes exist; the shorter is the real one (forward wins ties).
            return 3 * min(offsets, key=abs) + _STATUS_STEP[entry.status]

    preceding = _step(line_key, origin, -direction)
    if not 0 <= preceding < len(snapshot):
        boarding = snapshot[origin]
        forward_bucket = _train_bucket(line_key, boarding, direction)
        reverse_bucket = boarding.dn if forward_bucket is boarding.up else boarding.up
        for entry in reverse_bucket:
            if (
                entry.no == train_no
                and entry.status in ("접근", "도착")
                and normalize_name(entry.dest) == normalize_name(boarding.name)
            ):
                return _STATUS_STEP[entry.status]
    return None


def infer_station_events(
    history: Sequence[tuple[float, list[StationSnapshot]]],
    leg: SubwayLeg,
    train_no: str,
    *,
    max_bracket: float = MAX_BRACKET_SECONDS,
) -> list[tuple[float | None, float | None]]:
    """(arrival, departure) per leg station; None where the snapshots don't bracket it."""
    unknown: list[tuple[float | None, float | None]] = [(None, None)] * len(leg.stations)
    samples: list[tuple[float, int]] = []
    for fetched_at, snapshot in sorted(history, key=lambda item: item[0]):
        resolved = _resolve(snapshot, leg)
        if resolved is None:
            continue
        line_key, indices, direction = resolved
        # Express legs list only their stops; physical hop counts would then
        # land events on the wrong station, so don't infer at all.
        if indices[-1] is None or _stations_between(
            line_key, indices[0], indices[-1], direction,
        ) != len(leg.stations) - 1:
            return unknown
        progress = train_progress(snapshot, leg, train_no)
        if progress is not None:
            samples.append((fetched_at, progress))

    # A drop in progress means an earlier pass of the same train number: keep the latest run.
    start = 0
    for k in range(1, len(samples)):
        if samples[k][1] < samples[k - 1][1]:
            start = k
    samples = samples[start:]

    def event(threshold: int) -> float | None:
        first = next((k for k, (_, progress) in enumerate(samples) if progress >= threshold), None)
        if not first:  # never reached, or already reached in the first kept sample
            return None
        before, after = samples[first - 1][0], samples[first][0]
        return (before + after) / 2 if after - before <= max_bracket else None

    return [(event(3 * i + 1), event(3 * i + 2)) for i in range(len(leg.stations))]


def merge_event_times(schedule: list[float], inferred: list[float | None], upper: float) -> list[float]:
    """Inferred times where known; otherwise schedule shifted by the nearest known event.

    The result is strictly increasing in ≥1 s steps (points are integer seconds
    deduplicated per journey) and stays at least 1 s below `upper`.
    """
    known = [k for k, value in enumerate(inferred) if value is not None]
    times: list[float] = []
    for k, scheduled in enumerate(schedule):
        value = inferred[k]
        if value is not None:
            times.append(value)
        elif known:
            nearest = min(known, key=lambda i: abs(i - k))
            times.append(scheduled + inferred[nearest] - schedule[nearest])
        else:
            times.append(scheduled)
    for k in range(len(times) - 1, -1, -1):
        ceiling = upper if k == len(times) - 1 else times[k + 1]
        times[k] = min(times[k], ceiling - 1)
    return times
