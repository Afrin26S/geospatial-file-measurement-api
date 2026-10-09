import pytest

import app.api.files as files_module
from tests.conftest import SAMPLE_KML


def upload_kml(client, **params):
    return client.post(
        "/api/files/",
        params=params,
        files={"file": ("survey.kml", SAMPLE_KML.read_bytes(), "application/vnd.google-earth.kml+xml")},
    )


def test_upload_kml_and_get_file_info(client):
    response = upload_kml(client)
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"
    assert body["summary"]["features_by_type"] == {"Polygon": 1, "LineString": 1, "Point": 1}

    fetched = client.get(f"/api/files/{body['id']}/")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert fetched.json()["status"] == "COMPLETED"


def test_measurements_for_each_feature(client):
    file_id = upload_kml(client).json()["id"]
    body = client.get(f"/api/files/{file_id}/measurements/").json()

    assert body["total"] == 3
    assert body["area_unit"] == "sq_m" and body["length_unit"] == "m"
    polygon, line, point = body["features"]

    assert polygon["geometry_type"] == "Polygon"
    assert polygon["geometry"]["type"] == "Polygon"
    assert polygon["crs"] == "EPSG:4326"
    assert "Square plot" in polygon["properties"].values()
    assert polygon["measurement"]["projected_crs"] == "EPSG:32643"
    assert polygon["measurement"]["area"] == pytest.approx(12000, rel=0.01)

    assert line["measurement"]["length"] == pytest.approx(1085, rel=0.01)

    assert point["measurement"]["status"] == "not_applicable"
    assert point["measurement"]["area"] is None


def test_unit_conversion(client):
    file_id = upload_kml(client).json()["id"]
    body = client.get(
        f"/api/files/{file_id}/measurements/", params={"area_unit": "ha", "length_unit": "km"}
    ).json()
    assert body["area_unit"] == "ha" and body["length_unit"] == "km"
    assert body["features"][0]["measurement"]["area"] == pytest.approx(1.2, rel=0.01)
    assert body["features"][1]["measurement"]["length"] == pytest.approx(1.085, rel=0.01)


def test_invalid_unit_is_rejected(client):
    file_id = upload_kml(client).json()["id"]
    response = client.get(f"/api/files/{file_id}/measurements/", params={"area_unit": "bigha"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_pagination(client):
    file_id = upload_kml(client).json()["id"]
    body = client.get(
        f"/api/files/{file_id}/measurements/", params={"limit": 1, "offset": 1}
    ).json()
    assert body["total"] == 3
    assert len(body["features"]) == 1
    assert body["features"][0]["feature_index"] == 1


def test_geometry_can_be_left_out(client):
    file_id = upload_kml(client).json()["id"]
    body = client.get(
        f"/api/files/{file_id}/measurements/", params={"include_geometry": "false"}
    ).json()
    assert all(f["geometry"] is None for f in body["features"])


def test_upload_shapefile_zip(client, make_shapefile_zip):
    zip_path = make_shapefile_zip()
    response = client.post(
        "/api/files/", files={"file": ("parcels.zip", zip_path.read_bytes(), "application/zip")}
    )
    assert response.status_code == 201
    file_id = response.json()["id"]

    feature = client.get(f"/api/files/{file_id}/measurements/").json()["features"][0]
    assert feature["properties"]["plot_id"] == 7
    assert feature["measurement"]["area"] == pytest.approx(12000, rel=0.01)


def test_shapefile_without_prj_needs_assume_crs(client, make_shapefile_zip):
    payload = make_shapefile_zip(with_prj=False).read_bytes()
    files = {"file": ("parcels.zip", payload, "application/zip")}

    rejected = client.post("/api/files/", files=files)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "missing_crs"

    accepted = client.post("/api/files/", files=files, params={"assume_crs": "EPSG:4326"})
    assert accepted.status_code == 201
    assert accepted.json()["crs_assumed"] is True


def test_unsupported_file_type(client):
    response = client.post("/api/files/", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 415
    error = response.json()["error"]
    assert error["code"] == "unsupported_type"
    assert "file_id" not in error


def test_corrupt_zip_is_recorded_as_failed(client):
    response = client.post(
        "/api/files/", files={"file": ("broken.zip", b"not really a zip", "application/zip")}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "bad_zip"

    info = client.get(f"/api/files/{error['file_id']}/").json()
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "bad_zip"

    measurements = client.get(f"/api/files/{error['file_id']}/measurements/")
    assert measurements.status_code == 409
    assert measurements.json()["error"]["code"] == "file_not_ready"


def test_file_too_large(client, monkeypatch):
    monkeypatch.setattr(files_module, "MAX_UPLOAD_BYTES", 10)
    response = upload_kml(client)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_empty_file(client):
    response = client.post("/api/files/", files={"file": ("empty.kml", b"", "text/plain")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_file"


def test_missing_file_part(client):
    response = client.post("/api/files/")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_id_returns_404(client):
    for path in ("/api/files/nope/", "/api/files/nope/measurements/"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
