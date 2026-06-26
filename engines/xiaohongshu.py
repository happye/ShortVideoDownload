"""
ShortVideoDownload - 小红书下载引擎
使用 Playwright 真实浏览器环境，拦截浏览器自身的 API 响应获取数据
aiohttp 直接下载视频/图片

反爬规避策略（基于全网调研 + 实测验证）:
1. 不手动调用 API（签名格式 XYW_ 不被接受，浏览器用 XYS_）
2. 让浏览器自己发请求（滚动触发），拦截响应 → 合法请求
3. 滚动间隔 5-10 秒随机 → 模拟人类浏览
4. 详情页访问间隔 10-15 秒 → 模拟人类阅读
5. 单次下载上限 20 个 → 控制使用强度
6. 检测到 461/captcha 立即停止 → 保护账号

注意：window._webmsxyw 返回的 XYW_ 签名格式已被 API 拒绝（406），
      必须让浏览器自己发请求（XYS_ 格式）然后拦截响应。
"""
import os
import re
import json
import random
import asyncio
from typing import List, Optional

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


# 安全配置（基于全网调研的最佳实践）
# 单次下载超过 20 个会显著增加被检测风险
MAX_DOWNLOAD_PER_SESSION = 20
# 滚动间隔（秒）- 模拟人类浏览速度
MIN_SCROLL_DELAY = 5.0
MAX_SCROLL_DELAY = 10.0
# 详情页访问间隔（秒）- 模拟人类阅读时间
MIN_DETAIL_DELAY = 10.0
MAX_DETAIL_DELAY = 15.0
# 最大滚动次数（每次获取约 30 个笔记）
MAX_SCROLL_COUNT = 5

STEALTH_JS = '''
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = {runtime: {}};
'''

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'


class XiaohongshuEngine(BaseEngine):
    """小红书下载引擎 - 拦截浏览器 API 响应 + aiohttp 直接下载"""

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

    @staticmethod
    def _random_delay(min_d: float, max_d: float) -> float:
        """随机延时（模拟人类行为）"""
        return random.uniform(min_d, max_d)

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """
        获取小红书用户的所有笔记列表
        通过拦截浏览器自身的 API 响应获取数据（不手动调用 API）
        """
        if not self._cookie:
            raise RuntimeError(
                "小红书需要登录 Cookie 才能获取用户作品列表。\n\n"
                "请使用以下方式之一提供 Cookie:\n"
                "  1. --cookie \"your_cookie\"\n"
                "  2. --browser-cookie firefox\n"
                "  3. 导出 cookies.txt 文件放到项目根目录"
            )

        # 单次下载上限保护
        effective_max = self.config.max_count
        if effective_max == 0 or effective_max > MAX_DOWNLOAD_PER_SESSION:
            if effective_max > MAX_DOWNLOAD_PER_SESSION:
                self._log(f"  ⚠ 单次下载超过 {MAX_DOWNLOAD_PER_SESSION} 会触发风控，已自动限制")
            effective_max = MAX_DOWNLOAD_PER_SESSION

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

        # 在 goto 之前注册响应拦截器
        # 原因：首次 user_posted API 在 page.goto() 期间就发出，
        # 如果拦截器在 goto 之后才注册，会错过首次响应，导致首屏 0 个笔记。
        # 滚动不会重新触发 user_posted（首次请求已返回数据，页面内部已渲染）。
        captured_data = {'notes': [], 'stop': False}
        seen_ids = set()

        async def on_response(response):
            if captured_data['stop']:
                return
            if '/api/sns/web/v1/user_posted' in response.url:
                if response.status == 461:
                    captured_data['stop'] = True
                    return
                if response.status == 200:
                    try:
                        json_data = await response.json()
                        if json_data.get('success'):
                            new_notes = json_data.get('data', {}).get('notes', [])
                            for n in new_notes:
                                nid = n.get('note_id', '')
                                if nid and nid not in seen_ids:
                                    seen_ids.add(nid)
                                    captured_data['notes'].append(n)
                    except Exception:
                        pass

        page.on('response', on_response)

        try:
            # 访问用户主页（仅一次，建立浏览器环境）
            self._log(f"  访问用户主页建立会话...")
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

            # 获取笔记列表（拦截器已在 goto 前注册，复用 captured_data）
            notes = await self._scroll_and_intercept_notes(page, effective_max, captured_data)
            self._log(f"  共获取 {len(notes)} 个笔记")

            if not notes:
                self._log("  未获取到任何笔记")
                return []

            # 逐个获取笔记详情（通过访问详情页，拦截 feed API 响应）
            items = []
            total = len(notes)
            for idx, note_info in enumerate(notes, 1):
                note_id = note_info.get('note_id') or note_info.get('noteId') or note_info.get('id', '')
                if not note_id:
                    continue

                self._log(f"  [{idx}/{total}] 获取详情: {note_info.get('display_title', '')[:40]}")

                try:
                    item = await self._fetch_note_detail_via_page(page, note_info, nickname)
                    if item:
                        items.append(item)
                    else:
                        self._log(f"  [{idx}/{total}] 跳过: 无法获取详情")
                except RuntimeError as e:
                    # 检测到风控，立即停止
                    if 'captcha' in str(e).lower() or 'blocked' in str(e).lower() or '461' in str(e):
                        self._log(f"  ⚠ 检测到风控限制，停止获取以保护账号: {e}")
                        break
                    raise

                # 随机延时（10-15 秒，模拟人类阅读）
                if idx < total:
                    delay = self._random_delay(MIN_DETAIL_DELAY, MAX_DETAIL_DELAY)
                    self._log(f"  等待 {delay:.1f}s（模拟阅读）...")
                    await asyncio.sleep(delay)

            return items

        finally:
            page.remove_listener('response', on_response)
            await context.close()
            await self._close_browser()

    async def fetch_single_item(self, note_id: str, original_url: str = None) -> Optional[DownloadItem]:
        """
        获取单个小红书笔记详情（用于单视频链接下载）
        Args:
            note_id: 小红书笔记 ID
            original_url: 原始 URL（用于提取 xsec_token，访问详情页需要）
        Returns:
            DownloadItem 或 None（失败时）
        """
        if not self._cookie:
            raise RuntimeError(
                "小红书需要登录 Cookie 才能获取笔记详情。\n"
                "请使用 --cookie 参数或 cookies.txt 文件提供 Cookie。"
            )

        # 从原始 URL 提取 xsec_token
        xsec_token = ''
        if original_url:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(original_url)
            params = parse_qs(parsed.query)
            if params.get('xsec_token'):
                xsec_token = params['xsec_token'][0]

        # 构造 note_info，复用 _fetch_note_detail_via_page
        note_info = {
            'note_id': note_id,
            'noteId': note_id,
            'id': note_id,
            'xsec_token': xsec_token,
            'display_title': '',
        }

        context = await self._new_context()
        page = await context.new_page()

        try:
            # 先访问首页建立会话（避免直接访问详情页被识别为爬虫）
            try:
                await page.goto('https://www.xiaohongshu.com', wait_until='domcontentloaded', timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            # 访问详情页并从 Pinia store 读取数据
            # nickname 传空字符串，_fetch_note_detail_via_page 会从详情中提取作者昵称
            item = await self._fetch_note_detail_via_page(page, note_info, '')

            if not item:
                self._log(f"  无法获取笔记 {note_id} 详情（可能 Cookie 失效或笔记已删除）")

            return item
        finally:
            await context.close()
            await self._close_browser()

    async def _scroll_and_intercept_notes(self, page, max_count: int, captured_data: dict) -> list:
        """缓慢滚动页面，拦截浏览器自身的 user_posted API 响应

        注意：拦截器（on_response）由调用方在 page.goto() 之前注册，
        并通过 captured_data 共享状态。本方法只负责滚动和收集。
        """
        notes = []

        try:
            # 首次加载会自动触发一次 user_posted 请求
            # 拦截器已在调用方 goto 前注册，此处直接收集首屏数据
            self._log(f"  等待首屏笔记加载...")
            await page.wait_for_timeout(3000)

            # 如果首屏没有数据，尝试滚动一次
            if not captured_data['notes']:
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(3000)

            # 收集首屏数据
            notes = list(captured_data['notes'])
            self._log(f"  首屏: {len(notes)} 个笔记")

            # 继续滚动加载更多（缓慢，模拟人类）
            scroll_count = 0
            while len(notes) < max_count and scroll_count < MAX_SCROLL_COUNT and not captured_data['stop']:
                scroll_count += 1
                delay = self._random_delay(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY)
                self._log(f"  滚动加载第 {scroll_count} 次，等待 {delay:.1f}s...")
                await asyncio.sleep(delay)

                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await page.wait_for_timeout(3000)

                # 更新笔记列表
                notes = list(captured_data['notes'])
                self._log(f"  累计: {len(notes)} 个笔记")

                # 检测风控
                if captured_data['stop']:
                    self._log(f"  ⚠ 检测到风控（461），停止滚动")
                    break

            return notes[:max_count]

        finally:
            # 监听器由调用方负责移除
            pass

    async def _fetch_note_detail_via_page(self, page, note_info: dict, nickname: str) -> Optional[DownloadItem]:
        """
        访问笔记详情页，从 Pinia store 读取详情数据
        详情通过 SSR 渲染，不调用 feed API（已验证）
        """
        note_id = note_info.get('note_id') or note_info.get('noteId') or note_info.get('id', '')
        xsec_token = note_info.get('xsec_token', '')

        note_url = f'https://www.xiaohongshu.com/explore/{note_id}'
        if xsec_token:
            note_url += f'?xsec_token={xsec_token}&xsec_source=pc_note'

        # 访问详情页（浏览器会通过 SSR 加载数据到 Pinia store）
        try:
            await page.goto(note_url, wait_until='domcontentloaded', timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # 从 Pinia store 读取详情数据（camelCase 格式）
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
        title = detail.get('title', '') or note_info.get('display_title', '') or f'note_{note_id}'
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
