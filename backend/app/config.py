"""应用配置管理（环境变量）"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用
    APP_NAME: str = "兰园公共底座"
    DEBUG: bool = True

    # 数据库 - 默认使用 SQLite 开发，生产用 MySQL
    DATABASE_URL: str = "sqlite+aiosqlite:///./lanyuan.db"

    # JWT
    JWT_SECRET_KEY: str = "lanyuan-dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_HOURS: int = 8760  # 365 days

    # DeepSeek API
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    # 上下文压缩（#8）— 生产调优直接改环境变量，无需改代码
    COMPACT_MAX_MESSAGES: int = 50            # L1: 消息数超过则裁剪中间
    COMPACT_KEEP_HEAD: int = 3                # L1/L4: 头部保留条数
    COMPACT_KEEP_RECENT_TOOL_RESULTS: int = 3 # L2: 保留最近 N 个 tool 结果
    COMPACT_TOOL_RESULT_SNIP_LENGTH: int = 120  # L2: 旧 tool 结果超此长度才占位
    COMPACT_THRESHOLD: int = 60_000           # L4: 字符数估算阈值（agent 内 llm 层压缩用，≈30K~50K token）
    COMPACT_TOKEN_THRESHOLD: int = 40_000     # rotation 超限阈值（token，用 llm_usage 精确值判断，见 TECH_SPEC 8.3）
    COMPACT_KEEP_TAIL: int = 5                # L4/reactive: 尾部保留条数（含最新 user 消息）
    COMPACT_SUMMARY_INPUT_LIMIT: int = 80_000 # 发给摘要 LLM 的对话截断（字符）

    # 跨会话记忆（#9）
    MEMORY_MAX_PER_USER: int = 30             # 每用户记忆条数上限，超限触发 LLM 合并
    MEMORY_INDEX_LIMIT: int = 30              # 注入 system prompt 的索引条数上限

    # 微信 (开发环境模拟)
    WECHAT_APPID: str = "wx_dev_appid"
    WECHAT_SECRET: str = "wx_dev_secret"

    # 云存储 (开发环境本地存储)
    UPLOAD_DIR: str = "./uploads"

    # 日志
    LOG_LEVEL: str = "INFO"                      # DEBUG / INFO / WARNING / ERROR
    LOG_TARGET: str = "local"                    # local | oss
    LOG_DIR: str = "./logs"                      # LOG_TARGET=local 时日志文件目录

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
