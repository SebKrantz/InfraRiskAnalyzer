"""Uploads, analysis and exports against a small local hazard raster: the raster comes
from the catalogue, uploads cannot write outside their temp folder, awkward files are
read or refused with a clear message, one user's analyses do not evict another's, and
the split GPKG export adds up to the totals on screen.

Run: cd backend && python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402

import main  # noqa: E402
from app.api import analyze, export, upload  # noqa: E402
from app.api.upload import uploaded_files  # noqa: E402
from app.config import settings  # noqa: E402
from app.utils.geospatial import parse_vulnerability_curve_data  # noqa: E402

# A 1/100-degree grid over 10-11 E, 10-11 N whose value is 100 x the column:
# a point at 10.505 E reads 5000, one at 10.105 E reads 1000.
_TMP = tempfile.TemporaryDirectory()
RASTER = Path(_TMP.name) / "hazard.tif"
with rasterio.open(
    RASTER, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32",
    crs="EPSG:4326", transform=from_origin(10, 11, 0.01, 0.01), nodata=-9999,
) as _dst:
    _dst.write(np.tile(np.arange(100, dtype=np.float32) * 100, (100, 1)), 1)

CATALOGUE = {"test": {"hazard_id": "test", "hazard": "Test", "dataset_url": str(RASTER), "unit": "mm"}}

POINTS_CSV = b"lat,lon,name\n10.5,10.505,deep\n10.5,10.105,shallow\n"


def _geojson(features: list) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _shapefile(gdf: gpd.GeoDataFrame, folder: Path, prefix: str = "", prj: bool = True) -> bytes:
    gdf.to_file(folder / "pts.shp")
    return _zip({
        prefix + f.name: f.read_bytes()
        for f in folder.iterdir()
        if f.suffix != ".prj" or prj
    })


class _AppTest(unittest.TestCase):
    def setUp(self):
        for target in (analyze, export):
            patcher = mock.patch.object(target, "load_hazards_dict", lambda: CATALOGUE)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(upload, "_start_sweeper", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.ids: list[str] = []

    def tearDown(self):
        for file_id in self.ids:
            upload.forget_upload(file_id)

    def upload(self, name: str, data: bytes, status: int = 200):
        r = self.client.post("/api/upload", files={"file": (name, data)})
        self.assertEqual(r.status_code, status, r.text)
        if r.status_code == 200:
            self.ids.append(r.json()["file_id"])
        return r.json()

    def analyze(self, file_id: str, threshold=None, status: int = 200, **extra):
        body = {"file_id": file_id, "hazard_id": "test", "intensity_threshold": threshold, **extra}
        r = self.client.post("/api/analyze", json=body)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()


class CatalogueRasterTest(_AppTest):
    def test_the_raster_comes_from_the_catalogue_not_the_request(self):
        file_id = self.upload("pts.csv", POINTS_CSV)["file_id"]
        result = self.analyze(file_id, 2000, hazard_url="/etc/hosts")
        self.assertEqual(result["summary"]["affected_count"], 1)
        r = self.client.post("/api/analyze", json={
            "file_id": file_id, "hazard_id": "elsewhere", "hazard_url": str(RASTER)})
        self.assertEqual(r.status_code, 404)

    def test_a_failed_raster_read_is_an_error_not_unaffected(self):
        file_id = self.upload("pts.csv", POINTS_CSV)["file_id"]
        with mock.patch.object(
            rasterio.io.DatasetReader, "read",
            side_effect=rasterio.errors.RasterioIOError("connection reset"),
        ):
            r = self.client.post("/api/analyze", json={"file_id": file_id, "hazard_id": "test"})
        self.assertEqual(r.status_code, 502, r.text)
        self.assertIsNone(analyze.get_cached_raster_values(file_id, "test"))
        self.assertEqual(self.analyze(file_id, 2000)["summary"]["affected_count"], 1)


class UploadFilesTest(_AppTest):
    def test_a_zip_name_cannot_choose_where_the_upload_is_written(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            data = _shapefile(gpd.GeoDataFrame(geometry=[Point(10.5, 10.5)], crs=4326), Path(folder))
            for name in ("../../escape.zip", f"{outside}/escape.zip"):
                with self.subTest(name=name):
                    self.upload(name, data)
            self.assertEqual(list(Path(outside).iterdir()), [])
            self.assertFalse((Path(tempfile.gettempdir()).parent / "escape.zip").exists())

    def test_a_shapefile_in_a_zipped_folder_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            data = _shapefile(
                gpd.GeoDataFrame(geometry=[Point(10.5, 10.5)], crs=4326), Path(folder), prefix="towers/")
        self.assertEqual(self.upload("towers.zip", data)["feature_count"], 1)

    def test_size_limits(self):
        with mock.patch.object(settings, "MAX_UPLOAD_SIZE", 10):
            self.upload("pts.csv", POINTS_CSV, status=413)
        bomb = _zip({"pts.shp": b"\0" * 5000})
        with mock.patch.object(settings, "MAX_UPLOAD_SIZE", 1000), \
                mock.patch.object(settings, "MAX_UNZIPPED_FACTOR", 1):
            self.assertIn("unpacks", self.upload("bomb.zip", bomb, status=400)["detail"])

    def test_a_bare_shp_is_refused_with_a_reason(self):
        self.assertIn(".zip", self.upload("pts.shp", b"\0" * 100, status=400)["detail"])

    def test_dates_reach_the_browser_as_text(self):
        data = _geojson([{"type": "Feature", "properties": {"surveyed": "2021-03-04"},
                          "geometry": {"type": "Point", "coordinates": [10.505, 10.5]}}])
        result = self.upload("dated.geojson", data)
        self.assertTrue(result["geojson"]["features"][0]["properties"]["surveyed"].startswith("2021-03-04"))
        analysed = self.analyze(result["file_id"], 2000)
        self.assertEqual(analysed["summary"]["affected_count"], 1)
        self.assertIn("infrastructure_features", analysed)

    def test_multipoints_become_points_and_empty_geometries_are_skipped(self):
        data = _geojson([
            {"type": "Feature", "properties": {"n": 1},
             "geometry": {"type": "Point", "coordinates": [10.505, 10.5]}},
            {"type": "Feature", "properties": {"n": 2},
             "geometry": {"type": "MultiPoint", "coordinates": [[10.105, 10.5], [10.705, 10.5]]}},
            {"type": "Feature", "properties": {"n": 3}, "geometry": None},
        ])
        result = self.upload("mixed.geojson", data)
        self.assertEqual((result["geometry_type"], result["feature_count"]), ("Point", 3))
        self.assertEqual(result["skipped_features"], 1)
        self.assertEqual(self.analyze(result["file_id"], 2000)["summary"]["affected_count"], 2)

    def test_a_projected_shapefile_without_its_prj_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            utm = gpd.GeoDataFrame(geometry=[Point(500000, 1100000)], crs=32632)
            data = _shapefile(utm, Path(folder), prj=False)
        self.assertIn(".prj", self.upload("utm.zip", data, status=400)["detail"])
        with tempfile.TemporaryDirectory() as folder:
            lonlat = gpd.GeoDataFrame(geometry=[Point(10.5, 10.5)], crs=4326)
            data = _shapefile(lonlat, Path(folder), prj=False)
        self.assertEqual(self.upload("lonlat.zip", data)["crs"], "EPSG:4326")

    def test_errors_name_the_file_not_the_server_paths(self):
        detail = self.upload("assets.gpkg", b"not a geopackage", status=400)["detail"]
        self.assertNotIn(tempfile.gettempdir(), detail)
        self.assertIn("assets.gpkg", detail)

    def test_the_least_recently_used_upload_makes_room(self):
        with mock.patch.object(settings, "MAX_UPLOADS", 2):
            first = self.upload("a.csv", POINTS_CSV)["file_id"]
            self.upload("b.csv", POINTS_CSV)
            self.upload("c.csv", POINTS_CSV)
        self.assertNotIn(first, uploaded_files)


class CacheTest(_AppTest):
    def test_one_users_analyses_do_not_evict_anothers(self):
        mine = self.upload("mine.csv", POINTS_CSV)["file_id"]
        theirs = self.upload("theirs.csv", POINTS_CSV)["file_id"]
        self.analyze(mine, 2000)
        for threshold in range(100, 2100, 100):
            self.analyze(theirs, threshold)
        r = self.client.post("/api/export/data", json={
            "file_id": mine, "hazard_id": "test", "intensity_threshold": 2000, "mode": "csv_points"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_an_export_never_falls_back_to_another_threshold(self):
        file_id = self.upload("pts.csv", POINTS_CSV)["file_id"]
        self.analyze(file_id)
        r = self.client.post("/api/export/data", json={
            "file_id": file_id, "hazard_id": "test", "intensity_threshold": 2000, "mode": "csv_points"})
        self.assertEqual(r.status_code, 404, r.text)


class SplitExportTest(_AppTest):
    def test_the_split_gpkg_adds_up_to_the_totals_on_screen(self):
        line = gpd.GeoDataFrame({"name": ["fibre"]},
                                geometry=[LineString([(10.005, 10.5), (10.905, 10.5)])], crs=4326)
        file_id = self.upload("line.geojson", line.to_json().encode())["file_id"]
        intensity = np.linspace(0, 10000, 11)
        central = intensity / 10000
        curve = "intensity,lower,central,upper\n" + "".join(
            f"{i},{c / 2},{c},{min(1.0, c * 1.5)}\n" for i, c in zip(intensity, central))
        r = self.client.post("/api/analyze", data={
            "file_id": file_id, "hazard_id": "test", "intensity_threshold": "3000",
            "replacement_value": "10"}, files={"vulnerability_curve_file": ("curve.csv", curve)})
        self.assertEqual(r.status_code, 200, r.text)
        summary = r.json()["summary"]

        r = self.client.post("/api/export/data", json={
            "file_id": file_id, "hazard_id": "test", "intensity_threshold": 3000,
            "mode": "gpkg_lines_split"})
        self.assertEqual(r.status_code, 200, r.text)
        spans = gpd.read_file(io.BytesIO(r.content))
        for column, total in (("damage_cost", "total_damage_cost"),
                              ("damage_cost_lower", "total_damage_cost_lower"),
                              ("damage_cost_upper", "total_damage_cost_upper")):
            with self.subTest(column=column):
                self.assertAlmostEqual(spans[column].sum(), summary[total], delta=1e-6 * summary[total])
        self.assertTrue((spans.loc[~spans["affected"], "damage_cost"] == 0).all())
        self.assertGreater((~spans["affected"]).sum(), 0)


class CurveTest(unittest.TestCase):
    def test_a_step_in_a_long_curve_keeps_its_order(self):
        rows = [(i * 100, 0.01 * i) for i in range(25)]
        rows[10:11] = [(1000, 0.25), (1000, 0.75)]
        # Rows out of order, the step's two rows in the file's order: the default
        # (unstable) sort swaps them for this order
        random.Random(3).shuffle(rows)
        a, b = rows.index((1000, 0.25)), rows.index((1000, 0.75))
        if a > b:
            rows[a], rows[b] = rows[b], rows[a]
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("".join(f"{i},{p}\n" for i, p in rows))
        interp = parse_vulnerability_curve_data(f.name)[0]
        Path(f.name).unlink()
        self.assertGreater(interp(1000.5), 0.7)


if __name__ == "__main__":
    unittest.main()
