"""评测 CLI — 有 LLM 类评测入口（#57）

门控设计（#55 定稿）：
- 有 LLM 类评测花钱，**必须显式 --llm 参数才执行**；无 --llm 直接退出、不产生任何 LLM 调用
- CI 不挂本入口（CI 只跑无 LLM 类的报告器）

用法：
    python -m app.harness.evals.cli --tasks <任务文件或目录> --llm [--db-url URL]
    # 单配置跑全部任务
    python -m app.harness.evals.cli --tasks tasks/ --llm
    # baseline/candidate 对比（system_prompt 维度）+ bootstrap 置信度
    python -m app.harness.evals.cli --tasks tasks/ --llm \
        --baseline-prompt-file baseline.txt --candidate-prompt-file candidate.txt --reps 3

    # 成本预期：compare 模式 LLM 调用数 = tasks × reps × 2（baseline + candidate）。
    # 例：10 任务 × 3 reps = 60 次调用/维度；reps=3 时 bootstrap CI 较宽
    # （0.2-0.8），适合冒烟，精细对比需加大 reps 或后续 paired 对比（PR #61 review）。

数据库：默认独立评测库 eval_lanyuan.db（不碰开发/生产数据），--db-url 可覆盖。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import cast


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="agent 行为评测（有 LLM 类）")
    p.add_argument("--tasks", required=True, help="任务文件(.py)或目录（含 TASKS 列表）")
    p.add_argument("--llm", action="store_true",
                   help="门控：显式开启才会调用 LLM（花钱）")
    p.add_argument("--db-url", default="",
                   help="评测数据库 URL（默认独立评测库 eval_lanyuan.db，不碰开发库）")
    p.add_argument("--baseline-prompt-file", default="",
                   help="baseline system prompt 文件（开启对比模式）")
    p.add_argument("--candidate-prompt-file", default="",
                   help="candidate system prompt 文件（开启对比模式）")
    p.add_argument("--reps", type=int, default=3, help="对比模式每任务重复次数")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # ── 门控：无 --llm 直接退出，不产生任何 LLM 调用 ──
    if not args.llm:
        print(
            "评测需要调用 LLM（花钱）。请显式加 --llm 参数执行，例如：\n"
            "  python -m app.harness.evals.cli --tasks <任务> --llm",
            file=sys.stderr,
        )
        return 1

    # 独立评测库：在 import app.core.database（engine 于模块导入时创建）之前设置
    os.environ.setdefault("DATABASE_URL", args.db_url or "sqlite+aiosqlite:///./eval_lanyuan.db")

    from app.core.database import async_session_factory, init_db
    from app.harness.evals.harness import (
        RunConfig,
        compare,
        load_tasks,
        run_batch,
    )
    from app.models.user import User
    from sqlalchemy import select

    async def _run() -> int:
        await init_db()

        # 建/取评测用户（工具调用需要 user_id）
        async with async_session_factory() as session:
            user = (await session.execute(select(User).where(User.openid == "eval"))).scalar_one_or_none()
            if user is None:
                user = User(openid="eval", nickname="评测用户")
                session.add(user)
                await session.commit()
                await session.refresh(user)
            user_id = cast(int, user.id)

        tasks = load_tasks(args.tasks)
        if not tasks:
            print(f"任务目录 {args.tasks} 未找到 TASKS 定义", file=sys.stderr)
            return 1
        print(f"加载 {len(tasks)} 个任务")

        async with async_session_factory() as session:
            if args.baseline_prompt_file and args.candidate_prompt_file:
                baseline = RunConfig(
                    name="baseline",
                    system_prompt=Path(args.baseline_prompt_file).read_text(encoding="utf-8"),
                )
                candidate = RunConfig(
                    name="candidate",
                    system_prompt=Path(args.candidate_prompt_file).read_text(encoding="utf-8"),
                )
                print(f"对比模式: baseline vs candidate, reps={args.reps}\n")
                print(f"成本预期: {len(tasks)} 任务 × {args.reps} reps × 2 = "
                      f"{len(tasks) * args.reps * 2} 次 LLM 调用\n")
                report = await compare(tasks, baseline, candidate, session, user_id, reps=args.reps)
                print(report.render())
            else:
                cfg = RunConfig(name="run")
                print("单配置模式\n")
                results = await run_batch(tasks, cfg, session, user_id)
                print(f"{'task':28s} {'result':>8s}  reason")
                print("-" * 74)
                for r in results:
                    print(f"{r.task[:28]:28s} {'PASS' if r.passed else 'FAIL':>8s}  {r.reason}")
                print("-" * 74)
                passed = sum(1 for r in results if r.passed)
                print(f"TOTAL: {passed}/{len(results)} 通过")
        return 0

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
