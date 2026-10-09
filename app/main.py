"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload
Docs at:   http://127.0.0.1:8000/docs
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse

from app.api import files
from app.config import settings
from app.db import Base, engine
from app.errors import ApiError, api_error_handler, validation_error_handler


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    version="1.0.0",
    description=(
        "Upload a zipped shapefile or a KML file and get per-feature geometry, "
        "attributes and measurements (area for polygons, length for lines). "
        "Measurements are computed in a projected CRS (UTM) and cross-checked "
        "with a geodesic calculation."
    ),
    lifespan=lifespan,
)
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(files.router)


@app.get("/", include_in_schema=False)
def index():
    """The small web UI (app/static/index.html)."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
