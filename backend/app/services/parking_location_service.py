"""停车地图位置查询服务。

位置数据唯一来源是 docs/parking/parking_spots_buildings_gates_roads.json。
Wiki 中的物业出租公告不参与位置查询。
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from app.core.parking import ParkingLocationEntityType

_DATA_FILENAME = "parking_spots_buildings_gates_roads.json"
_IMAGE_WIDTH = 3370
_IMAGE_HEIGHT = 4299
_KNOWN_W_SPOTS = frozenset({"W002", "W005", "W009", "W010"})
_W_SPOT_PATTERN = re.compile(r"^W\d{3}$")
_BUILDING_PATTERN = re.compile(r"^(\d{1,2})(?:#|号楼|栋|楼)?$")


@lru_cache(maxsize=1)
def _load_data() -> dict:
    """本地从仓库 docs/ 读取，容器从 /app/docs/ 读取；进程内只加载一次。"""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "parking" / _DATA_FILENAME
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"未找到停车地图数据文件 {_DATA_FILENAME}")


def _normalize_query(query: str, entity_type: ParkingLocationEntityType | None) -> str:
    value = query.strip().replace(" ", "")
    if entity_type == ParkingLocationEntityType.SPOT or re.fullmatch(r"[A-Za-z]\d{3}", value):
        return value.upper()
    building = _BUILDING_PATTERN.fullmatch(value)
    if building:
        return f"{int(building.group(1))}#"
    if entity_type == ParkingLocationEntityType.GATE:
        for suffix in ("停车场出入口", "出入口", "入口", "出口"):
            value = value.removesuffix(suffix)
    return value


def _center(item: dict) -> tuple[float, float]:
    return item["x"] + item["w"] / 2, item["y"] + item["h"] / 2


def _nearest(center_x: float, center_y: float, source_key: str, count: int) -> list[dict]:
    candidates = []
    for item in _load_data()[source_key]:
        item_x, item_y = _center(item)
        candidates.append({
            "id": item["id"],
            "title": f'{item["id"]}楼' if source_key == "buildings" else item["id"],
            "distance_pixels": round(math.hypot(item_x - center_x, item_y - center_y), 1),
        })
    return sorted(candidates, key=lambda item: item["distance_pixels"])[:count]


def _base_result(entity_type: str, item_id: str, title: str, subtitle: str,
                 center_x: float, center_y: float) -> dict:
    horizontal = "西部" if center_x < _IMAGE_WIDTH / 3 else "东部" if center_x > _IMAGE_WIDTH * 2 / 3 else ""
    vertical = "北部" if center_y < _IMAGE_HEIGHT / 3 else "南部" if center_y > _IMAGE_HEIGHT * 2 / 3 else "中部"
    return {
        "entity_type": entity_type,
        "id": item_id,
        "title": title,
        "subtitle": subtitle,
        "coordinates_available": True,
        "map_region": f"地图{horizontal}{vertical}",
        "coordinate_system": {
            "origin": "top_left",
            "units": "pixels",
            "image_width": _IMAGE_WIDTH,
            "image_height": _IMAGE_HEIGHT,
        },
        "center": {
            "x": round(center_x, 2),
            "y": round(center_y, 2),
            "normalized_x": round(center_x / _IMAGE_WIDTH, 6),
            "normalized_y": round(center_y / _IMAGE_HEIGHT, 6),
        },
    }


def _serialize_spot(item: dict) -> dict:
    width = item["rect_h"] if item["landscape"] else item["rect_w"]
    height = item["rect_w"] if item["landscape"] else item["rect_h"]
    center_x = item["stitched_x"] + width / 2
    center_y = item["stitched_y"] + height / 2
    result = _base_result(
        "spot", item["id"], item["id"], f'{item["area"]} · {item["num"]}号车位',
        center_x, center_y,
    )
    result.update({
        "area": item["area"],
        "orientation": "horizontal" if item["landscape"] else "vertical",
        "bounds": {
            "left": item["stitched_x"],
            "top": item["stitched_y"],
            "width": width,
            "height": height,
        },
        "nearest_buildings": _nearest(center_x, center_y, "buildings", 3),
        "nearest_gate": _nearest(center_x, center_y, "gates", 1)[0],
    })
    return result


def _serialize_landmark(item: dict, entity_type: ParkingLocationEntityType) -> dict:
    center_x, center_y = _center(item)
    is_building = entity_type == ParkingLocationEntityType.BUILDING
    return _base_result(
        entity_type.value,
        item["id"],
        f'{item["id"]}楼' if is_building else item["id"],
        "楼栋" if is_building else "停车场出入口",
        center_x,
        center_y,
    )


def search(query: str, entity_type: ParkingLocationEntityType | None = None) -> dict:
    """按准确编号或名称查询位置，不使用 Wiki 文本补全或推测。"""
    normalized = _normalize_query(query, entity_type)

    if _W_SPOT_PATTERN.fullmatch(normalized):
        verified = normalized in _KNOWN_W_SPOTS
        return {
            "query": query,
            "normalized_query": normalized,
            "status": "coordinates_unavailable",
            "results": [{
                "entity_type": "spot",
                "id": normalized,
                "verified_id": verified,
                "coordinates_available": False,
            }],
            "message": (
                "该 W 编号车位已在物业历史公告中出现，但尚未录入停车地图，"
                "没有精确坐标。" if verified else
                "W 编号车位尚未录入停车地图，无法确认该编号是否存在，也没有精确坐标。"
            ),
        }

    matches = []
    data = _load_data()
    source_types = (
        (ParkingLocationEntityType.SPOT, "spots"),
        (ParkingLocationEntityType.BUILDING, "buildings"),
        (ParkingLocationEntityType.GATE, "gates"),
    )
    for source_type, source_key in source_types:
        if entity_type is not None and source_type != entity_type:
            continue
        for item in data[source_key]:
            if item["id"].upper() != normalized.upper():
                continue
            matches.append(
                _serialize_spot(item)
                if source_type == ParkingLocationEntityType.SPOT
                else _serialize_landmark(item, source_type)
            )

    if not matches:
        return {
            "query": query,
            "normalized_query": normalized,
            "status": "not_found",
            "results": [],
            "message": "停车地图中未找到该车位、楼栋或出入口，请核对名称后重试。",
        }
    return {
        "query": query,
        "normalized_query": normalized,
        "status": "found",
        "results": matches,
        "message": "位置来自停车地图数据；不得用 Wiki 公告中的文字方位覆盖或修正。",
    }
