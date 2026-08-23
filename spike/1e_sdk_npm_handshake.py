"""Spike 1e: SDK 拉起 npm runtime — 最小握手验证（initialize 往返）

验证 launch 参数组合：runtime_bin + DSH_CORDIS_CONFIG → npm runtime 进程
起来并应答 initialize（不创建 agent，避开 spine-demo npm 发布缺口）。
"""
import os

from deepseek_harness import HarnessClient, HarnessConfig

NPM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "npm-dsh")


def main() -> None:
    runtime_bin = os.path.join(NPM_DIR, "node_modules", ".bin", "dsh-jsonrpc-agent")
    cordis = os.path.join(NPM_DIR, "cordis-minimal.yml")
    env = dict(os.environ)
    env["DSH_CORDIS_CONFIG"] = cordis
    env["DSH_SESSION_ROOT"] = os.path.join(NPM_DIR, ".sessions-sdk")

    config = HarnessConfig(
        runtime_bin=runtime_bin,
        env=env,
    )
    client = HarnessClient(config)
    client.start()
    try:
        resp = client.initialize(
            cwd=NPM_DIR,
            provider="deepseek-official",
            model="deepseek-v4-flash",
        )
        print(f"[ok] initialize 握手成功: server_info={resp.server_info!r}")
        print(f"[ok] 协议版本: {resp.protocol_version!r}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
