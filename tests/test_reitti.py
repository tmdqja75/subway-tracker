import httpx
import pytest
import respx
from httpx import Response

from app.models import TrackPoint
from app.reitti import ReittiError, commit_workbench_patch, push_points


@respx.mock
async def test_push_points_reports_each_successful_transfer():
    respx.post("http://reitti.test/api/v1/ingest/owntracks").mock(
        side_effect=[Response(204), Response(200)]
    )
    progress = []

    async def record_progress(sent_points: int):
        progress.append(sent_points)

    sent = await push_points(
        "http://reitti.test",
        "token",
        [
            TrackPoint(lat=37.4980, lon=127.0277, ts=1, estimated=False),
            TrackPoint(lat=37.4766, lon=126.9816, ts=2, estimated=True),
        ],
        on_progress=record_progress,
    )

    assert sent == 2
    assert progress == [1, 2]


@respx.mock
async def test_commit_workbench_patch_sends_expected_patch():
    route = respx.post("http://reitti.test/api/v2/workbench/commit").mock(
        return_value=Response(200, json={"success": True, "message": "ok"})
    )

    await commit_workbench_patch("http://reitti.test", "token", "3", 1_000, 2_000)

    request = route.calls.last.request
    assert request.headers["X-API-Token"] == "token"
    assert request.headers["content-type"] == "application/json"
    body = request.content.decode()
    assert '"deviceId": "3"' in body or '"deviceId":"3"' in body


@respx.mock
async def test_commit_workbench_patch_raises_on_server_rejection():
    respx.post("http://reitti.test/api/v2/workbench/commit").mock(
        return_value=Response(200, json={"success": False, "message": "boom"})
    )

    with pytest.raises(ReittiError, match="boom"):
        await commit_workbench_patch("http://reitti.test", "token", "3", 1_000, 2_000)


@respx.mock
async def test_commit_workbench_patch_raises_on_connection_error():
    respx.post("http://reitti.test/api/v2/workbench/commit").mock(
        side_effect=httpx.ConnectError("boom")
    )

    with pytest.raises(ReittiError):
        await commit_workbench_patch("http://reitti.test", "token", "3", 1_000, 2_000)
