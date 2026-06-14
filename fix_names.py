"""
ShortVideoDownload - 文件重命名工具
校正本地目录中 untitled/douyin_ID 命名的视频文件
通过重新爬取用户主页获取正确的标题信息，建立一一对应关系后重命名

用法:
  python fix_names.py <用户主页URL> <本地目录路径>
  python fix_names.py <用户主页URL> <本地目录路径> --dry-run

示例:
  python fix_names.py "https://www.douyin.com/user/MS4wLjAB..." "output/douyin/某用户"
  python fix_names.py "https://www.douyin.com/user/MS4wLjAB..." "output/douyin/某用户" --dry-run
"""
import os
import re
import sys
import asyncio
import argparse

# Windows 控制台 UTF-8 编码设置
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
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

from utils import build_display_title, sanitize_filename, load_cookies_from_file, suppress_f2_logging


def extract_item_id_from_filename(filename: str) -> str:
    """
    从文件名中提取 item_id
    支持格式:
      - untitled_7646452031309589946.mp4
      - douyin_7646452031309589946.mp4
      - 某标题_7646452031309589946.mp4
      - 某标题_7646452031309589946_001.jpg
    """
    name = os.path.splitext(filename)[0]
    parts = name.split('_')
    for part in reversed(parts):
        if len(part) >= 10 and part.isdigit():
            return part
    return ""


def find_rename_candidates(directory: str) -> dict:
    """
    扫描目录，找出需要重命名的文件（untitled_ 或 douyin_ 开头）
    返回 {item_id: [filepath1, filepath2, ...]} 的映射
    """
    candidates = {}
    needs_rename_prefixes = ('untitled_', 'douyin_')

    if not os.path.isdir(directory):
        print(f"目录不存在: {directory}")
        return candidates

    for filename in os.listdir(directory):
        # 跳过文本描述文件
        if filename.endswith('.txt'):
            continue

        # 检查是否需要重命名
        name_without_ext = os.path.splitext(filename)[0]
        needs_rename = False
        for prefix in needs_rename_prefixes:
            if name_without_ext.startswith(prefix):
                needs_rename = True
                break

        if not needs_rename:
            continue

        item_id = extract_item_id_from_filename(filename)
        if not item_id:
            continue

        filepath = os.path.join(directory, filename)
        if item_id not in candidates:
            candidates[item_id] = []
        candidates[item_id].append(filepath)

    return candidates


async def fetch_user_titles(user_url: str) -> dict:
    """
    爬取用户主页，获取 {item_id: desc} 的映射
    当 desc 为空时，使用 music_title_raw 作为回退
    """
    # 加载 Cookie
    cookie = load_cookies_from_file("douyin.com")

    try:
        from f2.apps.douyin.handler import DouyinHandler
        from f2.apps.douyin.utils import ClientConfManager, SecUserIdFetcher
    except ImportError:
        print("错误: f2 库未安装，请运行: pip install f2")
        return {}

    # 抑制 f2 日志（使用 utils 中的统一函数）
    suppress_f2_logging()

    # 获取 sec_user_id
    sec_uid = await SecUserIdFetcher.get_sec_user_id(user_url)

    kwargs = dict(ClientConfManager.client_conf.get("douyin", {}))
    kwargs["url"] = user_url
    kwargs["mode"] = "post"
    kwargs["cookie"] = cookie or ""

    handler = DouyinHandler(kwargs)
    title_map = {}

    async for aweme_data in handler.fetch_user_post_videos(sec_uid):
        if not aweme_data.has_aweme:
            continue

        aweme_ids = aweme_data.aweme_id
        descs = aweme_data.desc
        # 获取音乐标题作为回退
        music_titles = getattr(aweme_data, 'music_title_raw', None) or getattr(aweme_data, 'music_title', None)

        if not isinstance(aweme_ids, list):
            aweme_ids = [aweme_ids]
        if not isinstance(descs, list):
            descs = [descs]
        if not isinstance(music_titles, list):
            music_titles = [music_titles] if music_titles is not None else []

        for i in range(len(aweme_ids)):
            item_id = str(aweme_ids[i])
            desc = descs[i] if i < len(descs) else ""
            # desc 为空时用音乐标题回退
            if not desc and i < len(music_titles):
                music = music_titles[i]
                if music and music != "原声":
                    desc = f"#{music}"
            title_map[item_id] = desc

    return title_map


def rename_files(candidates: dict, title_map: dict, directory: str, dry_run: bool = False) -> list:
    """
    执行重命名操作
    返回重命名日志列表
    """
    logs = []
    renamed = 0
    skipped = 0
    failed = 0

    for item_id, filepaths in sorted(candidates.items()):
        if item_id not in title_map:
            for fp in filepaths:
                msg = f"[跳过] {os.path.basename(fp)} -> 无法匹配（爬取结果中无此 item_id）"
                logs.append(msg)
                skipped += 1
            continue

        desc = title_map[item_id]
        # 描述为空时用 item_id 作为标题
        new_base = build_display_title(desc) if desc else f"video_{item_id}"
        new_suffix = "" if not desc else f"_{item_id}"

        for old_path in filepaths:
            old_name = os.path.basename(old_path)
            ext = os.path.splitext(old_name)[1]

            # 处理封面文件（如 _cover.jpg）
            cover_match = re.search(r'_cover' + re.escape(ext) + r'$', old_name)
            # 处理图集序号（如 _001.jpg）
            idx_match = re.search(r'_(\d{3})' + re.escape(ext) + r'$', old_name)

            if cover_match:
                new_name = f"{new_base}{new_suffix}_cover{ext}"
            elif idx_match:
                new_name = f"{new_base}{new_suffix}{idx_match.group(0)}"
            else:
                new_name = f"{new_base}{new_suffix}{ext}"

            new_name = sanitize_filename(new_name)
            new_path = os.path.join(directory, new_name)

            # 检查新文件名是否与旧文件名相同
            if old_name == new_name:
                msg = f"[无需重命名] {old_name}"
                logs.append(msg)
                skipped += 1
                continue

            # 检查新文件名是否已存在
            if os.path.exists(new_path) and old_path != new_path:
                msg = f"[冲突] {old_name} -> {new_name} (目标文件已存在)"
                logs.append(msg)
                failed += 1
                continue

            if dry_run:
                msg = f"[预览] {old_name} -> {new_name}"
            else:
                try:
                    os.rename(old_path, new_path)
                    msg = f"[成功] {old_name} -> {new_name}"
                    renamed += 1
                except OSError as e:
                    msg = f"[失败] {old_name} -> {new_name} ({e})"
                    failed += 1

            logs.append(msg)

    # 汇总
    summary = f"\n{'='*60}"
    summary += f"\n重命名完成: 成功 {renamed}, 跳过 {skipped}, 失败 {failed}"
    if dry_run:
        summary += " (预览模式，未实际执行)"
    summary += f"\n{'='*60}"
    logs.append(summary)

    return logs


async def main():
    parser = argparse.ArgumentParser(description="校正 untitled/douyin_ID 命名的视频文件")
    parser.add_argument("url", help="用户主页 URL")
    parser.add_argument("directory", help="本地目录路径")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际重命名")
    args = parser.parse_args()

    directory = os.path.abspath(args.directory)

    print(f"扫描目录: {directory}")
    candidates = find_rename_candidates(directory)

    if not candidates:
        print("未找到需要重命名的文件")
        return

    print(f"找到 {len(candidates)} 个需要重命名的作品（共 {sum(len(v) for v in candidates.values())} 个文件）")

    print(f"正在爬取用户主页获取标题...")
    title_map = await fetch_user_titles(args.url)

    if not title_map:
        print("爬取失败，无法获取标题信息")
        return

    print(f"获取到 {len(title_map)} 个作品的标题信息")

    # 执行重命名
    logs = rename_files(candidates, title_map, directory, dry_run=args.dry_run)

    for log in logs:
        print(log)


if __name__ == "__main__":
    asyncio.run(main())
