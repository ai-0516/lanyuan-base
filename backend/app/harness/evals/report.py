"""ATOF 报告器 — 读任意 ATOF jsonl，输出行为指标（#56）

数据格式服务于评测：本模块只用 ATOF 中语义明确的字段，不用启发式推断。
若字段缺失导致指标算不准，应修改 jsonl.py 的写入补充字段，而非在此兜底。

指标（对齐 hermes-agent toolperf_ab_eval score_run，字段映射到 lanyuan-base ATOF）：
- llm_calls:   llm:end 事件数
- tool_calls:  tool:start 事件数
- tool_errors: tool:end 且 status != "ok" 的事件数
- retries:     工具执行出错后，下一次调用同名工具的次数（error → 同名 start）
- tokens:      llm:end usage.completion_tokens 之和（无 usage 时回退 tokens 字段）
- wall_s:      req 内首事件到 agent:end 的墙钟秒数（无 agent:end 用末事件）

用法：
    python -m app.harness.evals.report <jsonl 文件或目录> ...
    无参数时默认读 logs/llm-requests/
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from app.harness.hooks.events import (
    AGENT_END,
    AGENT_START,
    LLM_END,
    LLM_ERROR,
    TOOL_END,
    TOOL_START,
    TURN_START,
)

DEFAULT_LOG_DIR = "logs/llm-requests"


@dataclass
class ReqMetrics:
    """单次 agent 运行（一个 req_id）的行为指标"""

    req_id: str
    turns: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    retries: int = 0
    completion_tokens: int = 0
    wall_s: float = 0.0
    error: str | None = None
    # 出错工具明细：(tool_name, 出错次数)
    error_tools: list[tuple[str, int]] = field(default_factory=list)


def _parse_ts(ts: object) -> datetime | None:
    """解析 ISO 时间戳（带或不带时区），失败返回 None"""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def read_events(path: str) -> list[dict]:
    """读取一个 ATOF jsonl 文件，返回事件列表（容忍损坏行）"""
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if "event" in ev:
                    events.append(ev)
            except json.JSONDecodeError:
                print(f"  [warn] 跳过损坏的日志行: {line[:80]}", file=sys.stderr)
    return events


def _score_error_tools(events: list[dict]) -> list[tuple[str, int]]:
    """统计各工具出错次数（按 tool:end status != ok）"""
    counter: dict[str, int] = defaultdict(int)
    for ev in events:
        if ev.get("event") == TOOL_END and ev.get("status") != "ok":
            counter[ev.get("tool_name", "")] += 1
    return sorted(counter.items(), key=lambda kv: -kv[1])


def score_req(req_id: str, events: list[dict]) -> ReqMetrics:
    """对单个 req_id 的事件序列计算指标

    事件按 ts 排序后单遍扫描：
    - tool:start 若与上一个出错工具同名 → retries+1
    - tool:end status != "ok" → 记为当前出错工具
    """
    events = sorted(events, key=lambda e: e.get("ts", ""))

    m = ReqMetrics(req_id=req_id)
    last_err_tool: str | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    agent_end_ts: datetime | None = None

    for ev in events:
        event = ev.get("event")

        if first_ts is None:
            first_ts = _parse_ts(ev.get("ts"))
        last_ts = _parse_ts(ev.get("ts")) or last_ts

        if event == AGENT_START:
            pass
        elif event == TURN_START:
            m.turns += 1
        elif event == LLM_END:
            m.llm_calls += 1
            usage = ev.get("usage")
            if isinstance(usage, dict) and usage.get("completion_tokens"):
                m.completion_tokens += usage["completion_tokens"]
            else:
                m.completion_tokens += ev.get("tokens") or 0
        elif event == TOOL_START:
            m.tool_calls += 1
            if ev.get("tool_name") and ev.get("tool_name") == last_err_tool:
                m.retries += 1
        elif event == TOOL_END:
            if ev.get("status") != "ok":
                m.tool_errors += 1
                last_err_tool = ev.get("tool_name")
            else:
                last_err_tool = None
        elif event == AGENT_END:
            agent_end_ts = _parse_ts(ev.get("ts"))
            if ev.get("error"):
                m.error = ev.get("error")

    # req 级错误：agent:end.error 或存在 llm:error 事件
    if not m.error and any(e.get("event") == LLM_ERROR for e in events):
        m.error = "llm_error"

    if first_ts is not None:
        end_ts = agent_end_ts or last_ts
        if end_ts is not None:
            m.wall_s = round((end_ts - first_ts).total_seconds(), 1)

    m.error_tools = _score_error_tools(events)
    return m


def score_file(path: str) -> list[ReqMetrics]:
    """读取一个 jsonl 并按 req_id 分组计算指标"""
    events = read_events(path)
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        rid = ev.get("req_id")
        if rid:
            groups[rid].append(ev)
    return [score_req(rid, evs) for rid, evs in groups.items()]


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def aggregate(metrics: list[ReqMetrics]) -> dict:
    """聚合多条 req 的指标（总量 + 均值）"""
    n = len(metrics)
    return {
        "req_count": n,
        "turns_total": sum(m.turns for m in metrics),
        "llm_calls_total": sum(m.llm_calls for m in metrics),
        "tool_calls_total": sum(m.tool_calls for m in metrics),
        "tool_errors_total": sum(m.tool_errors for m in metrics),
        "retries_total": sum(m.retries for m in metrics),
        "completion_tokens_total": sum(m.completion_tokens for m in metrics),
        "wall_s_mean": _mean([m.wall_s for m in metrics]),
        "reqs_with_error": sum(1 for m in metrics if m.error),
    }


def _collect_files(args: list[str]) -> list[str]:
    """解析 CLI 路径参数：文件或目录，返回 jsonl 文件列表"""
    files: list[str] = []
    for arg in args or [DEFAULT_LOG_DIR]:
        if os.path.isdir(arg):
            files.extend(sorted(glob.glob(os.path.join(arg, "*.jsonl"))))
        elif os.path.isfile(arg):
            files.append(arg)
        else:
            print(f"  [warn] 路径不存在，跳过: {arg}", file=sys.stderr)
    return files


def format_report(metrics: list[ReqMetrics], agg: dict) -> str:
    """渲染 per-req 表格 + 聚合行"""
    lines = [
        f"{'req_id':12s} {'turns':>5s} {'llm':>4s} {'tools':>6s} "
        f"{'errs':>5s} {'retr':>5s} {'tokens':>8s} {'wall_s':>7s} error",
        "-" * 68,
    ]
    for m in metrics:
        err = m.error or ("; ".join(f"{t}×{c}" for t, c in m.error_tools[:3]) or "-")
        lines.append(
            f"{m.req_id[:12]:12s} {m.turns:5d} {m.llm_calls:4d} {m.tool_calls:6d} "
            f"{m.tool_errors:5d} {m.retries:5d} {m.completion_tokens:8d} "
            f"{m.wall_s:7.1f} {err}"
        )
    lines.append("-" * 68)
    lines.append(
        f"{'TOTAL':12s} (n={agg['req_count']}) turns={agg['turns_total']} "
        f"llm={agg['llm_calls_total']} tools={agg['tool_calls_total']} "
        f"errs={agg['tool_errors_total']} retries={agg['retries_total']} "
        f"tokens={agg['completion_tokens_total']} wall_mean={agg['wall_s_mean']}s "
        f"reqs_with_error={agg['reqs_with_error']}"
    )
    return "\n".join(lines)


def main() -> int:
    files = _collect_files(sys.argv[1:])
    if not files:
        print("没有找到 ATOF jsonl 文件", file=sys.stderr)
        return 1

    all_metrics: list[ReqMetrics] = []
    for f in files:
        metrics = score_file(f)
        if metrics:
            print(f"== {f} ({len(metrics)} reqs) ==")
            all_metrics.extend(metrics)
        else:
            print(f"== {f} (无有效事件) ==")

    if not all_metrics:
        return 1

    print()
    print(format_report(all_metrics, aggregate(all_metrics)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
