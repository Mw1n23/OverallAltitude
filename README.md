# GPX Elevation Analysis

[![CI](https://github.com/Mw1n23/OverallAltitude/actions/workflows/python-ci.yml/badge.svg)](https://github.com/Mw1n23/OverallAltitude/actions/workflows/python-ci.yml)

## Overview
This project analyzes GPX tracks and estimates total ascent/descent with a more realistic pipeline than a raw point-by-point sum of GPX altitude values.

The main script now:
- reads the GPX track,
- resamples it to a fixed horizontal spacing,
- optionally replaces noisy GPX altitudes with DEM-based elevations from OpenTopoData,
- caches external elevation lookups in SQLite,
- smooths the resulting profile before computing total climb.

This avoids the classic problem where summing every GPS altitude jump produces an implausibly large overall ascent.

Technical background and method notes are documented in `docs/ELEVATION_METHOD_AND_SETUP.md`.

## Structure
- `OverallAltitude/Code/Track_analysis_05.py`: main script and analysis pipeline.
- `OverallAltitude/RawMaterial/`: sample GPX files.
- `pyproject.toml` and `setup.py`: package metadata and install entry points.
- `requirements.txt`: convenience wrapper for `pip install -r requirements.txt`.
- `tests/`: standard-library unit tests.

## Clone and Install
Standard installation:
```bash
git clone https://github.com/Mw1n23/OverallAltitude.git
cd OverallAltitude
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .[plot]
```

Development installation:
```bash
git clone https://github.com/Mw1n23/OverallAltitude.git
cd OverallAltitude
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[plot]
```

`matplotlib` is optional and is only needed for plotting. The core analysis itself uses the Python standard library.

## Run
Installed CLI:
```bash
overall-altitude --help
```

Module entry point:
```bash
python -m OverallAltitude --help
```

Default analysis run:
```bash
overall-altitude
```

Explicit GPX input:
```bash
overall-altitude --gpx-file OverallAltitude/RawMaterial/WACHAUmarathon_Marathon.gpx
```

Force offline mode and use the embedded GPX elevations only:
```bash
overall-altitude --elevation-source gpx
```

Use the official DEM lookup only:
```bash
overall-altitude --elevation-source opentopodata
```

Important options:
- `--resample-distance 25`: horizontal spacing in meters for the analysis profile.
- `--dataset eudem25m,mapzen`: DEM dataset stack used for the lookup.
- `--cache-db ~/.cache/overall-altitude/elevation_cache.sqlite3`: SQLite cache for repeated runs.
- `--plot / --no-plot`: enable or disable plotting.

## Tests
```bash
python -m unittest discover -s tests -q
```

## GitHub workflow
- CI runs on push and pull request.
- Use `CONTRIBUTING.md` for the local development gate.
- Use `CHANGELOG.md` to track user-visible repository changes.
