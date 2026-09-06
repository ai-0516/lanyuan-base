"""小区知识库 wiki 只读工具（issue #103 二期：AI 回答链路接线）

知识源 = 仓库 docs/wiki/ 下的 markdown（供热条例等法规已编译入库，随 Docker
镜像内置——容器无持久磁盘，镜像内置即持久；更新走「改 md → PR → 部署」）。

两个工具（对应 wiki 查询模式：先看有哪些页 → 整页读入）：
- wiki_index: 列出知识页清单（slug + title），AI 判断哪个页覆盖用户问题
- wiki_read: 读指定页完整 markdown（含 frontmatter 溯源信息），AI 据此作答

双注册（v1 @tool → registry / v2 @mcp_tool → MCP 桥，v1/v2 两个 AI 都能用）：
- 纯文件系统读取，不依赖 db/user_id（知识对登录用户开放，无私有数据）
- 页面不存在 → api_success(None)（业务失败≠系统异常：查无此页是正常查询结果，
  不是错误——LLM 据此告知用户「我还没登记这项信息」而非报错）

路径定位：env WIKI_DIR 显式指定（Dockerfile ENV WIKI_DIR=/app/docs/wiki，
云托管可配）；未配置时向上查找含 docs/wiki/ 的仓库根（本机开发用）。
"""

import logging
import os
from pathlib import Path

from app.harness.tool_registry import dumps, tool
from tools.mcp_server.decorator import mcp_tool

logger = logging.getLogger(__name__)

# 页面目录名（相对 wiki 根）
_PAGES_DIR = "pages"
_MAX_PAGE_CHARS = 30_000  # 单页超长保护（防 payload 爆炸；知识页正常远小于此）


def _wiki_root() -> Path:
    """定位 wiki 根目录：env WIKI_DIR 优先（容器 /app/docs/wiki），
    否则从本文件向上找含 docs/wiki/ 的仓库根（本机 backend/ 下开发）。"""
    env = os.getenv("WIKI_DIR")
    if env:
        return Path(env)
    # backend/app/api/v1/wiki.py → parents 向上找含 docs/wiki/pages 的目录
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "wiki"
        if (candidate / _PAGES_DIR).is_dir():
            return candidate
    raise FileNotFoundError("未找到 docs/wiki/ 目录：请设置 WIKI_DIR 或在仓库根运行")


def _list_pages() -> list[dict]:
    """扫描 pages/ 下知识页：slug（文件名）+ title（frontmatter）"""
    pages_dir = _wiki_root() / _PAGES_DIR
    result = []
    for md in sorted(pages_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        title = ""
        if text.startswith("---"):
            for line in text.splitlines()[1:]:
                if line == "---":
                    break
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip("'\"")
                    break
        result.append({"slug": md.stem, "title": title or md.stem})
    return result


def _read_page(page: str) -> dict | None:
    """读取单个知识页完整 markdown（slug 白名单防路径穿越）"""
    # slug 白名单：仅字母/数字/连字符（对齐 wiki 文件名约定），防 ../ 穿越
    if not page or not all(c.isalnum() or c == "-" for c in page):
        return None
    pages_dir = _wiki_root() / _PAGES_DIR
    md = pages_dir / f"{page}.md"
    if not md.is_file():
        return None
    text = md.read_text(encoding="utf-8")
    if len(text) > _MAX_PAGE_CHARS:
        text = text[:_MAX_PAGE_CHARS] + "\n…(页面过长已截断)"
    return {"slug": page, "content": text}


def _format_wiki_index(data: dict) -> str:
    """无删减：知识页清单原样返回（slug/title 语义清晰，AI 需要全量判断）"""
    return dumps(data)


@mcp_tool(result_formatter=_format_wiki_index)
@tool(result_formatter=_format_wiki_index)
async def wiki_index() -> dict:
    """列出小区知识库中所有知识页（slug + title）。当用户询问小区相关事实
    （建成年代、供热规定、物业事项等）时，先调用本工具查看有哪些页面，
    再调用 wiki_read 读取对应页面内容。返回 pages 数组：slug 是页面标识
    （传给 wiki_read 的 page 参数），title 是页面标题。"""
    pages = _list_pages()
    return {"pages": pages, "total": len(pages)}


def _format_wiki_read(data: dict | None) -> str:
    """无删减：知识页完整 markdown 原样返回（LLM 需要读全文作答；None = 无此页）"""
    return dumps(data)


@mcp_tool(result_formatter=_format_wiki_read)
@tool(result_formatter=_format_wiki_read)
async def wiki_read(page: str) -> dict | None:
    """读取小区知识库中指定页面的完整内容（markdown 原文，含来源条款信息）。

    page 参数取 wiki_index 返回的 slug（如 "heating-season-and-standard"）。
    返回 content = 页面全文；若页面不存在返回 null（表示知识库中还没有这项信息，
    应如实告知用户「该信息尚未登记」，不要编造）。

    典型用法：用户问「我们小区供热期是什么时候/暖气不热怎么办」→ wiki_index
    看有哪些页 → wiki_read("heating-season-and-standard") 读全文 → 按原文回答。
    """
    return _read_page(page)
