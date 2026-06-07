"""
ShortVideoDownload - 小红书下载引擎
通过解析用户主页HTML获取笔记列表，yt-dlp 下载单个笔记
"""
import os
import re
import json
import asyncio
from typing import List, Optional
from urllib.parse import quote

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


class XiaohongshuEngine(BaseEngine):
    """小红书下载引擎 - HTML解析 + yt-dlp"""

    platform = "xiaohongshu"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""

    def _extract_user_id(self, url: str) -> str:
        """从小红书 URL 提取用户 ID"""
        match = re.search(r'/user/profile/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL 提取小红书用户 ID: {url}")

    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def _parse_initial_state(self, html: str) -> dict:
        """从HTML页面中解析 __INITIAL_STATE__ JSON数据"""
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*</script>', html, re.DOTALL)
        if not match:
            match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?})\s*;?\s*$', html, re.MULTILINE)
        if not match:
            return {}
        try:
            # 小红书的JSON中可能包含undefined，需要替换
            json_str = match.group(1)
            json_str = json_str.replace('undefined', 'null')
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {}

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取小红书用户的所有笔记列表
        通过解析用户主页HTML中的 __INITIAL_STATE__ 获取笔记
        这种方式不需要 X-s/X-t 签名，比API更可靠
        """
        import aiohttp

        user_id = self._extract_user_id(user_url)
        headers = self._build_headers()
        items = []
        cursor = ""
        has_more = True
        page = 1

        async with aiohttp.ClientSession() as session:
            while has_more:
                # 方式1: 尝试通过用户主页HTML获取笔记列表
                # 小红书用户主页会包含 __INITIAL_STATE__ 数据
                profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
                if cursor:
                    profile_url += f"?cursor={cursor}"

                try:
                    async with session.get(
                        profile_url, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                        allow_redirects=True,
                    ) as resp:
                        if resp.status != 200:
                            if not items:
                                raise RuntimeError(
                                    f"小红书页面请求失败 (HTTP {resp.status})，可能需要提供 Cookie。\n\n"
                                    f"请使用以下方式之一提供 Cookie：\n"
                                    f"  1. --cookie \"your_cookie\"\n"
                                    f"  2. --browser-cookie firefox\n"
                                    f"  3. 导出 cookies.txt 文件放到项目根目录"
                                )
                            break

                        html = await resp.text()

                except RuntimeError:
                    raise
                except Exception as e:
                    if not items:
                        raise RuntimeError(
                            f"小红书页面请求失败: {e}\n\n"
                            f"可能需要提供 Cookie，请使用 --cookie 或 --browser-cookie 参数。"
                        )
                    break

                # 解析 __INITIAL_STATE__
                state = self._parse_initial_state(html)
                if not state:
                    if not items:
                        raise RuntimeError(
                            "无法解析小红书页面数据，可能需要提供 Cookie。\n\n"
                            "请使用以下方式之一提供 Cookie：\n"
                            "  1. --cookie \"your_cookie\"\n"
                            "  2. --browser-cookie firefox\n"
                            "  3. 导出 cookies.txt 文件放到项目根目录"
                        )
                    break

                # 从 state 中提取笔记列表
                # 数据路径: user.notes 是一个 list，notes[0] 是第一个 tab 的笔记列表
                # 每个 tab 元素格式: {id: "noteId", noteCard: {type, displayTitle, cover, ...}}
                user_data = state.get("user", {})
                notes_raw = user_data.get("notes", [])

                # 检查是否被重定向到登录页
                logged_in = user_data.get("loggedIn", False)
                if not logged_in and not notes_raw:
                    if not items:
                        raise RuntimeError(
                            "小红书需要登录 Cookie 才能访问用户主页。\n\n"
                            "请使用以下方式之一提供 Cookie：\n"
                            "  1. --cookie \"your_cookie\"\n"
                            "  2. --browser-cookie firefox\n"
                            "  3. 导出 cookies.txt 文件放到项目根目录"
                        )
                    break

                # 解析笔记列表
                notes = []
                if isinstance(notes_raw, list) and len(notes_raw) > 0:
                    # notes_raw[0] 是第一个 tab（笔记），是 list 类型
                    first_tab = notes_raw[0]
                    if isinstance(first_tab, list):
                        notes = first_tab
                    elif isinstance(first_tab, dict):
                        # 可能是 dict 格式
                        notes = first_tab.get("notes", []) or first_tab.get("noteCards", [])
                        if not isinstance(notes, list):
                            notes = [first_tab]
                elif isinstance(notes_raw, dict):
                    notes = notes_raw.get("notes", [])

                if not notes:
                    if not items:
                        raise RuntimeError(
                            "未获取到笔记数据，可能需要提供 Cookie。\n\n"
                            "请使用以下方式之一提供 Cookie：\n"
                            "  1. --cookie \"your_cookie\"\n"
                            "  2. --browser-cookie firefox\n"
                            "  3. 导出 cookies.txt 文件放到项目根目录"
                        )
                    break

                for note_item in notes:
                    # note_item 可能是 {id, noteCard: {...}} 格式
                    if isinstance(note_item, dict) and "noteCard" in note_item:
                        note_card = note_item["noteCard"]
                        note_id = note_item.get("id", "") or note_card.get("noteId", "")
                        note_type = note_card.get("type", "")
                        display_title = note_card.get("displayTitle", "") or note_card.get("title", "") or "untitled"
                        cover = note_card.get("cover", {})
                    elif isinstance(note_item, dict):
                        # 直接是笔记数据
                        note_id = note_item.get("noteId", "") or note_item.get("id", "") or note_item.get("note_id", "")
                        note_type = note_item.get("type", "")
                        display_title = note_item.get("displayTitle", "") or note_item.get("title", "") or note_item.get("display_title", "") or "untitled"
                        cover = note_item.get("cover", {})
                    else:
                        continue

                    is_video = note_type == "video"

                    # 过滤
                    if self.config.video_only and not is_video:
                        continue
                    if self.config.image_only and is_video:
                        continue

                    # 构建笔记 URL
                    note_url = f"https://www.xiaohongshu.com/explore/{note_id}"

                    # 封面图
                    cover_url = ""
                    if isinstance(cover, dict):
                        cover_url = cover.get("url", "") or cover.get("urlDefault", "")

                    item = DownloadItem(
                        item_id=note_id,
                        item_type="video" if is_video else "image",
                        title=display_title,
                        urls=[note_url],
                        create_time=str(note_item.get("lastUpdateTime", "") or note_item.get("time", "")),
                        cover_url=cover_url,
                        description=display_title,
                    )
                    items.append(item)

                # 翻页 - HTML解析方式不支持翻页，只获取首屏数据
                # 如需翻页，需要使用API方式（需要X-s/X-t签名）
                break

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个小红书笔记（通过 yt-dlp）"""
        try:
            note_url = item.urls[0] if item.urls else ""
            if not note_url:
                return DownloadResult(False, item, error="无笔记链接")

            # 使用 yt-dlp 下载单个笔记
            quality_map = {
                "best": "bestvideo+bestaudio/best",
                "hd": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "sd": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            }
            fmt = quality_map.get(self.config.quality, "best")

            ext = ".mp4" if item.is_video else ""
            filepath = self._make_filepath(save_dir, item, ext)

            cmd = [
                "yt-dlp",
                "-f", fmt if item.is_video else "best",
                "--no-warnings",
                "-o", filepath,
                "--retries", str(self.config.max_retries),
                "--socket-timeout", str(self.config.timeout),
            ]

            # Cookie（优先级：cookies-from-browser > cookies.txt > 临时文件）
            cookie_added = False

            # 方式1: 直接让 yt-dlp 从浏览器提取（最可靠）
            if self.config.browser_cookie:
                cmd.extend(["--cookies-from-browser", self.config.browser_cookie])
                cookie_added = True

            # 方式2: 使用项目根目录的 cookies.txt 文件
            if not cookie_added:
                from utils import load_cookies_from_file
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cookie_file_path = os.path.join(project_root, "cookies.txt")
                if os.path.exists(cookie_file_path):
                    cmd.extend(["--cookies", cookie_file_path])
                    cookie_added = True

            # 方式3: 将 Cookie 字符串写入临时文件
            if not cookie_added and self._cookie:
                import tempfile
                cookie_file = os.path.join(tempfile.gettempdir(), "svd_xhs_cookies.txt")
                with open(cookie_file, 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    for part in self._cookie.split(';'):
                        part = part.strip()
                        if '=' in part:
                            name, value = part.split('=', 1)
                            name = name.strip()
                            value = value.strip()
                            f.write(f".xiaohongshu.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")
                cmd.extend(["--cookies", cookie_file])

            if self.config.proxies:
                cmd.extend(["--proxy", self.config.proxies])

            cmd.append(note_url)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout * 10
            )

            saved_paths = []

            # 检查下载结果 - yt-dlp 可能改变扩展名
            if proc.returncode == 0:
                # 查找实际输出的文件
                base_name = os.path.splitext(filepath)[0]
                for possible_ext in ['.mp4', '.webm', '.jpg', '.png', '.webp']:
                    if os.path.exists(base_name + possible_ext):
                        saved_paths.append(base_name + possible_ext)
                # 也检查原始路径
                if os.path.exists(filepath):
                    saved_paths.append(filepath)

            if saved_paths:
                return DownloadResult(True, item, saved_paths=saved_paths)
            else:
                err = stderr.decode("utf-8", errors="replace")[:300]
                return DownloadResult(False, item, error=err)

        except asyncio.TimeoutError:
            return DownloadResult(False, item, error="下载超时")
        except Exception as e:
            return DownloadResult(False, item, error=str(e))
