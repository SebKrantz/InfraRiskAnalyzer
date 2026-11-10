"""
Analysis endpoints for computing intersections
"""

import math
import json
import asyncio
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Optional, Dict, Tuple
import numpy as np

from app.utils.geospatial import analyze_intersection, parse_vulnerability_curve_data, HazardReadError
from app.config import settings
from app.api.upload import uploaded_files
from app.api.hazards import load_hazards_dict

router = APIRouter()

# Thread pool for blocking analysis operations
_analysis_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analysis_worker")

# Both caches hold whole analyses — for a national line network that is a
# segment GeoDataFrame plus every sample point along it, hundreds of MB per
# entry. Dragging the threshold slider produces one analysis-result entry per
# stop, so an unbounded cache grows without limit in a process that is meant to
# stay up. Each upload keeps its own few most recent entries: one cache shared
# by every user let one person's slider push another's results out, and that
# person's exports then failed. A cache miss costs a re-sample (raster values)
# or only arithmetic (analysis results). An upload's entries go with it.
_MAX_LAYERS_PER_UPLOAD = 4
_MAX_RESULTS_PER_UPLOAD = 4

# file_id -> hazard_id -> sampled raster values
# For Points: np.ndarray of raster values
# For LineStrings: dict with 'line_data' and 'raster_values'
# This allows threshold changes without re-sampling the raster
_raster_values_cache: "Dict[str, OrderedDict]" = {}

# file_id -> (hazard_id, threshold) -> full analysis result, for the export endpoints
_analysis_results_cache: "Dict[str, OrderedDict]" = {}

# The analysis handler writes these caches while the export workers read them
_cache_lock = threading.Lock()


def _cache_get(cache: Dict[str, OrderedDict], file_id: str, key) -> Optional[any]:
    """Read through an upload's LRU cache, marking the key as most recently used."""
    with _cache_lock:
        entries = cache.get(file_id)
        if entries is None or key not in entries:
            return None
        entries.move_to_end(key)
        return entries[key]


def _cache_put(cache: Dict[str, OrderedDict], file_id: str, key, value, limit: int) -> None:
    """Write to an upload's LRU cache, evicting its least recently used entry."""
    with _cache_lock:
        entries = cache.setdefault(file_id, OrderedDict())
        entries[key] = value
        entries.move_to_end(key)
        while len(entries) > limit:
            entries.popitem(last=False)


def get_cached_raster_values(file_id: str, hazard_id: str) -> Optional[any]:
    """Get cached raster values if available."""
    return _cache_get(_raster_values_cache, file_id, hazard_id)


def set_cached_raster_values(file_id: str, hazard_id: str, values: any):
    """Cache raster values for future threshold changes."""
    _cache_put(_raster_values_cache, file_id, hazard_id, values, _MAX_LAYERS_PER_UPLOAD)


def clear_raster_cache_for_file(file_id: str):
    """Clear an upload's cached raster values and analysis results (when it is deleted)."""
    with _cache_lock:
        _raster_values_cache.pop(file_id, None)
        _analysis_results_cache.pop(file_id, None)


def get_cached_analysis_result(file_id: str, hazard_id: str, threshold: Optional[float]) -> Optional[dict]:
    """Get cached analysis result if available."""
    return _cache_get(_analysis_results_cache, file_id, (hazard_id, threshold))


def set_cached_analysis_result(file_id: str, hazard_id: str, threshold: Optional[float], result: dict):
    """Cache full analysis result."""
    _cache_put(
        _analysis_results_cache, file_id, (hazard_id, threshold), result, _MAX_RESULTS_PER_UPLOAD
    )


class AnalyzeRequest(BaseModel):
    """Request model for analysis (JSON body)"""
    file_id: str
    hazard_id: str
    # Accepted for older clients and ignored: the raster is the catalogue's, by hazard_id
    hazard_url: Optional[str] = None
    intensity_threshold: Optional[float] = None


@router.post("/analyze")
async def analyze_intersections(
    request_obj: Request,
    request: Optional[AnalyzeRequest] = None,
    # FormData parameters (optional, used when vulnerability curve is provided)
    file_id: Optional[str] = Form(default=None),
    hazard_id: Optional[str] = Form(default=None),
    hazard_url: Optional[str] = Form(default=None),
    intensity_threshold: Optional[float] = Form(default=None),
    vulnerability_curve_file: Optional[UploadFile] = File(default=None),
    replacement_value: Optional[float] = Form(default=None)
):
    """
    Analyze intersections between uploaded infrastructure and hazard raster
    
    Parameters:
    - file_id: ID of uploaded infrastructure file
    - hazard_id: ID of hazard layer (its raster is looked up in hazard_layers.csv)
    - hazard_url: ignored; accepted so that older clients keep working
    - intensity_threshold: Optional threshold for hazard intensity filtering
    - vulnerability_curve_file: Optional CSV file with vulnerability curve (2 columns: intensity, proportion_destroyed)
    - replacement_value: Optional monetary replacement value (for lines, cost per meter)
    
    Returns:
    - Summary statistics (counts/meters affected, or total damage cost if vulnerability analysis enabled)
    - Optional GeoJSON for visualization
    """
    # Check content type to determine request format
    # Note: FastAPI may not parse JSON body when Form params are declared, so we check content-type header
    content_type = request_obj.headers.get("content-type", "")
    
    # Handle both JSON and FormData requests
    if "multipart/form-data" in content_type:
        # FormData request (when vulnerability curve file is included)
        if file_id is None or hazard_id is None:
            raise HTTPException(status_code=400, detail="Missing required parameters: file_id, hazard_id")
        actual_file_id = file_id
        actual_hazard_id = hazard_id
        actual_threshold = intensity_threshold
        actual_vulnerability_file = vulnerability_curve_file
        actual_replacement_value = replacement_value
    else:
        # JSON request - FastAPI should have parsed it into 'request'
        if request is None:
            # If request is None, try to parse JSON body manually
            try:
                body = await request_obj.json()
                request = AnalyzeRequest(**body)
            except Exception:
                raise HTTPException(status_code=400, detail="Missing or invalid request body")
        actual_file_id = request.file_id
        actual_hazard_id = request.hazard_id
        actual_threshold = request.intensity_threshold
        actual_vulnerability_file = None
        actual_replacement_value = None
    
    # Check if file exists
    if actual_file_id not in uploaded_files:
        raise HTTPException(
            status_code=404,
            detail="Uploaded file not found — uploads are deleted after "
            f"{settings.UPLOAD_TTL_HOURS:g} hours without use; please upload it again",
        )
    
    file_info = uploaded_files[actual_file_id]
    geometry_type = file_info["geometry_type"]
    infrastructure_gdf = file_info["gdf"]

    # The raster comes from the catalogue, never from the request: a client-supplied
    # path or URL let a caller make the server open local files or fetch from hosts
    # only it can reach, and cached its values under a real layer's id.
    hazard_data = load_hazards_dict().get(actual_hazard_id)
    if hazard_data is None or not hazard_data.get("dataset_url"):
        raise HTTPException(status_code=404, detail=f"Hazard layer '{actual_hazard_id}' not found")
    actual_hazard_url = hazard_data["dataset_url"]
    
    # Parse vulnerability curve if provided
    vulnerability_curve_interp = None
    vulnerability_curve_lower_interp = None
    vulnerability_curve_upper_interp = None
    vulnerability_curve_intensity: Optional[list] = None
    vulnerability_curve_proportion: Optional[list] = None
    if actual_vulnerability_file is not None:
        try:
            # Read vulnerability curve file
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as tmp_file:
                content = await actual_vulnerability_file.read()
                tmp_file.write(content)
                tmp_path = tmp_file.name

            try:
                (
                    vulnerability_curve_interp,
                    vuln_intensity,
                    vuln_prop,
                    vulnerability_curve_lower_interp,
                    vulnerability_curve_upper_interp,
                ) = parse_vulnerability_curve_data(tmp_path)
                vulnerability_curve_intensity = vuln_intensity.tolist()
                vulnerability_curve_proportion = vuln_prop.tolist()
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Error parsing vulnerability curve: {str(e)}"
            )

    if actual_vulnerability_file is not None and actual_replacement_value is None:
        raise HTTPException(
            status_code=400,
            detail="replacement_value is required when vulnerability_curve_file is provided",
        )
    if actual_replacement_value is not None and actual_vulnerability_file is None:
        raise HTTPException(
            status_code=400,
            detail="vulnerability_curve_file is required when replacement_value is provided",
        )
    if actual_replacement_value is not None and actual_replacement_value <= 0:
        raise HTTPException(
            status_code=400,
            detail="replacement_value must be greater than zero",
        )

    try:
        # Check cache for previously sampled raster values
        cached_values = get_cached_raster_values(actual_file_id, actual_hazard_id)
        
        # Perform spatial intersection analysis in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        analysis_result = await loop.run_in_executor(
            _analysis_executor,
            lambda: analyze_intersection(
                infrastructure_gdf=infrastructure_gdf,
                hazard_raster_path=actual_hazard_url,
                geometry_type=geometry_type,
                intensity_threshold=actual_threshold,
                cached_raster_values=cached_values,
                vulnerability_curve_interp=vulnerability_curve_interp,
                replacement_value=actual_replacement_value,
                vulnerability_curve_lower_interp=vulnerability_curve_lower_interp,
                vulnerability_curve_upper_interp=vulnerability_curve_upper_interp,
            )
        )

        if vulnerability_curve_intensity is not None:
            analysis_result["vulnerability_curve_intensity"] = vulnerability_curve_intensity
            analysis_result["vulnerability_curve_proportion"] = vulnerability_curve_proportion
            analysis_result["vulnerability_replacement_value"] = actual_replacement_value
            # The split GPKG export re-applies the curves, bounds included
            analysis_result["vulnerability_interps"] = (
                vulnerability_curve_interp,
                vulnerability_curve_lower_interp,
                vulnerability_curve_upper_interp,
            )
        
        # Cache raster values for future threshold changes
        if "raster_values" in analysis_result:
            if geometry_type == "Point":
                set_cached_raster_values(actual_file_id, actual_hazard_id, analysis_result["raster_values"])
            elif geometry_type == "LineString" and "line_data" in analysis_result:
                # For LineString, cache both line_data and raster_values
                set_cached_raster_values(actual_file_id, actual_hazard_id, {
                    "line_data": analysis_result["line_data"],
                    "raster_values": analysis_result["raster_values"]
                })
        
        # Cache full analysis result for export endpoints
        set_cached_analysis_result(actual_file_id, actual_hazard_id, actual_threshold, analysis_result)
        
        # Build response - ensure no NaN values
        def safe_float(value):
            """Convert float to JSON-safe value, replacing NaN/Inf with None"""
            if value is None:
                return None
            if isinstance(value, (int, float)):
                if math.isnan(value) or math.isinf(value):
                    return None
                return float(value)
            return value
        
        result = {
            "file_id": actual_file_id,
            "hazard_id": actual_hazard_id,
            "geometry_type": geometry_type,
            "summary": {
                "total_features": file_info["feature_count"]
            }
        }
        
        # Add vulnerability analysis results if available
        if "total_damage_cost" in analysis_result:
            result["summary"]["total_damage_cost"] = safe_float(analysis_result["total_damage_cost"])
        if "total_damage_cost_lower" in analysis_result:
            result["summary"]["total_damage_cost_lower"] = safe_float(analysis_result["total_damage_cost_lower"])
        if "total_damage_cost_upper" in analysis_result:
            result["summary"]["total_damage_cost_upper"] = safe_float(analysis_result["total_damage_cost_upper"])
        if "total_replacement_value" in analysis_result:
            result["summary"]["total_replacement_value"] = safe_float(analysis_result["total_replacement_value"])
        
        if geometry_type == "Point":
            result["summary"]["affected_count"] = analysis_result["affected_count"]
            result["summary"]["unaffected_count"] = analysis_result["unaffected_count"]
            result["summary"]["affected_meters"] = 0.0
            result["summary"]["unaffected_meters"] = 0.0
        elif geometry_type == "LineString":
            result["summary"]["affected_count"] = 0
            result["summary"]["unaffected_count"] = 0
            result["summary"]["affected_meters"] = safe_float(analysis_result["affected_meters"])
            result["summary"]["unaffected_meters"] = safe_float(analysis_result["unaffected_meters"])
        
        # Include full infrastructure GeoJSON with affected status
        try:
            # Get the full GeoDataFrame with affected status from analysis result
            if "full_gdf" in analysis_result:
                display_gdf = analysis_result["full_gdf"].copy()
            else:
                # Fallback: create from original with affected status set to False
                display_gdf = infrastructure_gdf.copy()
                display_gdf['affected'] = False

            if "line_id" in display_gdf.columns:
                display_gdf = display_gdf.drop(columns=["line_id"])
            
            # GeoDataFrame is already in WGS84 (EPSG:4326) and cleaned from upload
            # Convert to GeoJSON, stripping per-feature bbox to avoid
            # MapLibre geojson-vt tiler issues
            geo_dict = display_gdf.__geo_interface__
            if "bbox" in geo_dict:
                del geo_dict["bbox"]
            for feature in geo_dict.get("features", []):
                feature.pop("bbox", None)
            result["infrastructure_features"] = geo_dict
        except Exception as e:
            # If GeoJSON conversion fails, skip it
            print(f"Warning: Could not convert to GeoJSON: {e}")
        
        # Use JSONResponse with a simple encoder for any edge cases
        def json_encoder(obj):
            """Custom JSON encoder that handles NaN and Inf (shouldn't be needed if DataFrame cleaning works)"""
            if isinstance(obj, float):
                if math.isnan(obj):
                    return None
                if math.isinf(obj):
                    return None
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        # Serialize once and hand the bytes straight back. Encoding, decoding and
        # letting JSONResponse encode again walked the whole feature collection
        # three times, which for a large upload is the most expensive step in
        # the request.
        try:
            body = json.dumps(result, default=json_encoder, allow_nan=False)
        except (ValueError, TypeError) as e:
            # If serialization fails, remove features and retry
            print(f"Warning: JSON serialization issue, removing features: {e}")
            result.pop("infrastructure_features", None)
            body = json.dumps(result, default=json_encoder, allow_nan=False)

        return Response(content=body, media_type="application/json")

    except HTTPException:
        raise
    except HazardReadError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        print(f"Analysis failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error during analysis: {type(e).__name__}"
        )


@router.get("/analyze/{file_id}/status")
async def get_analysis_status(file_id: str):
    """Get status of analysis for a file"""
    if file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="File not found")
    
    return JSONResponse(content={
        "file_id": file_id,
        "status": "ready",
        "geometry_type": uploaded_files[file_id]["geometry_type"],
        "feature_count": uploaded_files[file_id]["feature_count"]
    })

