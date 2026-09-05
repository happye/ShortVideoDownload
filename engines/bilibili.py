"""
ShortVideoDownload - B站下载引擎
完全基于 yt-dlp：
- fetch_user_items: yt-dlp --flat-playlist 列出用户所有视频（自动处理投稿/合集/系列/子合集）
- download_item: yt-dlp 下载并用 %(title)s_%(id)s.%(ext)s 模板命名
- fetch_single_item: 调 view API 拿标题/UP主，yt-dlp 下载

设计理由：B站旧 API（x/space/arc/search、x/polymer/space/seasons_series_list）已废弃返回 404，
新 API 需要 wbi 签名且风控严格；yt-dlp 内部维护 API 路径和签名，跟着升级，且能自动列出
投稿+合集+系列的全部视频，最稳定完整。
"""
import os
import re
import asyncio
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig
from utils import ytdlp_cmd


class BilibiliEngine(BaseEngine):
    """B站下载引擎 - 完全基于 yt-dlp"""

    platform = "bilibili"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""

    def _extract_uid(self, url: str) -> str:
        """从 URL 提取B站 UID（支持所有 space.bilibili.com 子路径）"""
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
        """构建请求头（用于 view API）"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        if self._cookie:
            headers["Cookie"] = self._cookie
        return headers

    def _build_ytdlp_cookie_args(self) -> list:
        """
        构造 yt-dlp 的 Cookie 参数列表
        优先级：--cookies-from-browser > cookies.txt > 临时文件 > 自动尝试浏览器 > 无

        注意：B站无 Cookie 会触发 412 Precondition Failed 风控，
        无法获取任何视频格式（包括免费的 1080p）。
        """
        # 方式1: 用户指定的浏览器（--browser-cookie firefox/edge/chrome）
        if self.config.browser_cookie:
            return ["--cookies-from-browser", self.config.browser_cookie]

        # 方式2: 项目根目录的 cookies.txt 文件（Netscape 格式）
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cookie_file_path = os.path.join(project_root, "cookies.txt")
        if os.path.exists(cookie_file_path):
            # 检查 cookies.txt 是否包含 B站 Cookie
            if self._cookies_file_has_bilibili(cookie_file_path):
                return ["--cookies", cookie_file_path]

        # 方式3: 将 Cookie 字符串写入临时文件（--cookie "k=v; k=v"）
        if self._cookie:
            import tempfile
            cookie_file = os.path.join(tempfile.gettempdir(), "svd_bili_cookies.txt")
            with open(cookie_file, 'w', encoding='utf-8') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for part in self._cookie.split(';'):
                    part = part.strip()
                    if '=' in part:
                        name, value = part.split('=', 1)
                        f.write(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name.strip()}\t{value.strip()}\n")
            return ["--cookies", cookie_file]

        # 方式4: 自动尝试所有浏览器（yt-dlp 自己处理）
        # yt-dlp 会依次尝试，失败则跳过；firefox 不受 App-Bound Encryption 影响
        for browser in ("firefox", "edge", "chrome"):
            try:
                # 用 yt-dlp 快速验证浏览器是否能提取 B站 Cookie
                if self._browser_has_bilibili_cookie(browser):
                    return ["--cookies-from-browser", browser]
            except Exception:
                continue

        return []

    @staticmethod
    def _cookies_file_has_bilibili(cookie_file_path: str) -> bool:
        """检查 cookies.txt 文件是否包含 B站 Cookie"""
        try:
            with open(cookie_file_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if 'bilibili.com' in line.lower() and not line.startswith('#'):
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _browser_has_bilibili_cookie(browser: str) -> bool:
        """用 rookiepy 快速检测浏览器是否登录了 B站（不抛异常）"""
        try:
            import rookiepy
            func_map = {
                "firefox": rookiepy.firefox,
                "chrome": rookiepy.chrome,
                "edge": rookiepy.edge,
            }
            func = func_map.get(browser.lower())
            if not func:
                return False
            cookies = func(domains=[".bilibili.com"])
            # 至少要有 SESSDATA 才算登录
            return any(c.get("name") == "SESSDATA" for c in cookies)
        except Exception:
            # Edge/Chrome v130+ App-Bound Encryption 会抛异常
            return False

    @staticmethod
    def _format_no_cookie_error(action: str) -> str:
        """生成无 Cookie 错误提示（含解决方案）

        B站无 Cookie 会触发 412 Precondition Failed 风控，
        连免费的 1080p 都拿不到。必须提供 Cookie 才能下载任何画质。
        """
        return (
            f"B站{action}失败：HTTP 412 Precondition Failed（无 Cookie 风控）\n\n"
            f"原因：B站要求登录 Cookie 才能访问视频格式列表，没 Cookie 连免费的 1080p 都拿不到。\n"
            f"注意：1080p 不需要大会员，但必须登录（普通账号即可）。\n\n"
            f"解决方案（任选其一）：\n"
            f"  1. 运行专用 Cookie 获取工具（推荐，自动绕过 Edge 加密）：\n"
            f"       python _fetch_bili_cookie.py\n"
            f"     该工具会启动 Edge 让你确认登录态，自动提取 Cookie 保存到 cookies.txt\n\n"
            f"  2. 从 Firefox 提取 Cookie（如果 Firefox 已登录 B站）：\n"
            f"       python svd.py \"URL\" --browser-cookie firefox\n\n"
            f"  3. 手动导出 cookies.txt：\n"
            f"     在浏览器装 \"Get cookies.txt LOCALLY\" 扩展，导出后放到项目根目录\n\n"
            f"  4. 手动提供 Cookie 字符串：\n"
            f"     F12 → Network → 刷新页面 → 找请求 → 复制 Cookie 头 →\n"
            f"       python svd.py \"URL\" --cookie \"复制的值\""
        )

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取B站用户的所有视频列表（用 yt-dlp --flat-playlist）
        - yt-dlp 自动处理投稿/合集/系列/子合集，保证视频列表完整
        - 支持的 URL：space.bilibili.com/{uid}、/{uid}/upload/video、/{uid}/channel/collectionDetail?sid=xxx 等
        - 标题用 BV 号占位，下载时 yt-dlp 用真实标题命名文件
        - 调一次 view API 拿 UP 主昵称（用于目录命名）
        """
        uid = self._extract_uid(user_url)

        # 构造 yt-dlp 命令（绑定 venv 内 yt-dlp，见 utils.ytdlp_cmd）
        cmd = [
            *ytdlp_cmd(),
            "--flat-playlist",
            "--no-progress",
            "--no-warnings",
            "-O", "%(id)s",
        ]
        cmd.extend(self._build_ytdlp_cookie_args())
        if self.config.proxies:
            cmd.extend(["--proxy", self.config.proxies])
        cmd.append(user_url)

        # 调用 yt-dlp 列出所有视频
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 yt-dlp 命令，请先安装：pip install yt-dlp"
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                "yt-dlp 列出视频超时（180s），可能网络问题或被风控。\n"
                "建议提供 Cookie：--browser-cookie chrome 或 cookies.txt 文件。"
            )

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            # 检测 412 风控（无 Cookie 时 B站直接拒绝）
            if "412" in err or "Precondition Failed" in err:
                raise RuntimeError(self._format_no_cookie_error("列出视频"))
            raise RuntimeError(
                f"yt-dlp 列出视频失败 (exit={proc.returncode}): {err}\n\n"
                f"如果提示需要登录，请提供 Cookie：\n"
                f"  --cookie \"your_cookie\" 或 --browser-cookie firefox"
            )

        # 解析 BV 号（yt-dlp --flat-playlist -O "%(id)s" 每行输出一个 ID）
        bvids = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            # B站视频 ID 格式：BV + 10 位字母数字（如 BV1WV3g6eE9z）
            if re.match(r'^BV[A-Za-z0-9]{10}$', line):
                bvids.append(line)

        if not bvids:
            raise RuntimeError(
                "未找到任何视频，可能用户没有投稿或需要 Cookie。\n"
                "建议提供 Cookie：--browser-cookie firefox 或 cookies.txt 文件。"
            )

        # 调一次 view API 拿 UP 主昵称（用于按昵称创建目录）
        nickname = None
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.bilibili.com/x/web-interface/view",
                    params={"bvid": bvids[0]},
                    headers=self._build_headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("code") == 0:
                            owner = (data.get("data") or {}).get("owner") or {}
                            nickname = owner.get("name") or None
        except Exception:
            pass  # 失败时目录用 "unknown"

        # 构造 DownloadItem 列表
        items = []
        for bvid in bvids:
            items.append(DownloadItem(
                item_id=bvid,
                item_type="video",
                title=bvid,  # 占位，下载时 yt-dlp 用真实标题命名文件
                urls=[f"https://www.bilibili.com/video/{bvid}"],
                nickname=nickname,
                uid=uid,
            ))

        # 数量限制
        if self.config.max_count > 0:
            items = items[:self.config.max_count]

        return items

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """
        下载单个B站视频（通过 yt-dlp）
        - 文件命名用 yt-dlp 模板 %(title)s_%(id)s.%(ext)s（含 BV 号用于去重）
        - yt-dlp 自己获取真实标题，无需预先调 view API
        - 用 --print after_move:filepath 获取实际保存路径
        """
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

            # 用 yt-dlp 模板命名：发布日期_真实标题前15字_BV号.mp4
            # yt-dlp 获取真实标题和发布日期（upload_date 为 YYYYMMDD），BV 号后缀用于扫描去重
            output_template = os.path.join(save_dir, "%(upload_date)s_%(title).15s_%(id)s.%(ext)s")

            cmd = [
                *ytdlp_cmd(),
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", output_template,
                "--print", "after_move:filepath",  # 输出最终文件路径
                "--no-progress",
                "--no-warnings",
                "--retries", str(self.config.max_retries),
                "--socket-timeout", str(self.config.timeout),
            ]
            cmd.extend(self._build_ytdlp_cookie_args())

            if self.config.proxies:
                cmd.extend(["--proxy", self.config.proxies])

            cmd.append(video_url)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.config.timeout * 30
            )

            saved_paths = []
            if proc.returncode == 0:
                # 从 stdout 解析 yt-dlp 输出的实际文件路径
                for line in stdout.decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and os.path.exists(line):
                        saved_paths.append(line)

            if saved_paths:
                return DownloadResult(True, item, saved_paths=saved_paths)
            else:
                # yt-dlp 的 WARNING（如 cookie 解析）在 stderr 前部，
                # 真正的 ERROR 在末尾。从末尾截 1500 字符避免 WARNING 淹没关键错误。
                err_full = stderr.decode("utf-8", errors="replace")
                err = err_full[-1500:] if len(err_full) > 1500 else err_full
                # 检测 412 风控（无 Cookie 时 B站直接拒绝）
                if "412" in err_full or "Precondition Failed" in err_full:
                    return DownloadResult(False, item, error=self._format_no_cookie_error("下载视频"))
                return DownloadResult(False, item, error=err)

        except FileNotFoundError:
            return DownloadResult(False, item, error="未找到 yt-dlp 命令，请先安装：pip install yt-dlp")
        except asyncio.TimeoutError:
            return DownloadResult(False, item, error="下载超时")
        except Exception as e:
            return DownloadResult(False, item, error=str(e))
