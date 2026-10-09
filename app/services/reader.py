"""Reading uploaded files safely: validation, zip handling, KML / shapefile loading.

Everything that can go wrong with an untrusted upload is handled here and
reported as a FileReadError with a short machine-readable `code`, so the API
layer can turn it into a clean HTTP error.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath

import geopandas as gpd
import pandas as pd
import pyogrio
from pyproj import CRS

# --- limits (protect the server from huge or malicious uploads) --------------
MAX_UPLOAD_BYTES = 50 * 1024 * 1024            # size of the uploaded file itself
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024     # total size after unzipping
MAX_ZIP_MEMBERS = 200

# Only these parts of a shapefile are ever extracted from a zip.
SHAPEFILE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}


class FileReadError(Exception):
    """The uploaded file cannot be used. `code` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class LoadedFile:
    kind: str                    # "shapefile" or "kml"
    gdf: gpd.GeoDataFrame
    crs: CRS
    crs_assumed: bool = False    # True if the caller supplied the CRS


# --- zip handling -------------------------------------------------------------
def _copy_limited(src, dst, budget: int) -> int:
    """Copy src to dst, never writing more than `budget` bytes.

    The sizes stored in a zip header can lie (zip bomb), so we count the
    bytes we actually write.
    """
    written = 0
    while chunk := src.read(64 * 1024):
        written += len(chunk)
        if written > budget:
            raise FileReadError("zip_too_large", "Zip expands to more than the allowed size")
        dst.write(chunk)
    return written


def _extract_shapefile_parts(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract only shapefile parts, refusing paths that escape `dest` (zip-slip)."""
    members = zf.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise FileReadError("zip_too_many_files", "Zip contains too many files")

    dest = dest.resolve()
    remaining = MAX_UNCOMPRESSED_BYTES

    for info in members:
        if info.is_dir():
            continue
        parts = PurePosixPath(info.filename.replace("\\", "/")).parts
        if any(p.startswith("__MACOSX") for p in parts) or parts[-1].startswith("._"):
            continue  # macOS junk
        if PurePosixPath(info.filename).suffix.lower() not in SHAPEFILE_PARTS:
            continue  # ignore everything that is not a shapefile part

        target = (dest / info.filename).resolve()
        if target != dest and dest not in target.parents:
            raise FileReadError("unsafe_zip", "Zip contains an unsafe file path")

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            remaining -= _copy_limited(src, out, remaining)


def _read_shapefile_zip(path: Path) -> gpd.GeoDataFrame:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise FileReadError("bad_zip", "File is not a valid zip archive") from exc

    with zf, tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _extract_shapefile_parts(zf, tmp_dir)

        shp_files = sorted(tmp_dir.rglob("*.shp"))
        if not shp_files:
            raise FileReadError("no_shapefile", "Zip does not contain a .shp file")

        frames = []
        for shp in shp_files:
            try:
                frame = gpd.read_file(shp)
            except Exception as exc:
                raise FileReadError("unreadable", f"Could not read {shp.name}: {exc}") from exc
            frame["layer"] = shp.stem
            frames.append(frame)

    if len({str(f.crs) for f in frames}) > 1:
        raise FileReadError(
            "mixed_crs", "The shapefiles in the zip use different coordinate systems"
        )
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


# --- KML handling -------------------------------------------------------------
def _read_kml(path: Path) -> gpd.GeoDataFrame:
    with open(path, "rb") as fh:
        head = fh.read(4096).decode("utf-8", errors="ignore").lower()
    if "<kml" not in head:
        raise FileReadError(
            "invalid_kml", "File does not look like KML (KMZ is not supported, use .kml)"
        )

    try:
        layers = pyogrio.list_layers(path)
        frames = []
        # A KML folder shows up as a separate layer, so read every layer.
        for name, _geometry_type in layers:
            frame = gpd.read_file(path, layer=name)
            if len(frame):
                frame["layer"] = name
                frames.append(frame)
    except Exception as exc:
        raise FileReadError("unreadable", f"Could not parse KML: {exc}") from exc

    if not frames:
        raise FileReadError("no_features", "KML contains no features")
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


# --- public entry point -------------------------------------------------------
def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return "shapefile"
    if suffix == ".kml":
        return "kml"
    raise FileReadError("unsupported_type", "Only .zip (shapefile) and .kml files are supported")


def read_geofile(path: Path, assume_crs: str | None = None) -> LoadedFile:
    """Load an uploaded .zip shapefile or .kml into a GeoDataFrame."""
    kind = detect_kind(path)
    gdf = _read_shapefile_zip(path) if kind == "shapefile" else _read_kml(path)

    if len(gdf) == 0:
        raise FileReadError("no_features", "File contains no features")

    assumed = False
    if gdf.crs is None:
        if assume_crs is None:
            raise FileReadError(
                "missing_crs",
                "The file has no coordinate system (shapefile without .prj?). "
                "Pass assume_crs, e.g. EPSG:4326, if you know it.",
            )
        try:
            gdf = gdf.set_crs(assume_crs)
        except Exception as exc:
            raise FileReadError("bad_assume_crs", f"Unknown CRS: {assume_crs}") from exc
        assumed = True

    return LoadedFile(kind=kind, gdf=gdf, crs=CRS.from_user_input(gdf.crs), crs_assumed=assumed)


# --- attributes -> JSON-safe dicts -------------------------------------------
def clean_properties(gdf: gpd.GeoDataFrame) -> list[dict]:
    """One dict per feature with empty/NaN values dropped and JSON-safe types.

    KML files produce many empty columns (timestamp, drawOrder, icon ...) and
    NaN cannot be written as JSON, so both are removed here.
    """
    columns = [c for c in gdf.columns if c != gdf.geometry.name]
    result = []
    for _, row in gdf[columns].iterrows():
        props = {}
        for key, value in row.items():
            if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NaT:
                continue
            if isinstance(value, str) and value == "":
                continue
            if hasattr(value, "item"):             # numpy scalar -> python scalar
                value = value.item()
            if isinstance(value, (datetime, date, pd.Timestamp)):
                value = value.isoformat()
            if not isinstance(value, (str, int, float, bool)):
                value = str(value)
            props[str(key)] = value
        result.append(props)
    return result
