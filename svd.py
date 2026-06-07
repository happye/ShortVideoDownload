#!/usr/bin/env python3
"""
ShortVideoDownload - 短视频平台用户作品批量下载工具
支持抖音、快手、小红书、B站、微博等平台

用法:
    python svd.py <用户主页URL> [选项]

示例:
    python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..."
    python svd.py "https://www.kuaishou.com/profile/3x..." -o "D:\\Downloads"
    python svd.py "https://www.xiaohongshu.com/user/profile/5f..." --cookie "your_cookie"
"""
import os
import sys
import asyncio
import argparse
from datetime import datetime

# Windows 控制台 UTF-8 编码设置（必须在其他 import 之前）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    # 设置控制台输出代码页为 UTF-8
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    # 重新包装 stdout/stderr 为 UTF-8
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name)
        if stream and hasattr(stream, 'buffer'):
            try:
                import io
                setattr(sys, stream_name, io.TextIOWrapper(
                    stream.buffer, encoding='utf-8', errors='replace', line_buffering=True
                ))
            except Exception:
                pass

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 抑制 f2 库的冗余日志（必须在 import f2 之前设置）
import logging
logging.getLogger("f2").setLevel(logging.CRITICAL)

from config import DownloadConfig, PLATFORM_PATTERNS
from utils import detect_platform, extract_user_id, format_file_size
from engines import ENGINES

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


if RICH_AVAILABLE:
    # 检测是否在真实终端中运行（非管道/重定向）
    _is_terminal = sys.stdout.isatty()
    console = Console(force_terminal=_is_terminal, no_color=False)
else:
    class _FallbackConsole:
        def print(self, *args, **kwargs):
            print(*args)
        def status(self, msg, **kwargs):
            # 非 Rich 环境下的 status 上下文管理器
            import contextlib
            @contextlib.contextmanager
            def _status():
                print(msg)
                yield
            return _status()
    console = _FallbackConsole()


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="ShortVideoDownload - 短视频平台用户作品批量下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
支持平台:
  抖音:   https://www.douyin.com/user/MS4wLjABAAAA...
  快手:   https://www.kuaishou.com/profile/3x...
  小红书: https://www.xiaohongshu.com/user/profile/5f...
  B站:    https://space.bilibili.com/123456
  微博:   https://weibo.com/u/123456

示例:
  python svd.py "https://www.douyin.com/user/MS4wLjABAAAA..."
  python svd.py "https://www.kuaishou.com/profile/3x..." -o "D:\\Downloads"
  python svd.py "https://www.xiaohongshu.com/user/profile/5f..." --cookie "your_cookie"
        """,
    )

    parser.add_argument(
        "url",
        help="用户主页 URL"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="保存路径 (默认: 项目根目录/output)"
    )
    parser.add_argument(
        "-n", "--max-count",
        type=int,
        default=0,
        help="最大下载数量 (0=无限制, 默认: 0)"
    )
    parser.add_argument(
        "--date-from",
        default=None,
        help="起始日期 (格式: 2025-01-01)"
    )
    parser.add_argument(
        "--date-to",
        default=None,
        help="截止日期 (格式: 2025-06-01)"
    )
    parser.add_argument(
        "--video-only",
        action="store_true",
        help="仅下载视频 (跳过图集)"
    )
    parser.add_argument(
        "--image-only",
        action="store_true",
        help="仅下载图集 (跳过视频)"
    )
    parser.add_argument(
        "--cookie",
        default=None,
        help="登录 Cookie (部分平台必需)"
    )
    parser.add_argument(
        "--browser-cookie",
        default=None,
        help="从浏览器提取 Cookie (chrome/edge/firefox)"
    )
    parser.add_argument(
        "-q", "--quality",
        choices=["best", "hd", "sd"],
        default="best",
        help="画质选择 (best/hd/sd, 默认: best)"
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="代理服务器 (例: http://127.0.0.1:7890)"
    )
    parser.add_argument(
        "--no-cover",
        action="store_true",
        help="不保存封面"
    )
    parser.add_argument(
        "--no-desc",
        action="store_true",
        help="不保存文案"
    )
    parser.add_argument(
        "--music",
        action="store_true",
        help="保存视频原声"
    )
    parser.add_argument(
        "--max-connections",
        type=int,
        default=5,
        help="最大并发连接数 (默认: 5)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="网络超时时间/秒 (默认: 30)"
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="重试次数 (默认: 3)"
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["api", "cli", "direct"],
        default="direct",
        help="下载模式: api=逐个API下载, cli=调用f2/yt-dlp命令行, direct=批量直接下载 (默认: direct)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出作品信息，不下载"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径 (YAML)"
    )

    return parser.parse_args()


def build_config(args) -> DownloadConfig:
    """从命令行参数构建配置"""
    from config import load_config

    cfg = load_config(args.config)

    # 命令行参数覆盖配置文件
    if args.output:
        cfg.save_dir = os.path.abspath(args.output)
    if args.max_count:
        cfg.max_count = args.max_count
    if args.date_from:
        cfg.date_from = args.date_from
    if args.date_to:
        cfg.date_to = args.date_to
    if args.video_only:
        cfg.video_only = True
    if args.image_only:
        cfg.image_only = True
    if args.cookie:
        cfg.cookie = args.cookie
    if args.browser_cookie:
        cfg.browser_cookie = args.browser_cookie
        # 自动提取 Cookie（延迟到引擎初始化时，因为需要知道平台域名）
    if args.quality:
        cfg.quality = args.quality
    if args.proxy:
        cfg.proxies = args.proxy
    if args.no_cover:
        cfg.save_cover = False
    if args.no_desc:
        cfg.save_desc = False
    if args.music:
        cfg.save_music = True
    if args.max_connections:
        cfg.max_connections = args.max_connections
    if args.timeout:
        cfg.timeout = args.timeout
    if args.retries:
        cfg.max_retries = args.retries

    return cfg


def print_banner():
    """打印启动横幅"""
    banner = """
[bold cyan]* ShortVideoDownload[/bold cyan] [dim]v1.0.0[/dim]
[dim]短视频平台用户作品批量下载工具[/dim]
"""
    if RICH_AVAILABLE:
        console.print(banner)
    else:
        print("ShortVideoDownload v1.0.0")


def print_summary(platform: str, results: list, elapsed: float):
    """打印下载结果汇总"""
    success = sum(1 for r in results if r.success and not r.skipped)
    failed = sum(1 for r in results if not r.success and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)

    total_files = sum(len(r.saved_paths) for r in results if r.success and not r.skipped)
    total_size = sum(
        os.path.getsize(p) for r in results if r.success and not r.skipped for p in r.saved_paths if os.path.exists(p)
    )

    if RICH_AVAILABLE:
        console.print(f"\n[bold]── 下载完成 ──[/bold]")
        console.print(f"  平台: [cyan]{platform}[/cyan]")
        console.print(f"  成功: [green]{success}[/green]  跳过: [yellow]{skipped}[/yellow]  失败: [red]{failed}[/red]")
        console.print(f"  文件: {total_files} 个  大小: [green]{format_file_size(total_size)}[/green]  耗时: {elapsed:.1f}s")

        if failed > 0:
            console.print(f"\n[red]失败详情:[/red]")
            for r in results:
                if not r.success and not r.skipped:
                    console.print(f"  - {r.item.title[:50]}: {r.error or '未知错误'}")
    else:
        print(f"\n── 下载完成 ──")
        print(f"  平台: {platform}")
        print(f"  成功: {success}  跳过: {skipped}  失败: {failed}")
        print(f"  文件: {total_files} 个  大小: {format_file_size(total_size)}  耗时: {elapsed:.1f}s")

        if failed > 0:
            print(f"\n失败详情:")
            for r in results:
                if not r.success and not r.skipped:
                    print(f"  - {r.item.title[:50]}: {r.error or '未知错误'}")


async def run_download(url: str, config: DownloadConfig, mode: str, dry_run: bool = False):
    """执行下载"""
    # 检测平台
    platform = detect_platform(url)
    if platform == "unknown":
        if RICH_AVAILABLE:
            console.print(f"[red]X 无法识别平台，请检查 URL: {url}[/red]")
        else:
            print(f"无法识别平台: {url}")
        sys.exit(1)

    # 支持的平台
    supported = list(PLATFORM_PATTERNS.keys())
    if platform not in supported:
        if RICH_AVAILABLE:
            console.print(f"[red]X 暂不支持该平台: {platform}[/red]")
            console.print(f"[dim]支持的平台: {', '.join(supported)}[/dim]")
        else:
            print(f"暂不支持该平台: {platform}")
        sys.exit(1)

    if RICH_AVAILABLE:
        console.print(f"[green]> 检测到平台: {platform}[/green]")
        console.print(f"[dim]   用户主页: {url}[/dim]")
        console.print(f"[dim]   保存路径: {config.save_dir}[/dim]")
        console.print(f"[dim]   画质偏好: {config.quality}[/dim]")
    else:
        print(f"平台: {platform}, 保存: {config.save_dir}, 画质: {config.quality}")

    # 获取引擎
    engine_class = ENGINES.get(platform)
    if not engine_class:
        if RICH_AVAILABLE:
            console.print(f"[red]X 平台 {platform} 的下载引擎尚未实现[/red]")
        else:
            print(f"平台 {platform} 下载引擎未实现")
        sys.exit(1)

    engine = engine_class(config)

    # 自动从浏览器提取 Cookie
    if config.browser_cookie and not config.cookie:
        from utils import extract_browser_cookies, get_domain_for_platform, load_cookies_from_file
        domain = get_domain_for_platform(platform)
        if domain:
            cookie_found = False

            # 方式1: 从浏览器提取 Cookie
            try:
                if RICH_AVAILABLE:
                    with console.status(f"[bold green]正在从 {config.browser_cookie} 提取 {domain} Cookie..."):
                        config.cookie = extract_browser_cookies(config.browser_cookie, domain)
                else:
                    print(f"正在从 {config.browser_cookie} 提取 {domain} Cookie...")
                    config.cookie = extract_browser_cookies(config.browser_cookie, domain)

                # 重新设置引擎的 cookie
                if hasattr(engine, '_cookie'):
                    engine._cookie = config.cookie

                if RICH_AVAILABLE:
                    console.print(f"[green]> Cookie 提取成功 ({len(config.cookie)} 字符)[/green]")
                else:
                    print(f"Cookie 提取成功 ({len(config.cookie)} 字符)")
                cookie_found = True
            except RuntimeError as e:
                # 浏览器提取失败，静默回退到 cookies.txt
                pass

            # 方式2: 从 cookies.txt 文件加载
            if not cookie_found:
                config.cookie = load_cookies_from_file(domain)
                if config.cookie:
                    if hasattr(engine, '_cookie'):
                        engine._cookie = config.cookie
                    if RICH_AVAILABLE:
                        console.print(f"[green]> 从 cookies.txt 加载 Cookie 成功 ({len(config.cookie)} 字符)[/green]")
                    else:
                        print(f"从 cookies.txt 加载 Cookie 成功 ({len(config.cookie)} 字符)")
                    cookie_found = True

            if not cookie_found:
                if RICH_AVAILABLE:
                    console.print("[yellow]! 所有 Cookie 获取方式均失败[/yellow]")
                    console.print("[dim]  提示: 你可以手动导出 cookies.txt 文件放到项目根目录[/dim]")
                    console.print("[dim]  或使用 --cookie 参数手动提供 Cookie[/dim]")
                else:
                    print("所有 Cookie 获取方式均失败")
                    print("  提示: 你可以手动导出 cookies.txt 文件放到项目根目录")
                    print("  或使用 --cookie 参数手动提供 Cookie")
    elif not config.cookie:
        # 没有指定 --browser-cookie，也尝试从 cookies.txt 加载
        from utils import load_cookies_from_file, get_domain_for_platform
        domain = get_domain_for_platform(platform)
        if domain:
            config.cookie = load_cookies_from_file(domain)
            if config.cookie:
                if hasattr(engine, '_cookie'):
                    engine._cookie = config.cookie
                if RICH_AVAILABLE:
                    console.print(f"[green]> 从 cookies.txt 加载 Cookie 成功 ({len(config.cookie)} 字符)[/green]")
                else:
                    print(f"从 cookies.txt 加载 Cookie 成功 ({len(config.cookie)} 字符)")

    start_time = datetime.now()

    if dry_run:
        # 仅列出作品
        if RICH_AVAILABLE:
            console.print("[bold green]> 正在获取作品列表...[/bold green]")
        else:
            print("正在获取作品列表...")

        items = await engine.fetch_user_items(url)

        if RICH_AVAILABLE:
            table = Table(title=f"| {platform} 用户作品列表 (共 {len(items)} 个)")
            table.add_column("#", style="dim", width=4)
            table.add_column("类型", width=6)
            table.add_column("标题", max_width=60)
            table.add_column("ID", width=20)

            for idx, item in enumerate(items, 1):
                type_icon = "视频" if item.is_video else "图集"
                table.add_row(
                    str(idx),
                    type_icon,
                    item.title[:60],
                    item.item_id[:20],
                )
            console.print(table)
        else:
            print(f"\n共 {len(items)} 个作品:")
            for idx, item in enumerate(items, 1):
                print(f"  {idx}. [{item.item_type}] {item.title[:60]}")

        return

    # 获取作品列表
    if RICH_AVAILABLE:
        console.print("[bold green]> 正在获取作品列表...[/bold green]")
    else:
        print("正在获取作品列表...")

    items = await engine.fetch_user_items(url)

    if not items:
        if RICH_AVAILABLE:
            console.print("[yellow]! 未找到任何作品[/yellow]")
        else:
            print("未找到任何作品")
        return

    # 执行下载
    if RICH_AVAILABLE:
        console.print(f"[bold green]>> 开始下载 {len(items)} 个作品[/bold green]")
    else:
        print(f">> 开始下载 {len(items)} 个作品")

    results = await engine.download_user(url, items=items)

    elapsed = (datetime.now() - start_time).total_seconds()
    print_summary(platform, results, elapsed)


def check_dependencies():
    """检查依赖是否安装"""
    missing = []

    # 检查 yt-dlp
    import subprocess
    try:
        subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing.append("yt-dlp")

    # 检查 f2 (可选)
    try:
        subprocess.run(
            ["f2", "--help"],
            capture_output=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # f2 可选，仅抖音需要

    # 检查 Python 包
    try:
        import aiohttp
    except ImportError:
        missing.append("aiohttp")

    try:
        import aiofiles
    except ImportError:
        missing.append("aiofiles")

    if missing:
        if RICH_AVAILABLE:
            console.print("[yellow]! 以下依赖缺失:[/yellow]")
            for pkg in missing:
                console.print(f"  [dim]- {pkg}[/dim]")
            console.print("\n[cyan]请运行: pip install -r requirements.txt[/cyan]")
        else:
            print(f"缺失依赖: {', '.join(missing)}")
            print("请运行: pip install -r requirements.txt")
        sys.exit(1)

    # 自动更新 yt-dlp（参考 VideoSummarize 项目的做法）
    try:
        if RICH_AVAILABLE:
            console.print("[dim]正在检查 yt-dlp 更新...[/dim]")
        else:
            print("正在检查 yt-dlp 更新...")
        result = subprocess.run(
            ["yt-dlp", "-U"],
            capture_output=True, timeout=60,
        )
        output = result.stdout.decode('utf-8', errors='replace').strip()
        if output and RICH_AVAILABLE:
            console.print(f"[dim]{output}[/dim]")
    except Exception:
        pass  # 更新失败不影响使用


def main():
    """主入口"""
    args = parse_args()

    print_banner()

    # 检查依赖
    check_dependencies()

    # 构建配置
    config = build_config(args)

    # 确保保存目录存在
    os.makedirs(config.save_dir, exist_ok=True)

    # 运行下载
    try:
        asyncio.run(run_download(args.url, config, args.mode, args.dry_run))
    except KeyboardInterrupt:
        if RICH_AVAILABLE:
            console.print("\n[yellow]用户中断[/]")
        else:
            print("\n用户中断")
    except RuntimeError as e:
        if RICH_AVAILABLE:
            console.print(f"\n[red]错误:[/] {e}")
        else:
            print(f"\n错误: {e}")
        sys.exit(1)
    except Exception as e:
        if RICH_AVAILABLE:
            console.print(f"\n[red]未预期的错误:[/] {e}")
        else:
            print(f"\n未预期的错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
