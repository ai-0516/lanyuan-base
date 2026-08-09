"""评测 harness — 驱动测试任务跑真实 agent，产出可对比报告（#57）

设计（#55 调研定稿）：
- 测试任务 = 数据（prompt）+ judge 逻辑（确定性断言），任务文件导出 TASKS 列表
- 复用现有 AIAgent 循环 + 工具注册表（不修改生产代码）
- 配置三分离：被测配置（RunConfig：system_prompt）vs 测试集（tasks）vs 评测参数（reps）
- baseline/candidate 对比 + bootstrap 置信度（区分真提升与随机波动）
- 框架本身不含 LLM 调用；是否真跑 LLM 由 CLI 的 --llm 门控决定
"""

from __future__ import annotations

import importlib.util
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.harness.agent import AIAgent
from app.harness.context import build_messages
from app.harness.evals.judge import AgentTrace, EvalContext, ToolCall
from app.harness.tools import TOOLS, execute_tool

# 任务结构：{"name": str, "prompt": str, "judge": Judge, "system_prompt": str | None}

# ── 单次运行结果 ────────────────────────────────────────────────


@dataclass
class TaskResult:
    """单个任务单次运行的结果"""

    task: str
    cfg_name: str
    passed: bool
    reason: str
    trace: AgentTrace


@dataclass
class RunConfig:
    """被测配置（配置三分离之一）：目前支持 system_prompt 覆盖

    system_prompt=None → 使用生产默认 system prompt（build_messages）
    system_prompt=给定 → 完全覆盖（baseline/candidate 对比的维度）
    """

    name: str
    system_prompt: str | None = None


# ── 任务加载 ────────────────────────────────────────────────────


def load_tasks(path: str | Path) -> list[dict]:
    """从 Python 文件加载 TASKS 列表

    path 可以是 .py 文件，或包含 TASKS 定义的目录（扫描 *.py）。
    任务格式：{"name", "prompt", "judge": Judge 实例, "system_prompt"?: str}
    """
    p = Path(path)
    files = sorted(p.glob("*.py")) if p.is_dir() else [p]
    tasks: list[dict] = []
    for f in files:
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"evals_tasks_{f.stem}", f)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for task in getattr(mod, "TASKS", []):
            assert "name" in task and "prompt" in task and "judge" in task, (
                f"{f.name}: 任务必须含 name/prompt/judge"
            )
            tasks.append(task)
    return tasks


# ── 执行器 ──────────────────────────────────────────────────────


def _build_messages(prompt: str, task_name: str, system_prompt: str | None) -> list[dict]:
    """构造 LLM messages：默认走生产 system prompt，覆盖时直接拼接"""
    if system_prompt is None:
        return build_messages(
            history=[],
            user_message=prompt,
            session_id=f"eval-{task_name}",
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


async def run_task(
    task: dict,
    db: Any,
    user_id: int,
    cfg: RunConfig,
) -> TaskResult:
    """跑单个任务一次：agent 循环 → 收集轨迹 → judge 判定"""
    trace = AgentTrace(prompt=task["prompt"])
    messages = _build_messages(task["prompt"], task["name"], cfg.system_prompt)

    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    current_reply: list[str] = []
    async for event, data in agent.run(
        messages,
        db=db,
        user_id=user_id,
        meta={"eval": True, "task": task["name"], "cfg": cfg.name},
    ):
        if event == "message:start":
            current_reply = []
        elif event == "token":
            if isinstance(data, str):
                current_reply.append(data)
        elif event == "tool_call":
            if isinstance(data, dict):
                trace.tool_calls.append(ToolCall.from_llm(data))
            if current_reply:
                trace.replies.append("".join(current_reply))
                current_reply = []
        elif event == "done":
            if current_reply:
                reply = "".join(current_reply)
                trace.replies.append(reply)
                trace.final_reply = reply
                current_reply = []
        elif event == "error":
            trace.errors.append(str(data))

    judge = task["judge"]
    result = await judge.check(EvalContext(trace=trace, db=db, user_id=user_id))
    return TaskResult(
        task=task["name"],
        cfg_name=cfg.name,
        passed=result.passed,
        reason=result.reason,
        trace=trace,
    )


async def run_batch(
    tasks: list[dict],
    cfg: RunConfig,
    db: Any,
    user_id: int,
) -> list[TaskResult]:
    """单配置跑全部任务（每任务一次）"""
    return [await run_task(t, db, user_id, cfg) for t in tasks]


# ── bootstrap 置信度 ────────────────────────────────────────────


def bootstrap_ci(passed: list[bool], n_boot: int = 1000, seed: int = 42) -> tuple[float, float]:
    """通过/失败序列的 bootstrap 95% 置信区间（openai/simple-evals 思路）

    重采样 n_boot 次求通过率分布，取 2.5%/97.5% 分位。
    """
    if not passed:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(passed)
    rates = [
        sum(rng.choice(passed) for _ in range(n)) / n for _ in range(n_boot)
    ]
    rates.sort()
    low = rates[int(0.025 * n_boot)]
    high = rates[int(0.975 * n_boot)]
    return (round(low, 3), round(high, 3))


# ── baseline/candidate 对比 ─────────────────────────────────────


@dataclass
class CompareRow:
    """单个任务在两种配置下的对比结果"""

    task: str
    baseline_rate: float  # 0~1
    baseline_ci: tuple[float, float]
    candidate_rate: float
    candidate_ci: tuple[float, float]
    delta: float  # candidate - baseline，百分点


@dataclass
class CompareReport:
    rows: list[CompareRow]

    def render(self) -> str:
        lines = [
            f"{'task':28s} {'baseline':>18s} {'candidate':>18s} {'delta':>7s}",
            "-" * 74,
        ]
        for r in self.rows:
            lines.append(
                f"{r.task[:28]:28s} "
                f"{r.baseline_rate * 100:5.0f}% ({r.baseline_ci[0] * 100:.0f}-{r.baseline_ci[1] * 100:.0f}) "
                f"{r.candidate_rate * 100:5.0f}% ({r.candidate_ci[0] * 100:.0f}-{r.candidate_ci[1] * 100:.0f}) "
                f"{r.delta:6.0f}pp"
            )
        return "\n".join(lines)


async def compare(
    tasks: list[dict],
    baseline: RunConfig,
    candidate: RunConfig,
    db: Any,
    user_id: int,
    reps: int = 3,
) -> CompareReport:
    """同任务 × 两配置 × reps 次运行，输出通过率 + bootstrap CI + 提升"""
    rows: list[CompareRow] = []
    for task in tasks:
        b_passed: list[bool] = []
        c_passed: list[bool] = []
        for _ in range(reps):
            b_passed.append((await run_task(task, db, user_id, baseline)).passed)
            c_passed.append((await run_task(task, db, user_id, candidate)).passed)
        b_rate = sum(b_passed) / len(b_passed)
        c_rate = sum(c_passed) / len(c_passed)
        rows.append(CompareRow(
            task=task["name"],
            baseline_rate=b_rate,
            baseline_ci=bootstrap_ci(b_passed),
            candidate_rate=c_rate,
            candidate_ci=bootstrap_ci(c_passed),
            delta=round((c_rate - b_rate) * 100),
        ))
    return CompareReport(rows=rows)
