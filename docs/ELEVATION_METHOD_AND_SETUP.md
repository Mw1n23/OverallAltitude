# Elevation Method and Repository Setup

## Why this repository exists

The original problem was that a naive point-by-point sum of GPX altitude values often reports far too much climbing. Consumer GPS altitude data contains vertical noise, and if every local bump is counted as a real ascent, the total gain becomes inflated.

This repository now treats total ascent as a terrain-analysis problem instead of a pure GPX metadata problem.

## Current method

The analysis pipeline is implemented in `OverallAltitude/Code/Track_analysis_05.py`.

### 1. Parse the GPX track

- Read all track points with latitude, longitude, and embedded GPX elevation if present.
- Keep the original GPX values as a fallback and for comparison plots.

### 2. Resample to a fixed horizontal spacing

- The track is resampled to a fixed spacing, default `25 m`.
- This decouples the analysis from irregular GPS point density.
- It prevents routes with unusually dense recordings from looking artificially noisy.

### 3. Prefer terrain model elevations over raw GPX elevations

- Default mode is `auto`.
- In `auto`, the tool reads the open AWS Terrain Tiles (zoom 14) and falls back to GPX elevations only if the tiles cannot be fetched.
- The tiles carry the best open terrain model per region, for example the 10 m lidar model in Austria, the 2 m lidar model in England and 3DEP 10 m in the USA. In Germany they only contain EU-DEM; `--elevation-source opentopodata --dataset bkg200m` is less noisy there.
- `--elevation-source opentopodata` queries OpenTopoData instead. Default dataset stack: `eudem25m,mapzen`. Both are 25–30 m surface models and too noisy for flat courses; see `docs/RACE_COMPARISON.md`.

### 4. Cache elevation lookups

- OpenTopoData responses are cached in `~/.cache/overall-altitude/elevation_cache.sqlite3` by default.
- Terrain tiles are cached in the folder `terrain-tiles` next to that file.
- Repeated runs against the same route are much faster and less likely to hit rate limits.

### 5. Correct bridges and tunnels (optional)

- A terrain model follows the ground: below a bridge it drops to the water, above a tunnel it climbs over the hill.
- With `--course-id`, the sections listed for that course in `OverallAltitude/RawMaterial/races/structures.json` are corrected: inside a section the track follows the straight line between its two ends; a bridge is never below the terrain, a tunnel never above it.
- `python -m OverallAltitude.Code.course_structures --course <id>` lists candidate sections from OpenStreetMap for a course of the race manifest.

### 6. Suppress obvious spikes and smooth the profile

- Very large point-to-point jumps can be suppressed as outliers.
- A moving average smooths the elevation profile before ascent/descent is summed.

### 7. Compute ascent/descent between significant reversals

- The profile is split into climbs and descents at reversals of at least `--threshold` meters (default `2`); the heights of these legs are summed.
- Fluctuations below the threshold add nothing, and a long gentle climb counts in full.
- Ascent minus descent equals the net difference between start and finish.

## Supported modes

### `--elevation-source auto`

Recommended default.

Behavior:

- Use terrain tile elevations when available.
- Fall back to GPX elevations if the tiles cannot be fetched.

### `--elevation-source terrain-tiles`

Strict terrain tile mode.

Behavior:

- Fail if the tiles cannot be fetched.

### `--elevation-source opentopodata`

Strict OpenTopoData mode.

Behavior:

- Use the dataset stack given by `--dataset`.
- Fail if the DEM lookup does not work.

### `--elevation-source gpx`

Offline fallback mode.

Behavior:

- Use only the embedded GPX elevations.
- Still applies resampling, outlier suppression, and smoothing.
- Cannot be combined with `--course-id`.

## Operational notes

### Public DEM API limits

The public OpenTopoData endpoint can return:

- `429 Too Many Requests`
- `400 Bad Request` for oversized URL batches

The client now mitigates this by:

- splitting large requests into smaller batches,
- retrying on rate limits,
- using cached results whenever possible.

### Precision limits

This approach is materially better than summing raw GPS altitude points, but its quality depends on the terrain model behind the tiles.

Expected limitations:

- DEM vertical datum differences
- bridges and tunnels that are not listed for the course
- embankments, cuttings and river banks next to the road when the track is not exact
- regions without a lidar terrain model in the tiles (for example Germany), where forest cover and buildings add noise

Totals depend on smoothing and threshold. They are comparable between tracks analysed with the same settings, not with figures computed elsewhere.

## Best-practice clone and install flow

### Standard user installation

```bash
git clone <repo-url>
cd OverallAltitude
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .[plot]
```

Then run:

```bash
overall-altitude --help
```

### Development installation

```bash
git clone <repo-url>
cd OverallAltitude
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[plot]
```

Run tests:

```bash
python -m unittest discover -s tests -q
```
