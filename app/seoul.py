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

RATE_LIMIT_CODES = {"ERROR-337", "HTTP-429"}


class SeoulApiError(Exception):
    def __init__(self, code: str = "", message: str = ""):
        self.code = code
        self.message = message
        super().__init__(f"Seoul API {code}: {message}" if code else message)


def _api_keys(api_key: str, fallback_api_key: str = "") -> list[str]:
    keys = []
    if api_key:
        keys.append(api_key)
    if fallback_api_key and fallback_api_key not in keys:
        keys.append(fallback_api_key)
    return keys or [api_key]


def _is_rate_limit_error(error: SeoulApiError) -> bool:
    return error.code in RATE_LIMIT_CODES or "호출건수" in error.message


def _has_fallback(keys: list[str], idx: int) -> bool:
    return idx < len(keys) - 1


def _log_seoul_request(endpoint: str, **fields: str) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    log.info("seoul api request endpoint=%s %s", endpoint, suffix)


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
    message = err.get("message", "")
    log.warning("%s: Seoul API error %s: %s", context, code, message)
    raise SeoulApiError(code, message)


async def _fetch_json_with_key_rotation(
    api_key: str,
    fallback_api_key: str,
    endpoint: str,
    path: str,
    context: str,
    log_fields: dict[str, str],
) -> dict:
    keys = _api_keys(api_key, fallback_api_key)
    async with httpx.AsyncClient(timeout=10) as client:
        for idx, key in enumerate(keys):
            url = f"{BASE}/{key}/json/{path}"
            t0 = time.monotonic()
            _log_seoul_request(endpoint, **log_fields)
            try:
                resp = await client.get(url)
            except httpx.RequestError as e:
                log.warning(
                    "%s request failed after %.2fs: %s",
                    context,
                    time.monotonic() - t0,
                    e,
                )
                raise
            elapsed = time.monotonic() - t0
            log.debug("%s status=%s elapsed=%.2fs", context, resp.status_code, elapsed)
            if resp.status_code == 429 and _has_fallback(keys, idx):
                log.warning("%s: Seoul API key rate limited (HTTP 429), retrying with fallback key", context)
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                log.warning(
                    "%s http error status=%s body=%s",
                    context,
                    resp.status_code,
                    resp.text[:300],
                )
                raise
            data = resp.json()
            try:
                _check(data, context)
            except SeoulApiError as e:
                if _is_rate_limit_error(e) and _has_fallback(keys, idx):
                    log.warning(
                        "%s: Seoul API key rate limited (%s), retrying with fallback key",
                        context,
                        e.code,
                    )
                    continue
                raise
            return data
    raise SeoulApiError("HTTP-429", "all configured Seoul API keys were rate limited")


async def fetch_positions(api_key: str, line_key: str, fallback_api_key: str = "") -> list[dict]:
    """All trains currently running on a line. Raw dicts from the API."""
    context = f"fetch_positions line={line_key}"
    data = await _fetch_json_with_key_rotation(
        api_key,
        fallback_api_key,
        "realtimePosition",
        f"realtimePosition/0/200/{line_key}",
        context,
        {"line": line_key},
    )
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
    *,
    fallback_api_key: str = "",
) -> list[ArrivingTrain]:
    """Trains approaching a station on a given line, closest first.

    Direction matching: the arrival API labels trains "성수행 - 구의방면";
    if the 방면 (or terminus) station appears among the stations we are about
    to pass through, the train is headed our way.
    """
    query_name = normalize_name(station_name)
    context = f"fetch_arrivals station={query_name}"
    data = await _fetch_json_with_key_rotation(
        api_key,
        fallback_api_key,
        "realtimeStationArrival",
        f"realtimeStationArrival/0/30/{query_name}",
        context,
        {"station": query_name, "line": line_key},
    )

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
