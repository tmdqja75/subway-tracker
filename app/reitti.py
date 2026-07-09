"""Reitti ingest client.

Reitti 4.0 accepts OwnTracks-format points one per request:
POST {url}/api/v1/ingest/owntracks?token={token}
{"_type": "location", "lat": .., "lon": .., "tst": epoch, "acc": meters}
Reitti deduplicates on timestamp, so retrying a partial push is safe.
"""

import asyncio

import httpx

from .models import TrackPoint

ACC_REAL = 50  # station-anchored points
ACC_ESTIMATED = 150  # interpolated / time-based points


class ReittiError(Exception):
    pass


async def push_points(base_url: str, token: str, points: list[TrackPoint]) -> int:
    """Push all points; returns count sent. Raises ReittiError on failure."""
    url = f"{base_url.rstrip('/')}/api/v1/ingest/owntracks"
    sent = 0
    async with httpx.AsyncClient(timeout=15) as client:
        for p in points:
            body = {
                "_type": "location",
                "lat": p.lat,
                "lon": p.lon,
                "tst": p.ts,
                "acc": ACC_ESTIMATED if p.estimated else ACC_REAL,
            }
            for attempt in range(3):
                try:
                    resp = await client.post(url, params={"token": token}, json=body)
                    if resp.status_code < 300:
                        break
                    if resp.status_code in (401, 403):
                        raise ReittiError(f"Reitti auth failed ({resp.status_code})")
                except httpx.HTTPError as e:
                    if attempt == 2:
                        raise ReittiError(f"Reitti unreachable after {sent} points: {e}")
                await asyncio.sleep(1 + attempt)
            else:
                raise ReittiError(f"Reitti rejected point after {sent} sent")
            sent += 1
            await asyncio.sleep(0.05)  # be gentle
    return sent
