"""
ShortVideoDownload - X (Twitter) 下载引擎
CDP 真实浏览器 + Patchright 协议层反检测 + 拦截 UserMedia GraphQL 响应

反检测策略（与小红书引擎同一套已验证架构）:
1. connect_over_cdp 连接真实 Chrome（非 launch() Chromium），指纹全真值
2. Patchright 修补 CDP 协议层泄漏（Runtime.enable / Console.enable）
3. 不构造任何 API 请求 —— 让浏览器自己发 UserMedia GraphQL 请求
   （带正确的 public bearer token / ct0 / x-client-transaction-id），
   只拦截响应。因此无硬编码 queryId / features，X 改版不影响。
4. Cookie 来源：cookies.txt（x.com / twitter.com，含 auth_token+ct0）优先，
   其次 .chrome-profile 里已有的登录态
5. 滚动间隔随机 → 模拟人类浏览；检测到 429/errors 立即停止保护账号
6. 下载走 twimg CDN 直连（不带 Cookie，Referer https://x.com/）
"""
import os
import re
import json
import random
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urlparse, quote

from engines.base import BaseEngine, DownloadItem, DownloadResult
from engines.xiaohongshu import (
    CDP_PORT,
    CHROME_USER_DATA_DIR,
    _find_chrome_executable,
    _is_port_in_use,
    _kill_stale_chrome,
)
from config import DownloadConfig

# 安全配置（与小红书引擎同级别）
MIN_SCROLL_DELAY = 3.0
MAX_SCROLL_DELAY = 6.0
MAX_SCROLL_COUNT = 100
# 深历史搜索回补的查询上限（每查询 180 天窗口 × videos/images 两类）
MAX_SEARCH_QUERIES = 60

# X 保留路径（非用户名）
RESERVED_SCREEN_NAMES = {
    'home', 'i', 'explore', 'search', 'settings', 'messages',
    'notifications', 'compose', 'intent', 'hashtag', 'login', 'signup',
}


def _parse_created_at(created_at: str) -> str:
    """X 时间格式 'Wed Oct 10 20:19:24 +0000 2018' → '2018-10-10 20:04'（UTC）"""
    if not created_at:
        return ""
    try:
        dt = datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
        return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return created_at


def _clean_full_text(legacy: dict) -> str:
    """清理推文全文：去掉 t.co 短链（媒体/外链实体），压缩空白"""
    text = legacy.get('full_text', '') or ''
    entities = legacy.get('entities') or {}
    urls = [m.get('url') for m in (entities.get('media') or []) if m.get('url')]
    urls += [u.get('url') for u in (entities.get('urls') or []) if u.get('url')]
    for u in urls:
        text = text.replace(u, '')
    return re.sub(r'\s+', ' ', text).strip()


def _unwrap_tweet_result(result: dict) -> Optional[dict]:
    """解包 tweet_results.result，返回真正的 tweet dict（含 rest_id/legacy）"""
    if not isinstance(result, dict):
        return None
    typename = result.get('__typename', '')
    if typename == 'TweetWithVisibilityResults':
        tweet = result.get('tweet')
        return tweet if isinstance(tweet, dict) else None
    if typename in ('Tweet', 'TweetWithVisibility'):
        return result
    # tombstone / TweetUnavailable 等返回 None
    return None


def _find_tweet_dict(obj, tweet_id: str) -> Optional[dict]:
    """递归查找 rest_id 匹配且含 legacy 的 tweet dict（用于 TweetDetail 响应）"""
    if isinstance(obj, dict):
        if obj.get('rest_id') == tweet_id and 'legacy' in obj:
            return obj
        for v in obj.values():
            r = _find_tweet_dict(v, tweet_id)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_tweet_dict(v, tweet_id)
            if r is not None:
                return r
    return None


def _find_all_tweets(obj, out: dict):
    """递归收集响应中所有 tweet dict（含 rest_id + legacy）
    不依赖 GraphQL operation 名称 / 响应结构（timeline_v2 / TweetDetail 通用），
    X 改 queryId 或改结构均不影响。
    """
    if isinstance(obj, dict):
        rest_id = obj.get('rest_id')
        if isinstance(rest_id, str) and rest_id.isdigit() and 'legacy' in obj \
                and 'extended_entities' in (obj.get('legacy') or {}):
            if rest_id not in out:
                out[rest_id] = obj
            # 不再深入该 tweet 内部（quoted tweet 是别人的，按作者过滤前先不收）
            return
        for v in obj.values():
            _find_all_tweets(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_all_tweets(v, out)


def _tweet_user_info(tweet: dict) -> dict:
    """提取 tweet 作者信息，兼容新旧两种用户对象结构
    旧: core.user_results.result.legacy.{screen_name,name}
    新 (2026): core.user_results.result.core.{screen_name,name}
    返回 {'screen_name': str, 'name': str}
    """
    user_result = ((tweet.get('core') or {}).get('user_results') or {}).get('result') or {}
    legacy = user_result.get('legacy') or {}
    new_core = user_result.get('core') or {}
    return {
        'screen_name': (legacy.get('screen_name') or new_core.get('screen_name') or ''),
        'name': (legacy.get('name') or new_core.get('name') or ''),
    }


def _tweet_author_screen_name(tweet: dict) -> str:
    """提取 tweet 作者 screen_name（小写，失败返回空串）"""
    return _tweet_user_info(tweet).get('screen_name', '').lower()


class XEngine(BaseEngine):
    """X (Twitter) 下载引擎 - CDP 真实浏览器 + 拦截 GraphQL 响应"""

    platform = "x"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or ""
        self._playwright = None
        self._browser = None
        self._chrome_process = None
        self._user_agent = None
        self._sec_ch_ua = None
        self._sec_ch_ua_platform = None

    # ------------------------------------------------------------------ 浏览器

    def _load_cookie_dicts(self) -> list:
        """构建注入浏览器的 Cookie 列表
        优先级：--cookie 字符串 > cookies.txt（x.com + twitter.com）
        """
        # 1. 命令行 / 参数传入的 cookie 字符串
        if self._cookie:
            cookies = []
            for item in self._cookie.split(';'):
                item = item.strip()
                if '=' in item:
                    k, v = item.split('=', 1)
                    k, v = k.strip(), v.strip()
                    if k:
                        cookies.append({
                            'name': k,
                            'value': v,
                            'domain': '.x.com',
                            'path': '/',
                            'secure': True,
                            'httpOnly': k == 'auth_token',
                            'expires': -1,
                        })
            return cookies

        # 2. cookies.txt（保留 httpOnly/secure/expires 完整信息）
        from utils import load_netscape_cookie_dicts
        cookies = load_netscape_cookie_dicts('x.com')
        for c in cookies:
            # 本项目导出的 cookies.txt 未标记 #HttpOnly_，auth_token 实际是 httpOnly
            if c.get('name') == 'auth_token':
                c['httpOnly'] = True
            # 修正 host-only 域名（x.com → .x.com，让子域共享）
            if not c.get('domain', '').startswith('.'):
                c['domain'] = '.' + c['domain']
        return cookies

    async def _ensure_browser(self):
        """启动真实 Chrome 并通过 Patchright CDP 连接（同小红书引擎）"""
        if self._browser is not None:
            return

        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "未安装 patchright。请运行: pip install patchright\n"
                "（patchright 是 playwright 的反检测 fork，修补了 Runtime.enable / Console.enable 协议层泄漏）"
            )

        for attempt in range(2):
            if not _is_port_in_use(CDP_PORT):
                self._launch_chrome()
                for _ in range(30):
                    if _is_port_in_use(CDP_PORT):
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError(
                        f"Chrome 启动超时，CDP 端口 {CDP_PORT} 未就绪。\n"
                        "可能原因：已有 Chrome 实例占用，请先关闭所有 Chrome 窗口后重试。"
                    )

            self._playwright = await async_playwright().start()
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f'http://127.0.0.1:{CDP_PORT}', timeout=30_000
                )
                return
            except Exception:
                if self._playwright:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                if attempt == 0:
                    killed = _kill_stale_chrome()
                    self._log(f"CDP 连接失败（疑似残留僵尸 Chrome），已清理 {killed} 个进程，重启 Chrome 重试...")
                    await asyncio.sleep(2)
                    continue
                raise RuntimeError(
                    "Chrome CDP 连接失败（已尝试自动清理僵尸进程并重启）。\n"
                    "请手动关闭所有 Chrome 窗口后重试，或重新运行本命令。"
                )

    def _launch_chrome(self):
        """启动真实 Chrome（独立 Profile + CDP 调试端口）"""
        chrome_path = _find_chrome_executable()
        CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        args = [
            chrome_path,
            f'--remote-debugging-port={CDP_PORT}',
            f'--user-data-dir={CHROME_USER_DATA_DIR}',
            '--no-first-run',
            '--no-default-browser-check',
            '--mute-audio',
            # 媒体页长时间滚动防内存膨胀：禁视频自动播放预览
            # （chrome://flags 的正常用户选项，非自动化特征，区别于 --disable-blink-features）
            '--autoplay-policy=user-gesture-required',
        ]
        import subprocess
        self._chrome_process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    async def _close_browser(self):
        """断开 CDP 连接（不杀 Chrome，Profile 持久化累积登录态/痕迹）"""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _new_context(self):
        """获取 Chrome 默认 context 并注入 X Cookie，缓存真实 UA/Client-Hints"""
        await self._ensure_browser()

        if self._browser.contexts:
            context = self._browser.contexts[0]
        else:
            context = await self._browser.new_context()

        # 注入 Cookie（覆盖同域名同名 Cookie；auth_token+ct0 即登录态）
        cookies = self._load_cookie_dicts()
        if cookies:
            try:
                await context.add_cookies(cookies)
            except Exception as e:
                self._log(f"  Cookie 注入警告: {e}")

        # 缓存真实 UA / Client Hints（CDN 下载请求必须与浏览器指纹一致）
        if self._user_agent is None:
            try:
                pages = context.pages
                page_for_ua = pages[0] if pages else await context.new_page()
                try:
                    ua_data = await page_for_ua.evaluate('''() => ({
                        ua: navigator.userAgent,
                        brands: navigator.userAgentData ? navigator.userAgentData.brands : null,
                        platform: navigator.userAgentData ? navigator.userAgentData.platform : (navigator.platform || 'Windows'),
                    })''')
                    self._user_agent = ua_data.get('ua') or (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
                    )
                    brands = ua_data.get('brands') or []
                    if brands:
                        self._sec_ch_ua = ', '.join(
                            f'"{b["brand"]}";v="{b["version"]}"' for b in brands
                        )
                    else:
                        chrome_ver = re.search(r'Chrome/(\d+)', self._user_agent)
                        cv = chrome_ver.group(1) if chrome_ver else '130'
                        self._sec_ch_ua = f'"Chromium";v="{cv}", "Not?A_Brand";v="99", "Google Chrome";v="{cv}"'
                    self._sec_ch_ua_platform = f'"{ua_data.get("platform") or "Windows"}"'
                finally:
                    if not pages:
                        await page_for_ua.close()
            except Exception:
                self._user_agent = (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
                )
                self._sec_ch_ua = '"Chromium";v="130", "Not?A_Brand";v="99", "Google Chrome";v="130"'
                self._sec_ch_ua_platform = '"Windows"'

        return context

    # ------------------------------------------------------------------ 解析

    # 日期范围参数（X 独有）：URL 后接 /YYYYMMDD-YYYYMMDD
    # 分隔符 - _ ~ 均可，两端日期顺序不限（自动排序），范围含两端。
    # 例：https://x.com/{user}/20241201-20210506 → 2021-05-06 ~ 2024-12-01
    _DATE_RANGE_RE = re.compile(r'/(\d{8})([-_~])(\d{8})/?$')

    @classmethod
    def _parse_date_range(cls, url: str):
        """解析 URL 尾部的日期范围参数，返回 (start, end)（已排序，date 对象，含端点）；
        无参数返回 None；日期非真实日历日期抛 ValueError"""
        path = url.split('?')[0]
        m = cls._DATE_RANGE_RE.search(path)
        if not m:
            return None

        def _to_date(s: str):
            try:
                return datetime.strptime(s, '%Y%m%d').date()
            except ValueError:
                raise ValueError(
                    f"日期范围参数无效: {m.group(1)}{m.group(2)}{m.group(3)}"
                    f"（{s} 不是真实日期，格式 YYYYMMDD，如 20241201-20210506）")

        d1, d2 = _to_date(m.group(1)), _to_date(m.group(3))
        return (d1, d2) if d1 <= d2 else (d2, d1)

    def _extract_screen_name(self, url: str) -> str:
        """从 URL 提取 X 用户名（screen_name）"""
        path = url.split('?')[0].rstrip('/')
        # 单推 URL 不支持走用户主页流程
        if re.search(r'(?:x|twitter)\.com/[^/]+/status/\d+', path):
            raise ValueError(f"这是单条推文链接，请提供用户主页 URL: {url}")
        # 剥离日期范围参数段（/20241201-20210506）再匹配用户名
        path = re.sub(r'/\d{8}[-_~]\d{8}$', '', path)
        m = re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})$', path)
        if m and m.group(1).lower() not in RESERVED_SCREEN_NAMES:
            return m.group(1)
        m = re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})(?:/media)?$', path)
        if m and m.group(1).lower() not in RESERVED_SCREEN_NAMES:
            return m.group(1)
        raise ValueError(f"无法从 URL 提取 X 用户名: {url}")

    def _tweet_to_items(self, tweet: dict) -> List[DownloadItem]:
        """把一条 tweet dict 转成 DownloadItem 列表（视频/图集，可多条）"""
        legacy = tweet.get('legacy') or {}
        user_info = _tweet_user_info(tweet)
        screen_name = user_info.get('screen_name', '')
        display_name = user_info.get('name', '') or screen_name
        rest_id = str(tweet.get('rest_id', ''))

        media_list = (legacy.get('extended_entities') or {}).get('media') or []
        photos = []
        videos = []  # (media_url_https_cover, mp4_url, duration)
        for media in media_list:
            mtype = media.get('type', '')
            if mtype == 'photo':
                url = media.get('media_url_https', '')
                if not url:
                    continue
                # orig 原图：去掉扩展名，用 format/name 参数
                m = re.match(r'(.+)\.(jpg|jpeg|png|webp)$', url, re.IGNORECASE)
                if m:
                    photos.append(f"{m.group(1)}?format={m.group(2).lower()}&name=orig")
                else:
                    photos.append(url)
            elif mtype in ('video', 'animated_gif'):
                video_info = media.get('video_info') or {}
                variants = video_info.get('variants') or []
                # 选最高码率的 mp4（m3u8 无法直连下载，跳过）
                mp4s = [v for v in variants
                        if v.get('content_type') == 'video/mp4' and v.get('url')]
                if not mp4s:
                    continue
                best = max(mp4s, key=lambda v: v.get('bitrate', 0) or 0)
                duration = (video_info.get('duration_millis') or 0) / 1000.0
                videos.append((media.get('media_url_https', ''), best['url'], duration))

        items = []
        text = _clean_full_text(legacy)
        create_time = _parse_created_at(legacy.get('created_at', ''))

        # 视频项：单视频用 rest_id；多视频每条一个（rest_id_N），标题加 [i/n] 序号
        for i, (_cover, url, duration) in enumerate(videos):
            multi = len(videos) > 1 or bool(photos)
            item_id = rest_id if not multi else f"{rest_id}_{i + 1}"
            title = text or display_name
            if len(videos) > 1:
                title = f"{title} [{i + 1}/{len(videos)}]"
            items.append(DownloadItem(
                item_id=item_id,
                item_type="video",
                title=title,
                urls=[url],
                url=f"https://x.com/{screen_name}/status/{rest_id}",
                create_time=create_time,
                nickname=display_name,
                uid=screen_name,
                # X 无封面概念：缩略图与媒体重复，不设 cover_url（不下载 _cover 文件）
                description=text,
                duration=duration or None,
            ))

        # 图集项：同一推的所有原图合并为一个 item
        if photos:
            items.append(DownloadItem(
                item_id=rest_id if not videos else f"{rest_id}_img",
                item_type="image",
                title=text or display_name,
                urls=photos,
                url=f"https://x.com/{screen_name}/status/{rest_id}",
                create_time=create_time,
                nickname=display_name,
                uid=screen_name,
                description=text,
            ))
        return items

    # ------------------------------------------------------------------ 断点续传
    # 深度滚动（多年历史、数百条媒体）耗时长，中途崩溃/ Ctrl-C / 429 风控会丢失全部
    # 已捕获推文。断点持久化到 {save_dir}/x/.checkpoint_{screen_name}.json，
    # 重跑同一命令即自动续传（跳过已完成视图，未完成视图从顶部重放，靠去重+回放检测续上）。

    def _checkpoint_path(self, screen_name: str) -> str:
        d = os.path.join(self.config.save_dir, 'x')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f'.checkpoint_{screen_name.lower()}.json')

    def _load_checkpoint(self, screen_name: str) -> dict:
        """读取断点（7 天过期；不存在/损坏返回空 dict）"""
        import time
        path = self._checkpoint_path(screen_name)
        try:
            if not os.path.isfile(path):
                return {}
            if time.time() - os.path.getmtime(path) > 7 * 24 * 3600:
                os.remove(path)
                return {}
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) \
                    or data.get('screen_name', '').lower() != screen_name.lower():
                return {}
            return data
        except Exception:
            return {}

    def _save_checkpoint(self, screen_name: str, tweets: dict, views_done: set):
        """原子保存断点（tmp + replace）；调用方负责节流"""
        try:
            path = self._checkpoint_path(screen_name)
            tmp = path + '.tmp'
            payload = {
                'screen_name': screen_name,
                'tweets': tweets,
                'views_done': sorted(views_done),
                'updated_at': datetime.now().isoformat(timespec='seconds'),
            }
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            self._log(f"  ⚠ 断点保存失败（不影响本次抓取）: {e}")

    def _delete_checkpoint(self, screen_name: str):
        try:
            path = self._checkpoint_path(screen_name)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def _media_total(tweets: dict) -> int:
        """推文集合的媒体文件总数（照片+视频各算 1）"""
        return sum(
            len(((t.get('legacy') or {}).get('extended_entities') or {}).get('media') or [])
            for t in tweets.values()
        )

    async def _search_backfill(self, context, on_response, captured: dict,
                               screen_name: str, sources_done: set,
                               range_start=None, range_end=None, cp_key: str = None):
        """深历史搜索回补：media tab 游标深度用尽（~1500 条）后，
        用搜索日期窗口（f=live，倒序全量）继续挖更早的媒体。

        - 自动模式（默认）：窗口从已捕获最早推文往前 180 天一段，直到账号创建时间
        - 显式范围模式（range_start/range_end 给定，来自 URL 日期参数）：
          窗口覆盖 [range_start, range_end]（含两端），不依赖已有捕获，
          且禁用空窗口熔断（用户明确指定了范围，空段是正常情况）
        - 窗口切分按含端点语义：查询用 until:末尾+1天（X until 为排他语义，
          不 +1 会漏掉边界日当天的推文）
        - 每窗口两查：filter:videos / filter:images（搜索单查询也有深度限制，
          180 天窗口通常远低于该限制）
        - 拦截器复用：SearchTimeline 响应与时间线响应同构，递归解析不挑 operation
        - 熔断（仅自动模式）：连续 3 个窗口零新增即停（media_count 可能含转推媒体，
          永远到不了 100%，防止空转）
        """
        from utils import parse_create_time

        cp_key = cp_key or screen_name

        if range_start is not None and range_end is not None:
            end = range_end
            lower_d = range_start
        else:
            oldest = None
            for t in captured['tweets'].values():
                dt = parse_create_time(_parse_created_at(
                    (t.get('legacy') or {}).get('created_at', '')))
                if dt and (oldest is None or dt < oldest):
                    oldest = dt
            if oldest is None:
                return
            lower = parse_create_time(_parse_created_at(captured.get('user_created_at') or ''))
            if lower is None:
                lower = oldest - timedelta(days=3650)
            end = oldest.date()
            lower_d = lower.date()

        queries = 0
        empty_windows = 0
        while end >= lower_d and queries < MAX_SEARCH_QUERIES and not captured['stop']:
            start = max(lower_d, end - timedelta(days=179))
            until_param = end + timedelta(days=1)  # X until: 排他语义，+1 天才含端点
            before_total = len(captured['tweets'])

            for kind in ('videos', 'images'):
                if captured['stop']:
                    break
                q = (f"from:{screen_name} filter:{kind} "
                     f"since:{start.isoformat()} until:{until_param.isoformat()}")
                url = 'https://x.com/search?q=' + quote(q) + '&f=live'
                if url in sources_done:
                    continue
                queries += 1
                if queries > MAX_SEARCH_QUERIES:
                    break

                page = await context.new_page()
                page.on('response', on_response)
                self._log(f"  [搜索] {q}")
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    if '/login' in page.url:
                        raise RuntimeError('X 登录态失效（搜索被重定向到登录页），'
                                           '请重新登录后用断点续传重跑')
                    await page.wait_for_timeout(3000)

                    rounds = 0
                    empty = 0
                    while rounds < MAX_SCROLL_COUNT and not captured['stop']:
                        rounds += 1
                        # 深度退避同主流程
                        delay = random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY) \
                            + min(rounds * 0.1, 4.0)
                        await asyncio.sleep(delay)

                        before = len(captured['tweets'])
                        captured['saw_tweets'] = False
                        remaining = await page.evaluate(
                            'document.body.scrollHeight - window.innerHeight - window.scrollY')
                        while remaining > 0:
                            step = random.randint(300, 700)
                            await page.mouse.wheel(0, step)
                            await asyncio.sleep(0.05 + random.random() * 0.1)
                            remaining -= step
                        await page.wait_for_timeout(2500)

                        if captured['stop']:
                            break
                        if len(captured['tweets']) > before or captured['saw_tweets']:
                            empty = 0
                            if rounds % 5 == 0:
                                self._save_checkpoint(cp_key, captured['tweets'], sources_done)
                            continue
                        # 补滚 + 长等待再判空（同主流程）
                        await page.mouse.wheel(0, random.randint(500, 900))
                        await page.wait_for_timeout(4000)
                        if len(captured['tweets']) > before or captured['saw_tweets']:
                            empty = 0
                            continue
                        empty += 1
                        if empty >= 3:
                            break
                finally:
                    try:
                        page.remove_listener('response', on_response)
                    except Exception:
                        pass
                    try:
                        await page.close()
                    except Exception:
                        pass

                if captured['stop']:
                    break
                sources_done.add(url)
                self._save_checkpoint(cp_key, captured['tweets'], sources_done)

            window_new = len(captured['tweets']) - before_total
            self._log(f"  [搜索] 窗口 {start} ~ {end} 完成，新增 {window_new} 条"
                      f"（累计 {len(captured['tweets'])}）")
            # 空窗口熔断仅自动模式启用（显式范围时空段是正常情况，必须继续）
            if range_start is None and window_new == 0:
                empty_windows += 1
                if empty_windows >= 3:
                    self._log("  [搜索] 连续 3 个窗口无新增，更早历史已挖完")
                    break
            else:
                empty_windows = 0
            end = start - timedelta(days=1)  # 含端点切分：下一窗口从本窗口起点前一日开始

    # ------------------------------------------------------------------ 抓取

    async def fetch_user_items(self, user_url: str) -> List[DownloadItem]:
        """获取 X 用户媒体列表：双视图访问 /media（视频）+ /media?filter=photo（照片），
        拦截 GraphQL 响应合并去重，只收博主自己的原创媒体（无转推/回复）。
        URL 尾部可带日期范围参数 /YYYYMMDD-YYYYMMDD（分隔符 - _ ~ 均可，顺序不限，
        含两端）：带范围时跳过 media 视图，直接用搜索日期窗口抓取该范围"""
        screen_name = self._extract_screen_name(user_url)
        date_range = self._parse_date_range(user_url)

        effective_max = self.config.max_count
        if effective_max == 0:
            effective_max = 10**9
        else:
            self._log(f"  限制下载 {effective_max} 个")

        # 断点键：日期范围模式用独立键，避免与全量任务的断点互相覆盖/误删
        cp_key = screen_name
        if date_range:
            cp_key = (f"{screen_name}_rng{date_range[0]:%Y%m%d}"
                      f"_{date_range[1]:%Y%m%d}")
            self._log(f"  日期范围模式: {date_range[0]} ~ {date_range[1]}（含两端，只下此范围）")

        context = await self._new_context()

        # 拦截器必须在 goto 之前注册（首屏请求在 goto 期间发出）
        # 不依赖 operation 名称：解析所有 GraphQL 响应，递归收集含媒体的目标用户推文
        # user_state 由 UserByScreenName 响应判定（User=存在 / UserUnavailable|空=不存在或冻结）
        # saw_tweets：本轮滚动分页是否有目标作者推文响应——区分「续传回放/游标重叠」与「真到底」，
        # 否则续传后重放已捕获区间 delta=0，会被「连续 3 轮无新增」误判到底而漏抓
        captured = {
            'tweets': {}, 'stop': False, 'error': '', 'ops': set(),
            'user_state': '', 'saw_tweets': False,
        }

        # 断点续传：上次中断（崩溃/Ctrl-C/风控 429）的进度，重跑同命令自动接续
        checkpoint = self._load_checkpoint(cp_key)
        views_done = set(checkpoint.get('views_done', []))
        if checkpoint.get('tweets'):
            captured['tweets'].update(checkpoint['tweets'])
            done_desc = f"{len(views_done)} 个" if views_done else "无"
            self._log(f"  检测到断点，续传：已有 {len(captured['tweets'])} 条推文（已完成视图: {done_desc}）")

        async def on_response(response):
            if captured['stop'] or '/i/api/graphql/' not in response.url:
                return
            if response.status in (401, 403, 429):
                captured['stop'] = True
                captured['error'] = f"HTTP {response.status}"
                return
            if response.status != 200:
                return
            try:
                data = await response.json()
            except Exception:
                return
            if data.get('errors'):
                captured['stop'] = True
                captured['error'] = json.dumps(data['errors'], ensure_ascii=False)[:200]
                return
            # 用户存在性：UserByScreenName 响应（权威判定，不依赖 DOM 空态组件）
            user_result = ((data.get('data') or {}).get('user') or {}).get('result')
            if user_result is not None and not captured['user_state']:
                if user_result.get('__typename') == 'UserUnavailable':
                    captured['user_state'] = 'unavailable'
                elif user_result.get('__typename') == 'User':
                    captured['user_state'] = 'ok'
                    # 深历史判定素材：资料页媒体总数 + 账号创建时间
                    # （media tab 游标深度 ~1500 条，捕获量 < media_count 时需搜索回补）
                    core = user_result.get('core') or {}
                    leg = user_result.get('legacy') or {}
                    mc = core.get('media_count') or leg.get('media_count')
                    if mc and not captured.get('media_count'):
                        captured['media_count'] = int(mc)
                    ca = core.get('created_at') or leg.get('created_at') or ''
                    if ca and not captured.get('user_created_at'):
                        captured['user_created_at'] = ca
            found = {}
            _find_all_tweets(data, found)
            added = 0
            saw = False
            for rid, tweet in found.items():
                # 只收目标用户的推文（排除时间线里推荐/引用的他人推文）
                if _tweet_author_screen_name(tweet) != screen_name.lower():
                    continue
                # 防御性排除转推：RT 的原推作者可能被误判（结构变化），
                # 双保险看结构标记 + "RT @" 文本前缀
                legacy = tweet.get('legacy') or {}
                if legacy.get('retweeted_status_result') is not None:
                    continue
                if (legacy.get('full_text') or '').startswith('RT @'):
                    continue
                # 分页有目标作者推文（含重复）→ 时间线仍在推进，不是到底
                saw = True
                if rid in captured['tweets']:
                    continue
                captured['tweets'][rid] = tweet
                added += 1
            if saw:
                captured['saw_tweets'] = True
            if added:
                m = re.search(r'/graphql/[^/]+/(\w+)', response.url)
                if m:
                    captured['ops'].add(m.group(1))
                self._log(f"  拦截到 {added} 条推文（累计 {len(captured['tweets'])}）")

        async def _probe_user(pg):
            """登录检测 + 用户存在性判定（访问任一 x.com 页面后调用；
            登录看侧边栏账号按钮，存在性等 UserByScreenName 响应，最长 10s）"""
            logged_in = False
            for _ in range(6):
                count = await pg.locator(
                    '[data-testid="SideNav_AccountSwitcher_Button"]'
                ).count()
                if count > 0:
                    logged_in = True
                    break
                login_wall = await pg.locator(
                    'input[autocomplete="username"]'
                ).count()
                if login_wall > 0 or '/login' in pg.url:
                    break
                await pg.wait_for_timeout(1500)

            if not logged_in:
                raise RuntimeError(
                    "X 未登录，无法获取用户媒体时间线。\n\n"
                    "解决方式（三选一）:\n"
                    "  1. 在 cookies.txt 中加入 x.com 的 Cookie（需含 auth_token 和 ct0，"
                    "用浏览器扩展导出 Netscape 格式）\n"
                    "  2. 在弹出的 Chrome 窗口中手动登录 X 一次（.chrome-profile 会持久化登录态），然后重跑\n"
                    "  3. 使用 --cookie \"auth_token=xxx; ct0=xxx\" 参数"
                )

            # 用户存在性：等待 UserByScreenName 响应（权威判定）
            for _ in range(10):
                if captured['user_state'] or captured['stop']:
                    break
                await pg.wait_for_timeout(1000)

            if captured['user_state'] == 'unavailable':
                raise RuntimeError(f"用户 @{screen_name} 不存在或已被冻结")

        page = None
        try:
            if date_range:
                # 日期范围模式：跳过 media 视图（其不支持日期过滤），
                # 探测主页（登录+存在性）后直接用搜索日期窗口抓取指定范围
                page = await context.new_page()
                page.on('response', on_response)
                self._log("  访问主页确认登录与用户 ...")
                try:
                    await page.goto(f"https://x.com/{screen_name}",
                                    wait_until='domcontentloaded', timeout=30000)
                except Exception as e:
                    self._log(f"  页面加载警告: {e}")
                await page.wait_for_timeout(3000)
                await _probe_user(page)

                await self._search_backfill(
                    context, on_response, captured, screen_name, views_done,
                    range_start=date_range[0], range_end=date_range[1],
                    cp_key=cp_key)
                media_views = []  # 跳过下方 media 视图循环
            else:
                # 双视图抓取（media tab 天然只含博主自己的媒体，无转推/回复）：
                #   /media               → 视频视图（新版默认 tab 只有视频）
                #   /media?filter=photo  → 照片视图（纯图推文）
                # 两视图合并去重（按 rest_id）= 博主全部原创媒体。
                # 注意不能用主页「帖子」tab：其 UserOriginalsTimeline 的 "Originals"
                # 只排除回复不排除转推，且分页混大量纯文字推文（媒体密度低，滚动效率差）。
                media_views = [
                    f"https://x.com/{screen_name}/media",
                    f"https://x.com/{screen_name}/media?filter=photo",
                ]

            for view_idx, view_url in enumerate(media_views, 1):
                # 断点续传：上次已滚到底的视图直接跳过（其推文已全部在断点里）
                if view_url in views_done:
                    self._log(f"  [视图{view_idx}] 上次已完成，跳过")
                    continue
                if len(captured['tweets']) >= effective_max:
                    break

                # 每视图新 page：关闭旧页释放 JS 堆（深度滚动内存累积，小红书曾 140+ 笔记 OOM）
                if page is not None:
                    try:
                        page.remove_listener('response', on_response)
                    except Exception:
                        pass
                    try:
                        await page.close()
                    except Exception:
                        pass
                page = await context.new_page()
                page.on('response', on_response)

                self._log(f"  访问 {view_url} ...")
                try:
                    await page.goto(view_url, wait_until='domcontentloaded', timeout=30000)
                except Exception as e:
                    self._log(f"  页面加载警告: {e}")
                await page.wait_for_timeout(3000)

                # 登录检测 + 用户存在性（仅第一轮）
                if view_idx == 1:
                    await _probe_user(page)

                # 滚动加载当前视图（真实鼠标滚轮，随机节奏）
                scroll_count = 0
                consecutive_empty = 0
                saved_count = len(captured['tweets'])  # 上次写断点时的数量（节流）
                view_bottom = False
                while (len(captured['tweets']) < effective_max
                       and scroll_count < MAX_SCROLL_COUNT and not captured['stop']):
                    scroll_count += 1
                    # 深度退避：越往下翻分页越密集，逐渐放慢降低 429 风控风险（最多 +4s）
                    delay = random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY) \
                        + min(scroll_count * 0.1, 4.0)
                    self._log(f"  [视图{view_idx}] 滚动第 {scroll_count} 次，等待 {delay:.1f}s ...")
                    await asyncio.sleep(delay)

                    before_count = len(captured['tweets'])
                    captured['saw_tweets'] = False
                    scroll_y = await page.evaluate('window.scrollY')
                    total_height = await page.evaluate('document.body.scrollHeight')
                    viewport_h = await page.evaluate('window.innerHeight')
                    remaining = total_height - viewport_h - scroll_y
                    while remaining > 0:
                        step = random.randint(300, 700)
                        await page.mouse.wheel(0, step)
                        await asyncio.sleep(0.05 + random.random() * 0.1)
                        remaining -= step
                    await page.wait_for_timeout(2500)

                    if captured['stop']:
                        break
                    if len(captured['tweets']) >= effective_max:
                        break

                    if len(captured['tweets']) > before_count:
                        consecutive_empty = 0
                        # 节流写断点（每 5 轮或新增 ≥50）：丢了的部分可在续传回放中重新捕获
                        if scroll_count % 5 == 0 \
                                or len(captured['tweets']) - saved_count >= 50:
                            self._save_checkpoint(screen_name, captured['tweets'], views_done)
                            saved_count = len(captured['tweets'])
                        continue

                    if captured['saw_tweets']:
                        # 分页有响应但全是已捕获推文（断点续传回放 / 游标重叠区间）
                        # → 时间线仍在推进，不是到底
                        consecutive_empty = 0
                        continue

                    # 无响应也无新增：可能分页慢——补一次滚动 + 更长等待再判定
                    await page.mouse.wheel(0, random.randint(500, 900))
                    await page.wait_for_timeout(4000)
                    if len(captured['tweets']) > before_count or captured['saw_tweets']:
                        consecutive_empty = 0
                        continue

                    # 连续 3 轮确认到底（X 虚拟列表页高持续增长，页高不可作为到底依据）
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        self._log(f"  [视图{view_idx}] 连续 3 次滚动无新推文，视图到底")
                        view_bottom = True
                        break

                # 真正滚到底（非风控/非轮数上限中断）→ 标记完成并写断点
                if view_bottom and not captured['stop']:
                    views_done.add(view_url)
                    self._save_checkpoint(screen_name, captured['tweets'], views_done)

            # 深历史回补：X 对 media tab 有服务端游标深度限制（~1500 条推文，
            # 老账号会中途断流），资料页 media_count 明显大于捕获量时用搜索日期窗口继续挖
            # （仅全量模式；日期范围模式已直接用搜索覆盖指定范围，无需此判定）
            if (not date_range and effective_max >= 10**9 and not captured['stop']
                    and captured.get('media_count') and captured['tweets']):
                have = self._media_total(captured['tweets'])
                want = captured['media_count']
                self._log(f"  媒体覆盖: {have}/{want}（资料页计数）")
                if have < want * 0.95:
                    self._log("  media tab 游标已到深度上限，切换搜索模式回补更早历史 ...")
                    await self._search_backfill(context, on_response, captured,
                                                screen_name, views_done)

            if captured['error']:
                self._log(f"  ⚠ 检测到风控/错误，停止抓取以保护账号: {captured['error']}")
                self._log("  进度已存断点，稍后重跑同一命令可续传")

            # 干净完成（无风控/错误）→ 清除断点文件；中断场景保留供下次续传
            if not captured['stop']:
                self._delete_checkpoint(cp_key)

            # 日期范围模式：安全网过滤（探测页可能顺带捕获范围外的最新推文）
            if date_range:
                from utils import parse_create_time as _pct
                rs, re_ = date_range

                def _in_range(t: dict) -> bool:
                    dt = _pct(_parse_created_at(
                        (t.get('legacy') or {}).get('created_at', '')))
                    return dt is not None and rs <= dt.date() <= re_
                before_n = len(captured['tweets'])
                captured['tweets'] = {rid: t for rid, t in captured['tweets'].items()
                                      if _in_range(t)}
                self._log(f"  范围过滤: {before_n} → {len(captured['tweets'])} 条"
                          f"（{rs} ~ {re_}，含两端，UTC 日期）")

            self._log(f"  共拦截 {len(captured['tweets'])} 条含媒体推文"
                      + (f"（operation: {', '.join(sorted(captured['ops']))}）" if captured['ops'] else ""))

            # 推文 → DownloadItem（过滤纯文字推：无媒体的不在 media tab，但防御性跳过）
            items = []
            for tweet in captured['tweets'].values():
                items.extend(self._tweet_to_items(tweet))
            # 全局按发布时间倒序（来源混合：media 视图 + 搜索回补，插入序无意义）
            from utils import parse_create_time

            def _sort_key(i):
                dt = parse_create_time(i.create_time)
                return (dt is None, -dt.timestamp() if dt else 0.0)
            items.sort(key=_sort_key)
            # 时间线倒序（最新在前）已是 X 默认顺序
            return items[:effective_max] if effective_max < 10**9 else items

        finally:
            if page is not None:
                try:
                    page.remove_listener('response', on_response)
                except Exception:
                    pass
                try:
                    await page.close()
                except Exception:
                    pass
            await self._close_browser()

    async def fetch_single_item(self, video_id: str, original_url: str = None) -> Optional[DownloadItem]:
        """获取单条推文的媒体（访问推文页，拦截 TweetDetail GraphQL 响应）"""
        context = await self._new_context()
        page = await context.new_page()

        captured = {'data': None}

        async def on_response(response):
            if '/i/api/graphql/' not in response.url or response.status != 200:
                return
            try:
                data = await response.json()
            except Exception:
                return
            # 逐个存响应，推文本体稍后按 rest_id 检索（不依赖 operation 名称）
            if _find_tweet_dict(data, video_id) is not None:
                captured['data'] = data

        page.on('response', on_response)

        try:
            tweet_url = original_url or f"https://x.com/i/web/status/{video_id}"
            # 统一换成 x.com 域名（twitter.com 会重定向，拦截器照样能捕获）
            try:
                await page.goto(tweet_url, wait_until='domcontentloaded', timeout=30000)
            except Exception as e:
                self._log(f"  页面加载警告: {e}")

            # 等待 TweetDetail 响应（最长 15s）
            for _ in range(15):
                if captured['data']:
                    break
                await page.wait_for_timeout(1000)

            if not captured['data']:
                self._log(f"  未拦截到 TweetDetail 响应（可能 Cookie 失效或推文已删除）")
                return None

            tweet = _find_tweet_dict(captured['data'], video_id)
            if tweet is None:
                self._log(f"  响应中未找到推文 {video_id}")
                return None

            items = self._tweet_to_items(tweet)
            if not items:
                self._log(f"  推文 {video_id} 无视频/图片媒体")
                return None
            # 单链接场景：优先返回视频，否则第一张图集
            for it in items:
                if it.is_video:
                    return it
            return items[0]

        finally:
            try:
                page.remove_listener('response', on_response)
            except Exception:
                pass
            try:
                await page.close()
            except Exception:
                pass
            await self._close_browser()

    # ------------------------------------------------------------------ 下载

    def _build_cdn_headers(self, is_video: bool) -> dict:
        """构建 twimg CDN 下载 headers（与浏览器指纹一致，不带 Cookie）"""
        return {
            "Referer": "https://x.com/",
            "User-Agent": self._user_agent or (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
            ),
            "Accept": "*/*" if is_video else "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "sec-ch-ua": self._sec_ch_ua or '"Chromium";v="130", "Not?A_Brand";v="99", "Google Chrome";v="130"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": self._sec_ch_ua_platform or '"Windows"',
            "sec-fetch-dest": "video" if is_video else "image",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "cross-site",
        }

    @staticmethod
    def _image_ext(url: str) -> str:
        m = re.search(r'format=(jpg|jpeg|png|webp)', url, re.IGNORECASE)
        if m:
            ext = m.group(1).lower()
            return '.jpeg' if ext == 'jpeg' else f'.{ext}'
        return '.jpg'

    def _get_proxy(self) -> Optional[str]:
        """下载请求使用的代理：--proxy 显式指定 > Windows 系统代理（Chrome 同源）
        X/twimg 在部分网络环境需要代理直连（Chrome 走系统代理，aiohttp 不会自动走）"""
        if self.config.proxies:
            return self.config.proxies
        from utils import get_system_proxy
        return get_system_proxy() or None

    async def download_item(self, item: DownloadItem, save_dir: str) -> DownloadResult:
        """下载单条推文媒体（aiohttp 直连 twimg CDN，带重试 + 断点续传）"""
        import aiohttp
        import aiofiles

        saved_paths = []
        proxy = self._get_proxy()
        if proxy and not getattr(self, '_proxy_logged', False):
            self._log(f"  使用代理下载: {proxy}")
            self._proxy_logged = True

        try:
            if item.is_video:
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频下载链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return DownloadResult(True, item, saved_paths=[filepath], skipped=True, skip_reason="已存在")

                headers = self._build_cdn_headers(is_video=True)
                max_retries = self.config.max_retries
                last_error = None
                for attempt in range(max_retries):
                    try:
                        existing_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                        download_headers = dict(headers)
                        if existing_size > 0:
                            download_headers['Range'] = f'bytes={existing_size}-'

                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                video_url, headers=download_headers, proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=600, sock_read=60),
                                allow_redirects=True,
                            ) as resp:
                                if resp.status == 200:
                                    if existing_size > 0:
                                        os.remove(filepath)
                                        existing_size = 0
                                    mode = 'wb'
                                    content_length = resp.content_length
                                elif resp.status == 206:
                                    mode = 'ab'
                                    content_length = resp.content_length
                                else:
                                    last_error = f"HTTP {resp.status}"
                                    if attempt < max_retries - 1:
                                        await asyncio.sleep(2 * (attempt + 1))
                                        continue
                                    return DownloadResult(False, item, error=last_error)

                                async with aiofiles.open(filepath, mode) as f:
                                    async for chunk in resp.content.iter_chunked(65536):
                                        await f.write(chunk)

                                downloaded_size = os.path.getsize(filepath)
                                if content_length and downloaded_size < existing_size + content_length:
                                    raise aiohttp.ClientPayloadError(
                                        f"下载不完整: {downloaded_size}/{existing_size + content_length} bytes"
                                    )
                                saved_paths.append(filepath)
                                last_error = None
                                break
                    except (aiohttp.ClientPayloadError, aiohttp.ClientOSError,
                            ConnectionResetError, ConnectionError, asyncio.TimeoutError) as e:
                        last_error = str(e)
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 * (attempt + 1))
                if last_error:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    return DownloadResult(False, item, error=last_error)

            elif item.is_image:
                first_path = self._make_filepath(save_dir, item, self._image_ext(item.urls[0]) if item.urls else '.jpg', idx=1)
                if os.path.exists(first_path):
                    return DownloadResult(True, item, saved_paths=[first_path], skipped=True, skip_reason="已存在")

                headers = self._build_cdn_headers(is_video=False)
                img_errors = []
                async with aiohttp.ClientSession() as session:
                    for idx, img_url in enumerate(item.urls):
                        ext = self._image_ext(img_url)
                        filepath = self._make_filepath(save_dir, item, ext, idx=idx + 1)
                        # 单图已存在（上次部分成功的补下场景）直接跳过
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            saved_paths.append(filepath)
                            continue
                        # 每张图独立重试（pbs.twimg.com 偶发连接抖动）
                        last_err = None
                        for attempt in range(self.config.max_retries):
                            try:
                                async with session.get(
                                    img_url, headers=headers, proxy=proxy,
                                    timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                                    allow_redirects=True,
                                ) as resp:
                                    if resp.status == 200:
                                        async with aiofiles.open(filepath, 'wb') as f:
                                            async for chunk in resp.content.iter_chunked(8192):
                                                await f.write(chunk)
                                        saved_paths.append(filepath)
                                        last_err = None
                                        break
                                    last_err = f"HTTP {resp.status}"
                            except (aiohttp.ClientPayloadError, aiohttp.ClientOSError,
                                    ConnectionResetError, ConnectionError,
                                    asyncio.TimeoutError, OSError) as e:
                                last_err = str(e)
                            if attempt < self.config.max_retries - 1:
                                await asyncio.sleep(2 * (attempt + 1))
                        if last_err:
                            img_errors.append(f"图{idx + 1}: {last_err}")
                if img_errors:
                    return DownloadResult(False, item, error="; ".join(img_errors))

            # X 无封面概念：不下载 _cover 文件（视频缩略图/图集首图与媒体本身重复）

            if not saved_paths:
                return DownloadResult(False, item, error="未下载到任何文件")

            return DownloadResult(True, item, saved_paths=saved_paths)

        except Exception as e:
            return DownloadResult(False, item, error=str(e))
