"""SQLite persistence for durable Web Push notification delivery."""

import sqlite3
import threading
from pathlib import Path

import pytest

from app import db as db_mod
from app.config import Settings
from app.db import Database
from app.notifications import NotificationSender


VAPID_SETTINGS = {
    "web_push_vapid_public_key": "public-key",
    "web_push_vapid_private_key": "private-key",
    "web_push_vapid_subject": "mailto:notifications@example.com",
}
PAYLOAD_FOR_DOGOK = '{"title":"Subway Tracker","body":"다음 역은 도곡이에요. 하차를 준비하세요.","url":"/"}'


class FakePushResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakePushTransport:
    def __init__(self, outcomes: list[object]):
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []
        self.thread_ids: list[int] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.thread_ids.append(threading.get_ident())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def claimed_notification_delivery(db: Database, endpoint: str = "https://push.example/subscription") -> str:
    db.upsert_push_subscription(endpoint, "p256dh-key", "auth-key")
    claimed = db.claim_journey_leg_notification(41, 2)
    assert len(claimed) == 1
    return endpoint


def delivery_row(db: Database, endpoint: str):
    row = db.conn.execute(
        "SELECT state, attempts, last_error FROM journey_leg_notification_deliveries "
        "WHERE journey_id = ? AND leg_idx = ? AND endpoint = ?",
        (41, 2, endpoint),
    ).fetchone()
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_sender_uses_utf8_payload_vapid_claims_and_background_thread(tmp_path: Path):
    db = Database(tmp_path / "tracker.db")
    endpoint = claimed_notification_delivery(db)
    transport = FakePushTransport([FakePushResponse(201)])
    sender = NotificationSender(
        db,
        Settings(_env_file=None, **VAPID_SETTINGS),
        transport=transport,
        retry_delay_seconds=0,
    )

    await sender.send_claimed_deliveries(41, 2, "도곡")

    assert transport.calls == [{
        "subscription_info": {
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
        },
        "data": PAYLOAD_FOR_DOGOK,
        "vapid_private_key": "private-key",
        "vapid_claims": {"sub": "mailto:notifications@example.com"},
    }]
    assert transport.thread_ids[0] != threading.get_ident()
    assert dict(delivery_row(db, endpoint)) == {
        "state": "sent",
        "attempts": 1,
        "last_error": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_sender_deletes_permanently_invalid_subscriptions(tmp_path: Path, status: int):
    db = Database(tmp_path / "tracker.db")
    endpoint = claimed_notification_delivery(db)
    sender = NotificationSender(
        db,
        Settings(_env_file=None, **VAPID_SETTINGS),
        transport=FakePushTransport([FakePushResponse(status)]),
        retry_delay_seconds=0,
    )

    await sender.send_claimed_deliveries(41, 2, "도곡")

    assert db.has_push_subscriptions() is False
    assert dict(delivery_row(db, endpoint)) == {
        "state": "failed",
        "attempts": 1,
        "last_error": f"web push response status {status}",
    }


@pytest.mark.asyncio
async def test_sender_retries_transient_statuses_up_to_the_fixed_budget(tmp_path: Path):
    db = Database(tmp_path / "tracker.db")
    endpoint = claimed_notification_delivery(db)
    transport = FakePushTransport([
        FakePushResponse(503),
        FakePushResponse(429),
        FakePushResponse(201),
    ])
    sender = NotificationSender(
        db,
        Settings(_env_file=None, **VAPID_SETTINGS),
        transport=transport,
        retry_budget=3,
        retry_delay_seconds=0,
    )

    await sender.send_claimed_deliveries(41, 2, "도곡")

    assert len(transport.calls) == 3
    assert dict(delivery_row(db, endpoint)) == {
        "state": "sent",
        "attempts": 3,
        "last_error": None,
    }


@pytest.mark.asyncio
async def test_sender_contains_network_failure_and_records_exhaustion(tmp_path: Path):
    db = Database(tmp_path / "tracker.db")
    endpoint = claimed_notification_delivery(db)
    transport = FakePushTransport([TimeoutError("endpoint must not leak")] * 3)
    sender = NotificationSender(
        db,
        Settings(_env_file=None, **VAPID_SETTINGS),
        transport=transport,
        retry_budget=3,
        retry_delay_seconds=0,
    )

    await sender.send_claimed_deliveries(41, 2, "도곡")

    assert len(transport.calls) == 3
    assert dict(delivery_row(db, endpoint)) == {
        "state": "failed",
        "attempts": 3,
        "last_error": "web push network failure",
    }
    assert endpoint not in str(delivery_row(db, endpoint)["last_error"])


def test_notification_tables_migrate_existing_tracker_database(tmp_path: Path):
    path = tmp_path / "pre-notifications.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE journeys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            state TEXT NOT NULL,
            itinerary_json TEXT NOT NULL,
            current_leg_idx INTEGER NOT NULL DEFAULT 0,
            train_no TEXT,
            tracking_mode TEXT,
            leg_started_at INTEGER
        );
        CREATE TABLE points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            journey_id INTEGER NOT NULL,
            leg_idx INTEGER NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            ts INTEGER NOT NULL,
            estimated INTEGER NOT NULL DEFAULT 0,
            UNIQUE(journey_id, ts)
        );
        CREATE TABLE route_options_cache (
            start_name TEXT NOT NULL,
            start_line TEXT NOT NULL,
            end_name TEXT NOT NULL,
            end_line TEXT NOT NULL,
            itineraries_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (start_name, start_line, end_name, end_line)
        );
        CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.close()

    db = Database(path)

    tables = {
        row["name"]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "push_subscriptions",
        "journey_leg_notifications",
        "journey_leg_notification_deliveries",
    } <= tables
    assert {
        row["name"]
        for row in db.conn.execute("PRAGMA table_info(journey_leg_notification_deliveries)")
    } == {"journey_id", "leg_idx", "endpoint", "state", "attempts", "last_error", "updated_at"}


def test_push_subscription_upsert_refreshes_without_logging_secrets(tmp_path: Path, monkeypatch, caplog):
    db = Database(tmp_path / "tracker.db")
    endpoint = "https://push.example/subscription"
    monkeypatch.setattr(db_mod.time, "time", lambda: 100)

    db.upsert_push_subscription(endpoint, "initial-p256dh", "initial-auth")

    monkeypatch.setattr(db_mod.time, "time", lambda: 200)
    db.upsert_push_subscription(endpoint, "refreshed-p256dh", "refreshed-auth")

    subscriptions = db.list_push_subscriptions()
    assert db.has_push_subscriptions() is True
    assert len(subscriptions) == 1
    assert dict(subscriptions[0]) == {
        "endpoint": endpoint,
        "p256dh": "refreshed-p256dh",
        "auth": "refreshed-auth",
    }
    timestamps = db.conn.execute(
        "SELECT created_at, updated_at FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
    ).fetchone()
    assert dict(timestamps) == {"created_at": 100, "updated_at": 200}
    assert all(
        secret not in record.getMessage()
        for secret in (endpoint, "refreshed-p256dh", "refreshed-auth")
        for record in caplog.records
    )


def test_notification_claim_is_one_time_and_snapshots_current_subscriptions(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "tracker.db")
    monkeypatch.setattr(db_mod.time, "time", lambda: 100)
    db.upsert_push_subscription("https://push.example/one", "key-one", "auth-one")
    db.upsert_push_subscription("https://push.example/two", "key-two", "auth-two")

    claimed = db.claim_journey_leg_notification(42, 3)

    assert [delivery["endpoint"] for delivery in claimed] == [
        "https://push.example/one",
        "https://push.example/two",
    ]
    assert all(delivery["state"] == "pending" and delivery["attempts"] == 0 for delivery in claimed)
    db.upsert_push_subscription("https://push.example/late", "key-late", "auth-late")
    assert db.claim_journey_leg_notification(42, 3) == []
    assert [delivery["endpoint"] for delivery in db.get_pending_notification_deliveries(42, 3)] == [
        "https://push.example/one",
        "https://push.example/two",
    ]
    notification = db.conn.execute(
        "SELECT state, created_at FROM journey_leg_notifications WHERE journey_id = ? AND leg_idx = ?",
        (42, 3),
    ).fetchone()
    assert notification is not None
    assert tuple(notification) == ("claimed", 100)


def test_delivery_helpers_track_attempts_results_and_remove_permanent_subscription(tmp_path: Path):
    db = Database(tmp_path / "tracker.db")
    endpoint = "https://push.example/permanent"
    db.upsert_push_subscription(endpoint, "key", "auth")
    db.claim_journey_leg_notification(7, 1)

    db.record_notification_delivery_attempt(7, 1, endpoint, last_error="temporary timeout")
    db.record_notification_delivery_result(7, 1, endpoint, state="retryable", last_error="temporary timeout")
    retryable = db.get_pending_notification_deliveries(7, 1)
    assert len(retryable) == 1
    assert retryable[0]["attempts"] == 1
    assert retryable[0]["state"] == "retryable"
    assert retryable[0]["last_error"] == "temporary timeout"

    db.record_notification_delivery_attempt(7, 1, endpoint)
    db.record_notification_delivery_result(7, 1, endpoint, state="sent")
    assert db.get_pending_notification_deliveries(7, 1) == []
    assert db.delete_push_subscription(endpoint) is True
    assert db.delete_push_subscription(endpoint) is False
    assert db.has_push_subscriptions() is False
