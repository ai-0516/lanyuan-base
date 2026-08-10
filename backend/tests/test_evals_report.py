"""ATOF 报告器测试（#56）

构造合成 ATOF 事件序列，验证指标计算：计数、错误判定、重试、tokens、耗时、分组、聚合。
"""

import json

import pytest

from app.harness.evals.report import (
    aggregate,
    format_report,
    read_events,
    score_file,
    score_req,
)

pytestmark = pytest.mark.eval  # 无 LLM 类评测（#59）

TS0 = "2026-08-08T09:38:10.000000+00:00"


def _ev(event: str, ts: str = TS0, req_id: str = "r1", **kw) -> dict:
    d = {"event": event, "req_id": req_id, "ts": ts}
    d.update(kw)
    return d


def _ts(sec: float) -> str:
    """相对 TS0 偏移秒数生成 ISO 时间戳"""
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(TS0)
    return (base + timedelta(seconds=sec)).isoformat()


def _write_jsonl(tmp_path, events: list[dict], name: str = "test.jsonl") -> str:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return str(p)


# ── 计数：turns / llm_calls / tool_calls ──────────────────────────


def test_counts():
    events = [
        _ev("agent:start", _ts(0)),
        _ev("turn:start", _ts(1), turn=0),
        _ev("llm:start", _ts(2), turn=0),
        _ev("llm:end", _ts(3), turn=0, finish_reason="tool_calls",
            tokens=11, usage={"completion_tokens": 79, "total_tokens": 3226}),
        _ev("tool:start", _ts(4), turn=0, tool_name="get_post"),
        _ev("tool:end", _ts(5), turn=0, tool_name="get_post", result="{}", status="ok"),
        _ev("turn:end", _ts(6), turn=0),
        _ev("turn:start", _ts(7), turn=1),
        _ev("llm:start", _ts(8), turn=1),
        _ev("llm:end", _ts(9), turn=1, finish_reason="stop", tokens=5),
        _ev("turn:end", _ts(10), turn=1),
        _ev("agent:end", _ts(11), total_turns=2),
    ]
    m = score_req("r1", events)
    assert m.turns == 2
    assert m.llm_calls == 2
    assert m.tool_calls == 1
    assert m.wall_s == 11.0


# ── 错误判定 + 错误明细 ───────────────────────────────────────────


def test_tool_errors_and_details():
    events = [
        _ev("tool:start", _ts(0), tool_name="search_history"),
        _ev("tool:end", _ts(1), tool_name="search_history", result="boom", status="error"),
        _ev("tool:start", _ts(2), tool_name="memory_get"),
        _ev("tool:end", _ts(3), tool_name="memory_get", result="ok", status="ok"),
    ]
    m = score_req("r1", events)
    assert m.tool_errors == 1
    assert m.error_tools == [("search_history", 1)]


def test_llm_error_sets_req_error():
    events = [
        _ev("agent:start", _ts(0)),
        _ev("llm:start", _ts(1), turn=0),
        _ev("llm:error", _ts(2), turn=0, error="timeout", error_code="TIMEOUT"),
        _ev("agent:end", _ts(3), total_turns=1),
    ]
    m = score_req("r1", events)
    assert m.error == "llm_error"


def test_agent_end_error_wins():
    events = [
        _ev("agent:start", _ts(0)),
        _ev("llm:error", _ts(1), turn=0, error="x", error_code="X"),
        _ev("agent:end", _ts(2), total_turns=1, error="agent crashed"),
    ]
    m = score_req("r1", events)
    assert m.error == "agent crashed"


# ── 重试：错误后同名工具再次调用 ──────────────────────────────────


def test_retries_after_error():
    events = [
        _ev("tool:start", _ts(0), tool_name="search_history"),
        _ev("tool:end", _ts(1), tool_name="search_history", result="err", status="error"),
        _ev("tool:start", _ts(2), tool_name="search_history"),  # 重试
        _ev("tool:end", _ts(3), tool_name="search_history", result="ok", status="ok"),
        _ev("tool:start", _ts(4), tool_name="memory_get"),  # 非重试（前一个成功）
        _ev("tool:end", _ts(5), tool_name="memory_get", result="ok", status="ok"),
    ]
    m = score_req("r1", events)
    assert m.retries == 1
    assert m.tool_calls == 3
    assert m.tool_errors == 1


def test_no_retry_when_prev_succeeded():
    events = [
        _ev("tool:start", _ts(0), tool_name="search_history"),
        _ev("tool:end", _ts(1), tool_name="search_history", result="ok", status="ok"),
        _ev("tool:start", _ts(2), tool_name="search_history"),  # 再次调用非重试
        _ev("tool:end", _ts(3), tool_name="search_history", result="ok", status="ok"),
    ]
    m = score_req("r1", events)
    assert m.retries == 0


# ── tokens：usage 优先，tokens 字段回退 ───────────────────────────


def test_tokens_from_usage():
    events = [
        _ev("llm:end", _ts(0), turn=0, tokens=11,
            usage={"completion_tokens": 79, "total_tokens": 3226}),
        _ev("llm:end", _ts(1), turn=1, tokens=5,
            usage={"completion_tokens": 120}),
    ]
    m = score_req("r1", events)
    assert m.completion_tokens == 199


def test_tokens_fallback_without_usage():
    events = [_ev("llm:end", _ts(0), turn=0, tokens=42)]
    m = score_req("r1", events)
    assert m.completion_tokens == 42


# ── 分组 + 文件读取 + 坏行 ────────────────────────────────────────


def test_score_file_groups_by_req_id(tmp_path):
    events = [
        _ev("llm:end", _ts(0), req_id="a", turn=0, tokens=10),
        _ev("llm:end", _ts(1), req_id="b", turn=0, tokens=20),
        _ev("llm:end", _ts(2), req_id="a", turn=1, tokens=30),
    ]
    path = _write_jsonl(tmp_path, events)
    metrics = score_file(path)
    by_id = {m.req_id: m for m in metrics}
    assert set(by_id) == {"a", "b"}
    assert by_id["a"].llm_calls == 2
    assert by_id["a"].completion_tokens == 40
    assert by_id["b"].llm_calls == 1


def test_bad_lines_skipped(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        "{\"event\": \"llm:end\", \"req_id\": \"r1\", \"ts\": \"2026-08-08T09:38:10+00:00\", \"tokens\": 7}\n"
        "这不是合法的 json\n"
        "not json either\n",
        encoding="utf-8",
    )
    events = read_events(str(p))
    assert len(events) == 1


def test_read_events_skips_non_event_lines(tmp_path):
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        "{\"foo\": \"bar\"}\n"
        "{\"event\": \"llm:end\", \"req_id\": \"r1\", \"ts\": \"2026-08-08T09:38:10+00:00\", \"tokens\": 3}\n",
        encoding="utf-8",
    )
    events = read_events(str(p))
    assert len(events) == 1
    assert events[0]["event"] == "llm:end"


# ── 聚合 ──────────────────────────────────────────────────────────


def test_aggregate():
    m1 = score_req("a", [_ev("llm:end", _ts(0), req_id="a", turn=0, tokens=10)])
    m2 = score_req("b", [
        _ev("tool:start", _ts(0), req_id="b", tool_name="x"),
        _ev("tool:end", _ts(1), req_id="b", tool_name="x", result="e", status="error"),
        _ev("agent:end", _ts(5), req_id="b", total_turns=1, error="boom"),
    ])
    agg = aggregate([m1, m2])
    assert agg["req_count"] == 2
    assert agg["llm_calls_total"] == 1
    assert agg["tool_calls_total"] == 1
    assert agg["tool_errors_total"] == 1
    assert agg["completion_tokens_total"] == 10
    assert agg["reqs_with_error"] == 1


# ── sample 数据端到端（PR #61 review 补充）──────────────────────


def test_sample_atof_end_to_end():
    """tests/data/sample_atof.jsonl 完整走 score_file → aggregate → format_report

    两个 req：001 正常流程（0 错 0 重试）、002 工具失败后重试成功（1 错 1 重试）。
    用真实 sample 数据验证报告器，避免纯合成事件与生产格式脱节。
    """
    from pathlib import Path

    sample = Path(__file__).parent / "data" / "sample_atof.jsonl"
    metrics = score_file(str(sample))
    by_id = {m.req_id: m for m in metrics}
    assert set(by_id) == {"sample-20260808-001", "sample-20260808-002"}

    m1 = by_id["sample-20260808-001"]
    assert m1.turns == 2 and m1.llm_calls == 2 and m1.tool_calls == 1
    assert m1.tool_errors == 0 and m1.retries == 0
    assert m1.completion_tokens == 199  # 79 + 120

    m2 = by_id["sample-20260808-002"]
    assert m2.turns == 2 and m2.llm_calls == 2 and m2.tool_calls == 2
    assert m2.tool_errors == 1 and m2.retries == 1
    assert m2.completion_tokens == 150  # 60 + 90
    assert m2.error_tools == [("search_history", 1)]

    agg = aggregate(metrics)
    assert agg["req_count"] == 2
    assert agg["tool_errors_total"] == 1 and agg["retries_total"] == 1
    text = format_report(metrics, agg)
    assert "sample-20260" in text and "search_history×1" in text
