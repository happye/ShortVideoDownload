"""
ShortVideoDownload - B站下载引擎
使用B站 API 获取用户视频列表，yt-dlp 下载单个视频
"""
import os
import re
import json
import asyncio
import hashlib
from typing import List, Optional
from datetime import datetime

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


class BilibiliEngine(BaseEngine):
    """B站下载引擎 - API + yt-dlp"""

    platform = "bilibili"

    # wbi 签名相关
    MIXIN_KEY_ENC_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
        27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 16,
        20, 36, 34, 17, 6, 22, 48, 44, 40, 21, 25, 13, 4, 52, 37, 26,
        55, 1, 24, 51, 7, 56, 57, 30, 11, 0, 54, 59, 62, 61, 60, 63,
    ]

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""
        self._wbi_keys = None  # 缓存的 img_key + sub_key

    def _extract_uid(self, url: str) -> str:
        """从 URL 提取B站 UID"""
        match = re.search(r'space\.bilibili\.com/(\d+)', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL 提取B站 UID: {url}")

    @staticmethod
    def _normalize_bvid(video_id: str) -> tuple:
        """
        规范化 video_id，返回 (api_param_key, api_param_value, video_url)
        - BV号: ('bvid', 'BVxxxx', 'https://www.bilibili.com/video/BVxxxx')
        - av号: ('aid', '123456',  'https://www.bilibili.com/video/av123456')
        """
        vid = video_id.strip()
        if vid.lower().startswith("av") and vid[2:].isdigit():
            aid = vid[2:]
            return ("aid", aid, f"https://www.bilibili.com/video/av{aid}")
        # 默认按 BV 号处理
        return ("bvid", vid, f"https://www.bilibili.com/video/{vid}")

    async def fetch_single_item(self, video_id: str, original_url: str = None) -> Optional[DownloadItem]:
        """
        获取单个B站视频信息（用于单视频 URL 下载）
        Args:
            video_id: BVID (BVxxxx) 或 AVID (av123456)
            original_url: 原始 URL（含 spm_id_from 等参数，优先作为下载源）
        Returns:
            DownloadItem，标题来自 view API；API 失败时退化为 video_id 作标题
        """
        import aiohttp

        param_key, param_value, default_video_url = self._normalize_bvid(video_id)
        # 优先用原始 URL（含的参数对风控/统计无影响，但保留以保持原始来源）
        video_url = original_url or default_video_url

        headers = self._build_headers()
        api_url = "https://api.bilibili.com/x/web-interface/view"

        # 默认值：API 失败时退化为用 video_id 作标题
        title = video_id
        description = ""
        pic = ""
        created = ""
        aid = ""
        bvid = video_id if video_id.startswith("BV") else ""
        nickname = None
        uid = None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    api_url, params={param_key: param_value}, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("code") == 0:
                            v = data.get("data", {})
                            title = v.get("title", "") or video_id
                            description = v.get("desc", "") or ""
                            pic = v.get("pic", "") or ""
                            created = str(v.get("pubdate", 0))
                            aid = str(v.get("aid", ""))
                            bvid = v.get("bvid", "") or bvid
                            owner = v.get("owner", {}) or {}
                            nickname = owner.get("name") or None
                            uid = str(owner.get("mid") or "") or None
        except Exception:
            # API 失败（412 风控/网络异常等）：退化为用 video_id 作标题
            # yt-dlp 仍可直接下载，文件名会是 {video_id}_{video_id}.mp4
            pass

        # item_id 优先用 bvid，否则用 av{aid}，再否则用原始 video_id
        item_id = bvid or (f"av{aid}" if aid else video_id)

        return DownloadItem(
            item_id=item_id,
            item_type="video",
            title=title,
            urls=[video_url],
            create_time=created,
            cover_url=pic,
            description=description,
            nickname=nickname,
            uid=uid,
        )

    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    @staticmethod
    def _get_mixin_key(raw_key: str) -> str:
        """生成 wbi mixin key"""
        return "".join(raw_key[i] for i in BilibiliEngine.MIXIN_KEY_ENC_TAB)[:32]

    def _sign_wbi(self, params: dict) -> dict:
        """对参数进行 wbi 签名"""
        if not self._wbi_keys:
            return params

        img_key, sub_key = self._wbi_keys
        mixin_key = self._get_mixin_key(img_key + sub_key)

        # 添加 wts
        params["wts"] = int(datetime.now().timestamp())

        # 按 key 排序
        params = dict(sorted(params.items()))

        # 过滤非法字符
        query = "&".join(
            f"{k}={v}" for k, v in params.items()
            if all(c not in str(v) for c in "!'()*")
        )

        # 计算 w_rid
        w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
        params["w_rid"] = w_rid

        return params

    async def _fetch_wbi_keys(self, session) -> None:
        """获取 wbi 签名所需的 key"""
        if self._wbi_keys:
            return

        import aiohttp

        # 获取 nav API 中的 img_url 和 sub_url
        nav_url = "https://api.bilibili.com/x/web-interface/nav"
        headers = self._build_headers()

        try:
            async with session.get(nav_url, headers=headers) as resp:
                if resp.status == 412:
                    raise RuntimeError(
                        "B站 API 返回 412 (请求被拦截)，需要提供 Cookie。\n"
                        "请使用 --cookie 或 --browser-cookie 参数提供 Cookie。"
                    )
                data = await resp.json()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"获取B站 wbi 签名密钥失败: {e}\n"
                f"可能需要提供 Cookie，请使用 --cookie 或 --browser-cookie 参数。"
            )

        wbi_img = data.get("data", {}).get("wbi_img", {})
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")

        if img_url and sub_url:
            # 提取 key（文件名不含扩展名）
            self._wbi_keys = (
                img_url.rsplit("/", 1)[-1].split(".")[0],
                sub_url.rsplit("/", 1)[-1].split(".")[0],
            )

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """获取B站用户的所有视频"""
        import aiohttp

        uid = self._extract_uid(user_url)
        headers = self._build_headers()
        # 使用更具体的 Referer，有助于通过风控
        headers["Referer"] = f"https://space.bilibili.com/{uid}/"
        items = []
        pn = 1  # 页码

        async with aiohttp.ClientSession() as session:
            while True:
                # 优先使用旧API端点（不需要wbi签名，更稳定）
                # 如果旧API失败，再尝试wbi签名API
                api_url = "https://api.bilibili.com/x/space/arc/search"
                params = {
                    "mid": uid,
                    "pn": str(pn),
                    "ps": "30",
                    "order": "pubdate",
                }

                try:
                    async with session.get(
                        api_url, params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        data = await resp.json()
                except Exception as e:
                    if not items:
                        raise RuntimeError(
                            f"B站 API 请求失败: {e}\n\n"
                            f"如果持续失败，请尝试提供 Cookie：\n"
                            f"  --cookie \"your_cookie\" 或 --browser-cookie chrome"
                        )
                    break

                code = data.get("code", -1)
                # -799: 请求过于频繁，等待后重试
                if code == -799:
                    import asyncio as _asyncio
                    wait_time = 5
                    for retry in range(3):
                        await _asyncio.sleep(wait_time)
                        try:
                            async with session.get(
                                api_url, params=params, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=15),
                            ) as resp:
                                data = await resp.json()
                                code = data.get("code", -1)
                                if code != -799:
                                    break
                                wait_time *= 2
                        except Exception:
                            break

                code = data.get("code", -1)
                if code != 0:
                    msg = data.get("message", "未知错误")
                    # 旧API失败时，尝试wbi签名API
                    if code in (-352, -403) and not self._wbi_keys:
                        try:
                            await self._fetch_wbi_keys(session)
                            if self._wbi_keys:
                                # 用wbi签名重试
                                wbi_url = "https://api.bilibili.com/x/space/wbi/arc/search"
                                wbi_params = self._sign_wbi(params.copy())
                                async with session.get(
                                    wbi_url, params=wbi_params, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=15),
                                ) as resp2:
                                    data = await resp2.json()
                                    code = data.get("code", -1)
                        except Exception:
                            pass  # wbi也失败，继续用原始错误

                    if code != 0:
                        if not items:
                            if code == -352:
                                raise RuntimeError(
                                    "B站 API 风控校验失败，需要提供登录 Cookie。\n"
                                    "请使用 --cookie 或 --browser-cookie 参数提供 Cookie。\n\n"
                                    "获取Cookie方法：\n"
                                    "  1. 在浏览器登录 bilibili.com\n"
                                    "  2. 使用浏览器扩展导出 cookies.txt 文件放到项目根目录\n"
                                    "  3. 或使用 --cookie 参数手动提供 Cookie"
                                )
                            raise RuntimeError(
                                f"B站 API 返回错误 (code={code}): {msg}\n\n"
                                f"如果提示需要登录，请提供 Cookie：\n"
                                f"  --cookie \"your_cookie\" 或 --browser-cookie chrome"
                            )
                        break

                vlist = data.get("data", {}).get("list", {}).get("vlist", [])
                if not vlist:
                    break

                for video in vlist:
                    bvid = video.get("bvid", "")
                    aid = video.get("aid", "")
                    title = video.get("title", "") or "untitled"
                    description = video.get("description", "")
                    created = video.get("created", 0)
                    pic = video.get("pic", "")

                    # 构建视频 URL
                    video_url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

                    item = DownloadItem(
                        item_id=bvid or str(aid),
                        item_type="video",
                        title=title,
                        urls=[video_url],
                        create_time=str(created),
                        cover_url=pic,
                        description=description,
                    )
                    items.append(item)

                # 翻页
                page_info = data.get("data", {}).get("page", {})
                total = page_info.get("count", 0)
                if pn * 30 >= total:
                    break
                pn += 1

                # 数量限制
                if self.config.max_count > 0 and len(items) >= self.config.max_count:
                    items = items[:self.config.max_count]
                    break

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个B站视频（通过 yt-dlp）"""
        try:
            video_url = item.urls[0] if item.urls else ""
            if not video_url:
                return DownloadResult(False, item, error="无视频链接")

            quality_map = {
                "best": "bestvideo+bestaudio/best",
                "hd": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
                "sd": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            }
            fmt = quality_map.get(self.config.quality, "bestvideo+bestaudio/best")

            filepath = self._make_filepath(save_dir, item, ".mp4")

            cmd = [
                "yt-dlp",
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", filepath,
                "--no-warnings",
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
                cookie_file = os.path.join(tempfile.gettempdir(), "svd_bili_cookies.txt")
                with open(cookie_file, 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    for part in self._cookie.split(';'):
                        part = part.strip()
                        if '=' in part:
                            name, value = part.split('=', 1)
                            f.write(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name.strip()}\t{value.strip()}\n")
                cmd.extend(["--cookies", cookie_file])

            if self.config.proxies:
                cmd.extend(["--proxy", self.config.proxies])

            cmd.append(video_url)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout * 20
            )

            saved_paths = []
            if proc.returncode == 0 and os.path.exists(filepath):
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
