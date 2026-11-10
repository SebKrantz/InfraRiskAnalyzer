"""
File upload endpoints
"""

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import asyncio
import os
import time
import zipfile
import tempfile
from pathlib import Path
from typing import Optional

from app.config import settings
from app.utils.geospatial import (
    load_spatial_file, load_csv_points, validate_geometry_type, convert_polygons_to_centroids,
    drop_missing_geometries, explode_multipoints, json_safe_attributes,
)

router = APIRouter()

_READ_CHUNK = 1024 * 1024


class UploadRejected(ValueError):
    """The file cannot be used as uploaded; the message says why (HTTP 400)."""


async def _read_limited(file: UploadFile) -> bytes:
    """Read the upload, refusing it (413) as soon as it passes MAX_UPLOAD_SIZE rather
    than after holding all of it in memory."""
    chunks, size = [], 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE / (1024 * 1024):g} MB",
            )
        chunks.append(chunk)


def _shapefile_from_zip(content: bytes, temp_dir: str) -> str:
    """Unpack a zipped shapefile into temp_dir and return the path of its .shp.

    The archive is written under a fixed name (the client's file name could hold a
    path), its unpacked size is capped, and the .shp may sit in a folder inside the
    zip, as when a folder is compressed.
    """
    zip_path = Path(temp_dir) / "upload.zip"
    zip_path.write_bytes(content)
    out_dir = Path(temp_dir) / "unzipped"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            unpacked = sum(info.file_size for info in zf.infolist())
            limit = settings.MAX_UPLOAD_SIZE * settings.MAX_UNZIPPED_FACTOR
            if unpacked > limit:
                raise UploadRejected(
                    f"The zip unpacks to {unpacked / 1024 ** 2:,.0f} MB, more than the "
                    f"{limit / 1024 ** 2:,.0f} MB allowed."
                )
            # extractall strips absolute paths and ".." from member names
            zf.extractall(out_dir)
    except zipfile.BadZipFile:
        raise UploadRejected("The file is not a valid zip archive.")
    shp_files = sorted(
        p for p in out_dir.rglob("*.shp")
        if "__MACOSX" not in p.parts and not p.name.startswith(".")
    )
    if not shp_files:
        raise UploadRejected("No .shp file found in the zip archive.")
    if len(shp_files) > 1:
        names = ", ".join(p.name for p in shp_files[:5])
        raise UploadRejected(
            f"The zip holds {len(shp_files)} shapefiles ({names}); upload one at a time."
        )
    return str(shp_files[0])


def _drop_least_recently_used() -> None:
    """Keep at most MAX_UPLOADS uploads in memory by dropping the least recently used."""
    excess = len(uploaded_files) - settings.MAX_UPLOADS + 1
    if excess <= 0:
        return
    by_age = sorted(
        dict.items(uploaded_files), key=lambda kv: kv[1].get("last_used", 0.0)
    )
    for file_id, _ in by_age[:excess]:
        forget_upload(file_id)

class _UploadStore(dict):
    """Uploaded datasets by file_id, in memory only. Reading an entry marks it as used,
    so expire_uploads() drops only the ones nobody has touched for UPLOAD_TTL_HOURS."""

    def __getitem__(self, file_id):
        info = super().__getitem__(file_id)
        if "last_used" in info:
            info["last_used"] = time.monotonic()
        return info

    def get(self, file_id, default=None):
        return self[file_id] if file_id in self else default


# Store uploaded file metadata and GeoDataFrame in memory (never on disk)
uploaded_files = _UploadStore()

# How often the expiry runs while the server is up, seconds.
_SWEEP_INTERVAL_S = 600
_sweeper: Optional[asyncio.Task] = None


def forget_upload(file_id: str) -> bool:
    """Delete an upload and every cached result computed from it."""
    from app.api.analyze import clear_raster_cache_for_file

    found = uploaded_files.pop(file_id, None) is not None
    clear_raster_cache_for_file(file_id)
    return found


def expire_uploads(now: Optional[float] = None) -> list:
    """Delete the uploads unused for longer than UPLOAD_TTL_HOURS. Only uploads made
    through this endpoint carry `last_used`; anything else is left alone."""
    now = time.monotonic() if now is None else now
    ttl = settings.UPLOAD_TTL_HOURS * 3600
    stale = [
        file_id for file_id, info in list(uploaded_files.items())
        if now - info.get("last_used", now) > ttl
    ]
    for file_id in stale:
        forget_upload(file_id)
    return stale


async def _sweep_forever() -> None:
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL_S)
        expire_uploads()


def _start_sweeper() -> None:
    """Run the expiry on this event loop from the first upload on, so data expires even
    when no further request arrives."""
    global _sweeper
    loop = asyncio.get_running_loop()
    if _sweeper is None or _sweeper.done() or _sweeper.get_loop() is not loop:
        _sweeper = loop.create_task(_sweep_forever())


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    Upload a spatial file (Shapefile, GeoPackage, GeoJSON, or CSV with coordinates)

    Accepts:
    - .zip (zipped shapefile: .shp with its .shx, .dbf and .prj, possibly in a folder)
    - .gpkg (GeoPackage)
    - .geojson (GeoJSON)
    - .csv (with lat/lon, lat/lng, latitude/longitude, or y/x columns, or a
            WKT geometry column such as geometry/geom/wkt holding POINT or
            LINESTRING geometries)

    Shapefile and GeoPackage may contain Polygon or MultiPolygon geometries
    (e.g. buildings); these are converted to centroid points for analysis.
    MultiPoint features are split into points, and features without a geometry
    are skipped.

    Returns file metadata including geometry type and feature count
    """
    filename = Path(file.filename or "").name
    file_ext = Path(filename).suffix.lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not supported. Allowed: {', '.join(sorted(settings.ALLOWED_EXTENSIONS))}"
        )
    if file_ext == ".shp":
        raise HTTPException(
            status_code=400,
            detail="Upload a shapefile as a .zip holding the .shp together with its "
            ".shx, .dbf and .prj files.",
        )

    file_content = await _read_limited(file)

    expire_uploads()
    _start_sweeper()

    import uuid
    file_id = str(uuid.uuid4())

    try:
        gdf = _load_upload(file_content, file_ext)
        gdf, skipped = drop_missing_geometries(gdf)
        if len(gdf) == 0:
            raise UploadRejected("The file has no features with a geometry.")

        # Convert Polygon/MultiPolygon to centroids (point dataset) before validation
        gdf = convert_polygons_to_centroids(gdf)
        gdf = explode_multipoints(gdf)

        # Validate geometry type (points or lines)
        geometry_type = validate_geometry_type(gdf)
    except HTTPException:
        raise
    except Exception as e:
        # Whatever stops a file from being read or used is a problem with the file
        raise HTTPException(status_code=400, detail=_scrub_temp_paths(str(e), filename))

    try:
        # Clean NaN values from GeoDataFrame before storing (clean once, not during analysis)
        import numpy as np
        import pandas as pd
        for col in gdf.columns:
            if col != 'geometry':
                if gdf[col].dtype in ['float64', 'float32']:
                    gdf[col] = gdf[col].replace([np.nan, np.inf, -np.inf], [None, None, None])
                elif pd.api.types.is_numeric_dtype(gdf[col]):
                    gdf[col] = gdf[col].replace([np.nan], [None])
        gdf = json_safe_attributes(gdf)

        # Transform to WGS84 in place (all hazard rasters use EPSG:4326, so store in WGS84)
        if gdf.crs and gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        _drop_least_recently_used()

        # Store metadata and GeoDataFrame
        uploaded_files[file_id] = {
            "filename": filename,
            "geometry_type": geometry_type,
            "feature_count": len(gdf),
            "crs": str(gdf.crs) if gdf.crs else None,
            "bounds": {
                "minx": float(gdf.bounds.minx.min()),
                "miny": float(gdf.bounds.miny.min()),
                "maxx": float(gdf.bounds.maxx.max()),
                "maxy": float(gdf.bounds.maxy.max())
            },
            "gdf": gdf,  # Store GeoDataFrame for analysis (already cleaned and in WGS84)
            "last_used": time.monotonic(),
        }

        # Convert to GeoJSON for display (gdf is already in WGS84 and cleaned)
        # Strip per-feature bbox to avoid MapLibre geojson-vt tiler issues
        try:
            geo_json = gdf.__geo_interface__
            if "bbox" in geo_json:
                del geo_json["bbox"]
            for feature in geo_json.get("features", []):
                feature.pop("bbox", None)
        except Exception as e:
            print(f"Warning: Could not convert to GeoJSON for upload response: {e}")
            geo_json = None

        message = "File uploaded successfully"
        if skipped:
            message += f"; {skipped} feature{'s' if skipped != 1 else ''} without a geometry skipped"
        return JSONResponse(content={
            "file_id": file_id,
            "filename": filename,
            "geometry_type": geometry_type,
            "feature_count": len(gdf),
            "skipped_features": skipped,
            "crs": uploaded_files[file_id]["crs"],
            "bounds": uploaded_files[file_id]["bounds"],
            "geojson": geo_json,  # Include GeoJSON for initial display
            "message": message,
        })

    except Exception as e:
        uploaded_files.pop(file_id, None)
        print(f"Upload processing failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing file: {_scrub_temp_paths(str(e), filename)}"
        )


def _load_upload(content: bytes, file_ext: str):
    """Parse the uploaded bytes in a temporary location that is removed afterwards."""
    if file_ext == ".zip":
        with tempfile.TemporaryDirectory() as temp_dir:
            return load_spatial_file(_shapefile_from_zip(content, temp_dir))

    suffix = ".csv" if file_ext == ".csv" else file_ext
    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(content)
        tmp_path = tmp_file.name
    try:
        return load_csv_points(tmp_path) if file_ext == ".csv" else load_spatial_file(tmp_path)
    finally:
        os.unlink(tmp_path)


def _scrub_temp_paths(message: str, filename: str) -> str:
    """Put the uploaded file's name in place of the temporary paths a reader's error
    message carries, which say nothing to the user and describe the server."""
    import re

    name = filename or "the file"
    for root in {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}:
        message = re.sub(re.escape(root) + r"[^\s'\",;)]*", lambda _m: name, message)
    return message


@router.get("/upload/{file_id}")
async def get_upload_info(file_id: str):
    """Get metadata for an uploaded file"""
    if file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="File not found")
    
    # The stored entry also holds the GeoDataFrame, which is not JSON-serialisable
    info = uploaded_files[file_id]
    return JSONResponse(content={
        "file_id": file_id,
        "filename": info["filename"],
        "geometry_type": info["geometry_type"],
        "feature_count": info["feature_count"],
        "crs": info["crs"],
        "bounds": info["bounds"]
    })


@router.delete("/upload/{file_id}")
async def delete_upload(file_id: str):
    """Delete an uploaded file"""
    if not forget_upload(file_id):
        raise HTTPException(status_code=404, detail="File not found")

    return JSONResponse(content={"message": "File deleted successfully"})

