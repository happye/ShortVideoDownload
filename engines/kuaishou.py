"""
ShortVideoDownload - 快手下载引擎
使用快手 Web GraphQL API 获取用户作品
"""
import os
import re
import json
import asyncio
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


class KuaishouEngine(BaseEngine):
    """快手下载引擎 - GraphQL API"""

    platform = "kuaishou"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""
        self._graphql_url = "https://www.kuaishou.com/graphql"

    def _extract_user_id(self, url: str) -> str:
        """从 URL 提取快手用户 ID (kwaiId)"""
        match = re.search(r'/profile/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL 提取快手用户 ID: {url}")

    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.kuaishou.com/",
            "Content-Type": "application/json",
            "Origin": "https://www.kuaishou.com",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """获取快手用户的所有作品"""
        import aiohttp

        user_id = self._extract_user_id(user_url)
        headers = self._build_headers()
        items = []
        pcursor = ""

        async with aiohttp.ClientSession() as session:
            while True:
                query = {
                    "operationName": "visionProfilePhotoList",
                    "query": """query visionProfilePhotoList($pcursor: String, $userId: String, $page: String, $webPageArea: String) {
                        visionProfilePhotoList(pcursor: $pcursor, userId: $userId, page: $page, webPageArea: $webPageArea) {
                            result
                            llsid
                            feeds {
                                photo {
                                    ... on PhotoEntity {
                                        id
                                        photoUrl
                                        caption
                                        timestamp
                                        likeCount
                                        commentCount
                                        viewCount
                                        animatedCoverUrl
                                        coverUrl
                                    }
                                }
                            }
                        }
                    }""",
                    "variables": {
                        "pcursor": pcursor,
                        "userId": user_id,
                        "page": "profile",
                        "webPageArea": "",
                    }
                }

                try:
                    async with session.post(
                        self._graphql_url, json=query, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        data = await resp.json()
                except Exception as e:
                    if not items:
                        raise RuntimeError(
                            f"快手 API 请求失败，可能需要提供 Cookie。\n"
                            f"错误: {e}\n\n"
                            f"请使用 --cookie 参数提供快手登录 Cookie，\n"
                            f"或 --browser-cookie chrome 从浏览器自动提取。"
                        )
                    break

                # 检查 GraphQL errors
                if data.get("errors"):
                    error_msgs = [e.get("message", "") for e in data["errors"]]
                    if not items:
                        raise RuntimeError(
                            f"快手 GraphQL 查询失败:\n"
                            + "\n".join(f"  - {m}" for m in error_msgs[:3])
                        )
                    break

                result = data.get("data", {}).get("visionProfilePhotoList", {}).get("result", 0)
                if result != 1:
                    # 可能需要验证码或 Cookie
                    if not items:
                        raise RuntimeError(
                            f"快手 API 返回异常 (result={result})，可能需要提供 Cookie。\n\n"
                            f"请使用以下方式之一提供 Cookie：\n"
                            f"  1. --cookie \"your_cookie\"\n"
                            f"  2. --browser-cookie firefox\n"
                            f"  3. 导出 cookies.txt 文件放到项目根目录"
                        )
                    break

                feeds = data.get("data", {}).get("visionProfilePhotoList", {}).get("feeds", [])
                if not feeds:
                    break

                # 检查是否有重复数据来判断是否到最后一页
                new_ids = {feed.get("photo", {}).get("id", "") for feed in feeds if feed.get("photo")}
                existing_ids = {item.item_id for item in items}
                overlap = new_ids & existing_ids
                is_last_page = len(overlap) == len(new_ids) or len(feeds) < 20

                # 添加不重复的数据
                for feed in feeds:
                    photo = feed.get("photo", {})
                    if not photo or photo.get("id", "") in existing_ids:
                        continue
                    photo_url = photo.get("photoUrl", "")
                    if not photo_url:
                        continue
                    caption = photo.get("caption", "") or "untitled"
                    item = DownloadItem(
                        item_id=photo.get("id", ""),
                        item_type="video",
                        title=caption,
                        urls=[photo_url],
                        create_time=str(photo.get("timestamp", "")),
                        cover_url=photo.get("coverUrl") or photo.get("animatedCoverUrl"),
                        description=caption,
                    )
                    items.append(item)

                if is_last_page:
                    break

                # 数量限制
                if self.config.max_count > 0 and len(items) >= self.config.max_count:
                    items = items[:self.config.max_count]
                    break

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个快手作品"""
        import aiohttp
        import aiofiles

        saved_paths = []

        try:
            video_url = item.urls[0] if item.urls else ""
            if not video_url:
                return DownloadResult(False, item, error="无视频下载链接")

            filepath = self._make_filepath(save_dir, item, ".mp4")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.kuaishou.com/",
            }

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

            # 保存封面
            if self.config.save_cover and item.cover_url:
                cover_path = self._make_filepath(save_dir, item, "_cover.jpg")
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

            return DownloadResult(True, item, saved_paths=saved_paths)

        except Exception as e:
            return DownloadResult(False, item, error=str(e))
