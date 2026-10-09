"""Orchestrates one upload: read -> measure every feature -> save to the database.

The API layer stays thin; all the "what happens to a file" logic is here.
"""
import json
import logging
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import FeatureRecord, UploadedFile
from app.services.measure import Measurement, MeasurementError, crs_label, measure_geometry
from app.services.reader import clean_properties, read_geofile

logger = logging.getLogger(__name__)


def _measure_safely(geom, crs) -> Measurement:
    """One bad feature must never fail the whole file."""
    geometry_type = geom.geom_type if geom is not None else None
    try:
        return measure_geometry(geom, crs)
    except MeasurementError as exc:
        return Measurement(geometry_type=geometry_type, status="failed", warnings=[str(exc)])
    except Exception:
        logger.exception("Unexpected error while measuring a %s feature", geometry_type)
        return Measurement(
            geometry_type=geometry_type,
            status="failed",
            warnings=["Unexpected error while measuring this feature"],
        )


def _geometry_json(geom) -> str | None:
    if geom is None or geom.is_empty:
        return None
    return json.dumps(geom.__geo_interface__)


def _build_summary(measurements: list[Measurement]) -> dict:
    total_area = sum(m.area_sq_m for m in measurements if m.area_sq_m is not None)
    total_length = sum(m.length_m for m in measurements if m.length_m is not None)
    return {
        "features_by_type": dict(Counter(str(m.geometry_type) for m in measurements)),
        "features_by_status": dict(Counter(m.status for m in measurements)),
        "total_area_sq_m": round(total_area, 4),
        "total_length_m": round(total_length, 4),
    }


def process_file(db: Session, record: UploadedFile, path: Path, assume_crs: str | None = None) -> None:
    """Fill in `record` and its features. Raises FileReadError if the file is unusable."""
    loaded = read_geofile(path, assume_crs=assume_crs)
    source_crs = loaded.gdf.crs
    label = crs_label(source_crs)
    properties = clean_properties(loaded.gdf)

    measurements: list[Measurement] = []
    rows: list[FeatureRecord] = []
    for index, (geom, props) in enumerate(zip(loaded.gdf.geometry, properties)):
        m = _measure_safely(geom, source_crs)
        measurements.append(m)
        rows.append(
            FeatureRecord(
                file_id=record.id,
                feature_index=index,
                geometry_type=m.geometry_type,
                geometry_json=_geometry_json(geom),
                properties_json=json.dumps(props),
                crs=label,
                measurement_status=m.status,
                projected_crs=m.projected_crs,
                area_sq_m=m.area_sq_m,
                length_m=m.length_m,
                geodesic_area_sq_m=m.geodesic_area_sq_m,
                geodesic_length_m=m.geodesic_length_m,
                discrepancy_pct=m.discrepancy_pct,
                warnings_json=json.dumps(m.warnings),
            )
        )

    db.add_all(rows)
    record.kind = loaded.kind
    record.crs = label
    record.crs_assumed = loaded.crs_assumed
    record.feature_count = len(rows)
    record.summary_json = json.dumps(_build_summary(measurements))
    record.status = "COMPLETED"
    db.commit()
