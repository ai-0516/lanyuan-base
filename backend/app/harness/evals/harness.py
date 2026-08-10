"""评测 harness — 驱动测试任务跑真实 agent，产出可对比报告（#57）

设计（#55 调研定稿）：
- 测试任务 = 数据（prompt）+ judge 逻辑（确定性断言），任务文件导出 TASKS 列表
- 复用现有 AIAgent 循环 + 工具注册表（不修改生产代码）
- 配置三分离：被测配置（RunConfig：system_prompt）vs 测试集（tasks）vs 评测参数（reps）
- baseline/candidate 对比 + bootstrap 置信度（区分真提升与随机波动）
- 框架本身不含 LLM 调用；是否真跑 LLM 由 CLI 的 --llm 门控决定
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.harness.agent import AIAgent
from app.harness.context import build_messages
from app.harness.evals.judge import (
    AgentTrace,
    AllOf,
    AnyOf,
    DBMemoryContains,
    EvalContext,
    Judge,
    MarkerInReply,
    NoToolCalled,
    ToolCall,
    ToolCalled,
)
from app.harness.tools import TOOLS, execute_tool

# ── 任务结构 ────────────────────────────────────────────────────


@dataclass
class Task:
    """评测任务：数据（prompt）+ 判定逻辑（judge）

    任务文件导出的 TASKS 列表元素即 Task（load_tasks 从 dict 构造并校验）。
    """

    name: str
    prompt: str
    judge: Judge
    system_prompt: str | None = None


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


def _expect_to_judge(exp: dict) -> Judge:
    """jsonl 测试题 expect 字段 → Judge 组件（#58 样本与逻辑分离）

    expect 支持（可递归组合，判定键互斥——组合必须用 all/any 显式表达）：
    - {"tool": "名称", "params": {...}}       → ToolCalled（params 值 list = 包含匹配）
    - {"no_tool": true}                       → NoToolCalled
    - {"marker": "字符串"}                    → MarkerInReply
    - {"db_memory_contains": "关键词"}        → DBMemoryContains（DB 状态检查）
    - {"all": [expect...]} / {"any": [...]}   → AllOf / AnyOf 组合
    """
    _KEYS = ("all", "any", "tool", "no_tool", "marker", "db_memory_contains")
    keys = [k for k in _KEYS if k in exp]
    if len(keys) > 1:
        raise ValueError(
            f"expect 判定键互斥，同时出现 {keys}；组合请用 all/any 显式表达（#66 review）"
        )
    if "all" in exp:
        return AllOf(*(_expect_to_judge(e) for e in exp["all"]))
    if "any" in exp:
        return AnyOf(*(_expect_to_judge(e) for e in exp["any"]))
    if "tool" in exp:
        return ToolCalled(exp["tool"], exp.get("params"))
    if exp.get("no_tool"):
        return NoToolCalled()
    if "marker" in exp:
        return MarkerInReply(exp["marker"])
    if "db_memory_contains" in exp:
        return DBMemoryContains(exp["db_memory_contains"])
    raise ValueError(f"无法识别的 expect 判定: {exp!r}")


def _load_jsonl_tasks(f: Path) -> list[Task]:
    """从 jsonl 数据文件加载测试题（#58 验收：测试题沉淀为 jsonl 数据文件）

    每行一个 JSON 对象：{"name", "prompt", "expect"}（expect 见 _expect_to_judge）。
    空行与 # 开头的注释行跳过。判定逻辑全部由 expect 翻译为确定性断言组件。
    """
    tasks: list[Task] = []
    for line_no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        missing = [k for k in ("name", "prompt", "expect") if k not in item]
        if missing:
            raise ValueError(f"{f.name}:{line_no} 缺少字段 {missing}")
        tasks.append(Task(
            name=item["name"],
            prompt=item["prompt"],
            judge=_expect_to_judge(item["expect"]),
        ))
    return tasks


def load_tasks(path: str | Path) -> list[Task]:
    """从 jsonl 数据文件加载测试集（#58/#66：只支持 jsonl，去掉 Python 任务文件）

    - .jsonl 文件：每行 {name, prompt, expect}，expect 翻译为确定性 judge
    - 目录：扫描 *.jsonl（_ 前缀文件跳过）
    - 需要新的判定类型时先补全 judge 组件（expect 类型），再用 jsonl 定义 case
      （#66 定：自定义 judge 场景 = judge 定义不全，不为此保留 Python 任务文件）
    """
    p = Path(path)
    if p.is_dir():
        files = sorted(p.glob("*.jsonl"))
    else:
        if p.suffix != ".jsonl":
            raise ValueError(f"评测任务文件仅支持 .jsonl（#66 定）：{p}")
        files = [p]
    tasks: list[Task] = []
    for f in files:
        if f.name.startswith("_"):
            continue
        tasks.extend(_load_jsonl_tasks(f))
    return tasks


# ── 执行器 ──────────────────────────────────────────────────────


def _build_messages(prompt: str, task_name: str, system_prompt: str | None) -> list[dict]:
    """构造 LLM messages：默认走生产 system prompt，覆盖时直接拼接

    注意：build_messages 只拼 system + history，user 消息由生产路径从 DB
    history 提供（docstring：user_message 参数保留用于未来扩展）。评测无
    history，必须显式追加 user 消息——否则 LLM 只收到 system，回复与
    prompt 无关（实测 0/6 根因）。
    """
    if system_prompt is None:
        messages = build_messages(
            history=[],
            user_message=prompt,
            session_id=f"eval-{task_name}",
        )
        messages.append({"role": "user", "content": prompt})
        return messages
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


async def run_task(
    task: Task,
    db: Any,
    user_id: int,
    cfg: RunConfig,
) -> TaskResult:
    """跑单个任务一次：agent 循环 → 收集轨迹 → judge 判定"""
    trace = AgentTrace(prompt=task.prompt)
    messages = _build_messages(task.prompt, task.name, cfg.system_prompt)

    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    current_reply: list[str] = []
    async for event, data in agent.run(
        messages,
        db=db,
        user_id=user_id,
        meta={"eval": True, "task": task.name, "cfg": cfg.name},
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

    result = await task.judge.check(EvalContext(trace=trace, db=db, user_id=user_id))
    return TaskResult(
        task=task.name,
        cfg_name=cfg.name,
        passed=result.passed,
        reason=result.reason,
        trace=trace,
    )


async def run_batch(
    tasks: list[Task],
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
    tasks: list[Task],
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
            task=task.name,
            baseline_rate=b_rate,
            baseline_ci=bootstrap_ci(b_passed),
            candidate_rate=c_rate,
            candidate_ci=bootstrap_ci(c_passed),
            delta=round((c_rate - b_rate) * 100),
        ))
    return CompareReport(rows=rows)
