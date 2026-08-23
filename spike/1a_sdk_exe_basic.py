"""Spike 1a: SDK + bundled exe 零配置链路验证"""
import os
import sys

from deepseek_harness import DeepSeekHarness


def main() -> None:
    with DeepSeekHarness() as harness:
        print(f"[info] runtime bin: {harness.client._default_launch_args()}")
        result = harness.run("用一句话介绍你自己。")
        print(f"[result] finish_reason={result.finish_reason}")
        print(f"[result] final_response={result.final_response!r}")
        print(f"[result] events={len(result.events)} notifications={len(result.notifications)}")
        print(f"[result] session_id={result.session_id}")


if __name__ == "__main__":
    main()
