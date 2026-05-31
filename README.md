# GPX Elevation Analysis

## Overview
Python-based analysis of GPX tracks with smoothing, thresholding, outlier handling, and elevation profile plotting.

## Structure
- `OverallAltitude/Code/Track_analysis_05.py`: main script.
- `OverallAltitude/RawMaterial/`: sample GPX files.
- `requirements.txt`: runtime dependencies.
- `tests/`: lightweight quality checks.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
python OverallAltitude/Code/Track_analysis_05.py
```

With explicit input:
```bash
python OverallAltitude/Code/Track_analysis_05.py --gpx-file ../RawMaterial/WACHAUmarathon_Marathon.gpx
```

## Tests
```bash
pytest -q
```
