import pytest

from app import journey as journey_mod


@pytest.fixture(autouse=True)
def no_live_subway_feed(monkeypatch):
    """Keep platform polling off the network in every test.

    Each real feed request is a live Seoul Open API call. Only the name
    `app.journey` imported is replaced, so the respx tests of
    `subway_feed.fetch_line_snapshot` still exercise the real parser. A test
    that needs particular snapshots patches `journey_mod.fetch_line_snapshot`
    again; the later patch wins. Yields the base URLs that were "fetched".
    """
    calls: list[str] = []

    async def fake_fetch_line_snapshot(base_url, line_id):
        calls.append(base_url)
        return []

    monkeypatch.setattr(journey_mod, "fetch_line_snapshot", fake_fetch_line_snapshot)
    yield calls
