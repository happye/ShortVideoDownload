"""
ShortVideoDownload - 小红书下载引擎
CDP 真实浏览器 + Patchright 协议层反检测 + aiohttp 直接下载

反检测策略（基于实战验证，参考 yousali.com 反检测文章）:
1. connect_over_cdp 连接用户真实 Chrome（非 launch() 启动 Chromium）
   - 真实 Chrome 指纹：UA / Client Hints / WebGL / Canvas 全为真值
   - 有真实浏览历史 / 扩展 / 书签 / 其他网站 cookies
2. Patchright 在 CDP 协议层修补 Runtime.enable / Console.enable 泄漏
   - 不注入任何 JS（add_init_script 注入本身就是检测信号）
3. 独立 user-data-dir 累积"生活痕迹"（一次启动后 Profile 持久化）
4. 不覆盖 User-Agent（UA 和浏览器实际指纹必须一致）
5. 让浏览器自己发请求（滚动触发），拦截响应 → 合法请求
6. 滚动间隔 5-10s 随机 → 模拟人类浏览
7. 详情页访问间隔 3-5s → 模拟人类快速浏览
8. 单次下载上限 100 个 → 控制使用强度
9. 检测到 461/captcha 立即停止 → 保护账号

注意：window._webmsxyw 返回的 XYW_ 签名格式已被 API 拒绝（406），
      必须让浏览器自己发请求（XYS_ 格式）然后拦截响应。
"""
import os
import re
import sys
import json
import socket
import random
import asyncio
import subprocess
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, urlencode

from engines.base import BaseEngine, DownloadItem, DownloadResult
from config import DownloadConfig


# 安全配置（基于全网调研的最佳实践）
# 单次默认上限：100 个（详情页间隔 3-5s，约 5-8 分钟，平衡风控与需求）
MAX_DOWNLOAD_PER_SESSION = 100
# 滚动间隔（秒）- 模拟人类浏览速度
MIN_SCROLL_DELAY = 5.0
MAX_SCROLL_DELAY = 10.0
# 详情页访问间隔（秒）- 模拟人类快速浏览节奏（真实 Chrome + Patchright 已解决指纹层检测，
# 此处仅控制行为频率，3-5s 是真实用户快速浏览的合理间隔）
MIN_DETAIL_DELAY = 3.0
MAX_DETAIL_DELAY = 5.0
# 最大滚动次数（安全上限，防无限循环；正常情况靠"连续无新增"自然停止）
MAX_SCROLL_COUNT = 100

# Chrome CDP 配置
CDP_PORT = 9222
# 独立 Profile 目录：累积浏览历史 / cookies，越来越像真实浏览器
# 放在项目目录内（避免沙箱限制访问 home 目录），不影响用户日常 Chrome
CHROME_USER_DATA_DIR = Path(__file__).resolve().parent.parent / '.chrome-profile'


def _find_chrome_executable() -> str:
    """查找系统安装的 Google Chrome 可执行文件路径"""
    if sys.platform == 'win32':
        candidates = [
            Path(os.environ.get('PROGRAMFILES', 'C:\\Program Files')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:\\Program Files (x86)')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe',
        ]
    elif sys.platform == 'darwin':
        candidates = [Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')]
    else:
        candidates = [
            Path('/usr/bin/google-chrome'),
            Path('/usr/bin/google-chrome-stable'),
            Path('/usr/bin/chromium'),
            Path('/usr/bin/chromium-browser'),
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise RuntimeError(
        "未找到系统 Google Chrome，请安装 Google Chrome 浏览器。\n"
        "下载地址: https://www.google.com/chrome/"
    )


def _is_port_in_use(port: int) -> bool:
    """检查 CDP 端口是否已被监听（说明 Chrome 已在运行）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


def _extract_initial_state_from_html(html: str):
    """从 HTML 中直接提取 window.__INITIAL_STATE__ 的 JSON 并解析

    背景：patchright CDP 模式下，页面内联 <script>window.__INITIAL_STATE__=...</script>
    不会在 main world 执行（可能是 CSP 或 hydration 清理），导致 page.evaluate
    读不到 window.__INITIAL_STATE__。但 script 标签的文本内容仍在 DOM 中，
    可以用括号匹配直接从 HTML 提取 JSON。

    注意：JSON 中可能包含 undefined（JS 合法但 JSON 非法），需替换为 null。
    """
    marker = 'window.__INITIAL_STATE__='
    idx = html.find(marker)
    if idx < 0:
        return None
    start = html.find('{', idx)
    if start < 0:
        return None
    # 括号匹配，处理字符串内的括号
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(html)):
        c = html[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    json_str = html[start:end + 1]
    # JS 对象字面量可能包含 undefined（JSON 标准不允许），替换为 null
    json_str = re.sub(r'(?<=:)\s*undefined(?=\s*[,}\]])', 'null', json_str)
    json_str = re.sub(r'(?<=[,\[])\s*undefined(?=\s*[,}\]])', 'null', json_str)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


# 不再使用 STEALTH_JS 注入
# 真实 Chrome 不需要任何 JS 修补，patchright 在 CDP 协议层完成所有反检测
# JS 注入本身就是检测信号（属性描述符 / 原型链 / toString() 都会暴露）


class XiaohongshuEngine(BaseEngine):
    """小红书下载引擎 - CDP 真实浏览器 + Patchright 协议层反检测"""

    platform = "xiaohongshu"

    def __init__(self, config: DownloadConfig):
        super().__init__(config)
        self._cookie = config.cookie or self._load_cookie()
        self._playwright = None
        self._browser = None
        self._chrome_process = None  # 脚本启动的 Chrome 子进程（None = 连接外部已启动的 Chrome）
        self._user_agent = None  # 从浏览器获取真实 UA，供 download_item 的 HTTP 请求使用

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
        """启动真实 Chrome（如未启动）并通过 Patchright CDP 连接

        反检测关键点：
        1. 用 connect_over_cdp 连接真实 Chrome，不是 launch() 启动 Chromium
           - launch() 启动的浏览器带 --enable-automation 标记，UA 是 Chromium 不是 Chrome
           - Client Hints brand 是 "Chromium" 而非 "Google Chrome"，秒检测
           - 全新实例无书签 / 扩展 / 浏览历史 / 其他网站 cookies
        2. 用 Patchright 替代 Playwright，修补 CDP 协议层泄漏
           - Runtime.enable leak（反检测脚本能检测此命令是否被调用过）
           - Console.enable leak（同上）
           - 这些泄漏在 CDP 协议层完成，页面内 JS 完全透明
        3. 独立 user-data-dir 累积"生活痕迹"
           - 第一次启动后 Profile 持久化，浏览器越来越像真实用户
           - 不影响用户日常 Chrome（用户日常 Chrome 用默认 Profile）
        """
        if self._browser is not None:
            return

        # 1. 如 CDP 端口未监听，启动真实 Chrome
        if not _is_port_in_use(CDP_PORT):
            chrome_path = _find_chrome_executable()
            CHROME_USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

            args = [
                chrome_path,
                f'--remote-debugging-port={CDP_PORT}',
                f'--user-data-dir={CHROME_USER_DATA_DIR}',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-blink-features=AutomationControlled',
                '--mute-audio',
                # 不加 --headless（headless 是检测点）
                # 不加 --no-startup-window（CDP 模式必须有窗口）
            ]
            self._chrome_process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # 等待 CDP 端口就绪（最长 15 秒）
            for _ in range(30):
                if _is_port_in_use(CDP_PORT):
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(
                    f"Chrome 启动超时，CDP 端口 {CDP_PORT} 未就绪。\n"
                    "可能原因：已有 Chrome 实例占用，请先关闭所有 Chrome 窗口后重试。"
                )

        # 2. 用 Patchright 通过 CDP 连接（API 与 Playwright 完全兼容）
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "未安装 patchright。请运行: pip install patchright\n"
                "（patchright 是 playwright 的反检测 fork，修补了 Runtime.enable / Console.enable 协议层泄漏）"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f'http://127.0.0.1:{CDP_PORT}'
        )

    async def _close_browser(self):
        """断开 CDP 连接

        CDP 模式注意事项：
        - browser.close() 仅断开 CDP 协议连接，不会关闭 Chrome 进程
        - 不杀 Chrome 子进程 —— 让独立 Profile 持久化累积"生活痕迹"
          （下次启动 Chrome 时这个 Profile 已经有历史，更像真实浏览器）
        - 用户可手动关闭 Chrome，或下次脚本启动时复用同一 Profile
        """
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
        # 不杀 Chrome 子进程（让 Profile 持久化）

    async def _new_context(self):
        """获取 CDP 浏览器的现有 context

        CDP 模式关键限制（参考反检测文章坑 5）:
        - 不能 new_context()，会触发 ERR_CONNECTION_CLOSED
        - 必须用 browser.contexts[0]（Chrome 启动时自动创建的默认 context）
        - 不调用 add_init_script（JS 注入本身就是检测信号）
        - 真实 Chrome 不需要任何 stealth JS 修补
        - 不覆盖 user_agent / viewport / locale / timezone
          （UA 和浏览器实际指纹必须一致，否则 UA-Client Hints 不一致是检测点）
        """
        await self._ensure_browser()

        # 用现有 context，不创建新 context
        if self._browser.contexts:
            context = self._browser.contexts[0]
        else:
            # 极少数情况下 Chrome 启动后还没有 context，创建一个
            context = await self._browser.new_context()

        # 注入 cookies（覆盖同域名同名的 cookie）
        await context.add_cookies(self._parse_cookies())

        # 缓存真实 UA，供 download_item 的 HTTP 请求使用
        if self._user_agent is None:
            try:
                pages = context.pages
                if pages:
                    self._user_agent = await pages[0].evaluate('navigator.userAgent')
                else:
                    new_page = await context.new_page()
                    try:
                        self._user_agent = await new_page.evaluate('navigator.userAgent')
                    finally:
                        await new_page.close()
            except Exception:
                # 极少数情况下获取失败，用 fallback UA（不理想但能跑）
                self._user_agent = (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/130.0.0.0 Safari/537.36'
                )

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
        # -n 0（不限）→ 用默认上限 100
        # -n N → 尊重用户选择，超过 100 给风控警告但不强制限制
        effective_max = self.config.max_count
        if effective_max == 0:
            effective_max = MAX_DOWNLOAD_PER_SESSION
            self._log(f"  未指定 -n，默认下载 {MAX_DOWNLOAD_PER_SESSION} 个")
        elif effective_max > MAX_DOWNLOAD_PER_SESSION:
            self._log(f"  ⚠ 您指定了 {effective_max} 个，超过建议上限 {MAX_DOWNLOAD_PER_SESSION}，注意风控风险")

        user_id = self._extract_user_id(user_url)

        # 保留原始 URL 中的 xsec_token 等参数
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
            # 等待 Vue app 挂载 + SSR hydration 完成
            try:
                await page.wait_for_function(
                    "() => { const el = document.querySelector('#app'); return el && el.__vue_app__; }",
                    timeout=15000
                )
            except Exception:
                pass
            await page.wait_for_timeout(2000)

            # 从 HTML 直接提取 __INITIAL_STATE__（patchright CDP 模式下 inline script
            # 不在 main world 执行，window.__INITIAL_STATE__ 读取不到，必须从 HTML 提取）
            html = await page.content()
            initial_state = _extract_initial_state_from_html(html)

            # 检查登录状态：优先用 __INITIAL_STATE__.user，fallback 到 DOM 信号
            nickname = ''
            logged_in = False
            if initial_state and initial_state.get('user'):
                user_state = initial_state['user']
                logged_in = bool(user_state.get('loggedIn'))
                user_info = user_state.get('userInfo') or {}
                nickname = user_info.get('nickname', '')

            # Fallback：__INITIAL_STATE__ 拿不到时用 DOM 信号
            if not logged_in:
                dom_login_check = await page.evaluate('''() => {
                    const loginBtn = document.querySelector('.login-btn, [class*="login-container"]');
                    const url = window.location.href;
                    const hasLoginRedirect = url.includes('/login') || url.includes('signin');
                    const userAvatar = document.querySelector(
                        '[class*="user-avatar"], [class*="avatar-wrapper"], [class*="user-info"]'
                    );
                    return {
                        hasLoginButton: !!loginBtn,
                        hasLoginRedirect: hasLoginRedirect,
                        hasUserAvatar: !!userAvatar,
                    };
                }''')
                if dom_login_check.get('hasUserAvatar') and not dom_login_check.get('hasLoginRedirect'):
                    logged_in = True
                    # 从页面标题提取昵称（格式: "昵称 - 小红书"）
                    try:
                        title = await page.title()
                        if ' - 小红书' in title:
                            nickname = title.split(' - 小红书')[0]
                    except Exception:
                        pass

            if not logged_in:
                raise RuntimeError(
                    "小红书 Cookie 已失效或未登录，无法获取笔记数据。\n"
                    "请重新导出 cookies.txt 中的 xiaohongshu.com Cookie。"
                )

            self._log(f"  已登录: {nickname or page.url}")

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
            # CDP 模式下不调用 context.close()
            # 原因：context 是 Chrome 默认 context（browser.contexts[0]），
            # close() 会关闭所有 page，可能影响用户的其他标签页
            try:
                await page.close()
            except Exception:
                pass
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
            # CDP 模式下不调用 context.close()（同 fetch_user_items）
            await self._close_browser()

    async def _scroll_and_intercept_notes(self, page, max_count: int, captured_data: dict) -> list:
        """获取笔记列表：SSR 数据为主，API 拦截为辅，滚动补充更多

        数据源优先级：
        1. __INITIAL_STATE__.user.notes — SSR 渲染的笔记（含完整 xsecToken）
           ※ user_posted API 的 cursor 会跳过前 30 个，只返回更早的 4 个，has_more=False，
              无法靠 API 拿到全部笔记。SSR 是唯一可靠的首屏数据源。
        2. user_posted API 拦截器捕获的笔记（SSR 之外的更多笔记，需滚动触发）

        字段命名：SSR 中是 xsecToken（驼峰），API 响应中是 xsec_token（下划线），
        此处统一保留原字段名，_fetch_note_detail_via_page 会兼容两种命名。

        注意：patchright CDP 模式下 inline script 不在 main world 执行，
        window.__INITIAL_STATE__ 读取不到，必须从 page.content() 的 HTML 提取。
        """
        notes = []
        seen_ids = set()

        def _add(n):
            nid = n.get('note_id') or n.get('id', '')
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                notes.append(n)

        # 1. 从 HTML 提取 __INITIAL_STATE__，获取 SSR 笔记（主要数据源）
        self._log(f"  从 __INITIAL_STATE__ 提取 SSR 笔记...")
        html = await page.content()
        initial_state = _extract_initial_state_from_html(html)
        if initial_state:
            user_state = initial_state.get('user') or {}
            tabs = user_state.get('notes') or []
            # notes 是数组的数组（每个 tab 一个数组），取第一个非空 tab
            for tab in tabs:
                if isinstance(tab, list):
                    for n in tab:
                        if not isinstance(n, dict):
                            continue
                        note_id = n.get('id', '')
                        nc = n.get('noteCard') or {}
                        _add({
                            'note_id': note_id,
                            'id': note_id,
                            'xsecToken': n.get('xsecToken', ''),  # camelCase
                            'display_title': nc.get('displayTitle', ''),
                            'type': nc.get('type', ''),
                        })
        self._log(f"  SSR 笔记: {len(notes)} 个")

        # 2. 合并 API 拦截器已捕获的笔记（goto 期间就发出的首次响应）
        api_count_before = len(notes)
        for n in captured_data['notes']:
            _add(n)
        if len(notes) > api_count_before:
            self._log(f"  合并 API 拦截新增 {len(notes) - api_count_before} 个")

        # 3. 继续滚动加载更多（缓慢，模拟人类），合并新捕获的 API 笔记
        # 停止条件：达到 max_count / 检测到风控(461) / 连续 2 次无新增（已到底）
        scroll_count = 0
        consecutive_empty = 0  # 连续无新增次数
        while len(notes) < max_count and scroll_count < MAX_SCROLL_COUNT and not captured_data['stop']:
            scroll_count += 1
            delay = self._random_delay(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY)
            self._log(f"  滚动加载第 {scroll_count} 次，等待 {delay:.1f}s...")
            await asyncio.sleep(delay)

            # 平滑滚动（模拟人类滚轮，不是瞬间跳到底部）
            await page.evaluate('''() => {
                return new Promise(resolve => {
                    const total = document.body.scrollHeight - window.innerHeight - window.scrollY;
                    const step = 300 + Math.random() * 200;
                    let scrolled = 0;
                    const timer = setInterval(() => {
                        window.scrollBy(0, step);
                        scrolled += step;
                        if (scrolled >= total) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100 + Math.random() * 100);
                });
            }''')
            await page.wait_for_timeout(3000)

            # 合并新捕获的 API 笔记
            before = len(notes)
            for n in captured_data['notes']:
                _add(n)
            new_count = len(notes) - before
            self._log(f"  累计: {len(notes)} 个笔记" + (f" (+{new_count})" if new_count > 0 else ""))

            if captured_data['stop']:
                self._log(f"  ⚠ 检测到风控（461），停止滚动")
                break

            # 连续 2 次无新增 → 已到底，停止
            if new_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    self._log(f"  连续 2 次无新增，已到底部，停止滚动")
                    break
            else:
                consecutive_empty = 0

        return notes[:max_count]

    async def _fetch_note_detail_via_page(self, page, note_info: dict, nickname: str) -> Optional[DownloadItem]:
        """
        访问笔记详情页，从 HTML 中的 __INITIAL_STATE__ 读取详情数据
        详情通过 SSR 渲染，不调用 feed API（已验证）

        注意：patchright CDP 模式下 inline script 不在 main world 执行，
        Pinia store 为空，必须从 page.content() 的 HTML 提取 __INITIAL_STATE__。
        """
        note_id = note_info.get('note_id') or note_info.get('noteId') or note_info.get('id', '')
        # 兼容两种命名：SSR 中是 xsecToken（驼峰），API 响应中是 xsec_token（下划线）
        xsec_token = note_info.get('xsec_token') or note_info.get('xsecToken') or ''

        note_url = f'https://www.xiaohongshu.com/explore/{note_id}'
        if xsec_token:
            note_url += f'?xsec_token={xsec_token}&xsec_source=pc_note'

        # 访问详情页（浏览器会通过 SSR 渲染数据到 __INITIAL_STATE__）
        try:
            await page.goto(note_url, wait_until='domcontentloaded', timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # 从 HTML 提取 __INITIAL_STATE__，再从中读取 note 详情
        html = await page.content()
        initial_state = _extract_initial_state_from_html(html)
        if not initial_state:
            return None

        # note 详情在 state.note.noteDetailMap[note_id].note
        note_store = initial_state.get('note') or {}
        detail_map = note_store.get('noteDetailMap') or {}
        # 优先用 note_id 查找，找不到就用第一个 entry
        detail_entry = detail_map.get(note_id)
        if not detail_entry:
            for v in detail_map.values():
                detail_entry = v
                break
        if not detail_entry:
            return None

        note = detail_entry.get('note') or {}
        if not note:
            return None

        # 提取视频 URL
        video_url = ''
        video = note.get('video')
        if video:
            streams = (video.get('media') or {}).get('stream') or {}
            for k in ('h264', 'h265', 'av1'):
                stream_list = streams.get(k) or []
                if stream_list:
                    video_url = stream_list[0].get('masterUrl', '') or ''
                    if not video_url:
                        backup = stream_list[0].get('backupUrls') or []
                        if backup:
                            video_url = backup[0]
                    if video_url:
                        break

        # 提取图片 URL 列表
        image_urls = []
        image_list = note.get('imageList') or []
        for img in image_list:
            info_list = img.get('infoList') or []
            dft = None
            for il in info_list:
                if il.get('imageScene') == 'WB_DFT':
                    dft = il
                    break
            url = (dft or {}).get('url') or img.get('urlDefault') or ''
            if url:
                image_urls.append(url)

        detail = {
            'type': note.get('type', ''),
            'title': note.get('title', ''),
            'desc': note.get('desc', ''),
            'noteId': note.get('noteId') or note_id,
            'videoUrl': video_url,
            'imageUrls': image_urls,
            'coverUrl': (image_list[0].get('urlDefault') if image_list else '') or (video.get('image') or {}).get('firstFrame', ''),
            'nickname': (note.get('user') or {}).get('nickname', ''),
            'createTime': note.get('time', 0),
        }

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
                # 用浏览器的真实 UA（CDP 模式下从 navigator.userAgent 获取）
                # UA 必须和浏览器实际指纹一致，否则 UA-Client Hints 不一致是检测点
                "User-Agent": self._user_agent or (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/130.0.0.0 Safari/537.36'
                ),
            }

            if item.is_video:
                video_url = item.urls[0] if item.urls else ""
                if not video_url:
                    return DownloadResult(False, item, error="无视频下载链接")

                filepath = self._make_filepath(save_dir, item, ".mp4")

                # 检查文件是否已存在（跳过 0 字节残留）
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return DownloadResult(True, item, saved_paths=[filepath], skipped=True, skip_reason="已存在")

                # 下载视频（带重试 + 断点续传）
                # 断点续传：网络中断后重试时用 Range header 从断点继续，
                # 避免大文件反复从头下载导致在同样位置失败
                max_retries = self.config.max_retries
                last_error = None
                for attempt in range(max_retries):
                    try:
                        # 检查已下载的字节数（断点续传）
                        existing_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
                        download_headers = dict(headers)
                        if existing_size > 0:
                            download_headers['Range'] = f'bytes={existing_size}-'

                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                video_url, headers=download_headers,
                                timeout=aiohttp.ClientTimeout(total=600, sock_read=60),
                                allow_redirects=True,
                            ) as resp:
                                if resp.status == 200:
                                    # 服务器不支持续传（返回完整内容），从头写入
                                    if existing_size > 0:
                                        os.remove(filepath)
                                        existing_size = 0
                                    mode = 'wb'
                                    content_length = resp.content_length
                                elif resp.status == 206:
                                    # 断点续传成功，追加到文件
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

                                # 校验下载完整性
                                downloaded_size = os.path.getsize(filepath)
                                if content_length and downloaded_size < existing_size + content_length:
                                    # 未下载完整，触发重试
                                    raise aiohttp.ClientPayloadError(
                                        f"下载不完整: {downloaded_size}/{existing_size + content_length} bytes"
                                    )

                                saved_paths.append(filepath)
                                last_error = None
                                break
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
