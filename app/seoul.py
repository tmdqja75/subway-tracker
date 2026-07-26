"""Seoul open-data realtime subway clients.

Position (OA-12764): http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimePosition/0/200/{line}
Arrival:             http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/30/{station}
Same API key works for both. Arrival btrainNo matches position trainNo.
"""

import logging
import math
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .lines import LINE_TO_SUBWAY_ID, tmap_route_to_line_key
from .models import ArrivingTrain, OnboardTrain, SubwayLeg
from .stations import normalize_name

log = logging.getLogger(__name__)

BASE = "http://swopenapi.seoul.go.kr/api/subway"

# Seoul's arvlCd: 0=진입/1=도착/2=출발 at the queried station, 3/4/5=전역출발/진입/도착
# (one station before it). Anything else (99 = "운행중") gives no fixed-distance
# code — arvlMsg2 sometimes carries "[N]번째 전역" instead, which we parse directly.
ARVL_CD_HERE = {"0", "1"}
ARVL_CD_DEPARTED = "2"
ARVL_CD_ONE_AWAY = {"3", "4", "5"}
BRACKET_COUNT = re.compile(r"\[(\d+)\]")
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")

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


def _position_observed_at(value: object, *, now: int | float) -> int:
    """Convert Seoul's reception timestamp to Unix seconds without reading a clock.

    The position feed normally uses ``YYYY-MM-DD HH:MM:SS`` in Seoul local
    time, but accepts ISO timestamps and numeric Unix seconds/milliseconds to
    remain usable with recorded and proxy-normalized payloads.  The caller
    supplies the fallback so candidate selection stays deterministic and pure.
    """
    fallback = int(now)
    if value is None or isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return fallback
        numeric = int(value)
        return numeric // 1_000 if numeric >= 1_000_000_000_000 else numeric

    text = str(value).strip()
    if not text:
        return fallback
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return int(datetime.strptime(text, pattern).replace(tzinfo=SEOUL_TIMEZONE).timestamp())
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SEOUL_TIMEZONE)
        return int(parsed.timestamp())
    except ValueError:
        pass
    try:
        numeric = int(text)
    except ValueError:
        return fallback
    return numeric // 1_000 if numeric >= 1_000_000_000_000 else numeric


def find_onboard_trains(
    positions: list[dict],
    leg: SubwayLeg,
    *,
    now: int | float,
) -> list[OnboardTrain]:
    """Select verified trains already travelling between a leg's endpoints.

    ``statnNm`` is a station for statuses 0/1/2 and the *next* station for
    status 3.  The latter is retained as ``station_index`` to match the live
    tracking model, while its active location is evaluated on the preceding
    station-to-station segment.
    """
    if not leg.line_key or len(leg.stations) < 2:
        return []

    station_names = [normalize_name(station.name) for station in leg.stations]
    last_index = len(station_names) - 1
    expected_subway_id = LINE_TO_SUBWAY_ID.get(leg.line_key)
    if expected_subway_id is None:
        # A line without a known Seoul realtime mapping cannot be verified
        # against this feed, so do not surface a potentially wrong train.
        return []
    candidates: list[OnboardTrain] = []

    for position in positions:
        train_no = str(position.get("trainNo") or "").strip()
        station_name = str(position.get("statnNm") or "").strip()
        terminus = str(position.get("statnTnm") or "").strip()
        raw_status = position.get("trainSttus")
        status_code = str(raw_status).strip() if raw_status is not None else ""
        if not train_no or not station_name or not terminus or status_code not in {"0", "1", "2", "3"}:
            continue

        subway_id = str(position.get("subwayId") or "").strip()
        line_name = str(position.get("subwayNm") or "").strip()
        if subway_id:
            if subway_id != expected_subway_id:
                continue
        elif tmap_route_to_line_key(line_name) != leg.line_key:
            # Without numeric evidence, accept only a known spelling that
            # resolves to this leg's normalized Seoul line key.
            continue

        normalized_station = normalize_name(station_name)
        normalized_terminus = normalize_name(terminus)
        if normalized_station not in station_names:
            continue
        station_index = station_names.index(normalized_station)
        if normalized_terminus not in station_names:
            continue
        terminus_index = station_names.index(normalized_terminus)

        if status_code == "0":
            status, active_location = "approaching", float(station_index)
        elif status_code == "1":
            status, active_location = "arrived", float(station_index)
        elif status_code == "2":
            status, active_location = "departed", station_index + 0.5
        else:  # 3: departed the previous station, travelling to statnNm
            status, active_location = "between", station_index - 0.5

        # A terminal ahead of the active location is the only direction
        # evidence in realtimePosition. Do not infer a direction from updnLine
        # or station order when the terminal cannot be reconciled with this leg.
        matches_direction = terminus_index > active_location
        if not matches_direction:
            continue
        if not 0 < active_location < last_index:
            continue

        candidates.append(
            OnboardTrain(
                train_no=train_no,
                line_name=line_name or leg.line_key,
                terminus=terminus,
                direction_label=f"{terminus} 방면",
                station_name=station_name,
                station_index=station_index,
                status=status,
                observed_at=_position_observed_at(position.get("recptnDt"), now=now),
                matches_direction=True,
                is_express=(
                    str(position.get("directAt") or "") == "1"
                    or str(position.get("btrainSttus") or "") == "급행"
                ),
            )
        )
    return candidates


async def fetch_arrivals(
    api_key: str,
    station_name: str,
    line_key: str,
    upcoming_stations: list[str],
    limit: int = 3,
    *,
    fallback_api_key: str = "",
    avg_seconds_per_station: float | None = None,
    alt_station_name: str | None = None,
) -> list[ArrivingTrain]:
    """Trains approaching a station on a given line, closest first.

    Direction matching: the arrival API labels trains "성수행 - 구의방면".
    Return only trains whose 방면 (or, when absent, terminus) is one of the
    stations this leg will pass through. A train that has already departed the
    boarding station is excluded even though the arrival API still reports it.

    Seoul's own distance signal (arvlCd / the "[N]번째 전역" text in arvlMsg2)
    is used when present. Some lines only ever report a countdown in seconds
    (barvlDt) with no station count at all -- for those, `avg_seconds_per_station`
    (typically this leg's own scheduled time-per-station) is used to derive an
    estimated count instead, so the UI never has to fall back to showing a time.

    `realtimeStationArrival` matches on Seoul's own exact station display
    name, which is inconsistent about parenthetical disambiguators -- some
    stations (e.g. "광나루(장신대)") only return results with the suffix
    included, others (e.g. "왕십리") only return results *without* one, even
    though normalize_name() strips it for cross-source matching everywhere
    else. When the normalized query comes back empty, retry once with
    `alt_station_name` (typically the station registry's raw CSV name) before
    giving up, so the picker doesn't stay empty forever for these stations.
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
    if not data.get("realtimeArrivalList") and alt_station_name and alt_station_name != query_name:
        alt_context = f"fetch_arrivals station={alt_station_name} (alt)"
        alt_data = await _fetch_json_with_key_rotation(
            api_key,
            fallback_api_key,
            "realtimeStationArrival",
            f"realtimeStationArrival/0/30/{alt_station_name}",
            alt_context,
            {"station": alt_station_name, "line": line_key},
        )
        if alt_data.get("realtimeArrivalList"):
            data = alt_data

    subway_id = LINE_TO_SUBWAY_ID.get(line_key)
    upcoming = {normalize_name(n) for n in upcoming_stations}

    trains = []
    for a in data.get("realtimeArrivalList") or []:
        if subway_id and a.get("subwayId") != subway_id:
            continue
        arvl_cd = a.get("arvlCd", "")
        if arvl_cd == ARVL_CD_DEPARTED:
            continue
        direction_label = a.get("trainLineNm", "")
        toward = ""
        if "-" in direction_label:
            toward = direction_label.split("-")[-1].replace("방면", "").strip()
        terminus = a.get("bstatnNm", "")
        direction_station = normalize_name(toward)
        matches = direction_station in upcoming if direction_station else normalize_name(terminus) in upcoming
        try:
            eta = int(a.get("barvlDt", 0))
        except ValueError:
            eta = 0
        arrival_msg = a.get("arvlMsg2", "")
        stations_away: int | None
        estimated = False
        if arvl_cd in ARVL_CD_HERE:
            stations_away = 0
        elif arvl_cd in ARVL_CD_ONE_AWAY:
            stations_away = 1
        elif (m := BRACKET_COUNT.search(arrival_msg)):
            stations_away = int(m.group(1))
        elif eta > 0 and avg_seconds_per_station:
            stations_away = max(1, round(eta / avg_seconds_per_station))
            estimated = True
        else:
            stations_away = None
            estimated = True
        trains.append(
            ArrivingTrain(
                train_no=a.get("btrainNo", ""),
                line_name=line_key,
                terminus=terminus,
                direction_label=direction_label,
                eta_seconds=eta,
                arrival_msg=arrival_msg,
                stations_away=stations_away,
                stations_away_estimated=estimated,
                matches_direction=matches,
                is_express=a.get("btrainSttus", "") == "급행",
            )
        )
    # matching direction first, then fewest stations away, then soonest
    trains.sort(key=lambda t: (
        not t.matches_direction,
        t.stations_away if t.stations_away is not None else 999,
        t.eta_seconds,
    ))
    matching = [t for t in trains if t.matches_direction][:limit]
    log.debug(
        "fetch_arrivals station=%s total=%d matching_direction=%d upcoming=%s",
        query_name, len(trains), len(matching), upcoming,
    )
    return matching
