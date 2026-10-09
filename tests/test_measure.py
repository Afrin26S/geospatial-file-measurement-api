import geopandas as gpd
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from app.services.measure import MeasurementError, measure_geometry

WGS84 = "EPSG:4326"
UTM43 = "EPSG:32643"  # UTM zone 43N covers Bengaluru


def utm_to_wgs84(geom):
    """Build test shapes with exact metre sizes in UTM, then convert to lat/lon."""
    return gpd.GeoSeries([geom], crs=UTM43).to_crs(WGS84).iloc[0]


def test_degrees_are_not_metres():
    # The trap this project avoids: shapely on raw lat/lon gives 'square degrees'.
    square = box(77.5946, 12.9716, 77.5956, 12.9726)
    assert square.area < 1e-5


def test_square_plot_matches_sample_kml():
    square = box(77.5946, 12.9716, 77.5956, 12.9726)
    m = measure_geometry(square, WGS84)
    assert m.status == "measured"
    assert m.projected_crs == "EPSG:32643"
    assert m.area_sq_m == pytest.approx(12000, rel=0.01)


def test_line_length_matches_sample_kml():
    road = LineString([(77.5946, 12.9716), (77.6046, 12.9716)])
    m = measure_geometry(road, WGS84)
    assert m.length_m == pytest.approx(1085, rel=0.01)


def test_100m_square_is_10000_sq_m_and_methods_agree():
    shape = utm_to_wgs84(box(780000, 1434000, 780100, 1434100))
    m = measure_geometry(shape, WGS84)
    assert m.area_sq_m == pytest.approx(10000, rel=1e-3)
    assert m.geodesic_area_sq_m == pytest.approx(10000, rel=0.005)
    assert m.discrepancy_pct < 0.5


def test_polygon_with_hole_subtracts_the_hole():
    outer = box(780000, 1434000, 780100, 1434100)
    hole = box(780040, 1434040, 780060, 1434060)  # 20 m x 20 m
    shape = utm_to_wgs84(Polygon(outer.exterior.coords, [hole.exterior.coords]))
    m = measure_geometry(shape, WGS84)
    assert m.area_sq_m == pytest.approx(9600, rel=1e-3)


def test_multipolygon_sums_its_parts():
    a = box(780000, 1434000, 780100, 1434100)
    b = box(780300, 1434000, 780400, 1434100)
    m = measure_geometry(utm_to_wgs84(MultiPolygon([a, b])), WGS84)
    assert m.area_sq_m == pytest.approx(20000, rel=1e-3)


def test_point_has_no_measurement():
    m = measure_geometry(Point(77.6, 12.98), WGS84)
    assert m.status == "not_applicable"
    assert m.area_sq_m is None and m.length_m is None


def test_unsupported_geometry_is_handled_gracefully():
    collection = GeometryCollection([Point(77.6, 12.98), Point(77.7, 12.99)])
    m = measure_geometry(collection, WGS84)
    assert m.status == "unsupported"
    assert m.warnings


def test_empty_geometry_is_handled_gracefully():
    m = measure_geometry(Polygon(), WGS84)
    assert m.status == "empty"


def test_invalid_bowtie_polygon_is_repaired_with_warning():
    x, y, d = 77.5946, 12.9716, 0.001
    bowtie = Polygon([(x, y), (x + d, y + d), (x + d, y), (x, y + d)])
    assert not bowtie.is_valid
    m = measure_geometry(bowtie, WGS84)
    assert m.status == "measured"
    assert any("repaired" in w for w in m.warnings)
    # the two triangles together are half of the 12,000 m^2 square
    assert 5000 < m.area_sq_m < 7000


def test_missing_crs_raises_clear_error():
    with pytest.raises(MeasurementError):
        measure_geometry(box(77.5946, 12.9716, 77.5956, 12.9726), None)
