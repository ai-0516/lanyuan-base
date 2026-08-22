"""Spike 2h: 会话历史注入方案验证

场景：lanyuan 用户会话跨进程恢复。DSH session/prompt 只接受 user 消息的
content blocks（无 role），验证「历史文本拼成多块注入 + 新问题」是否能让
agent 理解上下文（v2 候选方案：MySQL 历史 → 注入新 DSH session）。
"""
import asyncio
import json
import time

from deepseek_harness import DeepSeekHarness


async def main() -> None:
    with DeepSeekHarness() as harness:
        # 模拟：历史对话（两条）+ 新问题（一条）
        history_blocks = [
            {"type": "text", "text": "[历史对话 1/2] 用户：我想给家里的地暖调低温度。"},
            {"type": "text", "text": "[历史对话 2/2] AI：好的，已帮您把地暖从 25°C 调到 22°C。还有其他需要吗？"},
            {"type": "text", "text": "新问题：我刚才把地暖调到多少度了？"},
        ]
        t0 = time.monotonic()
        result = harness.run(history_blocks, session_id="ly-inject-demo")
        print(f"[inject-demo] finish={result.finish_reason} ({time.monotonic()-t0:.1f}s)")
        print(f"[reply] {result.final_response[:200]!r}")


if __name__ == "__main__":
    asyncio.run(main())
