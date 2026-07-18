from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as production_app
from app.main import mount_static


def test_production_api_routes_precede_root_static_mount():
    """The live app must not let the root static mount shadow `/api/*` routes."""
    static_mount_index = next(
        index
        for index, route in enumerate(production_app.routes)
        if getattr(route, "name", None) == "static"
        and getattr(route, "path", None) in {"", "/"}
    )
    def contains_api_route(route) -> bool:
        if getattr(route, "path", "").startswith("/api/"):
            return True
        return any(
            getattr(api_route, "path", "").startswith("/api/")
            for api_route in getattr(
                getattr(route, "original_router", None), "routes", ()
            )
        )

    api_route_indexes = [
        index
        for index, route in enumerate(production_app.routes)
        if contains_api_route(route)
    ]

    assert api_route_indexes
    assert max(api_route_indexes) < static_mount_index


def test_static_export_debug_assets_and_api_route_coexist(tmp_path):
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "index.html").write_text("<main>Next rider app</main>")
    (tmp_path / "_next" / "static" / "app.js").write_text("console.log('next')")
    (tmp_path / "debug.html").write_text("<main>Debug view</main>")

    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"source": "api"}

    mount_static(app, tmp_path)
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    assert "Next rider app" in root.text

    next_asset = client.get("/_next/static/app.js")
    assert next_asset.status_code == 200
    assert next_asset.text == "console.log('next')"

    debug = client.get("/debug.html")
    assert debug.status_code == 200
    assert "Debug view" in debug.text

    api = client.get("/api/ping")
    assert api.status_code == 200
    assert api.json() == {"source": "api"}
