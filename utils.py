"""
ShortVideoDownload - 工具函数
"""
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path


def ytdlp_cmd() -> list:
    """
    返回 yt-dlp 命令前缀（绑定当前解释器环境，优先以模块方式调用）

    使用 `sys.executable -m yt_dlp` 而非 PATH 里的 `yt-dlp`：
    - 环境隔离：跟随 venv 内 pip 安装的版本，不受全局 Python / PATH 干扰
      （PATH 上的 yt-dlp 可能来自全局环境，版本不可控且 `yt-dlp -U` 对
      pip 安装无效，曾经导致"检查更新"形同虚设）
    - 跨机器稳定：换机器只需 venv 内 pip install，无需全局安装
    - venv 未安装时兜底回退 PATH（兼容二进制发行版场景）
    """
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return ["yt-dlp"]


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    清理文件名，移除非法字符，截断过长的名字
    注意：不使用 NFC 规范化，因为会破坏 emoji 的 ZWJ 序列
    """
    # 移除 Windows 文件名非法字符（保留 emoji 和 ZWJ 序列）
    illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(illegal_chars, '_', name)
    # 将连续空格替换为单个空格
    name = re.sub(r'\s+', ' ', name)
    # 移除首尾空格和点号
    name = name.strip(' .')
    # 截断（按 Unicode 字符数截断，避免截断 emoji 代理对）
    if len(name) > max_length:
        name = name[:max_length]
    # 空名回退
    if not name:
        name = "untitled"
    return name


def deduplicate_filepath(filepath: str, duplicate_format: str = "_{seq:03d}") -> str:
    """
    处理文件名重复：如果文件已存在，在文件名后追加序号
    例如：video.mp4 → video_001.mp4 → video_002.mp4
    """
    if not os.path.exists(filepath):
        return filepath

    base, ext = os.path.splitext(filepath)
    seq = 1
    while True:
        new_path = f"{base}{duplicate_format.format(seq=seq)}{ext}"
        if not os.path.exists(new_path):
            return new_path
        seq += 1
        # 安全阈值
        if seq > 9999:
            raise RuntimeError(f"Too many duplicate files for: {filepath}")


def ensure_dir(path: str) -> str:
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)
    return path


def parse_create_time(value) -> "datetime | None":
    """
    统一解析各平台发布时间 → datetime，失败返回 None
    支持：epoch 秒（抖音/快手/B站，10位）、epoch 毫秒（小红书，13位）、
    ISO 变体（X '2018-10-10 20:04'）、微博英文格式 'Wed Aug 12 07:34:56 +0800 2026'
    """
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 纯数字：10 位=秒级，13 位=毫秒级
    if s.isdigit():
        n = int(s)
        if n <= 0:
            return None
        if len(s) >= 13:
            n //= 1000
        try:
            return datetime.fromtimestamp(n)
        except (OverflowError, OSError, ValueError):
            return None
    # ISO 变体（X: '2018-10-10 20:04' / 标准 ISO 带 Z 或偏移）
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        pass
    # 微博英文格式
    try:
        return datetime.strptime(s, '%a %b %d %H:%M:%S %z %Y')
    except ValueError:
        pass
    return None


def build_display_title(desc: str, max_len: int = 80) -> str:
    """
    从抖音描述构建显示标题：主标题 + 副标题（标签）
    - 主标题：描述中非 #标签 的文字
    - 副标题：描述中的 #标签 内容（去掉 # 符号）
    - 合并格式：主标题_副标题1_副标题2
    - 若主标题为空，用副标题替代（不使用 untitled）
    - 若副标题为空，只用主标题
    """
    if not desc:
        return "untitled"

    first_line = desc.split('\n')[0].strip()

    # 按 # 分割：第一个 # 之前的是主标题，之后的是标签
    parts = first_line.split('#')
    main_title = parts[0].strip()
    # 每个 # 后面的内容是一个标签（到下一个 # 或行尾）
    tags = []
    for part in parts[1:]:
        tag = part.strip().rstrip('_')
        if tag:
            # 清理标签中的特殊字符（保留中文、字母、数字、下划线）
            clean_tag = re.sub(r'[^\w\u4e00-\u9fff]', '', tag)
            if clean_tag:
                tags.append(clean_tag)

    # 构建标题
    if main_title and tags:
        combined = main_title + '_' + '_'.join(tags)
    elif tags:
        combined = '_'.join(tags)
    elif main_title:
        combined = main_title
    else:
        return "untitled"

    # 截断
    if len(combined) > max_len:
        combined = combined[:max_len]

    return sanitize_filename(combined) if combined else "untitled"


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def detect_platform(url: str) -> str:
    """
    根据 URL 检测平台
    返回平台标识字符串
    """
    from config import PLATFORM_PATTERNS

    url_lower = url.lower()
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url_lower):
                return platform
    return "unknown"


def detect_single_video(url: str) -> tuple:
    """
    检测 URL 是否是单个视频/作品链接
    返回 (platform, video_id) 或 (None, None)

    支持的抖音格式:
      - https://www.douyin.com/user/{sec_uid}?modal_id={aweme_id}  (用户主页弹窗)
      - https://www.douyin.com/video/{aweme_id}
      - https://www.douyin.com/note/{aweme_id}    (图集笔记)
      - https://www.iesdouyin.com/share/video/{aweme_id}

    支持的小红书格式:
      - https://www.xiaohongshu.com/explore/{note_id}    (标准，可带 ?xsec_token=)
      - https://www.xiaohongshu.com/discovery/item/{note_id}  (旧格式)
      - https://www.xiaohongshu.com/note/{note_id}       (笔记直链)

    支持的B站格式:
      - https://www.bilibili.com/video/BVxxxxxxxxxx       (BV号，可带 ?spm_id_from= 等参数)
      - https://www.bilibili.com/video/av123456           (av号旧格式)

    注意: 小红书访问详情页需要 xsec_token，由 fetch_single_item 从原始 URL 提取。
    """
    from urllib.parse import urlparse, parse_qs

    platform = detect_platform(url)
    if platform == "douyin":
        # 1. modal_id 参数（用户主页弹窗模式）
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if params.get('modal_id'):
            return ("douyin", params['modal_id'][0])
        # 2. /video/{aweme_id} 路径
        m = re.search(r'/video/(\d+)', url)
        if m:
            return ("douyin", m.group(1))
        # 3. /note/{aweme_id} 路径（图集）
        m = re.search(r'/note/(\d+)', url)
        if m:
            return ("douyin", m.group(1))
        # 4. iesdouyin share
        m = re.search(r'iesdouyin\.com/share/video/(\d+)', url)
        if m:
            return ("douyin", m.group(1))
    elif platform == "xiaohongshu":
        # 1. /explore/{note_id} （标准格式，可带 xsec_token 参数）
        m = re.search(r'/explore/([A-Za-z0-9]{8,})', url)
        if m:
            return ("xiaohongshu", m.group(1))
        # 2. /discovery/item/{note_id} （旧格式）
        m = re.search(r'/discovery/item/([A-Za-z0-9]{8,})', url)
        if m:
            return ("xiaohongshu", m.group(1))
        # 3. /note/{note_id} （笔记直链）
        m = re.search(r'/note/([A-Za-z0-9]{8,})', url)
        if m:
            return ("xiaohongshu", m.group(1))
    elif platform == "bilibili":
        # 1. /video/BVxxxxxxxxxx （BV号，B站主推格式：BV + 10 位字母数字）
        m = re.search(r'bilibili\.com/video/(BV[A-Za-z0-9]{10})', url)
        if m:
            return ("bilibili", m.group(1))
        # 2. /video/av123456 （av号旧格式，保留 av 前缀以区分 BV 号）
        m = re.search(r'bilibili\.com/video/av(\d+)', url, re.IGNORECASE)
        if m:
            return ("bilibili", f"av{m.group(1)}")
    elif platform == "x":
        # https://x.com/{user}/status/{id} 或 https://twitter.com/{user}/status/{id}
        m = re.search(r'(?:x|twitter)\.com/[^/]+/status/(\d+)', url)
        if m:
            return ("x", m.group(1))
    return (None, None)


def extract_user_id(url: str, platform: str) -> str:
    """
    从 URL 中提取用户 ID
    不同平台的用户主页 URL 格式不同
    """
    if platform == "douyin":
        # https://www.douyin.com/user/MS4wLjABAAAA...
        match = re.search(r'/user/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
    elif platform == "kuaishou":
        # https://www.kuaishou.com/profile/3x...
        match = re.search(r'/profile/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
    elif platform == "xiaohongshu":
        # https://www.xiaohongshu.com/user/profile/5f...
        match = re.search(r'/user/profile/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(1)
    elif platform == "bilibili":
        # https://space.bilibili.com/123456
        match = re.search(r'space\.bilibili\.com/(\d+)', url)
        if match:
            return match.group(1)
    elif platform == "weibo":
        # https://weibo.com/u/123456
        match = re.search(r'weibo\.com/u/(\d+)', url)
        if match:
            return match.group(1)
    elif platform == "x":
        # https://x.com/{screen_name} 或 https://x.com/{screen_name}/media
        # 可选尾部日期范围参数：/20241201-20210506（X 引擎专用，此处剥离）
        path = url.split('?')[0].rstrip('/')
        path = re.sub(r'/\d{8}[-_~]\d{8}$', '', path)
        match = re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})(?:/media)?$', path)
        if match:
            reserved = {'home', 'i', 'explore', 'search', 'settings', 'messages',
                        'notifications', 'compose', 'intent', 'hashtag', 'login', 'signup'}
            if match.group(1).lower() not in reserved:
                return match.group(1)

    # 回退：尝试提取 URL 最后一段路径
    match = re.search(r'/([A-Za-z0-9_-]+)/?$', url.rstrip('/'))
    if match:
        return match.group(1)

    return "unknown_user"


def extract_browser_cookies(browser: str, domain: str) -> str:
    """
    从浏览器提取指定域名的 Cookie
    按优先级尝试多种提取方式：
    1. rookiepy（Rust实现，最可靠，Firefox无需管理员权限）
    2. yt-dlp 内置的 Cookie 提取
    3. browser_cookie3

    注意：Chrome/Edge v127+ 引入了 App-Bound Encryption，
    在 Windows 上非管理员权限无法解密 Cookie。
    建议使用 Firefox 或手动导出 cookies.txt 文件。

    Args:
        browser: 浏览器名称 (chrome, firefox, edge, opera)
        domain: 目标域名 (如 douyin.com, kuaishou.com)

    Returns:
        Cookie 字符串 (如 "key1=val1; key2=val2")

    Raises:
        RuntimeError: 如果提取失败
    """
    # 方式1: 使用 rookiepy（Rust实现，最可靠）
    cookie_str = _extract_cookies_via_rookiepy(browser, domain)
    if cookie_str:
        return cookie_str

    # 方式2: 使用 yt-dlp 内置的 Cookie 提取
    cookie_str = _extract_cookies_via_ytdlp(browser, domain)
    if cookie_str:
        return cookie_str

    # 方式3: 使用 browser_cookie3
    cookie_str = _extract_cookies_via_browser_cookie3(browser, domain)
    if cookie_str:
        return cookie_str

    raise RuntimeError(
        f"无法从浏览器 {browser} 提取 {domain} 的 Cookie。\n\n"
        f"可能的原因:\n"
        f"  1. Chrome/Edge v127+ 使用了 App-Bound Encryption，非管理员无法解密\n"
        f"  2. 浏览器未登录 {domain}\n"
        f"  3. Cookie 已加密且无法解密\n\n"
        f"替代方案:\n"
        f"  1. 使用 Firefox: --browser-cookie firefox（Firefox 不受影响）\n"
        f"  2. 使用浏览器扩展导出 cookies.txt 文件，放到项目根目录\n"
        f"  3. 使用 --cookie 参数手动提供 Cookie\n"
        f"  4. 以管理员权限运行本程序"
    )


def _extract_cookies_via_rookiepy(browser: str, domain: str) -> str:
    """
    使用 rookiepy 从浏览器提取 Cookie（首选方案）
    rookiepy 是 Rust 实现的 Cookie 提取库，比 browser_cookie3 更可靠
    注意：Chrome/Edge v127+ 在 Windows 上需要管理员权限
    """
    try:
        import rookiepy
    except ImportError:
        return ""

    browser_map = {
        "chrome": rookiepy.chrome,
        "firefox": rookiepy.firefox,
        "edge": rookiepy.edge,
        "opera": rookiepy.opera,
    }

    cookie_func = browser_map.get(browser.lower())
    if not cookie_func:
        return ""

    try:
        # rookiepy 需要 domain 前加 .
        domain_pattern = f".{domain}" if not domain.startswith(".") else domain
        cookies_list = cookie_func(domains=[domain_pattern])

        if not cookies_list:
            return ""

        # rookiepy 返回 list[dict]，每个 dict 包含 name, value 等
        cookie_parts = []
        for c in cookies_list:
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                cookie_parts.append(f"{name}={value}")

        if not cookie_parts:
            return ""

        return "; ".join(cookie_parts)

    except Exception:
        return ""


def _extract_cookies_via_ytdlp(browser: str, domain: str) -> str:
    """
    使用 yt-dlp 内置的 Cookie 提取功能
    yt-dlp 的实现比 browser_cookie3 更可靠，支持更多浏览器版本
    """
    import subprocess
    import tempfile

    try:
        # 使用 yt-dlp 导出 Cookie 到 Netscape 格式文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            cookie_file = f.name

        # 构造一个测试URL来触发yt-dlp的Cookie提取
        test_url = f"https://www.{domain}/"

        cmd = [
            *ytdlp_cmd(),
            "--cookies-from-browser", browser,
            "--cookies", cookie_file,
            "--skip-download",
            "--quiet",
            "--no-warnings",
            test_url,
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )

        # 即使 yt-dlp 下载失败，Cookie 文件可能已经生成
        if os.path.exists(cookie_file):
            cookie_str = _parse_netscape_cookie_file(cookie_file, domain)
            try:
                os.unlink(cookie_file)
            except OSError:
                pass
            if cookie_str:
                return cookie_str

        return ""

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    except Exception:
        return ""


def _extract_cookies_via_browser_cookie3(browser: str, domain: str) -> str:
    """
    使用 browser_cookie3 从浏览器提取 Cookie（回退方案）
    """
    try:
        import browser_cookie3
    except ImportError:
        return ""

    browser_map = {
        "chrome": browser_cookie3.chrome,
        "firefox": browser_cookie3.firefox,
        "edge": browser_cookie3.edge,
        "opera": browser_cookie3.opera,
    }

    cookie_func = browser_map.get(browser.lower())
    if not cookie_func:
        return ""

    try:
        cj = cookie_func(domain_name=domain)
        cookies = []
        for c in cj:
            cookies.append(f"{c.name}={c.value}")

        if not cookies:
            return ""

        return "; ".join(cookies)

    except Exception:
        return ""


def _parse_netscape_cookie_file(cookie_file: str, domain: str) -> str:
    """
    解析 Netscape 格式的 Cookie 文件，提取指定域名的 Cookie
    返回 "key1=val1; key2=val2" 格式的字符串
    """
    cookies = []
    # 去掉域名开头的点，用于匹配
    domain_clean = domain.lstrip('.')

    try:
        with open(cookie_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) < 7:
                    continue
                cookie_domain = parts[0].lstrip('.')
                # 匹配域名（支持子域名）
                if cookie_domain == domain_clean or cookie_domain.endswith('.' + domain_clean) or domain_clean.endswith(cookie_domain):
                    name = parts[5]
                    value = parts[6]
                    cookies.append(f"{name}={value}")
    except Exception:
        pass

    return "; ".join(cookies)


def load_cookies_from_file(domain: str) -> str:
    """
    从项目根目录的 cookies.txt 文件加载指定域名的 Cookie
    cookies.txt 使用 Netscape 格式（与 yt-dlp 兼容）

    自动尝试备用域名（如 bilibili.cn / bilibili.tv 也作为 B站 Cookie）

    Args:
        domain: 目标域名（主域名，如 bilibili.com）

    Returns:
        Cookie 字符串，如果文件不存在或无匹配则返回空字符串
    """
    # 在项目根目录查找 cookies.txt
    project_root = os.path.dirname(os.path.abspath(__file__))
    cookie_file = os.path.join(project_root, "cookies.txt")

    if not os.path.exists(cookie_file):
        return ""

    # 尝试主域名
    cookie_str = _parse_netscape_cookie_file(cookie_file, domain)
    if cookie_str:
        return cookie_str

    # 尝试备用域名（如 .bilibili.cn 也是 B站的）
    domain_alt_map = {
        "bilibili.com": ["bilibili.cn", "bilibili.tv"],
        "weibo.com": ["weibo.cn"],
        "x.com": ["twitter.com"],
    }
    alts = domain_alt_map.get(domain, [])
    for alt in alts:
        alt_cookie = _parse_netscape_cookie_file(cookie_file, alt)
        if alt_cookie:
            return alt_cookie

    return ""


def get_domain_for_platform(platform: str) -> str:
    """获取平台对应的主域名（用于Cookie提取）"""
    domain_map = {
        "douyin": "douyin.com",
        "kuaishou": "kuaishou.com",
        "xiaohongshu": "xiaohongshu.com",
        "bilibili": "bilibili.com",
        "weibo": "weibo.com",
        "x": "x.com",
    }
    return domain_map.get(platform, "")


def get_alt_domains_for_platform(platform: str) -> list:
    """获取平台的备用域名列表（用于 cookies.txt 解析时的额外匹配）"""
    alt_map = {
        "bilibili": ["bilibili.cn", "bilibili.tv"],
        "weibo": ["weibo.cn"],
        "x": ["twitter.com"],
    }
    return alt_map.get(platform, [])


def load_netscape_cookie_dicts(domain: str) -> list:
    """
    从项目根目录 cookies.txt 加载指定域名的完整 Cookie 信息
    返回 Playwright add_cookies 格式的 dict 列表（保留 domain/path/secure/httpOnly/expires），
    供 CDP 浏览器注入使用（X 引擎）。同一 (name, domain) 重复时保留 expires 最长的一条。

    支持 "#HttpOnly_" 前缀行（yt-dlp / 浏览器扩展导出的 httpOnly Cookie 标记）。
    自动包含备用域名（通过 get_alt_domains_for_platform 的映射）。
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    cookie_file = os.path.join(project_root, "cookies.txt")
    if not os.path.exists(cookie_file):
        return []

    domains = {domain.lstrip('.')}
    # 备用域名（x.com → twitter.com）
    alt_map = {"x.com": ["twitter.com"]}
    domains |= set(alt_map.get(domain, []))

    best = {}  # (name, domain) -> cookie dict
    try:
        with open(cookie_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                http_only = False
                if raw.startswith('#HttpOnly_'):
                    http_only = True
                    raw = raw[len('#HttpOnly_'):]
                elif raw.startswith('#'):
                    continue
                parts = raw.split('\t')
                if len(parts) < 7:
                    continue
                cookie_domain = parts[0]
                domain_clean = cookie_domain.lstrip('.')
                if not (domain_clean in domains or
                        any(domain_clean.endswith('.' + d) or d.endswith('.' + domain_clean)
                            for d in domains)):
                    continue
                try:
                    expires = float(parts[4])
                except ValueError:
                    expires = -1
                name = parts[5]
                value = parts[6]
                if not name:
                    continue
                key = (name, cookie_domain)
                if key in best and best[key].get('expires', -1) >= expires:
                    continue
                best[key] = {
                    'name': name,
                    'value': value,
                    'domain': cookie_domain,
                    'path': parts[2] or '/',
                    'secure': parts[3].upper() == 'TRUE',
                    'httpOnly': http_only,
                    'expires': expires if expires > 0 else -1,
                }
    except Exception:
        return []

    return list(best.values())


def get_system_proxy() -> str:
    """读取 Windows 系统代理设置（WinINET，Chrome/Edge 同源）
    返回 aiohttp 可用的代理 URL（如 http://127.0.0.1:7890），未启用返回空串
    """
    if sys.platform != 'win32':
        return ""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
        )
        try:
            enable, _ = winreg.QueryValueEx(key, 'ProxyEnable')
            if not enable:
                return ""
            server, _ = winreg.QueryValueEx(key, 'ProxyServer')
        finally:
            winreg.CloseKey(key)
        if not server:
            return ""
        # "http=host:port;https=host:port" 按协议分发格式 → 取 http 的
        if '=' in server:
            for part in server.split(';'):
                if part.startswith('http='):
                    server = part[5:]
                    break
            else:
                return ""
        if not server.startswith(('http://', 'https://', 'socks5://')):
            server = f'http://{server}'
        return server
    except Exception:
        return ""


# ------------------------------------------------------------------ 失败日志
# 下载失败的条目持久化到 {save_dir}/_failed_downloads.json，
# 可随时用 `python svd.py --retry-failed` 重新下载（无需重新爬列表）

FAILED_LOG_FILENAME = '_failed_downloads.json'


def load_failed_log(save_dir: str) -> list:
    """读取失败日志（不存在或损坏返回空列表）"""
    import json
    path = os.path.join(save_dir, FAILED_LOG_FILENAME)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_failed_log(save_dir: str, entries: list):
    """原子写入失败日志（tmp + replace，防写入中断损坏）"""
    import json
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, FAILED_LOG_FILENAME)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_failed_entry(save_dir: str, entry: dict):
    """追加一条失败记录；同 platform+item_id+save_dir 只保留最新一条"""
    entries = load_failed_log(save_dir)
    item_id = (entry.get('item') or {}).get('item_id')
    entries = [e for e in entries if not (
        e.get('platform') == entry.get('platform')
        and (e.get('item') or {}).get('item_id') == item_id
        and e.get('save_dir') == entry.get('save_dir')
    )]
    entries.append(entry)
    save_failed_log(save_dir, entries)


def prefilter_f2_logging():
    """
    在 import f2 之前预先屏蔽 f2 logger 的所有日志记录。

    f2 在 import 时（model.py 的 BaseRequestModel 类体）就会发起网络请求生成
    msToken。瞬时网络/代理 TLS 失败（如 SSL: UNEXPECTED_EOF_WHILE_READING）
    会先 logger.error 打出完整 traceback，然后 f2 内部静默重试并成功（自愈型
    错误，不影响后续下载）。suppress_f2_logging() 在 import 之后才生效，拦截
    不到 import 期间；而 f2 的 log_setup 只重置 level 和 handler，不会清除
    预先挂上的 filter，所以必须在 import 前调用本函数挂 filter。

    若 msToken 两次生成都失败，import 会直接抛出 APIConnectionError，由上层
    正常捕获报错，因此本函数不会掩盖真正的致命错误。
    """
    import logging

    class _SilenceFilter(logging.Filter):
        _svd_mark = True

        def filter(self, record):
            return False

    f2_logger = logging.getLogger("f2")
    if not any(getattr(f, "_svd_mark", False) for f in f2_logger.filters):
        f2_logger.addFilter(_SilenceFilter())


def suppress_f2_logging():
    """
    抑制 f2 库的冗余日志输出
    f2 的日志有两个来源：
    1. logging 系统（logger.info/error 等）—— 通过设置级别为 CRITICAL 抑制
    2. rich_console.print() 直接输出 —— 通过 monkey-patch 替换为静默 Console 抑制
    必须在 import f2 之后调用，否则 f2 的 log_setup() 会重置级别
    """
    import logging
    import logging.handlers
    import io

    try:
        from rich.console import Console as RichConsole
    except ImportError:
        RichConsole = None

    # 先触发 f2 的 import（这会调用 log_setup() 设置级别为 INFO）
    _f2_dy_handler = None
    _f2_bark_handler = None
    try:
        import f2.apps.douyin.handler as _f2_dy_handler
    except ImportError:
        pass
    try:
        import f2.apps.bark.handler as _f2_bark_handler
    except ImportError:
        pass

    # 抑制 logging 系统输出
    f2_logger = logging.getLogger("f2")
    f2_logger.setLevel(logging.CRITICAL)
    for handler in f2_logger.handlers[:]:
        if not isinstance(handler, logging.handlers.TimedRotatingFileHandler):
            handler.setLevel(logging.CRITICAL)

    # 抑制 rich_console.print() 直接输出
    _silent_console = None
    if RichConsole is not None:
        _silent_console = RichConsole(file=io.StringIO(), width=80, no_color=True)
    if _f2_dy_handler and _silent_console:
        _f2_dy_handler.rich_console = _silent_console
    if _f2_bark_handler and _silent_console:
        _f2_bark_handler.rich_console = _silent_console
