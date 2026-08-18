"""Spike 4b: runtime 子进程崩溃 → 自建重连（SDK 无自动重启，需包装）

验证：kill 子进程后下一次调用抛 TransportClosedError，捕获后 close()+start()
重新拉起 runtime，新请求正常服务。session 恢复受限（id collision 已知）——
用新 session id。
"""
import time
import uuid

from deepseek_harness import DeepSeekHarness
from deepseek_harness.errors import TransportClosedError


def sid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def main() -> None:
    with DeepSeekHarness() as harness:
        # 1. 正常跑一个任务
        r1 = harness.run("你好", session_id=sid("crash"))
        print(f"[before crash] finish={r1.finish_reason}")

        # 2. 模拟崩溃：kill runtime 子进程
        proc = harness.client._proc
        print(f"[kill] pid={proc.pid}")
        proc.kill()
        proc.wait(timeout=10)
        time.sleep(1)

        # 3. 崩溃后调用 → 预期 TransportClosedError
        try:
            harness.run("还在吗", session_id=sid("crash"))
            print("[unexpected] no error raised")
        except TransportClosedError as e:
            print(f"[crash detected] TransportClosedError: {str(e)[:60]}")

        # 4. 自建重连：close() 清理（_proc=None, _initialized=False）再 start()
        harness.close()
        harness.start()
        print(f"[restarted] new pid={harness.client._proc.pid}")

        # 5. 新 session 恢复服务
        r2 = harness.run("你好，重连成功了吗", session_id=sid("crash"))
        print(f"[after restart] finish={r2.finish_reason} resp={r2.final_response[:40]!r}")


if __name__ == "__main__":
    main()
