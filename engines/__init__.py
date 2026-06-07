"""
ShortVideoDownload - 下载引擎
"""
import logging
import logging.handlers

from .douyin import DouyinEngine
from .kuaishou import KuaishouEngine
from .xiaohongshu import XiaohongshuEngine
from .bilibili import BilibiliEngine
from .weibo import WeiboEngine

# 抑制 f2 库的冗余日志输出
# 注意：f2 的 log_setup() 在首次 import 时会重置 logger 级别为 INFO
# 这里预设置为 CRITICAL，但 f2 import 后可能会被覆盖
# 真正的抑制在 douyin.py 的 _suppress_f2_logging() 中完成（在 f2 完全 import 后调用）
f2_logger = logging.getLogger("f2")
f2_logger.setLevel(logging.CRITICAL)
for handler in f2_logger.handlers[:]:
    if not isinstance(handler, logging.handlers.TimedRotatingFileHandler):
        handler.setLevel(logging.CRITICAL)

ENGINES = {
    "douyin": DouyinEngine,
    "kuaishou": KuaishouEngine,
    "xiaohongshu": XiaohongshuEngine,
    "bilibili": BilibiliEngine,
    "weibo": WeiboEngine,
}

__all__ = ["ENGINES", "DouyinEngine", "KuaishouEngine", "XiaohongshuEngine", "BilibiliEngine", "WeiboEngine"]
