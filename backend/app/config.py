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
    JWT_EXPIRES_HOURS: int = 168  # 7 days

    # DeepSeek API
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"

    # 微信 (开发环境模拟)
    WECHAT_APPID: str = "wx_dev_appid"
    WECHAT_SECRET: str = "wx_dev_secret"

    # 云存储 (开发环境本地存储)
    UPLOAD_DIR: str = "./uploads"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
