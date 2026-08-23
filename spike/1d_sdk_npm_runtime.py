"""Spike 1d: SDK 拉起 npm runtime（dsh-jsonrpc-agent）验证

验证点：SDK 通过 runtime_bin + cordis 配置拉起 npm 形态 runtime（非 bundled exe），
JSON-RPC 链路跑通真实对话。
"""
import os
import sys

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

NPM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "npm-dsh")


def main() -> None:
    runtime_bin = os.path.join(NPM_DIR, "node_modules", ".bin", "dsh-jsonrpc-agent")
    cordis = os.path.join(NPM_DIR, "cordis-jsonrpc.yml")
    print(f"[info] runtime_bin: {runtime_bin}")
    print(f"[info] cordis: {cordis}")

    env = dict(os.environ)
    env["DSH_CORDIS_CONFIG"] = cordis
    # 防残留：session 根固定在 npm-dsh 下独立目录
    env["DSH_SESSION_ROOT"] = os.path.join(NPM_DIR, ".sessions-sdk")

    config = DeepSeekHarnessConfig(
        provider="deepseek-official",
        model="deepseek-v4-flash",
        runtime_bin=runtime_bin,
        cordis=cordis,
        env=env,
        request_timeout_seconds=180,
    )
    with DeepSeekHarness(config) as harness:
        result = harness.run("用一句话介绍你自己。")
        print(f"[result] finish_reason={result.finish_reason}")
        print(f"[result] final_response={result.final_response!r}")
        print(f"[result] events={len(result.events)} notifications={len(result.notifications)}")
        print(f"[result] session_id={result.session_id}")


if __name__ == "__main__":
    main()
