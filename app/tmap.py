"""Tmap public-transit route search client.

Docs: https://tmap-public-skopenapi.readme.io/reference/대중교통-api
POST https://apis.openapi.sk.com/transit/routes with appKey header.
"""

from dataclasses import dataclass

import httpx

from .lines import tmap_route_to_line_key
from .models import Itinerary, LegStation, SubwayLeg

TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"


class TmapError(Exception):
    pass


@dataclass(frozen=True)
class TmapRouteSearchResult:
    itineraries: list[Itinerary]
    raw_response_json: str


TRACKABLE_TRANSIT_MODES = {"SUBWAY", "BUS", "EXPRESSBUS", "TRAIN", "FERRY"}
MODE_EMOJI = {
    "SUBWAY": "🚇",
    "BUS": "🚌",
    "EXPRESSBUS": "🚌",
    "TRAIN": "🚆",
    "FERRY": "⛴️",
}


def _parse_linestring(linestring: str) -> list[list[float]]:
    """Parse Tmap "lon,lat lon,lat ..." geometry into [lat, lon] pairs."""
    shape = []
    for pair in linestring.split():
        try:
            lon_s, lat_s = pair.split(",")
            shape.append([float(lat_s), float(lon_s)])
        except ValueError:
            continue
    return shape


def _walk_linestring_shape(leg: dict) -> list[list[float]]:
    """Return WALK geometry from passShape, falling back to step linestrings."""
    shape = _parse_linestring(leg.get("passShape", {}).get("linestring", ""))
    if shape:
        return shape

    for step in leg.get("steps", []):
        step_shape = _parse_linestring(step.get("linestring", ""))
        if shape and step_shape and shape[-1] == step_shape[0]:
            shape.extend(step_shape[1:])
        else:
            shape.extend(step_shape)
    return shape


def _parse_transit_leg(leg: dict, mode: str) -> SubwayLeg:
    route = leg.get("route", "")
    start_name = leg.get("start", {}).get("name", "?")
    end_name = leg.get("end", {}).get("name", "?")
    # live API uses "stations"; some docs say "stationList"
    psl = leg.get("passStopList", {})
    raw_stations = psl.get("stations") or psl.get("stationList") or []
    stations = [
        LegStation(
            index=int(s.get("index", i)),
            name=s.get("stationName", "?"),
            lat=float(s["lat"]),
            lon=float(s["lon"]),
        )
        for i, s in enumerate(raw_stations)
    ]
    # passShape.linestring: "lon,lat lon,lat ..." — the real route geometry
    shape = _parse_linestring(leg.get("passShape", {}).get("linestring", ""))
    return SubwayLeg(
        route=route,
        line_key=tmap_route_to_line_key(route) if mode == "SUBWAY" else None,
        section_time=int(leg.get("sectionTime", 0)),
        start_name=start_name,
        end_name=end_name,
        stations=stations,
        shape=shape,
    )


def _transit_summary(mode: str, leg: SubwayLeg) -> str:
    emoji = MODE_EMOJI.get(mode, "🚆")
    return f"{emoji} {leg.route}: {leg.start_name} → {leg.end_name}"


def _parse_itineraries(data: dict) -> list[Itinerary]:
    plan = data.get("metaData", {}).get("plan")
    if not plan:
        # Tmap signals "no route" / errors via a result object
        msg = data.get("result", {}).get("message", "no plan in response")
        raise TmapError(f"Tmap: {msg}")

    itineraries = []
    for it in plan.get("itineraries", []):
        transit_legs = []
        summary = []
        previous_mode = None
        pending_transfer_walk = None
        for leg in it.get("legs", []):
            mode = leg.get("mode")
            if mode in TRACKABLE_TRANSIT_MODES:
                if pending_transfer_walk and transit_legs:
                    transit_legs[-1].transfer_walk_time = pending_transfer_walk["time"]
                    transit_legs[-1].transfer_walk_shape = pending_transfer_walk["shape"]
                pending_transfer_walk = None
                transit_leg = _parse_transit_leg(leg, mode)
                transit_legs.append(transit_leg)
                summary.append(_transit_summary(mode, transit_leg))
            elif mode == "WALK":
                sec = int(leg.get("sectionTime", 0))
                if sec >= 60:
                    summary.append(f"🚶 도보 {sec // 60}분")
                pending_transfer_walk = (
                    {"time": sec, "shape": _walk_linestring_shape(leg)}
                    if transit_legs and previous_mode != "WALK"
                    else None
                )
            else:
                pending_transfer_walk = None
            previous_mode = mode
        if not transit_legs:
            continue  # walk-only itineraries cannot be tracked/selected
        fare = it.get("fare", {}).get("regular", {}).get("totalFare")
        itineraries.append(
            Itinerary(
                total_time=int(it.get("totalTime", 0)),
                transfer_count=int(it.get("transferCount", 0)),
                total_walk_time=int(it.get("totalWalkTime", 0)),
                fare=int(fare) if fare is not None else None,
                legs=transit_legs,
                summary=summary,
            )
        )
    return itineraries


async def search_routes_with_raw_response(
    app_key: str,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    count: int = 5,
) -> TmapRouteSearchResult:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TRANSIT_URL,
            headers={"appKey": app_key, "content-type": "application/json"},
            json={
                "startX": str(start_lon),
                "startY": str(start_lat),
                "endX": str(end_lon),
                "endY": str(end_lat),
                "count": count,
                "lang": 0,
                "format": "json",
            },
        )
    raw_response_json = resp.text
    if resp.status_code != 200:
        raise TmapError(f"Tmap HTTP {resp.status_code}: {raw_response_json[:300]}")
    data = resp.json()
    return TmapRouteSearchResult(
        itineraries=_parse_itineraries(data),
        raw_response_json=raw_response_json,
    )


async def search_routes(
    app_key: str,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    count: int = 5,
) -> list[Itinerary]:
    result = await search_routes_with_raw_response(
        app_key,
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        count=count,
    )
    return result.itineraries
