# Changelog

## Unreleased
- Added `overall-altitude-races`: compares ascent/descent of six marathons and four long-distance triathlons with one method and draws a comparison figure.
- Added `overall-altitude-fetch-tracks`: downloads the course tracks from their sources; the tracks are not part of the repository.
- Added an elevation backend for the open AWS Terrain Tiles (lidar terrain models in Austria, England and the USA).
- Added bridge and tunnel correction based on OpenStreetMap ways and surveyed deck elevations for the New York City bridges.
- Ascent/descent in the race comparison is summed between significant reversals (hysteresis), so gentle climbs count in full.

## 0.1.0
- Reworked the GPX analysis into an installable Python package.
- Added CLI entry points: `overall-altitude` and `python -m OverallAltitude`.
- Switched total-ascent estimation to a DEM-aware workflow with caching and fallback behavior.
- Added repository documentation for method, setup, and contribution flow.
- Added CI and local tests for package import, CLI help, and analysis behavior.
