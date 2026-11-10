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
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    DATA_DIR: Path = BASE_DIR / "data"
    HAZARD_LAYERS_CSV: Path = DATA_DIR / "hazard_layers.csv"
    
    # Ensure directories exist
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # CARTO basemap API key — CARTO's raster basemaps (Positron / Dark Matter)
    # now require ?key=<...>. Used server-side for PNG export basemaps and, where
    # the backend serves the built SPA, substituted into index.html for the
    # interactive map. Empty = request unkeyed.
    CARTO_API_KEY: str = os.getenv("CARTO_API_KEY", "")

    # File upload settings
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
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

