"""
Application configuration
"""

import os
from pathlib import Path
from typing import Optional


# backend/.env (gitignored) holds the API keys, such as CARTO_API_KEY below. Load
# it before anything reads the environment — Settings' attributes are evaluated
# once, when the class is defined. Variables already set in the environment win
# over the file.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # python-dotenv missing; the environment alone applies
    pass

class Settings:
    """Application settings"""
    
    # Server settings
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    
    # Paths. Uploads are never written to disk (they are parsed in the system temp
    # folder and held in memory), so there is no upload directory.
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    HAZARD_LAYERS_CSV: Path = DATA_DIR / "hazard_layers.csv"

    # CARTO basemap API key — CARTO's raster basemaps (Positron / Dark Matter)
    # now require ?key=<...>. Used server-side for PNG export basemaps and, where
    # the backend serves the built SPA, substituted into index.html for the
    # interactive map. Empty = request unkeyed.
    CARTO_API_KEY: str = os.getenv("CARTO_API_KEY", "")

    # File upload settings
    MAX_UPLOAD_SIZE: int = int(float(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024)
    # A zipped shapefile may unpack to at most this many times the upload limit
    MAX_UNZIPPED_FACTOR: int = 10
    # Uploads held in memory at once; past this, the least recently used is dropped
    MAX_UPLOADS: int = int(os.getenv("MAX_UPLOADS", "100"))
    ALLOWED_EXTENSIONS: set = {".shp", ".gpkg", ".zip", ".csv", ".geojson"}
    # An upload unused for this long is deleted from memory, with its cached results.
    # The browser deletes its own upload when the user replaces it or leaves the page;
    # this catches the rest (a closed laptop, a lost connection).
    UPLOAD_TTL_HOURS: float = float(os.getenv("UPLOAD_TTL_HOURS", "4"))

    # Other origins allowed to call the API from a browser, comma-separated. Empty (the
    # default): none — the app's own pages are same-origin and need no CORS.
    CORS_ORIGINS: list = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
    ]

settings = Settings()

