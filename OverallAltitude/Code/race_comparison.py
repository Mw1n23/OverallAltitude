"""Compare ascent and descent of several race courses with one consistent method.

Pipeline per course: GPX track -> 25 m resampling -> terrain elevations from the best
open terrain model of the region -> bridge and tunnel correction -> smoothing ->
ascent/descent from significant reversals (hysteresis) -> cumulative ascent over the
course. Courses, sources and structure spans are described in JSON files next to the
GPX data (``RawMaterial/races``).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .terrain_tiles import TerrainTileClient
from .Track_analysis_05 import (
    ElevationCache,
    OpenTopoDataClient,
    TrackPoint,
    cumulative_distances,
    default_cache_db,
    moving_average,
    parse_gpx,
    resample_track,
    suppress_outliers,
)


RACES_DIR = Path(__file__).resolve().parent.parent / "RawMaterial" / "races"
DEFAULT_MANIFEST = RACES_DIR / "races.json"
DEFAULT_STRUCTURES = RACES_DIR / "structures.json"
DEFAULT_DECK_POINTS = RACES_DIR / "bridge_deck_points.json"
DEFAULT_OUTPUT_DIR = Path("Output")

DISCIPLINES = ("marathon", "triathlon_run", "triathlon_bike")
DOWNLOAD_TYPES = ("file", "zip", "hdsports")
RESAMPLE_DISTANCE_M = 25.0
SMOOTHING_WINDOW = 5
CLIMB_THRESHOLD_M = 2.0
MAX_CHANGE_M = 50.0
DECK_SEARCH_RADIUS_M = 20.0
DECK_MEDIAN_WINDOW = 5
OPENTOPODATA_PREFIX = "opentopodata:"
TERRAIN_TILES_SOURCE = "terrain-tiles"
# The public OpenTopoData endpoint accepts 100 locations per request and one request per second.
OPENTOPODATA_BATCH_SIZE = 100
OPENTOPODATA_PAUSE_SECONDS = 1.1


@dataclass(frozen=True)
class CourseSpec:
    course_id: str
    event: str
    discipline: str
    location: str
    gpx_file: Path
    course_version: str
    laps: int
    start_km: float | None
    end_km: float | None
    official_distance_km: float | None
    elevation_source: str
    coarse_terrain_model: bool
    source_name: str
    source_url: str
    download_type: str | None
    download_url: str | None
    note: str


@dataclass(frozen=True)
class StructureSpan:
    kind: str
    start_km: float
    end_km: float
    name: str
    deck_points: str | None
    deck_max_m: float | None


@dataclass(frozen=True)
class CourseProfile:
    spec: CourseSpec
    distances_km: list[float]
    elevations_m: list[float]
    cumulative_ascent_m: list[float]
    total_ascent_m: float
    total_descent_m: float
    net_difference_m: float
    track_distance_km: float

    @property
    def distance_km(self) -> float:
        return self.distances_km[-1]

    @property
    def min_elevation_m(self) -> float:
        return min(self.elevations_m)

    @property
    def max_elevation_m(self) -> float:
        return max(self.elevations_m)


class ThrottledOpenTopoDataClient(OpenTopoDataClient):
    """Keeps uncached lookups within the rate limit of the public endpoint."""

    def _fetch_batch(self, points: Sequence[TrackPoint]) -> list[float | None]:
        time.sleep(OPENTOPODATA_PAUSE_SECONDS)
        return super()._fetch_batch(points)


def load_manifest(path: Path) -> list[CourseSpec]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    specs: list[CourseSpec] = []
    for entry in payload["courses"]:
        course_id = entry["id"]
        discipline = entry["discipline"]
        if discipline not in DISCIPLINES:
            raise ValueError(f"Course {course_id}: unknown discipline {discipline!r}.")
        elevation_source = entry["elevation_source"]
        if elevation_source != TERRAIN_TILES_SOURCE and not elevation_source.startswith(
            OPENTOPODATA_PREFIX
        ):
            raise ValueError(f"Course {course_id}: unknown elevation source {elevation_source!r}.")
        download = entry.get("download")
        if download is not None and download["type"] not in DOWNLOAD_TYPES:
            raise ValueError(f"Course {course_id}: unknown download type {download['type']!r}.")
        laps = entry.get("laps", 1)
        if laps < 1:
            raise ValueError(f"Course {course_id}: laps must be at least 1.")
        specs.append(
            CourseSpec(
                course_id=course_id,
                event=entry["event"],
                discipline=discipline,
                location=entry["location"],
                gpx_file=(path.parent / entry["gpx"]).resolve(),
                course_version=entry["course_version"],
                laps=laps,
                start_km=entry.get("start_km"),
                end_km=entry.get("end_km"),
                official_distance_km=entry.get("official_distance_km"),
                elevation_source=elevation_source,
                coarse_terrain_model=entry.get("coarse_terrain_model", False),
                source_name=entry["source"]["name"],
                source_url=entry["source"]["url"],
                download_type=None if download is None else download["type"],
                download_url=None if download is None else download["url"],
                note=entry.get("note", ""),
            )
        )

    course_ids = [spec.course_id for spec in specs]
    if len(set(course_ids)) != len(course_ids):
        raise ValueError("Course ids in the manifest must be unique.")
    return specs


def load_structures(path: Path) -> dict[str, list[StructureSpan]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    structures: dict[str, list[StructureSpan]] = {}
    for course_id, entries in payload["courses"].items():
        spans = []
        for entry in entries:
            if entry["kind"] not in ("bridge", "tunnel"):
                raise ValueError(f"Course {course_id}: unknown structure kind {entry['kind']!r}.")
            if entry["end_km"] < entry["start_km"]:
                raise ValueError(f"Course {course_id}: structure span ends before it starts.")
            spans.append(
                StructureSpan(
                    kind=entry["kind"],
                    start_km=entry["start_km"],
                    end_km=entry["end_km"],
                    name=entry.get("name", ""),
                    deck_points=entry.get("deck_points"),
                    deck_max_m=entry.get("deck_max_m"),
                )
            )
        structures[course_id] = spans
    return structures


def load_deck_points(path: Path) -> dict[str, list[tuple[float, float, float]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: [(latitude, longitude, elevation) for latitude, longitude, elevation in dataset["points"]]
        for name, dataset in payload["datasets"].items()
    }


def select_track_section(
    points: Sequence[TrackPoint],
    start_km: float | None,
    end_km: float | None,
) -> list[TrackPoint]:
    """Cut a section out of a track that holds more than one discipline."""
    if start_km is None and end_km is None:
        return list(points)
    distances_m = cumulative_distances(points)
    lower_m = 0.0 if start_km is None else start_km * 1000.0
    upper_m = distances_m[-1] if end_km is None else end_km * 1000.0
    section = [
        point for point, distance_m in zip(points, distances_m) if lower_m <= distance_m <= upper_m
    ]
    if len(section) < 2:
        raise ValueError("The selected track section contains fewer than two points.")
    return section


def lookup_elevations(
    points: Sequence[TrackPoint],
    elevation_source: str,
    cache_dir: Path,
) -> list[float]:
    if elevation_source == TERRAIN_TILES_SOURCE:
        return TerrainTileClient(cache_dir / "terrain-tiles").lookup(points)

    dataset = elevation_source[len(OPENTOPODATA_PREFIX) :]
    with ElevationCache(cache_dir / default_cache_db().name) as cache:
        client = ThrottledOpenTopoDataClient(
            dataset=dataset,
            batch_size=OPENTOPODATA_BATCH_SIZE,
            timeout_seconds=30.0,
            cache=cache,
        )
        elevations = client.lookup(points)
    if any(elevation is None for elevation in elevations):
        raise ValueError(f"Dataset {dataset} does not cover the whole track.")
    return [float(elevation) for elevation in elevations if elevation is not None]


def running_median(values: Sequence[float], window_size: int) -> list[float]:
    """Median filter whose window shrinks symmetrically towards both ends."""
    result = []
    for index in range(len(values)):
        radius = min(window_size // 2, index, len(values) - 1 - index)
        window = sorted(values[index - radius : index + radius + 1])
        result.append(window[radius])
    return result


def deck_elevations(
    points: Sequence[TrackPoint],
    deck_points: Sequence[tuple[float, float, float]],
    deck_max_m: float | None,
) -> list[float | None]:
    """Highest surveyed deck elevation near each track point, if there is one."""
    if not points:
        return []
    meters_per_degree_lat = 110_540.0
    meters_per_degree_lon = 111_320.0 * math.cos(math.radians(points[0].latitude))
    cell_m = DECK_SEARCH_RADIUS_M
    grid: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for latitude, longitude, elevation in deck_points:
        if deck_max_m is not None and elevation > deck_max_m:
            continue
        x = longitude * meters_per_degree_lon
        y = latitude * meters_per_degree_lat
        grid.setdefault((int(x // cell_m), int(y // cell_m)), []).append((x, y, elevation))

    result: list[float | None] = []
    for point in points:
        x = point.longitude * meters_per_degree_lon
        y = point.latitude * meters_per_degree_lat
        cell_x = int(x // cell_m)
        cell_y = int(y // cell_m)
        best: float | None = None
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for deck_x, deck_y, elevation in grid.get((cell_x + offset_x, cell_y + offset_y), []):
                    if math.hypot(deck_x - x, deck_y - y) > DECK_SEARCH_RADIUS_M:
                        continue
                    if best is None or elevation > best:
                        best = elevation
        result.append(best)
    return result


def fill_gaps_linear(values: Sequence[float | None]) -> list[float]:
    known = [(index, value) for index, value in enumerate(values) if value is not None]
    if not known:
        raise ValueError("Cannot interpolate a series without any known value.")
    filled: list[float] = []
    position = 0
    for index in range(len(values)):
        while position + 1 < len(known) and known[position + 1][0] <= index:
            position += 1
        left_index, left_value = known[position]
        if index <= left_index or position + 1 == len(known):
            filled.append(left_value)
            continue
        right_index, right_value = known[position + 1]
        fraction = (index - left_index) / (right_index - left_index)
        filled.append(left_value + (right_value - left_value) * fraction)
    return filled


def apply_structures(
    points: Sequence[TrackPoint],
    distances_m: Sequence[float],
    elevations_m: Sequence[float],
    spans: Sequence[StructureSpan],
    deck_point_sets: dict[str, list[tuple[float, float, float]]],
) -> list[float]:
    """Correct terrain elevations where the course is on a bridge or in a tunnel.

    A terrain model follows the ground: below a bridge it drops to the valley or water
    level, above a tunnel it climbs over the hill. Between the two ends of a span the
    course is assumed to follow the straight connection (chord). A bridge never lies
    below the terrain and a tunnel never above it, so a span that was flagged by
    mistake changes nothing where terrain and course agree. Long-span bridges with a
    surveyed deck profile use those elevations instead of the chord.
    """
    corrected = list(elevations_m)
    last_index = len(corrected) - 1
    tolerance_m = 1.0
    for span in spans:
        inside = [
            index
            for index, distance_m in enumerate(distances_m)
            if span.start_km * 1000.0 - tolerance_m <= distance_m <= span.end_km * 1000.0 + tolerance_m
        ]
        if not inside:
            raise ValueError(f"Structure span {span.name!r} lies outside the track.")
        anchor_start = max(0, inside[0] - 1)
        anchor_end = min(last_index, inside[-1] + 1)
        if anchor_end - anchor_start < 2:
            continue
        start_elevation = elevations_m[anchor_start]
        end_elevation = elevations_m[anchor_end]
        interior = range(anchor_start + 1, anchor_end)

        if span.deck_points is not None:
            if span.deck_points not in deck_point_sets:
                raise ValueError(f"Unknown deck point set {span.deck_points!r}.")
            surveyed = deck_elevations(
                [points[index] for index in interior],
                deck_point_sets[span.deck_points],
                span.deck_max_m,
            )
            deck = fill_gaps_linear([start_elevation, *surveyed, end_elevation])
            deck = running_median(deck, DECK_MEDIAN_WINDOW)[1:-1]
            for index, deck_elevation in zip(interior, deck):
                corrected[index] = max(elevations_m[index], deck_elevation)
            continue

        for index in interior:
            fraction = (index - anchor_start) / (anchor_end - anchor_start)
            chord = start_elevation + (end_elevation - start_elevation) * fraction
            if span.kind == "bridge":
                corrected[index] = max(elevations_m[index], chord)
            else:
                corrected[index] = min(elevations_m[index], chord)
    return corrected


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
    net elevation difference of the course.
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


def repeat_laps(
    distances_m: Sequence[float],
    elevations_m: Sequence[float],
    laps: int,
) -> tuple[list[float], list[float]]:
    lap_length_m = distances_m[-1]
    all_distances = list(distances_m)
    all_elevations = list(elevations_m)
    for lap in range(1, laps):
        all_distances.extend(distance_m + lap * lap_length_m for distance_m in distances_m[1:])
        all_elevations.extend(elevations_m[1:])
    return all_distances, all_elevations


def analyze_course(
    spec: CourseSpec,
    spans: Sequence[StructureSpan],
    deck_point_sets: dict[str, list[tuple[float, float, float]]],
    cache_dir: Path,
) -> CourseProfile:
    track = select_track_section(parse_gpx(spec.gpx_file), spec.start_km, spec.end_km)
    points, distances_m = resample_track(track, RESAMPLE_DISTANCE_M)
    terrain_m = lookup_elevations(points, spec.elevation_source, cache_dir)
    corrected_m = apply_structures(points, distances_m, terrain_m, spans, deck_point_sets)
    cleaned_m = suppress_outliers(corrected_m, max_change_m=MAX_CHANGE_M)
    smoothed_m = moving_average(cleaned_m, window_size=SMOOTHING_WINDOW)

    course_distances_m, course_elevations_m = repeat_laps(distances_m, smoothed_m, spec.laps)
    track_distance_km = course_distances_m[-1] / 1000.0
    scale = 1.0
    if spec.official_distance_km is not None:
        scale = spec.official_distance_km / track_distance_km

    total_ascent_m, total_descent_m, cumulative_ascent_m = climb_statistics(
        course_elevations_m,
        CLIMB_THRESHOLD_M,
    )
    return CourseProfile(
        spec=spec,
        distances_km=[distance_m / 1000.0 * scale for distance_m in course_distances_m],
        elevations_m=course_elevations_m,
        cumulative_ascent_m=cumulative_ascent_m,
        total_ascent_m=total_ascent_m,
        total_descent_m=total_descent_m,
        net_difference_m=course_elevations_m[-1] - course_elevations_m[0],
        track_distance_km=track_distance_km,
    )


def analyze_courses(
    manifest: Path = DEFAULT_MANIFEST,
    structures_file: Path = DEFAULT_STRUCTURES,
    deck_points_file: Path = DEFAULT_DECK_POINTS,
    cache_dir: Path | None = None,
) -> list[CourseProfile]:
    if cache_dir is None:
        cache_dir = default_cache_db().parent
    structures = load_structures(structures_file)
    deck_point_sets = load_deck_points(deck_points_file)
    specs = load_manifest(manifest)
    unknown = set(structures) - {spec.course_id for spec in specs}
    if unknown:
        raise ValueError(f"Structure spans refer to unknown courses: {sorted(unknown)}.")
    missing = sorted({spec.gpx_file.name for spec in specs if not spec.gpx_file.exists()})
    if missing:
        raise FileNotFoundError(
            f"Course tracks are missing: {', '.join(missing)}. They are not part of the repository; "
            "download them with overall-altitude-fetch-tracks."
        )
    return [
        analyze_course(spec, structures.get(spec.course_id, []), deck_point_sets, cache_dir)
        for spec in specs
    ]


def write_summary_csv(profiles: Sequence[CourseProfile], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "id",
                "event",
                "discipline",
                "location",
                "course_version",
                "laps",
                "distance_km",
                "track_distance_km",
                "total_ascent_m",
                "total_descent_m",
                "net_difference_m",
                "min_elevation_m",
                "max_elevation_m",
                "elevation_source",
                "track_source",
            ]
        )
        for profile in profiles:
            spec = profile.spec
            writer.writerow(
                [
                    spec.course_id,
                    spec.event,
                    spec.discipline,
                    spec.location,
                    spec.course_version,
                    spec.laps,
                    f"{profile.distance_km:.3f}",
                    f"{profile.track_distance_km:.3f}",
                    f"{profile.total_ascent_m:.0f}",
                    f"{profile.total_descent_m:.0f}",
                    f"{profile.net_difference_m:.0f}",
                    f"{profile.min_elevation_m:.0f}",
                    f"{profile.max_elevation_m:.0f}",
                    spec.elevation_source,
                    spec.source_name,
                ]
            )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare total ascent/descent of the race courses listed in a manifest "
            "and draw the comparison figure."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Course manifest (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--structures",
        type=Path,
        default=DEFAULT_STRUCTURES,
        help=f"Bridge and tunnel spans per course (default: {DEFAULT_STRUCTURES}).",
    )
    parser.add_argument(
        "--deck-points",
        type=Path,
        default=DEFAULT_DECK_POINTS,
        help=f"Surveyed bridge deck elevations (default: {DEFAULT_DECK_POINTS}).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_db().parent,
        help=f"Directory for cached elevation lookups (default: {default_cache_db().parent}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the summary table and the figure (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--plot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Draw the comparison figure (needs matplotlib).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    profiles = analyze_courses(
        manifest=args.manifest,
        structures_file=args.structures,
        deck_points_file=args.deck_points,
        cache_dir=args.cache_dir,
    )

    summary_file = args.output_dir / "race_comparison.csv"
    write_summary_csv(profiles, summary_file)
    for profile in profiles:
        print(
            f"{profile.spec.event:<38} {profile.spec.discipline:<15} "
            f"{profile.distance_km:7.2f} km  "
            f"+{profile.total_ascent_m:5.0f} m  -{profile.total_descent_m:5.0f} m  "
            f"netto {profile.net_difference_m:+5.0f} m"
        )
    print(f"Tabelle: {summary_file}")

    if args.plot:
        from .race_figure import save_comparison_figure

        for figure_file in save_comparison_figure(profiles, args.output_dir):
            print(f"Grafik: {figure_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
