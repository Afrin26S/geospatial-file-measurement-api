"""Step 1: load a zipped shapefile or a KML and print what is inside.

Usage:
    python explore.py path/to/survey.kml
    python explore.py path/to/parcels.zip
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio


def load(path: Path) -> gpd.GeoDataFrame:
    suffix = path.suffix.lower()

    if suffix == ".zip":
        # GDAL can read straight from inside a zip; no manual unzipping needed.
        try:
            return gpd.read_file(path)
        except Exception:
            return gpd.read_file(f"zip://{path.resolve()}")

    if suffix == ".kml":
        # A KML can hold several folders; GDAL exposes each as a "layer".
        # read_file() alone would only read the first one, so read them all.
        layers = pyogrio.list_layers(path)
        frames = []
        for name, _geometry_type in layers:
            frame = gpd.read_file(path, layer=name)
            frame["layer"] = name
            frames.append(frame)
        if not frames:
            raise ValueError("KML contains no layers")
        return pd.concat(frames, ignore_index=True)

    raise ValueError(f"Unsupported file type: {suffix}")


def main(path_str: str) -> None:
    path = Path(path_str)

    drivers = pyogrio.list_drivers()
    print("KML driver available:", "KML" in drivers or "LIBKML" in drivers)

    gdf = load(path)

    print("File:", path.name)
    print("CRS:", gdf.crs)                       # None means no CRS was found
    print("Feature count:", len(gdf))
    print("Columns:", [c for c in gdf.columns if c != "geometry"])
    print()

    for index, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            print(index, "-> no geometry")
            continue
        print(
            index,
            geom.geom_type,
            "| empty" if geom.is_empty else "",
            "| valid" if geom.is_valid else "| INVALID",
            "| bounds:", [round(v, 6) for v in geom.bounds] if not geom.is_empty else None,
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
