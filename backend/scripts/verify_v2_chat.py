"""v2 M1 全链路验证：SDK 拉起自写 runtime → 对话 → 事件层过滤（TECH_SPEC §4/§7）

验证点（M1 验收）：
1. 裁剪配置（5 插件、无 bash/subprocess/fs）runtime 正常
2. 白名单过滤：text-delta/step/start/user/message/turn 转发，tool/reasoning 不转发
3. turn/end 收尾（done）

运行：unset DSH_SESSION_ROOT DSH_HOME DSH_CWD && export DEEPSEEK_API_KEY=***
      .venv/bin/python verify_v2_chat.py
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.ai.dsh_runtime import dsh_runtime, DSH_DIR
from app.ai.event_layer import should_forward


def main() -> None:
    print(f"[info] runtime_bin: {DSH_DIR / 'bin/dsh-jsonrpc-agent.js'}")
    print(f"[info] cordis: {DSH_DIR / 'cordis-lanyuan.yml'}")

    forwarded: list[dict] = []
    filtered: list[str] = []

    def on_notification(n) -> None:
        if n.method == "session.event":
            event = n.payload.get("event") or {}
            if should_forward(event):
                forwarded.append(event)
            else:
                filtered.append(event.get("type", "?"))

    # 随机 session_id：避免重复运行踩 spike 2 的 id collision（新 runtime 进程
    # + 磁盘已有同 id 日志；review #92 建议 3）
    session_id = f"verify-{uuid.uuid4().hex[:8]}"
    print(f"[info] session_id: {session_id}")
    result = dsh_runtime.run("你好，简单介绍一下你自己", session_id, on_notification)
    print(f"[result] finish_reason={result.finish_reason}")
    print(f"[result] final_response={result.final_response!r}")

    types = [e.get("type") for e in forwarded]
    print(f"[forwarded] {len(forwarded)} 事件: {types}")
    print(f"[filtered] {len(filtered)} 事件: {set(filtered)}")

    text = "".join(
        (e.get("data") or {}).get("chunk", {}).get("text", "")
        for e in forwarded
        if e.get("type") == "assistant/chunk"
    )
    # 空回复自动重试一次（deepseek-v4-flash 偶发空回复；review #92 建议 7）
    if not text.strip():
        print("[warn] 首轮空回复，重试一次…")
        forwarded.clear()
        filtered.clear()
        result = dsh_runtime.run("你好，简单介绍一下你自己", session_id, on_notification)
        types = [e.get("type") for e in forwarded]
        text = "".join(
            (e.get("data") or {}).get("chunk", {}).get("text", "")
            for e in forwarded
            if e.get("type") == "assistant/chunk"
        )
        print(f"[retry text-delta 拼装] {len(text)} 字符")
        print(f"[retry text] {text[:120]!r}")

    print(f"[text-delta 拼装] {len(text)} 字符")
    print(f"[text] {text[:120]!r}")
    assert "turn/end" in types, "缺 turn/end（done 判定失败）"
    assert text.strip(), "无正文输出"
    assert "tool/call" not in types and "tool/result" not in types, "tool 事件不应转发"
    bad = [
        e.get("data", {}).get("chunk", {}).get("type")
        for t, e in zip(types, forwarded)
        if t == "assistant/chunk" and e.get("data", {}).get("chunk", {}).get("type") != "text-delta"
    ]
    assert not bad, f"非 text-delta chunk 不应转发: {bad}"
    print("\n✅ M1 全链路验证通过")


if __name__ == "__main__":
    main()
