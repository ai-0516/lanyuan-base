"""Spike 2c: 打印 assistant/chunk 与 tool/call 的原始结构（翻译层需要确切的字段路径）"""
import json

from deepseek_harness import DeepSeekHarness


def main() -> None:
    shown = set()

    def on_notification(n) -> None:
        if n.method != "session.event":
            return
        ev = n.payload.get("event") or {}
        etype = ev.get("type", "?")
        if etype in ("assistant/chunk", "tool/call", "tool/result", "assistant/message") and etype not in shown:
            shown.add(etype)
            print(f"=== {etype} ===")
            print(json.dumps(ev, ensure_ascii=False)[:600])
            print()

    with DeepSeekHarness() as harness:
        result = harness.run(
            "你好！请介绍一下你自己。另外帮我查一下 2026 年 8 月 DeepSeek 发布的新模型。",
            on_notification=on_notification,
        )
    print(f"[total events] {len(result.events)}")


if __name__ == "__main__":
    main()
