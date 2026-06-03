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

### 3. Prefer DEM elevations over raw GPX elevations
- Default mode is `auto`.
- In `auto`, the tool queries OpenTopoData and falls back to GPX elevations only if the DEM lookup fails.
- Default dataset stack: `eudem25m,mapzen`

This means:
- `eudem25m` is preferred for European coverage.
- `mapzen` acts as a fallback where EU-DEM is unavailable or empty.

### 4. Cache elevation lookups
- DEM responses are cached in `~/.cache/overall-altitude/elevation_cache.sqlite3` by default.
- Repeated runs against the same route are much faster and less likely to hit rate limits.

### 5. Suppress obvious spikes and smooth the profile
- Very large point-to-point jumps can be suppressed as outliers.
- A moving average smooths the elevation profile before ascent/descent is summed.

### 6. Compute ascent/descent with a threshold
- Small vertical fluctuations below the configured threshold are ignored.
- This reduces false climb caused by measurement noise even further.

## Supported modes

### `--elevation-source auto`
Recommended default.

Behavior:
- Use official DEM elevations when available.
- Fall back to GPX elevations if the remote lookup fails.

### `--elevation-source opentopodata`
Strict DEM mode.

Behavior:
- Fail if DEM lookup does not work.
- Useful for validating the DEM-based workflow.

### `--elevation-source gpx`
Offline fallback mode.

Behavior:
- Use only the embedded GPX elevations.
- Still applies resampling, outlier suppression, and smoothing.

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
This approach is materially better than summing raw GPS altitude points, but it is not equivalent to a surveyed terrain model or local LiDAR-based DTM.

Expected limitations:
- DEM vertical datum differences
- bridges, embankments, forest cover, or urban surfaces
- limited raster resolution

If higher accuracy is required, the next step is to replace the default public DEM stack with a regional terrain dataset or a self-hosted elevation backend.

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
