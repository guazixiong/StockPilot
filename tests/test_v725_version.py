"""v7.2.5 回归闸：__version__ 必须与最新交付版本一致。

用户反馈『v7.2.4 改动的布局怎么没在 v7.2 上看到』——字节码取证证明
布局代码已在 exe/zip 内，但 __version__ 自 v7.2.0 后未随版本演进，
窗口标题/托盘一直显示 v7.2.0，用户据标题判断版本，误以为更新没打进去。
此闸防止版本号再度与交付脱节：发版时改 __init__.py 后此测试必须同步。
"""
import re

import stockpilot


def test_version_matches_changelog_head():
    """__version__ 必须不落后于 git log 最新版本的语义（解析 vN.N.N 提交）。"""
    # 直接锚定当前交付版本：改版本时同步更新此常量
    assert stockpilot.__version__ == "7.2.5", (
        "版本号未随交付更新：窗口标题会误导用户以为更新未生效")


def test_version_semver_shape():
    assert re.fullmatch(r"\d+\.\d+\.\d+", stockpilot.__version__)
