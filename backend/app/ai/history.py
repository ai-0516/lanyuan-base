"""v2 历史消息投影（TECH_SPEC §10.4：历史列表数据源 = DSH session 日志派生）

MySQL events 表是 append-only 事件日志（DSH 侧 mysql-persistence 写入），
前端历史列表 = 事件日志的「用户视图」投影（session-surface.md 心智模型：
一个日志，多个投影）。投影规则与前端流式消费语义完全一致（§10.1）：

- `user/message` → 用户气泡（content）
- `step/start` → 开新 assistant 段（一次 LLM 调用 = 一条气泡，对齐
  step/start 前端语义）；若上一个段为空则先丢弃——纯工具步骤无文字不显示
- `assistant/chunk`（仅 text-delta）→ 追加当前 assistant 段
- `turn/end` → 收尾当前段（最终气泡仍空则丢弃）

其余事件（tool/*、reasoning-delta、step/end 等）不投影（§4.2 白名单外）。

本模块只做纯函数投影，不碰 DB——查询在 API 层（v2/ai.py）。
"""

from __future__ import annotations

import json


def _as_dict(data) -> dict:
    """events.data 归一化：text() 原生查询下 MySQL JSON 列（aiomysql/pymysql）
    与 SQLite JSON 列均返回 **str**（驱动层不反序列化）——必须显式 json.loads；
    ORM/类型化查询返回 dict 时原样透传。解析失败（异常数据）按空 dict 处理。
    """
    if isinstance(data, str):
        try:
            return json.loads(data)
        except (ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def _extract_user_content(data: dict) -> str:
    """user/message 的 content 提取（真实 DSH 事件 = content block 数组：
    [{"type": "text", "text": "..."}]；兼容裸字符串形态）"""
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content) if content else ""


def project_messages(events: list[dict]) -> list[dict]:
    """事件序列（seq 升序）→ 消息序列 [{role, content, seq, time}]

    - events: [{"seq", "type", "time", "data"}]，seq 必须升序（调用方保证）
    - 返回消息按 seq 升序（时间正序），与输入事件顺序一致
    - role: "user" | "assistant"；content: 文本；seq: 段内首个事件 seq
      （分页游标用，加载更早 = seq < 已加载最小 seq）；time: 段内首个事件 time
    """
    messages: list[dict] = []
    cur: dict | None = None  # 当前 assistant 段

    def _finish_segment() -> None:
        """收尾当前 assistant 段：非空才保留（纯工具步骤/空回复丢弃）"""
        nonlocal cur
        if cur is not None:
            if cur["content"]:
                messages.append(cur)
            cur = None

    for ev in events:
        etype = ev.get("type")
        data = _as_dict(ev.get("data"))
        if etype == "user/message":
            messages.append({
                "role": "user",
                "content": _extract_user_content(data),
                "seq": ev.get("seq"),
                "time": ev.get("time"),
            })
        elif etype == "step/start":
            # 上一个 step 的气泡就此定型（空则丢弃——纯工具步骤无文字）
            _finish_segment()
            cur = {"role": "assistant", "content": "", "seq": ev.get("seq"), "time": ev.get("time")}
        elif etype == "assistant/chunk":
            chunk = data.get("chunk") or {}
            if chunk.get("type") != "text-delta":
                continue
            if cur is None:
                # 无 step/start 的 text-delta：正常 DSH 事件流无此路径（agent-loop
                # 源码 548/621 行：step/start 先于 assistant/chunk append，PR #98
                # review 查证）；保留为 events 表异常历史数据（中断残留/旧版本）
                # 的兜底——投影对脏数据优雅降级（与 _as_dict 同哲学），不抛崩溃
                cur = {"role": "assistant", "content": "", "seq": ev.get("seq"), "time": ev.get("time")}
            cur["content"] += chunk.get("text", "")
        elif etype == "turn/end":
            # 回合结束：收尾当前段（最终气泡仍空则丢弃，§10.1）
            _finish_segment()

    # 流未正常收尾（turn/end 缺失）时兜底收尾
    _finish_segment()
    return messages
