"""Response models (what the API returns)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorInfo(BaseModel):
    code: str
    message: str


class FileInfo(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None = None
    status: str
    kind: str | None = None
    crs_assumed: bool = False
    created_at: datetime
    summary: dict[str, Any] | None = None
    error: ErrorInfo | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "5f0c2b9e8a7d4c3f9b1e6a2d4c8f0b17",
                    "filename": "survey.kml",
                    "feature_count": 3,
                    "crs": "EPSG:4326",
                    "status": "COMPLETED",
                    "kind": "kml",
                    "crs_assumed": False,
                    "created_at": "2026-10-09T10:15:00",
                    "summary": {
                        "features_by_type": {"Polygon": 1, "LineString": 1, "Point": 1},
                        "features_by_status": {"measured": 2, "not_applicable": 1},
                        "total_area_sq_m": 12016.9781,
                        "total_length_m": 1085.6045,
                    },
                    "error": None,
                }
            ]
        }
    )


class MeasurementOut(BaseModel):
    status: str  # measured | not_applicable | unsupported | empty | failed
    area: float | None = None
    length: float | None = None
    projected_crs: str | None = None
    geodesic_area: float | None = None
    geodesic_length: float | None = None
    discrepancy_pct: float | None = None
    warnings: list[str] = []


class FeatureOut(BaseModel):
    feature_index: int
    geometry_type: str | None = None
    geometry: dict[str, Any] | None = None
    crs: str | None = None
    properties: dict[str, Any] = {}
    measurement: MeasurementOut


class MeasurementsResponse(BaseModel):
    file_id: str
    status: str
    area_unit: str
    length_unit: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]
