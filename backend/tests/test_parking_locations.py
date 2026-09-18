"""停车地图位置 API / 服务测试。"""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.parking import ParkingLocationEntityType
from app.main import app
from app.services import parking_location_service

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_spot_location_comes_from_map_and_has_landmarks():
    result = parking_location_service.search("B194")
    assert result["status"] == "found"
    spot = result["results"][0]
    assert spot["entity_type"] == "spot"
    assert spot["id"] == "B194"
    assert spot["area"] == "B区"
    assert spot["coordinate_system"]["origin"] == "top_left"
    assert spot["center"]["x"] == pytest.approx(2166.39, abs=0.02)
    assert spot["center"]["y"] == pytest.approx(1209.71, abs=0.02)
    assert spot["bounds"] == {"left": 2153.89, "top": 1182.71, "width": 25.0, "height": 54.0}
    assert len(spot["nearest_buildings"]) == 3
    assert spot["nearest_gate"]["id"] in {"西门", "北门", "东门"}


@pytest.mark.parametrize("query", ["6#", "6号楼", "6栋"])
def test_building_query_aliases(query):
    result = parking_location_service.search(query, ParkingLocationEntityType.BUILDING)
    assert result["status"] == "found"
    assert result["results"][0]["id"] == "6#"
    assert result["results"][0]["entity_type"] == "building"


def test_gate_location():
    result = parking_location_service.search("西门出入口", ParkingLocationEntityType.GATE)
    assert result["status"] == "found"
    assert result["results"][0]["id"] == "西门"


@pytest.mark.parametrize(
    ("query", "verified"),
    [("W002", True), ("w010", True), ("W999", False)],
)
def test_w_spot_never_infers_coordinates(query, verified):
    result = parking_location_service.search(query)
    assert result["status"] == "coordinates_unavailable"
    assert result["results"][0]["verified_id"] is verified
    assert result["results"][0]["coordinates_available"] is False
    assert "center" not in result["results"][0]


def test_unknown_location_is_not_found():
    result = parking_location_service.search("不存在的入口")
    assert result["status"] == "not_found"
    assert result["results"] == []


@pytest.mark.asyncio
async def test_http_api_is_public_and_returns_standard_response():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/parking-locations/search", params={"query": "北门"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["status"] == "found"
    assert payload["data"]["results"][0]["entity_type"] == "gate"


def test_parking_json_is_included_in_runtime_image_without_map_image():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    source = "docs/parking/parking_spots_buildings_gates_roads.json"
    assert f"COPY {source}" in dockerfile
    assert f"!{source}" in dockerignore
    assert "COPY docs/parking/stitched_final.png" not in dockerfile
