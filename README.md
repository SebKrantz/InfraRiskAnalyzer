# Infrastructure Risk Analyzer

A web application for screening infrastructure assets against natural-hazard layers. Upload a
dataset of points (towers, substations, data centers) or lines (fibre, roads, pipelines), pick
a hazard layer, set an intensity threshold, and see which assets are exposed on an interactive
map, with a bar chart of the counts or lengths. With a vulnerability curve and a replacement
value the app also estimates the damage cost, with a low–high range when the curve carries
bounds.

The 29 hazard layers in the shipped catalogue are the GIRI global layers (floods, tropical
cyclone wind, landslides, earthquakes, drought, plus building exposure and population), read
as Cloud Optimized GeoTIFFs from the UNEP/GRID server.

## Features

- **Upload formats**: zipped Shapefile, GeoPackage, GeoJSON, or CSV with coordinate columns or a WKT column
- **Hazard layers**: 29 GIRI layers in `data/hazard_layers.csv`, each with its unit, description and source paper
- **Exposure analysis**: an asset is affected when the hazard intensity at it is at or above the threshold
  (above 0 with no threshold); lines are sampled at every vertex and at most 100 m apart and split into
  affected and unaffected stretches
- **Damage analysis**: a vulnerability curve (intensity → damage ratio, optionally with lower and upper
  bounds) times a replacement value per asset (points) or per metre (lines), over the affected assets
- **Map**: nine basemaps, hazard colour palettes and opacity, layer toggles, a pop-up with each asset's values
- **Exports**: map and bar chart as high-resolution PNG, results as CSV, and line results split into
  segments as GeoPackage

## Tech stack

- **Backend**: FastAPI and Uvicorn; geopandas and pyogrio (vector I/O), rasterio (raster I/O),
  pandas and numpy; matplotlib, seaborn and contextily (tiles and exports)
- **Frontend**: React 18 and TypeScript, built with Vite; MapLibre GL (map), Recharts (chart),
  TailwindCSS

## Quick start

### Prerequisites

- **Backend**: Python 3.10 or newer (3.11 recommended)
- **Frontend**: Node.js 18 or newer to build (22 or 24 LTS recommended; the frontend tests need 22.18 or newer)
- **Users**: a browser with WebGL 2 for the map (any current Chrome, Edge, Firefox or Safari)

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then set CARTO_API_KEY in it, see Configuration
python main.py                    # or: uvicorn main:app --reload --port 8000
```

The API runs at `http://localhost:8000`, with interactive documentation at `http://localhost:8000/docs`.

The hazard catalogue `data/hazard_layers.csv` (at the repository root) ships with all 29 layers as remote
URLs and works as is when the server has outbound HTTPS. To serve local copies instead, run
`python scripts/download_hazard_rasters.py` from the repository root: it downloads every layer into
`data/rasters/` and rewrites the catalogue to point at them.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The app runs at `http://localhost:5173`. The development server sends `/api/*` requests to the backend at
`http://localhost:8000`; set `BACKEND_URL` to use another address
(`BACKEND_URL=http://127.0.0.1:8123 npm run dev`).

## Using the app

1. **Upload** a dataset under *Upload Infrastructure Data*. CSV files need longitude/latitude columns
   (`lon`/`lat`, `lng`/`lat`, `longitude`/`latitude` or `x`/`y`) or a WKT column (`geometry`, `geom` or `wkt`).
2. **Select a hazard layer**. The ⓘ buttons explain each control and layer.
3. **Set the threshold**: type a value or drag the slider. A new layer starts at its minimum, which counts
   every asset the hazard reaches; the threshold carries over when you switch to another layer in the same
   unit (another return period or scenario).
4. **Damage (optional)**: tick *Vulnerability Analysis*, load a curve CSV (two columns, intensity and damage
   ratio, or four: intensity, lower, central, upper) and enter the replacement value: per asset for points,
   per metre for lines.
5. **Export** with the *Map*, *Barchart* and *Data* buttons under the chart.

Results update by themselves a moment after any change. The user guide (`docs/infra-risk-analyzer-guide.pdf`,
also behind the *Guide* button) walks through a full analysis.

## Project structure

```
backend/
├── main.py                    # FastAPI app: routers, CORS, /health
├── app/
│   ├── config.py              # Settings (paths, environment variables)
│   ├── api/
│   │   ├── upload.py          # Uploads, held in memory; expiry
│   │   ├── hazards.py         # Hazard catalogue and layer statistics
│   │   ├── analyze.py         # Exposure and damage analysis; per-upload caches
│   │   ├── tiles.py           # Hazard map tiles rendered from the rasters
│   │   └── export.py          # PNG map and chart, CSV / GeoPackage data
│   └── utils/
│       ├── geospatial.py      # Reading uploads, raster sampling, the analysis itself
│       └── export_data.py     # CSV and GeoPackage writers
├── tests/                     # python -m pytest
├── requirements.txt
└── requirements-dev.txt       # requirements.txt plus the test tools
frontend/
├── index.html
├── public/                    # The user guide opened by the Guide button
├── src/
│   ├── App.tsx                # Top-level state and the analysis requests
│   ├── components/
│   │   ├── Sidebar.tsx        # Controls, results and export buttons
│   │   ├── MapView.tsx        # MapLibre map, basemaps, hazard and asset layers
│   │   ├── BarChart.tsx       # Results chart
│   │   ├── DisclaimerDialog.tsx
│   │   └── ui/                # Small shared components
│   ├── lib/                   # Pop-up builder (escapes all user data), helpers
│   ├── services/api.ts        # Calls to the backend
│   └── types/index.ts
└── tests/                     # npm test
data/hazard_layers.csv         # The hazard catalogue (semicolon-separated)
scripts/download_hazard_rasters.py
docs/                          # Technical guide and user guide, Word and PDF
```

## API

All endpoints except `/health` start with `/api`; see `http://localhost:8000/docs` for the full reference.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/upload` | Upload a dataset (multipart field `file`); returns a `file_id`, metadata and the data as GeoJSON |
| GET | `/api/upload/{file_id}` | Metadata of an upload |
| DELETE | `/api/upload/{file_id}` | Delete an upload and its cached results |
| GET | `/api/hazards` | The hazard layers |
| GET | `/api/hazards/{hazard_id}` | One hazard layer |
| GET | `/api/hazards/{hazard_id}/stats` | A layer's minimum and maximum |
| POST | `/api/analyze` | Exposure analysis (JSON: `file_id`, `hazard_id`, optional `intensity_threshold`), or exposure and damage (multipart, adding `vulnerability_curve_file` and `replacement_value`). The raster is the catalogue's for `hazard_id` |
| GET | `/api/analyze/{file_id}/status` | Whether an upload is loaded |
| GET | `/api/tiles/{hazard_id}/{z}/{x}/{y}.png` | Map tile of a hazard layer |
| POST | `/api/export/barchart`, `/api/export/map` | PNG chart or map of the last analysis |
| POST | `/api/export/data` | CSV (`csv_points`, `csv_lines_aggregate`) or GeoPackage (`gpkg_lines_split`) of the last analysis |
| GET | `/health` | Health check |

## Configuration

The backend reads these environment variables, or `backend/.env` (copy `backend/.env.example`); a variable
set in the environment wins over the file.

| Variable | Default | Meaning |
|---|---|---|
| `CARTO_API_KEY` | (empty) | Key for the CARTO basemaps (Positron, the default, and Dark Matter). Without it they show an "API KEY REQUIRED" watermark |
| `UPLOAD_TTL_HOURS` | `4` | Hours an upload may go unused before it is deleted, with its cached results |
| `MAX_UPLOAD_MB` | `100` | Largest upload accepted; a zipped shapefile may unpack to at most ten times this |
| `MAX_UPLOADS` | `100` | Uploads held in memory at once; past this the least recently used is dropped |
| `CORS_ORIGINS` | (empty) | Comma-separated origins of other sites allowed to call the API from a browser |
| `HOST`, `PORT`, `DEBUG` | `0.0.0.0`, `8000`, `False` | Used by `python main.py` only (`DEBUG=true` turns on auto-reload); with `uvicorn`, pass `--host` and `--port` |

**Hazard catalogue** (`data/hazard_layers.csv`): semicolon-separated, with the columns `hazard` (display name)
and `dataset_url` (URL or path of a Cloud Optimized GeoTIFF in WGS84), and optionally `description`,
`background_paper`, `unit` and `category`. The layer ID used in the API is derived from the name.

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest                  # or: python -m unittest discover -s tests

cd ../frontend
npm test                          # Node 22.18 or newer
npm run lint
```

## Production

Build the frontend, serve `frontend/dist/` with a web server that forwards `/api/*` to the backend, and run
the backend with a single worker:

```bash
cd frontend
npm run build
sed -i 's/"__CARTO_API_KEY__"/"<your-key>"/' dist/index.html   # the CARTO key, after every build

cd ../backend
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

The built page carries a placeholder for the CARTO key, which the backend on this branch does not fill in
because it does not serve the page; write the key in as above, or the default basemap shows a watermark.

> **Note**: Always use `--workers 1`. Uploads and analysis results live in the memory of the backend
> process, so several workers would each see different data. Running several would need a shared store
> such as Redis.

The technical guide (`docs/`) has an nginx example, the proxy limits to set (100 MB bodies, timeouts of
several minutes) and the Posit Connect deployment, which lives on the `posit` branch: there the backend
serves the built frontend itself.

## Documentation

- [Technical guide](docs/infra-risk-analyzer-technical-guide.pdf): installation, configuration and deployment, for IT teams (Word source alongside)
- [User guide](docs/infra-risk-analyzer-guide.pdf): how to use the tool (Word source alongside); the app's Guide button opens the copy in `frontend/public`

## License

GPL-3
