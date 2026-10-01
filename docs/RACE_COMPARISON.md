# Race Comparison: Method, Data and Limits

`overall-altitude-races` compares the elevation profile, total ascent and total descent of
several race courses with one method. The current course set holds six marathons and four
long-distance triathlons (bike and run course each).

## Run

The course tracks are third-party material and are not part of the repository. Download
them once from their sources:

```bash
overall-altitude-fetch-tracks
```

Then run the comparison:

```bash
overall-altitude-races --output-dir Output
```

or, without installing the entry point:

```bash
python -m OverallAltitude.Code.race_comparison --output-dir Output
```

Output:

- `Output/race_comparison.csv`: one row per course with distance, ascent, descent, net
  difference, lowest and highest point, elevation source and track source.
- `Output/race_comparison.png` and `Output/race_comparison.svg`: the comparison figure
  (needs `matplotlib`; skip it with `--no-plot`).

The table and the PNG figure of the current analysis are kept in the repository.

Elevation lookups and terrain tiles are cached in `--cache-dir`
(default `~/.cache/overall-altitude`). A first run needs network access and takes several
minutes because the public OpenTopoData endpoint allows one request per second.

## Data files

All inputs live in `OverallAltitude/RawMaterial/races/`:

| File | Content |
| --- | --- |
| `races.json` | Course manifest: event, discipline, GPX file, laps, track section, official distance, elevation source, track source and download location |
| `structures.json` | Bridge and tunnel sections per course, in km along the resampled track |
| `bridge_deck_points.json` | Surveyed deck elevations for the bridges of the New York City Marathon |
| `*.gpx` | Course tracks, downloaded by `overall-altitude-fetch-tracks`; ignored by git |

## Method

1. **Track.** The GPX track is read and resampled to 25 m spacing. If one file holds several
   disciplines (Ironman Hawaii), `start_km` and `end_km` select the section. Lapped courses
   (`laps`) are analysed for one lap, and the lap is repeated.
2. **Terrain elevation.** Elevations in the GPX files are ignored. They come from unknown
   sources and differ strongly between files. Instead, every point gets its elevation from
   the best open terrain model of the region:
   - `terrain-tiles`: AWS Terrain Tiles at zoom 14. In Austria they contain the 10 m lidar
     terrain model, in England the 2 m lidar model.
   - `opentopodata:ned10m`: USGS 3DEP 10 m for the USA.
   - `opentopodata:bkg200m`: BKG DGM200 for Germany. No finer bare-earth model is available
     through an open API; courses with this source carry `coarse_terrain_model` in the
     manifest and are marked in the figure.
3. **Bridges and tunnels.** A terrain model follows the ground. Under a bridge it drops to
   the water, above a tunnel it climbs over the hill. Inside a span from `structures.json`
   the course follows the straight line between the two ends: a bridge is never below the
   terrain, a tunnel never above it. The five river bridges of the New York City Marathon
   are long and arched, so their deck profile comes from surveyed spot elevations.
4. **Smoothing.** Moving average over five points (125 m).
5. **Ascent and descent.** Significant reversals of at least 2 m split the profile into
   climbs and descents; the heights of these legs are summed. Noise below 2 m adds nothing,
   and a long gentle climb counts in full. Ascent minus descent equals the net difference
   between start and finish.
6. **Distance.** Run courses are scaled to 42.195 km, because a digitised track is up to
   1.3 % longer or shorter than the measured course. Bike courses keep their track length.

The smoothing window and the threshold are the same for all courses and equal the defaults
of the single-track analysis. `overall-altitude --gpx-file <file> --course-id <id>` therefore
reproduces the totals of a single-lap course that uses the terrain tiles.

## Why not the default OpenTopoData datasets

The OpenTopoData stack `eudem25m,mapzen` is too noisy for flat city courses. EU-DEM and the `mapzen` dataset are surface models with 25–30 m cells; buildings,
river banks and railway cuttings next to the road add several metres of noise. For the
Wachau marathon the sum ranged from 150 m to 390 m depending on the smoothing. The lidar
terrain models give a stable result, and with them the method reproduces published values
(see below).

## Results

Analysis of 1 October 2026 (window 125 m, threshold 2 m):

| Course | Discipline | Distance | Ascent | Descent | Net | Lowest–highest point |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Ironman Hawaii | Triathlon run | 42.2 km | 292 m | 292 m | +1 m | 0–48 m |
| New York City Marathon | Marathon | 42.2 km | 260 m | 275 m | −16 m | 2–79 m |
| Challenge Roth * | Triathlon run | 42.2 km | 248 m | 250 m | −2 m | 328–369 m |
| Boston Marathon | Marathon | 42.2 km | 245 m | 382 m | −137 m | 2–140 m |
| London Marathon | Marathon | 42.2 km | 94 m | 128 m | −34 m | 1–52 m |
| Vienna City Marathon | Marathon | 42.2 km | 89 m | 80 m | +9 m | 158–202 m |
| Ironman Hamburg * | Triathlon run | 42.2 km | 81 m | 81 m | 0 m | 3–10 m |
| Ironman Austria | Triathlon run | 42.2 km | 51 m | 52 m | −1 m | 441–448 m |
| Berlin-Marathon * | Marathon | 42.2 km | 42 m | 42 m | 0 m | 32–54 m |
| Wachau-Marathon | Marathon | 42.2 km | 42 m | 55 m | −13 m | 196–213 m |
| Ironman Austria | Triathlon bike | 175.1 km | 1,863 m | 1,863 m | 0 m | 440–684 m |
| Ironman Hawaii | Triathlon bike | 181.3 km | 1,849 m | 1,849 m | 0 m | 0–194 m |
| Challenge Roth * | Triathlon bike | 178.8 km | 1,555 m | 1,590 m | −35 m | 347–561 m |
| Ironman Hamburg * | Triathlon bike | 180.0 km | 509 m | 509 m | 0 m | 0–39 m |

Courses marked with `*` use the 200 m terrain model.

### Plausibility

| Course | This analysis | Published |
| --- | --- | --- |
| Boston Marathon | +245 m / −382 m | about +250 m / −390 m (common GPS-based figures) |
| Ironman Hawaii run | +292 m | 307 m (course description on hdsports.org) |
| Ironman Hawaii bike | +1,849 m | 1,772 m (course description on hdsports.org) |
| Ironman Austria bike | +1,863 m | 1,680–1,750 m (published course descriptions) |
| Challenge Roth bike | +1,555 m | 1,427 m with the same method on the elevations in the official GPX file |

### Sensitivity

With a 225 m window or a 3 m threshold the sums drop by roughly 5–20 % (up to 30 % on the
flattest courses); the order of the groups stays the same. Differences of a few percent between two courses are therefore not
meaningful.

## Limits

- Totals depend on terrain model, smoothing and threshold. They are comparable between the
  courses of this analysis, not with figures computed elsewhere.
- Germany: the 200 m model cannot resolve dykes, canal banks and short ramps. Totals of
  these courses are less exact and tend to be too high.
- Short underpasses (for example below Massachusetts Avenue in Boston) are not modelled.
- Course versions differ in age (see `course_version` in the manifest). Ironman Austria
  bike uses the classic lap twice; the race course since 2024 follows this lap and is about
  2 km longer per lap. Ironman Hamburg uses the 2022 lap; later editions changed parts of it.
- Lapped courses are one lap repeated; the way to and from the transition area is not
  modelled separately.
- Ironman Brasil is not included: no freely downloadable course file was found.

## Track sources

| Course | Source |
| --- | --- |
| Boston, New York City, London, Vienna City, Berlin marathon | goandrace.com course pages (GPX download) |
| Wachau marathon | `RawMaterial/WACHAUmarathon_Marathon.gpx` (AllTrails) |
| Challenge Roth bike and run | challenge-roth.com, course info downloads |
| Ironman Austria bike lap | woerthersee.com, tour "Ironman Runde - 2011" |
| Ironman Austria run, Ironman Hamburg bike and run lap, Ironman Hawaii | hdsports.org track pages (GPX download) |

Other data: terrain models from USGS (3DEP), the Austrian and English lidar models in the
AWS Terrain Tiles, and BKG (DGM200); bridge and tunnel ways from OpenStreetMap
(© OpenStreetMap contributors, ODbL); bridge deck elevations from the NYC Planimetric
Database (NYC Open Data, dataset `9uxf-ng6q`, feature code 3000, points within 25 m of the
course on the five bridge spans).

The GPX files are third-party material and stay out of the repository; only the Wachau
sample track is part of it. The manifest names the download location of every other track.

## Adding a course

1. Add an entry to `races.json`, with a `download` source or with the GPX file put into
   `OverallAltitude/RawMaterial/races/` by hand.
2. List bridge and tunnel candidates:

   ```bash
   python -m OverallAltitude.Code.course_structures --course <id>
   ```

   Copy the short bridges into `structures.json`. Check tunnels and long bridges by hand:
   OpenStreetMap cannot tell whether the course uses a tunnel or the road above it.
3. Run `overall-altitude-races`.
