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
box of 360 x 360 nautical miles, for the next 24 hours in 3-hour steps, and each is a
single API call. They are stored in `data/wind.json` and `data/waves.json`.

The box is measured in nautical miles rather than degrees, so the longitude span widens
with latitude - 11.2 degrees wide in the Skagerrak, 6.0 at the equator - and the spacing
between arrows stays 90 nautical miles wherever the ship is.

On the map, **wind** is a single arrow, coloured pale blue through green and amber to dark
red as it strengthens. **Waves** are a double chevron with the height in metres beside it,
on a blue-to-purple scale. Both point the way they are travelling, and the two sit on
either side of their grid point so they stay readable together. The slider under the map
steps through the forecast; the two buttons turn each layer on and off. Waves start off.

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

## What else is on the page

**On board** shows the ship's own local time as a running clock, how many hours that is
from your clock, sunrise, sunset, length of the day, and the moon phase for the night
watches. The time zone comes from Open-Meteo; the moon is computed locally from the
synodic month, no API needed.

**Voyage progress** measures how far along the planned route she has come, in nautical
miles and percent, next to the distance actually logged. The whole route is 14,848
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

**Day by day** draws two small charts - distance per day from the track, and the
strongest wind each day from `history.json`. They are deliberately two charts rather than
one with two scales.

**Share** shows a QR code for the page, a copy-link button and a ready-made message for a
parents' group. If the address changes, run `python3 scripts/make_qr.py <new-url>`.

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
| Map tiles | OpenStreetMap | none |

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
