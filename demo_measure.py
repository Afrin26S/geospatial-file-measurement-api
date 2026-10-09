"""Step 2 demo: load a file with explore.load() and measure every feature.

Usage:
    python demo_measure.py samples\\test_bengaluru.kml
"""
import sys
from dataclasses import asdict
from pathlib import Path
from pprint import pprint

from app.services.measure import measure_geometry
from explore import load


def main(path_str: str) -> None:
    gdf = load(Path(path_str))
    print("File CRS:", gdf.crs)
    for index, row in gdf.iterrows():
        print(f"\n--- feature {index} ---")
        pprint(asdict(measure_geometry(row.geometry, gdf.crs)), sort_dicts=False)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
