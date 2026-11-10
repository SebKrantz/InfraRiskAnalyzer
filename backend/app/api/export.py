"""
Export endpoints for generating high-resolution images
"""
import io
import re
import asyncio
import textwrap
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns
import geopandas as gpd
import numpy as np
from urllib.parse import quote

from app.config import settings
from app.api.upload import uploaded_files
from app.api.hazards import load_hazards_dict
from app.api.analyze import get_cached_raster_values, get_cached_analysis_result
from app.utils.geospatial import analyze_intersection, mask_raster_nodata
from app.utils.export_data import (
    lines_aggregate_to_csv_bytes,
    lines_split_to_gpkg_bytes,
    points_analysis_to_csv_bytes,
)

router = APIRouter()

# Thread pool for blocking image generation
_export_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="export_worker")

def _register_bundled_fonts() -> None:
    """
    Explicitly register matplotlib's bundled DejaVu Sans fonts.

    On some deployment hosts (e.g. Posit Connect) matplotlib's font cache can be
    stale or incomplete, so resolving the default ``sans-serif`` family fails with
    "Failed to find font DejaVu Sans ... and fallback to the default font was
    disabled" when rendering any text. matplotlib always ships these TTF files
    inside its own package, so adding them to the in-memory font manager makes
    font resolution independent of the host's (possibly corrupt) cache.
    """
    import os
    from matplotlib import font_manager

    ttf_dir = os.path.join(matplotlib.get_data_path(), 'fonts', 'ttf')
    for ttf in ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf'):
        path = os.path.join(ttf_dir, ttf)
        if os.path.exists(path):
            try:
                font_manager.fontManager.addfont(path)
            except Exception:
                pass


_register_bundled_fonts()

# Set style for plots
sns.set_style("whitegrid")
plt.rcParams['figure.dpi'] = 100
plt.rcParams['font.family'] = 'DejaVu Sans'


class ExportBarchartRequest(BaseModel):
    """Request model for barchart export"""
    file_id: str
    hazard_id: str
    intensity_threshold: Optional[float] = None


class ExportMapRequest(BaseModel):
    """Request model for map export"""
    file_id: str
    hazard_id: str
    color_palette: str = 'turbo'
    hazard_opacity: float = 0.6  # Not used: export always uses full opacity
    intensity_threshold: Optional[float] = None
    basemap: str = 'positron'


class ExportDataRequest(BaseModel):
    """Request model for tabular / vector data export"""
    file_id: str
    hazard_id: str
    intensity_threshold: Optional[float] = None
    mode: str  # csv_points | csv_lines_aggregate | gpkg_lines_split


def _run_data_export(
    file_id: str,
    hazard_id: str,
    intensity_threshold: Optional[float],
    mode: str,
) -> tuple[bytes, str, str]:
    """Returns (body, filename, media_type). Raises ValueError for bad input."""
    if file_id not in uploaded_files:
        raise ValueError("UPLOAD_NOT_FOUND")

    file_info = uploaded_files[file_id]
    geometry_type = file_info["geometry_type"]

    if mode == "csv_points" and geometry_type != "Point":
        raise ValueError("MODE_GEOMETRY_MISMATCH")
    if mode in ("csv_lines_aggregate", "gpkg_lines_split") and geometry_type != "LineString":
        raise ValueError("MODE_GEOMETRY_MISMATCH")

    # The result at this exact threshold: falling back to the one without a
    # threshold exported other numbers than the screen showed
    cached_analysis = get_cached_analysis_result(file_id, hazard_id, intensity_threshold)
    if cached_analysis is None:
        raise ValueError("NO_ANALYSIS_CACHE")

    short_f = file_id[:8]
    short_h = hazard_id[:8]

    if mode == "csv_points":
        full_gdf = cached_analysis.get("full_gdf")
        if full_gdf is None:
            full_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        body = points_analysis_to_csv_bytes(full_gdf)
        return body, f"data_{short_f}_{short_h}.csv", "text/csv; charset=utf-8"

    if mode == "csv_lines_aggregate":
        full_gdf = cached_analysis.get("full_gdf")
        if full_gdf is None:
            full_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        body = lines_aggregate_to_csv_bytes(full_gdf)
        return body, f"data_lines_{short_f}_{short_h}.csv", "text/csv; charset=utf-8"

    if mode == "gpkg_lines_split":
        line_data = cached_analysis.get("line_data")
        if line_data is None:
            line_data = []
        repl = cached_analysis.get("vulnerability_replacement_value")
        interp, lower, upper = cached_analysis.get("vulnerability_interps", (None, None, None))
        body = lines_split_to_gpkg_bytes(
            line_data, intensity_threshold, interp, repl, lower, upper
        )
        return body, f"lines_split_{short_f}_{short_h}.gpkg", "application/geopackage+sqlite3"

    raise ValueError("INVALID_MODE")


BASEMAP_TILE_URLS: dict[str, str] = {
    'positron': 'https://basemaps.cartocdn.com/rastertiles/light_all/{z}/{x}/{y}.png',
    'dark-matter': 'https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}.png',
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'topo': 'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
    'esri-street': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    'esri-topo': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    'esri-terrain': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}',
    'esri-ocean': 'https://services.arcgisonline.com/arcgis/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
    'esri-imagery': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
}


def basemap_tile_url(basemap: str) -> str:
    """
    Tile URL for a basemap name, with the CARTO API key appended.

    CARTO's raster basemaps now require ``?key=<...>``; the key comes from the
    CARTO_API_KEY environment variable. When it is unset the URL is
    returned unkeyed, and non-CARTO providers are never touched.
    """
    url = BASEMAP_TILE_URLS.get(basemap, BASEMAP_TILE_URLS['positron'])
    if 'basemaps.cartocdn.com' in url and settings.CARTO_API_KEY:
        sep = '&' if '?' in url else '?'
        url = f"{url}{sep}key={quote(settings.CARTO_API_KEY, safe='')}"
    return url


def _hazard_intensity_cbar_label(unit: Optional[str]) -> str:
    """Legend text for hazard colorbar; strip one pair of outer parentheses from unit (e.g. (index))."""
    if not unit or not str(unit).strip():
        return "Hazard Intensity"
    u = str(unit).strip()
    if u.startswith("(") and u.endswith(")") and len(u) > 2:
        u = u[1:-1].strip()
    return f"Hazard Intensity ({u})" if u else "Hazard Intensity"


def _threshold_labels(
    intensity_threshold: Optional[float],
    hazard_unit: Optional[str] = None,
) -> tuple[str, str]:
    """Return (affected_label, unaffected_label); append hazard unit as-is when present."""
    suffix = f" {hazard_unit}" if hazard_unit and str(hazard_unit).strip() else ""
    if intensity_threshold is not None:
        t = f'{intensity_threshold:g}'
        return f'Affected (\u2265 {t}){suffix}', f'Unaffected (< {t}){suffix}'
    return f'Affected (> 0){suffix}', f'Unaffected (= 0){suffix}'


def tex_safe(text) -> str:
    """Escape `$` so matplotlib prints it: two of them in one string switch on
    mathtext and garble everything between ("$15,166,246 - $45,498,737")."""
    return re.sub(r'(?<!\\)\$', r'\\$', str(text))


def _musd(value: float, like: Optional[float] = None) -> str:
    """US$ millions with thousands separators and just enough decimals to read
    (those of `like`, when given, so a range reads at one precision)."""
    m = value / 1e6
    ref = abs((like if like else value) / 1e6)
    if m == 0:
        return '0'
    decimals = 0 if ref >= 100 else 1 if ref >= 10 else 2 if ref >= 0.1 else 3
    return f'{m:,.{decimals}f}'


def _damage_label(damage: float, lower: Optional[float] = None, upper: Optional[float] = None) -> str:
    """'US$ 15.2 m', and the uncertainty range beneath it when there is one."""
    label = f'US$ {_musd(damage)} m'
    if lower is not None and upper is not None:
        label += f'\n[US$ {_musd(lower, damage)} - {_musd(upper, damage)} m]'
    return tex_safe(label)


def generate_barchart_png(
    analysis_result: dict,
    geometry_type: str,
    title: str,
    full_gdf: Optional[gpd.GeoDataFrame] = None,
    intensity_threshold: Optional[float] = None,
    hazard_unit: Optional[str] = None,
) -> bytes:
    """
    Generate PNG barchart from analysis results.
    
    Parameters:
    -----------
    analysis_result : dict
        Analysis result with summary statistics
    geometry_type : str
        'Point' or 'LineString'
    title : str
        Chart title
    full_gdf : GeoDataFrame, optional
        Full GeoDataFrame with vulnerability and damage_cost properties (for vulnerability mode)
    intensity_threshold : float, optional
        Hazard intensity threshold used for the analysis
    hazard_unit : str, optional
        Unit string from hazard metadata for category labels (exposure mode)
    
    Returns:
    --------
    bytes : PNG image bytes
    """
    import pandas as pd
    
    # Check if vulnerability analysis data is available
    total_damage_cost = analysis_result.get('total_damage_cost')
    
    if total_damage_cost is not None:
        # Vulnerability analysis mode - show damage cost (left axis) and exposure/vulnerability (right axis)
        damage_cost = max(0, total_damage_cost)

        # Uncertainty bounds (None when not provided)
        damage_lower = analysis_result.get('total_damage_cost_lower')
        damage_upper = analysis_result.get('total_damage_cost_upper')
        has_bounds = damage_lower is not None and damage_upper is not None
        if has_bounds:
            damage_lower = max(0, damage_lower)
            damage_upper = max(0, damage_upper)

        # Calculate exposure percentage
        exposure_pct = 0.0
        if geometry_type == 'Point':
            affected_count = analysis_result.get('affected_count', 0)
            total_features = analysis_result.get('total_features', 0)
            if total_features > 0:
                exposure_pct = (affected_count / total_features) * 100
        else:
            affected_meters = analysis_result.get('affected_meters', 0.0)
            unaffected_meters = analysis_result.get('unaffected_meters', 0.0)
            total_meters = affected_meters + unaffected_meters
            if total_meters > 0:
                exposure_pct = (affected_meters / total_meters) * 100

        # Calculate average vulnerability of exposed assets (as percentage)
        vulnerability_pct = 0.0
        if full_gdf is not None and 'vulnerability' in full_gdf.columns:
            if geometry_type == 'Point':
                # For points: simple average of vulnerability for affected points
                exposed = full_gdf[full_gdf['affected'] & full_gdf['vulnerability'].notna()]
                if len(exposed) > 0:
                    vulnerability_pct = exposed['vulnerability'].mean() * 100
            else:
                # For lines: length-weighted average vulnerability of exposed segments
                exposed = full_gdf[full_gdf['affected'] & full_gdf['vulnerability'].notna()]
                if len(exposed) > 0 and 'length_m' in exposed.columns:
                    # Calculate weighted average
                    exposed = exposed[exposed['length_m'] > 0]
                    if len(exposed) > 0:
                        weighted_sum = (exposed['vulnerability'] * exposed['length_m']).sum()
                        length_sum = exposed['length_m'].sum()
                        if length_sum > 0:
                            vulnerability_pct = (weighted_sum / length_sum) * 100

        # Two panels rather than a twin axis: money on the left, the two shares
        # on the right, each on a scale of its own
        fig, (ax1, ax2) = plt.subplots(
            1, 2, figsize=(7, 5), gridspec_kw={'width_ratios': [1.2, 2.2]}
        )
        ax1.grid(False)
        ax2.grid(False)
        width = 0.6

        # Left panel: damage cost in US$ million
        cost_m = damage_cost / 1e6
        ax1.bar(0, cost_m, width, color='#f59e0b', alpha=0.8, zorder=3)
        ax1.set_ylabel(tex_safe('Damage cost (US$ million)'), fontsize=12, color='#4b5563')
        ax1.tick_params(axis='y', labelcolor='#4b5563')
        ax1.set_xticks([0])
        ax1.set_xticklabels(['Damage'], fontsize=11, color='#4b5563')
        ax1.set_xlim(-0.6, 0.6)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:,.10g}'))

        # Y-axis upper limit — extend to accommodate error bar whisker when bounds present
        y_top_ref = (damage_upper if has_bounds else damage_cost) / 1e6
        y_top = max(y_top_ref, 0.1) * 1.25
        ax1.set_ylim(bottom=0, top=y_top)

        # Error bars for damage cost uncertainty bounds
        if has_bounds:
            ax1.errorbar(
                0, cost_m,
                yerr=[[cost_m - damage_lower / 1e6], [damage_upper / 1e6 - cost_m]],
                fmt='none', color='#374151', capsize=10, capthick=2,
                linewidth=2, zorder=4,
            )
        ax1.text(
            0, y_top_ref + y_top * 0.02,
            _damage_label(damage_cost, damage_lower if has_bounds else None,
                          damage_upper if has_bounds else None),
            ha='center', va='bottom', fontsize=9, color='#f59e0b',
        )

        # Right panel: exposure and damage ratio, in percent
        ax2.bar(0, exposure_pct, width, color='#3b82f6', alpha=0.8)
        ax2.bar(1, vulnerability_pct, width, color='#10b981', alpha=0.8)
        ax2.set_ylabel('Percentage (%)', fontsize=12, color='#4b5563')
        ax2.tick_params(axis='y', labelcolor='#4b5563')
        ax2.set_xticks([0, 1])
        ax2.set_xticklabels(['Exposure', 'Damage ratio'], fontsize=11, color='#4b5563')
        ax2.set_xlim(-0.6, 1.6)
        ax2.set_ylim(0, 100)
        ax2.text(0, exposure_pct + 1, f'{exposure_pct:.1f}%',
                 ha='center', va='bottom', fontsize=10, color='#3b82f6')
        ax2.text(1, vulnerability_pct + 1, f'{vulnerability_pct:.1f}%',
                 ha='center', va='bottom', fontsize=10, color='#10b981')

        fig.suptitle(tex_safe(textwrap.fill(title, 56)), fontsize=14, fontweight='bold')
        fig.tight_layout()

        # Save to bytes buffer
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='PNG', dpi=300, bbox_inches='tight')
        plt.close(fig)

        return img_buffer.getvalue()
    else:
        # Standard exposure analysis mode
        if geometry_type == 'Point':
            affected = analysis_result.get('affected_count', 0)
            unaffected = analysis_result.get('unaffected_count', 0)
            ylabel = 'Count'
        else:
            # km, as the on-screen chart: one unit for a length across the figures
            affected = analysis_result.get('affected_meters', 0) / 1000
            unaffected = analysis_result.get('unaffected_meters', 0) / 1000
            ylabel = 'Length (km)'
        
        affected_label, unaffected_label = _threshold_labels(
            intensity_threshold, hazard_unit
        )
        plot_data = pd.DataFrame({
            'Category': [affected_label, unaffected_label],
            'Value': [affected, unaffected]
        })
        
        palette = ['#d62728', '#2ca02c']  # Red for affected, green for unaffected
        
        # Create plot
        fig, ax = plt.subplots(figsize=(6, 5))
        sns.barplot(data=plot_data, x='Category', y='Value', 
                    palette=palette, ax=ax)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_xlabel('', fontsize=12)
        ax.set_title(tex_safe(title), fontsize=14, fontweight='bold')
        
        # Add value labels on bars
        max_value = max(plot_data['Value'])
        
        # Set y-axis limits to add margin above bars
        ax.set_ylim(bottom=0, top=max_value * 1.15)
        
        for i, v in enumerate(plot_data['Value']):
            label = f'{v:,.0f}' if geometry_type == 'Point' else f'{v:,.1f}' if v >= 10 else f'{v:,.2f}'
            ax.text(i, v + max_value * 0.01, 
                    label, ha='center', va='bottom', fontsize=11)
        
        plt.tight_layout()
        
        # Save to bytes buffer
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format='PNG', dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        return img_buffer.getvalue()


def generate_map_png(
    infrastructure_gdf: gpd.GeoDataFrame,
    hazard_raster_path: str,
    geometry_type: str,
    title: str,
    color_palette: str = 'turbo',
    hazard_opacity: float = 0.6,
    is_vulnerability_mode: bool = False,
    intensity_threshold: Optional[float] = None,
    basemap: str = 'positron',
    hazard_unit: Optional[str] = None,
) -> bytes:
    """
    Generate PNG map from infrastructure and hazard data.
    
    Parameters:
    -----------
    infrastructure_gdf : GeoDataFrame
        Infrastructure with 'affected' column (in WGS84)
    hazard_raster_path : str
        Path/URL to hazard raster
    geometry_type : str
        'Point' or 'LineString'
    title : str
        Map title
    color_palette : str
        Color palette name for hazard visualization
    hazard_opacity : float
        Opacity for hazard layer (0-1)
    intensity_threshold : float, optional
        Hazard intensity threshold used for the analysis
    basemap : str
        Basemap tile provider name
    hazard_unit : str, optional
        Unit string from hazard metadata for colorbar and legend labels
    
    Returns:
    --------
    bytes : PNG image bytes
    """
    if infrastructure_gdf is None or len(infrastructure_gdf) == 0:
        raise ValueError("No infrastructure data to plot")
    
    # Ensure infrastructure is in WGS84 first
    if infrastructure_gdf.crs != 'EPSG:4326':
        infrastructure_gdf = infrastructure_gdf.to_crs('EPSG:4326')
    
    # Get bounding box from infrastructure in WGS84
    bounds_wgs84 = infrastructure_gdf.total_bounds
    margin = (bounds_wgs84[2] - bounds_wgs84[0]) * 0.1
    bbox_wgs84 = [bounds_wgs84[0] - margin, bounds_wgs84[1] - margin, 
                   bounds_wgs84[2] + margin, bounds_wgs84[3] + margin]
    
    # Convert to Web Mercator for basemap and plotting
    from pyproj import Transformer
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:3857', always_xy=True)
    bbox_mercator = [
        transformer.transform(bbox_wgs84[0], bbox_wgs84[1])[0],  # minx
        transformer.transform(bbox_wgs84[0], bbox_wgs84[1])[1],  # miny
        transformer.transform(bbox_wgs84[2], bbox_wgs84[3])[0],  # maxx
        transformer.transform(bbox_wgs84[2], bbox_wgs84[3])[1]   # maxy
    ]
    
    # Convert infrastructure to Web Mercator for plotting
    infrastructure_mercator = infrastructure_gdf.to_crs('EPSG:3857')
    
    # Create figure
    fig, ax = plt.subplots(figsize=(12, 10))
    
    # Set extent first so basemap knows the bounds
    ax.set_xlim(bbox_mercator[0], bbox_mercator[2])
    ax.set_ylim(bbox_mercator[1], bbox_mercator[3])
    ax.set_aspect('equal')
    
    # Deferred colorbars for vulnerability mode (drawn after tight_layout for stable positioning)
    hazard_sm = None
    vuln_sm = None
    hazard_cbar_label = _hazard_intensity_cbar_label(hazard_unit)
    
    # Add basemap (should be at zorder 0, behind everything)
    try:
        import contextily as ctx
        tile_url = basemap_tile_url(basemap)
        ctx.add_basemap(
            ax,
            crs='EPSG:3857',
            source=tile_url,
            zoom='auto',
            zorder=0
        )
    except ImportError:
        print("Warning: contextily not available, skipping basemap")
    except Exception as e:
        import traceback
        print(f"Warning: Could not add basemap: {e}")
        traceback.print_exc()
    
    # Load and clip hazard raster using rasterio (much faster than xarray)
    try:
        import rasterio
        from rasterio.windows import from_bounds, bounds as window_bounds
        
        with rasterio.open(hazard_raster_path) as src:
            # Create window from bounding box (use WGS84 bbox for raster reading)
            window = from_bounds(bbox_wgs84[0], bbox_wgs84[1], bbox_wgs84[2], bbox_wgs84[3], src.transform)
            
            # Downsample to reasonable resolution for visualization (max 2000x2000)
            # Calculate target resolution based on window size
            window_height = window.height
            window_width = window.width
            
            # Target max dimension
            max_dim = 2000
            if window_height > max_dim or window_width > max_dim:
                scale = max(window_height, window_width) / max_dim
                out_shape = (int(window_height / scale), int(window_width / scale))
            else:
                out_shape = (int(window_height), int(window_width))
            
            # Read only the windowed, downsampled data
            hazard_data = src.read(1, window=window, out_shape=out_shape, boundless=True, fill_value=np.nan)
            hazard_data = mask_raster_nodata(hazard_data, src.nodata)
            
            # Get transform for the downsampled window
            window_transform = src.window_transform(window)
            
            # Calculate extent from window bounds (more reliable than transform calculation)
            # Get the actual bounds of the window (in raster CRS, likely WGS84)
            win_bounds = window_bounds(window, src.transform)
            # Convert extent to Web Mercator for plotting
            extent_wgs84 = [
                win_bounds[0],  # minx
                win_bounds[2],  # maxx
                win_bounds[1],  # miny
                win_bounds[3]   # maxy
            ]
            # Convert to Web Mercator
            extent = [
                transformer.transform(extent_wgs84[0], extent_wgs84[2])[0],  # minx
                transformer.transform(extent_wgs84[1], extent_wgs84[3])[0],  # maxx
                transformer.transform(extent_wgs84[0], extent_wgs84[2])[1],  # miny
                transformer.transform(extent_wgs84[1], extent_wgs84[3])[1]   # maxy
            ]
            
            # Normalize data
            valid_mask = ~np.isnan(hazard_data) & (hazard_data >= 0.0) & (hazard_data < 1e15)
            if np.any(valid_mask):
                vmin = np.nanmin(hazard_data[valid_mask])
                vmax = np.nanmax(hazard_data[valid_mask])
                
                if vmax > vmin:
                    # Use sqrt normalization like tile service
                    normalized = (np.sqrt(hazard_data) - np.sqrt(vmin)) / (np.sqrt(vmax) - np.sqrt(vmin))
                    normalized = np.clip(normalized, 0, 1)
                else:
                    normalized = np.zeros_like(hazard_data, dtype=np.float32)
                normalized[~valid_mask] = np.nan
                
                # Create single-color (blue-ish) overlay with variable transparency
                # Dark blue color: #1e40af (RGB: 30, 64, 175) - darker blue, clearly different from red and green
                blue_color = np.array([30, 64, 175]) / 255.0  # Normalize to 0-1
                
                # Create RGBA image: blue color with alpha based on normalized hazard
                # Alpha goes from 0 (transparent) at hazard=0 to full opacity at hazard=max
                h, w = normalized.shape
                colored_rgba = np.zeros((h, w, 4), dtype=np.float32)
                colored_rgba[:, :, 0] = blue_color[0]  # Red channel
                colored_rgba[:, :, 1] = blue_color[1]  # Green channel
                colored_rgba[:, :, 2] = blue_color[2]  # Blue channel
                colored_rgba[:, :, 3] = normalized * hazard_opacity  # Alpha channel (0 to hazard_opacity)
                
                # Convert to uint8
                colored_uint8 = (colored_rgba * 255).astype(np.uint8)
                
                # Plot hazard raster with RGBA (includes alpha channel)
                im = ax.imshow(colored_uint8, extent=extent, 
                              interpolation='bilinear', 
                              aspect='auto', origin='upper', zorder=1)
                
                # Create a ScalarMappable for the colorbar. FuncNorm carries a true
                # inverse, so the bar's ticks stay inside [vmin, vmax] on the sqrt
                # scale the raster is drawn with (a hand-rolled Normalize without
                # one drew ticks past both ends, which overprinted the end labels).
                from matplotlib.colors import FuncNorm, Normalize
                from matplotlib.cm import ScalarMappable
                
                # Create ScalarMappable for colorbar with blue colormap
                from matplotlib.colors import LinearSegmentedColormap
                # Create a blue colormap (light to dark blue) for the colorbar
                blue_colors = ['#dbeafe', '#1e40af']  # Light blue to dark blue
                blue_cmap = LinearSegmentedColormap.from_list('blue_hazard', blue_colors, N=256)
                
                norm = (FuncNorm((np.sqrt, np.square), vmin=vmin, vmax=vmax) if vmax > vmin
                        else Normalize(vmin=vmin, vmax=vmax))  # a constant window has no scale
                sm = ScalarMappable(norm=norm, cmap=blue_cmap)
                sm.set_array([])
                
                # Add colorbar - vulnerability mode defers to after tight_layout
                if is_vulnerability_mode:
                    hazard_sm = sm
                else:
                    cbar = plt.colorbar(sm, ax=ax, orientation='vertical', 
                                       pad=0.02, shrink=0.6, aspect=20)
                    cbar.set_label(hazard_cbar_label, rotation=270, labelpad=15, fontsize=10)
                    cbar.ax.tick_params(labelsize=9)
    except Exception as e:
        print(f"Warning: Could not load hazard raster: {e}")
        # Continue without hazard layer
    
    # Plot infrastructure (use Web Mercator version)
    if is_vulnerability_mode and 'vulnerability' in infrastructure_mercator.columns:
        # Vulnerability mode: color by vulnerability percentage (green to red gradient)
        # Unaffected segments have vulnerability = 0 (green)
        all_gdf = infrastructure_mercator.copy()
        
        # Set vulnerability to 0 for unaffected segments
        all_gdf.loc[~all_gdf['affected'], 'vulnerability'] = 0.0
        
        # Filter to only features with valid vulnerability values
        all_gdf = all_gdf[all_gdf['vulnerability'].notna()]
        
        # Calculate max vulnerability for scaling (in percentage)
        if len(all_gdf) > 0:
            max_vulnerability_pct = all_gdf['vulnerability'].max() * 100
        else:
            max_vulnerability_pct = 100  # Default to 100% if no features
        
        # Create green to red colormap
        from matplotlib.colors import LinearSegmentedColormap
        colors = ['#10b981', '#f59e0b', '#ef4444']  # Green, orange, red
        n_bins = 100
        cmap_vuln = LinearSegmentedColormap.from_list('vulnerability', colors, N=n_bins)
        
        # Create normalized vulnerability column for coloring
        all_gdf['vuln_norm'] = (all_gdf['vulnerability'] * 100 / max_vulnerability_pct).clip(0, 1) if max_vulnerability_pct > 0 else 0
        
        if geometry_type == 'Point':
            # Plot all points colored by vulnerability (unaffected = 0 = green)
            if len(all_gdf) > 0:
                all_gdf.plot(ax=ax, column='vuln_norm', cmap=cmap_vuln, 
                            markersize=50, marker='o', alpha=0.8,
                            edgecolor='black', linewidth=0.5, zorder=3,
                            vmin=0, vmax=1, legend=False)
        else:
            # Plot all lines colored by vulnerability (unaffected = 0 = green)
            if len(all_gdf) > 0:
                all_gdf.plot(ax=ax, column='vuln_norm', cmap=cmap_vuln,
                            linewidth=2, alpha=0.8, zorder=3,
                            vmin=0, vmax=1, legend=False)
        
        # Build ScalarMappable for deferred vulnerability colorbar
        from matplotlib.colors import Normalize
        from matplotlib.cm import ScalarMappable
        norm_vuln = Normalize(vmin=0, vmax=max_vulnerability_pct)
        vuln_sm = ScalarMappable(norm=norm_vuln, cmap=cmap_vuln)
        vuln_sm.set_array([])
        
    elif 'affected' in infrastructure_mercator.columns:
        # Standard exposure mode: red for affected, green for unaffected
        affected_gdf = infrastructure_mercator[infrastructure_mercator['affected']]
        unaffected_gdf = infrastructure_mercator[~infrastructure_mercator['affected']]
        affected_label, unaffected_label = _threshold_labels(
            intensity_threshold, hazard_unit
        )
        
        if geometry_type == 'Point':
            if len(unaffected_gdf) > 0:
                unaffected_gdf.plot(ax=ax, color='#2ca02c', markersize=50, 
                                  marker='o', label=unaffected_label, alpha=0.8, 
                                  edgecolor='black', linewidth=0.5, zorder=3)
            if len(affected_gdf) > 0:
                affected_gdf.plot(ax=ax, color='#d62728', markersize=50, 
                                marker='o', label=affected_label, alpha=0.8, 
                                edgecolor='black', linewidth=0.5, zorder=3)
        else:
            if len(unaffected_gdf) > 0:
                unaffected_gdf.plot(ax=ax, color='#2ca02c', linewidth=2, 
                                  label=unaffected_label, alpha=0.8, zorder=3)
            if len(affected_gdf) > 0:
                affected_gdf.plot(ax=ax, color='#d62728', linewidth=2, 
                               label=affected_label, alpha=0.8, zorder=3)
    else:
        # No analysis - plot all in gray
        if geometry_type == 'Point':
            infrastructure_mercator.plot(ax=ax, color='#6b7280', markersize=50, 
                                  marker='o', alpha=0.8, 
                                  edgecolor='black', linewidth=0.5, zorder=3)
        else:
            infrastructure_mercator.plot(ax=ax, color='#6b7280', linewidth=2, 
                                  alpha=0.8, zorder=3)
    
    # Set extent in Web Mercator
    ax.set_xlim(bbox_mercator[0], bbox_mercator[2])
    ax.set_ylim(bbox_mercator[1], bbox_mercator[3])
    
    # Add legend if we have affected/unaffected (not in vulnerability mode)
    if 'affected' in infrastructure_mercator.columns and not is_vulnerability_mode:
        ax.legend(loc='upper right', framealpha=0.9, fontsize=10)
    
    # Add title
    ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
    
    ax.set_axis_off()
    plt.tight_layout()
    
    # Draw deferred vulnerability-mode colorbars using the post-tight_layout axes position
    if is_vulnerability_mode and vuln_sm is not None:
        pos = ax.get_position()
        cbar_width = 0.015
        cbar_x = pos.x1 + 0.01
        gap = 0.06
        cbar_height = (pos.height - gap) / 2
        if hazard_sm is not None:
            cax_h = fig.add_axes([cbar_x, pos.y0, cbar_width, cbar_height])
            cb_h = plt.colorbar(hazard_sm, cax=cax_h, orientation='vertical')
            cb_h.set_label(hazard_cbar_label, rotation=270, labelpad=15, fontsize=10)
            cb_h.ax.tick_params(labelsize=9)
        cax_v = fig.add_axes([cbar_x, pos.y0 + cbar_height + gap, cbar_width, cbar_height])
        cb_v = plt.colorbar(vuln_sm, cax=cax_v, orientation='vertical')
        cb_v.set_label('Damage Ratio (%)', rotation=270, labelpad=15, fontsize=10)
        cb_v.ax.tick_params(labelsize=9)
    
    # Save to bytes buffer
    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='PNG', dpi=300, bbox_inches='tight')
    plt.close(fig)
    
    return img_buffer.getvalue()


@router.post("/export/barchart")
async def export_barchart(request: ExportBarchartRequest):
    """
    Export analysis results as a high-resolution PNG barchart.
    """
    # Check if file exists
    if request.file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    
    file_info = uploaded_files[request.file_id]
    geometry_type = file_info["geometry_type"]
    infrastructure_gdf = file_info["gdf"]
    
    # Get hazard info
    hazards_dict = load_hazards_dict()
    if request.hazard_id not in hazards_dict:
        raise HTTPException(status_code=404, detail=f"Hazard layer '{request.hazard_id}' not found")
    
    hazard_data = hazards_dict[request.hazard_id]
    hazard_url = hazard_data.get("dataset_url", "")
    
    if not hazard_url:
        raise HTTPException(status_code=500, detail="Hazard layer missing dataset_url")
    
    try:
        # The export buttons appear once the analysis is done, so the result at this
        # exact threshold is cached; falling back to the one without a threshold
        # exported other numbers than the screen showed
        cached_analysis = get_cached_analysis_result(
            request.file_id,
            request.hazard_id,
            request.intensity_threshold
        )

        if cached_analysis is None:
            raise HTTPException(
                status_code=404,
                detail="No analysis results found. Please run analysis first."
            )
        
        # Use cached result - no recalculation needed!
        analysis_result = cached_analysis
        
        # Build summary for barchart (include vulnerability data if available)
        summary = {
            "affected_count": analysis_result.get("affected_count", 0),
            "unaffected_count": analysis_result.get("unaffected_count", 0),
            "affected_meters": analysis_result.get("affected_meters", 0.0),
            "unaffected_meters": analysis_result.get("unaffected_meters", 0.0),
            "total_damage_cost": analysis_result.get("total_damage_cost"),
            "total_damage_cost_lower": analysis_result.get("total_damage_cost_lower"),
            "total_damage_cost_upper": analysis_result.get("total_damage_cost_upper"),
            "total_features": file_info["feature_count"]
        }
        
        # Get full_gdf for vulnerability calculations if available
        full_gdf = None
        if "full_gdf" in analysis_result:
            full_gdf = analysis_result["full_gdf"]
        
        # Generate title
        hazard_name = hazard_data.get("hazard", request.hazard_id)
        hazard_unit = hazard_data.get("unit")
        if summary.get("total_damage_cost") is not None:
            title = f"{hazard_name} - Vulnerability Analysis"
        else:
            title = f"{hazard_name} - Infrastructure Exposure Analysis"
        
        # Generate PNG
        loop = asyncio.get_event_loop()
        png_bytes = await loop.run_in_executor(
            _export_executor,
            generate_barchart_png,
            summary,
            geometry_type,
            title,
            full_gdf,
            request.intensity_threshold,
            hazard_unit,
        )
        
        # Generate filename
        filename = f"barchart_{request.file_id[:8]}_{request.hazard_id[:8]}.png"
        
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating barchart: {str(e)}"
        )


@router.post("/export/map")
async def export_map(request: ExportMapRequest):
    """
    Export analysis results as a high-resolution PNG map.
    """
    # Check if file exists
    if request.file_id not in uploaded_files:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    
    file_info = uploaded_files[request.file_id]
    geometry_type = file_info["geometry_type"]
    infrastructure_gdf = file_info["gdf"]
    
    # Get hazard info
    hazards_dict = load_hazards_dict()
    if request.hazard_id not in hazards_dict:
        raise HTTPException(status_code=404, detail=f"Hazard layer '{request.hazard_id}' not found")
    
    hazard_data = hazards_dict[request.hazard_id]
    hazard_url = hazard_data.get("dataset_url", "")
    
    if not hazard_url:
        raise HTTPException(status_code=500, detail="Hazard layer missing dataset_url")
    
    try:
        # The export buttons appear once the analysis is done, so the result at this
        # exact threshold is cached; falling back to the one without a threshold
        # exported other numbers than the screen showed
        cached_analysis = get_cached_analysis_result(
            request.file_id,
            request.hazard_id,
            request.intensity_threshold
        )

        if cached_analysis is None:
            raise HTTPException(
                status_code=404,
                detail="No analysis results found. Please run analysis first."
            )
        
        # Use cached result - no recalculation needed!
        analysis_result = cached_analysis
        
        # Get infrastructure GeoDataFrame with affected status
        if "full_gdf" in analysis_result:
            display_gdf = analysis_result["full_gdf"].copy()
        else:
            display_gdf = infrastructure_gdf.copy()
            display_gdf['affected'] = False

        if "line_id" in display_gdf.columns:
            display_gdf = display_gdf.drop(columns=["line_id"])
        
        # Check if vulnerability analysis mode
        is_vulnerability_mode = analysis_result.get("total_damage_cost") is not None
        
        # Generate title
        hazard_name = hazard_data.get("hazard", request.hazard_id)
        hazard_unit = hazard_data.get("unit")
        if is_vulnerability_mode:
            title = f"{hazard_name} - Vulnerability Analysis Map"
        else:
            title = f"{hazard_name} - Infrastructure Exposure Map"
        
        # Generate PNG (always use full opacity for hazard layer in export)
        loop = asyncio.get_event_loop()
        png_bytes = await loop.run_in_executor(
            _export_executor,
            generate_map_png,
            display_gdf,
            hazard_url,
            geometry_type,
            title,
            request.color_palette,
            1.0,  # Full opacity for export
            is_vulnerability_mode,
            request.intensity_threshold,
            request.basemap,
            hazard_unit,
        )
        
        # Generate filename
        filename = f"map_{request.file_id[:8]}_{request.hazard_id[:8]}.png"
        
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )


    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating map: {str(e)}"
        )


@router.post("/export/data")
async def export_data(request: ExportDataRequest):
    """
    Export analysis results as CSV (points or line aggregate) or GPKG (line split segments).
    """
    hazards_dict = load_hazards_dict()
    if request.hazard_id not in hazards_dict:
        raise HTTPException(status_code=404, detail=f"Hazard layer '{request.hazard_id}' not found")

    loop = asyncio.get_event_loop()
    try:
        body, filename, media_type = await loop.run_in_executor(
            _export_executor,
            _run_data_export,
            request.file_id,
            request.hazard_id,
            request.intensity_threshold,
            request.mode,
        )
    except ValueError as e:
        code = str(e)
        if code == "UPLOAD_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Uploaded file not found")
        if code == "MODE_GEOMETRY_MISMATCH":
            raise HTTPException(
                status_code=400,
                detail="Export mode is not valid for this dataset geometry type.",
            )
        if code == "NO_ANALYSIS_CACHE":
            raise HTTPException(
                status_code=404,
                detail="No analysis results found. Please run analysis first.",
            )
        if code == "INVALID_MODE":
            raise HTTPException(
                status_code=400,
                detail="Invalid mode. Use csv_points, csv_lines_aggregate, or gpkg_lines_split.",
            )
        raise HTTPException(status_code=500, detail=str(e))

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

