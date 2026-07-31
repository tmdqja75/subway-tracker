---
name: tabriz-live-train-lookup
description: Find which trains are near a given Korean subway station right now, using tabriz.kr's live line-diagram page (https://tabriz.kr/live/<line>/) via Playwriter DOM inspection rather than an official API. Use it when you need to inspect the ground truth for official train position
metadata:
  category: transit
  method: dom-position-scraping
---

# Tabriz.kr Live Train Lookup (Playwriter DOM method)

## What this solves

tabriz.kr's live page (`/live/<line>/`, e.g. `/live/2호선/`) renders a vertical
line diagram: station names as absolutely-positioned links, and train cards
(headsign/direction + 편성번호 + status) as absolutely-positioned siblings in
the *same* coordinate space. There is no per-station "trains near me" API —
proximity has to be computed from the shared `top` CSS values.

**Trap to avoid:** clicking a station name link navigates to
`/timetable/?line=...&stn=...`, which is the *scheduled* timetable, not live
positions. Don't click into the station — read the live page's DOM directly.

## Method

1. Load the `playwriter` skill and bind to the existing tab if the user
   already has the page open — don't spawn a fresh tab:
   ```js
   state.page = context.pages()[0] // or context.pages().find(p => p.url().includes('tabriz.kr'))
   ```
   If nothing is open, navigate: `await state.page.goto('https://tabriz.kr/live/2호선/', { waitUntil: 'domcontentloaded' })`.

2. Get the shared coordinate scale — every station link inside the diagram
   container has an inline `style.top` in `rem`:
   ```js
   const stations = await state.page.evaluate(() => {
     const c = document.querySelector('.flex.justify-center.items-start.flex-wrap.w-full')
     return [...c.querySelectorAll('a[href*=stn]')].map(a => ({
       name: a.textContent.trim(),
       top: parseFloat(a.style.top),
     }))
   })
   const target = stations.find(s => s.name === '선릉').top
   ```

3. Pull every train marker in the same container — they're `div[style*=top]`
   elements that contain an `<img>` (the train-face icon). Concatenated
   `textContent` gives you `<headsign/direction><편성번호><status>` e.g.
   `"209내선순환2112출발"`:
   ```js
   const trains = await state.page.evaluate(() => {
     const c = document.querySelector('.flex.justify-center.items-start.flex-wrap.w-full')
     return [...c.querySelectorAll('div[style*=top]')]
       .filter(d => d.querySelector('img'))
       .map(d => ({ top: parseFloat(d.style.top), text: d.textContent.trim() }))
   })
   ```

4. Rank by distance to the target station and take the closest few:
   ```js
   trains.sort((a, b) => Math.abs(a.top - target) - Math.abs(b.top - target))
   const nearest = trains.slice(0, 5)
   ```
   Two columns exist side by side in the layout (outer loop / inner loop, or
   branch destinations) — both share the same `top` scale, so this single
   sort covers both directions at once. Report each hit's train number,
   direction/destination, 편성번호, and status (도착/출발/진입) as parsed from
   the concatenated text — split on the run of digits vs. status keywords if
   you need the fields separated.

5. Optional visual cross-check: `scrollIntoViewIfNeeded()` on the station
   link, screenshot the page, and eyeball which cards line up horizontally
   with the station's row. Useful for a sanity check or when the user wants
   to *see* it, but the DOM-position math in steps 2-4 is the actual answer
   — don't rely on screenshots alone since a viewport only shows a fraction
   of the full line.

## Caveats

- `top` is a layout coordinate, not a real-world distance — it only encodes
  station *order* and how far along the vertical strip a train sits between
  two adjacent stations. Treat "nearest by `top`" as "nearest by track
  position," not meters or minutes.
- Status labels (도착/출발/진입) are set relative to whichever station the
  train's card is nearest, not necessarily the station you asked about — a
  train reading "도착" a few `rem` above your target may mean it arrived at
  the *previous* station, not yours. Say so when reporting, don't relabel it
  as "approaching your station" unless the position is essentially on top of
  the station's own `top` value.
- If `resizeImageForAgent` fails with `sharp is not installed`, just `Read`
  the raw screenshot PNG directly — no need to install sharp for a one-off
  lookup.
