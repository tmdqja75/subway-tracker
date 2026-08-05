from enum import StrEnum

from pydantic import BaseModel, Field


class JourneyState(StrEnum):
    AWAITING_BOARD = "awaiting_board"
    ON_TRAIN = "on_train"
    PUSHING = "pushing"
    COMPLETED = "completed"
    PUSH_FAILED = "push_failed"
    CANCELLED = "cancelled"


class Station(BaseModel):
    station_id: str
    name: str
    line: str
    lat: float
    lon: float


class RouteHistoryItem(BaseModel):
    start: Station
    end: Station


class RouteHistoryResponse(BaseModel):
    most_used: list[RouteHistoryItem]
    recent: list[RouteHistoryItem]


class LegStation(BaseModel):
    """A station on a subway leg's pass-stop list (coords from Tmap)."""

    index: int
    name: str
    lat: float
    lon: float


class SubwayLeg(BaseModel):
    """One trackable transit ride within an itinerary.

    Historically this was subway-only; bus/train legs use line_key=None and
    fall back to timer tracking.
    """

    route: str  # Tmap route name, e.g. "수도권3호선"
    line_key: str | None  # normalized key for Seoul realtime APIs, None if uncovered
    mode: str = "SUBWAY"  # Tmap transit mode: SUBWAY, BUS, EXPRESSBUS, TRAIN, FERRY
    section_time: int  # seconds
    start_name: str
    end_name: str
    stations: list[LegStation]
    # actual track geometry from Tmap passShape.linestring, as [lat, lon] pairs
    shape: list[list[float]] = Field(default_factory=list)
    # walking transfer after this subway leg before the next subway leg, from
    # the intervening Tmap WALK leg's linestring, as [lat, lon] pairs
    transfer_walk_shape: list[list[float]] = Field(default_factory=list)
    transfer_walk_time: int = 0


class Itinerary(BaseModel):
    total_time: int
    transfer_count: int
    total_walk_time: int
    fare: int | None
    legs: list[SubwayLeg]
    summary: list[str]  # human-readable leg descriptions incl. walks


class ArrivingTrain(BaseModel):
    train_no: str
    line_name: str
    terminus: str
    direction_label: str  # e.g. "성수행 - 구의방면"
    eta_seconds: int
    arrival_msg: str
    # Current station-relative state from the subway feed. It lets the
    # boarding diagram place an arrived train on its station instead of
    # treating every feed record as somewhere between stations.
    status: str = "approaching"
    stations_away: int | None  # None when Seoul gives us no usable distance signal
    stations_away_estimated: bool  # True when derived from ETA rather than the API's own count
    matches_direction: bool
    is_express: bool


class OnboardTrain(BaseModel):
    """A realtime-position train already travelling within a subway leg."""

    train_no: str
    line_name: str
    terminus: str
    direction_label: str
    station_name: str
    station_index: int  # next station while status is "between"
    status: str  # approaching / arrived / departed / between
    observed_at: int  # unix epoch seconds
    matches_direction: bool
    is_express: bool


class TrackPoint(BaseModel):
    lat: float
    lon: float
    ts: int  # unix epoch seconds
    estimated: bool


class TrainStatus(BaseModel):
    train_no: str
    station_name: str
    station_index: int | None  # next station while status is "between"
    status: str  # approaching / arrived / departed / between / estimated / lost
    lat: float
    lon: float
    updated_at: int
