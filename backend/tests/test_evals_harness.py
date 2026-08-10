"""评测 harness 测试（#57）

- run_task 管线冒烟：无 LLM key 时走 mock LLM，零成本验证任务→agent→judge→结果
- bootstrap 置信度计算
- baseline/candidate 对比报告
- load_tasks 任务加载
- CLI --llm 门控（无 --llm 不产生任何 LLM 调用）
"""

import sys

import pytest

from app.harness.evals.harness import (
    RunConfig,
    Task,
    bootstrap_ci,
    compare,
    load_tasks,
    run_batch,
    run_task,
)
from app.harness.evals.judge import MarkerInReply, NoToolCalled

MOCK_MARKER = "模拟模式"  # streaming.MOCK_REPLY_TEMPLATE 包含的固定文案


# ── run_task 冒烟（mock LLM）────────────────────────────────────


async def test_run_task_smoke_no_tool_call():
    """mock 模式：agent 不调工具 → NoToolCalled 通过"""
    task = Task(name="smoke_no_tool", prompt="你好", judge=NoToolCalled())
    result = await run_task(task, db=None, user_id=1, cfg=RunConfig(name="run"))
    assert result.passed is True
    assert result.trace.final_reply  # 有回复


async def test_run_task_marker_in_mock_reply():
    """mock 回复固定含「模拟模式」→ MarkerInReply 通过，验证 final_reply 收集正确"""
    task = Task(name="smoke_marker", prompt="你好", judge=MarkerInReply(MOCK_MARKER))
    result = await run_task(task, db=None, user_id=1, cfg=RunConfig(name="run"))
    assert result.passed is True


async def test_run_task_system_prompt_override():
    """system_prompt 覆盖：自定义 prompt 出现于 mock 回复中（mock 回显 user 消息）"""
    task = Task(name="smoke_sp", prompt="测试覆盖", judge=MarkerInReply("测试覆盖"))
    cfg = RunConfig(name="custom", system_prompt="你是评测专用助手。")
    result = await run_task(task, db=None, user_id=1, cfg=cfg)
    assert result.passed is True


async def test_run_batch_multiple_tasks():
    tasks = [
        Task(name="t1", prompt="你好", judge=NoToolCalled()),
        Task(name="t2", prompt="你好", judge=NoToolCalled()),
    ]
    results = await run_batch(tasks, RunConfig(name="run"), db=None, user_id=1)
    assert len(results) == 2
    assert all(r.passed for r in results)


# ── bootstrap 置信度 ─────────────────────────────────────────────


def test_bootstrap_ci_all_pass():
    assert bootstrap_ci([True] * 6) == (1.0, 1.0)


def test_bootstrap_ci_all_fail():
    assert bootstrap_ci([False] * 6) == (0.0, 0.0)


def test_bootstrap_ci_mixed_contains_rate():
    passed = [True, True, False, True, False, True, True, False, True, True]
    low, high = bootstrap_ci(passed, n_boot=2000)
    rate = sum(passed) / len(passed)
    assert low <= rate <= high
    assert 0.0 <= low <= high <= 1.0


def test_bootstrap_ci_empty():
    assert bootstrap_ci([]) == (0.0, 0.0)


# ── baseline/candidate 对比 ─────────────────────────────────────


async def test_compare_report_shape():
    """mock 下两配置都通过 NoToolCalled → 两行报告、delta=0"""
    tasks = [
        Task(name="cmp1", prompt="你好", judge=NoToolCalled()),
        Task(name="cmp2", prompt="你好", judge=NoToolCalled()),
    ]
    baseline = RunConfig(name="baseline")
    candidate = RunConfig(name="candidate", system_prompt="自定义 candidate prompt。")
    report = await compare(tasks, baseline, candidate, db=None, user_id=1, reps=2)

    assert len(report.rows) == 2
    for row in report.rows:
        assert row.baseline_rate == 1.0
        assert row.candidate_rate == 1.0
        assert row.delta == 0
    text = report.render()
    assert "cmp1" in text and "baseline" in text and "candidate" in text


# ── 任务加载 ─────────────────────────────────────────────────────


def test_load_tasks_from_file(tmp_path):
    f = tmp_path / "tasks.jsonl"
    f.write_text(
        '{"name": "a", "prompt": "p1", "expect": {"no_tool": true}}\n'
        '{"name": "b", "prompt": "p2", "expect": {"no_tool": true}}\n',
        encoding="utf-8",
    )
    tasks = load_tasks(f)
    assert [t.name for t in tasks] == ["a", "b"]


def test_load_tasks_rejects_py_file(tmp_path):
    """#66 定：任务文件只用 jsonl，Python 任务文件不再支持"""
    f = tmp_path / "tasks.py"
    f.write_text("TASKS = []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="jsonl"):
        load_tasks(f)


def test_load_tasks_from_dir(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        '{"name": "x", "prompt": "p", "expect": {"no_tool": true}}\n',
        encoding="utf-8",
    )
    (tmp_path / "_skip.jsonl").write_text(
        '{"name": "skip", "prompt": "p", "expect": {"no_tool": true}}\n',
        encoding="utf-8",
    )
    (tmp_path / "ignore.py").write_text("TASKS = []\n", encoding="utf-8")
    tasks = load_tasks(tmp_path)
    # 只加载 .jsonl（_ 前缀跳过），目录中的 .py 被忽略
    assert [t.name for t in tasks] == ["x"]


def test_load_tasks_from_sample_file():
    """sample 任务文件（tests/data/sample_tasks.jsonl）可被 load_tasks 直接加载（#66 迁移为 jsonl）"""
    from pathlib import Path

    sample = Path(__file__).parent / "data" / "sample_tasks.jsonl"
    tasks = load_tasks(sample)
    names = [t.name for t in tasks]
    assert names == [
        "greeting_no_tool",
        "get_post_by_id",
        "get_post_wrong_param",
        "search_and_mention",
        "either_tool_or_marker",
    ]
    # judge 全部是可执行断言组件（Judge 协议：有 check 方法）
    for t in tasks:
        assert callable(getattr(t.judge, "check", None)), t.name


# ── CLI --llm 门控 ──────────────────────────────────────────────


def test_cli_requires_llm_flag(monkeypatch, capsys):
    """无 --llm 时返回 1 且不进入评测流程（不 import app 评测模块）"""
    from app.harness.evals import cli

    monkeypatch.setattr(sys, "argv", ["cli", "--tasks", "/tmp/nonexistent.py"])
    assert cli.main() == 1
    err = capsys.readouterr().err
    assert "--llm" in err
