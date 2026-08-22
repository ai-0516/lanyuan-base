"""Spike 4a: 多 session 并发——一个 runtime 子进程同时跑多个会话"""
import asyncio
import time

from deepseek_harness import DeepSeekHarness


async def run_one(harness: DeepSeekHarness, session_id: str, prompt: str) -> str:
    t0 = time.monotonic()
    result = await asyncio.to_thread(harness.run, prompt, session_id=session_id)
    return f"{session_id}: finish={result.finish_reason} ({time.monotonic()-t0:.1f}s) resp={result.final_response[:30]!r}"


async def main() -> None:
    with DeepSeekHarness() as harness:
        tasks = [
            run_one(harness, "conc-1", "请用一句话介绍你自己。"),
            run_one(harness, "conc-2", "请计算 17 乘以 23 等于多少，只回答数字。"),
            run_one(harness, "conc-3", "请写一句赞美秋天的诗。"),
        ]
        results = await asyncio.gather(*tasks)
        for r in results:
            print(f"[{r}]")


if __name__ == "__main__":
    asyncio.run(main())
