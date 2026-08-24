from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from genshin_navigator.calibration import CalibrationSession, DistanceCalibration
from genshin_navigator.poi import MapSpaceMetric, PoiCatalog
from genshin_navigator.position import CoordinateSpace, MapPosition, PositionState


def position(x: float, y: float, *, layer: str = "surface") -> MapPosition:
    return MapPosition(
        region_id="fontaine",
        layer_id=layer,
        coordinate_space=(
            CoordinateSpace.SURFACE_ATLAS
            if layer == "surface"
            else CoordinateSpace.LAYER_LOCAL
        ),
        x=x,
        y=y,
        confidence=1.0,
        state=PositionState.TRACKING,
        timestamp=x + y + 1,
    )


def catalog() -> PoiCatalog:
    return PoiCatalog(
        [],
        [
            MapSpaceMetric(
                "fontaine", "surface", CoordinateSpace.SURFACE_ATLAS,
                ((2.0, 0.0), (0.0, 2.0)),
            )
        ],
    )


class CalibrationTests(unittest.TestCase):
    def test_uses_median_factor_when_three_samples_agree(self) -> None:
        session = CalibrationSession(catalog())
        session.add_sample(position(0, 0), position(50, 0), 100)
        session.add_sample(position(0, 0), position(100, 0), 204)
        session.add_sample(position(0, 0), position(75, 0), 147)

        result = session.result()

        self.assertAlmostEqual(result.meters_per_world_unit, 1.0)

    def test_rejects_samples_with_more_than_ten_percent_deviation(self) -> None:
        session = CalibrationSession(catalog())
        session.add_sample(position(0, 0), position(50, 0), 100)
        session.add_sample(position(0, 0), position(100, 0), 200)
        session.add_sample(position(0, 0), position(75, 0), 300)

        with self.assertRaisesRegex(ValueError, "disagree"):
            session.result()

    def test_rejects_incomplete_session(self) -> None:
        session = CalibrationSession(catalog())
        session.add_sample(position(0, 0), position(50, 0), 100)

        with self.assertRaisesRegex(ValueError, "incomplete"):
            session.result()

    def test_invalid_draft_does_not_replace_last_valid_calibration(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "fontaine.json"
            valid = DistanceCalibration("fontaine", 1.25)
            valid.save_atomic(path)
            session = CalibrationSession(catalog())
            session.add_sample(position(0, 0), position(50, 0), 100)
            session.write_draft(path, error="incomplete")

            restored = DistanceCalibration.load(path)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.meters_per_world_unit, 1.25)  # type: ignore[union-attr]

    def test_rejects_underground_measurement(self) -> None:
        session = CalibrationSession(catalog())
        with self.assertRaisesRegex(ValueError, "surface"):
            session.add_sample(position(0, 0, layer="floor78"), position(10, 0, layer="floor78"), 100)


if __name__ == "__main__":
    unittest.main()
