# Live Map Progress Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the live map, color the route from the leg start to the train icon in the line's color over a gray full route, and make a train stopped at a station face along the route.

**Architecture:** All changes are in `frontend/components/maps/live-journey-map.tsx`. A new pure function computes the traveled polyline from the same placement code that positions the marker. A second Leaflet polyline is created once per map and updated with `setLatLngs` in `syncTrainMarker`. The station-heading fallback in `headingForLiveTrain` now reads the route shape instead of drawing a straight line to the next station.

**Tech Stack:** Next.js / React, Leaflet, Vitest + Testing Library (Leaflet is mocked in the test file).

**Spec:** `docs/superpowers/specs/2026-11-18-live-map-progress-trace-design.md`

## Global Constraints

- Traveled color: `getLineColor(leg.line_key)` from `frontend/lib/line-colors.ts`. Unknown lines use `LINE_COLOR_FALLBACK` (`#5B6570`), which is what `getLineColor` already returns.
- Untraveled (full route) color: light gray `#C9CED4`. This replaces the navy `#183f70` on the live map only.
- `completed-journey-map.tsx` is not touched.
- No new dependencies. Run all commands from `frontend/`.

## Review Focus

1. Leg with no valid Tmap shape (station-coordinate fallback geometry): the trace must still end at the icon. Test in Task 2.
2. The train disappears from the feed (`train` becomes `null`): the trace must clear, not stay frozen. Test in Task 2.
3. The fixture's `line_key: "2"` is not a real key, so it must render in the fallback gray rather than throw. A real key like `"2호선"` must render `#00A84D`. Test in Task 2.
4. The shape has a duplicate point at a station (a zero-length segment): the heading must use the next distinct point, not return `undefined`. Test in Task 1.
5. The train is stopped at the last station: it faces the direction it arrived from, not north. Test in Task 1.

---

### Task 1: Station heading follows the route shape

**Files:**
- Modify: `frontend/components/maps/live-journey-map.tsx` (`headingForLiveTrain`, around lines 183-197)
- Test: `frontend/components/maps/live-journey-map.test.tsx` (replace the test "faces an arrived train toward the next station instead of defaulting north")

**Interfaces:**
- Produces: `headingForLiveTrain(leg, train): number | undefined`, with the same signature as today.

- [ ] **Step 1: Replace the existing heading test with the failing test**

```ts
it("faces a train stopped at a station along the route shape, not the straight line to the next station", () => {
  const leg: Pick<JourneyLegSnapshot, "shape" | "stations"> = {
    ...activeJourneySnapshot.leg,
    stations: [
      { index: 0, name: "출발", lat: 0, lon: 0 },
      { index: 1, name: "정차", lat: 0, lon: 10 },
      { index: 2, name: "도착", lat: 0, lon: 20 },
    ],
    // Leaves station 1 heading due north, and reaches station 2 heading due south.
    shape: [[0, 0], [0, 5], [0, 10], [0, 10], [5, 10], [5, 20], [0, 20]] as Coordinate[],
  };
  const at = (station_index: number | null, status: string, lon: number) => ({
    ...activeJourneySnapshot.train!, station_index, status, lat: 0, lon,
  });

  expect(headingForLiveTrain(leg, at(1, "arrived", 10))).toBeCloseTo(0, 5);      // was 90 (chord to next station); duplicate [0,10] is skipped
  expect(headingForLiveTrain(leg, at(1, "approaching", 10))).toBeCloseTo(0, 5);
  expect(headingForLiveTrain(leg, at(2, "arrived", 20))).toBeCloseTo(180, 5);    // last station: direction it arrived from (was undefined -> north)
  expect(headingForLiveTrain(leg, at(null, "before_leg", 0))).toBeCloseTo(90, 5);
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `npx vitest run components/maps/live-journey-map.test.tsx -t "along the route shape"`
Expected: FAIL. The first assertion gets ~90 instead of 0.

- [ ] **Step 3: Change the fallback in `headingForLiveTrain`**

Keep the first branch, which uses `resolveLiveTrainPlacement(...)?.heading`. Replace the fallback that runs when that branch has no heading:
- `stationIndex = train.station_index ?? 0`. Return `undefined` if it is outside `[0, leg.stations.length - 1]`.
- `geometry = geometryForLiveLeg(leg)`, and `i = closestShapeIndex(geometry, [station.lat, station.lon])`.
- Look forward from `i` for the first point that differs from `geometry[i]`. If one exists, return `bearingDegrees(geometry[i], thatPoint)`.
- Otherwise, look backward from `i` for the first point that differs, and return `bearingDegrees(thatPoint, geometry[i])`.
- Otherwise, return `undefined`.

Update the JSDoc above the function so it describes the route-tangent behavior.

- [ ] **Step 4: Run the file's tests**

Run: `npx vitest run components/maps/live-journey-map.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add components/maps/live-journey-map.tsx components/maps/live-journey-map.test.tsx
git commit -m "fix: face a train stopped at a station along the route shape"
```

---

### Task 2: Line-colored traveled trace

**Files:**
- Modify: `frontend/components/maps/live-journey-map.tsx`
- Test: `frontend/components/maps/live-journey-map.test.tsx`

**Interfaces:**
- Consumes: `getLineColor` from `../../lib/line-colors`.
- Produces: an exported `traveledPathForLiveTrain(leg: Pick<JourneyLegSnapshot, "shape" | "stations">, train: TrainStatus | null): Coordinate[]`. It returns `[]` when nothing should be colored.

- [ ] **Step 1: Write the failing unit test for the pure function**

```ts
it("ends the traveled path at the train icon: moving, stopped, and not located", () => {
  const leg: Pick<JourneyLegSnapshot, "shape" | "stations"> = {
    ...activeJourneySnapshot.leg,
    stations: [
      { index: 0, name: "출발", lat: 0, lon: 0 },
      { index: 1, name: "정차", lat: 0, lon: 10 },
      { index: 2, name: "도착", lat: 0, lon: 20 },
    ],
    shape: [[0, 0], [5, 5], [0, 10], [5, 15], [0, 20]] as Coordinate[],
  };
  const train = (station_index: number | null, status: string, lat: number, lon: number) => ({
    ...activeJourneySnapshot.train!, station_index, status, lat, lon,
  });

  // Moving: shape points up to the segment start, then exactly the marker position.
  const moving = train(2, "between", 0, 12.5); // a quarter of the way along station 1 -> 2, inside the first shape segment
  expect(traveledPathForLiveTrain(leg, moving)).toEqual([[0, 0], [5, 5], [0, 10], positionForLiveTrain(leg, moving)]);
  // Stopped at a station: shape up to that station (the marker sits on it).
  expect(traveledPathForLiveTrain(leg, train(1, "arrived", 0, 10))).toEqual([[0, 0], [5, 5], [0, 10], [0, 10]]);
  // Not located / before the boarding station.
  expect(traveledPathForLiveTrain(leg, train(null, "before_leg", 0, 0))).toEqual([]);
  expect(traveledPathForLiveTrain(leg, null)).toEqual([]);
  // No shape: falls back to station geometry and still ends at the marker.
  const noShape = { ...leg, shape: [] };
  const movingNoShape = train(2, "between", 0, 14);
  expect(traveledPathForLiveTrain(noShape, movingNoShape)).toEqual([[0, 0], [0, 10], [0, 14]]);
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `npx vitest run components/maps/live-journey-map.test.tsx -t "traveled path"`
Expected: FAIL, because `traveledPathForLiveTrain` is not exported.

- [ ] **Step 3: Implement `traveledPathForLiveTrain`**

- Make `pointAndHeadingAtDistanceFraction` also return `segmentIndex`, the index in its input of the start of the segment the point lands on. For the end-of-line case, use `geometry.length - 2`. In `resolveLiveTrainPlacement`, return `shapeIndex = shapeStartIndex + segmentIndex` alongside `position` and `heading`.
- In `traveledPathForLiveTrain`:
  - Return `[]` if `!hasTrainPosition(train)` or `train.station_index === null`.
  - Set `geometry = geometryForLiveLeg(leg)` and `position = positionForLiveTrain(leg, train)`.
  - Find the anchor index:
    - If the placement resolved, use `placement.shapeIndex`.
    - Otherwise, find the anchor station: `station_index - 1` for `"between"`, else `station_index`. Use `closestShapeIndex(geometry, anchorStation)`. Return `[]` if the anchor station is out of range.
  - Return `[...geometry.slice(0, anchorIndex + 1), position]`.

- [ ] **Step 4: Run it and confirm it passes**

Run: `npx vitest run components/maps/live-journey-map.test.tsx -t "traveled path"`
Expected: PASS.

- [ ] **Step 5: Write the failing render test, and update the mock and lifecycle test**

- In the Leaflet mock, add `setLatLngs: vi.fn()` to each polyline instance and to its type.
- In "mounts only through the client effect…", each map now creates two polylines: the first map gets instances `[0]` and `[1]`, the second gets `[2]` and `[3]`. After the leg change, assert that `[0]` and `[1]` were each removed once. After unmount, assert that `[2]` and `[3]` were each removed once. This replaces the current `[1]` assertion.
- Add this test:

```ts
it("draws a gray full route under a line-colored trace that follows the train and clears when it disappears", async () => {
  const leg = { ...activeJourneySnapshot.leg, line_key: "2호선" };
  const { rerender } = render(<LiveJourneyMap journeyLegKey="1:0" leg={leg} train={activeJourneySnapshot.train} />);

  await waitFor(() => expect(leaflet.polyline).toHaveBeenCalledTimes(2));
  expect(leaflet.polyline).toHaveBeenNthCalledWith(1, leg.shape, expect.objectContaining({ color: "#C9CED4" }));
  expect(leaflet.polyline).toHaveBeenNthCalledWith(2, [], expect.objectContaining({ color: "#00A84D" }));
  const trace = leaflet.polylineInstances[1];
  expect(trace.setLatLngs).toHaveBeenLastCalledWith(traveledPathForLiveTrain(leg, activeJourneySnapshot.train));

  const moved = { ...activeJourneySnapshot.train!, lat: 37.499, lon: 127.03 };
  rerender(<LiveJourneyMap journeyLegKey="1:0" leg={leg} train={moved} />);
  await waitFor(() => expect(trace.setLatLngs).toHaveBeenLastCalledWith(traveledPathForLiveTrain(leg, moved)));

  rerender(<LiveJourneyMap journeyLegKey="1:0" leg={leg} train={null} />);
  await waitFor(() => expect(trace.setLatLngs).toHaveBeenLastCalledWith([]));
});

it("uses the neutral fallback color for an unknown line key", async () => {
  render(<LiveJourneyMap journeyLegKey="1:0" leg={activeJourneySnapshot.leg} train={activeJourneySnapshot.train} />);
  await waitFor(() => expect(leaflet.polyline).toHaveBeenCalledTimes(2));
  expect(leaflet.polyline).toHaveBeenNthCalledWith(2, [], expect.objectContaining({ color: "#5B6570" }));
});
```

- [ ] **Step 6: Run them and confirm they fail**

Run: `npx vitest run components/maps/live-journey-map.test.tsx`
Expected: the two new tests and the lifecycle test fail. They see one polyline per map, and the color is `#183f70`.

- [ ] **Step 7: Render the two layers**

- Add `traveledLayer: (LeafletLayer & { setLatLngs: (path: Coordinate[]) => unknown }) | null` to `LeafletResource`. Add `polyline` to its `leaflet` type, and initialize the field to `null`.
- In setup, create the route layer first with `{ color: "#C9CED4", opacity: 0.9, weight: 5 }`, keeping the existing `geometry.length >= 2` guard. Then always create `resource.traveledLayer = leaflet.polyline([], { color: getLineColor(leg.line_key), opacity: 0.95, weight: 6 })` and add it to the map. It is created after the route layer so it draws on top.
- In `syncTrainMarker`, first call `resource.traveledLayer?.setLatLngs(traveledPathForLiveTrain(leg, train))`. Do this before the early return for a null train, so the trace clears.
- In `cleanupResource`, remove `traveledLayer` in its own try/catch, the same way `routeLayer` is removed.

- [ ] **Step 8: Run the full frontend checks**

Run: `npm test && npm run typecheck`
Expected: all PASS and no type errors.

- [ ] **Step 9: Commit**

```bash
git add components/maps/live-journey-map.tsx components/maps/live-journey-map.test.tsx
git commit -m "feat: paint the traveled part of the live route in the line color"
```
