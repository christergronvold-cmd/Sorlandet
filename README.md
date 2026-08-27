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
data/events.json              what changed: sail or engine, destination, ETA  <- written by the script
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
what she is heading for. **▶** plays the whole span through at 150 ms an hour; **Now**
jumps back to the present.

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

**Voyage so far** and **Voyage progress** no longer count AIS fixes. The number said more
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

**Event log** shows the last 24 hours and scrolls, rather than showing four lines and hiding
the rest. If it has been a quiet day it falls back to the last dozen entries whenever they
happened.

**Day by day** draws three small charts - distance per day from the track, and the
strongest wind and highest wave each day from `history.json`. They are deliberately three
charts rather than one with three scales. The third series is rose rather than the teal
that would suit "wave" better: teal came out 10.3 OKLab ΔE from the blue in dark mode,
under the 15 floor, so the two would have been hard to tell apart. Rose passes every
computable check - lightness band, chroma floor, CVD separation, contrast - in both themes.

**Share** shows a QR code for the page, a copy-link button and a ready-made message for a
parents' group. If the address changes, run `python3 scripts/make_qr.py <new-url>`.

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

## The event log

A tracking site's "Manoeuvring" and "Destination changed" lines are not messages the ship
sends. They are that site's reading of two things that ride along in ordinary AIS: the
**navigational status** in every position report, and the **voyage block** the crew fill in
by hand (message 5 - destination, ETA, draught). We receive both from aisstream and from
BarentsWatch, so `build_events()` derives the same log itself into `data/events.json`.

It can also name things more usefully than "manoeuvring". For a full-rigged ship the
interesting change is status 0 against status 8:

| Status | Shown as |
|---|---|
| 0 | Under way using engine |
| 8 | **Under way under sail** |
| 1 / 5 | At anchor / Moored |
| 3 | Restricted manoeuvrability |

Only changes are recorded, and a status has to hold for **two consecutive fixes** before it
counts - a single stray report while she tacks would otherwise fill the log with noise.
The log also notes when she comes back into AIS coverage after a silence of six hours or
more, which on this voyage will mean an ocean.

`data/events.json` keeps a `state` block alongside the events, so each run compares against
what was last true rather than re-deriving the whole history.

## What was taken out, and why

**Waves where she is now** and **Weather where she is now** were forecast strips running
forward in time from a fixed point. Once the route ahead is paced to the plan, they
contradict it: one card says she will be 100 nm further on by Saturday, the other quietly
assumes she is still here. They are gone, along with the API fields that fed them.

**At current speed** and **Positions logged** went for the reasons in the sections above:
a date extrapolated from this minute's speed is not a prediction, and a count of AIS fixes
measures receiver coverage rather than the voyage.

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
