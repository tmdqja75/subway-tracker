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
import time

from .config import Settings
from .db import Database
from .models import Itinerary, JourneyState, SubwayLeg, TrackPoint, TrainStatus
from .reitti import ReittiError, push_points
from .seoul import SeoulApiError, fetch_positions
from .stations import normalize_name

log = logging.getLogger(__name__)

LOST_POLL_LIMIT = 4  # consecutive polls without the train before timer fallback
MAX_SEGMENT_FRACTION = 0.92  # never claim arrival from interpolation alone
DEFAULT_SEGMENT_SECONDS = 120


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


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
        # interpolation anchor: index of segment start station, and whether the
        # train was last seen stopped at it (phase "station") or running past
        # it (phase "segment")
        self.anchor_idx = 0
        self.anchor_phase = "station"
        self.anchor_time = 0.0
        self.lost_polls = 0
        self.last_status: TrainStatus | None = None
        self.error: str | None = None
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
        self.logged_idx = 0
        self.last_arrival_time = time.time()
        self.shape_idx = _station_shape_indices(self.leg)

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
        self.active = j
        if j.state == JourneyState.ON_TRAIN:
            j.prepare_leg()  # stale after restart; re-syncs on the next poll
            self._start_tracker()
        log.info("resumed journey %s in state %s", j.id, j.state)

    async def start_journey(self, itinerary: Itinerary) -> ActiveJourney:
        if self.active:
            await self.cancel()
        journey_id = self.db.create_journey(itinerary, JourneyState.AWAITING_BOARD)
        self.active = ActiveJourney(journey_id, itinerary)
        return self.active

    async def board(self, train_no: str | None) -> None:
        j = self._require_active()
        if j.state != JourneyState.AWAITING_BOARD:
            raise ValueError(f"cannot board in state {j.state}")
        now = int(time.time())
        j.train_no = train_no
        j.tracking_mode = "realtime" if (train_no and j.leg.line_key) else "timer"
        j.state = JourneyState.ON_TRAIN
        j.leg_started_at = now
        j.prepare_leg()
        # user is standing on the platform: log the boarding station now
        start = j.leg.stations[0]
        self._emit(j, start.lat, start.lon, estimated=False)
        self.db.update_journey(
            j.id,
            state=j.state,
            train_no=train_no,
            tracking_mode=j.tracking_mode,
            leg_started_at=now,
        )
        self._start_tracker()

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
        j.state = JourneyState.CANCELLED
        self.db.update_journey(j.id, state=j.state)
        self.active = None

    async def retry_push(self) -> None:
        j = self._require_active()
        if j.state != JourneyState.PUSH_FAILED:
            raise ValueError(f"nothing to retry in state {j.state}")
        await self._push_to_reitti(j)

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

    async def _track_loop(self) -> None:
        try:
            while True:
                j = self.active
                if not j or j.state != JourneyState.ON_TRAIN:
                    return
                try:
                    await self._tick(j)
                except SeoulApiError as e:
                    log.warning("seoul api error, keeping last anchor: %s", e)
                except Exception:
                    log.exception("tracker tick failed")
                if not j or j.state != JourneyState.ON_TRAIN:
                    return
                await asyncio.sleep(self.settings.poll_interval_seconds)
        except asyncio.CancelledError:
            pass

    async def _tick(self, j: ActiveJourney) -> None:
        if j.tracking_mode == "realtime":
            found = await self._realtime_update(j)
            if not found:
                j.lost_polls += 1
                if j.lost_polls >= LOST_POLL_LIMIT:
                    log.warning("train %s lost, falling back to timer", j.train_no)
                    j.tracking_mode = "timer"
                    self.db.update_journey(j.id, tracking_mode="timer")
            else:
                j.lost_polls = 0
        if j.state != JourneyState.ON_TRAIN:
            return  # leg completed inside the update
        if j.tracking_mode == "timer":
            await self._timer_update(j)

    async def _realtime_update(self, j: ActiveJourney) -> bool:
        """Poll the position API; write the travelled path on each station
        arrival; complete the leg at the end station.
        Returns False when the train number is absent from the feed."""
        positions = await fetch_positions(self.settings.seoul_api_key, j.leg.line_key)
        train = next((p for p in positions if p.get("trainNo") == j.train_no), None)
        if train is None:
            return False

        names = [normalize_name(s.name) for s in j.leg.stations]
        statn = normalize_name(train.get("statnNm", ""))
        sttus = train.get("trainSttus", "")
        now = time.time()

        if statn not in names:
            # Train hasn't reached our boarding station yet (user waiting on the
            # platform) — or it already ran past the whole leg.
            if j.anchor_phase == "segment" and j.anchor_idx >= len(names) - 2:
                await self._complete_leg(j)
                return True
            j.last_arrival_time = now  # clock starts when we leave the platform
            j.last_status = TrainStatus(
                train_no=j.train_no, station_name=train.get("statnNm", "?"),
                station_index=None, status="before_leg",
                lat=j.leg.stations[0].lat, lon=j.leg.stations[0].lon,
                updated_at=int(now),
            )
            return True

        idx = names.index(statn)
        # trainSttus: 0 approaching, 1 arrived, 2 departed, 3 departed previous
        if sttus in ("0", "1"):
            new_idx, new_phase, status = idx, "station", ("approaching" if sttus == "0" else "arrived")
            reached = idx  # arrival at idx
        elif sttus == "2":
            new_idx, new_phase, status = idx, "segment", "departed"
            reached = idx  # we may have missed the arrival poll for idx
        else:  # "3": running toward statn -> in segment before idx
            new_idx, new_phase, status = max(idx - 1, 0), "segment", "between"
            reached = idx - 1

        if reached > j.logged_idx:
            self._log_segment(j, reached, now)

        if (new_idx, new_phase) != (j.anchor_idx, j.anchor_phase):
            j.anchor_idx, j.anchor_phase, j.anchor_time = new_idx, new_phase, now

        lat, lon, _ = self._interpolate(j, now)
        j.last_status = TrainStatus(
            train_no=j.train_no, station_name=train.get("statnNm", "?"),
            station_index=idx, status=status, lat=lat, lon=lon, updated_at=int(now),
        )

        arrived_at_end = idx >= len(names) - 1 and sttus in ("0", "1")
        if arrived_at_end:
            await self._complete_leg(j)
        return True

    def _log_segment(self, j: ActiveJourney, to_idx: int, now: float) -> None:
        """Write the path from the last logged station to to_idx, following the
        leg's linestring geometry, with timestamps spread over the ride time."""
        to_idx = min(to_idx, len(j.leg.stations) - 1)
        if to_idx <= j.logged_idx:
            return
        if j.shape_idx:
            a, b = j.shape_idx[j.logged_idx], j.shape_idx[to_idx]
            pts = j.leg.shape[a : b + 1]
        else:  # no geometry from Tmap: fall back to straight station hops
            pts = [[s.lat, s.lon] for s in j.leg.stations[j.logged_idx : to_idx + 1]]

        start_t = j.last_arrival_time or now
        span = max(now - start_t, 1.0)
        # points table is keyed by whole-second timestamps: never write more
        # points than there are seconds in the segment
        max_pts = max(min(len(pts), int(span)), 2)
        if len(pts) > max_pts:
            step = (len(pts) - 1) / (max_pts - 1)
            pts = [pts[round(i * step)] for i in range(max_pts)]

        n = len(pts)
        for i, (lat, lon) in enumerate(pts):
            if i == 0 and j.logged_idx > 0:
                continue  # departure point was already written as the previous arrival
            ts = int(start_t + span * (i / (n - 1))) if n > 1 else int(now)
            self._emit_at(j, lat, lon, ts, estimated=i < n - 1)
        j.logged_idx = to_idx
        j.last_arrival_time = now

    def _interpolate(self, j: ActiveJourney, now: float) -> tuple[float, float, bool]:
        stations = j.leg.stations
        a = stations[j.anchor_idx]
        if j.anchor_phase == "station" or j.anchor_idx >= len(stations) - 1:
            return a.lat, a.lon, False
        b = stations[j.anchor_idx + 1]
        seg_count = max(len(stations) - 1, 1)
        seg_time = max(j.leg.section_time / seg_count, 30) if j.leg.section_time else DEFAULT_SEGMENT_SECONDS
        f = min((now - j.anchor_time) / seg_time, MAX_SEGMENT_FRACTION)
        return _lerp(a.lat, b.lat, f), _lerp(a.lon, b.lon, f), True

    async def _timer_update(self, j: ActiveJourney) -> None:
        """No realtime data: advance along the leg by schedule time, logging
        each station's arrival as its estimated time passes."""
        stations = j.leg.stations
        now = time.time()
        total = max(j.leg.section_time, 60)
        elapsed = now - (j.leg_started_at or now)
        progress = elapsed / total
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

    def _emit_at(self, j: ActiveJourney, lat: float, lon: float, ts: int, estimated: bool) -> None:
        self.db.add_point(
            j.id, j.leg_idx,
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
            await self._push_to_reitti(j)
            return
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

    async def _push_to_reitti(self, j: ActiveJourney) -> None:
        points = self.db.get_points(j.id)
        try:
            if not self.settings.reitti_url or not self.settings.reitti_token:
                raise ReittiError("REITTI_URL / REITTI_TOKEN not configured")
            sent = await push_points(self.settings.reitti_url, self.settings.reitti_token, points)
            j.state = JourneyState.COMPLETED
            j.error = None
            self.db.update_journey(j.id, state=j.state, error=None)
            log.info("journey %s complete, %s points pushed to Reitti", j.id, sent)
        except ReittiError as e:
            j.state = JourneyState.PUSH_FAILED
            j.error = str(e)
            self.db.update_journey(j.id, state=j.state, error=str(e))
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
            },
            "summary": j.itinerary.summary,
            "train": j.last_status.model_dump() if j.last_status else None,
            "tracking_mode": j.tracking_mode,
            "point_count": self.db.point_count(j.id),
            "error": j.error,
        }
