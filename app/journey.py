"""Journey state machine and the background tracking loop.

One journey at a time. Per subway leg:
  AWAITING_BOARD -> (user picks train) -> ON_TRAIN -> arrival at leg end
  -> next leg (AWAITING_BOARD) or journey done -> push points to Reitti.

Tracking modes:
  realtime — poll the Seoul position API for the boarded train number and
             interpolate between station coordinates by elapsed time.
  timer    — line not covered by the realtime API (or train lost): advance
             along the leg's stations using Tmap's sectionTime.
"""

import asyncio
import logging
import math
import time

from .config import Settings
from .db import Database
from .models import Itinerary, JourneyState, OnboardTrain, SubwayLeg, TrackPoint, TrainStatus
from .reitti import ReittiError, push_points
from .stations import normalize_name
from .subway_feed import LegTrainStatus, SubwayApiError, locate_train

log = logging.getLogger(__name__)

MIN_STATION_POLL_WINDOW_SECONDS = 15
MAX_STATION_POLL_WINDOW_SECONDS = 60
STATION_POLL_WINDOW_FRACTION = 0.4
CRUISE_POLL_SECONDS = 30
MISSING_TRAIN_RETRY_SECONDS = 10
LOST_TRAIN_FALLBACK_SECONDS = 90
MAX_SEGMENT_FRACTION = 0.92  # never claim arrival from interpolation alone
DEFAULT_SEGMENT_SECONDS = 120
TRANSFER_FAILURE_MESSAGES = {
    "configuration": "Reitti 서버 설정이 완료되지 않았어요. 서버 주소와 토큰을 확인하세요.",
    "authentication": "Reitti 인증이 거부됐어요. 서버 토큰을 확인하세요.",
    "connection": "Reitti 서버에 연결하지 못했어요. 네트워크와 서버 상태를 확인하세요.",
    "rejected": "Reitti 서버가 위치 기록을 받지 않았어요. 서버 상태를 확인한 뒤 다시 시도하세요.",
    "unknown": "Reitti 전송 중 알 수 없는 오류가 발생했어요. 다시 시도하세요.",
}


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _distance_fractions(pts: list[list[float]]) -> list[float]:
    """Cumulative distance along pts as a 0..1 fraction per point, so
    timestamps can be spread by how far along the shape each point sits
    rather than by its raw index (Tmap shape vertices aren't evenly spaced)."""
    n = len(pts)
    if n <= 1:
        return [0.0] * n
    cum = [0.0]
    for i in range(1, n):
        cum.append(cum[-1] + _haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]))
    total = cum[-1]
    if total <= 0:
        return [i / (n - 1) for i in range(n)]
    return [d / total for d in cum]


def _station_shape_indices(leg: SubwayLeg) -> list[int]:
    """Nearest shape-vertex index for each station, monotonically increasing
    so segment slices of the linestring line up with station order."""
    if not leg.shape:
        return []
    idxs = []
    search_from = 0
    for s in leg.stations:
        best, best_d = search_from, float("inf")
        for k in range(search_from, len(leg.shape)):
            d = (leg.shape[k][0] - s.lat) ** 2 + (leg.shape[k][1] - s.lon) ** 2
            if d < best_d:
                best_d, best = d, k
        idxs.append(best)
        search_from = best
    return idxs


class ActiveJourney:
    def __init__(self, journey_id: int, itinerary: Itinerary, leg_idx: int = 0):
        self.id = journey_id
        self.itinerary = itinerary
        self.leg_idx = leg_idx
        self.state = JourneyState.AWAITING_BOARD
        self.train_no: str | None = None
        self.tracking_mode: str | None = None
        self.leg_started_at: int | None = None
        # True only when this leg began from a live train already underway and
        # its earlier route was reconstructed from schedule/geometry data.
        self.history_estimated = False
        # interpolation anchor: index of segment start station, and whether the
        # train was last seen stopped at it (phase "station") or running past
        # it (phase "segment")
        self.anchor_idx = 0
        self.anchor_phase = "station"
        self.anchor_time = 0.0
        self.lost_polls = 0
        self.missing_since: float | None = None
        self.next_seoul_poll_at = 0.0
        self.last_status: TrainStatus | None = None
        self.error: str | None = None
        self.error_reason: str | None = None
        self.error_sent_points: int | None = None
        self.error_total_points: int | None = None
        self.transfer_sent_points: int | None = None
        self.transfer_total_points: int | None = None
        self.transfer_attempt_base_sent_points = 0
        self.transfer_started_at: float | None = None
        # path logging: arrivals up to this station index are already written,
        # at last_arrival_time; shape_idx maps station index -> vertex in leg.shape
        self.logged_idx = 0
        self.last_arrival_time = 0.0
        self.shape_idx: list[int] = []

    def prepare_leg(self) -> None:
        """Reset per-leg tracking state and map stations onto the leg shape."""
        self.anchor_idx = 0
        self.anchor_phase = "station"
        self.anchor_time = time.time()
        self.lost_polls = 0
        self.missing_since = None
        self.next_seoul_poll_at = 0.0
        self.logged_idx = 0
        self.last_arrival_time = time.time()
        self.shape_idx = _station_shape_indices(self.leg)
        log.debug(
            "journey %s leg %s: line=%s stations=%d shape_pts=%d shape_idx=%s section_time=%s",
            self.id, self.leg_idx, self.leg.line_key, len(self.leg.stations),
            len(self.leg.shape or []), self.shape_idx, self.leg.section_time,
        )

    @property
    def leg(self) -> SubwayLeg:
        return self.itinerary.legs[self.leg_idx]

    @property
    def is_last_leg(self) -> bool:
        return self.leg_idx >= len(self.itinerary.legs) - 1


class JourneyManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.active: ActiveJourney | None = None
        self._task: asyncio.Task | None = None
        self._push_task: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------

    def resume_from_db(self) -> None:
        row = self.db.get_active_journey()
        if not row:
            return
        itinerary = self.db.load_itinerary(row)
        j = ActiveJourney(row["id"], itinerary, row["current_leg_idx"])
        j.state = JourneyState(row["state"])
        j.train_no = row["train_no"]
        j.tracking_mode = row["tracking_mode"]
        j.leg_started_at = row["leg_started_at"]
        j.history_estimated = bool(row["history_estimated"])
        j.error = row["error"]
        j.error_reason = row["error_reason"]
        j.error_sent_points = row["error_sent_points"]
        j.error_total_points = row["error_total_points"]
        # Older failed transfers predate the live-progress columns. Preserve
        # their durable retry counts when they are resumed after migration.
        j.transfer_sent_points = row["transfer_sent_points"]
        if j.transfer_sent_points is None:
            j.transfer_sent_points = row["error_sent_points"]
        j.transfer_total_points = row["transfer_total_points"]
        if j.transfer_total_points is None:
            j.transfer_total_points = row["error_total_points"]
        self.active = j
        if j.state == JourneyState.ON_TRAIN:
            j.prepare_leg()  # rebuild geometry mapping before restoring durable progress
            self._restore_leg_progress(j)
            self._start_tracker()
        elif j.state == JourneyState.PUSHING:
            self._start_push(j, resume=True)
        log.info("resumed journey %s in state %s", j.id, j.state)

    def _restore_leg_progress(self, j: ActiveJourney) -> None:
        """Recover the logged route and live interpolation anchor from points.

        SQLite intentionally persists the route points rather than transient
        tracker objects.  In particular, a retroactively started leg has an
        estimated path ending at one current non-estimated observation.  A
        fresh ``prepare_leg`` must not make the next position poll regard that
        history as unlogged and replay it from the origin.
        """
        points = self.db.get_points(j.id, leg_idx=j.leg_idx)
        if not points:
            return

        station_matches: list[tuple[int, TrackPoint]] = []
        for point in points:
            for station_idx, station in enumerate(j.leg.stations):
                if abs(point.lat - station.lat) < 1e-7 and abs(point.lon - station.lon) < 1e-7:
                    station_matches.append((station_idx, point))
                    break
        if station_matches:
            j.logged_idx = max(station_idx for station_idx, _ in station_matches)
            at_logged = [point for station_idx, point in station_matches if station_idx == j.logged_idx]
            estimated_arrivals = [point.ts for point in at_logged if point.estimated]
            j.last_arrival_time = max(estimated_arrivals or [point.ts for point in at_logged])

        live_points = [point for point in points if not point.estimated]
        if not live_points:
            return
        live = max(live_points, key=lambda point: point.ts)
        live_station_idx = next(
            (
                station_idx
                for station_idx, station in enumerate(j.leg.stations)
                if abs(live.lat - station.lat) < 1e-7 and abs(live.lon - station.lon) < 1e-7
            ),
            None,
        )
        if live_station_idx is not None:
            j.anchor_idx = live_station_idx
            # A preceding estimated point at this station is the reconstructed
            # arrival before the persisted live departure observation.  Without
            # it, the observation is a station anchor.
            j.anchor_phase = "segment" if any(
                point.estimated and point.ts < live.ts
                for station_idx, point in station_matches
                if station_idx == live_station_idx
            ) else "station"
            j.anchor_time = float(live.ts)
            return

        # The only non-station live anchor is a reconstructed "between" point.
        # Locate it on the closest station hop and preserve its elapsed fraction
        # so local interpolation continues from the observed position.
        best_idx, best_fraction, best_distance = 0, 0.0, float("inf")
        for station_idx in range(len(j.leg.stations) - 1):
            start, end = j.leg.stations[station_idx : station_idx + 2]
            dx, dy = end.lat - start.lat, end.lon - start.lon
            length_sq = dx * dx + dy * dy
            fraction = 0.0 if length_sq <= 0 else min(max(
                ((live.lat - start.lat) * dx + (live.lon - start.lon) * dy) / length_sq,
                0.0,
            ), 1.0)
            lat, lon = _lerp(start.lat, end.lat, fraction), _lerp(start.lon, end.lon, fraction)
            distance = (live.lat - lat) ** 2 + (live.lon - lon) ** 2
            if distance < best_distance:
                best_idx, best_fraction, best_distance = station_idx, fraction, distance
        j.anchor_idx = best_idx
        j.anchor_phase = "segment"
        j.anchor_time = live.ts - self._segment_seconds(j) * best_fraction

    async def start_journey(self, itinerary: Itinerary) -> ActiveJourney:
        if self.active and self.active.state != JourneyState.COMPLETED:
            await self.cancel()
        journey_id = self.db.create_journey(itinerary, JourneyState.AWAITING_BOARD)
        self.active = ActiveJourney(journey_id, itinerary)
        return self.active

    async def board(self, train_no: str | None) -> None:
        j = self._require_active()
        if j.state != JourneyState.AWAITING_BOARD:
            raise ValueError(f"cannot board in state {j.state}")
        now = time.time()
        if j.leg_idx > 0:
            self._log_transfer_walk(j, j.leg_idx - 1, now)
        j.train_no = train_no
        j.tracking_mode = "realtime" if (train_no and j.leg.line_key) else "timer"
        j.state = JourneyState.ON_TRAIN
        j.leg_started_at = int(now)
        j.transfer_started_at = None
        j.transfer_sent_points = None
        j.transfer_total_points = None
        j.prepare_leg()
        # user is standing on the platform: log the boarding station now
        start = j.leg.stations[0]
        self._emit(j, start.lat, start.lon, estimated=False)
        self.db.update_journey(
            j.id,
            state=j.state,
            train_no=train_no,
            tracking_mode=j.tracking_mode,
            leg_started_at=int(now),
            transfer_sent_points=None,
            transfer_total_points=None,
        )
        self._start_tracker()

    async def begin_realtime_tracking_from_onboard(self, candidate: OnboardTrain) -> None:
        """Track a freshly revalidated train that is already inside this leg.

        Seoul exposes a current station-relative observation, not historic stop
        events. Earlier points are explicitly schedule-derived estimates; the
        selected observation is persisted as the first non-estimated anchor.
        """
        j = self._require_active()
        if j.state != JourneyState.AWAITING_BOARD:
            raise ValueError(f"cannot begin onboard tracking in state {j.state}")
        if not isinstance(candidate, OnboardTrain):
            raise ValueError("onboard candidate must be an OnboardTrain")
        self._validate_onboard_candidate(j, candidate)

        j.train_no = candidate.train_no
        j.tracking_mode = "realtime"
        j.state = JourneyState.ON_TRAIN
        j.transfer_sent_points = None
        j.transfer_total_points = None
        j.history_estimated = True
        j.prepare_leg()

        budget, dwell, run = self._reconstruction_timing(j)
        observed_at = float(candidate.observed_at)
        idx = candidate.station_index
        if candidate.status == "departed":
            # observed_at is approximate departure from idx; its arrival is
            # retained as an estimated pause before the live departure anchor.
            origin_arrival = observed_at - idx * budget - dwell
        elif candidate.status in ("arrived", "approaching"):
            origin_arrival = observed_at - idx * budget
        else:  # between: station_index is the reported next station
            origin_arrival = observed_at - (idx - 1) * budget - dwell - run / 2

        # An onboard start skips the normal board() path, which is where the
        # preceding transfer's walk is usually recorded.  Finish that walk at
        # the reconstructed origin timestamp so it remains before the inferred
        # ride history and the current live observation.
        if j.leg_idx > 0:
            self._log_transfer_walk(j, j.leg_idx - 1, origin_arrival)
        j.transfer_started_at = None

        if candidate.status == "departed":
            # observed_at is approximate departure from idx; its arrival is
            # retained as an estimated pause before the live departure anchor.
            self._reconstruct_history(j, origin_arrival, idx, budget, dwell, run)
            j.logged_idx = idx
            j.last_arrival_time = observed_at - dwell
            j.anchor_idx, j.anchor_phase, j.anchor_time = idx, "segment", observed_at
            lat, lon = j.leg.stations[idx].lat, j.leg.stations[idx].lon
        elif candidate.status in ("arrived", "approaching"):
            # observed_at is the station arrival for these feed statuses.
            self._reconstruct_history(j, origin_arrival, idx, budget, dwell, run)
            j.logged_idx = idx
            j.last_arrival_time = observed_at
            j.anchor_idx, j.anchor_phase, j.anchor_time = idx, "station", observed_at
            lat, lon = j.leg.stations[idx].lat, j.leg.stations[idx].lon
        else:  # between: station_index is the reported next station
            previous_idx = idx - 1
            # Feed status 3 does not include GPS. Anchor the current live
            # observation halfway along the actual Tmap geometry instead.
            self._reconstruct_history(
                j, origin_arrival, previous_idx, budget, dwell, run,
                partial_segment_fraction=0.5,
            )
            j.logged_idx = previous_idx
            j.last_arrival_time = origin_arrival + previous_idx * budget
            # _interpolate uses the full existing station budget; this keeps
            # its local presentation at the midpoint until the next API poll.
            j.anchor_idx, j.anchor_phase, j.anchor_time = (
                previous_idx, "segment", observed_at - budget / 2,
            )
            lat, lon = self._segment_geometry(j, previous_idx, fraction=0.5)[-1]

        j.leg_started_at = int(origin_arrival)
        j.lost_polls = 0
        j.missing_since = None
        j.next_seoul_poll_at = 0.0
        j.last_status = TrainStatus(
            train_no=candidate.train_no,
            station_name=candidate.station_name,
            station_index=idx,
            status=candidate.status,
            lat=lat,
            lon=lon,
            updated_at=int(observed_at),
        )
        # An estimated geometry endpoint can land at the same second. Latest
        # write wins in SQLite, so this current observation stays authoritative.
        self._emit_at(j, lat, lon, int(observed_at), estimated=False)
        self.db.update_journey(
            j.id,
            state=j.state,
            train_no=j.train_no,
            tracking_mode=j.tracking_mode,
            leg_started_at=j.leg_started_at,
            history_estimated=True,
            transfer_sent_points=None,
            transfer_total_points=None,
        )
        self._start_tracker()

    def _validate_onboard_candidate(self, j: ActiveJourney, candidate: OnboardTrain) -> None:
        """Reject stale/hand-crafted candidates before they can start tracking."""
        stations = j.leg.stations
        if not j.leg.line_key or len(stations) < 2:
            raise ValueError("active leg does not support realtime onboard tracking")
        if not candidate.train_no.strip() or not candidate.matches_direction or candidate.observed_at <= 0:
            raise ValueError("invalid onboard train candidate")
        if candidate.status not in {"approaching", "arrived", "departed", "between"}:
            raise ValueError("invalid onboard train status")
        names = [normalize_name(station.name) for station in stations]
        station_name = normalize_name(candidate.station_name)
        if (
            candidate.station_index < 0
            or candidate.station_index >= len(stations)
            or station_name not in names
            or names[candidate.station_index] != station_name
        ):
            raise ValueError("onboard train station does not match the active leg")
        last_idx = len(stations) - 1
        if candidate.status == "between":
            # It may be in the final segment, but never beyond the alight stop.
            if not 0 < candidate.station_index <= last_idx:
                raise ValueError("onboard train is outside the active leg")
            active_location = candidate.station_index - 0.5
        elif candidate.status == "departed":
            # A departure observed at the rider's origin is already underway,
            # while a departure at the alight station is not a usable ride.
            if not 0 <= candidate.station_index < last_idx:
                raise ValueError("onboard train is outside the active leg")
            active_location = float(candidate.station_index)
        else:
            # Arrivals and approaches at an endpoint remain board/alight states,
            # not an already-onboard interior candidate.
            if not 0 < candidate.station_index < last_idx:
                raise ValueError("onboard train is outside the active leg")
            active_location = float(candidate.station_index)
        terminus = normalize_name(candidate.terminus)
        if terminus not in names or names.index(terminus) <= active_location:
            raise ValueError("onboard train direction does not match the active leg")

    def _reconstruction_timing(self, j: ActiveJourney) -> tuple[float, float, float]:
        station_count = len(j.leg.stations)
        budget = max(j.leg.section_time / max(station_count - 1, 1), 30)
        dwell = min(30, budget * 0.25)
        return budget, dwell, budget - dwell

    def _segment_geometry(
        self, j: ActiveJourney, start_idx: int, *, fraction: float = 1.0,
    ) -> list[list[float]]:
        """Tmap geometry for one station hop, optionally cut at its distance fraction."""
        end_idx = start_idx + 1
        if j.shape_idx:
            points = [list(point) for point in j.leg.shape[j.shape_idx[start_idx] : j.shape_idx[end_idx] + 1]]
        else:
            points = [
                [j.leg.stations[start_idx].lat, j.leg.stations[start_idx].lon],
                [j.leg.stations[end_idx].lat, j.leg.stations[end_idx].lon],
            ]
        fraction = min(max(fraction, 0.0), 1.0)
        if fraction >= 1.0 or len(points) < 2:
            return points
        fractions = _distance_fractions(points)
        prefix = [points[0]]
        for point_idx in range(1, len(points)):
            if fractions[point_idx] <= fraction:
                prefix.append(points[point_idx])
                continue
            before_fraction, after_fraction = fractions[point_idx - 1], fractions[point_idx]
            local_fraction = 0.0 if after_fraction <= before_fraction else (
                (fraction - before_fraction) / (after_fraction - before_fraction)
            )
            before, after = points[point_idx - 1], points[point_idx]
            prefix.append([
                _lerp(before[0], after[0], local_fraction),
                _lerp(before[1], after[1], local_fraction),
            ])
            break
        return prefix

    def _reconstruct_history(
        self,
        j: ActiveJourney,
        origin_arrival: float,
        full_segments: int,
        budget: float,
        dwell: float,
        run: float,
        *,
        partial_segment_fraction: float | None = None,
    ) -> None:
        """Write schedule-derived origin-to-anchor points, all marked estimated."""
        origin = j.leg.stations[0]
        self._emit_at(j, origin.lat, origin.lon, int(origin_arrival), estimated=True)
        for station_idx in range(full_segments):
            arrived_at = origin_arrival + station_idx * budget
            departed_at = arrived_at + dwell
            station = j.leg.stations[station_idx]
            # Arrival and later departure preserve an explicit station pause.
            self._emit_at(j, station.lat, station.lon, int(departed_at), estimated=True)
            geometry = self._segment_geometry(j, station_idx)
            for point, distance_fraction in zip(geometry, _distance_fractions(geometry)):
                self._emit_at(
                    j, point[0], point[1], int(departed_at + run * distance_fraction), estimated=True,
                )
        if partial_segment_fraction is not None:
            station_idx = full_segments
            arrived_at = origin_arrival + station_idx * budget
            departed_at = arrived_at + dwell
            station = j.leg.stations[station_idx]
            self._emit_at(j, station.lat, station.lon, int(departed_at), estimated=True)
            geometry = self._segment_geometry(j, station_idx, fraction=partial_segment_fraction)
            for point, distance_fraction in zip(geometry, _distance_fractions(geometry)):
                self._emit_at(
                    j, point[0], point[1],
                    int(departed_at + run * partial_segment_fraction * distance_fraction), estimated=True,
                )

    async def alight(self) -> None:
        """Manual override: user says they got off at the leg's end station."""
        j = self._require_active()
        if j.state != JourneyState.ON_TRAIN:
            raise ValueError(f"cannot alight in state {j.state}")
        await self._complete_leg(j)

    async def missed_train(self) -> None:
        """Back to the platform picker without losing the journey."""
        j = self._require_active()
        self._stop_tracker()
        j.state = JourneyState.AWAITING_BOARD
        j.train_no = None
        j.tracking_mode = None
        self.db.update_journey(j.id, state=j.state, train_no=None, tracking_mode=None)

    async def cancel(self) -> None:
        j = self._require_active()
        self._stop_tracker()
        self._stop_push()
        j.state = JourneyState.CANCELLED
        self.db.update_journey(j.id, state=j.state)
        self.active = None

    async def retry_push(self) -> None:
        j = self._require_active()
        if j.state != JourneyState.PUSH_FAILED:
            raise ValueError(f"nothing to retry in state {j.state}")
        self._start_push(j)

    async def retry_debug_push(self, journey_id: int) -> None:
        """Send retained points for an eligible terminal debug-history record."""
        row = self.db.get_journey(journey_id)
        if row is None:
            raise ValueError("journey not found")
        state = JourneyState(row["state"])
        if state not in {JourneyState.CANCELLED, JourneyState.PUSH_FAILED}:
            raise ValueError(f"nothing to retry in state {state}")
        if self._push_task and not self._push_task.done():
            raise ValueError("another Reitti transfer is already in progress")
        if self.active and self.active.id != journey_id:
            raise ValueError("another journey is active")
        if self.active:
            await self.retry_push()
            return

        # Cancelled records have no active tracker. Rebuild just enough state
        # to transfer their retained trace without resuming the old ride.
        j = ActiveJourney(row["id"], self.db.load_itinerary(row), row["current_leg_idx"])
        j.state = state
        self._start_push(j)

    # -- tracking loop -------------------------------------------------------

    def _start_tracker(self) -> None:
        self._stop_tracker()
        self._task = asyncio.create_task(self._track_loop())

    def _stop_tracker(self) -> None:
        task, self._task = self._task, None
        # never cancel the task we're currently running inside (leg completion
        # is triggered from within the loop itself)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _stop_push(self) -> None:
        task, self._push_task = self._push_task, None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _track_loop(self) -> None:
        try:
            while True:
                j = self.active
                if not j or j.state != JourneyState.ON_TRAIN:
                    return
                try:
                    await self._tick(j)
                except SubwayApiError as e:
                    log.warning("subway feed error, keeping last anchor: %s", e)
                except Exception:
                    log.exception("tracker tick failed")
                if not j or j.state != JourneyState.ON_TRAIN:
                    return
                await asyncio.sleep(self.settings.poll_interval_seconds)
        except asyncio.CancelledError:
            pass

    def _next_poll_delay(self, j: ActiveJourney) -> float:
        """Adaptive Seoul position polling interval.

        Use POLL_INTERVAL_SECONDS as the fast cadence around station events,
        but slow down while the train is cruising between stations because the
        Seoul feed only reports coarse station-relative states.
        """
        fast = self.settings.poll_interval_seconds
        if fast <= 0 or j.tracking_mode != "realtime":
            return fast
        if j.missing_since is not None:
            return min(MISSING_TRAIN_RETRY_SECONDS, CRUISE_POLL_SECONDS)
        if j.anchor_phase != "segment" or j.anchor_idx >= len(j.leg.stations) - 1:
            return fast

        elapsed = time.time() - j.anchor_time
        segment_seconds = self._segment_seconds(j)
        station_window = self._station_poll_window_seconds(segment_seconds)
        remaining = segment_seconds - elapsed
        if remaining <= station_window:
            return fast
        return min(CRUISE_POLL_SECONDS, max(fast, remaining - station_window))

    def _segment_seconds(self, j: ActiveJourney) -> float:
        seg_count = max(len(j.leg.stations) - 1, 1)
        return max(j.leg.section_time / seg_count, 30) if j.leg.section_time else DEFAULT_SEGMENT_SECONDS

    def _station_poll_window_seconds(self, segment_seconds: float) -> float:
        return min(
            MAX_STATION_POLL_WINDOW_SECONDS,
            max(MIN_STATION_POLL_WINDOW_SECONDS, segment_seconds * STATION_POLL_WINDOW_FRACTION),
        )

    async def _tick(self, j: ActiveJourney) -> None:
        if j.tracking_mode == "realtime":
            now = time.time()
            if j.next_seoul_poll_at and now < j.next_seoul_poll_at:
                self._local_realtime_update(j, now)
                return
            found = await self._realtime_update(j)
            if not found:
                now = time.time()
                j.lost_polls += 1
                if j.missing_since is None:
                    j.missing_since = now
                missing_for = now - j.missing_since
                log.debug(
                    "journey %s: train %s not in position feed (lost_polls=%d missing_for=%.1fs/%.1fs)",
                    j.id, j.train_no, j.lost_polls, missing_for, LOST_TRAIN_FALLBACK_SECONDS,
                )
                if missing_for >= LOST_TRAIN_FALLBACK_SECONDS:
                    log.warning("train %s lost, falling back to timer", j.train_no)
                    j.tracking_mode = "timer"
                    j.missing_since = None
                    self.db.update_journey(j.id, tracking_mode="timer")
            else:
                j.lost_polls = 0
                j.missing_since = None
            if j.state == JourneyState.ON_TRAIN and j.tracking_mode == "realtime":
                j.next_seoul_poll_at = time.time() + self._next_poll_delay(j)
        if j.state != JourneyState.ON_TRAIN:
            return  # leg completed inside the update
        if j.tracking_mode == "timer":
            await self._timer_update(j)

    def _local_realtime_update(self, j: ActiveJourney, now: float) -> None:
        """Refresh the displayed train position between Seoul API polls.

        The frontend polls our local snapshot every few seconds. Keep that
        local display moving via interpolation, but do not call Seoul until the
        adaptive Seoul poll deadline is due.
        """
        if not j.last_status or j.anchor_phase != "segment":
            return
        lat, lon, _ = self._interpolate(j, now)
        j.last_status = TrainStatus(
            train_no=j.last_status.train_no,
            station_name=j.last_status.station_name,
            # A "between" position is interpreted by the client as travelling
            # toward station_index. The old departure index made the first
            # segment look like it started before the leg, so the map fell
            # back to its coarse straight-line coordinate.
            station_index=min(j.anchor_idx + 1, len(j.leg.stations) - 1),
            status="between",
            lat=lat,
            lon=lon,
            updated_at=int(now),
        )

    async def _realtime_update(self, j: ActiveJourney) -> bool:
        """Poll the subway feed for the boarded train; write the travelled
        path on each station arrival; complete the leg at the end station.
        Returns False when the train is absent from the feed entirely."""
        result = await locate_train(self.settings.subway_api_url, j.leg, j.train_no)
        now = time.time()
        if result is None:
            log.debug("journey %s: train_no=%s not found on line=%s", j.id, j.train_no, j.leg.line_key)
            return False

        if result.leg_index is None:
            # Train hasn't reached our boarding station yet (user waiting on
            # the platform) — or it already ran past the whole leg.
            log.debug(
                "journey %s: %s not resolvable within leg (anchor_idx=%s anchor_phase=%s)",
                j.id, result.station_name, j.anchor_idx, j.anchor_phase,
            )
            if j.anchor_phase == "segment" and j.anchor_idx >= len(j.leg.stations) - 2:
                await self._complete_leg(j)
                return True
            j.last_status = TrainStatus(
                train_no=j.train_no, station_name=result.station_name,
                station_index=None, status="before_leg",
                lat=j.leg.stations[0].lat, lon=j.leg.stations[0].lon,
                updated_at=int(now),
            )
            return True

        idx = result.leg_index
        if result.status in ("approaching", "arrived"):
            new_idx, new_phase, status = idx, "station", result.status
            reached = idx
        else:  # "departed"
            new_idx, new_phase, status = idx, "segment", "departed"
            reached = idx
        log.debug(
            "journey %s: idx=%d status=%s -> new_idx=%d new_phase=%s reached=%d logged_idx=%d",
            j.id, idx, result.status, new_idx, new_phase, reached, j.logged_idx,
        )

        if reached > j.logged_idx:
            self._log_segment(j, reached, now)

        if (new_idx, new_phase) != (j.anchor_idx, j.anchor_phase):
            log.debug("journey %s: anchor %s/%s -> %s/%s", j.id, j.anchor_idx, j.anchor_phase, new_idx, new_phase)
            j.anchor_idx, j.anchor_phase, j.anchor_time = new_idx, new_phase, now

        lat, lon, _ = self._interpolate(j, now)
        j.last_status = TrainStatus(
            train_no=j.train_no, station_name=result.station_name,
            station_index=idx, status=status, lat=lat, lon=lon, updated_at=int(now),
        )

        arrived_at_end = idx >= len(j.leg.stations) - 1 and result.status in ("approaching", "arrived")
        if arrived_at_end:
            log.debug("journey %s: reached end station idx=%d, completing leg", j.id, idx)
            await self._complete_leg(j)
        return True

    def _log_segment(self, j: ActiveJourney, to_idx: int, now: float) -> None:
        """Write the path from the last logged station to to_idx, following the
        leg's linestring geometry, with timestamps spread over the ride time."""
        to_idx = min(to_idx, len(j.leg.stations) - 1)
        if to_idx <= j.logged_idx:
            log.debug(
                "journey %s: _log_segment no-op, to_idx=%d <= logged_idx=%d",
                j.id, to_idx, j.logged_idx,
            )
            return
        if j.shape_idx:
            a, b = j.shape_idx[j.logged_idx], j.shape_idx[to_idx]
            pts = j.leg.shape[a : b + 1]
        else:  # no geometry from Tmap: fall back to straight station hops
            a, b = None, None
            pts = [[s.lat, s.lon] for s in j.leg.stations[j.logged_idx : to_idx + 1]]

        start_t = j.last_arrival_time or now
        span = max(now - start_t, 1.0)
        # points table is keyed by whole-second timestamps: never write more
        # points than there are seconds in the segment
        max_pts = max(min(len(pts), int(span)), 2)
        if len(pts) > max_pts:
            step = (len(pts) - 1) / (max_pts - 1)
            pts = [pts[round(i * step)] for i in range(max_pts)]
        log.debug(
            "journey %s: _log_segment logged_idx=%d->to_idx=%d shape_range=%s..%s raw_pts=%d written_pts=%d span=%.1fs",
            j.id, j.logged_idx, to_idx, a, b, len(pts), max(len(pts) - (1 if j.logged_idx > 0 else 0), 0), span,
        )

        n = len(pts)
        fractions = _distance_fractions(pts)
        for i, (lat, lon) in enumerate(pts):
            if i == 0 and j.logged_idx > 0:
                continue  # departure point was already written as the previous arrival
            ts = int(start_t + span * fractions[i])
            self._emit_at(j, lat, lon, ts, estimated=i < n - 1)
        j.logged_idx = to_idx
        j.last_arrival_time = now

    def _log_transfer_walk(self, j: ActiveJourney, prev_leg_idx: int, now: float) -> None:
        """Write the Tmap WALK linestring between two subway legs."""
        prev_leg = j.itinerary.legs[prev_leg_idx]
        pts = prev_leg.transfer_walk_shape
        if not pts:
            return

        if j.transfer_started_at is None or j.transfer_started_at > now:
            span = max(float(prev_leg.transfer_walk_time), 1.0)
            start_t = now - span
        else:
            start_t = j.transfer_started_at
            span = max(now - start_t, 1.0)

        max_pts = max(min(len(pts), int(span)), 2)
        if len(pts) > max_pts:
            step = (len(pts) - 1) / (max_pts - 1)
            pts = [pts[round(i * step)] for i in range(max_pts)]

        log.debug(
            "journey %s: transfer walk after leg %d raw_pts=%d written_pts=%d span=%.1fs",
            j.id, prev_leg_idx, len(prev_leg.transfer_walk_shape), max(len(pts) - 1, 0), span,
        )
        fractions = _distance_fractions(pts)
        for i, (lat, lon) in enumerate(pts):
            if i == 0:
                continue  # alight point was already written as the previous leg end
            ts = int(start_t + span * fractions[i])
            self._emit_at(j, lat, lon, ts, estimated=True, leg_idx=prev_leg_idx)

    def _interpolate(self, j: ActiveJourney, now: float) -> tuple[float, float, bool]:
        stations = j.leg.stations
        a = stations[j.anchor_idx]
        if j.anchor_phase == "station" or j.anchor_idx >= len(stations) - 1:
            return a.lat, a.lon, False
        b = stations[j.anchor_idx + 1]
        seg_time = self._segment_seconds(j)
        f = min((now - j.anchor_time) / seg_time, MAX_SEGMENT_FRACTION)
        log.debug(
            "journey %s: interpolate anchor_idx=%d %s->%s seg_time=%.1fs elapsed=%.1fs f=%.3f",
            j.id, j.anchor_idx, a.name, b.name, seg_time, now - j.anchor_time, f,
        )
        return _lerp(a.lat, b.lat, f), _lerp(a.lon, b.lon, f), True

    async def _timer_update(self, j: ActiveJourney) -> None:
        """No realtime data: advance along the leg by schedule time, logging
        each station's arrival as its estimated time passes."""
        stations = j.leg.stations
        now = time.time()
        total = max(j.leg.section_time, 60)
        elapsed = now - (j.leg_started_at or now)
        progress = elapsed / total
        log.debug(
            "journey %s: timer_update elapsed=%.1fs total=%.1fs progress=%.3f",
            j.id, elapsed, total, progress,
        )
        if progress >= 1.0:
            last = stations[-1]
            j.last_status = TrainStatus(
                train_no=j.train_no or "-", station_name=last.name,
                station_index=len(stations) - 1, status="estimated",
                lat=last.lat, lon=last.lon, updated_at=int(now),
            )
            await self._complete_leg(j)
            return
        pos = progress * (len(stations) - 1)
        idx = min(int(pos), len(stations) - 2)
        if idx > j.logged_idx:
            self._log_segment(j, idx, now)
        f = pos - idx
        a, b = stations[idx], stations[idx + 1]
        lat, lon = _lerp(a.lat, b.lat, f), _lerp(a.lon, b.lon, f)
        j.last_status = TrainStatus(
            train_no=j.train_no or "-", station_name=a.name, station_index=idx,
            status="estimated", lat=lat, lon=lon, updated_at=int(now),
        )

    def _emit(self, j: ActiveJourney, lat: float, lon: float, estimated: bool) -> None:
        self._emit_at(j, lat, lon, int(time.time()), estimated)

    def _emit_at(
        self,
        j: ActiveJourney,
        lat: float,
        lon: float,
        ts: int,
        estimated: bool,
        leg_idx: int | None = None,
    ) -> None:
        self.db.add_point(
            j.id, j.leg_idx if leg_idx is None else leg_idx,
            TrackPoint(lat=lat, lon=lon, ts=ts, estimated=estimated),
        )

    # -- leg / journey completion ---------------------------------------------

    async def _complete_leg(self, j: ActiveJourney) -> None:
        if j.state != JourneyState.ON_TRAIN:
            return
        # write any path not yet logged (arrival poll for the end station may
        # have been missed), then pin the final point at the alight station
        self._log_segment(j, len(j.leg.stations) - 1, time.time())
        end = j.leg.stations[-1]
        self._emit(j, end.lat, end.lon, estimated=False)
        self._stop_tracker()
        if j.is_last_leg:
            self._start_push(j)
            return
        j.transfer_started_at = time.time()
        j.leg_idx += 1
        j.state = JourneyState.AWAITING_BOARD
        j.train_no = None
        j.tracking_mode = None
        j.last_status = None
        self.db.update_journey(
            j.id, state=j.state, current_leg_idx=j.leg_idx,
            train_no=None, tracking_mode=None,
        )
        log.info("journey %s: transfer, now awaiting leg %s", j.id, j.leg_idx)

    def _start_push(self, j: ActiveJourney, *, resume: bool = False) -> None:
        if self._push_task and not self._push_task.done():
            return
        points = self.db.get_points(j.id)
        if not resume:
            j.transfer_sent_points = 0
            j.transfer_total_points = len(points)
        else:
            j.transfer_sent_points = min(j.transfer_sent_points or 0, len(points))
            j.transfer_total_points = len(points)
        j.state = JourneyState.PUSHING
        j.error = None
        j.error_reason = None
        j.error_sent_points = None
        j.error_total_points = None
        self.db.update_journey(
            j.id,
            state=j.state,
            error=None,
            error_reason=None,
            error_sent_points=None,
            error_total_points=None,
            transfer_sent_points=j.transfer_sent_points,
            transfer_total_points=j.transfer_total_points,
        )
        self._push_task = asyncio.create_task(self._push_to_reitti(j))

    async def _record_push_progress(self, j: ActiveJourney, sent_in_attempt: int) -> None:
        sent_points = min(
            (j.transfer_attempt_base_sent_points or 0) + sent_in_attempt,
            j.transfer_total_points or 0,
        )
        j.transfer_sent_points = sent_points
        self.db.update_journey(j.id, transfer_sent_points=sent_points)

    async def _push_to_reitti(self, j: ActiveJourney) -> None:
        points = self.db.get_points(j.id)
        if j.transfer_total_points is None:
            j.transfer_sent_points = 0
            j.transfer_total_points = len(points)
        base_sent_points = min(j.transfer_sent_points or 0, len(points))
        j.transfer_attempt_base_sent_points = base_sent_points
        points_to_push = points[base_sent_points:]
        try:
            if not self.settings.reitti_url or not self.settings.reitti_token:
                raise ReittiError(
                    "REITTI_URL / REITTI_TOKEN not configured",
                    reason="configuration",
                )
            sent = await push_points(
                self.settings.reitti_url,
                self.settings.reitti_token,
                points_to_push,
                on_progress=lambda sent_in_attempt: self._record_push_progress(j, sent_in_attempt),
            )
            j.state = JourneyState.COMPLETED
            j.error = None
            j.error_reason = None
            j.transfer_sent_points = base_sent_points + sent
            j.transfer_total_points = len(points)
            self.db.update_journey(
                j.id,
                state=j.state,
                error=None,
                error_reason=None,
                error_sent_points=None,
                error_total_points=None,
                transfer_sent_points=j.transfer_sent_points,
                transfer_total_points=j.transfer_total_points,
            )
            log.info("journey %s complete, %s points pushed to Reitti", j.id, sent)
        except ReittiError as e:
            j.state = JourneyState.PUSH_FAILED
            j.error = str(e)
            j.error_reason = e.reason
            j.error_sent_points = base_sent_points + e.sent_points
            j.error_total_points = len(points)
            j.transfer_sent_points = j.error_sent_points
            j.transfer_total_points = len(points)
            self.db.update_journey(
                j.id,
                state=j.state,
                error=str(e),
                error_reason=e.reason,
                error_sent_points=j.error_sent_points,
                error_total_points=len(points),
                transfer_sent_points=j.transfer_sent_points,
                transfer_total_points=j.transfer_total_points,
            )
            log.error("journey %s: Reitti push failed: %s", j.id, e)

    # -- helpers ---------------------------------------------------------------

    def _require_active(self) -> ActiveJourney:
        if not self.active:
            raise ValueError("no active journey")
        return self.active

    def snapshot(self) -> dict | None:
        j = self.active
        if not j:
            return None
        leg = j.leg
        transfer: dict[str, object] | None = None
        if j.state in (JourneyState.PUSHING, JourneyState.COMPLETED, JourneyState.PUSH_FAILED):
            total_points = j.transfer_total_points
            if total_points is None:
                total_points = self.db.point_count(j.id)
            sent_points = min(j.transfer_sent_points or 0, total_points)
            transfer = {
                "sent_points": sent_points,
                "total_points": total_points,
                "remaining_points": max(total_points - sent_points, 0),
                "progress_percent": 100 if total_points == 0 else round(sent_points / total_points * 100),
            }
        if j.state == JourneyState.PUSH_FAILED:
            reason = j.error_reason or "unknown"
            if transfer is None:
                transfer = {}
            transfer.update({
                "reason": reason,
                "message": TRANSFER_FAILURE_MESSAGES.get(reason, TRANSFER_FAILURE_MESSAGES["unknown"]),
                "detail": j.error or "Reitti transfer failed",
                "can_retry": True,
            })
        return {
            "journey_id": j.id,
            "state": j.state,
            "leg_idx": j.leg_idx,
            "leg_count": len(j.itinerary.legs),
            "leg": {
                "route": leg.route,
                "line_key": leg.line_key,
                "start": leg.start_name,
                "end": leg.end_name,
                "covered": leg.line_key is not None,
                "stations": [s.model_dump() for s in leg.stations],
                "shape": leg.shape,
                "transfer_walk_shape": leg.transfer_walk_shape,
                "transfer_walk_time": leg.transfer_walk_time,
            },
            "summary": j.itinerary.summary,
            "train": j.last_status.model_dump() if j.last_status else None,
            "tracking_mode": j.tracking_mode,
            "history_estimated": j.history_estimated,
            "point_count": self.db.point_count(j.id),
            "error": j.error,
            "transfer": transfer,
            "trip": {
                "legs": [
                    {
                        "route": trip_leg.route,
                        "start": trip_leg.start_name,
                        "end": trip_leg.end_name,
                        "stations": [s.model_dump() for s in trip_leg.stations],
                        "shape": trip_leg.shape,
                        "transfer_walk_shape": trip_leg.transfer_walk_shape,
                    }
                    for trip_leg in j.itinerary.legs
                ],
            },
        }
