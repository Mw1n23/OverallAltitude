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

        self.assertAlmostEqual(ascent, 4.3)
        self.assertAlmostEqual(descent, 0.0)
        self.assertAlmostEqual(net, 4.0)

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
                module.OpenTopoDataClient,
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
