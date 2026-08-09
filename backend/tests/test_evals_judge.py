"""judge 断言组件单测（#57）"""


from app.harness.evals.judge import (
    AgentTrace,
    AllOf,
    AnyOf,
    EvalContext,
    MarkerInReply,
    NoToolCalled,
    ToolCall,
    ToolCalled,
)


# ── ToolCall.from_llm：解析 LLM tool_call 事件 ───────────────────


def test_from_llm_parses_json_arguments():
    data = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "search_history", "arguments": '{"query": "旋转"}'},
    }
    tc = ToolCall.from_llm(data)
    assert tc.name == "search_history"
    assert tc.arguments == {"query": "旋转"}


def test_from_llm_tolerates_bad_json():
    data = {"function": {"name": "memory_add", "arguments": "not-json"}}
    tc = ToolCall.from_llm(data)
    assert tc.name == "memory_add"
    assert tc.arguments == {}


def _ctx(trace: AgentTrace) -> EvalContext:
    return EvalContext(trace=trace)


# ── ToolCalled ───────────────────────────────────────────────────


async def test_tool_called_hit():
    trace = AgentTrace(prompt="p", tool_calls=[ToolCall("search_history", {"query": "x"})])
    r = await ToolCalled("search_history").check(_ctx(trace))
    assert r.passed


async def test_tool_called_miss():
    trace = AgentTrace(prompt="p", tool_calls=[ToolCall("memory_get", {})])
    r = await ToolCalled("search_history").check(_ctx(trace))
    assert not r.passed
    assert "search_history" in r.reason


async def test_tool_called_params_subset_match():
    trace = AgentTrace(prompt="p", tool_calls=[
        ToolCall("search_history", {"query": "旋转", "limit": 3}),
    ])
    r = await ToolCalled("search_history", params={"query": "旋转"}).check(_ctx(trace))
    assert r.passed


async def test_tool_called_params_mismatch():
    trace = AgentTrace(prompt="p", tool_calls=[
        ToolCall("search_history", {"query": "别的", "limit": 3}),
    ])
    r = await ToolCalled("search_history", params={"query": "旋转"}).check(_ctx(trace))
    assert not r.passed
    assert "参数不匹配" in r.reason


# ── NoToolCalled / MarkerInReply ─────────────────────────────────


async def test_no_tool_called_passes_when_empty():
    trace = AgentTrace(prompt="p")
    r = await NoToolCalled().check(_ctx(trace))
    assert r.passed


async def test_no_tool_called_fails_when_calls():
    trace = AgentTrace(prompt="p", tool_calls=[ToolCall("list_posts", {})])
    r = await NoToolCalled().check(_ctx(trace))
    assert not r.passed


async def test_marker_in_reply_hit():
    trace = AgentTrace(prompt="p", final_reply="收到您的消息：你好")
    r = await MarkerInReply("收到您的消息").check(_ctx(trace))
    assert r.passed


async def test_marker_in_reply_miss():
    trace = AgentTrace(prompt="p", final_reply="完全不同的回复")
    r = await MarkerInReply("收到您的消息").check(_ctx(trace))
    assert not r.passed


# ── 组合 ─────────────────────────────────────────────────────────


async def test_all_of_requires_all():
    trace = AgentTrace(prompt="p", tool_calls=[ToolCall("search_history", {})],
                       final_reply="ok")
    j = AllOf(ToolCalled("search_history"), MarkerInReply("ok"))
    assert (await j.check(_ctx(trace))).passed

    j2 = AllOf(ToolCalled("search_history"), ToolCalled("memory_get"))
    assert not (await j2.check(_ctx(trace))).passed


async def test_any_of_passes_on_first():
    trace = AgentTrace(prompt="p", tool_calls=[ToolCall("memory_get", {})])
    j = AnyOf(ToolCalled("search_history"), ToolCalled("memory_get"))
    assert (await j.check(_ctx(trace))).passed

    j2 = AnyOf(ToolCalled("search_history"), ToolCalled("list_posts"))
    assert not (await j2.check(_ctx(trace))).passed
