import importlib.util
import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from OverallAltitude.Code import course_structures, fetch_race_tracks, race_comparison, terrain_tiles
from OverallAltitude.Code.Track_analysis_05 import TrackPoint, cumulative_distances


def straight_track(point_count: int, step_degrees: float = 0.00025) -> list[TrackPoint]:
    """Points on the equator, roughly 27.8 m apart."""
    return [TrackPoint(0.0, index * step_degrees, None) for index in range(point_count)]


def encode_png(width: int, height: int, rgb: bytes, filter_types: list[int]) -> bytes:
    """Minimal PNG encoder that applies the given filter type per row."""

    def paeth(left: int, up: int, up_left: int) -> int:
        estimate = left + up - up_left
        distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
        if distances[0] <= distances[1] and distances[0] <= distances[2]:
            return left
        return up if distances[1] <= distances[2] else up_left

    stride = width * 3
    raw = bytearray()
    previous = bytes(stride)
    for row in range(height):
        line = rgb[row * stride : (row + 1) * stride]
        filter_type = filter_types[row % len(filter_types)]
        raw.append(filter_type)
        for index in range(stride):
            left = line[index - 3] if index >= 3 else 0
            up = previous[index]
            up_left = previous[index - 3] if index >= 3 else 0
            prediction = (0, left, up, (left + up) >> 1, paeth(left, up, up_left))[filter_type]
            raw.append((line[index] - prediction) & 0xFF)
        previous = line

    def chunk(chunk_type: bytes, body: bytes) -> bytes:
        checksum = zlib.crc32(chunk_type + body)
        return struct.pack(">I", len(body)) + chunk_type + body + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        terrain_tiles.PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


class ClimbStatisticsTests(unittest.TestCase):
    def test_gentle_climb_counts_in_full(self) -> None:
        elevations = [100.0 + 0.5 * index for index in range(41)]

        ascent, descent, cumulative = race_comparison.climb_statistics(elevations, threshold_m=2.0)

        self.assertAlmostEqual(ascent, 20.0)
        self.assertAlmostEqual(descent, 0.0)
        self.assertAlmostEqual(cumulative[-1], 20.0)

    def test_noise_below_threshold_adds_nothing(self) -> None:
        elevations = [100.0, 101.0, 100.0, 101.5, 100.2, 101.0, 100.0]

        ascent, descent, _ = race_comparison.climb_statistics(elevations, threshold_m=2.0)

        self.assertAlmostEqual(ascent, 0.0)
        self.assertAlmostEqual(descent, 0.0)

    def test_ascent_minus_descent_equals_net_difference(self) -> None:
        elevations = [100.0, 104.0, 103.0, 110.0, 95.0, 96.0, 99.5, 98.0, 107.0, 106.5]

        ascent, descent, cumulative = race_comparison.climb_statistics(elevations, threshold_m=2.0)

        self.assertAlmostEqual(ascent - descent, elevations[-1] - elevations[0])
        self.assertAlmostEqual(cumulative[-1], ascent)
        self.assertTrue(all(later >= earlier for earlier, later in zip(cumulative, cumulative[1:])))

    def test_significant_extremes_skip_small_reversals(self) -> None:
        elevations = [100.0, 105.0, 104.0, 110.0, 100.0, 101.0, 100.0]

        nodes = race_comparison.significant_extremes(elevations, threshold_m=2.0)

        self.assertEqual(nodes, [0, 3, 4, 6])


class StructureCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.points = straight_track(9)
        self.distances_m = cumulative_distances(self.points)
        self.span_km = (self.distances_m[2] / 1000.0, self.distances_m[6] / 1000.0)

    def span(self, kind: str, **extra) -> race_comparison.StructureSpan:
        return race_comparison.StructureSpan(
            kind=kind,
            start_km=self.span_km[0],
            end_km=self.span_km[1],
            name="test",
            deck_points=extra.get("deck_points"),
            deck_max_m=extra.get("deck_max_m"),
        )

    def test_bridge_spans_the_valley(self) -> None:
        terrain = [50.0, 50.0, 40.0, 10.0, 0.0, 10.0, 42.0, 52.0, 52.0]

        corrected = race_comparison.apply_structures(
            self.points, self.distances_m, terrain, [self.span("bridge")], {}
        )

        self.assertEqual(corrected[:2], [50.0, 50.0])
        self.assertEqual(corrected[7:], [52.0, 52.0])
        for index in range(2, 7):
            self.assertAlmostEqual(corrected[index], 50.0 + 2.0 * (index - 1) / 6)

    def test_tunnel_cuts_through_the_hill(self) -> None:
        terrain = [50.0, 50.0, 60.0, 80.0, 90.0, 80.0, 60.0, 50.0, 50.0]

        corrected = race_comparison.apply_structures(
            self.points, self.distances_m, terrain, [self.span("tunnel")], {}
        )

        self.assertEqual(corrected, [50.0] * 9)

    def test_wrongly_flagged_bridge_keeps_the_hill(self) -> None:
        terrain = [50.0, 50.0, 60.0, 80.0, 90.0, 80.0, 60.0, 50.0, 50.0]

        corrected = race_comparison.apply_structures(
            self.points, self.distances_m, terrain, [self.span("bridge")], {}
        )

        self.assertEqual(corrected, terrain)

    def test_surveyed_deck_replaces_the_chord(self) -> None:
        terrain = [20.0, 20.0, 0.0, 0.0, 0.0, 0.0, 0.0, 20.0, 20.0]
        deck = {
            "decks": [
                (0.0, self.points[index].longitude, elevation)
                for index, elevation in ((2, 30.0), (3, 40.0), (4, 45.0), (5, 40.0), (6, 30.0))
            ]
            + [(0.0, self.points[4].longitude, 60.0)]
        }

        corrected = race_comparison.apply_structures(
            self.points,
            self.distances_m,
            terrain,
            [self.span("bridge", deck_points="decks", deck_max_m=50.0)],
            deck,
        )

        self.assertEqual(corrected, [20.0, 20.0, 30.0, 40.0, 40.0, 40.0, 30.0, 20.0, 20.0])

    def test_span_outside_the_track_is_rejected(self) -> None:
        span = race_comparison.StructureSpan("bridge", 5.0, 6.0, "far away", None, None)

        with self.assertRaises(ValueError):
            race_comparison.apply_structures(self.points, self.distances_m, [0.0] * 9, [span], {})


class TrackHelperTests(unittest.TestCase):
    def test_fill_gaps_linear(self) -> None:
        self.assertEqual(
            race_comparison.fill_gaps_linear([None, 10.0, None, None, 16.0, None]),
            [10.0, 10.0, 12.0, 14.0, 16.0, 16.0],
        )

    def test_repeat_laps_appends_laps_without_duplicate_points(self) -> None:
        distances, elevations = race_comparison.repeat_laps([0.0, 50.0, 100.0], [5.0, 7.0, 5.0], laps=3)

        self.assertEqual(distances, [0.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0])
        self.assertEqual(elevations, [5.0, 7.0, 5.0, 7.0, 5.0, 7.0, 5.0])

    def test_select_track_section_cuts_by_distance(self) -> None:
        points = straight_track(101)
        total_km = cumulative_distances(points)[-1] / 1000.0

        section = race_comparison.select_track_section(points, start_km=total_km / 2, end_km=None)

        self.assertEqual(section[0], points[50])
        self.assertEqual(section[-1], points[-1])

    def test_analyze_course_scales_to_official_distance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            gpx_file = Path(tmpdir) / "course.gpx"
            track_points = "".join(
                f'<trkpt lat="0.0" lon="{index * 0.001:.3f}"><ele>0</ele></trkpt>' for index in range(11)
            )
            gpx_file.write_text(
                '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                f"{track_points}</trkseg></trk></gpx>",
                encoding="utf-8",
            )
            spec = race_comparison.CourseSpec(
                course_id="test",
                event="Test",
                discipline="marathon",
                location="Nowhere",
                gpx_file=gpx_file,
                course_version="1",
                laps=2,
                start_km=None,
                end_km=None,
                official_distance_km=2.0,
                elevation_source="terrain-tiles",
                coarse_terrain_model=False,
                source_name="test",
                source_url="https://example.invalid",
                download_type=None,
                download_url=None,
                note="",
            )

            def hill(points, elevation_source, cache_dir):
                peak = (len(points) - 1) / 2
                return [10.0 - abs(index - peak) * 0.2 for index in range(len(points))]

            with mock.patch.object(race_comparison, "lookup_elevations", side_effect=hill):
                profile = race_comparison.analyze_course(spec, [], {}, Path(tmpdir))

        self.assertAlmostEqual(profile.distance_km, 2.0)
        self.assertGreater(profile.track_distance_km, 2.2)
        self.assertAlmostEqual(profile.net_difference_m, 0.0, places=6)
        self.assertAlmostEqual(profile.total_ascent_m, profile.total_descent_m, places=6)
        # Two laps over a hill of 4.4 m; smoothing takes a little off the top.
        self.assertGreater(profile.total_ascent_m, 7.5)
        self.assertLess(profile.total_ascent_m, 8.8)


class RaceDataTests(unittest.TestCase):
    def test_manifest_structures_and_deck_points_are_consistent(self) -> None:
        specs = race_comparison.load_manifest(race_comparison.DEFAULT_MANIFEST)
        structures = race_comparison.load_structures(race_comparison.DEFAULT_STRUCTURES)
        deck_points = race_comparison.load_deck_points(race_comparison.DEFAULT_DECK_POINTS)

        course_ids = {spec.course_id for spec in specs}
        self.assertIn("wachau_marathon", course_ids)
        self.assertEqual(set(structures) - course_ids, set())
        for spans in structures.values():
            for span in spans:
                if span.deck_points is not None:
                    self.assertIn(span.deck_points, deck_points)
        for spec in specs:
            # Only the Wachau track is part of the repository; the others are downloaded.
            self.assertEqual(spec.download_type is None, spec.course_id == "wachau_marathon")

    def test_analysis_names_missing_course_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "races.json"
            manifest.write_text(
                json.dumps(
                    {
                        "courses": [
                            {
                                "id": "wachau_marathon",
                                "event": "Test",
                                "discipline": "marathon",
                                "location": "Nowhere",
                                "gpx": "not_downloaded.gpx",
                                "course_version": "1",
                                "elevation_source": "terrain-tiles",
                                "source": {"name": "test", "url": "https://example.invalid"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            structures = Path(tmpdir) / "structures.json"
            structures.write_text(json.dumps({"courses": {}}), encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "not_downloaded.gpx"):
                race_comparison.analyze_courses(
                    manifest=manifest,
                    structures_file=structures,
                    cache_dir=Path(tmpdir),
                )


class TrackDownloadTests(unittest.TestCase):
    GPX = b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"></gpx>'

    def spec(self, gpx_file: Path, download_type: str | None, url: str | None) -> race_comparison.CourseSpec:
        return race_comparison.CourseSpec(
            course_id="test",
            event="Test",
            discipline="marathon",
            location="Nowhere",
            gpx_file=gpx_file,
            course_version="1",
            laps=1,
            start_km=None,
            end_km=None,
            official_distance_km=None,
            elevation_source="terrain-tiles",
            coarse_terrain_model=False,
            source_name="test",
            source_url="https://example.invalid",
            download_type=download_type,
            download_url=url,
            note="",
        )

    def test_file_and_zip_sources_are_downloaded_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.gpx"
            source.write_bytes(self.GPX)
            archive = Path(tmpdir) / "source.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("Course.gpx", self.GPX)
            specs = [
                self.spec(Path(tmpdir) / "tracks" / "plain.gpx", "file", source.as_uri()),
                self.spec(Path(tmpdir) / "tracks" / "plain.gpx", "file", source.as_uri()),
                self.spec(Path(tmpdir) / "tracks" / "zipped.gpx", "zip", archive.as_uri()),
            ]

            with mock.patch.object(fetch_race_tracks, "PAUSE_SECONDS", 0.0):
                first = fetch_race_tracks.fetch_tracks(specs)
                second = fetch_race_tracks.fetch_tracks(specs)

            self.assertEqual([status for _, status in first], ["downloaded", "downloaded"])
            self.assertEqual([status for _, status in second], ["already there", "already there"])
            self.assertEqual((Path(tmpdir) / "tracks" / "zipped.gpx").read_bytes(), self.GPX)

    def test_source_that_is_not_a_gpx_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "page.html"
            source.write_text("<html></html>", encoding="utf-8")
            spec = self.spec(Path(tmpdir) / "track.gpx", "file", source.as_uri())

            with self.assertRaises(fetch_race_tracks.TrackDownloadError):
                fetch_race_tracks.fetch_tracks([spec])

            self.assertFalse(spec.gpx_file.exists())

    def test_missing_track_without_source_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            spec = self.spec(Path(tmpdir) / "track.gpx", None, None)

            with self.assertRaises(FileNotFoundError):
                fetch_race_tracks.fetch_tracks([spec])


class TerrainTileTests(unittest.TestCase):
    def test_png_decoder_handles_all_filter_types(self) -> None:
        width, height = 5, 10
        rgb = bytes((row * 31 + column * 17 + channel * 7) % 256 for row in range(height) for column in range(width) for channel in range(3))

        decoded = terrain_tiles.decode_rgb_png(encode_png(width, height, rgb, [0, 1, 2, 3, 4]))

        self.assertEqual(decoded, (width, height, rgb))

    def test_elevation_is_interpolated_between_pixels(self) -> None:
        zoom = 14
        size = terrain_tiles.TILE_SIZE
        pixel_x, pixel_y = terrain_tiles.pixel_position(48.2, 16.37, zoom)
        tile_x = int(pixel_x) // size
        tile_y = int(pixel_y) // size
        # Elevation rises by one metre per pixel column: terrarium value 32768 + 100 + column.
        rgb = bytearray()
        for _ in range(size):
            for column in range(size):
                value = 32768 + 100 + column
                rgb.extend((value // 256, value % 256, 0))

        with tempfile.TemporaryDirectory() as tmpdir:
            tile_file = Path(tmpdir) / str(zoom) / str(tile_x) / f"{tile_y}.png"
            tile_file.parent.mkdir(parents=True)
            tile_file.write_bytes(encode_png(size, size, bytes(rgb), [0, 4]))
            client = terrain_tiles.TerrainTileClient(Path(tmpdir), zoom=zoom)

            elevation = client.elevation(48.2, 16.37)

        self.assertAlmostEqual(elevation, 100.0 + (pixel_x - tile_x * size), places=6)


class StructureDetectionTests(unittest.TestCase):
    def test_only_ways_along_the_track_are_matched(self) -> None:
        points = straight_track(41)
        distances_m = cumulative_distances(points)
        along = course_structures.StructureWay(
            way_id=1,
            kind="bridge",
            name="Along",
            geometry=[(0.00005, points[10].longitude), (0.00005, points[14].longitude)],
        )
        across = course_structures.StructureWay(
            way_id=2,
            kind="bridge",
            name="Across",
            geometry=[(-0.001, points[30].longitude), (0.001, points[30].longitude)],
        )

        spans = course_structures.detect_structure_spans(points, distances_m, [along, across])

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].names, ("Along",))
        self.assertAlmostEqual(spans[0].start_km, distances_m[10] / 1000.0, places=3)
        self.assertAlmostEqual(spans[0].end_km, distances_m[14] / 1000.0, places=3)
        self.assertFalse(spans[0].needs_review)


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib is not installed")
class FigureTests(unittest.TestCase):
    def test_figure_files_are_written(self) -> None:
        from OverallAltitude.Code import race_figure

        specs = race_comparison.load_manifest(race_comparison.DEFAULT_MANIFEST)
        profiles = []
        for index, spec in enumerate(specs):
            length_km = 42.195 if spec.discipline != "triathlon_bike" else 180.0
            distances = [length_km * step / 200 for step in range(201)]
            elevations = [100.0 + (index + 1) * 5.0 * abs((step % 40) - 20) / 20 for step in range(201)]
            ascent, descent, cumulative = race_comparison.climb_statistics(elevations, 2.0)
            profiles.append(
                race_comparison.CourseProfile(
                    spec=spec,
                    distances_km=distances,
                    elevations_m=elevations,
                    cumulative_ascent_m=cumulative,
                    total_ascent_m=ascent,
                    total_descent_m=descent,
                    net_difference_m=elevations[-1] - elevations[0],
                    track_distance_km=length_km,
                )
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            files = race_figure.save_comparison_figure(profiles, Path(tmpdir))

            self.assertEqual([file.suffix for file in files], [".png", ".svg"])
            for file in files:
                self.assertGreater(file.stat().st_size, 10_000)

    def test_spread_positions_keeps_order_and_gap(self) -> None:
        from OverallAltitude.Code import race_figure

        positions = race_figure.spread_positions([10.0, 11.0, 50.0, 10.5], 5.0, 0.0, 100.0)

        ordered = sorted(positions)
        self.assertTrue(all(later - earlier >= 5.0 - 1e-6 for earlier, later in zip(ordered, ordered[1:])))
        self.assertEqual(sorted(range(4), key=lambda index: positions[index]), [0, 3, 1, 2])


if __name__ == "__main__":
    unittest.main()
