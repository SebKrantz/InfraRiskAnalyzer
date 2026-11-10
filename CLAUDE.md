# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Infrastructure Risk Analyzer — a full-stack web app for analyzing infrastructure assets (points/lines) against geospatial hazard raster layers (floods, earthquakes, cyclones, landslides, drought). Users upload spatial datasets, select hazard layers, set intensity thresholds, and view exposure results on an interactive map with bar charts. Branches: `main` (this layout) and `posit` (the same app packaged for Posit Connect: FastAPI serves the built frontend from `backend/static`). Make changes on `main` and carry them to `posit`; on `posit`, rebuild `backend/static` with `npm run build:connect` after any frontend change.

## Development Commands

### Backend (FastAPI/Python)

```bash
cd backend && python -m venv venv && source venv/bin/activate && pip install -r requirements-dev.txt
cd backend && python main.py                          # or: uvicorn main:app --reload --port 8000
cd backend && python -m pytest                        # tests (also: python -m unittest discover -s tests)
```

### Frontend (React/TypeScript/Vite)

```bash
cd frontend && npm run dev      # Dev server on port 5173 (proxies /api to :8000; BACKEND_URL overrides)
cd frontend && npm run build    # Production build (tsc && vite build)
cd frontend && npm run lint     # ESLint (.eslintrc.cjs), no warnings allowed
cd frontend && npm test         # node --test tests/ (Node 22.18+, runs TypeScript directly)
```

Start the backend first (port 8000), then the frontend (port 5173).

## Architecture

### Monorepo Layout

- `backend/` — FastAPI Python API server
- `frontend/` — React + TypeScript + Vite SPA
- `data/` — Hazard layer catalogue (`hazard_layers.csv`, semicolon-delimited) and, optionally, local rasters (`rasters/`, gitignored; `scripts/download_hazard_rasters.py` fills it)
- `docs/` — Technical guide and user guide (Word sources with PDFs). The Word files carry manual edits: edit them in place, never regenerate them

### Backend (`backend/`)

- **Entry:** `main.py` → FastAPI app, mounts all API routers; CORS only for `CORS_ORIGINS`
- **Config:** `app/config.py` — paths (BASE_DIR = repo root, DATA_DIR, HAZARD_LAYERS_CSV) and env vars (HOST, PORT, DEBUG, CARTO_API_KEY, UPLOAD_TTL_HOURS, MAX_UPLOAD_MB, MAX_UPLOADS, CORS_ORIGINS), read from the environment or `backend/.env`
- **API routers** in `app/api/`:
  - `upload.py` — `POST /api/upload`: reads the body with a size limit, parses zip (shapefile, possibly in a folder) / gpkg / geojson / csv in a temp location, drops null geometries, turns polygons into centroids and splits MultiPoints, makes attributes JSON-safe (dates → ISO text), reprojects to WGS84 and stores the GeoDataFrame in memory. File problems are 400s with the temp paths scrubbed from the message. Uploads expire after UPLOAD_TTL_HOURS; past MAX_UPLOADS the least recently used is dropped
  - `hazards.py` — `GET /api/hazards` reads `data/hazard_layers.csv`; `/stats` gives a layer's min/max
  - `analyze.py` — `POST /api/analyze`: the raster is **always** the catalogue's `dataset_url` for `hazard_id` (a client-sent `hazard_url` is ignored). Raster values and full results are cached **per upload** (the last 4 layers and 4 results each), so one user's analyses never evict another's; the export endpoints read the result for the exact threshold
  - `tiles.py` — `GET /api/tiles/{hazard_id}/{z}/{x}/{y}.png` COG tile renderer with matplotlib colormaps and TTL cache
  - `export.py` — `POST /api/export/barchart`, `/map` (PNG) and `/data` (CSV, or line segments as GeoPackage via `utils/export_data.py`)
- **Geospatial utils:** `app/utils/geospatial.py` — loading uploads, raster sampling (`sample_raster_at_points`: pixel-edge tolerance `_EDGE_TOL`; a failed window read raises `HazardReadError` → 502, never NaN), the analysis (`analyze_intersection`), vulnerability curves, line sampling via Geod

**Critical constraint:** All uploaded data and analysis results live in-memory (`uploaded_files` dict + caches). Backend must run with `--workers 1`. Multi-worker needs Redis or similar.

**Analysis logic:** An asset is affected when the sampled hazard intensity is **`>= intensity_threshold`**, or **`> 0`** when no threshold is passed (in `analyze_intersection`, for points and for the sample points along lines). Lines are sampled at every vertex and at most 100 m apart; a span takes the status of its second sample point. No-data (NaN) counts as unaffected. There is **no hazard-specific branch** anywhere in the analysis path — `analyze_intersection` never receives `hazard_id`, so PGA, flood depth and wind speed are all compared the same way.

**Vulnerability logic:** Curves are 2-column (`intensity, proportion_destroyed`) or 4+-column (`intensity, lower, central, upper`, index 2 being central), sorted stably by intensity (so a step keeps its order). `replacement_value` is **per feature** for point datasets and **per metre** for line datasets. Damage is computed **only over affected features** — raising the threshold lowers the cost and raises the mean damage ratio. The split GeoPackage export applies the same rule per span, so its damage adds up to the totals on screen.

### Frontend (`frontend/src/`)

- **Entry:** `main.tsx` → `App.tsx` (all top-level state via useState/useEffect)
- **Analysis requests (`App.tsx`):** one effect runs the analysis when an input changes. It waits for the selected layer's statistics (`statsHazardId`), and each run supersedes earlier ones (`analysisRun` counter), so a slow older response never replaces a newer result. A threshold carries over to a layer in the same unit when it is within the new layer's range; otherwise it starts at the layer minimum
- **Components:**
  - `Sidebar.tsx` — controls (upload, hazard selector, palette, opacity, threshold box and slider, vulnerability inputs), results, export buttons
  - `MapView.tsx` — MapLibre GL map with nine basemaps (`setStyle(..., { diff: false })`, layers restored from the current props on `style.load`), hazard tile layer, infrastructure GeoJSON layers and pop-ups. Layer updates wait on the `styleReady` ref (not `isStyleLoaded()`, which is false while tiles load) and are cancelled when superseded; pop-up listeners are kept in `popupHandlers` so they can be removed
  - `BarChart.tsx` — Recharts chart of affected/unaffected counts or km, or damage, exposure and damage ratio
  - `ui/` — small shadcn-style components (button, input, select, slider, dialog)
- **API client:** `services/api.ts` — typed fetch wrappers for the backend endpoints
- **Pop-up HTML:** `lib/featurePopup.ts` escapes everything from the user's file (tested in `tests/`)
- **CARTO key:** `index.html` carries the placeholder `"__CARTO_API_KEY__"`; `vite dev` takes the key from `backend/.env`, a production build of `main` needs it written into `dist/index.html`, and the `posit` backend fills it in when serving the page
- **Path alias:** `@/*` → `./src/*` (configured in tsconfig.json)

### Data Flow

1. **Upload:** User file → `POST /api/upload` → validate/parse/transform to WGS84 → store GeoDataFrame in memory → return GeoJSON + metadata
2. **Hazard tiles:** Frontend requests `/api/tiles/{id}/{z}/{x}/{y}.png?palette=turbo` → backend reads COG, applies colormap, returns cached PNG
3. **Analysis:** User sets hazard + threshold → `POST /api/analyze` → sample raster at infrastructure locations (cached per upload) → classify affected/unaffected → return summary + GeoJSON
4. **Export:** `POST /api/export/*` → matplotlib renders high-res PNG, or CSV / GeoPackage → download

### Hazard Layer Configuration

`data/hazard_layers.csv` uses semicolon (`;`) delimiter. Required columns: `hazard`, `dataset_url`. Optional: `description`, `background_paper`, `unit`, `category`. URLs point to Cloud Optimized GeoTIFFs in WGS84 (remote, or local in `data/rasters/`). A few landslide rows have misaligned `unit`/`category` fields — code reading them must tolerate junk.

## Key Technical Details

- **Geospatial stack:** geopandas, pyogrio, rasterio, pyproj (Geod); matplotlib, seaborn and contextily for tiles and exports
- **Tile caching:** TTL 5min, max 2000 tiles, thread-local rasterio connections (one reconnect retry on a dropped connection)
- **Infrastructure types:** Point and LineString/MultiLineString (Polygons become centroid points, MultiPoints are split). Lines use Geod for accurate length calculations (meters).
- **Frontend styling:** TailwindCSS 3.4 + class-variance-authority + tailwind-merge
- **TypeScript:** Strict mode, ES2020 target
