#!/usr/bin/env python3
"""LLM 请求重放工具

用法：
  # 查看最近 N 条请求
  uv run python scripts/replay_llm.py --latest 5

  # 按请求 ID 查看详情（不调 API）
  uv run python scripts/replay_llm.py --id req_20260726_123000_abc123 --dry-run

  # 按请求 ID 重放（再次调 DeepSeek API）
  uv run python scripts/replay_llm.py --id req_20260726_123000_abc123

  # 按 session_id 搜索
  uv run python scripts/replay_llm.py --session 42 --latest 3
"""

import argparse
import glob
import json
import os
import sys

_LOG_DIR = "logs/llm-requests"


def _find_log_files() -> list[str]:
    return sorted(glob.glob(os.path.join(_LOG_DIR, "*.jsonl")))


def _read_entries(files: list[str]) -> list[dict]:
    entries = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        print(f"  [warn] 跳过损坏的日志行: {line[:80]}", file=sys.stderr)
    return entries


def _print_entry(entry: dict, verbose: bool = False):
    rid = entry.get("id", "?")
    ts = entry.get("timestamp", "?")
    dur = entry.get("duration_ms", "?")
    sid = entry.get("session_id", "?")
    user_msg = entry.get("user_message", "?")
    err = entry.get("error")
    turns = entry.get("turns", [])

    # 从最后一轮取响应数据
    last = turns[-1] if turns else {}
    finish = last.get("finish_reason", "?")
    tokens = last.get("tokens", "?")
    tool_calls = last.get("tool_calls", [])
    content = last.get("content", "")

    print(f"── {rid} ─────────────────────────────────")
    print(f"  Time:       {ts}")
    print(f"  Duration:   {dur}ms")
    print(f"  Session:    {sid}")
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


def _replay(entry: dict):
    """重新发送 messages_sent 到 DeepSeek API（非流式，省 token）"""
    turns = entry.get("turns", [])
    msgs = turns[0].get("messages_sent", []) if turns else []
    tools = turns[0].get("tools_sent") if turns else None

    if not msgs:
        print("[error] 没有 messages_sent 数据，无法重放")
        return

    import httpx
    from app.config import settings

    if not settings.DEEPSEEK_API_KEY:
        print("[error] DEEPSEEK_API_KEY 未配置，无法重放")
        return

    reconstructed = [{"role": m["role"], "content": m.get("content", "")} for m in msgs]

    request_body = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": reconstructed,
        "stream": False,
    }
    if tools:
        request_body["tools"] = tools

    print(f">>> 重放 {entry.get('id', '?')} → {settings.DEEPSEEK_MODEL}")
    print(f">>> messages={len(reconstructed)}, tools={'yes' if tools else 'no'}")
    print()

    try:
        resp = httpx.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
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

    print(f"  finish_reason: {finish}")
    print(f"  content:       {msg.get('content', '')[:500]}")
    if msg.get("tool_calls"):
        print(f"  tool_calls:    {len(msg['tool_calls'])}")
        for tc in msg["tool_calls"]:
            name = tc.get("function", {}).get("name", "?")
            args = tc.get("function", {}).get("arguments", "{}")
            print(f"    → {name}({args[:200]})")
    else:
        print(f"  tool_calls:    0")

    usage = data.get("usage", {})
    print(f"  usage:         {json.dumps(usage)}")


def main():
    parser = argparse.ArgumentParser(description="LLM 请求重放工具")
    parser.add_argument("--id", help="请求 ID")
    parser.add_argument("--latest", type=int, default=5, help="显示最近 N 条")
    parser.add_argument("--session", type=int, help="按 session_id 过滤")
    parser.add_argument("--dry-run", action="store_true", help="只查看，不调 API")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示完整 messages_sent")
    args = parser.parse_args()

    files = _find_log_files()
    if not files:
        print(f"[info] 日志目录 {_LOG_DIR} 为空，还没 LLM 请求记录")
        return

    entries = _read_entries(files)
    if not entries:
        print("[info] 日志中没有有效条目")
        return

    if args.session is not None:
        entries = [e for e in entries if e.get("session_id") == args.session]
        if not entries:
            print(f"[info] 没有 session_id={args.session} 的记录")
            return

    target = None
    if args.id:
        for e in entries:
            if e.get("id") == args.id:
                target = e
                break
        if target is None:
            print(f"[error] 未找到请求 ID: {args.id}")
            print(f"\n最近的请求 ID（共 {len(entries)} 条）:")
            for e in entries[-10:]:
                print(f"  {e.get('id')}  |  session={e.get('session_id')}  |  {e.get('timestamp', '?')[:19]}")
            return

        _print_entry(target, verbose=args.verbose)
        if not args.dry_run:
            _replay(target)
        return

    n = min(args.latest, len(entries))
    print(f"最近的 {n} 条 LLM 请求（共 {len(entries)} 条）:\n")
    for e in entries[-n:]:
        _print_entry(e, verbose=args.verbose)

    print("---")
    print("提示: 用 --id <请求ID> 查看详情并重放")
    print("      用 --session <session_id> 过滤特定会话")


if __name__ == "__main__":
    main()
