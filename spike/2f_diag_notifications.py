"""Spike 2f: 诊断第二次 run 的通知序列（对比第一次的尾部）"""
import asyncio
import json
import time

from deepseek_harness import DeepSeekHarness


async def dump_run(harness: DeepSeekHarness, prompt: str, session_id: str, label: str) -> None:
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    t0 = time.monotonic()

    def on_notification(n) -> None:
        loop.call_soon_threadsafe(q.put_nowait, n)

    run_task = asyncio.create_task(
        asyncio.to_thread(harness.run, prompt, session_id=session_id, on_notification=on_notification)
    )
    seq = 0
    while True:
        n = await q.get()
        ts = time.monotonic() - t0
        if n.method == "session.event":
            ev = n.payload.get("event") or {}
            etype = ev.get("type")
            data = ev.get("data") or {}
            if etype == "assistant/chunk":
                chunk = data.get("chunk") or {}
                if chunk.get("type") == "text-delta":
                    seq += 1
                    if seq <= 3 or chunk.get("text", "").startswith("好的"):
                        print(f"[{label} {ts:5.2f}s] token: {chunk.get('text')!r}")
            elif etype in ("turn/start", "turn/end", "agent/inbox/spliced"):
                print(f"[{label} {ts:5.2f}s] {etype} turn={data.get('turn')} data={json.dumps(data, ensure_ascii=False)[:300]}")
                if etype == "turn/end":
                    # drain 残留后退出
                    while not q.empty():
                        n2 = q.get_nowait()
                        m2 = n2.method
                        e2 = (n2.payload.get("event") or {}).get("type", "") if m2 == "session.event" else ""
                        print(f"[{label} drain] {m2} {e2}")
                    break
        elif n.method == "session.status":
            print(f"[{label} {ts:5.2f}s] session.status={n.payload.get('status')}")
    result = await run_task
    print(f"[{label}] done finish={result.finish_reason}")


async def main() -> None:
    with DeepSeekHarness() as harness:
        await dump_run(harness, "请用一句话介绍你自己。", "spike-s1", "R1")
        print("--- 第二轮 ---")
        await dump_run(harness, "再说详细点。", "spike-s1", "R2")


if __name__ == "__main__":
    asyncio.run(main())
