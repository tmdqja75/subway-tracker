"""REST API consumed by the mobile frontend."""

import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from .models import Itinerary, RouteHistoryItem, RouteHistoryResponse
from .stations import normalize_name
from .subway_feed import SubwayApiError, fetch_arrivals, fetch_boarding_context, fetch_onboard_candidates
from .tmap import TmapError, search_routes_with_raw_response

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class RouteSearchRequest(BaseModel):
    start: str
    end: str
    # station_id from the autocomplete pick: pins the exact line's coordinates
    start_id: str | None = None
    end_id: str | None = None


class StartJourneyRequest(BaseModel):
    itinerary: Itinerary


class BoardRequest(BaseModel):
    train_no: str | None = None  # None: board on an uncovered line (timer mode)
    retroactive: bool = False


def _route_cache_key(start, end) -> tuple[str, str, str, str]:
    return (normalize_name(start.name), start.line, normalize_name(end.name), end.line)


async def _fetch_leg_arrivals(settings, leg):
    """Return trains that are still boardable for the given subway leg."""
    try:
        return await fetch_arrivals(settings.subway_api_url, leg)
    except SubwayApiError as e:
        raise HTTPException(502, str(e))


async def _fetch_leg_onboard(settings, leg):
    """Return already-onboard candidates for a covered subway leg."""
    try:
        return await fetch_onboard_candidates(settings.subway_api_url, leg, now=time.time())
    except SubwayApiError as e:
        raise HTTPException(502, str(e))


async def _fetch_leg_context(settings, leg):
    """Return station names before the boarding station, for the boarding-line diagram."""
    try:
        return await fetch_boarding_context(settings.subway_api_url, leg)
    except SubwayApiError as e:
        raise HTTPException(502, str(e))


@router.get("/stations/search")
async def station_search(request: Request, q: str):
    registry = request.app.state.stations
    return [s.model_dump() for s in registry.search(q, limit=30)]


@router.post("/routes")
async def routes(request: Request, body: RouteSearchRequest):
    registry = request.app.state.stations
    settings = request.app.state.settings
    start = (registry.get(body.start_id) if body.start_id else None) or registry.find(body.start)
    end = (registry.get(body.end_id) if body.end_id else None) or registry.find(body.end)
    if not start:
        raise HTTPException(404, f"station not found: {body.start}")
    if not end:
        raise HTTPException(404, f"station not found: {body.end}")
    db = request.app.state.manager.db
    cache_key = _route_cache_key(start, end)
    cached = db.get_cached_route_options(*cache_key)
    if cached is not None:
        log.debug(
            "route cache hit start=%s/%s end=%s/%s",
            cache_key[0], cache_key[1], cache_key[2], cache_key[3],
        )
        db.touch_cached_route_options(*cache_key)
        return [it.model_dump() for it in cached]
    try:
        route_search = await search_routes_with_raw_response(
            settings.tmap_app_key, start.lon, start.lat, end.lon, end.lat
        )
    except TmapError as e:
        raise HTTPException(502, str(e))
    itineraries = route_search.itineraries
    if not itineraries:
        raise HTTPException(404, "no subway routes found")
    db.cache_route_options(
        *cache_key,
        itineraries,
        raw_tmap_response=route_search.raw_response_json,
    )
    return [it.model_dump() for it in itineraries]


@router.get("/routes/history", response_model=RouteHistoryResponse)
async def route_history(request: Request):
    registry = request.app.state.stations
    most_used, recent = request.app.state.manager.db.route_history()

    def resolve(routes: list[tuple[str, str, str, str]]) -> list[RouteHistoryItem]:
        items = []
        for start_name, start_line, end_name, end_line in routes:
            start = registry.find(start_name, start_line)
            end = registry.find(end_name, end_line)
            if start is not None and end is not None:
                items.append(RouteHistoryItem(start=start, end=end))
            if len(items) == 5:
                break
        return items

    return RouteHistoryResponse(most_used=resolve(most_used), recent=resolve(recent))


@router.post("/journeys")
async def start_journey(request: Request, body: StartJourneyRequest):
    manager = request.app.state.manager
    j = await manager.start_journey(body.itinerary)
    return {"journey_id": j.id, "state": j.state}


@router.get("/journeys/current")
async def current_journey(request: Request):
    snap = request.app.state.manager.snapshot()
    return snap or {"state": "idle"}


@router.get("/journeys/current/arrivals")
async def arrivals(request: Request):
    """Closest trains approaching the current leg's boarding station."""
    manager = request.app.state.manager
    settings = request.app.state.settings
    j = manager.active
    if not j:
        raise HTTPException(404, "no active journey")
    leg = j.leg
    if not leg.line_key:
        return {"covered": False, "trains": [], "already_onboard": [], "context_before": []}
    trains = await _fetch_leg_arrivals(settings, leg)
    try:
        already_onboard = await _fetch_leg_onboard(settings, leg)
    except HTTPException as error:
        log.warning(
            "optional onboard-candidate lookup failed; returning arrivals without "
            "onboard candidates line=%s station=%s status=%s",
            leg.line_key, leg.start_name, error.status_code,
        )
        already_onboard = []
    try:
        context_before = await _fetch_leg_context(settings, leg)
    except HTTPException as error:
        log.warning(
            "optional boarding-context lookup failed; returning arrivals without "
            "context stations line=%s station=%s status=%s",
            leg.line_key, leg.start_name, error.status_code,
        )
        context_before = []
    return {
        "covered": True,
        "trains": [train.model_dump() for train in trains],
        "already_onboard": [train.model_dump() for train in already_onboard],
        "context_before": context_before,
    }


@router.post("/journeys/current/board")
async def board(request: Request, body: BoardRequest):
    manager = request.app.state.manager
    j = manager.active
    if body.retroactive:
        if not body.train_no:
            raise HTTPException(409, "retroactive board requires an onboard train")
        if not j or not j.leg.line_key:
            raise HTTPException(409, "retroactive boarding is unavailable for this leg")
        candidates = await _fetch_leg_onboard(request.app.state.settings, j.leg)
        candidate = next((c for c in candidates if c.train_no == body.train_no), None)
        if candidate is None:
            raise HTTPException(409, "selected train is no longer travelling this leg")
        try:
            await manager.begin_realtime_tracking_from_onboard(candidate)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}
    if body.train_no and j and j.leg.line_key:
        trains = await _fetch_leg_arrivals(request.app.state.settings, j.leg)
        if body.train_no not in {train.train_no for train in trains}:
            raise HTTPException(409, "selected train is no longer approaching this platform")
    try:
        await manager.board(body.train_no)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/journeys/current/alight")
async def alight(request: Request):
    try:
        await request.app.state.manager.alight()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/journeys/current/missed")
async def missed(request: Request):
    try:
        await request.app.state.manager.missed_train()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/journeys/current/cancel")
async def cancel(request: Request):
    try:
        await request.app.state.manager.cancel()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/journeys/current/retry-push")
async def retry_push(request: Request):
    try:
        await request.app.state.manager.retry_push()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/journeys/current/points")
async def points(request: Request):
    manager = request.app.state.manager
    if not manager.active:
        raise HTTPException(404, "no active journey")
    return [p.model_dump() for p in manager.db.get_points(manager.active.id)]


@router.get("/debug/locations")
async def debug_locations(request: Request, limit: int = Query(20, ge=1, le=100)):
    """Recent journeys with route geometry and logged points for map debugging."""
    manager = request.app.state.manager
    return {"journeys": manager.db.list_debug_journeys(limit)}
