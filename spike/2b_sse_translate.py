"""Spike 2b (v2): FastAPI 形态 SSE 实时转发——DSH 事件 → lanyuan 前端契约

翻译映射（基于实测事件结构）：
  assistant/chunk {chunk.type}:
    text-delta       → event: token, data: {"content": <text>}
    reasoning-delta  → 丢弃（前端不渲染 thinking）
    usage/finish     → 忽略
  tool/call, tool/result → 丢弃（v1 前端不渲染工具过程；v2 可加 tool 展示）
  turn/end          → event: done
"""
import asyncio
import json
import time

from deepseek_harness import DeepSeekHarness

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def sse_stream(harness: DeepSeekHarness, prompt: str, session_id: str | None = None):
    """模拟 FastAPI StreamingResponse 生成器"""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    t0 = time.monotonic()
    token_count = 0
    first_token_at: float | None = None

    def on_notification(n) -> None:
        loop.call_soon_threadsafe(q.put_nowait, n)

    run_task = asyncio.create_task(
        asyncio.to_thread(
            harness.run, prompt, session_id=session_id, on_notification=on_notification
        )
    )

    yield f"HTTP/1.1 200 OK\nContent-Type: text/event-stream\n{SSE_HEADERS}\n\n"

    while True:
        n = await q.get()
        ts = time.monotonic() - t0
        if n.method == "session.event":
            ev = n.payload.get("event") or {}
            etype = ev.get("type")
            data = ev.get("data") or {}

            if etype == "assistant/chunk":
                chunk = data.get("chunk") or {}
                ctype = chunk.get("type")
                if ctype == "text-delta":
                    text = chunk.get("text", "")
                    if text:
                        if first_token_at is None:
                            first_token_at = ts
                        token_count += 1
                        yield f"event: token\ndata: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"
            elif etype == "turn/end":
                reason = (data.get("reason") or {}).get("kind", "completed")
                yield f"event: done\ndata: {json.dumps('')}\n\n"
                break
        elif n.method == "session.status" and n.payload.get("status") == "idle":
            yield f"event: done\ndata: {json.dumps('')}\n\n"
            break

    result = await run_task
    print(f"[stats] tokens={token_count} first_token_at={first_token_at and f'{first_token_at:.2f}s'} "
          f"total={time.monotonic()-t0:.2f}s finish={result.finish_reason}")


async def main() -> None:
    with DeepSeekHarness() as harness:
        # 第一轮：普通对话（多轮场景：同一 session 第二次 run）
        async for line in sse_stream(harness, "请用一句话介绍你自己。", session_id="spike-s1"):
            if line.startswith(("event:", "data:")):
                print(repr(line)[:100])
        print("--- 第二轮（同一 session 追问）---")
        async for line in sse_stream(harness, "刚才你说你能做什么？再说详细点。", session_id="spike-s1"):
            if line.startswith(("event:", "data:")):
                print(repr(line)[:100])


if __name__ == "__main__":
    asyncio.run(main())
