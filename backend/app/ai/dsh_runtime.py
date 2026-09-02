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
from urllib.parse import unquote, urlparse

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

from app.config import settings
from app.core.security import get_mcp_token

logger = logging.getLogger(__name__)

# backend/app/ai/ → 仓库根 → dsh/（本机布局）；容器内镜像打平 backend 层级
# （Dockerfile `COPY backend/app ./app/`）parents[3] 不再指向仓库根——生产由
# Dockerfile ENV DSH_DIR 显式注入（/app/dsh，云托管可配），本机开发无 env 时
# 保持路径推导（PR #98 review 阻塞①修复）
DSH_DIR = Path(os.environ["DSH_DIR"]) if os.environ.get("DSH_DIR") else Path(__file__).resolve().parents[3] / "dsh"

_LLM_MODEL = os.environ.get("V2_LLM_MODEL", "deepseek-v4-flash")

# MCP server 端点默认值（§6.2：与 FastAPI 部署端口绑定；生产云托管端口非 8000
# 时用环境变量 LANYUAN_MCP_URL 覆盖——verify 脚本同源注入）
LANYUAN_MCP_URL_DEFAULT = "http://127.0.0.1:8000/mcp/"


def _mysql_env_from_database_url() -> dict:
    """从 DATABASE_URL 推导 DSH 侧 MySQL 连接参数（§5.2：同库同凭据）。

    生产 DATABASE_URL=`mysql+aiomysql://user:pass@host:port/db` → 拆成
    LANYUAN_MYSQL_{HOST,PORT,USER,PASSWORD,DATABASE} 注入 DSH 子进程
    （Node 侧 mysql2 经 cordis.yml env 引用读取）。非 mysql URL（SQLite
    开发）返回空——开发需显式设置 LANYUAN_MYSQL_*（如指向本地 MySQL）。
    """
    url = settings.DATABASE_URL
    if not url.startswith("mysql"):
        return {}
    parsed = urlparse(url)
    return {
        "LANYUAN_MYSQL_HOST": parsed.hostname or "127.0.0.1",
        "LANYUAN_MYSQL_PORT": str(parsed.port or 3306),
        "LANYUAN_MYSQL_USER": unquote(parsed.username or ""),
        "LANYUAN_MYSQL_PASSWORD": unquote(parsed.password or ""),
        "LANYUAN_MYSQL_DATABASE": (parsed.path or "/").lstrip("/").split("?", 1)[0],
    }


def _runtime_env() -> dict:
    """DSH 相关环境变量（2g 教训：显式管理，不继承 shell 残留）"""
    env = dict(os.environ)
    env.setdefault("DSH_CORDIS_CONFIG", str(DSH_DIR / "cordis-lanyuan.yml"))
    env.setdefault("DSH_HOME", str(DSH_DIR / ".dsh-home"))
    # jsonl persistence 条目已删（snxly review），DSH_SESSION_ROOT 无消费方，
    # 不再注入（DSH_HOME 已覆盖所有官方默认落盘路径的父目录）
    # MCP 工具桥（§6.2）：MCP server 挂载在 FastAPI /mcp（streamable-http），
    # 桥插件经 HTTP 消费——URL 与 FastAPI 部署端口绑定（外部可覆盖）
    env.setdefault("LANYUAN_MCP_URL", LANYUAN_MCP_URL_DEFAULT)
    # MCP 内部认证（PR #94 review 修复）：/mcp 内部共享密钥——桥插件所有请求带
    # X-Lanyuan-Internal-Token。与 FastAPI 进程内 get_mcp_token() 同值：显式
    # env LANYUAN_MCP_TOKEN（生产）优先，未配置则进程内自动生成注入（开发零配置）。
    # 缺失 token 的桥会被 server 401 拒绝（fail-closed，见 tools/mcp_server/security.py）
    env["LANYUAN_MCP_TOKEN"] = get_mcp_token()
    # M3（§5.2）：MySQL PersistenceBackend 连接——显式 env 优先（开发 SQLite
    # 库时手动指定），否则从 DATABASE_URL 推导（生产同库同凭据）
    for key, value in _mysql_env_from_database_url().items():
        env.setdefault(key, value)
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
