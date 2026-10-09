"""Generate sample and deliberately broken files for manual testing.

Run once from the project root:
    python make_test_files.py

Creates samples/test_files/ and a README.txt there listing what each file should do.
"""
import sys
import tempfile
import zipfile
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/test_files")

KML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>\n'
KML_TAIL = "</Document></kml>\n"


def ring(x: float, y: float, size: float) -> str:
    """KML coordinate string for a square whose lower-left corner is (x, y)."""
    pts = [(x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y)]
    return " ".join(f"{a},{b},0" for a, b in pts)


def placemark(name: str, geometry: str) -> str:
    return f"<Placemark><name>{name}</name>{geometry}</Placemark>\n"


def polygon(outer: str, inner: str | None = None) -> str:
    hole = f"<innerBoundaryIs><LinearRing><coordinates>{inner}</coordinates></LinearRing></innerBoundaryIs>" if inner else ""
    return (f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{outer}</coordinates>"
            f"</LinearRing></outerBoundaryIs>{hole}</Polygon>")


def make_plain_files() -> None:
    """Files that need nothing but the standard library."""
    OUT.mkdir(parents=True, exist_ok=True)

    # --- broken uploads ------------------------------------------------------
    (OUT / "broken.zip").write_bytes(b"this is definitely not a zip file")
    zipfile.ZipFile(OUT / "empty.zip", "w").close()
    with zipfile.ZipFile(OUT / "no_shp.zip", "w") as zf:
        zf.writestr("readme.txt", "a zip without any shapefile in it")
    with zipfile.ZipFile(OUT / "zipslip.zip", "w") as zf:
        zf.writestr("../evil.shp", b"x")          # tries to escape the extraction folder
    (OUT / "fake.kml").write_text("just some text pretending to be a KML")
    (OUT / "empty.kml").write_bytes(b"")
    (OUT / "malformed.kml").write_text(
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>oops')
    (OUT / "no_features.kml").write_text(KML_HEAD + "<name>nothing here</name>\n" + KML_TAIL)
    (OUT / "notes.txt").write_text("a plain text file")

    # --- valid KML with tricky features ---------------------------------------
    body = "".join([
        placemark("Odisha plot (UTM zone 45)", polygon(ring(85.88, 20.46, 0.001))),
        placemark("Plot with courtyard", polygon(ring(85.88, 20.47, 0.001), ring(85.8803, 20.4703, 0.0004))),
        placemark("Bow-tie (invalid polygon)", polygon(
            "77.5946,12.9716,0 77.5956,12.9726,0 77.5956,12.9716,0 77.5946,12.9726,0 77.5946,12.9716,0")),
        placemark("Mixed MultiGeometry", "<MultiGeometry><Point><coordinates>77.6,12.98,0</coordinates></Point>"
                  "<LineString><coordinates>77.6,12.98,0 77.61,12.98,0</coordinates></LineString></MultiGeometry>"),
        placemark("Bent road", "<LineString><coordinates>85.88,20.46,0 85.89,20.46,0 85.89,20.47,0</coordinates></LineString>"),
        placemark("Survey point", "<Point><coordinates>85.9,20.5,0</coordinates></Point>"),
    ])
    (OUT / "edge_cases.kml").write_text(KML_HEAD + body + KML_TAIL)

    # --- 2,500 small squares: exercises pagination in the UI -----------------
    cells = "".join(
        placemark(f"Cell {i}-{j}", polygon(ring(77.5 + i * 0.001, 12.9 + j * 0.001, 0.0005)))
        for i in range(50) for j in range(50)
    )
    (OUT / "many_features.kml").write_text(KML_HEAD + cells + KML_TAIL)


def make_geo_files() -> None:
    """Shapefile test files (need geopandas)."""
    import geopandas as gpd
    from shapely.geometry import LineString, Point, box

    def write_zip(zip_name: str, layers: dict, drop_prj: bool = False) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, gdf in layers.items():
                gdf.to_file(tmp / f"{name}.shp")
            with zipfile.ZipFile(OUT / zip_name, "w") as zf:
                for part in sorted(tmp.iterdir()):
                    if drop_prj and part.suffix == ".prj":
                        continue
                    zf.write(part, arcname=part.name)

    parcels = gpd.GeoDataFrame(
        {"plot_id": [1, 2], "owner": ["Asha", "Ravi"]},
        geometry=[box(77.5946, 12.9716, 77.5956, 12.9726), box(85.88, 20.46, 85.881, 20.461)],
        crs="EPSG:4326")
    roads = gpd.GeoDataFrame(
        {"road": ["A", "B"]},
        geometry=[LineString([(77.5946, 12.9716), (77.6046, 12.9716)]),
                  LineString([(85.88, 20.46), (85.89, 20.46)])],
        crs="EPSG:4326")
    points = gpd.GeoDataFrame(
        {"label": ["p1", "p2", "p3"]},
        geometry=[Point(77.6, 12.98), Point(85.9, 20.5), Point(72.88, 19.07)],
        crs="EPSG:4326")

    write_zip("parcels_wgs84.zip", {"parcels": parcels})
    write_zip("parcels_no_prj.zip", {"parcels": parcels}, drop_prj=True)
    write_zip("roads.zip", {"roads": roads})
    write_zip("points.zip", {"points": points})
    write_zip("two_layers_same_crs.zip", {"parcels": parcels, "roads": roads})
    write_zip("mixed_crs.zip", {"parcels": parcels, "roads_utm": roads.to_crs(32643)})


EXPECTED = """\
WHAT EACH FILE SHOULD DO   (numbers are approximate, about +/-1%)
==================================================================
Upload through the web page (/) or Swagger (/docs).

VALID FILES
  edge_cases.kml       201, 6 features
                        #0 Odisha plot      Polygon, ~11,550 m2, projected CRS EPSG:32645 (click it on the map)
                        #1 Plot w/ courtyard Polygon, ~9,700 m2 (hole subtracted)
                        #2 Bow-tie          status measured + warning "Invalid polygon was repaired", ~6,000 m2, EPSG:32643
                        #3 Mixed MultiGeom  status unsupported (handled gracefully, no crash)
                        #4 Bent road        LineString, ~2,150 m
                        #5 Survey point     not_applicable
  many_features.kml    201, 2500 features, total ~7.5 km2 (the table loads 3 pages of 1000 automatically)
  parcels_wgs84.zip    201, 2 polygons: ~12,017 m2 (EPSG:32643) and ~11,550 m2 (EPSG:32645), CRS EPSG:4326
  roads.zip            201, 2 lines: ~1,085.6 m and ~1,043 m
  points.zip           201, 3 points, all not_applicable
  two_layers_same_crs.zip  201, 4 features (2 polygons + 2 lines), each with a "layer" property
  parcels_no_prj.zip   with "Assume CRS" = EPSG:4326 -> 201 and CRS shown as (assumed)

FILES THAT MUST BE REJECTED (readable error, no crash)
  notes.txt            415 unsupported_type
  broken.zip           422 bad_zip
  empty.zip            422 no_shapefile
  no_shp.zip           422 no_shapefile
  zipslip.zip          422 unsafe_zip
  fake.kml             422 invalid_kml
  empty.kml            422 empty_file
  malformed.kml        422 (unreadable or no_features)
  no_features.kml      422 (no_features or unreadable)
  parcels_no_prj.zip   422 missing_crs   (when "Assume CRS" is left empty)
  parcels_no_prj.zip   422 bad_assume_crs  (when "Assume CRS" = EPSG:99999)
  mixed_crs.zip        422 mixed_crs

TOO LARGE (50 MB limit): create a 50 MB+ file in PowerShell, then upload it -> 413 file_too_large
  $f = [IO.File]::Create("samples\\test_files\\too_big.kml"); $f.SetLength(52428801); $f.Close()
"""


def main() -> None:
    make_plain_files()
    try:
        make_geo_files()
    except ImportError:
        print("geopandas not available: skipped the shapefile test files")
    (OUT / "README.txt").write_text(EXPECTED, encoding="utf-8")
    print(f"Created {len(list(OUT.iterdir()))} files in {OUT}")


if __name__ == "__main__":
    main()
