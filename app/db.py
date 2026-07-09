"""SQLite persistence. Single-user scale: sync sqlite3 with short transactions."""

import json
import sqlite3
import time
from pathlib import Path

from .models import Itinerary, TrackPoint

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
    error TEXT
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
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def create_journey(self, itinerary: Itinerary, state: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO journeys (created_at, state, itinerary_json) VALUES (?, ?, ?)",
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
            "SELECT * FROM journeys WHERE state IN ('awaiting_board', 'on_train') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def get_journey(self, journey_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM journeys WHERE id = ?", (journey_id,)
        ).fetchone()

    def load_itinerary(self, row: sqlite3.Row) -> Itinerary:
        return Itinerary.model_validate(json.loads(row["itinerary_json"]))

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

    def get_points(self, journey_id: int) -> list[TrackPoint]:
        rows = self.conn.execute(
            "SELECT lat, lon, ts, estimated FROM points WHERE journey_id = ? ORDER BY ts",
            (journey_id,),
        ).fetchall()
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
