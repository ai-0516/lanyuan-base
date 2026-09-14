"""公开内容审核状态。"""

from enum import Enum


class PostModerationStatus(str, Enum):
    """帖子整体审核状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class MediaModerationTaskStatus(str, Enum):
    """单张图片异步审核任务状态。"""

    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"
