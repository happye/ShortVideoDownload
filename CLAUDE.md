# ShortVideoDownload - AI Agent 规则手册

## 项目概览

短视频平台用户作品批量下载工具。三套引擎：抖音用 f2 库，小红书用 Chrome CDP + Patchright，B站纯 yt-dlp，快手/微博用自研 API + yt-dlp。

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
- `max_count` 两态语义：`0`（默认，不限，滚动到底）/ `N`（`-n N`，限制数量）。config.py 默认 `0`，svd.py argparse `default=0`
- **不要用 Edit/Write 工具修改 `run.bat`**：GBK 编码 + CRLF 换行符。Edit/Write 会用 UTF-8 读写导致"锟斤拷"乱码。`run.bat` 已设只读 + `.gitattributes` 标记 `binary` 双重保护。**如发现 run.bat 被修改（只读被取消/字节数变化/换行符变 LF），立即用 Python 字节级脚本转 CRLF + commit，然后 `Set-ItemProperty run.bat -Name IsReadOnly -Value $true` 重设只读**。修改内容必须用 Python 脚本以 GBK 编码读写

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
- `utils.detect_single_video(url)` 识别抖音 4 种 + 小红书 3 种 + B站 2 种 URL 格式（`/video/BVxxx`、`/video/avxxx`）
- 引擎实现 `fetch_single_item(video_id, original_url=None)`：抖音调用 f2 的 `fetch_one_video`；小红书用 CDP 连接的真实 Chrome 访问详情页（从 `original_url` 提取 `xsec_token`）；B站调用 `view` API 拿标题/UP主（失败退化为 video_id 作标题，yt-dlp 仍可下载）；快手/微博未实现 `fetch_single_item`，不支持单视频下载
- `svd.py run_download` 中检测到单视频 URL → `fetch_single_item` → `download_user(url, items=[item])` 复用按 nickname 创建目录 + 跳过已存在 + download_item 的逻辑

### B站引擎（完全基于 yt-dlp）
- **不再自调B站 API**：旧 `x/space/arc/search`、`x/polymer/space/seasons_series_list` 已废弃返回 404；新 API 需要 wbi 签名且风控严格（频繁 -799 / 412）。yt-dlp 内部维护 API 路径和签名，跟着升级，最稳定
- **Cookie 必需**：B站无 Cookie 触发 412 Precondition Failed 风控，连免费的 1080p 都拿不到（1080p 不需要大会员，但必须登录）。`svd.py` 检测到 B站且无 Cookie 时自动调用 `_fetch_bili_cookie.py`（Patchright 启动 Edge/Chrome 独立 Profile + CDP 拿明文 Cookie，绕过 App-Bound Encryption）
- **`fetch_user_items`**：用 `yt-dlp --flat-playlist -O "%(id)s"` 列出用户所有视频，**自动处理投稿/合集/系列/子合集**，保证视频列表完整
- **标题占位**：flat-playlist 模式拿不到标题，`item.title` 用 BV 号占位；下载时 yt-dlp 用真实标题命名文件
- **UP 主昵称**：调一次 `view` API 拿第一个视频的 `owner.name`，用于按昵称创建目录（失败时目录名 "unknown"）
- **`download_item`**：用 yt-dlp `-o "%(title)s_%(id)s.%(ext)s"` 模板命名文件（真实标题+BV号后缀），`--print after_move:filepath` 获取实际保存路径
- **画质选择**：`best`（默认，最高视频+最高音频）/ `hd`（≤1080p）/ `sd`（≤720p），通过 yt-dlp `-f` 格式选择实现
- **去重**：`_scan_existing_items` 识别文件名中的 BV 号（`BV[A-Za-z0-9]{10}`）和 av 号（`av\d+`）
- **URL 兼容性**：`_extract_uid` 用 `space\.bilibili\.com/(\d+)` 提取 UID，支持所有 space 子路径（主页、`/upload/video`、`/dynamic`、`/channel/collectionDetail?sid=xxx`、`/channel/seriesDetail?sid=xxx` 等），yt-dlp 自动按 URL 类型列出对应视频
- **Cookie 优先级**：`--cookies-from-browser` > 项目根 `cookies.txt` > 临时文件（从 `--cookie` 字符串生成）

### 抖音视频URL回退
- f2 的 `video_play_addr` 只映射 `bit_rate[0].play_addr.url_list`，部分视频 `bit_rate` 为空
- 当 `bit_rate` 为空时，必须回退到 `video.play_addr.url_list`（直接播放地址）
- 无 URL 的条目（非视频非图集）应跳过，不加入下载队列

### 抖音单视频详情
- **不要用 `handler.fetch_one_video`**：`aweme_detail` 为 null 时抛固定错误"如果是动图作品，则接口正在维护中"（误导）
- 用 `crawler.fetch_post_detail` 拿原始响应，`aweme_detail` 为 null 时读 `filter_detail.detail_msg` + `filter_reason`（真实原因：隐私设置 `status_friend_see`、删除、下架等）

### 小红书引擎（CDP + Patchright 反检测）
- **反检测架构（基于 yousali.com 反检测实战文章验证）**：
  - **用 `connect_over_cdp` 连接真实 Chrome**，不是 `launch()` 启动 Chromium
    - launch() 启动的浏览器带 `--enable-automation` 标记，UA 是 Chromium 不是 Chrome
    - Client Hints brand 是 `"Chromium"` 而非 `"Google Chrome"`，秒检测
    - 全新实例无书签 / 扩展 / 浏览历史 / 其他网站 cookies
  - **用 Patchright 替代 Playwright**（`pip install patchright`）
    - 修补 `Runtime.enable` / `Console.enable` CDP 协议层泄漏
    - 协议层修补对页面 JS 完全透明（不注入任何 JS）
  - **绝对不要 `add_init_script` 注入 stealth JS**：JS 注入本身就是检测信号
    - `Object.defineProperty` 留下 getter 痕迹，`toString()` 暴露非 native 代码
    - 真实 Chrome 不需要任何 JS 修补，patchright 在协议层完成所有反检测
  - **不覆盖 `user_agent` / `viewport` / `locale` / `timezone`**：UA 必须和浏览器实际指纹一致，否则 UA-Client Hints 不一致是检测点
  - **独立 user-data-dir**（`~/.shortvideo_download/chrome-profile`）：累积浏览历史 / cookies，越来越像真实浏览器；不影响用户日常 Chrome
  - **CDP 模式下不要 `new_context()`**：会触发 `ERR_CONNECTION_CLOSED`，必须用 `browser.contexts[0]`
  - **CDP 模式下不要 `context.close()`**：会关闭 Chrome 默认 context 的所有标签页，影响用户其他标签页；只 `page.close()`
  - **`_close_browser` 不杀 Chrome 子进程**：让独立 Profile 持久化累积"生活痕迹"
  - **`connect_over_cdp` 必须显式 `timeout=30_000`**：默认 180s，遇到僵尸 Chrome（进程活着占端口但 DevTools 卡死）会白等 3 分钟。`_ensure_browser` 连接失败时自动 `_kill_stale_chrome()`（按命令行匹配 `chrome-profile`，只杀本项目 Profile 的 Chrome）后重启重试一次
  - **不要加 `--disable-blink-features=AutomationControlled` 启动参数**：真实 Chrome 用户永远不会带此参数，它是 Playwright/Puppeteer 的经典反检测标记，通过 `chrome://version` 或 `process.argv` 直接可见。patchright 已在协议层修补 `navigator.webdriver`，不依赖此参数
  - **滚动用 `page.mouse.wheel()`，不要用 `page.evaluate('window.scrollBy()')`**：后者不触发 wheel 事件，网站可监听 wheel 事件 vs scrollY 变化区分真假滚动
- 小红书有反爬虫检测：aiohttp 直接请求会被识别为未登录（`loggedIn: false`），note_id 返回空
- 数据来源：从 `page.content()` 的 HTML 中直接提取 `window.__INITIAL_STATE__` 的 JSON
  - **不要用 `page.evaluate('window.__INITIAL_STATE__')`**：patchright CDP 模式下页面内联 `<script>window.__INITIAL_STATE__=...</script>` 不在 main world 执行（evaluate 本身确实在 main world，能访问 DOM 元素属性如 `__vue_app__`，但读不到 inline script 设置的 window 全局变量，`__SSR__` 同理读不到）
  - 模块级函数 `_extract_initial_state_from_html(html)` 实现提取：`marker` 定位 → 括号匹配（处理字符串内的括号）→ `_sanitize_js_object_literals()` 清理 JSON 非法的 JS 值 → `json.loads`
  - **`__INITIAL_STATE__` 会混入 JS 专有值**（2026-07 确认 `new Map([])`，如 `AiNoteDetailStore.noteDetailMap`），JSON 标准不允许。`_sanitize_js_object_literals()` 逐字符扫描 + 字符串状态跟踪，只在字符串外替换：`undefined`/`NaN`/`Infinity`/`-Infinity` → `null`；`new Xxx(...)` → `null`（括号匹配完整范围）。不要用简单正则替换（误伤字符串内容且不覆盖 `new Map()`）
  - 三处复用：登录检测（`user.loggedIn` / `user.userInfo.nickname`）/ SSR 笔记提取 / 笔记详情提取
  - 笔记列表：**SSR `__INITIAL_STATE__.user.notes[0]` 为主**（notes 是数组的数组，每个 tab 一个数组；含完整 xsecToken），user_posted API 拦截为辅（补充 SSR 之外的更多笔记）
  - 笔记详情：`__INITIAL_STATE__.note.noteDetailMap[note_id].note`，含 `video.media.stream`/`imageList`
- **video stream key 命名不稳定**：小红书会不定期改 `video.media.stream` 的 key（旧 `h264`/`h265`/`av1` → 新 `EF4`/`EF5`/`EF6`/`EF7`），字段名 `masterUrl`/`backupUrls` 未变。**必须遍历所有 stream keys，不能硬编码列表**，按 `videoBitrate` 降序选最高画质
- **不能依赖 user_posted API 拿首屏**：API 的 cursor 会跳过前 30 个，只返回 4 个 `has_more=False` 的更早笔记，必须从 SSR 提取
- **字段命名陷阱**：SSR 中是 `xsecToken`（驼峰），API 响应中是 `xsec_token`（下划线），`_fetch_note_detail_via_page` 兼容两种命名
- **响应拦截器必须在 `page.goto()` 之前注册**：用于补充捕获 SSR 之外的更多笔记（首次 user_posted API 在 goto 期间就发出）
- 笔记详情页 URL 需带 `xsec_token` 参数：`/explore/{note_id}?xsec_token={token}&xsec_source=pc_note`
- 翻页：滚动页面到底部触发新请求（5-10 秒间隔，连续 2 次无新增停止，安全上限 100 次）
- `max_count` 在获取详情前应用，避免不必要地获取所有详情
- 下载用 aiohttp（与抖音一致），图片 URL 需 `http://` → `https://`
- 下载请求的 UA 用 `self._user_agent`（从 `navigator.userAgent` 获取的真实 UA），保证 UA 和浏览器指纹一致
- **大文件断点续传**：重试时用 `Range: bytes={已下载大小}-` header 从断点继续，不删除已下载部分（避免 95MB+ 大文件在 CDN 断流处反复失败）；timeout `total=600, sock_read=60`；chunk 64KB
- **视频/图集类型判断**：视频笔记的 `imageList` 有 1 个封面图，不能仅凭 `image_urls` 非空判为图集。`is_image` 必须加 `and not video_url` 条件

### Cookie 获取优先级
1. `--browser-cookie` → rookiepy 提取（Firefox 正常，Chrome/Edge 受 App-Bound Encryption 限制）
2. `cookies.txt` 文件回退（Netscape 格式，按域名自动筛选）
3. `--cookie` 手动提供

### 去重机制
- `_scan_existing_items()` 扫描目录，从文件名提取 item_id
- 抖音 item_id：≥10 位纯数字；小红书 note_id：24 位十六进制；B站 BV号 `BV[A-Za-z0-9]{10}` / av号 `av\d+`
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
| 小红书 | Chrome CDP+Patchright | 需有效 Cookie |
| B站 | yt-dlp（投稿+合集+系列） | 需有效 Cookie |
| 微博 | Web API | 需有效 Cookie |
