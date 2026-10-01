from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .structure_correction import (
    DEFAULT_DECK_POINTS,
    DEFAULT_STRUCTURES,
    DeckPointSets,
    StructureSpan,
    apply_structures,
    load_deck_points,
    load_structures,
)
from .terrain_tiles import TerrainTileClient
from .track_types import ElevationServiceError, TrackPoint


GPX_NAMESPACE = {"default": "http://www.topografix.com/GPX/1/1"}
SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_GPX_FILE = SCRIPT_PATH.parent.parent / "RawMaterial" / "WACHAUmarathon_Marathon.gpx"
DEFAULT_DATASET = "eudem25m,mapzen"
DEFAULT_RESAMPLE_DISTANCE_M = 25.0
DEFAULT_WINDOW_SIZE = 5
DEFAULT_THRESHOLD_M = 2.0
DEFAULT_MAX_CHANGE_M = 50.0
TERRAIN_TILES_SOURCE = "terrain-tiles"
TERRAIN_TILE_CACHE_DIRNAME = "terrain-tiles"


def default_cache_db() -> Path:
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    base_dir = Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"
    return base_dir / "overall-altitude" / "elevation_cache.sqlite3"


DEFAULT_CACHE_DB = default_cache_db()


@dataclass(frozen=True)
class AnalysisResult:
    source_label: str
    dataset_label: str | None
    total_distance_m: float
    total_ascent_m: float
    total_descent_m: float
    net_difference_m: float
    average_point_spacing_m: float
    raw_point_count: int
    analysis_point_count: int
    raw_distances_m: list[float]
    raw_elevations_m: list[float]
    analysis_distances_m: list[float]
    analysis_elevations_m: list[float]


class BatchRequestTooLargeError(ElevationServiceError):
    """Raised when a batch request has to be split into smaller chunks."""


class ElevationCache:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS elevation_cache (
                provider TEXT NOT NULL,
                dataset TEXT NOT NULL,
                interpolation TEXT NOT NULL,
                latitude TEXT NOT NULL,
                longitude TEXT NOT NULL,
                elevation REAL,
                PRIMARY KEY (provider, dataset, interpolation, latitude, longitude)
            )
            """
        )
        self.connection.commit()

    @staticmethod
    def _coord_key(value: float) -> str:
        return f"{value:.6f}"

    def get(
        self,
        provider: str,
        dataset: str,
        interpolation: str,
        latitude: float,
        longitude: float,
    ) -> tuple[bool, float | None]:
        row = self.connection.execute(
            """
            SELECT elevation
            FROM elevation_cache
            WHERE provider = ? AND dataset = ? AND interpolation = ? AND latitude = ? AND longitude = ?
            """,
            (
                provider,
                dataset,
                interpolation,
                self._coord_key(latitude),
                self._coord_key(longitude),
            ),
        ).fetchone()
        if row is None:
            return False, None
        return True, row[0]

    def put_many(
        self,
        provider: str,
        dataset: str,
        interpolation: str,
        rows: Iterable[tuple[float, float, float | None]],
    ) -> None:
        payload = [
            (
                provider,
                dataset,
                interpolation,
                self._coord_key(latitude),
                self._coord_key(longitude),
                elevation,
            )
            for latitude, longitude, elevation in rows
        ]
        if not payload:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO elevation_cache
            (provider, dataset, interpolation, latitude, longitude, elevation)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ElevationCache":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class OpenTopoDataClient:
    provider_name = "opentopodata"

    def __init__(
        self,
        dataset: str = DEFAULT_DATASET,
        base_url: str = "https://api.opentopodata.org/v1",
        interpolation: str = "bilinear",
        timeout_seconds: float = 15.0,
        batch_size: int = 200,
        max_retries: int = 3,
        cache: ElevationCache | None = None,
    ):
        self.dataset = dataset
        self.base_url = base_url.rstrip("/")
        self.interpolation = interpolation
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.cache = cache

    def lookup(self, points: Sequence[TrackPoint]) -> list[float | None]:
        results: list[float | None] = [None] * len(points)
        missing_indices: list[int] = []

        for index, point in enumerate(points):
            if self.cache is None:
                missing_indices.append(index)
                continue
            cached, elevation = self.cache.get(
                self.provider_name,
                self.dataset,
                self.interpolation,
                point.latitude,
                point.longitude,
            )
            if cached:
                results[index] = elevation
            else:
                missing_indices.append(index)

        pending_batches = [
            missing_indices[start : start + self.batch_size]
            for start in range(0, len(missing_indices), self.batch_size)
        ]

        while pending_batches:
            batch_indices = pending_batches.pop(0)
            batch_points = [points[index] for index in batch_indices]
            try:
                elevations = self._fetch_batch(batch_points)
            except BatchRequestTooLargeError:
                if len(batch_indices) == 1:
                    raise ElevationServiceError(
                        "Elevation service rejected a single-point request."
                    )
                midpoint = len(batch_indices) // 2
                pending_batches.insert(0, batch_indices[midpoint:])
                pending_batches.insert(0, batch_indices[:midpoint])
                continue
            if len(elevations) != len(batch_points):
                raise ElevationServiceError(
                    "The elevation service returned an unexpected number of results."
                )
            for index, elevation in zip(batch_indices, elevations):
                results[index] = elevation
            if self.cache is not None:
                self.cache.put_many(
                    self.provider_name,
                    self.dataset,
                    self.interpolation,
                    [
                        (point.latitude, point.longitude, elevation)
                        for point, elevation in zip(batch_points, elevations)
                    ],
                )

        return results

    def _fetch_batch(self, points: Sequence[TrackPoint]) -> list[float | None]:
        locations = "|".join(
            f"{point.latitude:.6f},{point.longitude:.6f}" for point in points
        )
        query = urllib.parse.urlencode(
            {
                "locations": locations,
                "interpolation": self.interpolation,
                "nodata_value": "null",
            }
        )
        dataset_path = urllib.parse.quote(self.dataset, safe=",")
        url = f"{self.base_url}/{dataset_path}?{query}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "OverallAltitude/2.0"},
        )

        payload = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {400, 414} and len(points) > 1:
                    raise BatchRequestTooLargeError(
                        f"Batch request with {len(points)} points was rejected by the service."
                    ) from exc
                if exc.code == 429 and attempt < self.max_retries:
                    retry_after_header = exc.headers.get("Retry-After")
                    if retry_after_header and retry_after_header.isdigit():
                        sleep_seconds = float(retry_after_header)
                    else:
                        sleep_seconds = 1.5 * (attempt + 1)
                    time.sleep(sleep_seconds)
                    continue
                raise ElevationServiceError(f"Cannot reach elevation service: {exc}") from exc
            except urllib.error.URLError as exc:
                raise ElevationServiceError(f"Cannot reach elevation service: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ElevationServiceError("Elevation service returned invalid JSON.") from exc

        if payload is None:
            raise ElevationServiceError("Elevation service returned no payload.")
        if payload.get("status") != "OK":
            message = payload.get("error", "Unknown elevation service error.")
            raise ElevationServiceError(message)

        results = payload.get("results", [])
        return [entry.get("elevation") for entry in results]


def parse_gpx(file_path: Path) -> list[TrackPoint]:
    try:
        root = ET.parse(file_path).getroot()
    except ET.ParseError as exc:
        raise ValueError("Error parsing the GPX file. Ensure the file is valid.") from exc

    track_points: list[TrackPoint] = []
    for trkpt in root.findall(".//default:trkpt", GPX_NAMESPACE):
        latitude = float(trkpt.attrib["lat"])
        longitude = float(trkpt.attrib["lon"])
        elevation_node = trkpt.find("default:ele", GPX_NAMESPACE)
        elevation = float(elevation_node.text) if elevation_node is not None else None
        track_points.append(TrackPoint(latitude, longitude, elevation))

    if not track_points:
        raise ValueError("The GPX file does not contain any track points.")

    return track_points


def haversine_distance_m(point_a: TrackPoint, point_b: TrackPoint) -> float:
    radius_m = 6_371_000.0
    lat1 = math.radians(point_a.latitude)
    lon1 = math.radians(point_a.longitude)
    lat2 = math.radians(point_b.latitude)
    lon2 = math.radians(point_b.longitude)
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    hav = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius_m * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def cumulative_distances(points: Sequence[TrackPoint]) -> list[float]:
    distances = [0.0]
    for previous, current in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_distance_m(previous, current))
    return distances


def interpolate_point(
    point_a: TrackPoint,
    point_b: TrackPoint,
    fraction: float,
) -> TrackPoint:
    latitude = point_a.latitude + (point_b.latitude - point_a.latitude) * fraction
    longitude = point_a.longitude + (point_b.longitude - point_a.longitude) * fraction

    if point_a.elevation_m is not None and point_b.elevation_m is not None:
        elevation = point_a.elevation_m + (point_b.elevation_m - point_a.elevation_m) * fraction
    else:
        elevation = point_a.elevation_m if fraction < 0.5 else point_b.elevation_m

    return TrackPoint(latitude, longitude, elevation)


def resample_track(points: Sequence[TrackPoint], spacing_m: float) -> tuple[list[TrackPoint], list[float]]:
    raw_distances = cumulative_distances(points)
    total_distance_m = raw_distances[-1]
    if spacing_m <= 0 or total_distance_m == 0:
        return list(points), raw_distances

    targets = [0.0]
    current = spacing_m
    while current < total_distance_m:
        targets.append(current)
        current += spacing_m
    if targets[-1] != total_distance_m:
        targets.append(total_distance_m)

    resampled_points: list[TrackPoint] = []
    resampled_distances: list[float] = []
    segment_index = 1

    for target_distance in targets:
        while (
            segment_index < len(raw_distances) - 1
            and raw_distances[segment_index] < target_distance
        ):
            segment_index += 1

        previous_index = max(0, segment_index - 1)
        next_index = segment_index
        previous_distance = raw_distances[previous_index]
        next_distance = raw_distances[next_index]
        span = next_distance - previous_distance

        if span == 0:
            fraction = 0.0
        else:
            fraction = (target_distance - previous_distance) / span

        resampled_points.append(
            interpolate_point(points[previous_index], points[next_index], fraction)
        )
        resampled_distances.append(target_distance)

    return resampled_points, resampled_distances


def moving_average(values: Sequence[float], window_size: int) -> list[float]:
    if not values:
        return []
    if window_size <= 1:
        return list(values)

    radius = window_size // 2
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def suppress_outliers(values: Sequence[float], max_change_m: float) -> list[float]:
    if not values:
        return []
    cleaned = list(values)
    for index in range(1, len(cleaned)):
        change = abs(cleaned[index] - cleaned[index - 1])
        if change <= max_change_m:
            continue
        if index < len(cleaned) - 1:
            cleaned[index] = (cleaned[index - 1] + cleaned[index + 1]) / 2
        else:
            cleaned[index] = cleaned[index - 1]
    return cleaned


def significant_extremes(elevations_m: Sequence[float], threshold_m: float) -> list[int]:
    """Indices of the first point, every reversal of at least ``threshold_m`` and the last point."""
    if len(elevations_m) < 2:
        return list(range(len(elevations_m)))

    nodes = [0]
    candidate = 0
    direction = 0
    for index in range(1, len(elevations_m)):
        elevation = elevations_m[index]
        if direction == 0:
            if abs(elevation - elevations_m[0]) >= threshold_m:
                direction = 1 if elevation > elevations_m[0] else -1
                candidate = index
            continue
        if direction * (elevation - elevations_m[candidate]) > 0:
            candidate = index
        elif direction * (elevations_m[candidate] - elevation) >= threshold_m:
            nodes.append(candidate)
            direction = -direction
            candidate = index

    last_index = len(elevations_m) - 1
    if candidate not in (nodes[-1], last_index):
        nodes.append(candidate)
    if nodes[-1] != last_index:
        nodes.append(last_index)
    return nodes


def climb_statistics(
    elevations_m: Sequence[float],
    threshold_m: float,
) -> tuple[float, float, list[float]]:
    """Total ascent, total descent and the cumulative ascent at every point.

    Only legs between significant reversals count, so noise below the threshold adds
    nothing, while a long gentle climb counts in full. Ascent minus descent equals the
    net elevation difference of the track.
    """
    if not elevations_m:
        return 0.0, 0.0, []

    nodes = significant_extremes(elevations_m, threshold_m)
    total_ascent_m = 0.0
    total_descent_m = 0.0
    cumulative = [0.0] * len(elevations_m)
    for start, end in zip(nodes, nodes[1:]):
        leg_height = elevations_m[end] - elevations_m[start]
        if leg_height > 0:
            highest = elevations_m[start]
            for index in range(start + 1, end + 1):
                highest = max(highest, elevations_m[index])
                cumulative[index] = total_ascent_m + min(highest - elevations_m[start], leg_height)
            total_ascent_m += leg_height
        else:
            for index in range(start + 1, end + 1):
                cumulative[index] = total_ascent_m
            total_descent_m += -leg_height
    return total_ascent_m, total_descent_m, cumulative


def calculate_total_climb(
    elevations_m: Sequence[float],
    threshold_m: float,
) -> tuple[float, float, float]:
    if len(elevations_m) < 2:
        return 0.0, 0.0, 0.0

    total_ascent_m, total_descent_m, _ = climb_statistics(elevations_m, threshold_m)
    net_difference_m = elevations_m[-1] - elevations_m[0]
    return total_ascent_m, total_descent_m, net_difference_m


def extract_embedded_elevations(points: Sequence[TrackPoint]) -> list[float]:
    elevations = [point.elevation_m for point in points]
    if any(elevation is None for elevation in elevations):
        raise ValueError("The GPX file does not contain elevation data for all track points.")
    return [float(elevation) for elevation in elevations if elevation is not None]


def lookup_official_elevations(
    points: Sequence[TrackPoint],
    dataset: str,
    cache_db: Path,
    base_url: str,
    interpolation: str,
    timeout_seconds: float,
) -> tuple[list[float], str, str]:
    with ElevationCache(cache_db) as cache:
        client = OpenTopoDataClient(
            dataset=dataset,
            base_url=base_url,
            interpolation=interpolation,
            timeout_seconds=timeout_seconds,
            cache=cache,
        )
        elevations = client.lookup(points)

    resolved = []
    for point, elevation in zip(points, elevations):
        if elevation is None:
            if point.elevation_m is None:
                raise ElevationServiceError(
                    "Elevation service returned gaps and the GPX file cannot fill them."
                )
            resolved.append(point.elevation_m)
        else:
            resolved.append(float(elevation))

    label = "official-dem" if all(value is not None for value in elevations) else "official-dem+gpx-fallback"
    return resolved, label, dataset


def lookup_terrain_tile_elevations(
    points: Sequence[TrackPoint],
    cache_dir: Path,
    timeout_seconds: float,
) -> list[float]:
    client = TerrainTileClient(cache_dir, timeout_seconds=timeout_seconds)
    return client.lookup(points)


def analyze_track(
    points: Sequence[TrackPoint],
    elevation_source: str,
    resample_distance_m: float,
    threshold_m: float,
    window_size: int,
    max_change_m: float,
    dataset: str,
    cache_db: Path,
    base_url: str,
    interpolation: str,
    timeout_seconds: float,
    structures: Sequence[StructureSpan] = (),
    deck_point_sets: DeckPointSets | None = None,
) -> AnalysisResult:
    if structures and elevation_source == "gpx":
        raise ValueError("Bridge and tunnel sections can only correct terrain model elevations.")

    raw_distances_m = cumulative_distances(points)
    raw_elevations_m = extract_embedded_elevations(points)
    average_point_spacing_m = (
        raw_distances_m[-1] / max(1, len(points) - 1)
        if len(points) > 1
        else 0.0
    )

    if elevation_source == "gpx":
        effective_spacing_m = max(resample_distance_m, average_point_spacing_m)
        resampled_points, analysis_distances_m = resample_track(points, effective_spacing_m)
        analysis_elevations_m = extract_embedded_elevations(resampled_points)
        source_label = "gpx-embedded"
        dataset_label = None
    else:
        resampled_points, analysis_distances_m = resample_track(points, resample_distance_m)
        try:
            if elevation_source == "opentopodata":
                analysis_elevations_m, source_label, dataset_label = lookup_official_elevations(
                    points=resampled_points,
                    dataset=dataset,
                    cache_db=cache_db,
                    base_url=base_url,
                    interpolation=interpolation,
                    timeout_seconds=timeout_seconds,
                )
            else:
                analysis_elevations_m = lookup_terrain_tile_elevations(
                    resampled_points,
                    cache_dir=cache_db.parent / TERRAIN_TILE_CACHE_DIRNAME,
                    timeout_seconds=timeout_seconds,
                )
                source_label = TERRAIN_TILES_SOURCE
                dataset_label = None
        except ElevationServiceError:
            if elevation_source != "auto":
                raise
            warning_message = (
                "Terrain tile lookup failed; falling back to GPX elevations"
                + (" without bridge and tunnel correction. " if structures else ". ")
                + "Use --elevation-source gpx to suppress this warning."
            )
            warnings.warn(warning_message, RuntimeWarning)
            effective_spacing_m = max(resample_distance_m, average_point_spacing_m)
            resampled_points, analysis_distances_m = resample_track(points, effective_spacing_m)
            analysis_elevations_m = extract_embedded_elevations(resampled_points)
            source_label = "gpx-fallback"
            dataset_label = None
        else:
            analysis_elevations_m = apply_structures(
                resampled_points,
                analysis_distances_m,
                analysis_elevations_m,
                structures,
                deck_point_sets or {},
            )

    cleaned_elevations_m = suppress_outliers(analysis_elevations_m, max_change_m=max_change_m)
    smoothed_elevations_m = moving_average(cleaned_elevations_m, window_size=window_size)
    total_ascent_m, total_descent_m, net_difference_m = calculate_total_climb(
        smoothed_elevations_m,
        threshold_m=threshold_m,
    )

    total_distance_m = raw_distances_m[-1]
    return AnalysisResult(
        source_label=source_label,
        dataset_label=dataset_label,
        total_distance_m=total_distance_m,
        total_ascent_m=total_ascent_m,
        total_descent_m=total_descent_m,
        net_difference_m=net_difference_m,
        average_point_spacing_m=average_point_spacing_m,
        raw_point_count=len(points),
        analysis_point_count=len(resampled_points),
        raw_distances_m=raw_distances_m,
        raw_elevations_m=raw_elevations_m,
        analysis_distances_m=analysis_distances_m,
        analysis_elevations_m=smoothed_elevations_m,
    )


def detect_circles(
    waypoints: Sequence[TrackPoint],
    threshold_km: float = 0.005,
    min_distance_m: float = 3.0,
) -> list[tuple[int, int]]:
    visited_segments: set[int] = set()
    circles: list[tuple[int, int]] = []
    point_count = len(waypoints)

    for start_index in range(point_count):
        if start_index in visited_segments:
            continue

        for end_index in range(start_index + 1, point_count):
            distance_km = haversine_distance_m(waypoints[start_index], waypoints[end_index]) / 1000.0
            if distance_km >= threshold_km:
                continue
            if calculate_circle_distance(waypoints, start_index, end_index) < min_distance_m:
                break
            visited_segments.update(range(start_index, end_index + 1))
            circles.append((start_index, end_index))
            break

        if circles:
            break

    return circles


def calculate_circle_distance(
    waypoints: Sequence[TrackPoint],
    start_index: int,
    end_index: int,
) -> float:
    total_distance_m = 0.0
    for index in range(start_index, end_index):
        total_distance_m += haversine_distance_m(waypoints[index], waypoints[index + 1])
    return total_distance_m


def plot_elevation_profile(
    result: AnalysisResult,
    circles: Sequence[tuple[int, int]],
    plot_title: str,
) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print(
            "matplotlib is not installed, skipping the plot. Install requirements.txt to enable plotting.",
            file=sys.stderr,
        )
        return

    figure, primary_axis = plt.subplots(figsize=(12, 6))
    primary_axis.plot(
        result.raw_distances_m,
        result.raw_elevations_m,
        label="GPX elevation",
        color="lightgray",
        linewidth=1.0,
    )
    primary_axis.plot(
        result.analysis_distances_m,
        result.analysis_elevations_m,
        label="Analyzed elevation",
        color="blue",
        linewidth=1.4,
    )
    primary_axis.set_xlabel("Distanz (m)")
    primary_axis.set_ylabel("Hoehe (m)")
    primary_axis.grid(True)

    slopes_percent = [0.0]
    for previous_distance, current_distance, previous_ele, current_ele in zip(
        result.analysis_distances_m,
        result.analysis_distances_m[1:],
        result.analysis_elevations_m,
        result.analysis_elevations_m[1:],
    ):
        delta_distance = current_distance - previous_distance
        delta_elevation = current_ele - previous_ele
        slopes_percent.append((delta_elevation / delta_distance) * 100 if delta_distance else 0.0)

    secondary_axis = primary_axis.twinx()
    secondary_axis.set_ylabel("Steigung (%)")
    secondary_axis.plot(
        result.analysis_distances_m,
        slopes_percent,
        color="teal",
        linewidth=0.8,
        alpha=0.7,
    )

    total_distance_m = result.raw_distances_m[-1]
    kilometer_ticks = [tick for tick in range(0, int(total_distance_m) + 1000, 5000)]
    primary_axis.set_xticks(kilometer_ticks)
    primary_axis.set_xticklabels([str(int(tick / 1000)) for tick in kilometer_ticks])

    info = "\n".join(
        [
            f"Quelle: {result.source_label}",
            f"Datensatz: {result.dataset_label or '-'}",
            f"Gesamtanstieg: {int(round(result.total_ascent_m))} m",
            f"Gesamtabstieg: {int(round(result.total_descent_m))} m",
            f"Netto-Hoehenunterschied: {int(round(result.net_difference_m))} m",
            f"Analysepunkte: {result.analysis_point_count}",
        ]
    )
    primary_axis.text(
        total_distance_m / 2,
        min(result.analysis_elevations_m) + 6,
        info,
        fontsize=8,
        verticalalignment="bottom",
        horizontalalignment="center",
        bbox={"boxstyle": "round", "facecolor": "wheat", "alpha": 0.9},
    )

    for index, (start, end) in enumerate(circles, start=1):
        primary_axis.axvline(
            x=result.raw_distances_m[start],
            color="magenta",
            linestyle="--",
            label=f"Circle start {index}" if index == 1 else "",
        )
        primary_axis.axvline(
            x=result.raw_distances_m[end],
            color="magenta",
            linestyle="--",
            label=f"Circle end {index}" if index == 1 else "",
        )

    primary_axis.legend(loc="upper right")
    plt.title(plot_title)
    plt.tight_layout()
    plt.show()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze total ascent/descent from a GPX track. "
            "By default the script replaces noisy GPX elevations with terrain model elevations."
        )
    )
    parser.add_argument(
        "--gpx-file",
        type=Path,
        default=DEFAULT_GPX_FILE,
        help=f"Path to the GPX file (default: {DEFAULT_GPX_FILE}).",
    )
    parser.add_argument(
        "--elevation-source",
        choices=["auto", TERRAIN_TILES_SOURCE, "opentopodata", "gpx"],
        default="auto",
        help=(
            "Elevation source: auto (terrain tiles with GPX fallback), terrain-tiles, "
            "opentopodata (dataset given by --dataset) or gpx."
        ),
    )
    parser.add_argument(
        "--resample-distance",
        type=float,
        default=DEFAULT_RESAMPLE_DISTANCE_M,
        help=(
            "Horizontal spacing in meters for the analysis profile "
            f"(default: {DEFAULT_RESAMPLE_DISTANCE_M})."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD_M,
        help=(
            "Minimum reversal in meters that separates a climb from a descent "
            f"(default: {DEFAULT_THRESHOLD_M})."
        ),
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=DEFAULT_WINDOW_SIZE,
        help=f"Smoothing window size applied after elevation lookup (default: {DEFAULT_WINDOW_SIZE}).",
    )
    parser.add_argument(
        "--max-change",
        type=float,
        default=DEFAULT_MAX_CHANGE_M,
        help="Maximum plausible point-to-point elevation jump before outlier suppression.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=DEFAULT_DATASET,
        help=(
            "OpenTopoData dataset or comma-separated dataset stack "
            f"(default: {DEFAULT_DATASET})."
        ),
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=DEFAULT_CACHE_DB,
        help=(
            f"SQLite cache file for DEM lookups (default: {DEFAULT_CACHE_DB}). "
            "Terrain tiles are cached in the folder next to it."
        ),
    )
    parser.add_argument(
        "--opentopodata-base-url",
        type=str,
        default="https://api.opentopodata.org/v1",
        help="Base URL for the OpenTopoData API.",
    )
    parser.add_argument(
        "--interpolation",
        choices=["nearest", "bilinear", "cubic"],
        default="bilinear",
        help="Interpolation mode for DEM lookups.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=15.0,
        help="Network timeout for external DEM lookups.",
    )
    parser.add_argument(
        "--course-id",
        type=str,
        default=None,
        help=(
            "Correct the bridge and tunnel sections listed for this course in the structures file "
            "(for example wachau_marathon for the default GPX file)."
        ),
    )
    parser.add_argument(
        "--structures-file",
        type=Path,
        default=DEFAULT_STRUCTURES,
        help=f"Bridge and tunnel sections per course (default: {DEFAULT_STRUCTURES}).",
    )
    parser.add_argument(
        "--deck-points-file",
        type=Path,
        default=DEFAULT_DECK_POINTS,
        help=f"Surveyed bridge deck elevations (default: {DEFAULT_DECK_POINTS}).",
    )
    parser.add_argument(
        "--plot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show the elevation plot when matplotlib is available.",
    )
    args = parser.parse_args()
    if args.course_id is not None and args.elevation_source == "gpx":
        parser.error("--course-id needs terrain model elevations; it cannot be combined with --elevation-source gpx.")
    return args


def main() -> int:
    args = parse_arguments()
    gpx_file = args.gpx_file
    if not gpx_file.is_absolute():
        gpx_file = (SCRIPT_PATH.parent / gpx_file).resolve()

    if not gpx_file.exists():
        raise FileNotFoundError(f"GPX file not found at: {gpx_file}")

    structures: list[StructureSpan] = []
    deck_point_sets: DeckPointSets = {}
    if args.course_id is not None:
        structures_by_course = load_structures(args.structures_file)
        if args.course_id not in structures_by_course:
            raise ValueError(
                f"Unknown course {args.course_id!r} in {args.structures_file}. "
                f"Known courses: {sorted(structures_by_course)}."
            )
        structures = structures_by_course[args.course_id]
        deck_point_sets = load_deck_points(args.deck_points_file)

    track_points = parse_gpx(gpx_file)
    analysis = analyze_track(
        points=track_points,
        elevation_source=args.elevation_source,
        resample_distance_m=args.resample_distance,
        threshold_m=args.threshold,
        window_size=args.window_size,
        max_change_m=args.max_change,
        dataset=args.dataset,
        cache_db=args.cache_db,
        base_url=args.opentopodata_base_url,
        interpolation=args.interpolation,
        timeout_seconds=args.timeout_seconds,
        structures=structures,
        deck_point_sets=deck_point_sets,
    )
    circles = detect_circles(track_points, threshold_km=0.005, min_distance_m=3.0)

    print(f"Gesamtanstieg: {analysis.total_ascent_m:.2f} m")
    print(f"Gesamtabstieg: {analysis.total_descent_m:.2f} m")
    print(f"Netto Hoehenunterschied: {analysis.net_difference_m:.2f} m")
    print(f"Track-Laenge: {analysis.total_distance_m / 1000:.2f} km")
    print(f"Durchschnittliche Distanz zwischen Rohpunkten: {analysis.average_point_spacing_m:.2f} m")
    print(f"Verwendete Hoehenquelle: {analysis.source_label}")
    if analysis.dataset_label is not None:
        print(f"DEM-Datensatz: {analysis.dataset_label}")
    if args.course_id is not None and analysis.source_label != "gpx-fallback":
        print(f"Bruecken und Tunnel korrigiert: {len(structures)} Abschnitte ({args.course_id})")
    print(f"Rohpunkte: {analysis.raw_point_count}")
    print(f"Analysepunkte: {analysis.analysis_point_count}")
    print(f"Detected circles: {circles}")

    if args.plot:
        plot_elevation_profile(
            analysis,
            circles,
            plot_title=f"Hoehenprofil laut {gpx_file.name}",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
