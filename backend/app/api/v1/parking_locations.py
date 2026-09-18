"""停车场车位、楼栋和出入口位置查询 API。"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.response import api_success
from app.core.parking import ParkingLocationEntityType
from app.harness.tool_registry import dumps, tool
from app.services import parking_location_service
from tools.mcp_server.decorator import mcp_tool

router = APIRouter(prefix="/parking-locations", tags=["停车位置"])


def _format_get_parking_location(data) -> str:
    """无删减：坐标供 LLM 内部定位推理，工具说明约束其不得向用户直接展示。"""
    return dumps(data)


@router.get("/search")
@mcp_tool(name="get_parking_location", result_formatter=_format_get_parking_location)
@tool(name="get_parking_location", result_formatter=_format_get_parking_location)
async def get_parking_location(
    query: Annotated[str, Query(min_length=1, max_length=32)],
    entity_type: ParkingLocationEntityType | None = None,
):
    """查询兰园停车场内车位、楼栋或出入口的权威地图位置。

    用户询问具体车位在哪里、某栋楼或停车场出入口的位置时，必须优先调用本工具，
    不要使用 Wiki 中物业出租公告的大致方位代替地图数据。query 传准确车位号
    （如 B194）、楼栋号（如 6#、6号楼）或出入口名称（西门、北门、东门）；
    entity_type 可选 spot、building、gate，用于限定实体类型。返回坐标、区域、地图方位、
    车位方向及附近地标。坐标和像素距离仅供你内部定位与比较；最终给用户的回答必须转换
    为自然语言，只描述区域、附近楼栋、出入口和相对方位，绝对不能展示任何像素坐标或
    像素距离。W 开头的车位未录入地图，本工具会明确返回无精确坐标，禁止推测。
    """
    return api_success(parking_location_service.search(query, entity_type))
