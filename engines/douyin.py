"""
ShortVideoDownload - 抖音下载引擎
基于 f2 库实现，优先使用 f2 CLI（最稳定可靠）
"""
import os
import re
import io
import json
import sys
import asyncio
import logging
import logging.handlers
import subprocess
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig, load_f2_config

try:
    from rich.console import Console as RichConsole
except ImportError:
    RichConsole = None


def _suppress_f2_logging():
    """
    抑制 f2 库的冗余日志输出
    f2 的日志有两个来源：
    1. logging 系统（logger.info/error 等）—— 通过设置级别为 WARNING 抑制
    2. rich_console.print() 直接输出 —— 通过 monkey-patch 替换为静默 Console 抑制
    必须在 import f2 之后调用，否则 f2 的 log_setup() 会重置级别
    """
    # 0. 先触发 f2 的 import（这会调用 log_setup() 设置级别为 INFO）
    # 必须在设置级别之前 import，否则 log_setup() 会覆盖我们的设置
    _f2_dy_handler = None
    _f2_bark_handler = None
    try:
        import f2.apps.douyin.handler as _f2_dy_handler
    except ImportError:
        pass
    try:
        import f2.apps.bark.handler as _f2_bark_handler
    except ImportError:
        pass

    # 1. 抑制 logging 系统输出（在 f2 log_setup() 之后设置，避免被覆盖）
    # 设置为 CRITICAL 级别，抑制所有 INFO/WARNING/ERROR 消息
    # f2 的 INFO 消息（处理用户、等待、页数）和 ERROR 消息（Bark通知失败）对用户无意义
    f2_logger = logging.getLogger("f2")
    f2_logger.setLevel(logging.CRITICAL)
    # 设置所有 handler 的级别为 CRITICAL
    for handler in f2_logger.handlers[:]:
        if not isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.setLevel(logging.CRITICAL)

    # 2. 抑制 rich_console.print() 直接输出
    # f2 在 handler 模块级别创建了 rich_console = RichConsoleManager().rich_console
    # 将其替换为写入 StringIO 的静默 Console
    _silent_console = None
    if RichConsole is not None:
        _silent_console = RichConsole(file=io.StringIO(), width=80, no_color=True)
    if _f2_dy_handler and _silent_console:
        _f2_dy_handler.rich_console = _silent_console

    # 3. 抑制 Bark 通知控制台输出
    if _f2_bark_handler and _silent_console:
        _f2_bark_handler.rich_console = _silent_console


class DouyinEngine(BaseEngine):
    """抖音下载引擎 - 基于 f2"""

    platform = "douyin"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or self._load_cookie()

    def _load_cookie(self) -> str:
        """从 f2 配置文件加载 Cookie"""
        try:
            f2_conf = load_f2_config()
            dy_conf = f2_conf.get("douyin", {})
            cookie = dy_conf.get("cookie", "")
            if cookie:
                return cookie
        except Exception:
            pass
        return ""

    def _extract_sec_uid(self, url: str) -> str:
        """从 URL 提取 sec_uid"""
        match = re.search(r'/user/([A-Za-z0-9_\-=]+)', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL 提取抖音 sec_uid: {url}")

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取抖音用户的所有作品列表（用于 dry-run 预览）
        通过 f2 API 获取作品元信息
        """
        try:
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            from f2.apps.douyin.utils import SecUserIdFetcher
            # f2 import 后立即抑制其日志输出
            _suppress_f2_logging()
        except ImportError:
            raise RuntimeError(
                "f2 库未安装，请运行: pip install f2\n"
                "详见: https://github.com/Johnserf-Seed/f2"
            )

        if not self._cookie:
            raise RuntimeError(
                "抖音需要登录 Cookie 才能获取用户作品列表。\n"
                "请使用以下方式之一提供 Cookie:\n"
                "  1. 命令行参数: --cookie \"your_cookie\"\n"
                "  2. 浏览器提取: --browser-cookie chrome\n"
                "  3. 配置 f2: 运行 f2 --init-config douyin 后编辑 ~/.f2/conf.yaml\n"
                "\n获取Cookie方法：\n"
                "  在浏览器登录抖音 → F12 → Network → 刷新页面 → 找到请求头中的Cookie字段"
            )

        # 获取 sec_user_id
        sec_uid = await SecUserIdFetcher.get_sec_user_id(user_url)

        # 构建 kwargs（使用 f2 默认配置 + 用户 Cookie）
        kwargs = dict(ClientConfManager.client_conf.get("douyin", {}))
        kwargs["url"] = user_url
        kwargs["mode"] = "post"
        kwargs["cookie"] = self._cookie or ""
        if self.config.proxies:
            proxy = self.config.proxies
            kwargs["proxies"] = {"http://": proxy, "https://": proxy}

        handler = DouyinHandler(kwargs)
        items = []

        async for aweme_data in handler.fetch_user_post_videos(
            sec_uid,
            max_counts=self.config.max_count if self.config.max_count > 0 else None,
        ):
            if not aweme_data.has_aweme:
                continue

            # 解析作品列表
            aweme_ids = aweme_data.aweme_id
            descs = aweme_data.desc
            types = aweme_data.aweme_type
            nicknames = aweme_data.nickname
            create_times = aweme_data.create_time
            video_urls = aweme_data.video_play_addr
            images_list = aweme_data.images
            cover_urls = aweme_data.cover
            music_urls = aweme_data.music_play_url

            # 确保都是列表
            if not isinstance(aweme_ids, list):
                aweme_ids = [aweme_ids]
            if not isinstance(descs, list):
                descs = [descs]
            if not isinstance(types, list):
                types = [types]
            if not isinstance(nicknames, list):
                nicknames = [nicknames]
            if not isinstance(create_times, list):
                create_times = [create_times]
            if not isinstance(video_urls, list):
                video_urls = [video_urls]
            if not isinstance(images_list, list):
                images_list = [images_list]
            if not isinstance(cover_urls, list):
                cover_urls = [cover_urls]
            if not isinstance(music_urls, list):
                music_urls = [music_urls]

            count = len(aweme_ids)

            for i in range(count):
                aweme_id = aweme_ids[i] if i < len(aweme_ids) else ""
                desc = descs[i] if i < len(descs) else ""
                aweme_type = types[i] if i < len(types) else 0
                nickname = nicknames[i] if i < len(nicknames) else ""
                create_time = create_times[i] if i < len(create_times) else ""
                video_url_list = video_urls[i] if i < len(video_urls) else []
                images = images_list[i] if i < len(images_list) else None
                cover_url = cover_urls[i] if i < len(cover_urls) else None
                music_url = music_urls[i] if i < len(music_urls) else None

                # 判断类型: aweme_type=150 为图集, 其他为视频
                is_image_set = (aweme_type == 150 or aweme_type == 151) or (images and not video_url_list)

                if is_image_set:
                    item_type = "image"
                    urls = images if images else []
                else:
                    item_type = "video"
                    # video_url_list 可能是 url_list 数组
                    if isinstance(video_url_list, list) and video_url_list:
                        urls = video_url_list[:1]  # 取第一个（最高画质）
                    elif isinstance(video_url_list, str):
                        urls = [video_url_list]
                    else:
                        urls = []

                # 过滤
                if self.config.video_only and item_type != "video":
                    continue
                if self.config.image_only and item_type != "image":
                    continue

                item = DownloadItem(
                    item_id=str(aweme_id),
                    item_type=item_type,
                    title=desc or f"douyin_{aweme_id}",
                    urls=[u for u in urls if u],
                    create_time=str(create_time),
                    nickname=nickname,
                    cover_url=cover_url,
                    music_url=music_url,
                    description=desc,
                )
                items.append(item)

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个抖音作品（HTTP 直接下载）"""
        import aiohttp
        import aiofiles

        saved_paths = []

        try:
            headers = {
                "Referer": "https://www.douyin.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            if item.is_video:
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频下载链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")

                # 检查文件是否已存在（同一 item_id 的视频）
                if os.path.exists(filepath):
                    return DownloadResult(True, item, saved_paths=[filepath], skipped=True, skip_reason="已存在")

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        video_url, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                    ) as resp:
                        if resp.status == 200:
                            async with aiofiles.open(filepath, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(8192):
                                    await f.write(chunk)
                            saved_paths.append(filepath)
                        else:
                            return DownloadResult(False, item, error=f"HTTP {resp.status}")

            elif item.is_image:
                # 检查图集是否已存在（检查第一张图）
                first_img_path = self._make_filepath(save_dir, item, ".jpg", idx=1)
                if os.path.exists(first_img_path):
                    return DownloadResult(True, item, saved_paths=[first_img_path], skipped=True, skip_reason="已存在")

                for idx, img_url in enumerate(item.urls):
                    filepath = self._make_filepath(save_dir, item, ".jpg", idx=idx + 1)

                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            img_url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                        ) as resp:
                            if resp.status == 200:
                                async with aiofiles.open(filepath, 'wb') as f:
                                    async for chunk in resp.content.iter_chunked(8192):
                                        await f.write(chunk)
                                saved_paths.append(filepath)

            # 保存封面
            if self.config.save_cover and item.cover_url:
                cover_path = self._make_filepath(save_dir, item, "_cover.jpg")
                if not os.path.exists(cover_path):
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(item.cover_url, headers=headers,
                                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                if resp.status == 200:
                                    async with aiofiles.open(cover_path, 'wb') as f:
                                        async for chunk in resp.content.iter_chunked(8192):
                                            await f.write(chunk)
                                    saved_paths.append(cover_path)
                    except Exception:
                        pass

            # 保存音乐
            if self.config.save_music and item.music_url:
                music_path = self._make_filepath(save_dir, item, ".mp3")
                if not os.path.exists(music_path):
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(item.music_url, headers=headers,
                                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                if resp.status == 200:
                                    async with aiofiles.open(music_path, 'wb') as f:
                                        async for chunk in resp.content.iter_chunked(8192):
                                            await f.write(chunk)
                                    saved_paths.append(music_path)
                    except Exception:
                        pass

            return DownloadResult(True, item, saved_paths=saved_paths)

        except Exception as e:
            return DownloadResult(False, item, error=str(e))

    async def download_user_via_cli(self, user_url: str) -> List[DownloadResult]:
        """
        通过 f2 CLI 方式下载（最稳定的方案）
        f2 自己处理所有认证、分页、下载逻辑
        """
        save_dir = os.path.join(self.config.save_dir, self.platform)
        os.makedirs(save_dir, exist_ok=True)

        cmd = [
            sys.executable, "-m", "f2", "dy",
            "-M", "post",
            "-u", user_url,
            "-p", save_dir,
        ]

        if self._cookie:
            cmd.extend(["-k", self._cookie])

        if self.config.max_count > 0:
            cmd.extend(["--max_counts", str(self.config.max_count)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3600)

            if proc.returncode != 0:
                err_msg = stderr.decode('utf-8', errors='replace')[:500]
                return [DownloadResult(False, DownloadItem(
                    item_id="", item_type="video", title="",
                ), error=err_msg)]

            return [DownloadResult(True, DownloadItem(
                item_id="cli_batch", item_type="video", title="f2 CLI batch download",
            ))]
        except asyncio.TimeoutError:
            return [DownloadResult(False, DownloadItem(
                item_id="", item_type="video", title="",
            ), error="下载超时")]
        except Exception as e:
            return [DownloadResult(False, DownloadItem(
                item_id="", item_type="video", title="",
            ), error=str(e))]
