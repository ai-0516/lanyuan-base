"""Spike 2d: 统计 assistant/chunk 的 (type, blockType) 组合，并打印 text delta 结构"""
import json
from collections import Counter

from deepseek_harness import DeepSeekHarness


def main() -> None:
    counter: Counter = Counter()
    text_delta = None

    def on_notification(n) -> None:
        nonlocal text_delta
        if n.method != "session.event":
            return
        ev = n.payload.get("event") or {}
        if ev.get("type") != "assistant/chunk":
            return
        chunk = (ev.get("data") or {}).get("chunk") or {}
        counter[(chunk.get("type"), chunk.get("blockType"))] += 1
        if text_delta is None and chunk.get("type") == "text-delta":
            text_delta = json.dumps(chunk, ensure_ascii=False)[:400]

    with DeepSeekHarness() as harness:
        harness.run("请写一首关于春天的小诗，大约 50 字。", on_notification=on_notification)
    print(f"[chunk combos] {dict(counter)}")
    if text_delta:
        print(f"[text delta sample] {text_delta}")


if __name__ == "__main__":
    main()
