# Infrastructure Risk Analyzer

A high-performance web application for analyzing infrastructure assets against hazard layers. Upload spatial datasets (Shapefile, GeoPackage, or CSV) representing infrastructure and visualize their exposure to various hazard risks with interactive maps and statistics.

## Features

- **Multi-format Support**: Upload infrastructure data as Shapefiles, GeoPackages, or CSV files with coordinates
- **Hazard Layer Analysis**: Analyze infrastructure against multiple hazard layers (floods, earthquakes, etc.)
- **Interactive Visualization**: 
  - Dynamic map with multiple basemap options
  - Color-coded hazard layers with adjustable opacity
  - Real-time affected/unaffected infrastructure visualization
- **Customizable Analysis**:
  - Adjustable hazard intensity thresholds
  - Multiple color palettes for hazard visualization
  - Layer visibility controls
- **Statistical Summary**: Bar charts showing affected vs. unaffected infrastructure counts or lengths
- **User-Friendly Interface**: Collapsible sidebar with info tooltips and responsive design

## Tech Stack

### Backend
- **FastAPI** - Modern Python web framework
- **Geospatial Libraries**: `geopandas`, `xarray`, `rasterio`, `pyogrio`, `pandas`
- **Cloud Optimized GeoTIFF (COG)** support for efficient raster processing

### Frontend
- **React** + **TypeScript** - Component-based UI
- **Vite** - Fast build tool and dev server
- **MapLibre GL** - High-performance map rendering
- **Recharts** - Data visualization
- **TailwindCSS** + **shadcn/ui** - Modern styling and UI components

## Quick Start

### Prerequisites

- **Backend**: Python 3.10 or higher
- **Frontend**: Node.js 18+ and npm

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create data directory and configure hazard layers:
```bash
mkdir -p data
```

Create `data/hazard_layers.csv` with semicolon-delimited format:
```csv
hazard;dataset_url;description;background_paper
Flood Hazard 10 Years;https://example.com/flood.tif;Description;Paper URL
```

5. Start the backend server:
```bash
python main.py
# Or: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

The application will be available at `http://localhost:5173`

## Usage

1. **Upload Infrastructure Data**: 
   - Click "Upload Infrastructure Data" and select a Shapefile (zipped), GeoPackage, or CSV file
   - CSV files should contain coordinate columns (`lat`/`lon`, `latitude`/`longitude`, or `y`/`x`)

2. **Select Hazard Layer**: 
   - Choose a hazard layer from the dropdown
   - Click the info icon (ℹ️) to view metadata and background papers

3. **Customize Visualization**:
   - Select a color palette for the hazard layer
   - Adjust opacity (0-100%)
   - Set hazard intensity threshold using the slider

4. **View Results**:
   - The map shows affected (red) and unaffected (green) infrastructure
   - The bar chart displays summary statistics
   - Toggle layer visibility using the layer control panel

## Project Structure

### Backend

```
backend/
├── main.py                # FastAPI application entry point
├── app/
│   ├── config.py          # Configuration & paths
│   ├── api/
│   │   ├── upload.py      # File upload endpoints
│   │   ├── hazards.py     # Hazard layer endpoints
│   │   ├── analyze.py     # Analysis endpoints
│   │   └── tiles.py       # Tile serving endpoints
│   └── utils/
│       └── geospatial.py  # Geospatial utilities
├── requirements.txt       # Dependencies
└── README.md              # Backend setup instructions
```

### Frontend

```
frontend/
├── index.html             # HTML entry point
├── package.json           # NPM dependencies & scripts
├── vite.config.ts         # Vite configuration
├── tsconfig.json          # TypeScript configuration
├── tailwind.config.js     # TailwindCSS configuration
├── postcss.config.js      # PostCSS configuration
├── README.md              # Frontend setup instructions
└── src/
    ├── main.tsx           # React entry point
    ├── App.tsx            # Main app component
    ├── index.css          # Global styles
    ├── vite-env.d.ts      # Vite type definitions
    ├── components/
    │   ├── ui/            # Reusable UI components
    │   │   ├── button.tsx
    │   │   ├── input.tsx
    │   │   ├── select.tsx
    │   │   ├── slider.tsx
    │   │   ├── card.tsx
    │   │   └── dialog.tsx
    │   ├── Sidebar.tsx    # Collapsible sidebar with controls
    │   ├── MapView.tsx    # Map component with basemap selector
    │   └── BarChart.tsx   # Chart component (Recharts)
    ├── types/
    │   └── index.ts       # TypeScript type definitions
    ├── services/
    │   └── api.ts         # API service functions
    └── lib/
        └── utils.ts       # Utility functions
```

## API Endpoints

### Upload
- `POST /api/upload` - Upload spatial file (Shapefile, GeoPackage, or CSV)

### Hazards
- `GET /api/hazards` - List available hazard layers
- `GET /api/hazards/{hazard_id}` - Get hazard layer details
- `GET /api/hazards/{hazard_id}/stats` - Get hazard statistics (min, max, mean)

### Analysis
- `POST /api/analyze` - Perform spatial intersection analysis

### Tiles
- `GET /api/tiles/{hazard_id}/{z}/{x}/{y}.png` - Get hazard layer tiles

For detailed API documentation, visit `http://localhost:8000/docs` when the backend is running.

## Configuration

### Backend Environment Variables

- `HOST`: Server host (default: `0.0.0.0`)
- `PORT`: Server port (default: `8000`)
- `DEBUG`: Debug mode (default: `False`)
- `UPLOAD_DIR`: Upload directory (default: `backend/uploads`)
- `DATA_DIR`: Data directory (default: `backend/data`)

### Data Format

**Hazard Layers CSV** (`data/hazard_layers.csv`):
- Delimiter: semicolon (`;`)
- Required columns: `hazard`, `dataset_url`
- Optional columns: `description`, `background_paper`

**Infrastructure Upload Formats**:
- Shapefile: Zipped with `.shp`, `.shx`, `.dbf` (and optionally `.prj`)
- GeoPackage: Single `.gpkg` file
- CSV: Must contain coordinate columns (see usage section)

## Development

### Backend Development

Run with auto-reload:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development

The Vite dev server includes hot module replacement (HMR) for instant updates.

### Building for Production

**Frontend**:
```bash
cd frontend
npm run build
```

**Backend**:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Note**: Use `--workers 1` as the application uses in-memory state for uploaded files and analysis caching. Multi-worker deployment would require Redis or similar for shared state.  

### Posit Connect Deployment

Posit Connect supports FastAPI applications and can serve the built React assets from the same app.

1. Build the frontend for Connect (outputs to `backend/static`):
```bash
cd frontend
npm run build:connect
```

2. Deploy the FastAPI app from the repo root:
```bash
rsconnect deploy fastapi -n <saved-server-name> --entrypoint main:app --exclude .env backend/
```

Notes:
- Set `CARTO_API_KEY` under the content's Vars on Connect. For a local run, copy `backend/.env.example` to `backend/.env`; `--exclude .env` keeps that file out of the bundle.
- Ensure `data/hazard_layers.csv` is present on the server or included in the bundle.
- Keep `--workers 1` in production unless you add a shared cache (e.g., Redis).
- See the Posit Connect FastAPI docs for entrypoint details.

## Documentation

- [Technical guide](docs/infra-risk-analyzer-technical-guide.pdf): installation, configuration and deployment, for IT teams (Word source alongside)
- [User guide](frontend/public/infra-risk-analyzer-guide.pdf): how to use the tool, opened by the Guide button in the app

## License

GPL-3
