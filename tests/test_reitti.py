import pytest
import respx
from httpx import Response

from app.models import TrackPoint
from app.reitti import push_points


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
