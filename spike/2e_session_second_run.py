"""Spike 2e: 同一 session 第二次 run 报错诊断"""
import json
import traceback

from deepseek_harness import DeepSeekHarness


def main() -> None:
    with DeepSeekHarness() as harness:
        r1 = harness.run("请用一句话介绍你自己。", session_id="spike-diag")
        print(f"[r1] finish={r1.finish_reason} resp={r1.final_response[:40]!r}")
        try:
            r2 = harness.run("追问：再说详细点。", session_id="spike-diag")
            print(f"[r2] finish={r2.finish_reason} resp={r2.final_response[:40]!r}")
        except Exception:
            traceback.print_exc()
        # 直接看错误事件的 payload
        for ev in r1.events:
            if ev.get("type") in ("turn/end", "request/header"):
                print("[r1 turn/end]", json.dumps(ev, ensure_ascii=False)[:200])


if __name__ == "__main__":
    main()
