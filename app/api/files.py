"""The three endpoints required by the assignment, plus query options."""
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import ApiError
from app.models import FeatureRecord, UploadedFile
from app.schemas import (
    ErrorInfo,
    FeatureOut,
    FileInfo,
    MeasurementOut,
    MeasurementsResponse,
)
from app.services.processing import process_file
from app.services.reader import MAX_UPLOAD_BYTES, FileReadError, detect_kind
from app.services.units import (
    AREA_FACTORS,
    LENGTH_FACTORS,
    AreaUnit,
    LengthUnit,
    convert,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_SUFFIXES = {".zip", ".kml"}
HTTP_STATUS_FOR_CODE = {
    "unsupported_type": 415,
    "zip_too_large": 413,
    "zip_too_many_files": 413,
}


# --- helpers ------------------------------------------------------------------
def _save_upload(upload: UploadFile, dest: Path) -> None:
    """Stream the upload to disk in chunks, enforcing the size limit."""
    size = 0
    with open(dest, "wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise ApiError(
                    413, "file_too_large",
                    f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
            out.write(chunk)
    if size == 0:
        raise ApiError(422, "empty_file", "The uploaded file is empty")


def _mark_failed(db: Session, file_id: str, code: str, message: str) -> None:
    db.rollback()
    record = db.get(UploadedFile, file_id)
    record.status = "FAILED"
    record.error_code = code
    record.error_message = message
    db.commit()


def _get_record_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise ApiError(404, "not_found", f"No file with id '{file_id}'")
    return record


def _file_info(record: UploadedFile) -> FileInfo:
    return FileInfo(
        id=record.id,
        filename=record.filename,
        feature_count=record.feature_count,
        crs=record.crs,
        status=record.status,
        kind=record.kind,
        crs_assumed=record.crs_assumed,
        created_at=record.created_at,
        summary=json.loads(record.summary_json) if record.summary_json else None,
        error=(
            ErrorInfo(code=record.error_code, message=record.error_message)
            if record.error_code
            else None
        ),
    )


def _feature_out(row: FeatureRecord, area_factor: float, length_factor: float,
                 include_geometry: bool) -> FeatureOut:
    return FeatureOut(
        feature_index=row.feature_index,
        geometry_type=row.geometry_type,
        geometry=json.loads(row.geometry_json) if include_geometry and row.geometry_json else None,
        crs=row.crs,
        properties=json.loads(row.properties_json),
        measurement=MeasurementOut(
            status=row.measurement_status,
            area=convert(row.area_sq_m, area_factor),
            length=convert(row.length_m, length_factor),
            projected_crs=row.projected_crs,
            geodesic_area=convert(row.geodesic_area_sq_m, area_factor),
            geodesic_length=convert(row.geodesic_length_m, length_factor),
            discrepancy_pct=(
                round(row.discrepancy_pct, 4) if row.discrepancy_pct is not None else None
            ),
            warnings=json.loads(row.warnings_json),
        ),
    )


# --- endpoints ----------------------------------------------------------------
@router.post("/", response_model=FileInfo, status_code=201,
             summary="Upload a shapefile (.zip) or a KML file")
def upload_file(
    file: UploadFile = File(..., description="A .zip containing a shapefile, or a .kml file"),
    assume_crs: str | None = Query(
        None, description="CRS to assume when the file has none, e.g. EPSG:4326"
    ),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ApiError(415, "unsupported_type", "Only .zip (shapefile) and .kml files are supported")

    # Never use the client's filename on disk: store under a random id instead.
    file_id = uuid.uuid4().hex
    upload_dir = settings.upload_dir / file_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"original{suffix}"
    try:
        _save_upload(file, dest)
    except ApiError:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    record = UploadedFile(
        id=file_id,
        filename=filename,
        kind=detect_kind(dest),
        status="PROCESSING",
        stored_path=str(dest),
    )
    db.add(record)
    db.commit()

    try:
        process_file(db, record, dest, assume_crs=assume_crs)
    except FileReadError as exc:
        _mark_failed(db, file_id, exc.code, exc.message)
        raise ApiError(
            HTTP_STATUS_FOR_CODE.get(exc.code, 422), exc.code, exc.message, file_id=file_id
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected failure while processing upload %s", file_id)
        message = "Unexpected error while processing the file"
        _mark_failed(db, file_id, "processing_failed", message)
        raise ApiError(500, "processing_failed", message, file_id=file_id) from exc

    return _file_info(record)


@router.get("/{file_id}/", response_model=FileInfo, summary="Information about an uploaded file")
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _file_info(_get_record_or_404(db, file_id))


@router.get("/{file_id}/measurements/", response_model=MeasurementsResponse,
            summary="Per-feature details and measurements")
def get_measurements(
    file_id: str,
    area_unit: AreaUnit = Query("sq_m", description="Unit for areas"),
    length_unit: LengthUnit = Query("m", description="Unit for lengths"),
    include_geometry: bool = Query(True, description="Set false for a lighter response"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    record = _get_record_or_404(db, file_id)
    if record.status != "COMPLETED":
        raise ApiError(
            409, "file_not_ready",
            f"File status is {record.status}; measurements are only available when COMPLETED",
        )

    rows = db.scalars(
        select(FeatureRecord)
        .where(FeatureRecord.file_id == file_id)
        .order_by(FeatureRecord.feature_index)
        .offset(offset)
        .limit(limit)
    ).all()

    area_factor, length_factor = AREA_FACTORS[area_unit], LENGTH_FACTORS[length_unit]
    return MeasurementsResponse(
        file_id=record.id,
        status=record.status,
        area_unit=area_unit,
        length_unit=length_unit,
        total=record.feature_count,
        limit=limit,
        offset=offset,
        features=[_feature_out(r, area_factor, length_factor, include_geometry) for r in rows],
    )
