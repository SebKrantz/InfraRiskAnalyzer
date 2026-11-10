"""What happens to an uploaded file: nothing left on disk, gone from memory when the
user deletes it or stops using it, and no other website may call the API.

Run: cd backend && python -m unittest discover -s tests
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from shapely.geometry import Point  # noqa: E402

import main  # noqa: E402
from app.api import analyze, upload  # noqa: E402
from app.api.upload import expire_uploads, uploaded_files  # noqa: E402
from app.config import settings  # noqa: E402

GDF = gpd.GeoDataFrame({"name": ["a", "b"], "value": [1.0, 2.0]},
                       geometry=[Point(10, 10), Point(11, 11)], crs="EPSG:4326")


def _samples(folder: Path) -> dict[str, bytes]:
    """One file per accepted format, plus two broken ones."""
    GDF.to_file(folder / "plain.gpkg", driver="GPKG")
    GDF.to_file(folder / "wal.gpkg", driver="GPKG")
    con = sqlite3.connect(folder / "wal.gpkg")  # WAL mode makes SQLite write side files
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    (folder / "shp").mkdir()
    GDF.to_file(folder / "shp" / "pts.shp")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in (folder / "shp").iterdir():
            z.write(f, f.name)
    return {
        "plain.gpkg": (folder / "plain.gpkg").read_bytes(),
        "wal.gpkg": (folder / "wal.gpkg").read_bytes(),
        "pts.zip": buf.getvalue(),
        "pts.csv": b"lat,lon,name\n10,10,a\n11,11,b\n",
        "pts.geojson": GDF.to_json().encode(),
        "broken.gpkg": b"not a geopackage",
        "broken.zip": b"PK\x03\x04 truncated",
    }


class UploadTest(unittest.TestCase):
    def setUp(self):
        # the background expiry is tested on its own below
        patcher = mock.patch.object(upload, "_start_sweeper", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.ids: list[str] = []

    def tearDown(self):
        for file_id in self.ids:
            upload.forget_upload(file_id)

    def _upload(self, name: str, data: bytes) -> str:
        r = self.client.post("/api/upload", files={"file": (name, data)})
        self.assertEqual(r.status_code, 200, r.text)
        self.ids.append(r.json()["file_id"])
        return self.ids[-1]

    def test_nothing_is_left_in_the_temp_folder(self):
        with tempfile.TemporaryDirectory() as samples, tempfile.TemporaryDirectory() as tmp:
            files = _samples(Path(samples))
            with mock.patch.object(tempfile, "tempdir", tmp):
                for name, data in files.items():
                    with self.subTest(file=name):
                        r = self.client.post("/api/upload", files={"file": (name, data)})
                        self.assertEqual(r.status_code, 400 if "broken" in name else 200)
                        if r.status_code == 200:
                            self.ids.append(r.json()["file_id"])
                        self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_delete_drops_the_data_and_its_cached_results(self):
        file_id = self._upload("pts.csv", b"lat,lon\n1,1\n")
        analyze.set_cached_raster_values(file_id, "h", [1.0])
        analyze.set_cached_analysis_result(file_id, "h", 1.0, {"x": 1})
        self.assertEqual(self.client.delete(f"/api/upload/{file_id}").status_code, 200)
        self.assertNotIn(file_id, uploaded_files)
        self.assertIsNone(analyze.get_cached_raster_values(file_id, "h"))
        self.assertIsNone(analyze.get_cached_analysis_result(file_id, "h", 1.0))
        self.assertEqual(self.client.delete(f"/api/upload/{file_id}").status_code, 404)

    def test_unused_uploads_expire(self):
        old = self._upload("pts.csv", b"lat,lon\n1,1\n")
        used = self._upload("pts.csv", b"lat,lon\n2,2\n")
        analyze.set_cached_raster_values(old, "h", [1.0])
        uploaded_files[old]["last_used"] -= 3600 * settings.UPLOAD_TTL_HOURS + 1
        uploaded_files[used]["last_used"] -= 3600 * settings.UPLOAD_TTL_HOURS + 1
        uploaded_files[used]  # an analysis or export reading it marks it as used
        # a dataset loaded some other way carries no last_used and is left alone
        uploaded_files["loaded-elsewhere"] = {"gdf": GDF}
        self.addCleanup(uploaded_files.pop, "loaded-elsewhere", None)

        self.assertEqual(expire_uploads(), [old])
        self.assertNotIn(old, uploaded_files)
        self.assertIsNone(analyze.get_cached_raster_values(old, "h"))
        self.assertIn(used, uploaded_files)
        self.assertIn("loaded-elsewhere", uploaded_files)

        r = self.client.post("/api/analyze", json={"file_id": old, "hazard_id": "x", "hazard_url": "x"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("upload it again", r.json()["detail"])


class SweeperTest(unittest.TestCase):
    def test_expiry_runs_in_the_background(self):
        async def scenario():
            uploaded_files["bg"] = {"gdf": GDF, "last_used": time.monotonic() - 10}
            with mock.patch.object(upload, "_SWEEP_INTERVAL_S", 0.01), \
                    mock.patch.object(settings, "UPLOAD_TTL_HOURS", 1 / 3600):
                upload._start_sweeper()
                await asyncio.sleep(0.1)
                upload._sweeper.cancel()
            return "bg" in uploaded_files

        self.addCleanup(uploaded_files.pop, "bg", None)
        self.assertFalse(asyncio.run(scenario()))


class CorsTest(unittest.TestCase):
    def test_other_websites_get_no_cors_grant(self):
        client = TestClient(main.app)
        r = client.get("/api/hazards", headers={"Origin": "https://evil.example", "Cookie": "s=1"})
        self.assertNotIn("access-control-allow-origin", r.headers)
        r = client.options("/api/upload", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", r.headers)


if __name__ == "__main__":
    unittest.main()
