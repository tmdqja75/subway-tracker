"""Seoul open-data realtime subway clients.

Position (OA-12764): http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimePosition/0/200/{line}
Arrival:             http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/30/{station}
Same API key works for both. Arrival btrainNo matches position trainNo.
"""

import httpx

from .lines import LINE_TO_SUBWAY_ID
from .models import ArrivingTrain
from .stations import normalize_name

BASE = "http://swopenapi.seoul.go.kr/api/subway"


class SeoulApiError(Exception):
    pass


def _check(data: dict) -> None:
    err = data.get("errorMessage") or data.get("RESULT") or {}
    code = err.get("code", "INFO-000")
    # INFO-000 = ok, INFO-200 = no data (empty result, not an error for us)
    if code not in ("INFO-000", "INFO-200"):
        raise SeoulApiError(f"Seoul API {code}: {err.get('message', '')}")


async def fetch_positions(api_key: str, line_key: str) -> list[dict]:
    """All trains currently running on a line. Raw dicts from the API."""
    url = f"{BASE}/{api_key}/json/realtimePosition/0/200/{line_key}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    _check(data)
    return data.get("realtimePositionList") or []


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
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()
    _check(data)

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
    return matching if matching else trains[:limit]
