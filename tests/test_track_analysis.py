import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from OverallAltitude.Code import Track_analysis_05 as module


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "OverallAltitude" / "Code" / "Track_analysis_05.py"


class TrackAnalysisTests(unittest.TestCase):
    def test_script_compiles(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        compile(source, str(MODULE_PATH), "exec")

    def test_module_cli_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "OverallAltitude", "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Analyze total ascent/descent", result.stdout)

    def test_resample_track_uses_requested_spacing(self) -> None:
        points = [
            module.TrackPoint(0.0, 0.0, 100.0),
            module.TrackPoint(0.0, 0.001, 110.0),
        ]

        resampled_points, distances = module.resample_track(points, spacing_m=25.0)

        self.assertGreater(len(resampled_points), 2)
        self.assertAlmostEqual(distances[0], 0.0)
        self.assertAlmostEqual(
            distances[-1],
            module.cumulative_distances(points)[-1],
            places=6,
        )

    def test_calculate_total_climb_applies_threshold(self) -> None:
        elevations = [100.0, 100.4, 102.0, 101.3, 104.0]

        ascent, descent, net = module.calculate_total_climb(elevations, threshold_m=1.0)

        # The dip of 0.7 m stays below the threshold, so the track counts as one climb.
        self.assertAlmostEqual(ascent, 4.0)
        self.assertAlmostEqual(descent, 0.0)
        self.assertAlmostEqual(net, 4.0)

    def test_calculate_total_climb_counts_reversals_above_threshold(self) -> None:
        elevations = [100.0, 100.4, 102.0, 100.5, 104.0]

        ascent, descent, net = module.calculate_total_climb(elevations, threshold_m=1.0)

        self.assertAlmostEqual(ascent, 5.5)
        self.assertAlmostEqual(descent, 1.5)
        self.assertAlmostEqual(net, 4.0)

    def test_calculate_total_climb_counts_gentle_climb_in_full(self) -> None:
        elevations = [100.0 + 0.5 * index for index in range(41)]

        ascent, descent, net = module.calculate_total_climb(elevations, threshold_m=2.0)

        self.assertAlmostEqual(ascent, 20.0)
        self.assertAlmostEqual(descent, 0.0)
        self.assertAlmostEqual(net, 20.0)

    def test_analyze_track_uses_mocked_dem_values(self) -> None:
        points = [
            module.TrackPoint(48.0, 15.0, 120.0),
            module.TrackPoint(48.0, 15.001, 121.0),
            module.TrackPoint(48.0, 15.002, 122.0),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_db = Path(tmpdir) / "cache.sqlite3"
            with mock.patch.object(
                module.OpenTopoDataClient,
                "lookup",
                return_value=[130.0, 132.0, 135.0],
            ):
                result = module.analyze_track(
                    points=points,
                    elevation_source="opentopodata",
                    resample_distance_m=0.0,
                    threshold_m=0.5,
                    window_size=1,
                    max_change_m=50.0,
                    dataset="eudem25m,mapzen",
                    cache_db=cache_db,
                    base_url="https://api.opentopodata.org/v1",
                    interpolation="bilinear",
                    timeout_seconds=1.0,
                )

        self.assertEqual(result.source_label, "official-dem")
        self.assertEqual(result.dataset_label, "eudem25m,mapzen")
        self.assertAlmostEqual(result.total_ascent_m, 5.0)
        self.assertAlmostEqual(result.net_difference_m, 5.0)

    def test_auto_source_falls_back_to_gpx(self) -> None:
        points = [
            module.TrackPoint(48.0, 15.0, 120.0),
            module.TrackPoint(48.0, 15.001, 124.0),
            module.TrackPoint(48.0, 15.002, 121.0),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_db = Path(tmpdir) / "cache.sqlite3"
            with mock.patch.object(
                module.TerrainTileClient,
                "lookup",
                side_effect=module.ElevationServiceError("service unavailable"),
            ):
                with warnings_helper() as caught_warnings:
                    result = module.analyze_track(
                        points=points,
                        elevation_source="auto",
                        resample_distance_m=0.0,
                        threshold_m=0.5,
                        window_size=1,
                        max_change_m=50.0,
                        dataset="eudem25m,mapzen",
                        cache_db=cache_db,
                        base_url="https://api.opentopodata.org/v1",
                        interpolation="bilinear",
                        timeout_seconds=1.0,
                    )

        self.assertEqual(result.source_label, "gpx-fallback")
        self.assertTrue(caught_warnings)
        self.assertAlmostEqual(result.total_ascent_m, 4.0)
        self.assertAlmostEqual(result.total_descent_m, 3.0)

    def test_auto_source_uses_terrain_tiles(self) -> None:
        points = [
            module.TrackPoint(48.0, 15.0, 120.0),
            module.TrackPoint(48.0, 15.001, 121.0),
            module.TrackPoint(48.0, 15.002, 122.0),
        ]

        with mock.patch.object(
            module.TerrainTileClient,
            "lookup",
            return_value=[200.0, 204.0, 201.0],
        ):
            result = analyze_with_defaults(points, elevation_source="auto")

        self.assertEqual(result.source_label, "terrain-tiles")
        self.assertIsNone(result.dataset_label)
        self.assertAlmostEqual(result.total_ascent_m, 4.0)
        self.assertAlmostEqual(result.total_descent_m, 3.0)

    def test_strict_terrain_tiles_source_does_not_fall_back(self) -> None:
        points = [
            module.TrackPoint(48.0, 15.0, 120.0),
            module.TrackPoint(48.0, 15.001, 121.0),
        ]

        with mock.patch.object(
            module.TerrainTileClient,
            "lookup",
            side_effect=module.ElevationServiceError("service unavailable"),
        ):
            with self.assertRaises(module.ElevationServiceError):
                analyze_with_defaults(points, elevation_source="terrain-tiles")

    def test_bridge_section_removes_the_valley_below(self) -> None:
        points = [module.TrackPoint(48.0, 15.0 + index * 0.0003, 0.0) for index in range(9)]
        distances_m = module.cumulative_distances(points)
        bridge = module.StructureSpan(
            kind="bridge",
            start_km=distances_m[2] / 1000.0,
            end_km=distances_m[6] / 1000.0,
            name="test bridge",
            deck_points=None,
            deck_max_m=None,
        )
        terrain = [50.0, 50.0, 40.0, 10.0, 0.0, 10.0, 40.0, 50.0, 50.0]

        with mock.patch.object(module.TerrainTileClient, "lookup", return_value=terrain):
            plain = analyze_with_defaults(points, elevation_source="terrain-tiles")
        with mock.patch.object(module.TerrainTileClient, "lookup", return_value=terrain):
            corrected = analyze_with_defaults(
                points,
                elevation_source="terrain-tiles",
                structures=[bridge],
            )

        self.assertAlmostEqual(plain.total_ascent_m, 50.0)
        self.assertAlmostEqual(corrected.total_ascent_m, 0.0)
        self.assertAlmostEqual(corrected.total_descent_m, 0.0)

    def test_structures_need_terrain_model_elevations(self) -> None:
        points = [
            module.TrackPoint(48.0, 15.0, 120.0),
            module.TrackPoint(48.0, 15.001, 121.0),
        ]
        bridge = module.StructureSpan("bridge", 0.0, 0.05, "test bridge", None, None)

        with self.assertRaises(ValueError):
            analyze_with_defaults(points, elevation_source="gpx", structures=[bridge])

    def test_cli_rejects_course_id_with_gpx_elevations(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "OverallAltitude",
                "--elevation-source",
                "gpx",
                "--course-id",
                "wachau_marathon",
                "--no-plot",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--course-id", result.stderr)


def analyze_with_defaults(points, elevation_source, structures=()):
    with tempfile.TemporaryDirectory() as tmpdir:
        return module.analyze_track(
            points=points,
            elevation_source=elevation_source,
            resample_distance_m=0.0,
            threshold_m=0.5,
            window_size=1,
            max_change_m=50.0,
            dataset="eudem25m,mapzen",
            cache_db=Path(tmpdir) / "cache.sqlite3",
            base_url="https://api.opentopodata.org/v1",
            interpolation="bilinear",
            timeout_seconds=1.0,
            structures=structures,
        )


class warnings_helper:
    def __enter__(self):
        import warnings

        self._manager = warnings.catch_warnings(record=True)
        self._records = self._manager.__enter__()
        warnings.simplefilter("always")
        return self._records

    def __exit__(self, exc_type, exc, tb):
        return self._manager.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
