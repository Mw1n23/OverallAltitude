# GPX Elevation Analysis

This repository contains Python scripts to analyze GPX tracks, focusing on elevation gain, track analysis, and data conversion.

## Repository Structure
- `OverallAltitude/`
  - `Code/`
    - `GSB_csv_converter.py`: Converts GSB data to CSV format.
    - `Track_analysis-05.py`: Analyzes elevation gain from GPX tracks with smoothing, thresholding, and plotting (version 0.5).
    - `extract_elevations-05.py`: Extracts elevation data from GPX tracks (version 0.5).
  - `RawMaterial/`
    - `WACHAUmarathon_Marathon.gpx`: Sample GPX file (Wachau Marathon track).
    - `wallersee1lauf.gpx`: Sample GPX file (Wallersee run track).
- `LICENSE`: License for the repository.
- `README.md`: This file.

## Features of `Track_analysis-05.py`
- Smoothing of elevation data to reduce GPS noise.
- Threshold-based filtering of small elevation changes.
- Outlier detection and correction.
- Detection of loops in the track.
- Visualization of elevation profile with slope percentages.

## Output of `Track_analysis-05.py`
- Prints the total ascent, descent, net elevation difference, average distance between points, and detected loops.
- Displays a plot of the elevation profile with slope percentages and loop markers.
