"""
Tile endpoints for serving COG tiles
"""
import io
import math
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple
import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from rasterio.crs import CRS
import numpy as np
from PIL import Image
from matplotlib import colormaps
from cachetools import TTLCache

from app.utils.geospatial import mask_raster_nodata

router = APIRouter()

# Thread pool for blocking I/O operations
_tile_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tile_worker")

# Tile cache: cache rendered PNG tiles (key: "hazard_id:z:x:y:palette")
# Max 2000 tiles, 5 minute TTL
_tile_cache: TTLCache = TTLCache(maxsize=2000, ttl=300)
_tile_cache_lock = threading.Lock()

# Thread-local storage for rasterio datasets (each thread gets its own dataset)
# This avoids thread-safety issues with shared datasets
_thread_local = threading.local()
_WGS84 = CRS.from_epsg(4326)


def _get_thread_local_dataset(cog_url: str) -> rasterio.DatasetReader:
    """
    Get a thread-local rasterio dataset.
    Each thread maintains its own set of open datasets to avoid thread-safety issues.
    """
    if not hasattr(_thread_local, 'datasets'):
        _thread_local.datasets = {}
    
    if cog_url not in _thread_local.datasets:
        _thread_local.datasets[cog_url] = rasterio.open(cog_url)
    
    return _thread_local.datasets[cog_url]


def apply_colormap(data: np.ndarray, palette: str = 'turbo', vmin: float = None, vmax: float = None) -> np.ndarray:
    """Apply a colormap to raster data"""
    # Get colormap
    cmap = colormaps[palette]
    
    # Handle NaN and invalid values
    valid_mask = ~np.isnan(data) & (data >= 0.0) & (data < 1e15)
    all_valid = np.all(valid_mask)

    if all_valid:
        if vmax > vmin:
            normalized = (np.sqrt(data) - vmin) / (vmax - vmin)
        else:
            normalized = np.zeros_like(data, dtype=np.float32)
    else:
        normalized = np.zeros_like(data, dtype=np.float32)
        if vmax > vmin:
            normalized[valid_mask] = (np.sqrt(data[valid_mask]) - vmin) / (vmax - vmin)
    
    # Clip to [0, 1] in place
    np.clip(normalized, 0, 1, out=normalized)
    
    # Apply colormap
    colored = cmap(normalized)
    
    # Convert to uint8 RGB
    colored_uint8 = (colored[:, :, :3] * 255).astype(np.uint8)
    
    return colored_uint8


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Calculate XYZ tile bounds in EPSG:4326."""
    n = 2.0 ** z
    minx = (x / n) * 360.0 - 180.0
    maxx = ((x + 1) / n) * 360.0 - 180.0

    lat_rad_max = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_rad_min = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
    maxy = math.degrees(lat_rad_max)
    miny = math.degrees(lat_rad_min)

    return (minx, miny, maxx, maxy)


def _bounds_in_dataset_crs(
    bounds_wgs84: Tuple[float, float, float, float],
    src: rasterio.DatasetReader
) -> Tuple[float, float, float, float]:
    """Transform EPSG:4326 bounds into dataset CRS if needed."""
    if src.crs is None:
        raise ValueError("Raster dataset missing CRS; expected EPSG:4326 or projected CRS.")
    if src.crs == _WGS84:
        return bounds_wgs84
    return transform_bounds(_WGS84, src.crs, *bounds_wgs84, densify_pts=21)


def _generate_tile_sync(
    cog_url: str,
    z: int,
    x: int,
    y: int,
    palette: str,
    vmin: float,
    vmax: float
) -> bytes:
    """
    Generate a tile synchronously (runs in thread pool).
    Returns PNG bytes. Raises on raster open failure so the endpoint can return 500.
    """
    tile_size = 256
    bounds_wgs84 = _tile_bounds(z, x, y)

    def _open_failed() -> bool:
        """True if we never had a dataset (initial open failed)."""
        return cog_url not in getattr(_thread_local, "datasets", {})

    try:
        # Use thread-local dataset to avoid thread-safety issues
        src = _get_thread_local_dataset(cog_url)
        bounds = _bounds_in_dataset_crs(bounds_wgs84, src)
        window = from_bounds(*bounds, src.transform)
        data = src.read(1, window=window, out_shape=(tile_size, tile_size))
        data = mask_raster_nodata(data, src.nodata)

        colored = apply_colormap(data, palette=palette, vmin=vmin, vmax=vmax)

        img = Image.fromarray(colored, "RGB")
        img_buffer = io.BytesIO()
        img.save(img_buffer, format="PNG", optimize=False)
        return img_buffer.getvalue()

    # RasterioIOError is an OSError, so it is handled before the plain OSError clause
    # below; in the other order the retry after a dropped connection never ran.
    except rasterio.errors.RasterioIOError as e:
        # If we failed on initial open (no dataset cached), propagate so user sees error
        if _open_failed():
            raise
        # Connection may have been closed - clear thread-local cache and retry once
        try:
            _thread_local.datasets[cog_url].close()
        except Exception:
            pass
        del _thread_local.datasets[cog_url]

        try:
            src = _get_thread_local_dataset(cog_url)
            bounds = _bounds_in_dataset_crs(bounds_wgs84, src)
            window = from_bounds(*bounds, src.transform)
            data = src.read(1, window=window, out_shape=(tile_size, tile_size))
            data = mask_raster_nodata(data, src.nodata)

            colored = apply_colormap(data, palette=palette, vmin=vmin, vmax=vmax)

            img = Image.fromarray(colored, "RGB")
            img_buffer = io.BytesIO()
            img.save(img_buffer, format="PNG", optimize=False)
            return img_buffer.getvalue()
        except Exception as retry_e:
            print(f"Tile generation error (retry failed) for z={z},x={x},y={y}: {type(retry_e).__name__}: {retry_e}")
            return _empty_tile()

    except (PermissionError, FileNotFoundError, OSError) as e:
        # Raster path not readable or missing - propagate so endpoint can return 500
        raise

    except Exception as e:
        print(f"Tile generation error for z={z},x={x},y={y}: {type(e).__name__}: {e}")
        return _empty_tile()


def _empty_tile() -> bytes:
    """Generate an empty transparent tile."""
    transparent = np.zeros((256, 256, 4), dtype=np.uint8)
    img = Image.fromarray(transparent, 'RGBA')
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    return img_buffer.getvalue()


@router.get("/tiles/{hazard_id}/{z}/{x}/{y}.png")
async def get_tile(
    hazard_id: str,
    z: int,
    x: int,
    y: int,
    palette: str = Query('turbo', description="Color palette"),
):
    """
    Get a tile from a COG hazard layer.
    
    Uses caching and thread-local connection pooling for efficiency.
    """
    from app.api.hazards import load_hazards_dict, get_hazard_stats_cached
    
    # Check tile cache first
    cache_key = f"{hazard_id}:{z}:{x}:{y}:{palette}"
    with _tile_cache_lock:
        if cache_key in _tile_cache:
            return Response(content=_tile_cache[cache_key], media_type="image/png")
    
    # Get hazard info
    hazards_dict = load_hazards_dict()
    
    if hazard_id not in hazards_dict:
        raise HTTPException(status_code=404, detail=f"Hazard layer '{hazard_id}' not found")
    
    hazard_data = hazards_dict[hazard_id]
    cog_url = hazard_data.get("dataset_url", "")
    
    if not cog_url:
        raise HTTPException(status_code=500, detail="Hazard layer missing dataset_url")
    
    # Get cached min/max values for consistent colormap scaling
    stats = get_hazard_stats_cached(hazard_id)
    if stats:
        vmin, vmax = stats
    else:
        vmin, vmax = 0.0, 1e3  # Fallback if stats not cached yet
    
    # Run blocking tile generation in thread pool
    loop = asyncio.get_event_loop()
    try:
        tile_bytes = await loop.run_in_executor(
            _tile_executor,
            _generate_tile_sync,
            cog_url,
            z,
            x,
            y,
            palette,
            vmin,
            vmax,
        )
    except (PermissionError, FileNotFoundError, OSError, rasterio.errors.RasterioIOError) as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot read hazard raster: {e!s}",
        )

    # Only cache if we have proper stats (avoid caching tiles with wrong colors)
    if stats:
        with _tile_cache_lock:
            _tile_cache[cache_key] = tile_bytes

    return Response(content=tile_bytes, media_type="image/png")
