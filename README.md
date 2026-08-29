# Where is Sørlandet? 🚢

A small page for families following the school ship **Sørlandet** (MMSI 257165000):
where she is, where she has sailed, and what the weather and sea are like at her
actual position.

No server to look after. **GitHub Actions** fetches the position every 20 minutes and
stores it in this repository; **GitHub Pages** serves the page. Free, and everyone only
needs one link.

```
index.html                    the page (map + position + weather)
data/latest.json              last position and weather   <- written by the script
data/track.json               every position so far (the track itself)
data/ports.json               the 2026-2027 voyage plan (ports, dates, coordinates)
data/wind.json                wind forecast grid around the ship  <- written by the script
data/waves.json               wave forecast grid around the ship  <- written by the script
data/history.json             one row per day: strongest wind, highest wave  <- written by the script
data/wake.json                hour by hour: where she was and the weather there  <- written by the script
data/course.json              the fastest sailing course and its tacks  <- written by the script
scripts/sailrouter.py         the polar diagram and the isochrone router
scripts/orbithunter.py        finds the satellite passes that covered her
data/orbit.json               those passes, newest first  <- written by the script
share-qr.svg                  QR code for the page address
scripts/make_qr.py            regenerates share-qr.svg if the address changes
scripts/update.py             fetches AIS position + weather
scripts/seed_demo.py          writes sample data so you can preview the page
scripts/backfill_barentswatch.py  pulls dense historic AIS for Norwegian waters
.github/workflows/update.yml  listens for AIS 55 minutes of every hour
vendor/leaflet/               the map library, kept local so the page is self-contained
```

## Setup, in this order (about 15 minutes)

### 1. Get a free AIS key

Go to [aisstream.io](https://aisstream.io), sign in with GitHub, and create an API key
under *Account*. Free, no card required.

### 2. Create the repository

New repository, name it whatever you like, and make it **Public** — free GitHub Pages
only works on public repositories. That is fine here: AIS positions are public
information and the page contains nothing about the students.

### 3. Upload the files — all at once

*Add file → Upload files*, then drag in **the contents** of this folder (not the folder
itself). Do it in a single commit.

Two files will not come along, because macOS Finder and Windows Explorer hide names that
start with a dot. Create them in the browser instead — GitHub creates folders for you
when you type a slash in the name field:

| *Add file → Create new file* → name | Content |
|---|---|
| `.nojekyll` | leave completely empty |
| `.github/workflows/update.yml` | paste from the file in this folder |

`.nojekyll` matters: without it, Pages runs the site through Jekyll, a blog engine this
project does not use, and the build fails.

### 4. Add the key as a secret

*Settings → Secrets and variables → Actions → New repository secret*

| Name | Value |
|---|---|
| `AISSTREAM_API_KEY` | the key from step 1 |

The key belongs here and nowhere else — never in a file in the repository.

### 5. Turn on Pages

*Settings → Pages → Source: Deploy from a branch → Branch `main`, folder `/ (root)` → Save*

After a few minutes the green box at the top shows **Your site is live at …**. That is
the link you share.

### 6. Run it once

*Actions → Update position → Run workflow.* Nothing else starts on its own, so you can
read the log in peace. Open the step *Fetch position and weather*:

| Log line | Meaning |
|---|---|
| `-> position 58.7…, 5.2…` | working, and the ship was within range of a receiver |
| `-> no AIS message in this window` | working, but no receiver heard her. Normal at sea |
| `* no AISSTREAM_API_KEY` | the secret is missing or misspelled |
| `! aisstream returned an error` | the key was rejected — check for stray spaces |

From then on it runs by itself every 20 minutes, and the track grows a little each day.

> **Tip:** open the link on a phone and choose *Add to Home Screen*. It then behaves like
> a small app with its own icon.

## Why a restart used to look like nothing happened

`update.py` spends its whole listening window before it writes a file: it opens both AIS
streams, waits, merges, and only then recalculates the weather, the route and the forecast.
With a 29-minute window that means a restarted run changes nothing on the page for 29
minutes - however fresh the code is. That looked exactly like a bug, twice.

So each run now does a **quick pass first**: one invocation with `LISTEN_SECONDS=20`,
committed as `Refresh`, before the long rounds begin. Twenty seconds is plenty for
everything that does not depend on hearing her - and the ship's own feed is a rolling
window, so even the positions are not really lost. A restart is now visible within a
minute.

## How often it updates

The ship transmits a position every few minutes while she is within reach of a shore
receiver. The limit was never her - it was us: the job used to listen for a couple of
minutes every twenty, and threw the rest away.

GitHub's `schedule` trigger never fired once for this repository, so the run does not
rely on it: each run works for about five hours and then starts its successor through the
API, using a fine-grained token stored as the `DISPATCH_TOKEN` secret. See `START-HERE.txt`.

Each run does eleven rounds of 29 minutes, keeping every distinct position.
Coverage in home waters is therefore close to continuous, and one delayed or dropped
scheduled run costs an hour rather than a whole afternoon. GitHub Actions is free for
public repositories, so the long window costs nothing.

Because the track gets dense, `update.py` thins it as it ages: everything from the last
week at full detail, then one point per 30 minutes for the first month, one per 2 hours
up to four months, one per 6 hours beyond that. A nine-month voyage stays a file a phone
can download in a moment.

### The ship's own feed - the best of the three

The foundation runs its own position page, linked from
[fullriggeren.no](https://en.fullriggeren.no/) under *Follow the ship*. Behind it sits a
plain public JSON endpoint:

```
https://raptor.warrisk.tech/position/5334561      <- the IMO number, not the MMSI
```

It answers with an array of `[timestamp, GeoJSON Point]` pairs: the last **500 fixes**,
about **one every seven minutes**, a rolling window of roughly the last two and a half
days. No key, no rate limit, and no CORS header - so the updater reads it server-side,
not the page.

It is whitelisted to this one ship. Asking for a different vessel answers
`401 {"error":"IMO:… not authorized"}`, which is a good sign: the foundation publishes it
deliberately, for people following Sørlandet.

Two things follow from the rolling window, and they matter more than the density:

* **Nothing is lost between runs.** A run that starts within two days of the last one
  picks up every fix in between. A missed night, a broken chain, a GitHub outage - the
  next run repairs the hole rather than leaving a straight line across it.
* **A fix can belong anywhere in the track**, not only at the end. `update.py` therefore
  merges by timestamp instead of appending, and drops a fix that lands within
  `MIN_GAP_MIN` of one already stored unless the ship has moved.

Speed and course are not published, so they are computed from the step between
neighbouring fixes - which is also how the page can show a course arrow at all when this
is the source that heard her.

Set `FOUNDATION_URL=""` to switch it off.

### Three live sources at once

While the ship is in Norwegian waters the Coastal Administration's own feed is far
denser than a volunteer network. `update.py` therefore listens to **both** for the same
window: BarentsWatch's live SSE stream on a background thread, aisstream on the main one.
At the end of the window it also reads the foundation's rolling list. All three land in
the same pot, keyed by timestamp, so duplicates fold together and whichever source heard
her most wins on its own merit. The page's Position card names the one that supplied the
fix on screen.

BarentsWatch goes quiet the moment she leaves the Norwegian economic zone, and aisstream
carries on alone - no configuration change needed. Add `BW_CLIENT_ID` and
`BW_CLIENT_SECRET` as repository secrets to switch it on; without them the script says so
once and uses aisstream only.

Endpoints used, from the Live AIS API at `https://live.ais.barentswatch.no/live`:

| Path | Used for |
|---|---|
| `/v1/sse/combined` | the 55-minute listening window, filtered to our MMSI |
| `/v1/latest/combined` | one-shot fallback when the window heard nothing |

### Denser history for Norwegian waters

`scripts/backfill_barentswatch.py` pulls the Norwegian Coastal Administration's own AIS
archive through [BarentsWatch](https://developer.barentswatch.no/docs/AIS/) - up to 14
days back, free, and far denser than anything a sampling job can catch. It only covers
the Norwegian economic zone, so it is worth running while she is still in home waters:

```bash
export BW_CLIENT_ID=...        # from My page -> API clients at barentswatch.no
export BW_CLIENT_SECRET=...
python3 scripts/backfill_barentswatch.py 14
```

It merges into `data/track.json`, keeping what is already there.

## What to know about AIS

AIS is the ship's own radio transmitter, and the messages are picked up by **shore-based**
receivers. In practice:

* **Near coast and in port:** a new position almost every run.
* **Mid-Atlantic:** nothing, often for days. That is not a fault. The page says so
  plainly, keeps the last known position, and draws the gap dashed on the map once
  coverage returns.

The longest silence on this voyage will be **13–29 December**, Mindelo to Fernando de
Noronha. Continuous tracking across an ocean requires satellite AIS, which costs money
(Datalastic, MarineTraffic, VesselFinder and others). The script is written so such a
source can be added as an alternative inside `fetch_position_from_aisstream` without
touching anything else.

The foundation also runs its own position page, linked from
[fullriggeren.no](https://en.fullriggeren.no/) under *Follow the ship* — worth comparing
against.

## Running it locally

```bash
python3 scripts/seed_demo.py          # sample data, no key needed
python3 -m http.server 8000           # open http://localhost:8000

pip install -r requirements.txt
export AISSTREAM_API_KEY=your-key
python3 scripts/update.py             # real position + weather
```

Opening `index.html` straight from disk shows an empty page: browsers refuse to read the
`data/*.json` files that way. The small web server above is the fix.

## The route, 2026-2027

Taken from the *2026-2027 Voyage Plan* (A+ World Academy, dated 25 March 2026) and stored
in `data/ports.json`. The page uses it to show the current leg, distance and estimated
arrival, and draws the whole route on the map.

| Port | Dates |
|---|---|
| Kristiansand, Norway | departure 17 Aug 2026 |
| Lerwick, United Kingdom | 7–10 Sep |
| Dublin, Ireland | 16–20 Sep |
| St. Malo, France | 26 Sep – 2 Oct |
| Vigo, Spain | 10–14 Oct |
| Funchal, Madeira | 22–28 Oct |
| Sevilla, Spain | 5–10 Nov |
| Las Palmas, Gran Canaria | 18 Nov – 4 Dec (parent teacher conferences 19 Nov) |
| Mindelo, Cape Verde | 13–17 Dec |
| Fernando de Noronha, Brazil | 29 Dec – 2 Jan |
| Paramaribo, Suriname | 15–20 Jan 2027 |
| Bridgetown, Barbados | 26 Jan – 1 Feb |
| St. Martin, France | 5–9 Feb |
| Charleston, USA | 24 Feb – 2 Mar |
| New York City, USA | 9–24 Mar (parent teacher conferences 10 Mar) |
| Horta, Azores | 14–18 Apr |
| Scheveningen, Netherlands | 2–18 May (AP exams) |
| Skagen, Denmark | 25–28 May |
| Kristiansand, Norway | graduation 29 May 2027 |

The plan is tentative. If dates change, edit `data/ports.json` and the page follows.
Coordinates are approximate port positions — good enough for distance and estimates.

## Wind and waves on the map

Each run also fetches two forecast grids around the ship's position: wind speed and
direction, and wave height, direction and period. Each grid is 5 x 5 points covering a
box of 360 x 360 nautical miles, from 48 hours back to 72 hours ahead in 1-hour steps, and
each is a single API call. They are stored in `data/wind.json` and `data/waves.json`.

The box is measured in nautical miles rather than degrees, so the longitude span widens
with latitude - 11.2 degrees wide in the Skagerrak, 6.0 at the equator - and the spacing
between arrows stays 90 nautical miles wherever the ship is.

There are three layers, and the third costs nothing: the wind grid already calls
Open-Meteo's forecast endpoint, so asking the same request for `weather_code` and
`temperature_2m` adds a **Weather** layer - sky symbol and temperature at each grid point -
without a single extra API call. It starts off, because three glyphs per point is a lot at
once; Wind and Waves start on.

On the map, **wind** is a single arrow, coloured pale blue through green and amber to dark
red as it strengthens. **Waves** are a double chevron with the height in metres beside it,
on a blue-to-purple scale. Both point the way they are travelling, and the two sit on
either side of their grid point so they stay readable together. The slider under the map
steps through the forecast; the two buttons turn each layer on and off. Both start on. It opens on **now**, which
with the axis above puts the handle a little under half way along.

### The grid follows the map

The files above are a fixed box around the ship: dense when you look at the whole North
Sea, sparse when you zoom in on her. So the page also asks Open-Meteo directly, in the
browser, for a 6 x 6 grid covering **what is actually on screen**, whenever the map is
moved or zoomed. Zoom in and the arrows tighten to a few nautical miles apart; zoom out
and they spread again. A small "live grid" mark appears in the bar when that is what you
are looking at.

The files stay the fallback: they render instantly on load, work when Open-Meteo is
unreachable, and are what the page shows if a viewport request fails. Results are cached
per view, and requests are debounced by 0.7 seconds so panning does not hammer the API.

These are forecasts for the surrounding sea, not measurements from on board - the
anemometer on deck will read differently, especially in gusts. Grid size and horizon can
be changed with the environment variables `GRID_CELLS`, `GRID_SPAN_NM`, `GRID_HOURS` and
`GRID_STEP`.

## How far ahead she is assumed to have got

Her speed through the water is the wrong basis for guessing where she will be. A square
rigger beats to windward and runs sail drills: at the 5.5 knots showing on her log she
would "arrive" at Lerwick in two days, when the plan says eleven. The page used to state
that as an arrival date, which read as a prediction and was not one.

So the projection is paced to the **plan** instead:

* the passage clock runs from when she sails to the date the plan says she is due - if she
  is alongside somewhere, it starts at her departure, not today, or a leg beginning in
  three weeks would be spread across the weeks in port as well
* the implied pace is the route length over that passage, and never more than she can
  actually sail - if she is behind schedule, cruising speed is the ceiling
* `ahead.json` records which basis was used, so the card can say so rather than leaving
  the reader to guess

For the leg to Lerwick that works out at about 0.9 knots along the route: not her speed
through the water, but the rate at which she is actually closing on Shetland while sailing
back and forth. The forecast ladder reaches 6, 12, 24, 36, 48, 72, 96, 120, 144 and 168
hours out, trimmed to the leg, and stops at seven days because Open-Meteo's marine model
does.

Two things were removed for the same reason: **At current speed** in the Voyage plan card,
and the exact predicted arrival hour in the rewind panel. The distance still to run is
honest. A date derived from this minute's speed is not.

## Winding time back

The slider under the map does not start at now - it starts **48 hours ago**. Drag it left
and three things happen together:

* the ship goes to where she actually was at that hour, drawn in orange
* the stretch she has sailed since is drawn on top of the track, with the distance
* the wind arrows and wave chevrons rewind to the weather that was there

Drag it the other way instead and it does the mirror image: she moves to where she is
**expected** to be at that hour - hollow marker, so it never reads as a logged fix - the
stretch of the expected route she should have covered is drawn fine-dashed, and the panel
gives the wind and sea she is sailing into, with how far she still has to run. Past the
estimated arrival it says she is alongside rather than clamping to the last forecast point
and claiming she is still 59 nm out three days after docking.

The panel reads out the hour, her position, and the wind and sea - what she was in, or
what she is heading for. **▶** plays the whole span through at 300 ms an hour - about half a minute for the
five days, and 150 ms turned out to be faster than an eye can follow; **Now**
jumps back to the present.

That panel used to be shown only while the slider was away from now, and **Now** used to
appear only once you had moved off it. Both were wrong in the same way. The panel sits above
the cards, so every time the slider crossed the zero point the whole page jumped - and the
zero point is precisely what you are aiming at when you drag it back, so the last inch of
the drag flickered. The button, meanwhile, was hidden exactly when a reader had not yet
discovered there was a way back.

So: **Now** sits to the left of the slider, always visible, greyed out while it has nothing
to do. The panel keeps its place and changes what it says, and its geometry is fixed rather
than flowed - explicit grid areas, one line per field, an explicit line box - because the
first two attempts at "fixed height" still breathed by a few pixels. Wrapping made the
height depend on how long the words happened to be; `align-items: baseline` then sized each
row from its items' baselines, and an empty field has none, so a row grew six pixels the
moment it was filled. `test_slider.py` drags across the zero point at phone and desktop
widths and asserts the cards below do not move by so much as a pixel.

The slider therefore covers **-48 h to +72 h** in 1-hour steps: two days of what happened
and three of what is coming, on one control. `_time_index` in `update.py` and `pickTimes`
in the page both span exactly that, which is why one slider can carry both. `GRID_HOURS`
and `WAKE_HOURS` move the two ends.

Two files make it work:

`data/wake.json` - one entry per whole hour: where she was, and the wind and waves at that
point at that time. Built by `build_wake()`, which interpolates her position between the
two nearest fixes and asks Open-Meteo for that hour at that place. Hours already fetched
are kept, so a run only asks about the hours that are new - normally one. That matters:
the file is a record of what happened, not a re-derivation from today's model run. Change
the window with `WAKE_HOURS`.

The grids - the arrows themselves come from the same endpoints as the forecast, with
`past_days` set, so no separate historical API and no key. `_time_index` in `update.py` and
`pickTimes` in the page both span `-48 h .. +24 h` in 3-hour steps, which is why one slider
can carry both.

Two things it deliberately will not do. It never interpolates across an AIS silence longer
than three hours - drawing a line through a two-day gap mid-Atlantic would be an invention,
not a position, so those hours are simply absent and the panel says so. And it does not
claim to be a measurement: it is the best reanalysis of the sea she was in, not the log
from her own instruments.

## Keeping it quick on a phone

Measured on the live page rather than guessed at: the whole thing is about 80 kB over the
wire and the document is ready in a fifth of a second. The network was never the problem.
What costs time on a phone is what happens after the bytes arrive, so that is what was
changed:

| Change | Why |
|---|---|
| `track.json` written compact | 28 % of it was indentation - 347 kB of whitespace the phone had to parse |
| Coordinates rounded to 5 decimals | some fixes carried 15 digits of float noise; 5 decimals is about a metre, and the ship is 64 m long |
| One track point per 2 minutes for the last week | the dense sources gave one a minute - 0.1 nm apart at cruising speed, invisible at any zoom |
| `preferCanvas: true` on the map | the track and route were hundreds of SVG nodes the browser re-laid-out on every pan; now one canvas |
| Arrow grid scales to the screen | each arrow is a DOM node with an inline SVG. 6 x 6 on a desktop, 3 x 3 on a phone: 51 markers down to 19 |
| The weather bar wraps | at 390 px the time and the Now button ran off the right edge |

Gzip already squeezed the whitespace out on the wire, so those first two changes barely
move the download - the win is parse time and memory, which is what a three-year-old phone
actually runs out of.

## What else is on the page

**Clock in <port>** shows the time ashore in the port she is lying in, or the one she is
heading for - a city clock, running, with how many hours that is from yours. Plus sunrise,
sunset, day length and the moon phase for the night watches.

It used to ask Open-Meteo for the time zone *at the ship*, which is a trap. Near a coast
that returns the nearest country's zone; out at sea it falls back to plain GMT. So the card
read "GMT · 2 hours behind you" while she was 25 nm off Norway - true, useless, and it would
have jumped between Europe/Oslo, GMT and Europe/London for reasons that had nothing to do
with the ship.

Now the job looks up the IANA zone name **at the port** and stores the name rather than an
offset, and asks for sunrise and sunset in **UTC**. The page converts both into that port's
time with `Intl`, so the clock and the sun on the card cannot disagree, and summer time is
right for a port she reaches in six months rather than frozen at whatever it was on the day
the file was written. If the lookup fails, no zone is claimed and the card hides itself.

Ships at sea do keep their own zone time - 15-degree bands off longitude, which is why her
clock would be three hours from the town clock in Vigo, where Spain still keeps the Berlin
time Franco adopted in 1940. That is a good story and a poor thing to put on a card for
parents, so the page shows the city clock instead: the number that answers "can I call him
now".

**Voyage progress** no longer counts AIS fixes (the **Voyage so far** card is
folded into **Position**). The number said more
about receiver coverage than about the voyage: it jumps by a thousand when she passes a
busy shore station and stops entirely mid-ocean.

**Voyage progress** measures how far along the planned route she has come, in nautical
miles and percent, next to the distance actually logged. The raw number of AIS fixes is
deliberately not shown: it says more about receiver coverage than about the voyage. The whole route is 14,848
nautical miles.

It works by projecting her position onto the current leg's own polyline and measuring to
that point - not by summing the distance still to run through the leg's waypoints. The
latter is what the page used to do, and it broke as soon as she passed the first waypoint:
the sum walked back east to pick it up, came out longer than the whole leg, and the card
clamped to `0 of 14,848 nm · 0.00%` while she was already 88 nm on her way. The card also
shows the leg on its own (`88 of 338 nm on this leg`), which is the figure that visibly
moves day to day - the whole-voyage percentage crawls, because the voyage is long.

**Ship's log** fills itself in from the track: every 1,000 nautical miles, the equator
crossing, the best 24-hour run, the fastest speed logged, and each port reached. The
first port is the exception - she sailed *from* Kristiansand, so the log shows the
departure date (17 August) rather than the arrival date in `ports.json`, which is only
the day the crew mustered.

The **Course** layer no longer has a card of its own - six rows of numbers for one idea was
too much. The comparison that matters lives in the tooltip on the drawn line: how long the
fastest passage takes, and how much time the plan leaves in hand.

**Ship's log** scrolls once it holds more than nine entries, rather than showing a few
lines and hiding the rest.

**Day by day** draws four small charts - distance per day from the track, and the strongest
wind, highest wave and fastest speed she reported each day from `history.json`. They are
deliberately four charts rather than one with four scales.

They showed fourteen days, which made every bar thin, forced the figures onto their sides to
fit, and buried the one number a reader actually wants - the worst it has been. Now each
chart is **the record, a gap, then the last five days**: six bars, wide enough to carry
their numbers upright, with the date of the record under its own bar.

The record bar is drawn **even when the record falls inside those five days**, and getting
that wrong is worth recording. The first version left it out in that case, reasoning that
drawing it twice would read as two separate gales. On real data it was plainly wrong: this
voyage is a week old, so every record IS recent, and the bar simply never appeared - and the
chart was five bars or six depending on the weather. The point of the bar is to be a fixed
reference in a fixed place. When it repeats a day, both bars carry the same date and the
same figure in bold, which reads as "that day, there, is the worst so far". The third series is rose rather than the teal
that would suit "wave" better: teal came out 10.3 OKLab ΔE from the blue in dark mode,
under the 15 floor, so the two would have been hard to tell apart. Rose passes every
computable check - lightness band, chroma floor, CVD separation, contrast - in both themes.

**Share** shows a QR code for the page, a copy-link button and a ready-made message for a
parents' group. If the address changes, run `python3 scripts/make_qr.py <new-url>`.

## How fresh the pictures are

This is not Google Earth. Google Earth is a mosaic stitched from deliberately cloud-free
scenes gathered over months or years, with no single date - which is exactly why it never
has weather in it. Everything on this page is a **single pass with its real cloud**.

Latencies measured against the services, not taken from their marketing:

| Source | Cadence | Shutter to available | Resolution |
|---|---|---|---|
| Meteosat MTG GeoColour | a new image every **10 minutes** | ~20 minutes | ~1 km |
| Meteosat MSG (SEVIRI) infrared | every **15 minutes** | ~15 minutes | ~3 km |
| GOES-East | every **10 minutes** | **45-50 minutes** | ~2 km |
| VIIRS true colour | one pass a day, ~13:30 local | a few hours - today's pass over her longitude appeared between 13:00 and 14:40 UTC | 375 m |
| Sentinel-2 | every 2-5 days over a given spot | **4 h 40 min** (11:25 shutter, 16:05 in the catalogue) | 10 m |

### Which of them you get when you tick Satellite

The **Satellite** box swaps the map for a real picture of her weather, and it picks whatever
is freshest over the water she is on at the moment the slider is set to. Nothing here is
hard-wired to a leg: the chain is evaluated from her longitude every time it draws.

| Where | What you get | How often |
|---|---|---|
| North Sea, Shetland, Ireland, Biscay | Meteosat MTG GeoColour | every 10 min |
| Funchal, the Canaries, the Atlantic, the Caribbean | GOES-East GeoColor | every 10 min |
| anywhere either can see, if the first has a gap | Meteosat MSG infrared | every 15 min |
| beyond both (nowhere on this voyage) | VIIRS / MODIS true colour | once a day |

Until August 2026 the European half of that table was wrong. The page asked NASA's GIBS for
three guessed Meteosat layer names, none of which exist - **GIBS carries GOES-East,
GOES-West and Himawari, and the only parts of the world its geostationary imagery misses are
Europe, Africa and the poles**, which is exactly where she is until Funchal. Those requests
404'd and the chain fell through to a polar orbiter's picture from the day before. So the
honest answer to "how often does the satellite view update" was: over her, **once a day**.

It now comes from [EUMETView](https://view.eumetsat.int), Europe's equivalent, which is open
- no key, no fee, `AccessConstraints: none`. Both layer names, both styles and both
publishing cadences were read out of its own capabilities document rather than guessed:

    mtg_fd:rgb_geocolour   MTG-I / FCI GeoColour    PT10M   -81..81 deg
    msg_fes:ir108          MSG / SEVIRI 10.8 um     PT15M   -77..77 deg

**GeoColour is true colour while the sun is up and an infrared composite, with city lights,
once it sets** - and it blends across the terminator rather than flipping the whole disc
over in one step, which is what a day/night switch written here would have done. It needs to
know where the sun is; the product already does, so this page does not. It is also the same
product GOES-East is drawn with on the other side of the ocean, so the map does not change
character halfway through the voyage.

The infrared fallback is deliberately not a prettier daytime layer. It only runs when
GeoColour has a gap, and a gap at three in the morning still has to show something.

Reading the capabilities took two attempts worth recording. The full document covers every
workspace at once and is too large to be read reliably end to end - asked about it whole, it
answered that no RGB layers existed, which is how the first version of this shipped with a
grey infrared channel as the primary. The per-workspace endpoints, `/geoserver/mtg_fd/wms`
and `/geoserver/msg_fes/wms`, are small enough to enumerate exactly, and they list thirteen
and twenty-two layers respectively - GeoColour, true colour, dust, airmass, fog, convection
and the rest. **Ask a service what it has one workspace at a time.**

If you want the 375 m true-colour view of the coast, that is still what **Seen from space**
is for: this layer is weather, that one is detail.

The layer follows the time slider, and **▶** too. It did not at first: the play loop moved
the clock, drew the wind and the waves and never told the imagery, so the cloud sat still
through the whole animation while everything over it moved. It now follows by swapping the
timestamp on the layer that is already up rather than building a new one - which asks only
for the tiles on screen instead of all of them, and does not flash the map white forty times
in a row - and it is throttled, because a hundred and twenty frames of satellite imagery in half a
minute is not a reasonable thing to ask of a public service. The throttle is scaled with the
frame rate, so the animation shows about forty pictures however long it runs - slowing the
clock down should not mean asking a free service for twice as many images. The wind and waves
are already in memory and keep their own pace.

The layer follows the time slider. Drag it back and you get the picture from that hour -
the archive runs to 2020, so anywhere the slider reaches is covered. Drag it *forward* into
the forecast and there is nothing to show, because nobody has photographed Saturday: it
holds the newest image there is and the badge says `newest` so the cloud on screen is not
mistaken for a forecast. EUMETView declares its time dimension `nearestValue="1"`, so a slot
that has not published yet is answered with the closest one it has rather than an error,
which is why the page asks with a deliberate lag and reports the slot it asked for.

The newest Sentinel-2 scene near her is usually a day or so old, and that is the *revisit*
rather than the latency: the satellite simply has not been back yet.

Because today's VIIRS pass is often published by mid-afternoon, the card asks for **today**
first and lets the image fall back a day on its own error, up to three days, relabelling
the caption as it goes. There is nothing to gain from assuming the worst.

## From orbit

Two different things share this card, and the first one matters every single day.

**Yesterday's view of her weather.** A true-colour image from NASA centred on her position,
with a red ring and cross on it. Cloud hiding the sea does not spoil this - the cloud *is*
the thing worth seeing, and knowing she is underneath it is the point. Two panels: the wide
one, widened until a coastline is in frame, and a sixth of it. This works everywhere on
earth, every day, with no dependence on anyone's acquisition plan. The marker carries its
own dark outline because it sits over white cloud as often as over dark sea.

## Seen from space - the album

Every pass that photographed the patch of sea she was in gets a tile, newest first, and the
count sits in the card's title so it reads as something that grows over the year. Each tile
links to ESA's own viewer so the reader can zoom in themselves rather than trusting a crop.

**Port calls are the good ones**, and the card says which are which. Alongside she is not
going anywhere, the quay gives the eye something to measure her against, and she arrives
somewhere every ten days or so - which, given that every port on the voyage has Sentinel-2
coverage while the ocean crossings have none, is where most of the album will come from. A
tile for a port call is cropped tighter, 500 m instead of 800, because a stationary ship
against a harbour wall is worth a closer look than one somewhere in open water.

Whether she is alongside is worked out in the page from the voyage plan's own arrive and
depart dates, so it costs nothing and stays right if the plan changes.

## Passes that actually looked at her

`scripts/orbithunter.py` asks the right question. "Which scenes are near the ship" finds
pictures of the water she happens to be in today, taken on a morning she was two hundred
miles away. The question that matters is a **space-and-time intersection**: for every scene
in the window, where was she at *that scene's own timestamp*, and does the footprint contain
that point?

It searches Element84's Earth Search over AWS Open Data - free, keyless - once an hour,
for cloud-free **Sentinel-2** only: 10 m optical, so she is about six pixels, a bright patch
with a wake.

**Sentinel-1 radar was tried and dropped**, and the reason is worth recording. Radar is in
principle the better sensor here: it sees through cloud and in the dark, it covers open
ocean that ESA does not photograph optically, and a steel ship lights up in it. But its
pixels live in a requester-pays bucket, so the only thing the page could offer was a link to
a grey scene in someone else's viewer - neither the ship nor any coastline. A row you cannot
look at is not worth its space. If a free route to S1 pixels appears, `COLLECTIONS` in
`orbithunter.py` is where it goes back in.

### The wide panel is widened until it finds a coast

A picture with no ship and no landscape is not a picture. So the job walks the land mask
outward from her position - 9°, 16°, 26°, 40° - and stores the first span that contains a
coastline; the page uses it for panel 1. In the North Sea that is 1,000 km. On the Cape
Verde to Brazil crossing it comes out at 2,900 km, which puts the Brazilian coast in frame
and a tropical cloud system in the middle: still a picture that says where she is.

### Where the close passes are actually possible

Measured against the catalogue rather than guessed at - 45 days of Sentinel-2 scenes at the
midpoint of each leg, and 30 nm off each port:

| | scenes | fairly clear |
|---|---|---|
| Skagerrak, North Sea, Shetland, Celtic Sea, Biscay | 25-40 | 3-23 |
| **Funchal - Sevilla - Las Palmas** | 11-22 | **11-22, nearly all of them** |
| Vigo to Funchal, Las Palmas to Mindelo | **0** | 0 |
| Every Atlantic and Caribbean crossing | **0** | 0 |
| **30 nm off every port on the voyage** | 8-28 | 5-25 |

So coverage does not follow how coastal a leg looks on a chart - it follows where ESA
chooses to photograph. Two legs that look coastal are blind, and every ocean crossing is
blind. But **every port has coverage**, and she arrives somewhere every ten days or so, so
the close passes come at the departures and arrivals. The best window of the whole voyage is
October to December in the Morocco and Canaries corridor, where the sky is dry and almost
every pass is usable.

It worked the first week. On **26 August at 10:45:20 UTC** Sentinel-2A photographed her
patch of the Skagerrak under 0 % cloud, with her nearest AIS fix 29 seconds from the
shutter. The bright object in the crop sits 26 m - under two pixels - from where her own
course and speed put her at the exposure, 3° off her heading, and it is the only bright
thing in 100 km² of sea. Three Sentinel-1 passes covered her the same two days, which is how we know radar would
work if its pixels were reachable.

The **From orbit** card shows three panels, widest first, because a blue square with a
white speck in it means nothing without somewhere to be: the land-bearing wide view, then
a sixth of it, then her 800 m. Each panel is a single image request - two to NASA's WMS, one to a titiler instance
that crops the Sentinel-2 COG - so nothing is composited in the job or stored in the
repository. A cross marks where AIS put her; she will have moved a little from it, and the
caption says so.

What the card claims is careful: a satellite photographed the patch of sea she was in, at
the moment she was in it. Whether there is a ship in those pixels, the reader can see.

## Actual satellite pictures

**Satellite** swaps the map for the real picture of her weather, from NASA's Global Imagery
Browse Services. Free, no key, no quota - it is the same service Worldview runs on. Two
sources, and which applies depends on where she is:

| Source | Where | How fresh | Resolution |
|---|---|---|---|
| **GOES-East GeoColor** | west of about 12° W | a new picture every **10 minutes** | ~2 km |
| **VIIRS on NOAA-20**, true colour | worldwide | one pass a day | **375 m** |

So in the North Sea she gets yesterday's polar-orbiter pass. From November, once she is out
past the Azores and across to Brazil, the Caribbean and the American coast, GOES-East can
see her and the picture refreshes every ten minutes for the rest of the winter - the whole
Atlantic crossing, live.

**It does not show the ship.** She is 64 m long and the best of these is 375 m to a pixel.
What you see is her weather: the actual depression, on the day she was in it. Which is the
better picture anyway - wind the time slider back and the clouds wind back with it.

Both latencies were measured rather than assumed: GOES publishes about 40 minutes behind,
the daily layers about a day, and *today's* date returns 404 until the pass is processed.
The page therefore never asks for a slot that cannot exist, and if one is missing anyway it
steps back - ten minutes for GOES, a day for the orbiter - up to three times, and says in
the badge which picture it settled on and how old it is. Coastlines are drawn over the top,
because true colour over open water is impossible to place otherwise.

Zoom limits are real and enforced: 9 for the daily layers, 7 for GOES. Beyond that Leaflet
upscales rather than showing a void.

## The course the wind would make fastest

The dashed route to the next port assumes she closes on it steadily. She cannot: a
full-rigged ship makes no progress inside about 58 degrees of the true wind, so on a
headwind leg she has to beat, and where the tacks fall depends entirely on the forecast.

`scripts/sailrouter.py` works that out by **isochrone routing**. From her position it fans
out across 36 headings, advances each by three hours at the speed her sails would give in
the wind forecast for that place and hour, keeps only the best position in each of 90
bearing sectors, and repeats. The frontier after n steps is everywhere she could be in n
steps; the first to reach the port carries the fastest route back with it. Land comes from
the same packed bitmap the port waypoints use.

One Open-Meteo call covers the whole box for the whole passage - 40 to 90 nodes, 3-hourly -
and the wind between nodes is interpolated, with directions summed as vectors so that 350°
and 10° average to 0° rather than to 180°. On the Lerwick leg the whole thing takes **0.2
seconds**; on the 1,320 nm Cape Verde to Brazil crossing, 0.6.

### The polar is an estimate

Sørlandet's real polars are not published. The table in `sailrouter.py` is built from what
is known about her - 64 m, 1,236 m² of sail, cruising around 5-8 knots, best runs in low
double figures - and from how square riggers behave: useless close to the wind, fastest on
a broad reach at 110-150°. The routes say "this is where the wind would push a ship like
her", not "this is what the master will do".

Two things fall out of it that are worth seeing, and both are emergent rather than coded:

* **Dead upwind she beats.** 300 nm straight into a 16-knot wind: 186 hours and 35 tacks,
  using only 60° and 70° off the wind. That is exactly the time a perfect beat at best VMG
  would take, which is the check that the router is not cheating.
* **Dead downwind she gybes.** She does not run square before it, because 153° makes better
  progress than 180° does. 300 nm downwind: 45 hours and 3 gybes. Nobody told it to do
  that; it comes out of the polar.

### The number that actually explains the voyage

The card compares the fastest passage against what the plan allows. For Kristiansand to
Lerwick that is roughly **39 hours against 264** - nine and a half days in hand. That single
comparison explains what you see on the map: they are not making for Shetland, they are
sailing a school ship around the North Sea with a fortnight to do it in. On a leg where the
two numbers converge, she is on passage and the course estimate is worth watching.

Turn on **Course** above the map. A ring marks every tack, with the wind angle and which
side it is on. It is recomputed from her current position on every run.

## Which leg she is on

Everything about the voyage - the next port, the ports behind her, whether she is alongside,
what the forward projection aims at, the map's extent, the route strip - used to be decided
by comparing today's date against `ports.json`. The next port was "the first one whose
`arrive` date is still ahead". A plan this page itself calls tentative was therefore the
only authority on where the ship was.

The consequence had a date on it. At midnight on **7 September** - Lerwick's *scheduled*
arrival day - the whole forward projection would have swung to Dublin, 460 nm further on,
whether or not she had docked; and the Voyage plan card would have said she was in Lerwick
at the same moment. Two cards on one screen disagreeing.

The rule now: the furthest port along the route she has actually been **observed** at fixes
the leg. She is either still in it, or at sea beyond it. A call is the same thing the ship's
log counts - 5 nm, 2.5 hours, under 2 knots - so the log and the leg cannot tell different
stories. The calendar answers only where the track cannot see, and even there it reads the
same shape: a port counts as reached once its arrival day has fully passed, and if she is
alongside port *i* then the next port is *i+1*, so the fallback cannot say she is in Lerwick
and on her way to Lerwick at the same time.

The rule is written twice, once in Python for the routing and once in JavaScript for the
page. That is a standing invitation to drift, so `test_leg.py` runs eleven scenarios through
**both** and fails if the two ever answer differently.

### Kristiansand is two ports

Writing the test found something worse than the date bug. **The voyage ends where it began**:
Kristiansand is port 0 and port 18, at identical coordinates. The first version identified
ports by name, so the very first morning alongside in August matched the *last* entry - and
the page would have announced the whole voyage complete on day one, `Ports done 19 of 19`.

Ports are therefore identified by their index on the route, never their name, and the search
for "which port is she at" never looks behind the furthest one she has already reached. The
same quay is port 0 in August and port 18 next May. No two ports on this route are within
five miles of each other, so nothing else is affected.

## The ship's log

Arrivals and departures come out of her own track, not out of the schedule. The schedule
says when she is *due* somewhere, and a sailing ship is early or late; what a parent is
refreshing the page for is when she actually got there.

A call is a stay within **5 nm** of a listed port that lasts at least **2.5 hours** and
during which she averages under **2 knots** over the fixes inside that circle. Both halves
of that are load-bearing. Five miles is wide enough to cover an anchorage or a berth a
little off the position we hold for the port - and wide enough that she sails through it on
the way past, because the approaches to Lerwick are on the road to Dublin. Time alone does
not separate the two either: in light airs she can crawl through the same water for three
hours. A ship alongside averages a tenth of a knot; a ship passing averages her passage
speed.

Short excursions are merged into the stay they interrupt, so a harbour move or an afternoon
sail out and back does not read as leaving and arriving again. A stay she has not ended gets
an arrival and no departure. Ports the track cannot speak for - the ones she called at
before this page existed - still appear, greyed, from the schedule, and disappear the moment
the track can account for them.

The rest of the log is unchanged: tracking started, every thousand miles, the equator, the
best day, and the fastest speed she has reported.

## The long cards fold

The page grew: the log, the cameras, the charts, the album, the whole route, the share panel.
On a phone, scrolling past all of that was most of what you did to get from the map to
anything else. Measured on a 390 px screen: 4,719 px of cards with everything open, 2,163 px
with the folds shut - **54 % of the scroll was material nobody was reading at that moment.**

The cards that answer "where is she" - Position, Voyage plan, Ship's clock, the weather -
never fold. Everything else does.

They are `<details>` elements rather than a div and a click handler, and that is not a
detail. They open and close **with no script at all**; the keyboard and screen readers get it
for free; and find-in-page opens a closed card to show a match instead of hiding it, because
the content stays in the document either way. `test_folds.py` asserts each of those directly.

Which cards start open is a judgement about what a reader came for, and two of them are not
constants:

| Card | Starts |
|---|---|
| Ship's log, Day by day | open |
| Seen from space | open **only when there are pictures** - a card whose whole content is an apology should not take a screen |
| Harbour cameras | shut at sea, **open by itself the moment she is alongside somewhere with a camera on it** |
| Full route, Share | shut |

After that it is the reader's choice, remembered per browser in `localStorage`. Nothing is
sent anywhere, and a browser with storage switched off still works - the test runs the whole
page with `localStorage` throwing on access.

## Harbour cameras

One rule: **link, never embed.**

Pulling somebody else's stream into this page would be their bandwidth under their terms,
and camera addresses move without warning - it would die silently and take a while for
anyone to notice. A link is honest, it survives, and it sends them the traffic they run the
camera for. `test_cameras.py` asserts that loading the page makes **zero** requests to any
camera host, and that the card contains no `iframe`, `img` or `video`.

**The card only exists when there is something to see.** It began as a standing list of
every camera on the route, which is a directory: nine months of a row for Dublin sitting
there while she is in the Caribbean. A camera is worth exactly one thing - watching her come
in - so the card appears when she is within **25 nm** of a port that has one, or lying
alongside it, and is absent the rest of the time. The page knows both from AIS, and it uses
the same `voyageState()` answer as everything else, so it cannot disagree with the Voyage
plan card. Twenty-five miles is a few hours out, about when a ship is committed to the run
in.

Kristiansand's camera was in here and came out: it is the local newspaper's, it shows only
this minute, and a live view of the quay she left in August is not a thing anyone wants nine
months later.

The cameras live in `ports.json` beside the port they look at, so adding one is three lines:

    "cameras": [
      {"name": "Victoria Pier", "url": "https://...",
       "view": "the pier visiting sail training ships berth at", "by": "Shetland Webcams"}
    ]

What is in there now:

| Port | Cameras | Run by |
|---|---|---|
| Lerwick | Victoria Pier, Esplanade, Town Hall east, Harbour north | Shetland Webcams |
| Dublin | three: Poolbeg lighthouse, the bay, the Liffey | Dublin Port Company |

**They are live views, and that is a real limit.** None of them keeps an archive, so none can
be wound back to show a departure that has already happened - the request for a look back at
Kristiansand cannot be granted, and the card says so rather than implying otherwise. The one
exception is Dublin Port, whose streams rewind twelve hours, which is long enough to catch an
arrival after the fact.

## What was taken out, and why

**Waves where she is now** and **Weather where she is now** were forecast strips running
forward in time from a fixed point. Once the route ahead is paced to the plan, they
contradict it: one card says she will be 100 nm further on by Saturday, the other quietly
assumes she is still here. They are gone, along with the API fields that fed them.

**At current speed** and **Positions logged** went for the reasons in the sections above:
a date extrapolated from this minute's speed is not a prediction, and a count of AIS fixes
measures receiver coverage rather than the voyage.

**Pictures from Instagram** were going to be a hand-kept list in `data/posts.json`, folded
into the ship's log. It is gone before it was ever used, and the reason is worth recording
so nobody rebuilds it. There is no way to read a public Instagram account automatically:
the **Basic Display API was withdrawn in December 2024**, and what replaced it needs a token
belonging to the account owner - we do not have @aplusworldacademy's and have no business
asking for one. Scraping would be against their terms and would break on the first markup
change. That left a file somebody would have to paste links into, by hand, every week for
nine months, which is a chore that gets abandoned in October and then sits there empty.

Nor is there another source: neither `aplusworldacademy.com` nor `fullriggeren.no` publishes
an RSS or Atom feed, so there is nothing to subscribe to either. A link to the account
survived the first cut and then went too - it was the last remnant of a feature that no
longer existed, and a page that follows one ship does not need to be a directory of places
the ship is mentioned. There is nothing about Instagram on the page at all.

**The event log** listed what changed in her AIS fields: under sail against under engine,
and the voyage block the crew fill in by hand. In practice it was unpredictable and said
very little - the status flag is set by whoever is on watch and often left alone through a
sail change, so the card was either silent or full of noise. What belongs in a log is where
she has been, so the arrivals and departures moved into **Ship's log** and `build_events()`
is gone. `data/events.json` is no longer written or read; the copy in the repository is
harmless and can be deleted.

## Data sources

| What | Where | Key |
|---|---|---|
| Position (about every 7 min, last ~2.5 days) | the ship's own page, `raptor.warrisk.tech/position/5334561` | none |
| Position, speed, course (Norwegian waters) | [BarentsWatch](https://developer.barentswatch.no/docs/category/ais/) Live AIS | free client |
| Position, speed, course (worldwide) | [aisstream.io](https://aisstream.io) (WebSocket) | free key |
| Waves, swell, sea temperature | [Open-Meteo Marine](https://open-meteo.com/en/docs/marine-weather-api) | none |
| Wind, temperature, pressure, forecast | [Open-Meteo Forecast](https://open-meteo.com) | none |
| Wind grid for the map | [Open-Meteo Forecast](https://open-meteo.com), multi-location | none |
| Wave grid for the map | [Open-Meteo Marine](https://open-meteo.com/en/docs/marine-weather-api), multi-location | none |
| Weather she has already sailed through | the same two endpoints with `past_days` | none |
| Map tiles | OpenStreetMap | none |
| Satellite imagery | [NASA GIBS](https://worldview.earthdata.nasa.gov) - GOES-East, VIIRS | none |

Open-Meteo is free for non-commercial use. Norwegian waters can additionally be read from
the [BarentsWatch/Kystverket AIS API](https://developer.barentswatch.no/docs/category/ais/)
if you want denser updates while the ship is home.

## Ideas for later

* **Notifications** — a job that emails or pushes when the ship reappears in AIS coverage,
  or is within X nautical miles of the next port.
* **Weekly summary** — distance covered and highest wave last week, sent every Sunday.
* **Photos** — a small gallery per port, if the students share pictures.

This is a private family project. It is not an official source from the ship, the
foundation or the school, and the ship's own page always takes precedence.
