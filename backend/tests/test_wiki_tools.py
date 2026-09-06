"""小区知识库 wiki 只读工具测试（issue #103 二期：AI 回答链路）

覆盖：
1. v1/v2 双注册（@tool → registry、@mcp_tool → MCP）
2. wiki_index：列出知识页（真实 docs/wiki，供热条例 6 页）
3. wiki_read：读整页完整 markdown、缺失页 → None（业务失败≠系统异常）、
   slug 白名单防路径穿越
4. formatter：无删减、JSON 结构原样返回
"""

import json
import os

import pytest

from app.api.v1 import wiki as wiki_mod
from app.harness.tool_registry import registry
from tools.mcp_server.decorator import _REGISTERED_TOOLS

# 知识库真实位置（测试在 backend/ 跑 → 仓库根 docs/wiki）
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EXPECTED_PAGES = [
    "heating-fee",
    "heating-refund",
    "heating-responsibility",
    "heating-season-and-standard",
    "heating-user-rules",
    "heating-warranty-transfer",
]


class TestRegistration:
    """v1 @tool registry + v2 @mcp_tool 双通道注册"""

    def test_v1_registry_has_wiki_tools(self):
        assert registry.get("wiki_index") is not None
        assert registry.get("wiki_read") is not None

    def test_v2_mcp_has_wiki_tools(self):
        assert "wiki_index" in _REGISTERED_TOOLS
        assert "wiki_read" in _REGISTERED_TOOLS

    def test_no_db_dependency_in_schema(self):
        """纯文件读取：schema 不含 db/user_id（无 Depends 注入参数）"""
        td = registry.get("wiki_read")
        props = td.schema["function"]["parameters"]["properties"]
        assert "db" not in props
        assert "user_id" not in props
        assert "page" in props, "wiki_read 应暴露 page 业务参数"


class TestWikiIndex:
    @pytest.mark.asyncio
    async def test_lists_all_pages(self):
        result = await wiki_mod.wiki_index()
        slugs = [p["slug"] for p in result["pages"]]
        assert slugs == EXPECTED_PAGES, f"页面清单应与 docs/wiki/pages 一致: {slugs}"
        assert result["total"] == len(EXPECTED_PAGES)

    @pytest.mark.asyncio
    async def test_pages_have_titles(self):
        result = await wiki_mod.wiki_index()
        for p in result["pages"]:
            assert p["title"], f"{p['slug']} 应有 title（frontmatter）"
        titles = {p["slug"]: p["title"] for p in result["pages"]}
        assert "退费" in titles["heating-refund"], f"中文 title 正确: {titles['heating-refund']}"

    def test_index_formatter_is_dumps(self):
        """formatter 无删减：JSON 结构原样"""
        data = {"pages": [{"slug": "a", "title": "甲"}], "total": 1}
        out = wiki_mod._format_wiki_index(data)
        assert json.loads(out) == data


class TestWikiRead:
    @pytest.mark.asyncio
    async def test_reads_full_page(self):
        result = await wiki_mod.wiki_read("heating-refund")
        assert result is not None
        assert result["slug"] == "heating-refund"
        content = result["content"]
        assert content.startswith("---"), "应含 frontmatter（溯源）"
        assert "温度不达标退费规则" in content, "应含页面标题"
        assert "退 20%" in content and "退 50%" in content, "应含退费阶梯完整内容"
        assert "第 28 条" in content or "第28条" in content.replace(" ", ""), "应含依据条款"

    @pytest.mark.asyncio
    async def test_missing_page_returns_none(self):
        """业务失败≠系统异常：查无此页 = 正常 null（LLM 告知未登记，不报错）"""
        assert await wiki_mod.wiki_read("nonexistent-page") is None

    @pytest.mark.asyncio
    async def test_empty_slug_returns_none(self):
        assert await wiki_mod.wiki_read("") is None

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self):
        """slug 白名单：防 ../ 穿越到 wiki 外"""
        for bad in ("../secret", "..%2Fetc%2Fpasswd", "a/b", "heating-refund.md", ".hidden"):
            assert await wiki_mod.wiki_read(bad) is None, f"应拦截: {bad}"

    @pytest.mark.asyncio
    async def test_formatter_null_and_data(self):
        assert wiki_mod._format_wiki_read(None) == "null"
        data = {"slug": "x", "content": "# 标题\n正文"}
        assert json.loads(wiki_mod._format_wiki_read(data)) == data


class TestHttpStyleExecute:
    """经 v1 ToolDef.execute 全链路（模拟 registry 分发）"""

    @pytest.mark.asyncio
    async def test_execute_wiki_index(self):
        td = registry.get("wiki_index")
        out = await td.execute(db=None, user_id=1, args={})
        parsed = json.loads(out)
        assert parsed["total"] == len(EXPECTED_PAGES)

    @pytest.mark.asyncio
    async def test_execute_wiki_read(self):
        td = registry.get("wiki_read")
        out = await td.execute(db=None, user_id=1, args={"page": "heating-fee"})
        parsed = json.loads(out)
        assert parsed["slug"] == "heating-fee"
        assert "政府定价" in parsed["content"]

    @pytest.mark.asyncio
    async def test_execute_wiki_read_missing(self):
        td = registry.get("wiki_read")
        out = await td.execute(db=None, user_id=1, args={"page": "nope"})
        assert out == "null", "缺失页经 execute → null 字符串（LLM 读「无此页」）"
