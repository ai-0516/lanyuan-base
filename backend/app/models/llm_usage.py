"""LLM 调用用量统计（存数据库，方便汇总计算）"""


from sqlalchemy import Column, DateTime, Integer, String, func

from app.core.database import Base


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    req_id = Column(String(32), index=True, nullable=False, comment="请求关联 ID")
    session_id = Column(Integer, nullable=True, comment="会话 ID")
    user_id = Column(Integer, nullable=True, comment="用户 ID")
    total_tokens = Column(Integer, default=0, comment="总 token 数")
    prompt_tokens = Column(Integer, default=0, comment="输入 token 数")
    completion_tokens = Column(Integer, default=0, comment="输出 token 数")
    cached_tokens = Column(Integer, default=0, comment="缓存命中 token 数")
    cache_rate = Column(Integer, default=0, comment="缓存命中率（百分比，0~100）")
    turns = Column(Integer, default=0, comment="LLM 调用轮次")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
