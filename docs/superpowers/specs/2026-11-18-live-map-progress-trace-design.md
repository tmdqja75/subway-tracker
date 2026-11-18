# Live map: progress trace + station heading

**Why:** see trip progress at a glance on the live map.

## Progress trace
- Draw the whole leg in light gray. On top, draw a line in the line's color
  (`getLineColor(leg.line_key)`) from the leg start to the train icon.
- Trim the colored line on every train update (no map rebuild).
- Where the colored line ends:
  - moving between stations: the computed point along the route
  - stopped at / approaching a station: that station
  - not located yet, or before the boarding station: nothing colored
  - line with no known color: the darker neutral gray (`LINE_COLOR_FALLBACK`)

## Heading at stations
- At a station, face the train along the route's direction leaving that station.
- At the last station, face it along the direction it arrived from.
- Replaces the straight line to the next station and the north default.

## Tests
- Update the heading test to expect the route direction, including the last station.
- Add a test that the colored piece ends at the train: moving, at a station, not located.

## Not in scope
- Completed-journey map; drawing the recorded positions.

## Approaches
- **Chosen:** a two-line overlay (gray full route + colored piece) trimmed on each update.
- Rejected: a gradient polyline (Leaflet can't draw one without a plugin);
  redrawing the map on each update (flickers, resets zoom).
