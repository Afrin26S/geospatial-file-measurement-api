import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base, get_db
from app.main import app

SAMPLE_KML = Path(__file__).resolve().parent.parent / "samples" / "test_bengaluru.kml"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """API client wired to a throwaway database and upload folder."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")

    yield TestClient(app)  # no `with`: skips the lifespan, so the real DB is never touched

    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def make_shapefile_zip(tmp_path):
    """Factory: build a zipped one-polygon shapefile on the fly."""

    def _make(with_prj: bool = True) -> Path:
        src = tmp_path / "shp_src"
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

    return _make
