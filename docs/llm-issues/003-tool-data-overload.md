---
title: "tool 返回巨量原始数据，LLM 看不懂且撑爆消息体"
date: 2026-07-27
status: resolved
---

## 症状

数据过大导致 MySQL TEXT 溢出（DataError），且 LLM 收到的 tool 结果是 `<User object at 0x...>` 或含 base64 头像的百 KB JSON。

## 根因

`@tool` 装饰器默认返回 `json.dumps(api_success(result))` 的原始 API 响应。但 HTTP 客户端和 LLM 需要的是两套数据：

| 消费者 | 需要 | 问题 |
|--------|------|------|
| HTTP 客户端 | 完整数据（含头像、时间等） | — |
| LLM | 精炼摘要（作者、标题、点赞数） | 原始 JSON 含大量无用信息 |

具体问题：
1. **`list_posts`**：返回 15 条帖子，每条含 base64 头像（50-100KB/个）→ 总数据量数百 KB，MySQL TEXT 溢出
2. **`get_my_profile`**：`json.dumps(SQLAlchemy User object, default=str)` 输出 `<User object at 0x...>`，LLM 完全读不懂

## 解决方案

**引入 `result_formatter` 参数**（commit `4c1c0d7`）：

```python
def _format_posts(data: dict) -> str:
    items = data.get("items", [])
    return "\n".join(
        f"  #{p['id']} {p['user']['nickname']}：{p['content'][:80]} [{len(p['comments'])}条评论]"
        for p in items
    )

@tool(result_formatter=_format_posts)
async def list_posts(...):
    return api_success(result)
```

**执行流程**：

```
ToolDef.execute()
  → 调函数 → api_success(完整数据)
  → 自动解包取 data
  → 有 result_formatter？ → 调它，返回摘要文本
  → 没有？ → json.dumps + base64 清洗 + 50KB 截断兜底
```

同时增加两重兜底：
1. **SQLAlchemy 模型自动转 dict**：检测 `_sa_instance_state` 后用 `__table__.columns` 转为 `{col: value}`
2. **base64 头像清洗**：正则替换 `data:image/...;base64,...` 为空字符串

## 验证

- `list_posts`：从数百 KB 降到 ~200 字（"共 15 条帖子：\n  #24 一五OO：求租车位... [1条评论, 1赞]\n  ..."）
- `get_my_profile`：从 `<User object at 0x...>` 变为 `{"id": 10, "nickname": "一五OO", ...}`
- jsonl 日志中 `turns[].tool_results[].result` 内容精炼可读

## 教训

**每个 tool 都应该考虑给 LLM 看什么，而不只是把 HTTP 响应原样丢过去。** HTTP 要完整，LLM 要精炼，两者是不同需求。`result_formatter` 是 @tool 的可选参数，简单 tool 不需要，数据量大时必须用。

---

## 后续改进（2026-07-28）

### 1. `_strip_avatar`：统一移除头像字段

之前用正则 `_BASE64_PATTERN` 在 JSON 字符串中替换 base64 值，但：
- 有 `result_formatter` 的工具返回的是摘要文本，不会走到正则
- 正则只能清空值，不能删字段

改为 **序列化前递归删除所有 dict 的 `avatar` 键**（commit `1969d98`）：

```python
def _strip_avatar(data) -> None:
    if isinstance(data, dict):
        data.pop("avatar", None)
        for v in data.values():
            _strip_avatar(v)
    elif isinstance(data, list):
        for item in data:
            _strip_avatar(item)
```

放在 `ToolDef.execute()` 的 api_success 解包后、result_formatter 判断前，覆盖两条路径。

### 2. `large_tool` hook：自动监控大结果

新增独立钩子 `hooks/large_tool.py`（commit `5b6db92`）：
- 每个 tool 返回后记一行到 `app.log`（含 result_len）
- 超出阈值（暂设 50KB）额外记到 `logs/tool-oversize.log`
- 用于收集数据判断哪些 tool 需要加 formatter

### 3. 日志透传原始结果

`tool:end` handler 直接显示原始 result，不截断不展平，方便观察哪些字段占空间（commit `e037d93`）。
