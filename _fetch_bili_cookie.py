"""
B站 Cookie 获取工具（绕过 Edge/Chrome App-Bound Encryption）

问题背景：
  Edge/Chrome v127+ 引入 App-Bound Encryption，外部程序（rookiepy/browser_cookie3/yt-dlp）
  无法在非管理员权限下解密 Cookie 数据库。但用户日常用 Edge 登录 B站看 1080p。

解决方案：
  用 Patchright 启动 Edge（独立 User Data 目录，支持 CDP），引导用户登录一次，
  Edge 进程自己解密 Cookie，通过 CDP 协议拿到明文 Cookie，保存到 cookies.txt。
  后续 Cookie 会持久化在独立 Profile 中，下次自动复用，无需重新登录。
  Patchright 修补了 CDP 协议层泄漏（Runtime.enable / Console.enable），不会被网站检测。

设计理由（为什么用独立 Profile 而不是用户默认 Profile）：
  - Edge/Chrome 安全策略：默认 Profile 不允许启用 --remote-debugging-port
    （防止恶意软件通过 CDP 窃取 Cookie）
  - 独立 Profile 可以启用 CDP，且不影响用户日常使用的 Edge
  - 独立 Profile 的 Cookie 也是 App-Bound 加密的，但 Edge 进程自己能解密，
    通过 CDP Network.getCookies 可以拿到明文

使用方法：
  python _fetch_bili_cookie.py
  # 首次会打开 Edge 窗口，在里面登录 B站，登录成功后按回车

注意：
  - Cookie 保存到项目根目录的 cookies.txt（Netscape 格式，与 yt-dlp 兼容）
  - 独立 Profile 存放在 .edge-bili-profile/ 目录（持久化，下次自动登录）
  - 完成后 Edge 窗口会自动关闭
"""
import os
import sys
import asyncio
import subprocess
import socket
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# 浏览器可执行文件路径（Windows 默认安装位置）
# 优先 Chrome，其次 Edge（Chrome 的 CDP 更稳定）
BROWSER_PATHS = {
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ],
}

# 独立 Profile 目录（放在项目内，不影响用户日常浏览器）
PROFILE_DIR = PROJECT_ROOT / ".edge-bili-profile"

# CDP 远程调试端口
CDP_PORT = 9222


def _find_browser_executable() -> tuple:
    """查找可用的浏览器（优先 Edge，其次 Chrome）

    Returns:
        (browser_name, executable_path)
    """
    for name, paths in BROWSER_PATHS.items():
        for path in paths:
            if os.path.exists(path):
                return name, path
    raise RuntimeError(
        "未找到 Edge 或 Chrome 浏览器。请安装 Edge 或 Chrome。\n"
        f"尝试过的路径: {BROWSER_PATHS}"
    )


def _is_port_in_use(port: int) -> bool:
    """检查端口是否被监听"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


async def _fetch_bili_cookie_via_cdp() -> list:
    """启动浏览器加 --remote-debugging-port=9222，通过 Patchright CDP 拿明文 Cookie

    流程：
    1. subprocess 启动浏览器进程（独立 Profile 目录）
    2. 等待 CDP 端口就绪
    3. Patchright connect_over_cdp 连接（修补 Runtime.enable 泄漏）
    4. 新页面打开 bilibili.com
    5. 如果未登录，引导用户在浏览器中登录
    6. context.cookies() 拿明文 Cookie（浏览器进程已解密）
    7. 关闭 CDP 连接，终止浏览器进程
    """
    from patchright.async_api import async_playwright

    browser_name, browser_path = _find_browser_executable()

    # 1. 启动浏览器加 CDP 端口（独立 Profile 目录）
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    args = [
        browser_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        # 不加 --disable-blink-features=AutomationControlled（项目规则禁止）
        # patchright 已在协议层修补 navigator.webdriver
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. 等待 CDP 端口就绪（最长 15 秒）
    for _ in range(30):
        if _is_port_in_use(CDP_PORT):
            break
        await asyncio.sleep(0.5)
    else:
        raise RuntimeError(
            f"浏览器启动超时，CDP 端口 {CDP_PORT} 未就绪。\n"
            "可能原因：端口被占用或浏览器启动失败。"
        )

    pw = None
    browser = None
    try:
        # 3. Patchright 通过 CDP 连接
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")

        # 4. 获取现有 context（CDP 模式必须用 browser.contexts[0]，不能 new_context）
        context = browser.contexts[0] if browser.contexts else await browser.new_context()

        # 5. 新页面打开 bilibili.com
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.bilibili.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # 6. 检查是否已登录
        cookies = await context.cookies("https://www.bilibili.com")
        has_sessdata = any(c.get("name") == "SESSDATA" for c in cookies)

        if not has_sessdata:
            # 未登录，引导用户在浏览器中登录
            print("\n[!] 检测到未登录 B站。")
            print("    请在打开的浏览器窗口中登录 B站账号。")
            print("    登录成功后，在命令行中按回车键继续...")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print("\n[X] 用户取消")
                return []

            # 重新拿 Cookie
            cookies = await context.cookies("https://www.bilibili.com")

        return cookies
    finally:
        # 7. 关闭 CDP 连接
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        # 8. 终止浏览器进程
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _save_cookies_to_netscape(cookies: list, cookie_file: str) -> tuple:
    """将 Cookie 保存到 Netscape 格式文件

    Args:
        cookies: Playwright Cookie 列表
        cookie_file: cookies.txt 文件路径

    Returns:
        (added_count, removed_count): 新增的 Cookie 数量，删除的旧 B站 Cookie 数量
    """
    # 读取现有 cookies.txt
    existing_lines = []
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()

    # 删除旧的 B站 Cookie（避免重复）
    kept_lines = []
    removed = 0
    for line in existing_lines:
        if 'bilibili.com' in line.lower() and not line.startswith('#'):
            removed += 1
            continue
        kept_lines.append(line)

    # 追加新的 B站 Cookie（Netscape 格式）
    # 注意：Playwright/CDP 返回的 session cookie 的 expires=-1，
    # Netscape 格式要求 expires 是 0（session）或正数 Unix 时间戳，
    # yt-dlp 遇到 -1 会跳过该条 Cookie（"invalid expires at -1"），
    # 关键 Cookie（如 SESSDATA）被跳过会导致 412 风控。
    new_lines = ["# B站 Cookie (from browser via Patchright CDP, updated)\n"]
    for c in cookies:
        domain = c.get('domain', '')
        if not domain:
            continue
        flag = "TRUE" if domain.startswith('.') else "FALSE"
        path = c.get('path', '/') or '/'
        secure = "TRUE" if c.get('secure') else "FALSE"
        # expires <= 0 (含 -1 session cookie) → 0 (Netscape session cookie)
        expiry = int(c.get('expires', 0) or 0)
        if expiry < 0:
            expiry = 0
        name = c.get('name', '')
        value = c.get('value', '')
        new_lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n")

    # 写回 cookies.txt
    with open(cookie_file, "w", encoding="utf-8") as f:
        f.writelines(kept_lines)
        f.writelines(new_lines)

    return len(cookies), removed


async def main():
    print("=" * 60)
    print("B站 Cookie 获取工具（绕过 App-Bound Encryption）")
    print("=" * 60)

    browser_name, browser_path = _find_browser_executable()
    print(f"\n[>] 检测到浏览器: {browser_name} ({browser_path})")
    print(f"    独立 Profile 目录: {PROFILE_DIR}")

    # 1. 检查端口是否被占用
    if _is_port_in_use(CDP_PORT):
        print(f"\n[!] 端口 {CDP_PORT} 已被占用。")
        print("    可能有其他 CDP 调试程序在运行，请关闭后重试。")
        return 1

    # 2. 启动浏览器并通过 CDP 拿 Cookie
    print(f"\n[>] 正在启动 {browser_name} 并获取 B站 Cookie...")
    try:
        cookies = await _fetch_bili_cookie_via_cdp()
    except Exception as e:
        print(f"\n[X] 获取 Cookie 失败: {e}")
        return 1

    if not cookies:
        print("\n[X] 未获取到 B站 Cookie。")
        return 1

    # 3. 保存到 cookies.txt
    cookie_file = PROJECT_ROOT / "cookies.txt"
    added, removed = _save_cookies_to_netscape(cookies, str(cookie_file))

    print(f"\n[√] 成功获取 {added} 个 B站 Cookie")
    print(f"    删除 {removed} 个旧 B站 Cookie")
    print(f"    保存到: {cookie_file}")

    # 4. 验证关键 Cookie
    key_cookies = {c['name']: c['value'] for c in cookies if c.get('name') in ('SESSDATA', 'DedeUserID', 'bili_jct')}
    if 'SESSDATA' in key_cookies:
        print(f"\n[√] SESSDATA 已获取: {key_cookies['SESSDATA'][:30]}...")
    else:
        print("\n[!] 未找到 SESSDATA Cookie，登录可能未成功。")
        print("    请重新运行脚本并在浏览器中登录 B站。")
        return 1

    if 'DedeUserID' in key_cookies:
        print(f"[√] DedeUserID 已获取: {key_cookies['DedeUserID']}")
    if 'bili_jct' in key_cookies:
        print(f"[√] bili_jct 已获取: {key_cookies['bili_jct'][:20]}...")

    print("\n" + "=" * 60)
    print("[√] 完成！现在可以下载 1080p 视频了。")
    print("    Cookie 已持久化到独立 Profile，下次自动复用。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[X] 用户中断")
        sys.exit(1)
