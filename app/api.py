"""REST API consumed by the mobile frontend."""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .models import Itinerary
from .seoul import SeoulApiError, fetch_arrivals
from .stations import normalize_name
from .tmap import TmapError, search_routes

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
    try:
        itineraries = await search_routes(
            settings.tmap_app_key, start.lon, start.lat, end.lon, end.lat
        )
    except TmapError as e:
        raise HTTPException(502, str(e))
    if not itineraries:
        raise HTTPException(404, "no subway routes found")
    return [it.model_dump() for it in itineraries]


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
        return {"covered": False, "trains": []}
    upcoming = [s.name for s in leg.stations[1:]]
    try:
        trains = await fetch_arrivals(
            settings.seoul_api_key, leg.start_name, leg.line_key, upcoming
        )
    except SeoulApiError as e:
        raise HTTPException(502, str(e))
    return {"covered": True, "trains": [t.model_dump() for t in trains]}


@router.post("/journeys/current/board")
async def board(request: Request, body: BoardRequest):
    try:
        await request.app.state.manager.board(body.train_no)
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
