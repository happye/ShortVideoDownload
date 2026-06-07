"""
ShortVideoDownload - 配置管理
"""
import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# 默认保存路径（项目根目录下的 output 文件夹）
DEFAULT_SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# 平台标识
PLATFORM_DOUYIN = "douyin"
PLATFORM_KUAISHOU = "kuaishou"
PLATFORM_XIAOHONGSHU = "xiaohongshu"
PLATFORM_BILIBILI = "bilibili"
PLATFORM_WEIBO = "weibo"
PLATFORM_TIKTOK = "tiktok"

# 平台 URL 匹配规则
PLATFORM_PATTERNS = {
    PLATFORM_DOUYIN: [
        r"douyin\.com",
        r"iesdouyin\.com",
    ],
    PLATFORM_KUAISHOU: [
        r"kuaishou\.com",
        r"gifshow\.com",
        r"chenzhongtech\.com",
    ],
    PLATFORM_XIAOHONGSHU: [
        r"xiaohongshu\.com",
        r"xhslink\.com",
    ],
    PLATFORM_BILIBILI: [
        r"bilibili\.com",
        r"b23\.tv",
    ],
    PLATFORM_WEIBO: [
        r"weibo\.com",
        r"weibo\.cn",
    ],
    PLATFORM_TIKTOK: [
        r"tiktok\.com",
        r"vm\.tiktok\.com",
    ],
}


@dataclass
class DownloadConfig:
    """下载配置"""
    # 保存路径
    save_dir: str = DEFAULT_SAVE_DIR
    # 最大下载数量 (0=无限制)
    max_count: int = 0
    # 日期范围
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    # 内容类型过滤
    video_only: bool = False
    image_only: bool = False
    # Cookie
    cookie: Optional[str] = None
    # 从浏览器提取 Cookie
    browser_cookie: Optional[str] = None
    # 画质偏好: best / hd / sd
    quality: str = "best"
    # 并发数
    max_connections: int = 5
    # 超时 (秒)
    timeout: int = 30
    # 重试次数
    max_retries: int = 3
    # 代理
    proxies: Optional[str] = None
    # 是否保存封面
    save_cover: bool = True
    # 是否保存文案/描述（默认不保存）
    save_desc: bool = False
    # 是否保存音乐
    save_music: bool = False
    # 重名时的编号格式
    duplicate_format: str = "_{seq:03d}"


def load_config(config_path: Optional[str] = None) -> DownloadConfig:
    """从 YAML 文件加载配置"""
    cfg = DownloadConfig()
    if config_path and os.path.isfile(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def load_f2_config() -> dict:
    """加载 f2 的配置文件，获取已保存的 Cookie 等"""
    f2_conf_path = os.path.join(str(Path.home()), ".f2", "conf.yaml")
    if os.path.isfile(f2_conf_path):
        with open(f2_conf_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}
