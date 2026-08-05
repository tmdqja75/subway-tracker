from pathlib import Path


def test_debug_page_keeps_its_standalone_assets_and_diagnostic_markup():
    debug_html = Path("static/debug.html").read_text()
    debug_js = Path("static/debug.js").read_text()

    assert 'href="debug.css"' in debug_html
    assert 'src="debug.js"' in debug_html
    assert Path("static/debug.css").is_file()
    assert Path("static/debug.js").is_file()

    for markup in (
        'id="journey-select"',
        'id="debug-refresh"',
        'id="debug-retry-reitti"',
        'id="debug-status"',
        'id="debug-map"',
        'id="debug-timeline"',
        'id="debug-summary"',
        'id="debug-legend"',
    ):
        assert markup in debug_html

    assert "journey.can_retry" in debug_js
    assert 'fetch(`/api/debug/journeys/${journey.journey_id}/retry-push`' in debug_js
    assert "window.confirm" in debug_js
