"""确定性 judge 组件 — 评测断言（#57）

设计（#55 调研定稿）：
- 测试样本（prompt）与判定逻辑分离：任务文件提供 prompt + judge 实例
- 有客观判据一律确定性断言（不依赖 LLM judge），避免位置/冗长偏差与成本
- judge 通过 EvalContext 拿到 AgentTrace（工具调用序列/回复）与 db/user_id（DB 状态检查用）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

# ── 轨迹数据 ────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """一次工具调用"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_llm(cls, data: dict) -> "ToolCall":
        """从 agent.run 的 tool_call 事件 data 构造（arguments 为 JSON 字符串）"""
        fn = data.get("function", {})
        args_raw = fn.get("arguments", "{}") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {}
        return cls(name=fn.get("name", ""), arguments=args if isinstance(args, dict) else {})


@dataclass
class AgentTrace:
    """一次 agent 运行的轨迹（从 agent.run 事件流收集）"""

    prompt: str
    tool_calls: list[ToolCall] = field(default_factory=list)  # 按调用顺序
    replies: list[str] = field(default_factory=list)  # 各轮回复文本
    final_reply: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [tc.name for tc in self.tool_calls]

    def tool_called(self, name: str) -> bool:
        return any(tc.name == name for tc in self.tool_calls)


# ── Judge 协议 ──────────────────────────────────────────────────


@dataclass
class EvalContext:
    """judge 可用的上下文：轨迹 + 数据库会话 + 用户"""

    trace: AgentTrace
    db: Any = None
    user_id: int | None = None


@dataclass
class JudgeResult:
    passed: bool
    reason: str


class Judge(Protocol):
    async def check(self, ctx: EvalContext) -> JudgeResult: ...


# ── 内置确定性断言组件 ───────────────────────────────────────────


def _param_match(expected: Any, actual: Any) -> bool:
    """单参数值匹配：#58 扩展——期望值为 list 时做「包含匹配」（实际值须含全部字符串），
    否则精确相等。list 语义服务检索参数合理性断言（LLM 生成的 query 措辞不可控，
    只要求包含关键关键词）。"""
    if isinstance(expected, list):
        return isinstance(actual, str) and all(kw in actual for kw in expected)
    return actual == expected


class ToolCalled:
    """断言调用了指定工具；可带参数子集匹配（arguments 为 dict 时做子集比对）"""

    def __init__(self, name: str, params: dict[str, Any] | None = None) -> None:
        self.name = name
        self.params = params

    async def check(self, ctx: EvalContext) -> JudgeResult:
        for tc in ctx.trace.tool_calls:
            if tc.name != self.name:
                continue
            if self.params is None:
                return JudgeResult(True, f"调用了工具 {self.name}")
            # 参数子集匹配：断言里的键值都出现在实际参数中（list 值 = 包含匹配）
            missing = {
                k: v for k, v in self.params.items()
                if not _param_match(v, tc.arguments.get(k))
            }
            if not missing:
                return JudgeResult(True, f"调用了工具 {self.name} 且参数匹配")
            return JudgeResult(
                False,
                f"调用了工具 {self.name} 但参数不匹配: 期望 {self.params} 实际 {tc.arguments}",
            )
        return JudgeResult(False, f"未调用工具 {self.name}（实际调用: {ctx.trace.tool_names or '无'}）")


class NoToolCalled:
    """断言未调用任何工具（边界行为：无结果时不应乱调工具）"""

    async def check(self, ctx: EvalContext) -> JudgeResult:
        if not ctx.trace.tool_calls:
            return JudgeResult(True, "未调用任何工具")
        return JudgeResult(False, f"不应调用工具但实际调用了: {ctx.trace.tool_names}")


class MarkerInReply:
    """断言最终回复包含指定 marker 字符串"""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    async def check(self, ctx: EvalContext) -> JudgeResult:
        if self.marker in ctx.trace.final_reply:
            return JudgeResult(True, f"回复包含 marker「{self.marker}」")
        return JudgeResult(
            False,
            f"回复不含 marker「{self.marker}」，实际回复: {ctx.trace.final_reply[:80]}",
        )


class AllOf:
    """组合：全部子断言通过才算通过"""

    def __init__(self, *judges: Judge) -> None:
        self.judges = list(judges)

    async def check(self, ctx: EvalContext) -> JudgeResult:
        for j in self.judges:
            r = await j.check(ctx)
            if not r.passed:
                return JudgeResult(False, f"[{r.reason}]")
        return JudgeResult(True, "全部断言通过")


class AnyOf:
    """组合：任一子断言通过即通过"""

    def __init__(self, *judges: Judge) -> None:
        self.judges = list(judges)

    async def check(self, ctx: EvalContext) -> JudgeResult:
        reasons = []
        for j in self.judges:
            r = await j.check(ctx)
            if r.passed:
                return JudgeResult(True, r.reason)
            reasons.append(r.reason)
        return JudgeResult(False, "所有断言均未通过: " + " | ".join(reasons))


class DBMemoryContains:
    """DB 状态检查：断言该用户已写入含关键词的记忆（#58 记忆正确性）

    用途：验证「记住 X」类任务的落库结果——agent 调了 memory_add 只是第一步，
    还要确认内容真的写进了 UserMemory 表（body 或 description 包含关键词）。
    """

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword

    async def check(self, ctx: EvalContext) -> JudgeResult:
        if ctx.db is None or ctx.user_id is None:
            return JudgeResult(False, "无 db/user_id 上下文，无法做 DB 状态检查")
        from sqlalchemy import or_, select

        from app.models.user_memory import UserMemory

        stmt = (
            select(UserMemory)
            .where(
                UserMemory.user_id == ctx.user_id,
                or_(
                    UserMemory.body.contains(self.keyword),
                    UserMemory.description.contains(self.keyword),
                ),
            )
            .limit(1)
        )
        row = (await ctx.db.execute(stmt)).scalars().first()
        if row is not None:
            return JudgeResult(
                True,
                f"DB 已写入含「{self.keyword}」的记忆（id={row.id}）",
            )
        return JudgeResult(
            False,
            f"DB 中无含「{self.keyword}」的记忆（用户 {ctx.user_id}）",
        )
