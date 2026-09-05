import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import get_settings
from .db import Database
from .journey import JourneyManager
from .notifications import NotificationSender
from .observability import configure_observability
from .stations import StationRegistry

def configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(name)s %(message)s",
    )
    # httpx logs full request URLs at INFO, and the Seoul Open API key is part
    # of the URL path. Keep application logs at LOG_LEVEL while preventing API
    # keys from being shipped to Loki through dependency request logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


configure_logging()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def mount_static(app: FastAPI, static_dir: Path = STATIC_DIR) -> None:
    """Mount the static export after API routes so /api always has priority."""
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    app.state.stations = StationRegistry.from_csv(settings.stations_csv)
    db = Database(settings.db_path)
    app.state.manager = JourneyManager(
        db, settings, notification_sender=NotificationSender(db, settings),
    )
    app.state.manager.resume_from_db()
    logging.getLogger(__name__).info(
        "loaded %s stations", len(app.state.stations.stations)
    )
    try:
        yield
    finally:
        await app.state.manager.shutdown()
        if app.state.observability:
            app.state.observability.shutdown()


app = FastAPI(title="subway-tracker", lifespan=lifespan)
app.state.settings = get_settings()
app.state.observability = configure_observability(app, app.state.settings)
app.include_router(router)
mount_static(app)
