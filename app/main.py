import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import get_settings
from .db import Database
from .journey import JourneyManager
from .stations import StationRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.stations = StationRegistry.from_csv(settings.stations_csv)
    db = Database(settings.db_path)
    app.state.manager = JourneyManager(db, settings)
    app.state.manager.resume_from_db()
    logging.getLogger(__name__).info(
        "loaded %s stations", len(app.state.stations.stations)
    )
    yield
    app.state.manager._stop_tracker()


app = FastAPI(title="subway-tracker", lifespan=lifespan)
app.include_router(router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
