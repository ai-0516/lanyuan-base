"""tool_registry 单元测试

测试 @tool 装饰器的 schema 生成和执行逻辑。
"""

import json
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from fastapi.params import Depends as DependsClass
from pydantic import BaseModel

from app.harness.tool_registry import ToolDef, ToolRegistry, tool, registry


# ── 辅助 ──

class FakeModel(BaseModel):
    name: str
    age: int = 18
    tags: list[str] = []


async def _fake_db():
    return "db_session"


async def _fake_user():
    return 42


# ── 测试用 router 函数 ──


@tool
async def flat_params(
    page: int = 1,
    size: int = 20,
    db=DependsClass(_fake_db),
    user_id=DependsClass(_fake_user),
):
    """列出帖子"""
    return {"code": 0, "data": {"items": [], "page": page, "size": size}}


@tool
async def pydantic_param(
    post_id: int,
    data: FakeModel,
    db=DependsClass(_fake_db),
    user_id=DependsClass(_fake_user),
):
    """发布内容"""
    return {"code": 0, "data": {"id": 1, "name": data.name, "age": data.age}}


# ── Test Schema Generation ──


class TestSchemaGeneration:
    """验证自动生成的 JSON schema 是否正确"""

    def test_flat_params(self):
        """普通参数 + 排除 Depends"""
        td = registry.get("flat_params")
        assert td is not None

        fn = td.schema["function"]
        assert fn["name"] == "flat_params"
        assert fn["description"] == "列出帖子"

        props = fn["parameters"]["properties"]
        assert "page" in props
        assert props["page"]["type"] == "integer"
        assert "size" in props
        assert props["size"]["type"] == "integer"

        # Depends 参数不应出现在 schema 中
        assert "db" not in props
        assert "user_id" not in props

        # 有默认值的不在 required 中
        assert fn["parameters"]["required"] == []

    def test_pydantic_param_flattened(self):
        """Pydantic model 被展平为字段"""
        td = registry.get("pydantic_param")
        assert td is not None

        fn = td.schema["function"]
        assert fn["name"] == "pydantic_param"

        props = fn["parameters"]["properties"]
        assert "post_id" in props
        assert props["post_id"]["type"] == "integer"

        # Pydantic 字段被展平
        assert "name" in props
        assert props["name"]["type"] == "string"
        assert "age" in props
        assert props["age"]["type"] == "integer"
        assert "tags" in props
        assert props["tags"]["type"] == "array"

        # 不应出现 model 参数名 data
        assert "data" not in props

        # required 字段
        required = fn["parameters"]["required"]
        assert "post_id" in required
        assert "name" in required  # FakeModel.name 无默认值
        assert "age" not in required  # FakeModel.age 有默认值


# ── Test Execution ──


class TestExecution:
    """验证工具执行时的参数注入和返回值处理"""

    @pytest.mark.asyncio
    async def test_flat_params_execution(self):
        td = registry.get("flat_params")
        result = await td.execute(db="mock_db", user_id=99, args={"page": 3, "size": 10})
        data = json.loads(result)
        assert data["page"] == 3
        assert data["size"] == 10

    @pytest.mark.asyncio
    async def test_pydantic_param_execution(self):
        """Pydantic model 从展平参数重建"""
        td = registry.get("pydantic_param")
        result = await td.execute(
            db="mock_db",
            user_id=99,
            args={"post_id": 5, "name": "test", "age": 25, "tags": ["a", "b"]},
        )
        data = json.loads(result)
        assert data["id"] == 1
        assert data["name"] == "test"
        assert data["age"] == 25

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """不存在的工具返回错误信息"""
        result = await registry.execute("mock_db", 1, {
            "function": {"name": "not_exist", "arguments": "{}"}
        })
        assert "未知工具" in result

    @pytest.mark.asyncio
    async def test_bad_json_args(self):
        """参数格式错误返回友好提示"""
        result = await registry.execute("mock_db", 1, {
            "function": {"name": "flat_params", "arguments": "not-json"}
        })
        assert "参数解析失败" in result

    @pytest.mark.asyncio
    async def test_unwrap_api_success(self):
        """api_success 的 data 会被自动提取"""
        td = registry.get("flat_params")
        result = await td.execute(db="mock_db", user_id=1, args={"page": 1})
        data = json.loads(result)
        # data 字段被提取，不再有 code 包装
        assert "code" not in json.dumps(result)


# ── Test ToolRegistry ──


class TestToolRegistry:
    def test_all_and_to_openai(self):
        r = ToolRegistry()
        assert r.all == []
        assert r.to_openai_tools() == []

    def test_register_and_get(self):
        r = ToolRegistry()

        async def _fake_fn(db, user_id):
            """fake"""
            return {"code": 0, "data": "ok"}

        td = ToolDef("test_tool", "fake", _fake_fn)
        r.register(td)
        assert r.get("test_tool") is td
        assert r.get("nope") is None

        tools = r.to_openai_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "test_tool"
