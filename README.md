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

- **Backend**: Python 3.9 or higher
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
- `CARTO_API_KEY`: CARTO basemap key (Positron / Dark Matter), read from `backend/.env` (copy `backend/.env.example`); without it the tiles are requested unkeyed

The frontend dev proxy targets `http://localhost:8000`; override with `BACKEND_URL` if that port is
taken (`BACKEND_URL=http://127.0.0.1:8123 npm run dev`).

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

## License

GPL-3
