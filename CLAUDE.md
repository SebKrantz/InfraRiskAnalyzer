# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Infrastructure Risk Analyzer — a full-stack web app for analyzing infrastructure assets (points/lines) against geospatial hazard raster layers (floods, earthquakes, cyclones, landslides, drought). Users upload spatial datasets, select hazard layers, set intensity thresholds, and view exposure results on an interactive map with bar charts.

## Development Commands

### Backend (FastAPI/Python)

```bash
# Activate venv and run with auto-reload (from repo root)
source venv/bin/activate
cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Or simply:
cd backend && python main.py
```

### Frontend (React/TypeScript/Vite)

```bash
cd frontend && npm run dev      # Dev server on port 5173 (proxies /api to :8000)
cd frontend && npm run build    # Production build (tsc && vite build)
cd frontend && npm run lint     # ESLint
```

### Running Both

Start backend first (port 8000), then frontend (port 5173). Vite proxies `/api/*` requests to the backend.

## Architecture

### Monorepo Layout

- `backend/` — FastAPI Python API server
- `frontend/` — React + TypeScript + Vite SPA
- `data/` — Hazard layer config (`hazard_layers.csv`, semicolon-delimited), and local rasters (`rasters/`)

### Backend (`backend/`)

- **Entry:** `main.py` → FastAPI app with CORS, mounts all API routers
- **Config:** `app/config.py` — paths (BASE_DIR, UPLOAD_DIR, DATA_DIR), env vars (HOST, PORT, DEBUG, MAX_UPLOAD_SIZE)
- **API routers** in `app/api/`:
  - `upload.py` — `POST /api/upload` file parsing (zip/gpkg/csv), transforms to WGS84, stores GeoDataFrame in-memory
  - `hazards.py` — `GET /api/hazards` reads `data/hazard_layers.csv`, raster stats via rasterio
  - `analyze.py` — `POST /api/analyze` spatial intersection (raster sampling at infrastructure locations), caches raster values per `(file_id, hazard_id)` to avoid re-sampling on threshold changes
  - `tiles.py` — `GET /api/tiles/{hazard_id}/{z}/{x}/{y}.png` custom COG tile renderer with matplotlib colormaps and TTL cache
  - `export.py` — `POST /api/export/barchart`, `POST /api/export/map` high-res PNG exports
- **Geospatial utils:** `app/utils/geospatial.py` — load spatial formats, raster-infrastructure intersection, vulnerability curves, line length via Geod

**Critical constraint:** All uploaded data and analysis results live in-memory (`uploaded_files` dict + caches). Backend must run with `--workers 1`. Multi-worker needs Redis or similar.

**Analysis logic:** An asset is affected when the sampled hazard intensity is **`>= intensity_threshold`**, or **`> 0`** when no threshold is passed (`geospatial.py:692-695` for points, `:856-859` for lines). No-data (NaN) always counts as unaffected. There is **no hazard-specific branch** anywhere in the analysis path — `analyze_intersection` never receives `hazard_id`, so PGA, flood depth and wind speed are all compared the same way. (An earlier version of this file claimed PGA used inverted logic; that was never true of the code.)

**Vulnerability logic:** Curves are 2-column (`intensity, proportion_destroyed`) or 4+-column (`intensity, lower, central, upper`, index 2 being central). `replacement_value` is **per feature** for point datasets and **per metre** for line datasets. Damage is computed **only over affected features** — those at or above `intensity_threshold`, or above 0 when none is set. A curve typically returns a positive damage ratio well below any sensible threshold, so summing it over every feature made the total damage cost independent of the threshold while the reported damage ratio (already averaged over affected features only) moved with it; raising the threshold now lowers the cost and raises the mean ratio, which is the coherent pair. The threshold slider therefore stays visible in vulnerability mode.

### Frontend (`frontend/src/`)

- **Entry:** `main.tsx` → `App.tsx` (all top-level state via useState/useEffect)
- **Components:**
  - `Sidebar.tsx` — main control panel: file upload, hazard selector, color palette, opacity/threshold sliders, analysis results, vulnerability toggle, export buttons
  - `MapView.tsx` — MapLibre GL + deck.gl rendering, basemap selector (11 options), hazard tile overlay, infrastructure GeoJSON layer
  - `BarChart.tsx` — Recharts visualization of affected/unaffected counts or meters
  - `ui/` — shadcn-style reusable components (button, input, select, slider, card, dialog)
- **API client:** `services/api.ts` — typed fetch wrappers for all backend endpoints
- **Types:** `types/index.ts` — `Hazard`, `UploadedFile`, `AnalysisResult`, `ColorPalette`, `Basemap`
- **Path alias:** `@/*` → `./src/*` (configured in tsconfig.json)

### Data Flow

1. **Upload:** User file → `POST /api/upload` → validate/parse/transform to WGS84 → store GeoDataFrame in memory → return GeoJSON + metadata
2. **Hazard tiles:** Frontend requests `/api/tiles/{id}/{z}/{x}/{y}.png?palette=turbo` → backend reads COG, applies colormap, returns cached PNG
3. **Analysis:** User sets hazard + threshold → `POST /api/analyze` → sample raster at infrastructure locations (cached) → classify affected/unaffected → return summary + GeoJSON
4. **Export:** `POST /api/export/*` → matplotlib renders high-res PNG → download

### Hazard Layer Configuration

`data/hazard_layers.csv` uses semicolon (`;`) delimiter. Required columns: `hazard`, `dataset_url`. Optional: `description`, `background_paper`, `unit`, `category`. URLs point to Cloud Optimized GeoTIFFs (remote or local in `data/rasters/`). A few landslide rows have misaligned `unit`/`category` fields — code reading them must tolerate junk.

## Key Technical Details

- **Geospatial stack:** geopandas, rasterio, xarray/rioxarray, pyogrio for I/O
- **Tile caching:** TTL 5min, max 2000 tiles, thread-local rasterio connections
- **Infrastructure types:** Point and LineString geometries only. Lines use Geod for accurate length calculations (meters).
- **Vulnerability analysis:** Optional CSV upload (intensity, proportion_destroyed) + replacement value → damage cost calculation
- **Frontend styling:** TailwindCSS 3.4 + class-variance-authority + tailwind-merge
- **TypeScript:** Strict mode, ES2020 target
