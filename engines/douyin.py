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
from utils import suppress_f2_logging, prefilter_f2_logging


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
            # f2 import 时会发起 msToken 网络请求，瞬时失败会打出长 traceback
            # 后内部重试成功（自愈型），需在 import 前挂 filter 屏蔽
            prefilter_f2_logging()
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            from f2.apps.douyin.utils import SecUserIdFetcher
            # f2 import 后立即抑制其日志输出
            suppress_f2_logging()
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
        # 记录稳定用户 ID，download_user 据此在改名后仍复用原保存目录
        self._user_id = sec_uid

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
            # 备用视频URL: video.play_addr（bit_rate为空时的回退）
            video_play_addr_fallback = aweme_data._get_list_attr_value(
                "$.aweme_list[*].video.play_addr.url_list"
            )
            images_list = aweme_data.images
            cover_urls = aweme_data.cover
            music_urls = aweme_data.music_play_url
            # 获取音乐标题作为空描述的回退
            music_titles = getattr(aweme_data, 'music_title_raw', None) or getattr(aweme_data, 'music_title', None)

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
            if not isinstance(music_titles, list):
                music_titles = [music_titles] if music_titles is not None else []

            count = len(aweme_ids)

            for i in range(count):
                aweme_id = aweme_ids[i] if i < len(aweme_ids) else ""
                desc = descs[i] if i < len(descs) else ""
                # desc 为空时用音乐标题回退（与 fix_names.py 保持一致）
                if not desc and i < len(music_titles):
                    music = music_titles[i]
                    if music and music != "原声":
                        desc = f"#{music}"
                aweme_type = types[i] if i < len(types) else 0
                nickname = nicknames[i] if i < len(nicknames) else ""
                create_time = create_times[i] if i < len(create_times) else ""
                video_url_list = video_urls[i] if i < len(video_urls) else []
                # 当 bit_rate 为空时，回退到 video.play_addr
                if not video_url_list and i < len(video_play_addr_fallback):
                    video_url_list = video_play_addr_fallback[i]
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

                # 跳过既无视频URL也无图片URL的条目（非视频非图集的条目）
                if not urls:
                    continue

                # 过滤
                if self.config.video_only and item_type != "video":
                    continue
                if self.config.image_only and item_type != "image":
                    continue

                item = DownloadItem(
                    item_id=str(aweme_id),
                    item_type=item_type,
                    title=desc or f"video_{aweme_id}",
                    urls=[u for u in urls if u],
                    create_time=str(create_time),
                    nickname=nickname,
                    cover_url=cover_url,
                    music_url=music_url,
                    description=desc,
                )
                items.append(item)

        return items

    async def fetch_single_item(self, video_id: str, original_url: str = None) -> Optional[DownloadItem]:
        """
        获取单个抖音作品详情（用于单视频链接下载）
        Args:
            video_id: aweme_id（抖音视频 ID）
            original_url: 原始 URL（抖音不需要，小红书等平台用于提取 token）
        Returns:
            DownloadItem 或 None（失败时）
        """
        try:
            prefilter_f2_logging()
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            suppress_f2_logging()
        except ImportError:
            raise RuntimeError(
                "f2 库未安装，请运行: pip install f2\n"
                "详见: https://github.com/Johnserf-Seed/f2"
            )

        if not self._cookie:
            raise RuntimeError(
                "抖音需要登录 Cookie 才能获取作品详情。\n"
                "请使用 --cookie 参数或 cookies.txt 文件提供 Cookie。"
            )

        # 构建 kwargs（使用 f2 默认配置 + 用户 Cookie）
        kwargs = dict(ClientConfManager.client_conf.get("douyin", {}))
        kwargs["cookie"] = self._cookie or ""
        if self.config.proxies:
            proxy = self.config.proxies
            kwargs["proxies"] = {"http://": proxy, "https://": proxy}

        handler = DouyinHandler(kwargs)

        # 调用 f2 获取单视频详情
        # 直接调用 crawler.fetch_post_detail 获取原始响应，先检查 aweme_detail 是否为 null。
        # handler.fetch_one_video 在 aweme_detail 为 null 时会抛出误导性错误
        # "如果是动图作品，则接口正在维护中"，实际原因可能是隐私设置（filter_detail）。
        try:
            from f2.apps.douyin.crawler import DouyinCrawler
            from f2.apps.douyin.model import PostDetail
            from f2.apps.douyin.filter import PostDetailFilter

            async with DouyinCrawler(kwargs) as crawler:
                params = PostDetail(aweme_id=str(video_id))
                raw_response = await crawler.fetch_post_detail(params)

            if not isinstance(raw_response, dict) or not raw_response.get('aweme_detail'):
                # aweme_detail 为 null，提取 filter_detail 获取具体原因
                filter_detail = (raw_response or {}).get('filter_detail') if isinstance(raw_response, dict) else {}
                detail_msg = (filter_detail or {}).get('detail_msg', '')
                filter_reason = (filter_detail or {}).get('filter_reason', '')
                if detail_msg:
                    raise RuntimeError(
                        f"获取抖音视频 {video_id} 详情失败: {detail_msg}"
                        + (f"（原因: {filter_reason}）" if filter_reason else "")
                    )
                raise RuntimeError(f"获取抖音视频 {video_id} 详情失败: API 返回 aweme_detail 为空")

            aweme_data = PostDetailFilter(raw_response)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"获取抖音视频 {video_id} 详情失败: {e}")

        # 提取字段（PostDetailFilter 的属性）
        aweme_id = str(aweme_data.aweme_id or video_id)
        desc = aweme_data.desc or ""
        aweme_type = aweme_data.aweme_type or 0
        nickname = aweme_data.nickname or "unknown"
        create_time = str(aweme_data.create_time or "")
        video_url_list = aweme_data.video_play_addr or []
        # 备用：bit_rate 为空时回退到 video.play_addr
        if not video_url_list:
            video_url_list = aweme_data._get_list_attr_value(
                "$.aweme_detail.video.play_addr.url_list"
            ) or []
        images = aweme_data.images or []
        cover_url = aweme_data.cover or None
        music_url = aweme_data.music_play_url or None

        # 音乐标题作为空描述的回退
        if not desc:
            music_title = getattr(aweme_data, 'music_title_raw', None) or getattr(aweme_data, 'music_title', None)
            if music_title and music_title != "原声":
                desc = f"#{music_title}"

        # 判断类型: aweme_type=68/150/151 为图集
        is_image_set = (aweme_type in (68, 150, 151)) or (images and not video_url_list)

        if is_image_set:
            item_type = "image"
            urls = images if images else []
        else:
            item_type = "video"
            if isinstance(video_url_list, list) and video_url_list:
                urls = video_url_list[:1]
            elif isinstance(video_url_list, str):
                urls = [video_url_list]
            else:
                urls = []

        if not urls:
            return None

        # 过滤（与 fetch_user_items 保持一致）
        if self.config.video_only and item_type != "video":
            return None
        if self.config.image_only and item_type != "image":
            return None

        return DownloadItem(
            item_id=aweme_id,
            item_type=item_type,
            title=desc or f"video_{aweme_id}",
            urls=[u for u in urls if u],
            create_time=create_time,
            nickname=nickname,
            cover_url=cover_url,
            music_url=music_url,
            description=desc,
        )

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个抖音作品（HTTP 直接下载）"""
        import aiohttp
        import aiofiles

        saved_paths = []

        try:
            # 抖音视频/图片/封面/音乐的 CDN URL 不需要 Cookie 鉴权
            # 完整 Cookie 通常 >10KB（含 100+ 字段），会触发 Nginx "400 Request Header Or Cookie Too Large"
            # 仅保留 Referer 和 User-Agent 即可稳定下载
            headers = {
                "Referer": "https://www.douyin.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
            }

            if item.is_video:
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频下载链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")

                # 检查文件是否已存在（同一 item_id 的视频）
                # 跳过 0 字节文件（上次下载中断的残留）
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return DownloadResult(True, item, saved_paths=[filepath], skipped=True, skip_reason="已存在")

                # 下载视频（带重试，网络中断时自动重试）
                max_retries = self.config.max_retries
                last_error = None
                for attempt in range(max_retries):
                    try:
                        # 删除上次失败的不完整文件
                        if attempt > 0 and os.path.exists(filepath):
                            os.remove(filepath)
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                video_url, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=self.config.timeout, sock_read=30),
                                allow_redirects=True,
                            ) as resp:
                                if resp.status == 200:
                                    async with aiofiles.open(filepath, 'wb') as f:
                                        async for chunk in resp.content.iter_chunked(8192):
                                            await f.write(chunk)
                                    saved_paths.append(filepath)
                                    last_error = None
                                    break
                                else:
                                    last_error = f"HTTP {resp.status}"
                                    if attempt < max_retries - 1:
                                        await asyncio.sleep(2 * (attempt + 1))
                                        continue
                                    return DownloadResult(False, item, error=last_error)
                    except (aiohttp.ClientPayloadError, aiohttp.ClientOSError, ConnectionResetError, ConnectionError, asyncio.TimeoutError) as e:
                        last_error = str(e)
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 * (attempt + 1))
                if last_error:
                    # 清理不完整文件
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    return DownloadResult(False, item, error=last_error)

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
                            allow_redirects=True,
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
