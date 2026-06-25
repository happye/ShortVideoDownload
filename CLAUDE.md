# ShortVideoDownload - AI Agent 规则手册

## 项目概览

短视频平台用户作品批量下载工具。双引擎架构：抖音用 f2 库，其他平台用 yt-dlp + 自研 API。

## 关键规则

### 代码边界
- 抖音引擎逻辑只在 `engines/douyin.py`，其他引擎不得 import f2
- Cookie 提取逻辑只在 `utils.py`，引擎只接收 `config.cookie` 字符串
- 文件名必须包含 `item_id` 后缀（`标题_itemId.mp4`），用于去重判断
- `download_user()` 接受 `items` 参数避免重复 fetch
- 文件命名用 `build_display_title()`（主标题+标签合并），不用 `truncate_desc()`

### 禁止事项
- 不要用 `deduplicate_filepath()` 给同一视频加后缀——已下载的视频应跳过，不是重命名
- 不要在 `download_item()` 中保存 `.txt` 描述文件（`save_desc` 默认 False）
- 不要移除 f2 的 logging handler（会导致 f2 内部重新添加），改为设置 CRITICAL 级别
- 不要在 CLAUDE.md 里写历史叙事或变更日志

### f2 库处理
- f2 的日志有两个来源：logging 系统 + `rich_console.print()` 直接输出
- 抑制顺序：先 import f2（触发 `log_setup()`）→ 再设 CRITICAL 级别 + monkey-patch rich_console
- `_suppress_f2_logging()` 必须在 f2 完全 import 后调用

### Windows 编码
- `svd.py` 入口设置 `SetConsoleOutputCP(65001)` + 重包装 stdout/stderr 为 UTF-8
- `run.bat` 用 GBK 编码保存，不切换代码页（chcp 会导致终端刷新覆盖输出）
- Trae IDE 终端中文乱码是 Trae 自身问题，不是代码问题

### 抖音下载请求
- **下载请求不发送 Cookie**：抖音视频/图片/封面/音乐的 CDN URL（v*-web*.douyinvod.com）不需要 Cookie 鉴权，仅靠 URL 临时令牌即可访问；完整 Cookie 通常 >10KB（100+ 字段），会触发 Nginx `large_client_header_buffers` 8KB 限制报 `400 Request Header Or Cookie Too Large`
- 下载请求必须携带 Referer + 完整 User-Agent
- 视频下载带 3 次重试（网络中断/超时），指数退避 2s→4s→6s
- 失败时清理不完整文件，避免残留
- 注意：`fetch_user_items` / `fetch_single_item` 等 API 调用仍需要 Cookie（API 域名需要鉴权，CDN 域名不需要）

### 单视频链接下载
- `utils.detect_single_video(url)` 识别 4 种抖音 URL 格式：`?modal_id=`、`/video/{id}`、`/note/{id}`、`iesdouyin.com/share/video/{id}`
- 引擎实现 `fetch_single_item(video_id)`：调用 f2 的 `fetch_one_video(aweme_id)` 返回单个 DownloadItem
- `svd.py run_download` 中检测到单视频 URL → `fetch_single_item` → `download_user(url, items=[item])` 复用按 nickname 创建目录 + 跳过已存在 + download_item 的逻辑

### 抖音视频URL回退
- f2 的 `video_play_addr` 只映射 `bit_rate[0].play_addr.url_list`，部分视频 `bit_rate` 为空
- 当 `bit_rate` 为空时，必须回退到 `video.play_addr.url_list`（直接播放地址）
- 无 URL 的条目（非视频非图集）应跳过，不加入下载队列

### 小红书引擎（Playwright）
- 小红书有反爬虫检测：aiohttp 直接请求会被识别为未登录（`loggedIn: false`），note_id 返回空
- 必须用 Playwright 真实浏览器环境获取数据（stealth 模式 + cookie 注入）
- 数据来源：Vue 3 Pinia store（`document.querySelector('#app').__vue_app__.config.globalProperties.$pinia`）
  - 笔记列表：`userStore.notes[0]`，每条含 `id`/`noteCard.noteId`/`xsecToken`
  - 笔记详情：`noteStore.noteDetailMap[note_id].note`，含 `video.media.stream`/`imageList`
- 笔记详情页 URL 需带 `xsec_token` 参数：`/explore/{note_id}?xsec_token={token}&xsec_source=pc_note`
- 翻页：滚动页面到底部，Pinia store 自动追加笔记（最多 30 次滚动）
- `max_count` 在获取详情前应用，避免不必要地获取所有详情
- 下载用 aiohttp（与抖音一致），图片 URL 需 `http://` → `https://`

### Cookie 获取优先级
1. `--browser-cookie` → rookiepy 提取（Firefox 正常，Chrome/Edge 受 App-Bound Encryption 限制）
2. `cookies.txt` 文件回退（Netscape 格式，按域名自动筛选）
3. `--cookie` 手动提供

### 去重机制
- `_scan_existing_items()` 扫描目录，从文件名提取 item_id
- 抖音 item_id：≥10 位纯数字；小红书 note_id：24 位十六进制字符串
- `download_item()` 检查目标文件是否已存在
- 已存在 → 返回 `skipped=True`，不下载不重命名

## 深入文档

| 主题 | 文件 |
|------|------|
| 架构设计 | docs/architecture.md |
| Cookie 配置 | docs/cookie-guide.md |
| 故障排查 | docs/troubleshooting.md |
| 问题记录 | ISSUES.md |

## 命令速查

```bash
# 安装
python -m venv venv && venv\Scripts\pip install -r requirements.txt

# 抖音下载（自动从 cookies.txt 加载）
python svd.py "https://www.douyin.com/user/MS4wLjAB..."

# dry-run 预览
python svd.py "URL" --dry-run

# 限制数量
python svd.py "URL" -n 10

# 重命名 untitled 文件（预览）
python fix_names.py "用户URL" "output/douyin/用户目录" --dry-run

# 重命名 untitled 文件（执行）
python fix_names.py "用户URL" "output/douyin/用户目录"
```

## 平台状态

| 平台 | 引擎 | 状态 |
|------|------|------|
| 抖音 | f2 | 可用（需 Cookie） |
| 快手 | Web API | 需 web_st Cookie |
| 小红书 | HTML解析+yt-dlp | 需有效 Cookie |
| B站 | 旧API+yt-dlp | 需有效 Cookie |
| 微博 | Web API | 需有效 Cookie |
