import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from scripts.terrain_web_mesh import generate_terrain_mesh


class TerrainWebMeshTests(unittest.TestCase):
    def create_raster(self, path, hole=False):
        source = gdal.GetDriverByName("GTiff").Create(str(path), 5, 5, 1, gdal.GDT_Float32)
        crs = osr.SpatialReference(); crs.ImportFromEPSG(31983)
        source.SetProjection(crs.ExportToWkt()); source.SetGeoTransform((500000, 2, 0, 7600000, 0, -2))
        data = np.arange(25, dtype=np.float32).reshape(5, 5) + 100
        if hole: data[1, 1] = -9999
        source.GetRasterBand(1).SetNoDataValue(-9999)
        source.GetRasterBand(1).WriteArray(data); source.FlushCache()

    def test_sampled_cotes_and_bounded_mesh(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "terrain.tif"; self.create_raster(source)
            result = generate_terrain_mesh(source, Path(folder) / "mesh.json", 3)
            self.assertEqual(len(result["vertices"]), 9)
            self.assertEqual(len(result["triangles"]), 8)
            self.assertEqual(result["vertices"][0][2], 100)
            self.assertEqual(result["vertices"][-1][2], 124)
            self.assertFalse(result["guidance_authorized"])

    def test_unsampled_nodata_hole_prevents_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "terrain.tif"; self.create_raster(source, hole=True)
            result = generate_terrain_mesh(source, Path(folder) / "mesh.json", 3)
            self.assertEqual(len(result["vertices"]), 9)
            self.assertEqual(result["omitted_quad_count"], 1)
            self.assertEqual(len(result["triangles"]), 6)
