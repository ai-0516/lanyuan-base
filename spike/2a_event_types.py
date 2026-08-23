"""Spike 2a: 事件类型普查——SDK 实时通知流里到底有哪些事件

目标：确认 assistant/chunk（逐 token）、tool/*、turn/* 等事件在
on_notification 回调中的实际形态，为 SSE 转发设计事件翻译层。
"""
import time
from collections import Counter

from deepseek_harness import DeepSeekHarness


def main() -> None:
    t0 = time.monotonic()
    type_counter: Counter = Counter()
    chunk_count = 0
    first_token_at = None

    def on_notification(n) -> None:
        nonlocal chunk_count, first_token_at
        if n.method == "session.event":
            ev = n.payload.get("event") or {}
            etype = ev.get("type", "?")
            type_counter[etype] += 1
            if etype == "assistant/chunk":
                chunk_count += 1
                if first_token_at is None:
                    first_token_at = time.monotonic() - t0
        else:
            type_counter[f"NOTIFY:{n.method}"] += 1

    with DeepSeekHarness() as harness:
        result = harness.run(
            "请写一段 200 字左右的自我介绍，介绍一下你自己能做什么。",
            on_notification=on_notification,
        )

    total = time.monotonic() - t0
    print(f"[total] {total:.2f}s, chunks={chunk_count}, first_chunk_at={first_token_at and f'{first_token_at:.2f}s'}")
    print(f"[types] {dict(type_counter)}")
    print(f"[final] {result.final_response[:80]!r}")


if __name__ == "__main__":
    main()
