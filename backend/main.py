"""
FastAPI backend for Infrastructure Risk Analyzer
"""

import json

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
import uvicorn
from typing import Optional
from pathlib import Path

from app.api import upload, hazards, analyze, tiles, export
from app.config import settings

app = FastAPI(
    title="Infrastructure Risk Analyzer API",
    description="API for analyzing infrastructure assets against hazard layers",
    version="1.0.0"
)

# The frontend is served from the same origin (and proxied there by `vite dev`), so no
# other site may call the API from a browser unless CORS_ORIGINS names it. A wildcard
# with credentials let any page read the API with the user's cookies.
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Endpoints whose bodies are already compressed (PNG). Re-running gzip over
# them costs CPU on the busiest route in the app and saves nothing.
ALREADY_COMPRESSED = ("/api/tiles/", "/api/export/")


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZip responses except the ones that are already compressed bytes.

    Starlette's GZipMiddleware has no content-type filter, so on its own it
    would re-compress every map tile. The upload response is the reason this
    is here at all: it carries the whole dataset as GeoJSON, which is several
    hundred KB to a few MB of highly compressible text.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and any(p in scope["path"] for p in ALREADY_COMPRESSED):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(SelectiveGZipMiddleware, minimum_size=1024, compresslevel=6)

# Include routers
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(hazards.router, prefix="/api", tags=["hazards"])
app.include_router(analyze.router, prefix="/api", tags=["analyze"])
app.include_router(tiles.router, prefix="/api", tags=["tiles"])
app.include_router(export.router, prefix="/api", tags=["export"])

STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
INDEX_PATH = STATIC_DIR / "index.html"

# The built index.html carries a "__CARTO_API_KEY__" placeholder (see
# frontend/index.html). It is filled in when the page is served rather than at
# build time, so the key stays in the environment (Posit Connect "Vars") instead
# of being committed with backend/static.
_CARTO_KEY_PLACEHOLDER = '"__CARTO_API_KEY__"'


def _spa_index_response() -> HTMLResponse:
    """Serve the SPA shell with the CARTO API key substituted in."""
    html = INDEX_PATH.read_text(encoding="utf-8")
    return HTMLResponse(html.replace(_CARTO_KEY_PLACEHOLDER, json.dumps(settings.CARTO_API_KEY)))

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint"""
    if INDEX_PATH.exists():
        return _spa_index_response()
    return {"message": "Infrastructure Risk Analyzer API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

def _static_file_path(relative_path: str) -> Optional[Path]:
    """Resolve a path under STATIC_DIR, rejecting directory traversal."""
    static_root = STATIC_DIR.resolve()
    candidate = (STATIC_DIR / relative_path).resolve()
    # A path test, not a string-prefix test: "static_old/..." starts with "static"
    if not candidate.is_relative_to(static_root):
        return None
    return candidate if candidate.is_file() else None


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    static_file = _static_file_path(full_path)
    if static_file is not None and static_file != INDEX_PATH.resolve():
        return FileResponse(static_file)

    if INDEX_PATH.exists():
        return _spa_index_response()
    raise HTTPException(status_code=404, detail="Not Found")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )

