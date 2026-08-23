"""Spike 3b: MCP 工具桥全链路驱动（补齐实验 3 缺的拉起方）

2026-08-18 实验 3 只有被拉起方 3_lanyuan_mcp_server.py（Python MCP server），
拉起方（SDK + cordis 配 mcp-client + run prompt）当时是临时命令跑的没留脚本。
本脚本补齐：SDK 拉起 npm runtime（cordis-mcp.yml 含 mcp-lanyuan 条目），
agent 真实调用 mcp__lanyuan__search_history 并正确总结 → 实验 3 完整可复现。

运行前：
  unset DSH_SESSION_ROOT DSH_HOME DSH_CWD && export DEEPSEEK_API_KEY=***
  .venv/bin/python 3b_mcp_dsh_drive.py
"""
import os

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

NPM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "npm-dsh")


def main() -> None:
    runtime_bin = os.path.join(NPM_DIR, "node_modules", ".bin", "dsh-jsonrpc-agent")
    cordis = os.path.join(NPM_DIR, "cordis-mcp.yml")
    print(f"[info] runtime_bin: {runtime_bin}")
    print(f"[info] cordis: {cordis}")

    env = dict(os.environ)
    env["DSH_CORDIS_CONFIG"] = cordis
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
        result = harness.run(
            "用户之前想把家里的地暖调低温度。请用 search_history 搜一下他的历史对话，"
            "看看上次调到了多少度，并总结结果。"
        )
        print(f"[result] finish_reason={result.finish_reason}")
        print(f"[result] final_response={result.final_response!r}")

        tool_calls = [ev for ev in result.events if ev.get("type") == "tool/call"]
        tool_results = [ev for ev in result.events if ev.get("type") == "tool/result"]
        print(f"[result] tool_calls={len(tool_calls)} tool_results={len(tool_results)}")
        for tc in tool_calls:
            name = (tc.get("data") or {}).get("name", "?")
            args = (tc.get("data") or {}).get("arguments", {})
            print(f"  [tool/call] {name} args={args}")
        print(f"[result] events={len(result.events)}")


if __name__ == "__main__":
    main()
