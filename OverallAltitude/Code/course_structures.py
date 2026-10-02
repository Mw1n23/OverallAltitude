"""Find bridge and tunnel sections of a course from OpenStreetMap ways.

Helper for maintaining ``RawMaterial/races/structures.json``. It lists every section
where the resampled track runs along a way tagged as bridge or tunnel. Short bridges
can be taken over as they are; tunnels and long bridges have to be checked by hand,
because OpenStreetMap cannot tell whether the course uses a tunnel or the road above it.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .race_comparison import (
    DEFAULT_MANIFEST,
    RESAMPLE_DISTANCE_M,
    load_manifest,
    select_track_section,
)
from .Track_analysis_05 import TrackPoint, parse_gpx, resample_track


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CELL_DEGREES = 0.04
CELL_PADDING_DEGREES = 0.001
MATCH_DISTANCE_M = 25.0
MATCH_ANGLE_DEGREES = 30.0
REVIEW_LENGTH_M = 150.0
IGNORED_TUNNEL_VALUES = ("no", "building_passage")
METERS_PER_DEGREE_LAT = 110_540.0
METERS_PER_DEGREE_LON_AT_EQUATOR = 111_320.0


@dataclass(frozen=True)
class StructureWay:
    way_id: int
    kind: str
    name: str
    geometry: list[tuple[float, float]]


@dataclass(frozen=True)
class StructureCandidate:
    kind: str
    start_km: float
    end_km: float
    names: tuple[str, ...]

    @property
    def needs_review(self) -> bool:
        length_m = (self.end_km - self.start_km) * 1000.0
        return self.kind == "tunnel" or length_m >= REVIEW_LENGTH_M


class OverpassError(RuntimeError):
    """Raised when the Overpass API does not answer a query."""


def overpass_query(
    query: str,
    url: str = OVERPASS_URL,
    timeout_seconds: float = 90.0,
    max_retries: int = 5,
) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        headers={"User-Agent": "OverallAltitude/2.0"},
    )
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == max_retries:
                raise OverpassError(f"Overpass API did not answer: {exc}") from exc
            time.sleep(5.0 * (attempt + 1))
    raise OverpassError("Overpass API did not answer.")


def fetch_structure_ways(points: Sequence[TrackPoint], url: str = OVERPASS_URL) -> list[StructureWay]:
    """Bridge and tunnel ways in the map cells the track passes through."""
    cells = sorted(
        {
            (math.floor(point.latitude / CELL_DEGREES), math.floor(point.longitude / CELL_DEGREES))
            for point in points
        }
    )
    ways: dict[int, StructureWay] = {}
    for cell_lat, cell_lon in cells:
        south = cell_lat * CELL_DEGREES - CELL_PADDING_DEGREES
        west = cell_lon * CELL_DEGREES - CELL_PADDING_DEGREES
        north = (cell_lat + 1) * CELL_DEGREES + CELL_PADDING_DEGREES
        east = (cell_lon + 1) * CELL_DEGREES + CELL_PADDING_DEGREES
        box = f"({south:.4f},{west:.4f},{north:.4f},{east:.4f})"
        query = (
            "[out:json][timeout:60];"
            f'(way["highway"]["bridge"]{box};way["highway"]["tunnel"]{box};);'
            "out tags geom;"
        )
        for element in overpass_query(query, url=url)["elements"]:
            tags = element["tags"]
            if tags.get("bridge", "no") != "no":
                kind = "bridge"
            elif tags.get("tunnel", "no") not in IGNORED_TUNNEL_VALUES:
                kind = "tunnel"
            else:
                continue
            ways[element["id"]] = StructureWay(
                way_id=element["id"],
                kind=kind,
                name=tags.get("name") or tags.get("bridge:name") or tags.get("tunnel:name") or "",
                geometry=[(node["lat"], node["lon"]) for node in element["geometry"]],
            )
        time.sleep(1.5)
    return list(ways.values())


def detect_structure_spans(
    points: Sequence[TrackPoint],
    distances_m: Sequence[float],
    ways: Sequence[StructureWay],
) -> list[StructureCandidate]:
    """Sections where the track runs along (not across) a bridge or tunnel way."""
    if not points:
        return []
    meters_per_degree_lon = METERS_PER_DEGREE_LON_AT_EQUATOR * math.cos(
        math.radians(sum(point.latitude for point in points) / len(points))
    )

    def project(latitude: float, longitude: float) -> tuple[float, float]:
        return longitude * meters_per_degree_lon, latitude * METERS_PER_DEGREE_LAT

    cell_m = 100.0
    segments: list[tuple[tuple[float, float], tuple[float, float], int]] = []
    grid: dict[tuple[int, int], set[int]] = {}
    for way_index, way in enumerate(ways):
        nodes = [project(latitude, longitude) for latitude, longitude in way.geometry]
        for start, end in zip(nodes, nodes[1:]):
            segment_index = len(segments)
            segments.append((start, end, way_index))
            steps = int(math.hypot(end[0] - start[0], end[1] - start[1]) // (cell_m / 2)) + 1
            for step in range(steps + 1):
                fraction = step / steps
                x = start[0] + (end[0] - start[0]) * fraction
                y = start[1] + (end[1] - start[1]) * fraction
                grid.setdefault((int(x // cell_m), int(y // cell_m)), set()).add(segment_index)

    track = [project(point.latitude, point.longitude) for point in points]
    matches: list[int | None] = []
    for index, (x, y) in enumerate(track):
        before = track[max(0, index - 1)]
        after = track[min(len(track) - 1, index + 1)]
        heading_x = after[0] - before[0]
        heading_y = after[1] - before[1]
        heading_length = math.hypot(heading_x, heading_y)
        cell_x = int(x // cell_m)
        cell_y = int(y // cell_m)
        candidates: set[int] = set()
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                candidates |= grid.get((cell_x + offset_x, cell_y + offset_y), set())

        best_distance: float | None = None
        best_way: int | None = None
        for segment_index in candidates:
            start, end, way_index = segments[segment_index]
            delta_x = end[0] - start[0]
            delta_y = end[1] - start[1]
            length_squared = delta_x * delta_x + delta_y * delta_y
            if length_squared == 0:
                continue
            fraction = ((x - start[0]) * delta_x + (y - start[1]) * delta_y) / length_squared
            fraction = max(0.0, min(1.0, fraction))
            distance = math.hypot(x - (start[0] + fraction * delta_x), y - (start[1] + fraction * delta_y))
            if distance > MATCH_DISTANCE_M:
                continue
            if heading_length > 0:
                cosine = abs(heading_x * delta_x + heading_y * delta_y) / (
                    heading_length * math.sqrt(length_squared)
                )
                if math.degrees(math.acos(min(1.0, cosine))) > MATCH_ANGLE_DEGREES:
                    continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_way = way_index
        matches.append(best_way)

    spans: list[StructureCandidate] = []
    index = 0
    while index < len(matches):
        if matches[index] is None:
            index += 1
            continue
        last = index
        # A single unmatched sample between two matched ones does not split a span.
        while last + 1 < len(matches) and (
            matches[last + 1] is not None or (last + 2 < len(matches) and matches[last + 2] is not None)
        ):
            last += 1
        way_indices = {way_index for way_index in matches[index : last + 1] if way_index is not None}
        kinds = {ways[way_index].kind for way_index in way_indices}
        names = sorted({ways[way_index].name for way_index in way_indices if ways[way_index].name})
        spans.append(
            StructureCandidate(
                kind="bridge" if "bridge" in kinds else "tunnel",
                start_km=round(distances_m[index] / 1000.0, 3),
                end_km=round(distances_m[last] / 1000.0, 3),
                names=tuple(names),
            )
        )
        index = last + 1
    return spans


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List bridge and tunnel sections of one course as entries for structures.json. "
            "Tunnels and long bridges are listed separately and need a manual check."
        )
    )
    parser.add_argument("--course", required=True, help="Course id from the manifest.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Course manifest (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument("--overpass-url", default=OVERPASS_URL, help="Overpass API endpoint.")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    specs = {spec.course_id: spec for spec in load_manifest(args.manifest)}
    if args.course not in specs:
        raise ValueError(f"Unknown course {args.course!r}. Known courses: {sorted(specs)}.")
    spec = specs[args.course]

    track = select_track_section(parse_gpx(spec.gpx_file), spec.start_km, spec.end_km)
    points, distances_m = resample_track(track, RESAMPLE_DISTANCE_M)
    ways = fetch_structure_ways(points, url=args.overpass_url)
    spans = detect_structure_spans(points, distances_m, ways)

    for heading, selected in (
        ("Short bridges (can be used as they are):", [span for span in spans if not span.needs_review]),
        ("Tunnels and long bridges (check by hand):", [span for span in spans if span.needs_review]),
    ):
        print(heading)
        for span in selected:
            entry = {
                "kind": span.kind,
                "start_km": span.start_km,
                "end_km": span.end_km,
                "name": " / ".join(span.names[:2]),
            }
            print("  " + json.dumps(entry, ensure_ascii=False) + ",")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
