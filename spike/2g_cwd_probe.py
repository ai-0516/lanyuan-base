"""Spike 2g: 定位 SDK runtime 的实际 cwd 与 session log 落盘路径"""
import os

from deepseek_harness import DeepSeekHarness


def main() -> None:
    print(f"[py cwd] {os.getcwd()}")
    with DeepSeekHarness() as harness:
        print(f"[config cwd] {harness.config.cwd}")
        print(f"[config runtime_cwd] {harness.config.runtime_cwd}")
        print(f"[config session_root] {harness.config.session_root}")
        print(f"[client proc cwd] {harness.client._proc and os.readlink(f'/proc/{harness.client._proc.pid}/cwd')}")
        result = harness.run("你好", session_id="cwd-probe")
        print(f"[done] finish={result.finish_reason} session_root={result.session_root}")
    # 找落盘
    for root, dirs, files in os.walk(os.getcwd()):
        if any("session" in f for f in files) and ".venv" not in root:
            print(f"[log file] {os.path.join(root, files[0])}")


if __name__ == "__main__":
    main()
