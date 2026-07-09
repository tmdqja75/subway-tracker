"""Seoul open-data realtime subway clients.

Position (OA-12764): http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimePosition/0/200/{line}
Arrival:             http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/30/{station}
Same API key works for both. Arrival btrainNo matches position trainNo.
"""

import logging
import time

import httpx

from .lines import LINE_TO_SUBWAY_ID
from .models import ArrivingTrain
from .stations import normalize_name

log = logging.getLogger(__name__)

BASE = "http://swopenapi.seoul.go.kr/api/subway"


class SeoulApiError(Exception):
    pass


def _check(data: dict, context: str) -> None:
    err = data.get("errorMessage") or data.get("RESULT") or {}
    if not err and (data.get("code") or data.get("status") not in (None, 200, "200")):
        # Some Seoul Open API failures come back as a top-level object such as
        # {"status": 500, "code": "ERROR-337", "message": "..."} instead
        # of the usual errorMessage/RESULT wrapper. Treat them as API errors;
        # otherwise callers see an empty realtimeArrivalList and the UI claims
        # there are simply no approaching trains.
        err = {
            "code": data.get("code") or f"HTTP-{data.get('status')}",
            "message": data.get("message", ""),
        }
    code = err.get("code", "INFO-000")
    if code == "INFO-000":
        return
    if code == "INFO-200":
        # empty result, not an error for us, but worth knowing when debugging
        # "no location logged" (e.g. line temporarily has zero running trains)
        log.debug("%s: no data (INFO-200)", context)
        return
    log.warning("%s: Seoul API error %s: %s", context, code, err.get("message", ""))
    raise SeoulApiError(f"Seoul API {code}: {err.get('message', '')}")


async def fetch_positions(api_key: str, line_key: str) -> list[dict]:
    """All trains currently running on a line. Raw dicts from the API."""
    url = f"{BASE}/{api_key}/json/realtimePosition/0/200/{line_key}"
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
        except httpx.RequestError as e:
            log.warning(
                "fetch_positions line=%s request failed after %.2fs: %s",
                line_key, time.monotonic() - t0, e,
            )
            raise
    elapsed = time.monotonic() - t0
    log.debug("fetch_positions line=%s status=%s elapsed=%.2fs", line_key, resp.status_code, elapsed)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.warning(
            "fetch_positions line=%s http error status=%s body=%s",
            line_key, resp.status_code, resp.text[:300],
        )
        raise
    data = resp.json()
    _check(data, f"fetch_positions line={line_key}")
    trains = data.get("realtimePositionList") or []
    log.debug(
        "fetch_positions line=%s trains=%d train_nos=%s",
        line_key, len(trains), [t.get("trainNo") for t in trains],
    )
    return trains


async def fetch_arrivals(
    api_key: str,
    station_name: str,
    line_key: str,
    upcoming_stations: list[str],
    limit: int = 3,
) -> list[ArrivingTrain]:
    """Trains approaching a station on a given line, closest first.

    Direction matching: the arrival API labels trains "성수행 - 구의방면";
    if the 방면 (or terminus) station appears among the stations we are about
    to pass through, the train is headed our way.
    """
    query_name = normalize_name(station_name)
    url = f"{BASE}/{api_key}/json/realtimeStationArrival/0/30/{query_name}"
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(url)
        except httpx.RequestError as e:
            log.warning(
                "fetch_arrivals station=%s request failed after %.2fs: %s",
                query_name, time.monotonic() - t0, e,
            )
            raise
    elapsed = time.monotonic() - t0
    log.debug("fetch_arrivals station=%s status=%s elapsed=%.2fs", query_name, resp.status_code, elapsed)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        log.warning(
            "fetch_arrivals station=%s http error status=%s body=%s",
            query_name, resp.status_code, resp.text[:300],
        )
        raise
    data = resp.json()
    _check(data, f"fetch_arrivals station={query_name}")

    subway_id = LINE_TO_SUBWAY_ID.get(line_key)
    upcoming = {normalize_name(n) for n in upcoming_stations}

    trains = []
    for a in data.get("realtimeArrivalList") or []:
        if subway_id and a.get("subwayId") != subway_id:
            continue
        direction_label = a.get("trainLineNm", "")
        toward = ""
        if "-" in direction_label:
            toward = direction_label.split("-")[-1].replace("방면", "").strip()
        terminus = a.get("bstatnNm", "")
        matches = (
            normalize_name(toward) in upcoming
            or normalize_name(terminus) in upcoming
        )
        try:
            eta = int(a.get("barvlDt", 0))
        except ValueError:
            eta = 0
        trains.append(
            ArrivingTrain(
                train_no=a.get("btrainNo", ""),
                line_name=line_key,
                terminus=terminus,
                direction_label=direction_label,
                eta_seconds=eta,
                arrival_msg=a.get("arvlMsg2", ""),
                matches_direction=matches,
                is_express=a.get("btrainSttus", "") == "급행",
            )
        )
    # matching direction first, then soonest
    trains.sort(key=lambda t: (not t.matches_direction, t.eta_seconds))
    matching = [t for t in trains if t.matches_direction][:limit]
    log.debug(
        "fetch_arrivals station=%s total=%d matching_direction=%d upcoming=%s",
        query_name, len(trains), len(matching), upcoming,
    )
    return matching if matching else trains[:limit]
