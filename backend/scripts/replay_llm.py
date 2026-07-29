#!/usr/bin/env python3
"""LLM 请求重放工具（ATOF + ATIF）

用法：
  # 查看最近 N 条请求（默认，不调 API）
  replay-llm --latest 5

  # 按请求 ID 查看详情 + messages_sent
  replay-llm --id req_20260726_123000_abc123 --verbose

  # 按请求 ID 真正调 API 重放
  replay-llm --id req_20260726_123000_abc123 --replay

  # 只取前 19 条 message 重放（复现第 18 条助手成功调工具的场景）
  replay-llm --id req_20260726_123000_abc123 --replay --truncate 19

  # 按 session_id 搜索
  replay-llm --session 42 --latest 3

  # 导出 ATIF 轨迹文件
  replay-llm --id req_20260726_123000_abc123 --export-atif trajectory.json
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

_LOG_DIR = "logs/llm-requests"

# ── ATOF 事件名 ──

_EVENT_AGENT_START = "agent:start"
_EVENT_AGENT_END = "agent:end"
_EVENT_TURN_START = "turn:start"
_EVENT_TURN_END = "turn:end"
_EVENT_LLM_START = "llm:start"
_EVENT_LLM_END = "llm:end"
_EVENT_LLM_ERROR = "llm:error"
_EVENT_TOOL_START = "tool:start"
_EVENT_TOOL_END = "tool:end"

_AGENT_NAME = "lanyuan-agent"
_AGENT_VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════
#  ATOF 读取层
# ═══════════════════════════════════════════════════════════


def _find_log_files() -> list[str]:
    return sorted(glob.glob(os.path.join(_LOG_DIR, "*.jsonl")))


def _read_atof_events(files: list[str]) -> list[dict]:
    """读取所有 ATOF 文件，返回打平的事件列表"""
    events: list[dict] = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if "event" in ev:
                        events.append(ev)
                except json.JSONDecodeError:
                    print(f"  [warn] 跳过损坏的日志行: {line[:80]}", file=sys.stderr)
    return events


def _group_by_req_id(events: list[dict]) -> dict[str, list[dict]]:
    """按 req_id 分组"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        rid = ev.get("req_id")
        if rid:
            groups[rid].append(ev)
    return dict(groups)


def _has_agent_end(evs: list[dict]) -> bool:
    return any(e.get("event") == _EVENT_AGENT_END for e in evs)


# ═══════════════════════════════════════════════════════════
#  聚合为旧格式 entry（供 _print / _replay 消费）
# ═══════════════════════════════════════════════════════════


def _aggregate_to_entry(req_events: list[dict]) -> dict:
    """将 ATOF 事件列表聚合为旧格式的 entry dict"""
    # 排序：按 ts 排序（没有 ts 的放前面）
    req_events = sorted(req_events, key=lambda e: e.get("ts", ""))

    entry: dict[str, Any] = {
        "id": "",
        "timestamp": "",
        "duration_ms": 0,
        "session_id": None,
        "user_message": "",
        "model": "",
        "api_url": "",
        "turns": [],
        "error": None,
    }

    current_turn: dict | None = None
    turns: list[dict] = []
    agent_end_ts = None

    for ev in req_events:
        event = ev.get("event")

        if event == _EVENT_AGENT_START:
            entry["id"] = ev.get("req_id", "")
            entry["timestamp"] = ev.get("ts", "")
            entry["session_id"] = ev.get("session_id")
            entry["user_message"] = ev.get("user_message", "")
            entry["model"] = ev.get("model", "")
            entry["api_url"] = ev.get("api_url", "")

        elif event == _EVENT_AGENT_END:
            entry["duration_ms"] = 0
            agent_end_ts = ev.get("ts")
            entry["error"] = ev.get("error")

        elif event == _EVENT_TURN_START:
            current_turn = {
                "messages_sent": None,
                "tools_sent": None,
                "finish_reason": "",
                "tokens": 0,
                "content": "",
                "tool_calls": [],
                "tool_results": [],
            }

        elif event == _EVENT_LLM_START:
            if current_turn is not None:
                current_turn["messages_sent"] = ev.get("messages_sent")
                current_turn["tools_sent"] = ev.get("tools_sent")

        elif event == _EVENT_LLM_END:
            if current_turn is not None:
                current_turn["finish_reason"] = ev.get("finish_reason", "")
                current_turn["tokens"] = ev.get("tokens", 0)
                current_turn["content"] = ev.get("content", "")
                current_turn["tool_calls"] = ev.get("tool_calls", [])

        elif event == _EVENT_TOOL_END:
            if current_turn is not None:
                current_turn["tool_results"].append({
                    "tool": ev.get("tool_name", ""),
                    "tool_call_id": ev.get("tool_call_id", ""),
                    "result": ev.get("result", ""),
                    "status": ev.get("status", "ok"),
                })

        elif event == _EVENT_TURN_END:
            if current_turn is not None:
                turns.append(current_turn)
                current_turn = None

    entry["turns"] = turns

    # 尝试计算 duration_ms（agent:end ts - agent:start ts）
    if entry["timestamp"] and agent_end_ts:
        try:
            t_start = datetime.fromisoformat(entry["timestamp"])
            t_end = datetime.fromisoformat(agent_end_ts)
            entry["duration_ms"] = int((t_end - t_start).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    return entry


# ═══════════════════════════════════════════════════════════
#  ATIF v1.7 构建
# ═══════════════════════════════════════════════════════════


def _extract_user_messages(messages_sent: list[dict]) -> list[str]:
    """从 messages_sent 中提取所有用户消息（最后一个 user role 消息是新增的）"""
    return [
        m.get("content", "")
        for m in messages_sent
        if m.get("role") == "user"
    ]


def _extract_system_prompt(messages_sent: list[dict]) -> str:
    for m in messages_sent:
        if m.get("role") == "system":
            return m.get("content", "")
    return ""


def _build_atif(req_events: list[dict]) -> dict:
    """将 ATOF 事件列表构建为 ATIF v1.7 格式的轨迹"""
    req_events = sorted(req_events, key=lambda e: e.get("ts", ""))

    # 提取元数据
    agent_start = next((e for e in req_events if e.get("event") == _EVENT_AGENT_START), {})
    agent_end = next((e for e in req_events if e.get("event") == _EVENT_AGENT_END), {})

    req_id = agent_start.get("req_id", "")
    session_id = str(agent_start.get("session_id", "")) if agent_start.get("session_id") is not None else None
    model_name = agent_start.get("model", "")
    user_message = agent_start.get("user_message", "")

    # 按 turn 分组事件
    turns_events: dict[int, list[dict]] = defaultdict(list)
    for ev in req_events:
        turn = ev.get("turn")
        if turn is not None:
            turns_events[turn].append(ev)

    steps: list[dict] = []
    step_id = 0

    # 构建 tool_calls 到 tool_results 的映射（按 tool_call_id，跨 turn）
    tool_results_by_id: dict[str, dict] = {}
    for ev in req_events:
        if ev.get("event") == _EVENT_TOOL_END:
            tcid = ev.get("tool_call_id", "")
            if tcid:
                tool_results_by_id[tcid] = ev

    sorted_turns = sorted(turns_events.keys())
    prev_messages_sent: list[dict] | None = None

    for turn_idx in sorted_turns:
        evs = turns_events[turn_idx]

        # 找 llm:start 获取 messages_sent
        llm_start = next((e for e in evs if e.get("event") == _EVENT_LLM_START), None)
        llm_end = next((e for e in evs if e.get("event") == _EVENT_LLM_END), None)

        messages_sent = llm_start.get("messages_sent") if llm_start else None

        if messages_sent:
            # 首次：提取 system prompt + 第一条 user message
            if prev_messages_sent is None:
                sys_prompt = _extract_system_prompt(messages_sent)
                if sys_prompt:
                    step_id += 1
                    steps.append({
                        "step_id": step_id,
                        "source": "system",
                        "message": sys_prompt,
                    })

                # 第一条用户消息
                if user_message:
                    step_id += 1
                    steps.append({
                        "step_id": step_id,
                        "source": "user",
                        "message": user_message,
                    })
            else:
                # 后续 turn：找新增的用户消息（messages_sent 中最后一个 user 消息）
                curr_users = _extract_user_messages(messages_sent)
                prev_users = _extract_user_messages(prev_messages_sent)
                new_users = curr_users[len(prev_users):]
                for msg in new_users:
                    step_id += 1
                    steps.append({
                        "step_id": step_id,
                        "source": "user",
                        "message": msg,
                    })

        # Agent step
        if llm_end:
            step_id += 1
            agent_step: dict[str, Any] = {
                "step_id": step_id,
                "source": "agent",
                "message": llm_end.get("content", ""),
            }

            # tool_calls
            tool_calls = llm_end.get("tool_calls", [])
            if tool_calls:
                agent_step["tool_calls"] = [
                    {
                        "tool_call_id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", "{}"),
                        },
                    }
                    for tc in tool_calls
                ]

                # observation: 按 tool_call_id 匹配结果
                results = []
                for tc in tool_calls:
                    tcid = tc.get("id", "")
                    tres = tool_results_by_id.get(tcid)
                    if tres:
                        results.append({
                            "content": tres.get("result", ""),
                            "source_call_id": tcid,
                        })
                if results:
                    agent_step["observation"] = {"result": results}

            # metrics
            usage = llm_end.get("usage")
            tokens = llm_end.get("tokens", 0)
            if usage:
                agent_step["metrics"] = {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                }
            elif tokens:
                agent_step["metrics"] = {
                    "prompt_tokens": 0,
                    "completion_tokens": tokens,
                }

            steps.append(agent_step)

        prev_messages_sent = messages_sent

    # final_metrics
    total_prompt = 0
    total_completion = 0
    for s in steps:
        m = s.get("metrics")
        if m:
            total_prompt += m.get("prompt_tokens", 0)
            total_completion += m.get("completion_tokens", 0)

    # duration
    duration_ms = 0
    ts_start = agent_start.get("ts", "")
    ts_end = agent_end.get("ts", "")
    if ts_start and ts_end:
        try:
            t_start = datetime.fromisoformat(ts_start)
            t_end = datetime.fromisoformat(ts_end)
            duration_ms = int((t_end - t_start).total_seconds() * 1000)
        except (ValueError, TypeError):
            pass

    atif: dict[str, Any] = {
        "schema_version": "ATIF-v1.7",
        "trajectory_id": req_id,
        "agent": {
            "name": _AGENT_NAME,
            "version": _AGENT_VERSION,
            "model_name": model_name,
        },
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_steps": len(steps),
        },
        "duration_ms": duration_ms,
    }

    if session_id:
        atif["session_id"] = session_id

    err = agent_end.get("error")
    if err:
        atif["error"] = err

    return atif


# ═══════════════════════════════════════════════════════════
#  显示 / 重放（原有功能）
# ═══════════════════════════════════════════════════════════


def _print_entry(entry: dict, verbose: bool = False):
    rid = entry.get("id", "?")
    ts = entry.get("timestamp", "?")
    dur = entry.get("duration_ms", "?")
    sid = entry.get("session_id", "?")
    model = entry.get("model", "?")
    api_url = entry.get("api_url", "?")
    user_msg = entry.get("user_message", "?")
    err = entry.get("error")
    turns = entry.get("turns", [])

    last = turns[-1] if turns else {}
    finish = last.get("finish_reason", "?")
    tokens = last.get("tokens", "?")
    tool_calls = last.get("tool_calls", [])
    content = last.get("content", "")

    print(f"── {rid} ─────────────────────────────────")
    print(f"  Time:       {ts}")
    print(f"  Duration:   {dur}ms")
    print(f"  Session:    {sid}")
    print(f"  Model:      {model}")
    print(f"  API:        {api_url}")
    print(f"  Finish:     {finish}")
    print(f"  Tokens:     {tokens}")
    print(f"  ToolCalls:  {len(tool_calls)}")
    if tool_calls:
        for tc in tool_calls:
            name = tc.get("function", {}).get("name", "?")
            args = tc.get("function", {}).get("arguments", "{}")
            print(f"    → {name}({args[:120]})")
    print(f"  UserMsg:    {user_msg[:200]}")
    if err:
        print(f"  Error:      {err}")
    if content:
        print(f"  Reply:      {content[:300]}")

    if verbose or err:
        msgs = turns[0].get("messages_sent", []) if turns else []
        print(f"  Messages:   {len(msgs)}")
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            c = m.get("content", "")
            print(f"    [{i:02d}] {role}: {c[:200]}")

    print()


def _replay(entry: dict, truncate: int | None = None):
    """重放：Agent Loop（非流式，支持多轮工具调用）"""
    turns = entry.get("turns", [])
    msgs = turns[0].get("messages_sent", []) if turns else []
    tools = turns[0].get("tools_sent") if turns else None

    if truncate is not None:
        msgs = msgs[:truncate]

    if not msgs:
        print("[error] 没有 messages_sent 数据，无法重放")
        return

    import httpx
    from app.config import settings

    model = entry.get("model") or settings.DEEPSEEK_MODEL
    api_url = entry.get("api_url") or f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
    deepseek_api_key = settings.DEEPSEEK_API_KEY

    if not deepseek_api_key:
        print("[error] 未找到 DEEPSEEK_API_KEY，无法重放")
        return

    reconstructed = []
    for m in msgs:
        entry: dict = {"role": m["role"]}
        if m["role"] == "tool":
            entry["tool_call_id"] = m.get("tool_call_id", "")
            entry["content"] = m.get("content", "")
        elif m["role"] == "assistant" and m.get("tool_calls"):
            entry["content"] = m.get("content") or None
            entry["tool_calls"] = m["tool_calls"]
        else:
            entry["content"] = m.get("content", "")
        reconstructed.append(entry)
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    print(f">>> 重放 {entry.get('id', '?')} → {model}")
    label = f" (截取前 {truncate} 条)" if truncate else ""
    print(f">>> messages={len(reconstructed)}{label}, tools={'yes' if tools else 'no'}")
    print()

    max_turns = 10
    turn = 0
    for turn in range(max_turns):
        request_body = {
            "model": model,
            "messages": reconstructed,
            "stream": False,
        }
        if tools:
            request_body["tools"] = tools

        try:
            resp = httpx.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json=request_body,
                timeout=60.0,
            )
        except Exception as e:
            print(f"[error] API 请求失败: {e}")
            return

        if resp.status_code != 200:
            print(f"[error] API 返回 {resp.status_code}: {resp.text[:500]}")
            return

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        finish = choice.get("finish_reason", "?")
        msg = choice.get("message", {})
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        usage = data.get("usage", {})
        for k in total_usage:
            total_usage[k] = total_usage.get(k, 0) + usage.get(k, 0)

        print(f"  [Turn {turn + 1}] finish_reason: {finish}  tokens: {usage.get('completion_tokens', '?')}")
        if content:
            print(f"  content: {content[:400]}")
        if tool_calls:
            print(f"  tool_calls: {len(tool_calls)}")
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "?")
                args = tc.get("function", {}).get("arguments", "{}")
                print(f"    → {name}({args[:200]})")

        # 无工具调用 → 结束
        if not tool_calls:
            break

        # 回填 assistant + tool 消息
        reconstructed.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [dict(tc) for tc in tool_calls],
        })
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "?")
            tool_id = tc.get("id", "")
            # 模拟工具执行结果（不真正调 DB）
            if tool_name == "create_post":
                result = json.dumps({
                    "success": True,
                    "post_id": 999,
                    "message": "帖子发布成功（重放模拟）",
                }, ensure_ascii=False)
            else:
                result = json.dumps({
                    "success": True,
                    "message": f"工具 {tool_name} 执行成功（重放模拟）",
                }, ensure_ascii=False)
            reconstructed.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": result,
            })
            print(f"    ↳ result: {result[:120]}")
        print()

    print(f"  --- 总计 {turn + 1} 轮, total_tokens={total_usage}")


# ═══════════════════════════════════════════════════════════
#  ATIF 导出
# ═══════════════════════════════════════════════════════════


def _export_atif(atif: dict, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(atif, f, ensure_ascii=False, indent=2)
    print(f"[ok] ATIF 轨迹已导出到 {output_path}")


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="LLM 请求重放工具（ATOF + ATIF）")
    parser.add_argument("--id", help="请求 ID")
    parser.add_argument("--latest", type=int, default=5, help="显示最近 N 条")
    parser.add_argument("--session", type=int, help="按 session_id 过滤")
    parser.add_argument("--replay", action="store_true", help="真正调 DeepSeek API 重放（默认不调）")
    parser.add_argument("--truncate", type=int, help="只取前 N 条 message 重放")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示完整 messages_sent")
    parser.add_argument("--export-atif", metavar="FILE", help="导出 ATIF v1.7 轨迹到指定文件（不调 API）")
    args = parser.parse_args()

    files = _find_log_files()
    if not files:
        print(f"[info] 日志目录 {_LOG_DIR} 为空，还没 LLM 请求记录")
        return

    raw_events = _read_atof_events(files)
    if not raw_events:
        print("[info] 日志中没有有效条目")
        return

    by_req = _group_by_req_id(raw_events)

    # 按 session 过滤
    req_ids = sorted(by_req.keys())
    if args.session is not None:
        matched = {}
        for rid, evs in by_req.items():
            for ev in evs:
                if ev.get("event") == _EVENT_AGENT_START and ev.get("session_id") == args.session:
                    matched[rid] = evs
                    break
        if not matched:
            print(f"[info] 没有 session_id={args.session} 的记录")
            return
        by_req = matched
        req_ids = sorted(by_req.keys())

    target = None
    if args.id:
        if args.id in by_req:
            target = (args.id, by_req[args.id])
        if target is None:
            print(f"[error] 未找到请求 ID: {args.id}")
            all_ids = [rid for rid in req_ids]
            print(f"\n最近的请求 ID（共 {len(all_ids)} 条）:")
            for rid in all_ids[-10:]:
                evs = by_req[rid]
                agent_start = next((e for e in evs if e.get("event") == _EVENT_AGENT_START), {})
                sid = agent_start.get("session_id", "?")
                ts = agent_start.get("ts", "?")[:19]
                complete = "✓" if _has_agent_end(evs) else "✗"
                print(f"  {rid}  |  session={sid}  |  {ts}  {complete}")
            return

        rid, evs = target
        entry = _aggregate_to_entry(evs)
        _print_entry(entry, verbose=args.verbose)

        if args.replay:
            _replay(entry, truncate=args.truncate)

        if args.export_atif:
            atif = _build_atif(evs)
            _export_atif(atif, args.export_atif)

        return

    # 无 --id：显示最近 N 条
    req_ids_sorted = sorted(by_req.keys())
    n = min(args.latest, len(req_ids_sorted))
    print(f"最近的 {n} 条 LLM 请求（共 {len(req_ids_sorted)} 条）:\n")
    for rid in req_ids_sorted[-n:]:
        evs = by_req[rid]
        entry = _aggregate_to_entry(evs)
        _print_entry(entry, verbose=args.verbose)

    print("---")
    print("提示: replay-llm --id <请求ID>                               # 查看详情")
    print("      replay-llm --id <请求ID> --replay                      # 重放调 API")
    print("      replay-llm --id <ID> --replay --truncate 19            # 截取前19条重放")
    print("      replay-llm --id <ID> --export-atif trajectory.json     # 导出 ATIF 轨迹")
    print("      replay-llm --session <ID> --latest 3                   # 按 session 筛")


if __name__ == "__main__":
    main()
