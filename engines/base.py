"""
ShortVideoDownload - 下载引擎基类
定义所有平台下载引擎的统一接口
"""
import os
import re
import sys
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from config import DownloadConfig


@dataclass
class DownloadItem:
    """单个下载项"""
    # 作品 ID
    item_id: str
    # 作品类型: video / image
    item_type: str
    # 标题/描述
    title: str
    # 下载 URL (视频) 或 URL 列表 (图集)
    urls: List[str] = field(default_factory=list)
    # 原始页面 URL
    url: Optional[str] = None
    # 创建时间
    create_time: Optional[str] = None
    # 作者昵称
    nickname: Optional[str] = None
    # 作者 ID
    uid: Optional[str] = None
    # 封面 URL
    cover_url: Optional[str] = None
    # 缩略图 URL
    thumbnail: Optional[str] = None
    # 音乐 URL
    music_url: Optional[str] = None
    # 文案/描述全文
    description: Optional[str] = None
    # 时长 (秒)
    duration: Optional[float] = None

    @property
    def is_video(self) -> bool:
        return self.item_type == "video"

    @property
    def is_image(self) -> bool:
        return self.item_type == "image"


@dataclass
class DownloadResult:
    """下载结果"""
    success: bool
    item: DownloadItem
    saved_paths: List[str] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: Optional[str] = None


class BaseEngine(ABC):
    """下载引擎基类"""

    platform: str = "unknown"

    def __init__(self, config: DownloadConfig):
        self.config = config

    @abstractmethod
    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取用户的所有作品列表
        Args:
            user_url: 用户主页 URL
        Returns:
            作品列表
        """
        pass

    @abstractmethod
    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """
        下载单个作品
        Args:
            item: 作品信息
            save_dir: 保存目录
        Returns:
            下载结果
        """
        pass

    def _log(self, msg: str):
        """输出日志到控制台"""
        print(msg, flush=True)

    async def download_user(self, user_url: str, items: list = None) -> List[DownloadResult]:
        """
        下载用户的所有作品（完整流程）
        Args:
            user_url: 用户主页 URL
            items: 已获取的作品列表（如果为 None 则自动获取）
        """
        if items is None:
            items = await self.fetch_user_items(user_url)
        results = []

        # 过滤
        if self.config.video_only:
            items = [i for i in items if i.is_video]
        if self.config.image_only:
            items = [i for i in items if i.is_image]

        # 限制数量
        if self.config.max_count > 0:
            items = items[:self.config.max_count]

        # 确定保存目录
        if items:
            nickname = items[0].nickname or "unknown"
            save_dir = os.path.join(self.config.save_dir, self.platform, sanitize_dirname(nickname))
        else:
            save_dir = os.path.join(self.config.save_dir, self.platform, "unknown")

        os.makedirs(save_dir, exist_ok=True)

        total = len(items)
        self._log(f"  共 {total} 个作品待下载，保存至: {save_dir}")

        # 扫描已有文件，构建已下载集合（用于跳过重复）
        existing_ids = self._scan_existing_items(save_dir)

        for idx, item in enumerate(items, 1):
            # 检查是否已下载（基于 item_id 判断）
            if item.item_id and item.item_id in existing_ids:
                self._log(f"  [{idx}/{total}] 跳过(已存在): {item.title[:40]}")
                results.append(DownloadResult(True, item, skipped=True, skip_reason="已存在"))
                continue

            self._log(f"  [{idx}/{total}] 下载中: {item.title[:40]}")
            result = await self.download_item(item, save_dir)

            if result.success:
                size_str = ""
                if result.saved_paths:
                    total_size = sum(
                        os.path.getsize(p) for p in result.saved_paths if os.path.exists(p)
                    )
                    from utils import format_file_size
                    size_str = f" ({format_file_size(total_size)})"
                self._log(f"  [{idx}/{total}] 完成{size_str}: {item.title[:40]}")
            else:
                self._log(f"  [{idx}/{total}] 失败: {item.title[:40]} - {result.error}")

            results.append(result)

            # 反检测：下载间隔（模拟真实用户浏览节奏，避免连续请求 CDN 触发频率风控）
            if idx < total and not result.skipped:
                import random
                delay = random.uniform(2.0, 5.0)
                await asyncio.sleep(delay)

        return results

    def _scan_existing_items(self, save_dir: str) -> set:
        """
        扫描保存目录中已有的文件，提取已下载的 item_id 集合
        文件名格式: {title}_{itemId}.mp4 或 {itemId}.mp4
        """
        existing = set()
        if not os.path.isdir(save_dir):
            return existing

        for filename in os.listdir(save_dir):
            # 跳过封面、描述、音乐文件（只从主文件提取 item_id）
            if filename.endswith('_cover.jpg') or filename.endswith('.txt') or filename.endswith('.mp3'):
                continue
            # 从文件名中提取可能的 item_id
            # 文件名可能是: title_itemId.mp4 或 title_itemId_001.jpg
            name_without_ext = os.path.splitext(filename)[0]
            # 尝试提取最后一段作为 item_id
            parts = name_without_ext.split('_')
            for part in reversed(parts):
                # 抖音 item_id: ≥10 位纯数字
                # 小红书 note_id: 24 位十六进制字符串
                # B站 BV号: BV + 10 位字母数字 (如 BV1WV3g6eE9z)
                # B站 av号: av + 数字 (如 av123456)
                if re.match(r'^BV[A-Za-z0-9]{10}$', part):
                    existing.add(part)
                    break
                if re.match(r'^av\d+$', part):
                    existing.add(part)
                    break
                if len(part) >= 10 and (part.isdigit() or re.match(r'^[0-9a-f]{20,}$', part)):
                    existing.add(part)
                    break
        return existing

    def _make_filepath(self, save_dir: str, item: DownloadItem, ext: str, idx: int = 0) -> str:
        """
        生成文件路径
        使用 item_id 作为文件名的一部分，确保同一作品不会重复下载
        """
        from utils import sanitize_filename, build_display_title

        base_name = build_display_title(item.title)
        # 将 item_id 加入文件名，确保唯一性
        # item_id 为空时用时间戳+随机数避免覆盖
        if item.item_id:
            item_suffix = f"_{item.item_id}"
        else:
            import time
            item_suffix = f"_{int(time.time() * 1000) % 100000}"

        if item.is_image and idx > 0:
            filename = f"{base_name}{item_suffix}_{idx:03d}{ext}"
        else:
            filename = f"{base_name}{item_suffix}{ext}"

        filename = sanitize_filename(filename)
        filepath = os.path.join(save_dir, filename)

        return filepath

    def _is_file_exists(self, save_dir: str, item: DownloadItem, ext: str, idx: int = 0) -> bool:
        """检查文件是否已存在"""
        filepath = self._make_filepath(save_dir, item, ext, idx)
        return os.path.exists(filepath)


def sanitize_dirname(name: str) -> str:
    """清理目录名"""
    import re
    illegal = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(illegal, '_', name)
    name = name.strip(' .')
    return name if name else "unknown"
