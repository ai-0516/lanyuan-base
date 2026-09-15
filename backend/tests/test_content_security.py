"""送检图片配对校验单元测试（content_security_service._validate_image_pair）。"""

import logging

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
        # DNS 主机名大小写不敏感：urlparse 会把域名小写化，fileID 段保留原样，
        # 比对前不归一就会误拒同一环境的合法配对
        (
            "cloud://envA/posts/a.jpg",
            "https://envA.tcb.qcloud.la/posts/a.jpg?sign=1",
        ),
        (
            "cloud://Env-A.env-a/posts/a.jpg",
            "https://env-a.tcb.qcloud.la/posts/a.jpg",
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
        # 环境段为空段：不能被 `if part` 滤掉后绕过环境绑定
        ("cloud://./posts/a.jpg", "https://anything.tcb.qcloud.la/posts/a.jpg"),
        ("cloud://env-a..env-a/posts/a.jpg", "https://env-a.tcb.qcloud.la/posts/a.jpg"),
        # 大小写归一仅限主机名：对象路径大小写有意义，不归一时必须拒绝
        ("cloud://test-env/posts/A.JPG", "https://test-env.tcb.qcloud.la/posts/a.jpg"),
    ],
)
def test_mismatched_pair_is_rejected(file_id, media_url):
    """任一层不一致都按客户端参数错误拒绝（fail closed）。"""
    with pytest.raises(InvalidImageParamsError):
        _validate_image_pair(file_id, media_url)


def test_rejected_pair_logs_file_id_and_host(caplog):
    """环境校验失败时留下 file_id 与解析出的 host（部署后定位域名形态用）。"""
    with caplog.at_level(logging.WARNING):
        with pytest.raises(InvalidImageParamsError):
            _validate_image_pair(
                "cloud://env-a/posts/a.jpg",
                "https://env-b.tcb.qcloud.la/posts/a.jpg?sign=1",
            )

    assert "file_id='cloud://env-a/posts/a.jpg'" in caplog.text
    assert "host='env-b.tcb.qcloud.la'" in caplog.text
    # 临时 URL 带签名，不得整体落日志
    assert "sign=1" not in caplog.text


def test_rejected_pair_log_escapes_control_chars_and_truncates(caplog):
    """file_id/host/scope 客户端可控：控制字符须转义（防伪造日志行），长度须有上限。"""
    with caplog.at_level(logging.WARNING):
        with pytest.raises(InvalidImageParamsError):
            _validate_image_pair(
                "cloud://env-a\nFORGED-LOG-LINE/posts/x.jpg",
                "https://evil.tcb.qcloud.la/posts/x.jpg",
            )
        with pytest.raises(InvalidImageParamsError):
            _validate_image_pair(
                "cloud://env-a/posts/" + "a" * 500 + ".jpg",
                "https://evil.tcb.qcloud.la/posts/x.jpg",
            )

    messages = [
        r.getMessage() for r in caplog.records if "Rejected image pair" in r.getMessage()
    ]
    assert len(messages) == 2
    # 换行被转义成 \n 字面量 → 不会多出一条不带 logger 前缀的日志行
    assert all("\n" not in message for message in messages)
    # 转义后仍保留可诊断内容
    assert "FORGED-LOG-LINE" in messages[0]
    # 超长 file_id 被截断
    assert "a" * 500 not in messages[1]
