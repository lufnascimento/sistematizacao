import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr
from shapely.geometry import box

from scripts.generate_sulcation_scenarios import Terrain


class TerrainCoverageTests(unittest.TestCase):
    def make_raster(self, directory: Path, values: np.ndarray, nodata: float = -9999.0) -> Path:
        path = directory / "terrain.tif"
        dataset = gdal.GetDriverByName("GTiff").Create(
            str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32
        )
        dataset.SetGeoTransform((0.0, 1.0, 0.0, float(values.shape[0]), 0.0, -1.0))
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromEPSG(31982)
        dataset.SetProjection(spatial_ref.ExportToWkt())
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.WriteArray(values.astype("float32"))
        dataset = None
        return path

    def test_complete_valid_coverage_passes(self):
        with tempfile.TemporaryDirectory() as name:
            terrain = Terrain(self.make_raster(Path(name), np.arange(100).reshape(10, 10)))
            result = terrain.validate_geometry_coverage(box(2, 2, 8, 8))
            self.assertEqual(result["status"], "PASS_COMPLETE_VALID_CELL_COVERAGE")
            self.assertEqual(result["invalid_or_nodata_cell_count"], 0)
            terrain.dataset = None

    def test_geometry_outside_raster_fails(self):
        with tempfile.TemporaryDirectory() as name:
            terrain = Terrain(self.make_raster(Path(name), np.ones((10, 10))))
            with self.assertRaisesRegex(RuntimeError, "outside_area_m2"):
                terrain.validate_geometry_coverage(box(-1, 2, 4, 8))
            terrain.dataset = None

    def test_internal_nodata_fails(self):
        with tempfile.TemporaryDirectory() as name:
            values = np.ones((10, 10))
            values[5, 5] = -9999.0
            terrain = Terrain(self.make_raster(Path(name), values))
            with self.assertRaisesRegex(RuntimeError, "invalid_or_nodata_cells=1"):
                terrain.validate_geometry_coverage(box(4, 3, 7, 6))
            terrain.dataset = None

    def test_sample_outside_raster_fails_instead_of_clamping(self):
        with tempfile.TemporaryDirectory() as name:
            terrain = Terrain(self.make_raster(Path(name), np.ones((10, 10))))
            with self.assertRaisesRegex(RuntimeError, "outside the DTM"):
                terrain.sample(terrain.elevation, np.asarray([-0.1]), np.asarray([5.0]))
            terrain.dataset = None


if __name__ == "__main__":
    unittest.main()
