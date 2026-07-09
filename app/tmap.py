"""Tmap public-transit route search client.

Docs: https://tmap-public-skopenapi.readme.io/reference/대중교통-api
POST https://apis.openapi.sk.com/transit/routes with appKey header.
"""

import httpx

from .lines import tmap_route_to_line_key
from .models import Itinerary, LegStation, SubwayLeg

TRANSIT_URL = "https://apis.openapi.sk.com/transit/routes"


class TmapError(Exception):
    pass


async def search_routes(
    app_key: str,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    count: int = 5,
) -> list[Itinerary]:
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
    if resp.status_code != 200:
        raise TmapError(f"Tmap HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    plan = data.get("metaData", {}).get("plan")
    if not plan:
        # Tmap signals "no route" / errors via a result object
        msg = data.get("result", {}).get("message", "no plan in response")
        raise TmapError(f"Tmap: {msg}")

    itineraries = []
    for it in plan.get("itineraries", []):
        subway_legs = []
        summary = []
        for leg in it.get("legs", []):
            mode = leg.get("mode")
            start_name = leg.get("start", {}).get("name", "?")
            end_name = leg.get("end", {}).get("name", "?")
            if mode == "SUBWAY":
                route = leg.get("route", "")
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
                # passShape.linestring: "lon,lat lon,lat ..." — the real track geometry
                shape = []
                for pair in leg.get("passShape", {}).get("linestring", "").split():
                    try:
                        lon_s, lat_s = pair.split(",")
                        shape.append([float(lat_s), float(lon_s)])
                    except ValueError:
                        continue
                subway_legs.append(
                    SubwayLeg(
                        route=route,
                        line_key=tmap_route_to_line_key(route),
                        section_time=int(leg.get("sectionTime", 0)),
                        start_name=start_name,
                        end_name=end_name,
                        stations=stations,
                        shape=shape,
                    )
                )
                summary.append(f"🚇 {route}: {start_name} → {end_name}")
            elif mode == "WALK":
                sec = int(leg.get("sectionTime", 0))
                if sec >= 60:
                    summary.append(f"🚶 도보 {sec // 60}분")
        if not subway_legs:
            continue  # walk-only / bus itineraries are out of scope
        fare = it.get("fare", {}).get("regular", {}).get("totalFare")
        itineraries.append(
            Itinerary(
                total_time=int(it.get("totalTime", 0)),
                transfer_count=int(it.get("transferCount", 0)),
                total_walk_time=int(it.get("totalWalkTime", 0)),
                fare=int(fare) if fare is not None else None,
                legs=subway_legs,
                summary=summary,
            )
        )
    return itineraries
