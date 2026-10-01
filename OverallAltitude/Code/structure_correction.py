"""Correct terrain elevations where a course runs over a bridge or through a tunnel.

A terrain model follows the ground: below a bridge it drops to the valley or water
level, above a tunnel it climbs over the hill. The sections are listed per course in
``RawMaterial/races/structures.json``; ``course_structures`` helps to maintain that file.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .track_types import TrackPoint


RACES_DIR = Path(__file__).resolve().parent.parent / "RawMaterial" / "races"
DEFAULT_STRUCTURES = RACES_DIR / "structures.json"
DEFAULT_DECK_POINTS = RACES_DIR / "bridge_deck_points.json"
DECK_SEARCH_RADIUS_M = 20.0
DECK_MEDIAN_WINDOW = 5

DeckPointSets = dict[str, list[tuple[float, float, float]]]


@dataclass(frozen=True)
class StructureSpan:
    kind: str
    start_km: float
    end_km: float
    name: str
    deck_points: str | None
    deck_max_m: float | None


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


def load_deck_points(path: Path) -> DeckPointSets:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: [(latitude, longitude, elevation) for latitude, longitude, elevation in dataset["points"]]
        for name, dataset in payload["datasets"].items()
    }


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
    deck_point_sets: DeckPointSets,
) -> list[float]:
    """Correct terrain elevations inside the given bridge and tunnel spans.

    Between the two ends of a span the course is assumed to follow the straight
    connection (chord). A bridge never lies below the terrain and a tunnel never above
    it, so a span that was flagged by mistake changes nothing where terrain and course
    agree. Long-span bridges with a surveyed deck profile use those elevations instead
    of the chord.
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
