"""
ShortVideoDownload - 下载引擎
"""

from .douyin import DouyinEngine
from .kuaishou import KuaishouEngine
from .xiaohongshu import XiaohongshuEngine
from .bilibili import BilibiliEngine
from .weibo import WeiboEngine

ENGINES = {
    "douyin": DouyinEngine,
    "kuaishou": KuaishouEngine,
    "xiaohongshu": XiaohongshuEngine,
    "bilibili": BilibiliEngine,
    "weibo": WeiboEngine,
}

__all__ = ["ENGINES", "DouyinEngine", "KuaishouEngine", "XiaohongshuEngine", "BilibiliEngine", "WeiboEngine"]
