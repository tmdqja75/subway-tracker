"""SQLite persistence. Single-user scale: sync sqlite3 with short transactions."""

import json
import sqlite3
import time
from pathlib import Path

from .models import Itinerary, TrackPoint
from .stations import normalize_name

ROUTE_OPTIONS_CACHE_FORMAT_VERSION = "4"

SCHEMA = """
CREATE TABLE IF NOT EXISTS journeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    state TEXT NOT NULL,
    itinerary_json TEXT NOT NULL,
    current_leg_idx INTEGER NOT NULL DEFAULT 0,
    train_no TEXT,
    tracking_mode TEXT,           -- realtime | timer
    leg_started_at INTEGER,
    history_estimated INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    error_reason TEXT,
    error_sent_points INTEGER,
    error_total_points INTEGER,
    transfer_sent_points INTEGER,
    transfer_total_points INTEGER
);
CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id INTEGER NOT NULL REFERENCES journeys(id),
    leg_idx INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    ts INTEGER NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    UNIQUE(journey_id, ts)
);
CREATE TABLE IF NOT EXISTS route_options_cache (
    start_name TEXT NOT NULL,
    start_line TEXT NOT NULL,
    end_name TEXT NOT NULL,
    end_line TEXT NOT NULL,
    itineraries_json TEXT NOT NULL,
    raw_response_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (start_name, start_line, end_name, end_line)
);
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        journey_columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(journeys)")
        }
        for column, column_type in (
            ("error_reason", "TEXT"),
            ("error_sent_points", "INTEGER"),
            ("error_total_points", "INTEGER"),
            ("transfer_sent_points", "INTEGER"),
            ("transfer_total_points", "INTEGER"),
            ("history_estimated", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in journey_columns:
                self.conn.execute(f"ALTER TABLE journeys ADD COLUMN {column} {column_type}")

        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(route_options_cache)")
        }
        if "raw_response_json" not in columns:
            self.conn.execute(
                "ALTER TABLE route_options_cache ADD COLUMN raw_response_json TEXT"
            )
        self.conn.commit()

        cache_version = self.conn.execute(
            "SELECT value FROM app_meta WHERE key = ?",
            ("route_options_cache_format_version",),
        ).fetchone()
        if (
            cache_version is None
            or cache_version["value"] != ROUTE_OPTIONS_CACHE_FORMAT_VERSION
        ):
            self.conn.execute("DELETE FROM route_options_cache")
            self.conn.execute(
                "INSERT INTO app_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("route_options_cache_format_version", ROUTE_OPTIONS_CACHE_FORMAT_VERSION),
            )
            self.conn.commit()

    def create_journey(self, itinerary: Itinerary, state: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO journeys (created_at, state, itinerary_json, history_estimated) VALUES (?, ?, ?, 0)",
            (int(time.time()), state, itinerary.model_dump_json()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_journey(self, journey_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE journeys SET {cols} WHERE id = ?",
            (*fields.values(), journey_id),
        )
        self.conn.commit()

    def get_active_journey(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM journeys WHERE state IN ('awaiting_board', 'on_train', 'pushing', 'push_failed') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def get_journey(self, journey_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM journeys WHERE id = ?", (journey_id,)
        ).fetchone()

    def load_itinerary(self, row: sqlite3.Row) -> Itinerary:
        return Itinerary.model_validate(json.loads(row["itinerary_json"]))

    def route_history(self) -> tuple[
        list[tuple[str, str, str, str]], list[tuple[str, str, str, str]]
    ]:
        """Return the most-used and most-recent distinct persisted subway routes.

        most_used only counts journeys actually taken. recent also counts
        merely-searched routes (route_options_cache), so it reflects both
        finished travel and simply searched travel.

        Itinerary JSON is historical data and may predate current validation, so
        malformed rows are ignored rather than making the history endpoint fail.
        """
        groups: dict[tuple[str, str, str, str], dict[str, int]] = {}
        rows = self.conn.execute(
            "SELECT id, created_at, itinerary_json FROM journeys"
        ).fetchall()
        for row in rows:
            try:
                itinerary = json.loads(row["itinerary_json"])
                legs = itinerary["legs"]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if not isinstance(legs, list):
                continue
            created_at, journey_id = row["created_at"], row["id"]
            if type(created_at) is not int or type(journey_id) is not int:
                continue

            # Tmap persists every trackable transit mode as a SubwayLeg. A
            # non-empty line_key is the durable marker for an actual subway leg.
            subway_legs = [
                leg
                for leg in legs
                if isinstance(leg, dict)
                and isinstance(leg.get("line_key"), str)
                and leg["line_key"].strip()
            ]
            if not subway_legs:
                continue
            first, last = subway_legs[0], subway_legs[-1]
            start_name, start_line = first.get("start_name"), first.get("line_key")
            end_name, end_line = last.get("end_name"), last.get("line_key")
            if not (
                isinstance(start_name, str)
                and start_name.strip()
                and isinstance(start_line, str)
                and start_line.strip()
                and isinstance(end_name, str)
                and end_name.strip()
                and isinstance(end_line, str)
                and end_line.strip()
            ):
                continue

            route = (start_name, start_line, end_name, end_line)
            used_at = (created_at, journey_id)
            group = groups.setdefault(
                route,
                {"count": 0, "latest_created_at": created_at, "latest_id": journey_id},
            )
            group["count"] += 1
            if used_at > (group["latest_created_at"], group["latest_id"]):
                group["latest_created_at"], group["latest_id"] = used_at

        ranked = sorted(
            groups.items(),
            key=lambda item: (
                -item[1]["count"],
                -item[1]["latest_created_at"],
                -item[1]["latest_id"],
            ),
        )

        # recent: same routes as above keyed by normalized name (Tmap's raw
        # leg names and the registry-normalized cache keys can differ in
        # "역" suffix/whitespace for the same physical station), merged with
        # route_options_cache so a search alone also counts as "recent".
        recent_candidates: dict[tuple[str, str, str, str], tuple[int, int]] = {}
        recent_routes: dict[tuple[str, str, str, str], tuple[str, str, str, str]] = {}

        def consider(norm_key, event_at: tuple[int, int], route) -> None:
            if event_at > recent_candidates.get(norm_key, (0, 0)):
                recent_candidates[norm_key] = event_at
                recent_routes[norm_key] = route

        for route, group in groups.items():
            start_name, start_line, end_name, end_line = route
            norm_key = (
                normalize_name(start_name), start_line,
                normalize_name(end_name), end_line,
            )
            consider(norm_key, (group["latest_created_at"], group["latest_id"]), route)

        for row in self.conn.execute(
            "SELECT start_name, start_line, end_name, end_line, updated_at "
            "FROM route_options_cache"
        ):
            route = (row["start_name"], row["start_line"], row["end_name"], row["end_line"])
            norm_key = (
                normalize_name(row["start_name"]), row["start_line"],
                normalize_name(row["end_name"]), row["end_line"],
            )
            # cache rows have no journey id to tie-break with; 0 only wins
            # ties against another cache row, never against a journey.
            consider(norm_key, (row["updated_at"], 0), route)

        recent = sorted(
            recent_candidates.items(),
            key=lambda item: (-item[1][0], -item[1][1]),
        )
        return (
            [route for route, _ in ranked],
            [recent_routes[norm_key] for norm_key, _ in recent],
        )

    def get_cached_route_options(
        self,
        start_name: str,
        start_line: str,
        end_name: str,
        end_line: str,
    ) -> list[Itinerary] | None:
        row = self.conn.execute(
            "SELECT itineraries_json FROM route_options_cache "
            "WHERE start_name = ? AND start_line = ? AND end_name = ? AND end_line = ?",
            (start_name, start_line, end_name, end_line),
        ).fetchone()
        if row is None:
            return None
        return [Itinerary.model_validate(item) for item in json.loads(row["itineraries_json"])]

    def cache_route_options(
        self,
        start_name: str,
        start_line: str,
        end_name: str,
        end_line: str,
        itineraries: list[Itinerary],
        raw_tmap_response: str | None = None,
    ) -> None:
        now = int(time.time())
        payload = json.dumps(
            [itinerary.model_dump(mode="json") for itinerary in itineraries],
            ensure_ascii=False,
        )
        self.conn.execute(
            "INSERT INTO route_options_cache "
            "(start_name, start_line, end_name, end_line, itineraries_json, raw_response_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(start_name, start_line, end_name, end_line) DO UPDATE SET "
            "itineraries_json = excluded.itineraries_json, "
            "raw_response_json = excluded.raw_response_json, "
            "updated_at = excluded.updated_at",
            (start_name, start_line, end_name, end_line, payload, raw_tmap_response, now, now),
        )
        self.conn.commit()

    def touch_cached_route_options(
        self, start_name: str, start_line: str, end_name: str, end_line: str
    ) -> None:
        """Bump a cache-hit search's recency so repeat searches still count as recent."""
        self.conn.execute(
            "UPDATE route_options_cache SET updated_at = ? "
            "WHERE start_name = ? AND start_line = ? AND end_name = ? AND end_line = ?",
            (int(time.time()), start_name, start_line, end_name, end_line),
        )
        self.conn.commit()

    def add_point(self, journey_id: int, leg_idx: int, p: TrackPoint) -> None:
        # one point per second; the latest write wins (an arrival point emitted
        # right after a tick point must not be dropped)
        self.conn.execute(
            "INSERT INTO points (journey_id, leg_idx, lat, lon, ts, estimated) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(journey_id, ts) DO UPDATE SET "
            "lat = excluded.lat, lon = excluded.lon, estimated = excluded.estimated",
            (journey_id, leg_idx, p.lat, p.lon, p.ts, int(p.estimated)),
        )
        self.conn.commit()

    def get_points(self, journey_id: int, leg_idx: int | None = None) -> list[TrackPoint]:
        if leg_idx is None:
            query = "SELECT lat, lon, ts, estimated FROM points WHERE journey_id = ? ORDER BY ts"
            params = (journey_id,)
        else:
            query = (
                "SELECT lat, lon, ts, estimated FROM points "
                "WHERE journey_id = ? AND leg_idx = ? ORDER BY ts"
            )
            params = (journey_id, leg_idx)
        rows = self.conn.execute(query, params).fetchall()
        return [
            TrackPoint(lat=r["lat"], lon=r["lon"], ts=r["ts"], estimated=bool(r["estimated"]))
            for r in rows
        ]

    def list_debug_journeys(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM journeys ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        journeys = []
        for row in rows:
            itinerary = self.load_itinerary(row)
            point_rows = self.conn.execute(
                "SELECT leg_idx, lat, lon, ts, estimated FROM points "
                "WHERE journey_id = ? ORDER BY ts",
                (row["id"],),
            ).fetchall()
            journeys.append(
                {
                    "journey_id": row["id"],
                    "created_at": row["created_at"],
                    "state": row["state"],
                    "can_retry": row["state"] in {"cancelled", "push_failed"},
                    "current_leg_idx": row["current_leg_idx"],
                    "train_no": row["train_no"],
                    "tracking_mode": row["tracking_mode"],
                    "summary": itinerary.summary,
                    "legs": [
                        {
                            "idx": i,
                            "route": leg.route,
                            "line_key": leg.line_key,
                            "start": leg.start_name,
                            "end": leg.end_name,
                            "stations": [s.model_dump() for s in leg.stations],
                            "shape": leg.shape,
                            "transfer_walk_shape": leg.transfer_walk_shape,
                            "transfer_walk_time": leg.transfer_walk_time,
                        }
                        for i, leg in enumerate(itinerary.legs)
                    ],
                    "points": [
                        {
                            "leg_idx": p["leg_idx"],
                            "lat": p["lat"],
                            "lon": p["lon"],
                            "ts": p["ts"],
                            "estimated": bool(p["estimated"]),
                        }
                        for p in point_rows
                    ],
                }
            )
        return journeys

    def point_count(self, journey_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM points WHERE journey_id = ?", (journey_id,)
        ).fetchone()[0]
