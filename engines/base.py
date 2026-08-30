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

# 指纹回绑判定（两个条件同时满足才认定历史目录）：
# - 交集数 ≥ 2：挡住"目录里被手动放过 1 个别人的视频"的单点污染
# - 主导率 ≥ 0.5：交集 / 该目录已有作品数。作者本人的历史目录必然由其
#   作品主导；别人的目录即使混入几个同 id 文件占比也极低，不会被误绑。
#   相比绝对阈值（旧版 ≥3），能覆盖"只下过 2 个作品"的小作者改名场景。
_FINGERPRINT_MIN_MATCH = 2
_FINGERPRINT_MIN_RATIO = 0.5


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
        # 用户稳定 ID（抖音 sec_uid / 小红书 user_id），由引擎在 fetch_user_items 时设置
        # 用于改名后仍复用原保存目录（见 _resolve_user_dir）
        self._user_id = None

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

    def _record_failure(self, item: DownloadItem, error: Optional[str], save_dir: str):
        """把失败条目持久化到失败日志（供 --retry-failed 重下，无需重新爬列表）"""
        try:
            from dataclasses import asdict
            from datetime import datetime
            from utils import append_failed_entry
            append_failed_entry(self.config.save_dir, {
                'platform': self.platform,
                'item': asdict(item),
                'save_dir': save_dir,
                'error': (error or '未知错误')[:500],
                'failed_at': datetime.now().isoformat(timespec='seconds'),
            })
        except Exception as e:
            self._log(f"  ⚠ 写入失败日志异常: {e}")

    def _fingerprint_match_dir(self, platform_dir: str, item_ids: set):
        """
        指纹匹配：扫描 platform_dir 下所有子目录，返回与 item_ids 交集最大、
        且该目录被交集主导（本作者作品占目录多数）的目录。
        原理：文件名内嵌 item_id（{日期}_{配文}_{item_id}.ext），item_id 平台
        全局唯一且只出现在作者本人的目录中。
        候选排序键 (交集数, 主导率)：交集数优先选中作品最多的目录（本人的
        主目录），主导率打破平手（排除混入少量同 id 文件的外部目录）。
        Returns: (目录名, 交集数, 目录作品数)，无合格候选返回 (None, 0, 0)
        """
        best = None  # (排序键, 目录名, 交集数, 目录作品数)
        try:
            entries = os.listdir(platform_dir)
        except OSError:
            return None, 0, 0
        for name in entries:
            path = os.path.join(platform_dir, name)
            if not os.path.isdir(path):
                continue
            try:
                existing = self._scan_existing_items(path)
            except Exception:
                continue
            overlap = len(existing & item_ids)
            if overlap == 0:
                continue
            ratio = overlap / max(1, len(existing))
            key = (overlap, ratio)
            if best is None or key > best[0]:
                best = (key, name, overlap, len(existing))
        if best is None:
            return None, 0, 0
        return best[1], best[2], best[3]

    def _resolve_user_dir(self, nickname: Optional[str], item_ids: Optional[set] = None) -> str:
        """
        解析该用户的保存目录（用户改名后仍复用原目录，不新建目录重下）
        通过 output/{platform}/_users.json 注册表维护 user_id → 目录名 的映射：
        - 注册表命中：复用注册目录（目录名保持首次的昵称，不跟随改名）
        - 注册表 miss + 提供 item_ids：指纹回绑——扫描各子目录文件名中的
          item_id 求交集，交集 ≥3 的最大者识别为历史目录（覆盖存量用户，
          含升级前已改名、注册表无记录的情况），写入注册表
        - 无匹配：按当前昵称新建目录，写入注册表
        - 无 user_id 的引擎（如 B站未设置）：退化为纯昵称命名（旧行为）
        存量迁移：未改名的用户首次运行时指纹命中的就是自己的旧目录，
        与旧目录一致，无缝写入注册表。
        """
        import json

        platform_dir = os.path.join(self.config.save_dir, self.platform)
        os.makedirs(platform_dir, exist_ok=True)

        # 空昵称（items 为空/nickname 抓取失败）：不写注册表、不指纹，
        # 返回 unknown 目录（旧版行为）。否则会把 user_id 永久绑到 "unknown"
        # 目录，毒化后续运行。download_user 的 makedirs 会创建该目录。
        if not nickname:
            return os.path.join(platform_dir, "unknown")

        dirname = sanitize_dirname(nickname)

        if not self._user_id:
            return os.path.join(platform_dir, dirname)

        registry_path = os.path.join(platform_dir, "_users.json")
        registry = {}
        try:
            if os.path.exists(registry_path):
                with open(registry_path, "r", encoding="utf-8") as f:
                    registry = json.load(f)
        except Exception:
            registry = {}

        # 注册表命中：直接复用（目录可能尚未创建——fetch 阶段写入注册表后
        # 目录要到 download 阶段才 makedirs；目录被用户删除的场景也走这里，
        # 由 download_user 重建同名目录，绑定不丢）
        # 值校验：注册表被手工改坏时（"../.."、绝对路径、"."、非字符串），
        # os.path.join 会逃逸 platform_dir 甚至崩溃（int → TypeError），
        # 无效值一律忽略，走下方指纹/新建逻辑并用合法值覆盖自愈
        existing = registry.get(self._user_id)
        if (isinstance(existing, str) and existing
                and os.path.basename(existing) == existing
                and existing not in ('.', '..')):
            if existing != dirname:
                self._log(f"  用户已改名（{existing} → {dirname}），继续保存到原目录: {existing}")
            return os.path.join(platform_dir, existing)

        # 注册表 miss：尝试指纹回绑（识别改名前/升级前的历史目录）
        if item_ids:
            best_dir, overlap, dir_total = self._fingerprint_match_dir(platform_dir, item_ids)
            ratio = overlap / max(1, dir_total)
            if (best_dir and overlap >= _FINGERPRINT_MIN_MATCH
                    and ratio >= _FINGERPRINT_MIN_RATIO):
                registry[self._user_id] = best_dir
                try:
                    with open(registry_path, "w", encoding="utf-8") as f:
                        json.dump(registry, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    self._log(f"  ⚠ 写入用户注册表失败: {e}")
                if best_dir != dirname:
                    self._log(
                        f"  识别到历史目录（指纹匹配 {overlap}/{dir_total} 个作品）: {best_dir}，继续使用"
                    )
                return os.path.join(platform_dir, best_dir)

        registry[self._user_id] = dirname
        try:
            with open(registry_path, "w", encoding="utf-8") as f:
                json.dump(registry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"  ⚠ 写入用户注册表失败: {e}")
        return os.path.join(platform_dir, dirname)

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

        # 确定保存目录（经用户注册表解析；注册表 miss 时用本批 item_id 指纹回绑历史目录）
        item_ids = {i.item_id for i in items if i.item_id}
        if items:
            save_dir = self._resolve_user_dir(items[0].nickname, item_ids)
        else:
            save_dir = self._resolve_user_dir(None)

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
                self._record_failure(item, result.error, save_dir)

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
        格式: {发布日期YYYYMMDD}_{配文前15字}_{item_id}[_NNN].ext
        日期前缀方便按时间排序追踪；item_id 后缀确保同一作品不会重复下载
        """
        from utils import sanitize_filename, build_display_title, parse_create_time

        # 发布日期前缀（解析失败则省略）
        date_prefix = ""
        dt = parse_create_time(item.create_time)
        if dt:
            date_prefix = f"{dt:%Y%m%d}_"

        # 配文取前 15 个字符（过长截断，方便追踪）
        caption = sanitize_filename(build_display_title(item.title))[:15].rstrip(' ._')
        base_name = f"{date_prefix}{caption or 'untitled'}"

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
