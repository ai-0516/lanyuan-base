"""送检图片配对校验单元测试（content_security_service._validate_image_pair）。"""

import pytest

from app.services.content_security_service import (
    InvalidImageParamsError,
    _validate_image_pair,
)


@pytest.mark.parametrize(
    "file_id, media_url",
    [
        # 无存储桶段：fileID 环境即存储桶
        (
            "cloud://test-env/posts/a.jpg",
            "https://test-env.tcb.qcloud.la/posts/a.jpg?sign=1",
        ),
        # 带存储桶段
        (
            "cloud://test-env.7465-test-env-1300/posts/b.jpg",
            "https://7465-test-env-1300.tcb.qcloud.la/posts/b.jpg?sign=2",
        ),
        # 真实形态（官方 getdownloadtcbfilelink 示例）：临时 URL 域名带 AppID 后缀，
        # 与 fileID 的存储桶不是等值关系，必须仍然放行
        (
            "cloud://test2-4a89da.7465-test2-4a89da/A.png",
            "https://7465-test2-4a89da-1258717764.tcb.qcloud.la/A.png",
        ),
    ],
)
def test_matching_pair_is_accepted(file_id, media_url):
    """同一云存储环境、同一路径的 fileID 与临时 URL 通过校验。"""
    _validate_image_pair(file_id, media_url)


@pytest.mark.parametrize(
    "file_id, media_url",
    [
        # fileID 不是云存储 fileID
        ("posts/a.jpg", "https://test-env.tcb.qcloud.la/posts/a.jpg"),
        # 非 https
        ("cloud://test-env/posts/a.jpg", "http://test-env.tcb.qcloud.la/posts/a.jpg"),
        # 非云存储域名
        ("cloud://test-env/posts/a.jpg", "https://evil.example.com/posts/a.jpg"),
        # 域名只剩公共后缀（无存储桶）
        ("cloud://test-env/posts/a.jpg", "https://tcb.qcloud.la/posts/a.jpg"),
        # 跨环境：fileID 环境段不在域名内（送检图 ≠ 展示图）
        (
            "cloud://envA.envB/posts/a.jpg",
            "https://envB.tcb.qcloud.la/posts/a.jpg",
        ),
        # 路径不一致
        ("cloud://test-env/posts/a.jpg", "https://test-env.tcb.qcloud.la/other/a.jpg"),
        # fileID 缺少对象路径
        ("cloud://test-env.7465-test-env-1300", "https://7465-test-env-1300.tcb.qcloud.la/posts/a.jpg"),
    ],
)
def test_mismatched_pair_is_rejected(file_id, media_url):
    """任一层不一致都按客户端参数错误拒绝（fail closed）。"""
    with pytest.raises(InvalidImageParamsError):
        _validate_image_pair(file_id, media_url)
