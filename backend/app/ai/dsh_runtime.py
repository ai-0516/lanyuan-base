"""DSH runtime 包装层（TECH_SPEC §3.1）：每 worker 一个常驻子进程

- SDK（DeepSeekHarness）拉起自写 bin（dsh/bin/dsh-jsonrpc-agent.js，§7.4）
- cordis 配置 = dsh/cordis-lanyuan.yml（5 插件，能力裁剪）
- 崩溃恢复：close + start（M1 基础版；session 恢复策略 M3 由 get-or-load-or-create 承接）
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

from app.core.security import get_mcp_token

logger = logging.getLogger(__name__)

# backend/app/ai/ → 仓库根 → dsh/
DSH_DIR = Path(__file__).resolve().parents[3] / "dsh"

_LLM_MODEL = os.environ.get("V2_LLM_MODEL", "deepseek-v4-flash")

# MCP server 端点默认值（§6.2：与 FastAPI 部署端口绑定；生产云托管端口非 8000
# 时用环境变量 LANYUAN_MCP_URL 覆盖——verify 脚本同源注入）
LANYUAN_MCP_URL_DEFAULT = "http://127.0.0.1:8000/mcp/"


def _runtime_env() -> dict:
    """DSH 相关环境变量（2g 教训：显式管理，不继承 shell 残留）"""
    env = dict(os.environ)
    env.setdefault("DSH_CORDIS_CONFIG", str(DSH_DIR / "cordis-lanyuan.yml"))
    env.setdefault("DSH_HOME", str(DSH_DIR / ".dsh-home"))
    env.setdefault("DSH_SESSION_ROOT", str(DSH_DIR / ".sessions"))
    # MCP 工具桥（§6.2）：MCP server 挂载在 FastAPI /mcp（streamable-http），
    # 桥插件经 HTTP 消费——URL 与 FastAPI 部署端口绑定（外部可覆盖）
    env.setdefault("LANYUAN_MCP_URL", LANYUAN_MCP_URL_DEFAULT)
    # MCP 内部认证（PR #94 review 修复）：/mcp 内部共享密钥——桥插件所有请求带
    # X-Lanyuan-Internal-Token。与 FastAPI 进程内 get_mcp_token() 同值：显式
    # env LANYUAN_MCP_TOKEN（生产）优先，未配置则进程内自动生成注入（开发零配置）。
    # 缺失 token 的桥会被 server 401 拒绝（fail-closed，见 tools/mcp_server/security.py）
    env["LANYUAN_MCP_TOKEN"] = get_mcp_token()
    return env


class DshRuntime:
    """每 worker 常驻 runtime；懒启动 + 崩溃后 close+start 恢复"""

    def __init__(self) -> None:
        self._harness: DeepSeekHarness | None = None
        # 跨线程安全：HTTP 模式下 lifespan 后台预热 vs 首请求懒启动可能并发
        # （verify 复用单例场景实测；无锁会双建 harness）
        self._lock = threading.Lock()

    @property
    def harness(self) -> DeepSeekHarness:
        with self._lock:
            if self._harness is None:
                self._harness = self._create()
                logger.info("DSH runtime 启动（runtime_bin=%s）", DSH_DIR / "bin" / "dsh-jsonrpc-agent.js")
            return self._harness

    def start(self) -> None:
        """预热并常驻 runtime（lifespan startup 调用，TECH_SPEC §3.1）。"""
        _ = self.harness

    def _create(self) -> DeepSeekHarness:
        config = DeepSeekHarnessConfig(
            provider="deepseek-official",
            model=_LLM_MODEL,
            # 自写 bin（根包 bin 不进 node_modules/.bin，直接指向脚本，§7.4）
            runtime_bin=str(DSH_DIR / "bin" / "dsh-jsonrpc-agent.js"),
            cordis=str(DSH_DIR / "cordis-lanyuan.yml"),
            env=_runtime_env(),
            request_timeout_seconds=180,
        )
        harness = DeepSeekHarness(config)
        harness.start()
        return harness

    def close(self) -> None:
        with self._lock:
            if self._harness is not None:
                try:
                    self._harness.close()
                finally:
                    self._harness = None
                logger.info("DSH runtime 已关闭")

    def run(self, prompt: str, session_id: str, on_notification=None):
        """执行一轮对话（阻塞，调用方负责 to_thread）。崩溃时 close+start 后重试一次。"""
        try:
            return self.harness.run(prompt, session_id=session_id, on_notification=on_notification)
        except Exception:
            logger.exception("DSH run 异常，重启 runtime 后重试一次")
            self.close()
            return self.harness.run(prompt, session_id=session_id, on_notification=on_notification)


# 单例（每 worker 一个；多 worker 各进程独立实例）
dsh_runtime = DshRuntime()
