"""Spike 1c: SDK + bundled exe + 自定义 cordis.yml（验证 SDK 自定义配置通道）"""
import os

from deepseek_harness import DeepSeekHarness

CORDIS = os.path.join(os.path.dirname(__file__), "cordis-lanyuan.yml")


def main() -> None:
    with DeepSeekHarness(cordis=CORDIS) as harness:
        result = harness.run("你好，你是谁？")
        print(f"[result] finish_reason={result.finish_reason}")
        print(f"[result] final_response={result.final_response!r}")


if __name__ == "__main__":
    main()
