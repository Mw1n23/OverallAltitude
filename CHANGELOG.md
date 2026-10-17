# Changelog

## Unreleased

- Added `overall-altitude-races`: compares ascent/descent of six marathons and four long-distance triathlons with one method and draws a comparison figure.
- Added `overall-altitude-fetch-tracks`: downloads the course tracks from their sources; the tracks are not part of the repository.
- Added an elevation backend for the open AWS Terrain Tiles (lidar terrain models in Austria, England and the USA).
- Added bridge and tunnel correction based on OpenStreetMap ways and surveyed deck elevations for the New York City bridges.
- Ascent/descent in the race comparison is summed between significant reversals (hysteresis), so gentle climbs count in full.
- Changed `overall-altitude`: `--elevation-source auto` now reads the AWS Terrain Tiles instead of OpenTopoData (`terrain-tiles` is available as a strict mode, `opentopodata` is unchanged).
- Changed `overall-altitude`: ascent/descent is summed between significant reversals. `--threshold` is the minimum reversal and defaults to 2 m (before: per-step threshold of 1 m, which ignored gentle climbs).
- Added `--course-id` to `overall-altitude` to correct the bridge and tunnel sections of a listed course.
- `Track_analysis_05.py` now imports sibling modules; run it through `overall-altitude` or `python -m OverallAltitude`, not as a standalone script.
- Faster terrain tiles: missing tiles are downloaded in parallel and decoded with Pillow when it is installed (Wachau sample: first run 27 s → 4 s, repeated run 2.6 s → 0.25 s; race comparison with cached data 11.6 s → 1.4 s; results unchanged, `Output/race_comparison.csv` is reproduced byte for byte).
- Terrain tiles are checked before they are cached and written atomically. A damaged cached tile, a read timeout or a dropped connection now raises an elevation service error, so `--elevation-source auto` falls back to GPX elevations instead of stopping.
- Fixed `--gpx-file` with a relative path: it is read from the current directory (it was looked up next to the code, so the README example failed).
- Terrain model sources accept GPX files without elevations.
- The x-axis of the single-track plot is labelled in kilometres, matching its ticks.
- Removed the unused `OverallAltitude/dependencies.txt`.
- The data folders under `OverallAltitude/RawMaterial` are declared as packages, which silences the setuptools warning; the wheel contents are unchanged.

## 0.1.0

- Reworked the GPX analysis into an installable Python package.
- Added CLI entry points: `overall-altitude` and `python -m OverallAltitude`.
- Switched total-ascent estimation to a DEM-aware workflow with caching and fallback behavior.
- Added repository documentation for method, setup, and contribution flow.
- Added CI and local tests for package import, CLI help, and analysis behavior.
