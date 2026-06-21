"""
ShortVideoDownload - 小红书下载引擎
使用 Playwright 真实浏览器环境获取数据（绕过反爬虫检测）
aiohttp 直接下载视频/图片
"""
import os
import re
import json
import asyncio
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


STEALTH_JS = '''
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = {runtime: {}};
'''

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'


class XiaohongshuEngine(BaseEngine):
    """小红书下载引擎 - Playwright 获取数据 + aiohttp 直接下载"""

    platform = "xiaohongshu"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or self._load_cookie()
        self._playwright = None
        self._browser = None

    def _load_cookie(self) -> str:
        """从 cookies.txt 加载小红书 Cookie"""
        from utils import load_cookies_from_file
        return load_cookies_from_file("xiaohongshu.com")

    def _extract_user_id(self, url: str) -> str:
        """从 URL 提取用户 ID"""
        match = re.search(r'/user/profile/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从 URL 提取小红书用户 ID: {url}")

    def _parse_cookies(self) -> list:
        """解析 cookie 字符串为 Playwright 格式"""
        cookies = []
        if not self._cookie:
            return cookies
        secure_keys = ('web_session', 'id_token', 'acw_tc')
        for item in self._cookie.split(';'):
            item = item.strip()
            if '=' in item:
                k, v = item.split('=', 1)
                k = k.strip()
                v = v.strip()
                cookies.append({
                    'name': k,
                    'value': v,
                    'domain': '.xiaohongshu.com',
                    'path': '/',
                    'secure': k in secure_keys,
                    'httpOnly': k in secure_keys,
                })
        return cookies

    async def _ensure_browser(self):
        """确保 Playwright 浏览器已启动"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
            )

    async def _close_browser(self):
        """关闭 Playwright 浏览器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _new_context(self):
        """创建新的浏览器上下文"""
        await self._ensure_browser()
        context = await self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={'width': 1920, 'height': 1080},
        )
        await context.add_init_script(STEALTH_JS)
        await context.add_cookies(self._parse_cookies())
        return context

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取小红书用户的所有笔记列表
        使用 Playwright 访问用户主页，从 Pinia store 获取笔记列表
        滚动加载所有笔记，然后逐个获取详情
        """
        if not self._cookie:
            raise RuntimeError(
                "小红书需要登录 Cookie 才能获取用户作品列表。\n\n"
                "请使用以下方式之一提供 Cookie:\n"
                "  1. --cookie \"your_cookie\"\n"
                "  2. --browser-cookie firefox\n"
                "  3. 导出 cookies.txt 文件放到项目根目录"
            )

        user_id = self._extract_user_id(user_url)

        # 保留原始 URL 中的 xsec_token 等参数
        from urllib.parse import urlparse, parse_qs, urlencode
        parsed = urlparse(user_url)
        query_params = parse_qs(parsed.query)
        profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        if query_params:
            flat_params = {k: v[0] for k, v in query_params.items() if v}
            profile_url += f"?{urlencode(flat_params)}"

        context = await self._new_context()
        page = await context.new_page()

        try:
            # 访问用户主页
            self._log(f"  访问用户主页: {profile_url}")
            try:
                await page.goto(profile_url, wait_until='domcontentloaded', timeout=20000)
            except Exception as e:
                self._log(f"  页面加载警告: {e}")
            await page.wait_for_timeout(3000)

            # 检查登录状态
            login_info = await page.evaluate('''() => {
                try {
                    const app = document.querySelector('#app').__vue_app__;
                    const pinia = app.config.globalProperties.$pinia;
                    const userStore = pinia._s.get('user');
                    return {
                        loggedIn: userStore?.loggedIn,
                        nickname: userStore?.userInfo?.nickname || '',
                    };
                } catch(e) {
                    return {error: e.message};
                }
            }''')

            if not login_info or not login_info.get('loggedIn'):
                raise RuntimeError(
                    "小红书 Cookie 已失效或未登录，无法获取笔记数据。\n"
                    "请重新导出 cookies.txt 中的 xiaohongshu.com Cookie。"
                )

            nickname = login_info.get('nickname', '')
            self._log(f"  已登录: {nickname}")

            # 滚动加载所有笔记
            notes = await self._scroll_and_collect_notes(page)
            self._log(f"  共获取 {len(notes)} 个笔记")

            if not notes:
                self._log("  未获取到任何笔记")
                return []

            # 在获取详情前应用 max_count 限制（避免不必要地获取所有详情）
            if self.config.max_count > 0:
                notes = notes[:self.config.max_count]
                self._log(f"  限制下载前 {len(notes)} 个")

            # 逐个获取笔记详情
            items = []
            total = len(notes)
            for idx, note_info in enumerate(notes, 1):
                note_id = note_info.get('noteId') or note_info.get('id', '')
                if not note_id:
                    continue

                self._log(f"  [{idx}/{total}] 获取详情: {note_info.get('title', '')[:40]}")
                item = await self._fetch_note_detail(page, note_info, nickname)
                if item:
                    items.append(item)
                else:
                    self._log(f"  [{idx}/{total}] 跳过: 无法获取详情")

                # 请求间隔，避免触发反爬
                await asyncio.sleep(0.5)

            return items

        finally:
            await context.close()
            await self._close_browser()

    async def _scroll_and_collect_notes(self, page) -> list:
        """滚动页面加载所有笔记，返回笔记列表"""
        notes = []
        last_count = 0
        no_change_count = 0

        for scroll_idx in range(30):  # 最多滚动 30 次
            current_notes = await page.evaluate('''() => {
                try {
                    const app = document.querySelector('#app').__vue_app__;
                    const pinia = app.config.globalProperties.$pinia;
                    const userStore = pinia._s.get('user');
                    const notes = userStore?.notes?.[0] || [];
                    return notes.map(n => ({
                        id: n.id,
                        noteId: n.noteCard?.noteId || n.id,
                        xsecToken: n.xsecToken || n.noteCard?.xsecToken || '',
                        type: n.noteCard?.type || '',
                        title: n.noteCard?.displayTitle || '',
                    }));
                } catch(e) {
                    return [];
                }
            }''')

            if current_notes and len(current_notes) > len(notes):
                notes = current_notes
                no_change_count = 0
            else:
                no_change_count += 1

            # 连续 3 次没有新笔记，认为已加载完
            if no_change_count >= 3:
                break

            # 滚动到底部
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(2000)

        return notes

    async def _fetch_note_detail(self, page, note_info: dict, nickname: str) -> Optional[DownloadItem]:
        """获取单个笔记的详情（含视频/图片URL）"""
        note_id = note_info.get('noteId') or note_info.get('id', '')
        xsec_token = note_info.get('xsecToken', '')

        note_url = f'https://www.xiaohongshu.com/explore/{note_id}'
        if xsec_token:
            note_url += f'?xsec_token={xsec_token}&xsec_source=pc_note'

        try:
            await page.goto(note_url, wait_until='domcontentloaded', timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        # 从 Pinia store 获取笔记详情
        detail = await page.evaluate('''(noteId) => {
            try {
                const app = document.querySelector('#app').__vue_app__;
                const pinia = app.config.globalProperties.$pinia;
                const noteStore = pinia._s.get('note');
                const detailMap = noteStore?.noteDetailMap || {};
                const detail = detailMap[noteId] || Object.values(detailMap)[0];
                if (!detail) return null;
                const note = detail.note;
                if (!note) return null;

                // 提取视频 URL
                let videoUrl = '';
                if (note.video) {
                    const streams = note.video.media?.stream;
                    if (streams) {
                        for (const k of ['h264', 'h265', 'av1']) {
                            if (streams[k] && streams[k][0]) {
                                videoUrl = streams[k][0].masterUrl || '';
                                if (!videoUrl && streams[k][0].backupUrls) {
                                    videoUrl = streams[k][0].backupUrls[0] || '';
                                }
                                if (videoUrl) break;
                            }
                        }
                    }
                }

                // 提取图片 URL 列表
                let imageUrls = [];
                if (note.imageList) {
                    imageUrls = note.imageList.map(img => {
                        const infoList = img.infoList || [];
                        const dft = infoList.find(il => il.imageScene === 'WB_DFT');
                        return dft?.url || img.urlDefault || '';
                    }).filter(u => u);
                }

                return {
                    type: note.type || '',
                    title: note.title || '',
                    desc: note.desc || '',
                    noteId: note.noteId || noteId,
                    videoUrl: videoUrl,
                    imageUrls: imageUrls,
                    coverUrl: note.imageList?.[0]?.urlDefault || note.video?.image?.firstFrame || '',
                    nickname: note.user?.nickname || '',
                    createTime: note.time || 0,
                };
            } catch(e) {
                return null;
            }
        }''', note_id)

        if not detail:
            return None

        # 确定类型和 URL
        note_type = detail.get('type', '')
        video_url = detail.get('videoUrl', '')
        image_urls = detail.get('imageUrls', [])

        # 判断是视频还是图片
        is_video = note_type == 'video' or (video_url and not image_urls)
        is_image = note_type == 'normal' or bool(image_urls)

        if is_video and video_url:
            urls = [video_url]
            item_type = "video"
        elif is_image and image_urls:
            urls = image_urls
            item_type = "image"
        else:
            return None

        # 过滤
        if self.config.video_only and item_type != "video":
            return None
        if self.config.image_only and item_type != "image":
            return None

        # 将 http:// 转换为 https://
        urls = [u.replace('http://', 'https://') if u.startswith('http://') else u for u in urls]
        cover_url = detail.get('coverUrl', '')
        if cover_url and cover_url.startswith('http://'):
            cover_url = 'https://' + cover_url[7:]

        # 标题：优先用详情页的 title，其次用列表的 displayTitle
        title = detail.get('title', '') or note_info.get('title', '') or f'note_{note_id}'
        desc = detail.get('desc', '') or title
        note_nickname = detail.get('nickname', '') or nickname

        return DownloadItem(
            item_id=note_id,
            item_type=item_type,
            title=desc,  # build_display_title 会处理
            urls=urls,
            create_time=str(detail.get('createTime', '')),
            nickname=note_nickname,
            cover_url=cover_url,
            description=desc,
        )

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单个小红书笔记（HTTP 直接下载，与抖音引擎一致）"""
        import aiohttp
        import aiofiles

        saved_paths = []

        try:
            headers = {
                "Referer": "https://www.xiaohongshu.com/",
                "User-Agent": USER_AGENT,
            }

            if item.is_video:
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频下载链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")

                # 检查文件是否已存在（跳过 0 字节残留）
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return DownloadResult(True, item, saved_paths=[filepath], skipped=True, skip_reason="已存在")

                # 下载视频（带重试，网络中断时自动重试）
                max_retries = self.config.max_retries
                last_error = None
                for attempt in range(max_retries):
                    try:
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

            return DownloadResult(True, item, saved_paths=saved_paths)

        except Exception as e:
            return DownloadResult(False, item, error=str(e))
