"""Shared test helpers."""
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box


def make_shapefile_zip(tmp_path: Path, with_prj: bool = True) -> Path:
    """Write a one-polygon shapefile and zip its parts together."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
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
