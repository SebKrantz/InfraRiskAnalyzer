"""
FastAPI backend for Infrastructure Risk Analyzer
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from typing import Optional

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

# Include routers
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(hazards.router, prefix="/api", tags=["hazards"])
app.include_router(analyze.router, prefix="/api", tags=["analyze"])
app.include_router(tiles.router, prefix="/api", tags=["tiles"])
app.include_router(export.router, prefix="/api", tags=["export"])


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Infrastructure Risk Analyzer API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )

