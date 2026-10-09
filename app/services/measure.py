"""Measurement logic: pick a projected CRS, then compute area / length.

This module knows nothing about FastAPI or the database, so it is easy to
test and easy to explain.

Two independent methods are used for every measurable feature:

1. Projected: transform the geometry to the UTM zone that matches its
   location (metres), then use shapely's flat-plane area / length.
2. Geodesic: measure directly on the WGS 84 ellipsoid with pyproj.Geod.

The projected value is the main result (as the assignment asks). The geodesic
value is a cross-check, and the gap between them is reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd
from pyproj import CRS, Geod
from shapely import force_2d, make_valid
from shapely.ops import unary_union
from shapely.validation import explain_validity

GEOD = Geod(ellps="WGS84")

POLYGON_TYPES = {"Polygon", "MultiPolygon"}
LINE_TYPES = {"LineString", "MultiLineString"}
POINT_TYPES = {"Point", "MultiPoint"}

# If projected and geodesic results differ by more than this, add a warning.
DISCREPANCY_WARN_PCT = 0.5


class MeasurementError(Exception):
    """Raised when a geometry cannot be measured at all (e.g. unknown CRS)."""


@dataclass
class Measurement:
    geometry_type: str | None
    # measured | not_applicable | unsupported | empty
    status: str
    source_crs: str | None = None
    projected_crs: str | None = None
    area_sq_m: float | None = None
    length_m: float | None = None
    geodesic_area_sq_m: float | None = None
    geodesic_length_m: float | None = None
    discrepancy_pct: float | None = None
    warnings: list[str] = field(default_factory=list)


def crs_label(crs) -> str:
    """Short readable name, e.g. 'EPSG:32643'."""
    crs = CRS.from_user_input(crs)
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name


def _parts(geom):
    """Single geometries yield themselves; Multi* geometries yield their parts."""
    return list(getattr(geom, "geoms", [geom]))


def _repair_polygonal(geom):
    """Fix an invalid polygon and keep only the polygon parts of the result."""
    fixed = make_valid(geom)
    if fixed.geom_type in POLYGON_TYPES:
        return fixed
    parts = [g for g in _parts(fixed) if g.geom_type in POLYGON_TYPES]
    return unary_union(parts) if parts else None


def _geodesic_area(geom_lonlat) -> float:
    """Area in m^2 on the ellipsoid. Input must be lon/lat (EPSG:4326)."""
    total = 0.0
    for poly in _parts(geom_lonlat):
        area, _ = GEOD.polygon_area_perimeter(*poly.exterior.xy)
        total += abs(area)  # the sign only tells us the ring direction
        for hole in poly.interiors:
            hole_area, _ = GEOD.polygon_area_perimeter(*hole.xy)
            total -= abs(hole_area)
    return total


def _geodesic_length(geom_lonlat) -> float:
    """Length in metres on the ellipsoid. Input must be lon/lat (EPSG:4326)."""
    return sum(GEOD.line_length(*part.xy) for part in _parts(geom_lonlat))


def measure_geometry(geom, source_crs) -> Measurement:
    """Measure one geometry. Never crashes on odd geometry types."""
    if geom is None or geom.is_empty:
        return Measurement(
            geometry_type=getattr(geom, "geom_type", None),
            status="empty",
            warnings=["Feature has no geometry"],
        )

    gtype = geom.geom_type
    result = Measurement(
        geometry_type=gtype,
        status="measured",
        source_crs=crs_label(source_crs) if source_crs is not None else None,
    )

    if gtype in POINT_TYPES:
        result.status = "not_applicable"
        return result

    if gtype not in POLYGON_TYPES | LINE_TYPES:
        result.status = "unsupported"
        result.warnings.append(f"Measurement is not supported for {gtype}")
        return result

    if source_crs is None:
        raise MeasurementError(
            "Source CRS is unknown (shapefile without a .prj?). "
            "Cannot measure safely."
        )

    geom = force_2d(geom)  # shapely's area/length ignore Z anyway; drop it

    is_polygon = gtype in POLYGON_TYPES
    if is_polygon and not geom.is_valid:
        result.warnings.append(
            f"Invalid polygon was repaired before measuring: {explain_validity(geom)}"
        )
        geom = _repair_polygonal(geom)
        if geom is None or geom.is_empty:
            result.status = "unsupported"
            result.warnings.append("Polygon could not be repaired")
            return result

    series = gpd.GeoSeries([geom], crs=source_crs)

    # Method 1: projected. Pick the UTM zone from this feature's own location.
    utm_crs = series.estimate_utm_crs()
    projected = series.to_crs(utm_crs).iloc[0]
    result.projected_crs = crs_label(utm_crs)

    # Method 2: geodesic, on the ellipsoid, from lon/lat coordinates.
    lonlat = series.to_crs(4326).iloc[0]

    if is_polygon:
        result.area_sq_m = projected.area
        result.geodesic_area_sq_m = _geodesic_area(lonlat)
        main_value, reference = result.area_sq_m, result.geodesic_area_sq_m
    else:
        result.length_m = projected.length
        result.geodesic_length_m = _geodesic_length(lonlat)
        main_value, reference = result.length_m, result.geodesic_length_m

    if reference:
        result.discrepancy_pct = abs(main_value - reference) / reference * 100
        if result.discrepancy_pct > DISCREPANCY_WARN_PCT:
            result.warnings.append(
                f"Projected and geodesic results differ by "
                f"{result.discrepancy_pct:.2f}%"
            )

    return result
