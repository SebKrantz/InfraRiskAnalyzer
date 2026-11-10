"""
Geospatial utility functions
"""

import geopandas as gpd
import pandas as pd
import rasterio
import numpy as np
from shapely.geometry import Point, LineString
from shapely import wkt as shapely_wkt
from typing import Optional, Callable, Tuple
from pyproj import Geod
import csv
import threading


_nodata_mask_thread_local = threading.local()


def mask_raster_nodata(data: np.ndarray, nodata: Optional[float]) -> np.ndarray:
    """Replace a raster's declared nodata with NaN. Returns the array to use.

    Prefer this over inferring nodata from the values: the declared value is
    the only thing that distinguishes "no data" from a real measurement that
    happens to look like a sentinel. Reading it is free — it comes from the
    header, not the pixels.

    No-op in three cases:
      * nodata is None — nothing is declared, so nothing can be masked.
      * nodata is NaN — those cells already read as NaN.
      * nodata is 0 — the nine GIRI flood layers are uint32 with nodata=0,
        where 0 means "no flood depth here" and not "unknown"; valid depths
        start at 1 mm. Masking them erases every dry cell from the analysis
        and its exports while the map still draws the asset.

    Integer input is copied to float64 (NaN needs a float). Float input is
    modified in place, so pass a freshly read array, not a cached one.
    """
    if nodata is None or nodata == 0 or np.isnan(nodata):
        return data

    if data.dtype.kind != "f":
        data = data.astype(np.float64)

    # Reuse one boolean buffer per thread rather than allocating a mask per tile
    buf = getattr(_nodata_mask_thread_local, "mask_buf", None)
    if buf is None or buf.shape != data.shape:
        buf = np.empty(data.shape, dtype=np.bool_)
        _nodata_mask_thread_local.mask_buf = buf

    np.equal(data, nodata, out=buf)
    np.putmask(data, buf, np.nan)
    return data


def _find_wkt_geometry_column(columns) -> Optional[str]:
    """
    Return the name of a likely WKT geometry column, or None.

    Column names are assumed already normalized to lowercase. Matches an exact
    'geometry'/'geom'/'wkt' first, then any column starting with
    'geometry'/'geom' or containing 'wkt' (e.g. 'geometry_wkt', 'the_geom').
    """
    cols = list(columns)
    for exact in ('geometry', 'geom', 'wkt'):
        if exact in cols:
            return exact
    for c in cols:
        if c.startswith('geometry') or c.startswith('geom') or 'wkt' in c:
            return c
    return None


def _load_csv_wkt(df: pd.DataFrame, geom_col: str, original_columns) -> gpd.GeoDataFrame:
    """
    Build a GeoDataFrame from a CSV column of WKT geometry strings.

    Geometries may be POINT or LINESTRING (or any other WKT type). Rows with
    missing/blank/unparseable geometry are dropped. The raw WKT text column is
    removed from the attributes. Coordinates are assumed to be WGS84 (EPSG:4326).
    """
    geometries = []
    valid_positions = []
    for pos, value in enumerate(df[geom_col].tolist()):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if not text:
            continue
        try:
            geom = shapely_wkt.loads(text)
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        geometries.append(geom)
        valid_positions.append(pos)

    if not geometries:
        raise ValueError(
            f"Found a geometry column '{geom_col}' but could not parse any valid "
            f"WKT geometries from it (expected POINT or LINESTRING). "
            f"Original columns: {original_columns}."
        )

    dropped = len(df) - len(geometries)
    if dropped > 0:
        print(f"Warning: {dropped}/{len(df)} rows have missing/invalid geometry and will be dropped")

    # Keep attributes from valid rows; drop the raw WKT text column (now redundant)
    attrs = df.iloc[valid_positions].drop(columns=[geom_col]).reset_index(drop=True)
    gdf = gpd.GeoDataFrame(attrs, geometry=geometries, crs="EPSG:4326")
    return gdf


def load_csv_points(file_path: str) -> gpd.GeoDataFrame:
    """
    Load a CSV file with point coordinates or WKT geometries.

    Supports various column name variations (case-agnostic):
    - lat/lon, lat/lng, latitude/longitude
    - y/x (for Y/X coordinate order)

    If no coordinate columns are present, falls back to a WKT geometry column
    (e.g. 'geometry', 'geom', 'wkt', 'geometry_wkt') containing POINT or
    LINESTRING WKT strings.

    Supports both comma and semicolon delimiters (auto-detected).

    Args:
        file_path: Path to .csv file

    Returns:
        GeoDataFrame with Point or LineString geometries
    """
    # Read CSV - auto-detect delimiter and encoding
    # Try multiple encodings to handle different file formats
    import csv
    encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'iso-8859-1', 'cp1252', 'windows-1252']
    
    delimiter = ','
    encoding = 'utf-8'
    
    for enc in encodings:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                first_line = f.readline()
                # Try to detect delimiter
                sniffer = csv.Sniffer()
                try:
                    delimiter = sniffer.sniff(first_line, delimiters=',;').delimiter
                except:
                    # Fallback: check if semicolon is present, otherwise use comma
                    delimiter = ';' if ';' in first_line else ','
                encoding = enc
                break
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    # Read with detected delimiter and encoding
    df = pd.read_csv(file_path, sep=delimiter, encoding=encoding)
    
    # Normalize column names to lowercase and strip whitespace
    original_columns = list(df.columns)
    df.columns = df.columns.str.lower().str.strip()
    
    # Find coordinate columns (case-agnostic matching)
    lat_col = None
    lon_col = None
    
    # Try different column name patterns (in priority order)
    # Priority: exact matches first, then check alternatives
    lat_patterns = ['lat', 'latitude', 'y']
    lon_patterns = ['lon', 'lng', 'longitude', 'long', 'x']
    
    # Check for latitude column (try patterns in order of preference)
    for pattern in lat_patterns:
        # Check for exact match (case-insensitive since we already normalized)
        if pattern in df.columns:
            lat_col = pattern
            break
    
    # Check for longitude column
    for pattern in lon_patterns:
        if pattern in df.columns:
            lon_col = pattern
            break
    
    # Special handling: if we found x and y but no lat/lon, 
    # assume y=latitude (north-south) and x=longitude (east-west)
    # This is the standard geographic convention
    
    if lat_col is None or lon_col is None:
        # No coordinate columns: fall back to a WKT geometry column if present
        geom_col = _find_wkt_geometry_column(df.columns)
        if geom_col is not None:
            return _load_csv_wkt(df, geom_col, original_columns)

        # Provide helpful error message with both original and normalized column names
        raise ValueError(
            f"Could not find coordinate or geometry columns in CSV. "
            f"Expected coordinate columns like: lat/lon, lat/lng, latitude/longitude, or y/x, "
            f"or a WKT geometry column like: geometry, geom, wkt, geometry_wkt. "
            f"Found columns (original): {original_columns}. "
            f"Found columns (normalized): {list(df.columns)}"
        )
    
    # Extract coordinates
    lat = pd.to_numeric(df[lat_col], errors='coerce')
    lon = pd.to_numeric(df[lon_col], errors='coerce')
    
    # Check for missing values
    missing = lat.isna() | lon.isna()
    if missing.any():
        missing_count = missing.sum()
        total_count = len(df)
        print(f"Warning: {missing_count}/{total_count} rows have missing coordinates and will be dropped")
        lat = lat[~missing]
        lon = lon[~missing]
        df = df[~missing]
    # Validate coordinate ranges
    if lat.min() < -90 or lat.max() > 90:
        raise ValueError(f"Latitude values out of range [-90, 90]. Found range: [{lat.min()}, {lat.max()}]")
    
    if lon.min() < -180 or lon.max() > 180:
        raise ValueError(f"Longitude values out of range [-180, 180]. Found range: [{lon.min()}, {lon.max()}]")
    
    # Create Point geometries
    geometry = [Point(lon_val, lat_val) for lon_val, lat_val in zip(lon, lat)]
    
    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    
    return gdf


def load_spatial_file(file_path: str) -> gpd.GeoDataFrame:
    """
    Load a spatial file (Shapefile, GeoPackage, or GeoJSON)

    Args:
        file_path: Path to .shp, .gpkg, or .geojson file

    Returns:
        GeoDataFrame
    """
    try:
        # Use pyogrio for faster I/O if available
        gdf = gpd.read_file(file_path, engine="pyogrio")
    except:
        # Fallback to fiona
        gdf = gpd.read_file(file_path)
    
    # Ensure CRS is set (if not, assume WGS84)
    if gdf.crs is None:
        gdf.set_crs("EPSG:4326", inplace=True)
    
    return gdf


def convert_polygons_to_centroids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Convert Polygon/MultiPolygon geometries to their centroids (Point).

    If the GeoDataFrame contains only Polygon and/or MultiPolygon, replaces
    the geometry column with centroids so the dataset becomes a point layer.
    Mixed geometry (e.g. Point + Polygon) is not supported and raises.

    Args:
        gdf: GeoDataFrame (any CRS; conversion happens before reprojection).

    Returns:
        GeoDataFrame with Point geometries if input was polygon-only;
        otherwise unchanged.
    """
    if len(gdf) == 0:
        return gdf

    geom_types = gdf.geometry.geom_type.unique()
    unique_types = set(geom_types)
    polygon_types = {"Polygon", "MultiPolygon"}
    point_line_types = {"Point", "MultiPoint", "LineString", "MultiLineString"}

    if unique_types <= polygon_types:
        gdf = gdf.copy()
        gdf.geometry = gdf.geometry.centroid
        return gdf

    if unique_types & polygon_types and unique_types & point_line_types:
        raise ValueError(
            "Mixed geometry with polygons not supported. "
            "Use only Point, LineString, or only Polygon/MultiPolygon."
        )

    return gdf


def validate_geometry_type(gdf: gpd.GeoDataFrame) -> str:
    """
    Validate that geometry type is Point or LineString

    LineString and MultiLineString may appear in the same layer (both are line mode).

    Args:
        gdf: GeoDataFrame

    Returns:
        "Point" or "LineString"
    """
    unique_types = set(gdf.geometry.geom_type.unique())
    line_types = {"LineString", "MultiLineString"}

    if unique_types and unique_types <= line_types:
        return "LineString"

    if len(unique_types) > 1:
        raise ValueError(
            f"Mixed geometry types found: {unique_types}. Only Point or LineString are supported."
        )

    geom_type = next(iter(unique_types)) if unique_types else None
    if geom_type is None:
        raise ValueError("No geometry types found (empty layer?).")

    if geom_type not in ["Point", "LineString", "MultiPoint", "MultiLineString"]:
        raise ValueError(
            f"Geometry type '{geom_type}' not supported. Only Point or LineString are allowed."
        )

    if geom_type in ["Point", "MultiPoint"]:
        return "Point"
    return "LineString"


def vulnerability_interp_from_arrays(
    intensity: np.ndarray, proportion_destroyed: np.ndarray
) -> Callable[[float], float]:
    """Linear interpolation on sorted intensity / proportion_destroyed arrays.

    Takes a scalar or an array; array input is what lets the callers apply a
    curve to a whole dataset at once instead of a value at a time.

    np.interp already returns the end values outside the curve's range, which
    is what the explicit clamping branches here used to do by hand.

    A NaN intensity yields 0.0 — a cell the raster does not measure is
    reported as undamaged rather than dropped, so that a feature never
    silently disappears from a damage total.
    """
    if len(intensity) == 0:
        raise ValueError("Empty vulnerability curve arrays")

    def interpolate(intensity_value):
        values = np.asarray(intensity_value, dtype=np.float64)
        out = np.interp(values, intensity, proportion_destroyed)
        out = np.where(np.isnan(values), 0.0, out)
        return float(out) if out.ndim == 0 else out

    return interpolate


def parse_vulnerability_curve_data(
    csv_path: str,
) -> Tuple[
    Callable[[float], float],
    np.ndarray,
    np.ndarray,
    Optional[Callable[[float], float]],
    Optional[Callable[[float], float]],
]:
    """
    Parse vulnerability curve CSV; return interp and sorted numpy arrays (for caching / export).

    Supported CSV formats (header row optional — non-numeric rows are silently skipped):

    * **2 columns**: ``intensity, proportion_destroyed``
      → standard single-curve mode; no uncertainty bounds.

    * **4 columns**: ``intensity, proportion_lower, proportion_central, proportion_upper``
      → uncertainty-band mode; lower/upper interpolators are returned in addition to the
      central one. The central column (col 2) is used as the primary vulnerability curve.

    Returns
    -------
    tuple of 5 elements:
        (central_interp, intensity_arr, proportion_central_arr, lower_interp, upper_interp)

    ``lower_interp`` and ``upper_interp`` are ``None`` when only 2 columns are present.
    """
    encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'iso-8859-1', 'cp1252', 'windows-1252']
    delimiter = ','
    encoding = 'utf-8'

    for enc in encodings:
        try:
            with open(csv_path, 'r', encoding=enc) as f:
                first_line = f.readline()
                sniffer = csv.Sniffer()
                try:
                    delimiter = sniffer.sniff(first_line, delimiters=',;').delimiter
                except Exception:
                    delimiter = ';' if ';' in first_line else ','
                encoding = enc
                break
        except (UnicodeDecodeError, UnicodeError):
            continue

    df = pd.read_csv(csv_path, sep=delimiter, encoding=encoding, header=None)

    if df.shape[1] < 2:
        raise ValueError("Vulnerability curve CSV must have at least 2 columns")

    has_bounds = df.shape[1] >= 4

    intensity = pd.to_numeric(df.iloc[:, 0], errors='coerce')

    if has_bounds:
        # 4-column format: intensity | lower | central | upper
        proportion_lower_raw = pd.to_numeric(df.iloc[:, 1], errors='coerce')
        proportion_destroyed = pd.to_numeric(df.iloc[:, 2], errors='coerce')
        proportion_upper_raw = pd.to_numeric(df.iloc[:, 3], errors='coerce')
        valid_mask = ~(
            intensity.isna()
            | proportion_destroyed.isna()
            | proportion_lower_raw.isna()
            | proportion_upper_raw.isna()
        )
    else:
        # 2-column format: intensity | proportion_destroyed
        proportion_destroyed = pd.to_numeric(df.iloc[:, 1], errors='coerce')
        valid_mask = ~(intensity.isna() | proportion_destroyed.isna())

    intensity = intensity[valid_mask].values
    proportion_destroyed = proportion_destroyed[valid_mask].values

    if len(intensity) == 0:
        raise ValueError("No valid data rows found in vulnerability curve CSV")

    if (proportion_destroyed < 0).any() or (proportion_destroyed > 1).any():
        raise ValueError("Proportion destroyed (central) values must be between 0 and 1")

    sort_idx = np.argsort(intensity)
    intensity = intensity[sort_idx]
    proportion_destroyed = proportion_destroyed[sort_idx]

    central_interp = vulnerability_interp_from_arrays(intensity, proportion_destroyed)

    if has_bounds:
        proportion_lower = proportion_lower_raw[valid_mask].values[sort_idx]
        proportion_upper = proportion_upper_raw[valid_mask].values[sort_idx]

        if (proportion_lower < 0).any() or (proportion_lower > 1).any():
            raise ValueError("proportion_lower values must be between 0 and 1")
        if (proportion_upper < 0).any() or (proportion_upper > 1).any():
            raise ValueError("proportion_upper values must be between 0 and 1")

        lower_interp: Optional[Callable[[float], float]] = vulnerability_interp_from_arrays(
            intensity, proportion_lower
        )
        upper_interp: Optional[Callable[[float], float]] = vulnerability_interp_from_arrays(
            intensity, proportion_upper
        )
    else:
        lower_interp = None
        upper_interp = None

    return central_interp, intensity, proportion_destroyed, lower_interp, upper_interp


def parse_vulnerability_curve(csv_path: str) -> Callable[[float], float]:
    """
    Parse a vulnerability curve CSV file and return an interpolation function.

    CSV format: Two columns (intensity, proportion_destroyed)
    """
    interp, _, _, _, _ = parse_vulnerability_curve_data(csv_path)
    return interp


def sample_points_along_line(
    coords: list, geod: Geod, interval_meters: float = 100.0
) -> Tuple[list, float]:
    """
    Sample a line no more than interval_meters apart, keeping every vertex.

    Returns (sampled_points, total_length_m); ([], 0.0) for a degenerate line.

    The samples are a superset of the input vertices, so joining them reproduces
    the input geometry and the length reported is the geometry's own length.
    Resampling at a fixed interval instead — keeping the interpolated positions
    and dropping the vertices — cuts every corner in between. On the Congo fibre
    network, whose vertices sit a median 38 m apart, that shortened the network
    by 1.9% and individual lines by up to 21%.

    Points added inside a span lie on the geodesic between its two vertices, so
    densifying a span leaves its length unchanged.
    """
    n = len(coords)
    if n < 2:
        return [], 0.0

    lons = np.fromiter((c[0] for c in coords), dtype=np.float64, count=n)
    lats = np.fromiter((c[1] for c in coords), dtype=np.float64, count=n)

    _, _, spans = geod.inv(
        lons[:-1], lats[:-1], lons[1:], lats[1:], return_back_azimuth=True
    )
    total_length_m = float(spans.sum())
    if not total_length_m > 0.0:
        return [], 0.0

    # Sub-spans each vertex pair splits into to stay under the sampling interval
    splits = np.ceil(spans / interval_meters).astype(np.int64)
    np.maximum(splits, 1, out=splits)

    points = [(float(lons[0]), float(lats[0]))]
    for i in range(n - 1):
        extra = int(splits[i]) - 1
        if extra > 0:
            between = geod.inv_intermediate(
                lons[i], lats[i], lons[i + 1], lats[i + 1],
                npts=extra, initial_idx=1, terminus_idx=1,
                return_back_azimuth=True,
            )
            points.extend(zip(between.lons, between.lats))
        points.append((float(lons[i + 1]), float(lats[i + 1])))

    return points, total_length_m


# Fraction of a pixel within which a point counts as on a pixel edge. The float
# error of the inverse transform is ~1e-10 of a pixel at global scale; 1e-6 of a
# 1/1200-degree pixel is 0.1 mm.
_EDGE_TOL = 1e-6


def sample_raster_at_points(
    raster_path: str,
    x_coords: np.ndarray,
    y_coords: np.ndarray,
    tile_size: float = 1.0,
    max_workers: int = 16,
) -> np.ndarray:
    """
    Sample a raster at scattered lon/lat positions. Returns float64; NaN where a
    point falls outside the raster.

    Points are grouped into tile_size-degree tiles so each part of the raster is
    fetched once, and tiles are read in parallel: the hazard layers are remote
    COGs, so the cost is round trips rather than pixels.

    Pixel indices come from the dataset's own transform and are shifted by the
    window's integer offset. Recomputing them from the window's transform — as
    this did — puts the window offset back in as a float, and a point sitting
    exactly on a pixel boundary then lands on the neighbouring pixel: on the
    Congo cell towers that made 4 of 1594 points disagree with rasterio's own
    sample() by up to 274 mm of flood depth.

    A point on a pixel edge belongs to the pixel the edge starts (east of a
    column edge, south of a row edge), but the inverse transform puts it a
    float error to either side, and which side depends on the arithmetic:
    rasterio's rowcol() sends 69% of the edges of a 1/1200-degree grid that
    are exact decimals (every multiple of 0.0025) to the neighbouring pixel in
    1.4 and 41% in 1.5, and the two versions disagreed on 5 of those towers.
    So the index is floored after adding _EDGE_TOL, which is far above the
    float error and far below any real distance.
    """
    from concurrent.futures import ThreadPoolExecutor
    from rasterio.windows import Window

    n_points = len(x_coords)
    values = np.full(n_points, np.nan, dtype=np.float64)
    if n_points == 0:
        return values

    with rasterio.open(raster_path) as src:
        transform = src.transform
        height, width = src.height, src.width

    cols_f, rows_f = ~transform * (
        np.asarray(x_coords, dtype=np.float64), np.asarray(y_coords, dtype=np.float64)
    )
    rows = np.floor(rows_f + _EDGE_TOL).astype(np.int64)
    cols = np.floor(cols_f + _EDGE_TOL).astype(np.int64)

    # Off-raster is genuinely unknown, and must not be confused with a zero
    on_raster = (rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)
    if not on_raster.any():
        return values

    tile_x = np.floor(x_coords / tile_size).astype(np.int64)
    tile_y = np.floor(y_coords / tile_size).astype(np.int64)
    tile_keys = np.where(on_raster, (tile_x + 180) * 1000 + (tile_y + 90), -1)

    # Group by tile with a single sort. Testing `tile_keys == key` inside every
    # tile task instead costs O(points x tiles), which takes over once a dataset
    # runs to hundreds of thousands of sample points.
    order = np.argsort(tile_keys, kind="stable")
    sorted_keys = tile_keys[order]
    starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
    groups = [g for g in np.split(order, starts[1:]) if tile_keys[g[0]] >= 0]
    if not groups:
        return values

    def process_tile(idx: np.ndarray):
        r, c = rows[idx], cols[idx]
        row_off, col_off = int(r.min()), int(c.min())
        window = Window(
            col_off, row_off,
            int(c.max()) - col_off + 1, int(r.max()) - row_off + 1,
        )
        try:
            with rasterio.open(raster_path) as tile_src:
                data = tile_src.read(1, window=window)
                data = mask_raster_nodata(data, tile_src.nodata)
                return idx, data[r - row_off, c - col_off]
        except Exception as exc:
            # A failed read is unknown hazard, not absent hazard; say so, because
            # NaN here silently becomes "unaffected" downstream.
            print(
                f"Warning: could not read hazard raster window {window} "
                f"({len(idx)} sample points): {type(exc).__name__}: {exc}"
            )
            return idx, np.full(len(idx), np.nan)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(groups))) as executor:
        for idx, vals in executor.map(process_tile, groups):
            values[idx] = vals

    return values


def pairwise_mean(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Mean of each consecutive pair, over whichever endpoints are valid.

    NaN for a pair with no valid endpoint. Vectorized equivalent of the
    val1/val2 branches this replaces.
    """
    filled = np.where(valid, values, 0.0)
    total = filled[:-1] + filled[1:]
    count = valid[:-1].astype(np.float64) + valid[1:].astype(np.float64)
    return np.where(count > 0.0, total / np.maximum(count, 1.0), np.nan)


def analyze_intersection(
    infrastructure_gdf: gpd.GeoDataFrame,
    hazard_raster_path: str,
    geometry_type: str,
    intensity_threshold: Optional[float] = None,
    cached_raster_values: Optional[np.ndarray] = None,
    vulnerability_curve_interp: Optional[Callable[[float], float]] = None,
    replacement_value: Optional[float] = None,
    vulnerability_curve_lower_interp: Optional[Callable[[float], float]] = None,
    vulnerability_curve_upper_interp: Optional[Callable[[float], float]] = None,
) -> dict:
    """
    Analyze intersection between infrastructure and hazard raster

    All hazard rasters use EPSG:4326 (WGS84) as their CRS.

    Args:
        infrastructure_gdf: GeoDataFrame with infrastructure assets
        hazard_raster_path: Path or URL to hazard raster (assumed EPSG:4326)
        geometry_type: Geometry type ("Point" or "LineString") from stored metadata
        intensity_threshold: Optional threshold for filtering hazard intensity
        cached_raster_values: Optional pre-sampled raster values (for threshold changes)

    Returns:
        Dictionary with analysis results:
        - affected_count/unaffected_count (for points)
        - affected_meters/unaffected_meters (for lines)
        - full_gdf: GeoDataFrame with all features and affected status
        - raster_values: (for points) sampled values for caching
    """
    if len(infrastructure_gdf) == 0:
        return {
            "affected_count": 0,
            "unaffected_count": 0,
            "affected_meters": 0.0,
            "unaffected_meters": 0.0
        }

    has_bounds = (
        vulnerability_curve_lower_interp is not None
        and vulnerability_curve_upper_interp is not None
    )
    vuln_on = vulnerability_curve_interp is not None and replacement_value is not None

    if geometry_type == "Point":
        n_points = len(infrastructure_gdf)

        # Work on a copy. The caller's GeoDataFrame is the stored upload, and
        # every analysis result is cached by threshold; writing these columns in
        # place made all of those cached results the same object, so re-running
        # at a new threshold rewrote the exports of the runs before it.
        infrastructure_gdf = infrastructure_gdf.copy()

        if cached_raster_values is not None and len(cached_raster_values) == n_points:
            raster_values = cached_raster_values
        else:
            raster_values = sample_raster_at_points(
                hazard_raster_path,
                infrastructure_gdf.geometry.x.to_numpy(),
                infrastructure_gdf.geometry.y.to_numpy(),
            )

        valid_mask = ~np.isnan(raster_values)
        if intensity_threshold is not None:
            affected_mask = valid_mask & (raster_values >= intensity_threshold)
        else:
            affected_mask = valid_mask & (raster_values > 0)

        # NaN -> None so the GeoJSON response stays serializable
        infrastructure_gdf['exposure_level'] = pd.Series(
            np.where(valid_mask, raster_values, np.nan), index=infrastructure_gdf.index
        ).replace({np.nan: None})
        infrastructure_gdf['affected'] = affected_mask

        total_damage_cost = 0.0
        total_damage_cost_lower = 0.0
        total_damage_cost_upper = 0.0

        if vuln_on:
            # Damage is computed for AFFECTED points only. The curve returns a
            # positive damage ratio well below the exposure threshold, so summing
            # it over every point made the total damage cost independent of the
            # threshold while the reported damage ratio — already averaged over
            # affected points only — moved with it. What is not exposed takes no
            # damage; the threshold governs both figures or neither.
            vulnerability_values = np.where(
                affected_mask,
                np.asarray(vulnerability_curve_interp(raster_values), dtype=np.float64),
                0.0,
            )
            damage_cost_values = replacement_value * vulnerability_values
            total_damage_cost = float(damage_cost_values.sum())

            infrastructure_gdf['vulnerability'] = vulnerability_values
            infrastructure_gdf['damage_cost'] = damage_cost_values

            if has_bounds:
                dc_lower = replacement_value * np.where(
                    affected_mask,
                    np.asarray(vulnerability_curve_lower_interp(raster_values), dtype=np.float64),
                    0.0,
                )
                dc_upper = replacement_value * np.where(
                    affected_mask,
                    np.asarray(vulnerability_curve_upper_interp(raster_values), dtype=np.float64),
                    0.0,
                )
                total_damage_cost_lower = float(dc_lower.sum())
                total_damage_cost_upper = float(dc_upper.sum())
                infrastructure_gdf['damage_cost_lower'] = dc_lower
                infrastructure_gdf['damage_cost_upper'] = dc_upper
        else:
            infrastructure_gdf['vulnerability'] = None
            infrastructure_gdf['damage_cost'] = None

        affected_count = int(affected_mask.sum())

        result = {
            "affected_count": affected_count,
            "unaffected_count": n_points - affected_count,
            "affected_meters": 0.0,
            "unaffected_meters": 0.0,
            "full_gdf": infrastructure_gdf,
            "raster_values": raster_values,
        }

        if vuln_on:
            result["total_damage_cost"] = total_damage_cost
            if has_bounds:
                result["total_damage_cost_lower"] = total_damage_cost_lower
                result["total_damage_cost_upper"] = total_damage_cost_upper

        return result

    # LineString
    geod = Geod(ellps="WGS84")

    use_cache = (cached_raster_values is not None and
                 isinstance(cached_raster_values, dict) and
                 'line_data' in cached_raster_values and
                 'raster_values' in cached_raster_values)

    if use_cache:
        line_data = cached_raster_values['line_data']
        all_raster_values = cached_raster_values['raster_values']
    else:
        # Phase 1: sample every line, recording the upload row it came from
        line_data = []
        sample_x: list = []
        sample_y: list = []

        for feature_pos, (_idx, row) in enumerate(infrastructure_gdf.iterrows()):
            line = row.geometry
            if line is None or line.is_empty:
                continue
            if line.geom_type == 'MultiLineString':
                parts = list(line.geoms)
            elif line.geom_type == 'LineString':
                parts = [line]
            else:
                continue

            row_dict = row.to_dict()
            for single_line in parts:
                sampled_points, total_length_m = sample_points_along_line(
                    list(single_line.coords), geod
                )
                if len(sampled_points) < 2:
                    continue

                line_data.append({
                    'row_dict': row_dict,
                    'sampled_points': sampled_points,
                    'total_length_m': total_length_m,
                    'single_line': single_line,
                    # 1-based upload row order, recorded here rather than
                    # recovered afterwards by walking every geometry a second
                    # time — that doubled the cost of the most expensive phase
                    # and paid it again on every cached threshold change.
                    'line_id': feature_pos + 1,
                })
                sample_x.extend(p[0] for p in sampled_points)
                sample_y.extend(p[1] for p in sampled_points)

        if not line_data:
            return {
                "affected_count": 0,
                "unaffected_count": 0,
                "affected_meters": 0.0,
                "unaffected_meters": 0.0,
                "full_gdf": gpd.GeoDataFrame(geometry=[], crs=infrastructure_gdf.crs)
            }

        # Phase 2: one pass over the raster for every sample point at once
        all_raster_values = sample_raster_at_points(
            hazard_raster_path,
            np.asarray(sample_x, dtype=np.float64),
            np.asarray(sample_y, dtype=np.float64),
        )

        at = 0
        for ld in line_data:
            n_pts = len(ld['sampled_points'])
            ld['raster_values'] = all_raster_values[at:at + n_pts]
            at += n_pts

    # Phase 3: split each line into runs of equal affected status (always runs)
    affected_length = 0.0
    unaffected_length = 0.0
    total_damage_cost = 0.0
    total_damage_cost_lower = 0.0
    total_damage_cost_upper = 0.0
    segment_rows = []

    for ld in line_data:
        pts = ld['sampled_points']
        rv = np.asarray(ld['raster_values'], dtype=np.float64)
        n_pts = len(pts)

        lons = np.fromiter((p[0] for p in pts), dtype=np.float64, count=n_pts)
        lats = np.fromiter((p[1] for p in pts), dtype=np.float64, count=n_pts)
        # One geodesic call per line, not one per 100 m span
        _, _, span_len = geod.inv(
            lons[:-1], lats[:-1], lons[1:], lats[1:], return_back_azimuth=True
        )

        valid = ~np.isnan(rv)
        if intensity_threshold is not None:
            point_affected = valid & (rv >= intensity_threshold)
        else:
            point_affected = valid & (rv > 0)

        span_intensity = pairwise_mean(rv, valid)
        measured = ~np.isnan(span_intensity)
        cs_len = np.concatenate(([0.0], np.cumsum(span_len)))
        cs_int = np.concatenate(([0.0], np.cumsum(
            np.where(measured, span_intensity, 0.0) * span_len)))
        cs_int_len = np.concatenate(([0.0], np.cumsum(np.where(measured, span_len, 0.0))))

        if vuln_on:
            vuln_pt = np.asarray(vulnerability_curve_interp(rv), dtype=np.float64)
            span_vuln = np.nan_to_num(
                pairwise_mean(np.where(valid, vuln_pt, 0.0), valid), nan=0.0)
            cs_dmg = np.concatenate(([0.0], np.cumsum(span_vuln * span_len)))
            if has_bounds:
                cs_dmg_lo = np.concatenate(([0.0], np.cumsum(np.nan_to_num(
                    pairwise_mean(
                        np.where(valid, np.asarray(
                            vulnerability_curve_lower_interp(rv), dtype=np.float64), 0.0),
                        valid), nan=0.0) * span_len)))
                cs_dmg_hi = np.concatenate(([0.0], np.cumsum(np.nan_to_num(
                    pairwise_mean(
                        np.where(valid, np.asarray(
                            vulnerability_curve_upper_interp(rv), dtype=np.float64), 0.0),
                        valid), nan=0.0) * span_len)))

        # A span whose endpoints disagree ends the run; as before, that whole
        # span is attributed to the status of its second endpoint.
        cuts = np.flatnonzero(point_affected[1:] != point_affected[:-1])
        starts = np.concatenate(([0], cuts))
        ends = np.concatenate((cuts, [n_pts - 1]))

        row_dict = ld['row_dict']
        for a, b in zip(starts, ends):
            if b <= a:  # a run with no span of its own contributes no length
                continue
            a, b = int(a), int(b)
            affected = bool(point_affected[a] if a == 0 else point_affected[a + 1])

            segment_length_m = float(cs_len[b] - cs_len[a])
            if affected:
                affected_length += segment_length_m
            else:
                unaffected_length += segment_length_m

            # Length-weighted, to match how vulnerability is averaged below; a
            # plain mean over sample points weighted the final, short span of a
            # line the same as a full one.
            measured_len = cs_int_len[b] - cs_int_len[a]
            avg_value = float((cs_int[b] - cs_int[a]) / measured_len) if measured_len > 0 else None
            seg_vals = rv[a:b + 1]
            max_value = float(np.max(seg_vals[valid[a:b + 1]])) if valid[a:b + 1].any() else None

            seg_row = row_dict.copy()
            seg_row['geometry'] = LineString(pts[a:b + 1])
            seg_row['line_id'] = ld['line_id']
            seg_row['length_m'] = segment_length_m
            seg_row['affected'] = affected
            seg_row['exposure_level_avg'] = avg_value
            seg_row['exposure_level_max'] = max_value

            if vuln_on:
                # Damage for AFFECTED segments only — see the note in the point
                # branch above. Runs are already cut at threshold crossings, so
                # gating on the run's own status needs no re-averaging and keeps
                # the boundary span attributed exactly as the exposure figures
                # attribute it.
                damage_length = float(cs_dmg[b] - cs_dmg[a]) if affected else 0.0
                damage_cost = float(replacement_value * damage_length)
                total_damage_cost += damage_cost
                seg_row['vulnerability'] = (
                    damage_length / segment_length_m if segment_length_m > 0 else 0.0
                )
                seg_row['damage_cost'] = damage_cost
                if has_bounds:
                    dc_lo = float(
                        replacement_value * (cs_dmg_lo[b] - cs_dmg_lo[a])) if affected else 0.0
                    dc_hi = float(
                        replacement_value * (cs_dmg_hi[b] - cs_dmg_hi[a])) if affected else 0.0
                    total_damage_cost_lower += dc_lo
                    total_damage_cost_upper += dc_hi
                    seg_row['damage_cost_lower'] = dc_lo
                    seg_row['damage_cost_upper'] = dc_hi

            segment_rows.append(seg_row)

    if segment_rows:
        segment_gdf = gpd.GeoDataFrame(segment_rows, crs=infrastructure_gdf.crs)
        segment_gdf['affected'] = segment_gdf['affected'].astype(bool)
    else:
        segment_gdf = gpd.GeoDataFrame(geometry=[], crs=infrastructure_gdf.crs)

    result = {
        "affected_count": 0,
        "unaffected_count": 0,
        "affected_meters": float(affected_length),
        "unaffected_meters": float(unaffected_length),
        "full_gdf": segment_gdf,
        "line_data": line_data,
        "raster_values": all_raster_values
    }

    if vuln_on:
        result["total_damage_cost"] = total_damage_cost
        if has_bounds:
            result["total_damage_cost_lower"] = total_damage_cost_lower
            result["total_damage_cost_upper"] = total_damage_cost_upper

    return result
