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
    """返回所有 JSONL 日志文件（按时间正序）"""
    return sorted(glob.glob(os.path.join(_LOG_DIR, "*.jsonl")))


def _read_entries(files: list[str]) -> list[dict]:
    """从日志文件中读取所有条目"""
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
    """打印一条日志条目"""
    rid = entry.get("id", "?")
    ts = entry.get("timestamp", "?")
    dur = entry.get("duration_ms", "?")
    sid = entry.get("session_id", "?")
    user_msg = entry.get("user_message", "?")
    err = entry.get("error")
    turns = entry.get("turns", [])

    # 新格式：从最后一轮取响应数据
    # 旧格式：直接从 response 字段取
    if turns:
        last = turns[-1]
        finish = last.get("finish_reason", "?")
        tokens = last.get("tokens", "?")
        tool_calls = last.get("tool_calls", [])
        content = last.get("content", "")
    else:
        resp = entry.get("response", {})
        finish = resp.get("finish_reason", "?")
        tokens = resp.get("tokens", "?")
        tool_calls = resp.get("tool_calls", [])
        content = resp.get("content", "")

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
        # 新格式：从 turns 拿 messages_sent
        if turns:
            msgs = turns[0].get("messages_sent", [])
        # 旧格式：直接从顶层拿
        else:
            msgs = entry.get("messages_sent", [])
        print(f"  Messages:   {len(msgs)}")
        for i, m in enumerate(msgs):
            role = m.get("role", "?")
            c = m.get("content", "")
            print(f"    [{i:02d}] {role}: {c[:200]}")

    print()


def _replay(entry: dict):
    """重新发送 messages_sent 到 DeepSeek API（非流式，省 token）"""
    turns = entry.get("turns", [])

    # 新格式：从 turns 拿
    if turns:
        # 把所有轮次的 messages 拼起来发给 LLM（重放第一轮的原始输入）
        msgs = turns[0].get("messages_sent", [])
        tools = turns[0].get("tools_sent")
    else:
        msgs = entry.get("messages_sent", [])
        tools = entry.get("tools_sent")

    if not msgs:
        print("[error] 没有 messages_sent 数据，无法重放")
        return

    import httpx
    from app.config import settings

    if not settings.DEEPSEEK_API_KEY:
        print("[error] DEEPSEEK_API_KEY 未配置，无法重放")
        return

    # 重建原始消息（截断的用占位符替换）
    reconstructed = []
    for m in msgs:
        content = m.get("content", "")
        if "(len=" in content and ", truncated)" in content:
            content = content  # 保留截断标记，但至少能看到前半部分
        reconstructed.append({"role": m["role"], "content": content})

    request_body = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": reconstructed,
        "stream": False,  # 非流式，直接看完整结果
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

    # 按 session 过滤
    if args.session is not None:
        entries = [e for e in entries if e.get("session_id") == args.session]
        if not entries:
            print(f"[info] 没有 session_id={args.session} 的记录")
            return

    # 按 ID 查找
    target = None
    if args.id:
        for e in entries:
            if e.get("id") == args.id:
                target = e
                break
        if target is None:
            print(f"[error] 未找到请求 ID: {args.id}")
            # 显示最近的 ID 列表
            print(f"\n最近的请求 ID（共 {len(entries)} 条）:")
            for e in entries[-10:]:
                print(f"  {e.get('id')}  |  session={e.get('session_id')}  |  {e.get('timestamp', '?')[:19]}")
            return

        _print_entry(target, verbose=args.verbose)
        if not args.dry_run:
            _replay(target)
        return

    # 默认：显示最近 N 条摘要
    n = min(args.latest, len(entries))
    print(f"最近的 {n} 条 LLM 请求（共 {len(entries)} 条）:\n")
    for e in entries[-n:]:
        _print_entry(e, verbose=args.verbose)

    print("---")
    print(f"提示: 用 --id <请求ID> 查看详情并重放")
    print(f"      用 --session <session_id> 过滤特定会话")


if __name__ == "__main__":
    main()
