"""
ShortVideoDownload - 下载引擎
"""

from .douyin import DouyinEngine
from .kuaishou import KuaishouEngine
from .xiaohongshu import XiaohongshuEngine
from .bilibili import BilibiliEngine
from .weibo import WeiboEngine
from .x import XEngine

ENGINES = {
    "douyin": DouyinEngine,
    "kuaishou": KuaishouEngine,
    "xiaohongshu": XiaohongshuEngine,
    "bilibili": BilibiliEngine,
    "weibo": WeiboEngine,
    "x": XEngine,
}

__all__ = ["ENGINES", "DouyinEngine", "KuaishouEngine", "XiaohongshuEngine", "BilibiliEngine", "WeiboEngine", "XEngine"]
