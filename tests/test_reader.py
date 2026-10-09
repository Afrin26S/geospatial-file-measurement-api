import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from app.services.reader import (
    FileReadError,
    clean_properties,
    read_geofile,
)

SAMPLE_KML = Path(__file__).resolve().parent.parent / "samples" / "test_bengaluru.kml"


def make_shapefile_zip(tmp_path: Path, with_prj: bool = True) -> Path:
    """Write a one-polygon shapefile and zip its parts together."""
    src = tmp_path / "src"
    src.mkdir()
    gdf = gpd.GeoDataFrame(
        {"plot_id": [7], "owner": ["Asha"]},
        geometry=[box(77.5946, 12.9716, 77.5956, 12.9726)],
        crs="EPSG:4326",
    )
    gdf.to_file(src / "parcels.shp")

    zip_path = tmp_path / "parcels.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for part in src.iterdir():
            if not with_prj and part.suffix == ".prj":
                continue
            zf.write(part, arcname=part.name)
    return zip_path


def test_reads_shapefile_zip(tmp_path):
    loaded = read_geofile(make_shapefile_zip(tmp_path))
    assert loaded.kind == "shapefile"
    assert len(loaded.gdf) == 1
    assert loaded.crs.to_epsg() == 4326
    assert loaded.crs_assumed is False


def test_reads_kml():
    loaded = read_geofile(SAMPLE_KML)
    assert loaded.kind == "kml"
    assert len(loaded.gdf) == 3
    assert loaded.crs.to_epsg() == 4326


def test_missing_prj_needs_assume_crs(tmp_path):
    zip_path = make_shapefile_zip(tmp_path, with_prj=False)
    with pytest.raises(FileReadError) as err:
        read_geofile(zip_path)
    assert err.value.code == "missing_crs"

    loaded = read_geofile(zip_path, assume_crs="EPSG:4326")
    assert loaded.crs_assumed is True


def test_zip_without_shp(tmp_path):
    bad = tmp_path / "empty.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("notes.txt", "hello")
    with pytest.raises(FileReadError) as err:
        read_geofile(bad)
    assert err.value.code == "no_shapefile"


def test_corrupt_zip(tmp_path):
    bad = tmp_path / "broken.zip"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(FileReadError) as err:
        read_geofile(bad)
    assert err.value.code == "bad_zip"


def test_zip_slip_is_rejected(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../evil.shp", b"x")
    with pytest.raises(FileReadError) as err:
        read_geofile(evil)
    assert err.value.code == "unsafe_zip"
    assert not (tmp_path / "evil.shp").exists()


def test_kml_must_look_like_kml(tmp_path):
    fake = tmp_path / "fake.kml"
    fake.write_text("just some text, not xml")
    with pytest.raises(FileReadError) as err:
        read_geofile(fake)
    assert err.value.code == "invalid_kml"


def test_unsupported_extension(tmp_path):
    other = tmp_path / "data.txt"
    other.write_text("hello")
    with pytest.raises(FileReadError) as err:
        read_geofile(other)
    assert err.value.code == "unsupported_type"


def test_clean_properties_drops_empty_kml_columns():
    loaded = read_geofile(SAMPLE_KML)
    props = clean_properties(loaded.gdf)
    assert len(props) == 3
    assert "Square plot" in props[0].values()
    assert "timestamp" not in props[0]          # empty KML column removed
    assert all(isinstance(v, (str, int, float, bool)) for v in props[0].values())


def test_clean_properties_keeps_shapefile_attributes(tmp_path):
    loaded = read_geofile(make_shapefile_zip(tmp_path))
    props = clean_properties(loaded.gdf)[0]
    assert props["plot_id"] == 7
    assert props["owner"] == "Asha"
