"""
message:start 边界事件触发验证（PR #23）

验证每条 AI 回复的 message:start 边界触发行为（统一协议，用户方案）：
1. 正常两轮（首轮有文字+tool_call，第二轮有文字）→ 每轮各 1 次
2. 首轮纯 tool_call 无文字，第二轮有文字 → 仅第二轮 1 次
3. 单轮回复 → 1 次（含 turn=0，前端据此创建气泡）
4. fallback 多轮：turn=0 正常文字，turn=1 触发 fallback 降级 → 2 次

用法：
    uv run python scripts/review/multi_message_display/review.py

场景 4 是修复目标（issue #22 在 fallback 路径的复现验证）。
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def summarize(events, label):
    print(f"\n--- {label} ---")
    for e, d in events:
        if e == "token":
            print(f"  token: {str(d)[:30]}")
        elif e == "message:start":
            print("  ★ message:start")
        elif e == "tool_call":
            print(f"  tool_call: {d.get('function',{}).get('name','?')}")
        elif e in ("done", "error", "fallback"):
            print(f"  {e}: {str(d)[:40]}")
    starts = [i for i, (e, _) in enumerate(events) if e == "message:start"]
    tokens = [(i, d) for i, (e, d) in enumerate(events) if e == "token"]
    print(f"  → message:start 次数: {len(starts)} | token 次数: {len(tokens)}")
    return starts, tokens


async def run_agent(fake_source, label):
    import app.harness.streaming as S
    from app.harness.agent import AIAgent

    async def _ok_executor(*a, **k):
        return "ok"

    original = S.mock_chat
    S.mock_chat = fake_source
    try:
        agent = AIAgent(
            tools=[{"type": "function", "function": {"name": "dummy"}}],
            tool_executor=_ok_executor,
        )
        events = []
        async for event, data in agent.run([{"role": "user", "content": "你好"}]):
            events.append((event, data))
        return summarize(events, label)
    finally:
        S.mock_chat = original


async def main():
    print("=" * 64)
    print("  PR #23 message:start 触发验证")
    print("=" * 64)

    results = {}

    # 场景 1: 正常两轮
    async def multi_turn(messages, **kw):
        if any(m.get("role") == "tool" for m in messages):
            yield ("token", "第二轮：查到了")
            yield ("done", "")
        else:
            yield ("token", "第一轮：我来查一下")
            yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

    starts, tokens = await run_agent(multi_turn, "场景1: 两轮（首轮文字+tool_call）")
    results["场景1"] = (
        len(starts) == 2 and len(tokens) == 2
        and starts[0] < tokens[0][0] and starts[1] < tokens[1][0]
    )

    # 场景 2: 首轮纯 tool_call
    async def tool_only_first(messages, **kw):
        if any(m.get("role") == "tool" for m in messages):
            yield ("token", "第二轮：查到了")
            yield ("done", "")
        else:
            yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

    starts, tokens = await run_agent(tool_only_first, "场景2: 首轮纯 tool_call")
    results["场景2"] = len(starts) == 1 and len(tokens) == 1 and starts[0] < tokens[0][0]

    # 场景 3: 单轮
    async def single_turn(messages, **kw):
        yield ("token", "你好")
        yield ("done", "")

    starts, tokens = await run_agent(single_turn, "场景3: 单轮")
    results["场景3"] = len(starts) == 1 and len(tokens) == 1 and starts[0] < tokens[0][0]

    # 场景 4: turn=0 正常文字，turn=1 fallback
    async def fallback_second(messages, **kw):
        if any(m.get("role") == "tool" for m in messages):
            yield ("fallback", {"message": "您好，AI 暂时无法回复您的消息，请稍后重试。"})
        else:
            yield ("token", "第一轮：我来查一下")
            yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

    starts, tokens = await run_agent(fallback_second, "场景4: 第二轮 fallback 降级")
    results["场景4"] = (
        len(starts) == 2 and len(tokens) == 2
        and starts[0] < tokens[0][0] and starts[1] < tokens[1][0]
    )

    print(f"\n\n{'='*64}")
    print("  验证汇总")
    print(f"{'='*64}")
    for label, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {label}")


if __name__ == "__main__":
    asyncio.run(main())
