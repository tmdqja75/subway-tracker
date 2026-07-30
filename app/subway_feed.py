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
