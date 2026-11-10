# Infrastructure Risk Analyzer — Backend

FastAPI backend: it parses uploads, renders hazard map tiles, runs the exposure and damage analysis, and
writes the exports. Uploads and results are held in memory only, so run it as a single process
(`--workers 1`). Configuration, the hazard catalogue and production deployment are described in the
[root README](../README.md) and the technical guide in `../docs/`.

## Setup

Python 3.10 or newer (3.11 recommended). The rasterio and pyogrio wheels bundle GDAL, so no system GDAL is
needed.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then set CARTO_API_KEY in it
```

## Running

```bash
python main.py                                       # development; DEBUG=true for auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000 # the same with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1   # production
```

The API is at `http://localhost:8000`, with interactive documentation (Swagger UI) at
`http://localhost:8000/docs` and ReDoc at `http://localhost:8000/redoc`.

## Trying the API with curl

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/hazards                 # the layer IDs, e.g. flood_hazard_100_years_existing_climate

# Upload: a zipped shapefile, a GeoPackage, a GeoJSON or a CSV with lon/lat columns
curl -F "file=@towers.csv" http://localhost:8000/api/upload          # returns a file_id

# Exposure at a threshold (in the layer's unit, here mm of flood depth)
curl -X POST http://localhost:8000/api/analyze -H "Content-Type: application/json" \
  -d '{"file_id": "<file_id>", "hazard_id": "flood_hazard_100_years_existing_climate", "intensity_threshold": 500}'

# Exposure and damage: a curve CSV and a replacement value (per asset for points, per metre for lines)
curl -X POST http://localhost:8000/api/analyze \
  -F file_id=<file_id> -F hazard_id=flood_hazard_100_years_existing_climate \
  -F intensity_threshold=500 -F replacement_value=100000 -F "vulnerability_curve_file=@curve.csv"

# The results of that analysis as CSV
curl -X POST http://localhost:8000/api/export/data -H "Content-Type: application/json" -o results.csv \
  -d '{"file_id": "<file_id>", "hazard_id": "flood_hazard_100_years_existing_climate", "intensity_threshold": 500, "mode": "csv_points"}'

curl -X DELETE http://localhost:8000/api/upload/<file_id>
```

The hazard raster is always the one the catalogue lists for `hazard_id`; a `hazard_url` sent by older
clients is ignored. An export needs the analysis at the same threshold to have run first.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest                  # or: python -m unittest discover -s tests
```

`tests/test_privacy.py` covers what happens to uploaded data (nothing on disk, expiry, deletion, no CORS
grant); `tests/test_analysis.py` covers uploads, the analysis and the exports against a small local raster.

## Structure

```
main.py                    # FastAPI app: routers, CORS, /health
app/
├── config.py              # Settings (paths, environment variables)
├── api/
│   ├── upload.py          # Uploads, held in memory; expiry and limits
│   ├── hazards.py         # Hazard catalogue (data/hazard_layers.csv) and layer statistics
│   ├── analyze.py         # Exposure and damage analysis; per-upload caches
│   ├── tiles.py           # Hazard map tiles
│   └── export.py          # PNG map and chart, CSV / GeoPackage data
└── utils/
    ├── geospatial.py      # Reading uploads, raster sampling, the analysis itself
    └── export_data.py     # CSV and GeoPackage writers
tests/
```

## Troubleshooting

- **Port already in use**: set `PORT` (for `python main.py`) or pass `--port` to uvicorn.
- **No hazard layers listed**: the catalogue is missing or unreadable. On this branch it is
  `data/hazard_layers.csv` here in `backend/`, with paths on the Posit server; for a local run, start the
  backend with `HAZARD_LAYERS_CSV=../data/hazard_layers.csv` (the remote layers).
- **"Could not read the hazard layer" (502)**: the server could not reach the raster host
  (`hazards-data.unepgrid.ch` for the shipped catalogue); check outbound HTTPS and run the analysis again.
