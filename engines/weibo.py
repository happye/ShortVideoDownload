"""
ShortVideoDownload - 微博下载引擎
使用微博 Web API 获取用户作品，yt-dlp 下载单个视频
"""
import os
import re
import json
import asyncio
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


class WeiboEngine(BaseEngine):
    """微博下载引擎 - Web API + yt-dlp"""

    platform = "weibo"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""

    def _extract_uid(self, url: str) -> str:
        """从 URL 提取微博 UID"""
        # https://weibo.com/u/123456
        match = re.search(r'weibo\.com/u/(\d+)', url)
        if match:
            return match.group(1)
        # https://weibo.com/username 可能需要解析
        raise ValueError(f"无法从 URL 提取微博 UID: {url}")

    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://weibo.com/",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """获取微博用户的所有媒体作品（视频+图集）"""
        import aiohttp

        uid = self._extract_uid(user_url)
        headers = self._build_headers()
        items = []
        page = 1

        async with aiohttp.ClientSession() as session:
            while True:
                # 微博容器 API - 获取用户微博列表
                api_url = f"https://weibo.com/ajax/statuses/mymblog"
                params = {
                    "uid": uid,
                    "page": str(page),
                    "feature": "0",  # 0=全部, 1=原创
                }

                try:
                    async with session.get(
                        api_url, params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(
                                f"微博 API 返回 HTTP {resp.status}，可能需要提供 Cookie。\n"
                                f"请使用 --cookie 或 --browser-cookie 参数提供 Cookie。"
                            )
                        data = await resp.json()
                except RuntimeError:
                    raise
                except Exception as e:
                    if not items:
                        raise RuntimeError(
                            f"微博 API 请求失败，可能需要提供 Cookie。\n"
                            f"错误: {e}\n\n"
                            f"请使用 --cookie 或 --browser-cookie 参数提供 Cookie。"
                        )
                    break

                ok = data.get("ok", 0)
                if ok != 1:
                    msg = data.get("msg", "未知错误")
                    if not items:
                        raise RuntimeError(
                            f"微博 API 返回错误: {msg}\n\n"
                            f"可能需要提供 Cookie，请使用 --cookie 或 --browser-cookie 参数。"
                        )
                    break

                weibo_list = data.get("data", {}).get("list", [])
                if not weibo_list:
                    break

                for weibo in weibo_list:
                    weibo_id = weibo.get("id", "")
                    text_raw = weibo.get("text_raw", "") or weibo.get("text", "") or ""
                    # 清理 HTML 标签
                    text_raw = re.sub(r'<[^>]+>', '', text_raw).strip()
                    title = text_raw[:60] if text_raw else "untitled"

                    # 检查是否有媒体内容
                    media_info = weibo.get("page_info", {})
                    has_video = False
                    has_images = False

                    # 视频
                    video_url = ""
                    if media_info.get("type") == "video":
                        has_video = True
                        video_url = media_info.get("media_info", {}).get("stream_url_hd") or \
                                   media_info.get("media_info", {}).get("stream_url") or \
                                   media_info.get("media_info", {}).get("mp4_hd_url") or \
                                   media_info.get("media_info", {}).get("mp4_sd_url") or ""
                        if not video_url:
                            video_url = media_info.get("url", "")

                    # 图片
                    pic_urls = []
                    pics = weibo.get("pic_ids", [])
                    pic_infos = weibo.get("pic_infos", {})
                    if pics or pic_infos:
                        has_images = True
                        for pic_id in pics:
                            pic_info = pic_infos.get(pic_id, {})
                            url = pic_info.get("original", {}).get("url") or \
                                  pic_info.get("large", {}).get("url") or \
                                  pic_info.get("mw2000", {}).get("url") or ""
                            if url:
                                pic_urls.append(url)

                    # 跳过纯文字微博
                    if not has_video and not has_images:
                        continue

                    # 过滤
                    if self.config.video_only and not has_video:
                        continue
                    if self.config.image_only and not has_images:
                        continue

                    # 构建微博 URL
                    weibo_url = f"https://weibo.com/{uid}/{weibo_id}"

                    cover_url = ""
                    if has_video and media_info.get("page_pic"):
                        cover_url = media_info["page_pic"].get("url", "")
                    elif has_images and pic_urls:
                        cover_url = pic_urls[0]

                    item = DownloadItem(
                        item_id=str(weibo_id),
                        item_type="video" if has_video else "image",
                        title=title,
                        urls=[video_url] if has_video else pic_urls,
                        create_time=str(weibo.get("created_at", "")),
                        cover_url=cover_url,
                        description=text_raw,
                    )
                    items.append(item)

                # 翻页
                page += 1

                # 数量限制
                if self.config.max_count > 0 and len(items) >= self.config.max_count:
                    items = items[:self.config.max_count]
                    break

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个微博作品"""
        import aiohttp
        import aiofiles

        saved_paths = []

        try:
            if item.is_video:
                # 下载视频
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")

                headers = self._build_headers()
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
            else:
                # 下载图片
                for idx, pic_url in enumerate(item.urls):
                    ext = ".jpg"
                    if idx == 0:
                        filepath = self._make_filepath(save_dir, item, ext)
                    else:
                        filepath = self._make_filepath(save_dir, item, f"_{idx:03d}{ext}")

                    headers = self._build_headers()
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            pic_url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15),
                        ) as resp:
                            if resp.status == 200:
                                async with aiofiles.open(filepath, 'wb') as f:
                                    async for chunk in resp.content.iter_chunked(8192):
                                        await f.write(chunk)
                                saved_paths.append(filepath)

            if not saved_paths:
                return DownloadResult(False, item, error="未下载到任何文件")

            return DownloadResult(True, item, saved_paths=saved_paths)

        except Exception as e:
            return DownloadResult(False, item, error=str(e))
